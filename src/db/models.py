"""Database table definitions and initialization."""

import logging

import aiosqlite

from src.config import settings

logger = logging.getLogger(__name__)

TABLES_SQL = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK(side IN ('long', 'short')),
    size REAL NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL,
    pnl REAL,
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'closed', 'cancelled')),
    order_type TEXT NOT NULL CHECK(order_type IN ('market', 'limit', 'stop')),
    sl_price REAL,
    tp_price REAL,
    signal_source TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    closed_at DATETIME
);

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL CHECK(direction IN ('long', 'short')),
    indicators TEXT,
    confidence REAL,
    sl_price REAL,
    tp_price REAL,
    acted_on BOOLEAN DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS risk_settings (
    key TEXT PRIMARY KEY,
    value REAL NOT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS daily_pnl (
    date DATE PRIMARY KEY,
    realized REAL DEFAULT 0,
    trade_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS watchlist (
    symbol TEXT PRIMARY KEY,
    added_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS strategies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL UNIQUE,
    indicators TEXT NOT NULL,
    auto_execute BOOLEAN DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    alert_type TEXT NOT NULL CHECK(alert_type IN ('price_above', 'price_below', 'signal')),
    target_value REAL,
    triggered BOOLEAN DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""

DEFAULT_RISK_SETTINGS = {
    "max_position_size": settings.max_position_size,
    "max_total_exposure": settings.max_total_exposure,
    "max_leverage": settings.max_leverage,
    "default_sl_pct": settings.default_sl_pct,
    "default_tp_pct": settings.default_tp_pct,
    "daily_loss_limit": settings.daily_loss_limit,
    "max_open_positions": settings.max_open_positions,
}


async def init_db(db_path: str | None = None) -> None:
    """Create all tables and seed default risk settings."""
    path = db_path or settings.db_path
    async with aiosqlite.connect(path) as db:
        await db.executescript(TABLES_SQL)

        # Migrate existing signals table to add sl_price/tp_price columns
        try:
            await db.execute("ALTER TABLE signals ADD COLUMN sl_price REAL")
            await db.execute("ALTER TABLE signals ADD COLUMN tp_price REAL")
            await db.commit()
        except Exception:
            pass  # Columns already exist

        for key, value in DEFAULT_RISK_SETTINGS.items():
            await db.execute(
                "INSERT OR IGNORE INTO risk_settings (key, value) VALUES (?, ?)",
                (key, value),
            )
        await db.commit()
    logger.info("Database initialized at %s", path)
