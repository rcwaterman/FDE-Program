"""Plain-language meaning of every Review Flags token the import mapper can emit.

explain() gives (severity, sentence) for the selected-row panel. short() gives a 1-3 word label for the table.
A test checks that every token in import_template.FLAG_TOKENS has an entry here.
"""

import re

_PLAIN = {
    "RATE_FROM_ATTRIBUTES": ("info", "The pay rate came from the employee's printed rate, not the pay lines. Confirm it is the hourly rate."),
    "RATE_ANNOTATED": ("warn", "The printed rate has a note (struck out, obscured or circled). Check the number."),
    "RATE_UNREADABLE": ("error", "The printed rate could not be read, so Total Pay is blank. Enter the rate in Pay Rate."),
    "TOTAL_PAY_NOT_COMPUTED": ("warn", "Total Pay is blank because a rate or a multiplier is missing for these hours."),
    "TIPS_TYPE_ASSUMED": ("warn", "The form does not say whether the tips were cash or credit. Cash was used. Confirm."),
    "TIPS_QTY_AS_AMOUNT": ("warn", "The tips amount was read from the hours column. Confirm the dollar value."),
    "LABEL_CODE_MISMATCH": ("warn", "The written label and the pay code point to different columns. The code was used. Confirm."),
    "PRINTED_DEFAULT": ("info", "This value is the form's printed default, not a handwritten entry. Confirm it was submitted."),
    "EMAIL_AMENDED": ("info", "An email changed this line. Confirm the amendment applies."),
    "NO_EMPLOYEE_NUMBER": ("error", "No employee number on the form or in Salesforce. Enter it before import."),
    "EMP_NUM_FROM_CLOCK": ("warn", "The employee number was taken from the clock number. Confirm."),
    "NO_EMPLOYEE_REF": ("warn", "This pay line is not linked to an employee on the form. Check the name."),
    "NO_CLIENT_ID": ("error", "The client number is missing from the form."),
    "DEDUCTION_NO_CODE": ("warn", "This deduction has no code. Enter the code."),
    "DEDUCTION_CHANGE_REQUESTED": ("info", "The form requests a change to this deduction."),
    "LOW_CONFIDENCE": ("warn", "A value was read with low confidence. Check it against the page."),
    "HUMAN_EDITED": ("info", "A reviewer changed this row."),
    "KIND_INFERRED": ("warn", "No code or label was written. The model judged this to be the kind shown, from the evidence in the row's source items. Confirm it."),
    "MEASURE_CONFLICT": ("warn", "The model read dollars in an hours column. The amount was used as money. Confirm it is not hours."),
}

_UNPLACED = ("warn", "A pay line could not be placed in a column.")

_SHORT = {
    "RATE_FROM_ATTRIBUTES": "Rate from printed rate", "RATE_ANNOTATED": "Rate annotated",
    "RATE_UNREADABLE": "Rate unreadable", "TOTAL_PAY_NOT_COMPUTED": "Total pay blank",
    "TIPS_TYPE_ASSUMED": "Tips type assumed", "TIPS_QTY_AS_AMOUNT": "Tips from hours",
    "LABEL_CODE_MISMATCH": "Label ≠ code", "PRINTED_DEFAULT": "Printed default",
    "EMAIL_AMENDED": "Email change", "NO_EMPLOYEE_NUMBER": "No employee #",
    "EMP_NUM_FROM_CLOCK": "# from clock", "NO_EMPLOYEE_REF": "Unlinked name",
    "NO_CLIENT_ID": "No client #", "DEDUCTION_NO_CODE": "Deduction code?",
    "DEDUCTION_CHANGE_REQUESTED": "Deduction change", "LOW_CONFIDENCE": "Low confidence",
    "HUMAN_EDITED": "Edited", "KIND_INFERRED": "Kind inferred", "MEASURE_CONFLICT": "Dollars in hours",
}


def _key(token: str) -> str:
    return "UNPLACED" if token.startswith("UNPLACED") else token


_UNPLACED_RE = re.compile(r"^UNPLACED (P\d+): code (\S+)(?: '(.*?)')? = (.*) \((.*)\)$")


def explain(token: str) -> tuple[str, str]:
    """(severity, sentence). severity is one of info, warn, error."""
    if token.startswith("UNPLACED"):
        m = _UNPLACED_RE.match(token)
        if not m:
            return _UNPLACED[0], "A pay line could not be placed in a column. Assign it or ignore it."
        item, code, desc, value, reason = m.groups()
        label = f"'{desc}'" if desc else (f"code {code}" if code != "-" else "no code")
        return _UNPLACED[0], (f"Pay line {item} ({label}, {value}) could not be placed in a column: {reason}. "
                              "Assign it to a column or ignore it.")
    return _PLAIN.get(token, ("warn", f"Unrecognised flag: {token}"))


def short(token: str) -> str:
    if token.startswith("UNPLACED"):
        return "Unplaced line"
    return _SHORT.get(token, token.replace("_", " ").lower().capitalize())


def known(token: str) -> bool:
    return _key(token) in _PLAIN or _key(token) == "UNPLACED"
