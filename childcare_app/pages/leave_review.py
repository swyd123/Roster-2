# pages/leave_review.py  —  Full review screen for a single leave request
import streamlit as st
from datetime import date
from utils.staff_queries import fetch_leave_by_id, update_leave_status
from utils.helpers import (
    fmt_date, fmt_leave_type, fmt_name,
    toast_success, toast_error, workdays_between,
)


def render():
    leave_id      = st.session_state.get("reviewing_leave_id")
    preset_action = st.session_state.get("review_action")

    if not leave_id:
        st.warning("No leave request selected.")
        if st.button("← Leave List"):
            st.session_state.page = "leave_list"; st.rerun()
        return

    bc, _ = st.columns([1, 10])
    with bc:
        st.markdown('<div class="back-btn">', unsafe_allow_html=True)
        if st.button("← Back", key="lr_back"):
            st.session_state.pop("reviewing_leave_id", None)
            st.session_state.pop("review_action", None)
            st.session_state.page = "leave_list"; st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    with st.spinner("Loading…"):
        try:
            lv = fetch_leave_by_id(leave_id)
        except Exception as e:
            toast_error(f"Could not load: {e}"); return

    if not lv:
        toast_error("Leave request not found.")
        st.session_state.page = "leave_list"; st.rerun(); return

    u      = lv.get("users") or {}
    name   = fmt_name(u)
    status = lv.get("status","")
    lt     = fmt_leave_type(lv.get("leave_type",""))
    sd_str = lv.get("start_date","")
    ed_str = lv.get("end_date","")
    sd     = fmt_date(sd_str)
    ed     = fmt_date(ed_str)
    reason = lv.get("reason") or "No reason provided."
    centre = (lv.get("centres") or {}).get("name","—")

    try:
        ndays = workdays_between(date.fromisoformat(sd_str), date.fromisoformat(ed_str))
    except Exception:
        ndays = "?"

    st.title("Leave Request Review")
    st.markdown(f'<p class="page-sub">{name} · {lt}</p>', unsafe_allow_html=True)
    st.markdown("---")

    # ── Request summary card ──────────────────────────────────────────
    status_icons   = {"pending":"🟡","approved":"✅","declined":"❌","cancelled":"⚫"}
    status_colours = {
        "pending": ("#fef3cd","#92510a"), "approved": ("#d4f0e4","#0f6b3a"),
        "declined": ("#fde8e8","#991b1b"), "cancelled": ("#e8eff5","#4a6079"),
    }
    bg, tc = status_colours.get(status, ("#f5f8fb","#0d1f35"))
    icon   = status_icons.get(status,"❓")

    st.markdown(
        f'<div style="background:#fff;border:1px solid #e4edf5;border-radius:12px;'
        f'padding:1.2rem 1.6rem;margin-bottom:1rem;">'
        f'<div style="display:flex;justify-content:space-between;align-items:flex-start;">'
        f'<div>'
        f'<div style="font-family:DM Serif Display,serif;font-size:1.2rem;color:#0d1f35;">{name}</div>'
        f'<div style="color:#7a90a8;font-size:0.85rem;margin-top:3px;">{lt} · {centre}</div>'
        f'</div>'
        f'<span style="background:{bg};color:{tc};padding:4px 12px;border-radius:99px;'
        f'font-size:0.8rem;font-weight:600;">{icon} {status.title()}</span>'
        f'</div></div>',
        unsafe_allow_html=True
    )

    rc1, rc2, rc3, rc4 = st.columns(4)
    rc1.metric("Start Date",       sd)
    rc2.metric("End Date",         ed)
    rc3.metric("Working Days",     str(ndays))
    rc4.metric("Leave Type",       lt)

    st.markdown("")
    st.markdown(f"**Reason provided:**  \n_{reason}_")

    reviewer = lv.get("reviewer")
    if reviewer and lv.get("review_notes"):
        rev_name = f"{reviewer.get('first_name','')} {reviewer.get('last_name','')}".strip()
        st.info(f"**Previously reviewed** by {rev_name}: _{lv['review_notes']}_")

    # ── Decision form (only for pending) ─────────────────────────────
    if status == "pending":
        st.markdown("---")
        st.markdown("### Your Decision")

        # Pre-select action if coming from list buttons
        action_choices = {"approve": "✅ Approve", "decline": "❌ Decline"}
        default_choice = "approve" if preset_action == "approved" else \
                         "decline" if preset_action == "declined" else "approve"

        with st.form("review_form"):
            decision = st.radio(
                "Decision *",
                options=["approve", "decline"],
                format_func=lambda x: action_choices[x],
                index=0 if default_choice == "approve" else 1,
                horizontal=True,
            )
            review_notes = st.text_area(
                "Manager's note",
                height=100,
                placeholder="Optional for approvals; recommended when declining.",
            )

            sc1, sc2 = st.columns(2)
            if sc1.form_submit_button("Submit Decision", type="primary", use_container_width=True):
                new_status = "approved" if decision == "approve" else "declined"
                try:
                    update_leave_status(
                        leave_id=leave_id,
                        new_status=new_status,
                        reviewer_user_id="system",
                        review_notes=review_notes,
                    )
                    toast_success(f"Leave request {new_status}.")
                    import time; time.sleep(0.6)
                    st.session_state.pop("reviewing_leave_id", None)
                    st.session_state.pop("review_action", None)
                    st.session_state.page = "leave_list"
                    st.rerun()
                except Exception as e:
                    toast_error(str(e))
            if sc2.form_submit_button("Cancel", use_container_width=True):
                st.session_state.pop("reviewing_leave_id", None)
                st.session_state.pop("review_action", None)
                st.session_state.page = "leave_list"
                st.rerun()
    else:
        st.info(f"This leave request has already been **{status}** and cannot be changed.")
        if st.button("← Back to Leave List"):
            st.session_state.page = "leave_list"; st.rerun()
