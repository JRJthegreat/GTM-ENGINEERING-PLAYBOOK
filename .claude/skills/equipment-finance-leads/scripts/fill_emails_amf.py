"""
AMF person-endpoint email fill for the equipment-finance lender master —
robust variant of apollo-dm-waterfall/amf_person_fill.py for this reaping-prone
environment and the growing multi-state master:

  * writes PER BATCH (every WRITE_BATCH founds/misses), so a killed run keeps
    its progress instead of losing everything (the shared script writes
    all-at-end).
  * marks misses AB="amf_no_email", so re-runs SKIP them instead of re-hitting
    the same low-yield rows at the front of the sheet forever (the "low-yield
    wall" that starved the newly-added rows at the end).
  * --source lets you target just the new states (e.g. salesnav:IL,salesnav:IAAZWI).

VALID + domain-matched emails only (AMF can return a same-first-name person at a
different domain — rejected). 1 credit per FOUND; misses are free.

Cols: K company, L website(domain), T DM name, W email, AB status.

Usage:
  python3 -W ignore .claude/skills/equipment-finance-leads/scripts/fill_emails_amf.py \
      --sheet_url "URL" [--source salesnav:IL,salesnav:IAAZWI] [--limit N] [--apply] [--workers 10]
"""
import argparse
import json
import os
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", ".env"))
AMF_KEY = os.environ["ANYMAILFINDER_API_KEY"]
AMF_URL = "https://api.anymailfinder.com/v5.1/find-email/person"
TOKEN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "token.json")
TAB = "Leads"
COL_SOURCE, COL_COMPANY, COL_WEBSITE, COL_DM, COL_EMAIL, COL_STATUS = 0, 10, 11, 19, 22, 27
WRITE_BATCH = 15


def norm_domain(u):
    u = (u or "").strip().lower()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^www\.", "", u)
    return u.split("/")[0].split("?")[0]


def root(d):
    p = (d or "").split(".")
    return p[-2] if len(p) >= 2 else d


def amf_person(full_name, domain):
    try:
        r = requests.post(AMF_URL, headers={"Authorization": AMF_KEY, "Content-Type": "application/json"},
                          json={"full_name": full_name, "domain": domain}, timeout=120)
    except requests.RequestException as e:
        return None, f"error:{type(e).__name__}"
    if r.status_code != 200:
        return None, f"error:http_{r.status_code}"
    d = r.json() or {}
    if d.get("email_status") != "valid":
        return None, "no_record"
    email = (d.get("email") or d.get("valid_email") or "").strip()
    if not email or "@" not in email:
        return None, "no_email"
    if root(email.split("@")[1]) != root(domain):
        return None, f"domain_mismatch"
    return email, None


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet_url", required=True)
    ap.add_argument("--source", default="", help="comma-separated col-A sources to target")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    svc = get_service()
    sid = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", args.sheet_url).group(1)
    rows = svc.spreadsheets().values().get(spreadsheetId=sid, range=f"'{TAB}'!A2:AE10000").execute().get("values", [])

    def c(r, i):
        return (r[i].strip() if len(r) > i and r[i] else "")

    sources = {s.strip() for s in args.source.split(",") if s.strip()}
    todo = []
    for i, r in enumerate(rows):
        if c(r, COL_EMAIL) or not c(r, COL_DM):
            continue
        if c(r, COL_STATUS) in ("amf_no_email", "SKIP_BIG_BANK", "SKIP_NOT_LENDER"):
            continue
        dom = norm_domain(c(r, COL_WEBSITE))
        if not dom or "." not in dom:
            continue
        if sources and c(r, COL_SOURCE) not in sources:
            continue
        todo.append({"row": i + 2, "name": c(r, COL_DM), "domain": dom, "company": c(r, COL_COMPANY)})
    if args.limit:
        todo = todo[:args.limit]

    print(f"targets: {len(todo)} (DM+domain, no email, not already-missed"
          f"{', source '+args.source if sources else ''})")
    if not args.apply:
        print("dry run — add --apply."); return
    if not todo:
        return

    lock = threading.Lock()
    updates, found, miss = [], 0, 0

    def flush():
        with lock:
            if updates:
                svc.spreadsheets().values().batchUpdate(
                    spreadsheetId=sid, body={"valueInputOption": "RAW", "data": list(updates)}).execute()
                updates.clear()

    def col(i):
        return chr(65 + i) if i < 26 else chr(64 + i // 26) + chr(65 + i % 26)

    def work(t):
        nonlocal found, miss
        email, why = amf_person(t["name"], t["domain"])
        with lock:
            if email:
                found += 1
                updates.append({"range": f"'{TAB}'!{col(COL_EMAIL)}{t['row']}", "values": [[email]]})
                updates.append({"range": f"'{TAB}'!{col(COL_STATUS)}{t['row']}", "values": [["amf_email"]]})
            else:
                miss += 1
                updates.append({"range": f"'{TAB}'!{col(COL_STATUS)}{t['row']}", "values": [["amf_no_email"]]})
            need_flush = len(updates) >= WRITE_BATCH * 2
        if need_flush:
            flush()

    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(work, t) for t in todo]
        for _ in as_completed(futs):
            done += 1
            if done % 50 == 0:
                print(f"  {done}/{len(todo)}  found={found} miss={miss}", flush=True)
    flush()
    print(f"\nDone. {found} emails written, {miss} misses (marked, won't retry).")


if __name__ == "__main__":
    main()
