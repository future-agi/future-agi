"""Queue export must read the literal dotted keys offered by field discovery."""

import json
from dataclasses import replace
from unittest.mock import patch

import pytest

from model_hub.models.annotation_queues import (
    FULL_ACCESS_QUEUE_ROLES,
    AnnotationQueue,
    AnnotationQueueAnnotator,
    AnnotationQueueLabel,
    QueueItem,
    QueueItemNote,
)
from model_hub.models.choices import DataTypeChoices
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.score import Score
from model_hub.tests.test_annotation_queues_ch_sources import _make_chspan, _ReaderCM
from model_hub.views.annotation_queues import _nested_get
from tracer.models.project import Project


@pytest.mark.parametrize(
    ("content", "path", "expected"),
    [
        pytest.param(
            {"fields": {"answer.text": "original-answer"}},
            "fields.answer.text", "original-answer", id="literal-dataset-key",
        ),
        pytest.param(
            {"span_attributes": {"response.meta": {"score": 5}}},
            "span_attributes.response.meta.score", 5, id="literal-intermediate-key",
        ),
        pytest.param(
            {"metadata": {"request": {"method": "POST"}}},
            "metadata.request.method", "POST", id="ordinary-nested-path",
        ),
        pytest.param(
            {"metadata": {"messages": [{"answer.text": "first"}]}},
            "metadata.messages.0.answer.text", "first", id="list-literal-key",
        ),
        pytest.param(
            {"metadata": {"messages": [{"text": "first"}]}},
            "metadata.messages.0.text", "first", id="ordinary-list-index",
        ),
        pytest.param(
            {"fields": {"answer": {"text": "shadow"}, "answer.text": "literal"}},
            "fields.answer.text", "literal", id="literal-before-nested-shadow",
        ),
        pytest.param(
            {"fields": {"answer.text": None, "answer": {"text": "shadow"}}},
            "fields.answer.text", None, id="literal-null-is-not-missing",
        ),
        pytest.param(
            {"fields": {"answer.text": False, "answer": {"text": "shadow"}}},
            "fields.answer.text", False, id="literal-false-is-not-missing",
        ),
        pytest.param(
            {"fields": {"answer.text": 0, "answer": {"text": "shadow"}}},
            "fields.answer.text", 0, id="literal-zero-is-not-missing",
        ),
        pytest.param(
            {"fields": {"answer.text": "", "answer": {"text": "shadow"}}},
            "fields.answer.text", "", id="literal-empty-is-not-missing",
        ),
        pytest.param(
            {"fields": {"a.b": {"other": 1}, "a": {"b": {"c": "nested"}}}},
            "fields.a.b.c", "nested", id="incomplete-literal-prefix-falls-back",
        ),
        pytest.param(
            {"fields": {"answer..text": "literal"}},
            "fields.answer..text", "literal", id="repeated-dots",
        ),
        pytest.param(
            {"fields": {"answer": {}}},
            "fields.answer.text", None, id="missing-path",
        ),
        pytest.param(
            {"metadata": {"messages": []}},
            "metadata.messages.0.text", None, id="missing-list-index",
        ),
    ],
)
def test_export_lookup_preserves_literal_keys_and_nested_paths(content, path, expected):
    assert _nested_get(content, path) == expected


@pytest.fixture
def export_queue(organization, workspace, user):
    project = Project.objects.create(
        name="Literal key export project",
        organization=organization,
        workspace=workspace,
        model_type="GenerativeLLM",
        trace_type="observe",
    )
    queue = AnnotationQueue.objects.create(
        name="Literal key export queue",
        status="active",
        organization=organization,
        workspace=workspace,
        project=project,
        created_by=user,
    )
    label = AnnotationsLabels.objects.create(
        name="Quality",
        type="categorical",
        settings={"options": [{"label": "Positive"}], "multi_choice": False},
        organization=organization,
        workspace=workspace,
    )
    AnnotationQueueLabel.objects.create(queue=queue, label=label, order=0)
    AnnotationQueueAnnotator.objects.update_or_create(
        queue=queue,
        user=user,
        deleted=False,
        defaults={"role": "manager", "roles": FULL_ACCESS_QUEUE_ROLES},
    )
    return queue, label, project


def _export_discovered_fields(auth_client, queue, columns_by_id):
    url = f"/model-hub/annotation-queues/{queue.id}/"
    response = auth_client.get(f"{url}export-fields/")
    assert response.status_code == 200, response.data
    payload = response.data.get("result", response.data)
    fields = {field["id"]: field for field in payload["fields"]}
    assert set(columns_by_id) <= fields.keys(), fields

    # Submit the server-generated identifiers, just as the hosted dialog does.
    response = auth_client.post(
        f"{url}export-to-dataset/",
        {
            "dataset_name": "Literal key export result",
            "status_filter": "completed",
            "column_mapping": [
                {"field": fields[field_id]["id"], "column": column, "enabled": True}
                for field_id, column in columns_by_id.items()
            ],
        },
        format="json",
    )
    assert response.status_code == 200, response.data
    result = response.data.get("result", response.data)
    assert result["rows_created"] == 1
    row = Row.objects.get(dataset_id=result["dataset_id"], deleted=False)
    cells = {
        cell.column.name: cell.value
        for cell in Cell.objects.filter(row=row, deleted=False).select_related("column")
    }
    return cells, fields, row


@pytest.mark.django_db
def test_generated_dataset_field_round_trips_literal_key_and_annotation_notes(
    auth_client, organization, workspace, user, export_queue
):
    queue, label, project = export_queue
    dataset = Dataset.objects.create(
        name="Literal source columns",
        organization=organization,
        workspace=workspace,
        user=user,
    )
    row = Row.objects.create(dataset=dataset, order=1)
    for name, value in {"answer.text": "original-answer", "plain": "plain-value"}.items():
        column = Column.objects.create(
            name=name, data_type=DataTypeChoices.TEXT.value, dataset=dataset,
            source="OTHERS", status="completed",
        )
        Cell.objects.create(dataset=dataset, row=row, column=column, value=value)
    item = QueueItem.objects.create(
        queue=queue, source_type="dataset_row", dataset_row=row,
        status="completed", organization=organization, workspace=workspace,
        project=project,
    )
    Score.objects.create(
        source_type="dataset_row", dataset_row=row, queue_item=item,
        label=label, annotator=user, value="Positive", notes="Label note",
        score_source="human", organization=organization, workspace=workspace,
    )
    QueueItemNote.objects.create(
        queue_item=item, annotator=user, notes="Whole item note",
        organization=organization, workspace=workspace,
    )

    cells, fields, exported_row = _export_discovered_fields(
        auth_client, queue,
        {
            "attr:fields.answer.text": "copied_answer",
            "attr:fields.plain": "copied_plain",
            f"label:{label.id}:value": "quality",
            f"label:{label.id}:notes": "quality_notes",
            "item_notes": "item_notes",
        },
    )

    assert fields["attr:fields.answer.text"]["path"] == "fields.answer.text"
    assert cells == {
        "copied_answer": "original-answer",
        "copied_plain": "plain-value",
        "quality": "Positive",
        "quality_notes": "Label note",
        "item_notes": "Whole item note",
    }
    assert exported_row.metadata["queue_item_id"] == str(item.id)


@pytest.mark.django_db
def test_generated_span_fields_round_trip_dotted_and_nested_attributes(
    auth_client, organization, workspace, export_queue
):
    queue, _, project = export_queue
    span = replace(
        _make_chspan(project_id=project.id),
        metadata=json.dumps({"request": {"method": "POST"}}),
        attributes_extra=json.dumps({
            "extra.key": "extra-val",
            "response.meta": {"score": 5},
            # Discovery encounters the nested shadow first. Its type must not
            # override the literal key's type or value for their shared ID.
            "answer": {"text": "nested-shadow"},
            "answer.text": 7,
        }),
    )
    QueueItem.objects.create(
        queue=queue, source_type="observation_span", observation_span_id=span.id,
        status="completed", organization=organization, workspace=workspace,
        project=project,
    )
    with patch("tracer.services.clickhouse.v2.get_reader", return_value=_ReaderCM(span)):
        cells, fields, _ = _export_discovered_fields(
            auth_client, queue,
            {
                "attr:span_attributes.gen_ai.request.model": "copied_model",
                "attr:span_attributes.extra.key": "copied_extra",
                "attr:span_attributes.response.meta.score": "copied_score",
                "attr:span_attributes.answer.text": "copied_literal",
                "attr:resource_attributes.service.name": "copied_service",
                "attr:metadata.request.method": "copied_method",
            },
        )

    assert cells == {
        "copied_model": "gpt-4o",
        "copied_extra": "extra-val",
        "copied_score": "5",
        "copied_literal": "7",
        "copied_service": "collector-svc",
        "copied_method": "POST",
    }
    assert fields["attr:span_attributes.answer.text"]["data_type"] == (
        DataTypeChoices.INTEGER.value
    )
