"""Every native replica needs dictionaries, including previously installed ones."""

import re
from pathlib import Path

import pytest

from tracer.services.clickhouse.v2.apply_schema_rewriter import (
    extract_table_name,
    rewrite_for_replicated,
    split_statements,
    with_dictionary_credentials,
)

SCHEMA = Path(__file__).resolve().parents[1] / "services/clickhouse/v2/schema"


def _rewrite(sql):
    return rewrite_for_replicated(
        sql, table_name="trace_dict", cluster="test_cluster", zk_prefix="/test"
    )


@pytest.mark.parametrize(
    "prefix",
    [
        "CREATE DICTIONARY",
        "CREATE DICTIONARY IF NOT EXISTS",
        "CREATE OR REPLACE DICTIONARY",
    ],
)
@pytest.mark.parametrize(
    "name",
    [
        "trace_dict",
        "analytics.trace_dict",
        "`analytics`.`trace_dict`",
        '"analytics"."trace_dict"',
    ],
)
def test_dictionary_ddl_is_cluster_wide_without_changing_source(prefix, name):
    suffix = (
        "(id UUID, name String) PRIMARY KEY id "
        "SOURCE(CLICKHOUSE(TABLE 'traces')) "
        "LAYOUT(COMPLEX_KEY_HASHED()) LIFETIME(60);"
    )
    sql = f"{prefix} {name} {suffix}"
    assert extract_table_name(sql) == "trace_dict"
    rewritten = _rewrite(sql)
    assert rewritten == f"{prefix} {name} ON CLUSTER 'test_cluster' {suffix}"
    assert _rewrite(rewritten) == rewritten
    # Credentials are still injected only when executing, never stored in schema.
    executed = with_dictionary_credentials(rewritten, "test_user", "synthetic-password")
    assert "ON CLUSTER 'test_cluster'" in executed
    assert "USER 'test_user' PASSWORD 'synthetic-password'" in executed
    assert "synthetic-password" not in rewritten


def _dictionaries(path):
    return [
        s
        for s in split_statements(path.read_text())
        if re.match(r"CREATE\s+(?:OR\s+REPLACE\s+)?DICTIONARY\b", s, re.I)
    ]


def test_every_shipped_dictionary_enters_replicated_rewrite():
    found = set()
    for path in sorted(SCHEMA.glob("*.sql")):
        for stmt in _dictionaries(path):
            name = extract_table_name(stmt)
            assert name is not None, path.name
            found.add(name)
            out = rewrite_for_replicated(
                stmt, table_name=name, cluster="test_cluster", zk_prefix="/test"
            )
            assert out.count("ON CLUSTER 'test_cluster'") == 1, path.name
    assert found == {"trace_dict", "end_users_dict", "trace_sessions_dict"}


def test_forward_repair_reissues_all_original_definitions():
    original = {}
    for filename in [
        "015_traces_and_trace_dict.sql",
        "017_end_users.sql",
        "018_trace_sessions.sql",
    ]:
        for stmt in _dictionaries(SCHEMA / filename):
            normalized = re.sub(
                r"CREATE DICTIONARY IF NOT EXISTS", "CREATE OR REPLACE DICTIONARY", stmt
            )
            original[extract_table_name(stmt)] = normalized
    repair_file = SCHEMA / "028_reconcile_metadata_dictionaries.sql"
    repair = {extract_table_name(s): s for s in _dictionaries(repair_file)}
    assert repair.keys() == original.keys()
    for name, stmt in repair.items():
        assert " ".join(stmt.split()) == " ".join(original[name].split())
    # Repair is limited to dictionary metadata; never replays lost telemetry.
    assert len(split_statements(repair_file.read_text())) == 3
