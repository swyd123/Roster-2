# utils/csv_attendance_import.py
# Pure-Python CSV parsing and interval count generation for Child Attendance.
# No database calls — all DB work stays in attendance_queries.py.
# No .single() anywhere.
#
# Supports two CSV modes:
#   Single-date: child_name, room_name, start_time, end_time
#   Bulk-date:   attendance_date, child_name, room_name, start_time, end_time
#
# When attendance_date column is present, parse_csv_bulk() groups results by
# date and returns {date_str → {room_id → {interval_start → count}}}.
# parse_csv() remains for single-date backwards compatibility and delegates
# to the bulk parser internally.

from __future__ import annotations
import io
import math
from datetime import datetime, date as _date

import pandas as pd


# ── Public API — bulk (multi-date) ────────────────────────────────────────────

def parse_csv_bulk(
    file_bytes: bytes,
    rooms: list[dict],
    intervals: list[dict],
    fallback_date: str | None = None,
) -> dict:
    """
    Parse a CSV attendance file that may span multiple dates.

    Parameters
    ----------
    file_bytes      Raw bytes from st.file_uploader.
    rooms           List of room dicts — needs 'id' and 'name'.
    intervals       List of interval dicts from generate_intervals() —
                    needs 'interval_start' and 'interval_end' ("HH:MM:SS").
                    The same interval template is used for every date.
    fallback_date   ISO date string ("YYYY-MM-DD") used when the CSV has no
                    attendance_date column.  Required if column is absent.

    Returns
    -------
    dict with keys:
        errors        list[str]   — blocking problems; empty on success.
        warnings      list[str]   — non-blocking (unknown rooms, bad times).
        preview_df    pd.DataFrame | None  — one row per valid child-day.
        date_room_counts  dict | None
                      {date_str: {room_id: {interval_start: count}}}
                      None when errors is non-empty.
        dates         list[str]   — sorted unique date strings in the import.
        n_children    int         — valid row count.
        n_skipped     int         — skipped row count.
    """
    errors:   list[str] = []
    warnings: list[str] = []

    # ── 1. Read & normalise ───────────────────────────────────────────
    try:
        text = file_bytes.decode("utf-8", errors="replace")
        df   = pd.read_csv(io.StringIO(text))
    except Exception as exc:
        return _bulk_fail([f"Could not read CSV file: {exc}"])

    if df.empty:
        return _bulk_fail(["CSV file is empty."])

    df.columns = [
        c.strip().lower().replace(" ", "_").replace("-", "_")
        for c in df.columns
    ]

    # ── 2. Validate required columns ─────────────────────────────────
    has_date_col = "attendance_date" in df.columns
    required     = {"child_name", "room_name", "start_time", "end_time"}
    missing      = required - set(df.columns)
    if missing:
        return _bulk_fail([
            f"Missing required column(s): **{', '.join(sorted(missing))}**. "
            f"Found: {', '.join(df.columns.tolist())}."
        ])

    if not has_date_col and not fallback_date:
        return _bulk_fail([
            "Column **attendance_date** is missing and no fallback date was provided. "
            "Add an attendance_date column (YYYY-MM-DD) or select a date before uploading."
        ])

    # ── 3. Room lookup ─────────────────────────────────────────────────
    def _norm(s: str) -> str:
        return " ".join(str(s).strip().lower().split())

    room_by_name: dict[str, dict] = {_norm(r["name"]): r for r in rooms}

    # ── 4. Parse rows ─────────────────────────────────────────────────
    parsed_rows: list[dict] = []
    unknown_rooms: set[str] = set()
    bad_dates:     list[int] = []
    n_total = len(df)

    for idx, row in df.iterrows():
        rownum     = int(idx) + 2
        child_name = str(row.get("child_name", f"Child {rownum}")).strip()
        csv_room   = str(row.get("room_name", "")).strip()
        start_raw  = row.get("start_time", "")
        end_raw    = row.get("end_time", "")

        # Attendance date
        if has_date_col:
            date_str = _parse_date(row.get("attendance_date", ""))
            if date_str is None:
                raw_d = row.get("attendance_date", "")
                bad_dates.append(rownum)
                warnings.append(
                    f"Row {rownum} ({child_name}): invalid attendance_date "
                    f"'{raw_d}' — expected YYYY-MM-DD. Row skipped."
                )
                continue
        else:
            date_str = fallback_date  # already validated above

        # Room match
        matched_room = room_by_name.get(_norm(csv_room))
        if matched_room is None:
            unknown_rooms.add(csv_room)
            continue

        # Times
        start_str = _parse_time(start_raw)
        end_str   = _parse_time(end_raw)

        if start_str is None or end_str is None:
            warnings.append(
                f"Row {rownum} ({child_name}, {date_str}): could not parse "
                f"start_time='{start_raw}' or end_time='{end_raw}' — row skipped."
            )
            continue

        if end_str <= start_str:
            warnings.append(
                f"Row {rownum} ({child_name}, {date_str}): "
                f"end_time '{end_raw}' is not after start_time '{start_raw}' — row skipped."
            )
            continue

        parsed_rows.append({
            "date_str":   date_str,
            "child_name": child_name,
            "room_id":    matched_room["id"],
            "room_name":  matched_room["name"],
            "start":      start_str,
            "end":        end_str,
        })

    # Report unknown rooms once
    for ur in sorted(unknown_rooms):
        known = ", ".join(f"'{r['name']}'" for r in rooms)
        warnings.append(
            f"Unknown room name **'{ur}'** — rows skipped. "
            f"Known rooms: {known}."
        )

    if not parsed_rows:
        errors.append(
            "No valid rows remain after validation. "
            "Check room names, date format (YYYY-MM-DD) and times (HH:MM)."
        )
        return _bulk_fail(errors, warnings)

    # ── 5. Preview DataFrame ─────────────────────────────────────────
    preview_df = pd.DataFrame([
        {
            "Date":    r["date_str"],
            "Child":   r["child_name"],
            "Room":    r["room_name"],
            "Start":   r["start"][:5],
            "End":     r["end"][:5],
            "Hours":   _duration_str(r["start"], r["end"]),
        }
        for r in parsed_rows
    ]).sort_values(["Date", "Room", "Start"]).reset_index(drop=True)

    # ── 6. Count children: {date → {room_id → {interval_start → count}}} ─
    iv_starts = [iv["interval_start"] for iv in intervals]
    iv_lookup = {iv["interval_start"]: iv["interval_end"] for iv in intervals}

    date_room_counts: dict[str, dict[str, dict[str, int]]] = {}

    for child in parsed_rows:
        d   = child["date_str"]
        rid = child["room_id"]
        s   = child["start"]
        e   = child["end"]

        if d not in date_room_counts:
            date_room_counts[d] = {}
        if rid not in date_room_counts[d]:
            date_room_counts[d][rid] = {iv: 0 for iv in iv_starts}

        for iv_start in iv_starts:
            if s <= iv_start < e:
                date_room_counts[d][rid][iv_start] += 1

    # Remove rooms with all-zero counts (children outside operating hours)
    for d in list(date_room_counts):
        date_room_counts[d] = {
            rid: counts
            for rid, counts in date_room_counts[d].items()
            if any(v > 0 for v in counts.values())
        }
        if not date_room_counts[d]:
            del date_room_counts[d]

    dates = sorted(date_room_counts.keys())

    return {
        "errors":           errors,
        "warnings":         warnings,
        "preview_df":       preview_df,
        "date_room_counts": date_room_counts,
        "dates":            dates,
        "n_children":       len(parsed_rows),
        "n_skipped":        n_total - len(parsed_rows),
    }


# ── Public API — single-date (backwards compatible) ───────────────────────────

def parse_csv(
    file_bytes: bytes,
    rooms: list[dict],
    intervals: list[dict],
    fallback_date: str | None = None,
) -> dict:
    """
    Single-date wrapper around parse_csv_bulk.
    Returns the same shape as the old parse_csv for backwards compatibility,
    plus a 'date_room_counts' key for pages that have already migrated.
    """
    bulk = parse_csv_bulk(file_bytes, rooms, intervals, fallback_date)

    # Build legacy room_counts: flatten across all dates (or first date only)
    room_counts = None
    if bulk.get("date_room_counts"):
        # Merge counts across all dates — appropriate when there's only one date
        room_counts = {}
        for _d, rc in bulk["date_room_counts"].items():
            for rid, counts in rc.items():
                if rid not in room_counts:
                    room_counts[rid] = dict(counts)
                else:
                    for iv, cnt in counts.items():
                        room_counts[rid][iv] = room_counts[rid].get(iv, 0) + cnt

    return {
        "errors":           bulk["errors"],
        "warnings":         bulk["warnings"],
        "preview_df":       bulk["preview_df"],
        "room_counts":      room_counts,
        "date_room_counts": bulk.get("date_room_counts"),
        "dates":            bulk.get("dates", []),
        "n_children":       bulk["n_children"],
        "n_skipped":        bulk["n_skipped"],
    }


# ── Convenience: convert counts to upsert rows ────────────────────────────────

def room_counts_to_upsert_rows(
    room_counts: dict[str, dict[str, int]],
    intervals: list[dict],
    notes: str = "Imported from CSV",
) -> dict[str, list[dict]]:
    """
    Convert {room_id → {interval_start → count}} to
    {room_id → [row_dict, ...]} ready for upsert_all_intervals.
    Only intervals within the centre's operating hours are included.
    """
    iv_lookup = {iv["interval_start"]: iv for iv in intervals}
    result: dict[str, list[dict]] = {}

    for room_id, counts in room_counts.items():
        rows = []
        for iv_start, count in counts.items():
            if iv_start not in iv_lookup:
                continue
            iv = iv_lookup[iv_start]
            rows.append({
                "interval_start":    iv_start,
                "interval_end":      iv["interval_end"],
                "expected_children": count,
                "actual_children":   None,
                "notes":             notes,
            })
        if rows:
            result[room_id] = sorted(rows, key=lambda r: r["interval_start"])

    return result


def build_bulk_upsert_plan(
    date_room_counts: dict[str, dict[str, dict[str, int]]],
    intervals: list[dict],
) -> list[dict]:
    """
    Flatten date_room_counts into a list of upsert tasks.
    Each task: {date_str, room_id, rows: [row_dict, ...]}
    """
    tasks = []
    for date_str, room_counts in sorted(date_room_counts.items()):
        rows_by_room = room_counts_to_upsert_rows(room_counts, intervals)
        for room_id, rows in rows_by_room.items():
            tasks.append({
                "date_str": date_str,
                "room_id":  room_id,
                "rows":     rows,
            })
    return tasks


# ── Internal helpers ──────────────────────────────────────────────────────────

def _parse_date(val) -> str | None:
    """Parse a date value into ISO "YYYY-MM-DD" string, or None if unparseable."""
    if val is None:
        return None
    if isinstance(val, float) and math.isnan(val):
        return None
    s = str(val).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _parse_time(val) -> str | None:
    """Parse a time value into "HH:MM:SS" string, or None if unparseable."""
    if val is None:
        return None
    if isinstance(val, float) and math.isnan(val):
        return None
    s = str(val).strip()
    if not s:
        return None
    for fmt in ("%H:%M:%S", "%H:%M", "%I:%M %p", "%I:%M%p", "%I:%M:%S %p"):
        try:
            return datetime.strptime(s, fmt).strftime("%H:%M:%S")
        except ValueError:
            continue
    return None


def _duration_str(start: str, end: str) -> str:
    """Return a human-readable duration like '7h 30m'."""
    try:
        s    = datetime.strptime(start[:5], "%H:%M")
        e    = datetime.strptime(end[:5],   "%H:%M")
        mins = int((e - s).total_seconds() / 60)
        h, m = divmod(mins, 60)
        if m == 0:  return f"{h}h"
        if h == 0:  return f"{m}m"
        return f"{h}h {m}m"
    except Exception:
        return "—"


def _bulk_fail(errors: list[str], warnings: list[str] | None = None) -> dict:
    return {
        "errors":           errors,
        "warnings":         warnings or [],
        "preview_df":       None,
        "date_room_counts": None,
        "dates":            [],
        "n_children":       0,
        "n_skipped":        0,
    }
def parse_csv_bulk(file_bytes, rooms, intervals):
    """
    Backwards-compatible wrapper used by child_attendance.py.
    Calls the existing CSV parser.
    """
    return parse_csv(file_bytes, rooms, intervals)
