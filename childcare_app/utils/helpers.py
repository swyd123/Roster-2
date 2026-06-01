# utils/helpers.py
# Shared formatting, constants, and display helpers used across all pages.

from __future__ import annotations
from datetime import date, datetime
import streamlit as st

# ── Employment type ──────────────────────────────────────────────────────────
EMPLOYMENT_TYPES = {
    "full_time": "Full Time",
    "part_time":  "Part Time",
    "casual":     "Casual",
    "contract":   "Contract",
}
EMPLOYMENT_TYPE_KEYS = list(EMPLOYMENT_TYPES.keys())

# ── User roles ────────────────────────────────────────────────────────────────
ROLES = {
    "super_admin":     "Super Admin",
    "org_admin":       "Organisation Admin",
    "centre_manager":  "Centre Manager",
    "room_leader":     "Room Leader",
    "educator":        "Educator",
    "finance_officer": "Finance Officer",
}
ROLE_KEYS = list(ROLES.keys())

# ── Leave types ───────────────────────────────────────────────────────────────
LEAVE_TYPES = {
    "annual":         "Annual Leave",
    "sick":           "Sick Leave",
    "personal":       "Personal Leave",
    "unpaid":         "Unpaid Leave",
    "parental":       "Parental Leave",
    "compassionate":  "Compassionate Leave",
    "long_service":   "Long Service Leave",
    "public_holiday": "Public Holiday",
    "other":          "Other",
}
LEAVE_TYPE_KEYS = list(LEAVE_TYPES.keys())

# ── Qualification status ──────────────────────────────────────────────────────
QUAL_STATUS_CONFIG = {
    "active":               {"label": "Active",               "icon": "✅", "colour": "#27ae60"},
    "expired":              {"label": "Expired",              "icon": "❌", "colour": "#e74c3c"},
    "pending_verification": {"label": "Pending Verification", "icon": "🔄", "colour": "#f39c12"},
    "revoked":              {"label": "Revoked",              "icon": "🚫", "colour": "#95a5a6"},
}

# ── Day names ─────────────────────────────────────────────────────────────────
DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
DAYS_SHORT = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
WEEKDAYS = [1, 2, 3, 4, 5]   # Mon–Fri indices


# ── Formatting helpers ────────────────────────────────────────────────────────

def fmt_name(record: dict) -> str:
    """Full name from a staff record (handles nested users object)."""
    u = record.get("users") or record
    return f"{u.get('first_name','').strip()} {u.get('last_name','').strip()}".strip() or "Unknown"

def fmt_date(val) -> str:
    """Formats a date string or date object as '3 Jun 2026'. Returns '—' if None."""
    if not val:
        return "—"
    try:
        if isinstance(val, str):
            val = date.fromisoformat(val[:10])
        return val.strftime("%-d %b %Y")
    except Exception:
        return str(val)

def fmt_employment(val: str) -> str:
    return EMPLOYMENT_TYPES.get(val, val.replace("_"," ").title() if val else "—")

def fmt_role(val: str) -> str:
    return ROLES.get(val, val.replace("_"," ").title() if val else "—")

def fmt_leave_type(val: str) -> str:
    return LEAVE_TYPES.get(val, val.replace("_"," ").title() if val else "—")

def days_until(expiry_str: str | None) -> int | None:
    if not expiry_str:
        return None
    try:
        exp = date.fromisoformat(expiry_str[:10])
        return (exp - date.today()).days
    except Exception:
        return None

def qual_risk_level(expiry_str: str | None, status: str) -> str:
    """Returns 'expired', 'critical' (≤30d), 'warning' (≤60d), or 'ok'."""
    if status == "expired":
        return "expired"
    d = days_until(expiry_str)
    if d is None:
        return "ok"
    if d < 0:
        return "expired"
    if d <= 30:
        return "critical"
    if d <= 60:
        return "warning"
    return "ok"

def active_badge(is_active: bool) -> str:
    return "🟢 Active" if is_active else "🔴 Inactive"

def leave_status_badge(status: str) -> str:
    icons = {"pending": "🟡", "approved": "✅", "declined": "❌", "cancelled": "⚫"}
    labels = {"pending": "Pending", "approved": "Approved",
              "declined": "Declined", "cancelled": "Cancelled"}
    return f"{icons.get(status,'❓')} {labels.get(status, status.title())}"

def workdays_between(start: date, end: date) -> int:
    """Count Mon–Fri days between two dates inclusive."""
    count = 0
    d = start
    from datetime import timedelta
    while d <= end:
        if d.weekday() < 5:
            count += 1
        d += timedelta(days=1)
    return count


# ── Toast helpers ─────────────────────────────────────────────────────────────

def toast_success(msg: str):
    st.success(f"✅ {msg}")

def toast_error(msg: str):
    st.error(f"❌ {msg}")

def toast_warn(msg: str):
    st.warning(f"⚠️ {msg}")
