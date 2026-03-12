# Data Feeds

`ml4t-live` supports multiple live and replay data sources through `DataFeedProtocol`.

Each feed yields:

- `timestamp`
- `data`
- `context`

where `data` is typically shaped like `{symbol: {"open", "high", "low", "close", "volume"}}` for
bar feeds or `{symbol: {"price", "size"}}` for tick-style feeds.

## AlpacaDataFeed

```python
from ml4t.live import AlpacaDataFeed

feed = AlpacaDataFeed(
    api_key="...",
    secret_key="...",
    symbols=["AAPL", "BTC/USD"],
    data_type="bars",  # "bars", "quotes", or "trades"
    feed="iex",        # "iex" or "sip"
)
```

## IBDataFeed

`IBDataFeed` uses an existing connected IB session:

```python
from ml4t.live import IBBroker, IBDataFeed

broker = IBBroker(port=7497)
await broker.connect()

feed = IBDataFeed(broker.ib, symbols=["SPY", "QQQ"])
```

This feed emits tick-style updates. Wrap it in `BarAggregator` if your strategy expects bars.

## DataBentoFeed

Historical replay:

```python
from ml4t.live import DataBentoFeed

feed = DataBentoFeed.from_file(
    "data/ES_202401.dbn",
    symbols=["ES.FUT"],
    replay_speed=10.0,
)
```

Live streaming:

```python
feed = DataBentoFeed.from_live(
    api_key="...",
    dataset="GLBX.MDP3",
    schema="ohlcv-1s",
    symbols=["ES.c.0", "NQ.c.0"],
)
```

`DataBentoFeed` requires the optional `databento` package.

## CryptoFeed

```python
from ml4t.live import CryptoFeed

feed = CryptoFeed(
    exchange="binance",
    symbols=["BTC/USDT", "ETH/USDT"],
    timeframe="1m",
    stream_ohlcv=True,
)
```

This feed uses `ccxt` or `ccxt.pro` depending on availability.

## OKXFundingFeed

```python
from ml4t.live import OKXFundingFeed

feed = OKXFundingFeed(
    symbols=["BTC-USDT-SWAP", "ETH-USDT-SWAP"],
    timeframe="1H",
    poll_interval_seconds=60.0,
)
```

This feed combines OHLCV bars with funding-rate context for perpetual futures strategies.

## BarAggregator

Use `BarAggregator` to convert tick or sub-minute feeds into larger bars:

```python
from ml4t.live import BarAggregator

feed = BarAggregator(
    source_feed=raw_feed,
    bar_size_minutes=1,
    flush_timeout_seconds=2.0,
)
```

Optional filtering:

```python
feed = BarAggregator(raw_feed, bar_size_minutes=5, assets=["SPY", "QQQ"])
```

## Using a Feed With LiveEngine

```python
engine = LiveEngine(strategy, safe_broker, feed)
await engine.connect()
await engine.run()
```

`LiveEngine.connect()` starts the feed for you. You do not need to call `feed.start()` separately in
the normal engine flow.

## Choosing a Feed

- Use `AlpacaDataFeed` for Alpaca-native stocks and crypto
- Use `IBDataFeed` when IB is your broker and you want direct market data from TWS/Gateway
- Use `DataBentoFeed` for replay and institutional market-data workflows
- Use `CryptoFeed` for exchange-agnostic crypto streaming
- Use `OKXFundingFeed` for perpetual swap strategies that depend on funding-rate context
