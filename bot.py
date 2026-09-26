"""Тонкий слой Telegram-бота. Вся логика — в app.logic, отвечает через
inline-кнопки: callback_data всегда несёт реальный event_id из БД, поэтому
пагинация и повторные запросы не могут "разъехаться" с тем, что видит
пользователь (в отличие от старой схемы с вводом номера позиции)."""
import logging
import os

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes,
)

from . import logic

logger = logging.getLogger("bot")

PAGE_SIZE = 5


def _fmt_event(e: dict, with_desc: bool = False) -> str:
    price = "Бесплатно" if e["price_type"] in ("free", "registration") else (
        f"{int(e['price'])} ₽" if e["price"] is not None else "Платно"
    )
    lines = [f"*{e['title']}*"]
    if e["date_text"]:
        lines.append(f"🕐 {e['date_text']}")
    if e["place"]:
        lines.append(f"📍 {e['place']}")
    if e["age_group"]:
        lines.append(f"👥 {e['age_group']}")
    lines.append(f"💳 {price}")
    if with_desc and e["description"]:
        desc = e["description"]
        if len(desc) > 400:
            desc = desc[:400] + "…"
        lines.append("")
        lines.append(desc)
    if e["source_url"]:
        lines.append(f"Подробнее: {e['source_url']}")
    return "\n".join(lines)


def _user(update: Update):
    u = update.effective_user
    return ("telegram", str(u.id), u.full_name or u.username, str(update.effective_chat.id))


def _events_page_markup(evs: list[dict], page: int, has_more: bool) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(e["title"][:40], callback_data=f"detail:{e['id']}:{page}")]
            for e in evs]
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("◀️ Назад", callback_data=f"page:{page - 1}"))
    if has_more:
        nav.append(InlineKeyboardButton("Вперёд ▶️", callback_data=f"page:{page + 1}"))
    if nav:
        rows.append(nav)
    return InlineKeyboardMarkup(rows)


async def _render_events_page(page: int) -> tuple[str, InlineKeyboardMarkup]:
    offset = (page - 1) * PAGE_SIZE
    # запрашиваем на одну запись больше, чтобы понять, есть ли следующая страница
    evs = logic.list_events(limit=PAGE_SIZE + 1, offset=offset)
    has_more = len(evs) > PAGE_SIZE
    evs = evs[:PAGE_SIZE]
    if not evs:
        return ("Мероприятий не найдено.", None)
    lines = [f"Страница {page}. Выберите мероприятие:"]
    return ("\n".join(lines), _events_page_markup(evs, page, has_more))


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 Привет! Я бот мероприятий библиотек Зеленограда.\n\n"
        "/events — список мероприятий\n"
        "/search <запрос> — поиск\n"
        "/filters — фильтры\n"
        "/my — мои записи",
        parse_mode="Markdown",
    )


async def events_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    text, markup = await _render_events_page(1)
    await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")


async def search_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = " ".join(ctx.args) if ctx.args else ""
    if not q:
        await update.message.reply_text("Укажите запрос: /search мастер-класс")
        return
    evs = logic.list_events(query=q, limit=10)
    if not evs:
        await update.message.reply_text("Ничего не найдено.")
        return
    rows = [[InlineKeyboardButton(e["title"][:40], callback_data=f"detail:{e['id']}:search")]
            for e in evs]
    await update.message.reply_text(
        "Результаты поиска:", reply_markup=InlineKeyboardMarkup(rows)
    )


async def filters_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    fv = logic.filter_values()
    lines = ["Доступные фильтры (используйте в /search):", ""]
    lines.append("Места:")
    lines += [f"  • {p}" for p in fv["places"]]
    lines.append("\nВозраст:")
    lines += [f"  • {a}" for a in fv["age_groups"]]
    lines.append("\nЦена (текстом для поиска): бесплатно / платно / регистрация")
    await update.message.reply_text("\n".join(lines)[:4000])


async def my_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    plat, uid, _name, _chat = _user(update)
    evs = logic.my_events(plat, uid)
    if not evs:
        await update.message.reply_text("У вас пока нет записей.")
        return
    rows = [[InlineKeyboardButton(f"❌ Отменить: {e['title'][:35]}",
                                  callback_data=f"cancel:{e['id']}:my")]
            for e in evs]
    lines = ["Ваши записи:"] + [f"• {e['title']} — {e['date_text'] or ''}" for e in evs]
    await update.message.reply_text(
        "\n".join(lines), reply_markup=InlineKeyboardMarkup(rows)
    )


async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    parts = data.split(":")
    action = parts[0]

    if action == "page":
        page = int(parts[1])
        text, markup = await _render_events_page(page)
        await query.edit_message_text(text, reply_markup=markup, parse_mode="Markdown")
        return

    if action == "detail":
        event_id, origin = int(parts[1]), parts[2]
        e = logic.get_event(event_id)
        if not e:
            await query.edit_message_text("Мероприятие больше недоступно.")
            return
        rows = [[InlineKeyboardButton("✅ Записаться", callback_data=f"register:{event_id}:{origin}")],
                [InlineKeyboardButton("◀️ К списку", callback_data=(
                    f"page:{origin}" if origin not in ("search", "my") else "page:1"))]]
        await query.edit_message_text(
            _fmt_event(e, with_desc=True), reply_markup=InlineKeyboardMarkup(rows),
            parse_mode="Markdown",
        )
        return

    if action == "register":
        event_id, origin = int(parts[1]), parts[2]
        u = query.from_user
        plat, uid, name, chat = "telegram", str(u.id), (u.full_name or u.username), str(query.message.chat_id)
        res = logic.register(plat, uid, name, chat, event_id)
        if res["ok"]:
            await query.edit_message_text(
                f"✅ Вы записаны на:\n\n{_fmt_event(res['event'])}\n\nМы напомним за час до начала.",
                parse_mode="Markdown",
            )
        else:
            msg = {
                "already_registered": "Вы уже записаны на это мероприятие.",
                "no_capacity": "К сожалению, мест больше нет.",
                "event_past": "Это мероприятие уже прошло или уже началось — запись недоступна.",
                "event_not_found": "Мероприятие не найдено — возможно, его убрали с сайта.",
            }.get(res["code"], "Не удалось записаться.")
            await query.edit_message_text(msg)
        return

    if action == "cancel":
        event_id, _origin = int(parts[1]), parts[2]
        plat, uid = "telegram", str(query.from_user.id)
        res = logic.cancel(plat, uid, event_id)
        if res["ok"]:
            await query.edit_message_text("Запись отменена.")
        else:
            await query.edit_message_text("Не нашли активную запись на это мероприятие.")
        return


def build_app() -> Application:
    token = os.environ.get("TG_BOT")
    if not token:
        raise RuntimeError("TG_BOT is not set")
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("events", events_cmd))
    app.add_handler(CommandHandler("search", search_cmd))
    app.add_handler(CommandHandler("filters", filters_cmd))
    app.add_handler(CommandHandler("my", my_cmd))
    app.add_handler(CallbackQueryHandler(on_callback))
    return app


async def run_polling() -> None:
    app = build_app()
    await app.initialize()
    await app.start()
    logger.info("telegram bot polling started")
    await app.updater.start_polling()
    import asyncio
    stop = asyncio.Event()
    await stop.wait()
