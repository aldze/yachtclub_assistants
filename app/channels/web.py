"""Веб-канал единого окна: API для чат-виджета и демо-страница.

    python -m app.channels.web                    # http://127.0.0.1:8080 — демо-страница с виджетом
    python -m app.channels.web --port 8000 --host 0.0.0.0
    python -m app.channels.web --assistant service   # виджет одного ассистента, без роутера

Встраивание на сайт: <script src="https://<сервер>/static/widget.js" data-api="https://<сервер>" defer></script>
Сайт на другом домене надо разрешить в WEB_ALLOWED_ORIGINS (через запятую).

Публичный сервер (этап 6): лимиты запросов — app/limits.py (WEB_RATE_LIMIT, WEB_DAILY_LIMIT),
за обратным прокси (Caddy в docker-compose) — WEB_TRUST_PROXY=1, чтобы видеть настоящий IP посетителя.
GET /health — проверка живости для Docker и внешнего мониторинга.
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import logging
import os
import re
from pathlib import Path

from aiohttp import web

from app.config import ConfigError, load_company, load_env, load_passports, render
from app.limits import RateLimiter, parse_rate
from app.logs import setup_logging
from app.sessions import Answer, Sessions

log = logging.getLogger(__name__)

STATIC = Path(__file__).with_name("web_static")
SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
MAX_TEXT = 2000  # знаков в одной реплике посетителя

SESSIONS = web.AppKey("sessions", Sessions)
ORIGINS = web.AppKey("origins", set)
LIMITER = web.AppKey("limiter", RateLimiter)
LIMIT_TEXT = web.AppKey("limit_text", str)
TRUST_PROXY = web.AppKey("trust_proxy", bool)


def answer_json(answer: Answer) -> dict:
    """Ответ виджету. Содержимое карточек посетителю не показываем — только факт передачи."""
    return {
        "text": answer.text,
        "assistant": dataclasses.asdict(answer.assistant),
        "switched_from": dataclasses.asdict(answer.switched_from) if answer.switched_from else None,
        "starters": answer.starters,
        "handoff": bool(answer.cards),
        "error": answer.error,
    }


async def read_request(request: web.Request) -> tuple[str, str]:
    try:
        data = await request.json()
    except ValueError:
        raise web.HTTPBadRequest(text="ожидается JSON") from None
    session = str(data.get("session", ""))
    if not SESSION_ID.match(session):
        raise web.HTTPBadRequest(text="некорректный session")
    return session, str(data.get("text", ""))


def client_ip(request: web.Request) -> str:
    """IP посетителя. За своим прокси берем последний адрес X-Forwarded-For — его дописал прокси,
    а более ранние посетитель может подделать."""
    forwarded = request.headers.get("X-Forwarded-For", "")
    if request.app[TRUST_PROXY] and forwarded:
        return forwarded.split(",")[-1].strip()
    return request.remote or "?"


def check_limit(request: web.Request, counts_daily: bool) -> None:
    reason = request.app[LIMITER].hit(client_ip(request), counts_daily)
    if reason:
        log.warning("Лимит запросов (%s) для %s", reason, request.path)
        raise web.HTTPTooManyRequests(text=json.dumps({"error": reason, "text": request.app[LIMIT_TEXT]},
                                                      ensure_ascii=False), content_type="application/json")


async def api_start(request: web.Request) -> web.Response:
    session, _ = await read_request(request)
    check_limit(request, counts_daily=False)  # приветствие берется из промпта, модель не вызывается
    answer = await asyncio.to_thread(request.app[SESSIONS].start, session)
    return web.json_response(answer_json(answer))


async def api_message(request: web.Request) -> web.Response:
    session, text = await read_request(request)
    text = text.strip()
    if not text:
        raise web.HTTPBadRequest(text="пустое сообщение")
    if len(text) > MAX_TEXT:
        raise web.HTTPRequestEntityTooLarge(max_size=MAX_TEXT, actual_size=len(text))
    check_limit(request, counts_daily=True)
    answer = await asyncio.to_thread(request.app[SESSIONS].message, session, text)
    return web.json_response(answer_json(answer))


async def demo_page(request: web.Request) -> web.Response:
    company, _ = load_company()
    html = render((STATIC / "index.html").read_text(encoding="utf-8"), company, "web_static/index.html")
    return web.Response(text=html, content_type="text/html")


async def health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


@web.middleware
async def cors(request: web.Request, handler):
    """Разрешает виджет на сайтах из WEB_ALLOWED_ORIGINS; без настройки — только тот же домен."""
    origin = request.headers.get("Origin")
    allowed = origin and origin in request.app[ORIGINS]
    if request.method == "OPTIONS" and request.path.startswith("/api/"):
        response = web.Response()
    else:
        response = await handler(request)
    if allowed:
        response.headers.update({
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
            "Vary": "Origin",
        })
    return response


def create_app(sessions: Sessions, limiter: RateLimiter | None = None) -> web.Application:
    app = web.Application(middlewares=[cors], client_max_size=64 * 1024)
    app[SESSIONS] = sessions
    app[ORIGINS] = {o.strip() for o in os.getenv("WEB_ALLOWED_ORIGINS", "").split(",") if o.strip()}
    if limiter is None:
        per_ip, window = parse_rate(os.getenv("WEB_RATE_LIMIT") or "30/600")
        limiter = RateLimiter(per_ip, window, int(os.getenv("WEB_DAILY_LIMIT") or 1500))
    app[LIMITER] = limiter
    app[TRUST_PROXY] = os.getenv("WEB_TRUST_PROXY", "") in ("1", "true", "yes")
    company, _ = load_company()
    app[LIMIT_TEXT] = render(sessions.window.get("limit_text", ""), company, "config/assistants.yaml → window")
    app.router.add_get("/", demo_page)
    app.router.add_get("/health", health)
    app.router.add_post("/api/start", api_start)
    app.router.add_post("/api/message", api_message)
    app.router.add_static("/static/", STATIC)
    return app


def main() -> int:
    load_env()
    setup_logging(service="web")
    passports = load_passports()
    ap = argparse.ArgumentParser(description="Веб-виджет единого окна")
    ap.add_argument("--host", default=os.getenv("WEB_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.getenv("WEB_PORT", "8080")))
    ap.add_argument("--assistant", choices=list(passports), help="один ассистент без роутера")
    args = ap.parse_args()
    try:
        sessions = Sessions("web", assistant=args.assistant)
    except ConfigError as e:
        log.error("Ошибка конфигурации: %s", e)
        return 1
    mode = f"ассистент {args.assistant}" if args.assistant else "единое окно с роутером"
    log.info("Веб-виджет (%s): http://%s:%s", mode, args.host, args.port)
    web.run_app(create_app(sessions), host=args.host, port=args.port, print=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
