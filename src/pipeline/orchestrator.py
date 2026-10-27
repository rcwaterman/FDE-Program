"""Runs record stages with skip-if-unchanged, per-record isolation, and parallel records."""
from __future__ import annotations

import inspect
import logging
import subprocess
from functools import lru_cache
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from .store import now, write_json, sha256_file
from .stages import RECORD_STAGES, STAGE_NAMES
from .stages.base import Context, StageSkip

log = logging.getLogger(__name__)


@lru_cache(maxsize=None)
def _code_hash(stage_name: str, src_file: str) -> str:
    """Stage module + shared value parsing: a code change re-runs the stage (and, via outputs, later stages)."""
    base = Path(__file__).resolve().parent
    files = [Path(src_file), base / "values.py", base / "llm" / "prompts.py"]
    return ":".join(sha256_file(f)[:12] for f in files if f.exists())


def _input_hash(ctx: Context, rec, stage, entry) -> str:
    names = [n for n in stage.inputs if not n.startswith("@")]
    h = rec.input_hash(names) + ":" + _code_hash(stage.name, inspect.getsourcefile(stage.fn))
    if "@sf" in stage.inputs and entry.get("case_number"):
        p = ctx.run.sf_path(entry["case_number"])
        h += ":" + (sha256_file(p) if p.exists() else "none")
    if stage.name == "preprocess":
        h += ":" + (entry.get("sha256") or "") + f":{ctx.cfg.render_dpi}"
    if stage.name == "extract" and ctx.cfg.output_format == "csv":
        # The CSV prompt and the client rules shape the output, so changing either re-runs the stage.
        from .llm import prompts_csv
        h += ":" + sha256_file(ctx.cfg.client_rules_path) + ":" + sha256_file(Path(prompts_csv.__file__)) \
            + f":{ctx.cfg.client_rules_include_candidates}"
    if stage.name == "import_csv":
        from .exporters import import_template
        h += ":" + sha256_file(ctx.cfg.import_map_path) + f":{ctx.cfg.csv_extra_columns}" + ":" + sha256_file(Path(import_template.__file__))
    if stage.uses_llm:
        h += ":" + {"classify": ctx.cfg.classify_model, "extract": ctx.cfg.extract_model,
                    "amendments": ctx.cfg.amend_model}.get(stage.name, "")
    return h


def run_record(ctx: Context, entry: dict, from_stage: str | None = None, to_stage: str | None = None,
               force: set[str] | None = None) -> dict:
    rec = ctx.run.record(entry["record_id"])
    force = set(force or ())
    start = STAGE_NAMES.index(from_stage) if from_stage else 0
    end = STAGE_NAMES.index(to_stage) + 1 if to_stage else len(STAGE_NAMES)
    forced_downstream = False
    result = {"record_id": entry["record_id"], "stages": {}}
    stages = [s for s in RECORD_STAGES[start:end] if s.formats is None or ctx.cfg.output_format in s.formats]
    for stage in stages:
        st = rec.state().get(stage.name, {})
        ih = _input_hash(ctx, rec, stage, entry)
        forced_downstream = forced_downstream or stage.name in force
        if (not forced_downstream and st.get("status") == "done" and st.get("input_hash") == ih and rec.has(stage.output)):
            result["stages"][stage.name] = "cached"
            continue
        rec.set_state(stage.name, status="running", started=now(), error=None)
        try:
            stage.fn(ctx, rec, entry)
            rec.set_state(stage.name, status="done", finished=now(), input_hash=ih)
            result["stages"][stage.name] = "done"  # later stages re-run automatically: their input hashes change
        except StageSkip as e:
            rec.set_state(stage.name, status="skipped", finished=now(), error=str(e))
            result["stages"][stage.name] = f"skipped: {e}"
            break
        except Exception as e:
            log.exception("%s failed at %s", entry["record_id"], stage.name)
            rec.set_state(stage.name, status="failed", finished=now(), error=f"{type(e).__name__}: {e}"[:500])
            result["stages"][stage.name] = f"failed: {e}"
            break
    return result


def run_records(ctx: Context, record_ids: list[str] | None = None, **kw) -> list[dict]:
    entries = [m for m in ctx.run.manifest() if m.get("status") == "ready"
               and (record_ids is None or m["record_id"] in record_ids)]
    out = []
    with ThreadPoolExecutor(max_workers=max(1, ctx.cfg.max_records)) as ex:
        futs = {ex.submit(run_record, ctx, e, **kw): e["record_id"] for e in entries}
        for f in as_completed(futs):
            r = f.result()
            out.append(r)
            log.info("%s: %s", r["record_id"], ", ".join(f"{k}={v.split(':')[0]}" for k, v in r["stages"].items()))
    return out


def write_run_info(ctx: Context, command: str) -> None:
    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5).stdout.strip() or None
    except Exception:
        sha = None
    info_path = ctx.run.root / "run.json"
    write_json(info_path, {"run_id": ctx.run.run_id, "updated": now(), "last_command": command,
                           "git_sha": sha, "config": ctx.cfg.snapshot()})
