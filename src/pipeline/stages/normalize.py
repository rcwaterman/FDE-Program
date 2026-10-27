"""Stage 7: assembled (raw text) -> schema v2 record. All conversion rules live here."""
from __future__ import annotations

import re

from ..store import now
from ..values import parse_quantity, parse_amount, parse_date, freq_letter, split_name, snake
from .base import Context, StageSkip

DOC_TYPES = {"coad_payroll_timesheet_report", "coad_accrued_hours_report", "punch_time_card", "time_card_detail_report",
             "handwritten_payroll_note", "client_payroll_summary", "fax_cover_sheet", "direct_deposit_form",
             "new_hire_form", "tax_form", "other"}
KIND_TO_DOCTYPE = {"coad_timesheet": "coad_payroll_timesheet_report", "coad_accrued_hours": "coad_accrued_hours_report",
                   "handwritten_note": "handwritten_payroll_note", "fax_cover_sheet": "fax_cover_sheet"}
# CSV variant: the code a model-stated kind implies. Tips, other pay and unknown kinds have no code.
KIND_CODE = {"regular": "1", "overtime": "2", "double_time": "20", "vacation": "23", "holiday": "24", "sick": "25",
             "pto": "26", "bonus": "19", "commission": "27", "retro": "29"}


def _csv_column(col: str, measure: str | None) -> str:
    """Where a CSV-variant entry sits in the item model. A margin value is dollars or hours by measure, and a
    dollar value written in an hours column is treated as dollars (the column is recorded as a conflict)."""
    if col == "margin":
        return "amount_generic" if measure == "dollars" else "hours_generic"
    if measure == "dollars" and col in HOUR_COLUMNS:
        return "amount_generic"
    return col


COLUMN_CODE = {"regular": "1", "overtime": "2"}
HOUR_COLUMNS = {"regular", "overtime", "other_hours", "hours_generic"}
AMOUNT_COLUMNS = {"earnings", "deduction", "salary", "amount_generic"}
LABEL_CODE = [(re.compile(r"^(reg|regular|r)\b", re.I), "1"), (re.compile(r"^(ot|o/t|overtime)\b", re.I), "2"),
              (re.compile(r"^vac", re.I), "23"), (re.compile(r"^hol", re.I), "24"), (re.compile(r"^sick", re.I), "25"),
              (re.compile(r"^bonus", re.I), "19"), (re.compile(r"^comm", re.I), "27")]
NUMERIC_ATTR = re.compile(r"^(rate_\d|salary|ytd|vac|sick)", re.I)
CONF = {"clear": "high", "uncertain": "medium", "illegible": "low"}
PAYEE = re.compile(r"\b(inc|llc|fund|funds|bank|services|financial|union|local \d+|401k|aflac|trust)\b", re.I)


def _sourced(raw, source="printed", pages=None, confidence="high"):
    if not raw:
        return None
    return {"value": str(raw).replace("?", "").strip(), "source": source,
            "page": (pages or [None])[0], "confidence": "medium" if "?" in str(raw) else confidence}


def _sdate(raw, pages=None, default_year=None):
    if not raw:
        return None
    iso, inferred = parse_date(raw, default_year)
    return {"value": iso, "raw": str(raw), "source": "printed", "page": (pages or [None])[0],
            "confidence": "medium" if (inferred or "?" in str(raw) or iso is None) else "high"}


def _legend_desc(legend, code):
    if not code:
        return None
    for c in legend:
        if c["code"].upper() == str(code).upper():
            return c["description"]
    return None


def _code_from_label(label):
    for rx, code in LABEL_CODE:
        if label and rx.search(label.strip()):
            return code
    return None


def normalize(a: dict, entry: dict, pre: dict | None = None) -> dict:
    h = a["header"]
    hv = lambda f: (h.get(f) or {}).get("value")
    hp = lambda f: (h.get(f) or {}).get("pages")
    end_iso, _ = parse_date(hv("period_end"))
    default_year = int(end_iso[:4]) if end_iso else None
    flags = list(a.get("flags", []))
    legend = a.get("code_legend", [])

    # ---- employees
    employees, emp_by_ref = [], {}
    for e in a["employees"]:
        attrs = {}
        for k, val in (e.get("printed_fields") or {}).items():
            key = snake(k)
            if val in (None, ""):
                continue
            n = parse_amount(val) if NUMERIC_ATTR.match(key) else None
            attrs[key] = n if n is not None else val
        marks = " ".join(e.get("marks") or []).lower()
        status = "remove_requested" if re.search(r"\b(remove|term|terminated|delete)\b", marks) else \
            ("payee_only" if PAYEE.search(e.get("name") or "") and (e.get("pay_type") or "").upper() not in ("W-2", "W2") else "active")
        recd = [{"code": d["code"], "description": _legend_desc(legend, d["code"]),
                 "amount": parse_amount(d.get("amount_text")), "ytd": parse_amount(d.get("ytd_text")),
                 "balance": parse_amount(d.get("balance_text")), "change_requested": d.get("change_written")}
                for d in e.get("recurring_deductions") or []]
        emp = {"employee_ref": e["employee_ref"], "name": split_name(e.get("name") or "(unnamed)"),
               "employee_number": e.get("employee_number"), "clock_number": e.get("clock_number"),
               "pay_type": e.get("pay_type"), "status": status, "attributes": attrs,
               "recurring_deductions": recd, "page": e.get("page")}
        employees.append(emp); emp_by_ref[emp["employee_ref"]] = emp

    # ---- pay items
    items = []
    for en in a["entries"]:
        col = en["column"]
        kind = en.get("kind")  # present only in the CSV variant
        measure_conflict = kind is not None and en.get("measure") == "dollars" and col in HOUR_COLUMNS
        if kind is not None:
            col = _csv_column(col, en.get("measure"))
        p = parse_quantity(en.get("value_text"))
        emp = emp_by_ref.get(en.get("employee_ref"))
        reasons = []
        conf = CONF.get(en.get("legibility"), "high")
        if p.uncertain and conf == "high":
            conf = "medium"
        if conf != "high":
            reasons.append("uncertain_reading")
        if kind is None:
            code = COLUMN_CODE.get(col) or (en.get("code_written") or "").strip() or None
        else:
            # CSV variant: the model's kind decides the code. A tip under the Overtime column is not overtime.
            code = (en.get("code_written") or "").strip() or KIND_CODE.get(kind) or (COLUMN_CODE.get(col) if kind == "unknown" else None)
            if en.get("kind_basis") not in ("code", "legend", "label"):
                reasons.append("kind_inferred")
                conf = "medium" if conf == "high" else conf
            if en.get("kind_confidence") == "low":
                conf = "medium" if conf == "high" else conf
            if measure_conflict:
                reasons.append("measure_conflict")
        if col in ("hours_generic", "amount_generic") and not code:
            code = _code_from_label(en.get("label_written"))
        item_type, qty, amount, rate, dept = "earning", None, None, parse_amount(en.get("rate_text")), None
        if col == "deduction":
            item_type = "deduction"
        elif col.startswith("exception"):
            item_type = "exception"
        elif re.search(r"reimb|mileage", f"{en.get('label_written') or ''} {en.get('note') or ''}", re.I):
            item_type = "reimbursement"
        if p.kind == "salary_marker":
            amount = (emp or {}).get("attributes", {}).get("salary")
            amount = amount if isinstance(amount, (int, float)) else None
            reasons.append("salary_marker")
            if amount is None:
                reasons.append("salary_amount_unknown")
        elif col in HOUR_COLUMNS:
            if p.value is not None:
                qty = {"value": p.value, "unit": p.unit or "hours"}
                if p.unit == "days":
                    reasons.append("days_not_hours")
            amount = parse_amount(en.get("amount_text"))
        elif col in AMOUNT_COLUMNS:
            amount = p.value
        elif col == "exception_rate":
            rate = p.value
        elif col == "exception_dept":
            dept = en.get("value_text")
        if p.kind == "text" and col not in ("exception_dept", "exception_freq"):
            item_type = "other"
            reasons.append("unparsed_value")
            conf = "low"
        if item_type in ("earning", "deduction", "reimbursement") and not code and p.kind != "salary_marker":
            reasons.append("no_code")
        status = "struck_through" if en.get("struck_through") else ("printed_default" if en.get("source") == "printed" else "submitted")
        raw = en.get("value_text") if (p.kind != "number" or p.uncertain or str(p.value) != str(en.get("value_text")).strip()) else None
        comment_parts = [en.get("note")] + ([f"alternatives: {', '.join(en['alternatives'])}"] if en.get("alternatives") else [])
        csv_fields = ({"kind": kind, "kind_basis": en.get("kind_basis"), "kind_confidence": en.get("kind_confidence"),
                       "evidence": en.get("evidence"), "measure": en.get("measure")} if kind is not None else {})
        items.append({
            **csv_fields,
            "item_id": f"P{len(items) + 1:04d}", "employee_ref": en.get("employee_ref"),
            "employee_name": (emp or {}).get("name", {}).get("full") or en.get("row_key"),
            "item_type": item_type, "code": code, "code_description": _legend_desc(legend, code) or en.get("label_written"),
            "quantity": qty, "rate": rate, "amount": amount,
            "work_date": parse_date(en.get("work_date_text"), default_year)[0] if en.get("work_date_text") else None,
            "dept_override": dept, "calculation_shown": en.get("calculation_text"), "status": status,
            "provenance": {"source": en.get("source", "handwritten"), "page": en.get("page"), "location": en.get("location"),
                           "raw_text": raw, "email_message_id": None},
            "confidence": conf, "review_reasons": reasons,
            "comment": "; ".join(c for c in comment_parts if c) or None,
        })

    # ---- totals
    totals = []
    for t in a["totals"]:
        col = t["column"]
        val = parse_amount(t.get("value_text"))
        code = COLUMN_CODE.get(col) or (t.get("code_written") or None)
        measure = "hours" if col in ("regular", "overtime", "other_hours", "total_hours") else \
            ("amount" if col in ("earnings", "deduction", "total_amount") else None)
        totals.append({"label": t.get("label") or f"Totals {col}", "scope": t["scope"], "page": t["page"], "code": code,
                       "column": col, "measure": measure,
                       "document_value": val if val is not None else str(t.get("value_text")),
                       "source": t.get("source", "handwritten"), "extracted_sum": None, "difference": None, "reconciles": None})

    # ---- instructions
    instr = []
    for i in a.get("instructions", []):
        text = i["text"]
        kind = "remove_employee" if re.search(r"\b(remove|term)\b", text, re.I) else \
            "net_pay_target" if re.search(r"\bnet\b", text, re.I) else \
            "deduction_change" if re.search(r"401|deduct|garn", text, re.I) else "general"
        ref = None
        if i.get("row_key"):
            ref = next((e["employee_ref"] for e in a["employees"] if i["row_key"] in (e.get("employee_number"), e.get("name"))), None)
        instr.append({"text": text, "employee_ref": ref, "page": i.get("page"), "source": i.get("source", "handwritten"), "kind": kind})

    # ---- punches, DD, signatures
    punches = []
    for t in a.get("time_punches", []):
        io = t.get("in_out") or []
        segs = [{"in": io[k], "out": io[k + 1] if k + 1 < len(io) else None} for k in range(0, len(io), 2)]
        punches.append({"employee_ref": None, "employee_name": t.get("row_key"),
                        "date": parse_date(t.get("date_text"), default_year)[0] or t.get("date_text"),
                        "segments": segs, "hours": parse_amount(t.get("hours_text")), "minutes": None,
                        "source": t.get("source", "handwritten"), "page": t.get("page"), "comment": None})
    dd = []
    for d in a.get("direct_deposit", []):
        act = (d.get("action") or "").lower()
        dt = (d.get("deposit_type") or "").lower().replace(" ", "_")
        dd.append({"employee_name": d.get("employee_name"), "ssn": d.get("ssn"), "address": None,
                   "action": act if act in ("new", "change", "cancel") else None,
                   "accounts": [{"seq": 1, "account_type": (d.get("account_type") or "").lower() or None,
                                 "routing_number": d.get("routing_number"), "account_number": d.get("account_number"),
                                 "deposit_type": dt if dt in ("entire_net", "flat", "percent", "remainder") else None,
                                 "deposit_value": parse_amount(d.get("deposit_value_text")),
                                 "struck_through": bool(d.get("struck_through"))}],
                   "bank_name": None, "signed": d.get("signed"), "signature_date": d.get("signature_date_text"),
                   "page": d.get("page"), "comment": None})
    for e in [x for x in dd if x["accounts"][0]["account_type"] not in (None, "checking", "savings", "other")]:
        e["accounts"][0]["account_type"] = "other"

    flags += [{"code": "PAGE_ISSUE", "severity": "info", "message": f"p{i['page']}: {i['text']}", "refs": []} for i in a.get("issues", [])]
    doc_type = a.get("doc_type") if a.get("doc_type") in DOC_TYPES else "other"
    contacts = []
    if hv("payroll_contact"):
        contacts.append({"role": "payroll_contact", "name": hv("payroll_contact"), "phone": hv("payroll_contact_phone"),
                         "fax": None, "email": None, "page": (hp("payroll_contact") or [None])[0]})
    if hv("specialist"):
        contacts.append({"role": "payroll_specialist", "name": hv("specialist"), "phone": hv("specialist_phone"),
                         "fax": None, "email": None, "page": (hp("specialist") or [None])[0]})
    header_fields = {k: hv(k) for k in ("call_date", "call_time", "delivery", "generated_on") if hv(k)}
    header_fields.update({k: v for k, v in (a.get("header_extra") or {}).items()})
    fl = freq_letter(hv("frequency"))
    return {
        "schema_version": "2.0",
        "record_id": a["record_id"],
        "extraction": {"extracted_at": now(), "method": "claude-page-extraction"},
        "source": {"file_name": entry["file_name"], "case_number": entry["case_number"],
                   "content_document_id": entry["content_document_id"], "byte_size": entry.get("byte_size"),
                   "sha256": entry.get("sha256"), "page_count": max(1, len(a["pages"])),
                   "text_layer": (pre or {}).get("text_layer")},
        "document": {
            "doc_type": doc_type, "doc_type_detail": None, "form_variant": hv("form_variant"),
            "segments": [{"pages": s["pages"], "doc_type": s["doc_type"], "client_number": s["client_number"],
                          "client_name": s["client_name"], "note": s["note"]} for s in a.get("segments", [])],
            "client": {"name": _sourced(hv("client_name"), pages=hp("client_name")),
                       "number": _sourced(hv("client_number"), pages=hp("client_number"))},
            "pay_period": {"start": _sdate(hv("period_begin"), hp("period_begin"), default_year),
                           "end": _sdate(hv("period_end"), hp("period_end"), default_year),
                           "check_date": _sdate(hv("check_date"), hp("check_date"), default_year),
                           "frequency": _sourced(fl or hv("frequency"), pages=hp("frequency")),
                           "period_number": hv("period_number"), "summary_number": hv("summary_number")},
            "contacts": contacts, "header_fields": header_fields, "instructions": instr,
        },
        "code_legend": legend, "employees": employees, "pay_items": items, "time_punches": punches,
        "direct_deposit": dd, "totals": totals,
        "signatures": [{"signer": s.get("signer"), "role": s.get("role"), "date": s.get("date_text"), "page": s.get("page")}
                       for s in a.get("signatures", [])],
        "pages": [{"page": p["page"], "orientation": p.get("orientation"), "printed_text": p.get("printed_text"),
                   "handwritten_text": p.get("handwritten_text")} for p in a["pages"]],
        "review_flags": flags,
        "salesforce_verification": None,
    }


def run(ctx: Context, rec, entry: dict) -> None:
    if not rec.has("assembled.json"):
        raise StageSkip("nothing assembled")
    pre = rec.read("preprocess.json") if rec.has("preprocess.json") else None
    rec.write("normalized.json", normalize(rec.read("assembled.json"), entry, pre))
