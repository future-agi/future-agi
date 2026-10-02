"""NORMAL col_type dispatch: real column builds SQL, nullable-UUID text op wraps in
``toString(...)``, and injection-shaped column_id is rejected, not concatenated."""

import pytest

from tracer.services.clickhouse.query_builders.filters import ClickHouseFilterBuilder


@pytest.mark.unit
def test_normal_coltype_builds_column_condition_for_real_column():
    b = ClickHouseFilterBuilder(table="spans")
    # Unmapped column stays NORMAL -> _build_column_condition.
    sql = b._build_condition("my_column", b.NORMAL, "text", "is_null", None)
    assert sql is not None
    assert "my_column" in sql


@pytest.mark.unit
def test_normal_coltype_uuid_text_op_wraps_tostring():
    b = ClickHouseFilterBuilder(table="spans")
    # Nullable-UUID column: text op compares via toString(...) (_UUID_TEXT_FILTER_OPS).
    sql = b._build_condition("trace_session_id", b.NORMAL, "text", "contains", "abc")
    assert sql is not None
    assert "toString(trace_session_id)" in sql


@pytest.mark.unit
@pytest.mark.parametrize(
    "malicious",
    [
        "1) OR 1=1 --",
        "id; DROP TABLE spans",
        "id') OR ('1'='1",
    ],
)
def test_normal_coltype_rejects_sql_injection_in_column_id(malicious):
    b = ClickHouseFilterBuilder(table="spans")
    # NORMAL branch sanitizes col_id: injection-shaped id must raise, not be concatenated.
    with pytest.raises(ValueError):
        b._build_condition(malicious, b.NORMAL, "text", "is_null", None)
