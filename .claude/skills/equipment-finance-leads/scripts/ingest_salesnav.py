"""
Ingest a LinkedIn Sales Navigator PEOPLE export (Apify
linkedin-sales-navigator-search-scraper output) into a clean 29-col
lender-contacts Google Sheet — one contact per lender, ready for the standard
enrichment tail (exa-website-enrichment for the domain, then AMF person for the
email; the DM is already identified by the Sales Nav search, so no DM-finding
or verify step is needed).

Columns are resolved by HEADER NAME, not letter, so every state's export ingests
with the same script regardless of column drift.

Cleaning:
  - drops clearly-non-lender company industries (real estate, construction,
    staffing, software, coworking, etc.) — Banking / Financial Services /
    Investment Banking / blank are kept
  - drops obvious non-lender company-name patterns (coworking "business center")
  - one row per company by default (highest-ranked title wins); --keep_all keeps
    every contact

Output layout (29-col base + lender extras) so exa/AMF run with default flags:
  A source   B industry   C person_location
  K Company Name   L Company Website(blank)   R City   S State
  T DM Name   U DM Title   V LinkedIn URL   W Email
  X First   Y Last   Z Email Body   AA Added to Instantly   AB status
  AC about(person)   AD salesNavUrl

Usage:
  python3 -W ignore .claude/skills/equipment-finance-leads/scripts/ingest_salesnav.py \
      --src_url "SALESNAV_SHEET_URL" --state TX [--src_gid 595030705] \
      [--master_url MASTER_SHEET_URL] [--keep_all] [--dry_run]

  # first state: omit --master_url -> creates a new master sheet, prints its URL
  # next states: pass --master_url <that url> -> appends, dedupes against it
"""
import argparse
import json
import os
import re
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TOKEN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "..", "..", "..", "token.json")
TAB = "Leads"

HEADERS = [
    "Source", "Industry", "Person Location", "", "",                # A-E
    "", "", "", "", "Notes",                                        # F-J
    "Company Name", "Company Website", "Company Size", "Revenue",    # K-N
    "CEO Name", "Company Description", "Benefits",                   # O-Q
    "City", "State",                                                # R-S
    "DM Name", "DM Title", "LinkedIn URL", "Email",                 # T-W
    "First Name", "Last Name", "Email Body", "Added to Instantly",  # X-AA
    "status", "About", "SalesNav URL", "Company LinkedIn",         # AB-AE
]

# Company industries that are clearly NOT lenders. Banking / Financial Services /
# Investment Banking / blank are kept (blank is common and usually still a lender).
DROP_INDUSTRY = {
    "real estate", "construction", "staffing and recruiting", "software development",
    "internet marketplace platforms", "technology, information and internet",
    "information technology & services", "retail", "hospitality", "insurance",
    "accounting", "law practice", "legal services", "marketing and advertising",
}
DROP_NAME_RE = re.compile(r"\b(business cent(er|re)|coworking|co-working|realty|real estate)\b", re.I)

STATE_ABBR = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA",
    "kansas": "KS", "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV", "new hampshire": "NH",
    "new jersey": "NJ", "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
    "rhode island": "RI", "south carolina": "SC", "south dakota": "SD", "tennessee": "TN",
    "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}

# Title priority for one-per-company dedupe (higher wins).
def title_rank(t):
    t = (t or "").lower()
    if any(k in t for k in ("chief lending", "chief credit", "chief executive", "ceo",
                             "president", "owner", "founder", "chairman", "managing partner",
                             "managing director", "principal")):
        return 3
    if any(k in t for k in ("business development", "sba", "commercial lending",
                            "commercial banking")):
        return 2
    if any(k in t for k in ("evp", "svp", "vice president", "director")):
        return 1
    return 0


def get_service():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    with open(TOKEN_PATH) as f:
        td = json.load(f)
    creds = Credentials(token=td["token"], refresh_token=td["refresh_token"],
                        token_uri=td["token_uri"], client_id=td["client_id"],
                        client_secret=td["client_secret"],
                        scopes=td.get("scopes", ["https://www.googleapis.com/auth/spreadsheets"]))
    if creds.expired:
        creds.refresh(Request())
        td["token"] = creds.token
        with open(TOKEN_PATH, "w") as f:
            json.dump(td, f)
    return build("sheets", "v4", credentials=creds)


def sid_from_url(url):
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url)
    if not m:
        raise SystemExit(f"could not parse sheet id from {url}")
    return m.group(1)


def tab_for_gid(svc, sid, gid):
    meta = svc.spreadsheets().get(spreadsheetId=sid).execute()
    for sh in meta["sheets"]:
        if gid is None:
            return sh["properties"]["title"]
        if str(sh["properties"]["sheetId"]) == str(gid):
            return sh["properties"]["title"]
    return meta["sheets"][0]["properties"]["title"]


def parse_city_state(company_loc, person_loc):
    """'Dallas, Texas, United States' -> ('Dallas','TX')."""
    for loc in (company_loc, person_loc):
        if not loc:
            continue
        parts = [p.strip() for p in loc.split(",")]
        city = parts[0] if parts else ""
        st = ""
        for p in parts:
            if p.lower() in STATE_ABBR:
                st = STATE_ABBR[p.lower()]
            elif len(p) == 2 and p.upper() in STATE_ABBR.values():
                st = p.upper()
        if st:
            return city, st
    return "", ""


def create_master(svc, title):
    sid = svc.spreadsheets().create(body={"properties": {"title": title}},
                                    fields="spreadsheetId").execute()["spreadsheetId"]
    gid = svc.spreadsheets().get(spreadsheetId=sid).execute()["sheets"][0]["properties"]["sheetId"]
    svc.spreadsheets().batchUpdate(spreadsheetId=sid, body={"requests": [
        {"updateSheetProperties": {"properties": {"sheetId": gid, "title": TAB}, "fields": "title"}},
        {"updateSheetProperties": {"properties": {"sheetId": gid, "gridProperties": {
            "columnCount": len(HEADERS), "frozenRowCount": 1}}, "fields": "gridProperties(columnCount,frozenRowCount)"}},
        {"updateDimensionProperties": {"range": {"sheetId": gid, "dimension": "ROWS",
                                                 "startIndex": 0, "endIndex": 3000},
                                       "properties": {"pixelSize": 18}, "fields": "pixelSize"}},
        {"repeatCell": {"range": {"sheetId": gid, "startRowIndex": 0, "endRowIndex": 1},
                        "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                        "fields": "userEnteredFormat.textFormat.bold"}},
    ]}).execute()
    svc.spreadsheets().values().update(spreadsheetId=sid, range=f"'{TAB}'!A1",
                                       valueInputOption="RAW", body={"values": [HEADERS]}).execute()
    return sid


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src_url", default=None, help="Sales Nav export as a Google Sheet")
    ap.add_argument("--src_csv", default=None, help="Sales Nav export as a local CSV file")
    ap.add_argument("--src_gid", default=None)
    ap.add_argument("--state", required=True, help="state stamp, e.g. TX")
    ap.add_argument("--master_url", default=None, help="append to this master; omit to create one")
    ap.add_argument("--keep_all", action="store_true", help="keep every contact (skip one-per-company)")
    ap.add_argument("--dry_run", action="store_true")
    args = ap.parse_args()

    svc = get_service()
    if args.src_csv:
        import csv
        with open(args.src_csv, newline="", encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
    elif args.src_url:
        src = sid_from_url(args.src_url)
        tab = tab_for_gid(svc, src, args.src_gid)
        rows = svc.spreadsheets().values().get(spreadsheetId=src, range=f"'{tab}'!A1:CZ5000").execute().get("values", [])
    else:
        ap.error("need --src_url (Google Sheet) or --src_csv (local file)")
    hdr = rows[0]
    idx = {h: i for i, h in enumerate(hdr)}

    def g(r, name):
        i = idx.get(name)
        return (r[i].strip() if i is not None and i < len(r) and r[i] else "")

    kept, dropped = [], 0
    for r in rows[1:]:
        company = g(r, "companyName") or g(r, "currentPositions/0/companyName")
        if not company:
            dropped += 1; continue
        industry = g(r, "companyIndustry")
        if industry.lower() in DROP_INDUSTRY or DROP_NAME_RE.search(company):
            dropped += 1; continue
        title = g(r, "jobTitle") or g(r, "currentPositions/0/title")
        city, st = parse_city_state(g(r, "companyLocation"), g(r, "location"))
        cid = g(r, "companyId") or g(r, "currentPositions/0/companyId")
        company_li = f"https://www.linkedin.com/company/{cid}" if cid else ""
        kept.append({
            "company": company, "industry": industry,
            "first": g(r, "firstName"), "last": g(r, "lastName"),
            "name": g(r, "fullName") or f"{g(r,'firstName')} {g(r,'lastName')}".strip(),
            "title": title, "linkedin": g(r, "profileUrl"),
            "city": city, "state": st or args.state.upper(),
            "about": g(r, "about")[:1500], "snav": g(r, "salesNavigatorUrl"),
            "person_loc": g(r, "location"), "company_li": company_li,
        })

    if not args.keep_all:
        best = {}
        for k in kept:
            key = k["company"].lower()
            if key not in best or title_rank(k["title"]) > title_rank(best[key]["title"]):
                best[key] = k
        kept = list(best.values())

    print(f"Source: {len(rows)-1} rows -> {len(kept)} contacts kept ({dropped} dropped as off-target)"
          f"{'' if args.keep_all else ', one per company'}")

    # dedupe against master (by company + linkedin) if appending
    existing_companies = set()
    master_sid = None
    if args.master_url:
        master_sid = sid_from_url(args.master_url)
        mrows = svc.spreadsheets().values().get(spreadsheetId=master_sid, range=f"'{TAB}'!K2:K").execute().get("values", [])
        existing_companies = {(row[0].strip().lower() if row else "") for row in mrows}
        before = len(kept)
        kept = [k for k in kept if k["company"].lower() not in existing_companies]
        print(f"  master already has {len(existing_companies)} companies; {before-len(kept)} dupes skipped, {len(kept)} new")

    if args.dry_run:
        for k in kept[:15]:
            print(f"  {k['name']:28} | {k['title']:34} | {k['company']:32} | {k['city']},{k['state']}")
        print("Dry run — no sheet written.")
        return
    if not kept:
        print("Nothing to write."); return

    def row_vals(k):
        v = [""] * len(HEADERS)
        v[0] = f"salesnav:{args.state.upper()}"; v[1] = k["industry"]; v[2] = k["person_loc"]
        v[10] = k["company"]                      # K
        v[17] = k["city"]; v[18] = k["state"]     # R,S
        v[19] = k["name"]; v[20] = k["title"]; v[21] = k["linkedin"]  # T,U,V
        v[23] = k["first"]; v[24] = k["last"]     # X,Y
        v[28] = k["about"]; v[29] = k["snav"]     # AC,AD
        v[30] = k["company_li"]                    # AE
        return v

    if master_sid is None:
        title = f"Equipment Finance - Lenders (Sales Nav) - {date.today().isoformat()}"
        master_sid = create_master(svc, title)
        start_row = 2
        print(f"  Created master: https://docs.google.com/spreadsheets/d/{master_sid}/edit")
    else:
        existing = svc.spreadsheets().values().get(spreadsheetId=master_sid, range=f"'{TAB}'!A:A").execute().get("values", [])
        start_row = len(existing) + 1

    values = [row_vals(k) for k in kept]
    for s in range(0, len(values), 200):
        svc.spreadsheets().values().update(
            spreadsheetId=master_sid, range=f"'{TAB}'!A{start_row + s}",
            valueInputOption="RAW", body={"values": values[s:s + 200]}).execute()
    print(f"Wrote {len(values)} lender contacts. Sheet: "
          f"https://docs.google.com/spreadsheets/d/{master_sid}/edit")


if __name__ == "__main__":
    main()
