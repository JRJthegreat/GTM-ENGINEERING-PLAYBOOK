"""
Apollo -> Google -> Purple Magic waterfall — third DM lane, over rows both
Purple Magic's own /decision-makers index AND AMF's /decision-maker
(categories ceo + operations) missed.

WHY A THIRD LANE (Jude, 2026-08-14)
------------------------------------
Purple Magic's /decision-makers index and AMF's /decision-maker heuristic
are each one provider's private view of who works at a company. Apollo's
free people search (mixed_people/api_search) is a genuinely different
index — it can surface a person neither of the other two ever had.

This lane borrows the discovery half of apollo-dm-waterfall's
apollo_dm_waterfall.py (free Apollo search -> Google de-obfuscation of the
name) but swaps its final email step: instead of AMF's person endpoint,
it calls Purple Magic's own /find — reusing the same PM account and the
same "only bill on a verified hit" cost profile already proven in
find_dm_large_firms.py, rather than opening a second paid step through a
different provider for the same final lookup.

RANKING: no LLM call, unlike the original waterfall's GPT-4.1 RANK_SYSTEM.
Apollo's candidates are gated and ranked by the SAME mechanical ban+ladder
already built and cache-replay-proven in find_dm_large_firms.py — imported
directly (importlib), not re-implemented, so the ladder logic stays one
source of truth: owner -> new business -> desk/division owner -> ops exec,
clinical and support-staff titles banned before any rung is tested.
Jude's call, 2026-08-14: no LLM ranking on this vertical (mirrors his
distrust of GPT-4.1 for the ICP classification pass).

Schema: same campaign-sheet layout as find_ceo_demand.py /
find_dm_large_firms.py.
  A:Company Name  C:Employee Size  H:Website
  O:dm_name  P:dm_title  Q:dm_email  R:dm_linkedin_url  S:email_status
  Z:outreach_flag (KEEP required by default, via --require_keep)

Per row:
  1. Apollo mixed_people/api_search by domain (FREE, 0 Apollo credits) ->
     up to 100 candidates (obfuscated last name, title, first name).
  2. Gate + rank via find_dm_large_firms.classify() / RUNG_ORDER.
  3. Per candidate, best rung first:
       a. Google (apify~google-search-scraper) "{first} {company} {title}
          site:linkedin.com/in" de-obfuscates the surname, validated
          against the "Xx***x" pattern — logic ported verbatim from
          apollo_dm_waterfall.py's google_deobfuscate()/surname_matches().
       b. Purple Magic /find {firstName, lastName, domain} -> valid email
          only, domain-matched, free mailbox rejected (find_dm_large_firms'
          check_email(), reused as-is).
     First candidate to produce a verified email wins; a miss tries the
     next-ranked candidate — never downgrades to a worse rung once a
     better one is available.
  4. Never writes DM name/title without a valid email (no partial rows).

Run:
  python3 -W ignore find_dm_apollo_pm.py --sheet_url "URL" --tab "1-50 EMP" \
      --sizes "51-200" [--limit 150] [--workers 6] [--dry_run]
"""

import os
import re
import json
import time
import importlib.util
import argparse
import threading
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from dotenv import load_dotenv

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(SCRIPT_DIR, "..", "..", "..", ".env")
load_dotenv(ENV_PATH)

# --- reuse the proven gate/ladder + sheet/PM plumbing, don't reinvent it ---
_spec = importlib.util.spec_from_file_location(
    "lf", os.path.join(SCRIPT_DIR, "find_dm_large_firms.py"))
lf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lf)

APOLLO_API_KEY = os.getenv("APOLLO_API_KEY")
APIFY_TOKEN = os.getenv("APIFY_API_TOKEN")
APOLLO_SEARCH_URL = "https://api.apollo.io/api/v1/mixed_people/api_search"
GOOGLE_ACTOR_URL = ("https://api.apify.com/v2/acts/apify~google-search-scraper"
                    "/run-sync-get-dataset-items")

COL_NAME = 0            # A
COL_EMPSIZE = 2         # C
COL_WEBSITE = 7         # H
COL_DM_NAME = 14        # O
COL_DM_TITLE = 15       # P
COL_DM_EMAIL = 16       # Q
COL_DM_LINKEDIN = 17    # R
COL_EMAIL_STATUS = 18   # S
COL_OUTREACH_FLAG = 25  # Z

WRITE_BATCH = 10


def parse_obfuscation(obf):
    """'Wo***e' -> ('Wo', 'e'). Returns (prefix, suffix) or (None, None)."""
    m = re.match(r"^([A-Za-z]+)\*+([A-Za-z]+)$", (obf or "").strip())
    if not m:
        return None, None
    return m.group(1), m.group(2)


def surname_matches(surname, prefix, suffix):
    s = (surname or "").lower()
    return (len(s) >= len(prefix) + len(suffix)
            and s.startswith(prefix.lower()) and s.endswith(suffix.lower()))


def apollo_search(domain):
    payload = {"q_organization_domains_list": [domain], "page": 1, "per_page": 100}
    for attempt in (1, 2):
        try:
            resp = requests.post(
                APOLLO_SEARCH_URL,
                headers={"x-api-key": APOLLO_API_KEY, "Content-Type": "application/json"},
                json=payload, timeout=60)
        except requests.RequestException:
            return []
        if resp.status_code == 429 and attempt == 1:
            time.sleep(60)
            continue
        if resp.status_code != 200:
            return []
        return resp.json().get("people", []) or []
    return []


def google_deobfuscate(first, last_obf, title, company):
    """Returns (surname, linkedin_url) or (None, None)."""
    prefix, suffix = parse_obfuscation(last_obf)
    if not first or not prefix:
        return None, None
    title_short = " ".join((title or "").split()[:4])
    query = f"{first} {company} {title_short} site:linkedin.com/in"
    try:
        resp = requests.post(
            GOOGLE_ACTOR_URL, params={"token": APIFY_TOKEN},
            json={"queries": query, "resultsPerPage": 8, "maxPagesPerQuery": 1,
                  "languageCode": "en", "countryCode": "us",
                  "includeUnfilteredResults": False},
            timeout=180)
    except requests.RequestException:
        return None, None
    if resp.status_code not in (200, 201):
        return None, None

    company_tokens = {t for t in re.split(r"\W+", company.lower()) if len(t) > 2}
    for item in resp.json():
        for r in item.get("organicResults", []):
            url = r.get("url", "")
            if "linkedin.com/in/" not in url:
                continue
            blob = (r.get("title", "") + " " + r.get("description", "")).lower()
            slug = urlparse(url).path.split("/in/")[-1].strip("/")
            slug_tokens = [re.sub(r"\d+$", "", t) for t in slug.split("-") if t]
            candidates = []
            if slug_tokens and slug_tokens[0].lower() == first.lower():
                candidates.append("".join(slug_tokens[1:]) if len(slug_tokens) == 2
                                  else (slug_tokens[1] if len(slug_tokens) > 1 else ""))
            title_text = r.get("title", "").split(" - ")[0].split(" | ")[0].split(" – ")[0]
            words = [re.sub(r"[^A-Za-z'-]", "", w) for w in title_text.split()]
            words = [w for w in words if w]
            if len(words) >= 2 and words[0].lower() == first.lower():
                candidates.append(words[-1])
            for cand in candidates:
                if cand and surname_matches(cand, prefix, suffix):
                    if (any(t in blob for t in company_tokens)
                            or any(t in blob for t in title_short.lower().split() if len(t) > 3)):
                        return cand.title(), url.split("?")[0]
    return None, None


def rank_apollo_candidates(people):
    """-> [(rung, person)] best rung first, via find_dm_large_firms' gate."""
    ranked = []
    for p in people:
        rung = lf.classify(p.get("title"))
        if rung and rung != "AMBIGUOUS":
            ranked.append((rung, p))
    order = ["A_owner", "B_new_business", "C_desk_owner", "D_ops_exec"]
    ranked.sort(key=lambda rp: order.index(rp[0]))
    return ranked


def resolve(target):
    dom = target["domain"]
    people = apollo_search(dom)
    if not people:
        return {**target, "status": "apw_no_people", "rung": ""}
    ranked = rank_apollo_candidates(people)
    if not ranked:
        return {**target, "status": "apw_no_rung", "rung": ""}
    for rung, p in ranked:
        first = p.get("first_name") or ""
        surname, linkedin = google_deobfuscate(
            first, p.get("last_name_obfuscated", ""), p.get("title", ""), target["name"])
        if not surname:
            continue
        d2, err2 = lf.pm("/find", {"firstName": first, "lastName": surname, "domain": dom})
        if err2:
            continue
        if d2.get("status") != "valid":
            continue
        email = lf.check_email(d2.get("email"), dom)
        if not email:
            continue
        return {**target, "status": "found", "rung": rung,
                "dm_name": f"{first} {surname}",
                "dm_title": (p.get("title") or "").strip(),
                "dm_email": email, "dm_linkedin": linkedin or ""}
    return {**target, "status": "apw_not_found", "rung": ""}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet_url", required=True)
    ap.add_argument("--tab", required=True)
    ap.add_argument("--sizes", default="", help="comma-separated Employee Size bands")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--require_keep", action="store_true", default=True)
    ap.add_argument("--ignore_keep_flag", action="store_true")
    ap.add_argument("--dry_run", action="store_true")
    args = ap.parse_args()

    if not (APOLLO_API_KEY and APIFY_TOKEN and lf.PM_KEY) and not args.dry_run:
        print("ERROR: need APOLLO_API_KEY, APIFY_API_TOKEN, PURPLE_MAGIC_KEY")
        return

    sheet_id = lf.parse_sheet_id(args.sheet_url)
    tab = args.tab
    service = lf.get_service()
    rows = service.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=f"'{tab}'!A:Z").execute().get("values", [])[1:]

    bands = {b.strip() for b in args.sizes.split(",") if b.strip()}
    targets = []
    for i, row in enumerate(rows):
        def cell(idx):
            return (row[idx].strip() if len(row) > idx and row[idx] else "")
        name, size = cell(COL_NAME), cell(COL_EMPSIZE)
        email, dom = cell(COL_DM_EMAIL), lf.norm_domain(cell(COL_WEBSITE))
        status = cell(COL_EMAIL_STATUS)
        if not (name and dom) or email:
            continue
        if status.startswith("apw_"):
            continue  # this lane already tried this row — don't re-spend on a repeat lookup
        if bands and size not in bands:
            continue
        if args.require_keep and not args.ignore_keep_flag:
            if cell(COL_OUTREACH_FLAG) != "KEEP":
                continue
        targets.append({"row": i + 2, "name": name, "domain": dom, "size": size})
    if args.limit:
        targets = targets[:args.limit]

    print(f"=== Apollo -> Google -> Purple Magic (third DM lane) — tab '{tab}' ===")
    print(f"Rows in scope: {len(targets)}", flush=True)
    if args.dry_run:
        for t in targets[:15]:
            print(f"  row{t['row']:5d} {t['name'][:38]:38s} {t['size']:<10} {t['domain']}")
        return

    lock = threading.Lock()
    updates, found, missed = [], 0, 0

    def flush():
        data = []
        for u in updates:
            r = u["row"]
            if u["status"] == "found":
                data += [
                    {"range": f"'{tab}'!{lf.col_letter(COL_DM_NAME)}{r}", "values": [[u["dm_name"]]]},
                    {"range": f"'{tab}'!{lf.col_letter(COL_DM_TITLE)}{r}", "values": [[u["dm_title"]]]},
                    {"range": f"'{tab}'!{lf.col_letter(COL_DM_EMAIL)}{r}", "values": [[u["dm_email"]]]},
                    {"range": f"'{tab}'!{lf.col_letter(COL_EMAIL_STATUS)}{r}", "values": [["found"]]},
                ]
                if u.get("dm_linkedin"):
                    data.append({"range": f"'{tab}'!{lf.col_letter(COL_DM_LINKEDIN)}{r}",
                                 "values": [[u["dm_linkedin"]]]})
            else:
                data.append({"range": f"'{tab}'!{lf.col_letter(COL_EMAIL_STATUS)}{r}",
                             "values": [[u["status"]]]})
        for attempt in range(4):
            try:
                service.spreadsheets().values().batchUpdate(
                    spreadsheetId=sheet_id,
                    body={"valueInputOption": "RAW", "data": data}).execute()
                break
            except Exception as e:
                if attempt < 3 and "429" in str(e):
                    time.sleep(65)
                else:
                    raise
        updates.clear()

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = [ex.submit(resolve, t) for t in targets]
        done = 0
        for fut in as_completed(futures):
            res = fut.result()
            with lock:
                updates.append(res)
                done += 1
                if res["status"] == "found":
                    found += 1
                    print(f"  +  [{res['rung']:<14}] {res['name'][:34]:34s} -> "
                          f"{res['dm_name']} ({res['dm_title']}) | {res['dm_email']}",
                          flush=True)
                else:
                    missed += 1
                if len(updates) >= WRITE_BATCH:
                    flush()
                if done % 25 == 0:
                    print(f"  Progress: {done}/{len(targets)} (found {found}, missed {missed})",
                          flush=True)
        if updates:
            flush()

    print(f"\nDone. found={found}  missed={missed}  of {len(targets)}")


if __name__ == "__main__":
    main()
