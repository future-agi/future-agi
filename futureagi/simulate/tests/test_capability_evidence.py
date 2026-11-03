"""Capability evidence a build sealed: stored once per build, and read back as a report."""

import io
import json
import tarfile
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from simulate.models import HarnessCapabilityEvidence, HostedHarnessJob
from simulate.services.capability_evidence import (
    capability_report,
    record_capability_evidence,
    scenario_capabilities,
)
from simulate.tests.test_harness_versioning_backfill import (
    storage as storage,  # noqa: F401 - fixture
)

_DOCUMENT = {
    "schema_version": "futureagi.capability-evidence.v1",
    "catalog_version": "1",
    "inputs": {"source_digest": "s1", "bundle_digest": "b1"},
    "capabilities": {"start_health": {"finding": "passed", "checks": []}},
}
_CATALOGUE = [{"id": "start_health", "label": "Start and health", "depends_on": ["source"]}]


def _archive(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


@pytest.fixture
def job(db, organization, workspace):
    return HostedHarnessJob.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        run_id=uuid.uuid4(),
        idempotency_key=uuid.uuid4().hex,
        request_digest="digest",
        schema_version="1.6",
        seed=1,
        artifact_level="standard",
        max_artifact_bytes=1024,
        deadline_at=timezone.now() + timedelta(hours=1),
        scenario_count=1,
        payload={"metadata": {}},
    )


def test_a_build_s_evidence_is_stored_once_and_reads_back_as_a_report(job):
    body = _archive({"generic-harness/capability-evidence.json": json.dumps(_DOCUMENT).encode()})

    first = record_capability_evidence(job, body)
    again = record_capability_evidence(job, body)

    assert first is not None and again is not None and first.id == again.id
    assert HarnessCapabilityEvidence.no_workspace_objects.filter(job=job).count() == 1
    assert first.capabilities == _DOCUMENT["capabilities"] and first.catalog_version == "1"
    report = capability_report(_CATALOGUE, first, {"source_digest": "s1", "bundle_digest": "b1"})
    assert report["capabilities"][0]["status"] == "supported"
    assert capability_report(_CATALOGUE, first, {"source_digest": "s2"})["capabilities"][0]["status"] == "stale"


def test_a_build_without_evidence_stores_nothing_and_reads_untested(job):
    body = _archive({"generic-harness/certification.json": b"{}"})

    assert record_capability_evidence(job, body) is None
    assert not HarnessCapabilityEvidence.no_workspace_objects.filter(job=job).exists()
    assert capability_report(_CATALOGUE, None, {})["capabilities"][0]["status"] == "untested"


def test_the_environment_report_is_served_and_unknown_environments_are_not_found(job, auth_client):
    body = _archive(
        {
            "generic-harness/capability-evidence.json": json.dumps(
                {**_DOCUMENT, "order": ["start_health"], "labels": {"start_health": "Start and health"}}
            ).encode()
        }
    )
    record_capability_evidence(job, body)

    response = auth_client.get(f"/simulate/api/harness-environments/{job.id}/capabilities/")
    missing = auth_client.get(f"/simulate/api/harness-environments/{uuid.uuid4()}/capabilities/")

    assert response.status_code == 200, response.content
    rows = response.json()["capabilities"]
    assert [(row["id"], row["label"], row["status"]) for row in rows] == [
        ("start_health", "Start and health", "supported")
    ]
    assert missing.status_code == 404


def test_a_later_archive_of_the_same_build_refreshes_its_row(job):
    first = record_capability_evidence(job, _archive({"generic-harness/capability-evidence.json": json.dumps(_DOCUMENT).encode()}))
    final = {**_DOCUMENT, "capabilities": {"start_health": {"finding": "failed", "checks": []}}}
    later = record_capability_evidence(
        job,
        _archive(
            {
                "generic-harness/capability-evidence.json": json.dumps(final).encode(),
                "generic-harness/scenario-requirements.json": json.dumps(
                    {"scenarios": {"refund": {"capabilities": ["start_health"]}}}
                ).encode(),
            }
        ),
    )

    assert later.id == first.id
    assert HarnessCapabilityEvidence.no_workspace_objects.filter(job=job).count() == 1
    assert later.capabilities["start_health"]["finding"] == "failed"
    assert later.scenario_requirements == {"refund": {"capabilities": ["start_health"]}}


def _versions(organization, workspace):
    from simulate.models import AgentDefinition, AgentVersion

    definition = AgentDefinition.objects.create(
        agent_name="Refunds agent",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        inbound=True,
        description="",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )
    return [
        definition.create_version(description="", commit_message=name, status=AgentVersion.StatusChoices.DRAFT)
        for name in ("v1", "v2")
    ]


def test_the_report_goes_stale_once_the_agent_is_another_version_than_the_one_checked(job, organization, workspace):
    from simulate.services.capability_evidence import environment_capability_report

    first, second = _versions(organization, workspace)
    job.agent_version = first
    job.save(update_fields=["agent_version"])
    record_capability_evidence(job, _archive({"generic-harness/capability-evidence.json": json.dumps(_DOCUMENT).encode()}))

    def row(**kwargs):
        return environment_capability_report(job, **kwargs)["capabilities"][0]

    assert row()["status"] == "supported"
    assert row(agent_version_id=first.id)["status"] == "supported"
    asked = row(agent_version_id=second.id)
    assert (asked["status"], asked["stale_because"]) == ("stale", "agent_version_id")
    assert environment_capability_report(job, agent_version_id=second.id)["agent_version_id"] == str(second.id)
    # The environment itself moved to the new agent version: its own report is Stale too.
    job.agent_version = second
    job.save(update_fields=["agent_version"])
    assert row()["status"] == "stale"


def test_a_failed_agent_preflight_blocks_start_and_health_and_everything_behind_it(job, organization, workspace):
    from simulate.models import HarnessAgentVersionRuntime
    from simulate.services.capability_evidence import environment_capability_report

    first, second = _versions(organization, workspace)
    job.agent_version = first
    job.save(update_fields=["agent_version"])
    document = {**_DOCUMENT, "capabilities": {"start_health": {"finding": "passed"}, "one_turn": {"finding": "passed"}}}
    evidence = record_capability_evidence(
        job, _archive({"generic-harness/capability-evidence.json": json.dumps(document).encode()}))
    evidence.catalogue = [*_CATALOGUE, {"id": "one_turn", "label": "One turn", "depends_on": ["start_health"]}]
    evidence.save(update_fields=["catalogue"])
    for version, status, report in (
        (second, "passed", {"checks": [{"id": "source", "label": "Source", "status": "passed", "missing": []}]}),
        (first, "failed", {"checks": [
            {"id": "source", "label": "Source", "status": "passed", "missing": []},
            {"id": "credentials_present", "label": "Credentials", "status": "failed", "missing": ["API_KEY"]},
        ]}),
    ):
        HarnessAgentVersionRuntime.objects.create(
            agent_version=version, agent_definition=version.agent_definition, organization=organization,
            hash="sha256:" + "2" * 64, source_kind="github", trigger="creation",
            preflight_status=status, preflight_report=report,
        )

    rows = {row["id"]: row for row in environment_capability_report(job)["capabilities"]}
    health = rows["start_health"]
    assert (health["status"], health["reason_code"]) == ("blocked", "credentials_present")
    assert health["reason"] == "Agent preflight failed: Credentials (missing API_KEY)"
    assert (rows["one_turn"]["status"], rows["one_turn"]["blocked_by"]) == ("untested", "start_health")
    # A version whose preflight passed adds nothing: it was not the version checked, so the report is Stale for it.
    other = {row["id"]: row for row in environment_capability_report(job, agent_version_id=second.id)["capabilities"]}
    assert {row["status"] for row in other.values()} == {"stale"}


def test_a_run_keeps_the_capability_report_it_was_created_with(user, workspace, settings):
    from django.test import override_settings

    from simulate.services.hosted_harness import create_selected_harness_run
    from simulate.tests.test_harness_unpinned_runs import _environment

    settings.HARNESS_VERSIONING_ENABLED = False
    environment = _environment(user, workspace, "capability-snapshot")
    record_capability_evidence(
        environment, _archive({"generic-harness/capability-evidence.json": json.dumps(_DOCUMENT).encode()}))
    evidence = HarnessCapabilityEvidence.no_workspace_objects.get(job=environment)
    evidence.catalogue = _CATALOGUE
    evidence.save(update_fields=["catalogue"])

    with override_settings(HARNESS_MAX_ARTIFACT_BYTES=10_737_418_240):
        run, _ = create_selected_harness_run(
            environment, scenario_keys=["scenario-a"], trials=1, idempotency_key="snapshot-run")
    run.refresh_from_db()
    snapshot = run.capability_snapshot
    assert [(row["id"], row["status"]) for row in snapshot["capabilities"]] == [("start_health", "supported")]
    assert snapshot["rules_version"] and snapshot["computed_at"]

    # Later evidence changes the environment's report, never the run's copy.
    evidence.capabilities = {"start_health": {"finding": "failed"}}
    evidence.save(update_fields=["capabilities"])
    run.refresh_from_db()
    assert run.capability_snapshot == snapshot


def test_evidence_that_names_no_agent_version_is_never_stale_on_the_agent(job, organization, workspace):
    from simulate.services.capability_evidence import environment_capability_report

    first, _ = _versions(organization, workspace)
    record_capability_evidence(job, _archive({"generic-harness/capability-evidence.json": json.dumps(_DOCUMENT).encode()}))
    job.agent_version = first
    job.save(update_fields=["agent_version"])
    assert environment_capability_report(job)["capabilities"][0]["status"] == "supported"


def test_evidence_from_before_versions_existed_is_judged_only_on_what_it_names(user, workspace, storage, settings):
    from simulate.services.capability_evidence import environment_capability_report
    from simulate.tests.test_harness_versioning_picks import _environment

    settings.HARNESS_VERSIONING_ENABLED = True
    environment = _environment(user, workspace, "pre-version-evidence", storage)
    evidence = HarnessCapabilityEvidence.no_workspace_objects.create(
        organization_id=environment.organization_id, workspace_id=environment.workspace_id, job=environment,
        origin=HarnessCapabilityEvidence.Origin.CERTIFICATION, catalog_version="1", catalogue=_CATALOGUE,
        inputs={}, capabilities={"start_health": {"finding": "passed"}}, captured_at=timezone.now(),
    )

    def status():
        return environment_capability_report(environment)["capabilities"][0]["status"]

    assert environment.environment_version_id is not None and status() == "supported"
    other = environment.environment_version.__class__.objects.exclude(id=environment.environment_version_id).first()
    if other is not None:
        evidence.environment_version = other
        evidence.save(update_fields=["environment_version"])
        assert status() == "stale"


def test_an_archive_whose_evidence_cannot_be_read_says_so_until_a_readable_one_arrives(job):
    from simulate.services.capability_evidence import environment_capability_report

    broken = record_capability_evidence(job, _archive({"generic-harness/capability-evidence.json": b"{not json"}))

    row = HarnessCapabilityEvidence.no_workspace_objects.get(job=job)
    assert broken is None and (row.capture_status, bool(row.capture_error)) == ("errored", True)
    assert environment_capability_report(job)["evidence_unreadable"] is True
    record_capability_evidence(job, _archive({"generic-harness/capability-evidence.json": json.dumps(_DOCUMENT).encode()}))
    row.refresh_from_db()
    assert (row.capture_status, row.capture_error) == ("complete", None)
    assert environment_capability_report(job)["evidence_unreadable"] is False


def _versioned_with_evidence(user, workspace, storage, key):
    from simulate.models import HarnessSuiteMember
    from simulate.tests.test_harness_versioning_picks import _environment

    environment = _environment(user, workspace, key, storage)
    members = list(
        HarnessSuiteMember.no_workspace_objects.filter(suite_version=environment.suite_version)
        .values_list("scenario_key", flat=True)
    )
    requirements = {name: {"capabilities": ["start_health"]} for name in [*members, "retired-case"]}
    record_capability_evidence(environment, _archive({
        "generic-harness/capability-evidence.json": json.dumps(_DOCUMENT).encode(),
        "generic-harness/scenario-requirements.json": json.dumps({"scenarios": requirements}).encode(),
    }))
    HarnessCapabilityEvidence.no_workspace_objects.filter(job=environment).update(catalogue=_CATALOGUE)
    return environment, members


def test_the_report_counts_the_picked_suite_version_s_cases_and_refuses_what_is_not_in_it(
    user, workspace, storage, auth_client, settings
):
    settings.HARNESS_VERSIONING_ENABLED = True
    environment, members = _versioned_with_evidence(user, workspace, storage, "capability-picks")
    url = f"/simulate/api/harness-environments/{environment.id}/capabilities/"

    whole = auth_client.get(url, {"suite_version_id": str(environment.suite_version_id)})
    one = auth_client.get(url, {"keys": [members[0]]})
    outside = auth_client.get(url, {"keys": [members[0], "retired-case"]})
    unknown = auth_client.get(url, {"environment_version_id": str(uuid.uuid4())})

    assert whole.status_code == 200, whole.content
    assert whole.json()["selection"]["dropped"] == [{"key": "retired-case", "reason": "no longer in the suite"}]
    assert whole.json()["selection"]["eligible"] == len(members)
    assert one.json()["selection"]["eligible"] == 1 and one.json()["selection"]["dropped"] == []
    assert outside.status_code == 400 and outside.json()["error"] == "selection_invalid"
    assert outside.json()["keys"] == ["retired-case"]
    assert unknown.status_code == 404


def test_a_re_check_runs_a_failed_agent_preflight_again_and_leaves_a_passed_one(
    user, workspace, storage, auth_client, settings
):
    from simulate.models import HarnessAgentVersionRuntime

    settings.HARNESS_VERSIONING_ENABLED = True
    environment, _ = _versioned_with_evidence(user, workspace, storage, "capability-recheck")
    url = f"/simulate/api/harness-environments/{environment.id}/capabilities/recheck/"
    runtime = HarnessAgentVersionRuntime.no_workspace_objects.get(agent_version_id=environment.agent_version_id)

    runtime.preflight_status = "passed"
    runtime.save(update_fields=["preflight_status"])
    passed = auth_client.post(url, {"scope": "agent", "check": "start_health"}, format="json")
    runtime.preflight_status = "failed"
    runtime.save(update_fields=["preflight_status"])
    started = auth_client.post(url, {"scope": "agent", "check": "start_health"}, format="json")
    world = auth_client.post(url, {"scope": "world"}, format="json")
    unknown = auth_client.post(url, {"scope": "agent", "check": "no_such_check"}, format="json")

    assert passed.status_code == 200 and passed.json()["state"] == "not_needed"
    assert started.status_code == 200, started.content
    assert started.json() == {"scope": "agent", "agent_version_id": str(environment.agent_version_id), "state": "started"}
    runtime.refresh_from_db()
    assert runtime.preflight_status == "pending"
    assert world.status_code == 409 and world.json()["error"] == "world_recheck_not_offered"
    assert unknown.status_code == 400 and unknown.json()["error"] == "capability_unknown"


def test_each_case_reads_its_capabilities_for_the_agent_version_a_run_is_pinned_to(job, organization, workspace):
    first, second = _versions(organization, workspace)
    job.agent_version = first
    job.save(update_fields=["agent_version"])
    document = {**_DOCUMENT, "capabilities": {"start_health": {"finding": "failed", "checks": []}}}
    record_capability_evidence(job, _archive({
        "generic-harness/capability-evidence.json": json.dumps(document).encode(),
        "generic-harness/scenario-requirements.json": json.dumps(
            {"scenarios": {"refund": {"capabilities": ["start_health"]}}}).encode(),
    }))
    HarnessCapabilityEvidence.no_workspace_objects.filter(job=job).update(catalogue=_CATALOGUE)

    assert scenario_capabilities(job, first)["refund"]["blocked_by"][0]["capability"] == "start_health"
    # Checked with the first version only: for the second it is not known, so the case is untested, not blocked.
    pinned = scenario_capabilities(job, second)["refund"]
    assert pinned["blocked_by"] == [] and pinned["untested"] == ["start_health"]


_PRODUCTION_REFUND = {
    "tool_entrypoints": [{"tool": "refund", "mode": "import"}, {"tool": "lookup", "mode": "import"}],
    # The refund tool calls a payments service the environment has no way to stand up, so it would reach the real one.
    "dependencies": [
        {"name": "payments", "kind": "service", "used_by": ["refund"], "reached": {}},
        {"name": "orders", "kind": "datastore", "used_by": ["lookup"], "engine": "postgres",
         "reached": {"dsn_env": "ORDERS_DSN"}},
    ],
}


def test_a_refund_tool_that_reaches_production_blocks_tool_routing_until_the_agent_version_changes(
    user, workspace, storage, auth_client, settings
):
    from simulate.models import AgentVersion

    settings.HARNESS_VERSIONING_ENABLED = True
    environment, _ = _versioned_with_evidence(user, workspace, storage, "capability-production-refund")
    catalogue = [*_CATALOGUE, {"id": "tool_routing", "label": "Tool routing", "depends_on": [], "fix_type": "tool_routing"}]
    HarnessCapabilityEvidence.no_workspace_objects.filter(job=environment).update(catalogue=catalogue)
    version = environment.environment_version
    version.documents = {**(version.documents or {}), "contract": _PRODUCTION_REFUND}
    version.save(update_fields=["documents"])
    url = f"/simulate/api/harness-environments/{environment.id}/capabilities/"

    rows = {row["id"]: row for row in auth_client.get(url).json()["capabilities"]}

    routing = rows["tool_routing"]
    assert (routing["status"], routing["reason_code"]) == ("blocked", "production_route")
    assert routing["reason"] == "refund reaches payments outside the test world, so it would run live"
    assert routing["fix"]["type"] == "tool_routing" and routing["fix"]["target"] == ["refund"]
    assert routing["fix"]["repair"] == "agent.tools" and "payments" in routing["fix"]["text"]
    assert rows["start_health"]["status"] == "supported"

    newer = environment.agent_version.agent_definition.create_version(
        description="", commit_message="v2", status=AgentVersion.StatusChoices.DRAFT
    )
    after = {row["id"]: row for row in auth_client.get(url, {"agent_version_id": str(newer.id)}).json()["capabilities"]}
    assert (after["start_health"]["status"], after["start_health"]["stale_because"]) == ("stale", "agent_version_id")
    # The contract still routes refund live, so that stays Blocked rather than reading as merely out of date.
    assert after["tool_routing"]["status"] == "blocked"


def test_the_report_goes_stale_once_a_newer_harness_release_certified_the_same_versions(user, workspace, storage):
    from simulate.models import HarnessAssembledBundle
    from simulate.services.capability_evidence import environment_capability_report

    environment, _ = _versioned_with_evidence(user, workspace, storage, "capability-harness-release")
    document = {**_DOCUMENT, "inputs": {**_DOCUMENT["inputs"], "harness_version": "1.0"}}
    record_capability_evidence(environment, _archive({"generic-harness/capability-evidence.json": json.dumps(document).encode()}))
    HarnessCapabilityEvidence.no_workspace_objects.filter(job=environment).update(catalogue=_CATALOGUE)

    def bundle(release):
        return HarnessAssembledBundle.no_workspace_objects.create(
            job=environment,
            organization=environment.organization,
            agent_version=environment.agent_version,
            environment_version=environment.environment_version,
            suite_version=environment.suite_version,
            assembly_key=f"sha256:{release}",
            state=HarnessAssembledBundle.State.READY,
            certificate={"compiler": {"version": release}},
        )

    def row():
        return environment_capability_report(environment)["capabilities"][0]

    assert row()["status"] == "supported"
    bundle("1.0")
    assert row()["status"] == "supported"
    bundle("1.1")
    assert (row()["status"], row()["stale_because"]) == ("stale", "harness_version")
