"""Run folder layout, atomic JSON I/O, per-record stage state."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

_lock = threading.Lock()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: Path, obj: Any, indent: int | None = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp_", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=indent, ensure_ascii=False, default=str)
    os.replace(tmp, path)


def read_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def append_jsonl(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock, open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")


def read_jsonl(path: Path) -> list:
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


def write_jsonl(path: Path, rows: Iterable[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp_", suffix=".jsonl")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    os.replace(tmp, path)


class RunStore:
    """runs/<run_id>/ ..."""

    def __init__(self, runs_dir: Path, run_id: str):
        self.run_id = run_id
        self.root = Path(runs_dir) / run_id
        self.root.mkdir(parents=True, exist_ok=True)

    @property
    def manifest_path(self) -> Path:
        return self.root / "manifest.jsonl"

    def manifest(self) -> list[dict]:
        return read_jsonl(self.manifest_path)

    def sf_path(self, case_number: str) -> Path:
        return self.root / "sf" / f"{case_number}.json"

    @property
    def output_format_path(self) -> Path:
        # Written only by an explicit --output-format, never by write_run_info, so later commands keep it.
        return self.root / "output_format.json"

    def get_output_format(self) -> str | None:
        p = self.output_format_path
        return read_json(p).get("output_format") if p.exists() else None

    def set_output_format(self, fmt: str, command: str) -> None:
        write_json(self.output_format_path, {"output_format": fmt, "set_at": now(), "set_by_command": command})

    def record(self, record_id: str) -> "RecordStore":
        return RecordStore(self.root / "records" / record_id, record_id)

    def record_ids(self) -> list[str]:
        return [m["record_id"] for m in self.manifest() if m.get("status") == "ready"]


class RecordStore:
    def __init__(self, root: Path, record_id: str):
        self.root = root
        self.record_id = record_id
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "pages").mkdir(exist_ok=True)

    def path(self, name: str) -> Path:
        return self.root / name

    def page_image(self, n: int) -> Path:
        return self.root / "pages" / f"page-{n:03d}.png"

    def page_json(self, n: int) -> Path:
        return self.root / "pages" / f"page-{n:03d}.json"

    def has(self, name: str) -> bool:
        return (self.root / name).exists()

    def read(self, name: str) -> Any:
        return read_json(self.root / name)

    def write(self, name: str, obj: Any) -> None:
        write_json(self.root / name, obj)

    # ---- state ----
    @property
    def state_path(self) -> Path:
        return self.root / "state.json"

    def state(self) -> dict:
        return read_json(self.state_path) if self.state_path.exists() else {}

    def set_state(self, stage: str, **kw) -> None:
        with _lock:
            st = self.state()
            cur = st.get(stage, {})
            cur.update(kw)
            st[stage] = cur
            write_json(self.state_path, st)

    def input_hash(self, names: Iterable[str]) -> str:
        h = hashlib.sha256()
        for n in names:
            p = self.root / n
            h.update(n.encode())
            if p.is_file():
                h.update(sha256_file(p).encode())
            elif p.is_dir():
                for f in sorted(p.glob("*.json")):
                    h.update(f.name.encode()); h.update(sha256_file(f).encode())
        return h.hexdigest()

    def log_llm(self, entry: dict) -> None:
        append_jsonl(self.root / "llm_calls.jsonl", entry)
