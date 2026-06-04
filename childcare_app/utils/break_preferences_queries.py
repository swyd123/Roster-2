# utils/break_preferences_queries.py
# Database queries for staff_break_preferences table.
# No .single() anywhere.

from __future__ import annotations
from datetime import date
from typing import Optional
from utils.supabase_client import get_supabase_client


DAYS = {0: "Sun", 1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat"}
DAY_NAMES = {0: "Sunday", 1: "Monday", 2: "Tuesday", 3: "Wednesday",
             4: "Thursday", 5: "Friday", 6: "Saturday"}


def _one(resp) -> Optional[dict]:
    """Return first row or None. No .single()."""
    data = resp.data
    if not data:
        return None
    return data[0] if isinstance(data, list) else data


def fetch_break_prefs_for_user(
    user_id: str,
    centre_id: str,
    as_of: str | None = None,
) -> dict[int, bool]:
    """
    Return {day_of_week: unpaid_break_opt_out} for the given user at the
    given centre, using the most recent preference that is effective as_of
    the given date (defaults to today).

    day_of_week: 0=Sunday … 6=Saturday (matches Python date.isoweekday() % 7).
    Missing days default to False (no opt-out).
    """
    sb      = get_supabase_client()
    today   = as_of or date.today().isoformat()

    rows = (
        sb.from_("staff_break_preferences")
        .select("day_of_week, unpaid_break_opt_out, effective_from, effective_until")
        .eq("user_id",   user_id)
        .eq("centre_id", centre_id)
        .lte("effective_from", today)
        .order("effective_from", desc=True)   # most recent first
        .execute()
    ).data or []

    # For each day_of_week, use the most recent effective row that hasn't expired.
    result: dict[int, bool] = {}
    for row in rows:
        dow     = row.get("day_of_week")
        until   = row.get("effective_until")
        if dow is None:
            continue
        # Skip if already found a more recent preference for this day
        if dow in result:
            continue
        # Skip if this preference has expired
        if until and until < today:
            continue
        result[dow] = bool(row.get("unpaid_break_opt_out", False))

    return result


def fetch_break_prefs_for_centre(
    centre_id: str,
    as_of: str | None = None,
) -> dict[str, dict[int, bool]]:
    """
    Return {user_id: {day_of_week: unpaid_break_opt_out}} for all staff
    at a centre, effective as_of the given date.

    Used by the roster builder to bulk-load prefs without N queries.
    """
    sb    = get_supabase_client()
    today = as_of or date.today().isoformat()

    rows = (
        sb.from_("staff_break_preferences")
        .select("user_id, day_of_week, unpaid_break_opt_out, effective_from, effective_until")
        .eq("centre_id", centre_id)
        .lte("effective_from", today)
        .order("effective_from", desc=True)
        .execute()
    ).data or []

    result: dict[str, dict[int, bool]] = {}
    for row in rows:
        uid   = row.get("user_id", "")
        dow   = row.get("day_of_week")
        until = row.get("effective_until")
        if not uid or dow is None:
            continue
        if uid not in result:
            result[uid] = {}
        if dow in result[uid]:
            continue     # already have a more recent row for this day
        if until and until < today:
            continue     # expired
        result[uid][dow] = bool(row.get("unpaid_break_opt_out", False))

    return result


def upsert_break_pref(
    user_id: str,
    centre_id: str,
    day_of_week: int,
    unpaid_break_opt_out: bool,
    effective_from: str | None = None,
    effective_until: str | None = None,
    notes: str = "",
) -> dict:
    """
    Insert or update a break preference for one user+centre+day_of_week
    as of effective_from (default today).

    Uses the unique constraint (user_id, centre_id, day_of_week, effective_from)
    to upsert without .single().
    """
    sb  = get_supabase_client()
    eff = effective_from or date.today().isoformat()

    # Check for existing row matching the unique key
    existing = (
        sb.from_("staff_break_preferences")
        .select("id")
        .eq("user_id",        user_id)
        .eq("centre_id",      centre_id)
        .eq("day_of_week",    day_of_week)
        .eq("effective_from", eff)
        .limit(1)
        .execute()
    ).data or []

    if existing:
        row_id = existing[0]["id"]
        result = _one(
            sb.from_("staff_break_preferences")
            .update({
                "unpaid_break_opt_out": unpaid_break_opt_out,
                "effective_until":      effective_until or None,
                "notes":                notes.strip() or None,
            })
            .eq("id", row_id)
            .select()
            .execute()
        )
    else:
        result = _one(
            sb.from_("staff_break_preferences")
            .insert({
                "user_id":              user_id,
                "centre_id":            centre_id,
                "day_of_week":          day_of_week,
                "unpaid_break_opt_out": unpaid_break_opt_out,
                "effective_from":       eff,
                "effective_until":      effective_until or None,
                "notes":                notes.strip() or None,
            })
            .select()
            .execute()
        )

    if not result:
        raise ValueError(
            f"Break preference for day {day_of_week} could not be saved."
        )
    return result


def upsert_break_prefs_bulk(
    user_id: str,
    centre_id: str,
    prefs: dict[int, bool],
    effective_from: str | None = None,
    notes: str = "",
) -> int:
    """
    Save break preferences for multiple weekdays at once.
    prefs: {day_of_week: unpaid_break_opt_out}
    Returns count of rows saved.
    """
    saved = 0
    for dow, opt_out in prefs.items():
        upsert_break_pref(
            user_id=user_id,
            centre_id=centre_id,
            day_of_week=dow,
            unpaid_break_opt_out=opt_out,
            effective_from=effective_from,
            notes=notes,
        )
        saved += 1
    return saved


def delete_break_pref(pref_id: str) -> None:
    """Hard delete one break preference row."""
    sb = get_supabase_client()
    sb.from_("staff_break_preferences").delete().eq("id", pref_id).execute()


def fetch_all_break_prefs_for_user(
    user_id: str,
    centre_id: str,
) -> list[dict]:
    """All break preference rows for a user, newest first. Used by the UI."""
    sb = get_supabase_client()
    return (
        sb.from_("staff_break_preferences")
        .select("id, day_of_week, unpaid_break_opt_out, effective_from, effective_until, notes")
        .eq("user_id",   user_id)
        .eq("centre_id", centre_id)
        .order("day_of_week")
        .order("effective_from", desc=True)
        .execute()
    ).data or []
