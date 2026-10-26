"""Тесты карточки передачи: параметры инструмента, валидация, ПДн, хранение, диалог."""
import json

import pytest

from app.chat import Conversation
from app.config import load_passports
from app.llm import LLMClient, Reply, ToolCall
from app.store import Store, mask_card, mask_text
from app.tools import ToolContext, tools_for
from app.tools.handoff import create_handoff, format_card, tool_parameters

VALID = {
    "target_department": "sales",
    "priority": "normal",
    "topic": "подбор катера",
    "summary": "Семья из 5 человек, ищут боурайдер для Волги к сезону.",
    "pd_consent": True,
    "contact": {"name": "Иван Петров", "phone": "+7 916 123-45-02", "preferred_time": "вечер"},
    "details": {"purpose": "семейный отдых", "waters": "река Волга", "models_of_interest": ["Sea Ray SPX"]},
    "next_step": "звонок",
}


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


@pytest.fixture
def ctx(store):
    sent = []
    context = ToolContext(assistant_id="sales", conversation_id="conv-1", store=store,
                          notifier=lambda card: sent.append(card) or "console")
    context.sent = sent
    return context


# ---------- параметры инструмента ----------

def test_system_fields_hidden_from_model():
    params = tool_parameters()
    for field in ("card_id", "created_at", "conversation_id", "channel", "source_assistant"):
        assert field not in params["properties"]
        assert field not in params["required"]
    assert {"target_department", "pd_consent", "next_step"} <= set(params["required"])
    assert "contact" in params["properties"]


def test_tools_from_passport():
    passports = load_passports()
    assert [t.name for t in tools_for(passports["sales"])] == ["create_handoff"]
    # инструменты интервью у hr еще не реализованы и пропускаются без ошибки
    assert [t.name for t in tools_for(passports["hr"])] == ["create_handoff"]


# ---------- создание карточки ----------

def test_valid_card_saved_and_notified(ctx, store):
    result = create_handoff(dict(VALID), ctx)
    assert result["ok"] is True
    card = store.get_card(result["card_id"])
    assert card["source_assistant"] == "sales"
    assert card["conversation_id"] == "conv-1"
    assert card["channel"] == "cli"
    assert card["contact"]["phone"] == "+7 916 123-45-02"
    assert ctx.sent and ctx.sent[0]["card_id"] == result["card_id"]


def test_no_consent_drops_contact(ctx, store):
    result = create_handoff({**VALID, "pd_consent": False}, ctx)
    assert result["ok"] is True
    assert "contact" not in store.get_card(result["card_id"])
    assert "contact" not in ctx.sent[0]


def test_model_cannot_override_system_fields(ctx, store):
    result = create_handoff({**VALID, "source_assistant": "hr", "card_id": "hack"}, ctx)
    card = store.get_card(result["card_id"])
    assert card["source_assistant"] == "sales" and card["card_id"] != "hack"


@pytest.mark.parametrize("broken", [
    {"target_department": "marketing"},
    {"priority": "asap"},
    {"summary": "x" * 501},
    {"unknown_field": 1},
])
def test_invalid_card_rejected_and_not_saved(ctx, store, broken):
    result = create_handoff({**VALID, **broken}, ctx)
    assert result["ok"] is False and result["errors"]
    assert not ctx.sent
    assert store.cards_for_conversation("conv-1") == []


def test_missing_required_field(ctx):
    args = dict(VALID)
    del args["next_step"]
    result = create_handoff(args, ctx)
    assert not result["ok"] and any("next_step" in e for e in result["errors"])


def test_format_card_readable(ctx, store):
    card = store.get_card(create_handoff(dict(VALID), ctx)["card_id"])
    text = format_card(card)
    assert "Отдел: Продажи" in text and "Приоритет: обычный" in text
    assert "Модели: Sea Ray SPX" in text and "Следующий шаг: звонок" in text


# ---------- маскирование ----------

def test_mask_text():
    assert mask_text("тел. +7 (916) 123-45-02") == "тел. +7 9** ***-**-02"
    assert mask_text("ivan.petrov@mail.ru") == "i***@mail.ru"
    assert mask_text("Princess 2015 года, 350 моточасов") == "Princess 2015 года, 350 моточасов"


def test_mask_card():
    masked = mask_card({**VALID, "card_id": "1"})
    dump = json.dumps(masked, ensure_ascii=False)
    assert "Иван" not in dump and "123-45" not in dump
    assert masked["contact"]["name"] == "И*** П***"
    assert VALID["contact"]["name"] == "Иван Петров"  # исходная карточка не изменена


# ---------- инструмент внутри диалога ----------

class ScriptedClient(LLMClient):
    """Сначала вызывает create_handoff, затем отвечает текстом по результату."""

    def __init__(self, passport, arguments=None):
        super().__init__(passport.model, passport.temperature, passport.max_tokens)
        self.arguments = arguments or VALID
        self.seen_tools = []

    def chat(self, messages, tools=None):
        self.seen_tools.append(tools)
        if messages[-1]["role"] == "tool":
            result = json.loads(messages[-1]["content"])
            return Reply(text=f"Готово, карточка {result.get('card_id')}" if result["ok"] else "Ошибка")
        return Reply(text="", tool_calls=[ToolCall(id="call_1", name="create_handoff", arguments=self.arguments)])


def test_conversation_runs_handoff_tool(store):
    clients = {}

    def factory(passport):
        clients[passport.id] = ScriptedClient(passport)
        return clients[passport.id]

    conv = Conversation("sales", load_passports(), client_factory=factory, store=store,
                        notifier=lambda card: "console")
    reply = conv.send("Да, согласен. Иван, +7 916 123-45-02, вечером")
    assert len(conv.cards) == 1
    assert reply.text == f"Готово, карточка {conv.cards[0]['card_id']}"
    assert conv.cards[0]["conversation_id"] == conv.conversation_id
    assert [m["role"] for m in conv.history] == ["user", "assistant", "tool", "assistant"]
    assert clients["sales"].seen_tools[0][0]["name"] == "create_handoff"


def test_tool_loop_is_limited(store):
    class Looping(ScriptedClient):
        def chat(self, messages, tools=None):
            self.seen_tools.append(tools)
            if tools is None:
                return Reply(text="Передам менеджеру")
            return Reply(text="", tool_calls=[ToolCall(id=f"c{len(self.seen_tools)}", name="create_handoff",
                                                       arguments={"pd_consent": True})])

    conv = Conversation("sales", load_passports(), client_factory=Looping, store=store,
                        notifier=lambda card: "console")
    assert conv.send("Свяжите с менеджером").text == "Передам менеджеру"
    assert conv.cards == []
