"""The trading floor: advances the market, runs every trader concurrently, and records what happened.

One cycle = one trading day:
    market.step()  →  asyncio.gather(trader sessions)  →  snapshot equity  →  cycle event

Each trader session opens its own MCP sessions (accounts + memory bound to that trader; market
and news shared), builds a ToolBelt, adds the researcher sub-agent as a local tool, and runs the
agent loop. Transport is either in-process memory streams (fast, default) or stdio subprocesses
(one OS process per server, like a production MCP deployment).
"""
from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import Callable
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.memory import create_connected_server_and_client_session

from .agents.brains import Brain, LLMBrain
from .agents.personas import RESEARCHER_SYSTEM, TRADER_SYSTEM, TRADERS, Trader
from .agents.policies import PolicyBrain, PolicyResearcher
from .agents.runtime import ToolBelt, run_agent
from .config import Settings
from .db import DB
from .ledger import Ledger, RiskPolicy
from .market.sim import MarketSimulator
from .mcp_servers.servers import accounts_server, market_server, memory_server, news_server
from .tracing import trace

BrainFactory = Callable[[Trader], Brain]
logging.getLogger("mcp").setLevel(logging.WARNING)  # the SDK logs every request at INFO


class Floor:
    def __init__(self, settings: Settings, db: DB | None = None, brain_factory: BrainFactory | None = None,
                 researcher_brain: Brain | None = None, traders: list[Trader] | None = None):
        self.s = settings
        self.db = db or DB(settings.db_path)
        self.policy = RiskPolicy.from_settings(settings)
        self.ledger = Ledger(self.db, self.policy)
        self.market = MarketSimulator(self.db, seed=settings.seed)
        self.traders = traders or TRADERS
        if brain_factory is None:
            if settings.brain == "llm":
                def brain_factory(_t):
                    return LLMBrain(settings.llm_model, settings.llm_base_url, settings.llm_api_key)
                researcher_brain = researcher_brain or LLMBrain(settings.researcher_model or settings.llm_model,
                                                                settings.llm_base_url, settings.llm_api_key)
            else:
                def brain_factory(t):
                    return PolicyBrain(t.strategy)
        self.brain_factory = brain_factory
        self.researcher_brain = researcher_brain or PolicyResearcher()
        self._cycle_lock = asyncio.Lock()

    # ------------------------------------------------------------------ setup
    def setup(self, warmup_days: int = 30) -> None:
        if self.db.get_meta("initialised"):
            return
        self.market.initialise()
        self.market.warm_up(warmup_days)
        for t in self.traders:
            self.ledger.open_account(t.id, t.name, t.strategy, self.s.starting_cash)
        self.ledger.snapshot_all()
        self.db.set_meta("start_day", self.db.today())
        self.db.set_meta("initialised", True)
        self.db.log_event(None, "floor_open", {"traders": [t.id for t in self.traders], "day": self.db.today()})

    # ------------------------------------------------------------------ MCP sessions
    async def _open(self, stack: AsyncExitStack, kind: str, trader_id: str | None = None) -> ClientSession:
        if self.s.transport == "stdio":
            args = ["-m", "agora.mcp_servers", kind, "--db", self.db.path] + (["--trader", trader_id] if trader_id else [])
            r, w = await stack.enter_async_context(stdio_client(StdioServerParameters(command=sys.executable, args=args)))
            session = await stack.enter_async_context(ClientSession(r, w))
            await session.initialize()
            return session
        server = {"accounts": lambda: accounts_server(self.db, trader_id, self.policy),
                  "market": lambda: market_server(self.db), "news": lambda: news_server(self.db),
                  "memory": lambda: memory_server(self.db, trader_id)}[kind]()
        return await stack.enter_async_context(create_connected_server_and_client_session(server))

    # ------------------------------------------------------------------ one trader
    async def run_trader(self, t: Trader) -> dict:
        day = self.db.today()

        def emit(kind: str, data: dict) -> None:
            self.db.log_event(t.id, kind, data)

        async with AsyncExitStack() as stack:
            accounts = await self._open(stack, "accounts", t.id)
            market = await self._open(stack, "market")
            news = await self._open(stack, "news")
            memory = await self._open(stack, "memory", t.id)

            # the researcher is a sub-agent with read-only tools, exposed to the trader as one tool
            r_belt = ToolBelt()
            await r_belt.add_session("news", news)
            await r_belt.add_session("market", market, allow={"get_price_history", "get_price"})

            async def research(args: dict) -> str:
                q = str(args.get("question", ""))[:500]
                emit("research_request", {"question": q})
                out = await run_agent(self.researcher_brain, RESEARCHER_SYSTEM, q, r_belt, max_turns=4,
                                      on_event=lambda k, d: emit(k, {**d, "agent": "researcher"}), name="researcher")
                emit("research_brief", {"question": q, "brief": out.final})
                return out.final

            belt = ToolBelt()
            for label, sess in (("accounts", accounts), ("market", market), ("memory", memory)):
                await belt.add_session(label, sess)
            belt.add_local("research", "Ask the research analyst a question about specific companies (news, price "
                           "action, valuation). Returns a short brief ending with a signal.",
                           {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]},
                           research)

            system = TRADER_SYSTEM.format(name=t.name, style=t.style, max_pos=self.policy.max_position_pct,
                                          max_order=self.policy.max_order_pct)
            task = (f"Trading day {day - self.db.get_meta('start_day', 0)} has opened. Run your session now.")
            emit("session_start", {"day": day, "brain": getattr(self.brain_factory(t), "model", "?")})
            with trace(f"trader.{t.id}") as tr:
                result = await run_agent(self.brain_factory(t), system, task, belt, max_turns=self.s.max_agent_turns,
                                         on_event=emit, name=t.id)
            trades = [c for c in result.tool_calls if c["tool"] in ("buy_shares", "sell_shares") and not c["error"]]
            rejected = [c for c in result.tool_calls if c["tool"] in ("buy_shares", "sell_shares") and c["error"]]
            summary = {"day": day, "summary": result.final, "turns": result.turns, "trades": len(trades),
                       "rejected": len(rejected), "trace_id": tr.id, "llm_calls": tr.usage.llm_calls,
                       "tokens": tr.usage.prompt_tokens + tr.usage.completion_tokens,
                       "cost_usd": round(tr.usage.cost_usd, 5), "ms": round(tr.root.duration_ms)}
            emit("session_end", summary)
            return summary

    # ------------------------------------------------------------------ a cycle
    async def run_cycle(self) -> dict:
        async with self._cycle_lock:
            day = self.market.step()
            self.db.log_event(None, "market_open", {"day": day, "prices": self.market.last_prices()})
            results = await asyncio.gather(*(self.run_trader(t) for t in self.traders), return_exceptions=True)
            out = {}
            for t, r in zip(self.traders, results, strict=True):
                if isinstance(r, Exception):
                    self.db.log_event(t.id, "session_error", {"error": f"{type(r).__name__}: {r}"})
                    out[t.id] = {"error": str(r)}
                else:
                    out[t.id] = r
            self.ledger.snapshot_all()
            board = {t.id: round(self.ledger.equity(t.id), 2) for t in self.traders}
            self.db.log_event(None, "cycle_done", {"day": day, "equity": board})
            return {"day": day, "traders": out, "equity": board}

    async def run(self, cycles: int) -> list[dict]:
        return [await self.run_cycle() for _ in range(cycles)]

