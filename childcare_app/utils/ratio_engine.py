# utils/ratio_engine.py
# ─────────────────────────────────────────────────────────────────────────────
# Pure-Python ratio calculation engine.
# No database calls in this file — all inputs come from the caller.
# This separation means every calculation can be unit-tested without a DB.
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations
import math
from datetime import date, time, datetime, timedelta


# ── Status constants ──────────────────────────────────────────────────────────
STATUS_COMPLIANT = "compliant"
STATUS_WARNING   = "warning"
STATUS_BREACH    = "breach"
STATUS_EMPTY     = "empty"

STATUS_CONFIG = {
    STATUS_COMPLIANT: {
        "icon": "✅", "label": "Compliant",
        "bg": "#f0fdf4", "text": "#14532d",
        "border": "#86efac", "badge_bg": "#dcfce7", "badge_text": "#166534",
    },
    STATUS_WARNING: {
        "icon": "⚠️", "label": "At Limit",
        "bg": "#fffbeb", "text": "#78350f",
        "border": "#fcd34d", "badge_bg": "#fef3c7", "badge_text": "#92400e",
    },
    STATUS_BREACH: {
        "icon": "❌", "label": "Ratio Breach",
        "bg": "#fff1f2", "text": "#881337",
        "border": "#fca5a5", "badge_bg": "#fee2e2", "badge_text": "#991b1b",
    },
    STATUS_EMPTY: {
        "icon": "⚪", "label": "No Children",
        "bg": "#f8fafc", "text": "#64748b",
        "border": "#e2e8f0", "badge_bg": "#f1f5f9", "badge_text": "#475569",
    },
}


# ── Core calculation ──────────────────────────────────────────────────────────

def compute_ratio(
    children: int,
    staff: int,
    ratio_staff: int,
    ratio_children: int,
    capacity: int,
) -> dict:
    """
    Central ratio calculation. Returns a rich status dict.

    Example: children=7, staff=2, ratio_staff=1, ratio_children=4, capacity=12
      → min_staff=2 (ceil(7/4)*1), surplus=0, status="warning"
        because ceil((7+1)/4)*1 = 2 which equals current staff.

    Returns:
        status          — "compliant" | "warning" | "breach" | "empty"
        min_staff       — minimum educators required right now
        min_for_one_more— min needed if one more child arrives
        surplus         — staff above minimum (negative = shortfall)
        shortfall       — abs(surplus) when in breach, else 0
        capacity_pct    — children as % of licensed capacity
        spaces_free     — capacity - children
        ratio_str       — human-readable current ratio e.g. "1 : 3.5"
        required_str    — required ratio e.g. "1 : 4"
        config          — STATUS_CONFIG entry for colours/icons
    """
    if children == 0:
        cfg = STATUS_CONFIG[STATUS_EMPTY]
        return {
            "status": STATUS_EMPTY,
            "min_staff": 0, "min_for_one_more": ratio_staff,
            "surplus": staff, "shortfall": 0,
            "capacity_pct": 0, "spaces_free": capacity,
            "ratio_str": "—", "required_str": f"1 : {ratio_children}",
            "config": cfg,
        }

    min_staff        = math.ceil(children / ratio_children) * ratio_staff
    min_for_one_more = math.ceil((children + 1) / ratio_children) * ratio_staff
    surplus          = staff - min_staff
    capacity_pct     = round((children / capacity) * 100) if capacity > 0 else 0
    spaces_free      = max(0, capacity - children)

    ratio_str = f"1 : {children/staff:.1f}" if staff > 0 else "No staff"

    if surplus < 0:
        status = STATUS_BREACH
    elif min_for_one_more > staff:
        status = STATUS_WARNING
    else:
        status = STATUS_COMPLIANT

    cfg = STATUS_CONFIG[status]
    return {
        "status":           status,
        "min_staff":        min_staff,
        "min_for_one_more": min_for_one_more,
        "surplus":          surplus,
        "shortfall":        abs(surplus) if surplus < 0 else 0,
        "capacity_pct":     capacity_pct,
        "spaces_free":      spaces_free,
        "ratio_str":        ratio_str,
        "required_str":     f"1 : {ratio_children}",
        "config":           cfg,
    }


# ── Hourly timeline ───────────────────────────────────────────────────────────

def build_hourly_timeline(
    shifts: list[dict],
    n_children: int,
    ratio_staff: int,
    ratio_children: int,
    capacity: int,
    operating_start: int = 6,
    operating_end: int = 19,
) -> list[dict]:
    """
    Build a list of hourly slots with their ratio status.

    Each slot dict:
        hour        — int (6 = 6:00 AM)
        label       — "6:00 AM"
        n_staff     — educators rostered at this hour
        n_children  — children count passed in (constant for timeline)
        result      — compute_ratio() result
        is_now      — True if this is the current hour
    """
    now_hour = datetime.now().hour
    slots    = []

    for hour in range(operating_start, operating_end + 1):
        hstr    = f"{hour:02d}:00:00"
        n_staff = sum(
            1 for s in shifts
            if (s.get("start_time") or "00:00:00") <= hstr
            <= (s.get("end_time") or "23:59:59")
        )
        result = compute_ratio(n_children, n_staff, ratio_staff, ratio_children, capacity)
        label  = datetime.strptime(hstr[:5], "%H:%M").strftime("%-I%p").lower()

        slots.append({
            "hour":       hour,
            "label":      label,
            "n_staff":    n_staff,
            "n_children": n_children,
            "result":     result,
            "is_now":     hour == now_hour,
        })
    return slots


# ── Upcoming risk detection ───────────────────────────────────────────────────

def find_risk_points(
    shifts: list[dict],
    n_children: int,
    ratio_staff: int,
    ratio_children: int,
    now_time_str: str,
) -> list[dict]:
    """
    Identify future times when a shift ending would create or worsen a ratio problem.

    Returns list of dicts:
        time_str    — "14:30"
        staff_name  — who is leaving
        remaining   — staff count after they leave
        result      — compute_ratio() result after departure
        severity    — "warning" or "breach"
    """
    risks = []
    seen  = set()

    for shift in shifts:
        end_t = (shift.get("end_time") or "")[:8]
        if not end_t or end_t <= now_time_str:
            continue
        if end_t in seen:
            continue
        seen.add(end_t)

        # Staff still working after this shift ends
        remaining = sum(
            1 for s in shifts
            if (s.get("end_time") or "") > end_t
            and (s.get("start_time") or "") <= end_t
        )
        result = compute_ratio(n_children, remaining, ratio_staff, ratio_children, 0)

        if result["status"] in (STATUS_WARNING, STATUS_BREACH):
            u     = shift.get("users") or {}
            sname = f"{u.get('first_name', '')} {u.get('last_name', '')}".strip() or "Unknown"
            risks.append({
                "time_str":   end_t[:5],
                "staff_name": sname,
                "remaining":  remaining,
                "result":     result,
                "severity":   result["status"],
            })

    risks.sort(key=lambda r: r["time_str"])
    return risks


# ── Multi-room centre summary ─────────────────────────────────────────────────

def centre_ratio_summary(room_results: list[dict]) -> dict:
    """
    Aggregates per-room results into a centre-level summary.

    room_results: list of {room, n_children, n_staff, result}

    Returns:
        total_children, total_staff,
        n_compliant, n_warning, n_breach, n_empty,
        overall_status  — worst status across all rooms
        compliance_pct  — % of rooms that are compliant or empty
    """
    total_children = sum(r["n_children"] for r in room_results)
    total_staff    = sum(r["n_staff"]    for r in room_results)
    n_compliant    = sum(1 for r in room_results if r["result"]["status"] == STATUS_COMPLIANT)
    n_warning      = sum(1 for r in room_results if r["result"]["status"] == STATUS_WARNING)
    n_breach       = sum(1 for r in room_results if r["result"]["status"] == STATUS_BREACH)
    n_empty        = sum(1 for r in room_results if r["result"]["status"] == STATUS_EMPTY)

    if n_breach > 0:
        overall_status = STATUS_BREACH
    elif n_warning > 0:
        overall_status = STATUS_WARNING
    else:
        overall_status = STATUS_COMPLIANT

    active = len(room_results) - n_empty
    compliance_pct = round((n_compliant / active) * 100) if active > 0 else 100

    return {
        "total_children":  total_children,
        "total_staff":     total_staff,
        "n_compliant":     n_compliant,
        "n_warning":       n_warning,
        "n_breach":        n_breach,
        "n_empty":         n_empty,
        "overall_status":  overall_status,
        "overall_config":  STATUS_CONFIG[overall_status],
        "compliance_pct":  compliance_pct,
    }


# ── Breach severity classification ───────────────────────────────────────────

def classify_breach_severity(duration_minutes: int | None) -> dict:
    """Classify a breach by duration into minor / significant / critical."""
    d = duration_minutes or 0
    if d == 0:
        label, bg, text = "Unknown",     "#f1f5f9", "#475569"
    elif d < 5:
        label, bg, text = "Minor",       "#f0fdf4", "#166534"
    elif d <= 30:
        label, bg, text = "Significant", "#fef3c7", "#92400e"
    else:
        label, bg, text = "Critical",    "#fee2e2", "#991b1b"

    return {"label": label, "bg": bg, "text": text, "minutes": d}


# ── Date / time helpers ───────────────────────────────────────────────────────

def now_time_str() -> str:
    """Current time as HH:MM:SS string."""
    return datetime.now().strftime("%H:%M:%S")


def fmt_time_12h(t_str: str | None) -> str:
    """'14:30:00' → '2:30 PM'. Returns '—' for None/empty."""
    if not t_str:
        return "—"
    try:
        return datetime.strptime(t_str[:5], "%H:%M").strftime("%-I:%M %p")
    except Exception:
        return t_str[:5]


def minutes_between(start_str: str, end_str: str) -> int:
    """Minutes between two HH:MM:SS strings. Returns 0 if invalid."""
    try:
        s = datetime.strptime(start_str[:5], "%H:%M")
        e = datetime.strptime(end_str[:5],   "%H:%M")
        return max(0, int((e - s).total_seconds() / 60))
    except Exception:
        return 0
