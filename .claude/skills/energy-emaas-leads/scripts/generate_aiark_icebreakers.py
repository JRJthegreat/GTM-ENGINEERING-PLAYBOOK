"""
Generate a "Love X" icebreaker + company-type phrase per company, from the
AI Ark description already on the Campaign sheet. No scraping.

Model is Azure OpenAI GPT-5.1 — the repo's sanctioned GENERATION model
(Opus/Sonnet -> GPT-5.1). NOT GPT-4.1, which Jude distrusts for classification;
this is generation, a different job. The ANTHROPIC_API_KEY in .env is dead
(401), so Claude-via-API is unavailable. Strict grounding: both specifics must
come from the description, and a validation pass rejects any 4-digit year not
present in the source (the failure mode is an invented founding date). A
company whose blurb yields nothing usable gets a blank icebreaker; the body
step opens on the bridge line instead of faking one.

Formula (Jude's locked v3): "Love {specific 1}, {light compliment}. Btw, also
saw {specific 2}." Brand names lowercased. Credits something true, safe if
slightly wrong, no "must", no mission/values praise.

Per unique company; the result is written to every row of that company.
Batch-of-10 writes, resume-safe (skips rows whose icebreaker/type are filled).
"""

import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(SCRIPT_DIR, "..", "..", "..", ".env"))
TOKEN = os.path.join(SCRIPT_DIR, "..", "..", "..", "token.json")
TAB = "Campaign"
AZ_EP = os.environ["AZURE_OPENAI_ENDPOINT"]
AZ_KEY = os.environ["AZURE_OPENAI_API_KEY"]
AZ_DEP = os.environ["AZURE_OPENAI_DEPLOYMENT"]
AZ_VER = os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21")
AZ_URL = f"{AZ_EP}/openai/deployments/{AZ_DEP}/chat/completions?api-version={AZ_VER}"

C_COMPANY, C_INDUSTRY, C_DESC, C_TYPE, C_ICE = 3, 7, 8, 9, 10

SYS = """You write one cold-email icebreaker for an Australian food or beverage \
company, from its own description. Output STRICT JSON only.

Formula, exactly:
  "Love {specific 1}, {light compliment}. Btw, also saw {specific 2}."

Rules:
- Both specifics must be concrete facts FROM THE DESCRIPTION (a founding year,
  a origin story, a place, a flagship product, a generations/family fact, a
  named milestone). Never invent a fact, a date or a number.
- Lowercase the company/brand names inside the line, on purpose.
- Credit something true and be safe if slightly wrong. No "must". No praise of
  mission, values, culture, or "commitment to quality" - those are banned.
- UNDERSTATED. The word "Love" at the start is the only enthusiasm allowed.
  Ban these and anything like them: "cool", "such a", "really", "amazing",
  "awesome", "love how", "great bit of", "solid". The compliment is a plain,
  dry observation, not a gush. The "Btw, also saw {specific 2}" just states the
  second fact flatly, no adjectives piled on.
- Prefer specifics in this order: founding year / origin story, a flagship
  product, a generations-or-family fact, a named milestone. AVOID using
  head-office locations, which office cities they have, or corporate ownership
  structure ("part of X group") as a specific - those read as surveillance or
  are not real compliments.
- Keep it to the two sentences of the formula. Tight.
- If the description gives only ONE solid concrete fact, drop the "Btw" sentence
  and give a one-specific line: "Love {specific}, {light compliment}."
- If the description is empty or has no concrete fact (only generic marketing),
  return an empty string for the icebreaker.

Also return "company_type": a short plural noun phrase for what they make, for
the sentence "I spend a lot of time around {company_type} in {state}". Examples:
"dairy producers", "chocolate makers", "beverage producers", "commercial
bakeries", "meat processors", "pet food manufacturers". Lowercase.

Return: {"icebreaker": "...", "company_type": "..."}"""


def gen(company, industry, desc):
    r = requests.post(
        AZ_URL, headers={"api-key": AZ_KEY, "Content-Type": "application/json"},
        json={"messages": [
            {"role": "system", "content": SYS},
            {"role": "user", "content":
             f"Company: {company}\nIndustry: {industry}\n"
             f"Description:\n{desc[:1200]}"}],
              "max_completion_tokens": 500}, timeout=90)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    text = r.json()["choices"][0]["message"]["content"].strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    try:
        d = json.loads(text)
    except ValueError:
        return "", ""
    ice = (d.get("icebreaker") or "").strip()
    ctype = (d.get("company_type") or "").strip()
    # Grounding guard: any 4-digit year in the line must be in the description.
    for yr in set(re.findall(r"\b(1[89]\d\d|20\d\d)\b", ice)):
        if yr not in desc:
            return "", ctype
    return ice, ctype


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet_url", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()

    sid = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", args.sheet_url).group(1)
    svc = build("sheets", "v4",
                credentials=Credentials.from_authorized_user_file(TOKEN))
    vals = svc.spreadsheets().values().get(
        spreadsheetId=sid, range=f"{TAB}!A2:N2000").execute().get("values", [])

    def cell(r, i):
        return r[i].strip() if len(r) > i and r[i] else ""

    # one representative row per company (needs a description)
    by_company = {}
    for r in vals:
        co = cell(r, C_COMPANY)
        if co and cell(r, C_DESC) and co not in by_company:
            by_company[co] = (cell(r, C_INDUSTRY), cell(r, C_DESC))
    todo = list(by_company.items())
    if args.limit:
        todo = todo[:args.limit]
    print(f"{len(todo)} companies to generate")

    result = {}
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(gen, co, ind, desc): co
                for co, (ind, desc) in todo}
        done = 0
        for f in as_completed(futs):
            co = futs[f]
            try:
                result[co] = f.result()
            except Exception as e:
                print(f"  [!] {co}: {type(e).__name__}")
                result[co] = ("", "")
            done += 1
            if done % 25 == 0:
                print(f"  {done}/{len(todo)}", flush=True)

    # write back to every row of each company, batch-of-10
    pending = []

    def flush():
        if pending:
            svc.spreadsheets().values().batchUpdate(
                spreadsheetId=sid,
                body={"valueInputOption": "RAW", "data": list(pending)}).execute()
            pending.clear()

    n_ice = n_blank = 0
    for i, r in enumerate(vals, start=2):
        co = cell(r, C_COMPANY)
        if co not in result:
            continue
        if cell(r, C_ICE) and not args.overwrite:
            continue
        ice, ctype = result[co]
        pending.append({"range": f"{TAB}!J{i}:K{i}", "values": [[ctype, ice]]})
        if ice:
            n_ice += 1
        else:
            n_blank += 1
        if len(pending) >= 10:
            flush()
    flush()
    print(f"done. rows with icebreaker: {n_ice}, blank (bridge-only): {n_blank}")


if __name__ == "__main__":
    sys.exit(main())
