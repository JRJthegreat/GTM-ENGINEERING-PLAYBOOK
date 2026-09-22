"""
Find a decision maker + verified email at LARGE healthcare recruitment firms
(200+ employees) via Purple Magic — the band-aware twin of
find_ceo_pm_demand.py.

CLONE, NOT AN EDIT (repo convention). find_ceo_pm_demand.py belongs to the
completed 1-50 demand campaign and its owner-only gate must not be repointed
out from under it. Under 50 employees that gate is still correct and is the
only rung in this repo with campaign evidence behind it.

WHY A DIFFERENT GATE AT 200+ (Jude, 2026-08-12)
-----------------------------------------------
The offer is an introduction to clinics that need recruiters, i.e. NEW
BUSINESS. At a two-person shop the owner does business development
personally. At a 300-person staffing firm business development is a
department, the founder is unreachable, and the person who wants new client
relationships carries a quota. So the ladder below leads with owner, then
goes to new business, then to whoever owns the healthcare desk's P&L.

THE BAN LIST MATTERS MORE THAN THE LADDER HERE
----------------------------------------------
Measured over the 3,966 domains already in pm_dm_cache.jsonl, the most
common title Purple Magic returns at these firms is "certified nursing
assistant" (155), then "recruiter" (151), then "registered nurse" (116).
At a healthcare staffing firm the payroll IS clinicians and delivery
recruiters. Any gate that merely relaxes toward "more titles" emails a
travel nurse on a staffing firm's W-2. Clinical and support titles are
therefore rejected BEFORE any rung is tested, and line recruiters are
rejected by construction: they never match a rung, because every rung is
anchored on a leadership token.

Kept from the parent script unchanged: the gate runs BEFORE the /find call
so wrong titles never spend a lookup; valid-only emails; email domain must
match the company domain; free mailboxes rejected; no name/title without an
email; batch-of-10 writes; resume-safe.

THE AMBIGUOUS MIDDLE GOES TO CLAUDE, NOT TO A REGEX
---------------------------------------------------
Bare "Director", "Manager", "Vice President", "Executive" carry no
information on their own. Unknown-fails throws them away; a looser regex
emails a payroll manager. They are collected to JSON, judged in-session
against the company description, and applied via --verdicts, refusing any
verdict for a domain that was not in the candidates file (same hallucination
guard as enrich_websites_exa.py).

Schema (AI Ark A-N export + the demand track's O-S columns):
  A:Company Name  C:Employee Size  H:Website
  O:dm_name  P:dm_title  Q:dm_email  R:dm_linkedin  S:email_status
  X:dm_rung   <- NEW. Which rung won, so the first campaign on this band
                 produces rung-level reply data. B/C/D are hypotheses with
                 zero evidence; without this column they stay hypotheses.

Run:
  # 1. free replay over the cache — no API calls, no writes
  python3 -W ignore find_dm_large_firms.py --sheet_url "URL" --tab "50-200 EMP" \
      --cache_only --out data/ambiguous.json

  # 2. (Claude judges data/ambiguous.json in-session, writes verdicts JSON)

  # 3. real run, spends PM lookups only on gate survivors
  python3 -W ignore find_dm_large_firms.py --sheet_url "URL" --tab "50-200 EMP" \
      [--verdicts data/verdicts.json] [--limit 50] [--dry_run]
"""

import os
import re
import json
import time
import threading
import argparse
import requests
from urllib.parse import urlparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(SCRIPT_DIR, "..", "..", "..", ".env")
TOKEN_PATH = os.path.join(SCRIPT_DIR, "..", "..", "..", "token.json")
load_dotenv(ENV_PATH)

PM_KEY = os.getenv("PURPLE_MAGIC_KEY")
PM_BASE = "https://api.connector-os.com/api/email/v2"

HOSTING_DOMAINS = {
    "squarespace.com", "wix.com", "wixsite.com", "weebly.com", "wordpress.com",
    "webflow.io", "webflow.com", "godaddy.com", "shopify.com", "myshopify.com",
    "netlify.app", "vercel.app", "github.io", "carrd.co", "strikingly.com",
    "lovable.app", "framer.app", "framer.site", "bubble.io", "glide.page",
    "linktr.ee", "linktree.com", "bio.link", "beacons.ai",
    "mailchimp.com", "hubspot.com", "typeform.com",
}
FREE_MAIL = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com",
             "icloud.com", "protonmail.com", "live.com", "msn.com", "comcast.net"}

LARGE_BANDS = ("201-500", "501-1000", "1001-5000", "5001-10000", "10001+")

# ---------------------------------------------------------------- the gate --
# Rejected before any rung is tested. Clinical staff and placed candidates
# first, then support functions. "Director of Nursing" is clinical ops, not a
# buyer; "Director of Finance and Operations" is finance, not operations.
CLINICAL_RE = re.compile(
    r"\b(nurse|nursing|rn|lpn|lvn|cna|cma|nurse practitioner|practitioner|"
    r"physician|surgeon|dentist|pharmacist|pharmacy|therapist|therapy|"
    r"pathologist|psychologist|counselor|technologist|technician|phlebotom|"
    r"radiolog|respiratory|sonograph|medical assistant|medical coder|"
    r"caregiver|care giver|aide|patient care|clinician|hygienist|paramedic|"
    r"driver|housekeeping|security officer)\b", re.I)
SUPPORT_RE = re.compile(
    r"\b(finance|financial|cfo|controller|accountant|accounting|payroll|"
    r"billing|credentialing|compliance|legal|counsel|attorney|"
    r"human resources|\bhr\b|people operations|marketing|brand|"
    r"information technology|\bit\b|software|engineer|developer|"
    r"administrative assistant|receptionist|office manager|"
    r"customer service|program director|project manager)\b", re.I)
# Junior/qualifier words that disqualify even a leadership-looking title.
NOT_SENIOR_RE = re.compile(
    r"\b(assistant|associate|advisor to|office of|intern|trainee|former|ex[- ])\b",
    re.I)

LEAD = (r"(?:chief|c\.?e\.?o|c\.?o\.?o|vp|v\.p\.|vice president|svp|evp|"
        r"senior vice president|executive vice president|head|director|"
        r"senior director|executive director|managing|general manager|"
        r"regional|divisional|division|branch|market)")

# Ordered ladder. First match wins, so A outranks B outranks C outranks D.
# No trailing \b on the outer group: "director of recruiting" must match the
# recruit- stem without the boundary failing on the following letter.
TIERS = [
    ("A_owner", re.compile(
        r"\b(owner|business owner|co[- ]?founder|founder|ceo|chief executive|"
        r"president|managing director|managing partner|partner|principal|"
        r"chair(?:man|woman|person)?)\b", re.I)),
    ("B_new_business", re.compile(
        r"(chief revenue|chief growth|\bcro\b|business development|"
        r"\bbizdev\b|" + LEAD + r"[^,;|]{0,30}(?:sales|business development|"
        r"partnership|growth|client service|client relation|client success|"
        r"account management|revenue))", re.I)),
    ("C_desk_owner", re.compile(
        r"(" + LEAD + r"[^,;|]{0,30}(?:healthcare|health care|recruit|staffing|"
        r"talent solution|delivery|account)|general manager|regional director|"
        r"regional manager|branch manager|branch director|market director|"
        r"division director|executive director|managing consultant)", re.I)),
    ("D_ops_exec", re.compile(
        r"(chief operating|\bcoo\b|" + LEAD + r"[^,;|]{0,20}operations)", re.I)),
]
# Carries no information on its own. Judged in-session, never by regex.
AMBIGUOUS_RE = re.compile(
    r"^(director|manager|senior manager|vice president|senior vice president|"
    r"vp|executive|head|senior director|leader|lead|team lead|partner manager)$",
    re.I)

RUNG_ORDER = ["A_owner", "B_new_business", "C_desk_owner", "D_ops_exec", "JUDGED"]

COL_NAME = 0           # A
COL_EMPSIZE = 2        # C
COL_WEBSITE = 7        # H
COL_DM_NAME = 14       # O
COL_DM_TITLE = 15      # P
COL_DM_EMAIL = 16      # Q
COL_DM_LINKEDIN = 17   # R
COL_EMAIL_STATUS = 18  # S
COL_RUNG = 23          # X
COL_OUTREACH_FLAG = 25  # Z, written by apply_icp_research.py — KEEP/SKIP_*

WRITE_BATCH = 10


def classify(title):
    """-> rung name | 'AMBIGUOUS' | None (rejected). Bans run first."""
    t = (title or "").strip()
    if not t:
        return None
    if CLINICAL_RE.search(t) or SUPPORT_RE.search(t) or NOT_SENIOR_RE.search(t):
        return None
    for rung, rx in TIERS:
        if rx.search(t):
            return rung
    if AMBIGUOUS_RE.match(t):
        return "AMBIGUOUS"
    return None


def col_letter(idx):
    s, idx = "", idx + 1
    while idx:
        idx, r = divmod(idx - 1, 26)
        s = chr(65 + r) + s
    return s


def parse_sheet_id(url):
    return url.split("/d/")[1].split("/")[0]


def get_service():
    with open(TOKEN_PATH) as f:
        td = json.load(f)
    creds = Credentials(token=td["token"], refresh_token=td["refresh_token"],
                        token_uri=td["token_uri"], client_id=td["client_id"],
                        client_secret=td["client_secret"],
                        scopes=td.get("scopes",
                                      ["https://www.googleapis.com/auth/spreadsheets"]))
    if creds.expired:
        creds.refresh(Request())
        td["token"] = creds.token
        with open(TOKEN_PATH, "w") as f:
            json.dump(td, f)
    return build("sheets", "v4", credentials=creds)


def norm_domain(w):
    w = (w or "").strip().lower()
    if not w:
        return ""
    if not w.startswith("http"):
        w = "https://" + w
    h = urlparse(w).netloc or ""
    h = h[4:] if h.startswith("www.") else h
    if not re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}", h):
        return ""
    root = ".".join(h.split(".")[-2:])
    return "" if root in HOSTING_DOMAINS else h


# Shared with find_ceo_pm_demand.py on purpose: a domain is billed once ever,
# and gate-rule changes replay from the cache instead of re-billing.
CACHE_PATH = os.path.join(SCRIPT_DIR, "..", "data", "pm_dm_cache.jsonl")
_cache = {}
_cache_lock = threading.Lock()


def load_cache():
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH) as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    _cache[rec["domain"]] = rec["response"]
                except (json.JSONDecodeError, KeyError):
                    continue
    print(f"PM cache: {len(_cache)} domains loaded", flush=True)


def cache_put(domain, response):
    with _cache_lock:
        _cache[domain] = response
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        with open(CACHE_PATH, "a") as f:
            f.write(json.dumps({"domain": domain, "response": response,
                                "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}) + "\n")


def pm(endpoint, body):
    for attempt in range(3):
        try:
            r = requests.post(PM_BASE + endpoint,
                              headers={"Authorization": f"Bearer {PM_KEY}",
                                       "Content-Type": "application/json"},
                              json=body, timeout=120)
        except requests.RequestException as e:
            if attempt == 2:
                return {}, f"error:{type(e).__name__}"
            time.sleep(3)
            continue
        if r.status_code == 429:
            time.sleep(15)
            continue
        if r.status_code != 200:
            return {}, f"error:http_{r.status_code}"
        return (r.json() or {}), ""
    return {}, "rate_limited"


def pm_decision_makers(domain, cache_only=False):
    with _cache_lock:
        if domain in _cache:
            return _cache[domain], ""
    if cache_only:
        return {}, "not_cached"
    d, err = pm("/decision-makers", {"domain": domain})
    if not err:
        cache_put(domain, d)
    return d, err


def candidates(resp):
    out, seen = [], set()
    for c in [(resp or {}).get("best") or {}] + ((resp or {}).get("others") or []):
        fn = c.get("fullName")
        if fn and fn not in seen:
            seen.add(fn)
            out.append(c)
    return out


def check_email(email, domain):
    if not email:
        return None
    d = email.split("@")[-1].lower()
    if d in FREE_MAIL:
        return None
    if domain and d != domain and not (d.endswith("." + domain) or domain.endswith("." + d)):
        return None
    return email


def rank_candidates(cands, verdict_name=None):
    """-> [(rung, candidate)] best rung first, plus the ambiguous leftovers."""
    ranked, ambiguous = [], []
    for c in cands:
        rung = classify(c.get("title"))
        if rung == "AMBIGUOUS":
            if verdict_name and (c.get("fullName") or "") == verdict_name:
                ranked.append(("JUDGED", c))
            else:
                ambiguous.append(c)
        elif rung:
            ranked.append((rung, c))
    ranked.sort(key=lambda rc: (RUNG_ORDER.index(rc[0]),
                                -(rc[1].get("seniorityScore") or 0)))
    return ranked, ambiguous


def resolve(target, verdicts, cache_only):
    dom = target["domain"]
    resp, err = pm_decision_makers(dom, cache_only)
    if err:
        return {**target, "status": f"lf_{err}", "rung": ""}
    cands = candidates(resp)
    if not cands:
        return {**target, "status": "lf_no_dm", "rung": ""}
    ranked, ambiguous = rank_candidates(cands, (verdicts or {}).get(dom))
    if not ranked:
        return {**target, "status": "lf_ambiguous" if ambiguous else "lf_no_rung",
                "rung": "", "ambiguous": ambiguous}
    if cache_only:
        return {**target, "status": "would_target", "rung": ranked[0][0],
                "dm_name": ranked[0][1].get("fullName", ""),
                "dm_title": (ranked[0][1].get("title") or "").strip(),
                "ambiguous": ambiguous}
    for rung, c in ranked:
        first, last = c.get("firstName"), c.get("lastName")
        if not (first and last):
            parts = (c.get("fullName") or "").split()
            first, last = (parts[0], parts[-1]) if len(parts) >= 2 else (None, None)
        if not (first and last):
            continue
        d2, err2 = pm("/find", {"firstName": first, "lastName": last, "domain": dom})
        if err2:
            return {**target, "status": f"lf_{err2}", "rung": ""}
        if d2.get("status") != "valid":
            continue
        email = check_email(d2.get("email"), dom)
        if not email:
            continue
        return {**target, "status": "found", "rung": rung,
                "dm_name": c.get("fullName") or f"{first} {last}",
                "dm_title": (c.get("title") or "").strip(),
                "dm_email": email,
                "dm_linkedin": c.get("linkedIn") or ""}
    return {**target, "status": "lf_not_found", "rung": ""}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet_url", required=True)
    ap.add_argument("--tab", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--sizes", default=",".join(LARGE_BANDS),
                    help="Employee Size bands (col C) to target")
    ap.add_argument("--any_size", action="store_true",
                    help="ignore the band filter (e.g. to sweep pm_bad_title rows)")
    ap.add_argument("--retry_rejected", action="store_true", default=True,
                    help="include rows already stamped pm_*/lf_* (cache replays free, but "
                         "lf_not_found retries re-spend a paid /find call for no new benefit "
                         "unless the underlying PM data changed — prefer --only_status for a "
                         "precise retry instead of this blanket flag)")
    ap.add_argument("--only_status", default="",
                    help="comma-separated status values (e.g. lf_error:http_500) — restricts "
                         "retry to exactly these, instead of every non-found row")
    ap.add_argument("--cache_only", action="store_true",
                    help="replay the gate over cached responses; no API calls, no writes")
    ap.add_argument("--out", default="", help="cache_only: write ambiguous pile here")
    ap.add_argument("--verdicts", default="", help="JSON {domain: fullName|\"\"}")
    ap.add_argument("--dry_run", action="store_true")
    ap.add_argument("--require_keep", action="store_true", default=True,
                    help="only target rows column Z tags KEEP (skip if Z is blank/absent)")
    ap.add_argument("--ignore_keep_flag", action="store_true",
                    help="override --require_keep — target by size band alone, ICP tag ignored")
    args = ap.parse_args()

    if not PM_KEY and not (args.dry_run or args.cache_only):
        print("ERROR: PURPLE_MAGIC_KEY not set")
        return

    verdicts = {}
    if args.verdicts:
        with open(args.verdicts) as f:
            verdicts = json.load(f)
        print(f"Verdicts loaded: {len(verdicts)} domains")

    sheet_id = parse_sheet_id(args.sheet_url)
    tab = args.tab
    service = get_service()
    rows = service.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=f"'{tab}'!A:Z").execute().get("values", [])[1:]

    bands = {b.strip() for b in args.sizes.split(",") if b.strip()}
    targets = []
    for i, row in enumerate(rows):
        def cell(idx):
            return (row[idx].strip() if len(row) > idx and row[idx] else "")
        name, size = cell(COL_NAME), cell(COL_EMPSIZE)
        status, email = cell(COL_EMAIL_STATUS), cell(COL_DM_EMAIL)
        domain = norm_domain(cell(COL_WEBSITE))
        if not (name and domain) or email:
            continue
        if not args.any_size and size not in bands:
            continue
        if args.require_keep and not args.ignore_keep_flag:
            if cell(COL_OUTREACH_FLAG) != "KEEP":
                continue
        # Any status other than a successful "found" means the row still has
        # no email, whichever lane stamped it. The AMF lane writes "not found"
        # while the PM lane writes pm_*, so keying on a prefix would silently
        # skip every large firm that was misfiled into the 1-50 tab.
        stale = status.strip().lower() not in ("found",)
        only = {v.strip() for v in args.only_status.split(",") if v.strip()}
        if only:
            if status not in only:
                continue
        elif status and not (args.retry_rejected and stale):
            continue
        targets.append({"row": i + 2, "name": name, "domain": domain, "size": size})
    if args.limit:
        targets = targets[:args.limit]

    print(f"=== Large-firm DM (Purple Magic, tiered gate) — tab '{tab}' ===")
    load_cache()
    print(f"Rows in scope: {len(targets)}", flush=True)
    if args.dry_run:
        for t in targets[:15]:
            print(f"  row{t['row']:5d} {t['name'][:38]:38s} {t['size']:<10} {t['domain']}")
        return

    # ---------------- cache-only replay: free, read-only, no PM calls --------
    if args.cache_only:
        hist, amb_out = Counter(), []
        for t in targets:
            res = resolve(t, verdicts, cache_only=True)
            key = res["rung"] if res["status"] == "would_target" else res["status"]
            hist[key] += 1
            for c in res.get("ambiguous") or []:
                amb_out.append({"domain": t["domain"], "company": t["name"],
                                "size": t["size"], "row": t["row"],
                                "full_name": c.get("fullName"),
                                "title": c.get("title"),
                                "seniority": c.get("seniorityScore")})
        print("\n--- replay over cache (no spend) ---")
        for k in RUNG_ORDER + ["lf_ambiguous", "lf_no_rung", "lf_no_dm", "lf_not_cached"]:
            if hist.get(k):
                print(f"  {k:<16} {hist[k]}")
        reachable = sum(hist.get(r, 0) for r in RUNG_ORDER)
        print(f"  >>> would target: {reachable} of {len(targets)}")
        if args.out and amb_out:
            os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
            with open(args.out, "w") as f:
                json.dump(amb_out, f, indent=1)
            print(f"  ambiguous pile -> {args.out} ({len(amb_out)} people)")
        return

    # ---------------- real run ---------------------------------------------
    lock = threading.Lock()
    updates, found, missed = [], 0, 0
    rungs = Counter()

    def ensure_rung_column():
        meta = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
        for s in meta["sheets"]:
            if s["properties"]["title"] != tab:
                continue
            have = s["properties"]["gridProperties"]["columnCount"]
            if have < COL_RUNG + 1:
                service.spreadsheets().batchUpdate(
                    spreadsheetId=sheet_id,
                    body={"requests": [{"appendDimension": {
                        "sheetId": s["properties"]["sheetId"], "dimension": "COLUMNS",
                        "length": (COL_RUNG + 1) - have}}]}).execute()
            service.spreadsheets().values().update(
                spreadsheetId=sheet_id,
                range=f"'{tab}'!{col_letter(COL_RUNG)}1",
                valueInputOption="RAW", body={"values": [["dm_rung"]]}).execute()

    ensure_rung_column()

    def flush():
        data = []
        for u in updates:
            r = u["row"]
            if u["status"] == "found":
                data += [
                    {"range": f"'{tab}'!{col_letter(COL_DM_NAME)}{r}",
                     "values": [[u["dm_name"]]]},
                    {"range": f"'{tab}'!{col_letter(COL_DM_TITLE)}{r}",
                     "values": [[u["dm_title"]]]},
                    {"range": f"'{tab}'!{col_letter(COL_DM_EMAIL)}{r}",
                     "values": [[u["dm_email"]]]},
                    {"range": f"'{tab}'!{col_letter(COL_EMAIL_STATUS)}{r}",
                     "values": [["found"]]},
                    {"range": f"'{tab}'!{col_letter(COL_RUNG)}{r}",
                     "values": [[u["rung"]]]},
                ]
                if u.get("dm_linkedin"):
                    data.append({"range": f"'{tab}'!{col_letter(COL_DM_LINKEDIN)}{r}",
                                 "values": [[u["dm_linkedin"]]]})
            else:
                data.append({"range": f"'{tab}'!{col_letter(COL_EMAIL_STATUS)}{r}",
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
        futures = [ex.submit(resolve, t, verdicts, False) for t in targets]
        done = 0
        for fut in as_completed(futures):
            res = fut.result()
            with lock:
                updates.append(res)
                done += 1
                if res["status"] == "found":
                    found += 1
                    rungs[res["rung"]] += 1
                    print(f"  +  [{res['rung']:<14}] {res['name'][:34]:34s} -> "
                          f"{res['dm_name']} ({res['dm_title']}) | {res['dm_email']}",
                          flush=True)
                else:
                    missed += 1
                if len(updates) >= WRITE_BATCH:
                    flush()
                if done % 50 == 0:
                    print(f"  Progress: {done}/{len(targets)} "
                          f"(found {found}, missed {missed})", flush=True)
        if updates:
            flush()

    print(f"\nDone. found={found}  missed={missed}  of {len(targets)}")
    print(f"By rung: {dict(rungs)}")


if __name__ == "__main__":
    main()
