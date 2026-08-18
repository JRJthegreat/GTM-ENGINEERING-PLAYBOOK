"""AMF /decision-maker rescue over the commercial sheet's waterfall misses.

Purple Magic's /decision-makers index and the NPPES filer are the waterfall's
two sources; `no_candidates` rows are companies where PM had nobody AND the
filer was unusable. AnyMail Finder's /decision-maker heuristic is a third,
independent index — different providers fail on different companies (the
standing repo finding), so it reaches some of these. 2 credits per found
valid email, 0 on a miss.

Standing email rules kept: only email_status == "valid" is written; the email
domain must match the company domain; free mailboxes rejected; DM data never
written without a valid email; batch-of-10 writes; resume-safe (misses are
re-stamped so reruns skip them).

Row selection: Status (E) matches --status_prefix, email (W) blank,
website (L) present, AA blank, email_status (AF) in --statuses.
Stops at --target NEW valid emails.

Usage:
  python3 -W ignore .claude/skills/nppes-new-clinics/scripts/amf_dm_rescue.py
      --sheet_url URL [--status_prefix "NEW LOCATION,NEW SITE"]
      [--statuses no_candidates] [--category ceo] [--target 60] [--dry_run]
"""
import argparse
import os
import time

import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "..", "..", "..", ".env"))
AMF_API_KEY = os.getenv("ANYMAILFINDER_API_KEY")
AMF_DM_URL = "https://api.anymailfinder.com/v5.1/find-email/decision-maker"

TAB = "Leads"
C_STATUS_LEAD = 4
C_COMPANY, C_WEBSITE = 10, 11
C_DM_NAME, C_DM_TITLE, C_LINKEDIN = 19, 20, 21
C_EMAIL, C_FIRST, C_LAST = 22, 23, 24
C_EMAIL_STATUS = 31
C_SOURCE = 34

FREE_MAIL = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com",
             "icloud.com", "protonmail.com", "live.com", "msn.com", "comcast.net"}


def col_letter(i):
    return (chr(65 + i) if i < 26 else "A" + chr(65 + i - 26))


def norm_domain(url):
    d = (url or "").lower().strip()
    for p in ("https://", "http://", "www."):
        d = d[len(p):] if d.startswith(p) else d
    return d.split("/")[0].strip()


def get_service():
    import json as _json
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    tp = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "..", "..", "..", "token.json")
    with open(tp) as f:
        td = _json.load(f)
    creds = Credentials(token=td["token"], refresh_token=td["refresh_token"],
                        token_uri=td["token_uri"], client_id=td["client_id"],
                        client_secret=td["client_secret"],
                        scopes=td.get("scopes"))
    if creds.expired:
        creds.refresh(Request())
    return build("sheets", "v4", credentials=creds)


def amf_dm(domain, company, category):
    body = {"decision_maker_category": [category],
            "domain": domain, "company_name": company}
    try:
        r = requests.post(AMF_DM_URL, json=body, timeout=60,
                          headers={"Authorization": AMF_API_KEY,
                                   "Content-Type": "application/json"})
        if r.status_code == 429:
            return {"rate_limited": True}
        d = r.json()
        email = (d.get("valid_email") or d.get("email") or "").lower()
        if d.get("email_status") != "valid":
            return {}
        edom = email.split("@")[-1]
        if edom in FREE_MAIL:
            return {}
        droot = ".".join(domain.split(".")[-2:])
        if not (edom == domain or edom.endswith("." + droot) or edom == droot):
            return {}
        return {"email": email,
                "name": d.get("person_full_name") or "",
                "title": d.get("person_job_title") or "",
                "linkedin": d.get("person_linkedin_url") or ""}
    except Exception:
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet_url", required=True)
    ap.add_argument("--status_prefix", default="NEW LOCATION,NEW SITE")
    ap.add_argument("--statuses", default="no_candidates")
    ap.add_argument("--category", default="ceo")
    ap.add_argument("--target", type=int, default=60)
    ap.add_argument("--dry_run", action="store_true")
    args = ap.parse_args()
    prefixes = tuple(p.strip() for p in args.status_prefix.split(",") if p.strip())
    rescue_statuses = {s.strip() for s in args.statuses.split(",") if s.strip()}

    svc = get_service()
    sid = args.sheet_url.split("/d/")[1].split("/")[0]
    values = svc.spreadsheets().values().get(
        spreadsheetId=sid, range=f"'{TAB}'!A2:AI").execute().get("values", [])

    def c(r, i):
        return r[i].strip() if len(r) > i and r[i] else ""

    todo = [(n, r) for n, r in enumerate(values, start=2)
            if c(r, C_STATUS_LEAD).startswith(prefixes)
            and not c(r, C_EMAIL) and c(r, C_WEBSITE)
            and len(r) > 26 and not c(r, 26)
            and c(r, C_EMAIL_STATUS) in rescue_statuses]
    print(f"[amf-rescue] {len(todo)} eligible rows | target {args.target} new "
          f"| category {args.category}{' | DRY RUN' if args.dry_run else ''}")
    if args.dry_run:
        return

    found = 0
    updates = []
    for i, (n, r) in enumerate(todo):
        if found >= args.target:
            break
        domain = norm_domain(c(r, C_WEBSITE))
        hit = amf_dm(domain, c(r, C_COMPANY), args.category)
        if hit.get("rate_limited"):
            print("[amf-rescue] 429 — stopping")
            break
        if hit.get("email"):
            found += 1
            parts = hit["name"].split()
            first = parts[0] if parts else ""
            last = " ".join(parts[1:]) if len(parts) > 1 else ""
            for col, val in ((C_DM_NAME, hit["name"]), (C_DM_TITLE, hit["title"]),
                             (C_LINKEDIN, hit["linkedin"]), (C_EMAIL, hit["email"]),
                             (C_FIRST, first), (C_LAST, last),
                             (C_EMAIL_STATUS, "valid"),
                             (C_SOURCE, f"amf_dm:{args.category}")):
                if val:
                    updates.append({"range": f"'{TAB}'!{col_letter(col)}{n}",
                                    "values": [[val]]})
        else:
            updates.append({"range": f"'{TAB}'!{col_letter(C_EMAIL_STATUS)}{n}",
                            "values": [[f"amf_dm_{args.category}_not_found"]]})
        if len(updates) >= 10:
            svc.spreadsheets().values().batchUpdate(
                spreadsheetId=sid,
                body={"valueInputOption": "RAW", "data": updates}).execute()
            updates = []
        print(f"  {i+1}/{len(todo)} | found {found}/{args.target}", end="\r")
        time.sleep(0.3)
    if updates:
        svc.spreadsheets().values().batchUpdate(
            spreadsheetId=sid,
            body={"valueInputOption": "RAW", "data": updates}).execute()
    print(f"\n[amf-rescue] done: {found} new valid emails")


if __name__ == "__main__":
    main()
