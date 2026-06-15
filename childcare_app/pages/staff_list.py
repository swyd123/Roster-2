# pages/staff_list.py
# Staff list — table + add-new form.
# Shows RP/NS compliance role badges alongside name and employment type.

from __future__ import annotations
import streamlit as st
from utils.staff_queries import fetch_all_staff, create_staff_member
from utils.helpers import (
    fmt_name, fmt_employment, fmt_role,
    toast_success, toast_error,
)
from components.staff_form import staff_form


def render():
    st.title("👥 Staff List")

    # ── Top actions bar ───────────────────────────────────────────────
    col_search, col_add = st.columns([4, 1])
    search = col_search.text_input(
        "Search",
        placeholder="Name, email, employee number…",
        label_visibility="collapsed",
    )

    if col_add.button("➕ Add Staff", type="primary", use_container_width=True):
        st.session_state["show_add_staff"] = True

    # ── Load staff ────────────────────────────────────────────────────
    with st.spinner("Loading staff…"):
        try:
            all_staff = fetch_all_staff()
        except Exception as e:
            st.error(f"Could not load staff: {e}")
            all_staff = []

    # ── Filter ────────────────────────────────────────────────────────
    q = (search or "").lower().strip()
    if q:
        all_staff = [
            s for s in all_staff
            if q in fmt_name(s).lower()
            or q in (s.get("users") or {}).get("email", "").lower()
            or q in (s.get("employee_number") or "").lower()
        ]

    # ── Add-staff form ────────────────────────────────────────────────
    if st.session_state.get("show_add_staff"):
        with st.expander("➕ Add New Staff Member", expanded=True):
            values = staff_form(key_prefix="add", show_role_fields=True)
            if values:
                with st.spinner("Saving…"):
                    try:
                        create_staff_member(
                            first_name=values["first_name"],
                            last_name=values["last_name"],
                            email=values["email"],
                            phone=values["phone"],
                            date_of_birth=values["date_of_birth"],
                            employment_type=values["employment_type"],
                            employment_start_date=values["employment_start_date"],
                            employee_number=values["employee_number"],
                            centre_id=values["centre_id"],
                            role=values["role"],
                            primary_room_id=values.get("primary_room_id"),
                            emergency_contact_name=values["emergency_contact_name"],
                            emergency_contact_phone=values["emergency_contact_phone"],
                            emergency_contact_relationship=values["emergency_contact_relationship"],
                            notes=values["notes"],
                            contracted_hours_per_week=values.get("contracted_hours_per_week", 0.0),
                            is_responsible_person=values.get("is_responsible_person", False),
                            is_nominated_supervisor=values.get("is_nominated_supervisor", False),
                        )
                        toast_success("Staff member added.")
                        st.session_state.pop("show_add_staff", None)
                        st.rerun()
                    except Exception as e:
                        err = str(e)
                        if "duplicate" in err.lower() or "unique" in err.lower():
                            toast_error(f"Email {values['email']} is already in use.")
                        else:
                            toast_error(f"Could not save: {err}")

    # ── Staff table ───────────────────────────────────────────────────
    if not all_staff:
        st.info("No staff members found." + (" Try a different search." if q else ""))
        return

    # Sort: active first, then name
    all_staff.sort(key=lambda s: (
        0 if (s.get("users") or {}).get("is_active", True) else 1,
        fmt_name(s).lower(),
    ))

    # Render rows
    header_cols = st.columns([3, 2, 2, 2, 1])
    header_cols[0].markdown("**Name**")
    header_cols[1].markdown("**Type**")
    header_cols[2].markdown("**Role**")
    header_cols[3].markdown("**Roles**")
    header_cols[4].markdown("")
    st.divider()

    for s in all_staff:
        u         = s.get("users") or {}
        name      = fmt_name(s)
        is_active = u.get("is_active", True)
        etype     = fmt_employment(s.get("employment_type", ""))
        roles     = s.get("user_centre_roles") or []
        role_str  = fmt_role(roles[0]["role"]) if roles else "—"

        is_ns = bool(s.get("is_nominated_supervisor", False))
        is_rp = bool(s.get("is_responsible_person", False))

        # Build compact compliance badge HTML
        badges = []
        if is_ns:
            badges.append(
                '<span style="background:#e0e7ff;color:#3730a3;padding:1px 7px;'
                'border-radius:99px;font-size:0.72rem;font-weight:600;'
                'margin-right:4px;">NS</span>'
            )
        if is_rp:
            badges.append(
                '<span style="background:#dbeafe;color:#1e40af;padding:1px 7px;'
                'border-radius:99px;font-size:0.72rem;font-weight:600;">RP</span>'
            )
        badges_html = "".join(badges) if badges else "—"

        # Dim inactive rows
        name_display = name if is_active else f"~~{name}~~ (inactive)"

        row_cols = st.columns([3, 2, 2, 2, 1])
        row_cols[0].markdown(name_display)
        row_cols[1].markdown(etype)
        row_cols[2].markdown(role_str)
        row_cols[3].markdown(badges_html, unsafe_allow_html=True)
        if row_cols[4].button("View", key=f"view_{s['id']}"):
            st.session_state["viewing_staff_id"] = s["id"]
            st.session_state.page = "staff_profile"
            st.rerun()

    st.divider()
    st.caption(
        f"Showing {len(all_staff)} staff member(s). "
        "**NS** = Nominated Supervisor · **RP** = Responsible Person."
    )
