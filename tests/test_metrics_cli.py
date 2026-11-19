import json

import pytest

from agora.cli import main
from agora.metrics import max_drawdown, summarize


def test_drawdown_and_summary_known_values():
    assert max_drawdown([100, 120, 90, 130, 117]) == pytest.approx(-0.25)
    m = summarize([100, 101, 102.01])
    assert m["total_return"] == pytest.approx(0.0201) and m["days"] == 2 and m["max_drawdown"] == 0
    flat = summarize([100, 100, 100])
    assert flat["sharpe"] == 0 and flat["ann_vol"] == 0


def test_backtest_cli_writes_report(tmp_path, capsys):
    main(["backtest", "--days", "5", "--seed", "3", "--out", str(tmp_path)])
    rep = json.loads((tmp_path / "backtest_seed3.json").read_text())
    assert {"vale", "kite", "hollis", "juno", "benchmark"} <= set(rep)
    assert "| Equal-weight universe |" in capsys.readouterr().out


def test_mcp_config_points_at_module_entrypoint(capsys):
    main(["mcp-config", "--trader", "guest"])
    cfg = json.loads(capsys.readouterr().out)
    assert cfg["mcpServers"]["agora-accounts"]["args"][:3] == ["-m", "agora.mcp_servers", "accounts"]
