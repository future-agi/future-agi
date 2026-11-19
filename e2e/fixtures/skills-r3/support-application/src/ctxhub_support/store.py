"""Crash-durable application state, independent of the business backend ledger."""

from __future__ import annotations

import fcntl
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


def canonical(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def digest(value):
    return "sha256:" + hashlib.sha256(canonical(value).encode()).hexdigest()


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(self.path, timeout=30)
        self.path.chmod(0o600)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS threads (id TEXT PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS receipts (
                thread TEXT NOT NULL, call_id TEXT NOT NULL, request_hash TEXT NOT NULL,
                body TEXT NOT NULL, PRIMARY KEY(thread, call_id));
            CREATE TABLE IF NOT EXISTS events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, thread TEXT NOT NULL,
                kind TEXT NOT NULL, body TEXT NOT NULL);
        """)

    @contextmanager
    def exclusive(self):
        # Held across external calls: competing processes may not advance one checkpoint.
        with self.path.with_suffix(".lock").open("a", encoding="utf-8") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def load(self, thread):
        row = self.db.execute(
            "SELECT body FROM threads WHERE id=?", (thread,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def save(self, thread, state, event="checkpoint"):
        with self.db:
            self.db.execute(
                "INSERT INTO threads VALUES (?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",
                (thread, canonical(state)),
            )
            self.db.execute(
                "INSERT INTO events(thread,kind,body) VALUES (?,?,?)",
                (
                    thread,
                    event,
                    canonical(
                        {
                            "checkpoint": state["checkpoint"],
                            "state_digest": digest(state),
                        }
                    ),
                ),
            )

    def receipt(self, thread, call_id, request):
        row = self.db.execute(
            "SELECT request_hash,body FROM receipts WHERE thread=? AND call_id=?",
            (thread, call_id),
        ).fetchone()
        if row is None:
            return None
        if row[0] != digest(request):
            raise ValueError("tool_call_identity_conflict")
        return json.loads(row[1])

    def record_receipt(self, thread, call_id, request, result):
        # Separate FULL-synchronous commit BEFORE checkpoint advance, not one in-memory log.
        with self.db:
            self.db.execute(
                "INSERT INTO receipts VALUES (?,?,?,?)",
                (thread, call_id, digest(request), canonical(result)),
            )
            self.db.execute(
                "INSERT INTO events(thread,kind,body) VALUES (?,?,?)",
                (
                    thread,
                    "tool_receipt",
                    canonical({"call_id": call_id, "result_digest": digest(result)}),
                ),
            )

    def event(self, thread, kind, body):
        with self.db:
            self.db.execute(
                "INSERT INTO events(thread,kind,body) VALUES (?,?,?)",
                (thread, kind, canonical(body)),
            )

    def events(self, thread):
        return [
            {"sequence": row[0], "kind": row[1], "body": json.loads(row[2])}
            for row in self.db.execute(
                "SELECT sequence,kind,body FROM events WHERE thread=? ORDER BY sequence",
                (thread,),
            )
        ]

    def close(self):
        self.db.close()
