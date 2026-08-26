"""
Phase 4 - Texas candidate-angle campaign (Aug 2026): generate the email body.

CLONE of generate_texas_demand.py for the NEW campaign on sheet
"Healthcare Texas Indeed Leads - Aug 2026" (tab: Leads, standard 29-col base
layout). The July scripts (generate_texas_demand.py / push_texas_demand.py)
feed the July campaign and are NOT touched — clone, don't retrofit.

Copy approved by Jude 2026-08-26 (experience slots removed same day). The
candidate-forward angle: no proof story, no timing check — straight to
"I know a recruiter who has a few {role_plural} looking for new roles."

Rules baked in (standing):
- NO sign-off in the body (the Instantly sequence appends it).
- NO em dashes anywhere.
- Recipient nickname casualization IS ON for this lane (Jude, 2026-08-26:
  "use the casualize skill") — canonical rules from casualize-names, applied
  at generation time via the shared NICKNAMES map. Letter case normalized too.
- Batch-of-10 writes, resume-safe (rows with AD cleaned_role filled are
  skipped; clear AD+Z to regenerate a row).

Columns (0-based, verified against the Aug 2026 sheet):
  B=1 title  J=9 desc  K=10 company  M=12 size  R=17 city  S=18 state
  U=20 dm_title  W=22 email  X=23 first  Z=25 body  AB=27 dm_status
  AD-AH (29-33) audit: cleaned_role, role_plural, role_short_plural,
  casual_first, gen_status

Usage:
  python3 -W ignore generate_texas_candidate.py --sheet_url "URL" --preview 10
  python3 -W ignore generate_texas_candidate.py --sheet_url "URL" --limit 20
  python3 -W ignore generate_texas_candidate.py --sheet_url "URL"
"""

import os
import re
import json
import argparse
import threading
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(SCRIPT_DIR, "..", "..", "..", ".env"))
TOKEN_PATH = os.path.join(SCRIPT_DIR, "..", "..", "..", "token.json")

AZURE_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
AZURE_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21")
AZURE_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT_FAST", "gpt-4.1")

BATCH = 10
WORKERS = 8

# Subject is applied at push time via the {{cleaned_role}} custom variable.
SUBJECT = "{{firstName}}, the {{cleaned_role}} hire"

# --- Column indices (0-based). Standard 29-col base sheet, tab "Leads".
C_TITLE, C_DESC, C_COMPANY, C_SIZE = 1, 9, 10, 12
C_CITY, C_STATE = 17, 18
C_DM_TITLE, C_EMAIL, C_FIRST = 20, 22, 23
C_BODY, C_STATUS = 25, 27
C_ROLE, C_RPLUR, C_RSHORT, C_CFIRST, C_GSTAT = 29, 30, 31, 32, 33
AUDIT_HDRS = ["cleaned_role", "role_plural", "role_short_plural",
              "casual_first", "gen_status"]

# ---------------------------------------------------------------- copy ----
# Jude's template verbatim (approved 2026-08-26). Only the marked slots and
# the computed a/an article may change.
BODY = """Hi {first},

Saw that you posted {article} {cleaned_role} role on Indeed.

I know a recruiter who has a few {role_short_plural} looking for new roles.

If relevant, happy to connect you directly with him for more details."""
# NO SIGN-OFF. The Instantly sequence appends the signature; adding one here
# signs every email twice.

REQUIRED = [
    "role on Indeed.",
    "I know a recruiter who has a few",
    "looking for new roles.",
    "If relevant, happy to connect you directly with him for more details.",
]
FORBIDDEN = ["Best,", "Sent from my iPhone", "—"]

SYSTEM = """You extract three short slots for a cold email to a healthcare employer
that posted a job. Reply with ONLY JSON. No prose.

{"cleaned_role": "...", "role_plural": "...", "role_short_plural": "..."}

cleaned_role
  How a person would SAY this job in conversation. A bare noun phrase.
  - NEVER include the words role, position, opening, job, vacancy, hire.
    (The email says "a {cleaned_role} role", so "CT technologist role" would
    render as "role role".)
  - NEVER include seniority/shift/employment cruft: PRN, Full-Time, Part-Time,
    Sign-on Bonus, Hybrid, Remote, I/II/III, Senior, roman numerals, dashes,
    parentheses, location names, department codes.
  - Keep real acronyms UPPERCASE (NP, PA, RN, CT, MRI, SLP, OT, PT, LVN, CRNA,
    EMT, APRN). Everything else sentence case, not Title Case.
  - Max 5 words. Prefer how a clinician would say it.
  Examples:
    "Radiologic Technologist (CT) - PRN Nights"        -> "CT technologist"
    "Speech-Language Pathologist (SLP)"                -> "speech-language pathologist"
    "Advanced Practice Nurse Practitioner"             -> "nurse practitioner"
    "Physical Therapist II - Outpatient Ortho"         -> "orthopedic physical therapist"

role_plural
  Plural of cleaned_role, same casing rules. "CT technologists", "NPs",
  "speech-language pathologists". Must actually be plural.

role_short_plural
  The SHORTENED or paraphrased plural — how a recruiter would say the role
  out loud in one breath. Abbreviate the credential to its standard acronym
  and KEEP any specialty modifier. Plural, same casing rules.
  Examples:
    "psychiatric nurse practitioners"   -> "psychiatric NPs"
    "nurse practitioners"               -> "NPs"
    "physician assistants"              -> "PAs"
    "speech-language pathologists"      -> "SLPs"
    "orthopedic physical therapists"    -> "orthopedic PTs"
    "occupational therapists"           -> "OTs"
    "CT technologists"                  -> "CT techs"
    "radiologic technologists"          -> "rad techs"
    "operating room registered nurses"  -> "OR RNs"
  If there is no natural shorter form (e.g. "dental hygienists",
  "pharmacists"), return the plural unchanged. Never invent an acronym.
"""

ACRONYMS = {"NP", "PA", "RN", "CT", "MRI", "SLP", "OT", "PT", "LVN", "LPN",
            "CRNA", "EMT", "APRN", "CNA", "ICU", "ER", "OR", "CMA", "RT",
            "MLS", "MLT", "LMSW", "LCSW", "BCBA", "DPT", "NICU", "PACU"}

CRUFT = re.compile(
    r"\b(prn|per diem|full[- ]?time|part[- ]?time|contract|travel|temp|"
    r"sign[- ]?on|bonus|hybrid|remote|on[- ]?site|onsite|days?|nights?|"
    r"weekend|shift|new grad|senior|sr|junior|jr|lead|i{1,3}|iv|v)\b", re.I)

NICKNAMES = {
    "william": "Will", "robert": "Rob", "richard": "Rich", "michael": "Mike",
    "christopher": "Chris", "matthew": "Matt", "daniel": "Dan", "david": "Dave",
    "james": "Jim", "joseph": "Joe", "thomas": "Tom", "charles": "Charlie",
    "anthony": "Tony", "steven": "Steve", "stephen": "Steve", "andrew": "Andy",
    "kenneth": "Ken", "joshua": "Josh", "timothy": "Tim", "edward": "Ed",
    "jeffrey": "Jeff", "gregory": "Greg", "benjamin": "Ben", "samuel": "Sam",
    "patricia": "Pat", "jennifer": "Jen", "elizabeth": "Liz", "katherine": "Kate",
    "kathleen": "Kathy", "margaret": "Maggie", "deborah": "Deb", "rebecca": "Becca",
    "jacqueline": "Jackie", "alexandra": "Alex", "alexander": "Alex",
    "nicholas": "Nick", "jonathan": "Jon", "zachary": "Zach", "victoria": "Vicki",
    "pamela": "Pam", "cynthia": "Cindy", "sandra": "Sandy", "theodore": "Ted",
    "raymond": "Ray", "lawrence": "Larry", "ronald": "Ron", "donald": "Don",
    "douglas": "Doug", "frederick": "Fred", "leonard": "Len", "vincent": "Vince",
}

VOWEL_SOUND_LETTERS = set("AEFHILMNORSX")


def casual_first(name):
    """Casualize-names skill rules: common nickname if one exists, else the
    name with letter case normalized (Jude enabled nicknames on this lane,
    2026-08-26)."""
    n = (name or "").strip().split()[0] if (name or "").strip() else ""
    if not n:
        return ""
    return NICKNAMES.get(n.lower(),
                         n if n[:1].isupper() and not n.isupper() else n.title())


def article_for(role):
    w = (role or "").split()[0] if (role or "").strip() else ""
    if not w:
        return "a"
    if w.rstrip("s").upper() in ACRONYMS or w.isupper():
        return "an" if w[0].upper() in VOWEL_SOUND_LETTERS else "a"
    return "an" if w[0].lower() in "aeiou" else "a"


def idx_to_col(i):
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def tidy_role(s, fallback):
    s = (s or "").strip()
    s = re.sub(r"\s*[\(\[].*?[\)\]]\s*", " ", s)
    s = re.sub(r"[^A-Za-z0-9\-/ ]+", " ", s)
    s = re.sub(r"\b(role|position|opening|job|vacancy|hire)s?\b", " ", s, flags=re.I)
    s = CRUFT.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip(" -/")
    if not s or len(s.split()) > 6:
        s = re.sub(r"\s+", " ", CRUFT.sub(" ", (fallback or ""))).strip(" -/")
    words = []
    for w in s.split():
        if w.endswith(("s", "S")) and w[:-1].upper() in ACRONYMS:
            words.append(w[:-1].upper() + "s")
        elif w.upper() in ACRONYMS and not (w.lower() in ("or", "and") and not w.isupper()):
            words.append(w.upper())
        else:
            words.append(w.lower())
    return " ".join(words)[:60].strip()


def pluralize(s):
    if not s:
        return s
    head = s.split()
    last = head[-1]
    if last.upper() in ACRONYMS:
        head[-1] = last + "s"
    elif re.search(r"(s|x|z|ch|sh)$", last, re.I):
        head[-1] = last + "es"
    elif re.search(r"[^aeiou]y$", last, re.I):
        head[-1] = last[:-1] + "ies"
    else:
        head[-1] = last + "s"
    return " ".join(head)


def get_vars(client, lead):
    desc = " ".join((lead["desc"] or "").split())[:5000]
    user = (f'Company: "{lead["company"]}"\n'
            f'Job title: "{lead["job_title"]}"\n\n'
            f'JOB POSTING:\n{desc}')
    try:
        resp = client.chat.completions.create(
            model=AZURE_DEPLOYMENT, max_tokens=250, temperature=0,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": SYSTEM},
                      {"role": "user", "content": user}])
        v = json.loads(resp.choices[0].message.content)
    except Exception as e:
        print(f"  [!] LLM {lead['company']}: {type(e).__name__}")
        return None

    notes = []

    role = tidy_role(v.get("cleaned_role"), lead["job_title"])
    if not role:
        return None
    v["cleaned_role"] = role

    rp = tidy_role(v.get("role_plural"), "")
    if not rp or rp == role or len(rp.split()) > 6:
        rp = pluralize(role)
        notes.append("plural_derived")
    v["role_plural"] = rp

    rs = tidy_role(v.get("role_short_plural"), "")
    if not rs or len(rs.split()) > 5:
        rs = rp
        notes.append("short_fallback")
    v["role_short_plural"] = rs

    v["casual_first"] = casual_first(lead["first"]) or "there"
    v["gen_status"] = "ok"
    v["notes"] = ",".join(notes) or "ok"
    return v


def build_body(v):
    body = BODY.format(first=v["casual_first"],
                       article=article_for(v["cleaned_role"]),
                       cleaned_role=v["cleaned_role"],
                       role_plural=v["role_plural"],
                       role_short_plural=v["role_short_plural"])
    body = body.replace("—", ", ").replace("–", "-")
    return body


def verify(body):
    if "{" in body or "}" in body:
        return "unfilled_slot"
    for s in REQUIRED:
        if s not in body:
            return "fixed_copy_broken"
    if re.search(r"\brole role\b|  ", body):
        return "render_artifact"
    for f in FORBIDDEN:
        if f in body:
            return "body_contains_signoff"
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet_url", required=True)
    ap.add_argument("--tab", default="Leads")
    ap.add_argument("--limit", type=int, default=0, help="0 = all eligible")
    ap.add_argument("--preview", type=int, default=0,
                    help="Render N examples, write nothing")
    args = ap.parse_args()
    TAB = args.tab

    sheet_id = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", args.sheet_url).group(1)
    creds = Credentials.from_authorized_user_file(TOKEN_PATH)
    svc = build("sheets", "v4", credentials=creds)

    rows = svc.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=f"'{TAB}'!A1:AZ").execute().get("values", [])
    header, data = rows[0], rows[1:]

    def cell(r, i):
        return r[i].strip() if i < len(r) and r[i] else ""

    if not args.preview:
        meta = svc.spreadsheets().get(spreadsheetId=sheet_id).execute()
        sh = next(s for s in meta["sheets"] if s["properties"]["title"] == TAB)
        have = sh["properties"]["gridProperties"]["columnCount"]
        if have < C_GSTAT + 1:
            svc.spreadsheets().batchUpdate(spreadsheetId=sheet_id, body={
                "requests": [{"appendDimension": {
                    "sheetId": sh["properties"]["sheetId"], "dimension": "COLUMNS",
                    "length": C_GSTAT + 1 - have}}]}).execute()
        svc.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=f"'{TAB}'!{idx_to_col(C_ROLE)}1:{idx_to_col(C_GSTAT)}1",
            valueInputOption="RAW", body={"values": [AUDIT_HDRS]}).execute()

    todo, skipped = [], {"no_email": 0, "done": 0}
    for i, r in enumerate(data):
        if not cell(r, C_EMAIL):
            skipped["no_email"] += 1
            continue
        if cell(r, C_ROLE):
            skipped["done"] += 1
            continue
        todo.append({"row": i + 2, "company": cell(r, C_COMPANY),
                     "job_title": cell(r, C_TITLE), "desc": cell(r, C_DESC),
                     "size": cell(r, C_SIZE), "city": cell(r, C_CITY),
                     "state": cell(r, C_STATE), "dm_title": cell(r, C_DM_TITLE),
                     "first": cell(r, C_FIRST) or cell(r, 19)})
        if args.limit and len(todo) >= args.limit:
            break

    print(f"tab={TAB}  eligible={len(todo)}  "
          f"(skipped: {skipped['no_email']} no email, "
          f"{skipped['done']} already generated)")
    if not todo:
        return

    from openai import AzureOpenAI
    client = AzureOpenAI(azure_endpoint=AZURE_ENDPOINT, api_key=AZURE_API_KEY,
                         api_version=AZURE_API_VERSION)

    if args.preview:
        picks = todo[:args.preview]
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            out = list(ex.map(lambda t: (t, get_vars(client, t)), picks))
        for t, v in out:
            if not v:
                print(f"row {t['row']} | {t['company']}: LLM FAILED")
                continue
            print("=" * 74)
            print(f"row {t['row']} | {t['company']} | size {t['size'] or '?'} "
                  f"| DM {t['dm_title'] or '(none)'}")
            print(f"  raw title : {t['job_title']}")
            print(f"  slots     : role={v['cleaned_role']!r} plural={v['role_plural']!r} "
                  f"short={v['role_short_plural']!r}")
            print(f"  notes     : {v['notes']}")
            if v["gen_status"] != "ok":
                print(f"  SKIPPED   : {v['gen_status']}")
                continue
            body = build_body(v)
            err = verify(body)
            if err:
                print(f"  ** {err}")
            print(f"SUBJECT: {SUBJECT}")
            print("-" * 74)
            print(body)
            print()
        return

    lock = threading.Lock()
    done = 0

    def process(t):
        v = get_vars(client, t)
        if not v:
            return None
        audit = [v["cleaned_role"], v["role_plural"], v["role_short_plural"],
                 v.get("casual_first", ""), v["gen_status"]]
        if v["gen_status"] != "ok":
            return (t["row"], None, audit)
        body = build_body(v)
        err = verify(body)
        if err:
            with lock:
                print(f"  [!] row {t['row']} {t['company']}: {err} - SKIPPED")
            audit[-1] = err
            return (t["row"], None, audit)
        return (t["row"], body, audit)

    for start in range(0, len(todo), BATCH):
        chunk = todo[start:start + BATCH]
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            results = [r for r in ex.map(process, chunk) if r]
        upd = []
        for row, body, vals in results:
            if body:
                upd.append({"range": f"'{TAB}'!{idx_to_col(C_BODY)}{row}",
                            "values": [[body]]})
            upd.append({"range": (f"'{TAB}'!{idx_to_col(C_ROLE)}{row}:"
                                  f"{idx_to_col(C_GSTAT)}{row}"),
                        "values": [vals]})
        if upd:
            svc.spreadsheets().values().batchUpdate(
                spreadsheetId=sheet_id,
                body={"valueInputOption": "RAW", "data": upd}).execute()
        done += len(chunk)
        print(f"  -- batch written ({done}/{len(todo)})")
    print("Done.")


if __name__ == "__main__":
    main()
