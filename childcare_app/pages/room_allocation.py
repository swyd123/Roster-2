# pages/room_allocation.py  —  Screen 26: Room Allocation
import streamlit as st
from utils.room_queries import (
    fetch_rooms, fetch_children_by_centre,
    move_child_to_room,
    fmt_age, age_in_months, is_child_near_age_out,
)
from utils.staff_queries import fetch_centres
from utils.helpers import toast_success, toast_error, fmt_date


def render():
    # ── Header ────────────────────────────────────────────────────────
    bc, hc, btn_c = st.columns([1, 5, 1.5])
    with bc:
        st.markdown('<div class="back-btn">', unsafe_allow_html=True)
        if st.button("← Rooms", key="alloc_back"):
            st.session_state.page = "rooms_list"; st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)
    with hc:
        st.title("Room Allocation")
        st.markdown('<p class="page-sub">View which children are in each room and move them as they age up</p>',
                    unsafe_allow_html=True)

    # ── Centre selector ───────────────────────────────────────────────
    centres   = fetch_centres()
    if not centres:
        st.warning("No centres found.")
        return

    centre_opts = {c["id"]: c["name"] for c in centres}
    saved_centre = st.session_state.get("selected_centre_rooms") or \
                   st.session_state.get("selected_centre_id") or \
                   centres[0]["id"]

    centre_id = st.selectbox(
        "Centre",
        options=list(centre_opts.keys()),
        format_func=lambda x: centre_opts[x],
        index=list(centre_opts.keys()).index(saved_centre)
              if saved_centre in centre_opts else 0,
        key="alloc_centre_sel",
    )
    st.session_state.selected_centre_rooms = centre_id

    # ── Load data ─────────────────────────────────────────────────────
    with st.spinner("Loading…"):
        try:
            rooms    = fetch_rooms(centre_id)
            children = fetch_children_by_centre(centre_id)
        except Exception as e:
            toast_error(f"Could not load data: {e}"); return

    # ── Age-up alerts ─────────────────────────────────────────────────
    age_out_kids = []
    for child in children:
        room = child.get("rooms")
        if room:
            room_full = next((r for r in rooms if r["id"] == child.get("room_id")), None)
            if room_full and is_child_near_age_out(child.get("date_of_birth"), room_full.get("age_max_months",72)):
                age_out_kids.append((child, room_full))

    if age_out_kids:
        st.warning(
            f"⚠️ **{len(age_out_kids)} child(ren) approaching room age limit** — "
            f"review and move to the next room."
        )
        with st.expander(f"See {len(age_out_kids)} age-up suggestion(s)"):
            for child, room_full in age_out_kids:
                cname   = f"{child.get('first_name','')} {child.get('last_name','')}".strip()
                age_m   = age_in_months(child.get("date_of_birth"))
                max_m   = room_full.get("age_max_months", 72)
                in_room = room_full.get("name","")
                st.markdown(
                    f'<div style="display:flex;justify-content:space-between;padding:0.5rem 0;'
                    f'border-bottom:1px solid #fde68a;">'
                    f'<span>👶 <strong>{cname}</strong> · {fmt_age(age_m)} · currently in <strong>{in_room}</strong></span>'
                    f'<span style="color:#92400e;font-size:0.82rem;">Limit: {fmt_age(max_m)}</span>'
                    f'</div>',
                    unsafe_allow_html=True
                )

    st.markdown("---")

    # ── Summary metrics ───────────────────────────────────────────────
    unassigned = [c for c in children if not c.get("room_id")]
    m1, m2, m3 = st.columns(3)
    m1.metric("Total Enrolled", len(children))
    m2.metric("Unassigned",     len(unassigned),
               delta="need room" if unassigned else None,
               delta_color="inverse" if unassigned else "off")
    m3.metric("Active Rooms",  len(rooms))

    st.markdown("---")

    # ── Group children by room ────────────────────────────────────────
    room_map  = {r["id"]: r for r in rooms}
    room_kids = {r["id"]: [] for r in rooms}
    room_kids["__none__"] = []

    for child in children:
        rid = child.get("room_id")
        if rid and rid in room_kids:
            room_kids[rid].append(child)
        else:
            room_kids["__none__"].append(child)

    # Build dropdown options for move-to
    move_opts = {"": "— Unassigned —"}
    move_opts.update({r["id"]: r["name"] for r in rooms})

    # ── Render one column per room ────────────────────────────────────
    # Use a 2-up grid for rooms + an "unassigned" section
    all_room_sections = [(r, room_kids[r["id"]]) for r in rooms]

    # Two rooms per row
    for i in range(0, len(all_room_sections), 2):
        row_rooms = all_room_sections[i:i+2]
        cols = st.columns(len(row_rooms))

        for col, (room, kids) in zip(cols, row_rooms):
            with col:
                colour   = room.get("colour","#3498DB")
                rname    = room.get("name","Room")
                cap      = room.get("licensed_capacity", 0)
                n_kids   = len(kids)
                fill_pct = round((n_kids / cap) * 100) if cap > 0 else 0

                # Fill bar colour
                if fill_pct >= 100:
                    bar_colour = "#dc2626"
                elif fill_pct >= 80:
                    bar_colour = "#d97706"
                else:
                    bar_colour = colour

                st.markdown(
                    f'<div style="border:2px solid {colour};border-radius:12px;'
                    f'overflow:hidden;margin-bottom:1rem;">'
                    # Header bar
                    f'<div style="background:{colour};padding:0.65rem 0.9rem;'
                    f'display:flex;justify-content:space-between;align-items:center;">'
                    f'<span style="font-family:DM Serif Display,serif;font-size:1rem;'
                    f'color:#ffffff;">{rname}</span>'
                    f'<span style="font-size:0.8rem;color:rgba(255,255,255,0.85);font-weight:600;">'
                    f'{n_kids}/{cap}</span>'
                    f'</div>'
                    # Capacity bar
                    f'<div style="height:4px;background:#f0f4f8;">'
                    f'<div style="height:4px;width:{min(fill_pct,100)}%;background:{bar_colour};'
                    f'transition:width 0.3s;"></div></div>'
                    f'</div>',
                    unsafe_allow_html=True
                )

                if not kids:
                    st.caption("No children assigned.")
                else:
                    for child in kids:
                        _render_child_row(child, room_map, move_opts, colour)

    # ── Unassigned children ────────────────────────────────────────────
    if room_kids["__none__"]:
        st.markdown("---")
        st.markdown(
            f'<div style="background:#fff8f0;border:2px solid #f59e0b;border-radius:12px;'
            f'padding:0.65rem 0.9rem;margin-bottom:0.8rem;">'
            f'<span style="font-family:DM Serif Display,serif;font-size:1rem;color:#92400e;">'
            f'⚠️ Unassigned Children ({len(room_kids["__none__"])})</span>'
            f'</div>',
            unsafe_allow_html=True
        )
        for child in room_kids["__none__"]:
            _render_child_row(child, room_map, move_opts, "#f59e0b")


def _render_child_row(child: dict, room_map: dict, move_opts: dict, accent: str):
    cid    = child.get("id","")
    cname  = f"{child.get('first_name','')} {child.get('last_name','')}".strip()
    age_m  = age_in_months(child.get("date_of_birth"))
    age_s  = fmt_age(age_m)
    cur_room_id = child.get("room_id","")

    # Current room's max age for warning
    cur_room = room_map.get(cur_room_id)
    near_out = cur_room and is_child_near_age_out(child.get("date_of_birth"), cur_room.get("age_max_months",72))
    age_warn = " ⚠️" if near_out else ""

    with st.container():
        c1, c2 = st.columns([3, 2])
        c1.markdown(
            f'<div style="padding:0.3rem 0;font-size:0.88rem;color:#0d1f35;">'
            f'👶 <strong>{cname}</strong>'
            f'<span style="font-size:0.78rem;color:#7a90a8;margin-left:0.5rem;">{age_s}{age_warn}</span>'
            f'</div>',
            unsafe_allow_html=True
        )

        move_key = f"move_{cid}"
        selected = c2.selectbox(
            "Move to",
            options=list(move_opts.keys()),
            format_func=lambda x: move_opts[x],
            index=list(move_opts.keys()).index(cur_room_id) if cur_room_id in move_opts else 0,
            key=move_key,
            label_visibility="collapsed",
        )

        if selected != cur_room_id:
            if st.button("Move", key=f"confirm_move_{cid}", type="primary"):
                try:
                    move_child_to_room(cid, selected or None)
                    new_room_name = move_opts.get(selected,"unassigned")
                    toast_success(f"{cname} moved to {new_room_name}.")
                    st.rerun()
                except Exception as e:
                    toast_error(str(e))
