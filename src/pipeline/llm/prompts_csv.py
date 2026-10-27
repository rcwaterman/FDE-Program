"""Prompts for the CSV output variant. The JSON prompt (prompts.py) is unchanged.

The CSV variant keeps the transcription rules and adds a second job: say what each value is, with the evidence
for it. Uncoded tips and other pay were being recorded by position alone; this variant records the meaning too.
"""
from .prompts import PAGE_SYSTEM, PAGE_USER

PROMPT_VERSION_CSV = "2026-10-09.csv1"

CSV_ADDENDUM = """

For the import CSV you also classify every entry. value_text stays exactly as written. In addition, for each entry:
- measure: hours, dollars, days or unknown. Use dollars for money: a $ sign, decimals, a written or circled code 28 for an amount, or a number far above the employee's pay-period hours in an hours column.
- kind: what the value is. One of regular, overtime, double_time, vacation, sick, holiday, pto, tips_cash, tips_credit, tips_unspecified, bonus, commission, retro, other_pay, reimbursement, deduction, unknown.
- kind_basis: how you decided. One of code (a code is written beside the value), legend (the page's code legend says what the code is), label (a word is written with the value), sign ($, decimals), precedent (the same handwritten word or position is used elsewhere on this page), client_rule, magnitude, position, none.
- kind_confidence: high, medium or low.
- evidence: a short quote or description of what on the page supports the kind.

When a value has no code, use this order of evidence: a written code, the page's code legend, a written label, a dollar sign or decimals, precedent on the same page (for example one employee's "Tip" written beneath the code box, so the same position for others is probably tips), client rules, magnitude against the employee's hours and rate, and column position last.

Every handwritten number tied to an employee must be an entry, including amounts in the margin or outside the printed grid (use column margin for those). Never leave a value only in note, issues or handwritten_text.

If you cannot tell the kind, use kind unknown with kind_confidence low. Do not guess silently.
Set client_rules_applied to the ids of any client rules you used (an empty array when none).
"""

PAGE_SYSTEM_CSV = PAGE_SYSTEM + CSV_ADDENDUM
PAGE_USER_CSV = PAGE_USER


def user_suffix(rules_block: str | None) -> str:
    """Appended to the user message when the client has confirmed rules."""
    return f"\n\n{rules_block}" if rules_block else ""
