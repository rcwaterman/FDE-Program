"""Load/save records with atomic writes and JSONL synchronization."""
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def _fingerprint_records_dir(records_dir: str) -> str:
    """Hash the mtimes of all JSON files to detect changes on disk."""
    records_path = Path(records_dir)
    files = sorted(records_path.glob("*.json"))
    hasher = hashlib.md5()

    for f in files:
        mtime = f.stat().st_mtime
        hasher.update(f"{f.name}:{mtime}".encode())

    return hasher.hexdigest()


def load_all_records(records_dir: str) -> dict[str, dict]:
    """Load all records from extraction/records/*.json. Keyed by record_id."""
    records = {}
    records_path = Path(records_dir)

    for record_file in sorted(records_path.glob("*.json")):
        try:
            with open(record_file, "r") as f:
                record = json.load(f)
                record_id = record.get("record_id")
                if record_id:
                    records[record_id] = record
        except Exception as e:
            print(f"Error loading {record_file}: {e}")

    return records


def save_decision(
    record_id: str,
    field_decision: dict,
    records_dir: str,
    jsonl_path: str,
    reviewer_email: str,
) -> None:
    """
    Save a single field decision to a record file and regenerate the JSONL.
    field_decision should have: item_id, decision, corrected_value, note.
    decided_by/decided_at will be filled in here.
    """
    record_path = Path(records_dir) / f"{record_id}.json"

    # Read fresh from disk to avoid clobbering concurrent edits
    with open(record_path, "r") as f:
        record = json.load(f)

    # Ensure human_review block exists
    if "human_review" not in record:
        record["human_review"] = {
            "reviewed_by": reviewer_email,
            "status": "in_progress",
            "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
            "updated_at": datetime.now(timezone.utc).astimezone().isoformat(),
            "field_decisions": [],
        }
    else:
        record["human_review"]["updated_at"] = datetime.now(timezone.utc).astimezone().isoformat()

    # Upsert the decision
    field_decision["decided_by"] = reviewer_email
    field_decision["decided_at"] = datetime.now(timezone.utc).astimezone().isoformat()

    item_id = field_decision["item_id"]
    existing_idx = None
    for idx, d in enumerate(record["human_review"]["field_decisions"]):
        if d["item_id"] == item_id:
            existing_idx = idx
            break

    if existing_idx is not None:
        record["human_review"]["field_decisions"][existing_idx] = field_decision
    else:
        record["human_review"]["field_decisions"].append(field_decision)

    # Update completion status
    pay_items = record.get("pay_items", [])
    decided_ids = {d["item_id"] for d in record["human_review"]["field_decisions"]}
    if len(pay_items) == 0 or len(decided_ids) >= len(pay_items):
        record["human_review"]["status"] = "complete"
    else:
        record["human_review"]["status"] = "in_progress"

    # Atomic write: write to .tmp, fsync, then replace
    tmp_path = Path(str(record_path) + ".tmp")
    with open(tmp_path, "w") as f:
        json.dump(record, f, indent=1)
        f.flush()
        os.fsync(f.fileno())

    os.replace(tmp_path, record_path)

    # Regenerate JSONL from all records
    _regenerate_jsonl(records_dir, jsonl_path)


def save_decision_batch(
    record_id: str,
    field_decisions: list[dict],
    records_dir: str,
    jsonl_path: str,
    reviewer_email: str,
) -> None:
    """Save multiple field decisions to a record file in one batch."""
    record_path = Path(records_dir) / f"{record_id}.json"

    # Read fresh from disk
    with open(record_path, "r") as f:
        record = json.load(f)

    # Ensure human_review block exists
    if "human_review" not in record:
        record["human_review"] = {
            "reviewed_by": reviewer_email,
            "status": "in_progress",
            "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
            "updated_at": datetime.now(timezone.utc).astimezone().isoformat(),
            "field_decisions": [],
        }
    else:
        record["human_review"]["updated_at"] = datetime.now(timezone.utc).astimezone().isoformat()

    # Add timestamp and reviewer to all decisions
    now = datetime.now(timezone.utc).astimezone().isoformat()
    for decision in field_decisions:
        decision["decided_by"] = reviewer_email
        decision["decided_at"] = now

    # Upsert all decisions
    existing_item_ids = {d["item_id"] for d in record["human_review"]["field_decisions"]}
    for decision in field_decisions:
        item_id = decision["item_id"]
        existing_idx = None
        for idx, d in enumerate(record["human_review"]["field_decisions"]):
            if d["item_id"] == item_id:
                existing_idx = idx
                break

        if existing_idx is not None:
            record["human_review"]["field_decisions"][existing_idx] = decision
        else:
            record["human_review"]["field_decisions"].append(decision)

    # Update completion status
    pay_items = record.get("pay_items", [])
    decided_ids = {d["item_id"] for d in record["human_review"]["field_decisions"]}
    if len(pay_items) == 0 or len(decided_ids) >= len(pay_items):
        record["human_review"]["status"] = "complete"
    else:
        record["human_review"]["status"] = "in_progress"

    # Atomic write
    tmp_path = Path(str(record_path) + ".tmp")
    with open(tmp_path, "w") as f:
        json.dump(record, f, indent=1)
        f.flush()
        os.fsync(f.fileno())

    os.replace(tmp_path, record_path)

    # Regenerate JSONL
    _regenerate_jsonl(records_dir, jsonl_path)


def _regenerate_jsonl(records_dir: str, jsonl_path: str) -> None:
    """Regenerate the JSONL from all record files."""
    records_path = Path(records_dir)
    records = []

    # Load all record files in sorted order
    for record_file in sorted(records_path.glob("*.json")):
        try:
            with open(record_file, "r") as f:
                record = json.load(f)
                records.append(record)
        except Exception as e:
            print(f"Error loading {record_file}: {e}")

    # Atomic write of JSONL
    tmp_jsonl = Path(str(jsonl_path) + ".tmp")
    with open(tmp_jsonl, "w") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")
        f.flush()
        os.fsync(f.fileno())

    os.replace(tmp_jsonl, jsonl_path)


def materialize_from_run_jsonl(run_jsonl_path: str, records_dir: str) -> int:
    """Create flat <record_id>.json files in records_dir from a pipeline-run JSONL
    (e.g. a runs/<run_id>/payroll_extractions.jsonl), matching the flat-file
    layout the review app expects (one JSON file per record, globbed by
    load_all_records). Never overwrites a record_id that already has a flat file,
    so re-opening a run preserves any human_review decisions already made in a
    prior session. Returns the number of new files created."""
    records_path = Path(records_dir)
    records_path.mkdir(parents=True, exist_ok=True)
    created = 0

    with open(run_jsonl_path, "r") as f:
        for line in f:
            if not line.strip():
                continue
            record = json.loads(line)
            record_id = record.get("record_id")
            if not record_id:
                continue
            target = records_path / f"{record_id}.json"
            if not target.exists():
                with open(target, "w") as out:
                    json.dump(record, out, indent=1)
                created += 1

    return created


def undo_decision(
    record_id: str,
    item_id: str,
    records_dir: str,
    jsonl_path: str,
) -> None:
    """Remove a decision for a specific item_id."""
    record_path = Path(records_dir) / f"{record_id}.json"

    with open(record_path, "r") as f:
        record = json.load(f)

    if "human_review" not in record:
        return

    # Remove the decision
    record["human_review"]["field_decisions"] = [
        d for d in record["human_review"]["field_decisions"] if d["item_id"] != item_id
    ]
    record["human_review"]["updated_at"] = datetime.now(timezone.utc).astimezone().isoformat()

    # Update completion status
    pay_items = record.get("pay_items", [])
    decided_ids = {d["item_id"] for d in record["human_review"]["field_decisions"]}
    if len(pay_items) == 0 or len(decided_ids) >= len(pay_items):
        record["human_review"]["status"] = "complete"
    else:
        record["human_review"]["status"] = "in_progress"

    # Atomic write
    tmp_path = Path(str(record_path) + ".tmp")
    with open(tmp_path, "w") as f:
        json.dump(record, f, indent=1)
        f.flush()
        os.fsync(f.fileno())

    os.replace(tmp_path, record_path)

    # Regenerate JSONL
    _regenerate_jsonl(records_dir, jsonl_path)
