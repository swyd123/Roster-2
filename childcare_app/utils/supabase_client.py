# utils/supabase_client.py
# ------------------------------------------------------------------
# Creates and returns a single shared Supabase client.
# All database calls in the app go through this module.
#
# CREDENTIAL RESOLUTION ORDER
# ------------------------------------------------------------------
# Key used:  SUPABASE_SERVICE_ROLE_KEY  (preferred — bypasses RLS)
#            SUPABASE_ANON_KEY          (fallback — subject to RLS)
#
# The service role key is required for write operations (creating
# staff, inserting records) because RLS policies on the anon role
# block those inserts with error 42501. The service role key bypasses
# RLS entirely, so always prefer it when available.
#
# Both keys are found in:
#   Supabase Dashboard → Project Settings → API
#
# Secrets are resolved in this order:
#   1. st.secrets  — Streamlit Cloud App Settings → Secrets (TOML):
#
#        SUPABASE_URL              = "https://xxx.supabase.co"
#        SUPABASE_SERVICE_ROLE_KEY = "eyJ..."   ← add this
#        SUPABASE_ANON_KEY         = "eyJ..."   ← keep as fallback
#        ORGANISATION_ID           = "your-uuid"
#
#   2. Environment variables / .env file — local development.
#      Add SUPABASE_SERVICE_ROLE_KEY to your .env file.
#
# The app works in both environments with no code changes required.
# ------------------------------------------------------------------

import os
import streamlit as st
from supabase import create_client, Client

# Load .env for local development.
# Safe no-op on Streamlit Cloud where no .env file exists.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass   # python-dotenv is optional; st.secrets or env vars cover Cloud


def _get_secret(key: str) -> str | None:
    """
    Resolve a configuration value from st.secrets first,
    then fall back to environment variables.
    """
    # 1. Try st.secrets (Streamlit Cloud or local .streamlit/secrets.toml)
    try:
        value = st.secrets.get(key)
        if value:
            return str(value)
    except Exception:
        # st.secrets unavailable outside Streamlit execution context
        pass

    # 2. Fall back to environment variables / .env
    return os.getenv(key)


@st.cache_resource   # Created once per session — Streamlit re-runs on every interaction
def get_supabase_client() -> Client:
    """
    Returns a connected Supabase client, cached for the session.

    Uses SUPABASE_SERVICE_ROLE_KEY when available so that write
    operations (INSERT, UPDATE, DELETE) are not blocked by RLS
    policies (error 42501). Falls back to SUPABASE_ANON_KEY only
    when the service role key is not configured.
    """
    url = _get_secret("SUPABASE_URL")

    # Prefer service role key — it bypasses RLS and allows all writes.
    # Fall back to anon key for read-only or public deployments.
    key = _get_secret("SUPABASE_SERVICE_ROLE_KEY") or _get_secret("SUPABASE_ANON_KEY")

    if not url or not key:
        st.error(
            "⚠️ **Supabase credentials are missing.**\n\n"
            "**On Streamlit Cloud:** add them in App Settings → Secrets:\n"
            "```toml\n"
            "SUPABASE_URL              = \"https://your-project.supabase.co\"\n"
            "SUPABASE_SERVICE_ROLE_KEY = \"eyJ...\"   # recommended — bypasses RLS\n"
            "SUPABASE_ANON_KEY         = \"eyJ...\"   # fallback\n"
            "ORGANISATION_ID           = \"your-org-uuid\"\n"
            "```\n"
            "**Locally:** add the same keys to your `.env` file.\n\n"
            "Both keys are in: Supabase Dashboard → Project Settings → API"
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
