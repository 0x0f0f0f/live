"""Minimal CLI entry point for ml4t-live."""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any, cast

from ml4t.backtest import Strategy
from ml4t.backtest.types import Order, OrderSide, OrderType, Position

from ml4t.live import __version__
from ml4t.live.brokers.alpaca import AlpacaBroker
from ml4t.live.brokers.ib import IBBroker
from ml4t.live.engine import LiveEngine
from ml4t.live.feeds.alpaca_feed import AlpacaDataFeed
from ml4t.live.feeds.okx_feed import OKXFundingFeed
from ml4t.live.protocols import AsyncBrokerProtocol, DataFeedProtocol
from ml4t.live.safety import LiveRiskConfig, RiskState, SafeBroker

DEFAULT_STATE_FILE = ".ml4t_risk_state.json"


@dataclass
class BrokerProbeResult:
    status: str
    detail: str
    positions: dict[str, float]
    pending_orders: list[dict[str, Any]]


@dataclass
class OrderIntentRecord:
    created_at: datetime
    asset: str
    side: str
    quantity: float
    order_type: str


class NullBroker:
    """Broker stub used by the shadow CLI command."""

    def __init__(self) -> None:
        self._connected = False
        self._positions: dict[str, Position] = {}
        self._pending_orders: list[Order] = []
        self._cash = 100_000.0

    @property
    def positions(self) -> dict[str, Position]:
        return dict(self._positions)

    @property
    def pending_orders(self) -> list[Order]:
        return list(self._pending_orders)

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def is_connected_async(self) -> bool:
        return self._connected

    async def get_positions_async(self) -> dict[str, Position]:
        return dict(self._positions)

    async def get_pending_orders_async(self) -> list[Order]:
        return list(self._pending_orders)

    async def get_position_async(self, asset: str) -> Position | None:
        return self._positions.get(asset)

    async def get_account_value_async(self) -> float:
        return self._cash

    async def get_cash_async(self) -> float:
        return self._cash

    async def submit_order_async(
        self,
        asset: str,
        quantity: float,
        side: OrderSide | None = None,
        order_type: OrderType = OrderType.MARKET,
        limit_price: float | None = None,
        stop_price: float | None = None,
        **kwargs: Any,
    ) -> Order:
        raise RuntimeError("NullBroker should never receive live orders in shadow mode")

    async def cancel_order_async(self, order_id: str) -> bool:
        return False

    async def close_position_async(self, asset: str) -> Order | None:
        return None


class IntentPrintingSafeBroker(SafeBroker):
    """SafeBroker that surfaces order intents to stdout for shadow runs."""

    def __init__(self, broker: AsyncBrokerProtocol, config: LiveRiskConfig):
        super().__init__(broker, config)
        self._recent_order_intents: list[OrderIntentRecord] = []

    @property
    def recent_order_intents(self) -> list[OrderIntentRecord]:
        return list(self._recent_order_intents)

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
        resolved_side = side
        resolved_quantity = quantity
        if resolved_side is None:
            resolved_side = OrderSide.BUY if quantity > 0 else OrderSide.SELL
            resolved_quantity = abs(quantity)

        self._recent_order_intents.append(
            OrderIntentRecord(
                created_at=datetime.now(UTC),
                asset=asset,
                side=resolved_side.value,
                quantity=float(resolved_quantity),
                order_type=order_type.value,
            )
        )
        self._recent_order_intents = self._recent_order_intents[-10:]

        print(
            "order_intent"
            f" asset={asset}"
            f" side={resolved_side.value}"
            f" quantity={resolved_quantity}"
            f" type={order_type.value}"
        )
        return await super().submit_order_async(
            asset,
            quantity,
            side,
            order_type,
            limit_price,
            stop_price,
            **kwargs,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ml4t-live", description="ml4t-live command line")
    parser.add_argument(
        "--version",
        action="version",
        version=f"ml4t-live {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status", help="show risk state and broker connectivity")
    status.add_argument(
        "--state-file",
        default=DEFAULT_STATE_FILE,
        help="path to the persisted risk-state JSON file",
    )

    shadow = subparsers.add_parser("shadow", help="run a strategy in shadow mode")
    shadow.add_argument("strategy", type=Path, help="path to a Python strategy file")
    shadow.add_argument(
        "--feed",
        choices=("okx", "alpaca"),
        default="okx",
        help="data feed to use during the shadow run",
    )
    shadow.add_argument(
        "--duration",
        type=int,
        default=60,
        help="shadow run duration in seconds",
    )
    shadow.add_argument(
        "--state-file",
        default=DEFAULT_STATE_FILE,
        help="path to the persisted risk-state JSON file",
    )

    return parser


def _load_strategy_module(path: Path) -> ModuleType:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"Strategy file not found: {resolved}")

    spec = importlib.util.spec_from_file_location("ml4t_live_cli_strategy", resolved)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load strategy module from {resolved}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_strategy_instance(module: ModuleType) -> Strategy:
    factory = getattr(module, "create_strategy", None)
    if callable(factory):
        strategy = factory()
        if not isinstance(strategy, Strategy):
            raise TypeError("create_strategy() must return a Strategy instance")
        return strategy

    strategy_class = getattr(module, "STRATEGY_CLASS", None)
    if isinstance(strategy_class, type) and issubclass(strategy_class, Strategy):
        return strategy_class()

    candidates = [
        value
        for value in vars(module).values()
        if isinstance(value, type)
        and issubclass(value, Strategy)
        and value is not Strategy
        and value.__module__ == module.__name__
    ]
    if len(candidates) != 1:
        raise RuntimeError(
            "Strategy file must define create_strategy(), STRATEGY_CLASS, or exactly one"
            " Strategy subclass"
        )
    return candidates[0]()


def _module_symbols(module: ModuleType, feed_name: str) -> list[str]:
    candidate = getattr(module, "FEED_SYMBOLS", None)
    if candidate is None:
        candidate = getattr(module, "SYMBOLS", None)

    if candidate is None:
        return ["SPY"] if feed_name == "alpaca" else ["BTC-USDT-SWAP"]

    if not isinstance(candidate, (list, tuple)) or not all(
        isinstance(symbol, str) for symbol in candidate
    ):
        raise TypeError("SYMBOLS or FEED_SYMBOLS must be a list[str] or tuple[str, ...]")
    return list(candidate)


async def _make_feed(feed_name: str, module: ModuleType) -> Any:
    symbols = _module_symbols(module, feed_name)
    if feed_name == "alpaca":
        api_key = os.environ.get("ALPACA_API_KEY")
        secret_key = os.environ.get("ALPACA_SECRET_KEY")
        if not api_key or not secret_key:
            raise RuntimeError(
                "ALPACA_API_KEY and ALPACA_SECRET_KEY must be set for --feed alpaca"
            )
        data_type = getattr(module, "ALPACA_DATA_TYPE", "bars")
        feed = getattr(module, "ALPACA_FEED", "iex")
        return AlpacaDataFeed(
            api_key=api_key,
            secret_key=secret_key,
            symbols=symbols,
            data_type=data_type,
            feed=feed,
        )

    timeframe = getattr(module, "TIMEFRAME", "1m")
    poll_interval = float(getattr(module, "POLL_INTERVAL_SECONDS", 5.0))
    return OKXFundingFeed(
        symbols=symbols,
        timeframe=timeframe,
        poll_interval_seconds=poll_interval,
    )


def _format_positions(positions: dict[str, Position] | dict[str, float]) -> str:
    if not positions:
        return "flat"

    formatted: list[str] = []
    for asset, value in sorted(positions.items()):
        quantity = value.quantity if isinstance(value, Position) else value
        formatted.append(f"{asset}:{quantity:g}")
    return ", ".join(formatted)


def _format_pending_orders(pending_orders: list[dict[str, Any]]) -> str:
    if not pending_orders:
        return "none"
    return ", ".join(
        f"{order['asset']}:{order['side']}:{order['quantity']:g}:{order['order_type']}"
        for order in pending_orders
    )


def _format_recent_order_intents(intents: list[OrderIntentRecord]) -> str:
    if not intents:
        return "none"
    return ", ".join(
        f"{intent.asset}:{intent.side}:{intent.quantity:g}:{intent.order_type}"
        for intent in intents[-3:]
    )


def _format_timestamp(timestamp: datetime | None) -> str:
    if timestamp is None:
        return "n/a"
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(UTC).isoformat()


def _format_age(age_seconds: float | None) -> str:
    if age_seconds is None:
        return "n/a"
    return f"{age_seconds:.1f}s"


def _classify_status(
    state: RiskState | None,
    probes: list[BrokerProbeResult],
) -> tuple[str, str]:
    if state is not None and state.kill_switch_activated:
        return "degraded", "kill switch active"
    if any(probe.status == "ok" for probe in probes):
        if any(probe.status == "error" for probe in probes):
            return "degraded", "at least one broker probe failed"
        return "ok", "broker connectivity available"
    if any(probe.status == "error" for probe in probes):
        return "unavailable", "broker probes failed"
    return "unavailable", "no live broker probes configured"


async def _print_shadow_heartbeat(
    duration: int,
    feed_name: str,
    safe_broker: IntentPrintingSafeBroker,
    engine: LiveEngine,
) -> None:
    elapsed = 0
    while elapsed < duration:
        await asyncio.sleep(5)
        elapsed += 5
        runtime = engine.runtime_status()
        print(
            f"[{elapsed:>3}s] shadow feed={feed_name}"
            f" health={runtime['health']}"
            f" recovery={runtime['recovery_requested'] or 'none'}"
            f" attempts={runtime['recovery_attempts']}"
            f" broker={runtime['broker_connected']}"
            f" session={runtime['session_state']}"
            f" last_bar_age={_format_age(runtime['last_bar_age_seconds'])}"
            f" orders={safe_broker._state.orders_placed}"
            f" positions={_format_positions(safe_broker.positions)}"
            f" recent_intents={_format_recent_order_intents(safe_broker.recent_order_intents)}"
        )


async def _stop_engine_after(duration: int, engine: LiveEngine) -> None:
    await asyncio.sleep(duration)
    await engine.stop()


async def _run_shadow_command(args: argparse.Namespace) -> int:
    module = _load_strategy_module(args.strategy)
    strategy = _load_strategy_instance(module)
    feed = await _make_feed(args.feed, module)

    broker = IntentPrintingSafeBroker(
        NullBroker(),
        LiveRiskConfig(
            shadow_mode=True,
            state_file=str(Path(args.state_file).expanduser().resolve()),
        ),
    )
    engine = LiveEngine(
        strategy=strategy,
        broker=cast(AsyncBrokerProtocol, broker),
        feed=cast(DataFeedProtocol, feed),
        feed_silence_seconds=30.0,
    )

    print(
        f"Starting shadow run for {args.duration}s with feed={args.feed}"
        f" strategy={args.strategy.resolve()}"
    )

    await engine.connect()
    stop_task = asyncio.create_task(_stop_engine_after(args.duration, engine))
    heartbeat_task = asyncio.create_task(
        _print_shadow_heartbeat(args.duration, args.feed, broker, engine)
    )

    try:
        await engine.run()
    finally:
        heartbeat_task.cancel()
        stop_task.cancel()
        await asyncio.gather(heartbeat_task, stop_task, return_exceptions=True)
        await engine.stop()

    runtime = engine.runtime_status()
    print(
        "Completed shadow run."
        f" final_positions={_format_positions(broker.positions)}"
        f" final_health={runtime['health']}"
        f" last_bar_time={_format_timestamp(runtime['last_bar_time'])}"
        f" last_bar_age={_format_age(runtime['last_bar_age_seconds'])}"
        f" orders={broker._state.orders_placed}"
        f" daily_loss={broker._state.daily_loss:,.2f}"
        f" kill_switch={broker._state.kill_switch_activated}"
        f" recent_intents={_format_recent_order_intents(broker.recent_order_intents)}"
    )
    return 0


def _serialize_positions(positions: dict[str, Position]) -> dict[str, float]:
    return {asset: float(position.quantity) for asset, position in sorted(positions.items())}


def _serialize_pending_orders(orders: list[Order]) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for order in orders:
        entry = {
            "asset": order.asset,
            "side": order.side.value,
            "quantity": float(order.quantity),
            "order_type": order.order_type.value,
        }
        if order.limit_price is not None:
            entry["limit_price"] = float(order.limit_price)
        if order.stop_price is not None:
            entry["stop_price"] = float(order.stop_price)
        serialized.append(entry)
    return serialized


async def _probe_alpaca() -> BrokerProbeResult:
    api_key = os.environ.get("ALPACA_API_KEY")
    secret_key = os.environ.get("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        return BrokerProbeResult(
            status="skipped",
            detail="set ALPACA_API_KEY and ALPACA_SECRET_KEY to enable this check",
            positions={},
            pending_orders=[],
        )

    broker = AlpacaBroker(api_key=api_key, secret_key=secret_key, paper=True)
    try:
        await broker.connect()
        cash = await broker.get_cash_async()
        positions = _serialize_positions(await broker.get_positions_async())
        pending_orders = _serialize_pending_orders(await broker.get_pending_orders_async())
        return BrokerProbeResult(
            status="ok",
            detail=f"paper account reachable, cash=${cash:,.2f}",
            positions=positions,
            pending_orders=pending_orders,
        )
    except Exception as exc:
        return BrokerProbeResult(
            status="error",
            detail=str(exc),
            positions={},
            pending_orders=[],
        )
    finally:
        try:
            await broker.disconnect()
        except Exception:
            pass


async def _probe_ib() -> BrokerProbeResult:
    host = os.environ.get("IB_HOST") or os.environ.get("ML4T_IB_HOST")
    port = os.environ.get("IB_PORT") or os.environ.get("ML4T_IB_PORT")
    client_id = os.environ.get("IB_CLIENT_ID") or os.environ.get("ML4T_IB_CLIENT_ID")

    if host is None and port is None and client_id is None:
        return BrokerProbeResult(
            status="skipped",
            detail="set IB_HOST or IB_PORT to enable this check",
            positions={},
            pending_orders=[],
        )

    broker = IBBroker(
        host=host or "127.0.0.1",
        port=int(port or 7497),
        client_id=int(client_id or 1999),
    )
    try:
        await broker.connect()
        equity = await broker.get_account_value_async()
        positions = _serialize_positions(await broker.get_positions_async())
        pending_orders = _serialize_pending_orders(await broker.get_pending_orders_async())
        return BrokerProbeResult(
            status="ok",
            detail=f"connected to {broker._host}:{broker._port}, equity=${equity:,.2f}",
            positions=positions,
            pending_orders=pending_orders,
        )
    except Exception as exc:
        return BrokerProbeResult(
            status="error",
            detail=str(exc),
            positions={},
            pending_orders=[],
        )
    finally:
        try:
            await broker.disconnect()
        except Exception:
            pass


async def _run_status_command(args: argparse.Namespace) -> int:
    state_path = Path(args.state_file).expanduser().resolve()
    state = RiskState.load(str(state_path))

    print(f"ml4t-live {__version__}")
    print(f"risk_state_file: {state_path}")
    if state is None:
        print("risk_state: missing")
    else:
        print(
            "risk_state:"
            f" date={state.date}"
            f" orders_placed={state.orders_placed}"
            f" daily_loss={state.daily_loss:,.2f}"
            f" high_water_mark={state.high_water_mark:,.2f}"
            f" kill_switch={state.kill_switch_activated}"
        )
        if state.session_start_equity is not None:
            print(f"session_start_equity: {state.session_start_equity:,.2f}")
        if state.kill_switch_reason:
            print(f"kill_switch_reason: {state.kill_switch_reason}")
        print(f"persisted_positions: {_format_positions(state.persisted_positions)}")
        print(f"persisted_pending_orders: {_format_pending_orders(state.persisted_pending_orders)}")

    alpaca = await _probe_alpaca()
    ib = await _probe_ib()
    summary, detail = _classify_status(state, [alpaca, ib])
    print(f"status_summary: {summary} - {detail}")

    print(f"alpaca: {alpaca.status} - {alpaca.detail}")
    if alpaca.positions or alpaca.pending_orders:
        print(f"alpaca_positions: {_format_positions(alpaca.positions)}")
        print(f"alpaca_pending_orders: {_format_pending_orders(alpaca.pending_orders)}")

    print(f"ib: {ib.status} - {ib.detail}")
    if ib.positions or ib.pending_orders:
        print(f"ib_positions: {_format_positions(ib.positions)}")
        print(f"ib_pending_orders: {_format_pending_orders(ib.pending_orders)}")
    return 0


def run_cli(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "status":
        return asyncio.run(_run_status_command(args))
    if args.command == "shadow":
        return asyncio.run(_run_shadow_command(args))

    parser.error(f"Unknown command: {args.command}")
    return 2


def app() -> None:
    raise SystemExit(run_cli())


if __name__ == "__main__":
    app()
