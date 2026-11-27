"""One voice note -> transcript -> parsed order -> priced slip."""
from __future__ import annotations

import time
from dataclasses import asdict
from typing import Callable

from bko import brain, ears
from bko.catalog import Catalog
from bko.slip import build_slip


def item_names(catalog: Catalog) -> list[str]:
    return [f"{item.name} ({item.pack})" for item in catalog.items]


def process_note(audio: bytes, filename: str, catalog: Catalog, customer: dict | None,
                 transcribe: Callable = ears.transcribe, parse: Callable = brain.parse_order,
                 language_hint: str | None = None) -> dict:
    started = time.time()
    heard = transcribe(audio, filename, language_hint) if language_hint else transcribe(audio, filename)
    if not heard["ok"]:
        return {"ok": False, "stage": "speech", "error": heard["error"], "transcript": "", "slip": None}
    return process_text(heard["text"], catalog, customer, parse=parse,
                        extra={"speech_engine": heard["engine"], "speech_secs": heard.get("secs"),
                               "heard_language": heard["language"], "started": started})


def process_text(transcript: str, catalog: Catalog, customer: dict | None,
                 parse: Callable = brain.parse_order, extra: dict | None = None) -> dict:
    meta = dict(extra or {})
    started = meta.pop("started", time.time())
    understood = parse(transcript, item_names(catalog))
    if not understood["ok"]:
        return {"ok": False, "stage": "understanding", "error": understood["error"],
                "transcript": transcript, "slip": None, **meta}
    slip = build_slip(understood["parsed"], catalog, customer, transcript=transcript)
    return {"ok": True, "stage": "done", "error": None, "transcript": transcript,
            "parsed": understood["parsed"], "slip": asdict(slip),
            "total_secs": round(time.time() - started, 2), **meta}
