# pages/child_attendance.py — Child Attendance / Room Occupancy
#
# MVP: enter expected and actual child counts per room per 15-minute interval.
# No individual child profiles required.
# Data feeds ratio dashboard and roster validation automatically.

import streamlit as st
from datetime import date, datetime

from utils.attendance_queries import (
    generate_intervals, fetch_intervals_for_room,
    upsert_all_intervals, summarise_day,
    fetch_intervals_for_centre,
    get_children_count_for_room_at_time,
)
from utils.room_queries import fetch_rooms
from utils.staff_queries import fetch_centres
from utils.centre_queries import fetch_centre_by_id
# compute_ratio and now_time_str removed from this import.
# now_time_str does not exist in older deployments of ratio_engine.py,
# which caused the ImportError on startup. datetime is already imported above.
from utils.helpers import toast_success, toast_error


def render():
    # ── Header ────────────────────────────────────────────────────────
    h1, h2 = st.columns([4, 1])
    h1.title("Child Attendance")
    h1.markdown(
        '<p class="page-sub">Enter expected and actual child counts per room '
        "· 15-minute intervals · feeds ratio monitor automatically</p>",
        unsafe_allow_html=True,
    )
    with h2:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("📊  Ratio Monitor", use_container_width=True):
            st.session_state.page = "ratio_dashboard"
            st.rerun()

    # ── Centre selector ───────────────────────────────────────────────
    centres = fetch_centres()
    if not centres:
        st.warning("No centres configured. Go to **🏫 Centres** to create one first.")
        return

    centre_opts = {c["id"]: c["name"] for c in centres}
    saved_centre = (
        st.session_state.get("attendance_centre_id")
        or st.session_state.get("selected_centre_id")
        or centres[0]["id"]
    )

    cc1, cc2, cc3 = st.columns([2, 1, 1])
    centre_id = cc1.selectbox(
        "Centre",
        options=list(centre_opts.keys()),
        format_func=lambda x: centre_opts[x],
        index=list(centre_opts.keys()).index(saved_centre)
               if saved_centre in centre_opts else 0,
        key="att_centre_sel",
    )
    st.session_state.attendance_centre_id = centre_id

    # Date picker
    attendance_date = cc2.date_input(
        "Date",
        value=date.today(),
        key="att_date",
        format="DD/MM/YYYY",
    )

    # ── Load centre for operating hours ──────────────────────────────
    with st.spinner("Loading…"):
        try:
            centre  = fetch_centre_by_id(centre_id)
            rooms   = fetch_rooms(centre_id)
        except Exception as e:
            toast_error(f"Could not load data: {e}")
            return

    if not rooms:
        st.info(
            "No rooms configured for this centre. "
            "Go to **🚪 Rooms** to add rooms first."
        )
        return

    opens_at  = centre.get("opens_at")  if centre else None
    closes_at = centre.get("closes_at") if centre else None
    intervals = generate_intervals(opens_at, closes_at)

    if not intervals:
        st.error(
            "Could not generate intervals — centre opening/closing times are not set. "
            "Go to **🏫 Centres → Edit** to set them."
        )
        return

    # ── Room selector ─────────────────────────────────────────────────
    room_opts = {r["id"]: r["name"] for r in rooms}
    room_id   = cc3.selectbox(
        "Room",
        options=list(room_opts.keys()),
        format_func=lambda x: room_opts[x],
        key="att_room_sel",
    )
    selected_room = next((r for r in rooms if r["id"] == room_id), {})

    date_str  = attendance_date.isoformat()
    colour    = selected_room.get("colour", "#3498DB")
    capacity  = selected_room.get("licensed_capacity", 0)
    r_staff   = selected_room.get("required_ratio_staff", 1)
    r_children= selected_room.get("required_ratio_children", 4)

    # ── Load existing data for this room / date ───────────────────────
    with st.spinner("Loading intervals…"):
        try:
            saved = fetch_intervals_for_room(room_id, date_str)
        except Exception as e:
            toast_error(f"Could not load attendance data: {e}")
            saved = []

    # Build lookup: interval_start → saved row
    saved_map = {s["interval_start"]: s for s in saved}
    n_saved   = sum(1 for s in saved if s.get("expected_children", 0) > 0)

    st.markdown("---")

    # ── Day summary strip ─────────────────────────────────────────────
    _render_day_summary(saved, rooms, room_id, centre_id, date_str)

    st.markdown("---")

    # ── Room header ───────────────────────────────────────────────────
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:0.75rem;margin-bottom:0.5rem;">'
        f'<div style="width:14px;height:14px;border-radius:50%;background:{colour};'
        f'box-shadow:0 0 0 3px {colour}30;flex-shrink:0;"></div>'
        f'<span style="font-family:DM Serif Display,serif;font-size:1.1rem;color:#0d1f35;">'
        f'{selected_room.get("name","Room")}</span>'
        f'<span style="font-size:0.82rem;color:#7a90a8;margin-left:0.5rem;">'
        f'Capacity {capacity} · Ratio 1:{r_children} · '
        f'{attendance_date.strftime("%A %-d %B %Y")}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    if n_saved:
        st.caption(f"✅ {n_saved} of {len(intervals)} intervals have data saved.")
    else:
        st.info(
            "No attendance data entered yet for this room and date. "
            "Fill in the expected children column below and click **Save**."
        )

    # ── Helper: show whether we're in a shift right now ──────────────
    now = datetime.now().strftime("%H:%M:%S")
    is_today = attendance_date == date.today()

    # ── Interval entry form ───────────────────────────────────────────
    st.markdown(
        "**Enter expected and actual child counts for each 15-minute interval.**  \n"
        "Expected = planned headcount. Actual = observed (leave blank if not yet known)."
    )

    # Column headers
    hc0, hc1, hc2, hc3, hc4 = st.columns([1.8, 1.4, 1.4, 1.4, 2.0])
    hc0.markdown("**Time**")
    hc1.markdown("**Expected**")
    hc2.markdown("**Actual**")
    hc3.markdown("**Ratio status**")
    hc4.markdown("**Notes**")

    form_rows = []
    with st.form(key=f"attendance_form_{room_id}_{date_str}"):

        for iv in intervals:
            istart  = iv["interval_start"]
            iend    = iv["interval_end"]
            label   = iv["label"]
            existing = saved_map.get(istart, {})

            # Is this the current 15-min window?
            is_now  = is_today and istart <= now < iend

            label_html = (
                f'<span style="font-size:0.82rem;font-weight:{"700" if is_now else "400"};'
                f'color:{"#0d1f35" if is_now else "#475569"};">'
                f'{"▶ " if is_now else ""}{label}</span>'
            )

            c0, c1, c2, c3, c4 = st.columns([1.8, 1.4, 1.4, 1.4, 2.0])

            c0.markdown(label_html, unsafe_allow_html=True)

            exp = c1.number_input(
                "exp",
                min_value=0, max_value=capacity or 100,
                value=int(existing.get("expected_children") or 0),
                key=f"exp_{istart}",
                label_visibility="collapsed",
                step=1,
            )

            act_val = existing.get("actual_children")
            act = c2.number_input(
                "act",
                min_value=0, max_value=capacity or 100,
                value=int(act_val) if act_val is not None else 0,
                key=f"act_{istart}",
                label_visibility="collapsed",
                step=1,
            )
            # Treat 0 as "not recorded" only if there is also no expected count
            actual_to_save = act if (act > 0 or act_val is not None) else None

            # Inline ratio preview for this slot
            # Use actual if set, else expected
            n_children_for_ratio = act if (act_val is not None or act > 0) else exp
            if n_children_for_ratio > 0:
                # We don't know staff at this slot here — show capacity indicator only
                pct = round((n_children_for_ratio / capacity) * 100) if capacity else 0
                if pct >= 100:
                    ratio_html = '<span style="color:#991b1b;font-size:0.75rem;">⚠ Over cap</span>'
                elif pct >= 80:
                    ratio_html = f'<span style="color:#92400e;font-size:0.75rem;">{pct}% cap</span>'
                else:
                    ratio_html = f'<span style="color:#14532d;font-size:0.75rem;">{pct}% cap</span>'
            else:
                ratio_html = '<span style="color:#94a3b8;font-size:0.75rem;">—</span>'
            c3.markdown(ratio_html, unsafe_allow_html=True)

            notes_val = c4.text_input(
                "notes",
                value=existing.get("notes", "") or "",
                key=f"notes_{istart}",
                label_visibility="collapsed",
                placeholder="optional",
            )

            form_rows.append({
                "interval_start":    istart,
                "interval_end":      iend,
                "expected_children": exp,
                "actual_children":   actual_to_save,
                "notes":             notes_val,
            })

        st.markdown("")
        sc1, sc2 = st.columns([1, 4])
        submitted = sc1.form_submit_button(
            "💾  Save All Intervals",
            type="primary",
            use_container_width=True,
        )
        clear_btn = sc2.form_submit_button(
            "🔄  Reset to Saved",
            use_container_width=False,
        )

    if clear_btn:
        st.rerun()

    if submitted:
        # Only save rows that have at least an expected count
        rows_to_save = [r for r in form_rows if r["expected_children"] > 0
                        or r["actual_children"] is not None]

        if not rows_to_save:
            toast_error("Enter at least one expected or actual count before saving.")
        else:
            with st.spinner("Saving…"):
                try:
                    n = upsert_all_intervals(
                        centre_id=centre_id,
                        room_id=room_id,
                        attendance_date=date_str,
                        rows=rows_to_save,
                    )
                    toast_success(
                        f"Saved {n} interval(s) for "
                        f"{selected_room.get('name','')} — "
                        f"{attendance_date.strftime('%-d %b %Y')}."
                    )
                    st.rerun()
                except Exception as e:
                    toast_error(f"Could not save: {e}")

    # ── Tip ───────────────────────────────────────────────────────────
    st.markdown("")
    st.caption(
        "💡 **Tip:** These counts feed directly into the Ratio Monitor. "
        "Fill in 'Expected' the evening before to help with roster planning. "
        "Update 'Actual' during the day for live compliance tracking."
    )


# ── Day summary strip across all rooms ────────────────────────────────────────

def _render_day_summary(
    saved: list[dict],      # intervals for currently selected room
    rooms: list[dict],
    selected_room_id: str,
    centre_id: str,
    date_str: str,
):
    """
    Show a compact summary row per room for the selected day,
    so the user can see at a glance which rooms have data.
    """
    with st.spinner("Loading centre summary…"):
        try:
            all_intervals = fetch_intervals_for_centre(centre_id, date_str)
        except Exception:
            all_intervals = []

    st.markdown(f"### 📅 {date_str} — All Rooms")
    cols = st.columns(len(rooms)) if rooms else []

    for i, room in enumerate(rooms):
        rid    = room["id"]
        colour = room.get("colour", "#3498DB")
        rname  = room.get("name", "")
        cap    = room.get("licensed_capacity", 0)
        room_ivs = [r for r in all_intervals if r.get("room_id") == rid]

        if not room_ivs:
            n_exp  = 0
            n_act  = None
            status = "no_data"
        else:
            peak_exp = max(int(r.get("expected_children") or 0) for r in room_ivs)
            act_rows = [r for r in room_ivs if r.get("actual_children") is not None]
            peak_act = max(int(r.get("actual_children") or 0) for r in act_rows) if act_rows else None
            n_exp    = peak_exp
            n_act    = peak_act
            status   = "has_data"

        is_selected = rid == selected_room_id
        border = f"2px solid {colour}" if is_selected else "1px solid #e4edf5"
        bg     = f"{colour}12" if is_selected else "#fafcfe"

        if status == "no_data":
            content = '<span style="font-size:0.75rem;color:#94a3b8;">No data</span>'
        else:
            pct = round((n_exp / cap) * 100) if cap else 0
            act_str = f" / {n_act} actual" if n_act is not None else ""
            content = (
                f'<span style="font-size:0.78rem;color:#0d1f35;">'
                f'Peak {n_exp}{act_str}</span><br>'
                f'<span style="font-size:0.7rem;color:#7a90a8;">{pct}% of {cap}</span>'
            )

        cols[i % len(cols)].markdown(
            f'<div style="border:{border};background:{bg};border-radius:8px;'
            f'padding:0.5rem 0.6rem;text-align:center;margin-bottom:0.3rem;">'
            f'<div style="display:flex;align-items:center;justify-content:center;'
            f'gap:4px;margin-bottom:2px;">'
            f'<div style="width:7px;height:7px;border-radius:50%;background:{colour};"></div>'
            f'<span style="font-size:0.78rem;font-weight:600;color:#0d1f35;">{rname}</span>'
            f'</div>{content}</div>',
            unsafe_allow_html=True,
        )
