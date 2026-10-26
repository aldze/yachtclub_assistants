"""Диалог с ассистентами: общая история и активный ассистент, которого можно сменить.

Под «единое окно» (CLAUDE.md, решение 5): посетитель ведет один разговор,
а отвечает тот ассистент, который сейчас активен. Роутер появится на этапе 4.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Callable

from app.config import Passport, load_company, load_passports
from app.llm import LLMClient, Reply, ToolCall, get_client
from app.prompts import build_system_prompt, extract_greeting
from app.store import Store
from app.tools import ToolContext, tools_for

ClientFactory = Callable[[Passport], LLMClient]
MAX_TOOL_ROUNDS = 4  # защита от зацикливания: модель вызывает инструменты не больше N раз подряд

log = logging.getLogger(__name__)


class Conversation:
    def __init__(self, assistant_id: str, passports: dict[str, Passport] | None = None,
                 client_factory: ClientFactory = get_client, channel: str = "cli",
                 store: Store | None = None, notifier: Callable[[dict], str] | None = None):
        self.passports = passports or load_passports()
        self.company, _ = load_company()
        self.channel = channel
        self._client_factory = client_factory
        self._store = store
        self._notifier = notifier
        self._clients: dict[str, LLMClient] = {}
        self._prompts: dict[str, str] = {}
        self.history: list[dict] = []
        self.cards: list[dict] = []  # результаты create_handoff в этом диалоге
        self.conversation_id = uuid.uuid4().hex
        self.active = ""
        self.switch(assistant_id)

    @property
    def passport(self) -> Passport:
        return self.passports[self.active]

    @property
    def store(self) -> Store:
        if self._store is None:
            self._store = Store()
        return self._store

    def switch(self, assistant_id: str) -> None:
        """Делает ассистента активным; история диалога сохраняется."""
        if assistant_id not in self.passports:
            raise KeyError(f"Неизвестный ассистент «{assistant_id}». Доступны: {', '.join(self.passports)}")
        if assistant_id not in self._prompts:
            self._prompts[assistant_id] = build_system_prompt(self.passports[assistant_id], self.company)
        self.active = assistant_id

    def reset(self) -> None:
        self.history.clear()
        self.cards.clear()
        self.conversation_id = uuid.uuid4().hex

    def greet(self) -> str | None:
        """Приветствие активного ассистента из его промпта.

        В начале диалога оно записывается в историю, чтобы модель не здоровалась второй раз.
        """
        greeting = extract_greeting(self.passport, self.company)
        if greeting and not self.history:
            self.history.append({"role": "assistant", "content": greeting})
        return greeting

    def system_prompt(self) -> str:
        return self._prompts[self.active]

    def _client(self) -> LLMClient:
        if self.active not in self._clients:
            self._clients[self.active] = self._client_factory(self.passport)
        return self._clients[self.active]

    def _tool_context(self) -> ToolContext:
        ctx = ToolContext(assistant_id=self.active, conversation_id=self.conversation_id,
                          channel=self.channel, store=self.store)
        if self._notifier:
            ctx.notifier = self._notifier
        return ctx

    def _run_tool(self, call: ToolCall) -> dict:
        tools = {t.name: t for t in tools_for(self.passport)}
        if call.name not in tools:
            return {"ok": False, "errors": [f"Инструмент {call.name} недоступен"]}
        if "_raw" in call.arguments:
            return {"ok": False, "errors": ["Аргументы не являются корректным JSON"]}
        try:
            result = tools[call.name].handler(call.arguments, self._tool_context())
        except Exception as e:  # инструмент не должен ронять диалог
            log.exception("Ошибка инструмента %s", call.name)
            return {"ok": False, "errors": [f"Внутренняя ошибка инструмента: {type(e).__name__}"]}
        if call.name == "create_handoff" and result.get("ok"):
            self.cards.append(self.store.get_card(result["card_id"]))
        return result

    def send(self, text: str) -> Reply:
        """Добавляет реплику пользователя, получает ответ активного ассистента.

        Если модель вызывает инструменты, они выполняются, результат возвращается модели,
        и так до текстового ответа (не больше MAX_TOOL_ROUNDS раундов).
        """
        start = len(self.history)
        self.history.append({"role": "user", "content": text})
        specs = [t.spec() for t in tools_for(self.passport)] or None
        try:
            for round_no in range(MAX_TOOL_ROUNDS + 1):
                messages = [{"role": "system", "content": self.system_prompt()}, *self.history]
                # в последнем раунде инструменты не даем — модель обязана ответить текстом
                reply = self._client().chat(messages, specs if round_no < MAX_TOOL_ROUNDS else None)
                if not reply.tool_calls:
                    break
                self.history.append({
                    "role": "assistant",
                    "content": reply.text or None,
                    "tool_calls": [{"id": c.id, "type": "function",
                                    "function": {"name": c.name, "arguments": json.dumps(c.arguments, ensure_ascii=False)}}
                                   for c in reply.tool_calls],
                })
                if round_no == MAX_TOOL_ROUNDS - 1:
                    log.warning("Модель исчерпала лимит вызовов инструментов (%s)", MAX_TOOL_ROUNDS)
                for call in reply.tool_calls:
                    result = self._run_tool(call)
                    self.history.append({"role": "tool", "tool_call_id": call.id,
                                         "content": json.dumps(result, ensure_ascii=False)})
        except Exception:
            del self.history[start:]  # неудачная реплика не должна оставаться в истории
            raise
        self.history.append({"role": "assistant", "content": reply.text})
        return reply
