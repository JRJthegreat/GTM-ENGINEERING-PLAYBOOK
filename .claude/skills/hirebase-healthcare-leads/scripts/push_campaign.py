"""
Phase 6 — create the DRAFT Instantly campaign and push one lead per COMPANY.

Cadence and shape cloned from push_florida_demand.py, the closest proven
analogue (same niche, same connector offer): 4 steps at day 0 / +2 / +3 / +4,
BLANK subjects on steps 2-4 so they thread under the first as replies, and
alternating iPad/iPhone signatures.

ONE CHANGE TO THAT LADDER. Florida's steps 2-4 are pure bumps, which worked
because its step 1 carried the identity line AND the placement proof. Jude cut
both to keep this opener short, so without a change this sequence would carry
no credibility anywhere in it. The real, historical placement story therefore
moves into STEP 3, where a bare bump is weakest. Steps 2 and 4 stay exactly as
proven — the step-4 breakup consistently beats another chase.

ONE LEAD PER COMPANY. The sheet holds one row per JOB and a company can appear
in BOTH lanes, so pushing rows would email the same person several times over.
Only rows marked `push_owner == YES` are eligible (generate_bodies.py marks
exactly one lane per company), and within that the first row per company wins.
Jude's hard reaction to the two-emails-from-me case is the reason this guard
exists.

NO SENDING ACCOUNTS ARE ATTACHED — Jude configures mailboxes in the UI. The
campaign is created as a DRAFT and he activates it.

Standing rules enforced: text_only + first_email_text_only; the body carries no
sign-off (the SEQUENCE owns it, so including one would sign twice); custom
values go in `custom_variables` (nesting under payload is silently dropped);
duplicate inboxes are skipped in-run and against any row already AA=TRUE; a
blocklist 400 is marked AA=BLOCKLISTED and never retried.

Run:
  python3 -W ignore push_campaign.py --sheet_url URL --tab "General Campaign" \
    --campaign_name "HireBase General - Aug 2026" [--limit 5] [--apply]
"""

import os
import re
import sys
import time
import argparse
import importlib.util

import requests
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(SCRIPT_DIR, "..", "..", "..", ".env"))
TOKEN_PATH = os.path.join(SCRIPT_DIR, "..", "..", "..", "token.json")
INSTANTLY_KEY = os.getenv("INSTANTLY_API_KEY")
BASE = "https://api.instantly.ai/api/v2"

C_TITLE, C_COMPANY, C_WEBSITE = 1, 10, 11
C_DM, C_DM_TITLE, C_EMAIL, C_FIRST, C_LAST = 19, 20, 22, 23, 24
C_BODY, C_ADDED, C_STATUS = 25, 26, 27
C_SEG, C_BAND, C_CITY = 48, 49, 17
C_PUSH = 53
BATCH = 10

SUBJECT = "{{firstName}}, still hiring?"
SIGN_IPAD = ("<br /><br />Best,<br />{{sendingAccountFirstName}}"
             "<br /><br />Sent from my iPad")
SIGN_IPHONE = ("<br /><br />Best,<br />{{sendingAccountFirstName}}"
               "<br /><br />Sent from my iPhone")

STEP1 = "<div>{{personalization}}" + SIGN_IPAD + "</div>"
STEP2 = ("<div>Hi {{firstName}},<br /><br />"
         "Just bumping this.<br />"
         "{{q_open}}" + SIGN_IPHONE + "</div>")
# The one addition to Florida's ladder: a real, historical placement. Nothing
# the reader is invited to do can falsify it, unlike a claim about candidates
# being available right now.
STEP3 = ("<div>Hi {{firstName}},<br /><br />"
         "Recently a clinic had an NP role open for 45 days. I introduced them "
         "to one of the recruiters in my network and it was filled in a week."
         "<br /><br />"
         "Happy to do the same here if you are still looking." + SIGN_IPAD + "</div>")
STEP4 = ("<div>Hi {{firstName}},<br /><br />"
         "Going to close this out. If it opens back up, reach out and I'll "
         "make the intro." + SIGN_IPHONE + "</div>")


def headers():
    return {"Authorization": f"Bearer {INSTANTLY_KEY}",
            "Content-Type": "application/json"}


def sheet_id_of(url):
    return re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url).group(1)


def load_gen():
    spec = importlib.util.spec_from_file_location(
        "gb", os.path.join(SCRIPT_DIR, "generate_bodies.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def create_campaign(name):
    step = lambda d, subj, body: {
        "type": "email", "delay": d, "delay_unit": "days",
        "pre_delay_unit": "days",
        "variants": [{"subject": subj, "body": body}]}
    payload = {
        "name": name,
        "campaign_schedule": {"schedules": [{
            "name": "Default",
            "timing": {"from": "07:00", "to": "18:00"},
            # Saturday ON: measured 0.88% vs 0.32% on Indiana + Texas, and all
            # three interested replies landed Friday-Sunday. Sunday stays off.
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
        # NO email_tag_list / sending accounts — Jude attaches mailboxes in
        # the UI. Adding them here is a standing prohibition.
    }
    r = requests.post(f"{BASE}/campaigns", headers=headers(),
                      json=payload, timeout=30)
    if r.status_code not in (200, 201):
        raise SystemExit(f"campaign create failed {r.status_code}: {r.text[:300]}")
    return r.json()["id"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet_url", required=True)
    ap.add_argument("--tab", required=True)
    ap.add_argument("--campaign_name")
    ap.add_argument("--campaign_id")
    ap.add_argument("--title_tabs", nargs="+",
                    default=["SLP Campaign", "General Campaign"],
                    help="Tabs to aggregate job titles from when building "
                         "{{q_open}}/{{roles}}. Must match the tabs the BODY "
                         "was merged across, or the follow-up contradicts "
                         "the first email.")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    if not INSTANTLY_KEY:
        raise SystemExit("INSTANTLY_API_KEY not set")
    G = load_gen()

    svc = build("sheets", "v4",
                credentials=Credentials.from_authorized_user_file(TOKEN_PATH))
    sid = sheet_id_of(args.sheet_url)
    vals = svc.spreadsheets().values().get(
        spreadsheetId=sid, range=f"'{args.tab}'!A1:BB").execute().get("values", [])
    rows = vals[1:]

    def c(r, i):
        return r[i].strip() if i < len(r) else ""

    # one lead per COMPANY, from the lane that owns it
    seen_co, seen_mail, queue = set(), set(), []
    already = {c(r, C_EMAIL).lower() for r in rows
               if c(r, C_ADDED).upper() in ("TRUE", "BLOCKLISTED")}
    # Titles come from EVERY lane, not just this one. The body was merged
    # across lanes, so deriving the follow-up question from a single tab would
    # have step 2 asking about a narrower set of roles than step 1 named.
    titles = {}
    for t in args.title_tabs:
        tv = svc.spreadsheets().values().get(
            spreadsheetId=sid, range=f"'{t}'!A1:BB").execute().get("values", [])
        for r in tv[1:]:
            if c(r, C_COMPANY) and c(r, C_STATUS) == "KEEP":
                titles.setdefault(c(r, C_COMPANY), []).append(c(r, C_TITLE))
    for i, r in enumerate(rows):
        co, email = c(r, C_COMPANY), c(r, C_EMAIL)
        if c(r, C_PUSH) != "YES" or not c(r, C_BODY) or not email:
            continue
        if c(r, C_ADDED).upper() in ("TRUE", "BLOCKLISTED"):
            continue
        if co in seen_co or email.lower() in seen_mail or email.lower() in already:
            continue
        seen_co.add(co)
        seen_mail.add(email.lower())
        queue.append((i + 2, r, co))
    if args.limit:
        queue = queue[: args.limit]

    print(f"[{args.tab}] {len(queue)} leads (one per company)")
    if not args.apply:
        for sr, r, co in queue[:6]:
            print(f"    {co[:32]:32s} {c(r, C_FIRST):12s} <{c(r, C_EMAIL)}>")
        print("\n  (no --apply — no campaign created, nothing pushed)")
        return

    cid = args.campaign_id
    if not cid:
        if not args.campaign_name:
            raise SystemExit("--campaign_name required to create a campaign")
        cid = create_campaign(args.campaign_name)
        print(f"  DRAFT campaign created: {cid}")

    pending, pushed, blocked, failed = [], 0, 0, []

    def flush():
        nonlocal pending
        if pending:
            svc.spreadsheets().values().batchUpdate(
                spreadsheetId=sid,
                body={"valueInputOption": "RAW", "data": pending}).execute()
            pending = []

    for n, (sr, r, co) in enumerate(queue, 1):
        # the follow-up question must agree in number with what step 1 named
        ts = titles.get(co, [])
        roles, nfam = G.render_roles(ts, want_count=True)
        if nfam == 1 and len(ts) == 1:
            dom = G.dominant(ts)
            q_open = f"Is the {dom[0]} role still open?" if dom else "Is the role still open?"
        else:
            q_open = "Are those roles still open?"
        payload = {
            "campaign": cid,
            "email": c(r, C_EMAIL),
            "first_name": c(r, C_FIRST),
            "last_name": c(r, C_LAST),
            "company_name": co,
            "website": c(r, C_WEBSITE),
            "personalization": c(r, C_BODY),
            "custom_variables": {
                "q_open": q_open,
                "roles": roles,
                "dm_title": c(r, C_DM_TITLE),
                "segment": c(r, C_SEG),
                "size_band": c(r, C_BAND),
                "city": c(r, C_CITY),
                "company_website": c(r, C_WEBSITE),
            },
        }
        try:
            resp = requests.post(f"{BASE}/leads", headers=headers(),
                                 json=payload, timeout=30)
        except requests.RequestException as e:
            failed.append((sr, str(e)[:80]))
            continue
        if resp.status_code in (200, 201):
            pending.append({"range": f"'{args.tab}'!AA{sr}",
                            "values": [["TRUE"]]})
            pushed += 1
        elif resp.status_code == 400 and "blocklist" in resp.text.lower():
            pending.append({"range": f"'{args.tab}'!AA{sr}",
                            "values": [["BLOCKLISTED"]]})
            blocked += 1
        else:
            failed.append((sr, f"{resp.status_code}: {resp.text[:90]}"))
        if n % BATCH == 0:
            flush()
            print(f"  {n}/{len(queue)} | pushed {pushed} | blocked {blocked} "
                  f"| failed {len(failed)}")
    flush()
    print(f"\n=== {args.tab}: pushed {pushed}, blocklisted {blocked}, "
          f"failed {len(failed)} ===")
    print(f"  campaign {cid} is a DRAFT — attach mailboxes and activate in the UI")
    for sr, err in failed[:8]:
        print(f"    row {sr}: {err}")


if __name__ == "__main__":
    main()
