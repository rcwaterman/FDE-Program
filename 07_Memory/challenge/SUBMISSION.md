# Technical Challenge 7: procedural memory, and tests that target it

## The real architecture this is built against

| Fact | Where | Why it matters for memory |
|---|---|---|
| Claude does the work: `extract_model` Sonnet (page vision), `amend_model` Sonnet, `classify_model` Haiku, `review_model` Opus | `config.py` | Procedure is learned per model, so a model swap must not inherit another model's lessons |
| Every call is a **forced tool call** with a JSON schema, then validated by `jsonschema` | `llm/client.py`, `extract.py` | The schema is the contract; a failed validation is the observable event memory learns from |
| A page that fails validation is **retried up to `schema_retries` (default 2), re-sending the image** with the error text, so at most 3 vision calls per page | `extract._extract_page` | Each avoided retry saves one full image call |
| A model that rejects a forced tool call is remembered in `AnthropicClient._auto_only`, **in process memory only** | `llm/client.py` | Lost at the end of every run |
| The job is an OpenShift **CronJob** (`15 7-19 * * 1-3`, `concurrencyPolicy: Forbid`): a fresh pod per run, one run at a time, with a PVC at `/data` | `deploy/openshift/` | Memory has to live on the PVC or each run starts empty; only one writer at a time |
| Pages run in a **thread pool** (`max_llm_concurrency`, default 6); records run in another | `extract.py`, `job.py` | The store has to be thread-safe (it was not; a test found it and it is fixed) |
| The system prompt is sent with `cache_control: ephemeral` | `llm/client.py` | Lessons added to it become part of the cached prefix |
| Valid pages are cached by `prompt_version` + model + rules hash, so a re-run repeats only failed pages | `extract.py` | The same scope key for lessons; and failed pages are exactly where lessons matter |
| `PAGE_SYSTEM` already holds lessons a person added after failures ("do not add extra top-level properties like marks…", "omit the entry, do not send null") | `llm/prompts.py` | Procedural memory is already being kept, by hand-editing the prompt |
| Per-attempt logs exist: `llm_calls.jsonl` (usage per attempt), `apilog` (HTTP attempts, `tool_choice_fallback`), page `_meta.attempts` and `_meta.errors` | `store.py`, `apilog.py`, `extract.py` | The before/after for this app is already being recorded |

## Part 1. The kind of memory: procedural

**Chosen: procedural.** The repeated problem is *getting a valid answer from the model*, and nothing carries what a run learned about that to the next page or the next run.
Two concrete cases from the table: the schema-retry loop forgets its lesson before the next page, and the tool-choice fallback is forgotten at the end of every pod.

**Rejected:**
- *Semantic.* Per-client conventions already live in `client_rules.json`, curated by hand with evidence; the hard part there is who may write, not recall. I built it and measured it (`_semantic_v1/`); it solved a problem the code mostly answers.
- *Episodic.* Run folders already keep observed events and per-item provenance, and `apilog` deliberately logs shape, never content. The part worth keeping is built in: an event log written by the code, which explanations come from.

## Part 2. What was built

`proc_store.py` (SQLite, thread-safe) and `proc_client.py` (a wrapper around any `call_tool` client).

- **Lessons.** When a call fails schema validation and the retry for the same page passes, each error becomes a lesson: signature + hint. The live lessons for
  `(tool, model, prompt version)` are added to the **first** attempt of later calls. Hints are built **only** from the error's validator, its *schema* path and
  values the schema itself holds, never from page text, model output or a field name the model invented.
- **Capabilities.** "This model rejects a forced tool call": `preload_capabilities` seeds a fresh client before the run, `harvest_capabilities` stores what it learned after.
  They expire after `ttl_days` (default 30) and are re-checked.
- **Event log.** Every call (attempt, valid or not, error count, lesson ids, tokens), every lesson learned and every rejected write, in an append-only table written by the code. It never stores content.
- **Writers.** Only allow-listed writers can write; a scope holds at most 20 live lessons.
- **Staleness, as the README asks for it:** *validity window* for capabilities (expiry); *scoped validity* for lessons (a prompt-version change retires them, the same key `extract.py` uses for its page
  cache); *supersede-on-write* (a changed hint replaces the old one, one live row per signature, history kept). Recency was not used: it leaves the stale row in the candidate set.

**Where it plugs in:** `cli.py:59` builds `AnthropicClient`; the wrapper goes around that object, so `extract.py` is unchanged and its own retry loop runs. That is a tracked change, so it is not done here.

## Part 3. The tests: 17 pass

The **real** `stages.extract._extract_page` runs (its prompts, retry loop, validator and `llm_calls.jsonl`); only the model is scripted. The tool-choice test uses the **real** `AnthropicClient`
with a stubbed transport that rejects a forced tool call. No network.

1. **Staleness (4).** A prompt-version change retires the lessons, kept as history. A changed hint supersedes, one live row per signature. A capability expires after its window and is re-checked. A capability is superseded, not duplicated.
2. **Cost saving (4).** Two pages: the first needs a retry, the second passes first time. **The pipeline's own log** shows attempts `[0, 1]` and `[0]`: 3 calls against 4 without memory. Two `AnthropicClient` instances
   (a fresh pod per run): without memory the rejected call is paid every run (`tool, auto, auto`); with memory the second run is `auto, auto`. An empty memory leaves the system prompt byte-identical.
3. **Truthfulness (2).** The explanation is built from the event log and compared with the pipeline's **own** `llm_calls.jsonl`: attempts per page match; page 2's first attempt carries the lesson page 1 taught; the lesson text is in the system
   prompt that was really sent. A false account ("page 1 needed no retry") is caught.
4. **Leakage (2).** There are no users, so the leakage that matters is data: after a run, a dump of the whole store contains none of the page's values and not the model's invented field name; lessons and capabilities for one model are never served for another.
5. **Poisoning (4).** An attacker-chosen field name never reaches memory or a later prompt. A lesson cannot be written from free text. An unauthorised writer is rejected and logged. A scope cannot grow past 20.
6. **Concurrency (1).** 12 pages through 6 worker threads: all succeed, every call is logged once. Found by this test: the store was bound to its creating thread and failed in the pool. Fixed.

**Do the tests fail when the memory is broken?** I broke it six ways in throwaway copies and each was caught: no prompt-version scoping, no model scoping, a hint that leaks the error text, a wrapper that never serves lessons,
a capability that never expires, and the thread-bound store.

## Before / after: what is measured, and what is not

**Measured, from the real code and schema (no model involved):**
- A page costs at most **3 vision calls** (1 + `schema_retries`), each re-sending the image. A lesson that prevents a retry saves one whole call.
- Against the real page schema, the lessons for the common failures are **220 to 500 characters** (about 55 to 125 tokens, at roughly 4 characters per token, an estimate), against a system prompt of 3,893 characters plus a schema of 8,451
  (about 3,100 tokens before the image). So lessons add **under about 4%** to a call's text.
- **Break-even:** a lesson pays for itself if it prevents roughly one retry in every 25 calls it rides on (an upper bound, since the image makes a retry dearer). Prompt caching makes the lesson cheaper still while the set is stable, and
  costs one cache write whenever the set changes mid-run.
- The tool-choice capability saves **one rejected call per model per run**, deterministically (tested with the real client).

**Not measured: how often real Claude calls fail validation, and so how much memory would save.** That number does not exist in this repo, and I have no real runs here. The before/after is a query over data the pipeline already writes:

| Question | Field | Where |
|---|---|---|
| What share of pages needed a retry? | `_meta.attempts > 1` | each `pages/page-NNN.json` |
| Which schema rules break, per model? | `_meta.errors` (invalid pages) | same |
| How many calls and tokens did retries cost? | `usage` per attempt | `llm_calls.jsonl` |
| How often is a forced tool call rejected? | `outcome: tool_choice_fallback` | `logs/api_calls.jsonl` (`apilog` summary) |

If retries are rare on Sonnet, the lesson part is worth little and only the tool-choice capability is worth shipping. I can run that query for counts only if you point me at a runs folder; I did not, because those folders hold PII.

**Can memory make this worse? In this app, yes, in ways I can name without a model:**
- A **wrong or stale lesson is followed**, because it sits in the system prompt. Scoping to prompt version and model, and supersede-on-write, are the defences; there is no detector for a lesson that was never right.
- **A lesson can fight the prompt.** The "always include these fields" lesson (learned from a missing-field error) conflicts with `PAGE_SYSTEM`, which says to omit an entry rather than send null. I learned it from a degenerate (empty) answer, which is a flaw: it should never learn from a response that has almost no structure. **Not fixed.**
- **Mid-run learning is partial:** with 6 pages in flight, a lesson learned on page 3 reaches only pages that start later, and changes the cached prefix for them.
- **Every call pays for the lessons**, including pages that would have passed.
- Visibility cuts the other way: the event log shows which failures recur per model and prompt version. It points at one I would check first in real runs: `classify` names the type `payroll_timesheet_report`, the prompt tells the model
  `Classified as: <that name>`, and the schema's `page_kind` wants `timesheet` (`normalize.py` and `assemble.py` translate between them). If real `_meta.errors` show `page_kind` enum failures, fixing the prompt or mapping the name in code beats remembering it.

I also ran this against a local 7B vision model. You judged it unrepresentative of Claude and I agree it says little about it, so none of that is used above. The files (`measure_proc.py`, `results_proc.json`) remain only as a record: with that model memory did not help.

## Deploying it on this architecture

- **Where the file lives.** Not in a run folder (the job deletes them after `PIPELINE_RUN_RETENTION_DAYS`) but under the PVC, for example `/data/memory/`. A fresh pod per run means an in-pod file would always start empty.
- **SQLite on the PVC.** The PVC is `azurefile-csi` (an SMB share), where SQLite locking is unreliable. `concurrencyPolicy: Forbid` keeps it to one writer, but I would use the rollback journal and a single process, or store the memory as a JSON
  file with the atomic write `store.write_json` already uses. Untested here.
- **No PII.** The store holds schema-derived text and counts only, so it does not need the PVC's PII handling; the tests assert nothing from a page is kept.
- **Who writes.** A lesson is replayed into every later prompt for that model and prompt version. The allow-list here is a prototype; a real one needs an identity and a review step.

## Checklist

| Requirement | Status |
|---|---|
| One kind of memory implemented; choice and rejects in `decisions.md` | **Done: procedural** |
| Staleness handled explicitly, say which | **Done:** validity window, scoped validity by prompt version, supersede-on-write |
| Three memory tests running with everything else | **Done** (17 tests). Not in `use_case/evals` (this repo has none): `uv run --frozen pytest memory/tests` |
| Leakage test, if there are users | **Adapted:** no users; nothing from a page stored, nothing crosses models |
| Before/after on Week 4 numbers; find out whether memory made things worse | **Partly.** No Week 4 numbers exist here and real retry rates are not measurable here. The measured parts, the exact queries for the rest, and the ways it can make things worse are above |
| Recall quality at 10 / 100 / 1,000 entries (recommended) | **Does not apply:** exact-key lookups capped at 20 per scope, nothing to rank. |
