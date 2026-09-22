"""
Shared helpers for the equipment-finance-leads skill: config loading, the
SQLite store, and domain normalization.

Not a pipeline phase — imported by the phase scripts in this directory.

The store model mirrors production-house-leads / nppes-new-clinics: scrape
everything into data/finance.db, classify in place, then export campaign
batches as Google Sheets with exported_at/batch_id stamps so the same
company is never worked twice.

TWO SIDES live in one store:
  companies  DEALER side — medical/dental equipment dealers scraped from
             Google Maps. The cold ICP we email.
  lenders    SUPPLY side — US equipment leasing companies gathered from
             Google search over the trade-directory / vendor-finance space.
             The specialist we connect dealers to (curated shortlist, not a
             cold blast — the fitted universe is only a few dozen companies).
"""
import json
import os
import sqlite3
from datetime import datetime
from urllib.parse import urlparse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(SCRIPT_DIR)
CONFIG_DIR = os.path.join(SKILL_DIR, "config")
DATA_DIR = os.path.join(SKILL_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "finance.db")

# Social/portfolio hosts — keep the URL as an activity signal but never derive
# a domain from them (no email enrichment against facebook.com etc.).
SOCIAL_HOSTS = {
    "facebook.com", "instagram.com", "vimeo.com", "youtube.com", "youtu.be",
    "linkedin.com", "linktr.ee", "twitter.com", "x.com", "tiktok.com",
    "squarespace.com", "wixsite.com", "wix.com", "business.site",
    "google.com", "yelp.com", "yellowpages.com", "mapquest.com",
}

# Captive-finance national distributors + OEMs. These already run their own
# leasing arms (Henry Schein Financial Services, Benco's financing, GE
# HealthCare captive finance, etc.), so they are NOT the ICP — a dealer wants
# us precisely because it has no captive arm. Deterministic denylist applied
# BEFORE the LLM classifier so a giant can never slip through as a DEALER.
# Matched as a normalized-substring test on the company name; keep this list
# to unambiguous nationals so legit regional dealers survive.
CAPTIVE_GIANTS = {
    "henry schein", "patterson dental", "patterson companies", "benco dental",
    "darby dental", "burkhart dental", "atlanta dental", "midway dental",
    "safco dental", "dc dental", "mckesson", "cardinal health", "medline",
    "owens minor", "ge healthcare", "siemens healthineers", "philips healthcare",
    "canon medical", "dentsply sirona", "danaher", "stryker", "medtronic",
    "boston scientific", "hillrom", "steris", "envista",
}


def load_settings():
    with open(os.path.join(CONFIG_DIR, "settings.json")) as f:
        return json.load(f)


def is_captive_giant(name):
    n = " ".join((name or "").lower().replace(",", " ").replace(".", " ").split())
    return any(g in n for g in CAPTIVE_GIANTS)


def norm_domain(website):
    """Website URL -> registrable root domain, or None for social/portfolio hosts."""
    if not website:
        return None
    try:
        host = urlparse(website if "://" in website else "https://" + website).netloc.lower()
    except ValueError:
        return None
    host = host.split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    if not host or "." not in host:
        return None
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in ("co", "com", "org", "net", "ac") and len(parts[-1]) == 2:
        root = ".".join(parts[-3:])
    else:
        root = ".".join(parts[-2:])
    if root in SOCIAL_HOSTS:
        return None
    return root


def get_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS companies (
        place_id      TEXT PRIMARY KEY,
        name          TEXT NOT NULL,
        website       TEXT,
        domain        TEXT,
        phone         TEXT,
        street        TEXT,
        city          TEXT,
        state         TEXT,
        postal        TEXT,
        country       TEXT,
        metro         TEXT,
        category      TEXT,
        categories    TEXT,
        rating        REAL,
        reviews       INTEGER,
        maps_url      TEXT,
        search_term   TEXT,
        scraped_at    TEXT,
        classification TEXT,
        class_reason  TEXT,
        classified_at TEXT,
        exported_at   TEXT,
        batch_id      INTEGER
    );
    CREATE INDEX IF NOT EXISTS idx_companies_class ON companies(classification);
    CREATE INDEX IF NOT EXISTS idx_companies_domain ON companies(domain);
    CREATE INDEX IF NOT EXISTS idx_companies_export ON companies(exported_at);

    CREATE TABLE IF NOT EXISTS lenders (
        domain        TEXT PRIMARY KEY,
        name          TEXT,
        website       TEXT,
        source        TEXT,
        snippet       TEXT,
        fit           TEXT,
        fit_reason    TEXT,
        scraped_at    TEXT,
        exported_at   TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_lenders_fit ON lenders(fit);

    CREATE TABLE IF NOT EXISTS runs (run_at TEXT, script TEXT, summary TEXT);
    """)
    return conn


def log_run(conn, script, summary):
    conn.execute("INSERT INTO runs VALUES (?, ?, ?)",
                 (datetime.now().isoformat(timespec="seconds"), script, summary))
    conn.commit()
