"""
LENDER side, APPLY step — write Claude's in-session fit verdicts to the
lenders table (the judge half of the Claude-in-session flow that pull_lenders.py
collects for).

Hallucination guard: a verdict for a domain not present in the lenders table is
refused, and a fit value outside {KEEP, SKIP, UNCERTAIN} is refused — the guard
exists against Claude's own slips, not only a model's.

Verdicts file shape:
  {"verdicts": [{"domain": "somelender.com", "fit": "KEEP", "reason": "small-ticket medical vendor finance"}, ...]}

Usage:
  python3 -W ignore .claude/skills/equipment-finance-leads/scripts/apply_lender_fit.py --verdicts FILE [--dry_run]
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from finance_common import get_db, log_run

VALID = {"KEEP", "SKIP", "UNCERTAIN"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verdicts", required=True)
    ap.add_argument("--dry_run", action="store_true")
    args = ap.parse_args()

    with open(args.verdicts) as f:
        verdicts = json.load(f)["verdicts"]

    conn = get_db()
    known = {d for (d,) in conn.execute("SELECT domain FROM lenders")}

    applied = refused = 0
    for v in verdicts:
        domain = (v.get("domain") or "").strip().lower()
        fit = (v.get("fit") or "").strip().upper()
        reason = (v.get("reason") or "").strip()[:200]
        if domain not in known:
            print(f"  [refused] unknown domain not in store: {domain}")
            refused += 1
            continue
        if fit not in VALID:
            print(f"  [refused] bad fit '{fit}' for {domain}")
            refused += 1
            continue
        if not args.dry_run:
            conn.execute("UPDATE lenders SET fit=?, fit_reason=? WHERE domain=?",
                         (fit, reason, domain))
        applied += 1
    if not args.dry_run:
        conn.commit()

    kept = conn.execute("SELECT COUNT(*) FROM lenders WHERE fit='KEEP'").fetchone()[0]
    print(f"\n{'(dry) ' if args.dry_run else ''}applied {applied}, refused {refused}. "
          f"KEEP lenders in store: {kept}")
    if not args.dry_run:
        log_run(conn, "apply_lender_fit", f"applied {applied}, refused {refused}, keep={kept}")


if __name__ == "__main__":
    main()
