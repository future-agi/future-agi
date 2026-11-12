"""Operator thread lifecycle for recovery; synthetic fixture, no qualification claim.

    python -m ctxhub_support.lifecycle inspect --state DB --thread ID --output FILE
    python -m ctxhub_support.lifecycle close --state DB --thread ID --reason CODE --output FILE

``inspect`` reads the durable thread and performs only the session's live access
check with the current consume credential. It never resolves a label, opens a
session, calls a model or repeats a tool. ``close`` records an explicit closed
receipt with the report credential and marks the thread closed so ``run``
refuses it. Business receipts and transcripts are never erased or copied.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from contextlib import ExitStack
from pathlib import Path
from uuid import UUID

from fi.platform import Client, ContextAuth

from .entrypoint import fixture_url
from .reporting import report_closed
from .store import Store, canonical, digest

REASON = re.compile(r"[a-z][a-z0-9_]{0,63}")


def _client(variable):
    return Client(
        base_url=fixture_url(os.environ["CTXHUB_REGISTRY_URL"]).rstrip("/"),
        auth=ContextAuth(os.environ[variable]),
        max_retries=0,
    )


def classify(status: int | None) -> str:
    if status is None:
        return "registry_unavailable"
    if status in (401, 403):
        return "authority_unavailable"
    if status in (404, 409):
        return "thread_mapping_unavailable"
    if status >= 500:
        return "registry_unavailable"
    return "access_refused"


def inspect(store, thread, client, binding_id):
    state = store.load(thread)
    if state is None:
        return {
            "thread": thread,
            "known": False,
            "resumable": False,
            "reason": "thread_state_missing",
        }
    session = state.get("session") or {}
    receipts = [
        {"call_id": row[0], "result_digest": digest(json.loads(row[1]))}
        for row in store.db.execute(
            "SELECT call_id, body FROM receipts WHERE thread=? ORDER BY call_id",
            (thread,),
        )
    ]
    pending = [
        call["id"]
        for message in state["messages"]
        if message.get("role") == "assistant"
        for call in message.get("tool_calls") or []
        if not any(m.get("tool_call_id") == call["id"] for m in state["messages"])
    ]
    result = {
        "thread": thread,
        "known": True,
        "phase": state["phase"],
        "checkpoint": state["checkpoint"],
        "session_id": session.get("session_id"),
        "version_id": session.get("version_id"),
        "version": session.get("version"),
        "durable_receipts": receipts,
        "pending_tool_calls": pending,
        "case_digest": digest(state["case"]),
    }
    if state["phase"] == "closed":
        return {**result, "resumable": False, "reason": "thread_closed"}
    if not session.get("session_id"):
        return {**result, "resumable": False, "reason": "thread_mapping_unavailable"}
    status = None
    try:
        client.request(
            "POST",
            f"/model-hub/skills/contexts/bindings/{UUID(binding_id)}/sessions/{UUID(session['session_id'])}/access-check/",
            body={},
        )
        status = 200
    except Exception as exc:  # typed SDK errors; never echo a response body
        status = getattr(exc, "status_code", None) or getattr(
            getattr(exc, "response", None), "status_code", None
        )
    if status == 200:
        return {
            **result,
            "resumable": state["phase"] != "completed",
            "reason": "completed"
            if state["phase"] == "completed"
            else "original_identity_available",
        }
    return {**result, "resumable": False, "reason": classify(status)}


def close_thread(store, thread, client, binding_id, reason):
    """Record an explicit closed receipt and mark the thread closed so ``run`` refuses it."""
    with store.exclusive():
        state = store.load(thread)
        if state is None or state["phase"] == "closed":
            raise ValueError("thread_not_closable")
        report_closed(store, thread, client, binding_id, reason)
        state["phase"] = "closed"
        store.save(thread, state, "thread_closed")
        return {
            "thread": thread,
            "closed": True,
            "reason": reason,
            "session_id": state["session"]["session_id"],
            "checkpoint": state["checkpoint"],
        }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Inspect or close one durable thread")
    parser.add_argument("command", choices=["inspect", "close"])
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--thread", required=True)
    parser.add_argument("--reason", default="operator_closed")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if not REASON.fullmatch(args.reason):
        raise ValueError("invalid_reason_code")
    binding_id = os.environ["CTXHUB_BINDING_ID"]
    with ExitStack() as stack:
        store = Store(args.state)
        stack.callback(store.close)
        if args.command == "inspect":
            client = _client("CTXHUB_CONTEXT_CREDENTIAL")
            stack.callback(client.close)
            result = inspect(store, args.thread, client, binding_id)
        else:
            client = _client("CTXHUB_REPORT_CREDENTIAL")
            stack.callback(client.close)
            result = close_thread(store, args.thread, client, binding_id, args.reason)
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(canonical(result) + "\n")
        print(
            canonical(
                {
                    k: result.get(k)
                    for k in ("thread", "resumable", "reason", "closed")
                    if k in result
                }
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
