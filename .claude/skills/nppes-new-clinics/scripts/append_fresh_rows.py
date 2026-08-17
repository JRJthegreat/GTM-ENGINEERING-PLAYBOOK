"""Append fresh store rows to an EXISTING commercial-pool campaign sheet.

build_commercial_sheet.py rebuilds the whole pool into a new sheet; this
appender exists for batch 2+ so the campaign keeps one sheet (and one
already-pushed history to dedupe against). It reuses the builder's helpers
and layout by import — reuse-by-import, never edit the proven script.

Selection: store rows in --classes (default NEW_LOCATION +
HEALTH_SYSTEM_EXPANSION — batch 2 dropped NEW PRACTICE on reply data:
0 positive from 132, both wins came from existing-brand new locations),
solo rows excluded, cluster-filtered like the builder, and only NPIs not
already on the sheet. Appended rows are shuffled among themselves; col I
(owner dup count) continues from the AO names already on the sheet.

Usage:
  python3 -W ignore append_fresh_rows.py --sheet_url "URL" [--classes A,B]
      [--seed N] [--dry_run]
"""
import argparse
import importlib.util
import os
import random
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
spec = importlib.util.spec_from_file_location(
    "build_commercial_sheet", os.path.join(HERE, "build_commercial_sheet.py"))
bcs = importlib.util.module_from_spec(spec)
sys.modules["build_commercial_sheet"] = bcs
spec.loader.exec_module(bcs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet_url", required=True)
    ap.add_argument("--classes", default="NEW_LOCATION,HEALTH_SYSTEM_EXPANSION")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--dry_run", action="store_true")
    args = ap.parse_args()

    classes = [c.strip() for c in args.classes.split(",") if c.strip()]
    sid = args.sheet_url.split("/d/")[1].split("/")[0]

    svc = bcs.get_service()
    existing = svc.spreadsheets().values().get(
        spreadsheetId=sid, range=f"'{bcs.TAB}'!A2:AC").execute().get("values", [])
    known_npis = {r[0].strip() for r in existing if r and r[0].strip()}
    seen_owner = {}
    for r in existing:
        ao = (r[28].strip().upper() if len(r) > 28 and r[28] else "")
        if ao:
            seen_owner[ao] = seen_owner.get(ao, 0) + 1

    code_to_cluster = {}
    for cluster, codes in bcs.CLUSTERS.items():
        for c in codes:
            code_to_cluster[c] = cluster
    like = " OR ".join(f"taxonomy_code LIKE '{c}%'"
                       for codes in bcs.CLUSTERS.values() for c in codes)
    marks = ",".join("?" for _ in classes)
    conn = bcs.get_db()
    rows = [p for p in conn.execute(f"""
        SELECT * FROM practices
        WHERE classification IN ({marks})
          AND (solo_flag IS NULL OR solo_flag = 0)
          AND ({like})
          AND TRIM(COALESCE(org_name,'')) != ''""", classes).fetchall()
            if p["npi"] not in known_npis]
    random.Random(args.seed).shuffle(rows)

    nucc = bcs.load_nucc()
    today = date.today()
    values, tally = [], {}
    for p in rows:
        key = f"{(p['ao_first'] or '').strip()} {(p['ao_last'] or '').strip()}".strip().upper()
        seen_owner[key] = seen_owner.get(key, 0) + 1
        cluster = next((code_to_cluster[c] for c in code_to_cluster
                        if p["taxonomy_code"].startswith(c)), "Other")
        status = bcs.LEAD_TYPE.get(p["classification"], p["classification"])
        tally[status] = tally.get(status, 0) + 1
        row = [""] * len(bcs.HEADERS)
        row[0] = p["npi"]
        row[1] = nucc.get(p["taxonomy_code"], p["taxonomy_label"])
        row[2] = cluster
        row[3] = p["taxonomy_code"]
        row[4] = status
        row[5] = p["enumeration_date"]
        row[6] = str((today - date.fromisoformat(p["enumeration_date"])).days)
        row[7] = str(p["owner_site_count"] or 1)
        row[8] = str(seen_owner[key])
        row[9] = p["parent_lbn"] or ""
        row[10] = p["dba_name"] or p["org_name"]
        row[17] = p["city"] or ""
        row[18] = p["state"] or ""
        row[28] = f"{p['ao_first']} {p['ao_last']}".strip()
        row[29] = p["ao_title"] or ""
        row[30] = p["practice_phone"] or p["ao_phone"] or ""
        row[32] = p["addr1"] or ""
        row[33] = p["zip"] or ""
        row[34] = str(p["score"] if p["score"] is not None else "")
        values.append(row)

    print(f"[append] sheet has {len(known_npis)} NPIs | {len(values)} fresh rows to append")
    print("  " + " | ".join(f"{k}: {v}" for k, v in sorted(tally.items(), key=lambda x: -x[1])))
    if args.dry_run:
        print("[append] DRY RUN — nothing written")
        return
    for i in range(0, len(values), 500):
        svc.spreadsheets().values().append(
            spreadsheetId=sid, range=f"'{bcs.TAB}'!A1",
            valueInputOption="RAW", insertDataOption="INSERT_ROWS",
            body={"values": values[i:i + 500]}).execute()
    gid = svc.spreadsheets().get(spreadsheetId=sid).execute()["sheets"][0]["properties"]["sheetId"]
    total = len(existing) + len(values) + 1
    svc.spreadsheets().batchUpdate(spreadsheetId=sid, body={"requests": [
        {"updateDimensionProperties": {
            "range": {"sheetId": gid, "dimension": "ROWS",
                      "startIndex": 0, "endIndex": total},
            "properties": {"pixelSize": 18}, "fields": "pixelSize"}}]}).execute()
    print(f"[append] appended {len(values)} rows (row height pinned to 18px)")


if __name__ == "__main__":
    main()
