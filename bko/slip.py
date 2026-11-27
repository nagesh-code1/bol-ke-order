"""Turn the AI's parsed order into a priced, owner-reviewable order slip.

Rules that keep the shop safe:
- never guess an item: vague or unknown lines become questions;
- never swap an out-of-stock item silently: a substitute is only suggested;
- only confirmed ("ok") lines count towards the total.
"""
from __future__ import annotations

from dataclasses import dataclass

from bko.catalog import Catalog, Item, Match

MAX_QTY = 50

ASK_OPTIONS = {
    "hi": "कौन सा {label} चाहिए — {a} या {b}?",
    "en": "Which {label} would you like — {a} or {b}?",
    "ta": "எந்த {label} வேண்டும் — {a} அல்லது {b}?",
    "kn": "ಯಾವ {label} ಬೇಕು — {a} ಅಥವಾ {b}?",
}
ASK_UNKNOWN = {
    "hi": "'{said}' समझ नहीं आया, थोड़ा और बताएंगे?",
    "en": "Sorry, what did you mean by '{said}'?",
    "ta": "'{said}' புரியவில்லை, கொஞ்சம் விளக்குவீர்களா?",
    "kn": "'{said}' ಅರ್ಥವಾಗಲಿಲ್ಲ, ಸ್ವಲ್ಪ ಇನ್ನೂ ಹೇಳುತ್ತೀರಾ?",
}
ASK_QTY = {
    "hi": "{name} कितने चाहिए? {qty} सही है?",
    "en": "How many {name}? Is {qty} right?",
    "ta": "{name} எத்தனை வேண்டும்? {qty} சரியா?",
    "kn": "{name} ಎಷ್ಟು ಬೇಕು? {qty} ಸರಿಯೇ?",
}


@dataclass(frozen=True)
class SlipLine:
    said: str
    qty: float
    status: str  # "ok" | "ask" | "out_of_stock" | "unknown"
    item_id: str | None = None
    name: str = ""
    pack: str = ""
    unit_price: int = 0
    amount: float = 0
    note: str = ""
    question: str | None = None
    options: tuple[str, ...] = ()
    substitute_id: str | None = None


@dataclass(frozen=True)
class Slip:
    customer_id: str | None
    language: str
    lines: tuple[SlipLine, ...]
    total: float
    delivery: str | None
    credit: bool
    needs_owner: bool
    transcript: str = ""
    pay_by: str | None = None


def _template(table: dict, language: str) -> str:
    return table.get(language) or table["en"]


def _past_item_ids(customer: dict | None) -> list[str]:
    if not customer:
        return []
    rows = list(customer.get("history", [])) + list(customer.get("usual", []))
    return [row["item_id"] for row in rows]


def _ok_line(said: str, qty: float, item: Item, note: str = "") -> SlipLine:
    return SlipLine(
        said=said, qty=qty, status="ok", item_id=item.id, name=item.name, pack=item.pack,
        unit_price=item.price, amount=round(item.price * qty, 2), note=note,
    )


def _resolve_category(said: str, qty: float, category: str, catalog: Catalog,
                      customer: dict | None, language: str) -> SlipLine:
    candidates = [item for item in catalog.in_category(category) if item.in_stock]
    past = _past_item_ids(customer)
    for item_id in past:
        item = catalog.get(item_id)
        if item and item.category == category and item.in_stock:
            return _ok_line(said, qty, item, note="Same as last time")
    if len(candidates) == 1:
        return _ok_line(said, qty, candidates[0], note="Only one in stock")
    if not candidates:
        return _unknown_line(said, qty, language)
    first, second = candidates[0], candidates[1]
    question = _template(ASK_OPTIONS, language).format(
        label=catalog.label(category, language), a=first.name, b=second.name)
    return SlipLine(said=said, qty=qty, status="ask", question=question,
                    options=(first.id, second.id), note="Which one?")


def _out_of_stock_line(said: str, qty: float, item: Item, catalog: Catalog,
                       customer: dict | None) -> SlipLine:
    alternatives = [alt for alt in catalog.in_category(item.category) if alt.in_stock and alt.id != item.id]
    past = set(_past_item_ids(customer))
    alternatives.sort(key=lambda alt: alt.id not in past)
    substitute = alternatives[0] if alternatives else None
    note = f"Out of stock. Suggest {substitute.name}?" if substitute else "Out of stock"
    return SlipLine(
        said=said, qty=qty, status="out_of_stock", item_id=item.id, name=item.name, pack=item.pack,
        unit_price=item.price, note=note, substitute_id=substitute.id if substitute else None,
    )


def _unknown_line(said: str, qty: float, language: str) -> SlipLine:
    question = _template(ASK_UNKNOWN, language).format(said=said)
    return SlipLine(said=said, qty=qty, status="unknown", question=question, note="Not in rate list")


def _best_match(catalog: Catalog, said: str, hint: str, specific: bool) -> Match:
    rank = {"item": 2, "category": 1, "none": 0}
    said_match = catalog.match(said)
    hint_match = catalog.match(hint)
    best = max([hint_match, said_match], key=lambda m: (rank[m.kind], m.score))
    # The AI may have picked a brand/variety the customer never said: keep only its category.
    if best is hint_match and best.kind == "item" and not specific and said_match.kind != "item":
        return Match(kind="category", category=best.category, score=best.score)
    return best


def _resolve_line(raw: dict, catalog: Catalog, customer: dict | None, language: str) -> SlipLine:
    said = str(raw.get("said") or raw.get("item") or "").strip()
    hint = str(raw.get("item") or "")
    try:
        qty = float(raw.get("qty") or 1)
    except (TypeError, ValueError):
        qty = 1.0
    match = _best_match(catalog, said, hint, specific=bool(raw.get("specific", True)))

    if match.kind == "none":
        return _unknown_line(said, qty, language)
    if match.kind == "category":
        line = _resolve_category(said, qty, match.category, catalog, customer, language)
    elif not match.item.in_stock:
        return _out_of_stock_line(said, qty, match.item, catalog, customer)
    else:
        line = _ok_line(said, qty, match.item)

    if line.status == "ok" and not (0 < qty <= MAX_QTY):
        question = _template(ASK_QTY, language).format(name=line.name, qty=f"{qty:g}")
        return SlipLine(said=said, qty=qty, status="ask", item_id=line.item_id, name=line.name,
                        pack=line.pack, unit_price=line.unit_price, question=question,
                        note="Unusual quantity")
    return line


def _usual_lines(catalog: Catalog, customer: dict | None) -> list[SlipLine]:
    lines = []
    for row in (customer or {}).get("usual", []):
        item = catalog.get(row["item_id"])
        if item is None:
            continue
        qty = float(row.get("qty", 1))
        if item.in_stock:
            lines.append(_ok_line(item.name, qty, item, note="Usual order"))
        else:
            lines.append(_out_of_stock_line(item.name, qty, item, catalog, customer))
    return lines


def _with_lines(slip: dict, lines: list[dict]) -> dict:
    total = round(sum(line["amount"] for line in lines if line["status"] == "ok"), 2)
    return {**slip, "lines": lines, "total": total,
            "needs_owner": any(line["status"] != "ok" for line in lines)}


def choose_item(slip: dict, index: int, item_id: str, catalog: Catalog, qty: float | None = None) -> dict:
    """Owner answers a question or approves a substitute: returns a NEW slip dict."""
    lines = list(slip["lines"])
    if not 0 <= index < len(lines):
        raise IndexError(f"No line {index}")
    item = catalog.get(item_id)
    if item is None:
        raise ValueError(f"Unknown item: {item_id}")
    if not item.in_stock:
        raise ValueError(f"{item.name} is out of stock")
    old = lines[index]
    new_qty = float(qty if qty is not None else old["qty"])
    if not 0 < new_qty <= MAX_QTY:
        raise ValueError(f"Quantity must be between 0 and {MAX_QTY}")
    from dataclasses import asdict

    lines[index] = asdict(_ok_line(old["said"], new_qty, item, note="Chosen by shop"))
    return _with_lines(slip, lines)


def remove_line(slip: dict, index: int) -> dict:
    lines = list(slip["lines"])
    if not 0 <= index < len(lines):
        raise IndexError(f"No line {index}")
    return _with_lines(slip, lines[:index] + lines[index + 1:])


def build_slip_from_items(picks: list[dict], catalog: Catalog, customer: dict | None,
                          language: str | None = None) -> Slip:
    """Tap-to-order: the customer chose exact items from photos, so no AI and no guessing is needed."""
    language = language or (customer or {}).get("language") or "hi"
    lines: list[SlipLine] = []
    for pick in picks:
        item = catalog.get(str(pick.get("item_id")))
        if item is None:
            raise ValueError(f"Unknown item: {pick.get('item_id')}")
        qty = float(pick.get("qty") or 0)
        if not 0 < qty <= MAX_QTY:
            raise ValueError(f"Quantity for {item.name} must be between 0 and {MAX_QTY}")
        if item.in_stock:
            lines.append(_ok_line(item.name, qty, item, note="Picked from photos"))
        else:
            lines.append(_out_of_stock_line(item.name, qty, item, catalog, customer))
    if not lines:
        raise ValueError("Pick at least one item")
    total = round(sum(line.amount for line in lines if line.status == "ok"), 2)
    summary = ", ".join(f"{line.name} × {line.qty:g}" for line in lines)
    return Slip(customer_id=(customer or {}).get("id"), language=language, lines=tuple(lines), total=total,
                delivery=None, credit=False, needs_owner=any(line.status != "ok" for line in lines),
                transcript=f"Tapped: {summary}")


def customer_choose(slip: dict, index: int, item_id: str, catalog: Catalog) -> dict:
    """The customer answers the shop's question by tapping one of the OFFERED options (nothing else)."""
    lines = slip["lines"]
    if not 0 <= index < len(lines):
        raise IndexError(f"No line {index}")
    line = lines[index]
    offered = set(line.get("options") or [])
    if line["status"] == "out_of_stock" and line.get("substitute_id"):
        offered.add(line["substitute_id"])
    if line["status"] not in ("ask", "out_of_stock") or item_id not in offered:
        raise ValueError("That choice wasn't offered for this item")
    return choose_item(slip, index, item_id, catalog)


def build_slip(parsed: dict, catalog: Catalog, customer: dict | None, transcript: str = "") -> Slip:
    language = parsed.get("language") or (customer or {}).get("language") or "hi"
    lines: list[SlipLine] = []
    if parsed.get("repeat_usual"):
        lines.extend(_usual_lines(catalog, customer))
    lines.extend(_resolve_line(raw, catalog, customer, language) for raw in parsed.get("items", []))
    if not lines:  # nothing understood: never show an empty ₹0 order as ready
        lines.append(_unknown_line(transcript.strip() or "?", 1.0, language))

    total = round(sum(line.amount for line in lines if line.status == "ok"), 2)
    return Slip(
        customer_id=(customer or {}).get("id"),
        language=language,
        lines=tuple(lines),
        total=total,
        delivery=parsed.get("delivery"),
        credit=bool(parsed.get("credit")),
        needs_owner=any(line.status != "ok" for line in lines),
        transcript=transcript,
        pay_by=parsed.get("pay_by"),
    )
