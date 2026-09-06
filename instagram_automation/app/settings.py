"""Central configuration.

The OpenAI/LLM key lives in `.env` (never in the DB) per project policy.
Instagram account credentials and the optional News API key live in the
`rags` store (SQLite) and are editable from the frontend Settings panel.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# ---- Filesystem ----------------------------------------------------------
IMAGES_DIR = BASE_DIR / "images"
PREVIEWS_DIR = IMAGES_DIR / "previews"
DB_FILE = BASE_DIR / "posts.db"

IMAGES_DIR.mkdir(exist_ok=True)
PREVIEWS_DIR.mkdir(exist_ok=True)

# ---- Database (PostgreSQL) ----------------------------------------------
# Primary connection string for the containerized deployment. Falls back to
# assembling one from discrete PG* vars, then to a localhost default so a
# developer running Postgres locally still works out of the box.
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
if not DATABASE_URL:
    _pg_user = os.getenv("PGUSER", "instagram")
    _pg_pass = os.getenv("PGPASSWORD", "instagram")
    _pg_host = os.getenv("PGHOST", "localhost")
    _pg_port = os.getenv("PGPORT", "5432")
    _pg_db = os.getenv("PGDATABASE", "instagram_business")
    DATABASE_URL = f"postgresql://{_pg_user}:{_pg_pass}@{_pg_host}:{_pg_port}/{_pg_db}"

# One-time migration source: the old embedded SQLite file. When present (and
# Postgres is still empty) its rows are copied into Postgres on first boot.
LEGACY_SQLITE_DB = Path(os.getenv("LEGACY_SQLITE_DB", str(DB_FILE)))

# ---- LLM (key from .env only) -------------------------------------------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()
# Hard ceiling so a runaway generation can never burn the budget. For reasoning
# models (gpt-5*/o-series) this bounds reasoning + visible output combined, so
# keep it high enough that the JSON is never truncated by reasoning spend.
LLM_MAX_OUTPUT_TOKENS = int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "2200"))

# Reasoning-model controls (ignored by non-reasoning models like gpt-4.1-nano).
#   reasoning_effort: minimal | low | medium | high  -> "minimal" keeps the
#     (billed-as-output) reasoning tokens tiny, which is what this cost-sensitive
#     copywriting pipeline wants.
#   verbosity: ""(unset) | low | medium | high -> optional GPT-5 output-length hint.
LLM_REASONING_EFFORT = os.getenv("LLM_REASONING_EFFORT", "minimal").strip()
LLM_VERBOSITY = os.getenv("LLM_VERBOSITY", "").strip()

# ---- Public image hosting (GitHub raw) ----------------------------------
# Defaults here; can be overridden per-deploy via .env or the rags settings.
GITHUB_USERNAME = os.getenv("GITHUB_USERNAME", "skarthik06").strip()
GITHUB_REPO = os.getenv("GITHUB_REPO", "business-sk").strip()   # repo renamed from instagram_automation → business-sk (2026-09-05)
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main").strip()
# Personal-access token to authenticate git pushes for public image hosting at
# PUBLISH time. Set in .env to enable in-container live publishing.
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()

# ---- Instagram Graph API version (publisher + insights) -----------------
GRAPH_API_VERSION = os.getenv("GRAPH_API_VERSION", "v26.0").strip()
# Instagram carousel max items PER POST via the Content Publishing API. The manual app
# allows 20, but the API still rejects >10 with "(#100) too many attachments to qualify
# as a carousel" — so publishing splits into consecutive 10-item carousels. Configurable.
IG_MAX_CAROUSEL = int(os.getenv("IG_MAX_CAROUSEL", "10"))

# ---- Generation defaults (editable in UI) -------------------------------
NICHES = ("quotes", "news")
DEFAULT_POSTS_PER_BATCH = 3
DEFAULT_SLIDES_PER_POST = 4
MAX_POSTS_PER_BATCH = 6
MAX_SLIDES_PER_POST = 6

DEFAULT_HANDLE = os.getenv("IG_HANDLE", "sparkle06.exe").strip()

# ---- Single-admin authentication (private app, NOT multi-user SaaS) -------
# One administrator only. Credentials come from .env; change them there.
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin").strip()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin").strip()
# Token-signing secret. If unset, derived deterministically from the password so
# tokens stay valid across restarts without persisting a file.
ADMIN_SECRET = os.getenv("ADMIN_SECRET", "").strip()

# ---- Google sign-in (primary login) --------------------------------------
# The Google OAuth *Client ID* is public (safe in the browser). Only accounts
# whose verified email is in GOOGLE_ALLOWED_EMAILS may sign in.
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_ALLOWED_EMAILS = os.getenv("GOOGLE_ALLOWED_EMAILS", "").strip()
