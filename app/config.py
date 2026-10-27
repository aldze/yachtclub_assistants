"""Конфигурация: .env, паспорта ассистентов, данные компании и подстановка {{КЛЮЧ}}.

Этот модуль — единственная реализация подстановки плейсхолдеров:
им пользуются и ассистенты, и tools/build_docx.py.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


class ConfigError(Exception):
    """Ошибка конфигурации с понятным человеку сообщением."""


def load_env(root: Path = ROOT) -> None:
    """Загружает .env, не перетирая уже заданные переменные окружения.

    Docker compose читает «KEY=   # комментарий» как значение «# комментарий», а python-dotenv —
    как пустое. Чтобы .env работал одинаково в обоих случаях, такие значения считаем пустыми.
    """
    load_dotenv(root / ".env", override=False)
    for key, value in os.environ.items():
        if value.lstrip().startswith("#"):
            os.environ[key] = ""


def read_yaml(path: Path) -> dict:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        raise ConfigError(f"Не найден файл {path}") from None
    except yaml.YAMLError as e:
        raise ConfigError(f"Ошибка YAML в {path}: {e}") from None


# ---------- данные компании ----------

def company_path(root: Path = ROOT) -> Path:
    """company.local.yaml (реальные данные), если он есть, иначе легенда."""
    local = root / "config" / "company.local.yaml"
    return local if local.exists() else root / "config" / "company.example.yaml"


def load_company(root: Path = ROOT) -> tuple[dict, str]:
    """Возвращает значения плейсхолдеров и имя файла, из которого они взяты."""
    path = company_path(root)
    return read_yaml(path), path.name


def render(text: str, company: dict, where: str = "") -> str:
    """Подставляет {{КЛЮЧ}}; неизвестный ключ — ConfigError с указанием файла."""
    def sub(m: re.Match) -> str:
        key = m.group(1)
        if key not in company:
            place = f" в {where}" if where else ""
            raise ConfigError(
                f"Неизвестный плейсхолдер {{{{{key}}}}}{place}: "
                f"добавьте ключ {key} в config/company.example.yaml (и в company.local.yaml, если он есть)"
            )
        return str(company[key])
    return PLACEHOLDER.sub(sub, text)


# ---------- front matter ----------

def split_front_matter(text: str) -> tuple[dict, str]:
    """Разделяет YAML front matter (--- ... ---) и тело файла."""
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            meta = yaml.safe_load(text[4:end]) or {}
            return meta, text[end + 5:].lstrip("\n")
    return {}, text


def strip_front_matter(text: str) -> str:
    return split_front_matter(text)[1]


# ---------- паспорта ассистентов ----------

RETRIEVAL_MODES = ("stuffing", "rag")


@dataclass
class Passport:
    id: str
    name: str
    title: str
    provider: str
    model: str
    temperature: float
    max_tokens: int
    prompt: str
    knowledge: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    conversation_starters: list[str] = field(default_factory=list)
    retrieval: str = "stuffing"   # stuffing — вся база в промпте; rag — только найденные фрагменты (app/rag.py)
    top_k: int = 8                # сколько фрагментов базы знаний подставлять в режиме rag


def load_assistants_config(root: Path = ROOT) -> dict:
    return read_yaml(root / "config" / "assistants.yaml")


def load_passports(root: Path = ROOT) -> dict[str, Passport]:
    """Паспорта всех ассистентов.

    Порядок приоритета для provider, model и retrieval: defaults → .env (LLM_PROVIDER, LLM_MODEL, RETRIEVAL)
    → значение в самом паспорте ассистента. Остальные поля: defaults → паспорт.
    """
    cfg = load_assistants_config(root)
    defaults = dict(cfg.get("defaults", {}))
    for key, env in (("provider", "LLM_PROVIDER"), ("model", "LLM_MODEL"), ("retrieval", "RETRIEVAL")):
        if os.getenv(env):
            defaults[key] = os.environ[env]

    passports = {}
    for aid, a in (cfg.get("assistants") or {}).items():
        merged = {**defaults, **a}
        try:
            passports[aid] = Passport(
                id=aid,
                name=merged["name"],
                title=merged.get("title", ""),
                provider=merged["provider"],
                model=merged["model"],
                temperature=float(merged["temperature"]),
                max_tokens=int(merged["max_tokens"]),
                prompt=merged["prompt"],
                knowledge=list(merged.get("knowledge", [])),
                tools=list(merged.get("tools", [])),
                conversation_starters=list(merged.get("conversation_starters", [])),
                retrieval=str(merged.get("retrieval", "stuffing")),
                top_k=int(merged.get("top_k", 8)),
            )
        except KeyError as e:
            raise ConfigError(f"config/assistants.yaml: у ассистента {aid} нет поля {e.args[0]}") from None
        if passports[aid].retrieval not in RETRIEVAL_MODES:
            raise ConfigError(f"{aid}: retrieval «{passports[aid].retrieval}», варианты: {', '.join(RETRIEVAL_MODES)}")
    return passports


def get_passport(assistant_id: str, root: Path = ROOT) -> Passport:
    passports = load_passports(root)
    if assistant_id not in passports:
        raise ConfigError(
            f"Неизвестный ассистент «{assistant_id}». Доступны: {', '.join(passports)}"
        )
    return passports[assistant_id]
