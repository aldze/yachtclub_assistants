"""Сборка системного промпта: prompts/<id>.md + блок <knowledge> из файлов паспорта."""
from __future__ import annotations

import re
from pathlib import Path

from app.config import ROOT, ConfigError, Passport, load_company, render, split_front_matter

PROMPT_LIMIT = 8000  # лимит поля Instructions в Custom GPTs; база знаний в него не входит
GREETING_SECTION = re.compile(r"^##\s*(?:\d+\.\s*)?Приветствие\s*$(.*?)(?=^##\s)", re.M | re.S)
QUOTED = re.compile(r"«(.+?)»", re.S)


def render_prompt(passport: Passport, company: dict | None = None, root: Path = ROOT) -> str:
    """Текст промпта с подставленными плейсхолдерами (без базы знаний)."""
    if company is None:
        company, _ = load_company(root)
    path = root / passport.prompt
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(f"{passport.id}: не найден файл промпта {passport.prompt}") from None
    return render(text, company, passport.prompt).strip()


def render_knowledge(passport: Passport, company: dict | None = None, root: Path = ROOT) -> str:
    """Блок <knowledge>: файлы базы знаний без front matter, у каждого — строка «Источник: <id>»."""
    if company is None:
        company, _ = load_company(root)
    parts = []
    for rel in passport.knowledge:
        path = root / rel
        try:
            meta, body = split_front_matter(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ConfigError(f"{passport.id}: не найден файл базы знаний {rel}") from None
        allowed = meta.get("assistants")
        if allowed and passport.id not in allowed:
            raise ConfigError(
                f"{rel}: файл подключен к ассистенту {passport.id}, "
                f"но в front matter assistants: {allowed}"
            )
        source_id = meta.get("id") or path.stem
        parts.append(f"Источник: {source_id}\n\n{render(body, company, rel).strip()}")
    return "<knowledge>\n" + "\n\n---\n\n".join(parts) + "\n</knowledge>"


def build_system_prompt(passport: Passport, company: dict | None = None, root: Path = ROOT) -> str:
    """Полный системный промпт. Промпт длиннее PROMPT_LIMIT — ошибка."""
    if company is None:
        company, _ = load_company(root)
    prompt = render_prompt(passport, company, root)
    if len(prompt) > PROMPT_LIMIT:
        raise ConfigError(
            f"{passport.prompt}: {len(prompt)} знаков после подстановки, лимит {PROMPT_LIMIT}"
        )
    if not passport.knowledge:
        return prompt
    return prompt + "\n\n" + render_knowledge(passport, company, root)


def extract_greeting(passport: Passport, company: dict | None = None, root: Path = ROOT) -> str | None:
    """Приветствие из раздела «Приветствие» промпта (текст в кавычках «»), если оно есть."""
    match = GREETING_SECTION.search(render_prompt(passport, company, root) + "\n## ")
    if not match:
        return None
    quoted = QUOTED.search(match.group(1))
    return quoted.group(1).strip() if quoted else None
