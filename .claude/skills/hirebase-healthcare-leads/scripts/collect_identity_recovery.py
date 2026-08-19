"""
Phase 3e (COLLECT) — gather evidence to recover the REAL employer behind rows
HireBase stamped REVIEW_PROFILE_MISMATCH.

Those rows carry another company's name, website, size and LinkedIn because
HireBase fuzzy-matched the profile. The postings themselves are real clinical
demand, so the rows are recoverable rather than junk — this collects the two
free, high-signal sources that name the true employer:

  1. THE JOB DESCRIPTION. Postings almost always name their employer in the
     first couple of sentences ("Join a busy Therapy team at Nemours Children's
     Hospital, Delaware"), and col J already holds it. No fetch, no spend.
  2. THE ATS TENANT in col AR. A company's own hiring system is ground truth:
     `oneoncology.wd1.myworkdayjobs.com/Astera`, `jobs.lever.co/menta`,
     `careers.smartrecruiters.com/KIPP`.

Purely mechanical — no scraping, no LLM, no API cost. Claude judges the output
in-session and apply_identity_recovery.py writes the result under a
proof-on-page gate.

Run:
  python3 -W ignore collect_identity_recovery.py --sheet_url URL \
    --tabs "SLP Campaign" "General Campaign"
"""

import os
import re
import json
import argparse

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_PATH = os.path.join(SCRIPT_DIR, "..", "..", "..", "token.json")
DATA_DIR = os.path.join(SCRIPT_DIR, "..", "data")

COL_TITLE, COL_DESC, COL_COMPANY, COL_SITE = 1, 9, 10, 11
COL_CITY, COL_STATE, COL_BOARDLINK, COL_STATUS = 17, 18, 43, 47
TARGET = "REVIEW_PROFILE_MISMATCH"

ATS_HOST_TENANT = ("myworkdayjobs", "oraclecloud", "icims", "bamboohr",
                   "applytojob", "jobvite", "paycor")
ATS_PATH_TENANT = ("greenhouse", "lever.co", "smartrecruiters", "workable",
                   "paylocity", "ashbyhq", "recruitee")


def sheet_id_of(url):
    return re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url).group(1)


def ats_tenant(bl):
    """The employer's own handle inside its ATS URL."""
    if not bl:
        return ""
    host = re.sub(r"^https?://(www\.)?", "", bl).split("/")[0]
    path = [p for p in re.sub(r"^https?://[^/]+", "", bl).split("/") if p]
    if any(k in host for k in ATS_HOST_TENANT):
        t = host.split(".")[0]
    elif any(k in host for k in ATS_PATH_TENANT):
        t = path[-1] if path else ""
    else:
        t = host.split(".")[0]
    t = re.sub(r"^(careers?|jobs|www|https?)[-_]?", "", t, flags=re.I)
    t = re.sub(r"[-_]?(careers?|jobs|external|site)$", "", t, flags=re.I)
    t = re.sub(r"^jobs-at-", "", t, flags=re.I)
    return t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet_url", required=True)
    ap.add_argument("--tabs", nargs="+", required=True)
    ap.add_argument("--out", default=os.path.join(DATA_DIR, "identity_candidates.json"))
    args = ap.parse_args()

    svc = build("sheets", "v4",
                credentials=Credentials.from_authorized_user_file(TOKEN_PATH))
    sid = sheet_id_of(args.sheet_url)

    comps = {}
    for tab in args.tabs:
        vals = svc.spreadsheets().values().get(
            spreadsheetId=sid, range=f"'{tab}'!A1:AV").execute().get("values", [])
        for r in vals[1:]:
            def c(i, _r=r):
                return _r[i].strip() if i < len(_r) else ""
            if c(COL_STATUS) != TARGET:
                continue
            name = c(COL_COMPANY)
            if not name:
                continue
            d = comps.setdefault(name, {
                "row_company_name": name, "wrong_website": c(COL_SITE),
                "ats_url": c(COL_BOARDLINK), "ats_tenant": ats_tenant(c(COL_BOARDLINK)),
                "rows": 0, "lanes": set(), "titles": [], "locations": [],
                "description_excerpts": []})
            d["rows"] += 1
            d["lanes"].add(tab)
            if c(COL_TITLE) and c(COL_TITLE) not in d["titles"] and len(d["titles"]) < 5:
                d["titles"].append(c(COL_TITLE)[:70])
            loc = ", ".join(x for x in (c(COL_CITY), c(COL_STATE)) if x)
            if loc and loc not in d["locations"] and len(d["locations"]) < 5:
                d["locations"].append(loc)
            # The employer is named early — keep the head of the description.
            if c(COL_DESC) and len(d["description_excerpts"]) < 3:
                head = re.sub(r"\s+", " ", c(COL_DESC))[:520]
                if head not in d["description_excerpts"]:
                    d["description_excerpts"].append(head)

    out = []
    for name, d in sorted(comps.items(), key=lambda x: -x[1]["rows"]):
        d["lanes"] = sorted(d["lanes"])
        out.append(d)

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(args.out, "w") as f:
        json.dump({"companies": out}, f, indent=1)

    print(f"{len(out)} mismatch companies / {sum(d['rows'] for d in out)} rows")
    print(f"  with an ATS tenant:        {sum(1 for d in out if d['ats_tenant'])}")
    print(f"  with a description:        {sum(1 for d in out if d['description_excerpts'])}")
    print(f"  -> {args.out}")


if __name__ == "__main__":
    main()
