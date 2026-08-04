"""Web front door: a browser you can talk to Aria in, and the coach dashboard.

WhatsApp is the real product surface, but a judge cannot join a WhatsApp
number, and a reviewer on a laptop should not have to install anything to see
whether this works. So this serves the *same* tutor over HTTP:

    GET  /              student chat
    POST /api/message   one turn -> Aria's replies
    GET  /dashboard     the counsellor triage view
    GET  /api/health    liveness for the platform

`tutor.handle()` takes a phone number and a `send` callback. Here the "phone"
is a per-browser session id and `send` appends to a list, so the conversation
logic, mastery model and question bank are byte-for-byte what a WhatsApp
student gets. Only the transport differs -- which is the same claim demo.py
makes, and it is worth keeping true.

    pip install fastapi uvicorn
    python web.py                  # http://127.0.0.1:8000
"""

import hmac
import ipaddress
import json
import os
import re
import secrets
import sqlite3

from fastapi import Cookie, FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel

import config
import tutor
import whatsapp_cloud
from database import init_db, is_reply_processed, mark_reply_processed

app = FastAPI(title="Aria", docs_url=None, redoc_url=None)

HERE = os.path.dirname(os.path.abspath(__file__))
DASHBOARD_PATH = os.path.join(HERE, "dashboard.html")

# Browser sessions are namespaced away from real phone numbers so a web visitor
# can never land on a WhatsApp student's mastery record by guessing an id.
SESSION_PREFIX = "web_"
SESSION_COOKIE = "aria_session"
_SESSION_RE = re.compile(rf"^{SESSION_PREFIX}[0-9a-f]{{32}}$")


class Turn(BaseModel):
    message: str


# /api/message is public and writes to SQLite, so it is the one endpoint worth
# guarding. A token bucket per session: sustained one message every two
# seconds, with a burst of ten so real typing never trips it. In memory on
# purpose -- a single replica is already required (SQLite), so there is nothing
# to share state with, and a dependency on Redis would be a worse trade.
RATE_BURST = 10
RATE_REFILL_PER_SEC = 0.5
_buckets: dict[str, tuple[float, float]] = {}
_BUCKET_CAP = 10_000


def _rate_limited(key: str) -> bool:
    import time
    now = time.monotonic()
    tokens, last = _buckets.get(key, (float(RATE_BURST), now))
    tokens = min(RATE_BURST, tokens + (now - last) * RATE_REFILL_PER_SEC)
    if tokens < 1.0:
        _buckets[key] = (tokens, now)
        return True
    # Bound the dict so a flood of fresh sessions cannot grow it without limit.
    if len(_buckets) > _BUCKET_CAP and key not in _buckets:
        for stale in [k for k, (_, t) in _buckets.items()
                      if now - t > 3600][:_BUCKET_CAP // 2]:
            _buckets.pop(stale, None)
    _buckets[key] = (tokens - 1.0, now)
    return False


def _new_session() -> str:
    return SESSION_PREFIX + secrets.token_hex(16)


def _valid(session: str | None) -> bool:
    return bool(session and _SESSION_RE.match(session))


@app.on_event("startup")
def _startup():
    init_db()


@app.get("/api/health")
def health():
    import bank
    return {"ok": True, "questions": len(bank.load()) if bank.is_available() else 0}


@app.post("/api/message")
def message(turn: Turn, request: Request, response: Response,
            aria_session: str | None = Cookie(default=None)):
    session = aria_session if _valid(aria_session) else _new_session()
    response.set_cookie(SESSION_COOKIE, session, httponly=True,
                        samesite="lax", max_age=60 * 60 * 24 * 30)

    # Key on the session cookie, falling back to the peer address for a caller
    # that never keeps one -- which is exactly what a script hammering the
    # endpoint looks like.
    if _rate_limited(session if _valid(aria_session)
                     else (request.client.host if request.client else session)):
        response.status_code = 429
        return {"replies": ["You're going a bit fast for me. Give me a second."]}

    body = (turn.message or "").strip()
    if not body:
        return {"replies": []}
    # A single WhatsApp message is short by design; anything longer is not a
    # student typing an answer.
    if len(body) > 500:
        body = body[:500]

    replies: list[str] = []
    try:
        tutor.handle(session, body, replies.append)
    except Exception as exc:  # noqa: BLE001 - surface, never 500 at the student
        print(f"tutor error for {session}: {type(exc).__name__}: {exc}", flush=True)
        replies.append("Something went wrong on my end. Try that again?")
    return {"replies": replies}


@app.post("/api/reset")
def reset(response: Response, aria_session: str | None = Cookie(default=None)):
    """Wipe this browser's student so a demo can be run twice."""
    if _valid(aria_session):
        from config import DB_PATH
        conn = sqlite3.connect(DB_PATH)
        for table in ("sessions", "students", "skill_mastery", "attempts",
                      "agent_decisions", "processed_replies"):
            try:
                conn.execute(f"DELETE FROM {table} WHERE phone = ?", (aria_session,))
            except sqlite3.OperationalError:
                pass
        conn.commit()
        conn.close()
        import conversation
        conversation._sessions.pop(aria_session, None)
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


def _is_loopback(request: Request) -> bool:
    host = request.client.host if request.client else ""
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _dashboard_allowed(request: Request, token: str | None) -> bool:
    """The dashboard lists students by name with their performance.

    That is a roster of children and how they are doing, so it is not something
    to serve to whoever finds the URL. Local runs stay frictionless because
    demoing on your own machine is not the risk; anything reached over a
    network has to present DASHBOARD_TOKEN. With no token configured there is
    no value a remote caller could send, so remote access is simply refused
    rather than defaulting open.
    """
    if _is_loopback(request):
        return True
    expected = os.environ.get("DASHBOARD_TOKEN", "")
    if not expected or not token:
        return False
    return hmac.compare_digest(token, expected)


@app.get("/webhook/whatsapp", response_class=PlainTextResponse)
def whatsapp_verify(request: Request):
    """Meta's one-time subscription handshake: echo hub.challenge back."""
    params = request.query_params
    if params.get("hub.mode") == "subscribe" and \
            params.get("hub.verify_token") == config.WHATSAPP_VERIFY_TOKEN \
            and config.WHATSAPP_VERIFY_TOKEN:
        return PlainTextResponse(params.get("hub.challenge", ""))
    return PlainTextResponse("verification failed", status_code=403)


@app.post("/webhook/whatsapp")
async def whatsapp_inbound(request: Request):
    # Nothing should reach the tutor through here until WhatsApp is actually
    # wired up. Otherwise a deployment that has not configured it yet is
    # sitting on a public endpoint that writes student records.
    if not whatsapp_cloud.is_configured():
        return JSONResponse({"ok": False}, status_code=404)

    raw = await request.body()

    if not whatsapp_cloud.signature_ok(
            raw, request.headers.get("x-hub-signature-256")):
        return JSONResponse({"ok": False}, status_code=403)

    try:
        payload = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        return {"ok": True}

    for phone, text, message_id in whatsapp_cloud.extract_messages(payload):
        # Meta retries a webhook it thinks failed, so the same message can
        # arrive more than once. Answering twice would double-count the
        # attempt and corrupt the student's mastery estimate.
        if message_id and is_reply_processed(message_id):
            continue
        if message_id:
            mark_reply_processed(message_id, phone, text)

        if not text:
            whatsapp_cloud.send_message(
                phone, "I can only read text messages right now - "
                       "type your answer and I'll pick it up.")
            continue

        replies: list[str] = []
        try:
            tutor.handle(phone, text, replies.append)
        except Exception as exc:  # noqa: BLE001
            print(f"tutor error for {phone}: {type(exc).__name__}: {exc}",
                  flush=True)
            replies = ["Something went wrong on my end. Say GO to keep going."]
        for reply in replies:
            whatsapp_cloud.send_message(phone, reply)

    # Always 200: a non-2xx makes Meta redeliver the whole batch, and the
    # student has already been answered.
    return {"ok": True}


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard_page(request: Request, token: str | None = None,
                   dashboard_token: str | None = Cookie(default=None)):
    if not _dashboard_allowed(request, token or dashboard_token):
        return PlainTextResponse(
            "This view lists students by name and is not public.\n"
            "Set DASHBOARD_TOKEN on the server and open /dashboard?token=...",
            status_code=401)

    if not os.path.exists(DASHBOARD_PATH):
        return HTMLResponse(
            "<h1>No dashboard yet</h1><p>Run <code>python dashboard.py --seed</code> "
            "then <code>python dashboard.py</code>.</p>", status_code=404)
    with open(DASHBOARD_PATH, encoding="utf-8") as f:
        page = HTMLResponse(f.read())
    # Remember a token that checked out, so the URL can be shared without the
    # secret trailing behind it in browser history and referrer headers.
    if token:
        page.set_cookie("dashboard_token", token, httponly=True,
                        samesite="lax", max_age=60 * 60 * 12)
    return page


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(CHAT_PAGE)


# Deliberately one file, no build step, no CDN: the students this is for are on
# slow connections and borrowed phones. The whole page is a few KB and renders
# without a single extra request.
# Deliberately one file, no build step, no CDN, no web fonts: the students this
# is for are on borrowed phones and slow connections. The whole page is a few KB
# and renders without a single extra request, so it is usable before a
# framework bundle would have finished downloading. Every visual flourish here
# is CSS that costs nothing to send.
CHAT_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="light dark">
<meta name="description" content="Aria - an SAT coach that spends your minutes where they are worth the most.">
<title>Aria - SAT coach</title>
<style>
  :root{
    --bg:#0b0d12; --panel:#151922; --panel-2:#1b202b; --line:#252c3a;
    --ink:#eef1f6; --muted:#98a2b6; --me:#3b6dff; --me-ink:#fff;
    --accent:#4fd1a5; --warn:#f0b849; --shadow:0 1px 2px rgba(0,0,0,.4);
  }
  @media (prefers-color-scheme: light){
    :root{
      --bg:#f6f7f9; --panel:#fff; --panel-2:#f1f3f7; --line:#e3e7ee;
      --ink:#111621; --muted:#5b6577; --me:#2f5fe0; --me-ink:#fff;
      --accent:#0f8f68; --warn:#a86a04; --shadow:0 1px 2px rgba(16,24,40,.06);
    }
  }
  *{box-sizing:border-box}
  html,body{height:100%}
  body{
    margin:0;background:var(--bg);color:var(--ink);
    font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Ubuntu,sans-serif;
    display:flex;flex-direction:column;height:100dvh;
    -webkit-font-smoothing:antialiased;
  }

  header{
    display:flex;align-items:center;gap:11px;flex:none;
    padding:11px 16px calc(11px) 16px;
    background:var(--panel);border-bottom:1px solid var(--line);
    padding-top:max(11px,env(safe-area-inset-top));
  }
  .mark{
    width:32px;height:32px;border-radius:9px;flex:none;
    background:linear-gradient(140deg,var(--me),var(--accent));
    display:grid;place-items:center;color:#fff;font-weight:700;font-size:15px;
    letter-spacing:.5px;
  }
  .who{display:flex;flex-direction:column;line-height:1.25}
  .who b{font-size:15px;letter-spacing:-.01em}
  .who span{font-size:12px;color:var(--muted);display:flex;align-items:center;gap:5px}
  .dot{width:6px;height:6px;border-radius:50%;background:var(--accent);flex:none}
  .spacer{margin-left:auto}
  .ghost{
    color:var(--muted);font-size:13px;text-decoration:none;
    border:1px solid var(--line);padding:6px 11px;border-radius:8px;
    background:var(--panel-2);transition:color .15s,border-color .15s;
  }
  .ghost:hover{color:var(--ink);border-color:var(--muted)}

  #log{
    flex:1;overflow-y:auto;overscroll-behavior:contain;
    padding:18px 16px 8px;display:flex;flex-direction:column;gap:11px;
    scrollbar-width:thin;
  }
  #log::-webkit-scrollbar{width:8px}
  #log::-webkit-scrollbar-thumb{background:var(--line);border-radius:4px}

  .row{display:flex;flex-direction:column;max-width:min(680px,88%)}
  .row.mine{align-self:flex-end;align-items:flex-end}
  .row.theirs{align-self:flex-start}

  .msg{
    padding:10px 14px;border-radius:15px;white-space:pre-wrap;
    overflow-wrap:anywhere;box-shadow:var(--shadow);
    animation:rise .22s cubic-bezier(.2,.7,.3,1) both;
  }
  .theirs .msg{
    background:var(--panel);border:1px solid var(--line);
    border-bottom-left-radius:5px;
  }
  .mine .msg{
    background:var(--me);color:var(--me-ink);border-bottom-right-radius:5px;
  }
  @keyframes rise{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
  @media (prefers-reduced-motion:reduce){
    .msg{animation:none}
  }

  /* Monospace the answer options so A/B/C/D line up as a real question would */
  .msg.q{font-variant-numeric:tabular-nums}

  .starters{display:flex;flex-wrap:wrap;gap:7px;padding:2px 0 6px}
  .chip{
    border:1px solid var(--line);background:var(--panel);color:var(--muted);
    padding:7px 12px;border-radius:999px;font-size:13px;cursor:pointer;
    font-family:inherit;transition:color .15s,border-color .15s,transform .1s;
  }
  .chip:hover{color:var(--ink);border-color:var(--me)}
  .chip:active{transform:scale(.97)}

  .note{
    color:var(--muted);font-size:12.5px;text-align:center;padding:2px 8px 4px;
    max-width:560px;align-self:center;
  }

  form{
    display:flex;gap:9px;flex:none;padding:12px 16px;
    padding-bottom:max(12px,env(safe-area-inset-bottom));
    background:var(--panel);border-top:1px solid var(--line);
  }
  input{
    flex:1;min-width:0;padding:12px 14px;border-radius:11px;
    border:1px solid var(--line);background:var(--bg);color:var(--ink);
    font:inherit;font-size:16px; /* 16px stops iOS zooming on focus */
  }
  input::placeholder{color:var(--muted)}
  input:focus{outline:2px solid var(--me);outline-offset:-1px}
  button{
    padding:12px 18px;border:0;border-radius:11px;background:var(--me);
    color:var(--me-ink);font:inherit;font-size:15px;font-weight:600;
    cursor:pointer;transition:opacity .15s,transform .1s;
  }
  button:hover:not(:disabled){opacity:.92}
  button:active:not(:disabled){transform:scale(.98)}
  button:disabled{opacity:.45;cursor:default}

  .dots{display:inline-flex;gap:4px;padding:3px 2px}
  .dots i{
    width:6px;height:6px;border-radius:50%;background:var(--muted);
    animation:blink 1.3s infinite both;
  }
  .dots i:nth-child(2){animation-delay:.18s}
  .dots i:nth-child(3){animation-delay:.36s}
  @keyframes blink{0%,65%,100%{opacity:.22}30%{opacity:.95}}

  .sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}
</style>
</head>
<body>

<header>
  <div class="mark" aria-hidden="true">A</div>
  <div class="who">
    <b>Aria</b>
    <span><i class="dot" aria-hidden="true"></i>SAT coach</span>
  </div>
  <div class="spacer"></div>
  <a class="ghost" href="/dashboard">Coach view</a>
</header>

<div id="log" role="log" aria-live="polite" aria-label="Conversation">
  <div class="row theirs">
    <div class="msg">Hi! I'm Aria, your SAT coach.

Tell me how many minutes you have and I'll tell you what they're worth.</div>
  </div>
  <div class="starters" id="starters">
    <button class="chip" type="button" data-say="hi">Say hi</button>
    <button class="chip" type="button" data-say="I have 20 minutes">I have 20 minutes</button>
    <button class="chip" type="button" data-say="PLAN">Show my plan</button>
  </div>
  <p class="note">Plain text on purpose &mdash; this whole page is a few KB, so it works
  on a borrowed phone over 2G.</p>
</div>

<form id="f" autocomplete="off">
  <label class="sr" for="i">Message Aria</label>
  <input id="i" placeholder="Type a message" autofocus enterkeyhint="send">
  <button id="b" type="submit">Send</button>
</form>

<script>
const log=document.getElementById('log'), form=document.getElementById('f'),
      input=document.getElementById('i'), btn=document.getElementById('b'),
      starters=document.getElementById('starters');

const atBottom=()=>log.scrollHeight-log.scrollTop-log.clientHeight<80;

function add(text, who, isQuestion){
  const row=document.createElement('div');
  row.className='row '+(who==='me'?'mine':'theirs');
  const d=document.createElement('div');
  d.className='msg'+(isQuestion?' q':'');
  d.textContent=text;
  row.appendChild(d); log.appendChild(row);
  log.scrollTop=log.scrollHeight;
  return row;
}

// Aria's question format puts the options on their own lines as "A) ..." --
// worth detecting so they get tabular figures and stay aligned.
const looksLikeQuestion=t=>/^\\s*A\\)/m.test(t)&&/^\\s*D\\)/m.test(t);

async function say(text){
  if(!text) return;
  starters?.remove();
  add(text,'me');
  input.value=''; btn.disabled=true;

  const wait=add('','aria');
  wait.querySelector('.msg').innerHTML='<span class="dots"><i></i><i></i><i></i></span>';

  try{
    const r=await fetch('/api/message',{
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({message:text})
    });
    const data=await r.json().catch(()=>({replies:[]}));
    wait.remove();
    const replies=(data&&data.replies)||[];
    if(!replies.length) add("I didn't catch that. Try again?",'aria');
    else replies.forEach(m=>add(m,'aria',looksLikeQuestion(m)));
  }catch(err){
    wait.remove();
    add("Couldn't reach the server. Check your connection and try again.",'aria');
  }
  btn.disabled=false; input.focus();
}

form.addEventListener('submit',e=>{e.preventDefault(); say(input.value.trim());});
starters?.addEventListener('click',e=>{
  const chip=e.target.closest('[data-say]');
  if(chip) say(chip.dataset.say);
});
</script>
</body>
</html>
"""


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
