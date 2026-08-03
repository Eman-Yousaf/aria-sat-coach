"""Offline demo: the full agent loop with no WhatsApp and no network.

    python demo.py            scripted student, prints the conversation
    python demo.py --chat     talk to Aria yourself in the terminal

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


if __name__ == "__main__":
    if "--chat" in sys.argv:
        chat()
    else:
        run()
