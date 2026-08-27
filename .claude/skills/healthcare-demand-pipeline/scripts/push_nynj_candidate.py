"""
Phase 5 - create the NY-NJ candidate-angle campaign (Aug 2026) and push leads.

CLONE of push_nynj_candidate.py for sheet "Healthcare NY-NJ Indeed Leads -
Aug 2026" (tab: Leads). Differences from the Texas parent, both Jude-approved
2026-08-27: step 3's proof story is STATE-NEUTRAL (the story happened in
Texas, and copy claims are historical-only, so the location is dropped rather
than relocated), and the schedule runs Eastern time.

Conventions (Jude's latest, mirroring hirebase-healthcare-leads Aug 2026):
- DRAFT campaign; activation is a separate explicit step.
- Mailboxes attached BY TAG at create time + match_lead_esp + ORDERED
  provider_routing_rules. The tag set DRIFTS, so it is read at runtime from
  Jude's most recent campaign that carries one (GET /campaigns), never
  hardcoded. Fallback: the hirebase Aug 2026 set.
- Sequence day 0/+2/+3/+4, blank subjects on 2-4 so they thread. Saturday ON
  (measured 0.88% vs 0.32%; interested replies land Fri-Sun). Sunday off.
- Step 3 carries the identity + proof story (Jude's July copy verbatim) —
  the hirebase lesson: a short opener needs it restored in the thread.
- text_only + first_email_text_only, tracking off, stop_on_reply,
  daily_limit 500, timezone America/Chicago.
- One lead per unique inbox; AA=TRUE pushed, BLOCKLISTED never retried,
  HELD manually excluded. Sign-off lives in the SEQUENCE, never the body.
- LARGE 500+ rows are INCLUDED, matching the Texas candidate campaign
  (same test posture). Read replies BY SIZE BAND.

Usage:
  python3 -W ignore push_nynj_candidate.py --sheet_url "URL" --dry_run
  python3 -W ignore push_nynj_candidate.py --sheet_url "URL"
  python3 -W ignore push_nynj_candidate.py --sheet_url "URL" --campaign_id ID
"""

import os
import re
import time
import argparse

import requests
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from urllib.parse import urlparse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(SCRIPT_DIR, "..", "..", "..", ".env"))
TOKEN_PATH = os.path.join(SCRIPT_DIR, "..", "..", "..", "token.json")

INSTANTLY_KEY = os.getenv("INSTANTLY_API_KEY")
BASE = "https://api.instantly.ai/api/v2"
BATCH = 10

# --- Column indices (0-based), tab "Leads", Aug 2026 sheet.
C_JOBID, C_TITLE, C_COMPANY, C_SIZE = 0, 1, 10, 12
C_CITY = 17
C_DM_TITLE, C_EMAIL, C_FIRST, C_LAST = 20, 22, 23, 24
C_BODY, C_ADDED, C_STATUS, C_INDEED = 25, 26, 27, 28
C_CROLE, C_RPLUR, C_RSHORT, C_CFIRST, C_GSTAT = 29, 30, 31, 32, 33
C_WEBSITE = 11

SUBJECT = "{{firstName}}, the {{cleaned_role}} hire"

SIGN_IPAD = ("<br /><br />Best,<br />{{sendingAccountFirstName}}"
             "<br /><br />Sent from my iPad")
SIGN_IPHONE = ("<br /><br />Best,<br />{{sendingAccountFirstName}}"
               "<br /><br />Sent from my iPhone")

# Steps 2-4 approved by Jude 2026-08-27; step 3 is the July proof story
# verbatim. NO em dashes, no per-body sign-off.
STEP1 = "<div>{{personalization}}" + SIGN_IPAD + "</div>"
STEP2 = ("<div>Hi {{firstName}},<br /><br />"
         "Floating this back up. Is the {{cleaned_role}} role still open?"
         + SIGN_IPHONE + "</div>")
STEP3 = ("<div>Hi {{firstName}},<br /><br />"
         "My name is Rood. I connect healthcare employers with "
         "recruiters that can actually deliver.<br /><br />"
         "Recently a clinic had an NP role sitting for 45 days. "
         "I introduced them to one of the recruiters in my network and it was "
         "filled in a week.<br /><br />"
         "He only works on contingent, so nothing is owed unless the hire "
         "sticks. Want me to make the intro?" + SIGN_IPAD + "</div>")
STEP4 = ("<div>Hi {{firstName}},<br /><br />"
         "If the role's been filled or this isn't a priority right now, no "
         "worries at all. If it ever makes sense later, the intro is a two "
         "minute email." + SIGN_IPHONE + "</div>")

# Fallback only — the live set is read off Jude's most recent campaign.
FALLBACK_TAGS = [
    "2b2adf27-cf48-4ed1-bcb4-513ecb49f719",
    "d221f400-cd05-4ca0-bf28-c5194227f701",   # ScaledMail-Google
    "d00f89d5-9a82-4602-8614-64a172de6424",   # ScaledMail-Microsoft
    "ce2014e8-b42f-415e-b5f3-c185093f2042",
    "ba2df4e6-a8a7-412d-833e-b6b48c5e12da",   # Zapmail
]
FALLBACK_ROUTING = [
    {"action": "send", "recipient_esp": ["all"],     "sender_esp": ["google"]},
    {"action": "send", "recipient_esp": ["google"],  "sender_esp": ["google"]},
    {"action": "send", "recipient_esp": ["outlook"], "sender_esp": ["outlook"]},
]


def headers():
    return {"Authorization": f"Bearer {INSTANTLY_KEY}",
            "Content-Type": "application/json"}


def root_domain(host):
    host = (host or "").lower().strip()
    if not host:
        return ""
    if host.startswith("http"):
        host = urlparse(host).netloc
    host = host[4:] if host.startswith("www.") else host
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def latest_tag_config():
    """Copy email_tag_list / provider_routing_rules off the most recent
    campaign that has tags. The tag set drifts; scripts must not freeze it."""
    try:
        r = requests.get(f"{BASE}/campaigns",
                         headers=headers(), params={"limit": 50}, timeout=30)
        r.raise_for_status()
        camps = r.json().get("items", [])
        camps.sort(key=lambda c: c.get("timestamp_created", ""), reverse=True)
        for c in camps:
            if c.get("email_tag_list"):
                print(f"tag config copied from: {c.get('name')!r} "
                      f"({len(c['email_tag_list'])} tags)")
                return (c["email_tag_list"],
                        c.get("provider_routing_rules") or FALLBACK_ROUTING,
                        c.get("match_lead_esp", True))
    except Exception as e:
        print(f"  [!] tag lookup failed ({type(e).__name__}), using fallback set")
    return FALLBACK_TAGS, FALLBACK_ROUTING, True


def create_campaign(name):
    tags, routing, match_esp = latest_tag_config()
    step = lambda d, subj, body: {
        "type": "email", "delay": d, "delay_unit": "days",
        "pre_delay_unit": "days",
        "variants": [{"subject": subj, "body": body}]}
    payload = {
        "name": name,
        "campaign_schedule": {"schedules": [{
            "name": "Default",
            "timing": {"from": "07:00", "to": "18:00"},
            # Saturday ON (0.88% vs 0.32% measured); Sunday off.
            "days": {"1": True, "2": True, "3": True, "4": True, "5": True,
                     "6": True},
            # Instantly rejects "America/New_York"; Detroit is its ET entry.
            "timezone": "America/Detroit",
        }]},
        "sequences": [{"steps": [
            step(2, SUBJECT, STEP1),
            step(1, "", STEP2),
            step(1, "", STEP3),
            step(1, "", STEP4),
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
        "email_tag_list": tags,
        "match_lead_esp": match_esp,
        "provider_routing_rules": routing,
    }
    r = requests.post(f"{BASE}/campaigns", headers=headers(),
                      json=payload, timeout=30)
    if r.status_code not in (200, 201):
        raise SystemExit(f"campaign create failed {r.status_code}: {r.text[:300]}")
    return r.json()["id"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet_url", required=True)
    ap.add_argument("--tab", default="Leads")
    ap.add_argument("--campaign_name",
                    default="Healthcare NY-NJ Candidate - Aug 2026")
    ap.add_argument("--campaign_id", default="", help="resume into existing campaign")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry_run", action="store_true")
    args = ap.parse_args()
    TAB = args.tab

    sheet_id = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", args.sheet_url).group(1)
    creds = Credentials.from_authorized_user_file(TOKEN_PATH)
    svc = build("sheets", "v4", credentials=creds)
    rows = svc.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=f"'{TAB}'!A1:AH").execute().get("values", [])
    data = rows[1:]

    def cell(r, i):
        return r[i].strip() if i < len(r) and r[i] else ""

    seen = {cell(r, C_EMAIL).lower() for r in data
            if cell(r, C_ADDED).upper() == "TRUE" and cell(r, C_EMAIL)}

    todo, dupes, mismatch = [], 0, []
    for i, r in enumerate(data):
        if args.limit and len(todo) >= args.limit:
            break
        if cell(r, C_ADDED).upper() in ("TRUE", "BLOCKLISTED", "HELD"):
            continue
        email, body = cell(r, C_EMAIL), cell(r, C_BODY)
        if not email or not body:
            continue
        if email.lower() in seen:
            dupes += 1
            continue
        wd = root_domain(cell(r, C_WEBSITE))
        ed = root_domain(email.split("@")[-1])
        if wd and ed and wd != ed:
            mismatch.append((i + 2, cell(r, C_COMPANY), email, wd))
        seen.add(email.lower())
        todo.append((i + 2, r))

    print(f"tab={TAB}")
    print(f"to push : {len(todo)}")
    print(f"dupes   : {dupes}")
    print(f"domain mismatches: {len(mismatch)} (pushed anyway; most are "
          f"corporate-parent domains)")
    for row_n, comp, em, wd in mismatch[:10]:
        print(f"    row {row_n}: {comp} | {em} | site {wd}")
    if args.dry_run:
        print("\n--- DRY RUN, nothing created or pushed ---")
        print(f"would create campaign: {args.campaign_name!r}")
        return

    cid = args.campaign_id or create_campaign(args.campaign_name)
    if not args.campaign_id:
        print(f"\ncreated campaign {cid} ({args.campaign_name!r}) — "
              f"tags attached, NOT active until explicitly activated")

    pushed, blocked, failed = 0, 0, []
    pending = []

    def flush():
        nonlocal pending
        if pending:
            svc.spreadsheets().values().batchUpdate(
                spreadsheetId=sheet_id,
                body={"valueInputOption": "RAW", "data": pending}).execute()
            pending = []

    for n, (row_n, r) in enumerate(todo, 1):
        job_id = cell(r, C_JOBID)
        payload = {
            "campaign": cid,
            "email": cell(r, C_EMAIL),
            "first_name": cell(r, C_CFIRST) or cell(r, C_FIRST),
            "last_name": cell(r, C_LAST),
            "company_name": cell(r, C_COMPANY),
            "website": cell(r, C_WEBSITE),
            "personalization": cell(r, C_BODY),
            "custom_variables": {
                "cleaned_role": cell(r, C_CROLE),
                "role_plural": cell(r, C_RPLUR),
                "role_short_plural": cell(r, C_RSHORT),
                "city": cell(r, C_CITY),
                "job_title": cell(r, C_TITLE),
                "dm_title": cell(r, C_DM_TITLE),
                "company_website": cell(r, C_WEBSITE),
                "size_band": cell(r, C_SIZE),
                "job_post_url": (cell(r, C_INDEED) or
                                 (f"https://www.indeed.com/viewjob?jk={job_id}"
                                  if job_id else "")),
            },
        }
        try:
            resp = requests.post(f"{BASE}/leads", headers=headers(),
                                 json=payload, timeout=30)
        except requests.RequestException as e:
            failed.append((row_n, str(e)[:80]))
            continue
        if resp.status_code == 200:
            pending.append({"range": f"'{TAB}'!AA{row_n}", "values": [["TRUE"]]})
            pushed += 1
        elif resp.status_code == 400 and "blocklist" in resp.text.lower():
            pending.append({"range": f"'{TAB}'!AA{row_n}",
                            "values": [["BLOCKLISTED"]]})
            blocked += 1
        else:
            failed.append((row_n, f"{resp.status_code}: {resp.text[:80]}"))
        if n % BATCH == 0:
            flush()
            print(f"  -- {n}/{len(todo)} | pushed {pushed} | "
                  f"blocklisted {blocked} | failed {len(failed)}")
        time.sleep(0.15)
    flush()

    print("\n=== Summary ===")
    print(f"  Campaign:     {cid}")
    print(f"  Pushed:       {pushed}")
    print(f"  Blocklisted:  {blocked}")
    print(f"  Dupes:        {dupes}")
    print(f"  Failed:       {len(failed)}")
    for f in failed[:10]:
        print("   ", f)


if __name__ == "__main__":
    main()
