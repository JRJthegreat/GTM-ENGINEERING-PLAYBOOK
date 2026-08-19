"""
Phase 3b (APPLY) — write company classification verdicts to the lane tabs.

Third step of the repo's COLLECT -> JUDGE -> APPLY pattern. The verdicts file
is hand-written by Claude in-session against collect_classification.py's
candidates file. THIS SCRIPT NEVER TRUSTS THAT FILE: a verdict naming a
company absent from the candidates file is refused (hallucination guard), and
a status outside the vocabulary is refused. The guard exists against Claude's
own mistakes, not just a model's.

Vocabulary:
  KEEP                      direct employer of clinicians — the ICP.
  DROP_AGENCY               staffing / recruiting / travel / federal clinical
                            staffing contractor. Places clinicians at OTHER
                            organisations, so it is a competitor, not a buyer.
  DROP_NOT_EMPLOYER         certification or training vendor, job board,
                            software or investor. Does not employ clinicians.
  REVIEW_PROFILE_MISMATCH   HireBase attached ANOTHER company's profile. The
                            jobs are genuinely clinical, but the name, website,
                            size and LinkedIn on the row all belong to a
                            different company, so nothing about the row can be
                            trusted for enrichment. Held back from spend until
                            the identity is confirmed (the ATS tenant in col AR
                            is the ground truth to confirm it from).

Companies collect_classification.py auto-kept are written KEEP without needing
a verdict line — the judge only rules on the uncertain pile.

NOTHING IS DELETED. This writes col AV (company_status) only; removing rows is
a separate, explicit call for Jude.

Run:
  python3 -W ignore apply_classification.py --sheet_url URL \
    --tabs "SLP Campaign" "General Campaign" --verdicts data/class_verdicts.json
  ... then re-run with --apply
"""

import os
import re
import json
import time
import argparse
from collections import Counter

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_PATH = os.path.join(SCRIPT_DIR, "..", "..", "..", "token.json")
DATA_DIR = os.path.join(SCRIPT_DIR, "..", "data")
LOG_PATH = os.path.join(DATA_DIR, "class_log.jsonl")

COL_COMPANY = 10
COL_STATUS = 47   # AV
CHUNK = 500

VOCAB = {"KEEP", "DROP_AGENCY", "DROP_NOT_EMPLOYER", "REVIEW_PROFILE_MISMATCH"}


def write(svc, sid, rng, values, tries=5):
    """Sheets allows 60 writes/min/user. Back off rather than dying halfway."""
    for n in range(tries):
        try:
            return svc.spreadsheets().values().update(
                spreadsheetId=sid, range=rng, valueInputOption="RAW",
                body={"values": values}).execute()
        except Exception as e:
            if "429" not in str(e) or n == tries - 1:
                raise
            time.sleep(20 * (n + 1))


def sheet_id_of(url):
    return re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url).group(1)


def a1(col, row):
    s, c = "", col + 1
    while c:
        c, r = divmod(c - 1, 26)
        s = chr(65 + r) + s
    return f"{s}{row}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet_url", required=True)
    ap.add_argument("--tabs", nargs="+", required=True)
    ap.add_argument("--candidates", default=os.path.join(DATA_DIR, "class_candidates.json"))
    ap.add_argument("--verdicts", default=os.path.join(DATA_DIR, "class_verdicts.json"))
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    cand = json.load(open(args.candidates))
    verdicts = json.load(open(args.verdicts))
    uncertain = {r["company"] for r in cand["uncertain"]}
    auto = set(cand["auto_keep"])

    # ---- guards -------------------------------------------------------
    unknown = [c for c in verdicts if c not in uncertain]
    if unknown:
        raise SystemExit(
            f"REFUSED — {len(unknown)} verdict(s) name a company that is not in "
            f"the uncertain pile (hallucination guard): {unknown[:6]}")
    badval = {c: v for c, v in verdicts.items()
              if (v.get("status") if isinstance(v, dict) else v) not in VOCAB}
    if badval:
        raise SystemExit(f"REFUSED — status outside vocabulary: {list(badval)[:6]}")
    missing = sorted(uncertain - set(verdicts))
    if missing:
        raise SystemExit(
            f"REFUSED — {len(missing)} uncertain compan(ies) have no verdict. "
            f"Every one must be ruled on: {missing[:8]}")

    resolved = {c: "KEEP" for c in auto}
    reasons = {}
    for c, v in verdicts.items():
        if isinstance(v, dict):
            resolved[c] = v["status"]
            reasons[c] = v.get("reason", "")
        else:
            resolved[c] = v

    print(f"candidates: {len(auto)} auto-keep + {len(uncertain)} judged "
          f"= {len(resolved)} companies")
    for s, n in Counter(resolved.values()).most_common():
        print(f"    {s:24s} {n:5d} companies")

    svc = build("sheets", "v4",
                credentials=Credentials.from_authorized_user_file(TOKEN_PATH))
    sid = sheet_id_of(args.sheet_url)

    grand = Counter()
    for tab in args.tabs:
        vals = svc.spreadsheets().values().get(
            spreadsheetId=sid, range=f"'{tab}'!A1:AV").execute().get("values", [])
        rows = vals[1:]
        # company_status is ONE contiguous column, so it is written as column
        # ranges, not a request per row. Row-at-a-time blew the Sheets write
        # quota (60 req/min; 1,998 rows at batch-of-10 is 200 requests). The
        # batch-of-10 rule guards expensive per-row enrichment — this step is
        # pure local computation, costs nothing per row, and is idempotent, so
        # a chunked column write is both correct and safe to resume.
        col, per_tab, unseen, n_rows = [], Counter(), set(), 0
        for r in rows:
            name = r[COL_COMPANY].strip() if len(r) > COL_COMPANY else ""
            st = resolved.get(name) if name else None
            if name and st is None:
                unseen.add(name)
            col.append([st or ""])
            if st:
                per_tab[st] += 1
                grand[st] += 1
                n_rows += 1
        print(f"\n  [{tab}] {n_rows} rows")
        for s, n in per_tab.most_common():
            print(f"      {s:24s} {n:5d} rows")
        if unseen:
            print(f"      (!) {len(unseen)} companies had no verdict: "
                  f"{sorted(unseen)[:4]}")
        if not args.apply:
            continue

        write(svc, sid, f"'{tab}'!{a1(COL_STATUS, 1)}", [["company_status"]])
        for i in range(0, len(col), CHUNK):
            write(svc, sid, f"'{tab}'!{a1(COL_STATUS, i + 2)}", col[i:i + CHUNK])
            print(f"      written {min(i + CHUNK, len(col))}/{len(col)}")

    if args.apply:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(LOG_PATH, "a") as f:
            for c, st in resolved.items():
                f.write(json.dumps({"company": c, "status": st,
                                    "reason": reasons.get(c, "")}) + "\n")
        print(f"\n  logged {len(resolved)} verdicts -> {LOG_PATH}")
    else:
        print("\n  (no --apply — nothing written)")
    print(f"\n  ROW TOTALS: {dict(grand)}")
    print("  NOTE: no rows deleted. Removing DROP_* rows is a separate call.")


if __name__ == "__main__":
    main()
