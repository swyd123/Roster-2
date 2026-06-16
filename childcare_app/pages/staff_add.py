# pages/staff_add.py
import streamlit as st
import time
from utils.staff_queries import create_staff_member
from utils.helpers import toast_success, toast_error
from components.staff_form import staff_form


def render():
    bc, tc = st.columns([1, 8])
    with bc:
        st.markdown('<div class="back-btn">', unsafe_allow_html=True)
        if st.button("← Back", key="add_back"):
            st.session_state.page = "staff_list"
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)
    with tc:
        st.title("Add Staff Member")
        st.markdown(
            '<p class="page-sub">Fill in the details below. Fields marked * are required.</p>',
            unsafe_allow_html=True,
        )

    st.markdown("---")

    values = staff_form(key_prefix="add", defaults=None, show_role_fields=True)

    if values:
        with st.spinner("Creating staff member…"):
            try:
                profile = create_staff_member(
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
                    allows_unpaid_break_opt_out=values.get("allows_unpaid_break_opt_out", False),
                    is_responsible_person=values.get("is_responsible_person", False),
                    is_nominated_supervisor=values.get("is_nominated_supervisor", False),
                )
                if profile:
                    toast_success(
                        f"{values['first_name']} {values['last_name']} has been added."
                    )
                    time.sleep(0.8)
                    st.session_state.viewing_staff_id = profile["id"]
                    st.session_state.page = "staff_profile"
                    st.rerun()
            except Exception as e:
                err = str(e)
                if "duplicate" in err.lower() or "unique" in err.lower():
                    toast_error(
                        f"A staff member with email **{values['email']}** already exists."
                    )
                else:
                    toast_error(f"Could not create staff member: {err}")
