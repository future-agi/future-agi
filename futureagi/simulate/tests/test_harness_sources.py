import base64
import hashlib
import json
from types import SimpleNamespace

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from model_hub.models.choices import StatusType
from model_hub.models.develop_dataset import Files, KnowledgeBaseFile
from simulate.services import harness_sources
from simulate.services.harness_versioning import archives, drafts, versions
from simulate.services.hosted_harness import HostedHarnessError
from simulate.tests.test_harness_versioning_backfill import (
    storage as storage,  # noqa: F401
)
from simulate.tests.test_harness_versioning_record import _save_drafts
from simulate.tests.test_harness_versioning_rollback import _built

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def versioning_on(settings):
    settings.HARNESS_VERSIONING_ENABLED = True


def test_kb_reference_import_writes_private_snapshot_and_environment_draft(
    user, workspace, storage, monkeypatch
):
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

    def alk(payload):
        if payload["operation"] == "prepare":
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
        pin = prepared_draft["sources"][0]["pin"]
        return {
            "ok": True,
            "result": {
                "snapshot": {
                    "version": "environment-inputs.snapshot.v2",
                    "digest": "sha256:envelope",
                    "data": {
                        "snapshot_digest": "sha256:" + "a" * 64,
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
    assert (
        harness_sources.accept_source_import(request, environment, **accept_args)[
            "draft"
        ]
        == accepted["draft"]
    )
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
    user, workspace, storage, monkeypatch
):
    environment = _built(user, workspace, "source-multi-upload")
    seen = {}

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
    prepared = harness_sources.prepare_upload_import(
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
    )

    assert prepared["draft_digest"] == "sha256:prepared"
    assert [source["filename"] for source in seen["sources"]] == [
        "source-1.csv",
        "source-2.json",
    ]
    assert {source["role"] for source in seen["sources"]} == {"seed_records"}


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


def test_participant_visibility_fails_closed(user, workspace, storage, monkeypatch):
    environment = _built(user, workspace, "source-participant")
    staged = {
        "version": 1,
        "environment_id": str(environment.id),
        "environment_version_id": str(environment.environment_version_id),
        "draft": {"id": None, "revision": 0},
        "draft_payload": {"draft_digest": "sha256:prepared"},
        "origins": [],
        "originals": [],
        "captured_at": "2026-01-01T00:00:00Z",
    }
    body = harness_sources._canonical(staged)
    digest = hashlib.sha256(body).hexdigest()
    key = f"environment-source-imports/{environment.organization_id}/{environment.id}/{digest}.json"
    storage.objects[key] = body
    import_ref = harness_sources.signing.dumps(
        {
            "v": 1,
            "organization_id": str(environment.organization_id),
            "workspace_id": str(environment.workspace_id),
            "environment_id": str(environment.id),
            "environment_version_id": str(environment.environment_version_id),
            "draft": staged["draft"],
            "key": key,
            "digest": digest,
        },
        key=harness_sources.settings.SECRET_KEY,
        salt=harness_sources.IMPORT_REF_SALT,
    )
    request = SimpleNamespace(
        user=user, organization=user.organization, workspace=workspace
    )
    with pytest.raises(HostedHarnessError) as caught:
        harness_sources.accept_source_import(
            request,
            environment,
            import_ref=import_ref,
            expected_environment_version_id=str(environment.environment_version_id),
            expected_draft={"id": None, "revision": 0},
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
