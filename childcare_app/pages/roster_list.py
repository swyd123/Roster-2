# pages/roster_list.py — Roster periods overview: list, create, copy, publish
import streamlit as st
from datetime import date, timedelta
from utils.roster_queries import (
    fetch_roster_periods, create_roster_period,
    archive_roster_period, copy_shifts_to_period,
)
from utils.staff_queries import fetch_centres
from utils.helpers import toast_success, toast_error, fmt_date


PERIOD_STATUS_CFG = {
    "draft":     {"icon": "📝", "bg": "#eff6ff", "text": "#1d4ed8"},
    "published": {"icon": "✅", "bg": "#f0fdf4", "text": "#166534"},
    "archived":  {"icon": "📦", "bg": "#f1f5f9", "text": "#475569"},
}


def render():
    h1, h2 = st.columns([4, 1])
    h1.title("Rosters")
    h1.markdown('<p class="page-sub">Manage weekly and fortnightly rosters for your centre</p>',
                unsafe_allow_html=True)
    with h2:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("➕  New Roster", type="primary", use_container_width=True):
            st.session_state["show_create_roster"] = True
            st.rerun()

    # ── Centre selector ───────────────────────────────────────────────
    centres = fetch_centres()
    if not centres:
        st.warning("No centres found.")
        return

    centre_opts = {c["id"]: c["name"] for c in centres}
    saved = st.session_state.get("roster_centre_id") or centres[0]["id"]

    centre_id = st.selectbox(
        "Centre", options=list(centre_opts.keys()),
        format_func=lambda x: centre_opts[x],
        index=list(centre_opts.keys()).index(saved) if saved in centre_opts else 0,
        key="roster_list_centre",
    )
    st.session_state.roster_centre_id = centre_id

    # ── Create roster period form ─────────────────────────────────────
    if st.session_state.get("show_create_roster"):
        st.markdown("---")
        _render_create_form(centre_id)
        st.markdown("---")

    # ── Load periods ──────────────────────────────────────────────────
    with st.spinner("Loading rosters…"):
        try:
            periods = fetch_roster_periods(centre_id, limit=30)
        except Exception as e:
            toast_error(f"Could not load rosters: {e}")
            return

    if not periods:
        st.info("No rosters created yet. Click **➕ New Roster** to get started.")
        return

    # ── Summary metrics ───────────────────────────────────────────────
    n_draft     = sum(1 for p in periods if p.get("status") == "draft")
    n_published = sum(1 for p in periods if p.get("status") == "published")
    n_archived  = sum(1 for p in periods if p.get("status") == "archived")
    m1, m2, m3 = st.columns(3)
    m1.metric("Draft",     n_draft)
    m2.metric("Published", n_published)
    m3.metric("Archived",  n_archived)

    st.markdown("---")

    # ── Period cards ──────────────────────────────────────────────────
    for p in periods:
        _render_period_card(p, centre_id)


def _render_period_card(p: dict, centre_id: str):
    pid    = p["id"]
    status = p.get("status", "draft")
    cfg    = PERIOD_STATUS_CFG.get(status, PERIOD_STATUS_CFG["draft"])
    sd     = fmt_date(p.get("start_date"))
    ed     = fmt_date(p.get("end_date"))
    pub    = p.get("publisher") or {}
    pub_by = f"{pub.get('first_name','')} {pub.get('last_name','')}".strip()
    pub_at = fmt_date(p.get("published_at"))

    with st.expander(
        f"{cfg['icon']}  **{sd} → {ed}**  ·  {status.title()}",
        expanded=(status == "draft"),
    ):
        ic1, ic2, ic3 = st.columns(3)
        ic1.markdown(f"**Period**  \n{sd} to {ed}")
        ic2.markdown(f"**Status**  \n"
                     f'<span style="background:{cfg["bg"]};color:{cfg["text"]};'
                     f'padding:2px 9px;border-radius:99px;font-size:0.78rem;font-weight:600;">'
                     f'{cfg["icon"]} {status.title()}</span>',
                     unsafe_allow_html=True)
        ic3.markdown(
            f"**Published by**  \n{pub_by or '—'}" +
            (f"  \n{pub_at}" if pub_at and pub_at != "—" else "")
        )
        if p.get("notes"):
            st.markdown(f"_Notes: {p['notes']}_")

        st.markdown("")
        ab1, ab2, ab3, ab4, _ = st.columns([1.2, 1.2, 1.2, 1.2, 3])

        # Open builder
        if ab1.button("🗓️  Build", key=f"build_{pid}", use_container_width=True,
                      type="primary"):
            st.session_state.roster_period_id     = pid
            st.session_state.roster_centre_id     = centre_id
            st.session_state.page = "roster_builder"
            st.rerun()

        # Copy to new period
        if ab2.button("📋  Copy", key=f"copy_{pid}", use_container_width=True):
            st.session_state[f"copy_from_{pid}"] = True
            st.rerun()

        # Archive
        if status in ("published", "draft"):
            if ab3.button("📦  Archive", key=f"arch_{pid}", use_container_width=True):
                st.session_state[f"confirm_arch_{pid}"] = True
                st.rerun()

        if st.session_state.get(f"confirm_arch_{pid}"):
            st.warning("Archive this roster period?")
            ay, an = st.columns(2)
            if ay.button("Archive", key=f"ay_{pid}", type="primary", use_container_width=True):
                try:
                    archive_roster_period(pid)
                    toast_success("Roster archived.")
                    st.session_state.pop(f"confirm_arch_{pid}", None)
                    st.rerun()
                except Exception as e:
                    toast_error(str(e))
            if an.button("Cancel", key=f"an_{pid}", use_container_width=True):
                st.session_state.pop(f"confirm_arch_{pid}", None)
                st.rerun()

        # Copy form
        if st.session_state.get(f"copy_from_{pid}"):
            st.markdown("**Copy this roster to a new week:**")
            with st.form(f"copy_form_{pid}"):
                try:
                    old_start = date.fromisoformat(p["start_date"])
                    new_start_default = old_start + timedelta(weeks=1)
                except Exception:
                    new_start_default = date.today()
                new_start = st.date_input("New roster start date",
                                           value=new_start_default, format="DD/MM/YYYY")
                try:
                    old_end   = date.fromisoformat(p["end_date"])
                    period_len = (old_end - old_start).days
                    new_end    = new_start + timedelta(days=period_len)
                except Exception:
                    new_end = new_start + timedelta(days=6)

                sc1, sc2 = st.columns(2)
                if sc1.form_submit_button("Copy", type="primary", use_container_width=True):
                    try:
                        new_period = create_roster_period(
                            centre_id=centre_id,
                            start_date=new_start.isoformat(),
                            end_date=new_end.isoformat(),
                        )
                        offset = (new_start - old_start).days
                        n      = copy_shifts_to_period(pid, new_period["id"], centre_id, offset)
                        toast_success(f"Copied {n} shifts to new roster ({new_start} → {new_end}).")
                        st.session_state.pop(f"copy_from_{pid}", None)
                        st.rerun()
                    except Exception as e:
                        toast_error(str(e))
                if sc2.form_submit_button("Cancel", use_container_width=True):
                    st.session_state.pop(f"copy_from_{pid}", None)
                    st.rerun()


def _render_create_form(centre_id: str):
    st.markdown("### Create New Roster Period")
    with st.form("create_roster_form"):
        next_mon = date.today() + timedelta(days=(7 - date.today().weekday()) % 7 or 7)
        fc1, fc2 = st.columns(2)
        start_d  = fc1.date_input("Start date (Monday) *",
                                   value=next_mon, format="DD/MM/YYYY")
        period   = fc2.selectbox("Period length",
                                  options=["1 week (7 days)", "2 weeks (14 days)"])
        days     = 6 if "1 week" in period else 13
        end_d    = start_d + timedelta(days=days)
        st.caption(f"Period: {start_d.strftime('%-d %b')} → {end_d.strftime('%-d %b %Y')}")
        notes = st.text_input("Notes (optional)", placeholder="e.g. School holidays week")
        sc1, sc2 = st.columns(2)
        submitted = sc1.form_submit_button("Create Roster", type="primary",
                                            use_container_width=True)
        cancelled = sc2.form_submit_button("Cancel", use_container_width=True)

    if cancelled:
        st.session_state.pop("show_create_roster", None)
        st.rerun()
    if submitted:
        try:
            new_p = create_roster_period(centre_id, start_d.isoformat(),
                                          end_d.isoformat(), notes)
            toast_success(f"Roster created: {start_d} to {end_d}.")
            st.session_state.pop("show_create_roster", None)
            st.session_state.roster_period_id = new_p["id"]
            st.session_state.page = "roster_builder"
            st.rerun()
        except Exception as e:
            err = str(e)
            if "unique" in err.lower():
                toast_error(f"A roster for {start_d}–{end_d} already exists.")
            else:
                toast_error(f"Could not create roster: {err}")
