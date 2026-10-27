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

## Чат с ассистентом в терминале
```bash
cp .env.example .env                 # и впишите OPENAI_API_KEY
source .venv/bin/activate
python -m app.channels.cli --assistant sales    # sales | service | hr
pytest                               # тесты без обращения к LLM
```
В чате: `/switch service` — передать разговор Боцману (история сохраняется), `/reset` — начать заново, `/prompt` — размер системного промпта, `/cards` — карточки передачи этого диалога, `/exit` — выход.

Карточки передачи сохраняются в `data/app.sqlite3`. Если в `.env` заданы `TG_CHAT_<ОТДЕЛ>` и токен бота (`TG_NOTIFY_TOKEN`), карточка уходит в Telegram-чат отдела, иначе печатается в консоль.

## Единое окно: сайт и Telegram
Посетитель пишет в одно окно, а отвечает нужный ассистент: роутер (`prompts/router.md`) на каждую реплику решает, оставить разговор у текущего или передать Лоцману, Боцману или Штурману. История общая и хранится в `data/app.sqlite3`.

```bash
python -m app.channels.cli --window        # единое окно в терминале
python -m app.channels.web                 # http://127.0.0.1:8080 — демо-страница с чат-виджетом
python -m app.channels.telegram            # Telegram-бот единого окна (TG_TOKEN_WINDOW в .env)
python -m app.channels.telegram --assistant hr   # бот одного ассистента (TG_TOKEN_HR)
python -m eval.router --repeat 3           # точность роутера на tests/cases/router.yaml
```

**Виджет на сайт** — одна строка перед `</body>`:
```html
<script src="https://<сервер>/static/widget.js" data-api="https://<сервер>" defer></script>
```
Домен сайта добавьте в `WEB_ALLOWED_ORIGINS`. Кнопки-подсказки, ассистент по умолчанию и текст при сбое — в `config/assistants.yaml` → `window`.

**Telegram:** создайте бота у @BotFather (`/newbot`), впишите токен в `.env` и запустите. Команды: `/start`, `/reset`. Файлы и фото бот не сохраняет.

Логи маскируют телефоны и e-mail; текст реплик посетителей в лог не пишется.

## Оценка качества
```bash
python -m eval.run                          # все ассистенты: кейсы из tests/cases/ + LLM-судья
python -m eval.run --assistant hr --case H05
python -m eval.run --repeat 3              # каждый кейс 3 раза: засчитан, только если прошел все
python -m eval.compare до.json после.json   # разница «до и после» правки промпта
```
Отчеты — `build/eval_<ассистент>.md` (таблица кейсов, процент, провалы с объяснениями судьи) и `.json` для сравнения до и после правок. Приемка: не меньше 90 % кейсов и 100 % кейсов `injection` и `pd`. По умолчанию судья — `gpt-4.1-mini` (учебный режим): он строже и дает ложные провалы в части кейсов, см. CLAUDE.md. Для приемки перед продом — `--model gpt-4.1`.

## Учебная и реальная версии
- Учебная: данные из `config/company.example.yaml` (вымышленная компания).
- Реальная: создайте `config/company.local.yaml` с теми же ключами и реальными значениями — он в `.gitignore` и подхватывается автоматически.
- `config/forbidden.local.txt` — слова-маркеры реального заказчика для проверки обезличивания (тоже в `.gitignore`).

## Вайб-кодинг
Откройте папку в Claude Code и начните с промпта «Этап 0» из `docs/START_PROMPTS.md`.
