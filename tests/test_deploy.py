"""Тесты публичного запуска (этап 6): лимиты, /health, IP за прокси, оповещения об ошибках."""
import asyncio
import logging
from datetime import date
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

import app.logs
from app.channels.web import create_app
from app.limits import RateLimiter, parse_rate
from app.logs import MaskingFilter, TelegramAlertHandler
from app.sessions import Sessions
from app.store import Store
from tests.test_window import EchoLLM

ROOT = Path(__file__).resolve().parents[1]


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_parse_rate():
    assert parse_rate("30/600") == (30, 600)
    assert parse_rate("5") == (5, 60)


def test_limit_per_ip_with_window():
    clock = Clock()
    limiter = RateLimiter(2, 60, clock=clock)
    assert limiter.hit("1.1.1.1") is None
    assert limiter.hit("1.1.1.1") is None
    assert limiter.hit("1.1.1.1") == "ip"
    assert limiter.hit("2.2.2.2") is None  # другой посетитель — свой счетчик
    clock.now = 61
    assert limiter.hit("1.1.1.1") is None  # окно прошло


def test_daily_limit_resets_next_day():
    day = [date(2026, 10, 27)]
    limiter = RateLimiter(100, 60, daily=2, clock=Clock(), today=lambda: day[0])
    assert limiter.hit("a") is None and limiter.hit("b") is None
    assert limiter.hit("c") == "daily"
    assert limiter.hit("c", counts_daily=False) is None  # приветствие модель не вызывает
    day[0] = date(2026, 10, 28)
    assert limiter.hit("c") is None


def test_idle_visitors_forgotten():
    clock = Clock()
    limiter = RateLimiter(5, 60, clock=clock)
    limiter.hit("a")
    clock.now = 200
    limiter.hit("b")
    assert set(limiter._hits) == {"b"}


def run_web(scenario, monkeypatch, limiter=None, trust_proxy=""):
    monkeypatch.setenv("WEB_TRUST_PROXY", trust_proxy)

    async def main():
        sessions = Sessions("web", store=Store(":memory:"), assistant="sales", client_factory=EchoLLM)
        async with TestClient(TestServer(create_app(sessions, limiter))) as client:
            return await scenario(client)

    return asyncio.run(main())


def test_health(monkeypatch):
    async def scenario(client):
        r = await client.get("/health")
        return r.status, await r.json()

    assert run_web(scenario, monkeypatch) == (200, {"ok": True})


def test_web_limit_returns_429_with_text(monkeypatch):
    async def scenario(client):
        body = {"session": "abcdefgh12", "text": "Привет"}
        first = await client.post("/api/message", json=body)
        second = await client.post("/api/message", json=body)
        return first.status, second.status, await second.json()

    first, second, data = run_web(scenario, monkeypatch, RateLimiter(1, 600))
    assert (first, second) == (200, 429)
    assert data["error"] == "ip" and "Подождите" in data["text"] and "{{" not in data["text"]


def test_ip_from_proxy_header_only_when_trusted(monkeypatch):
    async def scenario(client):
        statuses = []
        for ip in ("10.0.0.1", "10.0.0.2"):  # подделанный первый адрес не помогает обойти лимит
            r = await client.post("/api/start", json={"session": "abcdefgh12"},
                                  headers={"X-Forwarded-For": f"{ip}, 203.0.113.7"})
            statuses.append(r.status)
        r = await client.post("/api/start", json={"session": "abcdefgh12"},
                              headers={"X-Forwarded-For": "198.51.100.1"})
        return statuses + [r.status]

    assert run_web(scenario, monkeypatch, RateLimiter(1, 600), trust_proxy="1") == [200, 429, 200]
    # без доверия к прокси заголовок игнорируется: все запросы — с одного адреса
    assert run_web(scenario, monkeypatch, RateLimiter(1, 600)) == [200, 429, 429]


def test_alert_handler_masks_and_throttles(monkeypatch):
    sent = []
    monkeypatch.setattr(app.logs, "send_telegram", lambda token, chat, text, timeout: sent.append(text))
    monkeypatch.setattr(app.logs.threading, "Thread",
                        lambda target, args, daemon: type("T", (), {"start": lambda self: target(*args)})())
    clock = Clock()
    handler = TelegramAlertHandler("tok", "chat", "web@test", clock=clock)
    handler.addFilter(MaskingFilter())
    logger = logging.getLogger("test.alerts")
    logger.addHandler(handler)
    try:
        logger.error("Сбой для %s", "+7 916 123-45-02")
        logger.error("Сбой для %s", "+7 916 123-45-02")  # повтор в пределах ALERT_REPEAT — молчим
        logger.warning("не ошибка")
        clock.now = app.logs.ALERT_REPEAT + 1
        logger.error("Сбой для %s", "+7 916 123-45-02")
    finally:
        logger.removeHandler(handler)
    assert len(sent) == 2
    assert "web@test" in sent[0] and "123-45" not in sent[0] and "-02" in sent[0]


def test_docker_image_excludes_secrets_and_real_data():
    ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8").split()
    for pattern in (".env", "config/*.local.*", "data/", "*.docx"):
        assert pattern in ignored
