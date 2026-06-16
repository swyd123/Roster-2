# utils/staff_queries.py
# All database queries for the Staff Management module.

from __future__ import annotations
from typing import Optional
from datetime import datetime, timezone
from utils.supabase_client import get_supabase_client, get_organisation_id


# ─────────────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────────────

def _one(resp) -> Optional[dict]:
    """Return the first row from a Supabase response, or None."""
    data = resp.data if hasattr(resp, "data") else resp
    if isinstance(data, list):
        return data[0] if data else None
    return data or None


# ─────────────────────────────────────────────────────────────────────────────
# STAFF — core list & profile
# ─────────────────────────────────────────────────────────────────────────────

def fetch_all_staff() -> list[dict]:
    """All active staff for this organisation with user + role data."""
    sb     = get_supabase_client()
    org_id = get_organisation_id()

    resp = (
        sb.from_("staff_profiles")
        .select(
            "id, employee_number, employment_type, employment_start_date,"
            "employment_end_date, notes, organisation_id, allows_unpaid_break_opt_out,"
            "contracted_hours_per_week, is_responsible_person, is_nominated_supervisor,"
            "users!staff_profiles_user_id_fkey("
            "  id, first_name, last_name, email, phone, is_active, created_at"
            "),"
            "user_centre_roles!user_centre_roles_user_id_fkey("
            "  role, primary_room_id, centre_id, is_active,"
            "  centres!user_centre_roles_centre_id_fkey(id, name),"
            "  rooms!user_centre_roles_primary_room_id_fkey(id, name)"
            ")"
        )
        .eq("organisation_id", org_id)
        .is_("deleted_at", "null")
        .order("id")
        .execute()
    )
    return resp.data or []


def fetch_staff_by_id(profile_id: str) -> Optional[dict]:
    """Full record for one staff member including qualifications summary."""
    sb = get_supabase_client()

    resp = (
        sb.from_("staff_profiles")
        .select(
            "id, employee_number, employment_type, employment_start_date,"
            "employment_end_date, date_of_birth, super_fund_name, super_member_number,"
            "emergency_contact_name, emergency_contact_phone,"
            "emergency_contact_relationship, notes, organisation_id,"
            "allows_unpaid_break_opt_out, contracted_hours_per_week,"
            "is_responsible_person, is_nominated_supervisor,"
            "users!staff_profiles_user_id_fkey("
            "  id, first_name, last_name, email, phone, is_active"
            "),"
            "user_centre_roles!user_centre_roles_user_id_fkey("
            "  role, primary_room_id, centre_id, is_active,"
            "  centres!user_centre_roles_centre_id_fkey(id, name),"
            "  rooms!user_centre_roles_primary_room_id_fkey(id, name)"
            ")"
        )
        .eq("id", profile_id)
        .is_("deleted_at", "null")
        .limit(1)
        .execute()
    )
    return _one(resp)


# ─────────────────────────────────────────────────────────────────────────────
# STAFF — create / update / delete
# ─────────────────────────────────────────────────────────────────────────────

def create_staff_member(
    first_name: str, last_name: str, email: str, phone: str,
    date_of_birth: str | None,
    employment_type: str, employment_start_date: str | None,
    employee_number: str,
    centre_id: str, role: str, primary_room_id: str | None,
    emergency_contact_name: str, emergency_contact_phone: str,
    emergency_contact_relationship: str, notes: str,
    contracted_hours_per_week: float = 0.0,
    allows_unpaid_break_opt_out: bool = False,
    is_responsible_person: bool = False,
    is_nominated_supervisor: bool = False,
) -> dict:
    sb     = get_supabase_client()
    org_id = get_organisation_id()

    # 1 — create user account
    u = _one(
        sb.from_("users")
        .insert({
            "first_name": first_name.strip(),
            "last_name":  last_name.strip(),
            "email":      email.strip().lower(),
            "phone":      phone.strip() or None,
            "is_active":  True,
        })
        .select()
        .execute()
    )
    if not u:
        raise ValueError("User account could not be created.")

    # 2 — create staff profile
    profile = _one(
        sb.from_("staff_profiles")
        .insert({
            "user_id":                        u["id"],
            "organisation_id":                org_id,
            "employee_number":                employee_number.strip() or None,
            "employment_type":                employment_type,
            "employment_start_date":          employment_start_date or None,
            "date_of_birth":                  date_of_birth or None,
            "contracted_hours_per_week":      contracted_hours_per_week,
            "allows_unpaid_break_opt_out":    allows_unpaid_break_opt_out,
            "is_responsible_person":          is_responsible_person,
            "is_nominated_supervisor":        is_nominated_supervisor,
            "emergency_contact_name":         emergency_contact_name.strip() or None,
            "emergency_contact_phone":        emergency_contact_phone.strip() or None,
            "emergency_contact_relationship": emergency_contact_relationship.strip() or None,
            "notes":                          notes.strip() or None,
        })
        .select()
        .execute()
    )

    # 3 — assign centre role
    if centre_id:
        sb.from_("user_centre_roles").insert({
            "user_id":         u["id"],
            "centre_id":       centre_id,
            "role":            role,
            "primary_room_id": primary_room_id or None,
            "is_active":       True,
        }).execute()

    return profile


def update_staff_member(
    profile_id: str, user_id: str,
    first_name: str, last_name: str, email: str, phone: str,
    date_of_birth: str | None,
    employment_type: str, employment_start_date: str | None,
    employee_number: str,
    emergency_contact_name: str, emergency_contact_phone: str,
    emergency_contact_relationship: str, notes: str,
    is_active: bool,
    contracted_hours_per_week: float = 0.0,
    allows_unpaid_break_opt_out: bool = False,
    is_responsible_person: bool = False,
    is_nominated_supervisor: bool = False,
) -> dict:
    sb = get_supabase_client()

    sb.from_("users").update({
        "first_name": first_name.strip(),
        "last_name":  last_name.strip(),
        "email":      email.strip().lower(),
        "phone":      phone.strip() or None,
        "is_active":  is_active,
    }).eq("id", user_id).execute()

    profile = _one(
        sb.from_("staff_profiles")
        .update({
            "employee_number":                employee_number.strip() or None,
            "employment_type":                employment_type,
            "employment_start_date":          employment_start_date or None,
            "date_of_birth":                  date_of_birth or None,
            "contracted_hours_per_week":      contracted_hours_per_week,
            "allows_unpaid_break_opt_out":    allows_unpaid_break_opt_out,
            "is_responsible_person":          is_responsible_person,
            "is_nominated_supervisor":        is_nominated_supervisor,
            "emergency_contact_name":         emergency_contact_name.strip() or None,
            "emergency_contact_phone":        emergency_contact_phone.strip() or None,
            "emergency_contact_relationship": emergency_contact_relationship.strip() or None,
            "notes":                          notes.strip() or None,
        })
        .eq("id", profile_id)
        .select()
        .execute()
    )
    return profile


def upsert_centre_role(
    user_id: str, centre_id: str, role: str, primary_room_id: str | None,
) -> None:
    """Insert or update the user_centre_roles row for a staff member at a centre."""
    sb = get_supabase_client()
    existing = (
        sb.from_("user_centre_roles")
        .select("id")
        .eq("user_id", user_id)
        .eq("centre_id", centre_id)
        .is_("deleted_at", "null")
        .limit(1)
        .execute()
    ).data or []

    if existing:
        sb.from_("user_centre_roles").update({
            "role":            role,
            "primary_room_id": primary_room_id or None,
            "is_active":       True,
        }).eq("id", existing[0]["id"]).execute()
    else:
        sb.from_("user_centre_roles").insert({
            "user_id":         user_id,
            "centre_id":       centre_id,
            "role":            role,
            "primary_room_id": primary_room_id or None,
            "is_active":       True,
        }).execute()


def soft_delete_staff(profile_id: str, user_id: str) -> None:
    sb  = get_supabase_client()
    now = datetime.now(timezone.utc).isoformat()
    sb.from_("staff_profiles").update({"deleted_at": now}).eq("id", profile_id).execute()
    sb.from_("users").update({"deleted_at": now, "is_active": False}).eq("id", user_id).execute()


# ─────────────────────────────────────────────────────────────────────────────
# CENTRES & ROOMS  (used in dropdowns)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_centres() -> list[dict]:
    sb     = get_supabase_client()
    org_id = get_organisation_id()
    return (
        sb.from_("centres")
        .select("id, name")
        .eq("organisation_id", org_id)
        .is_("deleted_at", "null")
        .order("name")
        .execute()
    ).data or []


def fetch_rooms_for_centre(centre_id: str) -> list[dict]:
    sb = get_supabase_client()
    return (
        sb.from_("rooms")
        .select("id, name, colour")
        .eq("centre_id", centre_id)
        .is_("deleted_at", "null")
        .eq("is_active", True)
        .order("sort_order")
        .execute()
    ).data or []


# ─────────────────────────────────────────────────────────────────────────────
# QUALIFICATIONS
# ─────────────────────────────────────────────────────────────────────────────

def fetch_qualifications_for_staff(profile_id: str) -> list[dict]:
    sb = get_supabase_client()
    return (
        sb.from_("staff_qualifications")
        .select(
            "id, issue_date, expiry_date, issuing_body, certificate_number,"
            "document_url, document_filename, status, notes, verified_at,"
            "qualification_types!staff_qualifications_qualification_type_id_fkey("
            "  id, name, short_name, category, requires_expiry"
            "),"
            "users!staff_qualifications_verified_by_user_id_fkey("
            "  first_name, last_name"
            ")"
        )
        .eq("staff_profile_id", profile_id)
        .is_("deleted_at", "null")
        .order("expiry_date", desc=False, nullsfirst=False)
        .execute()
    ).data or []


def fetch_qualification_types() -> list[dict]:
    sb     = get_supabase_client()
    org_id = get_organisation_id()
    return (
        sb.from_("qualification_types")
        .select("id, name, short_name, category, requires_expiry")
        .or_(f"organisation_id.is.null,organisation_id.eq.{org_id}")
        .eq("is_active", True)
        .order("sort_order")
        .execute()
    ).data or []


def add_qualification(
    profile_id: str, qual_type_id: str,
    issue_date: str | None, expiry_date: str | None,
    issuing_body: str, certificate_number: str,
    document_url: str | None, document_filename: str | None,
    notes: str,
) -> dict:
    sb = get_supabase_client()
    return _one(
        sb.from_("staff_qualifications")
        .insert({
            "staff_profile_id":      profile_id,
            "qualification_type_id": qual_type_id,
            "issue_date":            issue_date or None,
            "expiry_date":           expiry_date or None,
            "issuing_body":          issuing_body.strip() or None,
            "certificate_number":    certificate_number.strip() or None,
            "document_url":          document_url or None,
            "document_filename":     document_filename or None,
            "notes":                 notes.strip() or None,
            "status":                "pending_verification",
        })
        .select()
        .execute()
    )


def update_qualification(
    qual_id: str, issue_date: str | None, expiry_date: str | None,
    issuing_body: str, certificate_number: str, notes: str,
) -> dict:
    sb = get_supabase_client()
    return _one(
        sb.from_("staff_qualifications")
        .update({
            "issue_date":         issue_date or None,
            "expiry_date":        expiry_date or None,
            "issuing_body":       issuing_body.strip() or None,
            "certificate_number": certificate_number.strip() or None,
            "notes":              notes.strip() or None,
        })
        .eq("id", qual_id)
        .select()
        .execute()
    )


def verify_qualification(qual_id: str, verifier_user_id: str) -> None:
    sb  = get_supabase_client()
    now = datetime.now(timezone.utc).isoformat()
    sb.from_("staff_qualifications").update({
        "verified_at":          now,
        "verified_by_user_id":  verifier_user_id,
        "status":               "active",
    }).eq("id", qual_id).execute()


def soft_delete_qualification(qual_id: str) -> None:
    sb  = get_supabase_client()
    now = datetime.now(timezone.utc).isoformat()
    sb.from_("staff_qualifications").update({"deleted_at": now}).eq("id", qual_id).execute()


# ─────────────────────────────────────────────────────────────────────────────
# AVAILABILITY
# ─────────────────────────────────────────────────────────────────────────────

def fetch_availability(user_id: str, centre_id: str) -> list[dict]:
    sb = get_supabase_client()
    return (
        sb.from_("staff_availability")
        .select("*")
        .eq("user_id", user_id)
        .eq("centre_id", centre_id)
        .order("day_of_week")
        .execute()
    ).data or []


def upsert_availability(rows: list[dict]) -> None:
    """Save a list of availability rows (delete then re-insert)."""
    sb = get_supabase_client()
    if not rows:
        return
    user_id   = rows[0]["user_id"]
    centre_id = rows[0]["centre_id"]
    sb.from_("staff_availability").delete().eq("user_id", user_id).eq("centre_id", centre_id).execute()
    sb.from_("staff_availability").insert(rows).execute()


# ─────────────────────────────────────────────────────────────────────────────
# LEAVE REQUESTS
# ─────────────────────────────────────────────────────────────────────────────

def fetch_leave_requests(
    centre_id: str | None = None,
    user_id: str | None = None,
    status_filter: str | None = None,
) -> list[dict]:
    sb     = get_supabase_client()

    q = (
        sb.from_("leave_requests")
        .select(
            "id, leave_type, start_date, end_date, start_time, end_time,"
            "is_partial_day, reason, status, created_at, reviewed_at, review_notes,"
            "users!leave_requests_user_id_fkey(id, first_name, last_name, email),"
            "centres!leave_requests_centre_id_fkey(id, name),"
            "reviewer:users!leave_requests_reviewed_by_user_id_fkey(first_name, last_name)"
        )
    )
    if centre_id:
        q = q.eq("centre_id", centre_id)
    if user_id:
        q = q.eq("user_id", user_id)
    if status_filter and status_filter != "all":
        q = q.eq("status", status_filter)

    return (q.order("created_at", desc=True).execute()).data or []


def fetch_leave_by_id(leave_id: str) -> Optional[dict]:
    sb = get_supabase_client()
    return _one(
        sb.from_("leave_requests")
        .select(
            "id, leave_type, start_date, end_date, start_time, end_time,"
            "is_partial_day, reason, status, created_at, reviewed_at, review_notes,"
            "users!leave_requests_user_id_fkey(id, first_name, last_name, email),"
            "centres!leave_requests_centre_id_fkey(id, name),"
            "reviewer:users!leave_requests_reviewed_by_user_id_fkey(first_name, last_name)"
        )
        .eq("id", leave_id)
        .limit(1)
        .execute()
    )


def create_leave_request(
    user_id: str, centre_id: str, leave_type: str,
    start_date: str, end_date: str, reason: str,
    is_partial_day: bool = False,
    start_time: str | None = None, end_time: str | None = None,
) -> dict:
    sb = get_supabase_client()
    return _one(
        sb.from_("leave_requests")
        .insert({
            "user_id":        user_id,
            "centre_id":      centre_id,
            "leave_type":     leave_type,
            "start_date":     start_date,
            "end_date":       end_date,
            "is_partial_day": is_partial_day,
            "start_time":     start_time or None,
            "end_time":       end_time or None,
            "reason":         reason.strip() or None,
            "status":         "pending",
        })
        .select()
        .execute()
    )


def update_leave_status(
    leave_id: str, new_status: str,
    reviewer_user_id: str, review_notes: str,
) -> None:
    sb  = get_supabase_client()
    now = datetime.now(timezone.utc).isoformat()
    sb.from_("leave_requests").update({
        "status":               new_status,
        "reviewed_by_user_id":  reviewer_user_id,
        "reviewed_at":          now,
        "review_notes":         review_notes.strip() or None,
    }).eq("id", leave_id).execute()
