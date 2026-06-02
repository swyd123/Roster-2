# pages/centres.py — Centre Management
# Create, view, edit, and soft-delete childcare centres.
# All centres are scoped to the ORGANISATION_ID from secrets / .env.

import streamlit as st
from datetime import datetime, time as _time

from utils.centre_queries import (
    fetch_all_centres, create_centre, update_centre, soft_delete_centre,
)
from utils.helpers import toast_success, toast_error, fmt_date


# ── Constants ─────────────────────────────────────────────────────────────────

AU_STATES = ["ACT", "NSW", "NT", "QLD", "SA", "TAS", "VIC", "WA"]

AU_TIMEZONES = [
    "Australia/Sydney",
    "Australia/Melbourne",
    "Australia/Brisbane",
    "Australia/Perth",
    "Australia/Adelaide",
    "Australia/Hobart",
    "Australia/Darwin",
    "Australia/Lord_Howe",
]

WEEKDAYS = {
    1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu",
    5: "Fri", 6: "Sat", 7: "Sun",
}


# ── Page entry point ──────────────────────────────────────────────────────────

def render():
    # ── Header ────────────────────────────────────────────────────────
    hc, btn_c = st.columns([4, 1])
    hc.title("Centres")
    hc.markdown(
        '<p class="page-sub">Manage your childcare centre locations. '
        "Each centre has its own rooms, staff, and rosters.</p>",
        unsafe_allow_html=True,
    )
    with btn_c:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("➕  Add Centre", type="primary", use_container_width=True,
                     key="show_add_centre_btn"):
            st.session_state["show_add_centre"] = not st.session_state.get("show_add_centre", False)
            st.rerun()

    # ── Inline create form ────────────────────────────────────────────
    if st.session_state.get("show_add_centre"):
        st.markdown("---")
        _render_centre_form(mode="create")
        st.markdown("---")

    # ── Load all centres ──────────────────────────────────────────────
    with st.spinner("Loading centres…"):
        try:
            centres = fetch_all_centres()
        except Exception as e:
            toast_error(f"Could not load centres: {e}")
            return

    # ── Empty state ───────────────────────────────────────────────────
    if not centres:
        st.markdown("")
        st.info(
            "No centres found for this organisation. "
            "Click **➕ Add Centre** above to create your first one."
        )
        _render_getting_started_tip()
        return

    # ── Summary metrics ───────────────────────────────────────────────
    total_places = sum(c.get("approved_places") or 0 for c in centres)
    total_rooms  = sum(c.get("room_count", 0) for c in centres)

    m1, m2, m3 = st.columns(3)
    m1.metric("Centres",          len(centres))
    m2.metric("Total Rooms",      total_rooms)
    m3.metric("Total Approved Places", total_places if total_places else "—")

    st.markdown("---")

    # ── Centre cards ──────────────────────────────────────────────────
    for centre in centres:
        _render_centre_card(centre)


# ── Centre card ───────────────────────────────────────────────────────────────

def _render_centre_card(centre: dict):
    cid          = centre["id"]
    name         = centre.get("name", "Unnamed Centre")
    suburb       = centre.get("suburb", "")
    state        = centre.get("state", "")
    phone        = centre.get("phone") or "—"
    email        = centre.get("email") or "—"
    licence      = centre.get("licence_number") or "—"
    approved     = centre.get("approved_places")
    room_count   = centre.get("room_count", 0)
    timezone     = centre.get("timezone") or "Australia/Sydney"
    opens_at     = centre.get("opens_at")
    closes_at    = centre.get("closes_at")
    op_days      = centre.get("operating_days") or [1, 2, 3, 4, 5]
    created_at   = fmt_date(centre.get("created_at"))

    location_str = ", ".join(filter(None, [suburb, state])) or "No address set"
    hours_str    = (
        f"{_fmt_time(opens_at)} – {_fmt_time(closes_at)}"
        if opens_at and closes_at else "Hours not set"
    )
    days_str = "  ".join(
        f'<span style="background:#e0e7ff;color:#3730a3;padding:1px 6px;'
        f'border-radius:4px;font-size:0.72rem;font-weight:600;">{WEEKDAYS[d]}</span>'
        for d in sorted(op_days) if d in WEEKDAYS
    )

    # Card header (always visible)
    st.markdown(
        f'<div style="border:1px solid #e4edf5;border-left:5px solid #0d1f35;'
        f'border-radius:0 10px 10px 0;padding:0.85rem 1.2rem;'
        f'background:#fff;box-shadow:0 1px 4px rgba(13,31,53,0.05);margin-bottom:0.25rem;">'
        f'<div style="display:flex;align-items:center;gap:1rem;flex-wrap:wrap;">'
        f'<div>'
        f'<span style="font-family:DM Serif Display,serif;font-size:1.05rem;'
        f'color:#0d1f35;">{name}</span>'
        f'<span style="font-size:0.82rem;color:#7a90a8;margin-left:0.75rem;">'
        f'📍 {location_str}</span>'
        f'</div>'
        f'<div style="margin-left:auto;display:flex;gap:0.5rem;align-items:center;">'
        f'<span style="font-size:0.78rem;color:#4a6079;">'
        f'🏠 {room_count} room{"s" if room_count != 1 else ""}'
        + (f' · 👶 {approved} places' if approved else '')
        + f'</span>'
        f'</div></div></div>',
        unsafe_allow_html=True,
    )

    # Expandable detail section
    with st.expander("", expanded=False):
        dc1, dc2, dc3 = st.columns(3)

        with dc1:
            st.markdown('<p class="section-label">Contact</p>', unsafe_allow_html=True)
            st.markdown(f"**Phone:** {phone}")
            st.markdown(f"**Email:** {email}")
            st.markdown(f"**Licence:** {licence}")

        with dc2:
            st.markdown('<p class="section-label">Address</p>', unsafe_allow_html=True)
            addr_parts = [
                centre.get("address_line_1"),
                centre.get("address_line_2"),
                " ".join(filter(None, [suburb, state, centre.get("postcode")])),
            ]
            for part in addr_parts:
                if part and part.strip():
                    st.markdown(part)
            if not any(addr_parts):
                st.markdown("No address recorded")

        with dc3:
            st.markdown('<p class="section-label">Operating Hours</p>', unsafe_allow_html=True)
            st.markdown(f"**Hours:** {hours_str}")
            st.markdown(f"**Timezone:** {timezone.replace('Australia/','')}")
            st.markdown(
                f"**Days:** {days_str}" if days_str else "**Days:** Not set",
                unsafe_allow_html=True,
            )

        st.caption(f"Created {created_at}  ·  ID: `{cid}`")
        st.markdown("")

        # Action buttons
        ab1, ab2, ab3, _ = st.columns([1.2, 1.2, 1.2, 4])

        if ab1.button("✏️  Edit", key=f"edit_centre_{cid}", use_container_width=True):
            st.session_state[f"edit_centre_{cid}"] = not st.session_state.get(f"edit_centre_{cid}", False)
            st.rerun()

        if ab2.button("🚪  Rooms", key=f"goto_rooms_{cid}", use_container_width=True,
                      help="Go to Rooms for this centre"):
            st.session_state["selected_centre_id"] = cid
            st.session_state.page = "rooms_list"
            st.rerun()

        del_key = f"confirm_del_centre_{cid}"
        if st.session_state.get(del_key):
            st.warning(
                f"**Delete '{name}'?** This will hide the centre from the app. "
                "Existing rooms, staff and rosters are not removed."
            )
            dy, dn = st.columns(2)
            if dy.button("Delete", key=f"do_del_{cid}", type="primary", use_container_width=True):
                try:
                    soft_delete_centre(cid)
                    toast_success(f"Centre '{name}' removed.")
                    st.session_state.pop(del_key, None)
                    st.rerun()
                except Exception as e:
                    toast_error(str(e))
            if dn.button("Cancel", key=f"cancel_del_{cid}", use_container_width=True):
                st.session_state.pop(del_key, None)
                st.rerun()
        else:
            if ab3.button("🗑️  Delete", key=f"del_centre_{cid}", use_container_width=True):
                st.session_state[del_key] = True
                st.rerun()

        # Inline edit form
        if st.session_state.get(f"edit_centre_{cid}"):
            st.markdown("---")
            _render_centre_form(mode="edit", existing=centre)


# ── Shared create / edit form ─────────────────────────────────────────────────

def _render_centre_form(mode: str = "create", existing: dict | None = None):
    """
    Renders the centre create or edit form.

    mode     : "create" or "edit"
    existing : centre dict to pre-fill (edit mode only)
    """
    is_edit = mode == "edit"
    d       = existing or {}
    cid     = d.get("id", "")

    form_key = f"centre_form_{cid}" if is_edit else "centre_form_create"

    st.markdown(
        f"### {'Edit Centre: ' + d.get('name', '') if is_edit else 'Add New Centre'}"
    )

    with st.form(key=form_key, clear_on_submit=False):

        # ── Identity ──────────────────────────────────────────────────
        st.markdown('<p class="section-label">Centre Identity</p>', unsafe_allow_html=True)
        name = st.text_input(
            "Centre name *",
            value=d.get("name", ""),
            placeholder="e.g. Sunflower Early Learning Centre",
            key=f"cf_name_{cid}",
        )

        ic1, ic2 = st.columns(2)
        licence_number = ic1.text_input(
            "Licence / approval number",
            value=d.get("licence_number", "") or "",
            placeholder="e.g. SE-12345",
            key=f"cf_lic_{cid}",
        )
        approved_places = ic2.number_input(
            "Approved places",
            min_value=0, max_value=500,
            value=d.get("approved_places") or 0,
            key=f"cf_places_{cid}",
            help="Total licensed capacity across all rooms.",
        )

        st.divider()

        # ── Address ───────────────────────────────────────────────────
        st.markdown('<p class="section-label">Address</p>', unsafe_allow_html=True)
        address_line_1 = st.text_input(
            "Street address",
            value=d.get("address_line_1", "") or "",
            placeholder="e.g. 42 Wattle Street",
            key=f"cf_addr1_{cid}",
        )
        address_line_2 = st.text_input(
            "Address line 2",
            value=d.get("address_line_2", "") or "",
            placeholder="e.g. Suite 3",
            key=f"cf_addr2_{cid}",
        )

        ac1, ac2, ac3 = st.columns(3)
        suburb = ac1.text_input(
            "Suburb",
            value=d.get("suburb", "") or "",
            placeholder="e.g. Surry Hills",
            key=f"cf_suburb_{cid}",
        )
        cur_state   = d.get("state") or "NSW"
        state_idx   = AU_STATES.index(cur_state) if cur_state in AU_STATES else 1
        state       = ac2.selectbox(
            "State *", options=AU_STATES, index=state_idx, key=f"cf_state_{cid}",
        )
        postcode    = ac3.text_input(
            "Postcode",
            value=d.get("postcode", "") or "",
            placeholder="e.g. 2010",
            key=f"cf_post_{cid}",
        )

        st.divider()

        # ── Contact ───────────────────────────────────────────────────
        st.markdown('<p class="section-label">Contact Details</p>', unsafe_allow_html=True)
        cc1, cc2 = st.columns(2)
        phone = cc1.text_input(
            "Phone",
            value=d.get("phone", "") or "",
            placeholder="02 9XXX XXXX",
            key=f"cf_phone_{cid}",
        )
        email = cc2.text_input(
            "Email",
            value=d.get("email", "") or "",
            placeholder="info@yourcentre.com.au",
            key=f"cf_email_{cid}",
        )

        st.divider()

        # ── Operating hours ───────────────────────────────────────────
        st.markdown('<p class="section-label">Operating Hours</p>', unsafe_allow_html=True)
        st.caption(
            "These hours are used by the rostering engine to classify "
            "opening and closing shifts automatically."
        )

        hc1, hc2, hc3 = st.columns(3)

        # Parse existing times safely
        def _parse_time(t_str) -> _time | None:
            if not t_str:
                return None
            try:
                parts = str(t_str).split(":")
                return _time(int(parts[0]), int(parts[1]))
            except Exception:
                return None

        opens_default  = _parse_time(d.get("opens_at"))  or _time(7, 0)
        closes_default = _parse_time(d.get("closes_at")) or _time(18, 0)

        opens_at  = hc1.time_input("Opens at",  value=opens_default,  key=f"cf_open_{cid}")
        closes_at = hc2.time_input("Closes at", value=closes_default, key=f"cf_close_{cid}")

        cur_tz  = d.get("timezone") or "Australia/Sydney"
        tz_idx  = AU_TIMEZONES.index(cur_tz) if cur_tz in AU_TIMEZONES else 0
        timezone = hc3.selectbox(
            "Timezone *", options=AU_TIMEZONES, index=tz_idx,
            format_func=lambda x: x.replace("Australia/", ""),
            key=f"cf_tz_{cid}",
        )

        st.markdown('<p class="section-label" style="margin-top:0.6rem;">Operating Days</p>',
                    unsafe_allow_html=True)
        cur_days = d.get("operating_days") or [1, 2, 3, 4, 5]
        day_cols = st.columns(7)
        selected_days = []
        for i, (day_num, day_label) in enumerate(WEEKDAYS.items()):
            checked = day_cols[i].checkbox(
                day_label,
                value=(day_num in cur_days),
                key=f"cf_day_{cid}_{day_num}",
            )
            if checked:
                selected_days.append(day_num)

        # ── Submit ────────────────────────────────────────────────────
        st.markdown("")
        sc1, sc2 = st.columns(2)
        submitted = sc1.form_submit_button(
            "💾  Save Centre", type="primary", use_container_width=True,
        )
        cancelled = sc2.form_submit_button("Cancel", use_container_width=True)

    # Handle cancel outside the form block
    if cancelled:
        if is_edit:
            st.session_state.pop(f"edit_centre_{cid}", None)
        else:
            st.session_state.pop("show_add_centre", None)
        st.rerun()

    if not submitted:
        return

    # ── Validation ────────────────────────────────────────────────────
    errors = []
    if not name.strip():
        errors.append("Centre name is required.")
    if not selected_days:
        errors.append("At least one operating day must be selected.")
    if opens_at >= closes_at:
        errors.append("Closing time must be after opening time.")

    for e in errors:
        st.error(f"❌ {e}")
    if errors:
        return

    # ── Save ──────────────────────────────────────────────────────────
    opens_str  = opens_at.strftime("%H:%M:%S")
    closes_str = closes_at.strftime("%H:%M:%S")

    with st.spinner("Saving…"):
        try:
            if is_edit:
                update_centre(
                    centre_id=cid,
                    name=name,
                    address_line_1=address_line_1,
                    address_line_2=address_line_2,
                    suburb=suburb,
                    state=state,
                    postcode=postcode,
                    phone=phone,
                    email=email,
                    licence_number=licence_number,
                    approved_places=int(approved_places) if approved_places else None,
                    timezone=timezone,
                    opens_at=opens_str,
                    closes_at=closes_str,
                    operating_days=sorted(selected_days),
                )
                toast_success(f"Centre '{name}' updated.")
                st.session_state.pop(f"edit_centre_{cid}", None)

            else:
                create_centre(
                    name=name,
                    address_line_1=address_line_1,
                    address_line_2=address_line_2,
                    suburb=suburb,
                    state=state,
                    postcode=postcode,
                    phone=phone,
                    email=email,
                    licence_number=licence_number,
                    approved_places=int(approved_places) if approved_places else None,
                    timezone=timezone,
                    opens_at=opens_str,
                    closes_at=closes_str,
                    operating_days=sorted(selected_days),
                )
                toast_success(f"Centre '{name}' created.")
                st.session_state.pop("show_add_centre", None)

            st.rerun()

        except Exception as e:
            toast_error(f"Could not save centre: {e}")


# ── Getting started tip ───────────────────────────────────────────────────────

def _render_getting_started_tip():
    st.markdown("")
    st.markdown(
        '<div style="background:#f5f8fb;border:1px solid #e4edf5;border-radius:10px;'
        'padding:1.2rem 1.5rem;">'
        '<p style="font-family:DM Serif Display,serif;font-size:1rem;color:#0d1f35;'
        'margin-bottom:0.5rem;">Getting started</p>'
        '<ol style="font-size:0.88rem;color:#4a6079;margin:0;padding-left:1.2rem;">'
        '<li style="margin-bottom:0.4rem;">Create at least one centre here.</li>'
        '<li style="margin-bottom:0.4rem;">Add rooms to the centre via <strong>🚪 Rooms</strong>.</li>'
        '<li style="margin-bottom:0.4rem;">Add staff via <strong>➕ Add Staff Member</strong> '
        'and assign them to the centre.</li>'
        '<li>Start building rosters in <strong>📅 Rosters</strong>.</li>'
        '</ol>'
        '</div>',
        unsafe_allow_html=True,
    )


# ── Time formatter ────────────────────────────────────────────────────────────

def _fmt_time(t_str) -> str:
    """'07:00:00' → '7:00 AM'. Returns '—' for None."""
    if not t_str:
        return "—"
    try:
        parts = str(t_str).split(":")
        t     = _time(int(parts[0]), int(parts[1]))
        return t.strftime("%-I:%M %p")
    except Exception:
        return str(t_str)[:5]
