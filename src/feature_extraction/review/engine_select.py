"""Pick the review engine for a run: "json" (v2 records, the original app) or "csv" (import-template rows).

Precedence: --engine flag > the run's output_format.json marker > presence of import_csv/_manifest.json > json.
"""
import json
from pathlib import Path


def select_engine(run_root: Path, override: str = "auto") -> str:
    if override in ("json", "csv"):
        return override
    marker = run_root / "output_format.json"
    if marker.exists():
        fmt = json.loads(marker.read_text(encoding="utf-8")).get("output_format")
        if fmt in ("json", "csv"):
            return fmt
    if (run_root / "import_csv" / "_manifest.json").exists():
        return "csv"
    return "json"
