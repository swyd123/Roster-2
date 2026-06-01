# pages/ratio_dashboard.py  —  Screen 27: Live Ratio Dashboard
# Completely rebuilt with richer cards, centre-wide timeline,
# auto-refresh countdown, and qualification-aware staff counts.

import streamlit as st
from datetime import datetime, date
import time as _time_module

from utils.room_queries import (
    fetch_rooms, fetch_today_attendance, fetch_today_shifts,
)
from utils.ratio_engine import (
    compute_ratio, centre_ratio_summary, build_hourly_timeline,
    STATUS_CONFIG, STATUS_BREACH, STATUS_WARNING, STATUS_COMPLIANT,
    now_time_str, fmt_time_12h,
)
from utils.ratio_queries import fetch_shifts_with_quals, counts_toward_ratio
from utils.staff_queries import fetch_centres
from utils.helpers import toast_error


# ── How often to suggest a refresh (seconds) ─────────────────────────────────
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
    saved       = (
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
            rooms      = fetch_rooms(centre_id)
            attendance = fetch_today_attendance(centre_id)
            all_shifts = fetch_today_shifts(centre_id)
            rich_shifts = fetch_shifts_with_quals(centre_id)
        except Exception as e:
            toast_error(f"Could not load data: {e}")
            return

    if not rooms:
        st.info("No rooms configured. Go to **🚪 Rooms** in the sidebar to set them up.")
        return

    now = now_time_str()

    # ── Build per-room data ───────────────────────────────────────────
    room_results = []
    for room in rooms:
        rid = room["id"]

        n_children = sum(
            1 for a in attendance
            if a.get("room_id") == rid and a.get("status") == "present"
        )
        # Use rich_shifts to check who actually counts toward ratio
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
    m1.metric("Active Rooms",      len(rooms))
    m2.metric("Children Present",  summary["total_children"])
    m3.metric("Staff Active",      summary["total_staff"])
    m4.metric("✅ Compliant",       summary["n_compliant"])
    m5.metric("⚠️ At Limit",       summary["n_warning"],
              delta=str(summary["n_warning"]) if summary["n_warning"] else None,
              delta_color="inverse")
    m6.metric("❌ Breach",          summary["n_breach"],
              delta=str(summary["n_breach"]) if summary["n_breach"] else None,
              delta_color="inverse")

    st.markdown("---")

    # ── Room cards ────────────────────────────────────────────────────
    # Sort: breaches first, then warnings, then compliant, then empty
    status_order = {STATUS_BREACH: 0, STATUS_WARNING: 1,
                    STATUS_COMPLIANT: 2, "empty": 3}
    room_results.sort(key=lambda r: status_order.get(r["result"]["status"], 4))

    # 3 cards per row for a denser, more professional look
    for i in range(0, len(room_results), 3):
        row   = room_results[i:i + 3]
        cols  = st.columns(len(row))
        for col, rr in zip(cols, row):
            with col:
                _render_room_card(rr, all_shifts, attendance, centre_id, now)

    # ── Centre-wide daily timeline ────────────────────────────────────
    st.markdown("---")
    st.markdown("### 📅 Today's Coverage Timeline")
    st.caption(
        "Predicted staff coverage across all rooms by hour. "
        "Based on today's rostered shifts and current attendance."
    )
    _render_centre_timeline(rooms, all_shifts, attendance, now)

    # ── Last-updated footer ───────────────────────────────────────────
    st.markdown("---")
    last_refresh = st.session_state.get("ratio_last_refresh", datetime.now())
    elapsed      = int((datetime.now() - last_refresh).total_seconds())
    st.caption(
        f"Last updated: {last_refresh.strftime('%H:%M:%S')} · "
        f"{elapsed}s ago · Page refreshes every {REFRESH_INTERVAL}s"
    )
    if st.button("🔄  Refresh Now", key="ratio_refresh_bottom"):
        st.session_state["ratio_last_refresh"] = datetime.now()
        st.rerun()


# ── Status banner ──────────────────────────────────────────────────────────────
def _render_status_banner(summary: dict):
    n_breach  = summary["n_breach"]
    n_warning = summary["n_warning"]
    pct       = summary["compliance_pct"]
    cfg       = summary["overall_config"]

    if n_breach > 0:
        msg = (
            f"❌ **{n_breach} room{'s' if n_breach > 1 else ''} in ratio breach** — "
            f"immediate action required. Expand the affected room card and click **Log Breach**."
        )
        st.error(msg)
    elif n_warning > 0:
        msg = (
            f"⚠️ **{n_warning} room{'s' if n_warning > 1 else ''} at capacity limit** — "
            f"one more child would cause a breach. Monitor closely."
        )
        st.warning(msg)
    else:
        st.success(
            f"✅ **All rooms compliant** — {pct}% compliance rate across active rooms."
        )


# ── Individual room card ───────────────────────────────────────────────────────
def _render_room_card(rr: dict, all_shifts: list, attendance: list, centre_id: str, now: str):
    room       = rr["room"]
    n_children = rr["n_children"]
    n_staff    = rr["n_staff"]
    result     = rr["result"]
    room_id    = room["id"]
    colour     = room.get("colour", "#3498DB")
    name       = room.get("name", "Room")
    capacity   = room.get("licensed_capacity", 0)
    r_children = room.get("required_ratio_children", 4)
    cfg        = result["config"]
    surplus    = result["surplus"]
    min_staff  = result["min_staff"]

    # Capacity fill bar percentage
    fill_pct = result["capacity_pct"]
    fill_colour = (
        "#dc2626" if fill_pct >= 100 else
        "#d97706" if fill_pct >= 80  else
        colour
    )

    st.markdown(
        # Outer card
        f'<div style="border:2px solid {cfg["border"]};background:{cfg["bg"]};'
        f'border-radius:14px;overflow:hidden;margin-bottom:0.5rem;">'

        # Coloured top stripe with room name
        f'<div style="background:linear-gradient(135deg,{colour},{colour}cc);'
        f'padding:0.7rem 1rem;display:flex;justify-content:space-between;align-items:center;">'
        f'<span style="font-family:DM Serif Display,serif;font-size:1rem;'
        f'color:#fff;font-weight:400;">{name}</span>'
        f'<span style="font-size:1.3rem;">{cfg["icon"]}</span>'
        f'</div>'

        # Capacity fill bar
        f'<div style="height:3px;background:rgba(0,0,0,0.08);">'
        f'<div style="height:3px;width:{min(fill_pct,100)}%;background:{fill_colour};"></div>'
        f'</div>'

        # Numbers grid
        f'<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:1px;'
        f'background:{cfg["border"]};margin:0;">'

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

        # Status bar
        f'<div style="padding:0.45rem 0.9rem;background:{cfg["bg"]};'
        f'border-top:1px solid {cfg["border"]};font-size:0.8rem;'
        f'color:{cfg["text"]};font-weight:600;">'
        f'{cfg["icon"]} {cfg["label"]} · 1:{r_children} · {fill_pct}% capacity'
        + (f' · <strong>Need {abs(surplus)} more</strong>' if surplus < 0 else
           f' · {surplus} above min'                       if surplus > 0 else
           ' · At minimum')
        + f'</div></div>',
        unsafe_allow_html=True,
    )

    # Action buttons
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
def _render_centre_timeline(rooms: list, all_shifts: list, attendance: list, now: str):
    """
    Shows a row per room, columns per hour.
    Each cell is coloured by its ratio status at that hour.
    """
    if not rooms:
        return

    now_hour = datetime.now().hour
    hours    = list(range(6, 20))   # 6 AM to 7 PM

    # Header row
    header_cols = st.columns([2] + [1] * len(hours))
    header_cols[0].markdown(
        '<div style="font-size:0.7rem;font-weight:600;color:#7a90a8;'
        'text-transform:uppercase;letter-spacing:0.05em;padding:0.3rem 0;">Room</div>',
        unsafe_allow_html=True,
    )
    for i, hour in enumerate(hours):
        is_now  = hour == now_hour
        label   = f"{hour:02d}"
        style   = ("font-weight:800;color:#0d1f35;" if is_now
                   else "font-weight:400;color:#94a3b8;")
        header_cols[i + 1].markdown(
            f'<div style="text-align:center;font-size:0.68rem;{style}">{label}</div>',
            unsafe_allow_html=True,
        )

    # One row per room
    for room in rooms:
        rid        = room["id"]
        colour     = room.get("colour", "#3498DB")
        r_staff    = room.get("required_ratio_staff", 1)
        r_children = room.get("required_ratio_children", 4)
        capacity   = room.get("licensed_capacity", 0)

        n_children = sum(
            1 for a in attendance
            if a.get("room_id") == rid and a.get("status") == "present"
        )

        row_cols = st.columns([2] + [1] * len(hours))

        # Room label cell
        row_cols[0].markdown(
            f'<div style="display:flex;align-items:center;gap:5px;padding:0.15rem 0;">'
            f'<div style="width:8px;height:8px;border-radius:50%;'
            f'background:{colour};flex-shrink:0;"></div>'
            f'<span style="font-size:0.8rem;color:#1e3a55;white-space:nowrap;'
            f'overflow:hidden;text-overflow:ellipsis;">{room.get("name","")}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

        # Hour cells
        for i, hour in enumerate(hours):
            hstr    = f"{hour:02d}:00:00"
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
                f'<div style="color:#475569;font-size:0.6rem;">{n_staff}s</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

    # Legend
    st.markdown(
        '<div style="display:flex;gap:1.2rem;margin-top:0.5rem;flex-wrap:wrap;">'
        + "".join(
            f'<span style="font-size:0.75rem;color:#64748b;">'
            f'{cfg["icon"]} {cfg["label"]}</span>'
            for cfg in [STATUS_CONFIG["compliant"], STATUS_CONFIG["warning"],
                        STATUS_CONFIG["breach"]]
        )
        + '<span style="font-size:0.75rem;color:#64748b;">s = staff rostered</span>'
        + '</div>',
        unsafe_allow_html=True,
    )
