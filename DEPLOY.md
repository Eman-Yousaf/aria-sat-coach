# Deploying Aria

The container runs `web.py`: student chat at `/`, the counsellor view at
`/dashboard`, and `/api/health` for the platform's health probe. It reads
`$PORT`, runs as a non-root user, and keeps its SQLite file at `/data`.

Works on Railway, Render, Fly, or any container host. Railway is written out
below because it needs the least configuration.

## Before you deploy

Rebuild the two baked-in artifacts so the image ships current data:

```bash
python bank_build.py --report     # confirm skill coverage is what you expect
python dashboard.py --seed        # demo cohort
python dashboard.py               # writes dashboard.html
git add question_bank.json dashboard.html && git commit -m "Refresh bank and dashboard"
```

Both are **build artifacts on purpose**. Regenerating the question bank at boot
would need an LLM key, take hours, and serve different questions on every
deploy.

## Railway

1. [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub repo**
2. Pick the repo. Railway detects the `Dockerfile` — no build config needed.
3. **Variables** → add only what you actually need (see below).
4. **Settings → Volumes** → mount a volume at `/data` if you want student
   progress to survive a redeploy. Without it the database resets each deploy,
   which is fine for a demo and wrong for real students.

### Environment variables

The app **boots with none of these**. The mastery model, simulator, planner and
dashboard are pure computation over a local SQLite file, and the question bank
ships in the image — so a deploy with zero secrets gives a fully working demo.

| Variable | Needed for | If unset |
|---|---|---|
| `DB_PATH` | where SQLite lives | `/data/reminders.db` (set in the Dockerfile) |
| `PORT` | listen port | platform sets it; falls back to 8000 |
| `GROQ_API_KEY` *or* `AZURE_OPENAI_*` | warming outreach phrasing, follow-up answers | deterministic text ships unchanged — an outage costs tone, never correctness |
| `OPENCLAW_*` | live WhatsApp transport | web chat still works; only WhatsApp delivery is off |
| `GOOGLE_CREDENTIALS_PATH`, `SPREADSHEET_ID` | legacy Sheets export | unused by the current path |

Do **not** paste a `.env` file into the image. `.dockerignore` excludes it
deliberately; the platform injects variables at runtime.

### Verify

```bash
curl https://<your-app>/api/health
# {"ok":true,"questions":<n>}
```

`questions: 0` means `question_bank.json` did not make it into the image —
check it is committed, and that `.dockerignore` is not excluding it.

Then open `/` and send "hi", and `/dashboard` for the counsellor view.

## Azure App Service (what this project actually runs on)

```bash
az group create -n aria-sat-rg -l uaenorth
az appservice plan create -n aria-plan -g aria-sat-rg --is-linux --sku B1
az webapp create -n aria-sat-coach -g aria-sat-rg -p aria-plan --runtime "PYTHON:3.11"

az webapp config appsettings set -n aria-sat-coach -g aria-sat-rg --settings \
  DASHBOARD_TOKEN=<long-random-string> \
  DB_PATH=/home/data/reminders.db \
  SCM_DO_BUILD_DURING_DEPLOYMENT=true

az webapp config set -n aria-sat-coach -g aria-sat-rg \
  --startup-file "mkdir -p /home/data && python -m uvicorn web:app --host 0.0.0.0 --port 8000"

az webapp deploy -n aria-sat-coach -g aria-sat-rg --src-path aria.zip --type zip
```

Three things that are easy to get wrong here:

- **`DB_PATH` must be under `/home`.** Only `/home` is persistent on App
  Service; anywhere else is wiped on restart, taking every student's progress
  with it.
- **Deploy a curated zip, not the working directory.** `.wwebjs_auth/` is
  333 MB *and* holds a live WhatsApp credential. Push the source files,
  `question_bank.json`, `dashboard.html` and `requirements.txt` — nothing else.
- **`az acr build` does not work on an Azure for Students subscription.** ACR
  Tasks are refused with `TasksOperationsNotAllowed`, which is why this is a
  source deploy rather than a container one. The `Dockerfile` still works
  anywhere you can build an image.

Register the providers first if the subscription is new: `Microsoft.Web`, and
`Microsoft.App` + `Microsoft.ContainerRegistry` if you intend to use containers.

## Render / Fly

Same image, same variables.

- **Render**: New → Web Service → Docker. Health check path `/api/health`.
  Add a disk mounted at `/data` to persist progress.
- **Fly**: `fly launch --dockerfile Dockerfile`, then `fly volumes create` and
  mount at `/data`.

## Notes

- `requirements.txt` is the **runtime** set. `chromadb` and
  `sentence-transformers` are deliberately absent: they serve only
  `sat_rag.py`, the RAG path `bank.py` replaced, which nothing that runs
  imports. Including them pulls `torch` in for roughly 2 GB that never
  executes. The bank generator's SDKs live in `requirements-build.txt` and are
  not installed on the server either — the bank ships as a build artifact.
- Free tiers idle containers out. The `HEALTHCHECK` keeps the container
  reporting healthy but will not stop a platform from sleeping an idle
  service; expect a cold start on the first request after a quiet period.
- WhatsApp delivery runs through `main.py`, which spawns `whatsapp-bridge.js`
  — whatsapp-web.js driving a real Chrome via Puppeteer, authenticated by
  scanning a QR with the phone that owns the number. That cannot run in this
  container and should not: it needs a browser, a linked phone, and a session
  directory that is deliberately gitignored. **Run it on a machine you
  control; the hosted app is the browser surface.**
