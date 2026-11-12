"""Adding files to a knowledge base when their ingestion run cannot be started.

Files added to a knowledge base are saved first, and their ingestion run is
started once the request's transaction commits. When that start fails (for
example Temporal cannot be reached), the update answers an error, but the
files are already saved. They must then show as failed instead of staying
Processing with no run behind them, the knowledge base's other files must be
left alone, and adding the same file again must work.

As in a deployment, the start runs after the view's transaction block has
written its rows, and an error from the start reaches the view's error
handler. Under the test database that block is a savepoint, so its exit
stands in for the commit here. Temporal is replaced by a stand-in that
refuses a workflow id whose run is still open and can be told to fail the
next start it would accept. S3 uploads are patched out.
"""

import contextlib
import json
import sys
import uuid
from types import SimpleNamespace

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction as django_transaction
from rest_framework import status
from temporalio.exceptions import WorkflowAlreadyStartedError

from model_hub.models.choices import StatusType
from model_hub.models.develop_dataset import KnowledgeBaseFile

VIEW = "model_hub.views.develop_dataset.CreateKnowledgeBaseView"
MODULE = "model_hub.views.develop_dataset"
ENTITLEMENTS = "ee.usage.services.entitlements.Entitlements"
KB_URL = "/model-hub/knowledge-base/"
INGEST_ACTIVITY = "ingest_kb_files_activity"
ADDED_NAME = "changelog.txt"
ADDED_CONTENT = b"Version 2 adds search."


def _txt(name, content):
    return SimpleUploadedFile(name, content, content_type="text/plain")


class _TemporalStarts:
    """Records workflow starts and applies Temporal's id rule to them.

    A workflow id whose run is still open is refused with
    ``WorkflowAlreadyStartedError``; ``finish_runs`` closes every open run.
    After ``fail_next_start(error)``, the next start that would otherwise be
    accepted raises ``error`` instead and opens no run.
    """

    def __init__(self):
        self.running = set()
        self.started = []
        self.refused = []
        self.failed = []
        self._next_error = None

    def fail_next_start(self, error):
        self._next_error = error

    def finish_runs(self):
        self.running.clear()

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
        if workflow_id in self.running:
            self.refused.append(workflow_id)
            raise WorkflowAlreadyStartedError(workflow_id, "TaskRunnerWorkflow")
        if self._next_error is not None:
            error, self._next_error = self._next_error, None
            self.failed.append(workflow_id)
            raise error
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

    def task_id_that_carried(self, file_id):
        for start in self.started:
            if start["activity"] == INGEST_ACTIVITY and file_id in {
                str(key) for key in start["args"][0]
            }:
                return start["task_id"]
        return None


@pytest.fixture
def allow_entitlements(mocker):
    allowed = mocker.MagicMock(allowed=True, reason="")
    mocker.patch(f"{ENTITLEMENTS}.can_create", return_value=allowed)
    mocker.patch(f"{ENTITLEMENTS}.check_feature", return_value=allowed)


@pytest.fixture
def no_s3_upload(mocker):
    mocker.patch(f"{VIEW}._upload_file_to_s3_background")


@pytest.fixture
def ingestion_starts_after_commit(mocker):
    """Run commit callbacks when the view's transaction block exits cleanly.

    The view's rows are written (the savepoint is released) before the
    callbacks run, and an exception from a callback leaves the block and
    reaches the view's error handler, as with the outermost block in a
    deployment. A block that raises drops its callbacks, as a rollback does.
    """
    callbacks = []

    def on_commit(func, using=None, robust=False):
        callbacks.append(func)

    @contextlib.contextmanager
    def atomic(*args, **kwargs):
        callbacks.clear()
        with django_transaction.atomic(*args, **kwargs):
            yield
        pending = list(callbacks)
        callbacks.clear()
        for callback in pending:
            callback()

    mocker.patch(
        f"{MODULE}.transaction",
        SimpleNamespace(atomic=atomic, on_commit=on_commit),
    )
    mocker.patch(
        "model_hub.utils.kb_helpers.transaction",
        SimpleNamespace(on_commit=on_commit),
    )


@pytest.fixture
def temporal(mocker):
    starts = _TemporalStarts()
    mocker.patch(
        "tfc.temporal.drop_in.start_activity", side_effect=starts.start_activity
    )
    return starts


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


def _create_kb(auth_client):
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
    return created.json()["result"]


def _add_file(auth_client, created_result):
    return auth_client.patch(
        KB_URL,
        {
            "kb_id": str(created_result["kb_id"]),
            "name": created_result["kb_name"],
            "file": [_txt(ADDED_NAME, ADDED_CONTENT)],
        },
        format="multipart",
    )


def _metadata(file_row):
    metadata = file_row.metadata
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    return metadata or {}


def _files_by_name(kb_id):
    kb = KnowledgeBaseFile.objects.get(id=kb_id)
    rows = {}
    for file_row in kb.files.all():
        rows.setdefault(file_row.name, []).append(file_row)
    return rows


def _assert_added_file_failed_and_others_untouched(kb_id, temporal):
    rows = _files_by_name(kb_id)
    assert set(rows) == {"guide.txt", "faq.txt", ADDED_NAME}
    assert len(rows[ADDED_NAME]) == 1
    added = rows[ADDED_NAME][0]

    assert added.status == StatusType.FAILED.value, (
        f"the added file is {added.status!r} with no ingestion run behind it; "
        f"starts: {temporal.started}; failed starts: {temporal.failed}"
    )
    metadata = _metadata(added)
    assert isinstance(metadata.get("error"), str) and metadata["error"], metadata
    assert metadata.get("size") == len(ADDED_CONTENT), metadata

    for name in ("guide.txt", "faq.txt"):
        assert [row.status for row in rows[name]] == [
            StatusType.PROCESSING.value
        ], f"{name} belongs to the create's batch and must be left alone"

    assert str(added.id) not in temporal.file_ids_sent_to_ingestion()
    return str(added.id)


def _assert_added_again_and_ingested(kb_id, again, failed_id, temporal, errors):
    assert again.status_code == status.HTTP_200_OK, (
        f"adding the file again answered {again.status_code} {again.json()}; "
        f"view caught {errors}"
    )
    rows = _files_by_name(kb_id)
    added_again = [row for row in rows[ADDED_NAME] if str(row.id) != failed_id]
    assert len(added_again) == 1, rows[ADDED_NAME]
    new_id = str(added_again[0].id)
    assert added_again[0].status == StatusType.PROCESSING.value

    assert new_id in temporal.file_ids_sent_to_ingestion(), (
        f"no ingestion run was started for the file added again; starts: "
        f"{temporal.started}"
    )
    assert failed_id not in temporal.file_ids_sent_to_ingestion()
    return new_id


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.usefixtures(
    "allow_entitlements", "no_s3_upload", "ingestion_starts_after_commit"
)
class TestKnowledgeBaseIngestionStartFailure:
    def test_failed_start_of_the_separate_run_marks_the_added_file_failed(
        self, auth_client, temporal, view_errors
    ):
        created_result = _create_kb(auth_client)
        kb_id = str(created_result["kb_id"])
        fixed_workflow_id = f"task-kb-ingest-{kb_id}"
        assert temporal.running == {fixed_workflow_id}

        # The create's run is still open, so the update's batch goes to a run
        # of its own, and that start fails.
        temporal.fail_next_start(RuntimeError("Temporal is unavailable"))
        failed = _add_file(auth_client, created_result)

        assert failed.status_code == status.HTTP_400_BAD_REQUEST, failed.json()
        assert temporal.refused == [fixed_workflow_id]
        assert len(temporal.failed) == 1
        assert temporal.failed[0].startswith(f"{fixed_workflow_id}-")
        failed_id = _assert_added_file_failed_and_others_untouched(kb_id, temporal)

        again = _add_file(auth_client, created_result)

        new_id = _assert_added_again_and_ingested(
            kb_id, again, failed_id, temporal, view_errors
        )
        assert temporal.task_id_that_carried(new_id).startswith(
            f"kb-ingest-{kb_id}-"
        )

    def test_failed_start_on_a_settled_knowledge_base_marks_the_added_file_failed(
        self, auth_client, temporal, view_errors
    ):
        created_result = _create_kb(auth_client)
        kb_id = str(created_result["kb_id"])
        fixed_workflow_id = f"task-kb-ingest-{kb_id}"
        temporal.finish_runs()

        temporal.fail_next_start(RuntimeError("Temporal is unavailable"))
        failed = _add_file(auth_client, created_result)

        assert failed.status_code == status.HTTP_400_BAD_REQUEST, failed.json()
        assert temporal.refused == []
        assert temporal.failed == [fixed_workflow_id]
        failed_id = _assert_added_file_failed_and_others_untouched(kb_id, temporal)

        again = _add_file(auth_client, created_result)

        new_id = _assert_added_again_and_ingested(
            kb_id, again, failed_id, temporal, view_errors
        )
        assert temporal.task_id_that_carried(new_id) == f"kb-ingest-{kb_id}"
