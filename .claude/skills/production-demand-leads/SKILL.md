# production-demand-leads

Demand side of the commercial video production lane: detect direct brands that
need commercial video NOW, by signal, and stack signals per company. Supply
side is `production-directory-leads`; this skill finds who those houses should
be connected to. Built 2026-08-17 from a $0.28 five-signal pilot (results:
`.claude/scripts/demand_pilot/`, local only).

**SQLite-backed** (`data/demand.db`, gitignored) like `nppes-new-clinics` /
`energy-emaas-leads`: collectors upsert companies keyed by normalized name and
append typed signals; sheets are scored batches cut from the store. The same
brand surfaced by two collectors lands on one row — stacking is the whole
point (single signals reply ~1% in this vertical per market anecdote; stacked
signals are the edge).

## Signals

| type | collector | source | cost | intent logic |
|------|-----------|--------|------|--------------|
| `video_job` | `collect_video_jobs.py` | valig~indeed-jobs-scraper, video keywords × supply-side metros | ~$0.001/job | video budget confirmed + 60-day capacity gap (counter-position pitch) |
| `new_exec` | `collect_new_execs.py` | harvestapi~linkedin-post-search, quoted join phrases → GPT-4.1 extract | ~$0.0005/post | new Head of Brand/VP Marketing, first 90 days, no locked-in vendors; the person IS the DM |
| `funding` | `collect_formd.py` | EDGAR daily form indexes, Form D new notices, consumer groups $1M-$50M | free | fresh brand-building budget, often pre-press; exec names free in relatedPersonInfo |
| `ad_stale` | `enrich_adlibrary.py` | curious_coder~facebook-ads-library-scraper per stored brand | ~$0.0005/ad | actively spending on visibly exhausted creative; the icebreaker engine |

`ad_stale` is an **enrichment step, not a discovery source** — it only runs
over companies already in the store, and page names are fuzzy-matched back to
the company so keyword-search strangers don't mis-attribute.

## Order

```
collect_video_jobs.py → collect_new_execs.py → collect_formd.py   (any order)
  → filter_video_jobs.py      (GPT-4.1 job-relevance judge — Jude's calibration:
                               interns/UGC/content-creator = anti-signal, drift = out)
  → classify_companies.py     (mechanical prefilters + GPT-4.1; BRAND survives)
  → enrich_adlibrary.py       (BRAND rows only)
  → export_batch.py           (score+stack → 29-col sheet, delta-by-default)
```

Scoring: ad_stale 3 · video_job 2 · new_exec 2 · funding 1.5 · +1 per extra
distinct signal type. `--min_score`/`--max_rows` shape the batch.

## Sheet layout

29-col base schema; K/L/R/S/AB = company/website/city/state/status, so
`exa-website-enrichment` and `apollo-dm-waterfall` run with default flags.
AB = signal stack summary (score + per-signal detail). AC = Indeed URL for
job-sourced rows (Jude's rule). `new_exec` rows arrive with T/U/V prefilled
from the announcement — that person is the lead; verify (phase 2.5) before any
email spend. Form D rows prefill T/U from the filing's first related person.

## Downstream (standard stack, gated)

1. Websites: `exa-website-enrichment` (collect → Claude judges in-session → apply).
2. DM/email: clone `apollo-dm-waterfall`'s convention — **DM ladder for this
   vertical is an untested hypothesis; get Jude's sign-off before AMF spend**
   (new-pipeline validation gate, same as nppes).
3. Copy + push: template approval gate, then DRAFT campaign. Phases 4/5 are
   manual stops as everywhere.

## Cautions

- Geography default = supply-side metros (LA/NYC/Chicago/Atlanta/Austin/Miami/
  Nashville). New geography is Jude's call.
- ENTERPRISE_INHOUSE (10k+) is excluded from export by classify, not deleted —
  the cap is a hypothesis for this vertical, not measured like healthcare's 500.
- Indeed keyword drift is real (marketing/booking roles come back for video
  queries); GPT-4.1 classify judges from signal evidence, and export only takes
  BRAND.
- Collectors are idempotent (UNIQUE on company+type+key); re-running is safe
  and is the resume path after a partial failure.
