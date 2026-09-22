"""
Create the TRADES recruitment-firm campaign in Instantly (DRAFT) and, with
--push, upload KEEP leads one at a time.

Sept 2026. Cloned from healthcare-staffing-enrichment/push_pipeline_intro_campaign.py
per the clone-never-parameterize convention; that script feeds the live
healthcare campaign and must not be edited.

WHAT IS DIFFERENT FROM THE HEALTHCARE CLONE
-------------------------------------------
1. COPY IS STATIC AND LIVES IN THE SEQUENCE. The only variable is the
   recipient first name, so the body is written straight into the sequence
   steps as {{firstName}} rather than riding per-lead as {{personalization}}.
   There is no generate step for this lane and no col V body to build.

2. NO FIRST-NAME CASUALIZATION (Jude, 2026-09-11). Names are case-normalised
   only (AMF returns "ROBERT"), never renamed to Rob. Same call he made on
   production-directory-leads and hirebase-healthcare-leads.

3. COPY, approved in-session 2026-09-11 over several revisions. Deliberate
   choices, do not "improve" them:
     - Opener asks a QUESTION, it does not ask for the call. The reply is
       what drives to the call.
     - NO case study in the opener. Jude cut it: "seems like a pitch and
       they will not reply."
     - Follow-ups are short bumps. They never name specific trades, because
       naming a trade manufactures a wrong answer the reader can fail.
     - FU2 asks for the call directly.
     - FU3 STATES the close rather than asking, and invites a re-open.

TARGET ROW (all must hold):
  S == "found"  AND  Z == "KEEP"  AND  email(Q)  AND  a first name
  AND col W not TRUE/BLOCKLISTED
  AND email not already in the Instantly workspace (live freshness gate)
  AND (website blank OR email-domain root == website-domain root)

Schema (both tabs, A-based):
  A:Company  H:Website  O:dm_name  P:dm_title  Q:dm_email  S:email_status
  W:added_to_instantly  Y:icp_class  Z:outreach_flag

Run:
  python3 -W ignore push_trades_campaign.py --create
  python3 -W ignore push_trades_campaign.py --campaign_id ID --push [--limit N]
"""
import os, re, sys, json, time, argparse, requests
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(SCRIPT_DIR, "..", "..", "..", ".env"))
TOKEN_PATH = os.path.join(SCRIPT_DIR, "..", "..", "..", "token.json")
INSTANTLY_API_KEY = os.getenv("INSTANTLY_API_KEY")
BASE = "https://api.instantly.ai/api/v2"

SHEET_URL = "https://docs.google.com/spreadsheets/d/1yDjqsGXF28czCmmKsfCo3U-2cXM6lTPePKS0M3Bralw"
TABS = ["1-50 EMP", "50-200 EMP"]
CAMPAIGN_NAME = "Trades Recruitment Firms - Sep 2026"

COL_NAME, COL_WEBSITE = 0, 7
COL_DM_NAME, COL_DM_EMAIL, COL_STATUS = 14, 16, 18
COL_ADDED, COL_FLAG = 22, 25

SUBJECT = "trades reqs"

# Signature lives at SEQUENCE level, never inside a per-lead body
# (standing rule, Jude 2026-08-05).
SIGNATURE = ("<div><br /></div><div>Best regards,</div>"
             "<div>Rood Judeley (Jude)</div>")
IPHONE = "<div><br /></div><div>Sent from my iPhone</div>"
BR = "<div><br /></div>"

STEP1 = (
    "<div>Hi {{firstName}},</div>" + BR +
    "<div>I've got a few contractors struggling to find tradespeople right now, "
    "and they're open to external staffing help.</div>" + BR +
    "<div>Are you taking on new reqs right now, or are you at capacity?</div>"
    + SIGNATURE + IPHONE)

FU1 = ("<div>Hey {{firstName}}, any interest in new reqs?</div>" + SIGNATURE + IPHONE)

FU2 = ("<div>Still have contractors looking.</div>" + SIGNATURE + IPHONE)

FU3 = ("<div>I'll close this out for now. If things change, just reply and I'll "
       "pick it back up.</div>" + SIGNATURE + IPHONE)

# Live settings verified 2026-09-11 off "Equipment Finance - Lenders - Sep 2026"
# (his most recent campaign). Tag set drifts — always re-read, never copy an
# older script. provider_routing_rules is ORDERED: catch-all FIRST.
EMAIL_TAG_LIST = [
    "2b2adf27-cf48-4ed1-bcb4-513ecb49f719",
    "ce2014e8-b42f-415e-b5f3-c185093f2042",
    "d221f400-cd05-4ca0-bf28-c5194227f701",   # ScaledMail-Google
    "d00f89d5-9a82-4602-8614-64a172de6424",   # ScaledMail-Microsoft
    "ba2df4e6-a8a7-412d-833e-b6b48c5e12da",   # Zapmail
]
PROVIDER_ROUTING_RULES = [
    {"action": "send", "recipient_esp": ["all"],     "sender_esp": ["google"]},
    {"action": "send", "recipient_esp": ["google"],  "sender_esp": ["google"]},
    {"action": "send", "recipient_esp": ["outlook"], "sender_esp": ["outlook"]},
]


def H():
    return {"Authorization": f"Bearer {INSTANTLY_API_KEY}", "Content-Type": "application/json"}


def create_campaign():
    step = lambda d, subj, body: {
        "type": "email", "delay": d, "delay_unit": "days", "pre_delay_unit": "days",
        "variants": [{"subject": subj, "body": body}]}
    payload = {
        "name": CAMPAIGN_NAME,
        "campaign_schedule": {"schedules": [{
            "name": "Default",
            "timing": {"from": "07:00", "to": "18:00"},
            "days": {"1": True, "2": True, "3": True, "4": True, "5": True, "6": True},
            "timezone": "America/Detroit",
        }]},
        "sequences": [{"steps": [
            step(2, SUBJECT, STEP1),
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
    r = requests.post(f"{BASE}/campaigns", headers=H(), json=payload, timeout=30)
    if r.status_code not in (200, 201):
        raise SystemExit(f"create failed {r.status_code}: {r.text[:500]}")
    return r.json()



def sheets_svc():
    td = json.load(open(TOKEN_PATH))
    cr = Credentials(token=td["token"], refresh_token=td["refresh_token"],
                     token_uri=td["token_uri"], client_id=td["client_id"],
                     client_secret=td["client_secret"], scopes=td["scopes"])
    if not cr.valid:
        cr.refresh(Request()); td["token"] = cr.token; json.dump(td, open(TOKEN_PATH, "w"))
    return build("sheets", "v4", credentials=cr)


def root_domain(d):
    d = (d or "").strip().lower()
    d = re.sub(r"^https?://", "", d)
    d = re.sub(r"^www\.", "", d).split("/")[0]
    return ".".join(d.split(".")[-2:]) if d else ""


def split_name(full):
    parts = [p for p in re.split(r"\s+", (full or "").strip()) if p]
    if not parts:
        return "", ""
    first = parts[0]
    last = parts[-1] if len(parts) > 1 else ""
    # case-normalise only. NO nickname casualisation (Jude, 2026-09-11).
    fix = lambda w: w if re.search(r"[a-z]", w) else w.capitalize()
    return fix(first), fix(last)


def workspace_emails():
    """Every lead already in the Instantly workspace. This, not col W, is the
    real double-send guard — col W only knows about pushes this sheet made."""
    seen, cursor = set(), None
    while True:
        body = {"limit": 100}
        if cursor:
            body["starting_after"] = cursor
        r = requests.post(f"{BASE}/leads/list", headers=H(), json=body, timeout=60)
        if r.status_code != 200:
            raise SystemExit(f"leads/list failed {r.status_code}: {r.text[:300]}")
        d = r.json()
        items = d.get("items", [])
        for it in items:
            e = (it.get("email") or "").strip().lower()
            if e:
                seen.add(e)
        cursor = d.get("next_starting_after")
        if not cursor or not items:
            break
        print(f"    workspace scan: {len(seen)}", flush=True)
    return seen


def build_queue(svc, contacted):
    sid = re.search(r"/d/([a-zA-Z0-9-_]+)", SHEET_URL).group(1)
    queue, seen = [], set()
    skipped = {"not_found": 0, "not_keep": 0, "already_W": 0, "no_email": 0,
               "no_first": 0, "domain_mismatch": 0, "in_workspace": 0, "dupe_in_run": 0}
    for tab in TABS:
        rows = svc.spreadsheets().values().get(
            spreadsheetId=sid, range=f"'{tab}'!A:AA").execute().get("values", [])[1:]
        for i, r in enumerate(rows):
            g = lambda x: (r[x].strip() if len(r) > x and r[x] else "")
            if g(COL_STATUS).lower() != "found":
                skipped["not_found"] += 1; continue
            if g(COL_FLAG).upper() != "KEEP":
                skipped["not_keep"] += 1; continue
            if g(COL_ADDED).upper() in ("TRUE", "BLOCKLISTED"):
                skipped["already_W"] += 1; continue
            email = g(COL_DM_EMAIL).lower()
            if not email:
                skipped["no_email"] += 1; continue
            first, last = split_name(g(COL_DM_NAME))
            if not first:
                skipped["no_first"] += 1; continue
            wd, ed = root_domain(g(COL_WEBSITE)), root_domain(email.split("@")[-1])
            if wd and ed and wd != ed:
                skipped["domain_mismatch"] += 1; continue
            if email in contacted:
                skipped["in_workspace"] += 1; continue
            if email in seen:
                skipped["dupe_in_run"] += 1; continue
            seen.add(email)
            queue.append({"tab": tab, "row": i + 2, "email": email, "first": first,
                          "last": last, "company": g(COL_NAME), "website": g(COL_WEBSITE)})
    return sid, queue, skipped


def push(campaign_id, limit=0, apply=False):
    svc = sheets_svc()
    print("scanning the Instantly workspace for existing leads...", flush=True)
    contacted = workspace_emails()
    print(f"  workspace holds {len(contacted)} leads\n", flush=True)
    sid, queue, skipped = build_queue(svc, contacted)
    print(f"QUEUE: {len(queue)} leads")
    for k, v in skipped.items():
        if v:
            print(f"   skipped {k}: {v}")
    if limit:
        queue = queue[:limit]
    if not apply:
        print("\n(dry run — pass --apply)")
        for q in queue[:10]:
            print(f"   {q['first']} {q['last']} <{q['email']}>  {q['company'][:34]}")
        return
    ok = blocked = failed = 0
    pending = []
    for n, q in enumerate(queue, 1):
        body = {"campaign": campaign_id, "email": q["email"],
                "first_name": q["first"], "last_name": q["last"],
                "company_name": q["company"], "website": q["website"]}
        # A single read timeout used to kill the whole run (2026-09-14). Retry
        # rather than abort: the live workspace gate makes a resume safe, but a
        # mid-run crash still costs a full 16k-lead rescan on restart.
        r = None
        for attempt in range(4):
            try:
                r = requests.post(f"{BASE}/leads", headers=H(), json=body, timeout=90)
                break
            except requests.RequestException as e:
                if attempt == 3:
                    print(f"  [timeout x4] {q['email']}: {type(e).__name__}", flush=True)
                    r = None
                    break
                time.sleep(5 * (attempt + 1))
        if r is None:
            failed += 1
            continue
        if r.status_code == 429:
            time.sleep(20)
            r = requests.post(f"{BASE}/leads", headers=H(), json=body, timeout=90)
        if r.status_code in (200, 201):
            ok += 1; mark = "TRUE"
        elif r.status_code == 400:
            blocked += 1; mark = "BLOCKLISTED"
            print(f"  [blocklist] {q['email']}", flush=True)
        else:
            failed += 1
            print(f"  [!] {q['email']} -> {r.status_code} {r.text[:120]}", flush=True)
            continue
        pending.append({"range": f"'{q['tab']}'!W{q['row']}", "values": [[mark]]})
        if len(pending) >= 10:
            svc.spreadsheets().values().batchUpdate(
                spreadsheetId=sid, body={"valueInputOption": "RAW", "data": pending}).execute()
            pending = []
            print(f"  {n}/{len(queue)}  ok={ok}", flush=True)
    if pending:
        svc.spreadsheets().values().batchUpdate(
            spreadsheetId=sid, body={"valueInputOption": "RAW", "data": pending}).execute()
    print(f"\nDone. pushed={ok} blocklisted={blocked} failed={failed}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--create", action="store_true")
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--campaign_id", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    if a.create:
        c = create_campaign()
        cid = c["id"]
        print("CAMPAIGN CREATED (DRAFT)")
        print("  id    :", cid)
        print("  name  :", c.get("name"))
        print("  status:", c.get("status"), "(0 = draft)")
        print("  url   : https://app.instantly.ai/app/campaign/%s" % cid)
        v = requests.get(f"{BASE}/campaigns/{cid}", headers=H(), timeout=30).json()
        print("\nVERIFIED FROM A FRESH GET:")
        print("  text_only            :", v.get("text_only"))
        print("  first_email_text_only:", v.get("first_email_text_only"))
        print("  link_tracking        :", v.get("link_tracking"))
        print("  open_tracking        :", v.get("open_tracking"))
        print("  stop_on_reply        :", v.get("stop_on_reply"))
        print("  daily_limit          :", v.get("daily_limit"))
        print("  match_lead_esp       :", v.get("match_lead_esp"))
        print("  email_tag_list       :", len(v.get("email_tag_list") or []), "tags")
        print("  routing rules        :", json.dumps(v.get("provider_routing_rules")))
        steps = v.get("sequences", [{}])[0].get("steps", [])
        print("  sequence steps       :", len(steps))
        for i, s in enumerate(steps, 1):
            subj = s["variants"][0].get("subject")
            print(f"     step {i}: delay {s.get('delay')}d  subject={subj!r}")
        return
    if a.push:
        if not a.campaign_id:
            raise SystemExit("--push needs --campaign_id")
        push(a.campaign_id, a.limit, a.apply)
        return
    print("nothing to do — pass --create or --push")


if __name__ == "__main__":
    main()
