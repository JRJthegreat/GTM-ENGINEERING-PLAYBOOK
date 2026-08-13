"""
Apply outreach-pool verdicts (staffing_firm + serves_healthcare, from the
Claude-in-session / Sonnet-worker judge pipeline) to the AI Ark sheet, and
persist a durable local log first so no judged row is ever lost to a
scratchpad cleanup or an interrupted run.

WHY A LOCAL LOG (Jude, 2026-08-13)
-----------------------------------
Classifying ~5,000 rows runs across many worker batches over more than one
session. Every row a worker (or Claude in-session) judges is written to
data/icp_research_log.jsonl THE MOMENT it's produced — same convention as
find_ceo_pm_demand.py's pm_dm_cache.jsonl. Applying to the sheet then
becomes a replay: read the log, dedupe by row (last write wins), write.
Re-running this script after a partial sheet-write failure is always safe.

TWO-AXIS RULE (superseded the old 4-way icp_class taxonomy, 2026-08-13):
  staffing_firm      does the company recruit/place people for clients?
  serves_healthcare  do its clients include healthcare provider orgs —
                     ANY role type (clinical, IT, front desk, billing,
                     sales placed INTO a hospital/practice) counts; but
                     manufacturer/sponsor-facing work (pharma, biotech,
                     device, CRO, clinical-research, medical/device SALES
                     REPS placed at a manufacturer) does NOT count.
  KEEP = both true. Full rule: see RULE.md alongside the worker scripts.

Columns (AI Ark A-N export + demand-track O-W):
  Y:icp_class      kept for continuity with the earlier single-axis pass
                   (classify_healthcare_icp.py) — HEALTHCARE_STAFFING /
                   HEALTHCARE_ADJACENT / NOT_HEALTHCARE / UNCERTAIN, or
                   MFG_SPONSOR_FACING for the manufacturer-facing exclusion
                   this rule added.
  Z:outreach_flag  KEEP / SKIP_NOT_STAFFING / SKIP_NOT_HEALTHCARE
  AA:research_notes free-text facts a worker found (web search or
                   description) — for personalization later. Blank where
                   the row was judged from existing sheet text only.

Nothing is ever deleted; failing rows are tagged, never removed.

Run:
  python3 -W ignore apply_icp_research.py --sheet_url "URL" --tab "1-50 EMP" \
      --log data/icp_research_log.jsonl --apply
"""

import os
import re
import json
import time
import argparse
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_PATH = os.path.join(SCRIPT_DIR, "..", "..", "..", "token.json")
DEFAULT_LOG = os.path.join(SCRIPT_DIR, "..", "data", "icp_research_log.jsonl")

COL_NAME = 0        # A
COL_ICP = 24        # Y
COL_FLAG = 25        # Z
COL_NOTES = 26        # AA

WRITE_BATCH = 10


def col_letter(idx):
    s, idx = "", idx + 1
    while idx:
        idx, r = divmod(idx - 1, 26)
        s = chr(65 + r) + s
    return s


def parse_sheet_id(url):
    return url.split("/d/")[1].split("/")[0]


def get_service():
    with open(TOKEN_PATH) as f:
        td = json.load(f)
    creds = Credentials(token=td["token"], refresh_token=td["refresh_token"],
                        token_uri=td["token_uri"], client_id=td["client_id"],
                        client_secret=td["client_secret"],
                        scopes=td.get("scopes",
                                      ["https://www.googleapis.com/auth/spreadsheets"]))
    if creds.expired:
        creds.refresh(Request())
        td["token"] = creds.token
        with open(TOKEN_PATH, "w") as f:
            json.dump(td, f)
    return build("sheets", "v4", credentials=creds)


def load_log(path):
    """Dedupe by row, last write wins. Every record is a durable receipt of
    a judgment already made — never re-derive, only replay."""
    latest = {}
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    latest[rec["row"]] = rec
                except (json.JSONDecodeError, KeyError):
                    continue
    return latest


def icp_label(rec):
    if not rec.get("staffing_firm"):
        return "NOT_STAFFING_FIRM"
    if rec.get("serves_healthcare"):
        return "HEALTHCARE_STAFFING"
    if rec.get("mfg_sponsor_facing"):
        return "MFG_SPONSOR_FACING"
    return "NOT_HEALTHCARE"


def flag_label(rec):
    if not rec.get("staffing_firm"):
        return "SKIP_NOT_STAFFING"
    if not rec.get("serves_healthcare"):
        return "SKIP_NOT_HEALTHCARE"
    return "KEEP"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet_url", required=True)
    ap.add_argument("--tab", required=True)
    ap.add_argument("--log", default=DEFAULT_LOG)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    log = load_log(args.log)
    print(f"Log: {len(log)} judged rows total ({args.log})")
    if not log:
        print("Nothing to apply.")
        return

    sheet_id = parse_sheet_id(args.sheet_url)
    tab = args.tab
    service = get_service()
    rows = service.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=f"'{tab}'!A:A").execute().get("values", [])

    def cell(r, i):
        return (r[i].strip() if len(r) > i and r[i] else "")

    updates = []
    for i, row in enumerate(rows[1:]):
        rn = i + 2
        if not cell(row, 0) or rn not in log:
            continue
        rec = log[rn]
        updates.append((rn, icp_label(rec), flag_label(rec), rec.get("notes", "")))

    counts = {}
    for _, _, flag, _ in updates:
        counts[flag] = counts.get(flag, 0) + 1
    print(f"Rows on '{tab}' matched to a log entry: {len(updates)}")
    for k, n in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {k:<22}{n}")
    if not args.apply:
        print("\n(dry run — pass --apply to write columns Y/Z/AA)")
        return

    meta = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
    for s in meta["sheets"]:
        if s["properties"]["title"] != tab:
            continue
        have = s["properties"]["gridProperties"]["columnCount"]
        if have < COL_NOTES + 1:
            service.spreadsheets().batchUpdate(
                spreadsheetId=sheet_id,
                body={"requests": [{"appendDimension": {
                    "sheetId": s["properties"]["sheetId"], "dimension": "COLUMNS",
                    "length": (COL_NOTES + 1) - have}}]}).execute()
        service.spreadsheets().values().update(
            spreadsheetId=sheet_id, range=f"'{tab}'!{col_letter(COL_ICP)}1:{col_letter(COL_NOTES)}1",
            valueInputOption="RAW",
            body={"values": [["icp_class", "outreach_flag", "research_notes"]]}).execute()

    for i in range(0, len(updates), WRITE_BATCH):
        chunk = updates[i:i + WRITE_BATCH]
        data = []
        for rn, icp, flag, notes in chunk:
            data.append({"range": f"'{tab}'!{col_letter(COL_ICP)}{rn}:{col_letter(COL_NOTES)}{rn}",
                        "values": [[icp, flag, notes]]})
        for attempt in range(4):
            try:
                service.spreadsheets().values().batchUpdate(
                    spreadsheetId=sheet_id,
                    body={"valueInputOption": "RAW", "data": data}).execute()
                break
            except Exception as e:
                if attempt < 3 and "429" in str(e):
                    time.sleep(65)
                else:
                    raise
        if (i // WRITE_BATCH) % 10 == 0 or i + WRITE_BATCH >= len(updates):
            print(f"  wrote {min(i + WRITE_BATCH, len(updates))}/{len(updates)}", flush=True)

    print(f"\nDone. Columns Y/Z/AA written on '{tab}'.")


if __name__ == "__main__":
    main()
