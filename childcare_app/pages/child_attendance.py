 # pages/child_attendance.py — Child Attendance / Room Occupancy
#
# Two data entry paths:
#   1. Bulk CSV upload  → all rooms, all dates in one file → summary table
#                      → select date+room to review → pre-fills editable grid
#   2. Manual entry     → pick date and room → edit grid directly
#
# Session state bridges CSV → grid without a rerun:
#   "bulk_import_result"           full parse_csv_bulk() result, cached by file sig
#   _prefill_key(room_id, date)    {interval_start → count} for one room on one date
#   "bulk_review_date"             date currently selected in the review picker
#   "bulk_review_room"             room currently selected in the review picker
#
# No .single() anywhere.

import streamlit as st
from datetime import date, datetime

import pandas as pd

from utils.attendance_queries import (
    generate_intervals,
    fetch_intervals_for_room,
    fetch_intervals_for_centre,
    upsert_all_intervals,
    upsert_all_dates_from_bulk,
    upsert_single_date_from_bulk,
)
from utils.csv_attendance_import import parse_csv_bulk
from utils.room_queries import fetch_rooms
from utils.staff_queries import fetch_centres
from utils.centre_queries import fetch_centre_by_id
from utils.helpers import toast_success, toast_error


# ─────────────────────────────────────────────────────────────────────────────
# Session state key helpers
# ─────────────────────────────────────────────────────────────────────────────

def _prefill_key(room_id: str, date_str: str) -> str:
    return f"csv_prefill_{room_id}_{date_str}"

def _bulk_result_key() -> str:
    return "bulk_import_result"


# ─────────────────────────────────────────────────────────────────────────────
# Page entry point
# ─────────────────────────────────────────────────────────────────────────────

def render():
    # ── Header ────────────────────────────────────────────────────────
    h1, h2 = st.columns([4, 1])
    h1.title("Child Attendance")
    h1.markdown(
        '<p class="page-sub">Bulk CSV import across all rooms and dates '
        "· or enter counts manually · feeds ratio monitor automatically</p>",
        unsafe_allow_html=True,
    )
    with h2:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("📊  Ratio Monitor", use_container_width=True):
            st.session_state.page = "ratio_dashboard"
            st.rerun()

    # ── Centre selector ───────────────────────────────────────────────
    centres = fetch_centres()
    if not centres:
        st.warning("No centres configured. Go to **🏫 Centres** to create one first.")
        return

    centre_opts  = {c["id"]: c["name"] for c in centres}
    saved_centre = (
        st.session_state.get("attendance_centre_id")
        or st.session_state.get("selected_centre_id")
        or centres[0]["id"]
    )

    centre_id = st.selectbox(
        "Centre",
        options=list(centre_opts.keys()),
        format_func=lambda x: centre_opts[x],
        index=list(centre_opts.keys()).index(saved_centre)
               if saved_centre in centre_opts else 0,
        key="att_centre_sel",
    )
    st.session_state.attendance_centre_id = centre_id

    # ── Load centre + rooms ───────────────────────────────────────────
    with st.spinner("Loading…"):
        try:
            centre = fetch_centre_by_id(centre_id)
            rooms  = fetch_rooms(centre_id)
        except Exception as e:
            toast_error(f"Could not load data: {e}")
            return

    if not rooms:
        st.info("No rooms configured. Go to **🚪 Rooms** to add rooms first.")
        return

    opens_at  = centre.get("opens_at")  if centre else None
    closes_at = centre.get("closes_at") if centre else None
    intervals = generate_intervals(opens_at, closes_at)

    if not intervals:
        st.error(
            "Centre opening/closing times are not set. "
            "Go to **🏫 Centres → Edit** to set them."
        )
        return

    room_map = {r["id"]: r for r in rooms}

    st.markdown("---")

    # ── Section 1: Bulk CSV import ────────────────────────────────────
    _render_bulk_csv_section(
        centre_id=centre_id,
        rooms=rooms,
        room_map=room_map,
        intervals=intervals,
    )

    st.markdown("---")

    # ── Section 2: Manual / review grid ──────────────────────────────
    _render_manual_grid(
        centre_id=centre_id,
        rooms=rooms,
        room_map=room_map,
        intervals=intervals,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Section 1 — Bulk CSV import
# ─────────────────────────────────────────────────────────────────────────────

def _render_bulk_csv_section(
    centre_id: str,
    rooms: list[dict],
    room_map: dict[str, dict],
    intervals: list[dict],
):
    st.markdown("### 📂 Bulk CSV Import")
    st.caption(
        "Upload one CSV file covering any number of rooms and dates. "
        "Existing records are updated — no duplicates created."
    )

    # ── Sample template download ──────────────────────────────────────
    sample = (
        "attendance_date,child_name,room_name,start_time,end_time\n"
        "2026-06-01,Mia,Babies,08:00,16:30\n"
        "2026-06-01,Leo,Toddlers,09:15,15:45\n"
        "2026-06-01,Ava,Preschool,07:30,17:00\n"
        "2026-06-02,Mia,Babies,08:10,16:20\n"
        "2026-06-02,Leo,Toddlers,09:00,15:30\n"
        "2026-06-02,Ava,Preschool,08:00,17:00\n"
    )
    st.download_button(
        "⬇️  Download sample CSV",
        data=sample,
        file_name="bulk_attendance_template.csv",
        mime="text/csv",
        key="dl_bulk_sample",
    )

    # ── File uploader ─────────────────────────────────────────────────
    uploaded = st.file_uploader(
        "Choose CSV file",
        type=["csv"],
        key="bulk_csv_uploader",
        label_visibility="collapsed",
    )

    bkey = _bulk_result_key()

    if uploaded is None:
        if bkey in st.session_state:
            st.session_state.pop(bkey, None)
        st.caption("No file selected.")
        return

    # ── Parse (cached by filename+size to survive reruns) ─────────────
    sig    = f"{uploaded.name}_{uploaded.size}"
    cached = st.session_state.get(bkey)

    if cached is None or cached.get("_sig") != sig:
        raw    = uploaded.read()
        result = parse_csv_bulk(
            file_bytes=raw,
            rooms=rooms,
            intervals=intervals,
        )
        result["_sig"] = sig
        st.session_state[bkey] = result

        # Write prefill counts into session state for every room × date
        # so the manual grid below immediately shows the right values
        # when the user selects any combination.
        if result.get("date_room_counts"):
            for d_str, room_counts in result["date_room_counts"].items():
                for rid, counts in room_counts.items():
                    st.session_state[_prefill_key(rid, d_str)] = counts
    else:
        result = cached

    # ── Blocking errors ───────────────────────────────────────────────
    if result["errors"]:
        for err in result["errors"]:
            st.error(f"❌ {err}")
        for warn in result["warnings"]:
            st.warning(f"⚠️ {warn}")
        return

    # ── Non-blocking warnings ─────────────────────────────────────────
    for warn in result["warnings"]:
        st.warning(f"⚠️ {warn}")

    date_room_counts = result.get("date_room_counts") or {}
    dates            = result.get("dates", [])
    n_children       = result.get("n_children", 0)
    n_skipped        = result.get("n_skipped", 0)

    if not date_room_counts:
        st.info("No interval data could be calculated — all rows were skipped.")
        return

    # ── Top-line metrics ──────────────────────────────────────────────
    total_room_dates = sum(len(rc) for rc in date_room_counts.values())
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Dates",           len(dates))
    m2.metric("Room×Date combos", total_room_dates)
    m3.metric("Children loaded",  n_children)
    m4.metric("Rows skipped",     n_skipped,
              delta="see warnings" if n_skipped else None,
              delta_color="inverse" if n_skipped else "off")

    # ── Summary table: one row per date × room ────────────────────────
    st.markdown("**Import summary — by date and room**")
    summary_rows = _build_summary_table(date_room_counts, room_map, intervals)
    st.dataframe(
        pd.DataFrame(summary_rows),
        use_container_width=True,
        hide_index=True,
    )

    # ── Per-child preview (collapsed to save space) ───────────────────
    with st.expander("📋  Per-child row preview", expanded=False):
        st.dataframe(
            result.get("preview_df", pd.DataFrame()),
            use_container_width=True,
            hide_index=True,
        )

    st.markdown("")

    # ── Action buttons ────────────────────────────────────────────────
    ba1, ba2, _ = st.columns([1.8, 2.2, 3])

    # Save all dates and rooms
    if ba1.button(
        f"💾  Save all — {len(dates)} date(s), {total_room_dates} room×date(s)",
        type="primary",
        key="bulk_save_all_btn",
        use_container_width=True,
    ):
        with st.spinner("Saving all intervals to Supabase…"):
            total_ivs, total_rooms, errors = upsert_all_dates_from_bulk(
                centre_id=centre_id,
                date_room_counts=date_room_counts,
                intervals=intervals,
            )
        if errors:
            for e in errors:
                toast_error(f"Error: {e}")
        if total_ivs > 0:
            toast_success(
                f"✅ Saved {total_ivs} interval(s) across "
                f"{total_rooms} room×date combination(s)."
            )
            # Clear prefill cache — data now lives in Supabase
            _clear_all_prefills(date_room_counts)
            st.session_state.pop(bkey, None)
            st.rerun()

    # Save selected date only (helper for reviewing one date at a time)
    selected_review_date = st.session_state.get("bulk_review_date")
    if selected_review_date and selected_review_date in date_room_counts:
        if ba2.button(
            f"💾  Save {selected_review_date} only",
            key="bulk_save_date_btn",
            use_container_width=True,
        ):
            room_counts_for_date = date_room_counts[selected_review_date]
            with st.spinner(f"Saving {selected_review_date}…"):
                total_ivs, total_rooms, errors = upsert_single_date_from_bulk(
                    centre_id=centre_id,
                    date_str=selected_review_date,
                    room_counts=room_counts_for_date,
                    intervals=intervals,
                )
            if errors:
                for e in errors:
                    toast_error(f"Error: {e}")
            if total_ivs > 0:
                toast_success(
                    f"✅ Saved {total_ivs} interval(s) for {selected_review_date}."
                )
                # Clear prefill for this date's rooms
                for rid in room_counts_for_date:
                    st.session_state.pop(_prefill_key(rid, selected_review_date), None)
                st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# Section 2 — Manual / review grid
# ─────────────────────────────────────────────────────────────────────────────

def _render_manual_grid(
    centre_id: str,
    rooms: list[dict],
    room_map: dict[str, dict],
    intervals: list[dict],
):
    st.markdown("### ✏️ Review and Edit Intervals")
    st.caption(
        "Select a date and room to review CSV-imported counts or enter data manually. "
        "Edit any value, then click **Save**."
    )

    # ── Date + room selectors ─────────────────────────────────────────
    # Pre-fill selectors from bulk review state if set
    cached_result  = st.session_state.get(_bulk_result_key(), {})
    imported_dates = (cached_result.get("dates") or []) if cached_result else []

    gc1, gc2 = st.columns(2)

    # Date: offer imported dates prominently if available, else free-pick
    if imported_dates:
        # Default to first imported date, or whatever was last reviewed
        default_review = st.session_state.get("bulk_review_date") or imported_dates[0]
        idx = imported_dates.index(default_review) if default_review in imported_dates else 0
        selected_date_str = gc1.selectbox(
            "Date to review",
            options=imported_dates,
            index=idx,
            key="grid_date_sel",
        )
        st.session_state["bulk_review_date"] = selected_date_str
        try:
            attendance_date = date.fromisoformat(selected_date_str)
        except ValueError:
            attendance_date = date.today()
    else:
        attendance_date = gc1.date_input(
            "Date",
            value=date.fromisoformat(
                st.session_state.get("bulk_review_date") or date.today().isoformat()
            ),
            key="grid_date_pick",
            format="DD/MM/YYYY",
        )
        selected_date_str = attendance_date.isoformat()
        st.session_state["bulk_review_date"] = selected_date_str

    room_opts = {r["id"]: r["name"] for r in rooms}
    default_room = st.session_state.get("bulk_review_room") or list(room_opts.keys())[0]
    room_id = gc2.selectbox(
        "Room",
        options=list(room_opts.keys()),
        format_func=lambda x: room_opts[x],
        index=list(room_opts.keys()).index(default_room)
               if default_room in room_opts else 0,
        key="grid_room_sel",
    )
    st.session_state["bulk_review_room"] = room_id
    selected_room = room_map.get(room_id, {})

    date_str   = selected_date_str
    colour     = selected_room.get("colour", "#3498DB")
    capacity   = selected_room.get("licensed_capacity", 0)
    r_children = selected_room.get("required_ratio_children", 4)

    # ── Load saved data from Supabase ─────────────────────────────────
    with st.spinner("Loading intervals…"):
        try:
            saved = fetch_intervals_for_room(room_id, date_str)
        except Exception as e:
            toast_error(f"Could not load attendance data: {e}")
            saved = []

    saved_map = {s["interval_start"]: s for s in saved}
    n_saved   = sum(1 for s in saved if s.get("expected_children", 0) > 0)

    # ── Day summary strip ─────────────────────────────────────────────
    _render_day_summary(rooms, room_id, centre_id, date_str)
    st.markdown("")

    # ── Room header ───────────────────────────────────────────────────
    prefill_key    = _prefill_key(room_id, date_str)
    csv_prefill    = st.session_state.get(prefill_key, {})
    prefill_active = bool(csv_prefill)

    st.markdown(
        f'<div style="display:flex;align-items:center;gap:0.75rem;margin-bottom:0.5rem;">'
        f'<div style="width:14px;height:14px;border-radius:50%;background:{colour};'
        f'box-shadow:0 0 0 3px {colour}30;flex-shrink:0;"></div>'
        f'<span style="font-family:DM Serif Display,serif;font-size:1.1rem;color:#0d1f35;">'
        f'{selected_room.get("name","Room")}</span>'
        f'<span style="font-size:0.82rem;color:#7a90a8;margin-left:0.5rem;">'
        f'Capacity {capacity} · Ratio 1:{r_children} · {date_str}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    if prefill_active:
        n_prefilled = sum(1 for v in csv_prefill.values() if v > 0)
        st.info(
            f"📋 **{n_prefilled} interval(s) pre-filled from CSV upload.** "
            "Edit any values below, then click **Save**."
        )
    elif n_saved:
        st.caption(f"✅ {n_saved} of {len(intervals)} intervals have saved data.")
    else:
        st.info(
            "No data for this room and date yet. "
            "Upload a CSV above, or fill in the counts below and click **Save**."
        )

    st.markdown("**Expected and actual child counts per 15-minute interval.**")

    # ── Column headers ────────────────────────────────────────────────
    now      = datetime.now().strftime("%H:%M:%S")
    is_today = attendance_date == date.today()

    hc0, hc1, hc2, hc3, hc4 = st.columns([1.8, 1.4, 1.4, 1.4, 2.0])
    hc0.markdown("**Time**")
    hc1.markdown("**Expected**")
    hc2.markdown("**Actual**")
    hc3.markdown("**Cap %**")
    hc4.markdown("**Notes**")

    # ── Interval rows ─────────────────────────────────────────────────
    form_rows = []
    with st.form(key=f"grid_form_{room_id}_{date_str}"):
        for iv in intervals:
            istart   = iv["interval_start"]
            iend     = iv["interval_end"]
            label    = iv["label"]
            existing = saved_map.get(istart, {})
            is_now   = is_today and istart <= now < iend

            # Value priority: CSV prefill → Supabase saved → 0
            if istart in csv_prefill:
                exp_default = int(csv_prefill[istart])
            else:
                exp_default = int(existing.get("expected_children") or 0)

            label_html = (
                f'<span style="font-size:0.82rem;'
                f'font-weight:{"700" if is_now else "400"};'
                f'color:{"#0d1f35" if is_now else "#475569"};">'
                f'{"▶ " if is_now else ""}{label}</span>'
            )

            c0, c1, c2, c3, c4 = st.columns([1.8, 1.4, 1.4, 1.4, 2.0])
            c0.markdown(label_html, unsafe_allow_html=True)

            exp = c1.number_input(
                "exp", min_value=0, max_value=capacity or 100,
                value=exp_default,
                key=f"exp_{room_id}_{date_str}_{istart}",
                label_visibility="collapsed", step=1,
            )

            act_val = existing.get("actual_children")
            act = c2.number_input(
                "act", min_value=0, max_value=capacity or 100,
                value=int(act_val) if act_val is not None else 0,
                key=f"act_{room_id}_{date_str}_{istart}",
                label_visibility="collapsed", step=1,
            )
            actual_to_save = act if (act > 0 or act_val is not None) else None

            n_for_pct = act if (act_val is not None or act > 0) else exp
            if n_for_pct > 0 and capacity:
                pct = round((n_for_pct / capacity) * 100)
                if pct >= 100:
                    pct_html = f'<span style="color:#991b1b;font-size:0.75rem;">⚠ {pct}%</span>'
                elif pct >= 80:
                    pct_html = f'<span style="color:#92400e;font-size:0.75rem;">{pct}%</span>'
                else:
                    pct_html = f'<span style="color:#14532d;font-size:0.75rem;">{pct}%</span>'
            else:
                pct_html = '<span style="color:#94a3b8;font-size:0.75rem;">—</span>'
            c3.markdown(pct_html, unsafe_allow_html=True)

            notes_default = existing.get("notes", "") or ""
            notes_val = c4.text_input(
                "notes", value=notes_default,
                key=f"notes_{room_id}_{date_str}_{istart}",
                label_visibility="collapsed", placeholder="optional",
            )

            form_rows.append({
                "interval_start":    istart,
                "interval_end":      iend,
                "expected_children": exp,
                "actual_children":   actual_to_save,
                "notes":             notes_val,
            })

        st.markdown("")
        sc1, sc2 = st.columns([1.2, 1.2])
        submitted = sc1.form_submit_button(
            "💾  Save", type="primary", use_container_width=True,
        )
        clear_btn = sc2.form_submit_button(
            "🔄  Reset to Saved", use_container_width=True,
        )

    if clear_btn:
        st.session_state.pop(prefill_key, None)
        st.rerun()

    if submitted:
        rows_to_save = [
            r for r in form_rows
            if r["expected_children"] > 0 or r["actual_children"] is not None
        ]
        if not rows_to_save:
            toast_error("Enter at least one count before saving.")
        else:
            with st.spinner("Saving…"):
                try:
                    n = upsert_all_intervals(
                        centre_id=centre_id,
                        room_id=room_id,
                        attendance_date=date_str,
                        rows=rows_to_save,
                    )
                    toast_success(
                        f"Saved {n} interval(s) for "
                        f"{selected_room.get('name','')} — {date_str}."
                    )
                    st.session_state.pop(prefill_key, None)
                    st.rerun()
                except Exception as e:
                    toast_error(f"Could not save: {e}")

    st.markdown("")
    st.caption(
        "💡 Counts feed the Ratio Monitor. "
        "Use 'Expected' for planning, 'Actual' for live tracking."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Day summary strip
# ─────────────────────────────────────────────────────────────────────────────

def _render_day_summary(
    rooms: list[dict],
    selected_room_id: str,
    centre_id: str,
    date_str: str,
):
    with st.spinner("Loading summary…"):
        try:
            all_intervals = fetch_intervals_for_centre(centre_id, date_str)
        except Exception:
            all_intervals = []

    st.markdown(f"**{date_str} — All Rooms**")
    if not rooms:
        return

    cols = st.columns(len(rooms))
    for i, room in enumerate(rooms):
        rid    = room["id"]
        colour = room.get("colour", "#3498DB")
        rname  = room.get("name", "")
        cap    = room.get("licensed_capacity", 0)

        room_ivs = [r for r in all_intervals if r.get("room_id") == rid]
        if not room_ivs:
            content = '<span style="font-size:0.75rem;color:#94a3b8;">No data</span>'
        else:
            peak_exp = max(int(r.get("expected_children") or 0) for r in room_ivs)
            act_rows = [r for r in room_ivs if r.get("actual_children") is not None]
            peak_act = max(int(r.get("actual_children") or 0) for r in act_rows) if act_rows else None
            pct      = round((peak_exp / cap) * 100) if cap else 0
            act_str  = f" / {peak_act} act" if peak_act is not None else ""
            content  = (
                f'<span style="font-size:0.78rem;color:#0d1f35;">'
                f'Peak {peak_exp}{act_str}</span><br>'
                f'<span style="font-size:0.7rem;color:#7a90a8;">{pct}% of {cap}</span>'
            )

        is_sel = rid == selected_room_id
        border = f"2px solid {colour}" if is_sel else "1px solid #e4edf5"
        bg     = f"{colour}12"         if is_sel else "#fafcfe"

        cols[i].markdown(
            f'<div style="border:{border};background:{bg};border-radius:8px;'
            f'padding:0.5rem 0.6rem;text-align:center;margin-bottom:0.3rem;">'
            f'<div style="display:flex;align-items:center;justify-content:center;'
            f'gap:4px;margin-bottom:2px;">'
            f'<div style="width:7px;height:7px;border-radius:50%;background:{colour};"></div>'
            f'<span style="font-size:0.78rem;font-weight:600;color:#0d1f35;">{rname}</span>'
            f'</div>{content}</div>',
            unsafe_allow_html=True,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Build the summary table shown after CSV parse
# ─────────────────────────────────────────────────────────────────────────────

def _build_summary_table(
    date_room_counts: dict,
    room_map: dict[str, dict],
    intervals: list[dict],
) -> list[dict]:
    """
    Build a list of row dicts for the summary table:
    Date | Room | Children | Peak attendance | First arrival | Last departure
    """
    rows = []
    iv_lookup = {iv["interval_start"]: iv["interval_end"] for iv in intervals}

    for date_str in sorted(date_room_counts.keys()):
        room_counts = date_room_counts[date_str]
        for room_id, counts in sorted(
            room_counts.items(),
            key=lambda kv: (room_map.get(kv[0], {}).get("name", "")),
        ):
            rname = room_map.get(room_id, {}).get("name", room_id)

            # Intervals that have children
            active = {iv: cnt for iv, cnt in counts.items() if cnt > 0}
            if not active:
                continue

            peak        = max(active.values())
            first_iv    = min(active.keys())
            last_iv     = max(active.keys())

            # "Last departure" = end of the last occupied interval
            last_iv_end = iv_lookup.get(last_iv, last_iv)

            # Unique-child approximation: not possible from counts alone,
            # so report the sum of peak headcounts as a proxy.
            # The CSV preview shows actual children; this is the interval view.
            total_child_intervals = sum(active.values())

            rows.append({
                "Date":            date_str,
                "Room":            rname,
                "Children (sum)":  total_child_intervals,
                "Peak":            peak,
                "First arrival":   first_iv[:5],
                "Last departure":  last_iv_end[:5],
            })

    return rows


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _clear_all_prefills(date_room_counts: dict):
    """Remove all CSV prefill keys for every room×date in the import."""
    for date_str, room_counts in date_room_counts.items():
        for rid in room_counts:
            st.session_state.pop(_prefill_key(rid, date_str), None)
