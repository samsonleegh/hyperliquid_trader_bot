"""Backtesting engine — replays SignalGenerator over historical candles."""

import logging
from dataclasses import dataclass, field
from itertools import combinations

import pandas as pd

from src.analysis.indicators import TechnicalIndicators
from src.analysis.signals import SignalGenerator, check_funding_rate, check_open_interest
from src.config import settings

logger = logging.getLogger(__name__)

MIN_WARMUP = 50
ALL_INDICATORS = ["ema", "rsi", "macd", "support_resistance", "volume", "fvg", "ad", "stoch_rsi", "funding_rate", "open_interest"]

TAKER_FEE = 0.00035  # 0.035% per side
MAX_PORTFOLIO_PCT = 0.20  # 20% of portfolio per trade


@dataclass
class Trade:
    entry_idx: int
    entry_price: float
    side: str
    sl_price: float
    tp_price: float
    size_usd: float = 0.0
    exit_idx: int | None = None
    exit_price: float | None = None
    pnl: float = 0.0
    fees: float = 0.0
    net_pnl: float = 0.0
    exit_reason: str = ""


@dataclass
class BacktestResult:
    symbol: str
    period_days: int
    total_candles: int
    starting_capital: float = 0.0
    final_capital: float = 0.0
    label: str = ""
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_pnl: float = 0.0
    total_fees: float = 0.0
    net_pnl: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    win_rate: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float = 0.0
    best_trade: float = 0.0
    worst_trade: float = 0.0
    avg_hold_bars: float = 0.0
    return_pct: float = 0.0
    expectancy: float = 0.0
    trades: list[Trade] = field(default_factory=list)


def _generate_signal_with_subset(
    gen: SignalGenerator,
    symbol: str,
    current_price: float,
    active_indicators: set[str],
    sl_pct: float,
    tp_pct: float,
    extra_checks: dict | None = None,
) -> dict | None:
    """Run signal generation using only a subset of indicators."""
    all_checks = {
        "ema": gen.check_ema_crossover,
        "rsi": gen.check_rsi,
        "macd": gen.check_macd,
        "support_resistance": lambda: gen.check_support_resistance(current_price),
        "volume": gen.check_volume,
        "fvg": lambda: gen.check_fvg(current_price),
        "ad": gen.check_ad,
        "stoch_rsi": gen.check_stoch_rsi,
    }

    checks = {}
    for name, func in all_checks.items():
        if name in active_indicators:
            checks[name] = func()
        else:
            checks[name] = {"signal": None, "detail": "disabled"}

    # Merge pre-computed async checks (funding_rate, open_interest)
    if extra_checks:
        for name, result in extra_checks.items():
            checks[name] = result

    active_checks = {k: v for k, v in checks.items() if k in active_indicators}
    bullish = sum(1 for c in active_checks.values() if c.get("signal") == "bullish")
    bearish = sum(1 for c in active_checks.values() if c.get("signal") == "bearish")

    min_agreement = 1 if len(active_indicators) == 1 else 2
    if bullish < min_agreement and bearish < min_agreement:
        return None

    direction = "long" if bullish >= bearish else "short"
    total = len(active_indicators)
    confidence = max(bullish, bearish) / total

    sr = checks.get("support_resistance", {})
    nearest_support = sr.get("nearest_support")
    nearest_resistance = sr.get("nearest_resistance")

    default_sl = current_price * (1 - sl_pct / 100) if direction == "long" else current_price * (1 + sl_pct / 100)
    default_tp = current_price * (1 + tp_pct / 100) if direction == "long" else current_price * (1 - tp_pct / 100)

    if direction == "long":
        sl_price = nearest_support if nearest_support else default_sl
        tp_price = nearest_resistance if nearest_resistance else default_tp
        if sl_price >= current_price:
            sl_price = default_sl
        if tp_price <= current_price:
            tp_price = default_tp
    else:
        sl_price = nearest_resistance if nearest_resistance else default_sl
        tp_price = nearest_support if nearest_support else default_tp
        if sl_price <= current_price:
            sl_price = default_sl
        if tp_price >= current_price:
            tp_price = default_tp

    return {
        "direction": direction,
        "confidence": confidence,
        "sl_price": round(sl_price, 4),
        "tp_price": round(tp_price, 4),
    }


def _generate_signal_with_combos(
    gen: SignalGenerator,
    symbol: str,
    current_price: float,
    combos: list[list[str]],
    sl_pct: float,
    tp_pct: float,
    extra_checks: dict | None = None,
) -> dict | None:
    """Run signal generation using combo logic — any combo must unanimously agree."""
    all_checks = {
        "ema": gen.check_ema_crossover,
        "rsi": gen.check_rsi,
        "macd": gen.check_macd,
        "support_resistance": lambda: gen.check_support_resistance(current_price),
        "volume": gen.check_volume,
        "fvg": lambda: gen.check_fvg(current_price),
        "ad": gen.check_ad,
        "stoch_rsi": gen.check_stoch_rsi,
    }

    needed = set()
    for combo in combos:
        needed.update(combo)

    checks = {}
    for name, func in all_checks.items():
        if name in needed:
            checks[name] = func()
        else:
            checks[name] = {"signal": None, "detail": "disabled"}

    # Merge pre-computed async checks (funding_rate, open_interest)
    if extra_checks:
        for name, result in extra_checks.items():
            checks[name] = result
            needed.add(name)

    best_combo = None
    best_direction = None
    best_score = 0

    for combo in combos:
        combo_checks = {k: checks[k] for k in combo if k in checks}
        bullish = sum(1 for c in combo_checks.values() if c.get("signal") == "bullish")
        bearish = sum(1 for c in combo_checks.values() if c.get("signal") == "bearish")
        total = len(combo)

        if bullish == total and bullish > best_score:
            best_score = bullish
            best_direction = "long"
            best_combo = combo
        elif bearish == total and bearish > best_score:
            best_score = bearish
            best_direction = "short"
            best_combo = combo

    if not best_combo or not best_direction:
        return None

    all_active = {k: checks[k] for k in needed}
    agreeing = sum(1 for c in all_active.values() if c.get("signal") == ("bullish" if best_direction == "long" else "bearish"))
    confidence = agreeing / len(needed)

    sr = checks.get("support_resistance", {})
    nearest_support = sr.get("nearest_support")
    nearest_resistance = sr.get("nearest_resistance")

    default_sl = current_price * (1 - sl_pct / 100) if best_direction == "long" else current_price * (1 + sl_pct / 100)
    default_tp = current_price * (1 + tp_pct / 100) if best_direction == "long" else current_price * (1 - tp_pct / 100)

    if best_direction == "long":
        sl_price = nearest_support if nearest_support else default_sl
        tp_price = nearest_resistance if nearest_resistance else default_tp
        if sl_price >= current_price:
            sl_price = default_sl
        if tp_price <= current_price:
            tp_price = default_tp
    else:
        sl_price = nearest_resistance if nearest_resistance else default_sl
        tp_price = nearest_support if nearest_support else default_tp
        if sl_price <= current_price:
            sl_price = default_sl
        if tp_price >= current_price:
            tp_price = default_tp

    return {
        "direction": best_direction,
        "confidence": confidence,
        "sl_price": round(sl_price, 4),
        "tp_price": round(tp_price, 4),
    }


def _simulate_trades(
    df: pd.DataFrame,
    symbol: str,
    period_days: int,
    active_indicators: set[str],
    sl_pct: float,
    tp_pct: float,
    starting_capital: float,
    label: str,
    combos: list[list[str]] | None = None,
    funding_data: pd.DataFrame | None = None,
    oi_data: pd.DataFrame | None = None,
) -> BacktestResult:
    """Core simulation loop with portfolio sizing and fees."""
    result = BacktestResult(
        symbol=symbol,
        period_days=period_days,
        total_candles=len(df),
        starting_capital=starting_capital,
        label=label,
    )

    if len(df) < MIN_WARMUP + 10:
        result.final_capital = starting_capital
        return result

    capital = starting_capital
    open_trade: Trade | None = None
    peak_capital = capital

    for i in range(MIN_WARMUP, len(df)):
        row = df.iloc[i]
        current_price = float(row["close"])
        high = float(row["high"])
        low = float(row["low"])

        # Check SL/TP
        if open_trade is not None:
            if open_trade.side == "long":
                hit_sl = low <= open_trade.sl_price
                hit_tp = high >= open_trade.tp_price
            else:
                hit_sl = high >= open_trade.sl_price
                hit_tp = low <= open_trade.tp_price

            if hit_sl or hit_tp:
                if hit_sl:
                    exit_price = open_trade.sl_price
                    reason = "sl"
                else:
                    exit_price = open_trade.tp_price
                    reason = "tp"

                size = open_trade.size_usd / open_trade.entry_price
                if open_trade.side == "long":
                    gross_pnl = (exit_price - open_trade.entry_price) * size
                else:
                    gross_pnl = (open_trade.entry_price - exit_price) * size

                # Fees: entry + exit (taker both sides)
                exit_notional = size * exit_price
                fees = (open_trade.size_usd * TAKER_FEE) + (exit_notional * TAKER_FEE)
                net_pnl = gross_pnl - fees

                open_trade.exit_idx = i
                open_trade.exit_price = exit_price
                open_trade.pnl = gross_pnl
                open_trade.fees = fees
                open_trade.net_pnl = net_pnl
                open_trade.exit_reason = reason
                result.trades.append(open_trade)
                open_trade = None

                capital += net_pnl
                if capital > peak_capital:
                    peak_capital = capital
                drawdown = peak_capital - capital
                if drawdown > result.max_drawdown:
                    result.max_drawdown = drawdown
                continue

        if open_trade is not None:
            continue

        # Skip if capital too low
        if capital < 10:
            continue

        window = df.iloc[max(0, i - 200):i + 1].copy()
        if len(window) < MIN_WARMUP:
            continue

        try:
            ind = TechnicalIndicators(window)
            ind.calc_all()
            gen = SignalGenerator(ind)

            # Build extra_checks for funding_rate and open_interest
            extra_checks = {}
            use_funding = "funding_rate" in active_indicators or (combos and any("funding_rate" in c for c in combos))
            use_oi = "open_interest" in active_indicators or (combos and any("open_interest" in c for c in combos))

            if use_funding or use_oi:
                # Calculate price change % over last 8 bars (~2h on 15m candles)
                if i >= 8:
                    price_change_pct = (float(df.iloc[i]["close"]) - float(df.iloc[i - 8]["close"])) / float(df.iloc[i - 8]["close"]) * 100
                    recent_vol = df["volume"].iloc[i - 3:i + 1].mean()
                    prev_vol = df["volume"].iloc[i - 7:i - 3].mean()
                    volume_change_pct = (recent_vol - prev_vol) / prev_vol if prev_vol > 0 else 0
                else:
                    price_change_pct = 0
                    volume_change_pct = 0

            if use_funding and funding_data is not None and not funding_data.empty:
                candle_time = row["timestamp"]
                # Find the most recent funding rate at or before this candle
                mask = funding_data["time"] <= candle_time
                if mask.any():
                    funding_row = funding_data.loc[mask].iloc[-1]
                    funding_dict = {"funding_rate": funding_row["rate"]}
                    extra_checks["funding_rate"] = check_funding_rate(funding_dict, [], price_change_pct)

            if use_oi and oi_data is not None and not oi_data.empty:
                candle_time = row["timestamp"]
                mask = oi_data["timestamp"] <= candle_time
                if mask.any():
                    recent_oi = oi_data.loc[mask]
                    oi_val = float(recent_oi.iloc[-1]["open_interest"])
                    oi_history = recent_oi["open_interest"].tolist()[-24:]
                    oi_dict = {"open_interest": oi_val}
                    extra_checks["open_interest"] = check_open_interest(oi_dict, price_change_pct, volume_change_pct, oi_history=oi_history)

            if combos:
                signal = _generate_signal_with_combos(
                    gen, symbol, current_price, combos, sl_pct, tp_pct,
                    extra_checks=extra_checks or None,
                )
            else:
                signal = _generate_signal_with_subset(
                    gen, symbol, current_price, active_indicators, sl_pct, tp_pct,
                    extra_checks=extra_checks or None,
                )
        except Exception:
            continue

        if signal is None:
            continue

        # Skip weak signals (below 50% confidence)
        confidence = signal["confidence"]
        if confidence < 0.5:
            continue

        # Tiered sizing based on confidence
        # 50-66%: half size (10%), 67%+: full size (20%)
        if confidence >= 0.67:
            trade_size_usd = capital * MAX_PORTFOLIO_PCT
        else:
            trade_size_usd = capital * (MAX_PORTFOLIO_PCT / 2)

        open_trade = Trade(
            entry_idx=i,
            entry_price=current_price,
            side=signal["direction"],
            sl_price=signal["sl_price"],
            tp_price=signal["tp_price"],
            size_usd=trade_size_usd,
        )

    # Close remaining trade at last bar
    if open_trade is not None:
        last_price = float(df.iloc[-1]["close"])
        size = open_trade.size_usd / open_trade.entry_price
        if open_trade.side == "long":
            gross_pnl = (last_price - open_trade.entry_price) * size
        else:
            gross_pnl = (open_trade.entry_price - last_price) * size

        exit_notional = size * last_price
        fees = (open_trade.size_usd * TAKER_FEE) + (exit_notional * TAKER_FEE)
        net_pnl = gross_pnl - fees

        open_trade.exit_idx = len(df) - 1
        open_trade.exit_price = last_price
        open_trade.pnl = gross_pnl
        open_trade.fees = fees
        open_trade.net_pnl = net_pnl
        open_trade.exit_reason = "end"
        result.trades.append(open_trade)
        capital += net_pnl

    result.final_capital = capital
    _calc_stats(result)
    return result


def run_backtest(
    df: pd.DataFrame,
    symbol: str,
    period_days: int,
    sl_pct: float | None = None,
    tp_pct: float | None = None,
    starting_capital: float = 1000.0,
    active_indicators: set[str] | None = None,
    combos: list[list[str]] | None = None,
    funding_data: pd.DataFrame | None = None,
    oi_data: pd.DataFrame | None = None,
) -> BacktestResult:
    """Run backtest with all indicators, a custom subset, or combo groups."""
    sl_pct = sl_pct or settings.default_sl_pct
    tp_pct = tp_pct or settings.default_tp_pct

    if combos:
        indicators = set(ind for combo in combos for ind in combo)
        label = " OR ".join("+".join(c) for c in combos)
    elif active_indicators:
        indicators = active_indicators
        label = ",".join(sorted(indicators))
    else:
        indicators = set(ALL_INDICATORS)
        label = "all"

    return _simulate_trades(
        df, symbol, period_days,
        indicators, sl_pct, tp_pct, starting_capital, label, combos=combos,
        funding_data=funding_data, oi_data=oi_data,
    )


def run_indicator_breakdown(
    df: pd.DataFrame,
    symbol: str,
    period_days: int,
    sl_pct: float | None = None,
    tp_pct: float | None = None,
    starting_capital: float = 1000.0,
    funding_data: pd.DataFrame | None = None,
    oi_data: pd.DataFrame | None = None,
) -> list[BacktestResult]:
    """Run backtests for each individual indicator and all 2-indicator combos."""
    sl_pct = sl_pct or settings.default_sl_pct
    tp_pct = tp_pct or settings.default_tp_pct
    results = []

    # All indicators combined
    results.append(_simulate_trades(
        df, symbol, period_days,
        set(ALL_INDICATORS), sl_pct, tp_pct, starting_capital, "ALL COMBINED",
        funding_data=funding_data, oi_data=oi_data,
    ))

    # Each indicator solo
    for ind_name in ALL_INDICATORS:
        result = _simulate_trades(
            df, symbol, period_days,
            {ind_name}, sl_pct, tp_pct, starting_capital, ind_name,
            funding_data=funding_data, oi_data=oi_data,
        )
        results.append(result)

    # All 2-indicator combinations
    for combo in combinations(ALL_INDICATORS, 2):
        label = "+".join(combo)
        result = _simulate_trades(
            df, symbol, period_days,
            set(combo), sl_pct, tp_pct, starting_capital, label,
            funding_data=funding_data, oi_data=oi_data,
        )
        results.append(result)

    results.sort(key=lambda r: r.net_pnl, reverse=True)
    return results


def _calc_stats(result: BacktestResult) -> None:
    if not result.trades:
        result.final_capital = result.starting_capital
        return

    result.total_trades = len(result.trades)
    net_pnls = [t.net_pnl for t in result.trades]
    wins = [p for p in net_pnls if p > 0]
    losses = [p for p in net_pnls if p <= 0]

    result.winning_trades = len(wins)
    result.losing_trades = len(losses)
    result.total_pnl = sum(t.pnl for t in result.trades)
    result.total_fees = sum(t.fees for t in result.trades)
    result.net_pnl = sum(net_pnls)
    result.win_rate = len(wins) / len(net_pnls) * 100 if net_pnls else 0
    result.avg_win = sum(wins) / len(wins) if wins else 0
    result.avg_loss = sum(losses) / len(losses) if losses else 0
    result.best_trade = max(net_pnls) if net_pnls else 0
    result.worst_trade = min(net_pnls) if net_pnls else 0

    if result.starting_capital > 0:
        result.return_pct = (result.final_capital - result.starting_capital) / result.starting_capital * 100
        if result.max_drawdown > 0:
            result.max_drawdown_pct = result.max_drawdown / (result.starting_capital + result.max_drawdown) * 100

    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    result.profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    hold_bars = [
        (t.exit_idx - t.entry_idx) for t in result.trades
        if t.exit_idx is not None
    ]
    result.avg_hold_bars = sum(hold_bars) / len(hold_bars) if hold_bars else 0

    # Expectancy = (WR × Avg Win) − (LR × Avg Loss)
    wr = result.win_rate / 100
    lr = 1 - wr
    result.expectancy = (wr * result.avg_win) - (lr * abs(result.avg_loss))
