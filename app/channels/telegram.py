"""Telegram-канал на aiogram 3.

    python -m app.channels.telegram                    # единое окно: роутер, токен TG_TOKEN_WINDOW
    python -m app.channels.telegram --assistant hr     # бот одного ассистента, токен TG_TOKEN_HR

Команды бота: /start — приветствие и кнопки-подсказки, /reset — начать заново.
История диалога — в SQLite (DB_PATH), переживает перезапуск бота.
Файлы и фото бот не сохраняет: модель получает только отметку, что файл был.
"""
from __future__ import annotations

import argparse
import asyncio
import html
import logging
import os
import re

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatAction, ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove

from app.config import ConfigError, load_env, load_passports
from app.logs import setup_logging
from app.sessions import Answer, Sessions

log = logging.getLogger(__name__)

TG_LIMIT = 4096  # максимальная длина сообщения Telegram


def to_html(text: str) -> str:
    """Ответ модели → HTML для Telegram: экранирование и **жирный**."""
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", html.escape(text))


def split_message(text: str, limit: int = TG_LIMIT) -> list[str]:
    """Режет длинный ответ по абзацам, затем по строкам, чтобы не превысить лимит Telegram."""
    parts, chunk = [], ""
    for line in text.split("\n"):
        while len(line) > limit:
            parts.append(line[:limit])
            line = line[limit:]
        candidate = f"{chunk}\n{line}" if chunk else line
        if len(candidate) > limit:
            parts.append(chunk)
            chunk = line
        else:
            chunk = candidate
    if chunk:
        parts.append(chunk)
    return parts or [""]


def render_answer(answer: Answer, window: bool) -> str:
    """В едином окне один бот говорит за трех ассистентов — подписываем, кто отвечает."""
    body = to_html(answer.text)
    if not window:
        return body
    header = f"<b>{html.escape(answer.assistant.name)}</b>"
    if answer.switched_from:
        header = (f"<i>Разговор продолжит {html.escape(answer.assistant.name)} — "
                  f"{html.escape(answer.assistant.title.lower())}</i>\n\n" + header)
    return f"{header}\n{body}"


def keyboard(starters: list[str]) -> ReplyKeyboardMarkup | ReplyKeyboardRemove:
    if not starters:
        return ReplyKeyboardRemove()
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=s)] for s in starters],
                               resize_keyboard=True, one_time_keyboard=True)


def file_marker(message: Message) -> str | None:
    """Описание присланного файла для модели (сам файл не скачивается и не хранится)."""
    if message.document:
        return f"[Пользователь отправил файл «{message.document.file_name or 'без имени'}». " \
               "Бот файлы не сохраняет — предложи прислать ссылку или отправить на e-mail из базы знаний.]"
    if message.photo or message.video:
        return "[Пользователь отправил фото или видео. Бот их не сохраняет — передай в карточке, " \
               "что медиа есть, специалист запросит его при связи.]"
    if message.voice or message.audio:
        return "[Пользователь отправил голосовое сообщение. Бот их не распознает — попроси написать текстом.]"
    return None


def build_dispatcher(sessions: Sessions, bot_id: str) -> Dispatcher:
    dp = Dispatcher()
    window = sessions.router is not None

    def user_id(message: Message) -> str:
        # один пользователь может писать нескольким ботам — их диалоги не должны смешиваться
        return f"{bot_id}:{message.chat.id}"

    async def send(message: Message, answer: Answer) -> None:
        parts = split_message(render_answer(answer, window))
        for i, part in enumerate(parts):
            markup = keyboard(answer.starters) if i == len(parts) - 1 else None
            await message.answer(part, reply_markup=markup)

    @dp.message(CommandStart())
    @dp.message(Command("reset"))
    async def on_start(message: Message) -> None:
        answer = await asyncio.to_thread(sessions.start, user_id(message))
        await send(message, answer)

    @dp.message(F.text | F.document | F.photo | F.video | F.voice | F.audio)
    async def on_message(message: Message) -> None:
        text = message.text or file_marker(message)
        if message.caption:
            text = f"{text}\n{message.caption}"
        await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
        answer = await asyncio.to_thread(sessions.message, user_id(message), text[:2000])
        await send(message, answer)

    return dp


async def run(token: str, sessions: Sessions, bot_id: str) -> None:
    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    me = await bot.get_me()
    log.info("Telegram-бот @%s запущен (%s)", me.username,
             "единое окно" if sessions.router else f"ассистент {sessions.fixed}")
    await build_dispatcher(sessions, bot_id).start_polling(bot)


def main() -> int:
    load_env()
    setup_logging()
    passports = load_passports()
    ap = argparse.ArgumentParser(description="Telegram-бот ассистентов")
    ap.add_argument("--assistant", choices=list(passports), help="бот одного ассистента; без флага — единое окно")
    args = ap.parse_args()

    bot_id = args.assistant or "window"
    env = f"TG_TOKEN_{bot_id.upper()}"
    token = os.getenv(env)
    if not token:
        log.error("Не задан %s в .env — получите токен у @BotFather", env)
        return 1
    try:
        sessions = Sessions("telegram", assistant=args.assistant)
    except ConfigError as e:
        log.error("Ошибка конфигурации: %s", e)
        return 1
    try:
        asyncio.run(run(token, sessions, bot_id))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
