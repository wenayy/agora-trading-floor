"""Performance and risk metrics for each trader, computed from daily equity snapshots.

  total return          E_end / E_start - 1
  annualised volatility std(daily returns) * sqrt(252)
  Sharpe ratio          mean(daily) / std(daily) * sqrt(252)   (risk-free rate taken as 0)
  max drawdown          worst peak-to-trough fall of the equity curve
  turnover              traded notional / average equity
  benchmark             equal-weight buy-and-hold of the whole universe from the first trading day
"""
from __future__ import annotations

import math
import statistics

from .db import DB


def _daily_returns(values: list[float]) -> list[float]:
    return [values[i] / values[i - 1] - 1 for i in range(1, len(values)) if values[i - 1]]


def max_drawdown(values: list[float]) -> float:
    peak, worst = values[0] if values else 0, 0.0
    for v in values:
        peak = max(peak, v)
        worst = min(worst, v / peak - 1 if peak else 0)
    return worst


def summarize(values: list[float]) -> dict:
    rets = _daily_returns(values)
    sd = statistics.stdev(rets) if len(rets) > 1 else 0.0
    mean = statistics.fmean(rets) if rets else 0.0
    return {
        "total_return": round(values[-1] / values[0] - 1, 4) if len(values) > 1 else 0.0,
        "ann_vol": round(sd * math.sqrt(252), 4),
        "sharpe": round(mean / sd * math.sqrt(252), 2) if sd else 0.0,
        "max_drawdown": round(max_drawdown(values), 4),
        "best_day": round(max(rets), 4) if rets else 0.0,
        "worst_day": round(min(rets), 4) if rets else 0.0,
        "days": len(rets),
    }


def benchmark_curve(db: DB) -> list[dict]:
    start = int(db.get_meta("start_day", 0))
    rows = db.q("SELECT symbol, day, price FROM prices WHERE day>=? ORDER BY day", (start,))
    by_day: dict[int, dict[str, float]] = {}
    for r in rows:
        by_day.setdefault(r["day"], {})[r["symbol"]] = r["price"]
    if start not in by_day:
        return []
    base = by_day[start]
    return [{"day": d, "equity": round(100_000 * statistics.fmean(px[s] / base[s] for s in base), 2)}
            for d, px in sorted(by_day.items())]


def floor_report(db: DB) -> dict:
    out = {}
    for a in db.q("SELECT trader_id, name, strategy, starting_cash FROM accounts ORDER BY trader_id"):
        tid = a["trader_id"]
        curve = [r["equity"] for r in db.q("SELECT equity FROM snapshots WHERE trader_id=? ORDER BY day", (tid,))]
        tx = db.one("SELECT COUNT(*) n, COALESCE(SUM(qty*price),0) notional, COALESCE(SUM(fee),0) fees "
                    "FROM transactions WHERE trader_id=?", (tid,))
        m = summarize(curve) if curve else {}
        avg_eq = statistics.fmean(curve) if curve else 1
        m.update({"name": a["name"], "strategy": a["strategy"], "trades": tx["n"],
                  "fees": round(tx["fees"], 2), "turnover": round(tx["notional"] / avg_eq, 2)})
        out[tid] = m
    bench = [x["equity"] for x in benchmark_curve(db)]
    if bench:
        out["benchmark"] = {**summarize(bench), "name": "Equal-weight universe", "strategy": "buy and hold",
                            "trades": 0, "fees": 0.0, "turnover": 1.0}
    return out


def report_markdown(rep: dict) -> str:
    head = ("| Trader | Strategy | Return | Ann. vol | Sharpe | Max drawdown | Trades | Turnover | Fees |\n"
            "|---|---|---|---|---|---|---|---|---|")
    rows = []
    for _tid, m in sorted(rep.items(), key=lambda kv: (kv[0] == "benchmark", -kv[1].get("sharpe", 0))):
        rows.append(f"| {m['name']} | {m['strategy']} | {m['total_return']:+.1%} | {m['ann_vol']:.1%} | "
                    f"{m['sharpe']:.2f} | {m['max_drawdown']:.1%} | {m['trades']} | {m['turnover']:.1f}x | "
                    f"{m['fees']:,.0f} |")
    return "\n".join([head, *rows])
