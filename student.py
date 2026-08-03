"""Durable student profiles.

The tutoring session in `conversation.py` is transient -- it is cleared when a
student types STOP. What a student *is* (their name, their target score, when
they sit the test, when they actually reply to messages) has to outlive that,
because the mastery model and the autonomous scheduler both depend on it.
"""

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone

from config import DB_PATH

DEFAULT_TARGET_SCORE = 1200


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init():
    conn = _connect()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS students (
            phone         TEXT PRIMARY KEY,
            name          TEXT,
            target_score  INTEGER,
            test_date     TEXT,
            preferred_hour INTEGER,
            opted_out     INTEGER NOT NULL DEFAULT 0,
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_active   TIMESTAMP
        );
    """)
    conn.commit()
    conn.close()


@dataclass
class Student:
    phone: str
    name: str | None = None
    target_score: int = DEFAULT_TARGET_SCORE
    test_date: date | None = None
    preferred_hour: int | None = None
    opted_out: bool = False
    created_at: datetime | None = None
    last_active: datetime | None = None

    @property
    def days_until_test(self) -> int | None:
        if not self.test_date:
            return None
        return (self.test_date - datetime.now(timezone.utc).date()).days

    @property
    def days_since_active(self) -> float | None:
        if not self.last_active:
            return None
        return (datetime.now(timezone.utc) - self.last_active).total_seconds() / 86400.0


def _parse_dt(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _parse_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _row_to_student(row) -> Student:
    return Student(
        phone=row["phone"],
        name=row["name"],
        target_score=row["target_score"] or DEFAULT_TARGET_SCORE,
        test_date=_parse_date(row["test_date"]),
        preferred_hour=row["preferred_hour"],
        opted_out=bool(row["opted_out"]),
        created_at=_parse_dt(row["created_at"]),
        last_active=_parse_dt(row["last_active"]),
    )


def get(phone: str) -> Student | None:
    conn = _connect()
    row = conn.execute("SELECT * FROM students WHERE phone = ?", (phone,)).fetchone()
    conn.close()
    return _row_to_student(row) if row else None


def get_or_create(phone: str, name: str | None = None) -> Student:
    existing = get(phone)
    if existing:
        if name and not existing.name:
            update(phone, name=name)
            existing.name = name
        return existing

    conn = _connect()
    conn.execute(
        "INSERT INTO students (phone, name, target_score, last_active) VALUES (?, ?, ?, ?)",
        (phone, name, DEFAULT_TARGET_SCORE, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()
    return get(phone)


_UPDATABLE = {"name", "target_score", "test_date", "preferred_hour", "opted_out"}


def update(phone: str, **fields):
    fields = {k: v for k, v in fields.items() if k in _UPDATABLE}
    if not fields:
        return
    if isinstance(fields.get("test_date"), date):
        fields["test_date"] = fields["test_date"].isoformat()
    if "opted_out" in fields:
        fields["opted_out"] = 1 if fields["opted_out"] else 0

    assignments = ", ".join(f"{k} = ?" for k in fields)
    conn = _connect()
    conn.execute(f"UPDATE students SET {assignments} WHERE phone = ?",
                 (*fields.values(), phone))
    conn.commit()
    conn.close()


def touch(phone: str):
    conn = _connect()
    conn.execute("UPDATE students SET last_active = ? WHERE phone = ?",
                 (datetime.now(timezone.utc).isoformat(), phone))
    conn.commit()
    conn.close()


def all_students(include_opted_out: bool = False) -> list[Student]:
    conn = _connect()
    query = "SELECT * FROM students"
    if not include_opted_out:
        query += " WHERE opted_out = 0"
    rows = conn.execute(query).fetchall()
    conn.close()
    return [_row_to_student(r) for r in rows]
