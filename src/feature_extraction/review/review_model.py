"""Pure Python helpers for review logic (no Streamlit dependencies)."""
from datetime import datetime, timezone
from typing import Any, Callable, Optional


def get_max_flag_severity(review_flags: list) -> str:
    """Derive max severity from review_flags array. Returns 'error', 'warning', 'info', or 'none'."""
    if not review_flags:
        return "none"
    severities = {"error": 3, "warning": 2, "info": 1, "none": 0}
    max_sev = max(severities.get(f.get("severity", "info"), 1) for f in review_flags)
    for sev, val in severities.items():
        if val == max_sev and sev != "none":
            return sev
    return "none"


def build_case_index(records: dict[str, dict]) -> list[dict]:
    """Build a list of case summaries for navigation. Records keyed by record_id."""
    cases = []
    for record_id, record in records.items():
        source = record.get("source", {})
        document = record.get("document", {})
        pay_items = record.get("pay_items", [])
        review_flags = record.get("review_flags", [])
        human_review = record.get("human_review", {})

        client = document.get("client") or {}
        if isinstance(client, dict):
            name_obj = client.get("name", {})
            client_name = (
                name_obj.get("value", "Unknown")
                if isinstance(name_obj, dict)
                else "Unknown"
            )
        else:
            client_name = "Unknown"
        doc_type = document.get("doc_type", "Unknown")

        # Count decisions from human_review
        field_decisions = human_review.get("field_decisions", [])
        decided_item_ids = {d["item_id"] for d in field_decisions}
        n_decidable = len(pay_items)
        n_decided = len(decided_item_ids)

        # Completion status
        human_review_status = human_review.get("status", "not_started")
        if n_decidable == 0:
            is_complete = True
        else:
            is_complete = n_decided >= n_decidable and human_review_status == "complete"

        cases.append(
            {
                "record_id": record_id,
                "case_number": source.get("case_number", ""),
                "client_name": client_name,
                "doc_type": doc_type,
                "n_pay_items": n_decidable,
                "n_employees": len(record.get("employees", [])),
                "max_flag_severity": get_max_flag_severity(review_flags),
                "n_decided": n_decided,
                "n_total_decidable": n_decidable,
                "human_review_status": human_review_status,
                "is_complete": is_complete,
            }
        )

    return sorted(cases, key=lambda c: c["record_id"])


def filter_cases(
    cases: list[dict], unreviewed_only=False, has_flags_only=False, errors_only=False, warnings_only=False
) -> list[dict]:
    """Filter case list by review status/severity."""
    filtered = cases

    if unreviewed_only:
        filtered = [c for c in filtered if c["human_review_status"] == "not_started"]

    if has_flags_only:
        filtered = [c for c in filtered if c["max_flag_severity"] != "none"]

    if errors_only:
        filtered = [c for c in filtered if c["max_flag_severity"] == "error"]

    if warnings_only:
        filtered = [c for c in filtered if c["max_flag_severity"] in ("error", "warning")]

    return filtered


def search_cases(cases: list[dict], query: str) -> list[dict]:
    """Filter cases by case_number or client_name substring match (case-insensitive)."""
    if not query or not query.strip():
        return cases
    q = query.strip().lower()
    return [
        c for c in cases
        if q in c.get("case_number", "").lower() or q in c.get("client_name", "").lower()
    ]


def group_pay_items_by_employee(record: dict) -> list[tuple[str, list[dict]]]:
    """Group pay_items by employee_ref. Returns list of (employee_name, [items])."""
    pay_items = record.get("pay_items", [])
    employees = {e["employee_ref"]: e for e in record.get("employees", [])}

    grouped = {}
    for item in pay_items:
        emp_ref = item.get("employee_ref")
        if emp_ref not in grouped:
            emp = employees.get(emp_ref, {})
            emp_name = "Unknown"

            if emp and isinstance(emp, dict):
                name_obj = emp.get("name")
                if isinstance(name_obj, dict):
                    emp_name = name_obj.get("full", "Unknown")

            if not emp_name or emp_name == "Unknown":
                emp_name = f"Employee {emp_ref}" if emp_ref else "Unassigned"
            grouped[emp_ref] = {"employee_name": emp_name, "items": []}

        grouped[emp_ref]["items"].append(item)

    return [(data["employee_name"], data["items"]) for emp_ref, data in sorted(grouped.items())]


def bulk_accept(items: list[dict], existing_decisions: dict, predicate: Callable[[dict], bool]) -> list[dict]:
    """
    Build a list of new 'accept' decisions for items matching the predicate,
    without overwriting existing decisions. Returns list of field_decision dicts.
    existing_decisions is a dict keyed by item_id.
    """
    new_decisions = []
    now = datetime.now(timezone.utc).astimezone().isoformat()

    for item in items:
        item_id = item.get("item_id")
        # Never overwrite an existing decision
        if item_id not in existing_decisions and predicate(item):
            new_decisions.append(
                {
                    "item_id": item_id,
                    "decision": "accept",
                    "corrected_value": None,
                    "note": None,
                    "decided_by": None,  # Will be filled by caller
                    "decided_at": now,
                }
            )

    return new_decisions


def is_group_fully_decided(items: list[dict], existing_decisions: dict) -> bool:
    """Check if all items in a group have decisions."""
    item_ids = {item.get("item_id") for item in items}
    return item_ids.issubset(set(existing_decisions.keys()))


def get_decision_for_item(item_id: str, existing_decisions: dict) -> Optional[dict]:
    """Get the human_review decision for an item_id, if it exists."""
    return existing_decisions.get(item_id)
