"""Did it stick?

A student who goes 5-for-5 straight after an explanation has demonstrated that
they can answer questions straight after an explanation. That is not the same
as having learned anything, and optimising for it produces a tutor that teaches
brilliantly for ten minutes and leaves nothing behind.

So every episode that moved mastery gets a lightweight debt: one targeted
question, a couple of days later, on the same skill. If the student still has
it, the intervention that produced it earns real evidence. If they do not, the
intervention is downgraded -- not because it failed to teach, but because what
it taught was shallow, which is a different failure with a different fix.

This is the smallest honest instrument for the distinction. It is one item, so
it is noisy; the Beta posterior in policy.py is what carries that noise
forward rather than pretending a single question settles anything.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from config import DB_PATH

__all__ = [
    "Probe", "init", "schedule", "due", "pending", "resolve",
    "outcome_counts", "DEFAULT_DELAY_DAYS",
]

# Two days is the shortest gap at which forgetting is measurable under the
# half-life in mastery.py (three days after a single success), and the longest
# a student on a shared phone can be relied on to still be reachable.
DEFAULT_DELAY_DAYS = 2.0

# Below this much mastery movement there is nothing to check the retention of,
# and probing anyway would spend the student's time to measure noise.
MIN_GAIN_TO_PROBE = 0.02


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init():
    conn = _connect()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS retention_probes (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            phone        TEXT NOT NULL,
            episode_id   INTEGER NOT NULL,
            intervention TEXT NOT NULL,
            skill_id     TEXT NOT NULL,
            -- Mastery at the moment the episode closed. The probe asks whether
            -- this survived, so it has to be stored rather than re-read later,
            -- by which time decay has already eaten the answer.
            mastery_at_close REAL NOT NULL,
            due_at       TIMESTAMP NOT NULL,
            asked_at     TIMESTAMP,
            resolved_at  TIMESTAMP,
            correct      INTEGER,
            question_id  TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_probes_due
            ON retention_probes(phone, resolved_at, due_at);
    """)
    conn.commit()
    conn.close()


@dataclass
class Probe:
    id: int
    phone: str
    episode_id: int
    intervention: str
    skill_id: str
    mastery_at_close: float
    due_at: datetime
    asked_at: datetime | None
    resolved_at: datetime | None
    correct: int | None
    question_id: str | None

    @property
    def is_resolved(self) -> bool:
        return self.resolved_at is not None

    @property
    def days_overdue(self) -> float:
        return (_now() - self.due_at).total_seconds() / 86400.0


def _row_to_probe(row) -> Probe:
    from mastery import _parse
    return Probe(
        id=row["id"], phone=row["phone"], episode_id=row["episode_id"],
        intervention=row["intervention"], skill_id=row["skill_id"],
        mastery_at_close=row["mastery_at_close"],
        due_at=_parse(row["due_at"]), asked_at=_parse(row["asked_at"]),
        resolved_at=_parse(row["resolved_at"]), correct=row["correct"],
        question_id=row["question_id"],
    )


def schedule(phone: str, episode_id: int, intervention: str, skill_id: str,
             mastery_at_close: float, gain: float,
             delay_days: float = DEFAULT_DELAY_DAYS) -> int | None:
    """Book a delayed check, if there is anything worth checking.

    Returns the probe id, or None when the episode did not move mastery enough
    to be worth a student's minute two days from now.
    """
    if gain < MIN_GAIN_TO_PROBE:
        return None
    due = _now() + timedelta(days=delay_days)
    conn = _connect()
    cur = conn.execute(
        """INSERT INTO retention_probes
               (phone, episode_id, intervention, skill_id, mastery_at_close, due_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (phone, episode_id, intervention, skill_id, mastery_at_close,
         due.isoformat()),
    )
    probe_id = cur.lastrowid
    conn.commit()
    conn.close()
    return probe_id


def due(phone: str, now: datetime | None = None) -> list[Probe]:
    """Unresolved probes whose day has come, oldest first."""
    now = now or _now()
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM retention_probes WHERE phone = ? AND resolved_at IS NULL "
        "AND due_at <= ? ORDER BY due_at",
        (phone, now.isoformat()),
    ).fetchall()
    conn.close()
    return [_row_to_probe(r) for r in rows]


def pending(phone: str) -> list[Probe]:
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM retention_probes WHERE phone = ? AND resolved_at IS NULL "
        "ORDER BY due_at", (phone,)).fetchall()
    conn.close()
    return [_row_to_probe(r) for r in rows]


def get(probe_id: int) -> Probe | None:
    conn = _connect()
    row = conn.execute("SELECT * FROM retention_probes WHERE id = ?",
                       (probe_id,)).fetchone()
    conn.close()
    return _row_to_probe(row) if row else None


def mark_asked(probe_id: int, question_id: str):
    conn = _connect()
    conn.execute(
        "UPDATE retention_probes SET asked_at = ?, question_id = ? WHERE id = ?",
        (_now().isoformat(), question_id, probe_id))
    conn.commit()
    conn.close()


def resolve(probe_id: int, correct: bool) -> Probe | None:
    """Record the answer. This is the moment an intervention's retention
    estimate moves, and with it the ranking that chooses the next one."""
    conn = _connect()
    conn.execute(
        "UPDATE retention_probes SET resolved_at = ?, correct = ? WHERE id = ?",
        (_now().isoformat(), 1 if correct else 0, probe_id))
    conn.commit()
    conn.close()
    return get(probe_id)


def retire(probe_id: int):
    """Close a probe without recording an outcome.

    Used when there is nothing left in the bank to ask it with. Marking it
    resolved-and-wrong would be the easy path and it would be a lie: the
    student never saw a question, so the intervention that produced this
    episode has not been shown to have failed. `resolved_at` is set so it stops
    coming due; `correct` stays NULL so `outcome_counts` steps over it.
    """
    conn = _connect()
    conn.execute(
        "UPDATE retention_probes SET resolved_at = ? WHERE id = ? AND correct IS NULL",
        (_now().isoformat(), probe_id))
    conn.commit()
    conn.close()


def outcome_counts(phone: str, intervention: str | None = None) -> tuple[int, int]:
    """(kept, lost) across resolved probes. Feeds the Beta in policy.py."""
    conn = _connect()
    sql = ("SELECT correct, COUNT(*) AS n FROM retention_probes "
           "WHERE phone = ? AND resolved_at IS NOT NULL AND correct IS NOT NULL")
    params: list = [phone]
    if intervention:
        sql += " AND intervention = ?"
        params.append(intervention)
    sql += " GROUP BY correct"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    kept = lost = 0
    for r in rows:
        if r["correct"]:
            kept = r["n"]
        else:
            lost = r["n"]
    return kept, lost


def summary(phone: str) -> dict:
    """Counts for the dashboard and the demo."""
    kept, lost = outcome_counts(phone)
    return {
        "resolved": kept + lost,
        "kept": kept,
        "lost": lost,
        "pending": len(pending(phone)),
        "due_now": len(due(phone)),
    }
