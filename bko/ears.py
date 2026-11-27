"""Voice note audio -> text, with a cloud engine first and an offline fallback.

Engines are plain callables (audio, filename, language_hint) -> {"text", "language"}
so tests can swap them out and the app keeps working when one engine is down.
"""
from __future__ import annotations

import logging
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

MAX_AUDIO_BYTES = 20 * 1024 * 1024  # Groq's upload limit is 25 MB; WhatsApp notes are far smaller
GROQ_URL = "https://api.groq.com/openai/v1"
GROQ_MODEL = "whisper-large-v3"
LOCAL_MODEL = "small"
ULTRON_DIR = Path(__file__).resolve().parents[3] / "ultron"

LANGUAGE_NAMES = {
    "hindi": "hi", "english": "en", "tamil": "ta", "telugu": "te", "kannada": "kn", "marathi": "mr",
    "bengali": "bn", "gujarati": "gu", "punjabi": "pa", "malayalam": "ml", "urdu": "ur",
}

Engine = Callable[[bytes, str, "str | None"], dict]


def _language_code(value: object) -> str | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    return LANGUAGE_NAMES.get(text, text[:2])


def _groq_key() -> str:
    """GROQ_API_KEY first; else Ultron's encrypted settings if present."""
    from_env = os.environ.get("GROQ_API_KEY", "").strip()
    if from_env:
        return from_env
    if not (ULTRON_DIR / "state.py").exists():
        raise RuntimeError("No Groq API key: set GROQ_API_KEY (free at console.groq.com)")
    if str(ULTRON_DIR) not in sys.path:
        sys.path.insert(0, str(ULTRON_DIR))
    import state  # Ultron's settings; keys stay encrypted on disk

    cfg = state.load_config() or {}
    brains = [cfg] + list(cfg.get("extra_brains", []))
    for brain in brains:
        if brain.get("provider") == "groq" and brain.get("api_key"):
            return brain["api_key"]
    raise RuntimeError("No Groq API key in Ultron settings")


def groq_engine(audio: bytes, filename: str, language_hint: str | None) -> dict:
    from openai import OpenAI

    client = OpenAI(api_key=_groq_key(), base_url=GROQ_URL, max_retries=0, timeout=60)
    kwargs = {"language": language_hint} if language_hint else {}
    reply = client.audio.transcriptions.create(
        model=GROQ_MODEL, file=(filename, audio), response_format="verbose_json", temperature=0.0, **kwargs)
    return {"text": getattr(reply, "text", "") or "", "language": getattr(reply, "language", None)}


_local_model = None
_local_lock = threading.Lock()


def _load_local():
    global _local_model
    with _local_lock:
        if _local_model is None:
            from faster_whisper import WhisperModel

            _local_model = WhisperModel(LOCAL_MODEL, device="cpu", compute_type="int8", local_files_only=True)
    return _local_model


def local_engine(audio: bytes, filename: str, language_hint: str | None) -> dict:
    suffix = Path(filename).suffix or ".ogg"
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / f"note{suffix}"
        path.write_bytes(audio)
        segments, info = _load_local().transcribe(
            str(path), language=language_hint, beam_size=1, vad_filter=True, condition_on_previous_text=False)
        text = " ".join(segment.text.strip() for segment in segments)
    return {"text": text, "language": info.language}


DEFAULT_ENGINES: list[tuple[str, Engine]] = [("groq", groq_engine), ("local", local_engine)]


def transcribe(audio: bytes, filename: str = "note.ogg", language_hint: str | None = None,
               engines: list[tuple[str, Engine]] | None = None) -> dict:
    if not audio:
        return {"ok": False, "error": "Empty audio", "text": "", "language": None, "engine": None, "warnings": []}
    if len(audio) > MAX_AUDIO_BYTES:
        return {"ok": False, "error": "Voice note is too large (max 20 MB)", "text": "", "language": None,
                "engine": None, "warnings": []}

    warnings: list[str] = []
    for name, run in engines or DEFAULT_ENGINES:
        started = time.time()
        try:
            result = run(audio, filename, language_hint)
        except Exception as exc:  # fall through to the next engine
            warnings.append(f"{name}: {type(exc).__name__}: {str(exc)[:150]}")
            log.warning("Speech engine %s failed: %s", name, exc)
            continue
        text = (result.get("text") or "").strip()
        if not text:
            warnings.append(f"{name}: empty transcript")
            continue
        return {"ok": True, "error": None, "text": text, "language": _language_code(result.get("language")),
                "engine": name, "secs": round(time.time() - started, 2), "warnings": warnings}
    return {"ok": False, "error": "; ".join(warnings) or "No speech engine", "text": "", "language": None,
            "engine": None, "warnings": warnings}
