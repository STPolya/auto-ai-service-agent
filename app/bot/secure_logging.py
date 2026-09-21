"""Keep aiogram's own exception logging from exposing provider payloads/URLs."""

import logging


class TelegramErrorFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.WARNING:
            # aiogram logs raw exception arguments and tracebacks for polling and
            # update errors; either may contain tokens, URLs or message content.
            record.msg = "Telegram operation failed: category=transport_or_update."
            record.args = ()
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return True


def configure_telegram_logging() -> None:
    for name in ("aiogram.dispatcher", "aiogram.event"):
        logger = logging.getLogger(name)
        if not any(isinstance(item, TelegramErrorFilter) for item in logger.filters):
            logger.addFilter(TelegramErrorFilter())
