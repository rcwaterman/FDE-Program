"""Stage 5 (Claude): one record_page call per page, validated against the page schema.

Outputs pages/page-NNN.json and extract.json (per-page status). Pages already extracted with the
same prompt version and model are reused, so a re-run only repeats failed pages.
"""
from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from jsonschema import Draft202012Validator

from .. import client_rules as cr
from ..llm.client import image_block, text_block
from ..llm.prompts import PAGE_SYSTEM, PAGE_USER, PROMPT_VERSION
from ..llm.prompts_csv import PAGE_SYSTEM_CSV, PAGE_USER_CSV, PROMPT_VERSION_CSV, user_suffix
from ..store import read_json, write_json, now
from .base import Context, StageSkip

log = logging.getLogger(__name__)


def _friendly_error(error) -> str:
    """Convert jsonschema ValidationError to actionable feedback for the model.

    Returns a message that names the specific issue and suggests the fix, rather than
    echoing the default jsonschema error text."""
    val = error.validator
    if val == "required":
        missing = ", ".join(error.validator_value)
        return f"Missing required field(s): {missing}. Use an empty array [] if there is nothing to record."
    elif val == "enum":
        options = ", ".join(f"'{x}'" for x in error.validator_value)
        path = "/".join(map(str, error.path)) if error.path else "root"
        return f"At {path}: must be one of: {options}"
    elif val == "additionalProperties":
        # error.message typically says "Additional properties are not allowed ('field_name' was unexpected)"
        return f"Remove the extra field — only use fields documented in the schema. {error.message}"
    elif val == "type" and error.instance is None:
        field = "/".join(map(str, error.path)) if error.path else "field"
        return f"Field {field} cannot be null. For entries/totals, if there is no value, omit the entry entirely instead of sending null."
    else:
        return error.message[:200]


def _validator(ctx):
    return Draft202012Validator(json.loads(ctx.cfg.page_schema_path.read_text()))


def _spec(ctx: Context, rec, entry: dict) -> dict:
    """What this run sends: the JSON prompt and schema, or the CSV variant with the client's confirmed rules.
    The JSON spec is exactly what it was before the CSV variant existed, so JSON caches stay valid."""
    if ctx.cfg.output_format != "csv":
        return {"variant": "json", "system": PAGE_SYSTEM, "user": PAGE_USER, "version": PROMPT_VERSION,
                "schema": ctx.cfg.page_schema_path, "rules_block": None, "rules_hash": None}
    rules = cr.load(ctx.cfg.client_rules_path)
    client = _client_identity(ctx, entry)
    match = cr.lookup(rules, client_number=client.get("number"), client_name=client.get("name"))
    active = cr.active_rules(match, ctx.cfg.client_rules_include_candidates)
    return {"variant": "csv", "system": PAGE_SYSTEM_CSV, "user": PAGE_USER_CSV, "version": PROMPT_VERSION_CSV,
            "schema": ctx.cfg.csv_page_schema_path, "rules_block": cr.prompt_block(match, active),
            "rules_hash": cr.rules_hash(active), "client": (match or {}).get("key")}


def _client_identity(ctx: Context, entry: dict) -> dict:
    """The client from the Salesforce snapshot that is already on disk. Empty when there is no snapshot."""
    case = entry.get("case_number")
    path = ctx.run.sf_path(case) if case else None
    if not path or not path.exists():
        return {}
    account = read_json(path).get("account") or {}
    return {"number": account.get("employer_unique_id"), "name": account.get("name") or account.get("dba")}


def _extract_page(ctx: Context, rec, entry, pre, page_no: int, doc_type: str, schema: dict, v, spec: dict) -> dict:
    out = rec.page_json(page_no)
    if out.exists() and not ctx.options.get("force_extract"):
        prev = read_json(out)
        meta = prev.get("_meta", {})
        if (meta.get("prompt_version") == spec["version"] and meta.get("model") and meta.get("valid")
                and meta.get("rules_hash") == spec["rules_hash"]):
            return {"page": page_no, "status": "cached"}
    p = pre["pages"][page_no - 1]
    text_layer = f"Text layer for this page (may be partial):\n{p['text'][:12000]}" if p["text_chars"] > 40 else "No text layer; read the image."
    image_format = p.get("image_format", "png")
    media_type = f"image/{image_format}"
    user_text = spec["user"].format(page=page_no, page_count=pre["page_count"], record_id=rec.record_id,
                                    doc_type=doc_type, text_layer=text_layer) + user_suffix(spec["rules_block"])
    content = [image_block(rec.page_image(page_no), media_type=media_type), text_block(user_text)]
    errors = []
    friendly_errors = []
    for attempt in range(ctx.cfg.schema_retries + 1):
        msg = content if not errors else content + [text_block(
            "Your previous record_page call failed schema validation:\n" + "\n".join(friendly_errors[:20]) + "\nCall record_page again with a corrected object.")]
        res = ctx.llm.call_tool(model=ctx.cfg.extract_model, system=spec["system"], content=msg, tool_name="record_page",
                                tool_description="Record everything on this page.", input_schema=schema,
                                max_tokens=ctx.cfg.extract_max_tokens,
                                trace={"record_id": rec.record_id, "stage": "extract", "page": page_no, "attempt": attempt})
        rec.log_llm({"stage": "extract", "page": page_no, "attempt": attempt, "model": res.model, "usage": res.usage,
                     "stop_reason": res.stop_reason, "latency_s": res.latency_s, "at": now()})
        data = res.data
        data["page"] = page_no  # page number is ours, not the model's
        errors = [f"{'/'.join(map(str, e.path))}: {e.message[:200]}" for e in v.iter_errors(data)]
        friendly_errors = [_friendly_error(e) for e in v.iter_errors(data)]
        if not errors:
            data["_meta"] = {"prompt_version": spec["version"], "model": res.model, "valid": True, "at": now(),
                             "attempts": attempt + 1, "variant": spec["variant"], "rules_hash": spec["rules_hash"]}
            write_json(out, data)
            return {"page": page_no, "status": "ok", "attempts": attempt + 1}
    data["_meta"] = {"prompt_version": spec["version"], "model": res.model, "valid": False, "errors": errors[:20],
                     "at": now(), "variant": spec["variant"], "rules_hash": spec["rules_hash"]}
    write_json(out, data)
    return {"page": page_no, "status": "invalid", "errors": errors[:5]}


def run(ctx: Context, rec, entry: dict) -> None:
    if ctx.llm is None:
        raise StageSkip("no LLM client configured")
    if not ctx.cfg.allow_pii_to_llm:
        raise StageSkip("PIPELINE_ALLOW_PII_TO_LLM is false; page images contain PII and are not sent")
    doc_type = rec.read("classify.json")["doc_type"] if rec.has("classify.json") else "unknown"
    if doc_type not in ctx.cfg.target_doc_types:
        raise StageSkip(f"doc_type {doc_type!r} not in target_doc_types; skipping extraction")
    pre = rec.read("preprocess.json")
    spec = _spec(ctx, rec, entry)
    schema = json.loads(Path(spec["schema"]).read_text())
    v = Draft202012Validator(schema)
    pages = range(1, pre["page_count"] + 1)
    with ThreadPoolExecutor(max_workers=max(1, ctx.cfg.max_llm_concurrency)) as ex:
        results = list(ex.map(lambda n: _safe(ctx, rec, entry, pre, n, doc_type, schema, v, spec), pages))
    ok = all(r["status"] in ("ok", "cached") for r in results)
    rec.write("extract.json", {"ok": ok, "pages": results, "prompt_version": spec["version"], "variant": spec["variant"],
                               "rules_hash": spec["rules_hash"], "model": ctx.cfg.extract_model, "at": now()})
    if not ok:
        bad = [r["page"] for r in results if r["status"] not in ("ok", "cached")]
        raise RuntimeError(f"page extraction failed for pages {bad}")


def _safe(ctx, rec, entry, pre, n, doc_type, schema, v, spec):
    try:
        return _extract_page(ctx, rec, entry, pre, n, doc_type, schema, v, spec)
    except Exception as e:  # API error after SDK retries
        log.exception("extract %s p%d", rec.record_id, n)
        return {"page": n, "status": "error", "error": f"{type(e).__name__}: {e}"[:2000],
                "request_id": getattr(e, "request_id", None)}
