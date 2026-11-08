import json
import uuid
from unittest.mock import patch

import pytest
from django.core.cache import cache

from simulate.models import (
    AgentVersion,
    HarnessAgentVersionRuntime,
    HarnessAssembledBundle,
    HarnessEnvironmentVersion,
    HostedHarnessJob,
)
from simulate.serializers.harness_environment import (
    HarnessCapabilityCasesQuerySerializer,
    HarnessCapabilityReportSerializer,
    HarnessCapabilitySelectionReportSerializer,
)
from simulate.services import harness_readiness
from simulate.services.agent_setup import store_agent
from simulate.services.harness_readiness import report as report_module
from simulate.services.harness_versioning import archives, assembly
from simulate.services.hosted_harness import HostedHarnessError
from simulate.tests.test_agent_setup_service import source_agent
from simulate.tests.test_harness_agent_versions import (
    _a1,
    _create,
)
from simulate.tests.test_harness_agent_versions import _environment as _agent_env
from simulate.tests.test_harness_agent_versions import (  # noqa: F401 - fixture
    scheduled as scheduled,
)
from simulate.tests.test_harness_versioning_assembly import _row, _sealed_authoring
from simulate.tests.test_harness_versioning_backfill import (
    _agent_version,
    _definition,
)
from simulate.tests.test_harness_versioning_backfill import (  # noqa: F401 - fixture
    storage as storage,
)
from simulate.tests.test_harness_versioning_runs import _run

pytestmark = pytest.mark.django_db

BASE = "/simulate/api/harness-environments"
PASSED = {
    "static": "passed",
    "processes": "passed",
    "chat_ingress": "passed",
    "schema_and_seed": "passed",
    "tool_contract": "2/2",
    "action_behavior": "2/2",
    "reset_equivalence": "passed",
    "world_isolation": "passed",
}
TOOLS = [
    {"tool": "lookup_order", "availability": "certified", "reason": ""},
    {"tool": "issue_refund", "availability": "certified", "reason": ""},
]
ACTIONS = [
    {"action": "lookup_order", "status": "passed", "reason": ""},
    {"action": "issue_refund", "status": "passed", "reason": ""},
]
PRODUCTION_ONLY = {
    "tool_entrypoints": [{"tool": "issue_refund", "mode": "in_process"}],
    "dependencies": [
        {"name": "payments", "engine": "", "reached": {}, "used_by": ["issue_refund"]}
    ],
}


@pytest.fixture(autouse=True)
def readiness_on(settings):
    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
    settings.HARNESS_PUBLIC_BASE_URL = "https://platform.example"
    cache.clear()


def _environment(user, workspace, storage, key):
    """Legacy A1 is the default; its pairing with E1 and S1 is certified."""
    environment = _agent_env(user, workspace, key, storage, body=_sealed_authoring())
    assert _row(environment).state == "ready"
    return environment


def _with_a2(user, workspace, storage, key, *, tools=()):
    """Also A2: checked, not the default, and a new prompt, so it runs bundles of its
    own. Its tools were read: ``tools``, or ``None`` for a version never read."""
    environment = _environment(user, workspace, storage, key)
    a2 = _create(_a1(environment), environment, secret_values={"OPENAI_API_KEY": "v2"})
    HarnessAgentVersionRuntime.no_workspace_objects.filter(agent_version=a2).update(
        preflight_status="passed", prompt_hash="sha256:" + "f" * 64
    )
    assert not assembly.same_bundle_agent(a2.id, _a1(environment).id, environment)
    if tools is not None:
        _read_tools(a2, list(tools))
    return environment, a2


def _read_tools(agent, tools):
    """Store ``agent``'s document as a build's understand stage does, with ``tools``."""
    document = source_agent(tools=tools)
    document["behaviour"][0]["tools"] = [tool["name"] for tool in tools]
    assert (
        store_agent(agent_version_id=agent.id, agent=document, sdk_version="0.2.3")
        == "stored"
    )


def _certify(
    environment,
    storage,
    *,
    agent=None,
    checks=None,
    tools=None,
    actions=None,
    pairing=None,
    warnings=(),
):
    """Make the pairing of ``agent`` (the default when absent) with the defaults, or
    ``pairing``, a certified bundle holding this certificate, these per-tool and
    per-action files and these warnings."""
    pairing = pairing or _pairing(environment, agent)
    key = assembly.bundle_key(
        pairing.runtime, pairing.environment_version, pairing.suite_version
    )
    object_key = f"harness-sealed/{environment.id}/{uuid.uuid4().hex}.tar.gz"
    storage.objects[object_key] = archives.pack_archive(
        {
            harness_readiness.TOOL_CERTIFICATION: json.dumps(
                {"tools": TOOLS if tools is None else tools}
            ).encode(),
            harness_readiness.ACTION_CERTIFICATION: json.dumps(
                {"actions": ACTIONS if actions is None else actions}
            ).encode(),
        }
    )
    HarnessAssembledBundle._base_manager.update_or_create(
        job_id=environment.id,
        assembly_key=key,
        defaults={
            "organization_id": environment.organization_id,
            "agent_version_id": pairing.agent_version.id,
            "environment_version_id": pairing.environment_version.id,
            "suite_version_id": pairing.suite_version.id,
            "state": "ready",
            "object_key": object_key,
            "certificate": {"checks": PASSED if checks is None else checks},
            "warnings": list(warnings),
            "error": None,
        },
    )


def _built(environment, *, checks=None, actions=None, contract=None):
    """Give the default environment version a build certificate."""
    version = environment.environment_version
    documents = dict(version.documents or {})
    documents["stage_outputs"] = [
        *[
            one
            for one in documents.get("stage_outputs") or []
            if one.get("kind") != "certification"
        ],
        {
            "kind": "certification",
            "data": {
                "certificate": {"checks": PASSED if checks is None else checks},
                "action_certification": {
                    "actions": ACTIONS if actions is None else actions
                },
            },
        },
    ]
    if contract is not None:
        documents["contract"] = contract
    HarnessEnvironmentVersion.no_workspace_objects.filter(id=version.id).update(
        documents=documents
    )
    environment.refresh_from_db()


def _pairing(environment, agent=None):
    return harness_readiness.resolve_pairing(
        environment, agent_version_id=agent.id if agent is not None else None
    )


def _report(environment, agent=None):
    return harness_readiness.readiness_report(_pairing(environment, agent))


def _rows(report):
    return {row["id"]: row for row in report["rows"]}


def _committed_run(environment, capture):
    """A run of the defaults, with what follows its commit done."""
    with capture(execute=True):
        run = _run(environment)
    run.refresh_from_db()
    return run


def _bundle(environment, agent=None):
    return harness_readiness.pair_bundle(_pairing(environment, agent))


class TestRows:
    def test_certified_pair_reads_supported_measured_rows(
        self, user, workspace, storage
    ):
        environment = _environment(user, workspace, storage, "rd-ready")
        _certify(environment, storage)

        report = _report(environment)

        statuses = {row["id"]: row["status"] for row in report["rows"]}
        measured = [row["id"] for row in report["rows"] if row["measured"]]
        assert measured == [
            "start",
            "message",
            "tool_discovery",
            "tool_routing",
            "state",
            "reset",
            "cancel",
        ]
        assert {statuses[one] for one in measured[:-1]} == {"supported"}
        assert (statuses["cancel"], _rows(report)["cancel"]["reason_code"]) == (
            "untested",
            "stop_not_observed",
        )
        assert report["pairing"]["evidence"] == "pair_certificate"
        assert report["answer"] == {
            "kind": "ready",
            "worst": "supported",
            "reason_code": None,
        }
        assert (statuses["continuity"], _rows(report)["continuity"]["reason_code"]) == (
            "untested",
            "no_check_yet",
        )

    def test_refused_probe_counts_as_passed(self, user, workspace, storage):
        environment = _environment(user, workspace, storage, "rd-refused")
        # Refusals alone, and no probe count: only the refusal can make it pass.
        _certify(
            environment,
            storage,
            checks={
                name: value
                for name, value in PASSED.items()
                if name != "action_behavior"
            },
            actions=[
                {"action": "issue_refund", "status": "refused", "reason": "policy"},
            ],
        )

        routing = _rows(_report(environment))["tool_routing"]

        assert (routing["status"], routing["targets"]) == ("supported", [])

    def test_failed_probe_blocks_tool_routing(self, user, workspace, storage):
        environment = _environment(user, workspace, storage, "rd-probe-failed")
        _certify(
            environment,
            storage,
            checks={**PASSED, "action_behavior": "1/2"},
            actions=[
                {"action": "lookup_order", "status": "passed", "reason": ""},
                {"action": "issue_refund", "status": "failed", "reason": "no world"},
            ],
        )

        routing = _rows(_report(environment))["tool_routing"]

        assert routing["status"] == "blocked"
        assert routing["targets"] == [
            {"kind": "action", "name": "issue_refund", "result": "failed"}
        ]
        assert routing["evidence"] == [{"check": "action_behavior", "result": "1/2"}]
        assert routing["fix"]["target"] == "issue_refund"

    def test_runtime_only_tool_is_partial(self, user, workspace, storage):
        environment = _environment(user, workspace, storage, "rd-runtime-only")
        _certify(
            environment,
            storage,
            tools=[
                {"tool": "lookup_order", "availability": "certified", "reason": ""},
                {"tool": "issue_refund", "availability": "runtime_only", "reason": ""},
            ],
        )

        rows = _rows(_report(environment))

        discovery = rows["tool_discovery"]
        assert (discovery["status"], discovery["reason_code"]) == (
            "partial",
            "runtime_only",
        )
        assert discovery["targets"] == [
            {"kind": "tool", "name": "issue_refund", "result": "runtime_only"}
        ]
        # Partial opens its gate: routing is still read.
        assert rows["tool_routing"]["status"] == "supported"

    def test_rejected_tool_blocks_discovery_and_gates_routing(
        self, user, workspace, storage
    ):
        environment = _environment(user, workspace, storage, "rd-rejected")
        _certify(
            environment,
            storage,
            checks={**PASSED, "tool_contract": "1/2"},
            tools=[
                {"tool": "lookup_order", "availability": "certified", "reason": ""},
                {"tool": "issue_refund", "availability": "rejected", "reason": ""},
            ],
        )

        rows = _rows(_report(environment))

        assert rows["tool_discovery"]["status"] == "blocked"
        assert (rows["tool_routing"]["status"], rows["tool_routing"]["blocked_by"]) == (
            "untested",
            "tool_discovery",
        )

    def test_not_applicable_lane_is_unavailable(self, user, workspace, storage):
        environment = _environment(user, workspace, storage, "rd-not-applicable")
        _certify(
            environment, storage, checks={**PASSED, "schema_and_seed": "not_applicable"}
        )

        rows = _rows(_report(environment))

        assert (rows["state"]["status"], rows["state"]["reason_code"]) == (
            "unavailable",
            "not_applicable",
        )
        assert rows["state"]["fix"] is None

    def test_reset_is_partial_for_external_agents(self, user, workspace, storage):
        environment, a2 = _with_a2(user, workspace, storage, "rd-external")
        HarnessAgentVersionRuntime.no_workspace_objects.filter(agent_version=a2).update(
            source_kind="provider"
        )
        _certify(environment, storage, agent=a2)

        reset = _rows(_report(environment, a2))["reset"]

        assert (reset["status"], reset["reason_code"]) == ("partial", "external_agent")
        _certify(environment, storage)
        assert _rows(_report(environment))["reset"]["status"] == "supported"

    def test_gate_closed_leaves_dependents_untested(self, user, workspace, storage):
        environment = _environment(user, workspace, storage, "rd-gate")
        _certify(environment, storage, checks={**PASSED, "processes": "failed"})

        rows = _rows(_report(environment))

        assert rows["start"]["status"] == "blocked"
        for one in ("message", "tool_discovery", "state"):
            assert (rows[one]["status"], rows[one]["blocked_by"]) == (
                "untested",
                "start",
            )
        assert rows["tool_routing"]["blocked_by"] == "tool_discovery"
        assert rows["reset"]["blocked_by"] == "state"

    def test_absent_chat_ingress_is_not_run(self, user, workspace, storage):
        environment = _environment(user, workspace, storage, "rd-no-ingress")
        checks = {
            name: value for name, value in PASSED.items() if name != "chat_ingress"
        }
        _certify(environment, storage, checks=checks)

        message = _rows(_report(environment))["message"]

        assert (message["status"], message["reason_code"]) == ("untested", "not_run")

    def test_a_certified_pairing_offers_no_recheck_that_cannot_change_a_row(
        self, user, workspace, storage, auth_client
    ):
        environment = _environment(user, workspace, storage, "rd-ready-not-run")
        _certify(
            environment,
            storage,
            checks={**PASSED, "action_behavior": "0/0"},
            actions=[{"action": "issue_refund", "status": "not_run", "reason": ""}],
        )
        jobs = HostedHarnessJob.no_workspace_objects.count()

        routing = _rows(_get(auth_client, environment.id).json())["tool_routing"]
        rechecked = _recheck(auth_client, environment.id).json()["validation"]

        # Checking the certified pairing again starts nothing, so it is not offered.
        assert (rechecked["state"], rechecked["job_id"]) == ("ready", None)
        assert HostedHarnessJob.no_workspace_objects.count() == jobs
        assert (routing["status"], routing["reason_code"]) == ("untested", "not_run")
        assert routing["fix"] == {
            "type": "tool_routing",
            "category": "tool_routing",
            "target": "issue_refund",
            "action": "Point issue_refund at the test world",
            "would": "issue_refund lands in the test world and its probe passes",
            "recheck": None,
        }

    def test_a_check_not_run_on_build_evidence_is_rechecked_on_the_pairing(
        self, user, workspace, storage
    ):
        environment = _environment(user, workspace, storage, "rd-build-not-run")
        HarnessAssembledBundle._base_manager.filter(id=_row(environment).id).update(
            state="pending", object_key=None
        )
        checks = {
            name: value for name, value in PASSED.items() if name != "chat_ingress"
        }
        _built(environment, checks=checks)

        message = _rows(_report(environment))["message"]

        assert (message["status"], message["reason_code"]) == ("untested", "not_run")
        assert message["fix"]["type"] == "recheck"
        assert message["fix"]["recheck"]["scope"] == "pair"

    def test_production_only_reads_blocked_in_nikhils_words(
        self, user, workspace, storage
    ):
        environment = _environment(user, workspace, storage, "rd-production")
        _certify(environment, storage)
        _built(environment, contract=PRODUCTION_ONLY)

        routing = _rows(_report(environment))["tool_routing"]

        assert routing["status"] == "blocked"
        assert routing["reason_code"] == "production_only"
        assert routing["reason"] == (
            "Unsupported for this controlled test: issue_refund reaches production"
        )
        assert routing["targets"] == [
            {"kind": "tool", "name": "issue_refund", "result": "failed"}
        ]
        assert routing["fix"]["action"] == "Point issue_refund at the test world"


class TestPairs:
    def test_failed_pair_blocks_named_rows(self, user, workspace, storage):
        environment = _environment(user, workspace, storage, "rd-failed-pair")
        _certify(environment, storage)
        HarnessAssembledBundle._base_manager.filter(id=_row(environment).id).update(
            state="failed",
            object_key=None,
            error={
                "code": "authoring_runtime_validation_failed",
                "message": "rejected",
                "source": "validation",
                "failed_actions": [{"action": "issue_refund", "reason": "live"}],
            },
        )
        _built(environment)

        report = _report(environment)

        routing = _rows(report)["tool_routing"]
        assert (routing["status"], routing["reason_code"]) == (
            "blocked",
            "action_failed",
        )
        assert routing["targets"] == [
            {"kind": "action", "name": "issue_refund", "result": "failed"}
        ]
        assert report["validation"]["state"] == "failed"
        assert report["validation"]["error"] == {
            "code": "authoring_runtime_validation_failed",
            "reason": "the agent did not pass this environment's checks"
            " (failed actions: issue_refund)",
            "failed_actions": [{"action": "issue_refund", "reason": "live"}],
        }
        assert report["answer"] == {
            "kind": "blocked",
            "worst": "blocked",
            "reason_code": None,
        }

    def test_failed_pair_naming_no_action_blocks_the_answer(
        self, user, workspace, storage
    ):
        environment = _environment(user, workspace, storage, "rd-failed-unnamed")
        HarnessAssembledBundle._base_manager.filter(id=_row(environment).id).update(
            state="failed",
            object_key=None,
            error={"code": "certify_incomplete", "source": "validation"},
        )
        _built(environment)

        report = _report(environment)

        assert _rows(report)["tool_routing"]["status"] == "supported"
        assert report["answer"]["kind"] == "blocked"
        assert report["answer"]["reason_code"] == "pair_failed"

    def test_ready_pair_clears_stale(self, user, workspace, storage):
        environment, a2 = _with_a2(user, workspace, storage, "rd-stale")
        _built(environment)

        stale = _report(environment, a2)

        assert stale["pairing"]["evidence"] == "build_certificate"
        routing = _rows(stale)["tool_routing"]
        assert (routing["status"], routing["was"], routing["stale_because"]) == (
            "stale",
            "supported",
            "agent_version",
        )
        assert stale["answer"]["kind"] == "stale"

        _certify(environment, storage, agent=a2)
        fresh = _report(environment, a2)

        assert fresh["pairing"]["evidence"] == "pair_certificate"
        assert {
            row["status"]
            for row in fresh["rows"]
            if row["measured"] and row["id"] != "cancel"
        } == {"supported"}

    def test_build_with_unknown_agent_is_untested(self, user, workspace, storage):
        environment, a2 = _with_a2(user, workspace, storage, "rd-unknown-agent")
        _built(environment)
        HarnessEnvironmentVersion.no_workspace_objects.filter(
            id=environment.environment_version_id
        ).update(agent_version=None)
        environment.refresh_from_db()

        routing = _rows(_report(environment, a2))["tool_routing"]

        assert (routing["status"], routing["reason_code"]) == (
            "untested",
            "built_with_unknown",
        )

    def test_preflight_never_goes_stale(self, user, workspace, storage):
        environment, a2 = _with_a2(user, workspace, storage, "rd-preflight")
        _built(environment)
        HarnessAgentVersionRuntime.no_workspace_objects.filter(agent_version=a2).update(
            preflight_status="failed",
            preflight_report={
                "checks": [
                    {
                        "id": "credentials_present",
                        "label": "Credentials",
                        "status": "failed",
                        "missing": ["OPENAI_API_KEY"],
                    }
                ]
            },
        )

        rows = _rows(_report(environment, a2))

        start = rows["start"]
        assert (start["status"], start["reason_code"]) == (
            "blocked",
            "preflight_failed",
        )
        assert start["stale_because"] is None
        assert start["reason"] == (
            "Agent preflight failed: Credentials (missing OPENAI_API_KEY)"
        )
        assert start["fix"]["recheck"] == {
            "scope": "agent",
            "method": "POST",
            "path": f"{BASE}/{environment.id}/versions/agent/{a2.id}/retry/",
        }
        assert "OPENAI_API_KEY" in start["fix"]["action"]
        assert rows["message"]["status"] == "stale"

    def test_pending_preflight_blocks_start(self, user, workspace, storage):
        environment, a2 = _with_a2(user, workspace, storage, "rd-pending")
        HarnessAgentVersionRuntime.no_workspace_objects.filter(agent_version=a2).update(
            preflight_status="pending"
        )
        _certify(environment, storage, agent=a2)

        start = _rows(_report(environment, a2))["start"]

        assert (start["status"], start["reason_code"]) == (
            "blocked",
            "preflight_pending",
        )

    def test_run_keeps_its_snapshot(
        self, user, workspace, storage, django_capture_on_commit_callbacks
    ):
        environment = _environment(user, workspace, storage, "rd-snapshot")
        _certify(environment, storage)

        run = _committed_run(environment, django_capture_on_commit_callbacks)

        snapshot = run.capability_snapshot
        assert snapshot["answer"]["kind"] == "ready"
        assert snapshot["pairing"]["agent_version"]["id"] == str(
            environment.agent_version_id
        )
        _built(environment, contract=PRODUCTION_ONLY)
        assert _report(environment)["answer"]["kind"] == "blocked"
        run.refresh_from_db()
        assert run.capability_snapshot == snapshot

    def test_snapshot_is_read_after_the_run_commits_from_its_own_bundle(
        self, user, workspace, storage, django_capture_on_commit_callbacks
    ):
        environment = _environment(user, workspace, storage, "rd-snapshot-after")
        _certify(environment, storage)

        with patch.object(
            report_module, "read_evidence", wraps=report_module.read_evidence
        ) as read:
            with django_capture_on_commit_callbacks(execute=False) as callbacks:
                run = _run(environment)
            # Once, before the environment's lock, for the cases the run holds back.
            assert read.call_count == 1
            for callback in callbacks:
                callback()

        run.refresh_from_db()
        assert read.call_count == 2
        assert read.call_args.args[1].id == run.assembled_bundle_id
        assert run.capability_snapshot["answer"]["kind"] == "ready"

    def test_snapshot_reads_the_bundle_the_run_is_bound_to(
        self, user, workspace, storage, django_capture_on_commit_callbacks
    ):
        environment = _environment(user, workspace, storage, "rd-snapshot-bundle")
        _certify(environment, storage)

        with patch.object(harness_readiness, "store_run_snapshot") as stored:
            run = _committed_run(environment, django_capture_on_commit_callbacks)

        assert stored.call_args.args[0] == run.id
        assert stored.call_args.kwargs["bundle"].id == run.assembled_bundle_id

    def test_unreadable_evidence_never_fails_the_run(
        self, user, workspace, storage, django_capture_on_commit_callbacks
    ):
        environment = _environment(user, workspace, storage, "rd-snapshot-error")
        _certify(environment, storage)

        with patch.object(
            report_module,
            "read_evidence",
            side_effect=ConnectionError("storage unreachable"),
        ):
            run = _committed_run(environment, django_capture_on_commit_callbacks)

        assert run.test_execution_id is not None
        assert run.capability_snapshot is None


def _get(auth_client, environment_id, **params):
    return auth_client.get(f"{BASE}/{environment_id}/capabilities/", params)


def _recheck(auth_client, environment_id, **body):
    return auth_client.post(
        f"{BASE}/{environment_id}/capabilities/recheck/", body, format="json"
    )


class TestApi:
    def test_report_shape_and_counts(self, user, workspace, storage, auth_client):
        environment = _environment(user, workspace, storage, "rd-api-shape")
        _certify(environment, storage)

        response = _get(auth_client, environment.id)

        assert response.status_code == 200, response.content
        body = response.json()
        assert set(body) == {
            "pairing",
            "validation",
            "answer",
            "areas",
            "rows",
            "counts",
            "catalog_version",
            "rules_version",
            "computed_at",
            "selection",
        }
        assert set(body["selection"]) == {
            "suite_total",
            "excluded",
            "changed",
            "requested",
            "eligible",
            "blocked",
            "stale",
            "untested",
            "lists",
            "dropped",
            "grading",
            "requirements_recorded",
        }
        assert set(body["selection"]["lists"]) == {
            "eligible",
            "blocked",
            "stale",
            "untested",
        }
        assert set(body["selection"]["grading"]) == {
            "gradable",
            "not_gradable",
            "truncated",
        }
        assert set(body["pairing"]) == {
            "agent_version",
            "environment_version",
            "suite_version",
            "evidence",
            "agent_tools",
        }
        assert body["pairing"]["agent_tools"] == {"read": False, "count": 0}
        assert set(body["validation"]) == {
            "state",
            "job_id",
            "purpose",
            "finished_at",
            "error",
        }
        assert set(body["answer"]) == {"kind", "worst", "reason_code"}
        assert {frozenset(area) for area in body["areas"]} == {
            frozenset({"id", "status", "rows"})
        }
        assert [area["id"] for area in body["areas"]] == [
            "start_and_message",
            "tool_binding",
            "evidence",
            "reset",
            "cancellation",
        ]
        assert {frozenset(row) for row in body["rows"]} == {
            frozenset(
                {
                    "id",
                    "label",
                    "area",
                    "status",
                    "measured",
                    "finding",
                    "reason_code",
                    "reason",
                    "targets",
                    "blocked_by",
                    "stale_because",
                    "was",
                    "evidence",
                    "checked_at",
                    "fix",
                    "consequence",
                    "needed_by",
                    "affected",
                }
            )
        }
        assert {frozenset(row["affected"]) for row in body["rows"]} == {
            frozenset({"count", "of", "keys", "truncated"})
        }
        cases_query = HarnessCapabilityCasesQuerySerializer().fields
        assert cases_query["row"].choices == {
            one.id: one.id for one in harness_readiness.CATALOGUE
        }
        assert cases_query["bucket"].choices == {
            one: one for one in harness_readiness.BUCKETS
        }
        assert len(body["rows"]) == 16
        assert sum(body["counts"].values()) == 16
        assert body["counts"] == {
            "blocked": 0,
            "stale": 0,
            "untested": 10,
            "partial": 0,
            "unavailable": 0,
            "supported": 6,
        }
        assert body["validation"]["state"] == "ready"
        contract = HarnessCapabilitySelectionReportSerializer(data=body)
        assert contract.is_valid(), contract.errors

    def test_unknown_environment_is_404(self, auth_client):
        assert _get(auth_client, uuid.uuid4()).status_code == 404
        assert _recheck(auth_client, uuid.uuid4()).status_code == 404

    def test_versioning_disabled_is_404(
        self, user, workspace, storage, auth_client, settings
    ):
        environment = _environment(user, workspace, storage, "rd-api-off")
        settings.HARNESS_VERSIONING_ENABLED = False

        response = _get(auth_client, environment.id)

        assert response.status_code == 404
        assert response.json()["error"] == "versioning_disabled"

    def test_version_not_pickable_is_422(self, user, workspace, storage, auth_client):
        environment = _environment(user, workspace, storage, "rd-api-pick")
        stranger = _agent_version(_definition(user, workspace, name="Other agent"))

        agent = _get(auth_client, environment.id, agent_version_id=stranger.id)
        version = _get(auth_client, environment.id, environment_version_id=uuid.uuid4())
        recheck = _recheck(
            auth_client, environment.id, suite_version_id=str(uuid.uuid4())
        )

        for response, piece in (
            (agent, "agent"),
            (version, "environment"),
            (recheck, "suite"),
        ):
            assert response.status_code == 422, response.content
            assert response.json()["error"] == "version_not_pickable"
            assert response.json()["piece"] == piece

    def test_malformed_id_is_400(self, user, workspace, storage, auth_client):
        environment = _environment(user, workspace, storage, "rd-api-bad")

        assert (
            _get(auth_client, environment.id, agent_version_id="v2").status_code == 400
        )
        assert (
            _recheck(auth_client, environment.id, agent_version_id="v2").status_code
            == 400
        )

    def test_recheck_starts_a_recheck_validation_and_moves_nothing(
        self, user, workspace, storage, auth_client
    ):
        environment, a2 = _with_a2(user, workspace, storage, "rd-api-recheck")

        response = _recheck(auth_client, environment.id, agent_version_id=str(a2.id))

        assert response.status_code == 202, response.content
        validation = response.json()["validation"]
        assert (validation["state"], validation["purpose"]) == ("validating", "recheck")
        job = HostedHarnessJob.no_workspace_objects.get(id=validation["job_id"])
        assert job.payload["metadata"]["validation_purpose"] == "recheck"
        assert job.agent_version_id == a2.id
        assert "validation_move" not in job.payload["metadata"]
        environment.refresh_from_db()
        assert environment.agent_version_id == _a1(environment).id
        report = _get(auth_client, environment.id, agent_version_id=a2.id).json()
        assert report["validation"]["state"] == "validating"
        assert report["validation"]["job_id"] == validation["job_id"]
        assert report["answer"]["kind"] == "validating"

    def test_recheck_resets_a_failed_pairing(
        self, user, workspace, storage, auth_client
    ):
        environment = _environment(user, workspace, storage, "rd-api-reset")
        HarnessAssembledBundle._base_manager.filter(id=_row(environment).id).update(
            state="failed",
            object_key=None,
            error={"code": "certify_incomplete", "source": "validation"},
        )

        response = _recheck(auth_client, environment.id)

        assert response.status_code == 202, response.content
        assert response.json()["validation"]["state"] == "validating"
        assert _row(environment).state == "pending"

    def test_recheck_is_unavailable_without_certifying(
        self, user, workspace, storage, auth_client, settings
    ):
        environment = _environment(user, workspace, storage, "rd-api-unavailable")
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = False
        jobs = HostedHarnessJob.no_workspace_objects.count()

        response = _recheck(auth_client, environment.id)

        assert response.status_code == 202, response.content
        assert response.json()["validation"] == {
            "state": "unavailable",
            "job_id": None,
            "purpose": "recheck",
            "finished_at": None,
            "error": None,
        }
        assert HostedHarnessJob.no_workspace_objects.count() == jobs

    def test_recheck_waits_for_a_failed_preflight(
        self, user, workspace, storage, auth_client
    ):
        environment, a2 = _with_a2(user, workspace, storage, "rd-api-preflight")
        HarnessAgentVersionRuntime.no_workspace_objects.filter(agent_version=a2).update(
            preflight_status="failed"
        )
        jobs = HostedHarnessJob.no_workspace_objects.count()

        response = _recheck(auth_client, environment.id, agent_version_id=str(a2.id))

        assert response.status_code == 202, response.content
        assert response.json()["validation"]["state"] == "unavailable"
        assert HostedHarnessJob.no_workspace_objects.count() == jobs

    def test_run_header_shows_the_snapshot_it_was_created_with(
        self, user, workspace, storage, auth_client, django_capture_on_commit_callbacks
    ):
        environment = _environment(user, workspace, storage, "rd-api-header")
        _certify(environment, storage)
        run = _committed_run(environment, django_capture_on_commit_callbacks)
        _built(environment, contract=PRODUCTION_ONLY)

        header = auth_client.get(
            f"/simulate/v3/test-executions/{run.test_execution_id}/calls/"
        ).json()["execution"]

        assert header["capability_snapshot"] == run.capability_snapshot
        assert header["capability_snapshot"]["answer"]["kind"] == "ready"
        assert _get(auth_client, environment.id).json()["answer"]["kind"] == "blocked"


class TestDoneWhen:
    def test_production_only_refund_tool_blocks_routing_then_goes_stale(
        self, user, workspace, storage, auth_client
    ):
        """A reference agent with a production-only refund tool: tool routing is
        Blocked with its reason and fix, every other row reads its real status, and
        the report turns Stale once the agent version changes."""
        environment, a2 = _with_a2(user, workspace, storage, "rd-done-when")
        _certify(environment, storage)
        _built(environment, contract=PRODUCTION_ONLY)

        body = _get(auth_client, environment.id).json()

        rows = _rows(body)
        routing = rows["tool_routing"]
        assert routing["status"] == "blocked"
        assert routing["reason_code"] == "production_only"
        assert routing["reason"] == (
            "Unsupported for this controlled test: issue_refund reaches production"
        )
        assert routing["fix"] == {
            "type": "tool_routing",
            "category": "tool_routing",
            "target": "issue_refund",
            "action": "Point issue_refund at the test world",
            "would": "issue_refund lands in the test world and its probe passes",
            # Certified already: the fix is a new version, checked on its own.
            "recheck": None,
        }
        assert {
            one: rows[one]["status"]
            for one in ("start", "message", "tool_discovery", "state", "reset")
        } == dict.fromkeys(
            ("start", "message", "tool_discovery", "state", "reset"), "supported"
        )
        assert body["answer"]["kind"] == "blocked"
        contract = HarnessCapabilityReportSerializer(data=body)
        assert contract.is_valid(), contract.errors
        assert body["counts"] == {
            "blocked": 1,
            "stale": 0,
            "untested": 10,
            "partial": 0,
            "unavailable": 0,
            "supported": 5,
        }

        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            agent_version=a2
        )
        after = _get(auth_client, environment.id).json()

        assert after["pairing"]["agent_version"]["id"] == str(a2.id)
        assert after["answer"]["kind"] == "stale"
        routing = _rows(after)["tool_routing"]
        assert (routing["status"], routing["was"], routing["stale_because"]) == (
            "stale",
            "blocked",
            "agent_version",
        )


class TestReviewFollowUps:
    def test_a_gate_fix_names_the_gate_by_its_label(self, user, workspace, storage):
        environment = _environment(user, workspace, storage, "rd-gate-label")
        _certify(environment, storage, checks={**PASSED, "processes": "failed"})
        labels = {one.id: one.label for one in harness_readiness.CATALOGUE}

        fix = _rows(_report(environment))["message"]["fix"]

        assert fix["target"] == "start"
        assert fix["action"] == f"Fix {labels['start']} first"
        assert "start" not in fix["action"].replace(labels["start"], "")

    @pytest.mark.parametrize(
        ("code", "source", "state", "answer"),
        [
            ("authoring_runtime_validation_failed", "validation", "failed", "blocked"),
            ("authoring_runtime_validation_failed", None, "retry", "ready"),
            ("sandbox_disappeared", None, "retry", "ready"),
        ],
    )
    def test_only_a_check_that_ran_reads_a_failed_pairing(
        self, user, workspace, storage, code, source, state, answer
    ):
        environment = _environment(
            user, workspace, storage, f"rd-run-failed-{code[:8]}-{state}"
        )
        error = {"code": code} if source is None else {"code": code, "source": source}
        HarnessAssembledBundle._base_manager.filter(id=_row(environment).id).update(
            state="failed", object_key=None, error=error
        )
        _built(environment)

        report = _report(environment)

        assert report["validation"]["state"] == state
        shown = {"code": code}
        if state == "failed":
            shown["reason"] = "the agent did not pass this environment's checks"
        assert report["validation"]["error"] == shown
        assert report["answer"]["kind"] == answer

    @pytest.mark.parametrize("source", ["validation", None])
    def test_the_answer_reads_pair_failed_only_when_the_run_gate_refuses(
        self, user, workspace, storage, source
    ):
        environment = _environment(user, workspace, storage, f"rd-gate-{source}")
        error = {"code": "certify_incomplete"}
        if source is not None:
            error["source"] = source
        HarnessAssembledBundle._base_manager.filter(id=_row(environment).id).update(
            state="failed", object_key=None, error=error
        )
        _built(environment)

        reason = _report(environment)["answer"]["reason_code"]
        try:
            _run(environment, key=f"gate-{source}")
            refused = None
        except HostedHarnessError as refusal:
            refused = refusal.code

        assert reason == refused

    def test_an_unreadable_archive_is_read_again_only_after_a_while(
        self, user, workspace, storage
    ):
        from simulate.services.harness_readiness import evidence

        environment = _environment(user, workspace, storage, "rd-archive-down")
        _certify(environment, storage)

        with patch.object(
            evidence.archives, "get_archive", side_effect=EOFError("truncated")
        ) as fetched:
            first = _report(environment)
            second = _report(environment)

        assert fetched.call_count == 1
        assert first["pairing"]["evidence"] == "pair_certificate"
        assert _rows(second)["tool_routing"]["targets"] == []

    def test_recheck_needs_build_usage_unless_already_certified(
        self, user, workspace, storage, auth_client
    ):
        from ee.usage.exceptions import UsageLimitExceeded
        from ee.usage.schemas.events import CheckResult

        environment, a2 = _with_a2(user, workspace, storage, "rd-api-usage")
        jobs = HostedHarnessJob.no_workspace_objects.count()
        refusal = UsageLimitExceeded(
            CheckResult(
                allowed=False, reason="out of credits", error_code="BUDGET_PAUSED"
            )
        )

        with patch(
            "simulate.services.harness_usage._require_harness_action",
            side_effect=refusal,
        ) as usage:
            refused = _recheck(auth_client, environment.id, agent_version_id=str(a2.id))
            ready = _recheck(auth_client, environment.id)

        assert refused.status_code == 402, refused.content
        assert usage.call_args.args == (
            str(environment.organization_id),
            "harness_authoring",
        )
        assert ready.status_code == 202, ready.content
        assert ready.json()["validation"]["state"] == "ready"
        assert HostedHarnessJob.no_workspace_objects.count() == jobs

    def test_another_workspace_cannot_read_or_recheck_the_environment(
        self, user, workspace, storage, auth_client
    ):
        from accounts.models import Workspace

        environment = _environment(user, workspace, storage, "rd-api-tenant")
        elsewhere = Workspace.objects.create(
            name="Other workspace",
            organization=user.organization,
            is_default=False,
            is_active=True,
            created_by=user,
        )
        other_agent = AgentVersion.no_workspace_objects.create(
            agent_definition=_a1(environment).agent_definition,
            organization=environment.organization,
            workspace=elsewhere,
            description="elsewhere",
            commit_message="elsewhere",
            status="active",
        )

        picked = _get(auth_client, environment.id, agent_version_id=other_agent.id)
        auth_client.set_workspace(elsewhere)
        read = _get(auth_client, environment.id)
        rechecked = _recheck(auth_client, environment.id)

        assert picked.status_code == 422, picked.content
        assert picked.json()["error"] == "version_not_pickable"
        assert (read.status_code, rechecked.status_code) == (404, 404)


FAILED_PROBE = [
    {"action": "lookup_order", "status": "passed", "reason": ""},
    {"action": "issue_refund", "status": "failed", "reason": "no world"},
]
REJECTED_TOOL = [
    {"tool": "lookup_order", "availability": "certified", "reason": ""},
    {"tool": "issue_refund", "availability": "rejected", "reason": ""},
]


class TestRepairKinds:
    @pytest.mark.parametrize(
        ("row", "certificate", "fix_type", "category"),
        [
            (
                "start",
                {"checks": {**PASSED, "processes": "failed"}},
                "start",
                "startup",
            ),
            (
                "message",
                {"checks": {**PASSED, "chat_ingress": "failed"}},
                "message",
                "message_mapping",
            ),
            (
                "tool_discovery",
                {"checks": {**PASSED, "tool_contract": "1/2"}, "tools": REJECTED_TOOL},
                "tool_discovery",
                "tool_routing",
            ),
            (
                "tool_routing",
                {
                    "checks": {**PASSED, "action_behavior": "1/2"},
                    "actions": FAILED_PROBE,
                },
                "tool_routing",
                "tool_routing",
            ),
            (
                "state",
                {"checks": {**PASSED, "schema_and_seed": "failed"}},
                "state",
                "evidence",
            ),
            (
                "reset",
                {"checks": {**PASSED, "reset_equivalence": "failed"}},
                "reset",
                "evidence",
            ),
            (
                "message",
                {"checks": {**PASSED, "processes": "failed"}},
                "gate",
                "startup",
            ),
            (
                "tool_routing",
                {"checks": {**PASSED, "tool_contract": "1/2"}, "tools": REJECTED_TOOL},
                "gate",
                "tool_routing",
            ),
        ],
    )
    def test_fix_category_per_row(
        self, user, workspace, storage, row, certificate, fix_type, category
    ):
        environment = _environment(
            user, workspace, storage, f"rd-kind-{row[:6]}-{fix_type[:4]}"
        )
        _certify(environment, storage, **certificate)

        fix = _rows(_report(environment))[row]["fix"]

        assert (fix["type"], fix["category"]) == (fix_type, category)

    def test_preflight_fix_is_startup_and_recheck_has_no_kind(
        self, user, workspace, storage
    ):
        environment, a2 = _with_a2(user, workspace, storage, "rd-kind-preflight")
        _built(environment)
        HarnessAgentVersionRuntime.no_workspace_objects.filter(agent_version=a2).update(
            preflight_status="pending"
        )

        rows = _rows(_report(environment, a2))

        assert (rows["start"]["fix"]["type"], rows["start"]["fix"]["category"]) == (
            "preflight",
            "startup",
        )
        assert (rows["message"]["fix"]["type"], rows["message"]["fix"]["category"]) == (
            "recheck",
            None,
        )


class TestConsequence:
    def test_every_row_has_a_consequence(self, user, workspace, storage):
        environment = _environment(user, workspace, storage, "rd-consequence")
        _certify(
            environment,
            storage,
            checks={**PASSED, "action_behavior": "1/2"},
            actions=FAILED_PROBE,
        )

        report = _report(environment)

        rows = _rows(report)
        assert all(row["consequence"] for row in report["rows"])
        assert rows["tool_routing"]["consequence"] == (
            "Cases that use issue_refund cannot run until it is routed to the test world"
        )
        assert rows["reset"]["consequence"] == (
            "Every trial starts from the same seed, agent included"
        )
        assert rows["audio"]["consequence"].startswith("No check measures this yet")
        assert report["rules_version"] == "7"
        HarnessCapabilityReportSerializer(data=report).is_valid(raise_exception=True)

    @pytest.mark.parametrize(
        ("where", "consequence"),
        [
            (
                "sandbox",
                "The agent starts in the test sandbox, so every case can begin",
            ),
            (
                "remote",
                "The agent runs on its own side and its start checks passed, so every"
                " case can begin",
            ),
            (
                "provider",
                "The agent runs on its own side and its start checks passed, so every"
                " case can begin",
            ),
        ],
    )
    def test_start_consequence_by_connection(
        self, user, workspace, storage, where, consequence
    ):
        environment, a2 = _with_a2(user, workspace, storage, f"rd-start-{where}")
        runtimes = HarnessAgentVersionRuntime.no_workspace_objects.filter(
            agent_version=a2
        )
        if where == "provider":
            runtimes.update(source_kind="provider")
        elif where == "remote":
            runtime = runtimes.get()
            runtime.agent_payload = {
                **(runtime.agent_payload or {}),
                "source_access": {"kind": "remote"},
            }
            runtime.save(update_fields=["agent_payload"])
        _certify(environment, storage, agent=a2)

        start = _rows(_report(environment, a2))["start"]

        assert start["status"] == "supported"
        assert start["consequence"] == consequence

    def test_an_agent_without_a_side_row_starts_in_our_sandbox(self):
        from simulate.services.harness_readiness.rules import consequence

        row = {
            "id": "start",
            "measured": True,
            "status": "supported",
            "reason_code": None,
        }
        assert consequence({**row, "targets": []}, None) == (
            "The agent starts in the test sandbox, so every case can begin"
        )

    def test_reset_consequence_by_connection(self, user, workspace, storage):
        environment, a2 = _with_a2(user, workspace, storage, "rd-consequence-reset")
        HarnessAgentVersionRuntime.no_workspace_objects.filter(agent_version=a2).update(
            source_kind="provider"
        )
        _certify(environment, storage, agent=a2)
        rows = _rows(_report(environment, a2))
        assert rows["reset"]["consequence"] == (
            "The world resets between trials; the agent may carry its own memory"
        )

        _certify(
            environment,
            storage,
            agent=a2,
            checks={
                **PASSED,
                "schema_and_seed": "not_applicable",
                "reset_equivalence": "not_applicable",
                "world_isolation": "not_applicable",
            },
        )
        reset = _rows(_report(environment, a2))["reset"]
        assert reset["status"] == "unavailable"
        assert reset["consequence"] == (
            "No seeded world of ours to reset for this connection"
        )


def _stopped(
    environment, *, key, verified=True, agent=None, certify_only=False, sandbox=True
):
    """A run of the defaults (or a validation job) that was stopped after its sandbox
    started; ``verified`` when our cleanup saw the sandbox gone."""
    from django.utils import timezone

    from simulate.models import HostedHarnessAttempt
    from simulate.services.hosted_harness import register_attempt

    job = _run(environment, key=key)
    payload = dict(job.payload)
    if certify_only:
        payload["metadata"] = {**(payload.get("metadata") or {}), "certify_only": True}
    HostedHarnessJob.no_workspace_objects.filter(id=job.id).update(
        agent_version=agent or environment.agent_version,
        payload=payload,
        cancel_requested_at=timezone.now(),
        cancel_reason="user_canceled",
    )
    attempt = register_attempt(
        job.id, endpoint_base_url="https://platform.example"
    ).attempt
    HostedHarnessAttempt.no_workspace_objects.filter(id=attempt.id).update(
        provider_ref=f"sandbox-{key}" if sandbox else "",
        cleanup_verified_at=timezone.now() if verified else None,
    )
    return job


class TestCancel:
    def test_a_sandboxed_agent_stopped_and_cleaned_up_is_supported(
        self, user, workspace, storage
    ):
        environment = _environment(user, workspace, storage, "rd-cancel-sandbox")
        _certify(environment, storage)
        _stopped(environment, key="stop")

        cancel = _rows(_report(environment))["cancel"]

        assert (cancel["status"], cancel["reason_code"]) == ("supported", None)
        assert [one["check"] for one in cancel["evidence"]] == [
            "stop_requested",
            "sandbox_absent",
        ]
        assert cancel["checked_at"] and cancel["fix"] is None
        assert cancel["consequence"].startswith("A Stop ends the agent")

    @pytest.mark.parametrize(
        ("access", "where"),
        [({"source_access": {"kind": "remote"}}, "remote"), (None, "provider")],
    )
    def test_an_agent_outside_our_sandbox_stays_partial_after_a_stop(
        self, user, workspace, storage, access, where
    ):
        environment, a2 = _with_a2(user, workspace, storage, f"rd-cancel-{where}")
        if access is None:
            HarnessAgentVersionRuntime.no_workspace_objects.filter(
                agent_version=a2
            ).update(source_kind="provider")
        else:
            runtime = HarnessAgentVersionRuntime.no_workspace_objects.get(
                agent_version=a2
            )
            runtime.agent_payload = {**(runtime.agent_payload or {}), **access}
            runtime.save(update_fields=["agent_payload"])
        _certify(environment, storage, agent=a2)
        _stopped(environment, key="stop", agent=a2)

        report = _report(environment, a2)
        cancel = _rows(report)["cancel"]

        assert (cancel["status"], cancel["reason_code"]) == (
            "partial",
            "stop_unconfirmed",
        )
        expected = {
            "remote": "Stopping ends our side; the agent may keep running on its own side",
            "provider": "Stopping ends our side of the call; the provider may keep the"
            " call open and charge for it",
        }[where]
        assert cancel["consequence"] == expected
        assert report["answer"]["kind"] == "ready"
        assert report["answer"]["worst"] == "partial"

    def test_no_stop_yet_is_untested_with_the_connection_consequence(
        self, user, workspace, storage
    ):
        environment = _environment(user, workspace, storage, "rd-cancel-none")
        _certify(environment, storage)

        report = _report(environment)
        cancel = _rows(report)["cancel"]

        assert (cancel["status"], cancel["reason_code"]) == (
            "untested",
            "stop_not_observed",
        )
        assert cancel["consequence"].startswith("Stopping ends the agent with its test")
        assert report["answer"] == {
            "kind": "ready",
            "worst": "supported",
            "reason_code": None,
        }

    def test_a_stop_still_cleaning_up_is_partial_never_supported(
        self, user, workspace, storage
    ):
        environment = _environment(user, workspace, storage, "rd-cancel-pending")
        _certify(environment, storage)
        _stopped(environment, key="stop", verified=False)

        cancel = _rows(_report(environment))["cancel"]

        assert (cancel["status"], cancel["reason_code"]) == (
            "partial",
            "stop_unconfirmed",
        )

    def test_a_validation_stop_and_another_versions_stop_are_ignored(
        self, user, workspace, storage
    ):
        environment, a2 = _with_a2(user, workspace, storage, "rd-cancel-ignored")
        _certify(environment, storage)
        _stopped(environment, key="other", agent=a2)
        _stopped(environment, key="check", certify_only=True)

        cancel = _rows(_report(environment))["cancel"]

        assert cancel["status"] == "untested"

    def test_a_stop_before_any_sandbox_proves_nothing(self, user, workspace, storage):
        environment = _environment(user, workspace, storage, "rd-cancel-early")
        _certify(environment, storage)
        _stopped(environment, key="early", sandbox=False)

        assert _rows(_report(environment))["cancel"]["status"] == "untested"

    def test_a_stopped_build_of_the_agent_is_not_a_stop_of_a_run(
        self, user, workspace, storage
    ):
        from django.utils import timezone

        from simulate.models import HostedHarnessAttempt
        from simulate.services.hosted_harness import register_attempt

        environment = _environment(user, workspace, storage, "rd-cancel-build")
        _certify(environment, storage)
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            cancel_requested_at=timezone.now(), cancel_reason="user_canceled"
        )
        attempt = register_attempt(
            environment.id, endpoint_base_url="https://platform.example"
        ).attempt
        HostedHarnessAttempt.no_workspace_objects.filter(id=attempt.id).update(
            provider_ref="sandbox-build", cleanup_verified_at=timezone.now()
        )

        assert _rows(_report(environment))["cancel"]["status"] == "untested"

    def test_cancel_is_needed_by_no_case(self, user, workspace, storage):
        environment = _environment(user, workspace, storage, "rd-cancel-needs")
        _certify(environment, storage)

        report = harness_readiness.selection_report(
            _pairing(environment), keys=None, trials=1
        )

        assert _rows(report)["cancel"]["needed_by"] == 0


WORLD = {
    "tools": [
        {"name": "lookup_order", "args": ["order_id"]},
        {"name": "issue_refund", "args": ["order_id", "amount"]},
    ]
}


def _tool(name, *arguments):
    return {
        "name": name,
        "description": None,
        "kind": "builtin",
        "parameters": {
            "type": "object",
            "properties": {argument: {} for argument in arguments},
            "required": list(arguments),
        },
        "endpoint": None,
    }


SAME_TOOLS = (
    _tool("lookup_order", "order_id"),
    _tool("issue_refund", "order_id", "amount"),
)


class TestAgentGap:
    """Chintan's case: E1 built with A1, then A2 brings a tool E1 does not offer."""

    def test_a_tool_the_world_lacks_blocks_before_and_after_a_check(
        self, user, workspace, storage
    ):
        environment, a2 = _with_a2(
            user,
            workspace,
            storage,
            "rd-gap-world",
            tools=(*SAME_TOOLS, _tool("send_email", "to", "body")),
        )
        _built(environment, contract=WORLD)

        for certified in (False, True):
            if certified:
                _certify(environment, storage, agent=a2)
            report = _report(environment, a2)
            discovery = _rows(report)["tool_discovery"]

            assert (discovery["status"], discovery["reason_code"]) == (
                "blocked",
                "tool_not_in_world",
            )
            assert discovery["targets"] == [
                {"kind": "agent_tool", "name": "send_email", "result": "failed"}
            ]
            assert discovery["fix"] == {
                "type": "tool_not_in_world",
                "category": "tool_routing",
                "target": "send_email",
                "action": "Add send_email to this environment in the builder, or test"
                " this agent version in an environment that has it",
                "would": "send_email is simulated in the test world and its probe passes",
                "recheck": None,
            }
            assert discovery["consequence"].startswith(
                "In a run, send_email would reach outside the test world"
            )
            assert report["answer"]["kind"] == "blocked"
            assert report["pairing"]["agent_tools"] == {"read": True, "count": 3}
            HarnessCapabilityReportSerializer(data=report).is_valid(
                raise_exception=True
            )

    def test_a_changed_signature_blocks_routing_on_that_tool(
        self, user, workspace, storage
    ):
        environment, a2 = _with_a2(
            user,
            workspace,
            storage,
            "rd-gap-signature",
            tools=(
                _tool("lookup_order", "order_id"),
                _tool("issue_refund", "order_id", "amount", "reason"),
            ),
        )
        _built(environment, contract=WORLD)
        _certify(environment, storage, agent=a2)

        report = _report(environment, a2)
        rows = _rows(report)

        assert rows["tool_discovery"]["status"] == "supported"
        routing = rows["tool_routing"]
        assert (routing["status"], routing["reason_code"], routing["targets"]) == (
            "blocked",
            "tool_signature_changed",
            [{"kind": "tool", "name": "issue_refund", "result": "failed"}],
        )
        assert (routing["fix"]["type"], routing["fix"]["target"]) == (
            "tool_signature_changed",
            "issue_refund",
        )
        assert report["answer"]["kind"] == "blocked"

    def test_a_world_tool_the_agent_lacks_blocks_the_cases_that_use_it(
        self, user, workspace, storage
    ):
        environment, a2 = _with_a2(
            user,
            workspace,
            storage,
            "rd-gap-agent",
            tools=(_tool("lookup_order", "order_id"),),
        )
        _built(environment, contract=WORLD)
        _certify(environment, storage, agent=a2)

        discovery = _rows(_report(environment, a2))["tool_discovery"]

        assert (discovery["status"], discovery["reason_code"]) == (
            "blocked",
            "tool_not_in_agent",
        )
        assert discovery["targets"] == [
            {"kind": "tool", "name": "issue_refund", "result": "failed"}
        ]
        assert discovery["fix"]["action"] == (
            "Give this agent version issue_refund, or pick an agent version that has it"
        )

    def test_an_unread_version_is_untested_with_a_recheck(
        self, user, workspace, storage
    ):
        environment, a2 = _with_a2(
            user, workspace, storage, "rd-gap-unread", tools=None
        )
        _built(environment, contract=WORLD)
        _certify(environment, storage, agent=a2)

        report = _report(environment, a2)
        discovery = _rows(report)["tool_discovery"]

        assert (discovery["status"], discovery["reason_code"]) == (
            "untested",
            "agent_tools_unread",
        )
        assert discovery["fix"] == {
            "type": "agent_tools_unread",
            "category": "startup",
            "target": None,
            "action": "Re-check to read this agent's tools",
            "would": "This agent's tools are compared with the environment's",
            "recheck": {
                "scope": "pair",
                "method": "POST",
                "path": f"{BASE}/{environment.id}/capabilities/recheck/",
            },
        }
        assert report["pairing"]["agent_tools"] == {"read": False, "count": 0}
        assert report["answer"]["kind"] == "unchecked"

    def test_an_unread_provider_agent_offers_no_recheck_that_cannot_read_it(
        self, user, workspace, storage
    ):
        environment, a2 = _with_a2(
            user, workspace, storage, "rd-gap-unread-provider", tools=None
        )
        HarnessAgentVersionRuntime.no_workspace_objects.filter(agent_version=a2).update(
            source_kind="provider"
        )
        _built(environment, contract=WORLD)
        _certify(environment, storage, agent=a2)

        discovery = _rows(_report(environment, a2))["tool_discovery"]

        assert discovery["reason_code"] == "agent_tools_unread"
        assert discovery["fix"] is None

    def test_rechecking_a_certified_unread_version_reads_it_and_is_metered(
        self, user, workspace, storage, auth_client
    ):
        from ee.usage.exceptions import UsageLimitExceeded
        from ee.usage.schemas.events import CheckResult

        environment, a2 = _with_a2(
            user, workspace, storage, "rd-gap-unread-recheck", tools=None
        )
        _built(environment, contract=WORLD)
        _certify(environment, storage, agent=a2)
        refusal = UsageLimitExceeded(
            CheckResult(
                allowed=False, reason="out of credits", error_code="BUDGET_PAUSED"
            )
        )

        with patch(
            "simulate.services.harness_usage._require_harness_action",
            side_effect=refusal,
        ):
            refused = _recheck(auth_client, environment.id, agent_version_id=str(a2.id))
        started = _recheck(auth_client, environment.id, agent_version_id=str(a2.id))

        assert refused.status_code == 402, refused.content
        assert started.status_code == 202, started.content
        validation = started.json()["validation"]
        assert validation["state"] == "validating"
        job = HostedHarnessJob.no_workspace_objects.get(id=validation["job_id"])
        assert job.payload["metadata"]["agent_reading"] == "only"

    def test_the_same_tools_leave_the_certificate_reading(
        self, user, workspace, storage
    ):
        environment, a2 = _with_a2(
            user, workspace, storage, "rd-gap-same", tools=SAME_TOOLS
        )
        _built(environment, contract=WORLD)
        _certify(environment, storage, agent=a2)

        report = _report(environment, a2)

        assert {
            row["status"]
            for row in report["rows"]
            if row["measured"] and row["id"] != "cancel"
        } == {"supported"}
        assert report["answer"]["kind"] == "ready"

    def test_the_agent_the_environment_was_built_with_is_not_compared(
        self, user, workspace, storage
    ):
        environment = _environment(user, workspace, storage, "rd-gap-built-with")
        _built(environment, contract=WORLD)
        _certify(environment, storage)

        report = _report(environment)

        assert report["pairing"]["agent_tools"]["read"] is False
        assert _rows(report)["tool_discovery"]["status"] == "supported"

    def test_a_changed_signature_keeps_the_actions_a_failed_pair_named(
        self, user, workspace, storage
    ):
        environment, a2 = _with_a2(
            user,
            workspace,
            storage,
            "rd-gap-named",
            tools=(_tool("lookup_order", "id"), SAME_TOOLS[1]),
        )
        _built(environment, contract=WORLD)
        _certify(environment, storage, agent=a2)
        HarnessAssembledBundle._base_manager.filter(
            id=_bundle(environment, a2).id
        ).update(
            state="failed",
            object_key=None,
            error={
                "code": "authoring_runtime_validation_failed",
                "source": "validation",
                "failed_actions": [{"action": "issue_refund", "reason": "live"}],
            },
        )

        routing = _rows(_report(environment, a2))["tool_routing"]

        assert routing["reason_code"] == "tool_signature_changed"
        assert routing["targets"] == [
            {"kind": "tool", "name": "lookup_order", "result": "failed"},
            {"kind": "action", "name": "issue_refund", "result": "failed"},
        ]
        assert routing["reason"].endswith("failed its probe: issue_refund")
