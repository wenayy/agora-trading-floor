"""A minimal, explicit agent loop over MCP.

ToolBelt aggregates tools from several MCP sessions (plus local Python tools such as the
researcher sub-agent) into one OpenAI-style tool list, routes each call to the right server,
and turns MCP results and errors into tool messages the model can read. The loop is the
classic one: model → tool calls → results → model …, bounded by ``max_turns``.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from mcp import ClientSession

from ..tracing import span
from .brains import Brain, ToolCall

LocalFn = Callable[[dict], Awaitable[str]]
EventFn = Callable[[str, dict], None]


@dataclass
class ToolSpec:
    name: str
    description: str
    schema: dict
    session: ClientSession | None = None
    local: LocalFn | None = None
    remote_name: str = ""


class ToolBelt:
    def __init__(self):
        self.tools: dict[str, ToolSpec] = {}

    async def add_session(self, label: str, session: ClientSession, allow: set[str] | None = None) -> None:
        for t in (await session.list_tools()).tools:
            if allow and t.name not in allow:
                continue
            name = t.name if t.name not in self.tools else f"{label}_{t.name}"
            self.tools[name] = ToolSpec(name, t.description or "", t.inputSchema, session=session, remote_name=t.name)

    def add_local(self, name: str, description: str, schema: dict, fn: LocalFn) -> None:
        self.tools[name] = ToolSpec(name, description, schema, local=fn)

    def openai_tools(self) -> list[dict]:
        return [{"type": "function", "function": {"name": t.name, "description": t.description[:1000],
                                                  "parameters": t.schema}} for t in self.tools.values()]

    async def call(self, name: str, args: dict) -> tuple[str, bool]:
        spec = self.tools.get(name)
        if spec is None:
            return f"unknown tool {name!r}; available: {', '.join(self.tools)}", True
        if spec.local is not None:
            try:
                return await spec.local(args), False
            except Exception as e:
                return f"{type(e).__name__}: {e}", True
        res = await spec.session.call_tool(spec.remote_name, args)
        if res.structuredContent is not None:
            sc = res.structuredContent
            text = json.dumps(sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc)
        else:
            parts = [c.text for c in res.content if getattr(c, "type", "") == "text"]
            text = parts[0] if len(parts) == 1 else json.dumps([_maybe_json(p) for p in parts])
        return text, bool(res.isError)


def _maybe_json(t: str) -> Any:
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        return t


@dataclass
class AgentResult:
    final: str
    turns: int
    tool_calls: list[dict] = field(default_factory=list)


async def run_agent(brain: Brain, system: str, task: str, belt: ToolBelt, *, max_turns: int = 10,
                    on_event: EventFn | None = None, name: str = "agent") -> AgentResult:
    emit = on_event or (lambda _k, _d: None)
    messages: list[dict] = [{"role": "system", "content": system}, {"role": "user", "content": task}]
    log: list[dict] = []
    tools = belt.openai_tools()
    with span(f"agent.{name}") as s:
        for turn in range(1, max_turns + 1):
            with span("model.turn", model=brain.model, turn=turn):
                reply = await asyncio.to_thread(brain.respond, messages, tools)
            messages.append(_assistant_message(reply.content, reply.tool_calls))
            if reply.content and reply.tool_calls:
                emit("thought", {"agent": name, "text": reply.content})
            if not reply.tool_calls:
                s.set(turns=turn, tool_calls=len(log))
                return AgentResult(reply.content or "", turn, log)
            for tc in reply.tool_calls:  # sequential on purpose: order of trades matters (sell before buy)
                with span(f"tool.{tc.name}"):
                    text, is_error = await belt.call(tc.name, tc.arguments)
                entry = {"agent": name, "tool": tc.name, "args": tc.arguments, "error": is_error, "result": text[:4000]}
                log.append(entry)
                emit("tool_error" if is_error else "tool_call", entry)
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": text[:12000]})
        # budget exhausted: ask for a final summary without tools
        messages.append({"role": "user", "content": "Turn budget reached. Summarise what you did and why in 3 sentences."})
        reply = await asyncio.to_thread(brain.respond, messages, [])
        s.set(turns=max_turns, tool_calls=len(log), budget_hit=True)
        return AgentResult(reply.content or "", max_turns, log)


def _assistant_message(content: str | None, calls: list[ToolCall]) -> dict:
    msg: dict = {"role": "assistant", "content": content or ""}
    if calls:
        msg["tool_calls"] = [{"id": c.id, "type": "function",
                              "function": {"name": c.name, "arguments": json.dumps(c.arguments)}} for c in calls]
    return msg
