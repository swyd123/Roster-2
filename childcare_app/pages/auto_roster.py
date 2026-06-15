# pages/auto_roster.py — Auto Roster & Break Scheduling
#
# SUGGESTION ENGINE — generates draft shifts and breaks based on:
#   • actual attendance (room_attendance_intervals.actual_children)
#   • staff availability and leave
#   • ratio requirements per room
#   • recurring break opt-out preferences
#
# Nothing is saved until the user explicitly clicks Save.
# Published rosters are protected — only draft periods can be regenerated.

import streamlit as st
from datetime import date, timedelta
import pandas as pd

from utils.auto_roster_engine import (
    generate_roster, SuggestedShift, SuggestedBreak,
    FT_MIN_DAYS, FT_MIN_HOURS, FT_OVERTIME_THRESHOLD_HOURS,
    FT_TARGET_WEEKLY_HOURS, FT_PREFERRED_DAILY_HOURS,
)
from utils.roster_queries import (
    fetch_roster_periods, create_roster_period,
    fetch_approved_leave_for_period, fetch_availability_map,
    create_shifts_batch, delete_all_draft_shifts,
)
from utils.break_queries import fetch_break_rules, create_breaks_batch
from utils.break_preferences_queries import fetch_break_prefs_for_centre
from utils.attendance_queries import fetch_intervals_for_centre
from utils.room_queries import fetch_rooms
from utils.staff_queries import fetch_all_staff, fetch_centres
from utils.helpers import toast_success, toast_error


def render():
    # ── Header ────────────────────────────────────────────────────────
    st.title("Auto Roster & Breaks")
    st.markdown(
        '<p class="page-sub">Suggestion engine — generates draft shifts and breaks '
        "from attendance data, availability and ratio rules. "
        "Review and edit before saving.</p>",
        unsafe_allow_html=True,
    )

    # ── Centre selector ───────────────────────────────────────────────
    centres = fetch_centres()
    if not centres:
        st.warning("No centres found.")
        return

    centre_opts = {c["id"]: c["name"] for c in centres}
    saved_c = (
        st.session_state.get("auto_roster_centre")
        or st.session_state.get("selected_centre_id")
        or centres[0]["id"]
    )

    sc1, sc2, sc3 = st.columns([2, 1, 1])
    centre_id = sc1.selectbox(
        "Centre", options=list(centre_opts.keys()),
        format_func=lambda x: centre_opts[x],
        index=list(centre_opts.keys()).index(saved_c) if saved_c in centre_opts else 0,
        key="ar_centre",
    )
    st.session_state.auto_roster_centre = centre_id

    # ── Date range ────────────────────────────────────────────────────
    today    = date.today()
    next_mon = today + timedelta(days=(7 - today.weekday()))
    start_d  = sc2.date_input("Week start", value=next_mon,
                               format="DD/MM/YYYY", key="ar_start")
    end_d    = sc3.date_input("Week end",   value=next_mon + timedelta(days=6),
                               format="DD/MM/YYYY", key="ar_end")

    if start_d > end_d:
        st.error("Start date must be before end date.")
        return

    # Invalidate cached result when centre or date range changes
    cached = st.session_state.get("ar_result")
    if cached:
        if (
            st.session_state.get("ar_centre_id")       != centre_id
            or st.session_state.get("ar_result_start") != start_d.isoformat()
            or st.session_state.get("ar_result_end")   != end_d.isoformat()
        ):
            st.session_state.pop("ar_result",    None)
            st.session_state.pop("ar_period_id", None)

    days = []
    d    = start_d
    while d <= end_d:
        days.append(d)
        d += timedelta(days=1)

    st.markdown("---")

    # ── Load all required data ────────────────────────────────────────
    with st.spinner("Loading attendance, staff and availability…"):
        try:
            rooms       = fetch_rooms(centre_id)
            staff       = fetch_all_staff()
            leave_map   = fetch_approved_leave_for_period(
                centre_id, start_d.isoformat(), end_d.isoformat()
            )
            avail_map   = fetch_availability_map(centre_id)
            break_prefs = fetch_break_prefs_for_centre(centre_id)
            db_rules    = fetch_break_rules(centre_id)
        except Exception as e:
            toast_error(f"Could not load data: {e}")
            return

    if not rooms:
        st.info("No rooms configured for this centre.")
        return

    all_intervals: dict[str, list] = {}
    with st.spinner("Loading attendance intervals…"):
        for day in days:
            try:
                ivs = fetch_intervals_for_centre(centre_id, day.isoformat())
                if ivs:
                    all_intervals[day.isoformat()] = ivs
            except Exception:
                pass

    has_attendance = bool(all_intervals)
    if not has_attendance:
        st.warning(
            "⚠️ No attendance data found for this period. "
            "Upload attendance CSV on the **👶 Child Attendance** page first."
        )

    # ── Generate button ───────────────────────────────────────────────
    gen_col, _ = st.columns([2, 5])
    generate   = gen_col.button(
        "⚙️  Generate Roster & Breaks",
        type="primary",
        use_container_width=True,
        disabled=not has_attendance,
    )

    if generate or st.session_state.get("ar_result"):
        if generate:
            # Always discard any previously cached result before re-running
            st.session_state.pop("ar_result",    None)
            st.session_state.pop("ar_period_id", None)

            with st.spinner("Running auto-roster engine…"):
                result = generate_roster(
                    days=days,
                    rooms=rooms,
                    all_intervals=all_intervals,
                    staff=staff,
                    availability_map=avail_map,
                    leave_map=leave_map,
                    break_prefs=break_prefs,
                    break_rules=db_rules or None,
                    centre_id=centre_id,
                )
            st.session_state["ar_result"]       = result
            st.session_state["ar_centre_id"]    = centre_id
            st.session_state["ar_result_start"] = start_d.isoformat()
            st.session_state["ar_result_end"]   = end_d.isoformat()
            st.session_state["ar_intervals"]    = all_intervals
        else:
            result = st.session_state["ar_result"]

        _render_result(result, centre_id, start_d, end_d, rooms, db_rules)


# ─────────────────────────────────────────────────────────────────────────────
# RESULT RENDERING
# ─────────────────────────────────────────────────────────────────────────────

def _render_result(result, centre_id, start_d, end_d, rooms, db_rules):
    from utils.break_engine import BREAK_RULES_DEFAULT
    from utils.roster_timeline import (
        build_timeline_html, build_movement_notes_html,
        get_day_summary, build_weekly_summary_html,
    )

    room_map   = {r["id"]: r["name"] for r in rooms}
    movements  = getattr(result, "movements", [])
    shifts     = result.shifts
    breaks     = result.breaks

    n_shifts      = len(shifts)
    n_breaks      = len(breaks)
    n_movements   = len(movements)
    n_ratio_warn  = len(result.ratio_warnings)
    n_review_warn = len(result.review_warnings)
    n_unmet       = len(result.unmet_rooms)

    # ── Summary metrics ───────────────────────────────────────────────
    st.markdown("### 📊 Generation Summary")
    mc = st.columns(6)
    mc[0].metric("Shifts",          n_shifts)
    mc[1].metric("Breaks",          n_breaks)
    mc[2].metric("Movements",       n_movements,
                 delta="cover required" if n_movements else None,
                 delta_color="normal" if n_movements else "off")
    mc[3].metric("Ratio warnings",  n_ratio_warn,
                 delta="review needed" if n_ratio_warn else None,
                 delta_color="inverse" if n_ratio_warn else "off")
    mc[4].metric("Break conflicts", n_review_warn,
                 delta="manual review" if n_review_warn else None,
                 delta_color="inverse" if n_review_warn else "off")
    mc[5].metric("Rooms unstaffed", n_unmet,
                 delta="no staff available" if n_unmet else None,
                 delta_color="inverse" if n_unmet else "off")

    if result.unmet_rooms:
        st.error(f"❌ No available staff for: {', '.join(result.unmet_rooms)}")
    if result.ratio_warnings:
        with st.expander(f"⚠️ {n_ratio_warn} ratio warning(s)"):
            for w in result.ratio_warnings:
                st.warning(w)
    if result.review_warnings:
        with st.expander(f"🔍 {n_review_warn} break conflict(s) — manual review"):
            for w in result.review_warnings:
                st.warning(w)

    if not shifts:
        st.info("No shifts generated. Check attendance data and staff availability.")
        return

    st.markdown("---")

    # ── Collect attendance intervals (if already loaded in session_state) ─
    # The page may have loaded intervals per-day into session_state when
    # generating; fall back to empty list if not available.
    session_intervals = st.session_state.get("ar_intervals", {})

    # ── Per-day timeline grids ────────────────────────────────────────
    # Group by date
    from collections import defaultdict
    shifts_by_day:    defaultdict = defaultdict(list)
    breaks_by_day:    defaultdict = defaultdict(list)
    movements_by_day: defaultdict = defaultdict(list)

    for s in shifts:
        shifts_by_day[s.shift_date].append(s)
    for b in breaks:
        breaks_by_day[b.break_date].append(b)
    for mv in movements:
        movements_by_day[mv.move_date].append(mv)

    all_dates = sorted(shifts_by_day.keys())

    st.markdown("### 🗓️ Roster Timeline")
    st.caption(
        "Colour-coded by room · **B40** = 40-min combined break · "
        "**B##** in amber = manual review · **CODE†** = temporary cover · "
        "Footer rows show staff count per 15-min slot (🟢 ok / 🔴 under ratio)."
    )

    for date_str in all_dates:
        day_shifts    = shifts_by_day[date_str]
        day_breaks    = breaks_by_day.get(date_str, [])
        day_movements = movements_by_day.get(date_str, [])
        day_intervals = session_intervals.get(date_str, [])
        summary       = get_day_summary(date_str, day_shifts, day_breaks, day_movements)

        # Day header
        try:
            from datetime import date as _date
            wd = _date.fromisoformat(date_str).strftime("%A %-d %B %Y")
        except Exception:
            wd = date_str

        header_extra = []
        if summary["manual_review"]:
            header_extra.append(f"🔍 {summary['manual_review']} manual review")
        if summary["movements"]:
            header_extra.append(f"🔄 {summary['movements']} movement(s)")
        header_suffix = "  ·  " + "  ·  ".join(header_extra) if header_extra else ""

        st.markdown(
            f"<h4 style='margin:16px 0 6px;color:#0d1f35;'>"
            f"📅 {wd}{header_suffix}</h4>",
            unsafe_allow_html=True,
        )

        grid_html = build_timeline_html(
            date_str=date_str,
            shifts=day_shifts,
            breaks=day_breaks,
            movements=day_movements,
            rooms=rooms,
            intervals=day_intervals,
        )
        st.markdown(grid_html, unsafe_allow_html=True)

        # Movement notes below each day's grid
        notes_html = build_movement_notes_html(day_movements)
        if notes_html:
            st.markdown(notes_html, unsafe_allow_html=True)

    # ── Weekly staff summary ──────────────────────────────────────────
    if len(all_dates) > 1 or True:   # always show for context
        staff_profiles = {}
        for s in shifts:
            if s.user_id not in staff_profiles:
                staff_profiles[s.user_id] = {"employment_type": "full_time"}
        weekly_html = build_weekly_summary_html(shifts, breaks, all_dates, staff_profiles)
        if weekly_html:
            st.markdown("---")
            st.markdown(weekly_html, unsafe_allow_html=True)

    # ── Validation panel ──────────────────────────────────────────────
    validation = getattr(result, "validation", {})
    if validation:
        st.markdown("---")
        st.markdown("### ✅ Roster Validation")

        coverage_ok       = validation.get("centre_coverage_achieved", True)
        uncovered         = validation.get("uncovered_intervals", [])
        ratio_breach      = validation.get("centre_ratio_breaches", [])
        ft_below_contr    = validation.get("ft_below_contracted", [])
        ft_over_contr     = validation.get("ft_over_contracted", [])
        pt_hrs            = validation.get("pt_ca_hours_used", 0)
        ft_onsite_ok      = validation.get("ft_onsite_achieved", True)
        ft_onsite_violns  = validation.get("ft_onsite_violations", [])
        corrections_log   = validation.get("corrections_log", [])

        # Critical banner if hard constraints are breached.
        # ft_over_contracted is intentionally EXCLUDED here — over-allocation
        # caused by the FT onsite-coverage correction pass is a documented,
        # accepted trade-off (shown as a warning below), not a hard failure.
        critical_items = ft_below_contr + uncovered + ft_onsite_violns
        if critical_items:
            st.error(
                f"⛔ **CRITICAL: {len(critical_items)} hard constraint violation(s).** "
                "Full-time minimum allocation, FT onsite coverage, or centre coverage "
                "not fully achieved. Review the reports below before saving."
            )

        # Corrections applied — shown even if some violations remain,
        # so the user can see what the engine already fixed automatically.
        if corrections_log:
            with st.expander(f"🔧 {len(corrections_log)} automatic correction(s) applied",
                             expanded=True):
                st.caption(
                    "The engine automatically adjusted shifts to resolve hard "
                    "constraint violations before finalising the roster."
                )
                for c in corrections_log:
                    st.success(f"**{c['date']}** — {c['violation']}: {c['action']}")

        # Over-contract warnings
        if ft_over_contr:
            st.warning(
                f"⚠️ **{len(ft_over_contr)} full-time educator(s) rostered above "
                f"their {FT_TARGET_WEEKLY_HOURS:.0f}h weekly contract.** "
                "Review the Full-Time Allocation Report below — this may be an "
                "accepted trade-off for full-time onsite coverage."
            )

        # Summary metrics — add over-contract count and FT onsite coverage
        mc = st.columns(7)
        mc[0].metric("Centre coverage 7:15–18:00",
                     "✅ Met" if coverage_ok else "❌ Gaps",
                     delta=f"{len(uncovered)} gap(s)" if uncovered else None,
                     delta_color="inverse" if uncovered else "off")
        mc[1].metric("FT onsite 7:15–18:00",
                     "✅ Met" if ft_onsite_ok else "❌ Gaps",
                     delta=f"{len(ft_onsite_violns)} gap(s)" if ft_onsite_violns else None,
                     delta_color="inverse" if ft_onsite_violns else "off")
        mc[2].metric("FT below contracted",  len(ft_below_contr),
                     delta="CRITICAL" if ft_below_contr else None,
                     delta_color="inverse" if ft_below_contr else "off")
        mc[3].metric("FT over contracted", len(ft_over_contr),
                     delta="review" if ft_over_contr else None,
                     delta_color="inverse" if ft_over_contr else "off")
        mc[4].metric("Ratio breaches",  len(ratio_breach),
                     delta_color="inverse" if ratio_breach else "off")
        mc[5].metric("PT/casual hours", f"{pt_hrs:.1f}h")
        mc[6].metric("FT weekly target", f"{FT_TARGET_WEEKLY_HOURS:.0f}h")

        # FT Allocation Report
        ft_report = validation.get("ft_allocation_report", [])
        if ft_report:
            st.markdown("#### 👷 Full-Time Allocation Report")
            st.caption(
                f"Hard constraint: full-time compliance is measured against weekly "
                f"contracted hours (default {FT_TARGET_WEEKLY_HOURS:.0f}h/week, "
                f"≈{FT_PREFERRED_DAILY_HOURS:.1f}h × {FT_MIN_DAYS} days). "
                f"Compliant = rostered within ±{FT_OVERTIME_THRESHOLD_HOURS:.0f}h of contracted. "
                "Exceeding contract is only accepted when required for full-time "
                "onsite coverage (see Corrections Applied above)."
            )
            report_rows = []
            for row in ft_report:
                compliant   = row.get("compliant", True)
                zero_shifts = row.get("allocated_days", 1) == 0
                report_rows.append({
                    "Educator":      row["name"],
                    "Contracted":    f"{row['contracted_hours']:.1f}h",
                    "Rostered":      f"{row['rostered_hours']:.1f}h",
                    "Variance":      f"{row['variance']:+.1f}h",
                    "Opens":         row.get("opening_shifts", "—"),
                    "Closes":        row.get("closing_shifts", "—"),
                    "✓ Compliant":  "⛔ ZERO SHIFTS" if zero_shifts else ("✅ Yes" if compliant else "❌ No"),
                    "Reason":        row.get("reason", ""),
                })
            st.dataframe(pd.DataFrame(report_rows), use_container_width=True, hide_index=True)

        # Weekly Hours Report — contracted vs rostered for all staff
        hrs_report = validation.get("weekly_hours_report", [])
        if hrs_report:
            st.markdown("#### 🕐 Weekly Hours Report")
            st.caption(
                "Contracted hours vs rostered hours for all staff this period. "
                f"⚠️ Over contracted = rostered hours exceed contracted by more than "
                f"{FT_OVERTIME_THRESHOLD_HOURS:.0f}h. "
                f"⬇ Under contracted = rostered hours below contracted by more than "
                f"{FT_OVERTIME_THRESHOLD_HOURS:.0f}h."
            )
            hrs_rows = []
            for row in hrs_report:
                hrs_rows.append({
                    "Educator":       row["name"],
                    "Type":           row["employment_type"],
                    "Contracted hrs": row["contracted_hrs"],
                    "Rostered hrs":   row["rostered_hrs"],
                    "Variance":       row["variance"],
                    "Status":         row["status"],
                })
            st.dataframe(pd.DataFrame(hrs_rows), use_container_width=True, hide_index=True)

        # Attendance demand validation
        demand = validation.get("attendance_demand", [])
        if demand:
            shortfalls = [d for d in demand if d["delta"] < 0]
            surpluses  = [d for d in demand if d["delta"] > 1]
            with st.expander(
                f"📊 Attendance demand vs rostered staff "
                f"({'❌ ' + str(len(shortfalls)) + ' shortfall(s)' if shortfalls else '✅ all covered'})",
                expanded=bool(shortfalls),
            ):
                st.caption(
                    "Required educators = calculated from actual_children ÷ ratio. "
                    "Delta = rostered − required. Negative = shortfall, high positive = surplus."
                )
                # Show only rows with shortfall or surplus for brevity
                notable = [d for d in demand if d["delta"] != 0]
                if notable:
                    st.dataframe(pd.DataFrame(notable), use_container_width=True, hide_index=True)
                else:
                    st.success("Rostered staff exactly matches attendance demand for all intervals.")
        # FT onsite coverage 7:15–18:00
        ft_onsite = validation.get("ft_onsite_coverage", [])
        if ft_onsite_violns or ft_onsite:
            with st.expander(
                f"👤 FT onsite coverage 7:15–18:00 "
                f"({'❌ ' + str(len(ft_onsite_violns)) + ' gap(s)' if ft_onsite_violns else '✅ continuous'})",
                expanded=bool(ft_onsite_violns),
            ):
                st.caption(
                    "Hard constraint: at least one full-time educator must be onsite "
                    "for every 15-minute interval the centre is open. "
                    "Engine extends FT shift edges automatically where availability allows "
                    "(see Corrections Applied above)."
                )
                if ft_onsite_violns:
                    for w in ft_onsite_violns:
                        st.error(w)
                # Show only non-compliant slots for brevity; if all compliant, confirm
                non_compliant = [r for r in ft_onsite if not r["compliant"]]
                if non_compliant:
                    st.dataframe(pd.DataFrame(non_compliant), use_container_width=True, hide_index=True)
                else:
                    st.success("At least one full-time educator is onsite for every interval, every day.")

        if uncovered:
            with st.expander(f"❌ {len(uncovered)} uncovered interval(s) — centre not staffed",
                             expanded=True):
                for w in uncovered:
                    st.error(w)

        # Ratio breaches
        if ratio_breach:
            with st.expander(f"⚠️ {len(ratio_breach)} ratio breach(es)"):
                for w in ratio_breach:
                    st.warning(w)

        # Over-contract detail
        if ft_over_contr:
            with st.expander(f"⚠️ {len(ft_over_contr)} full-time educator(s) over contracted hours"):
                for w in ft_over_contr:
                    st.warning(w)

    # ── Debug expander (replaces old shift/break tables) ─────────────
    st.markdown("---")
    with st.expander("🔍 Raw data (debug)", expanded=False):
        st.markdown("**Shifts**")
        _render_shift_table(shifts, room_map)
        if breaks:
            st.markdown("**Breaks**")
            _render_break_table(breaks)
        if breaks:
            st.markdown("**Break objects**")
            debug_rows = []
            for b in sorted(breaks, key=lambda x: (x.break_date, x.user_name, x.planned_start_time)):
                debug_rows.append({
                    "Educator":     b.user_name,
                    "Date":         b.break_date,
                    "break_type":   b.break_type,
                    "dur_min":      b.planned_duration_minutes,
                    "combined":     getattr(b, "combined",      "—"),
                    "label":        getattr(b, "label",         "—"),
                    "paid_min":     getattr(b, "paid_minutes",  "—"),
                    "unpaid_min":   getattr(b, "unpaid_minutes","—"),
                    "status":       b.status,
                })
            st.dataframe(pd.DataFrame(debug_rows), use_container_width=True, hide_index=True)
        debug_log = getattr(result, "debug_log", [])
        if debug_log:
            st.markdown("**Break scheduling decisions (centre-wide ratio check)**")
            st.caption(
                "Shows each proposed break time, centre-wide staffing before/after, "
                "room staffing before/after, and whether it was accepted or rejected."
            )
            st.dataframe(pd.DataFrame(debug_log), use_container_width=True, hide_index=True)

    # ── Save section ──────────────────────────────────────────────────
    st.markdown("---")
    if movements:
        st.markdown("### 🔄 Educator Movements")
        _render_movement_table(movements)
        st.markdown("---")

    st.markdown("### 💾 Save Generated Data")

    existing_periods  = fetch_roster_periods(centre_id, limit=5)
    overlap           = [
        p for p in existing_periods
        if p.get("start_date") <= end_d.isoformat()
        and p.get("end_date")  >= start_d.isoformat()
    ]
    published_overlap = [p for p in overlap if p.get("status") == "published"]

    if published_overlap:
        st.error(
            "❌ A **published** roster overlaps this period. "
            "Archive it first if you want to regenerate."
        )
        return

    draft_overlap = [p for p in overlap if p.get("status") == "draft"]

    sa1, sa2, _ = st.columns([2, 2, 3])
    save_roster = sa1.button(
        f"💾 Save Roster ({n_shifts} shifts)",
        type="primary", use_container_width=True, disabled=n_shifts == 0,
    )
    save_breaks = sa2.button(
        f"💾 Save Breaks ({n_breaks} breaks)",
        use_container_width=True,
        disabled=n_breaks == 0 or not st.session_state.get("ar_period_id"),
    )

    if save_breaks and not st.session_state.get("ar_period_id"):
        st.info("Save the roster first.")

    if draft_overlap:
        st.warning(
            f"⚠️ A draft roster already exists "
            f"({draft_overlap[0]['start_date']} – {draft_overlap[0]['end_date']}). "
            "Saving will replace all draft shifts."
        )
        confirm = st.checkbox("Yes, replace the existing draft shifts", key="ar_confirm_replace")
    else:
        confirm = True

    if save_roster and confirm:
        with st.spinner("Saving roster…"):
            try:
                if draft_overlap:
                    period_id = draft_overlap[0]["id"]
                    delete_all_draft_shifts(period_id)
                else:
                    new_period = create_roster_period(
                        centre_id=centre_id,
                        start_date=start_d.isoformat(),
                        end_date=end_d.isoformat(),
                        notes="Auto-generated by roster engine",
                    )
                    period_id = new_period["id"]
                shift_rows = [
                    {
                        "user_id":                       s.user_id,
                        "room_id":                       s.room_id,
                        "shift_date":                    s.shift_date,
                        "start_time":                    s.start_time,
                        "end_time":                      s.end_time,
                        "shift_type":                    s.shift_type,
                        "break_duration_minutes":        0,
                        "unpaid_break_opt_out_override": s.break_opt_out_override,
                        "notes":                         f"Auto-generated ({s.source})",
                    }
                    for s in shifts
                ]
                n_saved = create_shifts_batch(period_id, centre_id, shift_rows)
                st.session_state["ar_period_id"] = period_id
                toast_success(f"✅ Saved {n_saved} shifts to draft roster.")
                st.rerun()
            except Exception as e:
                toast_error(f"Could not save roster: {e}")

    if save_breaks:
        period_id = st.session_state.get("ar_period_id")
        if not period_id:
            toast_error("Save the roster first.")
            return
        with st.spinner("Saving breaks…"):
            try:
                break_rows = [
                    {
                        "centre_id":                centre_id,
                        "user_id":                  b.user_id,
                        "break_date":               b.break_date,
                        "break_type":               b.break_type,
                        "planned_start_time":        b.planned_start_time,
                        "planned_end_time":          b.planned_end_time,
                        "planned_duration_minutes":  b.planned_duration_minutes,
                        "paid_component_minutes":    getattr(b, "paid_minutes",   0),
                        "unpaid_component_minutes":  getattr(b, "unpaid_minutes", 0),
                        "status":                    b.status,
                        "notes": (
                            f"Auto-generated · {b.opt_out_source}"
                            + (" · MANUAL REVIEW" if b.status == "manual_review" else "")
                        ),
                    }
                    for b in breaks
                ]
                n_saved = create_breaks_batch(break_rows)
                toast_success(f"✅ Saved {n_saved} breaks.")
            except Exception as e:
                toast_error(f"Could not save breaks: {e}")

    if st.session_state.get("ar_period_id"):
        pid = st.session_state["ar_period_id"]
        if st.button("✏️ Open in Roster Builder", use_container_width=False):
            st.session_state.roster_period_id = pid
            st.session_state.page = "roster_builder"
            st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# TABLE RENDERERS
# ─────────────────────────────────────────────────────────────────────────────

def _render_shift_table(shifts: list, room_map: dict):
    STATUS_ICON = {"primary_room": "⭐", "available": "✅", "unmatched": "⚠️"}
    rows = []
    for s in sorted(shifts, key=lambda x: (x.shift_date, x.room_name, x.start_time)):
        opt_label = {
            "opted_out":         "Opted out",
            "not_opted_out":     "Not opted out",
            "use_staff_default": "Staff default",
        }.get(s.break_opt_out_override, s.break_opt_out_override)
        rows.append({
            "Date":           s.shift_date,
            "Room":           s.room_name,
            "Educator":       s.user_name,
            "Start":          s.start_time[:5],
            "End":            s.end_time[:5],
            "Type":           s.shift_type.title(),
            "Unpaid opt-out": opt_label,
            "Source":         STATUS_ICON.get(s.source, "?") + " " + s.source.replace("_", " ").title(),
        })
    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)
    st.caption("⭐ Primary room assignment  ·  ✅ Available  ·  ⚠️ Unmatched")


def _collapse_breaks(breaks: list) -> list[dict]:
    """
    Collapse the raw SuggestedBreak list into display rows.

    Groups by (date, educator_name).  Within each group, sorts by start time
    then walks adjacent pairs: if two breaks overlap OR share the same
    combined-period window, they are merged into one display dict.

    Merge rules:
      • start      = min of both starts
      • end        = max of both ends
      • paid_min   = sum
      • unpaid_min = sum
      • duration   = minutes from merged start to merged end
      • break_type = "combined" when both paid_min > 0 and unpaid_min > 0,
                     else whichever type has non-zero minutes
      • status     = "manual_review" if either source row has that status,
                     otherwise "scheduled"
      • opt_out    = kept from the first row (both should agree)

    Non-overlapping breaks are emitted as separate display dicts.
    """
    from datetime import datetime as _dt

    def _mins(s: str, e: str) -> int:
        try:
            return max(0, int(
                (_dt.strptime(e[:5], "%H:%M") - _dt.strptime(s[:5], "%H:%M"))
                .total_seconds() / 60
            ))
        except Exception:
            return 0

    def _overlaps_display(a_start: str, a_end: str, b_start: str, b_end: str) -> bool:
        return a_start < b_end and b_start < a_end

    # Group by (date, educator)
    from collections import defaultdict
    groups: dict[tuple, list] = defaultdict(list)
    for b in breaks:
        groups[(b.break_date, b.user_name)].append(b)

    display_rows: list[dict] = []

    for (date_str, educator), group in sorted(groups.items()):
        # Sort within group by start time
        group.sort(key=lambda b: b.planned_start_time)

        # Walk and merge overlapping / same-window pairs
        # Use plain dicts as accumulators so we can freely mutate them
        accum: list[dict] = []
        for b in group:
            b_start = b.planned_start_time[:8]
            b_end   = b.planned_end_time[:8]
            b_paid   = getattr(b, "paid_minutes",   0) or 0
            b_unpaid = getattr(b, "unpaid_minutes", 0) or 0
            b_status = b.status or "scheduled"
            b_opt    = b.opt_out_source or ""

            if accum and _overlaps_display(
                accum[-1]["_start"], accum[-1]["_end"], b_start, b_end
            ):
                # Merge into the last accumulator entry
                prev = accum[-1]
                prev["_start"]   = min(prev["_start"],   b_start)
                prev["_end"]     = max(prev["_end"],     b_end)
                prev["paid"]    += b_paid
                prev["unpaid"]  += b_unpaid
                if b_status == "manual_review":
                    prev["status"] = "manual_review"
            else:
                accum.append({
                    "_start":  b_start,
                    "_end":    b_end,
                    "paid":    b_paid,
                    "unpaid":  b_unpaid,
                    "status":  b_status,
                    "opt_out": b_opt,
                })

        for row in accum:
            paid    = row["paid"]
            unpaid  = row["unpaid"]
            start5  = row["_start"][:5]
            end5    = row["_end"][:5]
            dur     = _mins(row["_start"], row["_end"])

            if paid > 0 and unpaid > 0:
                btype = "Combined break"
            elif paid > 0:
                btype = "Rest (paid)"
            else:
                btype = "Meal (unpaid)"

            STATUS_ICON = {"scheduled": "✅", "manual_review": "🔍"}
            status_str = row["status"]
            status_display = (
                STATUS_ICON.get(status_str, "?") + " "
                + status_str.replace("_", " ").title()
            )

            display_rows.append({
                "Date":       date_str,
                "Educator":   educator,
                "Break":      btype,
                "Start":      start5,
                "End":        end5,
                "Duration":   f"{dur} min",
                "Paid min":   paid   if paid   > 0 else "—",
                "Unpaid min": unpaid if unpaid > 0 else "—",
                "Status":     status_display,
                "Opt-out":    row["opt_out"],
            })

    return display_rows


def _render_break_table(breaks: list):
    """
    Render the generated break schedule table.

    Calls _collapse_breaks first so that two SuggestedBreak objects that
    occupy the same or overlapping window for the same educator/date are
    always shown as one combined display row — never as two separate rows
    with overlapping times.
    """
    if not breaks:
        st.info("No breaks generated.")
        return

    display_rows = _collapse_breaks(breaks)

    df = pd.DataFrame(display_rows)
    st.dataframe(df, use_container_width=True, hide_index=True)

    n_combined = sum(1 for r in display_rows if r["Break"] == "Combined break")
    n_separate = len(display_rows) - n_combined
    n_review   = sum(1 for r in display_rows if "Manual Review" in r["Status"])
    notes = []
    if n_combined:
        notes.append(f"🔵 {n_combined} combined block(s)")
    if n_separate:
        notes.append(f"⚪ {n_separate} separate break(s)")
    if n_review:
        notes.append(f"🔍 {n_review} manual review")
    else:
        notes.append("🔍 = manual review required")
    st.caption("  ·  ".join(notes))


def _render_movement_table(movements: list) -> None:
    """
    Render a simple table of temporary educator movements required for break cover.
    Each row: Educator | From room | To room | Date | Start | End | Reason
    """
    rows = []
    for mv in sorted(movements, key=lambda m: (m.move_date, m.start_time)):
        rows.append({
            "Educator":    mv.educator_name,
            "From room":   mv.from_room_name,
            "To room":     mv.to_room_name,
            "Date":        mv.move_date,
            "Start":       mv.start_time[:5],
            "End":         mv.end_time[:5],
            "Covering for": mv.covering_for_name,
            "Reason":      mv.reason,
        })
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        st.caption(
            "💡 These are temporary moves only. "
            "Original shift room assignments are unchanged."
        )
