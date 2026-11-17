"""Subscription matching, run targeting, the per-endpoint sequence and the paused backlog cap (DESIGN.md sections
7.6, 7.7 and 14; B8-EVENT-CONTRACT.md section 5.5; conformance ``payloads.json`` ``fan_out``).

The store adapter locks each endpoint's state row and calls :func:`plan_delivery`; :func:`fan_out` is the same rule
over plain dicts (the conformance scenario and an in-process store).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

BACKLOG_CAP = 10_000
DEFAULT_FAILED_STATUSES = ("failed", "interrupted")
MAX_SUBSCRIPTIONS = 50
CATALOG = (
    "prompt.version.created",
    "prompt.label.changed",
    "deployment.run.completed",
    "deployment.run.failed",
)
GROUPS = ("prompt.*", "deployment.run.*")


def type_matches(subscription_type: str, event_type: str) -> bool:
    return subscription_type == event_type or (
        subscription_type.endswith(".*")
        and event_type.startswith(subscription_type[:-1])
    )


def facts_from_data(event_type: str, data: Mapping[str, Any]) -> dict:
    """The filter facts of an event, read from its ``data`` object."""
    if event_type.startswith("prompt."):
        out = {"prompt_id": data["prompt"]["id"]}
        if event_type == "prompt.label.changed":
            out.update(label_name=data["label"]["name"], action=data["action"])
        return out
    if event_type.startswith("deployment.run."):
        return {
            "deployment_id": data["deployment"]["id"],
            "environment": data["deployment"]["environment"],
            "trigger_kind": data["run"]["trigger"]["kind"],
            "status": data["run"]["status"],
        }
    return {}


def filters_pass(
    ev: Mapping[str, Any], f: Mapping[str, Any], skip_deployment: bool = False
) -> bool:
    """``ev`` holds ``type`` and the facts. The deployment filter is skipped for a run-targeted endpoint."""
    t = ev["type"]
    if t.startswith("prompt."):
        pid = ev["prompt_id"]
        if f.get("prompt_ids") and pid not in f["prompt_ids"]:
            return False
        if pid in f.get("exclude_prompt_ids", ()):
            return False
        if t == "prompt.label.changed":
            if f.get("label_names") and ev["label_name"] not in f["label_names"]:
                return False
            if f.get("actions") and ev["action"] not in f["actions"]:
                return False
        return True
    if t.startswith("deployment.run."):
        if (
            not skip_deployment
            and f.get("deployment_ids")
            and ev["deployment_id"] not in f["deployment_ids"]
        ):
            return False
        if f.get("environments") and ev["environment"] not in f["environments"]:
            return False
        if f.get("trigger_kinds") and ev["trigger_kind"] not in f["trigger_kinds"]:
            return False
        if t == "deployment.run.failed" and ev["status"] not in f.get(
            "statuses", DEFAULT_FAILED_STATUSES
        ):
            return False
        return True
    return True


@dataclass(frozen=True)
class Match:
    deliver: bool
    targeted: bool = False


def match(endpoint: Mapping[str, Any], ev: Mapping[str, Any]) -> Match:
    """Whether ``endpoint`` (``id``, ``workspace_id``, ``subscriptions``) gets the event ``ev`` (``type``,
    ``workspace_id``, facts, ``targeted_endpoint_id``, ``test``, ``test_endpoint_id``).

    An org-wide endpoint (workspace null) sees every workspace. A test goes only to its endpoint. A run-targeted
    endpoint needs no subscription; when it has subscriptions for the type, their filters apply minus the deployment
    filter."""
    if ev.get("test"):
        return Match(ev.get("test_endpoint_id") == endpoint["id"])
    if (
        endpoint["workspace_id"] is not None
        and ev["workspace_id"] != endpoint["workspace_id"]
    ):
        return Match(False)
    subs = [
        s
        for s in endpoint.get("subscriptions", ())
        if type_matches(s["event_type"], ev["type"])
    ]
    if any(filters_pass(ev, s.get("filters") or {}) for s in subs):
        return Match(True)
    if ev.get("targeted_endpoint_id") == endpoint["id"]:
        ok = not subs or any(
            filters_pass(ev, s.get("filters") or {}, skip_deployment=True) for s in subs
        )
        return Match(ok, ok)
    return Match(False)


@dataclass(frozen=True)
class Planned:
    seq: int | None
    status: str
    skip_reason: str | None
    next_seq: int
    paused_count: int


def plan_delivery(
    endpoint_status: str, next_seq: int, paused_count: int, test: bool
) -> Planned:
    """One delivery under the endpoint state's row lock: a sequence number (none for tests), then pending, paused, or
    skipped with ``backlog_cap`` once 10,000 are paused. Paused and capped deliveries take numbers too."""
    if test:
        return Planned(None, "pending", None, next_seq, paused_count)
    if endpoint_status == "enabled":
        return Planned(next_seq, "pending", None, next_seq + 1, paused_count)
    if paused_count < BACKLOG_CAP:
        return Planned(next_seq, "paused", None, next_seq + 1, paused_count + 1)
    return Planned(next_seq, "skipped", "backlog_cap", next_seq + 1, paused_count)


def fan_out(
    events: Sequence[Mapping[str, Any]], endpoints: Iterable[Mapping[str, Any]]
) -> list[dict]:
    """Events in the order the dispatcher accepted them -> their deliveries; endpoints are taken in id order."""
    state = {}
    for e in endpoints:
        state[e["id"]] = {
            **e,
            "next_seq": e.get("next_seq", 1),
            "paused_count": e.get("paused_count", 0),
        }
    out = []
    for ev in events:
        dels = []
        for ep_id in sorted(state):
            e = state[ep_id]
            m = match(e, ev)
            if not m.deliver:
                continue
            p = plan_delivery(
                e["status"], e["next_seq"], e["paused_count"], bool(ev.get("test"))
            )
            e["next_seq"], e["paused_count"] = p.next_seq, p.paused_count
            d: dict = {"endpoint_id": ep_id, "seq": p.seq, "status": p.status}
            if p.skip_reason:
                d["skip_reason"] = p.skip_reason
            d["targeted"] = m.targeted
            dels.append(d)
        out.append({"event_id": ev["id"], "deliveries": dels})
    return out
