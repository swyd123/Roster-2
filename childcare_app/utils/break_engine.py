# utils/break_engine.py
# ─────────────────────────────────────────────────────────────────────────────
# Pure-Python break calculation engine.
# No database calls — all inputs come from the caller.
#
# Australian Children's Services Award 2010 break entitlements:
#   Shift ≥ 4 hours  → 1 × 10-min paid rest break
#   Shift ≥ 5 hours  → 1 × 30-min unpaid meal break  (+ the rest break)
#   Shift ≥ 8 hours  → 2 × rest breaks + 1 meal break
#
# Staff on break are NOT counted in ratio calculations.
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations
from datetime import datetime, time, date, timedelta
from typing import Optional
import math


# ── Break status labels & colours ────────────────────────────────────────────

BREAK_STATUS_CONFIG = {
    "scheduled": {
        "label": "Scheduled",
        "icon":  "📅",
        "bg":    "#eff6ff",
        "text":  "#1d4ed8",
        "border":"#bfdbfe",
    },
    "in_progress": {
        "label": "In Progress",
        "icon":  "☕",
        "bg":    "#fffbeb",
        "text":  "#92400e",
        "border":"#fcd34d",
    },
    "completed": {
        "label": "Completed",
        "icon":  "✅",
        "bg":    "#f0fdf4",
        "text":  "#14532d",
        "border":"#86efac",
    },
    "missed": {
        "label": "Missed",
        "icon":  "⚠️",
        "bg":    "#fff1f2",
        "text":  "#881337",
        "border":"#fca5a5",
    },
    "rescheduled": {
        "label": "Rescheduled",
        "icon":  "🔄",
        "bg":    "#f5f3ff",
        "text":  "#5b21b6",
        "border":"#c4b5fd",
    },
    "not_yet_due": {
        "label": "Not yet due",
        "icon":  "⏳",
        "bg":    "#f8fafc",
        "text":  "#64748b",
        "border":"#e2e8f0",
    },
}

BREAK_TYPE_LABELS = {
    "meal": "Meal Break",
    "rest": "Rest Break",
}


# ── Award entitlement calculation ─────────────────────────────────────────────

def shift_duration_minutes(start_str: str, end_str: str) -> int:
    """Minutes between two HH:MM or HH:MM:SS strings."""
    try:
        s = datetime.strptime(start_str[:5], "%H:%M")
        e = datetime.strptime(end_str[:5],   "%H:%M")
        diff = int((e - s).total_seconds() / 60)
        return max(0, diff)
    except Exception:
        return 0


def calc_break_entitlement(shift_minutes: int) -> dict:
    """
    Calculate the break entitlement for a given shift length.

    Returns:
        meal_breaks     — number of unpaid meal breaks (30 min each)
        rest_breaks     — number of paid rest breaks (10 min each)
        total_paid_min  — total paid break minutes
        total_unpaid_min— total unpaid break minutes
        total_min       — total break minutes
        summary         — human-readable description
    """
    meal_breaks = 0
    rest_breaks = 0

    if shift_minutes >= 8 * 60:
        meal_breaks = 1
        rest_breaks = 2
    elif shift_minutes >= 5 * 60:
        meal_breaks = 1
        rest_breaks = 1
    elif shift_minutes >= 4 * 60:
        meal_breaks = 0
        rest_breaks = 1
    # Under 4 hours: no entitlement

    total_paid_min   = rest_breaks * 10
    total_unpaid_min = meal_breaks * 30
    total_min        = total_paid_min + total_unpaid_min

    parts = []
    if rest_breaks:
        parts.append(f"{rest_breaks} × 10min rest")
    if meal_breaks:
        parts.append(f"{meal_breaks} × 30min meal")

    summary = " + ".join(parts) if parts else "No break entitlement"

    return {
        "meal_breaks":      meal_breaks,
        "rest_breaks":      rest_breaks,
        "total_paid_min":   total_paid_min,
        "total_unpaid_min": total_unpaid_min,
        "total_min":        total_min,
        "summary":          summary,
    }


def suggest_break_times(
    shift_start: str,
    shift_end: str,
    entitlement: dict,
) -> list[dict]:
    """
    Suggest ideal break times spread across the shift.
    Meal break is placed at the midpoint.
    Rest breaks are at the 1/3 and 2/3 points.

    Returns list of {break_type, planned_start, planned_end, duration_minutes}.
    """
    suggestions = []
    shift_mins  = shift_duration_minutes(shift_start, shift_end)
    if shift_mins <= 0:
        return suggestions

    try:
        start_dt = datetime.strptime(shift_start[:5], "%H:%M")
    except Exception:
        return suggestions

    n_meal = entitlement["meal_breaks"]
    n_rest = entitlement["rest_breaks"]

    # Place rest breaks evenly across shift
    if n_rest == 1:
        # Single rest break: 40% into shift
        offset = int(shift_mins * 0.40)
        t      = start_dt + timedelta(minutes=offset)
        suggestions.append({
            "break_type":        "rest",
            "planned_start":     t.strftime("%H:%M:%S"),
            "planned_end":       (t + timedelta(minutes=10)).strftime("%H:%M:%S"),
            "duration_minutes":  10,
        })
    elif n_rest == 2:
        for frac in [0.25, 0.65]:
            offset = int(shift_mins * frac)
            t      = start_dt + timedelta(minutes=offset)
            suggestions.append({
                "break_type":        "rest",
                "planned_start":     t.strftime("%H:%M:%S"),
                "planned_end":       (t + timedelta(minutes=10)).strftime("%H:%M:%S"),
                "duration_minutes":  10,
            })

    # Meal break at midpoint (push slightly past middle so rest comes first)
    if n_meal >= 1:
        offset = int(shift_mins * 0.52)
        t      = start_dt + timedelta(minutes=offset)
        suggestions.append({
            "break_type":        "meal",
            "planned_start":     t.strftime("%H:%M:%S"),
            "planned_end":       (t + timedelta(minutes=30)).strftime("%H:%M:%S"),
            "duration_minutes":  30,
        })

    return suggestions


# ── Live status derivation ────────────────────────────────────────────────────

def derive_break_status(
    break_record: dict,
    now_str: str,
) -> str:
    """
    Derive the live status of a break based on current time.
    Overrides the stored status if the break is now in progress or overdue.
    """
    stored_status   = break_record.get("status", "scheduled")
    planned_start   = (break_record.get("planned_start_time") or "")[:5]
    planned_end     = (break_record.get("planned_end_time") or "")[:5]
    actual_start    = break_record.get("actual_start_time")
    actual_end      = break_record.get("actual_end_time")
    now_5           = now_str[:5]

    # Already completed with actuals
    if stored_status == "completed" and actual_start and actual_end:
        return "completed"

    # Marked missed
    if stored_status == "missed":
        return "missed"

    # Rescheduled
    if stored_status == "rescheduled":
        return "rescheduled"

    # Break window has passed and not taken
    if planned_end < now_5 and stored_status == "scheduled":
        return "missed"

    # Break window is active
    if planned_start <= now_5 <= planned_end:
        if actual_start and not actual_end:
            return "in_progress"
        if not actual_start:
            return "in_progress"   # Should have started

    # Not yet due
    if planned_start > now_5:
        return "not_yet_due"

    return stored_status


def is_break_overdue(break_record: dict, now_str: str) -> bool:
    """True if break was due but hasn't been taken."""
    status = derive_break_status(break_record, now_str)
    return status in ("missed",)


# ── Compliance summary ────────────────────────────────────────────────────────

def compliance_summary(
    breaks: list[dict],
    entitlement: dict,
) -> dict:
    """
    Determine whether a staff member's breaks for a day meet their entitlement.

    Returns:
        compliant       — bool
        taken_minutes   — total break minutes actually taken
        entitled_minutes— what they were entitled to
        shortfall       — minutes short (0 if compliant)
        status          — "compliant" | "partial" | "missed" | "not_required"
        note            — human-readable explanation
    """
    entitled = entitlement["total_min"]

    if entitled == 0:
        return {
            "compliant": True,
            "taken_minutes":    0,
            "entitled_minutes": 0,
            "shortfall":        0,
            "status":           "not_required",
            "note":             "Shift too short for break entitlement.",
        }

    completed = [b for b in breaks if b.get("status") == "completed"]
    taken_min = sum(
        (b.get("actual_duration_minutes") or b.get("planned_duration_minutes") or 0)
        for b in completed
    )
    shortfall = max(0, entitled - taken_min)

    if taken_min == 0 and len(breaks) > 0:
        status = "missed"
        note   = f"Break not taken. Entitled to {entitled} min."
    elif shortfall > 0:
        status = "partial"
        note   = f"Took {taken_min} min of {entitled} min entitlement."
    else:
        status = "compliant"
        note   = f"Full {entitled} min break taken."

    return {
        "compliant":        shortfall == 0,
        "taken_minutes":    taken_min,
        "entitled_minutes": entitled,
        "shortfall":        shortfall,
        "status":           status,
        "note":             note,
    }


# ── Timeline rendering helpers ────────────────────────────────────────────────

def time_to_pct(t_str: str, day_start: int = 6, day_end: int = 20) -> float:
    """Convert a HH:MM time string to a % position in the day window."""
    try:
        h, m = int(t_str[:2]), int(t_str[3:5])
        minutes_from_start = (h - day_start) * 60 + m
        total_day_minutes  = (day_end - day_start) * 60
        return max(0.0, min(100.0, minutes_from_start / total_day_minutes * 100))
    except Exception:
        return 0.0


def build_gantt_bars(
    shift_start: str,
    shift_end:   str,
    breaks:      list[dict],
    day_start:   int = 6,
    day_end:     int = 20,
) -> list[dict]:
    """
    Build a list of coloured bar segments for one staff member's Gantt row.

    Each segment: {left_pct, width_pct, colour, label, type}
    Types: "shift", "break_scheduled", "break_taken", "break_missed"
    """
    segments = []

    shift_left  = time_to_pct(shift_start[:5], day_start, day_end)
    shift_right = time_to_pct(shift_end[:5],   day_start, day_end)
    shift_width = shift_right - shift_left

    # Main shift bar
    segments.append({
        "left_pct":  shift_left,
        "width_pct": shift_width,
        "colour":    "#3b82f6",   # Blue
        "border":    "#2563eb",
        "opacity":   "0.75",
        "type":      "shift",
        "label":     f"{shift_start[:5]}–{shift_end[:5]}",
        "z_index":   1,
    })

    # Break segments overlaid on top
    for b in breaks:
        ps   = (b.get("planned_start_time") or "")[:5]
        pe   = (b.get("planned_end_time")   or "")[:5]
        as_  = (b.get("actual_start_time")  or "")[:5]
        ae   = (b.get("actual_end_time")    or "")[:5]
        btype = b.get("break_type", "meal")
        status = b.get("status", "scheduled")

        # Planned break — always shown
        if ps and pe:
            left  = time_to_pct(ps, day_start, day_end)
            width = time_to_pct(pe, day_start, day_end) - left
            if width > 0:
                if status == "completed":
                    colour, border, label = "#ffffff", "#10b981", f"Break {ps}"
                elif status == "missed":
                    colour, border, label = "#fecdd3", "#f43f5e", f"Missed {ps}"
                elif status == "in_progress":
                    colour, border, label = "#fef9c3", "#eab308", f"On break"
                else:
                    colour, border, label = "#e0e7ff", "#6366f1", f"Break {ps}"

                segments.append({
                    "left_pct":  left,
                    "width_pct": width,
                    "colour":    colour,
                    "border":    border,
                    "opacity":   "1",
                    "type":      f"break_{status}",
                    "label":     label,
                    "z_index":   2,
                })

    return segments


# ── Format helpers ────────────────────────────────────────────────────────────

def fmt_duration(minutes: int | None) -> str:
    """30 → '30 min', 90 → '1h 30m', 0 → '—'."""
    if not minutes:
        return "—"
    h = minutes // 60
    m = minutes % 60
    if h == 0:
        return f"{m} min"
    if m == 0:
        return f"{h}h"
    return f"{h}h {m}m"


def fmt_time(t_str: str | None) -> str:
    """'14:30:00' → '2:30 PM'. Returns '—' for None."""
    if not t_str:
        return "—"
    try:
        return datetime.strptime(str(t_str)[:5], "%H:%M").strftime("%-I:%M %p")
    except Exception:
        return str(t_str)[:5]
