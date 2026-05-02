"""Annotation queue render lifecycle coverage.

These tests seed one queue with known-good sources, run automation-rule
evaluation, then verify the queue/list/detail payloads the annotator UI uses.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework import status

from model_hub.models.annotation_queues import (
    AnnotationQueue,
    AnnotationQueueAnnotator,
    AnnotationQueueLabel,
    AutomationRule,
    QueueItem,
)
from model_hub.models.choices import (
    AnnotationQueueStatusChoices,
    AnnotatorRole,
    DataTypeChoices,
    DatasetSourceChoices,
    QueueItemStatus,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.utils.annotation_queue_helpers import (
    evaluate_rule,
    resolve_source_preview,
)
from simulate.models.agent_definition import AgentDefinition, AgentTypeChoices
from simulate.models.chat_message import ChatMessageModel
from simulate.models.run_test import RunTest
from simulate.models.scenarios import Scenarios
from simulate.models.test_execution import (
    CallExecution,
    CallTranscript,
    TestExecution as SimulateTestExecution,
)
from tracer.models.observability_provider import ProviderChoices
from tracer.models.observation_span import ObservationSpan
from tracer.models.project import Project
from tracer.models.trace import Trace
from tracer.models.trace_session import TraceSession


QUEUE_ITEMS_URL = "/model-hub/annotation-queues/{queue_id}/items/"


def _unwrap(data):
    return data.get("result", data) if isinstance(data, dict) else data


def _list_results(data):
    data = _unwrap(data)
    if isinstance(data, dict) and "results" in data:
        return data["results"]
    return data


def _create_label(*, organization, workspace):
    return AnnotationsLabels.objects.create(
        name=f"render quality {uuid.uuid4().hex[:8]}",
        type="star",
        settings={"no_of_stars": 5},
        organization=organization,
        workspace=workspace,
    )


def _create_project(*, organization, workspace, name="annotation render project"):
    return Project.objects.create(
        name=f"{name} {uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
        model_type="GenerativeLLM",
        trace_type="observe",
    )


def _create_dataset(*, organization, workspace, user):
    dataset = Dataset.objects.create(
        name=f"annotation render dataset {uuid.uuid4().hex[:8]}",
        source=DatasetSourceChoices.BUILD.value,
        organization=organization,
        workspace=workspace,
        user=user,
    )
    columns = {
        "user_message": Column.objects.create(
            name="user_message",
            data_type=DataTypeChoices.TEXT.value,
            dataset=dataset,
            source=SourceChoices.OTHERS.value,
            status=StatusType.COMPLETED.value,
        ),
        "needs_review": Column.objects.create(
            name="needs_review",
            data_type=DataTypeChoices.BOOLEAN.value,
            dataset=dataset,
            source=SourceChoices.OTHERS.value,
            status=StatusType.COMPLETED.value,
        ),
        "expected_answer": Column.objects.create(
            name="expected_answer",
            data_type=DataTypeChoices.TEXT.value,
            dataset=dataset,
            source=SourceChoices.OTHERS.value,
            status=StatusType.COMPLETED.value,
        ),
    }
    row = Row.objects.create(dataset=dataset, order=7)
    Cell.objects.create(
        dataset=dataset,
        row=row,
        column=columns["user_message"],
        value="please help me order a pizza",
    )
    Cell.objects.create(
        dataset=dataset,
        row=row,
        column=columns["needs_review"],
        value="true",
    )
    Cell.objects.create(
        dataset=dataset,
        row=row,
        column=columns["expected_answer"],
        value="I can help with that order.",
    )
    return dataset, columns, row


def _create_trace_graph(*, project):
    session = TraceSession.objects.create(
        project=project,
        name=f"annotation render session {uuid.uuid4().hex[:8]}",
        bookmarked=True,
    )
    trace = Trace.objects.create(
        project=project,
        session=session,
        name=f"annotation render trace {uuid.uuid4().hex[:8]}",
        input={"user": "hello"},
        output={"assistant": "hi"},
        metadata={"test": "annotation-render"},
    )
    root = ObservationSpan.objects.create(
        id=f"span-root-{uuid.uuid4().hex[:12]}",
        project=project,
        trace=trace,
        parent_span_id=None,
        name=f"annotation render root span {uuid.uuid4().hex[:8]}",
        observation_type="agent",
        start_time=timezone.now(),
        end_time=timezone.now(),
        input={"message": "hello"},
        output={"message": "hi"},
        model="gpt-4",
        provider="openai",
        status="OK",
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        cost=0.001,
        span_attributes={"gen_ai.request.model": "gpt-4"},
        resource_attributes={"service.name": "annotation-render-test"},
        metadata={"span": "root"},
    )
    child = ObservationSpan.objects.create(
        id=f"span-child-{uuid.uuid4().hex[:12]}",
        project=project,
        trace=trace,
        parent_span_id=root.id,
        name=f"annotation render llm span {uuid.uuid4().hex[:8]}",
        observation_type="llm",
        start_time=timezone.now(),
        end_time=timezone.now(),
        input={"prompt": "hello"},
        output={"completion": "hi"},
        model="gpt-4",
        provider="openai",
        status="OK",
        prompt_tokens=8,
        completion_tokens=4,
        total_tokens=12,
        cost=0.0008,
        span_attributes={"gen_ai.system": "openai"},
        resource_attributes={"service.name": "annotation-render-test"},
        metadata={"span": "child"},
    )
    return session, trace, root, child


def _create_agent_definition(*, organization, workspace):
    return AgentDefinition.objects.create(
        agent_name=f"annotation render agent {uuid.uuid4().hex[:8]}",
        agent_type=AgentTypeChoices.VOICE,
        inbound=True,
        description="Fixture agent for annotation render tests",
        provider=ProviderChoices.VAPI.value,
        organization=organization,
        workspace=workspace,
    )


def _create_call_executions(*, organization, workspace, agent_definition):
    run_test = RunTest.objects.create(
        name=f"annotation render run {uuid.uuid4().hex[:8]}",
        agent_definition=agent_definition,
        organization=organization,
        workspace=workspace,
    )
    test_execution = SimulateTestExecution.objects.create(
        run_test=run_test,
        agent_definition=agent_definition,
        status=SimulateTestExecution.ExecutionStatus.COMPLETED,
        total_calls=2,
        completed_calls=2,
    )
    scenario = Scenarios.objects.create(
        name="completed voice checkout scenario",
        source="Customer asks for fast-food order status.",
        source_type=Scenarios.SourceTypes.AGENT_DEFINITION,
        agent_definition=agent_definition,
        organization=organization,
        workspace=workspace,
    )

    started_at = timezone.now()
    voice_call = CallExecution.objects.create(
        test_execution=test_execution,
        scenario=scenario,
        simulation_call_type=CallExecution.SimulationCallType.VOICE,
        status=CallExecution.CallStatus.COMPLETED,
        started_at=started_at,
        completed_at=started_at + timedelta(seconds=42),
        ended_at=started_at + timedelta(seconds=42),
        duration_seconds=42,
        recording_url="https://example.com/annotation-render-call.mp3",
        stereo_recording_url="https://example.com/annotation-render-call-stereo.mp3",
        call_summary="Customer completed a short order-status voice call.",
        ended_reason="customer-ended-call",
        transcript_available=True,
        recording_available=True,
        message_count=2,
        overall_score=9.0,
        call_metadata={
            "call_direction": "inbound",
            "rowData": {"persona": "hungry customer"},
        },
        provider_call_data={
            ProviderChoices.VAPI.value: {
                "id": "vapi-annotation-render-call",
                "status": "ended",
                "endedReason": "customer-ended-call",
                "messages": [
                    {
                        "role": "user",
                        "message": "Can you check my burger order?",
                        "secondsFromStart": 1.2,
                        "duration": 1800,
                    },
                    {
                        "role": "assistant",
                        "message": "Your order is being prepared now.",
                        "secondsFromStart": 3.1,
                        "duration": 2100,
                    },
                ],
                "artifact": {
                    "recording": {
                        "mono": {
                            "combinedUrl": (
                                "https://example.com/annotation-render-call.mp3"
                            )
                        },
                        "stereoUrl": (
                            "https://example.com/annotation-render-call-stereo.mp3"
                        ),
                    }
                },
            }
        },
    )
    CallTranscript.objects.create(
        call_execution=voice_call,
        speaker_role=CallTranscript.SpeakerRole.USER,
        content="Can you check my burger order?",
        start_time_ms=1200,
        end_time_ms=3000,
    )
    CallTranscript.objects.create(
        call_execution=voice_call,
        speaker_role=CallTranscript.SpeakerRole.ASSISTANT,
        content="Your order is being prepared now.",
        start_time_ms=3100,
        end_time_ms=5200,
    )

    chat_call = CallExecution.objects.create(
        test_execution=test_execution,
        scenario=scenario,
        simulation_call_type=CallExecution.SimulationCallType.TEXT,
        status=CallExecution.CallStatus.COMPLETED,
        started_at=started_at,
        completed_at=started_at + timedelta(seconds=12),
        ended_at=started_at + timedelta(seconds=12),
        duration_seconds=12,
        call_summary="Customer completed a short chat interaction.",
        transcript_available=True,
        message_count=2,
        overall_score=8.5,
        call_metadata={"rowData": {"persona": "chat customer"}},
        conversation_metrics_data={"turn_count": 2, "total_tokens": 18},
    )
    ChatMessageModel.objects.create(
        call_execution=chat_call,
        role=ChatMessageModel.RoleChoices.USER,
        messages=["Do you have fries?"],
        content=[{"type": "text", "text": "Do you have fries?"}],
        session_id="annotation-render-chat-session",
        organization=organization,
        workspace=workspace,
        tokens=6,
        latency_ms=100,
    )
    ChatMessageModel.objects.create(
        call_execution=chat_call,
        role=ChatMessageModel.RoleChoices.ASSISTANT,
        messages=["Yes, fries are available."],
        content=[{"type": "text", "text": "Yes, fries are available."}],
        session_id="annotation-render-chat-session",
        organization=organization,
        workspace=workspace,
        tokens=12,
        latency_ms=250,
    )
    return voice_call, chat_call


@pytest.fixture
def annotation_render_seed(db, organization, workspace, user):
    project = _create_project(organization=organization, workspace=workspace)
    dataset, columns, row = _create_dataset(
        organization=organization,
        workspace=workspace,
        user=user,
    )
    session, trace, root_span, child_span = _create_trace_graph(project=project)
    agent_definition = _create_agent_definition(
        organization=organization,
        workspace=workspace,
    )
    voice_call, chat_call = _create_call_executions(
        organization=organization,
        workspace=workspace,
        agent_definition=agent_definition,
    )

    queue = AnnotationQueue.objects.create(
        name=f"annotation render queue {uuid.uuid4().hex[:8]}",
        description="Queue seeded by annotation render e2e tests.",
        instructions="Review the seeded source payload.",
        status=AnnotationQueueStatusChoices.ACTIVE.value,
        auto_assign=True,
        organization=organization,
        workspace=workspace,
        project=project,
        dataset=dataset,
        agent_definition=agent_definition,
        created_by=user,
    )
    label = _create_label(organization=organization, workspace=workspace)
    AnnotationQueueLabel.objects.create(queue=queue, label=label, order=0)
    AnnotationQueueAnnotator.objects.create(
        queue=queue,
        user=user,
        role=AnnotatorRole.MANAGER.value,
    )
    return {
        "queue": queue,
        "dataset": dataset,
        "columns": columns,
        "row": row,
        "project": project,
        "session": session,
        "trace": trace,
        "root_span": root_span,
        "child_span": child_span,
        "agent_definition": agent_definition,
        "voice_call": voice_call,
        "chat_call": chat_call,
    }


def _create_rule(seed, *, source_type, name, conditions):
    return AutomationRule.objects.create(
        name=f"{name} {uuid.uuid4().hex[:8]}",
        queue=seed["queue"],
        source_type=source_type,
        conditions=conditions,
        enabled=True,
        organization=seed["queue"].organization,
        created_by=seed["queue"].created_by,
    )


def _assert_single_item_for_rule(rule, expected_fk_field, expected_source):
    result = evaluate_rule(rule, user=rule.created_by)
    assert result["matched"] == 1
    assert result["added"] == 1
    item = QueueItem.objects.get(queue=rule.queue, source_type=rule.source_type)
    assert item.status == QueueItemStatus.PENDING.value
    assert getattr(item, f"{expected_fk_field}_id") == expected_source.id
    return item


def _annotate_detail(auth_client, queue, item):
    response = auth_client.get(
        f"{QUEUE_ITEMS_URL.format(queue_id=queue.id)}{item.id}/annotate-detail/"
    )
    assert response.status_code == status.HTTP_200_OK, response.data
    return _unwrap(response.data)


@pytest.mark.django_db
def test_dataset_row_rule_creates_pending_item_with_renderable_cells(
    auth_client, annotation_render_seed
):
    seed = annotation_render_seed
    rule = _create_rule(
        seed,
        source_type="dataset_row",
        name="dataset row render",
        conditions={
            "scope": {"dataset_id": str(seed["dataset"].id)},
            "filter": [
                {
                    "column_id": str(seed["columns"]["user_message"].id),
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "contains",
                        "filter_value": "pizza",
                    },
                }
            ],
        },
    )

    item = _assert_single_item_for_rule(rule, "dataset_row", seed["row"])
    preview = resolve_source_preview(item)
    assert preview == {
        "type": "dataset_row",
        "dataset_id": str(seed["dataset"].id),
        "dataset_name": seed["dataset"].name,
        "row_order": 7,
    }

    response = auth_client.get(
        f"{QUEUE_ITEMS_URL.format(queue_id=seed['queue'].id)}?source_type=dataset_row"
    )
    assert response.status_code == status.HTTP_200_OK, response.data
    listed_items = _list_results(response.data)
    assert len(listed_items) == 1
    assert listed_items[0]["id"] == str(item.id)
    assert listed_items[0]["source_preview"]["dataset_name"] == seed["dataset"].name

    detail = _annotate_detail(auth_client, seed["queue"], item)
    content = detail["item"]["source_content"]
    assert content["fields"] == {
        "user_message": "please help me order a pizza",
        "needs_review": "true",
        "expected_answer": "I can help with that order.",
    }
    assert content["field_types"]["needs_review"] == DataTypeChoices.BOOLEAN.value


@pytest.mark.django_db
def test_trace_rule_creates_pending_item_with_trace_preview(
    auth_client, annotation_render_seed
):
    seed = annotation_render_seed
    rule = _create_rule(
        seed,
        source_type="trace",
        name="trace render",
        conditions={
            "rules": [
                {
                    "field": "name",
                    "op": "contains",
                    "value": "annotation render trace",
                }
            ]
        },
    )

    item = _assert_single_item_for_rule(rule, "trace", seed["trace"])
    preview = resolve_source_preview(item)
    assert preview["type"] == "trace"
    assert preview["name"] == seed["trace"].name
    assert preview["project_id"] == str(seed["project"].id)
    assert "hello" in preview["input_preview"]

    detail = _annotate_detail(auth_client, seed["queue"], item)
    content = detail["item"]["source_content"]
    assert content["trace_id"] == str(seed["trace"].id)
    assert content["project_source"] == "prototype"
    assert content["input"] == {"user": "hello"}


@pytest.mark.django_db
def test_observation_span_rule_creates_pending_item_with_span_preview(
    auth_client, annotation_render_seed
):
    seed = annotation_render_seed
    rule = _create_rule(
        seed,
        source_type="observation_span",
        name="span render",
        conditions={
            "rules": [
                {
                    "field": "name",
                    "op": "contains",
                    "value": "annotation render llm span",
                }
            ]
        },
    )

    item = _assert_single_item_for_rule(rule, "observation_span", seed["child_span"])
    preview = resolve_source_preview(item)
    assert preview["type"] == "observation_span"
    assert preview["name"] == seed["child_span"].name
    assert preview["observation_type"] == "llm"
    assert "hello" in preview["input_preview"]

    detail = _annotate_detail(auth_client, seed["queue"], item)
    content = detail["item"]["source_content"]
    assert content["span_id"] == seed["child_span"].id
    assert content["trace_id"] == str(seed["trace"].id)
    assert content["output"] == {"completion": "hi"}


@pytest.mark.django_db
def test_trace_session_rule_creates_pending_item_with_session_preview(
    auth_client, annotation_render_seed
):
    seed = annotation_render_seed
    rule = _create_rule(
        seed,
        source_type="trace_session",
        name="session render",
        conditions={
            "rules": [
                {
                    "field": "name",
                    "op": "contains",
                    "value": "annotation render session",
                }
            ]
        },
    )

    item = _assert_single_item_for_rule(rule, "trace_session", seed["session"])
    preview = resolve_source_preview(item)
    assert preview == {
        "type": "trace_session",
        "session_id": str(seed["session"].id),
        "name": seed["session"].name,
        "project_id": str(seed["project"].id),
    }

    detail = _annotate_detail(auth_client, seed["queue"], item)
    content = detail["item"]["source_content"]
    assert content["session_id"] == str(seed["session"].id)
    assert content["name"] == seed["session"].name


@pytest.mark.django_db
def test_call_execution_rule_creates_voice_and_chat_items_with_detail_payloads(
    auth_client, annotation_render_seed
):
    seed = annotation_render_seed
    rule = _create_rule(
        seed,
        source_type="call_execution",
        name="call execution render",
        conditions={"rules": [{"field": "status", "op": "eq", "value": "completed"}]},
    )

    result = evaluate_rule(rule, user=rule.created_by)
    assert result["matched"] == 2
    assert result["added"] == 2
    voice_item = QueueItem.objects.get(
        queue=seed["queue"],
        source_type="call_execution",
        call_execution=seed["voice_call"],
    )
    chat_item = QueueItem.objects.get(
        queue=seed["queue"],
        source_type="call_execution",
        call_execution=seed["chat_call"],
    )
    assert voice_item.status == QueueItemStatus.PENDING.value
    assert chat_item.status == QueueItemStatus.PENDING.value

    voice_preview = resolve_source_preview(voice_item)
    assert voice_preview == {
        "type": "call_execution",
        "status": "completed",
        "duration_seconds": 42,
        "simulation_call_type": "voice",
    }
    chat_preview = resolve_source_preview(chat_item)
    assert chat_preview["simulation_call_type"] == "text"

    response = auth_client.get(
        f"{QUEUE_ITEMS_URL.format(queue_id=seed['queue'].id)}?source_type=call_execution"
    )
    assert response.status_code == status.HTTP_200_OK, response.data
    listed_items = _list_results(response.data)
    listed_by_id = {item["id"]: item for item in listed_items}
    assert listed_by_id[str(voice_item.id)]["source_preview"] == voice_preview
    assert listed_by_id[str(chat_item.id)]["source_preview"]["simulation_call_type"] == (
        "text"
    )

    detail = _annotate_detail(auth_client, seed["queue"], voice_item)
    content = detail["item"]["source_content"]
    assert content["call_id"] == str(seed["voice_call"].id)
    assert content["status"] == "completed"
    assert content["simulation_call_type"] == "voice"

    call_response = auth_client.get(
        f"/simulate/call-executions/{seed['voice_call'].id}/"
    )
    assert call_response.status_code == status.HTTP_200_OK, call_response.data
    assert call_response.data["simulation_call_type"] == "voice"
    assert call_response.data["status"] == "completed"
    assert [turn["content"] for turn in call_response.data["transcript"]] == [
        "Can you check my burger order?",
        "Your order is being prepared now.",
    ]

    chat_response = auth_client.get(f"/simulate/call-executions/{seed['chat_call'].id}/")
    assert chat_response.status_code == status.HTTP_200_OK, chat_response.data
    assert chat_response.data["simulation_call_type"] == "text"
    assert chat_response.data["transcript"][0]["messages"] == ["Do you have fries?"]
