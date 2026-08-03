# Deploy Aria SAT Tutor on Railway

## Prerequisites
- GitHub account
- Railway account (free tier)
- Groq API key (free)
- Google Cloud project with Sheets API enabled

## Steps

### 1. Push to GitHub
```bash
git init
git add .
git commit -m "Aria SAT Tutor Agent"
git remote add origin https://github.com/YOUR_USER/aria-sat-tutor.git
git push -u origin main
```

### 2. Create Railway Project
1. Go to [railway.app](https://railway.app)
2. Click **New Project** → **Deploy from GitHub repo**
3. Select your `aria-sat-tutor` repo
4. Railway will auto-detect the Dockerfile

### 3. Add Environment Variables
In Railway dashboard, go to your project → **Variables** and add:

| Variable | Value |
|----------|-------|
| `GROQ_API_KEY` | Your Groq API key |
| `GOOGLE_CREDENTIALS_PATH` | `/app/credentials.json` |
| `SPREADSHEET_ID` | Your Google Sheet ID |
| `SHEET_NAME` | `Sheet1` |
| `OPENCLAW_BASE_URL` | `http://127.0.0.1:18789` |
| `OPENCLAW_SEND_ENDPOINT` | `/send` |
| `OPENCLAW_MESSAGES_ENDPOINT` | `/messages` |
| `DB_PATH` | `/app/reminders.db` |
| `CHROMA_DB_PATH` | `/app/chroma_db` |
| `POLL_INTERVAL_MINUTES` | `2` |
| `SCHEDULE_INTERVAL_HOURS` | `6` |

### 4. Add Google Credentials
1. Open your Google service account JSON key
2. In Railway, add a new variable `GOOGLE_CREDENTIALS_JSON`
3. Paste the **entire JSON content** as the value
4. Add a Start Command hook or use a script to write the file:
   ```
   echo "$GOOGLE_CREDENTIALS_JSON" > /app/credentials.json
   ```
   Or add this in a Railway **Pre-deploy Command** or **Start Command**.

### 5. Deploy
Railway will automatically build and deploy. Monitor logs under **Deployments** → **View Logs**.

### 6. Keep Alive (Free Tier)
The Dockerfile includes a HEALTHCHECK and main.py logs a heartbeat every 5 minutes, preventing Railway from idling your service.

## Verify
Check the deployment logs for:
```
==================================================
  Aria SAT Tutor Agent — Adaptive Learning
==================================================
  • WhatsApp listener active
  • Reminder scheduler: every 6 hour(s)
  • Heartbeat: every 5 minutes
```

## Troubleshooting
- **Module not found**: Ensure `requirements.txt` includes all deps
- **Sheets API error**: Verify SPREADSHEET_ID and share sheet with service account email
- **Groq API error**: Check GROQ_API_KEY is set
- **ChromaDB error**: Ensure CHROMA_DB_PATH directory is writable
