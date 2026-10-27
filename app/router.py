"""Роутер единого окна: решает, какой ассистент отвечает на новую реплику посетителя.

Инструкция — prompts/router.md (плейсхолдер {{ASSISTANTS}} заполняется из паспортов).
Модель роутера — паспорт `router` в config/assistants.yaml (раздел window), по умолчанию defaults.
"""
from __future__ import annotations

import dataclasses
import json
import logging
from dataclasses import dataclass

from app.config import ROOT, Passport, load_assistants_config, load_company, render
from app.llm import LLMClient, LLMError, get_client

log = logging.getLogger(__name__)

ROUTER_PROMPT = ROOT / "prompts" / "router.md"
CONTEXT_MESSAGES = 6     # сколько последних реплик видит роутер
MESSAGE_CHARS = 400      # длинные реплики обрезаются: роутеру нужна суть, а не детали


@dataclass
class Route:
    assistant: str
    reason: str = ""
    switched: bool = False


def window_config(root=ROOT) -> dict:
    return load_assistants_config(root).get("window", {}) or {}


def describe_assistants(passports: dict[str, Passport], root=ROOT) -> str:
    cfg = load_assistants_config(root).get("assistants", {})
    lines = []
    for aid, p in passports.items():
        a = cfg.get(aid, {})
        lines.append(f"- `{aid}` — {p.name}, {p.title}. Для кого: {a.get('audience', '')}. Задача: {a.get('goal', '')}.")
    return "\n".join(lines)


class Router:
    def __init__(self, passports: dict[str, Passport], client: LLMClient | None = None):
        self.passports = passports
        company, _ = load_company()
        values = {**company, "ASSISTANTS": describe_assistants(passports)}
        self.system_prompt = render(ROUTER_PROMPT.read_text(encoding="utf-8"), values, "prompts/router.md")
        self._client = client

    @property
    def client(self) -> LLMClient:
        if self._client is None:
            base = next(iter(self.passports.values()))
            model = window_config().get("router_model") or base.model
            self._client = get_client(dataclasses.replace(base, model=model, temperature=0.0, max_tokens=100))
        return self._client

    def route(self, active: str, history: list[dict], text: str) -> Route:
        """Кто отвечает на реплику text. При любой ошибке роутера — остается текущий ассистент."""
        context = []
        for m in history:
            if m.get("role") in ("user", "assistant") and m.get("content"):
                who = "Посетитель" if m["role"] == "user" else "Ассистент"
                context.append(f"{who}: {m['content'][:MESSAGE_CHARS]}")
        user = (f"Текущий ассистент: {active}\n\n"
                f"Последние реплики:\n" + ("\n".join(context[-CONTEXT_MESSAGES:]) or "(диалог только начался)") +
                f"\n\nНовая реплика посетителя:\n{text[:MESSAGE_CHARS]}")
        try:
            reply = self.client.chat([{"role": "system", "content": self.system_prompt},
                                      {"role": "user", "content": user}], json_mode=True)
            data = json.loads(reply.text)
        except (LLMError, json.JSONDecodeError) as e:
            log.warning("Роутер не сработал, остаемся у %s: %s", active, e)
            return Route(active, "ошибка роутера")
        target = data.get("assistant")
        if target not in self.passports:
            return Route(active, f"неизвестный ассистент «{target}»")
        return Route(target, str(data.get("reason", "")), switched=target != active)
