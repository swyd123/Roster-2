# components/staff_form.py
# Shared form used by both Add Staff and Edit Staff (profile) pages.

import streamlit as st
from datetime import date
from utils.helpers import (
    EMPLOYMENT_TYPE_KEYS, EMPLOYMENT_TYPES,
    ROLE_KEYS, ROLES,
)
from utils.staff_queries import fetch_centres, fetch_rooms_for_centre


def staff_form(key_prefix: str, defaults: dict | None = None,
               show_role_fields: bool = True) -> dict | None:
    """
    Renders the staff creation/edit form.
    Returns form values dict on submit, None otherwise.

    Parameters
    ----------
    key_prefix       : unique string to namespace widget keys ("add" or "edit")
    defaults         : existing staff record for pre-filling (edit mode).
                       None = all fields empty (add mode).
    show_role_fields : show centre / role / room dropdowns (True for add and edit).
    """
    d    = defaults or {}
    user = d.get("users") or {}

    # Pre-load centres for the role assignment dropdown
    centres     = fetch_centres() if show_role_fields else []
    centre_opts = {c["id"]: c["name"] for c in centres}

    # Extract current centre/role from the first active user_centre_roles row
    current_roles    = [r for r in (d.get("user_centre_roles") or []) if r.get("is_active")]
    current_role_row = current_roles[0] if current_roles else {}
    current_centre_id  = current_role_row.get("centre_id") or ""
    current_role_val   = current_role_row.get("role") or "educator"
    current_room_id    = current_role_row.get("primary_room_id") or ""

    with st.form(key=f"{key_prefix}_form", clear_on_submit=False):

        # ── Section 1 · Personal details ──────────────────────────
        st.markdown('<p class="section-label">Personal Details</p>', unsafe_allow_html=True)
        c1, c2 = st.columns(2)
        first_name = c1.text_input(
            "First name *", value=user.get("first_name", ""), key=f"{key_prefix}_fn"
        )
        last_name = c2.text_input(
            "Last name *", value=user.get("last_name", ""), key=f"{key_prefix}_ln"
        )

        c3, c4 = st.columns(2)
        email = c3.text_input(
            "Email address *", value=user.get("email", ""), key=f"{key_prefix}_em",
            help="Used for login. Must be unique.",
        )
        phone = c4.text_input(
            "Mobile phone", value=user.get("phone", "") or "", key=f"{key_prefix}_ph",
            placeholder="0412 345 678",
        )

        raw_dob = d.get("date_of_birth")
        dob_default = date.fromisoformat(raw_dob[:10]) if raw_dob else None
        date_of_birth = st.date_input(
            "Date of birth", value=dob_default,
            key=f"{key_prefix}_dob", format="DD/MM/YYYY",
        )

        st.divider()

        # ── Section 2 · Employment details ────────────────────────
        st.markdown('<p class="section-label">Employment Details</p>', unsafe_allow_html=True)
        c5, c6 = st.columns(2)
        current_et = d.get("employment_type", "casual")
        et_idx     = EMPLOYMENT_TYPE_KEYS.index(current_et) if current_et in EMPLOYMENT_TYPE_KEYS else 0
        employment_type = c5.selectbox(
            "Employment type *", options=EMPLOYMENT_TYPE_KEYS,
            index=et_idx, format_func=lambda x: EMPLOYMENT_TYPES[x],
            key=f"{key_prefix}_et",
        )
        employee_number = c6.text_input(
            "Employee number", value=d.get("employee_number", "") or "",
            key=f"{key_prefix}_enum", placeholder="e.g. EMP-001",
        )

        # Contracted hours
        c7, c8 = st.columns(2)
        contracted_default = {"full_time": 38.0, "part_time": 0.0, "casual": 0.0}.get(
            employment_type, 0.0
        )
        contracted_hours_per_week = c7.number_input(
            "Contracted hours / week",
            min_value=0.0, max_value=80.0, step=0.5, format="%.1f",
            value=float(
                d.get("contracted_hours_per_week")
                or d.get("full_time_contracted_hours_per_week")
                or contracted_default
            ),
            key=f"{key_prefix}_contracted_hrs",
            help="Weekly contracted hours. Full-time default: 38h. Leave as 0 for casual.",
        )
        c8.markdown("")  # spacer

        raw_start = d.get("employment_start_date")
        start_def = date.fromisoformat(raw_start[:10]) if raw_start else date.today()
        employment_start_date = st.date_input(
            "Start date *", value=start_def,
            key=f"{key_prefix}_sd", format="DD/MM/YYYY",
        )

        # ── Section 2b · Centre & role (shown in both add and edit) ──
        centre_id       = current_centre_id or None
        role            = current_role_val
        primary_room_id = current_room_id or None

        if show_role_fields:
            st.divider()
            st.markdown('<p class="section-label">Centre & Role Assignment</p>',
                        unsafe_allow_html=True)

            if not centres:
                st.warning("⚠️ No centres found. Create a centre before adding staff.")
            else:
                cr1, cr2 = st.columns(2)
                centre_keys    = list(centre_opts.keys())
                centre_default = (
                    centre_keys.index(current_centre_id)
                    if current_centre_id in centre_keys else 0
                )
                selected_centre = cr1.selectbox(
                    "Centre *", options=centre_keys,
                    index=centre_default,
                    format_func=lambda x: centre_opts[x],
                    key=f"{key_prefix}_centre",
                )
                centre_id = selected_centre

                role_idx = (
                    ROLE_KEYS.index(current_role_val)
                    if current_role_val in ROLE_KEYS
                    else ROLE_KEYS.index("educator")
                )
                role = cr2.selectbox(
                    "Role at this centre *", options=ROLE_KEYS,
                    index=role_idx, format_func=lambda x: ROLES[x],
                    key=f"{key_prefix}_role",
                )

                rooms     = fetch_rooms_for_centre(selected_centre) if selected_centre else []
                room_opts = {"": "— No primary room —"}
                room_opts.update({r["id"]: r["name"] for r in rooms})
                room_keys    = list(room_opts.keys())
                room_default = (
                    room_keys.index(current_room_id)
                    if current_room_id in room_keys else 0
                )
                primary_room_id = st.selectbox(
                    "Primary room", options=room_keys,
                    index=room_default,
                    format_func=lambda x: room_opts[x],
                    key=f"{key_prefix}_room",
                ) or None

        st.divider()

        # ── Section 3 · Emergency contact ─────────────────────────
        st.markdown('<p class="section-label">Emergency Contact</p>', unsafe_allow_html=True)
        c9, c10 = st.columns(2)
        ec_name  = c9.text_input(
            "Contact name", value=d.get("emergency_contact_name", "") or "",
            key=f"{key_prefix}_ecn",
        )
        ec_phone = c10.text_input(
            "Contact phone", value=d.get("emergency_contact_phone", "") or "",
            key=f"{key_prefix}_ecp",
        )
        ec_rel = st.text_input(
            "Relationship", value=d.get("emergency_contact_relationship", "") or "",
            key=f"{key_prefix}_ecr", placeholder="e.g. Spouse, Parent",
        )

        st.divider()

        # ── Section 4 · Compliance roles ──────────────────────────
        st.markdown('<p class="section-label">Compliance Roles</p>', unsafe_allow_html=True)
        st.caption(
            "Nominated Supervisors and Responsible Persons are required onsite at all times "
            "under the Education and Care Services National Law. "
            "The auto-roster enforces this as a hard constraint."
        )
        cc1, cc2 = st.columns(2)
        is_nominated_supervisor = cc1.checkbox(
            "Nominated Supervisor",
            value=bool(d.get("is_nominated_supervisor", False)),
            key=f"{key_prefix}_is_ns",
            help=(
                "This educator is a Nominated Supervisor under the Education "
                "and Care Services National Law/Regulations. At least one Nominated "
                "Supervisor or Responsible Person must be onsite at all times."
            ),
        )
        is_responsible_person = cc2.checkbox(
            "Responsible Person",
            value=bool(d.get("is_responsible_person", False)),
            key=f"{key_prefix}_is_rp",
            help=(
                "This educator may be designated as the Responsible Person "
                "in charge when onsite. Required at all times during operating hours."
            ),
        )

        st.divider()

        # ── Section 5 · Status + settings ─────────────────────────
        s1, s2 = st.columns(2)
        is_active = s1.toggle(
            "Account active", value=user.get("is_active", True),
            key=f"{key_prefix}_active",
            help="Inactive staff cannot log in and are hidden from rosters.",
        )
        allows_opt_out = s2.toggle(
            "Allow unpaid break opt-out",
            value=bool(d.get("allows_unpaid_break_opt_out", False)),
            key=f"{key_prefix}_allows_opt_out",
            help=(
                "When enabled, a manager may opt this educator out of the "
                "30-minute unpaid meal break on individual shifts. "
                "Only enable if permitted by the applicable award or agreement."
            ),
        )

        notes = st.text_area(
            "Internal notes (not shown to staff member)",
            value=d.get("notes", "") or "",
            key=f"{key_prefix}_notes",
            height=90,
            placeholder="e.g. Preferred hours, special arrangements…",
        )

        submitted = st.form_submit_button(
            "💾  Save Staff Member", use_container_width=True, type="primary"
        )

    if not submitted:
        return None

    # ── Validation ─────────────────────────────────────────────────
    errors = []
    if not first_name.strip():
        errors.append("First name is required.")
    if not last_name.strip():
        errors.append("Last name is required.")
    if not email.strip():
        errors.append("Email address is required.")
    elif "@" not in email:
        errors.append("Email address is not valid.")
    if show_role_fields and not centres:
        errors.append("No centres exist — create a centre before adding staff.")
    elif show_role_fields and not centre_id:
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
        "contracted_hours_per_week":        contracted_hours_per_week,
        "centre_id":                        centre_id,
        "role":                             role,
        "primary_room_id":                  primary_room_id,
        "emergency_contact_name":           ec_name.strip(),
        "emergency_contact_phone":          ec_phone.strip(),
        "emergency_contact_relationship":   ec_rel.strip(),
        "notes":                            notes.strip(),
        "is_active":                        is_active,
        "allows_unpaid_break_opt_out":      allows_opt_out,
        "is_nominated_supervisor":          is_nominated_supervisor,
        "is_responsible_person":            is_responsible_person,
    }
