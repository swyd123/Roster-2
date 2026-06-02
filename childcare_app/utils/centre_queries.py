# utils/centre_queries.py
# All database queries for the Centre Management module.

from __future__ import annotations
from typing import Optional
from datetime import datetime, timezone
from utils.supabase_client import get_supabase_client, get_organisation_id


def _one(resp) -> Optional[dict]:
    """Return the first row from a response, or None."""
    data = resp.data
    if not data:
        return None
    return data[0] if isinstance(data, list) else data


# ─────────────────────────────────────────────────────────────────────────────
# READ
# ─────────────────────────────────────────────────────────────────────────────

def fetch_all_centres() -> list[dict]:
    """
    All centres for this organisation, with a room count included.
    Ordered by name.
    """
    sb     = get_supabase_client()
    org_id = get_organisation_id()

    centres = (
        sb.from_("centres")
        .select(
            "id, name, address_line_1, address_line_2, suburb, state, postcode,"
            "phone, email, licence_number, approved_places,"
            "timezone, opens_at, closes_at, operating_days,"
            "created_at, deleted_at"
        )
        .eq("organisation_id", org_id)
        .is_("deleted_at", "null")
        .order("name")
        .execute()
    ).data or []

    if not centres:
        return []

    # Fetch room counts separately (PostgREST aggregates not available in all versions)
    centre_ids = [c["id"] for c in centres]
    rooms_resp = (
        sb.from_("rooms")
        .select("centre_id")
        .in_("centre_id", centre_ids)
        .is_("deleted_at", "null")
        .eq("is_active", True)
        .execute()
    ).data or []

    room_counts: dict[str, int] = {}
    for row in rooms_resp:
        cid = row["centre_id"]
        room_counts[cid] = room_counts.get(cid, 0) + 1

    for centre in centres:
        centre["room_count"] = room_counts.get(centre["id"], 0)

    return centres


def fetch_centre_by_id(centre_id: str) -> Optional[dict]:
    """Full record for one centre."""
    sb = get_supabase_client()
    return _one(
        sb.from_("centres")
        .select(
            "id, name, address_line_1, address_line_2, suburb, state, postcode,"
            "phone, email, licence_number, approved_places,"
            "timezone, opens_at, closes_at, operating_days, created_at"
        )
        .eq("id", centre_id)
        .is_("deleted_at", "null")
        .limit(1)
        .execute()
    )


# ─────────────────────────────────────────────────────────────────────────────
# CREATE
# ─────────────────────────────────────────────────────────────────────────────

def create_centre(
    name: str,
    address_line_1: str,
    address_line_2: str,
    suburb: str,
    state: str,
    postcode: str,
    phone: str,
    email: str,
    licence_number: str,
    approved_places: int | None,
    timezone: str,
    opens_at: str | None,
    closes_at: str | None,
    operating_days: list[int],
) -> dict:
    """
    Insert a new centre row linked to the current organisation.
    Raises ValueError if the insert returns nothing.
    """
    sb     = get_supabase_client()
    org_id = get_organisation_id()

    result = _one(
        sb.from_("centres")
        .insert({
            "organisation_id": org_id,
            "name":            name.strip(),
            "address_line_1":  address_line_1.strip() or None,
            "address_line_2":  address_line_2.strip() or None,
            "suburb":          suburb.strip() or None,
            "state":           state.strip() or None,
            "postcode":        postcode.strip() or None,
            "phone":           phone.strip() or None,
            "email":           email.strip().lower() or None,
            "licence_number":  licence_number.strip() or None,
            "approved_places": approved_places or None,
            "timezone":        timezone,
            "opens_at":        opens_at or None,
            "closes_at":       closes_at or None,
            "operating_days":  operating_days,
        })
        .select()
        .execute()
    )

    if not result:
        raise ValueError("Centre could not be created. Check that the name is unique.")

    return result


# ─────────────────────────────────────────────────────────────────────────────
# UPDATE
# ─────────────────────────────────────────────────────────────────────────────

def update_centre(
    centre_id: str,
    name: str,
    address_line_1: str,
    address_line_2: str,
    suburb: str,
    state: str,
    postcode: str,
    phone: str,
    email: str,
    licence_number: str,
    approved_places: int | None,
    timezone: str,
    opens_at: str | None,
    closes_at: str | None,
    operating_days: list[int],
) -> dict:
    """Update an existing centre row."""
    sb = get_supabase_client()

    result = _one(
        sb.from_("centres")
        .update({
            "name":            name.strip(),
            "address_line_1":  address_line_1.strip() or None,
            "address_line_2":  address_line_2.strip() or None,
            "suburb":          suburb.strip() or None,
            "state":           state.strip() or None,
            "postcode":        postcode.strip() or None,
            "phone":           phone.strip() or None,
            "email":           email.strip().lower() or None,
            "licence_number":  licence_number.strip() or None,
            "approved_places": approved_places or None,
            "timezone":        timezone,
            "opens_at":        opens_at or None,
            "closes_at":       closes_at or None,
            "operating_days":  operating_days,
        })
        .eq("id", centre_id)
        .select()
        .execute()
    )

    if not result:
        raise ValueError("Centre could not be updated.")

    return result


# ─────────────────────────────────────────────────────────────────────────────
# DELETE
# ─────────────────────────────────────────────────────────────────────────────

def soft_delete_centre(centre_id: str) -> None:
    """
    Soft-delete a centre by setting deleted_at.
    Does not remove any child data (rooms, shifts, etc.).
    """
    sb  = get_supabase_client()
    now = datetime.now(timezone.utc).isoformat()
    sb.from_("centres").update({"deleted_at": now}).eq("id", centre_id).execute()
