"""A seeded Users page states hash execution; an unseeded one streams in order.

Offline shape guards, no tables and no connection. The live differential that
proves the two execution shapes publish the same rows is
``test_users_seeded_page_read_settings_ch25.py``.
"""

import re
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from tracer.services.clickhouse.query_builders.user_list import (
    _SEEDED_PAGE_READ_SETTINGS,
    UserListQueryBuilder,
)
from tracer.services.clickhouse.v2.query_builders.filters import _append_v2_settings
from tracer.services.clickhouse.v2.query_builders.user_list import (
    UserListQueryBuilderV2,
)

pytestmark = pytest.mark.unit

PROJECT = str(UUID(int=31))
ORGANIZATION = str(UUID(int=32))
START = datetime(2026, 8, 1, tzinfo=UTC)
END = START + timedelta(days=3)
REQUIRED = "use_skip_indexes_if_final = 0, optimize_use_projections = 1"
OPT_OUT = (
    "optimize_aggregation_in_order = 0, optimize_distinct_in_order = 0, "
    "optimize_read_in_order = 0"
)
IN_ORDER_KEYS = (
    "optimize_aggregation_in_order",
    "optimize_distinct_in_order",
    "optimize_read_in_order",
)


def _date_filter():
    return {
        "column_id": "created_at",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [START.isoformat(), END.isoformat()],
        },
    }


def _text_filter(value="gold"):
    return {
        "column_id": "tag",
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": "text",
            "filter_op": "equals",
            "filter_value": value,
        },
    }


def _settings_tail(sql):
    match = re.search(r"\n\s*SETTINGS (.*)$", sql.rstrip(), re.DOTALL)
    assert match, "statement carries no trailing SETTINGS clause"
    return " ".join(match.group(1).split())


def _acquisition(*filters):
    builder = UserListQueryBuilderV2(
        organization_id=ORGANIZATION,
        project_ids=[PROJECT],
        filters=[_date_filter(), *filters],
        search="",
        empty_scope=False,
    )
    sql, _params = builder.build_dimension_candidate_query(
        limit=65, window_start=START, window_end=END
    )
    return sql


def test_seeded_acquisition_states_hash_execution_once():
    sql = _acquisition(_text_filter())
    assert "scalar_witness_identities AS" in sql, "fixture must qualify a witness"
    assert _settings_tail(sql) == f"{OPT_OUT}, {REQUIRED}"
    for key in IN_ORDER_KEYS:
        assert sql.count(key) == 1, key


def test_unseeded_acquisition_keeps_streaming_in_order_aggregation():
    sql = _acquisition()
    assert "scalar_witness_identities AS" not in sql
    assert "candidate_span_identities AS" not in sql
    assert _settings_tail(sql) == f"{REQUIRED}, optimize_aggregation_in_order = 1"
    assert "optimize_distinct_in_order" not in sql
    assert "optimize_read_in_order" not in sql


def test_witness_that_does_not_qualify_is_an_unseeded_page():
    # A non-ASCII value declines the scalar witness, so the page is unseeded
    # and must not give up the streaming collapse over the whole window.
    sql = _acquisition(_text_filter("göld"))
    assert "scalar_witness_identities AS" not in sql
    assert _settings_tail(sql) == f"{REQUIRED}, optimize_aggregation_in_order = 1"


def test_finite_candidate_page_replay_is_seeded_too():
    builder = UserListQueryBuilderV2(
        organization_id=ORGANIZATION,
        project_ids=[PROJECT],
        filters=[_date_filter()],
        limit=2,
        offset=0,
        candidate_end_user_ids=[str(UUID(int=41)), str(UUID(int=42))],
        candidate_scan_end_user_ids=[str(UUID(int=41)), str(UUID(int=42))],
        empty_scope=False,
    )
    sql, _params = builder.build_candidate_page_query()
    assert "candidate_span_identities AS" in sql
    assert _settings_tail(sql) == f"{OPT_OUT}, {REQUIRED}"


def test_v1_builder_emits_the_opt_out_only_for_seeded_pages():
    seeded = UserListQueryBuilder(
        organization_id=ORGANIZATION,
        project_ids=[PROJECT],
        filters=[_date_filter()],
        limit=65,
        offset=0,
    )
    with_witness, _ = seeded.build_candidate_page_query(
        cursor_mode=True,
        scalar_witness="mapContains(span_attr_str, 'tag')",
        scalar_witness_params={},
    )
    assert with_witness.rstrip().endswith(_SEEDED_PAGE_READ_SETTINGS)
    unseeded = UserListQueryBuilder(
        organization_id=ORGANIZATION,
        project_ids=[PROJECT],
        filters=[_date_filter()],
        limit=65,
        offset=0,
    )
    without_witness, _ = unseeded.build_candidate_page_query(cursor_mode=True)
    assert "SETTINGS" not in without_witness


@pytest.mark.parametrize("stated", [0, 1])
def test_boundary_keeps_an_explicit_aggregation_choice(stated):
    sql = f"SELECT 1 SETTINGS optimize_aggregation_in_order = {stated}"
    out = _append_v2_settings(sql)
    assert out == f"{sql}, {REQUIRED}"
    assert out.count("optimize_aggregation_in_order") == 1
    # The keyword default is still supplied when the statement says nothing.
    assert _append_v2_settings("SELECT 1 SETTINGS max_threads = 8") == (
        f"SELECT 1 SETTINGS max_threads = 8, {REQUIRED}, "
        "optimize_aggregation_in_order = 1"
    )


def test_boundary_choice_survives_a_trailing_format_clause():
    sql = "SELECT 1 SETTINGS optimize_aggregation_in_order = 0 FORMAT JSON"
    assert _append_v2_settings(sql) == (
        f"SELECT 1 SETTINGS optimize_aggregation_in_order = 0, {REQUIRED} FORMAT JSON"
    )
