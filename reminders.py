"""Отправка напоминаний. Канал выносим за интерфейс, чтобы легко
подключить Telegram сейчас и MAX позже."""
import asyncio
import logging
from datetime import datetime, timezone

from . import logic

logger = logging.getLogger("reminders")


async def _send_telegram(bot, chat_id: str, text: str) -> None:
    try:
        await bot.send_message(chat_id=chat_id, text=text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("telegram reminder send failed for %s: %s", chat_id, exc)


def _build_message(reg: dict) -> str:
    return (
        "⏰ Напоминание о мероприятии\n\n"
        f"Через час начнётся:\n{reg['title']}\n\n"
        f"🕐 {reg.get('date_text') or reg.get('starts_at')}\n"
        + (f"📍 {reg['place']}\n" if reg.get("place") else "")
        + "\nДо встречи!"
    )


async def run_reminder_pass(bot=None) -> int:
    """Один проход: найти due-записи и разослать напоминания.

    bot — объект telegram.Bot (или совместимый), если Telegram доступен.
    Возвращает число обработанных напоминаний.
    """
    due = logic.due_reminders()
    sent = 0
    now = datetime.now(timezone.utc).isoformat()
    for reg in due:
        text = _build_message(reg)
        delivered = False
        if bot and reg.get("chat_id"):
            await _send_telegram(bot, reg["chat_id"], text)
            delivered = True
        # MAX-канал добавим здесь же, когда появится токен.
        if delivered:
            logic.mark_reminder_sent(reg["reg_id"], now)
            sent += 1
        else:
            # не помечаем — попробуем ещё раз через полчаса
            logger.info("no channel for reminder reg_id=%s", reg["reg_id"])
    return sent


async def reminder_loop(bot_factory, interval_seconds: int = 30 * 60) -> None:
    """Фоновый цикл: каждые interval_seconds секунд делает проход."""
    while True:
        try:
            bot = bot_factory() if bot_factory else None
            n = await run_reminder_pass(bot)
            if n:
                logger.info("reminders sent: %d", n)
        except Exception as exc:  # noqa: BLE001
            logger.exception("reminder pass failed: %s", exc)
        await asyncio.sleep(interval_seconds)