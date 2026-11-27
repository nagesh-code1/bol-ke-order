import json

from bko.brain import build_prompt, parse_order, parse_reply


def fake_ask(reply_text):
    calls = []

    def ask(prompt):
        calls.append(prompt)
        return reply_text

    return ask, calls


def test_prompt_contains_transcript_and_shop_items():
    prompt = build_prompt("ek kilo cheeni", ["Sugar", "Tata Salt"])
    assert "ek kilo cheeni" in prompt
    assert "Sugar" in prompt
    assert "JSON" in prompt


def test_parses_fenced_json_reply():
    text = "```json\n" + json.dumps({
        "language": "hi", "repeat_usual": False, "delivery": "shaam", "credit": True,
        "items": [{"said": "do Parle-G", "item": "Parle-G", "qty": 2}],
    }) + "\n```"
    parsed = parse_reply(text)
    assert parsed["items"][0]["qty"] == 2
    assert parsed["credit"] is True
    assert parsed["delivery"] == "shaam"


def test_cleans_bad_fields():
    text = json.dumps({"language": "xx", "items": [{"said": "atta", "qty": "two"}, "junk", {"said": ""}]})
    parsed = parse_reply(text)
    assert parsed["language"] == "hi"
    assert parsed["items"] == [{"said": "atta", "item": "atta", "qty": 1.0, "specific": True, "from_last_time": False}]
    assert parsed["repeat_usual"] is False
    assert parsed["pay_by"] is None


def test_payment_date_copied_into_delivery_is_dropped():
    text = json.dumps({"credit": True, "delivery": "1 tarikh ko", "pay_by": "1 tarikh ko", "items": []})
    parsed = parse_reply(text)
    assert parsed["delivery"] is None
    assert parsed["pay_by"] == "1 tarikh ko"


def test_unparseable_reply_returns_none():
    assert parse_reply("sorry I cannot") is None


def test_parse_order_uses_injected_brain():
    ask, calls = fake_ask(json.dumps({"language": "ta", "items": [{"said": "arisi", "item": "rice", "qty": 5}]}))
    result = parse_order("anju kilo arisi", ["Sona Masoori Rice"], ask=ask)
    assert result["ok"] is True
    assert result["parsed"]["language"] == "ta"
    assert len(calls) == 1


def test_parse_order_reports_brain_failure():
    def broken(prompt):
        raise RuntimeError("429 rate limited")

    result = parse_order("ek cheeni", ["Sugar"], ask=broken)
    assert result["ok"] is False
    assert "429" in result["error"]


def test_nvidia_keys_come_from_environment_first(monkeypatch):
    from bko.brain import _nvidia_keys
    monkeypatch.setenv("NVIDIA_API_KEY", "key-a, key-b")
    assert _nvidia_keys() == ["key-a", "key-b"]


def test_groq_key_comes_from_environment_first(monkeypatch):
    from bko.ears import _groq_key
    monkeypatch.setenv("GROQ_API_KEY", "gk")
    assert _groq_key() == "gk"


def test_empty_transcript_is_rejected_without_calling_brain():
    ask, calls = fake_ask("{}")
    result = parse_order("   ", ["Sugar"], ask=ask)
    assert result["ok"] is False
    assert calls == []
