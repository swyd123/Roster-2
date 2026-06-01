# utils/supabase_client.py
# ------------------------------------------------------------------
# Creates and returns a single shared Supabase client.
# All database calls in the app go through this module.
#
# HOW IT WORKS:
#   - Reads your SUPABASE_URL and SUPABASE_ANON_KEY from the .env file
#   - Creates one client object and caches it (so it isn't re-created
#     on every page interaction — Streamlit re-runs the whole script
#     on every click, so caching matters)
# ------------------------------------------------------------------

import os
from dotenv import load_dotenv
import streamlit as st
from supabase import create_client, Client

# Load values from the .env file into environment variables
load_dotenv()


@st.cache_resource          # Cache the client — only created once per session
def get_supabase_client() -> Client:
    """
    Returns a connected Supabase client.

    Reads credentials from environment variables (set in .env file).
    Raises a clear error if the credentials are missing so the developer
    knows exactly what to fix.
    """
    url  = os.getenv("SUPABASE_URL")
    key  = os.getenv("SUPABASE_ANON_KEY")

    if not url or not key:
        st.error(
            "⚠️ Supabase credentials are missing.\n\n"
            "Please create a `.env` file in the project folder with:\n"
            "```\n"
            "SUPABASE_URL=https://your-project.supabase.co\n"
            "SUPABASE_ANON_KEY=your-anon-key\n"
            "```\n"
            "You can find these in: Supabase Dashboard → Project Settings → API"
        )
        st.stop()           # Halt the app — nothing works without credentials

    return create_client(url, key)


def get_organisation_id() -> str:
    """
    Returns the Organisation ID this app is scoped to.
    Set in the .env file as ORGANISATION_ID.
    """
    org_id = os.getenv("ORGANISATION_ID")

    if not org_id:
        st.error(
            "⚠️ ORGANISATION_ID is missing from your .env file.\n\n"
            "Add it after inserting your first organisation into the database:\n"
            "```\nORGANISATION_ID=your-organisation-uuid\n```"
        )
        st.stop()

    return org_id
