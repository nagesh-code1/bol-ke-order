from pathlib import Path

import pytest

from bko.catalog import Catalog

DATA = Path(__file__).resolve().parent / "fixtures" / "rate_list.json"


@pytest.fixture(scope="module")
def catalog():
    return Catalog.load(DATA)


def test_loads_all_items_and_categories(catalog):
    assert len(catalog.items) == 40
    assert "oil" in catalog.categories


def test_specific_brand_beats_generic_category(catalog):
    match = catalog.match("fortune tel")
    assert match.kind == "item"
    assert match.item.id == "oil_fortune_1l"


def test_generic_word_matches_category_only(catalog):
    match = catalog.match("woh wala tel")
    assert match.kind == "category"
    assert match.category == "oil"


def test_hindi_alias_matches_item(catalog):
    assert catalog.match("aadha kilo cheeni").item.id == "sugar_1kg"


def test_tamil_alias_matches_item(catalog):
    assert catalog.match("thakkali").item.id == "tomato_1kg"


def test_hyphen_and_case_are_normalised(catalog):
    assert catalog.match("PARLE-G").item.id == "biscuit_parleg"


def test_small_typo_still_matches(catalog):
    assert catalog.match("ashirvad").item.id == "atta_aashirvaad_5kg"


def test_unknown_phrase_returns_none(catalog):
    assert catalog.match("helicopter").kind == "none"


def test_alias_must_be_whole_word(catalog):
    # "tel" must not match inside "hotel"
    assert catalog.match("hotel").kind == "none"


def test_owner_can_add_new_item(tmp_path):
    import shutil
    path = tmp_path / "rate_list.json"
    shutil.copy(DATA, path)
    item_id = Catalog.add_item_to_file(path, "Lime Juice", "1 glass", 25)
    fresh = Catalog.load(path)
    assert fresh.get(item_id).price == 25
    assert fresh.match("lime juice").item.id == item_id
    with pytest.raises(ValueError):
        Catalog.add_item_to_file(path, "Lime Juice", "1 glass", 25)  # duplicate
    with pytest.raises(ValueError):
        Catalog.add_item_to_file(path, "Goli Soda", "1 bottle", 0)  # bad price


def test_owner_can_change_price_and_stock(tmp_path):
    import shutil
    path = tmp_path / "rate_list.json"
    shutil.copy(DATA, path)
    Catalog.update_item_in_file(path, "sugar_1kg", price=48, in_stock=False)
    fresh = Catalog.load(path)
    assert fresh.get("sugar_1kg").price == 48
    assert fresh.get("sugar_1kg").in_stock is False
    Catalog.update_item_in_file(path, "sugar_1kg", in_stock=True)
    assert Catalog.load(path).get("sugar_1kg").price == 48  # untouched field kept
    with pytest.raises(KeyError):
        Catalog.update_item_in_file(path, "nope")
    with pytest.raises(ValueError):
        Catalog.update_item_in_file(path, "sugar_1kg", price=-5)


def test_in_category_lists_only_that_category(catalog):
    ids = {item.id for item in catalog.in_category("butter")}
    assert ids == {"butter_amul_100g", "butter_md_100g"}
