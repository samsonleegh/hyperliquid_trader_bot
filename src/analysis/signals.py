"""Signal generation from technical indicators."""

import logging

from src.analysis.indicators import TechnicalIndicators
from src.config import settings
from src.exchange.orders import _round_price

logger = logging.getLogger(__name__)

# Active indicators — remove names from this set to disable them
ACTIVE_INDICATORS: set[str] = {"ema", "rsi", "macd", "support_resistance", "volume", "fvg", "ad", "stoch_rsi"}

# Hyperliquid funding is hourly. ±0.01% hourly = ±0.08% 8h equivalent (extreme).
# Threshold set at ±0.005% hourly (~±0.04% 8h equivalent) for moderate signal.
FUNDING_EXTREME_THRESHOLD = 0.0001  # 0.01% hourly (very extreme)
FUNDING_MODERATE_THRESHOLD = 0.00005  # 0.005% hourly (moderate)

# OI rate-of-change threshold: 5% change over lookback period is significant
OI_ROC_THRESHOLD = 0.05


class SignalGenerator:
    def __init__(self, indicators: TechnicalIndicators) -> None:
        self.ind = indicators
        self.df = indicators.df

    def check_ema_crossover(self) -> dict:
        """Check EMA 9/21 crossover."""
        if "ema_9" not in self.df.columns or "ema_21" not in self.df.columns:
            return {"signal": None, "detail": "EMA data unavailable"}

        last = self.df.iloc[-1]
        prev = self.df.iloc[-2] if len(self.df) > 1 else last

        if last["ema_9"] > last["ema_21"] and prev["ema_9"] <= prev["ema_21"]:
            return {"signal": "bullish", "detail": "EMA 9/21 bullish cross"}
        elif last["ema_9"] < last["ema_21"] and prev["ema_9"] >= prev["ema_21"]:
            return {"signal": "bearish", "detail": "EMA 9/21 bearish cross"}
        elif last["ema_9"] > last["ema_21"]:
            return {"signal": "bullish", "detail": "EMA 9 above 21"}
        elif last["ema_9"] < last["ema_21"]:
            return {"signal": "bearish", "detail": "EMA 9 below 21"}
        return {"signal": None, "detail": "EMA neutral"}

    def check_rsi(self) -> dict:
        if "rsi_14" not in self.df.columns:
            return {"signal": None, "value": None, "detail": "RSI unavailable"}

        rsi_val = float(self.df.iloc[-1]["rsi_14"])
        if rsi_val < 30:
            return {"signal": "bullish", "value": rsi_val, "detail": f"RSI(14): {rsi_val:.0f} (oversold)"}
        elif rsi_val > 70:
            return {"signal": "bearish", "value": rsi_val, "detail": f"RSI(14): {rsi_val:.0f} (overbought)"}
        return {"signal": None, "value": rsi_val, "detail": f"RSI(14): {rsi_val:.0f} (neutral)"}

    def check_macd(self) -> dict:
        if "macd" not in self.df.columns or "macd_signal" not in self.df.columns:
            return {"signal": None, "detail": "MACD unavailable"}

        last = self.df.iloc[-1]
        prev = self.df.iloc[-2] if len(self.df) > 1 else last

        if last["macd"] > last["macd_signal"] and prev["macd"] <= prev["macd_signal"]:
            return {"signal": "bullish", "detail": "MACD: bullish crossover"}
        elif last["macd"] < last["macd_signal"] and prev["macd"] >= prev["macd_signal"]:
            return {"signal": "bearish", "detail": "MACD: bearish crossover"}
        elif last["macd"] > last["macd_signal"]:
            return {"signal": "bullish", "detail": "MACD: bullish"}
        elif last["macd"] < last["macd_signal"]:
            return {"signal": "bearish", "detail": "MACD: bearish"}
        return {"signal": None, "detail": "MACD: neutral"}

    def check_support_resistance(self, current_price: float) -> dict:
        supports, resistances = self.ind.calc_support_resistance()
        nearest_support = max((s for s in supports if s < current_price), default=None)
        nearest_resistance = min((r for r in resistances if r > current_price), default=None)

        signal = None
        detail = "S/R: neutral"
        if nearest_support and (current_price - nearest_support) / current_price < 0.02:
            signal = "bullish"
            detail = f"Near support: ${nearest_support:,.2f}"
        elif nearest_resistance and (nearest_resistance - current_price) / current_price < 0.02:
            signal = "bearish"
            detail = f"Near resistance: ${nearest_resistance:,.2f}"

        return {
            "signal": signal,
            "nearest_support": nearest_support,
            "nearest_resistance": nearest_resistance,
            "detail": detail,
        }

    def check_volume(self) -> dict:
        if "volume_ratio" not in self.df.columns:
            return {"signal": None, "ratio": None, "detail": "Volume data unavailable"}

        ratio = float(self.df.iloc[-1]["volume_ratio"])
        if ratio > 2.0:
            return {"signal": "bullish", "ratio": ratio, "detail": f"Volume spike: {ratio:.1f}x average"}
        return {"signal": None, "ratio": ratio, "detail": f"Volume: {ratio:.1f}x average"}

    def check_fvg(self, current_price: float) -> dict:
        """Check for unfilled Fair Value Gaps near current price."""
        unfilled = self.ind.get_unfilled_fvgs(current_price, lookback=50)
        if not unfilled:
            return {"signal": None, "detail": "No unfilled FVGs nearby"}

        # Find nearest FVG within 3% of current price
        nearest = None
        for fvg in unfilled:
            if abs(fvg["distance_pct"]) < 3.0:
                if nearest is None or abs(fvg["distance_pct"]) < abs(nearest["distance_pct"]):
                    nearest = fvg

        if not nearest:
            return {"signal": None, "detail": f"FVG: {len(unfilled)} unfilled (none nearby)"}

        if nearest["type"] == "bullish":
            # Price is near/above a bullish FVG — expect it to fill (pull back) then continue up
            return {
                "signal": "bullish",
                "detail": f"Bullish FVG: ${nearest['bottom']:,.2f}-${nearest['top']:,.2f}",
                "fvg": nearest,
            }
        else:
            return {
                "signal": "bearish",
                "detail": f"Bearish FVG: ${nearest['bottom']:,.2f}-${nearest['top']:,.2f}",
                "fvg": nearest,
            }

    def check_stoch_rsi(self) -> dict:
        """Check Stochastic RSI for overbought/oversold + %K/%D crossover."""
        if "stoch_rsi_k" not in self.df.columns or "stoch_rsi_d" not in self.df.columns:
            return {"signal": None, "detail": "StochRSI unavailable"}

        last = self.df.iloc[-1]
        prev = self.df.iloc[-2] if len(self.df) > 1 else last
        k = float(last["stoch_rsi_k"])
        d = float(last["stoch_rsi_d"])

        # Bullish: %K crosses above %D in oversold zone (<0.2)
        if k < 0.2 and k > d and float(prev["stoch_rsi_k"]) <= float(prev["stoch_rsi_d"]):
            return {"signal": "bullish", "detail": f"StochRSI: bullish cross in oversold ({k:.2f})"}
        # Bearish: %K crosses below %D in overbought zone (>0.8)
        if k > 0.8 and k < d and float(prev["stoch_rsi_k"]) >= float(prev["stoch_rsi_d"]):
            return {"signal": "bearish", "detail": f"StochRSI: bearish cross in overbought ({k:.2f})"}
        # General oversold/overbought
        if k < 0.2:
            return {"signal": "bullish", "detail": f"StochRSI: oversold ({k:.2f})"}
        if k > 0.8:
            return {"signal": "bearish", "detail": f"StochRSI: overbought ({k:.2f})"}
        return {"signal": None, "detail": f"StochRSI: neutral ({k:.2f})"}

    def check_ad(self) -> dict:
        """Check Accumulation/Distribution trend vs price trend."""
        if "ad_line" not in self.df.columns or "ad_sma" not in self.df.columns:
            return {"signal": None, "detail": "A/D unavailable"}

        last = self.df.iloc[-1]
        prev = self.df.iloc[-5] if len(self.df) > 5 else self.df.iloc[0]

        ad_rising = last["ad_line"] > prev["ad_line"]
        price_rising = last["close"] > prev["close"]
        ad_above_sma = last["ad_line"] > last["ad_sma"]

        if ad_rising and ad_above_sma:
            if not price_rising:
                # A/D rising while price flat/falling = bullish divergence (accumulation)
                return {"signal": "bullish", "detail": "A/D: accumulation (bullish divergence)"}
            return {"signal": "bullish", "detail": "A/D: accumulation confirmed"}
        elif not ad_rising and not ad_above_sma:
            if price_rising:
                # A/D falling while price rising = bearish divergence (distribution)
                return {"signal": "bearish", "detail": "A/D: distribution (bearish divergence)"}
            return {"signal": "bearish", "detail": "A/D: distribution confirmed"}
        return {"signal": None, "detail": "A/D: neutral"}

    def generate_signal(self, symbol: str, current_price: float, active_set: set[str] | None = None, extra_checks: dict | None = None) -> dict | None:
        """Aggregate all checks and generate a trade signal if indicators agree."""
        use_indicators = active_set if active_set is not None else ACTIVE_INDICATORS
        all_checks = {
            "ema": self.check_ema_crossover,
            "rsi": self.check_rsi,
            "macd": self.check_macd,
            "support_resistance": lambda: self.check_support_resistance(current_price),
            "volume": self.check_volume,
            "fvg": lambda: self.check_fvg(current_price),
            "ad": self.check_ad,
            "stoch_rsi": self.check_stoch_rsi,
        }

        # Run ALL indicators for display
        checks = {}
        for name, func in all_checks.items():
            checks[name] = func()

        # Merge pre-computed async checks (funding_rate, open_interest)
        if extra_checks:
            for name, result in extra_checks.items():
                checks[name] = result

        # Only use strategy indicators for trading decision
        active = {k: v for k, v in checks.items() if k in use_indicators}
        bullish = sum(1 for c in active.values() if c.get("signal") == "bullish")
        bearish = sum(1 for c in active.values() if c.get("signal") == "bearish")
        total = len(active)

        if bullish < 2 and bearish < 2:
            return None

        direction = "long" if bullish >= bearish else "short"
        confidence = max(bullish, bearish) / total

        # Skip weak signals below 50% confidence
        if confidence < 0.5:
            return None

        sr = checks.get("support_resistance", {})
        sl_pct = settings.default_sl_pct
        tp_pct = settings.default_tp_pct

        default_sl = current_price * (1 - sl_pct / 100) if direction == "long" else current_price * (1 + sl_pct / 100)
        default_tp = current_price * (1 + tp_pct / 100) if direction == "long" else current_price * (1 - tp_pct / 100)
        # Minimum 1% distance from entry to avoid near-entry SL/TP that get invalidated by slippage
        min_distance = current_price * 0.01

        if direction == "long":
            sl_price = sr.get("nearest_support") or default_sl
            tp_price = sr.get("nearest_resistance") or default_tp
            # Validate: SL must be meaningfully below entry, TP meaningfully above
            if current_price - sl_price < min_distance:
                sl_price = default_sl
            if tp_price - current_price < min_distance:
                tp_price = default_tp
        else:
            sl_price = sr.get("nearest_resistance") or default_sl
            tp_price = sr.get("nearest_support") or default_tp
            # Validate: SL must be meaningfully above entry, TP meaningfully below
            if sl_price - current_price < min_distance:
                sl_price = default_sl
            if current_price - tp_price < min_distance:
                tp_price = default_tp

        return {
            "symbol": symbol,
            "direction": direction,
            "confidence": round(confidence, 2),
            "entry_price": current_price,
            "sl_price": _round_price(sl_price, 0),
            "tp_price": _round_price(tp_price, 0),
            "indicators": checks,
        }


    def generate_signal_combos(self, symbol: str, current_price: float, combos: list[list[str]], extra_checks: dict | None = None) -> dict | None:
        """Generate signal using indicator combos. Triggers if ANY combo's indicators all agree."""
        all_checks = {
            "ema": self.check_ema_crossover,
            "rsi": self.check_rsi,
            "macd": self.check_macd,
            "support_resistance": lambda: self.check_support_resistance(current_price),
            "volume": self.check_volume,
            "fvg": lambda: self.check_fvg(current_price),
            "ad": self.check_ad,
            "stoch_rsi": self.check_stoch_rsi,
        }

        # Run all unique indicators across all combos
        needed = set()
        for combo in combos:
            needed.update(combo)

        # Run ALL indicators for display
        checks = {}
        for name, func in all_checks.items():
            checks[name] = func()

        # Merge pre-computed async checks (funding_rate, open_interest)
        if extra_checks:
            for name, result in extra_checks.items():
                checks[name] = result

        # Check each combo — does the group unanimously agree?
        best_combo = None
        best_direction = None
        best_score = 0

        for combo in combos:
            combo_checks = {k: checks[k] for k in combo if k in checks}
            bullish = sum(1 for c in combo_checks.values() if c.get("signal") == "bullish")
            bearish = sum(1 for c in combo_checks.values() if c.get("signal") == "bearish")
            total = len(combo)

            # All indicators in the combo must agree
            if bullish == total:
                if bullish > best_score:
                    best_score = bullish
                    best_direction = "long"
                    best_combo = combo
            elif bearish == total:
                if bearish > best_score:
                    best_score = bearish
                    best_direction = "short"
                    best_combo = combo

        if not best_combo or not best_direction:
            return None

        # Confidence = proportion of ALL needed indicators that agree with the direction
        all_active = {k: checks[k] for k in needed}
        agreeing = sum(1 for c in all_active.values() if c.get("signal") == ("bullish" if best_direction == "long" else "bearish"))
        confidence = agreeing / len(needed)

        if confidence < 0.5:
            return None

        sr = checks.get("support_resistance", {})
        sl_pct = settings.default_sl_pct
        tp_pct = settings.default_tp_pct
        nearest_support = sr.get("nearest_support")
        nearest_resistance = sr.get("nearest_resistance")

        default_sl = current_price * (1 - sl_pct / 100) if best_direction == "long" else current_price * (1 + sl_pct / 100)
        default_tp = current_price * (1 + tp_pct / 100) if best_direction == "long" else current_price * (1 - tp_pct / 100)
        min_distance = current_price * 0.01

        if best_direction == "long":
            sl_price = nearest_support if nearest_support else default_sl
            tp_price = nearest_resistance if nearest_resistance else default_tp
            if current_price - sl_price < min_distance:
                sl_price = default_sl
            if tp_price - current_price < min_distance:
                tp_price = default_tp
        else:
            sl_price = nearest_resistance if nearest_resistance else default_sl
            tp_price = nearest_support if nearest_support else default_tp
            if sl_price - current_price < min_distance:
                sl_price = default_sl
            if current_price - tp_price < min_distance:
                tp_price = default_tp

        return {
            "symbol": symbol,
            "direction": best_direction,
            "confidence": round(confidence, 2),
            "entry_price": current_price,
            "sl_price": _round_price(sl_price, 0),
            "tp_price": _round_price(tp_price, 0),
            "triggered_combo": "+".join(best_combo),
            "indicators": checks,
        }


def check_funding_rate(funding_data: dict, funding_history: list[dict], price_change_pct: float = 0) -> dict:
    """Check funding rate + price direction for positioning signal.

    Hyperliquid uses hourly funding. Best signals combine funding with price:
    - Negative funding + price rising = shorts squeezed (strong bullish)
    - Positive funding + price falling = longs flushed (strong bearish)
    - Funding extreme but price confirming the crowd = mixed/neutral
    """
    rate = funding_data.get("funding_rate", 0)
    rate_8h = rate * 8  # Normalize to 8h equivalent for display

    detail_8h = f"{rate_8h * 100:+.4f}%/8h"
    price_rising = price_change_pct > 0.5  # >0.5% up
    price_falling = price_change_pct < -0.5  # >0.5% down

    if abs(rate) >= FUNDING_EXTREME_THRESHOLD:
        # Very extreme funding
        if rate < 0 and price_rising:
            # Shorts paying + price rising = squeeze in progress
            return {"signal": "bullish", "detail": f"Funding short squeeze ({detail_8h}) + price rising"}
        elif rate > 0 and price_falling:
            # Longs paying + price falling = flush in progress
            return {"signal": "bearish", "detail": f"Funding long flush ({detail_8h}) + price falling"}
        elif rate < 0:
            return {"signal": None, "detail": f"Funding crowded short ({detail_8h}) — awaiting price confirmation"}
        else:
            return {"signal": None, "detail": f"Funding crowded long ({detail_8h}) — awaiting price confirmation"}

    elif abs(rate) >= FUNDING_MODERATE_THRESHOLD:
        if rate < 0 and price_rising:
            return {"signal": "bullish", "detail": f"Funding short + price rising ({detail_8h})"}
        elif rate > 0 and price_falling:
            return {"signal": "bearish", "detail": f"Funding long + price falling ({detail_8h})"}
        elif rate < 0:
            return {"signal": None, "detail": f"Funding slightly short ({detail_8h})"}
        else:
            return {"signal": None, "detail": f"Funding slightly long ({detail_8h})"}

    return {"signal": None, "detail": f"Funding neutral ({detail_8h})"}


def check_open_interest(funding_data: dict, price_change_pct: float, volume_change_pct: float, oi_history: list[float] | None = None) -> dict:
    """Check OI + price direction for positioning signal.

    - OI rising + price rising = new money entering, trend confirmation (bullish)
    - OI rising + price falling = shorts piling in, squeeze setup (bullish contrarian)
    - OI falling + price falling = longs capitulating (bearish)
    - OI falling + price rising = shorts closing, squeeze weakening (neutral/caution)
    """
    oi = funding_data.get("open_interest", 0)
    if oi == 0:
        return {"signal": None, "detail": "OI data unavailable"}

    # Calculate OI rate of change if history available
    if oi_history and len(oi_history) >= 2:
        prev_oi = oi_history[0]
        oi_roc = (oi - prev_oi) / prev_oi if prev_oi > 0 else 0
    else:
        oi_roc = 0

    oi_rising = oi_roc > OI_ROC_THRESHOLD
    oi_falling = oi_roc < -OI_ROC_THRESHOLD
    price_rising = price_change_pct > 0.5
    price_falling = price_change_pct < -0.5

    oi_display = f"OI: {oi:,.0f} ({oi_roc:+.1%})"

    if oi_rising and price_rising:
        return {"signal": "bullish", "detail": f"{oi_display} — new money + price rising (trend confirmed)"}
    elif oi_rising and price_falling:
        return {"signal": "bullish", "detail": f"{oi_display} — shorts piling in + price falling (squeeze setup)"}
    elif oi_falling and price_falling:
        return {"signal": "bearish", "detail": f"{oi_display} — longs capitulating (bearish)"}
    elif oi_falling and price_rising:
        return {"signal": None, "detail": f"{oi_display} — shorts closing, squeeze fading (caution)"}

    return {"signal": None, "detail": f"{oi_display} — neutral"}


async def scan_symbol(symbol: str, market_data_fetcher, risk_settings: dict, active_indicators: set[str] | None = None, combos: list[list[str]] | None = None) -> dict | None:
    """Fetch candles, run TA, and generate a signal for a symbol."""
    try:
        df = await market_data_fetcher.get_candles(symbol, interval="15m", limit=100)
        if df.empty or len(df) < 30:
            logger.warning("Insufficient data for %s (%d candles)", symbol, len(df))
            return None

        ind = TechnicalIndicators(df)
        ind.calc_all()

        current_price = float(df.iloc[-1]["close"])

        # Always fetch funding/OI for display (all 10 indicators shown)
        extra_checks = {}
        try:
            funding_data = await market_data_fetcher.get_funding_and_oi(symbol)
            funding_history = await market_data_fetcher.get_funding_history(symbol, hours=24)

            if len(df) >= 8:
                price_change_pct = (float(df.iloc[-1]["close"]) - float(df.iloc[-8]["close"])) / float(df.iloc[-8]["close"]) * 100
                recent_vol = df["volume"].iloc[-4:].mean()
                prev_vol = df["volume"].iloc[-8:-4].mean()
                volume_change_pct = (recent_vol - prev_vol) / prev_vol if prev_vol > 0 else 0
            else:
                price_change_pct = 0
                volume_change_pct = 0

            extra_checks["funding_rate"] = check_funding_rate(funding_data, funding_history, price_change_pct)
            oi_history = funding_data.get("oi_history", [])
            extra_checks["open_interest"] = check_open_interest(funding_data, price_change_pct, volume_change_pct, oi_history=oi_history)
        except Exception:
            logger.warning("Could not fetch funding/OI for %s", symbol)

        gen = SignalGenerator(ind)
        if combos:
            signal = gen.generate_signal_combos(symbol, current_price, combos, extra_checks=extra_checks or None)
        else:
            signal = gen.generate_signal(symbol, current_price, active_set=active_indicators, extra_checks=extra_checks or None)

        if signal:
            max_pos = risk_settings.get("max_position_size", settings.max_position_size)
            signal["suggested_size"] = round(max_pos / current_price, 6)
            logger.info("Signal generated for %s: %s (confidence=%.2f)", symbol, signal["direction"], signal["confidence"])
            return signal

        # No signal — still return indicator details for display
        all_checks_map = {
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
        for name, func in all_checks_map.items():
            if name in (active_indicators or ACTIVE_INDICATORS):
                checks[name] = func()
        if extra_checks:
            checks.update(extra_checks)

        return {
            "symbol": symbol,
            "direction": None,
            "confidence": 0,
            "entry_price": current_price,
            "indicators": checks,
        }
    except Exception:
        logger.exception("Failed to scan %s", symbol)
        return None
