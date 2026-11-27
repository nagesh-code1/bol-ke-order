from bko.mouth import reply_text, voice_for


def line(status="ok", name="Parle-G", question=None, substitute_name=None):
    return {"status": status, "name": name, "question": question, "substitute_name": substitute_name,
            "said": name, "qty": 1, "amount": 10}


def slip(language="hi", total=493.0, credit=False, delivery=None, lines=None):
    return {"language": language, "total": total, "credit": credit, "delivery": delivery,
            "lines": lines if lines is not None else [line()]}


def test_hindi_confirmation_has_total_in_devanagari():
    text = reply_text(slip(total=493.0))
    assert "493" in text and "रुपये" in text
    assert "493.0" not in text


def test_credit_and_delivery_mentioned():
    text = reply_text(slip(credit=True, delivery="shaam"))
    assert "खाते" in text
    assert "समय" in text


def test_tamil_confirmation():
    text = reply_text(slip(language="ta", total=665))
    assert "665" in text and "ரூபாய்" in text


def test_open_question_is_asked_instead_of_confirming():
    lines = [line(), line(status="ask", question="कौन सा तेल चाहिए — Fortune या Saffola?")]
    text = reply_text(slip(lines=lines))
    assert "Fortune या Saffola" in text
    assert "रुपये" not in text  # no total until everything is clear


def test_out_of_stock_offers_substitute():
    lines = [line(status="out_of_stock", name="Amul Butter", substitute_name="Mother Dairy Butter")]
    text = reply_text(slip(lines=lines))
    assert "Amul Butter" in text and "Mother Dairy Butter" in text


def test_kannada_confirmation():
    text = reply_text(slip(language="kn", total=50))
    assert "50" in text and "ರೂಪಾಯಿ" in text


def test_unknown_language_falls_back_to_english():
    assert "rupees" in reply_text(slip(language="xx", total=10))


def test_owner_summary_in_hindi_mentions_numbers_and_stock():
    from bko.mouth import summary_text
    text = summary_text({"orders": 11, "accepted": 4, "pending": 3, "sales": 1530.0, "credit_given": 156.0},
                        ["Amul Butter", "Sugar"], "hi")
    for part in ("11", "4", "1530", "156", "3", "Amul Butter", "Sugar"):
        assert part in text
    assert "1530.0" not in text


def test_owner_summary_quiet_day_in_english():
    from bko.mouth import summary_text
    text = summary_text({"orders": 0, "accepted": 0, "pending": 0, "sales": 0, "credit_given": 0}, [], "en")
    assert "No orders" in text


def test_voice_per_language():
    assert voice_for("hi").startswith("hi-IN")
    assert voice_for("ta").startswith("ta-IN")
    assert voice_for("zz").startswith("en-IN")
