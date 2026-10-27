"""LLM access behind a small interface. Only stages 4 (fallback), 5 and 10 use it."""
from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from .. import apilog
from ..store import now

log = logging.getLogger(__name__)
MAX_ERROR = 2000


@dataclass
class ToolResult:
    data: dict
    usage: dict = field(default_factory=dict)
    model: str = ""
    stop_reason: str | None = None
    latency_s: float = 0.0


class LLMClient(Protocol):
    def call_tool(self, *, model: str, system: str, content: list[dict], tool_name: str,
                  tool_description: str, input_schema: dict, max_tokens: int,
                  trace: dict | None = None) -> ToolResult: ...


def image_block(path: Path, media_type: str = "image/png") -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": media_type,
                                        "data": base64.standard_b64encode(Path(path).read_bytes()).decode()}}


def text_block(text: str) -> dict:
    return {"type": "text", "text": text}


def _strip_meta(schema: dict) -> dict:
    """Tool input_schema: drop $schema/$id/title keys the API does not need."""
    return {k: v for k, v in schema.items() if k not in ("$schema", "$id", "title")}


def _forced_rejected(message: str) -> bool:
    """True when the API refused a forced tool_choice and only accepts "auto" (e.g. extended thinking on)."""
    return "tool_choice" in message and ("not supported" in message or "not be used" in message)


class AnthropicClient:
    """Anthropic Messages API with a forced tool call for structured output.

    The system prompt is marked for prompt caching so repeated page calls reuse it.

    tool_choice: "try" (default) forces the tool and, if a model refuses that, uses "auto" for that model from then
    on; "tool" always forces and never falls back; "auto" never forces. Every HTTP attempt is written to event_log
    when one is given (see apilog.py)."""

    def __init__(self, api_key: str | None = None, max_retries: int = 4, event_log: apilog.ApiEventLog | None = None,
                 tool_choice: str = "try", http_client: Any = None):
        import anthropic  # imported lazily so deterministic stages run without the SDK
        self._anthropic = anthropic
        self._events = event_log
        self._tool_choice = tool_choice
        self._auto_only: set[str] = set()
        if http_client is None and event_log is not None:
            http_client = anthropic.DefaultHttpxClient(event_hooks=apilog.hooks(event_log))
        self._client = anthropic.Anthropic(api_key=api_key, max_retries=max_retries, http_client=http_client)

    def call_tool(self, *, model, system, content, tool_name, tool_description, input_schema, max_tokens, trace=None):
        t0 = time.monotonic()
        kwargs = dict(
            model=model,
            max_tokens=max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            tools=[{"name": tool_name, "description": tool_description, "input_schema": _strip_meta(input_schema)}],
            messages=[{"role": "user", "content": content}],
        )
        with apilog.trace_scope(trace) as state:
            try:
                resp, choice = self._create(model, tool_name, kwargs)
            except Exception as e:
                self._event(state, "call_error", error_type=type(e).__name__,
                            status=getattr(e, "status_code", None), request_id=getattr(e, "request_id", None),
                            error=str(e)[:MAX_ERROR], latency_s=round(time.monotonic() - t0, 2))
                raise
            data = next((b.input for b in resp.content if getattr(b, "type", None) == "tool_use" and b.name == tool_name), None)
            if data is None:
                err = f"model returned no {tool_name} tool call (stop_reason={resp.stop_reason})"
                self._event(state, "call_error", error_type="NoToolCall", error=err, stop_reason=resp.stop_reason,
                            latency_s=round(time.monotonic() - t0, 2))
                raise RuntimeError(err)
            u = resp.usage
            usage = {k: getattr(u, k, None) for k in ("input_tokens", "output_tokens",
                                                       "cache_creation_input_tokens", "cache_read_input_tokens")}
            latency = round(time.monotonic() - t0, 2)
            self._event(state, "call_ok", model=resp.model, stop_reason=resp.stop_reason, usage=usage,
                        latency_s=latency, tool_choice=choice["type"])
            return ToolResult(data=data, usage=usage, model=resp.model, stop_reason=resp.stop_reason, latency_s=latency)

    def _create(self, model: str, tool_name: str, kwargs: dict):
        choice = self._choice(model, tool_name)
        try:
            return self._client.messages.create(tool_choice=choice, **kwargs), choice
        except self._anthropic.BadRequestError as e:
            msg = str(e)
            if self._tool_choice != "tool" and choice["type"] == "tool" and _forced_rejected(msg):
                self._remember_rejection(model, msg)
                auto = {"type": "auto"}
                return self._client.messages.create(tool_choice=auto, **kwargs), auto
            raise

    def _choice(self, model: str, tool_name: str) -> dict:
        if self._tool_choice == "auto" or (self._tool_choice == "try" and model in self._auto_only):
            return {"type": "auto"}
        return {"type": "tool", "name": tool_name}

    def _remember_rejection(self, model: str, message: str) -> None:
        if model in self._auto_only:
            return
        self._auto_only.add(model)
        log.warning("model %s refused a forced tool_choice; using auto for it from now on: %s", model, message[:500])
        if self._events is not None:
            self._events.write({"at": now(), "outcome": "tool_choice_fallback", "model": model,
                                "message": message[:MAX_ERROR]})

    def _event(self, state: dict, outcome: str, **fields) -> None:
        if self._events is None:
            return
        self._events.write({"at": now(), **{k: v for k, v in state.items() if not k.startswith("_")},
                            "outcome": outcome, **fields})


class FakeLLMClient:
    """Deterministic stand-in for tests and dry runs. `responder(tool_name, content) -> dict`."""

    def __init__(self, responder: Callable[[str, list[dict]], dict]):
        self.responder = responder
        self.calls: list[dict] = []

    def call_tool(self, *, model, system, content, tool_name, tool_description, input_schema, max_tokens, trace=None):
        self.calls.append({"model": model, "tool": tool_name,
                           "text": [c.get("text") for c in content if c.get("type") == "text"]})
        data = self.responder(tool_name, content)
        return ToolResult(data=json.loads(json.dumps(data)), usage={"input_tokens": 0, "output_tokens": 0},
                          model=f"fake:{model}", stop_reason="tool_use")
