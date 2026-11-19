"""The MCP servers that make up the trading floor.

  accounts  (per trader)  get_account, buy_shares, sell_shares, get_transactions  + resource account://report
  market    (shared)      get_market_snapshot, get_price, get_price_history, list_securities
  news      (shared)      search_news, get_company_profile
  memory    (per trader)  remember, recall, forget

Identity is bound when a server is *built*: the accounts server for trader "kite" can only ever
touch Kite's account, because no tool takes a trader id. A prompt-injected or confused agent
cannot trade on someone else's book. Least privilege by construction, not by instruction.

Every server can run in-process (memory transport) or as its own process over stdio:
    python -m agora.mcp_servers accounts --db .agora/floor.db --trader kite
"""
from __future__ import annotations

import json
import time

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from ..db import DB
from ..ledger import Ledger, RiskPolicy, RiskViolation
from ..market import sim


def accounts_server(db: DB, trader_id: str, policy: RiskPolicy | None = None) -> FastMCP:
    led = Ledger(db, policy)
    mcp = FastMCP(f"agora-accounts-{trader_id}",
                  instructions="Your brokerage account. Orders are checked against hard risk limits; a rejected order "
                               "explains why and how much you can trade instead.")

    @mcp.tool()
    def get_account() -> dict:
        """Your cash, equity, return since inception, positions with weights and P&L, and remaining trade budget."""
        return led.report(trader_id)

    @mcp.tool()
    def buy_shares(symbol: str, quantity: int, rationale: str) -> dict:
        """Buy whole shares at the current price plus half the spread. A rationale (why, in one or two sentences)
        is required and is recorded on the trade ticket."""
        try:
            return led.buy(trader_id, symbol, quantity, rationale)
        except RiskViolation as e:
            raise ToolError(f"Order rejected: {e}") from e

    @mcp.tool()
    def sell_shares(symbol: str, quantity: int, rationale: str) -> dict:
        """Sell whole shares you hold. Short selling is not allowed. A rationale is required."""
        try:
            return led.sell(trader_id, symbol, quantity, rationale)
        except RiskViolation as e:
            raise ToolError(f"Order rejected: {e}") from e

    @mcp.tool()
    def get_transactions(limit: int = 20) -> list[dict]:
        """Your most recent trades, newest first."""
        return led.transactions(trader_id, min(limit, 100))

    @mcp.resource("account://report")
    def account_report() -> str:
        """Full account report as JSON."""
        return json.dumps(led.report(trader_id))

    return mcp


def market_server(db: DB) -> FastMCP:
    mcp = FastMCP("agora-market", instructions="End-of-day prices and statistics for every listed security.")

    @mcp.tool()
    def get_market_snapshot() -> dict:
        """Every security with price, 1/5/20-day returns, annualised 20-day volatility, P/E, dividend yield and the
        latest headline. Start here."""
        return {"day": db.today(), "securities": sim.snapshot(db)}

    @mcp.tool()
    def get_price(symbol: str) -> dict:
        """Latest price for one symbol."""
        row = db.one("SELECT price, day FROM prices WHERE symbol=? ORDER BY day DESC LIMIT 1", (symbol.upper(),))
        if not row:
            raise ToolError(f"unknown symbol {symbol!r}")
        return {"symbol": symbol.upper(), "price": row["price"], "day": row["day"]}

    @mcp.tool()
    def get_price_history(symbol: str, days: int = 30) -> list[dict]:
        """Daily closing prices for the last N days (max 250)."""
        return [{"day": d, "price": p} for d, p in sim.history(db, symbol.upper(), min(days, 250))]

    @mcp.tool()
    def list_securities() -> list[dict]:
        """Symbols, names, sectors and one-line descriptions."""
        return [dict(r) for r in db.q("SELECT symbol, name, sector, description FROM securities ORDER BY symbol")]

    return mcp


def news_server(db: DB) -> FastMCP:
    mcp = FastMCP("agora-news", instructions="Company news wire and company profiles.")

    @mcp.tool()
    def search_news(symbol: str | None = None, days: int = 10, limit: int = 10) -> list[dict]:
        """Recent headlines, optionally for one symbol. sentiment is +1 (positive) or -1 (negative)."""
        since = db.today() - days
        if symbol:
            rows = db.q("SELECT day, symbol, headline, body, sentiment FROM news WHERE symbol=? AND day>? "
                        "ORDER BY id DESC LIMIT ?", (symbol.upper(), since, min(limit, 50)))
        else:
            rows = db.q("SELECT day, symbol, headline, body, sentiment FROM news WHERE day>? ORDER BY id DESC LIMIT ?",
                        (since, min(limit, 50)))
        return [dict(r) for r in rows]

    @mcp.tool()
    def get_company_profile(symbol: str) -> dict:
        """Sector, valuation and business description for one company."""
        row = db.one("SELECT * FROM securities WHERE symbol=?", (symbol.upper(),))
        if not row:
            raise ToolError(f"unknown symbol {symbol!r}")
        return dict(row)

    return mcp


def memory_server(db: DB, trader_id: str) -> FastMCP:
    mcp = FastMCP(f"agora-memory-{trader_id}",
                  instructions="Your private long-term notebook. It persists between trading sessions.")

    @mcp.tool()
    def remember(key: str, value: str) -> str:
        """Save or overwrite a note, e.g. key='thesis:SALT'. Keep notes short and specific."""
        db.x("INSERT OR REPLACE INTO memory VALUES (?,?,?,?,?)", (trader_id, key[:80], value[:2000], db.today(), time.time()))
        return f"saved {key}"

    @mcp.tool()
    def recall(key: str | None = None) -> list[dict]:
        """Read one note by key, or all notes (newest first) if no key is given."""
        if key:
            rows = db.q("SELECT key, value, day FROM memory WHERE trader_id=? AND key=?", (trader_id, key))
        else:
            rows = db.q("SELECT key, value, day FROM memory WHERE trader_id=? ORDER BY ts DESC LIMIT 30", (trader_id,))
        return [dict(r) for r in rows]

    @mcp.tool()
    def forget(key: str) -> str:
        """Delete a note."""
        db.x("DELETE FROM memory WHERE trader_id=? AND key=?", (trader_id, key))
        return f"deleted {key}"

    return mcp
