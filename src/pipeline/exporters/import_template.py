"""Import CSV from a v2 record. The layout is the template named by PIPELINE_CSV_TEMPLATE, which defaults to
import_template_charter (the 19 columns in use_case/CHARTER.md).

Deterministic projection only: no LLM calls. Every value comes from record.json, so the checks
already applied to the record (reconciliation, Salesforce verification, email amendments, status
policy, review routing) carry over. Row grain: one earnings row per employee, then one row per
deduction. Standing (recurring) deductions are not exported: they are already set up in the payroll
system, so exporting them would double-deduct. They are counted in the meta sidecar instead.

Usage: python -m src.pipeline.exporters.import_template <jsonl> <import_column_map.json> <out_dir>
Writes <out_dir>/<record_id>.csv and <out_dir>/<record_id>.meta.json for each supported record.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import sys
from collections import Counter, OrderedDict
from datetime import date
from pathlib import Path

from .earning_by_column import resolve, resolve_template

TEMPLATE = os.getenv("PIPELINE_CSV_TEMPLATE", "import_template_charter")
SCHEMA_VERSION = "1"


def _tpl(cmap: dict) -> dict:
    return resolve_template(cmap, TEMPLATE)


def load_map(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def supported_doc_types(cmap: dict) -> list[str]:
    return _tpl(cmap)["supported_doc_types"]


def _val(obj) -> str | None:
    return (obj or {}).get("value") or None


def _mdy(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        return date.fromisoformat(iso).strftime("%m/%d/%Y")
    except ValueError:
        return iso


_RATE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*$")


def _parse_rate(v) -> tuple[float | None, str | None]:
    """Printed rates arrive as text, sometimes annotated ('20.0000 (struck/overwritten)') or non-numeric
    ('Per Pay Period'). Return (rate, flag): a clean number passes, anything else is None and flagged."""
    if isinstance(v, (int, float)):
        return float(v), None
    m = _RATE_RE.match(str(v or ""))
    if m:
        return float(m.group(1)), None
    lead = re.match(r"^\s*(\d+(?:\.\d+)?)\s*\(", str(v or ""))
    if lead:
        return float(lead.group(1)), "RATE_ANNOTATED"
    return None, "RATE_UNREADABLE"


def _legend_desc(code, rec) -> str | None:
    if not code:
        return None
    return next((c.get("description") for c in rec.get("code_legend") or [] if c.get("code") == code), None)


def _description_column(text: str, amt, q: dict, cmap: dict) -> str | None:
    """Column for a printed description (the document's legend or the handwritten label)."""
    if not text:
        return None
    for pattern, target in _tpl(cmap)["description_patterns"]:
        if re.search(pattern, text.upper()):
            if target == "OTHER_BY_MEASURE":
                return "Other Amount" if amt is not None else "Other Hours"
            return target
    return None


def _map_item(item: dict, rec: dict, cmap: dict) -> dict:
    """Return {col, value, flags} for an earnings item, or {reason} when it cannot be placed.

    Order: the pay code via default_code_map (only codes that mean the same on every client), then the
    document's own legend description for that code, then the handwritten label. Client-specific codes are
    therefore read from the document they appear on, never from a fixed table."""
    t = _tpl(cmap)
    q = item.get("quantity") or {}
    amt = item.get("amount")
    code = item.get("code")
    label_raw = (item.get("code_description") or "").strip()
    legend = (_legend_desc(code, rec) or "").strip()
    flags: set[str] = set()
    tip_text = (label_raw or legend).upper()

    if re.search(t["tips_pattern"], tip_text):
        col = "Credit Tips Amount" if re.search(t["credit_pattern"], tip_text) else "Cash Tips Amount"
        if not re.search(f"{t['cash_pattern']}|{t['credit_pattern']}", tip_text):
            flags.add("TIPS_TYPE_ASSUMED")
        value = amt
        if value is None and q.get("value") is not None:
            value = q["value"]  # tips are sometimes written as a number in the hours column
            flags.add("TIPS_QTY_AS_AMOUNT")
        if value is None:
            return {"reason": "tips without amount"}
        return {"col": col, "value": float(value), "flags": flags}

    code_col, _how = resolve(code or "", rec, cmap, template=TEMPLATE)
    legend_col = _description_column(legend, amt, q, cmap)
    label_col = _description_column(label_raw, amt, q, cmap)
    kind = item.get("kind")
    kind_col = _kind_column(kind, item, cmap) if kind else None
    col = code_col or legend_col or label_col or kind_col
    if kind and not code_col and not legend_col and not label_col and kind_col:
        # Placed from the model's judgment, not from anything printed. The reviewer sees why.
        flags.add("KIND_INFERRED")
    if kind == "tips_unspecified" and col == "Cash Tips Amount":
        flags.add("TIPS_TYPE_ASSUMED")
    for other in (legend_col, label_col):
        if code_col and other and other != code_col:
            # The document's description and the pay code point at different columns. The code wins (it is
            # what payroll reads), but a person must confirm.
            flags.add("LABEL_CODE_MISMATCH")
            break
    if col is None:
        if re.search(t["summary_label_pattern"], (legend or label_raw).upper()):
            # Printed subtotals ("TOTAL HOURS") duplicate the line items: never sum them as earnings.
            return {"reason": "summary line (not summed)"}
        return {"reason": "unmapped code" if code else "no code or known label"}

    if "measure_conflict" in (item.get("review_reasons") or []):
        flags.add("MEASURE_CONFLICT")  # the model read dollars in an hours column
    if col.endswith("Amount"):
        value = amt
        if value is None:
            return {"reason": "no amount"}
    else:
        value = q.get("value") if q.get("unit") in ("hours", None) else None
        if value is None:
            return {"reason": "no hours value"}
    return {"col": col, "value": float(value), "flags": flags}


def _kind_column(kind: str, item: dict, cmap: dict) -> str | None:
    """Column for a model-stated kind (CSV variant). Hours kinds need hours; money kinds need an amount."""
    col = _tpl(cmap).get("kind_columns", {}).get(kind)
    if col is None:
        return None
    if col.endswith("Amount") and item.get("amount") is None:
        return None
    if col.endswith("Hours") and (item.get("quantity") or {}).get("value") is None:
        return None
    return col


def _unplaced(item: dict, ref: str, reason: str, rec: dict) -> tuple[dict, str]:
    """The sidecar entry and the Review Flags token for an item that could not be placed. The token carries
    the document's own description and the value, so a reviewer sees what was not placed without opening the
    sidecar."""
    code = item.get("code")
    legend = _legend_desc(code, rec)
    desc = (item.get("code_description") or legend or "").strip()
    q = item.get("quantity") or {}
    if item.get("amount") is not None:
        value = f"${float(item['amount']):,.2f}"
    elif q.get("value") is not None:
        value = f"{float(q['value']):g} {q.get('unit') or 'units'}"
    else:
        value = "no value"
    entry = {"item_id": item["item_id"], "employee_ref": ref, "item_type": item.get("item_type"),
             "code": code, "legend": legend, "label": item.get("code_description"), "amount": item.get("amount"),
             "quantity": item.get("quantity"), "reason": reason}
    token = f"UNPLACED {item['item_id']}: code {code or '-'}"
    token += f" '{desc}'" if desc else ""
    token += f" = {value} ({reason})"
    return entry, token


def compute_total_pay(cells: dict, rate, cmap: dict) -> tuple[float | None, str | None]:
    """hours x rate x multiplier + dollar amounts. Returns (total, flag). Blank when it cannot be
    computed faithfully (an hours column without a multiplier, or hours without a rate)."""
    t = _tpl(cmap)
    hours_total = 0.0
    for col, mult in t["hours_multipliers"].items():
        v = cells.get(col)
        if v in (None, 0, 0.0):
            continue
        if mult is None or rate in (None, ""):
            return None, "TOTAL_PAY_NOT_COMPUTED"
        hours_total += float(v) * float(rate) * float(mult)
    amounts = sum(float(v) for c, v in cells.items() if c.endswith("Amount") and v not in (None, ""))
    if hours_total == 0.0 and amounts == 0.0:
        return None, None
    return round(hours_total + amounts, 2), None


def format_value(col: str, v) -> str:
    """Column-aware CSV text: money to 2 dp, hours and rates trimmed."""
    if v is None or v == "":
        return ""
    if isinstance(v, str):
        return v
    if "Amount" in col or col == "Total Pay":
        return f"{float(v):.2f}"
    return ("%.4f" % float(v)).rstrip("0").rstrip(".")  # hours and rates


def build(rec: dict, cmap: dict, include_extra: bool = True) -> tuple[list[dict], list[str], dict]:
    """Return (csv_rows, columns, meta) for one v2 record. csv_rows are dicts keyed by column name."""
    t = _tpl(cmap)
    columns = t["columns"] + (t["extra_columns"] if include_extra else [])
    doc = rec["document"]
    client = doc.get("client") or {}
    pp = doc.get("pay_period") or {}
    client_id = _val(client.get("number"))
    specialist = next((c.get("name") for c in doc.get("contacts") or [] if c.get("role") == "payroll_specialist"), None)
    record_flags = []
    if not client_id:
        record_flags.append("NO_CLIENT_ID")
    record_level = {"Client ID": client_id or "", "Client Name": _val(client.get("name")) or "",
                    "Payroll Specialist": specialist or "",
                    "Pay Period Start": _mdy(_val(pp.get("start"))), "Pay Period End": _mdy(_val(pp.get("end"))),
                    "Check Date": _mdy(_val(pp.get("check_date"))), "Pay Frequency": _val(pp.get("frequency")) or ""}

    emps = OrderedDict((e["employee_ref"], e) for e in rec["employees"])
    groups: "OrderedDict[str, dict]" = OrderedDict((k, {"earn": [], "ded": []}) for k in emps)
    excluded, unmapped_items, standing_omitted = [], [], 0
    pol = cmap["status_policy"]

    for item in rec["pay_items"]:
        if item["status"] in pol["exclude"]:
            excluded.append({"item_id": item["item_id"], "employee_ref": item.get("employee_ref"), "status": item["status"],
                             "code": item.get("code"), "amount": item.get("amount")})
            continue
        key = item.get("employee_ref") or f"name:{item.get('employee_name') or 'UNKNOWN'}"
        g = groups.setdefault(key, {"earn": [], "ded": []})
        g["ded" if item["item_type"] == "deduction" else "earn"].append(item)

    rows, meta_rows = [], []
    for ref, g in groups.items():
        e = emps.get(ref) or {}
        name = e.get("name") or {}
        cells: dict = {}
        labels, pages, items_meta, row_flags = [], set(), [], set()
        rate = None
        for item in g["earn"]:
            if item["item_type"] != "earning":
                entry, token = _unplaced(item, ref, "non-earning item", rec)
                unmapped_items.append(entry)
                row_flags.add(token)
                continue
            m = _map_item(item, rec, cmap)
            if "reason" in m:
                entry, token = _unplaced(item, ref, m["reason"], rec)
                unmapped_items.append(entry)
                row_flags.add(token)
                continue
            cells[m["col"]] = round(cells.get(m["col"], 0.0) + m["value"], 4)
            row_flags |= m["flags"]
            if item.get("rate") is not None and rate is None:
                rate = item["rate"]
            if item["status"] == "printed_default":
                row_flags.add("PRINTED_DEFAULT")
            if item["status"] == "added_from_email":
                row_flags.add("EMAIL_AMENDED")
            if item.get("confidence") == "low":
                row_flags.add("LOW_CONFIDENCE")
            if item.get("code_description"):
                labels.append(item["code_description"])
            prov = item.get("provenance") or {}
            if prov.get("page"):
                pages.add(prov["page"])
            items_meta.append({"item_id": item["item_id"], "column": m["col"], "value": m["value"], "page": prov.get("page"),
                               "raw_text": prov.get("raw_text"), "confidence": item.get("confidence"),
                               "status": item["status"], "source": prov.get("source"),
                               **({"kind": item["kind"], "kind_basis": item.get("kind_basis"),
                                   "evidence": item.get("evidence")} if item.get("kind") else {})})

        if rate is None and (e.get("attributes") or {}).get("rate_1") is not None:
            rate, rate_flag = _parse_rate(e["attributes"]["rate_1"])
            row_flags.add("RATE_FROM_ATTRIBUTES")
            if rate_flag:
                row_flags.add(rate_flag)

        emp_number = e.get("employee_number")
        if not emp_number and e.get("clock_number"):
            emp_number = str(int(e["clock_number"])) if str(e["clock_number"]).isdigit() else e["clock_number"]
            row_flags.add("EMP_NUM_FROM_CLOCK")
        if not emp_number and ref in emps:
            row_flags.add("NO_EMPLOYEE_NUMBER")
        if ref not in emps:
            row_flags.add("NO_EMPLOYEE_REF")

        action = cmap_action(e.get("status"), cmap)
        identity = {"Employee Number": emp_number or "", "Employee First Name": name.get("first") or "",
                    "Employee Last Name": name.get("last") or (g["earn"][0].get("employee_name") if g["earn"] else "") or "",
                    "Pay Rate(Optional)": rate, "Employee Action": action}
        earn = None
        has_earn = bool(cells) or any(f.startswith("UNPLACED") for f in row_flags) or bool(action)
        if has_earn:
            total, tflag = compute_total_pay(cells, rate, cmap)
            if tflag:
                row_flags.add(tflag)
            if rate is None and any(c in cells for c in t["hours_multipliers"]):
                row_flags.add("TOTAL_PAY_NOT_COMPUTED")
            dept_attr = t.get("department_attribute")
            dept = next((i.get("dept_override") for i in g["earn"] if i.get("dept_override")), None)
            dept = dept or ((e.get("attributes") or {}).get(dept_attr) if dept_attr else None) or ""
            out = {**record_level, **identity, "Department (optional)": dept,
                   "Total Pay": total, "Source Code/Label": "; ".join(OrderedDict.fromkeys(labels)),
                   "Review Flags (optional)": "; ".join(sorted(row_flags))}
            for col, v in cells.items():
                out[col] = v
            rows.append(("earnings", ref, out, sorted(pages), items_meta))
            earn = rows[-1]

        for item in g["ded"]:
            code = item.get("code") or item.get("code_description") or ""
            amount = item.get("amount")
            flags = set() if code else {"DEDUCTION_NO_CODE"}
            if amount is None:
                # No amount means no row: a deduction line with a blank amount would import wrongly.
                entry, _token = _unplaced(item, ref, "deduction without amount", rec)
                unmapped_items.append(entry)
                continue
            prov = item.get("provenance") or {}
            ded_item = {"item_id": item["item_id"], "column": "Deduction Amount (Optional)", "value": amount,
                        "page": prov.get("page"), "raw_text": prov.get("raw_text"), "confidence": item.get("confidence"),
                        "status": item["status"], "source": prov.get("source")}
            if t.get("deductions_on_earnings_row") and earn is not None and earn[2].get("Deduction Amount (Optional)") is None:
                # One row per employee: the first deduction rides on the earnings row, later ones keep their own row.
                earn[2]["Deduction Code (Optional)"] = code
                earn[2]["Deduction Amount (Optional)"] = amount
                earn[2]["Review Flags (optional)"] = "; ".join(sorted(
                    set(filter(None, (earn[2]["Review Flags (optional)"] or "").split("; "))) | flags))
                earn[4].append(ded_item)
                if prov.get("page") and prov["page"] not in earn[3]:
                    earn[3].append(prov["page"])
                continue
            out = {**record_level, **identity, "Employee Action": "", "Pay Rate(Optional)": None,
                   "Deduction Code (Optional)": code, "Deduction Amount (Optional)": amount,
                   "Review Flags (optional)": "; ".join(sorted(flags)), "Total Pay": None}
            rows.append(("deduction", ref, out, [prov["page"]] if prov.get("page") else [], [ded_item]))

        for rd in e.get("recurring_deductions") or []:
            if not rd.get("change_requested"):
                standing_omitted += 1
                continue
            out = {**record_level, **identity, "Employee Action": "", "Pay Rate(Optional)": None,
                   "Deduction Code (Optional)": rd.get("code") or "", "Deduction Amount (Optional)": rd.get("amount"),
                   "Review Flags (optional)": "DEDUCTION_CHANGE_REQUESTED", "Total Pay": None}
            rows.append(("deduction", ref, out, [], []))

    meta = {"schema_version": SCHEMA_VERSION, "template": TEMPLATE, "columns": columns,
            "record_id": rec["record_id"], "case_number": (rec.get("source") or {}).get("case_number"),
            "review_status": rec["extraction"].get("review_status"),
            "review_reasons": rec["extraction"].get("review_reasons", []),
            "document": {"client": {"name": record_level["Client Name"], "number": client_id},
                         "pay_period": {k: record_level[k] for k in ("Pay Period Start", "Pay Period End", "Check Date", "Pay Frequency")},
                         "doc_type": doc.get("doc_type"), "page_count": (rec.get("source") or {}).get("page_count")},
            "rows": [], "unmapped_items": unmapped_items, "excluded_items": excluded,
            "standing_deductions_omitted": standing_omitted,
            "record_flags": [{"code": f.get("code"), "severity": f.get("severity")} for f in rec.get("review_flags") or []]}

    csv_rows = []
    for n, (kind, ref, out, pages, items_meta) in enumerate(rows, 1):
        flags = {f for f in (out.get("Review Flags (optional)") or "").split("; ") if f} | set(record_flags)
        formatted = {c: format_value(c, out.get(c)) for c in columns}
        formatted["Review Flags (optional)"] = "; ".join(sorted(flags))
        csv_rows.append(formatted)
        meta["rows"].append({"row_id": f"R{n:03d}", "row_kind": kind, "employee_ref": ref,
                             "employee_name": " ".join(filter(None, [out.get("Employee First Name"), out.get("Employee Last Name")])),
                             "source_pages": pages, "flags": sorted(flags),
                             "fingerprint": hashlib.sha1(json.dumps([formatted[c] for c in columns]).encode()).hexdigest()[:16],
                             "items": items_meta, "total_pay": formatted.get("Total Pay", "")})
    return csv_rows, columns, meta


def cmap_action(status: str | None, cmap: dict) -> str:
    return _tpl(cmap)["employee_action"].get(status or "", "")


def csv_bytes(columns: list[str], rows: list[dict]) -> bytes:
    """The import CSV as bytes: UTF-8 with BOM, CRLF line endings, matching the template file."""
    buf = io.StringIO(newline="")
    w = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    """Atomic write of csv_bytes()."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(csv_bytes(columns, rows))
    os.replace(tmp, path)


# Every token this module can put in Review Flags. flag_text.py must explain each one (a test enforces it).
# UNPLACED is a prefix: the full token is "UNPLACED <item_id>: ...".
FLAG_TOKENS = (
    "UNPLACED", "RATE_FROM_ATTRIBUTES", "RATE_ANNOTATED", "RATE_UNREADABLE", "TOTAL_PAY_NOT_COMPUTED",
    "TIPS_TYPE_ASSUMED", "TIPS_QTY_AS_AMOUNT", "LABEL_CODE_MISMATCH", "PRINTED_DEFAULT", "EMAIL_AMENDED",
    "NO_EMPLOYEE_NUMBER", "EMP_NUM_FROM_CLOCK", "NO_EMPLOYEE_REF", "NO_CLIENT_ID", "DEDUCTION_NO_CODE",
    "DEDUCTION_CHANGE_REQUESTED", "LOW_CONFIDENCE", "HUMAN_EDITED", "KIND_INFERRED", "MEASURE_CONFLICT",
)


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    os.replace(tmp, path)


def main(src: str, map_path: str, out_dir: str) -> dict:
    cmap = json.load(open(map_path))
    supported = set(_tpl(cmap)["supported_doc_types"])
    out = Path(out_dir)
    stats = Counter()
    reasons = Counter()
    flags = Counter()
    for line in open(src):
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec["document"].get("doc_type") not in supported:
            stats["skipped_doc_type"] += 1
            continue
        rows, columns, meta = build(rec, cmap)
        write_csv(out / f"{rec['record_id']}.csv", columns, rows)
        write_json(out / f"{rec['record_id']}.meta.json", meta)
        stats["records"] += 1
        stats["rows"] += len(rows)
        stats["deduction_rows"] += sum(r["row_kind"] == "deduction" for r in meta["rows"])
        stats["unmapped_items"] += len(meta["unmapped_items"])
        stats["excluded_items"] += len(meta["excluded_items"])
        stats["standing_deductions_omitted"] += meta["standing_deductions_omitted"]
        stats["records_with_unmapped"] += bool(meta["unmapped_items"])
        for u in meta["unmapped_items"]:
            reasons[u["reason"]] += 1
        for r in meta["rows"]:
            flags.update(r["flags"])
            if r["row_kind"] == "earnings":
                stats["earnings_rows"] += 1
                stats["earnings_rows_total_blank"] += r["total_pay"] == ""
                stats["earnings_rows_without_items"] += not r["items"]
    summary = {"stats": dict(stats), "unmapped_reasons": dict(reasons), "row_flags": dict(flags.most_common())}
    write_json(out / "_summary.json", summary)
    print(json.dumps(summary, indent=1))
    return summary


if __name__ == "__main__":
    main(*sys.argv[1:4])
