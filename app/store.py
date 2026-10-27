"""SQLite-хранилище карточек передачи и маскирование ПДн для логов.

База — DB_PATH из .env (по умолчанию data/app.sqlite3, папка data/ в .gitignore).
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.config import ROOT

SCHEMA = """
CREATE TABLE IF NOT EXISTS handoff_cards (
    card_id           TEXT PRIMARY KEY,
    created_at        TEXT NOT NULL,
    conversation_id   TEXT NOT NULL,
    channel           TEXT NOT NULL,
    source_assistant  TEXT NOT NULL,
    target_department TEXT NOT NULL,
    priority          TEXT NOT NULL,
    card              TEXT NOT NULL,   -- карточка целиком, JSON
    notify_status     TEXT             -- sent | console | error: ...
);
CREATE INDEX IF NOT EXISTS idx_cards_conversation ON handoff_cards(conversation_id);

-- Сессии каналов (веб, Telegram): текущий диалог посетителя целиком
CREATE TABLE IF NOT EXISTS sessions (
    session_key     TEXT PRIMARY KEY,   -- <канал>:<id посетителя>
    conversation_id TEXT NOT NULL,
    active          TEXT NOT NULL,      -- активный ассистент
    history         TEXT NOT NULL,      -- JSON: сообщения в формате OpenAI
    updated_at      TEXT NOT NULL
);
"""


def db_path() -> Path:
    path = Path(os.getenv("DB_PATH") or "data/app.sqlite3")
    return path if path.is_absolute() else ROOT / path


class Store:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else db_path()
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        # Одно соединение на процесс; веб и Telegram обращаются из разных потоков — под замком
        # timeout: веб и Telegram в Docker — два процесса с одной базой, запись ждет освобождения
        self._db = sqlite3.connect(str(self.path), check_same_thread=False, timeout=10)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            if str(self.path) != ":memory:":
                self._db.execute("PRAGMA journal_mode=WAL")  # чтение не блокирует запись
            self._db.executescript(SCHEMA)

    def save_card(self, card: dict) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO handoff_cards (card_id, created_at, conversation_id, channel, source_assistant,"
                " target_department, priority, card) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (card["card_id"], card["created_at"], card["conversation_id"], card["channel"],
                 card["source_assistant"], card["target_department"], card["priority"],
                 json.dumps(card, ensure_ascii=False)),
            )

    def set_notify_status(self, card_id: str, status: str) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE handoff_cards SET notify_status = ? WHERE card_id = ?", (status, card_id))

    def get_card(self, card_id: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT card FROM handoff_cards WHERE card_id = ?", (card_id,)).fetchone()
        return json.loads(row["card"]) if row else None

    def cards_for_conversation(self, conversation_id: str) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT card FROM handoff_cards WHERE conversation_id = ? ORDER BY created_at", (conversation_id,)
            ).fetchall()
        return [json.loads(r["card"]) for r in rows]

    # ---------- сессии ----------

    def save_session(self, session_key: str, conversation_id: str, active: str, history: list[dict]) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO sessions (session_key, conversation_id, active, history, updated_at)"
                " VALUES (?, ?, ?, ?, ?) ON CONFLICT(session_key) DO UPDATE SET"
                " conversation_id = excluded.conversation_id, active = excluded.active,"
                " history = excluded.history, updated_at = excluded.updated_at",
                (session_key, conversation_id, active, json.dumps(history, ensure_ascii=False), now),
            )

    def load_session(self, session_key: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM sessions WHERE session_key = ?", (session_key,)).fetchone()
        if not row:
            return None
        return {"conversation_id": row["conversation_id"], "active": row["active"],
                "history": json.loads(row["history"])}

    def delete_session(self, session_key: str) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM sessions WHERE session_key = ?", (session_key,))

    def close(self) -> None:
        with self._lock:
            self._db.close()


# ---------- маскирование ПДн ----------

EMAIL = re.compile(r"\b([\w.+-])[\w.+-]*@([\w-]+(?:\.[\w-]+)+)\b")
PHONE = re.compile(r"(?<!\w)(\+?\d)[\d\s()\-]{8,}(\d{2})(?!\w)")


def mask_phone(match: re.Match) -> str:
    digits = re.sub(r"\D", "", match.group(0))
    if len(digits) < 10:
        return match.group(0)
    # +7 9** ***-**-02: код страны, первая цифра кода оператора и две последние цифры
    return f"{match.group(1)} {digits[-10]}** ***-**-{digits[-2:]}"


def mask_text(text: str) -> str:
    """Маскирует телефоны и e-mail в произвольном тексте."""
    text = EMAIL.sub(lambda m: f"{m.group(1)}***@{m.group(2)}", text)
    return PHONE.sub(mask_phone, text)


def mask_name(name: str) -> str:
    return " ".join(part[:1] + "***" for part in name.split()) if name else name


def mask_card(card: dict) -> dict:
    """Копия карточки для логов: контакты и свободный текст без ПДн в открытом виде."""
    masked = json.loads(json.dumps(card, ensure_ascii=False))
    contact = masked.get("contact") or {}
    if contact.get("name"):
        contact["name"] = mask_name(contact["name"])
    for key, value in contact.items():
        if key != "name" and isinstance(value, str):
            contact[key] = mask_text(value)
    for key in ("summary", "topic"):
        if isinstance(masked.get(key), str):
            masked[key] = mask_text(masked[key])
    return masked
