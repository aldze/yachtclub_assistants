"""Диалог с ассистентами: общая история и активный ассистент, которого можно сменить.

Под «единое окно» (CLAUDE.md, решение 5): посетитель ведет один разговор,
а отвечает тот ассистент, который сейчас активен. Роутер появится на этапе 4.
"""
from __future__ import annotations

from typing import Callable

from app.config import Passport, load_company, load_passports
from app.llm import LLMClient, Reply, get_client
from app.prompts import build_system_prompt, extract_greeting

ClientFactory = Callable[[Passport], LLMClient]


class Conversation:
    def __init__(self, assistant_id: str, passports: dict[str, Passport] | None = None,
                 client_factory: ClientFactory = get_client):
        self.passports = passports or load_passports()
        self.company, _ = load_company()
        self._client_factory = client_factory
        self._clients: dict[str, LLMClient] = {}
        self._prompts: dict[str, str] = {}
        self.history: list[dict] = []
        self.active = ""
        self.switch(assistant_id)

    @property
    def passport(self) -> Passport:
        return self.passports[self.active]

    def switch(self, assistant_id: str) -> None:
        """Делает ассистента активным; история диалога сохраняется."""
        if assistant_id not in self.passports:
            raise KeyError(f"Неизвестный ассистент «{assistant_id}». Доступны: {', '.join(self.passports)}")
        if assistant_id not in self._prompts:
            self._prompts[assistant_id] = build_system_prompt(self.passports[assistant_id], self.company)
        self.active = assistant_id

    def reset(self) -> None:
        self.history.clear()

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

    def send(self, text: str) -> Reply:
        """Добавляет реплику пользователя, получает ответ активного ассистента."""
        self.history.append({"role": "user", "content": text})
        messages = [{"role": "system", "content": self.system_prompt()}, *self.history]
        try:
            reply = self._client().chat(messages)
        except Exception:
            self.history.pop()  # неудачная реплика не должна оставаться в истории
            raise
        self.history.append({"role": "assistant", "content": reply.text})
        return reply
