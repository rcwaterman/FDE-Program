"""Streamlit view for the CSV review engine.

One case at a time. The source document sits on the left and the import rows on the right, the same split as
the JSON review screen. Tick Approve to accept a row, Exclude to drop it, or change a value to edit it (which
also approves).

Called from review/app.py when select_engine() picks "csv". This module does not call st.set_page_config.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import pandas as pd
import streamlit as st

from src.feature_extraction.review.doc_render import get_page_image
from src.feature_extraction.review.csv_engine import csv_export, csv_model, csv_store
from src.feature_extraction.review.csv_engine.flag_text import explain

log = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[4]
IMPORT_MAP_PATH = _REPO_ROOT / "src" / "pipeline" / "schemas" / "import_column_map.json"
FILTERS = ("All", "Needs review", "Complete")
LAYOUTS = ("Document | Table", "Wider table", "Table only")
# Share of the width given to (document, table). Table only has no document pane.
SPLIT = {"Document | Table": [1, 1], "Wider table": [2, 3]}
PANE_HEIGHT = 560  # fits a 900px-tall window below the header and actions; both panes scroll inside it
# Short headers for the table. The import CSV keeps the template's own column names.
VALUE_LABELS = {"Pay Rate(Optional)": "Rate", "Reg Hours": "Reg hrs", "OT Hours": "OT hrs",
                "Vacation Hours": "Vacation", "Sick Hours": "Sick", "Holiday Hours": "Holiday",
                "PTO Hours": "PTO", "Other Hours": "Other hrs", "Cash Tips Amount": "Cash tips",
                "Credit Tips Amount": "Credit tips", "Bonus Amount": "Bonus", "Commission Amount": "Commission",
                "Other Amount": "Other $", "Department (optional)": "Dept", "Deduction Code (Optional)": "Ded code",
                "Deduction Amount (Optional)": "Ded $", "Total Pay": "Total pay",
                "Source Code/Label": "Label on form", "Employee Action": "Action"}


def _value_width(col: str, compact: bool) -> int:
    if compact:
        return 84
    return 130 if col in ("Total Pay", "Source Code/Label") else 96


def _load_cmap() -> dict:
    from src.pipeline.exporters import import_template as it
    return it.load_map(IMPORT_MAP_PATH)


def _init_state() -> None:
    st.session_state.setdefault("csv_reviewer", os.getenv("REVIEWER_EMAIL", "reviewer@example.com"))
    st.session_state.setdefault("csv_selected", None)
    st.session_state.setdefault("csv_rotation", {})
    st.session_state.setdefault("csv_flash", None)
    st.session_state.setdefault("csv_filter", "Needs review")
    st.session_state.setdefault("csv_layout", LAYOUTS[0])


def _set_selected(record_id: str) -> None:
    st.session_state["csv_selected"] = record_id
    st.session_state["csv_case_radio"] = record_id


def _on_case_change() -> None:
    st.session_state["csv_selected"] = st.session_state["csv_case_radio"]


def _flash(message: str) -> None:
    st.session_state["csv_flash"] = message


def _bump(record_id: str) -> None:
    """Redraw the table from saved decisions: a new editor key discards the widget's pending edits."""
    st.session_state[f"csv_ver_{record_id}"] = st.session_state.get(f"csv_ver_{record_id}", 0) + 1


def _on_table_change(run_root: Path, record_id: str, editor_key: str, table_rows: list[dict], meta_rows: list[dict]) -> None:
    changes = st.session_state[editor_key].get("edited_rows", {})
    ops = csv_model.apply_editor_changes(changes, table_rows, meta_rows)
    blocked = [o for o in ops if o[0] == "blocked"]
    saved = [o for o in ops if o[0] != "blocked"]
    if saved:
        csv_store.save_row_decisions_batch(run_root, record_id, saved, st.session_state["csv_reviewer"])
        _bump(record_id)
        _flash(f"Saved {len(saved)} change(s)")
    if blocked:
        _bump(record_id)  # discard the blocked edit from the table
        _flash(f"{blocked[0][1]}: {blocked[0][2]['reason']}")


def _approve_unflagged(run_root: Path, record_id: str, meta: dict, review: dict) -> None:
    ops = [("accept", m["row_id"], {"fingerprint": m["fingerprint"]}) for m in meta["rows"]
           if not m["flags"] and csv_model.row_status(review, m) == "pending"]
    if ops:
        csv_store.save_row_decisions_batch(run_root, record_id, ops, st.session_state["csv_reviewer"])
        _bump(record_id)
        _flash(f"Approved {len(ops)} unflagged row(s)")


def _set_page(key: str, page: int) -> None:
    st.session_state[key] = page


def _step_focus(record_id: str, rows: list[dict], direction: int, pending_only: bool) -> None:
    key = f"csv_focus_{record_id}"
    st.session_state[key] = csv_model.step_row(rows, st.session_state.get(key), direction, pending_only)


def _page_text(run_root: Path, record_id: str, page: int) -> tuple[str, str] | None:
    """Printed and handwritten text for a page, from the pipeline record. None when the record is not there."""
    path = run_root / "records" / record_id / "record.json"
    if not path.exists():
        return None
    try:
        pages = json.loads(path.read_text(encoding="utf-8")).get("pages") or []
    except (OSError, ValueError):
        return None
    if page - 1 >= len(pages):
        return None
    p = pages[page - 1]
    return p.get("printed_text") or "", p.get("handwritten_text") or ""


def _render_document(run_root: Path, record_id: str, meta: dict, attachments_dir: str, cache_dir: str,
                     focus_row: dict | None) -> None:
    """The source document, laid out like the JSON engine: page controls, rotation, image, page text."""
    total = meta.get("document", {}).get("page_count") or 1
    key = f"csv_page_{record_id}"
    page = max(1, min(st.session_state.get(key, 1), total))
    st.session_state[key] = page
    rot = st.session_state["csv_rotation"].get(record_id, 0)

    prev_col, sel_col, next_col = st.columns([1, 2, 1])
    prev_col.button("◀", key=f"csv_prev_{record_id}", use_container_width=True, disabled=page <= 1,
                    help="Previous page", on_click=_set_page, args=(key, page - 1))
    if total > 1:
        sel_col.selectbox(f"Page (1-{total})", list(range(1, total + 1)), key=key, label_visibility="collapsed")
    else:
        sel_col.markdown("<p style='text-align:center;margin-top:0.5rem;'>Page 1 of 1</p>", unsafe_allow_html=True)
    next_col.button("▶", key=f"csv_next_{record_id}", use_container_width=True, disabled=page >= total,
                    help="Next page", on_click=_set_page, args=(key, page + 1))
    page = st.session_state[key]

    r1, r2, r3 = st.columns(3)
    r1.button("↺", key=f"csv_rl_{record_id}", use_container_width=True, help="Rotate left",
              on_click=lambda: st.session_state["csv_rotation"].update({record_id: (rot - 90) % 360}))
    r2.button("↻", key=f"csv_rr_{record_id}", use_container_width=True, help="Rotate right",
              on_click=lambda: st.session_state["csv_rotation"].update({record_id: (rot + 90) % 360}))
    if rot and r3.button("⟲", key=f"csv_rreset_{record_id}", use_container_width=True, help="Reset rotation"):
        st.session_state["csv_rotation"][record_id] = 0
        st.rerun()

    if focus_row is not None and focus_row.get("Page"):
        where = int(focus_row["Page"])
        st.caption(f"Page {page} of {total} · {focus_row['_row_id']} is on page {where}"
                   + (" (this page)" if where == page else ""))
    else:
        st.caption(f"Page {page} of {total}")
    try:
        img = get_page_image(record_id, page, attachments_dir, cache_dir, rotation=rot)
        st.image(img, use_container_width=True)
    except Exception as e:  # missing or unreadable attachment: say why, keep the table usable
        log.exception("document render failed record=%s page=%s", record_id, page)
        st.error(f"Could not load the document: {e}")

    text = _page_text(run_root, record_id, page)
    if text is not None:
        printed, handwritten = text
        with st.expander("Page text"):
            st.markdown("**Printed**")
            st.text(printed or "(none)")
            st.markdown("**Handwritten**")
            st.text(handwritten or "(none)")


def _render_items_to_place(run_root: Path, record_id: str, rec: dict, review: dict, compact: bool) -> None:
    items = csv_model.unresolved_items(rec["meta"], review)
    if not items:
        return
    value_cols = [c for c in rec["columns"] if "Hours" in c or "Amount" in c]
    # Split layouts start collapsed so the table keeps its room; the title always shows how many lines wait.
    expanded = (not compact) and len(items) <= 3
    with st.expander(f"⚠ {len(items)} pay line(s) need a column, click to review", expanded=expanded):
        st.caption("Each line has a value but no clear pay code or label. Pick a column, then Assign it, or Ignore it.")
        for item in items:
            iid = item["item_id"]
            label = item.get("legend") or item.get("label") or "no label on the form"
            q = item.get("quantity") or {}
            shown = f"${item['amount']:,.2f}" if item.get("amount") is not None else f"{q.get('value')} {q.get('unit') or ''}".strip()
            st.markdown(f"**{iid}** · {label} · **{shown}**")
            # Stacked, so the controls keep their full width in the half-width review pane.
            col = st.selectbox("Column", [None, *value_cols], format_func=lambda c: "Choose a column…" if c is None else c,
                               key=f"um_col_{record_id}_{iid}", label_visibility="collapsed")
            val = float(item.get("amount") if item.get("amount") is not None else (q.get("value") or 0))
            b1, b2 = st.columns(2)
            b1.button("Assign", key=f"um_assign_{record_id}_{iid}", type="primary", disabled=col is None,
                      use_container_width=True,
                      on_click=_resolve_item, args=(run_root, record_id, iid, "assign", col, val))
            b2.button("Ignore", key=f"um_ignore_{record_id}_{iid}", use_container_width=True,
                      on_click=_resolve_item, args=(run_root, record_id, iid, "ignore", None, None))


def _resolve_item(run_root: Path, record_id: str, item_id: str, action: str, column, value) -> None:
    csv_store.save_unmapped_resolution(run_root, record_id, item_id, action,
                                       st.session_state["csv_reviewer"], column=column, value=value)
    _bump(record_id)
    _flash(f"{item_id}: {'assigned to ' + column if action == 'assign' else 'ignored'}")


def _render_table(run_root: Path, record_id: str, rec: dict, review: dict, cmap: dict, table: dict, compact: bool) -> None:
    """The approval table. Compact (split layouts): icons, flag count, no page column. Full (table only): text."""
    rows = table["rows"]
    version = st.session_state.get(f"csv_ver_{record_id}", 0)
    layout_key = "c" if compact else "f"
    editor_key = f"csv_table_{record_id}_{version}_{layout_key}"
    if compact:
        order = ["Approve", "Exclude", "Employee", "Flagged", *table["columns"]]
    else:
        order = ["Approve", "Exclude", "Employee", "Status", *table["columns"], "Flags", "Page"]
    frame = pd.DataFrame([{k: r[k] for k in order} for r in rows])
    if "Page" in order:
        frame["Page"] = frame["Page"].map(lambda p: f"p{int(p)}" if pd.notna(p) else "")
    column_config = {
        "Approve": st.column_config.CheckboxColumn("✓", help="Approve this row", width=44 if compact else 104,
                                                   default=False, pinned=True),
        "Exclude": st.column_config.CheckboxColumn("✗", help="Leave this row out of the import",
                                                   width=44 if compact else 104, default=False),
        "Employee": st.column_config.TextColumn("Employee", disabled=True, width=176 if compact else 230, pinned=True),
        "Flagged": st.column_config.TextColumn("⚠", disabled=True, width=40, help="Number of flags on this row"),
    }
    if not compact:
        column_config.update({
            "Status": st.column_config.TextColumn("Status", disabled=True, width=130),
            "Flags": st.column_config.TextColumn("Flags", disabled=True, width=300),
            "Page": st.column_config.TextColumn("Page", disabled=True, width=60),
        })
    for col in table["columns"]:
        column_config[col] = st.column_config.TextColumn(VALUE_LABELS.get(col, col),
                                                         width=_value_width(col, compact), alignment="right")
    st.data_editor(
        frame,
        key=editor_key,
        hide_index=True,
        width="stretch",
        column_order=order,
        column_config=column_config,
        disabled=[],
        on_change=_on_table_change,
        args=(run_root, record_id, editor_key, rows, rec["meta"]["rows"]),
        height=min(520, 56 + 36 * len(rows)),
    )


def _ensure_focus(record_id: str, rows: list[dict]) -> str | None:
    """The inspected row. Defaults to the first row needing a decision, and is set before the table is drawn so
    the table marks it on the first render."""
    if not rows:
        return None
    key = f"csv_focus_{record_id}"
    ids = [r["_row_id"] for r in rows]
    if st.session_state.get(key) not in ids:
        pending = [r["_row_id"] for r in rows if r["_status"] in ("pending", "stale")]
        st.session_state[key] = pending[0] if pending else ids[0]
    return st.session_state[key]


def _row_nav(record_id: str, rows: list[dict], compact: bool) -> None:
    """Prev row, row picker and Next row. Sits above the table so stepping never needs a scroll."""
    options = {r["_row_id"]: f"{r['_row_id']} · {r['Employee'].lstrip('▶ ').split(' ', 1)[-1]}" for r in rows}
    key = f"csv_focus_{record_id}"
    p1, p2, p3 = st.columns([1, 5, 1] if compact else [1, 4, 1])
    p1.button("◀" if compact else "◀ Previous row", key=f"csv_rprev_{record_id}", use_container_width=True,
              help="Previous row", on_click=_step_focus, args=(record_id, rows, -1, False))
    p2.selectbox("Row to inspect", list(options), format_func=lambda r: options[r], key=key)
    p3.button("▶" if compact else "Next row ▶", key=f"csv_rnext_{record_id}", use_container_width=True,
              help="Next row", on_click=_step_focus, args=(record_id, rows, 1, False))
    pending = [r for r in rows if r["_status"] in ("pending", "stale")]
    nxt = csv_model.step_row(rows, st.session_state[key], 1, pending_only=True)
    if pending:
        st.caption(f"{len(pending)} row(s) still need a decision · next one: {nxt}")


def _row_details(record_id: str, rec: dict, review: dict, rows: list[dict]) -> dict | None:
    """Flags in plain language and the source items for the inspected row. Shown below the table."""
    chosen = st.session_state.get(f"csv_focus_{record_id}")
    row = next((r for r in rows if r["_row_id"] == chosen), None)
    if row is None:
        return None
    meta_row = next(m for m in rec["meta"]["rows"] if m["row_id"] == chosen)
    st.markdown(f"**{chosen}** · {row['Employee']}")
    flags = csv_model.visible_flags(meta_row, review)
    if not flags:
        st.caption("No flags on this row.")
    for token in flags:
        severity, text = explain(token)
        {"error": st.error, "warn": st.warning, "info": st.info}[severity](text)
    st.markdown("**Source items**")
    if not meta_row["items"]:
        st.caption("No pay lines feed this row (it is here for the employee's action or a deduction).")
    for it_ in meta_row["items"]:
        raw = it_.get("raw_text") or "—"
        st.write(f"{it_['item_id']} → **{it_['column']}** = {it_['value']} · written as `{raw}` · "
                 f"{it_.get('confidence')} confidence · page {it_.get('page')}")
        if it_.get("kind"):
            st.caption(f"Model's reading: {it_['kind']} (basis: {it_.get('kind_basis') or 'unknown'}). "
                       f"Evidence: {it_.get('evidence') or 'none given'}")
    return row


def _sync_document_to_row(record_id: str, focus_row: dict | None) -> None:
    """When the inspected row changes, the document moves to its page. Manual paging is left alone otherwise."""
    if focus_row is None or not focus_row.get("Page"):
        return
    seen_key = f"csv_seen_{record_id}"
    if st.session_state.get(seen_key) != focus_row["_row_id"]:
        st.session_state[f"csv_page_{record_id}"] = int(focus_row["Page"])
        st.session_state[seen_key] = focus_row["_row_id"]


def _render_status(run_root: Path, record_id: str, cmap: dict, summary: dict) -> None:
    """Completion state for the case, shown at the top next to the progress: the download appears here once
    every row is decided, because that is the reviewer's next step."""
    if summary["complete"]:
        c1, c2 = st.columns([3, 2])
        c1.success("Every row is decided. This case is ready to import.")
        c2.download_button("⬇ Download reviewed CSV for this case", data=csv_export.reviewed_csv_bytes(run_root, record_id, cmap),
                           file_name=f"{record_id}_reviewed.csv", mime="text/csv", key=f"dl_{record_id}",
                           use_container_width=True)
    # Incomplete: the progress line below says what is left, so no banner takes up space here.


def _review_pane(run_root: Path, record_id: str, rec: dict, review: dict, cmap: dict, compact: bool) -> dict | None:
    """Everything on the review side: items to place, row navigation, the table, and the row details."""
    _render_items_to_place(run_root, record_id, rec, review, compact)
    focus = _ensure_focus(record_id, csv_model.build_table(rec, review, cmap)["rows"])
    table = csv_model.build_table(rec, review, cmap, focus_id=focus)
    if table["rows"]:
        _row_nav(record_id, table["rows"], compact)
    _render_table(run_root, record_id, rec, review, cmap, table, compact)
    return _row_details(record_id, rec, review, table["rows"])


def run_csv_review(run_root: Path, attachments_dir: str, cache_dir: str, source_label: str) -> None:
    _init_state()
    entries = csv_store.load_manifest(run_root)
    if not entries:
        built = list((run_root / "records").glob("*/import_template.csv")) if (run_root / "records").exists() else []
        marker = run_root / "output_format.json"
        csv_run = marker.exists() and json.loads(marker.read_text(encoding="utf-8")).get("output_format") == "csv"
        log.info("csv review: no manifest yet, run=%s built=%d csv_run=%s", run_root.name, len(built), csv_run)
        if built or csv_run:
            st.warning(f"This run has not been exported yet: {len(built)} case(s) are finished, but import_csv/ is written "
                       f"when the run ends. Wait for the pipeline to finish, or run `export --run-id {run_root.name}`.")
        else:
            st.error("This run has no import CSVs. Re-run with `--output-format csv`, then review it here.")
        return

    cmap = _load_cmap()
    loaded = []
    for e in entries:
        try:
            rec = csv_store.load_record(run_root, e["record_id"])
        except (OSError, KeyError, ValueError) as err:
            st.sidebar.warning(f"Skipped {e['record_id']}: {err}")
            continue
        loaded.append((e, rec, csv_store.load_review(run_root, e["record_id"])))
    index = [csv_model.case_summary(e, rec["meta"], review) for e, rec, review in loaded]
    by_id = {e["record_id"]: (rec, review) for e, rec, review in loaded}

    with st.sidebar:
        st.title("📋 Review")
        st.caption(f"{source_label}")
        st.text_input("Your email", key="csv_reviewer")
        query = st.text_input("Find a case", placeholder="Case # or client", key="csv_search")
        st.radio("Show", FILTERS, horizontal=True, key="csv_filter")
        pool = {"All": index,
                "Needs review": [c for c in index if not c["complete"]],
                "Complete": [c for c in index if c["complete"]]}[st.session_state["csv_filter"]]
        shown = csv_model.search_cases(pool, query)
        # Keep the case on screen after it leaves the filter (for example, it was just completed), so the
        # reviewer sees the finished state and its download instead of being moved to another case.
        current = st.session_state.get("csv_selected")
        if not query and current and current not in [c["record_id"] for c in shown]:
            shown = [c for c in index if c["record_id"] == current] + shown
        done = sum(c["complete"] for c in index)
        st.progress(done / len(index) if index else 0.0, text=f"{done} of {len(index)} cases complete")
        if shown:
            ids = [c["record_id"] for c in shown]
            if st.session_state["csv_selected"] not in ids:
                _set_selected(ids[0])
            # Labels must not change when a row is decided: the radio's returned value follows the label, so a
            # changing label made the selected case jump. Progress lives in the header and the per-case status.
            labels = {c["record_id"]: f"{'✅' if c['complete'] else '•'} {c['case_number']} · {c['client'] or 'unknown client'}"
                      for c in shown}
            # The radio has its own key; csv_selected holds the ID and is kept in step with it.
            st.radio("Cases", ids, format_func=lambda r: labels[r], key="csv_case_radio",
                     index=ids.index(st.session_state["csv_selected"]), on_change=_on_case_change)

    if st.session_state["csv_flash"]:
        st.toast(st.session_state["csv_flash"])
        st.session_state["csv_flash"] = None

    if not shown:
        st.info("No case matches the search or filter. Clear the search, or choose All.")
        return

    selected = st.session_state["csv_selected"]
    rec, review = by_id[selected]
    meta = rec["meta"]
    summary = next(c for c in index if c["record_id"] == selected)
    doc = meta.get("document", {})
    pp = doc.get("pay_period", {})
    client = doc.get("client", {})

    # Compact header: one title line, one detail line, progress.
    st.markdown(f"#### Case {summary['case_number']} · {client.get('name') or 'unknown client'}"
                f"{' (' + client['number'] + ')' if client.get('number') else ''}")
    st.caption(f"Pay period {pp.get('Pay Period Start') or '?'} – {pp.get('Pay Period End') or '?'} · "
               f"check {pp.get('Check Date') or '?'} · frequency {pp.get('Pay Frequency') or '?'}"
               + (f" · {summary['skipped_empty']} empty line(s) skipped" if summary["skipped_empty"] else ""))
    left = summary["rows"] - summary["decided"]
    progress_text = f"{summary['approved']} of {summary['rows']} rows approved"
    if left:
        progress_text += f" · {left} still need a decision"
    if summary["unmapped"]:
        progress_text += f" · {summary['unmapped']} line(s) need a column"
    st.progress(summary["approved"] / summary["rows"] if summary["rows"] else 1.0, text=progress_text)

    _render_status(run_root, selected, cmap, summary)

    a1, a2, a3 = st.columns([2, 2, 3])
    unflagged_pending = sum(1 for m in meta["rows"] if not m["flags"] and csv_model.row_status(review, m) == "pending")
    a1.button(f"✓ Approve {unflagged_pending} unflagged row(s)" if unflagged_pending else "✓ No unflagged rows left",
              key=f"csv_bulk_{selected}", disabled=unflagged_pending == 0,
              on_click=_approve_unflagged, args=(run_root, selected, meta, review))
    nxt = csv_model.next_incomplete(index, selected)
    a2.button("Next case to review →", key="csv_next_case", disabled=nxt is None,
              on_click=_set_selected, args=(nxt,) if nxt else (None,))
    a3.radio("Layout", LAYOUTS, horizontal=True, key="csv_layout", label_visibility="collapsed")

    layout = st.session_state["csv_layout"]

    if layout == "Table only":
        with st.container():
            focus_row = _review_pane(run_root, selected, rec, review, cmap, compact=False)
    else:
        doc_col, review_col = st.columns(SPLIT[layout], gap="medium")
        # The review side is rendered first: choosing a row sets the document's page in the same run.
        with review_col.container(height=PANE_HEIGHT, border=True):
            focus_row = _review_pane(run_root, selected, rec, review, cmap, compact=True)
        _sync_document_to_row(selected, focus_row)
        with doc_col.container(height=PANE_HEIGHT, border=True):
            _render_document(run_root, selected, meta, attachments_dir, cache_dir, focus_row)

    st.divider()
    if st.button("Export every complete case to import_csv_reviewed/", key="csv_export_all"):
        result = csv_export.export_reviewed(run_root, cmap)
        st.success(f"Wrote {result['written']} reviewed CSV(s) · {result['rows_dropped']} row(s) excluded · "
                   f"{result['rows_edited']} edited · {result['unmapped_assigned']} line(s) assigned")
        if result["not_complete"]:
            st.info(f"{len(result['not_complete'])} case(s) are not complete and were not written.")
        if result["unmapped_not_merged"]:
            st.warning("Assigned but not merged (no earnings row for that employee): "
                       + ", ".join(result["unmapped_not_merged"]))
