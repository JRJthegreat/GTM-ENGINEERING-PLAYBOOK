"""
AI Ark food & beverage list -> clean EMaaS campaign sheet.

The AI Ark export is already contact-level with verified emails, so this skips
the whole scrape/resolve/enrich stack. It only filters and lays the rows out
for icebreaker generation + push.

Filter (Jude's ICP, no company dedupe):
  * email present AND BounceBan-verified
  * <= 1000 employees (drop 1001+ bands)
  * operations / production / trades / leadership department (drop biz-dev,
    consulting, agriculture, unknown)
  * literal duplicate email addresses dropped (one inbox once)

Output tab columns:
  A first  B last  C email  D company  E title  F state  G seniority
  H industry  I description  J company_type  K icebreaker  L subject
  M body  N pushed
J-M are filled by later steps; the campaign is loaded from this sheet.
"""

import argparse
import os
import re
import sys

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN = os.path.join(SCRIPT_DIR, "..", "..", "..", "token.json")

SRC_ID = "1ogAOB_8xzLjZFNKZuys3L2Zi46uVwBzyPgXw9QZhw18"
SRC_TAB = "Australia food and Drinks"
OUT_TAB = "Campaign"

OVER = {"1001-5000", "5001-10000", "10001+", "", "null+"}
BAD_DEPT = {"business_development", "consulting", "unknown",
            "agriculture_forestry_and_animal_care"}

# AI Ark source column indices (0-based)
S_FIRST, S_LAST, S_TITLE = 0, 1, 3
S_EMAIL, S_STATUS = 7, 8
S_STATE_PERSON = 13
S_SENIORITY, S_DEPT = 15, 16
S_COMPANY = 20
S_EMP = 22
S_INDUSTRY = 25
S_DESC = 28
S_CO_STATE = 40

STATE_NORM = {
    "vic": "Victoria", "victoria": "Victoria",
    "nsw": "New South Wales", "new south wales": "New South Wales",
    "qld": "Queensland", "queensland": "Queensland",
    "sa": "South Australia", "south australia": "South Australia",
    "wa": "Western Australia", "western australia": "Western Australia",
    "tas": "Tasmania", "tasmania": "Tasmania",
    "act": "Australian Capital Territory",
    "nt": "Northern Territory",
}
HEADER = ["first", "last", "email", "company", "title", "state", "seniority",
          "industry", "description", "company_type", "icebreaker", "subject",
          "body", "pushed"]


def norm_state(*vals):
    for v in vals:
        k = (v or "").strip().lower()
        if k in STATE_NORM:
            return STATE_NORM[k]
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet_url", required=True, help="destination sheet URL")
    ap.add_argument("--dry_run", action="store_true")
    args = ap.parse_args()

    dst = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", args.sheet_url).group(1)
    svc = build("sheets", "v4",
                credentials=Credentials.from_authorized_user_file(TOKEN))

    rows = svc.spreadsheets().values().get(
        spreadsheetId=SRC_ID, range=f"'{SRC_TAB}'!A2:AP1000").execute().get("values", [])

    def g(r, i):
        return r[i].strip() if len(r) > i and r[i] else ""

    out, seen_email = [], set()
    for r in rows:
        email = g(r, S_EMAIL).lower()
        if not email or "erified" not in g(r, S_STATUS):
            continue
        if g(r, S_EMP) in OVER:
            continue
        if g(r, S_DEPT) in BAD_DEPT:
            continue
        if email in seen_email:
            continue
        seen_email.add(email)
        out.append([
            g(r, S_FIRST), g(r, S_LAST), email, g(r, S_COMPANY), g(r, S_TITLE),
            norm_state(g(r, S_CO_STATE), g(r, S_STATE_PERSON)),
            g(r, S_SENIORITY), g(r, S_INDUSTRY), g(r, S_DESC),
            "", "", "", "", "",
        ])

    companies = len({r[3] for r in out})
    print(f"filtered rows: {len(out)}  |  unique companies: {companies}")
    if args.dry_run:
        for r in out[:8]:
            print(f"  {r[0]} {r[1]:12s} {r[3][:26]:26s} {r[4][:22]:22s} {r[5]}")
        return

    svc.spreadsheets().batchUpdate(spreadsheetId=dst, body={"requests": [
        {"addSheet": {"properties": {"title": OUT_TAB}}}]}).execute()
    svc.spreadsheets().values().update(
        spreadsheetId=dst, range=f"{OUT_TAB}!A1", valueInputOption="RAW",
        body={"values": [HEADER]}).execute()
    for i in range(0, len(out), 400):
        svc.spreadsheets().values().append(
            spreadsheetId=dst, range=f"{OUT_TAB}!A1",
            valueInputOption="RAW", insertDataOption="INSERT_ROWS",
            body={"values": out[i:i + 400]}).execute()
    print(f"wrote {len(out)} rows to {OUT_TAB} tab")


if __name__ == "__main__":
    sys.exit(main())
