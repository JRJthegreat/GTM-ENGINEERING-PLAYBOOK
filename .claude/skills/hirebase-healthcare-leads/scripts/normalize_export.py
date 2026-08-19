"""
Phase 1 — normalize a raw HireBase job export tab into the repo's 29-col
campaign schema, as its own lane tab.

WHY THIS SKILL IS SEPARATE (Jude, 2026-08-19)
HireBase is a different PLATFORM being tested, not another source for the
Indeed pipeline, so this lane does NOT reuse `healthcare-demand-pipeline`.
The data arrives far richer than an Indeed scrape — company website is already
present on 464 of 468 companies and company LinkedIn on 468 of 468 — which
changes the whole enrichment shape downstream (see resolve_domains.py: the
expensive Google-search domain resolution is a LAST resort here, not phase
1.9). Built 2026-08-19 for the "Healthcare US - Aug 19th" sheet, which runs
TWO lanes: Speech Language Pathologist and General Healthcare.

TWO TRAPS THIS SCRIPT EXISTS TO HANDLE (both hit the Aug 19th sheet):

1. COLUMN LETTERS ARE NOT STABLE ACROSS TABS. HireBase flattens JSON arrays,
   so a tab with more `benefits/*` or `services/*` entries pushes every later
   field right. On the Aug 19th sheet `companyName` is BL on the SLP tab and
   CE on the General Healthcare tab. NEVER address these exports by letter —
   this script resolves every field by HEADER NAME.

2. STACKED EXPORTS WITH AN EMBEDDED HEADER ROW. The SLP tab is two separate
   exports pasted together: rows 2-149 match the row-1 header (151 cols), then
   row 150 is a SECOND header (306 cols) and rows 151+ match that one. Read
   against the top header alone, those 343 rows are silently misaligned
   (companyName reads blank, `country` reads "In-Person", `jobTitle` holds the
   whole job description). This script detects any row that repeats a header
   signature, starts a new block there, and maps each block with its own
   header. Blocks are then merged and deduped.

DEDUPE (Jude, 2026-08-19): dedupe by JOB, never by company.
  - The key is `applicationLink` — the per-job posting URL.
  - It is NOT `jobBoardLink`. Despite the name, `jobBoardLink` is the company's
    board ROOT (job-boards.greenhouse.io/wovencare). Measured on this sheet:
    432 jobBoardLinks map to 432 companies and none maps to more than one, so
    deduping on it collapses the list to one row per company — exactly the
    company dedupe Jude ruled out. It is carried through to col AR as a
    company key instead.
  - A company keeps as many rows as it has distinct live postings. Whether the
    Instantly push is one lead per company or one per job is a Phase 4
    decision, deliberately not made here.

STANDING RULES ENFORCED (both already satisfied by the Aug 19th export, kept
so a re-export cannot quietly violate them):
  - 35-day freshness window, applied FIRST (Jude, 2026-07-31).
  - 500-employee cap on the LOWER bound of the size range. Blank size is KEPT.

OUTPUT LAYOUT (29-col base, A-AC):
  A-J job, K-S company+location, T-AA outreach (blank, filled downstream),
  AB dm_status (blank — apollo-dm-waterfall / exa-website-enrichment default),
  AC Job URL (Jude's rule: AC always holds the job posting URL).
  AD-AM are left BLANK and RESERVED for the generation audit trail that
  generate_healthcare_demand.py and its clones write.
  AN-AS carry HireBase-only extras that would otherwise be lost.

K/L/R/S/AB land company/website/city/state/status where
`exa-website-enrichment` and `apollo-dm-waterfall` expect them by default, so
both run against this tab unmodified.

Nothing is filtered on company type here — no agency/job-board classification
and no deletions. Suspects are REPORTED at the end for a separate call.

Run:
  python3 -W ignore normalize_hirebase_export.py \
    --sheet_url "URL" --src_tab "General Healthcare" --out_tab "General Campaign" \
    [--cutoff_days 35] [--max_employees 500] [--dry_run]
"""

import re
import os
import argparse
from datetime import date, timedelta

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_PATH = os.path.join(SCRIPT_DIR, "..", "..", "..", "token.json")

# Header names that mark a row as a repeated header rather than data.
HEADER_SIGNATURE = {"applicationLink", "id", "companyName", "jobTitle"}

HEADERS = [
    # Job info (A-J)
    "Job_Id", "Job Title", "Job Type", "Occupations", "Date Published",
    "Salary Min", "Salary Max", "Salary Period", "Apply URL", "Job Description",
    # Company (K-Q)
    "Company Name", "Company Website", "Company Size", "Revenue", "CEO Name",
    "Company Description", "Benefits",
    # Location (R-S)
    "City", "State",
    # Outreach (T-AA) — blank, filled by downstream phases
    "DM Name", "DM Title", "LinkedIn URL", "Email",
    "First Name", "Last Name", "Email Body", "Added to Instantly",
    # Status + job URL (AB-AC)
    "dm_status", "Job URL",
    # AD-AM reserved for the generation audit trail — left blank on purpose
    "", "", "", "", "", "", "", "", "", "",
    # HireBase extras (AN-AS)
    "Openings @ Company", "Company LinkedIn", "Location Type",
    "Job Board", "Job Board Link", "Source Tab",
    # Enrichment status (AT-AV) — AT/AU by resolve_domains.py,
    # AV by apply_classification.py
    "domain_status", "domain_note", "company_status",
]


def sheet_id_of(url):
    return re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url).group(1)


def serial_to_iso(v):
    """HireBase dates come back as Google serial numbers under
    UNFORMATTED_VALUE. Accept serials, ISO strings, or blank."""
    s = str(v).strip()
    if not s:
        return ""
    try:
        return (date(1899, 12, 30) + timedelta(days=int(float(s)))).isoformat()
    except (ValueError, OverflowError):
        m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
        return m.group(1) if m else ""


def parse_size_lower_bound(size_str):
    """Lower bound of a '51 to 200' range, matching process_city_scrape.py.
    Deliberate: a '201 to 500' org is under a 500 cap."""
    if not size_str:
        return None
    s = str(size_str).strip().replace(",", "").replace("+", "")
    s = re.sub(r"\s+to\s+", "-", s, flags=re.IGNORECASE)
    s = re.sub(r"[^\d\-].*$", "", s).strip()
    if not s:
        return None
    try:
        return int(s.split("-")[0])
    except (ValueError, IndexError):
        return None


US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana",
    "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
    "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio",
    "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "PR": "Puerto Rico",
}

# A leading street address, so "99 N La Cienega Blvd Suite 306 Beverly Hills"
# yields "Beverly Hills" rather than a house number.
STREET_RE = re.compile(
    r"^\d+[\w\s\.\-#]*?\b(?:blvd|boulevard|ave|avenue|st|street|rd|road|dr|drive|"
    r"ln|lane|way|pkwy|parkway|hwy|highway|ct|court|pl|place|ste|suite|unit|"
    r"fl|floor|apt)\b\.?\s*(?:#?\s*[\w\-]+)?\s*", re.IGNORECASE)

NOT_A_CITY_RE = re.compile(
    r"\b(hospital|clinic|center|centre|campus|medical|health|building|"
    r"suite|floor|dept|department)\b", re.IGNORECASE)


def derive_city_state(get):
    """locations/0 is authoritative; fall back to parsing the `location`
    display string, which is populated on every row that lacks a structured
    city. Blank beats wrong — an unparseable value returns empty."""
    city = get("locations/0/city")
    state = get("locations/0/region")
    loc = get("location")

    if not city and loc and "," in loc:
        cand = STREET_RE.sub("", loc.split(",")[0]).strip(" .-")
        if (cand and not cand[0].isdigit() and len(cand) <= 40
                and not NOT_A_CITY_RE.search(cand)
                and re.fullmatch(r"[A-Za-z][A-Za-z .'\-]*", cand)):
            city = cand

    if not state and loc:
        parts = [p.strip() for p in loc.split(",")]
        for p in parts[1:]:
            tok = p.split()[0].upper() if p.split() else ""
            if tok in US_STATES:
                state = US_STATES[tok]
                break
            if p.title() in US_STATES.values():
                state = p.title()
                break
    elif state and state.upper() in US_STATES:
        state = US_STATES[state.upper()]

    return city, state


def split_blocks(values):
    """Split a raw tab into (header, rows) blocks, starting a new block at any
    row that repeats a header signature. Handles stacked exports."""
    blocks, header, rows = [], values[0], []
    for row in values[1:]:
        cells = {str(c).strip() for c in row[:6]}
        if cells & HEADER_SIGNATURE:
            if rows:
                blocks.append((header, rows))
            header, rows = row, []
        else:
            rows.append(row)
    if rows:
        blocks.append((header, rows))
    return blocks


def joined(get, prefix, limit=40, sep=" | "):
    """Collapse a flattened array (benefits/0, benefits/1, ...) into one cell."""
    parts = []
    for i in range(limit):
        v = get(f"{prefix}/{i}")
        if v:
            parts.append(v)
    return sep.join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet_url", required=True)
    ap.add_argument("--src_tab", required=True)
    ap.add_argument("--out_tab", required=True)
    ap.add_argument("--cutoff_days", type=int, default=35)
    ap.add_argument("--max_employees", type=int, default=500,
                    help="Drop employers above this LOWER-bound headcount. "
                         "0 disables. Blank size is always kept.")
    ap.add_argument("--dedupe_key", default="applicationLink",
                    help="Per-JOB key. Do not set this to jobBoardLink — that "
                         "is a company key and collapses the list.")
    ap.add_argument("--dry_run", action="store_true")
    args = ap.parse_args()

    sid = sheet_id_of(args.sheet_url)
    creds = Credentials.from_authorized_user_file(TOKEN_PATH)
    svc = build("sheets", "v4", credentials=creds)

    raw = svc.spreadsheets().values().get(
        spreadsheetId=sid, range=f"'{args.src_tab}'!A1:ZZ",
        valueRenderOption="UNFORMATTED_VALUE").execute().get("values", [])
    if not raw:
        raise SystemExit(f"No data on tab {args.src_tab!r}")

    blocks = split_blocks(raw)
    print(f"[{args.src_tab}] {len(raw) - 1} raw rows in {len(blocks)} export block(s)")
    for i, (hdr, rows) in enumerate(blocks, 1):
        print(f"  block {i}: {len(rows)} rows, {len(hdr)} header cols")

    records = []
    for hdr, rows in blocks:
        idx = {h: i for i, h in enumerate(hdr)}
        for row in rows:
            if not any(str(c).strip() for c in row):
                continue

            def get(name, _row=row, _idx=idx):
                i = _idx.get(name)
                if i is None or i >= len(_row):
                    return ""
                return str(_row[i]).strip()

            records.append(get)

    print(f"  -> {len(records)} data rows after block merge")

    # 1. Freshness window FIRST — hard rule, before dedupe or anything else.
    cutoff = (date.today() - timedelta(days=args.cutoff_days)).isoformat()
    fresh, stale = [], []
    for g in records:
        d = serial_to_iso(g("datePosted"))
        (fresh if (not d or d >= cutoff) else stale).append((g, d))
    print(f"  freshness ({args.cutoff_days}d, cutoff {cutoff}): "
          f"kept {len(fresh)}, dropped {len(stale)}")

    # 2. Size cap on the LOWER bound. Blank size is kept.
    capped, toobig = [], []
    for g, d in fresh:
        lo = parse_size_lower_bound(g("companyData/size_range/min"))
        if args.max_employees and lo is not None and lo > args.max_employees:
            toobig.append((g, d))
        else:
            capped.append((g, d))
    print(f"  size cap (<={args.max_employees}): kept {len(capped)}, "
          f"dropped {len(toobig)}")
    if toobig:
        names = sorted({g("companyName") for g, _ in toobig if g("companyName")})
        print(f"    sample dropped: {names[:8]}")

    # 3. Dedupe by JOB. Never by company.
    seen, deduped, dupes = set(), [], 0
    for g, d in capped:
        k = g(args.dedupe_key) or g("id") or g("jobSlug")
        if k in seen:
            dupes += 1
            continue
        seen.add(k)
        deduped.append((g, d))
    print(f"  dedupe on {args.dedupe_key}: kept {len(deduped)}, removed {dupes}")

    # Openings per company, computed AFTER dedupe so it counts real postings.
    counts = {}
    for g, _ in deduped:
        c = g("companyName") or g("jobBoardLink")
        counts[c] = counts.get(c, 0) + 1

    out_rows = []
    for g, d in deduped:
        smin = g("companyData/size_range/min")
        smax = g("companyData/size_range/max")
        size = f"{smin} to {smax}" if smin and smax else (smin or "")
        company = g("companyName")
        job_url = g("applicationLink")
        city, state = derive_city_state(g)
        out_rows.append([
            g("id"), g("jobTitle"), g("jobType"), g("jobCategories/0"), d,
            g("salaryRange/min"), g("salaryRange/max"), g("salaryRange/period"),
            job_url, g("descriptionText")[:45000],
            company, g("companyWebsite"), size, "", "",
            g("companyData/description_summary")[:5000],
            joined(g, "benefits")[:5000],
            city, state,
            "", "", "", "", "", "", "", "",
            "", job_url,
            "", "", "", "", "", "", "", "", "", "",
            counts.get(company or g("jobBoardLink"), 1),
            g("companyData/linkedin_link"), g("locationType"),
            g("jobBoard"), g("jobBoardLink"), args.src_tab,
            "", "", "",
        ])

    companies = {r[10] for r in out_rows if r[10]}
    print(f"\n  RESULT: {len(out_rows)} job rows / {len(companies)} companies")
    print(f"  blank website rows: {sum(1 for r in out_rows if not r[11])}")
    print(f"  blank city rows:    {sum(1 for r in out_rows if not r[17])}")

    if args.dry_run:
        print("\n  --dry_run: nothing written. Sample rows:")
        for r in out_rows[:3]:
            print(f"    {r[10]!r} | {r[1][:50]!r} | {r[17]}, {r[18]} | "
                  f"size={r[12]!r} | site={r[11]!r}")
        return

    # Create or clear the destination tab.
    meta = svc.spreadsheets().get(spreadsheetId=sid).execute()
    existing = {s["properties"]["title"]: s["properties"]["sheetId"]
                for s in meta["sheets"]}
    if args.out_tab in existing:
        tab_id = existing[args.out_tab]
        svc.spreadsheets().values().clear(
            spreadsheetId=sid, range=f"'{args.out_tab}'").execute()
    else:
        resp = svc.spreadsheets().batchUpdate(
            spreadsheetId=sid, body={"requests": [{"addSheet": {"properties": {
                "title": args.out_tab,
                "gridProperties": {"rowCount": max(len(out_rows) + 200, 2000),
                                   "columnCount": len(HEADERS)},
            }}}]}).execute()
        tab_id = resp["replies"][0]["addSheet"]["properties"]["sheetId"]

    # Pin 18px rows (Jude's standing rule — multi-line cells blow rows up).
    svc.spreadsheets().batchUpdate(spreadsheetId=sid, body={"requests": [
        {"updateSheetProperties": {
            "properties": {"sheetId": tab_id, "gridProperties": {
                "rowCount": max(len(out_rows) + 200, 2000),
                "columnCount": len(HEADERS)}},
            "fields": "gridProperties.rowCount,gridProperties.columnCount"}},
        {"updateDimensionProperties": {
            "range": {"sheetId": tab_id, "dimension": "ROWS",
                      "startIndex": 0, "endIndex": max(len(out_rows) + 200, 2000)},
            "properties": {"pixelSize": 18}, "fields": "pixelSize"}},
        {"updateSheetProperties": {
            "properties": {"sheetId": tab_id, "gridProperties": {
                "frozenRowCount": 1}},
            "fields": "gridProperties.frozenRowCount"}},
    ]}).execute()

    svc.spreadsheets().values().update(
        spreadsheetId=sid, range=f"'{args.out_tab}'!A1",
        valueInputOption="RAW", body={"values": [HEADERS]}).execute()

    CHUNK = 500
    for i in range(0, len(out_rows), CHUNK):
        svc.spreadsheets().values().update(
            spreadsheetId=sid, range=f"'{args.out_tab}'!A{i + 2}",
            valueInputOption="RAW",
            body={"values": out_rows[i:i + CHUNK]}).execute()
        print(f"  written {min(i + CHUNK, len(out_rows))}/{len(out_rows)}")

    print(f"\n  DONE -> tab {args.out_tab!r}")


if __name__ == "__main__":
    main()
