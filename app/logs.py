"""Настройка логов каналов: телефоны и e-mail маскируются в каждом сообщении.

Мониторинг ошибок: если в .env заданы TG_ALERT_CHAT и TG_NOTIFY_TOKEN, записи уровня ERROR
уходят в этот Telegram-чат (одинаковые — не чаще раза в ALERT_REPEAT секунд).
"""
from __future__ import annotations

import logging
import os
import socket
import threading
import time

from app.notify import NotifyError, send_telegram
from app.store import mask_text

ALERT_REPEAT = 600


class MaskingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = mask_text(record.getMessage())
        record.args = None
        return True


class TelegramAlertHandler(logging.Handler):
    def __init__(self, token: str, chat_id: str, service: str, clock=time.monotonic):
        super().__init__(logging.ERROR)
        self.token, self.chat_id, self.service = token, chat_id, service
        self._clock = clock
        self._sent: dict[str, float] = {}

    def emit(self, record: logging.LogRecord) -> None:
        if record.name == __name__:
            return  # ошибка самой отправки не должна порождать новое оповещение
        message = record.getMessage()
        now = self._clock()
        if now - self._sent.get(message, -ALERT_REPEAT) < ALERT_REPEAT:
            return
        self._sent[message] = now
        text = f"⚠️ {self.service}: {record.levelname} {record.name}\n{message}"
        if record.exc_info and record.exc_info[1] is not None:
            text += f"\n{type(record.exc_info[1]).__name__}: {mask_text(str(record.exc_info[1]))}"
        # в фоне: оповещение не должно задерживать ответ посетителю
        threading.Thread(target=self._send, args=(text[:4000],), daemon=True).start()

    def _send(self, text: str) -> None:
        try:
            send_telegram(self.token, self.chat_id, text, timeout=5)
        except NotifyError as e:
            logging.getLogger(__name__).warning("Оповещение об ошибке не отправлено: %s", e)


def setup_logging(level: str | None = None, service: str = "app") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(MaskingFilter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    token, chat = os.getenv("TG_NOTIFY_TOKEN"), os.getenv("TG_ALERT_CHAT")
    if token and chat:
        alert = TelegramAlertHandler(token, chat, f"{service}@{socket.gethostname()}")
        alert.addFilter(MaskingFilter())
        root.addHandler(alert)
    root.setLevel(level or os.getenv("LOG_LEVEL", "INFO"))
    # библиотеки пишут в лог адреса запросов: у Telegram в адресе токен бота
    for noisy in ("httpx", "httpx2", "httpcore", "openai", "aiogram.event"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
