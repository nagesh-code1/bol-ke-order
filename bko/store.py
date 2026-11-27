"""SQLite storage: customers, orders, and the udhaar (credit) ledger.

Money is only ever changed here, in plain Python, never by the AI.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import date, datetime
from pathlib import Path

STATUSES = ("new", "needs_answer", "accepted", "packed", "delivered", "rejected")
ACCEPTED_LIKE = ("accepted", "packed", "delivered")

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, phone TEXT, language TEXT,
    usual_json TEXT NOT NULL DEFAULT '[]', seed_history_json TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT, customer_id TEXT, created_at TEXT NOT NULL,
    status TEXT NOT NULL, source TEXT, slip_json TEXT NOT NULL, reply_text TEXT, reply_audio TEXT,
    packed_json TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT, customer_id TEXT NOT NULL, order_id INTEGER,
    amount REAL NOT NULL, note TEXT, created_at TEXT NOT NULL, UNIQUE(order_id)
);
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        self._lock = threading.Lock()
        with self._connect() as db:
            db.executescript(SCHEMA)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(orders)")}
            if "packed_json" not in columns:  # databases made before packing was saved
                db.execute("ALTER TABLE orders ADD COLUMN packed_json TEXT NOT NULL DEFAULT '[]'")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db

    # ---- customers -------------------------------------------------------
    def seed_customers(self, customers: list[dict]) -> None:
        with self._lock, self._connect() as db:
            for c in customers:
                inserted = db.execute(
                    "INSERT OR IGNORE INTO customers (id, name, phone, language, usual_json, seed_history_json)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (c["id"], c["name"], c.get("phone"), c.get("language", "hi"),
                     json.dumps(c.get("usual", [])), json.dumps(c.get("history", []))),
                ).rowcount
                if inserted and c.get("credit_balance"):
                    db.execute("INSERT INTO ledger (customer_id, order_id, amount, note, created_at)"
                               " VALUES (?, NULL, ?, 'Opening balance', ?)",
                               (c["id"], float(c["credit_balance"]), _now()))

    def list_customers(self) -> list[dict]:
        with self._connect() as db:
            ids = [row["id"] for row in db.execute("SELECT id FROM customers ORDER BY name")]
        return [self.get_customer(cid) for cid in ids]

    def get_customer(self, customer_id: str) -> dict | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
            if row is None:
                return None
            balance = db.execute("SELECT COALESCE(SUM(amount), 0) FROM ledger WHERE customer_id = ?",
                                 (customer_id,)).fetchone()[0]
            past = db.execute(
                "SELECT slip_json FROM orders WHERE customer_id = ? AND status IN (?, ?, ?) ORDER BY id DESC",
                (customer_id, *ACCEPTED_LIKE)).fetchall()
        history = [
            {"item_id": line["item_id"], "qty": line["qty"]}
            for order in past for line in json.loads(order["slip_json"])["lines"]
            if line["status"] == "ok" and line["item_id"]
        ] + json.loads(row["seed_history_json"])
        return {"id": row["id"], "name": row["name"], "phone": row["phone"], "language": row["language"],
                "usual": json.loads(row["usual_json"]), "history": history, "credit_balance": round(balance, 2)}

    # ---- orders ----------------------------------------------------------
    @staticmethod
    def _row_to_order(row: sqlite3.Row) -> dict:
        return {"id": row["id"], "customer_id": row["customer_id"], "created_at": row["created_at"],
                "status": row["status"], "source": row["source"], "slip": json.loads(row["slip_json"]),
                "reply_text": row["reply_text"], "reply_audio": row["reply_audio"],
                "packed": json.loads(row["packed_json"] or "[]")}

    def add_order(self, customer_id: str | None, slip: dict, source: str = "") -> dict:
        status = "needs_answer" if slip.get("needs_owner") else "new"
        with self._lock, self._connect() as db:
            cur = db.execute(
                "INSERT INTO orders (customer_id, created_at, status, source, slip_json) VALUES (?, ?, ?, ?, ?)",
                (customer_id, _now(), status, source, json.dumps(slip, ensure_ascii=False)))
            order_id = cur.lastrowid
        return self.get_order(order_id)

    def get_order(self, order_id: int) -> dict:
        with self._connect() as db:
            row = db.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        if row is None:
            raise KeyError(f"Order {order_id} not found")
        return self._row_to_order(row)

    def list_orders(self, status: str | None = None, limit: int = 100) -> list[dict]:
        query, args = "SELECT * FROM orders", []
        if status:
            query, args = query + " WHERE status = ?", [status]
        with self._connect() as db:
            rows = db.execute(query + " ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()
        return [self._row_to_order(row) for row in rows]

    def update_slip(self, order_id: int, slip: dict) -> dict:
        current = self.get_order(order_id)
        if current["status"] in ACCEPTED_LIKE:
            raise ValueError("Order already accepted; it can't be edited")
        status = "needs_answer" if slip.get("needs_owner") else "new"
        with self._lock, self._connect() as db:
            db.execute("UPDATE orders SET slip_json = ?, status = ? WHERE id = ?",
                       (json.dumps(slip, ensure_ascii=False), status, order_id))
        return self.get_order(order_id)

    def set_status(self, order_id: int, status: str) -> dict:
        if status not in STATUSES:
            raise ValueError(f"Unknown status: {status}")
        self.get_order(order_id)
        with self._lock, self._connect() as db:
            db.execute("UPDATE orders SET status = ? WHERE id = ?", (status, order_id))
        return self.get_order(order_id)

    def set_reply(self, order_id: int, reply_text: str, reply_audio: str | None = None) -> dict:
        self.get_order(order_id)
        with self._lock, self._connect() as db:
            db.execute("UPDATE orders SET reply_text = ?, reply_audio = ? WHERE id = ?",
                       (reply_text, reply_audio, order_id))
        return self.get_order(order_id)

    def set_packed(self, order_id: int, index: int, done: bool) -> dict:
        order = self.get_order(order_id)
        if not 0 <= index < len(order["slip"]["lines"]):
            raise IndexError(f"No line {index}")
        packed = sorted(set(order["packed"]) | {index}) if done else [i for i in order["packed"] if i != index]
        with self._lock, self._connect() as db:
            db.execute("UPDATE orders SET packed_json = ? WHERE id = ?", (json.dumps(packed), order_id))
        return self.get_order(order_id)

    def accept(self, order_id: int) -> dict:
        order = self.get_order(order_id)
        if order["status"] in ACCEPTED_LIKE:
            return order  # idempotent: a double tap changes nothing
        if not order["slip"]["lines"]:
            raise ValueError("This order has no items; reject it or ask the customer")
        if any(line["status"] != "ok" for line in order["slip"]["lines"]):
            raise ValueError("Answer the open questions on this order before accepting it")
        slip = order["slip"]
        with self._lock, self._connect() as db:
            db.execute("UPDATE orders SET status = 'accepted' WHERE id = ?", (order_id,))
            if slip.get("credit") and order["customer_id"] and slip["total"] > 0:
                db.execute("INSERT OR IGNORE INTO ledger (customer_id, order_id, amount, note, created_at)"
                           " VALUES (?, ?, ?, ?, ?)",
                           (order["customer_id"], order_id, float(slip["total"]),
                            f"Order #{order_id}" + (f", pay by {slip['pay_by']}" if slip.get("pay_by") else ""),
                            _now()))
        return self.get_order(order_id)

    # ---- reports ---------------------------------------------------------
    def day_summary(self, day: date | None = None) -> dict:
        prefix = (day or date.today()).isoformat()
        orders = [o for o in self.list_orders(limit=10_000) if o["created_at"].startswith(prefix)]
        accepted = [o for o in orders if o["status"] in ACCEPTED_LIKE]
        with self._connect() as db:
            credit = db.execute("SELECT COALESCE(SUM(amount), 0) FROM ledger WHERE order_id IS NOT NULL"
                                " AND created_at LIKE ?", (prefix + "%",)).fetchone()[0]
        return {
            "date": prefix,
            "orders": len(orders),
            "accepted": len(accepted),
            "pending": sum(o["status"] in ("new", "needs_answer") for o in orders),
            "sales": round(sum(o["slip"]["total"] for o in accepted), 2),
            "credit_given": round(credit, 2),
        }
