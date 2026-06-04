# components/staff_form.py
# Shared form used by Add Staff page and the Edit tab on the staff profile.
# show_role_fields=True   → shows centre / role / room dropdowns (add + edit)
# show_role_fields=False  → hides them (legacy path, not used)

import streamlit as st
from datetime import date
from utils.helpers import (
    EMPLOYMENT_TYPE_KEYS, EMPLOYMENT_TYPES,
    ROLE_KEYS, ROLES,
)
from utils.staff_queries import fetch_centres, fetch_rooms_for_centre


def staff_form(
    key_prefix: str,
    defaults: dict | None = None,
    show_role_fields: bool = True,
) -> dict | None:
    """
    Renders the staff creation / edit form.

    Returns a dict of values on submit, or None if not yet submitted.

    Parameters
    ----------
    key_prefix       : unique string to namespace widget keys ("add" or "edit")
    defaults         : existing staff record for pre-filling (edit mode).
                       None = all fields empty (add mode).
    show_role_fields : always True — centre + role assignment is required for
                       both creating and editing staff. Kept as a parameter for
                       backwards compatibility.
    """
    d    = defaults or {}
    user = d.get("users") or {}

    # Extract current centre/role from the first active user_centre_roles row
    current_roles    = [r for r in (d.get("user_centre_roles") or []) if r.get("is_active")]
    current_role_row = current_roles[0] if current_roles else {}
    current_centre_id   = current_role_row.get("centre_id") or ""
    current_role        = current_role_row.get("role") or "educator"
    current_room_id     = current_role_row.get("primary_room_id") or ""

    # Load centres for the dropdown
    centres     = fetch_centres()
    centre_opts = {c["id"]: c["name"] for c in centres}

    with st.form(key=f"{key_prefix}_form", clear_on_submit=False):

        # ── Section 1 · Personal details ──────────────────────────────
        st.markdown('<p class="section-label">Personal Details</p>', unsafe_allow_html=True)
        c1, c2 = st.columns(2)
        first_name = c1.text_input(
            "First name *",
            value=user.get("first_name", ""),
            key=f"{key_prefix}_fn",
        )
        last_name = c2.text_input(
            "Last name *",
            value=user.get("last_name", ""),
            key=f"{key_prefix}_ln",
        )

        c3, c4 = st.columns(2)
        email = c3.text_input(
            "Email address *",
            value=user.get("email", ""),
            key=f"{key_prefix}_em",
            help="Used for login. Must be unique.",
        )
        phone = c4.text_input(
            "Mobile phone",
            value=user.get("phone", "") or "",
            key=f"{key_prefix}_ph",
            placeholder="0412 345 678",
        )

        raw_dob     = d.get("date_of_birth")
        dob_default = date.fromisoformat(raw_dob[:10]) if raw_dob else None
        date_of_birth = st.date_input(
            "Date of birth",
            value=dob_default,
            key=f"{key_prefix}_dob",
            format="DD/MM/YYYY",
        )

        st.divider()

        # ── Section 2 · Employment details ────────────────────────────
        st.markdown('<p class="section-label">Employment Details</p>', unsafe_allow_html=True)
        c5, c6 = st.columns(2)
        current_et = d.get("employment_type", "casual")
        et_idx     = EMPLOYMENT_TYPE_KEYS.index(current_et) if current_et in EMPLOYMENT_TYPE_KEYS else 0
        employment_type = c5.selectbox(
            "Employment type *",
            options=EMPLOYMENT_TYPE_KEYS,
            index=et_idx,
            format_func=lambda x: EMPLOYMENT_TYPES[x],
            key=f"{key_prefix}_et",
        )
        employee_number = c6.text_input(
            "Employee number",
            value=d.get("employee_number", "") or "",
            key=f"{key_prefix}_enum",
            placeholder="e.g. EMP-001",
        )

        raw_start = d.get("employment_start_date")
        start_def = date.fromisoformat(raw_start[:10]) if raw_start else date.today()
        employment_start_date = st.date_input(
            "Start date *",
            value=start_def,
            key=f"{key_prefix}_sd",
            format="DD/MM/YYYY",
        )

        st.divider()

        # ── Section 3 · Centre & role assignment ──────────────────────
        st.markdown('<p class="section-label">Centre & Role Assignment</p>', unsafe_allow_html=True)

        if not centres:
            st.warning(
                "⚠️ No centres found. You must create a centre before adding staff."
            )
            centre_id       = ""
            role            = "educator"
            primary_room_id = None
        else:
            c7, c8 = st.columns(2)

            # Centre selector — pre-fill with existing centre in edit mode
            centre_keys     = list(centre_opts.keys())
            centre_default  = (
                centre_keys.index(current_centre_id)
                if current_centre_id in centre_keys
                else 0
            )
            selected_centre = c7.selectbox(
                "Centre *",
                options=centre_keys,
                index=centre_default,
                format_func=lambda x: centre_opts[x],
                key=f"{key_prefix}_centre",
            )
            centre_id = selected_centre

            # Role selector — pre-fill with existing role in edit mode
            role_idx = (
                ROLE_KEYS.index(current_role)
                if current_role in ROLE_KEYS
                else ROLE_KEYS.index("educator")
            )
            role = c8.selectbox(
                "Role at this centre *",
                options=ROLE_KEYS,
                index=role_idx,
                format_func=lambda x: ROLES[x],
                key=f"{key_prefix}_role",
            )

            # Room selector — reloads based on selected centre
            rooms     = fetch_rooms_for_centre(selected_centre) if selected_centre else []
            room_opts = {"": "— No primary room —"}
            room_opts.update({r["id"]: r["name"] for r in rooms})

            room_keys    = list(room_opts.keys())
            room_default = (
                room_keys.index(current_room_id)
                if current_room_id in room_keys
                else 0
            )
            selected_room = st.selectbox(
                "Primary room",
                options=room_keys,
                index=room_default,
                format_func=lambda x: room_opts[x],
                key=f"{key_prefix}_room",
            )
            primary_room_id = selected_room or None

        st.divider()

        # ── Section 4 · Emergency contact ─────────────────────────────
        st.markdown('<p class="section-label">Emergency Contact</p>', unsafe_allow_html=True)
        c9, c10 = st.columns(2)
        ec_name = c9.text_input(
            "Contact name",
            value=d.get("emergency_contact_name", "") or "",
            key=f"{key_prefix}_ecn",
        )
        ec_phone = c10.text_input(
            "Contact phone",
            value=d.get("emergency_contact_phone", "") or "",
            key=f"{key_prefix}_ecp",
        )
        ec_rel = st.text_input(
            "Relationship",
            value=d.get("emergency_contact_relationship", "") or "",
            key=f"{key_prefix}_ecr",
            placeholder="e.g. Spouse, Parent",
        )

        st.divider()

        # ── Section 5 · Account status + notes ────────────────────────
        c11, c12, _ = st.columns([1, 2, 1])
        is_active = c11.toggle(
            "Account active",
            value=user.get("is_active", True),
            key=f"{key_prefix}_active",
            help="Inactive staff cannot log in and are hidden from rosters.",
        )
        allows_opt_out = c12.toggle(
            "Allow unpaid break opt-out",
            value=bool(d.get("allows_unpaid_break_opt_out", False)),
            key=f"{key_prefix}_allows_opt_out",
            help=(
                "When enabled, a manager may opt this educator out of the "
                "30-minute unpaid meal break on individual shifts. "
                "Paid rest break entitlement is always retained. "
                "Only enable if permitted by the applicable award, enterprise "
                "agreement, or individual employment agreement."
            ),
        )
        if allows_opt_out and not d.get("allows_unpaid_break_opt_out", False):
            st.warning(
                "⚠️ **Confirm this complies with the applicable award/enterprise "
                "agreement and employee agreement** before enabling unpaid break opt-out."
            )
        notes = st.text_area(
            "Internal notes (not shown to staff member)",
            value=d.get("notes", "") or "",
            key=f"{key_prefix}_notes",
            height=90,
            placeholder="e.g. Preferred hours, special arrangements…",
        )

        submitted = st.form_submit_button(
            "💾  Save Staff Member",
            use_container_width=True,
            type="primary",
        )

    if not submitted:
        return None

    # ── Validation ────────────────────────────────────────────────────
    errors = []
    if not first_name.strip():
        errors.append("First name is required.")
    if not last_name.strip():
        errors.append("Last name is required.")
    if not email.strip():
        errors.append("Email address is required.")
    elif "@" not in email:
        errors.append("Email address is not valid.")
    if not centres:
        errors.append("No centres exist — create a centre before adding staff.")
    elif not centre_id:
        errors.append("Centre assignment is required.")

    for e in errors:
        st.error(f"❌ {e}")
    if errors:
        return None

    return {
        "first_name":                       first_name.strip(),
        "last_name":                        last_name.strip(),
        "email":                            email.strip().lower(),
        "phone":                            phone.strip(),
        "date_of_birth":                    date_of_birth.isoformat() if date_of_birth else None,
        "employment_type":                  employment_type,
        "employment_start_date":            employment_start_date.isoformat(),
        "employee_number":                  employee_number.strip(),
        "centre_id":                        centre_id,
        "role":                             role,
        "primary_room_id":                  primary_room_id,
        "emergency_contact_name":           ec_name.strip(),
        "emergency_contact_phone":          ec_phone.strip(),
        "emergency_contact_relationship":   ec_rel.strip(),
        "notes":                            notes.strip(),
        "is_active":                        is_active,
        "allows_unpaid_break_opt_out":      allows_opt_out,
    }
