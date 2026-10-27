"""Stage 9: deterministic checks of the record against the Salesforce snapshot."""
from __future__ import annotations

from ..values import digits, freq_letter, token_ratio, FREQ_WORD
from .base import Context, StageSkip
from .sf_snapshot import load_snapshot


def _chk(field, extracted, sf, src, result, note=None):
    return {"field": field, "extracted": None if extracted is None else str(extracted),
            "salesforce": None if sf is None else str(sf), "sf_source": src, "result": result, "note": note}


def verify(rec: dict, snap: dict, name_threshold: float = 0.85) -> dict:
    acct, case = snap.get("account") or {}, snap.get("case") or {}
    doc = rec["document"]
    checks, flags = [], []
    # attachment identity
    cd = (snap.get("content_documents") or {}).get(rec["source"]["content_document_id"])
    size = rec["source"].get("byte_size")
    if cd:
        ok = cd.get("content_size") == size
        checks.append(_chk("Attachment identity", f"{size} bytes", f"{cd.get('title')!r}, {cd.get('content_size')} bytes",
                           "ContentDocument", "match" if ok else "mismatch"))
    else:
        checks.append(_chk("Attachment identity", f"{size} bytes", None, "ContentDocument", "info", "ContentDocument not in snapshot"))
    # client number
    num = ((doc.get("client") or {}).get("number") or {}).get("value")
    sfnum = acct.get("employer_unique_id")
    if num and sfnum:
        checks.append(_chk("Client number", num, sfnum, "Account.EmployerUnique_ID__c",
                           "match" if num.strip().upper() == sfnum.strip().upper() else "mismatch"))
    elif sfnum:
        doc.setdefault("client", {})["number"] = {"value": sfnum, "source": "salesforce", "page": None, "confidence": "high"}
        checks.append(_chk("Client number", None, sfnum, "Account.EmployerUnique_ID__c", "filled_from_salesforce"))
    # client name
    name = ((doc.get("client") or {}).get("name") or {}).get("value")
    sfname = acct.get("name")
    if name and sfname:
        r = max(token_ratio(name, sfname), token_ratio(name, acct.get("dba") or ""))
        res = "match" if r >= 0.97 else ("corroborated" if r >= name_threshold else "mismatch")
        checks.append(_chk("Client name", name, sfname + (f" (dba {acct['dba']})" if acct.get("dba") else ""), "Account.Name",
                           res, f"similarity {r:.2f}" if res != "match" else None))
    elif sfname:
        doc.setdefault("client", {})["name"] = {"value": sfname, "source": "salesforce", "page": None, "confidence": "high"}
        checks.append(_chk("Client name", None, sfname, "Account.Name", "filled_from_salesforce"))
    # frequency
    pp = doc.get("pay_period") or {}
    fl = freq_letter((pp.get("frequency") or {}).get("value"))
    sffl = freq_letter(acct.get("payroll_frequency"))
    if fl and sffl:
        checks.append(_chk("Pay frequency", fl, acct.get("payroll_frequency"), "Account.Payroll_Frequency__c",
                           "match" if fl == sffl else "mismatch"))
    elif sffl:
        pp["frequency"] = {"value": sffl, "source": "salesforce", "page": None, "confidence": "high"}
        checks.append(_chk("Pay frequency", None, acct.get("payroll_frequency"), "Account.Payroll_Frequency__c", "filled_from_salesforce"))
    # payroll contact
    pc = next((c for c in doc.get("contacts", []) if c["role"] == "payroll_contact"), None)
    if pc and snap.get("contacts") is not None:
        best = max(snap["contacts"], key=lambda c: token_ratio(pc["name"] or "", c.get("name") or ""), default=None)
        if best and token_ratio(pc["name"] or "", best.get("name") or "") >= name_threshold:
            ph_ok = not pc.get("phone") or digits(pc["phone"])[-10:] == digits(best.get("phone"))[-10:]
            owner = None
            if not ph_ok:
                owner = next((c["name"] for c in snap["contacts"] if digits(c.get("phone"))[-10:] == digits(pc["phone"])[-10:]), None)
            checks.append(_chk("Payroll contact", f"{pc['name']} {pc.get('phone') or ''}".strip(),
                               f"{best.get('name')} {best.get('phone') or ''}".strip(), "Contact",
                               "match" if ph_ok else "mismatch",
                               None if ph_ok else ("printed phone belongs to " + owner if owner else "phone differs")))
        else:
            checks.append(_chk("Payroll contact", pc["name"], None, "Contact", "mismatch", "no Contact with a matching name"))
    # employee count (info)
    ee = acct.get("unique_ee_paid")
    if ee is not None:
        n = sum(1 for e in rec["employees"] if e["status"] in ("active", "remove_requested"))
        far = ee and abs(n - ee) / max(ee, 1) > 0.5 and rec["document"]["doc_type"] == "coad_payroll_timesheet_report"
        checks.append(_chk("Employee count", n, ee, "Account.Unique_EE_Paid__c", "mismatch" if far else "info",
                           "roster differs from paid count by more than 50%" if far else None))
    for c in checks:
        if c["result"] == "mismatch":
            flags.append({"code": "SF_MISMATCH", "severity": "warning", "message": f"{c['field']}: document {c['extracted']!r} vs Salesforce {c['salesforce']!r}", "refs": []})
    rec["review_flags"] = [f for f in rec["review_flags"] if f.get("code") != "SF_MISMATCH"] + flags
    rec["salesforce_verification"] = {
        "queried_at": snap.get("queried_at"),
        "case": {"id": case.get("id"), "subject": case.get("subject"), "contact_name": case.get("contact_name"),
                 "supplied_email": case.get("supplied_email")},
        "account": {"id": acct.get("id"), "name": acct.get("name"), "employer_unique_id": sfnum,
                    "payroll_frequency": acct.get("payroll_frequency"), "unique_ee_paid": ee},
        "content_document": {"title": (cd or {}).get("title"), "content_size": (cd or {}).get("content_size"),
                             "size_matches": None if not cd else cd.get("content_size") == size},
        "checks": checks, "email_amendments": [],
    }
    return rec


def run(ctx: Context, rec, entry: dict) -> None:
    if not rec.has("reconciled.json"):
        raise StageSkip("not reconciled")
    r = rec.read("reconciled.json")
    snap = load_snapshot(ctx, entry.get("case_number"))
    if snap is None:
        r["review_flags"].append({"code": "SF_NOT_CHECKED", "severity": "warning", "message": "no Salesforce snapshot for this case", "refs": []})
    else:
        r = verify(r, snap, ctx.cfg.name_match_threshold)
    rec.write("verified.json", r)
