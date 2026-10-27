"""Command line: python -m src.pipeline.cli <command> [options]

  ingest     build the run manifest from a folder or from Salesforce
  snapshot   fetch Salesforce snapshots for the cases in the manifest
  run        run record stages (all, or --from/--to a stage) for all or selected records
  export     write JSONL, workbook, Earning by Column draft and run_summary.json
  status     print stage status per record
  diagnose   summarise the Anthropic API log for a run (errors, retries, fallbacks, latency, cache)
  all        ingest + snapshot + run + export
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
from datetime import date, timedelta
from pathlib import Path

from .. import logsetup
from . import apilog
from .config import PipelineConfig
from .orchestrator import run_records, write_run_info
from .stages import STAGE_NAMES
from .stages import ingest as ingest_mod
from .stages.base import Context
from .stages.export import export
from .stages.sf_snapshot import snapshot_cases
from .store import RunStore, read_jsonl, write_json


def _sf_client(cfg: PipelineConfig):
    if not (cfg.sf_username and cfg.sf_password and cfg.sf_security_token):
        return None
    from src.data_retrieval.sf_client import SalesforceReadOnlyClient
    return SalesforceReadOnlyClient(cfg.sf_username, cfg.sf_password, cfg.sf_security_token, cfg.sf_domain)


def _llm(cfg: PipelineConfig, kind: str, run: RunStore, replay: str | None = None):
    if kind == "none":
        return None
    if kind == "replay":
        from .llm.replay import ReplayLLMClient
        if not replay:
            raise SystemExit("--replay <jsonl> is required with --llm replay")
        return ReplayLLMClient(Path(replay))
    from .llm.client import AnthropicClient
    return AnthropicClient(max_retries=cfg.api_max_retries,
                           event_log=apilog.ApiEventLog(run.root / "logs" / "api_calls.jsonl"),
                           tool_choice=cfg.tool_choice)


def build_context(args) -> Context:
    cfg = PipelineConfig()
    if args.runs_dir:
        cfg.runs_dir = Path(args.runs_dir)
    if getattr(args, "min_image_bytes", None) is not None:
        cfg.min_image_bytes = args.min_image_bytes
    llm_kind = getattr(args, "llm", "anthropic")
    if getattr(args, "allow_pii_to_llm", False) or llm_kind == "replay":
        cfg.allow_pii_to_llm = True  # replay sends nothing off-machine
    run = RunStore(cfg.runs_dir, args.run_id)
    # Output format precedence: --output-format flag > format this run was first given (output_format.json)
    # > PIPELINE_OUTPUT_FORMAT. A flag is remembered on commands that produce output.
    flag = getattr(args, "output_format", None)
    if flag:
        cfg.output_format = flag
        if args.command in ("run", "all", "export"):
            run.set_output_format(flag, args.command)
    elif run.get_output_format():
        cfg.output_format = run.get_output_format()
    need_sf = (args.command in ("snapshot", "all") or (args.command == "ingest" and args.source == "salesforce")) \
        and not args.no_sf and not args.snapshot_from_records
    llm = _llm(cfg, llm_kind, run, getattr(args, "replay", None)) \
        if args.command in ("run", "all") else None
    return Context(cfg=cfg, run=run, llm=llm,
                   sf=_sf_client(cfg) if need_sf else None,
                   options={"force_extract": "extract" in (getattr(args, "force", None) or []),
                            "force_render": "preprocess" in (getattr(args, "force", None) or [])})


def _terminate(signum, frame):
    # docker stop sends SIGTERM. Raising here lets the run's finally block export what has finished.
    raise KeyboardInterrupt(f"signal {signum}")


def _export_and_report(ctx: Context, args) -> None:
    s = export(ctx, workbook=not args.no_workbook)
    print(json.dumps({k: s[k] for k in ("records_exported", "review_status", "totals", "salesforce_checks", "llm", "failures")},
                     indent=1))
    host = os.getenv("HOST_RUNS_DIR")
    where = f" (on the host: {host}/{ctx.run.run_id})" if host else ""
    logging.info("outputs: %s%s", ctx.run.root, where)
    print(f"outputs: {ctx.run.root}{where}")


def _diagnose(ctx: Context) -> None:
    path = ctx.run.root / "logs" / "api_calls.jsonl"
    events = read_jsonl(path)
    summary = apilog.summarize(events)
    write_json(ctx.run.root / "logs" / "api_summary.json", summary)
    print(json.dumps(summary, indent=1))
    if not events:
        print(f"no API events in {path}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.pipeline.cli", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["ingest", "snapshot", "run", "export", "status", "all", "diagnose"])
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--runs-dir")
    ap.add_argument("--source", choices=["folder", "salesforce"], default="folder")
    ap.add_argument("--folder", help="attachments folder (source=folder)")
    ap.add_argument("--files", help="text file with one filename per line (source=folder)")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--since", default=(date.today() - timedelta(days=7)).isoformat())
    ap.add_argument("--until", default=(date.today() + timedelta(days=1)).isoformat())
    ap.add_argument("--cases", help="comma-separated case numbers (source=salesforce)")
    ap.add_argument("--output-format", choices=["json", "csv"],
                    help="final output: json (v2 record only) or csv (also one import-template CSV per submission)")
    ap.add_argument("--min-image-bytes", type=int,
                    help="skip Salesforce image attachments smaller than this (0 disables; default PIPELINE_MIN_IMAGE_BYTES)")
    ap.add_argument("--records", help="comma-separated record ids to run")
    ap.add_argument("--from", dest="from_stage", choices=STAGE_NAMES)
    ap.add_argument("--to", dest="to_stage", choices=STAGE_NAMES)
    ap.add_argument("--force", nargs="*", choices=STAGE_NAMES, help="re-run these stages even if inputs are unchanged")
    ap.add_argument("--llm", choices=["anthropic", "replay", "none"], default="anthropic")
    ap.add_argument("--replay", help="v2 JSONL whose records answer LLM calls (--llm replay)")
    ap.add_argument("--allow-pii-to-llm", action="store_true", help="send page images to the LLM (overrides env)")
    ap.add_argument("--no-sf", action="store_true", help="do not connect to Salesforce")
    ap.add_argument("--snapshot-from-records", help="build offline Salesforce snapshots from a verified v2 JSONL")
    ap.add_argument("--no-workbook", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true", help="DEBUG for src.* only; SDK and httpx stay at INFO")
    args = ap.parse_args(argv)
    ctx = build_context(args)
    cfg = ctx.cfg
    logsetup.configure(ctx.run.root, "pipeline", cfg.log_level, args.verbose)
    logging.info("start: command=%s run=%s output_format=%s models extract=%s classify=%s amend=%s "
                 "concurrency=%d tool_choice=%s allow_pii_to_llm=%s", args.command, args.run_id, cfg.output_format,
                 cfg.extract_model, cfg.classify_model, cfg.amend_model, cfg.max_llm_concurrency, cfg.tool_choice,
                 cfg.allow_pii_to_llm)
    write_run_info(ctx, " ".join(sys.argv if argv is None else argv))
    try:
        signal.signal(signal.SIGTERM, _terminate)
    except ValueError:  # not on the main thread (tests): leave the default handler
        pass

    if args.command in ("ingest", "all"):
        if args.source == "folder":
            if not args.folder:
                ap.error("--folder is required for --source folder")
            names = [l.strip() for l in open(args.files) if l.strip()] if args.files else None
            ingest_mod.ingest_folder(ctx, Path(args.folder), names, args.limit)
        else:
            ingest_mod.ingest_salesforce(ctx, args.since, args.until, args.limit,
                                         args.cases.split(",") if args.cases else None)
    if args.command in ("snapshot", "all") and args.snapshot_from_records:
        from .stages.sf_snapshot import snapshots_from_records
        logging.info("synthetic snapshots from %s: %d", args.snapshot_from_records,
                     snapshots_from_records(ctx, args.snapshot_from_records))
    elif args.command in ("snapshot", "all"):
        st = snapshot_cases(ctx)
        logging.info("snapshots: %s", json.dumps({k: sum(1 for v in st.values() if v == k) for k in set(st.values())}))
    if args.command in ("run", "all"):
        try:
            run_records(ctx, args.records.split(",") if args.records else None,
                        from_stage=args.from_stage, to_stage=args.to_stage, force=set(args.force or []))
        finally:
            # Export even when the run was interrupted, so the records that finished are visible to the review app.
            _export_and_report(ctx, args)
    elif args.command == "export":
        _export_and_report(ctx, args)
    if args.command == "status":
        print(f"output_format={ctx.cfg.output_format}")
        for rid in ctx.run.record_ids():
            st = ctx.run.record(rid).state()
            print(rid, " ".join(f"{s}={st.get(s, {}).get('status', '-')}" for s in STAGE_NAMES))
    if args.command == "diagnose":
        _diagnose(ctx)
    return 0


if __name__ == "__main__":
    sys.exit(main())
