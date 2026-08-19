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

NO RECIPIENT NICKNAME CASUALIZATION on this lane (Jude, 2026-08-19), the same
call he made on production-directory-leads: a stranger's cold email renaming
someone reads badly. William stays William. Only letter case is normalized.

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

COL_TITLE, COL_COMPANY, COL_CITY = 1, 10, 17
COL_FIRST, COL_BODY = 23, 25
COL_STATUS, COL_SEGMENT = 47, 48
BATCH = 10

# JUDE'S COPY, VERBATIM (2026-08-19). Only {first} / {role} / {role_plural} /
# {city} vary. Rebuilt from the proven Texas+Florida connector copy after the
# short "few candidates" draft was rejected — employers have heard "recruiter
# with candidates" so often they discount it on sight, and it is the bench
# claim the Florida generator already bans. What replaced it is a CAPABILITY
# claim plus risk reversal ("contingent, nothing owed unless the hire sticks"),
# which survives "send them over".
#
# ANCHORS ON ONE ROLE. The opener names a single role, so the awkward
# three-discipline sentence never has to exist. A company's dominant
# discipline (most live openings) wins; the rest inform priority, not copy.
#
# GEOGRAPHY-FREE, like Florida and unlike Texas ("a clinic here in Texas").
# This list spans 49 states, so any state-specific claim would be false in
# most inboxes.
BODY = """Hi {first},

Is the {role} role{in_city} still open?

I know a recruiter who specialises in placing {role_plural}. He only works on contingent, so nothing is owed unless the hire sticks.

If this hire is a priority right now, I'd be glad to make an intro."""

# Jude wrote "Best, Jude" inline at the end. It is NOT in BODY: the Instantly
# sequence already appends "Best,{{sendingAccountFirstName}}" + "Sent from my
# iPhone" to step 1, which on the Florida campaign rendered as "Rood" — so
# putting it here signs every email twice, under two different names. Pass
# --signoff to include it anyway, and then strip the signature from the
# sequence at push time instead.
SIGNOFF = "\n\nBest,\nJude"

# (regex, singular-with-article, plural). Order matters: a more specific
# family must precede one that would otherwise swallow it.
FAMILIES = [
    (r"board certified behavior analyst|\bbcba\b|behavior analyst",
     "a BCBA", "BCBAs"),
    # "Speech and Language Pathologist" is a common variant and must match:
    # without the optional "and" it fell through as a non-clinical posting.
    (r"speech[\s\-]*(and[\s\-]*)?language|speech patholog|speech therap|"
     r"\bslpa?\b", "a speech language pathologist",
     "speech language pathologists"),
    (r"occupational therap|\bcota\b|\bota\b",
     "an occupational therapist", "occupational therapists"),
    (r"physical therap|\bpta\b", "a physical therapist", "physical therapists"),
    (r"nurse practitioner|\bnp\b|pmhnp|\bfnp\b|\baprn\b|advanced practice",
     "a nurse practitioner", "nurse practitioners"),
    (r"physician assistant|\bpa-?c\b", "a physician assistant",
     "physician assistants"),
    (r"licensed practical nurse|licensed vocational nurse|\blpn\b|\blvn\b",
     "an LPN", "LPNs"),
    (r"certified nursing assistant|\bcna\b|nursing assistant",
     "a CNA", "CNAs"),
    (r"registered nurse|\brn\b|charge nurse|staff nurse",
     "a registered nurse", "registered nurses"),
    (r"social worker|\blcsw\b|\bmsw\b|\blmsw\b",
     "a social worker", "social workers"),
    (r"sonograph|ultrasound", "a sonographer", "sonographers"),
    (r"radiolog|rad tech|\bct tech|\bmri tech|x-?ray tech",
     "a radiologic technologist", "radiologic technologists"),
    (r"respiratory therap", "a respiratory therapist", "respiratory therapists"),
    (r"pharmacist", "a pharmacist", "pharmacists"),
    (r"medical assistant|\bcma\b", "a medical assistant", "medical assistants"),
    (r"dietit|nutritionist", "a dietitian", "dietitians"),
    (r"psychologist", "a psychologist", "psychologists"),
    (r"counselor|\blpc\b|mental health therap",
     "a counselor", "counselors"),
    (r"surgical tech", "a surgical technologist", "surgical technologists"),
    # \bmd\b is deliberately NOT here: titles carry state abbreviations
    # ("Registered Nurse - Towson, MD"), which would mint phantom Physicians.
    (r"\bphysician\b(?![\s\-]*assistant)", "a physician", "physicians"),
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


def first_name(name):
    """NO NICKNAME CASUALIZATION ON THIS LANE (Jude, 2026-08-19).

    The repo's standing rule casualizes recipient first names
    (William -> Will), but Jude dropped it here for the same reason he
    dropped it on production-directory-leads: a stranger's cold email
    renaming someone reads badly. William stays William.

    Case IS still normalized — AMF and Apollo return "SARAH" and "sarah" —
    because fixing shouting is formatting, not renaming. Names that are
    genuinely initials or already mixed-case (McKenzie, JoAnn) are left
    exactly as they came."""
    n = (name or "").strip()
    if not n:
        return ""
    n = n.split()[0]
    if n.isupper() or n.islower():
        return n.capitalize()
    return n


def families_of(title):
    t = (title or "").lower()
    if not t or NON_CLINICAL.search(t):
        return []          # checked FIRST: an admin post can still name a
                           # discipline ("Work Study ... Speech-Language
                           # Pathology Assistant to the Program Director")
    return [i for i, (rx, _, _) in enumerate(FAMILIES) if re.search(rx, t)]


def bare(sing):
    """'a bcba' -> 'BCBA' for the opener slot ("Is the BCBA role...")."""
    return re.sub(r"^(a|an) ", "", sing)


def dominant(titles):
    """The one discipline a company is most actively hiring for."""
    counts = Counter()
    for t in titles:
        for i in families_of(t):
            counts[i] += 1
    if not counts:
        return None
    i = counts.most_common(1)[0][0]
    return bare(FAMILIES[i][1]), FAMILIES[i][2]


def render_roles(titles, want_count=False):
    """Collapse a company's postings into at most three disciplines."""
    counts = Counter()
    for t in titles:
        for i in families_of(t):
            counts[i] += 1
    if not counts:
        return ("", 0) if want_count else ""
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
        out = parts[0]
    elif len(parts) == 2:
        out = f"{parts[0]}{joiner}{parts[1]}"
    else:
        out = f"{parts[0]}, {parts[1]}{joiner}{parts[2]}"
    return (out, len(parts)) if want_count else out


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
    ap.add_argument("--signoff", action="store_true",
                    help="Append 'Best,\\nJude'. OFF by default: the Instantly "
                         "sequence already signs step 1, so this double-signs "
                         "every email under two different names.")
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
                               "cities": [], "seg": c(r, COL_SEGMENT)})
        d["titles"].append(c(r, COL_TITLE))
        d["cities"].append(c(r, COL_CITY))
        d["rows"].append(i)
        d["first"] = d["first"] or c(r, COL_FIRST)

    made, skipped_norole, skipped_noname = {}, [], []
    for name, d in bycomp.items():
        dom = dominant(d["titles"])
        if not dom:
            skipped_norole.append(name)
            continue
        role, role_plural = dom
        first = first_name(d["first"])
        if not first:
            skipped_noname.append(name)
        # dominant city, so a multi-site company is anchored where the
        # hiring actually is rather than on whichever row sorted first
        city = ""
        cities = [c for c in d["cities"] if c]
        if cities:
            city = Counter(cities).most_common(1)[0][0]
        body = BODY.format(first=first or "{first}", role=role,
                           role_plural=role_plural,
                           in_city=f" in {city}" if city else "")
        made[name] = body + (SIGNOFF if args.signoff else "")

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
