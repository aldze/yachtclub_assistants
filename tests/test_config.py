"""Тесты app/config.py: подстановка плейсхолдеров, выбор файла компании, паспорта."""
import shutil

import pytest

from app.config import (
    ROOT, ConfigError, company_path, get_passport, load_company, load_passports,
    render, split_front_matter,
)


@pytest.fixture
def project(tmp_path):
    """Копия config/ во временной папке — чтобы не трогать настоящие файлы."""
    (tmp_path / "config").mkdir()
    for name in ("assistants.yaml", "company.example.yaml"):
        shutil.copy(ROOT / "config" / name, tmp_path / "config" / name)
    return tmp_path


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)


# ---------- render ----------

def test_render_substitutes_known_keys():
    assert render("Компания {{COMPANY}}, {{YEARS}}", {"COMPANY": "Бриз", "YEARS": "10 лет"}) == "Компания Бриз, 10 лет"


def test_render_unknown_key_names_key_and_file():
    with pytest.raises(ConfigError) as e:
        render("Звоните {{NO_SUCH_KEY}}", {"COMPANY": "Бриз"}, "prompts/sales.md")
    message = str(e.value)
    assert "NO_SUCH_KEY" in message and "prompts/sales.md" in message


def test_render_leaves_text_without_placeholders():
    assert render("Просто текст {одинарные скобки}", {}) == "Просто текст {одинарные скобки}"


# ---------- company.local / example ----------

def test_example_used_when_no_local(project):
    assert company_path(project).name == "company.example.yaml"
    company, source = load_company(project)
    assert source == "company.example.yaml"
    assert "COMPANY" in company


def test_local_has_priority(project):
    (project / "config" / "company.local.yaml").write_text('COMPANY: "Тестовая верфь"\n', encoding="utf-8")
    company, source = load_company(project)
    assert source == "company.local.yaml"
    assert company["COMPANY"] == "Тестовая верфь"


# ---------- front matter ----------

def test_split_front_matter():
    meta, body = split_front_matter("---\nid: x-faq\nassistants: [sales]\n---\n\n# Заголовок\n")
    assert meta == {"id": "x-faq", "assistants": ["sales"]}
    assert body == "# Заголовок\n"


def test_split_without_front_matter():
    assert split_front_matter("# Просто файл\n") == ({}, "# Просто файл\n")


# ---------- паспорта ----------

def test_all_three_passports_loaded():
    passports = load_passports()
    assert set(passports) == {"sales", "service", "hr"}
    assert passports["sales"].name == "Лоцман"
    assert passports["service"].temperature == 0.3


def test_defaults_merged_into_passport():
    sales = get_passport("sales")
    assert sales.provider == "openai"
    assert sales.max_tokens == 800  # задано только в defaults
    assert sales.temperature == 0.4


def test_env_overrides_defaults(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "test-model")
    assert get_passport("hr").model == "test-model"


def test_passport_value_beats_env(project, monkeypatch):
    path = project / "config" / "assistants.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace(
        '    name: "Лоцман"', '    name: "Лоцман"\n    model: own-model'), encoding="utf-8")
    monkeypatch.setenv("LLM_MODEL", "env-model")
    assert get_passport("sales", project).model == "own-model"
    assert get_passport("hr", project).model == "env-model"


def test_unknown_assistant():
    with pytest.raises(ConfigError, match="Доступны"):
        get_passport("captain")
