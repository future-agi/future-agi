"""Observe dataset previews retain the selected cohort before applying the cap."""

from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest
from rest_framework.exceptions import ValidationError

from tracer.services.dataset_sources import (
    DatasetSourcePreviewSerializer,
    preview_sources,
)

PROJECT_ID = UUID("11111111-1111-4111-8111-111111111111")
ORG_ID = UUID("22222222-2222-4222-8222-222222222222")
TRACE_ID = "33333333-3333-4333-8333-333333333333"
OTHER_TRACE_ID = "44444444-4444-4444-8444-444444444444"
SHARED_SPAN_ID = "0123456789abcdef"
WINDOW = ["2026-08-01T00:00:00Z", "2026-08-03T00:00:00Z"]
FILTERS = [
    {
        "column_id": "name",
        "filter_config": {
            "filter_type": "text",
            "filter_op": "equals",
            "filter_value": "chosen cohort",
        },
    },
    {
        "column_id": "status",
        "filter_config": {
            "filter_type": "text",
            "filter_op": "equals",
            "filter_value": "ERROR",
        },
    },
    {
        "column_id": "created_at",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": WINDOW,
        },
    },
]


def payload(kind="spans"):
    return {
        "project": str(PROJECT_ID),
        "source_kind": kind,
        "failures_only": False,
        "filters": deepcopy(FILTERS),
        "exclude_ids": ["excluded"],
    }


def fake_project():
    return SimpleNamespace(
        id=PROJECT_ID,
        organization_id=ORG_ID,
        organization=SimpleNamespace(id=ORG_ID),
    )


def fake_reader(rows=()):
    reader = MagicMock()
    reader.__enter__.return_value = reader
    reader._client.query.return_value = SimpleNamespace(result_rows=list(rows))
    return reader


def test_observe_preview_serializer_preserves_scope_and_exclusions():
    serializer = DatasetSourcePreviewSerializer(data=payload())
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["filters"] == FILTERS
    assert serializer.validated_data["exclude_ids"] == ["excluded"]


@pytest.mark.parametrize("kind", ["traces", "spans"])
@pytest.mark.parametrize("truncated", [False, True])
def test_observe_preview_resolves_matching_ids_before_hydration(kind, truncated):
    project = fake_project()
    options = payload(kind)
    source_id = TRACE_ID if kind == "traces" else "child"
    resolver_name = f"resolve_filtered_{'trace' if kind == 'traces' else 'span'}_ids"
    reader = fake_reader(
        [("child", TRACE_ID, None, "chosen cohort", datetime(2026, 8, 2, tzinfo=UTC))]
    )
    with (
        patch(
            f"model_hub.services.bulk_selection.{resolver_name}",
            return_value=SimpleNamespace(ids=[source_id], truncated=truncated),
        ) as resolve,
        patch("tracer.services.dataset_sources.get_reader", return_value=reader),
    ):
        result = preview_sources(project, options)
    resolve.assert_called_once_with(
        project_id=project.id,
        organization=project.organization,
        filters=FILTERS,
        exclude_ids=["excluded"],
        cap=200,
        user=None,
    )
    sql = reader._client.query.call_args.args[0]
    params = reader._client.query.call_args.kwargs["parameters"]
    key = "trace_ids" if kind == "traces" else "span_ids"
    column = "trace_id" if kind == "traces" else "id"
    assert f"{column} IN %({key})s" in sql
    assert params[key] == (source_id,)
    assert params["since"].replace(tzinfo=UTC) == datetime(2026, 8, 1, tzinfo=UTC)
    assert params["now"].replace(tzinfo=UTC) == datetime(2026, 8, 3, tzinfo=UTC)
    assert "start_time < %(now)s" in sql
    assert "org_id = %(org)s" in sql
    assert "project_id = %(project)s" in sql
    assert params["org"] == str(ORG_ID)
    assert params["project"] == str(PROJECT_ID)
    assert ("parent_span_id = ''" in sql) == (kind == "traces")
    assert result["limited"] is truncated
    assert result["limit"] == 200
    assert [item["span_id"] for item in result["items"]] == ["child"]
    assert options == payload(kind)


def test_scoped_span_preview_refuses_a_shared_id_from_outside_the_cohort():
    # Only the trace-a span matches the filters, so the resolver returns one
    # bare ID. The trace-b span reuses that ID and must not be offered.
    reader = fake_reader(
        [
            (
                SHARED_SPAN_ID,
                TRACE_ID,
                None,
                "chosen cohort",
                datetime(2026, 8, 2, tzinfo=UTC),
            ),
            (
                SHARED_SPAN_ID,
                OTHER_TRACE_ID,
                None,
                "outside cohort",
                datetime(2026, 8, 2, 1, tzinfo=UTC),
            ),
        ]
    )
    with (
        patch(
            "model_hub.services.bulk_selection.resolve_filtered_span_ids",
            return_value=SimpleNamespace(ids=[SHARED_SPAN_ID], truncated=False),
        ),
        patch("tracer.services.dataset_sources.get_reader", return_value=reader),
        pytest.raises(ValidationError) as error,
    ):
        preview_sources(fake_project(), payload())
    params = reader._client.query.call_args.kwargs["parameters"]
    assert params["span_ids"] == (SHARED_SPAN_ID,)
    (message,) = error.value.detail
    assert SHARED_SPAN_ID in message


def test_empty_observe_cohort_never_falls_back_to_project_wide_preview():
    with (
        patch(
            "model_hub.services.bulk_selection.resolve_filtered_span_ids",
            return_value=SimpleNamespace(ids=[], truncated=False),
        ),
        patch("tracer.services.dataset_sources.get_reader") as get_reader,
    ):
        result = preview_sources(fake_project(), payload())
    get_reader.assert_not_called()
    assert result == {"items": [], "limited": False, "limit": 200}


def test_resolver_failure_does_not_fall_back_to_unfiltered_preview():
    with (
        patch(
            "model_hub.services.bulk_selection.resolve_filtered_span_ids",
            side_effect=RuntimeError("source unavailable"),
        ),
        patch("tracer.services.dataset_sources.get_reader") as get_reader,
        pytest.raises(RuntimeError, match="source unavailable"),
    ):
        preview_sources(fake_project(), payload())
    get_reader.assert_not_called()


@pytest.mark.parametrize("filters", [[], FILTERS[:2]])
def test_observe_scope_without_a_date_window_is_rejected(filters):
    data = payload()
    data["filters"] = filters
    serializer = DatasetSourcePreviewSerializer(data=data)
    assert not serializer.is_valid()
    assert "filters" in serializer.errors


def test_invalid_observe_window_is_rejected_before_reading():
    data = payload()
    data["filters"][-1]["filter_config"]["filter_value"] = list(reversed(WINDOW))
    serializer = DatasetSourcePreviewSerializer(data=data)
    assert not serializer.is_valid()
    assert "filters" in serializer.errors


def test_session_scope_is_not_silently_resolved_as_trace_filters():
    serializer = DatasetSourcePreviewSerializer(data=payload("sessions"))
    with pytest.raises(ValidationError):
        serializer.is_valid(raise_exception=True)


def test_exclusions_require_a_filter_population():
    data = payload()
    del data["filters"]
    serializer = DatasetSourcePreviewSerializer(data=data)
    assert not serializer.is_valid()
    assert "exclude_ids" in serializer.errors
