"""Tests for LiveEngine - async orchestration layer."""

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import pytest
from ml4t.backtest import Strategy
from ml4t.backtest.types import Order, OrderSide, OrderType, Position

from ml4t.live.engine import LiveEngine
from ml4t.live.protocols import AsyncBrokerProtocol, DataFeedProtocol
from ml4t.live.safety import LiveRiskConfig, SafeBroker

# === Mock Implementations ===


class MockAsyncBroker:
    """Mock async broker for testing."""

    def __init__(self):
        self._positions: dict[str, Position] = {}
        self._pending_orders: list[Order] = []
        self._connected = False
        self._cash = 100_000.0
        self._account_value = 100_000.0
        self.submit_calls = 0

    # Properties (expected by ThreadSafeBrokerWrapper)
    @property
    def positions(self) -> dict[str, Position]:
        return self._positions.copy()

    @property
    def pending_orders(self) -> list[Order]:
        return self._pending_orders.copy()

    @property
    def is_connected(self) -> bool:
        """Sync property for direct access."""
        return self._connected

    # AsyncBrokerProtocol methods
    async def connect(self) -> None:
        """Connect to broker."""
        await asyncio.sleep(0.01)  # Simulate network I/O
        self._connected = True

    async def disconnect(self) -> None:
        """Disconnect from broker."""
        await asyncio.sleep(0.01)
        self._connected = False

    async def is_connected_async(self) -> bool:
        """Check if connected (async version)."""
        return self._connected

    async def get_positions_async(self) -> dict[str, Position]:
        """Get all positions (async version)."""
        return self._positions.copy()

    async def get_pending_orders_async(self) -> list[Order]:
        """Get pending orders (async version)."""
        return self._pending_orders.copy()

    async def get_position_async(self, asset: str) -> Position | None:
        """Get position (async version)."""
        return self._positions.get(asset)

    async def get_cash_async(self) -> float:
        return self._cash

    async def get_account_value_async(self) -> float:
        return self._account_value

    async def submit_order_async(
        self,
        asset: str,
        quantity: int,
        side: OrderSide | None = None,
        order_type: OrderType = OrderType.MARKET,
        limit_price: float | None = None,
        stop_price: float | None = None,
        **kwargs: Any,
    ) -> Order:
        """Submit order."""
        await asyncio.sleep(0.01)  # Simulate network I/O
        self.submit_calls += 1

        # Auto-detect side
        if side is None:
            side = OrderSide.BUY if quantity > 0 else OrderSide.SELL
            quantity = abs(quantity)

        order = Order(
            id=f"order_{len(self._pending_orders) + 1}",
            asset=asset,
            quantity=quantity,
            side=side,
            type=order_type,
            limit_price=limit_price,
            stop_price=stop_price,
            timestamp=datetime.now(),
        )
        self._pending_orders.append(order)
        return order

    async def cancel_order_async(self, order_id: str) -> bool:
        """Cancel order."""
        await asyncio.sleep(0.01)
        for order in self._pending_orders:
            if order.id == order_id:
                self._pending_orders.remove(order)
                return True
        return False

    async def close_position_async(self, asset: str) -> Order | None:
        """Close position."""
        pos = await self.get_position_async(asset)
        if pos is None:
            return None

        side = OrderSide.SELL if pos.quantity > 0 else OrderSide.BUY
        return await self.submit_order_async(asset, abs(pos.quantity), side, OrderType.MARKET)


# Check protocol compliance
assert isinstance(MockAsyncBroker(), AsyncBrokerProtocol)


class MockDataFeed:
    """Mock data feed for testing."""

    def __init__(self, bars: list[tuple[datetime, dict, dict]], delay: float = 0.01):
        self.bars = bars
        self.delay = delay
        self._started = False
        self._stopped = False

    async def start(self) -> None:
        """Start feed."""
        await asyncio.sleep(0.01)
        self._started = True

    def stop(self) -> None:
        """Stop feed."""
        self._stopped = True

    def __aiter__(self) -> AsyncIterator[tuple[datetime, dict[str, dict], dict]]:
        """Return async iterator."""
        return self

    async def __anext__(self) -> tuple[datetime, dict[str, dict], dict]:
        """Get next bar."""
        if not self.bars or self._stopped:
            raise StopAsyncIteration

        timestamp, data, context = self.bars.pop(0)
        await asyncio.sleep(self.delay)
        return timestamp, data, context


# Check protocol compliance
assert isinstance(MockDataFeed([]), DataFeedProtocol), (
    "MockDataFeed does not implement DataFeedProtocol"
)


class RecordingStrategy(Strategy):
    """Test strategy that records calls."""

    def __init__(self):
        self.on_start_called = False
        self.on_data_calls: list[tuple[datetime, dict, dict]] = []
        self.on_end_called = False
        self.broker_ref = None

    def on_start(self, broker: Any) -> None:
        self.on_start_called = True
        self.broker_ref = broker

    def on_data(self, timestamp: datetime, data: dict, context: dict, broker: Any) -> None:
        self.on_data_calls.append((timestamp, data, context))
        self.broker_ref = broker

    def on_end(self, broker: Any) -> None:
        self.on_end_called = True


class ErrorStrategy(Strategy):
    """Strategy that raises exceptions."""

    def __init__(self, error_on_bar: int = 0):
        self.error_on_bar = error_on_bar
        self.call_count = 0

    def on_data(self, timestamp: datetime, data: dict, context: dict, broker: Any) -> None:
        self.call_count += 1
        if self.call_count == self.error_on_bar:
            raise ValueError("Test error")


class ShadowEntryStrategy(Strategy):
    """Strategy that enters once and relies on position state thereafter."""

    def __init__(self):
        self.order_attempts = 0

    def on_data(self, timestamp: datetime, data: dict, context: dict, broker: Any) -> None:
        if broker.get_position("AAPL") is None:
            self.order_attempts += 1
            broker.submit_order("AAPL", 10, side=OrderSide.BUY)


# === Test Cases ===


@pytest.mark.asyncio
async def test_engine_initialization():
    """Test LiveEngine initialization."""
    strategy = RecordingStrategy()
    broker = MockAsyncBroker()
    feed = MockDataFeed([])

    engine = LiveEngine(strategy, broker, feed)

    assert engine.strategy is strategy
    assert engine.broker is broker
    assert engine.feed is feed
    assert engine.halt_on_error is False
    assert engine._running is False
    assert engine._wrapped_broker is None


@pytest.mark.asyncio
async def test_connect():
    """Test broker and feed connection."""
    strategy = RecordingStrategy()
    broker = MockAsyncBroker()
    feed = MockDataFeed([])

    engine = LiveEngine(strategy, broker, feed)
    await engine.connect()

    # Broker connected
    assert broker.is_connected is True

    # Feed started
    assert feed._started is True

    # Wrapper created
    assert engine._wrapped_broker is not None
    assert engine._loop is not None


@pytest.mark.asyncio
async def test_run_empty_feed():
    """Test engine with empty data feed."""
    strategy = RecordingStrategy()
    broker = MockAsyncBroker()
    feed = MockDataFeed([])

    engine = LiveEngine(strategy, broker, feed)
    await engine.connect()
    await engine.run()

    # Lifecycle callbacks called
    assert strategy.on_start_called is True
    assert strategy.on_end_called is True

    # No data received
    assert len(strategy.on_data_calls) == 0
    assert engine.stats["bar_count"] == 0


@pytest.mark.asyncio
async def test_run_with_data():
    """Test engine processes bars correctly."""
    strategy = RecordingStrategy()
    broker = MockAsyncBroker()

    # Create test bars
    bars = [
        (
            datetime(2024, 1, 1, 9, 30),
            {"AAPL": {"open": 150.0, "high": 151.0, "low": 149.0, "close": 150.5}},
            {"bar_type": "1min"},
        ),
        (
            datetime(2024, 1, 1, 9, 31),
            {"AAPL": {"open": 150.5, "high": 151.5, "low": 150.0, "close": 151.0}},
            {"bar_type": "1min"},
        ),
        (
            datetime(2024, 1, 1, 9, 32),
            {"AAPL": {"open": 151.0, "high": 152.0, "low": 151.0, "close": 151.5}},
            {"bar_type": "1min"},
        ),
    ]
    feed = MockDataFeed(bars)

    engine = LiveEngine(strategy, broker, feed)
    await engine.connect()
    await engine.run()

    # All bars processed
    assert len(strategy.on_data_calls) == 3
    assert strategy.on_data_calls[0][0] == datetime(2024, 1, 1, 9, 30)
    assert strategy.on_data_calls[1][0] == datetime(2024, 1, 1, 9, 31)
    assert strategy.on_data_calls[2][0] == datetime(2024, 1, 1, 9, 32)

    # Stats updated
    assert engine.stats["bar_count"] == 3
    assert engine.stats["error_count"] == 0
    assert engine.stats["last_bar_time"] == datetime(2024, 1, 1, 9, 32)


@pytest.mark.asyncio
async def test_strategy_receives_wrapper():
    """Test strategy receives ThreadSafeBrokerWrapper, not raw broker."""
    strategy = RecordingStrategy()
    broker = MockAsyncBroker()
    bars = [
        (
            datetime(2024, 1, 1, 9, 30),
            {"AAPL": {"close": 150.0}},
            {},
        )
    ]
    feed = MockDataFeed(bars)

    engine = LiveEngine(strategy, broker, feed)
    await engine.connect()
    await engine.run()

    # Strategy received wrapper
    assert strategy.broker_ref is not None
    assert strategy.broker_ref is engine._wrapped_broker
    assert strategy.broker_ref is not broker


@pytest.mark.asyncio
async def test_graceful_shutdown_via_stop():
    """Test engine stops gracefully when stop() called."""
    strategy = RecordingStrategy()
    broker = MockAsyncBroker()

    # Long-running feed (100 minutes worth of bars)
    bars = [
        (datetime(2024, 1, 1, 9 + i // 60, i % 60), {"AAPL": {"close": 150.0}}, {})
        for i in range(30, 130)  # 9:30 to 11:10
    ]
    feed = MockDataFeed(bars, delay=0.05)

    engine = LiveEngine(strategy, broker, feed)
    await engine.connect()

    # Start engine and stop after 0.1s
    async def stop_soon():
        await asyncio.sleep(0.15)  # Let a few bars process
        await engine.stop()

    await asyncio.gather(engine.run(), stop_soon())

    # Engine stopped early (not all 100 bars processed)
    assert strategy.on_data_calls  # Some bars processed
    assert len(strategy.on_data_calls) < 100  # But not all
    assert strategy.on_end_called is True


@pytest.mark.asyncio
async def test_error_handling_continue():
    """Test engine continues on strategy error when halt_on_error=False."""
    strategy = ErrorStrategy(error_on_bar=2)  # Raise error on 2nd bar
    broker = MockAsyncBroker()
    bars = [
        (datetime(2024, 1, 1, 9, 30), {"AAPL": {"close": 150.0}}, {}),
        (datetime(2024, 1, 1, 9, 31), {"AAPL": {"close": 151.0}}, {}),
        (datetime(2024, 1, 1, 9, 32), {"AAPL": {"close": 152.0}}, {}),
    ]
    feed = MockDataFeed(bars)

    errors: list[Exception] = []

    def error_handler(e: Exception, timestamp: datetime, data: dict) -> None:
        errors.append(e)

    engine = LiveEngine(strategy, broker, feed, on_error=error_handler, halt_on_error=False)
    await engine.connect()
    await engine.run()

    # All bars processed despite error
    assert strategy.call_count == 3

    # Error captured
    assert len(errors) == 1
    assert isinstance(errors[0], ValueError)
    assert str(errors[0]) == "Test error"

    # Stats updated
    assert engine.stats["bar_count"] == 3
    assert engine.stats["error_count"] == 1


@pytest.mark.asyncio
async def test_error_handling_halt():
    """Test engine halts on strategy error when halt_on_error=True."""
    strategy = ErrorStrategy(error_on_bar=2)  # Raise error on 2nd bar
    broker = MockAsyncBroker()
    bars = [
        (datetime(2024, 1, 1, 9, 30), {"AAPL": {"close": 150.0}}, {}),
        (datetime(2024, 1, 1, 9, 31), {"AAPL": {"close": 151.0}}, {}),
        (datetime(2024, 1, 1, 9, 32), {"AAPL": {"close": 152.0}}, {}),
    ]
    feed = MockDataFeed(bars)

    errors: list[Exception] = []

    def error_handler(e: Exception, timestamp: datetime, data: dict) -> None:
        errors.append(e)

    engine = LiveEngine(strategy, broker, feed, on_error=error_handler, halt_on_error=True)
    await engine.connect()
    await engine.run()

    # Engine stopped after error
    assert strategy.call_count == 2  # Only first 2 bars

    # Error captured
    assert len(errors) == 1

    # Stats show partial processing
    assert engine.stats["bar_count"] == 2
    assert engine.stats["error_count"] == 1


@pytest.mark.asyncio
async def test_stats_property():
    """Test stats property returns correct info."""
    strategy = RecordingStrategy()
    broker = MockAsyncBroker()
    bars = [
        (datetime(2024, 1, 1, 9, 30), {"AAPL": {"close": 150.0}}, {}),
        (datetime(2024, 1, 1, 9, 31), {"AAPL": {"close": 151.0}}, {}),
    ]
    feed = MockDataFeed(bars)

    engine = LiveEngine(strategy, broker, feed)
    await engine.connect()

    # Before run
    stats = engine.stats
    assert stats["running"] is False
    assert stats["bar_count"] == 0
    assert stats["error_count"] == 0
    assert stats["last_bar_time"] is None

    await engine.run()

    # After run
    stats = engine.stats
    assert stats["running"] is False
    assert stats["bar_count"] == 2
    assert stats["error_count"] == 0
    assert stats["last_bar_time"] == datetime(2024, 1, 1, 9, 31)


@pytest.mark.asyncio
async def test_run_without_connect_raises():
    """Test run() raises if connect() not called first."""
    strategy = RecordingStrategy()
    broker = MockAsyncBroker()
    feed = MockDataFeed([])

    engine = LiveEngine(strategy, broker, feed)

    with pytest.raises(RuntimeError, match="Call connect\\(\\) before run\\(\\)"):
        await engine.run()


@pytest.mark.asyncio
async def test_disconnect_on_stop():
    """Test broker disconnects when engine stops."""
    strategy = RecordingStrategy()
    broker = MockAsyncBroker()
    feed = MockDataFeed([])

    engine = LiveEngine(strategy, broker, feed)
    await engine.connect()

    assert broker.is_connected is True

    await engine.run()
    await engine.stop()

    # Broker disconnected
    assert broker.is_connected is False


@pytest.mark.asyncio
async def test_feed_stops_on_engine_stop():
    """Test data feed stops when engine stops."""
    strategy = RecordingStrategy()
    broker = MockAsyncBroker()
    feed = MockDataFeed([])

    engine = LiveEngine(strategy, broker, feed)
    await engine.connect()

    assert feed._stopped is False

    await engine.stop()

    # Feed stopped
    assert feed._stopped is True


@pytest.mark.asyncio
async def test_shadow_mode_end_to_end_uses_virtual_portfolio(tmp_path):
    """Test LiveEngine + SafeBroker shadow mode without real broker orders."""
    strategy = ShadowEntryStrategy()
    broker = MockAsyncBroker()
    bars = [
        (datetime(2024, 1, 1, 9, 30), {"AAPL": {"close": 150.0}}, {}),
        (datetime(2024, 1, 1, 9, 31), {"AAPL": {"close": 151.0}}, {}),
    ]
    feed = MockDataFeed(bars)
    safe_broker = SafeBroker(
        broker,
        LiveRiskConfig(
            shadow_mode=True,
            dedup_window_seconds=0.0,
            max_position_value=50_000.0,
            max_order_value=10_000.0,
            state_file=str(tmp_path / "shadow_mode_state.json"),
        ),
    )

    engine = LiveEngine(strategy, safe_broker, feed)
    await engine.connect()
    await engine.run()

    virtual_position = safe_broker._virtual_portfolio.positions.get("AAPL")

    assert strategy.order_attempts == 1
    assert broker.submit_calls == 0
    assert virtual_position is not None
    assert virtual_position.quantity == 10
