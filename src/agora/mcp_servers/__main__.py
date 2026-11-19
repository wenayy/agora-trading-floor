"""Run one floor MCP server over stdio, e.g. for Claude Desktop, Cursor or the stdio transport:

    python -m agora.mcp_servers market   --db .agora/floor.db
    python -m agora.mcp_servers accounts --db .agora/floor.db --trader kite
"""
from __future__ import annotations

import argparse

from ..config import get_settings
from ..db import DB
from ..ledger import RiskPolicy
from .servers import accounts_server, market_server, memory_server, news_server


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m agora.mcp_servers")
    p.add_argument("server", choices=["accounts", "market", "news", "memory"])
    p.add_argument("--db", default=None)
    p.add_argument("--trader", default=None, help="required for accounts and memory")
    a = p.parse_args()
    s = get_settings()
    db = DB(a.db or s.db_path)
    if a.server in ("accounts", "memory") and not a.trader:
        p.error(f"--trader is required for the {a.server} server")
    if a.server == "accounts" and not db.one("SELECT 1 FROM accounts WHERE trader_id=?", (a.trader,)):
        from ..ledger import Ledger

        Ledger(db).open_account(a.trader, a.trader.title(), "human", s.starting_cash)  # e.g. a guest trading via Claude
    server = {
        "accounts": lambda: accounts_server(db, a.trader, RiskPolicy.from_settings(s)),
        "market": lambda: market_server(db),
        "news": lambda: news_server(db),
        "memory": lambda: memory_server(db, a.trader),
    }[a.server]()
    server.run("stdio")


if __name__ == "__main__":
    main()
