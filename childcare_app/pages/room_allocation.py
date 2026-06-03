# pages/room_allocation.py — Room Allocation
# Shows per-room: capacity, active enrolments, vacancy, peak attendance for date.
# Children can be moved between rooms inline.
#
# Data sources
# ─────────────
# Enrolled count:  children table  (enrolment_status='active', deleted_at IS NULL)
# Peak attendance: room_attendance_intervals  (actual_children preferred,
#                  expected_children fallback — expected can be null/0)
#
# Both sources are independent — attendance shows even when children table is empty.

import streamlit as st
from datetime import date

from utils.room_queries import (
    fetch_rooms, fetch_children_by_centre, fetch_enrolled_counts_by_room,
    move_child_to_room,
    fmt_age, age_in_months, is_child_near_age_out,
)
from utils.attendance_queries import fetch_intervals_for_centre
from utils.staff_queries import fetch_centres
from utils.helpers import toast_success, toast_error


def render():
    # ── Header ────────────────────────────────────────────────────────
    bc, hc = st.columns([1, 8])
    with bc:
        st.markdown('<div class="back-btn">', unsafe_allow_html=True)
        if st.button("← Rooms", key="alloc_back"):
            st.session_state.page = "rooms_list"
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)
    with hc:
        st.title("Room Allocation")
        st.markdown(
            '<p class="page-sub">Enrolment and attendance per room · '
            "move children as they age up</p>",
            unsafe_allow_html=True,
        )

    # ── Centre + date selectors ───────────────────────────────────────
    centres = fetch_centres()
    if not centres:
        st.warning("No centres found.")
        return

    centre_opts  = {c["id"]: c["name"] for c in centres}
    saved_centre = (
        st.session_state.get("selected_centre_rooms")
        or st.session_state.get("selected_centre_id")
        or centres[0]["id"]
    )

    sel1, sel2 = st.columns([3, 1])
    centre_id = sel1.selectbox(
        "Centre",
        options=list(centre_opts.keys()),
        format_func=lambda x: centre_opts[x],
        index=list(centre_opts.keys()).index(saved_centre)
               if saved_centre in centre_opts else 0,
        key="alloc_centre_sel",
    )
    st.session_state.selected_centre_rooms = centre_id

    selected_date = sel2.date_input(
        "Attendance date",
        value=date.today(),
        key="alloc_date_sel",
        format="DD/MM/YYYY",
        help="Used for peak attendance figures from attendance intervals.",
    )
    date_str = selected_date.isoformat()

    # ── Load all data ─────────────────────────────────────────────────
    with st.spinner("Loading…"):
        try:
            rooms           = fetch_rooms(centre_id)
            children        = fetch_children_by_centre(centre_id)
            enrolled_counts = fetch_enrolled_counts_by_room(centre_id)
            day_intervals   = fetch_intervals_for_centre(centre_id, date_str)
        except Exception as e:
            toast_error(f"Could not load data: {e}")
            return

    # ── Build peak attendance from raw intervals ───────────────────────
    # Done directly from the interval rows — does NOT require the rooms
    # list, so attendance shows even when the children table is empty.
    #
    # Rule: use actual_children when it is not None; otherwise fall back
    # to expected_children. expected_children may be null (CSV import only
    # sets actual_children).
    peak_attendance: dict[str, int] = {}
    for iv in day_intervals:
        rid = iv.get("room_id")
        if not rid:
            continue
        act = iv.get("actual_children")
        exp = iv.get("expected_children")

        # Prefer actual; fall back to expected only when actual is absent
        count = int(act) if act is not None else (int(exp) if exp is not None else 0)

        if count > 0:
            if peak_attendance.get(rid, 0) < count:
                peak_attendance[rid] = count

    # ── Age-up alerts (only when children exist) ──────────────────────
    age_out_kids = []
    for child in children:
        room_full = next((r for r in rooms if r["id"] == child.get("room_id")), None)
        if room_full and is_child_near_age_out(
            child.get("date_of_birth"), room_full.get("age_max_months", 72)
        ):
            age_out_kids.append((child, room_full))

    if age_out_kids:
        st.warning(
            f"⚠️ **{len(age_out_kids)} child(ren) approaching room age limit** — "
            "review and move to the next room."
        )
        with st.expander(f"See {len(age_out_kids)} age-up suggestion(s)"):
            for child, room_full in age_out_kids:
                cname   = f"{child.get('first_name','')} {child.get('last_name','')}".strip()
                age_m   = age_in_months(child.get("date_of_birth"))
                max_m   = room_full.get("age_max_months", 72)
                in_room = room_full.get("name", "")
                st.markdown(
                    f'<div style="display:flex;justify-content:space-between;'
                    f'padding:0.5rem 0;border-bottom:1px solid #fde68a;">'
                    f'<span>👶 <strong>{cname}</strong> · {fmt_age(age_m)} · '
                    f'currently in <strong>{in_room}</strong></span>'
                    f'<span style="color:#92400e;font-size:0.82rem;">'
                    f'Limit: {fmt_age(max_m)}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

    st.markdown("---")

    # ── Centre-level summary metrics ──────────────────────────────────
    unassigned     = [c for c in children if not c.get("room_id")]
    total_enrolled = len(children)
    total_capacity = sum(r.get("licensed_capacity", 0) for r in rooms)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Active Rooms",   len(rooms))
    m2.metric("Total Enrolled", total_enrolled)
    m3.metric("Total Capacity", total_capacity)
    m4.metric(
        "Unassigned",
        len(unassigned),
        delta="need room" if unassigned else None,
        delta_color="inverse" if unassigned else "off",
    )

    if not children:
        st.info(
            "No child enrolment records found. "
            "Add children or use attendance import for actual daily counts."
        )

    if not day_intervals:
        st.caption(
            f"No attendance interval data for {date_str}. "
            "Upload a CSV on the **👶 Child Attendance** page to see peak figures."
        )
    else:
        rooms_with_data = len(peak_attendance)
        st.caption(
            f"Attendance data loaded for {date_str} — "
            f"{len(day_intervals)} interval(s) across {rooms_with_data} room(s)."
        )

    st.markdown("---")

    # ── Group children by room ────────────────────────────────────────
    room_map: dict[str, dict] = {r["id"]: r for r in rooms}
    room_kids: dict[str, list] = {r["id"]: [] for r in rooms}
    room_kids["__none__"] = []

    for child in children:
        rid = child.get("room_id")
        if rid and rid in room_kids:
            room_kids[rid].append(child)
        else:
            room_kids["__none__"].append(child)

    move_opts: dict[str, str] = {"": "— Unassigned —"}
    move_opts.update({r["id"]: r["name"] for r in rooms})

    # ── Room cards (2-up grid) ────────────────────────────────────────
    all_room_sections = [(r, room_kids[r["id"]]) for r in rooms]

    for i in range(0, len(all_room_sections), 2):
        row_rooms = all_room_sections[i : i + 2]
        cols      = st.columns(len(row_rooms))

        for col, (room, kids) in zip(cols, row_rooms):
            with col:
                _render_room_card(
                    room=room,
                    kids=kids,
                    enrolled_counts=enrolled_counts,
                    peak_attendance=peak_attendance,
                    date_str=date_str,
                    room_map=room_map,
                    move_opts=move_opts,
                )

    # ── Unassigned children ───────────────────────────────────────────
    if room_kids["__none__"]:
        st.markdown("---")
        st.markdown(
            f'<div style="background:#fff8f0;border:2px solid #f59e0b;'
            f'border-radius:12px;padding:0.65rem 0.9rem;margin-bottom:0.8rem;">'
            f'<span style="font-family:DM Serif Display,serif;font-size:1rem;'
            f'color:#92400e;">⚠️ Unassigned Children ({len(room_kids["__none__"])})'
            f'</span></div>',
            unsafe_allow_html=True,
        )
        for child in room_kids["__none__"]:
            _render_child_row(child, room_map, move_opts, "#f59e0b")


# ─────────────────────────────────────────────────────────────────────────────
# Room card
# ─────────────────────────────────────────────────────────────────────────────

def _render_room_card(
    room: dict,
    kids: list,
    enrolled_counts: dict[str, int],
    peak_attendance: dict[str, int],
    date_str: str,
    room_map: dict,
    move_opts: dict,
):
    rid    = room["id"]
    colour = room.get("colour", "#3498DB")
    rname  = room.get("name", "Room")
    cap    = room.get("licensed_capacity", 0)

    n_enrolled = enrolled_counts.get(rid, 0)
    vacancy    = max(0, cap - n_enrolled)
    peak       = peak_attendance.get(rid)   # None = no interval data for this room

    # Capacity bar fill — use peak attendance when available, else enrolment
    fill_n   = peak if peak is not None else n_enrolled
    fill_pct = round((fill_n / cap) * 100) if cap > 0 else 0
    if fill_pct >= 100:
        bar_colour = "#dc2626"
    elif fill_pct >= 80:
        bar_colour = "#d97706"
    else:
        bar_colour = colour

    # Peak cell content
    if peak is not None:
        peak_value_html = (
            f'<div style="font-family:DM Serif Display,serif;font-size:1.4rem;'
            f'color:#1d4ed8;line-height:1;">{peak}</div>'
            f'<div style="font-size:0.65rem;color:#7a90a8;text-transform:uppercase;'
            f'letter-spacing:0.04em;margin-top:2px;">Peak {date_str[5:]}</div>'
        )
    else:
        peak_value_html = (
            f'<div style="font-family:DM Serif Display,serif;font-size:1.4rem;'
            f'color:#cbd5e1;line-height:1;">—</div>'
            f'<div style="font-size:0.65rem;color:#94a3b8;text-transform:uppercase;'
            f'letter-spacing:0.04em;margin-top:2px;">No data</div>'
        )

    st.markdown(
        f'<div style="border:2px solid {colour};border-radius:12px;'
        f'overflow:hidden;margin-bottom:0.6rem;">'

        # Coloured header band
        f'<div style="background:{colour};padding:0.65rem 0.9rem;'
        f'display:flex;justify-content:space-between;align-items:center;">'
        f'<span style="font-family:DM Serif Display,serif;font-size:1rem;'
        f'color:#ffffff;">{rname}</span>'
        f'<span style="font-size:0.8rem;color:rgba(255,255,255,0.9);font-weight:600;">'
        f'Cap {cap}</span>'
        f'</div>'

        # Fill bar
        f'<div style="height:4px;background:#f0f4f8;">'
        f'<div style="height:4px;width:{min(fill_pct,100)}%;background:{bar_colour};'
        f'transition:width 0.3s;"></div></div>'

        # Stats row: Enrolled | Vacancies | Peak
        f'<div style="display:grid;grid-template-columns:1fr 1fr 1fr;'
        f'gap:0;border-top:1px solid {colour}22;">'

        f'<div style="padding:0.55rem 0.5rem;text-align:center;'
        f'border-right:1px solid #f0f4f8;">'
        f'<div style="font-family:DM Serif Display,serif;font-size:1.4rem;'
        f'color:#0d1f35;line-height:1;">{n_enrolled}</div>'
        f'<div style="font-size:0.65rem;color:#7a90a8;text-transform:uppercase;'
        f'letter-spacing:0.04em;margin-top:2px;">Enrolled</div>'
        f'</div>'

        f'<div style="padding:0.55rem 0.5rem;text-align:center;'
        f'border-right:1px solid #f0f4f8;">'
        f'<div style="font-family:DM Serif Display,serif;font-size:1.4rem;'
        f'color:{"#14532d" if vacancy > 0 else "#991b1b"};line-height:1;">{vacancy}</div>'
        f'<div style="font-size:0.65rem;color:#7a90a8;text-transform:uppercase;'
        f'letter-spacing:0.04em;margin-top:2px;">Vacancies</div>'
        f'</div>'

        f'<div style="padding:0.55rem 0.5rem;text-align:center;">'
        f'{peak_value_html}'
        f'</div>'

        f'</div></div>',
        unsafe_allow_html=True,
    )

    # Children list + move controls
    if not kids:
        st.caption("No children enrolled in this room." if n_enrolled == 0
                   else "No child records assigned to this room yet.")
    else:
        for child in kids:
            _render_child_row(child, room_map, move_opts, colour)

    st.markdown("")


# ─────────────────────────────────────────────────────────────────────────────
# Child row with move control
# ─────────────────────────────────────────────────────────────────────────────

def _render_child_row(child: dict, room_map: dict, move_opts: dict, accent: str):
    cid         = child.get("id", "")
    first       = child.get("first_name", "") or ""
    last        = child.get("last_name", "")  or ""
    cname       = f"{first} {last}".strip() or "Unnamed"
    age_m       = age_in_months(child.get("date_of_birth"))
    age_s       = fmt_age(age_m)
    cur_room_id = child.get("room_id", "")

    cur_room = room_map.get(cur_room_id)
    near_out = cur_room and is_child_near_age_out(
        child.get("date_of_birth"), cur_room.get("age_max_months", 72)
    )
    age_warn = " ⚠️" if near_out else ""

    with st.container():
        c1, c2 = st.columns([3, 2])
        c1.markdown(
            f'<div style="padding:0.3rem 0;font-size:0.88rem;color:#0d1f35;">'
            f'👶 <strong>{cname}</strong>'
            f'<span style="font-size:0.78rem;color:#7a90a8;margin-left:0.5rem;">'
            f'{age_s}{age_warn}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

        move_key = f"move_{cid}"
        selected = c2.selectbox(
            "Move to",
            options=list(move_opts.keys()),
            format_func=lambda x: move_opts[x],
            index=list(move_opts.keys()).index(cur_room_id)
                   if cur_room_id in move_opts else 0,
            key=move_key,
            label_visibility="collapsed",
        )

        if selected != cur_room_id:
            if st.button("Move", key=f"confirm_move_{cid}", type="primary"):
                try:
                    move_child_to_room(cid, selected or None)
                    new_room_name = move_opts.get(selected, "unassigned")
                    toast_success(f"{cname} moved to {new_room_name}.")
                    st.rerun()
                except Exception as e:
                    toast_error(str(e))
