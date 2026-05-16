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
    return ObservationSpan.objects.create(
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


@pytest.mark.django_db
class TestVoiceAnnotationRegressionE2E:
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
        assert SpanNotes.objects.get(
            span=root_conversation_span,
            created_by_user=user,
        ).notes == "whole call note"

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
        assert SpanNotes.objects.get(
            span=root_conversation_span,
            created_by_user=user,
        ).notes == "trace-level-only note"

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
        assert SpanNotes.objects.get(
            span=root_conversation_span,
            created_by_user=user,
        ).notes == "old observe toolbar note"

        score_resp = auth_client.get(
            "/model-hub/scores/for-source/",
            {
                "source_type": QueueItemSourceType.OBSERVATION_SPAN.value,
                "source_id": root_conversation_span.id,
            },
        )
        assert score_resp.status_code == status.HTTP_200_OK, score_resp.data
        assert score_resp.data["span_notes"][0]["notes"] == "old observe toolbar note"

    def test_th4759_simulation_call_detail_returns_scenario_columns(
        self,
        auth_client,
        simulation_call_execution,
        simulation_dataset_row,
    ):
        resp = auth_client.get(
            f"/simulate/call-executions/{simulation_call_execution.id}/"
        )

        assert resp.status_code == status.HTTP_200_OK, resp.data
        scenario_columns = resp.data["scenario_columns"]
        assert scenario_columns
        column_payload = next(iter(scenario_columns.values()))
        assert column_payload["column_name"] == "customer_goal"
        assert column_payload["value"] == "Order one cheeseburger"
        assert column_payload["dataset_id"] == str(simulation_dataset_row.dataset_id)

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
        AnnotationQueueAnnotator.objects.create(
            queue=queue,
            user=user,
            role="annotator",
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
        assert complete_first.data["result"]["next_item"]["id"] == str(
            skipped_item.id
        )
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
        AnnotationQueueAnnotator.objects.create(
            queue=queue,
            user=user,
            role="annotator",
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
        root_conversation_span.response_time = 123.5
        root_conversation_span.save(update_fields=["response_time"])
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
        assert span_preview["response_time_ms"] == 123.5
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
        root_conversation_span.save(
            update_fields=["span_attributes", "response_time"]
        )
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
        label_slot_1_annotator_field = (
            f"label:{thumbs_label.id}:slot:1:annotator_email"
        )
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
        assert cells["response_time_ms"] == "321.0"
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
        assert cells["thumbs_annotation_1_score"] == json.dumps({"value": "up"})
        assert cells["thumbs_annotation_1_notes"] == "label export note"
        assert cells["thumbs_annotation_1_annotator_email"] == user.email
        assert json.loads(cells["thumbs_annotation_1_record"])["notes"] == (
            "label export note"
        )
        assert cells["thumbs_annotation_2_score"] == json.dumps({"value": "down"})
        assert cells["thumbs_annotation_2_notes"] == "second annotator note"
        assert json.loads(cells["annotation_metrics"])[thumbs_label.name][0][
            "notes"
        ] == "label export note"
        assert json.loads(cells["annotation_metrics"])[thumbs_label.name][1][
            "annotator_email"
        ] == second_annotator.email
        assert (
            Column.objects.get(dataset=dataset, name="customer_score").data_type
            == DataTypeChoices.INTEGER.value
        )
        assert json.loads(cells["eval_metrics"])["Export Quality"]["score"] == 0.82
        assert cells["export_quality_score"] == "0.82"
        assert cells["customer_tier"] == "gold"
        assert cells["customer_score"] == "7"
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
        assert exported_item["source"]["span_attributes"]["score"] == 7
        assert exported_item["annotations"][1]["annotator_email"] == (
            second_annotator.email
        )
        assert exported_item["evals"]["Export Quality"]["score"] == 0.82
        assert exported_item["review"]["notes"] == "review export note"
        # Same per-queue scoping for the JSON download — see cell assertion above.
        assert exported_item["item_notes"] == ""

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
        assert Column.objects.filter(
            dataset=dataset, name="source_identifier", deleted=False
        ).count() == 1
        assert (
            Column.objects.get(dataset=dataset, name="customer_score").data_type
            == DataTypeChoices.INTEGER.value
        )
        dataset.refresh_from_db()
        assert str(
            Column.objects.get(dataset=dataset, name="thumbs_annotation_1_score").id
        ) in dataset.column_order
        assert str(
            Column.objects.get(dataset=dataset, name="customer_score").id
        ) in dataset.column_order

        exported_row = Row.objects.get(dataset=dataset, order=2, deleted=False)
        exported_cells = {
            cell.column.name: cell.value
            for cell in Cell.objects.filter(row=exported_row).select_related("column")
        }
        assert exported_cells["source_identifier"] == root_conversation_span.id
        assert exported_cells["thumbs_annotation_1_score"] == json.dumps(
            {"value": "up"}
        )
        assert exported_cells["customer_score"] == "7"
        assert exported_cells["existing_only"] == ""
        assert exported_row.metadata["queue_item_id"] == str(item.id)

        backfilled_existing_cells = {
            cell.column.name: cell.value
            for cell in Cell.objects.filter(row=existing_row).select_related("column")
        }
        assert backfilled_existing_cells["source_identifier"] == (
            "pre-existing-source"
        )
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
        label_slot_1_annotator_field = (
            f"label:{thumbs_label.id}:slot:1:annotator_email"
        )
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
        assert cells["slot_1_value"] == json.dumps({"value": "up"})
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
