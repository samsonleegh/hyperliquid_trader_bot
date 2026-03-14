"""Technical analysis indicator calculations."""

import logging

import pandas as pd
from ta.trend import EMAIndicator, MACD
from ta.momentum import RSIIndicator, StochRSIIndicator
from ta.volume import AccDistIndexIndicator

logger = logging.getLogger(__name__)


class TechnicalIndicators:
    def __init__(self, df: pd.DataFrame) -> None:
        """Initialize with OHLCV DataFrame (columns: timestamp, open, high, low, close, volume)."""
        self.df = df.copy()

    def calc_emas(self, periods: list[int] | None = None) -> pd.DataFrame:
        if periods is None:
            periods = [9, 21, 50, 200]
        for p in periods:
            col = f"ema_{p}"
            ema = EMAIndicator(close=self.df["close"], window=p)
            self.df[col] = ema.ema_indicator()
        return self.df

    def calc_rsi(self, period: int = 14) -> pd.DataFrame:
        rsi = RSIIndicator(close=self.df["close"], window=period)
        self.df[f"rsi_{period}"] = rsi.rsi()
        return self.df

    def calc_macd(
        self, fast: int = 12, slow: int = 26, signal: int = 9
    ) -> pd.DataFrame:
        macd = MACD(close=self.df["close"], window_fast=fast, window_slow=slow, window_sign=signal)
        self.df["macd"] = macd.macd()
        self.df["macd_signal"] = macd.macd_signal()
        self.df["macd_hist"] = macd.macd_diff()
        return self.df

    def calc_support_resistance(
        self, lookback: int = 20
    ) -> tuple[list[float], list[float]]:
        """Identify support/resistance from recent swing highs and lows."""
        if len(self.df) < lookback:
            return [], []

        recent = self.df.tail(lookback)
        supports: list[float] = []
        resistances: list[float] = []

        highs = recent["high"].values
        lows = recent["low"].values

        for i in range(1, len(highs) - 1):
            if highs[i] > highs[i - 1] and highs[i] > highs[i + 1]:
                resistances.append(float(highs[i]))
            if lows[i] < lows[i - 1] and lows[i] < lows[i + 1]:
                supports.append(float(lows[i]))

        supports.sort()
        resistances.sort()
        return supports, resistances

    def calc_fvg(self) -> pd.DataFrame:
        """Detect Fair Value Gaps (3-candle imbalance patterns).

        Bullish FVG: candle_1.high < candle_3.low (gap up)
        Bearish FVG: candle_1.low > candle_3.high (gap down)

        Adds columns: fvg_type ('bullish'/'bearish'/None), fvg_top, fvg_bottom
        """
        self.df["fvg_type"] = None
        self.df["fvg_top"] = None
        self.df["fvg_bottom"] = None

        highs = self.df["high"].values
        lows = self.df["low"].values

        for i in range(2, len(self.df)):
            # Bullish FVG: gap between candle 1 high and candle 3 low
            if lows[i] > highs[i - 2]:
                self.df.iloc[i, self.df.columns.get_loc("fvg_type")] = "bullish"
                self.df.iloc[i, self.df.columns.get_loc("fvg_bottom")] = float(highs[i - 2])
                self.df.iloc[i, self.df.columns.get_loc("fvg_top")] = float(lows[i])
            # Bearish FVG: gap between candle 1 low and candle 3 high
            elif highs[i] < lows[i - 2]:
                self.df.iloc[i, self.df.columns.get_loc("fvg_type")] = "bearish"
                self.df.iloc[i, self.df.columns.get_loc("fvg_top")] = float(lows[i - 2])
                self.df.iloc[i, self.df.columns.get_loc("fvg_bottom")] = float(highs[i])

        return self.df

    def get_unfilled_fvgs(self, current_price: float, lookback: int = 50) -> list[dict]:
        """Get recent FVGs that haven't been filled by price yet."""
        if "fvg_type" not in self.df.columns:
            self.calc_fvg()

        recent = self.df.tail(lookback)
        unfilled = []

        for idx, row in recent.iterrows():
            if row["fvg_type"] is None:
                continue
            top = row["fvg_top"]
            bottom = row["fvg_bottom"]
            if top is None or bottom is None:
                continue

            # Check if price has filled the gap since it formed
            subsequent = self.df.loc[idx:]
            if row["fvg_type"] == "bullish":
                # Filled if price dropped into the gap
                filled = (subsequent["low"] <= bottom).any()
                if not filled:
                    unfilled.append({
                        "type": "bullish",
                        "top": float(top),
                        "bottom": float(bottom),
                        "distance_pct": (current_price - top) / current_price * 100,
                    })
            else:
                # Filled if price rose into the gap
                filled = (subsequent["high"] >= top).any()
                if not filled:
                    unfilled.append({
                        "type": "bearish",
                        "top": float(top),
                        "bottom": float(bottom),
                        "distance_pct": (bottom - current_price) / current_price * 100,
                    })

        return unfilled

    def calc_stoch_rsi(self, period: int = 14, smooth_k: int = 3, smooth_d: int = 3) -> pd.DataFrame:
        """Stochastic RSI with %K and %D lines."""
        stoch_rsi = StochRSIIndicator(
            close=self.df["close"], window=period,
            smooth1=smooth_k, smooth2=smooth_d,
        )
        self.df["stoch_rsi_k"] = stoch_rsi.stochrsi_k()
        self.df["stoch_rsi_d"] = stoch_rsi.stochrsi_d()
        return self.df

    def calc_volume_profile(self, lookback: int = 20) -> pd.DataFrame:
        self.df["volume_sma"] = self.df["volume"].rolling(window=lookback).mean()
        self.df["volume_ratio"] = self.df["volume"] / self.df["volume_sma"]
        return self.df

    def calc_ad(self, sma_period: int = 20) -> pd.DataFrame:
        """Accumulation/Distribution line with SMA for trend detection."""
        ad = AccDistIndexIndicator(
            high=self.df["high"],
            low=self.df["low"],
            close=self.df["close"],
            volume=self.df["volume"],
        )
        self.df["ad_line"] = ad.acc_dist_index()
        self.df["ad_sma"] = self.df["ad_line"].rolling(window=sma_period).mean()
        return self.df

    def calc_all(self) -> pd.DataFrame:
        self.calc_emas()
        self.calc_rsi()
        self.calc_macd()
        self.calc_stoch_rsi()
        self.calc_volume_profile()
        self.calc_fvg()
        self.calc_ad()
        return self.df
