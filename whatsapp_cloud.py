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
from typing import NamedTuple

import config

_GRAPH = "https://graph.facebook.com"

# A voice note that would take longer to fetch than this is not worth holding
# the webhook open for; Meta retries a slow webhook, which double-answers.
_MEDIA_TIMEOUT = 20


class Inbound(NamedTuple):
    """One thing a student sent.

    `text` is what the tutor should act on -- typed, or tapped, or spoken and
    transcribed. `audio_id` is set when there is a voice note to fetch, and it
    is the caller's job to turn that into text; this module does transport and
    nothing else.
    """
    phone: str
    text: str
    message_id: str
    audio_id: str = ""
    unsupported: str = ""   # the media kind we cannot read, for the reply


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


# autonomy.py's trigger -> the approved template that says the same thing.
# Kept here rather than in autonomy.py because which templates exist is a fact
# about the Meta account, not about how Aria reasons. A trigger with no
# approved template simply cannot be sent outside the window, and saying
# nothing is the correct outcome -- there is no generic fallback, because a
# vague "come back and study" is exactly the notification this project exists
# to not send. See whatsapp_template.md.
TEMPLATES = {
    "first_nudge": "aria_first_nudge",
    "decay_risk": "aria_decay_review",
    "misconception_pattern": "aria_misconception",
    "high_value_idle": "aria_high_value_idle",
    "test_urgency": "aria_test_urgency",
}


def send_template(phone: str, template: str, params: list[str],
                  language: str = "en") -> bool:
    """Send a pre-approved template.

    Meta allows free-form text only within 24 hours of the student's last
    message. Outside that window this is the only way to reach them, which is
    to say it is the only way Aria's proactive outreach -- the thing that makes
    her an agent rather than a chatbot -- works on a hosted number at all.
    """
    if not is_configured() or not template:
        return False

    payload = json.dumps({
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": phone,
        "type": "template",
        "template": {
            "name": template,
            "language": {"code": language},
            "components": [{
                "type": "body",
                # Order is the contract: {{1}} is params[0]. The tables in
                # whatsapp_template.md are the spec for that ordering.
                "parameters": [{"type": "text", "text": str(p)}
                               for p in params],
            }] if params else [],
        },
    }).encode("utf-8")

    request = urllib.request.Request(
        f"{_GRAPH}/{config.WHATSAPP_API_VERSION}/"
        f"{config.WHATSAPP_PHONE_NUMBER_ID}/messages",
        data=payload, method="POST",
        headers={"Authorization": f"Bearer {config.WHATSAPP_TOKEN}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return 200 <= response.status < 300
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:300]
        # 132001 is "template does not exist / not approved in this language",
        # which during a hackathon is overwhelmingly the real cause.
        print(f"template send failed {exc.code} ({template}): {body}",
              flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"template send error: {type(exc).__name__}: {exc}", flush=True)
    return False


def send_decision(phone: str, decision, free_form: str,
                  within_window: bool) -> bool:
    """Deliver an outreach decision by whichever route is permitted.

    Inside the 24-hour window the student gets the real message, phrased for
    them. Outside it they get the template that carries the same numbers, or
    nothing at all -- which is the honest outcome, not a failure to paper over.
    """
    if within_window:
        return send_message(phone, free_form)
    template = TEMPLATES.get(getattr(decision, "trigger", ""))
    if not template:
        return False
    return send_template(phone, template,
                         getattr(decision, "template_params", []) or [])


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


def download_media(media_id: str) -> tuple[bytes, str] | None:
    """Fetch a media file by id. Returns (bytes, mime type), or None.

    Two round trips by design on Meta's side: the id resolves to a short-lived
    signed URL, which then has to be fetched with the same bearer token -- the
    URL alone is not enough, which is easy to miss and fails with a bare 401.
    """
    if not is_configured() or not media_id:
        return None
    auth = {"Authorization": f"Bearer {config.WHATSAPP_TOKEN}"}

    try:
        lookup = urllib.request.Request(
            f"{_GRAPH}/{config.WHATSAPP_API_VERSION}/{media_id}", headers=auth)
        with urllib.request.urlopen(lookup, timeout=_MEDIA_TIMEOUT) as response:
            meta = json.loads(response.read() or b"{}")
        url = meta.get("url")
        if not url:
            return None

        fetch = urllib.request.Request(url, headers=auth)
        with urllib.request.urlopen(fetch, timeout=_MEDIA_TIMEOUT) as response:
            return response.read(), meta.get("mime_type", "")
    except Exception as exc:  # noqa: BLE001
        print(f"media download failed: {type(exc).__name__}: {exc}", flush=True)
        return None


# Extensions the transcriber infers the decoder from. WhatsApp voice notes are
# audio/ogg; codecs= suffixes have to be stripped before this lookup.
_AUDIO_EXT = {"audio/ogg": "ogg", "audio/mpeg": "mp3", "audio/mp4": "m4a",
              "audio/aac": "m4a", "audio/amr": "amr", "audio/wav": "wav",
              "audio/webm": "webm"}


def audio_filename(mime: str) -> str:
    base = (mime or "").split(";")[0].strip().lower()
    return f"voice.{_AUDIO_EXT.get(base, 'ogg')}"


def extract_messages(payload: dict) -> list[Inbound]:
    """Pull the student messages out of a webhook payload.

    Meta nests these several layers deep and mixes in delivery receipts and
    read statuses, which must be ignored -- treating a status callback as a
    student message would have Aria replying to herself.
    """
    out: list[Inbound] = []
    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value") or {}
            for message in value.get("messages", []) or []:
                phone = message.get("from", "")
                message_id = message.get("id", "")
                kind = message.get("type")

                tapped = _tapped(message)
                if tapped is not None:
                    out.append(Inbound(phone, tapped, message_id))
                    continue

                if kind == "audio" or kind == "voice":
                    audio_id = (message.get(kind) or {}).get("id", "")
                    out.append(Inbound(phone, "", message_id,
                                       audio_id=audio_id))
                    continue

                if kind != "text":
                    # Photos of homework are a real request and a real
                    # non-goal; say which so the student knows it is a limit
                    # and not a failure.
                    out.append(Inbound(phone, "", message_id,
                                       unsupported=kind or "that"))
                    continue

                body = ((message.get("text") or {}).get("body") or "").strip()
                out.append(Inbound(phone, body, message_id))
    return [m for m in out if m.phone]
