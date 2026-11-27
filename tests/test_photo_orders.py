import shutil
from dataclasses import asdict
from pathlib import Path

import pytest

from bko.catalog import Catalog
from bko.slip import build_slip, build_slip_from_items, customer_choose

DATA = Path(__file__).resolve().parent / "fixtures"
CATALOG = Catalog.load(DATA / "rate_list.json")
IMRAN = {"id": "c_imran", "language": "hi", "usual": [], "history": []}


def test_tap_order_prices_exact_items_without_ai():
    slip = build_slip_from_items([{"item_id": "biscuit_parleg", "qty": 3}, {"item_id": "sugar_1kg", "qty": 1}],
                                 CATALOG, IMRAN)
    assert [line.item_id for line in slip.lines] == ["biscuit_parleg", "sugar_1kg"]
    assert slip.total == 30 + 46
    assert slip.needs_owner is False
    assert "Parle-G" in slip.transcript


def test_tap_order_out_of_stock_still_needs_owner():
    slip = build_slip_from_items([{"item_id": "butter_amul_100g", "qty": 1}], CATALOG, IMRAN)
    assert slip.lines[0].status == "out_of_stock"
    assert slip.needs_owner is True


@pytest.mark.parametrize("picks", [[], [{"item_id": "ghost", "qty": 1}], [{"item_id": "sugar_1kg", "qty": 0}],
                                   [{"item_id": "sugar_1kg", "qty": 99}]])
def test_tap_order_rejects_bad_picks(picks):
    with pytest.raises(ValueError):
        build_slip_from_items(picks, CATALOG, IMRAN)


def _ask_slip():
    parsed = {"language": "hi", "items": [{"said": "ek tel", "item": "oil", "qty": 1},
                                          {"said": "Amul butter", "item": "Amul butter", "qty": 1}]}
    return asdict(build_slip(parsed, CATALOG, IMRAN))


def test_customer_picks_offered_option():
    slip = _ask_slip()
    option = slip["lines"][0]["options"][0]
    updated = customer_choose(slip, 0, option, CATALOG)
    assert updated["lines"][0]["status"] == "ok"
    assert updated["lines"][0]["item_id"] == option


def test_customer_can_accept_offered_substitute():
    slip = _ask_slip()
    updated = customer_choose(slip, 1, "butter_md_100g", CATALOG)
    assert updated["lines"][1]["item_id"] == "butter_md_100g"


def test_customer_cannot_pick_something_not_offered():
    slip = _ask_slip()
    with pytest.raises(ValueError):
        customer_choose(slip, 0, "ghee_amul_500ml", CATALOG)


def test_photo_saved_in_rate_list(tmp_path):
    path = tmp_path / "rate_list.json"
    shutil.copy(DATA / "rate_list.json", path)
    Catalog.update_item_in_file(path, "sugar_1kg", photo="sugar_1kg.jpg")
    assert Catalog.load(path).get("sugar_1kg").photo == "sugar_1kg.jpg"
