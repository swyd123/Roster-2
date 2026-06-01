# pages/leave_add.py  —  Submit a new leave request
import streamlit as st
from datetime import date
from utils.staff_queries import (
    fetch_all_staff, fetch_centres, create_leave_request,
)
from utils.helpers import (
    fmt_name, LEAVE_TYPE_KEYS, LEAVE_TYPES,
    toast_success, toast_error, workdays_between,
)
import time


def render():
    bc, _ = st.columns([1, 10])
    with bc:
        st.markdown('<div class="back-btn">', unsafe_allow_html=True)
        if st.button("← Back", key="la_back"):
            st.session_state.page = "leave_list"; st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    st.title("Submit Leave Request")
    st.markdown('<p class="page-sub">Submit a leave request on behalf of a staff member.</p>',
                unsafe_allow_html=True)
    st.markdown("---")

    with st.spinner("Loading…"):
        try:
            all_staff = fetch_all_staff()
            centres   = fetch_centres()
        except Exception as e:
            toast_error(f"Could not load: {e}"); return

    staff_opts   = {s["id"]: fmt_name(s)    for s in all_staff}
    staff_uid    = {s["id"]: (s.get("users") or {}).get("id","") for s in all_staff}
    centre_opts  = {c["id"]: c["name"]      for c in centres}

    with st.form("leave_add_form"):
        st.markdown('<p class="section-label">Staff Member</p>', unsafe_allow_html=True)
        fc1, fc2 = st.columns(2)
        selected_profile = fc1.selectbox(
            "Staff member *",
            options=list(staff_opts.keys()),
            format_func=lambda x: staff_opts[x],
        )
        selected_centre = fc2.selectbox(
            "Centre *",
            options=list(centre_opts.keys()),
            format_func=lambda x: centre_opts[x],
        )

        st.divider()
        st.markdown('<p class="section-label">Leave Details</p>', unsafe_allow_html=True)
        lc1, lc2 = st.columns(2)
        leave_type = lc1.selectbox("Leave type *", LEAVE_TYPE_KEYS,
                                    format_func=lambda x: LEAVE_TYPES[x])
        lc2.markdown("")

        lc3, lc4 = st.columns(2)
        start_d = lc3.date_input("Start date *", value=date.today(), format="DD/MM/YYYY")
        end_d   = lc4.date_input("End date *",   value=date.today(), format="DD/MM/YYYY")

        partial = st.toggle("Part-day leave", value=False)
        if partial:
            tc1, tc2 = st.columns(2)
            from datetime import time as _time
            start_t = tc1.time_input("Start time", value=_time(9, 0))
            end_t   = tc2.time_input("End time",   value=_time(13, 0))
        else:
            start_t = end_t = None

        reason = st.text_area("Reason", height=90, placeholder="Optional")

        sc1, sc2 = st.columns(2)
        submitted = sc1.form_submit_button("Submit Request", type="primary", use_container_width=True)
        cancelled = sc2.form_submit_button("Cancel", use_container_width=True)

    if cancelled:
        st.session_state.page = "leave_list"; st.rerun()

    if submitted:
        if end_d < start_d:
            toast_error("End date cannot be before start date.")
            return

        # Get the user_id from the profile
        user_id = staff_uid.get(selected_profile,"")
        if not user_id:
            toast_error("Could not find user account for this staff member.")
            return

        try:
            create_leave_request(
                user_id=user_id,
                centre_id=selected_centre,
                leave_type=leave_type,
                start_date=start_d.isoformat(),
                end_date=end_d.isoformat(),
                reason=reason,
                is_partial_day=partial,
                start_time=start_t.strftime("%H:%M:%S") if start_t else None,
                end_time=end_t.strftime("%H:%M:%S")   if end_t   else None,
            )
            ndays = workdays_between(start_d, end_d)
            toast_success(f"Leave request submitted for {staff_opts[selected_profile]} ({ndays} working day(s)).")
            time.sleep(0.8)
            st.session_state.page = "leave_list"
            st.rerun()
        except Exception as e:
            toast_error(f"Could not submit: {e}")
