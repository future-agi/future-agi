import pytest

from tracer.services.clickhouse.v2.query_builders.filters import (
    ClickHouseFilterBuilderV2,
)

pytestmark = [pytest.mark.integration, pytest.mark.slow, pytest.mark.django_db]


def _attribute_filter(operation: str) -> dict:
    return {
        "column_id": "coupon",
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": "text",
            "filter_op": operation,
            "filter_value": None,
        },
    }


def _matching_count(ch_client, seeded_corpus, *, query_mode: str, operation: str):
    rows = seeded_corpus.rows
    target_trace_id = next(
        row.trace_id for row in rows if "coupon" in row.span_attr_str
    )
    builder = ClickHouseFilterBuilderV2(
        query_mode=query_mode,
        project_id=str(seeded_corpus.project.id),
    )
    predicate, params = builder.translate([_attribute_filter(operation)])
    params.update(
        project_id=str(seeded_corpus.project.id),
        target_trace_id=target_trace_id,
    )
    aggregate = "uniqExact(trace_id)" if query_mode == "trace" else "count()"
    query = f"""
        SELECT {aggregate}
        FROM spans
        WHERE project_id = %(project_id)s
          AND trace_id = %(target_trace_id)s
          AND is_deleted = 0
          AND {predicate}
    """
    return ch_client._client.execute(query, params)[0][0]


@pytest.mark.parametrize("surface", ["trace", "voice", "session"])
def test_multi_span_entity_attribute_nullness_is_mutually_exclusive(
    ch_schema,
    seeded_corpus,
    surface,
):
    query_mode = {"trace": "trace", "voice": "trace", "session": "trace"}[surface]
    assert (
        _matching_count(
            ch_schema,
            seeded_corpus,
            query_mode=query_mode,
            operation="is_null",
        )
        == 0
    )
    assert (
        _matching_count(
            ch_schema,
            seeded_corpus,
            query_mode=query_mode,
            operation="is_not_null",
        )
        == 1
    )


def test_multi_span_trace_preserves_row_level_span_nullness(ch_schema, seeded_corpus):
    trace_rows = [
        row
        for row in seeded_corpus.rows
        if row.trace_id
        == next(
            candidate.trace_id
            for candidate in seeded_corpus.rows
            if "coupon" in candidate.span_attr_str
        )
    ]
    present = sum("coupon" in row.span_attr_str for row in trace_rows)

    assert (
        _matching_count(
            ch_schema,
            seeded_corpus,
            query_mode="span",
            operation="is_null",
        )
        == len(trace_rows) - present
    )
    assert (
        _matching_count(
            ch_schema,
            seeded_corpus,
            query_mode="span",
            operation="is_not_null",
        )
        == present
    )
