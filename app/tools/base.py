"""Общие типы инструментов (function calling)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from app.store import Store


def _default_notifier(card: dict) -> str:
    from app.tools.handoff import notify_department
    return notify_department(card)


@dataclass
class ToolContext:
    """Что инструмент знает о диалоге помимо аргументов модели."""
    assistant_id: str
    conversation_id: str
    channel: str = "cli"
    store: Store | None = None
    notifier: Callable[[dict], str] = field(default=_default_notifier)


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict  # JSON Schema аргументов
    handler: Callable[[dict, ToolContext], dict]

    def spec(self) -> dict:
        """Нейтральное описание для адаптера LLM."""
        return {"name": self.name, "description": self.description, "parameters": self.parameters}
