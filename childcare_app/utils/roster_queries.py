# utils/roster_queries.py
# All database queries for the Roster module.

from __future__ import annotations
from typing import Optional
from datetime import date, datetime, timedelta, timezone
from utils.supabase_client import get_supabase_client, get_organisation_id


# ── ROSTER PERIODS ────────────────────────────────────────────────────────────

def fetch_roster_periods(centre_id: str, limit: int = 20) -> list[dict]:
    sb = get_supabase_client()
    return (
        sb.from_("roster_periods")
        .select(
            "id, start_date, end_date, status, published_at, notes,"
            "publisher:users!roster_periods_published_by_user_id_fkey("
            "  first_name, last_name)"
        )
        .eq("centre_id", centre_id)
        .order("start_date", desc=True)
        .limit(limit)
        .execute()
    ).data or []


def fetch_roster_period_by_id(period_id: str) -> Optional[dict]:
    sb = get_supabase_client()
    return (
        sb.from_("roster_periods")
        .select("*")
        .eq("id", period_id)
        .single()
        .execute()
    ).data


def create_roster_period(
    centre_id: str, start_date: str, end_date: str, notes: str = ""
) -> dict:
    sb = get_supabase_client()
    return (
        sb.from_("roster_periods")
        .insert({
            "centre_id":  centre_id,
            "start_date": start_date,
            "end_date":   end_date,
            "status":     "draft",
            "notes":      notes.strip() or None,
        })
        .select().single().execute()
    ).data


def publish_roster_period(period_id: str, publisher_user_id: str) -> dict:
    sb  = get_supabase_client()
    now = datetime.now(timezone.utc).isoformat()
    return (
        sb.from_("roster_periods")
        .update({
            "status":               "published",
            "published_at":         now,
            "published_by_user_id": publisher_user_id,
        })
        .eq("id", period_id)
        .select().single().execute()
    ).data


def archive_roster_period(period_id: str) -> None:
    sb = get_supabase_client()
    sb.from_("roster_periods").update({"status": "archived"}).eq("id", period_id).execute()


# ── SHIFTS ────────────────────────────────────────────────────────────────────

def fetch_shifts_for_period(period_id: str) -> list[dict]:
    """All shifts in a period, enriched with user, room, and qualification data."""
    sb = get_supabase_client()
    return (
        sb.from_("roster_shifts")
        .select(
            "id, shift_date, start_time, end_time, break_duration_minutes,"
            "shift_type, status, notes, room_id, user_id, shift_template_id,"
            "users!roster_shifts_user_id_fkey("
            "  id, first_name, last_name,"
            "  staff_profiles!staff_profiles_user_id_fkey("
            "    employment_type,"
            "    staff_qualifications!staff_qualifications_staff_profile_id_fkey("
            "      status, qualification_types!staff_qualifications_qualification_type_id_fkey("
            "        short_name, category)"
            "    )"
            "  )"
            "),"
            "rooms!roster_shifts_room_id_fkey(id, name, colour,"
            "  required_ratio_staff, required_ratio_children, requires_diploma)"
        )
        .eq("roster_period_id", period_id)
        .is_("deleted_at", "null")
        .order("shift_date")
        .order("start_time")
        .execute()
    ).data or []


def fetch_shifts_for_date(centre_id: str, shift_date: str) -> list[dict]:
    """All shifts at a centre on a specific date."""
    sb = get_supabase_client()
    return (
        sb.from_("roster_shifts")
        .select(
            "id, shift_date, start_time, end_time, break_duration_minutes,"
            "shift_type, status, notes, room_id, user_id,"
            "users!roster_shifts_user_id_fkey(id, first_name, last_name),"
            "rooms!roster_shifts_room_id_fkey(id, name, colour,"
            "  required_ratio_staff, required_ratio_children, requires_diploma)"
        )
        .eq("centre_id", centre_id)
        .eq("shift_date", shift_date)
        .is_("deleted_at", "null")
        .execute()
    ).data or []


def create_shift(
    period_id: str, centre_id: str, user_id: str,
    room_id: str, shift_date: str,
    start_time: str, end_time: str, break_duration_minutes: int,
    shift_type: str = "standard", notes: str = "",
    template_id: str | None = None,
    created_by: str | None = None,
) -> dict:
    sb = get_supabase_client()
    return (
        sb.from_("roster_shifts")
        .insert({
            "roster_period_id":      period_id,
            "centre_id":             centre_id,
            "user_id":               user_id,
            "room_id":               room_id or None,
            "shift_date":            shift_date,
            "start_time":            start_time,
            "end_time":              end_time,
            "break_duration_minutes": break_duration_minutes,
            "shift_type":            shift_type,
            "status":                "scheduled",
            "notes":                 notes.strip() or None,
            "shift_template_id":     template_id,
            "created_by_user_id":    created_by,
        })
        .select().single().execute()
    ).data


def update_shift(
    shift_id: str,
    room_id: str, start_time: str, end_time: str,
    break_duration_minutes: int, shift_type: str, notes: str,
) -> dict:
    sb = get_supabase_client()
    return (
        sb.from_("roster_shifts")
        .update({
            "room_id":               room_id or None,
            "start_time":            start_time,
            "end_time":              end_time,
            "break_duration_minutes": break_duration_minutes,
            "shift_type":            shift_type,
            "notes":                 notes.strip() or None,
        })
        .eq("id", shift_id)
        .select().single().execute()
    ).data


def delete_shift(shift_id: str) -> None:
    sb  = get_supabase_client()
    now = datetime.now(timezone.utc).isoformat()
    sb.from_("roster_shifts").update({"deleted_at": now}).eq("id", shift_id).execute()


def copy_shifts_to_period(source_period_id: str, dest_period_id: str,
                           centre_id: str, date_offset_days: int) -> int:
    """Copy all shifts from one period to another, adjusting dates."""
    sb     = get_supabase_client()
    source = fetch_shifts_for_period(source_period_id)
    if not source:
        return 0

    inserts = []
    for s in source:
        try:
            old_date = date.fromisoformat(s["shift_date"])
            new_date = old_date + timedelta(days=date_offset_days)
        except Exception:
            continue
        inserts.append({
            "roster_period_id":      dest_period_id,
            "centre_id":             centre_id,
            "user_id":               s["user_id"],
            "room_id":               s.get("room_id"),
            "shift_date":            new_date.isoformat(),
            "start_time":            s["start_time"],
            "end_time":              s["end_time"],
            "break_duration_minutes": s.get("break_duration_minutes", 0),
            "shift_type":            s.get("shift_type", "standard"),
            "status":                "scheduled",
        })

    if inserts:
        sb.from_("roster_shifts").insert(inserts).execute()
    return len(inserts)


# ── SHIFT TEMPLATES ───────────────────────────────────────────────────────────

def fetch_shift_templates(centre_id: str) -> list[dict]:
    sb = get_supabase_client()
    return (
        sb.from_("shift_templates")
        .select("*")
        .eq("centre_id", centre_id)
        .eq("is_active", True)
        .order("name")
        .execute()
    ).data or []


def create_shift_template(
    centre_id: str, name: str, start_time: str, end_time: str,
    break_duration_minutes: int, colour: str,
) -> dict:
    sb = get_supabase_client()
    return (
        sb.from_("shift_templates")
        .insert({
            "centre_id":             centre_id,
            "name":                  name.strip(),
            "start_time":            start_time,
            "end_time":              end_time,
            "break_duration_minutes": break_duration_minutes,
            "colour":                colour,
            "is_active":             True,
        })
        .select().single().execute()
    ).data


def update_shift_template(
    template_id: str, name: str, start_time: str, end_time: str,
    break_duration_minutes: int, colour: str,
) -> dict:
    sb = get_supabase_client()
    return (
        sb.from_("shift_templates")
        .update({
            "name":                  name.strip(),
            "start_time":            start_time,
            "end_time":              end_time,
            "break_duration_minutes": break_duration_minutes,
            "colour":                colour,
        })
        .eq("id", template_id)
        .select().single().execute()
    ).data


def delete_shift_template(template_id: str) -> None:
    sb = get_supabase_client()
    sb.from_("shift_templates").update({"is_active": False}).eq("id", template_id).execute()


# ── LEAVE & AVAILABILITY (for validation) ─────────────────────────────────────

def fetch_approved_leave_for_period(
    centre_id: str, start_date: str, end_date: str
) -> dict[str, list[str]]:
    """
    Returns {user_id: [list of date strings with approved leave]}
    for the given period.
    """
    sb = get_supabase_client()
    records = (
        sb.from_("leave_requests")
        .select("user_id, start_date, end_date")
        .eq("centre_id", centre_id)
        .eq("status", "approved")
        .lte("start_date", end_date)
        .gte("end_date",   start_date)
        .execute()
    ).data or []

    leave_map: dict[str, list[str]] = {}
    for r in records:
        uid = r["user_id"]
        try:
            sd = date.fromisoformat(r["start_date"])
            ed = date.fromisoformat(r["end_date"])
            d  = sd
            while d <= ed:
                leave_map.setdefault(uid, []).append(d.isoformat())
                d += timedelta(days=1)
        except Exception:
            pass
    return leave_map


def fetch_availability_map(
    centre_id: str
) -> dict[str, dict]:
    """
    Returns {user_id: {day_of_week: {is_available, available_from, available_until}}}
    """
    sb = get_supabase_client()
    records = (
        sb.from_("staff_availability")
        .select("user_id, day_of_week, is_available, available_from, available_until")
        .eq("centre_id", centre_id)
        .execute()
    ).data or []

    av_map: dict[str, dict] = {}
    for r in records:
        uid = r["user_id"]
        dow = r["day_of_week"]
        av_map.setdefault(uid, {})[dow] = {
            "is_available":   r.get("is_available", True),
            "available_from": r.get("available_from"),
            "available_until": r.get("available_until"),
        }
    return av_map


# ── STAFF ENRICHMENT ──────────────────────────────────────────────────────────

def enrich_shifts_with_qual_flags(shifts: list[dict]) -> list[dict]:
    """
    Add has_diploma and counts_ratio fields to each shift dict
    based on nested qualification data.
    """
    for s in shifts:
        u = s.get("users") or {}
        profiles = u.get("staff_profiles") or []
        has_diploma = False
        for profile in profiles:
            for sq in (profile.get("staff_qualifications") or []):
                qt = sq.get("qualification_types") or {}
                if (qt.get("category") == "formal_qualification"
                        and sq.get("status") == "active"):
                    short = (qt.get("short_name") or "").lower()
                    if short in ("diploma", "adv diploma", "b.ed (ec)"):
                        has_diploma = True
                        break

        s["has_diploma"]   = has_diploma
        s["counts_ratio"]  = s.get("shift_type", "standard") in ("standard", "opening",
                                                                    "closing", "split", "overtime")
        s["display_name"]  = f"{u.get('first_name','')} {u.get('last_name','')}".strip()
    return shifts
