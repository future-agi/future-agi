"""Deleting documents must release reusable KB upload capacity.

Exercise the real deletion functions, ORM and multipart PATCH. Only chunk
storage, file validation, background uploads and ingestion dispatch are stubbed.
"""

import json
from unittest.mock import MagicMock

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from model_hub.constants import MAX_KB_SIZE
from model_hub.models.choices import StatusType
from model_hub.models.develop_dataset import Files, KnowledgeBaseFile
from model_hub.tasks.develop_dataset import remove_kb_files

pytestmark = pytest.mark.django_db

KB_URL = "/model-hub/knowledge-base/"
VIEW = "model_hub.views.develop_dataset.CreateKnowledgeBaseView"
VIEW_MODULE = "model_hub.views.develop_dataset"
TASK_MODULE = "model_hub.tasks.develop_dataset"


@pytest.fixture
def kb(organization, workspace, user):
    return KnowledgeBaseFile.objects.create(
        name="Reusable capacity",
        organization=organization,
        workspace=workspace,
        created_by=user.email,
        size=0,
    )


@pytest.fixture
def upload_boundaries(mocker):
    validate = mocker.patch(
        f"{VIEW}.validate_all_files",
        return_value={"valid": True, "files_with_issues": []},
    )
    # Keep create_files_and_upload real, including the stored byte metadata.
    mocker.patch(f"{VIEW}._get_upload_executor", return_value=MagicMock())
    mocker.patch(f"{VIEW_MODULE}.schedule_kb_ingestion_on_commit")
    return validate


@pytest.fixture
def removal_boundary(mocker):
    failures = set()

    def remove_chunks(file_id, kb_id, org_id):
        result = {"file_id": file_id, "kb_id": kb_id}
        if str(file_id) in failures:
            result["error"] = "Chunk deletion failed"
        return result

    indexer = mocker.patch(f"{TASK_MODULE}.KBIndexer").return_value
    indexer.remove_chunks_from_kb.side_effect = remove_chunks
    return failures


def _patch_files(client, kb, documents):
    return client.patch(
        KB_URL,
        {
            "kb_id": str(kb.id),
            "file": [
                SimpleUploadedFile(name, content, content_type="text/plain")
                for name, content in documents
            ],
        },
        format="multipart",
    )


def _remove(kb, file_ids, whole_kb=False):
    # Bypass only the activity connection-management wrapper, as in the
    # existing ingestion tests; both removal function bodies remain real.
    return remove_kb_files._original_func(
        [] if whole_kb else [str(file_id) for file_id in file_ids],
        str(kb.organization_id),
        [str(kb.id)] if whole_kb else str(kb.id),
    )


@pytest.mark.parametrize("whole_kb", [False, True], ids=["documents", "whole-kb"])
def test_full_gib_delete_to_empty_allows_one_byte_reupload(
    kb, auth_client, upload_boundaries, removal_boundary, whole_kb
):
    # A supported-size fixture, without allocating or uploading a GiB in a test.
    documents = Files.objects.bulk_create(
        [
            Files(
                name=f"part-{index}.txt",
                status=StatusType.COMPLETED.value,
                metadata=json.dumps({"size": 4 * 1024 * 1024}),
                uploaded_url="https://example.com/document.txt",
            )
            for index in range(256)
        ]
    )
    kb.files.add(*documents)
    kb.size = MAX_KB_SIZE
    kb.save(update_fields=["size"])

    response = _patch_files(auth_client, kb, [("replacement.txt", b"x")])
    assert response.status_code == 400, response.data
    upload_boundaries.assert_not_called()

    result = _remove(kb, [doc.id for doc in documents], whole_kb=whole_kb)
    assert len(result) == 256
    assert all(item["status"] == StatusType.COMPLETED.value for item in result)
    assert not kb.files.filter(deleted=False).exists()
    kb.refresh_from_db()
    assert kb.size == 0
    assert kb.status == StatusType.COMPLETED.value

    response = _patch_files(auth_client, kb, [("replacement.txt", b"x")])
    assert response.status_code == 200, response.data
    kb.refresh_from_db()
    assert kb.size == 1
    assert kb.files.filter(deleted=False).count() == 1


def test_upload_to_limit_delete_and_reupload_is_retry_safe(
    kb, auth_client, upload_boundaries, removal_boundary, mocker
):
    # Exercise actual multipart admission and metadata creation with small
    # payloads; the separate GiB fixture pins the unmodified production cap.
    mocker.patch(f"{VIEW_MODULE}.MAX_KB_SIZE", 8)
    response = _patch_files(
        auth_client, kb, [("first.txt", b"abcd"), ("second.txt", b"efgh")]
    )
    assert response.status_code == 200, response.data
    kb.refresh_from_db()
    assert kb.size == 8
    original_ids = list(kb.files.values_list("id", flat=True))
    assert len(original_ids) == 2

    response = _patch_files(auth_client, kb, [("over-limit.txt", b"x")])
    assert response.status_code == 400, response.data

    _remove(kb, original_ids)
    kb.refresh_from_db()
    assert kb.size == 0
    response = _patch_files(auth_client, kb, [("replacement.txt", b"x")])
    assert response.status_code == 200, response.data

    # A replay of the original removal cannot free bytes from the new upload.
    _remove(kb, original_ids)
    kb.refresh_from_db()
    assert kb.size == 1
    assert kb.files.get().name == "replacement.txt"


@pytest.mark.parametrize("string_metadata", [True, False], ids=["json-string", "dict"])
@pytest.mark.parametrize(
    "failed_indexes, expected_size",
    [((), 7), ((1,), 12), ((0, 1), 15)],
    ids=["success", "partial-failure", "all-failed"],
)
def test_only_successful_attached_removals_reclaim_bytes(
    kb, removal_boundary, string_metadata, failed_indexes, expected_size
):
    documents = []
    for index, size in enumerate((3, 5, 7)):
        metadata = {"size": size}
        document = Files.objects.create(
            name=f"part-{index}.txt",
            metadata=json.dumps(metadata) if string_metadata else metadata,
            uploaded_url="https://example.com/document.txt",
        )
        documents.append(document)
    kb.files.add(*documents)
    # Neither an already-deleted attached document nor an unrelated file counts.
    old = Files.objects.create(
        name="previously-deleted.txt", metadata=json.dumps({"size": 29}), deleted=True
    )
    kb.files.add(old)
    Files.objects.create(name="unattached.txt", metadata=json.dumps({"size": 31}))
    kb.size = 15
    kb.save(update_fields=["size"])
    removal_boundary.update(str(documents[i].id) for i in failed_indexes)

    requested_ids = [doc.id for doc in documents[:2]]
    _remove(kb, requested_ids)
    kb.refresh_from_db()
    assert kb.size == expected_size
    if len(failed_indexes) == 1:
        assert kb.status == StatusType.PARTIAL_COMPLETED.value
    for index, document in enumerate(documents[:2]):
        document.refresh_from_db()
        assert document.deleted is (index not in failed_indexes)
    documents[2].refresh_from_db()
    assert not documents[2].deleted

    # Repeated success or repeated failure must not change the retained total.
    _remove(kb, requested_ids)
    kb.refresh_from_db()
    assert kb.size == expected_size
