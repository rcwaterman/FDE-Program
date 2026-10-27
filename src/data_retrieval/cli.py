"""CLI entrypoint for sample extraction."""

import logging
import sys
from .config import Config
from .sf_client import SalesforceReadOnlyClient
from .sampler import get_sample_case_ids
from .attachments import extract_and_save_attachments
from .export import export_cases_to_jsonl

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    """Main extraction workflow."""
    try:
        # Validate configuration
        logger.info("Validating configuration...")
        Config.setup_output_dirs()

        # Connect to Salesforce
        logger.info("Connecting to Salesforce...")
        sf = SalesforceReadOnlyClient(
            username=Config.SF_USERNAME,
            password=Config.SF_PASSWORD,
            security_token=Config.SF_SECURITY_TOKEN,
            domain=Config.SF_DOMAIN,
        )

        # Sample cases
        logger.info("Sampling cases...")
        cases = get_sample_case_ids(sf)
        if not cases:
            logger.error("No cases found; exiting")
            sys.exit(1)

        # Extract and download attachments
        logger.info("Extracting attachments...")
        attachment_paths_by_case = extract_and_save_attachments(sf, cases)

        # Export to JSONL
        logger.info("Exporting to JSONL...")
        export_cases_to_jsonl(cases, attachment_paths_by_case)

        logger.info("✓ Sample extraction complete")
        logger.info(f"  Output directory: {Config.OUTPUT_DIR.resolve()}")
        logger.info(f"  Cases: {len(cases)}")
        logger.info(f"  Attachments: {sum(len(p) for p in attachment_paths_by_case.values())}")

    except Exception as e:
        logger.error(f"Extraction failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
