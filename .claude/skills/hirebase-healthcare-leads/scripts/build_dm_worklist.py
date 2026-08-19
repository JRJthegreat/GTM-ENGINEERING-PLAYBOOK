"""
Phase 5a — build a one-row-per-COMPANY worklist for the Apollo DM waterfall.

WHY THIS EXISTS. `apollo-dm-waterfall` queues per SHEET ROW and has no
company/domain dedupe. This lane keeps one row per JOB, so pointing it at a
lane tab would enrich Compassus 380 times and Ivy Rehab 104 times — roughly
1,974 lookups for 432 companies. The worklist collapses that to one row each,
the waterfall runs against it unmodified, and sync_dm_results.py maps the
answers back to every row of that company.

Only `company_status == KEEP` rows are included: the REVIEW_* piles carry
another company's identity or an unproven domain, and enriching them emails
the wrong org.

Written in the 29-col layout the waterfall expects by default (company K,
website L, size M, city R, state S, CEO O, job B, DM T-Y, status AB).

SIZE IS REWRITTEN, DELIBERATELY. HireBase pre-filtered its export to <=500, so
Compassus arrives tagged "201 to 500" with 380 live postings. The waterfall
uses the size band to decide whether to search Apollo WITH a title filter —
without it, a 10,000-person system returns random staff and the ranking step
has nothing useful to choose from. Companies this lane adjudicated as
LARGE_ORG (col AX) are therefore written as "1000" so they band LARGE.

Run:
  python3 -W ignore build_dm_worklist.py --sheet_url URL \
    --tabs "SLP Campaign" "General Campaign" [--out_tab "DM Worklist"] [--apply]
"""

import os
import re
import time
import argparse
from collections import Counter

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_PATH = os.path.join(SCRIPT_DIR, "..", "..", "..", "token.json")

COL_TITLE, COL_COMPANY, COL_WEBSITE = 1, 10, 11
COL_CITY, COL_STATE = 17, 18
COL_STATUS, COL_BAND = 47, 49
NCOLS = 30

HEADERS = ["Job_Id", "Job Title", "Job Type", "Occupations", "Date Published",
           "Salary Min", "Salary Max", "Salary Period", "Apply URL",
           "Job Description", "Company Name", "Company Website",
           "Company Size", "Revenue", "CEO Name", "Company Description",
           "Benefits", "City", "State", "DM Name", "DM Title", "LinkedIn URL",
           "Email", "First Name", "Last Name", "Email Body",
           "Added to Instantly", "dm_status", "Job URL", "size_band"]


def sheet_id_of(url):
    return re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url).group(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet_url", required=True)
    ap.add_argument("--tabs", nargs="+", required=True)
    ap.add_argument("--out_tab", default="DM Worklist")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    svc = build("sheets", "v4",
                credentials=Credentials.from_authorized_user_file(TOKEN_PATH))
    sid = sheet_id_of(args.sheet_url)

    comps = {}
    for tab in args.tabs:
        vals = svc.spreadsheets().values().get(
            spreadsheetId=sid, range=f"'{tab}'!A1:BA").execute().get("values", [])
        for r in vals[1:]:
            def c(i, _r=r):
                return _r[i].strip() if i < len(_r) else ""
            if c(COL_STATUS) != "KEEP":
                continue
            name, web = c(COL_COMPANY), c(COL_WEBSITE)
            if not name or not web:
                continue
            d = comps.setdefault(name, {"web": web, "band": c(COL_BAND),
                                        "titles": [], "cities": [],
                                        "states": [], "n": 0})
            d["n"] += 1
            d["titles"].append(c(COL_TITLE))
            if c(COL_CITY):
                d["cities"].append(c(COL_CITY))
            if c(COL_STATE):
                d["states"].append(c(COL_STATE))

    rows = []
    for name, d in sorted(comps.items(), key=lambda x: -x[1]["n"]):
        city = Counter(d["cities"]).most_common(1)[0][0] if d["cities"] else ""
        state = Counter(d["states"]).most_common(1)[0][0] if d["states"] else ""
        title = Counter(d["titles"]).most_common(1)[0][0] if d["titles"] else ""
        size = "1000" if d["band"] == "LARGE_ORG" else "200"
        r = [""] * NCOLS
        r[1], r[10], r[11], r[12] = title, name, d["web"], size
        r[17], r[18] = city, state
        r[29] = d["band"]
        rows.append(r)

    print(f"{len(rows)} companies (from {sum(d['n'] for d in comps.values())} rows)")
    print(f"  LARGE_ORG banded 1000: "
          f"{sum(1 for r in rows if r[12] == '1000')}")
    print(f"  saved lookups vs per-row: "
          f"{sum(d['n'] for d in comps.values()) - len(rows)}")
    if not args.apply:
        print("  (no --apply — nothing written)")
        for r in rows[:5]:
            print(f"    {r[10][:34]:34s} {r[11][:34]:34s} {r[12]:6s} {r[17]}")
        return

    meta = svc.spreadsheets().get(spreadsheetId=sid).execute()
    existing = {s["properties"]["title"]: s["properties"]["sheetId"]
                for s in meta["sheets"]}
    if args.out_tab in existing:
        svc.spreadsheets().values().clear(
            spreadsheetId=sid, range=f"'{args.out_tab}'").execute()
        tab_id = existing[args.out_tab]
    else:
        resp = svc.spreadsheets().batchUpdate(
            spreadsheetId=sid, body={"requests": [{"addSheet": {"properties": {
                "title": args.out_tab,
                "gridProperties": {"rowCount": len(rows) + 100,
                                   "columnCount": NCOLS}}}}]}).execute()
        tab_id = resp["replies"][0]["addSheet"]["properties"]["sheetId"]

    svc.spreadsheets().batchUpdate(spreadsheetId=sid, body={"requests": [
        {"updateDimensionProperties": {
            "range": {"sheetId": tab_id, "dimension": "ROWS",
                      "startIndex": 0, "endIndex": len(rows) + 100},
            "properties": {"pixelSize": 18}, "fields": "pixelSize"}}]}).execute()
    svc.spreadsheets().values().update(
        spreadsheetId=sid, range=f"'{args.out_tab}'!A1",
        valueInputOption="RAW", body={"values": [HEADERS]}).execute()
    for i in range(0, len(rows), 500):
        svc.spreadsheets().values().update(
            spreadsheetId=sid, range=f"'{args.out_tab}'!A{i + 2}",
            valueInputOption="RAW", body={"values": rows[i:i + 500]}).execute()
    print(f"  DONE -> '{args.out_tab}' ({len(rows)} companies)")


if __name__ == "__main__":
    main()
