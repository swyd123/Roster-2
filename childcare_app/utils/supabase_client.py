# utils/supabase_client.py
# ------------------------------------------------------------------
# Creates and returns a single shared Supabase client.
# All database calls in the app go through this module.
#
# CREDENTIAL RESOLUTION ORDER
# ------------------------------------------------------------------
# 1. st.secrets  — used automatically on Streamlit Cloud.
#                  Add secrets in the Streamlit Cloud dashboard under
#                  App Settings → Secrets, in TOML format:
#
#                      SUPABASE_URL      = "https://xxx.supabase.co"
#                      SUPABASE_ANON_KEY = "eyJ..."
#                      ORGANISATION_ID   = "your-uuid"
#
# 2. Environment variables / .env file — used for local development.
#                  Copy .env.example to .env and fill in your values.
#                  python-dotenv loads the file automatically.
#
# The app works in both environments with no code changes required.
# ------------------------------------------------------------------

import os
import streamlit as st
from supabase import create_client, Client

# Load .env for local development.
# This is a no-op on Streamlit Cloud (no .env file present there),
# so it is always safe to call.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass   # python-dotenv is optional; st.secrets or env vars cover Cloud


def _get_secret(key: str) -> str | None:
    """
    Resolve a configuration value from st.secrets first,
    then fall back to environment variables.

    st.secrets is populated from:
      - Streamlit Cloud App Settings → Secrets (production)
      - .streamlit/secrets.toml        (local alternative to .env)

    Environment variables are populated from:
      - The .env file (via python-dotenv, local development)
      - The shell environment (CI, Docker, etc.)
    """
    # 1. Try st.secrets (Streamlit Cloud or local secrets.toml)
    try:
        value = st.secrets.get(key)
        if value:
            return str(value)
    except Exception:
        # st.secrets is not available in all execution contexts
        # (e.g. when running plain Python tests). Silently continue.
        pass

    # 2. Fall back to environment variables / .env
    return os.getenv(key)


@st.cache_resource   # Created once per session — Streamlit re-runs on every interaction
def get_supabase_client() -> Client:
    """
    Returns a connected Supabase client, cached for the session.

    Credentials are read from st.secrets (Streamlit Cloud) or
    environment variables / .env (local development).
    """
    url = _get_secret("SUPABASE_URL")
    key = _get_secret("SUPABASE_ANON_KEY")

    if not url or not key:
        st.error(
            "⚠️ **Supabase credentials are missing.**\n\n"
            "**On Streamlit Cloud:** add them in App Settings → Secrets:\n"
            "```toml\n"
            "SUPABASE_URL      = \"https://your-project.supabase.co\"\n"
            "SUPABASE_ANON_KEY = \"eyJ...\"\n"
            "ORGANISATION_ID   = \"your-org-uuid\"\n"
            "```\n"
            "**Locally:** copy `.env.example` to `.env` and fill in your values.\n\n"
            "Both values are in: Supabase Dashboard → Project Settings → API"
        )
        st.stop()   # Nothing in the app works without a DB connection

    return create_client(url, key)


def get_organisation_id() -> str:
    """
    Returns the Organisation ID this app is scoped to.

    Set via st.secrets or the ORGANISATION_ID environment variable.
    Find the value in your organisations table after first-time setup.
    """
    org_id = _get_secret("ORGANISATION_ID")

    if not org_id:
        st.error(
            "⚠️ **ORGANISATION_ID is not configured.**\n\n"
            "**On Streamlit Cloud:** add it in App Settings → Secrets:\n"
            "```toml\nORGANISATION_ID = \"your-organisation-uuid\"\n```\n"
            "**Locally:** add it to your `.env` file.\n\n"
            "Find the value by running this in the Supabase SQL Editor:\n"
            "```sql\nSELECT id, name FROM organisations LIMIT 10;\n```"
        )
        st.stop()

    return org_id
