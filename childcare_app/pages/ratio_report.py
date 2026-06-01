# pages/ratio_report.py  —  Ratio Compliance Report
# Generates a printable/exportable compliance summary for NQS assessments.
# Covers: breach summary, per-room analysis, qualification gaps, recommendations.

import streamlit as st
import pandas as pd
from datetime import date, timedelta

from utils.room_queries import fetch_rooms
from utils.ratio_queries import fetch_compliance_summary, fetch_breach_stats
from utils.ratio_engine import classify_breach_severity
from utils.staff_queries import fetch_centres, fetch_all_staff
from utils.helpers import fmt_date, toast_error


def render():
    # ── Header ────────────────────────────────────────────────────────
    bc, hc, btn_c = st.columns([1, 4, 1])
    with bc:
        st.markdown('<div class="back-btn">', unsafe_allow_html=True)
        if st.button("← Monitor", key="rr_back"):
            st.session_state.page = "ratio_dashboard"
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)
    with hc:
        st.title("Ratio Compliance Report")
        st.markdown(
            '<p class="page-sub">Generate a compliance summary for NQS self-assessments and regulatory visits</p>',
            unsafe_allow_html=True,
        )

    # ── Centre & period selector ──────────────────────────────────────
    centres = fetch_centres()
    if not centres:
        st.warning("No centres found.")
        return

    centre_opts = {c["id"]: c["name"] for c in centres}
    saved = (
        st.session_state.get("ratio_centre_id")
        or st.session_state.get("selected_centre_id")
        or centres[0]["id"]
    )

    cfg_col1, cfg_col2, cfg_col3 = st.columns([2, 1.5, 1.5])

    centre_id = cfg_col1.selectbox(
        "Centre *",
        options=list(centre_opts.keys()),
        format_func=lambda x: centre_opts[x],
        index=list(centre_opts.keys()).index(saved) if saved in centre_opts else 0,
        key="rr_centre",
    )
    st.session_state.ratio_centre_id = centre_id

    period_opt = cfg_col2.selectbox(
        "Report period",
        options=["last_7", "last_30", "last_90", "last_365", "custom"],
        format_func=lambda x: {
            "last_7":    "Last 7 days",
            "last_30":   "Last 30 days",
            "last_90":   "Last 90 days (quarter)",
            "last_365":  "Last 12 months",
            "custom":    "Custom range",
        }.get(x, x),
        key="rr_period",
    )

    today = date.today()
    if period_opt == "last_7":
        from_date, to_date = today - timedelta(days=7), today
    elif period_opt == "last_30":
        from_date, to_date = today - timedelta(days=30), today
    elif period_opt == "last_90":
        from_date, to_date = today - timedelta(days=90), today
    elif period_opt == "last_365":
        from_date, to_date = today - timedelta(days=365), today
    else:
        from_date = cfg_col3.date_input("From", value=today - timedelta(days=30),
                                         key="rr_from", format="DD/MM/YYYY")
        to_date   = cfg_col3.date_input("To",   value=today,
                                         key="rr_to",   format="DD/MM/YYYY")

    generate = st.button("📄  Generate Report", type="primary")
    if not generate and not st.session_state.get("rr_generated"):
        st.info("Select your centre and period, then click **Generate Report**.")
        return

    st.session_state["rr_generated"] = True

    # ── Load data ─────────────────────────────────────────────────────
    days_back = (to_date - from_date).days or 1
    with st.spinner("Generating report…"):
        try:
            rooms   = fetch_rooms(centre_id, include_inactive=True)
            stats   = fetch_breach_stats(centre_id, days_back=days_back + 1)
            records = fetch_compliance_summary(centre_id, days_back=days_back + 1)
            # Filter to period
            records = [
                r for r in records
                if from_date.isoformat() <= (r.get("breach_date") or "") <= to_date.isoformat()
            ]
            stats_filtered = _recompute_stats(records)
        except Exception as e:
            toast_error(f"Could not generate report: {e}")
            return

    centre_name = centre_opts.get(centre_id, "—")

    # ── Report header ─────────────────────────────────────────────────
    st.markdown("---")
    st.markdown(
        f'<div style="background:#0d1f35;border-radius:12px;padding:1.6rem 2rem;'
        f'margin-bottom:1.5rem;">'
        f'<div style="font-family:DM Serif Display,serif;font-size:1.6rem;'
        f'color:#ffffff;">Ratio Compliance Report</div>'
        f'<div style="color:#7a90a8;font-size:0.9rem;margin-top:0.3rem;">'
        f'{centre_name} · {fmt_date(from_date)} to {fmt_date(to_date)} · '
        f'Generated {today.strftime("%-d %B %Y")}'
        f'</div></div>',
        unsafe_allow_html=True,
    )

    # ── Executive summary ─────────────────────────────────────────────
    st.markdown("### Executive Summary")

    total     = stats_filtered["total"]
    total_min = stats_filtered["total_mins"]
    critical  = stats_filtered["critical"]
    avg_per_week = round(total / max(days_back / 7, 1), 1)

    # Overall compliance rating
    if total == 0:
        rating_text  = "Excellent"
        rating_icon  = "🏆"
        rating_colour = "#14532d"
        rating_bg     = "#f0fdf4"
        rating_msg    = (
            "No ratio breaches recorded during this period. "
            "This demonstrates strong compliance management."
        )
    elif critical == 0 and avg_per_week < 1:
        rating_text  = "Good"
        rating_icon  = "✅"
        rating_colour = "#1d4ed8"
        rating_bg     = "#eff6ff"
        rating_msg    = (
            f"{total} minor incident(s) recorded, none critical. "
            "Overall compliance is satisfactory."
        )
    elif avg_per_week < 3:
        rating_text  = "Needs Attention"
        rating_icon  = "⚠️"
        rating_colour = "#92400e"
        rating_bg     = "#fffbeb"
        rating_msg    = (
            f"{total} incidents recorded ({avg_per_week}/week average). "
            "Review staffing and rostering practices."
        )
    else:
        rating_text  = "Critical — Action Required"
        rating_icon  = "🚨"
        rating_colour = "#991b1b"
        rating_bg     = "#fff1f2"
        rating_msg    = (
            f"{total} incidents recorded ({avg_per_week}/week average) including "
            f"{critical} critical. Immediate review of staffing levels required."
        )

    st.markdown(
        f'<div style="background:{rating_bg};border-radius:12px;'
        f'padding:1.2rem 1.6rem;margin-bottom:1.2rem;display:flex;gap:1.2rem;align-items:flex-start;">'
        f'<div style="font-size:2.5rem;line-height:1;">{rating_icon}</div>'
        f'<div><div style="font-family:DM Serif Display,serif;font-size:1.3rem;'
        f'color:{rating_colour};">{rating_text}</div>'
        f'<div style="font-size:0.9rem;color:#4a6079;margin-top:0.25rem;">{rating_msg}</div>'
        f'</div></div>',
        unsafe_allow_html=True,
    )

    # Summary metrics
    sm1, sm2, sm3, sm4, sm5 = st.columns(5)
    sm1.metric("Period",           f"{days_back} days")
    sm2.metric("Total Incidents",  total)
    sm3.metric("Total Breach Time", f"{total_min} min")
    sm4.metric("Critical (>30m)",  critical)
    sm5.metric("Per Week (avg)",   avg_per_week)

    st.markdown("---")

    # ── Per-room analysis ─────────────────────────────────────────────
    st.markdown("### Per-Room Analysis")

    room_map: dict[str, dict] = {r["id"]: r for r in rooms}
    room_stats: dict[str, dict] = {}

    for rec in records:
        r_rec = rec.get("rooms") or {}
        rid   = r_rec.get("id") or "unknown"
        if rid not in room_stats:
            room_stats[rid] = {
                "name":        r_rec.get("name", "Unknown"),
                "colour":      r_rec.get("colour", "#3498DB"),
                "count":       0,
                "total_mins":  0,
                "critical":    0,
                "significant": 0,
                "minor":       0,
            }
        rs = room_stats[rid]
        rs["count"] += 1
        rs["total_mins"] += rec.get("duration_minutes") or 0
        sev = classify_breach_severity(rec.get("duration_minutes"))["label"]
        if sev == "Critical":     rs["critical"]    += 1
        elif sev == "Significant": rs["significant"] += 1
        elif sev == "Minor":       rs["minor"]       += 1

    if room_stats:
        for rs in sorted(room_stats.values(), key=lambda r: r["count"], reverse=True):
            c  = rs["colour"]
            st.markdown(
                f'<div style="border-left:5px solid {c};background:#fafcfe;'
                f'border-radius:0 10px 10px 0;padding:0.8rem 1.1rem;'
                f'margin-bottom:0.7rem;border:1px solid #e4edf5;border-left:5px solid {c};">'
                f'<div style="display:flex;justify-content:space-between;align-items:center;">'
                f'<span style="font-family:DM Serif Display,serif;font-size:1rem;'
                f'color:#0d1f35;">{rs["name"]}</span>'
                f'<span style="font-size:0.85rem;color:#7a90a8;">'
                f'{rs["count"]} incident{"s" if rs["count"] != 1 else ""} · '
                f'{rs["total_mins"]} min total'
                f'</span></div>'
                f'<div style="display:flex;gap:0.6rem;margin-top:0.4rem;flex-wrap:wrap;">'
                + (f'<span style="background:#fee2e2;color:#991b1b;padding:1px 8px;'
                   f'border-radius:99px;font-size:0.72rem;font-weight:600;">'
                   f'{rs["critical"]} critical</span>' if rs["critical"] else "")
                + (f'<span style="background:#fef3c7;color:#92400e;padding:1px 8px;'
                   f'border-radius:99px;font-size:0.72rem;font-weight:600;">'
                   f'{rs["significant"]} significant</span>' if rs["significant"] else "")
                + (f'<span style="background:#dcfce7;color:#166534;padding:1px 8px;'
                   f'border-radius:99px;font-size:0.72rem;font-weight:600;">'
                   f'{rs["minor"]} minor</span>' if rs["minor"] else "")
                + f'</div></div>',
                unsafe_allow_html=True,
            )
    else:
        st.success("✅ No breach records found for any room in this period.")

    st.markdown("---")

    # ── Room configuration review ─────────────────────────────────────
    st.markdown("### Room Configuration Review")
    st.caption("Current room settings used for ratio compliance monitoring.")

    if rooms:
        cfg_data = []
        for room in rooms:
            cfg_data.append({
                "Room":             room.get("name", ""),
                "Age Range":        _age_range_str(room),
                "Capacity":         room.get("licensed_capacity", 0),
                "Required Ratio":   f'1:{room.get("required_ratio_children", 4)}',
                "Diploma Required": "Yes" if room.get("requires_diploma") else "No",
                "Status":           "Active" if room.get("is_active") else "Inactive",
            })
        st.dataframe(
            pd.DataFrame(cfg_data),
            use_container_width=True,
            hide_index=True,
        )

    st.markdown("---")

    # ── Full incident list ────────────────────────────────────────────
    st.markdown("### Full Incident Record")

    if not records:
        st.success("✅ No incidents recorded in this period.")
    else:
        st.caption(f"{len(records)} incident(s) recorded. All times are local centre time.")
        incident_rows = []
        for rec in records:
            r   = rec.get("rooms") or {}
            doc = rec.get("documenter") or {}
            sev = classify_breach_severity(rec.get("duration_minutes"))
            incident_rows.append({
                "Date":           fmt_date(rec.get("breach_date")),
                "Time":           f'{rec.get("breach_start_time","")[:5]}–'
                                  f'{rec.get("breach_end_time","")[:5] if rec.get("breach_end_time") else "?"}',
                "Room":           r.get("name", "—"),
                "Dur.":           f'{rec.get("duration_minutes","?")}m',
                "Severity":       sev["label"],
                "Kids":           rec.get("children_present", "?"),
                "Staff":          rec.get("staff_present", "?"),
                "Req.":           rec.get("required_staff", "?"),
                "Reason":         (rec.get("breach_reason") or "")[:60],
                "Documented By":  f"{doc.get('first_name','')} {doc.get('last_name','')}".strip(),
            })
        st.dataframe(
            pd.DataFrame(incident_rows),
            use_container_width=True,
            hide_index=True,
        )

        # Full CSV export
        full_csv = pd.DataFrame(incident_rows).to_csv(index=False)
        st.download_button(
            "⬇️  Export Full Report (CSV)",
            data=full_csv,
            file_name=f"ratio_compliance_report_{centre_id[:8]}_{from_date}_{to_date}.csv",
            mime="text/csv",
        )

    st.markdown("---")

    # ── Recommendations ───────────────────────────────────────────────
    st.markdown("### Recommendations")
    recs = _generate_recommendations(stats_filtered, room_stats if room_stats else {})
    for i, rec in enumerate(recs, 1):
        st.markdown(
            f'<div style="display:flex;gap:0.8rem;margin-bottom:0.6rem;'
            f'padding:0.7rem 1rem;background:#f8fafc;border-radius:8px;">'
            f'<span style="font-family:DM Serif Display,serif;font-size:1rem;'
            f'color:#0d1f35;flex-shrink:0;">{i}.</span>'
            f'<span style="font-size:0.9rem;color:#1e3a55;">{rec}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # ── Report footer ─────────────────────────────────────────────────
    st.markdown(
        f'<div style="background:#f5f8fb;border-radius:8px;padding:0.9rem 1.2rem;'
        f'font-size:0.78rem;color:#7a90a8;margin-top:1rem;">'
        f'Report generated {today.strftime("%-d %B %Y")} from Childcare Platform v1.0. '
        f'This report covers {centre_name} for the period '
        f'{fmt_date(from_date)} to {fmt_date(to_date)} ({days_back} days). '
        f'Ratio requirements reflect room configuration at time of report generation. '
        f'Always verify requirements against current NQS regulations and your state regulator.'
        f'</div>',
        unsafe_allow_html=True,
    )


# ── Helpers ────────────────────────────────────────────────────────────────────

def _age_range_str(room: dict) -> str:
    mn = room.get("age_min_months", 0)
    mx = room.get("age_max_months", 72)
    def lbl(m):
        if m < 24: return f"{m}m"
        y = m // 12; r = m % 12
        return f"{y}y" if r == 0 else f"{y}y{r}m"
    return f"{lbl(mn)}–{lbl(mx)}"


def _recompute_stats(records: list[dict]) -> dict:
    total    = len(records)
    total_m  = sum((r.get("duration_minutes") or 0) for r in records)
    critical = sum(1 for r in records if (r.get("duration_minutes") or 0) > 30)
    sig      = sum(1 for r in records if 5 <= (r.get("duration_minutes") or 0) <= 30)
    minor    = sum(1 for r in records if 0 < (r.get("duration_minutes") or 0) < 5)
    return {
        "total": total, "total_mins": total_m,
        "critical": critical, "significant": sig, "minor": minor,
    }


def _generate_recommendations(stats: dict, room_stats: dict) -> list[str]:
    recs = []
    total = stats["total"]

    if total == 0:
        recs.append(
            "Maintain current practices. Continue regular roster review and "
            "ensure casual cover lists are kept up to date for unexpected absences."
        )
    else:
        if stats["critical"] > 0:
            recs.append(
                f"{stats['critical']} critical breach(es) detected (>30 minutes). "
                "Review emergency cover procedures and ensure all managers have "
                "access to a casual staff register for urgent callouts."
            )
        if total > 4:
            recs.append(
                "Frequency of breaches suggests systemic staffing coverage gaps. "
                "Consider reviewing minimum staffing levels in rosters, particularly "
                "during transition periods (early morning, late afternoon)."
            )
        # Most-breached room
        if room_stats:
            worst = max(room_stats.values(), key=lambda r: r["count"])
            if worst["count"] > 1:
                recs.append(
                    f"The {worst['name']} has recorded {worst['count']} incidents — "
                    "the most of any room. Review staffing patterns and shift handover "
                    "times specific to this room."
                )

        recs.append(
            "Ensure all breach records are documented within 24 hours of the incident, "
            "including the reason and resolution action taken."
        )
        recs.append(
            "Cross-check staff qualifications against room requirements. "
            "Rooms requiring diploma-qualified educators must have at least one "
            "such educator present whenever children are in the room."
        )

    recs.append(
        "Review this report with your team during the next staff meeting. "
        "The ratio breach log is available for inspection by regulatory bodies."
    )
    return recs
