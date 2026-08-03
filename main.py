import re
import signal
import sys
import time
from datetime import datetime, timezone

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from apscheduler.schedulers.blocking import BlockingScheduler

from agent import answer_follow_up, generate_question
from config import SCHEDULE_INTERVAL_HOURS
from conversation import (
    TutoringState,
    clear_session,
    create_session,
    get_confirmation_message,
    get_session,
    get_state_message,
    has_active_session,
    parse_time,
)
from crew_agents import run_diagnosis, run_feedback, run_question
from database import init_db, is_reply_processed, mark_reply_processed
from scheduler import check_students
from sheets import add_student, get_average_score, update_student_progress
from whatsapp import get_chat_id, send_message, set_chat_id, start_listener, stop_listener


def log(level: str, message: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{ts}] [{level}] {message}", flush=True)


def _reply(phone: str, message: str, chat_id: str | None = None):
    session = get_session(phone)
    cid = chat_id or (session.chat_id if session else None) or get_chat_id(phone)
    send_message(phone, message, chat_id=cid)


def _extract_digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def _is_global_command(body: str) -> str | None:
    lower = body.strip().lower()
    if lower in ("stop", "exit", "quit"):
        return "stop"
    if lower in ("help", "menu", "commands"):
        return "help"
    return None


def handle_tutoring_flow(phone: str, body: str):
    cmd = _is_global_command(body)
    if cmd == "stop":
        clear_session(phone)
        send_message(
            phone,
            "Session ended. Practice anytime! Reply HELP for commands.",
        )
        return
    if cmd == "help":
        send_message(
            phone,
            "Commands: Math, Reading, Writing to choose subject. "
            "STOP to end. HELP for this menu. "
            "Answer with A, B, C, or D for questions.",
        )
        return

    session = get_session(phone)
    if not session:
        return

    if session.state == TutoringState.AWAITING_NAME:
        name = body.strip().title()
        if not name or len(name) < 2 or name.lower() in ("hi", "hello", "hey", "help", "stop", "yes", "no", "ok"):
            send_message(phone, "Please tell me your name!")
            return
        session.name = name
        session.state = TutoringState.AWAITING_SUBJECT
        session.save()
        send_message(phone, get_state_message(session))
        log("INFO", f"Student: {name} — awaiting subject")

    elif session.state == TutoringState.AWAITING_SUBJECT:
        subj_lower = body.strip().lower()
        subjects_map = {
            "math": "Math", "maths": "Math", "mathematics": "Math",
            "reading": "Reading",
            "writing": "Writing",
            "m": "Math", "r": "Reading", "w": "Writing",
        }
        subject = subjects_map.get(subj_lower)
        if not subject:
            for key, val in subjects_map.items():
                if key in subj_lower or subj_lower in key:
                    subject = val
                    break
        if not subject:
            send_message(
                phone,
                "Please choose: Math, Reading, or Writing.",
            )
            return
        session.subject = subject
        session.state = TutoringState.AWAITING_TIME
        session.save()
        send_message(phone, get_state_message(session))
        log("INFO", f"{session.name} — subject: {subject} — awaiting time")

    elif session.state == TutoringState.AWAITING_TIME:
        parsed = parse_time(body)
        if not parsed:
            send_message(
                phone,
                "Please tell me how long, like '30 minutes' or '1 hour'.",
            )
            return
        session.time = parsed
        session.state = TutoringState.AWAITING_CONFIRMATION
        session.save()
        send_message(phone, get_state_message(session))
        log("INFO", f"{session.name} — duration: {parsed}")

    elif session.state == TutoringState.AWAITING_CONFIRMATION:
        lower = body.strip().lower()
        if lower in ("yes", "y", "yeah", "sure", "ok", "start"):
            send_message(phone, get_confirmation_message(session))
            _generate_and_send_question(phone, session)
            session.state = TutoringState.AWAITING_ANSWER
            session.save()
        else:
            session.state = TutoringState.AWAITING_SUBJECT
            session.subject = None
            session.time = None
            session.save()
            send_message(
                phone,
                "No problem! Choose a subject: Math, Reading, or Writing.",
            )

    elif session.state == TutoringState.AWAITING_ANSWER:
        if body.strip().upper() not in ("A", "B", "C", "D"):
            explanation = answer_follow_up(session.current_question, body)
            send_message(phone, explanation)
            return

        answer = body.strip().upper()
        correct = session.current_question.get("correct_answer", "")
        is_correct = answer == correct
        score = 100 if is_correct else 0

        feedback = run_feedback(
            session.current_question.get("question", ""),
            answer,
            correct,
        )

        try:
            update_student_progress(
                phone,
                session.subject or "",
                str(score),
                session.weakness or "",
            )
        except Exception:
            pass

        msg = f"{feedback}\n\nWant another question? Reply YES or NO."
        send_message(phone, msg)
        log("INFO", f"{session.name}: {'Correct' if is_correct else 'Wrong'} on {session.subject}")

        session.current_question = None
        session.state = TutoringState.AWAITING_CONFIRMATION
        session.save()


def _generate_and_send_question(phone: str, session):
    avg = None
    try:
        avg = get_average_score(phone)
    except Exception:
        pass

    if avg is None:
        difficulty = "easy"
    elif avg >= 80:
        difficulty = "hard"
    elif avg >= 50:
        difficulty = "medium"
    else:
        difficulty = "easy"

    weakness = session.weakness

    question = run_question(
        subject=session.subject or "Math",
        weakness=weakness,
        difficulty=difficulty,
    )

    session.current_question = question
    options_text = "\n".join(
        f"{chr(65+i)}) {opt}" for i, opt in enumerate(question.get("options", ["A", "B", "C", "D"]))
    )
    msg = (
        f"Level: {difficulty}\n\n"
        f"{question.get('question', '')}\n\n"
        f"{options_text}"
    )
    send_message(phone, msg)
    log("INFO", f"Question sent to {session.name} ({difficulty})")


def handle_reply(reply: dict):
    raw_phone = reply["from"]
    chat_id = reply.get("chatId")
    body = reply["body"].strip()
    msg_id = reply["id"]

    if chat_id:
        set_chat_id(raw_phone, chat_id)

    if is_reply_processed(msg_id):
        return
    if not body:
        mark_reply_processed(msg_id, raw_phone, body)
        return

    mark_reply_processed(msg_id, raw_phone, body)

    cmd = _is_global_command(body)
    if cmd == "stop":
        clear_session(raw_phone)
        _reply(raw_phone, "Session ended. Practice anytime! Reply HELP for commands.", chat_id)
        return
    if cmd == "help":
        _reply(
            raw_phone,
            "I'm Aria, your SAT tutor! Choose Math, Reading, or Writing. "
            "STOP to end. HELP for this menu.",
            chat_id,
        )
        return

    # If user greets us mid-session, reset so we don't stay stuck in
    # a stale state (e.g. AWAITING_TIME from a persisted old session).
    if body.lower() in ("hi", "hello", "hey", "hi aria", "hey aria", "hello aria", "good morning", "good evening"):
        clear_session(raw_phone)

    if has_active_session(raw_phone):
        session = get_session(raw_phone)
        if session and chat_id:
            session.chat_id = chat_id
            session.save()
        handle_tutoring_flow(raw_phone, body)
        return

    create_session(raw_phone, chat_id=chat_id)
    _reply(
        raw_phone,
        "Hi! I'm Aria, your AI SAT tutor. What's your name?",
        chat_id,
    )


def demo_mode(target_phone: str = "15551234567"):
    print("=" * 60)
    print("  Aria SAT Tutor — Demo Mode")
    print("=" * 60)
    print()

    _demo_phone = target_phone

    def _demo_send(phone, message):
        print(f"[Aria] {message}")
        print()

    global send_message
    original_send = send_message
    send_message = _demo_send

    init_db()

    demo_steps = [
        ("Student", "hi"),
        ("Student", "Alex"),
        ("Student", "Math"),
        ("Student", "30 minutes"),
        ("Student", "yes"),
        ("Student", "A"),
        ("Student", "yes"),
        ("Student", "B"),
        ("Student", "no"),
        ("Student", "stop"),
    ]

    phone = _demo_phone
    session = None

    for sender, text in demo_steps:
        print(f"[{sender}] {text}")
        time.sleep(0.5)

        if has_active_session(phone):
            handle_tutoring_flow(phone, text)
        else:
            handle_reply({
                "id": f"demo_{time.time_ns()}",
                "from": phone,
                "body": text,
                "timestamp": str(int(time.time())),
            })

        print()

    send_message = original_send
    print("=" * 60)
    print("  Demo complete!")
    print("=" * 60)


def start():
    init_db()
    log("INFO", "=" * 50)
    log("INFO", "Aria SAT Tutor Agent — Adaptive Learning")
    log("INFO", "=" * 50)

    start_listener(handle_reply)

    scheduler = BlockingScheduler()
    scheduler.add_job(
        check_students,
        "interval",
        hours=SCHEDULE_INTERVAL_HOURS,
        id="check_students",
    )

    scheduler.add_job(
        _heartbeat_log,
        "interval",
        minutes=5,
        id="heartbeat",
    )

    log("INFO", f"Reminder scheduler: every {SCHEDULE_INTERVAL_HOURS} hour(s)")
    log("INFO", "Heartbeat: every 5 minutes")
    log("INFO", "Press Ctrl+C to stop.")

    def shutdown(signum, frame):
        log("INFO", "Shutting down...")
        stop_listener()
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    scheduler.start()


def _heartbeat_log():
    log("INFO", f"Aria running — {time.strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo_mode()
    else:
        start()
