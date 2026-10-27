"""Stage 12 (run level): JSONL dataset, review workbook, Earning by Column draft, run summary."""
from __future__ import annotations

import logging
import shutil
from collections import Counter

from ..exporters.import_template import TEMPLATE
from .. import apilog
from ..store import read_json, write_jsonl, write_json, read_jsonl, now
from .base import Context

log = logging.getLogger(__name__)


def export(ctx: Context, workbook: bool = True) -> dict:
    recs = []
    for rid in ctx.run.record_ids():
        p = ctx.run.root / "records" / rid / "record.json"
        if p.exists():
            r = read_json(p)
            if r["extraction"].get("review_status") != "failed":
                recs.append(r)
    out = ctx.run.root / "payroll_extractions.jsonl"
    write_jsonl(out, sorted(recs, key=lambda r: r["record_id"]))
    import_csv = _collect_import_csv(ctx, [r["record_id"] for r in recs]) if ctx.cfg.output_format == "csv" else None
    if recs:
        from ..exporters import earning_by_column
        earning_by_column.main(str(out), str(ctx.cfg.import_map_path), str(ctx.run.root / "exports"))
        if workbook:
            try:
                from ..exporters import workbook as wb
                wb.main(str(out), str(ctx.run.root / "Payroll_Attachment_Extractions.xlsx"))
            except ImportError as e:
                log.warning("workbook skipped: %s", e)
    return summarize(ctx, recs, import_csv)


def _collect_import_csv(ctx: Context, record_ids: list[str]) -> dict:
    """Copy each record's import-template CSV (and meta sidecar) into runs/<id>/import_csv/ with a manifest.
    Records whose import_csv stage was skipped (e.g. a doc type with no template) are listed, not failed."""
    out_dir = ctx.run.root / "import_csv"
    out_dir.mkdir(exist_ok=True)
    entries, not_produced = [], []
    for rid in record_ids:
        rec = ctx.run.record(rid)
        if not rec.has("import_template.csv"):
            not_produced.append(rid)
            continue
        meta = rec.read("import_template.meta.json")
        shutil.copyfile(rec.path("import_template.csv"), out_dir / f"{rid}.csv")
        write_json(out_dir / f"{rid}.meta.json", meta)
        entries.append({"record_id": rid, "case_number": meta.get("case_number"),
                        "client": (meta.get("document") or {}).get("client", {}).get("number"),
                        "rows": len(meta["rows"]), "flagged_rows": sum(1 for r in meta["rows"] if r["flags"]),
                        "unmapped_items": len(meta["unmapped_items"]), "review_status": meta.get("review_status"),
                        "csv": f"{rid}.csv", "meta": f"{rid}.meta.json"})
    write_json(out_dir / "_manifest.json", {"output_format": "csv", "template": TEMPLATE,
                                            "written_at": now(), "records": entries, "not_produced": not_produced})
    return {"records": len(entries), "rows": sum(e["rows"] for e in entries),
            "flagged_rows": sum(e["flagged_rows"] for e in entries), "unmapped_items": sum(e["unmapped_items"] for e in entries),
            "not_produced": len(not_produced)}


def summarize(ctx: Context, recs: list[dict], import_csv: dict | None = None) -> dict:
    states = {}
    for rid in ctx.run.record_ids():
        st = ctx.run.record(rid).state()
        last = next((s for s in reversed(list(st)) if st[s].get("status") in ("failed", "skipped")), None)
        states[rid] = f"{st[last]['status']}@{last}" if last else ("done" if st.get("validate", {}).get("status") == "done" else "incomplete")
    calls = []
    for rid in ctx.run.record_ids():
        calls += read_jsonl(ctx.run.root / "records" / rid / "llm_calls.jsonl")
    tok = Counter()
    for c in calls:
        for k, v in (c.get("usage") or {}).items():
            tok[k] += v or 0
    s = {
        "run_id": ctx.run.run_id, "at": now(),
        "manifest": dict(Counter(m["status"] for m in ctx.run.manifest())),
        "record_states": dict(Counter(states.values())),
        "records_exported": len(recs),
        "review_status": dict(Counter(r["extraction"].get("review_status") for r in recs)),
        "review_reasons": dict(Counter(x for r in recs for x in r["extraction"].get("review_reasons", []))),
        "pay_items": sum(len(r["pay_items"]) for r in recs),
        "totals": {"reconciled": sum(1 for r in recs for t in r["totals"] if t.get("reconciles")),
                   "not_reconciled": sum(1 for r in recs for t in r["totals"] if t.get("reconciles") is False)},
        "salesforce_checks": dict(Counter(c["result"] for r in recs for c in (r.get("salesforce_verification") or {}).get("checks", []))),
        "llm": {"calls": len(calls), "tokens": dict(tok)},
        "failures": {rid: s for rid, s in states.items() if s.startswith(("failed", "skipped"))},
    }
    if import_csv is not None:
        s["import_csv"] = import_csv
    api_events = read_jsonl(ctx.run.root / "logs" / "api_calls.jsonl")
    if api_events:
        s["api"] = apilog.summarize(api_events)
    write_json(ctx.run.root / "run_summary.json", s)
    return s
