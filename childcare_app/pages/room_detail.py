# pages/room_detail.py  —  Screen 25: Room Detail (live snapshot)
import streamlit as st
from datetime import date, datetime
from utils.room_queries import (
    fetch_room_by_id,
    fetch_attendance_for_room_today,
    fetch_today_shifts_for_room,
    calc_ratio_status, fmt_age_range, fmt_age, age_in_months,
)
from utils.helpers import toast_error, fmt_date


def render():
    room_id   = st.session_state.get("viewing_room_id")
    centre_id = st.session_state.get("viewing_room_centre")

    if not room_id:
        st.warning("No room selected.")
        if st.button("← Rooms"):
            st.session_state.page = "rooms_list"; st.rerun()
        return

    # ── Load room config ──────────────────────────────────────────────
    with st.spinner("Loading…"):
        try:
            room = fetch_room_by_id(room_id)
        except Exception as e:
            toast_error(f"Could not load room: {e}"); return

    if not room:
        toast_error("Room not found.")
        st.session_state.page = "rooms_list"; st.rerun(); return

    # ── Load today's live data ────────────────────────────────────────
    try:
        attendance = fetch_attendance_for_room_today(room_id)
        shifts     = fetch_today_shifts_for_room(room_id)
    except Exception as e:
        toast_error(f"Could not load live data: {e}")
        attendance = []
        shifts     = []

    name       = room.get("name","Room")
    colour     = room.get("colour","#3498DB")
    r_staff    = room.get("required_ratio_staff", 1)
    r_children = room.get("required_ratio_children", 4)
    capacity   = room.get("licensed_capacity", 0)

    # Count present children (signed in, not signed out)
    present_children = [a for a in attendance if a.get("status") == "present"]
    n_children       = len(present_children)

    # Get current time to filter which shifts are active right now
    now_time = datetime.now().strftime("%H:%M:%S")
    active_shifts = [
        s for s in shifts
        if (s.get("start_time","00:00") <= now_time <= s.get("end_time","23:59"))
    ]
    n_staff = len(active_shifts)

    ratio_info = calc_ratio_status(n_children, n_staff, r_staff, r_children, capacity)

    # ── Header ────────────────────────────────────────────────────────
    bc, _ = st.columns([1, 10])
    with bc:
        st.markdown('<div class="back-btn">', unsafe_allow_html=True)
        if st.button("← Rooms", key="room_detail_back"):
            st.session_state.page = "rooms_list"; st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    # Room title with colour dot
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:0.75rem;margin-bottom:0.2rem;">'
        f'<div style="width:18px;height:18px;border-radius:50%;background:{colour};'
        f'box-shadow:0 0 0 4px {colour}30;flex-shrink:0;"></div>'
        f'<h1 style="margin:0;font-family:DM Serif Display,serif;font-size:2rem;'
        f'color:#0d1f35;">{name}</h1>'
        f'</div>',
        unsafe_allow_html=True
    )
    st.markdown(
        f'<p class="page-sub">{fmt_age_range(room.get("age_min_months",0), room.get("age_max_months",72))} · '
        f'Capacity {capacity} · Ratio 1:{r_children}</p>',
        unsafe_allow_html=True
    )

    # ── Action buttons ────────────────────────────────────────────────
    ab1, ab2, ab3, _ = st.columns([1.3, 1.3, 1.3, 4])
    with ab1:
        if st.button("✏️  Edit Room", key="rd_edit", use_container_width=True):
            st.session_state.editing_room_id = room_id
            st.session_state.page = "room_form"; st.rerun()
    with ab2:
        if st.button("📊  Ratio Monitor", key="rd_ratio", use_container_width=True):
            st.session_state.ratio_centre_id = centre_id
            st.session_state.page = "ratio_dashboard"; st.rerun()
    with ab3:
        if st.button("👶  Allocation", key="rd_alloc", use_container_width=True):
            st.session_state.selected_centre_rooms = centre_id
            st.session_state.page = "room_allocation"; st.rerun()

    st.markdown("---")

    # ── Live ratio status card ────────────────────────────────────────
    st.markdown("### 📡 Live Status — Today")

    ratio_col, children_col, staff_col, cap_col = st.columns(4)

    ratio_col.markdown(
        f'<div style="background:{ratio_info["colour"]};border-radius:12px;'
        f'padding:1.2rem;text-align:center;">'
        f'<div style="font-size:2rem;">{ratio_info["icon"]}</div>'
        f'<div style="font-family:DM Serif Display,serif;font-size:1.4rem;'
        f'color:{ratio_info["text_colour"]};margin-top:0.2rem;">{ratio_info["label"]}</div>'
        f'<div style="font-size:0.78rem;color:{ratio_info["text_colour"]};margin-top:0.3rem;">'
        f'{"Need " + str(abs(ratio_info["surplus"])) + " more staff" if ratio_info["surplus"] < 0 else "Staffing OK"}'
        f'</div></div>',
        unsafe_allow_html=True
    )
    children_col.metric("Children Present",  n_children,
                         delta=f"of {capacity} capacity",
                         delta_color="off")
    staff_col.metric("Staff Active Now",     n_staff,
                      delta=f"need ≥ {ratio_info['min_staff']}",
                      delta_color="off")
    cap_col.metric("Capacity Used",
                    f"{ratio_info['capacity_pct']}%",
                    delta=f"{capacity - n_children} spaces free",
                    delta_color="off")

    st.markdown("---")

    # ── Two-column detail ──────────────────────────────────────────────
    left, right = st.columns(2)

    # Children currently present
    with left:
        st.markdown("### 👶 Children Present Now")
        if not present_children:
            st.caption("No children currently signed in.")
        else:
            for a in present_children:
                child  = a.get("children") or {}
                cname  = f"{child.get('first_name','')} {child.get('last_name','')}".strip()
                age_m  = age_in_months(child.get("date_of_birth"))
                age_s  = fmt_age(age_m)
                sign_in = a.get("signed_in_at","")
                if sign_in:
                    try:
                        t = datetime.fromisoformat(sign_in.replace("Z","+00:00"))
                        sign_in_str = t.strftime("%I:%M %p")
                    except Exception:
                        sign_in_str = sign_in[:5]
                else:
                    sign_in_str = "—"

                st.markdown(
                    f'<div style="display:flex;justify-content:space-between;'
                    f'padding:0.4rem 0;border-bottom:1px solid #f0f4f8;">'
                    f'<span style="font-size:0.9rem;color:#0d1f35;">👶 {cname}</span>'
                    f'<span style="font-size:0.8rem;color:#7a90a8;">{age_s} · in {sign_in_str}</span>'
                    f'</div>',
                    unsafe_allow_html=True
                )

    # Staff rostered today
    with right:
        st.markdown("### 👩‍🏫 Staff Rostered Today")
        if not shifts:
            st.caption("No shifts rostered in this room today.")
        else:
            for shift in shifts:
                u      = shift.get("users") or {}
                sname  = f"{u.get('first_name','')} {u.get('last_name','')}".strip()
                start  = shift.get("start_time","")[:5] if shift.get("start_time") else "—"
                end    = shift.get("end_time","")[:5]   if shift.get("end_time") else "—"
                status = shift.get("status","")

                # Is this shift currently active?
                is_now = shift.get("start_time","") <= now_time <= shift.get("end_time","99:99")
                dot    = f'<span style="display:inline-block;width:8px;height:8px;border-radius:50%;'\
                         f'background:{"#1a6b4a" if is_now else "#cbd5e1"};margin-right:6px;"></span>'

                st.markdown(
                    f'<div style="display:flex;justify-content:space-between;'
                    f'padding:0.4rem 0;border-bottom:1px solid #f0f4f8;align-items:center;">'
                    f'<span style="font-size:0.9rem;color:#0d1f35;">{dot}👩‍🏫 {sname}</span>'
                    f'<span style="font-size:0.8rem;color:#7a90a8;">{start}–{end}</span>'
                    f'</div>',
                    unsafe_allow_html=True
                )

    st.markdown("---")

    # ── Room configuration summary ────────────────────────────────────
    st.markdown("### ⚙️ Room Configuration")
    cfg1, cfg2, cfg3 = st.columns(3)

    with cfg1:
        st.markdown('<p class="section-label">Age & Capacity</p>', unsafe_allow_html=True)
        st.markdown(f"**Age range:** {fmt_age_range(room.get('age_min_months',0), room.get('age_max_months',72))}")
        st.markdown(f"**Capacity:** {capacity} children")

    with cfg2:
        st.markdown('<p class="section-label">Ratio Requirements</p>', unsafe_allow_html=True)
        st.markdown(f"**Required ratio:** 1 educator : {r_children} children")
        st.markdown(f"**Diploma required:** {'Yes ✅' if room.get('requires_diploma') else 'No'}")

    with cfg3:
        st.markdown('<p class="section-label">Status</p>', unsafe_allow_html=True)
        active_str = "✅ Active" if room.get("is_active") else "🔴 Inactive"
        st.markdown(f"**Status:** {active_str}")
        if room.get("notes"):
            st.markdown(f"**Notes:** _{room['notes']}_")
