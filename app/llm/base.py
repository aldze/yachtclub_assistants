"""Единый интерфейс провайдеров LLM.

Сообщения — в формате OpenAI: {"role": "system" | "user" | "assistant" | "tool", "content": "..."}.
Инструменты — нейтральное описание {"name", "description", "parameters": JSON Schema};
адаптер сам переводит его в формат своего провайдера.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class LLMError(Exception):
    """Ошибка обращения к провайдеру с понятным человеку сообщением."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class Reply:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)


class LLMClient(ABC):
    def __init__(self, model: str, temperature: float, max_tokens: int):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

    @abstractmethod
    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> Reply:
        """Отправляет историю сообщений и возвращает ответ модели."""
