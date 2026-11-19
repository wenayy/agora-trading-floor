"""Record a run of the floor into static JSON so the dashboard can be published as a demo without a backend.

    python scripts/export_demo.py --days 60      # writes web/public/demo/*.json
    cd web && npm run build:demo                  # static site in web/dist-demo
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from fastapi.testclient import TestClient

from agora.api.server import create_app
from agora.config import Settings
from agora.db import DB
from agora.floor import Floor

OUT = Path(__file__).resolve().parents[1] / "web" / "public" / "demo"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=60)
    p.add_argument("--seed", type=int, default=7)
    a = p.parse_args()
    db = DB(":memory:")
    floor = Floor(Settings(db_path=":memory:", seed=a.seed), db=db)
    floor.setup()

    async def go():
        for _ in range(a.days):
            await floor.run_cycle()

    asyncio.run(go())
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.json"):  # trace ids change between runs
        old.unlink()
    with TestClient(create_app(floor=floor)) as c:
        files = {"floor": "/api/floor", "equity": "/api/equity", "metrics": "/api/metrics", "market": "/api/market"}
        for t in floor.traders:
            files[f"traders_{t.id}"] = f"/api/traders/{t.id}"
            files[f"events_{t.id}"] = f"/api/events?latest=true&limit=400&trader_id={t.id}"
        for t in floor.traders:  # the trace of each trader's most recent session
            tid = c.get(f"/api/traders/{t.id}").json()["sessions"][0]["trace_id"]
            files[f"traces_{tid}"] = f"/api/traces/{tid}"
        for name, url in files.items():
            (OUT / f"{name}.json").write_text(json.dumps(c.get(url).json(), separators=(",", ":")))
    print(f"wrote {len(files)} files to {OUT}")


if __name__ == "__main__":
    main()
