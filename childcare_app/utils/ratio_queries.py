# utils/ratio_queries.py
# Database queries specific to the Ratio Monitoring module.
# Extends room_queries.py with richer data fetches.

from __future__ import annotations
from typing import Optional
from datetime import date, datetime, timezone, timedelta
from utils.supabase_client import get_supabase_client


# ── Enhanced shifts fetch — includes qualification data ──────────────────────

def fetch_shifts_with_quals(centre_id: str, shift_date: str | None = None) -> list[dict]:
    """
    Today's shifts at a centre, enriched with each staff member's
    active qualifications so we can show "Diploma ✅" on the detail screen.
    """
    sb    = get_supabase_client()
    today = shift_date or date.today().isoformat()

    shifts = (
        sb.from_("roster_shifts")
        .select(
            "id, room_id, user_id, start_time, end_time,"
            "break_duration_minutes, status, shift_type,"
            "users!roster_shifts_user_id_fkey("
            "  id, first_name, last_name,"
            "  staff_profiles!staff_profiles_user_id_fkey("
            "    staff_qualifications!staff_qualifications_staff_profile_id_fkey("
            "      status, expiry_date,"
            "      qualification_types!staff_qualifications_qualification_type_id_fkey("
            "        name, short_name, category"
            "      )"
            "    )"
            "  )"
            "),"
            "rooms!roster_shifts_room_id_fkey(id, name, colour,"
            "  required_ratio_staff, required_ratio_children,"
            "  requires_diploma, licensed_capacity"
            ")"
        )
        .eq("centre_id", centre_id)
        .eq("shift_date", today)
        .in_("status", ["scheduled", "confirmed", "in_progress"])
        .is_("deleted_at", "null")
        .order("room_id")
        .order("start_time")
        .execute()
    ).data or []

    return shifts


def fetch_room_shifts_with_quals(room_id: str, shift_date: str | None = None) -> list[dict]:
    """Shifts for one room today, with qualification data per staff member."""
    sb    = get_supabase_client()
    today = shift_date or date.today().isoformat()

    return (
        sb.from_("roster_shifts")
        .select(
            "id, user_id, start_time, end_time, break_duration_minutes, status,"
            "users!roster_shifts_user_id_fkey("
            "  id, first_name, last_name,"
            "  staff_profiles!staff_profiles_user_id_fkey("
            "    employment_type,"
            "    staff_qualifications!staff_qualifications_staff_profile_id_fkey("
            "      status, expiry_date,"
            "      qualification_types!staff_qualifications_qualification_type_id_fkey("
            "        name, short_name, category"
            "      )"
            "    )"
            "  )"
            ")"
        )
        .eq("room_id", room_id)
        .eq("shift_date", today)
        .in_("status", ["scheduled", "confirmed", "in_progress"])
        .is_("deleted_at", "null")
        .order("start_time")
        .execute()
    ).data or []


def fetch_room_attendance_with_children(room_id: str, attendance_date: str | None = None) -> list[dict]:
    """Today's attendance for a room including full child details."""
    sb    = get_supabase_client()
    today = attendance_date or date.today().isoformat()

    return (
        sb.from_("attendance_records")
        .select(
            "id, child_id, status, signed_in_at, signed_out_at, absence_reason,"
            "children!attendance_records_child_id_fkey("
            "  id, first_name, last_name, date_of_birth, allergies, medical_notes"
            ")"
        )
        .eq("room_id", room_id)
        .eq("attendance_date", today)
        .execute()
    ).data or []


# ── Historical breach analytics ──────────────────────────────────────────────

def fetch_breach_stats(centre_id: str, days_back: int = 90) -> dict:
    """
    Aggregated breach statistics over the past N days.
    Returns counts, total duration, and per-room breakdown.
    """
    sb       = get_supabase_client()
    from_date = (date.today() - timedelta(days=days_back)).isoformat()

    records = (
        sb.from_("ratio_breach_log")
        .select(
            "id, breach_date, duration_minutes, children_present,"
            "staff_present, required_staff,"
            "rooms!ratio_breach_log_room_id_fkey(id, name, colour)"
        )
        .eq("centre_id", centre_id)
        .gte("breach_date", from_date)
        .execute()
    ).data or []

    total        = len(records)
    total_mins   = sum((r.get("duration_minutes") or 0) for r in records)
    critical     = sum(1 for r in records if (r.get("duration_minutes") or 0) > 30)
    significant  = sum(1 for r in records if 5 <= (r.get("duration_minutes") or 0) <= 30)
    minor        = sum(1 for r in records if 0 < (r.get("duration_minutes") or 0) < 5)
    unknown_dur  = sum(1 for r in records if not r.get("duration_minutes"))

    # Per-room breakdown
    room_counts: dict[str, dict] = {}
    for r in records:
        room = r.get("rooms") or {}
        rid  = room.get("id","unknown")
        if rid not in room_counts:
            room_counts[rid] = {
                "name":   room.get("name","Unknown"),
                "colour": room.get("colour","#3498DB"),
                "count":  0,
                "total_minutes": 0,
            }
        room_counts[rid]["count"] += 1
        room_counts[rid]["total_minutes"] += (r.get("duration_minutes") or 0)

    # Monthly trend (last 3 months)
    monthly: dict[str, int] = {}
    for r in records:
        bd = r.get("breach_date","")
        if bd:
            month_key = bd[:7]   # "YYYY-MM"
            monthly[month_key] = monthly.get(month_key, 0) + 1

    return {
        "total":       total,
        "total_mins":  total_mins,
        "critical":    critical,
        "significant": significant,
        "minor":       minor,
        "unknown_dur": unknown_dur,
        "per_room":    list(room_counts.values()),
        "monthly":     monthly,
        "days_back":   days_back,
        "records":     records,
    }


def fetch_compliance_summary(centre_id: str, days_back: int = 30) -> list[dict]:
    """
    Returns breach records for a compliance report, joined with room data.
    Used by the Ratio Compliance Report screen.
    """
    sb        = get_supabase_client()
    from_date = (date.today() - timedelta(days=days_back)).isoformat()

    return (
        sb.from_("ratio_breach_log")
        .select(
            "id, breach_date, breach_start_time, breach_end_time,"
            "duration_minutes, children_present, staff_present, required_staff,"
            "breach_reason, resolution_action, created_at,"
            "rooms!ratio_breach_log_room_id_fkey(id, name, colour),"
            "documenter:users!ratio_breach_log_documented_by_user_id_fkey("
            "  first_name, last_name"
            ")"
        )
        .eq("centre_id", centre_id)
        .gte("breach_date", from_date)
        .order("breach_date", desc=True)
        .execute()
    ).data or []


# ── Qualification helpers ─────────────────────────────────────────────────────

def extract_quals_for_shift(shift: dict) -> list[dict]:
    """
    Pull qualification records from a nested shift object.
    Returns flat list of {name, short_name, category, status, expiry_date}.
    """
    quals = []
    user  = shift.get("users") or {}
    for profile in (user.get("staff_profiles") or []):
        for sq in (profile.get("staff_qualifications") or []):
            qt = sq.get("qualification_types") or {}
            quals.append({
                "name":        qt.get("name", ""),
                "short_name":  qt.get("short_name", ""),
                "category":    qt.get("category", ""),
                "status":      sq.get("status", ""),
                "expiry_date": sq.get("expiry_date"),
            })
    return quals


def has_diploma(shift: dict) -> bool:
    """True if the shift's staff member has an active formal qualification (diploma or above)."""
    for q in extract_quals_for_shift(shift):
        if (q.get("category") == "formal_qualification"
                and q.get("status") == "active"
                and q.get("short_name", "").lower() in ("diploma", "adv diploma", "b.ed (ec)")):
            return True
    return False


def counts_toward_ratio(shift: dict) -> bool:
    """
    True if this shift's role type counts toward the educator ratio.
    Non-educators (admin, cook, etc.) do not count.
    Approximation: all scheduled/confirmed shifts count unless shift_type is non-standard.
    Extend this to check user_centre_roles if role data is available.
    """
    return shift.get("shift_type", "standard") in ("standard", "overtime")
