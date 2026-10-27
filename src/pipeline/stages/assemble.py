"""Stage 6: merge per-page readings into one document-level structure (still raw text values).

Deterministic rules:
- employee identity across pages: employee number, else normalized name
- header fields: most frequent value across pages; disagreement -> HEADER_CONFLICT
- legend: union keyed by (code, category)
- totals scope: 'page' when more than one page has totals for that column, 'cover_sheet' on fax cover pages, else 'document'
"""
from __future__ import annotations

from collections import Counter, defaultdict

from ..store import read_json, now
from ..values import name_key, parse_date
from .base import Context, StageSkip

HEADER_FIELDS = ["client_name", "client_number", "form_variant", "period_begin", "period_end", "check_date", "frequency",
                 "period_number", "summary_number", "call_date", "call_time", "delivery", "specialist", "specialist_phone",
                 "payroll_contact", "payroll_contact_phone", "generated_on"]
DATE_FIELDS = {"period_begin", "period_end", "check_date", "call_date"}


def _norm_header(field, value):
    if value is None:
        return None
    s = " ".join(str(value).split())
    if field in DATE_FIELDS:
        iso, _ = parse_date(s)
        return iso or s
    return s.upper() if field in ("client_number", "frequency") else s


def assemble(record_id: str, pages: list[dict], entry: dict, doc_type: str) -> dict:
    flags = []
    # ---- header votes
    votes: dict[str, Counter] = defaultdict(Counter)
    raw_by_norm: dict[tuple, str] = {}
    pages_by_value: dict[tuple, list[int]] = defaultdict(list)
    for pg in pages:
        h = pg.get("header") or {}
        for f in HEADER_FIELDS:
            n = _norm_header(f, h.get(f))
            if n:
                votes[f][n] += 1
                raw_by_norm.setdefault((f, n), h.get(f))
                pages_by_value[(f, n)].append(pg["page"])
    header = {}
    for f, c in votes.items():
        (best, _), = c.most_common(1)
        header[f] = {"value": raw_by_norm[(f, best)], "pages": pages_by_value[(f, best)]}
        if len(c) > 1 and f not in ("generated_on", "form_variant"):
            flags.append({"code": "HEADER_CONFLICT", "severity": "warning",
                          "message": f"{f}: " + "; ".join(f"{raw_by_norm[(f, v)]!r} on p{pages_by_value[(f, v)]}" for v in c),
                          "refs": []})
    extra = {}
    for pg in pages:
        for k, val in ((pg.get("header") or {}).get("extra") or {}).items():
            if val and k not in extra:
                extra[k] = val
    # ---- legend
    legend, seen = [], set()
    for pg in pages:
        for c in pg.get("code_legend") or []:
            key = (c["code"].strip().upper(), c["category"])
            if key not in seen:
                seen.add(key)
                legend.append({"code": c["code"].strip(), "description": c["description"].strip(), "category": c["category"]})
    # ---- employees
    emps, by_num, by_name, rowmap, emp_num = [], {}, {}, {}, {}
    for pg in pages:
        for e in pg.get("employees") or []:
            num = (e.get("employee_number") or "").strip().lstrip("0") or None
            nk = name_key(e.get("name"))
            if num:  # employee number decides; fall back to name only if that employee had no number
                ref = by_num.get(num)
                if ref is None and by_name.get(nk) and not emp_num.get(by_name[nk]):
                    ref = by_name[nk]
            else:
                ref = by_name.get(nk)
            if ref is None:
                ref = f"E{len(emps) + 1:03d}"
                emps.append({"employee_ref": ref, "name": e.get("name"), "employee_number": e.get("employee_number"),
                             "clock_number": e.get("clock_number"), "pay_type": e.get("pay_type"),
                             "printed_fields": dict(e.get("printed_fields") or {}),
                             "recurring_deductions": list(e.get("recurring_deductions") or []),
                             "marks": list(e.get("marks") or []), "page": pg["page"]})
            else:
                cur = next(x for x in emps if x["employee_ref"] == ref)
                for k, val in (e.get("printed_fields") or {}).items():
                    cur["printed_fields"].setdefault(k, val)
                cur["marks"].extend(m for m in e.get("marks") or [] if m not in cur["marks"])
                have = {d["code"] for d in cur["recurring_deductions"]}
                cur["recurring_deductions"].extend(d for d in e.get("recurring_deductions") or [] if d["code"] not in have)
            if num:
                by_num.setdefault(num, ref)
                emp_num.setdefault(ref, num)
            by_name.setdefault(nk, ref)
            rowmap[(pg["page"], e.get("row_key"))] = ref
    # ---- entries
    entries = []
    for pg in pages:
        for en in pg.get("entries") or []:
            ref = rowmap.get((pg["page"], en.get("row_key")))
            if ref is None and en.get("row_key"):
                ref = by_name.get(name_key(en["row_key"])) or by_num.get(str(en["row_key"]).lstrip("0"))
            entries.append({**en, "page": pg["page"], "employee_ref": ref})
            if ref is None:
                flags.append({"code": "UNLINKED_ENTRY", "severity": "warning",
                              "message": f"p{pg['page']}: entry {en.get('column')}={en.get('value_text')!r} has no matching employee (row_key {en.get('row_key')!r})",
                              "refs": []})
    # ---- totals scope
    kinds = {pg["page"]: pg.get("page_kind") for pg in pages}
    pages_with = defaultdict(set)
    for pg in pages:
        for t in pg.get("totals") or []:
            pages_with[(t["column"], t.get("code_written"))].add(pg["page"])
    totals = []
    for pg in pages:
        for t in pg.get("totals") or []:
            scope = "cover_sheet" if kinds.get(pg["page"]) == "fax_cover_sheet" else \
                ("page" if len(pages_with[(t["column"], t.get("code_written"))]) > 1 else "document")
            totals.append({**t, "page": pg["page"], "scope": scope})
    # ---- segments (page kinds)
    segments, cur = [], None
    for pg in sorted(pages, key=lambda p: p["page"]):
        k = pg.get("page_kind") or "other"
        cn = _norm_header("client_number", (pg.get("header") or {}).get("client_number"))
        if cur and cur["doc_type"] == k and (cn is None or cur["client_number"] in (None, cn)):
            cur["pages"].append(pg["page"]); cur["client_number"] = cur["client_number"] or cn
        else:
            cur = {"pages": [pg["page"]], "doc_type": k, "client_number": cn,
                   "client_name": (pg.get("header") or {}).get("client_name"), "note": None}
            segments.append(cur)
    main_kind = Counter(pg.get("page_kind") for pg in pages).most_common(1)[0][0] if pages else None
    if main_kind == "coad_timesheet":
        doc_type = "coad_payroll_timesheet_report"
    return {
        "record_id": record_id, "assembled_at": now(), "doc_type": doc_type, "header": header, "header_extra": extra,
        "code_legend": legend, "employees": emps, "entries": entries, "totals": totals,
        "instructions": [{**i, "page": pg["page"]} for pg in pages for i in pg.get("instructions") or []],
        "time_punches": [{**t, "page": pg["page"]} for pg in pages for t in pg.get("time_punches") or []],
        "direct_deposit": [{**d, "page": pg["page"]} for pg in pages for d in pg.get("direct_deposit") or []],
        "signatures": [{**s, "page": pg["page"]} for pg in pages for s in pg.get("signatures") or []],
        "pages": [{"page": pg["page"], "orientation": pg.get("orientation"), "printed_text": pg.get("printed_text"),
                   "handwritten_text": pg.get("handwritten_text"), "page_kind": pg.get("page_kind")} for pg in pages],
        "segments": segments if len(segments) > 1 else [],
        "issues": [{"page": pg["page"], "text": t} for pg in pages for t in pg.get("issues") or []],
        "flags": flags,
    }


def run(ctx: Context, rec, entry: dict) -> None:
    ex = rec.read("extract.json") if rec.has("extract.json") else None
    if not ex or not ex.get("ok"):
        raise StageSkip("extraction incomplete")
    pre = rec.read("preprocess.json")
    pages = [read_json(rec.page_json(n)) for n in range(1, pre["page_count"] + 1)]
    doc_type = rec.read("classify.json")["doc_type"] if rec.has("classify.json") else "other"
    rec.write("assembled.json", assemble(rec.record_id, pages, entry, doc_type))
