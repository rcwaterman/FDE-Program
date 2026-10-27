"""Streamlit app for human review of payroll extraction records.

By default, reviews the curated reference set (src/feature_extraction/payroll_extractions.jsonl).
To review the output of a specific pipeline run instead (e.g. one produced by
`python -m src.pipeline.cli run --run-id <id>`), launch with:

    streamlit run app.py -- --source run --run-id <run_id>

--run-id must match a run directory under runs/ (i.e. runs/<run_id>/payroll_extractions.jsonl
must exist).
"""
import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

# Streamlit only adds this script's own directory to sys.path, never the repo
# root or cwd -- so the absolute `from src...` imports below need the repo
# root put on sys.path explicitly, regardless of how/where this is launched from.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_FEATURE_EXTRACTION_DIR = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import streamlit as st
import streamlit.components.v1 as components

from src.feature_extraction.review.data_store import (
    load_all_records,
    materialize_from_run_jsonl,
    save_decision,
    save_decision_batch,
    undo_decision,
    _regenerate_jsonl,
)
from src.feature_extraction.review.doc_render import get_page_image, get_page_count
from src.feature_extraction.review.engine_select import select_engine
from src.feature_extraction.review.csv_engine.app_csv import run_csv_review
from src.feature_extraction.review.export_review import export_review_decisions
from src.feature_extraction.review.review_model import (
    build_case_index,
    filter_cases,
    search_cases,
    get_decision_for_item,
    group_pay_items_by_employee,
    bulk_accept,
)


from src.logsetup import configure as configure_logging

log = logging.getLogger(__name__)


def _parse_source_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=["opus", "run"], default="opus")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--runs-dir", default=None)
    # auto: pick the engine from the run's data (see engine_select.py); json/csv force one.
    parser.add_argument("--engine", choices=["auto", "json", "csv"], default="auto")
    # Streamlit forwards everything after `--` as sys.argv[1:]; parse_known_args
    # defensively ignores anything unexpected rather than crashing the app.
    args, _ = parser.parse_known_args(sys.argv[1:])
    return args


st.set_page_config(page_title="Payroll Extraction Review", layout="wide")

# Paths
BASE_DIR = _FEATURE_EXTRACTION_DIR
ATTACHMENTS_DIR = str(_REPO_ROOT / "src" / "data" / "attachments")
CACHE_DIR = str(BASE_DIR / "review" / ".cache")

# Scroll-sync component: watches the field review container's scroll position
# and reports back which page's fields are currently in view (see
# components/scroll_sync/index.html for the implementation).
_scroll_sync = components.declare_component(
    "scroll_sync", path=str(Path(__file__).parent / "components" / "scroll_sync")
)

_source_args = _parse_source_args()
DATA_SOURCE_LABEL = "Opus reference set"

if _source_args.source == "run":
    if not _source_args.run_id:
        st.error(
            "`--source run` requires `--run-id <id>`. Launch with:\n\n"
            "`streamlit run app.py -- --source run --run-id <run_id>`"
        )
        st.stop()
    runs_dir = Path(_source_args.runs_dir) if _source_args.runs_dir else (_REPO_ROOT / "runs")
    RUN_ROOT = runs_dir / _source_args.run_id
    # Pipeline runs download attachments into the run folder; the reference-set default
    # (src/data/attachments) doesn't contain them. Cache per run so renders can't collide.
    ATTACHMENTS_DIR = str(RUN_ROOT / "attachments")
    CACHE_DIR = str(RUN_ROOT / ".cache")
    configure_logging(RUN_ROOT, "review")
    log.info("review start: run=%s source=%s", _source_args.run_id, _source_args.source)
    # CSV-format runs get their own review engine. It returns early, so the JSON engine below never runs.
    if select_engine(RUN_ROOT, _source_args.engine) == "csv":
        run_csv_review(RUN_ROOT, ATTACHMENTS_DIR, CACHE_DIR, DATA_SOURCE_LABEL)
        st.stop()
    SOURCE_JSONL = RUN_ROOT / "payroll_extractions.jsonl"
    RECORDS_DIR = str(RUN_ROOT / "review_records")
    JSONL_PATH = str(RUN_ROOT / "review_records.jsonl")
    DATA_SOURCE_LABEL = f"Pipeline run: {_source_args.run_id}"

    if not SOURCE_JSONL.exists():
        st.error(f"No such run output found: `{SOURCE_JSONL}`")
        st.stop()

    # Create flat per-record files for any record not already materialized (never
    # overwrites an existing flat file, so prior human_review decisions survive
    # re-opening this run across app restarts).
    materialize_from_run_jsonl(str(SOURCE_JSONL), RECORDS_DIR)
    _regenerate_jsonl(RECORDS_DIR, JSONL_PATH)
else:
    RECORDS_DIR = str(BASE_DIR / "records")
    JSONL_PATH = str(BASE_DIR / "payroll_extractions.jsonl")


@st.cache_data(ttl=300)
def load_records_cached():
    """Load records with caching."""
    return load_all_records(RECORDS_DIR)


def init_session_state():
    """Initialize Streamlit session state."""
    if "current_record_index" not in st.session_state:
        st.session_state.current_record_index = 0
    if "current_page" not in st.session_state:
        st.session_state.current_page = 1
    if "editing_item_id" not in st.session_state:
        st.session_state.editing_item_id = None
    if "reviewer_email" not in st.session_state:
        st.session_state.reviewer_email = os.getenv(
            "REVIEWER_EMAIL", "reviewer@example.com"
        )
    if "filter_unreviewed" not in st.session_state:
        st.session_state.filter_unreviewed = False
    if "filter_has_flags" not in st.session_state:
        st.session_state.filter_has_flags = False
    if "filter_errors" not in st.session_state:
        st.session_state.filter_errors = False
    if "filter_warnings" not in st.session_state:
        st.session_state.filter_warnings = False
    if "page_rotations" not in st.session_state:
        st.session_state.page_rotations = {}


def render_page_image(record, page_num, rotation=0):
    """Render and display a page image."""
    try:
        img_path = get_page_image(
            record["record_id"], page_num, ATTACHMENTS_DIR, CACHE_DIR, rotation=rotation
        )
        st.image(img_path, use_container_width=True, caption=f"Page {page_num}")
    except Exception as e:
        log.exception("page image failed record=%s page=%s", record.get("record_id"), page_num)
        st.error(f"Failed to load page image: {e}")


def render_page_text_expander(record, page_num):
    """Show page text in an expander."""
    pages = record.get("pages", [])
    if page_num - 1 < len(pages):
        page_data = pages[page_num - 1]
        with st.expander("📄 Page text"):
            if page_data.get("printed_text"):
                st.text("**Printed:**")
                st.text(page_data["printed_text"])
            if page_data.get("handwritten_text"):
                st.text("**Handwritten:**")
                st.text(page_data["handwritten_text"])


def render_field_row(item, item_id, decision, employee_name, record_id, records):
    """Render a single pay item with clean UX."""
    code = item.get("code", "")
    desc = item.get("code_description", "")
    qty = item.get("quantity", {})
    qty_val = qty.get("value", "") if isinstance(qty, dict) else ""
    qty_unit = qty.get("unit", "") if isinstance(qty, dict) else ""
    amount = item.get("amount", "")
    status = item.get("status", "")
    confidence = item.get("confidence", "")
    prov = item.get("provenance", {})

    # Color codes
    conf_color = {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(confidence, "⚪")

    # Create a container for the field
    with st.container(border=True):
        # Row 1: Code + description (large, bold) next to the amount
        header_col1, header_col2 = st.columns([3, 1])
        with header_col1:
            st.markdown(f"#### {code} — {desc}" if desc else f"#### {code}")
        with header_col2:
            st.markdown(f"#### {f'${amount:,.2f}' if amount else '$—'}")

        # Row 2: quantity / confidence on one caption line
        meta_bits = []
        if qty_val != "":
            meta_bits.append(f"{qty_val} {qty_unit}".strip())
        meta_bits.append(f"{conf_color} {confidence} confidence")
        st.caption(" · ".join(meta_bits))

        # Status caption (plain, no emoji)
        st.caption(f"Status: {status.replace('_', ' ').title()}")

        # Row 3: provenance / location hint
        prov_str = f"📍 {prov.get('source', '')} · page {prov.get('page', '')} · {prov.get('location', '')}"
        st.caption(prov_str)

        st.markdown("")  # small spacer

        # Row 4: decision state banner OR the approve/reject/edit controls.
        # This is its own full-width row (not squeezed next to other info)
        # so the buttons have room to render their labels clearly.
        if decision:
            decision_type = decision.get("decision")
            decided_by = decision.get("decided_by", "unknown")
            decided_at = decision.get("decided_at", "")
            banner_col, undo_col = st.columns([4, 1])
            with banner_col:
                if decision_type == "accept":
                    st.success(f"✅ **Approved** by {decided_by}")
                elif decision_type == "reject":
                    st.error(f"❌ **Rejected** by {decided_by}")
                else:
                    note = decision.get("note") or ""
                    st.warning(f"✏️ **Edited** by {decided_by} — {note}" if note else f"✏️ **Edited** by {decided_by}")
            with undo_col:
                if st.button("↩️ Undo", key=f"undo_{item_id}", use_container_width=True, help="Remove this decision and review again"):
                    undo_decision(record_id, item_id, RECORDS_DIR, JSONL_PATH)
                    load_records_cached.clear()
                    st.session_state.flash = "↩️ Decision undone"
                    st.rerun()
        else:
            st.markdown("**Is this value correct?**")
            btn_col1, btn_col2, btn_col3 = st.columns(3)
            with btn_col1:
                if st.button(
                    "✅ Approve",
                    key=f"accept_{item_id}",
                    use_container_width=True,
                    type="primary",
                    help="This value is correct as extracted",
                ):
                    save_decision(
                        record_id,
                        {
                            "item_id": item_id,
                            "decision": "accept",
                            "corrected_value": None,
                            "note": None,
                        },
                        RECORDS_DIR,
                        JSONL_PATH,
                        st.session_state.reviewer_email,
                    )
                    load_records_cached.clear()
                    st.session_state.flash = "✅ Approved"
                    st.rerun()
            with btn_col2:
                if st.button(
                    "❌ Reject",
                    key=f"reject_{item_id}",
                    use_container_width=True,
                    help="This value is wrong and should be excluded",
                ):
                    save_decision(
                        record_id,
                        {
                            "item_id": item_id,
                            "decision": "reject",
                            "corrected_value": None,
                            "note": None,
                        },
                        RECORDS_DIR,
                        JSONL_PATH,
                        st.session_state.reviewer_email,
                    )
                    load_records_cached.clear()
                    st.session_state.flash = "❌ Rejected"
                    st.rerun()
            with btn_col3:
                if st.button(
                    "✏️ Edit value",
                    key=f"edit_{item_id}",
                    use_container_width=True,
                    help="Correct the amount or quantity",
                ):
                    st.session_state.editing_item_id = item_id
                    st.rerun()

        # Edit form (only if editing this item)
        if st.session_state.editing_item_id == item_id:
            st.divider()
            st.subheader("Correct this field")

            # Show original values for reference
            st.caption("**Original extracted values:**")
            orig_col1, orig_col2 = st.columns(2)
            with orig_col1:
                st.metric("Amount", f"${amount:,.2f}" if amount else "$—")
            with orig_col2:
                st.metric("Quantity", f"{qty_val} {qty_unit}".strip() if qty_val else "—")

            with st.form(key=f"edit_form_{item_id}"):
                st.write("**Enter corrected values below:**")
                col_amt, col_qty = st.columns(2)
                with col_amt:
                    st.caption(f"_Original: {f'${amount:,.2f}' if amount else '—'}_")
                    new_amount = st.number_input(
                        "Corrected Amount ($)",
                        value=float(amount) if amount else 0.0,
                        step=0.01,
                        help="Enter the correct dollar amount for this line item"
                    )
                with col_qty:
                    st.caption(f"_Original: {qty_val if qty_val else '—'} {qty_unit}_")
                    new_qty = st.number_input(
                        f"Corrected Quantity ({qty_unit if qty_unit else 'units'})",
                        value=float(qty_val) if qty_val else 0.0,
                        step=0.1,
                        help="Enter the correct quantity for this line item"
                    )

                st.write("**Why are you correcting this?**")
                new_note = st.text_area(
                    "Explanation",
                    "",
                    placeholder="e.g., 'Handwritten amount was 412.50, OCR misread it as 442.50'",
                    help="Briefly explain the discrepancy so reviewers can understand the correction"
                )

                col_save, col_cancel = st.columns(2)
                with col_save:
                    if st.form_submit_button("✅ Save Correction", use_container_width=True):
                        corrected = {}
                        if new_amount != (float(amount) if amount else 0.0):
                            corrected["amount"] = new_amount
                        if new_qty != (float(qty_val) if qty_val else 0.0):
                            corrected["quantity"] = {"value": new_qty, "unit": qty_unit}

                        save_decision(
                            record_id,
                            {
                                "item_id": item_id,
                                "decision": "edit",
                                "corrected_value": corrected if corrected else None,
                                "note": new_note if new_note else None,
                            },
                            RECORDS_DIR,
                            JSONL_PATH,
                            st.session_state.reviewer_email,
                        )
                        load_records_cached.clear()
                        st.session_state.flash = "✏️ Correction saved"
                        st.session_state.editing_item_id = None
                        st.rerun()
                with col_cancel:
                    if st.form_submit_button("Cancel", use_container_width=True):
                        st.session_state.editing_item_id = None
                        st.rerun()


def main():
    """Main app."""
    init_session_state()

    # Apply any page-sync requested by the scroll-sync component on the PREVIOUS
    # run, before the page selectbox widget below gets instantiated this run --
    # mutating a page_key-keyed value after that widget is instantiated raises
    # StreamlitWidgetAlreadyInstantiatedError (same class of bug as the earlier
    # Prev/Next button fix; this is the equivalent "set before instantiation"
    # pattern for a non-button trigger).
    pending_sync = st.session_state.pop("_pending_page_sync", None)
    if pending_sync:
        sync_record_id, sync_page = pending_sync
        st.session_state[f"page_{sync_record_id}"] = sync_page

    # Show a one-time toast for the last action, then clear it
    if st.session_state.get("flash"):
        st.toast(st.session_state.flash)
        st.session_state.flash = None

    # Load records
    records = load_records_cached()
    if not records:
        st.error("No records loaded!")
        return

    # Build case index
    all_cases = build_case_index(records)

    # Sidebar: filters and navigation
    st.sidebar.title("📋 Review Navigation")
    st.sidebar.caption(f"📂 Source: **{DATA_SOURCE_LABEL}**")

    # Reviewer email
    st.session_state.reviewer_email = st.sidebar.text_input(
        "Your email",
        st.session_state.reviewer_email,
    )

    # Search input
    search_query = st.sidebar.text_input(
        "🔍 Search cases", placeholder="Case # or client name"
    )

    # Filters
    st.sidebar.subheader("Filters")
    st.session_state.filter_unreviewed = st.sidebar.checkbox(
        "Unreviewed only", st.session_state.filter_unreviewed
    )
    st.session_state.filter_has_flags = st.sidebar.checkbox(
        "Has review flags", st.session_state.filter_has_flags
    )
    st.session_state.filter_errors = st.sidebar.checkbox(
        "Errors only", st.session_state.filter_errors
    )
    st.session_state.filter_warnings = st.sidebar.checkbox(
        "Warnings only", st.session_state.filter_warnings
    )

    # Apply filters
    filtered_cases = filter_cases(
        all_cases,
        unreviewed_only=st.session_state.filter_unreviewed,
        has_flags_only=st.session_state.filter_has_flags,
        errors_only=st.session_state.filter_errors,
        warnings_only=st.session_state.filter_warnings,
    )

    # Apply search
    filtered_cases = search_cases(filtered_cases, search_query)

    # Progress
    n_complete = sum(1 for c in all_cases if c["is_complete"])
    st.sidebar.progress(n_complete / len(all_cases))
    st.sidebar.text(f"{n_complete}/{len(all_cases)} reviewed")

    # Case list
    st.sidebar.subheader("Cases")
    if not filtered_cases:
        st.sidebar.warning("No cases match filters")
        return

    def _severity_dot(sev):
        return {"error": "🔴", "warning": "🟡"}.get(sev, "⚪")

    case_labels = [
        f"{_severity_dot(c['max_flag_severity'])} {c['case_number']} — {c['client_name']} "
        f"({c['n_decided']}/{c['n_total_decidable']})"
        for c in filtered_cases
    ]

    # Clamp current index
    if st.session_state.current_record_index >= len(filtered_cases):
        st.session_state.current_record_index = 0

    selected_case_idx = st.sidebar.radio(
        "Select case:",
        range(len(filtered_cases)),
        format_func=lambda i: case_labels[i],
        index=st.session_state.current_record_index,
    )

    st.session_state.current_record_index = selected_case_idx
    current_case = filtered_cases[selected_case_idx]
    record_id = current_case["record_id"]
    record = records[record_id]

    # Main panel
    st.title(f"Case {current_case['case_number']}")

    # Check if filters are active
    is_filtered = bool(search_query.strip()) or any([
        st.session_state.filter_unreviewed,
        st.session_state.filter_has_flags,
        st.session_state.filter_errors,
        st.session_state.filter_warnings
    ])

    # Position caption
    caption_text = f"Case {selected_case_idx + 1} of {len(filtered_cases)}"
    if is_filtered:
        caption_text += " (filtered)"
    st.caption(caption_text)

    # Case summary
    document = record.get("document", {})
    client = document.get("client") or {}
    if isinstance(client, dict):
        name_obj = client.get("name", {})
        if isinstance(name_obj, dict):
            client_name = name_obj.get("value", "Unknown")
        else:
            client_name = "Unknown"
    else:
        client_name = "Unknown"

    summary_col1, summary_col2, summary_col3 = st.columns(3)
    with summary_col1:
        st.metric("Client", client_name)
    with summary_col2:
        st.metric("Type", document.get("doc_type", "Unknown"))
    with summary_col3:
        st.metric("Pages", record.get("source", {}).get("page_count", "?"))

    # Previous/Next navigation
    prev_col, next_col = st.columns(2)
    with prev_col:
        has_prev = selected_case_idx > 0
        prev_label = f"◀ {filtered_cases[selected_case_idx - 1]['case_number']}" if has_prev else "◀ Previous"
        if st.button(prev_label, use_container_width=True, disabled=not has_prev):
            st.session_state.current_record_index = max(0, st.session_state.current_record_index - 1)
            st.rerun()
    with next_col:
        has_next = selected_case_idx < len(filtered_cases) - 1
        next_label = f"{filtered_cases[selected_case_idx + 1]['case_number']} ▶" if has_next else "Next ▶"
        if st.button(next_label, use_container_width=True, disabled=not has_next):
            st.session_state.current_record_index = min(
                len(filtered_cases) - 1, st.session_state.current_record_index + 1
            )
            st.rerun()

    # Export button (right-aligned)
    _, _, export_col = st.columns([6, 1, 1])
    with export_col:
        if st.button("Export", use_container_width=True):
            st.session_state.show_export = True

    # Review flags and totals
    if record.get("review_flags"):
        with st.expander("⚠️ Review Flags"):
            for flag in record["review_flags"]:
                severity = flag.get("severity", "info")
                icon = {"error": "🔴", "warning": "🟡", "info": "ℹ️"}.get(severity, "")
                st.write(f"{icon} {flag.get('message', '')}")

    if record.get("totals"):
        with st.expander("📊 Totals"):
            for total in record["totals"]:
                reconciles = total.get("reconciles", False)
                icon = "✅" if reconciles else "❌"
                st.write(
                    f"{icon} {total.get('label', '')}: "
                    f"Extracted {total.get('extracted_sum', '')} vs "
                    f"Document {total.get('document_value', '')}"
                )

    # Two-column layout: document viewer (left) + field table (right)
    doc_col, field_col = st.columns([1, 1])

    with doc_col:
        st.subheader("📄 Document")
        page_count = record.get("source", {}).get("page_count", 1)
        page_key = f"page_{record_id}"
        if page_key not in st.session_state:
            st.session_state[page_key] = 1

        def _go_prev_page(pk=page_key):
            st.session_state[pk] = max(1, st.session_state[pk] - 1)

        def _go_next_page(pk=page_key, pc=page_count):
            st.session_state[pk] = min(pc, st.session_state[pk] + 1)

        prev_page_col, page_select_col, next_page_col = st.columns([1, 2, 1])
        with prev_page_col:
            st.button(
                "◀ Prev page",
                key=f"prev_page_{record_id}",
                use_container_width=True,
                disabled=(st.session_state[page_key] <= 1),
                on_click=_go_prev_page,
            )
        with page_select_col:
            if page_count > 1:
                st.selectbox(
                    f"Page (1-{page_count})",
                    range(1, page_count + 1),
                    key=page_key,
                    label_visibility="collapsed",
                )
            else:
                st.markdown(f"<p style='text-align: center; margin-top: 0.5rem;'>Page 1 of 1</p>", unsafe_allow_html=True)
        with next_page_col:
            st.button(
                "Next page ▶",
                key=f"next_page_{record_id}",
                use_container_width=True,
                disabled=(st.session_state[page_key] >= page_count),
                on_click=_go_next_page,
            )

        current_page = st.session_state[page_key]
        rotation_key = record_id
        current_rotation = st.session_state.page_rotations.get(rotation_key, 0)

        rot_col1, rot_col2, rot_col3 = st.columns([1, 1, 1])
        with rot_col1:
            if st.button("↺ Rotate Left", key=f"rotate_left_{rotation_key}", use_container_width=True):
                st.session_state.page_rotations[rotation_key] = (current_rotation - 90) % 360
                st.rerun()
        with rot_col2:
            if st.button("↻ Rotate Right", key=f"rotate_right_{rotation_key}", use_container_width=True):
                st.session_state.page_rotations[rotation_key] = (current_rotation + 90) % 360
                st.rerun()
        with rot_col3:
            if current_rotation != 0:
                if st.button("Reset rotation", key=f"reset_rotation_{rotation_key}", use_container_width=True):
                    st.session_state.page_rotations[rotation_key] = 0
                    st.rerun()
        with st.spinner("Loading page..."):
            render_page_image(record, current_page, rotation=current_rotation)
        render_page_text_expander(record, current_page)

    with field_col:
        # Independently scrollable: the document viewer in doc_col stays in place
        # while this list scrolls, instead of the whole page scrolling together.
        # key is tied to record_id so switching cases creates a fresh container
        # (resetting scroll to the top) instead of reusing the same scrolled div.
        with st.container(height=800, key=f"field_review_scroll_{record_id}"):
            st.subheader("🔍 Field Review")
            st.caption(
                "✅ **Approve** = correct as extracted &nbsp;·&nbsp; "
                "❌ **Reject** = wrong, exclude it &nbsp;·&nbsp; "
                "✏️ **Edit value** = fix the amount or quantity"
            )

            # Get human_review decisions
            human_review = record.get("human_review", {})
            field_decisions = human_review.get("field_decisions", [])
            decisions_by_item_id = {d["item_id"]: d for d in field_decisions}

            # Group by employee
            employee_groups = group_pay_items_by_employee(record)

            if not record.get("pay_items"):
                st.info("No pay items in this record.")
            else:
                # Per-employee grouping
                for emp_name, items in employee_groups:
                    # Check if group is fully decided
                    item_ids = {item.get("item_id") for item in items}
                    group_fully_decided = item_ids.issubset(set(decisions_by_item_id.keys()))

                    # Auto-expand if undecided items
                    default_expanded = not group_fully_decided

                    with st.expander(f"👤 {emp_name}", expanded=default_expanded):
                        # Bulk accept for group
                        bulk_col1, bulk_col2, bulk_col3 = st.columns(3)

                        with bulk_col1:
                            if st.button(
                                f"✅ Accept all ({len(items)})",
                                key=f"bulk_accept_group_{emp_name}",
                            ):
                                new_decisions = bulk_accept(
                                    items,
                                    decisions_by_item_id,
                                    lambda x: True,  # Accept all in group, regardless of confidence
                                )
                                if new_decisions:
                                    for d in new_decisions:
                                        d["decided_by"] = st.session_state.reviewer_email
                                    save_decision_batch(
                                        record_id,
                                        new_decisions,
                                        RECORDS_DIR,
                                        JSONL_PATH,
                                        st.session_state.reviewer_email,
                                    )
                                    load_records_cached.clear()
                                st.rerun()

                        for item in items:
                            item_id = item.get("item_id")
                            decision = decisions_by_item_id.get(item_id)

                            # Invisible anchor so the scroll-sync component can tell
                            # which page's fields are currently scrolled into view.
                            item_page = (item.get("provenance") or {}).get("page")
                            if item_page is not None:
                                st.markdown(
                                    f'<div data-page="{item_page}" style="height:0;margin:0;padding:0;"></div>',
                                    unsafe_allow_html=True,
                                )

                            # Add color highlight for decided items
                            if decision:
                                decision_type = decision.get("decision")
                                if decision_type == "accept":
                                    st.success(f"✅ {item_id}")
                                elif decision_type == "reject":
                                    st.error(f"❌ {item_id}")
                                else:
                                    st.warning(f"✏️ {item_id}")

                            render_field_row(item, item_id, decision, emp_name, record_id, records)

                # Bulk accept for entire case
                st.divider()
                bulk_case_col1, bulk_case_col2 = st.columns(2)

                with bulk_case_col1:
                    confirm_key_hc = f"confirm_bulk_{record_id}_high_conf"
                    all_items = record.get("pay_items", [])
                    pending_hc = bulk_accept(
                        all_items,
                        decisions_by_item_id,
                        lambda x: x.get("confidence") == "high",
                    )
                    if st.session_state.get(confirm_key_hc):
                        st.warning(f"Approve {len(pending_hc)} high-confidence items? This cannot be bulk-undone.")
                        confirm_col, cancel_col = st.columns(2)
                        with confirm_col:
                            if st.button("✅ Confirm", key=f"{confirm_key_hc}_yes", use_container_width=True):
                                if pending_hc:
                                    for d in pending_hc:
                                        d["decided_by"] = st.session_state.reviewer_email
                                    save_decision_batch(
                                        record_id,
                                        pending_hc,
                                        RECORDS_DIR,
                                        JSONL_PATH,
                                        st.session_state.reviewer_email,
                                    )
                                    load_records_cached.clear()
                                st.session_state[confirm_key_hc] = False
                                st.rerun()
                        with cancel_col:
                            if st.button("Cancel", key=f"{confirm_key_hc}_no", use_container_width=True):
                                st.session_state[confirm_key_hc] = False
                                st.rerun()
                    else:
                        if st.button(f"✅ Accept all high-confidence in this case ({len(pending_hc)})", use_container_width=True):
                            st.session_state[confirm_key_hc] = True
                            st.rerun()

                with bulk_case_col2:
                    confirm_key_all = f"confirm_bulk_{record_id}_all_remaining"
                    all_items = record.get("pay_items", [])
                    pending_all = bulk_accept(
                        all_items,
                        decisions_by_item_id,
                        lambda x: True,
                    )
                    if st.session_state.get(confirm_key_all):
                        st.warning(f"Approve {len(pending_all)} remaining items? This cannot be bulk-undone.")
                        confirm_col, cancel_col = st.columns(2)
                        with confirm_col:
                            if st.button("✅ Confirm", key=f"{confirm_key_all}_yes", use_container_width=True):
                                if pending_all:
                                    for d in pending_all:
                                        d["decided_by"] = st.session_state.reviewer_email
                                    save_decision_batch(
                                        record_id,
                                        pending_all,
                                        RECORDS_DIR,
                                        JSONL_PATH,
                                        st.session_state.reviewer_email,
                                    )
                                    load_records_cached.clear()
                                st.session_state[confirm_key_all] = False
                                st.rerun()
                        with cancel_col:
                            if st.button("Cancel", key=f"{confirm_key_all}_no", use_container_width=True):
                                st.session_state[confirm_key_all] = False
                                st.rerun()
                    else:
                        if st.button(f"✅ Accept all remaining unreviewed in this case ({len(pending_all)})", use_container_width=True):
                            st.session_state[confirm_key_all] = True
                            st.rerun()

        # Scroll-sync: switch the document viewer's page automatically as the
        # reviewer scrolls past a page's fields in the list above. Invisible;
        # reports back whichever page's marker is currently at the top of the
        # visible scroll area (see components/scroll_sync/index.html).
        detected_page = _scroll_sync(
            container_key=f"field_review_scroll_{record_id}",
            key=f"scroll_sync_{record_id}",
            default=None,
        )
        if (
            detected_page is not None
            and detected_page != st.session_state.get(page_key)
            and 1 <= detected_page <= page_count
        ):
            # Can't set page_key directly here -- its widget was already
            # instantiated earlier this run (in doc_col). Defer to the start
            # of the next run instead (see the _pending_page_sync handling
            # at the top of main()).
            st.session_state["_pending_page_sync"] = (record_id, detected_page)
            st.rerun()

    # Export modal
    if hasattr(st.session_state, "show_export") and st.session_state.show_export:
        st.divider()
        st.subheader("📥 Export Review Decisions")

        export_col1, export_col2 = st.columns(2)

        with export_col1:
            exports_dir = BASE_DIR / "review" / "exports"
            os.makedirs(exports_dir, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            export_filename = f"review_decisions_{timestamp}.xlsx"
            export_path = str(exports_dir / export_filename)
            export_review_decisions(RECORDS_DIR, export_path)

            with open(export_path, "rb") as f:
                st.download_button(
                    label="Download Excel",
                    data=f.read(),
                    file_name=export_filename,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )

        with export_col2:
            if st.button("Clear"):
                st.session_state.show_export = False
                st.rerun()


if __name__ == "__main__":
    main()
