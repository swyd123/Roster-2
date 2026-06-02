# utils/csv_attendance_import.py
# Pure-Python CSV parsing and interval count generation for Child Attendance.
# No database calls. No .single().
#
# Two independent public functions — neither calls the other:
#
#   parse_csv_bulk(file_bytes, rooms, intervals, fallback_date=None)
#       Multi-date parser. Accepts attendance_date column.
#       Groups by date + room. Returns bulk_room_counts / date_room_counts.
#       CSV counts → actual_children (not expected_children).
#
#   parse_csv(file_bytes, rooms, intervals, fallback_date=None)
#       Single-date parser. Does not accept attendance_date column.
#       Returns room_counts (flat, one date only).
#       CSV counts → actual_children (not expected_children).
#
# Both share private _parse_date / _parse_time / _duration_str helpers only.

from __future__ import annotations
import io
import math
from datetime import datetime

import pandas as pd


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC: parse_csv_bulk — multi-date, all rooms
# ─────────────────────────────────────────────────────────────────────────────

def parse_csv_bulk(
    file_bytes: bytes,
    rooms: list[dict],
    intervals: list[dict],
    fallback_date: str | None = None,
) -> dict:
    """
    Parse a CSV attendance file that may span multiple dates and rooms.
    Does NOT call parse_csv — entirely self-contained.

    Required CSV columns:
        attendance_date   YYYY-MM-DD (optional if fallback_date is provided)
        child_name
        room_name
        start_time        HH:MM or HH:MM:SS
        end_time          HH:MM or HH:MM:SS

    CSV counts are placed into actual_children, not expected_children,
    because the CSV represents real attendance records.

    Returns
    -------
    dict with keys:
        errors            list[str]         — blocking; empty on success
        warnings          list[str]         — non-blocking
        preview_df        pd.DataFrame|None — one row per valid child-day
        summary_df        pd.DataFrame|None — one row per date × room
        bulk_room_counts  dict|None         — primary key
                          {date_str: {room_id: {interval_start: count}}}
        date_room_counts  dict|None         — alias for bulk_room_counts
        dates             list[str]         — sorted unique dates in import
        n_children        int               — valid rows parsed
        n_skipped         int               — rows skipped
    """
    errors:   list[str] = []
    warnings: list[str] = []

    # ── 1. Read & normalise columns ───────────────────────────────────
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

    # ── 2. Column validation ──────────────────────────────────────────
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
            "Add an attendance_date column (YYYY-MM-DD), or select a date on the page "
            "before uploading."
        ])

    # ── 3. Room name lookup (case-insensitive, whitespace-insensitive) ─
    def _norm(s: str) -> str:
        return " ".join(str(s).strip().lower().split())

    room_by_name: dict[str, dict] = {_norm(r["name"]): r for r in rooms}

    # ── 4. Parse and validate each row ───────────────────────────────
    parsed_rows:  list[dict] = []
    unknown_rooms: set[str]  = set()
    n_total = len(df)

    for idx, row in df.iterrows():
        rownum     = int(idx) + 2
        child_name = str(row.get("child_name", f"Child {rownum}")).strip()
        csv_room   = str(row.get("room_name", "")).strip()
        start_raw  = row.get("start_time", "")
        end_raw    = row.get("end_time",   "")

        # Date
        if has_date_col:
            date_str = _parse_date(row.get("attendance_date", ""))
            if date_str is None:
                raw_d = row.get("attendance_date", "")
                warnings.append(
                    f"Row {rownum} ({child_name}): invalid attendance_date "
                    f"'{raw_d}' — expected YYYY-MM-DD. Row skipped."
                )
                continue
        else:
            date_str = fallback_date

        # Room
        matched_room = room_by_name.get(_norm(csv_room))
        if matched_room is None:
            unknown_rooms.add(csv_room)
            continue

        # Times
        start_str = _parse_time(start_raw)
        end_str   = _parse_time(end_raw)

        if start_str is None or end_str is None:
            warnings.append(
                f"Row {rownum} ({child_name}, {date_str}): "
                f"could not parse start_time='{start_raw}' or "
                f"end_time='{end_raw}' — row skipped."
            )
            continue

        if end_str <= start_str:
            warnings.append(
                f"Row {rownum} ({child_name}, {date_str}): "
                f"end_time '{end_raw}' is not after start_time "
                f"'{start_raw}' — row skipped."
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

    for ur in sorted(unknown_rooms):
        known = ", ".join(f"'{r['name']}'" for r in rooms)
        warnings.append(
            f"Unknown room name **'{ur}'** — all rows for this room skipped. "
            f"Known rooms: {known}."
        )

    if not parsed_rows:
        errors.append(
            "No valid rows remain after validation. "
            "Check room names match exactly, date format is YYYY-MM-DD, "
            "and times are HH:MM or HH:MM:SS."
        )
        return _bulk_fail(errors, warnings)

    # ── 5. Preview DataFrame ──────────────────────────────────────────
    preview_df = pd.DataFrame([
        {
            "Date":  r["date_str"],
            "Child": r["child_name"],
            "Room":  r["room_name"],
            "Start": r["start"][:5],
            "End":   r["end"][:5],
            "Hours": _duration_str(r["start"], r["end"]),
        }
        for r in parsed_rows
    ]).sort_values(["Date", "Room", "Start"]).reset_index(drop=True)

    # ── 6. Count children per date × room × 15-min interval ──────────
    # Presence rule: child counted at interval_start if
    #   child_start <= interval_start < child_end
    iv_starts     = [iv["interval_start"] for iv in intervals]
    iv_end_lookup = {iv["interval_start"]: iv["interval_end"] for iv in intervals}

    # {date_str: {room_id: {interval_start: count}}}
    bulk_room_counts: dict[str, dict[str, dict[str, int]]] = {}

    for child in parsed_rows:
        d   = child["date_str"]
        rid = child["room_id"]
        s   = child["start"]
        e   = child["end"]

        if d not in bulk_room_counts:
            bulk_room_counts[d] = {}
        if rid not in bulk_room_counts[d]:
            bulk_room_counts[d][rid] = {iv: 0 for iv in iv_starts}

        for iv_start in iv_starts:
            if s <= iv_start < e:
                bulk_room_counts[d][rid][iv_start] += 1

    # Drop rooms where every interval is zero
    for d in list(bulk_room_counts):
        bulk_room_counts[d] = {
            rid: counts
            for rid, counts in bulk_room_counts[d].items()
            if any(v > 0 for v in counts.values())
        }
        if not bulk_room_counts[d]:
            del bulk_room_counts[d]

    dates = sorted(bulk_room_counts.keys())

    # ── 7. Summary DataFrame ──────────────────────────────────────────
    room_name_by_id = {r["id"]: r["name"] for r in rooms}
    summary_rows = []
    for d in dates:
        for rid, counts in bulk_room_counts[d].items():
            active = {iv: cnt for iv, cnt in counts.items() if cnt > 0}
            if not active:
                continue
            first_iv = min(active)
            last_iv  = max(active)
            summary_rows.append({
                "Date":           d,
                "Room":           room_name_by_id.get(rid, rid),
                "Children":       sum(active.values()),
                "Peak":           max(active.values()),
                "First arrival":  first_iv[:5],
                "Last departure": iv_end_lookup.get(last_iv, last_iv)[:5],
            })

    summary_df = (
        pd.DataFrame(summary_rows)
        if summary_rows
        else pd.DataFrame(columns=["Date", "Room", "Children",
                                    "Peak", "First arrival", "Last departure"])
    )

    return {
        "errors":           errors,
        "warnings":         warnings,
        "preview_df":       preview_df,
        "summary_df":       summary_df,
        "bulk_room_counts": bulk_room_counts,
        "date_room_counts": bulk_room_counts,   # alias — page uses this key
        "dates":            dates,
        "n_children":       len(parsed_rows),
        "n_skipped":        n_total - len(parsed_rows),
    }


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC: parse_csv — single-date only, backwards compatible
# ─────────────────────────────────────────────────────────────────────────────

def parse_csv(
    file_bytes: bytes,
    rooms: list[dict],
    intervals: list[dict],
    fallback_date: str | None = None,
) -> dict:
    """
    Parse a single-date CSV attendance file.
    Does NOT call parse_csv_bulk — entirely self-contained.

    Required CSV columns: child_name, room_name, start_time, end_time
    attendance_date column is ignored if present.

    CSV counts → actual_children (not expected_children).

    Returns
    -------
    dict with keys:
        errors       list[str]
        warnings     list[str]
        preview_df   pd.DataFrame | None
        room_counts  {room_id: {interval_start: count}}  (flat, one date)
        n_children   int
        n_skipped    int
    """
    errors:   list[str] = []
    warnings: list[str] = []

    try:
        text = file_bytes.decode("utf-8", errors="replace")
        df   = pd.read_csv(io.StringIO(text))
    except Exception as exc:
        return _single_fail([f"Could not read CSV file: {exc}"])

    if df.empty:
        return _single_fail(["CSV file is empty."])

    df.columns = [
        c.strip().lower().replace(" ", "_").replace("-", "_")
        for c in df.columns
    ]

    required = {"child_name", "room_name", "start_time", "end_time"}
    missing  = required - set(df.columns)
    if missing:
        return _single_fail([
            f"Missing required column(s): **{', '.join(sorted(missing))}**. "
            f"Found: {', '.join(df.columns.tolist())}."
        ])

    def _norm(s: str) -> str:
        return " ".join(str(s).strip().lower().split())

    room_by_name: dict[str, dict] = {_norm(r["name"]): r for r in rooms}

    parsed_rows:  list[dict] = []
    unknown_rooms: set[str]  = set()
    n_total = len(df)

    for idx, row in df.iterrows():
        rownum     = int(idx) + 2
        child_name = str(row.get("child_name", f"Child {rownum}")).strip()
        csv_room   = str(row.get("room_name", "")).strip()
        start_raw  = row.get("start_time", "")
        end_raw    = row.get("end_time",   "")

        matched_room = room_by_name.get(_norm(csv_room))
        if matched_room is None:
            unknown_rooms.add(csv_room)
            continue

        start_str = _parse_time(start_raw)
        end_str   = _parse_time(end_raw)

        if start_str is None or end_str is None:
            warnings.append(
                f"Row {rownum} ({child_name}): "
                f"could not parse start_time='{start_raw}' or "
                f"end_time='{end_raw}' — row skipped."
            )
            continue

        if end_str <= start_str:
            warnings.append(
                f"Row {rownum} ({child_name}): "
                f"end_time '{end_raw}' is not after start_time "
                f"'{start_raw}' — row skipped."
            )
            continue

        parsed_rows.append({
            "child_name": child_name,
            "room_id":    matched_room["id"],
            "room_name":  matched_room["name"],
            "start":      start_str,
            "end":        end_str,
        })

    for ur in sorted(unknown_rooms):
        known = ", ".join(f"'{r['name']}'" for r in rooms)
        warnings.append(
            f"Unknown room name **'{ur}'** — rows skipped. "
            f"Known rooms: {known}."
        )

    if not parsed_rows:
        errors.append(
            "No valid rows remain after validation. "
            "Check room names match exactly and times are HH:MM or HH:MM:SS."
        )
        return _single_fail(errors, warnings)

    preview_df = pd.DataFrame([
        {
            "Child": r["child_name"],
            "Room":  r["room_name"],
            "Start": r["start"][:5],
            "End":   r["end"][:5],
            "Hours": _duration_str(r["start"], r["end"]),
        }
        for r in parsed_rows
    ]).sort_values(["Room", "Start"]).reset_index(drop=True)

    iv_starts = [iv["interval_start"] for iv in intervals]

    room_counts: dict[str, dict[str, int]] = {}

    for child in parsed_rows:
        rid = child["room_id"]
        s   = child["start"]
        e   = child["end"]

        if rid not in room_counts:
            room_counts[rid] = {iv: 0 for iv in iv_starts}

        for iv_start in iv_starts:
            if s <= iv_start < e:
                room_counts[rid][iv_start] += 1

    room_counts = {
        rid: counts
        for rid, counts in room_counts.items()
        if any(v > 0 for v in counts.values())
    }

    return {
        "errors":      errors,
        "warnings":    warnings,
        "preview_df":  preview_df,
        "room_counts": room_counts,
        "n_children":  len(parsed_rows),
        "n_skipped":   n_total - len(parsed_rows),
    }


# ─────────────────────────────────────────────────────────────────────────────
# CONVENIENCE: convert counts to upsert row format
# ─────────────────────────────────────────────────────────────────────────────

def room_counts_to_upsert_rows(
    room_counts: dict[str, dict[str, int]],
    intervals: list[dict],
    notes: str = "Imported from CSV",
) -> dict[str, list[dict]]:
    """
    Convert {room_id → {interval_start → count}} to
    {room_id → [row_dict, ...]} ready for upsert_all_intervals.

    The CSV count goes into actual_children.
    expected_children is set to 0 so that existing planned headcounts
    are preserved by the preserve_expected flag in upsert_interval.
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
                "expected_children": 0,       # leave existing planned count intact
                "actual_children":   count,   # CSV count → actual attendance
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
    Flatten date_room_counts / bulk_room_counts into a list of upsert tasks.
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


# ─────────────────────────────────────────────────────────────────────────────
# PRIVATE HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _parse_date(val) -> str | None:
    """Parse a date value into ISO 'YYYY-MM-DD' string, or None."""
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
    """Parse a time value into 'HH:MM:SS' string, or None."""
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
    """'08:00:00', '16:30:00' → '8h 30m'."""
    try:
        s    = datetime.strptime(start[:5], "%H:%M")
        e    = datetime.strptime(end[:5],   "%H:%M")
        mins = int((e - s).total_seconds() / 60)
        h, m = divmod(mins, 60)
        if m == 0: return f"{h}h"
        if h == 0: return f"{m}m"
        return f"{h}h {m}m"
    except Exception:
        return "—"


def _bulk_fail(errors: list[str], warnings: list[str] | None = None) -> dict:
    return {
        "errors":           errors,
        "warnings":         warnings or [],
        "preview_df":       None,
        "summary_df":       None,
        "bulk_room_counts": None,
        "date_room_counts": None,
        "dates":            [],
        "n_children":       0,
        "n_skipped":        0,
    }


def _single_fail(errors: list[str], warnings: list[str] | None = None) -> dict:
    return {
        "errors":      errors,
        "warnings":    warnings or [],
        "preview_df":  None,
        "room_counts": None,
        "n_children":  0,
        "n_skipped":   0,
    }
