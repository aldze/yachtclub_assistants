"""Тесты каналов: API веб-виджета и помощники Telegram — без сети и LLM."""
import asyncio

import pytest
from aiohttp.test_utils import TestClient, TestServer

from app.channels.telegram import render_answer, split_message, to_html
from app.channels.web import create_app
from app.sessions import Answer, AssistantInfo, Sessions
from app.store import Store
from tests.test_window import EchoLLM


def run_web(scenario, monkeypatch, origins=""):
    monkeypatch.setenv("WEB_ALLOWED_ORIGINS", origins)

    async def main():
        store = Store(":memory:")
        sessions = Sessions("web", store=store, assistant="sales", client_factory=EchoLLM)
        async with TestClient(TestServer(create_app(sessions))) as client:
            return await scenario(client)

    return asyncio.run(main())


def test_web_start_and_message(monkeypatch):
    async def scenario(client):
        start = await (await client.post("/api/start", json={"session": "abcdefgh12"})).json()
        reply = await (await client.post("/api/message", json={"session": "abcdefgh12", "text": "Привет"})).json()
        return start, reply

    start, reply = run_web(scenario, monkeypatch)
    assert start["assistant"]["name"] == "Лоцман" and start["starters"]
    assert reply["text"] == "Лоцман: ответ" and reply["handoff"] is False and reply["error"] is False


@pytest.mark.parametrize("body,status", [
    ({"session": "../etc", "text": "x"}, 400),
    ({"session": "abcdefgh12", "text": "   "}, 400),
    ({"session": "abcdefgh12", "text": "x" * 2001}, 413),
])
def test_web_rejects_bad_input(monkeypatch, body, status):
    async def scenario(client):
        return (await client.post("/api/message", json=body)).status

    assert run_web(scenario, monkeypatch) == status


def test_web_demo_page_and_cors(monkeypatch):
    async def scenario(client):
        page = await (await client.get("/")).text()
        allowed = await client.post("/api/start", json={"session": "abcdefgh12"},
                                    headers={"Origin": "https://site.example"})
        foreign = await client.post("/api/start", json={"session": "abcdefgh13"},
                                    headers={"Origin": "https://evil.example"})
        return page, allowed.headers.get("Access-Control-Allow-Origin"), foreign.headers.get("Access-Control-Allow-Origin")

    page, allowed, foreign = run_web(scenario, monkeypatch, origins="https://site.example")
    assert "Северный Бриз" in page and "{{" not in page and "widget.js" in page
    assert allowed == "https://site.example" and foreign is None


# ---------- Telegram ----------

def test_to_html_escapes_and_bolds():
    assert to_html("<script> **Sea Ray** & Co") == "&lt;script&gt; <b>Sea Ray</b> &amp; Co"


def test_split_message_respects_limit():
    text = "\n".join(["строка " * 20] * 50)
    parts = split_message(text, limit=500)
    assert all(len(p) <= 500 for p in parts)
    assert "\n".join(parts).replace("\n", "") == text.replace("\n", "")
    assert split_message("x" * 1200, limit=500) == ["x" * 500, "x" * 500, "x" * 200]


def test_render_answer_signs_assistant_in_window():
    boatswain = AssistantInfo("service", "Боцман", "Ассистент сервиса")
    pilot = AssistantInfo("sales", "Лоцман", "Консультант")
    text = render_answer(Answer("Опишите проблему", boatswain, switched_from=pilot), window=True)
    assert text.startswith("<i>Разговор продолжит Боцман") and "<b>Боцман</b>\nОпишите проблему" in text
    assert render_answer(Answer("Ответ", boatswain), window=False) == "Ответ"
