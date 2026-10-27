"""The import CSV in the use_case/CHARTER.md layout, built from a hand-made v2 record.

The record below is invented (no client, employee or amount comes from a real submission), so these tests can
run anywhere and in CI. They check the layout promises: exact header, one earnings row per employee, the first
deduction on that row, department and rate read from the printed roster.
"""
from __future__ import annotations

import csv
import io
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src.feature_extraction.review.csv_engine.flag_text import known  # noqa: E402
from src.pipeline.exporters import import_template as it  # noqa: E402

IMPORT_MAP = REPO_ROOT / "src" / "pipeline" / "schemas" / "import_column_map.json"

CHARTER = [
    "Employee Last Name", "Pay Rate(Optional)", "Reg Hours", "OT Hours", "Vacation Hours", "Sick Hours",
    "Holiday Hours", "PTO Hours", "Other Hours", "Cash Tips Amount", "Credit Tips Amount", "Bonus Amount",
    "Commission Amount", "Other Amount", "Department (optional)", "Deduction Code (Optional)",
    "Deduction Amount (Optional)", "Project (optional)", "Job (optional)",
]


def _record() -> dict:
    return {
        "record_id": "00000001_069SYN000000000001",
        "document": {
            "doc_type": "coad_payroll_timesheet_report",
            "client": {"name": {"value": "Example Client"}, "number": {"value": "1000"}},
            "pay_period": {"start": {"value": "2026-10-01"}, "end": {"value": "2026-10-14"},
                           "check_date": {"value": "2026-10-16"}, "frequency": {"value": "biweekly"}},
            "contacts": [],
        },
        "source": {"case_number": None, "page_count": 1},
        "extraction": {"review_status": "ready", "review_reasons": []},
        "review_flags": [],
        "code_legend": [{"code": "1", "description": "REGULAR"}],
        "employees": [{
            "employee_ref": "E1", "status": "submitted", "employee_number": None, "clock_number": None,
            "name": {"first": "Sofia", "last": "Reyes"},
            "attributes": {"rate_1": "17.0000", "dept_loc": "SALES LUMBER"},
            "recurring_deductions": [],
        }],
        "pay_items": [
            {"item_id": "P1", "employee_ref": "E1", "employee_name": "Reyes", "item_type": "earning",
             "status": "submitted", "code": "1", "code_description": "REGULAR",
             "quantity": {"value": 55.25, "unit": "hours"}, "amount": None, "rate": None,
             "confidence": "high", "provenance": {"page": 1, "raw_text": "55.25", "source": "handwritten"}},
            {"item_id": "P2", "employee_ref": "E1", "item_type": "deduction", "status": "submitted",
             "code": "M", "code_description": "M", "quantity": None, "amount": 20.0,
             "confidence": "high", "provenance": {"page": 1, "source": "handwritten"}},
            {"item_id": "P3", "employee_ref": "E1", "item_type": "deduction", "status": "submitted",
             "code": "K", "code_description": "K", "quantity": None, "amount": 5.0,
             "confidence": "high", "provenance": {"page": 1, "source": "handwritten"}},
        ],
    }


def _build():
    cmap = it.load_map(IMPORT_MAP)
    return it.build(_record(), cmap)


def test_charter_template_has_the_19_columns_in_order():
    cmap = it.load_map(IMPORT_MAP)
    assert it.resolve_template(cmap, "import_template_charter")["columns"] == CHARTER


def test_csv_header_is_exactly_the_charter_header():
    rows, columns, _meta = _build()
    text = it.csv_bytes(columns, rows).decode("utf-8-sig")
    assert next(csv.reader(io.StringIO(text))) == CHARTER


def test_first_deduction_rides_on_the_earnings_row_and_the_next_gets_its_own_row():
    rows, _columns, meta = _build()
    assert len(rows) == 2
    assert rows[0]["Reg Hours"] == "55.25"
    assert rows[0]["Deduction Code (Optional)"] == "M"
    assert rows[0]["Deduction Amount (Optional)"] == "20.00"
    assert rows[1]["Deduction Code (Optional)"] == "K"
    assert rows[1]["Reg Hours"] == ""
    assert [r["row_kind"] for r in meta["rows"]] == ["earnings", "deduction"]


def test_department_and_rate_come_from_the_printed_roster():
    rows, _columns, _meta = _build()
    assert rows[0]["Department (optional)"] == "SALES LUMBER"
    assert rows[0]["Pay Rate(Optional)"] == "17"
    assert rows[0]["Employee Last Name"] == "Reyes"


def test_legacy_template_is_still_available_with_its_26_columns():
    cmap = it.load_map(IMPORT_MAP)
    assert len(it.resolve_template(cmap, "import_template_for_automation")["columns"]) == 26


def test_every_flag_token_has_an_explanation():
    samples = {"UNPLACED": "UNPLACED P1: code 99 'x' = $1.00 (unmapped code)"}
    for token in it.FLAG_TOKENS:
        assert known(samples.get(token, token)), token
