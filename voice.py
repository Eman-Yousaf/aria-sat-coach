"""Speech to text, so a student can talk instead of type.

Typing is a tax. It falls hardest on the students this is built for: a shared
handset, a cracked screen, a keyboard in the wrong script, or simply being
fifteen and faster at talking. A voice note is one press and a sentence.

Everything downstream is unchanged. `transcribe()` returns a string and
`tutor.handle()` receives it exactly as if it had been typed, so voice is a
transport detail and not a second conversation engine to keep in sync.

Failure is deliberately quiet. A transcription that did not happen returns
None, and the caller says so in words; it never raises into the webhook, where
a 500 would make Meta redeliver the message and answer the student twice.
"""

import io

import config

# Whisper's own ceiling is 25 MB. WhatsApp voice notes are Opus at roughly
# 1 KB per second, so this is minutes of speech -- the limit exists to stop a
# malicious upload, not to constrain a student.
MAX_AUDIO_BYTES = 20 * 1024 * 1024

# Below this, the student almost certainly fumbled the record button. Sending
# an empty string to the tutor reads as an off-script message and produces a
# confusing reply, so treat it as nothing said.
MIN_AUDIO_BYTES = 512


def is_configured() -> bool:
    return bool(config.USE_AZURE and config.AZURE_DEPLOYMENT_VOICE)


def transcribe(audio: bytes, filename: str = "voice.ogg") -> str | None:
    """Audio in, words out. None if it could not be done.

    `filename` matters more than it looks: the extension is how the service
    decides which decoder to use, and WhatsApp sends Opus in an Ogg container.
    """
    if not is_configured():
        return None
    if not audio or len(audio) < MIN_AUDIO_BYTES or len(audio) > MAX_AUDIO_BYTES:
        return None

    try:
        from openai import AzureOpenAI

        client = AzureOpenAI(
            azure_endpoint=config.AZURE_OPENAI_ENDPOINT,
            api_key=config.AZURE_OPENAI_API_KEY,
            api_version=config.AZURE_OPENAI_API_VERSION,
        )
        stream = io.BytesIO(audio)
        stream.name = filename
        result = client.audio.transcriptions.create(
            model=config.AZURE_DEPLOYMENT_VOICE,
            file=stream,
            # Nudges the decoder toward the vocabulary of the domain. Without
            # it "quadratic" and "denominator" come back as near-misses often
            # enough to matter, and a wrong word here becomes a wrong answer.
            prompt="SAT practice: algebra, quadratic, denominator, exponent, "
                   "linear equation, ratio, percentage, passage, evidence.",
        )
    except Exception as exc:  # noqa: BLE001
        print(f"transcription failed: {type(exc).__name__}: {exc}", flush=True)
        return None

    text = (getattr(result, "text", "") or "").strip()
    return text or None
