"""Stage 2 (per case): read-only Salesforce snapshot used by sf_verify and amendments.

Snapshot format (runs/<run>/sf/<case>.json):
{case:{id, case_number, subject, created, contact_name, supplied_email, account_id},
 account:{id, name, dba, employer_unique_id, payroll_frequency, unique_ee_paid},
 content_documents:{<069 id>: {title, extension, content_size}},
 contacts:[{name, title, phone, email}],
 emails:[{id, date, from, incoming, has_attachment, text}], queried_at}
"""
from __future__ import annotations

import logging

from ..store import write_json, read_json, now
from .base import Context

log = logging.getLogger(__name__)

Q_CASE = """SELECT Id, CaseNumber, Subject, CreatedDate, AccountId, Account.Name, Account.d_b_a__c,
Account.EmployerUnique_ID__c, Account.Payroll_Frequency__c, Account.Unique_EE_Paid__c, Contact.Name, SuppliedEmail
FROM Case WHERE CaseNumber IN ({nums})"""
Q_DOCS = "SELECT Id, Title, FileExtension, ContentSize FROM ContentDocument WHERE Id IN ({ids})"
Q_CONTACTS = "SELECT AccountId, Name, Title, Phone, Email FROM Contact WHERE AccountId IN ({ids})"
Q_EMAILS = """SELECT Id, ParentId, MessageDate, FromAddress, Incoming, HasAttachment, TextBody
FROM EmailMessage WHERE ParentId IN ({ids}) ORDER BY MessageDate"""


def _q(ids):
    return ",".join(f"'{i}'" for i in ids)


def snapshot_cases(ctx: Context, refresh: bool = False) -> dict[str, str]:
    """Write one snapshot per case in the manifest. Returns {case_number: status}."""
    manifest = [m for m in ctx.run.manifest() if m.get("case_number")]
    cases = sorted({m["case_number"] for m in manifest})
    todo = [c for c in cases if refresh or not ctx.run.sf_path(c).exists()]
    status = {c: "cached" for c in cases if c not in todo}
    if not todo:
        return status
    if ctx.sf is None:
        for c in todo:
            status[c] = "no_salesforce_client"
        log.warning("Salesforce client not configured; %d cases have no snapshot", len(todo))
        return status
    docs_by_case: dict[str, list[str]] = {}
    for m in manifest:
        if m.get("content_document_id"):
            docs_by_case.setdefault(m["case_number"], []).append(m["content_document_id"])
    for i in range(0, len(todo), 50):
        chunk = todo[i:i + 50]
        case_rows = ctx.sf.query_all(Q_CASE.format(nums=_q(chunk)))
        acct_ids = sorted({r["AccountId"] for r in case_rows if r.get("AccountId")})
        contacts = ctx.sf.query_all(Q_CONTACTS.format(ids=_q(acct_ids))) if acct_ids else []
        emails = ctx.sf.query_all(Q_EMAILS.format(ids=_q([r["Id"] for r in case_rows]))) if case_rows else []
        doc_ids = [d for c in chunk for d in docs_by_case.get(c, [])]
        docs = ctx.sf.query_all(Q_DOCS.format(ids=_q(doc_ids))) if doc_ids else []
        docs_map = {d["Id"]: {"title": d.get("Title"), "extension": d.get("FileExtension"), "content_size": d.get("ContentSize")} for d in docs}
        for r in case_rows:
            acct = r.get("Account") or {}
            snap = {
                "queried_at": now(),
                "case": {"id": r["Id"], "case_number": r["CaseNumber"], "subject": r.get("Subject"), "created": r.get("CreatedDate"),
                         "contact_name": (r.get("Contact") or {}).get("Name"), "supplied_email": r.get("SuppliedEmail"),
                         "account_id": r.get("AccountId")},
                "account": {"id": r.get("AccountId"), "name": acct.get("Name"), "dba": acct.get("d_b_a__c"),
                            "employer_unique_id": acct.get("EmployerUnique_ID__c"),
                            "payroll_frequency": acct.get("Payroll_Frequency__c"), "unique_ee_paid": acct.get("Unique_EE_Paid__c")},
                "content_documents": {d: docs_map[d] for d in docs_by_case.get(r["CaseNumber"], []) if d in docs_map},
                "contacts": [{"name": c.get("Name"), "title": c.get("Title"), "phone": c.get("Phone"), "email": c.get("Email")}
                             for c in contacts if c.get("AccountId") == r.get("AccountId")],
                "emails": [{"id": e["Id"], "date": e.get("MessageDate"), "from": e.get("FromAddress"), "incoming": e.get("Incoming"),
                            "has_attachment": e.get("HasAttachment"), "text": e.get("TextBody") or ""}
                           for e in emails if e.get("ParentId") == r["Id"]],
            }
            write_json(ctx.run.sf_path(r["CaseNumber"]), snap)
            status[r["CaseNumber"]] = "fetched"
        for c in chunk:
            status.setdefault(c, "case_not_found")
    return status


def snapshots_from_records(ctx: Context, jsonl_path) -> int:
    """Offline snapshots rebuilt from verified v2 records (for replay dry runs without Salesforce credentials).
    Contacts are unknown (contact check is skipped); one synthetic email per recorded amendment."""
    import json
    n = 0
    for line in open(jsonl_path, encoding="utf-8"):
        if not line.strip():
            continue
        r = json.loads(line)
        sv = r.get("salesforce_verification") or {}
        acct, case, cdoc = sv.get("account") or {}, sv.get("case") or {}, sv.get("content_document") or {}
        # batch-1 records were size-verified against Salesforce but did not store ContentSize; reuse byte_size
        size = cdoc.get("content_size") or r["source"].get("byte_size")
        snap = {"queried_at": sv.get("queried_at"), "synthetic_from_record": r["record_id"],
                "case": {"id": case.get("id"), "case_number": r["source"]["case_number"], "subject": case.get("subject"),
                         "contact_name": case.get("contact_name"), "supplied_email": case.get("supplied_email")},
                "account": {"id": acct.get("id"), "name": acct.get("name"), "dba": None,
                            "employer_unique_id": acct.get("employer_unique_id"), "payroll_frequency": acct.get("payroll_frequency"),
                            "unique_ee_paid": acct.get("unique_ee_paid")},
                "content_documents": {r["source"]["content_document_id"]: {"title": cdoc.get("title"), "content_size": size}},
                "contacts": None,
                "emails": [{"id": a.get("email_message_id") or f"synthetic-{i}", "date": a.get("message_date"), "from": a.get("from"),
                            "incoming": True, "has_attachment": False, "text": a["instruction"]}
                           for i, a in enumerate(sv.get("email_amendments", []))]}
        p = ctx.run.sf_path(r["source"]["case_number"])
        if p.exists():  # several records on one case: merge
            old = read_json(p)
            old["content_documents"].update(snap["content_documents"])
            ids = {e["id"] for e in old["emails"]}
            old["emails"] += [e for e in snap["emails"] if e["id"] not in ids]
            snap = old
        write_json(p, snap)
        n += 1
    return n


def load_snapshot(ctx: Context, case_number: str | None) -> dict | None:
    if not case_number:
        return None
    p = ctx.run.sf_path(case_number)
    return read_json(p) if p.exists() else None
