"""Accounts, order execution and the risk engine.

Risk is enforced *here*, in the accounts service, never by asking the model nicely. An agent can
only request an order; the ledger decides. A rejected order comes back to the agent as a
structured error it can reason about ("order is 31% of equity, limit is 25%"), so the agent
can resize and retry. This is the single most important design choice in the system.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from .db import DB


class RiskViolation(Exception):
    """An order was rejected by the risk policy. The message is shown to the agent."""


@dataclass(frozen=True)
class RiskPolicy:
    max_position_pct: float = 0.30   # no single holding above 30% of equity after the trade
    max_order_pct: float = 0.25      # no single order above 25% of equity
    max_trades_per_cycle: int = 6
    commission_bps: float = 5.0
    spread_bps: float = 10.0         # buys fill half a spread above mid, sells half below
    min_rationale_chars: int = 15    # every trade must say why (audit trail)

    @classmethod
    def from_settings(cls, s) -> RiskPolicy:
        return cls(s.max_position_pct, s.max_order_pct, s.max_trades_per_cycle, s.commission_bps, s.spread_bps,
                   s.min_rationale_chars)


class Ledger:
    def __init__(self, db: DB, policy: RiskPolicy | None = None):
        self.db, self.policy = db, policy or RiskPolicy()

    # ------------------------------------------------------------------ setup
    def open_account(self, trader_id: str, name: str, strategy: str, cash: float) -> None:
        self.db.x("INSERT OR IGNORE INTO accounts VALUES (?,?,?,?,?)", (trader_id, name, strategy, cash, cash))

    # ------------------------------------------------------------------ reads
    def price(self, symbol: str) -> float:
        row = self.db.one("SELECT price FROM prices WHERE symbol=? ORDER BY day DESC LIMIT 1", (symbol,))
        if not row:
            raise RiskViolation(f"unknown symbol {symbol!r}")
        return float(row["price"])

    def cash(self, trader_id: str) -> float:
        row = self.db.one("SELECT cash FROM accounts WHERE trader_id=?", (trader_id,))
        if not row:
            raise RiskViolation(f"unknown account {trader_id!r}")
        return float(row["cash"])

    def holdings(self, trader_id: str) -> dict[str, dict]:
        return {r["symbol"]: {"qty": r["qty"], "avg_cost": r["avg_cost"]}
                for r in self.db.q("SELECT symbol, qty, avg_cost FROM holdings WHERE trader_id=? AND qty>0", (trader_id,))}

    def equity(self, trader_id: str) -> float:
        return self.cash(trader_id) + sum(h["qty"] * self.price(s) for s, h in self.holdings(trader_id).items())

    def report(self, trader_id: str) -> dict:
        acct = self.db.one("SELECT * FROM accounts WHERE trader_id=?", (trader_id,))
        hold = self.holdings(trader_id)
        eq = self.equity(trader_id)
        positions = []
        for s, h in sorted(hold.items()):
            px = self.price(s)
            positions.append({"symbol": s, "qty": h["qty"], "avg_cost": round(h["avg_cost"], 2), "price": px,
                              "market_value": round(h["qty"] * px, 2),
                              "weight": round(h["qty"] * px / eq, 4) if eq else 0,
                              "unrealized_pnl": round(h["qty"] * (px - h["avg_cost"]), 2)})
        return {"trader_id": trader_id, "name": acct["name"], "strategy": acct["strategy"],
                "cash": round(acct["cash"], 2), "equity": round(eq, 2), "starting_cash": acct["starting_cash"],
                "return_pct": round(eq / acct["starting_cash"] - 1, 4), "positions": positions,
                "trades_left_this_cycle": self.policy.max_trades_per_cycle - self._trades_today(trader_id),
                "limits": {"max_position_pct": self.policy.max_position_pct, "max_order_pct": self.policy.max_order_pct}}

    def transactions(self, trader_id: str | None = None, limit: int = 50) -> list[dict]:
        sql = "SELECT * FROM transactions " + ("WHERE trader_id=? " if trader_id else "") + "ORDER BY id DESC LIMIT ?"
        args = (trader_id, limit) if trader_id else (limit,)
        return [dict(r) for r in self.db.q(sql, args)]

    def _trades_today(self, trader_id: str) -> int:
        return self.db.one("SELECT COUNT(*) c FROM transactions WHERE trader_id=? AND day=?",
                           (trader_id, self.db.today()))["c"]

    # ------------------------------------------------------------------ orders
    def _check_common(self, trader_id: str, symbol: str, qty: int, rationale: str) -> float:
        p = self.policy
        if not isinstance(qty, int) or qty <= 0:
            raise RiskViolation("quantity must be a positive whole number of shares")
        if len((rationale or "").strip()) < p.min_rationale_chars:
            raise RiskViolation(f"a rationale of at least {p.min_rationale_chars} characters is required for every trade")
        if self._trades_today(trader_id) >= p.max_trades_per_cycle:
            raise RiskViolation(f"trade limit reached: at most {p.max_trades_per_cycle} trades per cycle")
        return self.price(symbol)

    def buy(self, trader_id: str, symbol: str, qty: int, rationale: str) -> dict:
        symbol = symbol.upper()
        mid = self._check_common(trader_id, symbol, qty, rationale)
        px = round(mid * (1 + self.policy.spread_bps / 20_000), 4)
        notional = qty * px
        fee = round(max(1.0, notional * self.policy.commission_bps / 10_000), 2)
        with self.db.tx():
            cash = self.cash(trader_id)
            eq = self.equity(trader_id)
            if notional + fee > cash:
                raise RiskViolation(f"insufficient cash: order needs {notional + fee:,.2f}, available {cash:,.2f}")
            if notional > self.policy.max_order_pct * eq:
                raise RiskViolation(f"order is {notional / eq:.1%} of equity; the limit is {self.policy.max_order_pct:.0%}. "
                                    f"Max size now is {int(self.policy.max_order_pct * eq // px)} shares")
            held = self.holdings(trader_id).get(symbol, {"qty": 0, "avg_cost": 0.0})
            new_qty = held["qty"] + qty
            if new_qty * mid > self.policy.max_position_pct * eq:
                room = max(0, int((self.policy.max_position_pct * eq - held["qty"] * mid) // px))
                raise RiskViolation(f"position would be {new_qty * mid / eq:.1%} of equity; the limit is "
                                    f"{self.policy.max_position_pct:.0%}. You can buy at most {room} more shares")
            avg = (held["qty"] * held["avg_cost"] + notional) / new_qty
            self.db.conn.execute("UPDATE accounts SET cash = cash - ? WHERE trader_id=?", (notional + fee, trader_id))
            self.db.conn.execute("INSERT OR REPLACE INTO holdings VALUES (?,?,?,?)", (trader_id, symbol, new_qty, avg))
            tid = self.db.conn.execute(
                "INSERT INTO transactions (trader_id, day, side, symbol, qty, price, fee, rationale, ts) VALUES (?,?,?,?,?,?,?,?,?)",
                (trader_id, self.db.today(), "BUY", symbol, qty, px, fee, rationale.strip(), time.time())).lastrowid
        return {"id": tid, "side": "BUY", "symbol": symbol, "qty": qty, "price": px, "fee": fee,
                "cash_after": round(self.cash(trader_id), 2)}

    def sell(self, trader_id: str, symbol: str, qty: int, rationale: str) -> dict:
        symbol = symbol.upper()
        mid = self._check_common(trader_id, symbol, qty, rationale)
        px = round(mid * (1 - self.policy.spread_bps / 20_000), 4)
        with self.db.tx():
            held = self.holdings(trader_id).get(symbol)
            if not held or held["qty"] < qty:
                have = held["qty"] if held else 0
                raise RiskViolation(f"cannot sell {qty} {symbol}: you hold {have} (short selling is not allowed)")
            notional = qty * px
            fee = round(max(1.0, notional * self.policy.commission_bps / 10_000), 2)
            self.db.conn.execute("UPDATE accounts SET cash = cash + ? WHERE trader_id=?", (notional - fee, trader_id))
            self.db.conn.execute("UPDATE holdings SET qty = qty - ? WHERE trader_id=? AND symbol=?", (qty, trader_id, symbol))
            tid = self.db.conn.execute(
                "INSERT INTO transactions (trader_id, day, side, symbol, qty, price, fee, rationale, ts) VALUES (?,?,?,?,?,?,?,?,?)",
                (trader_id, self.db.today(), "SELL", symbol, qty, px, fee, rationale.strip(), time.time())).lastrowid
        realized = round(qty * (px - held["avg_cost"]) - fee, 2)
        return {"id": tid, "side": "SELL", "symbol": symbol, "qty": qty, "price": px, "fee": fee,
                "realized_pnl": realized, "cash_after": round(self.cash(trader_id), 2)}

    # ------------------------------------------------------------------ snapshots
    def snapshot_all(self) -> None:
        day = self.db.today()
        for r in self.db.q("SELECT trader_id FROM accounts"):
            tid = r["trader_id"]
            self.db.x("INSERT OR REPLACE INTO snapshots VALUES (?,?,?,?)", (tid, day, self.cash(tid), self.equity(tid)))

    def equity_curve(self, trader_id: str) -> list[dict]:
        return [{"day": r["day"], "equity": round(r["equity"], 2)}
                for r in self.db.q("SELECT day, equity FROM snapshots WHERE trader_id=? ORDER BY day", (trader_id,))]
