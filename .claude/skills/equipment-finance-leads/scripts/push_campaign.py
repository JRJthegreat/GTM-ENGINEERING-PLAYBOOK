"""
Push the equipment-finance LENDER campaign to Instantly as a DRAFT.

The copy is pure {{firstName}} (Jude's approved minimal opener), so it lives
entirely in the SEQUENCE steps — there is no per-lead body/personalization to
generate. Leads just carry first name + email + company.

Connector D2C offer: surface borrower demand, no call/free/state in the email.
The call + free-first-intro live in the reply conversation, not the sequence.

4-step cadence, blank subjects on steps 2-4 so they thread under the first.
Signature is the SENDING ACCOUNT identity ({{sendingAccountFirstName}} +
Sent from my iPhone/iPad), matching Jude's current live campaigns — not a
hardcoded name.

MAILBOXES ATTACHED BY TAG + provider matching, read off Jude's most recent
campaign (Healthcare Texas Candidate - Aug 2026, 2026-09) and identical to the
HireBase set. Individual addresses never attached. Created as a DRAFT; Jude
activates.

Standing rules: text_only + first_email_text_only; one lead per unique inbox;
skip rows already AA=TRUE/BLOCKLISTED; blocklist 400 -> AA=BLOCKLISTED, never
retried. SKIP_BIG_BANK / SKIP_NOT_LENDER rows are excluded (true-ICP only).

Run:
  python3 -W ignore push_campaign.py --sheet_url URL \
    --campaign_name "Equipment Finance - Lenders - Sep 2026" [--limit 5] [--apply]
  # resume after a partial push:
  python3 -W ignore push_campaign.py --sheet_url URL --campaign_id CID --apply
"""
import os
import re
import argparse

import requests
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(SCRIPT_DIR, "..", "..", "..", ".env"))
TOKEN_PATH = os.path.join(SCRIPT_DIR, "..", "..", "..", "token.json")
INSTANTLY_KEY = os.getenv("INSTANTLY_API_KEY")
BASE = "https://api.instantly.ai/api/v2"

# equipment-finance lender master layout
C_COMPANY, C_EMAIL, C_FIRST, C_LAST, C_ADDED, C_STATUS = 10, 22, 23, 24, 26, 27
BATCH = 10

SUBJECT = "businesses looking for financing"
SIGN_IPHONE = "<br /><br />Best,<br />{{sendingAccountFirstName}}<br /><br />Sent from my iPhone"
SIGN_IPAD = "<br /><br />Best,<br />{{sendingAccountFirstName}}<br /><br />Sent from my iPad"

STEP1 = ("<div>Hi {{firstName}},<br /><br />"
         "I have business owners actively looking for financing.<br /><br />"
         "Would love to send some your way." + SIGN_IPHONE + "</div>")
STEP2 = ("<div>Hi {{firstName}},<br /><br />"
         "Still have business owners looking for financing.<br /><br />"
         "Happy to send a few your way." + SIGN_IPAD + "</div>")
STEP3 = ("<div>Hi {{firstName}},<br /><br />"
         "Chasing loan growth before year-end? I've got business owners "
         "ready to go." + SIGN_IPHONE + "</div>")
STEP4 = ("<div>Hi {{firstName}},<br /><br />"
         "Should I close this out?" + SIGN_IPAD + "</div>")

# Read off Jude's most recent campaign (2026-09) — identical to the HireBase set.
EMAIL_TAG_LIST = [
    "2b2adf27-cf48-4ed1-bcb4-513ecb49f719",
    "d221f400-cd05-4ca0-bf28-c5194227f701",   # ScaledMail-Google
    "d00f89d5-9a82-4602-8614-64a172de6424",   # ScaledMail-Microsoft
    "ce2014e8-b42f-415e-b5f3-c185093f2042",
    "ba2df4e6-a8a7-412d-833e-b6b48c5e12da",   # Zapmail
]
# ORDER MATTERS — catch-all first, verbatim from his current campaign.
PROVIDER_ROUTING_RULES = [
    {"action": "send", "recipient_esp": ["all"],     "sender_esp": ["google"]},
    {"action": "send", "recipient_esp": ["google"],  "sender_esp": ["google"]},
    {"action": "send", "recipient_esp": ["outlook"], "sender_esp": ["outlook"]},
]


def headers():
    return {"Authorization": f"Bearer {INSTANTLY_KEY}", "Content-Type": "application/json"}


def create_campaign(name):
    step = lambda d, subj, body: {
        "type": "email", "delay": d, "delay_unit": "days", "pre_delay_unit": "days",
        "variants": [{"subject": subj, "body": body}]}
    payload = {
        "name": name,
        "campaign_schedule": {"schedules": [{
            "name": "Default",
            "timing": {"from": "07:00", "to": "18:00"},
            "days": {"1": True, "2": True, "3": True, "4": True, "5": True, "6": True},
            "timezone": "America/Detroit",
        }]},
        "sequences": [{"steps": [
            step(1, SUBJECT, STEP1),
            step(2, "", STEP2),
            step(2, "", STEP3),
            step(2, "", STEP4),
        ]}],
        "daily_limit": 500,
        "stop_on_reply": True,
        "stop_on_auto_reply": False,
        "link_tracking": False,
        "open_tracking": False,
        "text_only": True,
        "first_email_text_only": True,
        "prioritize_new_leads": False,
        "stop_for_company": False,
        "insert_unsubscribe_header": False,
        "email_tag_list": EMAIL_TAG_LIST,
        "match_lead_esp": True,
        "provider_routing_rules": PROVIDER_ROUTING_RULES,
    }
    r = requests.post(f"{BASE}/campaigns", headers=headers(), json=payload, timeout=30)
    if r.status_code not in (200, 201):
        raise SystemExit(f"campaign create failed {r.status_code}: {r.text[:300]}")
    return r.json()["id"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet_url", required=True)
    ap.add_argument("--tab", default="Leads")
    ap.add_argument("--campaign_name")
    ap.add_argument("--campaign_id")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    if not INSTANTLY_KEY:
        raise SystemExit("INSTANTLY_API_KEY not set")
    svc = build("sheets", "v4", credentials=Credentials.from_authorized_user_file(TOKEN_PATH))
    sid = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", args.sheet_url).group(1)
    rows = svc.spreadsheets().values().get(
        spreadsheetId=sid, range=f"'{args.tab}'!A1:AE10000").execute().get("values", [])[1:]

    def c(r, i):
        return r[i].strip() if i < len(r) and r[i] else ""

    already = {c(r, C_EMAIL).lower() for r in rows if c(r, C_ADDED).upper() in ("TRUE", "BLOCKLISTED")}
    seen, queue = set(), []
    for i, r in enumerate(rows):
        email = c(r, C_EMAIL)
        if not email or not c(r, C_FIRST):
            continue
        if c(r, C_STATUS) in ("SKIP_BIG_BANK", "SKIP_NOT_LENDER"):
            continue
        if c(r, C_ADDED).upper() in ("TRUE", "BLOCKLISTED"):
            continue
        if email.lower() in seen or email.lower() in already:
            continue
        seen.add(email.lower())
        queue.append((i + 2, r))
    if args.limit:
        queue = queue[:args.limit]

    print(f"{len(queue)} true-ICP leads to push (one per unique inbox)")
    if not args.apply:
        for sr, r in queue[:6]:
            print(f"    {c(r, C_COMPANY)[:34]:34} {c(r, C_FIRST):12} <{c(r, C_EMAIL)}>")
        print("\n  (no --apply — no campaign created, nothing pushed)")
        return

    cid = args.campaign_id
    if not cid:
        if not args.campaign_name:
            raise SystemExit("--campaign_name required")
        cid = create_campaign(args.campaign_name)
        print(f"  DRAFT campaign created: {cid}")

    pending, pushed, blocked, failed = [], 0, 0, []

    def flush():
        nonlocal pending
        if pending:
            svc.spreadsheets().values().batchUpdate(
                spreadsheetId=sid, body={"valueInputOption": "RAW", "data": pending}).execute()
            pending = []

    for n, (sr, r) in enumerate(queue, 1):
        payload = {
            "campaign": cid, "email": c(r, C_EMAIL),
            "first_name": c(r, C_FIRST), "last_name": c(r, C_LAST),
            "company_name": c(r, C_COMPANY),
        }
        try:
            resp = requests.post(f"{BASE}/leads", headers=headers(), json=payload, timeout=30)
        except requests.RequestException as e:
            failed.append((sr, str(e)[:80])); continue
        if resp.status_code in (200, 201):
            pending.append({"range": f"'{args.tab}'!AA{sr}", "values": [["TRUE"]]}); pushed += 1
        elif resp.status_code == 400 and "blocklist" in resp.text.lower():
            pending.append({"range": f"'{args.tab}'!AA{sr}", "values": [["BLOCKLISTED"]]}); blocked += 1
        else:
            failed.append((sr, f"{resp.status_code}: {resp.text[:90]}"))
        if n % BATCH == 0:
            flush()
            print(f"  {n}/{len(queue)} | pushed {pushed} | blocked {blocked} | failed {len(failed)}")
    flush()
    print(f"\n=== pushed {pushed}, blocklisted {blocked}, failed {len(failed)} ===")
    print(f"  campaign {cid} is a DRAFT — review, then activate in the UI")
    for sr, err in failed[:8]:
        print(f"    row {sr}: {err}")


if __name__ == "__main__":
    main()
