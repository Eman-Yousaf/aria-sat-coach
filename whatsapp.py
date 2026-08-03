import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler

BRIDGE_PORT = 18790
WEBHOOK_PORT = 18791

_on_reply = None
_bridge_proc: subprocess.Popen | None = None
_bridge_ready = False
_bridge_ready_lock = threading.Lock()
_chat_ids: dict[str, str] = {}
_chat_ids_lock = threading.Lock()


def log(level: str, message: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{ts}] [{level}] {message}", flush=True)


def _normalize_phone(phone: str) -> str:
    return re.sub(r"\D", "", phone)


def _bridge_health() -> dict | None:
    try:
        import urllib.request
        resp = urllib.request.urlopen(
            f"http://127.0.0.1:{BRIDGE_PORT}/health", timeout=3
        )
        return json.loads(resp.read().decode())
    except Exception:
        return None


def set_chat_id(phone: str, chat_id: str):
    phone = _normalize_phone(phone)
    if phone and chat_id:
        with _chat_ids_lock:
            _chat_ids[phone] = chat_id


def get_chat_id(phone: str) -> str | None:
    phone = _normalize_phone(phone)
    with _chat_ids_lock:
        return _chat_ids.get(phone)


def send_message(phone: str, message: str, chat_id: str | None = None) -> bool:
    phone = _normalize_phone(phone)
    chat_id = chat_id or get_chat_id(phone)
    try:
        import urllib.request
        payload = {"message": message}
        if chat_id:
            payload["chatId"] = chat_id
        else:
            payload["to"] = phone
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{BRIDGE_PORT}/send",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        t = time.time()
        resp = urllib.request.urlopen(req, timeout=30)
        elapsed = time.time() - t
        ok = resp.status == 200
        log("INFO", f"sent to +{phone} ({elapsed:.1f}s)")
        return ok
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:500]
        log("ERROR", f"send to +{phone} HTTP {e.code}: {body}")
        return False
    except Exception as e:
        log("ERROR", f"send to +{phone} failed: {e}")
        return False


def _start_bridge() -> subprocess.Popen | None:
    global _bridge_proc, _bridge_ready

    health = _bridge_health()
    if health and health.get("ready"):
        log("INFO", "reusing existing WhatsApp bridge on port 18790")
        with _bridge_ready_lock:
            _bridge_ready = True
        return None

    if health and not health.get("ready"):
        log("INFO", "existing bridge found but not ready yet, waiting...")

    log("INFO", "starting WhatsApp bridge (whatsapp-bridge.js)")
    js_path = os.path.join(os.path.dirname(__file__), "whatsapp-bridge.js")
    proc = subprocess.Popen(
        ["node", js_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    _bridge_proc = proc
    qr_shown = False

    def _read_stdout():
        global _bridge_ready
        for line in iter(proc.stdout.readline, ""):
            line = line.strip()
            if not line:
                continue
            if line.startswith("QRCODE:"):
                if not qr_shown:
                    qr_shown = True
                    print(line[len("QRCODE:"):], flush=True)
            elif "BRIDGE_AUTHENTICATED" in line:
                log("INFO", "bridge authenticated")
            elif "BRIDGE_READY" in line:
                log("INFO", "bridge ready")
                with _bridge_ready_lock:
                    _bridge_ready = True
            elif "BRIDGE_LISTENING:" in line:
                log("INFO", f"bridge listening on {line.split(':')[1]}")
            elif "send error:" in line:
                log("ERROR", f"bridge: {line}")
            elif "webhook error:" in line:
                log("WARN", f"bridge: {line}")
            elif "BRIDGE_" in line:
                log("WARN", line)
            else:
                log("DEBUG", f"bridge: {line}")

    t = threading.Thread(target=_read_stdout, daemon=True)
    t.start()

    for _ in range(120):
        if _bridge_ready:
            return proc
        health = _bridge_health()
        if health and health.get("ready"):
            with _bridge_ready_lock:
                _bridge_ready = True
            log("INFO", "bridge ready (via health check)")
            return proc
        time.sleep(0.5)

    log("WARN", "bridge did not report ready within 60s, continuing anyway")
    return proc


def _start_webhook() -> HTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body)
            except Exception:
                self.send_response(400)
                self.end_headers()
                return

            phone = _normalize_phone(data.get("from", ""))
            chat_id = data.get("chatId", "")
            msg = {
                "id": data.get("timestamp", str(time.time())),
                "from": phone,
                "chatId": chat_id,
                "body": data.get("body", "").strip(),
                "timestamp": data.get("timestamp", ""),
            }

            self.send_response(200)
            self.end_headers()

            if phone and msg["body"] and _on_reply:
                if chat_id:
                    set_chat_id(phone, chat_id)

                def _dispatch():
                    try:
                        _on_reply(msg)
                    except Exception as e:
                        log("ERROR", f"webhook handler failed: {e}")
                    log("INFO", f"webhook: message from {phone}")

                threading.Thread(target=_dispatch, daemon=True).start()

        def log_message(self, format, *args):
            pass

    server = HTTPServer(("127.0.0.1", WEBHOOK_PORT), Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    log("INFO", f"webhook server on port {WEBHOOK_PORT}")
    return server


def start_listener(on_reply):
    global _on_reply
    _on_reply = on_reply
    _start_bridge()
    _start_webhook()
    log("INFO", "WhatsApp listener active (direct bridge)")


def stop_listener():
    if _bridge_proc:
        _bridge_proc.terminate()
        log("INFO", "bridge stopped")
