"""Voice-note transcript -> structured order, using an LLM.

The model only splits the customer's words into order lines. It never sets
prices or picks the final item: bko.slip does that from the shop's rate list.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

LANGUAGES = ("hi", "en", "ta", "te", "kn", "mr", "bn", "gu", "pa", "ml")
# Ultra answered in ~1.4 s on 2026-11-27 while lightning timed out on every key.
MODELS = ("nvidia/nemotron-3-ultra-550b-a55b", "nvidia/nemotron-3.5-lightning-30b-a3b")
CALL_TIMEOUT = 12   # seconds for one request
TOTAL_BUDGET = 30   # seconds for all keys and models together, so a slow API can't freeze an order
# Nemotron "thinks" by default and can spend every token before answering.
NO_THINKING = {"chat_template_kwargs": {"enable_thinking": False}}
NVIDIA_URL = "https://integrate.api.nvidia.com/v1"
ULTRON_DIR = Path(__file__).resolve().parents[3] / "ultron"

PROMPT = """You read a customer's voice-note order to an Indian kirana shop and return ONLY JSON.
The note may be Hindi, English, Hinglish, Tamil or another Indian language, possibly mixed.

Shop items (for reference, the customer may use any word for them):
{items}

Return exactly this JSON shape:
{{"language": "<2-letter code of the customer's main language: hi, en, ta, ...>",
  "repeat_usual": <true if they ask for their usual/monthly/same list>,
  "delivery": "<a delivery TIME or instruction in their words (e.g. shaam ko, before 6, ghar pe), or null. Just 'send it' / 'bhej do' / 'anuppunga' is null. Not a payment date>",
  "credit": <true if they ask to pay later / udhaar / likh lo>,
  "pay_by": "<when they promise to pay, in their words, or null>",
  "items": [{{"said": "<their exact words for this item>",
              "item": "<short English name; brand or variety ONLY if they said it, e.g. oil, rice, Fortune oil, Parle-G>",
              "specific": <true only if they named the exact brand or variety themselves>,
              "qty": <number, convert number words carefully (see list below)>,
              "from_last_time": <true if they say same as last time / woh wala>}}]}}
Number words (spelling from speech-to-text may be rough):
Hindi: ek 1, do 2, teen 3, char 4, paanch 5, chhe 6, saat 7, aath 8, nau 9, das 10, aadha 0.5, dedh 1.5, dhai 2.5.
Kannada: ondu/ಒಂದು 1, eradu/ಎರಡು/ಎರ್ಡು 2, mooru/ಮೂರು 3, naalku/ನಾಲ್ಕು 4, aidu/ಐದು 5, aaru/ಆರು 6, elu/ಏಳು 7, entu/ಎಂಟು 8, ombattu 9, hattu 10, ardha/ಅರ್ಧ 0.5.
Tamil: onnu/ஒன்னு/ஒரு 1, rendu/ரெண்டு/இரண்டு 2, moonu/மூணு 3, naalu/நாலு 4, anju/அஞ்சு 5, aaru 6, ezhu 7, ettu/எட்டு 8, onbadhu 9, pathu 10, arai/அரை 0.5.
Telugu: okati 1, rendu 2, moodu 3, naalugu 4, aidu 5, aaru 6, edu 7, enimidi 8, tommidi 9, padi 10, ara 0.5.
If no number is said, qty is 1.
If they correct themselves ("do nahi, teen kar do", "no, make it three", "cancel the Maggi"), use only the
final version: corrected quantity, cancelled items removed.
Include EVERY item they ask for, even if it is NOT in the shop list (the shop will add it or reply).
Ignore any price the customer says; the shop sets prices.
Rules: never invent items they did not say. Never pick a brand or variety for them:
"arisi" or "chawal" is just "rice", "tel" is just "oil".

Voice note: "{transcript}"
"""


def build_prompt(transcript: str, item_names: list[str]) -> str:
    return PROMPT.format(items=", ".join(item_names), transcript=transcript.replace('"', "'"))


def _extract_json(text: str) -> dict | None:
    text = re.sub(r"```(?:json)?", "", text or "")
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _clean_item(raw: object) -> dict | None:
    if not isinstance(raw, dict):
        return None
    said = str(raw.get("said") or "").strip()
    item = str(raw.get("item") or said).strip()
    if not said and not item:
        return None
    try:
        qty = float(raw.get("qty") or 1)
    except (TypeError, ValueError):
        qty = 1.0
    return {
        "said": said or item, "item": item, "qty": qty,
        "specific": bool(raw.get("specific", True)),
        "from_last_time": bool(raw.get("from_last_time")),
    }


def parse_reply(text: str) -> dict | None:
    data = _extract_json(text)
    if data is None:
        return None
    language = str(data.get("language") or "hi").lower()[:2]
    items = [clean for clean in (_clean_item(raw) for raw in data.get("items") or []) if clean]
    delivery = _optional_text(data.get("delivery"))
    pay_by = _optional_text(data.get("pay_by"))
    if delivery and pay_by and delivery.lower() == pay_by.lower():
        delivery = None  # the model copied the payment promise into delivery
    return {
        "language": language if language in LANGUAGES else "hi",
        "repeat_usual": bool(data.get("repeat_usual")),
        "delivery": delivery,
        "credit": bool(data.get("credit")),
        "pay_by": pay_by,
        "items": items,
    }


def _optional_text(value: object) -> str | None:
    if value in (None, "", "null"):
        return None
    return str(value).strip() or None


def _nvidia_keys() -> list[str]:
    """NVIDIA_API_KEY (comma-separated for several) first; else Ultron's encrypted settings if present."""
    from_env = [k.strip() for k in os.environ.get("NVIDIA_API_KEY", "").split(",") if k.strip()]
    if from_env:
        return from_env
    if not (ULTRON_DIR / "quick_answer.py").exists():
        return []
    if str(ULTRON_DIR) not in sys.path:
        sys.path.insert(0, str(ULTRON_DIR))
    import quick_answer  # Ultron's key loader (keys stay encrypted in ~/.ultron/config.json)

    return list(quick_answer._keys())


def ask_nvidia(prompt: str, timeout: int = CALL_TIMEOUT, budget: int = TOTAL_BUDGET) -> str:
    """Send the prompt to NVIDIA's API, trying keys and models until one answers or the budget runs out."""
    from openai import OpenAI

    keys = _nvidia_keys()
    if not keys:
        raise RuntimeError("No NVIDIA API key: set NVIDIA_API_KEY (free at build.nvidia.com)")
    errors = []
    deadline = time.monotonic() + budget
    for model in MODELS:
        for key in keys:
            remaining = deadline - time.monotonic()
            if remaining < 2:
                errors.append(f"gave up after {budget} s")
                raise RuntimeError("; ".join(errors))
            try:
                client = OpenAI(api_key=key, base_url=NVIDIA_URL, max_retries=0, timeout=min(timeout, remaining))
                reply = client.chat.completions.create(
                    model=model, temperature=0.1, max_tokens=900,
                    messages=[{"role": "user", "content": prompt}], extra_body=NO_THINKING,
                )
                content = reply.choices[0].message.content or ""
                if content.strip():
                    return content
                errors.append(f"{model}: empty reply ({reply.choices[0].finish_reason})")
            except Exception as exc:  # try the next key/model, report all if none work
                errors.append(f"{model}: {type(exc).__name__}: {str(exc)[:120]}")
    raise RuntimeError("; ".join(errors))


def parse_order(transcript: str, item_names: list[str],
                ask: Callable[[str], str] = ask_nvidia) -> dict:
    if not transcript.strip():
        return {"ok": False, "error": "Empty voice note", "parsed": None}
    try:
        reply = ask(build_prompt(transcript, item_names))
    except Exception as exc:
        log.warning("Order brain failed: %s", exc)
        return {"ok": False, "error": f"AI brain unavailable: {exc}", "parsed": None}
    parsed = parse_reply(reply)
    if parsed is None:
        return {"ok": False, "error": "AI reply was not valid JSON", "parsed": None}
    return {"ok": True, "error": None, "parsed": parsed}
