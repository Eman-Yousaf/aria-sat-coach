"""The demo where Aria works out how a student learns.

    python discover.py            the whole arc, about three minutes to read
    python discover.py --short    the same run, decisions only

Nothing here is staged. The policy engine, the tutor, the mastery model, the
question bank and the retention scheduler are the shipping ones, running
against a real SQLite file. What is synthetic is the *student*: a person cannot
be summoned for a demo, so `SimulatedLearner` stands in for one.

That simulation is the honest part of the fixture and it is worth being precise
about what it does and does not do. It has hidden true response rates -- this
learner really does answer better after a worked example and really does fall
apart under time pressure, and really does forget what a timed drill taught
them. Aria is told none of it. She sees only answers, and everything she
concludes she has to derive from the same BKT updates a real student would
generate.

So the numbers on screen are not scripted. If you change the hidden truth at
the top of this file, Aria reaches a different conclusion, and she reaches it
by the same route.

The one place the fiction shows: a real student's response to an intervention
is not a fixed probability, and two days of forgetting is not a coin weighted
by which approach taught them. Those are stand-ins for effects that are real
but whose magnitudes nobody has measured for an individual. They are what makes
this a demonstration of the mechanism rather than evidence about pedagogy.
"""

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PHONE = "15550009001"
SEED = 20260814

# --- the hidden student ---------------------------------------------------
#
# Aria never reads this. It exists only so that the answers she sees come from
# something with a real pattern in it, rather than from a coin.
#
# The pattern chosen is deliberately one that contradicts a plausible pedagogy
# rule: this student learns most from worked examples, retains best from cold
# retrieval, and is actively harmed by timed practice -- which is the cheapest
# option per question and therefore the one Aria's *prior* prefers. If the
# engine only ever confirmed its priors, this fixture would catch it.

TRUE_ACCURACY = {
    "worked_example":      0.85,   # sees one solved, then gets them right
    "retrieval_practice":  0.50,   # struggles cold, which is the point of it
    "direct_explanation":  0.45,   # long explanations wash over this student
    "socratic":            0.60,
    "misconception_repair": 0.75,
    "hint_first":          0.60,   # the nudge carries them, shallowly
    "timed_drill":         0.30,   # falls apart against a clock
    "spaced_review":       0.65,
}

# P(still has it at a delayed check). Effortful retrieval retains; being handed
# the answer does not. Timed drills teach almost nothing durable here.
#
# The two numbers for `hint_first` are the interesting pair, and they are why
# retention is modelled separately rather than folded into one score: a hint
# makes today's answers look good and leaves nothing behind. A system watching
# only immediate performance would rank it highly and keep prescribing it.
TRUE_RETENTION = {
    "worked_example":      0.85,
    "retrieval_practice":  0.90,
    "direct_explanation":  0.30,
    "socratic":            0.65,
    "misconception_repair": 0.75,
    "hint_first":          0.25,
    "timed_drill":         0.20,
    "spaced_review":       0.80,
}

# The gaps above are wider than a real student's would be. That is a
# deliberate property of a fixture: the demo has to make a mechanism visible
# inside a fortnight of simulated study, and separating two approaches that
# differ by five percentage points takes hundreds of episodes, not thirty.
# Narrow the gaps and Aria takes correspondingly longer to be sure -- which is
# the correct behaviour, and is exactly what the uncertainty column reports.

BASE_ACCURACY = 0.45     # used for anything outside an intervention episode


class SimulatedLearner:
    """Answers questions the way a person with these hidden traits would."""

    def __init__(self, seed: int):
        self.rng = random.Random(seed)

    def answer(self, phone: str) -> str:
        import bank
        from conversation import get_session

        session = get_session(phone)
        if not session or not session.current_question_id:
            return "GO"
        question = bank.get(session.current_question_id)
        if not question:
            return "GO"

        if session.probe_id:
            accuracy = self._probe_accuracy(session.probe_id)
        else:
            accuracy = TRUE_ACCURACY.get(session.intervention, BASE_ACCURACY)

        if self.rng.random() < accuracy:
            return bank.index_to_letter(question["correct_index"])
        wrong = [i for i in range(4) if i != question["correct_index"]]
        return bank.index_to_letter(self.rng.choice(wrong))

    def _probe_accuracy(self, probe_id: int) -> float:
        """A delayed check is decided by what taught the skill, not by today."""
        import retention
        probe = retention.get(probe_id)
        if not probe:
            return BASE_ACCURACY
        return TRUE_RETENTION.get(probe.intervention, BASE_ACCURACY)


# --- presentation ---------------------------------------------------------

W = 74


def rule(char="="):
    print(char * W)


def head(title: str):
    print()
    rule()
    print(f"  {title}")
    rule()


def bar(value: float, scale: float = 5.0, width: int = 12) -> str:
    filled = max(0, min(width, round(value * scale)))
    return "#" * filled + "." * (width - filled)


def show_profile(phone: str, title: str):
    import policy
    print()
    print(f"  {title}")
    print()
    print(f"  {'':<30}{'durable':>9}  {'':<13}{'evidence'}")
    for est in policy.profile(phone):
        conf = f"{est.confidence:.0%}" if est.episodes else "-"
        detail = (f"{est.episodes}ep" if est.episodes else "no data")
        if est.retention_checks:
            detail += f" / {est.retention_checks}chk {est.retention.mean:.0%}"
        print(f"  {est.name[:29]:<30}{est.durable_effectiveness:>9.2f}  "
              f"{bar(est.durable_effectiveness):<13}{detail:<18}conf {conf}")
    print()
    print("  'durable' = learning per question relative to average practice,")
    print("  multiplied by the share of it that survives a delayed check.")
    print("  1.00 would be an average question fully retained.")


def show_decision(decision, label="ARIA'S DECISION"):
    import counterfactual
    c = decision.chosen
    print()
    print(f"  {label}   [{decision.mode.upper()}]")
    print(f"    Skill         {c.skill_name}")
    print(f"    Intervention  {c.intervention_name}")
    print(f"    Duration      {c.minutes:.0f} minutes ({c.questions} questions)")
    print(f"    Expected      +{c.expected_points:.1f} durable points "
          f"({c.value:.2f}/min, 80% range {c.value_low:.2f}-{c.value_high:.2f})")
    print(f"    Retention     {c.expected_retention:.0%} expected to survive "
          f"a delayed check")
    print(f"    Risk          {c.p_failure:.0%} chance this buys almost nothing")
    print()
    print("    Why:")
    for reason in decision.reasons:
        for line in _wrap(reason, W - 10):
            print(f"      - {line}" if line == _wrap(reason, W - 10)[0]
                  else f"        {line}")
    print()
    print(f"    {'':<24}{'skill':<26}{'pts/min':>9}{'p(best)':>9}")
    for row, cand in zip(counterfactual.shadow_table(decision),
                         [c] + decision.alternatives):
        tag = "CHOSEN" if not row["estimated"] else "counterfactual estimate"
        print(f"    {tag:<24}{row['skill'][:25]:<26}{row['value']:>9.2f}"
              f"{cand.p_best_intervention:>9.0%}  {row['intervention'][:26]}")
    print()
    print("    Rows below the first describe interventions that were not run.")
    print("    They are model estimates, not measurements.")


def _wrap(text: str, width: int) -> list[str]:
    words, lines, current = text.split(), [], ""
    for word in words:
        if len(current) + len(word) + 1 > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines or [""]


# --- the run --------------------------------------------------------------

def reset(phone: str):
    import sqlite3

    import conversation
    from config import DB_PATH
    conn = sqlite3.connect(DB_PATH)
    for table in ("sessions", "students", "skill_mastery", "attempts",
                  "agent_decisions", "processed_replies",
                  "intervention_episodes", "retention_probes", "shown_items"):
        try:
            conn.execute(f"DELETE FROM {table} WHERE phone = ?", (phone,))
        except sqlite3.OperationalError:
            pass
    conn.commit()
    conn.close()
    conversation._sessions.pop(phone, None)


def rewind(phone: str, days: float):
    """Age every row for this student, the way demo.py does.

    Faking the clock instead would mean demoing code paths that never run: the
    retention scheduler, the decay curve and the autonomy triggers are all
    functions of elapsed time. Moving the rows means the student has genuinely
    been away, and the real thresholds fire unmodified.
    """
    import sqlite3
    from datetime import timedelta

    import mastery
    from config import DB_PATH

    delta = timedelta(days=days)

    def back(ts):
        parsed = mastery._parse(ts)
        return (parsed - delta).isoformat() if parsed else ts

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    tables = (("skill_mastery", ("last_seen",)),
              ("attempts", ("at",)),
              ("students", ("created_at", "last_active")),
              ("agent_decisions", ("decided_at",)),
              ("intervention_episodes", ("started_at", "closed_at")),
              ("retention_probes", ("due_at",)))
    for table, columns in tables:
        try:
            rows = conn.execute(
                f"SELECT rowid AS _rid, * FROM {table} WHERE phone = ?",
                (phone,)).fetchall()
        except sqlite3.OperationalError:
            continue
        for row in rows:
            for col in columns:
                if col not in row.keys() or row[col] is None:
                    continue
                conn.execute(f"UPDATE {table} SET {col} = ? WHERE rowid = ?",
                             (back(row[col]), row["_rid"]))
    conn.commit()
    conn.close()


def session(phone: str, learner: SimulatedLearner, minutes: int,
            verbose: bool = False, max_turns: int = 30) -> list[str]:
    """One study session, driven through the real tutor.

    Bounded by the number of blocks the planner actually allocated, not by a
    turn count. A student with twenty minutes gets the two blocks that fit in
    twenty minutes and then stops; letting the loop run until it ran out of
    patience produced sessions of eleven episodes, which is not a study session
    and would have made the evidence base a fiction of the harness rather than
    of the plan.
    """
    import policy
    import tutor
    from conversation import get_session

    transcript: list[str] = []

    def send(text: str):
        transcript.append(text)
        if verbose:
            print()
            for line in text.split("\n"):
                print(f"    | {line}")

    def say(message: str):
        if verbose:
            print(f"\n    [Priya] {message}")
        tutor.handle(phone, message, send)

    closed_before = len(policy.episodes_for(phone))
    probes_before = _resolved_probe_count(phone)

    if get_session(phone) is None:
        for message in ("hi", "Priya", "1350", f"{minutes} minutes"):
            say(message)
    else:
        say("PLAN")

    current = get_session(phone)
    blocks = len(current.plan_alloc) if current else 1

    for _ in range(max_turns):
        say(learner.answer(phone))
        done = len(policy.episodes_for(phone)) - closed_before
        probed = _resolved_probe_count(phone) - probes_before
        if done >= blocks:
            break
        # A session that opened on a retention check and has nothing planned
        # after it is finished once the check is answered.
        if blocks == 0 and probed:
            break
        # The plan can also run out early -- the bank is thin on some skills,
        # so a block sometimes cannot be run at all. Stop rather than letting
        # the tutor improvise fresh blocks until the turn cap, which would turn
        # a twenty-minute session into eleven episodes of evidence.
        current = get_session(phone)
        if current and current.episode_id is None and current.plan_alloc \
                and all(e[2] <= 0 for e in current.plan_alloc):
            break
    return transcript


def _resolved_probe_count(phone: str) -> int:
    import retention
    kept, lost = retention.outcome_counts(phone)
    return kept + lost


def run(short: bool = False):
    import random as _random
    _random.seed(SEED)

    import bank
    import counterfactual
    import mastery
    import policy
    import retention
    from database import init_db

    init_db()
    if not bank.is_available():
        print("No question_bank.json. Build it first: python bank_build.py --pilot")
        return

    reset(PHONE)
    learner = SimulatedLearner(SEED)
    rng = _random.Random(SEED)

    head("1. A student Aria has never met")
    print()
    print("  Priya has answered nothing. Aria has a knowledge model with no")
    print("  evidence in it, and -- the part that is new -- a model of what")
    print("  helps her, which is equally empty.")
    show_profile(PHONE, "LEARNING RESPONSE PROFILE - before any evidence")
    print()
    print("  Every approach sits at the same value. That is not a placeholder:")
    print("  it is the prior, and it is identical by construction so that any")
    print("  ordering Aria ends up with has to be paid for with observations.")

    head("2. Aria decides what to do with 20 minutes")
    states = mastery.get_all_states(PHONE)
    first = counterfactual.decide(PHONE, states, 20, rng=rng)
    show_decision(first)
    print()
    print("  Note the mode: EXPLORE. Aria is not pretending to know. The")
    print("  cheapest-per-question approach leads only because her cost model")
    print("  says so, and a lead she inherited from her own priors is not")
    print("  something she knows about Priya.")

    head("3. A week of short sessions")
    print()
    print("  Driven through the real tutor. Priya answers the way the hidden")
    print("  learner at the top of this file answers -- Aria sees only the")
    print("  letters. Between study days the clock really moves, so mastery")
    print("  decays and the delayed checks Aria booked come due on their own.")

    schedule = [(1, [20, 18]), (3, [16, 20]), (5, [18, 16]),
                (7, [20, 18]), (9, [16, 20])]
    for index, (day, budgets) in enumerate(schedule):
        if index:
            rewind(PHONE, 2.0)
        before = len(policy.episodes_for(PHONE))
        checks_before = _resolved_probe_count(PHONE)
        for minutes in budgets:
            session(PHONE, learner, minutes, verbose=(not short and index == 0
                                                      and minutes == budgets[0]))
        new = len(policy.episodes_for(PHONE)) - before
        checks = _resolved_probe_count(PHONE) - checks_before
        print(f"\n  Day {day}: {len(budgets)} session(s), {new} episodes"
              + (f", {checks} delayed check(s) answered" if checks else ""))

    print()
    print("  Every episode, in order:")
    print()
    print(f"    {'approach':<24}{'skill':<26}{'score':>7}   learned")
    for ep in policy.episodes_for(PHONE):
        print(f"    {ep.intervention:<24}{ep.skill_id[:25]:<26}"
              f"{ep.correct}/{ep.answered:<5}"
              f"{ep.learning_multiplier:>7.2f}x average per question")

    head("4. The delayed checks are the verdict")
    print()
    print("  This is what separates learning from performing. Five right")
    print("  straight after an explanation proves the explanation was still")
    print("  on screen. Aria books a check for two days later, and what comes")
    print("  back is what she actually believes.")
    print()
    summary = retention.summary(PHONE)
    print(f"    resolved {summary['resolved']}   kept {summary['kept']}   "
          f"lost {summary['lost']}   still pending {summary['pending']}")
    print()
    for iv, (kept, lost) in _retention_by_intervention(PHONE).items():
        print(f"    {iv:<24}{kept} kept / {kept + lost} checked")

    print()
    print("  And what Aria decides to do unprompted, scored against itself:")
    print()
    import autonomy
    for d in sorted(autonomy.evaluate(PHONE), key=lambda x: -x.score)[:4]:
        print(f"    {d.score:6.1f}  {d.trigger}")
        for line in _wrap(d.reason, W - 14):
            print(f"            {line}")
        print(f"            evidence: {d.evidence}")

    head("5. The Learning Response Profile, paid for with evidence")
    show_profile(PHONE, "LEARNING RESPONSE PROFILE - after a week")

    head("6. The same question, asked again")
    states = mastery.get_all_states(PHONE)
    later = counterfactual.decide(PHONE, states, 20, rng=_random.Random(SEED))
    show_decision(later, "ARIA'S DECISION - 20 minutes, same as step 2")

    print()
    if later.chosen.intervention != first.chosen.intervention:
        print(f"  Step 2 chose {first.chosen.intervention_name.lower()}.")
        print(f"  Step 7 chose {later.chosen.intervention_name.lower()}.")
        print()
        print("  Nothing in the code changed between them. The decision moved")
        print("  because the evidence did.")
    else:
        print(f"  Aria stayed with {later.chosen.intervention_name.lower()} -")
        print("  the evidence supported what she started with.")

    head("7. Time is the other half of the decision")
    print()
    print("  The best approach is not the best approach at every budget. A")
    print("  worked example costs three minutes before the first question, and")
    print("  there are sessions where those three minutes are the session.")
    print()
    for minutes in (25, 12, 8, 5):
        d = counterfactual.decide(PHONE, mastery.get_all_states(PHONE), minutes,
                                  rng=_random.Random(SEED))
        if d is None:
            continue
        print(f"    {minutes:>3} min  ->  {d.chosen.intervention_name[:28]:<30}"
              f"{d.chosen.questions}q  {d.chosen.minutes:>4.0f}min  "
              f"{d.chosen.value:>5.2f} pts/min")

    head("8. What Aria got wrong")
    print()
    print("  Every episode was chosen against a forecast. Comparing the two is")
    print("  policy feedback, not causal inference -- it cannot separate a bad")
    print("  choice from a bad day, and with one student nothing can.")
    print()
    rows = policy.regret_log(PHONE, limit=6)
    if not rows:
        print("    (no closed episodes carried a forecast)")
    else:
        print(f"    {'intervention':<24}{'expected':>10}{'observed':>10}"
              f"{'gap':>9}   mode")
        for r in rows:
            print(f"    {r['name'][:23]:<24}{r['expected']:>10.2f}"
                  f"{r['observed']:>10.2f}{r['regret']:>9.2f}   {r['mode']}")

    head("The claim")
    print()
    print("  Aria did not just work out what Priya does not know.")
    print("  She worked out how Priya learns, by running experiments,")
    print("  measuring what survived two days, and changing her own")
    print("  teaching strategy when the evidence came back.")
    print()
    best = policy.profile(PHONE)[0]
    print(f"  For Priya: {best.name.lower()}.")
    print(f"  On the evidence of {best.episodes} sessions and "
          f"{best.retention_checks} delayed checks.")
    print()
    print("  Ask her again in a week with different evidence and she will")
    print("  tell you something else.")
    print()


def _retention_by_intervention(phone: str) -> dict:
    import retention
    from interventions import INTERVENTIONS
    out = {}
    for iv in INTERVENTIONS:
        kept, lost = retention.outcome_counts(phone, iv.id)
        if kept + lost:
            out[iv.id] = (kept, lost)
    return out


if __name__ == "__main__":
    run(short="--short" in sys.argv)
