# pages/roster_builder.py — The main visual roster builder with 15-min grid
#
# Each day tab now has two sub-tabs:
#   "🗓️ Roster"            — existing grid + shift list + add-shift form
#   "👥 Staffing Allocation" — overflow engine output: movement instructions,
#                              adjusted staffing, required educators
#
# Attendance data (room_attendance_intervals) is loaded per-day on demand
# from the staffing tab. Existing roster/build functionality is unchanged.

import streamlit as st
from datetime import date, timedelta, datetime

import pandas as pd

from utils.roster_queries import (
    fetch_roster_period_by_id, fetch_shifts_for_period,
    fetch_shift_templates, fetch_approved_leave_for_period,
    fetch_availability_map, enrich_shifts_with_qual_flags,
    create_shift, update_shift, delete_shift, publish_roster_period,
)
from utils.roster_engine import (
    validate_roster, roster_compliance_summary, find_staffing_gaps,
    build_grid_data, generate_time_options, classify_shift_type,
    slot_label, TOTAL_SLOTS, SLOT_MINUTES, DAY_START_HOUR,
)
from utils.room_queries import fetch_rooms, fetch_children_by_centre
from utils.staff_queries import fetch_all_staff, fetch_centres
from utils.attendance_queries import fetch_intervals_for_centre
from utils.room_overflow_engine import (
    analyse_overflow,
    centre_overflow_summary,
    interval_timeline,
    peak_adjusted_staff,
    peak_overflow,
)
from utils.break_preferences_queries import fetch_break_prefs_for_centre
from utils.helpers import toast_success, toast_error, toast_warn, fmt_date


SHIFT_COLOURS = {
    "opening":  "#0ea5e9",
    "closing":  "#8b5cf6",
    "standard": "#3b82f6",
    "split":    "#f59e0b",
    "overtime": "#ef4444",
    "on_call":  "#6b7280",
}

# Ratio status colours for the staffing table
_STATUS_STYLE = {
    "ok":      ("✅", "#f0fdf4", "#14532d"),
    "warning": ("⚠️", "#fffbeb", "#92400e"),
    "breach":  ("❌", "#fff1f2", "#991b1b"),
    "no_data": ("—",  "#f8fafc", "#94a3b8"),
}


def render():
    period_id = st.session_state.get("roster_period_id")
    if not period_id:
        st.warning("No roster period selected.")
        if st.button("← Rosters"):
            st.session_state.page = "roster_list"; st.rerun()
        return

    bc, _ = st.columns([1, 10])
    with bc:
        st.markdown('<div class="back-btn">', unsafe_allow_html=True)
        if st.button("← Rosters", key="rb_back"):
            st.session_state.page = "roster_list"; st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    with st.spinner("Loading roster…"):
        try:
            period = fetch_roster_period_by_id(period_id)
        except Exception as e:
            toast_error(f"Could not load roster: {e}"); return

    if not period:
        toast_error("Roster period not found.")
        st.session_state.page = "roster_list"; st.rerun(); return

    centre_id   = period["centre_id"]
    start_d     = date.fromisoformat(period["start_date"])
    end_d       = date.fromisoformat(period["end_date"])
    status      = period.get("status", "draft")
    is_editable = status == "draft"

    all_days = []
    d = start_d
    while d <= end_d:
        all_days.append(d)
        d += timedelta(days=1)

    # ── Header ────────────────────────────────────────────────────────
    st.markdown(
        f'<div style="display:flex;align-items:baseline;gap:1rem;margin-bottom:0.2rem;">'
        f'<h1 style="margin:0;">Roster Builder</h1>'
        f'<span style="font-size:0.95rem;color:#7a90a8;">'
        f'{start_d.strftime("%-d %b")} – {end_d.strftime("%-d %b %Y")}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    status_html = (
        f'<span style="background:#f0fdf4;color:#166534;padding:3px 10px;'
        f'border-radius:99px;font-size:0.8rem;font-weight:600;">✅ Published</span>'
        if status == "published" else
        f'<span style="background:#eff6ff;color:#1d4ed8;padding:3px 10px;'
        f'border-radius:99px;font-size:0.8rem;font-weight:600;">📝 Draft</span>'
    )
    hb1, hb2, hb3 = st.columns([3, 1, 1])
    hb1.markdown(f'<p class="page-sub">{status_html}&nbsp; Roster period</p>',
                 unsafe_allow_html=True)

    if is_editable:
        with hb2:
            if st.button("📋  Templates", use_container_width=True):
                st.session_state.page = "shift_templates"; st.rerun()
        with hb3:
            if st.button("✅  Publish", type="primary", use_container_width=True):
                st.session_state["show_publish_panel"] = True
                st.rerun()

    # ── Load core data ────────────────────────────────────────────────
    with st.spinner("Loading shifts and validation data…"):
        try:
            raw_shifts  = fetch_shifts_for_period(period_id)
            all_shifts  = enrich_shifts_with_qual_flags(raw_shifts)
            rooms       = fetch_rooms(centre_id)
            children    = fetch_children_by_centre(centre_id)
            templates   = fetch_shift_templates(centre_id)
            staff_list  = fetch_all_staff()
            leave_map   = fetch_approved_leave_for_period(
                centre_id, period["start_date"], period["end_date"])
            avail_map   = fetch_availability_map(centre_id)
            break_prefs = fetch_break_prefs_for_centre(centre_id)
        except Exception as e:
            toast_error(f"Could not load data: {e}"); return

    # ── Publish panel ─────────────────────────────────────────────────
    if st.session_state.get("show_publish_panel"):
        _render_publish_panel(period_id, centre_id, all_shifts, rooms,
                              children, leave_map, avail_map, all_days)

    # ── Week-level compliance strip ───────────────────────────────────
    _render_week_compliance_strip(all_shifts, rooms, children, leave_map, avail_map, all_days)

    st.markdown("---")

    # ── Day tabs ──────────────────────────────────────────────────────
    tab_labels = [d.strftime("%a %-d") for d in all_days]
    tabs       = st.tabs(tab_labels)

    for tab, day in zip(tabs, all_days):
        with tab:
            day_shifts = [s for s in all_shifts
                          if s.get("shift_date") == day.isoformat()]

            # Two sub-tabs per day
            sub_roster, sub_staffing = st.tabs(["🗓️  Roster", "👥  Staffing Allocation"])

            with sub_roster:
                _render_day_view(
                    day, day_shifts, rooms, children, templates, staff_list,
                    leave_map, avail_map, period_id, centre_id, is_editable,
                )

            with sub_staffing:
                _render_staffing_allocation_tab(
                    day, day_shifts, rooms, children, centre_id, break_prefs,
                )


# ── Week compliance strip ──────────────────────────────────────────────────────

def _render_week_compliance_strip(shifts, rooms, children, leave_map, avail_map, days):
    cols = st.columns(len(days))
    for col, day in zip(cols, days):
        day_shifts = [s for s in shifts if s.get("shift_date") == day.isoformat()]
        if not day_shifts and not any(
            day.isoweekday() in (c.get("enrolment_days") or []) for c in children
        ):
            col.markdown(
                f'<div style="text-align:center;padding:0.3rem;background:#f1f5f9;'
                f'border-radius:6px;font-size:0.72rem;color:#94a3b8;">'
                f'{day.strftime("%a")}<br>—</div>',
                unsafe_allow_html=True,
            )
            continue

        conflicts = validate_roster(day_shifts, rooms, children, leave_map, avail_map, day)
        errors    = sum(1 for c in conflicts if c.severity == "error")
        warnings  = sum(1 for c in conflicts if c.severity == "warning")

        if errors > 0:
            bg, tc, label = "#fee2e2", "#991b1b", f"❌ {errors}e"
        elif warnings > 0:
            bg, tc, label = "#fef3c7", "#92400e", f"⚠️ {warnings}w"
        else:
            bg, tc, label = "#dcfce7", "#166534", "✅ OK"

        col.markdown(
            f'<div style="text-align:center;padding:0.3rem;background:{bg};'
            f'border-radius:6px;font-size:0.72rem;color:{tc};font-weight:600;">'
            f'{day.strftime("%a")}<br>{label}</div>',
            unsafe_allow_html=True,
        )


# ── Day view (roster sub-tab) ──────────────────────────────────────────────────

def _render_day_view(day, day_shifts, rooms, children, templates, staff_list,
                     leave_map, avail_map, period_id, centre_id, is_editable):
    conflicts = validate_roster(day_shifts, rooms, children, leave_map, avail_map, day)
    errors    = [c for c in conflicts if c.severity == "error"]
    warnings  = [c for c in conflicts if c.severity == "warning"]

    if errors:
        st.error(
            f"❌ **{len(errors)} error(s)** on {day.strftime('%A %-d %b')} — "
            f"roster cannot be published until resolved."
        )
    if warnings:
        st.warning(f"⚠️ {len(warnings)} warning(s) — review before publishing.")

    grid_data = build_grid_data(day_shifts, rooms, children, day.isoweekday())
    _render_coverage_grid(grid_data, rooms, day)

    gaps = find_staffing_gaps(day_shifts, rooms, children, day.isoweekday())
    if gaps:
        with st.expander(f"🔍 {len(gaps)} staffing gap(s) found", expanded=len(gaps) > 0):
            for gap in gaps:
                colour = gap.get("room_colour", "#3498DB")
                st.markdown(
                    f'<div style="border-left:4px solid {colour};padding:0.4rem 0.8rem;'
                    f'margin-bottom:0.3rem;background:#fff8f0;border-radius:0 6px 6px 0;">'
                    f'<strong>{gap["room_name"]}</strong> · '
                    f'{gap["time_from"]}–{gap["time_to"]} · '
                    f'Need <strong>{gap["shortfall"]} more staff</strong> '
                    f'({gap["n_children"]} children, {gap["n_staff"]} staff)'
                    f'</div>',
                    unsafe_allow_html=True,
                )

    st.markdown("---")

    if day_shifts:
        st.markdown(f"**{len(day_shifts)} shift(s) — {day.strftime('%A %-d %B')}**")
        _render_shift_list(day_shifts, rooms, conflicts, is_editable, centre_id)
    else:
        st.caption(f"No shifts on {day.strftime('%A %-d %B')}.")

    if is_editable:
        st.markdown("")
        add_key = f"add_shift_{day.isoformat()}"
        if st.button(f"➕  Add Shift — {day.strftime('%a %-d %b')}", key=add_key,
                      use_container_width=False):
            st.session_state[f"show_add_{day.isoformat()}"] = True
            st.rerun()

        if st.session_state.get(f"show_add_{day.isoformat()}"):
            _render_add_shift_form(
                day, period_id, centre_id, rooms, staff_list, templates,
                leave_map, avail_map,
            )


# ── Staffing Allocation sub-tab ────────────────────────────────────────────────

def _render_staffing_allocation_tab(
    day: date,
    day_shifts: list[dict],
    rooms: list[dict],
    children: list[dict],
    centre_id: str,
    break_prefs: dict[str, dict[int, bool]] | None = None,
):
    """
    Loads attendance intervals for this day, runs the overflow engine,
    then renders:
      • Summary banner (total movements, busiest interval, unresolved overflow)
      • Per-room per-interval staffing table with movement instructions
    """
    date_str  = day.isoformat()
    room_map  = {r["id"]: r for r in rooms}

    st.markdown(f"**Staffing Allocation — {day.strftime('%A %-d %B %Y')}**")
    st.caption(
        "Based on actual attendance from room_attendance_intervals. "
        "Movement instructions are for staffing guidance only — "
        "no attendance records or room assignments are changed."
    )

    # ── Load attendance intervals for this day ─────────────────────────
    with st.spinner("Loading attendance data…"):
        try:
            day_intervals = fetch_intervals_for_centre(centre_id, date_str)
        except Exception as e:
            st.error(f"Could not load attendance data: {e}")
            return

    if not day_intervals:
        st.info(
            f"No attendance interval data found for {date_str}. "
            "Upload a CSV on the **👶 Child Attendance** page first."
        )
        return

    # ── Run overflow engine ────────────────────────────────────────────
    overflow_results = analyse_overflow(
        rooms=rooms,
        day_intervals=day_intervals,
        children=children,
    )

    if not overflow_results:
        st.info("Attendance data found but no interval results could be computed.")
        return

    # ── Build rostered staff counts per room per interval ─────────────
    # For each interval slot, count shifts that cover it.
    rostered_map = _build_rostered_staff_map(day_shifts, rooms)

    # ── Summary banner ────────────────────────────────────────────────
    summary = centre_overflow_summary(overflow_results, rooms)
    _render_staffing_summary_banner(summary, date_str, overflow_results, rooms)

    st.markdown("---")

    # ── Per-room staffing table ───────────────────────────────────────
    for room in rooms:
        rid    = room["id"]
        rname  = room.get("name", "Room")
        colour = room.get("colour", "#3498DB")

        timeline = interval_timeline(overflow_results, rid)
        if not timeline:
            continue   # no attendance data for this room today

        st.markdown(
            f'<div style="display:flex;align-items:center;gap:0.6rem;'
            f'margin:0.8rem 0 0.3rem;">'
            f'<div style="width:12px;height:12px;border-radius:50%;'
            f'background:{colour};"></div>'
            f'<strong style="font-size:1rem;color:#0d1f35;">{rname}</strong>'
            f'</div>',
            unsafe_allow_html=True,
        )

        table_rows = []
        for iv in timeline:
            istart    = iv["interval_start"]
            orig      = iv["original_count"]
            adjusted  = iv["adjusted_count"]
            cap       = iv["capacity"]
            overflow  = iv["overflow"]
            min_staff = iv["min_staff_required"]
            received  = iv["received_overflow"]
            needs_rev = iv["needs_review"]

            # Rostered staff at this interval
            rostered = rostered_map.get(rid, {}).get(istart, 0)

            # Ratio status using adjusted count
            ratio_status = _calc_ratio_status(adjusted, rostered, min_staff, overflow)
            icon, bg, tc = _STATUS_STYLE.get(ratio_status, _STATUS_STYLE["no_data"])

            # Movement instruction
            instruction = _build_movement_instruction(
                iv["suggestions"], rid, room_map, needs_rev
            )

            table_rows.append({
                "Time":               istart[:5],
                "Actual":             orig,
                "Adjusted":           adjusted,
                "Cap":                cap,
                "Overflow":           overflow if overflow > 0 else "",
                "Received":           received if received > 0 else "",
                "Req. educators":     min_staff,
                "Rostered":           rostered,
                "Ratio":              f"{icon} {ratio_status.upper()}" if ratio_status != "no_data" else "—",
                "Movement instruction": instruction,
            })

        if table_rows:
            df = pd.DataFrame(table_rows)
            st.dataframe(
                df,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Time":               st.column_config.TextColumn("Time",    width="small"),
                    "Actual":             st.column_config.NumberColumn("Actual", width="small"),
                    "Adjusted":           st.column_config.NumberColumn("Adj.",   width="small"),
                    "Cap":                st.column_config.NumberColumn("Cap",    width="small"),
                    "Overflow":           st.column_config.NumberColumn("Overflow", width="small"),
                    "Received":           st.column_config.NumberColumn("Received", width="small"),
                    "Req. educators":     st.column_config.NumberColumn("Req. staff", width="small"),
                    "Rostered":           st.column_config.NumberColumn("Rostered",  width="small"),
                    "Ratio":              st.column_config.TextColumn("Ratio",    width="small"),
                    "Movement instruction": st.column_config.TextColumn(
                        "Movement instruction", width="large"
                    ),
                },
            )

    # ── Legend ────────────────────────────────────────────────────────
    st.markdown("")
    st.caption(
        "**Columns:** Actual = recorded attendance · Adj. = after overflow redistribution · "
        "Req. staff = min educators for adjusted count · Rostered = shifts covering this slot · "
        "Overflow / Received = children moved out / in for staffing only."
    )
    st.caption(
        "⚠️ Movement instructions are staffing guidance only. "
        "No child records, room assignments, or attendance data are changed."
    )


# ── Summary banner ─────────────────────────────────────────────────────────────

def _render_staffing_summary_banner(
    summary: dict,
    date_str: str,
    overflow_results: dict,
    rooms: list[dict],
):
    n_overflow    = summary["n_overflow_rooms"]
    n_intervals   = summary["n_overflow_intervals"]
    needs_review  = summary["needs_review"]
    total_peak    = summary["total_peak_overflow"]

    if n_overflow == 0:
        st.success(
            f"✅ **No room overflow on {date_str}.** "
            "All rooms are within licensed capacity — no movement instructions needed."
        )
        return

    # Find busiest interval (most total overflow across all rooms)
    interval_totals: dict[str, int] = {}
    for rid, ivs in overflow_results.items():
        for istart, iv in ivs.items():
            interval_totals[istart] = interval_totals.get(istart, 0) + iv["overflow"]
    busiest_iv = max(interval_totals, key=interval_totals.get) if interval_totals else "—"

    unresolved = sum(
        1 for ivs in overflow_results.values()
        for iv in ivs.values()
        if iv["overflow"] > 0 and any(s["to_room_id"] == "" for s in iv["suggestions"])
    )

    col1, col2, col3 = st.columns(3)
    col1.metric("Overflow rooms",         n_overflow)
    col2.metric("Overflow intervals",     n_intervals)
    col3.metric("Unresolved overflows",   unresolved,
                delta="manual review required" if unresolved else None,
                delta_color="inverse" if unresolved else "off")

    if needs_review or unresolved > 0:
        st.warning(
            f"⚠️ **{unresolved} overflow(s) could not be automatically resolved.** "
            "Check age suitability manually — see 'Movement instruction' column below."
        )
    else:
        st.info(
            f"📋 **Overflow detected in {n_overflow} room(s) across {n_intervals} interval(s).** "
            f"Busiest interval: **{busiest_iv[:5]}** ({interval_totals.get(busiest_iv, 0)} overflow). "
            "Suggested movements shown below."
        )


# ── Movement instruction builder ──────────────────────────────────────────────

def _build_movement_instruction(
    suggestions: list[dict],
    room_id: str,
    room_map: dict,
    needs_review: bool,
) -> str:
    """
    Convert suggestions list into a human-readable movement instruction string.

    For the room sending children out:
        "Move 2 → Toddlers, 1 → Preschool"
        "Move 2 → Toddlers · Check age suitability manually."
        "Overflow unresolved — manual review"

    For rooms receiving children (received_overflow handled in the row):
        Returns "No movement" when no suggestions.
    """
    if not suggestions:
        return "No movement"

    parts = []
    has_unresolved = False
    has_age_check  = False

    for s in suggestions:
        count   = s.get("overflow_count", 0)
        to_room = s.get("to_room_name", "")
        compat  = s.get("age_compatible")
        to_rid  = s.get("to_room_id", "")

        if not to_rid:
            # Unresolved
            has_unresolved = True
            parts.append(f"⚠ {count} unresolved")
            continue

        arrow = f"Move {count} → {to_room}"
        if compat is None:
            arrow += " ⚠"
            has_age_check = True
        parts.append(arrow)

    instruction = " · ".join(parts) if parts else "No movement"

    if has_unresolved:
        instruction += " — manual review required"
    elif has_age_check:
        instruction += " · Check age suitability manually."

    return instruction


# ── Ratio status for a slot ────────────────────────────────────────────────────

def _calc_ratio_status(
    adjusted_count: int,
    rostered: int,
    min_staff_required: int,
    overflow: int,
) -> str:
    """Return 'ok', 'warning', 'breach', or 'no_data'."""
    if adjusted_count == 0:
        return "no_data"
    if rostered >= min_staff_required:
        return "ok"
    if rostered == min_staff_required - 1:
        return "warning"
    return "breach"


# ── Rostered staff map ─────────────────────────────────────────────────────────

def _build_rostered_staff_map(
    day_shifts: list[dict],
    rooms: list[dict],
) -> dict[str, dict[str, int]]:
    """
    Build {room_id: {interval_start: staff_count}} from roster shifts.

    A shift covers an interval if:
        shift.start_time <= interval_start < shift.end_time

    Interval starts are every 15 minutes from 06:00 to 20:00.
    """
    from utils.roster_engine import DAY_START_HOUR

    # Generate all 15-min interval starts as HH:MM:SS strings
    iv_starts = []
    for slot in range(TOTAL_SLOTS):
        total_mins = DAY_START_HOUR * 60 + slot * SLOT_MINUTES
        h, m = divmod(total_mins, 60)
        iv_starts.append(f"{h:02d}:{m:02d}:00")

    room_ids  = {r["id"] for r in rooms}
    result: dict[str, dict[str, int]] = {rid: {} for rid in room_ids}

    for shift in day_shifts:
        rid    = shift.get("room_id", "")
        s_time = (shift.get("start_time") or "")[:8]
        e_time = (shift.get("end_time")   or "")[:8]
        if not rid or rid not in result or not s_time or not e_time:
            continue

        for iv_start in iv_starts:
            if s_time <= iv_start < e_time:
                result[rid][iv_start] = result[rid].get(iv_start, 0) + 1

    return result


# ── Visual coverage grid ───────────────────────────────────────────────────────

def _render_coverage_grid(grid_data: dict, rooms: list, day: date):
    status_colours = {
        "ok":      "#86efac",
        "warning": "#fde68a",
        "breach":  "#fca5a5",
        "empty":   "#f1f5f9",
    }

    hour_markers = grid_data["hour_markers"]
    status_m     = grid_data["status_matrix"]
    SLOTS        = TOTAL_SLOTS

    room_names_html = "".join(
        f'<th style="padding:2px 4px;font-size:0.7rem;color:#475569;'
        f'text-align:center;max-width:60px;overflow:hidden;text-overflow:ellipsis;'
        f'white-space:nowrap;">{r.get("name","")[:8]}</th>'
        for r in rooms
    )

    time_cells_html = ('<td style="padding:2px 4px;font-size:0.62rem;color:#94a3b8;'
                       'white-space:nowrap;min-width:36px;">Time</td>')
    hour_set = {s for s, _ in hour_markers}
    for slot in range(SLOTS):
        if slot in hour_set:
            lbl = slot_label(slot)
            time_cells_html += (
                f'<td style="padding:1px;font-size:0.6rem;color:#64748b;'
                f'text-align:center;">{lbl}</td>'
            )
        else:
            time_cells_html += '<td></td>'

    rows_html = ""
    for room in rooms:
        rid      = room["id"]
        rcolour  = room.get("colour", "#3498DB")
        statuses = status_m.get(rid, ["empty"] * SLOTS)

        cells_html = ""
        for slot in range(SLOTS):
            st_val = statuses[slot]
            bg     = status_colours.get(st_val, "#f1f5f9")
            border = "1px solid #e2e8f0" if slot % 4 == 0 else "none"
            cells_html += (
                f'<td style="width:100%;background:{bg};height:12px;'
                f'border-left:{border};border-right:none;'
                f'border-top:none;border-bottom:none;padding:0;"></td>'
            )

        name_cell = (
            f'<td style="padding:2px 6px;font-size:0.75rem;font-weight:600;'
            f'color:#1e3a55;white-space:nowrap;min-width:70px;">'
            f'<span style="display:inline-block;width:8px;height:8px;border-radius:50%;'
            f'background:{rcolour};margin-right:4px;"></span>'
            f'{room.get("name","")[:10]}</td>'
        )
        rows_html += f'<tr>{name_cell}{cells_html}</tr>'

    html = (
        f'<div style="overflow-x:auto;margin-bottom:0.5rem;">'
        f'<table style="border-collapse:collapse;width:100%;table-layout:fixed;">'
        f'<thead><tr><th></th>{room_names_html}</tr>'
        f'<tr>{time_cells_html}</tr></thead>'
        f'<tbody>{rows_html}</tbody>'
        f'</table>'
        f'<div style="display:flex;gap:1.2rem;margin-top:0.4rem;flex-wrap:wrap;">'
        f'<span style="font-size:0.72rem;"><span style="display:inline-block;'
        f'width:12px;height:10px;background:#86efac;margin-right:3px;border-radius:2px;">'
        f'</span>Compliant</span>'
        f'<span style="font-size:0.72rem;"><span style="display:inline-block;'
        f'width:12px;height:10px;background:#fde68a;margin-right:3px;border-radius:2px;">'
        f'</span>At limit</span>'
        f'<span style="font-size:0.72rem;"><span style="display:inline-block;'
        f'width:12px;height:10px;background:#fca5a5;margin-right:3px;border-radius:2px;">'
        f'</span>Breach</span>'
        f'<span style="font-size:0.72rem;"><span style="display:inline-block;'
        f'width:12px;height:10px;background:#f1f5f9;margin-right:3px;border-radius:2px;">'
        f'</span>Empty</span>'
        f'<span style="font-size:0.72rem;color:#64748b;">Each cell = 15 min</span>'
        f'</div></div>'
    )
    st.markdown(html, unsafe_allow_html=True)


# ── Shift list ─────────────────────────────────────────────────────────────────

def _render_shift_list(day_shifts, rooms, conflicts, is_editable, centre_id):
    room_map      = {r["id"]: r for r in rooms}
    conflict_sids = {c.shift_id for c in conflicts if c.shift_id}

    for s in sorted(day_shifts, key=lambda x: (
        (x.get("rooms") or {}).get("name",""),
        x.get("start_time",""),
    )):
        sid       = s["id"]
        u         = s.get("users") or {}
        sname     = f"{u.get('first_name','')} {u.get('last_name','')}".strip()
        room      = s.get("rooms") or {}
        rname     = room.get("name","—")
        rcolour   = room.get("colour","#3498DB")
        start     = (s.get("start_time") or "")[:5]
        end       = (s.get("end_time")   or "")[:5]
        brk       = s.get("break_duration_minutes", 0)
        stype     = s.get("shift_type", "standard")
        scolour   = SHIFT_COLOURS.get(stype, "#3b82f6")
        has_error = sid in conflict_sids
        diploma   = "🎓" if s.get("has_diploma") else ""

        border_style = "2px solid #f43f5e" if has_error else f"1px solid #e4edf5"
        st.markdown(
            f'<div style="border:{border_style};border-radius:8px;'
            f'padding:0.55rem 0.9rem;margin-bottom:0.4rem;background:#fff;'
            f'display:flex;align-items:center;gap:0.6rem;">'
            f'<div style="width:4px;height:36px;background:{scolour};'
            f'border-radius:2px;flex-shrink:0;"></div>'
            f'<div style="flex:1;">'
            f'<div style="font-weight:600;font-size:0.9rem;color:#0d1f35;">'
            f'{sname} {diploma}</div>'
            f'<div style="font-size:0.78rem;color:#7a90a8;">'
            f'<span style="display:inline-block;width:7px;height:7px;border-radius:50%;'
            f'background:{rcolour};margin-right:4px;"></span>'
            f'{rname} · {start}–{end}'
            + (f' · {brk}m break' if brk else '')
            + f' · {stype.title()}'
            f'</div></div>'
            + (f'<span style="color:#f43f5e;font-size:0.9rem;">⚠️</span>'
               if has_error else '')
            + f'</div>',
            unsafe_allow_html=True,
        )

        if is_editable:
            ea, eb, _ = st.columns([1, 1, 5])
            if ea.button("✏️", key=f"edit_s_{sid}", help="Edit shift"):
                st.session_state[f"edit_shift_{sid}"] = True
                st.rerun()
            if eb.button("🗑️", key=f"del_s_{sid}", help="Delete shift"):
                st.session_state[f"confirm_del_{sid}"] = True
                st.rerun()

            if st.session_state.get(f"confirm_del_{sid}"):
                st.warning(f"Delete {sname}'s shift?")
                dy, dn = st.columns(2)
                if dy.button("Delete", key=f"dy_{sid}", type="primary",
                              use_container_width=True):
                    try:
                        delete_shift(sid)
                        toast_success("Shift deleted.")
                        st.session_state.pop(f"confirm_del_{sid}", None)
                        st.rerun()
                    except Exception as e:
                        toast_error(str(e))
                if dn.button("Cancel", key=f"dn_{sid}", use_container_width=True):
                    st.session_state.pop(f"confirm_del_{sid}", None)
                    st.rerun()

            if st.session_state.get(f"edit_shift_{sid}"):
                _render_edit_shift_form(s, rooms, sid)


# ── Add shift form ─────────────────────────────────────────────────────────────

def _render_add_shift_form(day, period_id, centre_id, rooms, staff_list,
                            templates, leave_map, avail_map):
    key = f"show_add_{day.isoformat()}"
    st.markdown(f"**Add shift — {day.strftime('%A %-d %B')}**")

    time_opts = generate_time_options(15, DAY_START_HOUR, 20)

    staff_on_leave = {uid for uid, dates in leave_map.items()
                      if day.isoformat() in dates}
    staff_opts = {}
    for s in staff_list:
        u   = s.get("users") or {}
        uid = u.get("id","")
        nm  = f"{u.get('first_name','')} {u.get('last_name','')}".strip()
        if uid and nm and uid not in staff_on_leave:
            staff_opts[uid] = nm

    room_opts = {r["id"]: r["name"] for r in rooms}
    tpl_opts  = {"": "— No template —"}
    tpl_opts.update({t["id"]: t["name"] for t in templates})

    with st.form(f"add_shift_form_{day.isoformat()}"):
        fc1, fc2 = st.columns(2)
        selected_uid = fc1.selectbox(
            "Staff member *",
            options=list(staff_opts.keys()),
            format_func=lambda x: staff_opts[x],
            key=f"as_staff_{day.isoformat()}",
        )
        selected_room = fc2.selectbox(
            "Room *",
            options=list(room_opts.keys()),
            format_func=lambda x: room_opts[x],
            key=f"as_room_{day.isoformat()}",
        )

        tc1, tc2, tc3 = st.columns(3)
        template_id   = tc1.selectbox(
            "Shift template",
            options=list(tpl_opts.keys()),
            format_func=lambda x: tpl_opts[x],
            key=f"as_tpl_{day.isoformat()}",
        )

        default_start = "07:00"
        default_end   = "15:00"
        default_brk   = 30
        if template_id:
            tpl = next((t for t in templates if t["id"] == template_id), {})
            default_start = (tpl.get("start_time") or "07:00")[:5]
            default_end   = (tpl.get("end_time")   or "15:00")[:5]
            default_brk   = tpl.get("break_duration_minutes", 30)

        start_idx = time_opts.index(default_start) if default_start in time_opts else 4
        end_idx   = time_opts.index(default_end)   if default_end   in time_opts else 16

        start_time = tc2.selectbox("Start *", time_opts, index=start_idx,
                                    key=f"as_st_{day.isoformat()}")
        end_time   = tc3.selectbox("End *",   time_opts, index=end_idx,
                                    key=f"as_et_{day.isoformat()}")

        bc1, bc2 = st.columns(2)
        brk_min  = bc1.number_input("Break (minutes)", min_value=0, max_value=120,
                                     value=default_brk, step=15,
                                     key=f"as_brk_{day.isoformat()}")
        stype    = classify_shift_type(start_time, end_time)
        bc2.markdown(f"**Shift type (auto)**  \n{stype.title()}")

        notes = st.text_input("Notes", key=f"as_notes_{day.isoformat()}")

        # Unpaid break opt-out — three-way override, shown when profile allows it
        allows_opt_out = False
        uid_prefs: dict[int, bool] = {}
        for s in staff_list:
            u_chk = s.get("users") or {}
            if u_chk.get("id") == selected_uid:
                for profile in (u_chk.get("staff_profiles") or []):
                    if profile.get("allows_unpaid_break_opt_out"):
                        allows_opt_out = True
                uid_prefs = break_prefs.get(selected_uid, {})

        override_value = "use_staff_default"
        if allows_opt_out:
            dow            = day.isoweekday() % 7
            default_opt_out = uid_prefs.get(dow, False)
            default_label  = (
                f"Use staff default — **{'Opted out' if default_opt_out else 'Not opted out'}** "
                f"on {day.strftime('%A')}s"
            )
            radio_opts     = [
                ("use_staff_default", default_label),
                ("opted_out",         "Opted out — remove unpaid break this shift"),
                ("not_opted_out",     "Not opted out — keep unpaid break this shift"),
            ]
            choice = st.radio(
                "Unpaid break opt-out",
                options=[k for k, _ in radio_opts],
                format_func=lambda x: next(v for k, v in radio_opts if k == x),
                index=0,
                key=f"as_override_{day.isoformat()}",
                horizontal=True,
            )
            override_value = choice
            if override_value in ("opted_out", "use_staff_default") and (
                override_value == "opted_out" or default_opt_out
            ):
                st.warning(
                    "⚠️ **Confirm this complies with the applicable award/enterprise "
                    "agreement and employee agreement.** Paid rest break is unchanged."
                )

        dow_db = day.isoweekday() % 7
        if selected_uid and selected_uid in avail_map:
            av = avail_map[selected_uid].get(dow_db, {})
            if not av.get("is_available", True):
                st.warning(f"⚠️ Staff marked unavailable on {day.strftime('%A')}s.")
            elif av.get("available_from") or av.get("available_until"):
                af = str(av.get("available_from",""))[:5]
                au = str(av.get("available_until",""))[:5]
                st.info(f"ℹ️ Available {af}–{au}")

        sc1, sc2 = st.columns(2)
        submitted = sc1.form_submit_button("💾 Add Shift", type="primary",
                                            use_container_width=True)
        cancelled = sc2.form_submit_button("Cancel", use_container_width=True)

    if cancelled:
        st.session_state.pop(key, None); st.rerun()

    if submitted:
        if start_time >= end_time:
            toast_error("End time must be after start time."); return
        try:
            create_shift(
                period_id=period_id,
                centre_id=centre_id,
                user_id=selected_uid,
                room_id=selected_room,
                shift_date=day.isoformat(),
                start_time=start_time + ":00",
                end_time=end_time + ":00",
                break_duration_minutes=int(brk_min),
                shift_type=stype,
                notes=notes,
                template_id=template_id or None,
                unpaid_break_opt_out_override=override_value,
            )
            toast_success(f"Shift added for {staff_opts[selected_uid]}.")
            st.session_state.pop(key, None)
            st.rerun()
        except Exception as e:
            toast_error(f"Could not add shift: {e}")


# ── Edit shift form ────────────────────────────────────────────────────────────

def _render_edit_shift_form(s: dict, rooms: list, sid: str):
    key       = f"edit_shift_{sid}"
    room_opts = {r["id"]: r["name"] for r in rooms}
    time_opts = generate_time_options(15, DAY_START_HOUR, 20)

    cur_room     = s.get("room_id","")
    cur_start    = (s.get("start_time") or "07:00")[:5]
    cur_end      = (s.get("end_time")   or "15:00")[:5]
    cur_brk      = s.get("break_duration_minutes", 0)
    cur_type     = s.get("shift_type","standard")
    cur_notes    = s.get("notes","") or ""
    cur_opted_out = bool(s.get("unpaid_break_opted_out", False))
    cur_override  = s.get("unpaid_break_opt_out_override", "use_staff_default") or "use_staff_default"

    # Determine if this staff member's profile allows opt-out
    u_data = s.get("users") or {}
    allows_opt_out = any(
        p.get("allows_unpaid_break_opt_out")
        for p in (u_data.get("staff_profiles") or [])
    )

    start_idx = time_opts.index(cur_start) if cur_start in time_opts else 4
    end_idx   = time_opts.index(cur_end)   if cur_end   in time_opts else 16
    room_keys = list(room_opts.keys())
    room_idx  = room_keys.index(cur_room) if cur_room in room_keys else 0

    with st.form(f"edit_form_{sid}"):
        ec1, ec2 = st.columns(2)
        new_room  = ec1.selectbox("Room", options=room_keys, index=room_idx,
                                   format_func=lambda x: room_opts[x], key=f"er_{sid}")
        et1, et2 = st.columns(2)
        new_start = et1.selectbox("Start", time_opts, index=start_idx, key=f"est_{sid}")
        new_end   = et2.selectbox("End",   time_opts, index=end_idx,   key=f"eet_{sid}")
        new_brk   = st.number_input("Break (min)", min_value=0, max_value=120,
                                     value=int(cur_brk), step=15, key=f"ebr_{sid}")
        type_opts = ["standard","opening","closing","split","overtime","on_call"]
        type_idx  = type_opts.index(cur_type) if cur_type in type_opts else 0
        new_type  = st.selectbox("Shift type", type_opts, index=type_idx,
                                  format_func=lambda x: x.title(), key=f"ety_{sid}")
        new_notes = st.text_input("Notes", value=cur_notes, key=f"en_{sid}")

        new_override = "use_staff_default"
        if allows_opt_out:
            # Show what the staff default is for this shift's weekday
            uid_prefs  = break_prefs.get(s.get("user_id",""), {}) if "break_prefs" in dir() else {}
            shift_date = s.get("shift_date","")
            try:
                from datetime import date as _date
                dow_edit = _date.fromisoformat(shift_date[:10]).isoweekday() % 7
            except Exception:
                dow_edit = -1
            default_opt_out = uid_prefs.get(dow_edit, False) if dow_edit >= 0 else False
            default_label = (
                f"Use staff default — **{'Opted out' if default_opt_out else 'Not opted out'}**"
            )
            radio_opts = [
                ("use_staff_default", default_label),
                ("opted_out",         "Opted out — remove unpaid break this shift"),
                ("not_opted_out",     "Not opted out — keep unpaid break this shift"),
            ]
            cur_idx = next((i for i, (k, _) in enumerate(radio_opts) if k == cur_override), 0)
            new_override = st.radio(
                "Unpaid break opt-out",
                options=[k for k, _ in radio_opts],
                format_func=lambda x: next(v for k, v in radio_opts if k == x),
                index=cur_idx,
                key=f"eo_{sid}",
                horizontal=True,
            )
            if new_override in ("opted_out",) or (new_override == "use_staff_default" and default_opt_out):
                st.warning(
                    "⚠️ **Confirm this complies with the applicable award/enterprise "
                    "agreement and employee agreement.** Paid rest break is unchanged."
                )

        sc1, sc2 = st.columns(2)
        saved     = sc1.form_submit_button("💾 Save", type="primary", use_container_width=True)
        cancelled = sc2.form_submit_button("Cancel", use_container_width=True)

    if cancelled:
        st.session_state.pop(key, None); st.rerun()
    if saved:
        try:
            update_shift(sid, new_room, new_start+":00", new_end+":00",
                         int(new_brk), new_type, new_notes,
                         unpaid_break_opt_out_override=new_override)
            toast_success("Shift updated.")
            st.session_state.pop(key, None)
            st.rerun()
        except Exception as e:
            toast_error(str(e))


# ── Publish panel ──────────────────────────────────────────────────────────────

def _render_publish_panel(period_id, centre_id, shifts, rooms, children,
                           leave_map, avail_map, days):
    st.markdown("---")
    st.markdown("### Pre-Publish Checklist")

    total_errors   = 0
    total_warnings = 0
    for day in days:
        day_shifts = [s for s in shifts if s.get("shift_date") == day.isoformat()]
        conflicts  = validate_roster(day_shifts, rooms, children,
                                     leave_map, avail_map, day)
        total_errors   += sum(1 for c in conflicts if c.severity == "error")
        total_warnings += sum(1 for c in conflicts if c.severity == "warning")

    checks = [
        ("Ratio compliance across all days",
         total_errors == 0, f"{total_errors} error(s) found"),
        ("Warnings reviewed",
         total_warnings == 0, f"{total_warnings} warning(s)"),
        ("All rooms have at least one shift",
         all(any(s.get("room_id") == r["id"] for s in shifts) for r in rooms),
         "Some rooms have no shifts"),
    ]

    for label, passed, fail_note in checks:
        icon   = "✅" if passed else "❌"
        colour = "#166534" if passed else "#991b1b"
        bg     = "#f0fdf4" if passed else "#fee2e2"
        st.markdown(
            f'<div style="background:{bg};border-radius:7px;padding:0.5rem 0.9rem;'
            f'margin-bottom:0.3rem;color:{colour};font-size:0.88rem;">'
            f'{icon} {label}'
            + ('' if passed else f' — <em>{fail_note}</em>')
            + '</div>',
            unsafe_allow_html=True,
        )

    can_publish = total_errors == 0
    if not can_publish:
        st.error("❌ Resolve all errors before publishing.")
    else:
        st.success("✅ Roster is ready to publish.")

    pb1, pb2 = st.columns(2)
    if can_publish:
        if pb1.button("✅  Publish Now", type="primary", use_container_width=True,
                       key="do_publish"):
            try:
                publish_roster_period(period_id, "system")
                toast_success("Roster published. Staff will be notified.")
                st.session_state.pop("show_publish_panel", None)
                st.rerun()
            except Exception as e:
                toast_error(str(e))
    if pb2.button("Cancel", use_container_width=True, key="cancel_publish"):
        st.session_state.pop("show_publish_panel", None)
        st.rerun()

    st.markdown("---")
