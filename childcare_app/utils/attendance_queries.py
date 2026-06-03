# utils/attendance_queries.py
# Database queries for the Child Attendance / Room Occupancy module.
# Uses room_attendance_intervals — aggregate counts per 15-minute slot.
# No .single() anywhere — uses _one() helper throughout.
#
# Performance design
# ──────────────────
# upsert_batch uses Supabase .upsert() with on_conflict for batch efficiency.
# Requires the unique index:
#   CREATE UNIQUE INDEX IF NOT EXISTS uq_room_attendance_interval
#   ON room_attendance_intervals (centre_id, room_id, attendance_date, interval_start);
#
# If the index is absent, upsert_batch falls back to per-row SELECT+UPDATE/INSERT
# so data always persists regardless of index state.

from __future__ import annotations
from typing import Callable, Optional
from datetime import date, datetime, time, timedelta, timezone
from utils.supabase_client import get_supabase_client


BATCH_SIZE = 500


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
    Upsert a list of fully-formed interval row dicts.

    Primary path: Supabase .upsert() with on_conflict for maximum efficiency.
    Requires the unique index uq_room_attendance_interval.

    Fallback path: if the upsert returns no data (index absent or permissions
    issue), falls back to per-row SELECT + INSERT/UPDATE so data always persists.

    preserve_expected=True  (CSV import):
        Omit expected_children from payload so ON CONFLICT DO UPDATE never
        overwrites the existing planned count.

    preserve_expected=False (manual grid):
        Include expected_children — full row is written on conflict.

    Returns count of rows written.
    """
    if not rows:
        return 0

    sb = get_supabase_client()

    if preserve_expected:
        payload = [
            {k: v for k, v in row.items() if k != "expected_children"}
            for row in rows
        ]
    else:
        payload = rows

    try:
        resp = (
            sb.from_("room_attendance_intervals")
            .upsert(
                payload,
                on_conflict="centre_id,room_id,attendance_date,interval_start",
            )
            .execute()
        )
        # If upsert returned data, it worked
        if resp.data is not None:
            return len(payload)
        # Fallthrough to per-row fallback
    except Exception:
        pass  # Fallthrough to per-row fallback

    # ── Per-row fallback (no unique index, or upsert not supported) ───
    return _upsert_rows_individually(rows, preserve_expected)


def _upsert_rows_individually(
    rows: list[dict],
    preserve_expected: bool,
) -> int:
    """
    Fallback: SELECT then INSERT or UPDATE, one row at a time.
    Guarantees persistence even when the unique index is absent.
    """
    sb    = get_supabase_client()
    saved = 0

    for row in rows:
        centre_id      = row.get("centre_id", "")
        room_id        = row.get("room_id", "")
        att_date       = row.get("attendance_date", "")
        iv_start       = row.get("interval_start", "")
        iv_end         = row.get("interval_end", "")
        exp_children   = row.get("expected_children", 0)
        act_children   = row.get("actual_children")
        notes          = row.get("notes")

        existing = (
            sb.from_("room_attendance_intervals")
            .select("id")
            .eq("room_id", room_id)
            .eq("attendance_date", att_date)
            .eq("interval_start", iv_start)
            .limit(1)
            .execute()
        ).data or []

        if existing:
            row_id = existing[0]["id"]
            if preserve_expected:
                update_payload = {
                    "actual_children": act_children,
                    "notes":           notes,
                }
            else:
                update_payload = {
                    "expected_children": exp_children,
                    "actual_children":   act_children,
                    "notes":             notes,
                }
            sb.from_("room_attendance_intervals") \
              .update(update_payload) \
              .eq("id", row_id) \
              .execute()
        else:
            sb.from_("room_attendance_intervals") \
              .insert({
                  "centre_id":         centre_id,
                  "room_id":           room_id,
                  "attendance_date":   att_date,
                  "interval_start":    iv_start,
                  "interval_end":      iv_end,
                  "expected_children": exp_children,
                  "actual_children":   act_children,
                  "notes":             notes,
              }) \
              .execute()
        saved += 1

    return saved


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
        expected_children — int  (omitted from payload when preserve_expected=True)
        actual_children   — int or None
        notes             — str (optional)

    preserve_expected=True  → CSV import: preserve existing planned headcounts.
    preserve_expected=False → Manual grid: write expected_children as supplied.

    Returns count of rows saved.
    """
    if not rows:
        return 0

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

    Collects ALL rows across ALL tasks into one flat list then sends in
    batches of BATCH_SIZE. Uses preserve_expected=True so CSV imports
    update actual_children without overwriting planned expected_children.

    progress_callback(fraction) — called after each batch, 0.0–1.0.

    Returns (total_intervals_saved, total_room_dates_saved, error_messages).
    """
    if not tasks:
        return 0, 0, []

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

    total_batches = (len(all_rows) + BATCH_SIZE - 1) // BATCH_SIZE
    saved_rows    = 0
    errors: list[str] = []

    for batch_idx, i in enumerate(range(0, len(all_rows), BATCH_SIZE)):
        chunk = all_rows[i : i + BATCH_SIZE]
        try:
            n = upsert_batch(chunk, preserve_expected=True)
            saved_rows += n
        except Exception as exc:
            dates_in_chunk = sorted({r["attendance_date"] for r in chunk})
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
    """Save one date's CSV data as actual_children using batch upsert."""
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
    """Save every date in a bulk import result as actual_children."""
    from utils.csv_attendance_import import build_bulk_upsert_plan

    tasks = build_bulk_upsert_plan(date_room_counts, intervals)
    return upsert_bulk_import(centre_id, tasks, progress_callback=progress_callback)


# ─────────────────────────────────────────────────────────────────────────────
# SINGLE-ROW UPSERT — kept for backward compatibility
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
    Upsert a single interval row. No .single() — uses upsert_batch.
    Kept for backward compatibility; prefer upsert_all_intervals for bulk.
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
    use_actual=True  → prefer actual_children, fall back to expected.
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
    """Return the child count for a room at a specific time (HH:MM:SS)."""
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
    peak_exp = max((int(r.get("expected_children") or 0) for r in room_ivs), default=0)
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
