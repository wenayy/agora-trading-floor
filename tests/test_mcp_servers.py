import json
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.memory import create_connected_server_and_client_session as connect

from agora.db import DB
from agora.ledger import Ledger
from agora.market.sim import MarketSimulator
from agora.mcp_servers.servers import accounts_server, market_server, memory_server, news_server


@pytest.fixture()
def db(tmp_path):
    d = DB(tmp_path / "floor.db")
    sim = MarketSimulator(d, seed=5)
    sim.initialise()
    sim.warm_up(25)
    led = Ledger(d)
    led.open_account("kite", "Kite", "momentum", 100_000)
    led.open_account("vale", "Vale", "value", 100_000)
    return d


def payload(result):
    return json.loads(result.content[0].text)


async def test_tools_are_discoverable_with_schemas(db):
    async with connect(accounts_server(db, "kite")) as s:
        tools = {t.name: t for t in (await s.list_tools()).tools}
    assert set(tools) == {"get_account", "buy_shares", "sell_shares", "get_transactions"}
    schema = tools["buy_shares"].inputSchema
    assert schema["properties"]["quantity"]["type"] == "integer" and "rationale" in schema["required"]


async def test_buy_through_mcp_and_risk_rejection_is_a_tool_error(db):
    async with connect(accounts_server(db, "kite")) as s:
        ok = await s.call_tool("buy_shares", {"symbol": "HLXF", "quantity": 10, "rationale": "momentum leader, 20d high"})
        assert not ok.isError and payload(ok)["side"] == "BUY"
        bad = await s.call_tool("buy_shares", {"symbol": "HLXF", "quantity": 100000, "rationale": "far too big an order"})
        assert bad.isError and "Order rejected" in bad.content[0].text
        acct = payload(await s.call_tool("get_account", {}))
        assert acct["positions"][0]["symbol"] == "HLXF"
        res = await s.read_resource("account://report")
        assert json.loads(res.contents[0].text)["trader_id"] == "kite"


async def test_accounts_server_is_scoped_to_one_trader(db):
    async with connect(accounts_server(db, "kite")) as s:
        await s.call_tool("buy_shares", {"symbol": "KAVR", "quantity": 5, "rationale": "defensive staples exposure"})
    assert Ledger(db).holdings("vale") == {}  # no tool can address another trader's account


async def test_market_and_news_servers(db):
    async with connect(market_server(db)) as s:
        snap = payload(await s.call_tool("get_market_snapshot", {}))
        assert len(snap["securities"]) == 10
        hist = await s.call_tool("get_price_history", {"symbol": "salt", "days": 10})
        assert not hist.isError and len(hist.content) == 10  # list results come back as one content block per item
        bad = await s.call_tool("get_price", {"symbol": "NOPE"})
        assert bad.isError
    async with connect(news_server(db)) as s:
        prof = payload(await s.call_tool("get_company_profile", {"symbol": "SALT"}))
        assert prof["sector"] == "Energy storage"
        await s.call_tool("search_news", {"days": 30})


async def test_memory_server_persists_and_is_private(db):
    async with connect(memory_server(db, "kite")) as s:
        await s.call_tool("remember", {"key": "thesis:SALT", "value": "sodium-ion demand rising"})
    async with connect(memory_server(db, "kite")) as s:
        notes = await s.call_tool("recall", {})
        assert "sodium-ion" in notes.content[0].text
    async with connect(memory_server(db, "vale")) as s:
        assert "sodium" not in str((await s.call_tool("recall", {})).content)  # Vale cannot read Kite's notes


async def test_server_runs_over_stdio_as_separate_process(db):
    params = StdioServerParameters(command=sys.executable, args=["-m", "agora.mcp_servers", "market", "--db", db.path])
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        names = [t.name for t in (await s.list_tools()).tools]
        price = payload(await s.call_tool("get_price", {"symbol": "HLXF"}))
    assert "get_market_snapshot" in names and price["price"] > 0
