"""Выбор адаптера LLM по полю provider паспорта (или LLM_PROVIDER в .env)."""
from __future__ import annotations

from app.config import Passport
from app.llm.base import LLMClient, LLMError, Reply, ToolCall

__all__ = ["get_client", "LLMClient", "LLMError", "Reply", "ToolCall"]

PLANNED = {"yandex", "gigachat", "anthropic"}


def get_client(passport: Passport) -> LLMClient:
    if passport.provider == "openai":
        from app.llm.openai_compat import OpenAICompatClient
        return OpenAICompatClient(passport.model, passport.temperature, passport.max_tokens)
    if passport.provider in PLANNED:
        raise LLMError(f"Адаптер «{passport.provider}» еще не реализован; пока используйте LLM_PROVIDER=openai")
    raise LLMError(f"Неизвестный провайдер «{passport.provider}». Варианты: openai, {', '.join(sorted(PLANNED))}")
