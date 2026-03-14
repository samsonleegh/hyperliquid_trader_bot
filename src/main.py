"""Entry point for the Hyperliquid Telegram Trading Bot."""

import logging
import os
from datetime import time as dt_time

from telegram import BotCommand
from telegram.ext import ApplicationBuilder

from src.bot.handlers import register_handlers
from src.config import settings
from src.db.models import init_db
from src.db.repository import Repository
from src.exchange.client import HyperliquidClient
from src.exchange.market_data import MarketDataFetcher
from src.exchange.orders import OrderManager
from src.risk.manager import RiskManager
from src.scheduler import daily_pnl_rollup, health_check, monitor_positions, scan_markets


def setup_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


async def post_init(application) -> None:
    """Initialize services after the application starts."""
    # Ensure data directory exists
    os.makedirs(os.path.dirname(settings.db_path) or ".", exist_ok=True)

    # Database
    await init_db(settings.db_path)
    repo = Repository(settings.db_path)
    await repo.connect()

    # Exchange
    client = HyperliquidClient(
        private_key=settings.hl_private_key,
        wallet_address=settings.hl_wallet_address,
        testnet=settings.hl_testnet,
    )
    order_manager = OrderManager(client)
    market_data = MarketDataFetcher(client)
    risk_manager = RiskManager(repo, client)

    # Store in bot_data for access in handlers
    application.bot_data["repo"] = repo
    application.bot_data["client"] = client
    application.bot_data["order_manager"] = order_manager
    application.bot_data["market_data"] = market_data
    application.bot_data["risk_manager"] = risk_manager

    # Schedule periodic tasks
    job_queue = application.job_queue
    job_queue.run_repeating(scan_markets, interval=settings.scan_interval_minutes * 60, first=10)
    job_queue.run_repeating(monitor_positions, interval=30, first=5)
    job_queue.run_repeating(health_check, interval=60, first=15)
    job_queue.run_daily(daily_pnl_rollup, time=dt_time(hour=0, minute=0))

    # Register commands for Telegram auto-complete menu
    await application.bot.set_my_commands([
        BotCommand("scan", "Run TA scan on symbol or watchlist"),
        BotCommand("trade", "Place trade: /trade <symbol> <side> <size>"),
        BotCommand("limit", "Limit order: /limit <symbol> <side> <price> <size>"),
        BotCommand("close", "Close position: /close <symbol>"),
        BotCommand("cancel", "Cancel order(s)"),
        BotCommand("positions", "Show open positions"),
        BotCommand("balance", "Show account balance"),
        BotCommand("history", "Show trade history"),
        BotCommand("pnl", "PnL summary: today/week/month/all"),
        BotCommand("watchlist", "Manage watchlist"),
        BotCommand("alerts", "View active alerts"),
        BotCommand("backtest", "Backtest: /backtest <symbol> [days] [breakdown]"),
        BotCommand("indicators", "Enable/disable indicators"),
        BotCommand("strategy", "Per-coin strategies with auto-execute"),
        BotCommand("mode", "Switch auto/manual mode"),
        BotCommand("risk", "View/edit risk settings"),
        BotCommand("status", "Bot health & connection status"),
        BotCommand("help", "List all commands"),
    ])

    logging.getLogger(__name__).info("Bot initialized successfully")


async def post_shutdown(application) -> None:
    """Clean up on shutdown."""
    repo = application.bot_data.get("repo")
    if repo:
        await repo.close()


def main() -> None:
    setup_logging()
    logger = logging.getLogger(__name__)
    logger.info("Starting Hyperliquid Trading Bot...")

    if not settings.telegram_bot_token:
        logger.error("TELEGRAM_BOT_TOKEN not set")
        return

    application = (
        ApplicationBuilder()
        .token(settings.telegram_bot_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    register_handlers(application)
    logger.info("Bot starting polling...")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
