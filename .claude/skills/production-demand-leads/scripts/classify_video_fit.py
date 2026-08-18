"""Second classify dimension: how likely is this company to BUY commercial video?

Jude's call (2026-08-18): the funding signal swept in deep-tech/industrial/
medical raisers that will never commission commercial production. This pass
tags every BRAND company:

  DTC_CONSUMER - consumer-facing brand (DTC, ecom, CPG, skincare, food & bev,
                 apparel, consumer services/venues). The thesis ICP.
  B2B_SOFT     - B2B software/services with a marketing motion (explainers,
                 brand films plausible). Secondary angle at best.
  DEEP_TECH    - industrial, biotech, medical devices, mining, infra, finance
                 vehicles. Never a commercial-production buyer; parked.
  UNKNOWN      - cannot tell.

Stored in companies.fit; export/copy tiers read it.

Usage: python3 -W ignore classify_video_fit.py [--limit N] [--reset]
"""
import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from openai import AzureOpenAI

from store import (AZURE_API_KEY, AZURE_API_VERSION, AZURE_DEPLOYMENT_FAST,
                   AZURE_ENDPOINT, connect)

VALID = {"DTC_CONSUMER", "B2B_SOFT", "DEEP_TECH", "UNKNOWN"}

SYSTEM = """You judge how likely a company is to commission COMMERCIAL VIDEO
PRODUCTION (brand films, ads, social content) from an external studio.

Return STRICT JSON: {"fit": "DTC_CONSUMER" | "B2B_SOFT" | "DEEP_TECH" | "UNKNOWN",
"reason": "..."}

DTC_CONSUMER - consumer-facing: DTC/ecommerce brands, CPG, skincare/beauty,
food & beverage, apparel/fashion, consumer health & wellness, restaurants,
fitness, consumer apps/entertainment, local consumer services. These buy
brand and social video constantly.

B2B_SOFT - B2B software, SaaS, professional/business services with a real
marketing motion. They buy product explainers and brand films sometimes.

DEEP_TECH - industrial equipment, manufacturing components, biotech/pharma,
medical devices, mining/energy/infrastructure, logistics infra, financial
vehicles/holdcos/exchanges, defense. Effectively never buys commercial
production for consumer-style marketing.

Judge from the company name, website domain, and context given."""

def judge(client, name, site, ctx):
    try:
        resp = client.chat.completions.create(
            model=AZURE_DEPLOYMENT_FAST, temperature=0,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": SYSTEM},
                      {"role": "user",
                       "content": f"Company: {name}\nWebsite: {site or 'unknown'}\n"
                                  f"Context: {ctx[:600]}"}])
        v = json.loads(resp.choices[0].message.content)
        fit = v.get("fit", "UNKNOWN")
        return (fit if fit in VALID else "UNKNOWN"), (v.get("reason") or "")[:150]
    except Exception as e:
        return "", f"error: {type(e).__name__}"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()

    con = connect()
    try:
        con.execute("ALTER TABLE companies ADD COLUMN fit TEXT DEFAULT ''")
    except Exception:
        pass
    if args.reset:
        con.execute("UPDATE companies SET fit='' WHERE category='BRAND'")
    con.commit()

    rows = []
    for cid, name, site in con.execute(
            "SELECT id, name, website FROM companies"
            " WHERE category='BRAND' AND fit=''").fetchall():
        bits = []
        for t, d in con.execute("SELECT type, detail FROM signals"
                                " WHERE company_id=?", (cid,)):
            dd = json.loads(d)
            if t == "funding":
                bits.append(f"SEC raise ${dd.get('amount_sold',0):,} "
                            f"industry={dd.get('industry')}")
            elif t == "video_job" and dd.get("relevance") != "IRRELEVANT":
                bits.append(f"job posted: {dd.get('job_title')}")
        rows.append((cid, name, site, "; ".join(bits) or "(none)"))
    if args.limit:
        rows = rows[:args.limit]
    print(f"{len(rows)} companies to fit-tag")

    client = AzureOpenAI(azure_endpoint=AZURE_ENDPOINT, api_key=AZURE_API_KEY,
                         api_version=AZURE_API_VERSION)
    counts = {k: 0 for k in VALID}
    done = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(judge, client, n, s, c): cid
                for cid, n, s, c in rows}
        for f in as_completed(futs):
            cid = futs[f]
            fit, reason = f.result()
            if fit:
                con.execute("UPDATE companies SET fit=? WHERE id=?", (fit, cid))
                counts[fit] += 1
            done += 1
            if done % 100 == 0:
                con.commit()
                print(f"  {done}/{len(rows)}", flush=True)
    con.commit()
    print(f"done: {counts}")

if __name__ == "__main__":
    sys.exit(main())
