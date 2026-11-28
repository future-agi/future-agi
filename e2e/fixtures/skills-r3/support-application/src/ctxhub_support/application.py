"""A bounded support agent; model text is never a business-effect receipt.

All provider traffic uses the E2E Skills transport fake. SDK authorization is
checked at model and tool boundaries. Durable tool intents and independent
business idempotency allow safe recovery across either side of a network loss.
"""

from __future__ import annotations

import json
from uuid import uuid4

import httpx

from .registry import snapshot_lock
from .store import canonical, digest


class Paused(RuntimeError):
    pass


def function(name, properties, required):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": name.replace("_", " "),
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


TOOLS = [
    function("load_skill", {"skill_id": {"type": "string"}}, ["skill_id"]),
    function(
        "read_skill_file",
        {"skill_id": {"type": "string"}, "path": {"type": "string"}},
        ["skill_id", "path"],
    ),
    function("issue_refund", {"amount": {"type": "integer"}}, ["amount"]),
    function("open_chargeback", {}, []),
    function("track_shipment", {}, []),
]


class Application:
    def __init__(
        self,
        *,
        store,
        registry,
        provider,
        business,
        model="fixture-model",
        after_receipt=None,
    ):
        self.store, self.registry = store, registry
        self.provider, self.business, self.model = provider, business, model
        self.after_receipt = after_receipt

    def run(self, *, thread, case, expected_lock=None):
        """Resume a thread in-place. A different task, runtime or lock must be a new thread.

        Returns status=completed|paused and the actual durable checkpoint. Never
        reports recovered; the product must verify the original case independently.
        """
        with self.store.exclusive():
            state = self.store.load(thread)
            if state and state.get("phase") == "closed":
                # J6: an operator-closed thread is never resumed or re-resolved.
                raise ValueError("thread_closed")
            if state and (
                state["case"] != case
                or state["runtime_digest"] != self.registry.runtime_digest
                or state["authority"] != self.registry.authority
            ):
                raise ValueError("thread_identity_conflict")
            if state is None:
                if set(case) != {"case_id", "order_id", "request"} or not all(
                    isinstance(v, str) and v for v in case.values()
                ):
                    raise ValueError("invalid_case")
                state = {
                    "case": case,
                    "runtime_digest": self.registry.runtime_digest,
                    "authority": self.registry.authority,
                    "incarnation": str(uuid4()),
                    "open_operation_id": str(uuid4()),
                    "session": None,
                    "lock": None,
                    "checkpoint": 0,
                    "messages": [],
                    "phase": "new",
                    "turns": 0,
                    "selected": [],
                }
                self.store.save(thread, state, "thread_created")

            def retain_session(session):
                state["session"] = session
                self.store.save(thread, state, "session_registered")

            try:
                session, snapshot = self.registry.open(
                    thread,
                    state["incarnation"],
                    state["session"],
                    state["open_operation_id"] if state["session"] is None else None,
                    on_session=retain_session,
                )
                lock = snapshot_lock(snapshot)
                if (state["lock"] is not None and state["lock"] != lock) or (
                    expected_lock is not None and expected_lock != lock
                ):
                    raise Paused("locked_context_changed")
                state["session"], state["lock"] = session, lock
                self.snapshot, self.session = snapshot, session
                self.catalog = self._catalog(snapshot)
                if state["phase"] == "new":
                    state["messages"] = [
                        {
                            "role": "system",
                            "content": snapshot.files["AGENTS.md"]
                            + "\nAvailable skills:\n"
                            + canonical(self.catalog),
                        },
                        {
                            "role": "user",
                            "content": canonical(
                                {
                                    "request": case["request"],
                                    "order_id": case["order_id"],
                                }
                            ),
                        },
                    ]
                    state["phase"] = "model"
                    self.store.event(
                        thread,
                        "root_staged",
                        {
                            "sha256": digest(snapshot.files["AGENTS.md"]),
                            "lock_digest": digest(lock),
                        },
                    )
                    self.store.event(thread, "catalog", {"members": self.catalog})
                self.store.save(thread, state, "session_locked")
                if state["phase"] in {"model_inflight", "model_unknown"}:
                    raise Paused("model_response_unknown_requires_operator")
                while state["phase"] != "completed":
                    if state["phase"] == "tools":
                        self._tools(thread, state)
                        state["phase"] = "model"
                        self.store.save(thread, state)
                    if state["turns"] >= 8:
                        raise Paused("turn_limit")
                    self.registry.authorize(session, snapshot)
                    state["phase"] = "model_inflight"
                    self.store.save(thread, state)
                    request = {
                        "model": self.model,
                        "messages": state["messages"],
                        "tools": TOOLS,
                        "stream": False,
                        "temperature": 0,
                        "max_tokens": 512,
                    }
                    self.store.event(
                        thread,
                        "model_request",
                        {
                            "request_digest": digest(request),
                            "lock_digest": digest(lock),
                        },
                    )
                    try:
                        response = self.provider.post(
                            "chat/completions",
                            content=canonical(request),
                            headers={"Content-Type": "application/json"},
                        )
                        response.raise_for_status()
                        message = response.json()["choices"][0]["message"]
                        self._validate_message(message, state)
                    except (
                        httpx.HTTPError,
                        ValueError,
                        KeyError,
                        TypeError,
                        IndexError,
                    ) as exc:
                        state["phase"] = "model_unknown"
                        self.store.save(thread, state)
                        raise Paused(
                            "model_response_unknown_requires_operator"
                        ) from exc
                    self.store.event(
                        thread,
                        "model_response_received",
                        {"request_digest": digest(request)},
                    )
                    self.store.event(
                        thread,
                        "root_delivery",
                        {
                            "request_digest": digest(request),
                            "lock_digest": digest(lock),
                        },
                    )
                    for prior in state["messages"]:
                        if prior["role"] == "tool":
                            delivered = json.loads(prior["content"])
                            if "skill_id" in delivered and "content" in delivered:
                                self.store.event(
                                    thread,
                                    "file_delivery",
                                    {
                                        "request_digest": digest(request),
                                        "skill_id": delivered["skill_id"],
                                        "path": delivered["path"],
                                        "content_digest": digest(delivered["content"]),
                                    },
                                )
                    state["messages"].append(message)
                    state["turns"] += 1
                    state["phase"] = (
                        "tools" if message.get("tool_calls") else "completed"
                    )
                    state["checkpoint"] += 1
                    self.store.save(thread, state)
                return self._result(state, "completed")
            except (Paused, httpx.HTTPError) as exc:
                # SDK API errors are handled below as well; never retry or resolve a label.
                self.store.event(
                    thread,
                    "paused",
                    {
                        "reason": str(exc)
                        if isinstance(exc, Paused)
                        else type(exc).__name__
                    },
                )
                return self._result(
                    state,
                    "paused",
                    str(exc) if isinstance(exc, Paused) else type(exc).__name__,
                )
            except Exception as exc:
                # Platform transport exceptions do not inherit HTTPError. Keep their typed
                # code, never the response (which may include private request contents).
                from fi.context.snapshot import (
                    ResolutionExpiredError,
                    SnapshotIntegrityError,
                )
                from fi.platform import FutureAGIError

                if not isinstance(
                    exc,
                    (FutureAGIError, ResolutionExpiredError, SnapshotIntegrityError),
                ):
                    raise
                reason = type(exc).__name__
                self.store.event(thread, "paused", {"reason": reason})
                return self._result(state, "paused", reason)

    @staticmethod
    def _catalog(snapshot):
        descriptor = json.loads(snapshot.files[".futureagi/context.json"])
        return [
            {
                "id": link["skill_id"],
                "mount_path": link["mount"],
                "description": snapshot.files[link["mount"] + "SKILL.md"].split(
                    "---", 2
                )[1],
            }
            for link in descriptor["links"]
        ]

    @staticmethod
    def _validate_message(message, state):
        if not isinstance(message, dict) or message.get("role") != "assistant":
            raise ValueError("invalid_model_message")
        calls = message.get("tool_calls") or []
        if not isinstance(calls, list) or len(calls) > 8:
            raise ValueError("invalid_tool_calls")
        old_ids = {c["id"] for m in state["messages"] for c in m.get("tool_calls", [])}
        for call in calls:
            if not isinstance(call, dict) or not isinstance(call.get("function"), dict):
                raise TypeError("invalid_tool_call")
            if (
                not isinstance(call.get("id"), str)
                or not call["id"]
                or call["id"] in old_ids
                or call.get("type") != "function"
            ):
                raise ValueError("duplicate_or_invalid_tool_call")
            old_ids.add(call["id"])
            if call["function"]["name"] not in {
                tool["function"]["name"] for tool in TOOLS
            }:
                raise ValueError("unknown_tool")
            if not isinstance(json.loads(call["function"]["arguments"]), dict):
                raise TypeError("invalid_tool_arguments")
        if not calls and not isinstance(message.get("content"), str):
            raise ValueError("missing_final_text")

    def _tools(self, thread, state):
        assistant = next(
            m for m in reversed(state["messages"]) if m["role"] == "assistant"
        )
        for call in assistant["tool_calls"]:
            if any(m.get("tool_call_id") == call["id"] for m in state["messages"]):
                continue
            request = call["function"]
            self.registry.authorize(self.session, self.snapshot)
            result = self.store.receipt(thread, call["id"], request)
            if result is None:
                try:
                    result = self._tool(
                        thread, state, request["name"], json.loads(request["arguments"])
                    )
                except ValueError as exc:
                    result = {"error": str(exc)}
                self.store.record_receipt(thread, call["id"], request, result)
                if self.after_receipt:
                    self.after_receipt(call, result)
            # Registry failure here deliberately leaves a durable receipt but no
            # checkpoint advance. Resume reconciles it; the business call is not replayed.
            self.registry.authorize(self.session, self.snapshot)
            if (
                request["name"] in {"load_skill", "read_skill_file"}
                and "skill_id" in result
                and result["skill_id"] not in state["selected"]
            ):
                state["selected"].append(result["skill_id"])
            state["messages"].append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": canonical(result),
                }
            )
            state["checkpoint"] += 1
            self.store.save(thread, state)

    def _tool(self, thread, state, name, args):
        if name in {"load_skill", "read_skill_file"}:
            required = {"skill_id"} if name == "load_skill" else {"skill_id", "path"}
            if set(args) != required:
                raise ValueError("invalid_tool_arguments")
            member = next(
                (item for item in self.catalog if item["id"] == args["skill_id"]), None
            )
            if member is None:
                raise ValueError("skill_not_in_locked_catalog")
            path = "SKILL.md" if name == "load_skill" else args["path"]
            if (
                not isinstance(path, str)
                or path.startswith("/")
                or any(p in {"", ".", ".."} for p in path.split("/"))
            ):
                raise ValueError("invalid_skill_path")
            content = self.registry.read_skill_file(self.snapshot, member["mount_path"] + path)
            if content is None:
                raise ValueError("skill_file_not_found")
            if args["skill_id"] not in state["selected"]:
                state["selected"].append(args["skill_id"])
            self.store.event(
                thread,
                "child_selection",
                {"skill_id": args["skill_id"], "lock_digest": digest(state["lock"])},
            )
            self.store.event(
                thread,
                "file_read",
                {
                    "skill_id": args["skill_id"],
                    "path": path,
                    "content_digest": digest(content),
                },
            )
            return {"skill_id": args["skill_id"], "path": path, "content": content}
        if (
            name == "issue_refund"
            and (set(args) != {"amount"} or type(args["amount"]) is not int)
        ) or (name != "issue_refund" and args):
            raise ValueError("invalid_tool_arguments")
        body = {"action": name, "order_id": state["case"]["order_id"], **args}
        # Stable across threads and call IDs. Reusing a key with another amount
        # is a conflict, not a second refund. Business policy remains server-side.
        operation = digest({"order_id": body["order_id"], "action": name})
        response = self.business.post(
            "operations", json=body, headers={"Idempotency-Key": operation}
        )
        response.raise_for_status()
        result = response.json()
        if result.get("operation_id") != operation or result.get(
            "request_digest"
        ) != digest(body):
            raise Paused("business_receipt_identity_mismatch")
        self.store.event(
            thread,
            "tool_result",
            {"operation_id": operation, "receipt_digest": digest(result)},
        )
        return result

    @staticmethod
    def _result(state, status, reason=None):
        return {
            "schema": 1,
            "evidence_class": "synthetic fixture",
            "status": status,
            "reason": reason,
            "checkpoint": state["checkpoint"],
            "session_id": (state["session"] or {}).get("session_id"),
            "lock_digest": digest(state["lock"]) if state["lock"] else None,
            "context_digest": state["lock"]["identity"]["digest"]
            if state["lock"]
            else None,
            "version_id": state["lock"]["identity"]["version_id"]
            if state["lock"]
            else None,
            "selected": state["selected"],
            "output": state["messages"][-1].get("content")
            if status == "completed"
            else None,
        }
