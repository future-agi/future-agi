"""Native candidate prefixes must prune granules, never replacement versions.

All engine work uses the isolated chdb fixture, with the production-shaped
short PRIMARY KEY. No ClickHouse socket or production credentials are used.
"""

import json
from datetime import UTC, timedelta, timezone
from types import SimpleNamespace

import pytest
from clickhouse_driver import Client

from tracer.services.clickhouse.query_builders.span_list import _unix_microseconds
from tracer.services.clickhouse.v2.query_builders.span_list import (
    SpanListQueryBuilderV2,
)
from tracer.tests.test_span_physical_identity_latest import (
    OTHER_PROJECT,
    PROJECT,
    START,
    attr_filter,
    row,
    time_filter,
)
from tracer.tests.test_span_physical_identity_latest import engine as engine


@pytest.fixture
def short_key_engine(request):
    return request.getfixturevalue("engine")


class PreviousCandidateScope(SpanListQueryBuilderV2):
    """Frozen pre-prefix helper; full query and barriers otherwise identical."""

    def _filter_candidate_scope_sql(self, identities, params):
        identities = self._normalize_span_identities(identities)
        params["candidate_span_identities"] = tuple(
            (project, trace, span, _unix_microseconds(hour), observation, service)
            for project, trace, span, hour, observation, service in identities
        )
        return """
              AND (
                  toString(project_id), trace_id, id,
                  toUnixTimestamp64Micro(toDateTime64(toStartOfHour(start_time), 6, 'UTC')),
                  observation_type, service_name
              ) IN %(candidate_span_identities)s
        """


def target(*, previous=False, **kwargs):
    cls = PreviousCandidateScope if previous else SpanListQueryBuilderV2
    return cls(
        **{
            "project_id": PROJECT,
            "filters": [time_filter(end=START + timedelta(days=2))],
            "bounded_internal_scan": True,
            **kwargs,
        }
    )


def queries(subject, candidates):
    return (
        subject.build_filter_match_query_from_seed_rows(candidates),
        subject.build_content_query(
            list(dict.fromkeys(value["id"] for value in candidates)),
            span_identities=[
                subject.bounded_filter_row_identity(value) for value in candidates
            ],
        ),
    )


def primary_indexes(explain):
    plan = json.loads("\n".join(value["explain"] for value in explain))
    found = []

    def visit(value):
        if isinstance(value, dict):
            if value.get("Type") == "PrimaryKey":
                found.append(value)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(plan)
    assert found, plan
    return found


@pytest.mark.integration
def test_short_primary_key_explain_reduces_granules_with_identical_results(
    short_key_engine, record_property
):
    execute, _ = short_key_engine
    assert execute("SELECT version() AS version")[0]["version"].startswith("25.")
    primary_key = execute("SELECT primary_key FROM system.tables WHERE name = 'spans'")[
        0
    ]["primary_key"]
    assert (
        primary_key
        == "project_id, observation_type, service_name, toStartOfHour(start_time)"
    )
    # 32 native hour ranges, each with a full 8192-row granule. Trace/id are
    # ORDER BY suffixes, not columns of the sparse primary index.
    execute(
        """INSERT INTO spans
        (project_id, observation_type, service_name, start_time, trace_id, id, _version)
        SELECT toUUID(%(project)s), 'span', 'service-a',
               fromUnixTimestamp64Micro(%(start_us)s) + toIntervalHour(intDiv(number, 8192)),
               'trace', toString(number), 1
        FROM numbers(262144)""",
        {
            "project": PROJECT,
            "start_us": _unix_microseconds(START + timedelta(minutes=20)),
        },
    )
    candidates = [row(id="0")]
    before = queries(target(previous=True), candidates)
    after = queries(target(), candidates)
    for surface, old, new in zip(("classifier", "content"), before, after, strict=True):
        old_indexes = primary_indexes(
            execute("EXPLAIN indexes = 1, json = 1 " + old[0], old[1])
        )
        new_indexes = primary_indexes(
            execute("EXPLAIN indexes = 1, json = 1 " + new[0], new[1])
        )
        old_granules = sum(index["Selected Granules"] for index in old_indexes)
        new_granules = sum(index["Selected Granules"] for index in new_indexes)
        record_property(surface + "_granules", f"{old_granules}->{new_granules}")
        assert new_granules < old_granules
        assert any(
            "toStartOfHour(start_time)" in index["Keys"] for index in new_indexes
        )
        expected = execute(*old)
        assert len(expected) == 1 and expected[0]["id"] == "0"
        assert execute(*new) == expected


@pytest.mark.unit
@pytest.mark.parametrize("offset_minutes", [0, 330, 345, -210])
def test_prefix_utc_floor_deduplication_and_driver_timezone_independence(
    offset_minutes,
):
    instant = (START + timedelta(minutes=20, microseconds=123)).replace(tzinfo=UTC)
    offset_time = instant.astimezone(timezone(timedelta(minutes=offset_minutes)))
    candidates = [
        row(id="one", start_time=offset_time),
        row(id="two", start_time=instant),
    ]
    for sql, params in queries(target(), [*candidates, candidates[0]]):
        # Floor after UTC conversion, not in the caller's half/quarter-hour
        # zone. IDs sharing a native prefix must not duplicate its IN values.
        assert params["candidate_span_primary_prefixes"] == (
            ("span", "service-a", START, "trace"),
        )
        assert len(params["candidate_span_identities"]) == 2
        assert {identity[3] for identity in params["candidate_span_identities"]} == {
            _unix_microseconds(START)
        }
        rendered = []
        for server_zone in ("UTC", "Asia/Kolkata", "America/Los_Angeles"):
            formatter = Client("invalid.invalid")  # formatting only, never connected
            formatter.connection.context.server_info = SimpleNamespace(
                get_timezone=lambda zone=server_zone: zone
            )
            rendered.append(
                formatter.substitute_params(
                    "%(candidate_span_primary_prefixes)s",
                    params,
                    formatter.connection.context,
                )
            )
        assert len(set(rendered)) == 1
        assert "'2026-08-08 12:00:00'" in rendered[0]
        assert (
            "toUnixTimestamp64Micro(toDateTime64(toStartOfHour(start_time), 6, 'UTC'))"
            in sql
        )
        assert "optimize_move_to_prewhere_if_final = 0" in sql
        assert "enable_optimize_predicate_expression_to_final_subquery = 0" in sql
        assert "query_plan_merge_expressions = 0" in sql


@pytest.mark.unit
def test_prefix_does_not_change_seed_bare_id_navigation_or_unscoped_content():
    options = {"filters": [time_filter(), attr_filter()]}
    old, new = target(previous=True, **options), target(**options)
    calls = [
        lambda builder: builder.build_filter_seed_page(
            slice_start=START, slice_end=START + timedelta(hours=1), limit=2
        ),
        lambda builder: builder.build_filter_anchor_probe(limit=2),
        lambda builder: builder.build_filter_navigation_target_query(target_id="span"),
        lambda builder: builder.build_filter_match_query(["span"]),
        lambda builder: builder.build_content_query(["span"]),
    ]
    for call in calls:
        assert call(old) == call(new)
        assert "candidate_span_primary_prefixes" not in call(new)[1]
    assert new.build_filter_match_query_from_seed_rows([]) == ("", {})
    assert new.build_content_query(["span"], span_identities=[]) == ("", {})


@pytest.mark.integration
@pytest.mark.parametrize("session_zone", ["UTC", "Asia/Kolkata", "America/Los_Angeles"])
@pytest.mark.parametrize("extended_dates", [0, 1])
def test_native_hour_set_matches_datetime_types_and_session_timezones(
    short_key_engine, session_zone, extended_dates
):
    execute, insert = short_key_engine
    insert(input="old")
    latest = START + timedelta(minutes=10, microseconds=789)
    insert(start_time=latest, _version=2, input="latest", latency_ms=None)
    offset_time = (
        (START + timedelta(minutes=20))
        .replace(tzinfo=UTC)
        .astimezone(timezone(timedelta(minutes=345)))
    )
    candidates = [row(start_time=offset_time)]
    for old, new in zip(
        queries(target(previous=True), candidates),
        queries(target(), candidates),
        strict=True,
    ):
        results = []
        for sql, params in (old, new):
            results.append(
                execute(
                    sql + ", session_timezone = %(test_zone)s, "
                    "enable_extended_results_for_datetime_functions = %(test_extended_dates)s",
                    {
                        **params,
                        "test_zone": session_zone,
                        "test_extended_dates": extended_dates,
                    },
                )
            )
        assert len(results[0]) == 1
        assert results[0][0]["start_time"] == latest
        assert str(results[0][0]["_version"]) == "2"
        assert results[1] == results[0]


@pytest.mark.integration
@pytest.mark.parametrize("deleted_project", [None, PROJECT, OTHER_PROJECT])
def test_full_key_rejects_cross_project_prefix_combinations_and_keeps_tombstones(
    short_key_engine, deleted_project
):
    execute, insert = short_key_engine
    candidates = [
        row(id="one", trace_id="trace-a"),
        row(
            project_id=OTHER_PROJECT,
            id="two",
            trace_id="trace-b",
            service_name="service-b",
            start_time=START + timedelta(hours=1, minutes=20),
        ),
    ]
    for candidate in candidates:
        insert(**candidate, input="old")
        updated = {
            **candidate,
            "start_time": candidate["start_time"] - timedelta(minutes=10),
            "_version": 2,
        }
        insert(
            **updated,
            input="latest",
            is_deleted=int(candidate["project_id"] == deleted_project),
        )
        # Both decoys pass the request project set, ID set, and native prefix.
        # Only the retained full-six-part predicate prevents their inclusion.
        insert(
            **{
                **candidate,
                "project_id": OTHER_PROJECT
                if candidate["project_id"] == PROJECT
                else PROJECT,
            },
            input="cross-project-decoy",
        )
    options = {"project_id": None, "project_ids": [PROJECT, OTHER_PROJECT]}
    old, new = target(previous=True, **options), target(**options)
    for surface, before, after in zip(
        ("classifier", "content"),
        queries(old, candidates),
        queries(new, candidates),
        strict=True,
    ):
        expected = execute(*before)
        assert len(expected) == (2 if deleted_project is None else 1)
        assert all(str(value["_version"]) == "2" for value in expected)
        actual = execute(*after)
        if surface == "content":
            # Content is joined to the already ordered page by full identity;
            # the content-only SELECT has no ORDER BY across project parts.
            key = old.bounded_filter_row_identity
            assert sorted(actual, key=key) == sorted(expected, key=key)
        else:
            assert actual == expected


@pytest.mark.integration
@pytest.mark.parametrize("new_minute", [10, 25, 40])
@pytest.mark.parametrize("operation", ["equals", "not_equals", "is_null"])
def test_prefix_preserves_corrected_time_and_latest_typed_membership(
    short_key_engine, new_minute, operation
):
    execute, insert = short_key_engine
    insert(attrs_number={"tag": 1})
    insert(
        start_time=START + timedelta(minutes=new_minute), _version=2, attrs_number={}
    )
    options = {
        "filters": [
            time_filter(START + timedelta(minutes=15), START + timedelta(minutes=30)),
            attr_filter(operation),
        ]
    }
    before = target(previous=True, **options).build_filter_match_query_from_seed_rows(
        [row()]
    )
    after = target(**options).build_filter_match_query_from_seed_rows([row()])
    expected = execute(*before)
    assert bool(expected) == (operation == "is_null" and new_minute == 25)
    assert execute(*after) == expected
