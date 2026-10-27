"""Stage definition and shared context."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..config import PipelineConfig
from ..store import RunStore, RecordStore


class StageSkip(Exception):
    """Raised by a stage that does not apply to this record (e.g. extract on a failed preprocess)."""


@dataclass
class Context:
    cfg: PipelineConfig
    run: RunStore
    llm: Any = None          # LLMClient or None
    sf: Any = None           # SalesforceReadOnlyClient or None
    options: dict = field(default_factory=dict)


@dataclass
class RecordStage:
    name: str
    inputs: list[str]        # artifact names inside the record folder (files or 'pages' dir)
    output: str
    fn: Callable[[Context, RecordStore, dict], None]
    uses_llm: bool = False
    formats: tuple[str, ...] | None = None  # output formats this stage runs for; None = all
