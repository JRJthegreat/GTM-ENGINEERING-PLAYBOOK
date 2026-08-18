"""One-off: re-render bodies with the current generator copy and PATCH the
already-pushed B2 leads' personalization. Safe ONLY while the campaign is
DRAFT (same constraint as production-directory-leads'
sync_revised_personalization.py, which this mirrors).

Why (Jude, 2026-08-18): two copy changes after the first B2 pushes —
multi-location framing (name up to 3 cities, spelled site count past that)
and NO first-name nicknaming. Reuses generate_connector_emails.py by import
so there is exactly one copy implementation.

Per lead: recompute body from the sheet row -> update sheet col Z -> PATCH
the Instantly lead's custom_variables.personalization (merge semantics).
Verifies persistence with a fresh GET on the first patched lead.

Usage:
  python3 -W ignore resync_b2_personalization.py --sheet_url URL
      --campaign_id ID [--dry_run]
"""
import argparse
import importlib.util
import json
import os
import sys
import time

import requests
from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(HERE, "..", "..", "..", ".env"))
BASE = "https://api.instantly.ai/api/v2"
H = {"Authorization": f"Bearer {os.getenv('INSTANTLY_API_KEY')}",
     "Content-Type": "application/json"}

sys.path.insert(0, HERE)
spec = importlib.util.spec_from_file_location(
    "gce", os.path.join(HERE, "generate_connector_emails.py"))
gce = importlib.util.module_from_spec(spec)
sys.modules["gce"] = gce
spec.loader.exec_module(gce)


def list_campaign_leads(cid):
    out, starting_after = [], None
    while True:
        body = {"campaign": cid, "limit": 100}
        if starting_after:
            body["starting_after"] = starting_after
        r = requests.post(f"{BASE}/leads/list", headers=H, json=body, timeout=60)
        r.raise_for_status()
        d = r.json()
        items = d.get("items", [])
        for it in items:
            if it.get("campaign") != cid:
                sys.exit("FATAL: campaign filter did not apply; aborting.")
        out.extend(items)
        starting_after = d.get("next_starting_after")
        if not starting_after or not items:
            return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet_url", required=True)
    ap.add_argument("--campaign_id", required=True)
    ap.add_argument("--dry_run", action="store_true")
    args = ap.parse_args()

    svc = gce.get_service()
    sid = args.sheet_url.split("/d/")[1].split("/")[0]
    values = svc.spreadsheets().values().get(
        spreadsheetId=sid, range=f"'{gce.TAB}'!A2:AI").execute().get("values", [])

    def c(r, i):
        return r[i].strip() if len(r) > i and r[i] else ""

    multi = gce.build_multi_bodies(values)

    # first pushed row per inbox, mirroring the pusher's rows[0] pick
    first_row = {}
    for n, r in enumerate(values, start=2):
        em = c(r, gce.C_EMAIL).lower()
        if em and c(r, 26) == "TRUE" and em not in first_row:
            first_row[em] = (n, r)

    leads = list_campaign_leads(args.campaign_id)
    print(f"[resync] {len(leads)} leads in campaign")

    changed = missing = 0
    checked = False
    for l in leads:
        em = (l.get("email") or "").lower()
        if em not in first_row:
            missing += 1
            continue
        n, row = first_row[em]
        if em in multi:
            body = multi[em][0]
        else:
            body, _ = gce.render(row)
        old = (l.get("payload") or {}).get("personalization", "")
        if body == old:
            continue
        changed += 1
        if args.dry_run:
            if changed <= 3:
                print(f"--- {em}\n{body}\n")
            continue
        svc.spreadsheets().values().update(
            spreadsheetId=sid, range=f"'{gce.TAB}'!Z{n}",
            valueInputOption="RAW", body={"values": [[body]]}).execute()
        r = requests.patch(f"{BASE}/leads/{l['id']}", headers=H,
                           json={"custom_variables": {"personalization": body}},
                           timeout=30)
        if r.status_code != 200:
            print(f"  FAIL {em}: {r.status_code} {r.text[:150]}")
            continue
        if not checked:
            g = requests.get(f"{BASE}/leads/{l['id']}", headers=H,
                             timeout=30).json()
            ok = (g.get("payload") or {}).get("personalization") == body
            print(f"  persistence check on {em}: {'OK' if ok else 'MISSING'}")
            if not ok:
                sys.exit(1)
            checked = True
        time.sleep(0.25)
    print(f"[resync] done: {changed} updated, {missing} leads not matched to sheet")


if __name__ == "__main__":
    main()
