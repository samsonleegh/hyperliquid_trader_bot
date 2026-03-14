"""Market data fetching: prices, candles, orderbook."""

import logging
import time
from collections import deque

import pandas as pd

from src.exchange.client import HyperliquidClient

# Interval string to seconds
_INTERVAL_SECS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "1d": 86400, "1w": 604800,
}

logger = logging.getLogger(__name__)


class MarketDataFetcher:
    def __init__(self, client: HyperliquidClient) -> None:
        self.client = client
        # Store last 24 OI snapshots per symbol (~6 hours at 15min scan interval)
        self._oi_history: dict[str, deque[float]] = {}

    async def get_current_price(self, symbol: str) -> float:
        """Get the current mid price for a symbol."""
        mids = await self.get_all_mids()
        if symbol not in mids:
            raise ValueError(f"Symbol {symbol} not found")
        return mids[symbol]

    async def get_candles(
        self, symbol: str, interval: str = "15m", limit: int = 100
    ) -> pd.DataFrame:
        """Fetch OHLCV candle data as a DataFrame."""
        try:
            end_time = int(time.time() * 1000)
            interval_secs = _INTERVAL_SECS.get(interval, 900)
            start_time = end_time - (limit * interval_secs * 1000)
            if symbol not in self.client.info.name_to_coin:
                raise ValueError(f"Symbol '{symbol}' not found on Hyperliquid. Check the exact coin name.")
            raw = await self.client._run_sync(
                self.client.info.candles_snapshot,
                symbol,
                interval,
                start_time,
                end_time,
            )
            if not raw:
                return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

            df = pd.DataFrame(raw)
            df = df.rename(columns={
                "t": "timestamp",
                "o": "open",
                "h": "high",
                "l": "low",
                "c": "close",
                "v": "volume",
            })
            for col in ["open", "high", "low", "close", "volume"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            if "timestamp" in df.columns:
                df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            df = df.sort_values("timestamp").reset_index(drop=True)
            return df[["timestamp", "open", "high", "low", "close", "volume"]]
        except Exception:
            logger.exception("Failed to fetch candles for %s", symbol)
            raise

    async def get_orderbook(self, symbol: str, depth: int = 5) -> dict:
        """Get the order book with bids and asks."""
        try:
            book = await self.client._run_sync(
                self.client.info.l2_snapshot, symbol
            )
            bids = [(float(level["px"]), float(level["sz"])) for level in book.get("levels", [[]])[0][:depth]]
            asks = [(float(level["px"]), float(level["sz"])) for level in book.get("levels", [[], []])[1][:depth]]
            return {"bids": bids, "asks": asks}
        except Exception:
            logger.exception("Failed to fetch orderbook for %s", symbol)
            raise

    async def get_all_mids(self) -> dict[str, float]:
        """Get current mid prices for all assets."""
        try:
            raw = await self.client._run_sync(self.client.info.all_mids)
            return {k: float(v) for k, v in raw.items()}
        except Exception:
            logger.exception("Failed to fetch all mids")
            raise

    async def get_funding_and_oi(self, symbol: str) -> dict:
        """Get current funding rate (hourly) and open interest for a symbol.

        Also stores OI snapshots for rate-of-change calculation.
        """
        try:
            raw = await self.client._run_sync(self.client.info.meta_and_asset_ctxs)
            meta = raw[0]  # metadata
            ctxs = raw[1]  # asset contexts

            for asset, ctx in zip(meta.get("universe", []), ctxs):
                if asset.get("name") == symbol:
                    oi = float(ctx.get("openInterest", 0))

                    # Store OI snapshot for history
                    if symbol not in self._oi_history:
                        self._oi_history[symbol] = deque(maxlen=24)
                    self._oi_history[symbol].append(oi)

                    return {
                        "funding_rate": float(ctx.get("funding") or 0),
                        "open_interest": oi,
                        "oi_history": list(self._oi_history[symbol]),
                        "mark_price": float(ctx.get("markPx") or 0),
                        "premium": float(ctx.get("premium") or 0),
                        "day_volume": float(ctx.get("dayNtlVlm") or 0),
                    }
            return {}
        except Exception:
            logger.exception("Failed to fetch funding/OI for %s", symbol)
            return {}

    async def get_funding_history(self, symbol: str, hours: int = 24) -> list[dict]:
        """Get historical hourly funding rates."""
        try:
            end_time = int(time.time() * 1000)
            start_time = end_time - (hours * 3600 * 1000)
            raw = await self.client._run_sync(
                self.client.info.funding_history, symbol, start_time, end_time
            )
            return [
                {"time": r["time"], "rate": float(r["fundingRate"]), "premium": float(r["premium"])}
                for r in raw
            ]
        except Exception:
            logger.exception("Failed to fetch funding history for %s", symbol)
            return []

    async def get_all_funding_and_oi(self) -> list[dict]:
        """Get funding rate, OI, mark price, premium, and volume for ALL perp coins.

        Uses a single API call (meta_and_asset_ctxs) so this is efficient.
        """
        try:
            raw = await self.client._run_sync(self.client.info.meta_and_asset_ctxs)
            meta = raw[0]
            ctxs = raw[1]

            results = []
            for asset, ctx in zip(meta.get("universe", []), ctxs):
                symbol = asset.get("name", "")
                if not symbol:
                    continue
                oi = float(ctx.get("openInterest", 0))

                # Update in-memory OI history
                if symbol not in self._oi_history:
                    self._oi_history[symbol] = deque(maxlen=24)
                self._oi_history[symbol].append(oi)

                results.append({
                    "symbol": symbol,
                    "funding_rate": float(ctx.get("funding") or 0),
                    "open_interest": oi,
                    "mark_price": float(ctx.get("markPx") or 0),
                    "premium": float(ctx.get("premium") or 0),
                    "day_volume": float(ctx.get("dayNtlVlm") or 0),
                })
            return results
        except Exception:
            logger.exception("Failed to fetch all funding/OI data")
            return []

    async def get_market_info(self, symbol: str) -> dict:
        """Get market metadata (tick size, lot size, max leverage, etc.)."""
        try:
            meta = await self.client._run_sync(self.client.info.meta)
            for asset in meta.get("universe", []):
                if asset.get("name") == symbol:
                    return {
                        "name": asset["name"],
                        "sz_decimals": asset.get("szDecimals", 0),
                        "max_leverage": asset.get("maxLeverage", 1),
                    }
            raise ValueError(f"Market info not found for {symbol}")
        except Exception:
            logger.exception("Failed to fetch market info for %s", symbol)
            raise
