# Human Review UI for Payroll Extraction

A Streamlit app for reviewing and validating payroll extraction records extracted by Claude. Reviewers can compare the source document against extracted data, accept/reject/edit individual fields, mass-accept groups of fields, and export a final decision log.

## Quick Start

### 1. Install dependencies

```bash
uv sync
```

### 2. Run the app

From the project root:

```bash
streamlit run src/feature_extraction/review/app.py
```

The app will open in your default browser at `http://localhost:8501`.

## Two review engines

`--source run --run-id <id>` picks the engine from the run's data (`engine_select.py`), in this order:
`--engine json|csv` (forces one), then the run's `output_format.json`, then the presence of
`import_csv/_manifest.json`. Otherwise it is the JSON engine, unchanged.

**CSV engine** (`csv_engine/`), for runs made with `--output-format csv`. The import CSV is shown as a table:

0. The source document is on the **left** and the import rows on the **right**, the same split as the JSON
   engine. Use the **Layout** control to choose `Document | Table` (default), `Wider table`, or `Table only`.
   Page and rotation controls match the JSON engine. Both panes scroll on their own.
1. Pick a case from the sidebar. Filter with **All / Needs review / Complete**, or search by case number or client.
   The header shows the pay period and the progress (rows approved, empty lines skipped).
2. **Approve** a row by ticking its checkbox. **Exclude** leaves it out of the import. **Edit** by changing a value
   in its cell: an edit also approves the row, and a row marked "Edited" can be undone by unticking Approve.
   Editing an excluded row is refused until you untick Exclude.
3. **Approve N unflagged rows** approves every pending row that has no flags, in one click.
4. Lines the mapper could not place, and that carry a value, appear in a panel above the table. Choose a column and
   click **Assign**, or click **Ignore**. Lines with no value are skipped automatically and never block a case.
5. **◀ / ▶** above the table step through the rows. The document follows the inspected row to its page, but
   manual paging stays put until you change row. The row's flags (in plain language) and source items sit below
   the table.
6. When every row is decided, the case shows a green banner and a **Download reviewed CSV** button at the top.
   **Next case to review →** moves to the next incomplete case.

Rules that matter:
- Decisions are stored in `runs/<id>/csv_review/<record_id>.json`, separate from `review_records/`. Each keeps the
  fingerprint of the row it was made on. If the pipeline changes that row, the decision shows as **changed, review
  again** and the case is not complete.
- Excluded rows are never exported by accident: if one batch ticks both Approve and Exclude, Exclude wins.
- Export writes `runs/<id>/import_csv_reviewed/<record_id>.csv` for complete cases only. Rejected rows are dropped,
  edited rows are flagged `HUMAN_EDITED` with a recomputed Total Pay, and assigned lines are added to that employee's
  earnings row.
- Labels in the case list are kept stable on purpose. The sidebar radio's value follows its label, so a label
  that changes after a decision moves the selection to another case.

```bash
streamlit run src/feature_extraction/review/app.py -- --source run --run-id <run-id>
./src/feature_extraction/review/run.sh --source run --run-id <run-id>   # run.sh forwards these args to the app
```

## Features

### Navigation
- **Case list** (sidebar): Click to jump between records. Filtered by status/severity if desired.
- **Progress bar**: Shows how many records have been reviewed (complete status).
- **Filters**: View only unreviewed records, records with flags, errors, or warnings.
- **Next/Previous buttons**: Quick navigation between cases (respects active filters).

### Per-Case Review
- **Document viewer** (left): Rendered PDF page for the current case. Page selector to jump between pages.
- **Page text**: Expandable section showing OCR text transcript for reference.
- **Field table** (right): All pay items grouped by employee. Shows code, description, amount, status badges, confidence level, and provenance hint.

### Accept/Reject/Edit
- **Accept**: Mark a field as correct (no changes needed).
- **Reject**: Mark a field as wrong and should be excluded.
- **Edit**: Correct a specific value (e.g., amount or quantity). Only the changed fields are stored, not a full item copy.
- **Mass-accept buttons**:
  - "Accept all high-confidence in this case" — accept only items with `confidence: high`.
  - "Accept all in this employee group" — accept all items for one employee.
  - "Accept all remaining unreviewed" — accept everything not yet decided (fast path for clean cases).

### Persistence
- Decisions are stored in a non-destructive `human_review` block per record (top-level key).
- Pipeline-owned fields (`pay_items[].status`, `confidence`, etc.) are never mutated.
- Decisions survive app restarts and can be re-visited to view/update.
- JSONL (`src/feature_extraction/payroll_extractions.jsonl`) is automatically regenerated from record files after every save, staying in sync.

### Export
- Click **Export** to download a `review_decisions.xlsx` workbook.
- Contains two sheets:
  - **Review_Decisions**: One row per item with a decision, including corrected values and notes.
  - **Summary**: Aggregate counts (total records, complete/in-progress/not-started, total accept/reject/edit decisions).

## Data Format

### `human_review` block (per record)

```json
{
  "human_review": {
    "reviewed_by": "reviewer@example.com",
    "status": "in_progress",
    "created_at": "2026-10-02T09:14:00-04:00",
    "updated_at": "2026-10-02T09:31:12-04:00",
    "field_decisions": [
      {
        "item_id": "P0001",
        "decision": "accept",
        "corrected_value": null,
        "note": null,
        "decided_by": "reviewer@example.com",
        "decided_at": "2026-10-02T09:14:05-04:00"
      },
      {
        "item_id": "P0007",
        "decision": "edit",
        "corrected_value": {"amount": 412.50},
        "note": "Handwritten total was 412.50, OCR read 442.50",
        "decided_by": "reviewer@example.com",
        "decided_at": "2026-10-02T09:18:40-04:00"
      }
    ]
  }
}
```

- `reviewed_by`: Email of the reviewer (set once at session start).
- `status`: `in_progress` = some items decided; `complete` = all items decided (auto-updated).
- `field_decisions`: List of per-item decisions (item_id → decision + optional corrected value + note).
- `corrected_value`: Only the fields that changed, never a full item copy. `null` for accept/reject.

## Code Structure

- **`app.py`**: Streamlit entry point. Handles page layout, sidebar navigation, per-case rendering.
- **`data_store.py`**: Load/save records with atomic writes. Handles `human_review` block creation/upsert, JSONL regeneration.
- **`doc_render.py`**: PDF/image rendering via `pypdfium2` with on-disk caching. Falls back to direct image display for non-PDFs.
- **`review_model.py`**: Pure Python helpers (no Streamlit): severity derivation, case index building, bulk-accept logic, grouping.
- **`export_review.py`**: Build Excel workbook from decisions. Standalone script or called by the app's download button.

## Testing

Run the test suite:

```bash
# Review model tests (pure functions)
python -m pytest tests/review/test_review_model.py -v

# Data store tests (persistence, atomic writes)
python -m pytest tests/review/test_data_store.py -v

# All review tests
python -m pytest tests/review/ -v
```

## Verification Checklist

Before considering the review tool "production-ready", manually verify:

1. ✅ Install and run `streamlit run src/feature_extraction/review/app.py`.
2. ✅ Review 2–3 real cases end-to-end:
   - One with `review_flags` severity `error` (to test navigation and manual review).
   - One with many employees and pay items (to test grouping + bulk-accept).
   - One multi-page PDF (to test page selector).
3. ✅ After decisions, check the record file directly (`cat src/feature_extraction/records/<record_id>.json`) — confirm `human_review` block is present and well-formed.
4. ✅ Confirm `src/feature_extraction/payroll_extractions.jsonl` was regenerated (grep for the record_id in the JSONL; it should contain the new `human_review` block).
5. ✅ Close and relaunch the app, navigate back to a reviewed case — confirm decisions show as "already decided" (highlighted rows with timestamp).
6. ✅ Export the workbook and confirm Review_Decisions and Summary sheets contain the test decisions.
7. ✅ Test bulk-accept: confirm "Accept all remaining unreviewed" doesn't overwrite items already explicitly rejected/edited.

## Schema Changes

The `src/feature_extraction/schema/payroll_extraction.schema.json` was updated to add an optional `human_review` property. The change is backward-compatible:
- Records without `human_review` are valid (optional property).
- Records with `human_review` must conform to the schema.
- The pipeline (`src/pipeline/`) does not yet populate `human_review`; this is review-app-only data.

See `src/feature_extraction/schema/README.md` section 6 (Schema revisions) for the changelog entry.

## Reviewer Email Configuration

The app defaults to `reviewer@example.com` (set via env var `REVIEWER_EMAIL` or hardcoded fallback). Reviewers can change it in the sidebar before starting a session. The email is recorded per decision for audit/attribution purposes.

## Future Enhancements (v2)

- **Keyboard shortcuts**: Add `j`/`k` for prev/next case, `a`/`r` for accept/reject to speed up mouse-free review.
- **Bounding box highlights**: If the pipeline adds pixel-coordinate provenance in the future, overlay highlighted regions on the rendered pages.
- **Multi-user concurrency**: Add locking/version tracking to safely support multiple reviewers on the same dataset.
- **Comment threads**: Allow reviewers to leave comments on specific fields for discussion before final decision.
- **Quality metrics**: Track reviewer agreement, decision reversal rate, and flag-accuracy over time.

## Troubleshooting

### "No attachment found for record <id>"
The app couldn't find the source PDF/image. Verify the attachment exists in `runs/<run-id>/attachments/<record_id>.<ext>` and that the record_id is correct.

### "Page out of range"
The page number selected exceeds the PDF's page count. Check the record's `source.page_count` field.

### "Failed to render PDF page"
Rare pypdfium2 rendering error. Confirm the PDF is valid and not corrupted. Try re-exporting the source document from Salesforce.

### "Error loading <record_file>"
The record JSON is malformed. Validate it against `src/feature_extraction/schema/payroll_extraction.schema.json` with `jsonschema` to find the offending field.

## Contact

Questions or issues? See the project docs or reach out to the pipeline maintainers.
