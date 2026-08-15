"""Runtime access to the vetted question bank.

Reads `question_bank.json` (built offline by bank_build.py) and serves items.
No network calls: selecting a question is a dictionary lookup, so the student
waits on their own connection and nothing else.

Two jobs beyond lookup:

  * never repeat a question to the same student -- the old RAG path handed back
    the same nearest neighbour forever, which is instantly visible in a demo
  * pick a difficulty that lands in the zone of proximal development, roughly a
    70% chance of success: hard enough to teach, easy enough to stay engaged
"""

import json
import os
import random
from collections import defaultdict

BANK_PATH = os.path.join(os.path.dirname(__file__), "question_bank.json")

# Aim each question at this success probability. Well below this and students
# disengage; well above and they learn nothing from the attempt.
TARGET_SUCCESS = 0.70

_bank: list[dict] | None = None
_by_skill: dict[str, dict[str, list[dict]]] = {}
_by_id: dict[str, dict] = {}


def load(force: bool = False) -> list[dict]:
    global _bank, _by_skill, _by_id
    if _bank is not None and not force:
        return _bank

    if not os.path.exists(BANK_PATH):
        _bank, _by_skill, _by_id = [], {}, {}
        return _bank

    with open(BANK_PATH, encoding="utf-8") as f:
        _bank = json.load(f)

    grouped: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    _by_id = {}
    for q in _bank:
        grouped[q["skill_id"]][q["difficulty"]].append(q)
        _by_id[q["id"]] = q
    _by_skill = {k: dict(v) for k, v in grouped.items()}
    return _bank


def is_available() -> bool:
    return bool(load())


def difficulty_for(p_mastery: float) -> str:
    """Choose the difficulty whose expected success rate sits nearest TARGET_SUCCESS.

    Mastery is the probability the student knows the skill; a harder item makes
    that knowledge matter more. These bands are coarse on purpose -- with three
    difficulty levels there is no point pretending to finer resolution.
    """
    if p_mastery < 0.45:
        return "easy"
    if p_mastery < 0.78:
        return "medium"
    return "hard"


def _candidates(skill_id: str, difficulty: str, exclude: set[str]) -> list[dict]:
    pool = _by_skill.get(skill_id, {}).get(difficulty, [])
    return [q for q in pool if q["id"] not in exclude]


def encodes_misconception(question: dict, slug: str) -> bool:
    """Does one of this item's wrong options encode this specific error?"""
    for tag in (question.get("distractors") or {}).values():
        if tag and tag.get("slug") == slug:
            return True
    return False


def pick(skill_id: str, p_mastery: float = 0.25,
         exclude: set[str] | None = None,
         rng: random.Random | None = None,
         prefer_misconception: str | None = None) -> dict | None:
    """Best unseen question for this skill, or None if the bank is exhausted.

    `prefer_misconception` is what makes the misconception-repair intervention
    something other than a relabelled quiz: it pulls items whose distractors
    encode the exact error this student keeps making, so the trap is walked
    into deliberately and named, rather than waited for. It is a preference and
    not a filter -- if no such item is left, ordinary selection continues,
    because refusing to serve anything would be a worse failure than serving a
    generic question.
    """
    load()
    exclude = exclude or set()
    rng = rng or random

    wanted = difficulty_for(p_mastery)
    # Try the target difficulty, then step outward rather than repeating an item.
    order = {
        "easy": ["easy", "medium", "hard"],
        "medium": ["medium", "easy", "hard"],
        "hard": ["hard", "medium", "easy"],
    }[wanted]

    if prefer_misconception:
        for difficulty in order:
            pool = [q for q in _candidates(skill_id, difficulty, exclude)
                    if encodes_misconception(q, prefer_misconception)]
            if pool:
                return rng.choice(pool)

    for difficulty in order:
        pool = _candidates(skill_id, difficulty, exclude)
        if pool:
            return rng.choice(pool)
    return None


def get(question_id: str) -> dict | None:
    load()
    return _by_id.get(question_id)


def misconception_for(question: dict, chosen_index: int) -> dict | None:
    """The misconception encoded by the option the student picked."""
    if chosen_index == question.get("correct_index"):
        return None
    return (question.get("distractors") or {}).get(str(chosen_index))


def coverage() -> dict[str, int]:
    load()
    return {skill_id: sum(len(v) for v in diffs.values())
            for skill_id, diffs in _by_skill.items()}


def remaining_for(skill_id: str, exclude: set[str]) -> int:
    load()
    diffs = _by_skill.get(skill_id, {})
    return sum(1 for pool in diffs.values() for q in pool if q["id"] not in exclude)


LETTERS = "ABCD"


def format_question(question: dict, header: str | None = None) -> str:
    """Render for WhatsApp: plain text, no markdown, minimal bytes."""
    parts = []
    if header:
        parts.append(header)
    if question.get("passage"):
        parts.append(question["passage"])
    parts.append(question["question"])
    parts.append("\n".join(
        f"{LETTERS[i]}) {opt}" for i, opt in enumerate(question["options"])
    ))
    return "\n\n".join(parts)


def letter_to_index(letter: str) -> int | None:
    letter = (letter or "").strip().upper()
    return LETTERS.index(letter) if letter in LETTERS else None


def index_to_letter(index: int) -> str:
    return LETTERS[index] if 0 <= index < 4 else "?"


if __name__ == "__main__":
    bank = load()
    if not bank:
        print(f"no bank at {BANK_PATH} -- run: python bank_build.py")
        raise SystemExit(1)

    from skills import SKILLS
    cov = coverage()
    print(f"{len(bank)} questions, {len(cov)}/{len(SKILLS)} skills covered")
    missing = [s.id for s in SKILLS if not cov.get(s.id)]
    if missing:
        print(f"missing: {', '.join(missing)}")

    thin = sorted((n, k) for k, n in cov.items() if n < 8)
    if thin:
        print("thin skills: " + ", ".join(f"{k}={n}" for n, k in thin))

    print("\nsample:")
    q = random.choice(bank)
    print(format_question(q, header=f"[{q['skill_id']} / {q['difficulty']}]"))
    print(f"\nanswer: {index_to_letter(q['correct_index'])} -- {q['explanation']}")
