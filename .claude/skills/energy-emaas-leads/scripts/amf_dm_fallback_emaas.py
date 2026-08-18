"""AMF /decision-maker fallback (EMaaS clone) for rows the waterfall ranked a
DM but found no verified email (status `not_found`).

Two differences from apollo-dm-waterfall/scripts/amf_dm_fallback.py, which is
left untouched because it serves the live healthcare pipeline:
  * status column is AC, not AB — on the EMaaS sheet AB holds website-
    resolution provenance and AC holds DM status.
  * --category is a flag defaulting to `operations`, because Sherif's ladder
    is operations-first (Ops Manager -> Plant/Production/Engineering -> owner).
    Run `operations` first, then `ceo` over what is still empty.
An email returned on a domain other than the company's own is rejected, same
guard as the waterfall.
2 credits per found valid email; misses free. Valid-only, batch-of-10,
idempotent (skips rows with an email or a changed status)."""
import os
import re
import requests
from dotenv import load_dotenv
import argparse
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(SCRIPT_DIR, "..", "..", "..", ".env"))
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from urllib.parse import urlparse

ap = argparse.ArgumentParser()
ap.add_argument("--sheet_url", required=True)
ap.add_argument("--limit", type=int, default=0, help="Max rows to attempt (0 = all)")
ap.add_argument("--category", default="operations",
                help="AMF decision_maker_category (operations|ceo|engineering|...)")
ap.add_argument("--statuses", default="not_found",
                help="comma-separated AC statuses to retry. The waterfall leaves "
                     "several distinct misses — no_apollo_people (nobody indexed, "
                     "exactly what /decision-maker is for), no_dm_candidates "
                     "(people found but none on the ladder), fallback_not_found "
                     "(an earlier category pass missed).")
args = ap.parse_args()
SID = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", args.sheet_url).group(1)
RETRY_STATUSES = {x.strip() for x in args.statuses.split(",") if x.strip()}
AMF_KEY = os.getenv("ANYMAILFINDER_API_KEY")
URL = "https://api.anymailfinder.com/v5.1/find-email/decision-maker"

def norm_domain(w):
    w = (w or "").strip().lower()
    if not w:
        return ""
    if not w.startswith("http"):
        w = "https://" + w
    h = urlparse(w).netloc or ""
    return h[4:] if h.startswith("www.") else h

def band(size):
    m = re.search(r"([\d,]+)", size or "")
    if not m: return "TINY"
    lb = int(m.group(1).replace(",", ""))
    return "LARGE" if lb >= 500 else ("MID" if lb >= 50 else "TINY")

creds = Credentials.from_authorized_user_file(
    os.path.join(SCRIPT_DIR, "..", "..", "..", "token.json"))
svc = build("sheets", "v4", credentials=creds)
vals = svc.spreadsheets().values().get(
    spreadsheetId=SID, range="Leads!A1:AC5000").execute().get("values", [])

def cell(r, i):
    return r[i].strip() if len(r) > i and r[i] else ""

todo = []
for i, r in enumerate(vals[1:], start=2):
    if cell(r, 28) not in RETRY_STATUSES:
        continue
    if cell(r, 22):  # email already present — never overwrite
        continue
    dom = norm_domain(cell(r, 11))
    if dom:
        todo.append((i, dom, cell(r, 10), band(cell(r, 12))))
if args.limit:
    todo = todo[:args.limit]
print(f"fallback candidates: {len(todo)}")

found = credits = 0
pending = []
def flush():
    global pending
    if pending:
        svc.spreadsheets().values().batchUpdate(
            spreadsheetId=SID,
            body={"valueInputOption": "RAW", "data": pending}).execute()
        pending = []

for n, (row, dom, company, b) in enumerate(todo, 1):
    category = args.category
    try:
        resp = requests.post(
            URL, headers={"Authorization": AMF_KEY,
                          "Content-Type": "application/json"},
            json={"domain": dom, "decision_maker_category": category},
            timeout=180)
        d = resp.json()
    except Exception as e:
        print(f"  [!] {company}: {type(e).__name__}")
        continue
    credits += d.get("credits_charged", 0) or 0
    if d.get("email_status") == "valid" and d.get("valid_email"):
        email = d["valid_email"]
        full = (d.get("person_full_name") or "").strip()
        title = (d.get("person_job_title") or category.upper()).strip()
        parts = full.split()
        first = parts[0] if parts else ""
        last = parts[-1] if len(parts) > 1 else ""
        pending.append({"range": f"Leads!T{row}:Y{row}", "values": [[
            full or title, title, "", email, first, last]]})
        pending.append({"range": f"Leads!AC{row}",
                        "values": [[f"found_amf_dm_fallback_{category}"]]})
        found += 1
        print(f"  ✓ [{b}/{category}] {company}: {full or title} <{email}>")
    else:
        pending.append({"range": f"Leads!AC{row}",
                        "values": [["fallback_not_found"]]})
    if n % 10 == 0:
        flush()
        print(f"  -- {n}/{len(todo)} | found {found} | credits {credits}")
flush()
print(f"Done. Found {found}/{len(todo)}, AMF credits: {credits}")
