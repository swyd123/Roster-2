# utils/room_overflow_engine.py
# Pure-Python room overflow and adjusted staffing allocation engine.
# No database calls. No Streamlit. No .single().
#
# OVERVIEW
# ────────
# When a room's attendance exceeds its licensed_capacity for a given
# 15-minute interval, children cannot simply disappear — they are physically
# present and require supervision. This engine:
#
#   1. Identifies overflow per room per interval.
#   2. Finds "receiver" rooms in the same centre that have spare capacity
#      at the same interval.
#   3. Optionally uses age-band rules to constrain which rooms can receive
#      overflow children (if individual child DOBs are available).
#   4. Returns an adjusted staffing allocation — how many staff are needed
#      in each room after overflow redistribution — without touching any
#      database records.
#
# OUTPUTS (all in-memory, stored in session_state by the page)
# ──────────────────────────────────────────────────────────────
# analyse_overflow() returns:
#   {room_id: {interval_start: IntervalResult}}
#
# IntervalResult is a TypedDict:
#   original_count      int     — actual_children from DB (or expected fallback)
#   capacity            int     — licensed_capacity
#   overflow            int     — max(0, original_count - capacity)
#   after_overflow      int     — count after redistributing overflow OUT
#   received_overflow   int     — overflow received FROM other rooms
#   adjusted_count      int     — after_overflow + received_overflow
#   min_staff_required  int     — ceil(adjusted_count / ratio_children) * ratio_staff
#   suggestions         list[OverflowSuggestion]
#   needs_review        bool    — True when age data is unavailable
#
# OverflowSuggestion:
#   overflow_count      int
#   to_room_id          str
#   to_room_name        str
#   reason              str     — why this room was chosen
#   age_compatible      bool|None  — None when unknown

from __future__ import annotations
import math
from typing import TypedDict


# ─────────────────────────────────────────────────────────────────────────────
# TYPE DEFINITIONS
# ─────────────────────────────────────────────────────────────────────────────

class OverflowSuggestion(TypedDict):
    overflow_count: int
    to_room_id:     str
    to_room_name:   str
    reason:         str
    age_compatible: bool | None   # None = unknown (no individual child ages)


class IntervalResult(TypedDict):
    interval_start:     str
    interval_end:       str
    original_count:     int
    capacity:           int
    overflow:           int
    after_overflow:     int     # original_count - overflow sent to others
    received_overflow:  int     # overflow received from other rooms
    adjusted_count:     int     # after_overflow + received_overflow
    min_staff_required: int     # calculated from adjusted_count + room ratio
    suggestions:        list[OverflowSuggestion]
    needs_review:       bool


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────

def analyse_overflow(
    rooms: list[dict],
    day_intervals: list[dict],
    children: list[dict],
) -> dict[str, dict[str, IntervalResult]]:
    """
    Compute overflow and adjusted staffing allocation for every room
    across every 15-minute interval in day_intervals.

    Parameters
    ----------
    rooms           list of room dicts from fetch_rooms()
                    Must have: id, name, licensed_capacity,
                    required_ratio_staff, required_ratio_children,
                    age_min_months, age_max_months
    day_intervals   list of interval rows from fetch_intervals_for_centre()
                    Must have: room_id, interval_start, interval_end,
                    actual_children, expected_children
    children        list of child rows from fetch_children_by_centre()
                    Used for age-band overflow routing.
                    May be empty — engine degrades gracefully.

    Returns
    -------
    {room_id: {interval_start: IntervalResult}}
    Only intervals with actual data (non-zero count or capacity > 0) are
    included. Callers should handle missing keys as no-overflow / no-data.
    """
    if not rooms or not day_intervals:
        return {}

    room_map = {r["id"]: r for r in rooms}

    # Build interval count lookup: {room_id: {interval_start: count}}
    # Prefer actual_children; fall back to expected_children.
    iv_counts: dict[str, dict[str, int]] = {}
    iv_ends:   dict[str, str]            = {}   # interval_start → interval_end

    for iv in day_intervals:
        rid   = iv.get("room_id", "")
        istart = iv.get("interval_start", "")
        iend   = iv.get("interval_end",   "")
        act    = iv.get("actual_children")
        exp    = iv.get("expected_children")
        count  = int(act) if act is not None else (int(exp) if exp is not None else 0)

        if rid not in iv_counts:
            iv_counts[rid] = {}
        iv_counts[rid][istart] = count
        iv_ends[istart]         = iend

    # All unique interval starts across all rooms
    all_intervals = sorted({istart for counts in iv_counts.values() for istart in counts})

    # Build age-band lookup for children: {room_id: [age_in_months, ...]}
    has_age_data = any(c.get("date_of_birth") for c in children)
    children_by_room = _group_children_by_room(children)

    results: dict[str, dict[str, IntervalResult]] = {}

    for istart in all_intervals:
        iend = iv_ends.get(istart, "")

        # Snapshot: count per room at this interval
        room_counts = {
            rid: iv_counts.get(rid, {}).get(istart, 0)
            for rid in room_map
        }

        # Snapshot: spare capacity per room at this interval
        # spare = licensed_capacity - current_count  (clamped at 0)
        room_spare = {
            rid: max(0, room_map[rid].get("licensed_capacity", 0) - room_counts[rid])
            for rid in room_map
        }

        # Overflow redistribution — greedy, first-fit, age-aware
        # We process rooms in overflow order (largest overflow first) so the
        # most critical rooms get receiver priority.
        overflow_rooms = [
            rid for rid in room_map
            if room_counts[rid] > room_map[rid].get("licensed_capacity", 0)
        ]
        overflow_rooms.sort(
            key=lambda r: room_counts[r] - room_map[r].get("licensed_capacity", 0),
            reverse=True,
        )

        # Tracking adjustments across rooms for this interval
        sent_out:   dict[str, int] = {rid: 0 for rid in room_map}
        received_in: dict[str, int] = {rid: 0 for rid in room_map}
        suggestions_by_room: dict[str, list[OverflowSuggestion]] = {
            rid: [] for rid in room_map
        }
        needs_review_by_room: dict[str, bool] = {rid: False for rid in room_map}

        for over_rid in overflow_rooms:
            over_room = room_map[over_rid]
            over_cap  = over_room.get("licensed_capacity", 0)
            overflow  = room_counts[over_rid] - over_cap
            if overflow <= 0:
                continue

            remaining_to_place = overflow
            over_min = over_room.get("age_min_months", 0)
            over_max = over_room.get("age_max_months", 999)

            # Find candidate receiver rooms (everything except the overflowing room)
            # sorted by: (age-compatible first, then most spare capacity)
            candidates = _find_receiver_rooms(
                over_rid=over_rid,
                over_min=over_min,
                over_max=over_max,
                room_map=room_map,
                room_spare=room_spare,
                children_by_room=children_by_room,
                has_age_data=has_age_data,
            )

            for cand in candidates:
                if remaining_to_place <= 0:
                    break

                to_rid       = cand["room_id"]
                available    = room_spare.get(to_rid, 0)
                move_count   = min(remaining_to_place, available)
                if move_count <= 0:
                    continue

                # Update spare capacity to prevent double-booking
                room_spare[to_rid]   = max(0, room_spare[to_rid] - move_count)
                received_in[to_rid] += move_count
                sent_out[over_rid]  += move_count
                remaining_to_place  -= move_count

                suggestions_by_room[over_rid].append(OverflowSuggestion(
                    overflow_count=move_count,
                    to_room_id=to_rid,
                    to_room_name=room_map[to_rid].get("name", ""),
                    reason=cand["reason"],
                    age_compatible=cand["age_compatible"],
                ))

            if remaining_to_place > 0:
                # Could not place all overflow — flag for manual review
                needs_review_by_room[over_rid] = True
                suggestions_by_room[over_rid].append(OverflowSuggestion(
                    overflow_count=remaining_to_place,
                    to_room_id="",
                    to_room_name="",
                    reason=f"{remaining_to_place} child(ren) could not be placed — "
                           "no rooms with sufficient spare capacity found.",
                    age_compatible=None,
                ))

        # Build IntervalResult for each room that has data at this interval
        for rid, room in room_map.items():
            count = room_counts[rid]
            # Only emit results for rooms that have count > 0 OR have overflow/receive
            if count == 0 and received_in[rid] == 0:
                continue

            cap          = room.get("licensed_capacity", 0)
            overflow_out = sent_out[rid]
            overflow_in  = received_in[rid]
            overflow_amt = max(0, count - cap)
            after_out    = count - overflow_out
            adjusted     = after_out + overflow_in

            r_staff      = room.get("required_ratio_staff",    1)
            r_children   = room.get("required_ratio_children", 4)
            min_staff    = (
                math.ceil(adjusted / r_children) * r_staff
                if adjusted > 0 and r_children > 0
                else 0
            )

            if rid not in results:
                results[rid] = {}

            results[rid][istart] = IntervalResult(
                interval_start=istart,
                interval_end=iend,
                original_count=count,
                capacity=cap,
                overflow=overflow_amt,
                after_overflow=after_out,
                received_overflow=overflow_in,
                adjusted_count=adjusted,
                min_staff_required=min_staff,
                suggestions=suggestions_by_room[rid],
                needs_review=needs_review_by_room[rid] or (
                    overflow_amt > 0 and not has_age_data
                ),
            )

    return results


# ─────────────────────────────────────────────────────────────────────────────
# SUMMARY HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def peak_adjusted_staff(
    overflow_results: dict[str, dict[str, IntervalResult]],
    room_id: str,
) -> int | None:
    """Return the peak min_staff_required across all intervals for a room."""
    ivs = overflow_results.get(room_id, {})
    if not ivs:
        return None
    return max(r["min_staff_required"] for r in ivs.values())


def peak_overflow(
    overflow_results: dict[str, dict[str, IntervalResult]],
    room_id: str,
) -> int:
    """Return the peak overflow count for a room across all intervals."""
    ivs = overflow_results.get(room_id, {})
    if not ivs:
        return 0
    return max(r["overflow"] for r in ivs.values())


def has_overflow(
    overflow_results: dict[str, dict[str, IntervalResult]],
    room_id: str,
) -> bool:
    return peak_overflow(overflow_results, room_id) > 0


def centre_overflow_summary(
    overflow_results: dict[str, dict[str, IntervalResult]],
    rooms: list[dict],
) -> dict:
    """
    Returns a summary dict for the whole centre:
        n_overflow_rooms    int
        n_overflow_intervals int
        total_peak_overflow  int
        needs_review         bool
        overflow_room_ids    list[str]
    """
    n_overflow_rooms     = 0
    n_overflow_intervals = 0
    total_peak_overflow  = 0
    needs_review         = False
    overflow_room_ids    = []

    for room in rooms:
        rid  = room["id"]
        ivs  = overflow_results.get(rid, {})
        room_overflow_ivs = [r for r in ivs.values() if r["overflow"] > 0]
        if room_overflow_ivs:
            n_overflow_rooms     += 1
            n_overflow_intervals += len(room_overflow_ivs)
            total_peak_overflow  += max(r["overflow"] for r in room_overflow_ivs)
            overflow_room_ids.append(rid)
        if any(r["needs_review"] for r in ivs.values()):
            needs_review = True

    return {
        "n_overflow_rooms":     n_overflow_rooms,
        "n_overflow_intervals": n_overflow_intervals,
        "total_peak_overflow":  total_peak_overflow,
        "needs_review":         needs_review,
        "overflow_room_ids":    overflow_room_ids,
    }


def interval_timeline(
    overflow_results: dict[str, dict[str, IntervalResult]],
    room_id: str,
) -> list[IntervalResult]:
    """All IntervalResult entries for a room, sorted by interval_start."""
    ivs = overflow_results.get(room_id, {})
    return sorted(ivs.values(), key=lambda r: r["interval_start"])


# ─────────────────────────────────────────────────────────────────────────────
# PRIVATE
# ─────────────────────────────────────────────────────────────────────────────

def _group_children_by_room(children: list[dict]) -> dict[str, list[int | None]]:
    """
    Group children's ages (in months) by room_id.
    Returns {room_id: [age_months or None, ...]}
    """
    from datetime import date as _date

    result: dict[str, list[int | None]] = {}
    today = _date.today()

    for child in children:
        rid = child.get("room_id")
        if not rid:
            continue
        dob_str = child.get("date_of_birth")
        age_m: int | None = None
        if dob_str:
            try:
                dob   = _date.fromisoformat(str(dob_str)[:10])
                months = (today.year - dob.year) * 12 + (today.month - dob.month)
                if today.day < dob.day:
                    months -= 1
                age_m = max(0, months)
            except Exception:
                pass

        result.setdefault(rid, []).append(age_m)

    return result


def _find_receiver_rooms(
    over_rid: str,
    over_min: int,
    over_max: int,
    room_map: dict[str, dict],
    room_spare: dict[str, int],
    children_by_room: dict[str, list[int | None]],
    has_age_data: bool,
) -> list[dict]:
    """
    Return candidate receiver rooms sorted by suitability.

    Each candidate dict:
        room_id, spare, age_compatible (bool|None), reason
    """
    candidates = []

    for rid, room in room_map.items():
        if rid == over_rid:
            continue
        spare = room_spare.get(rid, 0)
        if spare <= 0:
            continue

        r_min = room.get("age_min_months", 0)
        r_max = room.get("age_max_months", 999)

        # Age compatibility check
        if not has_age_data:
            # No individual DOBs — we can only do room-level band overlap
            # Bands that completely don't overlap are definitely incompatible.
            bands_overlap = not (over_max < r_min or over_min > r_max)
            age_compatible = None   # unknown at individual level
            if not bands_overlap:
                continue            # hard incompatibility — skip

            reason = (
                f"Spare capacity {spare}. "
                f"Age bands partially overlap ({over_min}–{over_max}m / "
                f"{r_min}–{r_max}m). Manual review required."
            )
        else:
            # We have individual child ages — check if any overflow children
            # (from the source room) would fit in this receiver room.
            source_ages = children_by_room.get(over_rid, [])
            if source_ages:
                compatible_count = sum(
                    1 for a in source_ages
                    if a is not None and r_min <= a <= r_max
                )
                if compatible_count == 0:
                    continue   # no children from this room fit here
                age_compatible = True
                reason = (
                    f"{compatible_count} of {len(source_ages)} child(ren) in "
                    f"age range for {room.get('name','')} ({r_min}–{r_max}m). "
                    f"Spare capacity {spare}."
                )
            else:
                age_compatible = None
                reason = f"Spare capacity {spare}. No age data for source room."

        candidates.append({
            "room_id":       rid,
            "spare":         spare,
            "age_compatible": age_compatible,
            "reason":        reason,
        })

    # Sort: age-compatible=True first, then by spare capacity descending
    def _sort_key(c: dict) -> tuple:
        compat_sort = 0 if c["age_compatible"] is True else (1 if c["age_compatible"] is None else 2)
        return (compat_sort, -c["spare"])

    return sorted(candidates, key=_sort_key)
