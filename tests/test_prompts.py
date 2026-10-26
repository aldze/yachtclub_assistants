"""Тесты app/prompts.py и app/chat.py без вызовов LLM."""
import pytest

from app.chat import Conversation
from app.config import ConfigError, get_passport, load_company, load_passports
from app.llm import LLMClient, Reply
from app.prompts import PROMPT_LIMIT, build_system_prompt, extract_greeting, render_prompt

ASSISTANTS = ["sales", "service", "hr"]


@pytest.fixture(scope="module")
def company():
    return load_company()[0]


@pytest.mark.parametrize("aid", ASSISTANTS)
def test_system_prompt_is_complete(aid, company):
    passport = get_passport(aid)
    text = build_system_prompt(passport, company)
    assert "{{" not in text, "остались неподставленные плейсхолдеры"
    assert text.count("<knowledge>") == 1 and text.rstrip().endswith("</knowledge>")
    assert text.count("Источник: ") == len(passport.knowledge)
    assert "\nassistants:" not in text and "\nstatus:" not in text, "front matter не вырезан"
    assert company["COMPANY"] in text


@pytest.mark.parametrize("aid", ASSISTANTS)
def test_prompt_within_limit(aid, company):
    assert len(render_prompt(get_passport(aid), company)) <= PROMPT_LIMIT


def test_knowledge_sources_use_front_matter_id(company):
    text = build_system_prompt(get_passport("sales"), company)
    assert "Источник: sales-faq" in text
    assert "Источник: service-warranty" in text


def test_prompt_over_limit_is_error(tmp_path, company):
    (tmp_path / "long.md").write_text("а" * (PROMPT_LIMIT + 1), encoding="utf-8")
    passport = get_passport("sales")
    passport.prompt, passport.knowledge = "long.md", []
    with pytest.raises(ConfigError, match="лимит"):
        build_system_prompt(passport, company, root=tmp_path)


def test_knowledge_file_for_other_assistant_is_error(tmp_path, company):
    (tmp_path / "p.md").write_text("Промпт", encoding="utf-8")
    (tmp_path / "kb.md").write_text("---\nid: hr-only\nassistants: [hr]\n---\nФакт", encoding="utf-8")
    passport = get_passport("sales")
    passport.prompt, passport.knowledge = "p.md", ["kb.md"]
    with pytest.raises(ConfigError, match="hr-only|kb.md"):
        build_system_prompt(passport, company, root=tmp_path)


@pytest.mark.parametrize("aid,name", [("sales", "Лоцман"), ("service", "Боцман"), ("hr", "Штурман")])
def test_greeting_from_prompt(aid, name, company):
    greeting = extract_greeting(get_passport(aid), company)
    assert greeting and greeting.startswith("Здравствуйте") and name in greeting
    assert "{{" not in greeting


# ---------- диалог с подставной моделью ----------

class FakeClient(LLMClient):
    def __init__(self, passport):
        super().__init__(passport.model, passport.temperature, passport.max_tokens)
        self.name = passport.name
        self.calls = []

    def chat(self, messages, tools=None):
        self.calls.append(messages)
        return Reply(text=f"{self.name}: ответ")


def test_conversation_keeps_history_and_switches():
    conv = Conversation("sales", load_passports(), client_factory=FakeClient)
    assert conv.send("Хочу катер").text == "Лоцман: ответ"
    conv.switch("service")
    assert conv.send("А где ТО?").text == "Боцман: ответ"
    assert [m["role"] for m in conv.history] == ["user", "assistant", "user", "assistant"]
    last_call = conv._clients["service"].calls[-1]
    assert "Боцман" in last_call[0]["content"]  # системный промпт активного ассистента
    assert last_call[1]["content"] == "Хочу катер"  # история общая


def test_conversation_reset():
    conv = Conversation("hr", load_passports(), client_factory=FakeClient)
    conv.send("Привет")
    conv.reset()
    assert conv.history == []


def test_failed_call_does_not_stay_in_history():
    class Broken(FakeClient):
        def chat(self, messages, tools=None):
            raise RuntimeError("сеть")

    conv = Conversation("sales", load_passports(), client_factory=Broken)
    with pytest.raises(RuntimeError):
        conv.send("Привет")
    assert conv.history == []


def test_greeting_goes_to_history_only_at_start():
    conv = Conversation("sales", load_passports(), client_factory=FakeClient)
    greeting = conv.greet()
    assert conv.history == [{"role": "assistant", "content": greeting}]
    conv.send("Хочу катер")
    conv.switch("service")
    conv.greet()
    assert len(conv.history) == 3  # посреди диалога приветствие не дублируется в историю
