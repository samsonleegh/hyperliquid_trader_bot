# Hyperliquid Telegram Trading Bot

## Project Overview
A Python-based Telegram bot providing TA-driven trade signals and execution on Hyperliquid (spot + perpetual futures). Supports manual confirmation and auto-execution modes with configurable risk management.

## Tech Stack
- Python 3.13, async throughout
- `python-telegram-bot` v20+ (async Telegram framework)
- `hyperliquid-python-sdk` (exchange API)
- `pandas` + `pandas-ta` (technical analysis)
- `aiosqlite` (SQLite database)
- `APScheduler` (periodic tasks via job_queue)
- `python-dotenv` (config from `.env`)

## Project Structure
```
src/
├── config.py              # Settings dataclass from .env
├── main.py                # Entry point, wires services, runs polling
├── scheduler.py           # Periodic tasks (scan, monitor, PnL rollup, health)
├── bot/
│   ├── handlers.py        # 16 Telegram command + callback handlers
│   ├── keyboards.py       # Inline keyboard builders
│   └── formatters.py      # Message formatting with emoji/unicode
├── exchange/
│   ├── client.py          # HyperliquidClient (async wrapper over sync SDK)
│   ├── orders.py          # OrderManager (market, limit, SL/TP, cancel)
│   └── market_data.py     # MarketDataFetcher (candles, prices, orderbook)
├── analysis/
│   ├── indicators.py      # TechnicalIndicators (EMA, RSI, MACD, S/R, volume)
│   └── signals.py         # SignalGenerator + scan_symbol()
├── risk/
│   └── manager.py         # RiskManager (position/exposure/leverage/daily checks)
└── db/
    ├── models.py          # Table DDL + init_db()
    └── repository.py      # Repository class with async CRUD
```

## Key Commands
- Run: `python -m src.main`
- Install deps: `pip install -r requirements.txt`
- Config: copy `.env.example` to `.env` and fill in credentials

## Architecture Patterns
- All handlers use `@authorized` decorator checking `TELEGRAM_ALLOWED_USER_IDS`
- Services stored in `application.bot_data` dict, accessed via helper functions
- Hyperliquid SDK is synchronous; wrapped with `asyncio.to_thread()` for async
- Risk checks run before every trade execution
- Signals require 2+ agreeing indicators to generate

## Important Conventions
- Use `src.*` imports (package-style)
- All DB operations go through `Repository` class
- All exchange calls go through `HyperliquidClient` (never call SDK directly)
- Format user-facing messages via `formatters.py`
- Callback data format: `"action:param1:param2"`
