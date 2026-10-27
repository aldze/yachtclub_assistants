"""Адаптер OpenAI и OpenAI-совместимых API (шлюзы, локальные модели) через OPENAI_BASE_URL."""
from __future__ import annotations

import json
import os

import openai

from app.llm.base import LLMClient, LLMError, Reply, ToolCall

DEFAULT_BASE_URL = "https://api.openai.com/v1"


class OpenAICompatClient(LLMClient):
    def __init__(self, model: str, temperature: float, max_tokens: int,
                 api_key: str | None = None, base_url: str | None = None):
        super().__init__(model, temperature, max_tokens)
        api_key = api_key or os.getenv("OPENAI_API_KEY")
        base_url = base_url or os.getenv("OPENAI_BASE_URL") or None
        if not api_key:
            raise LLMError("Не задан OPENAI_API_KEY: пропишите ключ в файле .env (образец — .env.example)")
        self.base_url = base_url
        # Адрес передаем явно: иначе SDK сам читает OPENAI_BASE_URL и пустая строка из .env ломает запрос
        self._client = openai.OpenAI(api_key=api_key, base_url=base_url or DEFAULT_BASE_URL)

    def chat(self, messages: list[dict], tools: list[dict] | None = None, json_mode: bool = False) -> Reply:
        params: dict = {"model": self.model, "messages": messages, "temperature": self.temperature}
        # OpenAI перешел на max_completion_tokens; совместимые шлюзы обычно понимают только max_tokens
        params["max_tokens" if self.base_url else "max_completion_tokens"] = self.max_tokens
        if tools:
            params["tools"] = [{"type": "function", "function": t} for t in tools]
        if json_mode:
            params["response_format"] = {"type": "json_object"}

        try:
            response = self._client.chat.completions.create(**params)
        except openai.AuthenticationError:
            raise LLMError("Ключ OPENAI_API_KEY не принят провайдером — проверьте .env") from None
        except openai.NotFoundError:
            raise LLMError(f"Модель «{self.model}» не найдена у провайдера — проверьте LLM_MODEL") from None
        except openai.RateLimitError:
            raise LLMError("Превышен лимит запросов или закончился баланс у провайдера") from None
        except openai.APIConnectionError as e:
            cause = e.__cause__ or e.__context__
            detail = f" ({cause})" if cause else ""
            raise LLMError(f"Нет связи с провайдером LLM — проверьте интернет и OPENAI_BASE_URL{detail}") from None
        except openai.APIError as e:
            raise LLMError(f"Ошибка провайдера LLM: {e}") from None

        message = response.choices[0].message
        calls = []
        for call in message.tool_calls or []:
            try:
                arguments = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                arguments = {"_raw": call.function.arguments}
            calls.append(ToolCall(id=call.id, name=call.function.name, arguments=arguments))
        return Reply(text=message.content or "", tool_calls=calls)
