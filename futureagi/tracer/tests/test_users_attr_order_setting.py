"""Focused public contract for helper-local ordered-aggregation opt-out."""
from datetime import UTC, datetime, timedelta

import pytest

from tracer.services.clickhouse.v2.query_builders.filters import _append_v2_settings
from tracer.services.users_list_manager import _users_attr_enrichment_query

pytestmark = pytest.mark.unit
COMMON = "use_skip_indexes_if_final = 0, optimize_use_projections = 1"


@pytest.mark.parametrize("sql,head,tail", [
    ("SELECT 1", "SELECT 1\nSETTINGS ", ""),
    ("SELECT 1;", "SELECT 1\nSETTINGS ", ""),
    ("SELECT 1 FORMAT JSON", "SELECT 1\nSETTINGS ", " FORMAT JSON"),
    ("SELECT 1 SETTINGS max_threads = 8", "SELECT 1 SETTINGS max_threads = 8, ", ""),
    ("SELECT 1 SETTINGS max_threads = 8 FORMAT JSONEachRow",
     "SELECT 1 SETTINGS max_threads = 8, ", " FORMAT JSONEachRow"),
])
def test_append_defaults_preserve_previous_sql_bytes(sql, head, tail):
    expected = head + COMMON + ", optimize_aggregation_in_order = 1" + tail
    assert _append_v2_settings(sql) == expected
    assert _append_v2_settings(sql, aggregation_in_order=True) == expected
    assert _append_v2_settings(sql, aggregation_in_order=False) == (
        head + COMMON + ", optimize_aggregation_in_order = 0" + tail
    )


def test_ordered_aggregation_optout_is_keyword_only():
    with pytest.raises(TypeError):
        _append_v2_settings("SELECT 1", False)


@pytest.mark.parametrize("finite", [False, True])
@pytest.mark.parametrize("workspace", [False, True])
def test_only_users_attribute_helper_opts_out(finite, workspace):
    project = "11111111-1111-1111-1111-111111111111"
    survivor = "22222222-2222-2222-2222-222222222222"
    start = datetime(2026, 8, 8, tzinfo=UTC)
    sql, params = _users_attr_enrichment_query(
        **({"project_ids": [project]} if workspace else {"project_id": project}),
        attribute_keys=["tag", "absent"], start_date=start,
        end_date=start + timedelta(hours=1),
        candidate_end_user_id_map={survivor: survivor} if finite else None,
    )
    assert sql.endswith(COMMON + ", optimize_aggregation_in_order = 0")
    assert sql.count("optimize_aggregation_in_order") == 1
    assert params["requested_attribute_keys"] == ["tag", "absent"]
    assert _append_v2_settings("SELECT 1").endswith("optimize_aggregation_in_order = 1")
