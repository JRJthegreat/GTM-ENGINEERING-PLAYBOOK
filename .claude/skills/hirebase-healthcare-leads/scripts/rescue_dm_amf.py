"""
Phase 5c — AMF /decision-maker rescue over the worklist rows the Apollo
waterfall could not fill. CEO CATEGORY ONLY.

WHY A NEW SCRIPT rather than the shared companions. Neither existing rescue
covers this lane's biggest miss bucket:
  - amf_dm_fallback.py targets ONLY status `not_found`
  - amf_ceo_rescue.py targets ONLY `no_apollo_people` AND only TINY-banded rows
  - `no_dm_candidates` — 115 companies here, the largest bucket — is untouched
    by both.
amf_ceo_then_ops.py does reach them, but falls back to category `operations`
when CEO misses. Jude's ladder says otherwise: COO/Ops replied 0 times from 87
sends across four campaigns, and a company with nobody on the ladder is left
un-enriched, because a wasted credit plus a burned company beats an empty row.
That shared script feeds other live lanes and must not be edited, so this is a
CEO-only clone rather than a retrofit.

Apollo only knows who it has indexed. AMF /decision-maker resolves a role at a
DOMAIN, so it reaches companies Apollo has nobody for at all.

2 AMF credits per FOUND valid email; misses are free. Valid-only — `risky` is
rejected everywhere. Never writes a name without an email. Batch-of-10,
idempotent (a row with an email, or already stamped by this pass, is skipped).

Run:
  python3 -W ignore rescue_dm_amf.py --sheet_url URL --tab "DM Worklist" [--apply]
"""

import os
import re
import time
import argparse
from collections import Counter
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(SCRIPT_DIR, "..", "..", "..", ".env"))
TOKEN_PATH = os.path.join(SCRIPT_DIR, "..", "..", "..", "token.json")
AMF_KEY = os.getenv("ANYMAILFINDER_API_KEY")
AMF_URL = "https://api.anymailfinder.com/v5.1/find-email/decision-maker"

COL_COMPANY, COL_WEBSITE = 10, 11
COL_DM, COL_TITLE, COL_LI, COL_EMAIL, COL_FIRST, COL_LAST = 19, 20, 21, 22, 23, 24
COL_STATUS = 27
BATCH = 10

# Statuses the waterfall leaves behind when it cannot fill a row. All of them
# are eligible: each means "no verified email", by a different route.
RETRY = {"not_found", "no_dm_candidates", "no_apollo_people",
         "ranked_no_email", ""}


def sheet_id_of(url):
    return re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url).group(1)


def a1(col, row):
    s, c = "", col + 1
    while c:
        c, r = divmod(c - 1, 26)
        s = chr(65 + r) + s
    return f"{s}{row}"


def norm_domain(web):
    w = (web or "").strip()
    if not w:
        return ""
    if not w.startswith(("http://", "https://")):
        w = "https://" + w
    h = urlparse(w).netloc.lower()
    return h[4:] if h.startswith("www.") else h


def amf_ceo(domain, company, timeout=180):
    """Returns (email, full_name, title, credits). Valid only.

    Credits come from AMF's own `credits_charged` rather than being assumed:
    a miss is free, and only the API knows what a call actually cost."""
    try:
        r = requests.post(
            AMF_URL,
            headers={"Authorization": AMF_KEY, "Content-Type": "application/json"},
            json={"domain": domain, "decision_maker_category": "ceo"},
            timeout=timeout)
    except requests.RequestException:
        return None, None, None, 0
    if r.status_code != 200:
        return None, None, None, 0
    try:
        d = r.json()
    except ValueError:
        return None, None, None, 0
    # valid-only: `risky` is rejected everywhere in this repo
    cr = d.get("credits_charged", 0) or 0
    if d.get("email_status") != "valid" or not d.get("valid_email"):
        return None, None, None, cr
    email = (d.get("valid_email") or "").strip()
    name = (d.get("person_full_name") or "").strip()
    title = (d.get("person_job_title") or "CEO").strip()
    return email, name, title, cr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet_url", required=True)
    ap.add_argument("--tab", default="DM Worklist")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    if not AMF_KEY:
        raise SystemExit("ANYMAILFINDER_API_KEY not set")

    svc = build("sheets", "v4",
                credentials=Credentials.from_authorized_user_file(TOKEN_PATH))
    sid = sheet_id_of(args.sheet_url)
    vals = svc.spreadsheets().values().get(
        spreadsheetId=sid, range=f"'{args.tab}'!A1:AD").execute().get("values", [])
    rows = vals[1:]

    def c(r, i):
        return r[i].strip() if i < len(r) else ""

    todo = []
    for i, r in enumerate(rows):
        if c(r, COL_EMAIL):
            continue
        if c(r, COL_STATUS) not in RETRY:
            continue
        dom = norm_domain(c(r, COL_WEBSITE))
        if not dom:
            continue
        todo.append((i + 2, dom, c(r, COL_COMPANY)))
    if args.limit:
        todo = todo[: args.limit]

    print(f"[{args.tab}] {len(todo)} companies eligible for CEO rescue")
    print(f"  (2 credits per FOUND email; misses free)")
    if not args.apply:
        for sr, dom, co in todo[:10]:
            print(f"    row {sr}: {co[:38]:38s} {dom}")
        print("\n  (no --apply — nothing spent)")
        return

    pending, found, spent = [], 0, 0
    for n, (sr, dom, co) in enumerate(todo, 1):
        email, name, title, cr = amf_ceo(dom, co)
        spent += cr
        if email:
            found += 1
            parts = (name or "").split()
            first = parts[0] if parts else ""
            last = " ".join(parts[1:]) if len(parts) > 1 else ""
            pending.append({"range": f"'{args.tab}'!{a1(COL_DM, sr)}",
                            "values": [[name, title, "", email, first, last]]})
            pending.append({"range": f"'{args.tab}'!{a1(COL_STATUS, sr)}",
                            "values": [["found_amf_ceo_rescue"]]})
            print(f"    ✓ {co[:34]:34s} {name} <{email}>  [{title[:26]}]")
        else:
            pending.append({"range": f"'{args.tab}'!{a1(COL_STATUS, sr)}",
                            "values": [["rescue_amf_no_ceo"]]})
        if n % BATCH == 0:
            svc.spreadsheets().values().batchUpdate(
                spreadsheetId=sid,
                body={"valueInputOption": "RAW", "data": pending}).execute()
            pending = []
            print(f"  -- {n}/{len(todo)} | found {found} | credits {spent}")
    if pending:
        svc.spreadsheets().values().batchUpdate(
            spreadsheetId=sid,
            body={"valueInputOption": "RAW", "data": pending}).execute()
    print(f"\n=== CEO rescue: {found}/{len(todo)} found, ~{spent} AMF credits ===")


if __name__ == "__main__":
    main()
