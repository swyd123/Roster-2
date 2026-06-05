# utils/break_queries.py
# All database queries for the Break Tracking module.

from __future__ import annotations
from typing import Optional
from datetime import date, datetime, timedelta, timezone
from utils.supabase_client import get_supabase_client


def _one(resp) -> Optional[dict]:
    """
    Return the first row from a query response, or None.
    Replaces .single() which is not available on SyncQueryRequestBuilder.
    """
    data = resp.data
    if not data:
        return None
    return data[0] if isinstance(data, list) else data


# ── READ ──────────────────────────────────────────────────────────────────────

def fetch_breaks_today(centre_id: str, break_date: str | None = None) -> list[dict]:
    """
    All break records for today at this centre.
    Includes the staff member's name and their shift data.
    """
    sb    = get_supabase_client()
    today = break_date or date.today().isoformat()

    return (
        sb.from_("break_records")
        .select(
            "id, user_id, break_date, break_type, status, notes,"
            "planned_start_time, planned_end_time, planned_duration_minutes,"
            "actual_start_time, actual_end_time, actual_duration_minutes,"
            "users!break_records_user_id_fkey(id, first_name, last_name),"
            "roster_shifts!break_records_roster_shift_id_fkey("
            "  id, start_time, end_time, room_id,"
            "  rooms!roster_shifts_room_id_fkey(id, name, colour)"
            ")"
        )
        .eq("centre_id", centre_id)
        .eq("break_date", today)
        .order("planned_start_time")
        .execute()
    ).data or []


def fetch_breaks_for_shift(roster_shift_id: str) -> list[dict]:
    """All breaks for a specific roster shift."""
    sb = get_supabase_client()
    return (
        sb.from_("break_records")
        .select("*")
        .eq("roster_shift_id", roster_shift_id)
        .order("planned_start_time")
        .execute()
    ).data or []


def fetch_breaks_for_user_today(user_id: str, break_date: str | None = None) -> list[dict]:
    """All breaks for one staff member today."""
    sb    = get_supabase_client()
    today = break_date or date.today().isoformat()
    return (
        sb.from_("break_records")
        .select("*")
        .eq("user_id", user_id)
        .eq("break_date", today)
        .order("planned_start_time")
        .execute()
    ).data or []


def fetch_break_history(
    centre_id: str,
    from_date: str | None = None,
    to_date: str | None = None,
    user_id: str | None = None,
    status_filter: str | None = None,
) -> list[dict]:
    """
    Historical break records for reporting and award compliance auditing.
    """
    sb = get_supabase_client()
    q  = (
        sb.from_("break_records")
        .select(
            "id, user_id, break_date, break_type, status, notes,"
            "planned_start_time, planned_end_time, planned_duration_minutes,"
            "actual_start_time, actual_end_time, actual_duration_minutes,"
            "users!break_records_user_id_fkey(id, first_name, last_name),"
            "roster_shifts!break_records_roster_shift_id_fkey("
            "  id, start_time, end_time,"
            "  rooms!roster_shifts_room_id_fkey(id, name, colour)"
            ")"
        )
        .eq("centre_id", centre_id)
    )
    if from_date:
        q = q.gte("break_date", from_date)
    if to_date:
        q = q.lte("break_date", to_date)
    if user_id:
        q = q.eq("user_id", user_id)
    if status_filter and status_filter != "all":
        q = q.eq("status", status_filter)

    return (
        q.order("break_date", desc=True)
        .order("planned_start_time", desc=False)
        .execute()
    ).data or []


def fetch_break_by_id(break_id: str) -> Optional[dict]:
    sb = get_supabase_client()
    return _one(
        sb.from_("break_records")
        .select("*")
        .eq("id", break_id)
        .limit(1)
        .execute()
    )


def fetch_break_rules(centre_id: str) -> list[dict]:
    """
    Fetch configurable break rules from the break_rules table.

    The break_rules table stores per-centre (or global) break entitlement tiers.
    Expected columns: min_hours, max_hours, paid_minutes, unpaid_minutes,
                      paid_count, paid_duration, label, is_active.

    If the table does not exist or has no rows for this centre, returns [] so
    the caller can fall back to BREAK_RULES_DEFAULT without error.
    No .single() — plain list query.
    """
    sb = get_supabase_client()
    try:
        rows = (
            sb.from_("break_rules")
            .select(
                "min_hours, max_hours, paid_minutes, unpaid_minutes, "
                "paid_count, paid_duration, label"
            )
            .eq("is_active", True)
            .or_(f"centre_id.eq.{centre_id},centre_id.is.null")
            .order("min_hours")
            .execute()
        ).data or []
        return rows
    except Exception:
        # Table may not exist yet — fall back to defaults silently
        return []


# ── CREATE ────────────────────────────────────────────────────────────────────

def create_break(
    centre_id: str,
    user_id: str,
    break_date: str,
    break_type: str,
    planned_start_time: str,
    planned_end_time: str,
    planned_duration_minutes: int,
    roster_shift_id: str | None = None,
    notes: str = "",
) -> dict:
    """Schedule a new break."""
    sb     = get_supabase_client()
    result = _one(
        sb.from_("break_records")
        .insert({
            "centre_id":               centre_id,
            "user_id":                 user_id,
            "break_date":              break_date,
            "break_type":              break_type,
            "planned_start_time":      planned_start_time,
            "planned_end_time":        planned_end_time,
            "planned_duration_minutes": planned_duration_minutes,
            "roster_shift_id":         roster_shift_id,
            "status":                  "scheduled",
            "notes":                   notes.strip() or None,
        })
        .select()
        .execute()
    )
    if not result:
        raise ValueError("Break could not be created — no row returned from database.")
    return result


# ── UPDATE ────────────────────────────────────────────────────────────────────

def create_breaks_batch(rows: list[dict]) -> int:
    """
    Batch-insert multiple break records in a single Supabase call.
    Each row must contain: centre_id, user_id, break_date, break_type,
    planned_start_time, planned_end_time, planned_duration_minutes.
    Optional: roster_shift_id, notes, status.
    Returns count of rows inserted.
    No .single() — plain insert.
    """
    if not rows:
        return 0
    sb = get_supabase_client()
    payload = [
        {
            "centre_id":               r["centre_id"],
            "user_id":                 r["user_id"],
            "break_date":              r["break_date"],
            "break_type":              r["break_type"],
            "planned_start_time":      r["planned_start_time"],
            "planned_end_time":        r["planned_end_time"],
            "planned_duration_minutes": r["planned_duration_minutes"],
            "roster_shift_id":         r.get("roster_shift_id"),
            "status":                  r.get("status", "scheduled"),
            "notes":                   (r.get("notes") or "").strip() or None,
        }
        for r in rows
    ]
    resp = sb.from_("break_records").insert(payload).execute()
    return len(resp.data or [])


def update_break_schedule(
    break_id: str,
    planned_start_time: str,
    planned_end_time: str,
    planned_duration_minutes: int,
    break_type: str,
    notes: str = "",
) -> dict:
    """Edit the scheduled times of a break."""
    sb     = get_supabase_client()
    result = _one(
        sb.from_("break_records")
        .update({
            "planned_start_time":       planned_start_time,
            "planned_end_time":         planned_end_time,
            "planned_duration_minutes": planned_duration_minutes,
            "break_type":               break_type,
            "notes":                    notes.strip() or None,
        })
        .eq("id", break_id)
        .select()
        .execute()
    )
    if not result:
        raise ValueError("Break schedule could not be updated — no row returned from database.")
    return result


def mark_break_started(break_id: str, actual_start: str) -> dict:
    """Record the actual start time when a break begins."""
    sb     = get_supabase_client()
    result = _one(
        sb.from_("break_records")
        .update({
            "actual_start_time": actual_start,
            "status":            "in_progress",
        })
        .eq("id", break_id)
        .select()
        .execute()
    )
    if not result:
        raise ValueError("Break start could not be recorded — no row returned from database.")
    return result


def mark_break_completed(
    break_id: str,
    actual_start: str,
    actual_end: str,
    actual_duration_minutes: int,
    notes: str = "",
) -> dict:
    """Record actual break times and mark as completed."""
    sb     = get_supabase_client()
    result = _one(
        sb.from_("break_records")
        .update({
            "actual_start_time":       actual_start,
            "actual_end_time":         actual_end,
            "actual_duration_minutes": actual_duration_minutes,
            "status":                  "completed",
            "notes":                   notes.strip() or None,
        })
        .eq("id", break_id)
        .select()
        .execute()
    )
    if not result:
        raise ValueError("Break completion could not be recorded — no row returned from database.")
    return result


def mark_break_missed(break_id: str, notes: str = "") -> dict:
    """Mark a break as missed."""
    sb     = get_supabase_client()
    result = _one(
        sb.from_("break_records")
        .update({
            "status": "missed",
            "notes":  notes.strip() or None,
        })
        .eq("id", break_id)
        .select()
        .execute()
    )
    if not result:
        raise ValueError("Break could not be marked missed — no row returned from database.")
    return result


def update_break_status(break_id: str, new_status: str) -> dict:
    """Update break status directly."""
    sb     = get_supabase_client()
    result = _one(
        sb.from_("break_records")
        .update({"status": new_status})
        .eq("id", break_id)
        .select()
        .execute()
    )
    if not result:
        raise ValueError(f"Break status could not be set to '{new_status}' — no row returned from database.")
    return result


def delete_break(break_id: str) -> None:
    """Hard delete a scheduled break (not yet taken)."""
    sb = get_supabase_client()
    sb.from_("break_records").delete().eq("id", break_id).execute()
