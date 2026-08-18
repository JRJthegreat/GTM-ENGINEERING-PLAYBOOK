"""Score, stack, and export BRAND companies to a 29-col campaign sheet.

Scoring (stacking is the whole point):
  ad_stale +3 · video_job +2 · new_exec +2 · funding +1.5 · +1 per extra
  distinct signal type beyond the first. Sorted score desc, newest signal first.

Sheet layout: 29-col base schema. Company/website/city/state/status land at
K/L/R/S/AB so exa-website-enrichment and apollo-dm-waterfall run against the
sheet with default flags. Job-sourced rows carry the job in A-J and the Indeed
URL in AC (Jude's rule: AC always holds the Indeed URL). new_exec rows carry
the announced person in T/U/V (the person IS the DM — verify before any email
spend). AB holds the signal stack summary.

Delta-by-default: exported_at IS '' only. --include_exported re-exports all.
Batch-of-10 writes, 18px rows.

Usage: python3 -W ignore export_batch.py [--max_rows 300] [--min_score 0]
       [--include_exported] [--dry_run] [--title "..."]
"""
import argparse
import json
import re
import sys
from datetime import date

from store import connect, get_google_service, now_iso

TAB = "Leads"
HEADERS = [
    "Job_Id", "Job Title", "Job Type", "Occupations", "Date Published",
    "Salary Min", "Salary Max", "Salary Period", "Apply URL", "Job Description",
    "Company Name", "Company Website", "Company Size", "Revenue", "CEO Name",
    "Company Description", "Benefits", "City", "State",
    "DM Name", "DM Title", "LinkedIn URL", "Email",
    "First Name", "Last Name", "Email Body", "Added to Instantly",
    "Signal Stack", "Indeed URL",
]
WEIGHTS = {"ad_stale": 3.0, "new_exec": 2.0, "funding": 1.5}
# video_job weight comes from filter_video_jobs.py's GPT-4.1 relevance verdict
# (Jude's calibration, 2026-08-18): STRONG 2.0, WEAK 0.5, IRRELEVANT 0.
# Unjudged signals fall back to the old title regex at WEAK strength.
RELEVANCE_WEIGHT = {"STRONG": 2.0, "WEAK": 0.5, "IRRELEVANT": 0.0}
VIDEO_TITLE_RE = re.compile(
    r"video|videograph|content|multimedia|creative|photo|social media|brand", re.I)

def video_job_weight(job_details):
    best = 0.0
    for d in job_details:
        rel = d.get("relevance")
        if rel:
            best = max(best, RELEVANCE_WEIGHT.get(rel, 0.0))
        elif VIDEO_TITLE_RE.search(d.get("job_title") or ""):
            best = max(best, 0.5)
    return best

def score_company(sig_types, job_details):
    s = 0.0
    counted_types = 0
    for t in sig_types:
        if t == "video_job":
            w = video_job_weight(job_details)
            s += w
            if w > 0:
                counted_types += 1
        else:
            s += WEIGHTS.get(t, 0)
            counted_types += 1
    return s + max(0, counted_types - 1)

def score_game(details):
    """Gaming lane: publisher money + trailer gap + a set date = window entered."""
    best = 0.0
    for d in details:
        s = 2.0
        if d.get("publisher_attached"):
            s += 1.5
        if (d.get("trailer_count") or 0) <= 1:
            s += 1.5
        if re.search(r"\d{4}", d.get("release_date") or ""):
            s += 0.5
        best = max(best, s)
    return best

def build_rows(con, include_exported, lane="brand"):
    cat = "GAME" if lane == "gaming" else "BRAND"
    q = f"SELECT id, name, size, city, state, website FROM companies WHERE category='{cat}'"
    if not include_exported:
        q += " AND exported_at=''"
    out = []
    for cid, name, size, city, state, website in con.execute(q).fetchall():
        sigs = con.execute(
            "SELECT type, detail, event_date FROM signals WHERE company_id=?"
            " ORDER BY event_date DESC", (cid,)).fetchall()
        if not sigs:
            continue
        types = sorted({t for t, _, _ in sigs})
        if lane == "gaming":
            games = [json.loads(d) for t, d, _ in sigs if t == "game_launch"]
            if not games:
                continue
            sc = score_game(games)
            g = games[0]
            row = [""] * len(HEADERS)
            row[10] = name
            row[11] = website
            row[15] = (f"Game: {g.get('game')} | release {g.get('release_date')} | "
                       f"trailers {g.get('trailer_count')} | "
                       f"{', '.join(g.get('genres') or [])[:60]} | "
                       f"{g.get('short_description','')}")[:400]
            row[27] = (f"score={sc:g} | game_launch:{g.get('game')} "
                       f"rel={g.get('release_date')} trailers={g.get('trailer_count')} "
                       f"pub={'y' if g.get('publisher_attached') else 'n'} | "
                       f"{g.get('steam_url','')}")
            out.append((sc, max((e for _, _, e in sigs if e), default=""), cid, row))
            continue
        job_details = [json.loads(d) for t, d, _ in sigs if t == "video_job"]
        sc = score_company(types, job_details)
        if sc <= 0:
            continue
        newest = max((e for _, _, e in sigs if e), default="")
        job, job_key = None, ""
        best_w = -1.0
        for key, d, e in con.execute(
                "SELECT key, detail, event_date FROM signals"
                " WHERE company_id=? AND type='video_job'"
                " ORDER BY event_date DESC", (cid,)):
            dd = json.loads(d)
            w = RELEVANCE_WEIGHT.get(dd.get("relevance", ""),
                                     0.5 if VIDEO_TITLE_RE.search(dd.get("job_title") or "") else 0.0)
            if w > best_w:
                best_w, job, job_key = w, dd, key
        if job is not None and best_w <= 0:
            job, job_key = None, ""    # only irrelevant jobs — show none
        exec_d = next((json.loads(d) for t, d, _ in sigs if t == "new_exec"), None)
        fund = next((json.loads(d) for t, d, _ in sigs if t == "funding"), None)

        stack_bits = []
        for t, d, e in sigs:
            dd = json.loads(d)
            if t == "video_job":
                if dd.get("relevance") == "IRRELEVANT":
                    continue
                rel_tag = f"[{dd['relevance']}]" if dd.get("relevance") else ""
                stack_bits.append(f"video_job{rel_tag}:{dd.get('job_title','')[:40]}")
            elif t == "new_exec":
                stack_bits.append(f"new_exec:{dd.get('person','')} ({dd.get('role','')[:30]})")
            elif t == "funding":
                stack_bits.append(f"funding:${dd.get('amount_sold',0):,} {dd.get('industry','')}")
            elif t == "ad_stale":
                stack_bits.append(f"ad_stale:{dd.get('active_ads')}ads/"
                                  f"{dd.get('oldest_days')}d/"
                                  f"{int((dd.get('video_share') or 0)*100)}%video")
        summary = f"score={sc:g} | " + " + ".join(stack_bits)

        dm_name = dm_title = dm_li = ""
        if exec_d:
            dm_name = exec_d.get("person", "")
            dm_title = exec_d.get("role", "")
            dm_li = exec_d.get("author_linkedin", "")
        elif fund and fund.get("people"):
            p = fund["people"][0]
            dm_name = f"{p.get('first','')} {p.get('last','')}".strip().title()
            dm_title = "/".join(p.get("roles", []))[:60]

        row = [""] * len(HEADERS)
        if job:
            row[0] = job_key
            row[1] = job.get("job_title") or ""
            row[4] = next((e for t, _, e in sigs if t == "video_job"), "")
            row[8] = job.get("apply_url") or ""
            row[9] = (job.get("desc") or "")[:400]
            row[28] = f"https://www.indeed.com/viewjob?jk={job_key}" if job_key else ""
        row[10] = name
        row[11] = website
        row[12] = size
        row[17] = city
        row[18] = state
        row[19] = dm_name
        row[20] = dm_title
        row[21] = dm_li
        row[27] = summary
        out.append((sc, newest, cid, row))
    out.sort(key=lambda x: x[1] or "", reverse=True)   # newest signal first…
    out.sort(key=lambda x: -x[0])                      # …within score desc (stable)
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lane", choices=["brand", "gaming"], default="brand")
    ap.add_argument("--max_rows", type=int, default=300)
    ap.add_argument("--min_score", type=float, default=0)
    ap.add_argument("--include_exported", action="store_true")
    ap.add_argument("--dry_run", action="store_true")
    ap.add_argument("--title", default="")
    ap.add_argument("--append_to", default="",
                    help="URL/id of the master spreadsheet: append to its lane tab "
                         "(Brand/Gaming) instead of creating a new spreadsheet")
    args = ap.parse_args()

    con = connect()
    rows = [r for r in build_rows(con, args.include_exported, args.lane)
            if r[0] >= args.min_score][:args.max_rows]
    print(f"{len(rows)} rows to export")
    stacked = sum(1 for sc, _, _, _ in rows if sc > 3)
    print(f"  multi-signal (score>3): {stacked}")
    if args.dry_run:
        for sc, newest, _, row in rows[:25]:
            print(f"  [{sc:g}] {row[10][:35]:35s} | {row[27][:90]}")
        return

    svc = get_google_service()

    if args.append_to:
        import re as _re
        m = _re.search(r"/d/([A-Za-z0-9_-]+)", args.append_to)
        sid = m.group(1) if m else args.append_to
        tab = "Gaming" if args.lane == "gaming" else "Brand"
        written = 0
        for i in range(0, len(rows), 10):
            chunk = rows[i:i + 10]
            svc.spreadsheets().values().append(
                spreadsheetId=sid, range=f"'{tab}'!A1", valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body={"values": [r for _, _, _, r in chunk]}).execute()
            for _, _, cid, _ in chunk:
                con.execute("UPDATE companies SET exported_at=? WHERE id=?",
                            (now_iso(), cid))
            con.commit()
            written += len(chunk)
        print(f"done: {written} rows appended to '{tab}' tab -> "
              f"https://docs.google.com/spreadsheets/d/{sid}/edit")
        return

    title = args.title or f"Production Demand - {date.today().isoformat()}"
    resp = svc.spreadsheets().create(body={"properties": {"title": title}},
                                     fields="spreadsheetId").execute()
    sid = resp["spreadsheetId"]
    meta = svc.spreadsheets().get(spreadsheetId=sid).execute()
    gid = meta["sheets"][0]["properties"]["sheetId"]
    svc.spreadsheets().batchUpdate(spreadsheetId=sid, body={"requests": [
        {"updateSheetProperties": {"properties": {"sheetId": gid, "title": TAB},
                                   "fields": "title"}},
        {"updateSheetProperties": {"properties": {
            "sheetId": gid, "gridProperties": {"rowCount": 5000}},
            "fields": "gridProperties.rowCount"}},
        {"updateDimensionProperties": {
            "range": {"sheetId": gid, "dimension": "ROWS",
                      "startIndex": 0, "endIndex": 5000},
            "properties": {"pixelSize": 18}, "fields": "pixelSize"}},
    ]}).execute()
    svc.spreadsheets().values().update(
        spreadsheetId=sid, range=f"'{TAB}'!A1", valueInputOption="RAW",
        body={"values": [HEADERS]}).execute()

    written = 0
    for i in range(0, len(rows), 10):
        chunk = rows[i:i + 10]
        svc.spreadsheets().values().append(
            spreadsheetId=sid, range=f"'{TAB}'!A1", valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": [r for _, _, _, r in chunk]}).execute()
        for _, _, cid, _ in chunk:
            con.execute("UPDATE companies SET exported_at=? WHERE id=?",
                        (now_iso(), cid))
        con.commit()
        written += len(chunk)
    print(f"done: {written} rows → https://docs.google.com/spreadsheets/d/{sid}/edit")

if __name__ == "__main__":
    sys.exit(main())
