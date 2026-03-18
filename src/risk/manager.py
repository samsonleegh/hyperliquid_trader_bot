"""Risk management: position limits, SL/TP, daily loss tracking."""

import logging
from datetime import date

from src.config import settings

logger = logging.getLogger(__name__)


class RiskManager:
    MAX_PORTFOLIO_PCT = 0.20  # 20% of portfolio per trade

    def __init__(self, repository, client=None) -> None:
        self.repo = repository
        self.client = client

    async def get_settings(self) -> dict[str, float]:
        return await self.repo.get_all_risk_settings()

    async def check_position_size(self, size_usd: float) -> tuple[bool, str]:
        max_size = await self.repo.get_risk_setting("max_position_size") or settings.max_position_size
        if size_usd > max_size:
            return False, f"Position size ${size_usd:,.0f} exceeds max ${max_size:,.0f}"
        return True, "OK"

    async def check_total_exposure(self, additional_usd: float) -> tuple[bool, str]:
        max_exposure = await self.repo.get_risk_setting("max_total_exposure") or settings.max_total_exposure
        open_trades = await self.repo.get_open_trades()
        current_exposure = sum(t["size"] * t["entry_price"] for t in open_trades)
        total = current_exposure + additional_usd
        if total > max_exposure:
            return False, f"Total exposure ${total:,.0f} would exceed max ${max_exposure:,.0f} (current: ${current_exposure:,.0f})"
        return True, "OK"

    async def check_max_positions(self) -> tuple[bool, str]:
        max_pos = await self.repo.get_risk_setting("max_open_positions") or settings.max_open_positions
        open_trades = await self.repo.get_open_trades()
        if len(open_trades) >= int(max_pos):
            return False, f"Already at max open positions ({int(max_pos)})"
        return True, "OK"

    async def check_daily_loss_limit(self) -> tuple[bool, str]:
        limit = await self.repo.get_risk_setting("daily_loss_limit") or settings.daily_loss_limit
        today_pnl = await self.repo.get_daily_pnl(date.today())
        realized = today_pnl["realized"] if today_pnl else 0
        if realized < -limit:
            return False, f"Daily loss limit hit: ${realized:,.2f} (limit: -${limit:,.0f})"
        return True, "OK"

    async def check_leverage(self, leverage: float) -> tuple[bool, str]:
        max_lev = await self.repo.get_risk_setting("max_leverage") or settings.max_leverage
        if leverage > max_lev:
            return False, f"Leverage {leverage}x exceeds max {max_lev}x"
        return True, "OK"

    async def check_portfolio_pct(self, size_usd: float, leverage: float = 1.0) -> tuple[bool, str]:
        if not self.client:
            return True, "OK"
        try:
            summary = await self.client.get_account_summary()
            portfolio_value = summary.get("account_value", 0)
            if portfolio_value <= 0:
                return True, "OK"
            # Compare margin used (notional / leverage) against portfolio percentage
            margin_used = size_usd / leverage
            max_margin = portfolio_value * self.MAX_PORTFOLIO_PCT
            if margin_used > max_margin:
                return False, f"Trade margin ${margin_used:,.0f} exceeds 20% of portfolio (${max_margin:,.0f} / ${portfolio_value:,.0f})"
        except Exception:
            logger.warning("Could not check portfolio percentage")
        return True, "OK"

    async def validate_trade(
        self,
        symbol: str,
        side: str,
        size: float,
        price: float,
        leverage: float = 1.0,
    ) -> tuple[bool, list[str]]:
        """Run all risk checks. Returns (all_passed, list_of_failure_reasons)."""
        size_usd = size * price
        failures: list[str] = []

        checks = [
            await self.check_position_size(size_usd),
            await self.check_portfolio_pct(size_usd, leverage),
            await self.check_total_exposure(size_usd),
            await self.check_max_positions(),
            await self.check_daily_loss_limit(),
            await self.check_leverage(leverage),
        ]
        for passed, reason in checks:
            if not passed:
                failures.append(reason)

        return len(failures) == 0, failures

    async def calculate_position_size(self, price: float, max_position_size: float | None = None, sz_decimals: int = 2, confidence: float = 1.0, leverage: float = 1.0) -> float:
        import math
        max_pos = max_position_size or settings.max_position_size

        # Cap at portfolio percentage based on confidence tier
        # 50-66% confidence: 10% of portfolio margin (half size)
        # 67%+ confidence: 20% of portfolio margin (full size)
        # Margin × leverage = notional exposure
        if self.client:
            try:
                summary = await self.client.get_account_summary()
                portfolio_value = summary.get("account_value", 0)
                if confidence >= 0.67:
                    pct = self.MAX_PORTFOLIO_PCT  # 20%
                else:
                    pct = self.MAX_PORTFOLIO_PCT / 2  # 10%
                margin_cap = portfolio_value * pct
                portfolio_cap = margin_cap * leverage  # notional = margin × leverage
                if portfolio_cap > 0:
                    max_pos = min(max_pos, portfolio_cap)
                    logger.info("Position sized: %.0f%% margin ($%.2f) × %.0fx leverage = $%.2f notional (confidence: %.0f%%, portfolio: $%.2f)",
                                pct * 100, margin_cap, leverage, max_pos, confidence * 100, portfolio_value)
            except Exception:
                logger.warning("Could not fetch portfolio value, using max_position_size setting")

        raw = max_pos / price
        factor = 10 ** sz_decimals
        size = math.floor(raw * factor) / factor

        # Ensure minimum $10 order value (Hyperliquid minimum)
        min_size = math.ceil(10.0 / price * factor) / factor
        if size < min_size:
            size = min_size

        return size

    def calculate_sl_tp(
        self,
        entry_price: float,
        side: str,
        sl_pct: float | None = None,
        tp_pct: float | None = None,
        sz_decimals: int = 0,
        leverage: float = 1.0,
    ) -> tuple[float, float]:
        """Calculate SL/TP prices, capping SL at max margin loss.

        SL price distance = sl_margin_pct / leverage.
        E.g., 15% margin risk at 5x leverage = 3% price move SL.
        TP defaults to 2× the SL distance (2:1 reward/risk).
        """
        # Margin-based SL: max price move = margin_risk% / leverage
        max_sl_price_pct = settings.sl_margin_pct / leverage

        # Use the tighter of: configured SL% or margin-based SL%
        configured_sl_pct = sl_pct or settings.default_sl_pct
        effective_sl_pct = min(configured_sl_pct, max_sl_price_pct)

        # TP: use configured, but at minimum 2:1 reward/risk
        configured_tp_pct = tp_pct or settings.default_tp_pct
        effective_tp_pct = max(configured_tp_pct, effective_sl_pct * 2)

        if side.lower() == "long":
            sl = entry_price * (1 - effective_sl_pct / 100)
            tp = entry_price * (1 + effective_tp_pct / 100)
        else:
            sl = entry_price * (1 + effective_sl_pct / 100)
            tp = entry_price * (1 - effective_tp_pct / 100)

        logger.info(
            "SL/TP calculated: leverage=%.0fx, sl=%.2f%% (margin risk=%.1f%%), tp=%.2f%% (R:R=1:%.1f)",
            leverage, effective_sl_pct, effective_sl_pct * leverage, effective_tp_pct, effective_tp_pct / effective_sl_pct,
        )

        from src.exchange.orders import _round_price
        return _round_price(sl, sz_decimals), _round_price(tp, sz_decimals)

    async def update_setting(self, key: str, value: float) -> None:
        await self.repo.update_risk_setting(key, value)
        logger.info("Risk setting updated: %s = %s", key, value)
