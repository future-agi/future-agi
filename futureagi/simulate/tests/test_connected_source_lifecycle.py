import hashlib
import json
from uuid import uuid4

import pytest

from simulate.models import AgentDefinition, HostedHarnessJob
from simulate.serializers.harness_job import HarnessJobCreateSerializer
from simulate.services import harness_designs as designs
from simulate.services import harness_reviewed_builds as builds
from simulate.services import harness_source_reviews as reviews
from simulate.services import harness_sources
from simulate.services.harness_provider import HostedHarnessProvider
from simulate.services.hosted_harness import HostedHarnessError
from simulate.services.hosted_harness_gateway import guest_job_document
from simulate.tests.test_design_source_lifecycle import (
    _object_storage as _object_storage,
)
from simulate.tests.test_design_source_lifecycle import (
    _request,
)
from simulate.tests.test_design_source_lifecycle import (
    alk_source_cli as alk_source_cli,
)
from simulate.tests.test_harness_connected_versions import _connect
from simulate.tests.test_harness_designs import _archive
from simulate.tests.test_harness_provider import _LIVEKIT_REFS, _v1_payload


def _connector_payload(kind, user, workspace):
    payload = _v1_payload(scenario_count=1)
    payload["agent"] = {"connector": "auto", "config": {}, "secret_refs": {}}
    if kind == "repo":
        return payload
    if kind == "saved_agent":
        agent = AgentDefinition.objects.create(
            agent_name="support",
            agent_type=AgentDefinition.AgentTypeChoices.TEXT,
            inbound=True,
            organization=user.organization,
            workspace=workspace,
        )
        version = _connect(agent)
        payload["metadata"] = {
            "agent_definition_id": str(agent.id),
            "agent_version_id": str(version.id),
        }
        return payload
    if kind == "archive":
        payload["source"] = {
            "kind": "archive",
            "archive_artifact_id": str(uuid4()),
            "visibility": "private",
        }
        return payload
    del payload["source"]
    config = {"target_system_prompt": "Answer a user's policy questions."}
    refs = {}
    if kind == "phone":
        config["phone_number"] = "+12025550108"
    elif kind == "livekit":
        config["agent_name"] = "support-desk"
        refs = _LIVEKIT_REFS
    else:
        config["assistant_id" if kind == "vapi" else "agent_id"] = "support"
        refs = {
            kind.upper() + "_API_KEY": {
                **_LIVEKIT_REFS["LIVEKIT_API_KEY"],
                "key": "demo-reference",
            }
        }
    payload["agent"] = {
        "connector": kind,
        "mode": "connect_only",
        "config": config,
        "secret_refs": refs,
    }
    return payload


@pytest.mark.django_db
@pytest.mark.parametrize(
    "kind", ["repo", "archive", "vapi", "retell", "livekit", "phone", "saved_agent"]
)
def test_each_connector_parks_and_recovers_approved_inputs(
    kind,
    user,
    workspace,
    request,
    settings,
    monkeypatch,
    alk_source_cli,
    django_capture_on_commit_callbacks,
):
    request.getfixturevalue("_object_storage")
    req = _request(user, workspace)
    req.headers = {"Idempotency-Key": "source-job"}
    payload = _connector_payload(kind, user, workspace)
    document = designs.store_design_document(
        user.organization,
        workspace,
        user,
        name="desk.txt",
        body=b"Refunds within three days require a receipt.",
    )
    payload["source_selection"] = {"document_ids": [str(document.id)]}
    dispatch = []
    monkeypatch.setattr(
        "simulate.temporal.client.start_hosted_harness_gateway_workflow",
        lambda *args, **kwargs: dispatch.append(args[0]),
    )
    monkeypatch.setattr(
        "simulate.services.harness_provider._preflight_source_connectors",
        lambda *args: ([], [], None),
    )
    monkeypatch.setattr(
        "simulate.services.phone_telephony.platform_phone_telephony",
        lambda: {"SIP_OUTBOUND_FROM_NUMBER": "+12025550106"},
    )
    monkeypatch.setattr(
        "simulate.services.harness_usage.require_harness_run_usage",
        lambda *_: pytest.fail("attempted target execution"),
    )
    settings.HARNESS_VERSIONING_ENABLED = False

    def create():
        serializer = HarnessJobCreateSerializer(data=payload)
        serializer.is_valid(raise_exception=True)
        req.validated_data = serializer.validated_data
        response = HostedHarnessProvider().create(req)
        assert response.status_code == 202, response.data
        return response.data["job"]

    public = create()
    assert create()["job_id"] == public["job_id"]
    job = HostedHarnessJob.no_workspace_objects.get(pk=public["job_id"])
    assert job.current_stage == designs.AWAITING_REVIEW and not job.attempts.exists()
    assert str(document.id) not in json.dumps(public, default=str)
    parked = public["metadata"]["source_review"]
    ready = reviews.get_review(req, parked["id"])
    assert (ready["state"], ready["revision"]) == ("ready", parked["revision"])
    assert not dispatch
    with pytest.raises(HostedHarnessError):
        builds.launch_material(job)
    key = ready["preview"]["sources"][0]["pin"]["source_id"]
    args = {
        "expected_revision": ready["revision"],
        "expected_schema_digest": parked["schema_digest"],
        "expected_draft_digest": ready["draft_digest"],
        "idempotency_key": "accept",
        "decisions": {
            "reference_reviews": [{"source_id": key, "visibility": "agent_visible"}]
        },
    }
    for _ in range(2):
        with django_capture_on_commit_callbacks(execute=True):
            reviews.accept_review(req, parked["id"], **args)
    assert dispatch == [str(job.id)]
    job.refresh_from_db()
    material = builds.launch_material(job)
    assert material.receipt["review_id"] == parked["id"] and material.documents[0][
        "id"
    ] == str(document.id)
    assert "build_source_pin" not in guest_job_document(job.payload)["metadata"]
    pin = builds.expected_pin(job)
    raw = json.dumps(pin).encode()
    case = {
        "id": "approved-refund",
        "category": "success",
        "purpose": "Answer a refund question.",
        "starting_facts": [],
        "policy_reference": "Refunds within three days require a receipt.",
        "citations": [{"source_id": key, "page": 1, "row": None}],
    }
    case["proof_digest"] = reviews.schema_digest(case)
    scenario = {
        "scenario_key": "reviewed-refund",
        "name": "policy-refund",
        "use_case": "Test the approved returns policy",
        "fixture": {"reviewed_source": case},
    }
    scenario_bytes = json.dumps(scenario).encode()
    setup_bytes = b'def setup(world):\n    world["route_code"] = "ORCHID-72"\n'
    ready_bytes = b'def ready(world):\n    return world["route_code"] == "ORCHID-72"\n'
    files = {
        "reviewed-source.json": raw,
        "environment-bundle/reviewed-source.json": raw,
        "scenarios.json": json.dumps(
            [{**scenario, "steps": 0, "folder": "scenarios/policy-refund"}]
        ).encode(),
        "scenarios/policy-refund/scenario.json": scenario_bytes,
        "scenarios/policy-refund/setup.py": setup_bytes,
        "scenarios/policy-refund/ready.py": ready_bytes,
        "environment-bundle/scenarios/policy-refund/scenario.json": scenario_bytes,
        "environment-bundle/scenarios/policy-refund/setup.py": setup_bytes,
        "environment-bundle/scenarios/policy-refund/ready.py": ready_bytes,
        "proofs.json": json.dumps(
            {
                "reviewed-refund": {
                    "reviewed_source_digest": builds._semantic_source_digest(
                        case, scenario, setup_bytes, ready_bytes
                    )
                }
            }
        ).encode(),
    }
    manifest = {
        "schema_version": "futureagi.environment-bundle.v2",
        "metadata": {"reviewed_source": pin},
        "digest": "sha256:" + "d" * 64,
        "files": [
            {
                "path": "reviewed-source.json",
                "size": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            },
            {
                "path": "scenarios/policy-refund/scenario.json",
                "size": len(scenario_bytes),
                "sha256": hashlib.sha256(scenario_bytes).hexdigest(),
            },
            {
                "path": "scenarios/policy-refund/setup.py",
                "size": len(setup_bytes),
                "sha256": hashlib.sha256(setup_bytes).hexdigest(),
            },
            {
                "path": "scenarios/policy-refund/ready.py",
                "size": len(ready_bytes),
                "sha256": hashlib.sha256(ready_bytes).hexdigest(),
            },
        ],
    }
    files["environment-bundle/manifest.json"] = json.dumps(manifest).encode()
    trusted_requests = []
    trusted_native = {
        **scenario,
        "scenario_key": "generated-refund",
        "name": "generated-refund",
        "use_case": "Test the behavior of the connected agent",
    }

    def trusted_scenarios(payload):
        trusted_requests.append(payload)
        return {
            "ok": True,
            "result": {
                "snapshot_id": pin["id"],
                "snapshot_digest": pin["digest"],
                "simulation_schema_digest": pin["schema_digest"],
                "bundle_digest": manifest["digest"],
                "scenarios": [
                    {"native": trusted_native, "source_case": case, "scenario": {}}
                ],
            },
        }

    monkeypatch.setattr(harness_sources, "execute_source_request", trusted_scenarios)
    assert builds.check_archive(job, _archive(files)) == pin
    assert trusted_requests[-1]["operation"] == "generate_scenarios"
    assert trusted_requests[-1]["bundle_manifest"] == manifest
    task = (
        job.payload["metadata"].get("agent_name")
        or job.name
        or "the connected agent"
    )
    assert trusted_requests[-1]["environment_requirements"] == {
        "task": f"Test the behavior of {task}"
    }
    for path in (
        "reviewed-source.json",
        "environment-bundle/reviewed-source.json",
        "environment-bundle/manifest.json",
        "proofs.json",
        "scenarios/policy-refund/scenario.json",
        "scenarios/policy-refund/setup.py",
        "scenarios/policy-refund/ready.py",
    ):
        with pytest.raises(HostedHarnessError):
            builds.check_archive(job, _archive({**files, path: b"{}"}))
    if kind in {"repo", "archive"}:
        from simulate.services import hosted_harness_gateway as gateway_service
        from simulate.services.harness_capacity import SandboxCapacity
        from simulate.tests.test_hosted_harness_gateway import _Daytona

        gateway = object.__new__(gateway_service.HostedHarnessGateway)
        gateway.client = _Daytona()
        gateway.snapshot = "alk-hosted-v1"
        gateway.snapshot_digest = "sha256:" + "b" * 64
        settings.ALK_HOSTED_BUILDER_SESSION = kind == "repo"
        monkeypatch.setattr(
            "simulate.services.harness_capacity.configured_capacity",
            lambda *args, **kwargs: SandboxCapacity("selected", 6, 12288, 20, 1),
        )
        monkeypatch.setattr(gateway_service, "_platform_simulator_material", lambda: {})
        monkeypatch.setattr(
            gateway_service.HostedSourceAcquirer,
            "acquire",
            lambda *_: (gateway_service._empty_source_archive(), "a" * 40),
        )
        attempt = gateway.launch(job, endpoint_base_url="https://platform.example.com")
        sandbox = gateway.client.sandbox
        command = sandbox.process.session_request.command
        assert "fi.alk.harness.hosted_entrypoint" not in command
        assert builds.controller_args(job) in command
        assert ("--await-harness" in command) == settings.ALK_HOSTED_BUILDER_SESSION
        assert builds.PRIVATE_PERMISSIONS in " ".join(sandbox.process.exec_calls)
        shared = sandbox.fs.uploads["/work/job.json"]
        assert str(document.id).encode() not in shared
        raw = json.loads(sandbox.fs.uploads[builds.PRIVATE_CONTROLLER_JOB])
        assert raw["metadata"]["source_review_documents"][0]["id"] == str(document.id)
        assert raw["metadata"]["source_review_receipt"]["review_id"] == parked["id"]
        assert sandbox.fs.uploads[builds.SNAPSHOT] == material.snapshot
        if kind == "repo":
            from simulate.models import HostedHarnessConversation

            gateway._start_chat_in_sandbox(
                job=job,
                conversation=HostedHarnessConversation.no_workspace_objects.get(
                    job=job
                ),
                attempt=attempt,
                sandbox=sandbox,
                endpoint_base_url="https://platform.example.com",
            )
            assert (
                builds.controller_args(job) in sandbox.process.session_request.command
            )
        if kind == "repo":
            wrong = {**files, "reviewed-source.json": b"{}"}
            with monkeypatch.context() as patch:
                patch.setattr(
                    gateway_service,
                    "freeze_authoring_archive",
                    lambda *_args, **_kwargs: pytest.fail(
                        "stored an unapproved workspace"
                    ),
                )
                assert (
                    builds.complete(attempt, _archive(wrong)).state
                    == HostedHarnessJob.State.FAILED
                )
        else:
            completed = builds.complete(attempt, _archive(files))
            assert completed.state == HostedHarnessJob.State.COMPLETED
            assert completed.scenario_registrations.count() == 1
            assert completed.payload["metadata"]["authoring_object_key"]


def test_archive_mixes_ordinary_and_grounded_scenarios_without_losing_coverage(monkeypatch):
    from types import SimpleNamespace

    pin = {"id": "s1", "digest": "snapshot", "schema_digest": "schema"}
    monkeypatch.setattr(builds, "expected_pin", lambda _job: pin)
    case = {"id": "rule-a", "category": "success", "policy_reference": "Give code RA-25"}
    expected = [{"source_case": case}, {"source_case": {**case, "id": "old-second-slot"}}]
    monkeypatch.setattr(builds, "_trusted_scenarios", lambda *_: expected)
    job = SimpleNamespace(scenario_count=2)
    files = {"reviewed-source.json": json.dumps(pin).encode()}
    index, proofs = [], {}
    for key, fixture in [("refund", {"reviewed_source": case}), ("greeting", {})]:
        folder = f"scenarios/{key}"
        document = {"name": key, "scenario_key": key, "fixture": fixture}
        index.append({**document, "steps": 0, "folder": folder})
        files[folder + "/scenario.json"] = json.dumps(document).encode()
        files[folder + "/setup.py"] = files[folder + "/ready.py"] = b""
        proofs[key] = (
            {"reviewed_source_digest": builds._semantic_source_digest(case, document, b"", b"")}
            if fixture else {}
        )
    manifest = {
        "schema_version": "futureagi.environment-bundle.v2", "metadata": {"reviewed_source": pin},
        "files": [{"path": k, "size": len(v), "sha256": hashlib.sha256(v).hexdigest()}
                  for k, v in files.items()],
    }
    files.update({"environment-bundle/" + k: v for k, v in files.copy().items()})
    files["environment-bundle/manifest.json"] = json.dumps(manifest).encode()
    files["scenarios.json"] = json.dumps(index).encode()
    files["proofs.json"] = json.dumps(proofs).encode()
    assert builds.check_archive(job, _archive(files)) == pin
    expected[1]["source_case"] = {**case, "id": "rule-b", "policy_reference": "Escalate damage"}
    with pytest.raises(HostedHarnessError, match="source case"):
        builds.check_archive(job, _archive(files))
