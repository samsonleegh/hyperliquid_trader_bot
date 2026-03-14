# Hyperliquid Telegram Trading Bot

A Python-based Telegram bot that provides technical analysis-driven trade recommendations and executes trades on [Hyperliquid](https://hyperliquid.xyz) (spot and perpetual futures). Supports manual confirmation and auto-execution modes with configurable risk management.

## Features

- **Technical Analysis Signals** — EMA crossovers, RSI, MACD, support/resistance, volume spikes
- **Trade Execution** — Market and limit orders with automatic SL/TP attachment
- **Risk Management** — Position limits, exposure caps, leverage checks, daily loss limits
- **Portfolio Dashboard** — Real-time positions, PnL tracking, trade history
- **Scheduled Scanning** — Periodic watchlist scans with signal delivery via Telegram
- **Dual Execution Modes** — Manual confirmation or auto-execute with risk gates

## Quick Start

### Prerequisites

- Python 3.13+
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- A Hyperliquid wallet (private key + address)

### Installation

```bash
git clone <repo-url>
cd hyperliquid-telegram-bot
pip install -r requirements.txt
```

### Configuration

```bash
cp .env.example .env
```

Edit `.env` with your credentials:

```env
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_ALLOWED_USER_IDS=123456789
HL_PRIVATE_KEY=your_private_key
HL_WALLET_ADDRESS=your_wallet_address
HL_TESTNET=true
```

### Run

```bash
python -m src.main
```

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message and authorization check |
| `/help` | List all commands |
| `/scan [symbol]` | Run TA scan on a symbol or entire watchlist |
| `/trade <symbol> <side> <size>` | Place a market trade |
| `/limit <symbol> <side> <price> <size>` | Place a limit order |
| `/close <symbol>` | Close an open position |
| `/cancel [order_id]` | Cancel pending order(s) |
| `/positions` | Show open positions with unrealized PnL |
| `/balance` | Show account balances |
| `/history [n]` | Show last N trades |
| `/pnl [period]` | PnL summary (today/week/month/all) |
| `/watchlist [add\|remove <symbol>]` | Manage watchlist |
| `/alerts` | View active alerts |
| `/mode [auto\|manual]` | Switch execution mode |
| `/risk` | View/edit risk parameters |
| `/status` | Bot health and connection status |

## Risk Management

| Parameter | Default | Description |
|-----------|---------|-------------|
| Max position size | $500 | Per-trade USD limit |
| Max total exposure | $2,000 | Combined open positions cap |
| Max leverage | 5x | Leverage ceiling |
| Default stop-loss | 3% | Auto-attached to every trade |
| Default take-profit | 6% | Auto-attached to every trade |
| Daily loss limit | $200 | Pauses auto-execution when hit |
| Max open positions | 5 | Concurrent position limit |

All parameters are configurable via the `/risk` command.

## Architecture

```
src/
├── config.py              # Settings from .env
├── main.py                # Entry point
├── scheduler.py           # Periodic tasks
├── bot/                   # Telegram interface
│   ├── handlers.py        # Command & callback handlers
│   ├── keyboards.py       # Inline keyboards
│   └── formatters.py      # Message formatting
├── exchange/              # Hyperliquid integration
│   ├── client.py          # API wrapper
│   ├── orders.py          # Order execution
│   └── market_data.py     # Price feeds & candles
├── analysis/              # Technical analysis
│   ├── indicators.py      # TA indicators
│   └── signals.py         # Signal generation
├── risk/                  # Risk management
│   └── manager.py         # Position & exposure checks
└── db/                    # Persistence
    ├── models.py          # Schema definitions
    └── repository.py      # CRUD operations
```

## Scheduled Tasks

| Task | Interval | Description |
|------|----------|-------------|
| Market scan | 15 min | TA scan on watchlist, signal generation |
| Position monitor | 30 sec | Check prices against SL/TP levels |
| Daily PnL rollup | Midnight | Aggregate daily PnL summary |
| Health check | 60 sec | Verify API connectivity |

## Security

- **User allowlist** — Only configured Telegram user IDs can interact with the bot
- **Testnet first** — Defaults to Hyperliquid testnet until explicitly switched
- **Confirmation required** — Destructive actions (close-all, mode changes) require inline confirmation
- **Secrets in .env** — Private keys are never logged or displayed

## License

MIT
