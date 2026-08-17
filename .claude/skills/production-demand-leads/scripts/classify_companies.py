"""Classify stored companies: direct brand vs supply-side vs enterprise vs junk.

Mechanical prefilters first (>10,000 headcount → ENTERPRISE_INHOUSE; obvious
supply-side names → AGENCY_OR_PRODUCTION), then GPT-4.1 on the rest using the
company's own signal evidence (job titles posted, exec post snippet, Form D
industry). Only BRAND rows are exported by default.

Idempotent: rows with a category are skipped. --reset clears categories first
(asks nothing — categories are cheap to recompute; signals are never touched).

Usage: python3 -W ignore classify_companies.py [--limit N] [--reset]
"""
import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from openai import AzureOpenAI

from store import (AZURE_API_KEY, AZURE_API_VERSION, AZURE_DEPLOYMENT_FAST,
                   AZURE_ENDPOINT, connect)

SUPPLY_RE = re.compile(
    r"staffing|recruit(ing|ment)|\bagency\b|productions\b|\bstudios\b|"
    r"media group|marketing group|creative group", re.I)
ENTERPRISE_SIZES = {"10,000+"}
VALID = {"BRAND", "AGENCY_OR_PRODUCTION", "ENTERPRISE_INHOUSE",
         "NONPROFIT_GOV_EDU", "UNKNOWN"}

SYSTEM = """You classify companies for a B2B list. The sender connects businesses
with commercial video production studios, so the target is a DIRECT company that
would BUY video production ("BRAND").
Return STRICT JSON: {"category": "...", "reason": "..."} with category one of:
- BRAND: a direct business (product, service, retail, restaurant, tech, health...)
  that could commission commercial video for its own marketing.
- AGENCY_OR_PRODUCTION: marketing/creative/ad agency, video production company,
  studio, staffing/recruiting firm, or freelancer collective — supply side, never email.
- ENTERPRISE_INHOUSE: very large corporation (roughly 10k+ staff or a household
  mega-brand) that runs in-house studios and buys through procurement.
- NONPROFIT_GOV_EDU: nonprofit, church, school/university, government body.
- UNKNOWN: cannot tell from the evidence.
Judge from the evidence given; do not guess a category off the name alone when
evidence contradicts it."""

def classify_one(client, name, size, evidence):
    try:
        resp = client.chat.completions.create(
            model=AZURE_DEPLOYMENT_FAST, temperature=0,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": SYSTEM},
                      {"role": "user", "content":
                       f"Company: {name}\nSize: {size or 'unknown'}\n"
                       f"Evidence:\n{evidence[:1500]}"}])
        v = json.loads(resp.choices[0].message.content)
        cat = v.get("category", "UNKNOWN")
        return (cat if cat in VALID else "UNKNOWN"), (v.get("reason") or "")[:200]
    except Exception as e:
        return "", f"error: {type(e).__name__}"

def evidence_for(con, cid):
    bits = []
    for typ, detail in con.execute(
            "SELECT type, detail FROM signals WHERE company_id=?", (cid,)):
        d = json.loads(detail)
        if typ == "video_job":
            bits.append(f"- posted job: {d.get('job_title')} "
                        f"({(d.get('desc') or '')[:200]})")
        elif typ == "new_exec":
            bits.append(f"- new exec joined: {d.get('person')} as {d.get('role')} "
                        f"(post: {(d.get('snippet') or '')[:150]})")
        elif typ == "funding":
            bits.append(f"- SEC Form D raise: ${d.get('amount_sold'):,} "
                        f"industry={d.get('industry')}")
    return "\n".join(bits) or "(no evidence)"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()

    con = connect()
    if args.reset:
        con.execute("UPDATE companies SET category='', classify_reason=''")
        con.commit()

    rows = con.execute(
        "SELECT id, name, size FROM companies WHERE category=''").fetchall()
    if args.limit:
        rows = rows[:args.limit]
    print(f"{len(rows)} companies to classify")

    # Mechanical prefilters
    pending = []
    for cid, name, size in rows:
        if size in ENTERPRISE_SIZES:
            con.execute("UPDATE companies SET category=?, classify_reason=? WHERE id=?",
                        ("ENTERPRISE_INHOUSE", "mechanical: 10,000+ headcount", cid))
        elif SUPPLY_RE.search(name):
            con.execute("UPDATE companies SET category=?, classify_reason=? WHERE id=?",
                        ("AGENCY_OR_PRODUCTION", "mechanical: supply-side name", cid))
        else:
            pending.append((cid, name, size))
    con.commit()
    print(f"  {len(rows)-len(pending)} settled mechanically, {len(pending)} to GPT-4.1")

    client = AzureOpenAI(azure_endpoint=AZURE_ENDPOINT, api_key=AZURE_API_KEY,
                         api_version=AZURE_API_VERSION)
    done = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(classify_one, client, name, size,
                            evidence_for(con, cid)): cid
                for cid, name, size in pending}
        for f in as_completed(futs):
            cid = futs[f]
            cat, reason = f.result()
            if cat:
                con.execute(
                    "UPDATE companies SET category=?, classify_reason=? WHERE id=?",
                    (cat, reason, cid))
            done += 1
            if done % 10 == 0:
                con.commit()
                print(f"  {done}/{len(pending)}", flush=True)
    con.commit()
    counts = dict(con.execute(
        "SELECT category, COUNT(*) FROM companies GROUP BY category"))
    print(f"done: {counts}")

if __name__ == "__main__":
    sys.exit(main())
