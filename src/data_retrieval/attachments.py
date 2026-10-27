"""Attachment extraction and file writing."""

import logging
from pathlib import Path
from typing import List, Dict, Any
from .sf_client import SalesforceReadOnlyClient
from .queries import CONTENT_DOCUMENT_LINKS_BY_EMAIL
from .config import Config

logger = logging.getLogger(__name__)


def fetch_attachment_metadata(sf: SalesforceReadOnlyClient, email_ids: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    """Fetch ContentDocumentLink metadata for a batch of EmailMessage IDs.

    Args:
        sf: SalesforceReadOnlyClient instance.
        email_ids: List of EmailMessage IDs.

    Returns:
        Dictionary mapping EmailMessage ID to list of attachment metadata.
    """
    if not email_ids:
        return {}

    # Chunk email IDs to avoid query size limits (Salesforce allows ~100 IN clause items)
    chunk_size = 50
    all_links = []

    for i in range(0, len(email_ids), chunk_size):
        chunk = email_ids[i : i + chunk_size]
        email_ids_str = ",".join([f"'{eid}'" for eid in chunk])
        query = CONTENT_DOCUMENT_LINKS_BY_EMAIL.format(email_ids=email_ids_str)
        links = sf.query(query)
        all_links.extend(links)

    # Group by LinkedEntityId (EmailMessage ID)
    attachments_by_email = {}
    for link in all_links:
        email_id = link.get("LinkedEntityId")
        if email_id not in attachments_by_email:
            attachments_by_email[email_id] = []
        attachments_by_email[email_id].append(link)

    logger.info(f"Fetched metadata for {len(all_links)} attachments across {len(attachments_by_email)} emails")
    return attachments_by_email


def download_attachment_file(
    sf: SalesforceReadOnlyClient,
    case_number: str,
    content_doc_id: str,
    version_id: str,
    title: str,
    file_type: str,
) -> str:
    """Download a single attachment and write to disk.

    Args:
        sf: SalesforceReadOnlyClient instance.
        case_number: Case number for organizing output.
        content_doc_id: ContentDocument ID.
        version_id: ContentVersion ID to download.
        title: Original filename.
        file_type: File extension.

    Returns:
        Path to the written file (relative to output dir).
    """
    # Construct filename: CaseNumber_ContentDocumentId.extension
    safe_title = title.replace("/", "_").replace("\\", "_")
    filename = f"{case_number}_{content_doc_id}.{file_type.lower()}"
    filepath = Config.OUTPUT_DIR / "attachments" / filename

    try:
        content = sf.get_attachment_content(version_id)
        if content:
            filepath.write_bytes(content)
            logger.info(f"Wrote attachment: {filepath}")
            return f"attachments/{filename}"
        else:
            logger.warning(f"No content retrieved for {filename}")
            return None
    except Exception as e:
        logger.error(f"Failed to download {filename}: {e}")
        return None


def extract_and_save_attachments(
    sf: SalesforceReadOnlyClient,
    cases: List[Dict[str, Any]],
) -> Dict[str, List[str]]:
    """Extract and save attachments for all cases.

    Args:
        sf: SalesforceReadOnlyClient instance.
        cases: List of Case records (with EmailId).

    Returns:
        Dictionary mapping Case ID to list of attachment file paths.
    """
    # Collect all EmailMessage IDs
    email_ids = [case.get("EmailId") for case in cases if case.get("EmailId")]
    email_ids = [eid for eid in email_ids if eid]  # filter None

    if not email_ids:
        logger.info("No email IDs found; skipping attachment fetch")
        return {}

    # Fetch attachment metadata
    attachments_by_email = fetch_attachment_metadata(sf, email_ids)

    # Download each attachment
    attachment_paths_by_case = {}
    for case in cases:
        case_id = case.get("Id")
        case_number = case.get("CaseNumber")
        email_id = case.get("EmailId")

        if email_id not in attachments_by_email:
            attachment_paths_by_case[case_id] = []
            continue

        attachment_paths = []
        for link in attachments_by_email[email_id]:
            content_doc = link.get("ContentDocument", {})
            doc_id = content_doc.get("Id")
            version_id = content_doc.get("LatestPublishedVersionId")
            title = content_doc.get("Title")
            file_type = content_doc.get("FileType")

            if version_id:
                path = download_attachment_file(sf, case_number, doc_id, version_id, title, file_type)
                if path:
                    attachment_paths.append(path)

        attachment_paths_by_case[case_id] = attachment_paths

    logger.info(f"Downloaded attachments for {len(attachment_paths_by_case)} cases")
    return attachment_paths_by_case
