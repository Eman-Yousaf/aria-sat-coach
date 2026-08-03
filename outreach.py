"""Turning a decision into a message.

autonomy.py decides *whether and why* to reach out. This turns that into words.

The reason text is written by the decision itself, not by a language model, so
what the student reads is the actual basis for the message rather than a
paraphrase. The LLM is used only to warm the phrasing, and if it is unavailable
or returns something unusable the deterministic version goes out unchanged.
That ordering matters: an outage should cost tone, never correctness.
"""

import bank
import mastery
import simulator
from skills import SKILL_BY_ID

MAX_CHARS = 320


def _deterministic(profile, decision) -> str:
    name = profile.name or "there"
    lines = [f"Hi {name}. {decision.reason}"]

    if decision.skill_id:
        states = mastery.get_all_states(profile.phone)
        seen = mastery.seen_question_ids(profile.phone)
        if bank.remaining_for(decision.skill_id, seen) > 0:
            lines.append("Reply GO for one question on it - takes two minutes.")
        else:
            lines.append("Reply PLAN and I'll pick your next focus.")
    else:
        lines.append("Reply GO when you have a few minutes.")

    return "\n\n".join(lines)


_WARM_PROMPT = (
    "Rewrite this SAT coach's message so it sounds like a person who knows the "
    "student, not a notification.\n\n"
    "Hard rules:\n"
    "- Keep every number and fact exactly as given. Invent nothing.\n"
    "- Under 300 characters.\n"
    "- No emoji, no markdown, no exclamation marks.\n"
    "- Keep the closing instruction (the GO or PLAN reply) intact.\n"
    "- Plain warmth, not hype. Do not call them a superstar or a rockstar.\n\n"
    "Message:\n{message}"
)


def _warm(message: str) -> str | None:
    try:
        from agent import _get_llm
        from langchain_core.prompts import ChatPromptTemplate
        prompt = ChatPromptTemplate.from_messages([("human", _WARM_PROMPT)])
        result = (prompt | _get_llm()).invoke({"message": message})
        text = (result.content or "").strip()
    except Exception:
        return None

    if not text or len(text) > MAX_CHARS:
        return None
    # If the rewrite dropped the call to action it is worse than the original.
    if "GO" not in text.upper() and "PLAN" not in text.upper():
        return None
    return text


def compose(profile, decision) -> str:
    base = _deterministic(profile, decision)
    return _warm(base) or base


def preview(phone: str) -> str:
    """What would Aria say to this student right now, and why. For the demo."""
    import autonomy
    import student as student_mod

    profile = student_mod.get(phone)
    if not profile:
        return f"no student {phone}"

    allowed, why = autonomy.can_message(phone)
    candidates = autonomy.evaluate(phone)
    lines = [f"student: {profile.name or phone}",
             f"can message: {allowed} ({why})", ""]

    if not candidates:
        lines.append("no reason to reach out right now")
        return "\n".join(lines)

    lines.append("candidate reasons, best first:")
    for d in sorted(candidates, key=lambda x: -x.score):
        skill = SKILL_BY_ID[d.skill_id].name if d.skill_id else "-"
        lines.append(f"  [{d.score:5.1f}] {d.trigger:22s} {skill}")
        lines.append(f"          {d.evidence}")

    best = max(candidates, key=lambda d: d.score)
    lines.append("")
    lines.append("would send:")
    lines.append(compose(profile, best))
    return "\n".join(lines)
