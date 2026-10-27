# TC3 submission: 

## Deviation Explanation

My project does not actively use an agent-in-the-loop with the user. Instead, the agentic pipeline is utilized to extract data from images and format it in an import template, which will be reviewed and approved by a human. The value add is in the reduction of labor hours associated with an otherwise manual data entry task. The "retrieval" in this case is the data extraction that happens for each payroll entry, which has been successfully built and tested. In order to still make good progress on this project with this deviation, I have built a review UI that the Payroll Specialist can use to verify the output of the extraction agent. All application code can be found under src/. 

![Review UI: case 00000001 with the document and the row table](submission_images/Screenshot%202026-10-27%20at%203.19.02%E2%80%AFPM.png)

## Build

I have added api_calls.jsonl to this challenge folder, which logs the API calls to Claude as well as the tool usage. This is the log file generated during the extraction process so I can monitor the results of the extraction. Additionally, I have added run_summary.jsonl, which gives additional debug info about the extraction run including extraction failures and summary statistics. **NOTE THAT ALL DATA IN THIS REPOSITORY IS SYNTHETIC AND NOT ACTUAL CLIENT DATA/PII.**

## Ship

### Ten labelled questions

See prior explanation. I have added 10 synthetic data extractions under the submission_sample_data folder to satisfy this requirement. 

![Source payroll timesheet (synthetic), case 00000001](submission_images/Screenshot%202026-10-27%20at%203.20.54%E2%80%AFPM.png)

### Schema implemented, not redesigned

There is a much more complex orchestration pipeline that occurs here to preprocess the images, prepare them for data extraction, and finally allow claude to extract the data such that it can be saved in the proper csv format. See src/pipeline/llm.

![Review UI: a pay line that needs a column assignment](submission_images/Screenshot%202026-10-27%20at%203.22.49%E2%80%AFPM.png)

### At least three techniques measured

This is satisfied by run_summary.json and api_calls.jsonl. The primary measures are the extraction metrics and flags created using the sonnet 5-5 model. This has proven to be both accurate and cost effective.

![Review UI: case list filtered to needs review (102 cases)](submission_images/Screenshot%202026-10-27%20at%203.23.12%E2%80%AFPM.png)

### A new `@app.api` endpoint that names its sources

The approach used here is to handle all preprocessing and postprocessing programmatically, so Claude is only responsible for the visual data extraction portion of the pipeline. See api_calls.jsonl for the LLM interaction portion of the pipeline.

![Review UI: row R001 with its source items and their provenance](submission_images/Screenshot%202026-10-27%20at%203.20.32%E2%80%AFPM.png)

### `use_case/decisions.md` has the retrieval decision and the four non-text answers

Evidence in this repo: `use_case/decisions.md`.

### It still runs in Docker

Evidence in this repo: `01_Product_Engineering/challenge/Dockerfile`, `Dockerfile.review`, `docker-compose.yml`.

Additionally, the images, above, were all captured from the docker container to review the output of the extraction pipeline. The screenshot, below, is the terminal on the extraction docker run:

![Extraction Docker Terminal View](submission_images/Screenshot%2026-10-27%at%4.07.37%PM.png)

## Share

### A demo to a real user, with their feedback recorded in your own words

Evidence in this repo: `use_case/stakeholders.md` (Week 3 Payroll Manager rows).

### What you would swap to run this at work

Three lines in your README: data, model or endpoint, and the approval you would need.
Evidence in this repo: `use_case/ecosystem.md` (Week 3 answers).
