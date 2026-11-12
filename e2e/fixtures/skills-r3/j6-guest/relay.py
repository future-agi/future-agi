"""J6 guest sidecar: the only egress a VM container guest has (synthetic fixture).

Roles on the guest-only internal network (alias ``skills-fakes``):
  :8081 authenticated registry relay -> real product API (outage switch)
  :8082 deterministic rule-based model (OpenAI-shaped, not an LLM)
  :8083 independent business backend (own SQLite, idempotent receipts)
Control on :8090 is published to the VM loopback only and requires a bearer
token. It arms faults and reads independent ledgers; it never edits application
state, product rows or business receipts. No secrets or bodies are logged.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
from ctxhub_support.business import Business
from ctxhub_support.store import canonical

UPSTREAM = os.environ["J6_REGISTRY_UPSTREAM"].rstrip("/")
CONTROL_TOKEN = os.environ["J6_CONTROL_TOKEN"]
FORWARD = {"authorization", "content-type", "idempotency-key", "x-context-resolution"}
LOCK = threading.Lock()
STATE = {"available": True, "arm_after_business": False, "outages": 0}
REGISTRY = []
MODEL = []
BUSINESS = Business(os.environ.get("J6_BUSINESS_DB", "/business/business.sqlite"))
ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class Base(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def body(self) -> bytes:
        size = int(self.headers.get("Content-Length", "0") or 0)
        if size > 4 * 1024 * 1024:
            raise ValueError("body_too_large")
        return self.rfile.read(size) if size else b""

    def reply(
        self, status: int, payload: bytes | dict, content_type="application/json"
    ):
        data = payload if isinstance(payload, bytes) else canonical(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class Registry(Base):
    def relay(self):
        started = time.perf_counter_ns()
        body = self.body()
        if not self.path.startswith("/model-hub/skills/contexts/"):
            return self.reply(404, {"error": "not_relayed"})
        with LOCK:
            available = STATE["available"]
        if not available:
            status, payload = 503, b'{"error":"synthetic_registry_outage"}'
        else:
            with httpx.Client(
                trust_env=False, follow_redirects=False, timeout=30
            ) as client:
                result = client.request(
                    self.command,
                    UPSTREAM + self.path,
                    content=body,
                    headers={
                        k: v for k, v in self.headers.items() if k.lower() in FORWARD
                    },
                )
            status, payload = result.status_code, result.content
        self.reply(status, payload)
        with LOCK:
            # Route shape and latency only: no credential, body or file bytes.
            REGISTRY.append(
                {
                    "method": self.command,
                    "route": ID.sub("{id}", self.path.split("?", 1)[0]),
                    "status": status,
                    "elapsed_ns": time.perf_counter_ns() - started,
                    "outage": not available,
                }
            )

    do_GET = relay
    do_POST = relay


def decide(request: dict) -> tuple[dict, dict]:
    """Deterministic policy reader. Behaviour depends only on delivered bytes."""
    messages = request["messages"]
    system = messages[0]["content"]
    user = json.loads(messages[1]["content"])
    catalog = json.loads(system.split("\nAvailable skills:\n", 1)[1])
    tools = [m for m in messages if m["role"] == "tool"]
    results = [json.loads(m["content"]) for m in tools]
    turn = len(messages)

    def call(name, args):
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": f"{name}-{turn}",
                    "type": "function",
                    "function": {"name": name, "arguments": canonical(args)},
                }
            ],
        }

    def final(text):
        return {"role": "assistant", "content": text}

    wants_refund = "refund" in user["request"].lower()
    member = next((m for m in catalog if "refund" in m["description"].lower()), None)
    if not wants_refund or member is None:
        return final("No refund skill applies; no business action requested."), {
            "rule": "no_refund_skill"
        }
    policy = next((r for r in results if r.get("path") == "references/policy.md"), None)
    refund = next((r for r in results if r.get("action") == "issue_refund"), None)
    if refund is not None:
        text = (
            f"Refund of {refund['amount']} units applied; operation {refund['operation_id']}."
            if refund.get("status") == "applied"
            else "Refund declined by the business system."
        )
        return final(text), {
            "rule": "report_business_receipt",
            "status": refund.get("status"),
        }
    if not results:
        return call("load_skill", {"skill_id": member["id"]}), {
            "rule": "select_refund_skill"
        }
    if policy is None:
        return call(
            "read_skill_file",
            {"skill_id": member["id"], "path": "references/policy.md"},
        ), {"rule": "read_policy"}
    stated = re.search(
        r"refund (\d+) units when age is at most (\d+) days", policy["content"]
    )
    if not stated:
        return final("The retained refund policy is unreadable; escalating."), {
            "rule": "policy_unreadable"
        }
    amount, window = int(stated[1]), int(stated[2])
    override = re.search(r"REFUND-WINDOW-DAYS: (\d+)", system)
    if override:
        window = min(window, int(override[1]))
    age = re.search(r"(\d+) days ago", user["request"])
    if age is None or int(age[1]) > window:
        return final(
            f"Refund declined: the order is outside the {window}-day refund window."
        ), {"rule": "declined_by_window", "window": window}
    return call("issue_refund", {"amount": amount}), {
        "rule": "refund_within_window",
        "window": window,
    }


class Model(Base):
    def do_POST(self):
        raw = self.body()
        if not self.path.endswith("/chat/completions"):
            return self.reply(404, {"error": "unknown_route"})
        request = json.loads(raw)
        message, rule = decide(request)
        finish = "tool_calls" if message.get("tool_calls") else "stop"
        response = {
            "id": "j6-" + sha(raw)[:16],
            "object": "chat.completion",
            "model": request.get("model"),
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        }
        system = request["messages"][0]["content"]
        delivered = [
            json.loads(m["content"]) for m in request["messages"] if m["role"] == "tool"
        ]
        with LOCK:
            MODEL.append(
                {
                    "request_sha256": sha(raw),
                    "system_sha256": sha(
                        system.split("\nAvailable skills:\n", 1)[0].encode()
                    ),
                    "delivered_files": [
                        {
                            "skill_id": d["skill_id"],
                            "path": d["path"],
                            "sha256": sha(d["content"].encode()),
                        }
                        for d in delivered
                        if "content" in d and "skill_id" in d
                    ],
                    "decision": rule,
                }
            )
        self.reply(200, response)


class Operations(Base):
    def do_GET(self):
        # The same read-only ledger view the business server itself exposes; it
        # arms no fault and never mutates a receipt.
        if self.path != "/ledger":
            return self.reply(404, {})
        self.reply(200, BUSINESS.ledger())

    def do_POST(self):
        if self.path != "/operations":
            return self.reply(404, {})
        try:
            body = json.loads(self.body())
            status, value = BUSINESS.execute(self.headers.get("Idempotency-Key"), body)
        except (ValueError, TypeError):
            return self.reply(400, {"error": "invalid_request"})
        with LOCK:
            if STATE["arm_after_business"]:
                # Receipt is committed and returned; the NEXT registry boundary fails.
                STATE.update(available=False, arm_after_business=False)
                STATE["outages"] += 1
        self.reply(status, value)


class Control(Base):
    def authorized(self) -> bool:
        supplied = self.headers.get("Authorization", "")
        return hmac.compare_digest(supplied, "Bearer " + CONTROL_TOKEN)

    def do_GET(self):
        if not self.authorized():
            return self.reply(401, {})
        with LOCK:
            if self.path == "/control/ledger":
                return self.reply(
                    200, {"registry": REGISTRY, "model": MODEL, "state": STATE}
                )
        if self.path == "/control/business":
            return self.reply(200, BUSINESS.ledger())
        return self.reply(404, {})

    def do_POST(self):
        if not self.authorized():
            return self.reply(401, {})
        body = json.loads(self.body() or b"{}")
        if self.path == "/control/orders":
            BUSINESS.seed(body["order_id"], age=body["age"], amount=body["amount"])
            return self.reply(200, {"seeded": body["order_id"]})
        if self.path == "/control/faults":
            with LOCK:
                if "available" in body:
                    STATE["available"] = bool(body["available"])
                if "arm_after_business" in body:
                    STATE["arm_after_business"] = bool(body["arm_after_business"])
                return self.reply(200, dict(STATE))
        return self.reply(404, {})


def main():
    servers = [
        ThreadingHTTPServer(("0.0.0.0", port), handler)
        for port, handler in [
            (8081, Registry),
            (8082, Model),
            (8083, Operations),
            (8090, Control),
        ]
    ]
    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in servers]
    for thread in threads:
        thread.start()
    print(canonical({"ready": True, "evidence_class": "synthetic fixture"}), flush=True)
    for thread in threads:
        thread.join()


if __name__ == "__main__":
    main()
