"""Loopback-only fake business backend with atomic, idempotent operation receipts.

Its SQLite ledger is independent from the application's checkpoint database.
Only seeded synthetic orders exist. It never calls a payment or shipping API.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .store import canonical, digest


class Business:
    def __init__(self, path):
        self.path = str(path)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS orders (id TEXT PRIMARY KEY, age INTEGER, amount INTEGER, disputed INTEGER);
                CREATE TABLE IF NOT EXISTS operations (id TEXT PRIMARY KEY, request_digest TEXT NOT NULL, receipt TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS requests (sequence INTEGER PRIMARY KEY AUTOINCREMENT, operation_id TEXT NOT NULL);
            """)
        Path(path).chmod(0o600)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.execute("PRAGMA synchronous=FULL")
        return db

    def seed(self, order_id, *, age=30, amount=25, disputed=False):
        if not order_id.startswith("synthetic-"):
            raise ValueError("synthetic_orders_only")
        with self.connect() as db:
            db.execute(
                "INSERT INTO orders VALUES (?,?,?,?)",
                (order_id, age, amount, int(disputed)),
            )

    def execute(self, key, body):
        action = body.get("action")
        required = (
            {"action", "order_id", "amount"}
            if action == "issue_refund"
            else {"action", "order_id"}
        )
        if set(body) != required or action not in {
            "issue_refund",
            "open_chargeback",
            "track_shipment",
        }:
            return 400, {"error": "invalid_operation"}
        expected_key = digest({"order_id": body["order_id"], "action": action})
        if key != expected_key:
            return 400, {"error": "invalid_idempotency_key"}
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO requests(operation_id) VALUES (?)", (key,))
            old = db.execute(
                "SELECT request_digest,receipt FROM operations WHERE id=?", (key,)
            ).fetchone()
            if old:
                return (
                    (200, json.loads(old[1]))
                    if old[0] == digest(body)
                    else (409, {"error": "idempotency_conflict"})
                )
            order = db.execute(
                "SELECT age,amount,disputed FROM orders WHERE id=?", (body["order_id"],)
            ).fetchone()
            if not order:
                return 404, {"error": "unknown_synthetic_order"}
            age, amount, disputed = order
            allowed = True
            if action == "issue_refund":
                allowed = (
                    type(body["amount"]) is int
                    and 0 < body["amount"] <= amount <= 25
                    and age <= 30
                    and not disputed
                )
            elif action == "open_chargeback":
                allowed = bool(disputed)
            receipt = {
                "schema": 1,
                "operation_id": key,
                "request_digest": digest(body),
                "action": action,
                "order_id": body["order_id"],
                "status": "applied" if allowed else "declined",
                "amount": body.get("amount")
                if allowed and action == "issue_refund"
                else 0,
            }
            db.execute(
                "INSERT INTO operations VALUES (?,?,?)",
                (key, digest(body), canonical(receipt)),
            )
            return 200, receipt

    def ledger(self):
        with self.connect() as db:
            return {
                "operations": [
                    json.loads(row[0])
                    for row in db.execute("SELECT receipt FROM operations ORDER BY id")
                ],
                "request_count": db.execute("SELECT COUNT(*) FROM requests").fetchone()[
                    0
                ],
            }


def server(business, port=0):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, status, body):
            data = canonical(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self.reply(
                200, business.ledger()
            ) if self.path == "/ledger" else self.reply(404, {})

        def do_POST(self):
            if self.path != "/operations":
                return self.reply(404, {})
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 4096:
                return self.reply(413, {})
            try:
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise TypeError("invalid_request_body")
                status, value = business.execute(
                    self.headers.get("Idempotency-Key"), body
                )
            except (ValueError, TypeError):
                return self.reply(400, {"error": "invalid_request"})
            self.reply(status, value)

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--port", type=int, default=8099)
    args = parser.parse_args()
    server(Business(args.database), args.port).serve_forever()


if __name__ == "__main__":
    main()
