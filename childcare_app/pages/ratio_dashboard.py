# pages/ratio_dashboard.py  —  Screen 27: Live Ratio Dashboard
# Uses room_attendance_intervals for child counts when available,
# falling back to attendance_records (individual sign-ins) if not.

import streamlit as st
from datetime import datetime, date

from utils.room_queries import (
    fetch_rooms, fetch_today_attendance, fetch_today_shifts,
)
from utils.attendance_queries import (
    fetch_intervals_for_centre, intervals_to_slot_counts,
    get_children_count_for_room_at_time,
)
from utils.ratio_engine import (
    compute_ratio, centre_ratio_summary, build_hourly_timeline,
    STATUS_CONFIG, STATUS_BREACH, STATUS_WARNING, STATUS_COMPLIANT,
    now_time_str, fmt_time_12h,
)
from utils.ratio_queries import fetch_shifts_with_quals, counts_toward_ratio
from utils.staff_queries import fetch_centres
from utils.helpers import toast_error


REFRESH_INTERVAL = 120


def render():
    # ── Header ────────────────────────────────────────────────────────
    h1, h2, h3 = st.columns([4, 1, 1])
    h1.title("Ratio Monitor")
    h1.markdown(
        f'<p class="page-sub">Live compliance status · '
        f'{date.today().strftime("%A %-d %B %Y")}</p>',
        unsafe_allow_html=True,
    )
    with h2:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("🔄  Refresh", use_container_width=True, key="ratio_refresh_top"):
            st.session_state["ratio_last_refresh"] = datetime.now()
            st.rerun()
    with h3:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("🗒️  Breach Log", use_container_width=True):
            st.session_state.page = "ratio_breach_log"
            st.rerun()

    # ── Centre selector ───────────────────────────────────────────────
    centres = fetch_centres()
    if not centres:
        st.warning("No centres found. Set up a centre first.")
        return

    centre_opts = {c["id"]: c["name"] for c in centres}
    saved = (
        st.session_state.get("ratio_centre_id")
        or st.session_state.get("selected_centre_id")
        or centres[0]["id"]
    )

    centre_id = st.selectbox(
        "Centre",
        options=list(centre_opts.keys()),
        format_func=lambda x: centre_opts[x],
        index=list(centre_opts.keys()).index(saved) if saved in centre_opts else 0,
        key="ratio_centre_sel",
    )
    st.session_state.ratio_centre_id = centre_id

    # ── Load data ─────────────────────────────────────────────────────
    with st.spinner("Loading live data…"):
        try:
            rooms       = fetch_rooms(centre_id)
            all_shifts  = fetch_today_shifts(centre_id)
            rich_shifts = fetch_shifts_with_quals(centre_id)
            # Primary source: room_attendance_intervals
            today_intervals = fetch_intervals_for_centre(
                centre_id, date.today().isoformat()
            )
            # Fallback: individual attendance records (may be empty)
            attendance  = fetch_today_attendance(centre_id)
        except Exception as e:
            toast_error(f"Could not load data: {e}")
            return

    if not rooms:
        st.info("No rooms configured. Go to **🚪 Rooms** to set them up.")
        return

    now = now_time_str()

    # Build slot-indexed child counts from intervals (preferred source)
    interval_slot_counts = intervals_to_slot_counts(today_intervals, use_actual=True)
    has_interval_data    = bool(today_intervals)

    # ── Data source indicator ─────────────────────────────────────────
    if has_interval_data:
        st.info(
            "📋 Child counts from **Child Attendance** intervals. "
            "Update counts on the [Child Attendance](/child_attendance) page."
        )
    else:
        st.caption(
            "No attendance intervals entered for today. "
            "Child counts show 0 — enter counts on the **👶 Child Attendance** page "
            "for live ratio checking."
        )

    # ── Build per-room data ───────────────────────────────────────────
    room_results = []
    for room in rooms:
        rid = room["id"]

        # Prefer interval data; fall back to attendance_records sign-ins
        if has_interval_data:
            n_children = get_children_count_for_room_at_time(
                today_intervals, rid, now, use_actual=True
            )
        else:
            n_children = sum(
                1 for a in attendance
                if a.get("room_id") == rid and a.get("status") == "present"
            )

        n_staff = sum(
            1 for s in rich_shifts
            if s.get("room_id") == rid
            and (s.get("start_time") or "") <= now <= (s.get("end_time") or "99:99")
            and counts_toward_ratio(s)
        )

        result = compute_ratio(
            n_children, n_staff,
            room.get("required_ratio_staff", 1),
            room.get("required_ratio_children", 4),
            room.get("licensed_capacity", 0),
        )
        room_results.append({
            "room": room, "n_children": n_children,
            "n_staff": n_staff, "result": result,
        })

    summary = centre_ratio_summary(room_results)

    # ── Centre-wide status banner ─────────────────────────────────────
    _render_status_banner(summary)

    # ── Summary metrics ───────────────────────────────────────────────
    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("Active Rooms",     len(rooms))
    m2.metric("Children Present", summary["total_children"])
    m3.metric("Staff Active",     summary["total_staff"])
    m4.metric("✅ Compliant",      summary["n_compliant"])
    m5.metric("⚠️ At Limit",      summary["n_warning"],
              delta=str(summary["n_warning"]) if summary["n_warning"] else None,
              delta_color="inverse")
    m6.metric("❌ Breach",         summary["n_breach"],
              delta=str(summary["n_breach"]) if summary["n_breach"] else None,
              delta_color="inverse")

    st.markdown("---")

    # ── Room cards sorted by severity ─────────────────────────────────
    status_order = {STATUS_BREACH: 0, STATUS_WARNING: 1, STATUS_COMPLIANT: 2, "empty": 3}
    room_results.sort(key=lambda r: status_order.get(r["result"]["status"], 4))

    for i in range(0, len(room_results), 3):
        row  = room_results[i:i + 3]
        cols = st.columns(len(row))
        for col, rr in zip(cols, row):
            with col:
                _render_room_card(rr, all_shifts, centre_id, now)

    # ── Centre-wide daily timeline ────────────────────────────────────
    st.markdown("---")
    st.markdown("### 📅 Today's Coverage Timeline")
    st.caption(
        "Staff coverage per hour across all rooms. "
        "Child counts from interval attendance data when available."
    )
    _render_centre_timeline(
        rooms, all_shifts, today_intervals, interval_slot_counts,
        attendance, now, has_interval_data,
    )

    # ── Last updated ──────────────────────────────────────────────────
    st.markdown("---")
    last_refresh = st.session_state.get("ratio_last_refresh", datetime.now())
    elapsed      = int((datetime.now() - last_refresh).total_seconds())
    st.caption(
        f"Last updated: {last_refresh.strftime('%H:%M:%S')} · "
        f"{elapsed}s ago"
    )
    if st.button("🔄  Refresh Now", key="ratio_refresh_bottom"):
        st.session_state["ratio_last_refresh"] = datetime.now()
        st.rerun()


# ── Status banner ──────────────────────────────────────────────────────────────
def _render_status_banner(summary: dict):
    n_breach  = summary["n_breach"]
    n_warning = summary["n_warning"]
    pct       = summary["compliance_pct"]

    if n_breach > 0:
        st.error(
            f"❌ **{n_breach} room{'s' if n_breach > 1 else ''} in ratio breach** — "
            f"immediate action required. Expand the affected room card and click **Log Breach**."
        )
    elif n_warning > 0:
        st.warning(
            f"⚠️ **{n_warning} room{'s' if n_warning > 1 else ''} at capacity limit** — "
            f"one more child would cause a breach. Monitor closely."
        )
    else:
        st.success(f"✅ **All rooms compliant** — {pct}% compliance rate across active rooms.")


# ── Individual room card ───────────────────────────────────────────────────────
def _render_room_card(rr: dict, all_shifts: list, centre_id: str, now: str):
    room       = rr["room"]
    n_children = rr["n_children"]
    n_staff    = rr["n_staff"]
    result     = rr["result"]
    room_id    = room["id"]
    colour     = room.get("colour", "#3498DB")
    name       = room.get("name", "Room")
    r_children = room.get("required_ratio_children", 4)
    cfg        = result["config"]
    surplus    = result["surplus"]
    min_staff  = result["min_staff"]
    fill_pct   = result["capacity_pct"]
    fill_colour = (
        "#dc2626" if fill_pct >= 100 else
        "#d97706" if fill_pct >= 80  else
        colour
    )

    st.markdown(
        f'<div style="border:2px solid {cfg["border"]};background:{cfg["bg"]};'
        f'border-radius:14px;overflow:hidden;margin-bottom:0.5rem;">'
        f'<div style="background:linear-gradient(135deg,{colour},{colour}cc);'
        f'padding:0.7rem 1rem;display:flex;justify-content:space-between;align-items:center;">'
        f'<span style="font-family:DM Serif Display,serif;font-size:1rem;color:#fff;">{name}</span>'
        f'<span style="font-size:1.3rem;">{cfg["icon"]}</span></div>'
        f'<div style="height:3px;background:rgba(0,0,0,0.08);">'
        f'<div style="height:3px;width:{min(fill_pct,100)}%;background:{fill_colour};"></div></div>'
        f'<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:1px;'
        f'background:{cfg["border"]};">'
        f'<div style="background:{cfg["bg"]};padding:0.7rem 0;text-align:center;">'
        f'<div style="font-family:DM Serif Display,serif;font-size:1.8rem;'
        f'line-height:1;color:#0d1f35;">{n_children}</div>'
        f'<div style="font-size:0.62rem;text-transform:uppercase;letter-spacing:0.06em;'
        f'color:#7a90a8;margin-top:2px;">Children</div></div>'
        f'<div style="background:{cfg["bg"]};padding:0.7rem 0;text-align:center;">'
        f'<div style="font-family:DM Serif Display,serif;font-size:1.8rem;'
        f'line-height:1;color:#0d1f35;">{n_staff}</div>'
        f'<div style="font-size:0.62rem;text-transform:uppercase;letter-spacing:0.06em;'
        f'color:#7a90a8;margin-top:2px;">Staff</div></div>'
        f'<div style="background:{cfg["bg"]};padding:0.7rem 0;text-align:center;">'
        f'<div style="font-family:DM Serif Display,serif;font-size:1.8rem;'
        f'line-height:1;color:{cfg["text"]};">{min_staff}</div>'
        f'<div style="font-size:0.62rem;text-transform:uppercase;letter-spacing:0.06em;'
        f'color:#7a90a8;margin-top:2px;">Required</div></div>'
        f'</div>'
        f'<div style="padding:0.45rem 0.9rem;background:{cfg["bg"]};'
        f'border-top:1px solid {cfg["border"]};font-size:0.8rem;'
        f'color:{cfg["text"]};font-weight:600;">'
        f'{cfg["icon"]} {cfg["label"]} · 1:{r_children} · {fill_pct}% capacity'
        + (f' · <strong>Need {abs(surplus)} more</strong>' if surplus < 0 else
           f' · {surplus} above min'                        if surplus > 0 else
           ' · At minimum')
        + f'</div></div>',
        unsafe_allow_html=True,
    )

    bc1, bc2, bc3 = st.columns(3)
    if bc1.button("👁 Detail", key=f"rd_{room_id}", use_container_width=True):
        st.session_state.viewing_room_id     = room_id
        st.session_state.viewing_room_centre = centre_id
        st.session_state.page = "ratio_detail"
        st.rerun()
    if bc2.button("🚪 Room", key=f"rr_{room_id}", use_container_width=True):
        st.session_state.viewing_room_id     = room_id
        st.session_state.viewing_room_centre = centre_id
        st.session_state.page = "room_detail"
        st.rerun()
    if result["status"] in (STATUS_BREACH, STATUS_WARNING):
        if bc3.button("📋 Log", key=f"lb_{room_id}", use_container_width=True):
            st.session_state.log_breach_room_id   = room_id
            st.session_state.log_breach_centre_id = centre_id
            st.session_state.log_breach_children  = n_children
            st.session_state.log_breach_staff     = n_staff
            st.session_state.log_breach_min_staff = min_staff
            st.session_state.page = "ratio_breach_log"
            st.rerun()


# ── Centre-wide hourly timeline ────────────────────────────────────────────────
def _render_centre_timeline(
    rooms: list,
    all_shifts: list,
    today_intervals: list[dict],
    interval_slot_counts: dict[str, list[int]],
    attendance: list,
    now: str,
    has_interval_data: bool,
):
    """
    One row per room, one column per hour (6 AM – 7 PM).
    Child counts from interval data when available, else attendance records.
    """
    if not rooms:
        return

    from utils.roster_engine import TOTAL_SLOTS, SLOTS_PER_HOUR, time_to_slot
    now_hour = datetime.now().hour
    hours    = list(range(6, 20))

    # Header
    header_cols = st.columns([2] + [1] * len(hours))
    header_cols[0].markdown(
        '<div style="font-size:0.7rem;font-weight:600;color:#7a90a8;'
        'text-transform:uppercase;letter-spacing:0.05em;padding:0.3rem 0;">Room</div>',
        unsafe_allow_html=True,
    )
    for i, hour in enumerate(hours):
        is_now  = hour == now_hour
        style   = "font-weight:800;color:#0d1f35;" if is_now else "font-weight:400;color:#94a3b8;"
        header_cols[i + 1].markdown(
            f'<div style="text-align:center;font-size:0.68rem;{style}">{hour:02d}</div>',
            unsafe_allow_html=True,
        )

    for room in rooms:
        rid        = room["id"]
        colour     = room.get("colour", "#3498DB")
        r_staff    = room.get("required_ratio_staff", 1)
        r_children = room.get("required_ratio_children", 4)
        capacity   = room.get("licensed_capacity", 0)

        # Get slot-level child counts for this room
        slot_counts = interval_slot_counts.get(rid)   # list[int] or None

        row_cols = st.columns([2] + [1] * len(hours))
        row_cols[0].markdown(
            f'<div style="display:flex;align-items:center;gap:5px;padding:0.15rem 0;">'
            f'<div style="width:8px;height:8px;border-radius:50%;'
            f'background:{colour};flex-shrink:0;"></div>'
            f'<span style="font-size:0.8rem;color:#1e3a55;white-space:nowrap;'
            f'overflow:hidden;text-overflow:ellipsis;">{room.get("name","")}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

        for i, hour in enumerate(hours):
            hstr    = f"{hour:02d}:00:00"
            slot_idx = (hour - 6) * 4   # slot index for HH:00

            # Child count for this hour slot
            if slot_counts is not None and slot_idx < len(slot_counts):
                n_children = slot_counts[slot_idx]
            elif has_interval_data:
                n_children = 0
            else:
                n_children = sum(
                    1 for a in attendance
                    if a.get("room_id") == rid and a.get("status") == "present"
                )

            n_staff = sum(
                1 for s in all_shifts
                if s.get("room_id") == rid
                and (s.get("start_time") or "") <= hstr
                <= (s.get("end_time") or "99:99:99")
            )

            result  = compute_ratio(n_children, n_staff, r_staff, r_children, capacity)
            cfg     = result["config"]
            is_now  = hour == now_hour
            border  = f"2px solid {colour}" if is_now else f"1px solid {cfg['border']}"

            row_cols[i + 1].markdown(
                f'<div style="background:{cfg["bg"]};border:{border};'
                f'border-radius:4px;text-align:center;padding:0.18rem 0;'
                f'font-size:0.75rem;line-height:1.4;">'
                f'<div>{cfg["icon"]}</div>'
                f'<div style="color:#475569;font-size:0.6rem;">'
                f'{n_staff}s{"/" + str(n_children) + "c" if n_children else ""}'
                f'</div></div>',
                unsafe_allow_html=True,
            )

    st.markdown(
        '<div style="display:flex;gap:1.2rem;margin-top:0.5rem;flex-wrap:wrap;">'
        + "".join(
            f'<span style="font-size:0.75rem;color:#64748b;">'
            f'{cfg["icon"]} {cfg["label"]}</span>'
            for cfg in [STATUS_CONFIG["compliant"], STATUS_CONFIG["warning"],
                        STATUS_CONFIG["breach"]]
        )
        + '<span style="font-size:0.75rem;color:#64748b;">s=staff c=children</span>'
        + '</div>',
        unsafe_allow_html=True,
    )
