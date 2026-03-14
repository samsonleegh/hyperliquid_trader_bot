"""Message formatting for Telegram output."""

import json


def format_signal(signal: dict) -> str:
    direction = signal["direction"].upper()
    symbol = signal["symbol"]
    entry = signal["entry_price"]
    sl = signal["sl_price"]
    tp = signal["tp_price"]
    confidence = signal.get("confidence", 0)

    sl_pct = abs(sl - entry) / entry * 100
    tp_pct = abs(tp - entry) / entry * 100
    size = signal.get("suggested_size", 0)
    size_usd = size * entry

    indicator_lines = []
    indicators = signal.get("indicators", {})
    for key, val in indicators.items():
        detail = val.get("detail", "")
        if detail == "disabled":
            continue
        sig = val.get("signal")
        if sig == "bullish":
            icon = "\U0001f7e2"
        elif sig == "bearish":
            icon = "\U0001f534"
        else:
            icon = "\u26aa"
        check = "\u2713" if sig else "\u2014"
        indicator_lines.append(f"  {icon} {detail} {check}")

    indicators_text = "\n".join(indicator_lines)

    triggered = signal.get("triggered_combo")
    trigger_line = f"Triggered: {triggered}\n" if triggered else ""

    return (
        f"\U0001f4ca Signal: {symbol} \u2014 {direction}\n"
        f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        f"Confidence: {confidence:.0%}\n"
        f"{trigger_line}"
        f"Indicators:\n{indicators_text}\n"
        f"Entry: ${entry:,.2f} (market)\n"
        f"Stop-loss: ${sl:,.2f} (-{sl_pct:.1f}%)\n"
        f"Take-profit: ${tp:,.2f} (+{tp_pct:.1f}%)\n"
        f"Size: {size:.4f} (${size_usd:,.0f})\n"
        f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501"
    )


def format_positions(positions: list[dict], total_exposure: float, max_exposure: float) -> str:
    if not positions:
        return "\U0001f4c2 No open positions."

    lines = [f"\U0001f4c2 Open Positions ({len(positions)})", "\u2501" * 16]
    total_pnl = 0.0

    for p in positions:
        side = p["side"].upper()
        symbol = p.get("symbol", "???")
        size = p.get("size", 0)
        entry = p.get("entry_price", 0)
        mark = p.get("mark_price", entry)
        pnl = p.get("unrealized_pnl", 0)
        total_pnl += pnl

        pnl_pct = (pnl / (entry * size) * 100) if entry and size else 0
        color = "\U0001f7e2" if pnl >= 0 else "\U0001f534"
        sl = p.get("sl_price")
        tp = p.get("tp_price")

        lines.append(f"{symbol}  {side}  {size}")
        lines.append(f"  Entry: ${entry:,.2f} | Mark: ${mark:,.2f}")
        lines.append(f"  PnL: {'+' if pnl >= 0 else ''}{pnl:,.2f} ({pnl_pct:+.1f}%) {color}")
        if sl or tp:
            sl_str = f"${sl:,.2f}" if sl else "---"
            tp_str = f"${tp:,.2f}" if tp else "---"
            lines.append(f"  SL: {sl_str} | TP: {tp_str}")
        lines.append("")

    lines.append("\u2501" * 16)
    lines.append(f"Total PnL: {'+' if total_pnl >= 0 else ''}{total_pnl:,.2f}")
    lines.append(f"Exposure: ${total_exposure:,.0f} / ${max_exposure:,.0f}")
    return "\n".join(lines)


def format_balance(balances: dict) -> str:
    perp = balances.get("perp", {})
    lines = [
        "\U0001f4b0 Account Balance",
        "\u2501" * 16,
        f"Account Value: ${perp.get('account_value', 0):,.2f}",
        f"Margin Used: ${perp.get('total_margin_used', 0):,.2f}",
        f"Position Value: ${perp.get('total_ntl_pos', 0):,.2f}",
        f"Free Balance: ${perp.get('total_raw_usd', 0):,.2f}",
    ]
    spot_balances = balances.get("spot", {}).get("balances", [])
    if spot_balances:
        lines.append("\nSpot:")
        for b in spot_balances:
            token = b.get("coin", "???")
            total = float(b.get("total", 0))
            if total > 0:
                lines.append(f"  {token}: {total:.4f}")
    return "\n".join(lines)


def format_trade_result(result: dict) -> str:
    status = result.get("status", "unknown")
    symbol = result.get("symbol", "???")
    side = result.get("side", "???").upper()
    size = result.get("size", 0)

    if status == "error":
        return f"\u274c Trade failed: {result.get('error', 'Unknown error')}"

    emoji = "\u2705" if status in ("filled", "placed", "closed") else "\u26a0\ufe0f"
    price_str = f" @ ${result['price']:,.2f}" if "price" in result else ""
    return f"{emoji} {status.upper()}: {side} {size} {symbol}{price_str}"


def format_pnl_summary(pnl_data: dict, period: str) -> str:
    total = pnl_data.get("total_pnl", 0)
    unrealized = pnl_data.get("unrealized_pnl", 0)
    trades = pnl_data.get("total_trades", 0)
    account_value = pnl_data.get("account_value", 0)
    combined = total + unrealized
    color = "\U0001f7e2" if combined >= 0 else "\U0001f534"
    lines = [
        f"\U0001f4c8 PnL Summary ({period})",
        "\u2501" * 16,
        f"Realized PnL: {'+' if total >= 0 else ''}{total:,.2f} " + ("\U0001f7e2" if total >= 0 else "\U0001f534"),
        f"Unrealized PnL: {'+' if unrealized >= 0 else ''}{unrealized:,.2f}",
        f"Combined: {'+' if combined >= 0 else ''}{combined:,.2f} {color}",
        f"Fills: {trades}",
    ]
    if account_value:
        lines.append(f"Account Value: ${account_value:,.2f}")
    lines.append(f"Period: {pnl_data.get('start_date', 'N/A')} \u2014 today")
    return "\n".join(lines)


def format_trade_history(trades: list[dict]) -> str:
    if not trades:
        return "\U0001f4dc No trade history."

    lines = [f"\U0001f4dc Trade History (last {len(trades)})", "\u2501" * 16]
    for t in trades:
        side = t["side"].upper()
        status = t["status"]
        pnl = t.get("pnl")
        pnl_str = f" PnL: {'+' if pnl >= 0 else ''}{pnl:,.2f}" if pnl is not None else ""
        lines.append(
            f"{t['symbol']} {side} {t['size']} @ ${t['entry_price']:,.2f} "
            f"[{status}]{pnl_str}"
        )
    return "\n".join(lines)


def format_risk_settings(risk_settings: dict) -> str:
    labels = {
        "max_position_size": ("Max Position Size", "$"),
        "max_total_exposure": ("Max Total Exposure", "$"),
        "max_leverage": ("Max Leverage", "x"),
        "default_sl_pct": ("Default Stop-Loss", "%"),
        "default_tp_pct": ("Default Take-Profit", "%"),
        "daily_loss_limit": ("Daily Loss Limit", "$"),
        "max_open_positions": ("Max Open Positions", ""),
    }
    lines = ["\u2699\ufe0f Risk Settings", "\u2501" * 16]
    for key, value in risk_settings.items():
        label, suffix = labels.get(key, (key, ""))
        if suffix == "$":
            lines.append(f"{label}: ${value:,.0f}")
        elif suffix == "x":
            lines.append(f"{label}: {value:.0f}x")
        elif suffix == "%":
            lines.append(f"{label}: {value:.1f}%")
        else:
            lines.append(f"{label}: {value:.0f}")
    return "\n".join(lines)


def format_watchlist(symbols: list[str]) -> str:
    if not symbols:
        return "\U0001f4cb Watchlist is empty. Use /watchlist add <symbol> to add."
    lines = [f"\U0001f4cb Watchlist ({len(symbols)})", "\u2501" * 16]
    for i, s in enumerate(symbols, 1):
        lines.append(f"  {i}. {s}")
    return "\n".join(lines)


def format_alerts(alerts: list[dict]) -> str:
    if not alerts:
        return "\U0001f514 No active alerts."
    lines = [f"\U0001f514 Active Alerts ({len(alerts)})", "\u2501" * 16]
    for a in alerts:
        lines.append(f"  #{a['id']} {a['symbol']} {a['alert_type']} ${a.get('target_value', 0):,.2f}")
    return "\n".join(lines)


def format_status(status: dict) -> str:
    connected = "\u2705" if status.get("connected") else "\u274c"
    mode = status.get("mode", "manual").upper()
    return (
        f"\U0001f916 Bot Status\n"
        f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        f"API Connection: {connected}\n"
        f"Execution Mode: {mode}\n"
        f"Uptime: {status.get('uptime', 'N/A')}\n"
        f"Network: {'Testnet' if status.get('testnet') else 'Mainnet'}\n"
        f"Open Positions: {status.get('open_positions', 0)}\n"
        f"Watchlist Size: {status.get('watchlist_size', 0)}"
    )


def format_backtest(result) -> str:
    """Format backtest results."""
    if result.total_trades == 0:
        return f"\U0001f4ca Backtest: {result.symbol} ({result.period_days}d)\nNo signals generated over {result.total_candles} candles."

    net_icon = "\U0001f7e2" if result.net_pnl >= 0 else "\U0001f534"
    pf = f"{result.profit_factor:.2f}" if result.profit_factor != float("inf") else "inf"
    hold_hours = result.avg_hold_bars * 15 / 60

    strategy_line = f"Strategy: {result.label}" if result.label and result.label != "all" else "Indicators: all"
    lines = [
        f"\U0001f4ca Backtest: {result.symbol} ({result.period_days}d, 15m)",
        strategy_line,
        "\u2501" * 16,
        f"Starting Capital: ${result.starting_capital:,.2f}",
        f"Final Capital: ${result.final_capital:,.2f}",
        f"Return: {'+' if result.return_pct >= 0 else ''}{result.return_pct:.1f}% {net_icon}",
        "\u2501" * 16,
        f"Gross PnL: {'+' if result.total_pnl >= 0 else ''}{result.total_pnl:,.2f}",
        f"Total Fees: -${result.total_fees:,.2f}",
        f"Net PnL: {'+' if result.net_pnl >= 0 else ''}{result.net_pnl:,.2f} {net_icon}",
        "\u2501" * 16,
        f"Trades: {result.total_trades} ({result.winning_trades}W / {result.losing_trades}L)",
        f"Win Rate: {result.win_rate:.1f}%",
        f"Profit Factor: {pf}",
        f"Avg Win: +{result.avg_win:,.2f}",
        f"Avg Loss: {result.avg_loss:,.2f}",
        f"Best Trade: +{result.best_trade:,.2f}",
        f"Worst Trade: {result.worst_trade:,.2f}",
        f"Expectancy: {'+' if result.expectancy >= 0 else ''}{result.expectancy:,.2f} /trade",
        f"Max Drawdown: -${result.max_drawdown:,.2f} ({result.max_drawdown_pct:.1f}%)",
        f"Avg Hold: {hold_hours:.1f}h ({result.avg_hold_bars:.0f} bars)",
        "\u2501" * 16,
        f"Position Size: 20% of portfolio",
        f"Fees: 0.035% taker (entry + exit)",
        f"Candles: {result.total_candles}",
    ]
    return "\n".join(lines)


def format_indicator_breakdown(results: list) -> str:
    """Format ranked indicator breakdown with top 3 and bottom 3."""
    if not results:
        return "No backtest results."

    symbol = results[0].symbol
    days = results[0].period_days
    capital = results[0].starting_capital

    active = [r for r in results if r.total_trades > 0]
    inactive = [r for r in results if r.total_trades == 0]

    lines = [
        f"\U0001f3af Indicator Breakdown: {symbol} ({days}d)",
        f"Starting: ${capital:,.0f} | 20% sizing | 0.035% fees",
        "\u2501" * 20,
        "",
        "\U0001f947 TOP 3 PERFORMERS",
        "\u2501" * 20,
    ]

    for i, r in enumerate(active[:3], 1):
        medal = ["\U0001f947", "\U0001f948", "\U0001f949"][i - 1]
        pf = f"{r.profit_factor:.2f}" if r.profit_factor != float("inf") else "inf"
        lines.append(
            f"{medal} {r.label}\n"
            f"   Net: {'+' if r.net_pnl >= 0 else ''}{r.net_pnl:,.2f} ({'+' if r.return_pct >= 0 else ''}{r.return_pct:.1f}%) | "
            f"Fees: ${r.total_fees:,.2f}\n"
            f"   WR: {r.win_rate:.0f}% | PF: {pf} | EV: {'+' if r.expectancy >= 0 else ''}{r.expectancy:,.2f} | Trades: {r.total_trades}"
        )

    lines.extend(["", "\U0001f534 BOTTOM 3 PERFORMERS", "\u2501" * 20])

    for r in active[-3:]:
        pf = f"{r.profit_factor:.2f}" if r.profit_factor != float("inf") else "inf"
        lines.append(
            f"\u274c {r.label}\n"
            f"   Net: {'+' if r.net_pnl >= 0 else ''}{r.net_pnl:,.2f} ({'+' if r.return_pct >= 0 else ''}{r.return_pct:.1f}%) | "
            f"Fees: ${r.total_fees:,.2f}\n"
            f"   WR: {r.win_rate:.0f}% | PF: {pf} | EV: {'+' if r.expectancy >= 0 else ''}{r.expectancy:,.2f} | Trades: {r.total_trades}"
        )

    lines.extend(["", "\U0001f4ca FULL RANKING", "\u2501" * 20])
    for i, r in enumerate(active, 1):
        icon = "\U0001f7e2" if r.net_pnl >= 0 else "\U0001f534"
        lines.append(
            f"{i:2d}. {r.label:25s} {'+' if r.net_pnl >= 0 else ''}{r.net_pnl:>8,.2f} {icon} "
            f"({'+' if r.return_pct >= 0 else ''}{r.return_pct:.1f}%, {r.total_trades}T)"
        )

    if inactive:
        lines.append(f"\n\u26a0\ufe0f {len(inactive)} combos had 0 signals")

    return "\n".join(lines)


def format_no_signal(data: dict) -> str:
    """Format scan result when no trade signal is generated."""
    symbol = data.get("symbol", "???")
    price = data.get("entry_price", 0)
    indicators = data.get("indicators", {})

    indicator_lines = []
    for key, val in indicators.items():
        sig = val.get("signal")
        detail = val.get("detail", "")
        if sig == "bullish":
            icon = "\U0001f7e2"
        elif sig == "bearish":
            icon = "\U0001f534"
        else:
            icon = "\u26aa"
        indicator_lines.append(f"  {icon} {detail}")

    indicators_text = "\n".join(indicator_lines) if indicator_lines else "  No data"
    price_str = f"${price:,.2f}" if price else "N/A"

    return (
        f"\U0001f4ca Signal: {symbol} \u2014 NO TRADE\n"
        f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        f"Price: {price_str}\n"
        f"Indicators:\n{indicators_text}\n"
        f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501"
    )


def format_error(message: str) -> str:
    return f"\u274c Error: {message}"
