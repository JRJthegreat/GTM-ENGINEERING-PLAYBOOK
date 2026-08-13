"""
Tag every classified row with whether it belongs in the outreach pool, on a
SECOND axis from column Y: is this actually a STAFFING / RECRUITMENT FIRM?

WHY A SECOND AXIS (Jude, 2026-08-13)
------------------------------------
Column Y answers "is this healthcare?". It does not answer "can this company
take a client req?". The HEALTHCARE_ADJACENT pile is full of companies that
are healthcare-shaped but structurally cannot be a recruitment partner:
hospitals and home-care providers (they BUY staff), medical-billing and RCM
outsourcers, healthcare IT consultancies, job boards, VMS/MSP platforms, and
vendors who sell TO staffing agencies. Those are not leads no matter how
healthcare they look.

Jude's rule: keep HEALTHCARE_STAFFING + HEALTHCARE_ADJACENT + UNCERTAIN, but
only where the company is a staffing/recruitment firm. Adjacent is kept on
purpose — an IT-staffing firm with a healthcare desk, or a life-sciences
recruiter, can still work a clinical req, and the campaign will tell us
whether they do.

NOTHING IS EVER DELETED. Rows that fail are tagged, not removed (standing
repo rule, and Jude restated it here).

TAGS (column Z, `outreach_flag`)
  KEEP                 in the pool: a staffing/recruitment firm that is
                       healthcare, adjacent, or uncertain
  SKIP_NOT_STAFFING    healthcare-ish but not a staffing firm (provider,
                       software/marketplace, BPO/RCM, job board, consultancy,
                       vendor-to-staffing)
  SKIP_NOT_HEALTHCARE  column Y said NOT_HEALTHCARE
  (blank)              column Y not yet classified

HEALTHCARE_STAFFING rows are KEEP automatically — column Y's definition
("places clinicians with healthcare employers") already entails a staffing
firm, so re-judging them would be busywork. Only ADJACENT and UNCERTAIN rows
need a verdict, which is what --collect exports.

No LLM API call. Same collect / judge / apply shape as
classify_healthcare_icp.py, and the same guard: a verdict for a row absent
from the candidates file is refused, as is any tag outside the vocabulary.

Run:
  # 1. export the rows that need judging (ADJACENT + UNCERTAIN)
  python3 -W ignore flag_outreach_targets.py --sheet_url "URL" --tab "50-200 EMP" \
      --collect --out data/staffing_candidates.json

  # 2. Claude judges in-session -> [{"row": 12, "staffing": true}, ...]

  # 3. apply (also auto-tags the HEALTHCARE_STAFFING and NOT_HEALTHCARE rows)
  python3 -W ignore flag_outreach_targets.py --sheet_url "URL" --tab "50-200 EMP" \
      --candidates data/staffing_candidates.json \
      --verdicts data/staffing_verdicts.json --apply
"""

import os
import re
import json
import time
import argparse
from collections import Counter
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_PATH = os.path.join(SCRIPT_DIR, "..", "..", "..", "token.json")

COL_NAME = 0        # A
COL_EMPSIZE = 2     # C
COL_INDUSTRY = 3    # D
COL_PRODUCTS = 4    # E
COL_DESC = 5        # F
COL_SEO = 6         # G
COL_WEBSITE = 7     # H
COL_DM_EMAIL = 16   # Q
COL_ADDED = 22      # W
COL_ICP = 24        # Y
COL_FLAG = 25       # Z

NEEDS_VERDICT = ("HEALTHCARE_ADJACENT", "UNCERTAIN")
AUTO_KEEP = ("HEALTHCARE_STAFFING",)
AUTO_SKIP = ("NOT_HEALTHCARE",)
TAGS = {"KEEP", "SKIP_NOT_STAFFING", "SKIP_NOT_HEALTHCARE"}

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


def squash(s, cap=700):
    return re.sub(r"\s+", " ", (s or "")).strip()[:cap]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet_url", required=True)
    ap.add_argument("--tab", required=True)
    ap.add_argument("--collect", action="store_true")
    ap.add_argument("--out", default="staffing_candidates.json")
    ap.add_argument("--candidates", default="")
    ap.add_argument("--verdicts", default="")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    sheet_id = parse_sheet_id(args.sheet_url)
    tab = args.tab
    service = get_service()
    rows = service.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=f"'{tab}'!A:Z").execute().get("values", [])[1:]

    def cell(row, idx):
        return (row[idx].strip() if len(row) > idx and row[idx] else "")

    # ------------------------------------------------------------- collect --
    if args.collect:
        out = []
        for i, row in enumerate(rows):
            if not cell(row, COL_NAME):
                continue
            if cell(row, COL_ICP) not in NEEDS_VERDICT:
                continue
            out.append({
                "row": i + 2,
                "company": cell(row, COL_NAME),
                "icp": cell(row, COL_ICP),
                "size": cell(row, COL_EMPSIZE),
                "industry": squash(cell(row, COL_INDUSTRY), 120),
                "products": squash(cell(row, COL_PRODUCTS), 220),
                "description": squash(cell(row, COL_DESC)),
                "seo": squash(cell(row, COL_SEO), 300),
            })
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w") as f:
            json.dump(out, f, indent=1)
        print(f"Collected {len(out)} ADJACENT/UNCERTAIN rows from '{tab}' -> {args.out}")
        return

    # --------------------------------------------------------------- apply --
    if not (args.candidates and args.verdicts):
        print("ERROR: need --collect, or both --candidates and --verdicts")
        return

    with open(args.candidates) as f:
        allowed = {c["row"] for c in json.load(f)}
    with open(args.verdicts) as f:
        verdicts = {v["row"]: v for v in json.load(f)}

    refused = 0
    for r in verdicts:
        if r not in allowed:
            print(f"  [!] row {r}: not in candidates file — refused")
            refused += 1

    updates, counts, unjudged = [], Counter(), 0
    for i, row in enumerate(rows):
        rn = i + 2
        if not cell(row, COL_NAME):
            continue
        icp = cell(row, COL_ICP)
        if not icp:
            continue
        if icp in AUTO_KEEP:
            tag = "KEEP"
        elif icp in AUTO_SKIP:
            tag = "SKIP_NOT_HEALTHCARE"
        elif icp in NEEDS_VERDICT:
            v = verdicts.get(rn)
            if v is None or rn not in allowed:
                unjudged += 1
                continue
            tag = "KEEP" if v.get("staffing") else "SKIP_NOT_STAFFING"
        else:
            continue
        if tag not in TAGS:
            refused += 1
            continue
        counts[tag] += 1
        updates.append((rn, tag))

    print(f"\nTab '{tab}': {len(updates)} rows tagged, {refused} refused, "
          f"{unjudged} awaiting a verdict")
    for k, n in counts.most_common():
        print(f"  {k:<22}{n}")
    if not args.apply:
        print("\n(dry run — pass --apply to write column Z)")
        return

    meta = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
    for s in meta["sheets"]:
        if s["properties"]["title"] != tab:
            continue
        have = s["properties"]["gridProperties"]["columnCount"]
        if have < COL_FLAG + 1:
            service.spreadsheets().batchUpdate(
                spreadsheetId=sheet_id,
                body={"requests": [{"appendDimension": {
                    "sheetId": s["properties"]["sheetId"], "dimension": "COLUMNS",
                    "length": (COL_FLAG + 1) - have}}]}).execute()
        service.spreadsheets().values().update(
            spreadsheetId=sheet_id, range=f"'{tab}'!{col_letter(COL_FLAG)}1",
            valueInputOption="RAW", body={"values": [["outreach_flag"]]}).execute()

    for i in range(0, len(updates), WRITE_BATCH):
        chunk = updates[i:i + WRITE_BATCH]
        data = [{"range": f"'{tab}'!{col_letter(COL_FLAG)}{r}", "values": [[t]]}
                for r, t in chunk]
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
            print(f"  wrote {min(i + WRITE_BATCH, len(updates))}/{len(updates)}",
                  flush=True)

    print(f"\nDone. Column Z written on '{tab}'.")


if __name__ == "__main__":
    main()
