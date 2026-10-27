"""Лимиты публичного виджета: сайт открыт всем, а каждый ответ ассистента стоит денег.

- на посетителя (IP): не больше N запросов за окно в секундах — WEB_RATE_LIMIT=30/600;
- на весь сервис: не больше M реплик в сутки — WEB_DAILY_LIMIT=1500 (0 — без лимита).
Счетчики в памяти процесса: после перезапуска обнуляются, для одного сервера этого достаточно.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from datetime import date


def parse_rate(value: str) -> tuple[int, int]:
    """«30/600» → (30 запросов, 600 секунд)."""
    count, _, seconds = value.partition("/")
    return int(count), int(seconds or 60)


class RateLimiter:
    def __init__(self, per_ip: int, window: int, daily: int = 0,
                 clock=time.monotonic, today=date.today):
        self.per_ip, self.window, self.daily = per_ip, window, daily
        self._clock, self._today = clock, today
        self._hits: dict[str, deque[float]] = {}
        self._day, self._day_count = today(), 0
        self._lock = threading.Lock()
        self._last_prune = clock()

    def _prune(self, now: float) -> None:
        """Забывает посетителей, которые давно не писали, — словарь не растет бесконечно."""
        if now - self._last_prune < self.window:
            return
        self._last_prune = now
        for ip in [ip for ip, q in self._hits.items() if not q or now - q[-1] > self.window]:
            del self._hits[ip]

    def hit(self, ip: str, counts_daily: bool = True) -> str | None:
        """Учитывает запрос. Возвращает причину отказа («ip» или «daily») либо None."""
        with self._lock:
            now = self._clock()
            self._prune(now)
            q = self._hits.setdefault(ip, deque())
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.per_ip:
                return "ip"
            if counts_daily and self.daily:
                if self._today() != self._day:
                    self._day, self._day_count = self._today(), 0
                if self._day_count >= self.daily:
                    return "daily"
                self._day_count += 1
            q.append(now)
            return None
