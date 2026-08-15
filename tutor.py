"""The tutoring conversation -- the execution layer.

This module used to be the brain. It is now the hands.

`counterfactual.py` decides *what* to do: which skill, which teaching approach,
for how many questions. This file carries that out, and the difference is
visible in the messages a student receives. A worked-example decision produces
a fully solved item before the first question; a cold-retrieval decision
produces no preamble at all; a misconception-repair decision names the exact
error the student keeps making and then hands them an item built around it.
Same student, same skill, same minute budget -- genuinely different sessions,
because the policy engine chose differently.

That separation is what makes the learning loop honest. If the tutor always
behaved the same way, the effectiveness estimates in policy.py would be
measuring nothing, and the profile they produce would be decoration.

Four things happen here that a quiz bot does not do:

  * the session opens with a *decision* -- which skill, which approach, and
    why -- rather than a subject menu
  * the approach actually changes the conversation, and the outcome of it is
    measured and fed back
  * wrong answers are diagnosed, not just marked. The bank knows which
    misconception each distractor encodes, so Aria can name the mistake and
    recognise it when it recurs
  * days later, one question comes back to check whether any of it stuck

Message text is kept short and plain: no markdown, no emoji-as-information, no
images. Every reply is a few hundred bytes, which is the point -- this has to
work on a shared phone on a slow connection. None of the policy vocabulary
reaches the student: they are told "I'll walk one through first", never
"posterior" or "Thompson sampling".
"""

import random
import re

import bank
import counterfactual
import interventions as iv_mod
import mastery
import policy
import retention
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
from interventions import INTERVENTION_BY_ID
from skills import SKILL_BY_ID

# Below this many answers the projection is mostly prior, not evidence, so we
# say so rather than showing a confident number the student would over-trust.
MIN_ATTEMPTS_FOR_PROJECTION = 8

HELP_TEXT = (
    "I'm Aria, your SAT coach.\n\n"
    "Just reply A, B, C or D to answer.\n\n"
    "PLAN - what to study and why\n"
    "PROFILE - which practice styles work for you\n"
    "SCORE - your projected score\n"
    "WHY - why I messaged you\n"
    "GOAL 1400 - set your target\n"
    "SKIP - a different question\n"
    "STOP - pause everything\n\n"
    "Or ask me anything about the question."
)

# A student needs at least this many closed episodes before Aria will claim to
# know anything about how they learn. Below it the profile is her prior wearing
# their name, and showing it would be the exact overclaim this layer exists to
# avoid -- the same reason projections are withheld under eight answers.
MIN_EPISODES_FOR_PROFILE = 3


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


def _plan_message(phone: str, minutes: int, states=None,
                  rng=None) -> tuple[str, list[list]]:
    """The opening pitch: which skills, which approach, and why both.

    The old version of this answered "which skills are worth the most minutes".
    It still does -- that ranking is inside the value model -- but it now also
    answers "and what should I actually *do* in those minutes", which is the
    part that varies by student and the part nobody was modelling.

    Everything the student reads here is plain language. The probabilities and
    posteriors behind it are for the coach dashboard and the decision log; a
    sixteen-year-old with nine minutes does not need to be told about
    Thompson sampling to benefit from it.
    """
    states = states or mastery.get_all_states(phone)
    plan = counterfactual.plan(phone, states, minutes, rng=rng)
    if plan.is_empty:
        gains = simulator.marginal_gains(states)
        alloc = [[gains[0].skill_id, None, 99]] if gains else []
        return ("Let's just practise - I'll pick as we go. Reply GO.", alloc)

    header = "1 minute" if minutes == 1 else f"{minutes} minutes"
    lines = [f"{header}. Here's what I'd do with them:", ""]
    for i, block in enumerate(plan.blocks, 1):
        c = block.candidate
        count = f"{c.questions} question" + ("" if c.questions == 1 else "s")
        lines.append(f"{i}. {c.skill_name} - {c.student_label.lower()}")
        lines.append(f"   {count}, {c.minutes:.0f} min, "
                     f"worth about {c.expected_points:.0f} points")
    lines.append("")
    lines.append(f"Total: about +{plan.expected_points:.0f} points that should "
                 f"still be there on test day.")

    lead = plan.blocks[0].decision
    lines.append("")
    lines.append(_why_for_student(phone, lead))

    # The non-obvious part, and the reason this is not just a weakness list:
    # the weakest skill is frequently not worth studying, because it barely
    # appears on the test.
    chosen_ids = {b.candidate.skill_id for b in plan.blocks}
    skipped = [g for g in simulator.marginal_gains(states)
               if g.skill_id not in chosen_ids]
    weakest = min(skipped, key=lambda g: g.current_mastery, default=None)
    top = plan.blocks[0].candidate
    if weakest and weakest.current_mastery < top.mastery_before - 0.02:
        lines.append("")
        lines.append(
            f"Not {weakest.name}, even though you're weaker at it - it comes "
            f"up less often on the test, so it earns fewer points per minute."
        )

    lines.append("")
    lines.append("Ready? Reply GO.")
    return "\n".join(lines), plan.alloc()


def _why_for_student(phone: str, decision) -> str:
    """The reason, in the student's language.

    Two different sentences, and which one Aria gets to say is not a stylistic
    choice -- it is whether she has evidence. Claiming to know what works for
    someone before measuring it is the exact failure this whole layer exists to
    avoid, so the confident sentence is gated on the confident branch of the
    decision.
    """
    c = decision.chosen
    if decision.is_experiment:
        if c.episodes == 0:
            return (f"I know what you're weak at. I don't yet know what helps "
                    f"you improve fastest, so I'm trying something and "
                    f"watching what it does.")
        return (f"I'm still working out which practice style helps you most, "
                f"so this one is partly a test - I'll see how much of it you "
                f"still have in a couple of days.")

    bits = [f"{c.intervention_name.lower()} has been working best for you"]
    if c.retention_checks:
        bits.append(f"when I checked back days later, you still had it "
                    f"{c.expected_retention:.0%} of the time")
    elif c.episodes:
        bits.append(f"you've learned about {c.expected_multiplier:.1f} times as "
                    f"much per question from it")
    return "Why this: " + ", and ".join(bits) + "."


def _begin(phone: str, session, minutes: int, send) -> None:
    """Commit to a duration and hand back the plan for it.

    Two paths arrive here: the student who answered "how many minutes", and
    the student who volunteered it before being asked. They must land in the
    same place -- IDLE, holding a plan -- or the second one ends up parked in
    AWAITING_MINUTES where their next "GO" is read as a duration and fails.
    """
    session.minutes = minutes
    states = mastery.get_all_states(phone)
    if mastery.total_attempts(phone) >= MIN_ATTEMPTS_FOR_PROJECTION:
        session.start_projection = simulator.project(states, n_sims=400).total
    message, alloc = _plan_message(phone, minutes, states)
    session.plan_alloc = alloc
    session.state = TutoringState.IDLE
    session.save()
    send(message)


# --- the intervention episode -------------------------------------------
#
# One episode is one block of the plan: a single approach applied to a single
# skill for a fixed number of questions. It opens with whatever preamble the
# approach calls for, runs its questions, and closes by writing the outcome
# back to policy.py and booking a retention check. That close is the only place
# the learning loop is completed, so every path out of an episode has to reach
# it -- including the student typing STOP halfway through.

def _exclude_set(phone: str) -> set[str]:
    """Items this student must not be served: answered, or already shown to
    them as a worked example. Serving a demonstrated item back as a question
    would measure whether they remember the last five minutes."""
    return mastery.seen_question_ids(phone) | policy.shown_item_ids(phone)


def _top_misconception(phone: str, skill_id: str) -> str | None:
    counts: dict[str, int] = {}
    for m in mastery.recent_misconceptions(phone, skill_id, limit=30):
        counts[m["misconception"]] = counts.get(m["misconception"], 0) + 1
    if not counts:
        return None
    slug, n = max(counts.items(), key=lambda kv: kv[1])
    return slug if n >= 2 else None


def advance(phone: str, session, send) -> None:
    """Send whatever should happen next: a probe, a preamble, or a question.

    Single entry point on purpose. Deciding "close the old episode, then maybe
    answer a retention debt, then maybe open a new episode, then serve" in one
    place is the only way the ordering stays right; spread across the state
    machine it drifted, and an episode that never closed is an episode whose
    evidence was silently thrown away.
    """
    states = mastery.get_all_states(phone)

    # A finished episode closes before anything new begins.
    if session.episode_id and session.episode_done >= session.episode_target:
        summary = _close_episode(phone, session, completed=True)
        if summary:
            send(summary)
        states = mastery.get_all_states(phone)

    if session.episode_id is None:
        # Debts first. A retention check is worth more than a fresh question
        # and costs one item, so it never waits behind a full new episode.
        if _serve_probe(phone, session, send):
            return
        if not _start_episode(phone, session, states, send):
            send("You've worked through everything I have on your priority "
                 "skills. That's genuinely impressive. Say PLAN to pick a new "
                 "focus.")
        return

    question = _serve_question(phone, session, states)
    if question is None:
        # The bank ran dry mid-episode. Close on what was actually answered
        # rather than leaving the episode open forever.
        summary = _close_episode(phone, session, completed=False)
        send(summary or "That's everything I have on that one. Say PLAN.")
        return
    send(question)


def _start_episode(phone: str, session, states, send) -> bool:
    """Open the next block: preamble, then the first question."""
    entry = None
    exclude = _exclude_set(phone)
    for candidate in session.plan_alloc:
        skill_id, iv_id, remaining = candidate[0], candidate[1], candidate[2]
        if remaining <= 0:
            continue
        # Re-check inventory now rather than trusting the count from planning
        # time: the block before this one has been eating items on its own
        # skill, and a worked example burns one more than it practises. A block
        # that can no longer be run is skipped, not started and abandoned.
        iv = INTERVENTION_BY_ID.get(iv_id)
        overhead = iv.demo_items if iv else 0
        room = bank.remaining_for(skill_id, exclude) - overhead
        if room <= 0:
            candidate[2] = 0
            continue
        candidate[2] = min(remaining, room)
        entry = candidate
        break

    if entry is None:
        # Plan exhausted, or nothing left in the bank for its skills. Re-decide
        # rather than falling back to an unreasoned pick: the student still has
        # minutes, and the engine can still say what they are worth.
        decision = counterfactual.decide(phone, states,
                                         float(session.minutes or 10))
        if decision is None:
            return False
        entry = [decision.chosen.skill_id, decision.chosen.intervention,
                 decision.chosen.questions]
        session.plan_alloc.append(entry)
        expected_ppm = decision.chosen.value
        ppm_scale = decision.chosen.points_per_mastery
        mode, p_best = decision.mode, decision.chosen.p_best_intervention
        multiplier = decision.chosen.expected_multiplier
    else:
        expected_ppm = ppm_scale = None
        mode = p_best = multiplier = None

    skill_id, intervention_id = entry[0], entry[1]
    if intervention_id not in INTERVENTION_BY_ID:
        intervention_id = "retrieval_practice"
        entry[1] = intervention_id

    state = states[skill_id]
    if expected_ppm is None:
        # Re-price the block as it starts. The plan may have been drawn up
        # several questions ago against mastery that has since moved -- and
        # without a forecast stored here, this episode contributes nothing to
        # the regret log, which is how that feature came to be dark on the
        # live path while passing its own unit tests.
        expected_ppm, multiplier, ppm_scale = counterfactual.expected_value(
            phone, states, skill_id, intervention_id, max(1, entry[2]))

    session.intervention = intervention_id
    session.episode_skill = skill_id
    session.episode_before = state.p_mastery
    session.episode_target = max(1, entry[2])
    session.episode_done = 0
    session.episode_correct = 0
    session.episode_expected_ppm = expected_ppm
    session.episode_ppm_scale = ppm_scale
    session.episode_id = policy.record_episode(
        phone, intervention_id, skill_id, SKILL_BY_ID[skill_id].domain,
        state.p_mastery, session.episode_target,
        expected_multiplier=multiplier,
        expected_points_per_min=expected_ppm,
        decision_mode=mode, p_best_at_decision=p_best,
    )
    entry[2] = 0     # the whole block is now in flight
    session.save()

    preamble = _preamble(phone, session, states)
    if preamble:
        send(preamble)

    question = _serve_question(phone, session, states)
    if question is None:
        _close_episode(phone, session, completed=False)
        return False
    send(question)
    return True


def _preamble(phone: str, session, states) -> str | None:
    """What the approach says before the first question.

    This is where the interventions stop being labels. Each branch produces a
    materially different opening, and the differences are the thing whose
    effect policy.py measures.
    """
    intervention = session.intervention
    skill_id = session.episode_skill
    skill = SKILL_BY_ID[skill_id]

    if intervention == "worked_example":
        example = bank.pick(skill_id, states[skill_id].p_mastery,
                            exclude=_exclude_set(phone))
        if example is None:
            return None
        policy.mark_shown(phone, example["id"])
        right = bank.index_to_letter(example["correct_index"])
        parts = [f"{skill.name} - let me do one first.", ""]
        if example.get("passage"):
            parts.append(example["passage"])
        parts.append(example["question"])
        parts.append("\n".join(
            f"{bank.LETTERS[i]}) {opt}" for i, opt in enumerate(example["options"])))
        parts.append("")
        parts.append(f"Answer: {right}. {example['explanation']}")
        parts.append("")
        parts.append("Your turn.")
        return "\n".join(parts)

    if intervention == "direct_explanation":
        return (f"{skill.name}. The rule:\n\n"
                f"{iv_mod.skill_principle(skill_id)}\n\n"
                f"Now let's use it.")

    if intervention == "retrieval_practice":
        return (f"{skill.name}. No warm-up on this one - I want to see what "
                f"comes back cold. Getting some wrong is the point.")

    if intervention == "misconception_repair":
        slug = _top_misconception(phone, skill_id)
        why = _misconception_text(phone, skill_id, slug)
        if not why:
            return (f"{skill.name}. There's one thing you keep slipping on - "
                    f"let's go straight at it.")
        return (f"{skill.name}. There's one specific trap you've fallen into "
                f"more than once: {why}\n\n"
                f"The next questions are built around exactly that. Watch for it.")

    if intervention == "timed_drill":
        iv = INTERVENTION_BY_ID[intervention]
        seconds = int(iv.minutes_per_question * 60)
        return (f"{skill.name} - {session.episode_target} questions, about "
                f"{seconds} seconds each. Don't agonise; if you don't see it, "
                f"pick and move on.")

    if intervention == "spaced_review":
        return (f"Quick check on {skill.name} - you had this before. Let's see "
                f"if it's still there.")

    if intervention == "hint_first":
        return None      # the hint rides along with each question instead
    if intervention == "socratic":
        return f"{skill.name}. I'm going to ask you a question before each question."
    return None


def _misconception_text(phone: str, skill_id: str, slug: str | None) -> str | None:
    """A plain-language description of an error, taken from a real distractor.

    Read out of the bank rather than written here, so the wording a student
    sees is the same wording the item author attached to the wrong option.
    """
    if not slug:
        return None
    for question in bank.load():
        if question.get("skill_id") != skill_id:
            continue
        for tag in (question.get("distractors") or {}).values():
            if tag and tag.get("slug") == slug and tag.get("why"):
                return tag["why"].strip().rstrip(".").lower() + "."
    return slug.replace("_", " ") + "."


def _serve_question(phone: str, session, states) -> str | None:
    """Serve one item inside the running episode, decorated by the approach."""
    skill_id = session.episode_skill
    if not skill_id:
        return None
    exclude = _exclude_set(phone)
    prefer = (_top_misconception(phone, skill_id)
              if session.intervention == "misconception_repair" else None)

    question = bank.pick(skill_id, states[skill_id].p_mastery, exclude=exclude,
                         prefer_misconception=prefer)
    if question is None:
        return None

    session.current_skill = skill_id
    session.current_question_id = question["id"]
    session.state = TutoringState.AWAITING_ANSWER

    skill = SKILL_BY_ID[skill_id]
    n = session.episode_done + 1
    header = f"{skill.name} ({question['difficulty']})"
    if session.episode_target > 1:
        header += f" - {n} of {session.episode_target}"

    if session.intervention == "socratic":
        # Hold the options back until the student has said what the question is
        # asking. The item is already chosen and stored, so their reply lands
        # on the right question.
        session.state = TutoringState.AWAITING_LEAD_IN
        session.save()
        parts = [header]
        if question.get("passage"):
            parts.append(question["passage"])
        parts.append(question["question"])
        parts.append(iv_mod.socratic_lead_in(skill_id))
        return "\n\n".join(parts)

    session.save()
    text = bank.format_question(question, header=header)
    if session.intervention == "hint_first":
        text += f"\n\nHint: {iv_mod.skill_hint(skill_id)}"
    return text


def _close_episode(phone: str, session, completed: bool) -> str | None:
    """Write the outcome back, book the retention check, clear the episode.

    Called from every exit: a finished block, an exhausted bank, a student
    typing STOP. An episode that stays open is evidence that was collected and
    then dropped, which is worse than not collecting it -- the model would go
    on believing whatever it believed before, with no record that it had been
    tested.
    """
    episode_id = session.episode_id
    if not episode_id:
        return None
    skill_id = session.episode_skill
    intervention = session.intervention or "retrieval_practice"
    iv = INTERVENTION_BY_ID[intervention]
    answered = session.episode_done

    after = (mastery.get_state(phone, skill_id).p_mastery
             if skill_id else (session.episode_before or 0.0))
    minutes = iv.minutes_for(answered) if answered else iv.setup_minutes

    ep = policy.close_episode(
        episode_id, mastery_after=after, minutes=minutes,
        answered=answered, correct=session.episode_correct,
        completed=completed and answered >= session.episode_target,
        points_per_mastery=session.episode_ppm_scale,
    )

    session.episode_id = None
    session.episode_skill = None
    session.intervention = None
    session.episode_target = 0
    session.episode_done = 0
    session.episode_correct = 0
    # Drop the item on screen with the episode. Left set, the next thing the
    # student types is read as an answer to a question belonging to a block
    # that no longer exists -- and it would be scored against the skill and
    # intervention of a session they have already left.
    session.current_question_id = None
    session.current_skill = None
    session.save()

    if ep is None or answered == 0:
        return None

    gain = after - (session.episode_before or after)
    if skill_id:
        retention.schedule(phone, episode_id, intervention, skill_id, after,
                           gain=gain)

    # Student-facing: what the approach bought, not what the model learned.
    if gain > 0.02:
        return (f"That block moved {SKILL_BY_ID[skill_id].name} to "
                f"{_fmt_mastery(after)}. I'll check back on it in a couple of "
                f"days - that's how I find out whether it really stuck.")
    return None


def _serve_probe(phone: str, session, send) -> bool:
    """If a retention check is due, ask it. Returns True if one was sent."""
    probes = retention.due(phone)
    if not probes:
        return False
    probe = probes[0]
    states = mastery.get_all_states(phone)
    question = bank.pick(probe.skill_id, states[probe.skill_id].p_mastery,
                         exclude=_exclude_set(phone))
    if question is None:
        # Nothing unseen left on that skill to ask with. Retire the probe
        # unresolved rather than leaving it due forever and blocking every
        # future session behind it -- and record no retention observation,
        # because a check that was never asked is not evidence either way.
        retention.retire(probe.id)
        return False

    retention.mark_asked(probe.id, question["id"])
    session.probe_id = probe.id
    session.current_skill = probe.skill_id
    session.current_question_id = question["id"]
    session.state = TutoringState.AWAITING_ANSWER
    session.save()

    skill = SKILL_BY_ID[probe.skill_id]
    send(f"Before anything new - one question on {skill.name} from a few days "
         f"ago. I'm not testing you, I'm testing whether what we did actually "
         f"stuck.")
    send(bank.format_question(question, header=f"{skill.name} (check-in)"))
    return True


def _diagnose(phone: str, question: dict, chosen_index: int, skill_id: str) -> str:
    """Feedback that names the specific error, and notices when it repeats."""
    correct_index = question["correct_index"]
    if chosen_index == correct_index:
        return f"Correct. {question['explanation']}"

    tag = bank.misconception_for(question, chosen_index)
    right = bank.index_to_letter(correct_index)
    parts = [f"Not quite - the answer is {right}."]

    if tag:
        # The bank writes rationales in the third person ("fails to consider
        # the impact"), so "You {why}" produces "You fails to consider". Frame
        # it as a property of the option instead, which reads correctly for
        # every phrasing the generator produces.
        why = tag["why"].strip().rstrip(".")
        parts.append(f"That option {why}.")
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
        # Close any episode in flight before the session is discarded. Walking
        # away *is* an observation -- it is the engagement signal -- and losing
        # it would leave the model believing an intervention was completed when
        # the student abandoned it.
        open_session = get_session(phone)
        if open_session and open_session.episode_id:
            _close_episode(phone, open_session, completed=False)
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

    # "my name is Eman" should work whenever they say it, not only while the
    # state machine happens to be asking. Skipped while awaiting a name, where
    # the state handler deals with it and can re-prompt.
    if not (session and session.state == TutoringState.AWAITING_NAME):
        stated = _NAME_STATEMENT.search(text)
        if stated:
            new_name = stated.group(1).title()
            if new_name.lower() not in GO_WORDS:
                student_mod.update(phone, name=new_name)
                send(f"Got it - {new_name} it is.")
                return

    if lower in ("plan", "what should i study"):
        minutes = (session.minutes if session else None) or 20
        # Re-planning abandons whatever block was running, so close it on what
        # was actually answered. Otherwise the next episode opens on top of an
        # unclosed one and the first block's evidence is lost.
        if session and session.episode_id:
            _close_episode(phone, session, completed=False)
        message, alloc = _plan_message(phone, minutes)
        if session:
            session.plan_alloc = alloc
            session.state = TutoringState.IDLE
            session.save()
        send(message)
        return

    if lower in ("profile", "how i learn", "what works for me"):
        send(_learning_profile_report(phone))
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
                     f"about {days} day{'' if days == 1 else 's'} of "
                     f"consistent work gets you there.")
        else:
            send("Tell me a score between 400 and 1600, like: GOAL 1400")
        return

    # --- new student -----------------------------------------------------
    if session is None:
        session = create_session(phone)
        # Whatever they opened with is still information. "I have 20 minutes"
        # as a first message used to be answered with the greeting and nothing
        # else, and then the minutes were asked for again a few turns later --
        # which is the student watching Aria ignore what they just said.
        opening_minutes = parse_minutes(text)
        if opening_minutes:
            session.minutes = opening_minutes

        if profile.name and opening_minutes:
            # Knows them, and they led with the one thing still needed. There
            # is nothing left to ask, so plan instead of making conversation.
            send(f"Welcome back, {profile.name}.")
            _begin(phone, session, opening_minutes, send)
        elif profile.name:
            session.state = TutoringState.AWAITING_MINUTES
            session.save()
            send(f"Welcome back, {profile.name}. "
                 f"How many minutes do you have today?")
        elif opening_minutes:
            session.save()
            send(f"Hi! I'm Aria, your SAT coach. {opening_minutes} minutes is "
                 f"enough to be worth spending well.\n\nWhat should I call you?")
        else:
            send("Hi! I'm Aria, your SAT coach. What should I call you?")
        return

    # --- state machine ---------------------------------------------------
    if session.state == TutoringState.AWAITING_NAME:
        name = _extract_name(text)
        if not name or lower in ("hi", "hello", "hey", "yes", "no", "ok"):
            # Same dead end as the minutes prompt: repeating the question
            # forever is not a clarification. A name is optional anyway.
            session.confusions += 1
            if session.confusions < 3:
                session.save()
                off = _offscript_reply(phone, session, text) \
                    if _looks_offscript(text) else None
                send(off or ("What's your name?" if session.confusions == 1
                             else "What should I call you? Any name is fine."))
                return
            session.confusions = 0
            session.state = TutoringState.AWAITING_TARGET
            session.save()
            send("No worries, I'll skip that. What score are you aiming for? "
                 "(Reply with a number like 1200, or SKIP.)")
            return
        session.confusions = 0
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
        # They told us at the door. Asking again would be the same insult in
        # a different place -- and leaving them in AWAITING_MINUTES after they
        # already answered means their next "GO" fails to parse as a duration.
        if session.minutes:
            _begin(phone, session, session.minutes, send)
            return
        session.state = TutoringState.AWAITING_MINUTES
        session.save()
        send("How many minutes do you have to study today?")
        return

    if session.state == TutoringState.AWAITING_MINUTES:
        minutes = parse_minutes(text)
        if not minutes:
            # Never ask the same question twice the same way. A student who
            # cannot phrase it the way the parser wants is stuck forever
            # otherwise, and they are the student least likely to persist.
            session.confusions += 1
            if session.confusions < 3:
                session.save()
                off = _offscript_reply(phone, session, text) \
                    if _looks_offscript(text) else None
                if off:
                    send(off)
                elif session.confusions == 1:
                    send("Roughly how long? Something like '20 minutes' or '1 hour'.")
                else:
                    send("No problem - just reply with a number.\n\n"
                         "5 = five minutes\n15 = a quarter of an hour\n60 = an hour")
                return
            # Third failure: stop asking and start working. Ten minutes is
            # enough for a real plan, and they can say PLAN to change it.
            minutes = 10
            send("Let's just start with 10 minutes - say PLAN any time to "
                 "change it.")
        session.confusions = 0
        _begin(phone, session, minutes, send)
        return

    if session.state == TutoringState.IDLE:
        if lower in ("no", "not now", "later"):
            send("No problem. Say GO when you're ready.")
            return
        # Only an actual go-ahead serves a question. Treating every message as
        # "yes, next question" meant a student asking "why do you need to know
        # how long?" got a reading passage back instead of an answer.
        if lower not in GO_WORDS and _looks_offscript(text):
            off = _offscript_reply(phone, session, text)
            if off:
                send(off)
                return
        advance(phone, session, send)
        return

    if session.state == TutoringState.AWAITING_LEAD_IN:
        # Socratic: they were asked what the question is looking for. Any
        # answer counts -- the value is in having articulated it, and grading a
        # free-text reply with a regex would fail the students who most need
        # the scaffold.
        question = bank.get(session.current_question_id or "")
        if question is None:
            session.state = TutoringState.IDLE
            session.save()
            send("Lost track of that one - say GO for a fresh question.")
            return
        session.state = TutoringState.AWAITING_ANSWER
        session.save()
        send("Good - hold onto that. Here are the options.\n\n"
             + "\n".join(f"{bank.LETTERS[i]}) {opt}"
                         for i, opt in enumerate(question["options"])))
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
            # than punish a student for being honest that they don't know. It
            # does not consume the episode's question budget either -- an
            # intervention should not be judged on items nobody attempted.
            states = mastery.get_all_states(phone)
            replacement = _serve_question(phone, session, states)
            send(replacement or "That's everything I have for now. Say PLAN.")
            return

        index = bank.letter_to_index(text)
        if index is None:
            send(_answer_followup(question, text))
            return

        _apply_answer(phone, session, question, index, send)
        return

    # Fallback: something unexpected. Answer it rather than reciting the menu.
    session.state = TutoringState.IDLE
    session.save()
    off = _offscript_reply(phone, session, text) if _looks_offscript(text) else None
    send(off or "Say GO for a question, PLAN for what to study, or HELP for options.")


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
        source="probe" if session.probe_id else "chat",
    )

    session.asked += 1
    session.correct += 1 if correct else 0

    skill = SKILL_BY_ID[skill_id]
    movement = (f"{skill.name}: {_fmt_mastery(before.p_mastery)} "
                f"-> {_fmt_mastery(after.p_mastery)}")

    parts = [feedback, "", movement]

    # A retention check is not part of any episode's question budget: it is the
    # verdict on an episode that already closed. Resolving it here is what
    # turns "they got it right today" into "the approach that taught it holds
    # up", which is the distinction the whole retention layer exists for.
    if session.probe_id:
        probe = retention.resolve(session.probe_id, correct)
        session.probe_id = None
        if probe:
            iv = INTERVENTION_BY_ID.get(probe.intervention)
            label = iv.name.lower() if iv else "that session"
            parts.append("")
            parts.append(
                f"You still had it - that tells me {label} works for you."
                if correct else
                f"That one faded. Worth knowing: it means {label} taught it "
                f"more shallowly than it looked at the time.")
    elif session.episode_id:
        session.episode_done += 1
        session.episode_correct += 1 if correct else 0

    # Every few questions, show the thing they are actually here for.
    if session.asked % 4 == 0:
        parts.append("")
        parts.append(_projection_line(phone))

    parts.append("")
    parts.append("Reply GO for the next one.")

    session.state = TutoringState.IDLE
    session.current_question_id = None
    session.current_skill = None
    session.save()
    send("\n".join(parts))


GO_WORDS = {"go", "yes", "y", "ok", "okay", "ready", "next", "sure", "start",
            "yep", "yeah", "continue", "more", "another"}

# "Eman", "Mary-Jane", "Ana Sofia" -- but not "what is the SAT out of?"
_NAME_SHAPE = re.compile(r"^[A-Za-z][A-Za-z'\-]{1,20}(?: [A-Za-z][A-Za-z'\-]{1,20})?$")
_NAME_STATEMENT = re.compile(
    r"\b(?:my name is|i'?m called|call me|it'?s|i am|im)\s+"
    r"([A-Za-z][A-Za-z'\-]{1,20})", re.I)


def _extract_name(text: str) -> str | None:
    """Pull a usable name out of a message, or None if there isn't one.

    Previously any string of two or more characters was accepted, so asking
    "what is the SAT out of?" got you greeted as What Is The Sat Out Of. A
    name has a shape, and anything that does not fit it is a message to answer
    rather than a name to store.
    """
    stripped = text.strip().rstrip(".!")
    statement = _NAME_STATEMENT.search(stripped)
    if statement:
        return statement.group(1).title()
    if "?" in stripped:
        return None
    if _NAME_SHAPE.match(stripped):
        return stripped.title()
    return None


_EXPECTED = {
    "awaiting_name": "what to call them",
    "awaiting_target": "the score they're aiming for",
    "awaiting_minutes": "how many minutes they have to study",
    "awaiting_answer": "an answer of A, B, C or D to the question on screen",
    "idle": "whether they want another question",
}


def _looks_offscript(text: str) -> bool:
    """Is this a real utterance rather than a mistyped answer?

    Deliberately generous. Treating a genuine question as noise and repeating
    a prompt at someone is the failure worth avoiding; answering something
    that turned out to be a typo costs nothing.
    """
    stripped = text.strip()
    if "?" in stripped:
        return True
    if len(stripped.split()) >= 3:
        return True
    return bool(re.match(
        r"^(what|why|how|who|when|where|can|do|does|is|are|should|will|"
        r"help|explain|sorry|wait|huh|idk|dunno)\b", stripped, re.I))


def _offscript_reply(phone: str, session, text: str) -> str | None:
    """Answer what the student actually said, then steer back.

    The state machine expects one specific thing at each step, and anything
    else used to fall through to that step's canned prompt -- so a student who
    asked "what's the SAT out of?" got "What's your name?" back, forever. This
    is the escape hatch: respond to the message on its own terms, then re-ask.
    """
    from agent import chat_text

    state = session.state.value if session else "idle"
    expecting = _EXPECTED.get(state, "whether they want another question")

    context = [f"You are waiting for: {expecting}."]
    profile = student_mod.get(phone)
    if profile and profile.name:
        context.append(f"The student's name is {profile.name}.")
    # Only mention a target the student actually chose. student.py falls back
    # to a default, and quoting it back as "your 1200" to someone who never
    # named a target is the kind of small invention that costs trust.
    from student import DEFAULT_TARGET_SCORE
    if profile and profile.target_score \
            and profile.target_score != DEFAULT_TARGET_SCORE:
        context.append(f"Their target score is {profile.target_score}.")
    else:
        context.append("They have not told you a target score yet, so do not "
                       "refer to one.")
    if session and session.current_question_id:
        question = bank.get(session.current_question_id)
        if question:
            context.append("The question on their screen is: "
                           + question["question"][:300])

    reply = chat_text(
        system=(
            "You are Aria, an SAT coach messaging a student on WhatsApp. "
            "The student said something you were not expecting. Answer them "
            "directly and warmly in at most two short sentences, then ask "
            "again for the thing you need. Plain text only: no markdown, no "
            "emoji, no bullet points, under 300 characters. Never invent SAT "
            "scores or statistics. If you do not know, say so plainly.\n\n"
            + " ".join(context)
        ),
        user=text,
        max_tokens=500,
    )
    return reply.strip() if reply else None


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
        lines.append(f"About {days} day{'' if days == 1 else 's'} at "
                     f"20 min/day would get you there.")

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


def _learning_profile_report(phone: str) -> str:
    """"Which practice styles help me?" -- answered from evidence or not at all.

    Deliberately free of numbers a student would over-read. Aria does not tell
    them a multiplier or a probability; she tells them what she has noticed and
    how many sessions it rests on, which is the honest shape of the claim.
    """
    episodes = policy.episodes_for(phone)
    if len(episodes) < MIN_EPISODES_FOR_PROFILE:
        left = MIN_EPISODES_FOR_PROFILE - len(episodes)
        return ("I'm still learning which practice style helps you most. "
                f"Give me {left} more session{'s' if left != 1 else ''} and "
                "I'll be able to tell you something real about it.")

    ranked = [e for e in policy.profile(phone) if e.episodes > 0]
    if not ranked:
        return ("I'm still learning which practice style helps you most.")

    lines = ["What I've noticed about how you learn:", ""]
    for est in ranked[:4]:
        bar = "#" * max(1, min(10, round(est.durable_effectiveness * 5)))
        lines.append(f"{est.name}")
        lines.append(f"  {bar}  ({est.episodes} session"
                     f"{'s' if est.episodes != 1 else ''}"
                     + (f", {est.retention_checks} check-in"
                        f"{'s' if est.retention_checks != 1 else ''}"
                        if est.retention_checks else "") + ")")
    best = ranked[0]
    lines.append("")
    lines.append(f"Best for you so far: {best.name.lower()}.")
    if best.retention_checks:
        lines.append(f"When I checked back days later you'd kept it "
                     f"{best.retention.mean:.0%} of the time.")
    lines.append("")
    lines.append("This is what I've seen in your sessions, not a personality "
                 "type. If it changes, I'll change with it.")
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
