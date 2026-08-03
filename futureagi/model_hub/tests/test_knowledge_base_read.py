"""Functional coverage for the knowledge-base read and file-management routes.

Covers ``GET /model-hub/knowledge-base/get/`` (table), ``GET
/model-hub/knowledge-base/list/`` (dropdown) and both methods of ``POST|DELETE
/model-hub/knowledge-base/files/``.

Also pins sort validation: a blank ``column_id`` is rejected with 400 by the
request serializer, the same code the view returns for an unknown one.
"""

import json
import uuid

import pytest
from rest_framework import status

from model_hub.models.choices import StatusType
from model_hub.models.develop_dataset import Files, KnowledgeBaseFile

MODULE = "model_hub.views.develop_dataset"
REMOVE_KB_FILES = f"{MODULE}.remove_kb_files.delay"
TABLE_URL = "/model-hub/knowledge-base/get/"
LIST_URL = "/model-hub/knowledge-base/list/"
FILES_URL = "/model-hub/knowledge-base/files/"


def _file(name, status_value=None, size=12):
    return Files.objects.create(
        name=name,
        status=status_value or StatusType.COMPLETED.value,
        metadata=json.dumps({"size": size}),
        updated_by="Test User",
        uploaded_url="https://example.com/test.txt",
    )


def _kb(organization, workspace, name, files=None, **kwargs):
    kb = KnowledgeBaseFile.objects.create(
        name=name,
        organization=organization,
        workspace=workspace,
        status=kwargs.pop("status", StatusType.COMPLETED.value),
        created_by=kwargs.pop("created_by", "Test User"),
        size=kwargs.pop("size", 12),
        **kwargs,
    )
    if files:
        kb.files.set(files)
    return kb


@pytest.mark.integration
@pytest.mark.api
class TestKnowledgeBaseTableView:
    """``GET /model-hub/knowledge-base/get/``."""

    def test_returns_rows_with_file_counts_and_total(
        self, auth_client, organization, workspace
    ):
        _kb(
            organization,
            workspace,
            "table-kb-a",
            files=[_file("a1.txt"), _file("a2.txt")],
        )
        _kb(organization, workspace, "table-kb-b")

        response = auth_client.get(TABLE_URL)

        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        assert result["total_rows"] == 2
        rows = {row["name"]: row for row in result["table_data"]}
        assert rows["table-kb-a"]["files_uploaded"] == 2
        assert rows["table-kb-b"]["files_uploaded"] == 0
        assert set(rows["table-kb-a"]) == {
            "id",
            "name",
            "files_uploaded",
            "status",
            "error",
            "updated_at",
            "created_by",
        }

    def test_error_is_suppressed_for_completed_and_processing_rows(
        self, auth_client, organization, workspace
    ):
        _kb(
            organization,
            workspace,
            "completed-kb",
            status=StatusType.COMPLETED.value,
            last_error="should not surface",
        )
        _kb(
            organization,
            workspace,
            "failed-kb",
            status=StatusType.FAILED.value,
            last_error="ingestion exploded",
        )

        response = auth_client.get(TABLE_URL)

        rows = {row["name"]: row for row in response.json()["result"]["table_data"]}
        assert rows["completed-kb"]["error"] is None
        assert rows["failed-kb"]["error"] == "ingestion exploded"

    def test_org_without_any_kb_returns_empty_result(self, auth_client):
        response = auth_client.get(TABLE_URL)

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["result"] == {}

    def test_org_with_only_soft_deleted_kbs_returns_column_config_only(
        self, auth_client, organization, workspace
    ):
        kb = _kb(organization, workspace, "gone-kb")
        kb.deleted = True
        kb.save()

        response = auth_client.get(TABLE_URL)

        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        assert "table_data" not in result
        assert [column["name"] for column in result["column_config"]] == [
            "id",
            "name",
            "files_uploaded",
            "status",
            "updated_at",
            "created_by",
        ]

    def test_search_narrows_rows(self, auth_client, organization, workspace):
        _kb(organization, workspace, "invoices-kb")
        _kb(organization, workspace, "contracts-kb")

        response = auth_client.get(TABLE_URL, {"search": "invoice"})

        names = [row["name"] for row in response.json()["result"]["table_data"]]
        assert names == ["invoices-kb"]

    @pytest.mark.parametrize(
        "sort_type,expected",
        [
            ("ascending", ["alpha-kb", "zulu-kb"]),
            ("descending", ["zulu-kb", "alpha-kb"]),
        ],
    )
    def test_sort_by_name(
        self, auth_client, organization, workspace, sort_type, expected
    ):
        _kb(organization, workspace, "zulu-kb")
        _kb(organization, workspace, "alpha-kb")

        response = auth_client.get(
            TABLE_URL,
            {"sort": json.dumps([{"column_id": "name", "type": sort_type}])},
        )

        names = [row["name"] for row in response.json()["result"]["table_data"]]
        assert names == expected

    def test_sort_by_files_uploaded_uses_file_count(
        self, auth_client, organization, workspace
    ):
        _kb(organization, workspace, "few-files-kb", files=[_file("f1.txt")])
        _kb(
            organization,
            workspace,
            "many-files-kb",
            files=[_file("m1.txt"), _file("m2.txt"), _file("m3.txt")],
        )

        response = auth_client.get(
            TABLE_URL,
            {
                "sort": json.dumps(
                    [{"column_id": "files_uploaded", "type": "descending"}]
                )
            },
        )

        rows = response.json()["result"]["table_data"]
        assert [row["name"] for row in rows] == ["many-files-kb", "few-files-kb"]

    def test_unknown_sort_column_is_rejected(
        self, auth_client, organization, workspace
    ):
        _kb(organization, workspace, "sortable-kb")

        response = auth_client.get(
            TABLE_URL,
            {"sort": json.dumps([{"column_id": "size", "type": "ascending"}])},
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_pagination_slices_rows(self, auth_client, organization, workspace):
        for index in range(3):
            _kb(organization, workspace, f"paged-kb-{index}")

        response = auth_client.get(TABLE_URL, {"page_number": 0, "page_size": 2})

        result = response.json()["result"]
        assert len(result["table_data"]) == 2
        assert result["total_rows"] == 3

    @pytest.mark.parametrize("blank_column_id", [None, ""])
    def test_sort_item_with_blank_column_id_returns_400(
        self, auth_client, organization, workspace, blank_column_id
    ):
        """A blank ``column_id`` is rejected with the same 400 as an unknown one."""
        _kb(organization, workspace, "unsorted-kb")

        response = auth_client.get(
            TABLE_URL,
            {
                "sort": json.dumps(
                    [{"column_id": blank_column_id, "type": "ascending"}]
                )
            },
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.integration
@pytest.mark.api
class TestKnowledgeBaseDropdownList:
    """``GET /model-hub/knowledge-base/list/``."""

    def test_returns_id_and_name_pairs(self, auth_client, organization, workspace):
        kb = _kb(organization, workspace, "dropdown-kb")

        response = auth_client.get(LIST_URL)

        assert response.status_code == status.HTTP_200_OK
        rows = response.json()["result"]["table_data"]
        assert rows == [{"id": str(kb.id), "name": "dropdown-kb"}]

    def test_search_narrows_rows(self, auth_client, organization, workspace):
        _kb(organization, workspace, "policies-kb")
        _kb(organization, workspace, "receipts-kb")

        response = auth_client.get(LIST_URL, {"search": "polic"})

        names = [row["name"] for row in response.json()["result"]["table_data"]]
        assert names == ["policies-kb"]

    def test_status_flag_keeps_only_completed_kbs(
        self, auth_client, organization, workspace
    ):
        _kb(
            organization,
            workspace,
            "ready-kb",
            status=StatusType.COMPLETED.value,
        )
        _kb(
            organization,
            workspace,
            "still-processing-kb",
            status=StatusType.PROCESSING.value,
        )

        response = auth_client.get(LIST_URL, {"status": "true"})

        names = [row["name"] for row in response.json()["result"]["table_data"]]
        assert names == ["ready-kb"]

    def test_soft_deleted_kbs_are_excluded(
        self, auth_client, organization, workspace
    ):
        kb = _kb(organization, workspace, "archived-kb")
        kb.deleted = True
        kb.save()

        response = auth_client.get(LIST_URL)

        assert response.json()["result"]["table_data"] == []


@pytest.mark.integration
@pytest.mark.api
class TestKnowledgeBaseFilesList:
    """``POST /model-hub/knowledge-base/files/``."""

    def test_returns_files_with_size_and_status(
        self, auth_client, organization, workspace
    ):
        kb_file = _file("listed.txt", size=99)
        kb = _kb(organization, workspace, "files-kb", files=[kb_file])

        response = auth_client.post(
            FILES_URL,
            {"kb_id": str(kb.id), "page_number": 0, "page_size": 10},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        assert result["total_rows"] == 1
        assert result["status"] == StatusType.COMPLETED.value
        row = result["table_data"][0]
        assert row["name"] == "listed.txt"
        assert row["file_size"] == 99
        assert row["status"] == StatusType.COMPLETED.value

    def test_files_being_deleted_are_excluded(
        self, auth_client, organization, workspace
    ):
        keep = _file("keep.txt")
        going = _file("going.txt", status_value=StatusType.DELETING.value)
        kb = _kb(organization, workspace, "mixed-files-kb", files=[keep, going])

        response = auth_client.post(
            FILES_URL, {"kb_id": str(kb.id)}, format="json"
        )

        names = [row["name"] for row in response.json()["result"]["table_data"]]
        assert names == ["keep.txt"]

    def test_processing_file_drives_kb_status(
        self, auth_client, organization, workspace
    ):
        kb = _kb(
            organization,
            workspace,
            "processing-files-kb",
            files=[
                _file("done.txt"),
                _file("busy.txt", status_value=StatusType.PROCESSING.value),
            ],
        )

        response = auth_client.post(
            FILES_URL, {"kb_id": str(kb.id)}, format="json"
        )

        result = response.json()["result"]
        assert result["status"] == StatusType.PROCESSING.value
        assert result["status_count"] == 1

    def test_search_narrows_files(self, auth_client, organization, workspace):
        kb = _kb(
            organization,
            workspace,
            "searchable-files-kb",
            files=[_file("annual-report.pdf"), _file("menu.txt")],
        )

        response = auth_client.post(
            FILES_URL, {"kb_id": str(kb.id), "search": "annual"}, format="json"
        )

        names = [row["name"] for row in response.json()["result"]["table_data"]]
        assert names == ["annual-report.pdf"]

    def test_missing_kb_is_rejected(self, auth_client):
        response = auth_client.post(
            FILES_URL, {"kb_id": str(uuid.uuid4())}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_sort_item_with_blank_column_id_returns_400(
        self, auth_client, organization, workspace
    ):
        """Same rejection as the table endpoint, on the files POST path."""
        kb = _kb(
            organization, workspace, "unsorted-files-kb", files=[_file("only.txt")]
        )

        response = auth_client.post(
            FILES_URL,
            {"kb_id": str(kb.id), "sort": [{"column_id": None, "type": "ascending"}]},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.integration
@pytest.mark.api
class TestKnowledgeBaseFileDelete:
    """``DELETE /model-hub/knowledge-base/files/``."""

    def test_delete_by_ids_marks_files_deleting_and_dispatches_removal(
        self, mocker, auth_client, organization, workspace
    ):
        target = _file("delete-me.txt")
        keep = _file("keep-me.txt")
        kb = _kb(organization, workspace, "delete-kb", files=[target, keep])
        remove_kb_files = mocker.patch(REMOVE_KB_FILES)

        response = auth_client.delete(
            FILES_URL,
            {"kb_id": str(kb.id), "file_ids": [str(target.id)]},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        remove_kb_files.assert_called_once()
        dispatched_ids, org_id, kb_id = remove_kb_files.call_args[0]
        assert [str(file_id) for file_id in dispatched_ids] == [str(target.id)]
        assert org_id == str(organization.id)
        assert kb_id == str(kb.id)

        target.refresh_from_db()
        keep.refresh_from_db()
        assert target.status == StatusType.DELETING.value
        assert keep.status == StatusType.COMPLETED.value

    def test_delete_by_names_marks_matching_files(
        self, mocker, auth_client, organization, workspace
    ):
        target = _file("by-name.txt")
        keep = _file("untouched.txt")
        kb = _kb(organization, workspace, "delete-by-name-kb", files=[target, keep])
        mocker.patch(REMOVE_KB_FILES)

        response = auth_client.delete(
            FILES_URL,
            {"kb_id": str(kb.id), "file_names": ["by-name.txt"]},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        target.refresh_from_db()
        keep.refresh_from_db()
        assert target.status == StatusType.DELETING.value
        assert keep.status == StatusType.COMPLETED.value

    def test_delete_all_marks_every_file(
        self, mocker, auth_client, organization, workspace
    ):
        first = _file("all-1.txt")
        second = _file("all-2.txt")
        kb = _kb(organization, workspace, "delete-all-kb", files=[first, second])
        mocker.patch(REMOVE_KB_FILES)

        response = auth_client.delete(
            FILES_URL, {"kb_id": str(kb.id), "delete_all": True}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK
        for file_obj in (first, second):
            file_obj.refresh_from_db()
            assert file_obj.status == StatusType.DELETING.value

    def test_delete_all_honours_excluded_file_ids(
        self, mocker, auth_client, organization, workspace
    ):
        removed = _file("sweep-me.txt")
        spared = _file("spare-me.txt")
        kb = _kb(organization, workspace, "delete-except-kb", files=[removed, spared])
        mocker.patch(REMOVE_KB_FILES)

        response = auth_client.delete(
            FILES_URL,
            {
                "kb_id": str(kb.id),
                "delete_all": True,
                "excluded_file_ids": [str(spared.id)],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        removed.refresh_from_db()
        spared.refresh_from_db()
        assert removed.status == StatusType.DELETING.value
        assert spared.status == StatusType.COMPLETED.value

    def test_unknown_file_name_is_rejected(
        self, mocker, auth_client, organization, workspace
    ):
        kb_file = _file("real.txt")
        kb = _kb(organization, workspace, "bad-name-kb", files=[kb_file])
        remove_kb_files = mocker.patch(REMOVE_KB_FILES)

        response = auth_client.delete(
            FILES_URL,
            {"kb_id": str(kb.id), "file_names": ["ghost.txt"]},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        remove_kb_files.assert_not_called()
        kb_file.refresh_from_db()
        assert kb_file.status == StatusType.COMPLETED.value

    def test_neither_ids_nor_names_is_rejected(
        self, mocker, auth_client, organization, workspace
    ):
        kb_file = _file("safe.txt")
        kb = _kb(organization, workspace, "no-selector-kb", files=[kb_file])
        remove_kb_files = mocker.patch(REMOVE_KB_FILES)

        response = auth_client.delete(
            FILES_URL, {"kb_id": str(kb.id)}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        remove_kb_files.assert_not_called()
        kb_file.refresh_from_db()
        assert kb_file.status == StatusType.COMPLETED.value

    def test_missing_kb_id_is_rejected(self, mocker, auth_client):
        remove_kb_files = mocker.patch(REMOVE_KB_FILES)

        response = auth_client.delete(FILES_URL, {"file_ids": []}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        remove_kb_files.assert_not_called()

    def test_unknown_kb_is_rejected(self, mocker, auth_client):
        remove_kb_files = mocker.patch(REMOVE_KB_FILES)

        response = auth_client.delete(
            FILES_URL,
            {"kb_id": str(uuid.uuid4()), "file_ids": [str(uuid.uuid4())]},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        remove_kb_files.assert_not_called()
