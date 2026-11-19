"""Authenticated delivery/closure receipts with the application's report credential.

Receipts state only what the durable thread state proves: the exact AGENTS.md
bytes were in a model request that received a response (``root_delivery``), or
the operator explicitly closed an abandoned thread. A report never resolves a
label, opens a session or claims business recovery.
"""

from __future__ import annotations

import hashlib
import time
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid5

ADAPTER = "ctxhub-support/0.1.0"
RUNTIME = "python/3.11"


class ReportError(RuntimeError):
    pass


def root_text(state: dict) -> str:
    """The AGENTS.md bytes the application actually staged in the system message."""
    system = state["messages"][0]["content"]
    text = system.split("\nAvailable skills:\n", 1)[0]
    expected = next(f for f in state["lock"]["files"] if f["path"] == "AGENTS.md")
    if hashlib.sha256(text.encode()).hexdigest() != expected["sha256"]:
        raise ReportError("staged_root_differs_from_locked_closure")
    return text


def _receipt(
    state: dict, stage: str, event_key: str, *, path=None, text=None, codes=()
):
    session = state["session"]
    identity = state["lock"]["identity"]
    return {
        "event_id": str(
            uuid5(NAMESPACE_URL, f"ctxhub-support:{session['session_id']}:{event_key}")
        ),
        "session_id": session["session_id"],
        "instance_id": state["incarnation"],
        # Monotonic per instance; later closure always supersedes earlier delivery.
        "sequence": time.time_ns() // 1000,
        "stage": stage,
        "version_id": identity["version_id"],
        "digest": identity["digest"],
        "materialization_digest": None,
        "observed_at": datetime.now(UTC).isoformat(),
        "details": {
            "path": path,
            "sha256": hashlib.sha256(text.encode()).hexdigest()
            if text is not None
            else None,
            "ranges": [[0, len(text)]] if text is not None else [],
            "runtime_version": RUNTIME,
            "adapter_version": ADAPTER,
            "diagnostic_codes": list(codes),
            "trace_ref": None,
        },
    }


def post(client, binding_id: str, receipt: dict) -> None:
    client.request(
        "POST",
        f"/model-hub/skills/contexts/bindings/{UUID(binding_id)}/receipts/",
        body=receipt,
    )


def report_deliveries(store, thread: str, client, binding_id: str) -> int:
    """Report each delivered root once per session; return how many were posted."""
    state = store.load(thread)
    if not state or not state.get("session") or not state.get("lock"):
        return 0
    events = store.events(thread)
    reported = {
        e["body"]["session_id"] for e in events if e["kind"] == "delivery_reported"
    }
    delivered = any(e["kind"] == "root_delivery" for e in events)
    session_id = state["session"]["session_id"]
    if not delivered or session_id in reported:
        return 0
    text = root_text(state)
    post(
        client,
        binding_id,
        _receipt(state, "instructions_delivered", "root", path="AGENTS.md", text=text),
    )
    store.event(
        thread,
        "delivery_reported",
        {"session_id": session_id, "stage": "instructions_delivered"},
    )
    return 1


def report_closed(store, thread: str, client, binding_id: str, reason: str) -> dict:
    state = store.load(thread)
    if not state or not state.get("session"):
        raise ReportError("thread_has_no_session_to_close")
    post(client, binding_id, _receipt(state, "closed", "closed", codes=[reason]))
    store.event(
        thread,
        "closed_reported",
        {"session_id": state["session"]["session_id"], "reason": reason},
    )
    return state
