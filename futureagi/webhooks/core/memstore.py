"""An in-process :class:`~webhooks.core.ports.Store` and :class:`~webhooks.core.ports.Queue`.

It runs the same core rules as the hosted ORM store (fan-out, sequences, the backlog cap, leases with fencing
tokens, ``worker_lost`` and ``superseded``), under one lock instead of row locks. The core tests drive the dispatcher
with it, and an embedded build can start from it.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from . import ids, schedule
from .fanout import facts_from_data, match, plan_delivery
from .payloads import variant_body
from .ports import Claim, EndpointConfig

FINAL = frozenset({"succeeded", "failed", "skipped", "cancelled"})
log = logging.getLogger("webhooks.fanout")


@dataclass
class MemEndpoint:
    config: EndpointConfig
    workspace_id: str | None
    subscriptions: list[dict] = field(default_factory=list)
    payload_mode: str = "thin"
    include_people_names: bool = True
    max_in_flight: int = 4
    next_seq: int = 1
    paused_count: int = 0
    in_flight: int = 0
    lane: str = "normal"
    disabled_reason: str | None = None


class MemQueue:
    def __init__(self) -> None:
        self._ev = threading.Event()

    def kick(self) -> None:
        self._ev.set()

    def wait(self, timeout_s: float) -> bool:
        got = self._ev.wait(timeout_s)
        self._ev.clear()
        return got


class MemStore:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.endpoints: dict[str, MemEndpoint] = {}
        self.events: dict[str, dict] = {}
        self.payloads: dict[tuple, bytes] = {}
        self.deliveries: dict[str, dict] = {}
        self.attempts: list[dict] = []
        self.source_keys: dict[tuple[str, str], tuple[str, str]] = {}

    # ---- producer side -----------------------------------------------------------------------------------------

    def publish(
        self,
        *,
        organization_id: str,
        event: Mapping[str, Any],
        source_key: str,
        source_hash: str,
    ) -> str:
        """Insert an event once per (org, source_key); the same key with another hash is refused."""
        with self.lock:
            k = (organization_id, source_key)
            if k in self.source_keys:
                eid, h = self.source_keys[k]
                if h != source_hash:
                    raise ValueError("source_key reused with a different source_hash")
                return eid
            eid = event["id"]
            self.events[eid] = {
                **event,
                "organization_id": organization_id,
                "fanout_state": "pending",
            }
            self.source_keys[k] = (eid, source_hash)
            return eid

    # ---- Store port --------------------------------------------------------------------------------------------

    def fan_out(self, now_ms: int, limit: int) -> int:
        """Each event on its own: everything it needs (facts, matches, bodies) is computed before anything changes,
        so an event that cannot be fanned out (malformed ``data``, no builder, a body over 64 KB) is marked
        ``failed`` and changes nothing, and the events after it go on (R12)."""
        with self.lock:
            pending = [
                e
                for e in self.events.values()
                if e["fanout_state"] == "pending"
                and e.get("not_before_ms", 0) <= now_ms
            ][:limit]
            for ev in pending:
                try:
                    planned = self._plan_event(ev)
                except Exception as exc:  # noqa: BLE001 - one poisoned event never stops the others
                    ev["fanout_state"] = "failed"
                    ev["fanout_error"] = type(exc).__name__
                    log.error(
                        "webhooks event could not be fanned out",
                        extra={
                            "metric": "webhooks.fanout.failed",
                            "event_id": ev["id"],
                            "error_type": type(exc).__name__,
                        },
                    )
                    continue
                self._apply_event(ev, planned, now_ms)
            return len(pending)

    def _plan_event(self, ev: dict) -> list[tuple[str, bool, tuple, bytes | None]]:
        facts = {**ev, **facts_from_data(ev["type"], ev["data"])}
        out = []
        for ep_id in sorted(self.endpoints):
            ep = self.endpoints[ep_id]
            if ep.config.organization_id != ev["organization_id"]:
                continue
            m = match(
                {
                    "id": ep_id,
                    "workspace_id": ep.workspace_id,
                    "subscriptions": ep.subscriptions,
                },
                facts,
            )
            if not m.deliver:
                continue
            vkey = (ev["id"], ep.payload_mode, "v1", ep.include_people_names)
            body = None
            if vkey not in self.payloads:
                body = variant_body(
                    event_id=ev["id"],
                    type_=ev["type"],
                    timestamp=ev["timestamp"],
                    organization_id=ev["organization_id"],
                    workspace_id=ev["workspace_id"],
                    data=ev["data"],
                    builder_input=ev.get("builder_input"),
                    variant={
                        "payload_mode": ep.payload_mode,
                        "include_people_names": ep.include_people_names,
                    },
                    test=bool(ev.get("test")),
                )
            out.append((ep_id, m.targeted, vkey, body))
        return out

    def _apply_event(self, ev: dict, planned: list, now_ms: int) -> None:
        for ep_id, targeted, vkey, body in planned:
            ep = self.endpoints[ep_id]
            if body is not None:
                self.payloads.setdefault(vkey, body)
            p = plan_delivery(
                ep.config.status, ep.next_seq, ep.paused_count, bool(ev.get("test"))
            )
            ep.next_seq, ep.paused_count = p.next_seq, p.paused_count
            did = ids.new_id("dlv", now_ms)
            self.deliveries[did] = {
                "id": did,
                "event_id": ev["id"],
                "endpoint_id": ep_id,
                "payload": vkey,
                "seq": p.seq,
                "targeted": targeted,
                "status": p.status,
                "skip_reason": p.skip_reason,
                "attempts_count": 0,
                "next_attempt_at_ms": now_ms,
                "lease_token": None,
                "lease_until_ms": None,
                "is_test": bool(ev.get("test")),
                "created_at_ms": now_ms,
                "history": [],
                "retry_class": None,
                "final_outcome": None,
                "trigger": "auto",
            }
        ev["fanout_state"] = "done"

    def _claim(self, d: dict, now_ms: int, n: int) -> Claim:
        ep = self.endpoints[d["endpoint_id"]]
        ev = self.events[d["event_id"]]
        d["lease_token"] = str(uuid.uuid4())
        d["lease_until_ms"] = now_ms + schedule.lease_ms(ep.config.timeout_ms)
        d["status"] = "sending"
        return Claim(
            delivery_id=d["id"],
            lease_token=d["lease_token"],
            lease_until_ms=d["lease_until_ms"],
            event_id=ev["id"],
            event_type=ev["type"],
            seq=d["seq"],
            is_test=d["is_test"],
            attempt_n=n,
            trigger=d["trigger"],
            created_at_ms=d["created_at_ms"],
            body=self.payloads[d["payload"]],
            endpoint=ep.config,
            history=tuple(d["history"]),
        )

    def reap_expired(self, now_ms: int) -> list[Claim]:
        with self.lock:
            out = []
            for d in self.deliveries.values():
                if (
                    d["status"] == "sending"
                    and d["lease_until_ms"] is not None
                    and d["lease_until_ms"] < now_ms
                ):
                    # The lost attempt is attempt n itself; the reaper writes its worker_lost row and settles it.
                    out.append(self._claim(d, now_ms, d["attempts_count"]))
            return out

    def claim_due(
        self,
        now_ms: int,
        lane: str,
        slots: int,
        per_org: int,
        org_busy: Mapping[str, int] | None = None,
    ) -> list[Claim]:
        with self.lock:
            due = sorted(
                (
                    d
                    for d in self.deliveries.values()
                    if d["status"] in ("pending", "retrying")
                    and d["next_attempt_at_ms"] <= now_ms
                    and self.endpoints[d["endpoint_id"]].lane == lane
                ),
                key=lambda d: (d["next_attempt_at_ms"], d["id"]),
            )
            out: list[Claim] = []
            per: dict[str, int] = dict(org_busy or {})
            for d in due:
                if len(out) >= slots:
                    break
                ep = self.endpoints[d["endpoint_id"]]
                org = ep.config.organization_id
                if ep.config.status != "enabled" and not d["is_test"]:
                    # Not sent while the endpoint is off; it keeps its sequence number (section 7.7).
                    d["status"] = "paused"
                    ep.paused_count += 1
                    continue
                if per.get(org, 0) >= per_org or ep.in_flight >= ep.max_in_flight:
                    continue
                ep.in_flight += 1
                per[org] = per.get(org, 0) + 1
                d["attempts_count"] += 1
                out.append(self._claim(d, now_ms, d["attempts_count"]))
            return out

    def renew(self, delivery_id: str, lease_token: str, lease_until_ms: int) -> bool:
        with self.lock:
            d = self.deliveries[delivery_id]
            if d["lease_token"] != lease_token:
                return False
            d["lease_until_ms"] = lease_until_ms
            return True

    def settle(self, claim: Claim, attempt: Mapping, action: Mapping) -> bool:
        with self.lock:
            d = self.deliveries[claim.delivery_id]
            row = {**attempt, "delivery_id": d["id"]}
            if d["lease_token"] != claim.lease_token:
                row["outcome"] = "superseded"
                row["observed_outcome"] = attempt.get("outcome")
                self.attempts.append(row)
                return False
            ep = self.endpoints[d["endpoint_id"]]
            ep.in_flight -= 1
            if action["action"] == "block_by_policy":
                # Nothing was sent: no attempt row, and the attempt count is given back (section 6.3).
                d["attempts_count"] -= 1
                d["lease_token"], d["lease_until_ms"] = None, None
                d["status"] = "paused"
                ep.paused_count += 1
                self._turn_off(ep, action["disabled_status"], action["disabled_reason"])
                return True
            self.attempts.append(row)
            answer = {k: attempt[k] for k in ("settled_at_ms",)}
            if attempt.get("response_status") is not None:
                answer["status"] = attempt["response_status"]
                if attempt.get("retry_after") is not None:
                    answer["retry_after"] = attempt["retry_after"]
            else:
                answer["error"] = attempt["outcome"]
            d["history"].append(answer)
            d["lease_token"], d["lease_until_ms"] = None, None
            d["status"] = action["delivery_status"]
            d["retry_class"] = action.get("retry_class")
            d["final_outcome"] = action.get("final_outcome")
            if action["action"] == "retry":
                d["next_attempt_at_ms"] = action["retry_at_ms"]
            if action["delivery_status"] == "paused":
                ep.paused_count += 1
            if action["action"] == "disable_endpoint":
                self._turn_off(ep, "disabled_auto", action.get("disabled_reason"))
            return True

    def _turn_off(self, ep: MemEndpoint, status: str, reason: str | None) -> None:
        """Only an enabled endpoint changes; its due backlog becomes ``paused`` (section 7.7)."""
        if ep.config.status != "enabled":
            return
        ep.config = replace(ep.config, status=status)
        ep.disabled_reason = reason
        for d in self.deliveries.values():
            if (
                d["endpoint_id"] == ep.config.id
                and d["status"] in ("pending", "retrying")
                and not d["is_test"]
            ):
                d["status"] = "paused"
                ep.paused_count += 1

    def release(self, claim: Claim, retry_at_ms: int, reason: str) -> bool:
        with self.lock:
            d = self.deliveries[claim.delivery_id]
            if d["lease_token"] != claim.lease_token:
                return False
            ep = self.endpoints[d["endpoint_id"]]
            ep.in_flight -= 1
            d["attempts_count"] -= 1
            d["lease_token"], d["lease_until_ms"] = None, None
            if retry_at_ms - d["created_at_ms"] > ep.config.max_age_s * 1000:
                d["status"], d["final_outcome"] = "failed", "max_age_exceeded"
            else:
                d["status"] = "retrying" if d["attempts_count"] else "pending"
                d["next_attempt_at_ms"] = retry_at_ms
            d["release_reason"] = reason
            return True
