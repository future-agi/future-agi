"""Run readiness per declared worker, read from what ALK sealed: each worker row from the bundle root's `door.json`,
each case's hold from `worker-limits.json`, and the sub-agent evidence row from the worker record each call keeps."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from simulate.serializers.harness_environment import HarnessCapabilityReportSerializer
from simulate.services.harness_readiness import children as children_module
from simulate.services.harness_readiness import evidence as evidence_module
from simulate.services.harness_readiness import selection, workers
from simulate.services.harness_readiness.evidence import Evidence
from simulate.services.harness_readiness.report import snapshot_as_read
from simulate.services.harness_scenario_needs import CaseNeeds
from simulate.services.harness_versioning import assembly

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
# What ALK seals with the bundle, as it writes it.
TOOL_PROXY_DOOR = {
    "present": True,
    "surface": "tool_proxy",
    "worker_deadlines": "per_worker",
}
IN_PROCESS_DOOR = {"present": True, "surface": "in_process", "workers": ["refund"]}
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


def _scenario(*, held, workers=(), relies=None, cause=None, deadline=None, unheld=None):
    # `unheld`: ALK's sealed `unheld_workers`, empty when not held; None leaves it out, as an older seal does.
    sealed = {} if unheld is None and held else {"unheld_workers": []}
    if unheld is not None:
        sealed["unheld_workers"] = list(unheld)
    return {
        **sealed,
        "workers": list(workers),
        "relies_on_worker_limits": bool(workers) if relies is None else relies,
        "held": held,
        "reason": "worker_limits_not_enforced" if held else None,
        "cause": cause if held else None,
        "limit": "all" if held else None,
        "deadline": deadline,
    }


def _limits(scenarios=None, limited=True):
    return {
        "workers": {one["id"]: {"limited": limited} for one in CONTRACT["workers"]},
        "scenarios": scenarios or {},
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
            "worker_permissions": PERMISSIONS,
            "door_outcome": TOOL_PROXY_DOOR,
            "worker_limits": _limits(),
            "actions_to_tools": ACTIONS,
            **fields,
        },
    )


def _rows(pairing=None, evidence=None, calls=()):
    return {
        one["worker_id"]: one
        for one in workers.worker_boundaries(
            pairing or _pairing(), evidence or _evidence(), list(calls)
        )
    }


def _boundaries(row):
    return {one["tool"]: one["boundary"] for one in row["tools"]}


def _bypass_call(n, worker="refund"):
    return SimpleNamespace(
        id=f"c{n}",
        test_execution_id=f"r{n}",
        call_metadata={
            "harness_trial_state": {
                "notices": [{"code": workers.BYPASS_CODE, "workers": [worker]}]
            }
        },
    )


class TestWorkerRows:
    def test_a_tool_proxy_holds_a_separate_worker_to_its_sealed_allow_list(self):
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
        # The limit is cooperative: no worker ever reads Supported.
        assert all(one["status"] != "supported" for one in _rows().values())

    def test_an_in_process_door_holds_only_the_workers_it_lists(self):
        rows = _rows(
            evidence=_evidence(door_outcome=IN_PROCESS_DOOR, worker_permissions=None)
        )

        listed, unlisted = rows["refund"], rows["order_lookup"]
        assert (listed["status"], listed["reason_code"], listed["fix"]) == (
            "partial",
            "in_process_door",
            None,
        )
        # Not in the door's list: its deadline is not held either, so a limited worker reads Blocked.
        assert (unlisted["status"], unlisted["reason_code"], unlisted["fix"]) == (
            "blocked",
            "in_process_not_held",
            None,
        )
        for row in rows.values():
            assert set(_boundaries(row).values()) == {"not_enforced"}

    @pytest.mark.parametrize(
        "reason", ["tool_service_not_fronted", "in_process_unbound", "no_tool_service"]
    )
    def test_a_build_sealed_without_a_door_reads_alk_s_reason_and_offers_no_rebuild(
        self, reason
    ):
        row = _rows(
            evidence=_evidence(door_outcome={"present": False, "reason": reason})
        )["order_lookup"]

        assert (row["status"], row["reason_code"], row["fix"]) == (
            "blocked",
            reason,
            None,
        )
        assert set(_boundaries(row).values()) == {"not_enforced"}

    def test_no_root_door_file_reads_sealed_before_worker_limits_with_a_rebuild(self):
        old = _evidence(
            door_outcome=None,
            worker_limits=None,
            worker_permissions=None,
            actions_to_tools=None,
        )
        row = _rows(evidence=old)["order_lookup"]

        assert (row["status"], row["reason_code"]) == (
            "untested",
            "sealed_before_worker_limits",
        )
        assert row["fix"]["type"] == "rebuild_environment"
        assert set(_boundaries(row).values()) == {"not_enforced"}

    def test_worker_limits_sealed_without_a_door_file_offer_no_rebuild(self):
        row = _rows(evidence=_evidence(door_outcome=None))["order_lookup"]

        assert (row["status"], row["reason_code"], row["fix"]) == (
            "untested",
            "door_decision_not_sealed",
            None,
        )

    def test_a_shared_worker_alk_limits_is_blocked_and_a_rebuild_after_rebinding_fixes_it(
        self,
    ):
        shared = {
            "workers": [{**one, "binding": "shared"} for one in CONTRACT["workers"]]
        }
        rows = _rows(_pairing(shared))
        unlimited = _rows(
            _pairing(shared), _evidence(worker_limits=_limits(limited=False))
        )

        assert (rows["refund"]["status"], rows["refund"]["reason_code"]) == (
            "blocked",
            "shared_binding",
        )
        assert rows["refund"]["fix"]["type"] == "rebuild_environment"
        assert unlimited["refund"]["status"] == "unavailable"

    @pytest.mark.parametrize("field", ["unreadable", "door_unreadable"])
    def test_files_that_could_not_be_read_read_untested_never_held(self, field):
        row = _rows(evidence=_evidence(**{field: True}))["refund"]

        assert (row["status"], row["reason_code"]) == (
            "untested",
            "evidence_unreadable",
        )
        assert set(_boundaries(row).values()) == {"not_enforced"}

    def test_a_bypass_blocks_that_worker_with_a_rebind_and_counts_every_call(self):
        calls = [_bypass_call(n) for n in range(7)]

        rows = _rows(calls=calls)

        assert (rows["refund"]["status"], rows["refund"]["fix"]["type"]) == (
            "blocked",
            "rebind_worker",
        )
        assert (len(rows["refund"]["bypassed"]), rows["refund"]["bypassed_count"]) == (
            5,
            7,
        )
        assert rows["order_lookup"]["status"] == "partial"


def _case(key):
    return CaseNeeds(
        key=key,
        number=1,
        name=key,
        persona="",
        rows=frozenset({"start", "message"}),
        tools=frozenset(),
        unknown=False,
        recorded=True,
        grading_gap=None,
    )


def _placed(key, *, contract=CONTRACT, evidence=None, calls=()):
    pairing = _pairing(contract)
    evidence = evidence or _evidence()
    bypassed = frozenset(
        workers.bypassed_workers(
            workers.worker_boundaries(pairing, evidence, list(calls))
        )
    )
    return selection._place(
        _case(key),
        {},
        proved=True,
        voice=False,
        many=False,
        pair_validating=False,
        pair_failed=False,
        hold=workers.case_hold(workers.case_holds(pairing, evidence), key),
        bypassed=bypassed,
    )


class TestCaseHolds:
    """A case is held exactly when ALK's run would hold it: FA reads ALK's sealed answer, never decides it again."""

    @pytest.mark.parametrize(
        "sealed",
        [
            # A budget for the refund worker, on a door that cannot hold it.
            _scenario(
                held=True,
                workers=["refund"],
                cause="tool_service_not_fronted",
                unheld=["refund"],
            ),
            # A sub-goal about a limited worker, on a worker wired as shared.
            _scenario(
                held=True, workers=["refund"], cause="shared_worker", unheld=["refund"]
            ),
            # A sub-goal on an unlimited worker beside it is not one it is held by.
            _scenario(
                held=True,
                workers=["notes", "refund"],
                cause="shared_worker",
                unheld=["refund"],
            ),
        ],
    )
    def test_a_case_alk_seals_held_is_held_by_its_unheld_worker(self, sealed):
        evidence = _evidence(worker_limits=_limits({"refund-1": sealed}))

        placed = _placed("refund-1", evidence=evidence)

        assert (placed.bucket, placed.by) == ("blocked", ["worker:refund"])

    def test_a_held_case_sealed_before_alk_named_its_unheld_workers_is_held_by_worker_limits(
        self,
    ):
        sealed = _scenario(held=True, workers=["notes", "refund"], cause="in_process")
        evidence = _evidence(worker_limits=_limits({"refund-1": sealed}))

        placed = _placed("refund-1", evidence=evidence)

        assert (placed.bucket, placed.by) == ("blocked", [selection.WORKER_LIMITS_HELD])
        # Filed with the sealed cause, never under a worker the seal does not say is unheld.
        assert placed.hold.reason == workers._CAUSE_REASONS["in_process"]

    @pytest.mark.parametrize(
        "sealed",
        [
            _scenario(held=False, workers=["refund"]),
            # A sub-goal on a worker with no limits: nothing to hold.
            _scenario(held=False, workers=["notes"], relies=False),
            # The door inside the agent decides a deadline at run time: not held before it.
            _scenario(held=False, workers=["refund"], deadline="decided_at_run"),
        ],
    )
    def test_a_case_alk_seals_clear_runs(self, sealed):
        evidence = _evidence(worker_limits=_limits({"refund-1": sealed}))

        assert _placed("refund-1", evidence=evidence).bucket == "eligible"

    def test_a_case_missing_from_the_sealed_limits_is_not_known(self):
        evidence = _evidence(worker_limits=_limits({"other": _scenario(held=False)}))

        placed = _placed("refund-1", evidence=evidence)

        assert (placed.bucket, placed.by) == (
            "untested",
            [selection.WORKER_LIMITS_UNKNOWN],
        )

    @pytest.mark.parametrize(
        "fields",
        [{"worker_limits": None}, {"unreadable": True}, {"door_unreadable": True}],
    )
    def test_no_readable_sealed_limits_leave_no_case_eligible(self, fields):
        assert _placed("refund-1", evidence=_evidence(**fields)).bucket == "untested"

    def test_an_agent_declaring_no_workers_holds_nothing_on_worker_limits(self):
        placed = _placed(
            "refund-1", contract={"workers": []}, evidence=_evidence(worker_limits=None)
        )

        assert placed.bucket == "eligible"

    def test_a_case_relying_on_a_worker_that_bypassed_its_channel_is_held(self):
        evidence = _evidence(
            worker_limits=_limits(
                {
                    "refund-1": _scenario(held=False, workers=["refund"]),
                    "lookup-1": _scenario(held=False, workers=["order_lookup"]),
                }
            )
        )
        calls = [_bypass_call(1)]

        assert _placed("refund-1", evidence=evidence, calls=calls).by == [
            "worker:refund"
        ]
        assert _placed("lookup-1", evidence=evidence, calls=calls).bucket == "eligible"


class TestBackCompat:
    def test_a_malformed_door_file_reads_unreadable_not_absent(self):
        files = assembly.certification_files(
            {assembly.DOOR_OUTCOME_PATH: b"{not json"}, "k1"
        )
        absent = assembly.certification_files({}, "k1")

        assert (files["door_outcome"], files["door_unreadable"]) == (None, True)
        assert absent["door_unreadable"] is False

    @pytest.mark.django_db
    def test_a_row_kept_at_an_older_files_version_is_read_again_and_stamped(
        self, monkeypatch
    ):
        from django.core.cache import cache

        members = {
            assembly.DOOR_OUTCOME_PATH: json.dumps(TOOL_PROXY_DOOR).encode(),
            assembly.WORKER_LIMITS_PATH: json.dumps(_limits()).encode(),
        }
        reads = []

        def stream_members(object_key, names, _limit):
            reads.append(object_key)
            return {name: members[name] for name in names if name in members}

        monkeypatch.setattr(evidence_module.archives, "stream_members", stream_members)
        cache.clear()
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
                # Kept by a release before the door files were read.
                self.certification_files = {
                    "object_key": "k1",
                    "files_version": 2,
                    "tools": [],
                }

        monkeypatch.setattr(evidence_module, "HarnessAssembledBundle", _Bundle)
        row = _Bundle()

        files = evidence_module._certification_files(row, "k1")
        row.certification_files = saved[0]
        again = evidence_module._certification_files(row, "k1")

        assert reads == ["k1"]
        assert saved[0]["files_version"] == assembly.CERTIFICATION_FILES_VERSION
        assert files["door_outcome"] == again["door_outcome"] == TOOL_PROXY_DOOR
        assert files["worker_permissions"] is None

    def test_a_run_snapshot_kept_before_workers_serializes_with_none(self):
        old = {"counts": {"blocked": 2}, "rows": []}

        read = snapshot_as_read(old)

        assert (read["workers"], read["blockers"]) == ([], 2)
        assert HarnessCapabilityReportSerializer().fields["workers"].required


class _Calls(list):
    def filter(self, **_kwargs):
        return self

    def order_by(self, *_fields):
        return self


def _call(run, record, trial=None, receipt=None):
    metadata = {children_module.WORKERS_KEY: record}
    if trial is not None:
        metadata["harness_trial_state"] = trial
    if receipt is not None:
        metadata["hosted_harness_receipt"] = receipt
    return SimpleNamespace(
        test_execution_id=run,
        call_metadata=metadata,
        created_at=datetime(2026, 11, 14, tzinfo=UTC),
    )


def _worker(run, local, observed=True):
    # As the worker record keeps it: `worker_id` scoped to the run, `id` the contract's.
    return {
        "worker_id": f"{run}/{local}",
        "id": local,
        "name": local,
        "observed": observed,
        "undeclared": False,
        "instance_ids": [],
    }


def _children(monkeypatch, calls, contract=CONTRACT, runs=("r1",)):
    from simulate.models import CallExecution

    monkeypatch.setattr(CallExecution, "objects", _Calls(calls))
    return children_module.children(contract["workers"], list(runs))


def _checks(reading):
    return {one["check"]: one["result"] for one in reading.evidence}


class TestSubAgentEvidence:
    def test_a_transfer_is_a_whole_hand_off_without_a_return(self, monkeypatch):
        record = {
            "workers": [_worker("r1", "order_lookup"), _worker("r1", "refund")],
            "events": [
                {
                    "type": "transferred",
                    "worker_id": "r1/order_lookup",
                    "delegation_id": "t1",
                },
                {
                    "type": "transferred",
                    "worker_id": "r1/refund",
                    "delegation_id": "t2",
                },
            ],
        }

        reading = _children(monkeypatch, [_call("r1", record)])

        assert reading.finding == "passed"
        assert _checks(reading)["delegation_and_return"] == "seen"

    def test_a_worker_cut_short_is_still_seen_but_its_hand_off_is_not_whole(
        self, monkeypatch
    ):
        record = {
            "workers": [_worker("r1", "order_lookup"), _worker("r1", "refund")],
            "events": [
                {"type": "delegated", "worker_id": "r1/refund", "delegation_id": "d1"}
            ],
        }

        reading = _children(monkeypatch, [_call("r1", record)])

        assert (reading.finding, reading.reason_code) == (
            "limited",
            "workers_partly_observed",
        )
        assert _checks(reading)["delegation_and_return"] == "not_seen"

    def test_a_worker_stored_as_cut_short_is_seen_timed_out_or_cancelled(
        self, monkeypatch
    ):
        def record(state):
            return {"workers": [{**_worker("r1", "refund"), "state": state}]}

        cut = _children(monkeypatch, [_call("r1", record("deadline_passed"))])
        back = _children(monkeypatch, [_call("r1", record("completed"))])

        assert _checks(cut)["timeout_or_cancel"] == "seen"
        assert _checks(back)["timeout_or_cancel"] == "not_seen"

    def test_ids_are_compared_across_runs_by_the_contract_id(self, monkeypatch):
        def record(run):
            return {"workers": [_worker(run, "refund")], "events": []}

        across = _children(
            monkeypatch,
            [_call("r1", record("r1")), _call("r2", record("r2"))],
            runs=("r1", "r2"),
        )
        one_run = _children(
            monkeypatch, [_call("r1", record("r1")), _call("r1", record("r1"))]
        )

        assert _checks(across)["stable_ids"] == "seen"
        assert _checks(one_run)["stable_ids"] == "not_applicable"

    def test_a_single_agent_is_not_applicable_and_no_run_is_not_run(self, monkeypatch):
        single = _children(monkeypatch, [], contract={"workers": []}, runs=())
        waiting = _children(monkeypatch, [], runs=())

        assert single.finding == "not_applicable"
        assert waiting.finding == "not_run"
