"""JSONL export of case data."""

import json
import logging
from typing import List, Dict, Any
from .config import Config

logger = logging.getLogger(__name__)


def export_cases_to_jsonl(
    cases: List[Dict[str, Any]],
    attachment_paths_by_case: Dict[str, List[str]],
) -> None:
    """Export case data to JSONL format.

    Args:
        cases: List of Case records with embedded email data.
        attachment_paths_by_case: Mapping of Case ID to list of attachment file paths.
    """
    output_file = Config.OUTPUT_DIR / "sample_cases.jsonl"

    with open(output_file, "w") as f:
        for case in cases:
            case_id = case.get("Id")
            attachment_paths = attachment_paths_by_case.get(case_id, [])

            record = {
                "case_id": case_id,
                "case_number": case.get("CaseNumber"),
                "case_subject": case.get("Subject"),
                "case_status": case.get("Status"),
                "case_origin": case.get("Origin"),
                "case_created_date": case.get("CreatedDate"),
                "email_id": case.get("EmailId"),
                "email_subject": case.get("EmailSubject"),
                "email_from": case.get("FromAddress"),
                "email_to": case.get("ToAddress"),
                "email_cc": case.get("CcAddress"),
                "email_bcc": case.get("BccAddress"),
                "email_text_body": case.get("TextBody"),
                "email_html_body": case.get("HtmlBody"),
                "email_created_date": case.get("EmailCreatedDate"),
                "has_attachment": case.get("HasAttachment", False),
                "attachment_count": len(attachment_paths),
                "attachment_paths": attachment_paths,
            }
            f.write(json.dumps(record) + "\n")

    logger.info(f"Exported {len(cases)} cases to {output_file}")
