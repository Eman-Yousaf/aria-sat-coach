import os
from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
if not GROQ_API_KEY:
    print("WARNING: GROQ_API_KEY is not set.")

# Azure OpenAI. When the endpoint is set, bank_build uses Azure instead of
# Groq: Groq's free tier meters tokens-per-day per model, which stalled the
# question-bank build partway through. Deployment names are ours to choose at
# deploy time, so they are configured rather than hardcoded.
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "")
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY", "")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21")
AZURE_DEPLOYMENT_MATH = os.getenv("AZURE_DEPLOYMENT_MATH", "")
AZURE_DEPLOYMENT_RW = os.getenv("AZURE_DEPLOYMENT_RW", "")
AZURE_DEPLOYMENT_VERIFIER = os.getenv("AZURE_DEPLOYMENT_VERIFIER", "")

USE_AZURE = bool(AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY)

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
