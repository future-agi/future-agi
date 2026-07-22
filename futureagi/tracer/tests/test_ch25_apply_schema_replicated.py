"""Unit tests for the --replicated rewriter in apply_schema.py.

Why a separate test file: the rewriter is a pure function and we want
deterministic, fast tests that don't need a CH connection. The full
apply_schema integration is covered by `test_ch25_apply_schema.py`
(if it exists) which does need a live CH.

The rewriter is load-bearing for the production rollout. A silent
regression here (e.g., a future schema edit using SummingMergeTree that
the regex doesn't catch, or an ALTER TABLE that doesn't get ON CLUSTER)
would create non-replicated tables in prod — silent split-brain across
replicas. These tests cover the engines and statement shapes we care
about; any new engine the schema starts using needs a test added here.
"""

from __future__ import annotations

import pytest

from tracer.services.clickhouse.v2.apply_schema_rewriter import (
    ReplicatedRewriteError,
    rewrite_for_replicated,
    split_statements,
)
from tracer.services.clickhouse.v2.apply_schema_rewriter import (
    extract_table_name as _extract_table_name,
)


def _rewrite(sql: str, table: str = "spans") -> str:
    """Convenience wrapper with prod-default cluster + zk_prefix."""
    return rewrite_for_replicated(
        sql,
        table_name=table,
        cluster="default",
        zk_prefix="/clickhouse/tables",
    )


class TestExtractTableName:
    @pytest.mark.parametrize(
        "stmt, expected",
        [
            ("CREATE TABLE IF NOT EXISTS spans (a UInt8) ENGINE = MergeTree", "spans"),
            (
                "CREATE TABLE spans_v2_dead_letter (...) ENGINE = MergeTree",
                "spans_v2_dead_letter",
            ),
            (
                "CREATE MATERIALIZED VIEW IF NOT EXISTS spans_per_session_mv TO spans_per_session AS ...",
                "spans_per_session_mv",
            ),
            (
                "CREATE MATERIALIZED VIEW eval_per_config_mv TO eval_per_config AS ...",
                "eval_per_config_mv",
            ),
            ("ALTER TABLE spans ADD COLUMN foo String", "spans"),
            ("ALTER TABLE spans_per_session MODIFY TTL ...", "spans_per_session"),
            # Negative — statements we deliberately don't touch:
            ("INSERT INTO spans VALUES (1)", None),
            ("SELECT * FROM spans", None),
            ("DROP TABLE spans", None),  # we don't rewrite DROPs — safety
        ],
    )
    def test_extracts_or_skips(self, stmt, expected):
        assert _extract_table_name(stmt) == expected


class TestEngineRewrite:
    def test_replacing_merge_tree_preserves_args(self):
        out = _rewrite(
            "CREATE TABLE IF NOT EXISTS spans (a UInt8)\n"
            "ENGINE = ReplacingMergeTree(_version, is_deleted)\n"
            "ORDER BY a"
        )
        assert (
            "ENGINE = ReplicatedReplacingMergeTree("
            "'/clickhouse/tables/{shard}/spans', '{replica}', _version, is_deleted)"
        ) in out
        # ON CLUSTER appended at the right spot
        assert "CREATE TABLE IF NOT EXISTS spans ON CLUSTER 'default'" in out

    def test_aggregating_merge_tree_no_args(self):
        out = _rewrite(
            "CREATE TABLE IF NOT EXISTS spans_per_session (a UInt8)\n"
            "ENGINE = AggregatingMergeTree\n"
            "ORDER BY a",
            table="spans_per_session",
        )
        assert (
            "ENGINE = ReplicatedAggregatingMergeTree("
            "'/clickhouse/tables/{shard}/spans_per_session', '{replica}')"
        ) in out

    def test_merge_tree_basic(self):
        out = _rewrite(
            "CREATE TABLE IF NOT EXISTS dead_letter (a UInt8)\n"
            "ENGINE = MergeTree\nORDER BY a",
            table="dead_letter",
        )
        assert (
            "ENGINE = ReplicatedMergeTree("
            "'/clickhouse/tables/{shard}/dead_letter', '{replica}')"
        ) in out

    def test_summing_merge_tree_with_args(self):
        # We don't ship a SummingMergeTree today, but if/when we add the
        # filter-discovery rollup (MV_STRATEGY.md option (e)) this must work.
        out = _rewrite(
            "CREATE TABLE IF NOT EXISTS attr_kv_rollup (cnt UInt64)\n"
            "ENGINE = SummingMergeTree(cnt)\nORDER BY tuple()",
            table="attr_kv_rollup",
        )
        assert (
            "ENGINE = ReplicatedSummingMergeTree("
            "'/clickhouse/tables/{shard}/attr_kv_rollup', '{replica}', cnt)"
        ) in out


class TestOnClusterAttachment:
    def test_create_table_gets_on_cluster(self):
        out = _rewrite("CREATE TABLE IF NOT EXISTS spans (a UInt8) ENGINE = MergeTree")
        assert "CREATE TABLE IF NOT EXISTS spans ON CLUSTER 'default'" in out

    def test_create_materialized_view_gets_on_cluster(self):
        out = _rewrite(
            "CREATE MATERIALIZED VIEW IF NOT EXISTS spans_per_session_mv "
            "TO spans_per_session AS SELECT 1",
            table="spans_per_session_mv",
        )
        assert (
            "CREATE MATERIALIZED VIEW IF NOT EXISTS spans_per_session_mv "
            "ON CLUSTER 'default'"
        ) in out

    def test_alter_table_gets_on_cluster(self):
        out = _rewrite("ALTER TABLE spans ADD PROJECTION p (...)")
        assert "ALTER TABLE spans ON CLUSTER 'default'" in out

    def test_idempotent_on_already_clustered(self):
        # If the schema author already wrote ON CLUSTER manually, we must
        # NOT add a second one (CH rejects duplicate ON CLUSTER clauses).
        stmt = "CREATE TABLE foo ON CLUSTER 'shard1' (a UInt8) ENGINE = MergeTree"
        out = _rewrite(stmt, table="foo")
        # Engine still rewrites...
        assert "ReplicatedMergeTree" in out
        # ...but ON CLUSTER count stays at 1
        assert out.upper().count("ON CLUSTER") == 1


class TestDictionaryOnCluster:
    """Dictionaries are node-local objects — a CREATE DICTIONARY without
    ON CLUSTER lands only on the replica apply_schema connected to. This
    shipped: us2 prod had `end_users_dict` / `trace_sessions_dict` on 1 of
    3 replicas, so queries load-balanced to the other two failed Code 36.
    """

    @pytest.mark.parametrize(
        "stmt, expected",
        [
            (
                "CREATE DICTIONARY IF NOT EXISTS end_users_dict (a UInt8) PRIMARY KEY a "
                "SOURCE(CLICKHOUSE(TABLE 'end_users')) LIFETIME(60) LAYOUT(FLAT())",
                "end_users_dict",
            ),
            (
                "CREATE OR REPLACE DICTIONARY trace_dict (a UInt8) PRIMARY KEY a "
                "SOURCE(CLICKHOUSE(TABLE 'traces')) LIFETIME(60) LAYOUT(FLAT())",
                "trace_dict",
            ),
            (
                "CREATE DICTIONARY analytics.trace_dict (a UInt8) PRIMARY KEY a "
                "SOURCE(CLICKHOUSE(TABLE 'traces')) LIFETIME(60) LAYOUT(FLAT())",
                "trace_dict",
            ),
            ("DROP DICTIONARY trace_dict", None),
            ("SYSTEM RELOAD DICTIONARY trace_dict", None),
        ],
    )
    def test_extracts_or_skips(self, stmt, expected):
        assert _extract_table_name(stmt) == expected

    def test_create_dictionary_gets_on_cluster(self):
        out = _rewrite(
            "CREATE DICTIONARY IF NOT EXISTS end_users_dict\n"
            "(\n    end_user_id UUID,\n    user_id String\n)\n"
            "PRIMARY KEY end_user_id\n"
            "SOURCE(CLICKHOUSE(TABLE 'end_users' WHERE 'is_deleted = 0'))\n"
            "LIFETIME(MIN 60 MAX 120)\n"
            "LAYOUT(COMPLEX_KEY_HASHED());",
            table="end_users_dict",
        )
        assert (
            "CREATE DICTIONARY IF NOT EXISTS end_users_dict ON CLUSTER 'default'" in out
        )

    def test_create_dictionary_without_engine_does_not_raise(self):
        # Dictionaries have no ENGINE clause — the CREATE-TABLE fail-closed
        # path must not fire on them.
        out = _rewrite(
            "CREATE DICTIONARY d (a UInt8) PRIMARY KEY a "
            "SOURCE(CLICKHOUSE(TABLE 't')) LIFETIME(60) LAYOUT(FLAT())",
            table="d",
        )
        assert out.upper().count("ON CLUSTER") == 1

    def test_idempotent_on_already_clustered(self):
        stmt = (
            "CREATE DICTIONARY d ON CLUSTER 'shard1' (a UInt8) PRIMARY KEY a "
            "SOURCE(CLICKHOUSE(TABLE 't')) LIFETIME(60) LAYOUT(FLAT())"
        )
        out = _rewrite(stmt, table="d")
        assert out.upper().count("ON CLUSTER") == 1


class TestCustomZkPrefix:
    def test_zk_prefix_substituted(self):
        out = rewrite_for_replicated(
            "CREATE TABLE IF NOT EXISTS spans (a UInt8) ENGINE = MergeTree",
            table_name="spans",
            cluster="prod_cluster",
            zk_prefix="/ch/tables_v2",
        )
        assert (
            "ENGINE = ReplicatedMergeTree('/ch/tables_v2/{shard}/spans', '{replica}')"
        ) in out
        assert "ON CLUSTER 'prod_cluster'" in out


class TestAllShippedSchemas:
    """Sweep every shipped schema file through the rewriter and confirm
    the output is structurally what prod expects. Catches the case where
    someone adds a new schema file using an engine the regex doesn't
    cover (e.g., GraphiteMergeTree, CollapsingMergeTree).
    """

    @pytest.fixture
    def schema_files(self):
        import pathlib

        here = pathlib.Path(__file__).resolve().parents[1]
        schema_dir = here / "services" / "clickhouse" / "v2" / "schema"
        return sorted(schema_dir.glob("*.sql"))

    def test_every_create_table_becomes_replicated(self, schema_files):
        for f in schema_files:
            for stmt in split_statements(f.read_text()):
                name = _extract_table_name(stmt)
                if name is None:
                    continue
                if "CREATE TABLE" not in stmt.upper():
                    continue
                rewritten = _rewrite(stmt, table=name)
                # Every CREATE TABLE statement must end up with a
                # Replicated* engine in prod mode.
                assert "Replicated" in rewritten, (
                    f"{f.name}: CREATE TABLE for {name} did not become "
                    f"Replicated. Engine regex needs updating in apply_schema.py."
                )
                assert "ON CLUSTER" in rewritten, (
                    f"{f.name}: CREATE TABLE for {name} missing ON CLUSTER."
                )

    def test_every_create_mv_gets_on_cluster(self, schema_files):
        for f in schema_files:
            for stmt in split_statements(f.read_text()):
                name = _extract_table_name(stmt)
                if name is None:
                    continue
                if "CREATE MATERIALIZED VIEW" not in stmt.upper():
                    continue
                rewritten = _rewrite(stmt, table=name)
                assert "ON CLUSTER" in rewritten

    def test_every_create_dictionary_gets_on_cluster(self, schema_files):
        # The gate for the us2 incident: 015/017/018 ship dictionaries, and
        # every one of them must fan out to all replicas in prod mode.
        import re

        create_dict = re.compile(
            r"\s*CREATE\s+(?:OR\s+REPLACE\s+)?DICTIONARY", re.IGNORECASE
        )
        seen = 0
        for f in schema_files:
            for stmt in split_statements(f.read_text()):
                name = _extract_table_name(stmt)
                if name is None:
                    continue
                if not create_dict.match(stmt):
                    continue
                seen += 1
                rewritten = _rewrite(stmt, table=name)
                assert "ON CLUSTER" in rewritten, (
                    f"{f.name}: CREATE DICTIONARY for {name} missing ON CLUSTER."
                )
        assert seen >= 3, "expected the shipped dictionary DDLs to be swept"

    def test_every_alter_table_gets_on_cluster(self, schema_files):
        for f in schema_files:
            for stmt in split_statements(f.read_text()):
                name = _extract_table_name(stmt)
                if name is None:
                    continue
                if "ALTER TABLE" not in stmt.upper():
                    continue
                rewritten = _rewrite(stmt, table=name)
                assert "ON CLUSTER" in rewritten

    def test_local_mode_passthrough_unchanged(self, schema_files):
        # When --replicated is NOT set, files must apply verbatim.
        # This is implicitly tested by the rewriter only being called in
        # replicated mode, but we assert here that the engine declarations
        # in the source files are the non-Replicated variants — otherwise
        # local-mode apply would write Replicated tables without ZK config.
        #
        # Earlier this only checked for literal "ReplicatedMergeTree" which
        # missed "ReplicatedReplacingMergeTree" / "ReplicatedAggregatingMergeTree"
        # (codex review). Now substring-match the "Replicated" prefix.
        for f in schema_files:
            text = f.read_text()
            assert "Replicated" not in text, (
                f"{f.name}: source contains Replicated* engine declaration. "
                f"Source files must use non-Replicated engines; --replicated "
                f"rewrites at apply time."
            )
            assert "ON CLUSTER" not in text.upper(), (
                f"{f.name}: source contains ON CLUSTER. Source files must "
                f"be cluster-agnostic; --replicated injects ON CLUSTER at "
                f"apply time."
            )


class TestFailClosed:
    """Regression coverage for codex P1: replicated mode must NOT silently
    apply a CREATE TABLE whose ENGINE the rewriter doesn't recognise.
    """

    def test_create_with_unsupported_engine_raises(self):
        with pytest.raises(ReplicatedRewriteError) as exc:
            _rewrite(
                "CREATE TABLE foo (a UInt8) ENGINE = CollapsingMergeTree(sign)",
                table="foo",
            )
        assert (
            "CollapsingMergeTree" in str(exc.value)
            or "unrecognised" in str(exc.value).lower()
            or "recognised" in str(exc.value).lower()
        )

    def test_create_with_no_engine_at_all_raises(self):
        # Defensive: a CREATE without ENGINE is invalid CH SQL anyway, but
        # the rewriter shouldn't pass it through silently.
        with pytest.raises(ReplicatedRewriteError):
            _rewrite("CREATE TABLE foo (a UInt8)", table="foo")

    def test_alter_with_no_engine_is_fine(self):
        # ALTER doesn't have ENGINE — must NOT raise.
        out = _rewrite("ALTER TABLE foo ADD COLUMN b UInt8", table="foo")
        assert "ON CLUSTER" in out


class TestSchemaQualifiedNames:
    """Codex P1: <db>.<table> must extract the LAST segment for the ZK
    path, not the database qualifier.
    """

    @pytest.mark.parametrize(
        "stmt, expected",
        [
            ("CREATE TABLE analytics.spans (a UInt8) ENGINE = MergeTree", "spans"),
            ("CREATE TABLE `analytics`.`spans` (a UInt8) ENGINE = MergeTree", "spans"),
            (
                "CREATE MATERIALIZED VIEW analytics.spans_mv TO analytics.spans AS SELECT 1",
                "spans_mv",
            ),
            ("ALTER TABLE analytics.spans ADD COLUMN b UInt8", "spans"),
            (
                "CREATE TABLE IF NOT EXISTS `default`.`spans` (a UInt8) ENGINE = MergeTree",
                "spans",
            ),
            ("CREATE OR REPLACE TABLE foo (a UInt8) ENGINE = MergeTree", "foo"),
        ],
    )
    def test_extracts_table_segment(self, stmt, expected):
        assert _extract_table_name(stmt) == expected

    def test_zk_path_uses_table_not_database(self):
        out = _rewrite(
            "CREATE TABLE analytics.spans (a UInt8) ENGINE = MergeTree",
            table="spans",
        )
        # ZK path must be <prefix>/{shard}/spans, NOT .../analytics
        assert "'/clickhouse/tables/{shard}/spans'" in out
        assert "/analytics'" not in out


class TestGoldenOutputForShippedFiles:
    """Codex follow-up: substring asserts let a rewriter regression pass
    silently. These tests pin the EXACT engine line we expect for each
    shipped CREATE TABLE — any future schema edit that changes intent
    must update this test, making the change visible in review.
    """

    @pytest.mark.parametrize(
        "table, expected_engine_substr",
        [
            (
                "spans",
                "ReplicatedReplacingMergeTree('/clickhouse/tables/{shard}/spans', '{replica}', _version, is_deleted)",
            ),
            (
                "spans_v2_dead_letter",
                "ReplicatedMergeTree('/clickhouse/tables/{shard}/spans_v2_dead_letter', '{replica}')",
            ),
            (
                "schema_versions",
                "ReplicatedMergeTree('/clickhouse/tables/{shard}/schema_versions', '{replica}')",
            ),
            (
                "backfill_checkpoints",
                "ReplicatedReplacingMergeTree('/clickhouse/tables/{shard}/backfill_checkpoints', '{replica}', _version)",
            ),
            (
                "spans_per_session",
                "ReplicatedAggregatingMergeTree('/clickhouse/tables/{shard}/spans_per_session', '{replica}')",
            ),
            (
                "eval_per_config",
                "ReplicatedAggregatingMergeTree('/clickhouse/tables/{shard}/eval_per_config', '{replica}')",
            ),
            (
                "spans_hourly_rollup",
                "ReplicatedAggregatingMergeTree('/clickhouse/tables/{shard}/spans_hourly_rollup', '{replica}')",
            ),
            (
                "tracer_eval_logger_v2",
                "ReplicatedReplacingMergeTree('/clickhouse/tables/{shard}/tracer_eval_logger_v2', '{replica}', _version, is_deleted)",
            ),
        ],
    )
    def test_engine_substr_matches(self, table, expected_engine_substr):
        # Find the file that declares this table and assert the rewrite
        # produces the expected engine line.
        import pathlib

        here = pathlib.Path(__file__).resolve().parents[1]
        schema_dir = here / "services" / "clickhouse" / "v2" / "schema"
        for f in sorted(schema_dir.glob("*.sql")):
            for stmt in split_statements(f.read_text()):
                if _extract_table_name(stmt) != table:
                    continue
                if "CREATE TABLE" not in stmt.upper():
                    continue
                out = _rewrite(stmt, table=table)
                assert expected_engine_substr in out, (
                    f"{f.name}: engine rewrite for {table} did not produce "
                    f"the pinned substring. Got:\n{out}"
                )
                return
        pytest.fail(f"No CREATE TABLE found for {table} in any shipped schema file")
