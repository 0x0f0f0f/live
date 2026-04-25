# Shared Project Context: ml4t-live

## Package

- Package name: `ml4t-live`
- Import path: `ml4t.live`
- Purpose: live trading with zero-code migration from `ml4t-backtest`

## Core Surface

- `engine.py` - live engine orchestration
- `wrappers.py` - sync to async strategy bridge
- `safety.py` - risk controls, shadow mode, VirtualPortfolio
- `brokers/` - Interactive Brokers and Alpaca adapters
- `feeds/` - Alpaca, IB, Databento, CCXT, OKX, and aggregation

## Workflow

```bash
uv sync
uv run ruff check src/
uv run ruff format src/
uv run ty check
uv run pytest tests/ -q
```

## Safety

- Start with `shadow_mode=True`
- Keep public symbols stable for book and notebook consumers
- Treat docs, examples, and tests as part of the shipped library surface
