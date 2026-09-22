"""
LENDER side — gather US equipment leasing / finance companies (the "specialist"
we connect dealers to) into the lenders table via Apify google-search-scraper
over the vendor-finance / medical-dental-financing query set in config.

This is a COLLECTOR (the mechanical half of the Claude-in-session judge flow):
it stores every plausible lender company site with fit=NULL. The FIT decision
(KEEP small-ticket + medical/dental-friendly + vendor-finance vs SKIP the big
banks and off-topic hits) is a judgment Claude makes in-session, then writes
back with apply_lender_fit.py. The fitted universe is only a few dozen
companies — this is a curated shortlist to pitch, not a cold blast.

Junk hosts (media, aggregators, the trade-directory portals themselves,
review sites) are filtered out — google returns the lender company sites
directly for these queries.

Usage:
  python3 -W ignore .claude/skills/equipment-finance-leads/scripts/pull_lenders.py [--dry_run]
"""
import argparse
import os
import sys
import time
from datetime import datetime

import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from finance_common import get_db, load_settings, log_run, norm_domain

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", ".env"))
APIFY_TOKEN = os.environ["APIFY_API_TOKEN"]
APIFY_BASE = "https://api.apify.com/v2"
APIFY_ACTOR = "apify~google-search-scraper"
QUERY_BATCH = 10

# Media / aggregator / directory-portal / review hosts — not lenders themselves.
JUNK_HOSTS = {
    "wikipedia.org", "investopedia.com", "nerdwallet.com", "forbes.com",
    "bankrate.com", "fundera.com", "lendio.com", "nav.com", "crunchbase.com",
    "linkedin.com", "facebook.com", "instagram.com", "youtube.com", "yelp.com",
    "reddit.com", "quora.com", "indeed.com", "glassdoor.com", "bbb.org",
    "elfaonline.org", "nefassociation.org", "monitordaily.com", "aacfb.org",
    "sba.gov", "irs.gov", "google.com", "amazon.com", "trustpilot.com",
    "clutch.co", "g2.com", "expertise.com", "yellowpages.com",
}


def apify_google_search(queries, country_code="us"):
    for attempt in range(4):
        try:
            resp = requests.post(
                f"{APIFY_BASE}/acts/{APIFY_ACTOR}/run-sync-get-dataset-items",
                params={"token": APIFY_TOKEN},
                json={"queries": "\n".join(queries), "resultsPerPage": 15,
                      "maxPagesPerQuery": 2, "languageCode": "en",
                      "countryCode": country_code, "includeUnfilteredResults": False},
                timeout=300)
        except requests.RequestException as e:
            if attempt == 3:
                print(f"  [!] network error after retries: {type(e).__name__}: {e}")
                return {}
            wait = 2 ** attempt * 3
            print(f"  [!] {type(e).__name__}, retrying in {wait}s ({attempt + 1}/4)")
            time.sleep(wait)
            continue
        if resp.status_code not in (200, 201):
            print(f"  [!] Apify HTTP {resp.status_code}: {resp.text[:200]}")
            return {}
        out = {}
        for item in resp.json():
            q = item.get("searchQuery", {}).get("term", "")
            if q:
                out[q] = item.get("organicResults", [])
        return out
    return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry_run", action="store_true")
    args = ap.parse_args()

    cfg = load_settings()
    queries = cfg["lender_queries"]
    print(f"Plan: {len(queries)} lender queries x {cfg['lender_results_per_query']} results "
          f"(1 result page each), Apify google-search")
    if args.dry_run:
        for q in queries:
            print(f"  {q}")
        print("\nDry run — nothing fired.")
        return

    conn = get_db()
    now = datetime.now().isoformat(timespec="seconds")
    new = seen = junk = 0
    for start in range(0, len(queries), QUERY_BATCH):
        batch = queries[start:start + QUERY_BATCH]
        results = apify_google_search(batch)
        for q, organic in results.items():
            for r in organic:
                url = r.get("url") or ""
                domain = norm_domain(url)
                if not domain or domain in JUNK_HOSTS:
                    junk += 1
                    continue
                title = (r.get("title") or "").strip()
                desc = (r.get("description") or "").strip()
                snippet = f"{title} — {desc}"[:400]
                cur = conn.execute(
                    "INSERT OR IGNORE INTO lenders (domain, name, website, source, snippet, scraped_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (domain, title or domain, url, f"google:{q}"[:120], snippet, now))
                if cur.rowcount:
                    new += 1
                else:
                    seen += 1
        conn.commit()
        print(f"  queries {start + 1}-{start + len(batch)}: +{new} new so far")

    total = conn.execute("SELECT COUNT(*) FROM lenders").fetchone()[0]
    print(f"\nDone. +{new} new lenders ({seen} dupes, {junk} junk-host results). Store total: {total}")
    print("Next: Claude judges fit in-session, then apply_lender_fit.py writes KEEP/SKIP.")
    log_run(conn, "pull_lenders", f"+{new} new (total {total})")


if __name__ == "__main__":
    main()
