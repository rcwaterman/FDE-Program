"""Per-attempt log of every Anthropic HTTP request, written to <run>/logs/api_calls.jsonl.

Logs the shape of a request and the status of its response, never the content: no base64 image data, no prompt
text, no extracted values. The one exception is the body of an error response from Anthropic, truncated, because
that body is the diagnosis.

The SDK retries inside messages.create(), so each attempt gets its own line. Attempts are attributed to the record,
stage and page that made the call through the TRACE context variable, which call_tool sets in its own thread.
"""
from __future__ import annotations

import contextvars
import json
import time
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import httpx

from .store import append_jsonl, now

MAX_BODY = 2000
# Response headers worth keeping. request-id is the one Anthropic support asks for.
KEPT_HEADERS = ("request-id", "retry-after", "x-should-retry",
                "anthropic-ratelimit-requests-remaining", "anthropic-ratelimit-tokens-remaining",
                "anthropic-ratelimit-input-tokens-remaining", "anthropic-ratelimit-output-tokens-remaining")

# Holds {record_id, stage, page, attempt, http_attempts} for the call running in this thread. None outside a call.
TRACE: contextvars.ContextVar[dict | None] = contextvars.ContextVar("api_trace", default=None)


class ApiEventLog:
    def __init__(self, path: Path):
        self.path = Path(path)

    def write(self, event: dict) -> None:
        append_jsonl(self.path, event)


@contextmanager
def trace_scope(trace: dict | None) -> Iterator[dict]:
    state = {**(trace or {}), "http_attempts": 0}
    token = TRACE.set(state)
    try:
        yield state
    finally:
        TRACE.reset(token)


def summarize_request(body: bytes | None) -> dict:
    """Shape of a Messages API request: sizes and counts only. Image data is measured, never copied."""
    if not body:
        return {}
    try:
        req = json.loads(body)
    except ValueError:
        return {"unparsed_bytes": len(body)}
    images, image_chars, media_types, blocks = 0, 0, set(), 0
    for msg in req.get("messages") or []:
        content = msg.get("content")
        for block in content if isinstance(content, list) else []:
            blocks += 1
            if block.get("type") == "image":
                source = block.get("source") or {}
                images += 1
                image_chars += len(source.get("data") or "")
                media_types.add(source.get("media_type"))
    tool_choice = req.get("tool_choice") or {}
    return {
        "model": req.get("model"),
        "max_tokens": req.get("max_tokens"),
        "tool_choice": tool_choice.get("type"),
        "messages": len(req.get("messages") or []),
        "content_blocks": blocks,
        "images": images,
        "image_base64_chars": image_chars,
        "image_media_types": sorted(m for m in media_types if m),
        "system_chars": len(json.dumps(req.get("system") or "")),
        "tools": [t.get("name") for t in req.get("tools") or []],
    }


def hooks(log: ApiEventLog) -> dict:
    """httpx event hooks. They keep the client's own transport and proxy settings, which a custom transport would
    replace. Pass the result as event_hooks= to an httpx.Client or anthropic.DefaultHttpxClient."""

    def on_request(request: httpx.Request) -> None:
        state = TRACE.get()
        if state is not None:
            state["http_attempts"] += 1
            state["_attempt_t0"] = time.monotonic()
        request.extensions["fde_request_summary"] = summarize_request(request.content)

    def on_response(response: httpx.Response) -> None:
        state = TRACE.get() or {}
        t0 = state.get("_attempt_t0")
        event = {
            "at": now(), **{k: v for k, v in state.items() if not k.startswith("_")},
            "outcome": "http", "method": response.request.method, "path": response.request.url.path,
            "status": response.status_code,
            "elapsed_s": round(time.monotonic() - t0, 2) if t0 else None,
            "request": response.request.extensions.get("fde_request_summary", {}),
            "headers": {k: response.headers.get(k) for k in KEPT_HEADERS if response.headers.get(k) is not None},
        }
        event["request_id"] = event["headers"].get("request-id")
        if response.status_code >= 400:
            event["body"] = response.read().decode("utf-8", "replace")[:MAX_BODY]
        log.write(event)

    return {"request": [on_request], "response": [on_response]}


def summarize(events: list[dict]) -> dict:
    """Totals and error groups for a run's api_calls.jsonl. Pure: no I/O, no network."""
    http = [e for e in events if e.get("outcome") == "http"]
    calls = [e for e in events if e.get("outcome") in ("call_ok", "call_error")]
    by_status = Counter(str(e.get("status")) for e in http)
    errors: dict[str, dict] = {}
    for e in http:
        if (e.get("status") or 0) < 400:
            continue
        key = f"{e['status']} {_error_type(e.get('body'))}"
        group = errors.setdefault(key, {"count": 0, "message": _error_message(e.get("body")), "request_ids": [],
                                        "records": []})
        group["count"] += 1
        if e.get("request_id") and len(group["request_ids"]) < 5:
            group["request_ids"].append(e["request_id"])
        if e.get("record_id") and len(group["records"]) < 5 and e["record_id"] not in group["records"]:
            group["records"].append(e["record_id"])
    fallbacks = [{"model": e.get("model"), "message": e.get("message")}
                 for e in events if e.get("outcome") == "tool_choice_fallback"]
    retried = [c for c in calls if (c.get("http_attempts") or 0) > 1]
    latencies = sorted(c["latency_s"] for c in calls if c.get("outcome") == "call_ok" and c.get("latency_s") is not None)
    usage = Counter()
    for c in calls:
        for k, v in (c.get("usage") or {}).items():
            usage[k] += v or 0
    cache_read, cache_write = usage["cache_read_input_tokens"], usage["cache_creation_input_tokens"]
    return {
        "http_attempts": len(http),
        "http_by_status": dict(by_status),
        "calls_ok": sum(c.get("outcome") == "call_ok" for c in calls),
        "calls_error": sum(c.get("outcome") == "call_error" for c in calls),
        "calls_retried": len(retried),
        "tool_choice_fallbacks": fallbacks,
        "errors": errors,
        "latency_s": {"p50": _percentile(latencies, 0.5), "p95": _percentile(latencies, 0.95)},
        "usage": dict(usage),
        "prompt_cache_hit_ratio": round(cache_read / (cache_read + cache_write), 3) if cache_read + cache_write else None,
    }


def _error_body(body: str | None) -> dict:
    try:
        return json.loads(body or "") or {}
    except ValueError:
        return {}


def _error_type(body: str | None) -> str:
    err = _error_body(body).get("error") or {}
    return err.get("type") or "unparsed_body"


def _error_message(body: str | None) -> str:
    err = _error_body(body).get("error") or {}
    return (err.get("message") or (body or ""))[:300]


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    return values[min(len(values) - 1, int(q * len(values)))]
