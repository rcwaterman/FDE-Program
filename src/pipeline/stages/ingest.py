"""Stage 1 (run level): build manifest.jsonl from a local folder or from Salesforce."""
from __future__ import annotations

import logging
import re
from pathlib import Path

from ..store import sha256_file, write_jsonl, now
from .base import Context
from .preprocess import ALLOWED_EXTENSIONS, IMAGE_EXTENSIONS

log = logging.getLogger(__name__)
NAME_RE = re.compile(r"^(?P<case>\d{8})_(?P<cdid>069[A-Za-z0-9]{15})\.(?P<ext>[A-Za-z0-9_]+)$")


def _entry(path: Path) -> dict:
    m = NAME_RE.match(path.name)
    rid = path.stem
    e = {"record_id": rid, "file_name": path.name, "path": str(path.resolve()),
         "case_number": m.group("case") if m else None, "content_document_id": m.group("cdid") if m else None,
         "extension": (m.group("ext") if m else path.suffix.lstrip(".")).lower(), "ingested_at": now()}
    if not m:
        e.update(status="skipped", reason="filename does not match <case>_<ContentDocumentId>.<ext>")
    else:
        e.update(status="ready", byte_size=path.stat().st_size, sha256=sha256_file(path))
    return e


def ingest_folder(ctx: Context, folder: Path, names: list[str] | None = None, limit: int | None = None) -> list[dict]:
    files = [folder / n for n in names] if names else sorted(p for p in folder.iterdir() if p.is_file())
    entries = [_entry(p) for p in files if p.exists()]
    if limit:
        ready = [e for e in entries if e["status"] == "ready"][:limit]
        entries = ready
    write_jsonl(ctx.run.manifest_path, entries)
    log.info("manifest: %d ready, %d skipped", sum(e["status"] == "ready" for e in entries),
             sum(e["status"] != "ready" for e in entries))
    return entries


CASES_WITH_EMAIL_ATTACHMENTS = """
SELECT Id, CaseNumber, CreatedDate
FROM Case
WHERE Origin = 'Email' AND CreatedDate >= {since}T00:00:00Z AND CreatedDate < {until}T00:00:00Z
  AND Sub_Select_a_Topic__c = 'Regular Payroll'
ORDER BY CreatedDate
"""

LINKS_FOR_EMAILS = """
SELECT ContentDocumentId, LinkedEntityId, ContentDocument.Title, ContentDocument.FileExtension,
       ContentDocument.ContentSize, ContentDocument.LatestPublishedVersionId
FROM ContentDocumentLink WHERE LinkedEntityId IN ({ids})
"""


def ingest_salesforce(ctx: Context, since: str, until: str, limit: int | None = None, case_numbers: list[str] | None = None) -> list[dict]:
    """Download PDF and image attachments on incoming case emails into runs/<run>/attachments/
    (read-only Salesforce access). Before download, anything that is not a PDF or image
    (ALLOWED_EXTENSIONS) is skipped, and so are images below cfg.min_image_bytes (logos, icons,
    tracking pixels). Skips are recorded in the manifest so the decisions stay auditable."""
    sf = ctx.sf
    min_image_bytes = ctx.cfg.min_image_bytes
    size_skipped = type_skipped = 0
    if sf is None:
        raise RuntimeError("Salesforce client not configured (SF_USERNAME / SF_PASSWORD / SF_SECURITY_TOKEN)")
    if case_numbers:
        ids = ",".join(f"'{c}'" for c in case_numbers)
        cases = sf.query_all(f"SELECT Id, CaseNumber, CreatedDate FROM Case WHERE CaseNumber IN ({ids})")
    else:
        query = CASES_WITH_EMAIL_ATTACHMENTS.format(since=since, until=until)
        if limit:
            query += f"LIMIT {limit}\n"
        cases = sf.query_all(query)
        log.info("salesforce ingest: %d case(s) in range %s..%s", len(cases), since, until)
        if limit and len(cases) == limit:
            log.warning("case count equals --limit (%d); results may be truncated -- "
                        "re-run without --limit to confirm you have the full range", limit)
    out_dir = ctx.run.root / "attachments"
    out_dir.mkdir(exist_ok=True)
    entries = []
    for i in range(0, len(cases), 50):
        chunk = cases[i:i + 50]
        by_id = {c["Id"]: c for c in chunk}
        ids = ",".join(f"'{c['Id']}'" for c in chunk)
        emails = sf.query_all(f"SELECT Id, ParentId FROM EmailMessage WHERE ParentId IN ({ids}) AND Incoming = true AND HasAttachment = true")
        if not emails:
            continue
        email_case = {e["Id"]: by_id[e["ParentId"]]["CaseNumber"] for e in emails}
        for j in range(0, len(emails), 50):
            eids = ",".join(f"'{e['Id']}'" for e in emails[j:j + 50])
            for link in sf.query_all(LINKS_FOR_EMAILS.format(ids=eids)):
                doc = link.get("ContentDocument") or {}
                ext = (doc.get("FileExtension") or "").lower()
                fname = f"{email_case[link['LinkedEntityId']]}_{link['ContentDocumentId']}.{ext}"
                dest = out_dir / fname
                size = doc.get("ContentSize") or 0
                if ext not in ALLOWED_EXTENSIONS:
                    type_skipped += 1
                    entries.append({"record_id": dest.stem, "file_name": fname, "case_number": email_case[link["LinkedEntityId"]],
                                    "content_document_id": link["ContentDocumentId"], "extension": ext,
                                    "status": "skipped", "reason": f"unsupported file type (.{ext}); only PDF and images are processed",
                                    "content_size": size})
                    continue
                if ext in IMAGE_EXTENSIONS and 0 < size < min_image_bytes:
                    size_skipped += 1
                    entries.append({"record_id": dest.stem, "file_name": fname, "case_number": email_case[link["LinkedEntityId"]],
                                    "content_document_id": link["ContentDocumentId"], "extension": ext,
                                    "status": "skipped", "reason": f"image below min size ({size} bytes < {min_image_bytes})",
                                    "content_size": size})
                    continue
                if not dest.exists():
                    data = sf.get_attachment_content(doc.get("LatestPublishedVersionId"))
                    if data:
                        dest.write_bytes(data)
                if dest.exists():
                    entries.append(_entry(dest))
                else:
                    entries.append({"record_id": dest.stem, "file_name": fname, "case_number": email_case[link["LinkedEntityId"]],
                                    "content_document_id": link["ContentDocumentId"], "extension": ext,
                                    "status": "skipped", "reason": "empty content returned by Salesforce"})
    # de-duplicate (same document linked to several emails)
    seen, uniq = set(), []
    for e in entries:
        if e["record_id"] not in seen:
            seen.add(e["record_id"]); uniq.append(e)
    write_jsonl(ctx.run.manifest_path, uniq)
    if type_skipped:
        log.info("salesforce ingest: skipped %d attachment(s) of unsupported file type", type_skipped)
    if size_skipped:
        log.info("salesforce ingest: skipped %d image(s) below %d bytes", size_skipped, min_image_bytes)
    return uniq
