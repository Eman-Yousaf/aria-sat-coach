from agent import generate_reminder
from database import is_reminder_sent, mark_reminder_sent
from sheets import get_students_for_reminder
from whatsapp import send_message


def check_students():
    students = get_students_for_reminder()
    if not students:
        return

    for s in students:
        student_id = str(s["row_index"])

        if is_reminder_sent(student_id, "daily"):
            continue

        message = generate_reminder(s["name"], s["subject"])
        if not send_message(s["phone"], message):
            continue

        mark_reminder_sent(student_id, s["phone"], "daily")
        print(f"[✓] Daily reminder sent to {s['name']} ({s['phone']})")
