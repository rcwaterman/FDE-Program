"""Sample case selection logic."""

import logging
from typing import List, Dict, Any
from .sf_client import SalesforceReadOnlyClient
from .queries import CASES_FOR_SAMPLING, EMAIL_MESSAGES_BY_IDS
from .config import Config

logger = logging.getLogger(__name__)

BATCH_SIZE = 200


def _batch_ids(ids: List[str], batch_size: int = BATCH_SIZE) -> List[List[str]]:
    """Split a list of IDs into batches."""
    return [ids[i:i + batch_size] for i in range(0, len(ids), batch_size)]


def _merge_case_with_email(case: Dict[str, Any], email_lookup: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Merge EmailMessage data into Case record using flattened field names."""
    source_id = case.get("SourceId")
    if not source_id or source_id not in email_lookup:
        return None

    email = email_lookup[source_id]
    merged = {
        "Id": case["Id"],
        "CaseNumber": case["CaseNumber"],
        "Subject": case["Subject"],
        "Status": case["Status"],
        "Origin": case["Origin"],
        "CreatedDate": case["CreatedDate"],
        "SourceId": source_id,
        "EmailId": email.get("Id"),
        "EmailSubject": email.get("Subject"),
        "FromAddress": email.get("FromAddress"),
        "ToAddress": email.get("ToAddress"),
        "CcAddress": email.get("CcAddress"),
        "BccAddress": email.get("BccAddress"),
        "TextBody": email.get("TextBody"),
        "HtmlBody": email.get("HtmlBody"),
        "EmailCreatedDate": email.get("CreatedDate"),
        "HasAttachment": email.get("HasAttachment"),
    }
    return merged


def get_sample_case_ids(sf: SalesforceReadOnlyClient) -> List[Dict[str, Any]]:
    """Fetch sample cases: 70% with attachments, 30% plain text.

    Queries Cases first (selective filters), then fetches matching EmailMessages in batches.
    Merges data and splits by HasAttachment, preserving most-recent-first order.

    Args:
        sf: SalesforceReadOnlyClient instance.

    Returns:
        List of merged Case+EmailMessage records, ordered by creation date DESC.
    """
    attachment_count = Config.ATTACHMENT_COUNT
    plaintext_count = Config.PLAINTEXT_COUNT

    logger.info(f"Fetching up to {Config.MAX_CASES_TO_SCAN} cases for sampling...")
    cases = sf.query_all(CASES_FOR_SAMPLING.format(limit=Config.MAX_CASES_TO_SCAN))
    logger.info(f"Retrieved {len(cases)} case records")

    source_ids = [c.get("SourceId") for c in cases if c.get("SourceId")]
    if not source_ids:
        logger.warning("No cases with SourceId found")
        return []

    logger.info(f"Fetching EmailMessage data for {len(source_ids)} emails (in batches of {BATCH_SIZE})...")
    email_lookup = {}
    for batch in _batch_ids(source_ids):
        email_ids_str = ",".join(f"'{id}'" for id in batch)
        batch_emails = sf.query(EMAIL_MESSAGES_BY_IDS.format(email_ids=email_ids_str))
        for email in batch_emails:
            email_lookup[email["Id"]] = email
        logger.info(f"  Batch complete: {len(email_lookup)} emails loaded so far")

    logger.info(f"Merging Case and EmailMessage data...")
    merged_cases = []
    unmatched_count = 0
    for case in cases:
        merged = _merge_case_with_email(case, email_lookup)
        if merged:
            merged_cases.append(merged)
        else:
            unmatched_count += 1

    if unmatched_count:
        logger.warning(f"Could not match {unmatched_count} case(s) to EmailMessage records")

    logger.info(f"Splitting by attachment status...")
    with_attachment = [c for c in merged_cases if c.get("HasAttachment")]
    without_attachment = [c for c in merged_cases if not c.get("HasAttachment")]

    logger.info(f"Available: {len(with_attachment)} with attachments, {len(without_attachment)} without")

    if len(with_attachment) < attachment_count:
        logger.warning(f"Only {len(with_attachment)} cases with attachments available (need {attachment_count})")
    if len(without_attachment) < plaintext_count:
        logger.warning(f"Only {len(without_attachment)} plain-text cases available (need {plaintext_count})")

    sampled_with = with_attachment[:attachment_count]
    sampled_without = without_attachment[:plaintext_count]

    all_cases = sampled_with + sampled_without
    logger.info(f"Total sample: {len(all_cases)} cases ({len(sampled_with)} with attachments, {len(sampled_without)} plain text)")

    return all_cases
