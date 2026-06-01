# pages/roster_builder.py — The main visual roster builder with 15-min grid
import streamlit as st
from datetime import date, timedelta, datetime

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
from utils.helpers import toast_success, toast_error, toast_warn, fmt_date


# Shift type colours for the grid display
SHIFT_COLOURS = {
    "opening":  "#0ea5e9",
    "closing":  "#8b5cf6",
    "standard": "#3b82f6",
    "split":    "#f59e0b",
    "overtime": "#ef4444",
    "on_call":  "#6b7280",
}


def render():
    period_id = st.session_state.get("roster_period_id")
    if not period_id:
        st.warning("No roster period selected.")
        if st.button("← Rosters"):
            st.session_state.page = "roster_list"; st.rerun()
        return

    # ── Back + load period ────────────────────────────────────────────
    bc, _ = st.columns([1, 10])
    with bc:
        st.markdown('<div class="back-btn">', unsafe_allow_html=True)
        if st.button("← Rosters", key="rb_back"):
            st.session_state.page = "roster_list"; st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    with st.spinner("Loading roster…"):
        try:
            period   = fetch_roster_period_by_id(period_id)
        except Exception as e:
            toast_error(f"Could not load roster: {e}"); return

    if not period:
        toast_error("Roster period not found.")
        st.session_state.page = "roster_list"; st.rerun(); return

    centre_id  = period["centre_id"]
    start_d    = date.fromisoformat(period["start_date"])
    end_d      = date.fromisoformat(period["end_date"])
    status     = period.get("status", "draft")
    is_editable = status == "draft"

    # Day selector (tab per day)
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

    # Status badge + action buttons
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

    # ── Load all data ─────────────────────────────────────────────────
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
            _render_day_view(
                day, day_shifts, rooms, children, templates, staff_list,
                leave_map, avail_map, period_id, centre_id, is_editable,
            )


# ── Week compliance strip ──────────────────────────────────────────────────────

def _render_week_compliance_strip(shifts, rooms, children, leave_map, avail_map, days):
    """One compliance badge per day across the top."""
    cols = st.columns(len(days))
    for col, day in zip(cols, days):
        day_shifts  = [s for s in shifts if s.get("shift_date") == day.isoformat()]
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

        conflicts = validate_roster(
            day_shifts, rooms, children, leave_map, avail_map, day,
        )
        errors   = sum(1 for c in conflicts if c.severity == "error")
        warnings = sum(1 for c in conflicts if c.severity == "warning")

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


# ── Day view (the core of the builder) ────────────────────────────────────────

def _render_day_view(day, day_shifts, rooms, children, templates, staff_list,
                     leave_map, avail_map, period_id, centre_id, is_editable):
    """Render the full day: visual grid + shift list + add-shift form."""

    # Run validation for this day
    conflicts = validate_roster(day_shifts, rooms, children, leave_map, avail_map, day)
    errors    = [c for c in conflicts if c.severity == "error"]
    warnings  = [c for c in conflicts if c.severity == "warning"]

    # ── Conflict banners ──────────────────────────────────────────────
    if errors:
        st.error(
            f"❌ **{len(errors)} error(s)** on {day.strftime('%A %-d %b')} — "
            f"roster cannot be published until resolved."
        )
    if warnings:
        st.warning(f"⚠️ {len(warnings)} warning(s) — review before publishing.")

    # ── Visual coverage grid ──────────────────────────────────────────
    grid_data = build_grid_data(day_shifts, rooms, children, day.isoweekday())
    _render_coverage_grid(grid_data, rooms, day)

    # ── Staffing gaps ─────────────────────────────────────────────────
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

    # ── Shift list for this day ───────────────────────────────────────
    if day_shifts:
        st.markdown(f"**{len(day_shifts)} shift(s) — {day.strftime('%A %-d %B')}**")
        _render_shift_list(day_shifts, rooms, conflicts, is_editable, centre_id)
    else:
        st.caption(f"No shifts on {day.strftime('%A %-d %B')}.")

    # ── Add shift form ────────────────────────────────────────────────
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


# ── Visual coverage grid ───────────────────────────────────────────────────────

def _render_coverage_grid(grid_data: dict, rooms: list, day: date):
    """
    Render the slot-by-slot heat-map grid.
    Each cell is coloured by compliance status.
    Hour gridlines every 4 slots.
    """
    status_colours = {
        "ok":      "#86efac",   # green
        "warning": "#fde68a",   # amber
        "breach":  "#fca5a5",   # red
        "empty":   "#f1f5f9",   # grey
    }

    hour_markers = grid_data["hour_markers"]   # [(slot, label), ...]
    status_m     = grid_data["status_matrix"]

    # Header row: hour labels
    # We only label every 4th slot (on the hour)
    n_cols   = len(rooms) + 1   # +1 for the time axis
    SLOTS    = TOTAL_SLOTS

    # Build one compact HTML table for the whole grid
    # Time axis column + one column per room
    room_names_html = "".join(
        f'<th style="padding:2px 4px;font-size:0.7rem;color:#475569;'
        f'text-align:center;max-width:60px;overflow:hidden;text-overflow:ellipsis;'
        f'white-space:nowrap;">{r.get("name","")[:8]}</th>'
        for r in rooms
    )

    # Time labels row
    time_cells_html = '<td style="padding:2px 4px;font-size:0.62rem;color:#94a3b8;'
    time_cells_html += 'white-space:nowrap;min-width:36px;">Time</td>'
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
        rid        = room["id"]
        rcolour    = room.get("colour", "#3498DB")
        statuses   = status_m.get(rid, ["empty"] * SLOTS)

        cells_html = ""
        for slot in range(SLOTS):
            st_val  = statuses[slot]
            bg      = status_colours.get(st_val, "#f1f5f9")
            border  = "1px solid #e2e8f0" if slot % 4 == 0 else "none"
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
            + (f'<span style="color:#f43f5e;font-size:0.9rem;" title="Has conflicts">⚠️</span>'
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

    # Build staff options (filter out those on leave today)
    staff_on_leave = {uid for uid, dates in leave_map.items()
                      if day.isoformat() in dates}
    staff_opts = {}
    staff_uid_map = {}
    for s in staff_list:
        u   = s.get("users") or {}
        uid = u.get("id","")
        nm  = f"{u.get('first_name','')} {u.get('last_name','')}".strip()
        if uid and nm and uid not in staff_on_leave:
            staff_opts[uid]   = nm
            staff_uid_map[uid] = s

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

        # Default times
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
        brk_min   = bc1.number_input("Break (minutes)", min_value=0, max_value=120,
                                      value=default_brk, step=15,
                                      key=f"as_brk_{day.isoformat()}")
        stype     = classify_shift_type(start_time, end_time)
        bc2.markdown(f"**Shift type (auto)**  \n{stype.title()}")

        notes = st.text_input("Notes", key=f"as_notes_{day.isoformat()}")

        # Availability indicator
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
            period = fetch_roster_period_by_id(period_id)
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

    cur_room  = s.get("room_id","")
    cur_start = (s.get("start_time") or "07:00")[:5]
    cur_end   = (s.get("end_time")   or "15:00")[:5]
    cur_brk   = s.get("break_duration_minutes", 0)
    cur_type  = s.get("shift_type","standard")
    cur_notes = s.get("notes","") or ""

    start_idx = time_opts.index(cur_start) if cur_start in time_opts else 4
    end_idx   = time_opts.index(cur_end)   if cur_end   in time_opts else 16
    room_keys = list(room_opts.keys())
    room_idx  = room_keys.index(cur_room) if cur_room in room_keys else 0

    with st.form(f"edit_form_{sid}"):
        ec1, ec2 = st.columns(2)
        new_room  = ec1.selectbox("Room", options=room_keys,
                                   index=room_idx, format_func=lambda x: room_opts[x],
                                   key=f"er_{sid}")
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

        sc1, sc2 = st.columns(2)
        saved    = sc1.form_submit_button("💾 Save", type="primary", use_container_width=True)
        cancelled= sc2.form_submit_button("Cancel", use_container_width=True)

    if cancelled:
        st.session_state.pop(key, None); st.rerun()
    if saved:
        try:
            update_shift(sid, new_room, new_start+":00", new_end+":00",
                         int(new_brk), new_type, new_notes)
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
        icon = "✅" if passed else "❌"
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
