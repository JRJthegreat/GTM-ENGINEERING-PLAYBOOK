"""Signal collector: new marketing-exec announcements (harvestapi~linkedin-post-search).

A new Head of Brand / VP Marketing in their first 90 days has a mandate and no
locked-in vendors. Quoted announcement phrases (memory: unquoted relevance
matching returns junk), then GPT-4.1 extracts (company, role, person) from each
candidate post. The person IS the DM — name/title/LinkedIn land in the signal
detail and the exporter writes them to T/U/V.

Usage: python3 -W ignore collect_new_execs.py [--max_posts 40] [--dry_run]
"""
import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from openai import AzureOpenAI

from store import (APIFY_API_TOKEN, AZURE_API_KEY, AZURE_API_VERSION,
                   AZURE_DEPLOYMENT_FAST, AZURE_ENDPOINT, add_signal, connect,
                   upsert_company)

URL = ("https://api.apify.com/v2/acts/harvestapi~linkedin-post-search/"
       "run-sync-get-dataset-items")
QUERIES = [
    '"excited to announce" "Head of Brand"',
    '"excited to share" "VP of Marketing"',
    '"joined" "as VP of Marketing"',
    '"new role" "Head of Marketing"',
    '"thrilled to share" "Chief Marketing Officer"',
    '"excited to share" "Director of Brand"',
    '"excited to announce" "Chief Marketing Officer"',
    '"new role" "VP of Brand"',
    '"excited to share" "Head of Brand"',
    '"joined" "as CMO"',
    '"thrilled to announce" "VP of Marketing"',
    '"starting a new position" "Head of Marketing"',
]
JOIN_RE = re.compile(r"joining|joined|new role|new chapter|next chapter|"
                     r"excited to announce|thrilled to (share|announce)|"
                     r"start(ing)? (as|my|a new)", re.I)
TITLE_RE = re.compile(r"head of brand|head of marketing|vp,? (of )?marketing|"
                      r"vice president,? (of )?marketing|chief marketing|cmo\b|"
                      r"brand director|director of brand", re.I)

EXTRACT_SYSTEM = """You read a LinkedIn post announcing someone starting a new marketing leadership role.
Return STRICT JSON: {"is_join": bool, "company": str, "role": str, "person": str}
is_join is true ONLY if the AUTHOR is announcing THEIR OWN new marketing/brand
leadership position at a named company (not a job seeker, not an event recap,
not congratulating someone else, not a company announcement). company = the
employer they are joining (empty if unclear). role = their new title.
person = the author's name as given."""

def extract(client, author, content):
    try:
        resp = client.chat.completions.create(
            model=AZURE_DEPLOYMENT_FAST, temperature=0,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": EXTRACT_SYSTEM},
                      {"role": "user",
                       "content": f"Author: {author}\nPost:\n{content[:1200]}"}])
        return json.loads(resp.choices[0].message.content)
    except Exception as e:
        print(f"  [!] extract failed for {author}: {type(e).__name__}", flush=True)
        return {"is_join": False}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max_posts", type=int, default=40)
    ap.add_argument("--dataset_id", default="",
                    help="reuse an existing Apify dataset instead of re-running the actor")
    ap.add_argument("--dry_run", action="store_true")
    args = ap.parse_args()

    print(f"{len(QUERIES)} queries × maxPosts {args.max_posts}")
    if args.dry_run:
        for q in QUERIES:
            print(f"  {q}")
        return

    if args.dataset_id:
        r = requests.get(
            f"https://api.apify.com/v2/datasets/{args.dataset_id}/items",
            params={"token": APIFY_API_TOKEN, "clean": 1}, timeout=300)
    else:
        r = requests.post(URL, params={"token": APIFY_API_TOKEN},
                          json={"searchQueries": QUERIES, "maxPosts": args.max_posts,
                                "sortBy": "date", "postedLimit": "month"},
                          timeout=600)
    if r.status_code not in (200, 201):
        print(f"HTTP {r.status_code}: {r.text[:300]}")
        return 1
    items = r.json() or []
    cands = []
    seen_urls = set()
    for it in items:
        a = it.get("author") or {}
        content = it.get("content") or ""
        url = it.get("linkedinUrl") or ""
        if url in seen_urls or a.get("type") != "profile":
            continue
        seen_urls.add(url)
        if JOIN_RE.search(content) and TITLE_RE.search(
                content + " " + (a.get("info") or "")):
            cands.append((a, content, url, (it.get("postedAt") or {}).get("date", "")))
    print(f"{len(items)} posts → {len(cands)} regex candidates; extracting...")

    client = AzureOpenAI(azure_endpoint=AZURE_ENDPOINT, api_key=AZURE_API_KEY,
                         api_version=AZURE_API_VERSION)
    con = connect()
    kept = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(extract, client, c[0].get("name", ""), c[1]): c
                for c in cands}
        for f in as_completed(futs):
            a, content, url, posted = futs[f]
            v = f.result()
            if not (v.get("is_join") and (v.get("company") or "").strip()):
                continue
            cid = upsert_company(con, v["company"].strip())
            if cid is None:
                continue
            if add_signal(con, cid, "new_exec", url, {
                    "person": v.get("person") or a.get("name", ""),
                    "role": v.get("role", ""),
                    "author_linkedin": (a.get("linkedinUrl") or "").split("?")[0],
                    "headline": a.get("info", ""),
                    "post_url": url,
                    "snippet": content[:300],
            }, posted[:10]):
                kept += 1
            con.commit()
    con.commit()
    print(f"done: {kept} new_exec signals stored")

if __name__ == "__main__":
    sys.exit(main())
