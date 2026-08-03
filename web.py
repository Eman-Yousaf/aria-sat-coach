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
import os
import re
import secrets
import sqlite3

from fastapi import Cookie, FastAPI, Request, Response
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel

import tutor
from database import init_db

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
def message(turn: Turn, response: Response,
            aria_session: str | None = Cookie(default=None)):
    session = aria_session if _valid(aria_session) else _new_session()
    response.set_cookie(SESSION_COOKIE, session, httponly=True,
                        samesite="lax", max_age=60 * 60 * 24 * 30)

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
CHAT_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Aria - SAT coach</title>
<style>
  :root{
    --bg:#0f1115; --panel:#171a21; --line:#262b36; --ink:#e8eaed;
    --muted:#9aa3b2; --me:#2b5cff; --accent:#5cc8a0;
  }
  @media (prefers-color-scheme: light){
    :root{ --bg:#f4f5f7; --panel:#fff; --line:#e2e5ea; --ink:#14171c;
           --muted:#5d6673; --me:#2b5cff; --accent:#118a63; }
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
    font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
    display:flex;flex-direction:column;height:100dvh}
  header{padding:12px 16px;border-bottom:1px solid var(--line);
    display:flex;align-items:center;gap:10px;background:var(--panel);flex:none}
  header b{font-size:16px}
  header span{color:var(--muted);font-size:13px}
  header a{margin-left:auto;color:var(--muted);font-size:13px;text-decoration:none;
    border:1px solid var(--line);padding:5px 10px;border-radius:7px}
  header a:hover{color:var(--ink)}
  #log{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:10px}
  .msg{max-width:min(680px,86%);padding:10px 13px;border-radius:13px;
    white-space:pre-wrap;word-wrap:break-word}
  .aria{background:var(--panel);border:1px solid var(--line);
    border-bottom-left-radius:4px;align-self:flex-start}
  .me{background:var(--me);color:#fff;border-bottom-right-radius:4px;align-self:flex-end}
  .hint{color:var(--muted);font-size:13px;text-align:center;padding:6px}
  form{display:flex;gap:8px;padding:12px;border-top:1px solid var(--line);
    background:var(--panel);flex:none}
  input{flex:1;padding:11px 13px;border-radius:9px;border:1px solid var(--line);
    background:var(--bg);color:var(--ink);font-size:15px}
  input:focus{outline:2px solid var(--me);outline-offset:-1px}
  button{padding:11px 17px;border:0;border-radius:9px;background:var(--me);
    color:#fff;font-size:15px;font-weight:600;cursor:pointer}
  button:disabled{opacity:.5;cursor:default}
  .dots span{display:inline-block;width:5px;height:5px;margin-right:3px;
    border-radius:50%;background:var(--muted);animation:b 1.2s infinite}
  .dots span:nth-child(2){animation-delay:.2s}
  .dots span:nth-child(3){animation-delay:.4s}
  @keyframes b{0%,60%,100%{opacity:.25}30%{opacity:1}}
</style>
</head>
<body>
<header>
  <b>Aria</b><span>SAT coach</span>
  <a href="/dashboard">Coach view</a>
</header>

<div id="log">
  <div class="msg aria">Hi! I'm Aria, your SAT coach. Say hello to start.</div>
  <div class="hint">Try: <b>hi</b> &rarr; your name &rarr; a target score &rarr; how many minutes you have.</div>
</div>

<form id="f" autocomplete="off">
  <input id="i" placeholder="Type a message" autofocus aria-label="Message">
  <button id="b">Send</button>
</form>

<script>
const log=document.getElementById('log'), form=document.getElementById('f'),
      input=document.getElementById('i'), btn=document.getElementById('b');

function add(text, who){
  const d=document.createElement('div');
  d.className='msg '+who; d.textContent=text;
  log.appendChild(d); log.scrollTop=log.scrollHeight; return d;
}

form.addEventListener('submit', async (e)=>{
  e.preventDefault();
  const text=input.value.trim(); if(!text) return;
  add(text,'me'); input.value=''; btn.disabled=true;

  const wait=add('','aria');
  wait.innerHTML='<span class="dots"><span></span><span></span><span></span></span>';

  try{
    const r=await fetch('/api/message',{
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({message:text})
    });
    const data=await r.json();
    wait.remove();
    if(!data.replies || !data.replies.length){
      add("(no reply)", 'aria');
    } else {
      for(const m of data.replies) add(m,'aria');
    }
  }catch(err){
    wait.remove();
    add("Couldn't reach the server. Check your connection and try again.",'aria');
  }
  btn.disabled=false; input.focus();
});
</script>
</body>
</html>
"""


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
