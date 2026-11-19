"""FastAPI backend for the floor dashboard.

GET  /api/floor              leaderboard: equity, return, positions, last session summary per trader
GET  /api/equity             equity curves for every trader + the equal-weight benchmark
GET  /api/traders/{id}       account report, order tickets, notebook, recent sessions
GET  /api/market             market snapshot        GET /api/news     news wire
GET  /api/metrics            risk-adjusted performance table
GET  /api/events?after=ID    event log page         GET /api/stream   live events (SSE)
POST /api/cycle              run one trading day    POST /api/autopilot {enabled, interval_s}
GET  /api/traces/{id}        span tree for one trader session
"""
from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..agents.personas import BY_ID
from ..config import Settings, get_settings
from ..floor import Floor
from ..market.sim import snapshot
from ..metrics import benchmark_curve, floor_report
from ..tracing import STORE


class Autopilot(BaseModel):
    enabled: bool
    interval_s: float = Field(default=20, ge=2, le=3600)


def _web_dist() -> Path | None:
    for p in (os.environ.get("AGORA_WEB_DIST"), Path(__file__).resolve().parents[3] / "web" / "dist",
              Path(__file__).resolve().parent / "web"):
        if p and Path(p, "index.html").exists():
            return Path(p)
    return None


def create_app(settings: Settings | None = None, floor: Floor | None = None) -> FastAPI:
    s = settings or get_settings()
    state: dict = {"floor": floor, "auto": {"enabled": False, "interval_s": 20.0}, "task": None, "running": False}

    async def autopilot_loop():
        while state["auto"]["enabled"]:
            await run_cycle_safely()
            await asyncio.sleep(state["auto"]["interval_s"])

    async def run_cycle_safely():
        if state["running"]:
            return None
        state["running"] = True
        try:
            return await state["floor"].run_cycle()
        finally:
            state["running"] = False

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if state["floor"] is None:
            state["floor"] = Floor(s)
        state["floor"].setup()
        yield
        state["auto"]["enabled"] = False
        if state["task"]:
            state["task"].cancel()

    app = FastAPI(title="Agora", version="1.0.0", lifespan=lifespan)

    def f() -> Floor:
        return state["floor"]

    def trading_day(day: int) -> int:
        return day - int(f().db.get_meta("start_day", 0))

    @app.get("/api/floor")
    def floor_view():
        fl = f()
        traders = []
        for t in fl.traders:
            rep = fl.ledger.report(t.id)
            last = fl.db.one("SELECT data FROM events WHERE trader_id=? AND kind='session_end' ORDER BY id DESC LIMIT 1",
                             (t.id,))
            traders.append({"id": t.id, "name": t.name, "strategy": t.strategy, "style": t.style,
                            "equity": rep["equity"], "cash": rep["cash"], "return_pct": rep["return_pct"],
                            "positions": len(rep["positions"]),
                            "last_session": json.loads(last["data"]) if last else None})
        bench = benchmark_curve(fl.db)
        return {"day": trading_day(fl.db.today()), "running": state["running"], "autopilot": state["auto"],
                "brain": fl.s.brain, "model": fl.s.llm_model if fl.s.brain == "llm" else "rule-based policies",
                "transport": fl.s.transport, "traders": traders,
                "benchmark_return": round(bench[-1]["equity"] / 100_000 - 1, 4) if bench else 0.0,
                "limits": {"max_position_pct": fl.policy.max_position_pct, "max_order_pct": fl.policy.max_order_pct,
                           "max_trades_per_cycle": fl.policy.max_trades_per_cycle}}

    @app.get("/api/equity")
    def equity():
        fl = f()
        curves = {t.id: [{"day": trading_day(p["day"]), "equity": p["equity"]} for p in fl.ledger.equity_curve(t.id)]
                  for t in fl.traders}
        bench = [{"day": trading_day(p["day"]), "equity": p["equity"]} for p in benchmark_curve(fl.db)]
        return {"traders": curves, "benchmark": bench}

    @app.get("/api/traders/{tid}")
    def trader(tid: str):
        fl = f()
        if tid not in {t.id for t in fl.traders}:
            raise HTTPException(404, f"no trader {tid!r}")
        rep = fl.ledger.report(tid)
        rep["style"] = BY_ID[tid].style if tid in BY_ID else ""
        tx = fl.ledger.transactions(tid, 60)
        for x in tx:
            x["trading_day"] = trading_day(x["day"])
        notes = [dict(r) for r in fl.db.q("SELECT key, value, day FROM memory WHERE trader_id=? ORDER BY ts DESC", (tid,))]
        sessions = [json.loads(r["data"]) for r in fl.db.q(
            "SELECT data FROM events WHERE trader_id=? AND kind='session_end' ORDER BY id DESC LIMIT 10", (tid,))]
        for x in sessions:
            x["trading_day"] = trading_day(x["day"])
        return {**rep, "transactions": tx, "notes": notes, "sessions": sessions}

    @app.get("/api/market")
    def market():
        return {"day": trading_day(f().db.today()), "securities": snapshot(f().db)}

    @app.get("/api/news")
    def news(limit: int = 20):
        rows = f().db.q("SELECT day, symbol, headline, sentiment FROM news ORDER BY id DESC LIMIT ?", (min(limit, 100),))
        return [{**dict(r), "trading_day": trading_day(r["day"])} for r in rows]

    @app.get("/api/metrics")
    def metrics():
        return floor_report(f().db)

    def _events(after: int, limit: int, trader_id: str | None = None) -> list[dict]:
        sql = "SELECT * FROM events WHERE id>?" + (" AND trader_id=?" if trader_id else "") + " ORDER BY id LIMIT ?"
        args = (after, trader_id, limit) if trader_id else (after, limit)
        out = []
        for r in f().db.q(sql, args):
            out.append({"id": r["id"], "trading_day": trading_day(r["day"]), "trader_id": r["trader_id"],
                        "kind": r["kind"], "data": json.loads(r["data"]), "ts": r["ts"]})
        return out

    @app.get("/api/events")
    def events(after: int = 0, limit: int = 200, trader_id: str | None = None, latest: bool = False):
        if latest:  # the most recent `limit` events, oldest first
            last = f().db.one("SELECT MAX(id) m FROM events")["m"] or 0
            after = max(0, last - limit)
        return _events(after, min(limit, 1000), trader_id)

    @app.get("/api/stream")
    async def stream(request: Request, after: int | None = None):
        start = after if after is not None else (f().db.one("SELECT MAX(id) m FROM events")["m"] or 0)

        async def gen():
            cursor = start
            yield "retry: 2000\n\n"
            while not await request.is_disconnected():
                batch = _events(cursor, 200)
                for ev in batch:
                    cursor = ev["id"]
                    yield f"id: {ev['id']}\nevent: {ev['kind']}\ndata: {json.dumps(ev, default=str)}\n\n"
                await asyncio.sleep(0.4 if batch else 0.8)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/cycle", status_code=202)
    async def cycle():
        if state["running"]:
            raise HTTPException(409, "a trading day is already in progress")
        asyncio.create_task(run_cycle_safely())
        return {"started": True, "day": trading_day(f().db.today()) + 1}

    @app.post("/api/autopilot")
    async def autopilot(body: Autopilot):
        state["auto"] = body.model_dump()
        if body.enabled and (state["task"] is None or state["task"].done()):
            state["task"] = asyncio.create_task(autopilot_loop())
        return state["auto"]

    @app.get("/api/traces/{trace_id}")
    def trace(trace_id: str):
        t = STORE.get(trace_id)
        if not t:
            raise HTTPException(404, "trace not found (traces are kept in memory for recent sessions)")
        return t.to_dict()

    @app.get("/api/health")
    def health():
        return {"status": "ok", "day": trading_day(f().db.today())}

    dist = _web_dist()
    if dist:
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            target = dist / path
            return FileResponse(target if path and target.is_file() else dist / "index.html")

    return app
