# pages/break_history.py  —  Screen 40: Break History
# Historical break log with award compliance analysis,
# per-staff entitlement summary, and export.

import streamlit as st
import pandas as pd
from datetime import date, timedelta

from utils.break_queries import fetch_break_history
from utils.break_engine import (
    BREAK_STATUS_CONFIG, BREAK_TYPE_LABELS,
    shift_duration_minutes, calc_break_entitlement, compliance_summary,
    fmt_duration, fmt_time,
)
from utils.staff_queries import fetch_all_staff, fetch_centres
from utils.helpers import toast_error, fmt_date


def render():
    # ── Header ────────────────────────────────────────────────────────
    bc, hc, btn_c = st.columns([1, 4, 1])
    with bc:
        st.markdown('<div class="back-btn">', unsafe_allow_html=True)
        if st.button("← Schedule", key="bh_back"):
            st.session_state.page = "break_schedule"
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)
    with hc:
        st.title("Break History")
        st.markdown(
            '<p class="page-sub">'
            'Award entitlement compliance · Fair Work audit trail</p>',
            unsafe_allow_html=True,
        )

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
        key="bh_centre_sel",
    )
    st.session_state.break_centre_id = centre_id

    # ── Filters ───────────────────────────────────────────────────────
    st.markdown("---")
    fc1, fc2, fc3, fc4 = st.columns([1.5, 1.5, 1.5, 1.5])
    from_d = fc1.date_input(
        "From", value=date.today() - timedelta(days=14),
        key="bh_from", format="DD/MM/YYYY",
    )
    to_d = fc2.date_input(
        "To", value=date.today(),
        key="bh_to", format="DD/MM/YYYY",
    )
    status_filter = fc3.selectbox(
        "Status",
        options=["all", "scheduled", "completed", "missed", "in_progress", "rescheduled"],
        format_func=lambda x: (
            "All Statuses" if x == "all"
            else BREAK_STATUS_CONFIG.get(x, {}).get("label", x.title())
        ),
        key="bh_status", label_visibility="collapsed",
    )
    type_filter = fc4.selectbox(
        "Break type",
        options=["all", "meal", "rest"],
        format_func=lambda x: "All Types" if x == "all" else BREAK_TYPE_LABELS.get(x, x.title()),
        key="bh_type", label_visibility="collapsed",
    )

    # Load staff for search
    try:
        all_staff = fetch_all_staff()
        staff_opts = {"": "All Staff"}
        staff_opts.update({
            (s.get("users") or {}).get("id", ""): (
                f"{(s.get('users') or {}).get('first_name','')} "
                f"{(s.get('users') or {}).get('last_name','')}".strip()
            )
            for s in all_staff
        })
    except Exception:
        all_staff, staff_opts = [], {"": "All Staff"}

    search_uid = fc1.selectbox(
        "Staff member",
        options=list(staff_opts.keys()),
        format_func=lambda x: staff_opts[x],
        key="bh_staff", label_visibility="collapsed",
    ) if len(staff_opts) > 1 else ""

    # ── Load data ─────────────────────────────────────────────────────
    with st.spinner("Loading history…"):
        try:
            records = fetch_break_history(
                centre_id=centre_id,
                from_date=from_d.isoformat(),
                to_date=to_d.isoformat(),
                user_id=search_uid or None,
                status_filter=status_filter if status_filter != "all" else None,
            )
        except Exception as e:
            toast_error(f"Could not load history: {e}")
            return

    # Apply type filter client-side
    if type_filter != "all":
        records = [r for r in records if r.get("break_type") == type_filter]

    # ── Summary metrics ───────────────────────────────────────────────
    total       = len(records)
    completed   = sum(1 for r in records if r.get("status") == "completed")
    missed      = sum(1 for r in records if r.get("status") == "missed")
    total_taken = sum(
        (r.get("actual_duration_minutes") or r.get("planned_duration_minutes") or 0)
        for r in records if r.get("status") == "completed"
    )

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total Records",    total)
    m2.metric("Completed",        completed)
    m3.metric("Missed",           missed,
              delta="needs review" if missed else None,
              delta_color="inverse" if missed else "off")
    m4.metric("Total Break Time", fmt_duration(total_taken))

    # ── Export ────────────────────────────────────────────────────────
    if records:
        export_rows = []
        for r in records:
            u  = r.get("users") or {}
            rs = r.get("roster_shifts") or {}
            rm = rs.get("rooms") or {} if isinstance(rs, dict) else {}
            export_rows.append({
                "Date":            r.get("break_date", ""),
                "Staff":           f"{u.get('first_name','')} {u.get('last_name','')}".strip(),
                "Shift":           (
                    f"{rs.get('start_time','')[:5]}–{rs.get('end_time','')[:5]}"
                    if isinstance(rs, dict) and rs.get("start_time") else ""
                ),
                "Room":            rm.get("name", ""),
                "Break Type":      BREAK_TYPE_LABELS.get(r.get("break_type",""), ""),
                "Planned Start":   (r.get("planned_start_time") or "")[:5],
                "Planned End":     (r.get("planned_end_time")   or "")[:5],
                "Planned Dur (min)": r.get("planned_duration_minutes", ""),
                "Actual Start":    (r.get("actual_start_time")  or "")[:5],
                "Actual End":      (r.get("actual_end_time")    or "")[:5],
                "Actual Dur (min)": r.get("actual_duration_minutes", ""),
                "Status":          r.get("status", "").title(),
                "Notes":           r.get("notes", ""),
            })
        csv = pd.DataFrame(export_rows).to_csv(index=False)
        st.download_button(
            "⬇️  Export CSV (Fair Work Audit)",
            data=csv,
            file_name=f"break_history_{centre_id[:8]}_{from_d}_{to_d}.csv",
            mime="text/csv",
        )

    if not records:
        st.info("No break records found for the selected filters.")
        return

    st.markdown("---")

    # ── Per-staff compliance summary ──────────────────────────────────
    st.markdown("### Staff Compliance Summary")
    st.caption(
        "Based on Australian Children's Services Award 2010 break entitlements. "
        "Staff working ≥4h are entitled to a rest break; ≥5h also get a meal break."
    )

    _render_compliance_summary(records)

    st.markdown("---")

    # ── Individual records ────────────────────────────────────────────
    st.markdown("### All Records")

    for rec in records:
        _render_history_row(rec)


# ── Per-staff compliance summary ──────────────────────────────────────────────

def _render_compliance_summary(records: list):
    """
    Group records by staff member + date, then check compliance
    against award entitlements.
    """
    # Group: (user_id, break_date) → breaks + shift data
    from collections import defaultdict
    groups: dict[tuple, dict] = defaultdict(lambda: {
        "breaks": [], "staff_name": "", "shift_start": "", "shift_end": ""
    })

    for r in records:
        u    = r.get("users") or {}
        uid  = r.get("user_id", "")
        bd   = r.get("break_date", "")
        key  = (uid, bd)
        g    = groups[key]
        g["breaks"].append(r)
        g["staff_name"] = f"{u.get('first_name','')} {u.get('last_name','')}".strip()
        # Get shift times if available
        rs = r.get("roster_shifts")
        if isinstance(rs, dict) and rs.get("start_time"):
            g["shift_start"] = rs.get("start_time","")[:5]
            g["shift_end"]   = rs.get("end_time","")[:5]

    if not groups:
        return

    # Render as a compact compliance table
    all_rows = []
    for (uid, bd), g in sorted(groups.items(), key=lambda x: (x[0][1], x[0][0]), reverse=True):
        sname  = g["staff_name"]
        breaks = g["breaks"]
        ss     = g["shift_start"]
        se     = g["shift_end"]

        shift_mins = shift_duration_minutes(ss, se) if ss and se else 0
        ent        = calc_break_entitlement(shift_mins)
        comp       = compliance_summary(breaks, ent)

        # Status colour
        if comp["status"] == "compliant":
            sc_bg, sc_text = "#dcfce7", "#166534"
        elif comp["status"] == "partial":
            sc_bg, sc_text = "#fef3c7", "#92400e"
        elif comp["status"] == "missed":
            sc_bg, sc_text = "#fee2e2", "#991b1b"
        else:
            sc_bg, sc_text = "#f1f5f9", "#475569"

        all_rows.append({
            "date":    bd,
            "sname":   sname,
            "ent":     ent,
            "comp":    comp,
            "sc_bg":   sc_bg,
            "sc_text": sc_text,
            "ss":      ss,
            "se":      se,
            "shift_mins": shift_mins,
        })

    for row in all_rows[:50]:   # Cap at 50 rows for performance
        st.markdown(
            f'<div style="display:flex;justify-content:space-between;align-items:center;'
            f'padding:0.45rem 0.8rem;border-bottom:1px solid #f0f4f8;font-size:0.88rem;">'

            f'<div style="display:flex;gap:0.8rem;align-items:center;flex:1;">'
            f'<span style="color:#7a90a8;font-size:0.78rem;white-space:nowrap;">'
            f'{fmt_date(row["date"])}</span>'
            f'<span style="color:#0d1f35;font-weight:500;">{row["sname"]}</span>'
            + (
                f'<span style="color:#94a3b8;font-size:0.78rem;">'
                f'{row["ss"]}–{row["se"]}</span>'
                if row["ss"] else ""
            )
            + f'</div>'

            f'<div style="display:flex;gap:0.6rem;align-items:center;">'
            f'<span style="font-size:0.78rem;color:#7a90a8;">'
            f'{row["ent"]["summary"]}</span>'
            f'<span style="background:{row["sc_bg"]};color:{row["sc_text"]};'
            f'padding:2px 9px;border-radius:99px;font-size:0.75rem;font-weight:600;">'
            f'{row["comp"]["note"]}</span>'
            f'</div></div>',
            unsafe_allow_html=True,
        )


# ── Individual record row ──────────────────────────────────────────────────────

def _render_history_row(r: dict):
    """Expandable row for one break record."""
    u      = r.get("users") or {}
    sname  = f"{u.get('first_name','')} {u.get('last_name','')}".strip()
    btype  = BREAK_TYPE_LABELS.get(r.get("break_type",""), "Break")
    bd     = fmt_date(r.get("break_date"))
    pstart = fmt_time(r.get("planned_start_time"))
    pend   = fmt_time(r.get("planned_end_time"))
    pdur   = r.get("planned_duration_minutes", 0)
    astart = fmt_time(r.get("actual_start_time"))
    aend   = fmt_time(r.get("actual_end_time"))
    adur   = r.get("actual_duration_minutes")
    status = r.get("status", "scheduled")
    notes  = r.get("notes") or ""
    cfg    = BREAK_STATUS_CONFIG.get(status, BREAK_STATUS_CONFIG["scheduled"])

    # Variance from entitlement
    diff = (adur or 0) - pdur if adur is not None else None

    with st.expander(
        f"**{sname}** · {bd} · {btype} · {pstart}–{pend} · {cfg['icon']} {cfg['label']}",
        expanded=False,
    ):
        rc1, rc2, rc3, rc4 = st.columns(4)
        rc1.markdown(f"**Staff**  \n{sname}")
        rc2.markdown(f"**Date**  \n{bd}")
        rc3.markdown(
            f"**Status**  \n"
            f'<span style="background:{cfg["bg"]};color:{cfg["text"]};'
            f'padding:2px 8px;border-radius:99px;font-size:0.78rem;font-weight:600;">'
            f'{cfg["icon"]} {cfg["label"]}</span>',
            unsafe_allow_html=True,
        )
        rc4.markdown(f"**Break type**  \n{btype}")

        rd1, rd2, rd3, rd4 = st.columns(4)
        rd1.markdown(f"**Planned**  \n{pstart} – {pend}")
        rd2.markdown(f"**Entitlement**  \n{fmt_duration(pdur)}")
        rd3.markdown(f"**Actual**  \n{astart} – {aend}" if astart != "—" else "**Actual**  \nNot recorded")
        rd4.markdown(f"**Actual duration**  \n{fmt_duration(adur)}")

        # Variance highlight
        if diff is not None:
            if diff < 0:
                st.markdown(
                    f'<p style="color:#991b1b;font-size:0.85rem;">'
                    f'⚠️ Break was <strong>{abs(diff)} min short</strong> of entitlement.</p>',
                    unsafe_allow_html=True,
                )
            elif diff > 5:
                st.markdown(
                    f'<p style="color:#92400e;font-size:0.85rem;">'
                    f'ℹ️ Break was <strong>{diff} min over</strong> scheduled time.</p>',
                    unsafe_allow_html=True,
                )

        # Shift context
        rs = r.get("roster_shifts")
        if isinstance(rs, dict) and rs.get("start_time"):
            rm = rs.get("rooms") or {}
            st.caption(
                f"Shift: {rs.get('start_time','')[:5]}–{rs.get('end_time','')[:5]}"
                + (f" · Room: {rm.get('name','')}" if rm.get("name") else "")
            )

        if notes:
            st.markdown(f"_Notes: {notes}_")

        # Flag for review button
        if status in ("missed",) or diff is not None and diff < -5:
            if st.button(
                "🚩 Flag for Payroll Review",
                key=f"flag_{r.get('id','')}",
                help="Mark this break for the payroll officer to review",
            ):
                st.info(
                    "Flagged. In a full implementation this would create a payroll "
                    "review task. For now, export the CSV and note this record."
                )
