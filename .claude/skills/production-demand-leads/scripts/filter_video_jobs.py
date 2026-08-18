"""Phase-1.9 equivalent: AI relevance judge over video_job signals.

Jude's review of the first master sheet (2026-08-18) found the mechanical
title regex let through Indeed keyword drift (real estate advisers, billing
specialists, copy editors) and anti-signal roles (interns, generic content
creators). This judge classifies every video_job signal:

  STRONG     - the company is investing in professional video/content
               production (video producer, videographer, multimedia producer,
               brand content producer, creative/ad content strategist).
  WEAK       - marketing-adjacent; video demand plausible but not proven
               (brand manager, social media manager whose JD includes video).
  IRRELEVANT - keyword drift or anti-signal (sales, billing, copy editor,
               interns, generic UGC content creator).

Calibration examples in the prompt are Jude's calls verbatim. The verdict is
stored in the signal's detail JSON as "relevance" and consumed by
export_batch.py scoring (STRONG 2.0 / WEAK 0.5 / IRRELEVANT 0).

Usage: python3 -W ignore filter_video_jobs.py [--limit N] [--reset]
"""
import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from openai import AzureOpenAI

from store import (AZURE_API_KEY, AZURE_API_VERSION, AZURE_DEPLOYMENT_FAST,
                   AZURE_ENDPOINT, connect)

SYSTEM = """You judge whether a job posting signals that the company BUYS or
INVESTS IN professional video/content production. The sender connects brands
with commercial video production studios; the signal is "this company has
video marketing budget and a content capacity gap".

Return STRICT JSON: {"relevance": "STRONG" | "WEAK" | "IRRELEVANT", "reason": "..."}

STRONG - professional video/content production investment:
- Video Producer, Videographer, Multimedia Producer, Video Editor (staff)
- Brand Content Producer, Creative Producer, Video Marketing Manager
- "DTC Ecommerce Creative and Ad Strategist" / ad-creative content roles

WEAK - marketing-adjacent, video demand plausible but unproven:
- Brand Manager (okay per Jude)
- Social Media Manager ONLY when the description includes video content
  production duties; otherwise IRRELEVANT
- Roles producing online video classes/programs (e.g. instructor FOR online
  videos) when the company clearly publishes video products

IRRELEVANT - drift or anti-signal:
- ANY intern/internship role (a company hiring an intern for video wants it
  cheap - the opposite of a production buyer)
- Generic "Content Creator" (UGC/influencer-style, usually solo cheap content)
- Sales, billing, finance, legal, real estate adviser, territory/field sales,
  copy editor, admin, HR, engineering, and any non-marketing role
- Photographer-only roles with no video component

Judge from title AND description when given. When the title is marketing-ish
but there is no evidence of video duties, prefer WEAK over STRONG, and
IRRELEVANT over WEAK."""

def judge(client, title, desc):
    try:
        resp = client.chat.completions.create(
            model=AZURE_DEPLOYMENT_FAST, temperature=0,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": SYSTEM},
                      {"role": "user",
                       "content": f"Job title: {title}\nDescription: {(desc or '')[:800]}"}])
        v = json.loads(resp.choices[0].message.content)
        rel = v.get("relevance", "WEAK")
        return (rel if rel in ("STRONG", "WEAK", "IRRELEVANT") else "WEAK",
                (v.get("reason") or "")[:150])
    except Exception as e:
        return "", f"error: {type(e).__name__}"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()

    con = connect()
    rows = []
    for sig_id, detail in con.execute(
            "SELECT id, detail FROM signals WHERE type='video_job'"):
        d = json.loads(detail)
        if d.get("relevance") and not args.reset:
            continue
        rows.append((sig_id, d))
    if args.limit:
        rows = rows[:args.limit]
    print(f"{len(rows)} video_job signals to judge")

    client = AzureOpenAI(azure_endpoint=AZURE_ENDPOINT, api_key=AZURE_API_KEY,
                         api_version=AZURE_API_VERSION)
    counts = {"STRONG": 0, "WEAK": 0, "IRRELEVANT": 0}
    done = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(judge, client, d.get("job_title", ""),
                            d.get("desc", "")): (sig_id, d)
                for sig_id, d in rows}
        for f in as_completed(futs):
            sig_id, d = futs[f]
            rel, reason = f.result()
            if rel:
                d["relevance"] = rel
                d["relevance_reason"] = reason
                con.execute("UPDATE signals SET detail=? WHERE id=?",
                            (json.dumps(d, ensure_ascii=False), sig_id))
                counts[rel] += 1
            done += 1
            if done % 100 == 0:
                con.commit()
                print(f"  {done}/{len(rows)}", flush=True)
    con.commit()
    print(f"done: {counts}")

if __name__ == "__main__":
    sys.exit(main())
