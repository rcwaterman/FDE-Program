"""Storage for the CSV review engine.

Reads the import CSVs and meta sidecars written by stages/import_csv.py (copied into import_csv/ by export).
Reviewer decisions go to runs/<id>/csv_review/<record_id>.json, never into review_records/, so the JSON
engine never sees them.
"""
import csv
import io
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)
SCHEMA_VERSION = "1"
DECISIONS = ("accept", "reject", "edit")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_json(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def _write_json(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def load_manifest(run_root: Path) -> list[dict]:
    p = run_root / "import_csv" / "_manifest.json"
    return _read_json(p)["records"] if p.exists() else []


def load_record(run_root: Path, record_id: str) -> dict:
    d = run_root / "import_csv"
    raw = (d / f"{record_id}.csv").read_bytes().decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(raw)))
    meta = _read_json(d / f"{record_id}.meta.json")
    return {"record_id": record_id, "columns": meta["columns"], "rows": rows, "meta": meta}


def _review_path(run_root: Path, record_id: str) -> Path:
    return run_root / "csv_review" / f"{record_id}.json"


def load_review(run_root: Path, record_id: str) -> dict:
    p = _review_path(run_root, record_id)
    if p.exists():
        return _read_json(p)
    return {"schema_version": SCHEMA_VERSION, "record_id": record_id, "reviewed_by": None,
            "row_decisions": {}, "unmapped_resolutions": {}}


def save_row_decision(run_root: Path, record_id: str, row_id: str, fingerprint: str, decision: str,
                      reviewer: str, cell_edits: dict | None = None, note: str = "") -> None:
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {DECISIONS}")
    review = load_review(run_root, record_id)
    review["reviewed_by"] = reviewer
    review["row_decisions"][row_id] = {"decision": decision, "fingerprint": fingerprint,
                                       "cell_edits": cell_edits or {}, "note": note,
                                       "decided_by": reviewer, "decided_at": _now()}
    _write_json(_review_path(run_root, record_id), review)
    log.info("csv decision record=%s row=%s decision=%s reviewer=%s", record_id, row_id, decision, reviewer)


def undo_row_decision(run_root: Path, record_id: str, row_id: str) -> None:
    review = load_review(run_root, record_id)
    review["row_decisions"].pop(row_id, None)
    _write_json(_review_path(run_root, record_id), review)


def save_unmapped_resolution(run_root: Path, record_id: str, item_id: str, action: str, reviewer: str,
                             column: str | None = None, value: float | None = None) -> None:
    if action not in ("assign", "ignore"):
        raise ValueError("action must be assign or ignore")
    review = load_review(run_root, record_id)
    review["reviewed_by"] = reviewer
    review["unmapped_resolutions"][item_id] = {"action": action, "column": column, "value": value,
                                               "decided_by": reviewer, "decided_at": _now()}
    _write_json(_review_path(run_root, record_id), review)
    log.info("csv resolution record=%s item=%s action=%s reviewer=%s", record_id, item_id, action, reviewer)


def save_row_decisions_batch(run_root: Path, record_id: str, ops: list[tuple], reviewer: str) -> None:
    """Apply several decisions with one file write. ops are (op, row_id, payload) from
    csv_model.apply_editor_changes, where op is accept | reject | edit | undo | blocked."""
    review = load_review(run_root, record_id)
    review["reviewed_by"] = reviewer
    log.info("csv review record=%s reviewer=%s ops=%d kinds=%s", record_id, reviewer, len(ops),
             sorted({op for op, _, _ in ops}))
    for op, row_id, payload in ops:
        if op == "blocked":
            continue  # refused by the model (for example, an edit on an excluded row); nothing to store
        if op == "undo":
            review["row_decisions"].pop(row_id, None)
            continue
        review["row_decisions"][row_id] = {
            "decision": "accept" if op == "accept" else op,
            "fingerprint": payload["fingerprint"],
            "cell_edits": payload.get("cell_edits", {}),
            "note": "",
            "decided_by": reviewer,
            "decided_at": _now(),
        }
    _write_json(_review_path(run_root, record_id), review)
