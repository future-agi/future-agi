"""Adding files to a knowledge base right after creating it.

The Python SDK creates a knowledge base with
``create_kb(name=..., file_paths=[...])`` (a multipart POST carrying ``name``
and one ``file`` part per file) and adds files with
``update_kb(kb_name=..., file_paths=[...])`` (a multipart PATCH carrying
``kb_id``, the knowledge base's current ``name`` and one ``file`` part per
file), both to ``/model-hub/knowledge-base/``.

Each request that adds files starts an ingestion run once its transaction
commits, under a workflow id derived from the knowledge base id. Temporal
refuses to start an id whose run has not finished, and the run started by
create is still going for the first seconds after create. Temporal is
replaced here by a stand-in that applies the same rule, S3 uploads are
patched out, and the ingestion start runs as soon as it is registered: in a
deployment the view's transaction block is the outermost one, so the start
runs before the view builds its response.
"""

import sys
import uuid
from types import SimpleNamespace

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from temporalio.exceptions import WorkflowAlreadyStartedError

from model_hub.models.develop_dataset import KnowledgeBaseFile

VIEW = "model_hub.views.develop_dataset.CreateKnowledgeBaseView"
MODULE = "model_hub.views.develop_dataset"
ENTITLEMENTS = "ee.usage.services.entitlements.Entitlements"
KB_URL = "/model-hub/knowledge-base/"
INGEST_ACTIVITY = "ingest_kb_files_activity"


def _txt(name, content):
    return SimpleUploadedFile(name, content, content_type="text/plain")


class _TemporalStarts:
    """Records workflow starts and applies Temporal's id rule to them.

    With ``first_run_finished=False`` a workflow id that was already started
    is refused with ``WorkflowAlreadyStartedError``, as Temporal does while
    that run is still going. With ``first_run_finished=True`` the id can be
    started again, as Temporal allows once the earlier run has closed.
    """

    def __init__(self, first_run_finished):
        self.first_run_finished = first_run_finished
        self.running = set()
        self.started = []
        self.refused = []

    def start_activity(
        self,
        activity_name,
        args=(),
        kwargs=None,
        queue="default",
        task_id=None,
        **options,
    ):
        task_id = task_id or f"{activity_name}-{uuid.uuid4().hex[:8]}"
        workflow_id = f"task-{task_id}"
        if workflow_id in self.running and not self.first_run_finished:
            self.refused.append(workflow_id)
            raise WorkflowAlreadyStartedError(workflow_id, "TaskRunnerWorkflow")
        self.running.add(workflow_id)
        self.started.append(
            {"activity": activity_name, "args": args, "task_id": task_id}
        )
        return workflow_id

    def file_ids_sent_to_ingestion(self):
        file_ids = set()
        for start in self.started:
            if start["activity"] == INGEST_ACTIVITY:
                file_ids.update(str(file_id) for file_id in start["args"][0])
        return file_ids


@pytest.fixture
def allow_entitlements(mocker):
    allowed = mocker.MagicMock(allowed=True, reason="")
    mocker.patch(f"{ENTITLEMENTS}.can_create", return_value=allowed)
    mocker.patch(f"{ENTITLEMENTS}.check_feature", return_value=allowed)


@pytest.fixture
def no_s3_upload(mocker):
    mocker.patch(f"{VIEW}._upload_file_to_s3_background")


@pytest.fixture
def ingestion_starts_when_registered(mocker):
    mocker.patch(
        "model_hub.utils.kb_helpers.transaction",
        SimpleNamespace(on_commit=lambda func, using=None, robust=False: func()),
    )


@pytest.fixture
def view_errors(mocker):
    """Exceptions the knowledge base view caught, as (class name, message)."""
    caught = []

    def _record(*args, **kwargs):
        error = sys.exc_info()[1]
        if error is not None:
            caught.append((type(error).__name__, str(error)))

    logger = mocker.patch(f"{MODULE}.logger")
    logger.exception.side_effect = _record
    return caught


def _create_then_add_file(auth_client, mocker, first_run_finished):
    temporal = _TemporalStarts(first_run_finished=first_run_finished)
    mocker.patch(
        "tfc.temporal.drop_in.start_activity", side_effect=temporal.start_activity
    )

    created = auth_client.post(
        KB_URL,
        {
            "name": "product-docs",
            "file": [
                _txt("guide.txt", b"How to set up the product."),
                _txt("faq.txt", b"Frequently asked questions."),
            ],
        },
        format="multipart",
    )
    assert created.status_code == status.HTTP_200_OK, created.json()
    created_result = created.json()["result"]

    updated = auth_client.patch(
        KB_URL,
        {
            "kb_id": str(created_result["kb_id"]),
            "name": created_result["kb_name"],
            "file": [_txt("changelog.txt", b"Version 2 adds search.")],
        },
        format="multipart",
    )
    return temporal, created_result, updated


def _assert_file_added(temporal, created_result, updated, view_errors):
    assert updated.status_code == status.HTTP_200_OK, (
        f"update answered {updated.status_code} {updated.json()}; "
        f"view caught {view_errors}; refused workflow ids {temporal.refused}"
    )
    result = updated.json()["result"]
    assert result["name"] == "product-docs"

    kb = KnowledgeBaseFile.objects.get(id=created_result["kb_id"])
    assert set(kb.files.values_list("name", flat=True)) == {
        "guide.txt",
        "faq.txt",
        "changelog.txt",
    }
    assert {str(file_id) for file_id in result["files"]} == {
        str(file_id) for file_id in kb.files.values_list("id", flat=True)
    }

    added_file_id = str(kb.files.get(name="changelog.txt").id)
    assert added_file_id in temporal.file_ids_sent_to_ingestion(), (
        f"no ingestion run was started for the added file; starts: "
        f"{temporal.started}"
    )


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.usefixtures(
    "allow_entitlements", "no_s3_upload", "ingestion_starts_when_registered"
)
class TestKnowledgeBaseUpdateAfterCreate:
    def test_file_added_right_after_create_is_attached_and_ingested(
        self, auth_client, mocker, view_errors
    ):
        temporal, created_result, updated = _create_then_add_file(
            auth_client, mocker, first_run_finished=False
        )

        _assert_file_added(temporal, created_result, updated, view_errors)

    def test_file_added_after_create_ingestion_finished_is_attached_and_ingested(
        self, auth_client, mocker, view_errors
    ):
        temporal, created_result, updated = _create_then_add_file(
            auth_client, mocker, first_run_finished=True
        )

        _assert_file_added(temporal, created_result, updated, view_errors)
