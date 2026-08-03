import os
from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
if not GROQ_API_KEY:
    print("WARNING: GROQ_API_KEY is not set.")

GOOGLE_CREDENTIALS_PATH = os.getenv("GOOGLE_CREDENTIALS_PATH", "")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID", "")
SHEET_NAME = os.getenv("SHEET_NAME", "Sheet1")

OPENCLAW_GATEWAY_TOKEN = os.getenv("OPENCLAW_GATEWAY_TOKEN", "")
if not OPENCLAW_GATEWAY_TOKEN:
    print("WARNING: OPENCLAW_GATEWAY_TOKEN is not set.")

OPENCLAW_BASE_URL = os.getenv("OPENCLAW_BASE_URL", "http://127.0.0.1:18789")
OPENCLAW_SEND_ENDPOINT = os.getenv("OPENCLAW_SEND_ENDPOINT", "/send")
OPENCLAW_MESSAGES_ENDPOINT = os.getenv("OPENCLAW_MESSAGES_ENDPOINT", "/messages")

DB_PATH = os.getenv("DB_PATH", "reminders.db")
CHROMA_DB_PATH = os.getenv("CHROMA_DB_PATH", "./chroma_db")

POLL_INTERVAL_MINUTES = int(os.getenv("POLL_INTERVAL_MINUTES", "2"))
SCHEDULE_INTERVAL_HOURS = int(os.getenv("SCHEDULE_INTERVAL_HOURS", "6"))
