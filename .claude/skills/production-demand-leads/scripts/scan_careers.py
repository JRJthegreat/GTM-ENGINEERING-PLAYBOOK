"""Careers-page scan: does a funded company have marketing/video roles open?

Direct HTTP against the company's own site (no Apify cost) — tries common
careers paths plus any career-ish link on the homepage, extracts text, and
GPT-4.1 lists open marketing/content/video roles. Found roles are written as
`video_job` signals (key careers:{cid}:{slug}) with `relevance` set inline
using the same STRONG/WEAK/IRRELEVANT ladder as filter_video_jobs.py, so
export scoring and stacking pick them up with zero extra plumbing.

Runs over BRAND companies that have a website and a funding signal (the pool
Jude asked to stack-check, 2026-08-18). Idempotent: companies with an existing
careers:* signal are skipped.

Usage: python3 -W ignore scan_careers.py [--limit N] [--all_brand]
"""
import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup
from openai import AzureOpenAI

from store import (APIFY_API_TOKEN, AZURE_API_KEY, AZURE_API_VERSION,
                   AZURE_DEPLOYMENT_FAST, AZURE_ENDPOINT, add_signal, connect)

WCC_SYNC = ("https://api.apify.com/v2/acts/apify~website-content-crawler/"
            "run-sync-get-dataset-items")

def careers_text_rendered(domain):
    """JS-rendering fallback via apify~website-content-crawler — Jude's call
    (2026-08-18): careers pages are mostly Greenhouse/Lever embeds that plain
    HTTP cannot see; the crawler renders them."""
    try:
        r = requests.post(
            WCC_SYNC, params={"token": APIFY_API_TOKEN},
            json={"startUrls": [{"url": f"https://{domain}/careers"},
                                {"url": f"https://{domain}/jobs"},
                                {"url": f"https://{domain}"}],
                  "maxCrawlPages": 4, "maxCrawlDepth": 1,
                  "includeUrlGlobs": [f"**{domain}**career**",
                                      f"**{domain}**job**",
                                      f"**{domain}**join**"],
                  "crawlerType": "playwright:adaptive",
                  "saveMarkdown": False, "maxResults": 4},
            timeout=300)
        if r.status_code not in (200, 201):
            return ""
        chunks = []
        for item in (r.json() or []):
            t = (item.get("text") or "").strip()
            if t and LINK_RE.search(item.get("url", "") + " " + t[:200]):
                chunks.append(t)
            elif t:
                chunks.append(t)
        return re.sub(r"\s+", " ", " ".join(chunks))[:8000]
    except requests.RequestException:
        return ""

HDRS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"}
PATHS = ["/careers", "/careers/", "/jobs", "/join-us", "/join", "/about/careers"]
LINK_RE = re.compile(r"career|jobs|join|hiring|we're hiring", re.I)

SYSTEM = """You read text from a company's careers/jobs page. List open roles
relevant to marketing/content/video and classify each:
  STRONG    - video/content production roles (video producer, videographer,
              content producer, creative producer, ad-creative strategist)
  WEAK      - marketing-adjacent (brand manager, social media manager with
              video duties, growth/marketing manager)
  IRRELEVANT- everything else (engineering, sales, ops, finance, interns)
Return STRICT JSON: {"roles": [{"title": "...", "relevance": "STRONG|WEAK"}]}
Include ONLY STRONG or WEAK roles (omit irrelevant ones). Empty list if none
or if the text clearly is not a careers page."""

def fetch(url):
    try:
        r = requests.get(url, headers=HDRS, timeout=12, allow_redirects=True)
        if r.status_code == 200 and "text/html" in r.headers.get("content-type", ""):
            return r.text
    except requests.RequestException:
        pass
    return ""

def page_text(html, cap=6000):
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ")).strip()[:cap]

def careers_text(domain):
    base = f"https://{domain}"
    for p in PATHS:
        txt = fetch(base + p)
        if txt and len(page_text(txt, 500)) > 100:
            return page_text(txt)
    home = fetch(base)
    if home:
        soup = BeautifulSoup(home, "html.parser")
        for a in soup.find_all("a", href=True):
            if LINK_RE.search(a.get_text(" ") or "") or LINK_RE.search(a["href"]):
                href = a["href"]
                if href.startswith("/"):
                    href = base + href
                if href.startswith("http"):
                    txt = fetch(href)
                    if txt:
                        return page_text(txt)
                break
    return ""

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--all_brand", action="store_true",
                    help="scan every BRAND company with a website, not just funded")
    ap.add_argument("--render", action="store_true",
                    help="render JS via apify~website-content-crawler (costs Apify usage)")
    ap.add_argument("--rescan", action="store_true",
                    help="also rescan companies with no prior careers hit")
    args = ap.parse_args()

    con = connect()
    q = ("SELECT DISTINCT c.id, c.name, c.website FROM companies c"
         " JOIN signals s ON s.company_id=c.id"
         " WHERE c.category='BRAND' AND c.website!=''")
    if not args.all_brand:
        q += " AND s.type='funding'"
    todo = []
    for cid, name, site in con.execute(q).fetchall():
        done = con.execute("SELECT 1 FROM signals WHERE company_id=? AND"
                           " type='video_job' AND key LIKE 'careers:%' LIMIT 1",
                           (cid,)).fetchone()
        if not done:
            dom = re.sub(r"^https?://", "", site).split("/")[0]
            todo.append((cid, name, dom.removeprefix("www.")))
    if args.limit:
        todo = todo[:args.limit]
    print(f"{len(todo)} companies to careers-scan")

    client = AzureOpenAI(azure_endpoint=AZURE_ENDPOINT, api_key=AZURE_API_KEY,
                         api_version=AZURE_API_VERSION)

    def work(cid, name, dom):
        txt = careers_text_rendered(dom) if args.render else careers_text(dom)
        if not txt:
            return cid, name, []
        try:
            resp = client.chat.completions.create(
                model=AZURE_DEPLOYMENT_FAST, temperature=0,
                response_format={"type": "json_object"},
                messages=[{"role": "system", "content": SYSTEM},
                          {"role": "user", "content": f"Company: {name}\n{txt}"}])
            roles = json.loads(resp.choices[0].message.content).get("roles", [])
            return cid, name, [r for r in roles
                               if r.get("relevance") in ("STRONG", "WEAK")][:4]
        except Exception:
            return cid, name, []

    found = scanned = 0
    with ThreadPoolExecutor(max_workers=10) as pool:
        futs = [pool.submit(work, *t) for t in todo]
        for f in as_completed(futs):
            cid, name, roles = f.result()
            scanned += 1
            for r in roles:
                slug = re.sub(r"[^a-z0-9]+", "-", r["title"].lower())[:40]
                if add_signal(con, cid, "video_job", f"careers:{cid}:{slug}", {
                        "job_title": r["title"],
                        "desc": "open role on company careers page",
                        "relevance": r["relevance"],
                        "relevance_reason": "careers-page scan",
                        "source": "careers_page"}):
                    found += 1
            if roles:
                print(f"  + {name}: " + "; ".join(
                    f"{r['title']} [{r['relevance']}]" for r in roles), flush=True)
            con.commit()
            if scanned % 50 == 0:
                print(f"  {scanned}/{len(todo)} scanned", flush=True)
    print(f"done: {scanned} scanned, {found} marketing/video roles found")

if __name__ == "__main__":
    sys.exit(main())
