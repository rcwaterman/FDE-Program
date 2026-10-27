"""Logging for the pipeline CLI and the review app: console, plus a rotating file under <run>/logs/.

configure() is safe to call again. Streamlit re-runs the app script on every interaction, so each call checks what is
already attached instead of adding another handler.

-v (verbose) only raises the src.* loggers to DEBUG. The anthropic and httpx loggers stay at INFO. At DEBUG the
Anthropic SDK logs the full request body, which includes base64 page images.
"""
from __future__ import annotations

import logging
import logging.handlers
import re
from pathlib import Path

FORMAT = "%(asctime)s %(levelname)s [%(threadName)s] %(name)s: %(message)s"
QUIET_LOGGERS = ("anthropic", "httpx", "httpcore")
_LONG_BASE64 = re.compile(r"[A-Za-z0-9+/]{200,}={0,2}")  # '=' only as trailing padding, so "key=" prefixes survive
_TAG = "_fde_handler"


class RedactLongRuns(logging.Filter):
    """Backstop: replaces any long base64-looking run in a log message. Normal messages never contain one."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        redacted = _LONG_BASE64.sub(lambda m: f"<redacted {len(m.group(0))} chars>", msg)
        if redacted != msg:
            record.msg, record.args = redacted, ()
        return True


def _has_tag(root: logging.Logger, tag: str) -> bool:
    return any(getattr(h, _TAG, None) == tag for h in root.handlers)


def configure(run_root: Path | None, name: str, level: str = "INFO", verbose: bool = False) -> Path | None:
    """Attach console and file logging. Returns the log file path, or None when there is no run folder."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not _has_tag(root, "console"):
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter(FORMAT))
        console.addFilter(RedactLongRuns())
        setattr(console, _TAG, "console")
        root.addHandler(console)

    logging.getLogger("src").setLevel(logging.DEBUG if verbose else getattr(logging, level.upper(), logging.INFO))
    for quiet in QUIET_LOGGERS:
        logging.getLogger(quiet).setLevel(logging.INFO)

    if run_root is None:
        return None
    path = Path(run_root) / "logs" / f"{name}.log"
    tag = f"file:{path}"
    if not _has_tag(root, tag):
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
        handler.setFormatter(logging.Formatter(FORMAT))
        handler.addFilter(RedactLongRuns())
        setattr(handler, _TAG, tag)
        root.addHandler(handler)
    return path
