# pages/staff_list.py
import streamlit as st
import pandas as pd
from utils.staff_queries import fetch_all_staff, soft_delete_staff
from utils.helpers import (
    fmt_name, fmt_employment, fmt_role, fmt_date,
    active_badge, qual_risk_level, EMPLOYMENT_TYPE_KEYS, EMPLOYMENT_TYPES,
    toast_success, toast_error,
)


def render():
    # ── Header ───────────────────────────────────────────────────────
    col_h, col_btn = st.columns([4, 1])
    col_h.title("Staff")
    col_h.markdown('<p class="page-sub">All staff members in your organisation</p>',
                   unsafe_allow_html=True)
    with col_btn:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("➕  Add Staff", type="primary", use_container_width=True):
            st.session_state.page = "staff_add"
            st.rerun()

    # ── Load data ─────────────────────────────────────────────────────
    with st.spinner("Loading…"):
        try:
            staff = fetch_all_staff()
        except Exception as e:
            toast_error(f"Could not load staff: {e}")
            return

    # ── Metrics ───────────────────────────────────────────────────────
    total    = len(staff)
    active   = sum(1 for s in staff if (s.get("users") or {}).get("is_active"))
    ft       = sum(1 for s in staff if s.get("employment_type") == "full_time")
    casual   = sum(1 for s in staff if s.get("employment_type") == "casual")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total Staff",  total)
    m2.metric("Active",       active)
    m3.metric("Full Time",    ft)
    m4.metric("Casual",       casual)

    st.markdown("---")

    # ── Filters ───────────────────────────────────────────────────────
    fc1, fc2, fc3, fc4 = st.columns([3, 1.5, 1.5, 1.5])
    search    = fc1.text_input("🔍  Search", placeholder="Name or email…",
                                label_visibility="collapsed")
    et_filter = fc2.selectbox("Type", ["All"] + EMPLOYMENT_TYPE_KEYS,
                               format_func=lambda x: "All Types" if x == "All" else EMPLOYMENT_TYPES[x],
                               label_visibility="collapsed")
    status_filter = fc3.selectbox("Status", ["All", "Active", "Inactive"],
                                   label_visibility="collapsed")
    sort_by   = fc4.selectbox("Sort", ["Name A–Z", "Name Z–A", "Start Date", "Employment Type"],
                               label_visibility="collapsed")

    # ── Apply filters ─────────────────────────────────────────────────
    filtered = staff
    if search:
        t = search.lower()
        filtered = [s for s in filtered if
                    t in fmt_name(s).lower() or
                    t in (s.get("users") or {}).get("email","").lower() or
                    t in (s.get("employee_number") or "").lower()]
    if et_filter != "All":
        filtered = [s for s in filtered if s.get("employment_type") == et_filter]
    if status_filter == "Active":
        filtered = [s for s in filtered if (s.get("users") or {}).get("is_active")]
    elif status_filter == "Inactive":
        filtered = [s for s in filtered if not (s.get("users") or {}).get("is_active")]

    # Sorting
    if sort_by == "Name A–Z":
        filtered.sort(key=lambda s: fmt_name(s))
    elif sort_by == "Name Z–A":
        filtered.sort(key=lambda s: fmt_name(s), reverse=True)
    elif sort_by == "Start Date":
        filtered.sort(key=lambda s: s.get("employment_start_date") or "", reverse=True)
    elif sort_by == "Employment Type":
        filtered.sort(key=lambda s: s.get("employment_type") or "")

    if search or et_filter != "All" or status_filter != "All":
        st.caption(f"Showing {len(filtered)} of {total} staff members")

    # ── Export ────────────────────────────────────────────────────────
    if filtered:
        export_rows = []
        for s in filtered:
            u = s.get("users") or {}
            export_rows.append({
                "Name":            fmt_name(s),
                "Email":           u.get("email",""),
                "Phone":           u.get("phone",""),
                "Employment Type": fmt_employment(s.get("employment_type","")),
                "Employee #":      s.get("employee_number",""),
                "Start Date":      fmt_date(s.get("employment_start_date")),
                "Status":          "Active" if u.get("is_active") else "Inactive",
            })
        csv = pd.DataFrame(export_rows).to_csv(index=False)
        st.download_button("⬇️  Export CSV", data=csv,
                           file_name="staff_list.csv", mime="text/csv",
                           help="Download the filtered list as a spreadsheet")

    st.markdown("")

    # ── Empty state ───────────────────────────────────────────────────
    if not filtered:
        st.info("No staff members found. Use **➕ Add Staff** to get started.")
        return

    # ── Staff rows ────────────────────────────────────────────────────
    for s in filtered:
        u         = s.get("users") or {}
        name      = fmt_name(s)
        et        = fmt_employment(s.get("employment_type",""))
        is_active = u.get("is_active", False)
        email     = u.get("email","—")
        phone     = u.get("phone") or "—"
        start     = fmt_date(s.get("employment_start_date"))
        emp_num   = s.get("employee_number") or "—"

        # Role + centre from user_centre_roles (first active one)
        roles_list = [r for r in (s.get("user_centre_roles") or []) if r.get("is_active")]
        role_str   = fmt_role(roles_list[0]["role"]) if roles_list else "—"
        centre_str = (roles_list[0].get("centres") or {}).get("name","—") if roles_list else "—"
        room_str   = (roles_list[0].get("rooms") or {}).get("name","") if roles_list else ""

        # Status colour
        status_colour = "#d4f0e4" if is_active else "#fde8e8"
        status_text   = "Active" if is_active else "Inactive"
        status_tc     = "#0f6b3a" if is_active else "#991b1b"

        with st.expander(
            f"**{name}**  ·  {et}  ·  {centre_str}",
            expanded=False,
        ):
            # Top row — details grid
            d1, d2, d3, d4 = st.columns(4)
            d1.markdown(f"**Email**  \n{email}")
            d2.markdown(f"**Phone**  \n{phone}")
            d3.markdown(f"**Role**  \n{role_str}" + (f" · {room_str}" if room_str else ""))
            d4.markdown(f"**Start Date**  \n{start}")

            d5, d6, d7, d8 = st.columns(4)
            d5.markdown(f"**Employee #**  \n{emp_num}")
            d6.markdown(f"**Employment**  \n{et}")
            d7.markdown(
                f"**Status**  \n"
                f'<span style="background:{status_colour};color:{status_tc};'
                f'padding:2px 8px;border-radius:99px;font-size:0.78rem;font-weight:600;">'
                f'{status_text}</span>',
                unsafe_allow_html=True
            )
            d8.markdown(f"**Centre**  \n{centre_str}")

            if s.get("notes"):
                st.markdown(f"**Notes:** _{s['notes']}_")

            st.markdown("")

            # Action buttons
            ba1, ba2, ba3, _sp = st.columns([1, 1, 1, 4])

            with ba1:
                if st.button("👁  View Profile", key=f"view_{s['id']}", use_container_width=True):
                    st.session_state.viewing_staff_id = s["id"]
                    st.session_state.page = "staff_profile"
                    st.rerun()

            with ba2:
                if st.button("✏️  Edit", key=f"edit_{s['id']}", use_container_width=True):
                    st.session_state.editing_staff_id = s["id"]
                    st.session_state.page = "staff_profile"
                    st.session_state.profile_tab = "edit"
                    st.rerun()

            with ba3:
                confirm_key = f"confirm_del_{s['id']}"
                if st.session_state.get(confirm_key):
                    st.warning(f"Remove **{name}**?")
                    y, n = st.columns(2)
                    if y.button("Yes, remove", key=f"yes_{s['id']}", type="primary",
                                 use_container_width=True):
                        try:
                            soft_delete_staff(s["id"], u["id"])
                            toast_success(f"{name} has been removed.")
                            st.session_state.pop(confirm_key, None)
                            st.rerun()
                        except Exception as e:
                            toast_error(str(e))
                    if n.button("Cancel", key=f"no_{s['id']}", use_container_width=True):
                        st.session_state.pop(confirm_key, None)
                        st.rerun()
                else:
                    if st.button("🗑️  Remove", key=f"del_{s['id']}", use_container_width=True):
                        st.session_state[confirm_key] = True
                        st.rerun()
