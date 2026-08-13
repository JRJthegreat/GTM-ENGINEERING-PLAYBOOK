---
name: energy-emaas-leads
description: Energy GreenPrint (AU) demand pipeline — builds an evidence-qualified list of energy-intensive Victorian/NSW businesses (EPA licences, shift-pattern/process-equipment job ads, NABERS ratings) into SQLite, then exports 29-col campaign sheets for the standard enrichment stack. Use when the user asks to build the Energy GreenPrint / EMaaS list, scrape energy-intensity signals, or export an EMaaS campaign batch.
---

# energy-emaas-leads

Demand generation for **Energy GreenPrint** (Sherif Hani, Melbourne) — Energy
Management as a Service. Deliverable: 10 qualified introductions.

**The qualification problem this skill exists to solve:** the ICP gate is
electricity spend > A$90k/yr, which is not public. So no company enters the
store without *documented evidence of energy intensity* — an EPA licence, a
job ad publishing shift work or process equipment, or (planned) a NABERS
rating. Firmographics alone never qualify a row.

ICP (from Sherif's slide pack — see memory `project-energy-greenprint-au-campaign`):
2-shift/24-7 operation, 50–1,000 staff (his floor was 200; we widened for
refrigeration-heavy sites), VIC first (Dandenong/Keysborough/Campbellfield/
Thomastown + regional food corridor), NSW second wave. Excluded: alcohol-primary,
government, and — this vertical's agency-trap — **service contractors** (HVAC/
refrigeration/FM firms advertise the same trades but service equipment rather
than own it).

## Architecture

SQLite store (`data/emaas.db`, gitignored) like `nppes-new-clinics` /
`production-directory-leads`; sheets are batches cut from it. Two tables:
`companies` (unique on company_norm+state, evidence-tagged, excluded flag)
and `job_signals` (one row per signal-bearing ad).

| Phase | Script | Purpose |
|-------|--------|---------|
| 1a | `ingest_epa.py` | EPA VIC operating-licence WFS → ICP activity codes (D0x food, G0x chemical, H/I/J industrial) → store. The ANCHOR tier (~170 companies), evidence=`epa_licence` |
| 1b | `scrape_indeed_signals.py` | valig~indeed-jobs-scraper, country=au, keyword × VIC-city grid. Regex-classifies ads (shift / equipment / both); discards no-signal ads; flags agencies, contractors, alcohol, government. Employer block carries corporateWebsite + employeesCount, so many rows arrive pre-filled. evidence=`job_ad` |
| 1.75 | (Claude-in-session judge — planned) | Collect uncertain rows (is it an owner-operator or a service firm?) → judge → apply. Extend the repo's judge pattern; no per-row LLM calls |
| 2 | `export_batch.py` (planned) | Store → Google Sheet, 29-col base layout, company/website/city/state/status at K/L/R/S/AB so `exa-website-enrichment` and `apollo-dm-waterfall` run unmodified |

Downstream from the sheet: `exa-website-enrichment` (rows without a website),
`apollo-dm-waterfall` (RANK_SYSTEM retargeted: MD/GM/Ops Director/COO primary,
Plant/Production/Engineering/Maintenance Manager second persona), AMF valid-only.
Two contacts per company — do NOT dedupe to one row per company.

## Commands

```bash
# Phase 1a — EPA anchor tier (free, no key, idempotent)
python3 -W ignore .claude/skills/energy-emaas-leads/scripts/ingest_epa.py [--dry_run]

# Phase 1b — Indeed signal scrape (Apify)
python3 -W ignore .claude/skills/energy-emaas-leads/scripts/scrape_indeed_signals.py \
    --limit 25 [--cities "A,B"] [--keywords "A,B"] [--workers 6] [--dry_run]
```

## Quirks

- The WFS layer is the whole VIC register (~723 licences); ~24% are
  ICP-relevant after code filtering. Sewage/landfill licensees are water
  authorities and councils — the GOV regex drops them by name.
- Excluded rows are stored flagged (`alcohol` / `government` / `contractor`),
  never silently dropped — Sherif's alcohol rule is primary-trade only, so a
  judge pass can rescue mixed businesses.
- `employeesCount` comes from Indeed's employer block as a band string
  ("201 to 500"), which is the GLOBAL headcount for multinationals (Asahi:
  "10,000+"), not the AU site. Treat as a hint, not a gate.
- The Aug 2026 validation batch showed ~92% of ads from trade-role keywords
  carry signal, but several employers were HVAC/FM contractors — hence
  CONTRACTOR_RE and the planned judge phase.
- NSW second wave: rerun 1b with a NSW city grid + state column; EPA NSW has
  a different (POEO licence) register — separate ingest when needed.
