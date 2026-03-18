"""Whale movement detection via OI spikes and CoinGlass API."""

import logging

import aiohttp

logger = logging.getLogger(__name__)

COINGLASS_BASE_URL = "https://open-api.coinglass.com/public/v2"


class WhaleDetector:
    def __init__(self, coinglass_api_key: str = "") -> None:
        self.coinglass_api_key = coinglass_api_key

    async def detect_oi_spikes(self, repo, threshold_pct: float) -> list[dict]:
        """Compare last two OI snapshots per coin and flag large changes.

        Returns list of whale event dicts.
        """
        events = []
        try:
            # Get all recent snapshots (last 1 hour to capture two 15-min intervals)
            snapshots = await repo.get_funding_oi_all(hours=1)
            if not snapshots:
                return []

            # Group by symbol
            by_symbol: dict[str, list[dict]] = {}
            for s in snapshots:
                by_symbol.setdefault(s["symbol"], []).append(s)

            for symbol, rows in by_symbol.items():
                if len(rows) < 2:
                    continue

                # Compare last two snapshots
                prev = rows[-2]
                latest = rows[-1]
                prev_oi = prev["open_interest"]
                latest_oi = latest["open_interest"]

                if prev_oi <= 0:
                    continue

                oi_change_pct = ((latest_oi - prev_oi) / prev_oi) * 100

                if abs(oi_change_pct) < threshold_pct:
                    continue

                # Infer direction from OI change + price change
                price_change = latest["mark_price"] - prev["mark_price"]
                if oi_change_pct > 0 and price_change > 0:
                    direction = "long"
                elif oi_change_pct > 0 and price_change < 0:
                    direction = "short"
                elif oi_change_pct < 0 and price_change < 0:
                    direction = "long"  # Longs closing
                else:
                    direction = "short"  # Shorts closing

                events.append({
                    "symbol": symbol,
                    "event_type": "oi_spike",
                    "direction": direction,
                    "magnitude": round(abs(oi_change_pct), 2),
                    "detail": f"OI {'+' if oi_change_pct > 0 else ''}{oi_change_pct:.1f}% in 15m, price {'up' if price_change > 0 else 'down'}",
                    "source": "hyperliquid",
                })

        except Exception:
            logger.exception("Failed to detect OI spikes")

        return events

    async def fetch_coinglass_whales(self) -> list[dict]:
        """Fetch whale positions from CoinGlass Hyperliquid endpoint.

        Returns positions >$1M with symbol, direction, size, leverage.
        """
        if not self.coinglass_api_key:
            return []

        headers = {
            "coinglassSecret": self.coinglass_api_key,
            "Accept": "application/json",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{COINGLASS_BASE_URL}/hyperliquid/whale-position",
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        logger.warning("CoinGlass whale-position API returned %d", resp.status)
                        return []
                    data = await resp.json()
        except Exception:
            logger.exception("Failed to fetch CoinGlass whale positions")
            return []

        events = []
        for pos in data.get("data", []):
            size_usd = abs(float(pos.get("size", 0))) * float(pos.get("entryPrice", 0))
            if size_usd < 1_000_000:
                continue

            direction = "long" if float(pos.get("size", 0)) > 0 else "short"
            events.append({
                "symbol": pos.get("symbol", ""),
                "event_type": "whale_position",
                "direction": direction,
                "magnitude": round(size_usd, 2),
                "detail": f"${size_usd:,.0f} {direction} @ {pos.get('entryPrice')} ({pos.get('leverage', '?')}x)",
                "source": "coinglass",
            })

        return events

    async def fetch_coinglass_alerts(self) -> list[dict]:
        """Fetch recent whale alerts from CoinGlass."""
        if not self.coinglass_api_key:
            return []

        headers = {
            "coinglassSecret": self.coinglass_api_key,
            "Accept": "application/json",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{COINGLASS_BASE_URL}/hyperliquid/whale-alert",
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        logger.warning("CoinGlass whale-alert API returned %d", resp.status)
                        return []
                    data = await resp.json()
        except Exception:
            logger.exception("Failed to fetch CoinGlass whale alerts")
            return []

        events = []
        for alert in data.get("data", []):
            direction = alert.get("side", "unknown").lower()
            if direction not in ("long", "short"):
                direction = "unknown"

            events.append({
                "symbol": alert.get("symbol", ""),
                "event_type": "whale_alert",
                "direction": direction,
                "magnitude": float(alert.get("size", 0)),
                "detail": alert.get("description", f"Whale {direction} alert"),
                "source": "coinglass",
            })

        return events
