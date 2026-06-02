# app.py  —  Entry point. Run: streamlit run app.py
import streamlit as st

st.set_page_config(
    page_title="Childcare Platform",
    page_icon="🏫",
    layout="wide",
    initial_sidebar_state="expanded",
)

from utils.styles import GLOBAL_CSS
st.markdown(GLOBAL_CSS, unsafe_allow_html=True)

NAV = [
    ("STAFF MANAGEMENT",  None),
    ("👥  Staff List",          "staff_list"),
    ("➕  Add Staff Member",    "staff_add"),
    ("🏖️  Leave Requests",     "leave_list"),
    ("───", None),
    ("ROSTERING",         None),
    ("📅  Rosters",             "roster_list"),
    ("🗓️  Roster Builder",      "roster_builder"),
    ("📋  Shift Templates",     "shift_templates"),
    ("📊  Roster Report",       "roster_report"),
    ("───", None),
    ("ROOMS & RATIOS",    None),
    ("🚪  Rooms",               "rooms_list"),
    ("👶  Room Allocation",     "room_allocation"),
    ("📊  Ratio Monitor",       "ratio_dashboard"),
    ("🔬  Ratio Detail",        "ratio_detail"),
    ("🗒️  Breach Log",          "ratio_breach_log"),
    ("📄  Compliance Report",   "ratio_report"),
    ("───", None),
    ("BREAK TRACKING",    None),
    ("☕  Break Schedule",      "break_schedule"),
    ("📜  Break History",       "break_history"),
    ("───", None),
    ("SETTINGS",          None),
    ("🏫  Centres",             "centres"),
]

CONTEXT_KEYS = [
    "viewing_staff_id",  "editing_staff_id",
    "viewing_leave_id",  "add_qual_for",
    "viewing_room_id",   "editing_room_id",
    "viewing_room_centre",
    "log_breach_room_id", "log_breach_centre_id",
    "selected_centre_rooms", "ratio_centre_id",
    "rr_generated",
    "break_centre_id",
    "show_log_break", "prefill_shift_id", "prefill_user_id",
    "roster_period_id", "show_create_roster",
    "show_publish_panel",
    "show_add_centre",
]

with st.sidebar:
    st.markdown('<p class="sidebar-brand">🏫 Childcare Platform</p>', unsafe_allow_html=True)
    st.markdown("---")
    for item, key in NAV:
        if key is None:
            if item and item != "───":
                st.markdown(
                    f'<p style="font-size:0.67rem;letter-spacing:0.1em;text-transform:uppercase;'
                    f'color:#4a6079;margin:0.9rem 0 0.3rem 0.5rem;">{item}</p>',
                    unsafe_allow_html=True,
                )
            else:
                st.markdown('<hr style="border-color:#1e3a55;margin:0.3rem 0"/>', unsafe_allow_html=True)
        else:
            active = st.session_state.get("page") == key
            if active:
                st.markdown('<div class="nav-active">', unsafe_allow_html=True)
            if st.button(item, key=f"nav_{key}", use_container_width=True):
                for k in CONTEXT_KEYS:
                    st.session_state.pop(k, None)
                st.session_state.page = key
                st.rerun()
            if active:
                st.markdown("</div>", unsafe_allow_html=True)
    st.markdown("---")
    st.markdown(
        '<p style="font-size:0.72rem;color:#3a566e;text-align:center;">v1.0 · Childcare Platform</p>',
        unsafe_allow_html=True,
    )

if "page" not in st.session_state:
    st.session_state.page = "staff_list"

page = st.session_state.get("page", "staff_list")

if   page == "staff_list":     from pages.staff_list     import render; render()
elif page == "staff_add":      from pages.staff_add      import render; render()
elif page == "staff_profile":  from pages.staff_profile  import render; render()
elif page == "leave_list":     from pages.leave_list     import render; render()
elif page == "leave_review":   from pages.leave_review   import render; render()
elif page == "leave_add":      from pages.leave_add      import render; render()
elif page == "roster_list":    from pages.roster_list    import render; render()
elif page == "roster_builder": from pages.roster_builder import render; render()
elif page == "shift_templates":from pages.shift_templates import render; render()
elif page == "roster_report":  from pages.roster_report  import render; render()
elif page == "rooms_list":     from pages.rooms_list     import render; render()
elif page == "room_form":      from pages.room_form      import render; render()
elif page == "room_detail":    from pages.room_detail    import render; render()
elif page == "room_allocation":from pages.room_allocation import render; render()
elif page == "ratio_dashboard":  from pages.ratio_dashboard  import render; render()
elif page == "ratio_detail":     from pages.ratio_detail     import render; render()
elif page == "ratio_breach_log": from pages.ratio_breach_log import render; render()
elif page == "ratio_report":     from pages.ratio_report     import render; render()
elif page == "break_schedule": from pages.break_schedule import render; render()
elif page == "break_history":  from pages.break_history  import render; render()
elif page == "centres":        from pages.centres        import render; render()
else:
    st.info("Select an item from the sidebar to get started.")
