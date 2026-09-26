"""Точка входа: FastAPI + фоновые задачи (бот, напоминания, парсер).

- GET  /health         — статус и счётчики БД
- POST /api            — JSON-конверт action/payload (для MAX / Mini App)
- GET  /refresh        — принудительный парсинг источника
"""
import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from . import db, logic, parser, reminders

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("main")

REMINDER_INTERVAL = int(os.environ.get("REMINDER_INTERVAL_SECONDS", 30 * 60))


def _make_bot():
    """Возвращает telegram.Bot, если настроен токен, иначе None."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        return None
    from telegram import Bot
    return Bot(token)


async def _bot_task() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.info("TELEGRAM_BOT_TOKEN not set — bot disabled")
        return
    try:
        from . import bot as botmod
        await botmod.run_polling()
    except Exception:  # noqa: BLE001
        logger.exception("bot task failed")


async def _parser_loop(interval_seconds: int = 3600) -> None:
    while True:
        try:
            res = parser.run()
            logger.info("parser run: %s", res)
        except Exception:  # noqa: BLE001
            logger.exception("parser loop failed")
        await asyncio.sleep(interval_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    # первичная загрузка данных
    try:
        res = parser.run()
        logger.info("initial parse: %s", res)
    except Exception:  # noqa: BLE001
        logger.exception("initial parse failed")

    tasks = []
    tasks.append(asyncio.create_task(_parser_loop()))
    tasks.append(asyncio.create_task(reminders.reminder_loop(_make_bot, REMINDER_INTERVAL)))
    tasks.append(asyncio.create_task(_bot_task()))
    logger.info("background tasks started")
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(lifespan=lifespan)


@app.get("/health")
def health():
    conn = db.get_conn()
    events = conn.execute("SELECT COUNT(*) c FROM events WHERE is_active=1").fetchone()["c"]
    regs = conn.execute("SELECT COUNT(*) c FROM registrations WHERE status='active'").fetchone()["c"]
    last_run = parser.last_run_info()
    return {
        "ok": True,
        "events": events,
        "active_registrations": regs,
        "last_parser_run": last_run,
    }


@app.get("/refresh")
def refresh():
    res = parser.run()
    return JSONResponse(res)


@app.post("/api")
async def api(body: dict):
    action = body.get("action")
    payload = body.get("payload") or {}
    try:
        if action == "events.list":
            return {"ok": True, "data": logic.list_events(
                query=payload.get("query"),
                filters=payload.get("filters"),
                limit=payload.get("limit", 20),
                offset=payload.get("offset", 0),
            )}
        if action == "events.detail":
            e = logic.get_event(payload.get("event_id"))
            return {"ok": e is not None, "data": e}
        if action == "events.filters":
            return {"ok": True, "data": logic.filter_values()}
        if action == "registrations.create":
            u = payload.get("user") or {}
            res = logic.register(
                platform=u.get("platform", "api"),
                platform_user_id=str(u.get("platform_user_id")),
                name=u.get("name"),
                chat_id=u.get("chat_id"),
                event_id=int(payload["event_id"]),
            )
            return {"ok": res["ok"], "code": res.get("code"), "data": res}
        if action == "registrations.check":
            u = payload.get("user") or {}
            evs = logic.my_events(u.get("platform", "api"), str(u.get("platform_user_id")))
            return {"ok": True, "data": {"registered_event_ids": [e["id"] for e in evs]}}
        if action == "users.my_events":
            u = payload.get("user") or {}
            return {"ok": True, "data": logic.my_events(u.get("platform", "api"), str(u.get("platform_user_id")))}
        if action == "registrations.cancel":
            u = payload.get("user") or {}
            res = logic.cancel(u.get("platform", "api"), str(u.get("platform_user_id")), int(payload["event_id"]))
            return {"ok": res["ok"], "code": res.get("code"), "data": res}
        return {"ok": False, "error": f"unknown action {action!r}"}
    except Exception as exc:  # noqa: BLE001
        logger.exception("api error action=%s", action)
        return {"ok": False, "error": str(exc)}