# Aria — Adaptive AI SAT Tutor

Aria is an intelligent SAT tutor agent accessible via WhatsApp. She diagnoses student weaknesses, generates personalized practice questions with adaptive difficulty, tracks progress in Google Sheets, and sends daily study reminders.

## Architecture

```
WhatsApp <──> OpenClaw ──> whatsapp.py ──> main.py ──> crew_agents.py ──> agent.py (Groq LLM)
                                        │                                      │
                                        ├── conversation.py (state machine)    │
                                        ├── sheets.py (progress tracking)      │
                                        ├── sat_rag.py (ChromaDB questions) ───┘
                                        ├── scheduler.py (daily reminders)
                                        ├── database.py (SQLite dedup)
                                        └── Dockerfile (Railway deploy)
```

## Features

- **True AI Agent**: Takes initiative with daily reminders, adapts to performance, acts on behalf of students
- **Adaptive Difficulty (Scaffolding)**: Easy → Medium → Hard based on last 3 scores
- **RAG Question Bank**: ChromaDB with 60+ SAT questions across Math, Reading, Writing
- **WhatsApp Interface**: Primary WebSocket listener with CLI polling fallback
- **Progress Tracking**: Google Sheets with Name, Phone, Subject, Weakness, Score, Date, NextReminder
- **Daily Reminders**: APScheduler + Groq-generated personalized messages
- **Global Commands**: `HELP`, `STOP` for easy navigation
- **Reading Comprehension**: Passage retrieval with guided questions

## Setup

### 1. Clone and virtual env

```bash
git clone <repo> && cd zayraa-reminder-agent
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/Mac:
source .venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Google Sheets Setup

1. Enable Google Sheets API in [Google Cloud Console](https://console.cloud.google.com/)
2. Create a service account, download JSON key
3. Create a Sheet with columns: Name, Phone, Subject, Weakness, Score, Date, NextReminder
4. Share sheet with service account email (Editor)
5. Get Spreadsheet ID from URL

### 4. Environment

```bash
copy .env.example .env
```

Edit `.env` with your keys.

### 5. Run Demo

```bash
python main.py --demo
```

### 6. Run (WhatsApp)

```bash
python main.py
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `GROQ_API_KEY` | Yes | Groq API key for LLM |
| `GOOGLE_CREDENTIALS_PATH` | Yes | Path to service account JSON |
| `SPREADSHEET_ID` | Yes | Google Sheet ID |
| `SHEET_NAME` | No | Sheet tab name (default: Sheet1) |
| `OPENCLAW_BASE_URL` | No | OpenClaw gateway URL |
| `DB_PATH` | No | SQLite database path |
| `CHROMA_DB_PATH` | No | ChromaDB vector store path |
| `POLL_INTERVAL_MINUTES` | No | Reply polling interval |
| `SCHEDULE_INTERVAL_HOURS` | No | Reminder check interval |

## Project Files

| File | Purpose |
|------|---------|
| `main.py` | Entry point, tutoring flow, demo mode, heartbeat |
| `agent.py` | Groq LLM prompts for diagnosis, questions, feedback, reminders |
| `conversation.py` | State machine (TutoringState, TutoringSession) |
| `crew_agents.py` | Orchestration wrappers (diagnosis, question, feedback) |
| `sat_rag.py` | ChromaDB with 60+ seed questions and reading passages |
| `sheets.py` | Google Sheets CRUD for student progress |
| `scheduler.py` | Daily reminder dispatch |
| `whatsapp.py` | WebSocket listener + CLI polling fallback |
| `database.py` | SQLite dedup for reminders and replies |
| `config.py` | Environment configuration |
| `demo.py` | Terminal demo script |
| `Dockerfile` | Container with HEALTHCHECK |
| `deploy_railway.md` | Railway deployment guide |

## Deployment

See `deploy_railway.md` for Railway deployment instructions.

## Constraints

- WhatsApp messages < 300 characters
- Global commands (`STOP`, `HELP`) handled at top of message flow
- All external calls wrapped in try/except
- Free tiers only: Groq, Railway, ChromaDB local, HuggingFace embeddings
