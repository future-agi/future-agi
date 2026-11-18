"""Journey: the cases ALK holds on worker limits are held back by name, through the API the run dialog uses."""

import pytest

from simulate.models import CallExecution, HostedHarnessJob
from simulate.services.harness_versioning import assembly
from simulate.services.hosted_harness_ingestion import TRIAL_STATE_KEY
from simulate.tests.test_harness_affected_scenarios import (
    LOOKUP,
    LOOKUP_KEYS,
    REFUND,
    REFUND_KEYS,
    _doc,
    _suite,
)
from simulate.tests.test_harness_call_actors import _ingest
from simulate.tests.test_harness_readiness import (
    BASE,
    _built,
    _certify,
    _committed_run,
    _environment,
    _report,
)

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("readiness_on", "storage"),
]

# Two workers wired separately with their own allow-lists.
WORKERS = [
    {
        "id": "order_lookup",
        "name": "Order-lookup worker",
        "tools": [LOOKUP],
        "allowed_tools": [LOOKUP],
        "binding": "separate",
        "address_env": "ORDER_LOOKUP_API_URL",
    },
    {
        "id": "refund",
        "name": "Refund worker",
        "tools": [REFUND],
        "allowed_tools": [REFUND],
        "binding": "separate",
        "address_env": "REFUND_API_URL",
    },
]


def _sealed(door, *, held):
    """ALK's root `door.json` and `worker-limits.json`: each refund case relies on the refund worker."""

    def entry(relies):
        hold = held and relies
        return {
            "workers": ["refund"] if relies else [],
            "relies_on_worker_limits": relies,
            "held": hold,
            "unheld_workers": ["refund"] if hold else [],
            "reason": "worker_limits_not_enforced" if hold else None,
            "cause": "tool_service_not_fronted" if hold else None,
            "limit": "all" if hold else None,
            "deadline": None,
        }

    return {
        assembly.DOOR_OUTCOME_PATH: door,
        assembly.WORKER_LIMITS_PATH: {
            "workers": {one["id"]: {"limited": True} for one in WORKERS},
            "scenarios": {
                **{key: entry(True) for key in REFUND_KEYS},
                **{key: entry(False) for key in LOOKUP_KEYS},
            },
        },
    }


def _environment_with_workers(user, workspace, storage, monkeypatch, key, sealed):
    monkeypatch.setattr(
        "simulate.services.harness_usage.require_harness_call_usage",
        lambda *args, **kwargs: None,
    )
    environment = _environment(user, workspace, storage, key)
    _suite(
        environment,
        [
            _doc(
                one,
                solution=(LOOKUP, REFUND),
                sub_goals=("refunded",),
                worker_budget_seconds={"refund": 30},
            )
            for one in REFUND_KEYS
        ]
        + [_doc(one, solution=(LOOKUP,)) for one in LOOKUP_KEYS],
    )
    _certify(environment, storage, sealed=sealed)
    _built(environment, contract={"agent": "support", "workers": WORKERS})
    return environment


def _run(auth_client, environment, keys, key):
    return auth_client.post(
        f"{BASE}/{environment.id}/run/",
        {"scenario_ids": keys, "trials": 1},
        format="json",
        HTTP_IDEMPOTENCY_KEY=key,
    )


def test_the_cases_alk_holds_are_held_back_and_every_refusal_names_the_worker(
    auth_client, user, workspace, storage, monkeypatch
):
    environment = _environment_with_workers(
        user,
        workspace,
        storage,
        monkeypatch,
        "worker-hold",
        _sealed({"present": False, "reason": "tool_service_not_fronted"}, held=True),
    )

    # 1. Readiness: the refund worker holds back its four cases; the lookups are eligible.
    report = auth_client.get(f"{BASE}/{environment.id}/capabilities/").json()
    held = {one["id"]: one for one in report["selection"]["held_by"]}
    assert {key: one["count"] for key, one in held.items()} == {
        "worker:refund": len(REFUND_KEYS)
    }
    blocked_workers = sum(1 for one in report["workers"] if one["status"] == "blocked")
    assert report["blockers"] == report["counts"]["blocked"] + blocked_workers
    assert report["answer"]["kind"] == "blocked"
    assert report["selection"]["eligible"] == len(LOOKUP_KEYS)
    # Only a worker holds cases back: no readiness area is named as blocking them.
    assert report["selection"]["blocking_area"] is None
    reason = held["worker:refund"]["reason"]
    assert reason

    # 2. A run of only the refund cases is refused, naming the worker and why.
    refused = _run(auth_client, environment, REFUND_KEYS, "worker-hold-refunds")
    assert refused.status_code == 409, refused.content
    assert refused.json()["error"] == "all_cases_blocked"
    assert f"(Refund worker: {reason})" in refused.json()["message"]

    # 3. The whole suite runs the lookups and records the worker as the reason the refunds were held back.
    started = _run(
        auth_client, environment, REFUND_KEYS + LOOKUP_KEYS, "worker-hold-all"
    )
    assert started.status_code in (200, 201, 202), started.content
    run = HostedHarnessJob.no_workspace_objects.filter(environment=environment).latest(
        "created_at"
    )
    chosen = run.payload["metadata"]["readiness_selection"]
    assert (chosen["requested"], chosen["running"]) == (
        len(REFUND_KEYS) + len(LOOKUP_KEYS),
        len(LOOKUP_KEYS),
    )
    assert chosen["held_back"]["by"] == {"worker:refund": len(REFUND_KEYS)}
    assert chosen["reasons"] == {"worker:refund": reason}


def test_a_bypass_alk_reports_holds_the_cases_relying_on_that_worker(
    auth_client,
    organization,
    user,
    workspace,
    storage,
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    environment = _environment_with_workers(
        user,
        workspace,
        storage,
        monkeypatch,
        "worker-bypass",
        _sealed(
            {
                "present": True,
                "surface": "tool_proxy",
                "worker_deadlines": "per_worker",
            },
            held=False,
        ),
    )
    assert _report(environment)["blockers"] == _report(environment)["counts"]["blocked"]

    # ALK's notice, as ingestion keeps it on the call.
    metadata: dict = {}
    _ingest(
        organization,
        workspace,
        "worker-bypass-call",
        CallExecution.SimulationCallType.TEXT,
        {
            "messages": [],
            "notices": [
                {
                    "code": "worker_channel_bypassed",
                    "message": "Refund worker reached the world without its own channel.",
                    "workers": ["refund"],
                }
            ],
        },
        metadata,
    )
    run = _committed_run(environment, django_capture_on_commit_callbacks)
    CallExecution.objects.filter(test_execution_id=run.test_execution_id).update(
        call_metadata={TRIAL_STATE_KEY: metadata[TRIAL_STATE_KEY]}
    )

    report = auth_client.get(f"{BASE}/{environment.id}/capabilities/").json()

    refund = next(one for one in report["workers"] if one["worker_id"] == "refund")
    assert (refund["status"], refund["bypassed_count"]) == ("blocked", 1)
    held = {one["id"]: one["count"] for one in report["selection"]["held_by"]}
    assert held == {"worker:refund": len(REFUND_KEYS)}
    assert report["selection"]["eligible"] == len(LOOKUP_KEYS)
