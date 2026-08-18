"""
Phase 4: generate the Email 1 body for each enriched EMaaS lead -> column Z.

CONNECTOR FRAMEWORK (Jude, 2026-08-18). Jude is the sender and the offer is an
INTRODUCTION to Sherif. Never first person as the service provider: no "we run
the audit", no "our platform". Jude knows an energy specialist and is offering
to connect them. Sherif's proof points are attributed to Sherif.

Structure, locked with Jude line by line:
    {opening_fact} {waste_clause}
    I know {specialist_phrase} who audits exactly this. {what_he_does}.
    The audit is free, {savings_funded}.
    {cta}

Two variable slots drive all the sector variation, both mechanical from fields
already in the store, so there is NO LLM call here and every line is auditable
back to a column:
  * opening fact  <- evidence type (job ad shift / job ad equipment / EPA
                     licence / aged-care register)
  * waste clause  <- the equipment signal in evidence_detail

CTA rule (Jude):
  * industrial / EPA / hospitality -> "If your power bill is north of 90k a
    year, might be worth an introduction."  The conditional does the
    qualifying: a reply means they have self-reported clearing Sherif's floor.
  * aged care and multi-site -> "Worth an introduction?"  The line above has
    already asserted they clear 90k, so re-asking would undercut it and read
    as not knowing their world.

House rules applied: no em dashes, no sign-off (the sending account signature
carries identity), plain text with real newlines, company names casualised,
recipient first names NEVER nicknamed.

Usage:
  python3 -W ignore generate_emaas_bodies.py --sheet_url URL --preview 8
  python3 -W ignore generate_emaas_bodies.py --sheet_url URL --apply
"""

import argparse
import os
import re
import sys

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN = os.path.join(SCRIPT_DIR, "..", "..", "..", "token.json")
TAB = "Leads"

C_COMPANY, C_WEB, C_SIZE = 10, 11, 12
C_EVID_DETAIL, C_EVID, C_CITY, C_STATE = 15, 16, 17, 18
C_DMNAME, C_DMTITLE, C_EMAIL, C_FIRST = 19, 20, 22, 23
C_BODY = 25
C_SUBJECT = 29          # AD — not in the 29-col base schema; rides as
                        # {{subject_line}} on the Instantly lead

LEGAL_RE = re.compile(
    r"\s*(pty\.?\s*ltd\.?|proprietary\s+limited|pty\.?|ltd\.?|limited|inc\.?|"
    r"incorporated|p/l|group|holdings|australia|aust)\b\.?", re.I)


def casual_company(name):
    """Strip legal suffixes and generic tails. Deliberately conservative: a
    name that would collapse to fewer than 3 characters keeps its original."""
    n = name.strip()
    prev = None
    while prev != n:
        prev = n
        n = LEGAL_RE.sub("", n).strip(" ,.&-")
    n = re.sub(r"\s{2,}", " ", n).strip()
    n = n if len(n) >= 3 else name.strip()
    # Registers store names in caps ("BRADNAMS WINDOWS AND DOORS"). Shouting at
    # a stranger in line 1 is an instant tell, so title-case anything that is
    # all upper, preserving genuine short acronyms (BP, GB, SPC).
    letters = [c for c in n if c.isalpha()]
    if letters and all(c.isupper() for c in letters):
        small = {"AND", "OF", "THE", "FOR", "AT", "ON", "IN", "CO", "&"}
        out = []
        for w in n.split():
            if w in small:
                out.append(w.lower() if w != "CO" else "Co")
            elif (len(w) <= 3 or "." in w) and w.isupper():
                out.append(w)          # genuine acronym: GB, SPC, DA, S.C.F
            else:
                out.append(w.capitalize())
        n = " ".join(out)
    return n


# A first word that is an article, a geography or a generic adjective is not a
# brand: "Australian Portable Camps" must not become "Australian's power bill".
WEAK_FIRST = {
    "australian", "australia", "national", "southern", "northern", "eastern",
    "western", "central", "global", "international", "united", "general",
    "premier", "superior", "advanced", "modern", "quality", "total", "prime",
    "first", "new", "great", "royal", "st", "saint", "victorian", "queensland",
    "grand", "glass", "kitchen", "quality", "value", "budget", "city", "town",
}


def short_name(company):
    """Brand root for the subject line: 'Bulla Dairy Foods' -> 'Bulla'.

    Falls back to the full casual name whenever the first word would not stand
    alone as the business: a leading article, a geography or generic adjective,
    or an initial. Better a longer subject than 'Australian's power bill'."""
    cc = casual_company(company)
    words = cc.split()
    if words and words[0].lower() == "the":
        words = words[1:]
        cc = " ".join(words)
    if len(words) <= 2:
        return cc
    if len(words[0]) <= 2 or words[0].lower().strip(".") in WEAK_FIRST:
        # Full name, but never a subject line that runs off the screen.
        return " ".join(words[:4]) if len(cc) > 34 else cc
    if len(words) > 1 and words[0].lower() == words[1].lower():
        return " ".join(words[:2])     # Woy Woy, Wagga Wagga
    return words[0]


def possessive(name):
    return f"{name}'" if name.rstrip().endswith("s") else f"{name}'s"


def title_place(s):
    return " ".join(w.capitalize() if w.islower() or w.isupper() else w
                    for w in (s or "").split())


SHIFT_RE = re.compile(r"24/7|24-7|around the clock|rotating|night shift|"
                      r"afternoon shift|two shift|2 shift|shift work", re.I)
COLD_RE = re.compile(r"refrigerat|chiller|freezer|cold ?stor|cool ?room|ammonia|glycol", re.I)
HEAT_RE = re.compile(r"boiler|oven|furnace|kiln|retort|autoclave|steam", re.I)
HVAC_RE = re.compile(r"hvac|air handling|air conditioning", re.I)
AIR_RE = re.compile(r"compress", re.I)


def equipment_noun(signal):
    """Name the plant in the reader's own vocabulary, so line 1 states a fact
    they would recognise rather than a category we invented."""
    if re.search(r"cold ?stor|cool ?room|freezer", signal, re.I):
        return "cold storage"
    if COLD_RE.search(signal):
        return "refrigeration"
    if re.search(r"boiler|steam", signal, re.I):
        return "boilers"
    if re.search(r"oven", signal, re.I):
        return "ovens"
    if re.search(r"furnace|kiln", signal, re.I):
        return "furnaces"
    if HVAC_RE.search(signal):
        return "HVAC"
    return "plant"


def waste_clause(signal):
    """What is most likely bleeding money, from the equipment signal."""
    if COLD_RE.search(signal):
        return ("Refrigeration usually runs harder overnight than anyone "
                "realises, and 20 to 30 percent of the bill is typically "
                "waste nobody can see.")
    if HEAT_RE.search(signal):
        return ("Process heat is usually where the bill hides, and 20 to 30 "
                "percent of it is typically waste nobody can see.")
    if HVAC_RE.search(signal):
        return ("HVAC is usually the single biggest line, and 20 to 30 "
                "percent of it is typically waste nobody can see.")
    if AIR_RE.search(signal):
        return ("Compressed air is one of the most expensive things on a "
                "site, and 20 to 30 percent of the bill is typically waste "
                "nobody can see.")
    return ("Sites like that usually burn well past 90k a year in "
            "electricity, and 20 to 30 percent of it is typically waste "
            "nobody can see.")


# EPA activity -> how a person there would describe the site, and what its
# dominant energy load actually is. The register gives us the activity, which
# is a far better equipment signal than a job ad title, but only once mapped.
ACTIVITY_NOUN = {
    "galvanising": "a galvanising plant",
    "printing": "a printing operation",
    "fibreboard": "a fibreboard plant",
    "chemical": "chemical manufacturing",
    "chemical_works": "a chemical works",
    "abattoir": "an abattoir",
    "abattoir,rendering": "an abattoir and rendering operation",
    "rendering": "a rendering operation",
    "edible_oil,rendering": "an edible oil and rendering operation",
    "milk_processing": "a milk processing plant",
    "organic_waste": "an organic waste processing operation",
    "container_washing": "a container washing operation",
    "bulk_storage": "a bulk storage operation",
    "aquaculture": "an aquaculture operation",
    "manufacturing": "a manufacturing plant",
    "waste_to_energy": "a waste to energy operation",
}
ACTIVITY_SIGNAL = {
    "galvanising": "kettle furnace",          # molten zinc, held hot 24/7
    "printing": "compressed air",
    "fibreboard": "press furnace steam",
    "chemical": "boiler steam",
    "chemical_works": "boiler steam",
    "abattoir": "refrigeration chiller",
    "abattoir,rendering": "refrigeration cooker steam",
    "rendering": "cooker steam boiler",
    "edible_oil,rendering": "boiler steam",
    "milk_processing": "refrigeration pasteuriser",
    "container_washing": "boiler hot water",
    "aquaculture": "compressed air",
    "waste_to_energy": "boiler steam",
}


def parse_beds(detail):
    fac = re.search(r"(\d+)\s+facilit", detail or "")
    beds = re.search(r"(\d+)\s+beds", detail or "")
    return (int(fac.group(1)) if fac else None,
            int(beds.group(1)) if beds else None)


NUM_WORD = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six",
            7: "seven", 8: "eight", 9: "nine", 10: "ten"}


def build_opening(company, evidence, detail, city):
    """Returns (opening_fact, is_aged_care) or (None, _) if unusable."""
    cc = casual_company(company)
    place = title_place(city)
    signal = detail or ""

    if evidence == "aged_care_register":
        fac, beds = parse_beds(detail)
        if not beds:
            return None, True
        if fac and fac > 1:
            homes = NUM_WORD.get(fac, str(fac))
            where = f" around {place}" if place else ""
            return (f"{cc} runs {beds} beds across {homes} homes{where}. "
                    f"With 24/7 HVAC, kitchens and laundry, {homes} homes "
                    f"together would clear 90k a year in electricity easily."), True
        where = f" at {place}" if place else ""
        return (f"{cc} runs a {beds} bed home{where}. Running HVAC, a "
                f"kitchen and laundry around the clock, a home that size "
                f"usually clears 90k a year in electricity."), True

    if evidence == "epa_licence":
        cats = signal.split("|")[0].strip().lower()
        where = f" at {place}" if place else ""
        if not cats:
            return None, False
        noun = ACTIVITY_NOUN.get(cats, cats.replace("_", " ").replace(",", " and ")
                                 + " operation")
        # The EPA activity IS the equipment signal: a galvaniser runs molten
        # kettles, a renderer runs cookers and chillers. Without this the whole
        # EPA lane shared one generic waste line.
        return (f"{cc} runs {noun}{where}. "
                f"{waste_clause(ACTIVITY_SIGNAL.get(cats, ''))}"), False

    if evidence == "job_ad":
        where = f"{place} site" if place else "site"
        if SHIFT_RE.search(signal) and not (COLD_RE.search(signal)
                                            or HEAT_RE.search(signal)):
            return (f"{cc}'s {where} looks like it runs around the clock. "
                    f"{waste_clause(signal)}"), False
        if COLD_RE.search(signal) or HEAT_RE.search(signal) or HVAC_RE.search(signal):
            kit = equipment_noun(signal)
            clock = " around the clock" if SHIFT_RE.search(signal) else ""
            at = f" at {place}" if place else ""
            return (f"{cc} runs {kit}{at}{clock}. "
                    f"{waste_clause(signal)}"), False
        return (f"{cc}'s {where} looks like it runs around the clock. "
                f"{waste_clause(signal)}"), False

    return None, False


SPECIALIST = ("an energy engineer in Melbourne", "a specialist",
              "an energy specialist")


def build_body(first, company, evidence, detail, city, aged):
    opening, is_aged = build_opening(company, evidence, detail, city)
    if not opening:
        return None, None
    cc = casual_company(company)

    if is_aged:
        who = "an energy specialist who works with operators like you"
        does = ("He audits the site and typically finds 20 to 30 percent of "
                "the bill is pure waste")
        funded = ("and anything after that is funded from the savings, so "
                  "nothing comes out of your budget")
        cta = "Worth an introduction?"
        subject = f"{possessive(short_name(company))} power bill"
    else:
        who = "an energy engineer in Melbourne"
        does = ("He puts circuit-level monitoring across a site and shows you "
                "what is running outside hours, what is drawing production "
                "load while idle, and what it is costing in dollars")
        funded = ("and his ongoing model is funded out of the savings, so "
                  "there is never any capex")
        cta = ("If your power bill is north of 90k a year, might be worth an "
               "introduction.")
        subject = f"{possessive(short_name(company))} power bill"

    lead = "who audits exactly this. " if not is_aged else ". "
    lead = f"I know {who} {lead}".replace("  ", " ").replace(" . ", ". ")
    body = (f"Hi {first},\n\n"
            f"{opening}\n\n"
            f"{lead}{does}. The audit is "
            f"free, {funded}.\n\n"
            f"{cta}")
    return subject, body


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet_url", required=True)
    ap.add_argument("--preview", type=int, default=0)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()

    sid = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", args.sheet_url).group(1)
    svc = build("sheets", "v4",
                credentials=Credentials.from_authorized_user_file(TOKEN))
    vals = svc.spreadsheets().values().get(
        spreadsheetId=sid, range=f"{TAB}!A1:AC2000").execute().get("values", [])

    def cell(r, i):
        return r[i].strip() if len(r) > i and r[i] else ""

    made, skipped, pending = 0, 0, []
    previews = []
    for idx, r in enumerate(vals[1:], start=2):
        email = cell(r, C_EMAIL)
        if not email:
            continue
        if cell(r, C_BODY) and not args.overwrite:
            continue
        first = cell(r, C_FIRST) or (cell(r, C_DMNAME).split() or [""])[0]
        if not first:
            skipped += 1
            continue
        subject, body = build_body(
            first, cell(r, C_COMPANY), cell(r, C_EVID),
            cell(r, C_EVID_DETAIL), cell(r, C_CITY), None)
        if not body:
            skipped += 1
            continue
        made += 1
        if len(previews) < args.preview:
            previews.append((cell(r, C_COMPANY), cell(r, C_DMTITLE),
                             subject, body))
        pending.append({"range": f"{TAB}!Z{idx}", "values": [[body]]})
        pending.append({"range": f"{TAB}!AD{idx}", "values": [[subject]]})

    for i, (co, ti, subj, body) in enumerate(previews, 1):
        print(f"\n{'=' * 72}\n[{i}] {co}   ({ti})\nSUBJECT: {subj}\n{'-' * 72}")
        print(body)
    print(f"\n{'=' * 72}\ngenerated {made}, skipped {skipped}")

    if not args.apply:
        print("dry run — nothing written. Re-run with --apply.")
        return
    for i in range(0, len(pending), 10):
        svc.spreadsheets().values().batchUpdate(
            spreadsheetId=sid,
            body={"valueInputOption": "RAW", "data": pending[i:i + 10]}).execute()
    print(f"wrote {len(pending)} bodies to column Z")


if __name__ == "__main__":
    sys.exit(main())
