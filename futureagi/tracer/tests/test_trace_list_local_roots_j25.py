"""Project-local roots in the actual Observe acquisition/replay SQL (J25.OSS-002).

Run with the existing CH25 integration harness. No collector or trace identity
row is needed: downstream SDKs export only spans with nonempty remote parents.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest

from tracer.services.clickhouse.v2.query_builders.observe_usage import (
    ObserveTraceListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)
from tracer.tests.test_openrouter_broadcast_list_rollups_ch25 import (
    _REMAP_DDL,
    _SPANS_DDL,
    _TRACES_DDL,
    _insert,
)
from tracer.tests.test_openrouter_broadcast_list_rollups_ch25 import ch as ch

START = datetime(2026, 10, 26, tzinfo=UTC)
END = START + timedelta(days=1)
PROJECT = str(UUID(int=101))
OTHER_PROJECT = str(UUID(int=102))
TRACE = str(UUID(int=201))
OTHER_TRACE = str(UUID(int=202))
SESSION = str(UUID(int=301))


@pytest.fixture(autouse=True, scope="session")
def _drop_legacy_ch_spans_mvs():
    yield


@pytest.fixture(autouse=True, scope="session")
def _ensure_test_score_tenant_column():
    yield


def _span(span_id="local", parent="remote", **changes):
    return {
        "project_id": PROJECT,
        "observation_type": "span",
        "service_name": "downstream",
        "start_time": START + timedelta(minutes=10),
        "trace_id": TRACE,
        "id": span_id,
        "parent_span_id": parent,
        "name": span_id,
        "end_time": START + timedelta(minutes=11),
        "trace_session_id": SESSION,
        "status": "OK",
        "model": "",
        "provider": "",
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cost": 0.0,
        "input": "local request",
        "output": "local response",
        "is_deleted": 0,
        "_version": 1,
        **changes,
    }


def _builder(cls=ObserveTraceListQueryBuilderV2, *, session=False, org=False):
    filters = [
        {
            "column_id": "created_at",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [START.isoformat(), END.isoformat()],
            },
        }
    ]
    if session:
        filters.append(
            {
                "column_id": "trace_session_id",
                "filter_config": {
                    "col_type": "NORMAL",
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": SESSION,
                },
            }
        )
    scope = (
        {"project_ids": [PROJECT, OTHER_PROJECT]} if org else {"project_id": PROJECT}
    )
    return cls(**scope, filters=filters, page_size=25)


@pytest.fixture
def spans(request):
    connection = request.getfixturevalue("ch")
    for ddl in (_SPANS_DDL, _TRACES_DDL, _REMAP_DDL):
        connection.execute(ddl)
    connection.execute("SYSTEM STOP MERGES spans")
    _insert(
        connection,
        [
            _span(),
            _span("child", "local", start_time=START + timedelta(minutes=12)),
        ],
    )
    return SimpleNamespace(ch=connection)


def _seed(builder, lane):
    if lane == "initial":
        return builder.build()
    method = (
        builder.build_filter_ordered_seed_page
        if lane == "ordered"
        else builder.build_filter_seed_page
    )
    # Slice bounds use the parser's UTC-naive convention, not the aware fixtures.
    start, end = builder.parse_time_range(builder.filters)
    return method(slice_start=start, slice_end=end, limit=100)


def _typed_roots(rows):
    # The native driver returns datetimes; the optional chdb fixture returns
    # JSONEachRow strings. Restore the driver's types before identity replay.
    for row in rows:
        for field in ("start_time", "_root_start_hour"):
            if isinstance(row.get(field), str):
                row[field] = datetime.fromisoformat(row[field]).replace(tzinfo=UTC)
        if isinstance(row.get("_root_version"), str):
            row["_root_version"] = int(row["_root_version"])
    return rows


def _page(spans, builder):
    seeds = spans.ch.execute(*_seed(builder, "ordered"))
    if not seeds:
        return [], []
    rows = _typed_roots(
        spans.ch.execute(
            *builder.build_filter_match_query([row["trace_id"] for row in seeds])
        )
    )
    if not rows:
        return [], []
    hydrated = _typed_roots(
        spans.ch.execute(*builder.build_filter_page_hydration_query(rows))
    )
    contents = spans.ch.execute(
        *builder.build_content_query(
            [row["trace_id"] for row in rows],
            root_identities=builder.content_root_identities_for_rows(rows),
            include_trace_rollup=True,
        )
    )
    return hydrated, contents


@pytest.mark.integration
@pytest.mark.parametrize("lane", ["initial", "ordered", "seed"])
def test_remote_parent_trace_is_acquired(spans, lane):
    rows = spans.ch.execute(*_seed(_builder(), lane))
    assert [row["trace_id"] for row in rows] == [TRACE]
    if lane != "initial":
        assert rows[0]["root_span_id"] == "local"


@pytest.mark.integration
def test_remote_parent_root_time_discovery(spans):
    builder = _builder()
    start, end = builder.parse_time_range(builder.filters)
    rows = spans.ch.execute(
        *builder.build_filter_root_time_discovery_query(
            slice_start=start,
            slice_end=end,
        )
    )
    assert rows == [
        {
            "newest_raw_root_us": int(
                (START + timedelta(minutes=10)).timestamp() * 1_000_000
            ),
        }
    ]


@pytest.mark.integration
@pytest.mark.parametrize("session", [False, True], ids=["project", "session"])
def test_remote_parent_trace_survives_classification_and_replay(spans, session):
    rows, contents = _page(spans, _builder(session=session))
    assert [(row["trace_id"], row["root_span_id"]) for row in rows] == [
        (TRACE, "local")
    ]
    assert len(contents) == 1
    assert (contents[0]["input"], contents[0]["output"]) == (
        "local request",
        "local response",
    )
    assert contents[0]["trace_tags"] == "[]"


@pytest.mark.integration
@pytest.mark.parametrize("count_only", [False, True], ids=["page", "count"])
def test_remote_parent_trace_usage_sort(spans, count_only):
    rows = spans.ch.execute(
        *_builder().build_usage_sorted_query(
            [{"column_id": "cost", "direction": "asc"}],
            count_only=count_only,
        )
    )
    if count_only:
        assert rows == [{"total": 1}]
    else:
        assert [(row["trace_id"], row["root_span_id"]) for row in rows] == [
            (TRACE, "local")
        ]


@pytest.mark.integration
@pytest.mark.parametrize("org", [False, True], ids=["project", "organization"])
def test_parent_lookup_is_project_and_trace_scoped(spans, org):
    _insert(
        spans.ch,
        [
            _span("remote", "", project_id=OTHER_PROJECT),
            _span("remote", "", trace_id=OTHER_TRACE),
        ],
    )
    rows, _ = _page(spans, _builder(org=org))
    assert [
        (row["trace_id"], row["root_span_id"])
        for row in rows
        if str(row["project_id"]) == PROJECT and row["trace_id"] == TRACE
    ] == [
        (TRACE, "local"),
    ]


@pytest.mark.integration
def test_parent_arrival_reconciles_to_one_root(spans):
    builder = _builder()
    rows, _ = _page(spans, builder)
    assert [row["root_span_id"] for row in rows] == ["local"]
    old_identities = builder.content_root_identities_for_rows(rows)
    _insert(
        spans.ch,
        [
            _span(
                "remote",
                "",
                service_name="upstream",
                start_time=START + timedelta(minutes=5),
            )
        ],
    )
    rows, _ = _page(spans, builder)
    assert [row["root_span_id"] for row in rows] == ["remote"]
    assert (
        spans.ch.execute(
            *builder.build_content_query(
                [TRACE],
                root_identities=old_identities,
            )
        )
        == []
    )
    assert [
        row["root_span_id"]
        for row in spans.ch.execute(
            *builder.build_usage_sorted_query(
                [{"column_id": "cost", "direction": "asc"}],
            )
        )
    ] == ["remote"]


@pytest.mark.integration
def test_present_parent_outside_window_is_not_an_orphan(spans):
    _insert(
        spans.ch,
        [
            _span(
                "remote",
                "",
                service_name="upstream",
                start_time=START - timedelta(days=3),
            )
        ],
    )
    assert _page(spans, _builder()) == ([], [])
    assert (
        spans.ch.execute(
            *_builder().build_usage_sorted_query(
                [{"column_id": "cost", "direction": "asc"}],
            )
        )
        == []
    )


@pytest.mark.integration
def test_latest_parent_tombstone_and_reappearance(spans):
    parent = _span("remote", "", service_name="upstream")
    _insert(spans.ch, [parent, {**parent, "is_deleted": 1, "_version": 2}])
    rows, _ = _page(spans, _builder())
    assert [row["root_span_id"] for row in rows] == ["local"]
    _insert(spans.ch, [{**parent, "_version": 3}])
    rows, _ = _page(spans, _builder())
    assert [row["root_span_id"] for row in rows] == ["remote"]


@pytest.mark.integration
def test_multiple_local_roots_still_classify_once(spans):
    _insert(
        spans.ch,
        [
            _span(
                "sibling",
                "other-remote",
                start_time=START + timedelta(minutes=15),
            )
        ],
    )
    rows, _ = _page(spans, _builder())
    assert [(row["trace_id"], row["root_span_id"]) for row in rows] == [
        (TRACE, "sibling")
    ]


@pytest.mark.integration
def test_parentless_control_is_byte_identical(spans):
    # A genuinely parentless control, not a formerly partial trace with old
    # physical versions that the raw acquisition superset may also return.
    spans.ch.execute("TRUNCATE TABLE spans")
    _insert(spans.ch, [_span(parent=""), _span("child", "local")])
    assert _page(spans, _builder()) == _page(spans, _builder(TraceListQueryBuilderV2))
    assert spans.ch.execute(*_builder().build()) == spans.ch.execute(
        *_builder(TraceListQueryBuilderV2).build()
    )


@pytest.mark.unit
def test_parent_absence_proof_is_bounded_and_replacement_aware():
    builder = _builder()
    sql, params = builder.build_filter_match_query([TRACE])
    assert "NOT IN" in sql
    assert "argMax(is_deleted, _version) = 0" in sql
    assert "toString(project_id), trace_id, id" in sql
    assert "toString(project_id), trace_id, parent_span_id" in sql
    assert "trace_id IN %(candidate_trace_ids)s" in sql
    assert params["candidate_trace_ids"] == (TRACE,)
    # Materialize the membership expression once, not once per displayed column.
    assert sql.count("AS _is_trace_root") == 1
    assert sql.count("HAVING argMax(is_deleted, _version) = 0") == 1
    parent_lookup = sql.split("NOT IN (", 1)[1].split(
        "HAVING argMax(is_deleted, _version) = 0",
        1,
    )[0]
    assert "LIMIT" not in parent_lookup
    assert "candidate_start_date" not in parent_lookup
    assert "project_version_id" not in parent_lookup


@pytest.mark.unit
def test_standalone_count_uses_local_roots_not_the_global_root_rollup():
    sql, params = _builder().build_count_query()
    assert "trace_count_rollup" not in sql
    assert "NOT IN" in sql
    assert params["start_date"] == START.replace(tzinfo=None)
    assert params["end_date"] == END.replace(tzinfo=None)


@pytest.mark.unit
def test_non_observe_root_contract_is_unchanged():
    legacy_sql, _ = _builder(TraceListQueryBuilderV2).build_filter_match_query([TRACE])
    assert "NOT IN" not in legacy_sql
    assert "latest_parent_span_id IS NULL" in legacy_sql
