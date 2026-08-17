"""
Phase 1b: Indeed AU job-ad scrape → energy-intensity signals → SQLite store.

Job ads are the qualification signal, NOT the lead source: a company
advertising "boiler operator, rotating roster" in Dandenong is publishing
its own load profile (2-shift/24-7 + process equipment = Sherif's two ICP
markers). Runs the same valig~indeed-jobs-scraper actor the Indeed pipelines
use, with country=au, over a keyword x VIC-city grid.

The employer block on each ad carries corporateWebsite and employeesCount,
so most rows arrive with domain + size pre-filled — enrichment only pays
for the remainder.

Classification per ad (title + description regex):
  shift      — afternoon/night shift, rotating roster, 24/7, continental
  equipment  — boiler, chiller, refrigeration, freezer, compressed air,
               HVAC, oven, furnace, kiln, ammonia
An ad with neither signal is discarded. Alcohol-primary and government
employers are stored flagged, never silently dropped.

Usage:
  python3 -W ignore scrape_indeed_signals.py [--limit 25] [--dry_run]
      [--cities "A,B"] [--keywords "A,B"] [--workers 6]
"""

import argparse
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "..", "..", "..", ".env"))
APIFY_API_TOKEN = os.environ["APIFY_API_TOKEN"]

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ingest_epa import ensure_store, norm_name, ALCOHOL_RE, GOV_RE  # noqa: E402

ACTOR = "valig~indeed-jobs-scraper"
SYNC_URL = f"https://api.apify.com/v2/acts/{ACTOR}/run-sync-get-dataset-items"

# AU-vernacular role keywords that only exist at energy-intensive sites
DEFAULT_KEYWORDS = [
    "boiler operator",
    "refrigeration technician",
    "maintenance fitter shift",
    "process operator",
    "production operator night shift",
    "shift electrician",
    "plant operator",
    "HVAC technician",
    "food production supervisor",
    "cold store operator",
]

# Melbourne industrial belt + regional VIC food corridor
DEFAULT_CITIES = [
    "Dandenong VIC", "Keysborough VIC", "Campbellfield VIC", "Thomastown VIC",
    "Laverton North VIC", "Truganina VIC", "Derrimut VIC", "Braeside VIC",
    "Clayton VIC", "Sunshine VIC", "Melbourne VIC",
    "Geelong VIC", "Ballarat VIC", "Bendigo VIC", "Shepparton VIC",
    "Wodonga VIC", "Warrnambool VIC", "Traralgon VIC",
]

SHIFT_RE = re.compile(
    r"afternoon shift|night shift|rotating roster|rotating shift|24/7|24-7"
    r"|2 shift|two shift|continental (?:shift|roster)|shift work|dayshift and"
    r"|day and afternoon|around the clock", re.I)
EQUIP_RE = re.compile(
    r"\bboiler(?!\s?maker)s?\b|boiler operator|chiller|refrigerat|freezer|blast free[zs]|cool ?room|cold ?store"
    r"|compressed air|compressor|\bhvac\b|air handling|ammonia|glycol"
    r"|\boven[s]?\b|furnace|kiln|autoclave|retort|steam plant", re.I)
AGENCY_RE = re.compile(
    r"recruit|labour hire|workforce|personnel|staffing|programmed|randstad"
    r"|adecco|hays\b|chandler|manpower|drake\b|toll people|blaze staffing"
    r"|atlam|astrum|zoom recruit", re.I)
# Firms that SERVICE energy equipment rather than OWN it — the agency-trap of
# this vertical. An HVAC contractor advertises "refrigeration technician"
# constantly but is not an energy-intensive site. Flagged, not dropped, so a
# judge pass can rescue edge cases.
CONTRACTOR_RE = re.compile(
    r"air ?conditioning|airconditioning|hvac (?:services|solutions|group)"
    r"|mechanical (?:services|solutions)|refrigeration (?:services|solutions"
    r"|group|pty)|engineering (?:group|services|solutions)|facilities? manage"
    r"|building services|electrical (?:services|contractors)|plumbing"
    r"|\bcbre\b|\bjll\b|cushman|knight frank|colliers|ventia|downer\b"
    r"|spotless|city fm|bgis\b", re.I)


def classify(title, desc):
    text = f"{title}\n{desc or ''}"
    shift = bool(SHIFT_RE.search(text))
    equip = bool(EQUIP_RE.search(text))
    if shift and equip:
        return "both", (SHIFT_RE.search(text).group(0) + " + " + EQUIP_RE.search(text).group(0))
    if shift:
        return "shift", SHIFT_RE.search(text).group(0)
    if equip:
        return "equipment", EQUIP_RE.search(text).group(0)
    return None, None


def run_combo(keyword, city, limit):
    try:
        r = requests.post(
            SYNC_URL, params={"token": APIFY_API_TOKEN},
            json={"title": keyword, "location": city, "country": "au",
                  "limit": limit},
            timeout=240)
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=25, help="items per combo")
    ap.add_argument("--cities", default=None)
    ap.add_argument("--keywords", default=None)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--state", default="VIC", help="state stamp for new rows")
    ap.add_argument("--dry_run", action="store_true")
    args = ap.parse_args()

    cities = [c.strip() for c in args.cities.split(",")] if args.cities else DEFAULT_CITIES
    keywords = [k.strip() for k in args.keywords.split(",")] if args.keywords else DEFAULT_KEYWORDS
    combos = [(k, c) for k in keywords for c in cities]
    print(f"{len(keywords)} keywords x {len(cities)} cities = {len(combos)} runs, "
          f"limit {args.limit}/run (max {len(combos) * args.limit} items)")
    if args.dry_run:
        for k, c in combos[:20]:
            print(f"  {k} @ {c}")
        return

    db = ensure_store()
    stats = {"ads": 0, "signal": 0, "agency": 0, "new_companies": 0}

    def ingest(items):
        for it in items:
            stats["ads"] += 1
            emp = it.get("employer") or {}
            name = emp.get("name") or it.get("company_name") or ""
            if not name:
                continue
            if AGENCY_RE.search(name):
                stats["agency"] += 1
                continue
            sig, sig_text = classify(it.get("title", ""), it.get("description", ""))
            if not sig:
                continue
            stats["signal"] += 1
            nn = norm_name(name)
            loc = it.get("location") or {}
            excluded = None
            if GOV_RE.search(name):
                excluded = "government"
            elif ALCOHOL_RE.search(name) or ALCOHOL_RE.search(emp.get("briefDescription") or ""):
                excluded = "alcohol"
            elif CONTRACTOR_RE.search(name) or CONTRACTOR_RE.search(emp.get("briefDescription") or ""):
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
            """, (name, nn, args.state, loc.get("city"),
                  emp.get("corporateWebsite"), emp.get("employeesCount"),
                  sig, f"{it.get('title', '')[:80]} | {sig_text}", excluded))
            if cur.rowcount and db.execute(
                    "SELECT changes()").fetchone()[0]:
                stats["new_companies"] += 1
            db.execute("""
                INSERT OR IGNORE INTO job_signals
                    (job_key, company_norm, title, city, signal_type,
                     signal_text, date_published)
                VALUES (?,?,?,?,?,?,?)
            """, (it.get("key"), nn, it.get("title"), loc.get("city"),
                  sig, sig_text, it.get("datePublished")))

    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(run_combo, k, c, args.limit): (k, c) for k, c in combos}
        for fut in as_completed(futs):
            k, c = futs[fut]
            items = fut.result()
            ingest(items)
            done += 1
            if done % 10 == 0:
                db.commit()
                print(f"  [{done}/{len(combos)}] ads={stats['ads']} "
                      f"signal={stats['signal']}")
    db.commit()

    total = db.execute(
        "SELECT COUNT(*) FROM companies WHERE excluded IS NULL").fetchone()[0]
    with_site = db.execute(
        "SELECT COUNT(*) FROM companies WHERE excluded IS NULL "
        "AND website IS NOT NULL").fetchone()[0]
    print(f"\nAds seen {stats['ads']}, signal-bearing {stats['signal']}, "
          f"agencies skipped {stats['agency']}")
    print(f"Store now: {total} usable companies ({with_site} with website)")


if __name__ == "__main__":
    sys.exit(main())
