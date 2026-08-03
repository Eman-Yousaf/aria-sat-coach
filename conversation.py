"""Conversation state for a tutoring session.

A session is what the student is doing *right now*: how many minutes they said
they have, which skills the planner chose for those minutes, and which question
is on screen. Durable facts about the student live in `student.py`; what they
know lives in `mastery.py`. This module owns only the in-flight conversation.

State survives process restarts (SQLite), because a student halfway through a
question should not lose it because a container restarted.
"""

import json
import re
from enum import Enum

import database


class TutoringState(Enum):
    AWAITING_NAME = "awaiting_name"
    AWAITING_TARGET = "awaiting_target"
    AWAITING_MINUTES = "awaiting_minutes"
    AWAITING_ANSWER = "awaiting_answer"
    IDLE = "idle"                       # between questions, awaiting "next"


_sessions: dict[str, "TutoringSession"] = {}


class TutoringSession:
    def __init__(self, phone: str):
        self.phone = phone
        self.state = TutoringState.AWAITING_NAME
        self.minutes: int | None = None
        # Remaining questions per skill, in plan order: [[skill_id, n], ...].
        # A list rather than a dict so the planner's ordering survives a save.
        self.plan_alloc: list[list] = []
        self.current_skill: str | None = None
        self.current_question_id: str | None = None
        self.asked: int = 0
        self.correct: int = 0
        self.start_projection: int | None = None   # to show movement at the end
        self.chat_id: str | None = None

    # -- persistence ------------------------------------------------------
    def save(self):
        database.save_session(
            self.phone,
            self.state.value,
            json.dumps({
                "minutes": self.minutes,
                "plan_alloc": self.plan_alloc,
                "current_skill": self.current_skill,
                "current_question_id": self.current_question_id,
                "asked": self.asked,
                "correct": self.correct,
                "start_projection": self.start_projection,
            }),
            self.chat_id,
        )
        _sessions[self.phone] = self

    @classmethod
    def from_row(cls, phone: str, row: dict) -> "TutoringSession":
        s = cls(phone)
        try:
            s.state = TutoringState(row["state"])
        except ValueError:
            s.state = TutoringState.AWAITING_NAME
        try:
            payload = json.loads(row["payload"]) if row["payload"] else {}
        except (json.JSONDecodeError, TypeError):
            payload = {}
        s.minutes = payload.get("minutes")
        s.plan_alloc = [list(x) for x in (payload.get("plan_alloc") or [])]
        s.current_skill = payload.get("current_skill")
        s.current_question_id = payload.get("current_question_id")
        s.asked = payload.get("asked", 0)
        s.correct = payload.get("correct", 0)
        s.start_projection = payload.get("start_projection")
        s.chat_id = row["chat_id"] if "chat_id" in row.keys() else None
        return s


def digits(text: str) -> str:
    return re.sub(r"\D", "", text or "")


def get_session(phone: str) -> TutoringSession | None:
    d = digits(phone)
    if d in _sessions:
        return _sessions[d]
    row = database.load_session(d)
    if not row:
        return None
    session = TutoringSession.from_row(d, row)
    _sessions[d] = session
    if session.chat_id:
        from whatsapp import set_chat_id
        set_chat_id(d, session.chat_id)
    return session


def create_session(phone: str, chat_id: str | None = None) -> TutoringSession:
    d = digits(phone)
    session = TutoringSession(d)
    session.chat_id = chat_id
    session.save()
    return session


def clear_session(phone: str):
    d = digits(phone)
    _sessions.pop(d, None)
    database.delete_session(d)


def has_active_session(phone: str) -> bool:
    d = digits(phone)
    return d in _sessions or database.load_session(d) is not None


# --- parsing helpers -----------------------------------------------------

def parse_minutes(text: str) -> int | None:
    """Read '30 minutes', '1 hour', 'half an hour', or a bare number."""
    lower = (text or "").lower().strip()
    if not lower:
        return None
    if "half an hour" in lower or "half hour" in lower:
        return 30
    if re.search(r"\ban hour\b", lower) and not re.search(r"\d", lower):
        return 60

    match = re.search(r"(\d+)\s*(h(?:ou)?rs?|h)\b", lower)
    if match:
        return min(int(match.group(1)) * 60, 240)
    match = re.search(r"(\d+)\s*(m(?:in(?:ute)?s?)?)\b", lower)
    if match:
        return min(int(match.group(1)), 240)

    match = re.search(r"\b(\d+)\b", lower)
    if match:
        n = int(match.group(1))
        # A bare number is minutes unless it is small enough to mean hours.
        return min(n * 60, 240) if n <= 4 else min(n, 240)
    return None


def parse_target_score(text: str) -> int | None:
    """Read a target SAT score. Accepts '1400', 'about 1300', '1,250'."""
    lower = (text or "").lower().replace(",", "")
    for match in re.finditer(r"\b(\d{3,4})\b", lower):
        n = int(match.group(1))
        if 400 <= n <= 1600:
            return int(round(n / 10.0) * 10)
    return None
