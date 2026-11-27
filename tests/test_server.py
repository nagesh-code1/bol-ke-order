import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server import create_app

DATA = Path(__file__).resolve().parent / "fixtures"


HINTS = []


def fake_transcribe(audio, filename, language_hint=None):
    HINTS.append(language_hint)
    return {"ok": True, "error": None, "text": audio.decode("utf-8"), "language": "hi", "engine": "fake", "secs": 0}


def fake_parse(transcript, names):
    items = {
        "parle": [{"said": "do Parle-G", "item": "Parle-G", "qty": 2}],
        "tel": [{"said": "ek tel", "item": "oil", "qty": 1}],
        "udhaar": [{"said": "do doodh", "item": "milk", "qty": 2}],
        "lime": [{"said": "do lime juice", "item": "lime juice", "qty": 2}],
    }[transcript]
    return {"ok": True, "error": None,
            "parsed": {"language": "hi", "items": items, "credit": transcript == "udhaar", "delivery": None}}


def fake_speak(text, language, folder):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "reply_test.mp3"
    path.write_bytes(b"ID3fake")
    return path


@pytest.fixture
def client(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    for name in ("rate_list.json", "customers.json"):
        shutil.copy(DATA / name, data / name)
    app = create_app(data, transcribe=fake_transcribe, parse=fake_parse, speak=fake_speak)
    return TestClient(app)


def voice_order(client, text, customer="c_imran"):
    return client.post(f"/api/voice-order?customer_id={customer}", content=text.encode("utf-8"),
                       headers={"x-filename": "note.webm"})


def test_pages_are_served(client):
    assert "Bol Ke Order" in client.get("/").text
    assert "Bol Ke Order" in client.get("/order").text
    assert "Bol Ke Order" in client.get("/welcome").text


def test_voice_order_creates_priced_order(client):
    res = voice_order(client, "parle").json()
    assert res["ok"] is True
    order = res["data"]
    assert order["status"] == "new"
    assert order["slip"]["total"] == 20
    assert client.get("/api/orders").json()["data"][0]["id"] == order["id"]


def test_unknown_customer_rejected(client):
    res = voice_order(client, "parle", customer="ghost")
    assert res.status_code == 404
    assert res.json()["ok"] is False


def test_empty_audio_rejected(client):
    res = client.post("/api/voice-order?customer_id=c_imran", content=b"")
    assert res.status_code == 400


def test_question_flow_choose_then_accept_with_voice_reply(client):
    order = voice_order(client, "tel").json()["data"]
    assert order["status"] == "needs_answer"

    asked = client.post(f"/api/orders/{order['id']}/ask").json()["data"]
    assert "या" in asked["reply_text"]
    assert asked["reply_audio"]

    blocked = client.post(f"/api/orders/{order['id']}/accept")
    assert blocked.status_code == 409

    chosen = client.post(f"/api/orders/{order['id']}/choose", json={"index": 0, "item_id": "oil_saffola_1l"}).json()
    assert chosen["data"]["status"] == "new"
    accepted = client.post(f"/api/orders/{order['id']}/accept").json()["data"]
    assert accepted["status"] == "accepted"
    assert "195" in accepted["reply_text"]
    assert client.get(f"/api/replies/{accepted['reply_audio']}").content == b"ID3fake"


def test_credit_order_updates_khata_and_summary(client):
    order = voice_order(client, "udhaar", customer="c_ramesh").json()["data"]
    client.post(f"/api/orders/{order['id']}/accept")
    ramesh = next(c for c in client.get("/api/customers").json()["data"] if c["id"] == "c_ramesh")
    assert ramesh["credit_balance"] == 450 + 56
    summary = client.get("/api/summary").json()["data"]
    assert summary["sales"] == 56 and summary["credit_given"] == 56


def test_status_flow_and_bad_status(client):
    order = voice_order(client, "parle").json()["data"]
    client.post(f"/api/orders/{order['id']}/accept")
    assert client.post(f"/api/orders/{order['id']}/status", json={"status": "packed"}).json()["data"]["status"] == "packed"
    assert client.post(f"/api/orders/{order['id']}/status", json={"status": "flying"}).status_code == 400


def test_text_order_for_manual_entry(client):
    res = client.post("/api/text-order", json={"customer_id": "c_imran", "text": "parle"}).json()
    assert res["data"]["slip"]["total"] == 20


def test_reply_file_path_traversal_blocked(client):
    assert client.get("/api/replies/..%2Fshop.db").status_code in (400, 404)


def test_owner_adds_unlisted_item_and_order_becomes_ready(client):
    order = voice_order(client, "lime").json()["data"]
    assert order["status"] == "needs_answer"
    res = client.post(f"/api/orders/{order['id']}/add-item",
                      json={"index": 0, "name": "Lime Juice", "pack": "1 glass", "price": 25}).json()
    assert res["data"]["status"] == "new"
    assert res["data"]["slip"]["total"] == 50
    assert any(i["name"] == "Lime Juice" for i in client.get("/api/catalog").json()["data"])


def test_language_hint_passed_and_bad_value_ignored(client):
    HINTS.clear()
    client.post("/api/voice-order?customer_id=c_imran&lang=kn", content=b"parle")
    client.post("/api/voice-order?customer_id=c_imran&lang=evil", content=b"parle")
    assert HINTS == ["kn", None]


def test_owner_edits_price_and_stock_used_by_new_orders(client):
    assert client.patch("/api/catalog/biscuit_parleg", json={"price": 12}).json()["data"]["price"] == 12
    assert voice_order(client, "parle").json()["data"]["slip"]["total"] == 24
    assert client.patch("/api/catalog/biscuit_parleg", json={"in_stock": False}).json()["data"]["in_stock"] is False
    assert voice_order(client, "parle").json()["data"]["slip"]["lines"][0]["status"] == "out_of_stock"
    assert client.patch("/api/catalog/ghost", json={"price": 5}).status_code == 404
    bad = client.patch("/api/catalog/biscuit_parleg", json={"price": -1})
    assert bad.status_code == 422
    assert bad.json()["ok"] is False and "price" in bad.json()["error"]


def test_owner_adds_item_from_stock_page(client):
    res = client.post("/api/catalog", json={"name": "Goli Soda", "pack": "1 bottle", "price": 15}).json()
    assert res["data"]["id"].startswith("custom_")
    assert client.post("/api/catalog", json={"name": "Goli Soda", "price": 15}).status_code == 400


def test_summary_is_spoken(client):
    voice_order(client, "parle")
    res = client.post("/api/summary/speak", json={"language": "hi"}).json()["data"]
    assert "ऑर्डर" in res["text"] and res["audio"]


def test_packing_tick_saved(client):
    order = voice_order(client, "parle").json()["data"]
    client.post(f"/api/orders/{order['id']}/accept")
    assert client.post(f"/api/orders/{order['id']}/pack", json={"index": 0, "done": True}).json()["data"]["packed"] == [0]
    assert client.post(f"/api/orders/{order['id']}/pack", json={"index": 7, "done": True}).status_code == 400


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def test_owner_uploads_item_photo_and_it_is_served(client):
    res = client.post("/api/catalog/sugar_1kg/photo", content=PNG).json()
    assert res["data"]["photo"] == "sugar_1kg.png"
    assert client.get("/api/photos/sugar_1kg.png").content == PNG


def test_photo_upload_rejects_non_images_and_unknown_items(client):
    assert client.post("/api/catalog/sugar_1kg/photo", content=b"<script>alert(1)</script>").status_code == 415
    assert client.post("/api/catalog/ghost/photo", content=PNG).status_code == 404
    assert client.get("/api/photos/..%2Fshop.db").status_code == 404


def test_customer_taps_items_from_photos(client):
    res = client.post("/api/tap-order", json={"customer_id": "c_imran",
                                              "items": [{"item_id": "biscuit_parleg", "qty": 2}]}).json()
    assert res["data"]["status"] == "new" and res["data"]["slip"]["total"] == 20
    assert client.post("/api/tap-order", json={"customer_id": "c_imran", "items": []}).status_code == 422


def test_customer_answers_question_only_on_own_order(client):
    order = voice_order(client, "tel").json()["data"]
    option = order["slip"]["lines"][0]["options"][0]
    stranger = client.post(f"/api/orders/{order['id']}/customer-choose",
                           json={"customer_id": "c_ramesh", "index": 0, "item_id": option})
    assert stranger.status_code == 403
    mine = client.post(f"/api/orders/{order['id']}/customer-choose",
                       json={"customer_id": "c_imran", "index": 0, "item_id": option}).json()
    assert mine["data"]["status"] == "new"


def test_missing_order_is_404(client):
    assert client.post("/api/orders/999/accept").status_code == 404
