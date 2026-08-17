"""Enrichment: Meta Ad Library staleness metrics per BRAND company.

Per brand: exact-phrase keyword search of active US ads via
curious_coder~facebook-ads-library-scraper, then mechanical metrics — oldest
active ad age, video share, distinct creative bodies. stale = oldest >= 90 days
OR zero video among actives. Page names are fuzzy-matched back to the company
(token overlap) so a keyword search returning strangers doesn't mis-attribute.

Not a discovery source: runs only over companies already in the store.
Idempotent: ad_checked rows are skipped (--retry_checked to redo).

Usage: python3 -W ignore enrich_adlibrary.py [--limit N] [--per_brand 12]
       [--batch 10] [--retry_checked]
"""
import argparse
import sys
import urllib.parse
from datetime import datetime, timezone

import requests

from store import APIFY_API_TOKEN, add_signal, connect, normalize

SYNC = ("https://api.apify.com/v2/acts/curious_coder~facebook-ads-library-scraper/"
        "run-sync-get-dataset-items")
STALE_DAYS = 90

def search_url(brand):
    q = urllib.parse.quote(f'"{brand}"')
    return ("https://www.facebook.com/ads/library/?active_status=active"
            f"&ad_type=all&country=US&q={q}&search_type=keyword_exact_phrase"
            "&media_type=all")

def page_matches(page_name, comp_norm):
    ptoks = set(normalize(page_name).split())
    ctoks = set(comp_norm.split())
    if not ptoks or not ctoks:
        return False
    return len(ptoks & ctoks) / len(ctoks) > 0.5

def ad_age_days(a, now):
    start = (a.get("startDate") or a.get("start_date")
             or a.get("adDeliveryStartTime") or a.get("ad_delivery_start_time"))
    if isinstance(start, (int, float)):
        return (now - datetime.fromtimestamp(start, tz=timezone.utc)).days
    if isinstance(start, str) and len(start) >= 10:
        try:
            dt = datetime.fromisoformat(start[:19]).replace(tzinfo=timezone.utc)
            return (now - dt).days
        except ValueError:
            return None
    return None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--per_brand", type=int, default=12)
    ap.add_argument("--batch", type=int, default=10)
    ap.add_argument("--retry_checked", action="store_true")
    args = ap.parse_args()

    con = connect()
    q = "SELECT id, name, norm FROM companies WHERE category='BRAND'"
    if not args.retry_checked:
        q += " AND ad_checked=0"
    rows = con.execute(q).fetchall()
    if args.limit:
        rows = rows[:args.limit]
    print(f"{len(rows)} brands to check, {args.per_brand} ads each")

    now = datetime.now(timezone.utc)
    flagged = 0
    for i in range(0, len(rows), args.batch):
        chunk = rows[i:i + args.batch]
        try:
            r = requests.post(
                SYNC, params={"token": APIFY_API_TOKEN},
                json={"urls": [{"url": search_url(name)} for _, name, _ in chunk],
                      "limitPerSource": args.per_brand, "scrapeAdDetails": False},
                timeout=600)
            items = r.json() if r.status_code in (200, 201) else []
            if not isinstance(items, list):
                items = []
        except requests.RequestException as e:
            print(f"  [!] batch {i}: {type(e).__name__}", flush=True)
            items = []
        for cid, name, comp_norm in chunk:
            ads = [a for a in items if page_matches(
                a.get("pageName") or a.get("page_name") or "", comp_norm)]
            ages = [d for d in (ad_age_days(a, now) for a in ads) if d is not None]
            video = sum(1 for a in ads
                        if ((a.get("snapshot") or {}).get("videos")
                            or a.get("videos")))
            bodies = set()
            for a in ads:
                body = (a.get("snapshot") or {}).get("body") or {}
                txt = body.get("text") if isinstance(body, dict) else str(body)
                if txt:
                    bodies.add(txt[:120])
            stale = bool(ads) and (bool(ages and max(ages) >= STALE_DAYS)
                                   or video == 0)
            con.execute(
                "UPDATE companies SET ad_checked=1, ad_active=?, ad_oldest_days=?,"
                " ad_video_share=?, ad_distinct_bodies=?, ad_stale=? WHERE id=?",
                (len(ads), max(ages) if ages else None,
                 round(video / len(ads), 2) if ads else None,
                 len(bodies), int(stale), cid))
            if stale:
                flagged += 1
                add_signal(con, cid, "ad_stale", "adlib", {
                    "active_ads": len(ads),
                    "oldest_days": max(ages) if ages else None,
                    "video_share": round(video / len(ads), 2),
                    "distinct_bodies": len(bodies),
                })
        con.commit()
        print(f"  {min(i+args.batch, len(rows))}/{len(rows)} "
              f"({flagged} stale so far)", flush=True)
    print(f"done: {flagged} brands flagged ad_stale")

if __name__ == "__main__":
    sys.exit(main())
