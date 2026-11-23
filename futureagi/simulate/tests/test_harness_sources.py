import base64
import hashlib
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from model_hub.models.choices import StatusType
from model_hub.models.develop_dataset import Files, KnowledgeBaseFile
from simulate.services import harness_source_generation, harness_sources
from simulate.services import harness_source_reviews as reviews
from simulate.services.harness_versioning import archives, drafts, versions
from simulate.services.hosted_harness import HostedHarnessError
from simulate.tests.test_harness_versioning_backfill import (
    storage as _storage_fixture,  # noqa: F401
)
from simulate.tests.test_harness_versioning_record import _save_drafts
from simulate.tests.test_harness_versioning_rollback import _built

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def versioning_on(settings):
    settings.HARNESS_VERSIONING_ENABLED = True


@pytest.fixture(autouse=True)
def staging_expiry_scheduled(monkeypatch):
    schedule = harness_sources._schedule_staging_expiry
    monkeypatch.setattr(harness_sources, "_schedule_staging_expiry", lambda key: None)
    return schedule


def test_kb_reference_import_writes_private_snapshot_and_environment_draft(
    user, workspace, request, monkeypatch
):
    storage = request.getfixturevalue("_storage_fixture")
    environment = _built(user, workspace, "source-import")
    kb = KnowledgeBaseFile.no_workspace_objects.create(
        name="Support docs",
        organization=user.organization,
        workspace=workspace,
        status=StatusType.COMPLETED.value,
    )
    file = Files.no_workspace_objects.create(
        name="guide.txt",
        uploaded_url="http://invalid.invalid/ignored",
        status=StatusType.COMPLETED.value,
    )
    kb.files.add(file)
    storage.objects[f"knowledge-base/{kb.id}/{file.id}.txt"] = b"REFERENCE_SENTINEL"
    request = SimpleNamespace(
        user=user,
        organization=user.organization,
        workspace=workspace,
        headers={},
    )
    prepared_draft = {"draft_digest": "sha256:prepared", "sources": []}
    trusted_scenarios = []

    def alk(payload):
        if payload["operation"] == "prepare":
            assert "destination_schema" not in payload
            content = base64.b64decode(payload["sources"][0]["content_base64"])
            prepared_draft["sources"] = [
                {
                    "pin": {
                        "source_id": payload["sources"][0]["source_id"],
                        "source_sha256": "sha256:"
                        + hashlib.sha256(content).hexdigest(),
                        "parser_revision": "test",
                        "role": "reference",
                    }
                }
            ]
            return {"ok": True, "result": {"draft": prepared_draft}}
        if payload["operation"] == "generate_scenarios":
            assert payload["requested_count"] == len(trusted_scenarios)
            return {
                "ok": True,
                "result": {
                    "snapshot_id": "snapshot-kb",
                    "snapshot_digest": "sha256:" + "a" * 64,
                    "scenarios": trusted_scenarios,
                },
            }
        pin = prepared_draft["sources"][0]["pin"]
        return {
            "ok": True,
            "result": {
                "snapshot": {
                    "version": "environment-inputs.snapshot.v2",
                    "digest": "sha256:envelope",
                    "data": {
                        "snapshot_digest": "sha256:" + "a" * 64,
                        "snapshot_id": "snapshot-kb",
                        "sources": [{"pin": pin}],
                        "diagnostics": [],
                    },
                },
                "compiled": {
                    "agent_environments": [
                        {"kind": "retrieval", "documents": ["REFERENCE_SENTINEL"]}
                    ],
                    "evaluator_inputs": {
                        "policies": [],
                        "records": [],
                        "references": [
                            {
                                "source_id": pin["source_id"],
                                "documents": ["REFERENCE_SENTINEL"],
                            }
                        ],
                        "rules": [],
                    },
                },
            },
        }

    monkeypatch.setattr(harness_sources, "execute_source_request", alk)
    basis = drafts.draft_refs(environment)["environment"]
    prepared = harness_sources.prepare_source_import(
        request,
        environment,
        {"kb_id": str(kb.id), "file_ids": [str(file.id)]},
        expected_environment_version_id=str(environment.environment_version_id),
        expected_draft={"id": basis.id, "revision": basis.revision},
    )
    accept_args = {
        "import_ref": prepared["import_ref"],
        "expected_environment_version_id": str(environment.environment_version_id),
        "expected_draft": {"id": basis.id, "revision": basis.revision},
        "expected_draft_digest": prepared["draft_digest"],
        "review": {
            "expected_draft_digest": prepared["draft_digest"],
            "record_reviews": [],
            "policy_reviews": [],
            "reference_reviews": [
                {
                    "source_id": prepared_draft["sources"][0]["pin"]["source_id"],
                    "audience": {"tested_agent": True, "participant_ids": []},
                }
            ],
        },
    }
    accepted = harness_sources.accept_source_import(request, environment, **accept_args)
    assert accepted["draft"]["revision"] == 1
    assert accepted["validation"] == {
        "status": "structural_only",
        "affected_scenarios": [
            {
                "scenario_key": "book-ride-airport",
                "name": "book_ride_airport",
                "executed": False,
            },
            {
                "scenario_key": "cancel-active-ride",
                "name": "cancel_active_ride",
                "executed": False,
            },
        ],
    }
    assert (
        harness_sources.accept_source_import(request, environment, **accept_args)[
            "draft"
        ]
        == accepted["draft"]
    )
    generation = reviews.HarnessSourceScenarioGeneration.objects.get(
        review_id=prepared["import_ref"]
    )
    assert (
        reviews.get_review(request, prepared["import_ref"])["scenario_generation"][
            "state"
        ]
        == "queued"
    )
    original_suite_id = environment.suite_version_id
    templates = versions.suite_documents(drafts.edit_sides(environment)["suite"])
    source_id = prepared_draft["sources"][0]["pin"]["source_id"]
    for number, template in enumerate(templates, start=1):
        case = {
            "id": f"grounded-case-{number}",
            "purpose": "Use the approved support reference.",
            "citations": [{"source_id": source_id, "page": 1, "row": None}],
            "proof_digest": "sha256:" + str(number) * 64,
        }
        native = {
            **{key: value for key, value in template.items() if key != "folder"},
            "scenario_key": f"grounded-{number}",
            "name": f"grounded_{number}",
            "fixture": {"origin": "seed", "reviewed_source": case},
        }
        trusted_scenarios.append(
            {"scenario": {}, "native": native, "source_case": case}
        )
    completed = harness_source_generation.process(generation.id)
    assert completed["state"] == "completed"
    assert len(completed["generated_scenarios"]) == len(templates)
    environment.refresh_from_db()
    assert environment.suite_version_id == original_suite_id
    suite_draft = drafts.fresh_draft(environment, "suite")
    assert len((suite_draft.documents or {})["scenarios"]) == len(templates) * 2
    suite_draft.delete()
    source_draft = drafts.fresh_draft(environment, "environment")
    files = archives.read_archive(archives.get_archive(source_draft.archive_object_key))
    assert b"REFERENCE_SENTINEL" in files["sources/tested-agent.json"]
    assert b"REFERENCE_SENTINEL" not in files["sources/manifest.json"]
    saved = _save_drafts(environment, user=user)
    assert saved["saved"]["environment"]["id"] == str(
        environment.environment_version_id
    )
    assert drafts.fresh_draft(environment, "environment") is None
    private_keys = [
        key
        for key in storage.objects
        if key.startswith("environment-source-snapshots/")
    ]
    assert len(private_keys) == 2
    assert any(b"REFERENCE_SENTINEL" in storage.objects[key] for key in private_keys)
    for key in private_keys:
        if b'"snapshot"' in storage.objects[key]:
            assert key.endswith(
                f"/{hashlib.sha256(storage.objects[key]).hexdigest()}.json"
            )
        else:
            aggregate = json.loads(storage.objects[key])
            payload = {
                "schema_version": aggregate["schema_version"],
                "private_snapshot_digests": aggregate["private_snapshot_digests"],
                "source_snapshot_digests": aggregate["source_snapshot_digests"],
            }
            assert key.endswith(
                f"/{hashlib.sha256(harness_sources._canonical(payload)).hexdigest()}.json"
            )
    run = SimpleNamespace(
        environment_id=environment.id,
        organization_id=environment.organization_id,
        workspace_id=workspace.id,
        environment_version=source_draft,
    )
    resolved = harness_sources.resolve_evaluator_sources(run)
    assert resolved["snapshot_digest"] == accepted["snapshot_digest"]
    assert b"REFERENCE_SENTINEL" in harness_sources._canonical(resolved)
    leaf_key = next(
        key for key in private_keys if b'"snapshot"' in storage.objects[key]
    )
    storage.objects[leaf_key] += b" "
    with pytest.raises(HostedHarnessError) as caught:
        harness_sources.resolve_evaluator_sources(run)
    assert caught.value.code == "source_snapshot_invalid"


def test_upload_prepare_keeps_csv_and_json_in_one_review(
    user, workspace, request, monkeypatch
):
    request.getfixturevalue("_storage_fixture")
    environment = _built(user, workspace, "source-multi-upload")
    seen = {}

    version = environment.environment_version
    version.documents["contract"]["data_schema"] = {
        "customers": {"customer_id": "TEXT"},
        "orders": {"order_id": "INTEGER"},
    }
    version.save(update_fields=["documents"])

    def alk(payload):
        seen.update(payload)
        return {
            "ok": True,
            "result": {
                "draft": {
                    "draft_digest": "sha256:prepared",
                    "sources": [],
                    "proposals": {},
                    "diagnostics": [],
                }
            },
        }

    monkeypatch.setattr(harness_sources, "execute_source_request", alk)
    basis = drafts.draft_refs(environment)["environment"]

    def upload(target_table=None):
        return harness_sources.prepare_upload_import(
            environment,
            [
                SimpleUploadedFile("customers.csv", b"customer_id\nC17\n", "text/csv"),
                SimpleUploadedFile(
                    "orders.json",
                    b'[{"order_id":"O1","buyer_id":"C17"}]',
                    "application/json",
                ),
            ],
            purpose="seed_records",
            expected_environment_version_id=str(environment.environment_version_id),
            expected_draft={"id": basis.id, "revision": basis.revision},
            target_table=target_table,
        )

    ambiguous = upload()
    assert ambiguous["state"] == "needs_repair"
    assert ambiguous["draft_digest"] == ""
    assert ambiguous["draft"]["destination_tables"] == ["customers", "orders"]
    assert not seen
    request = SimpleNamespace(
        user=user, organization=user.organization, workspace=workspace, headers={}
    )
    reloaded = reviews.get_review(request, ambiguous["import_ref"])
    args = {
        "expected_revision": reloaded["revision"],
        "idempotency_key": "choose-orders",
        "target_table": "orders",
    }
    prepared = reviews.reprepare_review(request, reloaded["id"], **args)
    assert prepared["id"] == reloaded["id"]
    assert prepared["revision"] == reloaded["revision"] + 1
    assert reviews.reprepare_review(request, reloaded["id"], **args) == prepared
    with pytest.raises(HostedHarnessError, match="changed"):
        reviews.reprepare_review(
            request, reloaded["id"], **{**args, "target_table": "customers"}
        )
    other = _built(user, workspace, "source-other-environment")
    with pytest.raises(HostedHarnessError, match="not found"):
        reviews.reprepare_review(
            request,
            reloaded["id"],
            environment=other,
            expected_draft=drafts.NO_DRAFT,
            **args,
        )
    assert seen["destination_schema"] == {
        "fields": [{"name": "order_id", "data_type": "integer", "required": False}]
    }

    assert prepared["draft_digest"] == "sha256:prepared"
    assert [source["filename"] for source in seen["sources"]] == [
        "source-1.csv",
        "source-2.json",
    ]
    assert {source["role"] for source in seen["sources"]} == {"seed_records"}


def test_staging_expiry_uses_the_review_lifetime(monkeypatch, staging_expiry_scheduled):
    from simulate.tasks.environment_source_imports import (
        expire_environment_source_import,
    )

    calls = []
    monkeypatch.setattr(
        expire_environment_source_import,
        "apply_async",
        lambda **kwargs: calls.append(kwargs),
    )
    key = f"{harness_sources.STAGING_PREFIX}/org/env/{'a' * 64}.json"

    staging_expiry_scheduled(key)

    assert calls == [
        {
            "args": [key],
            "task_id": "environment-source-import-expiry-"
            + hashlib.sha256(key.encode()).hexdigest(),
            "id_conflict_policy": harness_sources.WorkflowIDConflictPolicy.USE_EXISTING,
            "start_delay": timedelta(
                seconds=harness_sources.IMPORT_REF_MAX_AGE_SECONDS + 60
            ),
            "dispatch_timeout_seconds": 30,
        }
    ]


def test_prepare_does_not_store_originals_without_scheduled_expiry(
    user, workspace, request, monkeypatch
):
    storage = request.getfixturevalue("_storage_fixture")
    environment = _built(user, workspace, "source-expiry-required")
    monkeypatch.setattr(
        harness_sources,
        "execute_source_request",
        lambda payload: {
            "ok": True,
            "result": {
                "draft": {
                    "draft_digest": "sha256:prepared",
                    "sources": [],
                    "proposals": {},
                    "diagnostics": [],
                }
            },
        },
    )

    def unavailable(_key):
        raise ConnectionError("Temporal unavailable")

    monkeypatch.setattr(
        harness_sources,
        "_schedule_staging_expiry",
        unavailable,
    )
    basis = drafts.draft_refs(environment)["environment"]

    with pytest.raises(HostedHarnessError) as caught:
        harness_sources.prepare_upload_import(
            environment,
            [SimpleUploadedFile("guide.txt", b"private", "text/plain")],
            purpose="reference",
            expected_environment_version_id=str(environment.environment_version_id),
            expected_draft={"id": basis.id, "revision": basis.revision},
        )

    assert caught.value.code == "source_processor_unavailable"
    assert not any(
        key.startswith(f"{harness_sources.STAGING_PREFIX}/") for key in storage.objects
    )


def test_safe_version_documents_exclude_private_source_fields():
    environment = {
        "sources/manifest.json": harness_sources._canonical(
            {
                "schema_version": 1,
                "snapshot_digest": "sha256:aggregate",
                "source_count": 1,
                "sources": [
                    {
                        "source_id": "guide",
                        "purpose": "reference",
                        "private_key": "must-not-escape",
                    }
                ],
                "tested_agent_projection_sha256": "sha256:public",
                "validation_status": "accepted",
                "private_snapshot_digests": ["sha256:private"],
            }
        )
    }

    documents = versions._source_documents(environment)

    assert documents == {
        "schema_version": 1,
        "snapshot_digest": "sha256:aggregate",
        "source_count": 1,
        "sources": [{"source_id": "guide", "purpose": "reference"}],
        "tested_agent_projection_sha256": "sha256:public",
        "validation_status": "accepted",
    }
    assert "private" not in json.dumps(documents)


def test_evaluator_resolution_deduplicates_a_batch_snapshot(monkeypatch):
    leaf = harness_sources._canonical(
        {"compiled": {"evaluator_inputs": {"references": ["shared"]}}}
    )
    leaf_digest = "sha256:" + hashlib.sha256(leaf).hexdigest()
    source_digests = {"first": leaf_digest, "second": leaf_digest}
    aggregate_payload = {
        "schema_version": 1,
        "private_snapshot_digests": [leaf_digest],
        "source_snapshot_digests": source_digests,
    }
    aggregate_digest = (
        "sha256:"
        + hashlib.sha256(harness_sources._canonical(aggregate_payload)).hexdigest()
    )
    aggregate = harness_sources._canonical(
        {**aggregate_payload, "snapshot_digest": aggregate_digest}
    )
    monkeypatch.setattr(
        harness_sources.archives,
        "get_archive",
        lambda key: b"archive",
    )
    monkeypatch.setattr(
        harness_sources.archives,
        "read_archive",
        lambda body: {
            "sources/manifest.json": harness_sources._canonical(
                {
                    "snapshot_digest": aggregate_digest,
                    "sources": [
                        {"source_id": "first"},
                        {"source_id": "second"},
                    ],
                }
            )
        },
    )
    monkeypatch.setattr(
        harness_sources,
        "_get_private",
        lambda key: leaf if leaf_digest.removeprefix("sha256:") in key else aggregate,
    )
    version = SimpleNamespace(
        job_id="environment", organization_id="organization", archive_object_key="key"
    )
    run = SimpleNamespace(
        environment_id="environment",
        organization_id="organization",
        environment_version=version,
    )

    resolved = harness_sources.resolve_evaluator_sources(run)

    assert resolved["sources"] == [
        {
            "source_ids": ["first", "second"],
            "evaluator_inputs": {"references": ["shared"]},
        }
    ]


def test_participant_visibility_fails_closed(user, workspace, request, monkeypatch):
    request.getfixturevalue("_storage_fixture")
    environment = _built(user, workspace, "source-participant")
    monkeypatch.setattr(
        harness_sources,
        "prepare_draft",
        lambda *args: {"draft_digest": "sha256:prepared"},
    )
    basis = {"id": None, "revision": 0}
    prepared = harness_sources.prepare_upload_import(
        environment,
        [SimpleUploadedFile("guide.txt", b"private")],
        purpose=None,
        expected_environment_version_id=environment.environment_version_id,
        expected_draft=basis,
    )
    request = SimpleNamespace(
        user=user, organization=user.organization, workspace=workspace
    )
    with pytest.raises(HostedHarnessError) as caught:
        harness_sources.accept_source_import(
            request,
            environment,
            import_ref=prepared["import_ref"],
            expected_environment_version_id=str(environment.environment_version_id),
            expected_draft=basis,
            expected_draft_digest="sha256:prepared",
            review={
                "record_reviews": [],
                "policy_reviews": [],
                "reference_reviews": [
                    {
                        "source_id": "src_one",
                        "audience": {
                            "tested_agent": False,
                            "participant_ids": ["buyer"],
                        },
                    }
                ],
            },
        )
    assert caught.value.code == "participant_isolation_unavailable"
    assert drafts.fresh_draft(environment, "environment") is None
