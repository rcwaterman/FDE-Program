"""Reviewed import CSVs: one per complete case, from the same rules the table shows.

Rows: approved rows are kept (edited ones with their changes), excluded rows are dropped. Assigned unmapped
lines are added to that employee's earnings row. Lines with no value were skipped and do not appear.
A case is written only when every row has a current decision and every valued unplaced line is resolved.
"""
from pathlib import Path

from src.pipeline.exporters import import_template as it
from src.feature_extraction.review.csv_engine import csv_model, csv_store


def _assigned_rows(rec: dict, review: dict, cmap: dict) -> tuple[list[tuple[dict, dict]], list[str], list[str]]:
    """Approved rows as (effective_row, meta_row) pairs, the unplaced item ids merged into a row, and the
    assigned ids that had no earnings row to merge into."""
    meta = rec["meta"]
    kept = []
    for csv_row, meta_row in zip(rec["rows"], meta["rows"]):
        status = csv_model.row_status(review, meta_row)
        if status in ("reject", "pending", "stale"):
            continue
        kept.append((csv_model.effective_row(csv_row, meta_row, review, cmap), meta_row))
    merged, not_merged = [], []
    for item in meta.get("unmapped_items", []):
        res = review["unmapped_resolutions"].get(item["item_id"])
        if not res or res["action"] != "assign":
            continue
        target = next((r for r, m in kept if m["employee_ref"] == item["employee_ref"] and m["row_kind"] == "earnings"), None)
        if target is None or not res.get("column"):
            not_merged.append(item["item_id"])
            continue
        total = float(target.get(res["column"]) or 0) + float(res["value"])
        target[res["column"]] = it.format_value(res["column"], total)
        csv_model.add_flag(target, csv_model.REVIEWED_FLAG)
        csv_model.recompute_total(target, cmap)
        merged.append(item["item_id"])
    return kept, merged, not_merged


def reviewed_case(run_root: Path, record_id: str, cmap: dict) -> dict:
    """The reviewed result for one case: {complete, rows, columns, dropped, edited, merged, not_merged}."""
    rec = csv_store.load_record(run_root, record_id)
    review = csv_store.load_review(run_root, record_id)
    complete = csv_model.is_complete(rec["meta"], review)
    kept, merged, not_merged = _assigned_rows(rec, review, cmap)
    not_merged += [u["item_id"] for u in csv_model.unresolved_items(rec["meta"], review)]
    dropped = sum(1 for r in rec["meta"]["rows"] if csv_model.row_status(review, r) == "reject")
    edited = sum(1 for r in rec["meta"]["rows"] if csv_model.row_status(review, r) == "edit")
    return {"complete": complete, "columns": rec["columns"], "rows": [r for r, _ in kept],
            "dropped": dropped, "edited": edited, "merged": merged, "not_merged": not_merged}


def reviewed_csv_bytes(run_root: Path, record_id: str, cmap: dict) -> bytes | None:
    """Bytes of the reviewed CSV for one case, or None while the case is not complete."""
    result = reviewed_case(run_root, record_id, cmap)
    if not result["complete"]:
        return None
    return it.csv_bytes(result["columns"], result["rows"])


def export_reviewed(run_root: Path, cmap: dict, out_dir: Path | None = None) -> dict:
    out = out_dir or run_root / "import_csv_reviewed"
    summary = {"written": 0, "not_complete": [], "rows_dropped": 0, "rows_edited": 0,
               "unmapped_assigned": 0, "unmapped_not_merged": []}
    for entry in csv_store.load_manifest(run_root):
        rid = entry["record_id"]
        result = reviewed_case(run_root, rid, cmap)
        if not result["complete"]:
            summary["not_complete"].append(rid)
            continue
        it.write_csv(out / f"{rid}.csv", result["columns"], result["rows"])
        summary["written"] += 1
        summary["rows_dropped"] += result["dropped"]
        summary["rows_edited"] += result["edited"]
        summary["unmapped_assigned"] += len(result["merged"])
        summary["unmapped_not_merged"] += result["not_merged"]
    return summary
