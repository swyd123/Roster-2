# pages/staff_profile.py
# Tabbed staff profile: Overview | Qualifications | Availability | Leave | Edit

import streamlit as st
from datetime import date
from utils.staff_queries import (
    fetch_staff_by_id, update_staff_member, upsert_centre_role,
    fetch_qualifications_for_staff, fetch_qualification_types,
    add_qualification, update_qualification,
    verify_qualification, soft_delete_qualification,
    fetch_availability, upsert_availability,
    fetch_leave_requests, create_leave_request,
)
from utils.helpers import (
    fmt_name, fmt_date, fmt_employment, fmt_role, fmt_leave_type,
    active_badge, qual_risk_level, days_until,
    QUAL_STATUS_CONFIG, LEAVE_TYPE_KEYS, LEAVE_TYPES,
    EMPLOYMENT_TYPE_KEYS, EMPLOYMENT_TYPES, DAYS, DAYS_SHORT,
    toast_success, toast_error, toast_warn, workdays_between,
)
from components.staff_form import staff_form
from utils.break_preferences_queries import (
    fetch_all_break_prefs_for_user, upsert_break_prefs_bulk, DAY_NAMES,
)
import time


def render():
    profile_id = st.session_state.get("viewing_staff_id") or \
                 st.session_state.get("editing_staff_id")

    if not profile_id:
        st.warning("No staff member selected.")
        if st.button("← Staff List"):
            st.session_state.page = "staff_list"; st.rerun()
        return

    # Load record
    with st.spinner("Loading…"):
        try:
            staff = fetch_staff_by_id(profile_id)
        except Exception as e:
            toast_error(f"Could not load: {e}"); return

    if not staff:
        toast_error("Staff member not found.")
        if st.button("← Staff List"):
            st.session_state.page = "staff_list"; st.rerun()
        return

    user      = staff.get("users") or {}
    user_id   = user.get("id", "")
    name      = fmt_name(staff)
    is_active = user.get("is_active", False)

    # Role & centre — first active assignment
    roles_list = [r for r in (staff.get("user_centre_roles") or []) if r.get("is_active")]
    role_str   = fmt_role(roles_list[0]["role"]) if roles_list else "—"
    centre_str = (roles_list[0].get("centres") or {}).get("name", "—") if roles_list else "—"
    centre_id  = roles_list[0].get("centre_id") if roles_list else None

    # ── Back button ───────────────────────────────────────────────────
    bc, _ = st.columns([1, 10])
    with bc:
        st.markdown('<div class="back-btn">', unsafe_allow_html=True)
        if st.button("← Back", key="profile_back"):
            st.session_state.page = "staff_list"
            st.session_state.pop("viewing_staff_id", None)
            st.session_state.pop("editing_staff_id", None)
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    # ── Profile header card ───────────────────────────────────────────
    status_html = (
        '<span style="background:#d4f0e4;color:#0f6b3a;padding:3px 10px;'
        'border-radius:99px;font-size:0.78rem;font-weight:600;">Active</span>'
        if is_active else
        '<span style="background:#fde8e8;color:#991b1b;padding:3px 10px;'
        'border-radius:99px;font-size:0.78rem;font-weight:600;">Inactive</span>'
    )
    st.markdown(
        f'<div style="background:#ffffff;border:1px solid #e4edf5;border-radius:14px;'
        f'padding:1.4rem 1.8rem;margin-bottom:1.2rem;box-shadow:0 2px 8px rgba(13,31,53,0.06);">'
        f'<div style="display:flex;align-items:center;gap:1rem;">'
        f'<div style="width:52px;height:52px;border-radius:50%;background:#0d1f35;'
        f'display:flex;align-items:center;justify-content:center;'
        f'font-family:DM Serif Display,serif;font-size:1.3rem;color:#ffffff;">'
        f'{name[0].upper() if name else "?"}</div>'
        f'<div><div style="font-family:DM Serif Display,serif;font-size:1.4rem;'
        f'color:#0d1f35;">{name}</div>'
        f'<div style="font-size:0.85rem;color:#7a90a8;margin-top:2px;">'
        f'{role_str} · {centre_str}</div></div>'
        f'<div style="margin-left:auto;">{status_html}</div>'
        f'</div></div>',
        unsafe_allow_html=True,
    )

    # ── Alert when no centre is assigned ─────────────────────────────
    if not centre_id:
        st.warning(
            "⚠️ **No centre assigned.** "
            "Go to the **Edit** tab to assign this staff member to a centre. "
            "Availability and Leave tracking require a centre assignment."
        )

    # ── Tabs ──────────────────────────────────────────────────────────
    default_tab = st.session_state.pop("profile_tab", "overview")
    tab_labels  = ["📋 Overview", "🎓 Qualifications", "📅 Availability", "🏖️ Leave", "☕ Break Prefs", "✏️ Edit"]
    tab_map     = {"overview": 0, "quals": 1, "avail": 2, "leave": 3, "breaks": 4, "edit": 5}

    tabs = st.tabs(tab_labels)

    # ════════════════════════════════════════════════════════════════
    # TAB 1 — Overview
    # ════════════════════════════════════════════════════════════════
    with tabs[0]:
        st.markdown("")
        col_a, col_b = st.columns(2)

        with col_a:
            st.markdown('<p class="section-label">Personal Details</p>', unsafe_allow_html=True)
            _detail_row("Full name",     name)
            _detail_row("Email",         user.get("email", "—"))
            _detail_row("Phone",         user.get("phone") or "—")
            _detail_row("Date of birth", fmt_date(staff.get("date_of_birth")))

            st.markdown("")
            st.markdown('<p class="section-label">Emergency Contact</p>', unsafe_allow_html=True)
            _detail_row("Name",         staff.get("emergency_contact_name") or "—")
            _detail_row("Phone",        staff.get("emergency_contact_phone") or "—")
            _detail_row("Relationship", staff.get("emergency_contact_relationship") or "—")

        with col_b:
            st.markdown('<p class="section-label">Employment Details</p>', unsafe_allow_html=True)
            _detail_row("Employee #",  staff.get("employee_number") or "—")
            _detail_row("Type",        fmt_employment(staff.get("employment_type", "")))
            _detail_row("Start date",  fmt_date(staff.get("employment_start_date")))
            _detail_row("End date",    fmt_date(staff.get("employment_end_date")) if staff.get("employment_end_date") else "Current")
            _detail_row("Centre",      centre_str)
            _detail_row("Role",        role_str)

            # Compliance role badges
            is_rp = bool(staff.get("is_responsible_person", False))
            is_ns = bool(staff.get("is_nominated_supervisor", False))
            if is_rp or is_ns:
                badges = []
                if is_ns:
                    badges.append(
                        '<span style="background:#e0e7ff;color:#3730a3;padding:2px 9px;'
                        'border-radius:99px;font-size:0.75rem;font-weight:600;'
                        'margin-right:6px;">Nominated Supervisor</span>'
                    )
                if is_rp:
                    badges.append(
                        '<span style="background:#dbeafe;color:#1e40af;padding:2px 9px;'
                        'border-radius:99px;font-size:0.75rem;font-weight:600;">'
                        'Responsible Person</span>'
                    )
                st.markdown(
                    f'<div style="margin-top:6px;">{"".join(badges)}</div>',
                    unsafe_allow_html=True,
                )

            if staff.get("notes"):
                st.markdown("")
                st.markdown('<p class="section-label">Notes</p>', unsafe_allow_html=True)
                st.markdown(
                    f'<div style="background:#f5f8fb;border:1px solid #e4edf5;'
                    f'border-radius:8px;padding:0.75rem;font-size:0.88rem;color:#4a6079;">'
                    f'{staff["notes"]}</div>',
                    unsafe_allow_html=True,
                )

    # ════════════════════════════════════════════════════════════════
    # TAB 2 — Qualifications
    # ════════════════════════════════════════════════════════════════
    with tabs[1]:
        _render_qualifications(profile_id)

    # ════════════════════════════════════════════════════════════════
    # TAB 3 — Availability
    # ════════════════════════════════════════════════════════════════
    with tabs[2]:
        _render_availability(user_id, centre_id)

    # ════════════════════════════════════════════════════════════════
    # TAB 4 — Leave
    # ════════════════════════════════════════════════════════════════
    with tabs[3]:
        _render_leave(user_id, centre_id)

    # ════════════════════════════════════════════════════════════════
    # TAB 5 — Break Preferences
    # ════════════════════════════════════════════════════════════════
    with tabs[4]:
        _render_break_preferences(user_id, centre_id, staff)

    # ════════════════════════════════════════════════════════════════
    # TAB 6 — Edit
    # ════════════════════════════════════════════════════════════════
    with tabs[5]:
        st.markdown("")

        # Pass show_role_fields=True so centre/role can be added or changed.
        # The form pre-fills from staff["user_centre_roles"] in edit mode.
        values = staff_form(key_prefix="edit", defaults=staff, show_role_fields=True)

        if values:
            with st.spinner("Saving…"):
                try:
                    # Step 1 — update user + staff profile fields
                    update_staff_member(
                        profile_id=profile_id,
                        user_id=user_id,
                        first_name=values["first_name"],
                        last_name=values["last_name"],
                        email=values["email"],
                        phone=values["phone"],
                        date_of_birth=values["date_of_birth"],
                        employment_type=values["employment_type"],
                        employment_start_date=values["employment_start_date"],
                        employee_number=values["employee_number"],
                        emergency_contact_name=values["emergency_contact_name"],
                        emergency_contact_phone=values["emergency_contact_phone"],
                        emergency_contact_relationship=values["emergency_contact_relationship"],
                        notes=values["notes"],
                        is_active=values["is_active"],
                        allows_unpaid_break_opt_out=values.get("allows_unpaid_break_opt_out", False),
                        contracted_hours_per_week=values.get("contracted_hours_per_week", 0.0),
                        is_responsible_person=values.get("is_responsible_person", False),
                        is_nominated_supervisor=values.get("is_nominated_supervisor", False),
                    )

                    # Step 2 — upsert the centre role assignment.
                    # This inserts a new row if none exists, or updates the
                    # existing one — so this fixes missing centre assignments
                    # as well as changing them.
                    if values.get("centre_id"):
                        upsert_centre_role(
                            user_id=user_id,
                            centre_id=values["centre_id"],
                            role=values["role"],
                            primary_room_id=values.get("primary_room_id"),
                        )

                    toast_success("Profile saved.")
                    time.sleep(0.6)
                    st.rerun()

                except Exception as e:
                    err = str(e)
                    if "duplicate" in err.lower() or "unique" in err.lower():
                        toast_error(f"Email {values['email']} is already in use.")
                    else:
                        toast_error(f"Could not save: {err}")


# ── Helper: detail row ────────────────────────────────────────────────────────
def _detail_row(label: str, value: str):
    st.markdown(
        f'<div style="display:flex;gap:0.5rem;margin-bottom:0.55rem;">'
        f'<span style="font-size:0.8rem;font-weight:600;color:#7a90a8;'
        f'min-width:120px;padding-top:1px;">{label}</span>'
        f'<span style="font-size:0.88rem;color:#1e3a55;">{value}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )


# ════════════════════════════════════════════════════════════════════════════
# QUALIFICATIONS sub-section
# ════════════════════════════════════════════════════════════════════════════
def _render_qualifications(profile_id: str):
    st.markdown("")

    try:
        quals = fetch_qualifications_for_staff(profile_id)
    except Exception as e:
        toast_error(f"Could not load qualifications: {e}"); return

    expired  = [q for q in quals if qual_risk_level(q.get("expiry_date"), q.get("status", "")) == "expired"]
    critical = [q for q in quals if qual_risk_level(q.get("expiry_date"), q.get("status", "")) == "critical"]
    warning  = [q for q in quals if qual_risk_level(q.get("expiry_date"), q.get("status", "")) == "warning"]

    if expired:
        st.error(f"❌ **{len(expired)} qualification(s) expired** — immediate action required.")
    if critical:
        st.warning(f"⚠️ **{len(critical)} qualification(s) expiring within 30 days.**")
    elif warning:
        st.info(f"ℹ️ {len(warning)} qualification(s) expire within 60 days.")

    hc1, hc2 = st.columns([5, 1])
    hc1.markdown(f"**{len(quals)} qualification(s) on record**")
    with hc2:
        if st.button("➕  Add Qualification", key="add_qual_btn", use_container_width=True, type="primary"):
            st.session_state["show_add_qual"] = True

    if st.session_state.get("show_add_qual"):
        _render_add_qual_form(profile_id)

    st.markdown("")

    if not quals:
        st.info("No qualifications recorded yet. Click **➕ Add Qualification** to add the first one.")
        return

    for q in quals:
        qt       = q.get("qualification_types") or {}
        qt_name  = qt.get("name", "Unknown")
        status   = q.get("status", "")
        expiry   = q.get("expiry_date")
        d_until  = days_until(expiry)
        risk     = qual_risk_level(expiry, status)
        cfg      = QUAL_STATUS_CONFIG.get(status, {})
        icon     = cfg.get("icon", "❓")

        if not expiry:
            expiry_display = "Does not expire"
        elif risk == "expired":
            expiry_display = f"❌ Expired {fmt_date(expiry)}"
        elif risk == "critical":
            expiry_display = f"🔴 {fmt_date(expiry)} ({d_until}d)"
        elif risk == "warning":
            expiry_display = f"🟡 {fmt_date(expiry)} ({d_until}d)"
        else:
            expiry_display = fmt_date(expiry)

        verifier     = q.get("users")
        verified_str = (
            f"Verified by {verifier['first_name']} {verifier['last_name']} on {fmt_date(q.get('verified_at'))}"
            if verifier and q.get("verified_at") else "Not verified"
        )

        with st.expander(f"{icon} **{qt_name}** — {expiry_display}",
                         expanded=(risk in ("expired", "critical"))):
            qc1, qc2, qc3 = st.columns(3)
            qc1.markdown(f"**Issuing body**  \n{q.get('issuing_body') or '—'}")
            qc2.markdown(f"**Issue date**  \n{fmt_date(q.get('issue_date'))}")
            qc3.markdown(f"**Cert #**  \n{q.get('certificate_number') or '—'}")

            qc4, qc5, qc6 = st.columns(3)
            qc4.markdown(f"**Status**  \n{icon} {cfg.get('label', status)}")
            qc5.markdown(f"**Verification**  \n{verified_str}")
            if q.get("document_url"):
                qc6.markdown(f"**Document**  \n[📄 View document]({q['document_url']})")

            if q.get("notes"):
                st.markdown(f"_Notes: {q['notes']}_")

            st.markdown("")
            ab1, ab2, ab3, _ = st.columns([1, 1, 1, 4])

            if status == "pending_verification":
                if ab1.button("✅  Verify", key=f"verify_{q['id']}", use_container_width=True, type="primary"):
                    try:
                        verify_qualification(q["id"], "system")
                        toast_success("Marked as verified.")
                        st.rerun()
                    except Exception as e:
                        toast_error(str(e))

            edit_key = f"edit_qual_{q['id']}"
            if ab2.button("✏️  Edit", key=f"eqbtn_{q['id']}", use_container_width=True):
                st.session_state[edit_key] = not st.session_state.get(edit_key, False)
                st.rerun()

            del_key = f"del_qual_{q['id']}"
            if st.session_state.get(del_key):
                st.warning("Delete this qualification record?")
                dy, dn = st.columns(2)
                if dy.button("Delete", key=f"dqy_{q['id']}", type="primary", use_container_width=True):
                    try:
                        soft_delete_qualification(q["id"])
                        toast_success("Qualification removed.")
                        st.session_state.pop(del_key, None)
                        st.rerun()
                    except Exception as e:
                        toast_error(str(e))
                if dn.button("Cancel", key=f"dqn_{q['id']}", use_container_width=True):
                    st.session_state.pop(del_key, None); st.rerun()
            else:
                if ab3.button("🗑️  Delete", key=f"delbtn_{q['id']}", use_container_width=True):
                    st.session_state[del_key] = True; st.rerun()

            if st.session_state.get(edit_key):
                st.markdown("---")
                st.markdown("**Edit qualification details**")
                with st.form(key=f"eq_form_{q['id']}"):
                    ec1, ec2 = st.columns(2)
                    raw_issue  = q.get("issue_date")
                    raw_expiry = q.get("expiry_date")
                    new_issue  = ec1.date_input(
                        "Issue date",
                        value=date.fromisoformat(raw_issue[:10]) if raw_issue else None,
                        key=f"eqi_{q['id']}", format="DD/MM/YYYY",
                    )
                    new_expiry = ec2.date_input(
                        "Expiry date",
                        value=date.fromisoformat(raw_expiry[:10]) if raw_expiry else None,
                        key=f"eqe_{q['id']}", format="DD/MM/YYYY",
                    )
                    new_body  = st.text_input("Issuing body", value=q.get("issuing_body", "") or "", key=f"eqb_{q['id']}")
                    new_cert  = st.text_input("Certificate #", value=q.get("certificate_number", "") or "", key=f"eqc_{q['id']}")
                    new_notes = st.text_area("Notes", value=q.get("notes", "") or "", key=f"eqn_{q['id']}", height=70)
                    if st.form_submit_button("Save changes", type="primary"):
                        try:
                            update_qualification(
                                q["id"],
                                new_issue.isoformat() if new_issue else None,
                                new_expiry.isoformat() if new_expiry else None,
                                new_body, new_cert, new_notes,
                            )
                            toast_success("Qualification updated.")
                            st.session_state.pop(edit_key, None)
                            st.rerun()
                        except Exception as e:
                            toast_error(str(e))


def _render_add_qual_form(profile_id: str):
    try:
        qual_types = fetch_qualification_types()
    except Exception as e:
        toast_error(f"Could not load qualification types: {e}"); return

    qt_opts = {qt["id"]: qt["name"] for qt in qual_types}
    qt_req  = {qt["id"]: qt["requires_expiry"] for qt in qual_types}

    with st.form(key="add_qual_form"):
        st.markdown("**Add new qualification**")
        aq1, aq2 = st.columns(2)
        selected_type = aq1.selectbox(
            "Qualification type *",
            options=list(qt_opts.keys()),
            format_func=lambda x: qt_opts[x],
            key="aq_type",
        )
        issuing_body = aq2.text_input("Issuing body", placeholder="e.g. St John Ambulance", key="aq_body")

        aq3, aq4 = st.columns(2)
        issue_date  = aq3.date_input("Issue date",  value=None, key="aq_issue",  format="DD/MM/YYYY")
        expiry_date = aq4.date_input("Expiry date", value=None, key="aq_expiry", format="DD/MM/YYYY",
                                      help="Required for this qualification type" if selected_type and qt_req.get(selected_type) else "")
        cert_num  = st.text_input("Certificate / registration number", key="aq_cert")
        aq_notes  = st.text_area("Notes", key="aq_notes", height=70)

        sc1, sc2 = st.columns(2)
        if sc1.form_submit_button("Upload & Save", type="primary", use_container_width=True):
            if selected_type and qt_req.get(selected_type) and not expiry_date:
                toast_error("Expiry date is required for this qualification type.")
            else:
                try:
                    add_qualification(
                        profile_id=profile_id,
                        qual_type_id=selected_type,
                        issue_date=issue_date.isoformat() if issue_date else None,
                        expiry_date=expiry_date.isoformat() if expiry_date else None,
                        issuing_body=issuing_body,
                        certificate_number=cert_num,
                        document_url=None,
                        document_filename=None,
                        notes=aq_notes,
                    )
                    toast_success("Qualification added.")
                    st.session_state.pop("show_add_qual", None)
                    st.rerun()
                except Exception as e:
                    toast_error(str(e))
        if sc2.form_submit_button("Cancel", use_container_width=True):
            st.session_state.pop("show_add_qual", None); st.rerun()


# ════════════════════════════════════════════════════════════════════════════
# AVAILABILITY sub-section
# ════════════════════════════════════════════════════════════════════════════
def _render_availability(user_id: str, centre_id: str | None):
    st.markdown("")

    if not centre_id:
        st.info(
            "No centre assigned. Go to the **Edit** tab to assign this staff member "
            "to a centre, then return here to set their availability."
        )
        return

    try:
        existing = fetch_availability(user_id, centre_id)
    except Exception as e:
        toast_error(f"Could not load availability: {e}"); return

    avail_map = {a["day_of_week"]: a for a in existing}

    st.markdown("**Weekly availability pattern**")
    st.caption(
        "Set which days and hours this staff member is available to work. "
        "The roster builder uses this to flag scheduling conflicts."
    )

    with st.form("availability_form"):
        from datetime import date as _date
        rows = []
        display_order = [1, 2, 3, 4, 5, 6, 0]  # Mon to Sun

        for dow in display_order:
            rec      = avail_map.get(dow, {})
            day_name = DAYS[dow]
            av1, av2, av3, av4 = st.columns([1.5, 1, 1, 2])

            is_avail = av1.toggle(
                day_name,
                value=rec.get("is_available", dow in [1, 2, 3, 4, 5]),
                key=f"avail_toggle_{dow}",
            )

            from_time  = None
            until_time = None
            if is_avail:
                raw_from  = rec.get("available_from", "06:30")
                raw_until = rec.get("available_until", "18:00")
                try:
                    from datetime import time as _time
                    def parse_t(s):
                        parts = str(s).split(":")
                        return _time(int(parts[0]), int(parts[1]))
                    from_default  = parse_t(raw_from)
                    until_default = parse_t(raw_until)
                except Exception:
                    from datetime import time as _time
                    from_default  = _time(6, 30)
                    until_default = _time(18, 0)

                from_time  = av2.time_input("From",  value=from_default,  key=f"af_{dow}")
                until_time = av3.time_input("Until", value=until_default, key=f"au_{dow}")
                av4.text_input(
                    "Notes", value=rec.get("notes", "") or "",
                    key=f"an_{dow}", placeholder="e.g. School pickup at 3pm",
                    label_visibility="collapsed",
                )

            rows.append({
                "user_id":        user_id,
                "centre_id":      centre_id,
                "day_of_week":    dow,
                "is_available":   is_avail,
                "available_from": from_time.strftime("%H:%M:%S") if from_time else None,
                "available_until":until_time.strftime("%H:%M:%S") if until_time else None,
                "effective_from": _date.today().isoformat(),
                "effective_until":None,
                "notes":          st.session_state.get(f"an_{dow}", "") or None,
            })

        if st.form_submit_button("💾  Save Availability", type="primary", use_container_width=True):
            try:
                upsert_availability(rows)
                toast_success("Availability saved.")
                st.rerun()
            except Exception as e:
                toast_error(f"Could not save: {e}")


# ════════════════════════════════════════════════════════════════════════════
# LEAVE sub-section
# ════════════════════════════════════════════════════════════════════════════
def _render_leave(user_id: str, centre_id: str | None):
    st.markdown("")

    if not centre_id:
        st.info(
            "No centre assigned. Go to the **Edit** tab to assign this staff member "
            "to a centre, then return here to manage leave."
        )
        return

    lh1, lh2 = st.columns([5, 1])
    lh1.markdown("**Leave history for this staff member**")
    with lh2:
        if st.button("➕  Add Leave", key="add_leave_profile", use_container_width=True, type="primary"):
            st.session_state["show_add_leave_profile"] = not st.session_state.get("show_add_leave_profile", False)

    if st.session_state.get("show_add_leave_profile"):
        with st.form("add_leave_profile_form"):
            st.markdown("**Submit leave request**")
            al1, al2 = st.columns(2)
            leave_type = al1.selectbox(
                "Leave type *", LEAVE_TYPE_KEYS,
                format_func=lambda x: LEAVE_TYPES[x], key="alp_type",
            )
            al2.markdown("")

            al3, al4 = st.columns(2)
            start_d = al3.date_input("Start date *", value=date.today(), key="alp_start", format="DD/MM/YYYY")
            end_d   = al4.date_input("End date *",   value=date.today(), key="alp_end",   format="DD/MM/YYYY")

            reason = st.text_area(
                "Reason", key="alp_reason", height=70,
                placeholder="Optional — helps the manager make a decision",
            )

            sc1, sc2 = st.columns(2)
            if sc1.form_submit_button("Submit Request", type="primary", use_container_width=True):
                if end_d < start_d:
                    toast_error("End date cannot be before start date.")
                else:
                    try:
                        create_leave_request(user_id, centre_id, leave_type,
                                             start_d.isoformat(), end_d.isoformat(), reason)
                        ndays = workdays_between(start_d, end_d)
                        toast_success(f"Leave request submitted ({ndays} working day(s)).")
                        st.session_state.pop("show_add_leave_profile", None)
                        st.rerun()
                    except Exception as e:
                        toast_error(str(e))
            if sc2.form_submit_button("Cancel", use_container_width=True):
                st.session_state.pop("show_add_leave_profile", None); st.rerun()

    try:
        leaves = fetch_leave_requests(user_id=user_id)
    except Exception as e:
        toast_error(f"Could not load leave: {e}"); return

    if not leaves:
        st.info("No leave requests on record for this staff member.")
        return

    status_icons = {"pending": "🟡", "approved": "✅", "declined": "❌", "cancelled": "⚫"}
    for lv in leaves:
        status = lv.get("status", "")
        icon   = status_icons.get(status, "❓")
        lt     = fmt_leave_type(lv.get("leave_type", ""))
        sd     = fmt_date(lv.get("start_date"))
        ed     = fmt_date(lv.get("end_date"))
        reason = lv.get("reason") or "—"

        try:
            ndays = workdays_between(
                date.fromisoformat(lv["start_date"]),
                date.fromisoformat(lv["end_date"]),
            )
        except Exception:
            ndays = "?"

        with st.expander(f"{icon} **{lt}** — {sd} to {ed} ({ndays} days)  ·  {status.title()}"):
            lc1, lc2, lc3 = st.columns(3)
            lc1.markdown(f"**Leave type**  \n{lt}")
            lc2.markdown(f"**Duration**  \n{ndays} working day(s)")
            lc3.markdown(f"**Status**  \n{icon} {status.title()}")
            st.markdown(f"**Reason:** {reason}")
            if lv.get("review_notes"):
                st.markdown(f"**Manager's note:** {lv['review_notes']}")


# ─────────────────────────────────────────────────────────────────────────────
# BREAK PREFERENCES TAB
# ─────────────────────────────────────────────────────────────────────────────

def _render_break_preferences(user_id: str, centre_id: str, staff: dict):
    """
    Tab 5 — Weekly unpaid break opt-out preferences.
    Managers set which weekdays this educator opts out of the unpaid meal break.
    Paid rest breaks are always unaffected.
    Only visible/editable when the profile has allows_unpaid_break_opt_out=True.
    """
    allows = bool(staff.get("allows_unpaid_break_opt_out", False))

    st.markdown("### ☕ Break Preferences")
    st.caption(
        "Set recurring weekday preferences for the unpaid meal break opt-out. "
        "These defaults are applied automatically when a shift is created. "
        "The roster form can override them per shift."
    )

    if not allows:
        st.info(
            "This educator's profile does not currently allow unpaid break opt-out. "
            "Enable **Allow unpaid break opt-out** in the **✏️ Edit** tab first."
        )
        return

    st.warning(
        "⚠️ **Confirm each opted-out day complies with the applicable award, "
        "enterprise agreement, and employee agreement.** "
        "Paid rest break entitlement is always retained."
    )

    # Load existing preferences
    try:
        existing_rows = fetch_all_break_prefs_for_user(user_id, centre_id)
    except Exception as e:
        st.error(f"Could not load break preferences: {e}")
        return

    # Build current {dow: bool} from most recent row per day
    today      = date.today().isoformat()
    current: dict[int, bool] = {}
    for row in existing_rows:
        dow   = row.get("day_of_week")
        until = row.get("effective_until")
        if dow is None or dow in current:
            continue
        if until and until < today:
            continue
        current[dow] = bool(row.get("unpaid_break_opt_out", False))

    st.markdown("**Select which weekdays to opt out of the unpaid meal break:**")
    st.caption("Paid rest break is always required regardless of these settings.")

    with st.form(key="break_prefs_form"):
        new_prefs: dict[int, bool] = {}
        cols = st.columns(7)
        # Mon=1 … Sat=6, Sun=0  (Python isoweekday() % 7)
        for i, (col, (dow, day_name)) in enumerate(
            zip(cols, [(1,"Mon"),(2,"Tue"),(3,"Wed"),(4,"Thu"),(5,"Fri"),(6,"Sat"),(0,"Sun")])
        ):
            full_name = DAY_NAMES.get(dow, day_name)
            val       = current.get(dow, False)
            new_prefs[dow] = col.checkbox(
                full_name,
                value=val,
                key=f"bp_{dow}",
            )

        eff_from = st.date_input(
            "Effective from",
            value=date.today(),
            key="bp_eff_from",
            format="DD/MM/YYYY",
            help="Preferences will apply from this date. Use today to take effect immediately.",
        )
        bp_notes = st.text_input(
            "Notes (optional)",
            placeholder="e.g. per EA clause 12.3",
            key="bp_notes",
        )

        saved = st.form_submit_button("💾 Save Break Preferences", type="primary",
                                       use_container_width=False)

    if saved:
        with st.spinner("Saving…"):
            try:
                n = upsert_break_prefs_bulk(
                    user_id=user_id,
                    centre_id=centre_id,
                    prefs=new_prefs,
                    effective_from=eff_from.isoformat(),
                    notes=bp_notes,
                )
                opted_days = [DAY_NAMES.get(d,"") for d, v in new_prefs.items() if v]
                toast_success(
                    f"Break preferences saved for {n} day(s). "
                    + (f"Opted out: {', '.join(opted_days)}." if opted_days else "No days opted out.")
                )
                st.rerun()
            except Exception as e:
                st.error(f"Could not save: {e}")

    # ── History ────────────────────────────────────────────────────────
    if existing_rows:
        with st.expander("📜 Preference history", expanded=False):
            for row in existing_rows:
                dow      = row.get("day_of_week", "?")
                day_name = DAY_NAMES.get(dow, str(dow))
                opt_out  = row.get("unpaid_break_opt_out", False)
                eff_f    = row.get("effective_from","")
                eff_u    = row.get("effective_until","—")
                notes    = row.get("notes","") or ""
                st.markdown(
                    f'<div style="font-size:0.82rem;padding:0.25rem 0;'
                    f'border-bottom:1px solid #f0f4f8;">'
                    f'<strong>{day_name}</strong> · '
                    f'{"✅ Opted out" if opt_out else "❌ Not opted out"} · '
                    f'From {eff_f} until {eff_u}'
                    + (f' · {notes}' if notes else '')
                    + f'</div>',
                    unsafe_allow_html=True,
                )
