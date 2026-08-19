"""
Phase 5d — Purple Magic (ConnectorOS) rescue, third and last DM lane.

Runs over the worklist rows still empty after the Apollo waterfall AND the AMF
CEO rescue. It is a THIRD lane rather than a replacement because different
providers fail on different companies: Apollo only knows who it has indexed,
AMF resolves a role at a domain, and PM has its own index that covers small
practices neither reaches. PM's /decision-makers returns nobody roughly 83% of
the time on small practices, so expect modest yield — but the misses are free
and it reaches domains the other two cannot.

THE LOGIC IS IMPORTED, NOT REWRITTEN. find_ceo_pm_demand.py already encodes
the working contract — the Bearer auth, the /decision-makers + /find shapes,
the owner-title gate that runs BEFORE the /find call so no credit is spent on
non-authority people, the title priority walk over EVERY person returned (PM's
own "best" pick is often a recruiter while the owner sits in the array), and
the free-mail + domain-match email check. Reimplementing it from memory is how
an endpoint gets guessed wrong, so it is importlib-loaded as one source of
truth. Do not fork the API logic here.

The owner gate matches this lane's ladder exactly: CEO / owner / founder /
president / managing director / managing partner / principal, and nobody else.

Writes T-Y + AB on the worklist with `pm_*` statuses, so the three lanes stay
distinguishable and never re-attempt each other's rows.

Run:
  python3 -W ignore rescue_dm_pm.py --sheet_url URL --tab "DM Worklist" [--apply]
"""

import os
import re
import argparse
import importlib.util
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_PATH = os.path.join(SCRIPT_DIR, "..", "..", "..", "token.json")
PM_SRC = os.path.join(SCRIPT_DIR, "..", "..", "healthcare-staffing-enrichment",
                      "scripts", "find_ceo_pm_demand.py")

COL_COMPANY, COL_WEBSITE = 10, 11
COL_DM, COL_TITLE, COL_LI, COL_EMAIL, COL_FIRST, COL_LAST = 19, 20, 21, 22, 23, 24
COL_STATUS = 27
BATCH = 10

# Everything the earlier lanes leave behind when they cannot fill a row.
RETRY = {"not_found", "no_dm_candidates", "no_apollo_people",
         "ranked_no_email", "rescue_amf_no_ceo", ""}


def load_pm():
    spec = importlib.util.spec_from_file_location("pm_src", PM_SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sheet_id_of(url):
    return re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url).group(1)


def a1(col, row):
    s, c = "", col + 1
    while c:
        c, r = divmod(c - 1, 26)
        s = chr(65 + r) + s
    return f"{s}{row}"


def norm_domain(web):
    w = (web or "").strip()
    if not w:
        return ""
    if not w.startswith(("http://", "https://")):
        w = "https://" + w
    h = urlparse(w).netloc.lower()
    return h[4:] if h.startswith("www.") else h


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet_url", required=True)
    ap.add_argument("--tab", default="DM Worklist")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    if not os.getenv("PURPLE_MAGIC_KEY"):
        from dotenv import load_dotenv
        load_dotenv(os.path.join(SCRIPT_DIR, "..", "..", "..", ".env"))
    pmmod = load_pm()
    if not getattr(pmmod, "PM_KEY", None):
        raise SystemExit("PURPLE_MAGIC_KEY not set")

    svc = build("sheets", "v4",
                credentials=Credentials.from_authorized_user_file(TOKEN_PATH))
    sid = sheet_id_of(args.sheet_url)
    vals = svc.spreadsheets().values().get(
        spreadsheetId=sid, range=f"'{args.tab}'!A1:AD").execute().get("values", [])
    rows = vals[1:]

    def c(r, i):
        return r[i].strip() if i < len(r) else ""

    todo = []
    for i, r in enumerate(rows):
        if c(r, COL_EMAIL) or c(r, COL_STATUS) not in RETRY:
            continue
        dom = norm_domain(c(r, COL_WEBSITE))
        if dom:
            todo.append({"sheet_row": i + 2, "domain": dom,
                         "company": c(r, COL_COMPANY)})
    if args.limit:
        todo = todo[: args.limit]

    print(f"[{args.tab}] {len(todo)} companies for the Purple Magic lane")
    if not args.apply:
        for t in todo[:8]:
            print(f"    row {t['sheet_row']}: {t['company'][:36]:36s} {t['domain']}")
        print("\n  (no --apply — nothing spent)")
        return

    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(pmmod.resolve, t): t for t in todo}
        for n, f in enumerate(as_completed(futs), 1):
            try:
                res = f.result()
            except Exception as e:
                res = {**futs[f], "status": f"pm_error_{type(e).__name__}"}
            results.append(res)
            if res.get("status") == "found":
                print(f"    ✓ {res['company'][:32]:32s} {res['dm_name']} "
                      f"<{res['dm_email']}>  [{res.get('dm_title','')[:24]}]")
            if n % 25 == 0:
                print(f"  -- {n}/{len(todo)}")

    pending, found = [], 0
    for res in results:
        sr = res["sheet_row"]
        if res.get("status") == "found":
            found += 1
            name = res.get("dm_name", "")
            parts = name.split()
            pending.append({"range": f"'{args.tab}'!{a1(COL_DM, sr)}",
                            "values": [[name, res.get("dm_title", ""),
                                        res.get("dm_linkedin", ""),
                                        res.get("dm_email", ""),
                                        parts[0] if parts else "",
                                        " ".join(parts[1:]) if len(parts) > 1 else ""]]})
            pending.append({"range": f"'{args.tab}'!{a1(COL_STATUS, sr)}",
                            "values": [["found_pm_rescue"]]})
        else:
            pending.append({"range": f"'{args.tab}'!{a1(COL_STATUS, sr)}",
                            "values": [[res.get("status", "pm_not_found")]]})
        if len(pending) >= BATCH * 2:
            svc.spreadsheets().values().batchUpdate(
                spreadsheetId=sid,
                body={"valueInputOption": "RAW", "data": pending}).execute()
            pending = []
    if pending:
        svc.spreadsheets().values().batchUpdate(
            spreadsheetId=sid,
            body={"valueInputOption": "RAW", "data": pending}).execute()

    print(f"\n=== Purple Magic: {found}/{len(todo)} found ===")
    print("  ", dict(Counter(r.get("status") for r in results).most_common(8)))


if __name__ == "__main__":
    main()
