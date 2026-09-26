# Афиша библиотек Зеленограда — Telegram-бот

FastAPI + фоновый парсер zelkultura.ru + Telegram-бот на inline-кнопках +
напоминания за час до мероприятия. Хранилище — SQLite (файл на диске).

## Деплой на свой сервер (Docker)

1. Скопируйте этот репозиторий на сервер (`git clone ...` или `scp`).
2. Создайте `.env` на основе примера и впишите токен бота:
   ```bash
   cp .env.example .env
   nano .env   # TELEGRAM_BOT_TOKEN=...
   ```
3. Запустите:
   ```bash
   docker compose up -d --build
   ```
4. Проверьте, что всё поднялось:
   ```bash
   curl http://localhost:8000/health
   ```
   В ответе будет `last_parser_run` — статус последнего парсинга сайта.

Данные (SQLite) хранятся в docker-volume `data`, переживают перезапуск и
пересборку контейнера.

## Обновление кода на сервере

```bash
git pull
docker compose up -d --build
```

## Локальный запуск без Docker (для отладки)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN=...
export DATA_DIR=./data
uvicorn app.main:app --reload
```

## Известные ограничения / что проверить после первого запуска

- Реальный ключ характеристики с датой мероприятия в Tilda-API уточнён как
  `"Дата и время"` — если на сайте он называется иначе, поправьте
  `_DATE_CHAR_KEYS` в `app/parser.py` (проверить: `GET /refresh`, затем
  посмотреть `raw_json` события в БД).
- Для работы с часовым поясом Europe/Moscow базовый образ включает
  `tzdata` (см. `Dockerfile`) — не убирайте эту строку, если будете менять
  базовый образ.
