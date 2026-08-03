"""Aria - entry point.

Wiring only. Inbound WhatsApp messages go to tutor.handle(); a scheduler wakes
periodically and asks autonomy.decide() whether any student is worth messaging.
The tutoring logic lives in tutor.py and the reasoning in simulator.py.
"""

import signal
import sys
import time
from datetime import datetime, timezone

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import tutor
from config import SCHEDULE_INTERVAL_HOURS
from conversation import digits, get_session
from database import init_db, is_reply_processed, mark_reply_processed
from whatsapp import get_chat_id, send_message, set_chat_id, start_listener, stop_listener

# Bytes sent to and from students, so the accessibility claim is measured
# rather than asserted. Printed by the heartbeat.
_bytes_out = 0
_bytes_in = 0
_messages_out = 0


def log(level: str, message: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{ts}] [{level}] {message}", flush=True)


def _make_sender(phone: str, chat_id: str | None):
    def send(text: str):
        global _bytes_out, _messages_out
        _bytes_out += len(text.encode("utf-8"))
        _messages_out += 1
        session = get_session(phone)
        cid = chat_id or (session.chat_id if session else None) or get_chat_id(phone)
        send_message(phone, text, chat_id=cid)
    return send


def handle_reply(reply: dict):
    global _bytes_in
    phone = digits(reply.get("from", ""))
    chat_id = reply.get("chatId")
    body = (reply.get("body") or "").strip()
    msg_id = reply.get("id") or f"msg_{time.time_ns()}"

    if not phone:
        return
    if chat_id:
        set_chat_id(phone, chat_id)
    if is_reply_processed(msg_id):
        return
    mark_reply_processed(msg_id, phone, body)
    if not body:
        return

    _bytes_in += len(body.encode("utf-8"))

    try:
        tutor.handle(phone, body, _make_sender(phone, chat_id))
    except Exception as e:
        log("ERROR", f"handling {phone}: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        try:
            _make_sender(phone, chat_id)(
                "Something went wrong on my end. Say GO to keep practising."
            )
        except Exception:
            pass


def check_outreach():
    """Ask, for every student, whether there is a reason to message them."""
    import autonomy
    import student as student_mod
    from outreach import compose

    students = student_mod.all_students()
    if not students:
        return

    considered = acted = 0
    for profile in students:
        considered += 1
        try:
            decision = autonomy.decide(profile.phone)
            if decision is None:
                continue
            message = compose(profile, decision)
            send = _make_sender(profile.phone, None)
            send(message)
            autonomy.record_action(decision, message)
            acted += 1
            log("INFO", f"outreach -> {profile.phone}: {decision.trigger} "
                        f"(score {decision.score:.1f}) {decision.evidence}")
        except Exception as e:
            log("ERROR", f"outreach for {profile.phone} failed: {e}")

    log("INFO", f"outreach pass: {considered} considered, {acted} messaged")


def _heartbeat():
    total = _bytes_out + _bytes_in
    per_msg = (_bytes_out / _messages_out) if _messages_out else 0
    log("INFO", f"alive | {_messages_out} messages sent | "
                f"{total/1024:.1f} KB total traffic | {per_msg:.0f} B per message")


def start():
    init_db()
    log("INFO", "=" * 52)
    log("INFO", "Aria - SAT coach")

    import bank
    from skills import SKILLS
    if bank.is_available():
        coverage = bank.coverage()
        log("INFO", f"question bank: {len(bank.load())} items, "
                    f"{len(coverage)}/{len(SKILLS)} skills")
    else:
        log("WARN", "no question_bank.json - run: python bank_build.py")
    log("INFO", "=" * 52)

    start_listener(handle_reply)

    from apscheduler.schedulers.blocking import BlockingScheduler
    scheduler = BlockingScheduler()
    scheduler.add_job(check_outreach, "interval",
                      hours=SCHEDULE_INTERVAL_HOURS, id="outreach")
    scheduler.add_job(_heartbeat, "interval", minutes=10, id="heartbeat")

    log("INFO", f"outreach check every {SCHEDULE_INTERVAL_HOURS}h")
    log("INFO", "Ctrl+C to stop")

    def shutdown(signum, frame):
        log("INFO", "shutting down")
        stop_listener()
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    scheduler.start()


if __name__ == "__main__":
    if "--demo" in sys.argv:
        from demo import run
        run()
    elif "--outreach" in sys.argv:
        init_db()
        check_outreach()
    else:
        start()
