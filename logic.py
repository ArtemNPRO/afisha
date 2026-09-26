"""Бизнес-логика: список, поиск, фильтры, детали, запись, отмена."""
import sqlite3
from datetime import datetime, timezone, timedelta

from . import db


def _event_row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "source_id": row["source_id"],
        "title": row["title"],
        "description": row["description"],
        "starts_at": row["starts_at"],
        "date_text": row["date_text"],
        "place": row["place"],
        "age_group": row["age_group"],
        "price": row["price"],
        "price_type": row["price_type"],
        "capacity": row["capacity"],
        "source_url": row["source_url"],
        "image_url": row["image_url"],
    }


def list_events(query: str | None = None, filters: dict | None = None,
                limit: int = 20, offset: int = 0,
                include_past: bool = False) -> list[dict]:
    conn = db.get_conn()
    where = ["is_active = 1"]
    params: list = []

    if not include_past:
        # Не показываем то, на что уже физически нельзя записаться:
        # событие либо без даты (нельзя определить), либо ещё не началось.
        where.append("(starts_at IS NULL OR starts_at > ?)")
        params.append(datetime.now(timezone.utc).isoformat())

    if query and query.strip():
        where.append("(title LIKE ? OR description LIKE ? OR place LIKE ?)")
        like = f"%{query.strip()}%"
        params += [like, like, like]

    filters = filters or {}
    if filters.get("price_type"):
        where.append("price_type = ?")
        params.append(filters["price_type"])
    if filters.get("place"):
        where.append("place = ?")
        params.append(filters["place"])
    if filters.get("age_group"):
        where.append("age_group = ?")
        params.append(filters["age_group"])

    # сортировка: сначала с известной датой и ближайшие, затем без даты
    order = (
        "ORDER BY (starts_at IS NULL) ASC, starts_at ASC, id ASC "
        "LIMIT ? OFFSET ?"
    )
    params += [limit, offset]
    rows = conn.execute(
        f"SELECT * FROM events WHERE {' AND '.join(where)} {order}", params
    ).fetchall()
    return [_event_row_to_dict(r) for r in rows]


def get_event(event_id: int) -> dict | None:
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    return _event_row_to_dict(row) if row else None


def filter_values() -> dict:
    conn = db.get_conn()
    out = {"places": [], "age_groups": [], "price_types": []}
    for r in conn.execute(
        "SELECT DISTINCT place FROM events WHERE is_active=1 AND place IS NOT NULL ORDER BY place"
    ).fetchall():
        out["places"].append(r["place"])
    for r in conn.execute(
        "SELECT DISTINCT age_group FROM events WHERE is_active=1 AND age_group IS NOT NULL ORDER BY age_group"
    ).fetchall():
        out["age_groups"].append(r["age_group"])
    out["price_types"] = ["free", "paid", "registration"]
    return out


def _get_or_create_user(platform: str, platform_user_id: str, name: str | None,
                        chat_id: str | None) -> int:
    conn = db.get_conn()
    now = datetime.now(timezone.utc).isoformat()
    row = conn.execute(
        "SELECT id FROM users WHERE platform=? AND platform_user_id=?",
        (platform, platform_user_id),
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE users SET name=COALESCE(?, name), chat_id=COALESCE(?, chat_id), "
            "updated_at=? WHERE id=?",
            (name, chat_id, now, row["id"]),
        )
        conn.commit()
        return row["id"]
    cur = conn.execute(
        "INSERT INTO users (platform, platform_user_id, name, chat_id, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?)",
        (platform, platform_user_id, name, chat_id, now, now),
    )
    conn.commit()
    return cur.lastrowid


def register(platform: str, platform_user_id: str, name: str | None,
             chat_id: str | None, event_id: int) -> dict:
    conn = db.get_conn()
    event = get_event(event_id)
    if not event:
        return {"ok": False, "code": "event_not_found"}
    row = conn.execute("SELECT is_active FROM events WHERE id=?", (event_id,)).fetchone()
    if row is None or not row["is_active"]:
        return {"ok": False, "code": "event_not_found"}

    # уже прошло?
    if event["starts_at"]:
        try:
            if datetime.fromisoformat(event["starts_at"]) <= datetime.now(timezone.utc):
                return {"ok": False, "code": "event_past"}
        except ValueError:
            pass

    uid = _get_or_create_user(platform, platform_user_id, name, chat_id)

    # дубликат
    exists = conn.execute(
        "SELECT id FROM registrations WHERE user_id=? AND event_id=? AND status='active'",
        (uid, event_id),
    ).fetchone()
    if exists:
        return {"ok": False, "code": "already_registered"}

    # места
    if event["capacity"] is not None:
        active = conn.execute(
            "SELECT COUNT(*) c FROM registrations WHERE event_id=? AND status='active'",
            (event_id,),
        ).fetchone()["c"]
        if active >= event["capacity"]:
            return {"ok": False, "code": "no_capacity"}

    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        "INSERT INTO registrations (user_id, event_id, status, created_at) VALUES (?,?, 'active', ?)",
        (uid, event_id, now),
    )
    conn.commit()
    return {"ok": True, "code": "registered", "registration_id": cur.lastrowid,
            "event": event}


def cancel(platform: str, platform_user_id: str, event_id: int) -> dict:
    conn = db.get_conn()
    uid = conn.execute(
        "SELECT id FROM users WHERE platform=? AND platform_user_id=?",
        (platform, platform_user_id),
    ).fetchone()
    if not uid:
        return {"ok": False, "code": "not_registered"}
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        "UPDATE registrations SET status='cancelled', cancelled_at=? "
        "WHERE user_id=? AND event_id=? AND status='active'",
        (now, uid["id"], event_id),
    )
    conn.commit()
    if cur.rowcount == 0:
        return {"ok": False, "code": "not_registered"}
    return {"ok": True, "code": "cancelled"}


def my_events(platform: str, platform_user_id: str) -> list[dict]:
    conn = db.get_conn()
    rows = conn.execute(
        """
        SELECT e.*, r.id AS reg_id, r.status AS reg_status, r.created_at AS reg_created
        FROM registrations r JOIN events e ON e.id = r.event_id
        WHERE r.status='active'
          AND r.user_id = (SELECT id FROM users WHERE platform=? AND platform_user_id=?)
        ORDER BY (e.starts_at IS NULL) ASC, e.starts_at ASC
        """,
        (platform, platform_user_id),
    ).fetchall()
    out = []
    for r in rows:
        d = _event_row_to_dict(r)
        d["registration_id"] = r["reg_id"]
        out.append(d)
    return out


# ---------- напоминания ----------

def due_reminders(now: datetime | None = None) -> list[dict]:
    """Активные записи, где событие начнётся в ближайший час и напоминание ещё не слали."""
    conn = db.get_conn()
    now = now or datetime.now(timezone.utc)
    # окно: событие уже в [now, now+60мин], напоминание не отправлено
    cutoff = (now + timedelta(minutes=60)).isoformat()
    rows = conn.execute(
        """
        SELECT r.id AS reg_id, r.user_id, r.event_id,
               e.title, e.starts_at, e.place, e.date_text,
               u.platform, u.platform_user_id, u.chat_id, u.name
        FROM registrations r
        JOIN events e ON e.id = r.event_id
        JOIN users  u ON u.id = r.user_id
        WHERE r.status = 'active'
          AND r.reminder_sent_at IS NULL
          AND e.starts_at IS NOT NULL
          AND e.starts_at <= ?
          AND e.starts_at > ?
        """,
        (cutoff, now.isoformat()),
    ).fetchall()
    return [dict(r) for r in rows]


def mark_reminder_sent(reg_id: int, sent_at: str) -> None:
    conn = db.get_conn()
    conn.execute(
        "UPDATE registrations SET reminder_sent_at=? WHERE id=?", (sent_at, reg_id)
    )
    conn.commit()