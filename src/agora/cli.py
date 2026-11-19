"""Command line: ``agora setup | cycle | backtest | serve | mcp-config``."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .config import get_settings


def _floor(args=None):
    from .floor import Floor

    s = get_settings()
    if args is not None and getattr(args, "llm", False):
        s.brain = "llm"
    if args is not None and getattr(args, "stdio", False):
        s.transport = "stdio"
    return Floor(s)


def cmd_setup(args) -> None:
    f = _floor(args)
    f.setup()
    print(f"floor ready at {f.db.path}: {len(f.traders)} traders, day {f.db.today()}")


def cmd_cycle(args) -> None:
    f = _floor(args)
    f.setup()
    for _ in range(args.n):
        out = asyncio.run(f.run_cycle())
        board = "  ".join(f"{k} {v:,.0f}" for k, v in out["equity"].items())
        print(f"day {out['day']}: {board}")
        for tid, r in out["traders"].items():
            if args.verbose:
                print(f"  {tid}: {r.get('summary') or r.get('error')}")


def cmd_backtest(args) -> None:
    from .db import DB
    from .floor import Floor
    from .metrics import floor_report, report_markdown

    s = get_settings()
    s.seed = args.seed
    path = Path(args.out) / f"backtest_seed{args.seed}.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)
    f = Floor(s, db=DB(path))
    f.setup(warmup_days=30)

    async def go():
        for i in range(args.days):
            await f.run_cycle()
            if (i + 1) % 20 == 0:
                print(f"  day {i + 1}/{args.days}", file=sys.stderr)

    asyncio.run(go())
    rep = floor_report(f.db)
    md = report_markdown(rep)
    (Path(args.out) / f"backtest_seed{args.seed}.md").write_text(md + "\n")
    (Path(args.out) / f"backtest_seed{args.seed}.json").write_text(json.dumps(rep, indent=2))
    print(md)


def cmd_serve(args) -> None:
    import uvicorn

    uvicorn.run("agora.api.server:create_app", factory=True, host=args.host, port=args.port)


def cmd_mcp_config(args) -> None:
    """Print a Claude Desktop / Cursor style config so you can trade on the floor yourself through any MCP client."""
    s = get_settings()
    db = str(Path(s.db_path).resolve())
    servers = {"agora-market": {"command": sys.executable, "args": ["-m", "agora.mcp_servers", "market", "--db", db]},
               "agora-news": {"command": sys.executable, "args": ["-m", "agora.mcp_servers", "news", "--db", db]},
               "agora-accounts": {"command": sys.executable,
                                  "args": ["-m", "agora.mcp_servers", "accounts", "--db", db, "--trader", args.trader]},
               "agora-memory": {"command": sys.executable,
                                "args": ["-m", "agora.mcp_servers", "memory", "--db", db, "--trader", args.trader]}}
    print(json.dumps({"mcpServers": servers}, indent=2))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="agora", description="Agora: an autonomous paper-trading floor on MCP")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn, help_ in (("setup", cmd_setup, "create the floor (market history + accounts)"),
                            ("cycle", cmd_cycle, "run trading cycles")):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("--llm", action="store_true", help="use the LLM brain instead of rule-based policies")
        sp.add_argument("--stdio", action="store_true", help="run every MCP server as its own process")
        if name == "cycle":
            sp.add_argument("-n", type=int, default=1)
            sp.add_argument("-v", "--verbose", action="store_true")
        sp.set_defaults(fn=fn)
    b = sub.add_parser("backtest", help="run the rule-based traders for N days and report risk-adjusted performance")
    b.add_argument("--days", type=int, default=120)
    b.add_argument("--seed", type=int, default=7)
    b.add_argument("--out", default="results")
    b.set_defaults(fn=cmd_backtest)
    sv = sub.add_parser("serve", help="run the API and dashboard")
    sv.add_argument("--host", default="0.0.0.0")
    sv.add_argument("--port", type=int, default=8000)
    sv.set_defaults(fn=cmd_serve)
    mc = sub.add_parser("mcp-config", help="print an MCP client config to trade on the floor yourself")
    mc.add_argument("--trader", default="guest")
    mc.set_defaults(fn=cmd_mcp_config)
    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
