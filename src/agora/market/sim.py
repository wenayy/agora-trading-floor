"""A seeded, explainable market simulator.

Daily log-returns are a factor model:

    r_i,t = mu_i + beta_i * M_t + S_sector(i),t + e_i,t + news_i,t

M_t is a market shock, S a sector shock, e idiosyncratic noise, and news_i,t the impact of a
generated headline (positive or negative). The universe is fictional (it shares the Helix
Freight / Saltwave world used elsewhere in my projects), so nothing here is confused with real
market data. The same seed always produces the same market, which makes backtests reproducible.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass

from ..db import DB


@dataclass(frozen=True)
class Security:
    symbol: str
    name: str
    sector: str
    price: float
    pe: float
    dividend_yield: float
    mu: float  # daily drift
    beta: float
    vol: float  # daily idiosyncratic vol
    description: str


UNIVERSE = [
    Security("HLXF", "Helix Freight", "Logistics software", 84.0, 38.0, 0.0, 0.0006, 1.2, 0.018,
             "Route optimisation, dock scheduling and freight audit software."),
    Security("NRDH", "Nordhavn Logistics", "Logistics", 46.0, 14.0, 0.028, 0.0002, 0.9, 0.012,
             "Third-party logistics operator in Northern Europe."),
    Security("HELV", "Helvetia Parcel", "Logistics", 112.0, 19.0, 0.021, 0.0003, 0.8, 0.010,
             "Parcel delivery network across the DACH region."),
    Security("SALT", "Saltwave Cells", "Energy storage", 31.0, 55.0, 0.0, 0.0009, 1.5, 0.026,
             "Sodium-ion battery cell manufacturer with a gigafactory in Pune."),
    Security("VOLT", "Voltaris Energy", "Energy storage", 22.0, 61.0, 0.0, 0.0008, 1.6, 0.028,
             "Grid-scale sodium-ion storage systems integrator."),
    Security("KSTR", "Kestrel Grid", "Energy storage", 9.5, 120.0, 0.0, 0.0010, 1.8, 0.034,
             "Early-stage containerised storage startup, recently listed."),
    Security("KAVR", "Kaveri Foods", "Consumer staples", 58.0, 22.0, 0.019, 0.0003, 0.6, 0.009,
             "Packaged foods company in South India."),
    Security("DCAG", "Deccan Agro", "Consumer staples", 27.0, 12.0, 0.024, 0.0002, 0.7, 0.013,
             "Agricultural inputs and produce distribution."),
    Security("TJPH", "Tejas Pharma", "Healthcare", 140.0, 26.0, 0.012, 0.0004, 0.7, 0.014,
             "Generic and cold-chain biologic medicines."),
    Security("BHST", "Bharat Steel Movers", "Industrials", 64.0, 9.0, 0.035, 0.0001, 1.1, 0.016,
             "Heavy-industry logistics and steel handling."),
]
SECTOR_VOL = {"Logistics software": 0.008, "Logistics": 0.006, "Energy storage": 0.014, "Consumer staples": 0.004,
              "Healthcare": 0.006, "Industrials": 0.007}

POS_NEWS = ["{n} wins a multi-year contract with a major customer", "{n} beats quarterly earnings estimates",
            "{n} raises full-year guidance", "Analysts upgrade {n} after strong order book",
            "{n} announces share buyback programme"]
NEG_NEWS = ["{n} misses quarterly revenue estimates", "{n} cuts guidance on weaker demand",
            "Regulator opens inquiry into {n}", "{n} delays flagship plant expansion",
            "Key executive departs {n}"]


class MarketSimulator:
    def __init__(self, db: DB, seed: int = 7, news_prob: float = 0.06):
        self.db, self.seed, self.news_prob = db, seed, news_prob
        self.by_symbol = {s.symbol: s for s in UNIVERSE}

    def _rng(self, day: int) -> random.Random:
        # One RNG per (seed, day): re-running a day reproduces it exactly.
        return random.Random(self.seed * 1_000_003 + day)

    def initialise(self) -> None:
        if self.db.one("SELECT 1 FROM prices LIMIT 1"):
            return
        with self.db.tx():
            for s in UNIVERSE:
                self.db.conn.execute("INSERT OR REPLACE INTO securities VALUES (?,?,?,?,?,?)",
                                     (s.symbol, s.name, s.sector, s.pe, s.dividend_yield, s.description))
                self.db.conn.execute("INSERT INTO prices VALUES (?,?,?)", (s.symbol, 0, s.price))
        self.db.set_meta("day", 0)

    def warm_up(self, days: int = 30) -> None:
        """Generate some history before trading starts, so momentum signals exist on day one."""
        for _ in range(days):
            self.step()
        # history before the first trading day is "pre-market": rebase the day counter
        self.db.set_meta("warmup_days", days)

    def last_prices(self) -> dict[str, float]:
        day = self.db.today()
        return {r["symbol"]: r["price"] for r in self.db.q("SELECT symbol, price FROM prices WHERE day=?", (day,))}

    def step(self) -> int:
        """Advance one trading day. Returns the new day number."""
        day = self.db.today() + 1
        rng = self._rng(day)
        prev = self.last_prices()
        market = rng.gauss(0.0002, 0.009)
        sectors = {sec: rng.gauss(0, v) for sec, v in SECTOR_VOL.items()}
        rows, news = [], []
        for s in UNIVERSE:
            shock = 0.0
            if rng.random() < self.news_prob:
                positive = rng.random() < 0.55
                shock = (1 if positive else -1) * rng.uniform(0.025, 0.07)
                tmpl = rng.choice(POS_NEWS if positive else NEG_NEWS)
                headline = tmpl.format(n=s.name)
                body = (f"{headline}. Shares moved {shock * 100:+.1f}% on the day as investors reacted. "
                        f"{s.name} operates in {s.sector.lower()}: {s.description}")
                news.append((day, s.symbol, headline, body, 1.0 if positive else -1.0))
            r = s.mu + s.beta * market + sectors[s.sector] + rng.gauss(0, s.vol) + shock
            rows.append((s.symbol, day, round(prev[s.symbol] * math.exp(r), 2)))
        with self.db.tx():
            self.db.conn.executemany("INSERT OR REPLACE INTO prices VALUES (?,?,?)", rows)
            self.db.conn.executemany("INSERT INTO news (day, symbol, headline, body, sentiment) VALUES (?,?,?,?,?)",
                                     news)
            self.db.conn.execute("INSERT OR REPLACE INTO meta VALUES ('day', ?)", (str(day),))
        return day


# ---------------------------------------------------------------- read helpers used by the MCP servers


def history(db: DB, symbol: str, days: int = 30) -> list[tuple[int, float]]:
    today = db.today()
    return [(r["day"], r["price"]) for r in db.q(
        "SELECT day, price FROM prices WHERE symbol=? AND day>? ORDER BY day", (symbol, today - days))]


def snapshot(db: DB) -> list[dict]:
    """Per-security view: price, 1/5/20-day returns, 20-day volatility, valuation and latest news."""
    today = db.today()
    out = []
    for sec in db.q("SELECT * FROM securities ORDER BY symbol"):
        h = [p for _, p in history(db, sec["symbol"], 21)]
        if not h:
            continue
        last = h[-1]

        def ret(n: int) -> float | None:
            return round(last / h[-1 - n] - 1, 4) if len(h) > n else None

        rets = [math.log(h[i] / h[i - 1]) for i in range(1, len(h))]
        vol = (sum((x - sum(rets) / len(rets)) ** 2 for x in rets) / max(len(rets) - 1, 1)) ** 0.5 if len(rets) > 2 else None
        latest = db.one("SELECT headline, sentiment, day FROM news WHERE symbol=? ORDER BY id DESC LIMIT 1", (sec["symbol"],))
        out.append({
            "symbol": sec["symbol"], "name": sec["name"], "sector": sec["sector"], "price": last,
            "ret_1d": ret(1), "ret_5d": ret(5), "ret_20d": ret(20),
            "vol_20d": round(vol * math.sqrt(252), 3) if vol is not None else None,
            "pe": sec["pe"], "dividend_yield": sec["dividend_yield"],
            "latest_news": ({"headline": latest["headline"], "sentiment": latest["sentiment"],
                             "days_ago": today - latest["day"]} if latest else None),
        })
    return out
