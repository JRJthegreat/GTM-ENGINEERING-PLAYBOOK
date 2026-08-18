"""
Phase 5: push enriched EMaaS leads into a DRAFT Instantly campaign.

Creates the campaign as a DRAFT and never adds sending accounts — Jude wires
those up in the UI and activates. Text-only (text_only + first_email_text_only),
because those two flags are what actually make Instantly send plain text.

Sequence, connector framework throughout (Jude is the sender, the offer is an
introduction to Sherif):
  Step 1  day 0  {{subject_line}}   per-lead body rides as {{personalization}}
  Step 2  day 2  blank subject      generic bump, threads under step 1
  Step 3  day 5  blank subject      AASB S2 angle, strictly measurement-layer

Steps 2 and 3 carry NO per-lead body. Same house pattern as the NPPES connector
and production retarget campaigns: personalise the opener, keep the follow-ups
generic, so a copy revision does not mean regenerating 272 rows.

Guards:
  * one lead per unique inbox; siblings marked DUP and never pushed
  * refuses any row whose email domain does not match the resolved website
  * rows Instantly rejects for the workspace blocklist are marked BLOCKLISTED
    in col AA and never retried
  * resume-safe: skips rows already marked TRUE, DUP or BLOCKLISTED

Usage:
  python3 -W ignore push_emaas_campaign.py --sheet_url URL --dry_run
  python3 -W ignore push_emaas_campaign.py --sheet_url URL --apply
"""

import argparse
import os
import re
import sys
import time

import requests
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(SCRIPT_DIR, "..", "..", "..", ".env"))
INSTANTLY_API_KEY = os.environ["INSTANTLY_API_KEY"]
TOKEN = os.path.join(SCRIPT_DIR, "..", "..", "..", "token.json")

API = "https://api.instantly.ai/api/v2"
HDRS = {"Authorization": f"Bearer {INSTANTLY_API_KEY}",
        "Content-Type": "application/json"}
TAB = "Leads"
CAMPAIGN_NAME = "Energy GreenPrint - EMaaS - Aug 2026"

C_COMPANY, C_WEB = 10, 11
C_CITY, C_STATE = 17, 18
C_DMTITLE, C_EMAIL, C_FIRST, C_LAST = 20, 22, 23, 24
C_BODY, C_PUSHED = 25, 26
C_SUBJECT = 29

# Signature lives in the SEQUENCE so it renders per sending account, never in
# the per-lead personalization body (Jude, 2026-08-04 standing rule). The rule
# is "no sign-off in the generated body", NOT "no signature anywhere" — the
# first pass of this script dropped it from both and the emails went out bare.
SIG = "<br><br>Best,<br>{{sendingAccountFirstName}}<br><br>Sent from my iPhone"

STEP2 = (
    "Hi {{firstName}},\n\n"
    "Just checking this reached you. The audit costs nothing and takes about "
    "two weeks, and it either finds savings worth chasing or it does not.\n\n"
    "Happy to make the introduction if it is useful."
)

# AASB S2 framing is deliberately narrow: Sherif's guardrail is that Energy
# GreenPrint is the MEASUREMENT LAYER feeding a disclosure, never a compliance
# or carbon-accounting product. Do not loosen this wording.
STEP3 = (
    "Hi {{firstName}},\n\n"
    "Last one from me. Mandatory climate reporting is reaching mid-size "
    "Australian businesses now, and the part that catches people out is the "
    "energy data itself: utility bills and annual meter reads are not built "
    "to survive an auditor.\n\n"
    "The same monitoring that cuts the bill produces that data as a "
    "by-product. If it is not a priority right now, no problem at all, I will "
    "leave it there."
)


def norm_domain(w):
    d = re.sub(r"^https?://", "", (w or "").strip().lower())
    return re.sub(r"^www\.", "", d).split("/")[0]


def domain_root(d):
    parts = [p for p in norm_domain(d).split(".")
             if p not in ("com", "net", "org", "au", "co", "gov", "edu")]
    return parts[-1] if parts else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet_url", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry_run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--campaign_id", default=None,
                    help="push into an existing campaign instead of creating one")
    args = ap.parse_args()

    sid = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", args.sheet_url).group(1)
    svc = build("sheets", "v4",
                credentials=Credentials.from_authorized_user_file(TOKEN))
    vals = svc.spreadsheets().values().get(
        spreadsheetId=sid, range=f"{TAB}!A1:AD2000").execute().get("values", [])

    def cell(r, i):
        return r[i].strip() if len(r) > i and r[i] else ""

    todo, seen_inbox, refused = [], set(), []
    for idx, r in enumerate(vals[1:], start=2):
        email = cell(r, C_EMAIL).lower()
        body, subject = cell(r, C_BODY), cell(r, C_SUBJECT)
        pushed = cell(r, C_PUSHED).upper()
        if not (email and body and subject):
            continue
        if pushed in ("TRUE", "DUP", "BLOCKLISTED"):
            continue
        # Containment, not equality — same rule as the waterfall's guard.
        # An org routinely mails from a shorter or longer form of its own
        # domain: boandik.org.au vs boandiklodge.org.au, dahallco vs dahall.
        want, got = domain_root(cell(r, C_WEB)), domain_root(email.split("@")[-1])
        if want and got and want != got and want not in got and got not in want:
            refused.append((idx, cell(r, C_COMPANY), email, "domain_mismatch"))
            continue
        if email in seen_inbox:
            refused.append((idx, cell(r, C_COMPANY), email, "DUP"))
            continue
        seen_inbox.add(email)
        todo.append({
            "row": idx, "email": email, "subject": subject, "body": body,
            "first": cell(r, C_FIRST), "last": cell(r, C_LAST),
            "company": cell(r, C_COMPANY), "title": cell(r, C_DMTITLE),
            "city": cell(r, C_CITY), "state": cell(r, C_STATE),
        })
    if args.limit:
        todo = todo[:args.limit]

    print(f"ready to push: {len(todo)}")
    print(f"refused:       {len(refused)} "
          f"({sum(1 for x in refused if x[3] == 'DUP')} duplicate inbox, "
          f"{sum(1 for x in refused if x[3] == 'domain_mismatch')} domain mismatch)")
    for x in refused[:8]:
        print(f"   row {x[0]} {x[1][:34]:34s} {x[2]:38s} {x[3]}")

    if not args.apply or args.dry_run:
        print("\ndry run — no campaign created, nothing pushed.")
        for t in todo[:2]:
            print(f"\n--- {t['company']} ({t['title']})\nSUBJECT: {t['subject']}\n{t['body']}")
        return

    # --- campaign ---
    cid = args.campaign_id
    if not cid:
        seq_steps = [
            {"type": "email", "delay": 0, "variants": [
                {"subject": "{{subject_line}}",
                 "body": "<div>{{personalization}}" + SIG + "</div>"}]},
            {"type": "email", "delay": 2, "variants": [
                {"subject": "",
                 "body": "<div>" + STEP2.replace("\n", "<br />") + SIG + "</div>"}]},
            {"type": "email", "delay": 5, "variants": [
                {"subject": "",
                 "body": "<div>" + STEP3.replace("\n", "<br />") + SIG + "</div>"}]},
        ]
        payload = {
            "name": CAMPAIGN_NAME,
            "campaign_schedule": {"schedules": [{
                "name": "AU business hours",
                "timing": {"from": "09:00", "to": "17:00"},
                "days": {"1": True, "2": True, "3": True, "4": True, "5": True},
                "timezone": "Australia/Melbourne",
            }]},
            "sequences": [{"steps": seq_steps}],
            "text_only": True,
            "first_email_text_only": True,
            "daily_limit": 100,
        }
        resp = requests.post(f"{API}/campaigns", headers=HDRS, json=payload, timeout=60)
        if resp.status_code not in (200, 201):
            print("campaign create failed:", resp.status_code, resp.text[:400])
            return
        cid = resp.json().get("id")
        print(f"DRAFT campaign created: {cid}  ({CAMPAIGN_NAME})")

    pushed = blocked = 0
    pending = []

    def flush():
        if pending:
            svc.spreadsheets().values().batchUpdate(
                spreadsheetId=sid,
                body={"valueInputOption": "RAW", "data": list(pending)}).execute()
            pending.clear()

    for n, t in enumerate(todo, 1):
        lead = {
            "campaign": cid,
            "email": t["email"],
            "first_name": t["first"],
            "last_name": t["last"],
            "company_name": t["company"],
            "personalization": t["body"],
            "custom_variables": {
                "subject_line": t["subject"],
                "dm_title": t["title"],
                "site_city": t["city"],
                "site_state": t["state"],
            },
        }
        r = requests.post(f"{API}/leads", headers=HDRS, json=lead, timeout=60)
        if r.status_code in (200, 201):
            pushed += 1
            pending.append({"range": f"{TAB}!AA{t['row']}", "values": [["TRUE"]]})
        elif "blocklist" in r.text.lower():
            blocked += 1
            pending.append({"range": f"{TAB}!AA{t['row']}", "values": [["BLOCKLISTED"]]})
        else:
            print(f"  [!] {t['company']}: {r.status_code} {r.text[:120]}")
        if n % 10 == 0:
            flush()
            print(f"  {n}/{len(todo)} pushed={pushed} blocked={blocked}", flush=True)
        time.sleep(0.15)
    flush()

    for idx, company, email, why in refused:
        if why == "DUP":
            svc.spreadsheets().values().update(
                spreadsheetId=sid, range=f"{TAB}!AA{idx}",
                valueInputOption="RAW", body={"values": [["DUP"]]}).execute()

    print(f"\nDone. pushed {pushed}, blocklisted {blocked}, campaign {cid}")
    print("Campaign is a DRAFT with no sending accounts. Activate in the UI.")


if __name__ == "__main__":
    sys.exit(main())
