# Образ ассистентов (этап 6): один и тот же для веб-виджета и Telegram-бота — отличается команда запуска.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DB_PATH=/app/data/app.sqlite3 \
    WEB_HOST=0.0.0.0 \
    WEB_PORT=8080

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

# Только то, что нужно для работы. Секреты (.env) и реальные данные (config/*.local.*)
# в образ не попадают — см. .dockerignore.
COPY app app
COPY config config
COPY prompts prompts
COPY knowledge knowledge
COPY schemas schemas

# Не root: если в приложении найдется уязвимость, у взломщика будет меньше прав
# chmod: права файлов приходят с компьютера разработчика, папки могут быть закрыты для других
RUN useradd --create-home --uid 1000 bot && mkdir -p /app/data && chown bot /app/data && chmod -R a+rX /app
USER bot

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)"
CMD ["python", "-m", "app.channels.web"]
