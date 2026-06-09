# utils/auto_roster_engine.py
# Pure-Python auto-roster and break scheduling engine.
# No database calls. No Streamlit imports.
# No .single() anywhere.
#
# ALGORITHM OVERVIEW
# ──────────────────
# Step 1 — Roster generation
#   1. For each day and room, read actual_children per 15-minute slot.
#   2. Compute required_staff per slot: ceil(children / ratio_children).
#   3. Merge adjacent slots into contiguous coverage windows per room.
#   4. Assign staff to windows using a greedy allocator:
#        a. Prefer staff whose primary_room matches.
#        b. Exclude staff on leave.
#        c. Exclude staff unavailable for this weekday.
#        d. Respect available_from / available_until windows.
#        e. Minimise total staff: only assign as many as required.
#   5. Produce SuggestedShift records (not yet saved).
#
# Step 2 — Break scheduling
#   1. For each suggested shift, compute entitlement + opt-out.
#   2. For each break, find a time window where removing 1 staff
#      from the room does not drop coverage below required.
#   3. Prefer unpaid breaks 11:00–14:00; paid breaks outside peak.
#   4. Stagger breaks: no two staff from same room break simultaneously.
#   5. Flag "Manual review required" when no compliant window exists.

from __future__ import annotations
import math
from datetime import datetime, date, timedelta
from dataclasses import dataclass, field
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# DATA CLASSES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SuggestedShift:
    user_id:        str
    user_name:      str
    room_id:        str
    room_name:      str
    shift_date:     str          # YYYY-MM-DD
    start_time:     str          # HH:MM:SS
    end_time:       str          # HH:MM:SS
    shift_type:     str          # opening | standard | closing
    break_opt_out_override: str  # use_staff_default | opted_out | not_opted_out
    source:         str          # "primary_room" | "available" | "unmatched"
    warnings:       list[str] = field(default_factory=list)


@dataclass
class SuggestedBreak:
    user_id:                str
    user_name:              str
    shift_key:              str   # f"{user_id}_{shift_date}" — links to SuggestedShift
    break_date:             str   # YYYY-MM-DD
    break_type:             str   # rest | meal | combined
    planned_start_time:     str   # HH:MM:SS
    planned_end_time:       str   # HH:MM:SS
    planned_duration_minutes: int
    paid_minutes:           int   # 20 for combined/rest, 0 for meal
    unpaid_minutes:         int   # 30 for combined/meal, 0 for rest
    combined:               bool  # True when paid+unpaid merged into one block
    label:                  str   # display label
    status:                 str   # scheduled | manual_review
    opt_out_source:         str   # "Staff default" | "Manual override — opted out" | etc.
    warnings:               list[str] = field(default_factory=list)


@dataclass
class RosterResult:
    shifts:         list[SuggestedShift]
    breaks:         list[SuggestedBreak]
    ratio_warnings: list[str]           # rooms/slots still under-staffed
    review_warnings: list[str]          # break scheduling failures
    unmet_rooms:    list[str]           # room names with no staff available


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────

def generate_roster(
    days: list[date],
    rooms: list[dict],
    all_intervals: dict[str, list[dict]],   # {date_str: list[interval_rows]}
    staff: list[dict],
    availability_map: dict[str, dict],      # {uid: {dow: {is_available, from, until}}}
    leave_map: dict[str, list[str]],        # {uid: [date_strs with leave]}
    break_prefs: dict[str, dict[int, bool]],# {uid: {dow: opt_out_bool}}
    break_rules: list[dict] | None = None,
    centre_id: str = "",
) -> RosterResult:
    """
    Generate suggested shifts and breaks for a roster period.

    Parameters
    ----------
    days            Ordered list of calendar days in the period.
    rooms           Room dicts — needs id, name, licensed_capacity,
                    required_ratio_staff, required_ratio_children,
                    age_min_months, age_max_months.
    all_intervals   {date_str: [interval rows from fetch_intervals_for_centre]}.
                    Rows need room_id, interval_start, interval_end,
                    actual_children, expected_children.
    staff           Enriched staff list from fetch_all_staff(). Each entry needs
                    users.id, users.first_name/last_name, user_centre_roles
                    (with primary_room_id, centre_id, is_active).
    availability_map From fetch_availability_map(centre_id).
    leave_map       From fetch_approved_leave_for_period(centre_id, …).
    break_prefs     From fetch_break_prefs_for_centre(centre_id).
    break_rules     Optional break rule tiers (None → engine defaults).
    centre_id       Used to filter staff to this centre.
    """
    from utils.break_engine import (
        calc_break_entitlement, suggest_break_times,
        shift_duration_minutes, resolve_opt_out, BREAK_RULES_DEFAULT,
    )
    active_rules = break_rules or BREAK_RULES_DEFAULT

    # ── Flatten staff to this centre, build lookup tables ─────────────
    centre_staff  = _build_centre_staff(staff, centre_id)
    all_shifts:    list[SuggestedShift] = []
    all_breaks:    list[SuggestedBreak] = []
    ratio_warns:   list[str]            = []
    review_warns:  list[str]            = []
    unmet_rooms:   list[str]            = []

    room_map = {r["id"]: r for r in rooms}

    for day in days:
        date_str = day.isoformat()
        dow      = day.isoweekday() % 7   # 0=Sun, 1=Mon … 6=Sat

        # Intervals for this day keyed by room_id
        day_ivs   = all_intervals.get(date_str, [])
        room_ivs  = _group_intervals_by_room(day_ivs)

        # ── Step 1: compute required staff per room per slot ──────────
        room_windows = {}     # {room_id: [CoverageWindow]}
        for room in rooms:
            rid     = room["id"]
            r_staff = room.get("required_ratio_staff",    1)
            r_child = room.get("required_ratio_children", 4)
            cap     = room.get("licensed_capacity", 0)

            ivs = room_ivs.get(rid, [])
            if not ivs:
                continue

            # Required staff at each interval
            req_by_slot = {}
            for iv in ivs:
                act = iv.get("actual_children")
                exp = iv.get("expected_children")
                n   = int(act) if act is not None else (int(exp) if exp is not None else 0)
                if n > 0:
                    req = math.ceil(n / r_child) * r_staff
                    req_by_slot[iv["interval_start"]] = req

            if not req_by_slot:
                continue

            windows = _merge_slots_to_windows(req_by_slot, ivs)
            room_windows[rid] = windows

        # ── Step 2: assign staff to windows ──────────────────────────
        # Track who has been assigned and for how long each day
        assigned_minutes: dict[str, int] = {}   # uid → total minutes assigned today
        day_shifts: list[SuggestedShift]  = []

        for rid, windows in room_windows.items():
            room   = room_map.get(rid, {})
            rname  = room.get("name", rid)

            # Find eligible staff for this room
            eligible = _eligible_staff(
                centre_staff, rid, date_str, dow,
                availability_map, leave_map,
            )

            for window in windows:
                staffed = 0
                for _ in range(window.required_staff):
                    best = _pick_staff(
                        eligible, rid, date_str, window,
                        assigned_minutes, day_shifts,
                    )
                    if best is None:
                        ratio_warns.append(
                            f"{rname} {window.start[:5]}–{window.end[:5]} on {date_str}: "
                            f"could not fill staff slot {staffed + 1}/{window.required_staff}."
                        )
                        break

                    uid      = best["uid"]
                    uname    = best["name"]
                    stype    = _shift_type(window.start, window.end)
                    dur_mins = shift_duration_minutes(window.start, window.end)

                    # Apply break opt-out preference for this weekday
                    pref_day = break_prefs.get(uid, {}).get(dow, False)
                    override = "opted_out" if pref_day else "use_staff_default"

                    shift = SuggestedShift(
                        user_id=uid,
                        user_name=uname,
                        room_id=rid,
                        room_name=rname,
                        shift_date=date_str,
                        start_time=window.start,
                        end_time=window.end,
                        shift_type=stype,
                        break_opt_out_override=override,
                        source=best["source"],
                    )
                    day_shifts.append(shift)
                    assigned_minutes[uid] = assigned_minutes.get(uid, 0) + dur_mins
                    staffed += 1

            if not eligible:
                if rname not in unmet_rooms:
                    unmet_rooms.append(rname)

        all_shifts.extend(day_shifts)

        # ── Step 3: schedule breaks for each shift ────────────────────
        # Build room→shift-coverage map for ratio checking during breaks
        room_coverage = _build_room_coverage(day_shifts, room_map)

        # Track break windows already assigned to avoid overlap
        breaks_by_room: dict[str, list[tuple[str, str]]] = {}

        for shift in day_shifts:
            uid  = shift.user_id
            rid  = shift.room_id
            ss   = shift.start_time
            se   = shift.end_time

            # Resolve opt-out
            mock_shift = {
                "user_id":                    uid,
                "shift_date":                 date_str,
                "unpaid_break_opt_out_override": shift.break_opt_out_override,
            }
            opted_out, opt_src = resolve_opt_out(mock_shift, break_prefs)

            dur_mins = shift_duration_minutes(ss, se)
            ent      = calc_break_entitlement(dur_mins, active_rules, unpaid_opted_out=opted_out)

            if ent["total_min"] == 0:
                continue   # no break required

            suggestions = suggest_break_times(ss, se, ent)
            # For combined suggestions: if ratio conflict, fall back to two separate
            if len(suggestions) == 1 and suggestions[0].get("combined"):
                conflict_chk, _ = _check_break_impact(
                    suggestions[0]["planned_start"][:8],
                    suggestions[0]["planned_end"][:8],
                    rid, uid, room_coverage, breaks_by_room, r_staff, r_child,
                )
                if conflict_chk == "breach":
                    from utils.break_engine import suggest_break_times_separate
                    suggestions = suggest_break_times_separate(ss, se, ent)

            room        = room_map.get(rid, {})
            r_staff     = room.get("required_ratio_staff",    1)
            r_child     = room.get("required_ratio_children", 4)
            shift_key   = f"{uid}_{date_str}"

            for sug in suggestions:
                btype    = sug["break_type"]
                b_dur    = sug["duration_minutes"]
                b_start  = sug["planned_start"][:8]
                b_end    = sug["planned_end"][:8]

                # Prefer unpaid (meal) or combined breaks 11:00–14:30
                if btype in ("meal", "combined"):
                    b_start, b_end = _shift_break_to_window(
                        b_start, b_end, b_dur, ss, se, "11:00:00", "14:30:00"
                    )

                # Check: if this staff is removed, does coverage drop below ratio?
                conflict, reason = _check_break_impact(
                    b_start, b_end, rid, uid,
                    room_coverage, breaks_by_room, r_staff, r_child,
                )

                if conflict == "breach":
                    # Try to find an alternate window
                    alt_start, alt_end, alt_conflict = _find_alt_break_window(
                        ss, se, b_dur, rid, uid,
                        room_coverage, breaks_by_room, r_staff, r_child,
                    )
                    if alt_conflict:
                        brk = SuggestedBreak(
                            user_id=uid, user_name=shift.user_name,
                            shift_key=shift_key, break_date=date_str,
                            break_type=btype,
                            planned_start_time=b_start,
                            planned_end_time=b_end,
                            planned_duration_minutes=b_dur,
                            paid_minutes=sug.get("paid_minutes", b_dur if btype == "rest" else 0),
                            unpaid_minutes=sug.get("unpaid_minutes", b_dur if btype == "meal" else 0),
                            combined=sug.get("combined", False),
                            label=sug.get("label", btype.title()),
                            status="manual_review",
                            opt_out_source=opt_src,
                            warnings=[f"No compliant break window found. {reason}"],
                        )
                        review_warns.append(
                            f"{shift.user_name} ({shift.room_name}) on {date_str}: "
                            f"no compliant {btype} break window — manual review required."
                        )
                    else:
                        b_start, b_end = alt_start, alt_end
                        brk = SuggestedBreak(
                            user_id=uid, user_name=shift.user_name,
                            shift_key=shift_key, break_date=date_str,
                            break_type=btype,
                            planned_start_time=b_start,
                            planned_end_time=b_end,
                            planned_duration_minutes=b_dur,
                            paid_minutes=sug.get("paid_minutes", b_dur if btype == "rest" else 0),
                            unpaid_minutes=sug.get("unpaid_minutes", b_dur if btype == "meal" else 0),
                            combined=sug.get("combined", False),
                            label=sug.get("label", btype.title()),
                            status="scheduled",
                            opt_out_source=opt_src,
                        )
                else:
                    brk = SuggestedBreak(
                        user_id=uid, user_name=shift.user_name,
                        shift_key=shift_key, break_date=date_str,
                        break_type=btype,
                        planned_start_time=b_start,
                        planned_end_time=b_end,
                        planned_duration_minutes=b_dur,
                        paid_minutes=sug.get("paid_minutes", b_dur if btype == "rest" else 0),
                        unpaid_minutes=sug.get("unpaid_minutes", b_dur if btype == "meal" else 0),
                        combined=sug.get("combined", False),
                        label=sug.get("label", btype.title()),
                        status="scheduled",
                        opt_out_source=opt_src,
                    )

                all_breaks.append(brk)
                # Record this break so next educator avoids same window
                breaks_by_room.setdefault(rid, []).append((b_start, b_end))

    return RosterResult(
        shifts=all_shifts,
        breaks=all_breaks,
        ratio_warnings=ratio_warns,
        review_warnings=review_warns,
        unmet_rooms=unmet_rooms,
    )


# ─────────────────────────────────────────────────────────────────────────────
# PRIVATE DATA CLASSES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CoverageWindow:
    start:          str   # HH:MM:SS
    end:            str   # HH:MM:SS
    required_staff: int
    peak_children:  int


# ─────────────────────────────────────────────────────────────────────────────
# PRIVATE — BUILD HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _build_centre_staff(staff: list[dict], centre_id: str) -> list[dict]:
    """
    Flatten staff list to those active at this centre.
    Returns list of {uid, name, primary_room_id, employment_type}.
    """
    result = []
    for profile in staff:
        u = profile.get("users") or {}
        uid = u.get("id", "")
        if not uid or not u.get("is_active", True):
            continue

        for role in (profile.get("user_centre_roles") or []):
            if role.get("centre_id") != centre_id:
                continue
            if not role.get("is_active", True):
                continue
            result.append({
                "uid":             uid,
                "name":            f"{u.get('first_name','')} {u.get('last_name','')}".strip(),
                "primary_room_id": role.get("primary_room_id"),
                "employment_type": profile.get("employment_type", "full_time"),
                "allows_opt_out":  profile.get("allows_unpaid_break_opt_out", False),
            })
            break  # one role per centre

    return result


def _group_intervals_by_room(day_ivs: list[dict]) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = {}
    for iv in day_ivs:
        rid = iv.get("room_id", "")
        if rid:
            result.setdefault(rid, []).append(iv)
    return result


def _merge_slots_to_windows(
    req_by_slot: dict[str, int],
    all_ivs: list[dict],
) -> list[CoverageWindow]:
    """
    Merge adjacent required-staff slots into contiguous CoverageWindows.
    Adjacent slots with the same required_staff count are merged.
    """
    if not req_by_slot:
        return []

    # Sort by interval_start
    iv_lookup = {iv["interval_start"]: iv for iv in all_ivs}
    sorted_starts = sorted(req_by_slot.keys())

    windows: list[CoverageWindow] = []
    cur_start     = sorted_starts[0]
    cur_req       = req_by_slot[cur_start]
    cur_peak      = cur_req
    cur_end       = iv_lookup[cur_start]["interval_end"]

    for istart in sorted_starts[1:]:
        req = req_by_slot[istart]
        iv  = iv_lookup.get(istart, {})
        iend = iv.get("interval_end", istart)

        # Adjacent if this slot starts exactly where the previous one ended
        if istart == cur_end and req == cur_req:
            # Extend current window
            cur_end  = iend
            cur_peak = max(cur_peak, req)
        else:
            # Save current window, start new one
            windows.append(CoverageWindow(
                start=cur_start, end=cur_end,
                required_staff=cur_req, peak_children=cur_peak,
            ))
            cur_start = istart
            cur_req   = req
            cur_peak  = req
            cur_end   = iend

    windows.append(CoverageWindow(
        start=cur_start, end=cur_end,
        required_staff=cur_req, peak_children=cur_peak,
    ))
    return windows


def _eligible_staff(
    centre_staff: list[dict],
    room_id: str,
    date_str: str,
    dow: int,
    availability_map: dict[str, dict],
    leave_map: dict[str, list[str]],
) -> list[dict]:
    """
    Return staff eligible to work in room_id on date_str, sorted by preference:
      1. Primary room matches (preferred).
      2. Available and not on leave.
    """
    result_primary = []
    result_other   = []

    for s in centre_staff:
        uid = s["uid"]

        # Leave check
        if date_str in leave_map.get(uid, []):
            continue

        # Availability check
        av = availability_map.get(uid, {}).get(dow)
        if av is not None and not av.get("is_available", True):
            continue

        entry = {**s, "avail": av}

        if s.get("primary_room_id") == room_id:
            result_primary.append(entry)
        else:
            result_other.append(entry)

    return result_primary + result_other


def _pick_staff(
    eligible: list[dict],
    room_id: str,
    date_str: str,
    window: CoverageWindow,
    assigned_minutes: dict[str, int],
    day_shifts: list[SuggestedShift],
) -> dict | None:
    """
    Pick the best available staff member for this window, avoiding double-booking.
    Returns {uid, name, source} or None if no one is available.
    """
    # Build set of already-booked (uid, overlapping) for this window
    already_in_window = set()
    for s in day_shifts:
        if s.shift_date == date_str:
            if s.start_time < window.end and s.end_time > window.start:
                already_in_window.add(s.user_id)

    window_dur = _mins_between(window.start, window.end)

    for s in eligible:
        uid = s["uid"]
        if uid in already_in_window:
            continue

        # Check availability window fits
        av = s.get("avail")
        if av:
            av_from  = (av.get("available_from")  or "00:00")[:5] + ":00"
            av_until = (av.get("available_until") or "23:59")[:5] + ":00"
            if window.start < av_from or window.end > av_until:
                continue

        source = "primary_room" if s.get("primary_room_id") == room_id else "available"
        return {"uid": uid, "name": s["name"], "source": source}

    return None


def _shift_type(start: str, end: str) -> str:
    """Classify opening / closing / standard based on time."""
    s = start[:5]
    e = end[:5]
    if s <= "07:30":
        return "opening"
    if e >= "17:30":
        return "closing"
    return "standard"


def _mins_between(start: str, end: str) -> int:
    try:
        s = datetime.strptime(start[:5], "%H:%M")
        e = datetime.strptime(end[:5],   "%H:%M")
        return max(0, int((e - s).total_seconds() / 60))
    except Exception:
        return 0


# ─────────────────────────────────────────────────────────────────────────────
# PRIVATE — BREAK SCHEDULING HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _build_room_coverage(
    day_shifts: list[SuggestedShift],
    room_map: dict[str, dict],
) -> dict[str, dict[str, int]]:
    """
    {room_id: {HH:MM: staff_count}} for 15-min slots based on suggested shifts.
    """
    result: dict[str, dict[str, int]] = {}
    slots = [
        f"{h:02d}:{m:02d}:00"
        for h in range(6, 21)
        for m in (0, 15, 30, 45)
    ]
    for shift in day_shifts:
        rid = shift.room_id
        if rid not in result:
            result[rid] = {s: 0 for s in slots}
        for slot in slots:
            if shift.start_time <= slot < shift.end_time:
                result[rid][slot] = result[rid].get(slot, 0) + 1
    return result


def _check_break_impact(
    b_start: str,
    b_end: str,
    room_id: str,
    user_id: str,
    room_coverage: dict[str, dict[str, int]],
    breaks_by_room: dict[str, list[tuple[str, str]]],
    r_staff: int,
    r_child: int,
) -> tuple[str, str]:
    """
    Return ("ok" | "breach", reason).

    Checks that:
    1. Removing this staff member during b_start–b_end doesn't drop
       room coverage below r_staff.
    2. Another staff from the same room isn't already on break in this window.
    """
    cov = room_coverage.get(room_id, {})

    # Check no simultaneous room break
    for existing_start, existing_end in breaks_by_room.get(room_id, []):
        if existing_start < b_end and existing_end > b_start:
            return "breach", "Another staff member is already on break in this window."

    # Check ratio coverage
    slots_in_break = [s for s in cov if b_start <= s < b_end]
    for slot in slots_in_break:
        staff_at_slot  = cov.get(slot, 0)
        staff_if_break = max(0, staff_at_slot - 1)
        if staff_if_break < r_staff:
            return "breach", f"Coverage at {slot[:5]} drops to {staff_if_break} (need {r_staff})."

    return "ok", ""


def _find_alt_break_window(
    shift_start: str,
    shift_end: str,
    dur_minutes: int,
    room_id: str,
    user_id: str,
    room_coverage: dict[str, dict[str, int]],
    breaks_by_room: dict[str, list[tuple[str, str]]],
    r_staff: int,
    r_child: int,
) -> tuple[str, str, bool]:
    """
    Scan the shift for a compliant break window.
    Returns (start, end, still_conflict).
    """
    slots = sorted(room_coverage.get(room_id, {}).keys())
    step  = timedelta(minutes=15)

    try:
        current = datetime.strptime(shift_start[:8], "%H:%M:%S")
        end_dt  = datetime.strptime(shift_end[:8],   "%H:%M:%S")
    except Exception:
        return shift_start, shift_end, True

    while current + timedelta(minutes=dur_minutes) <= end_dt:
        b_s = current.strftime("%H:%M:%S")
        b_e = (current + timedelta(minutes=dur_minutes)).strftime("%H:%M:%S")
        conflict, _ = _check_break_impact(
            b_s, b_e, room_id, user_id,
            room_coverage, breaks_by_room, r_staff, r_child,
        )
        if conflict == "ok":
            return b_s, b_e, False
        current += step

    return shift_start, shift_end, True


def _shift_break_to_window(
    b_start: str,
    b_end: str,
    dur_minutes: int,
    shift_start: str,
    shift_end: str,
    preferred_from: str,
    preferred_until: str,
) -> tuple[str, str]:
    """
    Try to move a break so it falls within preferred_from–preferred_until.
    Falls back to original times if the preferred window doesn't fit.
    """
    try:
        pref_from = datetime.strptime(preferred_from, "%H:%M:%S")
        pref_to   = datetime.strptime(preferred_until, "%H:%M:%S")
        ss        = datetime.strptime(shift_start[:8], "%H:%M:%S")
        se        = datetime.strptime(shift_end[:8],   "%H:%M:%S")
        b_dur     = timedelta(minutes=dur_minutes)

        window_start = max(ss, pref_from)
        window_end   = min(se, pref_to)

        if window_start + b_dur <= window_end:
            mid = window_start + (window_end - window_start - b_dur) / 2
            return mid.strftime("%H:%M:%S"), (mid + b_dur).strftime("%H:%M:%S")
    except Exception:
        pass

    return b_start, b_end
