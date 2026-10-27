"""Тесты RAG (этап 5): чанкинг, фильтр по ассистенту, кеш эмбеддингов, подстановка в промпт — без вызовов API."""
import dataclasses
import json
import re
import zlib

import pytest

from app.chat import Conversation
from app.config import load_passports
from app.llm import LLMClient, Reply
from app.prompts import build_system_prompt
from app.rag import Retriever, chunk_text, cosine, rag_query
from app.store import Store

DIM = 256


class WordEmbedder:
    """Мешок слов: близки тексты с общими словами (по первым 5 буквам — грубая замена стемминга)."""

    model = "fake-words"

    def __init__(self):
        self.calls = []

    def embed(self, texts):
        self.calls.append(len(texts))
        vectors = []
        for text in texts:
            v = [0.0] * DIM
            for word in re.findall(r"\w{3,}", text.lower()):
                v[zlib.crc32(word[:5].encode()) % DIM] += 1.0
            vectors.append(v)
        return vectors


@pytest.fixture
def retriever(tmp_path):
    return Retriever(embedder=WordEmbedder(), cache_path=tmp_path / "rag_index.json")


def rag_passports():
    return {aid: dataclasses.replace(p, retrieval="rag", top_k=3) for aid, p in load_passports().items()}


def test_chunk_by_headings_with_breadcrumbs():
    text = "# Гарантия\nВступление\n## Сроки\nДва года\n### Мотор\nТри года\n## Исключения\nУдар о грунт"
    chunks = chunk_text(text, "service-warranty", "knowledge/service/warranty.md")
    assert [c.title for c in chunks] == ["Гарантия", "Гарантия › Сроки", "Гарантия › Сроки › Мотор",
                                         "Гарантия › Исключения", "Гарантия — перечень"]
    assert chunks[-1].text == "- Сроки\n- Исключения"  # у «Сроков» один подраздел — перечень не нужен
    assert chunks[2].content == "Гарантия › Сроки › Мотор\nТри года"
    assert all(c.source == "service-warranty" for c in chunks)


def test_long_section_split_by_paragraphs():
    text = "## Раздел\n" + "\n\n".join("абзац " * 40 for _ in range(5))
    chunks = chunk_text(text, "x", "x.md", max_chars=500)
    assert len(chunks) > 1
    assert all(c.title == "Раздел" for c in chunks)


def test_cosine():
    assert cosine([1, 0], [1, 0]) == pytest.approx(1.0)
    assert cosine([1, 0], [0, 1]) == 0.0
    assert cosine([0, 0], [1, 0]) == 0.0


def test_chunks_only_from_passport_files(retriever):
    passports = load_passports()
    hr_sources = {c.source for c in retriever.chunks(passports["hr"])}
    sales_sources = {c.source for c in retriever.chunks(passports["sales"])}
    assert "hr-vacancies" in hr_sources and "hr-vacancies" not in sales_sources
    assert "sales-faq" not in hr_sources


def test_search_finds_relevant_chunk(retriever):
    p = rag_passports()["service"]
    found = retriever.search(p, "Что не покрывается гарантией?")
    assert len(found) == 3
    assert any("Что не покрывается" in c.content for c, _ in found)


def test_embeddings_cached_on_disk(tmp_path):
    first = Retriever(embedder=WordEmbedder(), cache_path=tmp_path / "idx.json")
    p = rag_passports()["sales"]
    n = first.build(p)
    assert first.embedder.calls == [n]
    data = json.loads((tmp_path / "idx.json").read_text(encoding="utf-8"))
    assert data["model"] == "fake-words" and len(data["vectors"]) == n

    second = Retriever(embedder=WordEmbedder(), cache_path=tmp_path / "idx.json")
    second.search(p, "лизинг")
    assert second.embedder.calls == [1]  # только запрос — фрагменты взяты из кеша


def test_cache_of_other_model_ignored(tmp_path):
    (tmp_path / "idx.json").write_text(json.dumps({"model": "old", "vectors": {"x": [1.0]}}), encoding="utf-8")
    r = Retriever(embedder=WordEmbedder(), cache_path=tmp_path / "idx.json")
    n = r.build(rag_passports()["hr"])
    assert r.embedder.calls == [n]


def test_rag_query_uses_last_user_turns():
    history = [{"role": "user", "content": "Нужен катер"}, {"role": "assistant", "content": "Где ходить?"},
               {"role": "user", "content": "Ладога"}, {"role": "system", "content": "передан"},
               {"role": "user", "content": "да"}]
    assert rag_query(history, turns=2) == "Ладога\nда"


class RecordingLLM(LLMClient):
    def __init__(self, passport):
        super().__init__(passport.model, passport.temperature, passport.max_tokens)
        self.seen = []

    def chat(self, messages, tools=None, json_mode=False):
        self.seen.append(messages)
        return Reply(text="ответ")


def test_conversation_injects_found_chunks(retriever):
    passports = rag_passports()
    clients = []

    def factory(p):
        clients.append(RecordingLLM(p))
        return clients[-1]

    store = Store(":memory:")
    conv = Conversation("service", passports, client_factory=factory, store=store, retriever=retriever)
    conv.send("Что не покрывается гарантией?")
    system = clients[0].seen[0][0]["content"]
    assert system.startswith(build_system_prompt(passports["service"], with_knowledge=False))
    assert "<knowledge>" in system and "Что не покрывается" in system
    assert system.count("Источник:") == 3
    assert len(system) < len(build_system_prompt(passports["service"])) / 2
    assert all(s.startswith("service-") or s.startswith("common-") for s in conv.last_sources)
    store.close()


def test_stuffing_is_default(retriever):
    passports = load_passports()
    assert all(p.retrieval == "stuffing" for p in passports.values())
    conv = Conversation("sales", passports, client_factory=RecordingLLM, store=Store(":memory:"),
                        retriever=retriever)
    assert conv.system_prompt() == build_system_prompt(passports["sales"])
    assert retriever.embedder.calls == []


def test_retrieval_from_env(monkeypatch):
    monkeypatch.setenv("RETRIEVAL", "rag")
    assert all(p.retrieval == "rag" for p in load_passports().values())
    monkeypatch.setenv("RETRIEVAL", "vector")
    from app.config import ConfigError
    with pytest.raises(ConfigError):
        load_passports()
