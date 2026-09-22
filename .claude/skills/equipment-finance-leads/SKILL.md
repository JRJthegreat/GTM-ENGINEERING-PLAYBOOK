---
name: equipment-finance-leads
description: Equipment-finance connector lane. Two sides in one SQLite store. DEALER side scrapes independent medical/dental equipment dealers/distributors from Google Maps across a NY metro grid, classifies out practices/manufacturers/captive-finance giants, and exports 29-col campaign sheets — the cold ICP we email, pitched a point-of-sale financing partner so they close deals that stall on price. LENDER side gathers US small-ticket equipment leasing companies via Google search into a curated shortlist to pitch (land ONE as the client). Use when the user asks to build the equipment-finance list, scrape equipment dealers, or source equipment leasing companies.
---

# equipment-finance-leads

NEXAM connector campaign for **equipment finance**, US, **starting NY** (Jude,
2026-09-07). Modeled on the Grenke Australia play but for the US market.

## The model — read this first

This is a **two-sided connector**, and the two sides are deliberately
**asymmetric** — do not try to make them equal-sized.

- **DEALER side (the cold ICP we email).** Independent medical/dental
  equipment **dealers, distributors and VARs** — the companies that *sell*
  dental chairs, imaging, sterilizers, aesthetic lasers and DME to practices.
  The pitch: offer their customers point-of-sale financing so they close the
  deals that stall on price. This is a real connector play — we intro a dealer
  to a finance specialist, we don't sell anything. Volume target ~400-500.
- **LENDER side (the specialist we connect them to = the eventual client).**
  US small-ticket equipment **leasing/finance companies**. This is a **curated
  shortlist to pitch, not a cold campaign** — the fitted universe is only a
  few dozen companies nationwide (measured: 24 KEEP / 33 incl. banks out of 57
  gathered on the first pull, 2026-09-07). Land ONE as the client by showing
  them the warm dealer demand ("I've got NY medical/dental dealers asking for a
  financing partner — want the channel?").

**Spec build (no client yet).** Dealer copy stays specialist-agnostic ("a US
equipment finance partner") until a lender signs. The dealer campaign will
produce "yes, connect me" replies *before* there's a lender to hand them to —
that's demand-first to land the client, so replies need a holding line
("finalizing partner terms, can I grab your details"). Decide reply posture
before sending.

**Section 179** (full-cost deduction on financed equipment placed in service
before Dec 31) is the urgency layer for the dealer copy — the closest thing
this vertical has to a pain signal, and Q4 is the window.

## Store

SQLite at `data/finance.db` (gitignored, WAL), same model as
`production-house-leads`. Two tables:
- `companies` — DEALER side, scraped from Google Maps, classified in place.
- `lenders` — LENDER side, gathered from Google search, fit-judged in place.

## DEALER pipeline

| Phase | Script | Purpose |
|-------|--------|---------|
| 1 | `scrape_maps.py` | Apify `compass~crawler-google-places` over category × NY-metro grid → `companies`. One run per location query, idempotent by `place_id`. |
| 2 | `classify_dealers.py` | Deterministic captive-giant denylist first, then GPT-4.1 over Maps metadata → `DEALER` / `PRACTICE` / `MANUFACTURER` / `CAPTIVE_GIANT` / `SUPPLY_CONSUMABLES` / `REPAIR_ONLY` / `OTHER` / `UNCERTAIN`. Only `DEALER` exports; nothing deleted. |
| 3 | `export_batch.py` | Delta export of `DEALER` rows → 29-col base-schema Google Sheet (K/L/R/S/AB anchors), one row per domain, shuffled, stamped `exported_at`/`batch_id`. |

Then the standard tail runs against the exported sheet unmodified:
`exa-website-enrichment` (only if domains need proving) → `apollo-dm-waterfall`
+ AMF companions → connector copy generator → `push_campaign.py`.

**DM target (hypothesis — no campaign data yet):** owner / president / founder
at small dealers; **VP Sales / Sales Director** at mid-size ones, since the
pitch is "close more sales." Retarget the waterfall's `RANK_SYSTEM` for
"authority to sign a channel/finance partnership." Get Jude's sign-off before
AMF spend, per the repo's validate-before-enrichment gate.

## LENDER pipeline

| Step | Script | Purpose |
|------|--------|---------|
| Collect | `pull_lenders.py` | Apify `apify~google-search-scraper` over `lender_queries` → `lenders` table, junk hosts filtered, `fit=NULL`. |
| Judge | *Claude in-session* | Read the collected rows, judge fit: KEEP = US small/mid-ticket, medical/dental-friendly, vendor/channel finance. SKIP = foreign, big banks, OEM captives, media, software, law firms. |
| Apply | `apply_lender_fit.py --verdicts FILE` | Write verdicts (hallucination-guarded: unknown domain or bad fit value refused). |
| Export | `export_lenders.py [--include_uncertain]` | KEEP (+optionally UNCERTAIN banks) → shortlist sheet for the BD/pitch motion. DM here is **VP Vendor Finance / Director of Originations / Head of Partnerships**, or the owner at a small independent lessor — different from the dealer side. |

## Config (`config/settings.json`)

`search_terms` (dealer Maps queries), `metros` (NY grid: NYC 5 boroughs, Long
Island, Westchester/Hudson, Buffalo, Rochester, Syracuse, Albany),
`lender_queries`, batch sizes. **Geography is Jude's call** — NY first; expand
grid (or widen beyond medical/dental) only if the NY dealer pool falls short of
the target, and surface the real ceiling rather than silently padding.

## Commands

```bash
# DEALER — validate the pool cheaply first
python3 -W ignore .claude/skills/equipment-finance-leads/scripts/scrape_maps.py --dry_run
python3 -W ignore .claude/skills/equipment-finance-leads/scripts/scrape_maps.py --metros NYC --max_per_search 60
# full NY grid
python3 -W ignore .claude/skills/equipment-finance-leads/scripts/scrape_maps.py
python3 -W ignore .claude/skills/equipment-finance-leads/scripts/classify_dealers.py
python3 -W ignore .claude/skills/equipment-finance-leads/scripts/export_batch.py --dry_run
python3 -W ignore .claude/skills/equipment-finance-leads/scripts/export_batch.py --size 500

# LENDER — collect, judge in-session, apply, export
python3 -W ignore .claude/skills/equipment-finance-leads/scripts/pull_lenders.py
python3 -W ignore .claude/skills/equipment-finance-leads/scripts/apply_lender_fit.py --verdicts VERDICTS.json
python3 -W ignore .claude/skills/equipment-finance-leads/scripts/export_lenders.py --include_uncertain
```

## Gotchas

- `data/finance.db` is gitignored; a fresh clone looks empty — rebuild by
  re-running phase 1 + `pull_lenders.py`.
- The captive-giants denylist (`finance_common.CAPTIVE_GIANTS`) is the
  make-or-break list-quality cut: a dealer wants us *because* it has no captive
  finance arm, so Schein/Patterson/Benco/OEMs must never export as `DEALER`.
- `classify_dealers.py` uses GPT-4.1 (the direct `production-house-leads`
  analog). A Claude-in-session judge refinement pass over `UNCERTAIN` rows can
  be added later if precision needs it — extend, don't swap.
- All standing repo rules apply to the copy/push tail: connector framing, no
  sign-off, text-only, company-name casualization only (ask before recipient
  first-name casualization), valid-emails-only, by-tag sending accounts,
  DRAFT-then-manual-activation.
