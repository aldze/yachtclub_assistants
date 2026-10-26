"""create_handoff: карточка передачи → проверка по схеме → SQLite → уведомление отдела.

Параметры инструмента строятся из schemas/handoff_card.schema.json: поля, помеченные
«Заполняется системой», и source_assistant модели не передаются — их заполняет код.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from functools import lru_cache

import jsonschema

from app.config import ROOT, load_assistants_config
from app.store import Store, mask_card
from app.tools.base import Tool, ToolContext

log = logging.getLogger(__name__)

SCHEMA_PATH = ROOT / "schemas" / "handoff_card.schema.json"
SYSTEM_MARK = "Заполняется системой"
# source_assistant система тоже знает сама: это активный ассистент диалога
SYSTEM_FILLED_EXTRA = {"source_assistant"}

DESCRIPTION = (
    "Создает карточку передачи обращения в отдел компании, когда клиент или кандидат готов к следующему шагу "
    "(звонок, встреча, показ, осмотр, ТО, интервью) или запрос нужно передать человеку. "
    "pd_consent=true только если согласие на обработку персональных данных явно получено в диалоге; "
    "без согласия contact не заполняй. Пиши только факты из диалога, ничего не додумывай."
)

PRIORITY_RU = {"normal": "обычный", "high": "высокий", "urgent": "срочный"}
DETAIL_RU = {
    "purpose": "Назначение", "waters": "Акватория", "guests": "Гостей", "experience": "Опыт",
    "budget": "Бюджет", "timeline": "Сроки", "models_of_interest": "Модели",
    "boat_model": "Лодка", "boat_year": "Год", "hin_or_contract": "HIN / договор", "engine": "Двигатель",
    "engine_hours": "Моточасы", "boat_location": "Где лодка", "issue": "Проблема",
    "media_attached": "Фото/видео", "desired_dates": "Желаемые даты",
    "vacancy": "Вакансия", "screening_answers": "Ответы на отбор", "resume": "Резюме",
    "interview_slot": "Слот интервью", "hr_topic": "Тема HR",
}
CONTACT_RU = {"name": "Имя", "phone": "Телефон", "messenger": "Мессенджер", "email": "E-mail",
              "city": "Город", "preferred_time": "Удобное время"}


@lru_cache(maxsize=1)
def card_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def system_fields(schema: dict) -> set[str]:
    marked = {k for k, v in schema["properties"].items() if SYSTEM_MARK in v.get("description", "")}
    return marked | SYSTEM_FILLED_EXTRA


def tool_parameters(schema: dict | None = None) -> dict:
    """JSON Schema параметров инструмента: схема карточки без полей, которые заполняет система."""
    schema = copy.deepcopy(schema or card_schema())
    hidden = system_fields(schema)
    return {
        "type": "object",
        "properties": {k: v for k, v in schema["properties"].items() if k not in hidden},
        "required": [k for k in schema.get("required", []) if k not in hidden],
        "additionalProperties": False,
    }


def build_card(arguments: dict, ctx: ToolContext) -> dict:
    """Карточка из аргументов модели + системные поля; без согласия contact удаляется."""
    card = {k: v for k, v in arguments.items() if k not in system_fields(card_schema())}
    if card.get("pd_consent") is not True:
        card.pop("contact", None)
    card.update(
        card_id=uuid.uuid4().hex[:12],
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        conversation_id=ctx.conversation_id,
        channel=ctx.channel,
        source_assistant=ctx.assistant_id,
    )
    return card


def validate_card(card: dict) -> list[str]:
    validator = jsonschema.Draft202012Validator(card_schema())
    return [f"{'/'.join(map(str, e.absolute_path)) or 'карточка'}: {e.message}"
            for e in sorted(validator.iter_errors(card), key=lambda e: list(e.absolute_path))]


# ---------- уведомление отдела ----------

def format_card(card: dict) -> str:
    departments = load_assistants_config().get("departments", {})
    dept = departments.get(card["target_department"], {}).get("title", card["target_department"])
    lines = [
        f"КАРТОЧКА ПЕРЕДАЧИ №{card['card_id']}",
        f"Отдел: {dept}",
        f"Приоритет: {PRIORITY_RU.get(card['priority'], card['priority'])}",
        f"От: {card['source_assistant']} ({card['channel']}), {card['created_at']}",
        f"Тема: {card['topic']}",
        f"Запрос: {card['summary']}",
        f"Согласие на обработку ПДн: {'да' if card['pd_consent'] else 'нет'}",
    ]
    contact = card.get("contact") or {}
    if contact:
        lines.append("Клиент: " + "; ".join(f"{CONTACT_RU.get(k, k)}: {v}" for k, v in contact.items() if v))
    elif not card["pd_consent"]:
        lines.append("Клиент: контакты не собраны (нет согласия)")
    details = card.get("details") or {}
    for key, value in details.items():
        if value in (None, "", []):
            continue
        if key == "screening_answers":
            value = "; ".join(f"{qa.get('q', '')} — {qa.get('a', '')}" for qa in value)
        elif isinstance(value, list):
            value = ", ".join(map(str, value))
        elif isinstance(value, bool):
            value = "да" if value else "нет"
        lines.append(f"{DETAIL_RU.get(key, key)}: {value}")
    lines.append(f"Следующий шаг: {card['next_step']}")
    return "\n".join(lines)


def notify_department(card: dict) -> str:
    """Отправляет карточку в Telegram-чат отдела. Если чат не задан — печатает в консоль.

    Чат берется из config/assistants.yaml → departments.<отдел>.notify (имя переменной .env).
    Бот-отправитель: TG_NOTIFY_TOKEN, иначе TG_TOKEN_<ассистент>.
    """
    text = format_card(card)
    dept = load_assistants_config().get("departments", {}).get(card["target_department"], {})
    chat_id = os.getenv(dept.get("notify", "")) if dept.get("notify") else None
    token = os.getenv("TG_NOTIFY_TOKEN") or os.getenv(f"TG_TOKEN_{card['source_assistant'].upper()}")
    if not (chat_id and token):
        print("\n" + "─" * 60 + "\n" + text + "\n" + "─" * 60)
        return "console"
    try:
        body = json.dumps({"chat_id": chat_id, "text": text}).encode()
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage", data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            response.read()
        return "sent"
    except (urllib.error.URLError, TimeoutError) as e:
        # Токен не должен попасть в лог вместе с адресом запроса
        reason = str(getattr(e, "reason", e)).replace(token, "***")
        log.warning("Карточка %s не отправлена в Telegram: %s", card["card_id"], reason)
        print("\n" + text)
        return f"error: {reason}"


# ---------- инструмент ----------

def create_handoff(arguments: dict, ctx: ToolContext) -> dict:
    card = build_card(arguments, ctx)
    errors = validate_card(card)
    if errors:
        log.info("Карточка отклонена валидацией: %s", errors)
        return {"ok": False, "errors": errors, "hint": "Исправь поля и вызови create_handoff еще раз"}
    store = ctx.store or Store()
    store.save_card(card)
    status = ctx.notifier(card)
    store.set_notify_status(card["card_id"], status)
    log.info("Карточка создана: %s", json.dumps(mask_card(card), ensure_ascii=False))
    return {"ok": True, "card_id": card["card_id"], "target_department": card["target_department"]}


TOOL = Tool(
    name="create_handoff",
    description=DESCRIPTION,
    parameters=tool_parameters(),
    handler=create_handoff,
)
