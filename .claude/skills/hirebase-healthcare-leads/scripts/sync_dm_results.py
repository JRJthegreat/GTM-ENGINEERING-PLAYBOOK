"""
Phase 5b — copy DM results from the worklist back onto every row of each
company in the lane tabs.

Pair to build_dm_worklist.py. The waterfall enriched one row per company;
this fans the answer out to that company's job rows so the lane tabs are
complete for generation and push.

STANDING RULE ENFORCED: DM name/title/LinkedIn are NEVER written without a
valid email. A worklist row that ended `not_found` or `no_dm_candidates`
contributes its STATUS only, so the row stays honest and a later rescue pass
can find it. Partial identity with no email is how a row later looks enriched
and silently is not.

Writes T-Y (DM Name, Title, LinkedIn, Email, First, Last) and AB (dm_status),
as contiguous column ranges rather than a request per row — a per-row write
blows the Sheets 60/min quota on 1,900 rows.

Run:
  python3 -W ignore sync_dm_results.py --sheet_url URL \
    --tabs "SLP Campaign" "General Campaign" [--apply]
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

W_COMPANY, W_DM, W_TITLE, W_LI, W_EMAIL, W_FIRST, W_LAST, W_STATUS = \
    10, 19, 20, 21, 22, 23, 24, 27
L_COMPANY, L_DM, L_STATUS_COL = 10, 19, 27
L_KEEP = 47
CHUNK = 500


def sheet_id_of(url):
    return re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url).group(1)


def a1(col, row):
    s, c = "", col + 1
    while c:
        c, r = divmod(c - 1, 26)
        s = chr(65 + r) + s
    return f"{s}{row}"


def write(svc, sid, rng, values, tries=4):
    for n in range(tries):
        try:
            return svc.spreadsheets().values().update(
                spreadsheetId=sid, range=rng, valueInputOption="RAW",
                body={"values": values}).execute()
        except Exception as e:
            if "429" not in str(e) or n == tries - 1:
                raise
            time.sleep(20 * (n + 1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet_url", required=True)
    ap.add_argument("--tabs", nargs="+", required=True)
    ap.add_argument("--worklist", default="DM Worklist")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    svc = build("sheets", "v4",
                credentials=Credentials.from_authorized_user_file(TOKEN_PATH))
    sid = sheet_id_of(args.sheet_url)

    wl = svc.spreadsheets().values().get(
        spreadsheetId=sid, range=f"'{args.worklist}'!A1:AD").execute().get("values", [])
    found = {}
    for r in wl[1:]:
        def c(i, _r=r):
            return _r[i].strip() if i < len(_r) else ""
        if not c(W_COMPANY):
            continue
        found[c(W_COMPANY)] = {
            "dm": c(W_DM), "title": c(W_TITLE), "li": c(W_LI),
            "email": c(W_EMAIL), "first": c(W_FIRST), "last": c(W_LAST),
            "status": c(W_STATUS)}

    withmail = sum(1 for v in found.values() if v["email"])
    print(f"worklist: {len(found)} companies, {withmail} with a verified email")
    print("  statuses:", dict(Counter(v["status"] or "(pending)"
                                      for v in found.values()).most_common(8)))

    for tab in args.tabs:
        vals = svc.spreadsheets().values().get(
            spreadsheetId=sid, range=f"'{tab}'!A1:BA").execute().get("values", [])
        rows = vals[1:]
        block, status, n_rows, n_mail = [], [], 0, 0
        for r in rows:
            def c(i, _r=r):
                return _r[i].strip() if i < len(_r) else ""
            f = found.get(c(L_COMPANY)) if c(L_KEEP) == "KEEP" else None
            if not f:
                block.append([c(19), c(20), c(21), c(22), c(23), c(24)])
                status.append([c(L_STATUS_COL)])
                continue
            n_rows += 1
            if f["email"]:
                block.append([f["dm"], f["title"], f["li"], f["email"],
                              f["first"], f["last"]])
                n_mail += 1
            else:
                # no verified email -> status only, never a half-filled row
                block.append([c(19), c(20), c(21), c(22), c(23), c(24)])
            status.append([f["status"] or c(L_STATUS_COL)])
        print(f"  [{tab}] {n_rows} rows matched, {n_mail} get a DM + email")
        if not args.apply:
            continue
        for i in range(0, len(block), CHUNK):
            write(svc, sid, f"'{tab}'!{a1(L_DM, i + 2)}", block[i:i + CHUNK])
        for i in range(0, len(status), CHUNK):
            write(svc, sid, f"'{tab}'!{a1(L_STATUS_COL, i + 2)}",
                  status[i:i + CHUNK])
        print(f"    written")
    if not args.apply:
        print("\n  (no --apply — nothing written)")


if __name__ == "__main__":
    main()
