"""AMF /decision-maker fallback for the production demand lane.

Runs over rows the waterfall could not finish (domain present, no email,
failure status or blank status) — the standing rule from apollo-dm-waterfall:
always run the /decision-maker fallback after the waterfall, because AMF
resolves roles at companies Apollo has no people for. 2 credits per found.

Category ladder mirrors the demand RANK_SYSTEM hypothesis:
  1. marketing  (the creative-budget owner)
  2. ceo        (small companies where the owner IS marketing)
A `ceo` call is only made when `marketing` found nothing. Valid emails only;
the email's domain must match the company's website domain or it is discarded
(free mailboxes rejected). Never writes DM data without a valid email.

Usage:
  python3 -W ignore amf_dm_fallback_demand.py --sheet_url "URL" \
      [--col_status AE] [--limit 0] [--apply]
"""
import argparse
import os
import re
import sys
import time

import requests
from dotenv import load_dotenv

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(SCRIPT_DIR, "..", "..", "..", ".env"))
sys.path.insert(0, SCRIPT_DIR)
from store import get_google_service

AMF_KEY = os.getenv("ANYMAILFINDER_API_KEY")
AMF_DM_URL = "https://api.anymailfinder.com/v5.1/find-email/decision-maker"
FREE_MAIL_RE = re.compile(r"@(gmail|yahoo|hotmail|outlook|aol|icloud|proton)\.", re.I)
FAIL_STATUSES = ("apollo_empty", "no_dm", "ranked", "not_found", "")

def col_to_idx(col):
    n = 0
    for ch in col.upper():
        n = n * 26 + ord(ch) - 64
    return n - 1

def root(domain):
    d = (domain or "").lower().strip()
    d = re.sub(r"^https?://", "", d).split("/")[0]
    return d[4:] if d.startswith("www.") else d

def amf_find(domain, category):
    try:
        r = requests.post(AMF_DM_URL,
                          headers={"Authorization": AMF_KEY,
                                   "Content-Type": "application/json"},
                          json={"domain": domain,
                                "decision_maker_category": [category]},
                          timeout=90)
        if r.status_code != 200:
            return None
        d = r.json()
        email = d.get("valid_email") or d.get("email")
        if d.get("email_status") != "valid" or not email:
            return None
        d["email"] = email
        return d
    except requests.RequestException:
        return None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet_url", required=True)
    ap.add_argument("--tab", default="Leads")
    ap.add_argument("--col_website", default="L")
    ap.add_argument("--col_dm_name", default="T")
    ap.add_argument("--col_dm_title", default="U")
    ap.add_argument("--col_email", default="W")
    ap.add_argument("--col_first", default="X")
    ap.add_argument("--col_last", default="Y")
    ap.add_argument("--col_status", default="AE")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    m = re.search(r"/d/([A-Za-z0-9_-]+)", args.sheet_url)
    sheet_id = m.group(1) if m else args.sheet_url
    svc = get_google_service()
    vals = svc.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=f"'{args.tab}'!A2:AG500").execute().get("values", [])

    W = col_to_idx(args.col_website)
    EM = col_to_idx(args.col_email)
    ST = col_to_idx(args.col_status)

    def cell(r, i):
        return (r[i] if len(r) > i else "").strip()

    todo = []
    for i, r in enumerate(vals):
        site, email, status = cell(r, W), cell(r, EM), cell(r, ST)
        if not site or email:
            continue
        if status and not any(status.startswith(s) for s in FAIL_STATUSES if s):
            if status != "":
                # unknown/success statuses are skipped; only failures retried
                if "found" in status or "suspect" in status or "skip" in status:
                    continue
        todo.append((i + 2, root(site)))
    if args.limit:
        todo = todo[:args.limit]
    print(f"{len(todo)} rows eligible for /decision-maker fallback")
    if not args.apply:
        for row, dom in todo[:20]:
            print(f"  row {row}: {dom}")
        return

    found = credits = 0
    updates = []
    for n, (row, dom) in enumerate(todo):
        result, used_cat = None, None
        for cat in ("marketing", "ceo"):
            result = amf_find(dom, cat)
            if result:
                used_cat = cat
                break
            time.sleep(0.4)
        if result:
            email = result["email"]
            if FREE_MAIL_RE.search(email) or root(email.split("@")[-1]) != dom:
                result = None
        if result:
            name = (result.get("person_full_name") or "").strip()
            parts = name.split()
            updates += [
                {"range": f"'{args.tab}'!{args.col_dm_name}{row}", "values": [[name]]},
                {"range": f"'{args.tab}'!{args.col_dm_title}{row}",
                 "values": [[(result.get("person_job_title") or "")[:80]]]},
                {"range": f"'{args.tab}'!{args.col_email}{row}", "values": [[result["email"]]]},
                {"range": f"'{args.tab}'!{args.col_first}{row}",
                 "values": [[parts[0] if parts else ""]]},
                {"range": f"'{args.tab}'!{args.col_last}{row}",
                 "values": [[parts[-1] if len(parts) > 1 else ""]]},
                {"range": f"'{args.tab}'!{args.col_status}{row}",
                 "values": [[f"found_dmfb_{used_cat}"]]},
            ]
            found += 1
            credits += 2
        else:
            updates.append({"range": f"'{args.tab}'!{args.col_status}{row}",
                            "values": [["dmfb_not_found"]]})
        if len(updates) >= 60 or n == len(todo) - 1:
            svc.spreadsheets().values().batchUpdate(
                spreadsheetId=sheet_id,
                body={"valueInputOption": "RAW", "data": updates}).execute()
            updates = []
            print(f"  {n+1}/{len(todo)} | found {found}", flush=True)
    print(f"done: {found} verified emails, ~{credits} credits")

if __name__ == "__main__":
    sys.exit(main())
