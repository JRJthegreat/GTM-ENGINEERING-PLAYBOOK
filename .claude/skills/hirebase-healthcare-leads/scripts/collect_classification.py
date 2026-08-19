"""
Phase 3a (COLLECT) — gather the companies worth a second look before any DM
or email spend, using only text already on the sheet.

DELIBERATELY LIGHT (Jude, 2026-08-19: "no need to do too hard of a
classification"). The default verdict is KEEP. HireBase already flags agencies
(is_recruiting_agency / is_3rd_party_agency / recruiterAgency were False on
every row of the Aug 19th export), so this is a second pass for what those
flags miss, not a full re-classification. Only companies tripping a suspicion
signal are routed to the judge; everything else is auto-kept.

This is the repo's COLLECT -> JUDGE -> APPLY pattern: this script is purely
mechanical (no scraping, no LLM, no spend), Claude judges the uncertain pile
in-session, and apply_classification.py writes the verdicts back under a
hallucination guard.

What trips a review (a company needs only one):
  - agency language      staffing / recruiting / locums / travel nursing /
                         "our clients" / "we place" / outsourced workforce
  - not-an-employer      certification or training vendor, job board,
                         software / SaaS, investor or venture firm
  - non-US               foreign legal forms or no US state on any posting
  - contradiction        nothing healthcare-ish anywhere in its text

Writes candidates JSON. Deletes nothing, changes nothing.

Run:
  python3 -W ignore collect_classification.py --sheet_url URL \
    --tabs "SLP Campaign" "General Campaign" --out data/class_candidates.json
"""

import os
import re
import json
import argparse
from collections import defaultdict

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_PATH = os.path.join(SCRIPT_DIR, "..", "..", "..", "token.json")

COL_TITLE, COL_COMPANY, COL_SITE = 1, 10, 11
COL_SIZE, COL_DESC, COL_STATE = 12, 15, 18
COL_OPENINGS = 39

AGENCY_RE = re.compile(
    r"\b(staffing|staff augmentation|recruit\w*|locum|locums|travel nurs\w*|"
    r"travel therapy|per[- ]diem staffing|workforce solutions|talent acquisition|"
    r"we place|placement (agency|services|firm)|our clients|client facilities|"
    r"outsourced (therapy|staffing|clinical)|contract therapists|"
    r"managed service provider|msp|vendor management)\b", re.IGNORECASE)

NOT_EMPLOYER_RE = re.compile(
    r"\b(certification|certifications|credentialing exam|test prep|"
    r"e-?learning|online course|curriculum|job board|job search|"
    r"career site|aggregator|software|saas|platform for|technology company|"
    r"venture|ventures|capital|private equity|portfolio compan\w*|"
    r"consultancy|consulting firm|marketing agency)\b", re.IGNORECASE)

NON_US_RE = re.compile(
    r"(\bGmbH\b|\bS\.?A\.?R\.?L\b|\bB\.?V\.?\b|\bPty\b|\bLtd\b|\bLimited\b|"
    r"\bUK\b|United Kingdom|\bHôtel|\bHotel\b|Ireland|Australia|Canada\b)",
    re.IGNORECASE)

HEALTHCARE_RE = re.compile(
    r"\b(health|clinic|clinical|patient|patients|medical|medicine|therapy|"
    r"therapist|nursing|nurse|hospital|hospice|rehab\w*|dental|behavioral|"
    r"pediatric\w*|physician|care|caregiv\w*|autism|speech|occupational)\b",
    re.IGNORECASE)


def sheet_id_of(url):
    return re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url).group(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet_url", required=True)
    ap.add_argument("--tabs", nargs="+", required=True)
    ap.add_argument("--out", default=os.path.join(SCRIPT_DIR, "..", "data",
                                                  "class_candidates.json"))
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
            name = c(COL_COMPANY)
            if not name:
                continue
            d = comps.setdefault(name, {
                "company": name, "website": "", "size": "", "desc": "",
                "titles": [], "states": set(), "lanes": set(), "rows": 0})
            d["rows"] += 1
            d["lanes"].add(tab)
            d["website"] = d["website"] or c(COL_SITE)
            d["size"] = d["size"] or c(COL_SIZE)
            if len(c(COL_DESC)) > len(d["desc"]):
                d["desc"] = c(COL_DESC)
            if c(COL_TITLE) and len(d["titles"]) < 6:
                if c(COL_TITLE) not in d["titles"]:
                    d["titles"].append(c(COL_TITLE)[:70])
            if c(COL_STATE):
                d["states"].add(c(COL_STATE))

    auto, uncertain = [], []
    for name, d in comps.items():
        blob = f"{name} {d['desc']} {' '.join(d['titles'])}"
        reasons = []
        if AGENCY_RE.search(blob):
            reasons.append("agency_language")
        if NOT_EMPLOYER_RE.search(blob):
            reasons.append("maybe_not_employer")
        if NON_US_RE.search(name) or not d["states"]:
            reasons.append("maybe_non_us")
        if not HEALTHCARE_RE.search(blob):
            reasons.append("no_healthcare_signal")
        # HireBase resolves companyData by fuzzy NAME match and gets it wrong
        # on a real subset: "GMH UK" (UK steel billets) posting RNs in Georgia,
        # "SIH Hotels" (hotel investor) posting RN Same Day Surgery in Illinois
        # -- that employer is Southern Illinois Healthcare. When the profile
        # text carries no healthcare signal but the JOBS are clinical, the
        # attached profile belongs to a different company, which means its
        # website, size and LinkedIn are all the wrong company's too.
        if (d["desc"] and not HEALTHCARE_RE.search(d["desc"])
                and HEALTHCARE_RE.search(" ".join(d["titles"]))):
            reasons.append("profile_mismatch")
        rec = {
            "company": name, "website": d["website"], "size": d["size"],
            "rows": d["rows"], "lanes": sorted(d["lanes"]),
            "states": sorted(d["states"])[:6], "titles": d["titles"],
            "desc": d["desc"][:700], "flags": reasons,
        }
        (uncertain if reasons else auto).append(rec)

    uncertain.sort(key=lambda r: -r["rows"])
    out = {"auto_keep_count": len(auto),
           "auto_keep": sorted(r["company"] for r in auto),
           "uncertain": uncertain}
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(out, f, indent=1)

    print(f"companies: {len(comps)}")
    print(f"  auto KEEP (no suspicion signal): {len(auto)}")
    print(f"  need a judgement call:           {len(uncertain)}")
    from collections import Counter
    fc = Counter(f for r in uncertain for f in r["flags"])
    for f, n in fc.most_common():
        print(f"    {f:22s} {n:4d}")
    print(f"  -> {args.out}")


if __name__ == "__main__":
    main()
