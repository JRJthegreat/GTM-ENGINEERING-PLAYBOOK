"""
Purple Magic DM discovery for the trades lane, as a Claude-in-session judge
flow instead of a regex gate (Jude, 2026-09-10: "don't use regex, use the LLM
to judge").

WHY
---
The regex version (find_dm_pm_trades.py) stamped 269 rows pm_bad_title —
companies where PM DID return people and a pattern threw them all away.
"Executive Director" at a 10-person staffing firm is usually the owner;
"Vice President of Professional Services" is not; no regex separates those
without a list of exceptions that grows forever.

Per Jude's split of the waterfall: PM and Apollo hand back a LIST of people,
so the choice is a judgement. AMF /decision-maker picks one person under a
category and needs no judge.

THREE STEPS (same shape as classify_healthcare_icp.py)
  1. COLLECT  --collect   replay the PM cache (free) + fetch uncached domains,
                          emit every candidate person per company to JSON.
                          NO /find calls, so no email spend happens here.
  2. JUDGE    Claude reads that file in-session and writes verdicts JSON:
                [{"row": 12, "tab": "1-50 EMP", "pick": "Jane Doe"}, ...]
              pick == "" means nobody on the list has ownership authority.
  3. APPLY    --verdicts  PM /find for judged picks only -> valid-only email
                          -> sheet. Refuses any pick not in that row's
                          candidate list (hallucination guard).

TARGET: Jude's rule for this lane is owner / CEO / partner ONLY. No BD, no
VP grades, no ops, no branch managers.

Schema (A-based): A:Company H:Website O:dm_name P:dm_title Q:dm_email
R:dm_linkedin S:email_status

Run:
  python3 -W ignore pm_dm_judge.py --sheet_url URL --collect --out cands.json
  python3 -W ignore pm_dm_judge.py --sheet_url URL --candidates cands.json \
      --verdicts verdicts.json --apply
"""
import os, re, sys, json, time, threading, argparse, requests
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(SCRIPT_DIR, "..", "..", "..", ".env"))
TOKEN_PATH = os.path.join(SCRIPT_DIR, "..", "..", "..", "token.json")
PM_KEY = os.getenv("PURPLE_MAGIC_KEY")
PM_BASE = "https://api.connector-os.com/api/email/v2"
CACHE_PATH = os.path.join(SCRIPT_DIR, "..", "data", "pm_dm_cache.jsonl")
TABS = ["1-50 EMP", "50-200 EMP"]
COL_NAME, COL_WEBSITE = 0, 7
COL_DM_NAME, COL_DM_TITLE, COL_DM_EMAIL, COL_DM_LI, COL_STATUS = 14, 15, 16, 17, 18
WRITE_BATCH = 10

FREE_MAIL = {"gmail.com","yahoo.com","hotmail.com","outlook.com","aol.com",
             "icloud.com","live.com","msn.com","protonmail.com","gmx.com"}
_cache, _lock = {}, threading.Lock()


def load_cache():
    if os.path.exists(CACHE_PATH):
        for line in open(CACHE_PATH):
            try:
                r = json.loads(line); _cache[r["domain"]] = r["response"]
            except Exception:
                pass
    return len(_cache)


def cache_put(dom, resp):
    with _lock:
        _cache[dom] = resp
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        with open(CACHE_PATH, "a") as f:
            f.write(json.dumps({"domain": dom, "response": resp,
                                "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}) + "\n")


def pm(endpoint, body):
    for attempt in range(3):
        try:
            r = requests.post(PM_BASE + endpoint,
                              headers={"Authorization": f"Bearer {PM_KEY}",
                                       "Content-Type": "application/json"},
                              json=body, timeout=120)
        except requests.RequestException:
            if attempt == 2:
                return {}, "error:network"
            time.sleep(3); continue
        if r.status_code == 429:
            time.sleep(15); continue
        if r.status_code != 200:
            return {}, f"error:http_{r.status_code}"
        return (r.json() or {}), ""
    return {}, "rate_limited"


def norm_domain(url):
    u = (url or "").strip().lower()
    if not u:
        return ""
    if not u.startswith(("http://", "https://")):
        u = "http://" + u
    h = (urlparse(u).hostname or "").lower()
    h = h[4:] if h.startswith("www.") else h
    return h if re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}", h) else ""


def svc():
    td = json.load(open(TOKEN_PATH))
    c = Credentials(token=td["token"], refresh_token=td["refresh_token"],
                    token_uri=td["token_uri"], client_id=td["client_id"],
                    client_secret=td["client_secret"], scopes=td["scopes"])
    if not c.valid:
        c.refresh(Request()); td["token"] = c.token; json.dump(td, open(TOKEN_PATH, "w"))
    return build("sheets", "v4", credentials=c)


def sheet_id(url):
    m = re.search(r"/d/([a-zA-Z0-9-_]+)", url)
    return m.group(1) if m else url


def people_from(resp):
    out, seen = [], set()
    for p in ([resp.get("best")] if resp.get("best") else []) + (resp.get("others") or []):
        if not p:
            continue
        nm = (p.get("fullName") or "").strip()
        if not nm or nm.lower() in seen:
            continue
        seen.add(nm.lower())
        out.append({"name": nm, "first": (p.get("firstName") or "").strip(),
                    "last": (p.get("lastName") or "").strip(),
                    "title": (p.get("title") or "").strip(),
                    "linkedin": (p.get("linkedIn") or "").strip(),
                    "seniority": p.get("seniorityScore")})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet_url", required=True)
    ap.add_argument("--collect", action="store_true")
    ap.add_argument("--out", default="pm_candidates.json")
    ap.add_argument("--candidates")
    ap.add_argument("--verdicts")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--fetch_uncached", action="store_true",
                    help="collect mode: also call PM for domains not in cache")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    s, sid = svc(), sheet_id(a.sheet_url)
    n = load_cache()
    print(f"PM cache: {n} domains", flush=True)

    rows_by_tab = {}
    for tab in TABS:
        rows_by_tab[tab] = s.spreadsheets().values().get(
            spreadsheetId=sid, range=f"'{tab}'!A1:S100000").execute().get("values", [])[1:]

    # ---------------- COLLECT ----------------
    if a.collect:
        todo, out = [], []
        for tab, rows in rows_by_tab.items():
            for i, r in enumerate(rows):
                def g(x): return (r[x].strip() if len(r) > x and r[x] else "")
                if g(COL_DM_EMAIL):
                    continue
                dom = norm_domain(g(COL_WEBSITE))
                if not dom:
                    continue
                todo.append({"row": i + 2, "tab": tab, "company": g(COL_NAME), "domain": dom})
        if a.limit:
            todo = todo[:a.limit]
        print(f"rows needing a DM: {len(todo)}", flush=True)

        uncached = [t for t in todo if t["domain"] not in _cache]
        print(f"  cached: {len(todo)-len(uncached)}   uncached: {len(uncached)}", flush=True)
        if uncached and a.fetch_uncached:
            def fetch(t):
                d, err = pm("/decision-makers", {"domain": t["domain"]})
                if not err:
                    cache_put(t["domain"], d)
                return t, err
            with ThreadPoolExecutor(max_workers=a.workers) as ex:
                for k, f in enumerate(as_completed([ex.submit(fetch, t) for t in uncached]), 1):
                    t, err = f.result()
                    if k % 25 == 0:
                        print(f"    fetched {k}/{len(uncached)}", flush=True)

        empties = 0
        for t in todo:
            ppl = people_from(_cache.get(t["domain"]) or {})
            if not ppl:
                empties += 1
                continue
            out.append({**t, "candidates": ppl})
        json.dump(out, open(a.out, "w"), indent=1)
        print(f"\ncompanies with candidates: {len(out)}   no people: {empties}")
        print(f"-> {a.out}")
        return

    # ---------------- APPLY ----------------
    if not (a.candidates and a.verdicts):
        print("need --collect, or --candidates + --verdicts"); return
    collected = {(c["tab"], c["row"]): c for c in json.load(open(a.candidates))}
    verdicts = json.load(open(a.verdicts))

    picks = []
    refused = 0
    for v in verdicts:
        key = (v["tab"], v["row"])
        rec = collected.get(key)
        if rec is None:
            print(f"  [!] {key}: not in candidates — refused"); refused += 1; continue
        want = (v.get("pick") or "").strip()
        if not want:
            continue
        match = next((p for p in rec["candidates"] if p["name"].lower() == want.lower()), None)
        if match is None:
            print(f'  [!] {key} {rec["company"]}: pick "{want}" not among candidates — refused')
            refused += 1; continue
        picks.append({**rec, "person": match})

    print(f"picks to resolve: {len(picks)}   refused: {refused}")
    if not a.apply:
        for p in picks[:40]:
            print(f'  DRY {p["tab"]} r{p["row"]:4d} {p["company"][:34]:34s} '
                  f'{p["person"]["name"]} ({p["person"]["title"]})')
        print("\n(dry run — add --apply)"); return

    updates, found = {t: [] for t in TABS}, 0

    def resolve(p):
        person = p["person"]
        d, err = pm("/find", {"firstName": person["first"], "lastName": person["last"],
                              "domain": p["domain"]})
        if err:
            return p, "", f"pm_{err}"
        email = ((d.get("email") or "") if isinstance(d, dict) else "").strip().lower()
        status = (d.get("status") or d.get("validationStatus") or "").strip().lower()
        if not email:
            return p, "", "pm_not_found"
        if status and status != "valid":
            return p, "", f"pm_{status}"
        edom = email.split("@")[-1]
        if edom in FREE_MAIL:
            return p, "", "pm_free_mailbox"
        if edom.split(".")[-2:] != p["domain"].split(".")[-2:]:
            return p, "", "pm_domain_mismatch"
        return p, email, "found"

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
        futs = [ex.submit(resolve, p) for p in picks]
        for k, f in enumerate(as_completed(futs), 1):
            p, email, status = f.result()
            per = p["person"]
            if email:
                found += 1
                updates[p["tab"]].append({"row": p["row"],
                    "vals": [per["name"], per["title"], email, per["linkedin"], "found"]})
                print(f'  +  {p["company"][:38]:38s} -> {per["name"]} ({per["title"]}) | {email}', flush=True)
            else:
                updates[p["tab"]].append({"row": p["row"], "vals": ["", "", "", "", status]})
            pending += 1
            if pending >= WRITE_BATCH:
                flush(); pending = 0
    flush()
    print(f"\nDone. found={found} / {len(picks)} picks")


if __name__ == "__main__":
    main()
