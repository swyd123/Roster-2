# pages/break_schedule.py  —  Break Schedule + Log Break
# Gantt-style visual timeline per staff member with break recommendations,
# ratio conflict detection, and configurable break rules.

import streamlit as st
from datetime import datetime, date, time as _time, timedelta

from utils.break_queries import (
    fetch_breaks_today, fetch_break_rules,
    create_break, update_break_schedule,
    mark_break_completed, mark_break_missed, delete_break,
)
from utils.break_preferences_queries import fetch_break_prefs_for_centre
from utils.break_engine import (
    BREAK_STATUS_CONFIG, BREAK_TYPE_LABELS, BREAK_RULES_DEFAULT,
    shift_duration_minutes, calc_break_entitlement,
    suggest_break_times, derive_break_status,
    build_gantt_bars, time_to_pct, fmt_duration, fmt_time,
    generate_break_recommendations, break_schedule_summary,
)
from utils.room_queries import (
    fetch_today_shifts, fetch_rooms, fetch_today_attendance,
)
from utils.ratio_engine import compute_ratio, now_time_str
from utils.staff_queries import fetch_centres, fetch_all_staff
from utils.helpers import toast_success, toast_error, toast_warn


DAY_START = 6
DAY_END   = 20


def render():
    # ── Header ────────────────────────────────────────────────────────
    h1, h2, h3 = st.columns([4, 1, 1])
    h1.title("Break Schedule")
    h1.markdown(
        f'<p class="page-sub">Today\'s break plan · '
        f'{date.today().strftime("%A %-d %B %Y")} · '
        f'Staff on break are excluded from ratio counts</p>',
        unsafe_allow_html=True,
    )
    with h2:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("📜  History", use_container_width=True):
            st.session_state.page = "break_history"
            st.rerun()
    with h3:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("➕  Schedule Break", type="primary", use_container_width=True):
            st.session_state["show_log_break"] = True
            st.session_state.pop("prefill_shift_id", None)
            st.rerun()

    # ── Centre selector ───────────────────────────────────────────────
    centres = fetch_centres()
    if not centres:
        st.warning("No centres found.")
        return

    centre_opts = {c["id"]: c["name"] for c in centres}
    saved = (
        st.session_state.get("break_centre_id")
        or st.session_state.get("selected_centre_id")
        or centres[0]["id"]
    )

    centre_id = st.selectbox(
        "Centre",
        options=list(centre_opts.keys()),
        format_func=lambda x: centre_opts[x],
        index=list(centre_opts.keys()).index(saved) if saved in centre_opts else 0,
        key="break_centre_sel",
    )
    st.session_state.break_centre_id = centre_id

    # ── Load data ─────────────────────────────────────────────────────
    with st.spinner("Loading today's schedule…"):
        try:
            all_breaks   = fetch_breaks_today(centre_id)
            all_shifts   = fetch_today_shifts(centre_id)
            rooms        = fetch_rooms(centre_id)
            attendance   = fetch_today_attendance(centre_id)
            db_rules     = fetch_break_rules(centre_id)
            break_prefs  = fetch_break_prefs_for_centre(centre_id)
        except Exception as e:
            toast_error(f"Could not load data: {e}")
            return

    # Use DB rules when available, fall back to defaults
    active_rules = db_rules if db_rules else BREAK_RULES_DEFAULT

    now = now_time_str()

    # ── Log Break form (inline at top) ────────────────────────────────
    if st.session_state.get("show_log_break"):
        st.markdown("---")
        _render_log_break_form(centre_id, all_shifts, rooms, attendance, now, active_rules)
        st.markdown("---")

    # ── Generate break recommendations (uses actual roster staffing) ──
    recommendations = generate_break_recommendations(
        shifts=all_shifts,
        existing_breaks=all_breaks,
        rooms=rooms,
        rules=active_rules,
        staff_prefs=break_prefs,
    )

    # ── Summary metrics ───────────────────────────────────────────────
    total_staff     = len(all_shifts)
    scheduled       = sum(1 for b in all_breaks if b.get("status") == "scheduled")
    in_progress     = sum(1 for b in all_breaks if derive_break_status(b, now) == "in_progress")
    completed_today = sum(1 for b in all_breaks if b.get("status") == "completed")
    missed          = sum(1 for b in all_breaks if derive_break_status(b, now) == "missed")

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Staff Today",        total_staff)
    m2.metric("Breaks Scheduled",   scheduled)
    m3.metric("Currently on Break", in_progress,
              delta="affecting ratio" if in_progress else None,
              delta_color="inverse" if in_progress else "off")
    m4.metric("Completed",          completed_today)
    m5.metric("Missed / Overdue",   missed,
              delta="action needed" if missed else None,
              delta_color="inverse" if missed else "off")

    if missed > 0:
        st.warning(
            f"⚠️ **{missed} break(s) overdue or missed.** "
            "Review the schedule below and mark breaks as taken or rescheduled."
        )
    if in_progress > 0:
        st.info(
            f"☕ **{in_progress} staff member(s) currently on break** — "
            "excluded from ratio calculations until they return."
        )

    st.markdown("---")

    # ── Break Schedule Summary (new) ──────────────────────────────────
    _render_break_summary(recommendations, active_rules)

    st.markdown("---")

    # ── Ratio impact warnings ─────────────────────────────────────────
    ratio_warnings = _check_ratio_impact(all_breaks, all_shifts, rooms, attendance, now)
    if ratio_warnings:
        with st.expander(f"⚠️ {len(ratio_warnings)} ratio impact warning(s)", expanded=True):
            for w in ratio_warnings:
                st.warning(w)

    # ── Break Recommendations table ───────────────────────────────────
    if recommendations:
        st.markdown("### 📋 Break Recommendations")
        st.caption(
            "Auto-calculated from shift length using the configured break rules. "
            "Conflict status is based on actual rostered staff per 15-minute slot."
        )
        _render_recommendations_table(recommendations)
        st.markdown("---")

    # ── Visual Gantt timeline ─────────────────────────────────────────
    st.markdown("### 📊 Today's Timeline")
    st.caption(
        "Each row shows one staff member's shift (blue) with breaks overlaid. "
        "White = scheduled · green = completed · red = missed · yellow = in progress."
    )

    breaks_by_user: dict[str, list] = {}
    for b in all_breaks:
        uid = b.get("user_id", "")
        breaks_by_user.setdefault(uid, []).append(b)

    if not all_shifts:
        st.info("No shifts rostered today.")
    else:
        _render_timeline_header()
        for shift in sorted(all_shifts, key=lambda s: (
            (s.get("rooms") or {}).get("name", ""),
            (s.get("users") or {}).get("last_name", ""),
        )):
            uid      = shift.get("user_id", "")
            u_breaks = breaks_by_user.get(uid, [])
            _render_gantt_row(shift, u_breaks, centre_id, now, active_rules)

    # ── Break status table ────────────────────────────────────────────
    st.markdown("---")
    st.markdown("### 📋 Break Status Table")
    _render_break_table(all_breaks, all_shifts, now, centre_id, active_rules)


# ─────────────────────────────────────────────────────────────────────────────
# BREAK SCHEDULE SUMMARY
# ─────────────────────────────────────────────────────────────────────────────

def _render_break_summary(recommendations: list[dict], rules: list[dict]):
    """Top-level break summary: total paid, total unpaid, unresolved conflicts."""
    st.markdown("### 📊 Break Schedule Summary")

    summary = break_schedule_summary(recommendations, [])

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total paid breaks",   summary["total_paid_breaks"])
    m2.metric("Total unpaid breaks", summary["total_unpaid_breaks"])
    m3.metric("Breaks OK",           summary["scheduled_ok"])
    m4.metric(
        "Unresolved conflicts",
        summary["unresolved_conflicts"],
        delta="manual review required" if summary["unresolved_conflicts"] else None,
        delta_color="inverse" if summary["unresolved_conflicts"] else "off",
    )

    if summary["unresolved_conflicts"] > 0:
        st.warning(
            f"⚠️ **{summary['unresolved_conflicts']} break(s) have ratio conflicts or require review.** "
            "See the recommendations table below."
        )
    elif summary["scheduled_ok"] > 0:
        st.success(
            f"✅ All {summary['scheduled_ok']} break(s) scheduled without ratio conflicts."
        )

    # Show active rules source
    rule_source = "configured in database" if rules != BREAK_RULES_DEFAULT else "default rules"
    st.caption(
        f"Break entitlements calculated using {rule_source}. "
        "Rules: <4h = none · 4–5h = 10m paid · 5–7h = 10m paid + 30m unpaid · "
        "7+h = 20m paid + 30m unpaid."
    )


# ─────────────────────────────────────────────────────────────────────────────
# RECOMMENDATIONS TABLE
# ─────────────────────────────────────────────────────────────────────────────

def _render_recommendations_table(recommendations: list[dict]):
    """Render a compact table of break recommendations with status for each shift."""
    status_icon = {
        "scheduled":     "✅",
        "ratio_conflict": "❌",
        "manual_review":  "🔍",
        "no_entitlement": "—",
    }

    for rec in sorted(recommendations, key=lambda r: (r["room_name"], r["user_name"])):
        status  = rec["schedule_status"]
        sname   = rec["user_name"]
        rname   = rec["room_name"]
        ss      = rec["shift_start"]
        se      = rec["shift_end"]
        dur     = rec["shift_minutes"]
        ent     = rec["entitlement"]
        sugs    = rec["suggestions"]
        reason  = rec["status_reason"]
        icon    = status_icon.get(status, "—")

        cfg = {
            "scheduled":      ("#f0fdf4", "#14532d", "#bbf7d0"),
            "ratio_conflict": ("#fff1f2", "#991b1b", "#fca5a5"),
            "manual_review":  ("#fffbeb", "#92400e", "#fcd34d"),
            "no_entitlement": ("#f8fafc", "#64748b", "#e2e8f0"),
        }.get(status, ("#f8fafc", "#64748b", "#e2e8f0"))

        st.markdown(
            f'<div style="background:{cfg[0]};border:1px solid {cfg[2]};'
            f'border-radius:8px;padding:0.55rem 0.9rem;margin-bottom:0.4rem;">'
            f'<div style="display:flex;justify-content:space-between;align-items:baseline;">'
            f'<span style="font-weight:600;color:{cfg[1]};font-size:0.9rem;">'
            f'{icon} {sname}'
            f'<span style="font-weight:400;color:#7a90a8;font-size:0.8rem;margin-left:0.5rem;">'
            f'{rname} · {ss}–{se} ({dur}min)</span></span>'
            f'<span style="font-size:0.8rem;color:{cfg[1]};">{ent["summary"]}</span>'
            f'</div>'
            + (
                f'<div style="font-size:0.75rem;color:#166534;margin-top:3px;">'
                f'✅ Unpaid break opted out ({rec.get("opt_out_source","")}) · '
                f'Paid rest break still required</div>'
                if rec.get("unpaid_opted_out") else ""
            )
            + (
                f'<div style="font-size:0.78rem;color:{cfg[1]};margin-top:3px;">'
                f'{reason}</div>'
                if status in ("ratio_conflict", "manual_review") else ""
            )
            + (
                "".join(
                    f'<span style="font-size:0.75rem;color:#475569;margin-right:1rem;">'
                    f'{"☕" if s["break_type"] == "meal" else "☕"} '
                    f'{"Meal" if s["break_type"] == "meal" else "Rest"} '
                    f'{s["planned_start"][:5]}–{s["planned_end"][:5]} '
                    f'({s["duration_minutes"]}min)</span>'
                    for s in sugs
                )
                if sugs else ""
            )
            + f'</div>',
            unsafe_allow_html=True,
        )


# ─────────────────────────────────────────────────────────────────────────────
# TIMELINE HEADER
# ─────────────────────────────────────────────────────────────────────────────

def _render_timeline_header():
    hours = list(range(DAY_START, DAY_END + 1))
    name_w = 18
    tl_w   = 82

    hour_labels_html = ""
    for h in hours:
        pct   = (h - DAY_START) / (DAY_END - DAY_START) * 100
        label = f"{h}:00" if h < 12 else (f"{h-12 if h > 12 else 12}pm")
        hour_labels_html += (
            f'<span style="position:absolute;left:{pct:.1f}%;'
            f'font-size:0.65rem;color:#94a3b8;transform:translateX(-50%);">'
            f'{label}</span>'
        )

    now_h, now_m = datetime.now().hour, datetime.now().minute
    now_pct = ((now_h + now_m / 60 - DAY_START) / (DAY_END - DAY_START)) * 100

    st.markdown(
        f'<div style="display:flex;align-items:center;margin-bottom:0.2rem;">'
        f'<div style="width:{name_w}%;flex-shrink:0;"></div>'
        f'<div style="width:{tl_w}%;position:relative;height:18px;">'
        f'{hour_labels_html}'
        + (
            f'<div style="position:absolute;top:0;left:{now_pct:.1f}%;'
            f'width:2px;height:100%;background:#ef4444;opacity:0.7;"></div>'
            if 0 <= now_pct <= 100 else ""
        )
        + f'</div></div>',
        unsafe_allow_html=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# GANTT ROW per staff member
# ─────────────────────────────────────────────────────────────────────────────

def _render_gantt_row(shift: dict, breaks: list, centre_id: str, now: str, rules: list):
    u        = shift.get("users") or {}
    room     = (shift.get("rooms") or {})
    uid      = shift.get("user_id", "")
    shift_id = shift.get("id", "")
    sname    = f"{u.get('first_name','')} {u.get('last_name','')}".strip() or "Unknown"
    sstart   = (shift.get("start_time") or "")[:5]
    send     = (shift.get("end_time")   or "")[:5]
    rcolour  = room.get("colour", "#3498DB")
    shift_mins  = shift_duration_minutes(sstart, send)
    entitlement = calc_break_entitlement(shift_mins, rules)

    segments = build_gantt_bars(sstart + ":00", send + ":00", breaks, DAY_START, DAY_END)

    now_h, now_m = datetime.now().hour, datetime.now().minute
    now_pct = ((now_h + now_m / 60 - DAY_START) / (DAY_END - DAY_START)) * 100

    name_w = 18
    tl_w   = 82

    seg_html = ""
    for seg in segments:
        seg_html += (
            f'<div style="position:absolute;top:20%;height:60%;'
            f'left:{seg["left_pct"]:.2f}%;width:{max(seg["width_pct"],0.3):.2f}%;'
            f'background:{seg["colour"]};border:1px solid {seg["border"]};'
            f'border-radius:3px;opacity:{seg["opacity"]};'
            f'box-sizing:border-box;" title="{seg["label"]}"></div>'
        )

    room_dot = (
        f'<span style="display:inline-block;width:7px;height:7px;border-radius:50%;'
        f'background:{rcolour};margin-left:4px;vertical-align:middle;"></span>'
    )

    n_breaks = len(breaks)
    break_badge = (
        f'<span style="background:#e0e7ff;color:#4338ca;font-size:0.65rem;'
        f'padding:0 5px;border-radius:99px;margin-left:5px;">{n_breaks}b</span>'
        if n_breaks else (
            f'<span style="background:#fef3c7;color:#92400e;font-size:0.65rem;'
            f'padding:0 5px;border-radius:99px;margin-left:5px;">no break</span>'
            if entitlement["total_min"] > 0 else ""
        )
    )
    opt_out_badge = (
        f'<span style="background:#f0fdf4;color:#166534;font-size:0.62rem;'
        f'padding:0 5px;border-radius:99px;margin-left:4px;border:1px solid #86efac;">'
        f'unpaid opted out · paid rest ✓</span>'
        if entitlement.get("unpaid_opted_out") else ""
    )

    row_html = (
        f'<div style="display:flex;align-items:center;margin-bottom:4px;">'
        f'<div style="width:{name_w}%;flex-shrink:0;padding-right:8px;'
        f'font-size:0.82rem;color:#1e3a55;overflow:hidden;text-overflow:ellipsis;'
        f'white-space:nowrap;">'
        f'{sname}{room_dot}{break_badge}{opt_out_badge}'
        f'</div>'
        f'<div style="width:{tl_w}%;position:relative;height:28px;'
        f'background:#f1f5f9;border-radius:4px;overflow:hidden;">'
        + "".join(
            f'<div style="position:absolute;top:0;left:{((h - DAY_START) / (DAY_END - DAY_START) * 100):.1f}%;'
            f'width:1px;height:100%;background:#e2e8f0;"></div>'
            for h in range(DAY_START, DAY_END + 1)
        )
        + seg_html
        + (
            f'<div style="position:absolute;top:0;left:{now_pct:.1f}%;'
            f'width:2px;height:100%;background:#ef4444;opacity:0.6;z-index:10;"></div>'
            if 0 <= now_pct <= 100 else ""
        )
        + f'</div></div>'
    )

    col_row, col_btn = st.columns([7, 1])
    with col_row:
        st.markdown(row_html, unsafe_allow_html=True)
    with col_btn:
        if st.button("＋", key=f"add_break_{shift_id}",
                     help=f"Schedule a break for {sname}", use_container_width=True):
            st.session_state["show_log_break"]   = True
            st.session_state["prefill_shift_id"] = shift_id
            st.session_state["prefill_user_id"]  = uid
            st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# BREAK STATUS TABLE
# ─────────────────────────────────────────────────────────────────────────────

def _render_break_table(breaks: list, shifts: list, now: str, centre_id: str, rules: list):
    shift_map: dict[str, dict] = {s["id"]: s for s in shifts}

    if not breaks:
        st.info(
            "No breaks scheduled today. "
            "Click **➕ Schedule Break** or the **＋** next to a staff member."
        )
        unscheduled = [
            s for s in shifts
            if s.get("user_id") not in {b.get("user_id") for b in breaks}
        ]
        if unscheduled:
            st.markdown(f"**{len(unscheduled)} staff without any break scheduled:**")
            for s in unscheduled:
                u     = s.get("users") or {}
                sname = f"{u.get('first_name','')} {u.get('last_name','')}".strip()
                ss    = (s.get("start_time") or "")[:5]
                se    = (s.get("end_time")   or "")[:5]
                dur   = shift_duration_minutes(ss, se)
                ent   = calc_break_entitlement(dur, rules)
                if ent["total_min"] > 0:
                    st.markdown(
                        f'<div style="display:flex;justify-content:space-between;'
                        f'padding:0.35rem 0;border-bottom:1px solid #f0f4f8;">'
                        f'<span style="font-size:0.88rem;color:#0d1f35;">👩‍🏫 {sname}</span>'
                        f'<span style="font-size:0.78rem;color:#92400e;">'
                        f'⚠️ Entitled to {ent["summary"]}</span>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
        return

    for b in sorted(breaks, key=lambda x: (x.get("planned_start_time") or "")):
        _render_break_row(b, shift_map, now, centre_id)


def _render_break_row(b: dict, shift_map: dict, now: str, centre_id: str):
    bid        = b.get("id", "")
    u          = b.get("users") or {}
    sname      = f"{u.get('first_name','')} {u.get('last_name','')}".strip() or "Unknown"
    btype      = b.get("break_type", "meal")
    btype_lbl  = BREAK_TYPE_LABELS.get(btype, btype.title())
    pstart     = fmt_time(b.get("planned_start_time"))
    pend       = fmt_time(b.get("planned_end_time"))
    pdur       = b.get("planned_duration_minutes", 0)
    astart     = fmt_time(b.get("actual_start_time"))
    aend       = fmt_time(b.get("actual_end_time"))
    adur       = b.get("actual_duration_minutes")
    notes      = b.get("notes") or ""
    live_status = derive_break_status(b, now)
    cfg        = BREAK_STATUS_CONFIG.get(live_status, BREAK_STATUS_CONFIG["scheduled"])

    room_name   = ""
    room_colour = "#3498DB"
    rs_data = b.get("roster_shifts")
    if rs_data and isinstance(rs_data, dict):
        room_rec    = rs_data.get("rooms") or {}
        room_name   = room_rec.get("name", "")
        room_colour = room_rec.get("colour", "#3498DB")

    with st.expander(
        f"**{sname}** · {btype_lbl} · {pstart}–{pend}",
        expanded=(live_status in ("in_progress", "missed", "ratio_conflict")),
    ):
        tc1, tc2, tc3, tc4 = st.columns(4)
        tc1.markdown(f"**Staff**  \n{sname}")
        tc2.markdown(
            f"**Break type**  \n"
            f'<span style="background:#e0e7ff;color:#4338ca;padding:2px 8px;'
            f'border-radius:99px;font-size:0.78rem;font-weight:600;">{btype_lbl}</span>',
            unsafe_allow_html=True,
        )
        tc3.markdown(f"**Planned**  \n{pstart} – {pend} ({fmt_duration(pdur)})")
        tc4.markdown(
            f"**Status**  \n"
            f'<span style="background:{cfg["bg"]};color:{cfg["text"]};'
            f'padding:2px 9px;border-radius:99px;font-size:0.78rem;font-weight:600;">'
            f'{cfg["icon"]} {cfg["label"]}</span>',
            unsafe_allow_html=True,
        )

        if b.get("actual_start_time"):
            ac1, ac2, ac3, _ = st.columns(4)
            ac1.markdown(f"**Actual start**  \n{astart}")
            ac2.markdown(f"**Actual end**  \n{aend}")
            ac3.markdown(f"**Actual duration**  \n{fmt_duration(adur)}")

        if room_name:
            st.caption(f"Room: {room_name}")
        if notes:
            st.markdown(f"_Notes: {notes}_")

        # Status-specific callouts
        if live_status == "ratio_conflict":
            st.error(
                "❌ **Ratio conflict** — this break would reduce staffing below "
                "the required ratio. Reschedule or arrange cover before taking this break."
            )
        elif live_status == "manual_review":
            st.warning(
                "🔍 **Manual review required** — this break creates a staffing warning. "
                "Confirm cover is arranged before proceeding."
            )

        st.markdown("")
        ab1, ab2, ab3, ab4, _ = st.columns([1.2, 1.2, 1.4, 1.2, 3])

        if live_status in ("scheduled", "in_progress", "not_yet_due",
                           "ratio_conflict", "manual_review"):
            if ab1.button("✅ Mark Taken", key=f"take_{bid}", use_container_width=True,
                           type="primary"):
                st.session_state[f"mark_taken_{bid}"] = True
                st.rerun()

        if live_status not in ("completed",):
            if ab2.button("✏️ Edit", key=f"edit_b_{bid}", use_container_width=True):
                st.session_state[f"edit_break_{bid}"] = not st.session_state.get(
                    f"edit_break_{bid}", False)
                st.rerun()

        if live_status in ("scheduled", "in_progress", "manual_review") and ab3.button(
            "⚠️ Mark Missed", key=f"miss_{bid}", use_container_width=True
        ):
            try:
                mark_break_missed(bid)
                toast_warn("Break marked as missed.")
                st.rerun()
            except Exception as e:
                toast_error(str(e))

        del_key = f"del_break_{bid}"
        if live_status not in ("completed",):
            if st.session_state.get(del_key):
                st.warning("Remove this break record?")
                dy, dn = st.columns(2)
                if dy.button("Remove", key=f"dby_{bid}", type="primary", use_container_width=True):
                    try:
                        delete_break(bid)
                        toast_success("Break removed.")
                        st.session_state.pop(del_key, None)
                        st.rerun()
                    except Exception as e:
                        toast_error(str(e))
                if dn.button("Cancel", key=f"dbn_{bid}", use_container_width=True):
                    st.session_state.pop(del_key, None)
                    st.rerun()
            else:
                if ab4.button("🗑️ Remove", key=f"del_{bid}", use_container_width=True):
                    st.session_state[del_key] = True
                    st.rerun()

        if st.session_state.get(f"mark_taken_{bid}"):
            st.markdown("---")
            _render_mark_taken_form(bid, b)

        if st.session_state.get(f"edit_break_{bid}"):
            st.markdown("---")
            _render_edit_break_form(bid, b)


def _render_mark_taken_form(bid: str, b: dict):
    pstart_raw = (b.get("planned_start_time") or "08:00")[:5]
    pend_raw   = (b.get("planned_end_time")   or "08:30")[:5]
    try:
        ps_t = datetime.strptime(pstart_raw, "%H:%M").time()
        pe_t = datetime.strptime(pend_raw,   "%H:%M").time()
    except Exception:
        ps_t = _time(8, 0)
        pe_t = _time(8, 30)

    st.markdown("**Record break times**")
    with st.form(f"taken_form_{bid}"):
        tc1, tc2 = st.columns(2)
        actual_start = tc1.time_input("Actual start time", value=ps_t, key=f"tas_{bid}")
        actual_end   = tc2.time_input("Actual end time",   value=pe_t, key=f"tae_{bid}")
        taken_notes  = st.text_input("Notes", placeholder="e.g. Taken late due to incident",
                                     key=f"tn_{bid}")
        sc1, sc2 = st.columns(2)
        if sc1.form_submit_button("💾 Save", type="primary", use_container_width=True):
            dur = max(0, int(
                (datetime.combine(date.today(), actual_end)
                 - datetime.combine(date.today(), actual_start)).total_seconds() / 60
            ))
            try:
                mark_break_completed(
                    bid,
                    actual_start.strftime("%H:%M:%S"),
                    actual_end.strftime("%H:%M:%S"),
                    dur, taken_notes,
                )
                toast_success("Break recorded as completed.")
                st.session_state.pop(f"mark_taken_{bid}", None)
                st.rerun()
            except Exception as e:
                toast_error(str(e))
        if sc2.form_submit_button("Cancel", use_container_width=True):
            st.session_state.pop(f"mark_taken_{bid}", None)
            st.rerun()


def _render_edit_break_form(bid: str, b: dict):
    pstart_raw   = (b.get("planned_start_time") or "12:00")[:5]
    pend_raw     = (b.get("planned_end_time")   or "12:30")[:5]
    try:
        ps_t = datetime.strptime(pstart_raw, "%H:%M").time()
        pe_t = datetime.strptime(pend_raw,   "%H:%M").time()
    except Exception:
        ps_t = _time(12, 0)
        pe_t = _time(12, 30)

    current_type = b.get("break_type", "meal")
    type_opts    = list(BREAK_TYPE_LABELS.keys())
    type_idx     = type_opts.index(current_type) if current_type in type_opts else 0

    st.markdown("**Edit break schedule**")
    with st.form(f"edit_form_{bid}"):
        ec1, ec2, ec3 = st.columns(3)
        new_type  = ec1.selectbox("Break type", options=type_opts, index=type_idx,
                                   format_func=lambda x: BREAK_TYPE_LABELS[x],
                                   key=f"ebt_{bid}")
        new_start = ec2.time_input("Planned start", value=ps_t, key=f"eps_{bid}")
        new_end   = ec3.time_input("Planned end",   value=pe_t, key=f"epe_{bid}")
        new_notes = st.text_input("Notes", value=b.get("notes","") or "", key=f"en_{bid}")
        sc1, sc2  = st.columns(2)
        if sc1.form_submit_button("💾 Save Changes", type="primary", use_container_width=True):
            dur = max(0, int(
                (datetime.combine(date.today(), new_end)
                 - datetime.combine(date.today(), new_start)).total_seconds() / 60
            ))
            try:
                update_break_schedule(bid, new_start.strftime("%H:%M:%S"),
                                      new_end.strftime("%H:%M:%S"), dur, new_type, new_notes)
                toast_success("Break updated.")
                st.session_state.pop(f"edit_break_{bid}", None)
                st.rerun()
            except Exception as e:
                toast_error(str(e))
        if sc2.form_submit_button("Cancel", use_container_width=True):
            st.session_state.pop(f"edit_break_{bid}", None)
            st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# LOG BREAK FORM
# ─────────────────────────────────────────────────────────────────────────────

def _render_log_break_form(centre_id, all_shifts, rooms, attendance, now, rules):
    st.markdown("### Schedule a Break")

    staff_opts: dict[str, str] = {}
    shift_by_uid: dict[str, dict] = {}
    for s in all_shifts:
        u   = s.get("users") or {}
        uid = s.get("user_id", "")
        nm  = f"{u.get('first_name','')} {u.get('last_name','')}".strip()
        if uid and nm:
            staff_opts[uid]   = nm
            shift_by_uid[uid] = s

    if not staff_opts:
        st.warning("No staff rostered today.")
        if st.button("Close", key="close_no_staff"):
            st.session_state.pop("show_log_break", None)
            st.rerun()
        return

    with st.form("log_break_form", clear_on_submit=False):
        fc1, fc2 = st.columns(2)

        # Pre-fill from session state (clicking ＋ on a Gantt row)
        prefill_uid = st.session_state.get("prefill_user_id", "")
        uid_keys    = list(staff_opts.keys())
        default_idx = uid_keys.index(prefill_uid) if prefill_uid in uid_keys else 0

        selected_uid = fc1.selectbox(
            "Staff member *",
            options=uid_keys,
            format_func=lambda x: staff_opts[x],
            index=default_idx,
            key="lb_staff",
        )

        break_type = fc2.selectbox(
            "Break type *",
            options=list(BREAK_TYPE_LABELS.keys()),
            format_func=lambda x: BREAK_TYPE_LABELS[x],
            key="lb_type",
        )

        # Show entitlement for selected staff member
        selected_shift = shift_by_uid.get(selected_uid, {})
        ss = (selected_shift.get("start_time") or "")[:5]
        se = (selected_shift.get("end_time")   or "")[:5]
        sug_t = _time(12, 0)
        sug_e = _time(12, 30)

        if ss and se:
            dur_mins = shift_duration_minutes(ss, se)
            ent      = calc_break_entitlement(dur_mins, rules)
            suggestions = suggest_break_times(ss + ":00", se + ":00", ent)
            st.markdown(
                f'<p style="font-size:0.82rem;color:#1a6b4a;">'
                f'📋 Shift {ss}–{se} ({dur_mins} min) · '
                f'Entitlement: <strong>{ent["summary"]}</strong>'
                f'</p>',
                unsafe_allow_html=True,
            )
            if suggestions:
                best  = suggestions[0] if break_type == "rest" else (
                    next((s for s in suggestions if s["break_type"] == "meal"),
                         suggestions[-1])
                )
                sug_t = datetime.strptime(best["planned_start"][:5], "%H:%M").time()
                sug_e = datetime.strptime(best["planned_end"][:5],   "%H:%M").time()

        tc1, tc2 = st.columns(2)
        planned_start = tc1.time_input("Planned start *", value=sug_t, key="lb_ps")
        planned_end   = tc2.time_input("Planned end *",   value=sug_e, key="lb_pe")

        st.toggle("Record actual times now (break already taken)",
                   value=False, key="lb_has_actual")
        if st.session_state.get("lb_has_actual"):
            ac1, ac2 = st.columns(2)
            actual_start = ac1.time_input("Actual start", value=sug_t, key="lb_as")
            actual_end   = ac2.time_input("Actual end",   value=sug_e, key="lb_ae")
        else:
            actual_start = actual_end = None

        lb_notes = st.text_input("Notes", placeholder="e.g. Cover arranged with Preschool",
                                  key="lb_notes")

        # Ratio impact preview
        room_rec = (selected_shift.get("rooms") or {})
        room_id  = room_rec.get("id")
        if room_id:
            room_cfg = next((r for r in rooms if r["id"] == room_id), None)
            if room_cfg:
                n_children = sum(
                    1 for a in attendance
                    if a.get("room_id") == room_id and a.get("status") == "present"
                )
                total_staff_in_room = sum(
                    1 for s in shift_by_uid.values()
                    if s.get("room_id") == room_id
                    and (s.get("start_time","") or "") <= now <= (s.get("end_time","99:99") or "99:99")
                )
                staff_during_break = max(0, total_staff_in_room - 1)
                impact = compute_ratio(
                    n_children, staff_during_break,
                    room_cfg.get("required_ratio_staff", 1),
                    room_cfg.get("required_ratio_children", 4),
                    room_cfg.get("licensed_capacity", 0),
                )
                cfg = impact["config"]
                st.markdown(
                    f'<div style="background:{cfg["bg"]};border:1px solid {cfg["border"]};'
                    f'border-radius:8px;padding:0.6rem 0.9rem;margin:0.5rem 0;">'
                    f'<strong>Ratio Impact Preview</strong> — {room_rec.get("name","")} during break:<br>'
                    f'{cfg["icon"]} With 1 fewer staff: '
                    f'<strong>{staff_during_break} staff / {n_children} children</strong> '
                    f'→ <strong>{cfg["label"]}</strong>'
                    + (" Consider rescheduling." if impact["status"] in ("breach","warning") else " Safe to proceed.")
                    + f'</div>',
                    unsafe_allow_html=True,
                )

        sc1, sc2 = st.columns(2)
        submitted = sc1.form_submit_button("💾 Save Break", type="primary", use_container_width=True)
        cancelled = sc2.form_submit_button("Cancel", use_container_width=True)

    if cancelled:
        st.session_state.pop("show_log_break",   None)
        st.session_state.pop("prefill_shift_id", None)
        st.session_state.pop("prefill_user_id",  None)
        st.rerun()

    if submitted:
        dur = int(
            (datetime.combine(date.today(), planned_end)
             - datetime.combine(date.today(), planned_start)).total_seconds() / 60
        )
        if dur <= 0:
            toast_error("End time must be after start time.")
            return

        selected_shift = shift_by_uid.get(selected_uid, {})
        try:
            create_break(
                centre_id=centre_id,
                user_id=selected_uid,
                break_date=date.today().isoformat(),
                break_type=break_type,
                planned_start_time=planned_start.strftime("%H:%M:%S"),
                planned_end_time=planned_end.strftime("%H:%M:%S"),
                planned_duration_minutes=dur,
                roster_shift_id=selected_shift.get("id"),
                notes=lb_notes,
            )
            toast_success(
                f"Break scheduled for {staff_opts[selected_uid]} "
                f"at {planned_start.strftime('%H:%M')}."
            )
            st.session_state.pop("show_log_break",   None)
            st.session_state.pop("prefill_shift_id", None)
            st.session_state.pop("prefill_user_id",  None)
            st.rerun()
        except Exception as e:
            toast_error(f"Could not save break: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# RATIO IMPACT CHECKER
# ─────────────────────────────────────────────────────────────────────────────

def _check_ratio_impact(breaks, shifts, rooms, attendance, now):
    warnings = []
    now_h    = datetime.now().hour
    check_until = f"{min(now_h + 2, 23):02d}:59:59"
    room_map = {r["id"]: r for r in rooms}

    for b in breaks:
        ps = (b.get("planned_start_time") or "")[:8]
        if not ps or ps > check_until:
            continue
        if b.get("status") == "completed":
            continue

        u    = b.get("users") or {}
        uid  = b.get("user_id", "")
        sname = f"{u.get('first_name','')} {u.get('last_name','')}".strip()

        shift = next((s for s in shifts if s.get("user_id") == uid), None)
        if not shift:
            continue

        room_id  = shift.get("room_id")
        room_cfg = room_map.get(room_id)
        if not room_cfg:
            continue

        n_children = sum(
            1 for a in attendance
            if a.get("room_id") == room_id and a.get("status") == "present"
        )
        total_in_room = sum(
            1 for s in shifts
            if s.get("room_id") == room_id
            and (s.get("start_time","") or "") <= now
            <= (s.get("end_time","99:99") or "99:99")
        )
        staff_during_break = max(0, total_in_room - 1)
        impact = compute_ratio(
            n_children, staff_during_break,
            room_cfg.get("required_ratio_staff", 1),
            room_cfg.get("required_ratio_children", 4),
            room_cfg.get("licensed_capacity", 0),
        )
        if impact["status"] in ("breach", "warning"):
            rname  = room_cfg.get("name", "")
            ps_fmt = fmt_time(ps[:8])
            warnings.append(
                f"⚠️ **{sname}'s break at {ps_fmt}** reduces **{rname}** to "
                f"{staff_during_break} staff for {n_children} children → "
                f"{impact['config']['label']}. Consider rescheduling."
            )
    return warnings
