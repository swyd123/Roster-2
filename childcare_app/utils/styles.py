# utils/styles.py
# Global CSS injected once via app.py.
# Design: institutional calm — deep navy, warm white, eucalyptus accent.
# Typography: DM Serif Display (headings) + DM Sans (body)

GLOBAL_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Serif+Display&family=DM+Sans:wght@300;400;500;600&display=swap');

/* ── Reset & base ────────────────────────────────────────────── */
html, body, [class*="css"] {
    font-family: 'DM Sans', sans-serif !important;
}
.block-container {
    padding: 1.8rem 2.5rem 3rem !important;
    max-width: 1300px;
}

/* ── Sidebar ─────────────────────────────────────────────────── */
[data-testid="stSidebar"] {
    background: #0d1f35 !important;
    border-right: none;
}
[data-testid="stSidebar"] * { color: #c8d6e5 !important; }
[data-testid="stSidebar"] .sidebar-brand {
    font-family: 'DM Serif Display', serif;
    font-size: 1.25rem;
    color: #ffffff !important;
    letter-spacing: 0.02em;
}
[data-testid="stSidebar"] hr {
    border-color: #1e3a55 !important;
}
[data-testid="stSidebar"] .stButton > button {
    background: transparent !important;
    border: none !important;
    color: #a8bfd4 !important;
    text-align: left !important;
    font-size: 0.88rem;
    padding: 0.45rem 0.75rem !important;
    border-radius: 6px !important;
    transition: all 0.15s;
}
[data-testid="stSidebar"] .stButton > button:hover {
    background: #1e3a55 !important;
    color: #ffffff !important;
}
[data-testid="stSidebar"] .nav-active > button {
    background: #1a6b4a !important;
    color: #ffffff !important;
}

/* ── Page headings ───────────────────────────────────────────── */
h1 {
    font-family: 'DM Serif Display', serif !important;
    font-size: 2rem !important;
    color: #0d1f35 !important;
    letter-spacing: -0.01em;
    margin-bottom: 0.1rem !important;
}
h2 {
    font-family: 'DM Serif Display', serif !important;
    font-size: 1.35rem !important;
    color: #0d1f35 !important;
}
h3 {
    font-family: 'DM Sans', sans-serif !important;
    font-weight: 600 !important;
    font-size: 1rem !important;
    color: #1e3a55 !important;
    text-transform: uppercase;
    letter-spacing: 0.06em;
}

/* ── Buttons ─────────────────────────────────────────────────── */
.stButton > button {
    font-family: 'DM Sans', sans-serif !important;
    font-weight: 500 !important;
    border-radius: 6px !important;
    font-size: 0.875rem !important;
    transition: all 0.15s ease !important;
}
.stButton > button[kind="primary"] {
    background: #0d1f35 !important;
    border-color: #0d1f35 !important;
    color: #ffffff !important;
}
.stButton > button[kind="primary"]:hover {
    background: #1a6b4a !important;
    border-color: #1a6b4a !important;
}
.stButton > button[kind="secondary"] {
    background: #ffffff !important;
    border: 1px solid #c8d6e5 !important;
    color: #0d1f35 !important;
}
.stButton > button[kind="secondary"]:hover {
    border-color: #0d1f35 !important;
    background: #f5f8fb !important;
}

/* ── Form inputs ─────────────────────────────────────────────── */
.stTextInput input, .stSelectbox select,
.stTextArea textarea, .stDateInput input {
    border-radius: 6px !important;
    border: 1px solid #d0dde8 !important;
    font-family: 'DM Sans', sans-serif !important;
    font-size: 0.9rem !important;
    background: #fafcfe !important;
}
.stTextInput input:focus, .stSelectbox select:focus,
.stTextArea textarea:focus {
    border-color: #1a6b4a !important;
    box-shadow: 0 0 0 2px rgba(26,107,74,0.15) !important;
}
label { font-size: 0.83rem !important; font-weight: 500 !important; color: #4a6079 !important; }

/* ── Metric cards ────────────────────────────────────────────── */
[data-testid="stMetric"] {
    background: #ffffff;
    border: 1px solid #e4edf5;
    border-radius: 10px;
    padding: 1.1rem 1.3rem !important;
    box-shadow: 0 1px 4px rgba(13,31,53,0.06);
}
[data-testid="stMetricValue"] {
    font-family: 'DM Serif Display', serif !important;
    font-size: 2rem !important;
    color: #0d1f35 !important;
}
[data-testid="stMetricLabel"] {
    font-size: 0.78rem !important;
    font-weight: 500 !important;
    letter-spacing: 0.05em !important;
    color: #7a90a8 !important;
    text-transform: uppercase;
}

/* ── Alerts ──────────────────────────────────────────────────── */
.stAlert { border-radius: 8px !important; font-size: 0.9rem !important; }

/* ── Expanders ───────────────────────────────────────────────── */
[data-testid="stExpander"] {
    border: 1px solid #e4edf5 !important;
    border-radius: 10px !important;
    background: #ffffff !important;
    margin-bottom: 0.6rem !important;
    box-shadow: 0 1px 3px rgba(13,31,53,0.04);
}
[data-testid="stExpander"]:hover {
    border-color: #b0c8dc !important;
    box-shadow: 0 2px 8px rgba(13,31,53,0.08) !important;
}

/* ── Tabs ─────────────────────────────────────────────────────── */
.stTabs [data-baseweb="tab-list"] {
    gap: 0 !important;
    border-bottom: 2px solid #e4edf5 !important;
    background: transparent !important;
}
.stTabs [data-baseweb="tab"] {
    font-family: 'DM Sans', sans-serif !important;
    font-weight: 500 !important;
    font-size: 0.875rem !important;
    color: #7a90a8 !important;
    padding: 0.65rem 1.2rem !important;
    border-bottom: 2px solid transparent !important;
    margin-bottom: -2px !important;
    border-radius: 0 !important;
    background: transparent !important;
}
.stTabs [aria-selected="true"] {
    color: #0d1f35 !important;
    border-bottom: 2px solid #1a6b4a !important;
}

/* ── Dividers ────────────────────────────────────────────────── */
hr { border-color: #e4edf5 !important; margin: 1rem 0 !important; }

/* ── Status pill component ───────────────────────────────────── */
.pill {
    display: inline-block;
    padding: 2px 10px;
    border-radius: 99px;
    font-size: 0.75rem;
    font-weight: 600;
    letter-spacing: 0.03em;
}
.pill-green  { background: #d4f0e4; color: #0f6b3a; }
.pill-red    { background: #fde8e8; color: #991b1b; }
.pill-amber  { background: #fef3cd; color: #92510a; }
.pill-grey   { background: #e8eff5; color: #4a6079; }
.pill-blue   { background: #dbeafe; color: #1d4ed8; }

/* ── Staff card ──────────────────────────────────────────────── */
.staff-card {
    background: #ffffff;
    border: 1px solid #e4edf5;
    border-radius: 12px;
    padding: 1.1rem 1.4rem;
    margin-bottom: 0.65rem;
    box-shadow: 0 1px 4px rgba(13,31,53,0.05);
    transition: box-shadow 0.15s;
}
.staff-card:hover {
    box-shadow: 0 4px 14px rgba(13,31,53,0.1);
    border-color: #b0c8dc;
}
.staff-name {
    font-family: 'DM Sans', sans-serif;
    font-weight: 600;
    font-size: 1rem;
    color: #0d1f35;
}
.staff-meta {
    font-size: 0.82rem;
    color: #7a90a8;
    margin-top: 2px;
}

/* ── Qual badge colours ──────────────────────────────────────── */
.qual-ok       { color: #0f6b3a; }
.qual-warn     { color: #92510a; }
.qual-critical { color: #b91c1c; }
.qual-expired  { color: #7a90a8; text-decoration: line-through; }

/* ── Section label ───────────────────────────────────────────── */
.section-label {
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: #7a90a8;
    margin-bottom: 0.5rem;
}

/* ── Availability grid ───────────────────────────────────────── */
.avail-day {
    background: #f5f8fb;
    border: 1px solid #e4edf5;
    border-radius: 8px;
    padding: 0.75rem;
    text-align: center;
    font-size: 0.82rem;
    font-weight: 600;
    color: #4a6079;
}
.avail-day.available {
    background: #d4f0e4;
    border-color: #a3d9bc;
    color: #0f6b3a;
}

/* ── DataFrames ──────────────────────────────────────────────── */
.stDataFrame { border-radius: 10px !important; }
.stDataFrame table { font-size: 0.875rem !important; }

/* ── Page subtitle ───────────────────────────────────────────── */
.page-sub {
    font-size: 0.88rem;
    color: #7a90a8;
    margin-top: -0.3rem;
    margin-bottom: 1.2rem;
}

/* ── Back button override ────────────────────────────────────── */
.back-btn > button {
    background: transparent !important;
    border: none !important;
    color: #7a90a8 !important;
    font-size: 0.85rem !important;
    padding: 0 !important;
}
.back-btn > button:hover { color: #0d1f35 !important; }
</style>
"""
