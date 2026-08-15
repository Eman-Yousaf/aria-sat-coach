"""Autonomous outreach.

The difference between a chatbot and an agent is who starts the conversation
and whether the decision to start it was reasoned. The old scheduler sent every
student "time to practice!" every day on a fixed timer -- a cron job wearing a
tutor's clothes.

This module instead asks, for each student: is there something worth saying
right now, and what is it worth? Every decision is scored, the best one wins,
and the reasoning is written to a table. That last part matters as much as the
decision itself: a student (or a judge) can ask Aria *why she messaged*, and
get the actual recorded reason rather than a plausible-sounding story generated
after the fact.

Triggers, each with its own evidence:

  retention_check ....... a delayed check is due, and the answer decides
                          whether an intervention gets believed
  decay_risk ............ a skill they had earned is slipping below usefulness
  misconception_pattern . the same specific error three times or more
  policy_experiment ..... Aria does not know which approach works for this
                          student, and has a cheap way to find out
  high_value_idle ....... they have been away and points are sitting on the table
  test_urgency .......... the exam is close and the plan is behind
  first_nudge ........... signed up, never practised

The last two additions are what stop autonomy being a nag with a reason
attached. "You have not studied in three days" is a notification. "I do not yet
know whether examples or questions work better for you, you have eight minutes,
so I am going to find out" is an agent doing something -- and the student can
tell the difference, which is the whole argument for scoring these against each
other rather than firing them on a timer.
"""

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import counterfactual
import mastery
import policy
import retention
import simulator
import student as student_mod
from config import DB_PATH
from interventions import INTERVENTION_BY_ID
from skills import SKILL_BY_ID

# Never message outside these hours in the student's local reckoning. Without a
# timezone we fall back to the hours they have historically replied in.
QUIET_START_HOUR = 21
QUIET_END_HOUR = 8

MIN_HOURS_BETWEEN_MESSAGES = 20
MAX_MESSAGES_PER_WEEK = 5

# A skill is "at risk" once it drops under this. Above it the student can still
# answer the question; below it they have effectively lost the skill.
DECAY_THRESHOLD = 0.60
DECAY_LOOKAHEAD_DAYS = 2.0

MISCONCEPTION_REPEAT_THRESHOLD = 3
IDLE_DAYS_BEFORE_NUDGE = 2.0

# A due retention check outranks almost everything: it is one question, and the
# answer decides whether a whole intervention keeps its credibility. Left
# unasked past this it stops measuring retention and starts measuring how long
# ago the student last opened the app.
RETENTION_BASE_SCORE = 14.0
RETENTION_STALE_DAYS = 5.0

# Running an experiment is worth doing when Aria is genuinely unsure, and worth
# nothing when she is not. Scored below the decay and misconception triggers on
# purpose: losing a skill the student already earned is a real cost today,
# while the value of resolving uncertainty is spread over every future session.
EXPERIMENT_BASE_SCORE = 7.0
EXPERIMENT_MAX_MINUTES = 10.0


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init():
    conn = _connect()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS agent_decisions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            phone       TEXT NOT NULL,
            trigger     TEXT NOT NULL,
            skill_id    TEXT,
            score       REAL NOT NULL,
            reason      TEXT NOT NULL,
            evidence    TEXT,
            message     TEXT,
            acted       INTEGER NOT NULL DEFAULT 0,
            decided_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_decisions_phone
            ON agent_decisions(phone, decided_at);
    """)
    conn.commit()
    conn.close()


@dataclass
class Decision:
    phone: str
    trigger: str
    skill_id: str | None
    score: float           # expected value; the highest-scoring decision wins
    reason: str            # human-readable, shown verbatim if asked "why?"
    evidence: str          # the numbers behind the reason
    # Ordered values for the WhatsApp template matching this trigger, when the
    # 24-hour window has closed and free-form text is not allowed. Built here
    # rather than parsed back out of `reason`, because recovering "9" and
    # "Percentages" from a sentence is a regex waiting to be wrong.
    template_params: list[str] = field(default_factory=list)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _recent_message_count(phone: str, days: int) -> int:
    conn = _connect()
    since = (_now() - timedelta(days=days)).isoformat()
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM agent_decisions "
        "WHERE phone = ? AND acted = 1 AND decided_at >= ?",
        (phone, since),
    ).fetchone()
    conn.close()
    return row["n"] if row else 0


def _hours_since_last_message(phone: str) -> float | None:
    conn = _connect()
    row = conn.execute(
        "SELECT decided_at FROM agent_decisions WHERE phone = ? AND acted = 1 "
        "ORDER BY decided_at DESC LIMIT 1",
        (phone,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    last = mastery._parse(row["decided_at"])
    if not last:
        return None
    return (_now() - last).total_seconds() / 3600.0


def is_quiet_hours(phone: str, now: datetime | None = None) -> bool:
    """Don't wake anyone up. Uses observed reply hours as a timezone proxy."""
    now = now or _now()
    hist = mastery.reply_hour_histogram(phone)
    if hist:
        # Treat the student's own active window as the acceptable window, padded
        # by an hour either side. This sidesteps needing a real timezone, which
        # we cannot get from a phone number reliably.
        active = sorted(hist)
        earliest, latest = active[0], active[-1]
        return not (earliest - 1 <= now.hour <= latest + 1)
    return now.hour >= QUIET_START_HOUR or now.hour < QUIET_END_HOUR


def can_message(phone: str) -> tuple[bool, str]:
    prof = student_mod.get(phone)
    if prof and prof.opted_out:
        return False, "student opted out"
    hours = _hours_since_last_message(phone)
    if hours is not None and hours < MIN_HOURS_BETWEEN_MESSAGES:
        return False, f"messaged {hours:.1f}h ago, minimum gap is {MIN_HOURS_BETWEEN_MESSAGES}h"
    if _recent_message_count(phone, 7) >= MAX_MESSAGES_PER_WEEK:
        return False, f"already sent {MAX_MESSAGES_PER_WEEK} messages this week"
    if is_quiet_hours(phone):
        return False, "outside the hours this student is normally active"
    return True, "ok"


def evaluate(phone: str) -> list[Decision]:
    """Score every reason Aria might have to reach out. Highest score wins."""
    states = mastery.get_all_states(phone)
    prof = student_mod.get(phone) or student_mod.Student(phone=phone)
    name = prof.name or "there"
    decisions: list[Decision] = []

    total_attempts = mastery.total_attempts(phone)

    # --- never practised -------------------------------------------------
    if total_attempts == 0:
        decisions.append(Decision(
            phone, "first_nudge", None, 5.0,
            "You signed up but haven't tried a question yet.",
            "0 attempts recorded",
            template_params=[name],
        ))
        return decisions

    gains = {g.skill_id: g for g in simulator.marginal_gains(states)}

    # --- a delayed check has come due ------------------------------------
    for probe in retention.due(phone):
        skill = SKILL_BY_ID.get(probe.skill_id)
        if not skill:
            continue
        iv = INTERVENTION_BY_ID.get(probe.intervention)
        approach = iv.name.lower() if iv else "that session"
        # Decays with lateness: a check asked eight days after the fact is no
        # longer measuring the two-day retention it was booked to measure.
        staleness = max(0.0, 1.0 - probe.days_overdue / RETENTION_STALE_DAYS)
        decisions.append(Decision(
            phone, "retention_check", probe.skill_id,
            RETENTION_BASE_SCORE * (0.5 + 0.5 * staleness),
            f"One question on {skill.name}. You learned it a few days ago and "
            f"I want to know if it's still there.",
            f"probe {probe.id} due {probe.days_overdue:.1f}d ago, tests "
            f"{approach}, mastery was {probe.mastery_at_close:.2f} at close",
            template_params=[name, skill.name],
        ))
        break   # one check-in per message; the rest keep until next time

    # --- a skill is slipping ---------------------------------------------
    for skill_id, state in states.items():
        if not state.is_seen or state.correct == 0:
            continue
        days_left = mastery.days_until_decay_below(state, DECAY_THRESHOLD)
        if days_left is None or days_left > DECAY_LOOKAHEAD_DAYS:
            continue
        worth = gains[skill_id].points_gained if skill_id in gains else 5.0
        # Losing a skill you already paid for is worse than never having it.
        score = 10.0 + worth * (1.0 - days_left / max(DECAY_LOOKAHEAD_DAYS, 0.1))
        skill = SKILL_BY_ID[skill_id]
        decisions.append(Decision(
            phone, "decay_risk", skill_id, score,
            f"You learned {skill.name} and it's fading. "
            f"A few questions now keeps it.",
            f"mastery {state.p_mastery:.2f}, crosses {DECAY_THRESHOLD} in "
            f"{days_left:.1f}d, worth {worth:.0f} pts",
            template_params=[name, skill.name, f"{days_left:.0f}"],
        ))

    # --- the same mistake, repeatedly ------------------------------------
    misconceptions = mastery.recent_misconceptions(phone, limit=40)
    counts: dict[tuple[str, str], int] = {}
    for m in misconceptions:
        key = (m["skill_id"], m["misconception"])
        counts[key] = counts.get(key, 0) + 1
    for (skill_id, slug), n in counts.items():
        if n < MISCONCEPTION_REPEAT_THRESHOLD:
            continue
        skill = SKILL_BY_ID.get(skill_id)
        if not skill:
            continue
        decisions.append(Decision(
            phone, "misconception_pattern", skill_id, 8.0 + n,
            f"You've made the same slip in {skill.name} {n} times. "
            f"It's one habit, and it's fixable in one session.",
            f"misconception '{slug}' repeated {n}x",
            template_params=[name, str(n), skill.name],
        ))

    # --- idle, with points on the table ----------------------------------
    idle_days = prof.days_since_active
    if idle_days is not None and idle_days >= IDLE_DAYS_BEFORE_NUDGE:
        top = simulator.marginal_gains(states)
        if top:
            best = top[0]
            score = 4.0 + min(idle_days, 7.0) * 0.5
            decisions.append(Decision(
                phone, "high_value_idle", best.skill_id, score,
                f"{best.questions_needed} questions on {best.name} is worth about "
                f"{best.points_gained:.0f} points. That's the biggest win available to you.",
                f"idle {idle_days:.1f}d, top skill {best.skill_id} "
                f"at {best.points_per_minute:.2f} pts/min",
                template_params=[name, str(best.questions_needed), best.name,
                                 f"{best.points_gained:.0f}"],
            ))

    # --- Aria does not know how this student learns ----------------------
    #
    # The one trigger here that is about Aria's own ignorance rather than the
    # student's. It fires only when the decision engine, asked for a short
    # session, says it would be running an experiment -- so the message is not
    # a claim that an experiment would be useful, it is the actual decision the
    # student would get if they replied GO.
    decision = counterfactual.decide(phone, states, EXPERIMENT_MAX_MINUTES)
    if decision is not None and decision.is_experiment:
        c = decision.chosen
        skill = SKILL_BY_ID.get(c.skill_id)
        if skill:
            # More uncertainty means more to gain from resolving it.
            doubt = 1.0 - c.p_best_intervention
            decisions.append(Decision(
                phone, "policy_experiment", c.skill_id,
                EXPERIMENT_BASE_SCORE + 4.0 * doubt,
                f"I don't know yet what makes {skill.name} click for you. "
                f"Give me {c.minutes:.0f} minutes and I'll try something and "
                f"find out.",
                f"{c.intervention} at {c.p_best_intervention:.0%} likely best "
                f"(runner-up {decision.greedy.intervention}), "
                f"{c.episodes} prior episodes, "
                f"resolves {c.info_value / policy.POP_MULTIPLIER_SD:.0%} of "
                f"remaining doubt",
                template_params=[name, f"{c.minutes:.0f}", skill.name],
            ))

    # --- the exam is coming ----------------------------------------------
    days_left = prof.days_until_test
    if days_left is not None and 0 < days_left <= 30:
        projection = simulator.expected_total(states)
        shortfall = prof.target_score - projection
        if shortfall > 0:
            needed = simulator.days_to_target(states, prof.target_score, 20)
            urgency = 6.0 + (30 - days_left) * 0.3
            feasible = (needed is not None and needed <= days_left)
            decisions.append(Decision(
                phone, "test_urgency", None, urgency,
                (f"Your test is in {days_left} days and you're about "
                 f"{shortfall:.0f} points below {prof.target_score}. "
                 + ("Still reachable if you start today."
                    if feasible else
                    "Let's lock in the points that are still winnable.")),
                f"projected {projection:.0f}, target {prof.target_score}, "
                f"needs ~{needed}d at 20min/day",
                template_params=[name, str(days_left), f"{shortfall:.0f}",
                                 str(prof.target_score)],
            ))

    return decisions


def decide(phone: str) -> Decision | None:
    """The single best action right now, or None if Aria should stay quiet."""
    allowed, why = can_message(phone)
    if not allowed:
        _log(Decision(phone, "suppressed", None, 0.0, why, why), acted=False)
        return None

    candidates = evaluate(phone)
    if not candidates:
        return None
    return max(candidates, key=lambda d: d.score)


def _log(decision: Decision, acted: bool, message: str | None = None):
    conn = _connect()
    conn.execute(
        """INSERT INTO agent_decisions
               (phone, trigger, skill_id, score, reason, evidence, message, acted, decided_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (decision.phone, decision.trigger, decision.skill_id, decision.score,
         decision.reason, decision.evidence, message, 1 if acted else 0,
         _now().isoformat()),
    )
    conn.commit()
    conn.close()


def record_action(decision: Decision, message: str):
    _log(decision, acted=True, message=message)


def last_decision(phone: str) -> dict | None:
    """Backs the student-facing 'why did you message me?' answer."""
    conn = _connect()
    row = conn.execute(
        "SELECT trigger, skill_id, reason, evidence, message, decided_at "
        "FROM agent_decisions WHERE phone = ? AND acted = 1 "
        "ORDER BY decided_at DESC LIMIT 1",
        (phone,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def decision_log(phone: str | None = None, limit: int = 50) -> list[dict]:
    conn = _connect()
    if phone:
        rows = conn.execute(
            "SELECT * FROM agent_decisions WHERE phone = ? "
            "ORDER BY decided_at DESC LIMIT ?", (phone, limit)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM agent_decisions ORDER BY decided_at DESC LIMIT ?",
            (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]
