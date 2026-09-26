"""Парсер мероприятий zelkultura.ru через Tilda API + нормализация в БД."""
import json
import re
import sqlite3
import requests
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from . import db

SOURCE_URL = "https://zelkultura.ru/events"
API_URL = (
    "https://store.tildaapi.com/api/getproductslist/"
    "?storepartuid=367159377881&recid=475890235"
    "&getparts=true&getoptions=true&slice=1&size=200&flag_root=withroot"
)

HEADERS = {
    "Referer": SOURCE_URL,
    "Origin": "https://zelkultura.ru",
    "User-Agent": "Mozilla/5.0",
}

# Реальные события идут по московскому времени (Зеленоград = Москва).
# Даты на сайте не содержат смещения, поэтому фиксируем зону явно, а не
# полагаемся на системный TZ контейнера (обычно UTC, что даёт трёхчасовой
# сдвиг при наивной .astimezone()).
EVENT_TZ = ZoneInfo("Europe/Moscow")

# Ключи характеристик Tilda, где реально может лежать дата/время мероприятия.
# Порядок важен: проверяем сначала самые специфичные.
_DATE_CHAR_KEYS = (
    "Дата и время", "Дата проведения", "Дата", "Когда", "Время проведения",
)

# Русские месяцы для парсинга дат вида "25 сентября в 11:00"
_MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5,
    "июня": 6, "июля": 7, "августа": 8, "сентября": 9, "октября": 10,
    "ноября": 11, "декабря": 12,
}

_DATE_RE = re.compile(r"(\d{1,2})\s+([а-яё]+)(?:.{0,15}?(\d{1,2})[.:](\d{2}))?", re.I)


def _strip_html(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</(p|div|li|ul|ol|h\d)>", "\n", text, flags=re.I)
    text = re.sub(r"<li[^>]*>", "• ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    import html
    text = html.unescape(text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _find_date_candidates(text: str) -> list[tuple[int, str, str | None, str | None]]:
    """Возвращает все совпадения даты в тексте: (день, месяц-слово, час, минута)."""
    if not text:
        return []
    s = text.lower().replace("&nbsp;", " ")
    s = re.sub(r"<[^>]+>", " ", s)
    out = []
    for m in _DATE_RE.finditer(s):
        day = int(m.group(1))
        mon_word = m.group(2)
        if mon_word not in _MONTHS:
            continue
        out.append((day, mon_word, m.group(3), m.group(4)))
    return out


def parse_starts_at(date_text: str, year: int | None = None) -> str | None:
    """Best-effort ISO datetime (UTC) из строки с датой мероприятия.

    Возвращает None, если распознать не удалось (тогда событие нельзя
    использовать для напоминаний, но в списке остаётся).
    """
    candidates = _find_date_candidates(date_text)
    if not candidates:
        return None
    if year is None:
        year = datetime.now(EVENT_TZ).year

    # Берём первое совпадение с явным временем (наиболее вероятно — реальная
    # дата мероприятия, а не случайное число в тексте описания); если такого
    # нет — берём первое совпадение вообще.
    day, mon_word, hh, mm = next((c for c in candidates if c[2]), candidates[0])
    mon = _MONTHS[mon_word]
    hour, minute = (int(hh), int(mm)) if hh else (0, 0)
    try:
        naive = datetime(year, mon, day, hour, minute)
    except ValueError:
        return None
    # Явно локализуем как московское время, затем переводим в UTC —
    # никакой зависимости от системного TZ контейнера.
    localized = naive.replace(tzinfo=EVENT_TZ)
    return localized.astimezone(timezone.utc).isoformat()


def fetch_products() -> list[dict]:
    resp = requests.get(API_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data.get("products", [])


def _normalize(p: dict) -> dict:
    chars = {(c.get("title") or "").strip(): c.get("value") for c in (p.get("characteristics") or [])}
    price_type = ""
    mark = (p.get("mark") or "").strip().lower()
    if "платно" in mark or p.get("price") not in (None, ""):
        price_type = "paid"
    elif "бесплатно" in mark or "свободн" in mark:
        price_type = "free"
    elif "регистрац" in mark:
        price_type = "registration"

    gallery = p.get("gallery")
    image_url = None
    try:
        if isinstance(gallery, str) and gallery.strip():
            imgs = json.loads(gallery)
            if imgs and isinstance(imgs, list) and isinstance(imgs[0], dict):
                image_url = imgs[0].get("img")
    except Exception:
        image_url = None
    if not image_url:
        eds = p.get("editions") or []
        if eds and isinstance(eds[0], dict):
            image_url = eds[0].get("img")

    # Дата мероприятия должна браться из характеристики карточки, а не
    # выдёргиваться регуляркой из произвольного описания — там могут
    # встречаться посторонние числа с названиями месяцев.
    date_text = None
    for key in _DATE_CHAR_KEYS:
        if chars.get(key):
            date_text = chars[key]
            break
    if not date_text:
        # запасной вариант: ищем в описании/тексте, но это менее надёжно
        date_text = _strip_html(p.get("descr") or "") or _strip_html(p.get("text") or "")

    return {
        "source_id": str(p.get("uid")),
        "title": (p.get("title") or "").strip(),
        "description": _strip_html(p.get("text") or ""),
        "date_text": date_text,
        "starts_at": parse_starts_at(date_text),
        "place": chars.get("Адрес"),
        "age_group": chars.get("Возраст"),
        "price": p.get("price"),
        "price_type": price_type,
        "capacity": None,
        "source_url": p.get("url"),
        "image_url": image_url,
        "raw_json": json.dumps(p, ensure_ascii=False),
    }


def upsert(products: list[dict]) -> int:
    conn = db.get_conn()
    now = datetime.now(timezone.utc).isoformat()
    # ids "видимых на сайте" считаем по ВСЕМ полученным товарам, даже если
    # у части нет title и мы их не вставляем — иначе такие товары ошибочно
    # выключают из активных уже существующие записи с тем же source_id.
    seen_ids = [str(p.get("uid")) for p in products if p.get("uid") is not None]
    written = 0
    for p in products:
        n = _normalize(p)
        if not n["title"]:
            continue
        written += 1
        conn.execute(
            """
            INSERT INTO events (source_id, title, description, starts_at, date_text,
                place, age_group, price, price_type, capacity, source_url, image_url,
                raw_json, is_active, updated_at)
            VALUES (:source_id, :title, :description, :starts_at, :date_text,
                :place, :age_group, :price, :price_type, :capacity, :source_url,
                :image_url, :raw_json, 1, :updated_at)
            ON CONFLICT(source_id) DO UPDATE SET
                title=excluded.title, description=excluded.description,
                starts_at=excluded.starts_at, date_text=excluded.date_text,
                place=excluded.place, age_group=excluded.age_group,
                price=excluded.price, price_type=excluded.price_type,
                capacity=excluded.capacity, source_url=excluded.source_url,
                image_url=excluded.image_url, raw_json=excluded.raw_json,
                is_active=1, updated_at=excluded.updated_at
            """,
            {**n, "updated_at": now},
        )
    # помечаем события, которых больше нет в источнике, неактивными
    if seen_ids:
        marks = ",".join("?" for _ in seen_ids)
        conn.execute(f"UPDATE events SET is_active=0 WHERE source_id NOT IN ({marks})", seen_ids)
    conn.commit()
    return written


def run() -> dict:
    """Полный цикл парсинга. Пишет строку в parser_runs и возвращает сводку."""
    conn = db.get_conn()
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        "INSERT INTO parser_runs (started_at, status) VALUES (?, 'running')", (now,)
    )
    run_id = cur.lastrowid
    try:
        products = fetch_products()
        n = upsert(products)
        conn.execute(
            "UPDATE parser_runs SET finished_at=?, status='ok', events_fetched=? WHERE id=?",
            (datetime.now(timezone.utc).isoformat(), n, run_id),
        )
        conn.commit()
        return {"ok": True, "fetched": n, "run_id": run_id}
    except Exception as exc:  # noqa: BLE001
        conn.execute(
            "UPDATE parser_runs SET finished_at=?, status='error', error=? WHERE id=?",
            (datetime.now(timezone.utc).isoformat(), str(exc)[:500], run_id),
        )
        conn.commit()
        return {"ok": False, "error": str(exc), "run_id": run_id}


def last_run_info() -> dict | None:
    """Для /health: статус последнего запуска парсера."""
    conn = db.get_conn()
    row = conn.execute(
        "SELECT started_at, finished_at, status, events_fetched, error "
        "FROM parser_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None
