"""Offline demo: the full agent loop with no WhatsApp and no network.

    python demo.py                     scripted student, prints the conversation
    python demo.py --chat              talk to Aria yourself in the terminal
    python demo.py --gains             price every skill in points per minute
    python demo.py --autonomy          skip 4 days, watch Aria decide to speak
    python demo.py --autonomy --days 9 ... with a longer gap

Uses the real tutor, the real mastery model and the real question bank, so what
you see here is what a student gets. Only the transport is faked.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DEMO_PHONE = "15550000001"


def _sender(label="Aria"):
    total = {"bytes": 0}

    def send(text: str):
        total["bytes"] += len(text.encode("utf-8"))
        print(f"\n[{label}]")
        for line in text.split("\n"):
            print(f"  {line}")
    return send, total


def _reset(phone: str):
    """Wipe this demo student so runs are reproducible."""
    import sqlite3

    from config import DB_PATH
    conn = sqlite3.connect(DB_PATH)
    for table in ("sessions", "students", "skill_mastery", "attempts",
                  "agent_decisions", "processed_replies"):
        try:
            conn.execute(f"DELETE FROM {table} WHERE phone = ?", (phone,))
        except sqlite3.OperationalError:
            pass
    conn.commit()
    conn.close()
    import conversation
    conversation._sessions.pop(phone, None)


def _answer_for(phone: str, skill_bias: dict) -> str:
    """Answer the way a student with uneven skills would.

    Strong on reading, weak on algebra, so the mastery model has a real pattern
    to discover rather than noise.
    """
    import random

    import bank
    from conversation import get_session

    session = get_session(phone)
    if not session or not session.current_question_id:
        return "GO"
    question = bank.get(session.current_question_id)
    if not question:
        return "GO"

    accuracy = skill_bias.get(question["skill_id"], 0.5)
    if random.random() < accuracy:
        return bank.index_to_letter(question["correct_index"])
    wrong = [i for i in range(4) if i != question["correct_index"]]
    return bank.index_to_letter(random.choice(wrong))


def run():
    import random
    random.seed(7)

    import bank
    from database import init_db

    init_db()
    if not bank.is_available():
        print("No question_bank.json yet. Build it first:\n"
              "    python bank_build.py --pilot\n")
        return

    print("=" * 66)
    print("  Aria - offline demo")
    print("=" * 66)

    _reset(DEMO_PHONE)
    import tutor

    send, total = _sender()

    bias = {
        "m_systems": 0.15, "m_linear_two_var": 0.20, "m_equivalent_expr": 0.25,
        "m_nonlinear_eq": 0.25, "m_linear_one_var": 0.35,
        "rw_central_ideas": 0.85, "rw_words_in_context": 0.80,
        "rw_inferences": 0.75, "rw_boundaries": 0.70,
    }

    for message in ("hi", "Maya", "1400", "25 minutes", "GO"):
        print(f"\n[Maya] {message}")
        tutor.handle(DEMO_PHONE, message, send)
        time.sleep(0.15)

    for _ in range(14):
        reply = _answer_for(DEMO_PHONE, bias)
        print(f"\n[Maya] {reply}")
        tutor.handle(DEMO_PHONE, reply, send)
        time.sleep(0.1)

    for message in ("SCORE", "PLAN"):
        print(f"\n[Maya] {message}")
        tutor.handle(DEMO_PHONE, message, send)

    print("\n" + "=" * 66)
    print("  What Aria decided to do next, unprompted")
    print("=" * 66)
    import outreach
    print(outreach.preview(DEMO_PHONE))

    print("\n" + "=" * 66)
    print(f"  Whole session: {total['bytes']/1024:.1f} KB of text to the student.")
    print("  A single screenshot in a typical prep app is 300-800 KB.")
    print("=" * 66)


def chat():
    from database import init_db

    init_db()
    import bank
    if not bank.is_available():
        print("No question_bank.json yet - run: python bank_build.py")
        return

    import tutor
    phone = os.environ.get("DEMO_PHONE", "15550000009")
    print("Talking to Aria. Ctrl+C to quit, RESET to start over.")
    send, _ = _sender()
    while True:
        try:
            message = input("\n[you] ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not message:
            continue
        if message.upper() == "RESET":
            _reset(phone)
            print("  (reset)")
            continue
        tutor.handle(phone, message, send)


def _rewind(phone: str, days: float):
    """Move this student's whole history back by `days`.

    The alternative was to fake the clock, but every trigger in autonomy.py is
    a function of elapsed time, so a faked clock would mean demoing code paths
    that never run in production. Ageing the rows instead means the real decay
    curve, the real thresholds and the real scoring all execute unmodified --
    the student genuinely has been away for `days`.
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
    for table, columns in (("skill_mastery", ("last_seen",)),
                           ("attempts", ("at",)),
                           ("students", ("created_at", "last_active")),
                           ("agent_decisions", ("decided_at",))):
        try:
            rows = conn.execute(
                f"SELECT rowid AS _rid, * FROM {table} WHERE phone = ?", (phone,)
            ).fetchall()
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


def autonomy_demo(days: float = 4.0):
    """Show Aria deciding to speak first, and why.

    A freshly created student gives the autonomy path nothing to work with --
    no history has decayed, no mistake has repeated -- so the most interesting
    thing the agent does is invisible on a live demo. This ages a real session
    by `days` and then prints the whole deliberation: every candidate, its
    score, the evidence behind it, and which one won.

    Four days is the default because it is where the deliberation is richest,
    and the reason is worth knowing: decay_risk fires on skills *about to*
    cross the usefulness threshold, not ones already under it. Skip too far
    (5+ days here) and the interesting skills have already fallen through, so
    only the weaker high_value_idle trigger survives and the demo shows a
    single uncontested candidate. At four days both fire and one has to win.
    """
    import mastery
    import autonomy
    import outreach
    import student as student_mod
    from skills import SKILL_BY_ID

    run()   # produces a student with genuine attempts and misconceptions

    print("\n\n" + "=" * 74)
    print(f"  Now skip {days:.0f} days. Nobody messages Maya. What does Aria do?")
    print("=" * 74)

    before = {s.skill_id: s.p_mastery for s in mastery.get_all_states(DEMO_PHONE).values()}
    _rewind(DEMO_PHONE, days)
    after_states = mastery.get_all_states(DEMO_PHONE)

    print(f"\n  Mastery decays while she is away (half-life lengthens with each\n"
          f"  success, so well-learned skills fade slower):\n")
    moved = sorted(
        ((sid, before[sid], st.p_mastery) for sid, st in after_states.items()
         if sid in before and before[sid] - st.p_mastery > 0.005),
        key=lambda r: r[1] - r[2], reverse=True,
    )
    for sid, was, now in moved[:6]:
        name = SKILL_BY_ID[sid].name if sid in SKILL_BY_ID else sid
        flag = "  <- below the 0.60 threshold" if now < autonomy.DECAY_THRESHOLD <= was else ""
        print(f"    {name[:36]:<38}{was:.2f} -> {now:.2f}{flag}")
    if not moved:
        print("    (nothing decayed enough to show -- try a larger --days)")

    print("\n" + "-" * 74)
    print("  Every reason Aria considered, scored:")
    print("-" * 74)
    candidates = sorted(autonomy.evaluate(DEMO_PHONE), key=lambda d: d.score, reverse=True)
    if not candidates:
        print("    (no candidate reached a trigger)")
    for i, d in enumerate(candidates, 1):
        mark = "WINS " if i == 1 else "     "
        print(f"\n  {mark}{d.score:6.1f}  {d.trigger}")
        print(f"           {d.reason}")
        print(f"           evidence: {d.evidence}")

    print("\n" + "-" * 74)
    allowed, why = autonomy.can_message(DEMO_PHONE)
    print(f"  Send gate: {'PASS' if allowed else 'HOLD'} - {why}")
    print("-" * 74)

    # The gate is time-of-day dependent, so a demo run at 3am would otherwise
    # print nothing at exactly the moment it has the most to say.
    decision = candidates[0] if candidates else None
    if decision:
        profile = student_mod.get(DEMO_PHONE)
        print("\n  The message she would send:\n")
        for line in outreach.compose(profile, decision).split("\n"):
            print(f"    {line}")
        if not allowed:
            print("\n  (held by the send gate above -- shown here so the demo is "
                  "visible\n   regardless of what time you run it)")

    print("\n" + "=" * 74)
    print("  She was not on a timer. She had a reason, and it is on the record.")
    print("=" * 74)


def gains():
    """Price every skill in points per minute.

    This is the number the whole system turns on, so it gets its own view: the
    ranking is by *cost to move*, not by weakness, and those two orders differ.
    A skill the student is better at can still be the better place to spend the
    next twenty minutes, which is the opposite of what a weakness leaderboard
    would tell them.

    Runs on a fixed synthetic mastery vector rather than the demo database, so
    the table is identical on a clean checkout with no questions answered yet.
    """
    import random
    from datetime import datetime, timedelta, timezone

    import mastery
    import simulator
    import skills

    rng = random.Random(7)
    now = datetime.now(timezone.utc)
    states = {}
    for skill in skills.SKILLS:
        p = rng.uniform(0.25, 0.85)
        states[skill.id] = mastery.SkillState(
            skill_id=skill.id, p_mastery=p, p_mastery_raw=p,
            attempts=rng.randint(2, 9), correct=rng.randint(1, 5),
            last_seen=now - timedelta(days=rng.uniform(0, 20)),
        )

    projection = simulator.project(states, n_sims=2000, seed=11)
    print("=" * 74)
    print("  What is one minute of study actually worth?")
    print("=" * 74)
    print(f"\n  Projected today: {projection.total_low}-{projection.total_high} "
          f"(midpoint {projection.total}) "
          f"| R&W {projection.rw}  Math {projection.math}\n")

    print(f"  {'#':>2}  {'Skill':<32}{'Mastery':>8}{'Points':>8}"
          f"{'Min':>6}{'Pts/min':>9}")
    print("  " + "-" * 65)
    ranked = simulator.marginal_gains(states)
    shown = ranked[:8]
    for i, g in enumerate(shown, 1):
        print(f"  {i:>2}  {g.name[:32]:<32}{g.current_mastery:>8.2f}"
              f"{g.points_gained:>8.1f}{g.minutes_needed:>6.0f}"
              f"{g.points_per_minute:>9.2f}")

    # Surface the inversion explicitly -- it is the argument, not a footnote.
    # Pick the widest one, and only from the rows printed above: an example the
    # reader cannot see in the table proves nothing to them.
    best = ranked[0]
    candidates = [
        (hi, lo) for idx, hi in enumerate(shown)
        for lo in shown[idx + 1:]
        if hi.current_mastery > lo.current_mastery + 0.15
        and lo.points_gained > hi.points_gained
    ]
    inversion = max(
        candidates,
        key=lambda pair: pair[0].current_mastery - pair[1].current_mastery,
        default=None,
    )
    print(f"\n  Best use of the next {best.minutes_needed:.0f} minutes: {best.name}")
    if inversion:
        hi, lo = inversion
        print(f"\n  Note the inversion: the student is weaker at {lo.name} "
              f"({lo.current_mastery:.2f})\n  than at {hi.name} "
              f"({hi.current_mastery:.2f}), and {lo.name} is worth more raw\n"
              f"  points ({lo.points_gained:.1f} vs {hi.points_gained:.1f}) "
              f"-- yet {hi.name} ranks higher,\n  because it is cheaper to move. "
              "A weakness leaderboard gets this backwards.")

    plan = simulator.plan_session(states, minutes_available=40)
    print(f"\n{'=' * 74}")
    print(f"  Allocating 40 minutes -> +{plan.expected_points:.1f} points, "
          f"{plan.total_questions} questions")
    print("=" * 74)
    for item in plan.skills:
        print(f"  {item.name[:34]:<34}{item.questions:>3} q "
              f"{item.minutes:>5.0f} min  +{item.points_gained:>5.1f} pts   "
              f"{item.mastery_before:.2f} -> {item.mastery_after:.2f}")


if __name__ == "__main__":
    if "--chat" in sys.argv:
        chat()
    elif "--gains" in sys.argv:
        gains()
    elif "--autonomy" in sys.argv:
        skip = 4.0
        if "--days" in sys.argv:
            try:
                skip = float(sys.argv[sys.argv.index("--days") + 1])
            except (IndexError, ValueError):
                print("--days needs a number, e.g. --days 7")
                sys.exit(1)
        autonomy_demo(skip)
    else:
        run()
