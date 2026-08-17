"""Signal collector: game launches entering the marketing window (Steam, free).

The gaming lane of the demand skill. A game whose Steam page just went live has
4-5 trailer purchases ahead (announcement / gameplay / release-date / launch /
accolades). Pilot learning (2026-08-17): the "popular coming soon" list skews
days-from-launch where trailer money is already spent — so this collector walks
BOTH the popular list (traction proxy) and the full coming-soon catalog, and
the weekly re-run diffs naturally via the store's idempotent signal keys.

Companies are stamped category='GAME' directly (publisher if attached, else
developer) — they skip GPT classify and export as their own lane
(export_batch.py --lane gaming), never mixed into the brand sheet.

Usage: python3 -W ignore collect_steam.py [--target 350] [--dry_run]
"""
import argparse
import re
import sys
import time

import requests

from store import add_signal, connect, upsert_company

SEARCH = ("https://store.steampowered.com/search/results/"
          "?query&start={start}&count=50&category1=998&filter={filt}&infinite=1")
DETAILS = "https://store.steampowered.com/api/appdetails?appids={appid}&cc=us&l=en"
HDRS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

def walk_appids(filt, pages):
    ids = []
    for p in range(pages):
        try:
            r = requests.get(SEARCH.format(start=p * 50, filt=filt),
                             headers=HDRS, timeout=30)
            if r.status_code != 200:
                break
            found = re.findall(r'data-ds-appid="(\d+)"',
                               r.json().get("results_html", ""))
            if not found:
                break
            ids.extend(found)
        except requests.RequestException:
            break
        time.sleep(1.0)
    return ids

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=350,
                    help="max NEW games to detail this run")
    ap.add_argument("--pages", type=int, default=8, help="search pages per list")
    ap.add_argument("--dry_run", action="store_true")
    args = ap.parse_args()

    con = connect()
    known = {k for (k,) in con.execute(
        "SELECT key FROM signals WHERE type='game_launch'")}
    ordered, seen = [], set()
    for filt in ("popularcomingsoon", "comingsoon"):
        for a in walk_appids(filt, args.pages):
            if a not in seen and a not in known:
                seen.add(a)
                ordered.append(a)
    ordered = ordered[:args.target]
    print(f"{len(ordered)} new appids to detail ({len(known)} already stored)")
    if args.dry_run:
        return

    kept = 0
    for i, appid in enumerate(ordered):
        try:
            r = requests.get(DETAILS.format(appid=appid), headers=HDRS, timeout=30)
            if r.status_code == 429:
                time.sleep(30)
                r = requests.get(DETAILS.format(appid=appid), headers=HDRS,
                                 timeout=30)
            blob = (r.json() or {}).get(str(appid), {})
            if not blob.get("success"):
                continue
            d = blob["data"]
            if d.get("type") != "game":
                continue
            devs = d.get("developers") or []
            pubs = d.get("publishers") or []
            rel = d.get("release_date") or {}
            if not rel.get("coming_soon", False):
                continue
            pub_attached = bool(pubs) and (set(p.lower() for p in pubs)
                                           != set(x.lower() for x in devs))
            company = (pubs[0] if pub_attached and pubs else
                       (devs[0] if devs else (pubs[0] if pubs else "")))
            if not company:
                continue
            cid = upsert_company(con, company)
            if cid is None:
                continue
            con.execute("UPDATE companies SET category='GAME',"
                        " classify_reason='steam collector',"
                        " website=CASE WHEN website='' THEN ? ELSE website END"
                        " WHERE id=?", (d.get("website") or "", cid))
            if add_signal(con, cid, "game_launch", appid, {
                    "game": d.get("name"),
                    "release_date": rel.get("date", ""),
                    "trailer_count": len(d.get("movies") or []),
                    "publisher_attached": pub_attached,
                    "developers": devs, "publishers": pubs,
                    "genres": [g.get("description")
                               for g in (d.get("genres") or [])],
                    "short_description": (d.get("short_description") or "")[:200],
                    "steam_url": f"https://store.steampowered.com/app/{appid}/",
            }):
                kept += 1
            con.commit()
        except Exception:
            pass
        if (i + 1) % 50 == 0:
            print(f"  {i+1}/{len(ordered)} ({kept} stored)", flush=True)
        time.sleep(0.85)
    con.commit()
    print(f"done: {kept} game_launch signals stored")

if __name__ == "__main__":
    sys.exit(main())
