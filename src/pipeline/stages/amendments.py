"""Stage 10: email-thread amendments.

Reading (Claude): which instructions in the case emails add, change, exclude or confirm pay items.
Application (deterministic): validate references, normalize new values, change statuses, add items.
"""
from __future__ import annotations

import json
import re

from jsonschema import Draft202012Validator

from ..llm.client import text_block
from ..llm.prompts import AMEND_SYSTEM, PROMPT_VERSION
from ..store import now
from ..values import parse_quantity, parse_amount, name_key
from .base import Context, StageSkip
from .sf_snapshot import load_snapshot

CUT = re.compile(r"(^-{5,}\s*Original Message\s*-{5,}|^From:\s|^On .{5,80} wrote:|^Sent from my|^_{10,})", re.M)
BANNER = re.compile(r"ZjQcmQRYFpfptBannerStart.*?ZjQcmQRYFpfptBannerEnd", re.S)
URL = re.compile(r"<?https?://\S+>?")


def clean_email(text: str) -> str:
    """Strip quoted history, security banners, URLs and signature images; deterministic."""
    t = BANNER.sub("", text or "")
    m = CUT.search(t)
    if m:
        t = t[:m.start()]
    t = URL.sub("", t)
    t = re.sub(r"\[cid:[^\]]+\]|\[image[^\]]*\]", "", t, flags=re.I)
    lines = [l.rstrip() for l in t.splitlines() if not l.startswith(">")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


CODE_WORDS = {"reg": "1", "regular": "1", "ot": "2", "overtime": "2", "vacation": "23", "vac": "23",
              "holiday": "24", "sick": "25", "bonus": "19", "commission": "27"}


def _code(text, legend):
    if not text:
        return None
    s = text.strip()
    if any(c["code"].upper() == s.upper() for c in legend):
        return s
    for c in legend:
        if c["description"].lower().startswith(s.lower()[:4]):
            return c["code"]
    return CODE_WORDS.get(s.lower())


def apply_amendments(rec: dict, amendments: list[dict], snap_emails: dict) -> dict:
    items = {p["item_id"]: p for p in rec["pay_items"]}
    sv = rec.get("salesforce_verification") or {"checks": [], "email_amendments": []}
    rec["salesforce_verification"] = sv
    applied = []
    for a in amendments:
        em = snap_emails.get(a["email_message_id"], {})
        targets = [t for t in a.get("target_item_ids") or [] if t in items]
        unknown = [t for t in a.get("target_item_ids") or [] if t not in items]
        affects = list(targets)
        relates = a.get("relates_to_this_attachment", True)
        action = a["action"] if relates else "info"
        if unknown:
            rec["review_flags"].append({"code": "AMENDMENT_UNRESOLVED", "severity": "warning",
                                        "message": f"email {a['email_message_id']} references unknown items {unknown}", "refs": []})
        if action == "exclude":
            for t in targets:
                items[t]["status"] = "excluded"
                items[t]["review_reasons"].append("excluded_by_email")
        elif action in ("modify", "add"):
            nv = a.get("new_value") or {}
            q = parse_quantity(nv.get("hours_text")) if nv.get("hours_text") else None
            amt = parse_amount(nv.get("amount_text"))
            rate = parse_amount(nv.get("rate_text"))
            base = items[targets[0]] if targets else None
            if action == "modify" and not targets:
                rec["review_flags"].append({"code": "AMENDMENT_UNRESOLVED", "severity": "warning",
                                            "message": f"modify without target items: {a['instruction_quote'][:160]}", "refs": []})
                action = "info"
            elif (q is None or q.value is None) and amt is None and rate is None and not (action == "modify" and nv.get("code_text")):
                rec["review_flags"].append({"code": "AMENDMENT_UNRESOLVED", "severity": "warning",
                                            "message": f"{action} without a parseable value: {a['instruction_quote'][:160]}", "refs": affects})
                action = "info"
            else:
                # modify: restate each target item, overriding only the values the email gives;
                # add: one new item (base = first target if the model linked one, e.g. same employee)
                bases = [items[t] for t in targets] if action == "modify" else [base]
                for b in bases:
                    if action == "modify":
                        b["status"] = "superseded"
                    emp_ref = b["employee_ref"] if b else next(
                        (e["employee_ref"] for e in rec["employees"] if name_key(e["name"]["full"]) == name_key(a.get("employee_name") or "")), None)
                    new_id = f"P{len(rec['pay_items']) + 1:04d}"
                    new_code = _code(nv.get("code_text"), rec.get("code_legend", []))
                    rec["pay_items"].append({
                        "item_id": new_id, "employee_ref": emp_ref,
                        "employee_name": (b or {}).get("employee_name") or a.get("employee_name"),
                        "item_type": (b or {}).get("item_type", "earning"),
                        "code": new_code or (b or {}).get("code"),
                        "code_description": (nv.get("code_text") if new_code else None) or (b or {}).get("code_description") or nv.get("code_text"),
                        "quantity": {"value": q.value, "unit": q.unit or "hours"} if q and q.value is not None else (b or {}).get("quantity"),
                        "rate": rate if rate is not None else (b or {}).get("rate"),
                        "amount": amt if amt is not None else (b or {}).get("amount"),
                        "work_date": None, "dept_override": (b or {}).get("dept_override"), "calculation_shown": None,
                        "status": "added_from_email",
                        "provenance": {"source": "email", "page": None, "location": None, "raw_text": a["instruction_quote"][:500],
                                       "email_message_id": a["email_message_id"]},
                        "confidence": "high", "review_reasons": ["email_amendment"] + ([] if new_code or not nv.get("code_text") else ["code_text_unresolved"]),
                        "comment": f"replaces {b['item_id']}" if b and action == "modify" else None})
                    affects.append(new_id)
                    items[new_id] = rec["pay_items"][-1]
        entry = {"email_message_id": a["email_message_id"], "message_date": (em.get("date") or "")[:10] or None,
                 "from": em.get("from"), "instruction": a["instruction_quote"], "action": action, "affects": affects}
        sv["email_amendments"].append(entry)
        if action in ("add", "modify", "exclude"):
            rec["review_flags"].append({"code": "EMAIL_AMENDMENT", "severity": "info",
                                        "message": f"{action}: {a['instruction_quote'][:200]}", "refs": affects})
        applied.append(entry)
    return rec


def run(ctx: Context, rec, entry: dict) -> None:
    if not rec.has("verified.json"):
        raise StageSkip("not verified")
    r = rec.read("verified.json")
    snap = load_snapshot(ctx, entry.get("case_number"))
    emails = (snap or {}).get("emails") or []
    if not emails or ctx.llm is None or not ctx.cfg.llm_amendments:
        rec.write("amendments_raw.json", {"amendments": [], "skipped": "no emails" if not emails else "LLM amendments disabled", "at": now()})
        if emails:
            r["review_flags"].append({"code": "EMAILS_NOT_READ", "severity": "warning",
                                      "message": f"{len(emails)} case emails not checked for amendments", "refs": []})
        rec.write("amended.json", r)
        return
    thread = "\n\n".join(f"[email_message_id={e['id']}] {e.get('date')} from {e.get('from')} "
                         f"({'client' if e.get('incoming') else 'CoAd staff'}):\n{clean_email(e.get('text'))}" for e in emails)
    compact = [{"item_id": p["item_id"], "employee": p.get("employee_name"), "code": p.get("code"),
                "desc": p.get("code_description"), "qty": (p.get("quantity") or {}).get("value"),
                "unit": (p.get("quantity") or {}).get("unit"), "amount": p.get("amount"), "status": p["status"]}
               for p in r["pay_items"]]
    prompt = (f"Attachment: {r['source']['file_name']} (client {((r['document'].get('client') or {}).get('number') or {}).get('value')}).\n"
              f"Pay items extracted from the attachment:\n{json.dumps(compact, ensure_ascii=False)}\n\nEmail thread:\n{thread[:60000]}")
    schema = json.loads(ctx.cfg.amend_schema_path.read_text())
    res = ctx.llm.call_tool(model=ctx.cfg.amend_model, system=AMEND_SYSTEM, content=[text_block(prompt)],
                            tool_name="record_amendments", tool_description="Record pay-affecting instructions from the email thread.",
                            input_schema=schema, max_tokens=4000,
                            trace={"record_id": r["record_id"], "stage": "amendments"})
    rec.log_llm({"stage": "amendments", "model": res.model, "usage": res.usage, "latency_s": res.latency_s, "at": now()})
    errs = list(Draft202012Validator(schema).iter_errors(res.data))
    raw = {"prompt_version": PROMPT_VERSION, "model": res.model, "valid": not errs, "at": now(), **res.data}
    rec.write("amendments_raw.json", raw)
    if errs:
        r["review_flags"].append({"code": "AMENDMENTS_INVALID", "severity": "warning",
                                  "message": "amendment output failed schema validation; not applied", "refs": []})
        rec.write("amended.json", r)
        return
    rec.write("amended.json", apply_amendments(r, res.data["amendments"], {e["id"]: e for e in emails}))
