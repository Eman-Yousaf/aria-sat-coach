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
    # The Socratic intervention asks the student to say what a question is
    # asking before showing them the options. Any reply advances -- the point
    # is that they articulated it, not that a parser graded it.
    AWAITING_LEAD_IN = "awaiting_lead_in"
    IDLE = "idle"                       # between questions, awaiting "next"


_sessions: dict[str, "TutoringSession"] = {}


class TutoringSession:
    def __init__(self, phone: str):
        self.phone = phone
        self.state = TutoringState.AWAITING_NAME
        self.minutes: int | None = None
        # The blocks the policy engine allocated this session's minutes to, in
        # order: [[skill_id, intervention_id, questions_remaining], ...]. A list
        # rather than a dict so the ordering survives a save. Older rows hold
        # two-element entries and are widened on load.
        self.plan_alloc: list[list] = []
        self.current_skill: str | None = None
        self.current_question_id: str | None = None

        # --- the intervention episode in flight ---------------------------
        # An episode is one block of the plan: a single intervention applied to
        # a single skill. It is what policy.py measures, so it has to survive a
        # container restart or the evidence is lost halfway through being
        # collected.
        self.intervention: str | None = None
        self.episode_id: int | None = None
        self.episode_skill: str | None = None
        self.episode_before: float | None = None
        self.episode_target: int = 0
        self.episode_done: int = 0
        self.episode_correct: int = 0
        self.episode_expected_ppm: float | None = None
        self.episode_ppm_scale: float | None = None   # points per unit mastery
        # Set while a retention probe is on screen, so the answer is routed to
        # retention.resolve() as well as to the mastery model.
        self.probe_id: int | None = None
        self.asked: int = 0
        self.correct: int = 0
        self.start_projection: int | None = None   # to show movement at the end
        self.chat_id: str | None = None
        # How many times running the current prompt has failed to parse. Asking
        # the same question a third time is a dead end, not a clarification.
        self.confusions: int = 0

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
                "confusions": self.confusions,
                "intervention": self.intervention,
                "episode_id": self.episode_id,
                "episode_skill": self.episode_skill,
                "episode_before": self.episode_before,
                "episode_target": self.episode_target,
                "episode_done": self.episode_done,
                "episode_correct": self.episode_correct,
                "episode_expected_ppm": self.episode_expected_ppm,
                "episode_ppm_scale": self.episode_ppm_scale,
                "probe_id": self.probe_id,
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
        # Widen any two-element entry written before the plan carried an
        # intervention. A session saved by the old build then reloaded by this
        # one would otherwise unpack short and take down the reply.
        s.plan_alloc = [
            list(x) if len(x) >= 3 else [x[0], None, x[1]]
            for x in (payload.get("plan_alloc") or []) if len(x) >= 2
        ]
        s.current_skill = payload.get("current_skill")
        s.current_question_id = payload.get("current_question_id")
        s.asked = payload.get("asked", 0)
        s.correct = payload.get("correct", 0)
        s.start_projection = payload.get("start_projection")
        s.confusions = payload.get("confusions", 0)
        s.intervention = payload.get("intervention")
        s.episode_id = payload.get("episode_id")
        s.episode_skill = payload.get("episode_skill")
        s.episode_before = payload.get("episode_before")
        s.episode_target = payload.get("episode_target", 0)
        s.episode_done = payload.get("episode_done", 0)
        s.episode_correct = payload.get("episode_correct", 0)
        s.episode_expected_ppm = payload.get("episode_expected_ppm")
        s.episode_ppm_scale = payload.get("episode_ppm_scale")
        s.probe_id = payload.get("probe_id")
        s.chat_id = row["chat_id"] if "chat_id" in row.keys() else None
        return s


def digits(text: str) -> str:
    """Normalise a phone number to its digits, so "+92 300 1234567" and
    "923001234567" are one student rather than two.

    Identifiers that are not phone numbers are returned untouched. Stripping
    letters out of a web session id ("web_6b6b...") threw away most of its
    entropy and, for an id with no digits at all, collapsed it to the empty
    string -- at which point every such student shares one session and reads
    someone else's question. Web ids are hex so that is vanishingly unlikely
    in production, but "unlikely" is the wrong safety margin for handing one
    student another's conversation.
    """
    text = text or ""
    stripped = re.sub(r"\D", "", text)
    # A real phone number is digits, possibly with +, spaces, dashes, brackets.
    if stripped and re.fullmatch(r"[\d\s+()\-.]+", text):
        return stripped
    return text


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

# Students answer "how long have you got?" in words far more often than in
# numbers, and "no time" is the most common answer of all -- from exactly the
# student this is built for. Treating that as unparseable and re-asking was a
# dead end. Five minutes is a real answer to "no time", and it is the whole
# argument: a few minutes spent on the right skill still moves the score.
_PHRASE_MINUTES = [
    (r"\b(no time|haven'?t got time|have no time|not free|too busy|busy)\b", 5),
    (r"\b(barely any|hardly any|not much|very little|a little|a bit|short on)\b", 10),
    (r"\b(a few minutes|couple of minutes|quick|quickly|fast)\b", 5),
    (r"\b(all day|as long as it takes|whenever|lots|plenty)\b", 60),
]


def parse_minutes(text: str) -> int | None:
    """Read '30 minutes', '1 hour', 'half an hour', a bare number, or the
    words students actually use ('no time', 'not much', 'a quick one')."""
    lower = (text or "").lower().strip()
    if not lower:
        return None
    if "half an hour" in lower or "half hour" in lower:
        return 30
    if re.search(r"\ban hour\b", lower) and not re.search(r"\d", lower):
        return 60

    # Only when no digits are present, so "no time, ok 20 min" still reads 20.
    if not re.search(r"\d", lower):
        for pattern, minutes in _PHRASE_MINUTES:
            if re.search(pattern, lower):
                return minutes

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
