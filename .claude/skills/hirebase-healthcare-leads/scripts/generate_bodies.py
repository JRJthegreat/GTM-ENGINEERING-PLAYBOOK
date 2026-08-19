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
COL_DM_NAME = 19
COL_STATUS, COL_SEGMENT = 47, 48
COL_PUSH = 53   # BB — which lane owns the single send
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
# ONE role — Jude's copy exactly. The role appearing twice ("the BCBA role"
# ... "placing BCBAs") is fine here; it reinforces rather than repeats, and it
# is how the proven Florida copy reads.
BODY_ONE = """Hi {first},

Is the {role} role{in_city} still open?

I know a recruiter who specialises in placing {role_plural}. He only works on contingent, so nothing is owed unless the hire sticks.

If this hire is a priority right now, I'd be glad to make an intro."""

# SEVERAL roles — named ONCE, in the opener, then referred back to as "them"
# (Jude, 2026-08-19). Spelling three disciplines out twice is what made the
# multi-role case read badly; the fix is one mention plus a pronoun, not a
# shorter list. The opener is an observation rather than a question because we
# obviously already know — we saw all three ads. The CTA also has to shift
# from "this hire" to "any of these", since there is more than one.
CTA_ONE = "If this hire is a priority right now, I'd be glad to make an intro."
CTA_MANY = "If any of these are a priority right now, I'd be glad to make an intro."

BODY_MANY = """Hi {first},

Noticed you're hiring for {roles}{in_city}.

I know a recruiter who specialises in placing them. He only works on contingent, so nothing is owed unless the hire sticks.

{cta}"""

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
    parts = n.split()
    # "R. Adam Fishman" -> Adam. A leading initial is not a name, and the
    # subject line ("R., still hiring?") is where that shows up worst.
    while parts and (len(parts[0].rstrip(".")) <= 1 or parts[0].rstrip(".").isupper()
                     and len(parts[0].rstrip(".")) == 1):
        parts.pop(0)
    if not parts:
        return ""
    n = parts[0]
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


CITY_PREFIX = re.compile(
    r"^(city|town|village|borough|township|municipality) of\s+", re.I)


def clean_city(c):
    """Source city values carry administrative prefixes and facility names:
    'City of Rochester', 'Primary Care - Sonora - Sonora', 'Glacial Ridge
    Heath System - Glenwood'. Writing those into a cold email reads like
    unedited data. Take the last ' - ' segment (the facility precedes the
    place) and drop the 'City of' prefix."""
    c = (c or "").strip()
    if not c:
        return ""
    if " - " in c:
        c = c.split(" - ")[-1].strip()
    c = CITY_PREFIX.sub("", c).strip()
    if re.search(r"\d", c) or len(c.split()) > 4:
        return ""
    # the source shouts some names ("STE. GENEVIEVE"); a cold email that
    # shouts a city reads like unedited data. Mixed-case names are left alone
    # so McKeesport and DeKalb survive.
    if c.isupper() or c.islower():
        c = " ".join(w.capitalize() for w in c.split())
    return c


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
    ap.add_argument("--tabs", nargs="+", required=True,
                    help="ALL lane tabs at once. A company in both lanes must "
                         "be aggregated across them or it gets two emails.")
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
    raw, bycomp = {}, {}
    for tab in args.tabs:
        vals = svc.spreadsheets().values().get(
            spreadsheetId=sid, range=f"'{tab}'!A1:BA").execute().get("values", [])
        raw[tab] = vals[1:]
        for i, r in enumerate(raw[tab]):
            def c(x, _r=r):
                return _r[x].strip() if x < len(_r) else ""
            if c(COL_STATUS) != "KEEP" or not c(COL_COMPANY):
                continue
            d = bycomp.setdefault(c(COL_COMPANY),
                                  {"titles": [], "rows": {}, "first": "",
                                   "full": "", "cities": [],
                                   "seg": c(COL_SEGMENT)})
            d["titles"].append(c(COL_TITLE))
            d["cities"].append(c(COL_CITY))
            d["rows"].setdefault(tab, []).append(i)
            d["first"] = d["first"] or c(COL_FIRST)
            d["full"] = d["full"] or c(COL_DM_NAME)

    def c(r, i):
        return r[i].strip() if i < len(r) else ""

    made, skipped_norole, skipped_noname = {}, [], []
    fixed_first = {}
    for name, d in bycomp.items():
        dom = dominant(d["titles"])
        if not dom:
            skipped_norole.append(name)
            continue
        role, role_plural = dom
        # col X can hold a bare initial ("R." for R. Adam Fishman), which
        # resolves to nothing. Fall back to the full DM name, where the real
        # given name still sits.
        first = first_name(d["first"]) or first_name(d["full"])
        if not first:
            skipped_noname.append(name)
        roles, nfam = render_roles(d["titles"], want_count=True)
        # dominant city, so a multi-site company is anchored where the
        # hiring actually is rather than on whichever row sorted first
        cities = [x for x in (clean_city(c) for c in d["cities"]) if x]
        city = Counter(cities).most_common(1)[0][0] if cities else ""
        nloc = len(set(cities))
        # BODY_ONE is only right for a company with ONE live posting. Six
        # registered-nurse openings asked about as "the registered nurse role"
        # understates the pain and reads as if we only half-looked (Jude,
        # 2026-08-19). Several openings of a SINGLE discipline take the plural
        # observation form too — render_roles already pluralises them.
        if nfam == 1 and len(d["titles"]) == 1:
            body = BODY_ONE.format(first=first or "{first}", role=role,
                                   role_plural=role_plural,
                                   in_city=f" in {city}" if city else "")
        else:
            # naming one city on a company hiring across several would be
            # wrong, so the spread is acknowledged without listing them
            where = (f" in {city}" if nloc == 1 and city
                     else " across a few locations" if nloc > 1 else "")
            body = BODY_MANY.format(
                first=first or "{first}", roles=roles, in_city=where,
                cta=CTA_ONE if len(d["titles"]) == 1 else CTA_MANY)
        made[name] = body + (SIGNOFF if args.signoff else "")
        # The subject line renders {{firstName}} from col X, so a raw
        # "R." there would ship as "R., still hiring?". Heal the cell to
        # whatever the body greets, so subject and body always agree.
        if first and first != d["first"]:
            fixed_first[name] = first

    print(f"[{', '.join(args.tabs)}] {len(bycomp)} unique companies")
    print(f"  bodies rendered      : {len(made)}")
    print(f"  no clinical role     : {len(skipped_norole)}  {skipped_norole[:4]}")
    print(f"  awaiting a DM name   : {len(skipped_noname)}")

    if args.preview:
        seen, shown = set(), 0
        for seg in ("SINGLE", "MULTI_ONE_CITY", "MULTI_CITY"):
            print(f"\n{'='*66}\n{seg}\n{'='*66}")
            # show rows that would actually SEND — a preview full of
            # "Hi {first}," reviews nothing
            for name, d in bycomp.items():
                if d["seg"] != seg or name in seen or name not in made:
                    continue
                if not d["first"]:
                    continue
                if shown >= args.preview:
                    break
                seen.add(name)
                shown += 1
                nop = sum(len(v) for v in d["rows"].values())
                lanes = "+".join(t.split()[0] for t in d["rows"])
                print(f"\n--- {name}  ({nop} openings, {lanes}) ---")
                print(made[name])
            shown = 0
        return

    if not args.apply:
        print("\n  (no --apply and no --preview — nothing written)")
        return

    # A company in BOTH lanes must be emailed ONCE, not once per lane
    # (Jude, 2026-08-20). The body above is already built from the UNION of
    # its roles across lanes, so the single email names everything it is
    # hiring for. The lane with more openings owns the push; the other lane's
    # rows carry the same body but are marked DUP so the push skips them.
    owner = {}
    for name, d in bycomp.items():
        owner[name] = max(d["rows"], key=lambda t: len(d["rows"][t]))
    dual = [n for n, d in bycomp.items() if len(d["rows"]) > 1 and n in made]
    print(f"  companies in BOTH lanes, merged to one email: {len(dual)}")
    for n in dual[:5]:
        print(f"     {n[:34]:34s} -> push from {owner[n]!r}")

    if fixed_first:
        print(f"  first names healed for the subject line: {len(fixed_first)} "
              f"{list(fixed_first.items())[:3]}")
    for tab in args.tabs:
        write(svc, sid, [{"range": f"'{tab}'!{a1(COL_PUSH, 1)}",
                          "values": [["push_owner"]]}])
    pending, n = [], 0
    for name, body in made.items():
        if "{first}" in body:
            continue                      # never write "Hi {first},"
        d = bycomp[name]
        for tab, idxs in d["rows"].items():
            mark = "YES" if tab == owner[name] else "DUP"
            for ri in idxs:
                if name in fixed_first:
                    pending.append({"range": f"'{tab}'!{a1(COL_FIRST, ri + 2)}",
                                    "values": [[fixed_first[name]]]})
                pending.append({"range": f"'{tab}'!{a1(COL_PUSH, ri + 2)}",
                                "values": [[mark]]})
                if c(raw[tab][ri], COL_BODY) and not args.regenerate:
                    continue
                pending.append({"range": f"'{tab}'!{a1(COL_BODY, ri + 2)}",
                                "values": [[body]]})
        n += 1
        if n % BATCH == 0 and pending:
            write(svc, sid, pending)
            print(f"  written {n}/{len(made)} companies")
            pending = []
    if pending:
        write(svc, sid, pending)
    print(f"  done — {n} companies, one email each")


if __name__ == "__main__":
    main()
