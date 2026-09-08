"""
Assemble the Touch 1 body + subject per contact on the Campaign sheet, from the
locked template. Pure string assembly, no LLM.

Template (Jude-approved):
  Hi {first},
  {icebreaker}                        <- omitted if blank (bridge-only open)
  I spend a lot of time around {company_type} in {state}, and most of them tell
  me electricity is one of their biggest costs. Usually 20 to 30 percent of it
  is quiet waste, and no one ever shows them where to look.
  I know a chartered energy engineer in Melbourne with 20 years on Australian
  industrial sites, who audits exactly that. If it is worth acting on, the
  monitoring runs as a service paid out of the savings, so there is no capex.
  The first audit is free.
  If relevant, I can connect you with him directly.

Subject: {casual company}'s power bill. Signature is NOT here - it lives in the
Instantly sequence, per sending account.
"""

import argparse
import os
import re
import sys

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN = os.path.join(SCRIPT_DIR, "..", "..", "..", "token.json")
TAB = "Campaign"

C_FIRST, C_COMPANY, C_STATE = 0, 3, 5
C_TYPE, C_ICE, C_SUBJECT, C_BODY = 9, 10, 11, 12

LEGAL_RE = re.compile(
    r"\s*(pty\.?\s*ltd\.?|proprietary\s+limited|pty\.?|ltd\.?|limited|inc\.?|"
    r"incorporated|p/l|group|holdings|australia|aust)\b\.?", re.I)


def casual(name):
    n, prev = name.strip(), None
    while prev != n:
        prev = n
        n = LEGAL_RE.sub("", n).strip(" ,.&-")
    n = re.sub(r"\s{2,}", " ", n).strip() or name.strip()
    letters = [c for c in n if c.isalpha()]
    if letters and all(c.isupper() for c in letters):
        small = {"AND", "OF", "THE", "FOR", "CO", "&"}
        n = " ".join(w if (len(w) <= 3 or "." in w) and w.isupper()
                     else (w.lower() if w in small else w.capitalize())
                     for w in n.split())
    return n


def possessive(n):
    return f"{n}'" if n.rstrip().endswith("s") else f"{n}'s"


def build_body(first, ctype, state, ice):
    ctype = ctype or "food and drink producers"
    where = f" in {state}" if state else ""
    bridge = (f"I spend a lot of time around {ctype}{where}, and most of them "
              f"tell me electricity is one of their biggest costs. Usually 20 "
              f"to 30 percent of it is quiet waste, and no one ever shows them "
              f"where to look.")
    offer = ("I know a chartered energy engineer in Melbourne with 20 years on "
             "Australian industrial sites, who audits exactly that. If it is "
             "worth acting on, the monitoring runs as a service paid out of the "
             "savings, so there is no capex. The first audit is free.")
    cta = "If relevant, I can connect you with him directly."
    blocks = [f"Hi {first},"]
    if ice:
        blocks.append(ice)
    blocks += [bridge, offer, cta]
    return "\n\n".join(blocks)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet_url", required=True)
    ap.add_argument("--preview", type=int, default=0)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    sid = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", args.sheet_url).group(1)
    svc = build("sheets", "v4",
                credentials=Credentials.from_authorized_user_file(TOKEN))
    vals = svc.spreadsheets().values().get(
        spreadsheetId=sid, range=f"{TAB}!A2:N2000").execute().get("values", [])

    def cell(r, i):
        return r[i].strip() if len(r) > i and r[i] else ""

    made, pending, shown = 0, [], []
    for i, r in enumerate(vals, start=2):
        first, company = cell(r, C_FIRST), cell(r, C_COMPANY)
        if not (first and company and cell(r, 2)):
            continue
        cc = casual(company)
        subject = f"{possessive(cc)} power bill"
        body = build_body(first, cell(r, C_TYPE), cell(r, C_STATE), cell(r, C_ICE))
        pending.append({"range": f"{TAB}!L{i}:M{i}", "values": [[subject, body]]})
        made += 1
        if len(shown) < args.preview:
            shown.append((company, subject, body))

    for co, subj, body in shown:
        print(f"\n{'='*70}\n{co}\nSUBJECT: {subj}\n{'-'*70}\n{body}")
    print(f"\n{'='*70}\nassembled {made}")
    if not args.apply:
        print("dry run - nothing written. Re-run with --apply.")
        return
    for i in range(0, len(pending), 20):
        svc.spreadsheets().values().batchUpdate(
            spreadsheetId=sid,
            body={"valueInputOption": "RAW", "data": pending[i:i+20]}).execute()
    print(f"wrote subject+body for {made} rows")


if __name__ == "__main__":
    sys.exit(main())
