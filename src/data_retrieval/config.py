import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


class Config:
    # Salesforce credentials
    SF_USERNAME = os.getenv("SF_USERNAME")
    SF_PASSWORD = os.getenv("SF_PASSWORD")
    SF_SECURITY_TOKEN = os.getenv("SF_SECURITY_TOKEN")
    SF_DOMAIN = os.getenv("SF_DOMAIN", "login.salesforce.com")

    # Sample parameters
    SAMPLE_SIZE = int(os.getenv("SAMPLE_SIZE", 400))
    ATTACHMENT_RATIO = float(os.getenv("ATTACHMENT_RATIO", 0.7))
    MAX_CASES_TO_SCAN = int(os.getenv("MAX_CASES_TO_SCAN", 3000))
    # Default lives outside the repo so a Salesforce pull can never land under src/.
    OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", str(Path.home() / "fde_private" / "salesforce_pull")))

    # Derived
    ATTACHMENT_COUNT = int(SAMPLE_SIZE * ATTACHMENT_RATIO)
    PLAINTEXT_COUNT = SAMPLE_SIZE - ATTACHMENT_COUNT

    @staticmethod
    def setup_output_dirs():
        """Create output directories."""
        Config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        (Config.OUTPUT_DIR / "attachments").mkdir(parents=True, exist_ok=True)
