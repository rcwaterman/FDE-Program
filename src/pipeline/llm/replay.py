"""ReplayLLMClient: answers LLM calls from previously verified v2 records instead of calling the API.

Used for dry runs and regression tests of everything around the model: it converts a known-good record
(e.g. src/feature_extraction/payroll_extractions.jsonl) back into the page-level readings the model would return, so
the deterministic stages can be run end-to-end on the real PDFs and compared with the reference.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .client import ToolResult
from ..values import parse_quantity

DOC_TO_KIND = {"coad_payroll_timesheet_report": "coad_timesheet", "coad_accrued_hours_report": "coad_accrued_hours",
               "punch_time_card": "punch_time_card", "time_card_detail_report": "time_card_detail_report",
               "handwritten_payroll_note": "handwritten_note", "client_payroll_summary": "client_payroll_summary",
               "fax_cover_sheet": "fax_cover_sheet", "direct_deposit_form": "direct_deposit_form",
               "new_hire_form": "new_hire_form", "tax_form": "tax_form"}
RID = re.compile(r"Document (\S+)\.")
PAGE = re.compile(r"Page (\d+) of (\d+)")


def _fmt(v):
    if v is None:
        return ""
    return f"{v:.2f}" if isinstance(v, float) else str(v)


def _v(x):
    return (x or {}).get("value") if isinstance(x, dict) else None


def _raw(x):
    return (x or {}).get("raw") or _v(x)


def record_to_pages(rec: dict) -> dict[int, dict]:
    d = rec["document"]; pp = d.get("pay_period") or {}
    pc = next((c for c in d.get("contacts", []) if c["role"] == "payroll_contact"), {})
    sp = next((c for c in d.get("contacts", []) if c["role"] == "payroll_specialist"), {})
    header = {"client_name": _v((d.get("client") or {}).get("name")) if ((d.get("client") or {}).get("name") or {}).get("source") != "salesforce" else None,
              "client_number": _v((d.get("client") or {}).get("number")) if ((d.get("client") or {}).get("number") or {}).get("source") != "salesforce" else None,
              "form_variant": d.get("form_variant"), "period_begin": _raw(pp.get("start")), "period_end": _raw(pp.get("end")),
              "check_date": _raw(pp.get("check_date")),
              "frequency": _v(pp.get("frequency")) if (pp.get("frequency") or {}).get("source") != "salesforce" else None,
              "period_number": pp.get("period_number"), "summary_number": pp.get("summary_number"),
              "payroll_contact": pc.get("name"), "payroll_contact_phone": pc.get("phone"),
              "specialist": sp.get("name"), "specialist_phone": sp.get("phone")}
    n = rec["source"]["page_count"]
    kind = DOC_TO_KIND.get(d["doc_type"], "other")
    pages = {i: {"page": i, "orientation": "unknown", "page_kind": kind, "header": dict(header),
                 "code_legend": [{"code": c["code"], "description": c["description"],
                                  "category": c["category"] if c["category"] in ("earning", "deduction") else "other"}
                                 for c in rec.get("code_legend", [])],
                 "employees": [], "entries": [], "totals": [], "instructions": [], "time_punches": [],
                 "direct_deposit": [], "signatures": [], "printed_text": "", "handwritten_text": "", "issues": []}
             for i in range(1, n + 1)}
    for s in d.get("segments") or []:
        k = DOC_TO_KIND.get(s.get("doc_type"), s.get("doc_type") if s.get("doc_type") in DOC_TO_KIND.values() else None)
        for pg in s.get("pages", []):
            if pg in pages and k:
                pages[pg]["page_kind"] = k
    for t in rec.get("totals", []):
        if t.get("scope") == "cover_sheet" and t.get("page") in pages:
            pages[t["page"]]["page_kind"] = "fax_cover_sheet"
    for p in rec.get("pages", []):
        if p["page"] in pages:
            pages[p["page"]]["printed_text"] = p.get("printed_text") or ""
            pages[p["page"]]["handwritten_text"] = p.get("handwritten_text") or ""
    emp_page = {}
    for e in rec["employees"]:
        pg = e.get("page") if e.get("page") in pages else 1
        key = e.get("employee_number") or e["name"]["full"]
        emp_page[e["employee_ref"]] = (pg, key)
        pf = {k: _fmt(v) for k, v in (e.get("attributes") or {}).items()}
        marks = ["Remove"] if e.get("status") == "remove_requested" else []
        pages[pg]["employees"].append({"row_key": key, "name": e["name"]["full"], "employee_number": e.get("employee_number"),
                                       "clock_number": e.get("clock_number"), "pay_type": e.get("pay_type"), "printed_fields": pf,
                                       "recurring_deductions": [{"code": r["code"], "amount_text": _fmt(r.get("amount")),
                                                                 "ytd_text": _fmt(r.get("ytd")), "balance_text": _fmt(r.get("balance")),
                                                                 "change_written": r.get("change_requested")}
                                                                for r in e.get("recurring_deductions") or []],
                                       "marks": marks})
    for p in rec["pay_items"]:
        if p["provenance"]["source"] == "email":
            continue
        pg = p["provenance"].get("page") if p["provenance"].get("page") in pages else 1
        q = p.get("quantity") or {}
        if p["item_type"] == "deduction":
            col, val = "deduction", p.get("amount")
        elif p["item_type"] == "exception":
            col, val = "exception_rate", p.get("rate")
        elif q.get("value") is not None:
            col = {"1": "regular", "2": "overtime"}.get(p.get("code") or "", "other_hours")
            val = q["value"]
        else:
            col, val = "earnings", p.get("amount")
        raw = p["provenance"].get("raw_text")
        vt = _fmt(val)
        if raw and val is not None:  # keep the written form only when it parses back to the reference value
            pr = parse_quantity(raw)
            if pr.value is not None and abs(pr.value - float(val)) < 1e-9:
                vt = raw
        if q.get("unit") == "days" and "day" not in (vt or ""):
            vt = f"{_fmt(val)} days"
        if not vt and "salary_marker" in (p.get("review_reasons") or []):
            vt = "Pay"
        ref = p.get("employee_ref")
        key = emp_page.get(ref, (pg, p.get("employee_name")))[1]
        if ref in emp_page and emp_page[ref][0] != pg:
            # employee block printed on another page (multi-line employees): repeat a minimal block
            if not any(e["row_key"] == key for e in pages[pg]["employees"]):
                pages[pg]["employees"].append({"row_key": key, "name": p.get("employee_name") or key, "employee_number": None})
        elif ref is None and not any(e["row_key"] == key for e in pages[pg]["employees"]):
            pages[pg]["employees"].append({"row_key": key, "name": p.get("employee_name") or "(unnamed)", "employee_number": None})
        pages[pg]["entries"].append({"row_key": key, "column": col,
                                     "code_written": None if col in ("regular", "overtime") else p.get("code"),
                                     "label_written": None, "value_text": vt or "", "rate_text": _fmt(p.get("rate")) or None,
                                     "amount_text": _fmt(p.get("amount")) if col in ("regular", "overtime", "other_hours") and p.get("amount") is not None else None,
                                     "calculation_text": p.get("calculation_shown"), "work_date_text": p.get("work_date"),
                                     "source": p["provenance"]["source"] if p["provenance"]["source"] in ("handwritten", "printed", "typed") else "handwritten",
                                     "struck_through": p["status"] == "struck_through",
                                     "legibility": {"high": "clear", "medium": "uncertain", "low": "illegible"}.get(p.get("confidence"), "clear"),
                                     "location": p["provenance"].get("location"), "note": p.get("comment")})
    for t in rec.get("totals", []):
        pg = t.get("page") if t.get("page") in pages else n
        col = t.get("column") or ({"1": "regular", "2": "overtime"}.get(t.get("code") or "") or
                                   ("other_hours" if t.get("measure") == "hours" and t.get("code") else
                                    "total_hours" if t.get("measure") == "hours" else
                                    "earnings" if t.get("measure") == "amount" else "other"))
        pages[pg]["totals"].append({"column": col, "code_written": None if col in ("regular", "overtime") else t.get("code"),
                                    "label": t.get("label"), "value_text": _fmt(t["document_value"]),
                                    "source": t.get("source") if t.get("source") in ("handwritten", "printed", "typed") else "handwritten"})
    return pages


class ReplayLLMClient:
    def __init__(self, jsonl_path: Path):
        self.records = {}
        for line in open(jsonl_path, encoding="utf-8"):
            if line.strip():
                r = json.loads(line)
                self.records[r["record_id"]] = r
        self._pages: dict[str, dict] = {}
        self.calls = 0

    def call_tool(self, *, model, system, content, tool_name, tool_description, input_schema, max_tokens):
        self.calls += 1
        text = "\n".join(c.get("text", "") for c in content if c.get("type") == "text")
        if tool_name == "record_page":
            rid = RID.search(text).group(1); page = int(PAGE.search(text).group(1))
            rec = self.records.get(rid)
            if rec is None:
                raise KeyError(f"replay has no record {rid}")
            if rid not in self._pages:
                self._pages[rid] = record_to_pages(rec)
            data = self._pages[rid][page]
        elif tool_name == "classify_document":
            rid = next((r for r in self.records if r in text), None)
            data = {"doc_type": self.records[rid]["document"]["doc_type"] if rid else "other", "reason": "replay"}
        elif tool_name == "record_amendments":
            m = re.search(r"Attachment: (\S+?)\.pdf", text)
            rec = self.records.get(m.group(1)) if m else None
            data = {"amendments": []}
            if rec:
                # map reference item ids to the ids the pipeline assigned (prompt carries the pipeline's list)
                pm = re.search(r"Pay items extracted from the attachment:\n(\[.*?\])\n\nEmail thread:", text, re.S)
                current = json.loads(pm.group(1)) if pm else []
                used, idmap = set(), {}
                for p in rec["pay_items"]:
                    if p["provenance"]["source"] == "email":
                        continue
                    q = (p.get("quantity") or {}).get("value")
                    for c in current:
                        if c["item_id"] not in used and c["employee"] == p.get("employee_name") and c["code"] == p.get("code") \
                                and c["qty"] == q and c["amount"] == p.get("amount"):
                            idmap[p["item_id"]] = c["item_id"]; used.add(c["item_id"]); break
                orig = set(idmap)
                for a in (rec.get("salesforce_verification") or {}).get("email_amendments", []):
                    added = [p for p in rec["pay_items"] if p["item_id"] in a.get("affects", []) and p["provenance"]["source"] == "email"]
                    nv = None
                    if added:
                        q = added[0].get("quantity") or {}
                        nv = {"code_text": added[0].get("code"), "hours_text": _fmt(q.get("value")) or None,
                              "amount_text": _fmt(added[0].get("amount")) or None, "rate_text": _fmt(added[0].get("rate")) or None}
                        olds = [p for p in rec["pay_items"] if p["item_id"] in a.get("affects", []) and p["provenance"]["source"] != "email"]
                        if a["action"] == "modify" and olds:
                            # only the values the email changed (those that differ from the superseded item)
                            o, oq = olds[0], (olds[0].get("quantity") or {})
                            if added[0].get("code") == o.get("code"): nv["code_text"] = None
                            if q.get("value") == oq.get("value"): nv["hours_text"] = None
                            if added[0].get("amount") == o.get("amount"): nv["amount_text"] = None
                            if added[0].get("rate") == o.get("rate"): nv["rate_text"] = None
                    data["amendments"].append({"email_message_id": a.get("email_message_id") or "unknown", "action": a["action"],
                                               "instruction_quote": a["instruction"],
                                               "target_item_ids": [idmap[x] for x in a.get("affects", []) if x in orig],
                                               "employee_name": added[0]["employee_name"] if added else None,
                                               "new_value": nv, "relates_to_this_attachment": True})
        else:
            raise ValueError(tool_name)
        return ToolResult(data=json.loads(json.dumps(data)), usage={"input_tokens": 0, "output_tokens": 0},
                          model=f"replay:{model}", stop_reason="tool_use")
