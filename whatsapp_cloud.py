"""WhatsApp Cloud API transport -- the hostable one.

`whatsapp.py` drives WhatsApp Web through a headless Chrome logged in as a
linked device. That works, and it is what the local demo uses, but it cannot
be deployed: it needs a real browser, a linked handset, and a session folder
that must never leave the machine. So the claim "a student can reach Aria on
WhatsApp" could only ever be demonstrated on a laptop.

This is Meta's official Cloud API instead. It is a webhook, so it runs
wherever the web app runs, it works against a real WhatsApp number anyone can
message, and it is sanctioned rather than reverse-engineered -- no risk of the
number being banned for automation.

Same engine either way. `tutor.handle()` takes a phone and a send callback and
does not care which of these delivered the message.

Configure via:
    WHATSAPP_TOKEN             permanent access token from the Meta app
    WHATSAPP_PHONE_NUMBER_ID   the sending number's id, not the number itself
    WHATSAPP_VERIFY_TOKEN      any string; Meta echoes it when registering
    WHATSAPP_APP_SECRET        optional but recommended: verifies signatures
"""

import hashlib
import hmac
import json
import urllib.error
import urllib.request

import config

_GRAPH = "https://graph.facebook.com"


def is_configured() -> bool:
    return config.USE_WHATSAPP_CLOUD


def send_message(phone: str, text: str) -> bool:
    """Deliver one message. Returns False rather than raising: a send failure
    should never take down the webhook and cause Meta to retry the whole
    delivery, which would re-run the tutor and double-answer the student."""
    if not is_configured():
        return False
    url = (f"{_GRAPH}/{config.WHATSAPP_API_VERSION}/"
           f"{config.WHATSAPP_PHONE_NUMBER_ID}/messages")
    payload = json.dumps({
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": phone,
        "type": "text",
        # WhatsApp renders long text fine; Aria's messages are short by design.
        "text": {"preview_url": False, "body": text[:4000]},
    }).encode("utf-8")

    request = urllib.request.Request(
        url, data=payload, method="POST",
        headers={"Authorization": f"Bearer {config.WHATSAPP_TOKEN}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return 200 <= response.status < 300
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:300]
        print(f"whatsapp send failed {exc.code}: {body}", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"whatsapp send error: {type(exc).__name__}: {exc}", flush=True)
    return False


def signature_ok(raw_body: bytes, header: str | None) -> bool:
    """Verify X-Hub-Signature-256.

    Without this the webhook is an open write endpoint: anyone who learns the
    URL can post a payload claiming to be any phone number, corrupt that
    student's mastery record, and read the replies back.

    An unset app secret fails closed rather than open. The temptation is to
    skip the check so local testing is easy, but "unconfigured" is exactly the
    state a rushed deployment is in, and that is when the endpoint is public.
    Local tests set the secret; there is no path here that accepts unsigned
    traffic.
    """
    if not config.WHATSAPP_APP_SECRET:
        return False
    if not header or not header.startswith("sha256="):
        return False
    digest = hmac.new(config.WHATSAPP_APP_SECRET.encode("utf-8"),
                      raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, header.split("=", 1)[1])


def _tapped(message: dict) -> str | None:
    """The text behind a button tap, or None if this was not one.

    Template quick-replies come back as type "button" carrying the payload we
    set when sending. Interactive messages use a different shape again, with
    the choice nested under `interactive`. Both are a student answering, and
    both must reach the tutor as the word they would otherwise have typed --
    a student on a cheap handset over slow data taps rather than types, so
    dropping these breaks precisely the people the buttons are there for.
    """
    kind = message.get("type")
    if kind == "button":
        button = message.get("button") or {}
        return (button.get("payload") or button.get("text") or "").strip()
    if kind == "interactive":
        interactive = message.get("interactive") or {}
        reply = (interactive.get("button_reply")
                 or interactive.get("list_reply") or {})
        return (reply.get("id") or reply.get("title") or "").strip()
    return None


def extract_messages(payload: dict) -> list[tuple[str, str, str]]:
    """Pull (phone, text, message_id) out of a webhook payload.

    Meta nests these several layers deep and mixes in delivery receipts and
    read statuses, which must be ignored -- treating a status callback as a
    student message would have Aria replying to herself.
    """
    out: list[tuple[str, str, str]] = []
    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value") or {}
            for message in value.get("messages", []) or []:
                phone = message.get("from", "")
                message_id = message.get("id", "")

                tapped = _tapped(message)
                if tapped is not None:
                    # An empty payload is a malformed tap, not a voice note;
                    # it still falls through to the "text only" reply below.
                    out.append((phone, tapped, message_id))
                    continue

                if message.get("type") != "text":
                    # Voice notes and images are a deliberate non-goal for now;
                    # tell the student rather than silently ignoring them.
                    out.append((phone, "", message_id))
                    continue

                body = ((message.get("text") or {}).get("body") or "").strip()
                out.append((phone, body, message_id))
    return [(p, t, i) for p, t, i in out if p]
