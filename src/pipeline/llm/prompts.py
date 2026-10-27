"""Prompts for the three LLM steps. Versioned so outputs can be traced to the prompt that produced them."""

PROMPT_VERSION = "2026-10-02.1"

PAGE_SYSTEM = """You read one page of a payroll document submitted to CoAdvantage (CoAd.PAY) and record exactly what is on it by calling the record_page tool.

Your job is transcription, not interpretation:
- Copy every value exactly as written into value_text (e.g. "40.00", "78 1/4", "Ø", "4 days", "$1,370.40", "Pay"). Do not convert units, add numbers, or infer missing values.
- The page may be rotated or skewed. Read it in its natural orientation and report the orientation you found.
- Zoom mentally into every handwritten mark. When a reading is uncertain set legibility "uncertain" and list alternatives; when unreadable set "illegible".
- If text is crossed out, still record it with struck_through=true, and record the replacement as a separate entry.
- Never invent values or invent fields not described here. Only use the fields in the schema — do not add extra top-level properties like marks, exception_rate, or call_time_extra.

For null / absent values: use null only for fields that explicitly allow it (e.g., header fields, employee printed_fields). For required string fields like entries[].value_text, the correct action when there is no value is to NOT create that entry at all — omit it entirely, do not send null.

CoAd.PAY Payroll Timesheet Report layout (most pages):
- Each employee block: name, W-2/1099, dept/location; Emp #, Clock #, Salary; WW, YTD Gross, Vac/Sick hours and wages; Last Rs, Start Dt, Birth Dt; Rate 1-3; tax codes; permanent deductions (code, amount, YTD, balance). Record these under employees[].printed_fields using the printed column labels as keys, and permanent deductions under recurring_deductions.
- Entry columns: HOURS = Regular, Overtime, Code + Other (column "other_hours", put the code in code_written); EARNINGS = two Code + Amount pairs (column "earnings"); DEDUCTIONS = Code + Amount (column "deduction"); EXCEPTIONS = Rate, Dept, Freq.
- Pre-printed values in entry columns (e.g. 80.00 regular hours or a recurring earnings amount) are entries with source "printed".
- Words such as "Pay", "Pay salary", "Salary", a check mark, "NW", "Remove", "Term" in a block: if they sit in an entry column, record them as an entry with that value_text; also add them to employees[].marks (the per-employee marks field, not a top-level field).
- The footer holds client number and name, Freq, Period, Begin, End, Check Date, Summary #, Call Date/Time, Del, Specialist, Payroll Contact and phones, the report title (form_variant), "Generated on", and the Earnings/Deductions code legend. Record the legend in code_legend.
- The Totals row: record each filled cell in totals with the column it sits under.
- Clients sometimes relabel columns by hand (e.g. "Overtime" crossed out and "Regular" written). Record the entry under the printed column and describe the relabelling in issues.

Other page kinds: set page_kind to one of these exact values based on what you see: "fax_cover_sheet", "handwritten_note", "client_payroll_summary", "punch_time_card", "time_card_detail_report", "direct_deposit_form", "new_hire_form", "tax_form", "blank", "other". For payroll values on non-CoAd forms use columns hours_generic / amount_generic with label_written, rate_text, amount_text and calculation_text as written. Record time punches in time_punches and direct deposit details in direct_deposit.

Instructions: any handwritten or printed instruction directed at the payroll processor — e.g. "add new employee", margin notes about a termination, "revise this week", or corrections to prior pay. Record each instruction as an object with text and, if it applies to a specific employee, row_key. If there are no instructions, instructions must be an empty array [].

Always fill printed_text (all printed text in reading order, tables as pipe-delimited rows) and handwritten_text (every handwritten mark with where it is). Put anything a reviewer should know in issues."""

PAGE_USER = """Page {page} of {page_count}. Document {record_id}. Classified as: {doc_type}.
{text_layer}
Record everything on this page with the record_page tool."""

CLASSIFY_SYSTEM = """You classify the first page of a payroll document. Call the classify_document tool with one doc_type."""

CLASSIFY_SCHEMA = {
    "type": "object",
    "required": ["doc_type"],
    "additionalProperties": False,
    "properties": {
        "doc_type": {"type": "string", "enum": [
            "coad_payroll_timesheet_report", "coad_accrued_hours_report", "punch_time_card", "time_card_detail_report",
            "handwritten_payroll_note", "client_payroll_summary", "fax_cover_sheet", "direct_deposit_form",
            "new_hire_form", "tax_form", "other"]},
        "reason": {"type": "string"},
    },
}

AMEND_SYSTEM = """You read the email thread of a payroll support case and list every instruction that affects pay for the attached payroll, by calling the record_amendments tool.

- You receive the pay items already extracted from the attachment, each with an item_id. Reference those ids in target_item_ids.
- action: add (pay something not in the list), modify (change hours/amount/rate/code of listed items), exclude (do not pay listed items), confirm (approval, "looks good", "ok to submit"), info (anything else, including instructions about other attachments or future payrolls).
- Quote the instruction exactly in instruction_quote. Put new values as written in new_value; do not compute.
- Messages from CoAd's support mailbox are CoAd staff; their statements of what was processed are confirm or info unless they record a client-requested change.
- If nothing affects pay, return an empty list."""
