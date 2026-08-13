"""
Phase 1a: EPA Victoria licence register → SQLite store.

Pulls the full operating-licence layer from the Vic open-data WFS (no key
needed), filters to ICP-relevant activity codes (food/beverage processing +
energy-intensive industrial), drops government-ish holders, and upserts into
data/emaas.db. Idempotent — licence_number is the natural key.

These rows are the ANCHOR tier: an EPA licence for rendering / milk
processing / chemical works is hard evidence of industrial energy load
(evidence=epa_licence). Alcohol-primary businesses are stored but flagged
excluded='alcohol' rather than dropped, per Sherif's rule (fine if one
offering among many — flag is set only on obvious primary-trade matches).

Usage:
  python3 -W ignore ingest_epa.py [--dry_run]
"""

import argparse
import json
import os
import re
import sqlite3
import sys

import requests

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "emaas.db")
WFS_URL = (
    "https://opendata.maps.vic.gov.au/geoserver/wfs"
    "?service=WFS&version=2.0.0&request=GetFeature"
    "&typeNames=open-data-platform:epa_licence_point"
    "&outputFormat=application/json"
)

# activity code -> category. Only codes that imply sustained process load.
ICP_CODES = {
    "D01": "abattoir", "D02": "rendering", "D05": "pet_food",
    "D06": "food_processing", "D07": "milk_processing", "D08": "edible_oil",
    "B03": "fish_farm",
    "G01": "chemical_works", "G02": "plastics", "G04": "bulk_storage",
    "G05": "container_washing",
    "H05a": "glass_mfg", "H05b": "glass_reprocessing",
    "I01": "metal_works", "I03": "galvanising",
    "J01": "printing", "F01": "paper", "F02": "fibreboard",
    "A07a": "organic_waste", "A08": "waste_to_energy",
}

GOV_RE = re.compile(
    r"water|shire|council|city of|department|authority|university|hospital|health service",
    re.I,
)
ALCOHOL_RE = re.compile(
    r"brewer|brewing|winery|wines|distill|vineyard|\bcub\b|carlton & united", re.I
)


def ensure_store():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.execute("""
        CREATE TABLE IF NOT EXISTS companies (
            id INTEGER PRIMARY KEY,
            company TEXT NOT NULL,
            company_norm TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'VIC',
            suburb TEXT,
            address TEXT,
            website TEXT,
            employee_count TEXT,
            categories TEXT,          -- comma list (abattoir, rendering, ...)
            evidence TEXT NOT NULL,   -- epa_licence | job_ad | nabers | site
            evidence_detail TEXT,     -- licence no / job snippet / rating
            acn TEXT,
            excluded TEXT,            -- NULL | alcohol | government
            exported_at TEXT,
            UNIQUE(company_norm, state)
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS job_signals (
            id INTEGER PRIMARY KEY,
            job_key TEXT UNIQUE,
            company_norm TEXT,
            title TEXT,
            city TEXT,
            signal_type TEXT,         -- shift | equipment | both
            signal_text TEXT,
            date_published TEXT
        )
    """)
    return db


def norm_name(name):
    n = re.sub(r"[^a-z0-9 ]", "", name.lower())
    n = re.sub(
        r"\b(pty|ltd|limited|proprietary|holdings|group|australia|aust|the|co)\b",
        "", n)
    return re.sub(r"\s+", " ", n).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry_run", action="store_true")
    args = ap.parse_args()

    print("Pulling EPA licence layer ...")
    feats = requests.get(WFS_URL, timeout=120).json()["features"]
    print(f"  {len(feats)} licences in register")

    rows = []
    for f in feats:
        p = f["properties"]
        if p.get("status") != "Issued":
            continue
        codes = re.findall(r"([A-L]\d{2}[ab]?)", p.get("permission_activity") or "")
        cats = sorted({ICP_CODES[c] for c in codes if c in ICP_CODES})
        if not cats:
            continue
        name = (p.get("place_or_premises") or "").split("[")[0].strip().rstrip(".")
        if not name:
            continue
        excluded = None
        if GOV_RE.search(name):
            excluded = "government"
        elif ALCOHOL_RE.search(name):
            excluded = "alcohol"
        rows.append({
            "company": name.title(),
            "company_norm": norm_name(name),
            "suburb": p.get("suburb"),
            "address": p.get("premises_address"),
            "categories": ",".join(cats),
            "evidence_detail": p.get("licence_number"),
            "acn": p.get("acn"),
            "excluded": excluded,
        })

    # collapse multi-licence holders
    seen = {}
    for r in rows:
        prev = seen.get(r["company_norm"])
        if prev:
            cats = sorted(set(prev["categories"].split(",")) | set(r["categories"].split(",")))
            prev["categories"] = ",".join(cats)
        else:
            seen[r["company_norm"]] = r
    rows = list(seen.values())

    kept = [r for r in rows if not r["excluded"]]
    print(f"  {len(rows)} unique ICP-relevant holders "
          f"({len(rows) - len(kept)} flagged excluded)")

    if args.dry_run:
        for r in rows[:15]:
            print(f"  {r['company'][:50]:50s} {r['categories'][:30]:30s} "
                  f"{r['suburb']} {r['excluded'] or ''}")
        return

    db = ensure_store()
    n = 0
    for r in rows:
        db.execute("""
            INSERT INTO companies
                (company, company_norm, suburb, address, categories,
                 evidence, evidence_detail, acn, excluded)
            VALUES (?,?,?,?,?,'epa_licence',?,?,?)
            ON CONFLICT(company_norm, state) DO UPDATE SET
                categories=excluded.categories,
                evidence_detail=companies.evidence_detail
        """, (r["company"], r["company_norm"], r["suburb"], r["address"],
              r["categories"], r["evidence_detail"], r["acn"], r["excluded"]))
        n += 1
        if n % 10 == 0:
            db.commit()
    db.commit()
    total = db.execute(
        "SELECT COUNT(*) FROM companies WHERE evidence='epa_licence'"
    ).fetchone()[0]
    usable = db.execute(
        "SELECT COUNT(*) FROM companies WHERE evidence='epa_licence' AND excluded IS NULL"
    ).fetchone()[0]
    print(f"Store: {total} EPA companies ({usable} usable)")


if __name__ == "__main__":
    sys.exit(main())
