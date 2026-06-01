# pages/staff_edit.py
# ------------------------------------------------------------------
# Edit Staff page — pre-fills the form with existing data and
# saves changes back to the database.
# ------------------------------------------------------------------

import streamlit as st
from utils.staff_queries import fetch_staff_by_id, update_staff_member
from utils.helpers import format_name, show_success, show_error
from components.staff_form import staff_form


def render():
    """Renders the Edit Staff Member page."""

    # ---- Guard: we need to know WHICH staff member to edit ----
    staff_profile_id = st.session_state.get("editing_staff_id")

    if not staff_profile_id:
        st.warning("No staff member selected to edit.")
        if st.button("← Back to Staff List"):
            st.session_state.page = "staff_list"
            st.rerun()
        return

    # ---- Load the existing record ----
    with st.spinner("Loading staff member..."):
        try:
            staff = fetch_staff_by_id(staff_profile_id)
        except Exception as e:
            show_error(f"Could not load staff record: {e}")
            return

    if not staff:
        show_error("Staff member not found. They may have been deleted.")
        if st.button("← Back to Staff List"):
            st.session_state.page = "staff_list"
            st.rerun()
        return

    name    = format_name(staff)
    user    = staff.get("users") or {}
    user_id = user.get("id")

    # ---- Page header ----
    col_back, col_title = st.columns([1, 6])
    with col_back:
        if st.button("← Back", key="edit_back_btn"):
            st.session_state.page = "staff_list"
            st.session_state.pop("editing_staff_id", None)
            st.rerun()
    with col_title:
        st.title(f"✏️ Edit Staff: {name}")

    st.caption("Update the details below and click Save.")
    st.divider()

    # ---- Render the shared form, pre-filled with existing values ----
    form_values = staff_form(key_prefix="edit", defaults=staff)

    # ---- Handle form submission ----
    if form_values is not None:
        with st.spinner("Saving changes..."):
            try:
                updated = update_staff_member(
                    staff_profile_id=               staff_profile_id,
                    user_id=                        user_id,
                    first_name=                     form_values["first_name"],
                    last_name=                      form_values["last_name"],
                    email=                          form_values["email"],
                    phone=                          form_values["phone"],
                    employment_type=                form_values["employment_type"],
                    employment_start_date=          form_values["employment_start_date"],
                    employee_number=                form_values["employee_number"],
                    emergency_contact_name=         form_values["emergency_contact_name"],
                    emergency_contact_phone=        form_values["emergency_contact_phone"],
                    emergency_contact_relationship= form_values["emergency_contact_relationship"],
                    notes=                          form_values["notes"],
                    is_active=                      form_values["is_active"],
                )

                if updated:
                    show_success(
                        f"{form_values['first_name']} {form_values['last_name']} "
                        f"has been updated."
                    )
                    import time
                    time.sleep(1)
                    st.session_state.page = "staff_list"
                    st.session_state.pop("editing_staff_id", None)
                    st.rerun()
                else:
                    show_error("Update did not save. Please try again.")

            except Exception as e:
                error_msg = str(e)
                if "duplicate" in error_msg.lower() or "unique" in error_msg.lower():
                    show_error(
                        f"The email **{form_values['email']}** is already used "
                        f"by another account. Please use a different email."
                    )
                else:
                    show_error(f"Could not save changes: {error_msg}")
