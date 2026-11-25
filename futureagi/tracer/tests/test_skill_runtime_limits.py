"""Exact skill evidence reads at the SQL, row and request-window boundaries."""

from datetime import UTC, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.runtime_usage import read_runtime_statement
from tracer.services.clickhouse.query_service import QueryResult
from tracer.services.clickhouse.read_budget import ReadDeadline
from tracer.services.clickhouse.v2.query_builders.skill_runtime import (
    RuntimeReadSession,
    SkillRuntimeQueryBuilder,
)
from tracer.tests import test_skill_filters as evidence
from tracer.tests.test_bounded_trace_filter_reads import (
    END as PAGE_END,
)
from tracer.tests.test_bounded_trace_filter_reads import (
    PROJECT_ID,
    _attribute_filter,
    _call_observe_trace_list_with_bounded_page,
    _complete_empty_page,
    _render_driver_sql,
    _short_time_filter,
)

skill_query_client = evidence.skill_query_client
MAX_SQL_BYTES = 256 * 1024


class _NativeReads:
    """Send production statements through clickhouse-driver under the SQL limit."""

    def __init__(self, native, *, per_query_settings):
        self.native = native
        self.supports_per_query_read_settings = per_query_settings
        self.calls = []

    def execute_ch_query(self, sql, params, *, settings, **kwargs):
        options = (settings or {}) if self.supports_per_query_read_settings else {}
        on_wire = (
            sql
            if options.get("server_side_params")
            else _render_driver_sql(sql, params)
        )
        assert len(on_wire.encode("utf-8")) < MAX_SQL_BYTES
        rows, columns = self.native.execute(
            sql,
            params,
            with_column_types=True,
            settings={**options, "max_query_size": MAX_SQL_BYTES},
        )
        self.calls.append(params)
        return QueryResult.from_clickhouse_rows(rows, columns, 0)


@pytest.mark.integration
def test_usage_replay_4000_verified_members_under_native_sql_limit(skill_query_client):
    from conftest import _ch_test_native_client

    projects = [str(uuid4()), str(uuid4())]
    loads = [evidence.span_row(projects[i % 2], f"usage-{i}") for i in range(4000)]
    # Same trace ID, another authorized project, but *not* a cohort member.
    decoy = {
        **loads[0],
        "project_id": projects[1],
        "name": "request",
        "span_attributes": {},
    }
    evidence.insert_rows(skill_query_client, [*loads, decoy])
    query = SkillRuntimeQueryBuilder(
        project_ids=projects, start=evidence.START, end=evidence.END
    )
    expected = {(row["project_id"], row["trace_id"]) for row in loads}

    with _ch_test_native_client(database=skill_query_client.database) as native:
        for per_query_settings in (True, False):
            service = _NativeReads(native, per_query_settings=per_query_settings)
            session = RuntimeReadSession(service, deadline=ReadDeadline.start(60_000))
            winners = read_runtime_statement(
                session,
                query.build_usage_loads(
                    identities=[(evidence.SKILL, evidence.VERSION, evidence.DIGEST)],
                    skill_names=["refund"],
                ),
                max_rows=20_000,
            )
            members = [(str(row["project_id"]), row["trace_id"]) for row in winners]
            assert len(members) == len(expected) and set(members) == expected
            spans = read_runtime_statement(
                session, query.build_usage_spans(members=members), max_rows=20_000
            )
            assert len(spans) == len(expected)
            assert {
                (str(row["project_id"]), row["trace_id"]) for row in spans
            } == expected


@pytest.mark.parametrize("per_query_settings", [True, False])
def test_replay_uses_one_cumulative_fact_sentinel(per_query_settings):
    project = str(uuid4())
    query = SkillRuntimeQueryBuilder(
        project_ids=[project], start=evidence.START, end=evidence.END
    )
    members = [(project, str(uuid4())) for _ in range(4000)]
    calls = []

    class Service:
        supports_per_query_read_settings = per_query_settings

        def execute_ch_query(self, sql, params, *, server_execution_cap_ms, **kwargs):
            calls.append(server_execution_cap_ms)
            assert len(calls) <= 3  # never read beyond the rejection sentinel
            rows = [{}] * (10_000 if len(calls) <= 2 else 1)
            return SimpleNamespace(data=rows, row_count=len(rows))

    session = RuntimeReadSession(Service(), deadline=ReadDeadline.start(1000))
    with pytest.raises(SkillsHubError) as error:
        read_runtime_statement(
            session, query.build_usage_spans(members=members), max_rows=20_000
        )
    assert error.value.code == "runtime_read_too_large"
    assert len(calls) == 3 and 0 < calls[-1] <= calls[0] <= 1000


@pytest.mark.integration
@pytest.mark.parametrize("per_query_settings", [True, False])
def test_native_eval_replay_bounds_both_identifier_sets(
    skill_query_client, settings, per_query_settings
):
    from conftest import _ch_test_native_client

    settings.CH25_EVAL_LOGGER_TABLE = "tracer_eval_logger_v2"
    project = str(uuid4())
    traces = sorted(str(uuid4()) for _ in range(7000))
    configs = sorted(str(uuid4()) for _ in range(2400))
    # Require cross-batch combinations in both directions. One-sided matches
    # must not leak from an unrequested trace or config.
    expected = [(configs[0], traces[-1], 0.25), (configs[-1], traces[0], 0.75)]
    decoys = [(str(uuid4()), traces[0], 1.0), (configs[0], str(uuid4()), 1.0)]
    skill_query_client.insert(
        "tracer_eval_logger_v2",
        [
            (uuid4(), cfg, trace, score, evidence.START, evidence.START)
            for cfg, trace, score in [*expected, *decoys]
        ],
        column_names=[
            "id",
            "custom_eval_config_id",
            "trace_id",
            "output_float",
            "created_at",
            "updated_at",
        ],
    )
    builder = SkillRuntimeQueryBuilder(
        project_ids=[project], start=evidence.START, end=evidence.END
    )
    with _ch_test_native_client(database=skill_query_client.database) as native:
        service = _NativeReads(native, per_query_settings=per_query_settings)
        session = RuntimeReadSession(service, deadline=ReadDeadline.start(60_000))
        rows = read_runtime_statement(
            session,
            builder.build_usage_evals(
                config_ids=configs, members=[(project, trace) for trace in traces]
            ),
            max_rows=20_000,
        )
    assert (
        sorted((r["config_id"], r["trace_id"], r["output_float"]) for r in rows)
        == expected
    )

    # Check the full Cartesian product without materializing sixteen million pairs.
    observed = [(tuple(p["config_ids"]), tuple(p["trace_ids"])) for p in service.calls]
    config_batches, trace_batches = (set(dim) for dim in zip(*observed, strict=True))
    assert sorted(v for batch in config_batches for v in batch) == configs
    assert sorted(v for batch in trace_batches for v in batch) == traces
    assert (
        len(observed) == len(set(observed)) == len(config_batches) * len(trace_batches)
    )


@pytest.mark.integration
def test_page_zero_skips_excluded_winners_but_checks_the_next_key_page(
    skill_query_client,
):
    when = (PAGE_END - timedelta(minutes=2)).replace(tzinfo=UTC, microsecond=123456)
    until = when + timedelta(microseconds=1)
    filters = [
        _short_time_filter(),
        {
            "column_id": "start_time",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "not_between",
                "filter_value": [when.isoformat(), until.isoformat()],
            },
        },
        _attribute_filter("final_status", "Rejected"),
    ]
    rows = [
        evidence.span_row(PROJECT_ID, "probe-pages", id=f"{i:016x}", start_time=when)
        for i in range(1024)
    ]
    evidence.insert_rows(skill_query_client, rows)

    def execute(sql, params, *, settings, **kwargs):
        result = skill_query_client.query(sql, parameters=params, settings=settings)
        data = list(result.named_results())
        return QueryResult(data, len(data), "clickhouse", 0.0)

    def skill_links_exist():
        # An empty root page needs no other CH enrichments. Execute the actual
        # project-wide probe, whether it is one statement or a key/winner loop.
        response, *_ = _call_observe_trace_list_with_bounded_page(
            bounded_page=_complete_empty_page(),
            validated_data={
                "filters": filters,
                "page_number": 0,
                "page_size": 1,
                "include_skill_links": True,
            },
            analytics=SimpleNamespace(execute_ch_query=execute),
        )
        assert response[0] == "ok"
        return response[1]["metadata"]["skill_links_exist"]

    assert skill_links_exist() is False
    # The upper end of not_between is allowed; its key sorts after the entire
    # excluded page. An empty winner page must not be interpreted as EOF.
    evidence.insert_rows(
        skill_query_client,
        [
            evidence.span_row(
                PROJECT_ID, "probe-pages", id=f"{1024:016x}", start_time=until
            )
        ],
    )
    assert skill_links_exist() is True
