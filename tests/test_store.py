import json
from pathlib import Path

import pytest

from bko.store import Store

DATA = Path(__file__).resolve().parent / "fixtures"


def slip(total=100.0, credit=False, needs_owner=False, lines=None):
    return {
        "customer_id": "c_ramesh", "language": "hi", "total": total, "delivery": "shaam", "credit": credit,
        "needs_owner": needs_owner, "transcript": "do doodh", "pay_by": None,
        "lines": lines if lines is not None else [
            {"said": "do doodh", "qty": 2, "status": "ok", "item_id": "milk_amul_500ml", "name": "Amul Taaza Milk",
             "pack": "500 ml", "unit_price": 28, "amount": 56, "note": "", "question": None, "options": [],
             "substitute_id": None}],
    }


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "shop.db")
    s.seed_customers(json.loads((DATA / "customers.json").read_text(encoding="utf-8"))["customers"])
    return s


def test_seeded_customers_have_history_and_credit(store):
    ramesh = store.get_customer("c_ramesh")
    assert ramesh["name"] == "Ramesh Bhaiya"
    assert ramesh["credit_balance"] == 450
    assert ramesh["usual"][0]["item_id"] == "milk_amul_500ml"


def test_seeding_twice_does_not_duplicate(store):
    store.seed_customers(json.loads((DATA / "customers.json").read_text(encoding="utf-8"))["customers"])
    assert len(store.list_customers()) == 5
    assert store.get_customer("c_ramesh")["credit_balance"] == 450


def test_new_order_status_depends_on_needs_owner(store):
    clear = store.add_order("c_ramesh", slip(), source="a.ogg")
    unclear = store.add_order("c_ramesh", slip(needs_owner=True), source="b.ogg")
    assert clear["status"] == "new"
    assert unclear["status"] == "needs_answer"
    assert [o["id"] for o in store.list_orders()] == [unclear["id"], clear["id"]]  # newest first


def test_accepting_credit_order_adds_to_balance_once(store):
    order = store.add_order("c_ramesh", slip(total=101, credit=True), source="a.ogg")
    store.accept(order["id"])
    store.accept(order["id"])  # double tap must not double charge
    assert store.get_customer("c_ramesh")["credit_balance"] == 450 + 101


def test_accepted_items_become_history(store):
    order = store.add_order("c_imran", slip(), source="a.ogg")
    assert store.get_customer("c_imran")["history"] == []
    store.accept(order["id"])
    assert store.get_customer("c_imran")["history"][0]["item_id"] == "milk_amul_500ml"


def test_cannot_accept_order_with_open_questions(store):
    lines = [{"said": "ek tel", "qty": 1, "status": "ask", "item_id": None, "name": "", "pack": "", "unit_price": 0,
              "amount": 0, "note": "", "question": "Kaunsa tel?", "options": ["a", "b"], "substitute_id": None}]
    order = store.add_order("c_imran", slip(total=0, needs_owner=True, lines=lines), source="a.ogg")
    with pytest.raises(ValueError):
        store.accept(order["id"])


def test_cannot_accept_empty_order(store):
    order = store.add_order("c_imran", slip(total=0, lines=[]), source="a.ogg")
    with pytest.raises(ValueError):
        store.accept(order["id"])


def test_status_flow_and_unknown_status_rejected(store):
    order = store.add_order("c_ramesh", slip(), source="a.ogg")
    store.accept(order["id"])
    assert store.set_status(order["id"], "packed")["status"] == "packed"
    with pytest.raises(ValueError):
        store.set_status(order["id"], "teleported")


def test_update_slip_replaces_and_recomputes_status(store):
    order = store.add_order("c_ramesh", slip(needs_owner=True), source="a.ogg")
    updated = store.update_slip(order["id"], slip(total=56))
    assert updated["status"] == "new"
    assert updated["slip"]["total"] == 56


def test_day_summary_counts_money(store):
    a = store.add_order("c_ramesh", slip(total=101, credit=True), source="a.ogg")
    b = store.add_order("c_sharma", {**slip(total=493), "customer_id": "c_sharma"}, source="b.ogg")
    store.add_order("c_imran", slip(needs_owner=True), source="c.ogg")
    store.accept(a["id"])
    store.accept(b["id"])
    summary = store.day_summary()
    assert summary["orders"] == 3
    assert summary["accepted"] == 2
    assert summary["sales"] == 594
    assert summary["credit_given"] == 101
    assert summary["pending"] == 1


def test_packing_ticks_are_saved(store):
    order = store.add_order("c_ramesh", slip(), source="a.ogg")
    store.accept(order["id"])
    assert store.set_packed(order["id"], 0, True)["packed"] == [0]
    assert store.get_order(order["id"])["packed"] == [0]
    assert store.set_packed(order["id"], 0, False)["packed"] == []
    with pytest.raises(IndexError):
        store.set_packed(order["id"], 9, True)


def test_get_missing_order_raises(store):
    with pytest.raises(KeyError):
        store.get_order(999)
