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
    """All stored intervals for a room on a date, ordered by start time."""
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
    """All intervals for every room at a centre on a date."""
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
    """All intervals for a centre across a date range."""
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
# UPSERT — single interval
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
    preserve_expected: bool = False,
) -> dict:
    """
    Insert or update a single 15-minute interval row.

    preserve_expected=True (used by CSV import):
        When updating an existing row, leave expected_children unchanged.
        Only actual_children and notes are updated.
        When inserting a new row, expected_children defaults to 0.

    preserve_expected=False (default, used by the manual grid):
        Both expected_children and actual_children are written as supplied.

    No .single() — uses check-then-insert/update pattern.
    Safe against the UNIQUE constraint on (room_id, attendance_date, interval_start).
    """
    sb = get_supabase_client()

    existing = (
        sb.from_("room_attendance_intervals")
        .select("id, expected_children")
        .eq("room_id", room_id)
        .eq("attendance_date", attendance_date)
        .eq("interval_start", interval_start)
        .limit(1)
        .execute()
    ).data or []

    if existing:
        row_id = existing[0]["id"]

        if preserve_expected:
            # CSV import path: keep the previously planned expected count
            update_payload = {
                "actual_children": actual_children,
                "notes":           notes.strip() or None,
            }
        else:
            # Manual grid path: overwrite both columns as supplied
            update_payload = {
                "expected_children": expected_children,
                "actual_children":   actual_children,
                "notes":             notes.strip() or None,
            }

        result = _one(
            sb.from_("room_attendance_intervals")
            .update(update_payload)
            .eq("id", row_id)
            .select()
            .execute()
        )
    else:
        # New row — expected_children is 0 when coming from CSV import
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
            f"Interval {attendance_date} {interval_start} could not be saved — "
            "no row returned from database."
        )
    return result


# ─────────────────────────────────────────────────────────────────────────────
# UPSERT — one room, one date (manual form save path)
# ─────────────────────────────────────────────────────────────────────────────

def upsert_all_intervals(
    centre_id: str,
    room_id: str,
    attendance_date: str,
    rows: list[dict],
    preserve_expected: bool = False,
) -> int:
    """
    Upsert a list of intervals for one room on one date.

    Each row dict must contain:
        interval_start    — "HH:MM:SS"
        interval_end      — "HH:MM:SS"
        expected_children — int  (ignored when preserve_expected=True on update)
        actual_children   — int or None
        notes             — str (optional)

    preserve_expected=True  → CSV import: don't overwrite planned headcounts.
    preserve_expected=False → Manual grid: write both columns as given.

    Returns count of rows saved.
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
            preserve_expected=preserve_expected,
        )
        saved += 1
    return saved


# ─────────────────────────────────────────────────────────────────────────────
# UPSERT — bulk (multi-date CSV import paths)
# ─────────────────────────────────────────────────────────────────────────────

def upsert_bulk_import(
    centre_id: str,
    tasks: list[dict],
) -> tuple[int, int, list[str]]:
    """
    Save all tasks from build_bulk_upsert_plan() to Supabase.

    Each task: {date_str, room_id, rows: [row_dict, ...]}

    Uses preserve_expected=True so that CSV imports update actual_children
    without overwriting any existing planned expected_children counts.

    Returns (total_intervals_saved, total_room_dates_saved, error_messages).
    """
    total_ivs   = 0
    total_rooms = 0
    errors: list[str] = []

    for task in tasks:
        try:
            n = upsert_all_intervals(
                centre_id=centre_id,
                room_id=task["room_id"],
                attendance_date=task["date_str"],
                rows=task["rows"],
                preserve_expected=True,   # CSV import — preserve planned counts
            )
            total_ivs   += n
            total_rooms += 1
        except Exception as exc:
            errors.append(
                f"{task['date_str']} / room {task['room_id']}: {exc}"
            )

    return total_ivs, total_rooms, errors


def upsert_single_date_from_bulk(
    centre_id: str,
    date_str: str,
    room_counts: dict[str, dict[str, int]],
    intervals: list[dict],
) -> tuple[int, int, list[str]]:
    """
    Save one date's CSV data as actual_children without touching
    existing expected_children values.

    room_counts comes directly from date_room_counts[date_str] in the
    parse_csv_bulk() result.
    """
    from utils.csv_attendance_import import room_counts_to_upsert_rows

    rows_by_room = room_counts_to_upsert_rows(room_counts, intervals)
    tasks = [
        {"date_str": date_str, "room_id": rid, "rows": rows}
        for rid, rows in rows_by_room.items()
    ]
    return upsert_bulk_import(centre_id, tasks)


def upsert_all_dates_from_bulk(
    centre_id: str,
    date_room_counts: dict[str, dict[str, dict[str, int]]],
    intervals: list[dict],
) -> tuple[int, int, list[str]]:
    """
    Save every date in a bulk import result as actual_children without
    touching existing expected_children values.

    date_room_counts comes directly from parse_csv_bulk().
    """
    from utils.csv_attendance_import import build_bulk_upsert_plan

    tasks = build_bulk_upsert_plan(date_room_counts, intervals)
    return upsert_bulk_import(centre_id, tasks)


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS FOR RATIO ENGINE
# ─────────────────────────────────────────────────────────────────────────────

def intervals_to_slot_counts(
    intervals: list[dict],
    use_actual: bool = True,
) -> dict[str, list[int]]:
    """
    Convert interval rows into a slot-indexed count dict keyed by room_id.
    Slot 0 = 06:00, each slot = 15 min.

    use_actual=True  → prefer actual_children when set, fall back to expected.
                       This is the default: actual attendance data takes
                       priority over planned headcounts.
    use_actual=False → always use expected_children.
    """
    from utils.roster_engine import TOTAL_SLOTS, time_to_slot

    result: dict[str, list[int]] = {}

    for row in intervals:
        rid = row.get("room_id", "")
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
    Return the child count for a room at a specific time (HH:MM:SS).

    use_actual=True → prefer actual_children when recorded (CSV import),
                      fall back to expected_children (manual planning entry).
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


def summarise_day(intervals: list[dict], room_id: str) -> dict:
    """Summarise a room's day attendance from its intervals."""
    room_ivs    = [r for r in intervals if r.get("room_id") == room_id]
    n_intervals = len(room_ivs)
    n_recorded  = sum(1 for r in room_ivs if r.get("actual_children") is not None)
    total_exp   = sum(int(r.get("expected_children") or 0) for r in room_ivs)
    total_act   = sum(
        int(r.get("actual_children") or 0)
        for r in room_ivs if r.get("actual_children") is not None
    )
    peak_exp = max(
        (int(r.get("expected_children") or 0) for r in room_ivs), default=0
    )
    peak_act = max(
        (int(r.get("actual_children") or 0)
         for r in room_ivs if r.get("actual_children") is not None),
        default=0,
    )
    return {
        "total_expected": total_exp,
        "total_actual":   total_act,
        "peak_expected":  peak_exp,
        "peak_actual":    peak_act,
        "n_intervals":    n_intervals,
        "n_recorded":     n_recorded,
    }
