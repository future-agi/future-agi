"""Contracts for the bounded session seed's optional any-span witness gate.

The gate narrows which sessions one seed statement acquires; it must never
change which sessions the page publishes, which lane a filter shape routes to,
or - while it is switched off - a single byte of the emitted SQL.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest
from django.test import override_settings

from tracer.selectors.trace_filter_reads import read_bounded_filter_page
from tracer.services.clickhouse.list_cursor import (
    decode_list_cursor,
    encode_list_cursor,
    pin_filter_seed_witness_slack,
    read_filter_seed_witness_slack,
)
from tracer.services.clickhouse.query_builders.session_list import (
    SessionListQueryBuilder,
)
from tracer.services.clickhouse.query_service import QueryResult
from tracer.services.clickhouse.v2.query_builders.session_list import (
    SessionListQueryBuilderV2,
)

PROJECT_ID = "00000000-0000-4000-8000-000000000101"
END = datetime(2026, 7, 31, 7, 0)
START = END - timedelta(days=7)
SLICE_START = END - timedelta(hours=4)
SESSION_A = "00000000-0000-4000-8000-0000000000a1"
SESSION_B = "00000000-0000-4000-8000-0000000000b2"
SESSION_C = "00000000-0000-4000-8000-0000000000c3"


def _time_filter() -> dict:
    return {
        "column_id": "created_at",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [START.isoformat(), END.isoformat()],
        },
    }


def _attribute_filter(
    key: str = "account_id",
    value: Any = ("acct-1", "acct-2"),
    *,
    filter_type: str = "text",
    operation: str = "in",
) -> dict:
    return {
        "column_id": key,
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": filter_type,
            "filter_op": operation,
            "filter_value": list(value) if isinstance(value, tuple) else value,
        },
    }


def _session_field_filter() -> dict:
    return {
        "column_id": "total_cost",
        "filter_config": {
            "filter_type": "number",
            "filter_op": "greater_than",
            "filter_value": 0,
        },
    }


def _builder(*filters: dict, klass=SessionListQueryBuilderV2, page_size: int = 25):
    return klass(
        project_id=PROJECT_ID,
        filters=[_time_filter(), *filters],
        page_size=page_size,
        bounded_internal_scan=True,
    )


def _seed(builder, **kwargs) -> tuple[str, dict[str, Any]]:
    return builder.build_filter_seed_page(
        slice_start=SLICE_START,
        slice_end=END,
        limit=31,
        **kwargs,
    )


@pytest.mark.unit
def test_the_seed_carries_no_witness_until_the_setting_turns_the_lane_on() -> None:
    """The shipped default keeps the predicate-free seed, byte for byte."""

    sql, params = _seed(_builder(_attribute_filter()))

    assert "witness_spans" not in sql
    assert "attrs_string" not in sql
    assert not [key for key in params if key.startswith("filter_witness")]
    assert not [key for key in params if key.startswith("latest_filter_")]


@pytest.mark.unit
def test_the_switched_on_seed_gates_on_an_hour_aligned_witness_envelope() -> None:
    """One membership test on the session, bounded by the slice plus slack."""

    with override_settings(SESSION_LIST_FILTER_SEED_WITNESS_SLACK_HOURS=2):
        builder = _builder(_attribute_filter())
        sql, params = _seed(builder)

        assert builder.filter_seed_witness_slack_hours() == 2
    assert "AND seed_spans.trace_session_id IN (" in sql
    assert "FROM spans AS witness_spans" in sql
    # The gate is a membership test on the session, never a predicate on the
    # seed's own root rows: a qualifying attribute may live on any child span.
    assert "WHERE isNotNull(witness_spans.trace_session_id)" in sql
    assert (
        "witness_spans.start_time >= "
        "fromUnixTimestamp64Micro(%(filter_witness_start_us)s)" in sql
    )
    assert (
        "witness_spans.start_time < "
        "fromUnixTimestamp64Micro(%(filter_witness_end_us)s)" in sql
    )
    # Physical versions and tombstones must still witness a candidate.
    assert "witness_spans.is_deleted" not in sql
    assert "witness_spans._peerdb_is_deleted" not in sql
    assert params["filter_witness_start"] == SLICE_START - timedelta(hours=2)
    assert params["filter_witness_end"] == END + timedelta(hours=2)
    assert params["latest_filter_param_0"] == ("acct-1", "acct-2")


@pytest.mark.unit
def test_a_witness_envelope_snaps_to_whole_hours_around_the_slice() -> None:
    """The bound prunes on the immutable ``toStartOfHour`` key prefix."""

    ragged_start = SLICE_START + timedelta(minutes=17, seconds=5)
    ragged_end = END - timedelta(minutes=42)
    with override_settings(SESSION_LIST_FILTER_SEED_WITNESS_SLACK_HOURS=1):
        _sql, params = _builder(_attribute_filter()).build_filter_seed_page(
            slice_start=ragged_start,
            slice_end=ragged_end,
            limit=31,
        )

    # 03:17:05 floors to 03:00 and 06:18 ceils to 07:00 before the slack.
    assert params["filter_witness_start"] == SLICE_START - timedelta(hours=1)
    assert params["filter_witness_end"] == END + timedelta(hours=1)
    assert params["filter_witness_start"].minute == 0
    assert params["filter_witness_end"].minute == 0


@pytest.mark.unit
@pytest.mark.parametrize(
    ("extra_filters", "bounded_lane"),
    [
        ((), False),
        ((_attribute_filter(),), True),
        ((_attribute_filter(), _session_field_filter()), False),
        ((_session_field_filter(),), False),
        (
            (
                _attribute_filter(
                    "retry_count", 3, filter_type="number", operation="equals"
                ),
            ),
            False,
        ),
        ((_attribute_filter(operation="not_in"),), False),
    ],
)
def test_the_gate_changes_no_route_and_only_speaks_for_the_bounded_lane(
    extra_filters: tuple[dict, ...], bounded_lane: bool
) -> None:
    """Routing is decided before the gate and is identical on both settings."""

    routes = {}
    for slack in (-1, 1):
        with override_settings(SESSION_LIST_FILTER_SEED_WITNESS_SLACK_HOURS=slack):
            builder = _builder(*extra_filters)
            routes[slack] = (
                builder.prefers_bounded_filter_page(),
                builder.supports_candidate_cursor_page(),
                builder.supports_candidate_first_page(),
            )
            slack_hours = builder.filter_seed_witness_slack_hours()
        assert slack_hours == (1 if bounded_lane and slack == 1 else None)

    assert routes[-1] == routes[1]
    assert routes[1][0] is bounded_lane


@pytest.mark.unit
def test_the_sampled_internal_seed_keeps_its_canonical_id_contract() -> None:
    """Sampling hashes the public session ID; it never acquires a witness."""

    with override_settings(SESSION_LIST_FILTER_SEED_WITNESS_SLACK_HOURS=1):
        builder = _builder(_attribute_filter())
        builder._bounded_sampling_rate = 10.0
        builder._bounded_sampling_salt = "salt"
        sql, _params = _seed(builder)

        assert builder.filter_seed_witness_slack_hours() is None
    assert "witness_spans" not in sql


@pytest.mark.unit
def test_a_continuation_finishes_on_the_slack_its_first_hop_was_minted_with() -> None:
    """An operator turning the knob mid-chain cannot move the boundary."""

    with override_settings(SESSION_LIST_FILTER_SEED_WITNESS_SLACK_HOURS=3):
        first_hop = _builder(_attribute_filter())
        token = encode_list_cursor(
            resource="observe_sessions",
            scope={"project_ids": [PROJECT_ID]},
            query={"filters": []},
            page_size=25,
            window_start=START,
            window_end=END,
            seen_rows=0,
            order=(END, SESSION_A),
            witness_slack_hours=read_filter_seed_witness_slack(first_hop),
        )

    cursor = decode_list_cursor(
        token,
        resource="observe_sessions",
        scope={"project_ids": [PROJECT_ID]},
        query={"filters": []},
        page_size=25,
    )
    assert cursor.witness_slack_hours == 3

    # The knob is turned all the way off between the two hops.
    with override_settings(SESSION_LIST_FILTER_SEED_WITNESS_SLACK_HOURS=-1):
        second_hop = _builder(_attribute_filter())
        pin_filter_seed_witness_slack(second_hop, cursor)

        assert second_hop.filter_seed_witness_slack_hours() == 3
        sql, params = _seed(second_hop)

    assert "FROM spans AS witness_spans" in sql
    assert params["filter_witness_start"] == SLICE_START - timedelta(hours=3)


@pytest.mark.unit
def test_a_legacy_token_carries_no_slack_and_resolves_to_the_setting() -> None:
    """Tokens minted before the field keep behaving exactly as they did."""

    token = encode_list_cursor(
        resource="observe_sessions",
        scope={"project_ids": [PROJECT_ID]},
        query={"filters": []},
        page_size=25,
        window_start=START,
        window_end=END,
        seen_rows=0,
        order=(END, SESSION_A),
        witness_slack_hours=read_filter_seed_witness_slack(
            _builder(_attribute_filter())
        ),
    )
    cursor = decode_list_cursor(
        token,
        resource="observe_sessions",
        scope={"project_ids": [PROJECT_ID]},
        query={"filters": []},
        page_size=25,
    )

    assert cursor.witness_slack_hours is None

    with override_settings(SESSION_LIST_FILTER_SEED_WITNESS_SLACK_HOURS=5):
        builder = _builder(_attribute_filter())
        pin_filter_seed_witness_slack(builder, cursor)

        assert builder.filter_seed_witness_slack_hours() == 5


@pytest.mark.unit
def test_the_default_page_mints_a_cursor_with_no_slack_field() -> None:
    """The candidate lane has no envelope, so its tokens are unchanged."""

    with override_settings(SESSION_LIST_FILTER_SEED_WITNESS_SLACK_HOURS=1):
        assert read_filter_seed_witness_slack(_builder()) is None


@pytest.mark.unit
def test_the_pin_rejects_values_the_cursor_codec_could_not_have_minted() -> None:
    builder = _builder(_attribute_filter())

    for rejected in (True, 1.5, -1, 169):
        with pytest.raises(ValueError):
            builder.pin_filter_seed_witness_slack_hours(rejected)


@pytest.mark.unit
def test_the_v1_builder_answers_the_same_hooks_as_the_v2_subclass() -> None:
    """The gate lives on the shared builder, not on one schema's subclass."""

    with override_settings(SESSION_LIST_FILTER_SEED_WITNESS_SLACK_HOURS=1):
        builder = _builder(_attribute_filter(), klass=SessionListQueryBuilder)
        sql, _params = _seed(builder)

        assert builder.filter_seed_witness_slack_hours() == 1
    assert "FROM spans AS witness_spans" in sql


class _SessionWorld:
    """One tenant's sessions, answered through the builder's own SQL.

    Each session has one root span and one span carrying the filtered value
    (or none, for a session that does not match). The seed statement is
    answered from the root times, restricted by the witness envelope only when
    the emitted SQL actually carries the gate; the classifier is authoritative
    and answers from the matching set whatever the seed asked for.
    """

    def __init__(self, sessions: dict[str, tuple[datetime, datetime | None]]):
        self.sessions = sessions
        self.classified: list[str] = []
        self.seed_statements = 0

    def execute_ch_query(self, query, params, *, timeout_ms=None, settings=None):
        del timeout_ms, settings
        if "seed_sessions AS" in query:
            return QueryResult(*self._seed(query, params))
        return QueryResult(*self._classify(params))

    def _seed(self, query, params):
        self.seed_statements += 1
        gated = "witness_spans" in query
        rows = []
        for session_id, (root, witness) in self.sessions.items():
            if not params["filter_slice_start"] <= root < params["filter_slice_end"]:
                continue
            if gated and (
                witness is None
                or not params["filter_witness_start"]
                <= witness
                < params["filter_witness_end"]
            ):
                continue
            rows.append({"session_id": session_id, "start_time": root})
        rows.sort(key=lambda row: (row["start_time"], row["session_id"]), reverse=True)
        before = params.get("filter_before_start_time")
        if before is not None:
            boundary = (before, params["filter_before_session_id"])
            rows = [
                row for row in rows if (row["start_time"], row["session_id"]) < boundary
            ]
        rows = rows[: params["filter_seed_limit"]]
        return rows, len(rows), "clickhouse", 1.0

    def _classify(self, params):
        asked = {
            str(value)
            for bound in params.values()
            if isinstance(bound, list | tuple)
            for value in bound
            if str(value) in self.sessions
        }
        self.classified.extend(sorted(asked))
        rows = [
            {"session_id": session_id, "start_time": self.sessions[session_id][0]}
            for session_id in sorted(asked)
            if self.sessions[session_id][1] is not None
        ]
        rows.sort(key=lambda row: (row["start_time"], row["session_id"]), reverse=True)
        return rows, len(rows), "clickhouse", 1.0


def _read_page(world: _SessionWorld, builder) -> list[tuple[str, datetime]]:
    page = read_bounded_filter_page(
        builder=builder,
        analytics=world,
        filters=builder.filters,
        key_field="session_id",
        page_number=0,
        page_size=builder.page_size,
        deadline_ms=5_000,
        max_candidates=200,
        classify_batch_size=builder.recommended_filter_classify_batch_size(),
        include_incomplete_rows=True,
        bounded_continuation=True,
    )
    assert page.complete is True
    return [(str(row["session_id"]), row["start_time"]) for row in page.rows]


@pytest.mark.unit
def test_the_gated_walk_publishes_the_same_page_from_fewer_candidates() -> None:
    """Candidacy narrows; the published page does not move."""

    root = END - timedelta(hours=1)
    world = {
        SESSION_A: (root, root - timedelta(minutes=5)),
        SESSION_B: (root - timedelta(minutes=1), None),
        SESSION_C: (root - timedelta(minutes=2), root - timedelta(minutes=2)),
    }

    unseeded = _SessionWorld(dict(world))
    with override_settings(SESSION_LIST_FILTER_SEED_WITNESS_SLACK_HOURS=-1):
        open_page = _read_page(unseeded, _builder(_attribute_filter()))

    seeded = _SessionWorld(dict(world))
    with override_settings(SESSION_LIST_FILTER_SEED_WITNESS_SLACK_HOURS=1):
        gated_page = _read_page(seeded, _builder(_attribute_filter()))

    assert (
        gated_page == open_page == [(SESSION_A, root), (SESSION_C, world[SESSION_C][0])]
    )
    assert SESSION_B in unseeded.classified
    assert SESSION_B not in seeded.classified
