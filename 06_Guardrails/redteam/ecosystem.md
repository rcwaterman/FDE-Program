# Who can write to what this pipeline reads (Step 3 deliverable)

Evidence is from the code on `main`. "Reaches a model?" means the text is placed in an Anthropic call.

| # | Source | Where it enters | Who can write there | Reaches a model? | Can it change payroll output? |
|---|---|---|---|---|---|
| 1 | **Case emails** (Salesforce `EmailMessage.TextBody`, `FromAddress`, `Incoming`) | `sf_snapshot.py` -> `amendments.py` (stage 10) -> `AMEND_SYSTEM` + `record_amendments` | **Anyone who can email the case address** (clients, their staff, forwarded mail, spammers, spoofers). Nothing in this repo checks the sender against the account's contacts; whether Salesforce email-to-case filters or authenticates senders is **not verified here** and needs the Salesforce admin's answer. Staff replies are also in the thread | Yes (`amend_model`), `thread[:60000]` | **Yes, directly.** `apply_amendments()` adds, supersedes or excludes pay items with no sender check |
| 2 | **Case attachments** (PDF, images, xlsx, csv a client uploaded) | `ingest.py` -> `preprocess.py` -> `extract.py` (page image + text layer, up to 12,000 chars) | Clients and anyone attaching to a case | Yes (`extract_model`, `classify_model`, `review_model`) | Indirectly: whatever the model transcribes becomes pay items. By design the page schema records `instructions`, `handwritten_text` and `issues` |
| 3 | **Salesforce snapshot fields** (account name, contacts, case subject, supplied email) | `sf_snapshot.py`, `_client_identity()` | Salesforce users, plus the public for `SuppliedEmail` and subject | Account name goes into `client_rules` lookup; `case.subject` is not sent to a model | No (lookup key only) |
| 4 | **`client_rules.json`** | `client_rules.py` -> prompt block in CSV mode | Repo committers (reviewed PRs) | Yes | Yes, but it is code-reviewed, not attacker-writable |
| 5 | **Prompts and schemas** | `llm/prompts*.py`, `schemas/*.json` | Repo committers | Yes | Same: reviewed code |
| 6 | **Env/config** (`PIPELINE_*`, SF credentials, API key) | `config.py` | Deployers (OpenShift secrets) | n/a | Switches (`ALLOW_PII_TO_LLM`, `LLM_AMENDMENTS`) change behaviour, not attacker-writable |
| 7 | **Salesforce SOQL parameters** | `sf_cases.py` (`Q_CASES`, `Q_FILE`) | Case numbers come from the manifest; `lookup()` keeps digits only; the file title is built from a case number that passed that filter | No | Closed: no string reaches SOQL that was not checked as digits |

## Answer to "who can write to what it reads"

**Rows 1 and 2 are the incident.** Both are writable by people outside the company, and row 1 has no authority check between
"someone sent an email" and "pay items changed". That is true before any model is involved: the model only decides *which*
action to record. The deterministic applier then trusts it.

What stands between row 1 and a wrong paycheque today:
- the draft CSV is reviewed by a payroll specialist (the real control; amendment items are marked `email_amendment`,
  with a severity-`info` flag),
- `clean_email()` drops quoted history (`>` lines), banners, URLs, and **everything from a line starting `From:`** (a
  side effect that happens to neutralise two attack shapes, and would also drop a genuine instruction that follows one),
- `PIPELINE_ALLOW_PII_TO_LLM` and `PIPELINE_LLM_AMENDMENTS` switches (off = emails are not read, and a warning flag is added).

## Level 0 (constrained output) finding

The pipeline runs behind a closed API (Anthropic) with a forced tool call and a JSON schema, so logit control is not
available. The schema does constrain `action` to a closed set (`add|modify|exclude|confirm|info`), which stops free text
as an output, but amounts and names are free strings, so it cannot stop a *valid-looking* wrong amendment. Level 0 exists
here but only protects the shape, not the meaning. Provider structured output is already what `call_tool` uses.
