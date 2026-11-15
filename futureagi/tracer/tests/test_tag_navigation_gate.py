"""Native CH25 navigation resolves finite tag scope before classifying targets."""

from datetime import timedelta
from types import SimpleNamespace

import pytest

from tracer.selectors.trace_filter_reads import read_bounded_filter_neighbors
from tracer.services.clickhouse.read_budget import ReadDeadline
from tracer.services.clickhouse.v2 import effective_tags
from tracer.services.clickhouse.v2.query_builders.span_list import (
    SpanListQueryBuilderV2,
)
from tracer.services.entity_tags import TagReadUnavailable
from tracer.tests.tag_read_fixtures import empty_tag_assignments  # noqa: F401
from tracer.tests.test_effective_tag_filters import leaf
from tracer.tests.test_selected_root_tag_graphs import (
    PROJECT,
    START,
    _drop_legacy_ch_spans_mvs,  # noqa: F401
    _ensure_test_score_tenant_column,  # noqa: F401
    window,
)
from tracer.tests.test_selected_root_tag_graphs import (
    roots_store as roots_store,
)


def navigate(store):
    filters = window(leaf("tag", ["wanted"], "in"))
    return read_bounded_filter_neighbors(
        builder=SpanListQueryBuilderV2(project_id=PROJECT, filters=filters),
        analytics=SimpleNamespace(execute_ch_query=store.query),
        filters=filters,
        key_field="id",
        target_id="current",
        deadline_ms=30000,
        scan_limit=512,
        page_size=200,
        require_unique_target=True,
    )


def test_span_navigation_tag_scope_reaches_exact_neighbors(roots_store):
    for minutes, name in [(10, "older"), (20, "current"), (30, "newer")]:
        roots_store.insert(
            id=name,
            parent_span_id="parent",
            start_time=START + timedelta(minutes=minutes),
        )
    roots_store.insert(
        id="irrelevant",
        parent_span_id="parent",
        tags='["other"]',
        start_time=START + timedelta(minutes=25),
    )
    result = navigate(roots_store)
    assert result.complete, result.error_code
    assert result.current["id"] == "current"
    assert result.newer["id"] == "newer"
    assert result.older["id"] == "older"


@pytest.mark.parametrize(
    "code", ["tag_overlay_budget_exceeded", "tag_assignment_unavailable"]
)
def test_neighbor_executor_does_not_hide_tag_error(monkeypatch, roots_store, code):
    def fail(*args, **kwargs):
        raise TagReadUnavailable(code)

    monkeypatch.setattr(
        "tracer.selectors.trace_filter_reads.prepare_effective_tag_params", fail
    )
    with pytest.raises(TagReadUnavailable, match=code):
        navigate(roots_store)


@pytest.mark.parametrize("count", [0, 513])
def test_navigation_discovery_is_finite_and_empty_never_scans_assignments(
    monkeypatch, count
):
    filters = window(leaf("tag", ["wanted"], "in"))
    builder = SpanListQueryBuilderV2(project_id=PROJECT, filters=filters)
    _, params = builder.build_filter_navigation_target_query(
        target_id="current", result_limit=2
    )
    calls = []

    def execute(query, bound, **kwargs):
        calls.append((query, bound, kwargs))
        return SimpleNamespace(
            data=[{"trace_id": str(index)} for index in range(count)]
        )

    def unexpected_overlay(**kwargs):
        pytest.fail("empty or overflowing target discovery must not read assignments")

    monkeypatch.setattr(effective_tags, "read_tag_overlay", unexpected_overlay)
    if count:
        with pytest.raises(TagReadUnavailable, match="tag_candidate_budget_exceeded"):
            effective_tags.prepare_effective_tag_params(
                execute, params, deadline=ReadDeadline.start(10000)
            )
    else:
        prepared = effective_tags.prepare_effective_tag_params(
            execute, params, deadline=ReadDeadline.start(10000)
        )
        assert all(
            value == 1 for key, value in prepared.items() if key.endswith("_scoped")
        )
    assert len(calls) == 1
    query, bound, limits = calls[0]
    assert "LIMIT 513" in query
    assert "project_id IN %(tag_target_projects)s" in query
    assert bound["tag_target_projects"] == (PROJECT,)
    assert bound["tag_target_ids"] == ("current",)
    assert limits["settings"]["max_result_rows"] == 513
    assert 0 < limits["server_execution_cap_ms"] <= 10000
    assert limits["settings"]["result_overflow_mode"] == "throw"


def test_navigation_rejects_oversized_project_scope_before_source_read():
    filters = window(leaf("tag", ["wanted"], "in"))
    builder = SpanListQueryBuilderV2(project_id=PROJECT, filters=filters)
    _, params = builder.build_filter_navigation_target_query(
        target_id="current", result_limit=2
    )
    projects = tuple(
        f"{index:08x}-0000-0000-0000-000000000000" for index in range(4096)
    )
    for key, value in list(params.items()):
        if isinstance(value, effective_tags.EffectiveTagRelation):
            params[key] = effective_tags.EffectiveTagRelation(projects, value.values)

    def execute(*args, **kwargs):
        pytest.fail("oversized authorized project scope must be rejected before SQL")

    with pytest.raises(TagReadUnavailable, match="tag_overlay_budget_exceeded"):
        effective_tags.prepare_effective_tag_params(
            execute, params, deadline=ReadDeadline.start(10000)
        )
