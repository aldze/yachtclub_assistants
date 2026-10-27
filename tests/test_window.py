"""Тесты единого окна: роутер, передача разговора, сессии — без вызовов LLM."""
import json

import pytest

from app.llm import LLMClient, LLMError, Reply
from app.router import Route, Router
from app.sessions import Sessions
from app.store import Store


class FakeRouterLLM(LLMClient):
    """Отвечает заранее заданными решениями по ключевым словам."""

    def __init__(self, rules=None, raw=None):
        super().__init__("router", 0.0, 100)
        self.rules = rules or {}
        self.raw = raw
        self.seen = []

    def chat(self, messages, tools=None, json_mode=False):
        self.seen.append(messages)
        if self.raw is not None:
            return Reply(text=self.raw)
        text = messages[-1]["content"].rsplit("Новая реплика посетителя:\n", 1)[1]
        for word, aid in self.rules.items():
            if word in text:
                return Reply(text=json.dumps({"assistant": aid, "reason": "тема"}))
        active = messages[-1]["content"].split("\n", 1)[0].split(": ")[1]
        return Reply(text=json.dumps({"assistant": active, "reason": "продолжение"}))


class EchoLLM(LLMClient):
    def __init__(self, passport):
        super().__init__(passport.model, passport.temperature, passport.max_tokens)
        self.name = passport.name
        self.seen = []

    def chat(self, messages, tools=None, json_mode=False):
        self.seen.append(messages)
        return Reply(text=f"{self.name}: ответ")


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


def make_sessions(store, rules=None, **kw):
    from app.config import load_passports
    router = Router(load_passports(), client=FakeRouterLLM(rules))
    return Sessions("web", store=store, router=router, client_factory=EchoLLM,
                    notifier=lambda card: "test", **kw)


# ---------- роутер ----------

def test_router_prompt_lists_all_assistants():
    from app.config import load_passports
    router = Router(load_passports(), client=FakeRouterLLM())
    for name in ("`sales`", "`service`", "`hr`", "Лоцман", "Боцман", "Штурман"):
        assert name in router.system_prompt
    assert "{{" not in router.system_prompt


@pytest.mark.parametrize("raw", ["не json", '{"assistant": "captain"}', '{"x": 1}'])
def test_router_errors_keep_current(raw):
    from app.config import load_passports
    router = Router(load_passports(), client=FakeRouterLLM(raw=raw))
    route = router.route("service", [], "что-то")
    assert route == Route("service", route.reason, switched=False)


def test_router_llm_failure_keeps_current():
    from app.config import load_passports

    class Down(FakeRouterLLM):
        def chat(self, *a, **kw):
            raise LLMError("сеть")

    assert Router(load_passports(), client=Down()).route("hr", [], "катер").assistant == "hr"


# ---------- сессии ----------

def test_start_greets_with_default_and_window_starters(store):
    sessions = make_sessions(store)
    answer = sessions.start("u1")
    assert answer.assistant.id == "sales" and "Лоцман" in answer.text
    assert len(answer.starters) == 3


def test_message_routes_and_hands_over(store):
    sessions = make_sessions(store, rules={"мотор": "service"})
    sessions.start("u1")
    first = sessions.message("u1", "Хочу катер")
    assert first.assistant.id == "sales" and first.switched_from is None
    second = sessions.message("u1", "А у меня мотор сломался")
    assert second.assistant.id == "service" and second.switched_from.id == "sales"
    assert second.text == "Боцман: ответ"
    conv = sessions._cache["web:u1"]
    notes = [m for m in conv.history if m["role"] == "system"]
    assert len(notes) == 1 and "Лоцман" in notes[0]["content"] and "Боцман" in notes[0]["content"]
    # история общая: Боцман видит реплику, адресованную Лоцману
    boatswain_messages = conv._clients["service"].seen[-1]
    assert any(m.get("content") == "Хочу катер" for m in boatswain_messages)


def test_session_survives_restart(store):
    sessions = make_sessions(store, rules={"вакансии": "hr"})
    sessions.start("u1")
    sessions.message("u1", "Какие вакансии?")
    restarted = make_sessions(store)  # новый процесс, та же база
    assert restarted.current("u1").id == "hr"
    assert restarted.message("u1", "Моторист").assistant.id == "hr"
    history = restarted._cache["web:u1"].history
    assert [m["content"] for m in history if m["role"] == "user"] == ["Какие вакансии?", "Моторист"]


def test_sessions_are_separate(store):
    sessions = make_sessions(store, rules={"гарантия": "service"})
    sessions.start("a")
    sessions.start("b")
    sessions.message("a", "гарантия")
    assert sessions.current("a").id == "service" and sessions.current("b").id == "sales"


def test_reset_starts_over(store):
    sessions = make_sessions(store, rules={"гарантия": "service"})
    sessions.message("u1", "гарантия")
    answer = sessions.reset("u1")
    assert answer.assistant.id == "sales"
    assert [m["role"] for m in sessions._cache["web:u1"].history] == ["assistant"]  # только приветствие


def test_fixed_assistant_mode_has_no_router(store):
    sessions = Sessions("telegram", store=store, assistant="hr", client_factory=EchoLLM)
    assert sessions.router is None
    assert sessions.start("1").assistant.id == "hr"
    assert sessions.starters()[0] == "Какие у вас есть вакансии?"
    assert sessions.message("1", "мотор сломался").assistant.id == "hr"


def test_llm_error_returns_friendly_text(store):
    class Down(EchoLLM):
        def chat(self, *a, **kw):
            raise LLMError("баланс")

    sessions = Sessions("web", store=store, assistant="sales", client_factory=Down)
    answer = sessions.message("u1", "Привет")
    assert answer.error and "Извините" in answer.text and "{{" not in answer.text
