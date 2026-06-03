# utils/child_queries.py
# Database queries for creating and updating children from CSV import.
# No .single() anywhere.

from __future__ import annotations
from datetime import date
from utils.supabase_client import get_supabase_client


# ─────────────────────────────────────────────────────────────────────────────
# FETCH helpers
# ─────────────────────────────────────────────────────────────────────────────

def fetch_existing_children(centre_id: str) -> dict[str, dict]:
    """
    Return a lookup of existing non-deleted children for this centre.

    Key: normalised full name — lower-cased, whitespace-collapsed.
    Value: the full child row dict.

    Used by upsert_children_from_csv to match incoming CSV names without
    a round-trip per row.
    """
    sb   = get_supabase_client()
    rows = (
        sb.from_("children")
        .select("id, first_name, last_name, room_id, enrolment_status, date_of_birth")
        .eq("centre_id", centre_id)
        .is_("deleted_at", "null")
        .execute()
    ).data or []

    lookup: dict[str, dict] = {}
    for row in rows:
        key = _name_key(row.get("first_name", ""), row.get("last_name", ""))
        lookup[key] = row
    return lookup


# ─────────────────────────────────────────────────────────────────────────────
# UPSERT — called after CSV parse, before attendance save
# ─────────────────────────────────────────────────────────────────────────────

def upsert_children_from_csv(
    child_records: list[dict],
    centre_id: str,
) -> dict:
    """
    Create or update child records from a CSV attendance import.

    child_records — list of dicts from csv_attendance_import.extract_child_records():
        child_name   str   full name as it appeared in CSV
        first_name   str
        last_name    str
        room_id      str   UUID of the matched room
        room_name    str   display name
        date_of_birth str|None   ISO date or None

    Match key: normalised (lower-case, whitespace-collapsed) full name
    within the same centre.

    INSERT when no match exists.
    UPDATE room_id and enrolment_status='active' when a match exists
    (room may have changed; status may have been withdrawn/suspended).
    DOB is only written when:
      - inserting a new row (None → NULL, real date → stored)
      - updating an existing row whose stored DOB is NULL (placeholder fill-in)

    Returns {added, updated, skipped, errors}.
    No .single() — uses list queries and individual insert/update calls.
    """
    sb    = get_supabase_client()
    today = date.today().isoformat()

    added   = 0
    updated = 0
    skipped = 0
    errors: list[str] = []

    # Load all existing children once — avoids N SELECT calls
    existing = fetch_existing_children(centre_id)

    for rec in child_records:
        first = rec["first_name"]
        last  = rec["last_name"]
        key   = _name_key(first, last)

        try:
            if key in existing:
                # ── UPDATE ────────────────────────────────────────────────
                child_id    = existing[key]["id"]
                stored_dob  = existing[key].get("date_of_birth")

                payload: dict = {
                    "room_id":          rec["room_id"],
                    "enrolment_status": "active",
                }

                # Fill in DOB only when we have a real one and stored is NULL
                if rec.get("date_of_birth") and stored_dob is None:
                    payload["date_of_birth"] = rec["date_of_birth"]

                sb.from_("children") \
                  .update(payload) \
                  .eq("id", child_id) \
                  .execute()
                updated += 1

            else:
                # ── INSERT ────────────────────────────────────────────────
                sb.from_("children").insert({
                    "centre_id":           centre_id,
                    "room_id":             rec["room_id"],
                    "first_name":          first,
                    "last_name":           last,
                    "date_of_birth":       rec.get("date_of_birth"),  # may be NULL
                    "enrolment_status":    "active",
                    "enrolment_start_date": today,
                    "gender":              "not_specified",
                }).execute()
                added += 1

                # Add to local lookup so duplicate names in the same CSV
                # don't trigger duplicate inserts
                existing[key] = {
                    "id":               "__new__",
                    "first_name":       first,
                    "last_name":        last,
                    "room_id":          rec["room_id"],
                    "enrolment_status": "active",
                    "date_of_birth":    rec.get("date_of_birth"),
                }

        except Exception as exc:
            errors.append(f"{rec['child_name']} ({rec['room_name']}): {exc}")
            skipped += 1

    return {
        "added":   added,
        "updated": updated,
        "skipped": skipped,
        "errors":  errors,
    }


# ─────────────────────────────────────────────────────────────────────────────
# PRIVATE HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _name_key(first: str, last: str) -> str:
    """Normalised match key: lower-case, whitespace-collapsed full name."""
    return " ".join(f"{first} {last}".lower().split())
