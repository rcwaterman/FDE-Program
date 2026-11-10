# Architecture — Payroll Timesheet Pipeline

This document describes the design of a batch pipeline. The pipeline reads payroll timesheets that clients send by email. It turns each case into one import CSV for a payroll specialist to check.

## 1. Purpose and design rules

- **Claude only reads.** The model transcribes timesheet pages and reads email instructions. Python does all other work: rules, math, checks and CSV layout.
- **Flag, never guess.** A value that is not certain gets a flag in the CSV. A person checks each flagged value.
- **Resume without repeating work.** A stage cache and a saved time window make a retry cheap. No page goes to the model twice.
- **The case system is read-only,** with one optional write: the upload of the finished CSV to the case.
- **Nothing is written to the payroll engine.**

## 2. System context

```
   Case management system (CRM)
      ▲ optional CSV upload        │ reads + attachment download
      │ (the only write)           ▼
 ┌─────────────────────────────────────────────────────────────┐
 │ Scheduled batch job (Kubernetes CronJob, Python 3.12)        │
 │  ingest → screen → per-case stages → export → deliver        │
 │  Persistent volume: stage cache and per-case output          │
 └──────┬───────────────────────────────────────┬──────────────┘
        │ page images + prompts                  │ OTLP
        ▼                                        ▼
   LLM API                              OTel collector → Datadog (and other backends)
```

| Actor | Role |
|---|---|
| Case management system | Source of cases, emails and attachments. Target of the optional CSV upload. |
| LLM API | Reads timesheet pages and email threads. |
| Kubernetes | Runs the job on a schedule. |
| Telemetry collector | Sends traces, metrics and logs to Datadog and the other observability backends. |
| Payroll specialist | Reviews each CSV and loads it. |

## 3. Runtime and scheduling

- The job is a run-to-completion process. A CronJob starts it on a schedule (hourly, on selected weekdays).
- Only one run is active at a time. Two runs would share the watermark.
- A failed run is retried a limited number of times, and it has a time limit.
- The pod runs as a non-root user.

### 3.1 The time window and the watermark

The job keeps a state file with a `watermark`, a `pending` window and the last success.

1. If a past attempt did not finish, the job resumes its `pending` window. If not, it makes a new window. `since` is the watermark. `until` is the current time minus a settle delay. The delay lets the first emails of a case arrive.
2. The job cuts the window so it holds at most a set number of cases. The remaining cases wait for the next run. This limits compute use and model cost.
3. The job saves the window as `pending` **before** it starts work.
4. The watermark moves to `until` only after processing, delivery and indexing all finish.

If the pod stops, the retry resumes the same window under the same run id. The stage cache skips finished work.

Known limit: the job selects cases by creation time. A file that arrives later on an older case is not picked up.

## 4. End-to-end flow

```
ingest ─► snapshot ─► screen ─────────────► per-case work ───────► export ─► deliver ─► index
(run)     (per case)  (all attachments,     (one unit of work      (run)     (owner,     (case list,
                       local, no model)      per timesheet case)             upload)     watermark)
```

| Step | What it does |
|---|---|
| ingest | Selects cases. Downloads attachments. Writes a manifest. |
| snapshot | Saves a read-only snapshot of each case's data from the case system. |
| screen | Runs the local stages (render, OCR, document type) on every attachment. Each case then knows if it has a timesheet. |
| per-case | Runs the model and checking stages for each case. Writes the case folder. |
| export | Writes a dataset, a review workbook and a run summary. It also runs when a run is interrupted. |
| deliver | Reads each case owner. Uploads the CSV if enabled. Retries old failed uploads. |
| index | Adds one row per case to a case list file. |

### 4.1 Case and attachment selection

The job reads a case only when it matches the configured filter: email origin, the regular-payroll topic, a creation time inside the window, and a file attached to an **incoming** email.

The job skips attachments that:

- have no supported reader (supported: PDF, common image formats, spreadsheets);
- are very small images (logos and tracking pixels);
- return empty content;
- are duplicates of a file already linked to another email.

Each skip is recorded in the manifest with a reason.

## 5. Record stages

Each attachment is one **record**. Eleven stages run in order. Each stage reads files from the record folder and writes one output file.

| # | Stage | Model? | What it does |
|---|---|---|---|
| 1 | `preprocess` | No | Picks a reader by file type. Renders pages. Keeps the text layer. |
| 2 | `read_text` | No | Runs local OCR only on pages with no usable text. |
| 3 | `classify` | Fallback only | Matches page text to document-type rules. |
| 4 | `extract` | **Yes** | One forced tool call per page. Output is validated against a JSON schema. |
| 5 | `assemble` | No | Merges pages into one document. |
| 6 | `normalize` | No | Converts raw text into typed values. |
| 7 | `reconcile` | No | Compares printed totals with the sum of items. |
| 8 | `sf_verify` | No | Checks the record against the case-system snapshot. |
| 9 | `amendments` | **Yes** | Reads email instructions. Python applies them. |
| 10 | `validate` | No | Runs schema and business rules. Routes the record to `auto_ok` or `needs_review`. |
| 11 | `import_csv` | No | Projects the record to the import CSV. |

### 5.1 Input layer

All files become one shape: **pages of positioned text**. Each token has text and a bounding box. Later stages read only that shape.

- **Readers** exist for images, PDFs and spreadsheets. A bad file raises an error, and the record fails with a clear reason.
- **Text-layer check.** A PDF page counts as having text only if the layer reads as language. Some scans carry a hidden, mis-encoded layer, and that layer must not stop OCR.
- **OCR is local.** It uses a CPU-only engine (RapidOCR on ONNX Runtime). No page leaves the pod for OCR. A macOS engine is available for local runs.
- **Document types are data.** A type has a title pattern, optional supporting anchors and a fuzzy phrase match for OCR errors. The registry order sets the priority. Each type has a role: `source` (hours and amounts to enter), `approval` (never a source) or `other`.
- **Target types.** Only configured target types go to the `extract` stage.
- **Vision fallback.** If a page has no readable text, an optional low-cost model call can classify it. This option can be turned off.

### 5.2 Extract

- The job makes one model call per page. Calls run in parallel up to a set limit.
- Each call is a **forced tool call** with a JSON schema. The system prompt is cached.
- The prompt has the page image and the text layer as a hint.
- If the output fails schema validation, the client sends the errors back and asks again, a limited number of times. A page that still fails is marked `invalid`, and the stage fails.
- A per-client rule block can be added to the prompt. Only confirmed rules are sent by default.
- A page is reused from cache when the prompt version, model and rules are unchanged.
- The stage is skipped when there is no model client, when sending page images is not allowed, when the document type is not a target, or when the source has no page images.

### 5.3 Deterministic stages

- **assemble** finds employees across pages. It resolves header fields by majority and flags conflicts.
- **normalize** holds all conversion rules: fractions, negatives in parentheses, decimal commas, `h:mm` times and similar forms.
- **reconcile** compares printed totals with the sum of items and flags any difference.
- **sf_verify** compares the record with the case-system snapshot. It can fill a blank field from the snapshot.
- **validate** runs the schema check and business-rule checks, then routes the record.
- **import_csv** is a deterministic projection of the validated record. It writes one earnings row per employee and one row per deduction. It does not export standing deductions, because the payroll system already holds them.

### 5.4 Amendments

1. A deterministic step cleans the email thread: it removes quoted history, banners, links and signature images.
2. The model reads the cleaned thread and returns which instructions add, change, exclude or confirm pay items.
3. Python validates the references, normalizes new values, changes statuses and adds items.

If sending data to the model is not allowed, the thread is not sent, and the record is flagged for review.

### 5.5 Review routing

A record goes to `needs_review` when a check fails or is uncertain. Examples: totals do not reconcile, a case-system check does not match, a value has low confidence, a pay item is unresolved, or an email amendment was applied. Otherwise it is `auto_ok`. A record that fails schema validation gets the status `failed`.

### 5.6 CSV flags

A CSV row can carry flag tokens that tell the reviewer why a value needs a check. Examples: low confidence, uncertain reading, amended by email, value taken from a printed default, missing employee number. A flagged row needs a person to check it.

## 6. Orchestration and caching

- A stage is `cached` when its state is `done`, its input hash is unchanged and its output file exists.
- The input hash covers the input files and the stage code. It also covers extra items such as the snapshot, render resolution, OCR engine, model name, client rules, prompts and CSV mapping. A change to any item re-runs the stage and the later stages.
- Each record folder has a `state.json` with the status, times, input hash and error text of each stage.
- A stage error stops only that record.
- Records, cases and pages run in thread pools with set limits.
- On SIGTERM, the job starts no new work and lets running work finish.
- All file writes are atomic: temp file, then rename.

## 7. Case output and delivery

### 7.1 Case folder

The job rewrites each case folder from scratch:

```
output/<CaseNumber>/
  <CaseNumber>.csv   rows from every timesheet in the case
  case.json          status, run id, result per attachment, counts, owner, upload result
  sources/           the timesheet files the rows came from
output/cases.csv     one row per case per run
```

| Case status | Meaning |
|---|---|
| `csv` | Every timesheet gave rows. |
| `needs_attention` | A timesheet gave no rows, or an attachment could not be read. |
| `no_timesheet` | The job read every attachment, and none is a supported timesheet. |

Only `csv` cases are complete. The case list holds no timesheet data.

### 7.2 Productivity Unit

One case with a timesheet counts as one Productivity Unit (PU).

| Status | Rule |
|---|---|
| `completed` | Status is `csv`, no row is flagged, and no record needs review. |
| `partial` | A CSV exists, but a person must finish it. |
| `failed` | A timesheet was found, but no rows were written. |

A case with no timesheet is not a PU. A marker file stops a retry from counting a finished case twice.

### 7.3 Delivery

- The job reads the case owner. The owner is blank while a queue holds the case.
- When uploads are enabled, the job attaches the CSV to the case as `<CaseNumber> AI draft.csv`.
  - `uploaded`: new file.
  - `updated`: new version of the same file.
  - `already_on_case`: the checksum is the same, so nothing is sent. A retry never adds a copy.
  - `failed`: the run goes on, and later runs retry.
  - `not_in_salesforce`: the case no longer exists. It is not retried.
- Each run retries a limited number of old failed uploads, longest-waiting first.

## 8. Data and storage

| Location | Content | Lifetime |
|---|---|---|
| Run folder | Attachments, page images, per-stage JSON, model-call log, snapshots, manifest, exports | Deleted after a retention period |
| State file | Watermark and pending window | Permanent |
| Output folder | Per-case folders and the case list | Permanent |

All three locations are on one persistent volume. They contain personal data. Version control and Docker builds must ignore them.

## 9. Observability

The job sends traces, metrics and logs over OTLP to a shared collector. The collector forwards them to Datadog and the other observability backends. The job never calls a backend directly. The job flushes telemetry before exit, because a batch job ends right after `main()`.

One run is one trace:

```
job                         run id, window, case counts
  ingest, snapshot
  screen
    record.process > stage.* > ocr.page
  payroll.process           one per case: the PU (status, case attributes)
    record.process > stage.*
      chat <model>          one per model call (token usage, cached tokens)
  export
  deliver                   upload result counts
```

- Each model call has its own span. This keeps token counts and model names separate.
- Cost per PU is the sum of token usage from the call spans, grouped by case number and model.
- Worker threads must run inside a copied trace context. Without it, their spans start new traces and their calls are not counted against the case.
- A failed stage is an error span.

### 9.1 Datadog

- **Span-based metrics.** The job records no metrics of its own. Datadog builds three metrics from the spans it receives: `payroll.pu.count` (grouped by PU status), `payroll.claude.input_tokens` and `payroll.claude.output_tokens`. These counts do not depend on which traces Datadog keeps for search. They count only from the time you create them, so create them **before the first run**.
- **Retention filter.** A retention filter keeps every PU span and every model-call span searchable in Trace Explorer, by case number, for a set number of days.
- **Dashboard.** A dashboard shows PUs by status (completed, partial, failed), model calls per PU and tokens per PU. The dashboard definition is stored as JSON in the repo.
- **Logs.** Log records carry the trace and span ids, so a log line links to its trace.
- **Service name.** The `OTEL_SERVICE_NAME` variable is required and has no default. Telemetry without a service name cannot be attributed in Datadog.

## 10. Configuration

All settings are environment variables. A local `.env` file is supported. Variables set in the environment take priority. Groups:

| Group | Examples |
|---|---|
| Credentials | Case-system client id and secret, LLM API key. These come from a secret store, never from the repo. |
| Window | Initial start time, settle delay, maximum cases per run, run retention days |
| Paths | Run folder, output folder |
| Models | Extract, amendments, classify |
| Safety switches | Allow personal data to the LLM (default off), upload CSV to case (default off), vision classify, LLM amendments |
| Tuning | Concurrency limits, render resolution, OCR engine, schema retries, API retries |
| Output | Output format, extra CSV columns, target document types, client rules path |
| Telemetry | Service name, endpoint, environment, disable switch |

## 11. Safety design

- **Least privilege.** The case-system client exposes only read methods. The upload is a separate, optional function.
- **Personal data to the model.** Page images and email threads go to the LLM API only when an explicit switch is on. The default is off. OCR and rule-based classification are local.
- **Secrets.** Credentials are supplied at run time. The saved run configuration masks the secret.
- **Secret scanning.** CI scans the code for secrets.

## 12. Build and deploy

- A multi-stage Dockerfile builds three targets: `base` (locked dependencies and OCR models), `test` (adds dev tools and tests) and `runtime`.
- The OCR models load at build time. A model download at run time would fail inside a read-only, non-root pod.
- The cluster builds the image from the source repo. CI pushes no image.
- A merge does not deploy. An engineer deploys with a manual step after read-only preflight checks.
- CI runs manifest validation, lint, tests and a secret scan.

## 13. Testing

- Tests cover the full flow, the deterministic stages, the extract gate and retry, the amendments gate, classification, the CSV template, input readers, the job window logic, case-system ingest and login, and spans.
- Tests use a fake model client. A replay client rebuilds page readings from verified records for offline runs.
- A comparison tool writes a workbook with blank "Actual" columns so a reviewer can measure field-level accuracy.
- The key quality measure is the number of values that are wrong **and** not flagged. The target is zero.

## 14. Module map

| Module | Role |
|---|---|
| `job.py` | Scheduled entry point: window, watermark, case processing, delivery, index |
| `orchestrator.py` | Runs record stages with cache, isolation and parallelism |
| `cli.py` | Manual stage-by-stage commands |
| `config.py` | Environment settings |
| `store.py` | Run folder layout, atomic JSON, per-record state |
| `case_output.py` | Case folder, case list, PU status |
| `sf_client.py`, `sf_cases.py` | Read-only case-system client; owner lookup and CSV upload |
| `client_rules.py` | Per-client extraction rules |
| `values.py` | Parsing of values as written |
| `stages/` | Record stages and the run-level ingest, snapshot and export |
| `inputs/` | Readers, OCR engines, document types, page model |
| `llm/` | LLM client, prompts, fake and replay clients |
| `exporters/` | Import CSV, review workbook, comparison tool |
| `schemas/` | JSON schemas and the CSV column map |
| `observability/` | Telemetry setup, PU spans, span helpers |
