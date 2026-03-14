"""Telegram command and callback handlers."""

import logging
import time
from datetime import date, datetime, timedelta
from functools import wraps

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from src.bot import formatters, keyboards
from src.config import settings

logger = logging.getLogger(__name__)

BOT_START_TIME = time.time()
EXECUTION_MODE = settings.default_execution_mode


def _parse_strategy_indicators(strategy: dict | None) -> tuple[set[str] | None, list[list[str]] | None]:
    """Parse strategy indicators: flat list or list of combos."""
    if not strategy:
        return None, None
    indicators = strategy["indicators"]
    if not indicators:
        return None, None
    if isinstance(indicators[0], list):
        return None, indicators
    return set(indicators), None


def _format_strategy_indicators(indicators: list) -> str:
    """Format strategy indicators for display."""
    if not indicators:
        return "none"
    if isinstance(indicators[0], list):
        return " OR ".join("+".join(combo) for combo in indicators)
    return ", ".join(indicators)


def _parse_indicator_input(text: str) -> list:
    """Parse indicator input: 'fvg+ad,ema+ad' -> [['fvg','ad'],['ema','ad']], 'ema,rsi' -> ['ema','rsi']."""
    all_indicators = {"ema", "rsi", "macd", "support_resistance", "volume", "fvg", "ad", "stoch_rsi", "funding_rate", "open_interest"}
    parts = [p.strip().lower() for p in text.split(",")]

    has_combos = any("+" in p for p in parts)
    if has_combos:
        combos = []
        for part in parts:
            indicators = [i.strip() for i in part.split("+")]
            invalid = [i for i in indicators if i not in all_indicators]
            if invalid:
                return invalid
            combos.append(indicators)
        return combos
    else:
        invalid = [i for i in parts if i not in all_indicators]
        if invalid:
            return invalid
        return parts


def authorized(func):
    """Decorator to restrict access to allowed user IDs."""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message:
            return
        user_id = update.effective_user.id
        if settings.telegram_allowed_user_ids and user_id not in settings.telegram_allowed_user_ids:
            await update.message.reply_text("Unauthorized.")
            logger.warning("Unauthorized access attempt by user %d", user_id)
            return
        return await func(update, context)
    return wrapper


def _get_repo(context: ContextTypes.DEFAULT_TYPE):
    return context.bot_data["repo"]


def _get_order_mgr(context: ContextTypes.DEFAULT_TYPE):
    return context.bot_data["order_manager"]


def _get_market_data(context: ContextTypes.DEFAULT_TYPE):
    return context.bot_data["market_data"]


def _get_risk_mgr(context: ContextTypes.DEFAULT_TYPE):
    return context.bot_data["risk_manager"]


def _get_client(context: ContextTypes.DEFAULT_TYPE):
    return context.bot_data["client"]


# ── Commands ────────────────────────────────────────────────


@authorized
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "\U0001f916 Hyperliquid Trading Bot\n\n"
        "Use /help to see all commands.\n"
        f"Mode: {EXECUTION_MODE.upper()} | Network: {'Testnet' if settings.hl_testnet else 'Mainnet'}"
    )


@authorized
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Commands:\n"
        "/scan [symbol] \u2014 Run TA scan\n"
        "/trade <symbol> <side> <size> \u2014 Place trade\n"
        "/limit <symbol> <side> <price> <size> \u2014 Limit order\n"
        "/close <symbol> \u2014 Close position\n"
        "/cancel [order_id] \u2014 Cancel order(s)\n"
        "/positions \u2014 Open positions\n"
        "/balance \u2014 Account balance\n"
        "/history [n] \u2014 Trade history\n"
        "/pnl <today|week|month|all> \u2014 PnL summary\n"
        "/watchlist [add|remove <symbol>] \u2014 Manage watchlist\n"
        "/alerts \u2014 View alerts\n"
        "/strategy [create|auto|delete] \u2014 Per-coin strategies\n"
        "/indicators [enable|disable <name>] \u2014 Global indicator toggle\n"
        "/backtest <symbol> [days] [breakdown|strategy|indicators] \u2014 Backtest\n"
        "/mode [auto|manual] \u2014 Execution mode\n"
        "/risk \u2014 Risk settings\n"
        "/status \u2014 Bot status\n"
        "/symbols [search] \u2014 List available coins\n"
        "/cleanup \u2014 Remove stale trades from DB"
    )


@authorized
async def scan_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.chat.send_action(ChatAction.TYPING)
    repo = _get_repo(context)
    market_data = _get_market_data(context)
    risk_settings = await _get_risk_mgr(context).get_settings()

    args = context.args
    if args:
        symbols = [args[0].upper()]
    else:
        symbols = await repo.get_watchlist()
        if not symbols:
            await update.message.reply_text("Watchlist is empty. Use /watchlist add <symbol>")
            return

    from src.analysis.signals import scan_symbol

    strategies = await repo.get_all_strategies()
    strategy_map = {s["symbol"]: s for s in strategies}

    await update.message.reply_text(f"\u23f3 Scanning {len(symbols)} symbol(s)...")

    for symbol in symbols:
        try:
            strategy = strategy_map.get(symbol)
            active_indicators, combos = _parse_strategy_indicators(strategy)
            signal = await scan_symbol(symbol, market_data, risk_settings, active_indicators=active_indicators, combos=combos)
            if signal and signal.get("direction"):
                signal_id = await repo.create_signal(
                    symbol=signal["symbol"],
                    direction=signal["direction"],
                    indicators=signal["indicators"],
                    confidence=signal["confidence"],
                    sl_price=signal.get("sl_price"),
                    tp_price=signal.get("tp_price"),
                )
                text = formatters.format_signal(signal)
                await update.message.reply_text(text, reply_markup=keyboards.signal_keyboard(signal_id))
            else:
                text = formatters.format_no_signal(signal or {"symbol": symbol, "indicators": {}})
                await update.message.reply_text(text)
        except Exception as e:
            logger.exception("Error scanning %s", symbol)
            await update.message.reply_text(f"\U0001f4ca Signal: {symbol} \u2014 ERROR: {e}")


@authorized
async def trade_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    if not args or len(args) < 3:
        await update.message.reply_text("Usage: /trade <symbol> <long|short> <size>")
        return

    symbol = args[0].upper()
    side = args[1].lower()
    try:
        size = float(args[2])
    except ValueError:
        await update.message.reply_text("Invalid size.")
        return

    if side not in ("long", "short"):
        await update.message.reply_text("Side must be 'long' or 'short'.")
        return

    if EXECUTION_MODE == "manual":
        await update.message.reply_text(
            f"Confirm trade:\n{side.upper()} {size} {symbol}",
            reply_markup=keyboards.trade_confirm_keyboard(symbol, side, size),
        )
    else:
        await _execute_trade(update, context, symbol, side, size)


@authorized
async def limit_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    if not args or len(args) < 4:
        await update.message.reply_text("Usage: /limit <symbol> <long|short> <price> <size>")
        return

    symbol = args[0].upper()
    side = args[1].lower()
    try:
        price = float(args[2])
        size = float(args[3])
    except ValueError:
        await update.message.reply_text("Invalid price or size.")
        return

    await update.message.chat.send_action(ChatAction.TYPING)
    risk_mgr = _get_risk_mgr(context)
    market_data = _get_market_data(context)

    # Get leverage for risk validation
    leverage = settings.max_leverage
    try:
        client = _get_client(context)
        positions = await client.get_open_positions()
        pos = next((p for p in positions if p["symbol"] == symbol), None)
        if pos:
            leverage = pos.get("leverage", settings.max_leverage)
    except Exception:
        pass

    passed, failures = await risk_mgr.validate_trade(symbol, side, size, price, leverage=leverage)
    if not passed:
        await update.message.reply_text(formatters.format_error("\n".join(failures)))
        return

    market_info = await market_data.get_market_info(symbol)
    sz_decimals = market_info.get("sz_decimals", 0)

    sl, tp = risk_mgr.calculate_sl_tp(price, side, sz_decimals=sz_decimals)
    order_mgr = _get_order_mgr(context)
    result = await order_mgr.limit_order(symbol, side, price, size, sl, tp, sz_decimals=sz_decimals)
    await update.message.reply_text(formatters.format_trade_result(result))

    if result.get("status") != "error":
        repo = _get_repo(context)
        await repo.create_trade(symbol, side, size, price, "limit", sl, tp)


@authorized
async def close_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /close <symbol>")
        return

    symbol = args[0].upper()
    await update.message.reply_text(
        f"Close position for {symbol}?",
        reply_markup=keyboards.close_confirm_keyboard(symbol),
    )


@authorized
async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    order_mgr = _get_order_mgr(context)

    if args:
        result = await order_mgr.cancel_order(args[0])
    else:
        result = await order_mgr.cancel_all_orders()
    await update.message.reply_text(formatters.format_trade_result(result))


@authorized
async def positions_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.chat.send_action(ChatAction.TYPING)
    client = _get_client(context)
    risk_settings = await _get_risk_mgr(context).get_settings()
    positions = await client.get_open_positions()
    total_exposure = sum(p["size"] * p["entry_price"] for p in positions)
    max_exposure = risk_settings.get("max_total_exposure", settings.max_total_exposure)
    text = formatters.format_positions(positions, total_exposure, max_exposure)
    markup = keyboards.positions_keyboard(positions) if positions else None
    await update.message.reply_text(text, reply_markup=markup)


@authorized
async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.chat.send_action(ChatAction.TYPING)
    client = _get_client(context)
    balances = await client.get_balances()
    await update.message.reply_text(formatters.format_balance(balances))


@authorized
async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    limit = int(context.args[0]) if context.args and context.args[0].isdigit() else 10
    repo = _get_repo(context)
    trades = await repo.get_trade_history(limit)
    await update.message.reply_text(formatters.format_trade_history(trades))


@authorized
async def pnl_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    if not args:
        await update.message.reply_text(
            "Usage: /pnl <today|week|month|all>"
        )
        return

    await update.message.chat.send_action(ChatAction.TYPING)
    period = args[0].lower()
    client = _get_client(context)

    today = date.today()
    if period == "today":
        start = today
    elif period == "week":
        start = today - timedelta(days=today.weekday())
    elif period == "month":
        start = today.replace(day=1)
    else:
        start = date(2020, 1, 1)

    start_ms = int(datetime.combine(start, datetime.min.time()).timestamp() * 1000)

    try:
        fills = await client.get_fills(start_time=start_ms)
        summary = await client.get_account_summary()

        total_pnl = sum(float(f.get("closedPnl", 0)) for f in fills)
        trade_count = len(fills)
        unrealized = summary.get("total_unrealized_pnl", 0)

        data = {
            "period": period,
            "start_date": start.isoformat(),
            "total_pnl": total_pnl,
            "unrealized_pnl": unrealized,
            "total_trades": trade_count,
            "account_value": summary.get("account_value", 0),
        }
        await update.message.reply_text(formatters.format_pnl_summary(data, period))
    except Exception as e:
        await update.message.reply_text(formatters.format_error(f"Failed to fetch PnL: {e}"))


@authorized
async def watchlist_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    repo = _get_repo(context)
    args = context.args

    if not args:
        symbols = await repo.get_watchlist()
        await update.message.reply_text(formatters.format_watchlist(symbols))
        return

    action = args[0].lower()
    if action == "add" and len(args) > 1:
        symbol = args[1].upper()
        await repo.add_to_watchlist(symbol)
        await update.message.reply_text(f"Added {symbol} to watchlist.")
    elif action == "remove" and len(args) > 1:
        symbol = args[1].upper()
        await repo.remove_from_watchlist(symbol)
        await update.message.reply_text(f"Removed {symbol} from watchlist.")
    else:
        await update.message.reply_text("Usage: /watchlist [add|remove <symbol>]")


@authorized
async def symbols_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    client = _get_client(context)
    try:
        meta = await client._run_sync(client.info.meta)
        names = sorted(asset["name"] for asset in meta.get("universe", []))
    except Exception as e:
        await update.message.reply_text(formatters.format_error(f"Failed to fetch symbols: {e}"))
        return

    search = context.args[0].upper() if context.args else None
    if search:
        names = [n for n in names if search in n.upper()]

    if not names:
        await update.message.reply_text(f"No symbols matching '{search}'.")
        return

    text = f"\U0001f4cb Available Symbols ({len(names)})\n\n" + ", ".join(names)
    for i in range(0, len(text), 4000):
        await update.message.reply_text(text[i:i + 4000])


@authorized
async def cleanup_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Close stale DB trades that no longer exist on Hyperliquid."""
    await update.message.chat.send_action(ChatAction.TYPING)
    repo = _get_repo(context)
    client = _get_client(context)

    open_trades = await repo.get_open_trades()
    if not open_trades:
        await update.message.reply_text("No open trades in database.")
        return

    try:
        positions = await client.get_open_positions()
        exchange_symbols = {p["symbol"] for p in positions}
    except Exception as e:
        await update.message.reply_text(formatters.format_error(f"Failed to fetch positions: {e}"))
        return

    cleaned = 0
    # Close trades for symbols no longer on exchange
    for trade in open_trades:
        if trade["symbol"] not in exchange_symbols:
            await repo.close_trade(trade["id"], trade["entry_price"], 0)
            cleaned += 1

    # Close duplicate trades — keep only the latest per symbol
    from collections import defaultdict
    by_symbol = defaultdict(list)
    for trade in open_trades:
        if trade["symbol"] in exchange_symbols:
            by_symbol[trade["symbol"]].append(trade)

    for symbol, trades in by_symbol.items():
        if len(trades) > 1:
            # Sort by id descending, keep the latest
            trades.sort(key=lambda t: t["id"], reverse=True)
            for stale in trades[1:]:
                await repo.close_trade(stale["id"], stale["entry_price"], 0)
                cleaned += 1

    if cleaned:
        await update.message.reply_text(
            f"\U0001f9f9 Cleaned {cleaned} stale trade(s) from database.\n"
            f"Remaining open: {len(open_trades) - cleaned}"
        )
    else:
        await update.message.reply_text(
            f"No stale trades found. All {len(open_trades)} DB trades match exchange positions."
        )


@authorized
async def backtest_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    if not args:
        await update.message.reply_text(
            "\U0001f4ca Backtest Usage:\n\n"
            "/backtest <symbol> [days] \u2014 Standard backtest\n"
            "/backtest <symbol> [days] breakdown \u2014 Indicator breakdown\n"
            "/backtest <symbol> [days] strategy \u2014 Use saved strategy\n"
            "/backtest <symbol> [days] ema,rsi,ad \u2014 Specific indicators\n"
            "/backtest <symbol> [days] fvg+ad,ema+ad \u2014 Combo strategy\n\n"
            "Example: /backtest HYPE 90 fvg+ad,ema+ad"
        )
        return

    symbol = args[0].upper()
    days = int(args[1]) if len(args) > 1 and args[1].isdigit() else 90
    flags = [a.lower() for a in args]
    breakdown = "breakdown" in flags
    use_strategy = "strategy" in flags

    # Check if any arg looks like inline indicators
    all_ind_names = {"ema", "rsi", "macd", "support_resistance", "volume", "fvg", "ad", "stoch_rsi", "funding_rate", "open_interest"}
    strategy_indicators = None
    strategy_combos = None
    for a in args[1:]:
        a_lower = a.lower()
        if a_lower in ("breakdown", "strategy") or a_lower.isdigit():
            continue
        parts = [p.strip() for p in a_lower.replace("+", ",").split(",")]
        if all(p in all_ind_names for p in parts):
            parsed = _parse_indicator_input(a_lower)
            if isinstance(parsed[0], list):
                strategy_combos = parsed
            else:
                strategy_indicators = set(parsed)
            break

    # Resolve saved strategy if requested
    if not strategy_indicators and not strategy_combos and use_strategy:
        repo = _get_repo(context)
        strategy = await repo.get_strategy(symbol)
        if not strategy:
            await update.message.reply_text(
                f"No strategy found for {symbol}.\n"
                f"Create one: /strategy create {symbol} ema,rsi,macd"
            )
            return
        active_set, combos = _parse_strategy_indicators(strategy)
        if active_set:
            strategy_indicators = active_set
        elif combos:
            strategy_combos = combos

    if strategy_combos:
        mode_label = "combo"
        label_detail = f" ({' OR '.join('+'.join(c) for c in strategy_combos)})"
    elif strategy_indicators:
        mode_label = "custom" if not use_strategy else "strategy"
        label_detail = f" ({', '.join(sorted(strategy_indicators))})"
    else:
        mode_label = "breakdown" if breakdown else "standard"
        label_detail = ""
    await update.message.reply_text(f"\u23f3 Running {mode_label} backtest for {symbol} over {days} days{label_detail}...")
    await update.message.chat.send_action(ChatAction.TYPING)

    market_data = _get_market_data(context)

    try:
        candle_count = 4 * 24 * days
        df = await market_data.get_candles(symbol, interval="15m", limit=candle_count)

        if df.empty or len(df) < 60:
            await update.message.reply_text(formatters.format_error(f"Not enough data for {symbol} ({len(df)} candles)"))
            return

        # Fetch funding rate history from API (hourly, available historically)
        import pandas as pd
        funding_data = None
        try:
            funding_history = await market_data.get_funding_history(symbol, hours=days * 24)
            if funding_history:
                funding_data = pd.DataFrame(funding_history)
                funding_data["time"] = pd.to_datetime(funding_data["time"], unit="ms")
        except Exception:
            logger.warning("Could not fetch funding history for backtest: %s", symbol)

        # Fetch OI data from DB (only available if we've been collecting)
        oi_data = None
        try:
            repo = _get_repo(context)
            oi_rows = await repo.get_funding_oi_history(symbol, hours=days * 24)
            if oi_rows:
                oi_data = pd.DataFrame(oi_rows)
                oi_data["timestamp"] = pd.to_datetime(oi_data["timestamp"])
        except Exception:
            logger.warning("Could not fetch OI history for backtest: %s", symbol)

        from src.analysis.backtest import run_backtest, run_indicator_breakdown

        if breakdown:
            results = run_indicator_breakdown(df, symbol, days, funding_data=funding_data, oi_data=oi_data)
            text = formatters.format_indicator_breakdown(results)
        else:
            result = run_backtest(df, symbol, days, active_indicators=strategy_indicators, combos=strategy_combos, funding_data=funding_data, oi_data=oi_data)
            text = formatters.format_backtest(result)

        # Telegram has a 4096 char limit — split if needed
        for i in range(0, len(text), 4000):
            await update.message.reply_text(text[i:i + 4000])
    except Exception as e:
        await update.message.reply_text(formatters.format_error(f"Backtest failed: {e}"))


@authorized
async def indicators_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from src.analysis.signals import ACTIVE_INDICATORS

    args = context.args
    if not args:
        active = ", ".join(sorted(ACTIVE_INDICATORS))
        all_indicators = {"ema", "rsi", "macd", "support_resistance", "volume", "fvg", "ad", "stoch_rsi", "funding_rate", "open_interest"}
        inactive = all_indicators - ACTIVE_INDICATORS
        inactive_str = ", ".join(sorted(inactive)) if inactive else "none"
        await update.message.reply_text(
            f"\U0001f527 Global Indicators\n"
            f"Active: {active}\n"
            f"Inactive: {inactive_str}\n\n"
            f"Usage:\n"
            f"/indicators enable <name>\n"
            f"/indicators disable <name>\n\n"
            f"Available: {', '.join(sorted(all_indicators))}"
        )
        return

    action = args[0].lower()
    if action in ("enable", "disable") and len(args) > 1:
        ind = args[1].lower()
        all_indicators = {"ema", "rsi", "macd", "support_resistance", "volume", "fvg", "ad", "stoch_rsi", "funding_rate", "open_interest"}
        if ind not in all_indicators:
            await update.message.reply_text(f"Unknown indicator: {ind}\nAvailable: {', '.join(sorted(all_indicators))}")
            return

        if action == "enable":
            ACTIVE_INDICATORS.add(ind)
            await update.message.reply_text(f"\u2705 Enabled {ind}")
        else:
            if len(ACTIVE_INDICATORS) <= 1:
                await update.message.reply_text("At least 1 indicator must be active.")
                return
            ACTIVE_INDICATORS.discard(ind)
            await update.message.reply_text(f"\u274c Disabled {ind}")
    else:
        await update.message.reply_text("Usage: /indicators [enable|disable <name>]")


@authorized
async def strategy_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    repo = _get_repo(context)
    args = context.args

    # /strategy create HYPE fvg+ad,ema+ad
    if args and args[0].lower() == "create" and len(args) >= 3:
        symbol = args[1].upper()
        parsed = _parse_indicator_input(args[2])
        all_indicators = {"ema", "rsi", "macd", "support_resistance", "volume", "fvg", "ad", "stoch_rsi", "funding_rate", "open_interest"}
        if isinstance(parsed[0], list):
            flat = [i for combo in parsed for i in combo]
        else:
            flat = parsed
        invalid = [i for i in flat if i not in all_indicators]
        if invalid:
            await update.message.reply_text(
                f"Unknown indicator(s): {', '.join(invalid)}\n"
                f"Available: {', '.join(sorted(all_indicators))}"
            )
            return

        await repo.create_strategy(symbol, parsed, auto_execute=False)
        await repo.add_to_watchlist(symbol)
        display = _format_strategy_indicators(parsed)
        await update.message.reply_text(
            f"\u2705 Strategy saved for {symbol}\n"
            f"Indicators: {display}\n"
            f"Mode: \u270b MANUAL",
        )
        return

    # /strategy auto HYPE on|off
    if args and args[0].lower() == "auto" and len(args) >= 3:
        symbol = args[1].upper()
        toggle = args[2].lower()
        if toggle not in ("on", "off"):
            await update.message.reply_text("Usage: /strategy auto <symbol> <on|off>")
            return
        auto = toggle == "on"
        strategy = await repo.get_strategy(symbol)
        if not strategy:
            await update.message.reply_text(f"No strategy found for {symbol}.")
            return
        await repo.toggle_strategy_auto(symbol, auto)
        label = "\U0001f916 AUTO" if auto else "\u270b MANUAL"
        await update.message.reply_text(f"{symbol} strategy set to {label}")
        return

    # /strategy delete HYPE
    if args and args[0].lower() == "delete" and len(args) >= 2:
        symbol = args[1].upper()
        strategy = await repo.get_strategy(symbol)
        if not strategy:
            await update.message.reply_text(f"No strategy found for {symbol}.")
            return
        await repo.delete_strategy(symbol)
        await update.message.reply_text(f"\U0001f5d1 Strategy deleted for {symbol}.")
        return

    # /strategy (list all)
    strategies = await repo.get_all_strategies()
    if not strategies:
        await update.message.reply_text(
            "\U0001f3af No strategies yet.\n\n"
            "Create one:\n"
            "/strategy create HYPE ema,rsi,macd\n"
            "/strategy create HYPE fvg+ad,ema+ad\n\n"
            "Manage:\n"
            "/strategy auto HYPE on\n"
            "/strategy delete HYPE"
        )
        return

    lines = ["\U0001f3af Strategies"]
    for s in strategies:
        auto = "\U0001f916 AUTO" if s["auto_execute"] else "\u270b MANUAL"
        inds = _format_strategy_indicators(s["indicators"])
        lines.append(f"\n{s['symbol']}  {auto}\n  {inds}")
    lines.append(
        "\n\nManage:\n"
        "/strategy create <symbol> <indicators>\n"
        "/strategy auto <symbol> <on|off>\n"
        "/strategy delete <symbol>"
    )
    await update.message.reply_text("\n".join(lines))


@authorized
async def alerts_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    repo = _get_repo(context)
    alerts = await repo.get_active_alerts()
    await update.message.reply_text(formatters.format_alerts(alerts))


@authorized
async def mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global EXECUTION_MODE
    args = context.args
    if args and args[0].lower() in ("auto", "manual"):
        EXECUTION_MODE = args[0].lower()
        await update.message.reply_text(f"Execution mode set to {EXECUTION_MODE.upper()}.")
    else:
        await update.message.reply_text(
            f"Current mode: {EXECUTION_MODE.upper()}\n\n"
            f"Usage: /mode <auto|manual>"
        )


@authorized
async def risk_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    risk_mgr = _get_risk_mgr(context)
    risk_settings = await risk_mgr.get_settings()
    text = formatters.format_risk_settings(risk_settings)
    await update.message.reply_text(text)


@authorized
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    client = _get_client(context)
    repo = _get_repo(context)
    uptime_secs = time.time() - BOT_START_TIME
    hours, remainder = divmod(int(uptime_secs), 3600)
    minutes, secs = divmod(remainder, 60)
    watchlist = await repo.get_watchlist()

    status = {
        "connected": client.is_connected(),
        "mode": EXECUTION_MODE,
        "uptime": f"{hours}h {minutes}m {secs}s",
        "testnet": settings.hl_testnet,
        "open_positions": len(await client.get_open_positions()),
        "watchlist_size": len(watchlist),
    }
    await update.message.reply_text(formatters.format_status(status))


# ── Callback Queries ────────────────────────────────────────


async def execute_signal_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    signal_id = int(query.data.split(":")[1])

    repo = _get_repo(context)
    signals = await repo.get_recent_signals(50)
    signal = next((s for s in signals if s["id"] == signal_id), None)
    if not signal:
        await query.edit_message_text("Signal expired or not found.")
        return

    symbol = signal["symbol"]
    side = signal["direction"]

    market_data = _get_market_data(context)
    entry = await market_data.get_current_price(symbol)
    market_info = await market_data.get_market_info(symbol)
    sz_decimals = market_info.get("sz_decimals", 2)

    risk_mgr = _get_risk_mgr(context)
    risk_settings = await risk_mgr.get_settings()
    confidence = signal.get("confidence", 1.0)

    # Get leverage for position sizing (from open position or default)
    leverage = settings.max_leverage
    try:
        client = _get_client(context)
        positions = await client.get_open_positions()
        pos = next((p for p in positions if p["symbol"] == symbol), None)
        if pos:
            leverage = pos.get("leverage", settings.max_leverage)
    except Exception:
        pass

    size = await risk_mgr.calculate_position_size(entry, risk_settings.get("max_position_size"), sz_decimals, confidence=confidence, leverage=leverage)

    # Validate SL/TP against entry (S/R levels may be too close after price movement)
    sl_price = signal.get("sl_price")
    tp_price = signal.get("tp_price")
    min_distance = entry * 0.01
    if side.lower() == "long":
        if not sl_price or entry - sl_price < min_distance or not tp_price or tp_price - entry < min_distance:
            sl_price, tp_price = risk_mgr.calculate_sl_tp(entry, side, sz_decimals=sz_decimals)
    else:
        if not sl_price or sl_price - entry < min_distance or not tp_price or entry - tp_price < min_distance:
            sl_price, tp_price = risk_mgr.calculate_sl_tp(entry, side, sz_decimals=sz_decimals)

    await _execute_trade(
        update, context, symbol, side, size,
        signal_id=signal_id,
        sl_price=sl_price,
        tp_price=tp_price,
    )
    await repo.mark_signal_acted(signal_id)


async def modify_signal_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    signal_id = int(query.data.split(":")[1])

    repo = _get_repo(context)
    signals = await repo.get_recent_signals(50)
    signal = next((s for s in signals if s["id"] == signal_id), None)
    if not signal:
        await query.edit_message_text("Signal expired or not found.")
        return

    symbol = signal["symbol"]
    side = signal["direction"]
    await query.edit_message_text(
        f"Modify signal for {symbol} ({side.upper()}):\n\n"
        f"Send a trade command to customize:\n"
        f"/trade {symbol} {side} <size>\n\n"
        f"Or use /limit {symbol} {side} <price> <size> for a limit order.",
    )


async def dismiss_signal_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer("Signal dismissed.")
    await query.edit_message_text(query.message.text + "\n\n[Dismissed]")


async def confirm_trade_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")[1].split(":")
    symbol, side = parts[0], parts[1]
    size = float(parts[2])
    await _execute_trade(update, context, symbol, side, size)


async def confirm_close_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    symbol = query.data.split(":")[1]
    order_mgr = _get_order_mgr(context)
    result = await order_mgr.close_position(symbol)
    await query.edit_message_text(formatters.format_trade_result(result))

    if result.get("status") != "error":
        repo = _get_repo(context)
        open_trades = await repo.get_open_trades()
        trade = next((t for t in open_trades if t["symbol"] == symbol), None)
        if trade:
            mark = result.get("result", {}).get("avgPx", trade["entry_price"])
            size = trade["size"]
            entry = trade["entry_price"]
            pnl = (float(mark) - entry) * size if trade["side"] == "long" else (entry - float(mark)) * size
            await repo.close_trade(trade["id"], float(mark), pnl)
            await repo.update_daily_pnl(date.today(), pnl)


async def mode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global EXECUTION_MODE
    query = update.callback_query
    await query.answer()
    new_mode = query.data.split(":")[1]
    EXECUTION_MODE = new_mode
    await query.edit_message_text(f"Execution mode set to {EXECUTION_MODE.upper()}.")


async def cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer("Cancelled.")
    await query.edit_message_text("Action cancelled.")


# ── Helpers ─────────────────────────────────────────────────


async def _execute_trade(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    symbol: str,
    side: str,
    size: float,
    signal_id: int | None = None,
    sl_price: float | None = None,
    tp_price: float | None = None,
) -> None:
    """Execute a trade after all confirmations."""
    import math

    risk_mgr = _get_risk_mgr(context)
    market_data = _get_market_data(context)
    price = await market_data.get_current_price(symbol)

    # Round size to valid sz_decimals for the market
    market_info = await market_data.get_market_info(symbol)
    sz_decimals = market_info.get("sz_decimals", 2)
    factor = 10 ** sz_decimals
    size = math.floor(size * factor) / factor

    # Ensure minimum $10 order value (Hyperliquid minimum)
    min_size = math.ceil(10.0 / price * factor) / factor
    if size < min_size:
        size = min_size

    # Get leverage for risk validation
    leverage = settings.max_leverage
    try:
        client = _get_client(context)
        positions = await client.get_open_positions()
        pos = next((p for p in positions if p["symbol"] == symbol), None)
        if pos:
            leverage = pos.get("leverage", settings.max_leverage)
    except Exception:
        pass

    passed, failures = await risk_mgr.validate_trade(symbol, side, size, price, leverage=leverage)
    if not passed:
        msg = formatters.format_error("Risk check failed:\n" + "\n".join(failures))
        if update.callback_query:
            await update.callback_query.edit_message_text(msg)
        else:
            await update.message.reply_text(msg)
        return

    # Use signal's S/R-based SL/TP if provided, otherwise default 2:1 ratio
    if sl_price and tp_price:
        sl, tp = sl_price, tp_price
    else:
        sl, tp = risk_mgr.calculate_sl_tp(price, side, sz_decimals=sz_decimals)
    order_mgr = _get_order_mgr(context)
    result = await order_mgr.market_order(symbol, side, size, sl, tp, sz_decimals=sz_decimals)
    text = formatters.format_trade_result(result)

    if update.callback_query:
        await update.callback_query.edit_message_text(text)
    else:
        await update.message.reply_text(text)

    if result.get("status") != "error":
        repo = _get_repo(context)
        source = f"signal:{signal_id}" if signal_id else "manual"
        await repo.create_trade(symbol, side, size, price, "market", sl, tp, source)


# ── Registration ────────────────────────────────────────────


def register_handlers(application) -> None:
    """Register all command and callback query handlers."""
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("scan", scan_command))
    application.add_handler(CommandHandler("trade", trade_command))
    application.add_handler(CommandHandler("limit", limit_command))
    application.add_handler(CommandHandler("close", close_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CommandHandler("positions", positions_command))
    application.add_handler(CommandHandler("balance", balance_command))
    application.add_handler(CommandHandler("history", history_command))
    application.add_handler(CommandHandler("pnl", pnl_command))
    application.add_handler(CommandHandler("watchlist", watchlist_command))
    application.add_handler(CommandHandler("alerts", alerts_command))
    application.add_handler(CommandHandler("backtest", backtest_command))
    application.add_handler(CommandHandler("symbols", symbols_command))
    application.add_handler(CommandHandler("cleanup", cleanup_command))
    application.add_handler(CommandHandler("indicators", indicators_command))
    application.add_handler(CommandHandler("strategy", strategy_command))
    application.add_handler(CommandHandler("mode", mode_command))
    application.add_handler(CommandHandler("risk", risk_command))
    application.add_handler(CommandHandler("status", status_command))

    # Signal & trade callbacks (these are essential action buttons, not menus)
    application.add_handler(CallbackQueryHandler(execute_signal_callback, pattern=r"^execute_signal:"))
    application.add_handler(CallbackQueryHandler(modify_signal_callback, pattern=r"^modify_signal:"))
    application.add_handler(CallbackQueryHandler(dismiss_signal_callback, pattern=r"^dismiss_signal:"))
    application.add_handler(CallbackQueryHandler(confirm_trade_callback, pattern=r"^confirm_trade:"))
    application.add_handler(CallbackQueryHandler(confirm_close_callback, pattern=r"^confirm_close:"))
    application.add_handler(CallbackQueryHandler(confirm_close_callback, pattern=r"^close_pos:"))
    application.add_handler(CallbackQueryHandler(mode_callback, pattern=r"^set_mode:"))
    application.add_handler(CallbackQueryHandler(cancel_callback, pattern=r"^cancel_"))

    logger.info("All handlers registered")
