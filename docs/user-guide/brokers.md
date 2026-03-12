# Brokers

`ml4t-live` currently ships two broker adapters:

- `IBBroker` for Interactive Brokers
- `AlpacaBroker` for Alpaca stocks and crypto

## Broker Model

The raw broker implementations are asynchronous. They implement `AsyncBrokerProtocol` and are meant
to be used by `LiveEngine` and `SafeBroker`.

- Strategy code uses synchronous broker calls such as `broker.get_position(...)`
- Raw broker instances use async methods such as `await broker.get_cash_async()`

## Interactive Brokers

```python
from ml4t.live import IBBroker

broker = IBBroker(
    host="127.0.0.1",
    port=7497,  # paper
    client_id=1,
)

await broker.connect()
```

### Notes

- `7497` is the usual TWS paper port
- `7496` is the usual TWS live port
- You must have TWS or IB Gateway running with API access enabled

## Alpaca

```python
from ml4t.live import AlpacaBroker

broker = AlpacaBroker(
    api_key="YOUR_API_KEY",
    secret_key="YOUR_SECRET_KEY",
    paper=True,
)

await broker.connect()
```

### Notes

- `paper=True` is the safe default
- The broker maintains positions and pending orders internally from Alpaca account state and trade
  updates

## Recommended Wrapper

Wrap raw brokers with `SafeBroker` before handing them to `LiveEngine`:

```python
from ml4t.live import LiveRiskConfig, SafeBroker

safe_broker = SafeBroker(
    broker,
    LiveRiskConfig(
        shadow_mode=True,
        max_position_value=25_000,
        max_order_value=5_000,
    ),
)
```

## Direct Async Broker Calls

Outside strategy code, use the broker asynchronously:

```python
cash = await broker.get_cash_async()
equity = await broker.get_account_value_async()
order = await broker.submit_order_async("AAPL", 10)
```

## Strategy-Side Calls

Inside `Strategy.on_data(...)`, the broker object is synchronous:

```python
def on_data(self, timestamp, data, context, broker):
    if broker.get_position("AAPL") is None:
        broker.submit_order("AAPL", 10)
```

## Disconnect

```python
await broker.disconnect()
```

## Error Handling

Broker connection and order failures surface as standard Python exceptions, typically
`RuntimeError`, broker SDK exceptions, or `RiskLimitError` when wrapped by `SafeBroker`.

```python
from ml4t.live import RiskLimitError

try:
    await safe_broker.submit_order_async("AAPL", 10_000)
except RiskLimitError as exc:
    print(f"blocked by risk controls: {exc}")
```
