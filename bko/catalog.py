"""Shop rate list and phrase -> item matching.

Matching is deterministic on purpose: the AI only splits a voice note into
phrases; prices and item choice always come from the shop's own rate list.
"""
from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass
from pathlib import Path

FUZZY_CUTOFF = 0.82


@dataclass(frozen=True)
class Item:
    id: str
    category: str
    name: str
    pack: str
    price: float
    in_stock: bool
    aliases: tuple[str, ...]
    photo: str | None = None  # file name in data/photos/, set when the owner uploads one


@dataclass(frozen=True)
class Category:
    key: str
    labels: dict
    aliases: tuple[str, ...]

    def label(self, language: str) -> str:
        return self.labels.get(language) or self.labels.get("en") or self.key


@dataclass(frozen=True)
class Match:
    kind: str  # "item" | "category" | "none"
    item: Item | None = None
    category: str | None = None
    score: float = 0.0


NO_MATCH = Match(kind="none")


def normalise(text: str) -> str:
    text = (text or "").lower().replace("-", " ")
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _contains(phrase: str, alias: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", phrase) is not None


class Catalog:
    def __init__(self, items: list[Item], categories: dict[str, Category]) -> None:
        self.items: tuple[Item, ...] = tuple(items)
        self.categories: dict[str, Category] = dict(categories)
        self._by_id = {item.id: item for item in self.items}
        self._alias_targets = self._build_alias_index()

    @classmethod
    def load(cls, path: Path | str) -> "Catalog":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        categories = {
            key: Category(
                key=key,
                labels={k: v for k, v in spec.items() if k != "aliases"},
                aliases=tuple(normalise(a) for a in spec.get("aliases", [])),
            )
            for key, spec in raw.get("categories", {}).items()
        }
        items = [
            Item(
                id=entry["id"],
                category=entry["category"],
                name=entry["name"],
                pack=entry["pack"],
                price=float(entry["price"]),
                in_stock=bool(entry["in_stock"]),
                aliases=tuple(normalise(a) for a in entry.get("aliases", [])),
                photo=entry.get("photo"),
            )
            for entry in raw["items"]
        ]
        return cls(items, categories)

    def _build_alias_index(self) -> list[tuple[str, str, str]]:
        """(alias, kind, target) sorted longest alias first."""
        index = [(alias, "item", item.id) for item in self.items for alias in item.aliases]
        index += [(alias, "category", cat.key) for cat in self.categories.values() for alias in cat.aliases]
        # Longer alias wins; on equal length an item beats a category.
        return sorted(index, key=lambda row: (-len(row[0]), row[1] != "item"))

    @staticmethod
    def add_item_to_file(path: Path | str, name: str, pack: str, price: float, category: str = "other") -> str:
        """Append a new in-stock item to the rate list JSON and return its id. Reload the catalog after."""
        path = Path(path)
        name, pack = name.strip(), pack.strip() or "1 pc"
        if not name or len(name) > 60:
            raise ValueError("Item name must be 1-60 characters")
        if not 0 < price <= 100_000:
            raise ValueError("Price must be between 1 and 100000")
        raw = json.loads(path.read_text(encoding="utf-8"))
        existing = {entry["id"] for entry in raw["items"]}
        if any(normalise(entry["name"]) == normalise(name) for entry in raw["items"]):
            raise ValueError(f"{name} is already in the rate list")
        base = "custom_" + (re.sub(r"[^a-z0-9]+", "_", normalise(name)).strip("_") or "item")
        item_id, n = base, 2
        while item_id in existing:
            item_id, n = f"{base}_{n}", n + 1
        new_entry = {"id": item_id, "category": category, "name": name, "pack": pack, "price": price,
                     "in_stock": True, "aliases": [name.lower()]}
        updated = {**raw, "items": [*raw["items"], new_entry]}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(updated, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
        return item_id

    @staticmethod
    def update_item_in_file(path: Path | str, item_id: str, price: float | None = None,
                            in_stock: bool | None = None, photo: str | None = None) -> None:
        """Change one item's price, stock flag and/or photo in the rate list JSON (written atomically)."""
        path = Path(path)
        if price is not None and not 0 < price <= 100_000:
            raise ValueError("Price must be between 1 and 100000")
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not any(entry["id"] == item_id for entry in raw["items"]):
            raise KeyError(f"No item {item_id}")
        changes = {k: v for k, v in (("price", price), ("in_stock", in_stock), ("photo", photo)) if v is not None}
        items = [{**entry, **changes} if entry["id"] == item_id else entry for entry in raw["items"]]
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({**raw, "items": items}, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)

    def get(self, item_id: str) -> Item | None:
        return self._by_id.get(item_id)

    def in_category(self, category: str) -> list[Item]:
        return [item for item in self.items if item.category == category]

    def label(self, category: str, language: str) -> str:
        cat = self.categories.get(category)
        return cat.label(language) if cat else category

    def _to_match(self, kind: str, target: str, score: float) -> Match:
        if kind == "item":
            item = self._by_id[target]
            return Match(kind="item", item=item, category=item.category, score=score)
        return Match(kind="category", category=target, score=score)

    def match(self, phrase: str) -> Match:
        text = normalise(phrase)
        if not text:
            return NO_MATCH
        for alias, kind, target in self._alias_targets:
            if _contains(text, alias):
                return self._to_match(kind, target, 1.0)
        return self._fuzzy(text)

    def _fuzzy(self, text: str) -> Match:
        aliases = [row[0] for row in self._alias_targets]
        best: Match = NO_MATCH
        for candidate in [text, *text.split()]:
            close = difflib.get_close_matches(candidate, aliases, n=1, cutoff=FUZZY_CUTOFF)
            if not close:
                continue
            score = difflib.SequenceMatcher(None, candidate, close[0]).ratio()
            if score > best.score:
                _, kind, target = next(row for row in self._alias_targets if row[0] == close[0])
                best = self._to_match(kind, target, score)
        return best
