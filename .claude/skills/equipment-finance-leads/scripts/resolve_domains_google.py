"""
Domain waterfall, FALLBACK lane — resolve websites for lenders that had no
company LinkedIn URL (or where the LinkedIn scrape returned no site) via Google
search + a GPT-4.1 official-domain pick.

Resilient replacement for find_company_domains.py in this lane: that script has
no retry and kept crashing when Apify's google-search actor read-timed-out at
300s. Here apify_google_search retries with backoff on smaller batches, so a
slow call recovers instead of killing the run.

Only touches rows with a company name and a BLANK website (L) — so it runs
strictly after enrich_websites_linkedin.py, filling only what LinkedIn couldn't.
Writes the registrable domain to L, status to AB. Blank beats wrong: if GPT
isn't confident, the cell stays empty (no email gets sent to a guessed company).

Sheet: K company, L website, R city, S state, AB status.
Batch-of-10 sheet writes; resume-safe (skips rows with a website).

Usage:
  python3 -W ignore .claude/skills/equipment-finance-leads/scripts/resolve_domains_google.py \
      --sheet_url "URL" [--apply] [--limit N]
"""
import argparse
import json
import os
import re
import sys
import time

import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from finance_common import norm_domain

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", ".env"))
APIFY_TOKEN = os.environ["APIFY_API_TOKEN"]
APIFY_BASE = "https://api.apify.com/v2"
APIFY_ACTOR = "apify~google-search-scraper"
AZ_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZ_KEY = os.getenv("AZURE_OPENAI_API_KEY")
AZ_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21")
AZ_MODEL = os.getenv("AZURE_OPENAI_DEPLOYMENT_FAST", "gpt-4.1")

TOKEN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "token.json")
TAB = "Leads"
COL_COMPANY, COL_WEBSITE, COL_CITY, COL_STATE, COL_STATUS = 10, 11, 17, 18, 27
QUERY_BATCH = 8
WRITE_BATCH = 10

LENDER_RE = re.compile(
    r"\b(bank|credit union|lending|lender|loans?|capital|finance|financial|funding|"
    r"mortgage|fcu|savings|bancorp|bancshares|trust|leasing)\b", re.I)

JUNK = {"linkedin.com", "facebook.com", "instagram.com", "twitter.com", "x.com",
        "youtube.com", "yelp.com", "bbb.org", "bloomberg.com", "crunchbase.com",
        "zoominfo.com", "dnb.com", "mapquest.com", "indeed.com", "glassdoor.com",
        "wikipedia.org", "nmlsconsumeraccess.org", "yellowpages.com", "manta.com",
        "buzzfile.com", "apollo.io", "rocketreach.co", "signalhire.com"}


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
    return re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url).group(1)


def col_letter(idx):
    return chr(65 + idx) if idx < 26 else chr(64 + idx // 26) + chr(65 + idx % 26)


def apify_google_search(queries):
    """{query: [organicResults]} with retry/backoff on smaller batches + short timeout."""
    for attempt in range(4):
        try:
            resp = requests.post(
                f"{APIFY_BASE}/acts/{APIFY_ACTOR}/run-sync-get-dataset-items",
                params={"token": APIFY_TOKEN},
                json={"queries": "\n".join(queries), "resultsPerPage": 6,
                      "maxPagesPerQuery": 1, "languageCode": "en",
                      "countryCode": "us", "includeUnfilteredResults": False},
                timeout=150)
        except requests.RequestException as e:
            if attempt == 3:
                print(f"  [!] network error after retries: {type(e).__name__}")
                return {}
            time.sleep(2 ** attempt * 4)
            continue
        if resp.status_code not in (200, 201):
            if attempt == 3:
                print(f"  [!] Apify HTTP {resp.status_code}")
                return {}
            time.sleep(2 ** attempt * 4)
            continue
        out = {}
        for item in resp.json():
            q = item.get("searchQuery", {}).get("term", "")
            if q:
                out[q] = item.get("organicResults", [])
        return out
    return {}


def gpt_pick(batch):
    """batch: [{company, candidates:[{domain,title,snippet}]}] -> {i: domain|''}."""
    lines = []
    for i, b in enumerate(batch):
        cand = "; ".join(f"{c['domain']} ({c['title'][:50]})" for c in b["candidates"][:6]) or "none"
        lines.append(f'{i}. company="{b["company"]}" candidates: {cand}')
    sys_prompt = (
        "You pick a company's OFFICIAL website domain from Google results. For each company "
        "return the registrable domain (e.g. cierabank.com) of its own corporate site, or empty "
        "string if none of the candidates is clearly the company's own site. Reject directories, "
        "aggregators, news, social. Only return a domain you are confident belongs to that exact "
        'company. Return JSON {"results":[{"i":<n>,"domain":"<domain or empty>"}]} one per input.')
    try:
        resp = requests.post(
            f"{AZ_ENDPOINT}/openai/deployments/{AZ_MODEL}/chat/completions",
            params={"api-version": AZ_VERSION},
            headers={"api-key": AZ_KEY, "Content-Type": "application/json"},
            json={"messages": [{"role": "system", "content": sys_prompt},
                               {"role": "user", "content": "\n".join(lines)}],
                  "max_completion_tokens": 1500, "response_format": {"type": "json_object"}},
            timeout=90)
        if resp.status_code != 200:
            print(f"  [!] Azure HTTP {resp.status_code}")
            return {}
        out = json.loads(resp.json()["choices"][0]["message"]["content"])
        picks = {}
        for r in out.get("results", []):
            d = norm_domain((r.get("domain") or "").strip())
            if d and d not in JUNK:
                picks[r["i"]] = d
        return picks
    except Exception as e:
        print(f"  [!] {type(e).__name__}: {e}")
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet_url", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--lenders_only", action="store_true",
                    help="only resolve rows whose company name looks like a lender "
                         "(skip the off-target title-matches)")
    args = ap.parse_args()

    svc = get_service()
    sid = sid_from_url(args.sheet_url)
    rows = svc.spreadsheets().values().get(spreadsheetId=sid, range=f"'{TAB}'!A2:AE10000").execute().get("values", [])

    def cell(r, i):
        return (r[i].strip() if len(r) > i and r[i] else "")

    targets = []
    for i, r in enumerate(rows):
        if cell(r, COL_WEBSITE) or not cell(r, COL_COMPANY):
            continue
        if args.lenders_only and not LENDER_RE.search(cell(r, COL_COMPANY)):
            continue
        targets.append({"row": i + 2, "company": cell(r, COL_COMPANY),
                        "city": cell(r, COL_CITY), "state": cell(r, COL_STATE)})
    if args.limit:
        targets = targets[:args.limit]

    print(f"{len(targets)} rows need a Google-fallback domain")
    if not args.apply:
        print("Dry run — add --apply."); return
    if not targets:
        return

    updates, resolved, blank = [], 0, 0

    def flush():
        if updates:
            svc.spreadsheets().values().batchUpdate(
                spreadsheetId=sid, body={"valueInputOption": "RAW", "data": updates}).execute()
            updates.clear()

    for start in range(0, len(targets), QUERY_BATCH):
        chunk = targets[start:start + QUERY_BATCH]
        queries = [f'{t["company"]} {t["state"]}'.strip() for t in chunk]
        results = apify_google_search(queries)
        batch = []
        for t, q in zip(chunk, queries):
            cands = []
            for r in results.get(q, []):
                d = norm_domain(r.get("url") or "")
                if d and d not in JUNK and not any(d.endswith("." + j) or d == j for j in JUNK):
                    cands.append({"domain": d, "title": (r.get("title") or "").strip(),
                                  "snippet": (r.get("description") or "").strip()})
            batch.append({"t": t, "company": t["company"], "candidates": cands})
        picks = gpt_pick(batch)
        for i, b in enumerate(batch):
            dom = picks.get(i, "")
            if dom:
                resolved += 1
                updates.append({"range": f"'{TAB}'!{col_letter(COL_WEBSITE)}{b['t']['row']}", "values": [[dom]]})
                updates.append({"range": f"'{TAB}'!{col_letter(COL_STATUS)}{b['t']['row']}", "values": [["google_website"]]})
            else:
                blank += 1
                updates.append({"range": f"'{TAB}'!{col_letter(COL_STATUS)}{b['t']['row']}", "values": [["google_no_match"]]})
        if len(updates) >= WRITE_BATCH * 2:
            flush()
        print(f"  {min(start+QUERY_BATCH, len(targets))}/{len(targets)}  resolved={resolved} blank={blank}")
    flush()
    print(f"\nDone. {resolved} domains resolved via Google, {blank} left blank.")


if __name__ == "__main__":
    main()
