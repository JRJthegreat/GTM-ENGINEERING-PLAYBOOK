"""
Stage 2 of the trades DM waterfall — Apollo People Search, judged by Claude.

Jude's waterfall (2026-09-10):
  1. Purple Magic /decision-makers -> judge -> /find        (pm_dm_judge.py)
  2. THIS: Apollo free people search -> judge -> Google      de-obfuscation
     -> Purple Magic /find -> AMF /find-email/person fallback
  3. AMF /decision-maker category=ceo for whatever is left   (no judge needed:
     AMF picks the person itself, measured 94.9% owner-like)

Apollo's free endpoint obfuscates surnames ("Wo***e" = 2 prefix letters,
3 literal asterisks, last letter), so the full name is recovered by Googling
first name + company + title and matching a linkedin.com/in result against
the prefix/suffix mask. That is Jude's proven flow, reused verbatim from
healthcare-staffing-enrichment/find_dm_apollo_pm.py.

WHAT IS DIFFERENT FROM THAT SCRIPT
  It ranked candidates with find_dm_large_firms' regex ladder. Here the
  ranking is a Claude-in-session judgement instead, because Apollo returns a
  LIST and picking the owner off a list is a judgement, not a pattern
  (Jude, 2026-09-10). Apollo search is free, so collecting costs nothing.

TARGET: owner / CEO / partner / founder / president ONLY. No BD, no VP
grades, no ops, no branch or account managers.

  1. COLLECT  --collect   Apollo search per domain (FREE) -> candidates JSON
  2. JUDGE    Claude writes verdicts: [{"row":,"tab":,"pick":"First Obf"}]
  3. APPLY    --verdicts  Google de-obfuscate -> PM /find -> AMF person

Statuses (col S): ap_* so this lane stays distinguishable from pm_*.

Run:
  python3 -W ignore apollo_dm_judge.py --sheet_url URL --collect --out c.json
  python3 -W ignore apollo_dm_judge.py --sheet_url URL --candidates c.json \
      --verdicts v.json --apply
"""
import os, re, sys, json, time, argparse, requests
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(SCRIPT_DIR, "..", "..", "..", ".env"))
TOKEN_PATH = os.path.join(SCRIPT_DIR, "..", "..", "..", "token.json")
APOLLO_KEY = os.getenv("APOLLO_API_KEY")
APIFY_TOKEN = os.getenv("APIFY_API_TOKEN")
PM_KEY = os.getenv("PURPLE_MAGIC_KEY")
AMF_KEY = os.getenv("ANYMAILFINDER_API_KEY")

APOLLO_URL = "https://api.apollo.io/api/v1/mixed_people/api_search"
GOOGLE_ACTOR = ("https://api.apify.com/v2/acts/apify~google-search-scraper"
                "/run-sync-get-dataset-items")
PM_BASE = "https://api.connector-os.com/api/email/v2"
AMF_PERSON = "https://api.anymailfinder.com/v5.1/find-email/person"

TABS = ["1-50 EMP", "50-200 EMP"]
COL_NAME, COL_WEBSITE = 0, 7
COL_DM_EMAIL, COL_STATUS = 16, 18
WRITE_BATCH = 10
FREE_MAIL = {"gmail.com","yahoo.com","hotmail.com","outlook.com","aol.com",
             "icloud.com","live.com","msn.com","protonmail.com","gmx.com"}


def svc():
    td = json.load(open(TOKEN_PATH))
    c = Credentials(token=td["token"], refresh_token=td["refresh_token"],
                    token_uri=td["token_uri"], client_id=td["client_id"],
                    client_secret=td["client_secret"], scopes=td["scopes"])
    if not c.valid:
        c.refresh(Request()); td["token"] = c.token; json.dump(td, open(TOKEN_PATH, "w"))
    return build("sheets", "v4", credentials=c)


def sheet_id(u):
    m = re.search(r"/d/([a-zA-Z0-9-_]+)", u); return m.group(1) if m else u


def norm_domain(url):
    u = (url or "").strip().lower()
    if not u: return ""
    if not u.startswith(("http://", "https://")): u = "http://" + u
    h = (urlparse(u).hostname or "").lower()
    h = h[4:] if h.startswith("www.") else h
    return h if re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}", h) else ""


def parse_obf(o):
    m = re.match(r"^([A-Za-z]+)\*+([A-Za-z]+)$", (o or "").strip())
    return (m.group(1), m.group(2)) if m else (None, None)


def surname_matches(s, pre, suf):
    s = (s or "").lower()
    return len(s) >= len(pre)+len(suf) and s.startswith(pre.lower()) and s.endswith(suf.lower())


def apollo_search(domain):
    for attempt in (1, 2):
        try:
            r = requests.post(APOLLO_URL,
                              headers={"x-api-key": APOLLO_KEY, "Content-Type": "application/json"},
                              json={"q_organization_domains_list": [domain],
                                    "page": 1, "per_page": 100}, timeout=60)
        except requests.RequestException:
            return []
        if r.status_code == 429 and attempt == 1:
            time.sleep(60); continue
        if r.status_code != 200:
            return []
        return r.json().get("people", []) or []
    return []


def google_deobfuscate(first, last_obf, title, company):
    pre, suf = parse_obf(last_obf)
    if not first or not pre:
        return None, None
    tshort = " ".join((title or "").split()[:4])
    try:
        r = requests.post(GOOGLE_ACTOR, params={"token": APIFY_TOKEN},
                          json={"queries": f"{first} {company} {tshort} site:linkedin.com/in",
                                "resultsPerPage": 8, "maxPagesPerQuery": 1,
                                "languageCode": "en", "countryCode": "us",
                                "includeUnfilteredResults": False}, timeout=180)
    except requests.RequestException:
        return None, None
    if r.status_code not in (200, 201):
        return None, None
    ctok = {t for t in re.split(r"\W+", company.lower()) if len(t) > 2}
    for item in r.json():
        for res in item.get("organicResults", []):
            url = res.get("url", "")
            if "linkedin.com/in/" not in url:
                continue
            blob = (res.get("title","") + " " + res.get("description","")).lower()
            slug = urlparse(url).path.split("/in/")[-1].strip("/")
            stok = [re.sub(r"\d+$", "", t) for t in slug.split("-") if t]
            cands = []
            if stok and stok[0].lower() == first.lower():
                cands.append("".join(stok[1:]) if len(stok) == 2 else (stok[1] if len(stok) > 1 else ""))
            ttext = res.get("title","").split(" - ")[0].split(" | ")[0].split(" – ")[0]
            words = [re.sub(r"[^A-Za-z'-]", "", w) for w in ttext.split()]
            words = [w for w in words if w]
            if len(words) >= 2 and words[0].lower() == first.lower():
                cands.append(words[-1])
            for c in cands:
                if c and surname_matches(c, pre, suf):
                    if any(t in blob for t in ctok) or any(t in blob for t in tshort.lower().split() if len(t) > 3):
                        return c.title(), url.split("?")[0]
    return None, None


def check_email(email, domain):
    e = (email or "").strip().lower()
    if not e or "@" not in e: return ""
    d = e.split("@")[-1]
    if d in FREE_MAIL: return ""
    return e if d.split(".")[-2:] == domain.split(".")[-2:] else ""


def pm_find(first, last, domain):
    try:
        r = requests.post(PM_BASE + "/find",
                          headers={"Authorization": f"Bearer {PM_KEY}",
                                   "Content-Type": "application/json"},
                          json={"firstName": first, "lastName": last, "domain": domain},
                          timeout=120)
    except requests.RequestException:
        return ""
    if r.status_code != 200: return ""
    d = r.json() or {}
    if (d.get("status") or "").lower() != "valid": return ""
    return check_email(d.get("email"), domain)


def amf_person(full_name, domain):
    try:
        r = requests.post(AMF_PERSON,
                          headers={"Authorization": AMF_KEY, "Content-Type": "application/json"},
                          json={"full_name": full_name, "domain": domain}, timeout=120)
    except requests.RequestException:
        return ""
    if r.status_code != 200: return ""
    d = r.json() or {}
    if d.get("email_status") != "valid": return ""
    return check_email(d.get("email") or d.get("valid_email"), domain)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet_url", required=True)
    ap.add_argument("--collect", action="store_true")
    ap.add_argument("--out", default="apollo_candidates.json")
    ap.add_argument("--candidates"); ap.add_argument("--verdicts")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    s, sid = svc(), sheet_id(a.sheet_url)
    rows_by_tab = {t: s.spreadsheets().values().get(
        spreadsheetId=sid, range=f"'{t}'!A1:S100000").execute().get("values", [])[1:] for t in TABS}

    if a.collect:
        todo = []
        for tab, rows in rows_by_tab.items():
            for i, r in enumerate(rows):
                def g(x): return (r[x].strip() if len(r) > x and r[x] else "")
                if g(COL_DM_EMAIL): continue
                dom = norm_domain(g(COL_WEBSITE))
                if not dom: continue
                todo.append({"row": i+2, "tab": tab, "company": g(COL_NAME), "domain": dom})
        if a.limit: todo = todo[:a.limit]
        print(f"rows for Apollo: {len(todo)}", flush=True)

        out, none_ct = [], 0
        def work(t):
            return t, apollo_search(t["domain"])
        with ThreadPoolExecutor(max_workers=a.workers) as ex:
            for k, f in enumerate(as_completed([ex.submit(work, t) for t in todo]), 1):
                t, people = f.result()
                if k % 50 == 0: print(f"  {k}/{len(todo)}", flush=True)
                cands = []
                for p in people:
                    fn = (p.get("first_name") or "").strip()
                    ob = (p.get("last_name_obfuscated") or "").strip()
                    ti = (p.get("title") or "").strip()
                    if not fn or not ob: continue
                    cands.append({"first": fn, "obf": ob, "title": ti,
                                  "key": f"{fn} {ob}"})
                if cands: out.append({**t, "candidates": cands})
                else: none_ct += 1
        json.dump(out, open(a.out, "w"), indent=1)
        print(f"\nwith candidates: {len(out)}   no people: {none_ct}\n-> {a.out}")
        return

    collected = {(c["tab"], c["row"]): c for c in json.load(open(a.candidates))}
    verdicts = json.load(open(a.verdicts))
    picks, refused = [], 0
    for v in verdicts:
        rec = collected.get((v["tab"], v["row"]))
        if rec is None: refused += 1; continue
        want = (v.get("pick") or "").strip()
        if not want: continue
        m = next((p for p in rec["candidates"] if p["key"].lower() == want.lower()), None)
        if m is None:
            print(f'  [!] {rec["company"]}: "{want}" not a candidate — refused'); refused += 1; continue
        picks.append({**rec, "person": m})
    print(f"picks: {len(picks)}   refused: {refused}")
    if not a.apply:
        for p in picks[:40]:
            print(f'  DRY {p["company"][:34]:34s} {p["person"]["key"]} ({p["person"]["title"]})')
        print("\n(dry run — add --apply)"); return

    updates = {t: [] for t in TABS}; found = 0

    def resolve(p):
        per, dom = p["person"], p["domain"]
        surname, li = google_deobfuscate(per["first"], per["obf"], per["title"], p["company"])
        if not surname:
            return p, None, "ap_deobf_failed"
        full = f'{per["first"]} {surname}'
        email = pm_find(per["first"], surname, dom)
        src = "pm"
        if not email:
            email = amf_person(full, dom); src = "amf"
        if not email:
            return p, None, "ap_not_found"
        return p, {"name": full, "title": per["title"], "email": email,
                   "linkedin": li or "", "src": src}, "found"

    def flush():
        data = []
        for tab, ups in updates.items():
            for u in ups:
                data.append({"range": f"'{tab}'!O{u['row']}:S{u['row']}", "values": [u["vals"]]})
            ups.clear()
        if data:
            s.spreadsheets().values().batchUpdate(
                spreadsheetId=sid, body={"valueInputOption": "RAW", "data": data}).execute()

    pending = 0
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for k, f in enumerate(as_completed([ex.submit(resolve, p) for p in picks]), 1):
            p, hit, status = f.result()
            if hit:
                found += 1
                updates[p["tab"]].append({"row": p["row"],
                    "vals": [hit["name"], hit["title"], hit["email"], hit["linkedin"], "found"]})
                print(f'  +  {p["company"][:34]:34s} -> {hit["name"]} ({hit["title"][:30]}) | {hit["email"]} [{hit["src"]}]', flush=True)
            else:
                updates[p["tab"]].append({"row": p["row"], "vals": ["", "", "", "", status]})
            pending += 1
            if pending >= WRITE_BATCH: flush(); pending = 0
    flush()
    print(f"\nDone. found={found} / {len(picks)}")


if __name__ == "__main__":
    main()
