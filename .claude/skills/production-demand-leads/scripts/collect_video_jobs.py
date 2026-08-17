"""Signal collector: in-house video job postings (valig~indeed-jobs-scraper).

A direct brand hiring a video/content producer has video budget confirmed and
a 60-day capacity gap — the counter-position pitch. Writes companies +
`video_job` signals into the store. Same actor + input shape as
hr-leads-indeed/scrape_and_pull.py.

Usage:
  python3 -W ignore collect_video_jobs.py [--limit 20] [--dry_run]
    [--keywords "A,B"] [--metros "Los Angeles, CA|New York, NY"]
"""
import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from store import APIFY_API_TOKEN, add_signal, connect, upsert_company

SYNC = "https://api.apify.com/v2/acts/valig~indeed-jobs-scraper/run-sync-get-dataset-items"
DEFAULT_KEYWORDS = [
    "Video Producer", "Videographer", "Content Producer",
    "Video Content Creator", "Multimedia Producer", "Video Marketing Manager",
]
# Supply-side metros — where the ProductionHub houses are (Jude, 2026-08-17 default).
DEFAULT_METROS = [
    "Los Angeles, CA", "New York, NY", "Chicago, IL", "Atlanta, GA",
    "Austin, TX", "Miami, FL", "Nashville, TN",
]

def text_of(v):
    if isinstance(v, dict):
        return v.get("text") or v.get("html") or ""
    return v or ""

def run_combo(kw, loc, limit):
    try:
        r = requests.post(SYNC, params={"token": APIFY_API_TOKEN},
                          json={"title": kw, "location": loc,
                                "country": "us", "limit": limit},
                          timeout=240)
        if r.status_code not in (200, 201):
            print(f"  [!] {kw} @ {loc}: HTTP {r.status_code} {r.text[:100]}", flush=True)
            return kw, loc, []
        return kw, loc, (r.json() or [])
    except requests.RequestException as e:
        print(f"  [!] {kw} @ {loc}: {type(e).__name__}", flush=True)
        return kw, loc, []

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--keywords", default="")
    ap.add_argument("--metros", default="")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--dry_run", action="store_true")
    args = ap.parse_args()

    keywords = ([k.strip() for k in args.keywords.split(",") if k.strip()]
                or DEFAULT_KEYWORDS)
    metros = ([m.strip() for m in args.metros.split("|") if m.strip()]
              or DEFAULT_METROS)
    combos = [(k, m) for k in keywords for m in metros]
    print(f"{len(combos)} combos × limit {args.limit}")
    if args.dry_run:
        for k, m in combos:
            print(f"  {k} @ {m}")
        return

    con = connect()
    new_companies = new_signals = total = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = [pool.submit(run_combo, k, m, args.limit) for k, m in combos]
        for f in as_completed(futs):
            kw, loc, items = f.result()
            total += len(items)
            for it in items:
                jid = it.get("key") or ""
                emp = it.get("employer") or {}
                name = (emp.get("name") or "").strip()
                if not jid or not name:
                    continue
                loc_d = it.get("location") or {}
                city = loc_d.get("city", "") if isinstance(loc_d, dict) else ""
                state = loc_d.get("state", "") if isinstance(loc_d, dict) else ""
                before = con.total_changes
                cid = upsert_company(con, name, emp.get("employeesCount", ""),
                                     city, state)
                if cid is None:
                    continue
                if con.total_changes > before:
                    new_companies += 1
                if add_signal(con, cid, "video_job", jid, {
                        "job_title": it.get("title"),
                        "keyword": kw, "metro": loc,
                        "apply_url": it.get("applyUrl") or it.get("url") or "",
                        "desc": text_of(it.get("description"))[:500],
                }, (it.get("datePublished") or "")[:10]):
                    new_signals += 1
            con.commit()
            print(f"  {kw} @ {loc}: {len(items)} items", flush=True)
    con.commit()
    print(f"done: {total} items, {new_signals} new video_job signals, "
          f"{new_companies} new companies")

if __name__ == "__main__":
    sys.exit(main())
