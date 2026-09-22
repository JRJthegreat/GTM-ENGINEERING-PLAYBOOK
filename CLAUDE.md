# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Repo Is

NEXAM AI's recruitment lead generation system — Claude Code skills and Python scripts that scrape job postings, find decision makers, discover emails, generate personalized outreach, and push leads to Instantly campaigns. All code lives under `.claude/` (skills, agents, auth, env). There are no top-level source files — the root `.html` is a generated campaign report, not code.

**A Google Sheet is the database.** Almost every script reads a sheet, enriches rows in place, and writes back — there is no local model layer, no ORM, no intermediate store. That is why column constants, batch-of-10 writes, and idempotent skip-if-filled logic carry so much weight below. The exceptions are `nppes-new-clinics`, `production-house-leads`, `production-directory-leads`, `production-demand-leads`, `energy-emaas-leads`, and `equipment-finance-leads`, which use SQLite upstream — though all of them then build sheets and work them like the rest.

## All Pipelines

Each pipeline is a Claude Code skill with its own `SKILL.md` (authoritative detail) and `scripts/` directory. Exceptions: `healthcare-staffing-enrichment`, `recruitment-email-gen`, `sba-campaigns`, and `trades-staffing-leads` have no `SKILL.md` — their sections below plus script docstrings are the reference.

| Skill | Source | Niche | Geography |
|-------|--------|-------|-----------|
| `scrape-hr-leads` | TheirStack | HR roles | US |
| `scrape-tech-leads` | TheirStack | Tech roles | Europe + Gulf |
| `hr-leads-indeed` | Indeed (Apify) | HR roles | US states |
| `hr-linkedin-leads` | LinkedIn (Apify) | HR specialist roles (30-45 day pain window) | US cities |
| `tech-leads-indeed` | Indeed (Apify) | Engineering roles | Pan-EU cities |
| `civil-engineering-leads-indeed` | Indeed (Apify) | Civil/construction roles | UK cities |
| `healthcare-linkedin-leads` | LinkedIn (Apify) | Nurse Practitioner (low-applicant signal) | NY + MD |
| `verify-leads` | Any sheet | Re-verify DMs + emails | Any |
| `sba-campaigns` | SBA data | Borrower + lender outreach | US |
| `healthcare-staffing-enrichment` | Any sheet | Classify/enrich healthcare staffing agencies | US |
| `recruitment-email-gen` | Any sheet | Niche-agnostic supply-side outreach to recruitment agencies | Any (currently AU) |
| `healthcare-demand-pipeline` | Indeed (Apify) | Clinical roles — ask Jude for position types per client | Ask Jude for states per client (Indiana LIVE; Texas + Florida clones exist) |
| `apollo-dm-waterfall` | Any sheet | DM discovery + verified email (~1 AMF credit, 0 Apollo credits) | Any |
| `exa-website-enrichment` | Any sheet | Company domain resolution via Exa (proof-on-page gate) + last-resort DM name discovery | Any |
| `personalized-icebreakers` | Any sheet | Deep-research retarget campaign (LinkedIn + site → icebreaker → body → push) | Any |
| `nppes-new-clinics` | CMS NPPES bulk files | Newly-registered medical practices (pre-job-ad demand) | All 50 states + DC, filtered at export |
| `production-house-leads` | Google Maps (Apify) | Commercial video production houses (supply side of the production lane) | LA, NYC, US secondary hubs, Toronto, London, Amsterdam, Berlin |
| `production-directory-leads` | ProductionHub directory (custom Apify actor) | Commercial video production houses — **the live source for this lane**; the Maps store above is parked | US + Canada metros only (LA, NYC, Austin, Nashville, Chicago, Miami, Atlanta, Toronto) |
| `production-demand-leads` | Indeed + LinkedIn posts + EDGAR Form D + FB Ad Library | Demand side of the production lane — brands needing commercial video NOW, signal-stacked per company | US supply-side metros (LA/NYC/Chicago/Atlanta/Austin/Miami/Nashville) |
| `hirebase-healthcare-leads` | HireBase job export | Healthcare demand — two lanes (Speech Language Pathologist + General Healthcare); **both campaigns ACTIVE since 2026-08-20** | US |
| `energy-emaas-leads` | EPA VIC licence register (free WFS) + Indeed + SEEK (Apify) + AI Ark export | Energy-intensive businesses for Energy GreenPrint's EMaaS offer — evidence-qualified only (licence or signal-bearing job ad); separate AI Ark food&bev contact-level lane | AU: VIC first, NSW/QLD via `--state` + city grid |
| `equipment-finance-leads` | Sales Navigator exports (live lender lane) + Google Maps (dealer lane, parked) | D2C connector campaign to small-ticket equipment lenders, pitched borrower demand | US, sliced by state (TX first) |
| `trades-staffing-leads` | AI Ark trades export | Trades/construction staffing agencies (supply side; Sep 2026 pivot from healthcare) | US nationwide |

Utilities: `casualize-names`, `instantly-autoreply`, `add-webhook`, `local-server`. (`classify-leads` and `scrape-leads` are empty leftover directories — ignore them.)

**Skills do not own all their phases.** `hr-linkedin-leads` ships only 3 scripts (`scrape_and_pull.py`, `pull_dataset.py`, `enrich_company_profiles.py`) — everything from phase 1.5 onward is run out of `hr-leads-indeed/scripts/`, because both write the same 29-col schema. Calling another skill's script against your sheet is the normal pattern here, not a smell; check the SKILL.md's phase table for which directory each phase actually lives in.

⚠️ **`healthcare-linkedin-leads` is the exception — it does NOT hand off, and it is NOT on the 29-col schema.** Its own `SKILL.md` still says the downstream is "to be built"; that is stale. It ships 16 scripts and a complete self-contained pipeline on **its own column layout** (see below). Never point `hr-leads-indeed` scripts at its sheet — the `COL_*` constants do not line up.

## Phase Architecture (Indeed / LinkedIn pipelines)

The Indeed and LinkedIn pipelines share a common phase skeleton. All detailed phase commands live in each skill's `SKILL.md` — treat that as the authoritative reference.

| Phase | Script | Purpose |
|-------|--------|---------|
| 1 | `scrape_and_pull.py` | Scrape source → new Google Sheet |
| 1 (fallback) | `pull_dataset.py` | Ingest a pre-existing Apify dataset ID |
| 1.75 | `classify_companies.py` | LLM-classify companies; delete agencies / job boards |
| 1.8 | `dedupe_by_company.py` | One row per company (highest seniority wins; oldest posting tiebreaks) |
| 1.9 | `ai_filter_jobs.py` | AI relevance filter — drops non-target job titles |
| 1.9x | `find_company_domains.py` / `find_company_sizes.py` | Resolve official domain + headcount |
| 2 | `find_dm.py` | Find decision maker via Google Search + LinkedIn snippets |
| 2.5 | `verify_dms.py` | Verify DM is actually employed at target (Apify LinkedIn profile scrape) |
| 3 | `enrich_emails.py` | AnyMail Finder — person endpoint (DM known) or /decision-maker fallback |
| 3.5 | `find_dm_amf.py` | AMF rescue pass — retry `not_found` rows; `hr-leads-indeed` only |
| 4 | `generate_emails.py` | LLM-generate personalized email body (**requires template approval first**) |
| 5 | `push_campaign.py` | Push to Instantly campaign one lead at a time |

Not every pipeline has every phase — check the skill's `SKILL.md` for the exact sequence.

**`healthcare-demand-pipeline` replaces the retired `healthcare-leads-indeed` skill** (that directory is gone). Only five shared scripts survived the move into `healthcare-demand-pipeline/scripts/`: `scrape_and_pull.py`, `pull_dataset.py`, `reingest_from_apify.py`, `find_company_domains.py`, `verify_dms.py`. **The skeleton table above largely does not apply to it** — it has no `classify_companies.py`, `dedupe_by_company.py`, `ai_filter_jobs.py`, `find_dm.py`, or `enrich_emails.py` of its own, and there is no Phase 3: emails come out of the Apollo waterfall. Its real sequence is 1 → 1.5 → 1.9 → 2 → 2.5 → 2.9 → 2.9b → 4 → 5, documented in its `SKILL.md`. Specifically: Phase 1.5 `process_city_scrape.py` scripts the old manual filter steps (35-day window FIRST, then the 500-employee cap, then dedupe, then conservative classify); Phase 2 DM discovery runs through the **`apollo-dm-waterfall` skill**, not `find_dm.py`; Phases 4/5 are `generate_healthcare_demand.py` (copy templates live at the top of the script and are swapped per A/B test — never treat current copy as permanent) and `push_healthcare_demand.py`.

**Per-client copy scripts are cloned, never parameterized.** When a second client needs different copy on the same pipeline, the generate/push pair is duplicated under a new name rather than branched with a flag — `generate_healthcare_demand.py`/`push_healthcare_demand.py` feed the LIVE Indiana campaign and must not be edited for another client; `generate_texas_demand.py`/`push_texas_demand.py` are the Texas clone (different copy, 5 slots, no persona/age-band routing, own tab); `generate_florida_demand.py`/`push_florida_demand.py` are the Florida clone (same client as Texas, copy approved 2026-08-01, 4 slots, tab `Leads`). The same convention produced `push_campaign_uk.py` and `generate_emails_uk.py` in `.claude/scripts/`. Clone; do not retrofit. The clones drift structurally too — on the Texas sheet AD is "Keep Reason" (a DM-title adjudication pass) and the generation audit trail starts at AE; on the Florida sheet AD holds the waterfall's `dm_status` (different vocabulary from AB elsewhere) and is never written by the generator, with the audit trail at AE-AJ. Florida's generator also **skips** rows where `employer_type` would fall back to the generic "healthcare employers" instead of sending a bland line — read the docstring at the top of each clone before touching it; the copy rules (single vs double newline spacing, historical-only claims, no bench claim) are deliberate and documented there. The Aug 2026 **candidate-angle campaigns** added three more clones — `generate_texas_candidate.py`/`push_texas_candidate.py` (sheet "Healthcare Texas Indeed Leads - Aug 2026", straight to "I know a recruiter with {role_plural} looking", no proof story in the opener) and `push_nynj_candidate.py` (state-neutral proof story, Eastern schedule) — recorded in the SKILL.md (commit `848b133`). That lane flips two conventions: recipient nickname casualization is ON, and mailbox tags are resolved at runtime (see the sending-accounts rule).

**TheirStack pipelines (`scrape-hr-leads`, `scrape-tech-leads`)** use a simpler 5-phase structure: `scrape_leads.py` → `find_dm.py` → `enrich_leads.py` → `generate_emails.py` → `push_campaign.py`.

## Running Scripts

All scripts use `python3 -W ignore`. Pass `--sheet_url` as the first argument. Each skill's `SKILL.md` has the exact command with all flags.

```bash
# Example — Indeed pipeline phase 1
python3 -W ignore .claude/skills/hr-leads-indeed/scripts/scrape_and_pull.py \
  --sheet_url "SHEET_URL" --limit 100 --days 14

# Example — TheirStack pipeline
python3 -W ignore .claude/skills/scrape-hr-leads/scripts/scrape_leads.py \
  --sheet_url "SHEET_URL" --limit 100
```

Scripts are run from the repo root. The `.env` is loaded relative to script location.

## Verification — There Is No Test Suite

No `requirements.txt`, `pyproject.toml`, test files, linter config, or CI exist. Dependencies are whatever is installed in the ambient `python3`. **Do not invent build/lint/test commands** — verification happens by running scripts against real sheets with their safety flags:

| Flag | Meaning |
|------|---------|
| `--dry_run` | Print what would change; write nothing. Default posture for any destructive or costly step. |
| `--preview N` | Render N generated outputs (email bodies, classifications) to stdout for human approval. Required before Phase 4. |
| `--limit N` | Cap rows processed — always use a small N on the first run of a repurposed script. |
| `--apply` | Actually commit deletions/overwrites. **Never pass without asking Jude first.** |
| `--tab` | Target sheet tab (multi-tab skills: `healthcare-staffing-enrichment`, some utilities). |

Because every script is idempotent and skips already-processed rows (see Batch-of-10 below), the safe verification loop is: `--dry_run` → `--limit 5` real run → inspect the sheet → full run.

Two traps in that loop: `--limit N` counts **pending** rows, not sheet rows, so on a partially processed sheet it lands wherever the next N unfilled rows are; and idempotence keys off a specific output cell being non-empty, so re-running after a partial failure resumes rather than repeats — to actually regenerate a row you must clear its output cell first.

## The Claude-in-session Judge Pattern (Aug 2026)

A growing class of scripts makes **no LLM API call at all**. Where an earlier script would have called GPT-4.1 or Claude per row, the judgment is split into three steps and the model is *this Claude Code session*:

1. **COLLECT** — a script gathers raw evidence per row (search candidates, profile text, site pages) and writes one JSON file. Purely mechanical: HTTP/Apify/SQLite, plus mechanical prefilters (junk hosts, eligibility gates).
2. **JUDGE** — Claude reads that file in-session and hand-writes a verdicts JSON.
3. **APPLY** — a second script validates and writes the verdicts to the sheet or store.

**The apply step never trusts the verdicts file.** A verdict for a row/id absent from the candidates file is refused outright (hallucination guard), values outside the allowed vocabulary are refused, and any mechanical gate the original LLM-output path used is re-run over the hand-written lines — the guard exists against Claude's own mistakes, not only a model's.

Current instances: `exa-website-enrichment/enrich_websites_exa.py` + `enrich_websites_apify.py` (domain resolution), `production-directory-leads/collect_uncertain_for_claude.py` → `apply_claude_classifications.py` (classification), `production-directory-leads/collect_icebreaker_research.py` → `apply_icebreakers.py` (icebreaker copy), `healthcare-staffing-enrichment/classify_healthcare_icp.py` → `apply_icp_research.py` (ICP classification — see that skill's section for the two-axis rule), and the ambiguous-title middle in `healthcare-staffing-enrichment/find_dm_large_firms.py`. Jude's calls, 2026-08-10 through 08-14 — extend this pattern rather than adding a per-row LLM call when a new judgment step is needed on these lanes (he explicitly distrusts GPT-4.1 for these classification calls).

**Durable judge log (2026-08-13):** when a judged pass spans multiple sessions or worker batches, every verdict is appended to a `data/*.jsonl` log the moment it's produced (`icp_research_log.jsonl`, `pm_dm_cache.jsonl`), and the apply step replays the log (dedupe by row, last write wins). Scratchpad JSON alone is not durable — a judged row must never be re-payable.

## Critical Rules

**Batch-of-10:** Every script that enriches rows in place MUST write to the Google Sheet after each batch of 10 rows — never batch all then write. This enables crash recovery and idempotent reruns (scripts skip already-processed rows). Two classes are the exception and use a large `WRITE_BATCH`: a **pure splitter** that only copies rows to new tabs (`healthcare-staffing-enrichment/split_by_headcount.py`, `WRITE_BATCH=2000`), and a **durable-log-replay apply step** whose crash recovery lives in a `data/*.jsonl` log rather than in partial sheet writes (`healthcare-staffing-enrichment/apply_icp_research.py`, `WRITE_BATCH=400` — see the durable judge log note above). Any new per-row enrichment/spend script still follows batch-of-10.

**Geography is Jude's call:** before scraping any new vertical/client, ask which state(s) to target. Scrape city-grids (metros + regional hubs), not state-level queries — city grids return ~2x the postings.

**35-day window + 500-employee cap (healthcare-demand-pipeline):** postings older than 35 days never land on a campaign sheet; the window applies BEFORE dedupe and enrichment. Dedupe winner = oldest posting inside the window. Cut from 60 to 35 on 2026-07-31 on measured reply data (≤30 days old at first contact → 2.35%; 31-60 → 0.88%; >60 → 0.00% from 46 sends). 35 rather than 30 gives the ~1-week sequence headroom. **Do not narrow it to a 25-35 band** — the fresh end carries the result (0-25 days replies at 2.42%, a 25-35 band alone at 1.23%).

**Live campaigns are frozen:** never modify an active Instantly campaign (sequence, leads, copy) or the enriched sheet rows feeding it without Jude's explicit instruction.

**Instantly custom variables:** send as `custom_variables` on POST/PATCH /leads (merges into stored payload). Nesting under `payload` gets silently replaced; loose top-level keys are dropped. Verify persistence with a fresh GET, not the write response.

**Apollo (Basic plan):** People Search (`mixed_people/api_search`, x-api-key header) is the only free endpoint — names come back obfuscated (`Wo***e` = 2 prefix letters + 3 literal asterisks + last letter), no emails/LinkedIn/locations. Old `mixed_people/search` path 403s. Apollo = DM identification only; AMF = all emails (only charges on found verified emails). The `apollo-dm-waterfall` skill implements the full flow.

**Email template approval gate:** Phase 4 (`generate_emails.py`) MUST NOT run until the user has seen and approved the template. Show `--preview N` output first, wait for explicit approval, then run for real.

**Phase 4 and Phase 5 are manual stops** — never auto-chain into them.

**Valid emails only:** AnyMail Finder `risky` results are rejected everywhere — only `email_status == "valid"` emails are written to sheets or pushed to Instantly, and DM name/title/LinkedIn are never written without a valid email (no partial data).

**Sending accounts — attach BY TAG, never individually (revised 2026-08-20):** Jude previously configured mailboxes by hand and the rule was "never attach". He has since reversed that: campaigns should be created WITH `email_tag_list` plus `match_lead_esp: true` and `provider_routing_rules`, copied from whatever he last set. Individual addresses are still never attached — tags only. **The tag set drifts**, so read it off his most recent campaign rather than copying an older script: as of 2026-08-20 `hirebase-healthcare-leads/scripts/push_campaign.py` carries FIVE tags (Zapmail re-added the same day it was dropped) versus the three hardcoded in `push_florida_demand.py`. Instantly exposes no tags endpoint — confirm a tag ID via `GET /campaigns` → `email_tag_list` on campaigns that already use it. `provider_routing_rules` is an ORDERED precedence list and his current order puts the `all -> google` catch-all FIRST, unlike the Florida ordering; reproduce it verbatim. Newest pushes handle the drift two ways: `push_nynj_candidate.py` resolves the tag set AT RUNTIME from Jude's most recent tag-carrying campaign (`GET /campaigns`), while others bake it in after reading his latest campaign (`push_aiark_campaign.py` read off Pipeline Intro Sep 2026). Prefer the runtime-read pattern for new push scripts; either way the source is always his latest campaign, never an older script.

**Casualization is embedded in every GTM generator:** first names (common nicknames only: William→Will), company names (strip legal suffixes/generic tails), cities (local nicknames: Indianapolis→Indy). Canonical rules live in the `casualize-names` skill; each pipeline applies them at generation time (LLM prompt rules or the shared `NICKNAMES` map). Any NEW outreach generator must include them — **except recipient first names, which genuinely flip per lane**: dropped on `production-directory-leads` (2026-08-12), `hirebase-healthcare-leads` (2026-08-19), and `trades-staffing-leads` (2026-09-11), but explicitly turned ON for the healthcare candidate-angle clones (Jude, 2026-08-26: "use the casualize skill"). Ask per new lane; never assume either direction.

**No sign-off in generated copy** (standing rule, 2026-08-05): never append "Best, Jude" or any sign-off to a generated body or a sequence step. The sending account's signature carries identity. The `production-directory-leads` lane also drops **recipient** nickname casualization for the same reason a sign-off is dropped — a stranger's cold email renaming someone reads badly (Jude, 2026-08-12); `hirebase-healthcare-leads` drops it too (Jude, 2026-08-19), so this is now the pattern on new lanes rather than a one-off. Letter case is still normalized on those lanes (AMF returns "SARAH"), because fixing shouting is formatting, not renaming. Company-name casualization is unaffected everywhere.

**Text-only campaigns:** New Instantly campaigns set `text_only: true` and `first_email_text_only: true` — those flags are what make the send plain text. Personalization values written to leads are plain text with newlines and no markup. Sequence step bodies still carry the thin `<div>{{personalization}}</div>` / `<br />` wrapper Instantly stores them in; that is expected and is not the HTML the rule is about.

**Never delete rows without asking** — always confirm before calling `--apply` on destructive steps.

## DM Targeting Rules

Rules are pipeline-specific; see each SKILL.md. Summary:

| Pipeline | DM Target Logic |
|----------|----------------|
| `hr-leads-indeed` / `hr-linkedin-leads` | Size-based: CEO (<50), HR Manager (50-200), VP HR (200-500). Senior role (CHRO/VP HR) → always CEO |
| `tech-leads-indeed` | 3-pass: CTO/VP Eng → CEO/Founder → Head of People (safety net) |
| `civil-engineering-leads-indeed` | 2-pass: Owner/MD/CEO (<50) or COO/Ops Director (50-200) → fallback |
| `healthcare-demand-pipeline` | **Fixed ladder: CEO → COO → Medical Director → nobody** (Jude, 2026-07-31). Lists capped at 500 employees. Only rung 1 is evidence-backed; 2 and 3 are coverage fallbacks. Banned: HR at any level, every other clinical title, site/regional ops, practice managers. No one findable → leave the row un-enriched |
| `healthcare-linkedin-leads` | **Routed by geographic spread, not by size** (`assign_target_role.py` → col Z, consumed by `find_dm_openings.py`): openings in ONE metro → CEO/Owner; openings across MULTIPLE metros → COO. The old Practice/Office Manager ladder is dead — do not reconstruct it |

### Measured DM evidence (healthcare demand, 4 campaigns, 1,209 leads, Jul 2026)

The owner-first rule is not a heuristic — it is the only DM finding in this repo backed by campaign data. Pooled across Indiana, Texas and the two June 2nd campaigns:

| Title targeted | Emailed | Replies | Rate |
|---|---|---|---|
| Owner / CEO / Founder | 608 | 17 | **2.80%** |
| Clinical leader (all levels) | 307 | 4 | 1.30% |
| COO / Ops / Administrator | 87 | 0 | 0.00% |
| HR / Talent (incl. CHRO) | 32 | 0 | 0.00% |
| Other / unclear | 173 | 0 | 0.00% |

The DM rule that came out of this (Jude, 2026-07-31) is a fixed ladder: **CEO → COO → Medical Director → nobody.** Only the first rung is a finding. Rungs 2 and 3 are coverage fallbacks Jude chose for when no CEO exists, and both are unsupported by the data — COO/Ops is 0 for 87 and clinical leadership produced zero interested replies from 307. The Medical Director rung is restricted to a genuine company-level one, since at small clinics that title is usually a contracted outside physician. Clinical leadership is otherwise banned entirely: chief-level went 0 for 126 (CMO 0/78, CNO and Director of Nursing 0/29, Chief Clinical Officer 0/19), and the old discipline-matching ladder (CMO→physician roles, CNO→nursing) is deleted — do not reconstruct it. A company with nobody on the ladder is left un-enriched, because a wasted AMF credit plus a burned company beats an empty row.

**Company size is the other half of the story** (Indiana + Texas, size taken as the lower bound of the sheet's Company Size range):

| Size band | Emailed | Replies | Rate | Interested |
|---|---|---|---|---|
| TINY <50 | 142 | 5 | **3.52%** | 1 |
| size unknown | 219 | 6 | 2.74% | 1 |
| MID 50-499 | 249 | 3 | 1.20% | 1 |
| **LARGE 500+** | **251** | **0** | **0.00%** | **0** |

**251 companies at 500+ employees produced zero replies of any kind** — not one interested, not one rejection, nothing (2.30% for everything under 500; Fisher p = 0.008). That band is 29% of the list.

⚠️ **Size and title were confounded in that measurement** — under the old clinical-first rule the 500+ rows were targeted 175 clinical / 22 HR / 41 other and only **2** owner-ish contacts (a Managing Partner and a Managing Director, no actual CEO), while under-500 rows got 194 owner vs 89 clinical. So a large-org CEO was never actually tried, and strictly what the zero disproves is clinical-and-HR-at-big-orgs. **Jude capped at 500 anyway on 2026-07-31**, accepting that trade rather than spending another campaign to separate the two.

The 500+ band was also where list quality was worst: individual nursing homes inherit their parent chain's headcount (nine separate Life Care Center facilities all tagged 40,000), and non-healthcare corporates and government bodies survive classification there (Wolters Kluwer, Hearst, SpartanNash, SGS, US Dept of Veterans Affairs). The cap removes those chain facilities too — an accepted cost, surfaced in Phase 1.5's output rather than dropped silently.

**HQ vs regional at multi-site chains is an open question.** Company-level titles replied at 2.03% (10/492), region-scoped at 3.70% (1/27, and that one reply was "Stop."), site/facility-scoped at 0.00% (0/40). Region + site pooled is 1/67 against 10/492 company-level, Fisher p = 0.61 — no signal either way. Nobody has enough data; it needs a deliberate split test, not a guess.

## LLM Provider

| Pipeline | Classification / Filtering | Email Generation |
|----------|---------------------------|-----------------|
| `tech-leads-indeed` | Azure OpenAI GPT-4.1 (`AZURE_OPENAI_DEPLOYMENT_FAST`) | Azure OpenAI GPT-5.1 (`AZURE_OPENAI_DEPLOYMENT`) |
| `civil-engineering-leads-indeed` | Claude Haiku | Claude Opus 4.5 |
| All others | Claude Haiku (via `ANTHROPIC_API_KEY`) | Claude (model per skill) |

Azure OpenAI env vars: `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_API_VERSION` (default `2024-10-21`), `AZURE_OPENAI_DEPLOYMENT`, `AZURE_OPENAI_DEPLOYMENT_FAST`.

⚠️ As of Sep 2026 the `ANTHROPIC_API_KEY` in `.claude/.env` was returning 401 (noted in `generate_aiark_icebreakers.py`), so the "Claude via API" rows above may not actually work — Azure OpenAI is the working provider (mapping: Haiku→GPT-4.1 for classification, Opus/Sonnet→GPT-5.1 for generation). Verify the key before pointing any new lane at the Anthropic API. Jude distrusts GPT-4.1 for *classification* judgment calls (use the Claude-in-session judge pattern there), but GPT-5.1 for *generation* is sanctioned.

## Environment & Auth

- API keys: `.claude/.env` (loaded via `dotenv` relative to script location)
- Core env vars: `APIFY_API_TOKEN`, `ANYMAILFINDER_API_KEY`, `INSTANTLY_API_KEY`, `ANTHROPIC_API_KEY`
- TheirStack pipelines also need: `THEIRSTACK_API_KEY`
- Apollo waterfall scripts also need: `APOLLO_API_KEY`
- `exa-website-enrichment` scripts also need: `EXA_API_KEY` (`enrich_websites_apify.py` needs `APIFY_API_TOKEN` instead)
- `production-directory-leads` needs `APIFY_API_TOKEN` (scrape + LinkedIn), `PURPLE_MAGIC_KEY`, `ANYMAILFINDER_API_KEY`, `INSTANTLY_API_KEY`, the Azure OpenAI vars (classification + the Apollo fallback's ranking) and `APOLLO_API_KEY`
- `healthcare-staffing-enrichment`'s PM lanes (`find_ceo_pm_demand.py`, `find_dm_large_firms.py`, `find_dm_apollo_pm.py`) need `PURPLE_MAGIC_KEY`; the Apollo third lane also needs `APOLLO_API_KEY` + `APIFY_API_TOKEN`
- `trades-staffing-leads` needs `PURPLE_MAGIC_KEY` (both judge lanes' /find), `APOLLO_API_KEY` + `APIFY_API_TOKEN` (Apollo lane + Google de-obfuscation), `ANYMAILFINDER_API_KEY`, `INSTANTLY_API_KEY`
- `equipment-finance-leads` needs `APIFY_API_TOKEN` (Maps/Sales Nav/LinkedIn/Google actors), the Azure OpenAI vars (dealer classification + domain picks), `ANYMAILFINDER_API_KEY`, `INSTANTLY_API_KEY`
- `nppes-new-clinics` phases 1-3 need no API key (CMS data is free), only the Google OAuth token for `export_leads.py --to_sheet`. Its campaign track (below) needs `ANYMAILFINDER_API_KEY`, `PURPLE_MAGIC_KEY` (Purple Magic / ConnectorOS — `find_dm_waterfall.py`, `pm_rescue.py`), the Azure OpenAI vars, and `APIFY_API_TOKEN`
- Google Sheets OAuth: `.claude/token.json` (setup via `.claude/setup_google_auth.py`)

Gitignored and therefore absent on a fresh clone: `.claude/.env`, `.claude/token.json` (+ `.claude/token_*.json`), `.claude/scripts/` (see below), `.claude/cache/` and `.claude/backups/` (local working state — e.g. `cache/linkedin_companies.json`, `backups/contacted_emails_*.json`), `.claude/scheduled_tasks.lock`, `.playwright-mcp/`, and every skill `data/` directory that holds a SQLite store or working JSON — `nppes-new-clinics/data/`, `healthcare-staffing-enrichment/data/`, `production-house-leads/data/`, `production-directory-leads/data/`, `production-demand-leads/data/`, `hirebase-healthcare-leads/data/`, `energy-emaas-leads/data/`, `equipment-finance-leads/data/`, `trades-staffing-leads/data/`. A SQLite-backed skill therefore looks empty on a clone; the store rebuilds by re-running phase 1. `.claude/campaign_archive` is a symlink to a private archive directory outside the repo — see Shared Utility Scripts.

## API Quirks

- **Apify LinkedIn scraper (employees):** Sync endpoint returns HTTP **201**. Actor: `harvestapi~linkedin-company-employees`. Title is in `currentPositions[0]["title"]`.
- **Apify LinkedIn jobs scraper:** `insight_api_labs~linkedin-jobs-scraper` — HTTP 201; no company size in response; `fewApplicants=True` for low-applicant pain filter.
- **Apify LinkedIn profile scraper:** `dev_fusion/Linkedin-Profile-Scraper` — used in Phase 2.5 DM verification.
- **Apify LinkedIn company profile:** `pratikdani~linkedin-company-profile-scraper` — fills website/size/description for LinkedIn-sourced pipelines.
- **AnyMail Finder:** Auth header is `Authorization: {API_KEY}` (no "Bearer"). Two endpoints: `/find-email/person` and `/find-email/decision-maker`. Valid categories: `ceo, engineering, finance, hr, it, logistics, marketing, operations, buyer, sales` (`coo` is NOT valid — use `operations`).
- **Instantly API v2:** Bearer token auth. Leads added one at a time (no bulk endpoint). `DELETE /leads/{id}` is safe; `DELETE /leads` with a body wipes the **entire campaign**.
- **Purple Magic (ConnectorOS):** second-lane email provider — base `https://api.connector-os.com/api/email/v2`, Bearer auth (`PURPLE_MAGIC_KEY`). `/find` takes `{firstName,lastName,domain}`; `/decision-makers` takes `{domain}` and returns nobody ~83% of the time on small practices. Different providers fail on different companies, which is why it's run over AMF `not_found` piles rather than instead of AMF.
- **Google Search actor:** Used by `classify_companies.py`, `find_company_domains.py`, `find_dm.py` — runs via Apify, not direct Google API.

## DM Verification (Phase 2.5)

`verify_dms.py` scrapes each LinkedIn URL via Apify and compares `companyName`/`companyWebsite` against the sheet's target. Match priority:
1. Domain root match
2. Squished-name match (strips punctuation/legal suffixes, concatenates)
3. Token overlap guard (>50% of target's tokens must appear in scraped name)

Mismatches clear DM Name / DM Title / LinkedIn URL columns — leaving the company domain intact so Phase 3 can fall back to `/decision-maker`.

## Google Sheet Column Schemas

All Indeed/LinkedIn pipelines use a **29-column base schema** with pipeline-specific differences in the final columns. Canonical layout:

```
A:Job_Id     B:Job Title    C:Job Type       D:Occupations     E:Date Published
F:Salary Min G:Salary Max   H:Salary Period  I:Apply URL       J:Job Description
K:Company Name  L:Company Website  M:Company Size  N:Revenue  O:CEO Name
P:Company Description  Q:Benefits  R:City  S:State
T:DM Name  U:DM Title  V:LinkedIn URL  W:Email
X:First Name  Y:Last Name  Z:Email Body  AA:Added to Instantly
AB:pipeline-specific  AC:pipeline-specific
```

**healthcare-demand-pipeline** extends the base schema with AB:dm_status, AC:Indeed URL (`https://www.indeed.com/viewjob?jk={Job_Id}` — **AC always holds the Indeed URL, on every sheet; Jude's rule**), AD-AL generation audit trail (persona, age_band, cleaned_role, role_plural, team_word, employer_type, month, casual_company, review_status), AM:hq_state. **tech-leads-indeed** and **civil-engineering-leads-indeed** also have minor column differences — each SKILL.md has the verified layout.

**`tech-leads-indeed`** adds `AB:template_variant` and `AC:cleaned_role` (populated by `generate_emails.py`).

**`production-directory-leads`** extends the base schema with AB:Apollo/AMF waterfall status, **AC:PM Status** (the Purple Magic lane's own column — the two DM lanes must not share a status cell), AD:DM LinkedIn JSON, AE:site-pages JSON, AF:icebreaker, AG:fact_type, AH:Email 1 body. Note AH, not Z, is the body column on this lane.

**`healthcare-linkedin-leads` is off this schema entirely** — see its section below.

⚠️ Always check `COL_*` constants at the top of a script before running it against a sheet — repurposed scripts with shifted columns have corrupted data before.

## Shared Utility Scripts

`.claude/scripts/` contains one-off and cross-pipeline utilities (not part of any skill's standard pipeline). **This directory is gitignored** — it exists only on Jude's machine and will be absent on a fresh clone, so never assume these scripts are present; check before referencing one. Current local contents:
- `ingest_apify.py` — generic Apify dataset ingestion
- `research_dm.py` — standalone DM research
- `archive_campaign.py` — archives a completed Instantly campaign's config, analytics, leads, and reply emails into `.claude/campaign_archive/<campaign-id>/` (`campaign.json`, `analytics.json`, `leads.jsonl`, `emails.jsonl`). That path is a **symlink to a separate local directory outside this repo** (`~/nexam-campaign-archive`) — it contains lead PII and must never be committed here or made public
- `generate_emails_uk.py` / `generate_emails_v2.py` — legacy/experimental email generators
- `verify_emails.py` / `verify_emails_uk.py` — email verification passes
- `salesnav_enrich.py` / `salesnav_about_enrich.py` — Sales Navigator enrichment
- `consolidate_tabs.py`, `filter_icp.py`, `reenrich_invalids.py` — sheet maintenance utilities
- `push_campaign_uk.py`, `wipe_campaign_uk.py` — UK campaign variants (use with caution — `wipe_campaign_uk.py` is destructive)
- `scrape_linkedin_profiles.py` — standalone LinkedIn profile batch scrape
- `_inspect_*.py`, `_stats.py`, `_reorder_columns.py`, `_remove_unclassified.py` — diagnostics (prefixed `_` = dev tools, not pipeline steps)

## SBA Campaigns

`sba-campaigns` has no `SKILL.md`. It is a two-track outreach system using SBA loan data:

- **Borrower track:** `find_borrower_websites.py` → `find_borrower_dms.py` → `enrich_borrower_emails.py` → `render_bodies.py` → `push_campaigns.py`
- **Lender track:** `find_lender_dms.py` → `patch_lender_leads.py` → `push_campaigns.py`

Lead lists come from `borrowers.txt` / `lenders.txt` in the skill directory. No Apify scrape step.

## verify-leads Quirks

`verify-leads` takes **0-based column indices** as CLI args (A=0, B=1, …) instead of letter names. The scripts are schema-agnostic — you pass `--col_name`, `--col_website`, `--col_dm_name`, etc. for each run. Always check the exact flags against `verify-leads/SKILL.md` before running.

## healthcare-linkedin-leads

Self-contained pipeline (16 scripts) whose **`SKILL.md` documents only phases 1 and 1.85** and wrongly describes the rest as a handoff to `hr-leads-indeed` — docstrings are the reference for everything after ingest.

It works off an **external LinkedIn-jobs export with a 20-col A-T source schema** (A:Title, B:Job Desc, C:Primary Description, H:Location, M:Company Name, O:createdAt, T:aboutLink), not the 29-col base. Enrichment appends from U onward: **U:Website, X:Employer Type / Openings, Y:Openings Detail, Z:Target Role, AA:DM Name, AB:DM Title, AC:DM LinkedIn, AD:Email, AE:Icebreaker, AF:Clean Company, AG:Email Body.** Note the collisions with the base schema — M is Company Name here (K elsewhere) and AD is the email (W elsewhere).

**Two-tab, two-campaign structure.** `build_opening_tabs.py` splits ICP rows into a **Multiple Openings** tab (companies with >1 posting, one row per opening) and a **Single Opening** tab, and everything downstream runs per tab with its own copy and its own campaign — the same clone-don't-parameterize convention used elsewhere.

| Step | Script | Purpose |
|------|--------|---------|
| 1 | `scrape_and_pull.py` | LinkedIn low-applicant scrape → sheet |
| 1.85 | `enrich_company_profiles.py` / `enrich_company_websites.py` | Website, size, description from the LinkedIn company profile |
| 1.9 | `classify_agencies.py` → `classify_employer_type.py` | Drop agencies/job boards, then keep only `independent_practice` (col X) — platforms, systems and chains are cut here |
| 2.0 | `build_opening_tabs.py` | Split into the Multiple / Single tabs |
| 2.1 | `assign_target_role.py` | Metro-spread routing → col Z (CEO/Owner vs COO) |
| 2.2 | `find_dm_openings.py` | DM per col Z's target |
| 3 | `backfill_emails.py` | AMF person endpoint for rows with a DM name but no email |
| 3.5 | `clean_company_names.py` | LLM company-name cleanup → AF (one call per unique name) |
| 4 | `generate_icebreakers.py` → `generate_multi_emails.py` / `generate_single_emails.py` | Per-tab copy |
| 5 | `push_multi_campaign.py` / `push_single_campaign.py` | One Instantly campaign per tab |

## healthcare-staffing-enrichment

Standalone enrichment **and outreach** skill for healthcare staffing agency sheets (the supply side of the healthcare pipeline). No SKILL.md — script docstrings are the reference. Uses Azure OpenAI GPT-4.1 throughout (classification, website picking, about-page summaries).

**Enrichment order:** `classify_agencies.py` (Google-via-Apify + GPT-4.1; `--apply` deletes non-agencies) → `find_websites.py` / `find_missing_websites.py` → `verify_websites.py` / `reverify_websites.py` (label website correct/not_correct; reverify clears DM columns when the stored website turns out wrong) → `scrape_company_about.py` → `enrich_linkedin_company.py` / `enrich_company_profiles.py` → `find_ceo.py` (AMF only, no Google fallback; rejects hosting-platform domains like Squarespace/Wix and emails whose domain doesn't match the company).

**Outreach tail:** `generate_icebreaker.py` → `generate_email_body.py` (fixed body template + icebreaker) → `push_campaign.py` (creates the Instantly campaign as **DRAFT**; Jude activates manually). These take `--tab` — the sheet is multi-tab.

**Demand-campaign track** (healthcare recruitment firms as the leads, sourced from an AI Ark export with an A-N schema): `split_by_headcount.py` (splits source into `1-50 EMP` / `50-200 EMP` tabs by col B) → `find_ceo_demand.py` (AMF /decision-maker with domain + company name; appends O:dm_name P:dm_title Q:dm_email R:dm_linkedin S:email_status) → `split_dm_names.py` (GPT-4.1 name split → T/U) → `generate_demand_body.py` (fixed plain-text template, `{first_name}` only → V) → `push_demand_campaign.py` (DRAFT campaign, text-only, daily_limit 500; refuses rows whose email domain doesn't match the website domain; skips duplicate emails both within the push and against rows already pushed anywhere in the tab; leads rejected by Instantly's workspace blocklist get col W = `BLOCKLISTED` and are never retried — resume skips both `TRUE` and `BLOCKLISTED`) → `patch_greeting.py` (one-off post-push copy patcher — updates sheet col V, the Instantly sequence, AND each pushed lead's personalization).

**Expansion-campaign track (Aug 2026 — cloned from the demand track, never parameterized):** a second angle over the same AI Ark firms, framing newly-opened clinics staffing up new locations. Two additions to the pattern above. (1) A **Purple Magic email lane**: `find_ceo_pm_demand.py` is the PM-primary twin of `find_ceo_demand.py` (AMF lane) — `/decision-makers {domain}` → positive owner-like title gate BEFORE the `/find` call → valid + domain-matched email only, appending the same O-S columns but stamping `pm_*` statuses so the AMF and PM lanes stay distinguishable. (2) Cloned copy scripts: `generate_expansion_body.py` (Jude's verbatim expansion template, `{first}`/`{company}` only → col V) and `push_expansion_campaign.py` (DRAFT clone of `push_demand_campaign.py` mirroring the May 25th Supply campaign — subject `Awesome work at {{companyName}}`, "Hi" greetings, "Sent from my iPhone" footer, delays 2/2/5). **Do not edit `generate_demand_body.py`/`push_demand_campaign.py`** — they belong to the completed 1-50 demand campaign.

**ICP classification pass (Aug 2026 — runs BEFORE any DM/email spend on the AI Ark tabs):** the Expansion campaign's replies proved keyword filtering can't work here (97-98% of the list mentions healthcare somewhere; a higher-ed nursing recruiter and a nanny agency both passed). Claude-in-session judge flow: `classify_healthcare_icp.py` collects each row's existing sheet text (no scraping, no spend) → Claude judges → `apply_icp_research.py` writes. **Two-axis rule (2026-08-13, supersedes the old 4-way `icp_class`-only taxonomy):** `staffing_firm` (does it recruit/place for clients?) × `serves_healthcare` (are clients healthcare *provider* orgs — any role type placed INTO a provider counts; manufacturer/sponsor-facing work like pharma/device/CRO does NOT). KEEP = both true. Columns: Y:`icp_class` (kept for continuity, plus `MFG_SPONSOR_FACING`), Z:`outreach_flag` (`KEEP` / `SKIP_NOT_STAFFING` / `SKIP_NOT_HEALTHCARE`, written by `flag_outreach_targets.py` or `apply_icp_research.py`), AA:`research_notes`. Verdicts are appended to `data/icp_research_log.jsonl` the moment they're produced (durable judge log, above) and the apply step replays the log — nothing is ever deleted, failing rows are tagged. Downstream DM scripts (`find_ceo_demand.py` etc.) now require Z=KEEP by default (`--require_keep`, override with `--ignore_keep_flag`).

**Large-firm DM track (200+ employees, Aug 2026 — docstrings are the reference):** `find_ceo_pm_demand.py`'s owner-only gate stays correct under 50 employees and must not be repointed; at 200+ the buyer changes, so `find_dm_large_firms.py` is the band-aware clone with a different ladder: owner → new-business leadership → healthcare desk/division owner → ops exec. **The ban list matters more than the ladder** — at a staffing firm the payroll IS clinicians and line recruiters (most common Purple Magic titles on these domains: CNA, recruiter, RN), so clinical/support titles are rejected before any rung is tested, and every rung is anchored on a leadership token. Bare "Director"/"VP"/"Manager" titles go to the Claude-in-session judge, not a looser regex. Third lane: `find_dm_apollo_pm.py` (2026-08-14) — Apollo free search → Google de-obfuscation → **Purple Magic `/find`** (not AMF), over rows both PM's own index and AMF missed; it imports `find_dm_large_firms.py`'s ban+ladder via importlib (one source of truth, no LLM ranking by Jude's explicit call). `find_ceo_demand.py` grew matching flags: `--category` (second-category AMF passes; a hit upgrades, a miss never downgrades), `--sizes` (col C band filter), `--retry_pm` (now covers `lf_*` rows too) / `--retry_not_found`. `find_dm_large_firms.py` grew `--only_status` (comma-separated exact statuses, e.g. `lf_error:http_500`) for precise retries — prefer it over the blanket `--retry_rejected`, whose `lf_not_found` retries re-spend a paid `/find` call for nothing unless the underlying PM data changed.

**Pipeline Intro campaign (Sep 2026):** `push_pipeline_intro_campaign.py` — clone of `push_expansion_campaign.py` (do not edit the expansion/demand parents; their campaigns are complete). New connector offer approved 2026-09-08: employers already in the pipeline, Indiana social proof, 2 free intros; body is STATIC (`{first}` only, so there is no generate step), value-forward subject, 3 follow-ups (2/2/5). Pushes valid + KEEP + never-contacted leads with a **live Instantly-freshness gate** — col W's already-pushed markers had gone stale against the workspace, so it re-checks via the API rather than trusting the sheet. First push in this skill to attach mailboxes BY TAG at create time; later lanes (energy AI Ark, trades) copied their tag config from it.

**SIA one-offs:** `enrich_sia_emails.py` (AMF person endpoint), `enrich_sia_company_dms.py` (AMF /decision-maker for company-only rows), `rescue_sia_dms.py` (Google-search DM discovery then AMF person) — hardwired to the SIA Attendees sheet's own A-L schema.

**Quirks:**
- Every script defaults to a hardcoded `SHEET_ID`; `verify_websites.py`, `reverify_websites.py`, and `enrich_company_profiles.py` take no `--sheet_url` at all — they only run against that sheet.
- **Three conflicting column schemas coexist in this skill.** Older scripts (`reverify_websites.py`) expect DM name in col F; the supply-side outreach tail (`find_ceo.py` onward) uses N:dm_name, P:dm_email, Q:dm_linkedin, R:email_status; the demand track uses O:dm_name, Q:dm_email, S:email_status on top of the A-N AI Ark layout, with the ICP pass adding Y/Z/AA. Always check the `COL_*` constants at the top of a script before running it.
- DM lane statuses in col S are prefix-distinguished so lanes skip each other's rows: plain AMF statuses, `pm_*` (`find_ceo_pm_demand.py`), `lf_*` (`find_dm_large_firms.py`).
- Status filters differ per track — some downstream scripts filter on `email_status == "found"`, not `"valid"`. Match the docstring of the script you're running.

## recruitment-email-gen

Niche-agnostic supply-side outreach skill (ICP = recruitment agencies; first used for the AU campaign). No SKILL.md — script docstrings are the reference. Unlike `verify-leads`, column flags take **letter names** (`--col_website J`, `--col_dm_name AA`), and every column is configurable per run, so it works against any sheet schema.

**Order:** `find_dm_amf.py` (AMF /decision-maker straight from domain — no Google DM search; valid + domain-match emails only) → `scrape_website.py` (direct HTTP fetch of about/services/sectors pages, GPT-4.1 summary — no Apify cost) → `generate_icebreaker.py` (static icebreaker, only first name injected; also splits first/last name) → `generate_email_body.py` (GPT-5.1 extracts just two facts — ICP + one role — and the email is assembled deterministically in code) → `push_campaign.py` (DRAFT campaign; body rides as `{{personalization}}`; no subject line).

## apollo-dm-waterfall

Niche-agnostic DM discovery + verified email for **~1 AMF credit and 0 Apollo credits** per company — replaces AMF /decision-maker (2 credits, no title/LinkedIn). Its `SKILL.md` has exact commands; column letters are all CLI flags so it runs against any sheet schema. Waterfall per row: free Apollo People Search by domain (LARGE orgs searched with a `person_titles` filter) → GPT-4.1 ranks top 3 candidates by budget authority (`RANK_SYSTEM` in `apollo_dm_waterfall.py` is the only niche-specific part — edit it to retarget) → per candidate: Google de-obfuscation of the name → AMF person endpoint → "{first} {last-initial}" retry → sheet-CEO rescue (TINY/MID orgs only). Writes a status column; never writes DM data without a valid email.

Companion scripts: `amf_ceo_rescue.py` (AMF /decision-maker `ceo` rescue for TINY rows where Apollo had no people; 2 credits per found), `amf_dm_fallback.py` (AMF /decision-maker for `not_found` rows — TINY/MID→ceo, LARGE→hr; **standing rule: always run after waterfall + rescue**), and `apollo_org_enrich.py` (Apollo org enrichment for blank-size rows → real headcount in col M + HQ state in col AM; ~1 Apollo credit per company; needs `APOLLO_API_KEY`).

Two newer companions are **not yet in the SKILL.md** — docstrings are the reference: `amf_person_fill.py` (person-endpoint email fill for rows that already have a DM name, e.g. after the waterfall's identity-only `--skip_email` mode or `find_dm_exa.py` — 1 credit vs 2 for re-resolving a role; rejects emails whose domain doesn't match the company, which caught ~29% wrong-person hits on initial-only finds) and `amf_ceo_then_ops.py` (sequenced /decision-maker pass, Jude 2026-08-01: `ceo` first — a hit upgrades an existing Apollo admin, a miss never downgrades one — then `operations` only for rows still empty; reaches the companies Apollo has nobody for).

## exa-website-enrichment

Domain resolution with a proof-on-page gate. Run it BEFORE any DM/email enrichment when websites are missing or untrusted: a wrong domain doesn't fail loudly, it emails a real person at the wrong company. Never writes a domain it can't prove; blank beats wrong.

⚠️ **The SKILL.md is stale on the core mechanism.** It still describes a mechanical acceptance ladder; that was replaced (2026-08-10, commits `b49b107`/`882ccbb`) by the three-step Claude-in-session judge flow described above — `--apply` collects candidates, Claude judges in-session, `--verdicts FILE --apply` writes. The mechanical parts that survive are the *prefilters* (junk hosts, careers subdomains, parent-chain), not the acceptance decision. Cost/`EXA_API_KEY` details in the SKILL.md are still accurate.

`enrich_websites_apify.py` is a drop-in **alternate collector** (added when Exa credits ran out mid-campaign): same candidates/verdicts shape and the same apply step, but searches with `apify~google-search-scraper` (the actor `find_company_domains.py` already uses) instead of Exa. It deliberately stamps the same `exa_*` statuses — those mean "a resolution attempt happened", not which engine ran — so the two backends skip each other's rows correctly.

The skill also ships `find_dm_exa.py`, which the SKILL.md does **not** cover (its "ends at column L" claim predates it): a last-resort DM **name** finder for companies both Apollo and AMF /decision-maker dead-end on. GPT-4.1 extracts a name+title from Exa results under the CEO→COO→Medical Director ladder; it writes a name only, never an email — complete the row with `amf_person_fill.py`.

Two behaviors added Aug 2026 (ahead of the SKILL.md):
- **Junk-domain classes grew from live failures on NPPES-sourced lists.** NPI-registry mirrors are the worst false positive on a list sourced *from* the NPI registry — the page names the practice, city, and taxonomy, so it passes content verification perfectly while being a directory (12 of 789 resolved domains on the first healthcare run). Senior-care referral directories are the same trap for home-care agencies (CAREGIVERS ON DEMAND resolved to aplaceformom.com, and enrichment then returned that directory's CEO). Secretary-of-State registry mirrors are matched by a regex family (`JUNK_HOST_RE`, rejection class `registry_mirror`), not a fixed list.
- **Prior-attempt rows are skipped by default.** A miss leaves the website cell blank but stamps an `exa_*` status; without the skip those rows sit at the top and get re-searched (re-billed) on every `--limit` run before any fresh row is reached. Pass `--retry_attempted` to deliberately redo them.

## personalized-icebreakers

Despite the name, this is a **complete retarget campaign pipeline**, not just an icebreaker generator — deep per-lead research (LinkedIn + whole-site crawl) → dossier → icebreaker → full body → its own Instantly push. Built July 2026 for the healthcare retarget. `SKILL.md` has exact commands and the full copy-rule list; `reply-playbook.md` holds Jude's reply ladder for the campaign. All column letters are CLI flags, so it runs against any sheet schema. All LLM calls are Azure OpenAI GPT-4.1.

**This is the personalized alternative to `recruitment-email-gen`'s static icebreaker** — pick one per campaign, not both.

| Phase | Script | Purpose |
|-------|--------|---------|
| 0 | `scrape_linkedin.py` | DM's LinkedIn profile → compacted JSON (dev_fusion actor, $3/1k) |
| 1 | `scrape_facts.py` | Whole-site crawl → **per-page** abstracts as JSON (direct HTTP, no Apify cost) |
| 2 | `build_dossier.py` | Merge both sources → summary, niche, `healthcare_fit`, best/second fact + when-tags, flags |
| 3 | `generate_icebreaker.py` | n=3 → gates → reviewer → verify-revise loop → the line, or empty |
| 4 | `generate_body.py` | Greeting + icebreaker + Jude's fixed offer template, routed by `healthcare_fit` |
| 5 | `push_retarget.py` | DRAFT Instantly campaign, subject "new reqs", body rides as `{{personalization}}` |

Phases are split so each is re-runnable alone — retune copy by re-running phase 3 only, no re-scraping. Every script is batch-of-10 and resume-safe (skips filled output cells).

- **LinkedIn is the highest-yield source, not the website.** ~35% of agency sites WAF-block even with a full Chrome header set; a profile that scrapes always carries tenure, and `about` holds founder stories and self-published numbers. ~25-30% of profiles come back blocked per pass — recover by simply re-running. Phase 1 crawls the *whole* site (25 pages / ~14k chars / 75s bounds, priority-scored: team/story first) and emits per-page abstracts, never one concatenated blob — the homepage headline drowns the buried details that are the entire point.
- **`healthcare_fit` (from the dossier) routes the copy**, and **LinkedIn overrides the sheet as source of truth** — profiles routinely show the lead has moved employer since the list was built. That sets a `MOVED->{company}` flag; `NOT_A_RECRUITER` is the other flag. Both are skipped downstream, and `MOVED` rows are never pushed (dead email).
- **Fact priority:** prior career > published numbers > awards > milestone > narrow specialism. **Never education**, in either half. Business model/structure (locums vs perm, direct hire only, headcount-as-commentary), self-classification from directory categories, and values/culture/mission praise are banned fact types. So is surveillance material (posted pay rates, registered entity names, HQ locations) — filtered mechanically at both fact-input and line-output level.
- **The v3 formula is locked (July 2026):** `Love {specific 1}, {compliment tied to specific 1}. Btw, also noticed/saw how/that {specific 2}.` The compliment must credit them and be safe if slightly wrong; general industry truths are fine, guesses about their situation ("you must be struggling to fill those") are not — "must" is mechanically banned. Openers are Love-family only. Company names and acronyms are deliberately lowercase (correct branding everywhere is an AI tell).
- **Designed to return nothing rather than fake-personalize.** Never-converged rows write an empty cell; a `--static_fallback` flag exists but the default posture is empty, and **rows with no icebreaker are skipped by phase 4 and never sent** — the campaign is a personalization-only experiment. Expect a real miss rate.
- Phase 4's offer copy is **Jude's template verbatim** (stored at the top of the script, swapped per A/B test). Only `{icp}` and `{roles}` may change; CTA, proof line, and sign-off are fixed.
- Phases 3 and 4 are subject to the same preview-and-approve gate as any email generation step.

## production-directory-leads

The **live source for the commercial video production lane** (Saad's client brief names ProductionHub as "the main directory"); `production-house-leads`' Google Maps store is parked as a secondary pool, though its 1,134 classified PRODUCTION_HOUSE rows stay usable. SQLite-backed like `nppes-new-clinics` (`data/directory.db`, gitignored), sheets are batches cut from it. `export_batch.py` also checks the Maps store's exported domains, so the two lanes never email the same company.

**`SKILL.md` covers only phases 1-3** (scrape → classify → enrich → export) plus the Cloudflare/geography/thin-profile quirks — read it first. Everything downstream of the batch export was built Aug 11-12 2026 and is **docstring-only**:

| Step | Script | Purpose |
|------|--------|---------|
| Consolidate | `consolidate_master.py` | Merge resolved rows from all batch sheets into one master. **APPEND-ONLY against an existing master** — the original clear-and-rewrite version wiped 276 of 285 found emails (recovered from Drive revision history) because DM enrichment ran on the master, not the batch sheets. Reads the master first, never clears it |
| DM (lane 1) | `find_dm_pm.py` | **Purple Magic is PRIMARY for this vertical** (Jude, 2026-08-11 — reversed vs healthcare). `/decision-makers {domain}` → positive owner-title gate BEFORE `/find`. Writes status to **AC**, never AB |
| DM (lane 2) | `apollo_dm_waterfall_production.py` | Apollo/AMF fallback over PM's misses — a clone of `apollo-dm-waterfall`, retargeted `RANK_SYSTEM`. Status column **AB**. A PM miss (blank W, blank AB) is picked up automatically; a PM find is skipped |
| Icebreaker 0-1 | `scrape_dm_linkedin.py` (→AD), `scrape_company_facts.py` (→AE) | Clones of `personalized-icebreakers` phases 0-1 with the GPT abstraction step **deleted** — raw per-page text, not summaries |
| Icebreaker 2-3 | `collect_icebreaker_research.py` → *Claude judges* → `apply_icebreakers.py` (→AF icebreaker, AG fact_type) | Claude-in-session judge (above). No LLM API anywhere in this lane (Jude, 2026-08-12) |
| Copy | `generate_production_body.py` (→AH) | Saad's fixed Email 1 copy + greeting + icebreaker. Pure string assembly, no `{icp}`/`{roles}` slots to extract |
| Push | `push_production_retarget.py` | DRAFT campaign, Saad's 4-email framework (Day 0/2/3/4). Steps 2-4 carry no per-lead body — generic follow-up using `{{firstName}}` |
| Sync | `sync_revised_personalization.py` | One-off: PATCH already-pushed leads' personalization after a copy revision (safe only while the campaign is DRAFT) |

DM target for this vertical (Jude, 2026-08-11, **hypothesis — no campaign data yet**): Owner / Founder / CEO / President / Managing Director / Managing Partner / Principal, nobody else. Both lanes share the same `OWNER_RE`, reused from `healthcare-staffing-enrichment/find_ceo_pm_demand.py`.

Two conventions differ from the rest of the repo and are deliberate: **AC is the PM status column** (elsewhere AB carries the single status), and **icebreaker-less rows are still sent** — Jude reversed `personalized-icebreakers`' personalization-only posture on 2026-08-12, so 129 of 312 leads go out on Saad's plain copy alone rather than being dropped.

Cloudflare, geography, and profile-thinness gotchas all live in the SKILL.md — the short version: run the scraper with `--local` (cloud runs are CF-blocked), ProductionHub is US+Canada only, and free-tier profiles carry almost no contact data, so domains come from `exa-website-enrichment` on the exported sheet rather than from profile visits.

## production-demand-leads

Demand side of the video production lane (supply side is `production-directory-leads`): detect direct brands that need commercial video NOW, by signal, and **stack signals per company** — single signals reply ~1% in this vertical, stacking is the edge. SQLite-backed (`data/demand.db`, gitignored) like `nppes-new-clinics`. `SKILL.md` is current and has the signal table, scoring weights, and cautions.

Four signal types, each with its own collector: `video_job` (Indeed, video keywords × supply-side metros), `new_exec` (LinkedIn post search → GPT-4.1 extract; the announced person IS the DM, prefilled at T/U/V — verify before spend), `funding` (EDGAR Form D daily indexes, free), and `ad_stale` (FB Ad Library — an **enrichment step over stored companies, not a discovery source**). Order: collectors (any order) → `filter_video_jobs.py` (GPT-4.1 relevance judge; interns/UGC/content-creator = anti-signal) → `classify_companies.py` (only BRAND survives; ENTERPRISE_INHOUSE 10k+ excluded at export, not deleted — that cap is a hypothesis, unlike healthcare's measured 500) → `enrich_adlibrary.py` → `export_batch.py` (score+stack → 29-col sheet, delta-by-default, K/L/R/S/AB standard so `exa-website-enrichment` and `apollo-dm-waterfall` run unmodified). Collectors are idempotent (UNIQUE on company+type+key); re-running is the resume path. Downstream DM/copy/push is gated: the DM ladder for this vertical is an untested hypothesis — get Jude's sign-off before AMF spend.

## hirebase-healthcare-leads

Healthcare demand lane fed from **HireBase** exports. A SEPARATE skill from
`healthcare-demand-pipeline` by Jude's explicit call (2026-08-19) — HireBase is
a different platform under evaluation, and its data is rich enough that the
enrichment shape genuinely differs. `SKILL.md` is detailed and current — read it
before touching this lane; the pipeline is COMPLETE through push. Phases:
`normalize_export.py` → `resolve_domains.py` → classification
(`collect_classification.py` → *Claude judges* → `apply_classification.py`,
plus `delete_rows.py` for approved removals — backs up every row first) →
identity recovery (`collect_identity_recovery.py` → *Claude judges* →
`apply_identity_recovery.py`) → `assign_segments.py` → DM discovery
(`build_dm_worklist.py` → `apollo-dm-waterfall` → `sync_dm_results.py`, then
`rescue_dm_amf.py` → `rescue_dm_pm.py`) → `generate_bodies.py` →
`push_campaign.py`.

⚠️ **Both campaigns are ACTIVE as of 2026-08-20** (HireBase SLP + HireBase
General Healthcare, 324 leads / 324 distinct inboxes) — the frozen-campaign
rule applies to them and to the sheet rows feeding them. `push_campaign.py`
holds Jude's current `email_tag_list` + `provider_routing_rules` values (the
by-tag attach convention started here). Attaching mailboxes moves a campaign to
status 2 (paused); it is not active until `POST /campaigns/{id}/activate`.

Three things about this platform that will bite if assumed away:

- **Column letters are not stable across tabs.** HireBase flattens JSON arrays,
  so a tab with more `benefits/*` or `services/*` entries shifts every later
  field — `companyName` is BL on one tab and CE on another. Resolve by HEADER
  NAME, never by letter. Exports also get pasted on top of each other with a
  second header row buried mid-sheet, silently misaligning everything below it;
  `split_blocks()` handles that.
- **Domain resolution is nearly free here, so search is a LAST resort.** 464 of
  468 companies already carry a website and 468 of 468 carry a company
  LinkedIn. Waterfall: sheet domain (only if its root corroborates the company
  name) → the company's LinkedIn page → then the old Google/Exa resolution.
  Only 4 companies reached tier 3. **LinkedIn is not trusted blindly either** —
  it replaced a correct domain with a GiveLively donation page on the first
  run, so a replacement must be non-junk AND corroborate the name, else the
  sheet's value stands. Unchanged beats wrong.
- **HireBase attaches the WRONG company's profile on ~12% of companies** (54 of
  468). `companyData` is fuzzy name-matched: "GMH UK" (UK steel billets) posts
  RNs in Georgia; "SIH Hôtels" (French hotel investor) posts RN Same Day
  Surgery in Illinois — that employer is Southern Illinois Healthcare. Because
  the NAME matches, a naive name-vs-domain check ACCEPTS the wrong company's
  website. Detector: profile text has no healthcare signal while the postings
  are clinical. Those rows are stamped `REVIEW_PROFILE_MISMATCH` and held back
  from spend. The ATS tenant in col AR is the ground truth for recovering the
  real employer — the identity-recovery judge pass rewrites name/website behind
  a **proof-on-page gate** (a judge-proposed domain is fetched and must name the
  company, else the name is corrected and the website left blank with
  `needs_search`). Most recoverable mismatches turned out to be large hospital
  systems, stamped `REVIEW_OVER_CAP` rather than sent.

**Dedupe is per-JOB on `applicationLink`, never by company** (Jude). Despite the
name, `jobBoardLink` is the company's board ROOT and maps 1:1 to companies, so
deduping on it collapses the list to one row per company. Rows stay per-job as
the evidence layer, but **the outreach unit is one lead per COMPANY** (Jude,
2026-08-19) — openings/cities/roles become copy variables, and
`build_dm_worklist.py` collapses KEEP rows to one per company BEFORE the
waterfall (pointing `apollo-dm-waterfall` at a lane tab directly would enrich
Compassus 380 times). Classification is deliberately light (Jude: "no need to
do too hard") — default KEEP; suspicious companies reach the judge.
**Downstream must require AV == KEEP.** `dm_target` is CEO on ALL companies —
multi-city rows are deliberately NOT routed to a COO (0/87 measured), and the
`LARGE_ORG` band (AX) is a deliberate large-org-CEO TEST; read reply rates BY
BAND. Layout is the 29-col base with AD-AM reserved blank for the audit trail
and AN-BA carrying HireBase extras, statuses, segment/size-band/DM-target and
copy variables.

## energy-emaas-leads

Demand pipeline for Energy GreenPrint (Sherif Hani, Melbourne — EMaaS). The ICP gate (electricity spend > A$90k/yr) is not public, so **no company enters the store without documented evidence of energy intensity** — an EPA VIC operating licence (`ingest_epa.py`, the anchor tier, free) or a signal-bearing Indeed job ad (`scrape_indeed_signals.py`, regex-classified shift/equipment signals). Firmographics alone never qualify a row. SQLite store (`data/emaas.db`), 29-col sheet batches via `export_batch.py` (delta-by-default like `nppes-new-clinics`), company/website/city/state/status at K/L/R/S/AB so `exa-website-enrichment` and `apollo-dm-waterfall` run unmodified. **Two contacts per company — do NOT dedupe to one row per company.** This vertical's agency-trap is **service contractors** (HVAC/refrigeration/FM firms advertise the same trades but service equipment rather than own it); excluded rows are stored flagged, never dropped.

`SKILL.md` is the reference but already drifted: it lists `export_batch.py` and the Claude-in-session judge phase as "planned" — `export_batch.py` shipped (commit `49ce765`), and `scrape_indeed_signals.py` grew a `--state` flag so NSW/QLD grids share the scraper (commit `ab2f74a`). Indeed's `employeesCount` is the GLOBAL headcount band for multinationals — a hint, never a gate.

The drift widened in Sep 2026 — everything below is docstring-only:

- `scrape_seek_signals.py` — Phase 1c, SEEK via `websift~seek-job-scraper` (3-4x Indeed AU volume; same signal logic, imported from `scrape_indeed_signals.py`). Input quirks learned the expensive way: ALWAYS pass `maxResults` (default maxItems is 300; min 10, cap 550); the fields are `searchTerm`/`location`/`dateRange` — a bare `keyword` is silently ignored and returns a default feed; state-level filtering is unreliable, pass city-level `location`. Most rows arrive with domain + size pre-filled from `companyProfile`.
- Store-lane tail: `apollo_dm_waterfall_emaas.py` / `amf_dm_fallback_emaas.py` (DM), `generate_emaas_bodies.py` (Phase 4 — connector framework, Jude introduces Sherif; two mechanical variable slots from store fields, NO LLM call), `push_emaas_campaign.py` (Phase 5 — DRAFT, text-only, per-lead body only on step 1, follow-ups generic so a copy revision never means regenerating rows).
- **AI Ark food & beverage lane (separate track, own sheet, own A-N column layout — not the 29-col base):** the AI Ark export is contact-level with BounceBan-verified emails, so it skips the entire scrape/resolve/enrich stack. `build_aiark_sheet.py` (filter: verified email, ≤1000 employees, ops/production/trades/leadership departments; dedupe by literal email, NOT by company) → `generate_aiark_icebreakers.py` (Azure **GPT-5.1** Love-X from the AI Ark description alone, no scraping; strict grounding — any 4-digit year absent from the source is rejected; blank beats faked, the body opens on the bridge line instead) → `generate_aiark_bodies.py` (locked template, pure string assembly) → `push_aiark_campaign.py` (DRAFT; sending window Asia/Taipei 12:00-17:00 Mon-Fri; tag-attach config baked in, read off Pipeline Intro Sep 2026).

## equipment-finance-leads

Equipment-finance connector lane, US (Sep 2026). SQLite store `data/finance.db` (gitignored). ⚠️ **The SKILL.md is stale on the business model:** it describes a two-sided play — cold DEALER campaign (medical/dental equipment dealers from Google Maps) + curated LENDER shortlist to land one client. Jude has since **pivoted to D2C**: the LENDERS are themselves the cold campaign, pitched borrower demand directly (his SBA campaign is the precedent), and the dealer/Maps lane is parked (~1,945 raw rows in the store). Docstrings are the reference for the live lender lane:

- `ingest_salesnav.py` — LinkedIn Sales Navigator PEOPLE exports (Apify scraper output) → 29-col lender-contacts master, one contact per lender, columns resolved by HEADER NAME not letter. Sales Nav is sliced BY STATE around its 2,500-result cap. Non-lender industries and company-name patterns dropped at ingest; the DM is already identified by the Sales Nav search, so there is no DM-finding or verify step.
- Domain waterfall: `enrich_websites_linkedin.py` runs FIRST — scrapes each lender's company LinkedIn page (col AE, `pratikdani` actor) for its own website, which carries no wrong-company risk — then `resolve_domains_google.py` fills only what LinkedIn couldn't (a retry/backoff variant of `find_company_domains.py`, which kept dying on Apify 300s read-timeouts). Blank beats wrong throughout.
- `fill_emails_amf.py` — AMF person-endpoint fill; writes per batch, stamps misses AB=`amf_no_email` so re-runs skip the low-yield wall instead of starving newly-added rows, `--source salesnav:IL,...` targets specific state ingests.
- `push_campaign.py` — DRAFT; copy is pure `{{firstName}}` and lives entirely in the sequence (no per-lead body). D2C connector offer: surface borrower demand; no call ask, no "free", no state in the email — those live in the reply conversation. Mailboxes by tag + provider matching read off Jude's most recent campaign.

The SKILL.md's dealer pipeline (`scrape_maps.py` → `classify_dealers.py` → `export_batch.py`), the captive-giants denylist (`finance_common.CAPTIVE_GIANTS`), and the lender fit-judge flow (`pull_lenders.py` → in-session judge → `apply_lender_fit.py` → `export_lenders.py`) remain accurate for the parked side.

## trades-staffing-leads

Sep 2026 pivot of the AI Ark supply motion from healthcare to **trades/construction staffing agencies**, US nationwide. **No SKILL.md — docstrings are the reference.** Works the AI Ark A-N export layout with O-S appended (same status vocabulary as `find_ceo_demand.py`); scripts are clones of the healthcare-staffing-enrichment lanes, whose parents feed live campaigns and must not be edited.

- **`classify_trades_icp.py` runs BEFORE any DM/email spend.** On the first batch it ran AFTER the waterfall (2026-09-11) and paid to enrich insurance-claims, superyacht, and mobile-notary firms — free filter first, paid stages second. Two-axis rule mirroring the healthcare ICP pass: `staffing_firm` × `serves_trades_employers` (clients are contractors / industrial / manufacturing / energy companies; ANY role placed into such an employer counts — an estimator is as valid as a welder). Col Y vocabulary: `TRADES_STAFFING` (the ICP) / `TRADES_ADJACENT` (trade schools, union halls, apprenticeship trusts, safety consultancies, equipment rental, PEO/payroll-only) / `NOT_TRADES` / `UNCERTAIN`.
- **Three-stage DM waterfall (Jude, 2026-09-10), judged wherever a LIST comes back** ("don't use regex, use the LLM to judge"): (1) `pm_dm_judge.py` — Purple Magic `/decision-makers` as a Claude-in-session judge flow: collect replays `data/pm_dm_cache.jsonl` free and fetches uncached domains with NO `/find` spend → Claude picks per company (`pick: ""` = nobody has authority) → apply runs `/find` on judged picks only, refusing picks not in the row's candidate list. It superseded the regex gate in `find_dm_pm_trades.py`, which stamped 269 rows `pm_bad_title` on people PM DID return ("Executive Director" at a 10-person firm is usually the owner; no regex separates that from a "VP of Professional Services"). (2) `apollo_dm_judge.py` — free Apollo search per domain → Claude judges the candidate list → Google de-obfuscation → Purple Magic `/find` → AMF person fallback. (3) `find_dm_amf_trades.py` — AMF `/decision-maker` `ceo` for whatever is left; no judge needed since AMF picks one person itself (measured 94.9% owner-like). Target: owner / CEO / partner / founder / president ONLY — no BD, no VP grades, no ops, no branch/account managers.
- `push_trades_campaign.py` — DRAFT clone of `push_pipeline_intro_campaign.py`. Copy is STATIC in the sequence (`{{firstName}}` only — no per-lead personalization and no generate step or col V body). No first-name casualization (case-normalized only). Deliberate copy choices, approved in-session 2026-09-11, do not "improve": the opener asks a QUESTION rather than for the call, no case study in the opener, follow-ups are short bumps that never name a specific trade.

## nppes-new-clinics

**The first pipeline that is not a Google-Sheets pipeline** (`production-house-leads` later adopted the same model). Everything else in this repo treats a Sheet as the database and enriches rows in place. This one ingests CMS NPPES bulk files into **SQLite** (`data/nppes.db`, WAL, gitignored) and only emits a Sheet/CSV at the export step. Sources demand *before* a practice posts a job ad: a new organization NPI lands 3-8 months before an insurance-accepting clinic opens, 1-3 months before its first staff ad. `SKILL.md` is detailed and authoritative — read it before touching this skill.

| Phase | Script | Purpose |
|-------|--------|---------|
| 1 | `pull_new_practices.py` | Weekly V2 zip → filter (org + window + state + taxonomy) → SQLite |
| 1.5 | `build_baseline.py` | Monthly full file (~1.1GB) → address/name novelty baseline; rebuild monthly |
| 2 | `classify_practices.py` | NEW_INDEPENDENT / NEW_LOCATION / LIKELY_ADMIN / UNCERTAIN + solo-PLLC flag + score |
| 3 | `export_leads.py` | Scored CSV + optional Sheet; `--states IN,TX` filters to a client's geography |
| util | `resync_store.py` | Re-apply current allowlist/normalization to already-stored rows |

Architectural differences that will bite if assumed away:

- **Config-driven, never hardcoded** — `config/settings.json` (states, window, scoring weights) and `config/taxonomy_allowlist.json` (volume-first broad include + `_denylist`). Edit config, not scripts.
- **Pull skips known NPIs, so config changes never self-correct stored rows.** After tuning the allowlist or normalization you MUST run `resync_store.py`, or the DB keeps decisions made under the old config.
- **Exports are deltas by default** (`exported_at IS NULL`). `--include_exported` re-exports everything; `--mark_contacted npis.txt` retires worked leads.
- **Filter on Provider Enumeration Date, never on file presence** — weekly files mix new enumerations with updates and deactivation stubs (blank Entity Type Code; drop those first). The first weekly after a monthly release is ~4x normal size from update bloat, not new orgs.
- **The NPPES API cannot substitute for the bulk files** — no enumeration-date filter (params silently ignored), hard ~1,200-row ceiling with silent duplicate pages past it. API is for per-NPI lookups only.
- **~45-50% of new org NPIs are solo-clinician PLLCs**, not staffing launches. The solo flag is a score penalty, not a drop. Say "registered", never "opened" — and don't personalize on the practice address, which is often the owner's home.
- **Validation gate before any enrichment spend:** hand-check 20-30 NEW_INDEPENDENT records with Jude first. (The v1 "enrichment stays out of scope" rule is retired — the campaign track below now carries enrichment and copy in-skill.)
- National by design (~950/week raw allowlisted). Single states are too thin to scrape alone (IN ~11/week) — always run national, filter at export.
- **Multi-site owner signal:** one Authorized Official holding several new NPIs is a group opening locations, i.e. hot demand. `owner_site_count` is computed across the whole store in `resync_store.py` and scored `+multi_site_owner` only inside the 2-9 band — past ~10 sites it's an enterprise system (Cleveland Clinic's CFO holds 138), MSP-gated, not a warm lead. This makes `resync_store.py` matter for scoring, not just for allowlist changes.

### Campaign-execution track (Jul-Aug 2026 — NOT in the SKILL.md; docstrings are the reference)

The store → campaign sheet → verified email → copy path, built as untracked scripts in the same `scripts/` dir. Proven logic from other skills is reused **by import**, never edited — `resolve_domains_batch.py` and `resolve_parent_domains.py` both `importlib`-load `healthcare-demand-pipeline/scripts/find_company_domains.py`, which feeds the LIVE Indiana pipeline and must not be touched.

| Step | Script | Purpose |
|------|--------|---------|
| Sheet | `build_campaign_sheet.py` | Older category-priority sheet (psychiatry/behavioral first; `mental_health_counseling` excluded — 1099-therapist practices don't pay placement fees). Random within categories, one row per OWNER, solo/LIKELY_ADMIN excluded |
| Sheet | `build_commercial_sheet.py` | THE current sheet (scope agreed 2026-08-04): full commercial pool — multi-staff clinics, provider practices, home-care/nursing agencies, facilities IN; solo PLLCs and non-clinical social services OUT. Facility Type (col B) is the per-CODE NUCC display name (never per-prefix — a prefix bucket once swept in a horse stable). Fully shuffled by explicit instruction so reply data, not ordering, decides what works |
| Domains | `resolve_domains_batch.py` | Same Google-via-Apify + LLM pick as Indiana's `find_company_domains.py`, plus one behavior: misses stamp AB=`fcd_no_match` so reruns reach fresh rows (the original re-Googles the same failures: 400 lookups → 7 new domains on pass two) |
| Domains | `resolve_parent_domains.py` | For shell-LLC rows ("FAIRVIEW OPCO LLC"), search the Parent Org LBN (col J) instead; writes the parent domain with AB=`parent_domain` — for a health-system site the parent is where buying power lives |
| Email | `enrich_leads.py` | **Phase 3.5, store-side (not sheet-side) — costs money.** Domain (Apify Google + GPT-4.1, ~$0.007/company) → AMF person on the Authorized Official (1 credit per found valid email). No Apollo waterfall and no DM ranking: NPPES already names the DM, and the AO is the owner/CEO on 65% of records. `--owners_only` defaults ON, `--backed_first` orders expansion rows first (a parent business means a site already exists). Failures keep their phone and stay in the store for a later retry |
| Email | `find_ceo_emails.py` | Campaign-sheet layout. AO title owner-like → AMF person (1 credit); else AMF /decision-maker `ceo` (2 credits). `--target N` stops once N valid emails exist |
| Email | `find_dm_waterfall.py` | Commercial-sheet layout. **The filer is the target — deterministically** (the docstring still describes an LLM gate; the in-code comments dated 2026-08-04 supersede it: the gate was tried and wrongly rejected COOs and Office Managers — filing an NPI is itself evidence of authority). Only support-function titles (`NOT_TARGET` regex: finance, legal, billing, IT, marketing, front desk…) fall through to PM /decision-makers + GPT-5.1 ranking, with a **positive gate** on ranked titles (unknown fails; "Assistant to CEO" can't ride the word CEO through). Col AI records which lane won |
| Email | `pm_rescue.py` | Purple Magic second lane over AMF `not_found` rows — AMF misses ~65% here because practices registered 30-90 days ago barely exist on the web yet |
| Copy | `generate_connector_emails.py` | Jude's verbatim templates: Variant A (new practice) / Variant B (new location). `{ptype}`/`{gtype}` come from a fixed NUCC-code map — deterministic, no LLM; generic codes fall back to Jude's generic wording. Casualization embedded; bodies → col Z; `--preview` approval gate applies |
| Push | `push_connector_campaign.py` | Phase 5 — DRAFT Instantly campaign "New Clinics Connector - Aug 2026" (tab `Leads`). Per-variant subject via `{{subject_line}}` custom var (`practice staffing` for A / `new location staffing` for B/C); 3-step sequence (day 0/2/5, steps 2-3 blank-subject); signature lives in the SEQUENCE (`{{sendingAccountFirstName}}` + "Sent from my iPhone"), never in the per-lead body. One lead per unique inbox (first row wins, siblings marked DUP), text-only, no sending accounts, blocklist rejections marked `BLOCKLISTED` and never retried |

Both sheet builders deliberately place company/website/city/state/status at K/L/R/S/AB — `exa-website-enrichment`'s default flags — so that skill runs against them unmodified. All the standing email rules apply: valid-only, email domain must match the resolved website, free mailboxes rejected, never a name without an email.

## Agents

| Agent | Purpose | Model |
|-------|---------|-------|
| `decision-maker` | Research companies and identify DMs with budget authority | Sonnet |
| `code-reviewer` | Unbiased code review (correctness, performance, security) | Sonnet |
| `email-classifier` | Classify Gmail into Action Required / Waiting On / Reference | Sonnet |
| `qa` | Generate tests, run them, report pass/fail | Sonnet |
| `research` | Deep investigation with web + file access | Sonnet |
