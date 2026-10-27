"""Logging setup (idempotent, verbose-safe, redacting) and the run's export when the run is interrupted."""
from __future__ import annotations

import logging
import signal
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src import logsetup  # noqa: E402
from src.pipeline import cli  # noqa: E402

EXPORT_KEYS = ("records_exported", "review_status", "totals", "salesforce_checks", "llm", "failures")


@pytest.fixture(autouse=True)
def _clean_handlers():
    yield
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, logsetup._TAG, None):
            root.removeHandler(h)
            h.close()


@pytest.fixture(autouse=True)
def _restore_sigterm():
    previous = signal.getsignal(signal.SIGTERM)
    yield
    signal.signal(signal.SIGTERM, previous)


def _file_handlers(path: Path) -> list:
    return [h for h in logging.getLogger().handlers if getattr(h, logsetup._TAG, "") == f"file:{path}"]


def test_configure_is_idempotent_and_writes_to_the_run_logs_folder(tmp_path):
    first = logsetup.configure(tmp_path, "pipeline")
    second = logsetup.configure(tmp_path, "pipeline")
    assert first == second == tmp_path / "logs" / "pipeline.log"
    assert len(_file_handlers(first)) == 1
    logging.getLogger("src.test").warning("hello")
    assert "hello" in first.read_text(encoding="utf-8")


def test_verbose_raises_src_only_and_keeps_sdk_and_http_at_info():
    logsetup.configure(None, "x", verbose=True)
    assert logging.getLogger("src").level == logging.DEBUG
    assert logging.getLogger("anthropic").level == logging.INFO
    assert logging.getLogger("httpx").level == logging.INFO


def test_redaction_backstop_replaces_long_base64_runs():
    record = logging.LogRecord("src.x", logging.INFO, __file__, 1, "data=%s", ("A" * 300,), None)
    logsetup.RedactLongRuns().filter(record)
    assert record.getMessage() == "data=<redacted 300 chars>"


def test_interrupted_run_still_exports_what_finished(tmp_path, monkeypatch):
    calls = []

    def interrupted(ctx, *args, **kwargs):
        raise KeyboardInterrupt("SIGTERM")

    def fake_export(ctx, workbook=True):
        calls.append("export")
        return {k: 0 for k in EXPORT_KEYS}

    monkeypatch.setattr(cli, "run_records", interrupted)
    monkeypatch.setattr(cli, "export", fake_export)
    with pytest.raises(KeyboardInterrupt):
        cli.main(["run", "--run-id", "t-int", "--runs-dir", str(tmp_path), "--llm", "none"])
    assert calls == ["export"]


def test_normal_run_exports_once(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "run_records", lambda ctx, *a, **kw: [])
    monkeypatch.setattr(cli, "export", lambda ctx, workbook=True: (calls.append("export"), {k: 0 for k in EXPORT_KEYS})[1])
    assert cli.main(["run", "--run-id", "t-ok", "--runs-dir", str(tmp_path), "--llm", "none"]) == 0
    assert calls == ["export"]
