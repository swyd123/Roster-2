# pages/child_attendance.py — Child Attendance / Room Occupancy
#
# Two ways to enter data:
#   1. CSV upload  → calculates interval counts → pre-fills editable grid
#   2. Manual entry → edit counts directly in the grid
#
# Session state key "csv_prefill_{room_id}_{date_str}" bridges the CSV
# parser and the grid: parse_csv writes counts there; the grid reads them
# as default values for number_input widgets; the Save button captures
# whatever the user typed (not the raw CSV values), so manual edits stick.
#
# No .single() anywhere.

import io
import streamlit as st
from datetime import date, datetime

import pandas as pd

from utils.attendance_queries import (
    generate_intervals,
    fetch_intervals_for_room,
    fetch_intervals_for_centre,
    upsert_all_intervals,
)
from utils.csv_attendance_import import (
    parse_csv,
    room_counts_to_upsert_rows,
)
from utils.room_queries import fetch_rooms
from utils.staff_queries import fetch_centres
from utils.centre_queries import fetch_centre_by_id
from utils.helpers import toast_success, toast_error


# ── Session state key helpers ─────────────────────────────────────────────────

def _prefill_key(room_id: str, date_str: str) -> str:
    """Key for CSV-derived prefill counts: {interval_start → count}."""
    return f"csv_prefill_{room_id}_{date_str}"


def _csv_result_key(date_str: str) -> str:
    """Key for the full parse_csv result (preview, warnings, room_counts)."""
    return f"csv_result_{date_str}"


def render():
    # ── Header ────────────────────────────────────────────────────────
    h1, h2 = st.columns([4, 1])
    h1.title("Child Attendance")
    h1.markdown(
        '<p class="page-sub">Upload a CSV or enter counts manually '
        "· 15-minute intervals · feeds ratio monitor automatically</p>",
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

    centre_opts = {c["id"]: c["name"] for c in centres}
    saved_centre = (
        st.session_state.get("attendance_centre_id")
        or st.session_state.get("selected_centre_id")
        or centres[0]["id"]
    )

    cc1, cc2, cc3 = st.columns([2, 1, 1])
    centre_id = cc1.selectbox(
        "Centre",
        options=list(centre_opts.keys()),
        format_func=lambda x: centre_opts[x],
        index=list(centre_opts.keys()).index(saved_centre)
               if saved_centre in centre_opts else 0,
        key="att_centre_sel",
    )
    st.session_state.attendance_centre_id = centre_id

    attendance_date = cc2.date_input(
        "Date",
        value=date.today(),
        key="att_date",
        format="DD/MM/YYYY",
    )
    date_str = attendance_date.isoformat()

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

    # ── Room selector ─────────────────────────────────────────────────
    room_opts = {r["id"]: r["name"] for r in rooms}
    room_id   = cc3.selectbox(
        "Room",
        options=list(room_opts.keys()),
        format_func=lambda x: room_opts[x],
        key="att_room_sel",
    )
    selected_room = next((r for r in rooms if r["id"] == room_id), {})

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

    st.markdown("---")

    # ── Day summary ───────────────────────────────────────────────────
    _render_day_summary(rooms, room_id, centre_id, date_str)

    st.markdown("---")

    # ── CSV upload section ────────────────────────────────────────────
    # This section parses the CSV and stores room_counts in session state.
    # It does NOT rerun — the grid below reads from session state immediately
    # on the same render pass so pre-fill values appear without a second click.
    _render_csv_upload(
        centre_id=centre_id,
        rooms=rooms,
        intervals=intervals,
        date_str=date_str,
        room_id=room_id,
    )

    st.markdown("---")

    # ── Interval grid ─────────────────────────────────────────────────
    # Value priority for each interval's expected count:
    #   1. CSV prefill (written by _render_csv_upload into session state)
    #   2. Supabase saved value (saved_map)
    #   3. Zero
    #
    # The number_input widget captures whatever the user types.
    # Saving uses the widget values, not the prefill — so manual edits are
    # always honoured.

    prefill_key    = _prefill_key(room_id, date_str)
    csv_prefill    = st.session_state.get(prefill_key, {})
    prefill_active = bool(csv_prefill)

    now      = datetime.now().strftime("%H:%M:%S")
    is_today = attendance_date == date.today()

    # Room header
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:0.75rem;margin-bottom:0.5rem;">'
        f'<div style="width:14px;height:14px;border-radius:50%;background:{colour};'
        f'box-shadow:0 0 0 3px {colour}30;flex-shrink:0;"></div>'
        f'<span style="font-family:DM Serif Display,serif;font-size:1.1rem;color:#0d1f35;">'
        f'{selected_room.get("name","Room")}</span>'
        f'<span style="font-size:0.82rem;color:#7a90a8;margin-left:0.5rem;">'
        f'Capacity {capacity} · Ratio 1:{r_children} · '
        f'{attendance_date.strftime("%A %-d %B %Y")}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    if prefill_active:
        n_prefilled = sum(1 for v in csv_prefill.values() if v > 0)
        st.info(
            f"📋 **{n_prefilled} interval(s) pre-filled from CSV.** "
            "Edit any values below, then click **Save** to persist."
        )
    elif n_saved:
        st.caption(f"✅ {n_saved} of {len(intervals)} intervals have saved data.")
    else:
        st.info(
            "No data yet. Upload a CSV above, or fill in the counts below and click **Save**."
        )

    st.markdown(
        "**Expected and actual child counts per 15-minute interval.**"
    )

    # Column headers
    hc0, hc1, hc2, hc3, hc4 = st.columns([1.8, 1.4, 1.4, 1.4, 2.0])
    hc0.markdown("**Time**")
    hc1.markdown("**Expected**")
    hc2.markdown("**Actual**")
    hc3.markdown("**Cap %**")
    hc4.markdown("**Notes**")

    form_rows = []
    with st.form(key=f"attendance_form_{room_id}_{date_str}"):
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
                "exp",
                min_value=0,
                max_value=capacity or 100,
                value=exp_default,
                key=f"exp_{istart}",
                label_visibility="collapsed",
                step=1,
            )

            act_val = existing.get("actual_children")
            act = c2.number_input(
                "act",
                min_value=0,
                max_value=capacity or 100,
                value=int(act_val) if act_val is not None else 0,
                key=f"act_{istart}",
                label_visibility="collapsed",
                step=1,
            )
            actual_to_save = act if (act > 0 or act_val is not None) else None

            # Capacity % indicator
            n_for_pct = act if (act_val is not None or act > 0) else exp
            if n_for_pct > 0 and capacity:
                pct = round((n_for_pct / capacity) * 100)
                if pct >= 100:
                    pct_html = (
                        f'<span style="color:#991b1b;font-size:0.75rem;">⚠ {pct}%</span>'
                    )
                elif pct >= 80:
                    pct_html = (
                        f'<span style="color:#92400e;font-size:0.75rem;">{pct}%</span>'
                    )
                else:
                    pct_html = (
                        f'<span style="color:#14532d;font-size:0.75rem;">{pct}%</span>'
                    )
            else:
                pct_html = '<span style="color:#94a3b8;font-size:0.75rem;">—</span>'
            c3.markdown(pct_html, unsafe_allow_html=True)

            notes_default = existing.get("notes", "") or ""
            notes_val = c4.text_input(
                "notes",
                value=notes_default,
                key=f"notes_{istart}",
                label_visibility="collapsed",
                placeholder="optional",
            )

            form_rows.append({
                "interval_start":    istart,
                "interval_end":      iend,
                "expected_children": exp,
                "actual_children":   actual_to_save,
                "notes":             notes_val,
            })

        st.markdown("")
        sc1, sc2, sc3 = st.columns([1.2, 1.2, 4])
        submitted = sc1.form_submit_button(
            "💾  Save", type="primary", use_container_width=True,
        )
        clear_btn = sc2.form_submit_button(
            "🔄  Reset", use_container_width=True,
        )

    if clear_btn:
        # Clear CSV prefill so the grid reloads from Supabase
        st.session_state.pop(prefill_key, None)
        st.session_state.pop(_csv_result_key(date_str), None)
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
                        f"{selected_room.get('name','')} — "
                        f"{attendance_date.strftime('%-d %b %Y')}."
                    )
                    # Clear the CSV prefill now that data is saved to Supabase
                    st.session_state.pop(prefill_key, None)
                    st.session_state.pop(_csv_result_key(date_str), None)
                    st.rerun()
                except Exception as e:
                    toast_error(f"Could not save: {e}")

    st.markdown("")
    st.caption(
        "💡 Counts feed the Ratio Monitor. "
        "Use 'Expected' for planning the day, 'Actual' for live tracking."
    )


# ─────────────────────────────────────────────────────────────────────────────
# CSV UPLOAD SECTION
# ─────────────────────────────────────────────────────────────────────────────

def _render_csv_upload(
    centre_id: str,
    rooms: list[dict],
    intervals: list[dict],
    date_str: str,
    room_id: str,
):
    """
    CSV upload → parse → store counts in session state → pre-fill grid.

    Key design decisions:
    - Does NOT call st.rerun() after parsing. The grid on the same page
      reads from session state immediately in the same render pass.
    - Stores parse_csv result under _csv_result_key so warnings/preview
      survive reruns (e.g. after the user edits a number_input).
    - Stores per-room prefill counts under _prefill_key(room_id, date_str).
    - The Save button in the main grid (not here) persists to Supabase.
    """
    st.markdown("### 📂 Import from CSV")
    st.caption(
        "Upload a CSV with one row per child. "
        "The app counts how many children are in each room at every 15-minute interval "
        "and pre-fills the editable grid below."
    )

    # ── Sample download ───────────────────────────────────────────────
    sample = (
        "child_name,room_name,start_time,end_time\n"
        "Mia,Babies,08:00,16:30\n"
        "Leo,Babies,09:15,15:45\n"
        "Ava,Toddlers,07:30,17:00\n"
        "Noah,Preschool,09:00,15:00\n"
    )
    st.download_button(
        "⬇️  Download sample CSV",
        data=sample,
        file_name="attendance_template.csv",
        mime="text/csv",
        key="dl_sample_csv",
    )

    # ── File uploader ─────────────────────────────────────────────────
    uploaded = st.file_uploader(
        "Choose CSV file",
        type=["csv"],
        key="csv_upload_widget",
        label_visibility="collapsed",
    )

    result_key  = _csv_result_key(date_str)
    prefill_key = _prefill_key(room_id, date_str)

    if uploaded is None:
        # No file in uploader.  If we have a cached result from a previous
        # upload in this session, clear it so the grid reverts to saved data.
        if result_key in st.session_state:
            st.session_state.pop(result_key, None)
            # Do NOT pop prefill_key here — user may have already edited the
            # grid and not yet saved. Clearing prefill would wipe their edits.
        st.caption("No file selected.")
        return

    # ── Parse (or use cached result for this date) ────────────────────
    # Re-parse every time a new file is uploaded.  Streamlit gives uploaded
    # a new object on every render when a file is present, but the file
    # bytes are the same if the user hasn't changed the file.  Using a
    # cache keyed on (filename, size) avoids re-parsing on every re-render
    # caused by number_input interactions.
    cache_sig = f"{uploaded.name}_{uploaded.size}"
    cached    = st.session_state.get(result_key)

    if cached is None or cached.get("_sig") != cache_sig:
        # Fresh parse
        raw_bytes = uploaded.read()
        result    = parse_csv(
            file_bytes=raw_bytes,
            rooms=rooms,
            intervals=intervals,
        )
        result["_sig"] = cache_sig
        st.session_state[result_key] = result

        # Write prefill counts for every room into session state.
        # The grid for the currently selected room reads from prefill_key.
        # If the CSV covers other rooms too, their prefill keys are also set
        # so switching rooms immediately shows the right counts.
        if result["room_counts"]:
            for rid, counts in result["room_counts"].items():
                st.session_state[_prefill_key(rid, date_str)] = counts
    else:
        result = cached

    # ── Errors (blocking) ─────────────────────────────────────────────
    if result["errors"]:
        for err in result["errors"]:
            st.error(f"❌ {err}")
        for warn in result["warnings"]:
            st.warning(f"⚠️ {warn}")
        return

    # ── Warnings (non-blocking) ───────────────────────────────────────
    for warn in result["warnings"]:
        st.warning(f"⚠️ {warn}")

    preview_df  = result["preview_df"]
    room_counts = result["room_counts"]
    n_children  = result["n_children"]
    n_skipped   = result["n_skipped"]

    # ── Summary metrics ───────────────────────────────────────────────
    m1, m2, m3 = st.columns(3)
    m1.metric("Children loaded", n_children)
    m2.metric("Rows skipped",    n_skipped,
              delta="see warnings" if n_skipped else None,
              delta_color="inverse" if n_skipped else "off")
    m3.metric("Rooms with data", len(room_counts))

    # ── Per-child preview ─────────────────────────────────────────────
    st.markdown("**Per-child preview**")
    st.dataframe(preview_df, use_container_width=True, hide_index=True)

    # ── Per-room interval count preview ──────────────────────────────
    if room_counts:
        st.markdown("**Calculated interval counts by room**")
        st.caption(
            "These counts have been loaded into the editable grid below. "
            "Edit any value before saving."
        )

        room_name_map = {r["id"]: r["name"] for r in rooms}

        for rid, counts in room_counts.items():
            rname  = room_name_map.get(rid, rid)
            colour = next(
                (r.get("colour", "#3498DB") for r in rooms if r["id"] == rid),
                "#3498DB",
            )
            nonzero = [
                {"Time": iv_start[:5], "Expected children": cnt}
                for iv_start, cnt in sorted(counts.items())
                if cnt > 0
            ]
            if not nonzero:
                continue

            peak = max(v["Expected children"] for v in nonzero)
            st.markdown(
                f'<div style="display:flex;align-items:center;gap:0.5rem;'
                f'margin-bottom:0.3rem;">'
                f'<div style="width:10px;height:10px;border-radius:50%;'
                f'background:{colour};"></div>'
                f'<strong style="font-size:0.9rem;">{rname}</strong>'
                f'<span style="font-size:0.8rem;color:#7a90a8;margin-left:0.4rem;">'
                f'Peak {peak} · {len(nonzero)} interval(s) with children</span>'
                f'</div>',
                unsafe_allow_html=True,
            )
            st.dataframe(
                pd.DataFrame(nonzero),
                use_container_width=True,
                hide_index=True,
            )

    if room_counts:
        selected_room_name = room_name_map.get(room_id, "the selected room")  # type: ignore[name-defined]
        st.success(
            f"✅ Grid pre-filled for **{selected_room_name}**. "
            "Scroll down to review and edit, then click **Save**."
        )
    else:
        st.info("No interval data could be calculated — all rows were skipped.")


# ─────────────────────────────────────────────────────────────────────────────
# DAY SUMMARY STRIP
# ─────────────────────────────────────────────────────────────────────────────

def _render_day_summary(
    rooms: list[dict],
    selected_room_id: str,
    centre_id: str,
    date_str: str,
):
    """Compact per-room summary for the selected date."""
    with st.spinner("Loading centre summary…"):
        try:
            all_intervals = fetch_intervals_for_centre(centre_id, date_str)
        except Exception:
            all_intervals = []

    st.markdown(f"### 📅 {date_str} — All Rooms")
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
            peak_act = (
                max(int(r.get("actual_children") or 0) for r in act_rows)
                if act_rows else None
            )
            pct     = round((peak_exp / cap) * 100) if cap else 0
            act_str = f" / {peak_act} actual" if peak_act is not None else ""
            content = (
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
