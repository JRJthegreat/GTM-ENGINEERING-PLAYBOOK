"""
Phase 2 — resolve the company domain for a HireBase lane tab.

WHY THIS IS NOT `find_company_domains.py` OR `exa-website-enrichment` (Jude,
2026-08-19). On an Indeed scrape the domain is missing and every company needs
a paid Google/Exa lookup. HireBase is a different platform: measured on the
Aug 19th sheet, 464 of 468 companies ALREADY carry a website and 468 of 468
carry a company LinkedIn URL. So search is a LAST resort here, not phase 1.9.

THE REAL RISK ON THIS PLATFORM IS A WRONG DOMAIN, NOT A MISSING ONE. 107 of
468 companies carry a domain whose root does not correspond to the company
name. Some are genuine rebrands (The Shandy Clinic -> wovencare.com), some are
a PARENT's domain (Bon Secours Home Care -> compassus.com, which would send
AMF hunting for Compassus staff), and some are simply wrong (Epyz ->
solarfornature.com, a solar company, on 13 rows). A wrong domain does not fail
loudly — it emails a real person at the wrong company. So a sheet domain is
only trusted when it corroborates the company name; otherwise LinkedIn adjudicates.

WATERFALL, per company (not per row — a company resolves once and the answer
is written to all of its rows):

  Tier 1  sheet domain, FREE. Accept col L when its root corroborates the
          company name (squish / substring / token-overlap / acronym).
          -> domain_status = sheet_domain
  Tier 2  LinkedIn, ~1 Apify call. Runs ONLY for companies with no domain, or
          whose sheet domain failed Tier 1. The company's own LinkedIn page
          states its website, so it adjudicates.
          -> linkedin_confirmed  (LinkedIn agrees with the sheet)
             linkedin_corrected  (LinkedIn disagrees — LinkedIn wins)
             linkedin_filled     (sheet was empty, LinkedIn supplied one)
             linkedin_no_website (page carries no website -> Tier 3)
  Tier 3  the old Google-search resolution, explicitly allowed as the last
          resort. This script does NOT implement it — it stamps needs_search
          and prints the `exa-website-enrichment` command for those rows.

COL L IS ONLY OVERWRITTEN WHEN LINKEDIN SUPPLIES A BETTER ANSWER
(linkedin_corrected / linkedin_filled). A Tier 1 pass leaves the cell alone.
The pre-existing value is preserved in AU (domain_note) on every correction,
and the untouched raw export tabs remain the ultimate backstop.

Writes: L domain, AT domain_status, AU domain_note.
Batch-of-10 company writes. Resume-safe: a company with AT already set is
skipped unless --retry. Every verdict is appended to data/domain_log.jsonl the
moment it is produced, and the log is replayed on startup, so a company is
never re-billed across runs or lanes (the two lanes share 45 companies).

Run:
  python3 -W ignore resolve_domains.py --sheet_url URL --tab "General Campaign"
  python3 -W ignore resolve_domains.py --sheet_url URL --tab "General Campaign" --apply
"""

import os
import re
import json
import argparse
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(SCRIPT_DIR, "..", "..", "..", ".env"))
TOKEN_PATH = os.path.join(SCRIPT_DIR, "..", "..", "..", "token.json")
DATA_DIR = os.path.join(SCRIPT_DIR, "..", "data")
LOG_PATH = os.path.join(DATA_DIR, "domain_log.jsonl")

APIFY_TOKEN = os.getenv("APIFY_API_TOKEN")
ACTOR = "pratikdani~linkedin-company-profile-scraper"
SYNC_URL = f"https://api.apify.com/v2/acts/{ACTOR}/run-sync-get-dataset-items"

COL_COMPANY = 10    # K
COL_WEBSITE = 11    # L
COL_LINKEDIN = 40   # AO
COL_DSTATUS = 45    # AT
COL_DNOTE = 46      # AU
BATCH = 10

# Hosts that are never a company's own site.
JUNK_HOST_RE = re.compile(
    r"(linkedin\.com|facebook\.com|instagram\.com|twitter\.com|x\.com|"
    r"youtube\.com|indeed\.com|glassdoor\.|ziprecruiter\.|monster\.|"
    r"greenhouse\.io|lever\.co|workday|paylocity|bamboohr|jazzhr|smartrecruiters|"
    r"myworkdayjobs|icims\.com|oraclecloud\.com|adp\.com|paycom|ukg\.|"
    r"rippling\.com|workable\.com|breezy\.hr|recruitee\.com|ashbyhq\.com|"
    r"givelively\.org|donorbox\.org|classy\.org|gofundme\.com|"
    r"networkforgood|paypal\.com|givebutter\.com|"
    r"bit\.ly|goo\.gl|google\.com|wix\.com|squarespace\.com|weebly\.com)",
    re.IGNORECASE)

LEGAL_RE = re.compile(
    r"\b(inc|llc|pllc|plc|pc|llp|lp|ltd|limited|corp|corporation|co|company|"
    r"group|holdings|the|and|of|at|for)\b", re.IGNORECASE)
GENERIC_RE = re.compile(
    r"\b(health|healthcare|medical|medicine|clinic|clinics|practice|practices|"
    r"center|centers|centre|centres|care|services|service|associates|partners|"
    r"therapy|therapies|rehab|rehabilitation|hospital|hospitals|home|solutions|"
    r"systems|system|network|institute|physicians|physician|nursing|senior|"
    r"living|family|pediatric|pediatrics|behavioral|wellness)\b", re.IGNORECASE)

MULTI_TLD = ("co.uk", "com.au", "co.nz", "org.uk", "ac.uk", "com.br", "co.za")


def sheet_id_of(url):
    return re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url).group(1)


def host_of(url):
    if not url:
        return ""
    u = url.strip()
    if not u.startswith(("http://", "https://")):
        u = "https://" + u
    try:
        h = urlparse(u).netloc.lower()
    except ValueError:
        return ""
    return h[4:] if h.startswith("www.") else h


def domain_root(url):
    """'www.ivyrehab.com' -> 'ivyrehab'; 'foo.co.uk' -> 'foo'."""
    h = host_of(url)
    if not h:
        return ""
    for t in MULTI_TLD:
        if h.endswith("." + t):
            return h[: -(len(t) + 1)].split(".")[-1]
    parts = h.split(".")
    return parts[-2] if len(parts) >= 2 else parts[0]


def squish(name):
    n = GENERIC_RE.sub(" ", LEGAL_RE.sub(" ", name or ""))
    return re.sub(r"[^a-z0-9]", "", n.lower())


def core(name):
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def tokens(name):
    raw = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower()).split()
    return [t for t in raw if len(t) >= 4
            and not LEGAL_RE.fullmatch(t) and not GENERIC_RE.fullmatch(t)]


def acronym(name):
    raw = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower()).split()
    keep = [t for t in raw if not LEGAL_RE.fullmatch(t)]
    return "".join(t[0] for t in keep if t)


def name_matches_domain(name, url):
    """Does this domain plausibly belong to this company? Conservative on
    purpose — a false ACCEPT emails the wrong company, a false REJECT only
    costs one LinkedIn call."""
    root = domain_root(url)
    if not root or not name:
        return False
    c, s = core(name), squish(name)
    if s and (s in root or root in s):
        return True
    if c and (c in root or root in c):
        return True
    toks = tokens(name)
    if toks:
        hit = sum(1 for t in toks if t in root)
        if hit / len(toks) >= 0.5:
            return True
    a = acronym(name)
    if len(a) >= 3 and (root.startswith(a) or a == root):
        return True
    return False



def decide(name, old, li_site):
    """Choose between the sheet's domain and LinkedIn's.

    LinkedIn is NOT trusted blindly — it hands back donation pages and ATS
    portals. Two real misfires on the first Aug 19th run: Edward M. Kennedy
    Community Health Center's correct kennedychc.org was replaced by a
    GiveLively donate URL, and Ste. Genevieve County Memorial Hospital's
    plausible stegenevievehospital.org was replaced by ste-bv.com. Both were
    strictly worse than what the sheet already had.

    Rule: a replacement must be non-junk AND corroborate the company name.
    When neither candidate corroborates, KEEP the sheet's and flag it for
    review rather than churn to an unproven domain. Unchanged beats wrong."""
    li_ok = bool(li_site) and not JUNK_HOST_RE.search(li_site)
    old_ok = bool(old) and not JUNK_HOST_RE.search(old)
    li_match = li_ok and name_matches_domain(name, li_site)
    old_match = old_ok and name_matches_domain(name, old)

    if not old_ok and not li_ok:
        return ("linkedin_no_website" if not old else "needs_search",
                old if old_ok else "",
                "no usable domain from sheet or linkedin")
    if not old_ok and li_ok:
        return ("linkedin_filled" if not old else "linkedin_corrected",
                li_site, "sheet had no domain" if not old else f"was {old} (junk)")
    if old_ok and not li_ok:
        return "linkedin_junk_kept_sheet", old, "linkedin gave a junk/absent site"
    if domain_root(li_site) == domain_root(old):
        return "linkedin_confirmed", old, ""
    if li_match and not old_match:
        return "linkedin_corrected", li_site, f"was {old}"
    if old_match and not li_match:
        return "sheet_domain_kept", old, f"linkedin said {li_site}, unproven"
    return ("linkedin_disagreed_review", old,
            f"linkedin said {li_site}; neither corroborates the name")


def scrape_linkedin(li_url, timeout=90):
    try:
        r = requests.post(SYNC_URL, params={"token": APIFY_TOKEN, "limit": 1},
                          json={"url": li_url}, timeout=timeout)
    except requests.RequestException:
        return None
    if r.status_code not in (200, 201):
        return None
    try:
        data = r.json()
    except ValueError:
        return None
    if not data or data[0].get("error"):
        return None
    return (data[0].get("website") or "").strip()


def load_log():
    seen = {}
    if os.path.exists(LOG_PATH):
        with open(LOG_PATH) as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                seen[rec["company"]] = rec
    return seen


def append_log(rec):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(rec) + "\n")


def a1(col, row):
    s, c = "", col + 1
    while c:
        c, r = divmod(c - 1, 26)
        s = chr(65 + r) + s
    return f"{s}{row}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet_url", required=True)
    ap.add_argument("--tab", required=True)
    ap.add_argument("--apply", action="store_true",
                    help="Write to the sheet. Without it, report only.")
    ap.add_argument("--limit", type=int, default=0, help="Cap companies processed.")
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--only", action="append", default=[],
                    help="Re-resolve just these company names (repeatable). "
                         "Bypasses the durable log and the sheet status.")
    ap.add_argument("--retry", action="store_true",
                    help="Redo companies that already have a domain_status.")
    args = ap.parse_args()

    sid = sheet_id_of(args.sheet_url)
    svc = build("sheets", "v4",
                credentials=Credentials.from_authorized_user_file(TOKEN_PATH))
    vals = svc.spreadsheets().values().get(
        spreadsheetId=sid, range=f"'{args.tab}'!A1:AV").execute().get("values", [])
    hdr, rows = vals[0], vals[1:]

    def cell(r, i):
        return r[i].strip() if i < len(r) else ""

    # Group rows by company.
    groups = {}
    for i, r in enumerate(rows):
        c = cell(r, COL_COMPANY)
        if not c:
            continue
        g = groups.setdefault(c, {"rows": [], "site": "", "li": "", "status": ""})
        g["rows"].append(i + 2)
        g["site"] = g["site"] or cell(r, COL_WEBSITE)
        g["li"] = g["li"] or cell(r, COL_LINKEDIN)
        g["status"] = g["status"] or cell(r, COL_DSTATUS)

    log = load_log()
    print(f"[{args.tab}] {len(rows)} rows / {len(groups)} companies "
          f"({len(log)} in durable log)")

    tier1, need_li, pending = [], [], []
    for name, g in groups.items():
        forced = name in args.only
        if g["status"] and not args.retry and not forced:
            continue
        if name in log and not args.retry and not forced:
            pending.append((name, g, log[name]["status"], log[name]["domain"],
                            log[name].get("note", "")))
            continue
        site = g["site"]
        if site and not JUNK_HOST_RE.search(site) and name_matches_domain(name, site):
            tier1.append((name, g))
        else:
            need_li.append((name, g))

    print(f"  tier 1 (sheet domain corroborates name, free): {len(tier1)}")
    print(f"  tier 2 (needs LinkedIn adjudication):          {len(need_li)}")
    print(f"  replayed from durable log (no spend):          {len(pending)}")

    for name, g in tier1:
        rec = {"company": name, "status": "sheet_domain",
               "domain": g["site"], "note": ""}
        pending.append((name, g, rec["status"], rec["domain"], rec["note"]))
        append_log(rec)

    if args.limit:
        need_li = need_li[: args.limit]

    if need_li:
        if not APIFY_TOKEN:
            raise SystemExit("APIFY_API_TOKEN missing — cannot run tier 2.")
        print(f"\n  scraping {len(need_li)} LinkedIn company pages "
              f"({args.workers} workers)...")
        done = 0
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(scrape_linkedin, g["li"]): (name, g)
                    for name, g in need_li if g["li"]}
            for fut in as_completed(futs):
                name, g = futs[fut]
                done += 1
                li_site = fut.result()
                old = g["site"]
                status, domain, note = decide(name, old, li_site)
                rec = {"company": name, "status": status,
                       "domain": domain, "note": note}
                append_log(rec)
                pending.append((name, g, status, domain, note))
                print(f"    [{done}/{len(futs)}] {name[:38]:38s} {status:20s} "
                      f"{(domain or '(none)')[:44]}")

    # Companies with no LinkedIn at all and a failed sheet domain.
    for name, g in need_li:
        if not g["li"]:
            rec = {"company": name, "status": "needs_search",
                   "domain": g["site"], "note": "no linkedin url"}
            append_log(rec)
            pending.append((name, g, rec["status"], rec["domain"], rec["note"]))

    from collections import Counter
    print("\n  RESULT by status:")
    for s, n in Counter(p[2] for p in pending).most_common():
        print(f"    {s:22s} {n:5d} companies")
    corrected = [p for p in pending if p[2] == "linkedin_corrected"]
    if corrected:
        print(f"\n  {len(corrected)} DOMAIN CORRECTIONS (col L will change):")
        for name, g, st, dom, note in sorted(
                corrected, key=lambda x: -len(x[1]["rows"]))[:20]:
            print(f"    {len(g['rows']):4d} rows  {name[:34]:34s} "
                  f"{note} -> {dom}")
    search = [p for p in pending if p[2] in ("needs_search", "linkedin_no_website")]
    if search:
        print(f"\n  {len(search)} companies still unresolved -> tier 3 "
              f"(exa-website-enrichment):")
        for name, g, st, dom, note in search[:15]:
            print(f"    {len(g['rows']):4d} rows  {name[:40]:40s} {st}")

    if not args.apply:
        print("\n  (no --apply — nothing written)")
        return

    # Ensure AT/AU headers exist.
    if len(hdr) <= COL_DNOTE or not hdr[COL_DSTATUS]:
        svc.spreadsheets().values().update(
            spreadsheetId=sid, range=f"'{args.tab}'!{a1(COL_DSTATUS,1)}",
            valueInputOption="RAW",
            body={"values": [["domain_status", "domain_note"]]}).execute()

    updates, written = [], 0
    for name, g, status, domain, note in pending:
        for rn in g["rows"]:
            if status in ("linkedin_corrected", "linkedin_filled") and domain:
                updates.append({"range": f"'{args.tab}'!{a1(COL_WEBSITE, rn)}",
                                "values": [[domain]]})
            updates.append({"range": f"'{args.tab}'!{a1(COL_DSTATUS, rn)}",
                            "values": [[status, note]]})
        written += 1
        if written % BATCH == 0:
            svc.spreadsheets().values().batchUpdate(
                spreadsheetId=sid,
                body={"valueInputOption": "RAW", "data": updates}).execute()
            print(f"  written {written}/{len(pending)} companies")
            updates = []
    if updates:
        svc.spreadsheets().values().batchUpdate(
            spreadsheetId=sid,
            body={"valueInputOption": "RAW", "data": updates}).execute()
    print(f"  DONE — {written} companies written to {args.tab!r}")


if __name__ == "__main__":
    main()
