# utils/attendance_queries.py
# Database queries for the Child Attendance / Room Occupancy module.
# Uses room_attendance_intervals — aggregate counts per 15-minute slot.
# No .single() anywhere — uses _one() helper throughout.
#
# Performance design
# ──────────────────
# The old row-by-row path (SELECT + INSERT/UPDATE per row) made ~2 round-trips
# per interval. For one month of data across 3 rooms that is ~5,000+ HTTP calls.
#
# The new path uses Supabase .upsert() with on_conflict so the whole batch
# is handled in a single server-side operation per HTTP call. Rows are chunked
# at BATCH_SIZE to stay within PostgREST payload limits.
#
# preserve_expected semantics with batch upsert
# ─────────────────────────────────────────────
# When preserve_expected=True (CSV import), the upsert payload omits
# expected_children entirely. PostgreSQL ON CONFLICT DO UPDATE only touches
# the columns present in the payload, so the existing expected_children value
# is never overwritten.
# When preserve_expected=False (manual grid), expected_children is included
# and the full row is written.

from __future__ import annotations
from typing import Callable, Optional
from datetime import date, datetime, time, timedelta, timezone
from utils.supabase_client import get_supabase_client


BATCH_SIZE = 500    # rows per upsert call — safe for PostgREST payload limits


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
# BATCH UPSERT — core low-level function
# ─────────────────────────────────────────────────────────────────────────────

def upsert_batch(
    rows: list[dict],
    preserve_expected: bool = False,
) -> int:
    """
    Upsert a list of fully-formed interval row dicts in a single HTTP call.
    Rows are expected to already contain all required columns.

    on_conflict columns: centre_id, room_id, attendance_date, interval_start
    These four columns uniquely identify a row (matches the UNIQUE INDEX
    uq_room_attendance_interval in the migration SQL).

    preserve_expected=True  (CSV import):
        Omit expected_children from each row's payload so that PostgreSQL
        ON CONFLICT DO UPDATE never touches the existing planned count.
        Only actual_children, notes, interval_end, and updated_at are written.

    preserve_expected=False (manual grid):
        Include expected_children — full row is written on conflict.

    Returns count of rows sent to Supabase.
    Raises ValueError on empty response.
    """
    if not rows:
        return 0

    sb = get_supabase_client()

    if preserve_expected:
        # Strip expected_children from every row so it is never overwritten.
        # PostgreSQL will only SET the columns that appear in the payload.
        payload = [
            {k: v for k, v in row.items() if k != "expected_children"}
            for row in rows
        ]
    else:
        payload = rows

    resp = (
        sb.from_("room_attendance_intervals")
        .upsert(
            payload,
            on_conflict="centre_id,room_id,attendance_date,interval_start",
        )
        .execute()
    )

    # PostgREST upsert returns the affected rows; an empty list means nothing
    # was written (e.g. all rows were filtered out server-side).
    if resp.data is None:
        raise ValueError("Upsert returned no response — check Supabase connection.")

    return len(payload)


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
    Upsert a list of intervals for one room on one date using batch upsert.
    Replaces the old row-by-row SELECT+INSERT/UPDATE loop.

    Each row dict must contain:
        interval_start    — "HH:MM:SS"
        interval_end      — "HH:MM:SS"
        expected_children — int  (omitted from payload when preserve_expected=True)
        actual_children   — int or None
        notes             — str (optional)

    preserve_expected=True  → CSV import: preserve existing planned headcounts.
    preserve_expected=False → Manual grid: write expected_children as supplied.

    Returns count of rows saved.
    """
    if not rows:
        return 0

    # Build fully-formed row dicts with all required DB columns
    full_rows = [
        {
            "centre_id":         centre_id,
            "room_id":           room_id,
            "attendance_date":   attendance_date,
            "interval_start":    row["interval_start"],
            "interval_end":      row["interval_end"],
            "expected_children": int(row.get("expected_children") or 0),
            "actual_children":   row.get("actual_children"),
            "notes":             (row.get("notes") or "").strip() or None,
        }
        for row in rows
    ]

    saved = 0
    for i in range(0, len(full_rows), BATCH_SIZE):
        chunk = full_rows[i : i + BATCH_SIZE]
        saved += upsert_batch(chunk, preserve_expected=preserve_expected)

    return saved


# ─────────────────────────────────────────────────────────────────────────────
# UPSERT — bulk (multi-date CSV import path)
# ─────────────────────────────────────────────────────────────────────────────

def upsert_bulk_import(
    centre_id: str,
    tasks: list[dict],
    progress_callback: Callable[[float], None] | None = None,
) -> tuple[int, int, list[str]]:
    """
    Save all tasks from build_bulk_upsert_plan() to Supabase using batch upsert.

    Each task: {date_str, room_id, rows: [row_dict, ...]}

    Collects ALL rows across ALL tasks into one flat list, then sends them
    to Supabase in batches of BATCH_SIZE. This reduces hundreds or thousands
    of HTTP round-trips to a small number of batch calls.

    Uses preserve_expected=True: CSV imports update actual_children without
    overwriting existing expected_children (planned headcounts).

    progress_callback(fraction: float) — called after each batch with a value
        between 0.0 and 1.0. Pass st.progress(0).progress to get a live bar.

    Returns (total_intervals_saved, total_room_dates_saved, error_messages).
    """
    if not tasks:
        return 0, 0, []

    # ── 1. Flatten all tasks into one list of fully-formed DB rows ────
    all_rows: list[dict] = []
    room_date_set: set[tuple[str, str]] = set()

    for task in tasks:
        date_str = task["date_str"]
        room_id  = task["room_id"]
        room_date_set.add((room_id, date_str))

        for row in task["rows"]:
            all_rows.append({
                "centre_id":         centre_id,
                "room_id":           room_id,
                "attendance_date":   date_str,
                "interval_start":    row["interval_start"],
                "interval_end":      row["interval_end"],
                "expected_children": int(row.get("expected_children") or 0),
                "actual_children":   row.get("actual_children"),
                "notes":             (row.get("notes") or "").strip() or None,
            })

    if not all_rows:
        return 0, 0, []

    # ── 2. Send in batches ────────────────────────────────────────────
    total_batches = (len(all_rows) + BATCH_SIZE - 1) // BATCH_SIZE
    saved_rows    = 0
    errors:   list[str] = []

    for batch_idx, i in enumerate(range(0, len(all_rows), BATCH_SIZE)):
        chunk = all_rows[i : i + BATCH_SIZE]
        try:
            n = upsert_batch(chunk, preserve_expected=True)
            saved_rows += n
        except Exception as exc:
            # Identify which date/room this batch covers for the error message
            dates_in_chunk = sorted({r["attendance_date"] for r in chunk})
            rooms_in_chunk = sorted({r["room_id"][:8] for r in chunk})
            errors.append(
                f"Batch {batch_idx + 1}/{total_batches} "
                f"(dates {dates_in_chunk[0]}–{dates_in_chunk[-1]}, "
                f"{len(chunk)} rows): {exc}"
            )

        if progress_callback is not None:
            progress_callback((batch_idx + 1) / total_batches)

    return saved_rows, len(room_date_set), errors


def upsert_single_date_from_bulk(
    centre_id: str,
    date_str: str,
    room_counts: dict[str, dict[str, int]],
    intervals: list[dict],
    progress_callback: Callable[[float], None] | None = None,
) -> tuple[int, int, list[str]]:
    """
    Save one date's CSV data as actual_children using batch upsert.
    Does not overwrite existing expected_children values.

    room_counts comes directly from date_room_counts[date_str] in the
    parse_csv_bulk() result.
    """
    from utils.csv_attendance_import import room_counts_to_upsert_rows

    rows_by_room = room_counts_to_upsert_rows(room_counts, intervals)
    tasks = [
        {"date_str": date_str, "room_id": rid, "rows": rows}
        for rid, rows in rows_by_room.items()
    ]
    return upsert_bulk_import(centre_id, tasks, progress_callback=progress_callback)


def upsert_all_dates_from_bulk(
    centre_id: str,
    date_room_counts: dict[str, dict[str, dict[str, int]]],
    intervals: list[dict],
    progress_callback: Callable[[float], None] | None = None,
) -> tuple[int, int, list[str]]:
    """
    Save every date in a bulk import result as actual_children using batch upsert.
    Does not overwrite existing expected_children values.

    date_room_counts comes directly from parse_csv_bulk().
    """
    from utils.csv_attendance_import import build_bulk_upsert_plan

    tasks = build_bulk_upsert_plan(date_room_counts, intervals)
    return upsert_bulk_import(centre_id, tasks, progress_callback=progress_callback)


# ─────────────────────────────────────────────────────────────────────────────
# SINGLE-ROW UPSERT — kept for manual/legacy callers only
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
    Upsert a single interval row. Kept for backward compatibility.
    For bulk operations prefer upsert_all_intervals or upsert_bulk_import.
    No .single() — uses batch upsert of one row.
    """
    row = {
        "centre_id":         centre_id,
        "room_id":           room_id,
        "attendance_date":   attendance_date,
        "interval_start":    interval_start,
        "interval_end":      interval_end,
        "expected_children": expected_children,
        "actual_children":   actual_children,
        "notes":             notes.strip() or None,
    }
    upsert_batch([row], preserve_expected=preserve_expected)
    return row


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
