"""
Phase 1c: SEEK job-ad scrape → energy-intensity signals → SQLite store.

SEEK is Australia's dominant job board (3-4x Indeed AU volume), so this is
the deep pool for the same signal logic as scrape_indeed_signals.py: the ad
is the qualification evidence, the employer behind it is the lead.

Actor: websift~seek-job-scraper. Input quirks learned the expensive way:
  * default maxItems is 300 — ALWAYS pass maxResults (min 10, cap 550)
  * the correct fields are searchTerm / location / dateRange; a bare
    'keyword' input is silently ignored and returns a default feed
  * state-level filtering is unreliable — pass city-level `location`
Employer block: advertiser.name (company), companyProfile.size/.website/
.industry — richer than Indeed's employer block, so most rows arrive with
domain + size pre-filled.

Signal classification, agency/contractor/gov/alcohol handling: imported
from scrape_indeed_signals.py — one source of truth.

Usage:
  python3 -W ignore scrape_seek_signals.py [--limit 25] [--dry_run]
      [--cities "Melbourne,Geelong"] [--keywords "boiler operator,..."]
      [--state VIC] [--days 30] [--workers 4]
"""

import argparse
import importlib.util
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from dotenv import load_dotenv

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(SCRIPT_DIR, "..", "..", "..", ".env"))
APIFY_API_TOKEN = os.environ["APIFY_API_TOKEN"]

sys.path.insert(0, SCRIPT_DIR)
from ingest_epa import ensure_store, norm_name, ALCOHOL_RE, GOV_RE  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "indeed_sig", os.path.join(SCRIPT_DIR, "scrape_indeed_signals.py"))
indeed_sig = importlib.util.module_from_spec(spec)
spec.loader.exec_module(indeed_sig)
classify = indeed_sig.classify
AGENCY_RE = indeed_sig.AGENCY_RE
CONTRACTOR_RE = indeed_sig.CONTRACTOR_RE

ACTOR = "websift~seek-job-scraper"
SYNC_URL = f"https://api.apify.com/v2/acts/{ACTOR}/run-sync-get-dataset-items"

DEFAULT_KEYWORDS = [
    "boiler operator", "refrigeration technician", "process operator",
    "shift electrician", "plant operator", "cold store operator",
    "production operator night shift", "maintenance fitter shift",
    "food production", "meat process worker",
]


def run_combo(keyword, city, limit, days):
    try:
        r = requests.post(
            SYNC_URL, params={"token": APIFY_API_TOKEN},
            json={"searchTerm": keyword, "location": city,
                  "maxResults": max(10, limit), "dateRange": days,
                  "sortBy": "ListedDate"},
            timeout=300)
    except requests.RequestException as e:
        print(f"  [!] {keyword} @ {city}: {type(e).__name__}")
        return []
    if r.status_code not in (200, 201):
        print(f"  [!] {keyword} @ {city}: HTTP {r.status_code}")
        return []
    try:
        return r.json() or []
    except ValueError:
        return []


def ad_text(it):
    c = it.get("content") or {}
    parts = [it.get("title", ""), c.get("jobHook") or "",
             " ".join(c.get("bulletPoints") or []), str(c.get("unEditedContent") or "")]
    return "\n".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--cities", required=True, help="comma-separated city names")
    ap.add_argument("--keywords", default=None)
    ap.add_argument("--state", default="VIC")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--dry_run", action="store_true")
    args = ap.parse_args()

    cities = [c.strip() for c in args.cities.split(",")]
    keywords = ([k.strip() for k in args.keywords.split(",")]
                if args.keywords else DEFAULT_KEYWORDS)
    combos = [(k, c) for k in keywords for c in cities]
    print(f"{len(keywords)} keywords x {len(cities)} cities = {len(combos)} runs, "
          f"maxResults {max(10, args.limit)}/run")
    if args.dry_run:
        for k, c in combos[:15]:
            print(f"  {k} @ {c}")
        return

    db = ensure_store()
    stats = {"ads": 0, "signal": 0, "agency": 0, "new": 0}

    def ingest(items):
        for it in items:
            stats["ads"] += 1
            adv = it.get("advertiser") or {}
            prof = it.get("companyProfile") or {}
            name = (adv.get("name") or prof.get("name") or "").strip()
            if not name:
                continue
            if AGENCY_RE.search(name) or it.get("recruiterProfile"):
                stats["agency"] += 1
                continue
            sig, sig_text = classify(it.get("title", ""), ad_text(it))
            if not sig:
                continue
            stats["signal"] += 1
            loc = it.get("joblocationInfo") or {}
            excluded = None
            blurb = (prof.get("overview") or "")
            if GOV_RE.search(name):
                excluded = "government"
            elif ALCOHOL_RE.search(name) or ALCOHOL_RE.search(blurb):
                excluded = "alcohol"
            elif CONTRACTOR_RE.search(name) or CONTRACTOR_RE.search(blurb):
                excluded = "contractor"
            cur = db.execute("""
                INSERT INTO companies
                    (company, company_norm, state, suburb, website, employee_count,
                     categories, evidence, evidence_detail, excluded)
                VALUES (?,?,?,?,?,?,?, 'job_ad', ?, ?)
                ON CONFLICT(company_norm, state) DO UPDATE SET
                    website = COALESCE(companies.website, excluded.website),
                    employee_count = COALESCE(companies.employee_count,
                                              excluded.employee_count)
            """, (name, norm_name(name), args.state, loc.get("suburb"),
                  prof.get("website"), prof.get("size"), sig,
                  f"SEEK: {it.get('title', '')[:70]} | {sig_text}", excluded))
            if cur.rowcount and excluded is None:
                stats["new"] += 1

    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(run_combo, k, c, args.limit, args.days): (k, c)
                for k, c in combos}
        for f in as_completed(futs):
            ingest(f.result())
            done += 1
            if done % 10 == 0:
                print(f"  [{done}/{len(combos)}] ads={stats['ads']} "
                      f"signal={stats['signal']}", flush=True)
            db.commit()
    db.commit()
    print(f"\nSEEK ads {stats['ads']}, signal {stats['signal']}, "
          f"agencies skipped {stats['agency']}")
    n = db.execute("SELECT COUNT(*) FROM companies WHERE excluded IS NULL").fetchone()[0]
    w = db.execute("SELECT COUNT(*) FROM companies WHERE excluded IS NULL "
                   "AND website IS NOT NULL").fetchone()[0]
    print(f"Store now: {n} usable ({w} with website)")


if __name__ == "__main__":
    main()
