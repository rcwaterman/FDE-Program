"""Draft 'Default Earning by Column Import' rows from v2 records using schema/import_column_map.json.

Resolution order for each pay item code: client_overrides[client].code_map -> default_code_map ->
legend_keyword_map applied to the record's own code_legend description. Items with an
unresolved code, or a status outside the status policy, go to the exceptions file.

Called by the export stage (stages/export.py). Writes <out_dir>/earning_by_column_draft.csv and <out_dir>/earning_by_column_exceptions.csv
"""
import csv, json, os, sys
from collections import OrderedDict

T = "default_earning_by_column_import"


def resolve_template(cmap: dict, name: str) -> dict:
    """A template by name. A template with `based_on` inherits every key of its parent it does not set."""
    tpl = cmap["templates"][name]
    if "based_on" not in tpl:
        return tpl
    parent = resolve_template(cmap, tpl["based_on"])
    return {**parent, **{k: v for k, v in tpl.items() if k != "based_on"}}


def resolve(code, rec, cmap, template=T):
    tpl = resolve_template(cmap, template)
    client = (((rec["document"].get("client") or {}).get("number")) or {}).get("value")
    ov = (cmap.get("client_overrides", {}).get(client) or {}).get("code_map", {})
    if code in ov:
        return ov[code]["column"], "client_override"
    if code in tpl["default_code_map"]:
        return tpl["default_code_map"][code]["column"], "default"
    desc = next((c["description"] for c in rec.get("code_legend") or [] if c["code"] == code), "") or ""
    for kw, col in tpl["legend_keyword_map"].items():
        if kw != "description" and kw in desc.upper():
            return col, f"legend:{desc}"
    return None, None


def main(src, map_path, out_dir):
    cmap = json.load(open(map_path))
    tpl = cmap["templates"][T]; pol = cmap["status_policy"]
    cols = tpl["key_columns"] + tpl["value_columns"] + ["Department (optional)"]
    rows, exc = [], []
    for line in open(src):
        rec = json.loads(line)
        if rec["document"]["doc_type"] != "coad_payroll_timesheet_report":
            continue
        client = (((rec["document"].get("client") or {}).get("number")) or {}).get("value")
        emps = {e["employee_ref"]: e for e in rec["employees"]}
        acc = OrderedDict()
        for p in rec["pay_items"]:
            if p["status"] not in pol["include"] + pol["include_if_confirmed"]:
                continue
            if p["item_type"] not in ("earning",):
                exc.append([rec["record_id"], client, p["item_id"], p.get("employee_name"), p.get("code"), p["item_type"], p["status"], "non-earning item"])
                continue
            col, how = resolve(p.get("code") or "", rec, cmap)
            q = p.get("quantity") or {}
            measure_amount = col and col.endswith("Amount")
            val = p.get("amount") if measure_amount else (q.get("value") if q.get("unit") == "hours" else None)
            if not col or val is None:
                exc.append([rec["record_id"], client, p["item_id"], p.get("employee_name"), p.get("code"), p["item_type"], p["status"],
                            "unmapped code" if not col else f"no {'amount' if measure_amount else 'hours'} value"])
                continue
            e = emps.get(p.get("employee_ref")) or {}
            key = p.get("employee_ref") or p.get("employee_name")
            row = acc.setdefault(key, {"Employee Number": e.get("employee_number"),
                                       "Employee First Name": (e.get("name") or {}).get("first"),
                                       "Employee Last Name": (e.get("name") or {}).get("last") or p.get("employee_name"),
                                       "Pay Rate(Optional)": p.get("rate") or (e.get("attributes") or {}).get("rate_1"),
                                       "Department (optional)": p.get("dept_override"),
                                       "_record": rec["record_id"], "_client": client, "_flags": set()})
            row[col] = round((row.get(col) or 0) + val, 4)
            if p["status"] == "printed_default":
                row["_flags"].add("includes printed_default")
            if p.get("confidence") == "low":
                row["_flags"].add("low-confidence value")
        rows.extend(acc.values())
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "earning_by_column_draft.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Record ID", "Client #"] + cols + ["Review Notes"])
        for r in rows:
            w.writerow([r["_record"], r["_client"]] + [r.get(c) for c in cols] + ["; ".join(sorted(r["_flags"]))])
    with open(os.path.join(out_dir, "earning_by_column_exceptions.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Record ID", "Client #", "Item", "Employee", "Code", "Item Type", "Status", "Reason"])
        w.writerows(exc)
    print(f"{len(rows)} employee rows, {len(exc)} exceptions -> {out_dir}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
