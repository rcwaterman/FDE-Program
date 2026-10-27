"""Stage 10 (csv output only): project the validated v2 record to the import-template CSV.

Deterministic: no LLM call. Reads record.json, so every check already applied to the record
(reconciliation, Salesforce verification, email amendments, review routing) carries over.
"""
from __future__ import annotations

from ..exporters import import_template as it
from .base import Context, StageSkip


def run(ctx: Context, rec, entry: dict) -> None:
    if not rec.has("record.json"):
        raise StageSkip("no validated record (an earlier stage did not finish)")
    r = rec.read("record.json")
    if r["extraction"].get("review_status") == "failed":
        raise StageSkip("record failed schema validation; no import CSV")
    cmap = it.load_map(ctx.cfg.import_map_path)
    supported = set(it.supported_doc_types(cmap))
    if r["document"].get("doc_type") not in supported:
        raise StageSkip(f"doc_type {r['document'].get('doc_type')!r} has no import template")
    rows, columns, meta = it.build(r, cmap, include_extra=ctx.cfg.csv_extra_columns)
    it.write_csv(rec.path("import_template.csv"), columns, rows)
    rec.write("import_template.meta.json", meta)
