"""Journey: a worker whose limits nothing holds keeps back the cases that rely on it, and says so by name.

Through the API the run dialog uses, in order: read the pairing's readiness, start a run of only the cases that rely
on the blocked worker (refused, naming the worker and why), then start the whole suite (the other cases run, and the
run records the worker as the reason the rest were held back).
"""

import pytest

from simulate.models import HostedHarnessJob
from simulate.tests.test_harness_affected_scenarios import (
    LOOKUP,
    LOOKUP_KEYS,
    REFUND,
    REFUND_KEYS,
    _doc,
    _suite,
)
from simulate.tests.test_harness_readiness import (
    BASE,
    _built,
    _certify,
    _environment,
)
from simulate.tests.test_harness_readiness import (  # noqa: F401 - fixture
    readiness_on as readiness_on,
)
from simulate.tests.test_harness_readiness import (  # noqa: F401 - fixture
    storage as storage,
)

pytestmark = pytest.mark.django_db

# Two workers wired separately with their own allow-lists, sealed without the world door's worker limits.
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


@pytest.fixture
def refund_worker_blocked(user, workspace, storage, monkeypatch):
    """Four refund cases give the refund worker a budget; six lookups rely on no worker."""
    monkeypatch.setattr(
        "simulate.services.harness_usage.require_harness_call_usage",
        lambda *args, **kwargs: None,
    )
    environment = _environment(user, workspace, storage, "worker-hold")
    _suite(
        environment,
        [
            _doc(
                key,
                solution=(LOOKUP, REFUND),
                sub_goals=("refunded",),
                worker_budget_seconds={"refund": 30},
            )
            for key in REFUND_KEYS
        ]
        + [_doc(key, solution=(LOOKUP,)) for key in LOOKUP_KEYS],
    )
    _certify(environment, storage)
    _built(environment, contract={"agent": "support", "workers": WORKERS})
    return environment


def _run(auth_client, environment, keys, key):
    return auth_client.post(
        f"{BASE}/{environment.id}/run/",
        {"scenario_ids": keys, "trials": 1},
        format="json",
        HTTP_IDEMPOTENCY_KEY=key,
    )


def test_a_blocked_worker_holds_back_its_cases_and_every_refusal_names_it(
    auth_client, refund_worker_blocked
):
    environment = refund_worker_blocked

    # 1. Readiness: the refund worker is blocked and holds back its four cases; the lookups are eligible.
    report = auth_client.get(f"{BASE}/{environment.id}/capabilities/").json()
    refund = next(one for one in report["workers"] if one["worker_id"] == "refund")
    assert refund["status"] == "blocked"
    assert report["selection"]["held_by_workers"] == {"refund": len(REFUND_KEYS)}
    assert report["selection"]["eligible"] == len(LOOKUP_KEYS)

    # 2. A run of only the refund cases is refused, and the refusal names the worker and why.
    refused = _run(auth_client, environment, REFUND_KEYS, "worker-hold-refunds")
    assert refused.status_code == 409, refused.content
    assert refused.json()["error"] == "all_cases_blocked"
    assert f"(Refund worker: {refund['reason']})" in refused.json()["message"]

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
    assert chosen["reasons"] == {"worker:refund": refund["reason"]}
