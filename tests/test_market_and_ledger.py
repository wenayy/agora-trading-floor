import pytest

from agora.db import DB
from agora.ledger import Ledger, RiskPolicy, RiskViolation
from agora.market.sim import MarketSimulator, snapshot


@pytest.fixture()
def db():
    d = DB(":memory:")
    sim = MarketSimulator(d, seed=3)
    sim.initialise()
    sim.warm_up(25)
    return d


def test_simulator_is_deterministic_per_seed():
    a, b, c = DB(":memory:"), DB(":memory:"), DB(":memory:")
    for d, seed in ((a, 1), (b, 1), (c, 2)):
        s = MarketSimulator(d, seed=seed)
        s.initialise()
        for _ in range(10):
            s.step()
    pa, pb, pc = (MarketSimulator(x).last_prices() for x in (a, b, c))
    assert pa == pb and pa != pc


def test_snapshot_has_returns_vol_and_news(db):
    snap = snapshot(db)
    assert len(snap) == 10
    hlxf = next(s for s in snap if s["symbol"] == "HLXF")
    assert hlxf["ret_20d"] is not None and hlxf["vol_20d"] > 0
    assert any(s["latest_news"] for s in snap)


def make_ledger(db, **kw):
    led = Ledger(db, RiskPolicy(**kw))
    led.open_account("t1", "Test", "test", 100_000)
    return led


def test_buy_sell_roundtrip_costs_spread_and_fees(db):
    led = make_ledger(db)
    px = led.price("KAVR")
    b = led.buy("t1", "KAVR", 100, "staples are defensive in a weak tape")
    assert b["price"] > px and b["fee"] >= 1
    s = led.sell("t1", "KAVR", 100, "taking the round trip for the test")
    assert s["price"] < px and s["realized_pnl"] < 0  # spread + fees make an instant round trip lose money
    assert led.holdings("t1") == {}
    assert led.cash("t1") < 100_000


def test_risk_limits_are_enforced_by_the_ledger(db):
    led = make_ledger(db, max_order_pct=0.25, max_position_pct=0.30, max_trades_per_cycle=3)
    px = led.price("TJPH")
    with pytest.raises(RiskViolation, match="limit is 25%"):
        led.buy("t1", "TJPH", int(30_000 // px), "too big an order for the policy")
    with pytest.raises(RiskViolation, match="rationale"):
        led.buy("t1", "TJPH", 1, "because")
    with pytest.raises(RiskViolation, match="short selling"):
        led.sell("t1", "TJPH", 1, "selling something I do not own")
    with pytest.raises(RiskViolation, match="positive whole number"):
        led.buy("t1", "TJPH", 0, "zero shares is not an order")
    with pytest.raises(RiskViolation, match="unknown symbol"):
        led.buy("t1", "ZZZZ", 1, "no such company on this exchange")


def test_position_limit_tells_agent_how_much_room_is_left(db):
    led = make_ledger(db, max_order_pct=0.25, max_position_pct=0.30)
    px = led.price("BHST")
    led.buy("t1", "BHST", int(24_000 // px), "first tranche of a steel position")
    with pytest.raises(RiskViolation, match=r"at most \d+ more shares"):
        led.buy("t1", "BHST", int(10_000 // px), "second tranche that breaches the cap")


def test_trade_count_limit_per_cycle(db):
    led = make_ledger(db, max_trades_per_cycle=2)
    led.buy("t1", "DCAG", 1, "small starter position one")
    led.buy("t1", "DCAG", 1, "small starter position two")
    with pytest.raises(RiskViolation, match="trade limit"):
        led.buy("t1", "DCAG", 1, "third trade should be blocked")
    MarketSimulator(db).step()  # new day resets the count
    led.buy("t1", "DCAG", 1, "a new cycle allows trading again")


def test_report_and_equity_curve(db):
    led = make_ledger(db)
    led.buy("t1", "HELV", 50, "parcel volumes are recovering")
    led.snapshot_all()
    MarketSimulator(db).step()
    led.snapshot_all()
    rep = led.report("t1")
    assert rep["positions"][0]["symbol"] == "HELV" and 0 < rep["positions"][0]["weight"] < 0.3
    assert abs(rep["equity"] - (rep["cash"] + rep["positions"][0]["market_value"])) < 0.05
    assert len(led.equity_curve("t1")) == 2
