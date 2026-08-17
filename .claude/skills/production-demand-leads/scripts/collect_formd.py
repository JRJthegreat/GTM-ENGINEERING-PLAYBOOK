"""Signal collector: SEC Form D filings (free, EDGAR daily indexes).

Fresh capital = brand-building budget, filed within 15 days of first sale and
often before any press. New notices only (skips D/A amendments). Keeps the
consumer-ish industry groups in the $1M-$50M band; related persons (execs)
ride along free like the NPPES Authorized Official.

Usage: python3 -W ignore collect_formd.py [--days 10] [--cap 1500] [--dry_run]
"""
import argparse
import re
import sys
import time
from datetime import date, timedelta

import requests

from store import add_signal, connect, upsert_company

HDRS = {"User-Agent": "NEXAM AI research jude@ainexam.com"}
MIN_AMT, MAX_AMT = 1_000_000, 50_000_000
CONSUMER_GROUPS = {
    "Retailing", "Restaurants", "Manufacturing", "Health Care",
    "Other Health Care", "Business Services", "Other Technology",
    "Computers", "Telecommunications", "Other", "Consumer Goods",
    "Lodging & Conventions", "Travel & Tourism", "Tourism",
}

def tag(x, name):
    m = re.search(r"<%s>(.*?)</%s>" % (name, name), x, re.S)
    return m.group(1).strip() if m else ""

def quarter(d):
    return (d.month - 1) // 3 + 1

def business_days_back(n):
    out, d = [], date.today()
    while len(out) < n:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            out.append(d)
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=10)
    ap.add_argument("--cap", type=int, default=1500)
    ap.add_argument("--dry_run", action="store_true")
    args = ap.parse_args()

    days = business_days_back(args.days)
    print(f"indexing {len(days)} business days: {days[-1]} → {days[0]}")
    entries = []
    for d in days:
        url = (f"https://www.sec.gov/Archives/edgar/daily-index/"
               f"{d.year}/QTR{quarter(d)}/form.{d.strftime('%Y%m%d')}.idx")
        r = requests.get(url, headers=HDRS, timeout=30)
        if r.status_code != 200:
            print(f"  [!] {d}: HTTP {r.status_code}", flush=True)
            continue
        for line in r.text.splitlines():
            if not line.startswith("D "):
                continue
            parts = re.split(r"\s{2,}", line.strip())
            if len(parts) >= 5 and parts[0] == "D":
                entries.append({"company": parts[1], "date": parts[3],
                                "path": parts[4]})
        time.sleep(0.2)
    print(f"{len(entries)} Form D notices indexed")
    if args.dry_run:
        return

    con = connect()
    kept = fetched = 0
    for e in entries:
        if fetched >= args.cap:
            print(f"  [cap] stopped at {args.cap} fetches "
                  f"({len(entries)-fetched} unfetched)", flush=True)
            break
        try:
            r = requests.get("https://www.sec.gov/Archives/" + e["path"],
                             headers=HDRS, timeout=30)
            fetched += 1
            if r.status_code != 200:
                continue
            x = r.text
            amt_sold = tag(x, "totalAmountSold")
            amt = int(amt_sold) if amt_sold.isdigit() else 0
            industry = tag(x, "industryGroupType")
            if not (industry in CONSUMER_GROUPS and MIN_AMT <= amt <= MAX_AMT):
                continue
            people = []
            for m in re.finditer(r"<relatedPersonInfo>(.*?)</relatedPersonInfo>",
                                 x, re.S):
                b = m.group(1)
                people.append({
                    "first": tag(b, "firstName"), "last": tag(b, "lastName"),
                    "roles": re.findall(r"<relationship>(.*?)</relationship>", b),
                })
            name = tag(x, "entityName") or e["company"]
            accession = e["path"].rsplit("/", 1)[-1].replace(".txt", "")
            cid = upsert_company(con, name, state=tag(x, "stateOrCountry"))
            if cid is None:
                continue
            if add_signal(con, cid, "funding", accession, {
                    "amount_sold": amt, "industry": industry,
                    "total_offering": tag(x, "totalOfferingAmount"),
                    "people": people[:3],
            }, e["date"]):
                kept += 1
            con.commit()
        except requests.RequestException:
            pass
        if fetched % 200 == 0:
            print(f"  {fetched} fetched, {kept} kept", flush=True)
        time.sleep(0.13)
    con.commit()
    print(f"done: {fetched} fetched, {kept} funding signals stored")

if __name__ == "__main__":
    sys.exit(main())
