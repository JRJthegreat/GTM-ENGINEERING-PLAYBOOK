"""
Remove rows from a lane tab — by company_status, by company name, or both.

DESTRUCTIVE. Default is a dry run; --apply is required to actually delete, per
the repo's standing rule that rows are never removed without an explicit call.

EVERY DELETED ROW IS BACKED UP FIRST to data/deleted_rows_<tab>_<stamp>.json
(full cell contents plus the original row number) before a single row is
removed. If the backup cannot be written the deletion does not run.

Rows are deleted BOTTOM-UP in merged contiguous blocks. Deleting top-down
shifts every row beneath the cut and silently removes the wrong records.

Run:
  python3 -W ignore delete_rows.py --sheet_url URL --tab "General Campaign" \
    --statuses DROP_AGENCY DROP_NOT_EMPLOYER --companies "Elas" [--apply]
"""

import os
import re
import json
import argparse
from datetime import datetime

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_PATH = os.path.join(SCRIPT_DIR, "..", "..", "..", "token.json")
DATA_DIR = os.path.join(SCRIPT_DIR, "..", "data")

COL_COMPANY = 10
COL_STATUS = 47


def sheet_id_of(url):
    return re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url).group(1)


def contiguous(rows):
    """[2,3,4,9,10] -> [(2,4),(9,10)] — merged blocks, so 200 deletions
    become a handful of requests instead of 200."""
    out = []
    for r in sorted(rows):
        if out and r == out[-1][1] + 1:
            out[-1][1] = r
        else:
            out.append([r, r])
    return [tuple(b) for b in out]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet_url", required=True)
    ap.add_argument("--tab", required=True)
    ap.add_argument("--statuses", nargs="*", default=[])
    ap.add_argument("--companies", nargs="*", default=[])
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    if not args.statuses and not args.companies:
        raise SystemExit("Nothing selected — pass --statuses and/or --companies.")

    sid = sheet_id_of(args.sheet_url)
    svc = build("sheets", "v4",
                credentials=Credentials.from_authorized_user_file(TOKEN_PATH))

    meta = svc.spreadsheets().get(spreadsheetId=sid).execute()
    tab_id = next((s["properties"]["sheetId"] for s in meta["sheets"]
                   if s["properties"]["title"] == args.tab), None)
    if tab_id is None:
        raise SystemExit(f"No tab named {args.tab!r}")

    vals = svc.spreadsheets().values().get(
        spreadsheetId=sid, range=f"'{args.tab}'!A1:AV").execute().get("values", [])
    hdr, rows = vals[0], vals[1:]

    statuses, companies = set(args.statuses), set(args.companies)
    hits, kept = [], 0
    for i, r in enumerate(rows):
        def c(j, _r=r):
            return _r[j].strip() if j < len(_r) else ""
        if c(COL_STATUS) in statuses or c(COL_COMPANY) in companies:
            hits.append({"row": i + 2, "company": c(COL_COMPANY),
                         "status": c(COL_STATUS), "cells": r})
        else:
            kept += 1

    from collections import Counter
    print(f"[{args.tab}] {len(rows)} rows -> deleting {len(hits)}, keeping {kept}")
    for co, n in Counter(h["company"] for h in hits).most_common():
        st = next(h["status"] for h in hits if h["company"] == co)
        print(f"    {n:5d} rows  {co[:44]:44s} {st}")

    missing = companies - {h["company"] for h in hits}
    if missing:
        print(f"    (not present on this tab: {sorted(missing)})")

    if not hits:
        print("  nothing to do")
        return
    if not args.apply:
        print("\n  (no --apply — nothing deleted)")
        return

    os.makedirs(DATA_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^A-Za-z0-9]+", "_", args.tab)
    path = os.path.join(DATA_DIR, f"deleted_rows_{safe}_{stamp}.json")
    with open(path, "w") as f:
        json.dump({"tab": args.tab, "sheet": sid, "headers": hdr,
                   "deleted": hits}, f, indent=1)
    print(f"  backup written: {path} ({len(hits)} rows)")

    blocks = contiguous([h["row"] for h in hits])
    reqs = [{"deleteDimension": {"range": {
        "sheetId": tab_id, "dimension": "ROWS",
        "startIndex": a - 1, "endIndex": b}}}
        for a, b in sorted(blocks, reverse=True)]   # bottom-up
    svc.spreadsheets().batchUpdate(
        spreadsheetId=sid, body={"requests": reqs}).execute()
    print(f"  deleted {len(hits)} rows in {len(reqs)} block(s)")

    after = svc.spreadsheets().values().get(
        spreadsheetId=sid, range=f"'{args.tab}'!K:K").execute().get("values", [])
    print(f"  tab now has {len(after) - 1} data rows")


if __name__ == "__main__":
    main()
