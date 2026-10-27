"""Отправка сообщения в Telegram через Bot API (карточки отделам, оповещения об ошибках)."""
from __future__ import annotations

import json
import urllib.error
import urllib.request


class NotifyError(Exception):
    """Сообщение не отправлено; текст ошибки — без токена бота."""


def send_telegram(token: str, chat_id: str, text: str, timeout: float = 10) -> None:
    body = json.dumps({"chat_id": chat_id, "text": text}).encode()
    request = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body,
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read()
    except (urllib.error.URLError, TimeoutError) as e:
        # токен — часть адреса запроса, он не должен попасть в лог
        raise NotifyError(str(getattr(e, "reason", e)).replace(token, "***")) from None
