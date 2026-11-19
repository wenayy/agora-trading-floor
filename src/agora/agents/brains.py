"""Brains decide what an agent does next: either an LLM with native tool calling, or a deterministic
rule-based policy that drives the very same MCP tools (used offline, in tests and in backtests)."""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Protocol

from ..tracing import record_usage, span

PRICES = {"gpt-4.1-mini": (0.40, 1.60), "gpt-4.1-nano": (0.10, 0.40), "gpt-4o-mini": (0.15, 0.60),
          "gpt-5-mini": (0.25, 2.00), "llama-3.3-70b-versatile": (0.59, 0.79)}


@dataclass
class ToolCall:
    name: str
    arguments: dict
    id: str = field(default_factory=lambda: "call_" + uuid.uuid4().hex[:10])


@dataclass
class Turn:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)


class Brain(Protocol):
    model: str

    def respond(self, messages: list[dict], tools: list[dict]) -> Turn: ...


class LLMBrain:
    """OpenAI-compatible chat completions with tools (OpenAI, Groq, OpenRouter, Together, vLLM, Ollama)."""

    def __init__(self, model: str, base_url: str | None = None, api_key: str | None = None, retries: int = 3):
        from openai import OpenAI

        self.model, self.retries = model, retries
        self._client = OpenAI(base_url=base_url, api_key=api_key or "not-needed", max_retries=0, timeout=90)

    def respond(self, messages: list[dict], tools: list[dict]) -> Turn:
        with span("llm.respond", model=self.model, tools=len(tools)):
            delay = 1.0
            for attempt in range(self.retries):
                try:
                    r = self._client.chat.completions.create(model=self.model, messages=messages, temperature=0.2,
                                                             tools=tools or None)
                    break
                except Exception as e:
                    status = getattr(e, "status_code", None)
                    if attempt == self.retries - 1 or (status and status < 500 and status != 429):
                        raise
                    time.sleep(delay)
                    delay *= 2
            if r.usage:
                pin, pout = r.usage.prompt_tokens, r.usage.completion_tokens
                p = PRICES.get(self.model, (0, 0))
                record_usage(pin, pout, (pin * p[0] + pout * p[1]) / 1e6)
            m = r.choices[0].message
            calls = []
            for tc in m.tool_calls or []:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {"_raw": tc.function.arguments}
                calls.append(ToolCall(tc.function.name, args, tc.id))
            return Turn(m.content, calls)
