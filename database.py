import sqlite3
import re
from config import DB_PATH


def _get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _migrate_schema(conn):
    cols = {row[1] for row in conn.execute("PRAGMA table_info(sent_reminders)")}
    if cols and "student_id" not in cols and "appointment_id" in cols:
        conn.execute("ALTER TABLE sent_reminders RENAME COLUMN appointment_id TO student_id")

    session_cols = {row[1] for row in conn.execute("PRAGMA table_info(tutoring_sessions)")}
    if session_cols and "chat_id" not in session_cols:
        conn.execute("ALTER TABLE tutoring_sessions ADD COLUMN chat_id TEXT")


def init_db():
    conn = _get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sent_reminders (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id  TEXT NOT NULL,
            phone       TEXT NOT NULL,
            reminder_type TEXT NOT NULL DEFAULT 'daily',
            sent_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(student_id, reminder_type, sent_at)
        );

        CREATE TABLE IF NOT EXISTS processed_replies (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id  TEXT NOT NULL UNIQUE,
            phone       TEXT NOT NULL,
            body        TEXT NOT NULL,
            intent      TEXT,
            processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS tutoring_sessions (
            phone            TEXT PRIMARY KEY,
            state            TEXT NOT NULL,
            name             TEXT,
            subject          TEXT,
            time             TEXT,
            weakness         TEXT,
            current_question TEXT,
            chat_id          TEXT,
            updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- Session state carries a plan now, not a single subject, so the shape
        -- changed. New table rather than a migration: the old one holds nothing
        -- but transient half-finished conversations, and leaving it in place
        -- keeps any existing rows recoverable.
        CREATE TABLE IF NOT EXISTS sessions (
            phone       TEXT PRIMARY KEY,
            state       TEXT NOT NULL,
            payload     TEXT,
            chat_id     TEXT,
            updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    _migrate_schema(conn)
    conn.commit()
    conn.close()

    # Sibling modules own their own tables.
    import mastery
    import student
    import autonomy
    mastery.init()
    student.init()
    autonomy.init()


def is_reminder_sent(student_id, reminder_type="daily"):
    conn = _get_connection()
    cursor = conn.execute(
        "SELECT 1 FROM sent_reminders WHERE student_id = ? AND reminder_type = ? AND "
        "date(sent_at) = date('now')",
        (student_id, reminder_type),
    )
    result = cursor.fetchone() is not None
    conn.close()
    return result


def mark_reminder_sent(student_id, phone, reminder_type="daily"):
    conn = _get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO sent_reminders (student_id, phone, reminder_type) VALUES (?, ?, ?)",
        (student_id, phone, reminder_type),
    )
    conn.commit()
    conn.close()


def is_reply_processed(message_id):
    conn = _get_connection()
    cursor = conn.execute(
        "SELECT 1 FROM processed_replies WHERE message_id = ?", (message_id,)
    )
    result = cursor.fetchone() is not None
    conn.close()
    return result


def mark_reply_processed(message_id, phone, body, intent=None):
    conn = _get_connection()
    try:
        conn.execute(
            "INSERT INTO processed_replies (message_id, phone, body, intent) VALUES (?, ?, ?, ?)",
            (message_id, phone, body, intent),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        pass
    finally:
        conn.close()


def save_session(phone: str, state: str, payload: str | None, chat_id=None):
    conn = _get_connection()
    conn.execute(
        """INSERT INTO sessions (phone, state, payload, chat_id, updated_at)
           VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(phone) DO UPDATE SET
               state=excluded.state,
               payload=excluded.payload,
               chat_id=COALESCE(excluded.chat_id, sessions.chat_id),
               updated_at=CURRENT_TIMESTAMP""",
        (phone, state, payload, chat_id),
    )
    conn.commit()
    conn.close()


def load_session(phone: str):
    conn = _get_connection()
    row = conn.execute(
        "SELECT state, payload, chat_id FROM sessions WHERE phone = ?", (phone,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def delete_session(phone: str):
    conn = _get_connection()
    conn.execute("DELETE FROM sessions WHERE phone = ?", (phone,))
    conn.commit()
    conn.close()
