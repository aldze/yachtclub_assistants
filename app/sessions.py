"""Сессии каналов: один слой для сайта, Telegram и CLI.

Посетитель определяется ключом <канал>:<id>. Диалог хранится в SQLite и переживает перезапуск.
Режимы: единое окно (ассистента выбирает роутер) или один фиксированный ассистент.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Callable

from app.chat import ClientFactory, Conversation
from app.config import load_company, load_passports, render
from app.llm import LLMError, get_client
from app.router import Router, window_config
from app.store import Store

log = logging.getLogger(__name__)


@dataclass
class AssistantInfo:
    id: str
    name: str
    title: str


@dataclass
class Answer:
    text: str
    assistant: AssistantInfo
    switched_from: AssistantInfo | None = None   # если разговор только что передан
    starters: list[str] = field(default_factory=list)
    cards: list[dict] = field(default_factory=list)  # карточки, созданные этой репликой
    error: bool = False


class Sessions:
    def __init__(self, channel: str, store: Store | None = None, assistant: str | None = None,
                 router: Router | None = None, client_factory: ClientFactory = get_client,
                 notifier: Callable[[dict], str] | None = None):
        self.channel = channel
        self.passports = load_passports()
        self.window = window_config()
        self.fixed = assistant
        if assistant and assistant not in self.passports:
            raise KeyError(f"Неизвестный ассистент «{assistant}»")
        self.default = assistant or self.window.get("default_assistant") or next(iter(self.passports))
        self.router = None if assistant else (router or Router(self.passports))
        self.store = store or Store()
        self._client_factory = client_factory
        self._notifier = notifier
        self._cache: dict[str, Conversation] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()
        company, _ = load_company()
        self.error_text = render(self.window.get("error_text", ""), company, "config/assistants.yaml → window")

    # ---------- служебное ----------

    def _key(self, user_id: str) -> str:
        return f"{self.channel}:{user_id}"

    def _lock(self, key: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(key, threading.Lock())

    def _info(self, assistant_id: str) -> AssistantInfo:
        p = self.passports[assistant_id]
        return AssistantInfo(p.id, p.name, p.title)

    def _new(self) -> Conversation:
        return Conversation(self.default, self.passports, client_factory=self._client_factory,
                            channel=self.channel, store=self.store, notifier=self._notifier)

    def _get(self, key: str) -> Conversation:
        if key in self._cache:
            return self._cache[key]
        conv = self._new()
        state = self.store.load_session(key)
        if state and state["active"] in self.passports:
            conv.restore(state)
        self._cache[key] = conv
        return conv

    def _save(self, key: str, conv: Conversation) -> None:
        self.store.save_session(key, conv.conversation_id, conv.active, conv.history)

    def starters(self) -> list[str]:
        if self.fixed:
            return list(self.passports[self.fixed].conversation_starters)
        return list(self.window.get("starters") or [])

    # ---------- API каналов ----------

    def start(self, user_id: str) -> Answer:
        """Новый диалог: приветствие ассистента по умолчанию и кнопки-подсказки."""
        key = self._key(user_id)
        with self._lock(key):
            conv = self._new()
            self._cache[key] = conv
            greeting = conv.greet() or ""
            self._save(key, conv)
            log.info("Сессия %s: новый диалог %s", key, conv.conversation_id)
            return Answer(greeting, self._info(conv.active), starters=self.starters())

    reset = start

    def current(self, user_id: str) -> AssistantInfo:
        key = self._key(user_id)
        with self._lock(key):
            return self._info(self._get(key).active)

    def message(self, user_id: str, text: str) -> Answer:
        key = self._key(user_id)
        with self._lock(key):
            conv = self._get(key)
            before = self._info(conv.active)
            switched = None
            if self.router:
                route = self.router.route(conv.active, conv.history, text)
                if route.switched:
                    conv.handover(route.assistant, route.reason)
                    switched = before
                    log.info("Сессия %s: %s → %s (%s)", key, before.id, route.assistant, route.reason)
            cards_before = len(conv.cards)
            try:
                reply = conv.send(text)
            except LLMError as e:
                log.error("Сессия %s: ошибка LLM: %s", key, e)
                return Answer(self.error_text, self._info(conv.active), error=True)
            self._save(key, conv)
            # текст реплик в лог не пишем: имена регуляркой не замаскировать
            log.info("Сессия %s: %s ответил (%s → %s знаков, карточек: %s)", key, conv.active,
                     len(text), len(reply.text), len(conv.cards) - cards_before)
            return Answer(reply.text, self._info(conv.active), switched_from=switched,
                          cards=conv.cards[cards_before:])
