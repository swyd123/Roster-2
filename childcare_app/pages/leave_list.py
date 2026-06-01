# pages/leave_list.py  —  Leave Requests list with approve/decline actions
import streamlit as st
from datetime import date
import pandas as pd
from utils.staff_queries import (
    fetch_leave_requests, fetch_centres, create_leave_request
)
from utils.helpers import (
    fmt_date, fmt_leave_type, fmt_name,
    LEAVE_TYPE_KEYS, LEAVE_TYPES,
    toast_success, toast_error, workdays_between,
)


def render():
    hc, btn_c = st.columns([4, 1])
    hc.title("Leave Requests")
    hc.markdown('<p class="page-sub">Review and action staff leave requests</p>',
                unsafe_allow_html=True)
    with btn_c:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("➕  New Request", type="primary", use_container_width=True):
            st.session_state.page = "leave_add"
            st.rerun()

    # ── Filters ───────────────────────────────────────────────────────
    st.markdown("---")
    fc1, fc2, fc3 = st.columns([2, 1.5, 1.5])
    search        = fc1.text_input("🔍  Search staff", placeholder="Name…",
                                    label_visibility="collapsed")
    status_filter = fc2.selectbox("Status", ["all","pending","approved","declined","cancelled"],
                                   format_func=lambda x: x.title() if x != "all" else "All Statuses",
                                   label_visibility="collapsed")
    type_filter   = fc3.selectbox("Leave type", ["all"] + LEAVE_TYPE_KEYS,
                                   format_func=lambda x: "All Types" if x == "all" else LEAVE_TYPES[x],
                                   label_visibility="collapsed")

    # ── Load data ─────────────────────────────────────────────────────
    with st.spinner("Loading…"):
        try:
            leaves = fetch_leave_requests(status_filter=status_filter if status_filter != "all" else None)
        except Exception as e:
            toast_error(f"Could not load leave requests: {e}"); return

    # Apply filters
    if search:
        t = search.lower()
        leaves = [lv for lv in leaves if t in fmt_name(lv.get("users") or {}).lower()]
    if type_filter != "all":
        leaves = [lv for lv in leaves if lv.get("leave_type") == type_filter]

    # ── Metrics ───────────────────────────────────────────────────────
    pending  = sum(1 for lv in leaves if lv.get("status") == "pending")
    approved = sum(1 for lv in leaves if lv.get("status") == "approved")
    declined = sum(1 for lv in leaves if lv.get("status") == "declined")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total Requests",  len(leaves))
    m2.metric("Pending Action",  pending)
    m3.metric("Approved",        approved)
    m4.metric("Declined",        declined)
    st.markdown("---")

    # ── Export ────────────────────────────────────────────────────────
    if leaves:
        rows = []
        for lv in leaves:
            u = lv.get("users") or {}
            rows.append({
                "Staff":        fmt_name(u),
                "Leave Type":   fmt_leave_type(lv.get("leave_type","")),
                "Start":        lv.get("start_date",""),
                "End":          lv.get("end_date",""),
                "Status":       lv.get("status","").title(),
                "Reason":       lv.get("reason",""),
                "Review Notes": lv.get("review_notes",""),
            })
        csv = pd.DataFrame(rows).to_csv(index=False)
        st.download_button("⬇️  Export CSV", data=csv,
                           file_name="leave_requests.csv", mime="text/csv")

    # ── Empty state ───────────────────────────────────────────────────
    if not leaves:
        st.info("No leave requests found.")
        return

    st.markdown("")

    # ── Leave request rows ────────────────────────────────────────────
    status_icons   = {"pending":"🟡","approved":"✅","declined":"❌","cancelled":"⚫"}
    status_colours = {
        "pending":   ("#fef3cd","#92510a"),
        "approved":  ("#d4f0e4","#0f6b3a"),
        "declined":  ("#fde8e8","#991b1b"),
        "cancelled": ("#e8eff5","#4a6079"),
    }

    for lv in leaves:
        u      = lv.get("users") or {}
        name   = fmt_name(u)
        status = lv.get("status","")
        icon   = status_icons.get(status,"❓")
        lt     = fmt_leave_type(lv.get("leave_type",""))
        sd     = fmt_date(lv.get("start_date"))
        ed     = fmt_date(lv.get("end_date"))
        reason = lv.get("reason") or "—"
        centre = (lv.get("centres") or {}).get("name","—")

        try:
            ndays = workdays_between(
                date.fromisoformat(lv["start_date"]),
                date.fromisoformat(lv["end_date"])
            )
        except Exception:
            ndays = "?"

        bg, tc = status_colours.get(status, ("#f5f8fb","#0d1f35"))

        with st.expander(f"{icon} **{name}** · {lt} · {sd}→{ed} ({ndays}d)", expanded=(status=="pending")):
            dc1, dc2, dc3, dc4 = st.columns(4)
            dc1.markdown(f"**Staff**  \n{name}")
            dc2.markdown(f"**Centre**  \n{centre}")
            dc3.markdown(f"**Leave type**  \n{lt}")
            dc4.markdown(
                f"**Status**  \n"
                f'<span style="background:{bg};color:{tc};padding:2px 9px;'
                f'border-radius:99px;font-size:0.78rem;font-weight:600;">'
                f'{icon} {status.title()}</span>',
                unsafe_allow_html=True
            )
            st.markdown(f"**Reason:** {reason}")

            reviewer = lv.get("reviewer")
            if reviewer and lv.get("review_notes"):
                st.markdown(f"**Manager's note:** {lv['review_notes']}")
            elif reviewer:
                rev_name = f"{reviewer.get('first_name','')} {reviewer.get('last_name','')}".strip()
                st.caption(f"Reviewed by {rev_name} on {fmt_date(lv.get('reviewed_at'))}")

            # Actions — only for pending requests
            if status == "pending":
                st.markdown("")
                ac1, ac2, ac3, _ = st.columns([1.2, 1.2, 1.2, 4])
                if ac1.button("✅  Approve", key=f"appr_{lv['id']}", use_container_width=True, type="primary"):
                    st.session_state.reviewing_leave_id = lv["id"]
                    st.session_state.review_action      = "approved"
                    st.session_state.page               = "leave_review"
                    st.rerun()
                if ac2.button("❌  Decline", key=f"decl_{lv['id']}", use_container_width=True):
                    st.session_state.reviewing_leave_id = lv["id"]
                    st.session_state.review_action      = "declined"
                    st.session_state.page               = "leave_review"
                    st.rerun()
                if ac3.button("👁  Full Review", key=f"full_{lv['id']}", use_container_width=True):
                    st.session_state.reviewing_leave_id = lv["id"]
                    st.session_state.review_action      = None
                    st.session_state.page               = "leave_review"
                    st.rerun()
