# utils/roster_engine.py
# ─────────────────────────────────────────────────────────────────────────────
# Childcare Rostering Engine
# Pure-Python — no database calls. All inputs from caller.
#
# Core concept: the day is divided into 15-minute SLOTS.
#   Slot 0  = 06:00, Slot 1 = 06:15, ..., Slot 55 = 19:45  (56 slots total)
#
# Every shift occupies a range of slots [start_slot, end_slot).
# Every break within a shift vacates a range of slots from ratio counts.
# Validation runs slot-by-slot to find every gap, not just shift-level issues.
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations
import math
from datetime import datetime, date, time, timedelta
from typing import NamedTuple


# ── Constants ─────────────────────────────────────────────────────────────────

DAY_START_HOUR = 6      # 6:00 AM
DAY_END_HOUR   = 20     # 8:00 PM  (exclusive — last slot is 19:45)
SLOT_MINUTES   = 15
SLOTS_PER_HOUR = 60 // SLOT_MINUTES               # 4
TOTAL_SLOTS    = (DAY_END_HOUR - DAY_START_HOUR) * SLOTS_PER_HOUR  # 56

SHIFT_TYPES    = ["standard", "opening", "closing", "split", "on_call", "overtime"]
CONFLICT_TYPES = ["ratio_breach", "qual_missing", "leave_clash",
                  "availability_conflict", "double_booking", "diploma_required"]


# ── Slot utilities ────────────────────────────────────────────────────────────

def time_to_slot(t: str | time) -> int:
    """
    Convert a time (HH:MM or HH:MM:SS string, or time object) to a slot index.
    Slot 0 = 06:00, Slot 4 = 07:00, etc.
    Returns -1 if outside the day window.
    """
    if isinstance(t, str):
        try:
            parts = t.split(":")
            h, m  = int(parts[0]), int(parts[1])
        except Exception:
            return -1
    elif isinstance(t, time):
        h, m = t.hour, t.minute
    else:
        return -1

    slot = (h - DAY_START_HOUR) * SLOTS_PER_HOUR + m // SLOT_MINUTES
    return max(0, min(slot, TOTAL_SLOTS))


def slot_to_time(slot: int) -> str:
    """Convert a slot index back to 'HH:MM' string."""
    total_min = DAY_START_HOUR * 60 + slot * SLOT_MINUTES
    h = total_min // 60
    m = total_min % 60
    return f"{h:02d}:{m:02d}"


def slots_for_shift(start_str: str, end_str: str) -> range:
    """Return the range of slot indices occupied by a shift."""
    s = time_to_slot(start_str)
    e = time_to_slot(end_str)
    return range(max(0, s), min(e, TOTAL_SLOTS))


def slot_label(slot: int) -> str:
    """Human-readable label for a slot: '6:00', '14:15', etc."""
    t = slot_to_time(slot)
    h, m = int(t[:2]), int(t[3:5])
    suffix = "am" if h < 12 else "pm"
    h12    = h if h <= 12 else h - 12
    if h12 == 0:
        h12 = 12
    return f"{h12}:{m:02d}{suffix}" if m else f"{h12}{suffix}"


def generate_time_options(step_minutes: int = 15,
                           start_hour: int = 6,
                           end_hour: int = 20) -> list[str]:
    """Generate a list of time strings at step_minutes intervals."""
    times = []
    t = datetime(2000, 1, 1, start_hour, 0)
    end = datetime(2000, 1, 1, end_hour, 0)
    while t <= end:
        times.append(t.strftime("%H:%M"))
        t += timedelta(minutes=step_minutes)
    return times


# ── Core data structures ──────────────────────────────────────────────────────

class ShiftSlice(NamedTuple):
    """A shift with its slot range pre-computed."""
    shift_id:    str
    user_id:     str
    room_id:     str
    start_slot:  int
    end_slot:    int
    break_start: int   # slot index where break begins (-1 = no break)
    break_end:   int   # slot index where break ends   (-1 = no break)
    has_diploma: bool
    counts_ratio: bool


def build_shift_slices(shifts: list[dict]) -> list[ShiftSlice]:
    """
    Convert raw shift dicts (from DB) into ShiftSlice objects for fast slot math.
    Each shift dict must have: id, user_id, room_id, start_time, end_time,
    break_duration_minutes. Optionally: has_diploma, counts_ratio.
    """
    slices = []
    for s in shifts:
        start   = (s.get("start_time") or "")[:5]
        end     = (s.get("end_time")   or "")[:5]
        s_slot  = time_to_slot(start)
        e_slot  = time_to_slot(end)
        brk_min = s.get("break_duration_minutes", 0) or 0

        # Place break at midpoint of shift
        if brk_min > 0:
            mid_slot   = (s_slot + e_slot) // 2
            brk_slots  = math.ceil(brk_min / SLOT_MINUTES)
            brk_start  = mid_slot
            brk_end    = min(mid_slot + brk_slots, e_slot)
        else:
            brk_start = brk_end = -1

        slices.append(ShiftSlice(
            shift_id    = s.get("id", ""),
            user_id     = s.get("user_id", ""),
            room_id     = s.get("room_id", "") or "",
            start_slot  = s_slot,
            end_slot    = e_slot,
            break_start = brk_start,
            break_end   = brk_end,
            has_diploma = bool(s.get("has_diploma", False)),
            counts_ratio= bool(s.get("counts_ratio", True)),
        ))
    return slices


# ── Per-slot coverage matrix ──────────────────────────────────────────────────

def build_coverage_matrix(
    slices:      list[ShiftSlice],
    room_ids:    list[str],
) -> dict[str, list[int]]:
    """
    Build a per-room coverage matrix: room_id → list[int] of length TOTAL_SLOTS
    where each value = number of ratio-eligible staff in that room at that slot.
    Staff on break are excluded.

    Returns {room_id: [count_slot_0, count_slot_1, ...]}
    """
    matrix: dict[str, list[int]] = {rid: [0] * TOTAL_SLOTS for rid in room_ids}

    for sl in slices:
        rid = sl.room_id
        if rid not in matrix or not sl.counts_ratio:
            continue
        for slot in range(sl.start_slot, sl.end_slot):
            # Skip break slots
            if sl.break_start != -1 and sl.break_start <= slot < sl.break_end:
                continue
            matrix[rid][slot] += 1

    return matrix


def build_diploma_matrix(
    slices:   list[ShiftSlice],
    room_ids: list[str],
) -> dict[str, list[int]]:
    """
    Same as coverage_matrix but only counts staff with a diploma qualification.
    Used to check diploma-required rooms.
    """
    matrix: dict[str, list[int]] = {rid: [0] * TOTAL_SLOTS for rid in room_ids}

    for sl in slices:
        rid = sl.room_id
        if rid not in matrix or not sl.has_diploma:
            continue
        for slot in range(sl.start_slot, sl.end_slot):
            if sl.break_start != -1 and sl.break_start <= slot < sl.break_end:
                continue
            matrix[rid][slot] += 1

    return matrix


# ── Children coverage (from enrolment data) ───────────────────────────────────

def build_children_matrix(
    children:  list[dict],
    room_ids:  list[str],
    day_of_week: int,
) -> dict[str, list[int]]:
    """
    Build a per-room expected-children matrix for a given day of the week.
    Uses enrolment_days and usual_start/end times.

    day_of_week: 1=Mon ... 5=Fri (matches DB convention).
    """
    matrix: dict[str, list[int]] = {rid: [0] * TOTAL_SLOTS for rid in room_ids}

    for child in children:
        rid      = child.get("room_id", "")
        if rid not in matrix:
            continue
        enrol_days = child.get("enrolment_days") or [1, 2, 3, 4, 5]
        if day_of_week not in enrol_days:
            continue

        usual_start = (child.get("usual_start_time") or f"{DAY_START_HOUR:02d}:00")[:5]
        usual_end   = (child.get("usual_end_time")   or f"{DAY_END_HOUR:02d}:00")[:5]
        s_slot = time_to_slot(usual_start)
        e_slot = time_to_slot(usual_end)

        for slot in range(max(0, s_slot), min(e_slot, TOTAL_SLOTS)):
            matrix[rid][slot] += 1

    return matrix



# ── Children coverage (from interval attendance data) ─────────────────────────

def build_children_matrix_from_intervals(
    interval_counts: dict[str, list[int]],
    room_ids: list[str],
) -> dict[str, list[int]]:
    """
    Build a per-room children matrix directly from room_attendance_intervals data.

    interval_counts is the output of attendance_queries.intervals_to_slot_counts():
        {room_id: [count_per_slot]}  — 56 slots, 06:00–20:00.

    Rooms with no interval data get an all-zero row so downstream code is safe.
    This takes PRIORITY over build_children_matrix (enrolment-based) when data exists,
    because it reflects what was actually entered for that specific date.
    """
    matrix: dict[str, list[int]] = {}
    for rid in room_ids:
        if rid in interval_counts:
            # Copy so callers cannot mutate the source
            matrix[rid] = list(interval_counts[rid])
        else:
            matrix[rid] = [0] * TOTAL_SLOTS
    return matrix


# ── Validation ────────────────────────────────────────────────────────────────

class RosterConflict(NamedTuple):
    """A single validation finding."""
    conflict_type:  str     # from CONFLICT_TYPES
    severity:       str     # "error" | "warning"
    shift_id:       str
    user_id:        str
    room_id:        str
    shift_date:     str
    slot_start:     int     # first bad slot
    slot_end:       int     # last bad slot + 1
    message:        str
    suggestion:     str


def validate_roster(
    shifts:           list[dict],
    rooms:            list[dict],
    children:         list[dict],
    leave_map:        dict[str, list[str]],       # user_id → [date strings with approved leave]
    availability_map: dict[str, dict],            # user_id → {0..6: {from, until, available}}
    shift_date:       date,
    interval_counts:  dict[str, list[int]] | None = None,
) -> list[RosterConflict]:
    """
    Full validation of a roster day. Returns every conflict found.

    Parameters
    ----------
    shifts           : roster_shifts rows for this day (with has_diploma, counts_ratio added)
    rooms            : rooms config rows {id, required_ratio_staff, required_ratio_children,
                       licensed_capacity, requires_diploma, name}
    children         : enrolled children with room_id, enrolment_days, usual times
    leave_map        : approved leave per user on this date
    availability_map : availability windows per user per day-of-week
    shift_date       : the date being validated

    Returns
    -------
    List of RosterConflict objects, sorted by severity then slot.
    """
    conflicts: list[RosterConflict] = []
    dow       = shift_date.isoweekday()    # Mon=1, Sun=7; DB uses 0=Sun so adjust
    dow_db    = dow % 7                    # Mon=1→1, Sun=7→0

    room_map  = {r["id"]: r for r in rooms}
    room_ids  = list(room_map.keys())
    slices    = build_shift_slices(shifts)

    coverage  = build_coverage_matrix(slices, room_ids)
    diploma_c = build_diploma_matrix(slices, room_ids)
    if interval_counts is not None:
        children_m = build_children_matrix_from_intervals(interval_counts, room_ids)
    else:
        children_m = build_children_matrix(children, room_ids, dow)

    # ── 1. Ratio compliance per slot ──────────────────────────────────
    for room in rooms:
        rid        = room["id"]
        rname      = room.get("name", "Room")
        r_staff    = room.get("required_ratio_staff", 1)
        r_children = room.get("required_ratio_children", 4)
        capacity   = room.get("licensed_capacity", 0)
        req_diploma= room.get("requires_diploma", False)

        staff_arr   = coverage.get(rid, [0] * TOTAL_SLOTS)
        diploma_arr = diploma_c.get(rid, [0] * TOTAL_SLOTS)
        child_arr   = children_m.get(rid, [0] * TOTAL_SLOTS)

        # Find contiguous bad ranges (ratio breach)
        bad_start = None
        for slot in range(TOTAL_SLOTS):
            n_children = child_arr[slot]
            n_staff    = staff_arr[slot]
            if n_children == 0:
                if bad_start is not None:
                    _flush_ratio_conflict(conflicts, rid, rname, bad_start, slot,
                                          shift_date.isoformat(), r_staff, r_children)
                    bad_start = None
                continue

            min_staff = math.ceil(n_children / r_children) * r_staff
            if n_staff < min_staff:
                if bad_start is None:
                    bad_start = slot
            else:
                if bad_start is not None:
                    _flush_ratio_conflict(conflicts, rid, rname, bad_start, slot,
                                          shift_date.isoformat(), r_staff, r_children)
                    bad_start = None

        if bad_start is not None:
            _flush_ratio_conflict(conflicts, rid, rname, bad_start, TOTAL_SLOTS,
                                  shift_date.isoformat(), r_staff, r_children)

        # Diploma coverage check (only for rooms that require it)
        if req_diploma:
            for slot in range(TOTAL_SLOTS):
                if child_arr[slot] > 0 and diploma_arr[slot] == 0 and staff_arr[slot] > 0:
                    # Staff present but none with diploma
                    t_start = slot_to_time(slot)
                    t_end   = slot_to_time(min(slot + 1, TOTAL_SLOTS))
                    conflicts.append(RosterConflict(
                        conflict_type = "diploma_required",
                        severity      = "error",
                        shift_id      = "",
                        user_id       = "",
                        room_id       = rid,
                        shift_date    = shift_date.isoformat(),
                        slot_start    = slot,
                        slot_end      = slot + 1,
                        message       = (
                            f"{rname}: no diploma-qualified educator at {t_start}–{t_end}. "
                            f"{staff_arr[slot]} staff present but diploma required."
                        ),
                        suggestion    = (
                            f"Assign a Diploma or B.Ed (EC) qualified educator to "
                            f"{rname} during this window."
                        ),
                    ))
                    break  # one per room per day

    # ── 2. Per-shift conflict checks ──────────────────────────────────
    user_shift_slots: dict[str, list[tuple[int,int,str]]] = {}

    for s in shifts:
        uid       = s.get("user_id", "")
        sid       = s.get("id", "")
        rid       = s.get("room_id", "") or ""
        rname     = (room_map.get(rid) or {}).get("name", "unknown room")
        start_str = (s.get("start_time") or "")[:5]
        end_str   = (s.get("end_time")   or "")[:5]
        s_slot    = time_to_slot(start_str)
        e_slot    = time_to_slot(end_str)
        date_str  = shift_date.isoformat()

        # Track slots used per user for double-booking check
        user_shift_slots.setdefault(uid, []).append((s_slot, e_slot, sid))

        # 2a. Leave clash
        if uid in leave_map and date_str in leave_map[uid]:
            conflicts.append(RosterConflict(
                conflict_type = "leave_clash",
                severity      = "error",
                shift_id      = sid,
                user_id       = uid,
                room_id       = rid,
                shift_date    = date_str,
                slot_start    = s_slot,
                slot_end      = e_slot,
                message       = (
                    f"Staff has approved leave on {shift_date.strftime('%-d %b')} "
                    f"but is rostered {start_str}–{end_str} in {rname}."
                ),
                suggestion    = "Remove this shift or cancel the leave request.",
            ))

        # 2b. Availability conflict
        if uid in availability_map:
            av = availability_map[uid].get(dow_db, {})
            if not av.get("is_available", True):
                conflicts.append(RosterConflict(
                    conflict_type = "availability_conflict",
                    severity      = "warning",
                    shift_id      = sid,
                    user_id       = uid,
                    room_id       = rid,
                    shift_date    = date_str,
                    slot_start    = s_slot,
                    slot_end      = e_slot,
                    message       = (
                        f"Staff has marked themselves unavailable on "
                        f"{shift_date.strftime('%A')}s. Shift: {start_str}–{end_str} {rname}."
                    ),
                    suggestion    = "Confirm with staff before publishing, or update availability.",
                ))
            else:
                av_from  = av.get("available_from") or f"{DAY_START_HOUR:02d}:00"
                av_until = av.get("available_until") or f"{DAY_END_HOUR:02d}:00"
                av_f_slot = time_to_slot(str(av_from)[:5])
                av_u_slot = time_to_slot(str(av_until)[:5])
                if s_slot < av_f_slot or e_slot > av_u_slot:
                    conflicts.append(RosterConflict(
                        conflict_type = "availability_conflict",
                        severity      = "warning",
                        shift_id      = sid,
                        user_id       = uid,
                        room_id       = rid,
                        shift_date    = date_str,
                        slot_start    = s_slot,
                        slot_end      = e_slot,
                        message       = (
                            f"Shift {start_str}–{end_str} is outside stated availability "
                            f"({str(av_from)[:5]}–{str(av_until)[:5]})."
                        ),
                        suggestion    = "Confirm the shift time with the staff member.",
                    ))

    # 2c. Double-booking (same user, overlapping slots)
    for uid, windows in user_shift_slots.items():
        for i in range(len(windows)):
            for j in range(i + 1, len(windows)):
                a_s, a_e, a_id = windows[i]
                b_s, b_e, b_id = windows[j]
                if a_s < b_e and b_s < a_e:     # overlap
                    conflicts.append(RosterConflict(
                        conflict_type = "double_booking",
                        severity      = "error",
                        shift_id      = a_id,
                        user_id       = uid,
                        room_id       = "",
                        shift_date    = shift_date.isoformat(),
                        slot_start    = max(a_s, b_s),
                        slot_end      = min(a_e, b_e),
                        message       = (
                            f"Staff has overlapping shifts: "
                            f"{slot_to_time(a_s)}–{slot_to_time(a_e)} and "
                            f"{slot_to_time(b_s)}–{slot_to_time(b_e)}."
                        ),
                        suggestion    = "Remove or adjust one of the overlapping shifts.",
                    ))

    # Sort: errors first, then warnings; within each by slot
    conflicts.sort(key=lambda c: (0 if c.severity == "error" else 1, c.slot_start))
    return conflicts


def _flush_ratio_conflict(
    conflicts: list, rid: str, rname: str, slot_s: int, slot_e: int,
    date_str: str, r_staff: int, r_children: int,
):
    conflicts.append(RosterConflict(
        conflict_type = "ratio_breach",
        severity      = "error",
        shift_id      = "",
        user_id       = "",
        room_id       = rid,
        shift_date    = date_str,
        slot_start    = slot_s,
        slot_end      = slot_e,
        message       = (
            f"{rname}: insufficient staff {slot_to_time(slot_s)}–{slot_to_time(slot_e)} "
            f"(required 1:{r_children} ratio)."
        ),
        suggestion    = (
            f"Add a shift in {rname} covering "
            f"{slot_to_time(slot_s)}–{slot_to_time(slot_e)}."
        ),
    ))


# ── Opening / Closing shift detection ─────────────────────────────────────────

def classify_shift_type(start_str: str, end_str: str,
                         centre_open: str = "07:00",
                         centre_close: str = "18:00") -> str:
    """
    Classify a shift as opening, closing, standard, or split.
    Uses 15-minute slot granularity.
    """
    s_slot  = time_to_slot(start_str)
    e_slot  = time_to_slot(end_str)
    o_slot  = time_to_slot(centre_open)
    c_slot  = time_to_slot(centre_close)
    mid_slot = (o_slot + c_slot) // 2

    if s_slot <= o_slot and e_slot <= mid_slot:
        return "opening"
    if s_slot >= mid_slot and e_slot >= c_slot:
        return "closing"
    if s_slot <= o_slot and e_slot >= c_slot:
        return "split"
    return "standard"


# ── Roster compliance summary ─────────────────────────────────────────────────

def roster_compliance_summary(
    conflicts: list[RosterConflict],
    total_rooms: int,
    total_slots_with_children: int,
) -> dict:
    """
    Aggregate conflicts into a summary dict for the publish checklist.

    Returns:
        n_errors, n_warnings, ratio_breaches, leave_clashes,
        availability_conflicts, double_bookings, diploma_issues,
        is_publishable (no errors), compliance_pct
    """
    n_errors   = sum(1 for c in conflicts if c.severity == "error")
    n_warnings = sum(1 for c in conflicts if c.severity == "warning")

    by_type = {}
    for c in conflicts:
        by_type.setdefault(c.conflict_type, 0)
        by_type[c.conflict_type] += 1

    bad_slots = len({c.slot_start for c in conflicts if c.conflict_type == "ratio_breach"})
    pct       = (
        round((1 - bad_slots / total_slots_with_children) * 100)
        if total_slots_with_children > 0 else 100
    )

    return {
        "n_errors":               n_errors,
        "n_warnings":             n_warnings,
        "ratio_breaches":         by_type.get("ratio_breach", 0),
        "leave_clashes":          by_type.get("leave_clash", 0),
        "availability_conflicts": by_type.get("availability_conflict", 0),
        "double_bookings":        by_type.get("double_booking", 0),
        "diploma_issues":         by_type.get("diploma_required", 0),
        "is_publishable":         n_errors == 0,
        "compliance_pct":         pct,
    }


# ── Staffing gap finder ────────────────────────────────────────────────────────

def find_staffing_gaps(
    shifts:     list[dict],
    rooms:      list[dict],
    children:   list[dict],
    day_of_week: int,
    interval_counts: dict[str, list[int]] | None = None,
) -> list[dict]:
    """
    Find time windows where children are expected but staff coverage is insufficient.
    Returns list of gap dicts: {room_id, room_name, slot_start, slot_end,
    time_from, time_to, n_children, n_staff, shortfall}.

    interval_counts — optional slot-indexed child counts from room_attendance_intervals.
        When provided, takes priority over the enrolment-based children matrix.
    """
    room_ids  = [r["id"] for r in rooms]
    room_map  = {r["id"]: r for r in rooms}
    slices    = build_shift_slices(shifts)
    coverage  = build_coverage_matrix(slices, room_ids)
    if interval_counts is not None:
        children_m = build_children_matrix_from_intervals(interval_counts, room_ids)
    else:
        children_m = build_children_matrix(children, room_ids, day_of_week)

    gaps = []
    for room in rooms:
        rid     = room["id"]
        r_staff = room.get("required_ratio_staff", 1)
        r_child = room.get("required_ratio_children", 4)
        staff_a = coverage.get(rid, [0] * TOTAL_SLOTS)
        child_a = children_m.get(rid, [0] * TOTAL_SLOTS)

        gap_start = None
        for slot in range(TOTAL_SLOTS):
            nc = child_a[slot]
            ns = staff_a[slot]
            if nc == 0:
                if gap_start is not None:
                    gap_start = None
                continue
            min_s   = math.ceil(nc / r_child) * r_staff
            shortfall = min_s - ns
            if shortfall > 0:
                if gap_start is None:
                    gap_start = slot
                    gap_nc = nc
                    gap_ns = ns
                    gap_sf = shortfall
            else:
                if gap_start is not None:
                    gaps.append({
                        "room_id":   rid,
                        "room_name": room.get("name",""),
                        "room_colour": room.get("colour","#3498DB"),
                        "slot_start": gap_start,
                        "slot_end":   slot,
                        "time_from":  slot_to_time(gap_start),
                        "time_to":    slot_to_time(slot),
                        "n_children": gap_nc,
                        "n_staff":    gap_ns,
                        "shortfall":  gap_sf,
                    })
                    gap_start = None
        if gap_start is not None:
            gaps.append({
                "room_id":   rid,
                "room_name": room.get("name",""),
                "room_colour": room.get("colour","#3498DB"),
                "slot_start": gap_start,
                "slot_end":   TOTAL_SLOTS,
                "time_from":  slot_to_time(gap_start),
                "time_to":    slot_to_time(TOTAL_SLOTS),
                "n_children": child_a[gap_start],
                "n_staff":    staff_a[gap_start],
                "shortfall":  math.ceil(child_a[gap_start] / r_child) * r_staff - staff_a[gap_start],
            })

    return sorted(gaps, key=lambda g: g["slot_start"])


# ── Grid rendering data builder ────────────────────────────────────────────────

def build_grid_data(
    shifts:  list[dict],
    rooms:   list[dict],
    children: list[dict],
    day_of_week: int,
    interval_counts: dict[str, list[int]] | None = None,
) -> dict:
    """
    Build everything the roster grid UI needs for one day.

    interval_counts — optional output of attendance_queries.intervals_to_slot_counts().
        When provided (room_attendance_intervals data exists for this date),
        it is used in place of the enrolment-derived children matrix so the
        grid reflects actual/expected counts entered via the Child Attendance page.
        Falls back to enrolment-based build_children_matrix when None.

    Returns:
        coverage_matrix  — {room_id: [staff_count per slot]}
        children_matrix  — {room_id: [child_count per slot]}
        diploma_matrix   — {room_id: [diploma_staff per slot]}
        status_matrix    — {room_id: ["ok"|"warning"|"breach"|"empty" per slot]}
        slot_labels      — list of 56 label strings ("6am", "6:15am", ...)
        shifts_by_room   — {room_id: [shift_dict list]}
        hour_markers     — list of (slot_index, hour_label) for major gridlines
    """
    room_ids  = [r["id"] for r in rooms]
    room_map  = {r["id"]: r for r in rooms}
    slices    = build_shift_slices(shifts)
    cov_m     = build_coverage_matrix(slices, room_ids)
    dip_m     = build_diploma_matrix(slices, room_ids)
    if interval_counts is not None:
        child_m = build_children_matrix_from_intervals(interval_counts, room_ids)
    else:
        child_m = build_children_matrix(children, room_ids, day_of_week)

    status_m: dict[str, list[str]] = {}
    for room in rooms:
        rid      = room["id"]
        r_staff  = room.get("required_ratio_staff", 1)
        r_child  = room.get("required_ratio_children", 4)
        s_arr    = cov_m.get(rid, [0] * TOTAL_SLOTS)
        c_arr    = child_m.get(rid, [0] * TOTAL_SLOTS)
        statuses = []
        for slot in range(TOTAL_SLOTS):
            nc = c_arr[slot]
            ns = s_arr[slot]
            if nc == 0:
                statuses.append("empty")
            else:
                min_s = math.ceil(nc / r_child) * r_staff
                if ns < min_s:
                    statuses.append("breach")
                elif math.ceil((nc + 1) / r_child) * r_staff > ns:
                    statuses.append("warning")
                else:
                    statuses.append("ok")
        status_m[rid] = statuses

    shifts_by_room: dict[str, list] = {rid: [] for rid in room_ids}
    shifts_by_room["__unassigned__"] = []
    for s in shifts:
        rid = s.get("room_id") or "__unassigned__"
        if rid in shifts_by_room:
            shifts_by_room[rid].append(s)
        else:
            shifts_by_room["__unassigned__"].append(s)

    # Hour markers for gridlines (every 60 min = every 4 slots)
    hour_markers = [
        (slot, slot_label(slot))
        for slot in range(0, TOTAL_SLOTS, SLOTS_PER_HOUR)
    ]

    return {
        "coverage_matrix": cov_m,
        "children_matrix": child_m,
        "diploma_matrix":  dip_m,
        "status_matrix":   status_m,
        "slot_labels":     [slot_label(i) for i in range(TOTAL_SLOTS)],
        "shifts_by_room":  shifts_by_room,
        "hour_markers":    hour_markers,
        "total_slots":     TOTAL_SLOTS,
    }


# ── Week summary ──────────────────────────────────────────────────────────────

def week_compliance_pct(
    daily_conflicts: dict[str, list[RosterConflict]],
) -> int:
    """
    Given a dict of date→conflicts for a week,
    return an overall weekly compliance percentage.
    """
    total_errors = sum(
        sum(1 for c in cs if c.severity == "error")
        for cs in daily_conflicts.values()
    )
    total_checks = len(daily_conflicts) * TOTAL_SLOTS * 4   # rough denominator
    if total_checks == 0:
        return 100
    return max(0, round((1 - total_errors / total_checks) * 100 * 10))
