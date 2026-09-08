"""
Phase 5 (AI Ark lane): load the Campaign-sheet leads into a DRAFT Instantly
campaign. No sending accounts attached - Jude activates in the UI.

Sending window: Asia/Taipei 12:00-17:00, Mon-Fri (Jude's timezone; lands
early-to-late afternoon AEST for the AU recipients).

Sequence (connector framework, Sherif unnamed until the interested-reply
handoff):
  Step 1  day 0  {{subject_line}}  body rides as {{personalization}} + SIG
  Step 2  day 2  blank subject     supermarket-supplier follow-up + SIG
  Step 3  day 5  blank subject     AASB S2 close + SIG
SIG lives in the sequence (renders per sending account), never in the body.

Campaign-sheet columns: A first B last C email D company E title F state
G seniority H industry I description J company_type K icebreaker L subject
M body N pushed.

Guards: one lead per unique inbox; blocklist rejections marked BLOCKLISTED and
never retried; resume-safe (skips TRUE / DUP / BLOCKLISTED). No domain guard -
the AI Ark email is BounceBan-verified and is the source of truth (there is no
separately resolved website to compare against).
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
HDRS = {"Authorization": f"Bearer {INSTANTLY_API_KEY}", "Content-Type": "application/json"}
TAB = "Campaign"
CAMPAIGN_NAME = "Energy GreenPrint - EMaaS Food & Bev - Aug 2026"

C_FIRST, C_LAST, C_EMAIL, C_COMPANY, C_TITLE, C_STATE = 0, 1, 2, 3, 4, 5
C_TYPE, C_SUBJECT, C_BODY, C_PUSHED = 9, 11, 12, 13

SIG = "<br><br>Best,<br>{{sendingAccountFirstName}}<br><br>Sent from my iPhone"
STEP2 = ("Hi {{firstName}},\n\nOne more reason I thought of you. The major "
         "supermarkets have started asking suppliers for emissions data, and "
         "it comes back to your energy use. The same audit that finds the "
         "savings also gives you clean, defensible numbers, so you have them "
         "ready when the request lands, instead of scrambling.\n\nHappy to make "
         "the introduction if it is worth a look.")
STEP3 = ("Hi {{firstName}},\n\nLast one from me. Mandatory climate reporting "
         "reaches businesses your size over the next couple of years, and the "
         "hard part is the energy data underneath it. Utility bills and meter "
         "reads do not hold up to scrutiny.\n\nThe monitoring he puts in "
         "produces that data as a by-product of cutting the bill. If it is not "
         "a priority right now, no problem at all. I will leave it there.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet_url", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry_run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--campaign_id", default=None)
    args = ap.parse_args()

    sid = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", args.sheet_url).group(1)
    svc = build("sheets", "v4",
                credentials=Credentials.from_authorized_user_file(TOKEN))
    vals = svc.spreadsheets().values().get(
        spreadsheetId=sid, range=f"{TAB}!A2:N2000").execute().get("values", [])

    def cell(r, i):
        return r[i].strip() if len(r) > i and r[i] else ""

    todo, seen = [], set()
    for idx, r in enumerate(vals, start=2):
        email = cell(r, C_EMAIL).lower()
        body, subject = cell(r, C_BODY), cell(r, C_SUBJECT)
        if not (email and body and subject):
            continue
        if cell(r, C_PUSHED).upper() in ("TRUE", "DUP", "BLOCKLISTED"):
            continue
        if email in seen:
            svc.spreadsheets().values().update(
                spreadsheetId=sid, range=f"{TAB}!N{idx}", valueInputOption="RAW",
                body={"values": [["DUP"]]}).execute()
            continue
        seen.add(email)
        todo.append({"row": idx, "email": email, "subject": subject, "body": body,
                     "first": cell(r, C_FIRST), "last": cell(r, C_LAST),
                     "company": cell(r, C_COMPANY), "title": cell(r, C_TITLE),
                     "state": cell(r, C_STATE), "ctype": cell(r, C_TYPE)})
    if args.limit:
        todo = todo[:args.limit]
    print(f"ready to push: {len(todo)}")

    if not args.apply or args.dry_run:
        print("dry run - no campaign, nothing pushed.")
        return

    cid = args.campaign_id
    if not cid:
        steps = [
            {"type": "email", "delay": 0, "variants": [
                {"subject": "{{subject_line}}",
                 "body": "<div>{{personalization}}" + SIG + "</div>"}]},
            {"type": "email", "delay": 2, "variants": [
                {"subject": "", "body": "<div>" + STEP2.replace("\n", "<br />") + SIG + "</div>"}]},
            {"type": "email", "delay": 5, "variants": [
                {"subject": "", "body": "<div>" + STEP3.replace("\n", "<br />") + SIG + "</div>"}]},
        ]
        payload = {
            "name": CAMPAIGN_NAME,
            "campaign_schedule": {"schedules": [{
                "name": "Taipei afternoon",
                "timing": {"from": "12:00", "to": "17:00"},
                "days": {"1": True, "2": True, "3": True, "4": True, "5": True},
                "timezone": "Asia/Taipei",
            }]},
            "sequences": [{"steps": steps}],
            "text_only": True,
            "first_email_text_only": True,
            "daily_limit": 500,
            "stop_on_reply": True,
            "link_tracking": False,
            "open_tracking": False,
            # Sending accounts attach BY TAG, never individually (Jude, revised
            # 2026-08-20). This set + the all->google-first routing were read
            # off his latest live campaign (Pipeline Intro, Sep 2026). The tag
            # set DRIFTS - re-read it from GET /campaigns on his most recent
            # campaign before each new push rather than trusting these ids.
            "match_lead_esp": True,
            "email_tag_list": [
                "2b2adf27-cf48-4ed1-bcb4-513ecb49f719",
                "ce2014e8-b42f-415e-b5f3-c185093f2042",
                "d221f400-cd05-4ca0-bf28-c5194227f701",
                "d00f89d5-9a82-4602-8614-64a172de6424",
                "ba2df4e6-a8a7-412d-833e-b6b48c5e12da",
            ],
            "provider_routing_rules": [
                {"action": "send", "recipient_esp": ["all"], "sender_esp": ["google"]},
                {"action": "send", "recipient_esp": ["google"], "sender_esp": ["google"]},
                {"action": "send", "recipient_esp": ["outlook"], "sender_esp": ["outlook"]},
            ],
        }
        resp = requests.post(f"{API}/campaigns", headers=HDRS, json=payload, timeout=60)
        if resp.status_code not in (200, 201):
            print("campaign create failed:", resp.status_code, resp.text[:400])
            return
        cid = resp.json().get("id")
        print(f"DRAFT campaign created: {cid}")

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
            "campaign": cid, "email": t["email"],
            "first_name": t["first"], "last_name": t["last"],
            "company_name": t["company"], "personalization": t["body"],
            "custom_variables": {"subject_line": t["subject"],
                                 "dm_title": t["title"], "site_state": t["state"],
                                 "company_type": t["ctype"]},
        }
        r = requests.post(f"{API}/leads", headers=HDRS, json=lead, timeout=60)
        if r.status_code in (200, 201):
            pushed += 1
            pending.append({"range": f"{TAB}!N{t['row']}", "values": [["TRUE"]]})
        elif "blocklist" in r.text.lower():
            blocked += 1
            pending.append({"range": f"{TAB}!N{t['row']}", "values": [["BLOCKLISTED"]]})
        else:
            print(f"  [!] {t['company']}: {r.status_code} {r.text[:120]}")
        if n % 10 == 0:
            flush()
            print(f"  {n}/{len(todo)} pushed={pushed} blocked={blocked}", flush=True)
        time.sleep(0.15)
    flush()
    print(f"\nDone. pushed {pushed}, blocklisted {blocked}, campaign {cid}")
    print("DRAFT, Asia/Taipei 12:00-17:00, no sending accounts. Activate in the UI.")


if __name__ == "__main__":
    sys.exit(main())
