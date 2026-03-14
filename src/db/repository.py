"""Async CRUD operations for the trading bot database."""

import json
import logging
from datetime import date, datetime, timedelta

import aiosqlite

logger = logging.getLogger(__name__)


class Repository:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._db

    # ── Trades ──────────────────────────────────────────────

    async def create_trade(
        self,
        symbol: str,
        side: str,
        size: float,
        entry_price: float,
        order_type: str,
        sl_price: float | None = None,
        tp_price: float | None = None,
        signal_source: str | None = None,
    ) -> int:
        cursor = await self.db.execute(
            """INSERT INTO trades
               (symbol, side, size, entry_price, order_type, sl_price, tp_price, signal_source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (symbol, side, size, entry_price, order_type, sl_price, tp_price, signal_source),
        )
        await self.db.commit()
        logger.info("Created trade %d: %s %s %s @ %s", cursor.lastrowid, side, size, symbol, entry_price)
        return cursor.lastrowid  # type: ignore[return-value]

    async def update_trade(self, trade_id: int, **kwargs: object) -> None:
        if not kwargs:
            return
        columns = ", ".join(f"{k} = ?" for k in kwargs)
        values = list(kwargs.values()) + [trade_id]
        await self.db.execute(f"UPDATE trades SET {columns} WHERE id = ?", values)
        await self.db.commit()

    async def close_trade(self, trade_id: int, exit_price: float, pnl: float) -> None:
        await self.db.execute(
            "UPDATE trades SET exit_price = ?, pnl = ?, status = 'closed', closed_at = CURRENT_TIMESTAMP WHERE id = ?",
            (exit_price, pnl, trade_id),
        )
        await self.db.commit()
        logger.info("Closed trade %d: exit=%.4f pnl=%.2f", trade_id, exit_price, pnl)

    async def get_open_trades(self) -> list[dict]:
        cursor = await self.db.execute("SELECT * FROM trades WHERE status = 'open' ORDER BY created_at DESC")
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_trade(self, trade_id: int) -> dict | None:
        cursor = await self.db.execute("SELECT * FROM trades WHERE id = ?", (trade_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_trade_history(self, limit: int = 10) -> list[dict]:
        cursor = await self.db.execute(
            "SELECT * FROM trades ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    # ── Signals ─────────────────────────────────────────────

    async def create_signal(
        self,
        symbol: str,
        direction: str,
        indicators: dict,
        confidence: float,
        sl_price: float | None = None,
        tp_price: float | None = None,
    ) -> int:
        cursor = await self.db.execute(
            "INSERT INTO signals (symbol, direction, indicators, confidence, sl_price, tp_price) VALUES (?, ?, ?, ?, ?, ?)",
            (symbol, direction, json.dumps(indicators), confidence, sl_price, tp_price),
        )
        await self.db.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    async def mark_signal_acted(self, signal_id: int) -> None:
        await self.db.execute("UPDATE signals SET acted_on = 1 WHERE id = ?", (signal_id,))
        await self.db.commit()

    async def get_recent_signals(self, limit: int = 10) -> list[dict]:
        cursor = await self.db.execute(
            "SELECT * FROM signals ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    # ── Risk Settings ───────────────────────────────────────

    async def get_risk_setting(self, key: str) -> float | None:
        cursor = await self.db.execute("SELECT value FROM risk_settings WHERE key = ?", (key,))
        row = await cursor.fetchone()
        return row["value"] if row else None

    async def update_risk_setting(self, key: str, value: float) -> None:
        await self.db.execute(
            "INSERT OR REPLACE INTO risk_settings (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
            (key, value),
        )
        await self.db.commit()

    async def get_all_risk_settings(self) -> dict[str, float]:
        cursor = await self.db.execute("SELECT key, value FROM risk_settings")
        rows = await cursor.fetchall()
        return {row["key"]: row["value"] for row in rows}

    # ── Daily PnL ───────────────────────────────────────────

    async def update_daily_pnl(self, day: date, pnl_delta: float, trade_count_delta: int = 1) -> None:
        await self.db.execute(
            """INSERT INTO daily_pnl (date, realized, trade_count) VALUES (?, ?, ?)
               ON CONFLICT(date) DO UPDATE SET
               realized = realized + excluded.realized,
               trade_count = trade_count + excluded.trade_count""",
            (day.isoformat(), pnl_delta, trade_count_delta),
        )
        await self.db.commit()

    async def get_daily_pnl(self, day: date) -> dict | None:
        cursor = await self.db.execute("SELECT * FROM daily_pnl WHERE date = ?", (day.isoformat(),))
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_pnl_summary(self, period: str = "today") -> dict:
        today = date.today()
        if period == "today":
            start = today
        elif period == "week":
            start = today - timedelta(days=today.weekday())
        elif period == "month":
            start = today.replace(day=1)
        else:
            start = date(2000, 1, 1)

        cursor = await self.db.execute(
            "SELECT COALESCE(SUM(realized), 0) as total_pnl, COALESCE(SUM(trade_count), 0) as total_trades FROM daily_pnl WHERE date >= ?",
            (start.isoformat(),),
        )
        row = await cursor.fetchone()
        return {
            "period": period,
            "start_date": start.isoformat(),
            "total_pnl": row["total_pnl"],
            "total_trades": row["total_trades"],
        }

    # ── Watchlist ────────────────────────────────────────────

    async def add_to_watchlist(self, symbol: str) -> None:
        await self.db.execute("INSERT OR IGNORE INTO watchlist (symbol) VALUES (?)", (symbol,))
        await self.db.commit()

    async def remove_from_watchlist(self, symbol: str) -> None:
        await self.db.execute("DELETE FROM watchlist WHERE symbol = ?", (symbol,))
        await self.db.commit()

    async def get_watchlist(self) -> list[str]:
        cursor = await self.db.execute("SELECT symbol FROM watchlist ORDER BY added_at")
        rows = await cursor.fetchall()
        return [row["symbol"] for row in rows]

    # ── Strategies ────────────────────────────────────────────

    async def create_strategy(self, symbol: str, indicators: list, auto_execute: bool = False) -> int:
        cursor = await self.db.execute(
            """INSERT INTO strategies (symbol, indicators, auto_execute)
               VALUES (?, ?, ?)
               ON CONFLICT(symbol) DO UPDATE SET
               indicators = excluded.indicators,
               auto_execute = excluded.auto_execute,
               updated_at = CURRENT_TIMESTAMP""",
            (symbol, json.dumps(indicators), auto_execute),
        )
        await self.db.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    async def get_strategy(self, symbol: str) -> dict | None:
        cursor = await self.db.execute("SELECT * FROM strategies WHERE symbol = ?", (symbol,))
        row = await cursor.fetchone()
        if not row:
            return None
        result = dict(row)
        result["indicators"] = json.loads(result["indicators"])
        return result

    async def get_all_strategies(self) -> list[dict]:
        cursor = await self.db.execute("SELECT * FROM strategies ORDER BY symbol")
        rows = await cursor.fetchall()
        results = []
        for row in rows:
            r = dict(row)
            r["indicators"] = json.loads(r["indicators"])
            results.append(r)
        return results

    async def get_auto_strategies(self) -> list[dict]:
        cursor = await self.db.execute("SELECT * FROM strategies WHERE auto_execute = 1")
        rows = await cursor.fetchall()
        results = []
        for row in rows:
            r = dict(row)
            r["indicators"] = json.loads(r["indicators"])
            results.append(r)
        return results

    async def toggle_strategy_auto(self, symbol: str, auto_execute: bool) -> bool:
        cursor = await self.db.execute(
            "UPDATE strategies SET auto_execute = ?, updated_at = CURRENT_TIMESTAMP WHERE symbol = ?",
            (auto_execute, symbol),
        )
        await self.db.commit()
        return cursor.rowcount > 0

    async def delete_strategy(self, symbol: str) -> bool:
        cursor = await self.db.execute("DELETE FROM strategies WHERE symbol = ?", (symbol,))
        await self.db.commit()
        return cursor.rowcount > 0

    # ── Alerts ───────────────────────────────────────────────

    async def create_alert(self, symbol: str, alert_type: str, target_value: float) -> int:
        cursor = await self.db.execute(
            "INSERT INTO alerts (symbol, alert_type, target_value) VALUES (?, ?, ?)",
            (symbol, alert_type, target_value),
        )
        await self.db.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    async def get_active_alerts(self) -> list[dict]:
        cursor = await self.db.execute("SELECT * FROM alerts WHERE triggered = 0 ORDER BY created_at DESC")
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def trigger_alert(self, alert_id: int) -> None:
        await self.db.execute("UPDATE alerts SET triggered = 1 WHERE id = ?", (alert_id,))
        await self.db.commit()

    async def delete_alert(self, alert_id: int) -> None:
        await self.db.execute("DELETE FROM alerts WHERE id = ?", (alert_id,))
        await self.db.commit()

    # ── Funding / OI Snapshots ──────────────────────────────

    async def insert_funding_oi_batch(self, snapshots: list[dict]) -> int:
        """Insert a batch of funding/OI snapshots. Returns number inserted."""
        if not snapshots:
            return 0
        await self.db.executemany(
            """INSERT INTO funding_oi_snapshots
               (symbol, funding_rate, open_interest, mark_price, premium, day_volume)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [
                (s["symbol"], s["funding_rate"], s["open_interest"],
                 s["mark_price"], s.get("premium", 0), s.get("day_volume", 0))
                for s in snapshots
            ],
        )
        await self.db.commit()
        return len(snapshots)

    async def get_funding_oi_history(
        self, symbol: str, hours: int = 24
    ) -> list[dict]:
        """Get funding/OI snapshots for a symbol over the last N hours."""
        cursor = await self.db.execute(
            """SELECT * FROM funding_oi_snapshots
               WHERE symbol = ? AND timestamp >= datetime('now', ?)
               ORDER BY timestamp ASC""",
            (symbol, f"-{hours} hours"),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_funding_oi_all(self, hours: int = 24) -> list[dict]:
        """Get funding/OI snapshots for all symbols over the last N hours."""
        cursor = await self.db.execute(
            """SELECT * FROM funding_oi_snapshots
               WHERE timestamp >= datetime('now', ?)
               ORDER BY symbol, timestamp ASC""",
            (f"-{hours} hours",),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def cleanup_old_snapshots(self, days: int = 90) -> int:
        """Delete funding/OI snapshots older than N days."""
        cursor = await self.db.execute(
            "DELETE FROM funding_oi_snapshots WHERE timestamp < datetime('now', ?)",
            (f"-{days} days",),
        )
        await self.db.commit()
        return cursor.rowcount
