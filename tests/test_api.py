import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from agora.api.server import create_app
from agora.config import Settings
from agora.db import DB
from agora.floor import Floor


@pytest.fixture()
def client(tmp_path):
    fl = Floor(Settings(db_path=tmp_path / "api.db", seed=9), db=DB(tmp_path / "api.db"))
    fl.setup()
    asyncio.run(fl.run_cycle())
    with TestClient(create_app(floor=fl)) as c:
        yield c


def test_floor_equity_and_trader_views(client):
    floor = client.get("/api/floor").json()
    assert floor["day"] == 1 and len(floor["traders"]) == 4
    assert all(t["last_session"]["summary"] for t in floor["traders"])
    eq = client.get("/api/equity").json()
    assert len(eq["traders"]["kite"]) == 2 and eq["benchmark"][0]["day"] == 0
    kite = client.get("/api/traders/kite").json()
    assert kite["transactions"] and kite["notes"][0]["key"] == "last_session" and kite["sessions"]
    assert client.get("/api/traders/nobody").status_code == 404


def test_market_news_metrics_events(client):
    assert len(client.get("/api/market").json()["securities"]) == 10
    assert isinstance(client.get("/api/news").json(), list)
    assert "benchmark" in client.get("/api/metrics").json()
    evs = client.get("/api/events", params={"latest": True, "limit": 50}).json()
    assert evs and evs[-1]["kind"] == "cycle_done"
    trace_id = client.get("/api/traders/kite").json()["sessions"][0]["trace_id"]
    assert client.get(f"/api/traces/{trace_id}").json()["name"] == "trader.kite"


def test_cycle_endpoint_runs_a_day(client):
    r = client.post("/api/cycle")
    assert r.status_code == 202
    for _ in range(100):
        if client.get("/api/floor").json()["day"] == 2 and not client.get("/api/floor").json()["running"]:
            break
        time.sleep(0.05)
    assert client.get("/api/floor").json()["day"] == 2
