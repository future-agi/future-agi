import base64
import hashlib
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from model_hub.models.choices import StatusType
from model_hub.models.develop_dataset import Files, KnowledgeBaseFile
from simulate.models import HostedHarnessJob
from simulate.services import harness_source_generation, harness_sources
from simulate.services import harness_source_reviews as reviews
from simulate.services.harness_source_summary import resolve_legacy_filenames
from simulate.services.harness_versioning import archives, assembly, drafts, versions
from simulate.services.harness_versioning.versions import record_authoring
from simulate.services.hosted_harness import HostedHarnessError
from simulate.tests.test_harness_unpinned_runs import _held
from simulate.tests.test_harness_versioning_backfill import (
    _agent_version,
    _definition,
)
from simulate.tests.test_harness_versioning_backfill import (
    storage as _storage_fixture,  # noqa: F401
)
from simulate.tests.test_harness_versioning_record import _authoring, _environment
from simulate.tests.test_harness_versioning_rollback import _built, _rows

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def versioning_on(settings):
    settings.HARNESS_VERSIONING_ENABLED = True


@pytest.fixture(autouse=True)
def staging_expiry_scheduled(monkeypatch):
    schedule = harness_sources._schedule_staging_expiry
    monkeypatch.setattr(harness_sources, "_schedule_staging_expiry", lambda key: None)
    return schedule


@pytest.mark.parametrize("certified", [True, False])
def test_kb_reference_import_writes_private_snapshot_and_environment_draft(
    user, workspace, request, monkeypatch, auth_client, certified
):
    storage = request.getfixturevalue("_storage_fixture")
    environment = _environment(user, workspace, "source-import")
    agent = _agent_version(_definition(user, workspace))
    _held(agent)
    environment.agent_version = agent
    environment.save(update_fields=["agent_version", "updated_at"])
    agent_source = "b" * 64
    old_seal = "sha256:" + "c" * 64
    new_seal = "sha256:" + "d" * 64
    previous_pin = {
        "id": "previous-source",
        "digest": "sha256:" + "e" * 64,
        "schema_digest": "sha256:" + "f" * 64,
    }
    pin_path = "reviewed-source.json"
    bundled_pin_path = f"environment-bundle/{pin_path}"

    def certifies(digest):
        return json.dumps(
            {"compiler": {"bundle_digest": digest}, "source": {"digest": agent_source}}
        )

    sealed = {
        assembly.SEALED_MANIFEST: json.dumps(
            {
                "digest": old_seal,
                "provenance": {"source_digest": agent_source},
                "metadata": {"reviewed_source": previous_pin},
            }
        ),
        pin_path: harness_sources._canonical(previous_pin).decode(),
        bundled_pin_path: harness_sources._canonical(previous_pin).decode(),
    }
    if certified:
        sealed[assembly.CERTIFICATE] = certifies(old_seal)
    record_authoring(
        environment,
        _authoring(sealed=False, **sealed),
        origin="authoring",
    )
    environment.refresh_from_db()
    _rows(environment)
    default_authoring = drafts._default_authoring(environment)
    kb = KnowledgeBaseFile.no_workspace_objects.create(
        name="Support docs",
        organization=user.organization,
        workspace=workspace,
        status=StatusType.PARTIAL_COMPLETED.value
        if certified
        else StatusType.COMPLETED.value,
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
        if payload["operation"] == "reseal_scenarios":
            assert generation.__class__.objects.get(pk=generation.id).state == "running"
            assert payload["mode"] == "existing_environment"
            assert payload["generated_scenario_keys"] == [
                case["native"]["scenario_key"] for case in trusted_scenarios
            ]
            assert payload["schema_digest"] == generation.review.basis["schema_digest"]
            tree = archives.read_archive(
                base64.b64decode(payload["authoring_archive_base64"])
            )
            assert (
                tree["contract.json"] == default_authoring.environment["contract.json"]
            )
            assert assembly.manifest_facts(tree) == (old_seal, agent_source)
            snapshot = payload["snapshot"]["data"]
            pin = {
                "id": snapshot["snapshot_id"],
                "digest": snapshot["snapshot_digest"],
                "schema_digest": payload["schema_digest"],
            }
            assert snapshot["snapshot_id"] == "snapshot-kb"
            assert tree[pin_path] == harness_sources._canonical(pin)
            assert json.loads(tree[bundled_pin_path]) == previous_pin
            sealed = {
                assembly.SEALED_MANIFEST: json.dumps(
                    {
                        "digest": new_seal,
                        "provenance": {"source_digest": agent_source},
                        "metadata": {
                            "sealed_from": drafts.sealed_from(tree),
                            "reviewed_source": pin,
                        },
                    }
                ).encode(),
                bundled_pin_path: harness_sources._canonical(pin),
            }
            if certified:
                sealed[assembly.CERTIFICATE] = certifies(new_seal).encode()
            for case in trusted_scenarios:
                folder = f"scenarios/{case['native']['name']}"
                for name in ("scenario.json", "setup.py", "ready.py"):
                    path = f"{folder}/{name}"
                    sealed[f"environment-bundle/{path}"] = tree[path]
            updated = archives.split_authoring(tree)
            return {
                "ok": True,
                "result": {
                    "authoring_archive_base64": base64.b64encode(
                        archives.pack_archive(
                            archives.compose(updated.environment, updated.suite, sealed)
                        )
                    ).decode(),
                    "bundle_digest": new_seal,
                    "reviewed_source": pin,
                },
            }
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
    summary = harness_sources.source_summary(
        environment, reviewer_scope=reviews.require_source_scope(request, write=False)
    )
    assert summary["sources"][0]["filename"] == "guide.txt"
    assert summary["sources"][0]["unsaved"] is True
    assert summary["generation_review_id"] == prepared["import_ref"]
    assert "guide.txt" not in json.dumps(harness_sources.source_summary(environment))
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
    original_environment_id = environment.environment_version_id
    drafts_path = f"/simulate/api/harness-environments/{environment.id}/drafts/"
    source_draft = drafts.fresh_draft(environment, "environment")
    source_only = [
        {
            "piece": "environment",
            "expected_parent_id": str(source_draft.parent_id),
            "expected_revision": source_draft.revision,
        }
    ]

    def save(pieces):
        return auth_client.post(
            drafts_path + "save/",
            {"drafts": pieces, "idempotency_key": "save-reviewed-sources"},
            format="json",
        )

    for status, error in (
        ("queued", "source_generation_in_progress"),
        ("failed", "source_generation_not_ready"),
        ("stale", "source_generation_not_ready"),
        ("running", "source_generation_in_progress"),
    ):
        generation.state = status
        generation.save(update_fields=["state"])
        response = save(source_only)
        assert response.status_code == 409
        assert response.json()["error"] == error
    environment.refresh_from_db()
    assert environment.environment_version_id == original_environment_id
    assert environment.suite_version_id == original_suite_id
    templates = versions.suite_documents(drafts.edit_sides(environment)["suite"])
    source_id = prepared_draft["sources"][0]["pin"]["source_id"]
    for number, template in enumerate(templates, start=1):
        case = {
            "id": f"grounded-case-{number}",
            "purpose": "Use the approved support reference.",
            "policy_reference": f"Support reference {number}.",
            "starting_facts": [],
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

    def authored_patch(owner, files, pin, cases):
        from simulate.services.harness_reviewed_builds import _semantic_source_digest

        patch, index, proofs = {}, [], {}
        for item in trusted_scenarios:
            native = item["native"]
            folder = "scenarios/" + native["name"]
            setup = b"def setup(world): pass\n"
            ready = b"def ready(world): return True\n"
            patch.update(
                {
                    folder + "/scenario.json": json.dumps(native).encode(),
                    folder + "/setup.py": setup,
                    folder + "/ready.py": ready,
                }
            )
            index.append(
                {**native, "steps": len(native.get("solution") or []), "folder": folder}
            )
            proofs[native["scenario_key"]] = {
                "holds": True,
                "ready": True,
                "solvable": True,
                "not_vacuous": True,
                "reviewed_source_digest": _semantic_source_digest(
                    item["source_case"], native, setup, ready
                ),
            }
        return {
            **patch,
            "scenarios.json": json.dumps(index).encode(),
            "proofs.json": json.dumps(proofs).encode(),
        }

    monkeypatch.setattr(
        "simulate.services.harness_source_writer.author_additions", authored_patch
    )
    completed = harness_source_generation.process(generation.id)
    assert completed["state"] == "completed"
    assert len(completed["generated_scenarios"]) == len(templates)
    environment.refresh_from_db()
    assert environment.suite_version_id == original_suite_id
    suite_draft = drafts.fresh_draft(environment, "suite")
    assert len((suite_draft.documents or {})["scenarios"]) == len(templates) * 2
    bundle = suite_draft.documents["workspace_sealed"]
    assert bundle["resealed"] is True
    assert bundle["environment_hash"] == source_draft.hash
    assert bundle["suite_hash"] == suite_draft.hash
    sealed = archives.read_archive(archives.get_archive(bundle["key"]))
    assert assembly.certifies_bundle(sealed) is certified
    assert assembly.manifest_facts(sealed) == (new_seal, agent_source)
    accepted_pin = {
        "id": "snapshot-kb",
        "digest": "sha256:" + "a" * 64,
        "schema_digest": generation.review.basis["schema_digest"],
    }
    assert (
        json.loads(sealed[assembly.SEALED_MANIFEST])["metadata"]["reviewed_source"]
        == accepted_pin
    )
    assert json.loads(sealed[bundled_pin_path]) == accepted_pin
    files = archives.read_archive(archives.get_archive(source_draft.archive_object_key))
    assert json.loads(files[pin_path]) == accepted_pin
    assert json.loads(default_authoring.environment[pin_path]) == previous_pin
    assert b"REFERENCE_SENTINEL" in files["sources/tested-agent.json"]
    assert b"REFERENCE_SENTINEL" not in files["sources/manifest.json"]
    stale_response = save(source_only)
    assert stale_response.status_code == 409
    assert stale_response.json()["error"] == "draft_changed"
    refreshed = auth_client.get(drafts_path).json()
    paired = [
        {
            "piece": piece,
            "expected_parent_id": refreshed[piece]["parent_id"],
            "expected_revision": refreshed[piece]["revision"],
        }
        for piece in ("environment", "suite")
    ]
    mismatched = [
        paired[0],
        {**paired[1], "expected_revision": suite_draft.revision + 1},
    ]
    assert save(mismatched).status_code == 409
    other = _agent_version(_definition(user, workspace, name="Different fixture agent"))
    owner = HostedHarnessJob.no_workspace_objects.filter(pk=environment.id)
    owner.update(agent_version=other)
    assert save(paired).status_code == 409
    owner.update(agent_version=agent)
    saved_response = save(paired)
    assert saved_response.status_code == 200, saved_response.json()
    saved = saved_response.json()
    environment.refresh_from_db()
    assert saved["saved"]["environment"]["id"] == str(
        environment.environment_version_id
    )
    assert saved["saved"]["suite"]["id"] == str(environment.suite_version_id)
    assert environment.suite_version_id != original_suite_id
    assert environment.payload["metadata"]["default_pair_sealed"] is True
    assert not HostedHarnessJob.no_workspace_objects.filter(
        environment=environment
    ).exists()
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


def test_missing_world_snapshot_stops_before_sandbox_launch(monkeypatch):
    from simulate.services import harness_source_writer as writer

    monkeypatch.setattr(
        writer.gateway, "HostedHarnessGateway", lambda: pytest.fail("sandbox requested")
    )
    with pytest.raises(HostedHarnessError) as exc:
        writer.author_additions(
            None, {"environment-bundle/manifest.json": b"{}"}, {}, []
        )
    assert exc.value.code == "source_world_snapshot_missing"


@pytest.mark.parametrize("writer_exit", [0, 1])
def test_writer_cleanup_preserves_result_or_writer_error(monkeypatch, writer_exit):
    from unittest.mock import Mock

    from simulate.services import harness_source_writer as writer

    provider = Mock(name="provider", max_egress_domains=8)
    provider.name = "e2b"
    sandbox = provider.create.return_value
    sandbox.delete.side_effect = ConnectionError("sandbox cleanup interrupted")
    sandbox.process.exec.return_value.exit_code = writer_exit
    files = {"scenarios.json": b"[]", "manifest.json": b"{}"}
    sandbox.fs.download_file.return_value = archives.pack_archive(files)
    monkeypatch.setattr(
        writer.gateway, "HostedHarnessGateway", lambda: SimpleNamespace(client=provider)
    )
    monkeypatch.setattr(writer.gateway, "_platform_simulator_material", lambda: {})
    monkeypatch.setattr(writer.gateway, "_alk_source_overlay", lambda: None)
    owner = SimpleNamespace(
        pk="test", payload={"runtime": {"cpu_units": 2, "memory_mb": 2048}}
    )
    if writer_exit:
        with pytest.raises(HostedHarnessError) as exc:
            writer.author_additions(owner, files, {}, [])
        assert exc.value.code == "source_writer_failed"
    else:
        assert writer.author_additions(owner, files, {}, []) == files
    sandbox.delete.assert_called_once()


@pytest.fixture
def queued_source_job(user, workspace):
    from django.utils import timezone

    owner = _environment(user, workspace, "source-generation-retry")
    basis = harness_source_generation._basis(owner)
    review = reviews.HarnessSourceReview.objects.create(
        owner=owner,
        owner_kind="environment",
        organization=user.organization,
        workspace=workspace,
        state="accepted",
        basis={**basis, "suite_draft": basis["suite"]},
        receipt={"draft": basis["environment"]},
        expires_at=timezone.now() + timedelta(days=1),
    )
    return reviews.HarnessSourceScenarioGeneration.objects.create(
        review=review, requested_count=1, source_digest="sha256:example", basis=basis
    )


@pytest.mark.parametrize("hosted_error", [False, True])
def test_source_generation_reports_exhausted_activity_retries(
    queued_source_job, monkeypatch, hosted_error
):
    from dataclasses import replace

    from temporalio.common import RetryPolicy
    from temporalio.testing import ActivityEnvironment

    from simulate.tasks.environment_source_imports import (
        generate_environment_source_scenarios,
    )

    job = queued_source_job
    error = (
        HostedHarnessError("sandbox_unavailable", "temporary", retryable=True)
        if hosted_error
        else ConnectionError("provider disconnected")
    )

    def offline(*args):
        raise error

    monkeypatch.setattr(harness_source_generation, "_generate", offline)
    env = ActivityEnvironment()
    for attempt in (1, 2):
        env.info = replace(
            env.info, attempt=attempt, retry_policy=RetryPolicy(maximum_attempts=2)
        )
        with pytest.raises(type(error)):
            env.run(generate_environment_source_scenarios._original_func, str(job.pk))
        job.refresh_from_db()
        assert job.state == ("queued" if attempt == 1 else "failed")
    assert job.error == (
        "sandbox_unavailable" if hosted_error else "source_generation_failed"
    )


def test_source_dispatch_failure_can_be_replayed(
    queued_source_job, monkeypatch, django_capture_on_commit_callbacks
):
    from unittest.mock import Mock

    from simulate.tasks.environment_source_imports import (
        generate_environment_source_scenarios,
    )

    job = queued_source_job
    submit = Mock(side_effect=[ConnectionError("dispatch failed"), None])
    monkeypatch.setattr(generate_environment_source_scenarios, "apply_async", submit)
    assert not harness_source_generation.dispatch(job.pk)
    job.refresh_from_db()
    assert (job.state, job.error) == ("failed", "source_generation_dispatch_failed")
    monkeypatch.setattr(
        harness_sources,
        "read_accepted_snapshot",
        lambda *_: ({}, {"snapshot_digest": job.source_digest}),
    )
    with django_capture_on_commit_callbacks(execute=True):
        harness_source_generation.reserve(job.review, job.reviewer)
    job.refresh_from_db()
    assert (job.state, job.error) == ("queued", "")
    assert submit.call_count == 2


def test_reviewer_preview_redacts_blocked_pages_and_suspected_columns():
    source = {
        "pin": {"source_id": "a"},
        "filename": "source-1.pdf",
        "fields": [
            {"source_field": "tier", "personal_data_suspected": False},
            {"source_field": "email", "personal_data_suspected": True},
        ],
        "rows": [{"values": [["tier", "gold"], ["email", "private"]]}],
        "pages": [{"page": 2, "text": "Escalate after two returns."}],
        "statements": [
            {
                "statement_id": "p",
                "text": "Escalate after two returns.",
                "span": {"page": 2},
            }
        ],
    }
    source["rejected_records"] = [
        {"source_row": 2, "reasons": ["Row 2 has 4 cells; expected 3"]},
        {
            "source_row": 3,
            "reasons": [
                "JSON item 3 field 'private-email' is nested; flatten it before upload"
            ],
        },
        {"source_row": 4, "reasons": ["private raw cell text"]},
    ]
    draft = {"sources": [source], "draft_digest": "draft"}
    origins = [{"source_id": "a", "original_name": "folder/Returns.pdf"}]
    preview = harness_sources.safe_source_preview(draft, origins)["sources"][0]
    assert preview["filename"] == "Returns.pdf"
    assert preview["statements"][0]["text"] == source["pages"][0]["text"]
    assert preview["sample_rows"] == [{"tier": "gold"}]
    assert preview["rejected_row_details"] == [
        {"row": 2, "reasons": ["Found 4 cells, expected 3."]},
        {
            "row": 3,
            "reasons": [
                "The row contains a nested object or array. Flatten it before uploading."
            ],
        },
        {"row": 4, "reasons": ["The row could not be read."]},
    ]
    draft["diagnostics"] = [
        {
            "code": "source_privacy_unresolved",
            "severity": "blocking",
            "span": {"source_id": "a", "page": 2},
        }
    ]
    blocked = harness_sources.safe_source_preview(draft, origins)["sources"][0]
    assert "text" not in blocked["statements"][0] and blocked["sample_rows"] == []


def test_legacy_kb_previews_return_only_page_metadata():
    from simulate.services.harness_source_preview import public_source_preview

    source = {
        "origin": {"kind": "knowledge_base", "kb_id": "collection"},
        "sample_rows": [{"tier": "private"}],
        "statements": [
            {"statement_id": "p", "page": 2, "text": "private", "text_truncated": False}
        ],
    }
    sanitized = public_source_preview({"sources": [source]})["sources"][0]
    assert sanitized == {
        "origin": source["origin"],
        "sample_rows": [],
        "statements": [{"statement_id": "p", "page": 2}],
    }
    assert source["statements"][0]["text"] == "private"


def test_reviewer_retry_dispatches_once_and_rejects_changed_basis(
    queued_source_job, user, workspace, monkeypatch, django_capture_on_commit_callbacks
):
    from unittest.mock import Mock

    job = queued_source_job
    job.state = "failed"
    job.error = "source_world_snapshot_missing"
    job.save()
    request = SimpleNamespace(
        user=user, organization=user.organization, workspace=workspace, headers={}
    )
    dispatch = Mock()
    monkeypatch.setattr(harness_source_generation, "dispatch", dispatch)
    with pytest.raises(HostedHarnessError) as error:
        harness_source_generation.state_for_request(request, job.review_id, retry=True)
    assert error.value.code == "source_world_snapshot_missing"
    dispatch.assert_not_called()
    job.error = "source_generation_failed"
    job.save()
    with django_capture_on_commit_callbacks(execute=True):
        for _ in range(2):
            state = harness_source_generation.state_for_request(
                request, job.review_id, retry=True
            )
            assert state["state"] == "queued"
    dispatch.assert_called_once()
    job.state = "failed"
    job.basis = {}
    job.save()
    with pytest.raises(HostedHarnessError) as error:
        harness_source_generation.state_for_request(request, job.review_id, retry=True)
    assert error.value.code == "source_generation_stale"


def test_new_build_lists_its_pinned_sources_without_an_append_manifest(
    queued_source_job, user, workspace
):
    review = queued_source_job.review
    owner = review.owner
    review.owner_kind = reviews.Owner.BUILD_JOB
    review.snapshot_digest = "sha256:" + "3" * 64
    review.accepted_artifact_digest = "4" * 64
    review.receipt = {
        "snapshot_id": "checkpoint",
        "sources": [{"source_id": "pdf", "purpose": "policy"}],
    }
    review.preview = {
        "sources": [{"pin": {"source_id": "pdf"}, "filename": "Refunds.pdf"}]
    }
    review.save()
    owner.payload.setdefault("metadata", {})["build_source_pin"] = {
        "owner_id": str(owner.id),
        "review_id": str(review.id),
        "id": "checkpoint",
        "digest": review.snapshot_digest,
    }
    owner.save(update_fields=["payload"])
    scope = {"organization_id": user.organization_id, "workspace_id": workspace.id}
    assert harness_sources.source_summary(owner, reviewer_scope=scope)["sources"] == [
        {
            "source_id": "pdf",
            "purpose": "policy",
            "filename": "Refunds.pdf",
            "review_id": str(review.id),
            "review_owner_id": str(owner.id),
            "unsaved": False,
        }
    ]
    assert harness_sources.source_summary(owner)["sources"] == []


def test_legacy_kb_name_is_only_recovered_in_its_organizations_default_workspace(
    user, workspace
):
    from accounts.models.workspace import Workspace

    org = user.organization
    other = Workspace.objects.create(
        organization=org, name="secondary", display_name="Secondary", created_by=user
    )
    kb = KnowledgeBaseFile.no_workspace_objects.create(
        name="Legacy", organization=org, workspace=None
    )
    file = Files.no_workspace_objects.create(
        name="Legacy guide.txt", uploaded_url="http://invalid.invalid/unused"
    )
    kb.files.add(file)
    for workspace_id, expected in [
        (None, "source-1.txt"),
        (other.id, "source-1.txt"),
        (workspace.id, "Legacy guide.txt"),
    ]:
        source = {
            "filename": "source-1.txt",
            "origin": {
                "kind": "knowledge_base",
                "kb_id": str(kb.id),
                "file_id": str(file.id),
            },
        }
        row = SimpleNamespace(preview={"sources": [source]})
        resolve_legacy_filenames(
            [row], {"workspace_id": workspace_id, "organization_id": org.id}
        )
        assert source["filename"] == expected


def test_clipped_policy_rule_can_only_be_retained_as_reference():
    text = "A" * (harness_sources.STATEMENT_PREVIEW_LIMIT + 1)
    statement = {"statement_id": "s1", "text": text, "span": {"page": 1}}
    rule = {"rule_id": "r1", "statement_id": "s1", "status": "supported"}
    draft = {
        "draft_digest": "d",
        "sources": [
            {
                "pin": {"source_id": "p", "role": "policy"},
                "pages": [{"page": 1, "text": text}],
                "statements": [statement],
            }
        ],
        "proposals": {"policy_rules": [rule]},
    }
    staged = {"draft_payload": draft, "origins": []}
    rendered = harness_sources.safe_source_preview(draft, [])["sources"][0][
        "statements"
    ][0]
    assert (
        rendered["text_truncated"]
        and len(rendered["text"]) == harness_sources.STATEMENT_PREVIEW_LIMIT
    )
    decision = {"rule_id": "r1", "decision": "reference_only"}
    review = {"policy_reviews": [{"source_id": "p", "rules": [decision]}]}
    assert (
        harness_sources._checked_review(None, staged, review)["policy_reviews"][0][
            "rules"
        ][0]["decision"]
        == "reference_only"
    )
    decision["decision"] = "accepted"
    with pytest.raises(HostedHarnessError, match="as reference"):
        harness_sources._checked_review(None, staged, review)
