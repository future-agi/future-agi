"""Fan-out and the per-endpoint sequence (DESIGN.md sections 7.6, 7.7, 14; contract section 5.5)."""

from __future__ import annotations

BACKLOG_CAP = 10_000

DEFAULT_FAILED_STATUSES = ["failed", "interrupted"]


def _type_matches(sub: str, type_: str) -> bool:
    return sub == type_ or (sub.endswith(".*") and type_.startswith(sub[:-1]))


def _filters_pass(ev: dict, f: dict, skip_deployment: bool = False) -> bool:
    t = ev["type"]
    if t.startswith("prompt."):
        pid = ev["prompt_id"]
        if f.get("prompt_ids") and pid not in f["prompt_ids"]:
            return False
        if pid in f.get("exclude_prompt_ids", []):
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


def fan_out(case: dict) -> list[dict]:
    """Events in the order the dispatcher accepted them -> deliveries per event, with sequences."""
    eps = {e["id"]: dict(e) for e in case["endpoints"]}
    for e in eps.values():
        e.setdefault("next_seq", 1)
        e.setdefault("paused_count", 0)
    out = []
    for ev in case["events"]:
        dels = []
        for ep_id in sorted(eps):
            e = eps[ep_id]
            if ev.get("test"):
                if ev.get("test_endpoint_id") != ep_id:
                    continue
                dels.append(
                    {
                        "endpoint_id": ep_id,
                        "seq": None,
                        "status": "pending",
                        "targeted": False,
                    }
                )
                continue
            if (
                ev["workspace_id"] != e["workspace_id"]
                and e["workspace_id"] is not None
            ):
                continue
            subscribed = any(
                _type_matches(s["event_type"], ev["type"])
                and _filters_pass(ev, s.get("filters", {}))
                for s in e.get("subscriptions", [])
            )
            targeted = False
            if not subscribed and ev.get("targeted_endpoint_id") == ep_id:
                # A targeted endpoint needs no subscription; when it has ones for this type, their filters still
                # apply except the deployment filter (contract section 5.5 item 4).
                subs = [
                    s
                    for s in e.get("subscriptions", [])
                    if _type_matches(s["event_type"], ev["type"])
                ]
                targeted = not subs or any(
                    _filters_pass(ev, s.get("filters", {}), skip_deployment=True)
                    for s in subs
                )
            if not (subscribed or targeted):
                continue
            seq = e["next_seq"]
            e["next_seq"] += 1
            if e["status"] == "enabled":
                st = {"status": "pending"}
            elif e["paused_count"] < BACKLOG_CAP:
                e["paused_count"] += 1
                st = {"status": "paused"}
            else:
                st = {"status": "skipped", "skip_reason": "backlog_cap"}
            dels.append({"endpoint_id": ep_id, "seq": seq, **st, "targeted": targeted})
        out.append({"event_id": ev["id"], "deliveries": dels})
    return out
