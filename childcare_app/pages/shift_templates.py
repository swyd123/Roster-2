# pages/shift_templates.py — Manage reusable shift templates
import streamlit as st
from utils.roster_queries import (
    fetch_shift_templates, create_shift_template,
    update_shift_template, delete_shift_template,
)
from utils.roster_engine import generate_time_options, DAY_START_HOUR
from utils.staff_queries import fetch_centres
from utils.helpers import toast_success, toast_error

TEMPLATE_COLOURS = [
    ("#0ea5e9","Sky"),("#3b82f6","Blue"),("#6366f1","Indigo"),
    ("#8b5cf6","Purple"),("#ec4899","Pink"),("#ef4444","Red"),
    ("#f59e0b","Amber"),("#10b981","Emerald"),("#14b8a6","Teal"),
    ("#64748b","Slate"),
]


def render():
    bc, hc = st.columns([1, 9])
    with bc:
        st.markdown('<div class="back-btn">', unsafe_allow_html=True)
        if st.button("← Builder", key="st_back"):
            st.session_state.page = "roster_builder"; st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)
    hc.title("Shift Templates")
    hc.markdown('<p class="page-sub">Reusable shift patterns — apply to any day in the roster builder</p>',
                unsafe_allow_html=True)

    centres = fetch_centres()
    if not centres:
        st.warning("No centres found."); return
    centre_opts = {c["id"]: c["name"] for c in centres}
    saved = st.session_state.get("roster_centre_id") or centres[0]["id"]
    centre_id = st.selectbox(
        "Centre", options=list(centre_opts.keys()),
        format_func=lambda x: centre_opts[x],
        index=list(centre_opts.keys()).index(saved) if saved in centre_opts else 0,
        key="st_centre",
    )
    st.session_state.roster_centre_id = centre_id

    # ── Add form ──────────────────────────────────────────────────────
    with st.expander("➕  Add New Template", expanded=False):
        time_opts = generate_time_options(15, DAY_START_HOUR, 20)
        colour_labels = [c[1] for c in TEMPLATE_COLOURS]
        colour_values = [c[0] for c in TEMPLATE_COLOURS]
        with st.form("add_template_form"):
            fc1, fc2 = st.columns([3,1])
            tname = fc1.text_input("Template name *", placeholder="e.g. Early Shift")
            col_name = fc2.selectbox("Colour", colour_labels)
            colour = colour_values[colour_labels.index(col_name)]
            tc1, tc2, tc3 = st.columns(3)
            start = tc1.selectbox("Start time *", time_opts, index=4)
            end   = tc2.selectbox("End time *",   time_opts, index=16)
            brk   = tc3.number_input("Break (min)", min_value=0, max_value=90,
                                      value=30, step=15)
            if st.form_submit_button("Create Template", type="primary"):
                if not tname.strip():
                    toast_error("Template name is required.")
                elif start >= end:
                    toast_error("End time must be after start time.")
                else:
                    try:
                        create_shift_template(centre_id, tname, start+":00",
                                               end+":00", int(brk), colour)
                        toast_success(f"Template '{tname}' created.")
                        st.rerun()
                    except Exception as e:
                        toast_error(str(e))

    # ── Load templates ────────────────────────────────────────────────
    try:
        templates = fetch_shift_templates(centre_id)
    except Exception as e:
        toast_error(f"Could not load templates: {e}"); return

    st.markdown("---")
    if not templates:
        st.info("No templates yet. Create your first one above.")
        return

    st.markdown(f"**{len(templates)} template(s)**")

    for t in templates:
        tid    = t["id"]
        colour = t.get("colour","#3b82f6")
        name   = t.get("name","")
        start  = (t.get("start_time") or "")[:5]
        end    = (t.get("end_time")   or "")[:5]
        brk    = t.get("break_duration_minutes",0)

        with st.expander(f"**{name}** · {start}–{end}"):
            st.markdown(
                f'<div style="display:flex;align-items:center;gap:0.6rem;margin-bottom:0.5rem;">'
                f'<div style="width:14px;height:14px;border-radius:50%;background:{colour};"></div>'
                f'<span style="font-size:0.9rem;">{start} – {end}</span>'
                f'<span style="font-size:0.82rem;color:#7a90a8;">'
                f'{brk}min break</span></div>',
                unsafe_allow_html=True,
            )
            edit_key = f"edit_tpl_{tid}"
            ea, eb = st.columns([1, 5])
            if ea.button("✏️ Edit", key=f"etb_{tid}", use_container_width=True):
                st.session_state[edit_key] = not st.session_state.get(edit_key, False)
                st.rerun()
            if st.session_state.get(edit_key):
                _render_edit_template_form(t, time_opts := generate_time_options(15,6,20),
                                            colour_labels, colour_values, tid, edit_key)

            del_key = f"del_tpl_{tid}"
            if st.session_state.get(del_key):
                st.warning(f"Delete template '{name}'?")
                dy, dn = st.columns(2)
                if dy.button("Delete", key=f"dy_{tid}", type="primary", use_container_width=True):
                    try:
                        delete_shift_template(tid)
                        toast_success(f"Template '{name}' deleted.")
                        st.session_state.pop(del_key, None); st.rerun()
                    except Exception as e:
                        toast_error(str(e))
                if dn.button("Cancel", key=f"dn_{tid}", use_container_width=True):
                    st.session_state.pop(del_key, None); st.rerun()
            else:
                if ea.button("🗑️ Delete", key=f"dtb_{tid}", use_container_width=True):
                    st.session_state[del_key] = True; st.rerun()


def _render_edit_template_form(t, time_opts, colour_labels, colour_values, tid, edit_key):
    cur_start  = (t.get("start_time") or "07:00")[:5]
    cur_end    = (t.get("end_time")   or "15:00")[:5]
    cur_brk    = t.get("break_duration_minutes",30)
    cur_colour = t.get("colour","#3b82f6")
    cur_name   = t.get("name","")
    cur_col_idx = colour_values.index(cur_colour) if cur_colour in colour_values else 0
    start_idx   = time_opts.index(cur_start) if cur_start in time_opts else 4
    end_idx     = time_opts.index(cur_end)   if cur_end   in time_opts else 16

    with st.form(f"edit_tpl_form_{tid}"):
        fc1, fc2 = st.columns([3,1])
        new_name  = fc1.text_input("Name *", value=cur_name, key=f"etn_{tid}")
        col_name  = fc2.selectbox("Colour", colour_labels, index=cur_col_idx, key=f"etc_{tid}")
        new_colour= colour_values[colour_labels.index(col_name)]
        tc1, tc2, tc3 = st.columns(3)
        new_start = tc1.selectbox("Start", time_opts, index=start_idx, key=f"ets_{tid}")
        new_end   = tc2.selectbox("End",   time_opts, index=end_idx,   key=f"ete_{tid}")
        new_brk   = tc3.number_input("Break (min)", min_value=0, max_value=90,
                                      value=int(cur_brk), step=15, key=f"etb_{tid}")
        sc1, sc2 = st.columns(2)
        saved    = sc1.form_submit_button("💾 Save", type="primary", use_container_width=True)
        cancelled= sc2.form_submit_button("Cancel", use_container_width=True)

    if cancelled:
        st.session_state.pop(edit_key, None); st.rerun()
    if saved:
        if not new_name.strip():
            toast_error("Name required."); return
        if new_start >= new_end:
            toast_error("End after start."); return
        try:
            update_shift_template(tid, new_name, new_start+":00",
                                   new_end+":00", int(new_brk), new_colour)
            toast_success("Template updated.")
            st.session_state.pop(edit_key, None); st.rerun()
        except Exception as e:
            toast_error(str(e))
