---
name: hirebase-healthcare-leads
description: Healthcare demand pipeline sourced from HireBase job exports (not Indeed, not TheirStack). Normalizes a flattened HireBase export tab into the repo's 29-col schema per lane, resolves company domains from data already on the sheet before ever paying for a search, and screens out non-employers. Use when the user asks to work a HireBase export, build the SLP / General Healthcare lanes, or run the Aug 19th healthcare campaign.
---

# hirebase-healthcare-leads

Healthcare demand lane fed from **HireBase** exports. A separate skill from
`healthcare-demand-pipeline` by Jude's explicit call (2026-08-19): HireBase is
a **different platform being evaluated**, and its data is rich enough that the
enrichment shape genuinely differs — not just a new source for the same
pipeline.

First run: sheet **"Healthcare US - Aug 19th"**
(`11RLxpT5_IklxIO5MlcdtexFciV1nk4OuPtMcNV_FgZc`), two lanes.

| Lane | Working tab | Jobs | Companies |
|---|---|---|---|
| Speech Language Pathologist | `SLP Campaign` | 253 | 81 |
| General Healthcare | `General Campaign` | 1,788 | 407 |

(2,272 rows after phase 1; 231 removed at the screen — see below.)

## Why this platform is different

On an Indeed scrape the domain is missing and every company needs a paid
lookup. On HireBase, measured on the Aug 19th export:

- **464 of 468 companies already carry a website.** Only 4 were missing.
- **468 of 468 carry a company LinkedIn URL.**
- So Google/Exa domain resolution is a **last resort**, not phase 1.9.

That is the upside. The cost is two data-quality traps that do not exist on
Indeed, both of which will silently email the wrong person if ignored.

## Trap 1 — column letters are not stable across tabs

HireBase flattens JSON arrays into columns, so a tab with more `benefits/*` or
`services/*` entries shifts every later field. On the Aug 19th sheet
`companyName` is **BL** on the SLP tab and **CE** on the General Healthcare
tab. **Never address a HireBase export by letter.** `normalize_export.py`
resolves every field by header name.

## Trap 2 — exports get stacked with a header row buried mid-sheet

The SLP tab was two exports pasted together: rows 2-149 matched the row-1
header (151 cols), row 150 was a **second header** (306 cols), and rows 151+
matched that one. Read against the top header alone, 343 rows were silently
misaligned — `companyName` read blank, `country` read "In-Person", `jobTitle`
held whole job descriptions. `split_blocks()` detects a row repeating a header
signature and maps each block against its own header.

## Trap 3 — HireBase attaches the WRONG company's profile ~12% of the time

`companyData` (description, **website**, size, LinkedIn) is resolved by fuzzy
company-name match, and on **54 of 468 companies (11.5%)** it belongs to a
different company entirely:

| Row says | Profile describes | Jobs actually are |
|---|---|---|
| GMH UK | UK steel billets for automotive | RN, Georgia |
| SIH Hôtels | French hotel investment firm | RN Same Day Surgery, Illinois (Southern Illinois Healthcare) |
| Springbrook Software | cloud ERP for local government | SLP "On Campus", New York |
| Astera Institute | venture incubator | Oncology RN (ATS: `oneoncology.wd1.myworkdayjobs.com/Astera`) |
| ALPHA | localization services | RN (ATS: `pennant.wd1.myworkdayjobs.com`) |

This matters far more than a missing domain: the name matches, so a naive
name-vs-domain check **accepts** the wrong company's website. Detector:
the profile text carries no healthcare signal while the postings are clinical.
Those rows are stamped `REVIEW_PROFILE_MISMATCH` and held back from spend.

**The ATS tenant in col AR (`jobBoardLink`) is the ground truth** for
recovering the real employer, and the job description in col J is the other
half — postings name their employer in the opening sentence. Both are free.
`collect_identity_recovery.py` gathers them; Claude judges; and
`apply_identity_recovery.py` rewrites the name and website behind a
**PROOF-ON-PAGE GATE** — a judge-proposed domain is fetched and must actually
name the company, or the name is corrected and the website is left BLANK with
`needs_search`. Never write a domain on the judge's say-so.

Recovery of the 61 mismatch companies (221 rows), 2026-08-19:

| Outcome | Companies | Rows |
|---|---|---|
| `KEEP_AS_IS` — name/site were right, only the description was wrong | 12 | 37 |
| `RECOVERED` — real employer identified, in ICP | 17 | 35 |
| `RECOVERED_OVER_CAP` — real employer is a large system | 12 | 101 |
| `DROP_AGENCY` / `DROP_NOT_EMPLOYER` — recovery unmasked a staffing firm | 11 | 25 |
| `UNRESOLVED` — evidence insufficient | 9 | 23 |

Worked examples: `GMH UK` → **Grady Health System** (tenant `gmh`, Main Grady
Campus Downtown Atlanta); `Epyz` → **Nemours Children's Health**; `SIH Hôtels`
→ **Southern Illinois Healthcare**; `Wellth` → **West Tennessee Healthcare**
(tenant `wth`); `TruMed Systems` → **University Health Kansas City** (tenant
`trumed` = Truman Medical); `Obran Cooperative` → **Apollo Home Healthcare**.

⚠️ **Most recoverable mismatches turned out to be large hospital systems** —
101 of 221 rows. They are stamped `REVIEW_OVER_CAP` rather than sent: identity
is now TRUE, but the standing 500-employee cap says do not email them. That is
Jude's call to make, not the pipeline's.

8 of 29 proposed domains failed the proof gate on a **WAF block**, not a wrong
guess — consistent with `personalized-icebreakers`' finding that ~35% of sites
block automated fetches even with full Chrome headers. Those rows keep the
corrected NAME and fall to `needs_search`.

## Phases

| Phase | Script | Purpose |
|---|---|---|
| 1 | `normalize_export.py` | Raw HireBase tab → 29-col lane tab. 35-day window FIRST, then 500-employee cap, then **per-JOB dedupe**. |
| 2 | `resolve_domains.py` | 3-tier domain waterfall (below). |
| 3a | `collect_classification.py` | Mechanical: flag companies worth a second look. No spend. |
| 3b | *Claude judges in-session* | Hand-write `data/class_verdicts.json`. |
| 3c | `apply_classification.py` | Write col AV under a hallucination guard. Deletes nothing. |
| 3d | `delete_rows.py` | Remove rows by status/company. Backs up every row first. `--apply` required. |
| 3e | `collect_identity_recovery.py` | Recover the REAL employer behind mismatch rows. Free. |
| 3f | *Claude judges* → `apply_identity_recovery.py` | Rewrite name/website under a **proof-on-page gate**. |
| 3g | `assign_segments.py` | Segment, size band and DM target per company. Free. |
| 4 | `build_dm_worklist.py` → **apollo-dm-waterfall** → `sync_dm_results.py` | DM discovery, one row per COMPANY. |
| 4b | `rescue_dm_amf.py` → `rescue_dm_pm.py` | AMF /decision-maker (CEO only) then Purple Magic. |
| 5 | `generate_bodies.py --tabs <both>` | Copy. APPROVAL GATE. Merges lanes so a company gets ONE email. |
| 6 | `push_campaign.py` | DRAFT Instantly campaign, one lead per company. |

### Dedupe: per JOB, never per company (Jude, 2026-08-19)

The key is **`applicationLink`**. It is **not** `jobBoardLink` — despite the
name, that is the company's board ROOT (`job-boards.greenhouse.io/wovencare`).
Measured: 432 jobBoardLinks map to 432 companies and none maps to more than
one, so deduping on it collapses the list to one row per company, which is
exactly what Jude ruled out. A company keeps one row per live posting; the
count is precomputed in col AN.

Whether the Instantly push is one lead per company or one per job is a
**Phase 4 decision, deliberately still open**.

### Domain waterfall (`resolve_domains.py`)

| Tier | Source | Cost | Aug 19th result |
|---|---|---|---|
| 1 | sheet's `companyWebsite`, accepted only if its root corroborates the company name | free | **404 of 468** |
| 2 | the company's own LinkedIn page | ~1 Apify call | **60**: 55 confirmed, 2 filled, 1 corrected, 1 kept-over-junk, 1 disagreed |
| 3 | Google/Exa search — the old way, last resort | paid | **4 companies** (2 `needs_search`, 2 `linkedin_no_website`) |

**LinkedIn is not trusted blindly either.** On the first run it replaced Edward
M. Kennedy Community Health Center's correct `kennedychc.org` with a
**GiveLively donation page**, and Ste. Genevieve County Memorial Hospital's
plausible domain with an unrelated one. `decide()` now requires a replacement
to be non-junk **and** to corroborate the company name; when neither candidate
corroborates it keeps the sheet's value and stamps
`linkedin_disagreed_review`. Unchanged beats wrong.

Col L is overwritten **only** on `linkedin_corrected` / `linkedin_filled`, the
prior value is preserved in AU, and the raw export tabs are never touched.

### Classification is deliberately light (Jude: "no need to do too hard")

Default verdict is **KEEP**. HireBase's own agency flags were False on every
row of this export, so this is a second pass for what they miss, not a
re-classification. Only companies tripping a suspicion signal reach the judge:
**377 auto-kept, 91 judged**. Vocabulary: `KEEP`, `DROP_AGENCY`,
`DROP_NOT_EMPLOYER`, `REVIEW_PROFILE_MISMATCH`.

Result: 399 KEEP, 64 REVIEW_PROFILE_MISMATCH, 3 DROP_AGENCY, 2
DROP_NOT_EMPLOYER.

**206 rows removed on 2026-08-19** (Jude's go-ahead), backed up to
`data/deleted_rows_*.json`:

| Removed | Rows | Why |
|---|---|---|
| InHome Therapy, Akicita Federal, Akahi Associates | 63 | staffing firms — competitors, not buyers |
| NHA, Hippocratic AI | 15 | not employers (certification vendor; AI-evaluator role) |
| "Intermountain Ventures" | 100 | ATS tenant `imh` — really Intermountain Health, ~68k staff, far over the 500 cap |
| "Elas" | 22 | ATS tenant `denverhealth` — really Denver Health, ~7k staff |
| "TSFD Limited" | 6 | ATS tenant `tysonfoods` — an occupational-health RN at a meat plant |
| 10 more staffing firms unmasked by identity recovery | 25 | Ladgov, Resolution Think, Grace Federal, Flatwater, Topaz HR, ICS, StarTekk, MacMore, Ansible, HMR |

The last three were unmasked by the ATS tenant, not by their (wrong) profile.
The 221 mismatch rows were then RECOVERED rather than discarded — see Trap 3.

Final state: **2,041 rows / 448 companies**, of which **1,911 rows are `KEEP`**
and enrichable. Remaining held back: 101 `REVIEW_OVER_CAP`, 23
`REVIEW_PROFILE_MISMATCH`, 6 `REVIEW_NEEDS_DOMAIN`.

> **Downstream must require `AV == KEEP`.** The 64 mismatch companies (349
> rows) carry another company's domain; enriching them emails the wrong org.

## Outreach unit and segments (Jude, 2026-08-19)

**One lead per COMPANY, never one per job.** Rows stay per-job as the evidence
layer; openings / cities / roles become copy VARIABLES. A DM with nine reqs is
still one person with one inbox.

| Segment (AW) | Companies | Rows | Hook |
|---|---|---|---|
| `SINGLE` | 214 | 214 | the one specific req |
| `MULTI_ONE_CITY` | 71 | 226 | volume at a single site |
| `MULTI_CITY` | 147 | 1,534 | outgrown the local network — the connector pitch |

**`dm_target` (AY) is CEO on all 432 companies.** Under 500 that is the only
evidence-backed rung (owner/CEO 2.80% vs COO/Ops 0.00% from 87, HR 0.00% from
32). Multi-city companies are deliberately NOT routed to a COO for that reason,
and because only the owner is company-wide — office managers are per-location.

⚠️ **`size_band` (AX) marks a deliberate TEST, not a proven rung.** 18
companies / 763 rows are `LARGE_ORG`. The known 0.00% from 251 large companies
is confounded — those were targeted 175 clinical / 22 HR / 41 other with **no
actual CEO among them**, so it disproves clinical-and-HR-at-big-orgs, not
large orgs. A large-org CEO has never been tried. Jude chose to try it
(2026-08-19); AX keeps the bands separable so the run answers the question
instead of blurring it into the aggregate. Read reply rates BY BAND.

The `LARGE_ORG` arm is dominated by a few employers: Compassus 385 rows, Ivy
Rehab 104, Methodist Health System 75. 27 companies with 10-24 live openings
sit in `WITHIN_CAP` and are genuinely borderline.

## Phase 4 copy (Jude's template, 2026-08-19)

Verbatim; only `{first}` and `{roles}` may change. No subject, no sign-off,
no em dashes.

```
Hi {first},

Are you hiring for {roles}?
I know someone with a few candidates looking for new roles.

Can I connect you?
```

**No recipient nickname casualization** on this lane (Jude, 2026-08-19) — same
call as `production-directory-leads`. William stays William; only letter case
is normalized, since AMF returns "SARAH".

`{roles}` is the only real work. Raw HireBase titles are per-posting and
near-duplicate, so joining them reads like scraped data — one company's SLP
openings arrive as five separate titles. `generate_bodies.py` matches titles
against DISCIPLINE FAMILIES and collapses them, counting every family a title
matches (one posting can name several), capped at 3 by live-posting count.
A single posting stays singular and joins with "or".

Four matcher bugs were caught in `--preview` and would have shipped:
"physician **assistant**" also matching the Physician family; `\bmd\b`
matching state abbreviations in titles; a one-posting company being
pluralised; and "Speech **and** Language Pathologist" not matching at all.
**Always read a preview before approving.**

Coverage: SLP 76/77 companies, General 393/393.

## Column layout (both lane tabs)

Standard 29-col base at A-AC, so `exa-website-enrichment` and
`apollo-dm-waterfall` run unmodified (company/website/city/state/status at
K/L/R/S/AB). **AC holds the job posting URL** per Jude's standing rule.

```
A-J   job          K-S  company + location    T-AA outreach (blank)
AB    dm_status (blank, for the DM waterfall) AC   Job URL
AD-AM RESERVED BLANK for the generation audit trail
AN    Openings @ Company   AO Company LinkedIn  AP Location Type
AQ    Job Board            AR Job Board Link (ATS tenant = ground truth)
AS    Source Tab           AT domain_status     AU domain_note
AV    company_status      AW segment      AX size_band
AY    dm_target           AZ cities       BA roles
```

## Commands

```bash
U="https://docs.google.com/spreadsheets/d/11RLxpT5_IklxIO5MlcdtexFciV1nk4OuPtMcNV_FgZc/edit"

# Phase 1 — always --dry_run first
python3 -W ignore scripts/normalize_export.py --sheet_url "$U" \
  --src_tab "General Healthcare" --out_tab "General Campaign" --dry_run

# Phase 2 — omit --apply to report only. --only re-resolves one company.
python3 -W ignore scripts/resolve_domains.py --sheet_url "$U" \
  --tab "General Campaign" --apply

# Phase 3
python3 -W ignore scripts/collect_classification.py --sheet_url "$U" \
  --tabs "SLP Campaign" "General Campaign"
python3 -W ignore scripts/apply_classification.py --sheet_url "$U" \
  --tabs "SLP Campaign" "General Campaign" --apply
```

## DM discovery (Aug 2026 run)

`apollo-dm-waterfall` queues per SHEET ROW with no company dedupe, so pointing
it at a lane tab would enrich Compassus 380 times. `build_dm_worklist.py`
collapses KEEP rows to one per company first — 1,499 lookups saved — and
`sync_dm_results.py` fans the answers back out.

| Lane | Found | Note |
|---|---|---|
| Apollo waterfall | 248 | ~235 AMF credits, 58% hit |
| `rescue_dm_amf.py` (CEO only) | 75 | 146 credits, 42% of the misses |
| `rescue_dm_pm.py` (Purple Magic) | 7 | of 104; 67 were `pm_bad_title` |
| **Total** | **330 / 427 (77%)** | |

The shared companions do NOT cover this lane: `amf_dm_fallback.py` targets only
`not_found`, `amf_ceo_rescue.py` only `no_apollo_people` AND only TINY rows, and
`no_dm_candidates` — the largest bucket at 127 — is untouched by both.
`amf_ceo_then_ops.py` reaches them but falls back to `operations`, which replied
0/87; hence the CEO-only clone.

## Live campaigns (DRAFT, 2026-08-20)

| Campaign | ID | Leads |
|---|---|---|
| HireBase SLP - Aug 2026 | `8e6cbb7c-c13b-4536-9be7-f8ba607e9558` | 44 |
| HireBase General Healthcare - Aug 2026 | `92be979f-8be8-4f05-89b9-b78f6b32fb0c` | 280 |

324 leads, **324 distinct inboxes** — no one receives two emails.

**Mailboxes are attached BY TAG at create time** (Jude, 2026-08-20 — this
reverses the older "never attach" default). Four workspace tags plus
`match_lead_esp: true` and an ORDERED `provider_routing_rules` list whose
catch-all `all -> google` comes FIRST. Both values live at the top of
`push_campaign.py`; they differ from `push_florida_demand.py` (two tags added,
Zapmail dropped, different rule order), so copy from here. Campaigns are still
created as DRAFTs and Jude activates them.

Sequence: day 0/+2/+3/+4, blank subjects on 2-4 so they thread. Subject is
`{{firstName}}, still hiring?`. Step 3 carries the historical placement story,
because Jude's short opener dropped the identity line and proof that made
Florida's bumps work.

⚠️ **Custom variables persist merged FLAT into `payload`**, not under
`payload.custom_variables`. A GET that looks in the nested place reports them
missing when they are fine. Verify with a fresh GET, never the write response.

## Notes

- `data/` is gitignored: `domain_log.jsonl` and `class_log.jsonl` are durable
  judge logs, replayed on startup so a company is never re-billed across runs
  or lanes (the two lanes share 45 companies; the log saved 45 re-scrapes).
- Both lanes deliberately keep their **50 overlapping companies** — Jude's
  call on 2026-08-19 after being shown that those DMs will receive two
  different cold sequences.
- HireBase size bands are as unreliable as Indeed's brand headcount: Mercy Care
  is tagged "201 to 500" with **266 live postings**, Intermountain "11 to 50"
  with 98. The 500 cap passes organizations it was built to exclude.
- Needs `APIFY_API_TOKEN` (tier 2) and the Google OAuth token. No LLM API is
  called anywhere in this skill.
