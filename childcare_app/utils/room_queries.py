# utils/room_queries.py
# All database queries for the Room Management and Ratio Monitoring modules.

from __future__ import annotations
from typing import Optional
from datetime import datetime, date, timezone
from utils.supabase_client import get_supabase_client, get_organisation_id


def _one(resp) -> Optional[dict]:
    """
    Return the first row from a query response, or None.
    Replaces .single() which is not available on SyncQueryRequestBuilder.
    Used for SELECT … LIMIT 1, INSERT … SELECT, and UPDATE … SELECT
    where exactly one row is expected.
    """
    data = resp.data
    if not data:
        return None
    return data[0] if isinstance(data, list) else data


# ─────────────────────────────────────────────────────────────────────────────
# ROOMS — CRUD
# ─────────────────────────────────────────────────────────────────────────────

def fetch_rooms(centre_id: str, include_inactive: bool = False) -> list[dict]:
    """
    All rooms for a centre, ordered by sort_order.
    include_inactive=True shows deactivated rooms too (for settings views).
    """
    sb = get_supabase_client()
    q  = (
        sb.from_("rooms")
        .select("*")
        .eq("centre_id", centre_id)
        .is_("deleted_at", "null")
        .order("sort_order", desc=False)
        .order("name", desc=False)
    )
    if not include_inactive:
        q = q.eq("is_active", True)
    return q.execute().data or []


def fetch_room_by_id(room_id: str) -> Optional[dict]:
    sb = get_supabase_client()
    return _one(
        sb.from_("rooms")
        .select("*")
        .eq("id", room_id)
        .is_("deleted_at", "null")
        .limit(1)
        .execute()
    )


def create_room(
    centre_id: str,
    name: str,
    age_min_months: int,
    age_max_months: int,
    licensed_capacity: int,
    required_ratio_staff: int,
    required_ratio_children: int,
    requires_diploma: bool,
    colour: str,
    sort_order: int,
    notes: str,
) -> dict:
    sb     = get_supabase_client()
    result = _one(
        sb.from_("rooms")
        .insert({
            "centre_id":               centre_id,
            "name":                    name.strip(),
            "age_min_months":          age_min_months,
            "age_max_months":          age_max_months,
            "licensed_capacity":       licensed_capacity,
            "required_ratio_staff":    required_ratio_staff,
            "required_ratio_children": required_ratio_children,
            "requires_diploma":        requires_diploma,
            "colour":                  colour,
            "sort_order":              sort_order,
            "is_active":               True,
            "notes":                   notes.strip() or None,
        })
        .select()
        .execute()
    )
    if not result:
        raise ValueError("Room could not be created — no row returned from database.")
    return result


def update_room(
    room_id: str,
    name: str,
    age_min_months: int,
    age_max_months: int,
    licensed_capacity: int,
    required_ratio_staff: int,
    required_ratio_children: int,
    requires_diploma: bool,
    colour: str,
    sort_order: int,
    is_active: bool,
    notes: str,
) -> dict:
    sb     = get_supabase_client()
    result = _one(
        sb.from_("rooms")
        .update({
            "name":                    name.strip(),
            "age_min_months":          age_min_months,
            "age_max_months":          age_max_months,
            "licensed_capacity":       licensed_capacity,
            "required_ratio_staff":    required_ratio_staff,
            "required_ratio_children": required_ratio_children,
            "requires_diploma":        requires_diploma,
            "colour":                  colour,
            "sort_order":              sort_order,
            "is_active":               is_active,
            "notes":                   notes.strip() or None,
        })
        .eq("id", room_id)
        .select()
        .execute()
    )
    if not result:
        raise ValueError(f"Room '{name}' could not be updated — no row returned from database.")
    return result


def soft_delete_room(room_id: str) -> None:
    sb  = get_supabase_client()
    now = datetime.now(timezone.utc).isoformat()
    sb.from_("rooms").update({
        "deleted_at": now,
        "is_active":  False,
    }).eq("id", room_id).execute()


# ─────────────────────────────────────────────────────────────────────────────
# CHILDREN — room allocation queries
# ─────────────────────────────────────────────────────────────────────────────

def fetch_children_by_centre(centre_id: str) -> list[dict]:
    """
    All active enrolled children at this centre, with their room assignment.
    Used for allocation view and ratio calculations.
    """
    sb = get_supabase_client()
    return (
        sb.from_("children")
        .select(
            "id, first_name, last_name, date_of_birth, room_id, enrolment_status,"
            "enrolment_days, usual_start_time, usual_end_time,"
            "rooms!children_room_id_fkey(id, name, colour)"
        )
        .eq("centre_id", centre_id)
        .eq("enrolment_status", "active")
        .is_("deleted_at", "null")
        .order("last_name")
        .execute()
    ).data or []


def fetch_children_for_room(room_id: str) -> list[dict]:
    """All active children in a specific room."""
    sb = get_supabase_client()
    return (
        sb.from_("children")
        .select("id, first_name, last_name, date_of_birth, enrolment_days")
        .eq("room_id", room_id)
        .eq("enrolment_status", "active")
        .is_("deleted_at", "null")
        .order("last_name")
        .execute()
    ).data or []


def fetch_enrolled_counts_by_room(centre_id: str) -> dict[str, int]:
    """
    Return {room_id: enrolled_count} for all active, non-deleted children
    at this centre who have a room assigned.

    Counts only children where:
        enrolment_status = 'active'
        deleted_at IS NULL
        room_id IS NOT NULL

    The room_id IS NOT NULL filter is applied in Python (not via PostgREST)
    to avoid .not_.is_() compatibility issues across supabase-py versions.

    Returns {} when the children table has no matching rows — callers must
    treat a missing key as 0, not as an error.
    No .single() — plain list query grouped in Python.
    """
    sb = get_supabase_client()
    rows = (
        sb.from_("children")
        .select("room_id")
        .eq("centre_id", centre_id)
        .eq("enrolment_status", "active")
        .is_("deleted_at", "null")
        .execute()
    ).data or []

    counts: dict[str, int] = {}
    for row in rows:
        rid = row.get("room_id")
        if rid:                                 # skip rows where room_id is None
            counts[rid] = counts.get(rid, 0) + 1
    return counts


def move_child_to_room(child_id: str, new_room_id: str | None) -> None:
    """Reassign a child to a different room (or unassigned if None)."""
    sb = get_supabase_client()
    sb.from_("children").update({
        "room_id": new_room_id,
    }).eq("id", child_id).execute()


# ─────────────────────────────────────────────────────────────────────────────
# ATTENDANCE — for live ratio calculations
# ─────────────────────────────────────────────────────────────────────────────

def fetch_today_attendance(centre_id: str) -> list[dict]:
    """
    Today's attendance records for all children at this centre.
    Used to determine how many children are currently in each room.
    """
    sb    = get_supabase_client()
    today = date.today().isoformat()
    return (
        sb.from_("attendance_records")
        .select(
            "id, child_id, room_id, status, signed_in_at, signed_out_at,"
            "children!attendance_records_child_id_fkey(first_name, last_name)"
        )
        .eq("centre_id", centre_id)
        .eq("attendance_date", today)
        .execute()
    ).data or []


def fetch_attendance_for_room_today(room_id: str) -> list[dict]:
    """Today's sign-ins for a specific room."""
    sb    = get_supabase_client()
    today = date.today().isoformat()
    return (
        sb.from_("attendance_records")
        .select(
            "id, child_id, status, signed_in_at, signed_out_at,"
            "children!attendance_records_child_id_fkey(first_name, last_name, date_of_birth)"
        )
        .eq("room_id", room_id)
        .eq("attendance_date", today)
        .in_("status", ["present", "expected"])
        .execute()
    ).data or []


# ─────────────────────────────────────────────────────────────────────────────
# ROSTER SHIFTS — for today's staffing calculations
# ─────────────────────────────────────────────────────────────────────────────

def fetch_today_shifts(centre_id: str) -> list[dict]:
    """
    All scheduled/confirmed shifts at this centre today.
    Used to determine staff coverage per room.
    """
    sb    = get_supabase_client()
    today = date.today().isoformat()
    return (
        sb.from_("roster_shifts")
        .select(
            "id, room_id, user_id, start_time, end_time, break_duration_minutes, status,"
            "users!roster_shifts_user_id_fkey(first_name, last_name),"
            "rooms!roster_shifts_room_id_fkey(id, name, colour)"
        )
        .eq("centre_id", centre_id)
        .eq("shift_date", today)
        .in_("status", ["scheduled", "confirmed", "in_progress"])
        .is_("deleted_at", "null")
        .execute()
    ).data or []


def fetch_today_shifts_for_room(room_id: str) -> list[dict]:
    """Today's shifts in a specific room."""
    sb    = get_supabase_client()
    today = date.today().isoformat()
    return (
        sb.from_("roster_shifts")
        .select(
            "id, user_id, start_time, end_time, break_duration_minutes, status,"
            "users!roster_shifts_user_id_fkey(first_name, last_name)"
        )
        .eq("room_id", room_id)
        .eq("shift_date", today)
        .in_("status", ["scheduled", "confirmed", "in_progress"])
        .is_("deleted_at", "null")
        .order("start_time")
        .execute()
    ).data or []


# ─────────────────────────────────────────────────────────────────────────────
# RATIO BREACH LOG
# ─────────────────────────────────────────────────────────────────────────────

def fetch_breach_log(centre_id: str, from_date: str | None = None,
                     to_date: str | None = None, room_id: str | None = None) -> list[dict]:
    """All ratio breach records for this centre, with filters."""
    sb = get_supabase_client()
    q  = (
        sb.from_("ratio_breach_log")
        .select(
            "id, breach_date, breach_start_time, breach_end_time, duration_minutes,"
            "children_present, staff_present, required_staff,"
            "breach_reason, resolution_action, created_at,"
            "rooms!ratio_breach_log_room_id_fkey(id, name, colour),"
            "documenter:users!ratio_breach_log_documented_by_user_id_fkey(first_name, last_name)"
        )
        .eq("centre_id", centre_id)
    )
    if from_date:
        q = q.gte("breach_date", from_date)
    if to_date:
        q = q.lte("breach_date", to_date)
    if room_id:
        q = q.eq("room_id", room_id)
    return (q.order("breach_date", desc=True).order("breach_start_time", desc=True).execute()).data or []


def log_breach(
    centre_id: str,
    room_id: str,
    breach_date: str,
    breach_start_time: str,
    breach_end_time: str | None,
    children_present: int,
    staff_present: int,
    required_staff: int,
    breach_reason: str,
    resolution_action: str,
    documented_by_user_id: str | None = None,
) -> dict:
    """Manually record a ratio breach incident."""
    sb = get_supabase_client()

    # Calculate duration if both times provided
    duration = None
    if breach_start_time and breach_end_time:
        try:
            from datetime import time as _time
            def parse_t(s):
                parts = s.split(":")
                return _time(int(parts[0]), int(parts[1]))
            start      = parse_t(breach_start_time)
            end        = parse_t(breach_end_time)
            start_mins = start.hour * 60 + start.minute
            end_mins   = end.hour   * 60 + end.minute
            duration   = max(0, end_mins - start_mins)
        except Exception:
            pass

    result = _one(
        sb.from_("ratio_breach_log")
        .insert({
            "centre_id":             centre_id,
            "room_id":               room_id,
            "breach_date":           breach_date,
            "breach_start_time":     breach_start_time,
            "breach_end_time":       breach_end_time or None,
            "duration_minutes":      duration,
            "children_present":      children_present,
            "staff_present":         staff_present,
            "required_staff":        required_staff,
            "breach_reason":         breach_reason.strip() or None,
            "resolution_action":     resolution_action.strip() or None,
            "documented_by_user_id": documented_by_user_id,
        })
        .select()
        .execute()
    )
    if not result:
        raise ValueError("Breach record could not be saved — no row returned from database.")
    return result


# ─────────────────────────────────────────────────────────────────────────────
# RATIO CALCULATION HELPERS
# (Pure Python — no DB calls. Used by UI pages.)
# ─────────────────────────────────────────────────────────────────────────────

def calc_ratio_status(
    children_present: int,
    staff_present: int,
    required_ratio_staff: int,
    required_ratio_children: int,
    licensed_capacity: int,
) -> dict:
    """
    Returns a dict describing the current ratio status for a room.

    Fields:
        status      — "compliant", "warning", or "breach"
        min_staff   — minimum staff required for current children
        surplus     — staff above minimum (negative = shortfall)
        label       — human-readable status string
        colour      — hex background colour for the card
        text_colour — hex text colour
        icon        — emoji status indicator
        capacity_pct— children as % of licensed capacity
    """
    if children_present == 0:
        return {
            "status":      "compliant",
            "min_staff":   0,
            "surplus":     staff_present,
            "label":       "No children",
            "colour":      "#f0f4f8",
            "text_colour": "#4a6079",
            "icon":        "⚪",
            "capacity_pct": 0,
        }

    import math
    min_staff    = math.ceil(children_present / required_ratio_children) * required_ratio_staff
    surplus      = staff_present - min_staff
    capacity_pct = round((children_present / licensed_capacity) * 100) if licensed_capacity > 0 else 0

    if surplus >= 0:
        min_for_one_more = math.ceil((children_present + 1) / required_ratio_children) * required_ratio_staff
        if min_for_one_more > staff_present:
            status, label, colour, text_colour, icon = (
                "warning", "At limit", "#fffbeb", "#92400e", "⚠️")
        else:
            status, label, colour, text_colour, icon = (
                "compliant", "Compliant", "#f0fdf4", "#14532d", "✅")
    else:
        status, label, colour, text_colour, icon = (
            "breach", "Ratio breach", "#fff1f2", "#881337", "❌")

    return {
        "status":       status,
        "min_staff":    min_staff,
        "surplus":      surplus,
        "label":        label,
        "colour":       colour,
        "text_colour":  text_colour,
        "icon":         icon,
        "capacity_pct": capacity_pct,
    }


def age_in_months(dob_str: str | None) -> int | None:
    """Returns a child's age in whole months from a date string."""
    if not dob_str:
        return None
    try:
        from datetime import date as _date
        dob   = _date.fromisoformat(dob_str[:10])
        today = _date.today()
        months = (today.year - dob.year) * 12 + (today.month - dob.month)
        if today.day < dob.day:
            months -= 1
        return max(0, months)
    except Exception:
        return None


def fmt_age(months: int | None) -> str:
    """Formats months as a readable age string like '2y 3m' or '8m'."""
    if months is None:
        return "—"
    years  = months // 12
    remain = months % 12
    if years == 0:
        return f"{remain}m"
    if remain == 0:
        return f"{years}y"
    return f"{years}y {remain}m"


def fmt_age_range(min_months: int, max_months: int) -> str:
    """Returns e.g. '0–23 months' or '2–3 years'."""
    def label(m):
        if m < 24:
            return f"{m}m"
        y   = m // 12
        rem = m % 12
        return f"{y}y" if rem == 0 else f"{y}y{rem}m"
    return f"{label(min_months)} – {label(max_months)}"


def is_child_near_age_out(dob_str: str | None, max_months: int, warning_months: int = 3) -> bool:
    """True if the child will exceed the room's age limit within warning_months months."""
    age = age_in_months(dob_str)
    if age is None:
        return False
    return age >= (max_months - warning_months)
