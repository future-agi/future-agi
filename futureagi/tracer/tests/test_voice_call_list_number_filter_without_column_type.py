"""A number filter on an eval column, sent with or without a column type.

The Observe page can turn a number filter on an eval into a list-request
filter whose ``column_id`` is the eval config id and whose ``filter_config``
has no ``col_type``. The request passes validation, but the bounded filter
compiler cannot plan the leaf: it refuses it while the page plan is being
sized, before any ClickHouse statement or eval-config lookup. The voice call
list answers that request as the trace list does, with HTTP 400 and a plain
reason. The same filter sent with its column type still lists.
"""

import json
import re
from unittest import mock

import pytest
from rest_framework import status

from tracer.selectors.trace_filter_reads import BoundedFilterPage
from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    UnsupportedFilterShapeError,
)
from tracer.services.clickhouse.v2.query_builders.voice_call_list import (
    VoiceCallListQueryBuilderV2,
)

VOICE_CALL_LIST_URL = "/tracer/trace/list_voice_calls/"
INVALID_FILTER_REASON = "Voice call filter configuration is invalid"


def _eval_number_leaf(eval_id, operator="greater_than", value=0.5, col_type=None):
    # The page's shape: the eval id as the column and the typed percentage
    # divided by 100 (50 becomes 0.5). The page itself sends no col_type.
    filter_config = {
        "filter_type": "number",
        "filter_op": operator,
        "filter_value": value,
    }
    if col_type is not None:
        filter_config["col_type"] = col_type
    return {"column_id": str(eval_id), "filter_config": filter_config}


def _complete_empty_page():
    return BoundedFilterPage(
        rows=[],
        has_more=False,
        complete=True,
        status="complete",
        error_code=None,
        total_rows_lower_bound=0,
        elapsed_ms=0.0,
        query_count=0,
        rows_returned=0,
        result_payload_bytes=0,
        attempts=(),
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("operator", "value"),
    [("greater_than", 0.5), ("between", [0.2, 0.8])],
    ids=["greater_than", "between"],
)
def test_a_number_filter_without_column_type_is_refused_with_a_plain_reason(
    auth_client, project, custom_eval_config, monkeypatch, operator, value
):
    eval_id = str(custom_eval_config.id)
    analytics = mock.MagicMock()
    eval_config_lookup = mock.MagicMock()
    log = mock.MagicMock()
    monkeypatch.setattr("tracer.views.trace.V2AnalyticsQueryService", lambda: analytics)
    monkeypatch.setattr(
        "tracer.views.trace.get_project_eval_configs", eval_config_lookup
    )
    monkeypatch.setattr("tracer.views.trace.logger", log)

    response = auth_client.get(
        VOICE_CALL_LIST_URL,
        {
            "project_id": str(project.id),
            "filters": json.dumps([_eval_number_leaf(eval_id, operator, value)]),
        },
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.data["code"] == "invalid"
    assert response.data["result"] == INVALID_FILTER_REASON
    assert eval_id not in str(response.data)
    # A refused filter is the caller's error: it is logged with its type, never
    # as a server failure.
    log.exception.assert_not_called()
    log.info.assert_called_once_with(
        "voice_call_list_filter_invalid",
        project_id=str(project.id),
        error_type="UnsupportedFilterShapeError",
    )
    eval_config_lookup.assert_not_called()
    assert analytics.mock_calls == []

    # The view's first planning call raises the detail the response leaves out.
    builder = VoiceCallListQueryBuilderV2(
        project_id=str(project.id),
        filters=[_eval_number_leaf(eval_id, operator, value)],
        page_number=0,
        page_size=30,
        eval_config_ids=[],
        remove_simulation_calls=False,
        eval_count_mode=True,
        annotation_label_ids=[],
    )
    with pytest.raises(
        UnsupportedFilterShapeError,
        match=re.escape(f"unsupported trace filter {eval_id!r}"),
    ):
        builder.recommended_filter_classify_batch_size()


@pytest.mark.django_db
def test_a_number_filter_with_its_column_type_still_lists(
    auth_client, project, custom_eval_config, monkeypatch
):
    eval_id = str(custom_eval_config.id)
    leaf = _eval_number_leaf(eval_id, col_type="EVAL_METRIC")
    analytics = mock.MagicMock()
    page_read = mock.MagicMock(return_value=_complete_empty_page())
    monkeypatch.setattr("tracer.views.trace.V2AnalyticsQueryService", lambda: analytics)
    monkeypatch.setattr(
        "tracer.views.trace.get_project_eval_configs", lambda _project_id: ([], [])
    )
    monkeypatch.setattr(
        "tracer.views.trace.get_annotation_labels_for_project",
        lambda _project_id: [],
    )
    monkeypatch.setattr(
        "tracer.views.trace._build_annotation_map_from_scores",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "tracer.selectors.trace_filter_reads.read_bounded_filter_page", page_read
    )

    response = auth_client.get(
        VOICE_CALL_LIST_URL,
        {"project_id": str(project.id), "filters": json.dumps([leaf])},
    )

    assert response.status_code == status.HTTP_200_OK
    page_read.assert_called_once()
    [listed_filter] = page_read.call_args.kwargs["filters"]
    assert listed_filter["column_id"] == eval_id
    assert listed_filter["filter_config"]["col_type"] == "EVAL_METRIC"
    analytics.execute_ch_query.assert_not_called()
