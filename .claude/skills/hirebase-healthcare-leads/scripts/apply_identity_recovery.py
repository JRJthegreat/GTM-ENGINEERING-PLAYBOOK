"""
Phase 3f (APPLY) — write recovered employer identities back to the lane tabs.

Third step of COLLECT -> JUDGE -> APPLY for the REVIEW_PROFILE_MISMATCH pile.
The verdicts file is hand-written by Claude in-session from
collect_identity_recovery.py's evidence (job descriptions + ATS tenant).

TWO GUARDS, because this step rewrites the company NAME and WEBSITE — the two
fields every downstream credit is spent against.

1. HALLUCINATION GUARD. A verdict naming a company absent from the candidates
   file is refused, a status outside the vocabulary is refused, and every
   candidate must be ruled on.
2. PROOF-ON-PAGE GATE. A domain proposed by the judge is NOT written on the
   judge's say-so. The page is fetched and must actually mention the recovered
   company's distinctive name tokens. Fail -> the name is still corrected but
   the website is left BLANK and stamped needs_search. Blank beats wrong; the
   whole point of this lane is that a plausible-but-wrong domain emails a real
   person at the wrong company.

Vocabulary:
  KEEP_AS_IS           name and website were already right; only HireBase's
                       description was mismatched. Nothing rewritten, flag
                       cleared to KEEP.
  RECOVERED            real employer identified and inside ICP.
  RECOVERED_OVER_CAP   real employer identified but it is a large health
                       system, far above the standing 500-employee cap that
                       replied 0.00% across 251 companies. Identity is fixed
                       so the row is TRUE, but it is held back rather than
                       silently emailed. Jude's call whether to work them.
  DROP_AGENCY          recovery revealed a staffing/contract-clinician firm.
  DROP_NOT_EMPLOYER    recovery revealed a vendor posting on a client's behalf.
  UNRESOLVED           evidence was not enough. Stays REVIEW_PROFILE_MISMATCH.

Run:
  python3 -W ignore apply_identity_recovery.py --sheet_url URL \
    --tabs "SLP Campaign" "General Campaign" [--apply]
"""

import os
import re
import ssl
import json
import time
import argparse
import warnings
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

warnings.filterwarnings('ignore')

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_PATH = os.path.join(SCRIPT_DIR, "..", "..", "..", "token.json")
DATA_DIR = os.path.join(SCRIPT_DIR, "..", "data")

COL_COMPANY, COL_SITE = 10, 11
COL_LINKEDIN, COL_DSTATUS, COL_DNOTE, COL_STATUS = 40, 45, 46, 47
CHUNK = 500

VOCAB = {"KEEP_AS_IS", "RECOVERED", "RECOVERED_OVER_CAP",
         "DROP_AGENCY", "DROP_NOT_EMPLOYER", "UNRESOLVED"}
FINAL = {"KEEP_AS_IS": "KEEP", "RECOVERED": "KEEP",
         "RECOVERED_OVER_CAP": "REVIEW_OVER_CAP",
         "DROP_AGENCY": "DROP_AGENCY", "DROP_NOT_EMPLOYER": "DROP_NOT_EMPLOYER",
         "UNRESOLVED": "REVIEW_PROFILE_MISMATCH"}

STOP = {"the", "and", "of", "inc", "llc", "group", "system", "systems",
        "health", "healthcare", "medical", "center", "centre", "care",
        "services", "hospital", "company", "corporation", "school", "schools"}
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}


def sheet_id_of(url):
    return re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url).group(1)


def a1(col, row):
    s, c = "", col + 1
    while c:
        c, r = divmod(c - 1, 26)
        s = chr(65 + r) + s
    return f"{s}{row}"


def tokens(name):
    raw = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower()).split()
    return [t for t in raw if len(t) >= 3 and t not in STOP]


def _fetch(u, timeout):
    """Hospital and school sites block bare urllib constantly, and a fetch
    failure is NOT proof of a wrong domain — it just costs us a good one. Try a
    full browser header set, then the other www/non-www form."""
    import requests
    hdrs = {
        "User-Agent": UA["User-Agent"],
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }
    host = re.sub(r"^https?://", "", u).split("/")[0]
    alt = host[4:] if host.startswith("www.") else "www." + host
    for cand in (u, f"https://{alt}", u.replace("https://", "http://")):
        try:
            r = requests.get(cand, headers=hdrs, timeout=timeout,
                             allow_redirects=True, verify=False)
            if r.status_code < 400 and r.text:
                return r.text
        except Exception:
            continue
    return None


def prove(name, url, timeout=8):
    """Fetch the candidate domain and require the company's distinctive name
    tokens to actually appear. Returns (ok, note).

    A judge-proposed domain is never written on the judge's say-so — this is
    the gate that stops a plausible-but-wrong guess from emailing a real person
    at the wrong company."""
    if not url:
        return False, "no candidate domain"
    u = url if url.startswith(("http://", "https://")) else "https://" + url
    html = _fetch(u, timeout)
    if html is None:
        return False, "fetch failed (site unreachable/blocked)"
    text = re.sub(r"<[^>]+>", " ", html.lower())
    toks = tokens(name)
    if not toks:
        return False, "no distinctive tokens"
    hit = sum(1 for t in toks if t in text)
    if hit / len(toks) >= 0.5:
        return True, f"proved {hit}/{len(toks)} tokens on page"
    return False, f"page did not name the company ({hit}/{len(toks)})"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet_url", required=True)
    ap.add_argument("--tabs", nargs="+", required=True)
    ap.add_argument("--candidates", default=os.path.join(DATA_DIR, "identity_candidates.json"))
    ap.add_argument("--verdicts", default=os.path.join(DATA_DIR, "identity_verdicts.json"))
    ap.add_argument("--statuses", nargs="+", default=["REVIEW_PROFILE_MISMATCH"],
                    help="Rows with these company_status values are eligible "
                         "to be rewritten. Must match the collect run.")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    cand = {c["row_company_name"]: c
            for c in json.load(open(args.candidates))["companies"]}
    verdicts = json.load(open(args.verdicts))

    unknown = [c for c in verdicts if c not in cand]
    if unknown:
        raise SystemExit(f"REFUSED — not in candidates (hallucination guard): {unknown[:6]}")
    bad = [c for c, v in verdicts.items() if v.get("status") not in VOCAB]
    if bad:
        raise SystemExit(f"REFUSED — status outside vocabulary: {bad[:6]}")
    missing = sorted(set(cand) - set(verdicts))
    if missing:
        raise SystemExit(f"REFUSED — {len(missing)} unjudged: {missing[:8]}")

    # ---- proof-on-page for every proposed domain ----------------------
    todo = [(c, v) for c, v in verdicts.items()
            if v["status"] in ("RECOVERED", "RECOVERED_OVER_CAP")]
    print(f"proving {len(todo)} candidate domains...")
    proofs = {}
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(prove, v.get("real_name", c), v.get("domain_candidate", "")): c
                for c, v in todo}
        for f in as_completed(futs):
            c = futs[f]
            ok, note = f.result()
            proofs[c] = (ok, note)
            v = verdicts[c]
            mark = "OK  " if ok else "FAIL"
            print(f"  {mark} {v.get('real_name', c)[:38]:38s} "
                  f"{(v.get('domain_candidate') or '(none)')[:34]:34s} {note}")

    plan = {}
    for c, v in verdicts.items():
        st = v["status"]
        entry = {"final": FINAL[st], "name": None, "site": None,
                 "dstatus": "", "dnote": v.get("reason", "")[:200]}
        if st in ("RECOVERED", "RECOVERED_OVER_CAP"):
            entry["name"] = v.get("real_name") or None
            ok, note = proofs.get(c, (False, "not checked"))
            if ok:
                entry["site"] = v["domain_candidate"]
                entry["dstatus"] = "identity_recovered"
            else:
                entry["site"] = ""      # blank beats wrong
                entry["dstatus"] = "needs_search"
                entry["final"] = ("REVIEW_NEEDS_DOMAIN"
                                  if entry["final"] == "KEEP" else entry["final"])
            entry["dnote"] = f"was '{c}' / {cand[c]['wrong_website']}; {note}"[:200]
        plan[c] = entry

    print("\nOUTCOME by verdict:")
    for s, n in Counter(v["status"] for v in verdicts.values()).most_common():
        rows = sum(cand[c]["rows"] for c, v in verdicts.items() if v["status"] == s)
        print(f"  {s:22s} {n:3d} companies  {rows:4d} rows")
    print("\nRESULTING company_status:")
    for s, n in Counter(p["final"] for p in plan.values()).most_common():
        rows = sum(cand[c]["rows"] for c, p in plan.items() if p["final"] == s)
        print(f"  {s:22s} {n:3d} companies  {rows:4d} rows")

    if not args.apply:
        print("\n  (no --apply — nothing written)")
        return

    svc = build("sheets", "v4",
                credentials=Credentials.from_authorized_user_file(TOKEN_PATH))
    sid = sheet_id_of(args.sheet_url)
    for tab in args.tabs:
        vals = svc.spreadsheets().values().get(
            spreadsheetId=sid, range=f"'{tab}'!A1:AV").execute().get("values", [])
        rows = vals[1:]
        colK, colL, colAO, colAT, colAV, touched = [], [], [], [], [], 0
        for r in rows:
            def c(i, _r=r):
                return _r[i].strip() if i < len(_r) else ""
            name = c(COL_COMPANY)
            p = plan.get(name) if c(COL_STATUS) in args.statuses else None
            if not p:
                colK.append([name]); colL.append([c(COL_SITE)])
                colAO.append([c(COL_LINKEDIN)])
                colAT.append([c(COL_DSTATUS), c(COL_DNOTE)])
                colAV.append([c(COL_STATUS)])
                continue
            touched += 1
            colK.append([p["name"] or name])
            colL.append([c(COL_SITE) if p["site"] is None else p["site"]])
            # the stored LinkedIn belongs to the WRONG company — drop it
            colAO.append([c(COL_LINKEDIN) if p["name"] is None else ""])
            colAT.append([p["dstatus"] or c(COL_DSTATUS), p["dnote"]])
            colAV.append([p["final"]])
        for col, letters in ((colK, COL_COMPANY), (colL, COL_SITE),
                             (colAO, COL_LINKEDIN), (colAT, COL_DSTATUS),
                             (colAV, COL_STATUS)):
            for i in range(0, len(col), CHUNK):
                for attempt in range(4):
                    try:
                        svc.spreadsheets().values().update(
                            spreadsheetId=sid,
                            range=f"'{tab}'!{a1(letters, i + 2)}",
                            valueInputOption="RAW",
                            body={"values": col[i:i + CHUNK]}).execute()
                        break
                    except Exception as e:
                        if "429" not in str(e) or attempt == 3:
                            raise
                        time.sleep(20 * (attempt + 1))
        print(f"  [{tab}] rewrote {touched} rows")
    print("  done")


if __name__ == "__main__":
    main()
