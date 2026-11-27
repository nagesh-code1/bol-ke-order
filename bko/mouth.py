"""Reply to the customer in their own language: text, then speech (Edge TTS)."""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

VOICES = {
    "hi": "hi-IN-SwaraNeural", "ta": "ta-IN-PallaviNeural", "en": "en-IN-NeerjaNeural",
    "te": "te-IN-ShrutiNeural", "kn": "kn-IN-SapnaNeural", "mr": "mr-IN-AarohiNeural",
    "bn": "bn-IN-TanishaaNeural", "gu": "gu-IN-DhwaniNeural", "ml": "ml-IN-SobhanaNeural",
    "ur": "ur-IN-GulNeural",
}

CONFIRM = {
    "hi": "आपका ऑर्डर मिल गया। कुल {total} रुपये{credit}।{delivery} धन्यवाद!",
    "en": "Got your order. Total {total} rupees{credit}.{delivery} Thank you!",
    "ta": "உங்கள் ஆர்டர் கிடைத்தது. மொத்தம் {total} ரூபாய்{credit}.{delivery} நன்றி!",
    "kn": "ನಿಮ್ಮ ಆರ್ಡರ್ ಸಿಕ್ಕಿದೆ. ಒಟ್ಟು {total} ರೂಪಾಯಿ{credit}.{delivery} ಧನ್ಯವಾದಗಳು!",
}
CREDIT = {"hi": ", खाते में लिख दिया", "en": ", added to your account", "ta": ", கணக்கில் எழுதிவிட்டோம்",
          "kn": ", ಖಾತೆಗೆ ಬರೆದಿದ್ದೇವೆ"}
DELIVERY = {"hi": " आपके बताए समय पर भेज देंगे।", "en": " We'll deliver at the time you asked.",
            "ta": " நீங்கள் சொன்ன நேரத்தில் அனுப்புகிறோம்.", "kn": " ನೀವು ಹೇಳಿದ ಸಮಯಕ್ಕೆ ಕಳುಹಿಸುತ್ತೇವೆ."}
SUBSTITUTE = {"hi": "{name} खत्म हो गया है, {sub} चलेगा?", "en": "{name} is out of stock. Is {sub} okay?",
              "ta": "{name} இல்லை, {sub} பரவாயில்லையா?", "kn": "{name} ಖಾಲಿಯಾಗಿದೆ, {sub} ಆಗುತ್ತಾ?"}
NO_SUBSTITUTE = {"hi": "माफ़ कीजिए, {name} अभी नहीं है।", "en": "Sorry, {name} is out of stock.",
                 "ta": "மன்னிக்கவும், {name} இப்போது இல்லை.", "kn": "ಕ್ಷಮಿಸಿ, {name} ಈಗ ಇಲ್ಲ."}


def _pick(table: dict, language: str) -> str:
    return table.get(language) or table["en"]


def _language(slip: dict) -> str:
    language = slip.get("language") or "en"
    return language if language in CONFIRM else "en"


def _money(value: float) -> str:
    return f"{value:.0f}" if float(value).is_integer() else f"{value:.2f}"


def _open_questions(slip: dict, language: str) -> list[str]:
    asks = []
    for line in slip.get("lines", []):
        if line["status"] in ("ask", "unknown") and line.get("question"):
            asks.append(line["question"])
        elif line["status"] == "out_of_stock":
            sub = line.get("substitute_name")
            template = _pick(SUBSTITUTE if sub else NO_SUBSTITUTE, language)
            asks.append(template.format(name=line["name"], sub=sub))
    return asks


def reply_text(slip: dict) -> str:
    """What the shop says back. Questions first; a confirmation only when the order is clear."""
    language = _language(slip)
    questions = _open_questions(slip, language)
    if questions:
        return " ".join(questions)
    return _pick(CONFIRM, language).format(
        total=_money(slip["total"]),
        credit=_pick(CREDIT, language) if slip.get("credit") else "",
        delivery=_pick(DELIVERY, language) if slip.get("delivery") else "",
    )


SUMMARY = {
    "hi": ("आज {orders} ऑर्डर आए, {accepted} पक्के हुए। बिक्री {sales} रुपये, उधार {credit} रुपये।",
           " {pending} ऑर्डर आपका इंतज़ार कर रहे हैं।", " खत्म हो गया: {items}।", "आज अभी तक कोई ऑर्डर नहीं आया।"),
    "en": ("Today {orders} orders came in, {accepted} accepted. Sales {sales} rupees, credit given {credit} rupees.",
           " {pending} orders are waiting for you.", " Out of stock: {items}.", "No orders yet today."),
    "kn": ("ಇಂದು {orders} ಆರ್ಡರ್ ಬಂದಿವೆ, {accepted} ಖಚಿತವಾಗಿವೆ. ಮಾರಾಟ {sales} ರೂಪಾಯಿ, ಸಾಲ {credit} ರೂಪಾಯಿ.",
           " {pending} ಆರ್ಡರ್ ನಿಮಗಾಗಿ ಕಾಯುತ್ತಿವೆ.", " ಖಾಲಿಯಾಗಿದೆ: {items}.", "ಇಂದು ಇನ್ನೂ ಯಾವುದೇ ಆರ್ಡರ್ ಬಂದಿಲ್ಲ."),
}


def summary_text(summary: dict, out_of_stock: list[str], language: str = "hi") -> str:
    """The owner's spoken end-of-day report: counts, money, waiting orders and what's run out."""
    main, waiting, stock, quiet = SUMMARY.get(language) or SUMMARY["en"]
    if not summary.get("orders"):
        text = quiet
    else:
        text = main.format(orders=summary["orders"], accepted=summary["accepted"],
                           sales=_money(summary["sales"]), credit=_money(summary["credit_given"]))
        if summary.get("pending"):
            text += waiting.format(pending=summary["pending"])
    if out_of_stock:
        text += stock.format(items=", ".join(out_of_stock[:8]))
    return text


def voice_for(language: str) -> str:
    return VOICES.get(language, VOICES["en"])


async def _speak(text: str, voice: str, path: Path) -> None:
    import edge_tts

    await edge_tts.Communicate(text, voice).save(str(path))


def speak(text: str, language: str, folder: Path) -> Path:
    """Synthesise the reply to an mp3 (cached by text+voice). Needs internet for Edge TTS."""
    voice = voice_for(language)
    folder.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(f"{voice}|{text}".encode("utf-8")).hexdigest()[:16]
    path = folder / f"reply_{key}.mp3"
    if not path.exists() or path.stat().st_size == 0:
        asyncio.run(_speak(text, voice, path))
    return path
