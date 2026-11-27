import json
from pathlib import Path

import pytest

from bko.catalog import Catalog
from bko.slip import build_slip

DATA = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="module")
def catalog():
    return Catalog.load(DATA / "rate_list.json")


@pytest.fixture(scope="module")
def customers():
    raw = json.loads((DATA / "customers.json").read_text(encoding="utf-8"))
    return {c["id"]: c for c in raw["customers"]}


def parsed(items, **extra):
    base = {"language": "hi", "repeat_usual": False, "delivery": None, "credit": False, "items": items}
    return {**base, **extra}


def test_clear_order_is_priced_and_totalled(catalog, customers):
    order = parsed([
        {"said": "ek kilo Aashirvaad atta", "item": "Aashirvaad atta", "qty": 1},
        {"said": "do Parle-G", "item": "Parle-G", "qty": 2},
    ])
    slip = build_slip(order, catalog, customers["c_imran"])
    assert [line.status for line in slip.lines] == ["ok", "ok"]
    assert slip.lines[1].amount == 20
    assert slip.total == 285 + 20
    assert slip.needs_owner is False


def test_vague_item_resolved_from_customer_history(catalog, customers):
    order = parsed([{"said": "woh wala tel jo pichli baar liya", "item": "oil", "qty": 1, "from_last_time": True}])
    slip = build_slip(order, catalog, customers["c_sharma"])
    line = slip.lines[0]
    assert line.status == "ok"
    assert line.item_id == "oil_fortune_1l"
    assert "last" in line.note.lower()


def test_vague_item_without_history_asks_customer_in_their_language(catalog, customers):
    order = parsed([{"said": "ek tel", "item": "oil", "qty": 1}])
    slip = build_slip(order, catalog, customers["c_imran"])
    line = slip.lines[0]
    assert line.status == "ask"
    assert len(line.options) == 2
    assert "या" in line.question
    assert slip.needs_owner is True
    assert slip.total == 0


def test_out_of_stock_suggests_substitute_but_does_not_charge(catalog, customers):
    order = parsed([{"said": "Amul butter", "item": "Amul butter", "qty": 1}])
    slip = build_slip(order, catalog, customers["c_imran"])
    line = slip.lines[0]
    assert line.status == "out_of_stock"
    assert line.substitute_id == "butter_md_100g"
    assert slip.total == 0
    assert slip.needs_owner is True


def test_unknown_item_is_flagged_not_guessed(catalog, customers):
    order = parsed([{"said": "ek helicopter", "item": "helicopter", "qty": 1}])
    slip = build_slip(order, catalog, customers["c_imran"])
    assert slip.lines[0].status == "unknown"
    assert slip.lines[0].item_id is None
    assert slip.lines[0].question


def test_repeat_usual_rebuilds_customer_usual_list(catalog, customers):
    order = parsed([], repeat_usual=True)
    slip = build_slip(order, catalog, customers["c_sharma"])
    ids = [line.item_id for line in slip.lines]
    assert ids == ["atta_aashirvaad_5kg", "sugar_1kg", "oil_fortune_1l", "dal_toor_1kg", "tea_tata_250g"]
    assert slip.total == 285 + 2 * 46 + 165 + 160 + 140


def test_credit_and_delivery_are_carried(catalog, customers):
    order = parsed([{"said": "do doodh", "item": "milk", "qty": 2}], credit=True, delivery="shaam 6 baje")
    slip = build_slip(order, catalog, customers["c_ramesh"])
    assert slip.credit is True
    assert slip.delivery == "shaam 6 baje"


def test_bad_quantity_needs_answer(catalog, customers):
    order = parsed([{"said": "500 packet maggi", "item": "Maggi", "qty": 500}])
    slip = build_slip(order, catalog, customers["c_imran"])
    assert slip.lines[0].status == "ask"


def test_tamil_question_for_tamil_customer(catalog, customers):
    order = parsed([{"said": "ennai", "item": "oil", "qty": 1}], language="ta")
    slip = build_slip(order, catalog, customers["c_priya"])
    assert slip.lines[0].status == "ask"
    assert "அல்லது" in slip.lines[0].question


def test_ai_guessed_variety_is_not_trusted(catalog, customers):
    # Customer said only "arisi" (rice); the AI guessed Basmati but marked it not specific.
    order = parsed([{"said": "அஞ்சு கிலோ அரிசி", "item": "Basmati Rice", "specific": False, "qty": 5}], language="ta")
    slip = build_slip(order, catalog, customers["c_lakshmi"])
    assert slip.lines[0].item_id == "rice_sona_1kg"  # her usual, not the AI's guess


def test_ai_guessed_variety_without_history_asks(catalog, customers):
    order = parsed([{"said": "ek kilo chawal", "item": "Basmati Rice", "specific": False, "qty": 1}])
    slip = build_slip(order, catalog, customers["c_imran"])
    assert slip.lines[0].status == "ask"


def test_customer_named_variety_is_kept(catalog, customers):
    order = parsed([{"said": "ek kilo basmati", "item": "Basmati Rice", "specific": True, "qty": 1}])
    slip = build_slip(order, catalog, customers["c_lakshmi"])
    assert slip.lines[0].item_id == "rice_basmati_1kg"


def test_pay_by_is_carried(catalog, customers):
    order = parsed([{"said": "do doodh", "item": "milk", "qty": 2}], credit=True, pay_by="1 tarikh")
    slip = build_slip(order, catalog, customers["c_ramesh"])
    assert slip.pay_by == "1 tarikh"
    assert slip.delivery is None


def test_owner_choice_resolves_question_and_updates_total(catalog, customers):
    from dataclasses import asdict
    from bko.slip import choose_item

    order = parsed([{"said": "do Parle-G", "item": "Parle-G", "qty": 2}, {"said": "ek tel", "item": "oil", "qty": 1}])
    slip = asdict(build_slip(order, catalog, customers["c_imran"]))
    updated = choose_item(slip, 1, "oil_saffola_1l", catalog)
    assert updated["lines"][1]["status"] == "ok"
    assert updated["total"] == 20 + 195
    assert updated["needs_owner"] is False
    assert slip["lines"][1]["status"] == "ask"  # original untouched


def test_owner_can_remove_a_line(catalog, customers):
    from dataclasses import asdict
    from bko.slip import remove_line

    order = parsed([{"said": "do Parle-G", "item": "Parle-G", "qty": 2}, {"said": "Amul butter", "item": "Amul butter", "qty": 1}])
    slip = asdict(build_slip(order, catalog, customers["c_imran"]))
    updated = remove_line(slip, 1)
    assert len(updated["lines"]) == 1
    assert updated["needs_owner"] is False
    assert updated["total"] == 20


def test_choose_rejects_unknown_or_out_of_stock_item(catalog, customers):
    from dataclasses import asdict
    from bko.slip import choose_item

    slip = asdict(build_slip(parsed([{"said": "ek tel", "item": "oil", "qty": 1}]), catalog, customers["c_imran"]))
    with pytest.raises(ValueError):
        choose_item(slip, 0, "nope", catalog)
    with pytest.raises(ValueError):
        choose_item(slip, 0, "butter_amul_100g", catalog)
    with pytest.raises(IndexError):
        choose_item(slip, 5, "oil_saffola_1l", catalog)


def test_nothing_understood_is_flagged_not_empty(catalog, customers):
    slip = build_slip(parsed([]), catalog, customers["c_imran"], transcript="lime juice 25 rupees")
    assert len(slip.lines) == 1
    assert slip.lines[0].status == "unknown"
    assert slip.needs_owner is True


def test_kannada_question(catalog, customers):
    slip = build_slip(parsed([{"said": "ennu", "item": "oil", "qty": 1}], language="kn"), catalog, customers["c_imran"])
    assert "ಅಥವಾ" in slip.lines[0].question


def test_slip_does_not_mutate_input(catalog, customers):
    order = parsed([{"said": "do Parle-G", "item": "Parle-G", "qty": 2}])
    snapshot = json.dumps(order, sort_keys=True)
    build_slip(order, catalog, customers["c_imran"])
    assert json.dumps(order, sort_keys=True) == snapshot
