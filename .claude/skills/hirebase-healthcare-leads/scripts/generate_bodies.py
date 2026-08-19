"""
Phase 4 — generate the email body. APPROVAL GATE: run --preview N and get
Jude's explicit go before a real run.

TEMPLATE IS JUDE'S, VERBATIM (2026-08-19). Only {first} and {roles} may vary.
Do not add a subject line, a sign-off, an em dash, or any sentence of your own:

    Hi {first},

    Are you hiring for {roles}?
    I know someone with a few candidates looking for new roles.

    Can I connect you?

Connector framing, as always for NEXAM: Jude is offering an introduction to a
specialist, never writing as the party delivering the service. No sign-off —
the sending account's signature carries identity.

THE ONLY REAL WORK IS {roles}, AND RAW TITLES CANNOT BE USED.
HireBase titles are per-posting and near-duplicate, so a straight join reads
like a scrape:

  "Speech Language Pathologist, Speech-Language Pathologist, Pediatric Speech
   Language Pathologist, Speech Language Pathologist Assistant, Float
   Speech-Language Pathologist"

All five are one discipline. Titles are therefore matched against DISCIPLINE
FAMILIES and collapsed, so that company asks "Are you hiring for Speech
Language Pathologists?". A single posting can name several disciplines
("Speech Language Pathologist, Occupational Therapist, or Physical
Therapist"), so every family a title matches is counted, not just the first.

Capped at 3 families, ordered by how many live postings each has. Listing six
disciplines reads like scraped data, not like someone who noticed.

Singular takes an article ("a Speech Language Pathologist"); more than one
posting in a family goes plural. Non-clinical postings (work-study,
administrative and office assistants) are dropped — they are not what the
specialist places.

Writes col Z. Batch-of-10, resume-safe: a row with Z already filled is
skipped unless --regenerate. One body per COMPANY, copied to that company's
rows, because the lead is the company (see assign_segments.py).

Run:
  python3 -W ignore generate_bodies.py --sheet_url URL --tab "SLP Campaign" --preview 15
  python3 -W ignore generate_bodies.py --sheet_url URL --tab "SLP Campaign" --apply
"""

import os
import re
import time
import argparse
from collections import Counter

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_PATH = os.path.join(SCRIPT_DIR, "..", "..", "..", "token.json")

COL_TITLE, COL_COMPANY = 1, 10
COL_FIRST, COL_BODY = 23, 25
COL_STATUS, COL_SEGMENT = 47, 48
BATCH = 10

TEMPLATE = """Hi {first},

Are you hiring for {roles}?
I know someone with a few candidates looking for new roles.

Can I connect you?"""

# Common nicknames only (shared rule — see the casualize-names skill).
NICKNAMES = {
    "William": "Will", "Michael": "Mike", "Christopher": "Chris",
    "Matthew": "Matt", "Daniel": "Dan", "Benjamin": "Ben",
    "Nicholas": "Nick", "Alexander": "Alex", "Jonathan": "Jon",
    "Timothy": "Tim", "Jeffrey": "Jeff", "Gregory": "Greg",
    "Joshua": "Josh", "Robert": "Rob", "Richard": "Rich",
    "Thomas": "Tom", "Kenneth": "Ken", "Joseph": "Joe",
    "Edward": "Ed", "Donald": "Don", "Ronald": "Ron",
    "Steven": "Steve", "Stephen": "Steve", "David": "Dave",
    "Douglas": "Doug", "Lawrence": "Larry", "Frederick": "Fred",
    "Raymond": "Ray", "Jennifer": "Jen", "Elizabeth": "Liz",
    "Katherine": "Kate", "Kathleen": "Kathy", "Stephanie": "Steph",
    "Samantha": "Sam", "Jacqueline": "Jackie", "Deborah": "Deb",
    "Pamela": "Pam", "Cynthia": "Cindy", "Rebecca": "Becca",
}

# (regex, singular-with-article, plural). Order matters: a more specific
# family must precede one that would otherwise swallow it.
FAMILIES = [
    (r"board certified behavior analyst|\bbcba\b|behavior analyst",
     "a BCBA", "BCBAs"),
    # "Speech and Language Pathologist" is a common variant and must match:
    # without the optional "and" it fell through as a non-clinical posting.
    (r"speech[\s\-]*(and[\s\-]*)?language|speech patholog|speech therap|"
     r"\bslpa?\b", "a Speech Language Pathologist",
     "Speech Language Pathologists"),
    (r"occupational therap|\bcota\b|\bota\b",
     "an Occupational Therapist", "Occupational Therapists"),
    (r"physical therap|\bpta\b", "a Physical Therapist", "Physical Therapists"),
    (r"nurse practitioner|\bnp\b|pmhnp|\bfnp\b|\baprn\b|advanced practice",
     "a Nurse Practitioner", "Nurse Practitioners"),
    (r"physician assistant|\bpa-?c\b", "a Physician Assistant",
     "Physician Assistants"),
    (r"licensed practical nurse|licensed vocational nurse|\blpn\b|\blvn\b",
     "an LPN", "LPNs"),
    (r"certified nursing assistant|\bcna\b|nursing assistant",
     "a CNA", "CNAs"),
    (r"registered nurse|\brn\b|charge nurse|staff nurse",
     "a Registered Nurse", "Registered Nurses"),
    (r"social worker|\blcsw\b|\bmsw\b|\blmsw\b",
     "a Social Worker", "Social Workers"),
    (r"sonograph|ultrasound", "a Sonographer", "Sonographers"),
    (r"radiolog|rad tech|\bct tech|\bmri tech|x-?ray tech",
     "a Radiologic Technologist", "Radiologic Technologists"),
    (r"respiratory therap", "a Respiratory Therapist", "Respiratory Therapists"),
    (r"pharmacist", "a Pharmacist", "Pharmacists"),
    (r"medical assistant|\bcma\b", "a Medical Assistant", "Medical Assistants"),
    (r"dietit|nutritionist", "a Dietitian", "Dietitians"),
    (r"psychologist", "a Psychologist", "Psychologists"),
    (r"counselor|\blpc\b|mental health therap",
     "a Counselor", "Counselors"),
    (r"surgical tech", "a Surgical Technologist", "Surgical Technologists"),
    # \bmd\b is deliberately NOT here: titles carry state abbreviations
    # ("Registered Nurse - Towson, MD"), which would mint phantom Physicians.
    (r"\bphysician\b(?![\s\-]*assistant)", "a Physician", "Physicians"),
]

NON_CLINICAL = re.compile(
    r"work[\s\-]?study|administrative assistant|office assistant|"
    r"teaching assistant|program director assistant|receptionist|"
    r"front desk|billing|scheduler|custodian|dishwasher|driver", re.I)


def sheet_id_of(url):
    return re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url).group(1)


def a1(col, row):
    s, c = "", col + 1
    while c:
        c, r = divmod(c - 1, 26)
        s = chr(65 + r) + s
    return f"{s}{row}"


def casual_first(name):
    n = (name or "").strip()
    if not n:
        return ""
    n = n.split()[0]
    return NICKNAMES.get(n.title(), n if n.isupper() else n.title())


def families_of(title):
    t = (title or "").lower()
    if not t or NON_CLINICAL.search(t):
        return []          # checked FIRST: an admin post can still name a
                           # discipline ("Work Study ... Speech-Language
                           # Pathology Assistant to the Program Director")
    return [i for i, (rx, _, _) in enumerate(FAMILIES) if re.search(rx, t)]


def render_roles(titles):
    """Collapse a company's postings into at most three disciplines."""
    counts = Counter()
    for t in titles:
        for i in families_of(t):
            counts[i] += 1
    if not counts:
        return ""
    top = [i for i, _ in counts.most_common(3)]
    # A single posting can name several disciplines ("Nurse Practitioner or
    # Physician Assistant"). That is ONE opening, so it stays singular and
    # joins with "or" — pluralising it would claim openings they do not have.
    single_posting = len(titles) == 1
    parts = [FAMILIES[i][1] if single_posting or counts[i] == 1 and len(top) == 1
             else FAMILIES[i][2] for i in top]
    if single_posting:
        parts = [p if k == 0 else re.sub(r"^(a|an) ", "", p)
                 for k, p in enumerate(parts)]
        joiner = " or "
    else:
        joiner = " and "
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]}{joiner}{parts[1]}"
    return f"{parts[0]}, {parts[1]}{joiner}{parts[2]}"


def write(svc, sid, data, tries=4):
    for n in range(tries):
        try:
            return svc.spreadsheets().values().batchUpdate(
                spreadsheetId=sid,
                body={"valueInputOption": "RAW", "data": data}).execute()
        except Exception as e:
            if "429" not in str(e) or n == tries - 1:
                raise
            time.sleep(20 * (n + 1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet_url", required=True)
    ap.add_argument("--tab", required=True)
    ap.add_argument("--preview", type=int, default=0)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--regenerate", action="store_true")
    ap.add_argument("--no_casual", action="store_true",
                    help="Do not shorten first names (William->Will).")
    args = ap.parse_args()

    svc = build("sheets", "v4",
                credentials=Credentials.from_authorized_user_file(TOKEN_PATH))
    sid = sheet_id_of(args.sheet_url)
    vals = svc.spreadsheets().values().get(
        spreadsheetId=sid, range=f"'{args.tab}'!A1:BA").execute().get("values", [])
    rows = vals[1:]

    def c(r, i):
        return r[i].strip() if i < len(r) else ""

    bycomp = {}
    for i, r in enumerate(rows):
        if c(r, COL_STATUS) != "KEEP" or not c(r, COL_COMPANY):
            continue
        d = bycomp.setdefault(c(r, COL_COMPANY),
                              {"titles": [], "rows": [], "first": "",
                               "seg": c(r, COL_SEGMENT)})
        d["titles"].append(c(r, COL_TITLE))
        d["rows"].append(i)
        d["first"] = d["first"] or c(r, COL_FIRST)

    made, skipped_norole, skipped_noname = {}, [], []
    for name, d in bycomp.items():
        roles = render_roles(d["titles"])
        if not roles:
            skipped_norole.append(name)
            continue
        first = d["first"] if args.no_casual else casual_first(d["first"])
        if not first:
            skipped_noname.append(name)
        made[name] = TEMPLATE.format(first=first or "{first}", roles=roles)

    print(f"[{args.tab}] {len(bycomp)} companies")
    print(f"  bodies rendered      : {len(made)}")
    print(f"  no clinical role     : {len(skipped_norole)}  {skipped_norole[:4]}")
    print(f"  awaiting a DM name   : {len(skipped_noname)}")

    if args.preview:
        seen, shown = set(), 0
        for seg in ("SINGLE", "MULTI_ONE_CITY", "MULTI_CITY"):
            print(f"\n{'='*66}\n{seg}\n{'='*66}")
            for name, d in bycomp.items():
                if d["seg"] != seg or name in seen or name not in made:
                    continue
                if shown >= args.preview:
                    break
                seen.add(name)
                shown += 1
                print(f"\n--- {name}  ({len(d['rows'])} openings) ---")
                print(made[name])
            shown = 0
        return

    if not args.apply:
        print("\n  (no --apply and no --preview — nothing written)")
        return

    pending, n = [], 0
    for name, body in made.items():
        if not bycomp[name]["first"]:
            continue                      # never send "Hi {first},"
        for ri in bycomp[name]["rows"]:
            if c(rows[ri], COL_BODY) and not args.regenerate:
                continue
            pending.append({"range": f"'{args.tab}'!{a1(COL_BODY, ri + 2)}",
                            "values": [[body]]})
        n += 1
        if n % BATCH == 0 and pending:
            write(svc, sid, pending)
            print(f"  written {n}/{len(made)} companies")
            pending = []
    if pending:
        write(svc, sid, pending)
    print(f"  done — {n} companies")


if __name__ == "__main__":
    main()
