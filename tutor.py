"""The tutoring conversation.

Turns the model into something a student can feel. Three things happen here
that a quiz bot does not do:

  * the session opens with a *plan* -- "you have 25 minutes, here is what they
    are worth and why these skills" -- rather than a subject menu
  * wrong answers are diagnosed, not just marked. The bank knows which
    misconception each distractor encodes, so Aria can name the mistake and
    recognise it when it recurs
  * the student can see their projected score move as they answer

Message text is kept short and plain: no markdown, no emoji-as-information, no
images. Every reply is a few hundred bytes, which is the point -- this has to
work on a shared phone on a slow connection.
"""

import random

import bank
import mastery
import simulator
import student as student_mod
from conversation import (
    TutoringState,
    clear_session,
    create_session,
    get_session,
    parse_minutes,
    parse_target_score,
)
from skills import SKILL_BY_ID

# Below this many answers the projection is mostly prior, not evidence, so we
# say so rather than showing a confident number the student would over-trust.
MIN_ATTEMPTS_FOR_PROJECTION = 8

HELP_TEXT = (
    "I'm Aria, your SAT coach.\n\n"
    "Just reply A, B, C or D to answer.\n\n"
    "PLAN - what to study and why\n"
    "SCORE - your projected score\n"
    "WHY - why I messaged you\n"
    "GOAL 1400 - set your target\n"
    "SKIP - a different question\n"
    "STOP - pause everything\n\n"
    "Or ask me anything about the question."
)


# --- helpers -------------------------------------------------------------

def _fmt_mastery(p: float) -> str:
    return f"{round(p * 100)}%"


def _projection_line(phone: str, states=None) -> str:
    states = states or mastery.get_all_states(phone)
    attempts = mastery.total_attempts(phone)
    if attempts < MIN_ATTEMPTS_FOR_PROJECTION:
        left = MIN_ATTEMPTS_FOR_PROJECTION - attempts
        return (f"Still working you out - {left} more "
                f"question{'s' if left != 1 else ''} and I can project your score.")
    proj = simulator.project(states, n_sims=400)
    return f"Projected score: {proj.total} (range {proj.total_low}-{proj.total_high})"


def _plan_message(phone: str, minutes: int, states=None) -> tuple[str, list[str]]:
    """The opening pitch: what these minutes are worth, and why these skills."""
    states = states or mastery.get_all_states(phone)
    plan = simulator.plan_session(states, minutes)
    if plan.is_empty:
        gains = simulator.marginal_gains(states)
        skill_ids = [gains[0].skill_id] if gains else []
        return ("Let's just practise - I'll pick as we go.", skill_ids)

    lines = [f"{minutes} minutes. Here's the best use of them:", ""]
    for i, g in enumerate(plan.skills, 1):
        lines.append(f"{i}. {g.name} - {g.questions_needed} questions, "
                     f"worth about {g.points_gained:.0f} points")
    lines.append("")
    lines.append(f"Total: about +{plan.expected_points:.0f} points.")

    # The non-obvious part, and the reason this is not just a weakness list.
    skipped = [g for g in simulator.marginal_gains(states)
               if g.skill_id not in {s.skill_id for s in plan.skills}]
    weakest = min(skipped, key=lambda g: g.current_mastery, default=None)
    if weakest and plan.skills:
        top = plan.skills[0]
        if weakest.current_mastery < top.current_mastery:
            lines.append("")
            lines.append(
                f"Not {weakest.name}, even though it's your weakest - it shows up "
                f"less often on the test, so it's worth fewer points per minute."
            )

    lines.append("")
    lines.append("Ready? Reply GO.")
    return "\n".join(lines), [g.skill_id for g in plan.skills]


def _next_question(phone: str, session) -> str | None:
    """Serve the next question from the plan. None when the bank runs dry."""
    states = mastery.get_all_states(phone)
    seen = mastery.seen_question_ids(phone)

    candidates = session.plan_skills or []
    skill_id = None
    for candidate in candidates:
        if bank.remaining_for(candidate, seen) > 0:
            skill_id = candidate
            break

    if skill_id is None:
        skill_id = simulator.next_skill(states, minutes_available=session.minutes or 20)
        if skill_id is None or bank.remaining_for(skill_id, seen) == 0:
            for gain in simulator.marginal_gains(states):
                if bank.remaining_for(gain.skill_id, seen) > 0:
                    skill_id = gain.skill_id
                    break
            else:
                return None

    question = bank.pick(skill_id, states[skill_id].p_mastery, exclude=seen)
    if question is None:
        return None

    session.current_skill = skill_id
    session.current_question_id = question["id"]
    session.state = TutoringState.AWAITING_ANSWER
    session.save()

    skill = SKILL_BY_ID[skill_id]
    header = f"{skill.name} ({question['difficulty']})"
    return bank.format_question(question, header=header)


def _diagnose(phone: str, question: dict, chosen_index: int, skill_id: str) -> str:
    """Feedback that names the specific error, and notices when it repeats."""
    correct_index = question["correct_index"]
    if chosen_index == correct_index:
        return f"Correct. {question['explanation']}"

    tag = bank.misconception_for(question, chosen_index)
    right = bank.index_to_letter(correct_index)
    parts = [f"Not quite - the answer is {right}."]

    if tag:
        parts.append(f"You {tag['why']}.")
        # Has this exact error happened before? This is the thing a human tutor
        # does and an app almost never does.
        prior = [m for m in mastery.recent_misconceptions(phone, skill_id, limit=30)
                 if m["misconception"] == tag["slug"]]
        if len(prior) >= 1:
            parts.append(f"That's the {_ordinal(len(prior) + 1)} time on this one - "
                         f"it's a habit, not bad luck. Worth slowing down here.")

    parts.append(question["explanation"])
    return " ".join(parts)


def _ordinal(n: int) -> str:
    return {1: "1st", 2: "2nd", 3: "3rd"}.get(n, f"{n}th")


# --- entry point ---------------------------------------------------------

def handle(phone: str, body: str, send) -> None:
    """Process one inbound message. `send(text)` delivers a reply."""
    text = (body or "").strip()
    lower = text.lower()

    # Global commands work in any state, including no state at all.
    if lower in ("stop", "quit", "exit", "pause"):
        clear_session(phone)
        student_mod.update(phone, opted_out=True)
        send("Paused. I won't message you again until you say START. "
             "Your progress is saved.")
        return

    if lower in ("start", "resume", "unpause"):
        student_mod.update(phone, opted_out=False)

    if lower in ("help", "menu", "commands", "?"):
        send(HELP_TEXT)
        return

    profile = student_mod.get_or_create(phone)
    student_mod.touch(phone)
    session = get_session(phone)

    if lower in ("why", "why did you message me", "why this"):
        send(_explain_last_message(phone))
        return

    if lower in ("score", "progress", "how am i doing"):
        send(_score_report(phone))
        return

    if lower in ("plan", "what should i study"):
        minutes = (session.minutes if session else None) or 20
        message, skill_ids = _plan_message(phone, minutes)
        if session:
            session.plan_skills = skill_ids
            session.save()
        send(message)
        return

    if lower.startswith("goal"):
        target = parse_target_score(text)
        if target:
            student_mod.update(phone, target_score=target)
            states = mastery.get_all_states(phone)
            days = simulator.days_to_target(states, target, 20)
            if days is None:
                send(f"Target set: {target}. That's a stretch at 20 min/day - "
                     f"tell me how many minutes you can really do and I'll plan for it.")
            else:
                send(f"Target set: {target}. At 20 minutes a day, "
                     f"about {days} days of consistent work gets you there.")
        else:
            send("Tell me a score between 400 and 1600, like: GOAL 1400")
        return

    # --- new student -----------------------------------------------------
    if session is None:
        session = create_session(phone)
        if profile.name:
            session.state = TutoringState.AWAITING_MINUTES
            session.save()
            send(f"Welcome back, {profile.name}. How many minutes do you have today?")
        else:
            send("Hi! I'm Aria, your SAT coach. What should I call you?")
        return

    # --- state machine ---------------------------------------------------
    if session.state == TutoringState.AWAITING_NAME:
        name = text.strip().title()
        if len(name) < 2 or lower in ("hi", "hello", "hey", "yes", "no", "ok"):
            send("What's your name?")
            return
        student_mod.update(phone, name=name)
        session.state = TutoringState.AWAITING_TARGET
        session.save()
        send(f"Good to meet you, {name}. What score are you aiming for? "
             f"(Reply with a number like 1200, or SKIP if you're not sure.)")
        return

    if session.state == TutoringState.AWAITING_TARGET:
        target = parse_target_score(text)
        if target:
            student_mod.update(phone, target_score=target)
        session.state = TutoringState.AWAITING_MINUTES
        session.save()
        send("How many minutes do you have to study today?")
        return

    if session.state == TutoringState.AWAITING_MINUTES:
        minutes = parse_minutes(text)
        if not minutes:
            send("Roughly how long? Something like '20 minutes' or '1 hour'.")
            return
        session.minutes = minutes
        states = mastery.get_all_states(phone)
        if mastery.total_attempts(phone) >= MIN_ATTEMPTS_FOR_PROJECTION:
            session.start_projection = simulator.project(states, n_sims=400).total
        message, skill_ids = _plan_message(phone, minutes, states)
        session.plan_skills = skill_ids
        session.state = TutoringState.IDLE
        session.save()
        send(message)
        return

    if session.state == TutoringState.IDLE:
        if lower in ("no", "not now", "later"):
            send("No problem. Say GO when you're ready.")
            return
        question_text = _next_question(phone, session)
        if question_text is None:
            send("You've worked through everything I have on your priority skills. "
                 "That's genuinely impressive. Say PLAN to pick a new focus.")
            return
        send(question_text)
        return

    if session.state == TutoringState.AWAITING_ANSWER:
        question = bank.get(session.current_question_id or "")
        if question is None:
            session.state = TutoringState.IDLE
            session.save()
            send("Lost track of that question - say GO for a fresh one.")
            return

        if lower in ("skip", "next", "another"):
            # Skipping is evidence too, but weak evidence: record nothing rather
            # than punish a student for being honest that they don't know.
            question_text = _next_question(phone, session)
            send(question_text or "That's everything I have for now. Say PLAN.")
            return

        index = bank.letter_to_index(text)
        if index is None:
            send(_answer_followup(question, text))
            return

        _apply_answer(phone, session, question, index, send)
        return

    # Fallback: something unexpected. Reset gently rather than dead-ending.
    session.state = TutoringState.IDLE
    session.save()
    send("Say GO for a question, PLAN for what to study, or HELP for options.")


def _apply_answer(phone: str, session, question: dict, index: int, send) -> None:
    skill_id = session.current_skill or question["skill_id"]
    correct = index == question["correct_index"]
    tag = bank.misconception_for(question, index)

    before = mastery.get_state(phone, skill_id)
    feedback = _diagnose(phone, question, index, skill_id)
    after = mastery.record_attempt(
        phone, skill_id, correct,
        question_id=question["id"],
        chosen=bank.index_to_letter(index),
        misconception=tag["slug"] if tag else None,
    )

    session.asked += 1
    session.correct += 1 if correct else 0

    skill = SKILL_BY_ID[skill_id]
    movement = (f"{skill.name}: {_fmt_mastery(before.p_mastery)} "
                f"-> {_fmt_mastery(after.p_mastery)}")

    parts = [feedback, "", movement]

    # Every few questions, show the thing they are actually here for.
    if session.asked % 4 == 0:
        parts.append("")
        parts.append(_projection_line(phone))

    parts.append("")
    parts.append("Reply GO for the next one.")

    session.state = TutoringState.IDLE
    session.save()
    send("\n".join(parts))


def _answer_followup(question: dict, message: str) -> str:
    """Student asked something instead of answering. Answer it, don't lecture."""
    try:
        from agent import answer_follow_up
        return answer_follow_up(question, message)
    except Exception:
        return ("Reply A, B, C or D when you're ready - or say SKIP for a "
                "different question.")


def _score_report(phone: str) -> str:
    states = mastery.get_all_states(phone)
    profile = student_mod.get_or_create(phone)
    attempts = mastery.total_attempts(phone)

    if attempts < MIN_ATTEMPTS_FOR_PROJECTION:
        return (f"You've answered {attempts}. Give me "
                f"{MIN_ATTEMPTS_FOR_PROJECTION - attempts} more and I'll have "
                f"enough to project a score honestly.")

    proj = simulator.project(states, n_sims=800)
    lines = [
        f"Projected: {proj.total} (range {proj.total_low}-{proj.total_high})",
        f"Reading/Writing {proj.rw}   Math {proj.math}",
    ]

    chance = proj.probability_at_least(profile.target_score)
    lines.append("")
    lines.append(f"Chance of hitting {profile.target_score} today: {chance:.0%}")

    days = simulator.days_to_target(states, profile.target_score, 20)
    if days:
        lines.append(f"About {days} days at 20 min/day would get you there.")

    strongest = sorted(
        (s for s in states.values() if s.is_seen),
        key=lambda s: -s.p_mastery,
    )[:2]
    if strongest:
        lines.append("")
        lines.append("Strongest: " + ", ".join(
            f"{SKILL_BY_ID[s.skill_id].name} ({_fmt_mastery(s.p_mastery)})"
            for s in strongest))

    gains = simulator.marginal_gains(states)
    if gains:
        lines.append(f"Best next move: {gains[0].name} "
                     f"(+{gains[0].points_gained:.0f} pts)")
    return "\n".join(lines)


def _explain_last_message(phone: str) -> str:
    """Answer 'why did you message me?' from the decision log, not from a guess."""
    import autonomy
    last = autonomy.last_decision(phone)
    if not last:
        return ("I haven't messaged you on my own yet - you started this one. "
                "When I do reach out, ask WHY and I'll show you my reasoning.")
    lines = [last["reason"], "", f"My reasoning: {last['evidence']}"]
    if last.get("skill_id"):
        skill = SKILL_BY_ID.get(last["skill_id"])
        if skill:
            lines.append(f"Skill: {skill.name}")
    return "\n".join(lines)
