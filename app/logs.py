"""Настройка логов каналов: телефоны и e-mail маскируются в каждом сообщении."""
from __future__ import annotations

import logging
import os

from app.store import mask_text


class MaskingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = mask_text(record.getMessage())
        record.args = None
        return True


def setup_logging(level: str | None = None) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(MaskingFilter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level or os.getenv("LOG_LEVEL", "INFO"))
    # библиотеки пишут в лог адреса запросов: у Telegram в адресе токен бота
    for noisy in ("httpx", "httpx2", "httpcore", "openai", "aiogram.event"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
