"""RAG (этап 5): вместо всей базы знаний в промпт попадают только фрагменты, близкие к вопросу.

Включается в паспорте (`retrieval: rag`, `top_k`) или для всех через .env (`RETRIEVAL=rag`).

1. Чанкинг: каждый файл базы знаний режется по заголовкам `#`–`###`; у фрагмента — цепочка
   заголовков («Гарантия › Что не покрывается») и метаданные: файл, id источника.
   Длинный раздел делится по абзацам, чтобы фрагмент не превышал MAX_CHUNK знаков.
   Плюс перечни: у раздела с подразделами — фрагмент со списком их названий, для обзорных
   вопросов («какие есть вакансии?»).
2. Эмбеддинги: OpenAI `text-embedding-3-small` (EMBED_MODEL в .env). Векторы кешируются
   в data/rag_index.json по хешу текста — повторно оплачиваются только измененные фрагменты.
3. Поиск: косинусная близость запроса к фрагментам из файлов паспорта, top-k.
   Запрос — последние реплики посетителя: одно «да» без контекста ничего не найдет.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.config import ROOT, Passport, load_company
from app.prompts import knowledge_sources
from app.store import db_path

log = logging.getLogger(__name__)

HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*$")
MAX_CHUNK = 1500          # знаков во фрагменте; длиннее — делим по абзацам
QUERY_TURNS = 3           # сколько последних реплик посетителя идет в поисковый запрос
DEFAULT_EMBED_MODEL = "text-embedding-3-small"


@dataclass
class Chunk:
    source: str      # id источника из front matter (sales-faq)
    path: str        # файл (knowledge/sales/faq.md)
    title: str       # цепочка заголовков
    text: str

    @property
    def content(self) -> str:
        """Что эмбеддим и что видит модель: заголовок дает фрагменту смысл («Сколько стоит лодка?»)."""
        return f"{self.title}\n{self.text}" if self.title else self.text

    @property
    def key(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()


def _split_long(text: str, limit: int) -> list[str]:
    parts, current = [], ""
    for para in re.split(r"\n\s*\n", text):
        if current and len(current) + len(para) + 2 > limit:
            parts.append(current)
            current = para
        else:
            current = f"{current}\n\n{para}" if current else para
    if current:
        parts.append(current)
    return parts


def chunk_text(text: str, source: str, path: str, max_chars: int = MAX_CHUNK) -> list[Chunk]:
    """Режет markdown по заголовкам до третьего уровня включительно."""
    chunks: list[Chunk] = []
    stack: list[tuple[int, str]] = []
    lines: list[str] = []

    def flush() -> None:
        body = "\n".join(lines).strip()
        lines.clear()
        if not body:
            return
        title = " › ".join(t for _, t in stack)
        for part in _split_long(body, max_chars):
            chunks.append(Chunk(source, path, title, part))

    children: dict[str, list[str]] = {}  # раздел (цепочка заголовков) → его подразделы
    for line in text.splitlines():
        m = HEADING.match(line)
        if m:
            flush()
            level = len(m.group(1))
            stack = [(lv, t) for lv, t in stack if lv < level]
            if stack:
                children.setdefault(" › ".join(t for _, t in stack), []).append(m.group(2))
            stack.append((level, m.group(2)))
        else:
            lines.append(line)
    flush()
    # Перечень подразделов — отдельный фрагмент. Без него на вопрос «какие есть вакансии?»
    # не находится ни одна вакансия: каждая лежит в своем фрагменте и на вопрос не похожа.
    for parent, items in children.items():
        if len(items) > 1:
            chunks.append(Chunk(source, path, f"{parent} — перечень", "\n".join(f"- {t}" for t in items)))
    return chunks


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class Embedder(Protocol):
    model: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def default_embedder() -> Embedder:
    from app.llm.openai_compat import OpenAIEmbedder
    return OpenAIEmbedder(os.getenv("EMBED_MODEL") or DEFAULT_EMBED_MODEL)


class Retriever:
    """Индекс фрагментов базы знаний. Один на процесс: его делят все сессии канала."""

    def __init__(self, embedder: Embedder | None = None, company: dict | None = None,
                 root: Path = ROOT, cache_path: Path | str | None = None):
        self._embedder = embedder
        self.company = company if company is not None else load_company(root)[0]
        self.root = root
        # кеш лежит рядом с базой: в Docker это том data/, он переживает пересборку образа
        self.cache_path = Path(cache_path) if cache_path else db_path().with_name("rag_index.json")
        self._vectors: dict[str, list[float]] | None = None
        self._lock = threading.Lock()

    @property
    def embedder(self) -> Embedder:
        if self._embedder is None:
            self._embedder = default_embedder()
        return self._embedder

    # ---------- кеш векторов ----------

    def _load_cache(self) -> dict[str, list[float]]:
        if self._vectors is None:
            self._vectors = {}
            if self.cache_path != Path(":memory:") and self.cache_path.exists():
                data = json.loads(self.cache_path.read_text(encoding="utf-8"))
                if data.get("model") == self.embedder.model:
                    self._vectors = data.get("vectors", {})
        return self._vectors

    def _save_cache(self) -> None:
        if self.cache_path == Path(":memory:"):
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"model": self.embedder.model, "vectors": self._vectors}), encoding="utf-8")
        tmp.replace(self.cache_path)

    def _ensure(self, chunks: list[Chunk]) -> dict[str, list[float]]:
        with self._lock:
            vectors = self._load_cache()
            missing = {c.key: c.content for c in chunks if c.key not in vectors}
            if missing:
                log.info("RAG: эмбеддинги для %s новых фрагментов (%s)", len(missing), self.embedder.model)
                keys = list(missing)
                for key, vec in zip(keys, self.embedder.embed([missing[k] for k in keys])):
                    vectors[key] = vec
                self._save_cache()
            return vectors

    # ---------- поиск ----------

    def chunks(self, passport: Passport) -> list[Chunk]:
        """Фрагменты из файлов паспорта (front matter `assistants` проверяется при чтении)."""
        result = []
        for rel, source_id, text in knowledge_sources(passport, self.company, self.root):
            result.extend(chunk_text(text, source_id, rel))
        return result

    def build(self, passport: Passport) -> int:
        """Заранее считает эмбеддинги базы ассистента; возвращает число фрагментов."""
        chunks = self.chunks(passport)
        self._ensure(chunks)
        return len(chunks)

    def search(self, passport: Passport, query: str, k: int | None = None) -> list[tuple[Chunk, float]]:
        chunks = self.chunks(passport)
        if not chunks or not query.strip():
            return []
        vectors = self._ensure(chunks)
        q = self.embedder.embed([query])[0]
        scored = sorted(((c, cosine(q, vectors[c.key])) for c in chunks), key=lambda x: -x[1])
        return scored[: k or passport.top_k]

    def knowledge_block(self, passport: Passport, query: str) -> tuple[str, list[Chunk]]:
        """Блок <knowledge> из найденных фрагментов в том же формате, что и при context stuffing."""
        found = [c for c, _ in self.search(passport, query)]
        parts = [f"Источник: {c.source}\n\n{c.content}" for c in found]
        return "<knowledge>\n" + "\n\n---\n\n".join(parts) + "\n</knowledge>", found


def rag_query(history: list[dict], turns: int = QUERY_TURNS) -> str:
    """Поисковый запрос: последние реплики посетителя, самая свежая — последней."""
    texts = [m["content"] for m in history if m.get("role") == "user" and m.get("content")]
    return "\n".join(texts[-turns:])


_shared: Retriever | None = None
_shared_lock = threading.Lock()


def shared_retriever() -> Retriever:
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = Retriever()
        return _shared
