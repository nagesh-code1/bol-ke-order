from bko.ears import MAX_AUDIO_BYTES, transcribe


def engine(text="", language="hi", fail=None):
    calls = []

    def run(audio, filename, language_hint):
        calls.append((len(audio), filename, language_hint))
        if fail:
            raise RuntimeError(fail)
        return {"text": text, "language": language}

    return run, calls


def test_uses_first_engine_when_it_works():
    groq, groq_calls = engine("ek kilo cheeni")
    local, local_calls = engine("should not run")
    result = transcribe(b"audio-bytes", "note.ogg", engines=[("groq", groq), ("local", local)])
    assert result["ok"] is True
    assert result["text"] == "ek kilo cheeni"
    assert result["engine"] == "groq"
    assert local_calls == []


def test_falls_back_when_first_engine_fails():
    groq, _ = engine(fail="429 rate limit")
    local, _ = engine("do Parle-G", language="hi")
    result = transcribe(b"audio-bytes", "note.ogg", engines=[("groq", groq), ("local", local)])
    assert result["ok"] is True
    assert result["engine"] == "local"
    assert "groq" in result["warnings"][0]


def test_blank_transcript_counts_as_failure():
    groq, _ = engine("   ")
    local, _ = engine("ek tel")
    result = transcribe(b"audio-bytes", "note.ogg", engines=[("groq", groq), ("local", local)])
    assert result["engine"] == "local"


def test_all_engines_failing_reports_error():
    groq, _ = engine(fail="no key")
    local, _ = engine(fail="model missing")
    result = transcribe(b"audio-bytes", "note.ogg", engines=[("groq", groq), ("local", local)])
    assert result["ok"] is False
    assert "no key" in result["error"] and "model missing" in result["error"]


def test_empty_audio_rejected():
    groq, calls = engine("x")
    result = transcribe(b"", "note.ogg", engines=[("groq", groq)])
    assert result["ok"] is False
    assert calls == []


def test_oversized_audio_rejected():
    groq, calls = engine("x")
    result = transcribe(b"0" * (MAX_AUDIO_BYTES + 1), "note.ogg", engines=[("groq", groq)])
    assert result["ok"] is False
    assert calls == []


def test_language_code_is_normalised():
    groq, _ = engine("vanakkam", language="Tamil")
    result = transcribe(b"audio", "note.ogg", engines=[("groq", groq)])
    assert result["language"] == "ta"
