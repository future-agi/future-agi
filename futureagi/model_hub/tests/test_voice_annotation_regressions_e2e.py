import csv
import io
import json
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework import status

from accounts.models.user import User
from model_hub.models.ai_model import AIModel
from model_hub.models.annotation_queues import (
    AnnotationQueue,
    AnnotationQueueAnnotator,
    AnnotationQueueLabel,
    QueueItem,
    QueueItemNote,
)
from model_hub.models.choices import (
    AnnotationQueueStatusChoices,
    AnnotationTypeChoices,
    CellStatus,
    DatasetSourceChoices,
    DataTypeChoices,
    QueueItemSourceType,
    QueueItemStatus,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import EvalTemplate
from model_hub.models.score import Score
from simulate.models.agent_definition import AgentDefinition
from simulate.models.run_test import RunTest
from simulate.models.scenario_graph import ScenarioGraph
from simulate.models.scenarios import Scenarios
from simulate.models.test_execution import (
    CallExecution,
)
from simulate.models.test_execution import (
    TestExecution as SimTestExecution,
)
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.observation_span import EvalLogger, ObservationSpan
from tracer.models.project import Project
from tracer.models.span_notes import SpanNotes
from tracer.models.trace import Trace
from tracer.tests._ch_seed import seed_ch_span


@pytest.fixture
def observe_project(db, organization, workspace):
    return Project.objects.create(
        name=f"Voice Annotation Observe {uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )


@pytest.fixture
def observe_trace(db, observe_project):
    return Trace.objects.create(
        project=observe_project,
        name="Voice observe trace",
        input={"prompt": "hello"},
        output={"response": "world"},
    )


@pytest.fixture
def root_conversation_span(db, observe_project, observe_trace):
    span = ObservationSpan.objects.create(
        id=f"voice_root_{uuid.uuid4().hex[:16]}",
        project=observe_project,
        trace=observe_trace,
        name="Voice root conversation",
        observation_type="conversation",
        start_time=timezone.now() - timedelta(seconds=10),
        end_time=timezone.now(),
        input={"messages": [{"role": "user", "content": "hi"}]},
        output={"messages": [{"role": "assistant", "content": "hello"}]},
        latency_ms=1000,
        status="OK",
    )
    # Tracer sources resolve CH-native; mirror this root span into ClickHouse so
    # the trace/span resolves (the PG row alone is no longer read).
    seed_ch_span(span)
    return span


@pytest.fixture
def thumbs_label(db, organization, workspace, observe_project):
    return AnnotationsLabels.objects.create(
        name=f"voice-thumbs-{uuid.uuid4().hex[:8]}",
        type=AnnotationTypeChoices.THUMBS_UP_DOWN.value,
        organization=organization,
        workspace=workspace,
        project=observe_project,
        allow_notes=True,
    )


@pytest.fixture
def star_label(db, organization, workspace, observe_project):
    return AnnotationsLabels.objects.create(
        name=f"voice-star-{uuid.uuid4().hex[:8]}",
        type=AnnotationTypeChoices.STAR.value,
        organization=organization,
        workspace=workspace,
        project=observe_project,
        allow_notes=True,
    )


@pytest.fixture
def simulation_agent_definition(db, organization, workspace):
    return AgentDefinition.objects.create(
        agent_name=f"Voice Sim Agent {uuid.uuid4().hex[:8]}",
        inbound=True,
        description="Voice annotation regression agent",
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def simulation_dataset_row(db, organization, workspace):
    dataset = Dataset.objects.create(
        name=f"Voice Sim Dataset {uuid.uuid4().hex[:8]}",
        source=DatasetSourceChoices.SCENARIO.value,
        organization=organization,
        workspace=workspace,
    )
    column = Column.objects.create(
        name="customer_goal",
        data_type=DataTypeChoices.TEXT.value,
        dataset=dataset,
        source=SourceChoices.OTHERS.value,
        status=StatusType.COMPLETED.value,
    )
    dataset.column_order = [str(column.id)]
    dataset.save(update_fields=["column_order", "updated_at"])

    row = Row.objects.create(
        dataset=dataset,
        order=0,
        metadata={"session_id": "voice-e2e-session"},
    )
    Cell.objects.create(
        dataset=dataset,
        column=column,
        row=row,
        value="Order one cheeseburger",
        status=CellStatus.PASS.value,
    )
    return row


@pytest.fixture
def simulation_call_execution(
    db,
    organization,
    workspace,
    simulation_agent_definition,
    simulation_dataset_row,
):
    run_test = RunTest.objects.create(
        name=f"Voice Sim Run {uuid.uuid4().hex[:8]}",
        agent_definition=simulation_agent_definition,
        organization=organization,
        workspace=workspace,
    )
    test_execution = SimTestExecution.objects.create(
        run_test=run_test,
        agent_definition=simulation_agent_definition,
        status=SimTestExecution.ExecutionStatus.COMPLETED,
        total_scenarios=1,
        total_calls=1,
        completed_calls=1,
    )
    scenario = Scenarios.objects.create(
        name="Fast food scenario",
        source="Voice queue scenario",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        dataset=simulation_dataset_row.dataset,
        agent_definition=simulation_agent_definition,
    )
    return CallExecution.objects.create(
        test_execution=test_execution,
        scenario=scenario,
        status=CallExecution.CallStatus.COMPLETED,
        duration_seconds=42,
        call_metadata={"row_id": str(simulation_dataset_row.id)},
    )


def _queue(name, organization, workspace, user, **kwargs):
    return AnnotationQueue.objects.create(
        name=f"{name} {uuid.uuid4().hex[:8]}",
        status=kwargs.pop("status", AnnotationQueueStatusChoices.ACTIVE.value),
        organization=organization,
        workspace=workspace,
        created_by=user,
        **kwargs,
    )


def _annotate_detail_url(queue, item):
    return f"/model-hub/annotation-queues/{queue.id}/items/{item.id}/annotate-detail/"


def _submit_url(queue, item):
    return (
        f"/model-hub/annotation-queues/{queue.id}/items/{item.id}/"
        "annotations/submit/"
    )


def _complete_url(queue, item):
    return f"/model-hub/annotation-queues/{queue.id}/items/{item.id}/complete/"


def _add_items_url(queue):
    return f"/model-hub/annotation-queues/{queue.id}/items/add-items/"


@pytest.mark.django_db
class TestVoiceAnnotationRegressionE2E:
    def test_th4825_direct_voice_call_add_items_keeps_trace_source(
        self,
        auth_client,
        organization,
        workspace,
        user,
        observe_project,
        observe_trace,
        root_conversation_span,
    ):
        queue = _queue(
            "TH-4825 direct call trace queue",
            organization,
            workspace,
            user,
            project=observe_project,
        )

        resp = auth_client.post(
            _add_items_url(queue),
            {
                "items": [
                    {
                        "source_type": QueueItemSourceType.TRACE.value,
                        "source_id": str(observe_trace.id),
                    }
                ]
            },
            format="json",
        )

        assert resp.status_code == status.HTTP_200_OK, resp.data
        assert resp.data["result"]["added"] == 1
        item = QueueItem.objects.get(queue=queue, trace=observe_trace, deleted=False)
        assert item.source_type == QueueItemSourceType.TRACE.value
        assert item.trace_id == observe_trace.id
        assert item.observation_span_id is None
        assert item.observation_span_id != root_conversation_span.id

    def test_th5175_enumerated_add_rejects_in_progress_trace(
        self,
        auth_client,
        organization,
        workspace,
        user,
        observe_project,
    ):
        queue = _queue(
            "TH-5175 in-progress trace queue",
            organization,
            workspace,
            user,
            project=observe_project,
        )
        in_progress_trace = Trace.objects.create(
            project=observe_project,
            name="In-progress voice call trace",
        )
        in_progress_span = ObservationSpan.objects.create(
            id=f"voice_in_progress_{uuid.uuid4().hex[:16]}",
            project=observe_project,
            trace=in_progress_trace,
            name="In-progress voice root",
            observation_type="conversation",
            start_time=timezone.now(),
            parent_span_id=None,
            status="UNSET",
        )
        seed_ch_span(in_progress_span)

        resp = auth_client.post(
            _add_items_url(queue),
            {
                "items": [
                    {
                        "source_type": QueueItemSourceType.TRACE.value,
                        "source_id": str(in_progress_trace.id),
                    }
                ]
            },
            format="json",
        )

        assert resp.status_code == status.HTTP_200_OK, resp.data
        result = resp.data["result"]
        assert result["added"] == 0
        assert result["duplicates"] == 0
        assert "still in progress" in result["errors"][0]
        assert not QueueItem.objects.filter(
            queue=queue,
            trace=in_progress_trace,
            deleted=False,
        ).exists()

    def test_th5175_filter_add_skips_in_progress_traces(
        self,
        auth_client,
        organization,
        workspace,
        user,
        observe_project,
        observe_trace,
        root_conversation_span,
    ):
        queue = _queue(
            "TH-5175 filter skips in-progress traces",
            organization,
            workspace,
            user,
            project=observe_project,
        )
        in_progress_trace = Trace.objects.create(
            project=observe_project,
            name="In-progress voice call trace",
        )
        in_progress_span = ObservationSpan.objects.create(
            id=f"voice_filter_in_progress_{uuid.uuid4().hex[:16]}",
            project=observe_project,
            trace=in_progress_trace,
            name="In-progress voice root",
            observation_type="conversation",
            start_time=timezone.now(),
            parent_span_id=None,
            status="UNSET",
        )
        seed_ch_span(in_progress_span)

        resp = auth_client.post(
            _add_items_url(queue),
            {
                "selection": {
                    "mode": "filter",
                    "source_type": QueueItemSourceType.TRACE.value,
                    "project_id": str(observe_project.id),
                }
            },
            format="json",
        )

        assert resp.status_code == status.HTTP_200_OK, resp.data
        result = resp.data["result"]
        assert result["added"] == 1
        assert result["duplicates"] == 0
        assert result["total_matching"] == 2
        assert result["errors"] == [
            "1 trace is still in progress and was not added to the annotation queue."
        ]
        assert QueueItem.objects.filter(
            queue=queue,
            trace=observe_trace,
            deleted=False,
        ).exists()
        assert not QueueItem.objects.filter(
            queue=queue,
            trace=in_progress_trace,
            deleted=False,
        ).exists()

    def test_filter_add_default_workspace_accepts_null_workspace_trace(
        self,
        auth_client,
        organization,
        workspace,
        user,
    ):
        legacy_project = Project.objects.create(
            name=f"Legacy null workspace observe {uuid.uuid4().hex[:8]}",
            organization=organization,
            workspace=None,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="observe",
        )
        legacy_trace = Trace.objects.create(
            project=legacy_project,
            name="Legacy null workspace trace",
        )
        # Trace filter-mode resolves from ClickHouse only — seed the root span to
        # CH (built in memory, never written to the PG tracer tables).
        seed_ch_span(
            ObservationSpan(
                id=f"legacy_null_ws_root_{uuid.uuid4().hex[:16]}",
                project=legacy_project,
                trace=legacy_trace,
                name="Legacy null workspace root",
                observation_type="conversation",
                start_time=timezone.now(),
                parent_span_id=None,
                status="ok",
            )
        )
        queue = _queue(
            "Default workspace null-workspace trace",
            organization,
            workspace,
            user,
            project=legacy_project,
        )

        resp = auth_client.post(
            _add_items_url(queue),
            {
                "selection": {
                    "mode": "filter",
                    "source_type": QueueItemSourceType.TRACE.value,
                    "project_id": str(legacy_project.id),
                }
            },
            format="json",
        )

        assert resp.status_code == status.HTTP_200_OK, resp.data
        result = resp.data["result"]
        assert result["added"] == 1
        assert result["total_matching"] == 1
        assert result["errors"] == []
        assert QueueItem.objects.filter(
            queue=queue,
            trace=legacy_trace,
            deleted=False,
        ).exists()

    def test_th4782_simulation_queue_submit_uses_call_execution_source(
        self,
        auth_client,
        organization,
        workspace,
        user,
        simulation_agent_definition,
        simulation_call_execution,
        thumbs_label,
    ):
        queue = _queue(
            "TH-4782 voice simulation queue",
            organization,
            workspace,
            user,
            agent_definition=simulation_agent_definition,
        )
        AnnotationQueueLabel.objects.create(queue=queue, label=thumbs_label)
        item = QueueItem.objects.create(
            queue=queue,
            source_type=QueueItemSourceType.CALL_EXECUTION.value,
            call_execution=simulation_call_execution,
            organization=organization,
            workspace=workspace,
            status=QueueItemStatus.PENDING.value,
        )

        submit_resp = auth_client.post(
            _submit_url(queue, item),
            {
                "annotations": [
                    {
                        "label_id": str(thumbs_label.id),
                        "value": {"value": "up"},
                        "notes": "sim label note",
                    }
                ]
            },
            format="json",
        )

        assert submit_resp.status_code == status.HTTP_200_OK, submit_resp.data
        score = Score.objects.get(
            call_execution=simulation_call_execution,
            label=thumbs_label,
            annotator=user,
            deleted=False,
        )
        assert score.source_type == QueueItemSourceType.CALL_EXECUTION.value
        assert score.queue_item == item
        assert score.notes == "sim label note"

        score_resp = auth_client.get(
            "/model-hub/scores/for-source/",
            {
                "source_type": QueueItemSourceType.CALL_EXECUTION.value,
                "source_id": str(simulation_call_execution.id),
            },
        )
        assert score_resp.status_code == status.HTTP_200_OK, score_resp.data
        assert score_resp.data["result"][0]["queue_id"] == str(queue.id)
        assert str(score_resp.data["result"][0]["queue_item"]) == str(item.id)

        detail_resp = auth_client.get(_annotate_detail_url(queue, item))
        assert detail_resp.status_code == status.HTTP_200_OK, detail_resp.data
        detail = detail_resp.data["result"]
        assert detail["item"]["source_type"] == QueueItemSourceType.CALL_EXECUTION.value
        assert (
            detail["annotations"][0]["source_type"]
            == QueueItemSourceType.CALL_EXECUTION.value
        )

    def test_th4055_trace_call_annotation_reopens_with_labels_and_item_notes(
        self,
        auth_client,
        organization,
        workspace,
        user,
        observe_project,
        observe_trace,
        root_conversation_span,
        thumbs_label,
    ):
        from tracer.tests._ch_seed import seed_ch_span

        # _span_notes_target_for_queue_item reads root span from CH
        seed_ch_span(root_conversation_span)

        queue = _queue(
            "TH-4055 trace call queue",
            organization,
            workspace,
            user,
            project=observe_project,
        )
        AnnotationQueueLabel.objects.create(queue=queue, label=thumbs_label)
        item = QueueItem.objects.create(
            queue=queue,
            source_type=QueueItemSourceType.TRACE.value,
            trace=observe_trace,
            organization=organization,
            workspace=workspace,
            status=QueueItemStatus.PENDING.value,
        )

        # Score writes are now per-queue. Pass queue_item_id explicitly so
        # the bulk score lands in this test queue's review context. Without
        # it, the score would resolve to the project's default queue and
        # this queue's annotate-detail would stay empty (correct under the
        # new policy but not what this regression is exercising).
        bulk_resp = auth_client.post(
            "/model-hub/scores/bulk/",
            {
                "source_type": QueueItemSourceType.TRACE.value,
                "source_id": str(observe_trace.id),
                "queue_item_id": str(item.id),
                "scores": [
                    {
                        "label_id": str(thumbs_label.id),
                        "value": {"value": "up"},
                        "notes": "trace label note",
                    }
                ],
                "span_notes": "whole call note",
                "span_notes_source_id": root_conversation_span.id,
            },
            format="json",
        )

        assert bulk_resp.status_code == status.HTTP_200_OK, bulk_resp.data
        assert Score.objects.filter(
            source_type=QueueItemSourceType.TRACE.value,
            trace=observe_trace,
            label=thumbs_label,
            annotator=user,
            queue_item=item,
            deleted=False,
        ).exists()
        assert not Score.objects.filter(
            source_type=QueueItemSourceType.OBSERVATION_SPAN.value,
            observation_span=root_conversation_span,
            label=thumbs_label,
            annotator=user,
            deleted=False,
        ).exists()
        assert (
            SpanNotes.objects.get(
                span=root_conversation_span,
                created_by_user=user,
            ).notes
            == "whole call note"
        )

        detail_resp = auth_client.get(_annotate_detail_url(queue, item))
        assert detail_resp.status_code == status.HTTP_200_OK, detail_resp.data
        detail = detail_resp.data["result"]
        assert detail["existing_notes"] == "whole call note"
        assert detail["span_notes_source_id"] == root_conversation_span.id
        assert str(detail["annotations"][0]["label_id"]) == str(thumbs_label.id)
        assert detail["annotations"][0]["value"] == {"value": "up"}
        assert detail["annotations"][0]["notes"] == "trace label note"

        for_source_resp = auth_client.get(
            "/model-hub/annotation-queues/for-source/",
            {
                "sources": json.dumps(
                    [
                        {
                            "source_type": QueueItemSourceType.TRACE.value,
                            "source_id": str(observe_trace.id),
                            "span_notes_source_id": root_conversation_span.id,
                        }
                    ]
                )
            },
        )
        assert for_source_resp.status_code == status.HTTP_200_OK, for_source_resp.data
        queue_entry = for_source_resp.data["result"][0]
        assert queue_entry["existing_scores"][str(thumbs_label.id)] == {"value": "up"}
        assert queue_entry["existing_notes"] == "whole call note"

    def test_th4861_trace_item_notes_do_not_backfill_label_notes_on_submit(
        self,
        auth_client,
        organization,
        workspace,
        user,
        observe_project,
        observe_trace,
        root_conversation_span,
        thumbs_label,
    ):
        from tracer.tests._ch_seed import seed_ch_span

        # _span_notes_target_for_queue_item reads root span from CH
        seed_ch_span(root_conversation_span)

        queue = _queue(
            "TH-4861 trace note separation queue",
            organization,
            workspace,
            user,
            project=observe_project,
        )
        AnnotationQueueLabel.objects.create(queue=queue, label=thumbs_label)
        item = QueueItem.objects.create(
            queue=queue,
            source_type=QueueItemSourceType.TRACE.value,
            trace=observe_trace,
            organization=organization,
            workspace=workspace,
            status=QueueItemStatus.PENDING.value,
        )

        submit_resp = auth_client.post(
            _submit_url(queue, item),
            {
                "annotations": [
                    {
                        "label_id": str(thumbs_label.id),
                        "value": {"value": "up"},
                    }
                ],
                "notes": "trace-level-only note",
            },
            format="json",
        )

        assert submit_resp.status_code == status.HTTP_200_OK, submit_resp.data
        score = Score.objects.get(
            source_type=QueueItemSourceType.TRACE.value,
            trace=observe_trace,
            label=thumbs_label,
            annotator=user,
            deleted=False,
        )
        assert score.notes in ("", None)
        assert (
            SpanNotes.objects.get(
                span=root_conversation_span,
                created_by_user=user,
            ).notes
            == "trace-level-only note"
        )

        detail_resp = auth_client.get(_annotate_detail_url(queue, item))
        assert detail_resp.status_code == status.HTTP_200_OK, detail_resp.data
        detail = detail_resp.data["result"]
        assert detail["existing_notes"] == "trace-level-only note"
        assert detail["annotations"][0]["notes"] in ("", None)

        for_source_resp = auth_client.get(
            "/model-hub/annotation-queues/for-source/",
            {
                "sources": json.dumps(
                    [
                        {
                            "source_type": QueueItemSourceType.TRACE.value,
                            "source_id": str(observe_trace.id),
                            "span_notes_source_id": root_conversation_span.id,
                        }
                    ]
                )
            },
        )
        assert for_source_resp.status_code == status.HTTP_200_OK, for_source_resp.data
        queue_entry = for_source_resp.data["result"][0]
        assert queue_entry["existing_scores"][str(thumbs_label.id)] == {"value": "up"}
        assert queue_entry["existing_notes"] == "trace-level-only note"
        assert queue_entry["existing_label_notes"] == {}

    def test_th4055_old_observe_span_add_annotations_syncs_default_queue(
        self,
        auth_client,
        organization,
        workspace,
        user,
        observe_project,
        root_conversation_span,
        star_label,
    ):
        from tracer.tests._ch_seed import seed_ch_span

        # for-source span_notes reads span from CH to resolve org ownership
        seed_ch_span(root_conversation_span)

        queue = _queue(
            "TH-4055 default observe queue",
            organization,
            workspace,
            user,
            project=observe_project,
            is_default=True,
        )
        AnnotationQueueLabel.objects.create(queue=queue, label=star_label)

        resp = auth_client.post(
            "/tracer/observation-span/add_annotations/",
            {
                "observation_span_id": root_conversation_span.id,
                "annotation_values": {str(star_label.id): 4},
                "notes": "old observe toolbar note",
            },
            format="json",
        )

        assert resp.status_code == status.HTTP_200_OK, resp.data
        result = resp.data["result"]
        assert result["success_labels"] == [str(star_label.id)]

        item = QueueItem.objects.get(
            queue=queue,
            source_type=QueueItemSourceType.OBSERVATION_SPAN.value,
            observation_span=root_conversation_span,
            deleted=False,
        )
        score = Score.objects.get(
            observation_span=root_conversation_span,
            label=star_label,
            annotator=user,
            deleted=False,
        )
        assert score.source_type == QueueItemSourceType.OBSERVATION_SPAN.value
        assert score.queue_item == item
        assert score.value == {"rating": 4.0}
        assert (
            SpanNotes.objects.get(
                span=root_conversation_span,
                created_by_user=user,
            ).notes
            == "old observe toolbar note"
        )
        assert QueueItemNote.objects.get(queue_item=item, annotator=user).notes == (
            "old observe toolbar note"
        )

        score_resp = auth_client.get(
            "/model-hub/scores/for-source/",
            {
                "source_type": QueueItemSourceType.OBSERVATION_SPAN.value,
                "source_id": root_conversation_span.id,
            },
        )
        assert score_resp.status_code == status.HTTP_200_OK, score_resp.data
        assert score_resp.data["span_notes"][0]["notes"] == "old observe toolbar note"

        queue_resp = auth_client.get(
            "/model-hub/annotation-queues/for-source/",
            {
                "source_type": QueueItemSourceType.OBSERVATION_SPAN.value,
                "source_id": root_conversation_span.id,
            },
        )
        assert queue_resp.status_code == status.HTTP_200_OK, queue_resp.data
        queue_entry = queue_resp.data["result"][0]
        assert queue_entry["existing_notes"] == "old observe toolbar note"
        assert queue_entry["existing_label_notes"][str(star_label.id)] == (
            "old observe toolbar note"
        )

    def test_th4759_simulation_call_detail_returns_scenario_columns(
        self,
        auth_client,
        organization,
        simulation_call_execution,
        simulation_dataset_row,
    ):
        ScenarioGraph.objects.create(
            name="Drive-thru flow",
            scenario=simulation_call_execution.scenario,
            organization=organization,
            graph_config={
                "graph_data": {
                    "nodes": [
                        {
                            "name": "Greeting",
                            "type": "conversation",
                            "messagePlan": {"firstMessage": "Hello"},
                        }
                    ],
                    "edges": [],
                }
            },
        )
        resp = auth_client.get(
            f"/simulate/call-executions/{simulation_call_execution.id}/"
        )

        assert resp.status_code == status.HTTP_200_OK, resp.data
        assert resp.data["test_execution_id"] == str(
            simulation_call_execution.test_execution_id
        )
        assert resp.data["scenario_graph"]["nodes"][0]["name"] == "Greeting"
        scenario_columns = resp.data["scenario_columns"]
        assert scenario_columns
        column_payload = next(iter(scenario_columns.values()))
        assert column_payload["column_name"] == "customer_goal"
        assert column_payload["value"] == "Order one cheeseburger"
        assert column_payload["dataset_id"] == str(simulation_dataset_row.dataset_id)

    def test_th5123_voice_call_detail_returns_simulation_path_context(
        self,
        auth_client,
        monkeypatch,
        organization,
        observe_trace,
        root_conversation_span,
        simulation_call_execution,
    ):
        from tracer.tests._ch_seed import (
            _get_ch_client,
            seed_ch_span,
            seed_ch_trace,
        )

        ScenarioGraph.objects.create(
            name="Order flow",
            scenario=simulation_call_execution.scenario,
            organization=organization,
            graph_config={
                "graph_data": {
                    "nodes": [
                        {
                            "name": "Take order",
                            "type": "conversation",
                            "messagePlan": {
                                "firstMessage": "What would you like to order?"
                            },
                        }
                    ],
                    "edges": [],
                }
            },
        )
        root_conversation_span.span_attributes = {
            "raw_log": {
                "id": "provider-call-123",
                "transcript": "Customer ordered food.",
            }
        }
        root_conversation_span.eval_attributes = {
            "fi.simulator.call_execution_id": str(simulation_call_execution.id)
        }
        root_conversation_span.save(
            update_fields=["span_attributes", "eval_attributes"]
        )

        # Seed AFTER .save() so CH has the updated span_attributes/eval_attributes
        seed_ch_span(root_conversation_span)
        # voice_call_detail resolves the trace's project from the CH `traces` table
        # (PG tracer_trace is dropped on CH25), so the trace row must be seeded too;
        # seeding only the span leaves the traces lookup empty (404).
        seed_ch_trace(observe_trace)
        # The root_conversation_span fixture already seeded this span WITHOUT
        # eval_attributes; the re-seed above added them, leaving two
        # ReplacingMergeTree(_version) rows for the same id. The endpoint reads
        # `spans` without FINAL, so it can return the stale (no-eval) version and
        # drop the simulation context (flaky KeyError: call_execution_id). Force the
        # merge so the later, eval-bearing re-seed (higher insert-time _version)
        # wins, leaving one deterministic row.
        _ch = _get_ch_client()
        try:
            _ch.command("OPTIMIZE TABLE spans FINAL")
        finally:
            _ch.close()

        resp = auth_client.get(
            "/tracer/trace/voice_call_detail/",
            {"trace_id": str(observe_trace.id)},
        )

        assert resp.status_code == status.HTTP_200_OK, resp.data
        result = resp.data["result"]
        assert result["call_execution_id"] == str(simulation_call_execution.id)
        assert result["test_execution_id"] == str(
            simulation_call_execution.test_execution_id
        )
        assert result["scenario_id"] == str(simulation_call_execution.scenario_id)
        assert result["scenario_graph"]["nodes"][0]["name"] == "Take order"

    def test_th3884_th3886_th3889_navigation_keeps_skipped_items_in_work(
        self,
        auth_client,
        organization,
        workspace,
        user,
        observe_project,
        observe_trace,
        root_conversation_span,
        thumbs_label,
    ):
        second_span = ObservationSpan.objects.create(
            id=f"voice_second_{uuid.uuid4().hex[:16]}",
            project=observe_project,
            trace=observe_trace,
            name="Second item",
            observation_type="conversation",
            start_time=timezone.now(),
            input={"messages": [{"role": "user", "content": "second"}]},
            output={"messages": [{"role": "assistant", "content": "ok"}]},
            status="OK",
        )
        queue = _queue(
            "TH-388 navigation queue",
            organization,
            workspace,
            user,
            project=observe_project,
        )
        AnnotationQueueLabel.objects.create(queue=queue, label=thumbs_label)
        first_item = QueueItem.objects.create(
            queue=queue,
            source_type=QueueItemSourceType.OBSERVATION_SPAN.value,
            observation_span=root_conversation_span,
            organization=organization,
            workspace=workspace,
            order=1,
            status=QueueItemStatus.PENDING.value,
        )
        skipped_item = QueueItem.objects.create(
            queue=queue,
            source_type=QueueItemSourceType.OBSERVATION_SPAN.value,
            observation_span=second_span,
            organization=organization,
            workspace=workspace,
            order=2,
            status=QueueItemStatus.SKIPPED.value,
        )
        base_time = timezone.now()
        QueueItem.objects.filter(id=first_item.id).update(
            created_at=base_time + timedelta(minutes=1)
        )
        QueueItem.objects.filter(id=skipped_item.id).update(created_at=base_time)

        submit_first = auth_client.post(
            _submit_url(queue, first_item),
            {
                "annotations": [
                    {
                        "label_id": str(thumbs_label.id),
                        "value": {"value": "up"},
                    }
                ]
            },
            format="json",
        )
        assert submit_first.status_code == status.HTTP_200_OK, submit_first.data
        complete_first = auth_client.post(
            _complete_url(queue, first_item),
            {"exclude": str(first_item.id)},
            format="json",
        )
        assert complete_first.status_code == status.HTTP_200_OK, complete_first.data
        assert complete_first.data["result"]["next_item"]["id"] == str(skipped_item.id)
        queue.refresh_from_db()
        assert queue.status == AnnotationQueueStatusChoices.ACTIVE.value

        queue.status = AnnotationQueueStatusChoices.COMPLETED.value
        queue.save(update_fields=["status"])
        submit_skipped = auth_client.post(
            _submit_url(queue, skipped_item),
            {
                "annotations": [
                    {
                        "label_id": str(thumbs_label.id),
                        "value": {"value": "down"},
                    }
                ]
            },
            format="json",
        )
        assert submit_skipped.status_code == status.HTTP_200_OK, submit_skipped.data
        queue.refresh_from_db()
        assert queue.status == AnnotationQueueStatusChoices.ACTIVE.value

        complete_skipped = auth_client.post(
            _complete_url(queue, skipped_item),
            {"exclude": f"{first_item.id},{skipped_item.id}"},
            format="json",
        )
        assert complete_skipped.status_code == status.HTTP_200_OK
        assert complete_skipped.data["result"]["next_item"] is None
        queue.refresh_from_db()
        assert queue.status == AnnotationQueueStatusChoices.COMPLETED.value

    def test_th3884_start_annotating_resumes_latest_skipped_item(
        self,
        auth_client,
        organization,
        workspace,
        user,
        observe_project,
        observe_trace,
        root_conversation_span,
        thumbs_label,
    ):
        second_span = ObservationSpan.objects.create(
            id=f"voice_second_{uuid.uuid4().hex[:16]}",
            project=observe_project,
            trace=observe_trace,
            name="Second skipped item",
            observation_type="conversation",
            start_time=timezone.now(),
            input={"messages": [{"role": "user", "content": "second"}]},
            output={"messages": [{"role": "assistant", "content": "ok"}]},
            status="OK",
        )
        third_span = ObservationSpan.objects.create(
            id=f"voice_third_{uuid.uuid4().hex[:16]}",
            project=observe_project,
            trace=observe_trace,
            name="Third skipped item",
            observation_type="conversation",
            start_time=timezone.now(),
            input={"messages": [{"role": "user", "content": "third"}]},
            output={"messages": [{"role": "assistant", "content": "ok"}]},
            status="OK",
        )
        queue = _queue(
            "TH-3884 resume skipped queue",
            organization,
            workspace,
            user,
            project=observe_project,
            status=AnnotationQueueStatusChoices.COMPLETED.value,
        )
        AnnotationQueueLabel.objects.create(queue=queue, label=thumbs_label)
        QueueItem.objects.create(
            queue=queue,
            source_type=QueueItemSourceType.OBSERVATION_SPAN.value,
            observation_span=root_conversation_span,
            organization=organization,
            workspace=workspace,
            order=1,
            status=QueueItemStatus.COMPLETED.value,
        )
        older_skipped = QueueItem.objects.create(
            queue=queue,
            source_type=QueueItemSourceType.OBSERVATION_SPAN.value,
            observation_span=second_span,
            organization=organization,
            workspace=workspace,
            order=2,
            status=QueueItemStatus.SKIPPED.value,
        )
        latest_skipped = QueueItem.objects.create(
            queue=queue,
            source_type=QueueItemSourceType.OBSERVATION_SPAN.value,
            observation_span=third_span,
            organization=organization,
            workspace=workspace,
            order=3,
            status=QueueItemStatus.SKIPPED.value,
        )
        base_time = timezone.now()
        QueueItem.objects.filter(id=older_skipped.id).update(created_at=base_time)
        QueueItem.objects.filter(id=latest_skipped.id).update(
            created_at=base_time + timedelta(minutes=1)
        )

        resp = auth_client.get(
            f"/model-hub/annotation-queues/{queue.id}/items/next-item/"
        )

        assert resp.status_code == status.HTTP_200_OK, resp.data
        assert resp.data["result"]["item"]["id"] == str(latest_skipped.id)

    def test_th3535_queue_item_preview_exposes_latency_response_metrics(
        self,
        auth_client,
        organization,
        workspace,
        user,
        observe_project,
        root_conversation_span,
        thumbs_label,
        simulation_agent_definition,
        simulation_call_execution,
    ):
        # CH has no separate response_time column — a span's response_time_ms
        # collapses to latency_ms (the only CH timing signal). The call_execution
        # branch is PG-backed and keeps its distinct response_time_ms.
        simulation_call_execution.response_time_ms = 456
        simulation_call_execution.avg_agent_latency_ms = 789
        simulation_call_execution.save(
            update_fields=["response_time_ms", "avg_agent_latency_ms"]
        )

        queue = _queue(
            "TH-3535 metrics queue",
            organization,
            workspace,
            user,
            project=observe_project,
            agent_definition=simulation_agent_definition,
        )
        AnnotationQueueLabel.objects.create(queue=queue, label=thumbs_label)
        QueueItem.objects.create(
            queue=queue,
            source_type=QueueItemSourceType.OBSERVATION_SPAN.value,
            observation_span=root_conversation_span,
            organization=organization,
            workspace=workspace,
            order=1,
        )
        QueueItem.objects.create(
            queue=queue,
            source_type=QueueItemSourceType.CALL_EXECUTION.value,
            call_execution=simulation_call_execution,
            organization=organization,
            workspace=workspace,
            order=2,
        )

        resp = auth_client.get(f"/model-hub/annotation-queues/{queue.id}/items/")
        assert resp.status_code == status.HTTP_200_OK, resp.data
        previews = [item["source_preview"] for item in resp.data["results"]]
        span_preview = next(p for p in previews if p["type"] == "observation_span")
        call_preview = next(p for p in previews if p["type"] == "call_execution")
        assert span_preview["latency_ms"] == 1000
        assert span_preview["response_time_ms"] == 1000  # == latency_ms (CH-native)
        assert call_preview["latency_ms"] == 789
        assert call_preview["response_time_ms"] == 456
        assert call_preview["duration_seconds"] == 42

    def test_th4735_export_to_dataset_supports_mapping_and_attributes(
        self,
        auth_client,
        organization,
        workspace,
        user,
        observe_project,
        root_conversation_span,
        thumbs_label,
    ):
        root_conversation_span.span_attributes = {
            "customer": {"tier": "gold"},
            "score": 7,
        }
        root_conversation_span.response_time = 321.0
        root_conversation_span.save(update_fields=["span_attributes", "response_time"])
        # Re-seed CH after mutating the span: export fields read span_attributes
        # CH-native, so the PG-only write above must be mirrored.
        seed_ch_span(root_conversation_span)
        queue = _queue(
            "TH-4735 export queue",
            organization,
            workspace,
            user,
            project=observe_project,
            annotations_required=2,
            requires_review=True,
        )
        AnnotationQueueLabel.objects.create(queue=queue, label=thumbs_label)
        second_annotator = User.objects.create_user(
            email=f"second-annotator-{uuid.uuid4().hex[:8]}@example.com",
            password="test",
            name="Second Annotator",
            organization=organization,
        )
        reviewer = User.objects.create_user(
            email=f"reviewer-{uuid.uuid4().hex[:8]}@example.com",
            password="test",
            name="Reviewer User",
            organization=organization,
        )
        item = QueueItem.objects.create(
            queue=queue,
            source_type=QueueItemSourceType.OBSERVATION_SPAN.value,
            observation_span=root_conversation_span,
            organization=organization,
            workspace=workspace,
            status=QueueItemStatus.COMPLETED.value,
            review_status="approved",
            reviewed_by=reviewer,
            reviewed_at=timezone.now(),
            review_notes="review export note",
        )
        Score.objects.create(
            source_type=QueueItemSourceType.OBSERVATION_SPAN.value,
            observation_span=root_conversation_span,
            label=thumbs_label,
            value={"value": "up"},
            notes="label export note",
            annotator=user,
            queue_item=item,
            organization=organization,
            workspace=workspace,
        )
        Score.objects.create(
            source_type=QueueItemSourceType.OBSERVATION_SPAN.value,
            observation_span=root_conversation_span,
            label=thumbs_label,
            value={"value": "down"},
            notes="second annotator note",
            annotator=second_annotator,
            queue_item=item,
            organization=organization,
            workspace=workspace,
        )
        eval_template = EvalTemplate.objects.create(
            name=f"export_eval_template_{uuid.uuid4().hex[:8]}",
            description="Export eval test",
            organization=organization,
            workspace=workspace,
            config={"type": "score"},
        )
        eval_config = CustomEvalConfig.objects.create(
            name="Export Quality",
            project=observe_project,
            eval_template=eval_template,
            config={"threshold": 0.8},
            mapping={"input": "input", "output": "output"},
            filters={},
        )
        EvalLogger.objects.create(
            trace=root_conversation_span.trace,
            observation_span=root_conversation_span,
            custom_eval_config=eval_config,
            eval_type_id="export_quality",
            output_float=0.82,
            results_explanation={"reason": "clear answer"},
        )
        SpanNotes.objects.create(
            span=root_conversation_span,
            notes="whole item export note",
            created_by_user=user,
            created_by_annotator=user.email,
        )

        fields_resp = auth_client.get(
            f"/model-hub/annotation-queues/{queue.id}/export-fields/"
        )
        assert fields_resp.status_code == status.HTTP_200_OK, fields_resp.data
        fields = fields_resp.data["result"]["fields"]
        default_fields = {
            item["field"] for item in fields_resp.data["result"]["default_mapping"]
        }
        assert any(
            field["id"] == "attr:span_attributes.customer.tier" for field in fields
        )
        assert "eval_metrics" in default_fields
        assert "annotation_metrics" in default_fields

        label_slot_1_value_field = f"label:{thumbs_label.id}:slot:1:value"
        label_slot_1_notes_field = f"label:{thumbs_label.id}:slot:1:notes"
        label_slot_1_annotator_field = f"label:{thumbs_label.id}:slot:1:annotator_email"
        label_slot_1_record_field = f"label:{thumbs_label.id}:slot:1:annotation"
        label_slot_2_value_field = f"label:{thumbs_label.id}:slot:2:value"
        label_slot_2_notes_field = f"label:{thumbs_label.id}:slot:2:notes"
        label_bundle_field = f"label:{thumbs_label.id}:annotation_columns"
        eval_score_field = "eval:Export Quality:score"
        assert label_slot_1_annotator_field in default_fields
        assert label_slot_2_notes_field in default_fields
        assert "review_status" in default_fields
        bundle = next(field for field in fields if field["id"] == label_bundle_field)
        assert label_slot_1_value_field in bundle["expand_fields"]
        assert label_slot_2_value_field in bundle["expand_fields"]
        assert any(field["id"] == eval_score_field for field in fields)
        export_resp = auth_client.post(
            f"/model-hub/annotation-queues/{queue.id}/export-to-dataset/",
            {
                "dataset_name": f"Export dataset {uuid.uuid4().hex[:8]}",
                "status_filter": "completed",
                "column_mapping": [
                    {
                        "field": "source_id",
                        "column": "source_identifier",
                        "enabled": True,
                    },
                    {
                        "field": "latency_ms",
                        "column": "latency_ms",
                        "enabled": True,
                    },
                    {
                        "field": "response_time_ms",
                        "column": "response_time_ms",
                        "enabled": True,
                    },
                    {
                        "field": "item_notes",
                        "column": "item_notes",
                        "enabled": True,
                    },
                    {
                        "field": "review_status",
                        "column": "review_status",
                        "enabled": True,
                    },
                    {
                        "field": "reviewed_by_email",
                        "column": "reviewer_email",
                        "enabled": True,
                    },
                    {
                        "field": "review_notes",
                        "column": "review_notes",
                        "enabled": True,
                    },
                    {
                        "field": "annotation_metrics",
                        "column": "annotation_metrics",
                        "enabled": True,
                    },
                    {
                        "field": "eval_metrics",
                        "column": "eval_metrics",
                        "enabled": True,
                    },
                    {
                        "field": label_slot_1_value_field,
                        "column": "thumbs_annotation_1_score",
                        "enabled": True,
                    },
                    {
                        "field": label_slot_1_notes_field,
                        "column": "thumbs_annotation_1_notes",
                        "enabled": True,
                    },
                    {
                        "field": label_slot_1_annotator_field,
                        "column": "thumbs_annotation_1_annotator_email",
                        "enabled": True,
                    },
                    {
                        "field": label_slot_1_record_field,
                        "column": "thumbs_annotation_1_record",
                        "enabled": True,
                    },
                    {
                        "field": label_slot_2_value_field,
                        "column": "thumbs_annotation_2_score",
                        "enabled": True,
                    },
                    {
                        "field": label_slot_2_notes_field,
                        "column": "thumbs_annotation_2_notes",
                        "enabled": True,
                    },
                    {
                        "field": eval_score_field,
                        "column": "export_quality_score",
                        "enabled": True,
                    },
                    {
                        "field": "attr:span_attributes.customer.tier",
                        "column": "customer_tier",
                        "enabled": True,
                    },
                    {
                        "field": "attr:span_attributes.score",
                        "column": "customer_score",
                        "enabled": True,
                    },
                ],
            },
            format="json",
        )
        assert export_resp.status_code == status.HTTP_200_OK, export_resp.data
        dataset = Dataset.objects.get(id=export_resp.data["result"]["dataset_id"])
        row = Row.objects.get(dataset=dataset, deleted=False)
        cells = {
            cell.column.name: cell.value
            for cell in Cell.objects.filter(row=row).select_related("column")
        }
        assert cells["source_identifier"] == root_conversation_span.id
        assert cells["latency_ms"] == "1000"
        # CH has no response_time column — response_time_ms collapses to latency_ms.
        assert cells["response_time_ms"] == "1000"
        # Per-queue scoping: SpanNote ("whole item export note") was never
        # written through this queue's annotation flow, so item_notes is
        # empty for this queue's export. Pre-revamp the span-level note
        # leaked into every queue's export — that's the leak this work
        # removes. To carry whole-item notes into a queue's export, the
        # user must save them via the queue's own submit/bulk flow which
        # writes a ``QueueItemNote``.
        assert cells["item_notes"] == ""
        assert cells["review_status"] == "approved"
        assert cells["reviewer_email"] == reviewer.email
        assert cells["review_notes"] == "review export note"
        assert cells["thumbs_annotation_1_score"] == "up"
        assert cells["thumbs_annotation_1_notes"] == "label export note"
        assert cells["thumbs_annotation_1_annotator_email"] == user.email
        assert json.loads(cells["thumbs_annotation_1_record"])["notes"] == (
            "label export note"
        )
        assert cells["thumbs_annotation_2_score"] == "down"
        assert cells["thumbs_annotation_2_notes"] == "second annotator note"
        assert (
            json.loads(cells["annotation_metrics"])[thumbs_label.name][0]["notes"]
            == "label export note"
        )
        assert (
            json.loads(cells["annotation_metrics"])[thumbs_label.name][1][
                "annotator_email"
            ]
            == second_annotator.email
        )
        assert (
            Column.objects.get(dataset=dataset, name="customer_score").data_type
            == DataTypeChoices.FLOAT.value
        )
        assert json.loads(cells["eval_metrics"])["Export Quality"]["score"] == 0.82
        assert cells["export_quality_score"] == "0.82"
        assert cells["customer_tier"] == "gold"
        assert cells["customer_score"] == "7.0"  # CH attrs_number is Float64
        assert row.metadata["annotations"][str(thumbs_label.id)][0]["notes"] == (
            "label export note"
        )
        assert row.metadata["review"]["notes"] == "review export note"

        download_resp = auth_client.get(
            f"/model-hub/annotation-queues/{queue.id}/export/",
            {"export_format": "json"},
        )
        assert download_resp.status_code == status.HTTP_200_OK, download_resp.data
        exported_item = download_resp.data["result"][0]
        assert exported_item["source"]["span_attributes"]["customer"]["tier"] == "gold"
        assert exported_item["source"]["span_attributes"]["score"] == 7.0
        assert exported_item["annotations"][1]["annotator_email"] == (
            second_annotator.email
        )
        assert exported_item["evals"]["Export Quality"]["score"] == 0.82
        assert exported_item["review"]["notes"] == "review export note"
        # Same per-queue scoping for the JSON download — see cell assertion above.
        assert exported_item["item_notes"] == ""

        csv_resp = auth_client.get(
            f"/model-hub/annotation-queues/{queue.id}/export/",
            {"export_format": "csv"},
        )
        assert csv_resp.status_code == status.HTTP_200_OK
        csv_rows = list(csv.DictReader(io.StringIO(csv_resp.content.decode())))
        assert len(csv_rows) == 2
        assert csv_rows[0]["requires_review"] == "True"
        assert csv_rows[0]["review_status"] == "approved"
        assert csv_rows[0]["reviewer_email"] == reviewer.email
        assert csv_rows[0]["reviewer_name"] == reviewer.name
        assert csv_rows[0]["reviewer_id"] == str(reviewer.id)
        assert csv_rows[0]["reviewed_at"]
        assert csv_rows[0]["review_notes"] == "review export note"
        assert csv_rows[0]["value"] == "up"
        assert csv_rows[1]["review_status"] == "approved"
        assert csv_rows[1]["value"] == "down"

        duplicate_resp = auth_client.post(
            f"/model-hub/annotation-queues/{queue.id}/export-to-dataset/",
            {
                "dataset_name": f"Duplicate export {uuid.uuid4().hex[:8]}",
                "status_filter": "completed",
                "column_mapping": [
                    {"field": "source_id", "column": "duplicate", "enabled": True},
                    {"field": "input", "column": "Duplicate", "enabled": True},
                ],
            },
            format="json",
        )
        assert duplicate_resp.status_code == status.HTTP_400_BAD_REQUEST

        disabled_resp = auth_client.post(
            f"/model-hub/annotation-queues/{queue.id}/export-to-dataset/",
            {
                "dataset_name": f"Disabled export {uuid.uuid4().hex[:8]}",
                "status_filter": "completed",
                "column_mapping": [
                    {"field": "source_id", "column": "source_id", "enabled": False}
                ],
            },
            format="json",
        )
        assert disabled_resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_export_to_existing_dataset_reuses_columns_creates_missing_and_backfills(
        self,
        auth_client,
        organization,
        workspace,
        user,
        observe_project,
        root_conversation_span,
        thumbs_label,
    ):
        root_conversation_span.span_attributes = {"score": 7}
        root_conversation_span.save(update_fields=["span_attributes"])
        # Re-seed CH after mutating span_attributes: export reads them CH-native.
        seed_ch_span(root_conversation_span)
        queue = _queue(
            "Existing dataset export queue",
            organization,
            workspace,
            user,
            project=observe_project,
        )
        AnnotationQueueLabel.objects.create(queue=queue, label=thumbs_label)
        item = QueueItem.objects.create(
            queue=queue,
            source_type=QueueItemSourceType.OBSERVATION_SPAN.value,
            observation_span=root_conversation_span,
            organization=organization,
            workspace=workspace,
            status=QueueItemStatus.COMPLETED.value,
            order=1,
        )
        Score.objects.create(
            source_type=QueueItemSourceType.OBSERVATION_SPAN.value,
            observation_span=root_conversation_span,
            label=thumbs_label,
            value={"value": "up"},
            notes="existing dataset export note",
            annotator=user,
            queue_item=item,
            organization=organization,
            workspace=workspace,
        )

        dataset = Dataset.objects.create(
            name=f"Existing export target {uuid.uuid4().hex[:8]}",
            organization=organization,
            workspace=workspace,
            user=user,
        )
        source_column = Column.objects.create(
            dataset=dataset,
            name="source_identifier",
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.OTHERS.value,
            status=StatusType.COMPLETED.value,
        )
        existing_only_column = Column.objects.create(
            dataset=dataset,
            name="existing_only",
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.OTHERS.value,
            status=StatusType.COMPLETED.value,
        )
        existing_row = Row.objects.create(dataset=dataset, order=1)
        Cell.objects.create(
            dataset=dataset,
            row=existing_row,
            column=source_column,
            value="pre-existing-source",
        )
        Cell.objects.create(
            dataset=dataset,
            row=existing_row,
            column=existing_only_column,
            value="keep me",
        )
        dataset.column_order = [str(source_column.id), str(existing_only_column.id)]
        dataset.column_config = {
            str(source_column.id): {"is_frozen": False, "is_visible": True},
            str(existing_only_column.id): {"is_frozen": False, "is_visible": True},
        }
        dataset.save(update_fields=["column_order", "column_config"])

        label_slot_1_value_field = f"label:{thumbs_label.id}:slot:1:value"
        export_resp = auth_client.post(
            f"/model-hub/annotation-queues/{queue.id}/export-to-dataset/",
            {
                "dataset_id": str(dataset.id),
                "status_filter": "completed",
                "column_mapping": [
                    {
                        "field": "source_id",
                        "column": "source_identifier",
                        "enabled": True,
                    },
                    {
                        "field": label_slot_1_value_field,
                        "column": "thumbs_annotation_1_score",
                        "enabled": True,
                    },
                    {
                        "field": "attr:span_attributes.score",
                        "column": "customer_score",
                        "enabled": True,
                    },
                ],
            },
            format="json",
        )

        assert export_resp.status_code == status.HTTP_200_OK, export_resp.data
        assert export_resp.data["result"]["dataset_id"] == str(dataset.id)
        assert export_resp.data["result"]["rows_created"] == 1
        assert (
            Column.objects.filter(
                dataset=dataset, name="source_identifier", deleted=False
            ).count()
            == 1
        )
        assert (
            Column.objects.get(dataset=dataset, name="customer_score").data_type
            == DataTypeChoices.FLOAT.value
        )
        dataset.refresh_from_db()
        assert (
            str(
                Column.objects.get(dataset=dataset, name="thumbs_annotation_1_score").id
            )
            in dataset.column_order
        )
        assert (
            str(Column.objects.get(dataset=dataset, name="customer_score").id)
            in dataset.column_order
        )

        exported_row = Row.objects.get(dataset=dataset, order=2, deleted=False)
        exported_cells = {
            cell.column.name: cell.value
            for cell in Cell.objects.filter(row=exported_row).select_related("column")
        }
        assert exported_cells["source_identifier"] == root_conversation_span.id
        assert exported_cells["thumbs_annotation_1_score"] == "up"
        assert exported_cells["customer_score"] == "7.0"  # CH attrs_number is Float64
        assert exported_cells["existing_only"] == ""
        assert exported_row.metadata["queue_item_id"] == str(item.id)

        backfilled_existing_cells = {
            cell.column.name: cell.value
            for cell in Cell.objects.filter(row=existing_row).select_related("column")
        }
        assert backfilled_existing_cells["source_identifier"] == ("pre-existing-source")
        assert backfilled_existing_cells["existing_only"] == "keep me"
        assert backfilled_existing_cells["thumbs_annotation_1_score"] == ""
        assert backfilled_existing_cells["customer_score"] == ""

    def test_export_all_status_includes_all_queue_items(
        self,
        auth_client,
        organization,
        workspace,
        user,
        observe_project,
        observe_trace,
        root_conversation_span,
        thumbs_label,
    ):
        queue = _queue(
            "All status export queue",
            organization,
            workspace,
            user,
            project=observe_project,
        )
        AnnotationQueueLabel.objects.create(queue=queue, label=thumbs_label)
        QueueItem.objects.create(
            queue=queue,
            source_type=QueueItemSourceType.OBSERVATION_SPAN.value,
            observation_span=root_conversation_span,
            organization=organization,
            workspace=workspace,
            status=QueueItemStatus.COMPLETED.value,
            order=1,
        )
        QueueItem.objects.create(
            queue=queue,
            source_type=QueueItemSourceType.TRACE.value,
            trace=observe_trace,
            organization=organization,
            workspace=workspace,
            status=QueueItemStatus.PENDING.value,
            order=2,
        )

        download_resp = auth_client.get(
            f"/model-hub/annotation-queues/{queue.id}/export/",
            {"export_format": "json", "status": "all"},
        )
        assert download_resp.status_code == status.HTTP_200_OK, download_resp.data
        assert [item["status"] for item in download_resp.data["result"]] == [
            QueueItemStatus.COMPLETED.value,
            QueueItemStatus.PENDING.value,
        ]

        completed_resp = auth_client.get(
            f"/model-hub/annotation-queues/{queue.id}/export/",
            {"export_format": "json", "status": QueueItemStatus.COMPLETED.value},
        )
        assert completed_resp.status_code == status.HTTP_200_OK, completed_resp.data
        assert len(completed_resp.data["result"]) == 1

        dataset_resp = auth_client.post(
            f"/model-hub/annotation-queues/{queue.id}/export-to-dataset/",
            {
                "dataset_name": f"All status dataset {uuid.uuid4().hex[:8]}",
                "status_filter": "all",
                "column_mapping": [
                    {"field": "source_id", "column": "source_id", "enabled": True},
                    {"field": "status", "column": "status", "enabled": True},
                ],
            },
            format="json",
        )
        assert dataset_resp.status_code == status.HTTP_200_OK, dataset_resp.data
        dataset = Dataset.objects.get(id=dataset_resp.data["result"]["dataset_id"])
        assert Row.objects.filter(dataset=dataset, deleted=False).count() == 2

    def test_export_scores_do_not_leak_from_other_queues(
        self,
        auth_client,
        organization,
        workspace,
        user,
        observe_project,
        root_conversation_span,
        thumbs_label,
    ):
        queue = _queue(
            "Score scoped export queue",
            organization,
            workspace,
            user,
            project=observe_project,
            annotations_required=3,
        )
        other_queue = _queue(
            "Other score scoped export queue",
            organization,
            workspace,
            user,
            project=observe_project,
        )
        AnnotationQueueLabel.objects.create(queue=queue, label=thumbs_label)
        AnnotationQueueLabel.objects.create(queue=other_queue, label=thumbs_label)
        item = QueueItem.objects.create(
            queue=queue,
            source_type=QueueItemSourceType.OBSERVATION_SPAN.value,
            observation_span=root_conversation_span,
            organization=organization,
            workspace=workspace,
            status=QueueItemStatus.COMPLETED.value,
            order=1,
        )
        other_item = QueueItem.objects.create(
            queue=other_queue,
            source_type=QueueItemSourceType.OBSERVATION_SPAN.value,
            observation_span=root_conversation_span,
            organization=organization,
            workspace=workspace,
            status=QueueItemStatus.COMPLETED.value,
            order=1,
        )
        other_annotator = User.objects.create_user(
            email=f"other-queue-annotator-{uuid.uuid4().hex[:8]}@example.com",
            password="test",
            name="Other Queue Annotator",
            organization=organization,
        )
        inline_annotator = User.objects.create_user(
            email=f"inline-annotator-{uuid.uuid4().hex[:8]}@example.com",
            password="test",
            name="Inline Annotator",
            organization=organization,
        )
        Score.objects.create(
            source_type=QueueItemSourceType.OBSERVATION_SPAN.value,
            observation_span=root_conversation_span,
            label=thumbs_label,
            value={"value": "up"},
            notes="current queue score",
            annotator=user,
            queue_item=item,
            organization=organization,
            workspace=workspace,
        )
        Score.objects.create(
            source_type=QueueItemSourceType.OBSERVATION_SPAN.value,
            observation_span=root_conversation_span,
            label=thumbs_label,
            value={"value": "down"},
            notes="other queue score",
            annotator=other_annotator,
            queue_item=other_item,
            organization=organization,
            workspace=workspace,
        )
        Score.objects.create(
            source_type=QueueItemSourceType.OBSERVATION_SPAN.value,
            observation_span=root_conversation_span,
            label=thumbs_label,
            value={"value": "inline"},
            notes="inline source score",
            annotator=inline_annotator,
            organization=organization,
            workspace=workspace,
        )

        download_resp = auth_client.get(
            f"/model-hub/annotation-queues/{queue.id}/export/",
            {"export_format": "json"},
        )
        assert download_resp.status_code == status.HTTP_200_OK, download_resp.data
        notes = {
            annotation["notes"]
            for annotation in download_resp.data["result"][0]["annotations"]
        }
        # Strict per-queue scoping: this queue's export shows only the
        # score attributed to this queue's item. The orphan ("inline"
        # source-level score) and the other queue's score must both be
        # excluded. Pre-revamp the orphan was leaked in, which is the
        # behavior the queue-scoped uniqueness explicitly removes.
        assert "current queue score" in notes
        assert "inline source score" not in notes
        assert "other queue score" not in notes

        dataset_resp = auth_client.post(
            f"/model-hub/annotation-queues/{queue.id}/export-to-dataset/",
            {
                "dataset_name": f"Scoped score dataset {uuid.uuid4().hex[:8]}",
                "status_filter": "completed",
                "column_mapping": [
                    {
                        "field": "annotation_metrics",
                        "column": "annotation_metrics",
                        "enabled": True,
                    }
                ],
            },
            format="json",
        )
        assert dataset_resp.status_code == status.HTTP_200_OK, dataset_resp.data
        dataset = Dataset.objects.get(id=dataset_resp.data["result"]["dataset_id"])
        row = Row.objects.get(dataset=dataset, deleted=False)
        exported_notes = {
            entry["notes"]
            for entries in row.metadata["annotations"].values()
            for entry in entries
        }
        # Same scoping rule applies to dataset export — only this queue's
        # own scores. Orphans surface separately once the on_commit hook
        # attaches them to a default queue's item.
        assert "current queue score" in exported_notes
        assert "inline source score" not in exported_notes
        assert "other queue score" not in exported_notes

        annotations_resp = auth_client.get(
            f"/model-hub/annotation-queues/{queue.id}/items/{item.id}/annotations/"
        )
        assert annotations_resp.status_code == status.HTTP_200_OK, annotations_resp.data
        annotation_notes = {
            annotation["notes"] for annotation in annotations_resp.data["result"]
        }
        assert "current queue score" in annotation_notes
        assert "inline source score" not in annotation_notes
        assert "other queue score" not in annotation_notes

        complete_resp = auth_client.post(_complete_url(queue, item), {}, format="json")
        assert complete_resp.status_code == status.HTTP_200_OK, complete_resp.data
        item.refresh_from_db()
        assert item.status == QueueItemStatus.IN_PROGRESS.value

    def test_export_slots_prioritize_queue_scores_over_older_inline_scores(
        self,
        auth_client,
        organization,
        workspace,
        user,
        observe_project,
        root_conversation_span,
        thumbs_label,
    ):
        queue = _queue(
            "Queue score slot export order",
            organization,
            workspace,
            user,
            project=observe_project,
            annotations_required=2,
        )
        AnnotationQueueLabel.objects.create(queue=queue, label=thumbs_label)
        item = QueueItem.objects.create(
            queue=queue,
            source_type=QueueItemSourceType.OBSERVATION_SPAN.value,
            observation_span=root_conversation_span,
            organization=organization,
            workspace=workspace,
            status=QueueItemStatus.COMPLETED.value,
            order=1,
        )
        inline_annotator = User.objects.create_user(
            email=f"older-inline-annotator-{uuid.uuid4().hex[:8]}@example.com",
            password="test",
            name="Older Inline Annotator",
            organization=organization,
        )

        older_inline_score = Score.objects.create(
            source_type=QueueItemSourceType.OBSERVATION_SPAN.value,
            observation_span=root_conversation_span,
            label=thumbs_label,
            value={"value": "down"},
            notes="older inline source note",
            annotator=inline_annotator,
            organization=organization,
            workspace=workspace,
        )
        older_inline_score.created_at = timezone.now() - timedelta(days=1)
        older_inline_score.save(update_fields=["created_at"])

        queue_score = Score.objects.create(
            source_type=QueueItemSourceType.OBSERVATION_SPAN.value,
            observation_span=root_conversation_span,
            label=thumbs_label,
            value={"value": "up"},
            notes="queue label note",
            annotator=user,
            queue_item=item,
            organization=organization,
            workspace=workspace,
        )
        queue_score.created_at = timezone.now()
        queue_score.save(update_fields=["created_at"])

        label_slot_1_value_field = f"label:{thumbs_label.id}:slot:1:value"
        label_slot_1_notes_field = f"label:{thumbs_label.id}:slot:1:notes"
        label_slot_1_annotator_field = f"label:{thumbs_label.id}:slot:1:annotator_email"
        label_slot_2_value_field = f"label:{thumbs_label.id}:slot:2:value"
        label_slot_2_notes_field = f"label:{thumbs_label.id}:slot:2:notes"

        dataset_resp = auth_client.post(
            f"/model-hub/annotation-queues/{queue.id}/export-to-dataset/",
            {
                "dataset_name": f"Queue-first export {uuid.uuid4().hex[:8]}",
                "status_filter": "completed",
                "column_mapping": [
                    {
                        "field": "annotation_metrics",
                        "column": "annotation_metrics",
                        "enabled": True,
                    },
                    {
                        "field": label_slot_1_value_field,
                        "column": "slot_1_value",
                        "enabled": True,
                    },
                    {
                        "field": label_slot_1_notes_field,
                        "column": "slot_1_notes",
                        "enabled": True,
                    },
                    {
                        "field": label_slot_1_annotator_field,
                        "column": "slot_1_annotator",
                        "enabled": True,
                    },
                    {
                        "field": label_slot_2_value_field,
                        "column": "slot_2_value",
                        "enabled": True,
                    },
                    {
                        "field": label_slot_2_notes_field,
                        "column": "slot_2_notes",
                        "enabled": True,
                    },
                ],
            },
            format="json",
        )
        assert dataset_resp.status_code == status.HTTP_200_OK, dataset_resp.data
        dataset = Dataset.objects.get(id=dataset_resp.data["result"]["dataset_id"])
        row = Row.objects.get(dataset=dataset, deleted=False)
        cells = {
            cell.column.name: cell.value
            for cell in Cell.objects.filter(row=row).select_related("column")
        }
        # Per-queue strict scoping: only the queue-attributed score lands
        # in the export. The older inline (orphan) score belongs to no
        # queue and is excluded — pre-revamp it would have filled slot 2,
        # which is what this regression test originally guarded against.
        assert cells["slot_1_value"] == "up"
        assert cells["slot_1_notes"] == "queue label note"
        assert cells["slot_1_annotator"] == user.email
        # Slot 2 is empty because nothing else was scored *in this queue*.
        assert cells.get("slot_2_value") in (None, "")
        assert cells.get("slot_2_notes") in (None, "")
        metrics = json.loads(cells["annotation_metrics"])[thumbs_label.name]
        # ``metrics`` may serialize as either a list of entries or a single
        # entry depending on count. Either way, the queue label note must
        # appear and the inline note must not.
        if isinstance(metrics, list):
            notes = [entry.get("notes") for entry in metrics]
        else:
            notes = [metrics.get("notes")]
        assert "queue label note" in notes
        assert "older inline source note" not in notes
        assert row.metadata["annotations"][str(thumbs_label.id)][0]["notes"] == (
            "queue label note"
        )


# Unit tests for call_execution-side annotation queue export helpers

from types import SimpleNamespace

from model_hub.utils.annotation_queue_helpers import (
    canonical_score_value,
    eval_metrics_from_call_execution,
    eval_output_value,
)


def _label(type_value):
    return SimpleNamespace(type=type_value, id="lbl-1")


class TestCanonicalScoreValue:
    def test_text_label_extracts_text_key(self):
        assert canonical_score_value(
            _label(AnnotationTypeChoices.TEXT.value), {"text": "hello"}
        ) == "hello"

    def test_numeric_label_extracts_value_key(self):
        assert canonical_score_value(
            _label(AnnotationTypeChoices.NUMERIC.value), {"value": 7}
        ) == 7

    def test_star_label_extracts_rating_key(self):
        assert canonical_score_value(
            _label(AnnotationTypeChoices.STAR.value), {"rating": 4}
        ) == 4

    def test_thumbs_label_extracts_value_key(self):
        assert canonical_score_value(
            _label(AnnotationTypeChoices.THUMBS_UP_DOWN.value), {"value": "up"}
        ) == "up"

    def test_categorical_label_extracts_selected_key(self):
        assert canonical_score_value(
            _label(AnnotationTypeChoices.CATEGORICAL.value), {"selected": ["a", "b"]}
        ) == ["a", "b"]

    def test_none_raw_returns_none(self):
        assert canonical_score_value(_label(AnnotationTypeChoices.STAR.value), None) is None

    def test_scalar_raw_passes_through(self):
        assert canonical_score_value(_label(AnnotationTypeChoices.NUMERIC.value), 5) == 5

    def test_missing_label_returns_raw_dict(self):
        assert canonical_score_value(None, {"value": 1}) == {"value": 1}

    def test_unknown_label_type_falls_back_to_raw(self):
        unknown = SimpleNamespace(type="future_type", id="lbl-fut")
        assert canonical_score_value(unknown, {"value": 1}) == {"value": 1}

    def test_dict_missing_expected_key_returns_raw(self):
        assert canonical_score_value(
            _label(AnnotationTypeChoices.STAR.value), {"value": 1}
        ) == {"value": 1}


class TestEvalOutputValue:
    def test_typed_dict_output_float(self):
        assert eval_output_value({"output_float": 0.75}) == 0.75

    def test_typed_dict_output_bool(self):
        assert eval_output_value({"output_bool": True}) is True

    def test_typed_dict_output_str(self):
        assert eval_output_value({"output_str": "good"}) == "good"

    def test_typed_dict_output_str_list(self):
        assert eval_output_value({"output_str_list": ["a", "b"]}) == ["a", "b"]

    def test_legacy_output_score_dict(self):
        assert eval_output_value({"output": {"score": 0.5}}) == 0.5

    def test_legacy_output_choice_dict(self):
        assert eval_output_value({"output": {"choice": "pass"}}) == "pass"

    def test_legacy_scalar_output(self):
        assert eval_output_value({"output": 1.0}) == 1.0

    def test_none_source(self):
        assert eval_output_value(None) is None

    def test_eval_logger_row_prefers_typed_columns(self):
        row = SimpleNamespace(
            output_float=0.9,
            output_bool=None,
            output_str=None,
            output_str_list=[],
        )
        assert eval_output_value(row) == 0.9


class TestEvalMetricsFromCallExecution:
    def test_returns_empty_when_call_missing(self):
        assert eval_metrics_from_call_execution(None) == {}

    def test_returns_empty_when_eval_outputs_empty(self):
        call = SimpleNamespace(eval_outputs={})
        assert eval_metrics_from_call_execution(call) == {}

    def test_legacy_output_dict_with_score(self):
        call = SimpleNamespace(
            eval_outputs={
                "evt-1": {
                    "name": "Helpfulness",
                    "output": {"score": 0.8},
                    "reason": "looked helpful",
                }
            }
        )
        result = eval_metrics_from_call_execution(call)
        assert result == {
            "Helpfulness": [
                {
                    "score": 0.8,
                    "explanation": "looked helpful",
                    "tags": None,
                    "error": None,
                    "error_message": None,
                    "created_at": None,
                }
            ]
        }

    def test_typed_axis_sibling_keys_preferred_over_legacy_output(self):
        call = SimpleNamespace(
            eval_outputs={
                "evt-1": {
                    "name": "Score",
                    "output_float": 0.42,
                    "output": {"score": 0.99},
                }
            }
        )
        assert eval_metrics_from_call_execution(call)["Score"][0]["score"] == 0.42

    def test_error_field_preserved_raw_not_coerced(self):
        call = SimpleNamespace(
            eval_outputs={
                "evt-1": {
                    "name": "Failing",
                    "output": None,
                    "error": "error",
                    "error_message": "boom",
                }
            }
        )
        entry = eval_metrics_from_call_execution(call)["Failing"][0]
        assert entry["error"] == "error"
        assert entry["error_message"] == "boom"

    def test_error_message_none_when_no_error(self):
        call = SimpleNamespace(
            eval_outputs={
                "evt-1": {
                    "name": "OK",
                    "output": {"score": 1.0},
                    "error_message": "stale",
                }
            }
        )
        entry = eval_metrics_from_call_execution(call)["OK"][0]
        assert entry["error"] is None
        assert entry["error_message"] is None

    def test_non_dict_entry_skipped(self):
        call = SimpleNamespace(
            eval_outputs={"evt-1": "not-a-dict", "evt-2": {"name": "Real", "output": 1}}
        )
        result = eval_metrics_from_call_execution(call)
        assert "Real" in result and len(result) == 1

    def test_falls_back_to_eval_id_when_name_missing(self):
        call = SimpleNamespace(eval_outputs={"evt-id": {"output": {"score": 0.1}}})
        assert "evt-id" in eval_metrics_from_call_execution(call)

    def test_shape_pin_matches_typed_dict(self):
        from model_hub.utils.annotation_queue_helpers import EvalMetricEntry

        call = SimpleNamespace(
            eval_outputs={
                "evt-1": {
                    "name": "Pinned",
                    "output": {"score": 0.5},
                    "reason": "explain",
                    "tags": ["x"],
                    "created_at": "2026-01-01T00:00:00Z",
                }
            }
        )
        entries = eval_metrics_from_call_execution(call)["Pinned"]
        assert len(entries) == 1
        assert set(entries[0].keys()) == set(EvalMetricEntry.__annotations__.keys())


@pytest.mark.django_db
class TestQueueExportQueryCount:
    def _seed_call(self, organization, workspace, base_call, turns=2):
        from simulate.models.test_execution import CallExecution, CallTranscript

        call = CallExecution.objects.create(
            test_execution=base_call.test_execution,
            scenario=base_call.scenario,
            status=CallExecution.CallStatus.COMPLETED,
            duration_seconds=10,
        )
        for i in range(turns):
            role = (
                CallTranscript.SpeakerRole.USER
                if i % 2 == 0
                else CallTranscript.SpeakerRole.ASSISTANT
            )
            CallTranscript.objects.create(
                call_execution=call,
                speaker_role=role,
                content=f"turn {i}",
                start_time_ms=i * 500,
            )
        return call

    def _seed_queue_with_calls(
        self, organization, workspace, user, calls, queue_name="prefetch guard"
    ):
        queue = _queue(queue_name, organization, workspace, user)
        for call in calls:
            QueueItem.objects.create(
                queue=queue,
                source_type=QueueItemSourceType.CALL_EXECUTION.value,
                call_execution=call,
                organization=organization,
                workspace=workspace,
                status=QueueItemStatus.COMPLETED.value,
            )
        return queue

    def _run_export(self, queue):
        from model_hub.utils.annotation_queue_helpers import _call_transcript_turns
        from model_hub.views.annotation_queues import _queue_item_export_prefetches

        items_qs = (
            QueueItem.objects.filter(queue=queue, deleted=False)
            .select_related("call_execution")
            .prefetch_related(*_queue_item_export_prefetches())
        )
        items = list(items_qs)
        for item in items:
            _ = _call_transcript_turns(item.call_execution)
        return items

    def test_export_queryset_does_not_scale_with_call_items(
        self,
        organization,
        workspace,
        user,
        simulation_call_execution,
    ):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        from simulate.models.test_execution import CallTranscript

        for i in range(2):
            CallTranscript.objects.create(
                call_execution=simulation_call_execution,
                speaker_role=(
                    CallTranscript.SpeakerRole.USER
                    if i % 2 == 0
                    else CallTranscript.SpeakerRole.ASSISTANT
                ),
                content=f"fixture turn {i}",
                start_time_ms=i * 500,
            )

        call_b = self._seed_call(organization, workspace, simulation_call_execution)
        queue_2 = self._seed_queue_with_calls(
            organization,
            workspace,
            user,
            [simulation_call_execution, call_b],
            queue_name="prefetch guard 2-item",
        )
        with CaptureQueriesContext(connection) as ctx_2:
            self._run_export(queue_2)
        baseline = len(ctx_2.captured_queries)

        call_c = self._seed_call(organization, workspace, simulation_call_execution)
        call_d = self._seed_call(organization, workspace, simulation_call_execution)
        queue_3 = self._seed_queue_with_calls(
            organization,
            workspace,
            user,
            [simulation_call_execution, call_c, call_d],
            queue_name="prefetch guard 3-item",
        )
        with CaptureQueriesContext(connection) as ctx_3:
            self._run_export(queue_3)

        assert len(ctx_3.captured_queries) == baseline, (
            f"Query count grew with item count: {baseline} -> "
            f"{len(ctx_3.captured_queries)}. The transcripts fetch is "
            f"running per-item; the Prefetch is missing or _call_transcript_turns "
            f"is bypassing the prefetched attribute.\n"
            + "\n".join(q["sql"][:200] for q in ctx_3.captured_queries)
        )
