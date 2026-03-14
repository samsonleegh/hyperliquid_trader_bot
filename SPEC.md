# Hyperliquid Telegram Trading Bot — Specification

## 1. Overview

An interactive Python-based Telegram bot that provides technical analysis-driven trade recommendations and executes trades on Hyperliquid (spot and perpetual futures). The bot supports both manual confirmation and auto-execution modes, with configurable risk management.

## 2. Tech Stack

| Component          | Choice                                      |
| ------------------- | -------------------------------------------- |
| Language            | Python 3.13                                  |
| Telegram framework  | `python-telegram-bot` (v20+, async)          |
| Hyperliquid SDK     | `hyperliquid-python-sdk`                     |
| Technical analysis  | `pandas-ta` + `pandas`                       |
| Database            | SQLite via `aiosqlite`                       |
| Task scheduling     | `APScheduler`                                |
| Config              | `.env` file via `python-dotenv`              |
| Deployment          | Systemd service on VPS (or manual)           |

## 3. Architecture

```
┌──────────────┐     ┌──────────────────┐     ┌───────────────────┐
│   Telegram   │◄───►│   Bot Core       │◄───►│  Hyperliquid API  │
│   (User)     │     │                  │     │  (REST + WS)      │
└──────────────┘     │  ┌────────────┐  │     └───────────────────┘
                     │  │ TA Engine  │  │
                     │  ├────────────┤  │     ┌───────────────────┐
                     │  │ Risk Mgr   │  │◄───►│  SQLite Database  │
                     │  ├────────────┤  │     └───────────────────┘
                     │  │ Order Exec │  │
                     │  ├────────────┤  │
                     │  │ Scheduler  │  │
                     │  └────────────┘  │
                     └──────────────────┘
```

### Module Breakdown

```
src/
├── bot/
│   ├── __init__.py
│   ├── handlers.py          # Telegram command & callback handlers
│   ├── keyboards.py         # Inline keyboard builders
│   └── formatters.py        # Message formatting (PnL, tables, etc.)
├── exchange/
│   ├── __init__.py
│   ├── client.py            # Hyperliquid API wrapper (spot + perps)
│   ├── orders.py            # Order creation, cancellation, modification
│   └── market_data.py       # Price feeds, candles, orderbook
├── analysis/
│   ├── __init__.py
│   ├── indicators.py        # TA indicators (RSI, MACD, MA, S/R)
│   └── signals.py           # Signal generation from indicators
├── risk/
│   ├── __init__.py
│   └── manager.py           # Position limits, SL/TP, daily loss tracking
├── db/
│   ├── __init__.py
│   ├── models.py            # Table definitions
│   └── repository.py        # CRUD operations
├── config.py                # Settings from .env
├── scheduler.py             # Periodic tasks (scan markets, check stops)
└── main.py                  # Entry point
```

## 4. Features

### 4.1 Telegram Commands

| Command             | Description                                           |
| -------------------- | ----------------------------------------------------- |
| `/start`             | Welcome message, verify authorization                 |
| `/help`              | List all commands                                     |
| `/scan [symbol]`     | Run TA scan on a symbol or all watchlist symbols       |
| `/trade <symbol> <side> <size>` | Place a trade (prompts confirmation in manual mode) |
| `/limit <symbol> <side> <price> <size>` | Place a limit order                    |
| `/close <symbol>`    | Close an open position                                |
| `/cancel [order_id]` | Cancel pending order(s)                               |
| `/positions`         | Show all open positions with unrealized PnL            |
| `/balance`           | Show account balances                                 |
| `/history [n]`       | Show last N trades                                    |
| `/pnl [period]`      | Show PnL summary (today / week / month / all)          |
| `/watchlist`         | View/manage watchlist                                  |
| `/alerts`            | View/manage price & signal alerts                      |
| `/mode [auto\|manual]` | Switch execution mode                               |
| `/risk`              | View/edit risk parameters                              |
| `/status`            | Bot health, uptime, connection status                  |

### 4.2 Trade Recommendations (Technical Analysis)

The bot periodically scans watchlist symbols and generates signals based on:

- **Moving Averages**: EMA 9/21/50/200 crossovers
- **RSI** (14): Overbought (>70) / oversold (<30) conditions
- **MACD**: Signal line crossovers, histogram divergence
- **Support/Resistance**: Based on recent swing highs/lows
- **Volume**: Unusual volume spikes

**Signal format sent to Telegram:**
```
📊 Signal: ETH-USD — LONG
━━━━━━━━━━━━━━━━
Indicators:
  • EMA 9/21 bullish cross ✓
  • RSI(14): 38 (neutral)
  • MACD: bullish crossover ✓
  • Above support: $3,420
Entry: $3,455 (market)
Stop-loss: $3,380 (-2.2%)
Take-profit: $3,600 (+4.2%)
Size: 0.5 ETH ($1,727)
━━━━━━━━━━━━━━━━
[Execute] [Modify] [Dismiss]
```

### 4.3 Trade Execution

**Manual mode (default):**
1. Bot sends signal with inline buttons: `[Execute]` `[Modify]` `[Dismiss]`
2. User taps `Execute` → bot places order on Hyperliquid
3. Bot confirms fill with entry price and attached SL/TP

**Auto-execute mode:**
1. Bot evaluates signal against risk rules
2. If within limits → executes immediately, sends notification
3. If exceeds limits → falls back to manual confirmation

### 4.4 Risk Management

| Parameter                | Default    | Configurable via |
| ------------------------- | ---------- | ---------------- |
| Max position size (USD)   | $500       | `/risk`          |
| Max total exposure (USD)  | $2,000     | `/risk`          |
| Max leverage              | 5x         | `/risk`          |
| Default stop-loss %       | 3%         | `/risk`          |
| Default take-profit %     | 6%         | `/risk`          |
| Daily loss limit (USD)    | $200       | `/risk`          |
| Max open positions        | 5          | `/risk`          |

**Behaviors:**
- Every trade gets an automatic SL/TP attached (user can override per trade)
- When daily loss limit is hit → auto-execution paused, user notified
- Position size auto-calculated from risk parameters if not specified

### 4.5 Portfolio Dashboard

`/positions` shows:
```
📂 Open Positions (3)
━━━━━━━━━━━━━━━━
ETH-PERP  LONG  0.5 ETH
  Entry: $3,455 | Mark: $3,510
  PnL: +$27.50 (+1.6%) 🟢
  SL: $3,380 | TP: $3,600

BTC-PERP  SHORT  0.01 BTC
  Entry: $68,200 | Mark: $67,800
  PnL: +$4.00 (+0.6%) 🟢
  SL: $69,500 | TP: $65,000
━━━━━━━━━━━━━━━━
Total PnL: +$31.50
Exposure: $2,405 / $5,000
```

## 5. Database Schema

### trades
| Column        | Type     | Description                     |
| ------------- | -------- | ------------------------------- |
| id            | INTEGER  | Primary key                     |
| symbol        | TEXT     | e.g. "ETH-PERP"                |
| side          | TEXT     | "long" or "short"              |
| size          | REAL     | Position size in base asset     |
| entry_price   | REAL     | Fill price                      |
| exit_price    | REAL     | Fill price on close (nullable)  |
| pnl           | REAL     | Realized PnL (nullable)        |
| status        | TEXT     | "open", "closed", "cancelled"  |
| order_type    | TEXT     | "market", "limit", "stop"      |
| sl_price      | REAL     | Stop-loss price                |
| tp_price      | REAL     | Take-profit price              |
| signal_source | TEXT     | Which signal triggered this     |
| created_at    | DATETIME | Order creation time             |
| closed_at     | DATETIME | Position close time (nullable)  |

### signals
| Column      | Type     | Description                     |
| ----------- | -------- | ------------------------------- |
| id          | INTEGER  | Primary key                     |
| symbol      | TEXT     | Trading pair                    |
| direction   | TEXT     | "long" or "short"              |
| indicators  | TEXT     | JSON of indicator values        |
| confidence  | REAL     | Signal strength 0-1             |
| acted_on    | BOOLEAN  | Whether user executed           |
| created_at  | DATETIME | Signal generation time          |

### risk_settings
| Column           | Type    | Description            |
| ---------------- | ------- | ---------------------- |
| key              | TEXT    | Setting name (PK)      |
| value            | REAL    | Setting value          |
| updated_at       | DATETIME|                        |

### daily_pnl
| Column     | Type     | Description               |
| ---------- | -------- | ------------------------- |
| date       | DATE     | Trading day (PK)          |
| realized   | REAL     | Realized PnL for the day  |
| trade_count| INTEGER  | Number of trades          |

## 6. Configuration (.env)

```env
# Telegram
TELEGRAM_BOT_TOKEN=<from @BotFather>
TELEGRAM_ALLOWED_USER_IDS=123456789  # comma-separated, restricts access

# Hyperliquid
HL_PRIVATE_KEY=<wallet private key>
HL_WALLET_ADDRESS=<wallet address>
HL_TESTNET=true  # start on testnet

# Bot Settings
DEFAULT_EXECUTION_MODE=manual  # manual | auto
SCAN_INTERVAL_MINUTES=15
LOG_LEVEL=INFO
DB_PATH=data/bot.db
```

## 7. Security

- **User allowlist**: Only `TELEGRAM_ALLOWED_USER_IDS` can interact with the bot
- **Private key**: Stored in `.env`, never logged or displayed
- **Testnet first**: Default to testnet until explicitly switched
- **Rate limiting**: Respect Hyperliquid API rate limits (1200 req/min)
- **Confirmation for destructive actions**: Close-all, mode changes require confirmation

## 8. Scheduled Tasks

| Task                     | Interval   | Description                                     |
| ------------------------- | ---------- | ----------------------------------------------- |
| Market scan               | 15 min     | Run TA on watchlist, generate signals            |
| Position monitor          | 30 sec     | Check mark prices against SL/TP levels           |
| Daily PnL rollup          | Midnight   | Aggregate daily PnL, reset daily loss counter    |
| Connection health check   | 60 sec     | Verify Hyperliquid API connectivity              |

## 9. Error Handling

- **API errors**: Retry with exponential backoff (max 3 retries), notify user on failure
- **Order rejections**: Parse Hyperliquid error, send human-readable message to Telegram
- **Connection loss**: Automatic reconnect, pause auto-execution, notify user
- **Unhandled exceptions**: Log full traceback, send brief error alert to Telegram

## 10. Development Phases

### Phase 1 — Foundation
- Project setup, config, database
- Hyperliquid client wrapper (connect, balances, market data)
- Basic Telegram bot with `/start`, `/help`, `/balance`, `/status`

### Phase 2 — Trading Core
- Order execution (market, limit, stop)
- Position tracking and `/positions`, `/close`, `/cancel`
- SL/TP attachment and monitoring

### Phase 3 — Technical Analysis
- Indicator calculations (EMA, RSI, MACD, S/R, volume)
- Signal generation and scoring
- Scheduled market scanning
- Signal delivery with inline buttons

### Phase 4 — Risk Management
- Position size limits and exposure tracking
- Daily loss limit enforcement
- Auto-execute mode with risk gates
- `/risk` command for parameter management

### Phase 5 — Dashboard & Polish
- `/pnl`, `/history` reports
- `/watchlist` and `/alerts` management
- Error handling hardening
- Testnet validation, then mainnet deployment
