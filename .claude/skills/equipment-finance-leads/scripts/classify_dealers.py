"""
Phase 2 — classify stored companies: is this an independent medical/dental
equipment DEALER (the ICP) or not?

Two-step, cheapest-first:
  1. DETERMINISTIC giants denylist (finance_common.CAPTIVE_GIANTS) — Henry
     Schein / Patterson / Benco / OEMs run their own captive finance arms, so
     they are stamped CAPTIVE_GIANT and never reach the LLM. A dealer wants us
     precisely because it has NO captive arm; the giants are anti-ICP.
  2. GPT-4.1 (Azure fast deployment) over Google Maps metadata for everything
     else — name, category labels, domain, review count, city. No website
     scrape here; that spend is reserved for rows that survive.

Classes:
  DEALER            independent equipment dealer/distributor/VAR that SELLS
                    capital equipment (chairs, imaging, lasers, sterilizers,
                    DME) to medical/dental/vet practices — the ICP
  PRACTICE          an actual practice/clinic/hospital (end user, not a seller)
  MANUFACTURER      makes equipment; no local dealer/reseller motion
  CAPTIVE_GIANT     national distributor/OEM with its own finance arm
  SUPPLY_CONSUMABLES sells only consumables/materials (gloves, cement, film),
                    no capital equipment -> no financing need
  REPAIR_ONLY       service/repair only, no equipment sales
  OTHER             pharmacy, retail, unrelated
  UNCERTAIN         cannot tell from metadata — kept in store, not exported

Only DEALER rows ever reach a campaign sheet, so the classifier leans
UNCERTAIN when torn: a wrong DEALER wastes DM + email credits, an UNCERTAIN
just waits for a later refinement pass. Nothing is deleted.

Resume-safe: only classifies rows WHERE classification IS NULL. Commits per
batch of 20.

Usage:
  python3 -W ignore .claude/skills/equipment-finance-leads/scripts/classify_dealers.py [--limit N] [--dry_run]
"""
import argparse
import json
import os
import sys

import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from finance_common import get_db, log_run, is_captive_giant

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", ".env"))
AZ_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZ_KEY = os.getenv("AZURE_OPENAI_API_KEY")
AZ_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21")
AZ_MODEL = os.getenv("AZURE_OPENAI_DEPLOYMENT_FAST")

BATCH = 20
CLASSES = {"DEALER", "PRACTICE", "MANUFACTURER", "CAPTIVE_GIANT",
           "SUPPLY_CONSUMABLES", "REPAIR_ONLY", "OTHER", "UNCERTAIN"}

SYSTEM = """You classify companies scraped from Google Maps for a B2B campaign. The ICP is \
INDEPENDENT medical/dental equipment DEALERS: companies that SELL capital equipment \
(dental chairs and units, imaging/X-ray/CBCT, aesthetic lasers, sterilizers, surgical \
tables, durable medical equipment, veterinary equipment) to medical, dental and vet \
practices. These are the businesses that would offer their customers financing to close \
bigger deals.

For each company you get: name, Google Maps category labels, website domain, review count, city.

Classes:
- DEALER: independent equipment dealer / distributor / reseller / VAR that sells capital \
equipment to practices. Sales-and-service shops that also repair count as DEALER.
- PRACTICE: an actual care provider — dental office, medical clinic, hospital, imaging \
center, vet clinic, surgery center. The END USER, not a seller.
- MANUFACTURER: manufactures equipment with no local dealer/reseller motion.
- CAPTIVE_GIANT: a national distributor or OEM that runs its own financing arm (Henry \
Schein, Patterson, Benco, McKesson, GE HealthCare, Dentsply Sirona, etc.).
- SUPPLY_CONSUMABLES: sells only consumables/materials/supplies (gloves, impression \
material, film, scrubs) — no capital equipment, so no financing need.
- REPAIR_ONLY: equipment repair/calibration/service only, no sales.
- OTHER: pharmacy, general retail, software, unrelated business.
- UNCERTAIN: genuinely cannot tell from the metadata.

Rules:
- The signal is SELLS CAPITAL EQUIPMENT TO PRACTICES. A practice that happens to have a \
generic name is still PRACTICE.
- "Supply"/"supplies" in the name often means consumables -> SUPPLY_CONSUMABLES unless \
category/domain shows equipment sales.
- When torn between DEALER and anything else, choose UNCERTAIN. A wrong DEALER wastes \
enrichment credits; UNCERTAIN just waits.

Return JSON: {"results": [{"i": <index>, "class": "<CLASS>", "reason": "<max 12 words>"}, ...]} \
with exactly one entry per input index."""


def classify_batch(rows):
    lines = []
    for i, r in enumerate(rows):
        lines.append(f'{i}. name="{r["name"]}" | categories="{r["categories"] or r["category"] or ""}" '
                     f'| domain={r["domain"] or "none"} | reviews={r["reviews"] or 0} | city={r["city"] or ""}')
    try:
        resp = requests.post(
            f"{AZ_ENDPOINT}/openai/deployments/{AZ_MODEL}/chat/completions",
            params={"api-version": AZ_VERSION},
            headers={"api-key": AZ_KEY, "Content-Type": "application/json"},
            json={"messages": [{"role": "system", "content": SYSTEM},
                               {"role": "user", "content": "\n".join(lines)}],
                  "max_completion_tokens": 2000,
                  "response_format": {"type": "json_object"}},
            timeout=120)
        if resp.status_code != 200:
            print(f"  [!] Azure HTTP {resp.status_code}: {resp.text[:120]}")
            return None
        out = json.loads(resp.json()["choices"][0]["message"]["content"])
        return {r["i"]: (r["class"], r.get("reason", "")) for r in out.get("results", [])
                if r.get("class") in CLASSES}
    except Exception as e:
        print(f"  [!] {type(e).__name__}: {e}")
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="cap PENDING rows processed")
    ap.add_argument("--dry_run", action="store_true")
    args = ap.parse_args()

    conn = get_db()
    q = ("SELECT place_id, name, category, categories, domain, reviews, city "
         "FROM companies WHERE classification IS NULL ORDER BY metro, name")
    rows = [dict(zip(("place_id", "name", "category", "categories", "domain", "reviews", "city"), r))
            for r in conn.execute(q).fetchall()]

    # Step 1 — deterministic giants denylist, before any LLM call.
    giants = 0
    remaining = []
    for r in rows:
        if is_captive_giant(r["name"]):
            conn.execute("UPDATE companies SET classification='CAPTIVE_GIANT', "
                         "class_reason='captive-finance national (denylist)', "
                         "classified_at=datetime('now') WHERE place_id=?", (r["place_id"],))
            giants += 1
        else:
            remaining.append(r)
    conn.commit()
    if giants:
        print(f"Denylist: stamped {giants} CAPTIVE_GIANT rows")

    if args.limit:
        remaining = remaining[:args.limit]
    print(f"{len(remaining)} rows to LLM-classify")
    if args.dry_run:
        for r in remaining[:20]:
            print(f'  {r["name"]} | {r["categories"] or r["category"]} | {r["domain"]}')
        print("Dry run — no LLM calls, no writes.")
        return

    done = 0
    counts = {"CAPTIVE_GIANT": giants} if giants else {}
    for start in range(0, len(remaining), BATCH):
        batch = remaining[start:start + BATCH]
        result = classify_batch(batch)
        if result is None:
            print(f"  batch @{start} failed — skipping (rerun resumes here)")
            continue
        for i, r in enumerate(batch):
            cls, reason = result.get(i, ("UNCERTAIN", "no verdict returned"))
            conn.execute("UPDATE companies SET classification=?, class_reason=?, "
                         "classified_at=datetime('now') WHERE place_id=?",
                         (cls, reason, r["place_id"]))
            counts[cls] = counts.get(cls, 0) + 1
            done += 1
        conn.commit()
        print(f"  {done}/{len(remaining)} classified")

    print("\nBreakdown:")
    for cls, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {cls:18s} {n}")
    log_run(conn, "classify_dealers", f"classified {done} (+{giants} giants): {counts}")


if __name__ == "__main__":
    main()
