"""Shared store + helpers for the production-demand-leads skill.

SQLite-backed like nppes-new-clinics / energy-emaas-leads: collectors write
companies + signals into data/demand.db; export_batch.py cuts campaign sheets
from it. A company is keyed by a normalized name (norm) so the same brand
surfaced by different signals lands on one row and signals stack.
"""
import json
import os
import re
import sqlite3
from datetime import datetime, timezone

from dotenv import load_dotenv

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(SCRIPT_DIR, "..", "..", "..", ".env")
TOKEN_PATH = os.path.join(SCRIPT_DIR, "..", "..", "..", "token.json")
DB_PATH = os.path.join(SCRIPT_DIR, "..", "data", "demand.db")
load_dotenv(ENV_PATH)

APIFY_API_TOKEN = os.getenv("APIFY_API_TOKEN")
AZURE_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
AZURE_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21")
AZURE_DEPLOYMENT_FAST = os.getenv("AZURE_OPENAI_DEPLOYMENT_FAST")

LEGAL_TAIL_RE = re.compile(
    r"\b(incorporated|inc|llc|l\.l\.c|corp|corporation|co|company|ltd|limited|"
    r"holdings?|group|pbc|lp|l\.p)\b\.?", re.I)

def normalize(name):
    """Company-identity key: lowercase, no punctuation, legal tails stripped."""
    s = (name or "").lower()
    s = LEGAL_TAIL_RE.sub(" ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript("""
    CREATE TABLE IF NOT EXISTS companies (
      id INTEGER PRIMARY KEY,
      norm TEXT UNIQUE NOT NULL,
      name TEXT NOT NULL,
      size TEXT DEFAULT '',
      city TEXT DEFAULT '',
      state TEXT DEFAULT '',
      website TEXT DEFAULT '',
      category TEXT DEFAULT '',          -- '', BRAND, AGENCY_OR_PRODUCTION,
                                         -- ENTERPRISE_INHOUSE, NONPROFIT_GOV_EDU, UNKNOWN
      classify_reason TEXT DEFAULT '',
      ad_checked INTEGER DEFAULT 0,
      ad_active INTEGER,
      ad_oldest_days INTEGER,
      ad_video_share REAL,
      ad_distinct_bodies INTEGER,
      ad_stale INTEGER DEFAULT 0,
      exported_at TEXT DEFAULT '',
      added_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS signals (
      id INTEGER PRIMARY KEY,
      company_id INTEGER NOT NULL REFERENCES companies(id),
      type TEXT NOT NULL,                -- video_job | new_exec | funding | ad_stale
      key TEXT NOT NULL,                 -- job_id / post url / accession — dedupe key
      detail TEXT DEFAULT '{}',          -- JSON blob, shape per type
      event_date TEXT DEFAULT '',
      added_at TEXT NOT NULL,
      UNIQUE(company_id, type, key)
    );
    """)
    return con

def upsert_company(con, name, size="", city="", state=""):
    """Insert or fetch by normalized name. Fills blank size/city/state on match."""
    norm = normalize(name)
    if not norm:
        return None
    row = con.execute("SELECT id, size, city, state FROM companies WHERE norm=?",
                      (norm,)).fetchone()
    if row:
        cid, osize, ocity, ostate = row
        con.execute(
            "UPDATE companies SET size=CASE WHEN size='' THEN ? ELSE size END,"
            " city=CASE WHEN city='' THEN ? ELSE city END,"
            " state=CASE WHEN state='' THEN ? ELSE state END WHERE id=?",
            (size or "", city or "", state or "", cid))
        return cid
    cur = con.execute(
        "INSERT INTO companies (norm, name, size, city, state, added_at)"
        " VALUES (?,?,?,?,?,?)",
        (norm, name.strip(), size or "", city or "", state or "", now_iso()))
    return cur.lastrowid

def add_signal(con, company_id, sig_type, key, detail, event_date=""):
    """Idempotent signal insert. Returns True if new."""
    try:
        con.execute(
            "INSERT INTO signals (company_id, type, key, detail, event_date, added_at)"
            " VALUES (?,?,?,?,?,?)",
            (company_id, sig_type, key, json.dumps(detail, ensure_ascii=False),
             event_date, now_iso()))
        return True
    except sqlite3.IntegrityError:
        return False

def get_google_service():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    with open(TOKEN_PATH) as f:
        token_data = json.load(f)
    creds = Credentials(
        token=token_data["token"],
        refresh_token=token_data["refresh_token"],
        token_uri=token_data["token_uri"],
        client_id=token_data["client_id"],
        client_secret=token_data["client_secret"],
        scopes=token_data.get("scopes", ["https://www.googleapis.com/auth/spreadsheets"]),
    )
    if creds.expired:
        creds.refresh(Request())
        token_data["token"] = creds.token
        with open(TOKEN_PATH, "w") as f:
            json.dump(token_data, f)
    return build("sheets", "v4", credentials=creds)
