"""Hyperliquid API wrapper for spot and perpetual futures."""

import asyncio
import logging
from functools import partial

from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants

logger = logging.getLogger(__name__)


class HyperliquidClient:
    def __init__(self, private_key: str, wallet_address: str, testnet: bool = True) -> None:
        self.wallet_address = wallet_address
        self.testnet = testnet
        base_url = constants.TESTNET_API_URL if testnet else constants.MAINNET_API_URL

        wallet = Account.from_key(private_key)
        self._exchange = Exchange(
            wallet=wallet,
            base_url=base_url,
            account_address=wallet_address,
        )
        self._info = Info(base_url=base_url, skip_ws=True)
        logger.info("Hyperliquid client initialized (testnet=%s)", testnet)

    @property
    def exchange(self) -> Exchange:
        return self._exchange

    @property
    def info(self) -> Info:
        return self._info

    # Methods that modify state — never auto-retry these
    _WRITE_METHODS = {"market_open", "market_close", "order", "cancel", "update_leverage", "bulk_orders"}

    async def _run_sync(self, func, *args, retries: int = 3, **kwargs):
        """Run a synchronous SDK call in a thread executor with retry on connection errors.

        Write operations (orders, cancels) are NOT retried to prevent duplicate execution.
        """
        func_name = getattr(func, "__name__", str(func))
        is_write = func_name in self._WRITE_METHODS
        max_attempts = 1 if is_write else retries

        for attempt in range(1, max_attempts + 1):
            try:
                return await asyncio.to_thread(partial(func, *args, **kwargs))
            except (ConnectionError, OSError) as e:
                if attempt == max_attempts:
                    logger.error("API call %s failed after %d attempt(s): %s", func_name, attempt, e)
                    raise
                wait = attempt * 2
                logger.warning("API call %s failed (attempt %d/%d): %s — retrying in %ds", func_name, attempt, retries, e, wait)
                await asyncio.sleep(wait)

    async def get_balances(self) -> dict:
        """Get spot and perpetual account balances."""
        try:
            user_state = await self._run_sync(
                self._info.user_state, self.wallet_address
            )
            spot_state = await self._run_sync(
                self._info.spot_user_state, self.wallet_address
            )
            margin_summary = user_state.get("marginSummary", {})
            return {
                "perp": {
                    "account_value": float(margin_summary.get("accountValue", 0)),
                    "total_margin_used": float(margin_summary.get("totalMarginUsed", 0)),
                    "total_ntl_pos": float(margin_summary.get("totalNtlPos", 0)),
                    "total_raw_usd": float(margin_summary.get("totalRawUsd", 0)),
                },
                "spot": {
                    "balances": spot_state.get("balances", []),
                },
            }
        except Exception:
            logger.exception("Failed to fetch balances")
            raise

    async def get_open_positions(self) -> list[dict]:
        """Get all open perpetual positions."""
        try:
            user_state = await self._run_sync(
                self._info.user_state, self.wallet_address
            )
            positions = []
            for pos in user_state.get("assetPositions", []):
                p = pos.get("position", {})
                if float(p.get("szi", 0)) == 0:
                    continue
                size = float(p["szi"])
                positions.append({
                    "symbol": p.get("coin", ""),
                    "side": "long" if size > 0 else "short",
                    "size": abs(size),
                    "entry_price": float(p.get("entryPx", 0)),
                    "mark_price": float(p.get("positionValue", 0)) / abs(size) if size else 0,
                    "unrealized_pnl": float(p.get("unrealizedPnl", 0)),
                    "leverage": float(p.get("leverage", {}).get("value", 1)),
                    "margin_used": float(p.get("marginUsed", 0)),
                    "liquidation_price": float(p.get("liquidationPx", 0)) if p.get("liquidationPx") else None,
                })
            return positions
        except Exception:
            logger.exception("Failed to fetch positions")
            raise

    async def get_account_summary(self) -> dict:
        """Get account summary with equity and margin info."""
        user_state = await self._run_sync(
            self._info.user_state, self.wallet_address
        )
        margin = user_state.get("marginSummary", {})
        positions = user_state.get("assetPositions", [])
        total_unrealized = sum(
            float(p.get("position", {}).get("unrealizedPnl", 0))
            for p in positions
        )
        return {
            "account_value": float(margin.get("accountValue", 0)),
            "total_margin_used": float(margin.get("totalMarginUsed", 0)),
            "available_margin": float(margin.get("accountValue", 0)) - float(margin.get("totalMarginUsed", 0)),
            "total_unrealized_pnl": total_unrealized,
            "open_positions": len([p for p in positions if float(p.get("position", {}).get("szi", 0)) != 0]),
        }

    async def get_fills(self, start_time: int | None = None, end_time: int | None = None) -> list[dict]:
        """Get trade fills from Hyperliquid."""
        try:
            if start_time:
                fills = await self._run_sync(
                    self._info.user_fills_by_time,
                    self.wallet_address,
                    start_time,
                    end_time,
                )
            else:
                fills = await self._run_sync(
                    self._info.user_fills, self.wallet_address
                )
            return fills
        except Exception:
            logger.exception("Failed to fetch fills")
            raise

    async def get_historical_orders(self) -> list[dict]:
        """Get historical orders from Hyperliquid."""
        try:
            return await self._run_sync(
                self._info.historical_orders, self.wallet_address
            )
        except Exception:
            logger.exception("Failed to fetch historical orders")
            raise

    def is_connected(self) -> bool:
        """Check API connectivity."""
        try:
            self._info.all_mids()
            return True
        except Exception:
            return False
