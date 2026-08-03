import re
from datetime import datetime

from config import GOOGLE_CREDENTIALS_PATH, SPREADSHEET_ID, SHEET_NAME

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
_SERVICE = None


def _get_service():
    global _SERVICE
    if _SERVICE is None:
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build
        creds = Credentials.from_service_account_file(
            GOOGLE_CREDENTIALS_PATH, scopes=_SCOPES
        )
        _SERVICE = build("sheets", "v4", credentials=creds)
    return _SERVICE


def add_student(name: str, phone: str, subject: str, weakness: str = "") -> int:
    service = _get_service()
    body = {"values": [[name, phone, subject, weakness, "", "", ""]]}
    result = (
        service.spreadsheets()
        .values()
        .append(
            spreadsheetId=SPREADSHEET_ID,
            range=f"{SHEET_NAME}!A:G",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body=body,
        )
        .execute()
    )
    updated_range = result.get("updates", {}).get("updatedRange", "")
    match = re.search(r"(\d+)$", updated_range)
    return int(match.group(1)) if match else 0


def get_students():
    service = _get_service()
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=SPREADSHEET_ID, range=f"{SHEET_NAME}!A:G")
        .execute()
    )
    values = result.get("values", [])
    if not values or len(values) < 2:
        return []
    students = []
    for i, row in enumerate(values[1:], start=2):
        if len(row) < 2:
            continue
        row = row + [""] * (7 - len(row))
        students.append({
            "row_index": i,
            "name": (row[0] or "").strip(),
            "phone": (row[1] or "").strip(),
            "subject": (row[2] or "").strip(),
            "weakness": (row[3] or "").strip(),
            "score": (row[4] or "").strip(),
            "date": (row[5] or "").strip(),
            "next_reminder": (row[6] or "").strip(),
        })
    return students


def update_student_progress(phone: str, subject: str, score: str, weakness: str):
    students = get_students()
    digits = re.sub(r"\D", "", phone)
    for s in students:
        if re.sub(r"\D", "", s["phone"]) == digits:
            service = _get_service()
            service.spreadsheets().values().update(
                spreadsheetId=SPREADSHEET_ID,
                range=f"{SHEET_NAME}!C{ s['row_index'] }:G{ s['row_index'] }",
                valueInputOption="RAW",
                body={
                    "values": [[
                        subject,
                        weakness,
                        score,
                        "",
                        "",
                    ]]
                },
            ).execute()
            return True
    return False


def get_average_score(phone: str) -> float | None:
    digits = re.sub(r"\D", "", phone)
    students = get_students()
    scores = []
    for s in students:
        if re.sub(r"\D", "", s["phone"]) == digits:
            if s.get("score"):
                try:
                    scores.append(float(s["score"]))
                except ValueError:
                    pass
    if not scores:
        return None
    return sum(scores[-3:]) / len(scores[-3:])


def get_students_for_reminder():
    students = get_students()
    now = datetime.now().strftime("%Y-%m-%d")
    return [s for s in students if s.get("next_reminder", "") <= now or not s.get("next_reminder")]
