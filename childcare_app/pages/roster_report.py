# pages/roster_report.py — Roster compliance report with all 6 requirement areas
import streamlit as st
import pandas as pd
from datetime import date, timedelta

from utils.roster_queries import (
    fetch_roster_periods, fetch_shifts_for_period,
    fetch_approved_leave_for_period, fetch_availability_map,
    enrich_shifts_with_qual_flags, fetch_shift_templates,
)
from utils.roster_engine import (
    validate_roster, find_staffing_gaps, slot_label,
    TOTAL_SLOTS, SLOT_MINUTES,
)
from utils.room_queries import fetch_rooms, fetch_children_by_centre
from utils.staff_queries import fetch_all_staff, fetch_centres
from utils.helpers import toast_error, fmt_date
from utils.break_engine import (
    shift_duration_minutes, calc_break_entitlement, fmt_duration,
)


def render():
    bc, hc = st.columns([1, 9])
    with bc:
        st.markdown('<div class="back-btn">', unsafe_allow_html=True)
        if st.button("← Rosters", key="rr_back"):
            st.session_state.page = "roster_list"; st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)
    hc.title("Roster Compliance Report")
    hc.markdown(
        '<p class="page-sub">15-min interval analysis · ratio compliance · '
        'qualification coverage · break entitlements · room allocation</p>',
        unsafe_allow_html=True,
    )

    # ── Centre + period selector ──────────────────────────────────────
    centres = fetch_centres()
    if not centres:
        st.warning("No centres found."); return
    centre_opts = {c["id"]: c["name"] for c in centres}
    saved = st.session_state.get("roster_centre_id") or centres[0]["id"]
    fc1, fc2 = st.columns(2)
    centre_id = fc1.selectbox(
        "Centre", options=list(centre_opts.keys()),
        format_func=lambda x: centre_opts[x],
        index=list(centre_opts.keys()).index(saved) if saved in centre_opts else 0,
        key="rr_centre",
    )
    st.session_state.roster_centre_id = centre_id

    try:
        periods = fetch_roster_periods(centre_id, limit=10)
    except Exception as e:
        toast_error(str(e)); return

    if not periods:
        st.info("No roster periods found."); return

    period_opts = {p["id"]: f"{fmt_date(p['start_date'])} – {fmt_date(p['end_date'])} ({p['status']})"
                  for p in periods}
    period_id = fc2.selectbox(
        "Roster period", options=list(period_opts.keys()),
        format_func=lambda x: period_opts[x],
        key="rr_period",
    )

    if not st.button("📄  Generate Report", type="primary"):
        st.info("Select a roster period and click **Generate Report**.")
        return

    # ── Load everything ───────────────────────────────────────────────
    period = next(p for p in periods if p["id"] == period_id)
    start_d = date.fromisoformat(period["start_date"])
    end_d   = date.fromisoformat(period["end_date"])

    with st.spinner("Running 15-minute interval analysis…"):
        try:
            raw_shifts  = fetch_shifts_for_period(period_id)
            all_shifts  = enrich_shifts_with_qual_flags(raw_shifts)
            rooms       = fetch_rooms(centre_id)
            children    = fetch_children_by_centre(centre_id)
            staff_list  = fetch_all_staff()
            leave_map   = fetch_approved_leave_for_period(
                centre_id, period["start_date"], period["end_date"])
            avail_map   = fetch_availability_map(centre_id)
        except Exception as e:
            toast_error(f"Could not load data: {e}"); return

    # Run validation for each day
    all_days = []
    d = start_d
    while d <= end_d:
        all_days.append(d)
        d += timedelta(days=1)

    all_conflicts = []
    day_error_counts = {}
    for day in all_days:
        day_shifts = [s for s in all_shifts if s.get("shift_date") == day.isoformat()]
        conflicts  = validate_roster(day_shifts, rooms, children,
                                     leave_map, avail_map, day)
        all_conflicts.extend(conflicts)
        day_error_counts[day.isoformat()] = {
            "errors":   sum(1 for c in conflicts if c.severity == "error"),
            "warnings": sum(1 for c in conflicts if c.severity == "warning"),
        }

    total_errors   = sum(v["errors"]   for v in day_error_counts.values())
    total_warnings = sum(v["warnings"] for v in day_error_counts.values())

    # ── Report header ─────────────────────────────────────────────────
    st.markdown("---")
    centre_name = centre_opts[centre_id].split(" (")[0] if " (" in centre_opts.get(centre_id,"") else centre_opts.get(centre_id,"")
    st.markdown(
        f'<div style="background:#0d1f35;border-radius:12px;padding:1.4rem 2rem;margin-bottom:1.2rem;">'
        f'<div style="font-family:DM Serif Display,serif;font-size:1.5rem;color:#fff;">Roster Compliance Report</div>'
        f'<div style="color:#7a90a8;font-size:0.88rem;margin-top:0.3rem;">'
        f'{fmt_date(start_d)} to {fmt_date(end_d)} · '
        f'Generated {date.today().strftime("%-d %B %Y")}</div></div>',
        unsafe_allow_html=True,
    )

    # ── Executive summary ─────────────────────────────────────────────
    if total_errors == 0 and total_warnings == 0:
        rating_icon, rating_label = "🏆", "Fully Compliant"
        rating_bg, rating_tc = "#f0fdf4", "#166534"
    elif total_errors == 0:
        rating_icon, rating_label = "⚠️", "Minor Issues"
        rating_bg, rating_tc = "#fef3c7", "#92400e"
    else:
        rating_icon, rating_label = "❌", "Action Required"
        rating_bg, rating_tc = "#fee2e2", "#991b1b"

    st.markdown(
        f'<div style="background:{rating_bg};border-radius:10px;'
        f'padding:1.1rem 1.5rem;margin-bottom:1rem;display:flex;gap:1rem;align-items:center;">'
        f'<span style="font-size:2.5rem;">{rating_icon}</span>'
        f'<div><div style="font-family:DM Serif Display,serif;font-size:1.3rem;color:{rating_tc};">'
        f'{rating_label}</div>'
        f'<div style="font-size:0.88rem;color:{rating_tc};margin-top:0.2rem;">'
        f'{total_errors} error(s) and {total_warnings} warning(s) across {len(all_days)} days'
        f'</div></div></div>',
        unsafe_allow_html=True,
    )

    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("Period", f"{len(all_days)}d")
    m2.metric("Total Shifts",  len(all_shifts))
    m3.metric("Rooms",         len(rooms))
    m4.metric("Staff",         len({s.get("user_id") for s in all_shifts}))
    m5.metric("❌ Errors",     total_errors)
    m6.metric("⚠️ Warnings",   total_warnings)

    st.markdown("---")

    # ══════════════════════════════════════════════════════════════════
    # SECTION 1: DAY-BY-DAY COMPLIANCE
    # ══════════════════════════════════════════════════════════════════
    st.markdown("### 1 · Day-by-Day Compliance")
    for day in all_days:
        dc = day_error_counts[day.isoformat()]
        e, w = dc["errors"], dc["warnings"]
        if e > 0:    bg, tc, lbl = "#fee2e2","#991b1b", f"❌ {e} error(s)"
        elif w > 0:  bg, tc, lbl = "#fef3c7","#92400e", f"⚠️ {w} warning(s)"
        else:        bg, tc, lbl = "#f0fdf4","#166534", "✅ Compliant"
        st.markdown(
            f'<div style="display:flex;justify-content:space-between;'
            f'padding:0.4rem 0.8rem;border-bottom:1px solid #f0f4f8;">'
            f'<span style="font-size:0.88rem;color:#0d1f35;">{day.strftime("%A %-d %B")}</span>'
            f'<span style="background:{bg};color:{tc};padding:1px 9px;'
            f'border-radius:99px;font-size:0.78rem;font-weight:600;">{lbl}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # ══════════════════════════════════════════════════════════════════
    # SECTION 2: RATIO COMPLIANCE (15-MINUTE INTERVALS)
    # ══════════════════════════════════════════════════════════════════
    st.markdown("---")
    st.markdown("### 2 · Ratio Compliance — 15-Minute Interval Analysis")
    st.caption(
        "Each row = one staffing gap detected. Time windows show where "
        "children are expected but staff coverage is insufficient."
    )

    ratio_conflicts = [c for c in all_conflicts if c.conflict_type == "ratio_breach"]
    if not ratio_conflicts:
        st.success("✅ No ratio breaches across the entire roster period.")
    else:
        gap_rows = []
        for c in ratio_conflicts:
            gap_rows.append({
                "Day":       date.fromisoformat(c.shift_date).strftime("%a %-d %b"),
                "Room":      next((r["name"] for r in rooms if r["id"] == c.room_id),"—"),
                "From":      slot_label(c.slot_start),
                "To":        slot_label(c.slot_end),
                "Duration":  f"{(c.slot_end - c.slot_start) * SLOT_MINUTES} min",
                "Issue":     c.message[:80],
                "Fix":       c.suggestion[:60],
            })
        st.dataframe(pd.DataFrame(gap_rows), use_container_width=True, hide_index=True)

    # ══════════════════════════════════════════════════════════════════
    # SECTION 3: QUALIFICATION COVERAGE
    # ══════════════════════════════════════════════════════════════════
    st.markdown("---")
    st.markdown("### 3 · Qualification Coverage")

    diploma_conflicts = [c for c in all_conflicts if c.conflict_type == "diploma_required"]
    qual_conflicts    = [c for c in all_conflicts if c.conflict_type in ("qual_missing",)]

    col_a, col_b = st.columns(2)
    col_a.metric("Diploma coverage gaps", len(diploma_conflicts))
    col_b.metric("Staff with diploma",
                  sum(1 for s in all_shifts if s.get("has_diploma")))

    if diploma_conflicts:
        for c in diploma_conflicts[:10]:
            st.warning(f"**{date.fromisoformat(c.shift_date).strftime('%a %-d %b')}** — {c.message}")
    else:
        st.success("✅ Diploma-qualified educator present whenever required.")

    # Per-staff qualification summary
    st.markdown("**Staff qualification summary:**")
    sq_rows = []
    seen_users = set()
    for s in all_shifts:
        uid = s.get("user_id","")
        if uid in seen_users:
            continue
        seen_users.add(uid)
        u = s.get("users") or {}
        name = f"{u.get('first_name','')} {u.get('last_name','')}".strip()
        sq_rows.append({
            "Staff":   name,
            "Diploma": "✅ Yes" if s.get("has_diploma") else "❌ No",
            "Counts towards ratio": "✅ Yes" if s.get("counts_ratio") else "⚠️ No",
        })
    if sq_rows:
        st.dataframe(pd.DataFrame(sq_rows), use_container_width=True, hide_index=True)

    # ══════════════════════════════════════════════════════════════════
    # SECTION 4: OPENING & CLOSING COVERAGE
    # ══════════════════════════════════════════════════════════════════
    st.markdown("---")
    st.markdown("### 4 · Opening & Closing Shift Coverage")

    opening_shifts = [s for s in all_shifts if s.get("shift_type") == "opening"]
    closing_shifts = [s for s in all_shifts if s.get("shift_type") == "closing"]

    oc1, oc2, oc3 = st.columns(3)
    oc1.metric("Opening shifts", len(opening_shifts))
    oc2.metric("Closing shifts", len(closing_shifts))
    oc3.metric("Standard shifts",
               len([s for s in all_shifts if s.get("shift_type") == "standard"]))

    # Days without opening or closing coverage
    missing_open  = []
    missing_close = []
    for day in all_days:
        if day.isoweekday() > 5:    # Skip weekends
            continue
        day_shifts = [s for s in all_shifts if s.get("shift_date") == day.isoformat()]
        if not any(s.get("shift_type") == "opening" for s in day_shifts) and day_shifts:
            missing_open.append(day.strftime("%a %-d %b"))
        if not any(s.get("shift_type") == "closing" for s in day_shifts) and day_shifts:
            missing_close.append(day.strftime("%a %-d %b"))

    if missing_open:
        st.warning(f"⚠️ No opening shift on: {', '.join(missing_open)}")
    if missing_close:
        st.warning(f"⚠️ No closing shift on: {', '.join(missing_close)}")
    if not missing_open and not missing_close:
        st.success("✅ Opening and closing shifts covered every operating day.")

    # ══════════════════════════════════════════════════════════════════
    # SECTION 5: ROOM ALLOCATION
    # ══════════════════════════════════════════════════════════════════
    st.markdown("---")
    st.markdown("### 5 · Room Allocation")

    room_shift_counts = {}
    for r in rooms:
        rid = r["id"]
        n   = sum(1 for s in all_shifts if s.get("room_id") == rid)
        room_shift_counts[rid] = n

    unassigned = sum(1 for s in all_shifts if not s.get("room_id"))

    ra_rows = [
        {
            "Room":          r.get("name",""),
            "Age Range":     f"{r.get('age_min_months',0)}–{r.get('age_max_months',72)}m",
            "Capacity":      r.get("licensed_capacity",0),
            "Required Ratio": f'1:{r.get("required_ratio_children",4)}',
            "Diploma Req.":  "Yes" if r.get("requires_diploma") else "No",
            "Shifts assigned": room_shift_counts.get(r["id"],0),
        }
        for r in rooms
    ]
    if ra_rows:
        st.dataframe(pd.DataFrame(ra_rows), use_container_width=True, hide_index=True)

    if unassigned > 0:
        st.warning(f"⚠️ {unassigned} shift(s) have no room assigned.")
    else:
        st.success("✅ All shifts are assigned to rooms.")

    # ══════════════════════════════════════════════════════════════════
    # SECTION 6: BREAK ENTITLEMENT COMPLIANCE
    # ══════════════════════════════════════════════════════════════════
    st.markdown("---")
    st.markdown("### 6 · Break Entitlement Compliance")
    st.caption(
        "Based on Australian Children's Services Award 2010. "
        "Break entitlements calculated per shift length."
    )

    break_rows = []
    for s in all_shifts:
        u         = s.get("users") or {}
        name      = f"{u.get('first_name','')} {u.get('last_name','')}".strip()
        start_str = (s.get("start_time") or "")[:5]
        end_str   = (s.get("end_time")   or "")[:5]
        dur_m     = shift_duration_minutes(start_str, end_str)
        ent       = calc_break_entitlement(dur_m)
        sched_brk = s.get("break_duration_minutes", 0) or 0
        gap       = max(0, ent["total_min"] - sched_brk)
        break_rows.append({
            "Staff":       name,
            "Date":        date.fromisoformat(s["shift_date"]).strftime("%-d %b") if s.get("shift_date") else "",
            "Shift":       f"{start_str}–{end_str}",
            "Shift len":   fmt_duration(dur_m),
            "Entitlement": ent["summary"],
            "Scheduled":   f"{sched_brk} min",
            "Shortfall":   f"⚠️ {gap} min" if gap > 0 else "✅ OK",
        })

    if break_rows:
        df = pd.DataFrame(break_rows)
        n_short = sum(1 for r in break_rows if r["Shortfall"].startswith("⚠️"))
        if n_short > 0:
            st.warning(f"⚠️ {n_short} shift(s) have a break shortfall.")
        else:
            st.success("✅ All scheduled breaks meet entitlement requirements.")
        st.dataframe(df, use_container_width=True, hide_index=True)

    # ══════════════════════════════════════════════════════════════════
    # SECTION 7: OTHER CONFLICTS
    # ══════════════════════════════════════════════════════════════════
    other_conflicts = [
        c for c in all_conflicts
        if c.conflict_type not in ("ratio_breach","diploma_required")
    ]
    if other_conflicts:
        st.markdown("---")
        st.markdown("### 7 · Other Conflicts")
        for c in other_conflicts[:20]:
            label = c.conflict_type.replace("_"," ").title()
            icon  = "❌" if c.severity == "error" else "⚠️"
            day   = date.fromisoformat(c.shift_date).strftime("%a %-d %b")
            st.markdown(
                f'<div style="padding:0.4rem 0.8rem;border-bottom:1px solid #f0f4f8;">'
                f'<strong>{icon} {label}</strong> · {day}  \n{c.message}'
                f'<br><span style="font-size:0.8rem;color:#7a90a8;">→ {c.suggestion}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

    # ── Export ────────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("### Export")

    all_conflict_rows = []
    for c in all_conflicts:
        all_conflict_rows.append({
            "Day":      date.fromisoformat(c.shift_date).strftime("%a %-d %b"),
            "Type":     c.conflict_type.replace("_"," ").title(),
            "Severity": c.severity.title(),
            "From":     slot_label(c.slot_start),
            "To":       slot_label(c.slot_end),
            "Message":  c.message,
            "Suggestion": c.suggestion,
        })

    if all_conflict_rows:
        csv = pd.DataFrame(all_conflict_rows).to_csv(index=False)
        st.download_button(
            "⬇️  Export Conflicts CSV",
            data=csv,
            file_name=f"roster_conflicts_{period['start_date']}_{period['end_date']}.csv",
            mime="text/csv",
        )
    else:
        st.success("✅ No conflicts to export — fully compliant roster!")
