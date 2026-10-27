"""The Anthropic API event log: what is written per HTTP attempt, the tool_choice fallback, and the diagnosis summary.

No network: httpx.MockTransport stands in for api.anthropic.com. The client gets the same event hooks it gets in
production, through the http_client argument.
"""
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import anthropic  # noqa: E402

from src.pipeline import apilog  # noqa: E402
from src.pipeline.llm.client import AnthropicClient  # noqa: E402
from src.pipeline.store import read_jsonl  # noqa: E402


def _ok_message(tool: str = "record_page") -> dict:
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
            "content": [{"type": "tool_use", "id": "tu_1", "name": tool, "input": {"page": 1}}],
            "stop_reason": "tool_use", "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5, "cache_creation_input_tokens": 0,
                      "cache_read_input_tokens": 0}}


def _error(status: int, message: str, request_id: str = "req_test") -> httpx.Response:
    body = {"type": "error", "error": {"type": "invalid_request_error", "message": message}}
    return httpx.Response(status, json=body, headers={"request-id": request_id})


def _client(tmp_path: Path, handler) -> tuple[AnthropicClient, apilog.ApiEventLog]:
    log = apilog.ApiEventLog(tmp_path / "logs" / "api_calls.jsonl")
    http = httpx.Client(transport=httpx.MockTransport(handler), event_hooks=apilog.hooks(log))
    client = AnthropicClient(api_key="test-key", max_retries=0, event_log=log, http_client=http)
    return client, log


def _call(client: AnthropicClient, trace: dict | None = None, image_chars: int = 0):
    content = [{"type": "text", "text": "read this page"}]
    if image_chars:
        content.insert(0, {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                        "data": "A" * image_chars}})
    return client.call_tool(model="claude-sonnet-5-5", system="system prompt", content=content,
                            tool_name="record_page", tool_description="Record the page.",
                            input_schema={"type": "object"}, max_tokens=100, trace=trace)


def test_bad_request_is_logged_with_status_body_and_request_id_but_no_image_data(tmp_path):
    def handler(request):
        return _error(400, "image exceeds the maximum size", request_id="req_123")

    client, log = _client(tmp_path, handler)
    with pytest.raises(anthropic.BadRequestError):
        _call(client, trace={"record_id": "R1", "stage": "extract", "page": 2, "attempt": 0}, image_chars=5000)

    events = read_jsonl(log.path)
    http = [e for e in events if e["outcome"] == "http"]
    assert len(http) == 1
    event = http[0]
    assert event["status"] == 400
    assert event["request_id"] == "req_123"
    assert event["record_id"] == "R1" and event["stage"] == "extract" and event["page"] == 2
    assert "image exceeds the maximum size" in event["body"]
    assert event["request"]["images"] == 1
    assert event["request"]["image_base64_chars"] == 5000
    assert event["request"]["image_media_types"] == ["image/png"]

    call_error = [e for e in events if e["outcome"] == "call_error"]
    assert call_error and call_error[0]["request_id"] == "req_123"
    assert "A" * 50 not in log.path.read_text(encoding="utf-8")


def test_forced_tool_refusal_falls_back_once_per_model_and_is_remembered(tmp_path):
    sent_choices = []

    def handler(request):
        body = json.loads(request.content)
        sent_choices.append(body["tool_choice"]["type"])
        if body["tool_choice"]["type"] == "tool":
            return _error(400, "tool_choice of type tool is not supported with this model")
        return httpx.Response(200, json=_ok_message(), headers={"request-id": "req_ok"})

    client, log = _client(tmp_path, handler)
    first = _call(client, trace={"record_id": "R1", "stage": "extract"})
    second = _call(client, trace={"record_id": "R2", "stage": "extract"})

    assert first.data == {"page": 1} and second.data == {"page": 1}
    # One refused forced call and one accepted auto call for the first record; the second record goes straight to auto.
    assert sent_choices == ["tool", "auto", "auto"]
    events = read_jsonl(log.path)
    assert sum(e["outcome"] == "tool_choice_fallback" for e in events) == 1
    ok_calls = [e for e in events if e["outcome"] == "call_ok"]
    assert [c["tool_choice"] for c in ok_calls] == ["auto", "auto"]


def test_unrelated_bad_request_is_not_swallowed(tmp_path):
    sent_choices = []

    def handler(request):
        sent_choices.append(json.loads(request.content)["tool_choice"]["type"])
        return _error(400, "max_tokens is too large for this model")

    client, log = _client(tmp_path, handler)
    with pytest.raises(anthropic.BadRequestError):
        _call(client, trace={"record_id": "R1", "stage": "extract"})

    assert sent_choices == ["tool"]  # no retry with auto
    events = read_jsonl(log.path)
    assert not any(e["outcome"] == "tool_choice_fallback" for e in events)
    assert any(e["outcome"] == "call_error" for e in events)


def test_concurrent_calls_are_attributed_to_their_own_record(tmp_path):
    def handler(request):
        return httpx.Response(200, json=_ok_message(), headers={"request-id": "req_ok"})

    client, log = _client(tmp_path, handler)
    with ThreadPoolExecutor(max_workers=2) as ex:
        futures = [ex.submit(_call, client, {"record_id": rid, "stage": "extract"}) for rid in ("R1", "R2")]
        for f in futures:
            f.result()

    http = [e for e in read_jsonl(log.path) if e["outcome"] == "http"]
    assert sorted(e["record_id"] for e in http) == ["R1", "R2"]


def test_summary_groups_errors_and_reports_retries_fallbacks_and_cache():
    events = [
        {"outcome": "http", "status": 400, "request_id": "r1", "record_id": "R1",
         "body": json.dumps({"error": {"type": "invalid_request_error", "message": "image too large"}})},
        {"outcome": "http", "status": 400, "request_id": "r2", "record_id": "R2",
         "body": json.dumps({"error": {"type": "invalid_request_error", "message": "image too large"}})},
        {"outcome": "http", "status": 200, "request_id": "r3", "record_id": "R2"},
        {"outcome": "call_ok", "latency_s": 2.0, "http_attempts": 2,
         "usage": {"cache_read_input_tokens": 3, "cache_creation_input_tokens": 1}},
        {"outcome": "tool_choice_fallback", "model": "m", "message": "not supported"},
    ]
    summary = apilog.summarize(events)

    assert summary["http_by_status"] == {"400": 2, "200": 1}
    assert summary["errors"]["400 invalid_request_error"]["count"] == 2
    assert summary["errors"]["400 invalid_request_error"]["request_ids"] == ["r1", "r2"]
    assert summary["calls_retried"] == 1
    assert summary["tool_choice_fallbacks"] == [{"model": "m", "message": "not supported"}]
    assert summary["prompt_cache_hit_ratio"] == 0.75
    assert summary["latency_s"]["p50"] == 2.0


def test_summarize_handles_an_empty_log():
    summary = apilog.summarize([])
    assert summary["http_attempts"] == 0 and summary["prompt_cache_hit_ratio"] is None
