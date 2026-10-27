"""LLM access behind a small interface. Only stages 4 (fallback), 5 and 10 use it."""
from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol


@dataclass
class ToolResult:
    data: dict
    usage: dict = field(default_factory=dict)
    model: str = ""
    stop_reason: str | None = None
    latency_s: float = 0.0


class LLMClient(Protocol):
    def call_tool(self, *, model: str, system: str, content: list[dict], tool_name: str,
                  tool_description: str, input_schema: dict, max_tokens: int) -> ToolResult: ...


def image_block(path: Path, media_type: str = "image/png") -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": media_type,
                                        "data": base64.standard_b64encode(Path(path).read_bytes()).decode()}}


def text_block(text: str) -> dict:
    return {"type": "text", "text": text}


def _strip_meta(schema: dict) -> dict:
    """Tool input_schema: drop $schema/$id/title keys the API does not need."""
    return {k: v for k, v in schema.items() if k not in ("$schema", "$id", "title")}


class AnthropicClient:
    """Anthropic Messages API with a forced tool call for structured output.

    The system prompt is marked for prompt caching so repeated page calls reuse it."""

    def __init__(self, api_key: str | None = None, max_retries: int = 4):
        import anthropic  # imported lazily so deterministic stages run without the SDK
        self._anthropic = anthropic
        self._client = anthropic.Anthropic(api_key=api_key, max_retries=max_retries)

    def call_tool(self, *, model, system, content, tool_name, tool_description, input_schema, max_tokens):
        t0 = time.monotonic()
        kwargs = dict(
            model=model,
            max_tokens=max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            tools=[{"name": tool_name, "description": tool_description, "input_schema": _strip_meta(input_schema)}],
            messages=[{"role": "user", "content": content}],
        )
        try:
            resp = self._client.messages.create(tool_choice={"type": "tool", "name": tool_name}, **kwargs)
        except self._anthropic.BadRequestError as e:
            # Some model variants (e.g. ones with extended thinking enabled) reject a forced
            # tool_choice and only accept "auto" -- fall back to it rather than failing every call.
            msg = str(e)
            if "tool_choice" in msg and ("not supported" in msg or "not be used" in msg):
                resp = self._client.messages.create(tool_choice={"type": "auto"}, **kwargs)
            else:
                raise
        data = next((b.input for b in resp.content if getattr(b, "type", None) == "tool_use" and b.name == tool_name), None)
        if data is None:
            raise RuntimeError(f"model returned no {tool_name} tool call (stop_reason={resp.stop_reason})")
        u = resp.usage
        usage = {k: getattr(u, k, None) for k in ("input_tokens", "output_tokens",
                                                   "cache_creation_input_tokens", "cache_read_input_tokens")}
        return ToolResult(data=data, usage=usage, model=resp.model, stop_reason=resp.stop_reason,
                          latency_s=round(time.monotonic() - t0, 2))


class FakeLLMClient:
    """Deterministic stand-in for tests and dry runs. `responder(tool_name, content) -> dict`."""

    def __init__(self, responder: Callable[[str, list[dict]], dict]):
        self.responder = responder
        self.calls: list[dict] = []

    def call_tool(self, *, model, system, content, tool_name, tool_description, input_schema, max_tokens):
        self.calls.append({"model": model, "tool": tool_name,
                           "text": [c.get("text") for c in content if c.get("type") == "text"]})
        data = self.responder(tool_name, content)
        return ToolResult(data=json.loads(json.dumps(data)), usage={"input_tokens": 0, "output_tokens": 0},
                          model=f"fake:{model}", stop_reason="tool_use")
