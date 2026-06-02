# utils/attendance_queries.py
# Database queries for the Child Attendance / Room Occupancy module.
# Uses room_attendance_intervals — aggregate counts per 15-minute slot.
# No .single() anywhere — uses _one() helper throughout.

from __future__ import annotations
from typing import Optional
from datetime import date, datetime, time, timedelta, timezone
from utils.supabase_client import get_supabase_client


def _one(resp) -> Optional[dict]:
    """Return first row from a response, or None. Never raises."""
    data = resp.data
    if not data:
        return None
    return data[0] if isinstance(data, list) else data


# ─────────────────────────────────────────────────────────────────────────────
# INTERVAL GENERATION (pure Python, no DB)
# ─────────────────────────────────────────────────────────────────────────────

def generate_intervals(
    opens_at: str | None,
    closes_at: str | None,
    interval_minutes: int = 15,
) -> list[dict]:
    """
    Generate a list of 15-minute interval dicts for the operating window.

    Falls back to 07:00–18:00 if centre hours are not configured.

    Each dict:
        interval_start  — "HH:MM:SS"
        interval_end    — "HH:MM:SS"  (start + 15 min)
        label           — "7:00 AM"
        slot_index      — 0-based index from 06:00
    """
    def parse(t_str: str | None, default_h: int) -> time:
        if not t_str:
            return time(default_h, 0)
        try:
            parts = str(t_str).split(":")
            return time(int(parts[0]), int(parts[1]))
        except Exception:
            return time(default_h, 0)

    start = parse(opens_at,  7)
    end   = parse(closes_at, 18)

    intervals = []
    current   = datetime.combine(date.today(), start)
    day_end   = datetime.combine(date.today(), end)
    slot_idx  = 0

    while current < day_end:
        nxt = current + timedelta(minutes=interval_minutes)
        intervals.append({
            "interval_start": current.strftime("%H:%M:%S"),
            "interval_end":   nxt.strftime("%H:%M:%S"),
            "label":          current.strftime("%-I:%M %p"),
            "slot_index":     slot_idx,
        })
        current   = nxt
        slot_idx += 1

    return intervals


# ─────────────────────────────────────────────────────────────────────────────
# FETCH
# ─────────────────────────────────────────────────────────────────────────────

def fetch_intervals_for_room(
    room_id: str,
    attendance_date: str,
) -> list[dict]:
    """
    All stored intervals for a room on a date, ordered by start time.
    Returns [] if no data has been entered yet.
    """
    sb = get_supabase_client()
    return (
        sb.from_("room_attendance_intervals")
        .select(
            "id, interval_start, interval_end, "
            "expected_children, actual_children, notes"
        )
        .eq("room_id", room_id)
        .eq("attendance_date", attendance_date)
        .order("interval_start")
        .execute()
    ).data or []


def fetch_intervals_for_centre(
    centre_id: str,
    attendance_date: str,
) -> list[dict]:
    """
    All intervals for every room at a centre on a date.
    Used by ratio dashboard to get child counts across all rooms.
    """
    sb = get_supabase_client()
    return (
        sb.from_("room_attendance_intervals")
        .select(
            "id, room_id, interval_start, interval_end, "
            "expected_children, actual_children, notes"
        )
        .eq("centre_id", centre_id)
        .eq("attendance_date", attendance_date)
        .order("interval_start")
        .execute()
    ).data or []


def fetch_intervals_for_centre_range(
    centre_id: str,
    from_date: str,
    to_date: str,
) -> list[dict]:
    """
    All intervals for a centre across a date range.
    Used by the roster engine to validate a multi-day period.
    """
    sb = get_supabase_client()
    return (
        sb.from_("room_attendance_intervals")
        .select(
            "room_id, attendance_date, interval_start, interval_end, "
            "expected_children, actual_children"
        )
        .eq("centre_id", centre_id)
        .gte("attendance_date", from_date)
        .lte("attendance_date", to_date)
        .order("attendance_date")
        .order("interval_start")
        .execute()
    ).data or []


# ─────────────────────────────────────────────────────────────────────────────
# UPSERT (the primary write path)
# ─────────────────────────────────────────────────────────────────────────────

def upsert_interval(
    centre_id: str,
    room_id: str,
    attendance_date: str,
    interval_start: str,
    interval_end: str,
    expected_children: int,
    actual_children: int | None,
    notes: str = "",
) -> dict:
    """
    Insert or update a single 15-minute interval row.
    The UNIQUE constraint on (room_id, attendance_date, interval_start)
    makes this a natural upsert — existing row is replaced.

    Returns the saved row, or raises ValueError if nothing was returned.
    """
    sb = get_supabase_client()

    # Check for existing row to decide insert vs update
    existing = (
        sb.from_("room_attendance_intervals")
        .select("id")
        .eq("room_id", room_id)
        .eq("attendance_date", attendance_date)
        .eq("interval_start", interval_start)
        .limit(1)
        .execute()
    ).data or []

    if existing:
        row_id = existing[0]["id"]
        result = _one(
            sb.from_("room_attendance_intervals")
            .update({
                "expected_children": expected_children,
                "actual_children":   actual_children,
                "notes":             notes.strip() or None,
            })
            .eq("id", row_id)
            .select()
            .execute()
        )
    else:
        result = _one(
            sb.from_("room_attendance_intervals")
            .insert({
                "centre_id":         centre_id,
                "room_id":           room_id,
                "attendance_date":   attendance_date,
                "interval_start":    interval_start,
                "interval_end":      interval_end,
                "expected_children": expected_children,
                "actual_children":   actual_children,
                "notes":             notes.strip() or None,
            })
            .select()
            .execute()
        )

    if not result:
        raise ValueError(
            f"Interval {interval_start} could not be saved — "
            "no row returned from database."
        )
    return result


def upsert_all_intervals(
    centre_id: str,
    room_id: str,
    attendance_date: str,
    rows: list[dict],
) -> int:
    """
    Bulk-upsert a full day's intervals for one room.
    Each row must have: interval_start, interval_end,
    expected_children, actual_children (int or None).
    Returns count of rows saved.

    Uses individual upserts (not a bulk insert) so that partial saves
    work correctly if the user only edits a subset of intervals.
    """
    saved = 0
    for row in rows:
        upsert_interval(
            centre_id=centre_id,
            room_id=room_id,
            attendance_date=attendance_date,
            interval_start=row["interval_start"],
            interval_end=row["interval_end"],
            expected_children=int(row.get("expected_children") or 0),
            actual_children=row.get("actual_children"),
            notes=row.get("notes", "") or "",
        )
        saved += 1
    return saved


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS FOR RATIO ENGINE
# ─────────────────────────────────────────────────────────────────────────────

def intervals_to_slot_counts(
    intervals: list[dict],
    use_actual: bool = False,
) -> dict[str, list[int]]:
    """
    Convert interval rows into a slot-indexed count dict keyed by room_id.
    Slot 0 = 06:00, each slot = 15 min.

    use_actual=True → prefer actual_children when set, fall back to expected.
    use_actual=False → always use expected_children.

    Returns: {room_id: [count_per_slot, ...]}  (56 slots, 06:00–20:00)
    """
    from utils.roster_engine import TOTAL_SLOTS, time_to_slot

    result: dict[str, list[int]] = {}

    for row in intervals:
        rid   = row.get("room_id", "")
        if rid not in result:
            result[rid] = [0] * TOTAL_SLOTS

        slot = time_to_slot(row.get("interval_start", "06:00"))
        if slot < 0 or slot >= TOTAL_SLOTS:
            continue

        if use_actual and row.get("actual_children") is not None:
            count = int(row["actual_children"])
        else:
            count = int(row.get("expected_children") or 0)

        if 0 <= slot < TOTAL_SLOTS:
            result[rid][slot] = count

    return result


def get_children_count_for_room_at_time(
    intervals: list[dict],
    room_id: str,
    time_str: str,
    use_actual: bool = True,
) -> int:
    """
    Return the child count for a specific room at a specific time (HH:MM:SS).
    Finds the interval whose start <= time_str < end.
    Returns 0 if no matching interval found.
    """
    for row in intervals:
        if row.get("room_id") != room_id:
            continue
        start = row.get("interval_start", "")
        end   = row.get("interval_end",   "")
        if start <= time_str[:8] < end:
            if use_actual and row.get("actual_children") is not None:
                return int(row["actual_children"])
            return int(row.get("expected_children") or 0)
    return 0


def summarise_day(
    intervals: list[dict],
    room_id: str,
) -> dict:
    """
    Summarise a room's day attendance from its intervals.
    Returns: {total_expected, total_actual, peak_expected, peak_actual,
    n_intervals, n_recorded}
    """
    room_ivs     = [r for r in intervals if r.get("room_id") == room_id]
    n_intervals  = len(room_ivs)
    n_recorded   = sum(1 for r in room_ivs if r.get("actual_children") is not None)
    total_exp    = sum(int(r.get("expected_children") or 0) for r in room_ivs)
    total_act    = sum(int(r.get("actual_children") or 0)
                       for r in room_ivs if r.get("actual_children") is not None)
    peak_exp     = max((int(r.get("expected_children") or 0) for r in room_ivs), default=0)
    peak_act     = max((int(r.get("actual_children") or 0)
                        for r in room_ivs if r.get("actual_children") is not None),
                       default=0)
    return {
        "total_expected": total_exp,
        "total_actual":   total_act,
        "peak_expected":  peak_exp,
        "peak_actual":    peak_act,
        "n_intervals":    n_intervals,
        "n_recorded":     n_recorded,
    }
