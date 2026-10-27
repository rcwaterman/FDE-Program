"""Pure review logic for the CSV engine (no Streamlit).

A row decision is trusted only while its fingerprint matches the row as it is now. If the pipeline re-runs
and the row changes, the old decision shows as "stale" and the case is not complete.

Used by the table view, the per-case download and the run export, so all three agree on what a row is.
"""
import re

from src.pipeline.exporters import import_template as it

REVIEWED_FLAG = "HUMAN_EDITED"
DECIDED = ("accept", "reject", "edit")
# Columns a reviewer may change in the table. Identity and provenance are not editable.
EDITABLE_PREFIXES = ("Hours", "Amount")
EDITABLE_COLUMNS = ("Pay Rate(Optional)", "Department (optional)", "Employee Action",
                    "Deduction Code (Optional)", "Deduction Amount (Optional)")


def _is_editable(col: str) -> bool:
    return "Hours" in col or "Amount" in col or col in EDITABLE_COLUMNS


# ---- decisions ----

def row_status(review: dict, meta_row: dict) -> str:
    d = review["row_decisions"].get(meta_row["row_id"])
    if not d:
        return "pending"
    if d.get("fingerprint") != meta_row["fingerprint"]:
        return "stale"
    return d["decision"]


def is_empty_item(item: dict) -> bool:
    """A printed line with no amount and no hours: nothing was read, so there is nothing to place."""
    return item.get("amount") is None and (item.get("quantity") or {}).get("value") is None


def unresolved_items(meta: dict, review: dict) -> list[dict]:
    """Lines that carry a value but still need an assign or ignore decision."""
    return [u for u in meta.get("unmapped_items", [])
            if not is_empty_item(u) and u["item_id"] not in review["unmapped_resolutions"]]


def skipped_empty_items(meta: dict) -> list[dict]:
    """Lines with no value. They are listed for information and never block completion."""
    return [u for u in meta.get("unmapped_items", []) if is_empty_item(u)]


def is_complete(meta: dict, review: dict) -> bool:
    rows_done = all(row_status(review, r) in DECIDED for r in meta["rows"])
    return rows_done and not unresolved_items(meta, review)


def case_summary(entry: dict, meta: dict, review: dict) -> dict:
    statuses = [row_status(review, r) for r in meta["rows"]]
    return {
        "record_id": entry["record_id"],
        "case_number": entry.get("case_number"),
        "client": (meta.get("document") or {}).get("client", {}).get("name") or "",
        "rows": len(statuses),
        "decided": sum(s in DECIDED for s in statuses),
        "approved": sum(s in ("accept", "edit") for s in statuses),
        "stale": statuses.count("stale"),
        "flagged": sum(1 for r in meta["rows"] if r["flags"]),
        "unmapped": len(unresolved_items(meta, review)),
        "skipped_empty": len(skipped_empty_items(meta)),
        "complete": is_complete(meta, review),
        "review_status": meta.get("review_status"),
    }


def filter_cases(index: list[dict], unreviewed: bool = False, flagged: bool = False, unmapped: bool = False) -> list[dict]:
    out = index
    if unreviewed:
        out = [c for c in out if not c["complete"]]
    if flagged:
        out = [c for c in out if c["flagged"]]
    if unmapped:
        out = [c for c in out if c["unmapped"]]
    return out


def search_cases(index: list[dict], query: str) -> list[dict]:
    q = (query or "").strip().lower()
    if not q:
        return index
    return [c for c in index if q in (c["case_number"] or "").lower() or q in c["client"].lower()]


def next_incomplete(index: list[dict], current_id: str | None) -> str | None:
    """The next case after current_id that is not complete, wrapping around. None when all are complete."""
    open_ids = [c["record_id"] for c in index if not c["complete"]]
    if not open_ids:
        return None
    ids = [c["record_id"] for c in index]
    if current_id in ids:
        start = ids.index(current_id) + 1
        for rid in ids[start:] + ids[:start]:
            if rid in open_ids:
                return rid
    return open_ids[0]


# ---- effective rows (decisions applied) ----

def _numeric(col: str) -> bool:
    return "Hours" in col or "Amount" in col


def raw_cells(row: dict) -> dict:
    out = {}
    for col, v in row.items():
        if _numeric(col) and v not in (None, ""):
            try:
                out[col] = float(v)
            except ValueError:
                pass
    return out


def add_flag(row: dict, token: str) -> None:
    flags = {f for f in (row.get("Review Flags (optional)") or "").split("; ") if f}
    flags.add(token)
    row["Review Flags (optional)"] = "; ".join(sorted(flags))


def recompute_total(row: dict, cmap: dict) -> None:
    rate = row.get("Pay Rate(Optional)") or None
    total, flag = it.compute_total_pay(raw_cells(row), float(rate) if rate else None, cmap)
    row["Total Pay"] = "" if total is None else f"{total:.2f}"
    if flag:
        add_flag(row, flag)


def effective_row(csv_row: dict, meta_row: dict, review: dict, cmap: dict) -> dict:
    """The row as it will be exported: the reviewer's cell edits applied, Total Pay recomputed."""
    row = dict(csv_row)
    if row_status(review, meta_row) == "edit":
        row.update(review["row_decisions"][meta_row["row_id"]]["cell_edits"])
        add_flag(row, REVIEWED_FLAG)
        if meta_row["row_kind"] == "earnings":
            recompute_total(row, cmap)
    return row


# ---- table view ----

STATUS_TEXT = {"accept": "✅ Approved", "edit": "✏️ Edited", "reject": "❌ Excluded",
               "stale": "⚠️ Changed, review again", "pending": "⬜ Pending"}


def visible_columns(columns: list[str], rows: list[dict]) -> list[str]:
    """Only the value columns that have data in this case, in template order."""
    keep = []
    for col in columns:
        if col in ("Client ID", "Client Name", "Payroll Specialist", "Employee Number", "Employee First Name",
                   "Employee Last Name", "Pay Period Start", "Pay Period End", "Check Date", "Pay Frequency",
                   "Review Flags (optional)"):
            continue
        if any(str(r.get(col, "")).strip() not in ("", "None") for r in rows):
            keep.append(col)
    return keep


_UNPLACED_ID = re.compile(r"^UNPLACED (P\d+):")


def visible_flags(meta_row: dict, review: dict) -> list[str]:
    """A row's flags for display. An UNPLACED flag disappears once its line is assigned or ignored, so the
    row stops looking open after the reviewer has dealt with the line."""
    out = []
    for token in meta_row["flags"]:
        m = _UNPLACED_ID.match(token)
        if m and m.group(1) in review["unmapped_resolutions"]:
            continue
        out.append(token)
    return out


STATUS_ICON = {"accept": "✅", "edit": "✏️", "reject": "❌", "stale": "⚠️", "pending": "⬜"}


def build_table(rec: dict, review: dict, cmap: dict, focus_id: str | None = None) -> dict:
    """The display table for one case: visible columns, and one dict per row with its decision state.

    Each row carries everything either layout needs: Status (words), Flags (short text), Flagged (count),
    Employee with a status icon, and Page. The view picks the columns it shows."""
    meta, csv_rows = rec["meta"], rec["rows"]
    effective = [effective_row(c, m, review, cmap) for c, m in zip(csv_rows, meta["rows"])]
    cols = visible_columns(rec["columns"], effective)
    rows = []
    for i, (eff, m) in enumerate(zip(effective, meta["rows"])):
        status = row_status(review, m)
        name = " ".join(filter(None, [eff.get("Employee Last Name"), eff.get("Employee First Name")])) or "(no name)"
        number = f" ({eff['Employee Number']})" if eff.get("Employee Number") else ""
        marker = "▶ " if m["row_id"] == focus_id else ""
        flags = visible_flags(m, review)
        rows.append({
            "_row_id": m["row_id"], "_index": i, "_kind": m["row_kind"], "_status": status,
            "Approve": status in ("accept", "edit"),
            "Exclude": status == "reject",
            "Status": STATUS_TEXT[status],
            "Employee": f"{marker}{STATUS_ICON[status]} {name}{number}"
                        + ("  · deduction" if m["row_kind"] == "deduction" else ""),
            **{c: eff.get(c, "") for c in cols},
            "Flags": "; ".join(short_flag(f) for f in flags),
            "Flagged": len(flags) or "",
            "Page": m["source_pages"][0] if m["source_pages"] else None,
        })
    return {"columns": cols, "rows": rows}


def step_row(rows: list[dict], current_id: str | None, direction: int, pending_only: bool = False) -> str | None:
    """The row before (-1) or after (+1) current_id, wrapping at the ends. With pending_only, only rows still
    needing a decision are considered, so the next step lands on work left to do."""
    all_ids = [r["_row_id"] for r in rows]
    ids = [r["_row_id"] for r in rows if not pending_only or r["_status"] in ("pending", "stale")]
    if not ids:
        return current_id
    if current_id not in all_ids:
        return ids[0]
    pos = all_ids.index(current_id)
    for k in range(1, len(all_ids) + 1):
        cand = all_ids[(pos + direction * k) % len(all_ids)]
        if cand in ids:
            return cand
    return current_id


def short_flag(token: str) -> str:
    from src.feature_extraction.review.csv_engine.flag_text import short
    return short(token)


def apply_editor_changes(changes: dict, table_rows: list[dict], meta_rows: list[dict]) -> list[tuple]:
    """Turn a data_editor edit (index -> {column: new value}) into decision operations.

    table_rows are the rows as displayed. Returns (op, row_id, payload) tuples, op in accept | reject | undo |
    edit. Rules:
      - Approve ticked -> accept; Approve unticked -> undo.
      - Exclude ticked -> reject; Exclude unticked -> undo.
      - A value cell changed -> edit, which also approves the row. On an excluded row it is blocked instead.
    When one batch both ticks Approve and Exclude on the same row, Exclude wins: an excluded row must never
    be exported by accident.
    """
    ops = []
    for idx_key, change in changes.items():
        idx = int(idx_key)
        row = table_rows[idx]
        rid = row["_row_id"]
        meta_row = meta_rows[idx]
        # Compare against what the table shows now, so a second edit to an already-edited cell is detected.
        cell_edits = {c: str(v) for c, v in change.items()
                      if c not in ("Approve", "Exclude") and c in row and str(v) != str(row.get(c, ""))
                      and _is_editable(c)}
        fp = {"fingerprint": meta_row["fingerprint"]}
        if "Exclude" in change:
            ops.append(("reject" if change["Exclude"] else "undo", rid, fp))
        elif cell_edits and row.get("_status") == "reject":
            # Editing must not quietly bring an excluded row back into the import. Untick Exclude first.
            ops.append(("blocked", rid, {"reason": "Untick Exclude before editing this row"}))
        elif cell_edits:
            ops.append(("edit", rid, {**fp, "cell_edits": cell_edits}))
        elif "Approve" in change:
            ops.append(("accept" if change["Approve"] else "undo", rid, fp))
    return ops
