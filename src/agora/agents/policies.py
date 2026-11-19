"""Deterministic rule-based brains.

They implement each trader's strategy as code but act *exactly* like an LLM would: they read tool
results from the conversation and emit tool calls (account, recall, snapshot, research, orders,
remember, summary). That makes the whole floor runnable offline, testable in CI and
backtestable, and gives a baseline that LLM traders have to beat.
"""
from __future__ import annotations

import json
import re

from .brains import ToolCall, Turn

DRIFT_BAND = 0.04  # only trade when a weight is more than 4 points from target


def _tool_results(messages: list[dict]) -> dict[str, list[str]]:
    """Map tool name -> list of result strings, by pairing tool messages with their calls."""
    names: dict[str, str] = {}
    out: dict[str, list[str]] = {}
    for m in messages:
        for tc in m.get("tool_calls", []) or []:
            names[tc["id"]] = tc["function"]["name"]
        if m["role"] == "tool":
            out.setdefault(names.get(m["tool_call_id"], "?"), []).append(m["content"])
    return out


def _call_results(messages: list[dict]) -> list[tuple[str, dict, str]]:
    """(tool name, arguments, result text) for every completed call, in order."""
    pending: dict[str, tuple[str, dict]] = {}
    out = []
    for m in messages:
        for tc in m.get("tool_calls", []) or []:
            pending[tc["id"]] = (tc["function"]["name"], json.loads(tc["function"]["arguments"]))
        if m["role"] == "tool" and m["tool_call_id"] in pending:
            name, args = pending.pop(m["tool_call_id"])
            out.append((name, args, m["content"]))
    return out


def _calls_made(messages: list[dict]) -> list[tuple[str, dict]]:
    return [(tc["function"]["name"], json.loads(tc["function"]["arguments"]))
            for m in messages for tc in (m.get("tool_calls") or [])]


def target_weights(strategy: str, snap: list[dict], holdings: dict[str, float]) -> dict[str, float]:
    ok = [s for s in snap if s["ret_20d"] is not None]
    if strategy == "momentum":
        top = sorted((s for s in ok if s["ret_20d"] > 0), key=lambda s: -s["ret_20d"])[:3]
        return {s["symbol"]: 0.28 for s in top}
    if strategy == "value":
        cheap = sorted((s for s in ok if s["dividend_yield"] > 0), key=lambda s: s["pe"])[:4]
        return {s["symbol"]: 0.22 for s in cheap}
    if strategy == "macro":
        sectors: dict[str, list[dict]] = {}
        for s in ok:
            sectors.setdefault(s["sector"], []).append(s)
        best = max(sectors, key=lambda k: sum(x["ret_20d"] for x in sectors[k]) / len(sectors[k]))
        picks = sorted(sectors[best], key=lambda s: -s["ret_20d"])[:3]
        w = {s["symbol"]: 0.75 / len(picks) for s in picks} if best != "Consumer staples" else {}
        staples = sorted(sectors.get("Consumer staples", []), key=lambda s: s["vol_20d"] or 1)[:1]
        w.update({s["symbol"]: 0.2 if best != "Consumer staples" else 0.45 for s in staples})
        return {k: min(v, 0.28) for k, v in w.items()}
    if strategy == "contrarian":
        def clean(s):  # no fresh bad news behind the sell-off
            n = s["latest_news"]
            return not (n and n["sentiment"] < 0 and n["days_ago"] <= 5)
        losers = sorted((s for s in ok if s["ret_5d"] is not None and s["ret_5d"] < -0.03 and clean(s)),
                        key=lambda s: s["ret_5d"])[:3]
        keep = {sym: 0.2 for sym in holdings if any(x["symbol"] == sym and (x["ret_5d"] or 0) < 0.04 for x in ok)}
        return {**keep, **{s["symbol"]: 0.2 for s in losers}}
    raise ValueError(f"unknown strategy {strategy}")


def _rationale(strategy: str, side: str, s: dict, target: float) -> str:
    r20, r5 = (s["ret_20d"] or 0) * 100, (s["ret_5d"] or 0) * 100
    if side == "SELL" and target == 0:
        why = {"momentum": f"trend faded: 20d return {r20:+.1f}% is no longer top-3",
               "value": f"no longer among the cheapest dividend payers (P/E {s['pe']:.0f})",
               "macro": f"{s['sector']} is no longer the leading sector",
               "contrarian": f"rebound captured or thesis broken (5d {r5:+.1f}%)"}[strategy]
    elif side == "SELL":
        why = f"trimming back toward a {target:.0%} target weight"
    else:
        why = {"momentum": f"top-3 20d trend at {r20:+.1f}% with 20d vol {(s['vol_20d'] or 0) * 100:.0f}%",
               "value": f"P/E {s['pe']:.0f} and {s['dividend_yield'] * 100:.1f}% dividend yield, among the cheapest",
               "macro": (f"defensive sleeve: lowest-volatility staple at {(s['vol_20d'] or 0) * 100:.0f}% annualised vol"
                         if s["sector"] == "Consumer staples" else
                         f"{s['sector']} leads on 20d returns; {s['symbol']} {r20:+.1f}%"),
               "contrarian": f"5d sell-off of {r5:+.1f}% without negative news, expecting mean reversion"}[strategy]
    return f"{side.title()} {s['symbol']}: {why}."


class PolicyBrain:
    """Plays one trader's strategy through the MCP tools."""

    def __init__(self, strategy: str):
        self.strategy = strategy
        self.model = f"policy:{strategy}"

    def respond(self, messages: list[dict], tools: list[dict]) -> Turn:
        if not tools:  # asked for a summary after the turn budget
            return Turn("Session ended at the turn budget.")
        res = _tool_results(messages)
        made = _calls_made(messages)
        names = [n for n, _ in made]
        if "get_account" not in names:
            return Turn("Checking the book, my notes and the tape.",
                        [ToolCall("get_account", {}), ToolCall("recall", {}), ToolCall("get_market_snapshot", {})])
        acct = json.loads(res["get_account"][-1])
        snap = json.loads(res["get_market_snapshot"][-1])["securities"]
        by_sym = {s["symbol"]: s for s in snap}
        equity = acct["equity"]
        weights = {p["symbol"]: p["weight"] for p in acct["positions"]}
        qty_held = {p["symbol"]: p["qty"] for p in acct["positions"]}
        targets = target_weights(self.strategy, snap, weights)

        orders: list[ToolCall] = []
        for sym, w in sorted(weights.items()):  # sells first
            tgt = targets.get(sym, 0.0)
            if w - tgt > DRIFT_BAND or (tgt == 0 and w > 0):
                qty = qty_held[sym] if tgt == 0 else int((w - tgt) * equity // by_sym[sym]["price"])
                if qty > 0:
                    orders.append(ToolCall("sell_shares", {"symbol": sym, "quantity": qty,
                                                           "rationale": _rationale(self.strategy, "SELL", by_sym[sym], tgt)}))
        buys = []
        for sym, tgt in sorted(targets.items(), key=lambda x: -x[1]):
            gap = tgt - weights.get(sym, 0.0)
            if gap > DRIFT_BAND:
                qty = int(min(gap, 0.24) * equity // (by_sym[sym]["price"] * 1.002))
                if qty > 0:
                    buys.append(ToolCall("buy_shares", {"symbol": sym, "quantity": qty,
                                                        "rationale": _rationale(self.strategy, "BUY", by_sym[sym], tgt)}))
        new_names = [b.arguments["symbol"] for b in buys if b.arguments["symbol"] not in weights]

        # Agent-as-tool: consult the researcher once about the first new name before buying it.
        if new_names and "research" not in names:
            return Turn(f"Considering a new position in {new_names[0]}; asking research first.",
                        [ToolCall("research", {"question": f"What is the recent news and price action for {new_names[0]}?"})])
        if "research" in names and buys and "buy_shares" not in names and "sell_shares" not in names:
            brief = res.get("research", [""])[-1]
            if "Signal: negative" in brief:  # the analyst vetoed the new idea
                buys = [b for b in buys if b.arguments["symbol"] != new_names[0]]

        # Retry rejected orders once, resized to what the broker said is allowed.
        if res.get("buy_shares") or res.get("sell_shares"):
            retries = []
            for name, args, text in (c for c in _call_results(messages) if c[0] in ("buy_shares", "sell_shares")):
                m = re.search(r"(?:at most|Max size now is) (\d+)", text)
                already = sum(1 for n, a in made if n == name and a["symbol"] == args["symbol"])
                if "Order rejected" in text and m and int(m.group(1)) > 0 and already == 1:
                    retries.append(ToolCall(name, {**args, "quantity": int(m.group(1)),
                                                   "rationale": args["rationale"] + " Resized to the risk limit."}))
            if retries:
                return Turn("Resizing orders the broker rejected.", retries)
            if "remember" not in names:
                return Turn(None, [ToolCall("remember", {"key": "last_session", "value": self._note(targets)})])
            return Turn(self._summary(made, targets))

        if orders or buys:
            return Turn(f"Rebalancing toward {len(targets)} target positions.", orders + buys)
        if "remember" not in names:
            return Turn(None, [ToolCall("remember", {"key": "last_session", "value": self._note(targets)})])
        return Turn(self._summary(made, targets))

    def _note(self, targets: dict[str, float]) -> str:
        return f"{self.strategy} targets: " + (", ".join(f"{k} {v:.0%}" for k, v in targets.items()) or "all cash")

    def _summary(self, made: list[tuple[str, dict]], targets: dict[str, float]) -> str:
        trades = [f"{n.split('_')[0]} {a['quantity']} {a['symbol']}" for n, a in made if n in ("buy_shares", "sell_shares")]
        if not trades:
            return f"No trades: the book already matches my {self.strategy} targets."
        n = len(trades)
        return f"Executed {n} order{'s' if n != 1 else ''} ({'; '.join(trades)}) to move toward {self._note(targets)}."


class PolicyResearcher:
    """Offline research analyst: pulls the company's news and stats, then writes a brief with a signal."""

    model = "policy:researcher"

    def respond(self, messages: list[dict], tools: list[dict]) -> Turn:
        res = _tool_results(messages)
        q = messages[1]["content"]
        m = re.search(r"\b([A-Z]{4})\b", q)
        sym = m.group(1) if m else "HLXF"
        if "search_news" not in res:
            return Turn(None, [ToolCall("search_news", {"symbol": sym, "days": 20}),
                               ToolCall("get_price_history", {"symbol": sym, "days": 21})])
        news = json.loads(res["search_news"][-1]) if res["search_news"][-1].startswith("[") else []
        hist = json.loads(res["get_price_history"][-1]) if res.get("get_price_history") else []
        r20 = (hist[-1]["price"] / hist[0]["price"] - 1) if len(hist) > 1 else 0.0
        score = sum(n["sentiment"] for n in news)
        lines = [f"- {sym} moved {r20:+.1%} over the last {len(hist)} sessions."]
        lines += [f"- Day {n['day']}: {n['headline']} ({'positive' if n['sentiment'] > 0 else 'negative'})" for n in news[:3]]
        if not news:
            lines.append("- No company-specific headlines in the last 20 sessions.")
        signal = "positive" if score > 0 else "negative" if score < 0 else "mixed"
        return Turn("\n".join(lines) + f"\nSignal: {signal}")
