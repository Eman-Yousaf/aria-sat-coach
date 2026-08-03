FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

# requirements.txt is the runtime set only; it does not include
# chromadb/sentence-transformers (dead code) or the bank generator's SDKs.
# Nothing left in the install compiles, so no build toolchain is needed.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./
# The vetted question bank ships in the image. It is a build artifact, not
# something to regenerate at boot: generation needs an LLM key and takes hours,
# and a container that rebuilt it on start would serve different questions on
# every deploy.
COPY question_bank.json ./
# Pre-rendered counsellor view. dashboard.py builds this from the demo cohort
# before the image is built, so /dashboard has something to show on a fresh
# container whose database has no students in it yet.
COPY dashboard.html ./
COPY .env.example ./

# Run as a non-root user, with the SQLite file somewhere that user can write.
RUN useradd --create-home --uid 10001 aria \
    && mkdir -p /data && chown -R aria:aria /data /app
USER aria
ENV DB_PATH=/data/reminders.db

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/api/health',timeout=4).status==200 else 1)"

# Shell form so $PORT expands -- Railway and Render assign it at runtime.
CMD uvicorn web:app --host 0.0.0.0 --port ${PORT:-8000}
