"""Inline keyboard builders for Telegram bot."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def signal_keyboard(signal_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Execute", callback_data=f"execute_signal:{signal_id}"),
            InlineKeyboardButton("Modify", callback_data=f"modify_signal:{signal_id}"),
            InlineKeyboardButton("Dismiss", callback_data=f"dismiss_signal:{signal_id}"),
        ]
    ])


def trade_confirm_keyboard(symbol: str, side: str, size: float) -> InlineKeyboardMarkup:
    data = f"{symbol}:{side}:{size}"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Confirm", callback_data=f"confirm_trade:{data}"),
            InlineKeyboardButton("Cancel", callback_data="cancel_trade"),
        ]
    ])


def close_confirm_keyboard(symbol: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Confirm Close", callback_data=f"confirm_close:{symbol}"),
            InlineKeyboardButton("Cancel", callback_data="cancel_close"),
        ]
    ])


def mode_keyboard(current_mode: str) -> InlineKeyboardMarkup:
    other = "auto" if current_mode == "manual" else "manual"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"Switch to {other.upper()}", callback_data=f"set_mode:{other}")]
    ])


def positions_keyboard(positions: list[dict]) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(f"Close {p['symbol']}", callback_data=f"close_pos:{p['symbol']}")]
        for p in positions
    ]
    return InlineKeyboardMarkup(buttons)
