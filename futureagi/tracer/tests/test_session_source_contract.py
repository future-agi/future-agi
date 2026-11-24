"""Physical winners and compiler-owned sampling relations are distinct sources."""

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from tracer.models.eval_task import EvalTask
from tracer.selectors.eval_tasks.row_resolver import _build_sample_query
from tracer.services.clickhouse.query_builders.session_list import (
    ResolvedSpanSource,
    SessionListQueryBuilder,
)
from tracer.services.clickhouse.v2.query_builders.session_list import (
    SessionListQueryBuilderV2,
)
from tracer.services.eval_tasks.sampling.query import SamplingQuery, stream_query
from tracer.services.eval_tasks.sampling.service import task_policy
from tracer.tests._ch_seed import _get_ch_client


@pytest.mark.parametrize(
    "builder_class", [SessionListQueryBuilder, SessionListQueryBuilderV2]
)
@pytest.mark.parametrize("include_usage", [False, True])
@pytest.mark.parametrize("table", ["spans", "fixture_spans", "sampling_source"])
def test_table_name_alone_keeps_physical_final(builder_class, include_usage, table):
    builder = builder_class(project_id=str(uuid.uuid4()))
    builder.TABLE = table
    sql = builder._session_from_where(
        builder.params,
        time_where="",
        filter_fragment="",
        scope_usage_to_request_window=True,
        include_usage=include_usage,
    )
    assert f"FROM {table} FINAL" in sql
    if include_usage:
        assert f"FROM {table} PREWHERE" in " ".join(sql.split())
        assert "FROM candidate_member_identities" in sql


@pytest.mark.parametrize("include_usage", [False, True])
def test_resolved_source_is_explicit_and_cannot_follow_table_override(include_usage):
    builder = SessionListQueryBuilderV2(
        project_id=str(uuid.uuid4()), resolved_source=ResolvedSpanSource.SAMPLING
    )
    kwargs = {
        "time_where": "",
        "filter_fragment": "",
        "scope_usage_to_request_window": True,
        "include_usage": include_usage,
    }
    sql = builder._session_from_where(builder.params, **kwargs)
    assert "FROM sampling_source" in sql
    assert "sampling_source FINAL" not in sql
    assert "sampling_source PREWHERE" not in " ".join(sql.split())
    assert "FROM candidate_member_identities" not in sql
    assert "trace_session_id_remap FINAL" in sql
    builder.TABLE = "spans"
    with pytest.raises(ValueError, match="does not match TABLE"):
        builder._session_from_where(builder.params, **kwargs)


@pytest.mark.parametrize(
    "untrusted",
    [True, False, "sampling_source", "SAMPLING", {"value": "sampling_source"}],
)
def test_untrusted_values_cannot_claim_resolved_state(untrusted):
    with pytest.raises(ValueError, match="Unsupported resolved"):
        SessionListQueryBuilderV2(
            project_id=str(uuid.uuid4()), resolved_source=untrusted
        )
    with pytest.raises(ValueError, match="Unsupported resolved"):
        _build_sample_query(
            project_id=str(uuid.uuid4()),
            row_type="sessions",
            salt="contract",
            sampling_rate=100,
            filters={},
            limit=None,
            resolved_source=untrusted,
        )


def test_adapter_preserves_named_source_validation_and_physical_default():
    args = {
        "project_id": str(uuid.uuid4()),
        "row_type": "sessions",
        "salt": "contract",
        "sampling_rate": 100,
        "filters": {},
        "limit": None,
    }
    assert "FROM spans FINAL" in _build_sample_query(**args)[0]
    assert (
        "FROM sampling_source FINAL"
        in _build_sample_query(**args, source_table="sampling_source")[0]
    )
    assert (
        "FROM sampling_source FINAL"
        not in _build_sample_query(**args, resolved_source=ResolvedSpanSource.SAMPLING)[
            0
        ]
    )
    with pytest.raises(ValueError, match="Unsupported internal"):
        _build_sample_query(**args, source_table="arbitrary_cte")
    with pytest.raises(ValueError, match="Unsupported resolved"):
        _build_sample_query(
            **args,
            source_table="sampling_source",
            resolved_source=ResolvedSpanSource.SAMPLING,
        )


@pytest.mark.django_db
@pytest.mark.integration
@pytest.mark.parametrize("resolved", [False, True], ids=["physical", "resolved"])
@pytest.mark.parametrize("org_scope", [False, True], ids=["project", "organization"])
def test_current_session_sources_preserve_winners_counts_and_pages(
    project, ch_seed, resolved, org_scope
):
    """Unmerged corrections must not create stale sessions, even on counts/pages."""
    now = timezone.now().replace(microsecond=0)
    start, end = now - timedelta(hours=1), now - timedelta(minutes=1)
    event = now - timedelta(minutes=5)
    stale, current, other, dead, foreign = (uuid.uuid4() for _ in range(5))
    foreign_project = uuid.uuid4()
    trace, span = uuid.uuid4().hex, uuid.uuid4().hex[:16]
    version = int((now - timedelta(minutes=2)).timestamp()) * 1_000_000_000
    columns = [
        "project_id",
        "trace_id",
        "id",
        "trace_session_id",
        "parent_span_id",
        "start_time",
        "end_time",
        "observation_type",
        "service_name",
        "_version",
        "is_deleted",
        "total_tokens",
        "cost",
    ]

    def row(
        session,
        *,
        project_id=project.id,
        identity=None,
        revision=version,
        deleted=0,
    ):
        trace_id, span_id = identity or (trace, span)
        row_event = event - timedelta(seconds=1) if session == other else event
        return [
            project_id,
            trace_id,
            span_id,
            session,
            "",
            row_event,
            row_event + timedelta(seconds=1),
            "llm",
            "source-contract",
            revision,
            deleted,
            7,
            0.25,
        ]

    dead_identity = (uuid.uuid4().hex, uuid.uuid4().hex[:16])
    client = _get_ch_client()
    client.command("SYSTEM STOP MERGES spans")
    try:
        client.insert(
            "spans",
            [row(stale), row(dead, identity=dead_identity)],
            column_names=columns,
        )
        client.insert(
            "spans",
            [
                row(current, revision=version + 1),
                row(
                    dead,
                    identity=dead_identity,
                    revision=version + 1,
                    deleted=1,
                ),
                row(other, identity=(uuid.uuid4().hex, uuid.uuid4().hex[:16])),
                row(foreign, project_id=foreign_project),
            ],
            column_names=columns,
        )
        # Establish a semantic mutation control: uncollapsed physical state really
        # contains the obsolete membership; no background merge can hide it.
        assert client.query(
            "SELECT count() FROM spans WHERE project_id = {project:UUID} AND id = {span:String}",
            parameters={"project": str(project.id), "span": span},
        ).result_rows == [(2,)]
        task = EvalTask(
            project=project,
            row_type="sessions",
            # The page below projects usage. A sampling snapshot projects only
            # the columns its task consumes, so request a usage-capable source.
            filters={
                "filters": [
                    {
                        "column_id": "total_tokens",
                        "filter_config": {
                            "filter_type": "number",
                            "filter_op": "greater_than",
                            "filter_value": 0,
                        },
                    }
                ]
            },
            sampling_policy={
                "version": 1,
                "mode": "closed",
                "sampling_unit": "session",
                "window": {"start": start.isoformat(), "end": end.isoformat()},
                "default": {"rate": 100},
            },
        )
        policy, version_id = task_policy(task, now=now)
        sampling = SamplingQuery(task, policy, version_id, now + timedelta(seconds=30))
        source = sampling._source() if resolved else ""
        filters = [
            {
                "column_id": "start_time",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": [start, end],
                },
            }
        ]
        scope = (
            {"project_ids": [str(project.id)]}
            if org_scope
            else {"project_id": str(project.id)}
        )

        def builder(page_number=0):
            return SessionListQueryBuilderV2(
                **scope,
                filters=filters,
                page_size=1,
                page_number=page_number,
                resolved_source=ResolvedSpanSource.SAMPLING if resolved else None,
            )

        def execute(sql_params):
            sql, params = sql_params
            return [
                row
                for block in stream_query(
                    f"{source} {sql}" if source else sql,
                    {**sampling.params, **params},
                )
                for row in block
            ]

        # ID/count need no usage filter; pages project the child-aware totals.
        # All three must see exactly the same latest-live population.
        ids = execute(builder().build_id_query())
        expected = {str(current), str(other)}
        assert {str(row[0]) for row in ids} == expected
        assert len(ids) == 2
        assert execute(builder().build_count_query()) == [(2,)]
        pages = [execute(builder(index).build()) for index in range(3)]
        assert [len(page) for page in pages] == [2, 1, 0]  # page plus has_more sentinel
        assert pages[0][1] == pages[1][0]
        assert {str(page[0][0]) for page in pages[:2]} == expected
    finally:
        client.command("SYSTEM START MERGES spans")
        client.close()
