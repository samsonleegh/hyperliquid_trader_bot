"""Bot configuration loaded from environment variables."""

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    # Telegram
    telegram_bot_token: str = ""
    telegram_allowed_user_ids: list[int] = field(default_factory=list)

    # Hyperliquid
    hl_private_key: str = ""
    hl_wallet_address: str = ""
    hl_testnet: bool = True

    # Bot
    default_execution_mode: str = "manual"
    scan_interval_minutes: int = 15
    log_level: str = "INFO"
    db_path: str = "data/bot.db"

    # Risk defaults
    max_position_size: float = 500.0
    max_total_exposure: float = 2000.0
    max_leverage: int = 5
    default_sl_pct: float = 3.0
    default_tp_pct: float = 6.0
    daily_loss_limit: float = 200.0
    max_open_positions: int = 5


def _parse_user_ids(raw: str) -> list[int]:
    if not raw:
        return []
    return [int(uid.strip()) for uid in raw.split(",") if uid.strip()]


def load_settings() -> Settings:
    return Settings(
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_allowed_user_ids=_parse_user_ids(
            os.getenv("TELEGRAM_ALLOWED_USER_IDS", "")
        ),
        hl_private_key=os.getenv("HL_PRIVATE_KEY", ""),
        hl_wallet_address=os.getenv("HL_WALLET_ADDRESS", ""),
        hl_testnet=os.getenv("HL_TESTNET", "true").lower() == "true",
        default_execution_mode=os.getenv("DEFAULT_EXECUTION_MODE", "manual"),
        scan_interval_minutes=int(os.getenv("SCAN_INTERVAL_MINUTES", "15")),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        db_path=os.getenv("DB_PATH", "data/bot.db"),
        max_position_size=float(os.getenv("MAX_POSITION_SIZE", "500")),
        max_total_exposure=float(os.getenv("MAX_TOTAL_EXPOSURE", "2000")),
        max_leverage=int(os.getenv("MAX_LEVERAGE", "5")),
        default_sl_pct=float(os.getenv("DEFAULT_SL_PCT", "3")),
        default_tp_pct=float(os.getenv("DEFAULT_TP_PCT", "6")),
        daily_loss_limit=float(os.getenv("DAILY_LOSS_LIMIT", "200")),
        max_open_positions=int(os.getenv("MAX_OPEN_POSITIONS", "5")),
    )


settings = load_settings()
