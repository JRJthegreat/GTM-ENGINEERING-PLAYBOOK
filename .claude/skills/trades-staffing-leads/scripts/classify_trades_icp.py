"""
Decide whether an AI Ark firm actually recruits FOR TRADES EMPLOYERS, i.e.
whether it is the ICP at all.

RUN THIS BEFORE ANY DM OR EMAIL SPEND. On the first trades batch it was run
AFTER the waterfall (2026-09-11) and paid to enrich insurance-claims,
superyacht and mobile-notary firms. Free filter first, paid stages second.

TWO-AXIS RULE (mirrors the healthcare lane, employer-type based):
  staffing_firm          does it recruit / place people for client companies?
  serves_trades_employers are those clients contractors, industrial,
                          manufacturing or energy companies? ANY role placed
                          INTO such an employer counts (an estimator or a
                          plant manager is as valid as a welder), the same way
                          healthcare counted a biller placed into a hospital.
  KEEP = both true.

VOCABULARY (col Y icp_class)
  TRADES_STAFFING   places people with trades / construction / industrial /
                    energy employers. THE ICP.
  TRADES_ADJACENT   trades-flavoured, wrong buyer: trade schools, union
                    halls and apprenticeship trusts, safety consultancies,
                    equipment rental, PEO / payroll-only.
  NOT_TRADES        general, IT, healthcare, finance, exec search with no
                    trades or industrial client base.
  UNCERTAIN         too little text to judge. Never contacted.

Col Z outreach_flag: KEEP / SKIP_NOT_STAFFING / SKIP_NOT_TRADES
Col AA research_notes.

NO LLM API CALL — collect / Claude judges in-session / apply, same as the
healthcare twin. A verdict for a row absent from the candidates file is
refused (hallucination guard), as is any class outside the vocabulary.

Schema: AI Ark A-N export.
  A:Company Name  C:Employee Size  D:Industry  E:Product and Services
  F:Description   G:SEO Description  H:Website

Run:
  python3 -W ignore classify_trades_icp.py --sheet_url URL --tab "1-50 EMP" \
      --collect --out cands.json
  python3 -W ignore classify_trades_icp.py --sheet_url URL --tab "1-50 EMP" \
      --candidates cands.json --verdicts verdicts.json --apply
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

CLASSES = {"TRADES_STAFFING", "TRADES_ADJACENT", "NOT_TRADES", "UNCERTAIN"}
LARGE_BANDS = ("201-500", "501-1000", "1001-5000", "5001-10000", "10001+")

COL_NAME = 0        # A
COL_EMPSIZE = 2     # C
COL_INDUSTRY = 3    # D
COL_PRODUCTS = 4    # E
COL_DESC = 5        # F
COL_SEO = 6         # G
COL_WEBSITE = 7     # H
COL_ADDED = 22      # W
COL_ICP = 24        # Y

WRITE_BATCH = 10
MAX_TEXT = 700


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


def squash(s, cap=MAX_TEXT):
    s = re.sub(r"\s+", " ", (s or "")).strip()
    return s[:cap]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet_url", required=True)
    ap.add_argument("--tab", required=True)
    ap.add_argument("--collect", action="store_true")
    ap.add_argument("--out", default="icp_candidates.json")
    ap.add_argument("--candidates", default="")
    ap.add_argument("--verdicts", default="")
    ap.add_argument("--large_only", action="store_true",
                    help="only Employee Size bands 201-500 and up")
    ap.add_argument("--uncontacted_only", action="store_true",
                    help="skip rows already pushed to a campaign")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    sheet_id = parse_sheet_id(args.sheet_url)
    tab = args.tab
    service = get_service()
    rows = service.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=f"'{tab}'!A:Y").execute().get("values", [])[1:]

    def cell(row, idx):
        return (row[idx].strip() if len(row) > idx and row[idx] else "")

    # ------------------------------------------------------------- collect --
    if args.collect:
        out = []
        for i, row in enumerate(rows):
            name = cell(row, COL_NAME)
            if not name:
                continue
            if cell(row, COL_ICP):
                continue                      # already judged, resume-safe
            size = cell(row, COL_EMPSIZE)
            if args.large_only and size not in LARGE_BANDS:
                continue
            if args.uncontacted_only and cell(row, COL_ADDED).upper() in (
                    "TRUE", "BLOCKLISTED", "PRIOR_CAMPAIGN"):
                continue
            out.append({
                "row": i + 2,
                "company": name,
                "size": size,
                "industry": squash(cell(row, COL_INDUSTRY), 120),
                "products": squash(cell(row, COL_PRODUCTS), 220),
                "description": squash(cell(row, COL_DESC)),
                "seo": squash(cell(row, COL_SEO), 300),
                "website": cell(row, COL_WEBSITE),
            })
            if args.limit and len(out) >= args.limit:
                break
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w") as f:
            json.dump(out, f, indent=1)
        print(f"Collected {len(out)} rows from '{tab}' -> {args.out}")
        n_text = sum(1 for r in out if len(r["description"]) + len(r["seo"]) > 80)
        print(f"  with usable description text: {n_text}  "
              f"({len(out) - n_text} will likely be UNCERTAIN)")
        return

    # --------------------------------------------------------------- apply --
    if not (args.candidates and args.verdicts):
        print("ERROR: need --collect, or both --candidates and --verdicts")
        return

    with open(args.candidates) as f:
        allowed = {c["row"]: c["company"] for c in json.load(f)}
    with open(args.verdicts) as f:
        verdicts = json.load(f)

    updates, counts, refused = [], {}, 0
    for v in verdicts:
        r, cls = v.get("row"), v.get("class")
        if r not in allowed:
            print(f"  [!] row {r}: not in candidates file — refused")
            refused += 1
            continue
        if cls not in CLASSES:
            print(f"  [!] row {r}: class {cls!r} not in vocabulary — refused")
            refused += 1
            continue
        counts[cls] = counts.get(cls, 0) + 1
        updates.append((r, cls))

    print(f"\nVerdicts: {len(updates)} valid, {refused} refused")
    for k, n in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {k:<22} {n}")
    if not args.apply:
        print("\n(dry run — pass --apply to write column Y)")
        return

    meta = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
    for s in meta["sheets"]:
        if s["properties"]["title"] != tab:
            continue
        have = s["properties"]["gridProperties"]["columnCount"]
        if have < COL_ICP + 1:
            service.spreadsheets().batchUpdate(
                spreadsheetId=sheet_id,
                body={"requests": [{"appendDimension": {
                    "sheetId": s["properties"]["sheetId"], "dimension": "COLUMNS",
                    "length": (COL_ICP + 1) - have}}]}).execute()
        service.spreadsheets().values().update(
            spreadsheetId=sheet_id, range=f"'{tab}'!{col_letter(COL_ICP)}1",
            valueInputOption="RAW", body={"values": [["icp_class"]]}).execute()

    for i in range(0, len(updates), WRITE_BATCH):
        chunk = updates[i:i + WRITE_BATCH]
        data = [{"range": f"'{tab}'!{col_letter(COL_ICP)}{r}", "values": [[cls]]}
                for r, cls in chunk]
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
        print(f"  wrote {i + len(chunk)}/{len(updates)}", flush=True)

    print(f"\nDone. {len(updates)} rows classified in column Y.")


if __name__ == "__main__":
    main()
