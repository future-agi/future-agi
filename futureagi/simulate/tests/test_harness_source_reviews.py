import base64
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.db import close_old_connections, connection, transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from accounts.models.workspace import Workspace
from model_hub.models.choices import StatusType
from model_hub.models.develop_dataset import Files, KnowledgeBaseFile
from simulate.services import harness_source_reviews as reviews
from simulate.services import harness_sources
from simulate.services.harness_designs import store_design_document
from simulate.services.harness_storage import private_bucket
from simulate.services.hosted_harness import HostedHarnessError, create_hosted_job
from simulate.tests.test_harness_versioning_backfill import (
    storage as _storage_fixture,  # noqa: F401
)
from simulate.tests.test_hosted_harness_conversation import _payload

_DIGEST = "sha256:" + "a" * 64
_SECRET = "reference-private-sentinel"


def test_schema_digest_matches_the_utf8_canonical_vector():
    assert (
        reviews.schema_digest(
            {
                "fields": [
                    {"name": "café", "required": True, "data_type": "string"},
                    {"name": "count", "required": False, "data_type": "integer"},
                ]
            }
        )
        == "sha256:b4e59c33f8c10b05587f8e74167d8a34aefbd9bd9da7208507a39a7a0a9790ce"
    )


def test_environment_with_multiple_tables_requires_a_destination(monkeypatch):
    monkeypatch.setattr(reviews.drafts, "fresh_draft", lambda *_: None)
    owner = SimpleNamespace(
        environment_version=SimpleNamespace(
            documents={
                "contract": {
                    "data_schema": {
                        "customers": {"customer_id": "UUID"},
                        "orders": {"customer_id": "UUID", "total": "NUMERIC"},
                    }
                }
            }
        )
    )
    assert reviews._environment_schema(owner, None) == (None, ["customers", "orders"])
    schema, _tables = reviews._environment_schema(owner, "orders")
    assert schema["fields"] == [
        {"name": "customer_id", "data_type": "string", "required": False},
        {"name": "total", "data_type": "number", "required": False},
    ]


@pytest.fixture
def mock_processor(monkeypatch):
    calls = []

    def execute(payload):
        operation = payload["operation"]
        calls.append(operation)
        if operation == "prepare":
            draft = {
                "draft_digest": _DIGEST,
                "proposals": {},
                "diagnostics": [],
                "destination_schema": payload["destination_schema"],
                "sources": [
                    {
                        "pin": {
                            "source_id": item["source_id"],
                            "source_sha256": "sha256:"
                            + hashlib.sha256(
                                base64.b64decode(item["content_base64"])
                            ).hexdigest(),
                            "parser_revision": "test-parser-v1",
                            "role": item["role"],
                        },
                        "filename": item["filename"],
                        "rows": [{"values": [_SECRET]}],
                        "fields": [
                            {"source_field": "customer", "sample_values": [_SECRET]}
                        ],
                        "statements": [
                            {"statement_id": "p1", "text": _SECRET, "span": {"page": 1}}
                        ],
                    }
                    for item in payload["sources"]
                ],
            }
            return {"ok": True, "result": {"draft": draft}}
        return {
            "ok": True,
            "result": {
                "snapshot": {
                    "version": "environment-inputs.snapshot.v2",
                    "data": {
                        "snapshot_id": "snapshot-test-id",
                        "snapshot_digest": _DIGEST,
                        "sources": payload["draft"]["sources"],
                        "diagnostics": [],
                    },
                },
                "compiled": {
                    "agent_environments": [],
                    "evaluator_inputs": {"private": _SECRET},
                },
            },
        }

    monkeypatch.setattr(harness_sources, "execute_source_request", execute)
    monkeypatch.setattr(harness_sources, "_schedule_staging_expiry", lambda key: None)
    return calls


def _case(user, workspace, kind):
    schema = {
        "fields": [{"name": "customer", "data_type": "string", "required": False}]
    }
    basis = {"revision": 1, "schema_digest": reviews.schema_digest(schema)}
    meta = {"source_review_basis": {**basis, "destination_schema": schema}}
    if kind == "design":
        meta["design"] = {"name": "design"}
    owner, _created = create_hosted_job(
        user.organization,
        {**_payload(), "metadata": meta},
        workspace=workspace,
        idempotency_key=str(uuid4()),
    )
    request = SimpleNamespace(
        user=user, organization=user.organization, workspace=workspace, headers={}
    )
    return owner, request, {"kind": kind, "id": owner.id, **basis}


def _accept_args(review):
    return {
        "expected_revision": review["revision"],
        "expected_draft_digest": review["draft_digest"],
        "expected_schema_digest": review["scope"]["schema_digest"],
        "decisions": {},
        "idempotency_key": "one-accept-attempt",
    }


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("action", "review_state", "job_state", "failure_code"),
    [
        ("cancel", "cancelled", "canceled", "source_review_cancelled"),
        ("expire", "expired", "failed", "source_review_expired"),
    ],
)
def test_terminal_review_ends_parked_owner(
    action,
    review_state,
    job_state,
    failure_code,
    user,
    workspace,
):
    owner, request, scope = _case(user, workspace, "build_job")
    row = reviews.HarnessSourceReview.objects.create(
        organization=user.organization,
        workspace=workspace,
        owner=owner,
        owner_kind=reviews.Owner.BUILD_JOB,
        state=reviews.State.READY,
        basis={"schema_digest": scope["schema_digest"]},
        expires_at=timezone.now() + timedelta(minutes=5),
        draft_digest=_DIGEST,
    )
    owner.payload["metadata"].update(
        {"source_review_id": str(row.id), "build_source_selection": {}}
    )
    owner.current_stage = "awaiting_source_review"
    owner.state = owner.State.QUEUED
    owner.save(update_fields=["payload", "current_stage", "state", "updated_at"])
    if action == "cancel":
        result = reviews.cancel_review(
            request,
            row.id,
            expected_revision=row.revision,
            expected_draft_digest=row.draft_digest,
        )
    else:
        row.expires_at = timezone.now() - timedelta(seconds=1)
        row.save(update_fields=["expires_at"])
        result = reviews.get_review(request, row.id)
    owner.refresh_from_db()
    assert result["state"] == review_state
    assert owner.state == job_state
    assert owner.terminal_at is not None
    assert owner.failure["code"] == failure_code


@pytest.mark.django_db
@pytest.mark.parametrize("failure", ["parser", "object_storage"])
def test_failed_prepare_retries_same_selection(
    user, workspace, request, mock_processor, monkeypatch, failure
):
    request.getfixturevalue("_storage_fixture")
    owner, req, scope = _case(user, workspace, "build_job")
    document = store_design_document(
        user.organization, workspace, user, name="guide.txt", body=b"safe"
    )
    args = {
        "scope": scope,
        "document_ids": [document.id],
        "knowledge_base": [],
        "idempotency_key": "retry",
    }
    name = "prepare_draft" if failure == "parser" else "_put_private"
    with monkeypatch.context() as patch:

        def unavailable(*args):
            raise OSError("private internal detail")

        patch.setattr(harness_sources, name, unavailable)
        with pytest.raises(HostedHarnessError) as caught:
            reviews.create_review(req, **args)
        assert caught.value.status_code == 503 and caught.value.retryable
    row = reviews.HarnessSourceReview.objects.get(owner=owner)
    assert row.state == "needs_repair"
    assert "private internal detail" not in json.dumps(row.preview)
    retried = reviews.create_review(req, **args)
    assert retried["id"] == str(row.pk) and retried["state"] == "ready"
    assert retried["revision"] > row.revision


@pytest.mark.django_db
@pytest.mark.parametrize("kind", ["design", "build_job"])
def test_one_private_receipt_for_a_mixed_batch(
    kind, user, workspace, request, monkeypatch, mock_processor
):
    storage = request.getfixturevalue("_storage_fixture")
    put = storage.put_object
    writes = []

    def record_bucket(bucket, key, *args, **kwargs):
        writes.append(bucket)
        return put(bucket, key, *args, **kwargs)

    monkeypatch.setattr(storage, "put_object", record_bucket)
    owner, request, scope = _case(user, workspace, kind)
    documents = [
        store_design_document(
            user.organization, workspace, user, name="guide.txt", body=_SECRET.encode()
        )
        for _ in range(2)
    ]
    kb = KnowledgeBaseFile.no_workspace_objects.create(
        organization=user.organization,
        workspace=workspace,
        name="Policy",
        status=StatusType.COMPLETED.value,
    )
    file = Files.no_workspace_objects.create(
        name="guide.txt", status=StatusType.COMPLETED.value
    )
    kb.files.add(file)
    storage.objects[f"knowledge-base/{kb.id}/{file.id}.txt"] = _SECRET.encode()
    inputs = {
        "scope": scope,
        "document_ids": [item.id for item in documents],
        "knowledge_base": [{"kb_id": kb.id, "file_ids": [file.id]}],
        "idempotency_key": "mixed-input",
    }
    ready = reviews.create_review(request, **inputs)
    assert ready["state"] == "ready"
    assert reviews.create_review(request, **inputs) == ready
    assert len(ready["preview"]["sources"]) == 3
    assert _SECRET not in json.dumps(ready["preview"])
    expected = _accept_args(ready)
    with pytest.raises(HostedHarnessError, match="changed"):
        reviews.accept_review(
            request, ready["id"], **{**expected, "expected_revision": 1}
        )
    accepted = reviews.accept_review(request, ready["id"], **expected)
    assert accepted == reviews.accept_review(request, ready["id"], **expected)
    assert mock_processor == ["prepare", "accept"]
    assert accepted["state"] == "accepted"
    assert accepted["receipt"]["snapshot_digest"] == _DIGEST
    assert _SECRET not in json.dumps(accepted["receipt"])
    with pytest.raises(HostedHarnessError, match="already been accepted"):
        reviews.accept_review(
            request, ready["id"], **{**expected, "idempotency_key": "different"}
        )
    snapshot, receipt = reviews.accepted_source_snapshot(
        ready["id"], owner=owner, owner_kind=kind
    )
    assert receipt["snapshot_id"] == json.loads(snapshot)["data"]["snapshot_id"]
    assert {binding["origin"]["kind"] for binding in receipt["bindings"]} == {
        "upload",
        "knowledge_base",
    }
    assert len({binding["source_id"] for binding in receipt["bindings"]}) == 3
    assert {binding["source_sha256"] for binding in receipt["bindings"]} == {
        "sha256:" + hashlib.sha256(_SECRET.encode()).hexdigest()
    }
    assert set(writes) == {private_bucket()}
    other = Workspace.no_workspace_objects.create(
        organization=user.organization,
        name="Other",
        created_by=user,
    )
    request.workspace = other
    with pytest.raises((HostedHarnessError, PermissionDenied)):
        reviews.get_review(request, ready["id"])


@pytest.mark.django_db(transaction=True)
def test_concurrent_acceptance_returns_one_immutable_receipt(
    user, workspace, request, mock_processor
):
    request.getfixturevalue("_storage_fixture")
    owner, request, scope = _case(user, workspace, "design")
    document = store_design_document(
        user.organization, workspace, user, name="guide.txt", body=b"safe"
    )
    ready = reviews.create_review(
        request,
        scope=scope,
        document_ids=[document.id],
        knowledge_base=[],
        idempotency_key="prepare",
    )

    def accept(_):
        try:
            return reviews.accept_review(request, ready["id"], **_accept_args(ready))
        finally:
            close_old_connections()

    with ThreadPoolExecutor(2) as pool:
        first, second = pool.map(accept, range(2))
    assert first == second
    assert mock_processor == ["prepare", "accept"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("cleanup_first", [False, True])
def test_accept_waiting_across_expiry_never_reads_staging(
    user, workspace, request, monkeypatch, mock_processor, cleanup_first
):
    from simulate.tasks import environment_source_imports as cleanup

    request.getfixturevalue("_storage_fixture")
    owner, request, scope = _case(user, workspace, "design")
    document = store_design_document(
        user.organization, workspace, user, name="guide.txt", body=b"safe"
    )
    ready = reviews.create_review(
        request,
        scope=scope,
        document_ids=[document.id],
        knowledge_base=[],
        idempotency_key="prepare",
    )
    row = reviews.HarnessSourceReview.objects.get(pk=ready["id"])
    now = row.expires_at - timedelta(seconds=1)
    monkeypatch.setattr(reviews, "timezone", SimpleNamespace(now=lambda: now))
    monkeypatch.setattr(cleanup, "timezone", SimpleNamespace(now=lambda: now))
    removed = []
    monkeypatch.setattr(cleanup, "remove_private", removed.append)
    waiting = Event()

    def mark_query(execute, sql, params, many, context):
        if "FOR UPDATE" in sql:
            waiting.set()
        return execute(sql, params, many, context)

    def accept():
        try:
            with connection.execute_wrapper(mark_query):
                return reviews.accept_review(
                    request, ready["id"], **_accept_args(ready)
                )
        finally:
            close_old_connections()

    with ThreadPoolExecutor(1) as pool:
        with transaction.atomic():
            reviews.HarnessSourceReview.objects.select_for_update().get(pk=row.id)
            future = pool.submit(accept)
            assert waiting.wait(10)
            now = row.expires_at
            if cleanup_first:
                cleanup.expire_environment_source_import._original_func(
                    reviews._staging_key(row)
                )
        with pytest.raises(HostedHarnessError, match="expired"):
            future.result(timeout=10)
    row.refresh_from_db()
    assert row.state == reviews.State.EXPIRED
    assert mock_processor == ["prepare"]
    assert removed == ([reviews._staging_key(row)] if cleanup_first else [])
