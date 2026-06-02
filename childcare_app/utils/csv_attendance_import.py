# utils/csv_attendance_import.py
# Pure-Python CSV parsing and interval count generation for Child Attendance.
# No database calls — all DB work stays in attendance_queries.py.
# No .single() anywhere.

from __future__ import annotations
import io
from datetime import datetime, timedelta

import pandas as pd


# ── Public API ────────────────────────────────────────────────────────────────

def parse_csv(
    file_bytes: bytes,
    rooms: list[dict],
    intervals: list[dict],
) -> dict:
    """
    Parse a CSV attendance file and compute per-room, per-interval child counts.

    Parameters
    ----------
    file_bytes  Raw bytes from st.file_uploader.
    rooms       List of room dicts from fetch_rooms() — needs 'id' and 'name'.
    intervals   List of interval dicts from generate_intervals() — needs
                'interval_start' and 'interval_end' (both "HH:MM:SS").

    Returns
    -------
    dict with keys:
        errors      list[str]   — blocking problems (bad columns, unparseable rows)
        warnings    list[str]   — non-blocking (unknown room names, bad times)
        preview_df  pd.DataFrame | None — display table (one row per CSV child)
        room_counts dict | None — {room_id: {interval_start: count}}
                    None when errors is non-empty.

    CSV required columns (case-insensitive, spaces → underscores):
        child_name, room_name, start_time, end_time

    Presence rule: a child counts toward interval_start if
        child_start_time <= interval_start < child_end_time
    (string comparison on "HH:MM:SS" works because they share the same format)
    """
    errors:   list[str] = []
    warnings: list[str] = []

    # ── 1. Read CSV ───────────────────────────────────────────────────
    try:
        text = file_bytes.decode("utf-8", errors="replace")
        df   = pd.read_csv(io.StringIO(text))
    except Exception as exc:
        return _fail([f"Could not read CSV file: {exc}"])

    if df.empty:
        return _fail(["CSV file is empty."])

    # Normalise column names
    df.columns = [
        c.strip().lower().replace(" ", "_").replace("-", "_")
        for c in df.columns
    ]

    # ── 2. Validate required columns ─────────────────────────────────
    required = {"child_name", "room_name", "start_time", "end_time"}
    missing  = required - set(df.columns)
    if missing:
        return _fail([
            f"Missing required column(s): **{', '.join(sorted(missing))}**. "
            f"Found: {', '.join(df.columns.tolist())}."
        ])

    # ── 3. Build room lookup: normalised_name → room dict ─────────────
    # Normalise for matching: lowercase, strip, collapse spaces
    def _norm(s: str) -> str:
        return " ".join(str(s).strip().lower().split())

    room_by_name: dict[str, dict] = {_norm(r["name"]): r for r in rooms}

    # ── 4. Parse each row ─────────────────────────────────────────────
    parsed_rows = []        # [{child_name, room_id, room_name, start, end}]
    unknown_rooms: set[str] = set()
    bad_time_rows: list[int] = []

    for idx, row in df.iterrows():
        rownum    = int(idx) + 2   # 1-based, +1 for header
        child_name = str(row.get("child_name", f"Child {rownum}")).strip()
        csv_room   = str(row.get("room_name", "")).strip()
        start_raw  = row.get("start_time", "")
        end_raw    = row.get("end_time", "")

        # Match room
        matched_room = room_by_name.get(_norm(csv_room))
        if matched_room is None:
            unknown_rooms.add(csv_room)
            continue   # skip — will report as warning

        # Parse times
        start_str = _parse_time(start_raw)
        end_str   = _parse_time(end_raw)

        if start_str is None or end_str is None:
            bad_time_rows.append(rownum)
            warnings.append(
                f"Row {rownum} ({child_name}): could not parse "
                f"start_time='{start_raw}' or end_time='{end_raw}' — row skipped."
            )
            continue

        if end_str <= start_str:
            warnings.append(
                f"Row {rownum} ({child_name}): end_time '{end_raw}' is not after "
                f"start_time '{start_raw}' — row skipped."
            )
            continue

        parsed_rows.append({
            "child_name": child_name,
            "room_id":    matched_room["id"],
            "room_name":  matched_room["name"],
            "start":      start_str,
            "end":        end_str,
        })

    # Report unknown rooms
    for ur in sorted(unknown_rooms):
        known = ", ".join(f"'{r['name']}'" for r in rooms)
        warnings.append(
            f"Unknown room name **'{ur}'** — all children in this room were skipped. "
            f"Known rooms: {known}."
        )

    if not parsed_rows:
        errors.append(
            "No valid rows remain after validation. "
            "Check room names match exactly and times are in HH:MM or HH:MM:SS format."
        )
        return _fail(errors, warnings)

    # ── 5. Build preview DataFrame ────────────────────────────────────
    preview_df = pd.DataFrame([
        {
            "Child":      r["child_name"],
            "Room":       r["room_name"],
            "Start":      r["start"][:5],    # HH:MM
            "End":        r["end"][:5],
            "Hours":      _duration_str(r["start"], r["end"]),
        }
        for r in parsed_rows
    ])

    # ── 6. Count children per room per interval ───────────────────────
    # Presence rule: child present at interval if
    #     child_start <= interval_start < child_end
    # All comparisons on "HH:MM:SS" strings — sorts lexicographically
    # correctly for 24-hour times.

    # Initialise counts: {room_id: {interval_start: 0}}
    room_counts: dict[str, dict[str, int]] = {
        r["id"]: {iv["interval_start"]: 0 for iv in intervals}
        for r in rooms
    }

    for child in parsed_rows:
        rid         = child["room_id"]
        child_start = child["start"]
        child_end   = child["end"]
        if rid not in room_counts:
            continue
        for iv in intervals:
            iv_start = iv["interval_start"]
            if child_start <= iv_start < child_end:
                room_counts[rid][iv_start] += 1

    # Remove rooms that have no children in any interval
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
        "n_skipped":   len(df) - len(parsed_rows),
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_time(val) -> str | None:
    """
    Parse a time value into "HH:MM:SS" string.
    Accepts: "HH:MM", "HH:MM:SS", "H:MM AM/PM", NaN/None → None.
    """
    if val is None:
        return None
    if isinstance(val, float):
        import math
        if math.isnan(val):
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
        s = datetime.strptime(start[:5], "%H:%M")
        e = datetime.strptime(end[:5],   "%H:%M")
        mins = int((e - s).total_seconds() / 60)
        h, m = divmod(mins, 60)
        if m == 0:
            return f"{h}h"
        if h == 0:
            return f"{m}m"
        return f"{h}h {m}m"
    except Exception:
        return "—"


def _fail(errors: list[str], warnings: list[str] | None = None) -> dict:
    return {
        "errors":      errors,
        "warnings":    warnings or [],
        "preview_df":  None,
        "room_counts": None,
        "n_children":  0,
        "n_skipped":   0,
    }


# ── Convenience: convert room_counts to upsert rows ──────────────────────────

def room_counts_to_upsert_rows(
    room_counts: dict[str, dict[str, int]],
    intervals: list[dict],
) -> dict[str, list[dict]]:
    """
    Convert the room_counts output into the row format expected by
    upsert_all_intervals: {room_id → [row_dict, ...]}.

    Only intervals that fall within the centre's operating hours (i.e. exist
    in the intervals list) are included.
    """
    iv_lookup = {iv["interval_start"]: iv for iv in intervals}
    result: dict[str, list[dict]] = {}

    for room_id, counts in room_counts.items():
        rows = []
        for iv_start, count in counts.items():
            if iv_start not in iv_lookup:
                continue   # outside operating hours — skip
            iv = iv_lookup[iv_start]
            rows.append({
                "interval_start":    iv_start,
                "interval_end":      iv["interval_end"],
                "expected_children": count,
                "actual_children":   None,   # CSV provides expected, not actual
                "notes":             "Imported from CSV",
            })
        if rows:
            result[room_id] = sorted(rows, key=lambda r: r["interval_start"])

    return result
