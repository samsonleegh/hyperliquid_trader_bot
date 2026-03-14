"""Order execution: market, limit, stop-loss, take-profit."""

import logging

from src.exchange.client import HyperliquidClient

logger = logging.getLogger(__name__)


def _round_price(price: float, sz_decimals: int) -> float:
    """Round price to 5 significant figures + valid decimal places (Hyperliquid rule)."""
    rounded = float(f"{price:.5g}")
    decimals = max(6 - sz_decimals, 0)
    return round(rounded, decimals)


def _check_order_result(result: dict) -> tuple[bool, str]:
    """Check if an order result from the SDK indicates success."""
    if result.get("status") != "ok":
        return False, result.get("response", str(result))
    response = result.get("response", {})
    data = response.get("data", {})
    statuses = data.get("statuses", [])
    for status in statuses:
        if "error" in status:
            return False, status["error"]
    return True, ""


class OrderManager:
    def __init__(self, client: HyperliquidClient) -> None:
        self.client = client

    async def market_order(
        self,
        symbol: str,
        side: str,
        size: float,
        sl_price: float | None = None,
        tp_price: float | None = None,
        sz_decimals: int = 0,
    ) -> dict:
        """Place a market order with optional SL/TP."""
        is_buy = side.lower() == "long"
        try:
            result = await self.client._run_sync(
                self.client.exchange.market_open,
                symbol,
                is_buy,
                size,
                None,
                0.01,
            )
            logger.info("Market order: %s %s %s -> %s", side, size, symbol, result)

            success, error = _check_order_result(result)
            if not success:
                logger.error("Market order rejected: %s", error)
                return {"status": "error", "error": error, "symbol": symbol}

            # Use actual filled size for SL/TP, not requested size
            filled_size = size
            statuses = result.get("response", {}).get("data", {}).get("statuses", [])
            for s in statuses:
                if "filled" in s:
                    filled_size = float(s["filled"]["totalSz"])
                    break

            sl_tp_result = {}
            if (sl_price or tp_price) and filled_size > 0:
                sl_tp_result = await self.place_sl_tp(symbol, side, filled_size, sl_price, tp_price, sz_decimals=sz_decimals)
                logger.info("SL/TP result for %s: %s", symbol, sl_tp_result)

            return {
                "status": "filled",
                "symbol": symbol,
                "side": side,
                "size": filled_size,
                "result": result,
                "sl_tp": sl_tp_result,
            }
        except Exception as e:
            logger.exception("Market order failed: %s %s %s", side, size, symbol)
            return {"status": "error", "error": str(e)}

    async def limit_order(
        self,
        symbol: str,
        side: str,
        price: float,
        size: float,
        sl_price: float | None = None,
        tp_price: float | None = None,
        sz_decimals: int = 0,
    ) -> dict:
        """Place a limit order with optional SL/TP."""
        is_buy = side.lower() == "long"
        try:
            result = await self.client._run_sync(
                self.client.exchange.order,
                symbol,
                is_buy,
                size,
                price,
                {"limit": {"tif": "Gtc"}},
            )
            logger.info("Limit order: %s %s %s @ %s -> %s", side, size, symbol, price, result)

            success, error = _check_order_result(result)
            if not success:
                logger.error("Limit order rejected: %s", error)
                return {"status": "error", "error": error, "symbol": symbol}

            sl_tp_result = {}
            if sl_price or tp_price:
                sl_tp_result = await self.place_sl_tp(symbol, side, size, sl_price, tp_price, sz_decimals=sz_decimals)

            return {
                "status": "placed",
                "symbol": symbol,
                "side": side,
                "size": size,
                "price": price,
                "result": result,
                "sl_tp": sl_tp_result,
            }
        except Exception as e:
            logger.exception("Limit order failed: %s %s %s @ %s", side, size, symbol, price)
            return {"status": "error", "error": str(e)}

    async def close_position(self, symbol: str) -> dict:
        """Close an open position using the SDK's market_close."""
        try:
            positions = await self.client.get_open_positions()
            pos = next((p for p in positions if p["symbol"] == symbol), None)
            if not pos:
                return {"status": "error", "error": f"No open position for {symbol}"}

            result = await self.client._run_sync(
                self.client.exchange.market_close,
                symbol,
            )
            logger.info("Closed position: %s %s -> %s", symbol, pos["size"], result)

            success, error = _check_order_result(result)
            if not success:
                return {"status": "error", "error": error, "symbol": symbol}

            return {
                "status": "closed",
                "symbol": symbol,
                "size": pos["size"],
                "side": pos["side"],
                "result": result,
            }
        except Exception as e:
            logger.exception("Close position failed: %s", symbol)
            return {"status": "error", "error": str(e)}

    async def cancel_order(self, symbol: str, order_id: int) -> dict:
        """Cancel a specific order."""
        try:
            result = await self.client._run_sync(
                self.client.exchange.cancel, symbol, order_id
            )
            logger.info("Cancelled order %s on %s", order_id, symbol)
            return {"status": "cancelled", "order_id": order_id, "result": result}
        except Exception as e:
            logger.exception("Cancel order failed: %s", order_id)
            return {"status": "error", "error": str(e)}

    async def cancel_all_orders(self, symbol: str | None = None) -> list[dict]:
        """Cancel all open orders, optionally filtered by symbol."""
        try:
            open_orders = await self.get_open_orders(symbol)
            results = []
            for order in open_orders:
                res = await self.cancel_order(order["coin"], order["oid"])
                results.append(res)
            return results
        except Exception as e:
            logger.exception("Cancel all orders failed")
            return [{"status": "error", "error": str(e)}]

    async def get_open_orders(self, symbol: str | None = None) -> list[dict]:
        """Get all open orders, optionally filtered by symbol."""
        try:
            orders = await self.client._run_sync(
                self.client.info.open_orders, self.client.wallet_address
            )
            if symbol:
                orders = [o for o in orders if o.get("coin") == symbol]
            return orders
        except Exception:
            logger.exception("Failed to fetch open orders")
            raise

    async def place_sl_tp(
        self,
        symbol: str,
        side: str,
        size: float,
        sl_price: float | None = None,
        tp_price: float | None = None,
        sz_decimals: int = 0,
    ) -> dict:
        """Place stop-loss and take-profit orders for an existing position."""
        results = {}
        is_long = side.lower() == "long"
        close_side = not is_long

        # Round prices to 5 significant figures + valid decimal places (Hyperliquid requirement)
        if sl_price:
            sl_price = _round_price(sl_price, sz_decimals)
        if tp_price:
            tp_price = _round_price(tp_price, sz_decimals)

        logger.info("Placing SL/TP for %s: sl=%s, tp=%s, size=%s, side=%s", symbol, sl_price, tp_price, size, side)

        # Cancel existing SL/TP orders for this symbol to avoid conflicts
        try:
            open_orders = await self.client._run_sync(
                self.client.info.frontend_open_orders, self.client.wallet_address
            )
            for order in open_orders:
                if order.get("coin") == symbol and order.get("orderType") in ("Stop Market", "Take Profit Market"):
                    await self.client._run_sync(
                        self.client.exchange.cancel, symbol, order["oid"]
                    )
                    logger.info("Cancelled existing %s order for %s (oid=%s)", order["orderType"], symbol, order["oid"])
        except Exception:
            logger.warning("Could not cancel existing SL/TP orders for %s", symbol)

        if sl_price:
            try:
                sl_result = await self.client._run_sync(
                    self.client.exchange.order,
                    symbol,
                    close_side,
                    size,
                    sl_price,
                    {
                        "trigger": {
                            "triggerPx": sl_price,
                            "isMarket": True,
                            "tpsl": "sl",
                        }
                    },
                )
                success, error = _check_order_result(sl_result)
                if success:
                    results["sl"] = {"status": "placed", "price": sl_price}
                    logger.info("SL placed for %s at %s", symbol, sl_price)
                else:
                    results["sl"] = {"status": "error", "error": error}
                    logger.error("SL rejected for %s: %s", symbol, error)
            except Exception as e:
                results["sl"] = {"status": "error", "error": str(e)}
                logger.exception("SL placement failed for %s", symbol)

        if tp_price:
            try:
                tp_result = await self.client._run_sync(
                    self.client.exchange.order,
                    symbol,
                    close_side,
                    size,
                    tp_price,
                    {
                        "trigger": {
                            "triggerPx": tp_price,
                            "isMarket": True,
                            "tpsl": "tp",
                        }
                    },
                )
                success, error = _check_order_result(tp_result)
                if success:
                    results["tp"] = {"status": "placed", "price": tp_price}
                    logger.info("TP placed for %s at %s", symbol, tp_price)
                else:
                    results["tp"] = {"status": "error", "error": error}
                    logger.error("TP rejected for %s: %s", symbol, error)
            except Exception as e:
                results["tp"] = {"status": "error", "error": str(e)}
                logger.exception("TP placement failed for %s", symbol)

        return results
