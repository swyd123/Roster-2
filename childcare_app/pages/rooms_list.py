# pages/rooms_list.py  —  Screen 23: Rooms List
import streamlit as st
from utils.room_queries import (
    fetch_rooms, soft_delete_room, fetch_children_for_room,
    fmt_age_range, calc_ratio_status,
)
from utils.staff_queries import fetch_centres
from utils.helpers import toast_success, toast_error


# ── Australian NQS standard ratios as reference ───────────────────────────────
NQS_REFERENCE = {
    "Under 2 years":        "1 educator : 4 children",
    "2 to under 3 years":   "1 educator : 5 children",
    "3 years and over":     "1 educator : 11 children (approved provider)",
}


def render():
    # ── Header ────────────────────────────────────────────────────────
    hc, btn_c = st.columns([4, 1])
    hc.title("Rooms")
    hc.markdown('<p class="page-sub">Configure rooms, ratios, and age ranges for your centre</p>',
                unsafe_allow_html=True)
    with btn_c:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("➕  Add Room", type="primary", use_container_width=True):
            st.session_state.pop("editing_room_id", None)
            st.session_state.page = "room_form"
            st.rerun()

    # ── Centre selector ───────────────────────────────────────────────
    centres   = fetch_centres()
    if not centres:
        st.warning("No centres found. Please set up a centre first.")
        return

    centre_opts = {c["id"]: c["name"] for c in centres}
    if "selected_centre_id" not in st.session_state:
        st.session_state.selected_centre_id = centres[0]["id"]

    centre_id = st.selectbox(
        "Centre",
        options=list(centre_opts.keys()),
        format_func=lambda x: centre_opts[x],
        key="rooms_centre_selector",
        index=list(centre_opts.keys()).index(st.session_state.selected_centre_id)
              if st.session_state.selected_centre_id in centre_opts else 0,
    )
    st.session_state.selected_centre_id = centre_id

    # ── Load rooms ────────────────────────────────────────────────────
    with st.spinner("Loading rooms…"):
        try:
            show_inactive = st.toggle("Show inactive rooms", value=False, key="show_inactive_rooms")
            rooms = fetch_rooms(centre_id, include_inactive=show_inactive)
        except Exception as e:
            toast_error(f"Could not load rooms: {e}")
            return

    if not rooms:
        # ── Empty state with NQS reference ───────────────────────────
        st.markdown("")
        st.info("No rooms configured yet. Click **➕ Add Room** to create the first one.")
        st.markdown("")
        _render_nqs_reference()
        return

    # ── Summary metrics ───────────────────────────────────────────────
    active_rooms  = [r for r in rooms if r.get("is_active")]
    total_cap     = sum(r.get("licensed_capacity", 0) for r in active_rooms)
    diploma_rooms = sum(1 for r in active_rooms if r.get("requires_diploma"))

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Active Rooms",       len(active_rooms))
    m2.metric("Total Capacity",     total_cap)
    m3.metric("Diploma Required",   diploma_rooms)
    m4.metric("No Diploma Required", len(active_rooms) - diploma_rooms)

    st.markdown("---")

    # ── Room cards ────────────────────────────────────────────────────
    for room in rooms:
        _render_room_card(room, centre_id)

    st.markdown("---")
    _render_nqs_reference()


def _render_room_card(room: dict, centre_id: str):
    colour     = room.get("colour", "#4A90D9")
    name       = room.get("name", "Unknown")
    is_active  = room.get("is_active", True)
    age_range  = fmt_age_range(room.get("age_min_months", 0), room.get("age_max_months", 72))
    capacity   = room.get("licensed_capacity", 0)
    r_staff    = room.get("required_ratio_staff", 1)
    r_children = room.get("required_ratio_children", 4)
    diploma    = room.get("requires_diploma", False)
    sort_order = room.get("sort_order", 0)
    notes      = room.get("notes") or ""

    # Status badge
    status_html = (
        f'<span style="background:#d4f0e4;color:#0f6b3a;padding:2px 8px;border-radius:99px;'
        f'font-size:0.72rem;font-weight:600;">Active</span>' if is_active else
        f'<span style="background:#fde8e8;color:#991b1b;padding:2px 8px;border-radius:99px;'
        f'font-size:0.72rem;font-weight:600;">Inactive</span>'
    )
    diploma_html = (
        f'<span style="background:#dbeafe;color:#1d4ed8;padding:2px 8px;border-radius:99px;'
        f'font-size:0.72rem;font-weight:600;">Diploma required</span>' if diploma else
        f'<span style="background:#f1f5f9;color:#64748b;padding:2px 8px;border-radius:99px;'
        f'font-size:0.72rem;font-weight:600;">No diploma req.</span>'
    )

    # Left-border colour stripe using a container + custom HTML
    st.markdown(
        f'<div style="border-left:5px solid {colour};background:#fff;border-radius:0 10px 10px 0;'
        f'padding:0.8rem 1.2rem 0.4rem;margin-bottom:0.2rem;border:1px solid #e4edf5;'
        f'border-left:5px solid {colour};box-shadow:0 1px 4px rgba(13,31,53,0.05);">'
        f'<div style="display:flex;align-items:center;gap:0.8rem;">'
        f'<div style="width:14px;height:14px;border-radius:50%;background:{colour};'
        f'flex-shrink:0;box-shadow:0 0 0 3px {colour}30;"></div>'
        f'<span style="font-family:DM Serif Display,serif;font-size:1.05rem;'
        f'color:#0d1f35;font-weight:400;">{name}</span>'
        f'<span style="margin-left:auto;display:flex;gap:0.4rem;">'
        f'{status_html}&nbsp;{diploma_html}'
        f'</span></div></div>',
        unsafe_allow_html=True
    )

    with st.expander("", expanded=False):
        dc1, dc2, dc3, dc4 = st.columns(4)
        dc1.markdown(f"**Age range**  \n{age_range}")
        dc2.markdown(f"**Capacity**  \n{capacity} children")
        dc3.markdown(f"**Required ratio**  \n1 staff : {r_children} children")
        dc4.markdown(f"**Sort order**  \n{sort_order}")

        if notes:
            st.markdown(f"**Notes:** _{notes}_")

        st.markdown("")

        # Action buttons
        ab1, ab2, ab3, ab4, _ = st.columns([1.2, 1.2, 1.2, 1.2, 3])

        with ab1:
            if st.button("👁  View", key=f"view_room_{room['id']}", use_container_width=True):
                st.session_state.viewing_room_id   = room["id"]
                st.session_state.viewing_room_centre = centre_id
                st.session_state.page = "room_detail"
                st.rerun()

        with ab2:
            if st.button("✏️  Edit", key=f"edit_room_{room['id']}", use_container_width=True):
                st.session_state.editing_room_id   = room["id"]
                st.session_state.page = "room_form"
                st.rerun()

        with ab3:
            if st.button("👶  Allocation", key=f"alloc_room_{room['id']}", use_container_width=True):
                st.session_state.selected_centre_rooms = centre_id
                st.session_state.page = "room_allocation"
                st.rerun()

        with ab4:
            del_key = f"del_room_{room['id']}"
            if st.session_state.get(del_key):
                st.warning(f"Delete **{name}**? This cannot be undone.")
                dy, dn = st.columns(2)
                if dy.button("Delete", key=f"dely_{room['id']}", type="primary", use_container_width=True):
                    try:
                        soft_delete_room(room["id"])
                        toast_success(f"Room '{name}' removed.")
                        st.session_state.pop(del_key, None)
                        st.rerun()
                    except Exception as e:
                        toast_error(str(e))
                if dn.button("Cancel", key=f"deln_{room['id']}", use_container_width=True):
                    st.session_state.pop(del_key, None); st.rerun()
            else:
                if st.button("🗑️  Delete", key=f"del_btn_{room['id']}", use_container_width=True):
                    st.session_state[del_key] = True; st.rerun()


def _render_nqs_reference():
    """Show NQS ratio reference table as a helpful guide."""
    st.markdown('<p class="section-label">NQS Educator-to-Child Ratio Reference</p>',
                unsafe_allow_html=True)
    st.markdown(
        '<div style="background:#f5f8fb;border:1px solid #e4edf5;border-radius:10px;'
        'padding:1rem 1.4rem;">'
        '<table style="width:100%;border-collapse:collapse;font-size:0.88rem;">'
        '<tr style="border-bottom:1px solid #e4edf5;">'
        '<th style="text-align:left;padding:0.4rem 0.6rem;color:#4a6079;font-weight:600;">Age Group</th>'
        '<th style="text-align:left;padding:0.4rem 0.6rem;color:#4a6079;font-weight:600;">Minimum Ratio</th>'
        '</tr>'
        '<tr><td style="padding:0.5rem 0.6rem;">Under 2 years</td>'
        '<td style="padding:0.5rem 0.6rem;font-weight:500;color:#1a6b4a;">1 educator : 4 children</td></tr>'
        '<tr style="background:#fafcfe;"><td style="padding:0.5rem 0.6rem;">2 to under 3 years</td>'
        '<td style="padding:0.5rem 0.6rem;font-weight:500;color:#1a6b4a;">1 educator : 5 children</td></tr>'
        '<tr><td style="padding:0.5rem 0.6rem;">3 years and over (centre-based)</td>'
        '<td style="padding:0.5rem 0.6rem;font-weight:500;color:#1a6b4a;">1 educator : 11 children</td></tr>'
        '</table>'
        '<p style="font-size:0.75rem;color:#7a90a8;margin-top:0.8rem;margin-bottom:0;">'
        '* Based on Education and Care Services National Regulations. '
        'Refer to your state regulator for current requirements.</p>'
        '</div>',
        unsafe_allow_html=True
    )
