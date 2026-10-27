"""Copy the synthetic timesheets into a run's attachments folder, renamed to the shape the pipeline expects.

The pipeline only accepts <8-digit case>_069<15 chars>.<ext> (see pipeline/stages/ingest.py). Synthetic files get
the same shape, with the 15-char tail written as SYN plus 12 digits, so no staged name can be mistaken for a real
Salesforce id. The review app reads images from the same attachments folder, so staging also makes them viewable.

Writes <run>/synthetic_map.jsonl (staged record_id -> original file name) for scoring against row_labels.jsonl.

Usage:
    python -m src.synthetic.stage_synthetic --run-id <id> [--runs-dir DIR] [--source use_case/data] [--limit N]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = REPO_ROOT / "use_case" / "data"
DEFAULT_RUNS_DIR = Path(os.getenv("PIPELINE_RUNS_DIR", str(REPO_ROOT / "runs")))
SUFFIXES = {".jpeg", ".jpg", ".pdf"}
PREFIX = "Payroll_Timesheet_"


def stage(source: Path, run_root: Path, limit: int | None = None) -> list[dict]:
    files = sorted(p for p in source.iterdir()
                   if p.is_file() and p.name.startswith(PREFIX) and p.suffix.lower() in SUFFIXES)
    if limit:
        files = files[:limit]
    attachments = run_root / "attachments"
    attachments.mkdir(parents=True, exist_ok=True)
    mapping = []
    for n, src in enumerate(files, 1):
        staged = f"{n:08d}_069SYN{n:012d}{src.suffix.lower()}"
        shutil.copyfile(src, attachments / staged)
        mapping.append({"record_id": Path(staged).stem, "file_name": staged, "original": src.name})
    with open(run_root / "synthetic_map.jsonl", "w", encoding="utf-8") as fh:
        for row in mapping:
            fh.write(json.dumps(row) + "\n")
    return mapping


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--runs-dir", default=str(DEFAULT_RUNS_DIR))
    ap.add_argument("--source", default=str(DEFAULT_SOURCE), help="folder holding the Payroll_Timesheet_* files")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args(argv)

    run_root = Path(args.runs_dir) / args.run_id
    mapping = stage(Path(args.source), run_root, args.limit)
    if not mapping:
        print(f"No Payroll_Timesheet_* files found in {args.source}")
        return 1
    print(f"Staged {len(mapping)} files into {run_root / 'attachments'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
