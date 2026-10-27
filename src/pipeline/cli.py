"""Command line: python -m src.pipeline.cli <command> [options]

  ingest     build the run manifest from a folder or from Salesforce
  snapshot   fetch Salesforce snapshots for the cases in the manifest
  run        run record stages (all, or --from/--to a stage) for all or selected records
  export     write JSONL, workbook, Earning by Column draft and run_summary.json
  status     print stage status per record
  all        ingest + snapshot + run + export
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

from .config import PipelineConfig
from .orchestrator import run_records, write_run_info
from .stages import STAGE_NAMES
from .stages import ingest as ingest_mod
from .stages.base import Context
from .stages.export import export
from .stages.sf_snapshot import snapshot_cases
from .store import RunStore


def _sf_client(cfg: PipelineConfig):
    if not (cfg.sf_username and cfg.sf_password and cfg.sf_security_token):
        return None
    from src.data_retrieval.sf_client import SalesforceReadOnlyClient
    return SalesforceReadOnlyClient(cfg.sf_username, cfg.sf_password, cfg.sf_security_token, cfg.sf_domain)


def _llm(cfg: PipelineConfig, kind: str, replay: str | None = None):
    if kind == "none":
        return None
    if kind == "replay":
        from .llm.replay import ReplayLLMClient
        if not replay:
            raise SystemExit("--replay <jsonl> is required with --llm replay")
        return ReplayLLMClient(Path(replay))
    from .llm.client import AnthropicClient
    return AnthropicClient(max_retries=cfg.api_max_retries)


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
    llm = _llm(cfg, llm_kind, getattr(args, "replay", None)) \
        if args.command in ("run", "all") else None
    return Context(cfg=cfg, run=run, llm=llm,
                   sf=_sf_client(cfg) if need_sf else None,
                   options={"force_extract": "extract" in (getattr(args, "force", None) or []),
                            "force_render": "preprocess" in (getattr(args, "force", None) or [])})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.pipeline.cli", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["ingest", "snapshot", "run", "export", "status", "all"])
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
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ctx = build_context(args)
    write_run_info(ctx, " ".join(sys.argv if argv is None else argv))

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
        run_records(ctx, args.records.split(",") if args.records else None,
                    from_stage=args.from_stage, to_stage=args.to_stage, force=set(args.force or []))
    if args.command in ("export", "all", "run"):
        s = export(ctx, workbook=not args.no_workbook)
        print(json.dumps({k: s[k] for k in ("records_exported", "review_status", "totals", "salesforce_checks", "llm", "failures")}, indent=1))
    if args.command == "status":
        print(f"output_format={ctx.cfg.output_format}")
        for rid in ctx.run.record_ids():
            st = ctx.run.record(rid).state()
            print(rid, " ".join(f"{s}={st.get(s, {}).get('status', '-')}" for s in STAGE_NAMES))
    return 0


if __name__ == "__main__":
    sys.exit(main())
