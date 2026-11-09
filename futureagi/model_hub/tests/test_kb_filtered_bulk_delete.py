"""H10-C25-001: a filtered bulk selection must never expand at deletion."""

import json

import pytest

from model_hub.models.choices import StatusType
from model_hub.models.develop_dataset import Files, KnowledgeBaseFile
from model_hub.tasks import develop_dataset as tasks

FILES_URL = "/model-hub/knowledge-base/files/"


@pytest.mark.django_db
@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize("exclude_first", [False, True])
def test_h10_c25_001_filtered_snapshot_preserves_other_files_and_chunks(
    mocker, auth_client, organization, workspace, exclude_first
):
    def create_file(name):
        return Files.objects.create(
            name=name,
            status=StatusType.COMPLETED.value,
            metadata=json.dumps({"size": 12}),
            updated_by="Test User",
            uploaded_url="https://example.com/test.txt",
        )

    matches = [create_file(f"policy-{n:02d}.txt") for n in range(1, 22)]
    unrelated = create_file("unrelated.txt")
    already_deleting = create_file("policy-deleting.txt")
    already_deleting.status = StatusType.DELETING.value
    already_deleting.save()
    kb = KnowledgeBaseFile.objects.create(
        name="filtered-bulk-delete",
        organization=organization,
        workspace=workspace,
        status=StatusType.COMPLETED.value,
        created_by="Test User",
        size=12,
    )
    kb.files.set([*matches, unrelated, already_deleting])

    response = auth_client.post(
        FILES_URL,
        {"kb_id": str(kb.id), "search": "POLICY", "page_number": 0, "page_size": 10},
        format="json",
    )
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["total_rows"] == 21
    assert len(result["table_data"]) == 10
    # The snapshot covers all matching pages, not just the ten rendered rows.
    snapshot = result["matching_file_ids"]
    assert len(snapshot) == 21
    assert set(snapshot) == {str(file.id) for file in matches}

    # A later matching upload must not silently join an already confirmed selection.
    later_match = create_file("policy-later.txt")
    kb.files.add(later_match)
    excluded = {str(matches[0].id)} if exclude_first else set()
    selected_ids = [file_id for file_id in snapshot if file_id not in excluded]
    dispatch = mocker.patch("model_hub.views.develop_dataset.remove_kb_files.delay")
    response = auth_client.delete(
        FILES_URL,
        {"kb_id": str(kb.id), "delete_all": False, "file_ids": selected_ids},
        format="json",
    )
    assert response.status_code == 200
    dispatch.assert_called_once()
    queued_ids, org_id, kb_id = dispatch.call_args.args
    assert {str(file_id) for file_id in queued_ids} == set(selected_ids)
    assert org_id == str(organization.id)
    assert kb_id == str(kb.id)

    # Execute the real worker and chunk-removal loop, replacing only external storage.
    original_chunks = {
        str(file.id) for file in [*matches, unrelated, already_deleting, later_match]
    }
    remaining_chunks = set(original_chunks)

    def remove_chunks(file_id, target_kb_id, target_org_id):
        assert str(target_kb_id) == str(kb.id)
        assert str(target_org_id) == str(organization.id)
        remaining_chunks.remove(str(file_id))
        return {"file_id": file_id}

    indexer = mocker.patch.object(tasks, "KBIndexer").return_value
    indexer.remove_chunks_from_kb.side_effect = remove_chunks
    mocker.patch.object(tasks, "wrap_for_thread", side_effect=lambda fn: fn)
    tasks.remove_kb_files.__wrapped__(queued_ids, org_id, kb_id)

    assert remaining_chunks == original_chunks - set(selected_ids)
    for file in [*matches, unrelated, already_deleting, later_match]:
        file.refresh_from_db()
        assert file.deleted is (str(file.id) in selected_ids)
    assert unrelated.status == StatusType.COMPLETED.value
    assert str(unrelated.id) in remaining_chunks
