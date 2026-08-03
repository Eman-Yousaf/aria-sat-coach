"""Per-student, per-skill mastery model.

Bayesian Knowledge Tracing (BKT) with a forgetting curve.

Standard BKT tracks P(the student has learned skill k) and updates it after
every answer using slip/guess probabilities. Plain BKT never forgets, which is
wrong for test prep and -- more usefully -- leaves the agent with no principled
reason to message a student on any particular day. So mastery here decays back
toward the prior between practice sessions, with a half-life that lengthens
each time the student gets the skill right. That gives the spacing effect, and
it gives `days_until_decay_below()`, which is what lets Aria decide *when* to
reach out on her own.

Everything is stored in SQLite: it is local, fast, and works offline, so the
question loop never waits on a network round-trip.
"""

import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from config import DB_PATH
from skills import SKILL_BY_ID, SKILLS

# --- BKT parameters -------------------------------------------------------
# Deliberately conservative and shared across skills. Fitting per-skill
# parameters needs thousands of students; with one student's data, tuned
# parameters would just be overfitting.
P_INIT = 0.25       # prior P(mastered) before any evidence
P_LEARN = 0.15      # P(not mastered -> mastered) per practice opportunity
P_SLIP = 0.07       # P(wrong | mastered) -- also caps the top of the score scale
P_GUESS = 0.25      # P(right | not mastered), i.e. 1-in-4 on a 4-choice item

# --- Forgetting -----------------------------------------------------------
BASE_HALF_LIFE_DAYS = 3.0   # half-life after a single correct answer
SPACING_MULTIPLIER = 1.7    # each additional success stretches the half-life
MAX_HALF_LIFE_DAYS = 120.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init():
    conn = _connect()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS skill_mastery (
            phone       TEXT NOT NULL,
            skill_id    TEXT NOT NULL,
            p_mastery   REAL NOT NULL,
            attempts    INTEGER NOT NULL DEFAULT 0,
            correct     INTEGER NOT NULL DEFAULT 0,
            last_seen   TIMESTAMP,
            PRIMARY KEY (phone, skill_id)
        );

        CREATE TABLE IF NOT EXISTS attempts (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            phone         TEXT NOT NULL,
            skill_id      TEXT NOT NULL,
            question_id   TEXT,
            correct       INTEGER NOT NULL,
            chosen        TEXT,
            misconception TEXT,
            source        TEXT NOT NULL DEFAULT 'chat',
            at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_attempts_phone ON attempts(phone, at);
        CREATE INDEX IF NOT EXISTS idx_attempts_skill ON attempts(phone, skill_id);
    """)
    conn.commit()
    conn.close()


@dataclass
class SkillState:
    skill_id: str
    p_mastery: float        # decayed to "now"
    p_mastery_raw: float    # as last written, before decay
    attempts: int
    correct: int
    last_seen: datetime | None

    @property
    def days_since(self) -> float | None:
        if not self.last_seen:
            return None
        return (_now() - self.last_seen).total_seconds() / 86400.0

    @property
    def is_seen(self) -> bool:
        return self.attempts > 0

    def p_correct(self) -> float:
        """Probability the student answers a fresh item on this skill correctly."""
        return self.p_mastery * (1 - P_SLIP) + (1 - self.p_mastery) * P_GUESS


def bkt_update(p: float, correct: bool) -> float:
    """One BKT step: Bayes on the observation, then the learning transition."""
    if correct:
        num = p * (1 - P_SLIP)
        den = num + (1 - p) * P_GUESS
    else:
        num = p * P_SLIP
        den = num + (1 - p) * (1 - P_GUESS)
    posterior = num / den if den > 0 else p
    updated = posterior + (1 - posterior) * P_LEARN
    return min(max(updated, 0.001), 0.999)


def expected_step(p: float) -> float:
    """Expected mastery after one more practice question.

    Averages the correct and incorrect branches weighted by how likely the
    student is to actually get it right. Forward projections must use this
    rather than assuming a correct answer, or they promise progress the
    student will not make.
    """
    p_right = p * (1 - P_SLIP) + (1 - p) * P_GUESS
    return p_right * bkt_update(p, True) + (1 - p_right) * bkt_update(p, False)


def _half_life(successes: int) -> float:
    if successes <= 0:
        return BASE_HALF_LIFE_DAYS
    return min(BASE_HALF_LIFE_DAYS * (SPACING_MULTIPLIER ** (successes - 1)),
               MAX_HALF_LIFE_DAYS)


def decay_by_days(p_raw: float, successes: int, days: float) -> float:
    """Relax mastery toward the prior after `days` without practice."""
    if p_raw <= P_INIT or days <= 0:
        return p_raw
    retained = 0.5 ** (days / _half_life(successes))
    return P_INIT + (p_raw - P_INIT) * retained


def _decay(p_raw: float, successes: int, last_seen: datetime | None) -> float:
    if last_seen is None:
        return p_raw
    days = max(0.0, (_now() - last_seen).total_seconds() / 86400.0)
    return decay_by_days(p_raw, successes, days)


def get_state(phone: str, skill_id: str) -> SkillState:
    conn = _connect()
    row = conn.execute(
        "SELECT p_mastery, attempts, correct, last_seen FROM skill_mastery "
        "WHERE phone = ? AND skill_id = ?",
        (phone, skill_id),
    ).fetchone()
    conn.close()

    if not row:
        return SkillState(skill_id, P_INIT, P_INIT, 0, 0, None)

    last_seen = _parse(row["last_seen"])
    raw = row["p_mastery"]
    return SkillState(
        skill_id=skill_id,
        p_mastery=_decay(raw, row["correct"], last_seen),
        p_mastery_raw=raw,
        attempts=row["attempts"],
        correct=row["correct"],
        last_seen=last_seen,
    )


def get_all_states(phone: str) -> dict[str, SkillState]:
    conn = _connect()
    rows = conn.execute(
        "SELECT skill_id, p_mastery, attempts, correct, last_seen "
        "FROM skill_mastery WHERE phone = ?",
        (phone,),
    ).fetchall()
    conn.close()

    stored = {r["skill_id"]: r for r in rows}
    states: dict[str, SkillState] = {}
    for skill in SKILLS:
        row = stored.get(skill.id)
        if row is None:
            states[skill.id] = SkillState(skill.id, P_INIT, P_INIT, 0, 0, None)
            continue
        last_seen = _parse(row["last_seen"])
        states[skill.id] = SkillState(
            skill_id=skill.id,
            p_mastery=_decay(row["p_mastery"], row["correct"], last_seen),
            p_mastery_raw=row["p_mastery"],
            attempts=row["attempts"],
            correct=row["correct"],
            last_seen=last_seen,
        )
    return states


def record_attempt(
    phone: str,
    skill_id: str,
    correct: bool,
    question_id: str | None = None,
    chosen: str | None = None,
    misconception: str | None = None,
    source: str = "chat",
) -> SkillState:
    """Apply one BKT update and persist it. Returns the new state."""
    if skill_id not in SKILL_BY_ID:
        raise ValueError(f"unknown skill_id: {skill_id!r}")

    prior = get_state(phone, skill_id)
    # Start from the decayed value: time passing since last practice is itself
    # evidence about what the student currently knows.
    updated = bkt_update(prior.p_mastery, correct)

    now = _now()
    conn = _connect()
    conn.execute(
        """INSERT INTO skill_mastery (phone, skill_id, p_mastery, attempts, correct, last_seen)
           VALUES (?, ?, ?, 1, ?, ?)
           ON CONFLICT(phone, skill_id) DO UPDATE SET
               p_mastery = excluded.p_mastery,
               attempts  = skill_mastery.attempts + 1,
               correct   = skill_mastery.correct + excluded.correct,
               last_seen = excluded.last_seen""",
        (phone, skill_id, updated, 1 if correct else 0, now.isoformat()),
    )
    conn.execute(
        """INSERT INTO attempts (phone, skill_id, question_id, correct, chosen, misconception, source, at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (phone, skill_id, question_id, 1 if correct else 0, chosen, misconception, source,
         now.isoformat()),
    )
    conn.commit()
    conn.close()

    return SkillState(
        skill_id=skill_id,
        p_mastery=updated,
        p_mastery_raw=updated,
        attempts=prior.attempts + 1,
        correct=prior.correct + (1 if correct else 0),
        last_seen=now,
    )


def days_until_decay_below(state: SkillState, threshold: float) -> float | None:
    """How many days until this skill's mastery falls under `threshold`.

    None if it is already below, or if it will never get there (mastery decays
    toward P_INIT, so anything at or under P_INIT is a floor, not a slide).
    Drives the autonomous check-in scheduler.
    """
    if state.last_seen is None:
        return None
    if state.p_mastery <= threshold:
        return None
    if threshold <= P_INIT or state.p_mastery_raw <= P_INIT:
        return None

    # Solve P_INIT + (raw - P_INIT) * 0.5^(d / hl) = threshold  for d.
    ratio = (threshold - P_INIT) / (state.p_mastery_raw - P_INIT)
    if ratio <= 0:
        return None
    days_total = _half_life(state.correct) * math.log(ratio, 0.5)
    elapsed = (state.days_since or 0.0)
    remaining = days_total - elapsed
    return max(0.0, remaining)


def recent_misconceptions(phone: str, skill_id: str | None = None, limit: int = 20):
    """Past wrong answers with a tagged misconception, newest first."""
    conn = _connect()
    if skill_id:
        rows = conn.execute(
            "SELECT skill_id, misconception, at FROM attempts "
            "WHERE phone = ? AND skill_id = ? AND correct = 0 AND misconception IS NOT NULL "
            "ORDER BY at DESC LIMIT ?",
            (phone, skill_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT skill_id, misconception, at FROM attempts "
            "WHERE phone = ? AND correct = 0 AND misconception IS NOT NULL "
            "ORDER BY at DESC LIMIT ?",
            (phone, limit),
        ).fetchall()
    conn.close()
    return [{"skill_id": r["skill_id"], "misconception": r["misconception"],
             "at": _parse(r["at"])} for r in rows]


def seen_question_ids(phone: str) -> set[str]:
    """Questions this student has already been served -- so we never repeat one."""
    conn = _connect()
    rows = conn.execute(
        "SELECT DISTINCT question_id FROM attempts WHERE phone = ? AND question_id IS NOT NULL",
        (phone,),
    ).fetchall()
    conn.close()
    return {r["question_id"] for r in rows}


def total_attempts(phone: str) -> int:
    conn = _connect()
    row = conn.execute("SELECT COUNT(*) AS n FROM attempts WHERE phone = ?", (phone,)).fetchone()
    conn.close()
    return row["n"] if row else 0


def reply_hour_histogram(phone: str) -> dict[int, int]:
    """UTC hour-of-day counts for this student's attempts.

    Used to work out when they actually engage, so autonomous check-ins land at
    a time they are likely to be holding their phone.
    """
    conn = _connect()
    rows = conn.execute("SELECT at FROM attempts WHERE phone = ?", (phone,)).fetchall()
    conn.close()
    hist: dict[int, int] = {}
    for r in rows:
        dt = _parse(r["at"])
        if dt:
            hist[dt.hour] = hist.get(dt.hour, 0) + 1
    return hist
