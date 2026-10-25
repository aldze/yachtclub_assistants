# Нейроассистенты для компании по продаже яхт

Три LLM-ассистента для дилера яхт и катеров: **Лоцман** (продажи), **Боцман** (сервис и постпродажа), **Штурман** (HR). Учебный проект курса «Промпт-инжиниринг».

## Структура
```
CLAUDE.md                  контекст и правила для Claude Code — начните с него
docs/SPEC.md               ТЗ
docs/START_PROMPTS.md      промпты для Claude Code по этапам
docs/GIT_TUTORIAL.md       мини-учебник по git и GitHub на примере проекта
docs/IMPORT_NOCODE.md      импорт в Custom GPTs, Яндекс AI Studio, SMAIPL
docs/document_template.md  шаблон Word-документа проекта
prompts/                   системные промпты (sales, service, hr)
knowledge/                 база знаний: common, sales, service, hr
tests/cases/               тест-кейсы (по 12 на ассистента)
schemas/                   JSON-схема карточки передачи между отделами
config/assistants.yaml     паспорта ассистентов
config/company.example.yaml  легенда компании (значения плейсхолдеров {{КЛЮЧ}})
tools/build_docx.py        проверки + рендер + сборка Word-документа
tools/update_toc.py        заполнение оглавления через LibreOffice (необязательно)
```

## Быстрый старт
```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt      # + pandoc для сборки Word
python tools/build_docx.py --check   # длина промптов, плейсхолдеры, обезличивание
python tools/build_docx.py           # build/rendered/ + build/Нейроассистенты_яхты.docx
```

## Учебная и реальная версии
- Учебная: данные из `config/company.example.yaml` (вымышленная компания).
- Реальная: создайте `config/company.local.yaml` с теми же ключами и реальными значениями — он в `.gitignore` и подхватывается автоматически.
- `config/forbidden.local.txt` — слова-маркеры реального заказчика для проверки обезличивания (тоже в `.gitignore`).

## Вайб-кодинг
Откройте папку в Claude Code и начните с промпта «Этап 0» из `docs/START_PROMPTS.md`.
