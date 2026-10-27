"""Pipeline configuration from environment variables (.env supported)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # dotenv is optional for tests
    pass

PACKAGE_DIR = Path(__file__).resolve().parent
SCHEMA_DIR = PACKAGE_DIR / "schemas"


def _bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "y")


@dataclass
class PipelineConfig:
    runs_dir: Path = field(default_factory=lambda: Path(os.getenv("PIPELINE_RUNS_DIR", "runs")))
    render_dpi: int = int(os.getenv("PIPELINE_RENDER_DPI", 200))
    max_records: int = int(os.getenv("PIPELINE_MAX_RECORDS", 4))
    max_llm_concurrency: int = int(os.getenv("PIPELINE_MAX_LLM_CONCURRENCY", 6))

    extract_model: str = os.getenv("PIPELINE_EXTRACT_MODEL", "claude-sonnet-5-5")
    review_model: str = os.getenv("PIPELINE_REVIEW_MODEL", "claude-opus-5-5")
    classify_model: str = os.getenv("PIPELINE_CLASSIFY_MODEL", "claude-haiku-4-5-20251001")
    amend_model: str = os.getenv("PIPELINE_AMEND_MODEL", "claude-sonnet-5-5")
    extract_max_tokens: int = int(os.getenv("PIPELINE_EXTRACT_MAX_TOKENS", 16000))
    schema_retries: int = int(os.getenv("PIPELINE_SCHEMA_RETRIES", 2))
    api_max_retries: int = int(os.getenv("PIPELINE_API_MAX_RETRIES", 4))

    allow_pii_to_llm: bool = _bool("PIPELINE_ALLOW_PII_TO_LLM", False)
    # Vision classify for documents with no usable text layer (scanned/flattened PDFs, raster
    # images) -- the only reliable way to classify that subset; a deterministic image-matching
    # approach was tried and rejected after producing real false positives (see classify.py's
    # module docstring). On by default: without it, that entire subset would default to "other"
    # and never be extracted, regardless of what it actually is.
    llm_classify: bool = _bool("PIPELINE_LLM_CLASSIFY", True)
    llm_amendments: bool = _bool("PIPELINE_LLM_AMENDMENTS", True)

    # doc_types extract.py will actually run Claude extraction for; everything else is classified
    # (deterministically, for free) and then skipped before any extraction Claude call is made.
    # Comma-separated env var so new templates can be added without a code change.
    target_doc_types: frozenset = field(default_factory=lambda: frozenset(
        t.strip() for t in os.getenv("PIPELINE_TARGET_DOC_TYPES", "coad_payroll_timesheet_report").split(",") if t.strip()))

    # Output format for the final deliverable. "json" (default) keeps the v2 record as the only output;
    # "csv" also writes one import-template CSV per submission (stage import_csv). A run remembers
    # the format it was first run with in runs/<id>/output_format.json; the CLI flag overrides it.
    output_format: str = os.getenv("PIPELINE_OUTPUT_FORMAT", "json")
    # Appended after the template's columns. The default charter template has none; the older
    # import_template_for_automation has six. Turn off if the importer rejects extra columns;
    # the extras are still written to the meta sidecar.
    csv_extra_columns: bool = _bool("PIPELINE_CSV_EXTRA_COLUMNS", True)

    # Per-client conventions. Only confirmed rules reach Claude unless candidates are enabled.
    client_rules_path: Path = Path(os.getenv("PIPELINE_CLIENT_RULES_PATH", str(PACKAGE_DIR / "client_rules.json")))
    client_rules_include_candidates: bool = _bool("PIPELINE_CLIENT_RULES_INCLUDE_CANDIDATES", False)

    record_schema_path: Path = SCHEMA_DIR / "record_v2.schema.json"
    page_schema_path: Path = SCHEMA_DIR / "page_extraction.schema.json"
    csv_page_schema_path: Path = SCHEMA_DIR / "page_extraction_csv.schema.json"
    amend_schema_path: Path = SCHEMA_DIR / "email_amendments.schema.json"
    import_map_path: Path = SCHEMA_DIR / "import_column_map.json"

    # Salesforce (existing read-only client)
    sf_username: str | None = os.getenv("SF_USERNAME")
    sf_password: str | None = os.getenv("SF_PASSWORD")
    sf_security_token: str | None = os.getenv("SF_SECURITY_TOKEN")
    sf_domain: str = os.getenv("SF_DOMAIN", "login.salesforce.com")

    # Salesforce ingest: image attachments smaller than this many bytes (email-signature logos,
    # tracking pixels, inline icons) are skipped before download. Non-image files are never
    # size-filtered. 0 disables the filter.
    min_image_bytes: int = int(os.getenv("PIPELINE_MIN_IMAGE_BYTES", 20000))

    # Business-rule thresholds
    max_hours_per_code: dict = field(default_factory=lambda: {"W": 40.0, "B": 80.0, "S": 88.0, "M": 176.0})
    name_match_threshold: float = 0.85

    def snapshot(self) -> dict:
        d = asdict(self)
        for k in ("sf_password", "sf_security_token"):
            d[k] = "***" if d.get(k) else None
        return {k: (str(v) if isinstance(v, Path) else sorted(v) if isinstance(v, (set, frozenset)) else v)
                for k, v in d.items()}
