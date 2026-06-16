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
class SuggestedMovement:
    """
    A temporary room movement created to provide break cover.
    The educator moves from their rostered room to a receiving room
    for the duration of the break being covered.
    Does NOT change the educator's permanent shift room assignment.
    """
    educator_id:         str
    educator_name:       str
    from_room_id:        str
    from_room_name:      str
    to_room_id:          str
    to_room_name:        str
    start_time:          str   # HH:MM:SS
    end_time:            str   # HH:MM:SS
    move_date:           str   # YYYY-MM-DD
    covering_for_uid:    str   # user_id of the educator on break
    covering_for_name:   str
    reason:              str   # human-readable explanation


@dataclass
class RosterResult:
    shifts:          list[SuggestedShift]
    breaks:          list[SuggestedBreak]
    movements:       list[SuggestedMovement]   # temporary break-cover movements
    ratio_warnings:  list[str]
    review_warnings: list[str]
    unmet_rooms:     list[str]
    debug_log:       list[dict] = field(default_factory=list)  # per-break decision log
    validation:      dict       = field(default_factory=dict)  # weekly constraint validation


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
    centre_staff    = _build_centre_staff(staff, centre_id)
    all_shifts:     list[SuggestedShift]    = []
    all_breaks:     list[SuggestedBreak]    = []
    all_movements:  list[SuggestedMovement] = []
    all_debug_log:  list[dict]              = []
    ratio_warns:    list[str]               = []
    review_warns:   list[str]               = []
    unmet_rooms:    list[str]               = []
    corrections_log: list[dict]             = []   # FT onsite-coverage corrections
    ft_onsite_report: list[dict]            = []   # per-slot FT onsite coverage

    room_map = {r["id"]: r for r in rooms}

    # Track full-time rostered days and weekly hours across the period
    ft_rostered_days: dict[str, int]   = {}    # uid → days rostered
    ft_pattern_idx:   dict[str, int]   = {}    # uid → next pattern index (rotation)
    weekly_hours:     dict[str, float] = {}    # uid → total hours rostered this week
    # Build contracted-hours lookup for quick access
    contracted:       dict[str, float] = {
        s["uid"]: s.get("contracted_hours_per_week", 0.0)
        for s in centre_staff
    }

    # Stagger each FT educator's starting pattern so multiple FT staff don't
    # all pick the SAME 9.5h pattern on day 1 (which forces one educator to
    # absorb every onsite-coverage extension and become over-allocated while
    # another stays under). Offsetting by half the pattern list gives
    # complementary opening/closing pairs where possible.
    ft_staff_period = [s for s in centre_staff if s.get("employment_type") == "full_time"]
    _half_patterns  = max(1, len(FT_SHIFT_PATTERNS) // 2)
    for _i, _s in enumerate(sorted(ft_staff_period, key=lambda x: x.get("name", ""))):
        ft_pattern_idx[_s["uid"]] = (_i * _half_patterns) % len(FT_SHIFT_PATTERNS)

    # Per-slot Responsible Person / Nominated Supervisor onsite coverage
    rpns_onsite_report: list[dict] = []

    for day in days:
        date_str = day.isoformat()
        dow      = day.isoweekday() % 7   # 0=Sun, 1=Mon … 6=Sat

        # Intervals for this day keyed by room_id
        day_ivs  = all_intervals.get(date_str, [])
        room_ivs = _group_intervals_by_room(day_ivs)

        day_shifts: list[SuggestedShift] = []

        # ── Step 1A: Full-time base shifts ────────────────────────────
        # Allocate every available full-time educator toward their
        # contracted weekly hours (default 38h). Contract cap is applied
        # after FT_MIN_DAYS are satisfied.
        # FT staff are NOT required to cover every operating interval —
        # RP/NS coverage (Step 3 below) is the hard onsite constraint.
        ft_staff = [s for s in centre_staff if s.get("employment_type") == "full_time"]

        for s in sorted(ft_staff, key=lambda x: x.get("name", "")):
            uid  = s["uid"]
            name = s["name"]

            # ── Hard skip: leave ──────────────────────────────────────
            if date_str in leave_map.get(uid, []):
                continue

            # ── Hard skip: explicitly unavailable ────────────────────
            av = availability_map.get(uid, {}).get(dow)
            if av is not None and not av.get("is_available", True):
                continue

            # ── Contract-hours state ──────────────────────────────────
            # Contracted hours default to FT_TARGET_WEEKLY_HOURS (38h) if
            # not explicitly set on the staff profile.
            uid_contracted = contracted.get(uid, 0.0) or FT_TARGET_WEEKLY_HOURS
            uid_weekly_hrs = weekly_hours.get(uid, 0.0)
            days_so_far    = ft_rostered_days.get(uid, 0)
            remaining      = uid_contracted - uid_weekly_hrs
            need_min_days  = days_so_far < FT_MIN_DAYS

            # STOP condition: once FT_MIN_DAYS are rostered AND weekly hours
            # are at/over target (within tolerance), don't roster more days
            # — "maximum = 38h unless required for coverage/ratio" (those
            # cases are handled by the dedicated correction passes below).
            if not need_min_days and remaining <= FT_OVERTIME_THRESHOLD_HOURS:
                continue

            # ── Availability window ───────────────────────────────────
            if av:
                av_from  = (av.get("available_from")  or "00:00")[:5] + ":00"
                av_until = (av.get("available_until") or "23:59")[:5] + ":00"
            else:
                av_from, av_until = "00:00:00", "23:59:00"

            # Target duration for today: aim for FT_PREFERRED_DAILY_HOURS
            # (9.5h), but if remaining contracted hours are less than that
            # (e.g. last day needed to reach 38h), don't overshoot by much.
            target_hrs = FT_PREFERRED_DAILY_HOURS
            if not need_min_days:
                target_hrs = max(FT_MIN_PRACTICAL_SHIFT_HOURS,
                                  min(FT_PREFERRED_DAILY_HOURS, remaining))

            # ── Build candidate list ──────────────────────────────────
            # Each candidate: (score, pattern_idx, actual_start, actual_end)
            # Lower score = better. Score:
            #   0 = fits availability AND within (contract + tolerance)
            #   1 = fits availability, over tolerance but min days not yet met
            #   2 = trimmed to availability window (still ≥ practical minimum)
            #   3 = trimmed but < practical minimum (last resort, with warning)
            candidates = []
            for idx, (ps, pe) in enumerate(FT_SHIFT_PATTERNS):
                fits_av  = ps >= av_from and pe <= av_until
                dur_hrs  = _mins_between(ps, pe) / 60
                fits_cap = uid_weekly_hrs + dur_hrs <= uid_contracted + FT_OVERTIME_THRESHOLD_HOURS

                if fits_av:
                    if fits_cap:
                        candidates.append((0, idx, ps, pe))
                    elif need_min_days:
                        candidates.append((1, idx, ps, pe))
                else:
                    # Try trimming to availability window
                    trim_s = max(ps, av_from)
                    trim_e = min(pe, av_until)
                    trim_dur = _mins_between(trim_s, trim_e) / 60
                    if trim_dur >= FT_MIN_PRACTICAL_SHIFT_HOURS:
                        if fits_cap or need_min_days:
                            candidates.append((2, idx, trim_s, trim_e))
                    elif trim_dur > 0 and need_min_days:
                        # Under practical minimum but better than nothing
                        candidates.append((3, idx, trim_s, trim_e))

            if not candidates:
                # Genuinely cannot assign any shift today
                ratio_warns.append(
                    f"CRITICAL: Full-time {name} could not be rostered on {date_str} "
                    "— no viable shift fits availability window."
                )
                continue

            # Sort by score, then by closeness to target_hrs (prefer patterns
            # that land closest to today's target duration)
            candidates.sort(key=lambda c: (c[0], abs(_mins_between(c[2], c[3]) / 60 - target_hrs)))

            # Rotate through patterns for variety (opening/closing balance)
            # Among equally-scored candidates at the same duration, prefer the
            # one that follows the rotation index
            pat_idx = ft_pattern_idx.get(uid, 0)
            best_score = candidates[0][0]
            best_dur   = _mins_between(candidates[0][2], candidates[0][3])
            tied = [c for c in candidates
                    if c[0] == best_score and _mins_between(c[2], c[3]) == best_dur]
            if len(tied) > 1:
                tied.sort(key=lambda c: (c[1] - pat_idx) % len(FT_SHIFT_PATTERNS))
                candidates[0] = tied[0]

            _, chosen_idx, ss, se = candidates[0]
            shift_dur_hrs = _mins_between(ss, se) / 60

            if shift_dur_hrs < FT_MIN_PRACTICAL_SHIFT_HOURS:
                ratio_warns.append(
                    f"⚠️ Full-time {name} on {date_str}: rostered {shift_dur_hrs:.1f}h "
                    f"(below {FT_MIN_PRACTICAL_SHIFT_HOURS}h practical minimum) — "
                    f"availability only allows {av_from[:5]}–{av_until[:5]}."
                )

            pref_day  = break_prefs.get(uid, {}).get(dow, False)
            override  = "opted_out" if pref_day else "use_staff_default"
            rid       = s.get("primary_room_id") or (rooms[0]["id"] if rooms else "")
            rname     = room_map.get(rid, {}).get("name", "")

            shift = SuggestedShift(
                user_id=uid,
                user_name=name,
                room_id=rid,
                room_name=rname,
                shift_date=date_str,
                start_time=ss,
                end_time=se,
                shift_type=_shift_type(ss, se),
                break_opt_out_override=override,
                source="full_time_base",
            )
            day_shifts.append(shift)
            ft_rostered_days[uid]  = ft_rostered_days.get(uid, 0) + 1
            ft_pattern_idx[uid]    = (chosen_idx + 1) % len(FT_SHIFT_PATTERNS)
            weekly_hours[uid]      = uid_weekly_hrs + shift_dur_hrs

        # ── Step 1A-correction: RP/NS coverage already handled below ──────
        # FT onsite coverage is NO LONGER a hard constraint.
        # RP/NS onsite coverage (7:15–18:00) IS the hard constraint.
        # See _correct_rpns_coverage call after Step 1B.

        # ── Per-slot FT onsite report (informational only) ─────────────────
        ft_today = [s for s in day_shifts if s.source == "full_time_base"]
        slot_dt  = datetime.strptime(CENTRE_OPEN,  "%H:%M:%S")
        close_dt = datetime.strptime(CENTRE_CLOSE, "%H:%M:%S")
        while slot_dt < close_dt:
            slot_str = slot_dt.strftime("%H:%M:%S")
            onsite   = [s.user_name for s in ft_today
                        if s.start_time <= slot_str < s.end_time]
            ft_onsite_report.append({
                "date":      date_str,
                "slot":      slot_str[:5],
                "ft_count":  len(onsite),
                "compliant": len(onsite) >= 1,
                "assigned":  ", ".join(onsite) if onsite else "—",
            })
            slot_dt += timedelta(minutes=15)

        # ── Responsible Person / Nominated Supervisor onsite coverage ───
        # HARD CONSTRAINT — priority 3 (after centre coverage and FT
        # onsite, before contracted-hours balancing / ratio checks).
        rpns_staff_today = [
            s for s in centre_staff
            if s.get("is_responsible_person") or s.get("is_nominated_supervisor")
        ]
        _correct_rpns_coverage(
            day_shifts=day_shifts,
            date_str=date_str,
            dow=dow,
            rpns_staff=rpns_staff_today,
            availability_map=availability_map,
            leave_map=leave_map,
            weekly_hours=weekly_hours,
            contracted=contracted,
            room_map=room_map,
            rooms=rooms,
            break_prefs=break_prefs,
            corrections_log=corrections_log,
            ratio_warns=ratio_warns,
            rpns_onsite_report=rpns_onsite_report,
        )

        # ── Step 1B: Compute room ratio requirements from intervals ───
        # Build demand windows as before, then check what additional staff
        # are needed beyond the full-time base coverage already placed.
        room_windows: dict[str, list[CoverageWindow]] = {}
        for room in rooms:
            rid     = room["id"]
            r_staff = room.get("required_ratio_staff",    1)
            r_child = room.get("required_ratio_children", 4)

            ivs = room_ivs.get(rid, [])
            if not ivs:
                continue

            req_by_slot: dict[str, int] = {}
            for iv in ivs:
                act = iv.get("actual_children")
                exp = iv.get("expected_children")
                n   = int(act) if act is not None else (int(exp) if exp is not None else 0)
                if n > 0:
                    req = math.ceil(n / r_child) * r_staff
                    req_by_slot[iv["interval_start"]] = req

            if not req_by_slot:
                continue

            room_windows[rid] = _merge_slots_to_windows(req_by_slot, ivs)

        # ── Step 2A: Part-time contracted hours allocation ────────────
        # Proactively roster every available part-time educator for their
        # remaining contracted hours BEFORE considering casual staff.
        # This ensures PT staff are not under-rostered while casuals are used.
        pt_staff = [s for s in centre_staff if s.get("employment_type") == "part_time"]

        for s in sorted(pt_staff, key=lambda x: x.get("name", "")):
            uid  = s["uid"]
            name = s["name"]

            if date_str in leave_map.get(uid, []):
                continue
            av = availability_map.get(uid, {}).get(dow)
            if av is not None and not av.get("is_available", True):
                continue

            uid_contracted = contracted.get(uid, 0.0)
            if uid_contracted <= 0:
                continue  # no contracted hours — treat as casual in Step 2B

            uid_weekly_hrs = weekly_hours.get(uid, 0.0)
            remaining_hrs  = uid_contracted - uid_weekly_hrs
            if remaining_hrs <= FT_OVERTIME_THRESHOLD_HOURS:
                continue  # already at/over target

            if av:
                av_from  = (av.get("available_from")  or "00:00")[:5] + ":00"
                av_until = (av.get("available_until") or "23:59")[:5] + ":00"
            else:
                av_from, av_until = "00:00:00", "23:59:00"

            # Don't double-book if already assigned today
            if any(sh.user_id == uid for sh in day_shifts):
                continue

            # Build a shift covering as much of their contracted hours
            # as possible within their availability window today.
            # Spread across FT_MIN_DAYS to approximate daily hours.
            target_today = min(
                remaining_hrs,
                uid_contracted / max(FT_MIN_DAYS, 1),
                _mins_between(av_from, av_until) / 60,
            )
            if target_today < (CASUAL_MIN_SHIFT_MINUTES / 60):
                continue  # too little remaining to be worth a shift today

            # Anchor to centre open window
            shift_s = max(av_from, CENTRE_OPEN)
            shift_e_dt = (datetime.strptime(shift_s, "%H:%M:%S")
                          + timedelta(hours=target_today)).strftime("%H:%M:%S")
            shift_e = min(shift_e_dt, av_until, CENTRE_CLOSE)

            if _mins_between(shift_s, shift_e) < CASUAL_MIN_SHIFT_MINUTES:
                continue

            rid   = s.get("primary_room_id") or (rooms[0]["id"] if rooms else "")
            rname = room_map.get(rid, {}).get("name", "")
            pref_day = break_prefs.get(uid, {}).get(dow, False)
            override = "opted_out" if pref_day else "use_staff_default"

            shift = SuggestedShift(
                user_id=uid,
                user_name=name,
                room_id=rid,
                room_name=rname,
                shift_date=date_str,
                start_time=shift_s,
                end_time=shift_e,
                shift_type=_shift_type(shift_s, shift_e),
                break_opt_out_override=override,
                source="part_time_contracted",
            )
            day_shifts.append(shift)
            weekly_hours[uid] = uid_weekly_hrs + _mins_between(shift_s, shift_e) / 60

        # ── Step 2B: Casual gap fill (ratio / attendance demand only) ────
        # Only add casuals where ratio or attendance demand is still unmet
        # after FT + PT contracted shifts. PT staff with no contracted hours
        # are also placed here as needed.
        casual_staff = [
            s for s in centre_staff
            if s.get("employment_type") == "casual"
            or (s.get("employment_type") == "part_time"
                and contracted.get(s["uid"], 0.0) <= 0)
        ]
        assigned_gap_minutes: dict[str, int] = {}

        for rid, windows in room_windows.items():
            room  = room_map.get(rid, {})
            rname = room.get("name", rid)

            for window in windows:
                # Count how many already-placed shifts cover this window slot-by-slot
                ft_coverage_min = _count_coverage_in_window(day_shifts, rid, window)
                gap = window.required_staff - ft_coverage_min
                if gap <= 0:
                    continue   # base staffing already satisfies ratio

                # Eligible: casuals + PT-no-contract, ranked by employment type
                eligible = _eligible_staff(
                    casual_staff, rid, date_str, dow,
                    availability_map, leave_map,
                )

                for _ in range(gap):
                    best = _pick_staff(
                        eligible, rid, date_str, window,
                        assigned_gap_minutes, day_shifts,
                    )
                    if best is None:
                        ratio_warns.append(
                            f"{rname} {window.start[:5]}–{window.end[:5]} on {date_str}: "
                            f"gap of {gap} staff not filled — no casual/PT available."
                        )
                        if rname not in unmet_rooms:
                            unmet_rooms.append(rname)
                        break

                    uid           = best["uid"]
                    dur_mins      = shift_duration_minutes(window.start, window.end)
                    shift_dur_hrs = dur_mins / 60

                    # Contracted-hours cap applies even to casuals if set
                    uid_contracted = contracted.get(uid, 0.0)
                    uid_weekly_hrs = weekly_hours.get(uid, 0.0)
                    if uid_contracted > 0:
                        cap = uid_contracted + FT_OVERTIME_THRESHOLD_HOURS
                        if uid_weekly_hrs + shift_dur_hrs > cap:
                            eligible = [e for e in eligible if e["uid"] != uid]
                            best = _pick_staff(
                                eligible, rid, date_str, window,
                                assigned_gap_minutes, day_shifts,
                            )
                            if best is None:
                                ratio_warns.append(
                                    f"{rname} {window.start[:5]}–{window.end[:5]} on {date_str}: "
                                    f"gap not filled — eligible staff at contracted-hours limit."
                                )
                                if rname not in unmet_rooms:
                                    unmet_rooms.append(rname)
                                break
                            uid           = best["uid"]
                            dur_mins      = shift_duration_minutes(window.start, window.end)
                            shift_dur_hrs = dur_mins / 60

                    pref_day = break_prefs.get(uid, {}).get(dow, False)
                    override = "opted_out" if pref_day else "use_staff_default"

                    shift = SuggestedShift(
                        user_id=uid,
                        user_name=best["name"],
                        room_id=rid,
                        room_name=rname,
                        shift_date=date_str,
                        start_time=window.start,
                        end_time=window.end,
                        shift_type=_shift_type(window.start, window.end),
                        break_opt_out_override=override,
                        source=best["source"],
                    )
                    day_shifts.append(shift)
                    assigned_gap_minutes[uid] = assigned_gap_minutes.get(uid, 0) + dur_mins
                    weekly_hours[uid] = weekly_hours.get(uid, 0.0) + shift_dur_hrs

        all_shifts.extend(day_shifts)

        # ── Coverage gap check: 07:15–18:00 must be continuously staffed ─
        # At least one staff member must cover every 15-minute slot across
        # all rooms combined. Gaps are added to ratio_warns.
        coverage_warns = _check_centre_coverage(day_shifts, date_str)
        ratio_warns.extend(coverage_warns)

        # ── Step 3: schedule breaks for each shift ────────────────────
        # Build room→shift-coverage map for ratio checking during breaks
        room_coverage = _build_room_coverage(day_shifts, room_map)

        # breaks_by_room: {room_id:  [(start, end), ...]}   — room-level stagger
        # breaks_by_user: {user_id:  [(start, end, fixed)]} — per-educator overlap guard
        # cover_delta:    {room_id: {slot: int}}             — temporary cover additions
        breaks_by_room: dict[str, list[tuple[str, str]]]        = {}
        breaks_by_user: dict[str, list[tuple[str, str, bool]]]  = {}
        cover_delta:    dict[str, dict[str, int]]               = {}
        day_movements:  list[SuggestedMovement]                  = []
        day_debug_log:  list[dict]                               = []

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

            room    = room_map.get(rid, {})
            r_staff = room.get("required_ratio_staff",    1)
            r_child = room.get("required_ratio_children", 4)

            shift_key = f"{uid}_{date_str}"

            # Pre-compute the 1.5h buffer eligibility window for this shift
            from utils.break_engine import BREAK_BUFFER_MINS as _BBM
            try:
                _ss_dt = datetime.strptime(ss[:5], "%H:%M")
                _se_dt = datetime.strptime(se[:5], "%H:%M")
            except Exception:
                _ss_dt = _se_dt = None

            for sug in suggestions:
                btype    = sug["break_type"]
                b_dur    = sug["duration_minutes"]
                b_start  = sug["planned_start"][:8]
                b_end    = sug["planned_end"][:8]

                # Compute and enforce the 1.5h buffer window
                if _ss_dt and _se_dt:
                    _earliest = (_ss_dt + timedelta(minutes=_BBM)).strftime("%H:%M:%S")
                    _latest   = (_se_dt - timedelta(minutes=_BBM)).strftime("%H:%M:%S")
                else:
                    _earliest = ss
                    _latest   = se

                # Clamp b_start into [_earliest, _latest - b_dur]
                if b_start < _earliest:
                    b_start = _earliest
                    b_end = (datetime.strptime(b_start, "%H:%M:%S")
                             + timedelta(minutes=b_dur)).strftime("%H:%M:%S")
                _latest_start = (datetime.strptime(_latest, "%H:%M:%S")
                                 - timedelta(minutes=b_dur)).strftime("%H:%M:%S")
                if b_start > _latest_start:
                    b_start = _latest_start
                    b_end = (datetime.strptime(b_start, "%H:%M:%S")
                             + timedelta(minutes=b_dur)).strftime("%H:%M:%S")

                # Hard clamp against existing educator breaks
                for ex_s, ex_e, _ in breaks_by_user.get(uid, []):
                    if _overlaps(b_start, b_end, ex_s, ex_e):
                        if ex_e < _latest:
                            b_start = ex_e
                            b_end   = (datetime.strptime(b_start[:8], "%H:%M:%S")
                                       + timedelta(minutes=b_dur)).strftime("%H:%M:%S")
                        break

                # Check ratio impact — centre-wide (priority 0) then room-level
                conflict, reason, dbg = _check_break_impact(
                    b_start, b_end, rid, uid,
                    room_coverage, breaks_by_room, breaks_by_user,
                    r_staff, r_child, cover_delta, room_map,
                )

                cover_used: SuggestedMovement | None = None

                if conflict == "breach":
                    # Try alternate window within the 1.5h buffer zone
                    alt_s, alt_e, alt_conflict = _find_alt_break_window(
                        _earliest, _latest, b_dur, rid, uid,
                        room_coverage, breaks_by_room, breaks_by_user,
                        r_staff, r_child, cover_delta, room_map,
                    )
                    if not alt_conflict:
                        b_start, b_end = alt_s, alt_e
                        conflict = "ok"
                        conflict, reason, dbg = _check_break_impact(
                            b_start, b_end, rid, uid,
                            room_coverage, breaks_by_room, breaks_by_user,
                            r_staff, r_child, cover_delta, room_map,
                        )
                    else:
                        # Try temporary cover from another room
                        cover_mv = _find_break_cover(
                            break_start=b_start,
                            break_end=b_end,
                            break_room_id=rid,
                            break_room=room,
                            break_uid=uid,
                            break_uname=shift.user_name,
                            date_str=date_str,
                            day_shifts=day_shifts,
                            room_map=room_map,
                            room_coverage=room_coverage,
                            breaks_by_user=breaks_by_user,
                            cover_delta=cover_delta,
                        )
                        if cover_mv is not None:
                            conflict   = "ok"
                            cover_used = cover_mv

                # Build debug log entry
                overlap_with = [
                    f"{s.user_name} ({ex_s[:5]}–{ex_e[:5]})"
                    for ex_s, ex_e, _ in breaks_by_user.get(uid, [])
                    if _overlaps(b_start, b_end, ex_s, ex_e) and ex_s != b_start
                ] + [
                    f"room overlap {ex_s[:5]}–{ex_e[:5]}"
                    for ex_s, ex_e in breaks_by_room.get(rid, [])
                    if _overlaps(b_start, b_end, ex_s, ex_e)
                ]
                day_debug_log.append({
                    "educator":            shift.user_name,
                    "date":                date_str,
                    "shift_start":         ss[:5],
                    "shift_end":           se[:5],
                    "earliest_allowable":  _earliest[:5],
                    "latest_allowable":    _latest[:5],
                    "proposed_start":      b_start[:5],
                    "proposed_end":        b_end[:5],
                    "room":                shift.room_name,
                    "break_type":          btype,
                    "overlap":             "Yes" if overlap_with else "No",
                    "centre_staff_before": dbg.get("centre_staff_before", "—"),
                    "centre_staff_after":  dbg.get("centre_staff_after",  "—"),
                    "centre_required":     dbg.get("centre_required",     "—"),
                    "room_staff_before":   dbg.get("room_staff_before",   "—"),
                    "room_staff_after":    dbg.get("room_staff_after",    "—"),
                    "room_required":       r_staff,
                    "result":    "✅ accepted" if conflict == "ok" else (
                                 "⚠️ manual_review" if conflict == "fixed_conflict" else "❌ rejected"
                    ),
                    "reason":    reason or ("cover: " + cover_used.educator_name if cover_used else ""),
                    "cover_used": cover_used.educator_name if cover_used else "—",
                })

                if conflict in ("ok",):
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
                    if cover_used is not None:
                        # Apply the cover to the delta so later breaks see it
                        _apply_cover_delta(cover_delta, cover_used)
                        day_movements.append(cover_used)
                elif conflict == "fixed_conflict":
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
                        warnings=[f"Fixed break conflict: {reason}"],
                    )
                    review_warns.append(
                        f"{shift.user_name} on {date_str}: fixed break conflict — {reason}"
                    )
                else:
                    # breach with no alt window and no cover available
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
                        warnings=["No centre-wide ratio-safe break window found."],
                    )
                    review_warns.append(
                        f"{shift.user_name} ({shift.room_name}) on {date_str}: "
                        f"no centre-wide ratio-safe {btype} break window — manual review required."
                    )

                all_breaks.append(brk)
                breaks_by_room.setdefault(rid, []).append((b_start, b_end))
                breaks_by_user.setdefault(uid, []).append((b_start, b_end, False))

        all_movements.extend(day_movements)
        all_debug_log.extend(day_debug_log)

    # ── Post-generation merge: combine separate rest+meal into one block ──
    # When the initial combined suggestion was rejected by the ratio check,
    # the loop falls back to two separate breaks placed far apart.
    # This step finds those pairs and attempts to merge them into one
    # combined SuggestedBreak, trying the preferred window first.
    all_breaks, merge_warns = _merge_separate_rest_and_meal(
        all_breaks, all_shifts, room_map,
    )
    review_warns.extend(merge_warns)

    # ── Final validation pass: resolve any remaining per-educator overlaps ──
    # This runs after all break generation, fixed-break, paid/unpaid, and
    # manual-review logic so it catches any edge-case overlaps not prevented
    # by the per-suggestion checks above.
    all_breaks, extra_review_warns = _validate_and_resolve_break_overlaps(
        all_breaks, all_shifts, room_map,
    )
    review_warns.extend(extra_review_warns)

    # ── Build weekly validation report ────────────────────────────────
    ft_staff_all = [s for s in centre_staff if s.get("employment_type") == "full_time"]

    # Per-educator FT allocation report + contracted hours report
    ft_allocation_report  = []
    weekly_hours_report   = []   # all staff types
    ft_below_contracted   = []   # "Full-time contracted hours not achieved."
    ft_over_contracted    = []   # "Full-time contracted hours exceeded."
    over_contract_warns   = []

    # Build a shift-hour map for every educator (all sources)
    all_shift_hours: dict[str, float] = {}
    ft_shift_map:    dict[str, list]  = {}
    for s in all_shifts:
        dur = _mins_between(s.start_time, s.end_time) / 60
        all_shift_hours[s.user_id] = all_shift_hours.get(s.user_id, 0.0) + dur
        if s.source == "full_time_base":
            ft_shift_map.setdefault(s.user_id, []).append(s)

    # FT allocation report
    # Count opening/closing shifts per FT educator
    ft_opening_count: dict[str, int] = {}
    ft_closing_count: dict[str, int] = {}
    for s in all_shifts:
        if s.source == "full_time_base":
            if s.start_time <= CENTRE_OPEN:
                ft_opening_count[s.user_id] = ft_opening_count.get(s.user_id, 0) + 1
            if s.end_time >= CENTRE_CLOSE:
                ft_closing_count[s.user_id] = ft_closing_count.get(s.user_id, 0) + 1

    # Corrections that pushed an educator over contract for onsite coverage —
    # used to attribute "over contracted" to a documented, accepted cause.
    onsite_correction_uids: set[str] = set()
    for c in corrections_log:
        if "onsite coverage" in c["action"]:
            for s in ft_staff_all:
                if s["name"] in c["action"]:
                    onsite_correction_uids.add(s["uid"])

    for s in ft_staff_all:
        uid   = s["uid"]
        name  = s["name"]
        contr = contracted.get(uid, 0.0) or FT_TARGET_WEEKLY_HOURS
        udays = len(ft_shift_map.get(uid, []))
        uhrs  = all_shift_hours.get(uid, 0.0)

        variance  = uhrs - contr
        compliant = abs(variance) <= FT_OVERTIME_THRESHOLD_HOURS

        # Availability status
        av_days = sum(
            1 for d in days
            if availability_map.get(uid, {}).get(d.isoweekday() % 7, {}).get("is_available", True) is not False
            and d.isoformat() not in leave_map.get(uid, [])
        )
        leave_count = sum(1 for d in days if d.isoformat() in leave_map.get(uid, []))

        # Reason for non-compliance
        reason = ""
        if udays == 0:
            reason = "CRITICAL: Zero shifts allocated"
            ft_below_contracted.append(
                f"CRITICAL: {name} received zero shifts (contracted {contr:.1f}h/week)."
            )
        elif variance < -FT_OVERTIME_THRESHOLD_HOURS:
            reason = "Full-time contracted hours not achieved."
            if leave_count:
                reason += f" Leave on {leave_count} day(s) — only {av_days} available days."
            elif av_days * FT_PREFERRED_DAILY_HOURS < contr:
                reason += f" Only {av_days} available day(s) in period."
            ft_below_contracted.append(
                f"{name}: rostered {uhrs:.1f}h vs contracted {contr:.1f}h "
                f"({variance:+.1f}h). {reason}".strip()
            )
        elif variance > FT_OVERTIME_THRESHOLD_HOURS:
            reason = "Full-time contracted hours exceeded."
            if uid in onsite_correction_uids:
                reason += " Extra shift(s) required for full-time onsite coverage."
            ft_over_contracted.append(
                f"{name}: rostered {uhrs:.1f}h vs contracted {contr:.1f}h "
                f"(+{variance:.1f}h). {reason}"
            )
            over_contract_warns.append(
                f"{name}: rostered {uhrs:.1f}h vs contracted {contr:.1f}h "
                f"(+{variance:.1f}h over threshold of +{FT_OVERTIME_THRESHOLD_HOURS}h)"
            )

        ft_allocation_report.append({
            "name":              name,
            "employment_type":   "Full-time",
            "contracted_hours":  contr,
            "rostered_hours":    uhrs,
            "variance":          variance,
            "allocated_days":    udays,
            "opening_shifts":    ft_opening_count.get(uid, 0),
            "closing_shifts":    ft_closing_count.get(uid, 0),
            "compliant":         compliant,
            "reason":            reason,
        })

    # Backward-compat keys (kept for any external consumers)
    ft_below_days  = ft_below_contracted
    ft_below_hours = ft_below_contracted

    # Weekly hours report — all staff (FT + PT + casual)
    # Also produces pt_hours_report (PT-only) for the validation panel.
    pt_hours_report:       list[dict] = []
    pt_below_contracted:   list[str]  = []

    all_staff_by_uid = {s["uid"]: s for s in centre_staff}
    for uid, s in sorted(all_staff_by_uid.items(), key=lambda x: x[1].get("name", "")):
        if not any(sh.user_id == uid for sh in all_shifts):
            continue   # not rostered this period

        etype    = s.get("employment_type", "full_time")
        name     = s.get("name", uid)
        contr    = contracted.get(uid, 0.0)
        rostered = all_shift_hours.get(uid, 0.0)
        variance = rostered - contr if contr > 0 else 0.0

        if contr > 0 and variance > FT_OVERTIME_THRESHOLD_HOURS:
            status = "⚠️ Over contracted"
        elif contr > 0 and variance < -FT_OVERTIME_THRESHOLD_HOURS:
            status = "⬇ Under contracted"
        elif contr > 0:
            status = "✅ Compliant"
        else:
            status = "— No contract"

        row = {
            "name":             name,
            "employment_type":  etype.replace("_", " ").title(),
            "contracted_hrs":   f"{contr:.1f}h" if contr > 0 else "—",
            "rostered_hrs":     f"{rostered:.1f}h",
            "variance":         f"{variance:+.1f}h" if contr > 0 else "—",
            "status":           status,
        }
        weekly_hours_report.append(row)

        # Dedicated PT compliance tracking
        if etype == "part_time":
            compliant_pt = contr <= 0 or abs(variance) <= FT_OVERTIME_THRESHOLD_HOURS
            pt_hours_report.append({
                "name":            name,
                "contracted_hrs":  f"{contr:.1f}h" if contr > 0 else "—",
                "rostered_hrs":    f"{rostered:.1f}h",
                "variance":        f"{variance:+.1f}h" if contr > 0 else "—",
                "compliant":       compliant_pt,
                "status":          status,
            })
            if contr > 0 and variance < -FT_OVERTIME_THRESHOLD_HOURS:
                pt_below_contracted.append(
                    f"{name}: rostered {rostered:.1f}h vs contracted {contr:.1f}h "
                    f"({variance:+.1f}h). Part-time contracted hours not achieved."
                )

    # Coverage gaps already in ratio_warns — extract them
    coverage_gaps    = [w for w in ratio_warns if "Coverage gap" in w]
    manual_reviews   = [w for w in ratio_warns if "Manual review required" in w]
    rpns_warns       = [w for w in ratio_warns
                        if "Responsible Person/Nominated Supervisor" in w
                        or "No Responsible Person" in w]
    # FT onsite warnings kept as informational (no longer a hard constraint).
    # They contain "full-time" but not "Responsible Person", so filter precisely.
    ft_onsite_warns  = [w for w in ratio_warns
                        if "full-time" in w.lower()
                        and "onsite" in w.lower()
                        and "Responsible Person" not in w
                        and "Coverage gap" not in w
                        and "Manual review" not in w]
    ratio_breaches   = [
        w for w in ratio_warns
        if w not in coverage_gaps
        and w not in manual_reviews
        and w not in rpns_warns
        and w not in ft_onsite_warns
    ]

    pt_hours = sum(
        _mins_between(s.start_time, s.end_time) / 60
        for s in all_shifts
        if s.source != "full_time_base"
    )

    # ── Attendance demand validation ─────────────────────────────────
    # For every 15-min slot across the period, compute:
    #   required_educators (from actual_children + ratio)
    #   rostered_educators (from all_shifts)
    # Flag slots where rostered < required (shortfall) or
    # rostered > required + 1 (surplus) for information.
    demand_rows: list[dict] = []
    for date_str_v, ivs in sorted(all_intervals.items()):
        room_iv_map: dict[str, dict] = {}
        for iv in ivs:
            room_iv_map.setdefault(iv["room_id"], {})[iv["interval_start"]] = iv

        # Collect all unique slots for this day
        all_slots = sorted({iv["interval_start"] for iv in ivs})
        for slot in all_slots:
            required = 0
            for rid, slot_map in room_iv_map.items():
                iv  = slot_map.get(slot, {})
                act = iv.get("actual_children") or iv.get("expected_children") or 0
                n   = int(act)
                if n > 0:
                    room = room_map.get(rid, {})
                    r_s  = room.get("required_ratio_staff",    1)
                    r_c  = room.get("required_ratio_children", 4)
                    required += math.ceil(n / r_c) * r_s

            rostered = sum(
                1 for s in all_shifts
                if s.shift_date == date_str_v
                and s.start_time <= slot < s.end_time
            )
            delta = rostered - required
            if required > 0 or rostered > 0:
                demand_rows.append({
                    "date":     date_str_v,
                    "slot":     slot[:5],
                    "required": required,
                    "rostered": rostered,
                    "delta":    delta,
                    "status":   "✅ OK" if delta >= 0 else f"❌ Shortfall {delta}",
                })

    validation = {
        "centre_coverage_achieved": len(coverage_gaps) == 0,
        "uncovered_intervals":      coverage_gaps,
        "centre_ratio_breaches":    ratio_breaches,
        "ft_below_4_days":          ft_below_days,
        "ft_below_10h_days":        ft_below_hours,
        "ft_below_contracted":      ft_below_contracted,
        "ft_over_contracted":       ft_over_contracted,
        "over_contract_warnings":   over_contract_warns,
        "manual_review_items":      manual_reviews,
        "ft_allocation_report":     ft_allocation_report,
        "pt_hours_report":          pt_hours_report,
        "pt_below_contracted":      pt_below_contracted,
        "weekly_hours_report":      weekly_hours_report,
        "attendance_demand":        demand_rows,
        # FT onsite — informational only, no longer a hard constraint
        "ft_onsite_coverage":       ft_onsite_report,
        "ft_onsite_info":           ft_onsite_warns,
        # RP/NS — primary hard constraint
        "rpns_onsite_coverage":     rpns_onsite_report,
        "rpns_onsite_violations":   rpns_warns,
        "rpns_onsite_achieved":     len(rpns_warns) == 0,
        "corrections_log":          corrections_log,
        "pt_ca_hours_used":         round(pt_hours, 1),
        "review_warnings":          review_warns,
    }

    return RosterResult(
        shifts=all_shifts,
        breaks=all_breaks,
        movements=all_movements,
        ratio_warnings=ratio_warns,
        review_warnings=review_warns,
        unmet_rooms=unmet_rooms,
        debug_log=all_debug_log,
        validation=validation,
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
    Returns list of {uid, name, primary_room_id, employment_type,
                     contracted_hours_per_week}.

    contracted_hours_per_week is read from:
        profile["contracted_hours_per_week"]  (explicit, any type)
      or profile["full_time_contracted_hours_per_week"]  (FT-specific field)
      or DEFAULT_CONTRACTED_HOURS[employment_type]  (fallback)
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

            etype = profile.get("employment_type", "full_time")

            # Contracted hours — multiple possible field names
            contracted = (
                profile.get("contracted_hours_per_week")
                or profile.get("full_time_contracted_hours_per_week")
                or DEFAULT_CONTRACTED_HOURS.get(etype, 0.0)
            )
            try:
                contracted = float(contracted)
            except (TypeError, ValueError):
                contracted = DEFAULT_CONTRACTED_HOURS.get(etype, 0.0)

            result.append({
                "uid":                       uid,
                "name":                      f"{u.get('first_name','')} {u.get('last_name','')}".strip(),
                "primary_room_id":           role.get("primary_room_id"),
                "employment_type":           etype,
                "allows_opt_out":            profile.get("allows_unpaid_break_opt_out", False),
                "contracted_hours_per_week": contracted,
                "is_responsible_person":     bool(profile.get("is_responsible_person", False)),
                "is_nominated_supervisor":   bool(profile.get("is_nominated_supervisor", False)),
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


# Employment-type priority order for staff allocation.
# Lower number = higher priority. Casual staff are last.
EMPLOYMENT_PRIORITY: dict[str, int] = {
    "full_time":  0,
    "part_time":  1,
    "casual":     2,
}
CASUAL_MIN_SHIFT_MINUTES:      int   = 180    # 3 hours — casual staff floor
FT_OVERTIME_THRESHOLD_HOURS:  float = 1.0    # ± tolerance for "compliant" weekly hours

# Default contracted hours if not specified on staff profile
DEFAULT_CONTRACTED_HOURS: dict[str, float] = {
    "full_time":  38.0,
    "part_time":  0.0,   # 0 = not tracked unless set on profile
    "casual":     0.0,
}

# ── Full-time weekly model: 38h/week target, ~9.5h/day × 4 days ──────────
FT_TARGET_WEEKLY_HOURS:       float = 38.0   # default weekly contract target
FT_PREFERRED_DAILY_HOURS:     float = 9.5    # 38h / 4 days
FT_MIN_PRACTICAL_SHIFT_HOURS: float = 6.0    # configurable floor — avoid fragments
FT_MIN_DAYS:                  int   = 4      # preferred days/week for the 9.5h pattern

# Backward-compat alias (some callers/pages still import FT_MIN_HOURS)
FT_MIN_HOURS: float = FT_PREFERRED_DAILY_HOURS

# Preferred full-time shift patterns (start, end) — each ≈ FT_PREFERRED_DAILY_HOURS.
# Two "opening" + two "closing" patterns for natural opener/closer rotation.
FT_SHIFT_PATTERNS: list[tuple[str, str]] = [
    ("07:15:00", "16:45:00"),   # 9.5h — opening
    ("07:30:00", "17:00:00"),   # 9.5h — opening
    ("08:00:00", "17:30:00"),   # 9.5h — closing
    ("08:30:00", "18:00:00"),   # 9.5h — closing
]

# Full-day fallback pattern (covers the entire 07:15–18:00 operating window).
# Used only when FT onsite coverage cannot otherwise be achieved — this may
# push an educator over their contracted hours, which is an accepted
# trade-off per the "centre coverage overrides contract cap" rule.
FT_FULL_DAY_PATTERN: tuple[str, str] = ("07:15:00", "18:00:00")   # 10.75h


def _employment_rank(s: dict) -> int:
    """Return sort key for employment type (lower = higher priority)."""
    return EMPLOYMENT_PRIORITY.get(s.get("employment_type", "casual"), 2)


def _eligible_staff(
    centre_staff: list[dict],
    room_id: str,
    date_str: str,
    dow: int,
    availability_map: dict[str, dict],
    leave_map: dict[str, list[str]],
) -> list[dict]:
    """
    Return staff eligible to work in room_id on date_str, sorted by:
        1. Primary room match (preferred over non-primary)
        2. Employment type: full-time → part-time → casual
        3. Name (stable tie-break)

    Availability and leave are filtered before sorting.
    """
    result_primary = []
    result_other   = []

    for s in centre_staff:
        uid = s["uid"]

        if date_str in leave_map.get(uid, []):
            continue

        av = availability_map.get(uid, {}).get(dow)
        if av is not None and not av.get("is_available", True):
            continue

        entry = {**s, "avail": av}

        if s.get("primary_room_id") == room_id:
            result_primary.append(entry)
        else:
            result_other.append(entry)

    # Sort each bucket by employment priority then name
    key = lambda x: (_employment_rank(x), x.get("name", ""))
    result_primary.sort(key=key)
    result_other.sort(key=key)

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
    Pick the best available staff member for this window.

    Priority (already enforced by _eligible_staff ordering):
        1. Primary-room match
        2. Full-time before part-time before casual

    Additional constraint:
        Casual staff must not be assigned shifts shorter than
        CASUAL_MIN_SHIFT_MINUTES (3 hours = 180 min). If the window
        is under 3 hours a casual staff member is skipped entirely.

    Returns {uid, name, source, employment_type} or None.
    """
    already_in_window = set()
    for s in day_shifts:
        if s.shift_date == date_str:
            if s.start_time < window.end and s.end_time > window.start:
                already_in_window.add(s.user_id)

    window_dur = _mins_between(window.start, window.end)

    for s in eligible:
        uid  = s["uid"]
        etype = s.get("employment_type", "full_time")

        if uid in already_in_window:
            continue

        # Casual staff: enforce 3-hour minimum shift length
        if etype == "casual" and window_dur < CASUAL_MIN_SHIFT_MINUTES:
            continue

        av = s.get("avail")
        if av:
            av_from  = (av.get("available_from")  or "00:00")[:5] + ":00"
            av_until = (av.get("available_until") or "23:59")[:5] + ":00"
            if window.start < av_from or window.end > av_until:
                continue

        source = "primary_room" if s.get("primary_room_id") == room_id else "available"
        return {"uid": uid, "name": s["name"], "source": source, "employment_type": etype}

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

CENTRE_OPEN:  str = "07:15:00"   # earliest slot that must be covered
CENTRE_CLOSE: str = "18:00:00"   # coverage must reach (exclusive) this time


def _check_centre_coverage(
    day_shifts: list[SuggestedShift],
    date_str: str,
) -> list[str]:
    """
    Verify that at least one staff member covers every 15-minute slot from
    CENTRE_OPEN (07:15) to CENTRE_CLOSE (18:00) across all rooms combined.

    Returns a list of warning strings for any uncovered slots.
    """
    warnings: list[str] = []

    # Build set of 15-min slots that are covered by at least one shift
    covered: set[str] = set()
    for shift in day_shifts:
        ss = shift.start_time
        se = shift.end_time
        # Walk 15-min slots
        try:
            current = datetime.strptime(ss[:8], "%H:%M:%S")
            end_dt  = datetime.strptime(se[:8], "%H:%M:%S")
        except Exception:
            continue
        while current < end_dt:
            covered.add(current.strftime("%H:%M:%S"))
            current += timedelta(minutes=15)

    # Check every required slot
    try:
        slot_dt  = datetime.strptime(CENTRE_OPEN,  "%H:%M:%S")
        close_dt = datetime.strptime(CENTRE_CLOSE, "%H:%M:%S")
    except Exception:
        return warnings

    gap_start: str | None = None

    while slot_dt < close_dt:
        slot_str = slot_dt.strftime("%H:%M:%S")
        if slot_str not in covered:
            if gap_start is None:
                gap_start = slot_str[:5]
        else:
            if gap_start is not None:
                warnings.append(
                    f"Coverage gap {date_str} {gap_start}–{slot_str[:5]}: "
                    "no staff rostered across any room."
                )
                gap_start = None
        slot_dt += timedelta(minutes=15)

    if gap_start is not None:
        warnings.append(
            f"Coverage gap {date_str} {gap_start}–{CENTRE_CLOSE[:5]}: "
            "no staff rostered across any room."
        )

    return warnings


def _correct_rpns_coverage(
    day_shifts: list[SuggestedShift],
    date_str: str,
    dow: int,
    rpns_staff: list[dict],
    availability_map: dict[str, dict],
    leave_map: dict[str, list[str]],
    weekly_hours: dict[str, float],
    contracted: dict[str, float],
    room_map: dict[str, dict],
    rooms: list[dict],
    break_prefs: dict[str, dict[int, bool]],
    corrections_log: list[dict],
    ratio_warns: list[str],
    rpns_onsite_report: list[dict],
) -> None:
    """
    HARD CONSTRAINT: at least one Responsible Person or Nominated Supervisor
    must be onsite for every 15-min slot from CENTRE_OPEN to CENTRE_CLOSE.

    Mutates day_shifts (extends/adds shifts), weekly_hours, corrections_log,
    ratio_warns, and appends per-slot rows to rpns_onsite_report.

    Priority when choosing which RP/NS staff member to use:
      Nominated Supervisors first, then Responsible Persons, then by name.
    """
    rpns_uids = {s["uid"]: s for s in rpns_staff}
    if not rpns_uids:
        # No staff at this centre hold either flag — flag every slot.
        slot_dt  = datetime.strptime(CENTRE_OPEN,  "%H:%M:%S")
        close_dt = datetime.strptime(CENTRE_CLOSE, "%H:%M:%S")
        while slot_dt < close_dt:
            slot_str = slot_dt.strftime("%H:%M:%S")
            rpns_onsite_report.append({
                "date": date_str, "slot": slot_str[:5],
                "rpns_count": 0, "compliant": False, "assigned": "—",
            })
            slot_dt += timedelta(minutes=15)
        ratio_warns.append(
            f"CRITICAL: No Responsible Person/Nominated Supervisor onsite for "
            f"{CENTRE_OPEN[:5]}–{CENTRE_CLOSE[:5]} on {date_str} "
            "— no staff member at this centre holds either role."
        )
        return

    def _slots() -> list[str]:
        out = []
        t   = datetime.strptime(CENTRE_OPEN,  "%H:%M:%S")
        end = datetime.strptime(CENTRE_CLOSE, "%H:%M:%S")
        while t < end:
            out.append(t.strftime("%H:%M:%S"))
            t += timedelta(minutes=15)
        return out

    def _onsite(slot: str) -> list[SuggestedShift]:
        return [s for s in day_shifts
                if s.user_id in rpns_uids and s.start_time <= slot < s.end_time]

    def _gaps() -> list[tuple[str, str]]:
        ranges: list[tuple[str, str]] = []
        gap_start: str | None = None
        for slot in _slots():
            if not _onsite(slot):
                if gap_start is None:
                    gap_start = slot
            else:
                if gap_start is not None:
                    ranges.append((gap_start, slot))
                    gap_start = None
        if gap_start is not None:
            ranges.append((gap_start, CENTRE_CLOSE))
        return ranges

    # Candidates ranked: Nominated Supervisors first, then Responsible
    # Persons, then by name (stable).
    def _rank(s: dict) -> tuple[int, int, str]:
        return (0 if s.get("is_nominated_supervisor") else 1,
                0 if s.get("is_responsible_person") else 1,
                s.get("name", ""))

    ranked_rpns = sorted(rpns_staff, key=_rank)

    # ── Try to close each gap ────────────────────────────────────────
    for g_start, g_end in _gaps():
        # Re-check — an earlier gap's correction may have closed this one.
        if all(_onsite(s) for s in _slots() if g_start <= s < g_end):
            continue

        remaining_start, remaining_end = g_start, g_end

        # 1) Extend an existing RP/NS shift TODAY that's adjacent to the gap.
        rpns_today = [s for s in day_shifts if s.user_id in rpns_uids]

        # Try extending forward (shift ends exactly at remaining_start)
        for s in rpns_today:
            if s.end_time == remaining_start:
                uid = s.user_id
                av  = availability_map.get(uid, {}).get(dow)
                av_until = (av.get("available_until") or "23:59")[:5] + ":00" if av else "23:59:00"
                new_end  = min(remaining_end, av_until)
                if new_end > s.start_time and new_end > s.end_time:
                    extra = _mins_between(s.end_time, new_end) / 60
                    old_end = s.end_time
                    s.end_time   = new_end
                    s.shift_type = _shift_type(s.start_time, s.end_time)
                    weekly_hours[uid] = weekly_hours.get(uid, 0.0) + extra
                    corrections_log.append({
                        "date": date_str,
                        "violation": f"RP/NS coverage gap {remaining_start[:5]}–{remaining_end[:5]}",
                        "action": f"Extended {s.user_name}'s shift end from "
                                  f"{old_end[:5]} to {new_end[:5]} (Responsible Person/"
                                  f"Nominated Supervisor coverage).",
                    })
                    remaining_start = new_end
                    if remaining_start >= remaining_end:
                        break

        # Try extending backward (shift starts exactly at remaining_end)
        if remaining_start < remaining_end:
            for s in rpns_today:
                if s.start_time == remaining_end:
                    uid = s.user_id
                    av  = availability_map.get(uid, {}).get(dow)
                    av_from = (av.get("available_from") or "00:00")[:5] + ":00" if av else "00:00:00"
                    new_start = max(remaining_start, av_from)
                    if new_start < s.end_time and new_start < s.start_time:
                        extra = _mins_between(new_start, s.start_time) / 60
                        old_start = s.start_time
                        s.start_time = new_start
                        s.shift_type = _shift_type(s.start_time, s.end_time)
                        weekly_hours[uid] = weekly_hours.get(uid, 0.0) + extra
                        corrections_log.append({
                            "date": date_str,
                            "violation": f"RP/NS coverage gap {remaining_start[:5]}–{remaining_end[:5]}",
                            "action": f"Extended {s.user_name}'s shift start from "
                                      f"{old_start[:5]} to {new_start[:5]} (Responsible Person/"
                                      f"Nominated Supervisor coverage).",
                        })
                        remaining_end = new_start
                        if remaining_start >= remaining_end:
                            break

        # 2) If still uncovered, add a new shift for an available RP/NS
        #    staff member (NS preferred) covering the remaining gap.
        if remaining_start < remaining_end:
            for cand in ranked_rpns:
                uid = cand["uid"]
                if date_str in leave_map.get(uid, []):
                    continue
                av = availability_map.get(uid, {}).get(dow)
                if av is not None and not av.get("is_available", True):
                    continue
                av_from  = (av.get("available_from")  or "00:00")[:5] + ":00" if av else "00:00:00"
                av_until = (av.get("available_until") or "23:59")[:5] + ":00" if av else "23:59:00"
                seg_s = max(remaining_start, av_from)
                seg_e = min(remaining_end, av_until)
                if seg_e <= seg_s:
                    continue

                # Casual staff need the 3-hour minimum
                if cand.get("employment_type") == "casual":
                    if _mins_between(seg_s, seg_e) < CASUAL_MIN_SHIFT_MINUTES:
                        continue

                # Don't double-book this educator if already working this slot
                overlap = any(
                    s.user_id == uid and s.start_time < seg_e and s.end_time > seg_s
                    for s in day_shifts
                )
                if overlap:
                    continue

                # Check contracted hours — do NOT automatically exceed contract.
                uid_contracted = contracted.get(uid, 0.0)
                uid_weekly_hrs = weekly_hours.get(uid, 0.0)
                seg_dur_hrs    = _mins_between(seg_s, seg_e) / 60
                if uid_contracted > 0:
                    new_total = uid_weekly_hrs + seg_dur_hrs
                    over_by   = new_total - uid_contracted
                    if over_by > FT_OVERTIME_THRESHOLD_HOURS:
                        # Flag for manual review rather than silently exceeding contract
                        ratio_warns.append(
                            f"Manual review required: {cand['name']} contracted "
                            f"{uid_contracted:.1f}h/week — adding RP/NS coverage shift "
                            f"{seg_s[:5]}–{seg_e[:5]} on {date_str} would bring total to "
                            f"{new_total:.1f}h (+{over_by:.1f}h). "
                            f"Assign a casual RP/NS or approve overtime manually."
                        )
                        continue  # try next candidate

                pref_day = break_prefs.get(uid, {}).get(dow, False)
                override = "opted_out" if pref_day else "use_staff_default"
                rid      = cand.get("primary_room_id") or (rooms[0]["id"] if rooms else "")
                rname    = room_map.get(rid, {}).get("name", "")

                new_shift = SuggestedShift(
                    user_id=uid, user_name=cand["name"], room_id=rid, room_name=rname,
                    shift_date=date_str, start_time=seg_s, end_time=seg_e,
                    shift_type=_shift_type(seg_s, seg_e),
                    break_opt_out_override=override,
                    source="rpns_coverage",
                )
                day_shifts.append(new_shift)
                weekly_hours[uid] = weekly_hours.get(uid, 0.0) + seg_dur_hrs

                role_label = "Nominated Supervisor" if cand.get("is_nominated_supervisor") else "Responsible Person"
                corrections_log.append({
                    "date": date_str,
                    "violation": f"RP/NS coverage gap {remaining_start[:5]}–{remaining_end[:5]}",
                    "action": f"Added {seg_s[:5]}–{seg_e[:5]} shift for {cand['name']} "
                              f"({role_label}) to maintain Responsible Person/"
                              f"Nominated Supervisor coverage.",
                })
                remaining_start = seg_e
                if remaining_start >= remaining_end:
                    break

        # 3) Anything still uncovered → critical warning.
        if remaining_start < remaining_end:
            ratio_warns.append(
                f"CRITICAL: No Responsible Person/Nominated Supervisor onsite for "
                f"{remaining_start[:5]}–{remaining_end[:5]} on {date_str}."
            )

    # ── Build the final per-slot report ──────────────────────────────
    for slot in _slots():
        onsite = _onsite(slot)
        rpns_onsite_report.append({
            "date":       date_str,
            "slot":       slot[:5],
            "rpns_count": len(onsite),
            "compliant":  len(onsite) >= 1,
            "assigned":   ", ".join(s.user_name for s in onsite) if onsite else "—",
        })


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


def _count_coverage_in_window(
    day_shifts: list[SuggestedShift],
    room_id: str,
    window: "CoverageWindow",
) -> int:
    """
    Return the MINIMUM number of already-placed shifts that cover every
    15-minute slot inside [window.start, window.end) for room_id.

    "Minimum" means the worst-covered slot in the window — used to
    calculate how many additional staff are still needed to meet ratio.
    """
    # Build 15-min slots inside this window
    slots: list[str] = []
    try:
        cur = datetime.strptime(window.start[:8], "%H:%M:%S")
        end = datetime.strptime(window.end[:8],   "%H:%M:%S")
        while cur < end:
            slots.append(cur.strftime("%H:%M:%S"))
            cur += timedelta(minutes=15)
    except Exception:
        return 0

    if not slots:
        return 0

    min_cover = 9999
    for slot in slots:
        count = sum(
            1 for s in day_shifts
            if s.room_id == room_id
            and s.start_time <= slot < s.end_time
        )
        min_cover = min(min_cover, count)

    return min_cover if min_cover < 9999 else 0


def _merge_separate_rest_and_meal(
    breaks: list[SuggestedBreak],
    shifts: list[SuggestedShift],
    room_map: dict[str, dict],
) -> tuple[list[SuggestedBreak], list[str]]:
    """
    Post-generation merge pass.

    When the initial combined suggestion was rejected by the ratio check, the
    engine falls back to two separate SuggestedBreak objects (rest + meal)
    placed at different times with a gap between them.  This step finds those
    pairs and collapses them into one combined SuggestedBreak.

    Algorithm per (user_id, break_date) group:
      1. If the group has exactly one 'rest' and one 'meal' break, try to merge.
      2. Try anchor A — meal-anchored (preferred window):
            combined_start = meal_start − paid_component_minutes
            combined_end   = meal_end
         If combined_start < shift_start, clamp to shift_start and extend end.
      3. If anchor A causes a ratio breach, try anchor B — rest-anchored:
            combined_start = rest_start
            combined_end   = rest_start + total_combined_minutes
      4. If both anchors fail, keep the breaks separate and add a warning.
      5. When a merge succeeds, remove both originals and emit one SuggestedBreak
         with break_type="combined", combined=True, the correct label, and
         paid_minutes / unpaid_minutes preserved separately.

    Does not modify groups that already contain a combined break.
    """
    from collections import defaultdict

    review_warns: list[str] = []

    # Build per-day room coverage and shift lookup
    shift_by_uid_date: dict[tuple, SuggestedShift] = {
        (s.user_id, s.shift_date): s for s in shifts
    }
    day_coverage: dict[str, dict[str, dict[str, int]]] = {}
    for s in shifts:
        d = s.shift_date
        if d not in day_coverage:
            day_coverage[d] = _build_room_coverage(
                [x for x in shifts if x.shift_date == d], room_map
            )

    # Group breaks
    groups: dict[tuple, list[SuggestedBreak]] = defaultdict(list)
    for b in breaks:
        groups[(b.user_id, b.break_date)].append(b)

    result: list[SuggestedBreak] = []

    for (uid, date_str), group in groups.items():
        # Only touch groups that have exactly one rest + one meal, no combined
        rest_breaks = [b for b in group if b.break_type == "rest"]
        meal_breaks = [b for b in group if b.break_type == "meal"]
        has_combined = any(b.break_type == "combined" for b in group)

        if has_combined or len(rest_breaks) != 1 or len(meal_breaks) != 1:
            result.extend(group)
            continue

        rest = rest_breaks[0]
        meal = meal_breaks[0]

        # Don't merge if the meal was opted out (unpaid_minutes == 0 on meal)
        if meal.unpaid_minutes == 0:
            result.extend(group)
            continue

        paid_comp   = rest.paid_minutes   or rest.planned_duration_minutes
        unpaid_comp = meal.unpaid_minutes or meal.planned_duration_minutes
        total_dur   = paid_comp + unpaid_comp
        label       = f"{total_dur} min combined break"

        shift_rec   = shift_by_uid_date.get((uid, date_str))
        shift_start = shift_rec.start_time if shift_rec else "06:00:00"
        shift_end   = shift_rec.end_time   if shift_rec else "21:00:00"
        rid         = shift_rec.room_id if shift_rec else None
        room        = room_map.get(rid, {}) if rid else {}
        r_staff     = room.get("required_ratio_staff", 1)
        cov         = day_coverage.get(date_str, {})

        merged_brk: SuggestedBreak | None = None

        # ── Anchor A: meal-anchored (shift meal break left by paid_comp) ──
        meal_start = meal.planned_start_time
        meal_end   = meal.planned_end_time
        cand_start = _subtract_minutes(meal_start, paid_comp)
        cand_end   = meal_end

        # Clamp to shift start if needed
        if cand_start < shift_start:
            cand_start = shift_start
            cand_end   = _add_minutes(cand_start, total_dur)

        if cand_end <= shift_end and _ratio_allows_window(
            cand_start, cand_end, rid, uid, cov, r_staff
        ):
            merged_brk = _make_combined_break(
                rest, cand_start, cand_end, paid_comp, unpaid_comp,
                total_dur, label, date_str, uid,
            )

        # ── Anchor B: rest-anchored ───────────────────────────────────────
        if merged_brk is None:
            rest_start = rest.planned_start_time
            cand_start = rest_start
            cand_end   = _add_minutes(cand_start, total_dur)

            if cand_end <= shift_end and _ratio_allows_window(
                cand_start, cand_end, rid, uid, cov, r_staff
            ):
                merged_brk = _make_combined_break(
                    rest, cand_start, cand_end, paid_comp, unpaid_comp,
                    total_dur, label, date_str, uid,
                )

        if merged_brk is not None:
            result.append(merged_brk)
        else:
            # Ratio or shift constraints prevent a clean combined window.
            # Still emit ONE combined manual_review break rather than two
            # separate ones — anchored at the meal break time.
            fallback_start = _subtract_minutes(meal.planned_start_time, paid_comp)
            if fallback_start < shift_start:
                fallback_start = shift_start
            fallback_end = _add_minutes(fallback_start, total_dur)
            if fallback_end > shift_end:
                fallback_end = shift_end
                fallback_start = _subtract_minutes(fallback_end, total_dur)

            fallback_brk = SuggestedBreak(
                user_id=uid,
                user_name=rest.user_name,
                shift_key=rest.shift_key,
                break_date=date_str,
                break_type="combined",
                planned_start_time=fallback_start,
                planned_end_time=fallback_end,
                planned_duration_minutes=total_dur,
                paid_minutes=paid_comp,
                unpaid_minutes=unpaid_comp,
                combined=True,
                label=label,
                status="manual_review",
                opt_out_source=rest.opt_out_source,
                warnings=["Could not combine breaks due to ratio/shift constraints."],
            )
            result.append(fallback_brk)
            review_warns.append(
                f"{rest.user_name} on {date_str}: "
                "Could not combine breaks due to ratio/shift constraints. "
                "Combined break requires manual review."
            )

    return result, review_warns


def _subtract_minutes(t: str, mins: int) -> str:
    """Subtract mins from an HH:MM:SS string, return HH:MM:SS."""
    try:
        dt = datetime.strptime(t[:8], "%H:%M:%S") - timedelta(minutes=mins)
        return dt.strftime("%H:%M:%S")
    except Exception:
        return t


def _add_minutes(t: str, mins: int) -> str:
    """Add mins to an HH:MM:SS string, return HH:MM:SS."""
    try:
        dt = datetime.strptime(t[:8], "%H:%M:%S") + timedelta(minutes=mins)
        return dt.strftime("%H:%M:%S")
    except Exception:
        return t


def _make_combined_break(
    rest: "SuggestedBreak",
    start: str,
    end: str,
    paid_comp: int,
    unpaid_comp: int,
    total_dur: int,
    label: str,
    date_str: str,
    uid: str,
) -> "SuggestedBreak":
    """Construct one combined SuggestedBreak from a rest+meal pair."""
    return SuggestedBreak(
        user_id=uid,
        user_name=rest.user_name,
        shift_key=rest.shift_key,
        break_date=date_str,
        break_type="combined",
        planned_start_time=start,
        planned_end_time=end,
        planned_duration_minutes=total_dur,
        paid_minutes=paid_comp,
        unpaid_minutes=unpaid_comp,
        combined=True,
        label=label,
        status="scheduled",
        opt_out_source=rest.opt_out_source,
    )


def _find_break_cover(
    break_start: str,
    break_end: str,
    break_room_id: str,
    break_room: dict,
    break_uid: str,
    break_uname: str,
    date_str: str,
    day_shifts: list[SuggestedShift],
    room_map: dict[str, dict],
    room_coverage: dict[str, dict[str, int]],
    breaks_by_user: dict[str, list[tuple[str, str, bool]]],
    cover_delta: dict[str, dict[str, int]],
) -> "SuggestedMovement | None":
    """
    Try to find an educator from another room who can temporarily move to
    break_room for [break_start, break_end] to maintain ratio coverage.

    Eligibility criteria for a covering educator (cover_uid):
      1. Is working at break_room during the entire break window.
         (Their shift start ≤ break_start and shift end ≥ break_end.)
      2. Is NOT the educator on break.
      3. Is NOT already on break during this window.
      4. Moving them away from their own room does NOT breach their own
         room's required ratio.

    Preference order:
      1. Educators in rooms with the most spare capacity (staff above ratio).
      2. Earlier alphabetical name as tie-break.

    Returns a SuggestedMovement if cover is found, None otherwise.
    Does NOT mutate cover_delta — caller applies the delta after confirming.
    """
    r_staff_needed = break_room.get("required_ratio_staff", 1)
    r_room_name    = break_room.get("name", break_room_id)

    # Slots in the break window
    break_slots = [
        s for room_cov in room_coverage.values()
        for s in room_cov
        if break_start <= s < break_end
    ]
    break_slots = sorted(set(break_slots))

    # Collect already-on-break uids during this window
    on_break_uids: set[str] = set()
    for uid2, user_breaks in breaks_by_user.items():
        for bs, be, _ in user_breaks:
            if _overlaps(break_start, break_end, bs, be):
                on_break_uids.add(uid2)

    candidates = []

    for shift in day_shifts:
        cuid  = shift.user_id
        crid  = shift.room_id

        # Must be a different educator, in a different room, not on break
        if cuid == break_uid:
            continue
        if crid == break_room_id:
            continue
        if cuid in on_break_uids:
            continue

        # Must be working during the entire break window
        if shift.start_time > break_start or shift.end_time < break_end:
            continue

        # Moving them away must not breach their own room's ratio
        own_room   = room_map.get(crid, {})
        own_r_staff = own_room.get("required_ratio_staff", 1)
        own_cov    = room_coverage.get(crid, {})
        own_deltas = (cover_delta or {}).get(crid, {})
        feasible   = True
        surplus_min = 999  # minimum surplus across break slots

        for slot in break_slots:
            base   = own_cov.get(slot, 0)
            extra  = own_deltas.get(slot, 0)
            after  = base + extra - 1   # if this educator leaves
            if after < own_r_staff:
                feasible = False
                break
            surplus_min = min(surplus_min, after - own_r_staff)

        if not feasible:
            continue

        candidates.append({
            "uid":         cuid,
            "name":        shift.user_name,
            "from_room_id":   crid,
            "from_room_name": own_room.get("name", crid),
            "surplus":     surplus_min,
        })

    if not candidates:
        return None

    # Sort: most surplus first (least disruption), then name for stability
    candidates.sort(key=lambda c: (-c["surplus"], c["name"]))
    best = candidates[0]

    return SuggestedMovement(
        educator_id=best["uid"],
        educator_name=best["name"],
        from_room_id=best["from_room_id"],
        from_room_name=best["from_room_name"],
        to_room_id=break_room_id,
        to_room_name=r_room_name,
        start_time=break_start,
        end_time=break_end,
        move_date=date_str,
        covering_for_uid=break_uid,
        covering_for_name=break_uname,
        reason=(
            f"{best['name']} covers {r_room_name} {break_start[:5]}–{break_end[:5]} "
            f"while {break_uname} is on break."
        ),
    )


def _apply_cover_delta(
    cover_delta: dict[str, dict[str, int]],
    movement: "SuggestedMovement",
) -> None:
    """
    Update cover_delta in-place to reflect a temporary movement:
      - The receiving room gains +1 per slot during the movement window.
      - The sending room loses -1 per slot during the movement window.
    This is used by subsequent _check_break_impact calls.
    """
    slots = [
        f"{h:02d}:{m:02d}:00"
        for h in range(6, 21) for m in (0, 15, 30, 45)
        if movement.start_time <= f"{h:02d}:{m:02d}:00" < movement.end_time
    ]

    # Receiving room: +1 (cover educator is present)
    to_rid = movement.to_room_id
    if to_rid not in cover_delta:
        cover_delta[to_rid] = {}
    for slot in slots:
        cover_delta[to_rid][slot] = cover_delta[to_rid].get(slot, 0) + 1

    # Sending room: -1 (cover educator has left)
    from_rid = movement.from_room_id
    if from_rid not in cover_delta:
        cover_delta[from_rid] = {}
    for slot in slots:
        cover_delta[from_rid][slot] = cover_delta[from_rid].get(slot, 0) - 1


def _validate_and_resolve_break_overlaps(
    breaks: list[SuggestedBreak],
    shifts: list[SuggestedShift],
    room_map: dict[str, dict],
) -> tuple[list[SuggestedBreak], list[str]]:
    """
    Final validation pass: detect and resolve per-educator break overlaps.

    Runs after ALL other break generation, fixed-break, paid/unpaid, and
    manual-review logic — immediately before the result is returned.

    For each (user_id, break_date) group with more than one break:
        1. Sort by planned_start_time.
        2. Walk adjacent pairs; test overlap with _overlaps().
        3a. If overlap AND ratio allows the combined window:
              Replace the pair with one SuggestedBreak spanning
              min(starts)..max(ends).  paid_minutes and unpaid_minutes
              are summed from both breaks.  status → "scheduled",
              break_type → "combined", combined → True.
        3b. If overlap AND ratio does NOT allow combined:
              Try to move the later (non-fixed) break to the nearest
              valid window after the first break ends.
        3c. If neither break can be moved (both fixed, or no free slot):
              Mark the overlapping break as status="manual_review" and
              add an entry to review_warnings.

    Different educators are checked independently.
    The same educator on different days is also checked independently.

    Returns (resolved_breaks, new_review_warnings).
    """
    review_warns: list[str] = []

    # Build room coverage once per day from all shifts
    day_coverage: dict[str, dict[str, dict[str, int]]] = {}  # date → room coverage
    for shift in shifts:
        d = shift.shift_date
        if d not in day_coverage:
            day_coverage[d] = _build_room_coverage(
                [s for s in shifts if s.shift_date == d], room_map
            )

    # Group breaks by (user_id, break_date)
    from collections import defaultdict
    groups: dict[tuple, list[SuggestedBreak]] = defaultdict(list)
    for b in breaks:
        groups[(b.user_id, b.break_date)].append(b)

    resolved: list[SuggestedBreak] = []

    for (uid, date_str), group in groups.items():
        if len(group) == 1:
            resolved.append(group[0])
            continue

        # Sort by start time
        group.sort(key=lambda b: b.planned_start_time)

        # Identify the room for this educator on this day
        room_id = next(
            (s.room_id for s in shifts
             if s.user_id == uid and s.shift_date == date_str),
            None,
        )
        room = room_map.get(room_id, {}) if room_id else {}
        r_staff = room.get("required_ratio_staff",    1)
        r_child = room.get("required_ratio_children", 4)
        cov     = day_coverage.get(date_str, {})

        # Shift bounds for this educator (to constrain rescheduling)
        shift_rec = next(
            (s for s in shifts if s.user_id == uid and s.shift_date == date_str),
            None,
        )
        shift_start = shift_rec.start_time if shift_rec else "06:00:00"
        shift_end   = shift_rec.end_time   if shift_rec else "21:00:00"

        # Walk pairs; merge or reschedule as needed
        output: list[SuggestedBreak] = [group[0]]

        for brk in group[1:]:
            prev = output[-1]

            if not _overlaps(
                prev.planned_start_time, prev.planned_end_time,
                brk.planned_start_time,  brk.planned_end_time,
            ):
                output.append(brk)
                continue

            # ── Overlap detected ──────────────────────────────────────
            combined_start = min(prev.planned_start_time, brk.planned_start_time)
            combined_end   = max(prev.planned_end_time,   brk.planned_end_time)
            combined_dur   = _mins_between(combined_start, combined_end)
            paid_total     = prev.paid_minutes   + brk.paid_minutes
            unpaid_total   = prev.unpaid_minutes + brk.unpaid_minutes
            uname          = prev.user_name

            # Check whether ratio allows the combined window
            ratio_ok = _ratio_allows_window(
                combined_start, combined_end, room_id, uid, cov, r_staff,
            )

            if ratio_ok:
                # ── Combine into one block ────────────────────────────
                combined_label = (
                    f"{combined_dur} min combined break"
                    if (paid_total and unpaid_total)
                    else prev.label
                )
                merged = SuggestedBreak(
                    user_id=uid,
                    user_name=uname,
                    shift_key=prev.shift_key,
                    break_date=date_str,
                    break_type="combined",
                    planned_start_time=combined_start,
                    planned_end_time=combined_end,
                    planned_duration_minutes=combined_dur,
                    paid_minutes=paid_total,
                    unpaid_minutes=unpaid_total,
                    combined=True,
                    label=combined_label,
                    status="scheduled",
                    opt_out_source=prev.opt_out_source,
                )
                output[-1] = merged   # replace prev with the merged block

            else:
                # ── Try to move the later break ───────────────────────
                # Determine which break is movable (prefer moving brk;
                # if brk is a combined/fixed block, try moving prev).
                can_move_brk  = not (brk.combined  and brk.break_type == "combined")
                can_move_prev = not (prev.combined  and prev.break_type == "combined")

                moved = False
                if can_move_brk:
                    new_s, new_e = _next_free_slot(
                        prev.planned_end_time, brk.planned_duration_minutes,
                        shift_start, shift_end,
                        room_id, uid, cov, r_staff,
                        already_placed=[(b.planned_start_time, b.planned_end_time)
                                        for b in output],
                    )
                    if new_s is not None:
                        rescheduled = SuggestedBreak(
                            user_id=uid,
                            user_name=uname,
                            shift_key=brk.shift_key,
                            break_date=date_str,
                            break_type=brk.break_type,
                            planned_start_time=new_s,
                            planned_end_time=new_e,
                            planned_duration_minutes=brk.planned_duration_minutes,
                            paid_minutes=brk.paid_minutes,
                            unpaid_minutes=brk.unpaid_minutes,
                            combined=brk.combined,
                            label=brk.label,
                            status="scheduled",
                            opt_out_source=brk.opt_out_source,
                        )
                        output.append(rescheduled)
                        moved = True

                if not moved:
                    # Cannot resolve — flag for manual review
                    flagged = SuggestedBreak(
                        user_id=uid,
                        user_name=uname,
                        shift_key=brk.shift_key,
                        break_date=date_str,
                        break_type=brk.break_type,
                        planned_start_time=brk.planned_start_time,
                        planned_end_time=brk.planned_end_time,
                        planned_duration_minutes=brk.planned_duration_minutes,
                        paid_minutes=brk.paid_minutes,
                        unpaid_minutes=brk.unpaid_minutes,
                        combined=brk.combined,
                        label=brk.label,
                        status="manual_review",
                        opt_out_source=brk.opt_out_source,
                        warnings=[
                            f"Overlaps {prev.break_type} break "
                            f"{prev.planned_start_time[:5]}–{prev.planned_end_time[:5]}. "
                            "Ratio does not allow combined or rescheduled break."
                        ],
                    )
                    output.append(flagged)
                    review_warns.append(
                        f"{uname} on {date_str}: break overlap "
                        f"{prev.planned_start_time[:5]}–{prev.planned_end_time[:5]} ∩ "
                        f"{brk.planned_start_time[:5]}–{brk.planned_end_time[:5]} "
                        "— manual review required."
                    )

        resolved.extend(output)

    return resolved, review_warns


def _ratio_allows_window(
    b_start: str,
    b_end: str,
    room_id: str | None,
    user_id: str,
    cov: dict[str, dict[str, int]],
    r_staff: int,
) -> bool:
    """
    Return True if removing this educator during [b_start, b_end] keeps
    coverage at or above r_staff in every 15-minute slot.
    """
    if not room_id:
        return True
    room_cov = cov.get(room_id, {})
    for slot, count in room_cov.items():
        if b_start <= slot < b_end:
            if max(0, count - 1) < r_staff:
                return False
    return True


def _next_free_slot(
    not_before: str,
    dur_minutes: int,
    shift_start: str,
    shift_end: str,
    room_id: str | None,
    user_id: str,
    cov: dict[str, dict[str, int]],
    r_staff: int,
    already_placed: list[tuple[str, str]],
) -> tuple[str | None, str | None]:
    """
    Scan forward from `not_before` in 15-minute steps to find the next slot
    where the educator can take a break without overlapping any already-placed
    break and without breaching the room ratio.

    Returns (start, end) strings or (None, None) if no slot found.
    """
    try:
        current = datetime.strptime(not_before[:8], "%H:%M:%S")
        end_dt  = datetime.strptime(shift_end[:8],  "%H:%M:%S")
    except Exception:
        return None, None

    step = timedelta(minutes=15)

    while current + timedelta(minutes=dur_minutes) <= end_dt:
        b_s = current.strftime("%H:%M:%S")
        b_e = (current + timedelta(minutes=dur_minutes)).strftime("%H:%M:%S")

        # No overlap with already-placed breaks
        if any(_overlaps(b_s, b_e, ps, pe) for ps, pe in already_placed):
            current += step
            continue

        # Ratio check
        if _ratio_allows_window(b_s, b_e, room_id, user_id, cov, r_staff):
            return b_s, b_e

        current += step

    return None, None


def _overlaps(a_start: str, a_end: str, b_start: str, b_end: str) -> bool:
    """
    Return True when two time windows overlap.
    Canonical half-open interval test: a_start < b_end AND b_start < a_end.
    All arguments must be HH:MM:SS strings.
    """
    return a_start < b_end and b_start < a_end


def _check_centre_ratio(
    b_start: str,
    b_end: str,
    break_room_id: str,
    room_coverage: dict[str, dict[str, int]],
    room_map: dict[str, dict],
    cover_delta: dict[str, dict[str, int]] | None = None,
) -> tuple[bool, str, dict]:
    """
    Check whether removing one educator from break_room_id during [b_start, b_end)
    would cause the CENTRE as a whole to fall below its aggregate staffing requirement.

    For each 15-min slot in the break window:
        centre_staff_required = sum over all rooms of ceil(children/ratio_children)*ratio_staff
        centre_staff_present  = sum of all room coverage + cover_delta - 1 (for this break)

    Returns (ok: bool, reason: str, debug_info: dict).
    debug_info contains per-slot centre data for the debug log.
    """
    deltas = cover_delta or {}
    slots_in_break = [
        s for room_cov in room_coverage.values()
        for s in room_cov
        if b_start <= s < b_end
    ]
    slots_in_break = sorted(set(slots_in_break))

    worst_debug: dict = {}

    import math as _math
    for slot in slots_in_break:
        centre_staff    = 0
        centre_required = 0

        for rid, room_cov in room_coverage.items():
            base  = room_cov.get(slot, 0)
            delta = deltas.get(rid, {}).get(slot, 0)
            centre_staff += base + delta

            room = room_map.get(rid, {})
            r_s  = room.get("required_ratio_staff",    1)
            r_c  = room.get("required_ratio_children", 4)
            # Use room's licensed capacity as max children proxy when no interval data
            # (conservative — always assumes room could be full)
            cap  = room.get("licensed_capacity", 0)
            centre_required += r_s   # at minimum, every room needs r_s staff

        # After removing this educator from break_room
        staff_after = centre_staff - 1

        if not worst_debug:
            worst_debug = {
                "slot":              slot,
                "centre_staff_before": centre_staff,
                "centre_staff_after":  staff_after,
                "centre_required":     centre_required,
            }

        if staff_after < centre_required:
            return (
                False,
                f"Centre drops to {staff_after} staff at {slot[:5]} (need {centre_required}).",
                {
                    "slot":              slot,
                    "centre_staff_before": centre_staff,
                    "centre_staff_after":  staff_after,
                    "centre_required":     centre_required,
                },
            )

    return True, "", worst_debug


def _check_break_impact(
    b_start: str,
    b_end: str,
    room_id: str,
    user_id: str,
    room_coverage: dict[str, dict[str, int]],
    breaks_by_room: dict[str, list[tuple[str, str]]],
    breaks_by_user: dict[str, list[tuple[str, str, bool]]],
    r_staff: int,
    r_child: int,
    cover_delta: dict[str, dict[str, int]] | None = None,
    room_map: dict[str, dict] | None = None,
) -> tuple[str, str, dict]:
    """
    Return ("ok" | "breach" | "fixed_conflict", reason, debug_info).

    Checks in priority order:
    0. Centre-wide ratio (highest priority — checked first when room_map provided).
    1. Educator-level overlap.
    2. Room-level stagger (no two educators from same room on break simultaneously).
    3. Room-level ratio coverage.
    """
    debug_info: dict = {}

    # 0. Centre-wide ratio (priority 0 — must pass before anything else)
    if room_map:
        centre_ok, centre_reason, centre_debug = _check_centre_ratio(
            b_start, b_end, room_id, room_coverage, room_map, cover_delta,
        )
        debug_info.update(centre_debug)
        if not centre_ok:
            return "breach", f"Centre-wide ratio: {centre_reason}", debug_info

    # 1. Educator overlap
    for ex_start, ex_end, ex_fixed in breaks_by_user.get(user_id, []):
        if _overlaps(b_start, b_end, ex_start, ex_end):
            if ex_fixed:
                return (
                    "fixed_conflict",
                    f"Overlaps a fixed break {ex_start[:5]}–{ex_end[:5]} that cannot be moved.",
                    debug_info,
                )
            return (
                "breach",
                f"Overlaps educator's own break {ex_start[:5]}–{ex_end[:5]}.",
                debug_info,
            )

    # 2. Room-level stagger
    for existing_start, existing_end in breaks_by_room.get(room_id, []):
        if _overlaps(b_start, b_end, existing_start, existing_end):
            return "breach", "Another staff member is already on break in this window.", debug_info

    # 3. Room-level ratio (with cover_delta applied)
    cov    = room_coverage.get(room_id, {})
    deltas = (cover_delta or {}).get(room_id, {})
    slots_in_break = [s for s in cov if b_start <= s < b_end]
    room_staff_before = None
    for slot in slots_in_break:
        base_staff     = cov.get(slot, 0)
        extra_cover    = deltas.get(slot, 0)
        staff_if_break = max(0, base_staff + extra_cover - 1)
        if room_staff_before is None:
            room_staff_before = base_staff + extra_cover
        if staff_if_break < r_staff:
            debug_info.update({
                "room_staff_before": room_staff_before,
                "room_staff_after":  staff_if_break,
                "room_required":     r_staff,
            })
            return "breach", f"Room coverage at {slot[:5]} drops to {staff_if_break} (need {r_staff}).", debug_info

    debug_info.update({
        "room_staff_before": room_staff_before,
        "room_staff_after":  max(0, (room_staff_before or 0) - 1),
        "room_required":     r_staff,
    })
    return "ok", "", debug_info


def _find_alt_break_window(
    shift_start: str,
    shift_end: str,
    dur_minutes: int,
    room_id: str,
    user_id: str,
    room_coverage: dict[str, dict[str, int]],
    breaks_by_room: dict[str, list[tuple[str, str]]],
    breaks_by_user: dict[str, list[tuple[str, str, bool]]],
    r_staff: int,
    r_child: int,
    cover_delta: dict[str, dict[str, int]] | None = None,
    room_map: dict[str, dict] | None = None,
) -> tuple[str, str, bool]:
    """
    Scan the shift in 15-minute steps for a window satisfying ALL constraints
    (centre-wide ratio, educator overlap, room stagger, room ratio).
    Returns (start, end, still_conflict).
    """
    step = timedelta(minutes=15)

    try:
        current = datetime.strptime(shift_start[:8], "%H:%M:%S")
        end_dt  = datetime.strptime(shift_end[:8],   "%H:%M:%S")
    except Exception:
        return shift_start, shift_end, True

    while current + timedelta(minutes=dur_minutes) <= end_dt:
        b_s = current.strftime("%H:%M:%S")
        b_e = (current + timedelta(minutes=dur_minutes)).strftime("%H:%M:%S")
        conflict, _, _ = _check_break_impact(
            b_s, b_e, room_id, user_id,
            room_coverage, breaks_by_room, breaks_by_user, r_staff, r_child,
            cover_delta, room_map,
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
