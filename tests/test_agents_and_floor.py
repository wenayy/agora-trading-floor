import json

import pytest

from agora.agents.brains import ToolCall, Turn
from agora.agents.personas import TRADERS
from agora.agents.policies import PolicyBrain, target_weights
from agora.config import Settings
from agora.db import DB
from agora.floor import Floor


@pytest.fixture()
def floor(tmp_path):
    f = Floor(Settings(db_path=tmp_path / "f.db", seed=11), db=DB(tmp_path / "f.db"))
    f.setup(warmup_days=30)
    return f


async def test_cycle_runs_all_traders_with_research_and_trades(floor):
    out = await floor.run_cycle()
    assert set(out["traders"]) == {t.id for t in TRADERS}
    assert all("error" not in r for r in out["traders"].values())
    kinds = {r["kind"] for r in floor.db.q("SELECT kind FROM events")}
    assert {"market_open", "session_start", "tool_call", "research_request", "research_brief", "session_end",
            "cycle_done"} <= kinds
    txs = floor.ledger.transactions()
    assert txs and all(len(t["rationale"]) >= 15 for t in txs)
    # every trader's risk limits held
    for t in TRADERS:
        rep = floor.ledger.report(t.id)
        assert all(p["weight"] <= 0.30 + 1e-6 for p in rep["positions"]) and rep["cash"] >= 0


async def test_multi_day_run_is_deterministic(tmp_path):
    def run(path):
        f = Floor(Settings(db_path=path, seed=4), db=DB(path))
        f.setup(warmup_days=30)
        return f

    a, b = run(tmp_path / "a.db"), run(tmp_path / "b.db")
    ra = [await a.run_cycle() for _ in range(3)]
    rb = [await b.run_cycle() for _ in range(3)]
    assert [r["equity"] for r in ra] == [r["equity"] for r in rb]


async def test_rejected_orders_are_resized_and_retried(tmp_path):
    s = Settings(db_path=tmp_path / "r.db", seed=11, max_order_pct=0.10)
    f = Floor(s, db=DB(tmp_path / "r.db"), traders=[TRADERS[1]])
    f.setup()
    await f.run_cycle()
    errors = [json.loads(r["data"]) for r in f.db.q("SELECT data FROM events WHERE kind='tool_error'")]
    assert errors and all("Order rejected" in e["result"] for e in errors)
    retried = [t for t in f.ledger.transactions("kite") if "Resized to the risk limit" in t["rationale"]]
    assert retried, "the agent should resize rejected orders to the size the broker allows"


async def test_negative_research_vetoes_new_position(tmp_path):
    class Bearish:
        model = "bearish"

        def respond(self, messages, tools):
            return Turn("- nothing good\nSignal: negative")

    f = Floor(Settings(db_path=tmp_path / "v.db", seed=11), db=DB(tmp_path / "v.db"), traders=[TRADERS[1]],
              researcher_brain=Bearish())
    f.setup()
    await f.run_cycle()
    brief = json.loads(f.db.q("SELECT data FROM events WHERE kind='research_request'")[0]["data"])["question"]
    vetoed = brief.split(" for ")[-1].rstrip("?")
    assert vetoed not in {t["symbol"] for t in f.ledger.transactions("kite")}


async def test_llm_style_brain_with_bad_tool_name_gets_error_not_crash(floor):
    class Confused:
        model = "confused"
        n = 0

        def respond(self, messages, tools):
            self.n += 1
            if self.n == 1:
                return Turn(None, [ToolCall("transfer_funds_to", {"account": "vale"})])
            return Turn("I could not do that.")

    floor.brain_factory = lambda t: Confused()
    out = await floor.run_trader(TRADERS[1])
    err = json.loads(floor.db.q("SELECT data FROM events WHERE kind='tool_error'")[0]["data"])
    assert "unknown tool" in err["result"] and out["summary"] == "I could not do that."


def test_strategy_targets_respect_position_cap():
    snap = [{"symbol": f"S{i}", "sector": "A" if i < 5 else "Consumer staples", "price": 10 + i, "ret_1d": 0,
             "ret_5d": -0.05 * (i % 3), "ret_20d": 0.01 * i, "vol_20d": 0.2 + i / 100, "pe": 5 + i,
             "dividend_yield": 0.02, "latest_news": None} for i in range(8)]
    for strat in ("value", "momentum", "macro", "contrarian"):
        w = target_weights(strat, snap, {})
        assert all(v <= 0.28 for v in w.values()) and sum(w.values()) <= 1.0


def test_policy_brain_first_turn_reads_state_in_parallel():
    turn = PolicyBrain("momentum").respond([{"role": "system", "content": ""}, {"role": "user", "content": ""}], [{}])
    assert [c.name for c in turn.tool_calls] == ["get_account", "recall", "get_market_snapshot"]
