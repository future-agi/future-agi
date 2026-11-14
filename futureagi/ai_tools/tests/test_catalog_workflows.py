"""Exercise catalog contracts against the views they actually invoke."""

import json

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from model_hub.models.annotation_queues import (
    AnnotationQueue,
    AnnotationQueueAnnotator,
    QueueItem,
)
from tracer.models.monitor import UserAlertMonitor, UserAlertMonitorLog
from tracer.serializers.monitor import UserAlertMonitorListResponseSerializer
from tracer.serializers.trace import TraceObserveListQuerySerializer

from ai_tools.registry import registry
from ai_tools.tests.fixtures import make_dataset, make_dataset_with_rows

pytestmark = pytest.mark.django_db


def make_monitor(context, name="Catalog alert"):
    return UserAlertMonitor.objects.create(
        name=name,
        organization=context.organization,
        workspace=context.workspace,
        created_by=context.user,
        metric_type="count_of_errors",
        threshold_operator="greater_than",
    )


def test_trace_property_discovery_returns_filter_metadata(tool_context):
    result = registry.get("list_trace_properties").run(
        {
            "source": "traces",
            "role": "dimension",
            "per_eval_config": True,
            "cursor_mode": True,
            "category": "system_metric",
            "page_size": 50,
        },
        tool_context,
    )
    assert not result.is_error, result.content
    properties = result.data["metrics"]
    assert properties
    assert all("name" in prop and "property_id" in prop for prop in properties)
    assert all(prop["role"] == "dimension" for prop in properties)
    status_property = next(prop for prop in properties if prop["name"] == "status")
    query = TraceObserveListQuerySerializer(
        data={
            "filters": json.dumps(
                [
                    {
                        "column_id": status_property["name"],
                        "property_id": status_property["property_id"],
                        "source": "traces",
                        "filter_config": {
                            "col_type": "SYSTEM_METRIC",
                            "filter_type": "categorical",
                            "filter_op": "equals",
                            "filter_value": "ERROR",
                        },
                    }
                ]
            )
        }
    )
    assert query.is_valid(), query.errors


def test_alert_rules_use_zero_based_pages(tool_context):
    for index in range(35):
        make_monitor(tool_context, f"Alert {index}")
    tool = registry.get("list_alert_rules")
    pages = [
        tool.run({"page_number": page, "page_size": 1}, tool_context) for page in (0, 1)
    ]
    for result in pages:
        assert not result.is_error, result.content
        assert len(result.data["table"]) == 1
        assert result.data["metadata"]["total_rows"] == 35
        response = UserAlertMonitorListResponseSerializer(
            data={"status": True, "result": result.data}
        )
        assert response.is_valid(), response.errors
    assert pages[0].data["table"][0]["id"] != pages[1].data["table"][0]["id"]


@pytest.mark.parametrize(
    "arguments",
    [
        {"page_number": -1},
        {"page_size": 0},
        {"page_size": 101},
        {"page": 2, "limit": 1},
    ],
)
def test_alert_rules_reject_invalid_pagination(tool_context, arguments):
    result = registry.get("list_alert_rules").run(arguments, tool_context)
    assert result.is_error
    assert result.error_code == "VALIDATION_ERROR"


def test_dataset_deletion_accepts_ids_and_soft_deletes(tool_context):
    dataset = make_dataset(tool_context)
    result = registry.get("delete_datasets").run(
        {"dataset_ids": [str(dataset.id)]}, tool_context
    )
    assert not result.is_error, result.content
    dataset.refresh_from_db()
    assert dataset.deleted is True


@pytest.mark.parametrize(
    "arguments", [{}, {"dataset_ids": []}, {"dataset_ids": ["invalid"]}]
)
def test_dataset_deletion_validates_before_execution(tool_context, arguments):
    result = registry.get("delete_datasets").run(arguments, tool_context)
    assert result.is_error
    assert result.error_code == "VALIDATION_ERROR"


def test_fired_alerts_limit_the_database_read(tool_context):
    monitor = make_monitor(tool_context)
    UserAlertMonitorLog.objects.bulk_create(
        [
            UserAlertMonitorLog(alert=monitor, type="critical", message=f"Log {i}")
            for i in range(105)
        ]
    )
    tool = registry.get("list_fired_alerts")
    with CaptureQueriesContext(connection) as queries:
        first = tool.run({"page": 1, "limit": 1}, tool_context)
    assert not first.is_error, first.content
    assert len(first.data["results"]) == 1
    assert first.data["count"] == 105
    reads = [
        query["sql"]
        for query in queries.captured_queries
        if 'FROM "tracer_useralertmonitorlog"' in query["sql"]
        and "COUNT(" not in query["sql"]
    ]
    assert reads
    assert all("LIMIT 1" in query for query in reads)
    second = tool.run({"page": 2, "limit": 1}, tool_context)
    assert not second.is_error, second.content
    assert first.data["results"][0]["id"] != second.data["results"][0]["id"]
    default = tool.run({}, tool_context)
    assert not default.is_error, default.content
    assert len(default.data["results"]) == 10


def test_annotation_reservation_is_classified_as_a_write(tool_context, monkeypatch):
    _, _, rows = make_dataset_with_rows(tool_context)
    queue = AnnotationQueue.objects.create(
        name="Catalog reservation",
        organization=tool_context.organization,
        workspace=tool_context.workspace,
        created_by=tool_context.user,
        status="active",
    )
    AnnotationQueueAnnotator.objects.update_or_create(
        queue=queue,
        user=tool_context.user,
        defaults={"role": "annotator", "roles": ["annotator"]},
    )
    item = QueueItem.objects.create(
        queue=queue,
        organization=tool_context.organization,
        workspace=tool_context.workspace,
        source_type="dataset_row",
        dataset_row=rows[0],
    )
    tool = registry.get("get_annotation_item_detail")
    assert not tool.is_read_only
    assert tool.annotations["idempotentHint"] is False
    args = {"queue_id": str(queue.id), "id": str(item.id)}
    with monkeypatch.context() as denied:
        denied.setattr(
            "accounts.authentication.APIKeyAuthentication._can_write_to_workspace",
            lambda *args: False,
        )
        result = tool.run({**args, "reserve": True}, tool_context)
        assert result.is_error
        assert result.error_code == "PERMISSION_DENIED"
        item.refresh_from_db()
        assert item.reserved_by_id is None
    read = tool.run(args, tool_context)
    assert not read.is_error, read.content
    item.refresh_from_db()
    assert item.reserved_by_id is None
    reserve = tool.run({**args, "reserve": True}, tool_context)
    assert not reserve.is_error, reserve.content
    item.refresh_from_db()
    assert item.reserved_by_id == tool_context.user.id
