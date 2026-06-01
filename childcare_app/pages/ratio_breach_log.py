# pages/ratio_breach_log.py  —  Screen 29: Ratio Breach Log
# Rebuilt with: trend analytics, per-room stats, monthly summary,
# severity distribution chart, and improved log form.

import streamlit as st
import pandas as pd
from datetime import date, datetime, timedelta

from utils.room_queries import fetch_rooms, log_breach
from utils.ratio_queries import fetch_breach_stats
from utils.ratio_engine import classify_breach_severity, fmt_time_12h
from utils.staff_queries import fetch_centres
from utils.helpers import fmt_date, toast_success, toast_error


def render():
    # ── Header ────────────────────────────────────────────────────────
    h1, h2 = st.columns([4, 1])
    h1.title("Ratio Breach Log")
    h1.markdown(
        '<p class="page-sub">Compliance incident record for NQS quality assessments & regulatory audits</p>',
        unsafe_allow_html=True,
    )
    with h2:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("📊  Monitor", use_container_width=True):
            st.session_state.page = "ratio_dashboard"
            st.rerun()

    # ── Centre selector ───────────────────────────────────────────────
    centres = fetch_centres()
    if not centres:
        st.warning("No centres found.")
        return

    centre_opts = {c["id"]: c["name"] for c in centres}
    saved = (
        st.session_state.pop("log_breach_centre_id", None)
        or st.session_state.get("ratio_centre_id")
        or st.session_state.get("selected_centre_id")
        or centres[0]["id"]
    )

    centre_id = st.selectbox(
        "Centre",
        options=list(centre_opts.keys()),
        format_func=lambda x: centre_opts[x],
        index=list(centre_opts.keys()).index(saved) if saved in centre_opts else 0,
        key="breach_centre_sel",
    )
    st.session_state.ratio_centre_id = centre_id

    # ── Load rooms for dropdowns ──────────────────────────────────────
    try:
        rooms     = fetch_rooms(centre_id, include_inactive=True)
        room_opts = {"": "All Rooms"}
        room_opts.update({r["id"]: r["name"] for r in rooms})
    except Exception:
        rooms, room_opts = [], {"": "All Rooms"}

    # ── Pre-fill context from ratio dashboard ─────────────────────────
    pre_room_id   = st.session_state.pop("log_breach_room_id",   None)
    pre_children  = st.session_state.pop("log_breach_children",  0)
    pre_staff     = st.session_state.pop("log_breach_staff",      0)
    pre_min_staff = st.session_state.pop("log_breach_min_staff",  0)

    show_form = "show_breach_form"
    if pre_room_id:
        st.session_state[show_form] = True

    # ── Log breach button ─────────────────────────────────────────────
    if st.button("➕  Log Breach", type="primary", key="log_breach_btn"):
        st.session_state[show_form] = not st.session_state.get(show_form, False)

    if st.session_state.get(show_form):
        st.markdown("---")
        _render_log_form(
            centre_id, rooms, room_opts,
            pre_room_id, pre_children, pre_staff, pre_min_staff, show_form,
        )
        st.markdown("---")

    # ── Load 90-day analytics ─────────────────────────────────────────
    with st.spinner("Loading analytics…"):
        try:
            stats = fetch_breach_stats(centre_id, days_back=90)
        except Exception as e:
            toast_error(f"Could not load data: {e}")
            return

    records = stats["records"]

    # ── Analytics summary ─────────────────────────────────────────────
    _render_analytics(stats)

    st.markdown("---")

    # ── Filters ───────────────────────────────────────────────────────
    st.markdown("### Incident Records")
    fc1, fc2, fc3, fc4 = st.columns([1.5, 1.5, 1.5, 1.5])
    from_d = fc1.date_input(
        "From", value=date.today() - timedelta(days=30),
        key="breach_from", format="DD/MM/YYYY",
    )
    to_d = fc2.date_input(
        "To", value=date.today(),
        key="breach_to", format="DD/MM/YYYY",
    )
    room_filter = fc3.selectbox(
        "Room", options=list(room_opts.keys()),
        format_func=lambda x: room_opts[x],
        key="breach_room_filter", label_visibility="collapsed",
    )
    severity_filter = fc4.selectbox(
        "Severity",
        options=["all", "minor", "significant", "critical", "unknown"],
        format_func=lambda x: {
            "all": "All Severities", "minor": "Minor (<5 min)",
            "significant": "Significant (5–30 min)", "critical": "Critical (>30 min)",
            "unknown": "Unknown duration",
        }.get(x, x),
        key="breach_severity", label_visibility="collapsed",
    )

    # ── Filter records ────────────────────────────────────────────────
    filtered = [
        r for r in records
        if from_d.isoformat() <= (r.get("breach_date") or "") <= to_d.isoformat()
    ]
    if room_filter:
        filtered = [
            r for r in filtered
            if (r.get("rooms") or {}).get("id") == room_filter
        ]
    if severity_filter != "all":
        def sev_key(r):
            d = r.get("duration_minutes")
            if d is None: return "unknown"
            if d < 5:     return "minor"
            if d <= 30:   return "significant"
            return "critical"
        filtered = [r for r in filtered if sev_key(r) == severity_filter]

    # ── Export ────────────────────────────────────────────────────────
    if filtered:
        rows = []
        for b in filtered:
            r   = b.get("rooms") or {}
            doc = b.get("documenter") or {}
            rows.append({
                "Date":            b.get("breach_date", ""),
                "Start Time":      b.get("breach_start_time", "")[:5] if b.get("breach_start_time") else "",
                "End Time":        b.get("breach_end_time", "")[:5]   if b.get("breach_end_time")   else "",
                "Room":            r.get("name", ""),
                "Duration (min)":  b.get("duration_minutes", ""),
                "Severity":        classify_breach_severity(b.get("duration_minutes"))["label"],
                "Children":        b.get("children_present", ""),
                "Staff Present":   b.get("staff_present", ""),
                "Staff Required":  b.get("required_staff", ""),
                "Reason":          b.get("breach_reason", ""),
                "Resolution":      b.get("resolution_action", ""),
                "Documented By":   f"{doc.get('first_name','')} {doc.get('last_name','')}".strip(),
            })
        csv = pd.DataFrame(rows).to_csv(index=False)
        st.download_button(
            "⬇️  Export CSV (Audit)",
            data=csv,
            file_name=f"ratio_breaches_{centre_id[:8]}_{from_d}_{to_d}.csv",
            mime="text/csv",
        )

    # ── Filtered metrics ──────────────────────────────────────────────
    total_dur  = sum((b.get("duration_minutes") or 0) for b in filtered)
    n_critical = sum(1 for b in filtered if (b.get("duration_minutes") or 0) > 30)
    fm1, fm2, fm3 = st.columns(3)
    fm1.metric("Incidents shown",     len(filtered))
    fm2.metric("Total breach time",   f"{total_dur} min")
    fm3.metric("Critical (>30 min)",  n_critical)

    # ── Empty state ───────────────────────────────────────────────────
    if not filtered:
        st.success("✅ No breach records for the selected period and filters.")
        return

    st.markdown("")

    # ── Incident list ─────────────────────────────────────────────────
    for b in filtered:
        _render_breach_row(b)


# ── Analytics section ──────────────────────────────────────────────────────────
def _render_analytics(stats: dict):
    total   = stats["total"]
    mins    = stats["total_mins"]
    days    = stats["days_back"]

    st.markdown(f"### 📊 Last {days} Days — Analytics")

    # Top-line metrics
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Total Incidents",   total)
    m2.metric("Total Breach Time", f"{mins} min")
    m3.metric("Critical (>30m)",   stats["critical"])
    m4.metric("Significant",       stats["significant"])
    m5.metric("Minor (<5m)",       stats["minor"])

    # Per-room breakdown
    if stats["per_room"]:
        st.markdown("")
        st.markdown("**Incidents by room (last 90 days)**")
        cols = st.columns(min(len(stats["per_room"]), 4))
        for i, room_stat in enumerate(
            sorted(stats["per_room"], key=lambda r: r["count"], reverse=True)
        ):
            col      = cols[i % 4]
            colour   = room_stat.get("colour", "#3498DB")
            rname    = room_stat.get("name", "Room")
            count    = room_stat["count"]
            tot_mins = room_stat["total_minutes"]
            col.markdown(
                f'<div style="border-left:4px solid {colour};padding:0.5rem 0.8rem;'
                f'background:#fafcfe;border-radius:0 8px 8px 0;margin-bottom:0.4rem;">'
                f'<div style="font-weight:600;font-size:0.9rem;color:#0d1f35;">{rname}</div>'
                f'<div style="font-size:0.8rem;color:#7a90a8;">'
                f'{count} incident{"s" if count != 1 else ""} · {tot_mins} min total'
                f'</div></div>',
                unsafe_allow_html=True,
            )

    # Monthly trend mini-chart using st.bar_chart
    if stats["monthly"]:
        monthly = stats["monthly"]
        if len(monthly) > 1:
            st.markdown("")
            st.markdown("**Monthly trend**")
            month_df = pd.DataFrame(
                [(k, v) for k, v in sorted(monthly.items())],
                columns=["Month", "Incidents"],
            ).set_index("Month")
            st.bar_chart(month_df, use_container_width=True, height=160)


# ── Individual breach row ──────────────────────────────────────────────────────
def _render_breach_row(b: dict):
    r_rec   = b.get("rooms") or {}
    rname   = r_rec.get("name", "—")
    rcolour = r_rec.get("colour", "#3498DB")
    bd      = fmt_date(b.get("breach_date"))
    bstart  = fmt_time_12h(b.get("breach_start_time"))
    bend    = fmt_time_12h(b.get("breach_end_time")) if b.get("breach_end_time") else "ongoing"
    dur     = b.get("duration_minutes")
    sev     = classify_breach_severity(dur)
    nc      = b.get("children_present", "?")
    ns      = b.get("staff_present",    "?")
    nr      = b.get("required_staff",   "?")
    reason  = b.get("breach_reason")    or "—"
    resol   = b.get("resolution_action") or "—"
    doc     = b.get("documenter") or {}
    doc_str = f"{doc.get('first_name','')} {doc.get('last_name','')}".strip() or "—"
    dur_str = f"{dur} min" if dur else "Duration unknown"

    with st.expander(
        f"**{bd}** · {rname} · {bstart}–{bend} · {sev['label']} ({dur_str})",
        expanded=(sev["label"] == "Critical"),
    ):
        # Header row
        c1, c2, c3, c4 = st.columns(4)
        c1.markdown(
            f"**Room**  \n"
            f'<span style="display:inline-flex;align-items:center;gap:5px;">'
            f'<span style="width:10px;height:10px;border-radius:50%;'
            f'background:{rcolour};display:inline-block;"></span>{rname}</span>',
            unsafe_allow_html=True,
        )
        c2.markdown(f"**Time**  \n{bstart} → {bend}")
        c3.markdown(f"**Duration**  \n{dur_str}")
        c4.markdown(
            f"**Severity**  \n"
            f'<span style="background:{sev["bg"]};color:{sev["text"]};'
            f'padding:2px 9px;border-radius:99px;font-size:0.78rem;font-weight:600;">'
            f'{sev["label"]}</span>',
            unsafe_allow_html=True,
        )

        # Data row
        d1, d2, d3, d4 = st.columns(4)
        d1.markdown(f"**Children**  \n{nc}")
        d2.markdown(f"**Staff present**  \n{ns}")
        d3.markdown(f"**Staff required**  \n{nr}")
        d4.markdown(f"**Documented by**  \n{doc_str}")

        st.markdown(f"**Reason:** {reason}")
        if resol != "—":
            st.markdown(f"**Resolution:** {resol}")


# ── Log breach form ────────────────────────────────────────────────────────────
def _render_log_form(
    centre_id: str, rooms: list, room_opts: dict,
    pre_room_id, pre_children, pre_staff, pre_min_staff,
    show_form: str,
):
    st.markdown("### Log Ratio Breach")
    st.caption(
        "Document this incident for your compliance and audit records. "
        "All fields are stored permanently and cannot be edited after saving."
    )

    with st.form("log_breach_form"):
        lc1, lc2 = st.columns(2)
        room_keys    = list(room_opts.keys())
        room_default = (
            room_keys.index(pre_room_id)
            if pre_room_id and pre_room_id in room_keys
            else 0
        )
        selected_room = lc1.selectbox(
            "Room *",
            options=room_keys,
            format_func=lambda x: room_opts[x],
            index=room_default,
        )
        breach_date = lc2.date_input(
            "Date *", value=date.today(), format="DD/MM/YYYY",
        )

        lc3, lc4 = st.columns(2)
        from datetime import time as _time
        now = datetime.now()
        breach_start = lc3.time_input(
            "Start time *", value=_time(now.hour, 0),
        )
        breach_end = lc4.time_input(
            "End time", value=_time(now.hour, 30),
            help="Set equal to start time if breach is still ongoing.",
        )

        nc1, nc2, nc3 = st.columns(3)
        n_children = nc1.number_input(
            "Children present *",
            min_value=0, max_value=100, value=int(pre_children), key="lbf_nc",
        )
        n_staff = nc2.number_input(
            "Staff present *",
            min_value=0, max_value=50, value=int(pre_staff), key="lbf_ns",
        )
        req_staff = nc3.number_input(
            "Staff required *",
            min_value=1, max_value=50,
            value=int(pre_min_staff) if pre_min_staff else 1,
            key="lbf_nr",
        )

        # Auto-calculate severity preview
        if n_children > 0:
            end_mins   = breach_end.hour * 60 + breach_end.minute
            start_mins = breach_start.hour * 60 + breach_start.minute
            est_dur    = max(0, end_mins - start_mins) if breach_end != breach_start else 0
            sev_prev   = classify_breach_severity(est_dur)
            st.markdown(
                f'<p style="font-size:0.82rem;color:{sev_prev["text"]};">'
                f'Estimated severity: <strong>{sev_prev["label"]}</strong>'
                + (f" ({est_dur} min)" if est_dur else " (duration unknown)")
                + "</p>",
                unsafe_allow_html=True,
            )

        reason     = st.text_area(
            "Reason for breach *",
            placeholder="e.g. Staff member called in sick with no available cover.",
            key="lbf_reason",
        )
        resolution = st.text_area(
            "Action taken / resolution",
            placeholder="e.g. Director covered the room. Casual staff arranged for afternoon.",
            key="lbf_resolution",
        )

        sc1, sc2 = st.columns(2)
        save_btn   = sc1.form_submit_button(
            "📋  Save Breach Record", type="primary", use_container_width=True,
        )
        cancel_btn = sc2.form_submit_button("Cancel", use_container_width=True)

    if cancel_btn:
        st.session_state[show_form] = False
        st.rerun()

    if save_btn:
        if not selected_room:
            toast_error("Please select a room.")
        elif not reason.strip():
            toast_error("Please describe the reason for the breach.")
        else:
            try:
                end_str = (
                    breach_end.strftime("%H:%M:%S")
                    if breach_end != breach_start
                    else None
                )
                log_breach(
                    centre_id=centre_id,
                    room_id=selected_room,
                    breach_date=breach_date.isoformat(),
                    breach_start_time=breach_start.strftime("%H:%M:%S"),
                    breach_end_time=end_str,
                    children_present=int(n_children),
                    staff_present=int(n_staff),
                    required_staff=int(req_staff),
                    breach_reason=reason,
                    resolution_action=resolution,
                )
                toast_success("Breach record saved to compliance log.")
                st.session_state[show_form] = False
                st.rerun()
            except Exception as e:
                toast_error(f"Could not save: {e}")
