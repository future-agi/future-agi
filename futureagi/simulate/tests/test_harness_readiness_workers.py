"""Run readiness per declared worker: each tool Allowed, Blocked or Not enforced, never Supported; and the sub-agent
evidence row read from the agent's own runs."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from simulate.services.harness_readiness import evidence as evidence_module
from simulate.services.harness_readiness import workers
from simulate.services.harness_readiness.evidence import Evidence
from simulate.tests.test_harness_readiness import storage as storage  # noqa: F401

pytestmark = pytest.mark.usefixtures("readiness_on")

CONTRACT = {
    "workers": [
        {
            "id": "order_lookup",
            "name": "Order-lookup worker",
            "tools": ["get_order", "list_refunds", "update_order_status"],
            "allowed_tools": ["get_order", "list_refunds"],
            "binding": "separate",
            "address_env": "ORDER_LOOKUP_API_URL",
        },
        {
            "id": "refund",
            "name": "Refund worker",
            "tools": ["list_refunds", "issue_refund"],
            "allowed_tools": ["list_refunds", "issue_refund"],
            "binding": "separate",
            "address_env": "REFUND_API_URL",
        },
    ],
}
DOOR = {"worker_deadlines": "per_worker"}
# What ALK seals with the bundle: its door decision, each worker's limits, and each world action's tool.
DOOR_OUTCOME = {"present": True, "surface": "tool_proxy"}
WORKER_LIMITS = {
    "workers": {"order_lookup": {"limited": True}, "refund": {"limited": True}},
    "scenarios": {},
}
ACTIONS = {
    "POST orders.get": "get_order",
    "POST refunds.list": "list_refunds",
    "POST orders.update_status": "update_order_status",
    "POST refunds.issue": "issue_refund",
}
PERMISSIONS = {
    "worker:order_lookup": {"allow": ["POST orders.get", "POST refunds.list"]},
    "worker:refund": {"allow": ["POST refunds.issue", "POST refunds.list"]},
}


def _pairing(contract=CONTRACT):
    return SimpleNamespace(
        environment_version=SimpleNamespace(documents={"contract": contract})
    )


def _evidence(**fields):
    return Evidence(
        source="pair_certificate",
        checks={},
        tools=[],
        actions=[],
        checked_at="2026-11-14T10:00:00+00:00",
        **{
            "door": DOOR,
            "worker_permissions": PERMISSIONS,
            "door_outcome": DOOR_OUTCOME,
            "worker_limits": WORKER_LIMITS,
            "actions_to_tools": ACTIONS,
            **fields,
        },
    )


# The real reader, kept before the module's fixture stubs it for the row tests.
_READ_BYPASSES = workers._bypasses


@pytest.fixture(autouse=True)
def no_bypasses(monkeypatch):
    monkeypatch.setattr(workers, "_bypasses", lambda _pairing, _runs: {})


def _rows(pairing=None, evidence=None):
    return {
        one["worker_id"]: one
        for one in workers.worker_boundaries(
            pairing or _pairing(), evidence or _evidence(), []
        )
    }


def _boundaries(row):
    return {one["tool"]: one["boundary"] for one in row["tools"]}


def test_separate_worker_on_a_new_seal_reads_partial_with_allowed_and_blocked_tools():
    lookup = _rows()["order_lookup"]
    assert (lookup["status"], lookup["reason_code"]) == (
        "partial",
        "held_at_world_cooperative",
    )
    assert _boundaries(lookup) == {
        "get_order": "allowed",
        "list_refunds": "allowed",
        "update_order_status": "blocked",
    }
    # No rule ever yields Supported: the limit is cooperative.
    assert all(one["status"] != "supported" for one in _rows().values())


def test_shared_worker_reads_unavailable_not_enforced():
    shared = {
        **CONTRACT,
        "workers": [
            {**one, "binding": "shared", "allowed_tools": []}
            for one in CONTRACT["workers"]
        ],
    }
    # Even with an allow-list sealed for it, a shared worker's calls carry no channel, so nothing is held.
    row = _rows(_pairing(shared), _evidence())["refund"]
    assert (row["status"], row["reason_code"]) == ("unavailable", "shared_binding")
    assert set(_boundaries(row).values()) == {"not_enforced"}


def test_a_bundle_sealed_before_alk_wrote_its_door_decision_reads_sealed_before_worker_limits():
    old = _evidence(
        door=None, door_outcome=None, worker_limits=None, actions_to_tools=None
    )
    row = _rows(evidence=old)["order_lookup"]
    assert (row["status"], row["reason_code"]) == (
        "blocked",
        "sealed_before_worker_limits",
    )
    assert row["fix"]["type"] == "rebuild_environment"
    assert set(_boundaries(row).values()) == {"not_enforced"}


@pytest.mark.parametrize(
    ("reason", "code"),
    [
        ("tool_service_not_fronted", "no_world_door"),
        ("in_process_unbound", "in_process_tools"),
        ("no_tool_service", "in_process_tools"),
    ],
)
def test_a_build_alk_sealed_without_a_door_reads_its_reason_not_that_it_is_old(
    reason, code
):
    no_door = _evidence(door_outcome={"present": False, "reason": reason})
    row = _rows(evidence=no_door)["order_lookup"]
    assert (row["status"], row["reason_code"]) == ("blocked", code)
    assert row["fix"]["type"] == "rebuild_environment"
    assert set(_boundaries(row).values()) == {"not_enforced"}


def test_a_worker_alk_seals_as_unlimited_is_unavailable_not_blocked_where_no_door_stands():
    unlimited = {"id": "notes", "name": "Notes worker", "tools": ["get_order"]}
    contract = {**CONTRACT, "workers": [*CONTRACT["workers"], unlimited]}
    limits = {
        **WORKER_LIMITS,
        "workers": {**WORKER_LIMITS["workers"], "notes": {"limited": False}},
    }
    rows = _rows(
        _pairing(contract),
        _evidence(
            door_outcome={"present": False, "reason": "in_process_unbound"},
            worker_limits=limits,
        ),
    )
    # Nothing holds the limits a case about a limited worker relies on, so such a case is held back.
    assert rows["order_lookup"]["status"] == "blocked"
    assert (rows["notes"]["status"], rows["notes"]["fix"]) == ("unavailable", None)


def test_each_tool_takes_its_world_action_from_the_sealed_actions_file():
    # The tool proxy sealed no action for update_order_status: it has no route, so nothing holds it.
    actions = {k: v for k, v in ACTIONS.items() if v != "update_order_status"}
    lookup = _rows(evidence=_evidence(actions_to_tools=actions))["order_lookup"]
    assert {one["tool"]: one["action"] for one in lookup["tools"]} == {
        "get_order": "POST orders.get",
        "list_refunds": "POST refunds.list",
        "update_order_status": None,
    }
    assert _boundaries(lookup)["update_order_status"] == "not_enforced"


def test_a_bypass_in_a_run_blocks_that_worker_with_a_fix(monkeypatch):
    seen = [{"run_id": "r1", "call_execution_id": "c1", "tool": None}]
    monkeypatch.setattr(workers, "_bypasses", lambda _pairing, _runs: {"refund": seen})
    rows = _rows()
    assert (rows["refund"]["status"], rows["refund"]["reason_code"]) == (
        "blocked",
        "worker_channel_bypassed",
    )
    assert rows["refund"]["bypassed"] == seen
    assert rows["refund"]["fix"]["type"] == "rebind_worker"
    assert "REFUND_API_URL" in rows["refund"]["fix"]["action"]
    assert rows["order_lookup"]["status"] == "partial"


@pytest.mark.django_db
def test_a_bypass_with_another_agent_version_does_not_block(
    user, workspace, storage, django_capture_on_commit_callbacks
):
    """A run of agent v1 whose call says the refund worker bypassed its channel blocks the refund worker for v1's
    pairing only: the pairing of v2 never reads v1's runs."""
    from simulate.models import CallExecution
    from simulate.services.hosted_harness_ingestion import TRIAL_STATE_KEY
    from simulate.tests import test_harness_readiness as base

    environment, a2 = base._with_a2(user, workspace, storage, "rw-bypass-agent")
    base._certify(environment, storage)
    run = base._committed_run(environment, django_capture_on_commit_callbacks)
    notice = {"code": workers.BYPASS_CODE, "workers": ["refund"]}
    calls = CallExecution.objects.filter(test_execution_id=run.test_execution_id)
    assert calls.exists()
    calls.update(call_metadata={TRIAL_STATE_KEY: {"notices": [notice]}})

    v1 = base._pairing(environment)
    assert set(_READ_BYPASSES(v1, evidence_module._runs(v1))) == {"refund"}
    v2 = base._pairing(environment, a2)
    assert _READ_BYPASSES(v2, evidence_module._runs(v2)) == {}


def test_an_unreadable_archive_reads_untested():
    row = _rows(evidence=_evidence(unreadable=True))["refund"]
    assert (row["status"], row["reason_code"]) == ("untested", "evidence_unreadable")


def _archive_members(files):
    """The sealed members as the real fill packs them: only the manifest's listed paths go under the bundle root."""
    from simulate.services.harness_versioning import assembly

    return assembly._filled_files(
        files,
        [
            "door.json",
            "generated/tool-proxy/door.json",
            "generated/tool-proxy/permissions.json",
        ],
    )


@pytest.mark.django_db
def test_cached_files_without_door_keys_are_read_once_more(monkeypatch):
    from django.core.cache import cache

    from simulate.services.harness_versioning import assembly

    members = _archive_members(
        {
            "bundle/generated/tool-proxy/door.json": json.dumps(DOOR).encode(),
            "bundle/generated/tool-proxy/permissions.json": json.dumps(
                PERMISSIONS
            ).encode(),
            "bundle/door.json": json.dumps(DOOR_OUTCOME).encode(),
        }
    )
    assert assembly.DOOR_FILE_PATH in members
    reads = []

    def stream_members(object_key, names, _limit):
        reads.append(object_key)
        return {name: members[name] for name in names if name in members}

    monkeypatch.setattr(evidence_module.archives, "stream_members", stream_members)
    cache.clear()
    # A read cached before the door files were read, under the old key: never used again.
    old_key = (
        "harness-readiness-certification:"
        + evidence_module.hashlib.sha256(b"k1").hexdigest()
    )
    cache.set(old_key, ({name: [] for name in evidence_module._FILES}, True))
    saved = []

    class _Rows:
        def filter(self, **_kwargs):
            return self

        def update(self, **fields):
            saved.append(fields["certification_files"])

    class _Bundle:
        _base_manager = _Rows()

        def __init__(self):
            self.id = "b1"
            self.certification_files = {"object_key": "k1", "tools": []}

    monkeypatch.setattr(evidence_module, "HarnessAssembledBundle", _Bundle)
    row = _Bundle()
    files = evidence_module._certification_files(row, "k1")

    assert reads == ["k1"]
    assert files["door"] == DOOR
    assert files["worker_permissions"] == PERMISSIONS
    assert files["door_outcome"] == DOOR_OUTCOME
    # A bundle sealed before ALK wrote these files reads them as absent, never as an error.
    assert files["worker_limits"] is None
    assert saved[0]["files_version"] == assembly.CERTIFICATION_FILES_VERSION
    # Kept on the row now: the second read touches no archive.
    row.certification_files = saved[0]
    again = evidence_module._certification_files(row, "k1")
    assert reads == ["k1"]
    assert again["door"] == DOOR


class _Calls(list):
    def filter(self, **_kwargs):
        return self

    def order_by(self, *_fields):
        return self


def _call(record, trial=None, receipt=None):
    metadata = {"harness_workers": record}
    if trial is not None:
        metadata["harness_trial_state"] = trial
    if receipt is not None:
        metadata["hosted_harness_receipt"] = receipt
    return SimpleNamespace(
        call_metadata=metadata, created_at=datetime(2026, 11, 14, tzinfo=UTC)
    )


def _children(
    monkeypatch,
    calls,
    contract=CONTRACT,
    runs=("00000000-0000-4000-8000-000000000001",),
):
    from simulate.models import CallExecution

    monkeypatch.setattr(CallExecution, "objects", _Calls(calls))
    monkeypatch.setattr(evidence_module, "_contract", lambda _pairing: contract)
    return evidence_module._children(_pairing(contract), list(runs))


_EVENTS = [
    {"type": "delegated", "delegation_id": "d1", "worker_id": "r/order_lookup"},
    {"type": "tool_call", "worker_id": "r/order_lookup", "attribution": "reported"},
    {"type": "returned", "delegation_id": "d1", "worker_id": "r/order_lookup"},
    {"type": "delegated", "delegation_id": "d2", "worker_id": "r/refund"},
    {"type": "returned", "delegation_id": "d2", "worker_id": "r/refund"},
]


def _seen(*ids):
    return [
        {"id": one, "worker_id": f"r/{one}", "observed": True, "state": "completed"}
        for one in ids
    ]


@pytest.mark.django_db
def test_children_passed_when_every_declared_worker_is_seen(monkeypatch):
    reading = _children(
        monkeypatch,
        [
            _call(
                {"workers": _seen("order_lookup", "refund"), "events": _EVENTS},
                trial={
                    "state_changes": [
                        {"who": "worker:refund", "committed_actions": ["issue_refund"]}
                    ]
                },
                receipt={"sub_goals": [{"subject": "worker:refund", "held": True}]},
            )
        ],
    )
    assert reading.finding == "passed"
    checks = {one["check"]: one["result"] for one in reading.evidence}
    assert len(checks) == 7
    assert checks["delegation_and_return"] == "seen"
    assert checks["permissions_exercised"] == "seen"
    assert checks["worker_verdicts"] == "seen"
    assert checks["timeout_or_cancel"] == "not_seen"


@pytest.mark.django_db
def test_children_limited_when_a_declared_worker_is_never_seen(monkeypatch):
    reading = _children(
        monkeypatch,
        [
            _call(
                {
                    "workers": [
                        *_seen("order_lookup"),
                        {"id": "refund", "observed": False},
                    ],
                    "events": _EVENTS[:3],
                }
            )
        ],
    )
    assert (reading.finding, reading.reason_code) == (
        "limited",
        "workers_partly_observed",
    )
    assert reading.reason.startswith("1 of 2")


@pytest.mark.django_db
def test_children_not_applicable_for_a_single_agent_and_not_run_without_runs(
    monkeypatch,
):
    single = _children(monkeypatch, [], contract={"workers": []}, runs=())
    assert (single.finding, single.reason_code) == ("not_applicable", "single_agent")
    waiting = _children(monkeypatch, [], runs=())
    assert (waiting.finding, waiting.reason_code) == ("not_run", "no_run")


def test_the_workers_a_scenario_relies_on_come_from_the_sealed_limits_when_the_bundle_has_them():
    from simulate.services import harness_scenario_needs

    document = {
        "name": "refund_over_limit",
        "sub_goals": [],
        "worker_budget_seconds": {"order_lookup": 5},
    }

    def relied(sealed):
        return harness_scenario_needs._needs(
            "refund_over_limit", 1, document, document, {}, False, frozenset(), sealed
        ).workers

    # The bundle names the workers: the scenario's own fields are not read again.
    assert relied(frozenset({"refund"})) == {"refund"}
    assert relied(frozenset()) == frozenset()
    # Sealed before ALK wrote them: read from the scenario, as before.
    assert relied(None) == {"order_lookup"}
