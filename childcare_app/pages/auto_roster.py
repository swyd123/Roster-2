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

from utils.auto_roster_engine import generate_roster, SuggestedShift, SuggestedBreak
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
        else:
            result = st.session_state["ar_result"]

        _render_result(result, centre_id, start_d, end_d, rooms, db_rules)


# ─────────────────────────────────────────────────────────────────────────────
# RESULT RENDERING
# ─────────────────────────────────────────────────────────────────────────────

def _render_result(result, centre_id, start_d, end_d, rooms, db_rules):
    from utils.break_engine import BREAK_RULES_DEFAULT
    room_map = {r["id"]: r["name"] for r in rooms}

    shifts = result.shifts
    breaks = result.breaks

    # ── Summary banner ────────────────────────────────────────────────
    n_shifts      = len(shifts)
    n_breaks      = len(breaks)
    n_ratio_warn  = len(result.ratio_warnings)
    n_review_warn = len(result.review_warnings)
    n_unmet       = len(result.unmet_rooms)

    st.markdown("### 📊 Generation Summary")
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Shifts suggested",  n_shifts)
    m2.metric("Breaks suggested",  n_breaks)
    m3.metric("Ratio warnings",    n_ratio_warn,
              delta="review needed" if n_ratio_warn else None,
              delta_color="inverse" if n_ratio_warn else "off")
    m4.metric("Break conflicts",   n_review_warn,
              delta="manual review" if n_review_warn else None,
              delta_color="inverse" if n_review_warn else "off")
    m5.metric("Rooms unstaffed",   n_unmet,
              delta="no staff available" if n_unmet else None,
              delta_color="inverse" if n_unmet else "off")

    if result.unmet_rooms:
        st.error(
            f"❌ **No available staff found for: {', '.join(result.unmet_rooms)}** — "
            "check staff availability settings."
        )
    if result.ratio_warnings:
        with st.expander(f"⚠️ {n_ratio_warn} ratio warning(s)", expanded=False):
            for w in result.ratio_warnings:
                st.warning(w)
    if result.review_warnings:
        with st.expander(f"🔍 {n_review_warn} break conflict(s) — manual review required",
                         expanded=False):
            for w in result.review_warnings:
                st.warning(w)

    st.markdown("---")

    # ── Roster table ──────────────────────────────────────────────────
    st.markdown("### 🗓️ Generated Roster")
    st.caption(
        "Review the suggested shifts. "
        "Save creates a new **draft** roster period."
    )
    if not shifts:
        st.info("No shifts could be generated. Check attendance data and staff availability.")
    else:
        _render_shift_table(shifts, room_map)

    # ── Break schedule table ──────────────────────────────────────────
    st.markdown("---")
    st.markdown("### ☕ Generated Break Schedule")
    st.caption(
        "Break times are placed to maintain ratio compliance. "
        "5–7 hr shifts get a combined 40-min block (10 paid + 30 unpaid). "
        "7+ hr shifts get a combined 50-min block (20 paid + 30 unpaid). "
        "Conflicts are flagged for manual review."
    )
    if not breaks:
        st.info("No breaks generated (no shifts with break entitlement, or no eligible windows).")
    else:
        _render_break_table(breaks)

    # ── Debug expander — raw break object fields ──────────────────────
    if breaks:
        with st.expander("🔍 Break generation debug", expanded=False):
            st.caption(
                "Raw break object fields from the engine. "
                "Use this to verify combined breaks are being generated correctly."
            )
            debug_rows = []
            for b in sorted(breaks, key=lambda x: (x.break_date, x.user_name, x.planned_start_time)):
                debug_rows.append({
                    "Educator":              b.user_name,
                    "Date":                  b.break_date,
                    "break_type":            b.break_type,
                    "planned_duration_min":  b.planned_duration_minutes,
                    "combined":              getattr(b, "combined", "—"),
                    "label":                 getattr(b, "label",    "—"),
                    "paid_component_min":    getattr(b, "paid_minutes",   "—"),
                    "unpaid_component_min":  getattr(b, "unpaid_minutes", "—"),
                    "status":                b.status,
                    "opt_out_source":        b.opt_out_source,
                })
            st.dataframe(pd.DataFrame(debug_rows), use_container_width=True, hide_index=True)

    # ── Save buttons ──────────────────────────────────────────────────
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
            "Archive the existing roster first if you want to regenerate."
        )
        return

    draft_overlap = [p for p in overlap if p.get("status") == "draft"]

    sa1, sa2, _ = st.columns([2, 2, 3])

    save_roster = sa1.button(
        f"💾 Save Roster ({n_shifts} shifts)",
        type="primary",
        use_container_width=True,
        disabled=n_shifts == 0,
    )
    save_breaks = sa2.button(
        f"💾 Save Breaks ({n_breaks} breaks)",
        use_container_width=True,
        disabled=n_breaks == 0 or not st.session_state.get("ar_period_id"),
    )

    if save_breaks and not st.session_state.get("ar_period_id"):
        st.info("Save the roster first to get a period ID for the breaks.")

    if draft_overlap:
        st.warning(
            f"⚠️ A draft roster already exists for this period "
            f"({draft_overlap[0]['start_date']} – {draft_overlap[0]['end_date']}). "
            "Saving will **replace** all draft shifts with the new suggestions."
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
                        "user_id":                      s.user_id,
                        "room_id":                      s.room_id,
                        "shift_date":                   s.shift_date,
                        "start_time":                   s.start_time,
                        "end_time":                     s.end_time,
                        "shift_type":                   s.shift_type,
                        "break_duration_minutes":       0,
                        "unpaid_break_opt_out_override": s.break_opt_out_override,
                        "notes":                        f"Auto-generated ({s.source})",
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


def _render_break_table(breaks: list):
    """
    Render the generated break schedule table.
    Reads directly from SuggestedBreak objects — does NOT recalculate.
    """
    STATUS_ICON = {"scheduled": "✅", "manual_review": "🔍"}
    # Fallback label map for breaks that lack a label attribute
    TYPE_LABEL  = {
        "rest":     "Rest (paid)",
        "meal":     "Meal (unpaid)",
        "combined": "Combined break",
    }

    rows = []
    for b in sorted(breaks, key=lambda x: (x.break_date, x.user_name, x.planned_start_time)):
        # Use label attribute when present (combined breaks carry their own label)
        b_label = getattr(b, "label", None)
        b_combined = getattr(b, "combined", False)
        display_type = (
            b_label or TYPE_LABEL.get(b.break_type, b.break_type.title())
            if b_combined
            else TYPE_LABEL.get(b.break_type, b.break_type.title())
        )
        rows.append({
            "Date":        b.break_date,
            "Educator":    b.user_name,
            "Break":       display_type,
            "Start":       b.planned_start_time[:5],
            "End":         b.planned_end_time[:5],
            "Duration":    f"{b.planned_duration_minutes} min",
            "Paid min":    getattr(b, "paid_minutes",   "—"),
            "Unpaid min":  getattr(b, "unpaid_minutes", "—"),
            "Status":      STATUS_ICON.get(b.status, "?") + " " + b.status.replace("_", " ").title(),
            "Opt-out":     b.opt_out_source,
        })

    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)

    n_combined = sum(1 for b in breaks if getattr(b, "combined", False))
    n_separate = len(breaks) - n_combined
    notes = []
    if n_combined:
        notes.append(f"🔵 {n_combined} combined block(s)")
    if n_separate:
        notes.append(f"⚪ {n_separate} separate break(s)")
    notes.append("🔍 = manual review required")
    st.caption("  ·  ".join(notes))
