# ML4T Live

Live trading infrastructure for ML4T strategies.

## Overview

The main design goal is strategy portability: a strategy written for `ml4t-backtest` should keep the
same synchronous `on_data(...)` shape when moved into live execution.

`LiveEngine` handles the async broker/feed runtime, and `ThreadSafeBrokerWrapper` bridges that async
runtime back to the synchronous strategy interface.

## Architecture

```text
LiveEngine
    |
    +-- SafeBroker
    |       +-- IBBroker / AlpacaBroker
    |
    +-- ThreadSafeBrokerWrapper
    |       +-- Strategy.on_data(...)
    |
    +-- DataFeedProtocol
            +-- IBDataFeed / AlpacaDataFeed / DataBentoFeed / CryptoFeed / OKXFundingFeed
            +-- optional BarAggregator wrapper
```

## Safety Model

The intended rollout path is:

1. Shadow mode
2. Paper trading
3. Small live size
4. Gradual scale-up

`SafeBroker` provides:

- position and exposure limits
- order-size and rate limits
- duplicate-order suppression
- drawdown-triggered kill switch
- persistent state across restarts
- virtual portfolio tracking in shadow mode

## Quick Example

```python
from ml4t.live import AlpacaBroker, AlpacaDataFeed, LiveEngine, LiveRiskConfig, SafeBroker

safe_broker = SafeBroker(
    AlpacaBroker(api_key="...", secret_key="...", paper=True),
    LiveRiskConfig(shadow_mode=True),
)
feed = AlpacaDataFeed(api_key="...", secret_key="...", symbols=["SPY"])

engine = LiveEngine(my_strategy, safe_broker, feed)
await engine.connect()
await engine.run()
```

## Installation

```bash
pip install ml4t-live
```

Install `databento` separately if you want `DataBentoFeed`.

## Next Steps

- [Installation Guide](getting-started/installation.md)
- [Quickstart](getting-started/quickstart.md)
- [Brokers](user-guide/brokers.md)
- [Data Feeds](user-guide/feeds.md)
- [Risk Controls](user-guide/risk.md)
- [API Reference](api/index.md)

## Disclaimer

This library reduces operational risk but does not remove it. Start in shadow mode, validate order
flow carefully, and treat live deployment as a staged rollout rather than a one-step migration.
