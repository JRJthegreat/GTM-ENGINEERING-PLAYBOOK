"""
Create the healthcare recruitment-firm "PIPELINE INTRO" campaign in Instantly
(DRAFT) and push valid, KEEP, never-contacted leads one at a time.

Sept 2026 clone of push_expansion_campaign.py (per repo convention copy scripts
are cloned, never parameterized; do NOT edit push_expansion_campaign.py or
push_demand_campaign.py — they belong to completed campaigns).

WHAT IS DIFFERENT FROM THE EXPANSION CLONE
------------------------------------------
1. COPY. Jude's new offer (approved in-session 2026-09-08): "I've got healthcare
   employers hiring, I'm not a recruiter myself, recently connected an Indiana
   recruiter with 7 employers, I'll introduce you to 2 in my pipeline, worth 15
   min?" Subject is value-forward, not the May-25th compliment subject. 3
   follow-ups (2/2/5) reworked to this offer. Body is STATIC (only {first}).

2. SENDING ACCOUNTS ATTACHED BY TAG. The expansion script predates the
   2026-08-20 by-tag rule and attached nothing. This reproduces the live
   settings verified off the HireBase + Texas Candidate campaigns
   (GET /campaigns): 5 email tags, match_lead_esp, provider_routing_rules with
   the catch-all FIRST. Individual addresses are still never attached. Campaign
   is still a DRAFT; Jude activates it.

3. LIVE INSTANTLY FRESHNESS GATE. col W on this sheet is known-stale (420 rows
   were contacted in OTHER campaigns — May 25th Supply, Jul 21 Retargeting — but
   never marked here). So instead of trusting col W alone, this paginates every
   lead already in the Instantly workspace and refuses to upload any email that
   already exists ANYWHERE. That, not col W, is the real guard against a
   double-send. Both tabs are processed in one run for global de-dupe.

TARGET ROW (all must hold):
  S == "found"  AND  Z == "KEEP"  AND  email(Q)  AND  first_name(T)
  AND col W not TRUE/BLOCKLISTED
  AND email not already in the Instantly workspace
  AND email not a duplicate within this run (across both tabs)
  AND (website blank OR email-domain root == website-domain root)   # scramble guard

Schema (both tabs, A-based):
  A:Company H:Website I:Co-LinkedIn P:dm_title Q:dm_email R:dm_linkedin
  S:email_status T:first U:last V:email_body W:added_to_instantly Z:outreach_flag

Standing rules enforced: text_only + first_email_text_only; NO sign-off in the
per-lead body (the SEQUENCE owns the signature); duplicate inboxes skipped;
blocklist 400 -> W=BLOCKLISTED, never retried.

Run (dry-run is the default; nothing is created or written without --apply):
  python3 -W ignore push_pipeline_intro_campaign.py --sheet_url URL \
      --campaign_name "Healthcare Recruitment Firms - Pipeline Intro - Sep 2026" \
      [--limit N] [--apply]
"""

import os
import re
import sys
import json
import time
import argparse
import requests
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
ENV_PATH   = os.path.join(SCRIPT_DIR, "..", "..", "..", ".env")
TOKEN_PATH = os.path.join(SCRIPT_DIR, "..", "..", "..", "token.json")
load_dotenv(ENV_PATH)

INSTANTLY_API_KEY = os.getenv("INSTANTLY_API_KEY")
BASE = "https://api.instantly.ai/api/v2"
BATCH_SIZE = 10

try:
    from generate_expansion_body import casual_company
except Exception:
    def casual_company(name):
        return name

TABS = ["1-50 EMP", "50-200 EMP"]

COL_COMPANY, COL_WEBSITE, COL_CO_LINKEDIN = 0, 7, 8
COL_DM_NAME = 14                                   # O — full "First Last"
COL_DM_TITLE, COL_DM_EMAIL, COL_DM_LINKEDIN = 15, 16, 17
COL_EMAIL_STATUS, COL_FIRST, COL_LAST = 18, 19, 20
COL_BODY, COL_ADDED, COL_OUTREACH_FLAG = 21, 22, 25

# ---- Copy (Jude approved 2026-09-08). Body is plain text; only {first}. ----
BODY = (
    "Hi {first},\n\n"
    "I've got healthcare employers actively hiring right now, and since I'm not "
    "a recruiter myself, I pass them to specialist firms instead of filling the "
    "roles.\n\n"
    "Recently, I connected a recruiter in Indiana with 7 healthcare employers "
    "looking to hire. I am confident I can do the same for you. The offer is "
    "simple: I'll introduce you to 2 employers currently in my pipeline, no "
    "strings attached. If it's valuable, then we can talk about working together "
    "long term. The only thing I need from you is 15 minutes so I can learn the "
    "specifics around the roles and organizations you like to work with.\n\n"
    "Worth the 15-min chat?"
)

SUBJECT = "2 healthcare employers hiring"

# Signature lives at SEQUENCE level, never in the per-lead body (Jude
# 2026-08-05). Jude's personal connector persona, same as the expansion lane.
SIGNATURE = ("<div><br /></div><div>Best regards,</div>"
             "<div>Rood Judeley (Jude)</div>")
IPHONE = "<div><br /></div><div>Sent from my iPhone</div>"

STEP1_BODY = "<div>{{personalization}}</div>" + SIGNATURE + IPHONE

FU1 = ("<div>Hi {{firstName}},</div><div><br /></div>"
       "<div>Circling back on this. Those 2 employer intros are still open, I'd "
       "just need 15 minutes to make sure the ones I send actually fit the roles "
       "you focus on.</div><div><br /></div>"
       "<div>Worth a quick chat this week?</div>" + SIGNATURE + IPHONE)

FU2 = ("<div>Hi {{firstName}},</div><div><br /></div>"
       "<div>Not sure if this hit at a busy time. The offer still stands: 2 "
       "healthcare employers from my pipeline, no strings, once I know what "
       "you're recruiting for.</div><div><br /></div>"
       "<div>Want to send me a couple of times that work?</div>" + SIGNATURE + IPHONE)

FU3 = ("<div>Hi {{firstName}},</div><div><br /></div>"
       "<div>Last one from me. I'll assume the timing isn't right and hold "
       "off.</div><div><br /></div>"
       "<div>If you want those healthcare employer intros down the line, just "
       "reply here and I'll pick it back up.</div>" + SIGNATURE + IPHONE)

# Live settings verified 2026-09-08 off HireBase + Texas Candidate campaigns.
EMAIL_TAG_LIST = [
    "2b2adf27-cf48-4ed1-bcb4-513ecb49f719",
    "d221f400-cd05-4ca0-bf28-c5194227f701",   # ScaledMail-Google
    "d00f89d5-9a82-4602-8614-64a172de6424",   # ScaledMail-Microsoft
    "ce2014e8-b42f-415e-b5f3-c185093f2042",
    "ba2df4e6-a8a7-412d-833e-b6b48c5e12da",   # Zapmail
]
PROVIDER_ROUTING_RULES = [
    {"action": "send", "recipient_esp": ["all"],     "sender_esp": ["google"]},
    {"action": "send", "recipient_esp": ["google"],  "sender_esp": ["google"]},
    {"action": "send", "recipient_esp": ["outlook"], "sender_esp": ["outlook"]},
]


def headers():
    return {"Authorization": f"Bearer {INSTANTLY_API_KEY}", "Content-Type": "application/json"}


def cell(row, idx):
    return row[idx].strip() if idx < len(row) and row[idx] else ""


def col_letter(idx):
    result, idx = "", idx + 1
    while idx:
        idx, rem = divmod(idx - 1, 26)
        result = chr(65 + rem) + result
    return result


_TITLE_RE = re.compile(r"^(dr|mr|mrs|ms|miss|prof|mx)\.?\s+", re.I)


def _norm_case(tok):
    return tok.capitalize() if (tok.isupper() or tok.islower()) else tok


def split_name(raw):
    """Deterministic First/Last from a 'First Last' string. Strips titles and
    normalizes shouting ('SARAH' -> 'Sarah'). First names only in the copy, so
    a first-token split is sufficient; last is metadata."""
    raw = _TITLE_RE.sub("", (raw or "").strip())
    toks = raw.split()
    if not toks:
        return "", ""
    first = _norm_case(toks[0])
    last = " ".join(toks[1:])
    if last and (last.isupper() or last.islower()):
        last = last.title()
    return first, last


def get_service():
    with open(TOKEN_PATH) as f:
        td = json.load(f)
    creds = Credentials(
        token=td["token"], refresh_token=td["refresh_token"],
        token_uri=td["token_uri"], client_id=td["client_id"],
        client_secret=td["client_secret"],
        scopes=td.get("scopes", ["https://www.googleapis.com/auth/spreadsheets"]))
    if creds.expired:
        creds.refresh(Request())
        td["token"] = creds.token
        with open(TOKEN_PATH, "w") as f:
            json.dump(td, f)
    return build("sheets", "v4", credentials=creds)


def parse_sheet_id(url):
    return re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url).group(1)


def root_domain(domain):
    parts = domain.lower().strip().split(".")
    two = {"co", "com", "org", "net", "gov", "edu", "ac"}
    if len(parts) >= 3 and parts[-2] in two:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else domain


def extract_domain(url):
    if not url:
        return ""
    d = re.sub(r"^https?://(www\.)?", "", url.strip()).split("/")[0].split("?")[0].lower()
    return d if "." in d else ""


def fetch_instantly_emails():
    """Every email already in the workspace (all campaigns)."""
    present, cursor, pages = set(), None, 0
    while True:
        payload = {"limit": 100}
        if cursor:
            payload["starting_after"] = cursor
        r = requests.post(f"{BASE}/leads/list", headers=headers(), json=payload, timeout=60)
        r.raise_for_status()
        d = r.json()
        for it in d.get("items", []):
            em = (it.get("email") or "").strip().lower()
            if em:
                present.add(em)
        pages += 1
        cursor = d.get("next_starting_after")
        if not cursor or not d.get("items"):
            break
    print(f"  Instantly workspace: {len(present)} distinct emails ({pages} pages)")
    return present


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
            step(2, SUBJECT, STEP1_BODY),
            step(2, "", FU1),
            step(2, "", FU2),
            step(5, "", FU3),
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
        raise SystemExit(f"campaign create failed {r.status_code}: {r.text[:400]}")
    return r.json()["id"]


def build_queue(svc, sid, contacted):
    seen, queue, skip_dom, skip_dupe = set(), [], 0, 0
    for tab in TABS:
        rows = svc.spreadsheets().values().get(
            spreadsheetId=sid, range=f"'{tab}'!A:Z").execute().get("values", [])[1:]
        for i, r in enumerate(rows):
            if cell(r, COL_EMAIL_STATUS).lower() != "found":
                continue
            if cell(r, COL_OUTREACH_FLAG).upper() != "KEEP":
                continue
            if cell(r, COL_ADDED).upper() in ("TRUE", "BLOCKLISTED"):
                continue
            email = cell(r, COL_DM_EMAIL)
            # first name: prefer the split col T, fall back to full name in col O
            t_first = cell(r, COL_FIRST)
            if t_first:
                first, _lsplit = split_name(t_first)
                last = cell(r, COL_LAST) or _lsplit
                write_name = False
            else:
                first, last = split_name(cell(r, COL_DM_NAME))
                write_name = True   # backfill T/U so the sheet is complete
            if not email or not first:
                continue
            el = email.lower()
            if el in contacted:
                continue
            if el in seen:
                skip_dupe += 1
                continue
            wd = root_domain(extract_domain(cell(r, COL_WEBSITE)))
            ed = root_domain(email.split("@")[-1]) if "@" in email else ""
            if wd and wd != ed:
                skip_dom += 1
                continue
            seen.add(el)
            body = BODY.format(first=first)
            queue.append({
                "tab": tab, "row": i + 2, "body": body,
                "first": first, "last": last, "write_name": write_name,
                "payload": {
                    "email": email,
                    "first_name": first,
                    "last_name": last,
                    "company_name": casual_company(cell(r, COL_COMPANY)),
                    "website": cell(r, COL_WEBSITE),
                    "personalization": body,
                    "custom_variables": {
                        "job_title": cell(r, COL_DM_TITLE),
                        "dm_linkedin": cell(r, COL_DM_LINKEDIN),
                        "company_linkedin": cell(r, COL_CO_LINKEDIN),
                    },
                },
            })
    return queue, skip_dom, skip_dupe


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet_url", required=True)
    ap.add_argument("--campaign_name", required=True)
    ap.add_argument("--campaign_id", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    if not INSTANTLY_API_KEY:
        raise SystemExit("INSTANTLY_API_KEY not set")

    svc = get_service()
    sid = parse_sheet_id(args.sheet_url)

    print("Fetching Instantly workspace emails (freshness gate)...")
    contacted = fetch_instantly_emails()
    queue, skip_dom, skip_dupe = build_queue(svc, sid, contacted)
    if args.limit:
        queue = queue[: args.limit]

    from collections import Counter
    by_tab = Counter(q["tab"] for q in queue)
    print(f"\nTarget leads: {len(queue)}  {dict(by_tab)}")
    print(f"  skipped (domain mismatch): {skip_dom} | (dupe email in run): {skip_dupe}")
    for q in queue[:4]:
        print(f"    [{q['tab']}] {q['payload']['company_name'][:30]:30s} "
              f"{q['payload']['first_name']:12s} <{q['payload']['email']}>")

    if not args.apply:
        print("\n[DRY RUN] no col V written, no campaign created, nothing pushed. Pass --apply.")
        return

    # 1. write col V (body), and backfill T/U where the name was derived from O
    print("\nWriting email bodies to col V (and backfilling names to T/U)...")
    data = []
    for q in queue:
        tab = q["tab"]
        data.append({"range": f"'{tab}'!{col_letter(COL_BODY)}{q['row']}", "values": [[q["body"]]]})
        if q["write_name"]:
            data.append({"range": f"'{tab}'!{col_letter(COL_FIRST)}{q['row']}", "values": [[q["first"]]]})
            data.append({"range": f"'{tab}'!{col_letter(COL_LAST)}{q['row']}", "values": [[q["last"]]]})
    for k in range(0, len(data), 400):
        svc.spreadsheets().values().batchUpdate(
            spreadsheetId=sid,
            body={"valueInputOption": "RAW", "data": data[k:k + 400]}).execute()

    # 2. create the DRAFT campaign
    cid = args.campaign_id
    if not cid:
        cid = create_campaign(args.campaign_name)
        print(f"DRAFT campaign created: {cid}")

    # 3. push leads
    pushed, blocked, failed, pending = 0, 0, [], []

    def flush():
        if pending:
            svc.spreadsheets().values().batchUpdate(
                spreadsheetId=sid, body={"valueInputOption": "RAW", "data": pending}).execute()
            pending.clear()

    for n, q in enumerate(queue, 1):
        p = dict(q["payload"]); p["campaign"] = cid
        try:
            resp = requests.post(f"{BASE}/leads", headers=headers(), json=p, timeout=30)
        except requests.RequestException as e:
            failed.append((q["row"], str(e)[:80])); continue
        if resp.status_code in (200, 201):
            pending.append({"range": f"'{q['tab']}'!{col_letter(COL_ADDED)}{q['row']}",
                            "values": [["TRUE"]]})
            pushed += 1
        elif resp.status_code == 400 and "blocklist" in resp.text.lower():
            pending.append({"range": f"'{q['tab']}'!{col_letter(COL_ADDED)}{q['row']}",
                            "values": [["BLOCKLISTED"]]})
            blocked += 1
        else:
            failed.append((q["row"], f"{resp.status_code}: {resp.text[:90]}"))
        if n % BATCH_SIZE == 0:
            flush()
            print(f"  {n}/{len(queue)} | pushed {pushed} | blocked {blocked} | failed {len(failed)}")
            time.sleep(1.0)
    flush()

    print(f"\n=== Done ===\n  Pushed: {pushed} | Blocklisted: {blocked} | Failed: {len(failed)}")
    print(f"  Campaign {cid} is a DRAFT (mailboxes attached by tag -> it will show as paused).")
    print(f"  Review, then activate with POST /campaigns/{cid}/activate or the UI.")
    for row, err in failed[:10]:
        print(f"    row {row}: {err}")


if __name__ == "__main__":
    main()
