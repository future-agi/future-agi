"""Journey: the cases ALK holds on worker limits are held back by name, through the API the run dialog uses."""

from unittest.mock import patch

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

    # A door that stands seals each worker's allow-list and each world action's tool beside it.
    proxy = (
        {
            assembly.PERMISSIONS_FILE_PATH: {
                "worker:order_lookup": {"allow": ["POST orders.lookup"]},
                "worker:refund": {"allow": ["POST refunds.issue"]},
            },
            assembly.ACTIONS_FILE_PATH: {
                "POST orders.lookup": LOOKUP,
                "POST refunds.issue": REFUND,
            },
        }
        if door.get("present")
        else {}
    )
    return {
        **proxy,
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
    with patch("simulate.temporal.client.start_hosted_harness_gateway_workflow"):
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
    with django_capture_on_commit_callbacks(execute=True):
        started = _run(auth_client, environment, LOOKUP_KEYS, "worker-bypass-run")
    assert started.status_code in (200, 201, 202), started.content
    run = HostedHarnessJob.no_workspace_objects.filter(environment=environment).latest(
        "created_at"
    )
    # One call of the run carries ALK's notice.
    bypassing = CallExecution.objects.filter(
        test_execution_id=run.test_execution_id
    ).first()
    CallExecution.objects.filter(id=bypassing.id).update(
        call_metadata={TRIAL_STATE_KEY: metadata[TRIAL_STATE_KEY]}
    )

    report = auth_client.get(f"{BASE}/{environment.id}/capabilities/").json()

    refund = next(one for one in report["workers"] if one["worker_id"] == "refund")
    assert (refund["status"], refund["bypassed_count"]) == ("blocked", 1)
    held = {one["id"]: one["count"] for one in report["selection"]["held_by"]}
    assert held == {"worker:refund": len(REFUND_KEYS)}
    assert report["selection"]["eligible"] == len(LOOKUP_KEYS)
    # Only the bypassing worker reads Blocked; the other keeps its own reading.
    lookup = next(
        one for one in report["workers"] if one["worker_id"] == "order_lookup"
    )
    assert lookup["status"] != "blocked"
    held_by = next(
        one for one in report["selection"]["held_by"] if one["id"] == "worker:refund"
    )
    assert held_by["name"] == "Refund worker"
    assert held_by["reason"] == refund["reason"]
    assert refund["fix"]["action"].startswith("Make Refund worker's tool client use")

    # Before any run: a run of only the cases relying on it is refused and creates nothing.
    runs = HostedHarnessJob.no_workspace_objects.filter(environment=environment)
    before = runs.count()
    refused = _run(auth_client, environment, REFUND_KEYS, "worker-bypass-refunds")
    assert refused.status_code == 409, refused.content
    assert refused.json()["error"] == "all_cases_blocked"
    assert "Refund worker" in refused.json()["message"]
    assert runs.count() == before

    # The whole suite runs the lookups and records the refunds as held back by the worker.
    with django_capture_on_commit_callbacks(execute=True):
        started = _run(
            auth_client, environment, REFUND_KEYS + LOOKUP_KEYS, "worker-bypass-all"
        )
    assert started.status_code in (200, 201, 202), started.content
    chosen = runs.latest("created_at").payload["metadata"]["readiness_selection"]
    assert chosen["held_back"]["by"] == {"worker:refund": len(REFUND_KEYS)}
    assert chosen["reasons"] == {"worker:refund": refund["reason"]}


def test_a_bypass_older_than_the_newest_calls_still_blocks_its_worker(
    auth_client,
    organization,
    user,
    workspace,
    storage,
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    """A trial flags a worker's bypass, then many more calls of the agent's runs come after it (the returns app's live
    matrix): the worker still reads Blocked, since any recent run of the pairing saw it bypass."""
    environment = _environment_with_workers(
        user,
        workspace,
        storage,
        monkeypatch,
        "worker-bypass-old",
        _sealed(
            {
                "present": True,
                "surface": "tool_proxy",
                "worker_deadlines": "per_worker",
            },
            held=False,
        ),
    )
    with django_capture_on_commit_callbacks(execute=True):
        started = _run(auth_client, environment, LOOKUP_KEYS, "worker-bypass-old-run")
    assert started.status_code in (200, 201, 202), started.content
    run = HostedHarnessJob.no_workspace_objects.filter(environment=environment).latest(
        "created_at"
    )
    calls = list(CallExecution.objects.filter(test_execution_id=run.test_execution_id))
    flagged = calls[0]
    CallExecution.objects.filter(id=flagged.id).update(
        call_metadata={
            TRIAL_STATE_KEY: {
                "notices": [
                    {
                        "code": "worker_channel_bypassed",
                        "message": "Refund worker used the app's connection.",
                        "workers": ["refund"],
                    }
                ]
            }
        }
    )
    # Sixty later calls of the same run, none of them flagged.
    template = calls[-1]
    for _ in range(60):
        CallExecution.objects.create(
            test_execution_id=template.test_execution_id,
            scenario_id=template.scenario_id,
            status=template.status,
            simulation_call_type=template.simulation_call_type,
            call_metadata={},
        )

    report = auth_client.get(f"{BASE}/{environment.id}/capabilities/").json()

    refund = next(one for one in report["workers"] if one["worker_id"] == "refund")
    assert (refund["status"], refund["bypassed_count"]) == ("blocked", 1)
    assert refund["bypassed"] == [
        {"run_id": str(run.test_execution_id), "call_execution_id": str(flagged.id)}
    ]
