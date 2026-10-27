"""Record-level stage registry, in execution order."""
from .base import RecordStage
from . import preprocess, classify, extract, assemble, normalize, reconcile, sf_verify, amendments, validate, import_csv

RECORD_STAGES: list[RecordStage] = [
    RecordStage("preprocess", [], "preprocess.json", preprocess.run),
    RecordStage("classify", ["preprocess.json"], "classify.json", classify.run, uses_llm=True),
    RecordStage("extract", ["preprocess.json", "classify.json"], "extract.json", extract.run, uses_llm=True),
    RecordStage("assemble", ["extract.json", "pages"], "assembled.json", assemble.run),
    RecordStage("normalize", ["assembled.json"], "normalized.json", normalize.run),
    RecordStage("reconcile", ["normalized.json"], "reconciled.json", reconcile.run),
    RecordStage("sf_verify", ["reconciled.json", "@sf"], "verified.json", sf_verify.run),
    RecordStage("amendments", ["verified.json", "@sf"], "amended.json", amendments.run, uses_llm=True),
    RecordStage("validate", ["amended.json"], "record.json", validate.run),
    RecordStage("import_csv", ["record.json"], "import_template.csv", import_csv.run, formats=("csv",)),
]
STAGE_NAMES = [s.name for s in RECORD_STAGES]
