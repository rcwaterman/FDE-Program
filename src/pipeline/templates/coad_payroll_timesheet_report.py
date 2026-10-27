"""Reference data for deterministically recognizing a CoAd.PAY "Payroll Timesheet Report" page
from its text layer.

Validated against src/data/attachments/ (149 real PDFs, 51 with a usable text layer): the title
phrase alone achieved perfect separation -- 4 true positives, 0 false positives, and 0 near-misses
among the other 47 (none had more than 1 of 10 candidate keywords). TITLE_ANCHOR is required;
SUPPORTING_ANCHORS adds defense in depth beyond what was already sufficient on sample data.

Documents with no usable text layer (scanned/flattened PDFs, raster images -- about two thirds of
real PDF attachments) have no reliable deterministic equivalent: a perceptual image-hashing
approach (header-region dHash, tried across several crop/resolution variants) was built and
empirically tested against real samples in this same investigation, and rejected -- it produced
real false positives (an unrelated W-4 form and a different client's own payroll spreadsheet both
matched a template header's structure), with no threshold cleanly separating them from genuine
matches. classify.py instead uses a cheap Haiku vision call for that subset; see its module
docstring.
"""
from __future__ import annotations

DOC_TYPE = "coad_payroll_timesheet_report"

TITLE_ANCHOR = r"payroll\s+timesheet\s+report"
SUPPORTING_ANCHORS = (
    r"coad\.?\s*pay",
    r"permanent\s+deductions",
    r"exceptions",
)
