"""Stage 4: document type.

Documents with a real text layer are classified deterministically and for free, via anchor
regexes on the extracted text -- validated against the full sample set with 100% precision (see
coad_payroll_timesheet_report.py). Documents with no usable text layer (scanned/flattened PDFs,
raster image attachments -- about two thirds of real PDF attachments) have no reliable
deterministic alternative: perceptual image-hashing of the header region was built and tested
against real samples and produced real false positives (an unrelated W-4 form and a different
client's own payroll spreadsheet both matched), with no threshold that cleanly separated them from
genuine matches, so that approach was dropped rather than shipped. For this subset, a cheap Haiku
vision call on page 1 is the classification method.

Either way, extract.py's doc_type gate is what actually prevents the expensive per-page extraction
Claude calls for anything that isn't the target template -- this stage's job is only to label
correctly and as cheaply as possible.
"""
from __future__ import annotations

import re

from ..llm.client import image_block, text_block
from ..llm.prompts import CLASSIFY_SYSTEM, CLASSIFY_SCHEMA
from ..store import now
from .base import Context, StageSkip
from ..templates import coad_payroll_timesheet_report as coad_pts

MIN_TEXT_CHARS = 20  # below this, treat the page as having no usable text layer

TITLE_RX = re.compile(coad_pts.TITLE_ANCHOR, re.I)
SUPPORTING_RX = [re.compile(p, re.I) for p in coad_pts.SUPPORTING_ANCHORS]

# Other known text-layer-identifiable types. These never gate extraction (only
# coad_payroll_timesheet_report is in ctx.cfg.target_doc_types by default) -- kept purely so
# non-target documents are labeled rather than lumped into an undifferentiated "other" when the
# text layer makes it easy and free to tell them apart.
OTHER_RULES = [
    (re.compile(r"accrued\s+hours\s+report", re.I), "coad_accrued_hours_report"),
    (re.compile(r"time\s+card\s+detail\s+report", re.I), "time_card_detail_report"),
    (re.compile(r"direct\s+deposit", re.I), "direct_deposit_form"),
    (re.compile(r"fax\s+payroll\s+cover\s+sheet|fax\s+cover", re.I), "fax_cover_sheet"),
    (re.compile(r"employee'?s\s+withholding\s+certificate|form\s+w-4", re.I), "tax_form"),
]


def target_template_text_match(text: str) -> bool:
    """Title phrase is required; at least one supporting anchor must also appear, as defense in
    depth beyond what a single anchor already validated as sufficient on sample data (see
    coad_payroll_timesheet_report.py's calibration notes)."""
    if not TITLE_RX.search(text or ""):
        return False
    return any(rx.search(text) for rx in SUPPORTING_RX)


def other_rule_classify(text: str) -> str | None:
    for rx, dt in OTHER_RULES:
        if rx.search(text or ""):
            return dt
    return None


def run(ctx: Context, rec, entry: dict) -> None:
    pre = rec.read("preprocess.json")
    if not pre.get("pages"):
        raise StageSkip("no pages")
    page1_text = pre["pages"][0]["text"]
    text_all = "\n".join(p["text"] for p in pre["pages"])
    has_text_layer = len(page1_text.strip()) >= MIN_TEXT_CHARS

    if has_text_layer:
        if target_template_text_match(page1_text) or target_template_text_match(text_all):
            rec.write("classify.json", {"doc_type": coad_pts.DOC_TYPE, "method": "rules_text", "at": now()})
            return
        dt = other_rule_classify(page1_text) or other_rule_classify(text_all)
        # A real text layer that matches none of the known rules is confidently "other" -- the
        # rule set was validated to have zero near-misses against the sample data, so there's no
        # benefit to spending a Claude call here.
        rec.write("classify.json", {"doc_type": dt or "other", "method": "rules_text", "at": now()})
        return

    # No usable text layer: no reliable deterministic method exists (see module docstring), so a
    # cheap Haiku vision call on page 1 is the classification method for this subset.
    if ctx.llm is not None and ctx.cfg.llm_classify and ctx.cfg.allow_pii_to_llm:
        pre = rec.read("preprocess.json")
        image_format = pre["pages"][0].get("image_format", "png")
        media_type = f"image/{image_format}"
        res = ctx.llm.call_tool(model=ctx.cfg.classify_model, system=CLASSIFY_SYSTEM,
                                content=[image_block(rec.page_image(1), media_type=media_type), text_block("Classify this first page.")],
                                tool_name="classify_document", tool_description="Record the document type.",
                                input_schema=CLASSIFY_SCHEMA, max_tokens=300,
                                trace={"record_id": rec.record_id, "stage": "classify"})
        rec.log_llm({"stage": "classify", "model": res.model, "usage": res.usage, "latency_s": res.latency_s, "at": now()})
        rec.write("classify.json", {"doc_type": res.data.get("doc_type", "other"), "method": "llm",
                                    "reason": res.data.get("reason"), "model": res.model, "at": now()})
        return

    rec.write("classify.json", {"doc_type": "other", "method": "default", "at": now(),
                                "note": "no text layer and LLM classification disabled"})
