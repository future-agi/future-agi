"""An Observe import that names a source kind records each new row's source.

DATA-E2E-095 and DATA-E2E-096 import a trace and a session into a dataset and
open the row's source. The import request names only the kind; the worker
takes each row's trace, span and session from the row's own span. A request
without a kind records nothing, as before, and the kind never changes which
rows import.
"""

import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework import status

from model_hub.models.choices import DataTypeChoices, ModelTypes, SourceChoices
from model_hub.models.develop_dataset import Column, Dataset, Row
from model_hub.models.row_sources import RowSource
from tracer.models.observation_span import ObservationSpan
from tracer.tasks.dataset import process_spans_chunk_task

NEW = "/tracer/dataset/add_to_new_dataset/"
EXISTING = "/tracer/dataset/add_to_existing_dataset/"
MAPPING = [{"col_name": "input", "span_field": "input", "data_type": "text"}]
ALLOWED = "tracer.views.dataset.check_if_dataset_creation_is_allowed"
TASK = "tracer.views.dataset.process_spans_chunk_task"


def get_result(response):
    data = response.json()
    return data.get("result", data)


@pytest.fixture(autouse=True)
def row_source_default(monkeypatch):
    """Recorded sources with the switch unset, as on a stock install."""

    monkeypatch.delenv("DSX_DATASET_ROW_SOURCE_ENABLED", raising=False)


@pytest.fixture
def root_spans(db, observe_project, session_trace):
    """Two root spans of one trace in a session."""

    now = timezone.now()
    return [
        ObservationSpan.no_workspace_objects.create(
            id=f"src_{i}_{uuid.uuid4().hex[:12]}",
            project=observe_project,
            trace=session_trace,
            name=f"turn {i}",
            observation_type="llm",
            start_time=now - timedelta(seconds=10 - i),
            end_time=now - timedelta(seconds=9 - i),
            input={"messages": [{"role": "user", "content": f"question {i}"}]},
            output={"choices": [{"message": {"content": f"answer {i}"}}]},
            model="stub-model",
            status="OK",
        )
        for i in range(2)
    ]


@pytest.fixture
def existing_dataset(db, organization, user, workspace):
    dataset = Dataset.no_workspace_objects.create(
        name=f"source-target-{uuid.uuid4().hex[:6]}",
        organization=organization,
        workspace=workspace,
        user=user,
        model_type=ModelTypes.GENERATIVE_LLM.value,
    )
    Column.no_workspace_objects.create(
        name="input",
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
        dataset=dataset,
    )
    return dataset


def _new(observe_project, spans, **extra):
    return {
        "new_dataset_name": f"source-{uuid.uuid4().hex[:6]}",
        "project": str(observe_project.id),
        "span_ids": [s.id for s in spans],
        "mapping_config": MAPPING,
        **extra,
    }


def _context(kind, project):
    return {"v": 1, "kind": kind, "project_id": str(project.id)}


@pytest.mark.integration
@pytest.mark.api
class TestTheImportHandsItsSourceKindToTheWorker:
    @pytest.mark.parametrize("kind", ["trace", "span", "session"])
    @patch(TASK)
    @patch(ALLOWED, return_value=(True, {}))
    def test_a_named_kind_reaches_the_worker_with_the_import_project(
        self, _allowed, task, kind, auth_client, observe_project, root_spans, ch_seed
    ):
        ch_seed(root_spans)
        response = auth_client.post(
            NEW, _new(observe_project, root_spans, source_kind=kind), format="json"
        )
        assert response.status_code == status.HTTP_200_OK
        task.delay.assert_called_once()
        assert task.delay.call_args.args[5] == _context(kind, observe_project)

    @patch(TASK)
    @patch(ALLOWED, return_value=(True, {}))
    def test_without_a_kind_the_worker_records_nothing(
        self, _allowed, task, auth_client, observe_project, root_spans, ch_seed
    ):
        ch_seed(root_spans)
        response = auth_client.post(
            NEW, _new(observe_project, root_spans), format="json"
        )
        assert response.status_code == status.HTTP_200_OK
        task.delay.assert_called_once()
        assert len(task.delay.call_args.args) == 6
        assert task.delay.call_args.args[5] is None

    @patch(TASK)
    def test_an_import_into_an_existing_dataset_hands_its_kind_on_too(
        self,
        task,
        auth_client,
        observe_project,
        trace_session,
        root_spans,
        existing_dataset,
        ch_seed,
    ):
        ch_seed(root_spans)
        response = auth_client.post(
            EXISTING,
            {
                "dataset_id": str(existing_dataset.id),
                "project": str(observe_project.id),
                "span_ids": [s.id for s in root_spans],
                "mapping_config": MAPPING,
                "source_kind": "session",
                "session_ids": [str(trace_session.id)],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        task.delay.assert_called_once()
        assert task.delay.call_args.args[5] == _context("session", observe_project)

    @pytest.mark.parametrize(
        "extra",
        [
            {"source_kind": "traces"},
            {"source_kind": "session", "session_ids": ["s"] * 201},
            {"source_kind": "session", "session_ids": ["s" * 256]},
        ],
        ids=["plural kind", "too many session ids", "session id too long"],
    )
    @patch(TASK)
    @patch(ALLOWED, return_value=(True, {}))
    def test_a_malformed_source_is_refused_before_anything_is_created(
        self, _allowed, task, extra, auth_client, observe_project, root_spans, ch_seed
    ):
        ch_seed(root_spans)
        before = Dataset.no_workspace_objects.count()
        response = auth_client.post(
            NEW, _new(observe_project, root_spans, **extra), format="json"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Dataset.no_workspace_objects.count() == before
        task.delay.assert_not_called()


@pytest.mark.integration
@pytest.mark.api
# The worker closes old connections as it starts, which would close a TestCase
# transaction mid-test; a transactional db, as the worker's own tests use.
@pytest.mark.django_db(transaction=True)
class TestAProductionSheetImportRecordsEachRowsOwnSource:
    """The sheet's request: the preview's root spans by span id, with a kind."""

    @pytest.mark.parametrize("kind", ["trace", "session"])
    @patch(ALLOWED, return_value=(True, {}))
    def test_span_ids_with_a_kind_record_the_rows_trace_or_session(
        self,
        _allowed,
        kind,
        auth_client,
        observe_project,
        session_trace,
        trace_session,
        root_spans,
        ch_seed,
    ):
        ch_seed(root_spans)
        extra = {"source_kind": kind}
        if kind == "session":
            extra["session_ids"] = [str(trace_session.id)]
        with patch(TASK) as task:
            response = auth_client.post(
                NEW, _new(observe_project, root_spans, **extra), format="json"
            )
        assert response.status_code == status.HTTP_200_OK
        task.delay.assert_called_once()
        # The worker then runs on exactly what the request queued.
        process_spans_chunk_task.run_sync(*task.delay.call_args.args)
        dataset_id = get_result(response)["dataset_id"]
        rows = {
            row.id
            for row in Row.no_workspace_objects.filter(
                dataset_id=dataset_id, deleted=False
            )
        }
        assert len(rows) == 2
        records = list(RowSource.objects.filter(dataset_id=dataset_id))
        assert {record.row_id for record in records} == rows
        assert {record.span_id for record in records} == {s.id for s in root_spans}
        for record in records:
            assert record.kind == kind
            assert str(record.project_id) == str(observe_project.id)
            assert record.trace_id == str(session_trace.id)
            assert record.session_id == str(trace_session.id)
            assert record.session_missing is False

    @patch(ALLOWED, return_value=(True, {}))
    def test_with_the_switch_off_a_session_import_records_no_source(
        self,
        _allowed,
        settings,
        auth_client,
        observe_project,
        trace_session,
        root_spans,
        ch_seed,
    ):
        settings.DSX_DATASET_ROW_SOURCE_ENABLED = False
        ch_seed(root_spans)
        body = _new(
            observe_project,
            root_spans,
            source_kind="session",
            session_ids=[str(trace_session.id)],
        )
        with patch(TASK) as task:
            response = auth_client.post(NEW, body, format="json")
        assert response.status_code == status.HTTP_200_OK
        task.delay.assert_called_once()
        # Off, the export leaves out each span's session: record nothing.
        assert task.delay.call_args.args[5] is None
        process_spans_chunk_task.run_sync(*task.delay.call_args.args)
        dataset_id = get_result(response)["dataset_id"]
        rows = Row.no_workspace_objects.filter(dataset_id=dataset_id, deleted=False)
        assert rows.count() == 2
        assert not RowSource.objects.filter(dataset_id=dataset_id).exists()
