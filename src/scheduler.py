"""Scheduled tasks: market scanning, position monitoring, PnL rollup, health checks."""

import logging
from datetime import date

from telegram.ext import ContextTypes

from src.analysis.signals import scan_symbol
from src.bot import formatters, keyboards
from src.config import settings

logger = logging.getLogger(__name__)


def _parse_strategy_indicators(strategy: dict | None) -> tuple[set[str] | None, list[list[str]] | None]:
    """Parse strategy indicators into (flat_set, combos) depending on format.

    Flat format: ["ema", "rsi"] -> ({"ema", "rsi"}, None)
    Combo format: [["fvg", "ad"], ["ema", "ad"]] -> (None, [["fvg","ad"], ["ema","ad"]])
    """
    if not strategy:
        return None, None
    indicators = strategy["indicators"]
    if not indicators:
        return None, None
    # Detect: if first element is a list, it's combo format
    if isinstance(indicators[0], list):
        return None, indicators
    return set(indicators), None


async def scan_markets(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Periodic TA scan on all watchlist symbols, using per-coin strategies when configured."""
    import math

    repo = context.bot_data["repo"]
    market_data = context.bot_data["market_data"]
    risk_mgr = context.bot_data["risk_manager"]
    order_mgr = context.bot_data["order_manager"]
    risk_settings = await risk_mgr.get_settings()

    symbols = await repo.get_watchlist()
    if not symbols:
        return

    # Load per-coin strategies
    strategies = await repo.get_all_strategies()
    strategy_map = {s["symbol"]: s for s in strategies}

    # Skip symbols that already have an open trade (prevent stacking)
    open_trades = await repo.get_open_trades()
    open_symbols = {t["symbol"] for t in open_trades}

    logger.info("Scanning %d watchlist symbols (%d with open positions)", len(symbols), len(open_symbols))
    for symbol in symbols:
        if symbol in open_symbols:
            logger.debug("Skipping %s — already has open position", symbol)
            continue

        try:
            strategy = strategy_map.get(symbol)
            active_indicators, combos = _parse_strategy_indicators(strategy)

            signal = await scan_symbol(symbol, market_data, risk_settings, active_indicators=active_indicators, combos=combos)
            if not signal or not signal.get("direction"):
                continue

            signal_id = await repo.create_signal(
                symbol=signal["symbol"],
                direction=signal["direction"],
                indicators=signal["indicators"],
                confidence=signal["confidence"],
                sl_price=signal.get("sl_price"),
                tp_price=signal.get("tp_price"),
            )

            # Auto-execute if strategy has auto_execute enabled
            if strategy and strategy["auto_execute"]:
                await _auto_execute_signal(
                    context, signal, signal_id, risk_mgr, order_mgr, market_data, repo, risk_settings,
                )
            else:
                text = formatters.format_signal(signal)
                for user_id in settings.telegram_allowed_user_ids:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=text,
                        reply_markup=keyboards.signal_keyboard(signal_id),
                    )
        except Exception:
            logger.exception("Error scanning %s", symbol)


async def _auto_execute_signal(
    context, signal, signal_id, risk_mgr, order_mgr, market_data, repo, risk_settings,
) -> None:
    """Auto-execute a signal from a strategy with auto_execute enabled."""
    symbol = signal["symbol"]
    side = signal["direction"]
    entry = signal["entry_price"]
    confidence = signal.get("confidence", 1.0)

    market_info = await market_data.get_market_info(symbol)
    sz_decimals = market_info.get("sz_decimals", 2)

    # Get leverage for position sizing (from open position or default)
    leverage = settings.max_leverage
    try:
        client = context.bot_data.get("client")
        if client:
            positions = await client.get_open_positions()
            pos = next((p for p in positions if p["symbol"] == symbol), None)
            if pos:
                leverage = pos.get("leverage", settings.max_leverage)
    except Exception:
        pass

    size = await risk_mgr.calculate_position_size(
        entry, risk_settings.get("max_position_size"), sz_decimals, confidence=confidence, leverage=leverage,
    )

    passed, failures = await risk_mgr.validate_trade(symbol, side, size, entry, leverage=leverage)
    if not passed:
        logger.warning("Auto-execute blocked for %s: %s", symbol, failures)
        for user_id in settings.telegram_allowed_user_ids:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"\u26a0\ufe0f Auto-trade blocked for {symbol}:\n" + "\n".join(failures),
            )
        return

    sl = signal.get("sl_price")
    tp = signal.get("tp_price")
    if not sl or not tp:
        sl, tp = risk_mgr.calculate_sl_tp(entry, side, sz_decimals=sz_decimals)

    # Validate SL/TP against entry price (S/R levels may be too close after price movement)
    min_distance = entry * 0.01
    if side.lower() == "long":
        if not sl or entry - sl < min_distance or not tp or tp - entry < min_distance:
            sl, tp = risk_mgr.calculate_sl_tp(entry, side, sz_decimals=sz_decimals)
    else:
        if not sl or sl - entry < min_distance or not tp or entry - tp < min_distance:
            sl, tp = risk_mgr.calculate_sl_tp(entry, side, sz_decimals=sz_decimals)

    result = await order_mgr.market_order(symbol, side, size, sl, tp, sz_decimals=sz_decimals)
    text = formatters.format_trade_result(result)
    auto_label = "\U0001f916 AUTO-EXECUTED\n"

    for user_id in settings.telegram_allowed_user_ids:
        await context.bot.send_message(chat_id=user_id, text=auto_label + text)

    if result.get("status") != "error":
        await repo.create_trade(symbol, side, size, entry, "market", sl, tp, f"auto:signal:{signal_id}")
        await repo.mark_signal_acted(signal_id)
        logger.info("Auto-executed %s %s %s (signal %d)", side, size, symbol, signal_id)


async def monitor_positions(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Check mark prices against SL/TP levels, execute closes if triggered."""
    repo = context.bot_data["repo"]
    client = context.bot_data["client"]
    order_mgr = context.bot_data["order_manager"]

    open_trades = await repo.get_open_trades()
    if not open_trades:
        return

    try:
        positions = await client.get_open_positions()
        price_map = {p["symbol"]: p["mark_price"] for p in positions}
    except Exception:
        logger.exception("Failed to fetch positions for monitoring")
        return

    for trade in open_trades:
        symbol = trade["symbol"]
        mark = price_map.get(symbol)
        if mark is None:
            continue

        triggered = None
        if trade["sl_price"] and trade["side"] == "long" and mark <= trade["sl_price"]:
            triggered = "SL"
        elif trade["sl_price"] and trade["side"] == "short" and mark >= trade["sl_price"]:
            triggered = "SL"
        elif trade["tp_price"] and trade["side"] == "long" and mark >= trade["tp_price"]:
            triggered = "TP"
        elif trade["tp_price"] and trade["side"] == "short" and mark <= trade["tp_price"]:
            triggered = "TP"

        if triggered:
            logger.info("%s triggered for %s at %.4f", triggered, symbol, mark)
            result = await order_mgr.close_position(symbol)

            pnl = (mark - trade["entry_price"]) * trade["size"] if trade["side"] == "long" \
                else (trade["entry_price"] - mark) * trade["size"]
            await repo.close_trade(trade["id"], mark, pnl)
            await repo.update_daily_pnl(date.today(), pnl)

            for user_id in settings.telegram_allowed_user_ids:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"\u26a0\ufe0f {triggered} Hit: {symbol}\n"
                         f"Exit: ${mark:,.2f} | PnL: {'+' if pnl >= 0 else ''}{pnl:,.2f}",
                )


async def daily_pnl_rollup(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Midnight task: summarize daily PnL and notify user."""
    repo = context.bot_data["repo"]
    today = date.today()
    pnl_data = await repo.get_pnl_summary("today")

    for user_id in settings.telegram_allowed_user_ids:
        text = formatters.format_pnl_summary(pnl_data, "today")
        await context.bot.send_message(chat_id=user_id, text=f"\U0001f305 Daily Summary\n{text}")

    logger.info("Daily PnL rollup complete for %s", today)


async def collect_funding_oi(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Collect funding rate and OI snapshots for all coins and store to DB."""
    repo = context.bot_data["repo"]
    market_data = context.bot_data["market_data"]

    try:
        snapshots = await market_data.get_all_funding_and_oi()
        if snapshots:
            count = await repo.insert_funding_oi_batch(snapshots)
            logger.info("Collected funding/OI snapshots for %d coins", count)
        else:
            logger.warning("No funding/OI data returned")
    except Exception:
        logger.exception("Failed to collect funding/OI snapshots")


async def health_check(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Verify Hyperliquid API connectivity."""
    client = context.bot_data["client"]
    connected = client.is_connected()
    if not connected:
        logger.warning("Hyperliquid API connection lost")
        for user_id in settings.telegram_allowed_user_ids:
            await context.bot.send_message(
                chat_id=user_id,
                text="\u26a0\ufe0f API connection lost. Attempting reconnect...",
            )
