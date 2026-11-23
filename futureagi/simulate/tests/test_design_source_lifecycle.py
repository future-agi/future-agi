import hashlib
import json
import os
import sqlite3
import subprocess
from types import SimpleNamespace
from uuid import uuid4

import pytest

from model_hub.models.choices import StatusType
from model_hub.models.develop_dataset import Files, KnowledgeBaseFile
from simulate.models import (
    HarnessEnvironmentRelease,
    HarnessSuiteRelease,
    HostedHarnessJob,
)
from simulate.serializers.harness_release import HarnessDesignRequestSerializer
from simulate.services import (
    harness_designs as designs,
)
from simulate.services import (
    harness_releases as releases,
)
from simulate.services import (
    harness_source_reviews as reviews,
)
from simulate.services import (
    harness_sources,
)
from simulate.services.harness_provider import HostedHarnessProvider, _metadata_for_read
from simulate.services.hosted_harness import HostedHarnessError
from simulate.services.hosted_harness_gateway import guest_job_document
from simulate.tests.test_harness_designs import _archive
from simulate.tests.test_harness_versioning_backfill import (
    storage as _object_storage,  # noqa: F401
)


@pytest.fixture
def alk_source_cli(monkeypatch):
    """Point this integration fixture at the stacked ALK interpreter and optional src/ checkout."""
    python = os.getenv("TEST_ALK_PYTHON")
    if not python:
        pytest.skip("Set TEST_ALK_PYTHON to run the platform and ALK integration.")
    env = {**os.environ, "PYTHONPATH": os.getenv("TEST_ALK_SRC", "")}

    def execute(payload):
        run = subprocess.run(
            [python, "-m", "fi.alk.studio.environment_sources"],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        return json.loads(run.stdout)

    monkeypatch.setattr(harness_sources, "execute_source_request", execute)
    monkeypatch.setattr(harness_sources, "_schedule_staging_expiry", lambda key: None)


def _template(organization, fake, tmp_path):
    db = tmp_path / "world.sqlite"
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE orders(id TEXT PRIMARY KEY, amount INTEGER)")
    content = db.read_bytes()
    path = "world/world.sqlite"
    manifest = {
        "name": "desk",
        "schema_version": releases.ENVIRONMENT_RELEASE_SCHEMA,
        "files": [
            {
                "path": path,
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        ],
    }
    manifest["digest"] = releases._release_digest(manifest)
    env = HarnessEnvironmentRelease.no_workspace_objects.create(
        organization=organization,
        name="desk",
        manifest=manifest,
        digest=manifest["digest"],
        object_key="template/env",
    )
    suite = HarnessSuiteRelease.no_workspace_objects.create(
        organization=organization,
        name="desk-suite",
        environment=env,
        digest="sha256:" + "a" * 64,
        manifest={},
        object_key="template/suite",
        personas=[{"scenario_key": "inherited"}],
    )
    fake.objects["template/env"] = _archive(
        {path: content, "release.json": json.dumps(manifest).encode()}
    )
    fake.objects["template/suite"] = _archive({})
    return suite


def _request(user, workspace):
    return SimpleNamespace(
        user=user,
        organization=user.organization,
        workspace=workspace,
        headers={},
        build_absolute_uri=lambda path: "http://testserver/",
    )


def _create(request, key, **kwargs):
    serializer = HarnessDesignRequestSerializer(
        data={
            "name": "Orders",
            "requirements": "Handle purchase inquiries",
            "idempotency_key": key,
            **kwargs,
        }
    )
    serializer.is_valid(raise_exception=True)
    request.validated_data = serializer.validated_data
    response = HostedHarnessProvider().create_design(request)
    assert response.status_code == 202, response.data
    return HostedHarnessJob.no_workspace_objects.get(idempotency_key=key)


@pytest.mark.django_db
@pytest.mark.parametrize("templated", [False, True])
def test_source_free_design_starts_directly(
    templated, user, workspace, request, tmp_path, monkeypatch
):
    launches = []
    storage = request.getfixturevalue("_object_storage")
    monkeypatch.setattr(
        "simulate.services.harness_usage.require_harness_authoring", lambda org: None
    )
    monkeypatch.setattr(
        "simulate.temporal.client.start_hosted_harness_gateway_workflow",
        lambda *args, **kwargs: launches.append(args[0]),
    )
    template = (
        {"template_suite_id": _template(user.organization, storage, tmp_path).id}
        if templated
        else {}
    )
    job = _create(_request(user, workspace), "source-free", **template)
    assert launches == [str(job.id)]
    launch = designs.design_launch_for(job)
    assert designs.PRIVATE_SNAPSHOT not in dict(launch.uploads)
    assert (
        launch.guest_metadata(job.payload["metadata"], "attempt")["design"][
            "attempt_id"
        ]
        == "attempt"
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("templated", "uploads", "kb"),
    [
        (False, True, False),
        (False, False, True),
        (False, True, True),
        (True, True, True),
    ],
)
def test_review_then_dispatch_one_design(
    templated,
    uploads,
    kb,
    user,
    workspace,
    request,
    tmp_path,
    monkeypatch,
    alk_source_cli,
    django_capture_on_commit_callbacks,
):
    storage = request.getfixturevalue("_object_storage")
    request = _request(user, workspace)
    selection = {"document_ids": [], "knowledge_base": []}
    if uploads:
        doc = designs.store_design_document(
            user.organization,
            workspace,
            user,
            name="orders.csv",
            body=b"id,amount\n001,25\n",
        )
        selection["document_ids"] = [str(doc.id)]
    if kb:
        collection = KnowledgeBaseFile.no_workspace_objects.create(
            organization=user.organization,
            workspace=workspace,
            name="FAQ",
            status=StatusType.COMPLETED.value,
        )
        file = Files.no_workspace_objects.create(
            name="terms.txt", status=StatusType.COMPLETED.value
        )
        collection.files.add(file)
        storage.objects[f"knowledge-base/{collection.id}/{file.id}.txt"] = (
            b"PRIVATE_TERMS_SENTINEL"
        )
        selection["knowledge_base"] = [
            {
                "kind": "knowledge_base",
                "kb_id": str(collection.id),
                "file_ids": [str(file.id)],
            }
        ]
    launches, usage = [], []
    monkeypatch.setattr(
        "simulate.temporal.client.start_hosted_harness_gateway_workflow",
        lambda *args, **kwargs: launches.append(args[0]),
    )
    monkeypatch.setattr(
        "simulate.services.harness_usage.require_harness_authoring",
        lambda org: usage.append(org),
    )
    template = (
        {"template_suite_id": _template(user.organization, storage, tmp_path).id}
        if templated
        else {}
    )
    job = _create(request, "sourced", source_selection=selection, **template)
    assert (
        _create(request, "sourced", source_selection=selection, **template).id == job.id
    )
    assert not launches and not usage and not job.attempts.exists()
    assert job.current_stage == designs.AWAITING_REVIEW
    with pytest.raises(HostedHarnessError, match="Accept"):
        designs.design_launch_for(job)
    ref = job.payload["metadata"]["source_review_id"]
    review = reviews.get_review(request, ref)
    assert review["state"] == "ready", review
    assert "PRIVATE_TERMS_SENTINEL" not in json.dumps(review, default=str)
    preview = review["preview"]
    assert review["scope"]["schema_digest"] == reviews.schema_digest(
        preview["destination_schema"]
    )
    decisions = {"record_reviews": [], "reference_reviews": []}
    for source in preview["sources"]:
        key = source["pin"]["source_id"]
        decision = {"source_id": key, "visibility": "agent_visible"}
        if source["pin"]["role"] == "seed_records":
            decision["fields"] = [
                p["mapping"]
                for p in preview["proposals"]["field_mappings"]
                if p["source_id"] == key
            ]
            decisions["record_reviews"].append(decision)
        else:
            decisions["reference_reviews"].append(decision)
    args = {
        "expected_revision": review["revision"],
        "expected_draft_digest": review["draft_digest"],
        "expected_schema_digest": review["scope"]["schema_digest"],
        "decisions": decisions,
        "idempotency_key": "accept",
    }
    with pytest.raises(HostedHarnessError):
        reviews.accept_review(request, ref, **{**args, "expected_revision": 0})
    for _ in range(2):
        with django_capture_on_commit_callbacks(execute=True):
            reviews.accept_review(request, ref, **args)
    job.refresh_from_db()
    assert launches == [str(job.id)] and len(usage) == 1
    launch = designs.design_launch_for(job)
    assert (
        "--reviewed-snapshot" in launch.command
        and "chmod 0600" in launch.prepare_command
    )
    assert designs.DOCUMENTS_ARCHIVE not in dict(launch.uploads)
    snapshot = json.loads(dict(launch.uploads)[designs.PRIVATE_SNAPSHOT])
    assert snapshot["data"]["snapshot_digest"] == launch.receipt["snapshot_digest"]
    public = _metadata_for_read(job)
    assert "design_source_pin" not in public and "design_source_selection" not in public
    guest = launch.guest_metadata(guest_job_document(job.payload)["metadata"], uuid4())
    assert "source_review_receipt" not in guest and "documents" not in guest["design"]
    private = launch.controller_job({"metadata": guest})["metadata"]
    assert private["source_review_receipt"]["review_id"] == ref
    assert len(private["design"]["documents"]) == len(selection["document_ids"])
    assert designs.PRIVATE_CONTROLLER_JOB in launch.command
    assert designs.PRIVATE_CONTROLLER_JOB in launch.prepare_command
    assert guest["design"]["attempt_id"] and "design_source_pin" not in guest
    assert "source_review_receipt" not in job.payload["metadata"]
    assert "documents" not in public["design"]
    with pytest.raises(HostedHarnessError, match="another design"):
        designs._review_owner(
            HostedHarnessJob(
                organization=user.organization,
                workspace=workspace,
                payload={"metadata": {"design": {"name": "other"}}},
            ),
            job.payload["metadata"]["design_source_pin"],
        )
    if not templated and uploads and kb:
        from simulate.services.harness_capacity import SandboxCapacity
        from simulate.services.hosted_harness_gateway import HostedHarnessGateway
        from simulate.tests.test_hosted_harness_gateway import _Daytona

        gateway = object.__new__(HostedHarnessGateway)
        gateway.client = _Daytona()
        gateway.snapshot = "alk-hosted-v1"
        gateway.snapshot_digest = "sha256:" + "b" * 64
        monkeypatch.setattr(
            "simulate.services.harness_capacity.configured_capacity",
            lambda *args, **kwargs: SandboxCapacity("selected", 6, 12288, 20, 1),
        )
        monkeypatch.setattr(
            "simulate.services.hosted_harness_gateway._platform_simulator_material",
            lambda: {},
        )
        attempt = gateway.launch(job, endpoint_base_url="https://platform.example.com")
        files = gateway.client.sandbox.fs.uploads
        private_job = json.loads(files[designs.PRIVATE_CONTROLLER_JOB])
        shared_job = json.loads(files["/work/job.json"])
        assert private_job["metadata"]["source_review_receipt"]["review_id"] == ref
        assert str(doc.id) not in json.dumps(shared_job)
        assert shared_job["metadata"]["design"]["attempt_id"] == str(attempt.id)
        assert (
            files[designs.PRIVATE_SNAPSHOT]
            == dict(launch.uploads)[designs.PRIVATE_SNAPSHOT]
        )
        assert designs.DOCUMENTS_ARCHIVE not in files
        assert any(
            f"chmod 0600 {designs.PRIVATE_SNAPSHOT} {designs.PRIVATE_CONTROLLER_JOB}"
            in cmd
            for cmd in gateway.client.sandbox.process.exec_calls
        )
        job.state = HostedHarnessJob.State.FAILED
        job.failure = {"code": "design_waiting"}
        job.save(update_fields=["state", "failure"])
        from simulate.models import HostedHarnessConversation
        from simulate.services.hosted_harness import create_hosted_job

        HostedHarnessConversation.no_workspace_objects.filter(job=job).update(
            latest_workspace_object_key="checkpoints/held"
        )
        monkeypatch.setattr(
            "simulate.services.hosted_harness_conversation.load_workspace_archive",
            lambda _: _archive({"design.json": b"{}"}),
        )
        resumed, _ = create_hosted_job(
            user.organization,
            designs.continued_payload(user.organization, job),
            idempotency_key="continued",
            workspace=workspace,
        )
        next_launch = designs.design_launch_for(resumed)
        assert (
            json.loads(dict(next_launch.uploads)[designs.PRIVATE_SNAPSHOT]) == snapshot
        )
        assert next_launch.receipt == launch.receipt
        assert (
            next_launch.guest_metadata(
                guest_job_document(resumed.payload)["metadata"], "next"
            )["design"]["attempt_id"]
            != guest["design"]["attempt_id"]
        )


@pytest.mark.django_db
def test_schema_repair_supersedes_only_its_own_design_review(
    user, workspace, request, monkeypatch, alk_source_cli
):
    request.getfixturevalue("_object_storage")
    api = _request(user, workspace)
    monkeypatch.setattr(
        "simulate.temporal.client.start_hosted_harness_gateway_workflow",
        lambda *a, **kw: pytest.fail("unaccepted design started"),
    )
    document = designs.store_design_document(
        user.organization,
        workspace,
        user,
        name="sample.csv",
        body=b"id,amount\n001,4\n002,pending\n",
    )
    selection = {"document_ids": [str(document.id)], "knowledge_base": []}
    job = _create(api, "ambiguous", source_selection=selection)
    original = reviews.get_review(api, job.payload["metadata"]["source_review_id"])
    assert original["state"] == "needs_repair"
    repair = {
        "source_id": original["preview"]["sources"][0]["pin"]["source_id"],
        "text_fields": ["amount"],
    }
    updated = reviews.create_review(
        api,
        scope=original["scope"],
        **selection,
        schema_repairs=[repair],
        idempotency_key="fixed",
    )
    assert updated["state"] == "ready" and updated["id"] != original["id"]
    job.refresh_from_db()
    assert job.payload["metadata"]["source_review_id"] == updated["id"]
    with pytest.raises(HostedHarnessError) as exc:
        reviews.accept_review(
            api,
            original["id"],
            expected_revision=original["revision"],
            expected_draft_digest=original["draft_digest"],
            expected_schema_digest=original["scope"]["schema_digest"],
            decisions={},
            idempotency_key="stale",
        )
    assert exc.value.code == "source_review_stale"


@pytest.mark.django_db
def test_stale_attempt_cannot_finish_current_design(
    user, workspace, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "simulate.services.harness_usage.require_harness_authoring", lambda org: None
    )
    monkeypatch.setattr(
        "simulate.temporal.client.start_hosted_harness_gateway_workflow",
        lambda *args: None,
    )
    job = _create(_request(user, workspace), "attempts")
    job.current_attempt_number = 2
    job.save(update_fields=["current_attempt_number"])
    stale = SimpleNamespace(id=uuid4(), job_id=job.id, attempt_number=1)
    assert designs.complete_design(stale, tmp_path).terminal_at is None
    active = SimpleNamespace(id=uuid4(), job_id=job.id, attempt_number=2)
    (tmp_path / "result.json").write_text(
        json.dumps({"status": "completed", "attempt_id": str(stale.id)})
    )
    monkeypatch.setattr(
        designs,
        "register_release",
        lambda *args, **kwargs: pytest.fail("Stale output registered"),
    )
    finished = designs.complete_design(active, tmp_path)
    assert finished.failure["code"] == "design_attempt_mismatch"


@pytest.mark.django_db
def test_dispatch_failure_can_retry_without_duplicate_start(
    user, workspace, monkeypatch
):
    from simulate.services.hosted_harness import create_hosted_job

    payload = designs.design_payload(
        user.organization, name="desk", requirements="", modality="chat"
    )
    job, _ = create_hosted_job(
        user.organization, payload, idempotency_key="dispatch", workspace=workspace
    )
    job.current_stage = designs.PENDING_DISPATCH
    job.save(update_fields=["current_stage"])
    calls = []

    def start(*args, **kwargs):
        calls.append((args, kwargs))
        if len(calls) == 1:
            raise ConnectionError()

    monkeypatch.setattr(
        "simulate.temporal.client.start_hosted_harness_gateway_workflow", start
    )
    with pytest.raises(HostedHarnessError) as exc:
        designs.dispatch_reviewed_design(job.id, "http://testserver")
    assert exc.value.retryable
    job.refresh_from_db()
    assert job.current_stage == designs.PENDING_DISPATCH
    for _ in range(2):
        designs.dispatch_reviewed_design(job.id, "http://testserver")
    assert len(calls) == 2 and calls[0] == calls[1]


@pytest.mark.django_db
@pytest.mark.parametrize("wrong", ["environment", "suite", "both"])
def test_wrong_release_source_pointer_creates_no_objects_or_rows(
    wrong, organization, tmp_path, monkeypatch
):
    pin = {"id": "accepted", "digest": "sha256:" + "f" * 64}

    def write_manifest(directory, filename, schema, files, **fields):
        directory.mkdir()
        manifest = {"schema_version": schema, "name": "desk", "files": [], **fields}
        for name, data in files.items():
            content = json.dumps(data).encode()
            (directory / name).write_bytes(content)
            manifest["files"].append(
                {
                    "path": name,
                    "size": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
            )
        manifest["digest"] = releases._release_digest(manifest)
        (directory / filename).write_text(json.dumps(manifest))
        return manifest["digest"]

    env_dir, suite_dir = tmp_path / "environment", tmp_path / "suite"
    env = write_manifest(
        env_dir,
        "release.json",
        releases.ENVIRONMENT_RELEASE_SCHEMA,
        {},
        metadata={} if wrong in ("environment", "both") else {"reviewed_source": pin},
    )
    suite = write_manifest(
        suite_dir,
        "suite.json",
        releases.SUITE_RELEASE_SCHEMA,
        {
            "provision.json": {"personas": []},
            "proofs.json": {"runtime": {"engine": "postgres"}},
        },
        environment={"digest": env},
        scenarios=[],
        metadata={} if wrong in ("suite", "both") else {"reviewed_source": pin},
    )
    monkeypatch.setattr(
        releases,
        "_upload",
        lambda *args: pytest.fail("Release upload before pin verification"),
    )
    with pytest.raises(HostedHarnessError) as refused:
        releases.register_release(
            env_dir,
            suite_dir,
            organization=organization,
            expected_design={"source": pin, "digests": (env, suite)},
        )
    assert refused.value.code == "source_snapshot_changed"
    assert not HarnessEnvironmentRelease.no_workspace_objects.filter(
        digest=env
    ).exists()
    assert not HarnessSuiteRelease.no_workspace_objects.filter(digest=suite).exists()
