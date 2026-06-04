# utils/break_engine.py
# ─────────────────────────────────────────────────────────────────────────────
# Pure-Python break calculation engine.
# No database calls — all inputs come from the caller.
#
# Break entitlement rules (configurable — see BREAK_RULES_DEFAULT):
#   Shift < 4 hours  → no break
#   Shift 4–5 hours  → 1 × 10-min paid rest break
#   Shift 5–7 hours  → 1 × 10-min paid rest + 1 × 30-min unpaid meal
#   Shift 7+ hours   → 1 × 20-min paid rest + 1 × 30-min unpaid meal
#
# Staff on break are NOT counted in ratio calculations.
# Rules are configurable via the break_rules table (fetched by break_queries).
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations
from datetime import datetime, time, date, timedelta
from typing import Optional
import math


# ─────────────────────────────────────────────────────────────────────────────
# DEFAULT BREAK RULES (used when no DB rules are found)
# ─────────────────────────────────────────────────────────────────────────────
# Each tier: min_minutes (inclusive) to max_minutes (exclusive), break spec.
# Tiers are evaluated in ascending order; first match wins.
# paid_minutes   — total paid break minutes for this tier
# unpaid_minutes — total unpaid meal break minutes for this tier
# paid_count     — number of distinct paid break slots (affects scheduling)
# paid_duration  — minutes per paid break slot

BREAK_RULES_DEFAULT: list[dict] = [
    {
        "min_hours":      0,
        "max_hours":      4,       # < 4 hours
        "paid_minutes":   0,
        "unpaid_minutes": 0,
        "paid_count":     0,
        "paid_duration":  0,
        "label":          "No break entitlement",
    },
    {
        "min_hours":      4,
        "max_hours":      5,       # 4h ≤ shift < 5h
        "paid_minutes":   10,
        "unpaid_minutes": 0,
        "paid_count":     1,
        "paid_duration":  10,
        "label":          "1 × 10-min paid rest",
    },
    {
        "min_hours":      5,
        "max_hours":      7,       # 5h ≤ shift < 7h
        "paid_minutes":   10,
        "unpaid_minutes": 30,
        "paid_count":     1,
        "paid_duration":  10,
        "label":          "1 × 10-min paid rest + 1 × 30-min unpaid meal",
    },
    {
        "min_hours":      7,
        "max_hours":      999,     # 7h+
        "paid_minutes":   20,
        "unpaid_minutes": 30,
        "paid_count":     1,
        "paid_duration":  20,
        "label":          "1 × 20-min paid rest + 1 × 30-min unpaid meal",
    },
]


# ── Break status labels & colours ─────────────────────────────────────────────

BREAK_STATUS_CONFIG = {
    "scheduled": {
        "label":  "Scheduled",
        "icon":   "📅",
        "bg":     "#eff6ff",
        "text":   "#1d4ed8",
        "border": "#bfdbfe",
    },
    "in_progress": {
        "label":  "In Progress",
        "icon":   "☕",
        "bg":     "#fffbeb",
        "text":   "#92400e",
        "border": "#fcd34d",
    },
    "completed": {
        "label":  "Completed",
        "icon":   "✅",
        "bg":     "#f0fdf4",
        "text":   "#14532d",
        "border": "#86efac",
    },
    "missed": {
        "label":  "Missed",
        "icon":   "⚠️",
        "bg":     "#fff1f2",
        "text":   "#881337",
        "border": "#fca5a5",
    },
    "rescheduled": {
        "label":  "Rescheduled",
        "icon":   "🔄",
        "bg":     "#f5f3ff",
        "text":   "#5b21b6",
        "border": "#c4b5fd",
    },
    "not_yet_due": {
        "label":  "Not yet due",
        "icon":   "⏳",
        "bg":     "#f8fafc",
        "text":   "#64748b",
        "border": "#e2e8f0",
    },
    # New statuses for break schedule table
    "ratio_conflict": {
        "label":  "Ratio conflict",
        "icon":   "❌",
        "bg":     "#fff1f2",
        "text":   "#991b1b",
        "border": "#fca5a5",
    },
    "manual_review": {
        "label":  "Manual review required",
        "icon":   "🔍",
        "bg":     "#fffbeb",
        "text":   "#92400e",
        "border": "#fcd34d",
    },
}

BREAK_TYPE_LABELS = {
    "meal": "Meal Break (unpaid)",
    "rest": "Rest Break (paid)",
}


# ─────────────────────────────────────────────────────────────────────────────
# ENTITLEMENT CALCULATION
# ─────────────────────────────────────────────────────────────────────────────

def shift_duration_minutes(start_str: str, end_str: str) -> int:
    """Minutes between two HH:MM or HH:MM:SS strings."""
    try:
        s    = datetime.strptime(start_str[:5], "%H:%M")
        e    = datetime.strptime(end_str[:5],   "%H:%M")
        diff = int((e - s).total_seconds() / 60)
        return max(0, diff)
    except Exception:
        return 0


def _match_tier(shift_minutes: int, rules: list[dict]) -> dict:
    """Find the first matching tier for the given shift length."""
    shift_hours = shift_minutes / 60
    for tier in sorted(rules, key=lambda t: t["min_hours"]):
        if tier["min_hours"] <= shift_hours < tier["max_hours"]:
            return tier
    # Fallback: last tier (covers unbounded top)
    return rules[-1] if rules else BREAK_RULES_DEFAULT[-1]


def calc_break_entitlement(
    shift_minutes: int,
    rules: list[dict] | None = None,
    unpaid_opted_out: bool = False,
) -> dict:
    """
    Calculate break entitlement for a given shift length using configurable rules.

    Parameters
    ----------
    shift_minutes     Total shift duration in minutes.
    rules             Optional list of rule tier dicts. None → BREAK_RULES_DEFAULT.
    unpaid_opted_out  When True, the 30-minute unpaid meal break is removed.
                      Paid rest break(s) are always retained regardless of this flag.
                      Only applies when the staff profile allows_unpaid_break_opt_out
                      AND the shift has unpaid_break_opted_out set.

    Returns
    -------
    dict with keys:
        paid_minutes, unpaid_minutes, paid_count, paid_duration,
        total_min, has_meal, has_rest, summary, tier,
        unpaid_opted_out (bool — reflects the input flag),
        meal_breaks, rest_breaks, total_paid_min, total_unpaid_min (legacy aliases)
    """
    active_rules = rules if rules else BREAK_RULES_DEFAULT
    tier         = _match_tier(shift_minutes, active_rules)

    paid_min    = tier.get("paid_minutes",   0)
    unpaid_min  = tier.get("unpaid_minutes", 0)
    paid_count  = tier.get("paid_count",     0)
    paid_dur    = tier.get("paid_duration",  0)

    # Apply opt-out: remove unpaid meal break only; paid rest is always kept
    if unpaid_opted_out and unpaid_min > 0:
        unpaid_min = 0

    total_min   = paid_min + unpaid_min
    has_meal    = unpaid_min > 0
    has_rest    = paid_min > 0

    if unpaid_opted_out and tier.get("unpaid_minutes", 0) > 0:
        summary = f"{tier.get('label','')} — unpaid break opted out · paid rest still required"
    else:
        summary = tier.get("label", "No break entitlement")

    return {
        "paid_minutes":      paid_min,
        "unpaid_minutes":    unpaid_min,
        "paid_count":        paid_count,
        "paid_duration":     paid_dur,
        "total_min":         total_min,
        "has_meal":          has_meal,
        "has_rest":          has_rest,
        "summary":           summary,
        "tier":              tier,
        "unpaid_opted_out":  unpaid_opted_out,
        # Legacy aliases
        "meal_breaks":       1 if has_meal else 0,
        "rest_breaks":       paid_count,
        "total_paid_min":    paid_min,
        "total_unpaid_min":  unpaid_min,
    }


# ─────────────────────────────────────────────────────────────────────────────
# BREAK SCHEDULING
# ─────────────────────────────────────────────────────────────────────────────

def suggest_break_times(
    shift_start: str,
    shift_end: str,
    entitlement: dict,
) -> list[dict]:
    """
    Suggest ideal break times spread across the shift.

    Placement strategy:
        Single rest break (10m or 20m) — at 40% of shift
        Meal break (30m) — at 52% of shift (after rest)
        Two rest breaks — at 25% and 65% of shift (legacy rule support)

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

    paid_count  = entitlement.get("paid_count",    0)
    paid_dur    = entitlement.get("paid_duration",  10)
    has_meal    = entitlement.get("has_meal",       False)

    # Place paid rest break(s)
    if paid_count == 1:
        offset = int(shift_mins * 0.40)
        t      = start_dt + timedelta(minutes=offset)
        suggestions.append({
            "break_type":       "rest",
            "planned_start":    t.strftime("%H:%M:%S"),
            "planned_end":      (t + timedelta(minutes=paid_dur)).strftime("%H:%M:%S"),
            "duration_minutes": paid_dur,
        })
    elif paid_count == 2:
        for frac in [0.25, 0.65]:
            offset = int(shift_mins * frac)
            t      = start_dt + timedelta(minutes=offset)
            suggestions.append({
                "break_type":       "rest",
                "planned_start":    t.strftime("%H:%M:%S"),
                "planned_end":      (t + timedelta(minutes=paid_dur)).strftime("%H:%M:%S"),
                "duration_minutes": paid_dur,
            })

    # Meal break (unpaid, 30m) — past midpoint so rest comes first
    if has_meal:
        offset = int(shift_mins * 0.52)
        t      = start_dt + timedelta(minutes=offset)
        suggestions.append({
            "break_type":       "meal",
            "planned_start":    t.strftime("%H:%M:%S"),
            "planned_end":      (t + timedelta(minutes=30)).strftime("%H:%M:%S"),
            "duration_minutes": 30,
        })

    return suggestions


def generate_break_recommendations(
    shifts: list[dict],
    existing_breaks: list[dict],
    rooms: list[dict],
    rules: list[dict] | None = None,
    staff_prefs: dict[str, dict[int, bool]] | None = None,
) -> list[dict]:
    """
    Generate break recommendations for all shifts, checking for ratio conflicts.

    Parameters
    ----------
    shifts          List of enriched shift dicts from fetch_shifts_for_period().
    existing_breaks Scheduled break records from fetch_breaks_today().
    rooms           Room dicts from fetch_rooms().
    rules           Optional break rule tiers (None → BREAK_RULES_DEFAULT).
    staff_prefs     {user_id: {day_of_week: unpaid_break_opt_out}} loaded from
                    fetch_break_prefs_for_centre(). When None, defaults to {}.

    Each recommendation dict:
        user_id, user_name, shift_id, room_id, room_name,
        shift_start, shift_end, shift_minutes,
        entitlement (dict), suggestions (list[dict]),
        schedule_status ("scheduled"|"ratio_conflict"|"manual_review"|"no_entitlement"),
        status_reason (str),
        unpaid_opted_out (bool),
        opt_out_source   ("Staff default"|"Manual override — opted out"|
                          "Manual override — not opted out"|"No opt-out")
    """
    room_map    = {r["id"]: r for r in rooms}
    breaks_by_uid: dict[str, list] = {}
    for b in existing_breaks:
        uid = b.get("user_id", "")
        breaks_by_uid.setdefault(uid, []).append(b)

    if staff_prefs is None:
        staff_prefs = {}

    # Build staff-per-room count at each 15-min slot (for ratio checking)
    room_staff_counts = _build_room_staff_counts(shifts)

    recommendations = []

    # Prioritise educators finishing earliest — they need their breaks
    # scheduled before the end of their shift, so they come first.
    for shift in sorted(shifts, key=lambda s: (s.get("end_time") or "99:99")):
        u       = shift.get("users") or {}
        uid     = shift.get("user_id", "")
        rid     = shift.get("room_id", "")
        ss      = (shift.get("start_time") or "")[:5]
        se      = (shift.get("end_time")   or "")[:5]
        sname   = f"{u.get('first_name','')} {u.get('last_name','')}".strip()
        room_d  = room_map.get(rid, {})

        if not ss or not se:
            continue

        # ── Resolve unpaid break opt-out (three-way override) ─────────
        # override column: 'use_staff_default' | 'opted_out' | 'not_opted_out'
        # staff_prefs: {user_id: {day_of_week: bool}} passed in by caller
        unpaid_opted_out, opt_out_source = resolve_opt_out(shift, staff_prefs)

        dur_mins = shift_duration_minutes(ss, se)
        ent      = calc_break_entitlement(dur_mins, rules, unpaid_opted_out=unpaid_opted_out)

        if ent["total_min"] == 0:
            recommendations.append({
                "user_id":         uid,
                "user_name":       sname,
                "shift_id":        shift.get("id", ""),
                "room_id":         rid,
                "room_name":       room_d.get("name", ""),
                "shift_start":     ss,
                "shift_end":       se,
                "shift_minutes":   dur_mins,
                "entitlement":     ent,
                "suggestions":     [],
                "schedule_status": "no_entitlement",
                "status_reason":   "Shift under 4 hours — no break required.",
                "unpaid_opted_out":  unpaid_opted_out,
                "opt_out_source":    opt_out_source,
            })
            continue

        suggestions = suggest_break_times(ss + ":00", se + ":00", ent)

        # Check ratio impact for each suggested break
        ratio_conflict  = False
        manual_review   = False
        conflict_reason = ""

        for sug in suggestions:
            conflict, reason = _check_break_ratio_conflict(
                sug, rid, uid, room_d, room_staff_counts
            )
            if conflict == "breach":
                ratio_conflict  = True
                conflict_reason = reason
                break
            elif conflict == "warning":
                manual_review   = True
                conflict_reason = reason

        if ratio_conflict:
            status        = "ratio_conflict"
            status_reason = conflict_reason or "Break causes ratio breach."
        elif manual_review:
            status        = "manual_review"
            status_reason = conflict_reason or "Break causes ratio warning — review manually."
        else:
            status        = "scheduled"
            status_reason = f"Breaks fit within ratio limits. {ent['summary']}."

        recommendations.append({
            "user_id":         uid,
            "user_name":       sname,
            "shift_id":        shift.get("id", ""),
            "room_id":         rid,
            "room_name":       room_d.get("name", ""),
            "shift_start":     ss,
            "shift_end":       se,
            "shift_minutes":   dur_mins,
            "entitlement":     ent,
            "suggestions":     suggestions,
            "schedule_status": status,
            "status_reason":   status_reason,
            "unpaid_opted_out":  unpaid_opted_out,
            "opt_out_source":    opt_out_source,
        })

    return recommendations


# ─────────────────────────────────────────────────────────────────────────────
# LIVE STATUS DERIVATION
# ─────────────────────────────────────────────────────────────────────────────

def derive_break_status(
    break_record: dict,
    now_str: str,
) -> str:
    """
    Derive the live status of a break based on current time.
    Overrides stored status if the break is now in progress or overdue.
    """
    stored_status = break_record.get("status", "scheduled")
    planned_start = (break_record.get("planned_start_time") or "")[:5]
    planned_end   = (break_record.get("planned_end_time")   or "")[:5]
    actual_start  = break_record.get("actual_start_time")
    actual_end    = break_record.get("actual_end_time")
    now_5         = now_str[:5]

    if stored_status == "completed" and actual_start and actual_end:
        return "completed"
    if stored_status == "missed":
        return "missed"
    if stored_status == "rescheduled":
        return "rescheduled"
    if stored_status in ("ratio_conflict", "manual_review"):
        return stored_status

    if planned_end and now_5 and planned_end < now_5 and stored_status == "scheduled":
        return "missed"
    if planned_start and planned_end and planned_start <= now_5 <= planned_end:
        if actual_start and not actual_end:
            return "in_progress"
        if not actual_start:
            return "in_progress"
    if planned_start and planned_start > now_5:
        return "not_yet_due"

    return stored_status


def is_break_overdue(break_record: dict, now_str: str) -> bool:
    return derive_break_status(break_record, now_str) in ("missed",)


# ─────────────────────────────────────────────────────────────────────────────
# COMPLIANCE SUMMARY
# ─────────────────────────────────────────────────────────────────────────────

def compliance_summary(
    breaks: list[dict],
    entitlement: dict,
) -> dict:
    """
    Determine whether a staff member's breaks meet their entitlement.
    """
    entitled = entitlement["total_min"]

    if entitled == 0:
        return {
            "compliant":        True,
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


def break_schedule_summary(
    recommendations: list[dict],
    existing_breaks: list[dict],
) -> dict:
    """
    Compute the break schedule summary metrics for the top-of-page banner.

    Returns:
        total_paid_breaks      int
        total_unpaid_breaks    int
        unresolved_conflicts   int  — ratio_conflict + manual_review
        scheduled_ok           int
        no_entitlement         int
    """
    total_paid    = 0
    total_unpaid  = 0
    unresolved    = 0
    scheduled_ok  = 0
    no_ent        = 0

    for rec in recommendations:
        ent    = rec.get("entitlement", {})
        status = rec.get("schedule_status", "")

        if status == "no_entitlement":
            no_ent += 1
            continue

        if status in ("ratio_conflict", "manual_review"):
            unresolved += 1
        else:
            scheduled_ok += 1

        if ent.get("has_rest"):
            total_paid += 1
        if ent.get("has_meal"):
            total_unpaid += 1

    return {
        "total_paid_breaks":    total_paid,
        "total_unpaid_breaks":  total_unpaid,
        "unresolved_conflicts": unresolved,
        "scheduled_ok":         scheduled_ok,
        "no_entitlement":       no_ent,
    }


# ─────────────────────────────────────────────────────────────────────────────
# TIMELINE RENDERING HELPERS
# ─────────────────────────────────────────────────────────────────────────────

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
    Each segment: {left_pct, width_pct, colour, border, opacity, type, label, z_index}
    """
    segments = []

    shift_left  = time_to_pct(shift_start[:5], day_start, day_end)
    shift_right = time_to_pct(shift_end[:5],   day_start, day_end)
    shift_width = shift_right - shift_left

    segments.append({
        "left_pct":  shift_left,
        "width_pct": shift_width,
        "colour":    "#3b82f6",
        "border":    "#2563eb",
        "opacity":   "0.75",
        "type":      "shift",
        "label":     f"{shift_start[:5]}–{shift_end[:5]}",
        "z_index":   1,
    })

    for b in breaks:
        ps    = (b.get("planned_start_time") or "")[:5]
        pe    = (b.get("planned_end_time")   or "")[:5]
        btype  = b.get("break_type", "meal")
        status = b.get("status", "scheduled")

        if ps and pe:
            left  = time_to_pct(ps, day_start, day_end)
            width = time_to_pct(pe, day_start, day_end) - left
            if width > 0:
                if status == "completed":
                    colour, border, label = "#ffffff", "#10b981", f"Break {ps}"
                elif status == "missed":
                    colour, border, label = "#fecdd3", "#f43f5e", f"Missed {ps}"
                elif status == "in_progress":
                    colour, border, label = "#fef9c3", "#eab308", "On break"
                elif status == "ratio_conflict":
                    colour, border, label = "#fee2e2", "#ef4444", f"❌ Conflict {ps}"
                elif status == "manual_review":
                    colour, border, label = "#fef3c7", "#f59e0b", f"🔍 Review {ps}"
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


# ─────────────────────────────────────────────────────────────────────────────
# FORMAT HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def fmt_duration(minutes: int | None) -> str:
    """30 → '30 min', 90 → '1h 30m', 0 → '—'."""
    if not minutes:
        return "—"
    h = minutes // 60
    m = minutes % 60
    if h == 0: return f"{m} min"
    if m == 0: return f"{h}h"
    return f"{h}h {m}m"


def fmt_time(t_str: str | None) -> str:
    """'14:30:00' → '2:30 PM'. Returns '—' for None."""
    if not t_str:
        return "—"
    try:
        return datetime.strptime(str(t_str)[:5], "%H:%M").strftime("%-I:%M %p")
    except Exception:
        return str(t_str)[:5]


# ─────────────────────────────────────────────────────────────────────────────
# OPT-OUT RESOLUTION
# ─────────────────────────────────────────────────────────────────────────────

def resolve_opt_out(
    shift: dict,
    staff_prefs: dict[str, dict[int, bool]],
) -> tuple[bool, str]:
    """
    Determine whether a shift's unpaid meal break is opted out, and why.

    Logic (in priority order):
        1. If shift.unpaid_break_opt_out_override == 'opted_out'     → True,  "Manual override — opted out"
        2. If shift.unpaid_break_opt_out_override == 'not_opted_out' → False, "Manual override — not opted out"
        3. If shift.unpaid_break_opt_out_override == 'use_staff_default' (or missing):
              Look up staff_prefs[user_id][day_of_week].
              If found and True  → True,  "Staff default"
              If found and False → False, "Staff default"
              If not found       → False, "No opt-out"

    The profile-level allows_unpaid_break_opt_out flag is a prerequisite
    stored on the staff profile and checked by the UI before showing the
    controls; the engine trusts the override value stored on the shift.

    Parameters
    ----------
    shift       Shift dict — needs user_id, shift_date,
                unpaid_break_opt_out_override (may be absent → default).
    staff_prefs {user_id: {day_of_week: bool}} — loaded once per page render
                via fetch_break_prefs_for_centre().

    Returns
    -------
    (opted_out: bool, source: str)
    """
    override = shift.get("unpaid_break_opt_out_override", "use_staff_default") or "use_staff_default"

    if override == "opted_out":
        return True, "Manual override — opted out"

    if override == "not_opted_out":
        return False, "Manual override — not opted out"

    # use_staff_default: look up weekly preference
    uid        = shift.get("user_id", "")
    shift_date = shift.get("shift_date", "")
    dow        = _shift_day_of_week(shift_date)

    user_prefs = staff_prefs.get(uid, {})
    if dow in user_prefs:
        return user_prefs[dow], "Staff default"

    return False, "No opt-out"


def _shift_day_of_week(shift_date: str) -> int:
    """
    Convert a shift_date ISO string to day_of_week int.
    Returns Python date.isoweekday() % 7: Mon=1 … Sat=6, Sun=0.
    Returns -1 on parse failure.
    """
    try:
        from datetime import date as _date
        d = _date.fromisoformat(shift_date[:10])
        return d.isoweekday() % 7
    except Exception:
        return -1


# ─────────────────────────────────────────────────────────────────────────────
# PRIVATE — ratio impact helpers
# ─────────────────────────────────────────────────────────────────────────────

def _build_room_staff_counts(shifts: list[dict]) -> dict[str, dict[str, int]]:
    """
    Build {room_id: {hhmm_slot: staff_count}} for 15-min slots across the day.
    Used to check ratio impact when inserting a break.
    """
    from datetime import datetime as _dt

    result: dict[str, dict[str, int]] = {}
    slots = [
        f"{h:02d}:{m:02d}"
        for h in range(6, 21)
        for m in (0, 15, 30, 45)
    ]

    for shift in shifts:
        rid = shift.get("room_id", "")
        ss  = (shift.get("start_time") or "")[:5]
        se  = (shift.get("end_time")   or "")[:5]
        if not rid or not ss or not se:
            continue
        if rid not in result:
            result[rid] = {slot: 0 for slot in slots}
        for slot in slots:
            if ss <= slot < se:
                result[rid][slot] = result[rid].get(slot, 0) + 1

    return result


def _check_break_ratio_conflict(
    suggestion: dict,
    room_id: str,
    user_id: str,
    room_cfg: dict,
    room_staff_counts: dict[str, dict[str, int]],
) -> tuple[str, str]:
    """
    Check if a suggested break would cause a ratio breach.

    Returns ("breach"|"warning"|"ok", reason_str)
    """
    if not room_id or not room_cfg:
        return ("ok", "")

    r_staff    = room_cfg.get("required_ratio_staff",    1)
    r_children = room_cfg.get("required_ratio_children", 4)
    capacity   = room_cfg.get("licensed_capacity",       0)
    rname      = room_cfg.get("name", "")

    ps  = (suggestion.get("planned_start") or "")[:5]
    pe  = (suggestion.get("planned_end")   or "")[:5]

    slots_map = room_staff_counts.get(room_id, {})
    worst     = "ok"
    reason    = ""

    # Check every 15-min slot covered by the break
    slots_in_break = [s for s in slots_map if ps <= s < pe]
    for slot in slots_in_break:
        staff_at_slot = slots_map.get(slot, 0)
        # Remove this staff member during the break
        staff_during  = max(0, staff_at_slot - 1)

        # We don't have children count here — use a ratio-only check
        # If staff drops to 0 and there are normally staff, flag it
        if staff_at_slot > 0 and staff_during == 0:
            worst  = "breach"
            reason = (
                f"Break at {ps} leaves {rname} with 0 staff in slot {slot}."
            )
            break
        elif r_children > 0 and staff_at_slot > 0:
            # Minimum staff needed based on ratio (rough — no child count)
            # If removing one staff drops below 1, that's a breach
            if staff_during < r_staff and staff_at_slot >= r_staff:
                if worst != "breach":
                    worst  = "warning"
                    reason = (
                        f"Break at {ps} reduces {rname} to {staff_during} staff "
                        f"(ratio requires {r_staff}). Manual review recommended."
                    )

    return (worst, reason)
