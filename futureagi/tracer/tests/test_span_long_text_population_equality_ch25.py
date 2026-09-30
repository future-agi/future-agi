"""Live proof that key-only prefix discovery changes acquisition, never rows.

A span seed carries its exact value comparison once, on collapsed latest
state, and discovers which primary-key prefixes to collapse through a raw
witness. For a value too large to inline twice under the parser limit that
witness is key presence alone, which admits every prefix holding the key; the
exact comparison then rejects the rest. Whether that is still the same page
is a question about real rows in real granules, so it is read twice against a
real ClickHouse - once with value discovery, once with key-only discovery -
and the two answers must be identical. The measured oversized shape is then
read once more and must run at all: with the second copy admitted back it is
refused, which is what production recorded.

Opt-in exactly as ``test_long_text_anchor_equality_ch25``: it creates and
drops a table as an admin user, names the disposable stack through
``CH25_NATIVE_PORT`` and refuses the well-known port-forward ports.
"""

from __future__ import annotations

import pytest
from clickhouse_driver.errors import Error as ClickHouseError

from tracer.selectors.trace_filter_reads import read_bounded_filter_page
from tracer.services.clickhouse.query_builders import latest_filter_predicates
from tracer.services.clickhouse.query_service import QueryResult
from tracer.services.clickhouse.v2.query_builders.span_list import (
    SpanListQueryBuilderV2,
)
from tracer.tests.test_list_long_text_parser_limit import (
    FIRST_VALUE,
    MID_VALUE,
    SECOND_VALUE,
)
from tracer.tests.test_long_text_anchor_equality_ch25 import (  # noqa: F401
    KEY,
    PROJECT_ID,
    WINDOW_END,
    WINDOW_START,
    _row,
    anchor_span_table,
    ch_client,
    ch_port,
)

pytestmark = pytest.mark.integration

DECOY_VALUE = MID_VALUE.replace("assistant", "customer")


def _page(ch_client, table, values, statements):  # noqa: F811
    class LocalSpanBuilder(SpanListQueryBuilderV2):
        TABLE = table

    class LocalAnalytics:
        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            statements.append(query)
            data, schema = ch_client.execute(
                query,
                params,
                settings={
                    **settings,
                    "max_execution_time": max(timeout_ms / 1000, 5.0),
                },
                with_column_types=True,
            )
            names = [name for name, _type in schema]
            mapped = [dict(zip(names, row, strict=True)) for row in data]
            return QueryResult(
                data=mapped,
                row_count=len(mapped),
                backend_used="clickhouse",
                query_time_ms=0.0,
            )

    filters = [
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [WINDOW_START.isoformat(), WINDOW_END.isoformat()],
            },
        },
        {
            "column_id": KEY,
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "text",
                "filter_op": "in",
                "filter_value": values,
                "attribute_value_types": ["string"] * len(values),
            },
        },
    ]
    return read_bounded_filter_page(
        builder=LocalSpanBuilder(
            project_id=PROJECT_ID,
            filters=filters,
            page_size=25,
            bounded_internal_scan=True,
        ),
        analytics=LocalAnalytics(),
        filters=filters,
        key_field="id",
        page_number=0,
        page_size=25,
        deadline_ms=20_000,
        include_incomplete_rows=True,
    )


def _seed_statements(statements):
    return [sql for sql in statements if "AS latest_seed_spans" in sql]


def test_key_only_prefix_discovery_returns_the_same_rows_as_value_discovery(
    ch_client,  # noqa: F811
    anchor_span_table,  # noqa: F811
    monkeypatch,
):
    """Acquisition may change; the page may not."""

    matching = [0, 3, 7, 11, 19]
    rows = [_row(index, MID_VALUE) for index in matching]
    rows += [_row(index, MID_VALUE + " tail") for index in (23, 29)]
    # Decoys share the key, the hours and the prefixes of the matches, so a
    # key-only discovery admits their granules and the exact comparison must
    # be the thing that rejects them.
    rows += [_row(index, DECOY_VALUE) for index in range(30, 90)]
    ch_client.execute(f"INSERT INTO {anchor_span_table} VALUES", rows)
    expected = sorted(f"span-{index:03d}" for index in [*matching, 23, 29])

    by_value_statements: list[str] = []
    by_value = _page(
        ch_client,
        anchor_span_table,
        [MID_VALUE, MID_VALUE + " tail"],
        by_value_statements,
    )
    # Guard against a vacuous pass: value discovery must really have run.
    seeds = _seed_statements(by_value_statements)
    assert seeds
    assert all(sql.count("latest_filter_param_0_string") == 2 for sql in seeds)

    monkeypatch.setattr(
        latest_filter_predicates, "_MAX_TWICE_INLINED_VALUES_RENDERED_BYTES", 0
    )
    by_key_statements: list[str] = []
    by_key = _page(
        ch_client,
        anchor_span_table,
        [MID_VALUE, MID_VALUE + " tail"],
        by_key_statements,
    )
    seeds = _seed_statements(by_key_statements)
    assert seeds
    assert all(sql.count("latest_filter_param_0_string") == 1 for sql in seeds)

    assert sorted(item["id"] for item in by_value.rows) == expected
    assert sorted(item["id"] for item in by_key.rows) == expected
    assert by_value.complete == by_key.complete


def test_the_measured_oversized_shape_runs_and_its_second_copy_is_refused(
    ch_client,  # noqa: F811
    anchor_span_table,  # noqa: F811
    monkeypatch,
):
    """The shape production refused now reads its page; re-admitting the
    second copy reproduces the refusal on a real server."""

    matching = [2, 5]
    rows = [_row(index, FIRST_VALUE) for index in matching]
    rows += [_row(index, SECOND_VALUE) for index in (8,)]
    rows += [_row(index, DECOY_VALUE) for index in range(30, 60)]
    ch_client.execute(f"INSERT INTO {anchor_span_table} VALUES", rows)

    statements: list[str] = []
    page = _page(ch_client, anchor_span_table, [FIRST_VALUE, SECOND_VALUE], statements)
    assert page.complete is True
    assert sorted(item["id"] for item in page.rows) == [
        "span-002",
        "span-005",
        "span-008",
    ]

    monkeypatch.setattr(
        latest_filter_predicates, "_MAX_TWICE_INLINED_VALUES_RENDERED_BYTES", 1 << 40
    )
    with pytest.raises(ClickHouseError) as refused:
        _page(ch_client, anchor_span_table, [FIRST_VALUE, SECOND_VALUE], [])
    assert refused.value.code == 62
