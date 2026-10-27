"""Stage 11: JSON Schema v2 + business rules + review routing. Writes the final record.json."""
from __future__ import annotations

import json
from functools import lru_cache

from jsonschema import Draft202012Validator

from ..store import now
from ..values import freq_letter
from .base import Context, StageSkip

PAYABLE = {"submitted", "printed_default", "added_from_email"}


@lru_cache(maxsize=2)
def _validator(path: str) -> Draft202012Validator:
    return Draft202012Validator(json.loads(open(path).read()))


def business_rules(rec: dict, max_hours: dict) -> list[dict]:
    out = []
    doc = rec["document"]
    fl = freq_letter(((doc.get("pay_period") or {}).get("frequency") or {}).get("value"))
    cap = max_hours.get(fl or "", None)
    per = {}
    for p in rec["pay_items"]:
        if p["status"] not in PAYABLE:
            continue
        q = p.get("quantity") or {}
        if q.get("unit") == "hours" and q.get("value") is not None:
            if q["value"] < 0:
                out.append({"code": "NEGATIVE_HOURS", "severity": "error", "message": f"{p['employee_name']}: {q['value']} hours", "refs": [p["item_id"]]})
            key = (p.get("employee_ref") or p.get("employee_name"), p.get("code"))
            per[key] = per.get(key, 0) + q["value"]
    if cap:
        # Every hours code is checked, not only regular: 322 OVERTIME hours used to pass unflagged.
        for (emp, code), hrs in per.items():
            if hrs > cap:
                out.append({"code": "HOURS_OVER_PERIOD", "severity": "warning",
                            "message": f"{emp}: {hrs} hours under code {code} exceeds {cap} for frequency {fl}", "refs": []})
        # An uncoded hours value at or above the cap is more likely money than hours. Routes to review; never changes it.
        for p in rec["pay_items"]:
            q = p.get("quantity") or {}
            if p["status"] in PAYABLE and not p.get("code") and q.get("unit") == "hours" and (q.get("value") or 0) >= cap:
                out.append({"code": "HOURS_LOOK_LIKE_DOLLARS", "severity": "warning",
                            "message": f"{p['employee_name']}: uncoded {q['value']} hours is at or above the {cap} cap; may be dollars",
                            "refs": [p["item_id"]]})
    if rec["document"]["doc_type"] == "coad_payroll_timesheet_report":
        pp = doc.get("pay_period") or {}
        for f in ("start", "end", "check_date"):
            if not (pp.get(f) or {}).get("value"):
                out.append({"code": "MISSING_HEADER", "severity": "warning", "message": f"pay_period.{f} missing", "refs": []})
        if not ((doc.get("client") or {}).get("number") or {}).get("value"):
            out.append({"code": "MISSING_HEADER", "severity": "warning", "message": "client number missing", "refs": []})
    return out


def route(rec: dict) -> tuple[str, list[str]]:
    reasons = []
    if any(t.get("reconciles") is False for t in rec.get("totals", [])):
        reasons.append("totals_not_reconciled")
    sv = rec.get("salesforce_verification") or {}
    if any(c["result"] == "mismatch" for c in sv.get("checks", [])):
        reasons.append("salesforce_mismatch")
    if not sv:
        reasons.append("salesforce_not_checked")
    if any(p["status"] in PAYABLE and p.get("confidence") != "high" for p in rec["pay_items"]):
        reasons.append("uncertain_payable_value")
    if any(p["status"] in PAYABLE and ("no_code" in p["review_reasons"] or "salary_amount_unknown" in p["review_reasons"]
                                       or "unparsed_value" in p["review_reasons"]) for p in rec["pay_items"]):
        reasons.append("unresolved_pay_item")
    for code in ("AMENDMENT_UNRESOLVED", "EMAILS_NOT_READ", "AMENDMENTS_INVALID", "UNLINKED_ENTRY", "HEADER_CONFLICT",
                 "NEGATIVE_HOURS", "HOURS_OVER_PERIOD", "HOURS_LOOK_LIKE_DOLLARS", "MISSING_HEADER", "SF_NOT_CHECKED"):
        if any(f.get("code") == code for f in rec["review_flags"]):
            reasons.append(code.lower())
    if any(a["action"] in ("add", "modify", "exclude") for a in sv.get("email_amendments", [])):
        reasons.append("email_amendments_applied")
    return ("auto_ok" if not reasons else "needs_review"), reasons


def run(ctx: Context, rec, entry: dict) -> None:
    if not rec.has("amended.json"):
        raise StageSkip("amendments stage did not run")
    r = rec.read("amended.json")
    r["review_flags"] = [f for f in r["review_flags"] if f.get("code") not in
                         ("NEGATIVE_HOURS", "HOURS_OVER_PERIOD", "HOURS_LOOK_LIKE_DOLLARS", "MISSING_HEADER")] + business_rules(r, ctx.cfg.max_hours_per_code)
    status, reasons = route(r)
    r["extraction"].update({"review_status": status, "review_reasons": reasons, "validated_at": now(),
                            "batch": ctx.run.run_id})
    errs = [f"{'/'.join(map(str, e.path))}: {e.message[:200]}" for e in _validator(str(ctx.cfg.record_schema_path)).iter_errors(r)]
    rec.write("validation.json", {"schema_valid": not errs, "errors": errs[:50], "review_status": status,
                                  "review_reasons": reasons, "at": now()})
    if errs:
        r["extraction"]["review_status"] = "failed"
        rec.write("record.json", r)
        raise RuntimeError(f"record failed schema validation ({len(errs)} errors): {errs[0]}")
    rec.write("record.json", r)
