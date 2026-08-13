"""
Phase 2: SQLite store → Google Sheet campaign batch (29-col base layout).

Exports usable companies (excluded IS NULL, not yet exported) into a new or
existing sheet with company/website/city/state/status at K/L/R/S/AB — the
repo-standard positions — so `exa-website-enrichment` and
`apollo-dm-waterfall` run against it unmodified.

Column use:
  K  Company Name        L  Company Website (from Indeed employer block, else blank)
  M  Company Size        P  Company Description <- evidence detail (licence / ad / rating)
  Q  Benefits            <- evidence type (epa_licence / job_ad / nabers)
  R  City (suburb)       S  State
  AB (status)            <- left blank for the enrichment stack

Rows are marked exported_at in the store — deltas by default, like
nppes-new-clinics. --include_exported re-exports everything.

Usage:
  python3 -W ignore export_batch.py [--sheet_url URL] [--limit N] [--dry_run]
"""

import argparse
import datetime
import os
import sqlite3
import sys

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "emaas.db")
TOKEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "token.json")

HEADERS = [
    "Job_Id", "Job Title", "Job Type", "Occupations", "Date Published",
    "Salary Min", "Salary Max", "Salary Period", "Apply URL", "Job Description",
    "Company Name", "Company Website", "Company Size", "Revenue", "CEO Name",
    "Company Description", "Benefits", "City", "State",
    "DM Name", "DM Title", "LinkedIn URL", "Email",
    "First Name", "Last Name", "Email Body", "Added to Instantly",
    "Status", "PM Status",
]
SHEET_TITLE = "Energy GreenPrint - EMaaS Campaign - VIC"
TAB = "Leads"


def svc():
    creds = Credentials.from_authorized_user_file(TOKEN)
    return build("sheets", "v4", credentials=creds)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet_url", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--include_exported", action="store_true")
    ap.add_argument("--dry_run", action="store_true")
    args = ap.parse_args()

    db = sqlite3.connect(DB_PATH)
    where = "excluded IS NULL"
    if not args.include_exported:
        where += " AND exported_at IS NULL"
    q = (f"SELECT id, company, website, employee_count, categories, evidence, "
         f"evidence_detail, suburb, state FROM companies WHERE {where} "
         f"ORDER BY evidence, company")
    rows = db.execute(q).fetchall()
    if args.limit:
        rows = rows[:args.limit]
    print(f"{len(rows)} companies to export")
    if args.dry_run:
        for r in rows[:20]:
            print(f"  {r[1][:45]:45s} {r[5]:12s} {r[7] or ''}")
        return

    s = svc()
    if args.sheet_url:
        import re
        sheet_id = re.search(r"/d/([A-Za-z0-9_-]+)", args.sheet_url).group(1)
    else:
        doc = s.spreadsheets().create(body={
            "properties": {"title": SHEET_TITLE},
            "sheets": [{"properties": {"title": TAB}}],
        }).execute()
        sheet_id = doc["spreadsheetId"]
        s.spreadsheets().values().update(
            spreadsheetId=sheet_id, range=f"{TAB}!A1",
            valueInputOption="RAW", body={"values": [HEADERS]}).execute()
        print(f"Created: https://docs.google.com/spreadsheets/d/{sheet_id}")

    buf, exported_ids = [], []
    now = datetime.datetime.utcnow().isoformat()

    def flush():
        if not buf:
            return
        s.spreadsheets().values().append(
            spreadsheetId=sheet_id, range=f"{TAB}!A1",
            valueInputOption="RAW", insertDataOption="INSERT_ROWS",
            body={"values": buf}).execute()
        for cid in exported_ids:
            db.execute("UPDATE companies SET exported_at=? WHERE id=?", (now, cid))
        db.commit()
        buf.clear()
        exported_ids.clear()

    for (cid, company, website, size, cats, evidence, detail, suburb, state) in rows:
        row = [""] * len(HEADERS)
        row[10] = company                       # K
        row[11] = website or ""                 # L
        row[12] = size or ""                    # M
        row[15] = f"{cats or ''} | {detail or ''}"  # P evidence detail
        row[16] = evidence                      # Q evidence type
        row[17] = suburb or ""                  # R
        row[18] = state or "VIC"                # S
        buf.append(row)
        exported_ids.append(cid)
        if len(buf) >= 10:
            flush()
    flush()
    print(f"Exported. Sheet: https://docs.google.com/spreadsheets/d/{sheet_id}")


if __name__ == "__main__":
    sys.exit(main())
