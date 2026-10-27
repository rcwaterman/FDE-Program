# src/pipeline

Batch pipeline that turns payroll attachments into schema v2 records. Claude does the reading (page transcription, email instructions); every other step is deterministic Python.

## Setup

```bash
uv sync --inexact --group review-app   # from the repo root
cp .env.template .env                  # set ANTHROPIC_API_KEY; SF_* only for Salesforce runs
```

## Synthetic run (no Salesforce, no client data)

The course's synthetic timesheets (`use_case/data/`, 100 JPEGs and 5 PDFs) run through the same pipeline and into the
review app. Docker is the supported route, because the image holds code only and the data is mounted read-only:

```bash
export RUN_ID=tc3-synth-01
docker compose --profile setup run --rm stage        # copy the timesheets into runs/$RUN_ID under pipeline-safe names
docker compose --profile run run --rm pipeline       # ingest, extract with Claude, write the 19-column CSV
docker compose up review                             # review UI at http://localhost:8501 (localhost only)
```

The `pipeline` service sets `PIPELINE_ALLOW_PII_TO_LLM=true`. That is for synthetic data only. The default stays `false`
(see Safety switches). The staged names are `<8-digit case>_069SYN<12 digits>`, which pass ingest and cannot be mistaken
for real Salesforce ids. `runs/<id>/synthetic_map.jsonl` maps each staged record back to its original file for scoring.

## Output formats

By default each run ends with the v2 JSON record (`record.json`, `payroll_extractions.jsonl`). Pass
`--output-format csv` to also get one completed **import CSV** per submission. Its layout is the 19 columns of
`use_case/CHARTER.md` (`import_template_charter`, the default). The older 26-column layout stays available as
`PIPELINE_CSV_TEMPLATE=import_template_for_automation`. Each employee gets one earnings row. The first deduction is on
that row, and any further deduction gets its own row.

```bash
python -m src.pipeline.cli run --run-id 2026-10-08-b --output-format csv
```

- The CSV is a deterministic projection of the validated record (stage `import_csv`). It makes no LLM
  calls and does not change OCR, so runs that already have records can be converted for free:
  `run --run-id <id> --output-format csv --from import_csv --llm none --no-sf`.
- A run remembers its format in `runs/<id>/output_format.json`. Later `run` and `export` calls keep it
  unless `--output-format` is given again. `status` never changes it.
- Outputs: `records/<id>/import_template.csv` and `.meta.json` (row provenance, unmapped items, excluded
  items), then `runs/<id>/import_csv/` with a `_manifest.json`.
- Mapping rules, flag tokens and the code map live in `schemas/import_column_map.json`
  (`templates.import_template_charter` inherits the code map from `import_template_for_automation` through `based_on`)
  and `exporters/import_template.py`.
- Env: `PIPELINE_OUTPUT_FORMAT`, `PIPELINE_CSV_TEMPLATE`, `PIPELINE_CSV_EXTRA_COLUMNS` (only the legacy template has extra columns).

## Commands

```bash
# 1. manifest from a local folder (or --source salesforce --since 2026-09-28 --until 2026-10-01)
python -m src.pipeline.cli ingest   --run-id 2026-10-02-a --folder src/data/attachments --files list.txt

# 2. Salesforce snapshots (Case, Account, Contacts, ContentDocument, EmailMessages)
python -m src.pipeline.cli snapshot --run-id 2026-10-02-a

# 3. record stages + export
python -m src.pipeline.cli run      --run-id 2026-10-02-a
python -m src.pipeline.cli status   --run-id 2026-10-02-a

# re-run part of the pipeline
python -m src.pipeline.cli run --run-id 2026-10-02-a --from normalize            # deterministic stages only
python -m src.pipeline.cli run --run-id 2026-10-02-a --records <id> --force extract

# all in one
python -m src.pipeline.cli all --run-id 2026-10-02-a --folder src/data/attachments --files list.txt
```

Offline replay (no API key): `--llm replay` answers LLM calls from already-verified v2 records. Use it only with
records you are allowed to hold, and keep those files out of the repo.

Tests: `python -m pytest tests/pipeline tests/synthetic -q`

## Layout

| Module | Role |
|---|---|
| `cli.py` | Commands above |
| `orchestrator.py` | Runs record stages in order; skips a stage when its inputs, code and model are unchanged; records run in parallel; a failure stops only that record |
| `config.py` | Environment settings |
| `store.py` | Run folder, atomic JSON writes, per-record `state.json` |
| `values.py` | Parsing of values as written (fractions, slashed zero, days, `( )` negatives, decimal comma, h:mm) |
| `llm/client.py` | `AnthropicClient` (forced tool call, cached system prompt), `FakeLLMClient` |
| `llm/prompts.py` | Prompts, `PROMPT_VERSION` |
| `llm/replay.py` | `ReplayLLMClient`: converts verified v2 records back into page readings |
| `schemas/` | `page_extraction` (model output per page), `email_amendments`, `record_v2`, `import_column_map.json` |
| `stages/` | ingest, sf_snapshot, preprocess, classify, extract, assemble, normalize, reconcile, sf_verify, amendments, validate, export |
| `exporters/` | Review workbook, Default Earning by Column draft |

## Run folder

`runs/<run_id>/` holds `manifest.jsonl`, `sf/<case>.json`, `records/<record_id>/` (one JSON per stage, page images and page readings, `state.json`, `llm_calls.jsonl`), `payroll_extractions.jsonl`, the workbook, `exports/`, and `run_summary.json`. `runs/` is git-ignored because it contains PII.

## Safety switches

- `PIPELINE_ALLOW_PII_TO_LLM=false` (default): extract and classify-fallback do not send page images; records stop at `extract` with status `skipped`.
- Salesforce access is read-only (existing `src/data_retrieval/sf_client.py`).
- Nothing is written back to Salesforce or the payroll engine.
