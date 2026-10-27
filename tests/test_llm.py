"""Тесты адаптера OpenAI без сетевых запросов."""
from app.llm.openai_compat import DEFAULT_BASE_URL, OpenAICompatClient


def test_empty_base_url_in_env_uses_openai(monkeypatch):
    # В .env.example строка OPENAI_BASE_URL= пустая — запрос не должен уходить на пустой адрес
    monkeypatch.setenv("OPENAI_BASE_URL", "")
    client = OpenAICompatClient("gpt-4.1-mini", 0.4, 800, api_key="test")
    assert str(client._client.base_url).rstrip("/") == DEFAULT_BASE_URL
    assert client.base_url is None


def test_custom_base_url(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://gateway.example/v1")
    client = OpenAICompatClient("gpt-4.1-mini", 0.4, 800, api_key="test")
    assert str(client._client.base_url).rstrip("/") == "https://gateway.example/v1"


def test_exhausted_balance_is_reported_as_balance(monkeypatch):
    import httpx
    import openai
    import pytest

    from app.llm import LLMError

    client = OpenAICompatClient("gpt-4.1-mini", 0.4, 800, api_key="test")
    body = {"message": "You have no credits remaining.", "type": "insufficient_quota",
            "code": "credit_balance_exhausted"}
    response = httpx.Response(429, request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"))

    def fail(**kwargs):
        raise openai.RateLimitError("no credits", response=response, body=body)

    monkeypatch.setattr(client._client.chat.completions, "create", fail)
    with pytest.raises(LLMError, match="баланс"):
        client.chat([{"role": "user", "content": "привет"}])
