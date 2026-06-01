# pages/room_form.py  —  Screen 24: Add / Edit Room
import streamlit as st
from utils.room_queries import (
    fetch_room_by_id, create_room, update_room,
    fmt_age_range,
)
from utils.staff_queries import fetch_centres
from utils.helpers import toast_success, toast_error
import time


# Predefined colour palette — childcare-friendly, distinct enough to tell apart
ROOM_COLOURS = [
    ("#E74C3C", "Red"),
    ("#E67E22", "Orange"),
    ("#F1C40F", "Yellow"),
    ("#2ECC71", "Green"),
    ("#1ABC9C", "Teal"),
    ("#3498DB", "Blue"),
    ("#9B59B6", "Purple"),
    ("#E91E8B", "Pink"),
    ("#795548", "Brown"),
    ("#607D8B", "Slate"),
    ("#00BCD4", "Cyan"),
    ("#8BC34A", "Lime"),
]


def render():
    is_edit    = "editing_room_id" in st.session_state and st.session_state.editing_room_id
    room_id    = st.session_state.get("editing_room_id")
    existing   = None

    if is_edit and room_id:
        with st.spinner("Loading room…"):
            try:
                existing = fetch_room_by_id(room_id)
            except Exception as e:
                toast_error(f"Could not load room: {e}"); return

    # ── Header ────────────────────────────────────────────────────────
    bc, _ = st.columns([1, 10])
    with bc:
        st.markdown('<div class="back-btn">', unsafe_allow_html=True)
        if st.button("← Back", key="room_form_back"):
            st.session_state.pop("editing_room_id", None)
            st.session_state.page = "rooms_list"; st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    title = f"Edit Room: {existing['name']}" if existing else "Add New Room"
    st.title(title)
    st.markdown(
        '<p class="page-sub">Room settings directly affect ratio compliance and qualification checks.</p>',
        unsafe_allow_html=True
    )
    st.markdown("---")

    # ── Centre selector (add mode only) ───────────────────────────────
    if not is_edit:
        centres     = fetch_centres()
        centre_opts = {c["id"]: c["name"] for c in centres}
        centre_id   = st.selectbox(
            "Centre *",
            options=list(centre_opts.keys()),
            format_func=lambda x: centre_opts[x],
            key="room_form_centre",
        )
    else:
        centre_id = existing.get("centre_id") if existing else None

    # ── Form ──────────────────────────────────────────────────────────
    with st.form("room_form", clear_on_submit=False):

        # Section 1 — Identity
        st.markdown('<p class="section-label">Room Identity</p>', unsafe_allow_html=True)
        fc1, fc2 = st.columns([3, 1])
        name = fc1.text_input(
            "Room name *",
            value=existing.get("name","") if existing else "",
            placeholder="e.g. Nursery, Blue Room, Kindy",
            key="rf_name",
        )

        # Colour picker — grid of swatches
        current_colour = existing.get("colour","#3498DB") if existing else "#3498DB"
        colour_labels  = [c[1] for c in ROOM_COLOURS]
        colour_values  = [c[0] for c in ROOM_COLOURS]
        cur_idx        = colour_values.index(current_colour.upper()) \
                         if current_colour.upper() in colour_values else 5

        selected_colour_name = fc2.selectbox(
            "Room colour *",
            options=colour_labels,
            index=cur_idx,
            key="rf_colour_name",
            help="This colour appears on rosters and ratio cards to identify the room quickly.",
        )
        selected_colour = colour_values[colour_labels.index(selected_colour_name)]

        # Colour preview
        st.markdown(
            f'<div style="display:flex;align-items:center;gap:0.6rem;margin:-0.3rem 0 0.6rem;">'
            f'<div style="width:24px;height:24px;border-radius:50%;background:{selected_colour};'
            f'box-shadow:0 0 0 3px {selected_colour}40;"></div>'
            f'<span style="font-size:0.82rem;color:#7a90a8;">Room will appear as this colour on rosters and dashboards</span>'
            f'</div>',
            unsafe_allow_html=True
        )

        st.divider()

        # Section 2 — Age range
        st.markdown('<p class="section-label">Age Range</p>', unsafe_allow_html=True)
        st.caption("In months. Example: Nursery = 0–23 months, Toddlers = 24–35 months, Kindy = 36–72 months.")
        ac1, ac2 = st.columns(2)
        age_min = ac1.number_input(
            "Minimum age (months) *",
            min_value=0, max_value=120,
            value=existing.get("age_min_months", 0) if existing else 0,
            key="rf_age_min",
            help="0 = from birth",
        )
        age_max = ac2.number_input(
            "Maximum age (months) *",
            min_value=1, max_value=120,
            value=existing.get("age_max_months", 23) if existing else 23,
            key="rf_age_max",
        )

        # Live preview of age range
        if age_max > age_min:
            st.markdown(
                f'<p style="font-size:0.82rem;color:#1a6b4a;margin-top:-0.3rem;">'
                f'📏 Age range: <strong>{fmt_age_range(int(age_min), int(age_max))}</strong>'
                f'</p>',
                unsafe_allow_html=True
            )

        st.divider()

        # Section 3 — Ratio & Capacity
        st.markdown('<p class="section-label">Ratio & Capacity</p>', unsafe_allow_html=True)
        st.caption("Set the legally required minimum educator-to-child ratio for this room.")

        rc1, rc2, rc3 = st.columns(3)
        capacity = rc1.number_input(
            "Licensed capacity *",
            min_value=1, max_value=100,
            value=existing.get("licensed_capacity", 12) if existing else 12,
            key="rf_capacity",
            help="Maximum number of children allowed in this room (from your licence).",
        )
        ratio_staff = rc2.number_input(
            "Ratio — staff *",
            min_value=1, max_value=10,
            value=existing.get("required_ratio_staff", 1) if existing else 1,
            key="rf_ratio_staff",
            help="The '1' in '1 staff per 4 children'.",
        )
        ratio_children = rc3.number_input(
            "Ratio — children *",
            min_value=1, max_value=30,
            value=existing.get("required_ratio_children", 4) if existing else 4,
            key="rf_ratio_children",
            help="The '4' in '1 staff per 4 children'.",
        )

        # Live ratio preview
        st.markdown(
            f'<p style="font-size:0.82rem;color:#1a6b4a;margin-top:-0.3rem;">'
            f'📐 Required ratio: <strong>{int(ratio_staff)} educator(s) per {int(ratio_children)} children</strong> · '
            f'Capacity {int(capacity)} needs at least '
            f'<strong>{-(-int(capacity) // int(ratio_children)) * int(ratio_staff)} educators</strong>'
            f'</p>',
            unsafe_allow_html=True
        )

        st.divider()

        # Section 4 — Qualifications & Settings
        st.markdown('<p class="section-label">Qualification Requirements</p>', unsafe_allow_html=True)
        requires_diploma = st.toggle(
            "Requires diploma-qualified educator",
            value=existing.get("requires_diploma", False) if existing else (age_min < 24),
            key="rf_diploma",
            help="Under-2s rooms typically require at least one diploma-qualified educator per NQS.",
        )

        st.divider()

        # Section 5 — Display order & notes
        st.markdown('<p class="section-label">Display & Notes</p>', unsafe_allow_html=True)
        oc1, oc2 = st.columns([1, 3])
        sort_order = oc1.number_input(
            "Display order",
            min_value=0, max_value=99,
            value=existing.get("sort_order", 0) if existing else 0,
            key="rf_sort",
            help="Lower numbers appear first in lists.",
        )

        # Active toggle in edit mode
        is_active = True
        if is_edit and existing:
            is_active = st.toggle(
                "Room is active",
                value=existing.get("is_active", True),
                key="rf_active",
                help="Inactive rooms are hidden from rosters and ratio monitoring.",
            )

        notes = st.text_area(
            "Internal notes",
            value=existing.get("notes","") or "" if existing else "",
            key="rf_notes",
            height=80,
            placeholder="e.g. Window side, capacity may be restricted during renovations.",
        )

        # ── Buttons ───────────────────────────────────────────────────
        sc1, sc2 = st.columns(2)
        submitted = sc1.form_submit_button(
            "💾  Save Room", type="primary", use_container_width=True
        )
        cancelled = sc2.form_submit_button("Cancel", use_container_width=True)

    if cancelled:
        st.session_state.pop("editing_room_id", None)
        st.session_state.page = "rooms_list"; st.rerun()

    if submitted:
        # Validation
        errors = []
        if not name.strip():
            errors.append("Room name is required.")
        if int(age_max) <= int(age_min):
            errors.append("Maximum age must be greater than minimum age.")
        if int(capacity) < 1:
            errors.append("Capacity must be at least 1.")
        for e in errors:
            st.error(f"❌ {e}")
        if errors:
            return

        with st.spinner("Saving…"):
            try:
                if is_edit and room_id:
                    update_room(
                        room_id=room_id,
                        name=name,
                        age_min_months=int(age_min),
                        age_max_months=int(age_max),
                        licensed_capacity=int(capacity),
                        required_ratio_staff=int(ratio_staff),
                        required_ratio_children=int(ratio_children),
                        requires_diploma=requires_diploma,
                        colour=selected_colour,
                        sort_order=int(sort_order),
                        is_active=is_active,
                        notes=notes,
                    )
                    toast_success(f"Room '{name}' updated.")
                else:
                    new_room = create_room(
                        centre_id=centre_id,
                        name=name,
                        age_min_months=int(age_min),
                        age_max_months=int(age_max),
                        licensed_capacity=int(capacity),
                        required_ratio_staff=int(ratio_staff),
                        required_ratio_children=int(ratio_children),
                        requires_diploma=requires_diploma,
                        colour=selected_colour,
                        sort_order=int(sort_order),
                        notes=notes,
                    )
                    toast_success(f"Room '{name}' created.")
                    if new_room:
                        st.session_state.viewing_room_id = new_room["id"]
                        st.session_state.viewing_room_centre = centre_id

                time.sleep(0.5)
                st.session_state.pop("editing_room_id", None)
                st.session_state.page = "rooms_list"
                st.rerun()
            except Exception as e:
                toast_error(f"Could not save room: {e}")
