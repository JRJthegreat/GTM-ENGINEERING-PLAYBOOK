"""
Domain waterfall, primary lane — resolve each lender's website by scraping its
LinkedIn COMPANY page (authoritative: the page lists the company's own website),
using the company LinkedIn URL the Sales Nav ingest captured in column AE.

This is more accurate than a Google name-search (no wrong-company risk), so it
runs FIRST. Rows with no company LinkedIn URL, or where the scrape returns no
website, are left with a blank website (L) and stamped so the traditional
Google-search resolver (find_company_domains.py) picks them up as the fallback.

Apify actor: pratikdani~linkedin-company-profile-scraper (returns website +
company_size). Numeric-ID company URLs (…/company/3736798) work.

Sheet columns: K company, L website(domain), M size, AB status, AE company LinkedIn.
Writes the registrable domain to L (AMF/exa want a domain, not a full URL),
company size to M, and status to AB (li_website / li_no_website).

Batch-of-10 writes; resume-safe (skips rows that already have a website in L).

Usage:
  python3 -W ignore .claude/skills/equipment-finance-leads/scripts/enrich_websites_linkedin.py \
      --sheet_url "URL" [--apply] [--limit N] [--workers 5]
"""
import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from finance_common import norm_domain

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", ".env"))
APIFY_TOKEN = os.environ["APIFY_API_TOKEN"]
ACTOR = "pratikdani~linkedin-company-profile-scraper"
SYNC_URL = f"https://api.apify.com/v2/acts/{ACTOR}/run-sync-get-dataset-items"
TOKEN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "token.json")
TAB = "Leads"

COL_COMPANY = 10   # K
COL_WEBSITE = 11   # L
COL_SIZE = 12      # M
COL_STATUS = 27    # AB
COL_CO_LI = 30     # AE
WRITE_BATCH = 10


def get_service():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    with open(TOKEN_PATH) as f:
        td = json.load(f)
    creds = Credentials(token=td["token"], refresh_token=td["refresh_token"],
                        token_uri=td["token_uri"], client_id=td["client_id"],
                        client_secret=td["client_secret"],
                        scopes=td.get("scopes", ["https://www.googleapis.com/auth/spreadsheets"]))
    if creds.expired:
        creds.refresh(Request())
        td["token"] = creds.token
        with open(TOKEN_PATH, "w") as f:
            json.dump(td, f)
    return build("sheets", "v4", credentials=creds)


def sid_from_url(url):
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url)
    if not m:
        raise SystemExit(f"bad sheet url {url}")
    return m.group(1)


def col_letter(idx):
    return chr(65 + idx) if idx < 26 else chr(64 + idx // 26) + chr(65 + idx % 26)


def scrape_company(url):
    try:
        resp = requests.post(SYNC_URL, params={"token": APIFY_TOKEN, "limit": 1},
                             json={"url": url}, timeout=120)
        if resp.status_code not in (200, 201):
            return None
        data = resp.json()
        if not data or data[0].get("error"):
            return None
        return data[0]
    except requests.RequestException:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet_url", required=True)
    ap.add_argument("--apply", action="store_true", help="write to sheet (default: dry run)")
    ap.add_argument("--limit", type=int, default=0, help="cap pending rows")
    ap.add_argument("--workers", type=int, default=5)
    args = ap.parse_args()

    svc = get_service()
    sid = sid_from_url(args.sheet_url)
    rows = svc.spreadsheets().values().get(spreadsheetId=sid, range=f"'{TAB}'!A2:AE10000").execute().get("values", [])

    def cell(row, i):
        return (row[i].strip() if len(row) > i and row[i] else "")

    targets = []
    for i, row in enumerate(rows):
        website = cell(row, COL_WEBSITE)
        co_li = cell(row, COL_CO_LI)
        status = cell(row, COL_STATUS)
        if website or not co_li or status == "li_no_website":
            continue  # resolved, no LinkedIn URL (Google fallback), or already a LinkedIn miss
        targets.append({"row": i + 2, "company": cell(row, COL_COMPANY), "url": co_li})
    if args.limit:
        targets = targets[:args.limit]

    print(f"{len(targets)} rows with a company LinkedIn URL and no website yet")
    if not args.apply:
        print("Dry run — no scraping, no writes. Add --apply to run.")
        return
    if not targets:
        return

    updates = []
    hits = misses = 0

    def work(t):
        item = scrape_company(t["url"])
        website = norm_domain((item or {}).get("website") or "")
        size = ((item or {}).get("company_size") or "").strip()
        return t, website, size

    def flush():
        if updates:
            svc.spreadsheets().values().batchUpdate(
                spreadsheetId=sid, body={"valueInputOption": "RAW", "data": updates}).execute()
            updates.clear()

    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(work, t) for t in targets]
        for fut in as_completed(futs):
            t, website, size = fut.result()
            done += 1
            if website:
                hits += 1
                updates.append({"range": f"'{TAB}'!{col_letter(COL_WEBSITE)}{t['row']}", "values": [[website]]})
                if size:
                    updates.append({"range": f"'{TAB}'!{col_letter(COL_SIZE)}{t['row']}", "values": [[size]]})
                updates.append({"range": f"'{TAB}'!{col_letter(COL_STATUS)}{t['row']}", "values": [["li_website"]]})
                print(f"  [{done}/{len(targets)}] {t['company'][:32]:32} -> {website}")
            else:
                misses += 1
                updates.append({"range": f"'{TAB}'!{col_letter(COL_STATUS)}{t['row']}", "values": [["li_no_website"]]})
            if len(updates) >= WRITE_BATCH * 3:
                flush()
    flush()
    print(f"\nDone. {hits} websites resolved via LinkedIn, {misses} misses (→ Google fallback).")


if __name__ == "__main__":
    main()
