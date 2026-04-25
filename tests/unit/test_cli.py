"""Unit tests for the ml4t-live CLI."""

from pathlib import Path

from ml4t.live.cli.main import (
    BrokerProbeResult,
    _load_strategy_instance,
    _load_strategy_module,
    _module_symbols,
    run_cli,
)


def test_load_strategy_instance_from_single_subclass(tmp_path: Path):
    strategy_file = tmp_path / "demo_strategy.py"
    strategy_file.write_text(
        "from ml4t.backtest import Strategy\n"
        "class DemoStrategy(Strategy):\n"
        "    def on_data(self, timestamp, data, context, broker):\n"
        "        return None\n"
    )

    module = _load_strategy_module(strategy_file)
    strategy = _load_strategy_instance(module)

    assert strategy.__class__.__name__ == "DemoStrategy"


def test_module_symbols_defaults_and_overrides(tmp_path: Path):
    strategy_file = tmp_path / "symbols_strategy.py"
    strategy_file.write_text(
        "from ml4t.backtest import Strategy\n"
        "SYMBOLS = ['AAPL', 'MSFT']\n"
        "class DemoStrategy(Strategy):\n"
        "    def on_data(self, timestamp, data, context, broker):\n"
        "        return None\n"
    )

    module = _load_strategy_module(strategy_file)

    assert _module_symbols(module, "alpaca") == ["AAPL", "MSFT"]

    empty_file = tmp_path / "default_strategy.py"
    empty_file.write_text(
        "from ml4t.backtest import Strategy\n"
        "class DemoStrategy(Strategy):\n"
        "    def on_data(self, timestamp, data, context, broker):\n"
        "        return None\n"
    )
    default_module = _load_strategy_module(empty_file)

    assert _module_symbols(default_module, "alpaca") == ["SPY"]
    assert _module_symbols(default_module, "okx") == ["BTC-USDT-SWAP"]


def test_status_command_shows_persisted_snapshot(monkeypatch, tmp_path: Path, capsys):
    async def fake_probe() -> BrokerProbeResult:
        return BrokerProbeResult(
            status="skipped", detail="disabled", positions={}, pending_orders=[]
        )

    monkeypatch.setattr("ml4t.live.cli.main._probe_alpaca", fake_probe)
    monkeypatch.setattr("ml4t.live.cli.main._probe_ib", fake_probe)

    state_file = tmp_path / "risk-state.json"
    state_file.write_text(
        """{
  "date": "2024-01-02",
  "daily_loss": 12.5,
  "orders_placed": 3,
  "high_water_mark": 100500.0,
  "persisted_positions": {"AAPL": 5.0},
  "persisted_pending_orders": [
    {"asset": "AAPL", "side": "buy", "quantity": 5.0, "order_type": "limit"}
  ],
  "kill_switch_activated": false,
  "kill_switch_reason": ""
}
"""
    )

    exit_code = run_cli(["status", "--state-file", str(state_file)])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "status_summary: unavailable - no live broker probes configured" in output
    assert "persisted_positions: AAPL:5" in output
    assert "persisted_pending_orders: AAPL:buy:5:limit" in output


def test_status_command_without_state_file(monkeypatch, tmp_path: Path, capsys):
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    monkeypatch.delenv("IB_HOST", raising=False)
    monkeypatch.delenv("IB_PORT", raising=False)
    monkeypatch.delenv("IB_CLIENT_ID", raising=False)
    monkeypatch.delenv("ML4T_IB_HOST", raising=False)
    monkeypatch.delenv("ML4T_IB_PORT", raising=False)
    monkeypatch.delenv("ML4T_IB_CLIENT_ID", raising=False)

    missing_state = tmp_path / "missing-risk-state.json"
    exit_code = run_cli(["status", "--state-file", str(missing_state)])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "risk_state: missing" in output
    assert "status_summary: unavailable - no live broker probes configured" in output
    assert "alpaca: skipped" in output
    assert "ib: skipped" in output
