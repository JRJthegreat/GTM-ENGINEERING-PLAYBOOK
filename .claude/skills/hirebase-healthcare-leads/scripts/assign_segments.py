"""
Phase 3g — assign the outreach segment, size band and DM target per company.

Prep for DM discovery and copy. Computes nothing that costs money; every input
is already on the sheet. One row per JOB is kept as the evidence layer, but the
unit of outreach is the COMPANY — a decision maker with nine open reqs is still
one person with one inbox, so openings/cities/roles become copy VARIABLES
rather than a reason to send more email.

SEGMENT (col AW) — drives which hook the copy opens with:
  SINGLE            one live posting. The specific req is the hook.
  MULTI_ONE_CITY    several postings, one city. Volume at a single site.
  MULTI_CITY        several postings across cities. The strongest pain signal:
                    they have outgrown their local network, which is exactly
                    the connector pitch.

SIZE_BAND (col AX) — WITHIN_CAP or LARGE_ORG.
  The sheet's own size column cannot be trusted (HireBase pre-filtered the
  export to <=500, yet "Compassus" arrived tagged "201 to 500" with 385 live
  postings). Live openings are the better proxy: a sub-500 clinical org does
  not run 25+ simultaneous ads. Companies already adjudicated as large during
  identity recovery keep that verdict.

DM_TARGET (col AY) — CEO everywhere, Jude's call 2026-08-19.
  Under 500 this is the only evidence-backed rung: owner/CEO replied 2.80%
  (17/608) against COO/Ops 0.00% (0/87) and HR 0.00% (0/32). Multi-city
  companies are NOT routed to a COO for that reason, and because only the
  owner is company-wide — office managers are per-location.

  At 500+ this is a DELIBERATE TEST, not a proven rung. The 0.00% from 251
  large companies is confounded: those were targeted 175 clinical / 22 HR /
  41 other with no actual CEO among them, so what it disproves is
  clinical-and-HR-at-big-orgs. A large-org CEO has never been tried. AX keeps
  the two bands separable so this run answers the question instead of blurring
  it into the aggregate.

Also writes the copy inputs that would otherwise need recomputing: AZ cities,
BA roles.

Un-parks REVIEW_OVER_CAP rows to KEEP (they are now in the test arm) while
stamping AX=LARGE_ORG so they stay measurable and can be pulled back out.

Run:
  python3 -W ignore assign_segments.py --sheet_url URL \
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

COL_TITLE, COL_COMPANY, COL_CITY, COL_STATE = 1, 10, 17, 18
COL_STATUS = 47
COL_SEGMENT, COL_BAND, COL_DM, COL_CITIES, COL_ROLES = 48, 49, 50, 51, 52
NCOLS = 53
CHUNK = 500
LARGE_OPENINGS = 25

HDRS = ["segment", "size_band", "dm_target", "cities", "roles"]


def sheet_id_of(url):
    return re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url).group(1)


def a1(col, row):
    s, c = "", col + 1
    while c:
        c, r = divmod(c - 1, 26)
        s = chr(65 + r) + s
    return f"{s}{row}"


def clean_role(t):
    """'Pediatric Speech Language Pathologist (SLP) - PRN' -> the role stem."""
    t = re.sub(r"\s*[\(\[].*?[\)\]]", " ", t or "")
    t = re.split(r"\s+[-–—/|,]\s+", t)[0]
    t = re.sub(r"\b(prn|per diem|full[- ]time|part[- ]time|casual|weekend|"
               r"days?|nights?|sign[- ]on.*|bonus.*)\b", " ", t, flags=re.I)
    return re.sub(r"\s+", " ", t).strip()


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
    ap.add_argument("--large_openings", type=int, default=LARGE_OPENINGS)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    svc = build("sheets", "v4",
                credentials=Credentials.from_authorized_user_file(TOKEN_PATH))
    sid = sheet_id_of(args.sheet_url)

    # Pass 1 — aggregate per company ACROSS both lanes, so a company in both
    # gets one consistent segment.
    agg = {}
    raw = {}
    for tab in args.tabs:
        vals = svc.spreadsheets().values().get(
            spreadsheetId=sid, range=f"'{tab}'!A1:BA").execute().get("values", [])
        raw[tab] = vals
        for r in vals[1:]:
            def c(i, _r=r):
                return _r[i].strip() if i < len(_r) else ""
            name, st = c(COL_COMPANY), c(COL_STATUS)
            if not name or st not in ("KEEP", "REVIEW_OVER_CAP"):
                continue
            d = agg.setdefault(name, {"n": 0, "cities": [], "roles": [],
                                      "states": set(), "parked": False})
            d["n"] += 1
            if st == "REVIEW_OVER_CAP":
                d["parked"] = True
            if c(COL_CITY) and c(COL_CITY) not in d["cities"]:
                d["cities"].append(c(COL_CITY))
            role = clean_role(c(COL_TITLE))
            if role and role not in d["roles"]:
                d["roles"].append(role)
            if c(COL_STATE):
                d["states"].add(c(COL_STATE))

    plan = {}
    for name, d in agg.items():
        seg = ("SINGLE" if d["n"] == 1
               else "MULTI_ONE_CITY" if len(d["cities"]) <= 1 else "MULTI_CITY")
        band = ("LARGE_ORG" if (d["parked"] or d["n"] >= args.large_openings)
                else "WITHIN_CAP")
        plan[name] = {
            "segment": seg, "band": band, "dm": "CEO",
            "cities": ", ".join(d["cities"][:6]),
            "roles": ", ".join(d["roles"][:6]),
        }

    print(f"{len(plan)} companies / {sum(d['n'] for d in agg.values())} rows\n")
    print("SEGMENT x SIZE_BAND (companies):")
    x = Counter((p["segment"], p["band"]) for p in plan.values())
    for band in ("WITHIN_CAP", "LARGE_ORG"):
        for seg in ("SINGLE", "MULTI_ONE_CITY", "MULTI_CITY"):
            n = x.get((seg, band), 0)
            rows = sum(agg[c]["n"] for c, p in plan.items()
                       if p["segment"] == seg and p["band"] == band)
            if n:
                print(f"  {band:11s} {seg:15s} {n:4d} companies  {rows:5d} rows")
    print(f"\n  dm_target = CEO on all {len(plan)} companies")
    large = [c for c, p in plan.items() if p["band"] == "LARGE_ORG"]
    print(f"  LARGE_ORG test arm: {len(large)} companies, "
          f"{sum(agg[c]['n'] for c in large)} rows")
    for c in sorted(large, key=lambda c: -agg[c]["n"])[:10]:
        print(f"     {agg[c]['n']:4d} openings  {c}")

    if not args.apply:
        print("\n  (no --apply — nothing written)")
        return

    meta = svc.spreadsheets().get(spreadsheetId=sid).execute()
    reqs = [{"updateSheetProperties": {"properties": {
        "sheetId": s["properties"]["sheetId"],
        "gridProperties": {"columnCount": NCOLS}},
        "fields": "gridProperties.columnCount"}}
        for s in meta["sheets"] if s["properties"]["title"] in args.tabs
        and s["properties"]["gridProperties"]["columnCount"] < NCOLS]
    if reqs:
        svc.spreadsheets().batchUpdate(spreadsheetId=sid,
                                       body={"requests": reqs}).execute()

    for tab in args.tabs:
        rows = raw[tab][1:]
        block, status, touched = [], [], 0
        for r in rows:
            def c(i, _r=r):
                return _r[i].strip() if i < len(_r) else ""
            p = plan.get(c(COL_COMPANY)) if c(COL_STATUS) in ("KEEP", "REVIEW_OVER_CAP") else None
            if not p:
                block.append(["", "", "", "", ""])
                status.append([c(COL_STATUS)])
                continue
            touched += 1
            block.append([p["segment"], p["band"], p["dm"], p["cities"], p["roles"]])
            status.append(["KEEP"])          # un-park the test arm
        write(svc, sid, f"'{tab}'!{a1(COL_SEGMENT, 1)}", [HDRS])
        for i in range(0, len(block), CHUNK):
            write(svc, sid, f"'{tab}'!{a1(COL_SEGMENT, i + 2)}", block[i:i + CHUNK])
        for i in range(0, len(status), CHUNK):
            write(svc, sid, f"'{tab}'!{a1(COL_STATUS, i + 2)}", status[i:i + CHUNK])
        print(f"  [{tab}] {touched} rows segmented")
    print("  done")


if __name__ == "__main__":
    main()
