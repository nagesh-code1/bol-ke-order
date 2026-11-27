"""Bol Ke Order web server: customer voice-order page + shop dashboard + JSON API.

Run:  python server.py   ->  http://127.0.0.1:8790/  (shop)  and  /order  (customer)
Every API answer is {"ok": bool, "data": ..., "error": str|None}.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from bko import brain, ears, mouth
from bko.catalog import Catalog
from bko.pipeline import process_note, process_text
from bko.slip import build_slip_from_items, choose_item, customer_choose, remove_line
from bko.store import Store

log = logging.getLogger("bol_ke_order")
ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
PORT = 8790
REPLY_NAME = re.compile(r"^reply_[0-9a-f]{16}\.mp3$|^reply_test\.mp3$")


class TextOrder(BaseModel):
    customer_id: str = Field(min_length=1, max_length=40)
    text: str = Field(min_length=1, max_length=2000)


class Choice(BaseModel):
    index: int = Field(ge=0, le=100)
    item_id: str = Field(min_length=1, max_length=60)
    qty: float | None = Field(default=None, gt=0, le=50)


class LineIndex(BaseModel):
    index: int = Field(ge=0, le=100)


class StatusChange(BaseModel):
    status: str = Field(min_length=1, max_length=20)


class NewItem(BaseModel):
    index: int = Field(ge=0, le=100)
    name: str = Field(min_length=1, max_length=60)
    pack: str = Field(default="1 pc", max_length=30)
    price: float = Field(gt=0, le=100_000)
    qty: float | None = Field(default=None, gt=0, le=50)


class ItemChange(BaseModel):
    price: float | None = Field(default=None, gt=0, le=100_000)
    in_stock: bool | None = None


class CatalogItem(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    pack: str = Field(default="1 pc", max_length=30)
    price: float = Field(gt=0, le=100_000)


class PackTick(BaseModel):
    index: int = Field(ge=0, le=100)
    done: bool


class SummaryVoice(BaseModel):
    language: str = Field(default="hi", max_length=5)


class TapPick(BaseModel):
    item_id: str = Field(min_length=1, max_length=60)
    qty: float = Field(gt=0, le=50)


class TapOrder(BaseModel):
    customer_id: str = Field(min_length=1, max_length=40)
    items: list[TapPick] = Field(min_length=1, max_length=60)


class CustomerChoice(BaseModel):
    customer_id: str = Field(min_length=1, max_length=40)
    index: int = Field(ge=0, le=100)
    item_id: str = Field(min_length=1, max_length=60)


MAX_PHOTO_BYTES = 3 * 1024 * 1024
PHOTO_TYPES = ((b"\xff\xd8\xff", "jpg"), (b"\x89PNG\r\n\x1a\n", "png"), (b"RIFF", "webp"))
PHOTO_NAME = re.compile(r"^[a-z0-9_]{1,70}\.(jpg|png|webp)$")
ITEM_ID = re.compile(r"^[a-z0-9_]{1,60}$")


def photo_kind(data: bytes) -> str | None:
    """Trust the file's own bytes, not its name: only real JPEG, PNG or WebP images are kept."""
    for magic, ext in PHOTO_TYPES:
        if data.startswith(magic) and (ext != "webp" or data[8:12] == b"WEBP"):
            return ext
    return None


SPEECH_LANGUAGES = {"hi", "en", "ta", "kn", "te", "mr", "bn", "gu", "ml", "pa", "ur"}


def ok(data) -> dict:
    return {"ok": True, "data": data, "error": None}


def create_app(data_dir: Path = ROOT / "data", transcribe: Callable = ears.transcribe,
               parse: Callable = brain.parse_order, speak: Callable = mouth.speak) -> FastAPI:
    rate_list = data_dir / "rate_list.json"
    shop = {"catalog": Catalog.load(rate_list)}  # replaced (never mutated) when the owner adds an item
    store = Store(data_dir / "shop.db")
    store.seed_customers(json.loads((data_dir / "customers.json").read_text(encoding="utf-8"))["customers"])
    replies_dir = data_dir / "replies"
    photos_dir = data_dir / "photos"
    app = FastAPI(title="Bol Ke Order")
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"ok": False, "data": None, "error": exc.detail})

    @app.exception_handler(RequestValidationError)
    async def bad_input(_: Request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        field = ".".join(str(p) for p in first.get("loc", [])[1:]) or "input"
        return JSONResponse(status_code=422, content={"ok": False, "data": None,
                                                      "error": f"Check {field}: {first.get('msg', 'invalid value')}"})

    def customer_or_404(customer_id: str) -> dict:
        customer = store.get_customer(customer_id)
        if customer is None:
            raise HTTPException(404, "Unknown customer")
        return customer

    def order_or_404(order_id: int) -> dict:
        try:
            return store.get_order(order_id)
        except KeyError:
            raise HTTPException(404, "Order not found") from None

    def for_reply(slip: dict) -> dict:
        lines = [{**line, "substitute_name": (shop["catalog"].get(line["substitute_id"]).name
                                              if line.get("substitute_id") else None)}
                 for line in slip["lines"]]
        return {**slip, "lines": lines}

    async def send_reply(order: dict) -> dict:
        """Owner-approved message: text + spoken audio in the customer's language."""
        text = mouth.reply_text(for_reply(order["slip"]))
        audio_name = None
        try:
            path = await run_in_threadpool(speak, text, order["slip"].get("language", "hi"), replies_dir)
            audio_name = path.name
        except Exception as exc:  # text reply still works without audio
            log.warning("Voice reply failed for order %s: %s", order["id"], exc)
        return store.set_reply(order["id"], text, audio_name)

    def save(result: dict, customer_id: str, source: str) -> dict:
        if not result["ok"]:
            raise HTTPException(422, f"Could not read the order ({result['stage']}): {result['error']}")
        return store.add_order(customer_id, result["slip"], source=source)

    # ---- pages -----------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    def shop_page():
        return (STATIC / "shop.html").read_text(encoding="utf-8")

    @app.get("/sw.js")
    def service_worker():  # served from the root so it can make "/" and "/order" installable
        return FileResponse(STATIC / "sw.js", media_type="text/javascript",
                            headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})

    @app.get("/welcome", response_class=HTMLResponse)
    def landing_page():
        return (STATIC / "landing.html").read_text(encoding="utf-8")

    @app.get("/order", response_class=HTMLResponse)
    def customer_page():
        return (STATIC / "customer.html").read_text(encoding="utf-8")

    # ---- read API --------------------------------------------------------
    @app.get("/api/catalog")
    def get_catalog():
        return ok([asdict(item) for item in shop["catalog"].items])

    @app.get("/api/customers")
    def get_customers():
        return ok(store.list_customers())

    @app.get("/api/orders")
    def get_orders(status: str | None = None, customer_id: str | None = None):
        orders = store.list_orders(status=status)
        if customer_id:
            orders = [o for o in orders if o["customer_id"] == customer_id]
        return ok(orders)

    @app.get("/api/orders/{order_id}")
    def get_order(order_id: int):
        return ok(order_or_404(order_id))

    @app.get("/api/summary")
    def get_summary():
        return ok(store.day_summary())

    @app.post("/api/summary/speak")
    async def speak_summary(body: SummaryVoice):
        language = body.language if body.language in mouth.SUMMARY else "hi"
        out = [item.name for item in shop["catalog"].items if not item.in_stock]
        text = mouth.summary_text(store.day_summary(), out, language)
        audio_name = None
        try:
            audio_name = (await run_in_threadpool(speak, text, language, replies_dir)).name
        except Exception as exc:
            log.warning("Summary voice failed: %s", exc)
        return ok({"text": text, "audio": audio_name})

    # ---- stock & prices (owner) ---------------------------------------------
    @app.patch("/api/catalog/{item_id}")
    def change_item(item_id: str, body: ItemChange):
        try:
            Catalog.update_item_in_file(rate_list, item_id, price=body.price, in_stock=body.in_stock)
        except KeyError:
            raise HTTPException(404, "No such item") from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        shop["catalog"] = Catalog.load(rate_list)
        return ok(asdict(shop["catalog"].get(item_id)))

    @app.post("/api/catalog")
    def new_item(body: CatalogItem):
        try:
            item_id = Catalog.add_item_to_file(rate_list, body.name, body.pack, body.price)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        shop["catalog"] = Catalog.load(rate_list)
        return ok(asdict(shop["catalog"].get(item_id)))

    @app.post("/api/catalog/{item_id}/photo")
    async def upload_photo(item_id: str, request: Request):
        if not ITEM_ID.match(item_id) or shop["catalog"].get(item_id) is None:
            raise HTTPException(404, "No such item")
        data = await request.body()
        if not data:
            raise HTTPException(400, "No photo received")
        if len(data) > MAX_PHOTO_BYTES:
            raise HTTPException(413, "Photo is too big (max 3 MB)")
        ext = photo_kind(data)
        if ext is None:
            raise HTTPException(415, "Only JPG, PNG or WebP photos")
        photos_dir.mkdir(parents=True, exist_ok=True)
        for old in photos_dir.glob(f"{item_id}.*"):
            old.unlink()
        name = f"{item_id}.{ext}"
        (photos_dir / name).write_bytes(data)
        Catalog.update_item_in_file(rate_list, item_id, photo=name)
        shop["catalog"] = Catalog.load(rate_list)
        return ok(asdict(shop["catalog"].get(item_id)))

    @app.get("/api/photos/{name}")
    def get_photo(name: str):
        path = photos_dir / name
        if not PHOTO_NAME.match(name) or not path.is_file():
            raise HTTPException(404, "No such photo")
        media = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}[name.rsplit(".", 1)[1]]
        return FileResponse(path, media_type=media)

    @app.post("/api/tap-order")
    def tap_order(body: TapOrder):
        customer = customer_or_404(body.customer_id)
        try:
            slip = build_slip_from_items([p.model_dump() for p in body.items], shop["catalog"], customer)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return ok(store.add_order(body.customer_id, asdict(slip), source="tapped"))

    @app.post("/api/orders/{order_id}/customer-choose")
    def customer_pick(order_id: int, body: CustomerChoice):
        order = order_or_404(order_id)
        if order["customer_id"] != body.customer_id:
            raise HTTPException(403, "This isn't your order")
        if order["status"] not in ("new", "needs_answer"):
            raise HTTPException(409, "The shop has already handled this order")
        try:
            return ok(store.update_slip(order_id, customer_choose(order["slip"], body.index, body.item_id,
                                                                  shop["catalog"])))
        except (IndexError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from None

    @app.get("/api/replies/{name}")
    def get_reply_audio(name: str):
        path = replies_dir / name
        if not REPLY_NAME.match(name) or not path.is_file():
            raise HTTPException(404, "No such reply")
        return FileResponse(path, media_type="audio/mpeg")

    # ---- creating orders ---------------------------------------------------
    @app.post("/api/voice-order")
    async def voice_order(request: Request, customer_id: str, lang: str | None = None):
        customer = customer_or_404(customer_id)
        if lang is not None and lang not in SPEECH_LANGUAGES:
            lang = None  # "auto" or anything unexpected: let the speech engine detect it
        audio = await request.body()
        if not audio:
            raise HTTPException(400, "No audio received")
        filename = re.sub(r"[^\w.\-]", "_", request.headers.get("x-filename", "note.webm"))[:60]
        result = await run_in_threadpool(process_note, audio, filename, shop["catalog"], customer,
                                         transcribe, parse, lang)
        return ok(save(result, customer_id, filename))

    @app.post("/api/text-order")
    async def text_order(body: TextOrder):
        customer = customer_or_404(body.customer_id)
        result = await run_in_threadpool(process_text, body.text, shop["catalog"], customer, parse)
        return ok(save(result, body.customer_id, "typed"))

    # ---- shop actions ------------------------------------------------------
    @app.post("/api/orders/{order_id}/choose")
    def choose(order_id: int, body: Choice):
        order = order_or_404(order_id)
        try:
            slip = choose_item(order["slip"], body.index, body.item_id, shop["catalog"], body.qty)
            return ok(store.update_slip(order_id, slip))
        except (IndexError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from None

    @app.post("/api/orders/{order_id}/add-item")
    def add_item(order_id: int, body: NewItem):
        """Owner adds an item the shop didn't list yet (e.g. lime juice) and puts it on this order line."""
        order = order_or_404(order_id)
        if not 0 <= body.index < len(order["slip"]["lines"]):
            raise HTTPException(400, f"No line {body.index}")
        try:
            item_id = Catalog.add_item_to_file(rate_list, body.name, body.pack, body.price)
            shop["catalog"] = Catalog.load(rate_list)
            slip = choose_item(order["slip"], body.index, item_id, shop["catalog"], body.qty)
            return ok(store.update_slip(order_id, slip))
        except (IndexError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from None

    @app.post("/api/orders/{order_id}/remove")
    def remove(order_id: int, body: LineIndex):
        order = order_or_404(order_id)
        try:
            return ok(store.update_slip(order_id, remove_line(order["slip"], body.index)))
        except (IndexError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from None

    @app.post("/api/orders/{order_id}/ask")
    async def ask_customer(order_id: int):
        order = order_or_404(order_id)
        if not order["slip"]["needs_owner"]:
            raise HTTPException(409, "Nothing to ask on this order")
        return ok(await send_reply(order))

    @app.post("/api/orders/{order_id}/accept")
    async def accept(order_id: int):
        order_or_404(order_id)
        try:
            order = store.accept(order_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        return ok(await send_reply(order))

    @app.post("/api/orders/{order_id}/pack")
    def pack(order_id: int, body: PackTick):
        order_or_404(order_id)
        try:
            return ok(store.set_packed(order_id, body.index, body.done))
        except IndexError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.post("/api/orders/{order_id}/status")
    def change_status(order_id: int, body: StatusChange):
        order_or_404(order_id)
        if body.status not in ("packed", "delivered", "rejected"):
            raise HTTPException(400, "Status must be packed, delivered or rejected")
        return ok(store.set_status(order_id, body.status))

    return app


if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(create_app(), host="127.0.0.1", port=PORT)
