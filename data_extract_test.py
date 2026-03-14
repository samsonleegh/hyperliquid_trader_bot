"""Query funding/OI data from the bot database."""

import sqlite3
import pandas as pd

DB_PATH = "data/bot.db"


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def latest_snapshots():
    """Latest snapshot per coin (last 20 minutes)."""
    conn = connect()
    df = pd.read_sql_query(
        """SELECT symbol, funding_rate, open_interest, mark_price, premium, day_volume, timestamp
           FROM funding_oi_snapshots
           WHERE timestamp >= datetime('now', '-20 minutes')
           ORDER BY symbol""",
        conn, parse_dates=["timestamp"],
    )
    conn.close()
    print(f"\n=== Latest Snapshots ({len(df)} coins) ===")
    print(df.to_string(index=False))
    return df


def funding_history(symbol: str, hours: int = 24):
    """Funding rate + OI history for a specific coin."""
    conn = connect()
    df = pd.read_sql_query(
        """SELECT symbol, funding_rate, open_interest, mark_price, timestamp
           FROM funding_oi_snapshots
           WHERE symbol = ? AND timestamp >= datetime('now', ?)
           ORDER BY timestamp ASC""",
        conn, params=(symbol, f"-{hours} hours"), parse_dates=["timestamp"],
    )
    conn.close()
    print(f"\n=== {symbol} Funding/OI History (last {hours}h, {len(df)} rows) ===")
    print(df.to_string(index=False))
    return df


def extreme_funding(threshold: float = 0.00005):
    """Coins with extreme funding rates right now."""
    conn = connect()
    df = pd.read_sql_query(
        """SELECT symbol,
                  funding_rate,
                  funding_rate * 800 AS rate_8h_pct,
                  open_interest,
                  mark_price,
                  timestamp
           FROM funding_oi_snapshots
           WHERE timestamp >= datetime('now', '-20 minutes')
             AND ABS(funding_rate) > ?
           ORDER BY ABS(funding_rate) DESC""",
        conn, params=(threshold,), parse_dates=["timestamp"],
    )
    conn.close()
    print(f"\n=== Extreme Funding ({len(df)} coins above {threshold}) ===")
    print(df.to_string(index=False))
    return df


def export_csv(output_path: str = "funding_oi_export.csv"):
    """Export all funding/OI data to CSV."""
    conn = connect()
    df = pd.read_sql_query(
        "SELECT * FROM funding_oi_snapshots ORDER BY symbol, timestamp",
        conn, parse_dates=["timestamp"],
    )
    conn.close()
    df.to_csv(output_path, index=False)
    print(f"\nExported {len(df)} rows to {output_path}")
    return df


if __name__ == "__main__":
    # Uncomment the queries you want to run:

    latest_snapshots()
    # funding_history("BTC", hours=24)
    # extreme_funding(threshold=0.00005)
    # export_csv("funding_oi_export.csv")
