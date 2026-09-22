"""
LENDER side — export the fitted lender shortlist to a Google Sheet for the
BD/pitch motion (land ONE as the client). Small by design: the fitted universe
is a few dozen companies, so this is a pitch list, not a cold campaign.

Exports KEEP rows by default (pass --include_uncertain to add UNCERTAIN). Same
K/L/R/S/AB column anchors as the dealer sheet so apollo-dm-waterfall /
exa-website-enrichment run unmodified — but the DM target here is different:
VP Vendor Finance / Director of Originations / Head of Partnerships, or the
owner at a small independent lessor.

Usage:
  python3 -W ignore .claude/skills/equipment-finance-leads/scripts/export_lenders.py [--include_uncertain] [--dry_run]
"""
import argparse
import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from finance_common import get_db, load_settings, log_run

TOKEN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "..", "..", "..", "token.json")
TAB = "Leads"

HEADERS = [
    "Domain", "Source", "Fit", "Fit Reason", "Snippet",           # A-E
    "", "", "", "", "Notes",                                      # F-J
    "Company Name", "Company Website", "Company Size", "Revenue",  # K-N
    "CEO Name", "Company Description", "Benefits",                 # O-Q
    "City", "State",                                               # R-S
    "DM Name", "DM Title", "LinkedIn URL", "Email",                # T-W
    "First Name", "Last Name", "Email Body", "Added to Instantly", # X-AA
    "status",                                                      # AB
]


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


def create_sheet(svc, title, n_rows):
    sid = svc.spreadsheets().create(body={"properties": {"title": title}},
                                    fields="spreadsheetId").execute()["spreadsheetId"]
    gid = svc.spreadsheets().get(spreadsheetId=sid).execute()["sheets"][0]["properties"]["sheetId"]
    svc.spreadsheets().batchUpdate(spreadsheetId=sid, body={"requests": [
        {"updateSheetProperties": {"properties": {"sheetId": gid, "title": TAB}, "fields": "title"}},
        {"updateSheetProperties": {"properties": {"sheetId": gid, "gridProperties": {
            "rowCount": n_rows + 50, "columnCount": len(HEADERS), "frozenRowCount": 1}},
            "fields": "gridProperties(rowCount,columnCount,frozenRowCount)"}},
        {"updateDimensionProperties": {"range": {"sheetId": gid, "dimension": "ROWS",
                                                 "startIndex": 0, "endIndex": n_rows + 50},
                                       "properties": {"pixelSize": 18}, "fields": "pixelSize"}},
        {"repeatCell": {"range": {"sheetId": gid, "startRowIndex": 0, "endRowIndex": 1},
                        "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                        "fields": "userEnteredFormat.textFormat.bold"}},
    ]}).execute()
    svc.spreadsheets().values().update(
        spreadsheetId=sid, range=f"'{TAB}'!A1", valueInputOption="RAW",
        body={"values": [HEADERS]}).execute()
    print(f"  Created: https://docs.google.com/spreadsheets/d/{sid}/edit")
    return sid


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--include_uncertain", action="store_true")
    ap.add_argument("--dry_run", action="store_true")
    args = ap.parse_args()

    conn = get_db()
    fits = ("KEEP", "UNCERTAIN") if args.include_uncertain else ("KEEP",)
    rows = conn.execute(
        f"SELECT domain, name, website, source, fit, fit_reason, snippet "
        f"FROM lenders WHERE fit IN ({','.join('?' * len(fits))}) "
        f"AND exported_at IS NULL ORDER BY fit, name", fits).fetchall()

    print(f"Pool: {len(rows)} fitted lenders ({'/'.join(fits)})")
    if args.dry_run:
        for domain, name, _w, _s, fit, reason, _sn in rows:
            print(f"  [{fit}] {name} | {domain} | {reason}")
        print("Dry run — no sheet, nothing stamped.")
        return
    if not rows:
        print("Nothing to export (run apply_lender_fit.py first).")
        return

    svc = get_service()
    title = f"Equipment Finance - Lender Shortlist - {date.today().isoformat()}"
    sid = create_sheet(svc, title, len(rows))

    values = []
    for domain, name, website, source, fit, reason, snippet in rows:
        v = [""] * len(HEADERS)
        v[0], v[1], v[2], v[3], v[4] = domain, source or "", fit, reason or "", snippet or ""
        v[10], v[11] = name or "", domain          # K name, L website(domain)
        values.append(v)
    svc.spreadsheets().values().update(
        spreadsheetId=sid, range=f"'{TAB}'!A2", valueInputOption="RAW",
        body={"values": values}).execute()

    conn.executemany("UPDATE lenders SET exported_at=datetime('now') WHERE domain=?",
                     [(r[0],) for r in rows])
    conn.commit()
    log_run(conn, "export_lenders", f"{len(rows)} lenders -> {sid}")
    print(f"Exported {len(rows)} lenders and stamped.")


if __name__ == "__main__":
    main()
