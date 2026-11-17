"""Runtime cohorts retain rootless and pre-window traces through Observe paging."""

from datetime import timedelta

import pytest

from model_hub.tests.skills_hub import test_runtime_api_http as runtime_fixtures
from model_hub.tests.skills_hub.test_runtime_api_http import (
    get,
    landing_ids,
    load,
    params,
)
from model_hub.tests.skills_hub.test_runtime_usage import END, START, span
from tracer.selectors.trace_filter_reads import read_bounded_filter_page
from tracer.serializers.filters import FilterListField
from tracer.services.clickhouse.query_service import QueryResult
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)
from tracer.tests._ch_seed import seed_ch_spans

pytestmark = [pytest.mark.django_db, pytest.mark.integration]
live_service = runtime_fixtures.live_service
project = runtime_fixtures.project
runtime_ch = runtime_fixtures.runtime_ch


def observe_filters(link):
    return FilterListField().run_validation([*link["filters"], {
        "column_id": "created_at", "filter_config": {
            "filter_type": "datetime", "filter_op": "between",
            "filter_value": [link["from"].isoformat(), link["to"].isoformat()],
        },
    }])


class Analytics:
    def __init__(self, client):
        self.client = client

    def execute_ch_query(self, sql, params, **kwargs):
        data = list(self.client.query(sql, parameters=params).named_results())
        return QueryResult(data, len(data), "clickhouse", 0.0)


@pytest.mark.parametrize("cursor_mode", [False, True])
@pytest.mark.parametrize("root_mode", ["missing", "before_window", "days_before_window", "deleted"])
def test_runtime_count_lands_when_root_has_not_arrived(
    user, organization, workspace, hub_skill, publish_skill, project, live_service, root_mode, cursor_mode,
):
    version = publish_skill(hub_skill).version
    row = load(project, version)
    rows = [row]
    if root_mode != "missing":
        root = span(
            str(project.pk), row["trace_id"], name="request", parent_span_id="",
            span_attributes={}, status="ERROR",
            start_time=START - (timedelta(days=3) if root_mode == "days_before_window" else timedelta(minutes=5)),
        )
        if root_mode == "deleted":
            root["deleted"] = True
        rows.append(root)
    seed_ch_spans(rows, client=live_service, version_from_created_at=True)
    response = get(user, organization, workspace, params(hub_skill))
    assert response.status_code == 200, response.data
    result = response.data["result"]
    assert result["summary"]["traces"] == 1
    assert result["summary"]["incomplete"] == int(root_mode in {"missing", "deleted"})
    link = result["rows"][0]["links"]["all"]
    assert (link["from"], link["to"]) == (START, END)
    assert landing_ids(live_service, link, [row["trace_id"]]) == {row["trace_id"]}

    filters = observe_filters(link)
    builder = TraceListQueryBuilderV2(project_id=str(project.pk), filters=filters)
    page = read_bounded_filter_page(
        builder=builder, analytics=Analytics(live_service), filters=filters,
        key_field="trace_id", page_number=0, page_size=10, deadline_ms=30_000,
        bounded_continuation=cursor_mode, include_incomplete_rows=cursor_mode,
        carry_continuation_slice_width=cursor_mode,
    )
    assert page.complete, page
    assert [item["trace_id"] for item in page.rows] == [row["trace_id"]]
    expected = row if root_mode in {"missing", "deleted"} else root
    assert page.rows[0]["root_span_id"] == expected["id"]
    if root_mode in {"missing", "deleted"}:
        assert page.rows[0]["status"] is None
        assert page.rows[0]["latency_ms"] is None
    else:
        assert page.rows[0]["status"] == "ERROR"
    identities = builder.content_root_identities_for_rows(page.rows)
    sql, query = builder.build_content_query([row["trace_id"]], root_identities=identities)
    hydrated = list(live_service.query(sql, parameters=query).named_results())
    assert builder.content_root_rows_match(page.rows, hydrated)

    # Without the cohort, the normal Observe window remains root-grain.
    ordinary = TraceListQueryBuilderV2(
        project_id=str(project.pk), filters=[item for item in filters if item["column_id"] != "skill.trace_cohort"],
    )
    sql, query = ordinary.build_filter_match_query([row["trace_id"]])
    assert list(live_service.query(sql, parameters=query).named_results()) == []


def test_runtime_cohort_cursor_keeps_roots_before_load_window(
    user, organization, workspace, hub_skill, publish_skill, project, live_service,
):
    version = publish_skill(hub_skill).version
    loads = [load(project, version) for _ in range(3)]
    roots = [span(
        str(project.pk), row["trace_id"], name="request", parent_span_id="",
        span_attributes={}, start_time=START - timedelta(days=index + 1),
    ) for index, row in enumerate(loads)]
    children = [span(str(project.pk), row["trace_id"], name="child", span_attributes={}) for row in loads]
    unrelated = span(str(project.pk), "99999999-9999-4999-8999-999999999999", parent_span_id="", span_attributes={})
    seed_ch_spans([*loads, *roots, *children, unrelated], client=live_service, version_from_created_at=True)
    response = get(user, organization, workspace, params(hub_skill, to=(END + timedelta(days=6)).isoformat()))
    assert response.status_code == 200, response.data
    link = response.data["result"]["rows"][0]["links"]["all"]
    filters = observe_filters(link)
    builder = TraceListQueryBuilderV2(project_id=str(project.pk), filters=filters)
    start, end = builder.parse_time_range(filters)
    sql, query = builder.build_filter_seed_page(slice_start=start, slice_end=end, limit=10)
    assert len(list(live_service.query(sql, parameters=query).named_results())) == 3
    cursor = {}
    seen = []
    for index in range(3):
        builder = TraceListQueryBuilderV2(project_id=str(project.pk), filters=filters)
        page = read_bounded_filter_page(
            builder=builder, analytics=Analytics(live_service), filters=filters,
            key_field="trace_id", page_number=0, page_size=1, deadline_ms=30_000,
            bounded_continuation=True, include_incomplete_rows=True, **cursor,
        )
        assert page.complete, page
        assert len(page.rows) == 1
        seen.append(page.rows[0]["trace_id"])
        assert page.has_more == (index < 2)
        cursor = {"cursor_start_time": page.rows[-1]["start_time"], "cursor_order_token": page.rows[-1]["trace_id"]}
    assert seen == [row["trace_id"] for row in loads]
