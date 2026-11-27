import json
from pathlib import Path

from bko.catalog import Catalog
from bko.pipeline import process_note

DATA = Path(__file__).resolve().parent / "fixtures"
CATALOG = Catalog.load(DATA / "rate_list.json")


def fake_ears(text, ok=True):
    def run(audio, filename):
        if not ok:
            return {"ok": False, "error": "mic noise", "text": "", "language": None, "engine": None}
        return {"ok": True, "error": None, "text": text, "language": "hi", "engine": "fake", "secs": 0.1}
    return run


def fake_brain(parsed, ok=True):
    def run(transcript, names):
        if not ok:
            return {"ok": False, "error": "brain down", "parsed": None}
        return {"ok": True, "error": None, "parsed": parsed}
    return run


def test_full_chain_produces_priced_slip():
    parsed = {"language": "hi", "items": [{"said": "do Parle-G", "item": "Parle-G", "qty": 2}]}
    result = process_note(b"x", "n.ogg", CATALOG, None, transcribe=fake_ears("do Parle-G"), parse=fake_brain(parsed))
    assert result["ok"] is True
    assert result["slip"]["total"] == 20
    assert result["speech_engine"] == "fake"
    json.dumps(result)  # must be JSON-serialisable for the API


def test_speech_failure_is_reported_with_stage():
    result = process_note(b"x", "n.ogg", CATALOG, None, transcribe=fake_ears("", ok=False), parse=fake_brain({}))
    assert result == {"ok": False, "stage": "speech", "error": "mic noise", "transcript": "", "slip": None}


def test_brain_failure_keeps_transcript_for_owner():
    result = process_note(b"x", "n.ogg", CATALOG, None, transcribe=fake_ears("ek cheeni"),
                          parse=fake_brain(None, ok=False))
    assert result["ok"] is False
    assert result["stage"] == "understanding"
    assert result["transcript"] == "ek cheeni"
