# Risk Controls

`SafeBroker` wraps any async broker with pre-trade risk checks, duplicate-order protection, shadow
mode, and kill-switch state persistence.

## LiveRiskConfig

```python
from ml4t.live import LiveRiskConfig

config = LiveRiskConfig(
    max_position_value=25_000,
    max_position_shares=1_000,
    max_total_exposure=100_000,
    max_positions=10,
    max_order_value=5_000,
    max_order_shares=250,
    max_orders_per_minute=5,
    max_daily_loss=2_000,
    max_drawdown_pct=0.10,
    max_price_deviation_pct=0.05,
    max_data_staleness_seconds=60,
    dedup_window_seconds=1.0,
    allowed_assets={"SPY", "QQQ"},
    shadow_mode=True,
    state_file=".ml4t_risk_state.json",
)
```

## Shadow Mode

Shadow mode is the recommended starting point:

```python
safe_broker = SafeBroker(broker, LiveRiskConfig(shadow_mode=True))
```

In shadow mode:

- risk checks still run
- orders are marked filled virtually
- `VirtualPortfolio` tracks positions and cash
- no real broker order is submitted

## Risk Categories

### Position Limits

- `max_position_value`
- `max_position_shares`
- `max_total_exposure`
- `max_positions`

### Order Limits

- `max_order_value`
- `max_order_shares`
- `max_orders_per_minute`

### Loss Limits

- `max_daily_loss`
- `max_drawdown_pct`

### Trade Safety

- `max_price_deviation_pct`
- `max_data_staleness_seconds`
- `dedup_window_seconds`
- `allowed_assets`
- `blocked_assets`

## Kill Switch

The kill switch can be activated automatically by drawdown checks or manually:

```python
safe_broker.enable_kill_switch("manual halt")
safe_broker.disable_kill_switch()
```

Kill-switch state is persisted in `state_file`, so it survives restarts until manually cleared.

## SafeBroker Usage

```python
from ml4t.live import IBBroker, LiveRiskConfig, SafeBroker

raw_broker = IBBroker(port=7497)
safe_broker = SafeBroker(
    raw_broker,
    LiveRiskConfig(
        shadow_mode=True,
        max_position_value=10_000,
        max_order_value=2_500,
    ),
)
```

Inside strategy code, orders are still placed through the normal synchronous broker interface:

```python
def on_data(self, timestamp, data, context, broker):
    if broker.get_position("AAPL") is None:
        broker.submit_order("AAPL", 10)
```

## Errors

Risk violations raise `RiskLimitError`:

```python
from ml4t.live import RiskLimitError

try:
    await safe_broker.submit_order_async("AAPL", 10_000)
except RiskLimitError as exc:
    print(f"blocked: {exc}")
```

## Recommended Progression

1. Start in shadow mode
2. Validate order flow and position tracking
3. Move to paper credentials with conservative limits
4. Go live only after stable monitoring and manual review
