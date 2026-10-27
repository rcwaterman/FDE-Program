"""The synthetic staging step must produce names the pipeline's ingest accepts, and nothing else."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src.pipeline.stages.ingest import NAME_RE  # noqa: E402
from src.synthetic.stage_synthetic import stage  # noqa: E402


def _source(tmp_path: Path) -> Path:
    src = tmp_path / "source"
    src.mkdir()
    for name in ("Payroll_Timesheet_syn3_001.jpeg", "Payroll_Timesheet_syn3_002.jpeg", "Payroll_Timesheet_Synthetic_1.pdf"):
        (src / name).write_bytes(b"not a real scan")
    (src / "notes.txt").write_text("not a timesheet")
    return src


def test_staged_names_pass_the_ingest_pattern(tmp_path):
    run_root = tmp_path / "run"
    mapping = stage(_source(tmp_path), run_root)
    assert len(mapping) == 3
    for row in mapping:
        assert NAME_RE.match(row["file_name"]), row["file_name"]
        assert (run_root / "attachments" / row["file_name"]).exists()


def test_staged_ids_are_visibly_synthetic(tmp_path):
    mapping = stage(_source(tmp_path), tmp_path / "run")
    for row in mapping:
        assert "069SYN" in row["record_id"]


def test_map_records_the_original_file_for_scoring(tmp_path):
    run_root = tmp_path / "run"
    stage(_source(tmp_path), run_root)
    lines = (run_root / "synthetic_map.jsonl").read_text().splitlines()
    originals = [json.loads(line)["original"] for line in lines]
    assert originals == ["Payroll_Timesheet_Synthetic_1.pdf", "Payroll_Timesheet_syn3_001.jpeg", "Payroll_Timesheet_syn3_002.jpeg"]


def test_limit_stages_only_the_first_files(tmp_path):
    assert len(stage(_source(tmp_path), tmp_path / "run", limit=1)) == 1
