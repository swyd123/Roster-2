# pages/ratio_detail.py  —  Screen 28: Ratio Detail (per-room deep dive)
# Rebuilt with: qualification display, counts-toward-ratio flag,
# children detail, enriched hourly timeline, upcoming risk alerts.

import streamlit as st
from datetime import datetime, date

from utils.room_queries import (
    fetch_room_by_id, fmt_age_range, fmt_age, age_in_months,
)
from utils.ratio_engine import (
    compute_ratio, build_hourly_timeline, find_risk_points,
    STATUS_CONFIG, STATUS_BREACH, STATUS_WARNING,
    now_time_str, fmt_time_12h,
)
from utils.ratio_queries import (
    fetch_room_shifts_with_quals, fetch_room_attendance_with_children,
    extract_quals_for_shift, has_diploma, counts_toward_ratio,
)
from utils.helpers import toast_error, fmt_date


def render():
    room_id   = st.session_state.get("viewing_room_id")
    centre_id = st.session_state.get("viewing_room_centre")

    if not room_id:
        st.warning("No room selected.")
        if st.button("← Ratio Dashboard"):
            st.session_state.page = "ratio_dashboard"
            st.rerun()
        return

    # ── Back navigation ───────────────────────────────────────────────
    bc, _ = st.columns([1, 10])
    with bc:
        st.markdown('<div class="back-btn">', unsafe_allow_html=True)
        if st.button("← Monitor", key="rd_back"):
            st.session_state.page = "ratio_dashboard"
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    # ── Load data ─────────────────────────────────────────────────────
    with st.spinner("Loading…"):
        try:
            room       = fetch_room_by_id(room_id)
            shifts     = fetch_room_shifts_with_quals(room_id)
            attendance = fetch_room_attendance_with_children(room_id)
        except Exception as e:
            toast_error(f"Could not load: {e}")
            return

    if not room:
        toast_error("Room not found.")
        st.session_state.page = "ratio_dashboard"
        st.rerun()
        return

    name       = room.get("name", "Room")
    colour     = room.get("colour", "#3498DB")
    r_staff    = room.get("required_ratio_staff", 1)
    r_children = room.get("required_ratio_children", 4)
    capacity   = room.get("licensed_capacity", 0)
    requires_diploma = room.get("requires_diploma", False)

    now     = now_time_str()
    now_h   = datetime.now().hour

    # Present children (signed in, not yet signed out)
    present_attendance = [
        a for a in attendance if a.get("status") == "present"
    ]
    n_children = len(present_attendance)

    # Active shifts right now that count toward ratio
    active_shifts = [
        s for s in shifts
        if (s.get("start_time") or "") <= now <= (s.get("end_time") or "99:99")
        and counts_toward_ratio(s)
    ]
    n_staff = len(active_shifts)

    result = compute_ratio(n_children, n_staff, r_staff, r_children, capacity)
    cfg    = result["config"]

    # ── Page header ───────────────────────────────────────────────────
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:0.8rem;margin-bottom:0.2rem;">'
        f'<div style="width:18px;height:18px;border-radius:50%;background:{colour};'
        f'box-shadow:0 0 0 4px {colour}30;flex-shrink:0;"></div>'
        f'<h1 style="margin:0;font-family:DM Serif Display,serif;">'
        f'{name} — Ratio Detail</h1>'
        f'<span style="margin-left:auto;font-size:2rem;">{cfg["icon"]}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<p class="page-sub">'
        f'{date.today().strftime("%A %-d %B %Y")} · '
        f'{fmt_age_range(room.get("age_min_months", 0), room.get("age_max_months", 72))} · '
        f'Required {r_staff} educator : {r_children} children'
        + (" · Diploma required" if requires_diploma else "")
        + "</p>",
        unsafe_allow_html=True,
    )

    # ── Current status hero panel ─────────────────────────────────────
    surplus   = result["surplus"]
    min_staff = result["min_staff"]
    shortfall = result["shortfall"]

    st.markdown(
        f'<div style="background:{cfg["bg"]};border:2px solid {cfg["border"]};'
        f'border-radius:14px;padding:1.4rem 1.8rem;display:flex;'
        f'gap:2rem;align-items:center;flex-wrap:wrap;margin:0.5rem 0 1rem;">'
        f'<div style="font-size:3.5rem;line-height:1;">{cfg["icon"]}</div>'
        f'<div style="flex:1;min-width:200px;">'
        f'<div style="font-family:DM Serif Display,serif;font-size:1.5rem;'
        f'color:{cfg["text"]};">{cfg["label"]}</div>'
        f'<div style="font-size:0.92rem;color:{cfg["text"]};margin-top:0.35rem;">'
        f'<strong>{n_staff}</strong> staff and <strong>{n_children}</strong> children. '
        f'Minimum required: <strong>{min_staff}</strong>.'
        + (f' <strong>Add {shortfall} more staff immediately.</strong>' if shortfall > 0 else
           f' You have {surplus} staff above minimum.' if surplus > 0 else
           " Exactly at minimum — one child arrival would require another staff member.")
        + f'</div></div>'
        # Three live numbers
        f'<div style="display:flex;gap:1rem;flex-wrap:wrap;">'
        + _stat_bubble(str(n_children), "Children", colour)
        + _stat_bubble(str(n_staff),    "Staff Now", colour)
        + _stat_bubble(str(min_staff),  "Min Needed", cfg["text"])
        + f'</div></div>',
        unsafe_allow_html=True,
    )

    # ── Four metrics ──────────────────────────────────────────────────
    mc1, mc2, mc3, mc4 = st.columns(4)
    mc1.metric("Current Ratio",     result["ratio_str"])
    mc2.metric("Required Ratio",    result["required_str"])
    mc3.metric("Capacity Used",     f"{result['capacity_pct']}%",
               delta=f"{result['spaces_free']} spaces free", delta_color="off")
    mc4.metric("Diploma Required",  "Yes" if requires_diploma else "No")

    st.markdown("---")

    # ── Staff & children two-column ───────────────────────────────────
    left, right = st.columns(2)

    # ── LEFT: Staff in room now ───────────────────────────────────────
    with left:
        st.markdown("### 👩‍🏫 Staff In Room Now")
        if not active_shifts:
            st.info("No educators currently active in this room.")
        else:
            for shift in active_shifts:
                _render_staff_row(shift, requires_diploma)

        # All-day schedule (shifts not active yet or already ended)
        other_shifts = [
            s for s in shifts
            if not (
                (s.get("start_time") or "") <= now <= (s.get("end_time") or "99:99")
                and counts_toward_ratio(s)
            )
        ]
        if other_shifts:
            with st.expander(f"All shifts today ({len(shifts)} total)"):
                for shift in shifts:
                    u       = shift.get("users") or {}
                    sname   = f"{u.get('first_name','')} {u.get('last_name','')}".strip()
                    start   = fmt_time_12h(shift.get("start_time"))
                    end     = fmt_time_12h(shift.get("end_time"))
                    is_now  = (
                        (shift.get("start_time") or "") <= now
                        <= (shift.get("end_time") or "99:99")
                    )
                    counts  = counts_toward_ratio(shift)
                    dot_col = "#1a6b4a" if is_now else "#cbd5e1"
                    ratio_tag = "" if counts else ' <span style="color:#ef4444;font-size:0.75rem;">(excl.)</span>'

                    st.markdown(
                        f'<div style="display:flex;justify-content:space-between;'
                        f'padding:0.35rem 0;border-bottom:1px solid #f0f4f8;">'
                        f'<span style="font-size:0.85rem;">'
                        f'<span style="display:inline-block;width:7px;height:7px;'
                        f'border-radius:50%;background:{dot_col};margin-right:6px;"></span>'
                        f'{sname}{ratio_tag}</span>'
                        f'<span style="font-size:0.78rem;color:#7a90a8;">{start}–{end}</span>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

    # ── RIGHT: Children in room now ───────────────────────────────────
    with right:
        st.markdown("### 👶 Children Present")
        if not present_attendance:
            st.info("No children currently signed in.")
        else:
            for a in present_attendance:
                child  = a.get("children") or {}
                cname  = f"{child.get('first_name','')} {child.get('last_name','')}".strip()
                age_m  = age_in_months(child.get("date_of_birth"))
                sin    = a.get("signed_in_at", "")
                if sin:
                    try:
                        t = datetime.fromisoformat(sin.replace("Z", "+00:00"))
                        sin_str = t.strftime("%-I:%M %p")
                    except Exception:
                        sin_str = sin[:5]
                else:
                    sin_str = "—"

                # Allergy / medical alert indicator
                alert = ""
                if child.get("allergies"):
                    alert += ' <span title="Has allergies" style="color:#ef4444;">⚠</span>'
                if child.get("medical_notes"):
                    alert += ' <span title="Medical notes" style="color:#3b82f6;">🏥</span>'

                st.markdown(
                    f'<div style="display:flex;justify-content:space-between;'
                    f'padding:0.4rem 0;border-bottom:1px solid #f0f4f8;align-items:center;">'
                    f'<span style="font-size:0.88rem;color:#0d1f35;">'
                    f'👶 <strong>{cname}</strong>{alert}</span>'
                    f'<span style="font-size:0.78rem;color:#7a90a8;">'
                    f'{fmt_age(age_m)} · in {sin_str}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

        # Expected but not arrived
        expected = [
            a for a in attendance
            if a.get("status") == "expected"
        ]
        if expected:
            st.markdown("")
            st.caption(f"{len(expected)} children expected but not yet arrived:")
            for a in expected:
                child = a.get("children") or {}
                cname = f"{child.get('first_name','')} {child.get('last_name','')}".strip()
                st.markdown(
                    f'<div style="font-size:0.82rem;color:#94a3b8;'
                    f'padding:0.2rem 0;">⏳ {cname}</div>',
                    unsafe_allow_html=True,
                )

    st.markdown("---")

    # ── Upcoming risk alerts ──────────────────────────────────────────
    risks = find_risk_points(shifts, n_children, r_staff, r_children, now)
    if risks:
        st.markdown("### ⚠️ Upcoming Risk Points")
        st.caption(
            "Times today when a shift ending would create a ratio problem "
            "based on current attendance."
        )
        for risk in risks:
            cfg_r = risk["result"]["config"]
            st.markdown(
                f'<div style="background:{cfg_r["bg"]};border:1px solid {cfg_r["border"]};'
                f'border-radius:8px;padding:0.75rem 1rem;margin-bottom:0.5rem;">'
                f'<strong>{risk["time_str"]}</strong> — {risk["staff_name"]}\'s shift ends. '
                f'Only <strong>{risk["remaining"]} staff</strong> will remain for '
                f'<strong>{n_children} children</strong>. '
                f'Minimum required: <strong>{risk["result"]["min_staff"]}</strong>. '
                f'{cfg_r["icon"]} <strong>{cfg_r["label"]}</strong>'
                f'</div>',
                unsafe_allow_html=True,
            )
        st.markdown("")

    # ── Hourly ratio timeline ─────────────────────────────────────────
    st.markdown("### ⏱️ Today's Ratio Timeline")
    st.caption(
        "Each cell shows the predicted ratio status for that hour. "
        "Bold border = current hour. Based on current attendance and rostered shifts."
    )
    _render_rich_timeline(shifts, n_children, r_staff, r_children, capacity, colour, now_h)

    st.markdown("---")

    # ── Action buttons ────────────────────────────────────────────────
    ab1, ab2, _ = st.columns([1.5, 1.5, 4])
    with ab1:
        if st.button("📋  Log a Breach", key="rd_log", type="primary",
                      use_container_width=True):
            st.session_state.log_breach_room_id   = room_id
            st.session_state.log_breach_centre_id = centre_id
            st.session_state.log_breach_children  = n_children
            st.session_state.log_breach_staff     = n_staff
            st.session_state.log_breach_min_staff = min_staff
            st.session_state.page = "ratio_breach_log"
            st.rerun()
    with ab2:
        if st.button("✏️  Edit Room Config", key="rd_edit_room",
                      use_container_width=True):
            st.session_state.editing_room_id = room_id
            st.session_state.page = "room_form"
            st.rerun()


# ── Helper: stat bubble ───────────────────────────────────────────────────────
def _stat_bubble(value: str, label: str, colour: str) -> str:
    return (
        f'<div style="text-align:center;background:rgba(255,255,255,0.6);'
        f'border-radius:10px;padding:0.6rem 1rem;min-width:70px;">'
        f'<div style="font-family:DM Serif Display,serif;font-size:2rem;'
        f'line-height:1;color:{colour};">{value}</div>'
        f'<div style="font-size:0.65rem;text-transform:uppercase;'
        f'letter-spacing:0.05em;color:#7a90a8;margin-top:2px;">{label}</div>'
        f'</div>'
    )


# ── Helper: staff row with qual display ───────────────────────────────────────
def _render_staff_row(shift: dict, requires_diploma: bool):
    u     = shift.get("users") or {}
    sname = f"{u.get('first_name','')} {u.get('last_name','')}".strip()
    start = fmt_time_12h(shift.get("start_time"))
    end   = fmt_time_12h(shift.get("end_time"))
    brk   = shift.get("break_duration_minutes", 0)
    counts = counts_toward_ratio(shift)
    diploma = has_diploma(shift)

    # Build qualification chips
    qual_chips = ""
    active_quals = [
        q for q in extract_quals_for_shift(shift)
        if q.get("status") == "active"
    ]
    for q in active_quals[:3]:   # Show max 3
        short = q.get("short_name") or q.get("name", "")[:12]
        qual_chips += (
            f'<span style="background:#dbeafe;color:#1d4ed8;padding:1px 7px;'
            f'border-radius:99px;font-size:0.68rem;font-weight:600;'
            f'margin-right:3px;">{short}</span>'
        )

    # Diploma badge
    diploma_badge = ""
    if requires_diploma:
        if diploma:
            diploma_badge = (
                f'<span style="background:#dcfce7;color:#166534;padding:1px 7px;'
                f'border-radius:99px;font-size:0.68rem;font-weight:600;'
                f'margin-right:3px;">Diploma ✅</span>'
            )
        else:
            diploma_badge = (
                f'<span style="background:#fee2e2;color:#991b1b;padding:1px 7px;'
                f'border-radius:99px;font-size:0.68rem;font-weight:600;'
                f'margin-right:3px;">No Diploma ⚠️</span>'
            )

    # Counts-toward-ratio tag
    ratio_tag = "" if counts else (
        '<span style="background:#fef3c7;color:#92400e;padding:1px 7px;'
        'border-radius:99px;font-size:0.68rem;font-weight:600;">'
        "Excl. ratio</span>"
    )

    st.markdown(
        f'<div style="padding:0.6rem 0;border-bottom:1px solid #f0f4f8;">'
        f'<div style="display:flex;justify-content:space-between;align-items:flex-start;">'
        f'<div style="font-weight:600;font-size:0.92rem;color:#0d1f35;">'
        f'👩‍🏫 {sname}</div>'
        f'<div style="font-size:0.78rem;color:#7a90a8;white-space:nowrap;">'
        f'{start}–{end}'
        + (f' · {brk}m break' if brk else '')
        + f'</div></div>'
        f'<div style="margin-top:0.3rem;display:flex;flex-wrap:wrap;gap:2px;">'
        f'{diploma_badge}{qual_chips}{ratio_tag}'
        f'</div></div>',
        unsafe_allow_html=True,
    )


# ── Helper: rich hourly timeline ──────────────────────────────────────────────
def _render_rich_timeline(
    shifts: list, n_children: int, r_staff: int, r_children: int,
    capacity: int, colour: str, now_hour: int,
):
    hours     = list(range(6, 20))
    CELLS_ROW = 7

    for row_start in range(0, len(hours), CELLS_ROW):
        row_hours = hours[row_start:row_start + CELLS_ROW]
        cols      = st.columns(len(row_hours))

        for col, hour in zip(cols, row_hours):
            hstr    = f"{hour:02d}:00:00"
            n_staff = sum(
                1 for s in shifts
                if (s.get("start_time") or "") <= hstr
                <= (s.get("end_time") or "99:99:99")
                and counts_toward_ratio(s)
            )
            result  = compute_ratio(n_children, n_staff, r_staff, r_children, capacity)
            cfg     = result["config"]
            is_now  = hour == now_hour
            border  = f"2px solid {colour}" if is_now else f"1px solid {cfg['border']}"
            time_label = f"{hour:02d}:00"

            col.markdown(
                f'<div style="background:{cfg["bg"]};border:{border};'
                f'border-radius:8px;text-align:center;padding:0.45rem 0.1rem;">'
                f'<div style="font-size:0.72rem;font-weight:{"700" if is_now else "400"};'
                f'color:#0d1f35;">{time_label}</div>'
                f'<div style="font-size:1.1rem;margin:1px 0;">{cfg["icon"]}</div>'
                f'<div style="font-size:0.68rem;color:#475569;">'
                f'{n_staff}s / {n_children}c</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
