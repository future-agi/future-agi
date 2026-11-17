"""Sample events for test deliveries and the preview pane (DESIGN.md sections 8.2 and 14; K5).

A test carries ``"test": true`` in the signed envelope and only reserved ids: the nil UUID for prompt, version and
label ids, ``dep-0000000000000000`` for a deployment, and all-zero run, thread and trace ids, so a receiver that
fetches them gets 404 and a "label moved to Production" test cannot start a real release. Names come only from
objects inside the endpoint's own filters, prefixed "[Test] " by the core's builders; otherwise they are the
documented samples below, so a prompt-filtered endpoint never receives another prompt's name.

The preview renders the same shapes without ``test`` and with sample ids: what a real event of that type looks like.
"""

from __future__ import annotations

import time

from django.conf import settings

from webhooks.core import ids
from webhooks.core.payloads import BUILDERS, NIL_UUID, TEST_DEPLOYMENT_ID, TEST_PREFIX

SAMPLE_PROMPT = "support-reply"
SAMPLE_LABEL = "Production"
SAMPLE_DEPLOYMENT = "support-agent"
SAMPLE_UUID = "6c0e2a51-0000-4000-8000-000000000001"
SAMPLE_DEPLOYMENT_ID = "dep-3f9a0c1d2e4b5a69"
ZERO_RUN = "run_" + "0" * 26
ZERO_THREAD = "th_" + "0" * 26


def _base(name: str) -> str:
    """An absolute base URL: links in events must be absolute (section 14.1). FRONTEND_BASE_URL is empty on an install
    that sets neither FRONTEND_URL nor APP_URL; the API's BASE_URL is then the only absolute base there is."""
    value = (getattr(settings, name, "") or "").rstrip("/")
    return value or (getattr(settings, "BASE_URL", "") or "").rstrip("/")


def names_from_filters(organization_id, subscriptions, event_type: str) -> dict:
    """The first prompt, label and deployment named by the endpoint's own filters for ``event_type``."""
    from webhooks.core.fanout import type_matches

    out: dict = {}
    for s in subscriptions:
        if not type_matches(s["event_type"], event_type):
            continue
        f = s.get("filters") or {}
        if f.get("prompt_ids") and "prompt" not in out:
            from model_hub.models.run_prompt import PromptTemplate

            name = (
                PromptTemplate.all_objects.filter(
                    id__in=[p for p in f["prompt_ids"] if _is_uuid(p)],
                    organization_id=organization_id,
                    deleted=False,
                )
                .order_by("name")
                .values_list("name", flat=True)
                .first()
            )
            if name:
                out["prompt"] = name
        if f.get("label_names") and "label" not in out:
            out["label"] = f["label_names"][0]
    return out


def _is_uuid(value: str) -> bool:
    import uuid

    try:
        uuid.UUID(str(value))
    except ValueError:
        return False
    return True


def _prompt_input(event_type: str, names: dict, test: bool, at: str) -> dict:
    pid = NIL_UUID if test else SAMPLE_UUID
    links = {
        "app": f"{_base('FRONTEND_BASE_URL')}/dashboard/prompt/{pid}",
        "api": f"{_base('BASE_URL')}/model-hub/prompt-templates/{pid}/",
    }
    prompt = {"id": pid, "name": names.get("prompt", SAMPLE_PROMPT)}
    person = {"id": NIL_UUID if test else SAMPLE_UUID, "name": "Asha Rao"}
    if event_type == "prompt.version.created":
        return {
            "prompt": prompt,
            "version": {
                "id": pid,
                "version": "v7",
                "commit_message": "Tighten the refund wording",
                "is_default": False,
                "labels": [],
                "created_at": at,
                "created_by": person,
            },
            "previous_version": {"id": pid, "version": "v6"},
            "links": links,
        }
    return {
        "cause": "label_change",
        "prompt": prompt,
        "label": {
            "id": pid,
            "name": names.get("label", SAMPLE_LABEL),
            "type": "system",
        },
        "revision": 12,
        "from_version": {"id": pid, "version": "v6"},
        "to_version": {
            "id": pid,
            "version": "v7",
            "commit_message": "Tighten the refund wording",
        },
        "changed_at": at,
        "changed_by": person,
        "links": links,
    }


def _run_data(event_type: str, names: dict, test: bool, at_ms: int) -> dict:
    dep = TEST_DEPLOYMENT_ID if test else SAMPLE_DEPLOYMENT_ID
    run_id = ZERO_RUN if test else "run_01M5HHZX3JB8F0RFCVFWQ4MV41"
    failed = event_type == "deployment.run.failed"
    app = _base("FRONTEND_BASE_URL")
    return {
        "deployment": {
            "id": dep,
            "name": (TEST_PREFIX if test else "")
            + names.get("deployment", SAMPLE_DEPLOYMENT),
            "environment": "production",
            "revision": 4,
        },
        "run": {
            "id": run_id,
            "thread_id": ZERO_THREAD if test else "th_01M5HHXJ6C3W72TF2BB9MQH1WP",
            "assistant_id": None,
            "workflow_id": SAMPLE_DEPLOYMENT,
            "status": "failed" if failed else "succeeded",
            "attempt": 1,
            "trigger": {"kind": "api", "cron_id": None},
            "created_at": ids.iso_ms(at_ms - 6940),
            "started_at": ids.iso_ms(at_ms - 6830),
            "ended_at": ids.iso_ms(at_ms),
            "duration_ms": 6830,
            "queue_ms": 110,
            "supersteps": 9,
            "error": {"type": "ModelError", "code": "model_error"} if failed else None,
            "context": None,
            "verified_by_trace": False,
        },
        "usage": {
            "input_tokens": 1834,
            "output_tokens": 402,
            "total_tokens": 2236,
            "model_calls": 3,
            "source": "runtime",
        },
        "cost": None,
        "trace": None,
        "links": {
            "deployment": f"{app}/dashboard/deployments/{dep}",
            "trace": None,
            "run_api": None,
        },
    }


def sample_event(
    event_type: str,
    *,
    organization_id,
    workspace_id,
    test: bool,
    names: dict | None = None,
    event_id: str | None = None,
) -> dict:
    """``{id, data, builder_input, timestamp}`` (stamped now) for :func:`webhooks.publish`-shaped rows. Prompt events keep
    a builder input (their body depends on the endpoint's payload variant); run events carry their final data."""
    names = names or {}
    now_ms = int(time.time() * 1000)
    eid = event_id or ids.new_id("evt_test" if test else "evt", now_ms)
    org = str(organization_id)
    ws = None if workspace_id is None else str(workspace_id)
    if event_type in BUILDERS:
        inp = _prompt_input(event_type, names, test, ids.iso_ms(now_ms))
        thin = BUILDERS[event_type](
            {
                **inp,
                "event_id": eid,
                "organization_id": org,
                "workspace_id": ws,
                "test": test,
            },
            {"payload_mode": "thin", "include_people_names": True},
        )
        return {
            "id": eid,
            "data": thin["data"],
            "builder_input": inp,
            "timestamp": thin["timestamp"],
        }
    data = _run_data(event_type, names, test, now_ms)
    return {
        "id": eid,
        "data": data,
        "builder_input": None,
        "timestamp": data["run"]["ended_at"],
    }
