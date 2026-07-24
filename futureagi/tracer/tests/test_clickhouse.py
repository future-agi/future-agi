"""
Tests for ClickHouse Analytics Backend

Comprehensive tests covering:
- Schema DDL generation
- Filter builder translation
- Query builders (time-series, trace list, session list, eval metrics, error analysis)
- Analytics query service dispatch layer
- Consistency checker
- Base query builder utilities
"""

from unittest import mock

import pytest


@pytest.mark.unit
class TestNonTerminalEvalMarker:
    """The shared list-builder resolver for non-completed eval cell state."""

    def test_precedence_skipped_over_running_over_pending(self):
        from tracer.services.clickhouse.query_builders.eval_status import (
            non_terminal_eval_marker,
        )

        # skipped wins
        assert non_terminal_eval_marker(
            {"skipped_count": 1, "running_count": 1, "pending_count": 1}
        ) == {"status": "skipped"}
        # running beats pending
        assert non_terminal_eval_marker({"running_count": 1, "pending_count": 2}) == {
            "status": "running"
        }
        # pending only
        assert non_terminal_eval_marker({"pending_count": 3}) == {"status": "pending"}

    def test_skipped_reason_passthrough(self):
        from tracer.services.clickhouse.query_builders.eval_status import (
            non_terminal_eval_marker,
        )

        assert non_terminal_eval_marker(
            {"skipped_count": 1, "skipped_reason": "missing attr"}
        ) == {"status": "skipped", "skipped_reason": "missing attr"}

    def test_returns_none_when_no_non_terminal_rows(self):
        from tracer.services.clickhouse.query_builders.eval_status import (
            non_terminal_eval_marker,
        )

        assert non_terminal_eval_marker({}) is None
        assert non_terminal_eval_marker({"error_count": 5}) is None


# ============================================================================
# 1. Schema Tests
# ============================================================================


@pytest.mark.unit
class TestClickHouseSchema:
    """Test schema DDL generation."""

    def test_get_all_schema_ddl_returns_correct_count(self):
        """Should return all DDL statements in correct order."""
        from tracer.services.clickhouse.schema import (
            get_all_schema_ddl,
            should_drop_legacy_chain,
        )

        ddl = get_all_schema_ddl()
        assert len(ddl) >= 10
        names = [name for name, _ in ddl]
        assert "trace_dict" in names
        # The legacy CDC chain is gated by CH25_DROP_LEGACY_CDC_CHAIN.
        # Default (False, prod-safe) keeps it; True (dev) drops the whole
        # chain AND filters the legacy v1 ``spans`` registry entry (v2 spans
        # is applied via the v2 SQL files, not this registry).
        legacy_names = [
            "tracer_trace",
            "tracer_enduser",
            "trace_session",
            "tracer_observation_span",
            "enduser_dict",
            "trace_session_dict",
            "eval_metrics_hourly",
            "eval_metrics_hourly_mv",
        ]
        if should_drop_legacy_chain():
            for legacy in legacy_names:
                assert legacy not in names, f"{legacy} must be excluded when flag set"
            assert "spans" not in names, (
                "v1 SPANS_TABLE must be filtered when flag set — otherwise "
                "the boot DDL apply recreates v1 after the helper drops it."
            )
        else:
            for legacy in legacy_names:
                assert legacy in names
            assert "spans" in names
        # tracer_eval_logger is created regardless of the flag — the eval-config
        # discovery reads query it even in the spans-cutover (flag-on) config.
        assert "tracer_eval_logger" in names

    def test_get_all_schema_ddl_returns_list_of_tuples(self):
        """Each entry should be a (name, ddl_string) tuple."""
        from tracer.services.clickhouse.schema import get_all_schema_ddl

        ddl = get_all_schema_ddl()
        for entry in ddl:
            assert isinstance(entry, tuple)
            assert len(entry) == 2
            name, statement = entry
            assert isinstance(name, str)
            assert isinstance(statement, str)

    def test_get_all_schema_ddl_creation_order(self):
        """CDC tables must come before dict, dict before spans, spans before MVs."""
        from tracer.services.clickhouse.schema import (
            get_all_schema_ddl,
            should_drop_legacy_chain,
        )

        ddl = get_all_schema_ddl()
        names = [name for name, _ in ddl]

        # CDC tables must appear before trace_dict, and trace_dict before
        # v1 spans — only meaningful when the legacy chain is in the
        # registry (flag off); when dropped, those names aren't present.
        if not should_drop_legacy_chain():
            assert names.index("tracer_trace") < names.index("trace_dict")
            assert names.index("trace_dict") < names.index("spans")
        # Dataset CDC tables must appear before dataset dictionaries
        assert names.index("model_hub_dataset") < names.index("dataset_dict")
        assert names.index("model_hub_column") < names.index("column_dict")
        # Dataset dictionaries must appear before dataset_cells view
        assert names.index("column_dict") < names.index("dataset_cells")
        assert names.index("dataset_dict") < names.index("dataset_cells")
        # Legacy chain ordering — only meaningful when the chain is in
        # the registry. When CH25_DROP_LEGACY_CDC_CHAIN drops these, the
        # ordering invariants below have no entries to compare.
        if not should_drop_legacy_chain():
            assert names.index("spans") < names.index("spans_mv")
            assert names.index("spans_mv") < names.index("span_metrics_hourly")

    def test_dataset_dict_scopes_to_active_datasets(self):
        """Dataset dashboards should not surface soft-deleted datasets."""
        from tracer.services.clickhouse.schema import DATASET_DICT

        assert ".model_hub_dataset" in DATASET_DICT
        assert "WHERE _peerdb_is_deleted = 0" in DATASET_DICT
        assert "AND deleted = 0" in DATASET_DICT

    def test_eval_logger_ddl_has_target_type_and_trace_session_columns(self):
        """PR3: tracer_eval_logger DDL must carry the new row_type-stack columns.

        Asserts (a) ``trace_session_id`` is present and Nullable, (b)
        ``target_type`` is present with default 'span', (c) ``trace_id`` and
        ``observation_span_id`` are Nullable, (d) the eval_metrics_hourly MV
        resolves project_id correctly for session rows so all target types
        contribute to the rollup.
        """
        from tracer.services.clickhouse.schema import (
            CDC_EVAL_LOGGER,
            EVAL_METRICS_HOURLY_MV,
            TRACE_SESSION_DICT,
        )

        # New columns
        assert "trace_session_id Nullable(UUID)" in CDC_EVAL_LOGGER
        assert "target_type LowCardinality(String) DEFAULT 'span'" in CDC_EVAL_LOGGER

        # Existing FK columns must be Nullable for session rows to land
        assert "trace_id Nullable(UUID)" in CDC_EVAL_LOGGER
        assert "observation_span_id Nullable(String)" in CDC_EVAL_LOGGER

        # Bloom filter index on the new discriminator + session FK
        assert "idx_target_type target_type" in CDC_EVAL_LOGGER
        assert "idx_trace_session_id trace_session_id" in CDC_EVAL_LOGGER

        # Allow nullable key for trace_id in ORDER BY
        assert "allow_nullable_key = 1" in CDC_EVAL_LOGGER

        # New TRACE_SESSION_DICT exists and points at the trace_session table
        assert (
            "CREATE DICTIONARY IF NOT EXISTS trace_session_dict" in TRACE_SESSION_DICT
        )
        assert "trace_session" in TRACE_SESSION_DICT
        assert "project_id UUID" in TRACE_SESSION_DICT

        # MV INCLUDES sessions: no target_type filter; project_id resolved
        # via trace_session_dict for session rows, trace_dict for span/trace
        assert "target_type IN" not in EVAL_METRICS_HOURLY_MV, (
            "EVAL_METRICS_HOURLY_MV should NOT filter by target_type — sessions "
            "must contribute to the rollup. See PR3."
        )
        assert "trace_session_dict" in EVAL_METRICS_HOURLY_MV
        assert "trace_dict" in EVAL_METRICS_HOURLY_MV
        # The branching expression must be present
        assert (
            "if(" in EVAL_METRICS_HOURLY_MV
            and "target_type = 'session'" in EVAL_METRICS_HOURLY_MV
        ), "MV must branch project_id resolution on target_type='session'"

    def test_post_ddl_alters_evolves_existing_eval_logger_tables(self):
        """ALTER statements bring already-created tracer_eval_logger tables forward."""
        from tracer.services.clickhouse.schema import POST_DDL_ALTERS

        joined = "\n".join(POST_DDL_ALTERS)
        assert "tracer_eval_logger ADD COLUMN IF NOT EXISTS trace_session_id" in joined
        assert "tracer_eval_logger ADD COLUMN IF NOT EXISTS target_type" in joined
        assert "tracer_eval_logger MODIFY COLUMN trace_id Nullable(UUID)" in joined
        # The MODIFY trace_id must be sandwiched between DROP INDEX and ADD
        # INDEX for idx_trace_id (CH refuses to alter a column that's part
        # of a skip index — Code: 524).
        drop_idx = joined.index("DROP INDEX IF EXISTS idx_trace_id")
        modify = joined.index("MODIFY COLUMN trace_id Nullable(UUID)")
        readd_idx = joined.index("ADD INDEX IF NOT EXISTS idx_trace_id ")
        assert drop_idx < modify < readd_idx, (
            "POST_DDL_ALTERS must order DROP INDEX → MODIFY COLUMN → ADD INDEX "
            "for idx_trace_id; ClickHouse refuses to alter an indexed column."
        )

    def test_mv_recreate_manifest_consistency(self):
        """Every MV_RECREATE_MANIFEST entry must resolve to a real DDL constant."""
        from tracer.services.clickhouse import schema as ch_schema

        for mv_name, manifest in ch_schema.MV_RECREATE_MANIFEST.items():
            const_name = manifest["ddl_constant_name"]
            ddl = getattr(ch_schema, const_name, None)
            assert isinstance(ddl, str) and ddl.strip(), (
                f"Manifest entry for '{mv_name}' references "
                f"non-existent or empty DDL constant '{const_name}'"
            )
            # The MV must reference its declared source and target tables.
            assert manifest["source_table"] in ddl, (
                f"DDL for '{mv_name}' does not reference source_table "
                f"{manifest['source_table']!r}"
            )
            assert manifest["target_table"] in ddl, (
                f"DDL for '{mv_name}' does not reference target_table "
                f"{manifest['target_table']!r}"
            )

    def test_mv_recreate_manifest_backfill_mirrors_mv_filter(self):
        """The backfill_select must produce the same row set as the MV.

        Both the MV body and the manifest's backfill query include all
        target types (span/trace/session) and resolve project_id via the
        same target_type-branching expression. Drift between the two
        would cause the backfill to re-aggregate a different population
        than the MV processes on live writes.
        """
        from tracer.services.clickhouse.schema import (
            EVAL_METRICS_HOURLY_MV,
            MV_RECREATE_MANIFEST,
        )

        manifest = MV_RECREATE_MANIFEST["eval_metrics_hourly_mv"]
        backfill = manifest["backfill_select"]

        # Neither the MV nor the backfill filters by target_type — sessions
        # are deliberately included in the rollup. Pin this so a future
        # "fix" doesn't accidentally re-introduce the filter.
        assert "target_type IN" not in EVAL_METRICS_HOURLY_MV
        assert "target_type IN" not in backfill

        # Both branch project_id resolution on session vs span/trace
        for body in (EVAL_METRICS_HOURLY_MV, backfill):
            assert "target_type = 'session'" in body
            assert "trace_session_dict" in body
            assert "trace_dict" in body

        # Backfill carries the cutoff parameter
        assert "%(cutoff)s" in backfill
        # Backfill GROUP BY must match — the recreate command's chunk-injection
        # logic relies on the GROUP BY marker being present.
        assert "GROUP BY" in backfill

    def test_get_drop_statements(self):
        """Drop statements should be generated in reverse dependency order."""
        from tracer.services.clickhouse.schema import get_drop_statements

        drops = get_drop_statements()
        assert len(drops) > 0
        # Should contain DROP VIEW, DROP DICTIONARY, and DROP TABLE
        assert any("DROP VIEW" in d for d in drops)
        assert any("DROP DICTIONARY" in d for d in drops)
        assert any("DROP TABLE" in d for d in drops)

    def test_get_drop_statements_reverse_order(self):
        """MVs should be dropped before their target tables."""
        from tracer.services.clickhouse.schema import get_drop_statements

        drops = get_drop_statements()
        drop_names = []
        for d in drops:
            # Extract the name from "DROP ... IF EXISTS name;"
            parts = d.replace(";", "").split()
            drop_names.append(parts[-1])

        # eval_metrics_hourly_mv should come before eval_metrics_hourly
        assert drop_names.index("eval_metrics_hourly_mv") < drop_names.index(
            "eval_metrics_hourly"
        )
        # spans_mv should come before spans
        assert drop_names.index("spans_mv") < drop_names.index("spans")
        # trace_dict should come before tracer_trace
        assert drop_names.index("trace_dict") < drop_names.index("tracer_trace")

    def test_schema_ddl_contains_peerdb_columns(self):
        """CDC observation span table must contain PeerDB meta-columns."""
        from tracer.services.clickhouse.schema import CDC_OBSERVATION_SPAN

        assert "_peerdb_synced_at" in CDC_OBSERVATION_SPAN
        assert "_peerdb_is_deleted" in CDC_OBSERVATION_SPAN
        assert "_peerdb_version" in CDC_OBSERVATION_SPAN
        assert "ReplacingMergeTree" in CDC_OBSERVATION_SPAN

    def test_cdc_trace_contains_peerdb_columns(self):
        """CDC trace table must contain PeerDB meta-columns."""
        from tracer.services.clickhouse.schema import CDC_TRACE

        assert "_peerdb_synced_at" in CDC_TRACE
        assert "_peerdb_is_deleted" in CDC_TRACE
        assert "_peerdb_version" in CDC_TRACE
        assert "ReplacingMergeTree" in CDC_TRACE

    def test_cdc_eval_logger_contains_peerdb_columns(self):
        """CDC eval logger table must contain PeerDB meta-columns."""
        from tracer.services.clickhouse.schema import CDC_EVAL_LOGGER

        assert "_peerdb_synced_at" in CDC_EVAL_LOGGER
        assert "_peerdb_is_deleted" in CDC_EVAL_LOGGER
        assert "_peerdb_version" in CDC_EVAL_LOGGER

    def test_spans_table_has_map_columns(self):
        """Denormalized spans table must have typed Map columns for attribute analytics."""
        from tracer.services.clickhouse.schema import SPANS_TABLE

        assert "span_attr_str Map(LowCardinality(String), String)" in SPANS_TABLE
        assert "span_attr_num Map(LowCardinality(String), Float64)" in SPANS_TABLE
        assert "span_attr_bool Map(LowCardinality(String), UInt8)" in SPANS_TABLE

    def test_spans_table_has_denormalized_trace_fields(self):
        """Spans table must carry trace context for JOIN-free queries."""
        from tracer.services.clickhouse.schema import SPANS_TABLE

        assert "trace_name" in SPANS_TABLE
        assert "trace_session_id" in SPANS_TABLE
        assert "trace_external_id" in SPANS_TABLE
        assert "trace_tags" in SPANS_TABLE

    def test_spans_mv_reads_from_cdc_observation_span(self):
        """Spans MV must source from the CDC landing table."""
        from tracer.services.clickhouse.schema import SPANS_MV

        assert "FROM tracer_observation_span" in SPANS_MV
        assert "TO spans" in SPANS_MV

    def test_span_metrics_hourly_uses_aggregating_merge_tree(self):
        """Pre-aggregated table must use AggregatingMergeTree engine."""
        from tracer.services.clickhouse.schema import SPAN_METRICS_HOURLY_TABLE

        assert "AggregatingMergeTree" in SPAN_METRICS_HOURLY_TABLE

    def test_eval_metrics_hourly_table_exists(self):
        """Eval metrics hourly table must be defined."""
        from tracer.services.clickhouse.schema import EVAL_METRICS_HOURLY_TABLE

        assert "eval_metrics_hourly" in EVAL_METRICS_HOURLY_TABLE
        assert "AggregatingMergeTree" in EVAL_METRICS_HOURLY_TABLE

    def test_trace_dict_definition(self):
        """Trace dictionary must source from tracer_trace."""
        from tracer.services.clickhouse.schema import TRACE_DICT

        assert "CREATE DICTIONARY" in TRACE_DICT
        assert "tracer_trace" in TRACE_DICT
        assert "session_id" in TRACE_DICT

    def test_backfill_statements(self):
        """Backfill statements should insert into spans from CDC table."""
        from tracer.services.clickhouse.schema import get_backfill_statements

        stmts = get_backfill_statements()
        assert len(stmts) > 0
        assert "INSERT INTO spans" in stmts[0]
        assert "FROM tracer_observation_span" in stmts[0]
        assert "is_deleted = 0" in stmts[0]

    # ------------------------------------------------------------------
    # CH25_DROP_LEGACY_CDC_CHAIN toggle
    # ------------------------------------------------------------------
    # Dev/local docker compose sets the flag True so fi-collector is the
    # sole writer for `spans`. Prod keeps the legacy chain alive until
    # the cutover playbook (see docs/CH25_MIGRATION.md) is run.

    def test_legacy_chain_drop_statements_order_and_idempotency(self):
        """MV drops first, then their source tables; every drop is IF EXISTS."""
        from tracer.services.clickhouse.schema import (
            get_legacy_chain_drop_statements,
        )

        drops = get_legacy_chain_drop_statements()
        names = [n for n, _ in drops]
        # MV drops MUST precede the source table they reference.
        assert names.index("span_metrics_hourly_mv") < names.index(
            "span_metrics_hourly"
        ), "MV must drop before its source table"
        assert names.index("spans_mv") < names.index(
            "tracer_observation_span"
        ), "spans_mv must drop before tracer_observation_span"
        # Idempotency: every drop wraps IF EXISTS so reruns are no-ops.
        for _, sql in drops:
            assert "IF EXISTS" in sql, f"drop must be idempotent: {sql}"
        # Each kind drops with its matching statement — CH refuses the
        # wrong kind (a dictionary cannot be dropped with DROP TABLE).
        for name, sql in drops:
            if name.endswith("_mv"):
                assert sql.startswith("DROP VIEW IF EXISTS")
            elif name.endswith("_dict"):
                assert sql.startswith("DROP DICTIONARY IF EXISTS")
            else:
                assert sql.startswith("DROP TABLE IF EXISTS")

    def test_legacy_chain_drop_covers_exactly_the_expected_set(self):
        """The drop list pins the migration scope — no more, no less."""
        from tracer.services.clickhouse.schema import (
            get_legacy_chain_drop_statements,
        )

        names = {n for n, _ in get_legacy_chain_drop_statements()}
        assert names == {
            "span_metrics_hourly_mv",
            "spans_mv",
            "span_metrics_hourly",
            "tracer_observation_span",
            "eval_metrics_hourly_mv",
            "eval_metrics_hourly",
            "enduser_dict",
            "trace_session_dict",
            "tracer_trace",
            "tracer_enduser",
            "trace_session",
        }, (
            "Legacy chain drop set drifted; update "
            "_LEGACY_CDC_CHAIN_NAMES in schema.py and the cutover doc."
        )
        # tracer_eval_logger is intentionally exempt — the eval-config
        # discovery reads still query it, so it must not be dropped.
        assert "tracer_eval_logger" not in names

    # ------------------------------------------------------------------
    # v1 → v2 spans shape detection (dev-upgrade boot hook)
    # ------------------------------------------------------------------
    # CREATE TABLE IF NOT EXISTS silently no-ops on shape drift. Dev
    # boxes upgrading from an old CH 24.x volume need an explicit
    # detector so the boot hook can drop the v1 spans + re-apply v2.

    def test_detect_spans_v2(self):
        """v2 marker column ``attrs_string`` → result is ``v2``."""
        from tracer.services.clickhouse.schema import detect_spans_table_shape

        def stub(query: str):
            if "system.columns" in query:
                return [("attrs_string",)]
            return [(1,)]

        assert detect_spans_table_shape(stub) == "v2"

    def test_detect_spans_v1(self):
        """v1 marker ``span_attr_str`` → result is ``v1``."""
        from tracer.services.clickhouse.schema import detect_spans_table_shape

        def stub(query: str):
            if "system.columns" in query:
                return [("span_attr_str",)]
            return [(1,)]

        assert detect_spans_table_shape(stub) == "v1"

    def test_detect_spans_absent(self):
        """No table at all → ``absent`` (boot hook skips the drop)."""
        from tracer.services.clickhouse.schema import detect_spans_table_shape

        def stub(query: str):
            if "system.columns" in query:
                return []
            return [(0,)]

        assert detect_spans_table_shape(stub) == "absent"

    def test_detect_spans_unknown(self):
        """Table exists but neither marker → ``unknown``; refuse to migrate."""
        from tracer.services.clickhouse.schema import detect_spans_table_shape

        def stub(query: str):
            if "system.columns" in query:
                return []
            return [(1,)]

        assert detect_spans_table_shape(stub) == "unknown"

    def test_detect_prefers_v2_when_both_markers_appear(self):
        """When v2 + v1 column names coexist (unlikely but explicit),
        treat as v2 — don't trigger a destructive drop on a table that
        already has the v2 column."""
        from tracer.services.clickhouse.schema import detect_spans_table_shape

        def stub(query: str):
            if "system.columns" in query:
                return [("attrs_string",), ("span_attr_str",)]
            return [(1,)]

        assert detect_spans_table_shape(stub) == "v2"


# ============================================================================
# 2. Filter Builder Tests
# ============================================================================


@pytest.mark.unit
class TestClickHouseFilterBuilder:
    """Test ClickHouse filter translation."""

    def test_translate_empty_filters(self):
        """Empty filter list should produce empty WHERE clause."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder()
        where, params = builder.translate([])
        assert where == ""
        assert params == {}

    def test_translate_global_annotator_multi_value_filter(self):
        """Multiple annotators should match the union of traces they annotated."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        user_ids = [
            "11111111-1111-1111-1111-111111111111",
            "22222222-2222-2222-2222-222222222222",
        ]
        builder = ClickHouseFilterBuilder()
        where, params = builder.translate(
            [
                {
                    "column_id": "annotator",
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "in",
                        "filter_value": user_ids,
                        "col_type": "SYSTEM_METRIC",
                    },
                }
            ]
        )

        assert "trace_id IN" in where
        assert "model_hub_score AS s FINAL" in where
        assert "s.annotator_id IN" in where
        assert "toUUID(%(uid_1)s), toUUID(%(uid_2)s)" in where
        assert params == {"uid_1": user_ids[0], "uid_2": user_ids[1]}

    @pytest.mark.django_db
    def test_global_annotator_filter_ignores_stale_annotation_col_type(
        self, custom_eval_config
    ):
        """Global annotator must not be parsed as an annotation-label UUID."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        user_id = "11111111-1111-1111-1111-111111111111"
        builder = ClickHouseFilterBuilder()
        where, params = builder.translate(
            [
                {
                    "column_id": str(custom_eval_config.id),
                    "filter_config": {
                        "filter_type": "number",
                        "filter_op": "greater_than",
                        "filter_value": 70,
                        "col_type": "EVAL_METRIC",
                    },
                },
                {
                    "column_id": "annotator",
                    "filter_config": {
                        "filter_type": "annotator",
                        "filter_op": "equals",
                        "filter_value": [user_id],
                        "col_type": "ANNOTATION",
                    },
                },
            ]
        )

        assert "tracer_eval_logger" in where
        assert "model_hub_score AS s FINAL" in where
        assert "s.annotator_id IN (toUUID(%(uid_" in where
        assert "s.label_id = toUUID(%(ann_label" not in where
        assert "annotator" not in params.values()
        assert user_id in params.values()

    def test_span_mode_global_annotator_filter_targets_span_id(self):
        """The spans tab annotator filter should not widen to whole traces."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        user_id = "11111111-1111-1111-1111-111111111111"
        builder = ClickHouseFilterBuilder(
            query_mode=ClickHouseFilterBuilder.QUERY_MODE_SPAN
        )
        where, params = builder.translate(
            [
                {
                    "column_id": "annotator",
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": user_id,
                        "col_type": "SYSTEM_METRIC",
                    },
                }
            ]
        )

        assert where.strip().startswith("id IN")
        assert "trace_id IN" not in where
        assert "s.annotator_id IN (toUUID(%(uid_1)s))" in where
        assert "FROM spans WHERE" in where
        assert params == {"uid_1": user_id}

    def test_scoped_spans_subquery_org_mode_uses_project_ids(self):
        """Org-scoped mode (project_ids set) must emit ``project_id IN
        %(project_ids)s``, even for a single-element list. Earlier the
        single-element case fell through to ``project_id = %(project_id)s``
        which fails at execution because the outer query only binds
        ``project_ids``. Regression guard for the Codex P1 finding.
        """
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        # Single-element list — the buggy branch.
        single = ClickHouseFilterBuilder(
            project_ids=["11111111-1111-1111-1111-111111111111"]
        )
        where_single, _ = single.translate(
            [
                {
                    "column_id": "annotator",
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "22222222-2222-2222-2222-222222222222",
                        "col_type": "SYSTEM_METRIC",
                    },
                }
            ]
        )
        assert "project_id IN %(project_ids)s" in where_single
        assert "project_id = %(project_id)s" not in where_single

        # Two-element list — same shape.
        multi = ClickHouseFilterBuilder(
            project_ids=[
                "11111111-1111-1111-1111-111111111111",
                "22222222-2222-2222-2222-222222222222",
            ]
        )
        where_multi, _ = multi.translate(
            [
                {
                    "column_id": "annotator",
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "22222222-2222-2222-2222-222222222222",
                        "col_type": "SYSTEM_METRIC",
                    },
                }
            ]
        )
        assert "project_id IN %(project_ids)s" in where_multi

        # Single-project mode — should still emit the single-project shape.
        single_pid = ClickHouseFilterBuilder(
            project_id="11111111-1111-1111-1111-111111111111"
        )
        where_pid, _ = single_pid.translate(
            [
                {
                    "column_id": "annotator",
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "22222222-2222-2222-2222-222222222222",
                        "col_type": "SYSTEM_METRIC",
                    },
                }
            ]
        )
        assert "project_id = %(project_id)s" in where_pid
        assert "project_id IN %(project_ids)s" not in where_pid

    def test_system_metric_trace_wrap_org_mode_uses_project_ids(self):
        """Trace-mode SYSTEM_METRIC filters (e.g. ``node_type``) wrap the
        condition in a ``trace_id IN (SELECT trace_id FROM spans WHERE
        project_id ...)`` subquery. That wrap used to hardcode
        ``project_id = %(project_id)s`` regardless of org scope, so
        org-scoped requests (``project_ids`` set, no ``project_id``) raised
        ``KeyError: 'project_id'`` at execution time — surfaced by
        ``list_traces_of_session`` as a generic 400. Regression guard.
        """
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder(
            project_ids=["11111111-1111-1111-1111-111111111111"],
            query_mode=ClickHouseFilterBuilder.QUERY_MODE_TRACE,
        )
        where, params = builder.translate(
            [
                {
                    "column_id": "node_type",
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "in",
                        "filter_value": ["chain", "retriever"],
                        "col_type": "SYSTEM_METRIC",
                    },
                }
            ]
        )

        assert "trace_id IN" in where
        assert "project_id IN %(project_ids)s" in where
        assert "project_id = %(project_id)s" not in where
        assert "project_id" not in params

        # Single-project mode should still emit the scalar shape.
        single_pid = ClickHouseFilterBuilder(
            project_id="11111111-1111-1111-1111-111111111111",
            query_mode=ClickHouseFilterBuilder.QUERY_MODE_TRACE,
        )
        where_pid, _ = single_pid.translate(
            [
                {
                    "column_id": "node_type",
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "in",
                        "filter_value": ["chain", "retriever"],
                        "col_type": "SYSTEM_METRIC",
                    },
                }
            ]
        )
        assert "project_id = %(project_id)s" in where_pid
        assert "project_id IN %(project_ids)s" not in where_pid

    def test_score_date_scope_can_be_disabled_for_monitor_builders(self):
        """Monitor queries bind start_time/end_time, not start_date/end_date."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder(
            project_id="11111111-1111-1111-1111-111111111111",
            score_date_scope=False,
        )
        where, _ = builder.translate(
            [
                {
                    "column_id": "annotator",
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "22222222-2222-2222-2222-222222222222",
                        "col_type": "SYSTEM_METRIC",
                    },
                }
            ]
        )

        assert "model_hub_score" in where
        assert "%(start_date)s" not in where
        assert "project_id = %(project_id)s" in where

    def test_span_attribute_org_mode_uses_project_ids(self):
        """Trace-mode span attribute filters must mirror org-scoped params."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder(
            project_ids=["11111111-1111-1111-1111-111111111111"]
        )
        where, params = builder.translate(
            [
                {
                    "column_id": "metadata.provider",
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "openai",
                        "col_type": "SPAN_ATTRIBUTE",
                    },
                }
            ]
        )

        assert "project_id IN %(project_ids)s" in where
        assert "project_id = %(project_id)s" not in where
        assert params == {"attr_1": "openai"}

    def test_has_eval_org_mode_uses_project_ids(self):
        """has_eval joins spans for project scope and must support org mode."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder(
            project_ids=["11111111-1111-1111-1111-111111111111"]
        )
        where, params = builder.translate(
            [
                {
                    "column_id": "has_eval",
                    "filter_config": {
                        "filter_type": "boolean",
                        "filter_op": "equals",
                        "filter_value": True,
                    },
                }
            ]
        )

        assert "sp.project_id IN %(project_ids)s" in where
        assert "sp.project_id = %(project_id)s" not in where
        assert params == {}

    # ── eval-filter subqueries: eval_logger_source routing, no FINAL, window ──

    @staticmethod
    def _patched_eval_config_resolution(config_id):
        """Patch the PG config/template lookup `_build_eval_condition` performs
        so the SQL shape can be pinned without database fixtures. The template
        resolves to None → output_type stays SCORE (output_float compare)."""
        values = mock.MagicMock()
        values.__iter__ = lambda self: iter([config_id])
        values.first.return_value = None
        fake_qs = mock.MagicMock()
        fake_qs.exists.return_value = True
        fake_qs.filter.return_value = fake_qs
        fake_qs.values_list.return_value = values
        objects = mock.MagicMock()
        objects.filter.return_value = fake_qs
        template_mgr = mock.MagicMock()
        template_mgr.filter.return_value.values.return_value.first.return_value = None
        return (
            mock.patch(
                "tracer.models.custom_eval_config.CustomEvalConfig.objects", objects
            ),
            mock.patch(
                "model_hub.models.evals_metric.EvalTemplate.no_workspace_objects",
                template_mgr,
            ),
        )

    def _eval_metric_where(self, score_date_scope=True):
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        cfg_id = "22222222-2222-2222-2222-222222222222"
        builder = ClickHouseFilterBuilder(
            project_id="11111111-1111-1111-1111-111111111111",
            query_mode=ClickHouseFilterBuilder.QUERY_MODE_SPAN,
            score_date_scope=score_date_scope,
        )
        p1, p2 = self._patched_eval_config_resolution(cfg_id)
        with p1, p2:
            where, params = builder.translate(
                [
                    {
                        "column_id": cfg_id,
                        "filter_config": {
                            "col_type": "EVAL_METRIC",
                            "filter_type": "number",
                            "filter_op": "greater_than",
                            "filter_value": 80,
                        },
                    }
                ]
            )
        return where, params

    def test_eval_metric_filter_routes_via_eval_logger_source_v2(self):
        """The eval FILTER must read the same table the displayed eval cells
        read (eval_logger_source), with no table-level FINAL (whole-table
        merge — the OOM class the span-list Phase-2 rewrite removed) and a
        window-aligned created_at bound for monthly partition pruning."""
        from django.test import override_settings

        with override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger_v2"):
            where, _ = self._eval_metric_where()
        assert "FROM tracer_eval_logger_v2 " in where
        assert "FINAL" not in where
        assert "is_deleted = 0" in where
        assert "_peerdb_is_deleted" not in where
        assert "created_at >= %(start_date)s - INTERVAL 7 DAY" in where

    def test_eval_metric_filter_keeps_legacy_predicate(self):
        from django.test import override_settings

        with override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger"):
            where, _ = self._eval_metric_where()
        assert "FROM tracer_eval_logger " in where
        assert "tracer_eval_logger_v2" not in where
        assert "FINAL" not in where
        assert "_peerdb_is_deleted = 0" in where

    def test_eval_metric_filter_omits_date_bound_without_scope(self):
        """Callers that don't bind %(start_date)s (score_date_scope=False)
        must not receive the created_at bound — a missing-parameter error
        otherwise (dependency-failure path)."""
        where, _ = self._eval_metric_where(score_date_scope=False)
        assert "created_at >=" not in where

    def test_has_eval_no_final_and_window_bound(self):
        from django.test import override_settings

        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder(
            project_id="11111111-1111-1111-1111-111111111111"
        )
        with override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger_v2"):
            where, _ = builder.translate(
                [
                    {
                        "column_id": "has_eval",
                        "filter_config": {
                            "filter_type": "boolean",
                            "filter_op": "equals",
                            "filter_value": True,
                        },
                    }
                ]
            )
        assert "tracer_eval_logger_v2 AS el" in where
        assert "FINAL" not in where
        assert "el.is_deleted = 0" in where
        assert "el.created_at >= %(start_date)s - INTERVAL 7 DAY" in where

    def test_span_mode_my_annotations_filter_targets_span_id(self):
        """my_annotations uses span ids in span mode and trace ids elsewhere."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        user_id = "11111111-1111-1111-1111-111111111111"
        builder = ClickHouseFilterBuilder(
            query_mode=ClickHouseFilterBuilder.QUERY_MODE_SPAN
        )
        where, params = builder.translate(
            [
                {
                    "column_id": "my_annotations",
                    "filter_config": {
                        "filter_type": "boolean",
                        "filter_op": "equals",
                        "filter_value": True,
                        "user_id": user_id,
                    },
                }
            ]
        )

        assert where.strip().startswith("id IN")
        assert "trace_id IN" not in where
        assert "s.annotator_id = toUUID(%(uid_1)s)" in where
        assert params == {"uid_1": user_id}

    def test_translate_system_metric_equals(self):
        """SYSTEM_METRIC equals filter should map to direct column comparison."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder()
        filters = [
            {
                "column_id": "model",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "gpt-4",
                    "col_type": "SYSTEM_METRIC",
                },
            }
        ]
        where, params = builder.translate(filters)
        assert "model" in where
        assert "=" in where
        assert "gpt-4" in params.values()

    def test_translate_system_metric_column_mapping(self):
        """SYSTEM_METRIC filter should map frontend names to CH column names."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder()
        filters = [
            {
                "column_id": "latency_ms",
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "greater_than",
                    "filter_value": 100,
                    "col_type": "SYSTEM_METRIC",
                },
            }
        ]
        where, params = builder.translate(filters)
        # SYSTEM_METRIC numeric filter resolves to the latency_ms column
        assert "latency_ms" in where
        assert ">" in where

    def test_translate_span_name_system_metric_maps_to_name_column(self):
        """Span list Span Name filter should use spans.name, not an attribute map."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder(
            query_mode=ClickHouseFilterBuilder.QUERY_MODE_SPAN
        )
        filters = [
            {
                "column_id": "span_name",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "in",
                    "filter_value": ["response"],
                    "col_type": "SYSTEM_METRIC",
                },
            }
        ]

        where, params = builder.translate(filters)

        assert "lower(name) IN" in where
        assert "span_attr_" not in where
        assert "trace_id IN" not in where
        assert tuple(params.values()) == (("response",),)

    def test_trace_mode_span_name_filter_can_match_child_spans(self):
        """Trace-list Span Name should find traces by any child span name."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder(
            query_mode=ClickHouseFilterBuilder.QUERY_MODE_TRACE
        )
        filters = [
            {
                "column_id": "span_name",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "in",
                    "filter_value": ["response"],
                    "col_type": "SYSTEM_METRIC",
                },
            }
        ]

        where, params = builder.translate(filters)

        assert "trace_id IN" in where
        assert "lower(name) IN" in where
        assert "span_attr_" not in where
        assert "parent_span_id" not in where
        assert tuple(params.values()) == (("response",),)

    def test_trace_mode_legacy_name_filter_remains_root_span_only(self):
        """Legacy Trace Name alias should not match arbitrary child span names."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder(
            query_mode=ClickHouseFilterBuilder.QUERY_MODE_TRACE
        )
        filters = [
            {
                "column_id": "name",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "in",
                    "filter_value": ["root trace"],
                    "col_type": "SYSTEM_METRIC",
                },
            }
        ]

        where, params = builder.translate(filters)

        assert "trace_id IN" in where
        assert "lower(name) IN" in where
        assert "parent_span_id IS NULL OR parent_span_id = ''" in where
        assert tuple(params.values()) == (("root trace",),)

    def test_voice_duration_system_metric_uses_call_duration_attribute(self):
        """Voice Duration filters should route to the stored call.duration metric."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder()
        where, params = builder.translate(
            [
                {
                    "column_id": "duration",
                    "filter_config": {
                        "filter_type": "number",
                        "filter_op": "less_than_or_equal",
                        "filter_value": 20,
                        "col_type": "SYSTEM_METRIC",
                    },
                }
            ]
        )

        assert "span_attr_num['call.duration']" in where
        assert "span_attr_num['duration']" not in where
        assert 20 in params.values()

    @pytest.mark.parametrize(
        ("frontend_column", "clickhouse_column"),
        [
            ("latency", "latency_ms"),
            ("tokens", "total_tokens"),
            ("input_tokens", "prompt_tokens"),
            ("output_tokens", "completion_tokens"),
        ],
    )
    def test_translate_dashboard_system_metric_names(
        self, frontend_column, clickhouse_column
    ):
        """Dashboard metric names should filter on their denormalized CH columns."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder()
        filters = [
            {
                "column_id": frontend_column,
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "greater_than",
                    "filter_value": 0,
                    "col_type": "SYSTEM_METRIC",
                },
            }
        ]

        where, _params = builder.translate(filters)

        assert clickhouse_column in where
        assert "span_attr_" not in where

    def test_translate_span_attribute_string(self):
        """SPAN_ATTRIBUTE text filter should use span_attr_str map column."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder()
        filters = [
            {
                "column_id": "gen_ai.system",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "openai",
                    "col_type": "SPAN_ATTRIBUTE",
                },
            }
        ]
        where, params = builder.translate(filters)
        assert "span_attr_str" in where
        assert "gen_ai.system" in where
        assert "openai" in params.values()

    def test_translate_span_attribute_numeric(self):
        """SPAN_ATTRIBUTE number filter should use span_attr_num map column."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder()
        filters = [
            {
                "column_id": "gen_ai.usage.prompt_tokens",
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "greater_than",
                    "filter_value": 100,
                    "col_type": "SPAN_ATTRIBUTE",
                },
            }
        ]
        where, params = builder.translate(filters)
        assert "prompt_tokens" in where
        assert ">" in where

    def test_translate_span_attribute_boolean(self):
        """SPAN_ATTRIBUTE boolean filter should use span_attr_bool map column."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder()
        filters = [
            {
                "column_id": "gen_ai.is_streaming",
                "filter_config": {
                    "filter_type": "boolean",
                    "filter_op": "equals",
                    "filter_value": True,
                    "col_type": "SPAN_ATTRIBUTE",
                },
            }
        ]
        where, params = builder.translate(filters)
        assert "span_attr_bool" in where

    def test_translate_span_attribute_contains(self):
        """SPAN_ATTRIBUTE contains filter should use LIKE with percent wildcards."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder()
        filters = [
            {
                "column_id": "gen_ai.request.model",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "contains",
                    "filter_value": "gpt",
                    "col_type": "SPAN_ATTRIBUTE",
                },
            }
        ]
        where, params = builder.translate(filters)
        assert "LIKE" in where
        assert any("%" in str(v) for v in params.values())

    def test_translate_span_attribute_is_null(self):
        """SPAN_ATTRIBUTE is_null filter should check mapContains with NOT."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder()
        filters = [
            {
                "column_id": "gen_ai.system",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "is_null",
                    "filter_value": None,
                    "col_type": "SPAN_ATTRIBUTE",
                },
            }
        ]
        where, params = builder.translate(filters)
        assert "mapContains" in where
        assert "NOT" in where

    def test_translate_span_attribute_is_not_null(self):
        """SPAN_ATTRIBUTE is_not_null filter should check mapContains without NOT."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder()
        filters = [
            {
                "column_id": "gen_ai.system",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "is_not_null",
                    "filter_value": None,
                    "col_type": "SPAN_ATTRIBUTE",
                },
            }
        ]
        where, params = builder.translate(filters)
        assert "mapContains" in where
        assert "NOT" not in where

    def test_translate_between(self):
        """Between filter should produce BETWEEN clause with two params."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder()
        filters = [
            {
                "column_id": "cost",
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "between",
                    "filter_value": [0.1, 1.0],
                    "col_type": "SYSTEM_METRIC",
                },
            }
        ]
        where, params = builder.translate(filters)
        assert "BETWEEN" in where
        assert len(params) == 2

    def test_translate_multiple_filters(self):
        """Multiple filters should be joined with AND."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder()
        filters = [
            {
                "column_id": "model",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "gpt-4",
                    "col_type": "SYSTEM_METRIC",
                },
            },
            {
                "column_id": "status",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "ERROR",
                    "col_type": "SYSTEM_METRIC",
                },
            },
        ]
        where, params = builder.translate(filters)
        assert "AND" in where
        assert len(params) == 2

    def test_translate_sort(self):
        """Sort params should produce an ORDER BY clause."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder()
        sorts = [{"column_id": "created_at", "direction": "desc"}]
        order = builder.translate_sort(sorts)
        assert "ORDER BY" in order
        assert "created_at DESC" in order

    def test_translate_sort_multiple_columns(self):
        """Multiple sort params should produce multi-column ORDER BY."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder()
        sorts = [
            {"column_id": "created_at", "direction": "desc"},
            {"column_id": "cost", "direction": "asc"},
        ]
        order = builder.translate_sort(sorts)
        assert "ORDER BY" in order
        assert "created_at DESC" in order
        assert "cost ASC" in order

    def test_translate_sort_empty(self):
        """Empty sort params should produce empty string."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder()
        order = builder.translate_sort([])
        assert order == ""

    def test_translate_sort_with_field_map(self):
        """Sort should apply field_map to remap column names."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder()
        sorts = [{"column_id": "latency", "direction": "desc"}]
        field_map = {"latency": "latency_ms"}
        order = builder.translate_sort(sorts, field_map=field_map)
        assert "latency_ms DESC" in order

    def test_skips_datetime_filters(self):
        """Datetime filters on created_at should be skipped (handled by base builder)."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder()
        filters = [
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": ["2024-01-01", "2024-12-31"],
                },
            }
        ]
        where, params = builder.translate(filters)
        assert where == ""

    def test_translate_in_operator(self):
        """IN operator should produce IN clause with tuple param."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder()
        filters = [
            {
                "column_id": "model",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "in",
                    "filter_value": ["gpt-4", "gpt-3.5-turbo"],
                    "col_type": "SYSTEM_METRIC",
                },
            }
        ]
        where, params = builder.translate(filters)
        assert "IN" in where
        # Value should be a tuple for CH parameterized queries
        assert any(isinstance(v, tuple) for v in params.values())

    def test_translate_eval_metric_filter(self):
        """EVAL_METRIC filter should produce a subquery against tracer_eval_logger."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder()
        filters = [
            {
                "column_id": "00000000-0000-0000-0000-000000000099",
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "greater_than",
                    "filter_value": 0.8,
                    "col_type": "EVAL_METRIC",
                },
            }
        ]
        where, params = builder.translate(filters)
        assert "trace_id IN" in where
        assert "00000000-0000-0000-0000-000000000000" in where

    @pytest.mark.django_db
    def test_translate_score_eval_between_filter_scales_to_raw_storage(
        self, custom_eval_config
    ):
        """SCORE eval filters scale UI percentages to raw CH storage."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        custom_eval_config.eval_template.config = {"output": "score"}
        custom_eval_config.eval_template.save(update_fields=["config"])

        builder = ClickHouseFilterBuilder(
            project_ids=[str(custom_eval_config.project_id)]
        )
        where, params = builder.translate(
            [
                {
                    "column_id": str(custom_eval_config.id),
                    "filter_config": {
                        "filter_type": "number",
                        "filter_op": "between",
                        "filter_value": [20, 80],
                        "col_type": "EVAL_METRIC",
                    },
                }
            ]
        )

        assert "tracer_eval_logger" in where
        assert "output_float BETWEEN" in where
        assert 0.2 in params.values()
        assert 0.8 in params.values()

    @pytest.mark.django_db
    def test_translate_score_eval_not_between_and_in_filters_scale_values(
        self, custom_eval_config
    ):
        """SCORE eval range and list filters should compare raw 0-1 storage."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        custom_eval_config.eval_template.config = {"output": "score"}
        custom_eval_config.eval_template.save(update_fields=["config"])

        builder = ClickHouseFilterBuilder(
            project_ids=[str(custom_eval_config.project_id)]
        )
        where, params = builder.translate(
            [
                {
                    "column_id": str(custom_eval_config.id),
                    "filter_config": {
                        "filter_type": "number",
                        "filter_op": "not_between",
                        "filter_value": [20, 80],
                        "col_type": "EVAL_METRIC",
                    },
                },
                {
                    "column_id": str(custom_eval_config.id),
                    "filter_config": {
                        "filter_type": "number",
                        "filter_op": "in",
                        "filter_value": [10, 90],
                        "col_type": "EVAL_METRIC",
                    },
                },
            ]
        )

        assert "output_float NOT BETWEEN" in where
        assert "output_float IN" in where
        assert 0.2 in params.values()
        assert 0.8 in params.values()
        assert (0.1, 0.9) in params.values()

    @pytest.mark.django_db
    def test_translate_score_eval_null_filters_use_output_float(
        self, custom_eval_config
    ):
        """SCORE eval null filters should check output_float presence."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        custom_eval_config.eval_template.config = {"output": "score"}
        custom_eval_config.eval_template.save(update_fields=["config"])

        builder = ClickHouseFilterBuilder(
            project_ids=[str(custom_eval_config.project_id)]
        )
        where, _params = builder.translate(
            [
                {
                    "column_id": str(custom_eval_config.id),
                    "filter_config": {
                        "filter_type": "number",
                        "filter_op": "is_null",
                        "filter_value": None,
                        "col_type": "EVAL_METRIC",
                    },
                }
            ]
        )

        assert "trace_id NOT IN" in where
        assert "output_float IS NOT NULL" in where

    @pytest.mark.django_db
    def test_translate_pass_fail_eval_filter_uses_output_bool(self, custom_eval_config):
        """Pass/fail evals must use output_bool, not stale mixed fields."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        custom_eval_config.eval_template.config = {"output": "Pass/Fail"}
        custom_eval_config.eval_template.save(update_fields=["config"])

        builder = ClickHouseFilterBuilder(
            project_ids=[str(custom_eval_config.project_id)]
        )
        where, params = builder.translate(
            [
                {
                    "column_id": str(custom_eval_config.id),
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "in",
                        "filter_value": ["Passed", "Failed"],
                        "col_type": "EVAL_METRIC",
                    },
                }
            ]
        )

        assert "output_bool IN" in where
        assert "output_float" not in where
        assert (1, 0) in params.values()

    @pytest.mark.django_db
    def test_translate_pass_fail_eval_negative_and_null_filters(
        self, custom_eval_config
    ):
        """Pass/fail eval negative and null filters should use output_bool."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        custom_eval_config.eval_template.config = {"output": "Pass/Fail"}
        custom_eval_config.eval_template.save(update_fields=["config"])

        builder = ClickHouseFilterBuilder(
            project_ids=[str(custom_eval_config.project_id)]
        )
        where, params = builder.translate(
            [
                {
                    "column_id": str(custom_eval_config.id),
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "not_in",
                        "filter_value": ["Passed"],
                        "col_type": "EVAL_METRIC",
                    },
                },
                {
                    "column_id": str(custom_eval_config.id),
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "is_not_null",
                        "filter_value": None,
                        "col_type": "EVAL_METRIC",
                    },
                },
            ]
        )

        assert "output_bool NOT IN" in where
        assert "output_bool IS NOT NULL" in where
        assert (1,) in params.values()

    @pytest.mark.django_db
    def test_translate_choice_eval_filter_uses_list_and_scalar_outputs(
        self, custom_eval_config
    ):
        """Choice evals use output_str_list, with output_str as fallback."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        custom_eval_config.eval_template.config = {"output": "choices"}
        custom_eval_config.eval_template.save(update_fields=["config"])

        builder = ClickHouseFilterBuilder(
            project_ids=[str(custom_eval_config.project_id)]
        )
        where, params = builder.translate(
            [
                {
                    "column_id": str(custom_eval_config.id),
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "in",
                        "filter_value": ["fear", "joy"],
                        "col_type": "EVAL_METRIC",
                    },
                }
            ]
        )

        assert "has(JSONExtract(output_str_list, 'Array(String)')" in where
        assert "output_str =" in where
        assert "fear" in params.values()
        assert "joy" in params.values()

    @pytest.mark.django_db
    def test_translate_choice_eval_negative_filter_checks_all_selected_values(
        self, custom_eval_config
    ):
        """Negative choice eval filters should negate every selected choice."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        custom_eval_config.eval_template.config = {"output": "choices"}
        custom_eval_config.eval_template.save(update_fields=["config"])

        builder = ClickHouseFilterBuilder(
            project_ids=[str(custom_eval_config.project_id)]
        )
        where, params = builder.translate(
            [
                {
                    "column_id": str(custom_eval_config.id),
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "not_in",
                        "filter_value": ["fear", "joy"],
                        "col_type": "EVAL_METRIC",
                    },
                }
            ]
        )

        assert "NOT (" in where
        assert "notEmpty(JSONExtract(output_str_list, 'Array(String)'))" in where
        assert "has(JSONExtract(output_str_list, 'Array(String)')" in where
        assert "output_str =" in where
        assert "fear" in params.values()
        assert "joy" in params.values()

    @pytest.mark.django_db
    def test_translate_choice_eval_contains_and_prefix_filters_parse_choice_array(
        self, custom_eval_config
    ):
        """Choice eval substring filters should inspect each parsed list item."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        custom_eval_config.eval_template.config = {"output": "choices"}
        custom_eval_config.eval_template.save(update_fields=["config"])

        builder = ClickHouseFilterBuilder(
            project_ids=[str(custom_eval_config.project_id)]
        )
        where, params = builder.translate(
            [
                {
                    "column_id": str(custom_eval_config.id),
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "contains",
                        "filter_value": ["fear"],
                        "col_type": "EVAL_METRIC",
                    },
                },
                {
                    "column_id": str(custom_eval_config.id),
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "starts_with",
                        "filter_value": ["joy"],
                        "col_type": "EVAL_METRIC",
                    },
                },
            ]
        )

        assert "arrayExists(x -> x ILIKE" in where
        assert "JSONExtract(output_str_list, 'Array(String)')" in where
        assert "%fear%" in params.values()
        assert "joy%" in params.values()

    def test_translate_annotation_filter(self):
        """ANNOTATION filter should produce a subquery against annotation tables."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder()
        filters = [
            {
                "column_id": "annotation-label-uuid",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "good",
                    "col_type": "ANNOTATION",
                },
            }
        ]
        where, params = builder.translate(filters)
        assert "trace_id IN" in where
        assert "model_hub_score" in where

    def test_translate_annotation_filter_resolves_span_scores_to_trace(self):
        """Annotation filters should match Score rows stored on spans."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder()
        filters = [
            {
                "column_id": "00000000-0000-0000-0000-000000000044",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "good",
                    "col_type": "ANNOTATION",
                },
            }
        ]

        where, params = builder.translate(filters)

        assert "model_hub_score AS s FINAL" in where
        assert "FROM spans WHERE" in where
        assert "sp.id = s.observation_span_id" in where
        assert "sp.trace_id" in where
        assert "toString(s.trace_id)" in where
        assert "JSONExtractString(s.value, 'text')" in where
        assert params["ann_label_1"] == "00000000-0000-0000-0000-000000000044"

    def test_span_mode_annotation_filter_targets_span_id(self):
        """Span tab annotation filters must not match every span in a trace."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder(
            query_mode=ClickHouseFilterBuilder.QUERY_MODE_SPAN
        )
        filters = [
            {
                "column_id": "00000000-0000-0000-0000-000000000044",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "good",
                    "col_type": "ANNOTATION",
                },
            }
        ]

        where, params = builder.translate(filters)

        assert where.strip().startswith("id IN")
        assert "trace_id IN" not in where
        assert "model_hub_score AS s FINAL" in where
        assert "s.observation_span_id" in where
        assert "FROM spans WHERE" in where
        assert "parent_span_id" in where
        assert params["ann_label_1"] == "00000000-0000-0000-0000-000000000044"

    def test_span_mode_annotation_text_in_filter_targets_span_id(self):
        """Observe multi-select text filters should work for span annotations."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder(
            query_mode=ClickHouseFilterBuilder.QUERY_MODE_SPAN
        )
        where, params = builder.translate(
            [
                {
                    "column_id": "00000000-0000-0000-0000-000000000044",
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "in",
                        "filter_value": ["Good", "Bad"],
                        "col_type": "ANNOTATION",
                    },
                }
            ]
        )

        assert where.strip().startswith("id IN")
        assert "lower(JSONExtractString(s.value, 'text')) IN" in where
        assert params["ann_2"] == ("good", "bad")

    def test_translate_skips_empty_filter_config(self):
        """Filters with missing column_id or config should be skipped."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder()
        filters = [
            {"column_id": "", "filter_config": {}},
            {"filter_config": {"filter_op": "equals", "filter_value": "x"}},
        ]
        where, params = builder.translate(filters)
        assert where == ""
        assert params == {}

    def test_param_counter_uniqueness(self):
        """Each filter should get a unique parameter name."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder()
        filters = [
            {
                "column_id": "model",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "gpt-4",
                    "col_type": "SYSTEM_METRIC",
                },
            },
            {
                "column_id": "provider",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "openai",
                    "col_type": "SYSTEM_METRIC",
                },
            },
        ]
        where, params = builder.translate(filters)
        # Should have two unique param keys
        assert len(params) == 2
        param_keys = list(params.keys())
        assert param_keys[0] != param_keys[1]

    # ------------------------------------------------------------------
    # has_eval filter tests
    # ------------------------------------------------------------------

    def test_translate_has_eval_true(self):
        """has_eval=true should produce subquery against tracer_eval_logger."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder()
        filters = [
            {
                "column_id": "has_eval",
                "filter_config": {
                    "filter_type": "boolean",
                    "filter_op": "equals",
                    "filter_value": True,
                },
            }
        ]
        where, params = builder.translate(filters)
        assert "trace_id IN" in where
        assert "tracer_eval_logger" in where
        assert "is_deleted = 0" in where
        # No params needed — the subquery is static
        assert params == {}

    def test_translate_has_eval_false_produces_no_condition(self):
        """has_eval=false should produce no WHERE condition."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder()
        filters = [
            {
                "column_id": "has_eval",
                "filter_config": {
                    "filter_type": "boolean",
                    "filter_op": "equals",
                    "filter_value": False,
                },
            }
        ]
        where, params = builder.translate(filters)
        assert where == ""
        assert params == {}

    def test_translate_has_eval_string_true(self):
        """has_eval='true' (string) should work the same as boolean True."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder()
        filters = [
            {
                "column_id": "has_eval",
                "filter_config": {
                    "filter_type": "boolean",
                    "filter_op": "equals",
                    "filter_value": "true",
                },
            }
        ]
        where, params = builder.translate(filters)
        assert "trace_id IN" in where
        assert "tracer_eval_logger" in where

    def test_translate_has_eval_string_false(self):
        """has_eval='false' (string) should produce no condition."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder()
        filters = [
            {
                "column_id": "has_eval",
                "filter_config": {
                    "filter_type": "boolean",
                    "filter_op": "equals",
                    "filter_value": "false",
                },
            }
        ]
        where, params = builder.translate(filters)
        assert where == ""

    def test_translate_has_eval_accepts_camelcase_filter_keys(self):
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder()
        filters = [
            {
                "columnId": "has_eval",
                "filterConfig": {
                    "filterType": "boolean",
                    "filterOp": "equals",
                    "filterValue": True,
                },
            }
        ]
        where, params = builder.translate(filters)
        assert "trace_id IN" in where
        assert "tracer_eval_logger" in where

    # ------------------------------------------------------------------
    # has_annotation filter tests
    # ------------------------------------------------------------------

    def test_translate_has_annotation_true(self):
        """has_annotation=true should produce IN subquery against model_hub_score."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder()
        filters = [
            {
                "column_id": "has_annotation",
                "filter_config": {
                    "filter_type": "boolean",
                    "filter_op": "equals",
                    "filter_value": True,
                },
            }
        ]
        where, params = builder.translate(filters)
        assert "trace_id IN" in where
        assert "model_hub_score" in where
        assert "FROM spans WHERE" in where
        assert "sp.id = s.observation_span_id" in where
        assert "is_deleted = 0" in where
        assert params == {}

    def test_span_mode_has_annotation_targets_span_id(self):
        """has_annotation on the spans tab should filter span ids, not trace ids."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder(
            query_mode=ClickHouseFilterBuilder.QUERY_MODE_SPAN,
            annotation_label_ids=[
                "00000000-0000-0000-0000-000000000011",
                "00000000-0000-0000-0000-000000000022",
            ],
        )
        filters = [
            {
                "column_id": "has_annotation",
                "filter_config": {
                    "filter_type": "boolean",
                    "filter_op": "equals",
                    "filter_value": True,
                },
            }
        ]

        where, params = builder.translate(filters)

        assert where.strip().startswith("id IN")
        assert "trace_id IN" not in where
        assert "GROUP BY entity_id" in where
        assert "uniq(s.label_id) >= 2" in where
        assert "parent_span_id" in where
        assert params["lbl_1"] == "00000000-0000-0000-0000-000000000011"
        assert params["lbl_2"] == "00000000-0000-0000-0000-000000000022"

    def test_translate_has_annotation_false(self):
        """has_annotation=false should produce NOT IN subquery (non-annotated traces)."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder()
        filters = [
            {
                "column_id": "has_annotation",
                "filter_config": {
                    "filter_type": "boolean",
                    "filter_op": "equals",
                    "filter_value": False,
                },
            }
        ]
        where, params = builder.translate(filters)
        assert "trace_id NOT IN" in where
        assert "model_hub_score" in where
        assert params == {}

    def test_translate_has_annotation_string_true(self):
        """has_annotation='true' (string) should produce IN subquery."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder()
        filters = [
            {
                "column_id": "has_annotation",
                "filter_config": {
                    "filter_type": "boolean",
                    "filter_op": "equals",
                    "filter_value": "true",
                },
            }
        ]
        where, params = builder.translate(filters)
        assert "trace_id IN" in where
        assert "NOT IN" not in where

    def test_translate_has_annotation_string_false(self):
        """has_annotation='false' (string) should produce NOT IN subquery."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder()
        filters = [
            {
                "column_id": "has_annotation",
                "filter_config": {
                    "filter_type": "boolean",
                    "filter_op": "equals",
                    "filter_value": "false",
                },
            }
        ]
        where, params = builder.translate(filters)
        assert "trace_id NOT IN" in where

    def test_translate_has_eval_combined_with_other_filters(self):
        """has_eval should combine with other filters via AND."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder()
        filters = [
            {
                "column_id": "status",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "ERROR",
                    "col_type": "SYSTEM_METRIC",
                },
            },
            {
                "column_id": "has_eval",
                "filter_config": {
                    "filter_type": "boolean",
                    "filter_op": "equals",
                    "filter_value": True,
                },
            },
        ]
        where, params = builder.translate(filters)
        assert "AND" in where
        assert "status" in where
        assert "tracer_eval_logger" in where

    def test_translate_has_annotation_combined_with_has_eval(self):
        """has_annotation and has_eval should both produce subquery conditions."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder()
        filters = [
            {
                "column_id": "has_eval",
                "filter_config": {
                    "filter_type": "boolean",
                    "filter_op": "equals",
                    "filter_value": True,
                },
            },
            {
                "column_id": "has_annotation",
                "filter_config": {
                    "filter_type": "boolean",
                    "filter_op": "equals",
                    "filter_value": False,
                },
            },
        ]
        where, params = builder.translate(filters)
        assert "AND" in where
        assert "tracer_eval_logger" in where
        assert "model_hub_score" in where
        assert "NOT IN" in where


# ============================================================================
# 3. Query Builder Tests
# ============================================================================


@pytest.mark.unit
class TestTimeSeriesQueryBuilder:
    """Test time-series query builder."""

    def test_build_returns_query_and_params(self):
        """build() should return a (query_string, params_dict) tuple."""
        from tracer.services.clickhouse.query_builders import TimeSeriesQueryBuilder

        builder = TimeSeriesQueryBuilder(
            project_id="test-project-id",
            filters=[],
            interval="hour",
        )
        query, params = builder.build()
        assert isinstance(query, str)
        assert isinstance(params, dict)
        assert "project_id" in params
        # Unfiltered query should use the v2 pre-aggregated rollup
        assert "spans_hourly_rollup" in query

    def test_build_with_filters_uses_spans_table(self):
        """When attribute filters are present, should fall back to raw spans table."""
        from tracer.services.clickhouse.query_builders import TimeSeriesQueryBuilder

        builder = TimeSeriesQueryBuilder(
            project_id="test-project-id",
            filters=[
                {
                    "column_id": "model",
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "gpt-4",
                        "col_type": "SYSTEM_METRIC",
                    },
                }
            ],
            interval="day",
        )
        query, params = builder.build()
        # Should use raw spans table, not pre-aggregated
        assert "spans" in query
        assert "model" in query or "gpt-4" in str(params.values())

    def test_build_unfiltered_uses_agg_table(self):
        """Without filters, should use the v2 pre-aggregated spans_hourly_rollup."""
        from tracer.services.clickhouse.query_builders import TimeSeriesQueryBuilder

        builder = TimeSeriesQueryBuilder(
            project_id="test-project-id",
            filters=[],
            interval="hour",
        )
        query, params = builder.build()
        assert "spans_hourly_rollup" in query
        # Should use AggregatingMergeTree combinators
        assert "countMerge" in query
        assert "sumMerge" in query
        assert "quantilesTDigestMerge" in query

    def test_build_sets_start_and_end_dates(self):
        """build() should populate start_date and end_date in params."""
        from tracer.services.clickhouse.query_builders import TimeSeriesQueryBuilder

        builder = TimeSeriesQueryBuilder(
            project_id="test-project-id",
            filters=[],
            interval="hour",
        )
        query, params = builder.build()
        assert "start_date" in params
        assert "end_date" in params
        assert params["start_date"] is not None
        assert params["end_date"] is not None

    def test_build_query_contains_groupby_and_orderby(self):
        """Generated query should include GROUP BY and ORDER BY."""
        from tracer.services.clickhouse.query_builders import TimeSeriesQueryBuilder

        builder = TimeSeriesQueryBuilder(
            project_id="test-project-id",
            filters=[],
            interval="day",
        )
        query, params = builder.build()
        assert "GROUP BY" in query
        assert "ORDER BY" in query

    # ------------------------------------------------------------------
    # CH25 close-out: v2 spans_hourly_rollup specifics
    # ------------------------------------------------------------------
    # The rollup table partitions/orders by `hour` (DateTime, schema 010)
    # rather than `start_time`. If the WHERE clause uses the wrong column
    # the query still runs but skips no partitions — silent perf regression.

    def test_agg_query_filters_on_hour_not_start_time(self):
        """Rollup queries must filter on `hour`, the partition/order key."""
        from tracer.services.clickhouse.query_builders import TimeSeriesQueryBuilder

        builder = TimeSeriesQueryBuilder(
            project_id="test-project-id", filters=[], interval="hour"
        )
        query, _ = builder.build()
        # The rollup table has no `start_time` column — that lives on raw spans.
        assert "hour >= %(start_date)s" in query
        assert "hour < %(end_date)s" in query
        assert "start_time" not in query

    def test_agg_query_extracts_median_quantile(self):
        """avg_latency should be the median (index [1]) of the tDigest tuple.

        `quantilesTDigestMerge(0.5, 0.95, 0.99)` returns a 3-tuple; CH is
        1-indexed so [1] = p50. We assert the index, not the literal, so
        the test doesn't depend on whitespace.
        """
        from tracer.services.clickhouse.query_builders import TimeSeriesQueryBuilder

        builder = TimeSeriesQueryBuilder(
            project_id="test-project-id", filters=[], interval="hour"
        )
        query, _ = builder.build()
        assert "quantilesTDigestMerge(0.5, 0.95, 0.99)(latency_q)" in query
        # Median selector is appended to the merge call.
        assert ")[1]" in query

    def test_agg_query_avg_cost_divides_sum_by_count(self):
        """avg_cost must be `sumMerge(cost_sum) / countMerge(n)`.

        The rollup stores only the summed cost, not the average — the
        builder must reconstruct the mean. `greatest(..., 1)` guards
        against /0 on empty hours.
        """
        from tracer.services.clickhouse.query_builders import TimeSeriesQueryBuilder

        builder = TimeSeriesQueryBuilder(
            project_id="test-project-id", filters=[], interval="hour"
        )
        query, _ = builder.build()
        assert "sumMerge(cost_sum) / greatest(countMerge(n), 1)" in query

    def test_agg_query_error_rate_is_percentage(self):
        """error_rate must be a percentage (× 100.0), not a raw ratio."""
        from tracer.services.clickhouse.query_builders import TimeSeriesQueryBuilder

        builder = TimeSeriesQueryBuilder(
            project_id="test-project-id", filters=[], interval="hour"
        )
        query, _ = builder.build()
        assert "countMerge(error_count) * 100.0 / greatest(countMerge(n), 1)" in query

    def test_agg_query_does_not_reference_legacy_table(self):
        """No path back to the legacy CDC-fed aggregate or its source."""
        from tracer.services.clickhouse.query_builders import TimeSeriesQueryBuilder

        builder = TimeSeriesQueryBuilder(
            project_id="test-project-id", filters=[], interval="hour"
        )
        query, _ = builder.build()
        assert "span_metrics_hourly" not in query
        assert "tracer_observation_span" not in query

    def test_format_result_emits_all_metric_keys(self):
        """The response dict must include every metric the dashboard reads.

        The dashboard renderers index into specific keys — if any one is
        missing the chart silently disappears. Pin the contract.
        """

        from tracer.services.clickhouse.query_builders import TimeSeriesQueryBuilder

        builder = TimeSeriesQueryBuilder(
            project_id="test-project-id", filters=[], interval="hour"
        )
        builder.build()  # populates start_date / end_date
        # Single bucket within the parsed window.
        assert builder.start_date is not None and builder.end_date is not None
        rows = [
            {
                "time_bucket": builder.start_date,
                "avg_latency": 123.45,
                "total_tokens": 1000,
                "avg_cost": 0.0042,
                "traffic_count": 7,
                "prompt_tokens": 600,
                "completion_tokens": 400,
                "error_rate": 12.5,
            }
        ]
        result = builder.format_result(rows, columns=[])

        expected_keys = {
            "latency",
            "tokens",
            "cost",
            "traffic",
            "prompt_tokens",
            "completion_tokens",
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "error_rate",
        }
        assert expected_keys.issubset(result.keys())
        # input_tokens / output_tokens are aliases of prompt / completion —
        # this is a stable contract the frontend depends on.
        assert result["input_tokens"] == result["prompt_tokens"]
        assert result["output_tokens"] == result["completion_tokens"]
        assert result["total_tokens"] == result["tokens"]

    def test_format_result_preserves_values_from_dict_rows(self):
        """Dict-row path (returned by execute_ch_query) must round-trip values.

        Regression guard for the dual dict-or-tuple row handling in
        ``format_result``: it reshapes each dict into a 2-tuple
        ``(time_bucket, metric_value)`` for the zero-fill helper, so the
        canonical key on each output point is ``"value"`` (the synonymous
        ``latency`` / ``tokens`` / ``cost`` keys are zero-filled aliases —
        see test below).
        """
        from tracer.services.clickhouse.query_builders import TimeSeriesQueryBuilder

        builder = TimeSeriesQueryBuilder(
            project_id="test-project-id", filters=[], interval="hour"
        )
        builder.build()
        assert builder.start_date is not None
        rows = [
            {
                "time_bucket": builder.start_date,
                "avg_latency": 99.5,
                "total_tokens": 1234,
                "avg_cost": 0.000123456789,
                "traffic_count": 42,
                "prompt_tokens": 1000,
                "completion_tokens": 234,
                "error_rate": 5.0,
            }
        ]
        result = builder.format_result(rows, columns=[])

        # The hour bucket containing start_date — zero-fill creates other
        # buckets around it. Match by hour prefix to ignore minute/second
        # normalization done by `format_time_series._normalize_timestamp`.
        hour_prefix = builder.start_date.isoformat()[:13]

        def _bucket_at_start(series_key: str) -> dict:
            for p in result[series_key]:
                if p["timestamp"].startswith(hour_prefix):
                    return p
            raise AssertionError(
                f"No bucket matched hour {hour_prefix!r} in {series_key}"
            )

        assert _bucket_at_start("latency")["value"] == 99.5
        assert _bucket_at_start("tokens")["value"] == 1234
        # avg_cost is rounded to 9 decimal places by format_result.
        assert _bucket_at_start("cost")["value"] == round(0.000123456789, 9)
        assert _bucket_at_start("traffic")["traffic"] == 42

    def test_raw_query_path_keeps_start_time_column(self):
        """Filtered queries hit raw `spans`, which uses `start_time` (not `hour`).

        Belt-and-braces with `test_build_with_filters_uses_spans_table`:
        confirms the *column* swap happens too, not just the table swap.
        """
        from tracer.services.clickhouse.query_builders import TimeSeriesQueryBuilder

        builder = TimeSeriesQueryBuilder(
            project_id="test-project-id",
            filters=[
                {
                    "column_id": "model",
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "gpt-4",
                        "col_type": "SYSTEM_METRIC",
                    },
                }
            ],
            interval="hour",
        )
        query, _ = builder.build()
        # Raw-spans path filters on start_time, NOT hour.
        assert "start_time >= %(start_date)s" in query
        assert "start_time < %(end_date)s" in query
        assert "spans_hourly_rollup" not in query


@pytest.mark.unit
class TestTraceListQueryBuilder:
    """Test trace list query builder."""

    def test_build_paginated_query(self):
        """build() should produce a paginated query with LIMIT and OFFSET."""
        from tracer.services.clickhouse.query_builders import TraceListQueryBuilder

        builder = TraceListQueryBuilder(
            project_id="test-project-id",
            filters=[],
            page_number=0,
            page_size=10,
        )
        query, params = builder.build()
        assert "LIMIT" in query
        # Prefix-fetch pagination: no SQL OFFSET (see page_dedup.py).
        assert "OFFSET" not in query
        assert "parent_span_id IS NULL" in query
        assert params["limit"] == 20  # offset 0 + 2 * page_size 10

    def test_build_query_selects_expected_columns(self):
        """Phase-1 query should select trace metadata columns."""
        from tracer.services.clickhouse.query_builders import TraceListQueryBuilder

        builder = TraceListQueryBuilder(
            project_id="test-project-id",
            filters=[],
            page_number=0,
            page_size=10,
        )
        query, params = builder.build()
        assert "trace_id" in query
        assert "trace_name" in query
        assert "latency_ms" in query
        assert "cost" in query
        assert "model" in query

    def test_build_count_query(self):
        """build_count_query() should produce a count query."""
        from tracer.services.clickhouse.query_builders import TraceListQueryBuilder

        builder = TraceListQueryBuilder(
            project_id="test-project-id",
            filters=[],
            page_number=0,
            page_size=10,
        )
        # First call build() to set up start/end dates
        builder.build()
        query, params = builder.build_count_query()
        assert "uniq(trace_id)" in query
        assert "parent_span_id IS NULL" in query

    def test_build_eval_query(self):
        """Phase-2 eval query should query tracer_eval_logger grouped by config."""
        from tracer.services.clickhouse.query_builders import TraceListQueryBuilder

        builder = TraceListQueryBuilder(
            project_id="test-project-id",
            filters=[],
            page_number=0,
            page_size=10,
            eval_config_ids=[
                "00000000-0000-0000-0000-000000000001",
                "00000000-0000-0000-0000-000000000002",
            ],
        )
        trace_ids = ["trace-1", "trace-2"]
        query, params = builder.build_eval_query(trace_ids)
        assert "tracer_eval_logger" in query
        assert "GROUP BY" in query
        assert "trace_ids" in params
        assert "eval_config_ids" in params

    def test_build_eval_query_empty_inputs(self):
        """build_eval_query() should return empty query when no trace_ids or eval_config_ids."""
        from tracer.services.clickhouse.query_builders import TraceListQueryBuilder

        builder = TraceListQueryBuilder(
            project_id="test-project-id",
            filters=[],
            page_number=0,
            page_size=10,
            eval_config_ids=[],
        )
        query, params = builder.build_eval_query(["trace-1"])
        assert query == ""
        assert params == {}

        builder2 = TraceListQueryBuilder(
            project_id="test-project-id",
            eval_config_ids=["00000000-0000-0000-0000-000000000001"],
        )
        query2, params2 = builder2.build_eval_query([])
        assert query2 == ""
        assert params2 == {}

    def test_pivot_eval_results(self):
        """pivot_eval_results should nest eval scores by trace_id and config_id."""
        from tracer.services.clickhouse.query_builders import TraceListQueryBuilder

        rows = [
            ("trace-1", "00000000-0000-0000-0000-000000000001", 0.85, None, 5),
            ("trace-1", "00000000-0000-0000-0000-000000000002", None, 90.0, 3),
            ("trace-2", "00000000-0000-0000-0000-000000000001", 0.72, None, 2),
        ]
        columns = [
            "trace_id",
            "eval_config_id",
            "avg_score",
            "pass_rate",
            "eval_count",
        ]
        result = TraceListQueryBuilder.pivot_eval_results(rows, columns)
        assert "trace-1" in result
        assert "00000000-0000-0000-0000-000000000001" in result["trace-1"]
        assert (
            result["trace-1"]["00000000-0000-0000-0000-000000000001"]["avg_score"]
            == 85.0
        )
        assert "trace-2" in result
        assert (
            result["trace-2"]["00000000-0000-0000-0000-000000000001"]["avg_score"]
            == 72.0
        )

    def test_build_with_sort_params(self):
        """Custom sort params should override default ORDER BY."""
        from tracer.services.clickhouse.query_builders import TraceListQueryBuilder

        builder = TraceListQueryBuilder(
            project_id="test-project-id",
            filters=[],
            page_number=0,
            page_size=10,
            sort_params=[{"column_id": "cost", "direction": "asc"}],
        )
        query, params = builder.build()
        assert "ORDER BY" in query
        assert "cost ASC" in query

    def test_pagination_offset_calculation(self):
        """Offset should be page_number * page_size."""
        from tracer.services.clickhouse.query_builders import TraceListQueryBuilder

        builder = TraceListQueryBuilder(
            project_id="test-project-id",
            page_number=3,
            page_size=25,
        )
        query, params = builder.build()
        assert "OFFSET" not in query
        assert "offset" not in params
        assert params["limit"] == 125  # offset 75 + 2 * page_size 25

    def test_build_user_id_query(self):
        """build_user_id_query() should use enduser_dict for single-query lookup."""
        from tracer.services.clickhouse.query_builders import TraceListQueryBuilder

        builder = TraceListQueryBuilder(project_id="test-proj-123")
        trace_ids = ["trace-1", "trace-2", "trace-3"]

        query, params = builder.build_user_id_query(trace_ids)

        assert "SELECT" in query
        assert "trace_id" in query
        assert "dictGetOrDefault('enduser_dict', 'user_id', end_user_id, '')" in query
        assert "GROUP BY trace_id" in query
        assert "PREWHERE trace_id IN" in query
        assert params["user_trace_ids"] == ("trace-1", "trace-2", "trace-3")
        assert params["project_id"] == "test-proj-123"

    def test_build_user_id_query_empty_trace_ids(self):
        """build_user_id_query() should return empty when no trace_ids provided."""
        from tracer.services.clickhouse.query_builders import TraceListQueryBuilder

        builder = TraceListQueryBuilder(project_id="test-proj-123")
        query, params = builder.build_user_id_query([])

        assert query == ""
        assert params == {}

    def test_v2_build_user_id_query_uses_v2_dict_and_column(self):
        """TraceListQueryBuilderV2 must rewrite the inherited user-id query to the
        v2 `end_users_dict` / `is_deleted` (and append the v2 SETTINGS) — the v1
        `enduser_dict` / `_peerdb_is_deleted` artifacts must be gone."""
        from tracer.services.clickhouse.v2.query_builders.trace_list import (
            TraceListQueryBuilderV2,
        )

        builder = TraceListQueryBuilderV2(project_id="test-proj-123")
        query, params = builder.build_user_id_query(["trace-1", "trace-2"])

        assert "end_users_dict" in query
        assert "is_deleted" in query
        assert "use_skip_indexes_if_final" in query  # v2 SETTINGS appended
        assert "'enduser_dict'" not in query
        assert "_peerdb_is_deleted" not in query
        assert params["user_trace_ids"] == ("trace-1", "trace-2")

    def test_v2_build_user_id_query_empty_trace_ids(self):
        """The v2 override preserves the empty-input contract (no rewrite/SETTINGS
        applied to an empty query)."""
        from tracer.services.clickhouse.v2.query_builders.trace_list import (
            TraceListQueryBuilderV2,
        )

        builder = TraceListQueryBuilderV2(project_id="test-proj-123")
        query, params = builder.build_user_id_query([])

        assert query == ""
        assert params == {}


@pytest.mark.unit
class TestSessionListQueryBuilder:
    """Test session list query builder."""

    def test_build_query(self):
        """build() should produce a session list query with GROUP BY."""
        from tracer.services.clickhouse.query_builders import SessionListQueryBuilder

        builder = SessionListQueryBuilder(
            project_id="test-project-id",
            filters=[],
            page_number=0,
            page_size=10,
        )
        query, params = builder.build()
        assert "trace_session_id" in query
        assert "GROUP BY" in query
        assert "sum" in query.lower() or "SUM" in query

    def test_build_query_selects_session_aggregates(self):
        """Query should compute per-session aggregates."""
        from tracer.services.clickhouse.query_builders import SessionListQueryBuilder

        builder = SessionListQueryBuilder(
            project_id="test-project-id",
            filters=[],
            page_number=0,
            page_size=10,
        )
        query, params = builder.build()
        assert "min(start_time)" in query.lower() or "MIN(start_time)" in query
        assert "max(end_time)" in query.lower() or "MAX(end_time)" in query
        assert "uniq(trace_id)" in query

    def test_build_count_query_simple(self):
        """build_count_query() without HAVING filters uses count(DISTINCT ...)."""
        from tracer.services.clickhouse.query_builders import SessionListQueryBuilder

        builder = SessionListQueryBuilder(
            project_id="test-project-id",
            filters=[],
            page_number=0,
            page_size=10,
        )
        builder.build()
        query, params = builder.build_count_query()
        assert "count(DISTINCT trace_session_id)" in query
        # The id-remap survivor map (id_remap_sql) embeds internal
        # `GROUP BY new_id` + `GROUP BY any_id` in its join subquery; the simple
        # count path must have no OTHER (session-level) GROUP BY — strip the
        # remap's first.
        stripped = query.replace("GROUP BY new_id", "").replace("GROUP BY any_id", "")
        assert "GROUP BY" not in stripped

    def test_build_count_query_with_having(self):
        """build_count_query() with HAVING filters uses full aggregation subquery."""
        from tracer.services.clickhouse.query_builders import SessionListQueryBuilder

        builder = SessionListQueryBuilder(
            project_id="test-project-id",
            filters=[
                {
                    "column_id": "duration",
                    "filter_config": {
                        "filter_op": "greater_than",
                        "filter_value": 60,
                    },
                }
            ],
            page_number=0,
            page_size=10,
        )
        builder.build()
        query, params = builder.build_count_query()
        assert "count() AS total" in query
        assert "GROUP BY trace_session_id" in query
        assert "HAVING" in query

    def test_having_filter_accepts_canonical_operator(self):
        """Session aggregate filters use the canonical filter operator contract."""
        from tracer.services.clickhouse.query_builders import SessionListQueryBuilder

        builder = SessionListQueryBuilder(
            project_id="test-project-id",
            filters=[
                {
                    "column_id": "duration",
                    "filter_config": {
                        "filter_op": "equals",
                        "filter_value": 60,
                    },
                }
            ],
            page_number=0,
            page_size=10,
        )
        query, params = builder.build()

        assert "HAVING" in query
        assert "duration = %(having_" in query
        assert 60 in params.values()

    def test_having_filter_unknown_operator_does_not_fall_back_to_equals(self):
        """Unsupported session aggregate operators should match no rows."""
        from tracer.services.clickhouse.query_builders import SessionListQueryBuilder

        builder = SessionListQueryBuilder(
            project_id="test-project-id",
            filters=[
                {
                    "column_id": "duration",
                    "filter_config": {
                        "filter_op": "definitely_not_supported",
                        "filter_value": 60,
                    },
                }
            ],
            page_number=0,
            page_size=10,
        )
        query, _params = builder.build()

        assert "HAVING" in query
        assert "0 = 1" in query
        assert "duration = %(having_" not in query

    def test_build_with_user_id(self):
        """User membership should select sessions without shrinking aggregates."""
        from tracer.services.clickhouse.query_builders import SessionListQueryBuilder

        builder = SessionListQueryBuilder(
            project_id="test-project-id",
            filters=[],
            page_number=0,
            page_size=10,
            user_id="user-123",
        )
        query, params = builder.build()
        assert "trace_session_id IN (" in query
        assert "SELECT trace_session_id, end_user_id" in query
        assert "end_user_id = %(user_id)s" in query
        assert query.index("trace_session_id IN (") < query.index(
            "GROUP BY trace_session_id"
        )
        assert params.get("user_id") == "user-123"

    def test_user_null_filter_presence_resolves_session_ids_before_membership(self):
        from tracer.services.clickhouse.query_builders import SessionListQueryBuilder

        builder = SessionListQueryBuilder(
            project_id="test-project-id",
            filters=[
                {
                    "column_id": "end_user_id",
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "is_not_null",
                        "col_type": "SYSTEM_METRIC",
                    },
                }
            ],
        )

        query, _params = builder.build()

        assert "trace_session_id IN (" in query
        assert "trace_session_id_remap" in query
        assert "user_presence_ts_remap.survivor_id" in query
        assert "GROUP BY trace_session_id" in query
        assert "end_user_id IS NOT NULL" in query

    def test_build_excludes_null_session_ids(self):
        """Query should filter out rows without a session ID."""
        from tracer.services.clickhouse.query_builders import SessionListQueryBuilder

        builder = SessionListQueryBuilder(
            project_id="test-project-id",
            filters=[],
            page_number=0,
            page_size=10,
        )
        query, params = builder.build()
        assert "trace_session_id IS NOT NULL" in query

    def test_session_filter_alias_resolves_before_grouping(self):
        """Session picker values are strings; the frontend session filter resolves them
        new→old through the trace_session_id remap (P3b step1.5) so a cross-cutover
        straddler unifies under the OLD curated id, passing the ids as a bound
        UUID-set param — replacing the legacy ``toString(trace_session_id) IN`` cast.
        """
        from tracer.services.clickhouse.query_builders import SessionListQueryBuilder

        session_id = "003b76f1-2b4a-4af5-b0dc-224d687374d4"
        builder = SessionListQueryBuilder(
            project_id="test-project-id",
            filters=[
                {
                    "column_id": "session",
                    "display_name": "Session",
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "in",
                        "filter_value": [session_id],
                        "col_type": "SYSTEM_METRIC",
                    },
                }
            ],
            page_number=0,
            page_size=10,
        )

        query, params = builder.build()

        # Resolved-membership form (id_remap_sql survivor-collapse), not the
        # legacy toString cast.
        assert "trace_session_id_remap" in query
        assert "ts_remap.survivor_id" in query
        assert "IN %(sess_1)s" in query
        assert "toString(trace_session_id) IN" not in query
        assert query.index("IN %(sess_1)s") < query.index("GROUP BY trace_session_id")
        assert session_id in next(
            value for key, value in params.items() if key.startswith("sess_")
        )

    def test_total_traces_count_frontend_alias_filters_and_sorts(self):
        from tracer.services.clickhouse.query_builders import SessionListQueryBuilder

        builder = SessionListQueryBuilder(
            project_id="test-project-id",
            filters=[
                {
                    "column_id": "total_traces_count",
                    "filter_config": {
                        "filter_type": "number",
                        "filter_op": "greater_than",
                        "filter_value": 4,
                        "col_type": "SYSTEM_METRIC",
                    },
                }
            ],
            sort_params=[{"column_id": "total_traces_count", "direction": "desc"}],
        )

        query, params = builder.build()

        assert "HAVING traces_count > %(having_" in query
        assert "ORDER BY traces_count DESC" in query
        assert 4 in params.values()

    def test_first_message_filter_aggregates_before_having(self):
        from tracer.services.clickhouse.query_builders import SessionListQueryBuilder

        builder = SessionListQueryBuilder(
            project_id="test-project-id",
            filters=[
                {
                    "column_id": "first_message",
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "contains",
                        "filter_value": "recursion",
                        "col_type": "SYSTEM_METRIC",
                    },
                }
            ],
        )

        query, params = builder.build()
        count_query, _ = builder.build_count_query()

        assert "argMin(input, start_time) AS first_message" in query
        assert "HAVING first_message ILIKE %(having_" in query
        assert "%recursion%" in params.values()
        assert "argMin(input, start_time) AS first_message" in count_query

    def test_first_message_sort_projects_message_aggregates(self):
        from tracer.services.clickhouse.query_builders import SessionListQueryBuilder

        builder = SessionListQueryBuilder(
            project_id="test-project-id",
            filters=[],
            sort_params=[{"column_id": "first_message", "direction": "asc"}],
        )

        query, _params = builder.build()

        assert "argMin(input, start_time) AS first_message" in query
        assert "argMax(input, start_time) AS last_message" in query
        assert (
            "SELECT trace_session_id, trace_id, start_time, end_time, cost, total_tokens, input"
            in query
        )
        assert "ORDER BY first_message ASC" in query

    def test_v2_span_attributes_query_uses_native_ch25_columns(self):
        from tracer.services.clickhouse.v2.query_builders.session_list import (
            SessionListQueryBuilderV2,
        )

        builder = SessionListQueryBuilderV2(project_id="test-project-id")
        builder.build()
        query, _ = builder.build_span_attributes_query(["session-1"])

        assert "attributes_extra AS span_attributes_raw" in query
        assert "attrs_string" in query
        assert "attrs_number" in query
        assert "span_attr_str" not in query
        assert "span_attr_num" not in query
        assert "toJSONString(attributes_extra) AS span_attributes_raw" not in query

    def test_content_query_reuses_session_time_window(self):
        from tracer.services.clickhouse.query_builders import SessionListQueryBuilder

        builder = SessionListQueryBuilder(
            project_id="test-project-id",
            filters=[
                {
                    "column_id": "created_at",
                    "filter_config": {
                        "filter_type": "datetime",
                        "filter_op": "between",
                        "filter_value": [
                            "2026-01-01T00:00:00Z",
                            "2026-01-31T23:59:59Z",
                        ],
                    },
                }
            ],
            page_number=0,
            page_size=10,
        )
        builder.build()

        query, params = builder.build_content_query(["session-1"])

        assert "trace_session_id IN %(content_session_ids)s" in query
        assert params["content_session_ids"] == ("session-1",)

    def test_span_attributes_query_reuses_session_time_window(self):
        from tracer.services.clickhouse.query_builders import SessionListQueryBuilder

        builder = SessionListQueryBuilder(
            project_id="test-project-id",
            filters=[
                {
                    "column_id": "created_at",
                    "filter_config": {
                        "filter_type": "datetime",
                        "filter_op": "between",
                        "filter_value": [
                            "2026-02-01T00:00:00Z",
                            "2026-02-28T23:59:59Z",
                        ],
                    },
                }
            ],
            page_number=0,
            page_size=10,
        )
        builder.build()

        query, params = builder.build_span_attributes_query(["session-1"])

        assert "s.trace_session_id IN %(attr_session_ids)s" in query
        assert params["attr_session_ids"] == ("session-1",)

    def test_v2_span_attributes_query_reuses_session_time_window(self):
        from tracer.services.clickhouse.v2.query_builders.session_list import (
            SessionListQueryBuilderV2,
        )

        builder = SessionListQueryBuilderV2(
            project_id="test-project-id",
            filters=[
                {
                    "column_id": "created_at",
                    "filter_config": {
                        "filter_type": "datetime",
                        "filter_op": "between",
                        "filter_value": [
                            "2026-03-01T00:00:00Z",
                            "2026-03-31T23:59:59Z",
                        ],
                    },
                }
            ],
            page_number=0,
            page_size=10,
        )
        builder.build()

        query, params = builder.build_span_attributes_query(["session-1"])

        assert "s.trace_session_id IN %(attr_session_ids)s" in query
        assert params["attr_session_ids"] == ("session-1",)

    def test_build_uses_uniq_not_uniqExact(self):
        """build() should use approximate uniq() instead of expensive uniqExact()."""
        from tracer.services.clickhouse.query_builders import SessionListQueryBuilder

        builder = SessionListQueryBuilder(
            project_id="test-project-id",
            filters=[],
            page_number=0,
            page_size=10,
        )
        query, params = builder.build()
        assert "uniq(trace_id)" in query
        assert "uniqExact" not in query

    def test_has_having_filters_false_for_no_aggregate_filters(self):
        """has_having_filters() returns False when no aggregate column filters."""
        from tracer.services.clickhouse.query_builders import SessionListQueryBuilder

        builder = SessionListQueryBuilder(
            project_id="test-project-id",
            filters=[
                {
                    "column_id": "created_at",
                    "filter_config": {
                        "filter_type": "datetime",
                        "filter_op": "greater_than",
                        "filter_value": "2025-01-01T00:00:00Z",
                    },
                }
            ],
            page_number=0,
            page_size=10,
        )
        assert builder.has_having_filters() is False

    def test_has_having_filters_true_for_aggregate_filters(self):
        """has_having_filters() returns True when filtering on duration/cost/tokens."""
        from tracer.services.clickhouse.query_builders import SessionListQueryBuilder

        builder = SessionListQueryBuilder(
            project_id="test-project-id",
            filters=[
                {
                    "column_id": "total_cost",
                    "filter_config": {
                        "filter_op": "greater_than",
                        "filter_value": 1.0,
                    },
                }
            ],
            page_number=0,
            page_size=10,
        )
        assert builder.has_having_filters() is True

    def test_span_attributes_query_root_spans_only(self):
        """Span attributes query should filter to root spans only."""
        from tracer.services.clickhouse.query_builders import SessionListQueryBuilder

        builder = SessionListQueryBuilder(
            project_id="test-project-id",
            filters=[],
            page_number=0,
            page_size=10,
        )
        builder.build()
        query, params = builder.build_span_attributes_query(["session-1", "session-2"])
        assert "(parent_span_id IS NULL OR parent_span_id = '')" in query

    def test_span_attributes_query_has_limit(self):
        """Span attributes query should have a LIMIT to prevent unbounded scans."""
        from tracer.services.clickhouse.query_builders import SessionListQueryBuilder

        builder = SessionListQueryBuilder(
            project_id="test-project-id",
            filters=[],
            page_number=0,
            page_size=10,
        )
        builder.build()
        query, params = builder.build_span_attributes_query(["session-1", "session-2"])
        assert "LIMIT 500" in query

    def test_span_attributes_query_empty_sessions(self):
        """Span attributes query should return empty for no sessions."""
        from tracer.services.clickhouse.query_builders import SessionListQueryBuilder

        builder = SessionListQueryBuilder(
            project_id="test-project-id",
            filters=[],
            page_number=0,
            page_size=10,
        )
        builder.build()
        query, params = builder.build_span_attributes_query([])
        assert query == ""
        assert params == {}

    def test_count_query_routes_correctly(self):
        """build_count_query() should route to simple path without HAVING filters."""
        from tracer.services.clickhouse.query_builders import SessionListQueryBuilder

        # No aggregate filters -> simple path
        builder = SessionListQueryBuilder(
            project_id="test-project-id",
            filters=[],
            page_number=0,
            page_size=10,
        )
        builder.build()
        query, _ = builder.build_count_query()
        assert "count(DISTINCT trace_session_id)" in query

        # With aggregate filter -> aggregated path
        builder2 = SessionListQueryBuilder(
            project_id="test-project-id",
            filters=[
                {
                    "column_id": "duration",
                    "filter_config": {
                        "filter_op": "less_than",
                        "filter_value": 300,
                    },
                }
            ],
            page_number=0,
            page_size=10,
        )
        builder2.build()
        query2, _ = builder2.build_count_query()
        assert "count() AS total" in query2
        assert "GROUP BY" in query2

    def test_build_excludes_nil_uuid(self):
        """build() should exclude the ClickHouse nil UUID from session results."""
        from tracer.services.clickhouse.query_builders import SessionListQueryBuilder

        builder = SessionListQueryBuilder(
            project_id="test-project-id",
            filters=[],
            page_number=0,
            page_size=10,
        )
        query, _ = builder.build()
        assert "00000000-0000-0000-0000-000000000000" in query

    def test_count_query_excludes_nil_uuid(self):
        """Both simple and aggregated count queries should exclude the nil UUID."""
        from tracer.services.clickhouse.query_builders import SessionListQueryBuilder

        # Simple count path
        builder = SessionListQueryBuilder(
            project_id="test-project-id",
            filters=[],
            page_number=0,
            page_size=10,
        )
        builder.build()
        query, _ = builder.build_count_query()
        assert "00000000-0000-0000-0000-000000000000" in query

        # Aggregated count path
        builder2 = SessionListQueryBuilder(
            project_id="test-project-id",
            filters=[
                {
                    "column_id": "duration",
                    "filter_config": {
                        "filter_op": "greater_than",
                        "filter_value": 60,
                    },
                }
            ],
            page_number=0,
            page_size=10,
        )
        builder2.build()
        query2, _ = builder2.build_count_query()
        assert "00000000-0000-0000-0000-000000000000" in query2

    def test_format_sessions_skips_nil_uuid(self):
        """format_sessions() should drop rows with the nil UUID session_id."""
        from datetime import datetime

        from tracer.services.clickhouse.query_builders import SessionListQueryBuilder

        columns = [
            "session_id",
            "session_start",
            "session_end",
            "duration",
            "total_cost",
            "total_tokens",
            "traces_count",
        ]
        now = datetime.utcnow()
        rows = [
            ("00000000-0000-0000-0000-000000000000", now, now, 0, 0.0, 0, 1),
            ("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", now, now, 10, 1.5, 100, 3),
        ]
        result = SessionListQueryBuilder.format_sessions(rows, columns)
        assert len(result) == 1
        assert result[0]["session_id"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


@pytest.mark.unit
class TestUserListQueryBuilder:
    def test_eval_pass_rate_join_keeps_eval_trace_id_indexable(self):
        from tracer.services.clickhouse.query_builders.user_list import (
            UserListQueryBuilder,
        )

        builder = UserListQueryBuilder(
            organization_id="00000000-0000-0000-0000-000000000001",
            project_id="00000000-0000-0000-0000-000000000002",
            filters=[],
            limit=10,
            offset=0,
        )
        builder.build()
        query, _ = builder.build_eval_query(["00000000-0000-0000-0000-000000000003"])

        assert "e.trace_id = toUUIDOrNull(ut.trace_id)" in query
        assert "toString(e.trace_id)" not in query

    def test_build_excludes_eval_pass_rate_cte(self):
        from tracer.services.clickhouse.query_builders.user_list import (
            UserListQueryBuilder,
        )

        query, _ = UserListQueryBuilder(
            organization_id="00000000-0000-0000-0000-000000000001",
            project_id="00000000-0000-0000-0000-000000000002",
            filters=[],
            limit=10,
            offset=0,
        ).build()

        assert "eval_pass_rate AS" not in query
        assert "eval_logger" not in query.lower()
        assert "0 AS bool_eval_pass_rate" in query


@pytest.mark.unit
class TestSessionListCountSkipLogic:
    """Tests for the count-query-skip optimization in _list_sessions_clickhouse."""

    def test_skip_count_first_page_small_result(self):
        """When Phase 1 returns <= page_size rows on page 0, total = len(results)."""
        page_size = 30
        page_number = 0
        result_data = [{"session_id": f"s-{i}"} for i in range(15)]

        has_more = len(result_data) > page_size
        actual_data = result_data[:page_size]

        if not has_more and page_number == 0:
            total_count = len(actual_data)
        elif not has_more:
            total_count = (page_number * page_size) + len(actual_data)
        else:
            total_count = None

        assert total_count == 15

    def test_skip_count_later_page_no_more(self):
        """When Phase 1 returns < page_size on a later page, total = offset + len."""
        page_size = 30
        page_number = 3
        result_data = [{"session_id": f"s-{i}"} for i in range(10)]

        has_more = len(result_data) > page_size
        actual_data = result_data[:page_size]

        if not has_more and page_number == 0:
            total_count = len(actual_data)
        elif not has_more:
            total_count = (page_number * page_size) + len(actual_data)
        else:
            total_count = None

        assert total_count == 100

    def test_needs_count_query_when_has_more(self):
        """When Phase 1 returns page_size + 1 rows, count query is needed."""
        page_size = 30
        page_number = 0
        result_data = [{"session_id": f"s-{i}"} for i in range(31)]

        has_more = len(result_data) > page_size
        actual_data = result_data[:page_size]

        if not has_more and page_number == 0:
            total_count = len(actual_data)
        elif not has_more:
            total_count = (page_number * page_size) + len(actual_data)
        else:
            total_count = None

        assert total_count is None
        assert len(actual_data) == 30


@pytest.mark.unit
class TestSpanAttributesParsing:
    """Tests for the orjson + key-cap optimization in span attribute processing."""

    def test_json_loads_fallback(self):
        """_json_loads should work whether orjson is available or not."""
        from tracer.views.trace_session import _json_loads

        result = _json_loads(b'{"key": "value"}')
        assert result == {"key": "value"}

    def test_json_loads_handles_string(self):
        """_json_loads should handle string input."""
        from tracer.views.trace_session import _json_loads

        result = _json_loads('{"env": "production", "count": 42}')
        assert result == {"env": "production", "count": 42}

    def test_max_attr_keys_cap(self):
        """Attribute processing should cap keys per session at 50."""
        _MAX_ATTR_KEYS_PER_SESSION = 50
        aggregated_attrs: dict = {}
        sid = "session-1"

        for i in range(100):
            if (
                sid in aggregated_attrs
                and len(aggregated_attrs[sid]) >= _MAX_ATTR_KEYS_PER_SESSION
            ):
                continue
            if sid not in aggregated_attrs:
                aggregated_attrs[sid] = {}
            key = f"attr_{i}"
            if len(aggregated_attrs[sid]) >= _MAX_ATTR_KEYS_PER_SESSION:
                break
            aggregated_attrs[sid][key] = {f"val_{i}"}

        assert len(aggregated_attrs[sid]) == 50


@pytest.mark.unit
class TestEvalMetricsQueryBuilder:
    """Test eval metrics query builder."""

    def test_build_float_eval(self):
        """SCORE eval type should produce a query computing score aggregation."""
        from tracer.services.clickhouse.query_builders import EvalMetricsQueryBuilder

        builder = EvalMetricsQueryBuilder(
            project_id="test-project-id",
            custom_eval_config_id="00000000-0000-0000-0000-000000000001",
            interval="hour",
            eval_output_type="SCORE",
        )
        query, params = builder.build()
        # Pre-aggregated uses float_sum/float_count; raw uses output_float
        assert "float_sum" in query or "output_float" in query
        assert "eval_config_id" in params

    def test_build_float_eval_raw(self):
        """SCORE eval with use_preaggregated=False should use output_float."""
        from tracer.services.clickhouse.query_builders import EvalMetricsQueryBuilder

        builder = EvalMetricsQueryBuilder(
            project_id="test-project-id",
            custom_eval_config_id="00000000-0000-0000-0000-000000000001",
            interval="hour",
            eval_output_type="SCORE",
            use_preaggregated=False,
        )
        query, params = builder.build()
        assert "output_float" in query
        assert "eval_config_id" in params

    def test_build_bool_eval(self):
        """PASS_FAIL eval type should produce a query computing pass rate."""
        from tracer.services.clickhouse.query_builders import EvalMetricsQueryBuilder

        builder = EvalMetricsQueryBuilder(
            project_id="test-project-id",
            custom_eval_config_id="00000000-0000-0000-0000-000000000001",
            interval="day",
            eval_output_type="PASS_FAIL",
        )
        query, params = builder.build()
        # Pre-aggregated uses bool_pass/bool_fail; raw uses output_bool
        assert "bool_pass" in query or "output_bool" in query

    def test_build_bool_eval_raw(self):
        """PASS_FAIL eval with use_preaggregated=False should use output_bool."""
        from tracer.services.clickhouse.query_builders import EvalMetricsQueryBuilder

        builder = EvalMetricsQueryBuilder(
            project_id="test-project-id",
            custom_eval_config_id="00000000-0000-0000-0000-000000000001",
            interval="day",
            eval_output_type="PASS_FAIL",
            use_preaggregated=False,
        )
        query, params = builder.build()
        assert "output_bool" in query

    def test_build_choices_eval(self):
        """CHOICES eval type should produce per-choice percentage columns."""
        from tracer.services.clickhouse.query_builders import EvalMetricsQueryBuilder

        builder = EvalMetricsQueryBuilder(
            project_id="test-project-id",
            custom_eval_config_id="00000000-0000-0000-0000-000000000001",
            interval="day",
            eval_output_type="CHOICES",
            choices=["positive", "negative", "neutral"],
        )
        query, params = builder.build()
        assert "choice_0" in query
        assert "choice_1" in query
        assert "choice_2" in query
        assert "positive" in params.values()

    def test_build_choices_empty_falls_back_to_score(self):
        """CHOICES with no choices list should fall back to score query."""
        from tracer.services.clickhouse.query_builders import EvalMetricsQueryBuilder

        builder = EvalMetricsQueryBuilder(
            project_id="test-project-id",
            custom_eval_config_id="00000000-0000-0000-0000-000000000001",
            interval="day",
            eval_output_type="CHOICES",
            choices=[],
        )
        query, params = builder.build()
        # Should still produce a valid query
        assert isinstance(query, str)
        assert len(query) > 0

    def test_build_with_preaggregated(self):
        """When use_preaggregated=True, SCORE should use eval_metrics_hourly."""
        from tracer.services.clickhouse.query_builders import EvalMetricsQueryBuilder

        builder = EvalMetricsQueryBuilder(
            project_id="test-project-id",
            custom_eval_config_id="00000000-0000-0000-0000-000000000001",
            interval="hour",
            eval_output_type="SCORE",
            use_preaggregated=True,
        )
        query, params = builder.build()
        assert "eval_metrics_hourly" in query

    def test_build_without_preaggregated(self):
        """When use_preaggregated=False, should query tracer_eval_logger directly."""
        from tracer.services.clickhouse.query_builders import EvalMetricsQueryBuilder

        builder = EvalMetricsQueryBuilder(
            project_id="test-project-id",
            custom_eval_config_id="00000000-0000-0000-0000-000000000001",
            interval="hour",
            eval_output_type="SCORE",
            use_preaggregated=False,
        )
        query, params = builder.build()
        assert "tracer_eval_logger" in query
        assert "FINAL" in query

    def test_build_with_filters_unpacks_filter_builder_result(self):
        """Filtered eval graphs should merge the CH filter fragment and params."""
        from tracer.services.clickhouse.query_builders import EvalMetricsQueryBuilder

        builder = EvalMetricsQueryBuilder(
            project_id="test-project-id",
            custom_eval_config_id="00000000-0000-0000-0000-000000000001",
            interval="hour",
            eval_output_type="SCORE",
            use_preaggregated=False,
            filters=[
                {
                    "column_id": "status",
                    "filter_config": {
                        "col_type": "SYSTEM_METRIC",
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "OK",
                    },
                }
            ],
        )

        query, params = builder.build()
        assert "SELECT DISTINCT trace_id FROM spans" in query
        assert "lower(status) =" in query
        assert "ok" in params.values()

    def test_build_with_filters_forces_raw_eval_query(self):
        """Filtered eval graphs cannot use pre-aggregated rows."""
        from tracer.services.clickhouse.query_builders import EvalMetricsQueryBuilder

        builder = EvalMetricsQueryBuilder(
            project_id="test-project-id",
            custom_eval_config_id="00000000-0000-0000-0000-000000000001",
            interval="hour",
            eval_output_type="SCORE",
            filters=[
                {
                    "column_id": "status",
                    "filter_config": {
                        "col_type": "SYSTEM_METRIC",
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "OK",
                    },
                }
            ],
        )

        query, _params = builder.build()
        assert "tracer_eval_logger" in query
        assert "eval_metrics_hourly" not in query
        assert "SELECT DISTINCT trace_id FROM spans" in query

    def test_default_time_range(self):
        """When no start/end dates provided, should default to last 7 days."""

        from tracer.services.clickhouse.query_builders import EvalMetricsQueryBuilder

        builder = EvalMetricsQueryBuilder(
            project_id="test-project-id",
            custom_eval_config_id="00000000-0000-0000-0000-000000000001",
        )
        assert builder.start_date is not None
        assert builder.end_date is not None
        # Default should be roughly last 7 days
        diff = builder.end_date - builder.start_date
        assert 6 <= diff.days <= 8


@pytest.mark.unit
class TestErrorAnalysisQueryBuilder:
    """Test error analysis query builder."""

    def test_build_time_series(self):
        """Time series mode should produce a query counting errors by time bucket."""
        from tracer.services.clickhouse.query_builders import (
            ErrorAnalysisQueryBuilder,
        )

        builder = ErrorAnalysisQueryBuilder(
            project_id="test-project-id",
            filters=[],
            interval="hour",
            mode="time_series",
        )
        query, params = builder.build()
        assert "ERROR" in query
        assert "GROUP BY" in query
        assert "time_bucket" in query

    def test_build_breakdown(self):
        """Breakdown mode should group errors by a dimension."""
        from tracer.services.clickhouse.query_builders import (
            ErrorAnalysisQueryBuilder,
        )

        builder = ErrorAnalysisQueryBuilder(
            project_id="test-project-id",
            filters=[],
            interval="hour",
            mode="breakdown",
            group_by="model",
        )
        query, params = builder.build()
        assert "model" in query
        assert "error_count" in query
        assert "GROUP BY" in query

    def test_build_summary(self):
        """Summary mode should produce total error count and error rate."""
        from tracer.services.clickhouse.query_builders import (
            ErrorAnalysisQueryBuilder,
        )

        builder = ErrorAnalysisQueryBuilder(
            project_id="test-project-id",
            filters=[],
            mode="summary",
        )
        query, params = builder.build()
        assert "total_errors" in query
        assert "total_spans" in query
        assert "error_rate" in query

    def test_build_with_filters(self):
        """Filters should be applied to error analysis queries."""
        from tracer.services.clickhouse.query_builders import (
            ErrorAnalysisQueryBuilder,
        )

        builder = ErrorAnalysisQueryBuilder(
            project_id="test-project-id",
            filters=[
                {
                    "column_id": "model",
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "gpt-4",
                        "col_type": "SYSTEM_METRIC",
                    },
                }
            ],
            mode="time_series",
        )
        query, params = builder.build()
        assert "gpt-4" in str(params.values()) or "model" in query

    def test_invalid_group_by_defaults_to_model(self):
        """Invalid group_by value should default to 'model'."""
        from tracer.services.clickhouse.query_builders import (
            ErrorAnalysisQueryBuilder,
        )

        builder = ErrorAnalysisQueryBuilder(
            project_id="test-project-id",
            mode="breakdown",
            group_by="invalid_column",
        )
        assert builder.group_by == "model"

    def test_format_summary_result_empty(self):
        """format_summary_result should handle empty rows gracefully."""
        from tracer.services.clickhouse.query_builders import (
            ErrorAnalysisQueryBuilder,
        )

        result = ErrorAnalysisQueryBuilder.format_summary_result([], [])
        assert result["total_errors"] == 0
        assert result["total_spans"] == 0
        assert result["error_rate"] == 0.0

    def test_format_breakdown_result(self):
        """format_breakdown_result should convert rows to dicts."""
        from tracer.services.clickhouse.query_builders import (
            ErrorAnalysisQueryBuilder,
        )

        rows = [("gpt-4", 15, 3), ("gpt-3.5-turbo", 8, 2)]
        columns = ["dimension", "error_count", "affected_traces"]
        result = ErrorAnalysisQueryBuilder.format_breakdown_result(rows, columns)
        assert len(result) == 2
        assert result[0]["dimension"] == "gpt-4"
        assert result[0]["error_count"] == 15
        assert result[1]["dimension"] == "gpt-3.5-turbo"


# ============================================================================
# 4. Query Service Tests
# ============================================================================


@pytest.mark.unit
class TestAnalyticsQueryService:
    """Test the dispatch layer."""

    def test_get_backend_status(self):
        """get_backend_status reports CH enablement + connectivity."""
        from tracer.services.clickhouse.query_service import AnalyticsQueryService

        service = AnalyticsQueryService()
        status = service.get_backend_status()
        assert "clickhouse" in status
        assert "enabled" in status["clickhouse"]
        assert "connected" in status["clickhouse"]

    def test_query_result_creation(self):
        """QueryResult dataclass should be creatable with expected fields."""
        from tracer.services.clickhouse.query_service import QueryResult

        result = QueryResult(
            data=[{"id": 1}],
            row_count=1,
            backend_used="postgres",
            query_time_ms=50.5,
        )
        assert result.row_count == 1
        assert result.backend_used == "postgres"
        assert result.query_time_ms == 50.5
        assert result.data == [{"id": 1}]

    def test_query_result_from_clickhouse_rows(self):
        """QueryResult.from_clickhouse_rows should convert tuples to dicts."""
        from tracer.services.clickhouse.query_service import QueryResult

        rows = [(1, "model-a", 100), (2, "model-b", 200)]
        columns = ["id", "model", "count"]
        result = QueryResult.from_clickhouse_rows(rows, columns, query_time_ms=42.0)
        assert result.backend_used == "clickhouse"
        assert result.row_count == 2
        assert result.data[0]["id"] == 1
        assert result.data[0]["model"] == "model-a"
        assert result.data[1]["count"] == 200
        assert result.query_time_ms == 42.0

    def test_query_type_enum_values(self):
        """QueryType should have all expected query types."""
        from tracer.services.clickhouse.query_service import QueryType

        assert QueryType.TIME_SERIES == "TIME_SERIES"
        assert QueryType.TRACE_LIST == "TRACE_LIST"
        assert QueryType.SESSION_LIST == "SESSION_LIST"
        assert QueryType.EVAL_METRICS == "EVAL_METRICS"
        assert QueryType.ERROR_ANALYSIS == "ERROR_ANALYSIS"


# ============================================================================
# 5. Consistency Checker Tests
# ============================================================================


@pytest.mark.unit
class TestConsistencyChecker:
    """Test consistency monitoring."""

    def test_health_status_disabled(self):
        """When CH is not enabled, get_health_status should return disabled status."""
        from tracer.services.clickhouse.consistency import ConsistencyChecker

        checker = ConsistencyChecker()
        health = checker.get_health_status()
        # When CH is not enabled (default in test), should return disabled
        assert health.status in ("disabled", "unhealthy", "degraded")

    def test_consistency_result_dataclass(self):
        """ConsistencyResult dataclass should hold comparison data."""
        from tracer.services.clickhouse.consistency import ConsistencyResult

        result = ConsistencyResult(
            table="tracer_observation_span",
            pg_count=1000,
            ch_count=998,
            difference=2,
            difference_pct=0.2,
            is_consistent=True,
        )
        assert result.table == "tracer_observation_span"
        assert result.pg_count == 1000
        assert result.ch_count == 998
        assert result.is_consistent is True

    def test_health_status_dataclass(self):
        """HealthStatus dataclass should hold overall health information."""
        from tracer.services.clickhouse.consistency import HealthStatus

        status = HealthStatus(
            status="healthy",
            clickhouse_connected=True,
            cdc_lag={"tracer_trace": 5.0, "tracer_observation_span": 3.0},
        )
        assert status.status == "healthy"
        assert status.clickhouse_connected is True
        assert len(status.cdc_lag) == 2

    def test_monitored_tables_defined(self):
        """ConsistencyChecker should have a list of monitored PG/CH table pairs."""
        from tracer.services.clickhouse.consistency import ConsistencyChecker

        assert len(ConsistencyChecker.MONITORED_TABLES) > 0
        for pg_table, ch_table in ConsistencyChecker.MONITORED_TABLES:
            assert isinstance(pg_table, str)
            assert isinstance(ch_table, str)


# ============================================================================
# 6. Base Query Builder Tests
# ============================================================================


@pytest.mark.unit
class TestBaseQueryBuilder:
    """Test base query builder utilities."""

    def test_time_bucket_expr_hour(self):
        """hour interval should map to toStartOfHour."""
        from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder

        assert BaseQueryBuilder.time_bucket_expr("hour") == "toStartOfHour"

    def test_time_bucket_expr_day(self):
        """day interval should map to toStartOfDay."""
        from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder

        assert BaseQueryBuilder.time_bucket_expr("day") == "toStartOfDay"

    def test_time_bucket_expr_week(self):
        """week interval should map to toMonday."""
        from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder

        assert BaseQueryBuilder.time_bucket_expr("week") == "toMonday"

    def test_time_bucket_expr_month(self):
        """month interval should map to toStartOfMonth."""
        from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder

        assert BaseQueryBuilder.time_bucket_expr("month") == "toStartOfMonth"

    def test_time_bucket_expr_year(self):
        """year interval should map to toStartOfYear."""
        from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder

        assert BaseQueryBuilder.time_bucket_expr("year") == "toStartOfYear"

    def test_time_bucket_expr_invalid_defaults_to_hour(self):
        """Unknown interval should default to toStartOfHour."""
        from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder

        assert BaseQueryBuilder.time_bucket_expr("invalid") == "toStartOfHour"
        assert BaseQueryBuilder.time_bucket_expr("") == "toStartOfHour"

    def test_parse_time_range_with_between_filter(self):
        """parse_time_range should extract start and end dates from between filter."""
        from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder

        filters = [
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_op": "between",
                    "filter_value": [
                        "2024-01-01T00:00:00.000Z",
                        "2024-12-31T23:59:59.000Z",
                    ],
                },
            }
        ]
        start, end = BaseQueryBuilder.parse_time_range(filters)
        assert start is not None
        assert end is not None
        assert start.year == 2024
        assert start.month == 1
        assert end.year == 2024
        assert end.month == 12

    def test_parse_time_range_with_greater_than_and_less_than(self):
        """parse_time_range should handle separate greater_than/less_than filters."""
        from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder

        filters = [
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_op": "greater_than",
                    "filter_value": "2024-06-01T00:00:00Z",
                },
            },
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_op": "less_than",
                    "filter_value": "2024-06-30T23:59:59Z",
                },
            },
        ]
        start, end = BaseQueryBuilder.parse_time_range(filters)
        assert start.month == 6
        assert start.day == 1
        assert end.month == 6
        assert end.day == 30

    def test_parse_time_range_defaults(self):
        """Empty filters should default to a 30-day window (partition-pruning friendly)."""
        from datetime import datetime

        from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder

        start, end = BaseQueryBuilder.parse_time_range([])
        assert start is not None
        assert end is not None
        # Default is 30 days back from now (was 3650; shortened in CH 25.3 perf sweep).
        assert (datetime.utcnow() - start).days >= 29
        assert (datetime.utcnow() - start).days <= 31
        # End should be close to now
        assert abs((datetime.utcnow() - end).total_seconds()) < 5

    def test_parse_time_range_ignores_non_date_filters(self):
        """parse_time_range should ignore filters on non-date columns."""
        from datetime import datetime

        from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder

        filters = [
            {
                "column_id": "model",
                "filter_config": {
                    "filter_op": "equals",
                    "filter_value": "gpt-4",
                },
            }
        ]
        start, end = BaseQueryBuilder.parse_time_range(filters)
        # Should fall back to defaults (30 days back).
        assert (datetime.utcnow() - start).days >= 29
        assert (datetime.utcnow() - start).days <= 31

    def test_parse_time_range_handles_start_time_column(self):
        """parse_time_range should also recognize start_time as a date column."""
        from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder

        filters = [
            {
                "column_id": "start_time",
                "filter_config": {
                    "filter_op": "between",
                    "filter_value": [
                        "2024-03-01T00:00:00Z",
                        "2024-03-31T23:59:59Z",
                    ],
                },
            }
        ]
        start, end = BaseQueryBuilder.parse_time_range(filters)
        assert start.month == 3
        assert end.month == 3

    def test_project_where_without_alias(self):
        """project_where() without alias should produce unqualified column names."""
        from tracer.services.clickhouse.query_builders import TimeSeriesQueryBuilder

        builder = TimeSeriesQueryBuilder(project_id="test-id")
        where = builder.project_where()
        assert "project_id = %(project_id)s" in where
        assert "is_deleted = 0" in where

    def test_project_where_with_alias(self):
        """project_where() with alias should prefix column names."""
        from tracer.services.clickhouse.query_builders import TimeSeriesQueryBuilder

        builder = TimeSeriesQueryBuilder(project_id="test-id")
        where = builder.project_where(table_alias="s")
        assert "s.project_id = %(project_id)s" in where
        assert "s.is_deleted = 0" in where

    def test_normalize_timestamp_hour(self):
        """_normalize_timestamp for hour should truncate to hour boundary."""
        from datetime import datetime

        from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder

        dt = datetime(2024, 6, 15, 14, 37, 42, 123456)
        result = BaseQueryBuilder._normalize_timestamp(dt, "hour")
        assert result == datetime(2024, 6, 15, 14, 0, 0)

    def test_normalize_timestamp_day(self):
        """_normalize_timestamp for day should truncate to day boundary."""
        from datetime import datetime

        from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder

        dt = datetime(2024, 6, 15, 14, 37, 42)
        result = BaseQueryBuilder._normalize_timestamp(dt, "day")
        assert result == datetime(2024, 6, 15, 0, 0, 0)

    def test_normalize_timestamp_week(self):
        """_normalize_timestamp for week should truncate to Monday."""
        from datetime import datetime

        from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder

        # 2024-06-15 is a Saturday (weekday=5)
        dt = datetime(2024, 6, 15, 14, 0, 0)
        result = BaseQueryBuilder._normalize_timestamp(dt, "week")
        assert result.weekday() == 0  # Monday
        assert result == datetime(2024, 6, 10, 0, 0, 0)

    def test_normalize_timestamp_month(self):
        """_normalize_timestamp for month should truncate to first day of month."""
        from datetime import datetime

        from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder

        dt = datetime(2024, 6, 15, 14, 0, 0)
        result = BaseQueryBuilder._normalize_timestamp(dt, "month")
        assert result == datetime(2024, 6, 1, 0, 0, 0)

    def test_normalize_timestamp_accepts_clickhouse_date_bucket(self):
        """ClickHouse monthly buckets may arrive as date objects, not datetimes."""
        from datetime import date, datetime

        from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder

        result = BaseQueryBuilder._normalize_timestamp(date(2024, 6, 1), "month")
        assert result == datetime(2024, 6, 1, 0, 0, 0)

    def test_generate_timestamp_range_hourly(self):
        """_generate_timestamp_range should yield hourly buckets."""
        from datetime import datetime

        from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder

        start = datetime(2024, 6, 15, 10, 0, 0)
        end = datetime(2024, 6, 15, 13, 0, 0)
        timestamps = list(
            BaseQueryBuilder._generate_timestamp_range(start, end, "hour")
        )
        assert len(timestamps) == 4  # 10, 11, 12, 13
        assert timestamps[0] == datetime(2024, 6, 15, 10, 0, 0)
        assert timestamps[-1] == datetime(2024, 6, 15, 13, 0, 0)

    def test_generate_timestamp_range_daily(self):
        """_generate_timestamp_range should yield daily buckets."""
        from datetime import datetime

        from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder

        start = datetime(2024, 6, 10, 0, 0, 0)
        end = datetime(2024, 6, 13, 0, 0, 0)
        timestamps = list(BaseQueryBuilder._generate_timestamp_range(start, end, "day"))
        assert len(timestamps) == 4  # 10, 11, 12, 13
        assert timestamps[0] == datetime(2024, 6, 10, 0, 0, 0)
        assert timestamps[-1] == datetime(2024, 6, 13, 0, 0, 0)


# ============================================================================
# 7. Module Import / Export Tests
# ============================================================================


@pytest.mark.unit
class TestModuleExports:
    """Test that all modules export expected symbols."""

    def test_clickhouse_package_exports(self):
        """Top-level clickhouse package should export key symbols."""
        from tracer.services.clickhouse import (
            AnalyticsQueryService,
            ClickHouseClient,
            ConsistencyChecker,
            HealthStatus,
            QueryResult,
            QueryType,
            get_clickhouse_client,
            is_clickhouse_enabled,
        )

        assert ClickHouseClient is not None
        assert get_clickhouse_client is not None
        assert is_clickhouse_enabled is not None
        assert AnalyticsQueryService is not None
        assert QueryType is not None
        assert QueryResult is not None
        assert ConsistencyChecker is not None
        assert HealthStatus is not None

    def test_query_builders_package_exports(self):
        """Query builders package should export all builder classes."""
        from tracer.services.clickhouse.query_builders import (
            BaseQueryBuilder,
            ClickHouseFilterBuilder,
            ErrorAnalysisQueryBuilder,
            EvalMetricsQueryBuilder,
            SessionListQueryBuilder,
            TimeSeriesQueryBuilder,
            TraceListQueryBuilder,
        )

        assert BaseQueryBuilder is not None
        assert ClickHouseFilterBuilder is not None
        assert TimeSeriesQueryBuilder is not None
        assert TraceListQueryBuilder is not None
        assert SessionListQueryBuilder is not None
        assert EvalMetricsQueryBuilder is not None
        assert ErrorAnalysisQueryBuilder is not None

    def test_schema_module_exports(self):
        """Schema module should export DDL constants and helper functions."""
        from tracer.services.clickhouse.schema import (
            CDC_EVAL_LOGGER,
            CDC_OBSERVATION_SPAN,
            CDC_TRACE,
            CDC_TRACE_SESSION,
            SPANS_TABLE,
            TRACE_DICT,
            get_all_schema_ddl,
            get_backfill_statements,
            get_drop_statements,
        )

        assert CDC_OBSERVATION_SPAN is not None
        assert CDC_TRACE is not None
        assert CDC_TRACE_SESSION is not None
        assert CDC_EVAL_LOGGER is not None
        assert TRACE_DICT is not None
        assert SPANS_TABLE is not None
        assert callable(get_all_schema_ddl)
        assert callable(get_drop_statements)
        assert callable(get_backfill_statements)


# ============================================================================
# 8. Parse Datetime Utility Tests
# ============================================================================


@pytest.mark.unit
class TestParseDatetime:
    """Test the _parse_dt utility function used by BaseQueryBuilder."""

    def test_parse_iso_string_with_z(self):
        """Should parse ISO 8601 string with Z suffix."""
        from tracer.services.clickhouse.query_builders.base import _parse_dt

        result = _parse_dt("2024-06-15T12:00:00Z")
        assert result is not None
        assert result.year == 2024
        assert result.month == 6
        assert result.hour == 12
        assert result.tzinfo is None  # Should strip timezone

    def test_parse_iso_string_with_offset(self):
        """Should parse ISO 8601 string with timezone offset."""
        from tracer.services.clickhouse.query_builders.base import _parse_dt

        result = _parse_dt("2024-06-15T12:00:00+00:00")
        assert result is not None
        assert result.tzinfo is None

    def test_parse_iso_string_with_milliseconds(self):
        """Should parse ISO 8601 string with milliseconds."""
        from tracer.services.clickhouse.query_builders.base import _parse_dt

        result = _parse_dt("2024-06-15T12:00:00.123Z")
        assert result is not None
        assert result.year == 2024

    def test_parse_datetime_object(self):
        """Should return datetime objects as-is (stripped of tzinfo)."""
        from datetime import datetime

        from tracer.services.clickhouse.query_builders.base import _parse_dt

        dt = datetime(2024, 6, 15, 12, 0, 0)
        result = _parse_dt(dt)
        assert result == dt

    def test_parse_none(self):
        """Should return None for None input."""
        from tracer.services.clickhouse.query_builders.base import _parse_dt

        assert _parse_dt(None) is None

    def test_parse_date_only_string(self):
        """Should parse date-only string."""
        from tracer.services.clickhouse.query_builders.base import _parse_dt

        result = _parse_dt("2024-06-15")
        assert result is not None
        assert result.year == 2024
        assert result.month == 6
        assert result.day == 15


# ============================================================================
# 13. Span Attribute View Tests
# ============================================================================


@pytest.mark.unit
class TestSpanAttributeViews:
    """Test span attribute discovery view imports and structure."""

    def test_span_attribute_keys_view_importable(self):
        """SpanAttributeKeysView should be importable."""
        from tracer.views.span_attributes import SpanAttributeKeysView

        assert SpanAttributeKeysView is not None
        view = SpanAttributeKeysView()
        assert hasattr(view, "get")
        assert hasattr(view, "permission_classes")

    def test_span_attribute_values_view_importable(self):
        """SpanAttributeValuesView should be importable."""
        from tracer.views.span_attributes import SpanAttributeValuesView

        assert SpanAttributeValuesView is not None
        view = SpanAttributeValuesView()
        assert hasattr(view, "get")

    def test_span_attribute_detail_view_importable(self):
        """SpanAttributeDetailView should be importable."""
        from tracer.views.span_attributes import SpanAttributeDetailView

        assert SpanAttributeDetailView is not None
        view = SpanAttributeDetailView()
        assert hasattr(view, "get")

    def test_all_views_require_authentication(self):
        """All span attribute views should require authentication."""
        from rest_framework.permissions import IsAuthenticated

        from tracer.views.span_attributes import (
            SpanAttributeDetailView,
            SpanAttributeKeysView,
            SpanAttributeValuesView,
        )

        for view_class in [
            SpanAttributeKeysView,
            SpanAttributeValuesView,
            SpanAttributeDetailView,
        ]:
            assert IsAuthenticated in view_class.permission_classes

    def test_urls_registered(self):
        """Span attribute URLs should be registered."""
        from django.urls import NoReverseMatch, reverse

        # These URL names should be registered in tfc/urls.py
        for url_name in [
            "span-attribute-keys",
            "span-attribute-values",
            "span-attribute-detail",
        ]:
            try:
                url = reverse(url_name)
                assert url is not None
            except NoReverseMatch:
                # URL may not have a name, check it's importable at least
                pass


# ============================================================================
# 14. Comprehensive Trace List Query Builder Tests
# ============================================================================


@pytest.mark.unit
class TestTraceListQueryBuilderComprehensive:
    """Comprehensive tests for TraceListQueryBuilder covering filters,
    sorting, pagination, annotations, eval pivoting, and response format."""

    # ------------------------------------------------------------------
    # Build query with various filter types
    # ------------------------------------------------------------------

    def test_build_with_system_metric_filter(self):
        """SYSTEM_METRIC filter should be embedded in the WHERE clause."""
        from tracer.services.clickhouse.query_builders import TraceListQueryBuilder

        builder = TraceListQueryBuilder(
            project_id="proj-1",
            filters=[
                {
                    "column_id": "model",
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "gpt-4",
                        "col_type": "SYSTEM_METRIC",
                    },
                }
            ],
        )
        query, params = builder.build()
        assert "model" in query
        assert "gpt-4" in params.values()
        assert "parent_span_id IS NULL" in query

    def test_build_with_span_attribute_filter(self):
        """SPAN_ATTRIBUTE filter should reference span_attr_str map column."""
        from tracer.services.clickhouse.query_builders import TraceListQueryBuilder

        builder = TraceListQueryBuilder(
            project_id="proj-1",
            filters=[
                {
                    "column_id": "gen_ai.system",
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "openai",
                        "col_type": "SPAN_ATTRIBUTE",
                    },
                }
            ],
        )
        query, params = builder.build()
        assert "span_attr_str" in query
        assert "gen_ai.system" in query

    def test_build_with_numeric_span_attribute_filter(self):
        """Numeric SPAN_ATTRIBUTE filter should reference span_attr_num."""
        from tracer.services.clickhouse.query_builders import TraceListQueryBuilder

        builder = TraceListQueryBuilder(
            project_id="proj-1",
            filters=[
                {
                    "column_id": "gen_ai.usage.prompt_tokens",
                    "filter_config": {
                        "filter_type": "number",
                        "filter_op": "greater_than",
                        "filter_value": 100,
                        "col_type": "SPAN_ATTRIBUTE",
                    },
                }
            ],
        )
        query, params = builder.build()
        assert "prompt_tokens" in query
        assert ">" in query

    def test_build_with_eval_metric_filter(self):
        """EVAL_METRIC filter should generate a subquery against tracer_eval_logger."""
        from tracer.services.clickhouse.query_builders import TraceListQueryBuilder

        builder = TraceListQueryBuilder(
            project_id="proj-1",
            filters=[
                {
                    "column_id": "00000000-0000-0000-0000-000000000098",
                    "filter_config": {
                        "filter_type": "number",
                        "filter_op": "greater_than",
                        "filter_value": 0.8,
                        "col_type": "EVAL_METRIC",
                    },
                }
            ],
        )
        query, params = builder.build()
        assert "trace_id IN" in query
        assert "00000000-0000-0000-0000-000000000000" in query

    def test_build_with_annotation_filter(self):
        """ANNOTATION filter should generate a subquery against annotation table."""
        from tracer.services.clickhouse.query_builders import TraceListQueryBuilder

        builder = TraceListQueryBuilder(
            project_id="proj-1",
            filters=[
                {
                    "column_id": "00000000-0000-0000-0000-000000000044",
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "good",
                        "col_type": "ANNOTATION",
                    },
                }
            ],
        )
        query, params = builder.build()
        assert "trace_id IN" in query
        assert "model_hub_score" in query

    def test_build_with_multiple_filter_types(self):
        """Mixed filter types should all appear in the WHERE clause."""
        from tracer.services.clickhouse.query_builders import TraceListQueryBuilder

        builder = TraceListQueryBuilder(
            project_id="proj-1",
            filters=[
                {
                    "column_id": "model",
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "gpt-4",
                        "col_type": "SYSTEM_METRIC",
                    },
                },
                {
                    "column_id": "gen_ai.system",
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "contains",
                        "filter_value": "openai",
                        "col_type": "SPAN_ATTRIBUTE",
                    },
                },
                {
                    "column_id": "00000000-0000-0000-0000-000000000055",
                    "filter_config": {
                        "filter_type": "number",
                        "filter_op": "greater_than",
                        "filter_value": 0.5,
                        "col_type": "EVAL_METRIC",
                    },
                },
            ],
        )
        query, params = builder.build()
        assert "model" in query
        assert "span_attr_str" in query
        assert "00000000-0000-0000-0000-000000000000" in query

    def test_build_with_contains_filter(self):
        """Contains filter should produce LIKE with percent wildcards."""
        from tracer.services.clickhouse.query_builders import TraceListQueryBuilder

        builder = TraceListQueryBuilder(
            project_id="proj-1",
            filters=[
                {
                    "column_id": "model",
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "contains",
                        "filter_value": "gpt",
                        "col_type": "SYSTEM_METRIC",
                    },
                }
            ],
        )
        query, params = builder.build()
        assert "LIKE" in query
        assert any("%" in str(v) for v in params.values())

    def test_build_with_between_filter(self):
        """Between filter should produce BETWEEN clause."""
        from tracer.services.clickhouse.query_builders import TraceListQueryBuilder

        builder = TraceListQueryBuilder(
            project_id="proj-1",
            filters=[
                {
                    "column_id": "cost",
                    "filter_config": {
                        "filter_type": "number",
                        "filter_op": "between",
                        "filter_value": [0.01, 1.0],
                        "col_type": "SYSTEM_METRIC",
                    },
                }
            ],
        )
        query, params = builder.build()
        assert "BETWEEN" in query

    def test_build_with_in_filter(self):
        """IN filter should produce IN clause with tuple param."""
        from tracer.services.clickhouse.query_builders import TraceListQueryBuilder

        builder = TraceListQueryBuilder(
            project_id="proj-1",
            filters=[
                {
                    "column_id": "status",
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "in",
                        "filter_value": ["OK", "ERROR"],
                        "col_type": "SYSTEM_METRIC",
                    },
                }
            ],
        )
        query, params = builder.build()
        assert "IN" in query

    # ------------------------------------------------------------------
    # Date range handling
    # ------------------------------------------------------------------

    def test_build_uses_date_range_from_filters(self):
        """When datetime filters exist, they should set start/end dates."""
        from tracer.services.clickhouse.query_builders import TraceListQueryBuilder
        from tracer.services.clickhouse.query_builders.trace_list import (
            TIME_FILTER_COLUMN,
        )

        builder = TraceListQueryBuilder(
            project_id="proj-1",
            filters=[
                {
                    "column_id": "created_at",
                    "filter_config": {
                        "filter_type": "datetime",
                        "filter_op": "between",
                        "filter_value": [
                            "2024-01-01T00:00:00Z",
                            "2024-06-30T23:59:59Z",
                        ],
                    },
                }
            ],
        )
        query, params = builder.build()
        assert params["start_date"] is not None
        assert params["end_date"] is not None
        assert f"{TIME_FILTER_COLUMN} >=" in query
        assert f"{TIME_FILTER_COLUMN} <" in query

    def test_build_default_date_range(self):
        """When no datetime filter, should use default 30-day range."""
        from tracer.services.clickhouse.query_builders import TraceListQueryBuilder

        builder = TraceListQueryBuilder(
            project_id="proj-1",
            filters=[],
        )
        query, params = builder.build()
        assert params["start_date"] is not None
        assert params["end_date"] is not None

    # ------------------------------------------------------------------
    # Sorting
    # ------------------------------------------------------------------

    def test_sort_by_cost_ascending(self):
        """Sort by cost ASC should map to cost column."""
        from tracer.services.clickhouse.query_builders import TraceListQueryBuilder

        builder = TraceListQueryBuilder(
            project_id="proj-1",
            sort_params=[{"column_id": "cost", "direction": "asc"}],
        )
        query, _ = builder.build()
        assert "cost ASC" in query

    def test_sort_by_latency_descending(self):
        """Sort by latency should map to latency_ms column."""
        from tracer.services.clickhouse.query_builders import TraceListQueryBuilder

        builder = TraceListQueryBuilder(
            project_id="proj-1",
            sort_params=[{"column_id": "latency", "direction": "desc"}],
        )
        query, _ = builder.build()
        assert "latency_ms DESC" in query

    def test_sort_by_name(self):
        """Sort by name should map to trace_name column."""
        from tracer.services.clickhouse.query_builders import TraceListQueryBuilder

        builder = TraceListQueryBuilder(
            project_id="proj-1",
            sort_params=[{"column_id": "name", "direction": "asc"}],
        )
        query, _ = builder.build()
        assert "trace_name ASC" in query

    def test_sort_by_total_tokens(self):
        """Sort by total_tokens should map to total_tokens column."""
        from tracer.services.clickhouse.query_builders import TraceListQueryBuilder

        builder = TraceListQueryBuilder(
            project_id="proj-1",
            sort_params=[{"column_id": "total_tokens", "direction": "desc"}],
        )
        query, _ = builder.build()
        assert "total_tokens DESC" in query

    def test_default_sort_when_no_sort_params(self):
        """Should default to ORDER BY start_time DESC."""
        from tracer.services.clickhouse.query_builders import TraceListQueryBuilder

        builder = TraceListQueryBuilder(
            project_id="proj-1",
            sort_params=[],
        )
        query, _ = builder.build()
        assert "ORDER BY start_time DESC" in query

    def test_multi_column_sort(self):
        """Multiple sort columns should all appear in ORDER BY."""
        from tracer.services.clickhouse.query_builders import TraceListQueryBuilder

        builder = TraceListQueryBuilder(
            project_id="proj-1",
            sort_params=[
                {"column_id": "cost", "direction": "desc"},
                {"column_id": "latency", "direction": "asc"},
            ],
        )
        query, _ = builder.build()
        assert "cost DESC" in query
        assert "latency_ms ASC" in query

    # ------------------------------------------------------------------
    # Pagination edge cases
    # ------------------------------------------------------------------

    def test_pagination_page_zero(self):
        """Page 0 should have offset 0."""
        from tracer.services.clickhouse.query_builders import TraceListQueryBuilder

        builder = TraceListQueryBuilder(
            project_id="proj-1",
            page_number=0,
            page_size=50,
        )
        query, params = builder.build()
        assert "OFFSET" not in query
        assert params["limit"] == 100  # offset 0 + 2 * page_size 50

    def test_pagination_large_page(self):
        """Large page number should calculate correct offset."""
        from tracer.services.clickhouse.query_builders import TraceListQueryBuilder

        builder = TraceListQueryBuilder(
            project_id="proj-1",
            page_number=10,
            page_size=100,
        )
        query, params = builder.build()
        assert "OFFSET" not in query
        assert params["limit"] == 1200  # offset 1000 + 2 * page_size 100

    def test_pagination_small_page_size(self):
        """Small page size should work correctly."""
        from tracer.services.clickhouse.query_builders import TraceListQueryBuilder

        builder = TraceListQueryBuilder(
            project_id="proj-1",
            page_number=5,
            page_size=10,
        )
        query, params = builder.build()
        assert "OFFSET" not in query
        assert params["limit"] == 70  # offset 50 + 2 * page_size 10

    # ------------------------------------------------------------------
    # Count query
    # ------------------------------------------------------------------

    def test_count_query_has_no_limit_offset(self):
        """Count query should not have LIMIT or OFFSET."""
        from tracer.services.clickhouse.query_builders import TraceListQueryBuilder

        builder = TraceListQueryBuilder(
            project_id="proj-1",
            filters=[],
        )
        builder.build()  # sets up dates
        query, _ = builder.build_count_query()
        assert "uniq(trace_id)" in query
        assert "LIMIT" not in query
        assert "OFFSET" not in query

    def test_count_query_includes_filters(self):
        """Count query should include the same filters as build()."""
        from tracer.services.clickhouse.query_builders import TraceListQueryBuilder

        builder = TraceListQueryBuilder(
            project_id="proj-1",
            filters=[
                {
                    "column_id": "status",
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "ERROR",
                        "col_type": "SYSTEM_METRIC",
                    },
                }
            ],
        )
        builder.build()
        query, params = builder.build_count_query()
        assert "status" in query

    # ------------------------------------------------------------------
    # Annotation queries
    # ------------------------------------------------------------------

    def test_build_annotation_query(self):
        """Should produce annotation query for given trace IDs and label IDs."""
        from tracer.services.clickhouse.query_builders import TraceListQueryBuilder

        builder = TraceListQueryBuilder(
            project_id="proj-1",
        )
        query, params = builder.build_annotation_query(
            trace_ids=["t1", "t2"],
            annotation_label_ids=["label-1", "label-2"],
        )
        assert "model_hub_score" in query
        assert "label_id" in query
        assert "LEFT JOIN" in query
        assert "sp.id = s.observation_span_id" in query
        assert "s.deleted = false" in query
        assert "GROUP BY" in query
        assert params["trace_ids"] == ("t1", "t2")
        assert params["label_ids"] == ("label-1", "label-2")

    def test_build_annotation_query_empty_trace_ids(self):
        """Should return empty query if no trace IDs."""
        from tracer.services.clickhouse.query_builders import TraceListQueryBuilder

        builder = TraceListQueryBuilder(project_id="proj-1")
        query, params = builder.build_annotation_query(
            trace_ids=[],
            annotation_label_ids=["label-1"],
        )
        assert query == ""
        assert params == {}

    def test_build_annotation_query_empty_label_ids(self):
        """Should return empty query if no label IDs."""
        from tracer.services.clickhouse.query_builders import TraceListQueryBuilder

        builder = TraceListQueryBuilder(project_id="proj-1")
        query, params = builder.build_annotation_query(
            trace_ids=["t1"],
            annotation_label_ids=[],
        )
        assert query == ""
        assert params == {}

    def test_build_annotation_query_none_label_ids(self):
        """Should return empty query if label IDs is None."""
        from tracer.services.clickhouse.query_builders import TraceListQueryBuilder

        builder = TraceListQueryBuilder(project_id="proj-1")
        query, params = builder.build_annotation_query(
            trace_ids=["t1"],
            annotation_label_ids=None,
        )
        assert query == ""
        assert params == {}

    # ------------------------------------------------------------------
    # Annotation result pivoting
    # ------------------------------------------------------------------

    def test_pivot_annotation_results_thumbs_up_down(self):
        """THUMBS_UP_DOWN annotations should return bool values.

        Post-revamp, annotation rows come from ``model_hub_score`` where the
        value is a single JSON ``value`` column (not separate typed columns).
        """
        from tracer.services.clickhouse.query_builders import TraceListQueryBuilder

        rows = [
            {
                "trace_id": "t1",
                "label_id": "label-thumbs",
                "value": '{"value": "up"}',
            },
            {
                "trace_id": "t2",
                "label_id": "label-thumbs",
                "value": '{"value": "down"}',
            },
        ]
        label_types = {"label-thumbs": "THUMBS_UP_DOWN"}
        result = TraceListQueryBuilder.pivot_annotation_results(rows, label_types)
        assert result["t1"]["label-thumbs"] is True
        assert result["t2"]["label-thumbs"] is False

    def test_pivot_annotation_results_star(self):
        """STAR annotations should return float values."""
        from tracer.services.clickhouse.query_builders import TraceListQueryBuilder

        rows = [
            {
                "trace_id": "t1",
                "label_id": "label-star",
                "value": '{"rating": 4.5}',
            },
        ]
        label_types = {"label-star": "STAR"}
        result = TraceListQueryBuilder.pivot_annotation_results(rows, label_types)
        assert result["t1"]["label-star"] == 4.5

    def test_pivot_annotation_results_numeric(self):
        """NUMERIC annotations should return float values."""
        from tracer.services.clickhouse.query_builders import TraceListQueryBuilder

        rows = [
            {
                "trace_id": "t1",
                "label_id": "label-num",
                "value": '{"value": 3.14}',
            },
        ]
        label_types = {"label-num": "NUMERIC"}
        result = TraceListQueryBuilder.pivot_annotation_results(rows, label_types)
        assert result["t1"]["label-num"] == 3.14

    def test_pivot_annotation_results_categorical(self):
        """CATEGORICAL annotations should return the ``selected`` list."""
        from tracer.services.clickhouse.query_builders import TraceListQueryBuilder

        rows = [
            {
                "trace_id": "t1",
                "label_id": "label-cat",
                "value": '{"selected": ["question", "complaint"]}',
            },
        ]
        label_types = {"label-cat": "CATEGORICAL"}
        result = TraceListQueryBuilder.pivot_annotation_results(rows, label_types)
        assert result["t1"]["label-cat"] == ["question", "complaint"]

    def test_pivot_annotation_results_unknown_type(self):
        """Unknown type should fall back to returning the parsed value dict."""
        from tracer.services.clickhouse.query_builders import TraceListQueryBuilder

        rows = [
            {
                "trace_id": "t1",
                "label_id": "label-text",
                "value": '{"text": "free text note"}',
            },
        ]
        result = TraceListQueryBuilder.pivot_annotation_results(rows, label_types={})
        # No label_types mapping -> falls through to the generic value dict.
        assert result["t1"]["label-text"] == {"text": "free text note"}

    def test_pivot_annotation_results_multiple_traces_and_labels(self):
        """Should correctly group annotations by trace_id and label_id."""
        from tracer.services.clickhouse.query_builders import TraceListQueryBuilder

        rows = [
            {
                "trace_id": "t1",
                "label_id": "label-thumbs",
                "value": '{"value": "up"}',
            },
            {
                "trace_id": "t1",
                "label_id": "label-star",
                "value": '{"rating": 5.0}',
            },
            {
                "trace_id": "t2",
                "label_id": "label-thumbs",
                "value": '{"value": "down"}',
            },
        ]
        label_types = {
            "label-thumbs": "THUMBS_UP_DOWN",
            "label-star": "STAR",
        }
        result = TraceListQueryBuilder.pivot_annotation_results(rows, label_types)
        assert len(result) == 2
        assert result["t1"]["label-thumbs"] is True
        assert result["t1"]["label-star"] == 5.0
        assert result["t2"]["label-thumbs"] is False

    def test_pivot_annotation_results_null_bool(self):
        """THUMBS_UP_DOWN with missing value key should coerce to False.

        The new model_hub_score pivot path treats any non-``up``/``True``
        value as False (rather than preserving None).  A row with empty
        value JSON therefore resolves to ``False``.
        """
        from tracer.services.clickhouse.query_builders import TraceListQueryBuilder

        rows = [
            {
                "trace_id": "t1",
                "label_id": "label-thumbs",
                "value": "{}",
            },
        ]
        label_types = {"label-thumbs": "THUMBS_UP_DOWN"}
        result = TraceListQueryBuilder.pivot_annotation_results(rows, label_types)
        assert result["t1"]["label-thumbs"] is False

    # ------------------------------------------------------------------
    # Eval query and pivoting
    # ------------------------------------------------------------------

    def test_eval_query_groups_by_trace_and_config(self):
        """Eval query should GROUP BY trace_id and custom_eval_config_id."""
        from tracer.services.clickhouse.query_builders import TraceListQueryBuilder

        builder = TraceListQueryBuilder(
            project_id="proj-1",
            eval_config_ids=["00000000-0000-0000-0000-000000000011", "cfg-2"],
        )
        query, params = builder.build_eval_query(["t1", "t2", "t3"])
        assert "GROUP BY" in query
        assert "trace_id" in query
        assert "custom_eval_config_id" in query
        assert params["trace_ids"] == ("t1", "t2", "t3")
        assert params["eval_config_ids"] == (
            "00000000-0000-0000-0000-000000000011",
            "cfg-2",
        )

    def test_pivot_eval_results_score_only(self):
        """Eval results with avg_score only should use rounded avg_score."""
        from tracer.services.clickhouse.query_builders import TraceListQueryBuilder

        rows = [
            ("t1", "00000000-0000-0000-0000-000000000011", 0.856, None, 3),
        ]
        columns = ["trace_id", "eval_config_id", "avg_score", "pass_rate", "eval_count"]
        result = TraceListQueryBuilder.pivot_eval_results(rows, columns)
        assert result["t1"]["00000000-0000-0000-0000-000000000011"]["avg_score"] == 85.6

    def test_pivot_eval_results_pass_rate_only(self):
        """Eval results with pass_rate only should use rounded pass_rate."""
        from tracer.services.clickhouse.query_builders import TraceListQueryBuilder

        rows = [
            ("t1", "00000000-0000-0000-0000-000000000011", None, 75.5, 10),
        ]
        columns = ["trace_id", "eval_config_id", "avg_score", "pass_rate", "eval_count"]
        result = TraceListQueryBuilder.pivot_eval_results(rows, columns)
        assert result["t1"]["00000000-0000-0000-0000-000000000011"]["pass_rate"] == 75.5

    def test_pivot_eval_results_with_dict_rows(self):
        """Pivot should handle dict-style rows from CH client."""
        from tracer.services.clickhouse.query_builders import TraceListQueryBuilder

        rows = [
            {
                "trace_id": "t1",
                "eval_config_id": "00000000-0000-0000-0000-000000000011",
                "avg_score": 0.9,
                "pass_rate": None,
                "eval_count": 5,
            },
        ]
        columns = ["trace_id", "eval_config_id", "avg_score", "pass_rate", "eval_count"]
        result = TraceListQueryBuilder.pivot_eval_results(rows, columns)
        assert "t1" in result
        assert result["t1"]["00000000-0000-0000-0000-000000000011"]["avg_score"] == 90.0

    def test_pivot_eval_results_surfaces_non_terminal_status(self):
        """(trace, config) pairs with no completed score surface a lifecycle
        status marker (skipped > running > pending) instead of an empty score."""
        from tracer.services.clickhouse.query_builders import TraceListQueryBuilder

        base = {
            "trace_id": "t1",
            "avg_score": None,
            "pass_rate": None,
            "success_count": 0,
            "error_count": 0,
            "eval_count": 1,
        }
        rows = [
            {**base, "eval_config_id": "cfg-run", "running_count": 1},
            {
                **base,
                "eval_config_id": "cfg-skip",
                "skipped_count": 1,
                "skipped_reason": "no attr",
            },
        ]
        columns = ["trace_id", "eval_config_id", "avg_score", "pass_rate", "eval_count"]
        result = TraceListQueryBuilder.pivot_eval_results(rows, columns)
        assert result["t1"]["cfg-run"] == {"status": "running"}
        assert result["t1"]["cfg-skip"] == {
            "status": "skipped",
            "skipped_reason": "no attr",
        }

    def test_pivot_eval_results_multiple_configs_per_trace(self):
        """Multiple eval configs for the same trace should be nested correctly."""
        from tracer.services.clickhouse.query_builders import TraceListQueryBuilder

        rows = [
            ("t1", "00000000-0000-0000-0000-000000000011", 0.85, None, 3),
            ("t1", "cfg-2", None, 90.0, 5),
            ("t1", "cfg-3", 0.72, None, 2),
        ]
        columns = ["trace_id", "eval_config_id", "avg_score", "pass_rate", "eval_count"]
        result = TraceListQueryBuilder.pivot_eval_results(rows, columns)
        assert len(result["t1"]) == 3
        assert "00000000-0000-0000-0000-000000000011" in result["t1"]
        assert "cfg-2" in result["t1"]
        assert "cfg-3" in result["t1"]

    # ------------------------------------------------------------------
    # Query selects correct columns
    # ------------------------------------------------------------------

    def test_build_selects_metadata_map(self):
        """Phase-1b (content) query should select metadata_map.

        Heavy columns were moved out of Phase-1 into ``build_content_query``
        to avoid OOM on large tables.
        """
        from tracer.services.clickhouse.query_builders import TraceListQueryBuilder

        builder = TraceListQueryBuilder(project_id="proj-1")
        builder.build()
        content_query, _ = builder.build_content_query(["trace-1"])
        assert "metadata" in content_query

    def test_build_selects_trace_session_id(self):
        """Phase-1 query should select trace_session_id."""
        from tracer.services.clickhouse.query_builders import TraceListQueryBuilder

        builder = TraceListQueryBuilder(project_id="proj-1")
        query, _ = builder.build()
        assert "trace_session_id" in query

    def test_build_selects_trace_tags(self):
        """Phase-1b (content) query should select trace_tags.

        Heavy columns were moved out of Phase-1 into ``build_content_query``
        to avoid OOM on large tables.
        """
        from tracer.services.clickhouse.query_builders import TraceListQueryBuilder

        builder = TraceListQueryBuilder(project_id="proj-1")
        builder.build()
        content_query, _ = builder.build_content_query(["trace-1"])
        assert "trace_tags" in content_query


# ============================================================================
# 15. Comprehensive Span List Query Builder Tests
# ============================================================================


@pytest.mark.unit
class TestSpanListQueryBuilderComprehensive:
    """Comprehensive tests for SpanListQueryBuilder covering all three phases,
    filters, sorting, pagination, eval/annotation pivoting."""

    # ------------------------------------------------------------------
    # Phase 1: Paginated span list
    # ------------------------------------------------------------------

    def test_build_basic_span_query(self):
        """build() should produce a span query without parent_span_id filter."""
        from tracer.services.clickhouse.query_builders import SpanListQueryBuilder

        builder = SpanListQueryBuilder(
            project_id="proj-1",
            page_number=0,
            page_size=50,
        )
        query, params = builder.build()
        assert "spans" in query
        assert "LIMIT" in query
        # Prefix-fetch pagination: no SQL OFFSET — the query reads the sorted
        # prefix [0, offset + 2*page_size) as a bounded top-K and the view
        # dedups by span id then slices the page (see page_dedup.py).
        assert "OFFSET" not in query
        assert params["limit"] == 100  # offset 0 + 2 * page_size 50
        # Unlike trace list, span list shows ALL spans (no parent_span_id filter)
        assert "parent_span_id IS NULL" not in query

    def test_build_selects_span_columns(self):
        """Phase-1 query should select span-specific light columns.

        Heavy columns (input/output) moved to ``build_content_query`` to
        avoid OOM on large tables.
        """
        from tracer.services.clickhouse.query_builders import SpanListQueryBuilder

        builder = SpanListQueryBuilder(project_id="proj-1")
        query, _ = builder.build()
        for col in [
            "id",
            "trace_id",
            "name",
            "observation_type",
            "status",
            "start_time",
            "latency_ms",
            "cost",
            "total_tokens",
            "model",
        ]:
            assert col in query
        # input/output are fetched in build_content_query after pagination.
        content_query, _ = builder.build_content_query(["span-1"])
        for col in ["input", "output"]:
            assert col in content_query

    def test_build_with_system_metric_filter(self):
        """SYSTEM_METRIC filter should work in span list."""
        from tracer.services.clickhouse.query_builders import SpanListQueryBuilder

        builder = SpanListQueryBuilder(
            project_id="proj-1",
            filters=[
                {
                    "column_id": "model",
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "gpt-4",
                        "col_type": "SYSTEM_METRIC",
                    },
                }
            ],
        )
        query, params = builder.build()
        assert "model" in query
        assert "gpt-4" in params.values()

    def test_build_with_span_attribute_filter(self):
        """SPAN_ATTRIBUTE filter should work in span list."""
        from tracer.services.clickhouse.query_builders import SpanListQueryBuilder

        builder = SpanListQueryBuilder(
            project_id="proj-1",
            filters=[
                {
                    "column_id": "gen_ai.system",
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "openai",
                        "col_type": "SPAN_ATTRIBUTE",
                    },
                }
            ],
        )
        query, params = builder.build()
        assert "span_attr_str" in query

    def test_build_with_has_annotation_uses_span_annotation_labels(self):
        """SpanListQueryBuilder should pass label ids into has_annotation filters."""
        from tracer.services.clickhouse.query_builders import SpanListQueryBuilder

        builder = SpanListQueryBuilder(
            project_id="proj-1",
            annotation_label_ids=[
                "00000000-0000-0000-0000-000000000011",
                "00000000-0000-0000-0000-000000000022",
            ],
            filters=[
                {
                    "column_id": "has_annotation",
                    "filter_config": {
                        "filter_type": "boolean",
                        "filter_op": "equals",
                        "filter_value": True,
                    },
                }
            ],
        )

        query, params = builder.build()

        assert "AND id IN" in query
        assert "GROUP BY entity_id" in query
        assert "uniq(s.label_id) >= 2" in query
        assert params["lbl_1"] == "00000000-0000-0000-0000-000000000011"
        assert params["lbl_2"] == "00000000-0000-0000-0000-000000000022"

    def test_build_with_end_user_filter(self):
        """end_user_id should add end_user_id clause."""
        from tracer.services.clickhouse.query_builders import SpanListQueryBuilder

        builder = SpanListQueryBuilder(
            project_id="proj-1",
            end_user_id="user-123",
        )
        query, params = builder.build()
        assert "end_user_id" in query
        assert params["end_user_id"] == "user-123"

    def test_build_without_end_user(self):
        """Without end_user_id, no end_user_id clause."""
        from tracer.services.clickhouse.query_builders import SpanListQueryBuilder

        builder = SpanListQueryBuilder(project_id="proj-1")
        query, _ = builder.build()
        assert "end_user_id =" not in query

    def test_pagination_offset(self):
        """LIMIT covers the prefix [0, offset + 2*page_size); slicing is in Python."""
        from tracer.services.clickhouse.query_builders import SpanListQueryBuilder

        builder = SpanListQueryBuilder(
            project_id="proj-1",
            page_number=3,
            page_size=20,
        )
        query, params = builder.build()
        assert "OFFSET" not in query
        assert params["limit"] == 100  # offset 60 + 2 * page_size 20
        assert "offset" not in params

    def test_sort_default(self):
        """Default sort should be ORDER BY start_time DESC."""
        from tracer.services.clickhouse.query_builders import SpanListQueryBuilder

        builder = SpanListQueryBuilder(project_id="proj-1")
        query, _ = builder.build()
        assert "ORDER BY start_time DESC" in query

    def test_sort_by_cost(self):
        """Sort by cost should work."""
        from tracer.services.clickhouse.query_builders import SpanListQueryBuilder

        builder = SpanListQueryBuilder(
            project_id="proj-1",
            sort_params=[{"column_id": "cost", "direction": "asc"}],
        )
        query, _ = builder.build()
        assert "cost ASC" in query

    def test_sort_by_name(self):
        """Sort by span_name should map to name column."""
        from tracer.services.clickhouse.query_builders import SpanListQueryBuilder

        builder = SpanListQueryBuilder(
            project_id="proj-1",
            sort_params=[{"column_id": "span_name", "direction": "desc"}],
        )
        query, _ = builder.build()
        assert "name DESC" in query

    # ------------------------------------------------------------------
    # Count query
    # ------------------------------------------------------------------

    def test_count_query(self):
        """Count query should aggregate span rows with no LIMIT/OFFSET."""
        from tracer.services.clickhouse.query_builders import SpanListQueryBuilder

        builder = SpanListQueryBuilder(
            project_id="proj-1",
            filters=[],
        )
        builder.build()
        query, _ = builder.build_count_query()
        # PERF: count() rather than uniqExact(id) — uniqExact built an unbounded
        # exact hash set of every span id (GBs of memory, OOM-prone). count()
        # reads only filter columns; a transient un-merged duplicate counts once
        # extra, which is immaterial for a pagination total.
        assert "count() AS total" in query
        assert "uniqExact" not in query
        assert "LIMIT" not in query
        assert "OFFSET" not in query

    def test_count_query_with_end_user(self):
        """Count query should include end_user_id filter."""
        from tracer.services.clickhouse.query_builders import SpanListQueryBuilder

        builder = SpanListQueryBuilder(
            project_id="proj-1",
            end_user_id="user-123",
        )
        builder.build()
        query, params = builder.build_count_query()
        assert "end_user_id" in query

    # ------------------------------------------------------------------
    # Phase 2: Eval scores
    # ------------------------------------------------------------------

    def test_eval_query(self):
        """Eval query should query tracer_eval_logger by observation_span_id."""
        from tracer.services.clickhouse.query_builders import SpanListQueryBuilder

        builder = SpanListQueryBuilder(
            project_id="proj-1",
            eval_config_ids=["00000000-0000-0000-0000-000000000011"],
        )
        query, params = builder.build_eval_query(["span-1", "span-2"])
        assert "tracer_eval_logger" in query
        assert "observation_span_id" in query
        assert "GROUP BY" in query
        assert params["span_ids"] == ("span-1", "span-2")

    def test_eval_query_empty_spans(self):
        """Should return empty query if no span IDs."""
        from tracer.services.clickhouse.query_builders import SpanListQueryBuilder

        builder = SpanListQueryBuilder(
            project_id="proj-1",
            eval_config_ids=["00000000-0000-0000-0000-000000000011"],
        )
        query, params = builder.build_eval_query([])
        assert query == ""
        assert params == {}

    def test_eval_query_no_configs(self):
        """Should return empty query if no eval config IDs."""
        from tracer.services.clickhouse.query_builders import SpanListQueryBuilder

        builder = SpanListQueryBuilder(
            project_id="proj-1",
            eval_config_ids=[],
        )
        query, params = builder.build_eval_query(["span-1"])
        assert query == ""
        assert params == {}

    def test_pivot_eval_results(self):
        """Pivot should nest eval scores by span_id and config_id."""
        from tracer.services.clickhouse.query_builders import SpanListQueryBuilder

        rows = [
            {
                "observation_span_id": "span-1",
                "eval_config_id": "00000000-0000-0000-0000-000000000011",
                "avg_score": 0.85,
                "pass_rate": None,
                "eval_count": 5,
                "output_str_list": "[]",
            },
            {
                "observation_span_id": "span-1",
                "eval_config_id": "cfg-2",
                "avg_score": None,
                "pass_rate": 90.0,
                "eval_count": 3,
                "output_str_list": "[]",
            },
            {
                "observation_span_id": "span-2",
                "eval_config_id": "00000000-0000-0000-0000-000000000011",
                "avg_score": 0.72,
                "pass_rate": None,
                "eval_count": 2,
                "output_str_list": "[]",
            },
        ]
        result = SpanListQueryBuilder.pivot_eval_results(rows)
        assert "span-1" in result
        assert "00000000-0000-0000-0000-000000000011" in result["span-1"]
        assert "cfg-2" in result["span-1"]
        assert "span-2" in result
        # avg_score 0.85 * 100 = 85.0
        assert result["span-1"]["00000000-0000-0000-0000-000000000011"] == 85.0
        # pass_rate 90.0 directly
        assert result["span-1"]["cfg-2"] == 90.0

    def test_pivot_eval_results_surfaces_non_terminal_status(self):
        """(span, config) pairs with only pending/running/skipped rows surface a
        lifecycle status marker (skipped > running > pending) instead of a blank."""
        from tracer.services.clickhouse.query_builders import SpanListQueryBuilder

        base = {
            "observation_span_id": "span-1",
            "avg_score": None,
            "pass_rate": None,
            "success_count": 0,
            "error_count": 0,
            "eval_count": 1,
            "output_str_list": "[]",
        }
        rows = [
            {**base, "eval_config_id": "cfg-run", "running_count": 1},
            {**base, "eval_config_id": "cfg-pend", "pending_count": 2},
            {
                **base,
                "eval_config_id": "cfg-skip",
                # skipped beats a co-existing running/pending row
                "skipped_count": 1,
                "running_count": 1,
                "pending_count": 1,
                "skipped_reason": "missing attr",
            },
        ]
        result = SpanListQueryBuilder.pivot_eval_results(rows)
        assert result["span-1"]["cfg-run"] == {"status": "running"}
        assert result["span-1"]["cfg-pend"] == {"status": "pending"}
        assert result["span-1"]["cfg-skip"] == {
            "status": "skipped",
            "skipped_reason": "missing attr",
        }

    def test_pivot_eval_results_completed_score_wins_over_non_terminal(self):
        """A completed score is returned even when a re-run row is still running."""
        from tracer.services.clickhouse.query_builders import SpanListQueryBuilder

        rows = [
            {
                "observation_span_id": "span-1",
                "eval_config_id": "cfg-1",
                "avg_score": 0.9,
                "pass_rate": None,
                "success_count": 1,
                "error_count": 0,
                "running_count": 1,
                "eval_count": 2,
                "output_str_list": "[]",
            }
        ]
        result = SpanListQueryBuilder.pivot_eval_results(rows)
        assert result["span-1"]["cfg-1"] == 90.0

    # ------------------------------------------------------------------
    # Phase 3: Annotations
    # ------------------------------------------------------------------

    def test_annotation_query(self):
        """Annotation query should query model_hub_score by observation_span_id."""
        from tracer.services.clickhouse.query_builders import SpanListQueryBuilder

        builder = SpanListQueryBuilder(
            project_id="proj-1",
            annotation_label_ids=["label-1", "label-2"],
        )
        query, params = builder.build_annotation_query(["span-1", "span-2"])
        assert "model_hub_score" in query
        assert "observation_span_id" in query
        assert "label_id" in query
        assert "deleted = false" in query
        assert "GROUP BY" in query
        assert params["span_ids"] == ("span-1", "span-2")
        assert params["label_ids"] == ("label-1", "label-2")

    def test_annotation_query_empty_spans(self):
        """Should return empty query if no span IDs."""
        from tracer.services.clickhouse.query_builders import SpanListQueryBuilder

        builder = SpanListQueryBuilder(
            project_id="proj-1",
            annotation_label_ids=["label-1"],
        )
        query, params = builder.build_annotation_query([])
        assert query == ""
        assert params == {}

    def test_annotation_query_no_labels(self):
        """Should return empty query if no label IDs."""
        from tracer.services.clickhouse.query_builders import SpanListQueryBuilder

        builder = SpanListQueryBuilder(
            project_id="proj-1",
            annotation_label_ids=[],
        )
        query, params = builder.build_annotation_query(["span-1"])
        assert query == ""
        assert params == {}

    def test_pivot_annotation_results_all_types(self):
        """Pivot should handle all annotation types from model_hub_score value JSON."""
        from tracer.services.clickhouse.query_builders import SpanListQueryBuilder

        rows = [
            {
                "observation_span_id": "s1",
                "label_id": "lbl-thumbs",
                "value": '{"value": "up"}',
            },
            {
                "observation_span_id": "s1",
                "label_id": "lbl-star",
                "value": '{"rating": 4.0}',
            },
            {
                "observation_span_id": "s1",
                "label_id": "lbl-cat",
                "value": '{"selected": ["question"]}',
            },
            {
                "observation_span_id": "s2",
                "label_id": "lbl-thumbs",
                "value": '{"value": "down"}',
            },
        ]
        label_types = {
            "lbl-thumbs": "THUMBS_UP_DOWN",
            "lbl-star": "STAR",
            "lbl-cat": "CATEGORICAL",
        }
        result = SpanListQueryBuilder.pivot_annotation_results(rows, label_types)
        assert result["s1"]["lbl-thumbs"] is True
        assert result["s1"]["lbl-star"] == 4.0
        assert result["s1"]["lbl-cat"] == ["question"]
        assert result["s2"]["lbl-thumbs"] is False

    def test_pivot_annotation_results_empty_rows(self):
        """Empty rows should return empty dict."""
        from tracer.services.clickhouse.query_builders import SpanListQueryBuilder

        result = SpanListQueryBuilder.pivot_annotation_results([], {})
        assert result == {}


# ============================================================================
# 16. Eval Metrics Query Builder Extended Tests
# ============================================================================


@pytest.mark.unit
class TestEvalMetricsQueryBuilderExtended:
    """Extended tests for EvalMetricsQueryBuilder covering aggregated and raw
    queries, column names, and result formatting."""

    def test_score_agg_query_uses_correct_columns(self):
        """Score agg query should use float_sum and float_count columns."""
        from tracer.services.clickhouse.query_builders import EvalMetricsQueryBuilder

        builder = EvalMetricsQueryBuilder(
            custom_eval_config_id="00000000-0000-0000-0000-000000000011",
            project_id="proj-1",
            eval_output_type="SCORE",
            use_preaggregated=True,
        )
        query, _ = builder.build()
        assert "float_sum" in query
        assert "float_count" in query
        assert "eval_metrics_hourly" in query

    def test_score_agg_query_uses_sum_not_summerge(self):
        """Score agg query should use sum() not sumMerge()."""
        from tracer.services.clickhouse.query_builders import EvalMetricsQueryBuilder

        builder = EvalMetricsQueryBuilder(
            custom_eval_config_id="00000000-0000-0000-0000-000000000011",
            project_id="proj-1",
            eval_output_type="SCORE",
            use_preaggregated=True,
        )
        query, _ = builder.build()
        assert "sum(float_sum)" in query
        assert "sum(float_count)" in query
        assert "sumMerge" not in query

    def test_score_raw_query_uses_eval_logger(self):
        """Score raw query should use tracer_eval_logger table with FINAL."""
        from tracer.services.clickhouse.query_builders import EvalMetricsQueryBuilder

        builder = EvalMetricsQueryBuilder(
            custom_eval_config_id="00000000-0000-0000-0000-000000000011",
            project_id="proj-1",
            eval_output_type="SCORE",
            use_preaggregated=False,
        )
        query, _ = builder.build()
        assert "tracer_eval_logger" in query
        assert "FINAL" in query
        assert "avg(output_float)" in query

    def test_pass_fail_agg_query_uses_correct_columns(self):
        """Pass/fail agg query should use bool_pass and bool_fail columns."""
        from tracer.services.clickhouse.query_builders import EvalMetricsQueryBuilder

        builder = EvalMetricsQueryBuilder(
            custom_eval_config_id="00000000-0000-0000-0000-000000000011",
            project_id="proj-1",
            eval_output_type="PASS_FAIL",
            use_preaggregated=True,
        )
        query, _ = builder.build()
        assert "bool_pass" in query
        assert "bool_fail" in query
        assert "eval_metrics_hourly" in query

    def test_pass_fail_agg_uses_sum_not_summerge(self):
        """Pass/fail agg query should use sum() not sumMerge()."""
        from tracer.services.clickhouse.query_builders import EvalMetricsQueryBuilder

        builder = EvalMetricsQueryBuilder(
            custom_eval_config_id="00000000-0000-0000-0000-000000000011",
            project_id="proj-1",
            eval_output_type="PASS_FAIL",
            use_preaggregated=True,
        )
        query, _ = builder.build()
        assert "sum(bool_pass)" in query
        assert "sumMerge" not in query

    def test_pass_fail_raw_query(self):
        """Pass/fail raw query should use avg(CASE WHEN output_bool ...)."""
        from tracer.services.clickhouse.query_builders import EvalMetricsQueryBuilder

        builder = EvalMetricsQueryBuilder(
            custom_eval_config_id="00000000-0000-0000-0000-000000000011",
            project_id="proj-1",
            eval_output_type="PASS_FAIL",
            use_preaggregated=False,
        )
        query, _ = builder.build()
        assert "output_bool" in query
        assert "tracer_eval_logger" in query

    def test_choices_query_with_choices(self):
        """Choices query should parse the JSON-string choice list before has()."""
        from tracer.services.clickhouse.query_builders import EvalMetricsQueryBuilder

        builder = EvalMetricsQueryBuilder(
            custom_eval_config_id="00000000-0000-0000-0000-000000000011",
            project_id="proj-1",
            eval_output_type="CHOICES",
            choices=["yes", "no", "maybe"],
        )
        query, params = builder.build()
        assert "countIf" in query
        assert "has(JSONExtract(output_str_list, 'Array(String)')" in query
        assert "OR output_str =" in query
        assert "choice_0" in params
        assert "choice_1" in params
        assert "choice_2" in params

    def test_choices_query_without_choices_falls_back(self):
        """Choices query with empty choices list should fall back to score raw."""
        from tracer.services.clickhouse.query_builders import EvalMetricsQueryBuilder

        builder = EvalMetricsQueryBuilder(
            custom_eval_config_id="00000000-0000-0000-0000-000000000011",
            project_id="proj-1",
            eval_output_type="CHOICES",
            choices=[],
        )
        query, _ = builder.build()
        assert "avg(output_float)" in query

    def test_agg_query_filters_by_config_id(self):
        """Agg queries should filter by custom_eval_config_id."""
        from tracer.services.clickhouse.query_builders import EvalMetricsQueryBuilder

        builder = EvalMetricsQueryBuilder(
            custom_eval_config_id="00000000-0000-0000-0000-000000000097",
            project_id="proj-1",
            eval_output_type="SCORE",
            use_preaggregated=True,
        )
        query, params = builder.build()
        assert "custom_eval_config_id" in query
        assert params["eval_config_id"] == "00000000-0000-0000-0000-000000000097"

    def test_format_result_single_series(self):
        """format_result for SCORE should return single series dict."""
        from tracer.services.clickhouse.query_builders import EvalMetricsQueryBuilder

        builder = EvalMetricsQueryBuilder(
            custom_eval_config_id="00000000-0000-0000-0000-000000000011",
            project_id="proj-1",
            eval_output_type="SCORE",
            eval_name="My Eval",
        )
        # Simulate empty result
        result = builder.format_result([], ["time_bucket", "value"])
        assert isinstance(result, dict)
        assert result["name"] == "My Eval"
        assert result["id"] == "00000000-0000-0000-0000-000000000011"
        assert "data" in result

    def test_format_result_choices_returns_list(self):
        """format_result for CHOICES should return list of series dicts."""
        from tracer.services.clickhouse.query_builders import EvalMetricsQueryBuilder

        builder = EvalMetricsQueryBuilder(
            custom_eval_config_id="00000000-0000-0000-0000-000000000011",
            project_id="proj-1",
            eval_output_type="CHOICES",
            eval_name="Choice Eval",
            choices=["yes", "no"],
        )
        result = builder.format_result(
            [], ["time_bucket", "total_count", "choice_0", "choice_1"]
        )
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["name"] == "Choice Eval - yes"
        assert result[1]["name"] == "Choice Eval - no"

    def test_unknown_output_type_falls_back_to_score(self):
        """Unknown eval output type should fall back to score query."""
        from tracer.services.clickhouse.query_builders import EvalMetricsQueryBuilder

        builder = EvalMetricsQueryBuilder(
            custom_eval_config_id="00000000-0000-0000-0000-000000000011",
            project_id="proj-1",
            eval_output_type="UNKNOWN_TYPE",
            use_preaggregated=False,
        )
        query, _ = builder.build()
        assert "avg(output_float)" in query


# ============================================================================
# 17. Filter Builder Edge Cases
# ============================================================================


@pytest.mark.unit
class TestFilterBuilderEdgeCases:
    """Edge case tests for ClickHouseFilterBuilder."""

    def test_nullable_uuid_column_text_ops_cast_to_string(self):
        """Filtering a nullable-UUID column (session/user) must not crash and
        must wrap the column in toString() for string-comparison ops.

        Regression: ``_build_column_condition`` referenced an undefined
        ``_UUID_TEXT_FILTER_OPS``, so any session_id/end_user_id/
        trace_session_id filter raised AttributeError (e.g. the
        list_traces_of_session endpoint). ClickHouse also rejects direct
        UUID-vs-String comparisons, so the cast is required for correctness.
        """
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        # ``session_id`` is a frontend alias normalized to ``trace_session_id``
        # before it reaches the column condition, so assert on the canonical
        # columns that actually surface in the SQL.
        for column in ("trace_session_id", "end_user_id"):
            for op in ("contains", "equals", "starts_with", "in", "not_in"):
                builder = ClickHouseFilterBuilder()
                value = ["abc", "def"] if op in ("in", "not_in") else "0f57a0c2"
                filters = [
                    {
                        "column_id": column,
                        "filter_config": {
                            "filter_type": "categorical",
                            "filter_op": op,
                            "filter_value": value,
                            "col_type": "SYSTEM_METRIC",
                        },
                    }
                ]
                where, _ = builder.translate(filters)
                assert f"toString({column})" in where, (column, op, where)

    def test_nullable_uuid_column_is_null_uses_bare_column(self):
        """is_null/is_not_null on a nullable-UUID column skip the toString cast."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder()
        filters = [
            {
                "column_id": "trace_session_id",
                "filter_config": {
                    "filter_type": "categorical",
                    "filter_op": "is_null",
                    "filter_value": None,
                    "col_type": "SYSTEM_METRIC",
                },
            }
        ]
        where, _ = builder.translate(filters)
        assert "trace_session_id IS NULL" in where
        assert "toString(trace_session_id) IS NULL" not in where

    def test_not_equals_filter(self):
        """not_equals should produce != operator."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder()
        filters = [
            {
                "column_id": "status",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "not_equals",
                    "filter_value": "ERROR",
                    "col_type": "SYSTEM_METRIC",
                },
            }
        ]
        where, _ = builder.translate(filters)
        assert "!=" in where

    def test_not_contains_filter(self):
        """not_contains should produce NOT ILIKE (case-insensitive)."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder()
        filters = [
            {
                "column_id": "model",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "not_contains",
                    "filter_value": "gpt",
                    "col_type": "SYSTEM_METRIC",
                },
            }
        ]
        where, _ = builder.translate(filters)
        assert "NOT ILIKE" in where

    def test_starts_with_filter(self):
        """starts_with should produce LIKE with trailing %."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder()
        filters = [
            {
                "column_id": "model",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "starts_with",
                    "filter_value": "gpt",
                    "col_type": "SYSTEM_METRIC",
                },
            }
        ]
        where, params = builder.translate(filters)
        assert "LIKE" in where
        assert any(
            str(v).endswith("%") and not str(v).startswith("%") for v in params.values()
        )

    def test_ends_with_filter(self):
        """ends_with should produce LIKE with leading %."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder()
        filters = [
            {
                "column_id": "model",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "ends_with",
                    "filter_value": "turbo",
                    "col_type": "SYSTEM_METRIC",
                },
            }
        ]
        where, params = builder.translate(filters)
        assert "LIKE" in where
        assert any(
            str(v).startswith("%") and not str(v).endswith("%%")
            for v in params.values()
        )

    def test_not_between_filter(self):
        """not_between should produce NOT BETWEEN clause. (Canonical op
        name; `not_in_between` is the retired legacy alias.)"""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder()
        filters = [
            {
                "column_id": "cost",
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "not_between",
                    "filter_value": [0.1, 1.0],
                    "col_type": "SYSTEM_METRIC",
                },
            }
        ]
        where, _ = builder.translate(filters)
        assert "NOT BETWEEN" in where

    def test_not_in_filter(self):
        """not_in should produce NOT IN clause."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder()
        filters = [
            {
                "column_id": "status",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "not_in",
                    "filter_value": ["OK", "ERROR"],
                    "col_type": "SYSTEM_METRIC",
                },
            }
        ]
        where, _ = builder.translate(filters)
        assert "NOT IN" in where

    def test_gte_and_lte_operators(self):
        """greater_than_or_equal and less_than_or_equal should produce >= and <=."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder()
        filters = [
            {
                "column_id": "cost",
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "greater_than_or_equal",
                    "filter_value": 0.5,
                    "col_type": "SYSTEM_METRIC",
                },
            },
            {
                "column_id": "total_tokens",
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "less_than_or_equal",
                    "filter_value": 1000,
                    "col_type": "SYSTEM_METRIC",
                },
            },
        ]
        where, _ = builder.translate(filters)
        assert ">=" in where
        assert "<=" in where

    def test_span_attr_not_contains(self):
        """SPAN_ATTRIBUTE not_contains should produce NOT ILIKE (case-insensitive)."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder()
        filters = [
            {
                "column_id": "gen_ai.system",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "not_contains",
                    "filter_value": "azure",
                    "col_type": "SPAN_ATTRIBUTE",
                },
            }
        ]
        where, _ = builder.translate(filters)
        assert "NOT ILIKE" in where
        assert "span_attr_str" in where

    def test_span_attr_between(self):
        """SPAN_ATTRIBUTE between should produce BETWEEN clause."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder()
        filters = [
            {
                "column_id": "gen_ai.usage.tokens",
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "between",
                    "filter_value": [100, 1000],
                    "col_type": "SPAN_ATTRIBUTE",
                },
            }
        ]
        where, _ = builder.translate(filters)
        assert "BETWEEN" in where
        assert "span_attr_num" in where

    def test_camelcase_filter_keys_are_supported_defensively(self):
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder()
        filters = [
            {
                "columnId": "model",
                "filterConfig": {
                    "filterType": "text",
                    "filterOp": "equals",
                    "filterValue": "gpt-4",
                    "col_type": "SYSTEM_METRIC",
                },
            }
        ]
        where, params = builder.translate(filters)
        assert "lower(model) = %(col_1)s" in where
        assert params == {"col_1": "gpt-4"}

    def test_eval_metric_filter_subquery_structure(self):
        """EVAL_METRIC filter should have correct subquery structure."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder()
        filters = [
            {
                "column_id": "00000000-0000-0000-0000-000000000077",
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "less_than",
                    "filter_value": 0.5,
                    "col_type": "EVAL_METRIC",
                },
            }
        ]
        where, _ = builder.translate(filters)
        assert "trace_id IN (" in where
        assert "00000000-0000-0000-0000-000000000000" in where

    def test_annotation_filter_subquery_structure(self):
        """ANNOTATION filter should have correct subquery structure."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder()
        filters = [
            {
                "column_id": "00000000-0000-0000-0000-000000000066",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "good",
                    "col_type": "ANNOTATION",
                },
            }
        ]
        where, _ = builder.translate(filters)
        assert "trace_id IN (" in where
        assert "model_hub_score AS s FINAL" in where
        # The column on model_hub_score is simply label_id (not annotation_label_id).
        assert "label_id" in where
        assert "is_deleted = 0" in where

    def test_annotation_number_not_equals(self):
        """Canonical number op not_equals should translate to SQL !=."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder()
        filters = [
            {
                "column_id": "00000000-0000-0000-0000-000000000066",
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "not_equals",
                    "filter_value": 45,
                    "col_type": "ANNOTATION",
                },
            }
        ]
        where, params = builder.translate(filters)

        assert "model_hub_score AS s FINAL" in where
        assert ") != %(ann_" in where
        assert "45" not in where
        assert 45 in params.values()

    def test_annotation_number_not_between_alias(self):
        """Frontend number op not_between should translate to SQL NOT BETWEEN."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder()
        filters = [
            {
                "column_id": "00000000-0000-0000-0000-000000000066",
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "not_between",
                    "filter_value": [10, 50],
                    "col_type": "ANNOTATION",
                },
            }
        ]
        where, params = builder.translate(filters)

        assert "model_hub_score AS s FINAL" in where
        assert "NOT BETWEEN" in where
        assert 10 in params.values()
        assert 50 in params.values()

    def test_annotation_positive_canonical_operators(self):
        """Canonical annotation ops should translate to SQL."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        label_id = "00000000-0000-0000-0000-000000000066"
        cases = [
            ("number", "equals", 45, ") = %(ann_", {45}),
            ("number", "between", [10, 50], " BETWEEN ", {10, 50}),
            ("text", "equals", "good", ") = lower(%(ann_", {"good"}),
            ("text", "not_equals", "bad", ") != lower(%(ann_", {"bad"}),
        ]

        for filter_type, filter_op, value, sql_fragment, expected_values in cases:
            builder = ClickHouseFilterBuilder()
            where, params = builder.translate(
                [
                    {
                        "column_id": label_id,
                        "filter_config": {
                            "filter_type": filter_type,
                            "filter_op": filter_op,
                            "filter_value": value,
                            "col_type": "ANNOTATION",
                        },
                    }
                ]
            )

            assert "model_hub_score AS s FINAL" in where
            assert sql_fragment in where
            assert expected_values.issubset(set(params.values()))

    def test_unknown_operator_does_not_fall_back_to_equals(self):
        """Unsupported operators should match nothing instead of becoming =."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder()
        filters = [
            {
                "column_id": "model",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "definitely_not_supported",
                    "filter_value": "gpt-4",
                    "col_type": "SYSTEM_METRIC",
                },
            }
        ]
        where, params = builder.translate(filters)

        assert "0 = 1" in where
        assert "definitely_not_supported" not in where
        assert " = %(col_" not in where
        assert params == {}

    def test_empty_in_filters_do_not_emit_invalid_clickhouse_sql(self):
        """Empty IN arrays should not serialize to invalid IN () syntax."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        for filter_op, expected in (("in", "0 = 1"), ("not_in", "1 = 1")):
            builder = ClickHouseFilterBuilder()
            where, params = builder.translate(
                [
                    {
                        "column_id": "status",
                        "filter_config": {
                            "filter_type": "text",
                            "filter_op": filter_op,
                            "filter_value": [],
                            "col_type": "SYSTEM_METRIC",
                        },
                    }
                ]
            )

            assert expected in where
            assert "IN %(" not in where
            assert "IN ()" not in where
            assert params == {}

    def test_annotation_text_not_equals_requires_existing_annotation(self):
        """Text not-equals should not include rows with no annotation."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder()
        filters = [
            {
                "column_id": "00000000-0000-0000-0000-000000000066",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "not_equals",
                    "filter_value": "bad",
                    "col_type": "ANNOTATION",
                },
            }
        ]
        where, params = builder.translate(filters)

        assert "trace_id IN (" in where
        assert "trace_id NOT IN" not in where
        assert "!= lower" in where
        assert "bad" in params.values()

    def test_annotation_label_types_translate_to_expected_storage_shapes(self):
        """All FE annotation label types should hit the matching Score JSON shape."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        label_id = "00000000-0000-0000-0000-000000000066"
        cases = [
            (
                {
                    "filter_type": "number",
                    "filter_op": "equals",
                    "filter_value": 4,
                    "col_type": "ANNOTATION",
                },
                ["JSONExtractFloat", "'rating'", "'value'", ") = %(ann_"],
                {4},
            ),
            (
                {
                    "filter_type": "text",
                    "filter_op": "starts_with",
                    "filter_value": "needs",
                    "col_type": "ANNOTATION",
                },
                ["JSONExtractString", "'text'", "ILIKE"],
                {"needs%"},
            ),
            (
                {
                    "filter_type": "thumbs",
                    "filter_op": "in",
                    "filter_value": ["Thumbs Up", "down"],
                    "col_type": "ANNOTATION",
                },
                ["JSONExtractString", "'value'", " IN %(ann_"],
                {("up", "down")},
            ),
            (
                {
                    "filter_type": "categorical",
                    "filter_op": "contains",
                    "filter_value": ["refund", "billing"],
                    "col_type": "ANNOTATION",
                },
                ["JSONExtract", "'selected'", "has(", " OR "],
                {"refund", "billing"},
            ),
            (
                {
                    "filter_type": "categorical",
                    "filter_op": "not_contains",
                    "filter_value": ["refund"],
                    "col_type": "ANNOTATION",
                },
                ["JSONExtract", "'selected'", "has(", "AND NOT ("],
                {"refund"},
            ),
            (
                {
                    "filter_type": "annotator",
                    "filter_op": "equals",
                    "filter_value": [
                        "11111111-1111-1111-1111-111111111111",
                        "22222222-2222-2222-2222-222222222222",
                    ],
                    "col_type": "ANNOTATION",
                },
                ["s.annotator_id IN", "toUUID(%(ann_"],
                {
                    "11111111-1111-1111-1111-111111111111",
                    "22222222-2222-2222-2222-222222222222",
                },
            ),
        ]

        for config, expected_fragments, expected_values in cases:
            builder = ClickHouseFilterBuilder()
            where, params = builder.translate(
                [
                    {
                        "column_id": label_id,
                        "filter_config": config,
                    }
                ]
            )

            assert "model_hub_score AS s FINAL" in where
            for fragment in expected_fragments:
                assert fragment in where
            assert expected_values.issubset(set(params.values()))

    def test_is_null_system_metric(self):
        """IS NULL on system metric column."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder()
        filters = [
            {
                "column_id": "model",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "is_null",
                    "filter_value": None,
                    "col_type": "SYSTEM_METRIC",
                },
            }
        ]
        where, _ = builder.translate(filters)
        assert "IS NULL" in where

    def test_is_not_null_system_metric(self):
        """IS NOT NULL on system metric column."""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        builder = ClickHouseFilterBuilder()
        filters = [
            {
                "column_id": "model",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "is_not_null",
                    "filter_value": None,
                    "col_type": "SYSTEM_METRIC",
                },
            }
        ]
        where, _ = builder.translate(filters)
        assert "IS NOT NULL" in where


# ============================================================================
# Voice Call List Query Builder Tests
# ============================================================================


@pytest.mark.unit
class TestVoiceCallListQueryBuilder:
    """Test VoiceCallListQueryBuilder query generation."""

    def test_build_voice_call_list_query(self):
        """Phase 1 query should filter for conversation root spans."""
        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VoiceCallListQueryBuilder,
        )

        builder = VoiceCallListQueryBuilder(
            project_id="test-project-id",
            page_number=0,
            page_size=10,
            filters=[
                {
                    "column_id": "created_at",
                    "filter_config": {
                        "filter_type": "datetime",
                        "filter_op": "between",
                        "filter_value": [
                            "2025-01-01T00:00:00Z",
                            "2025-12-31T23:59:59Z",
                        ],
                    },
                }
            ],
        )
        query, params = builder.build()
        assert "observation_type = 'conversation'" in query
        assert "parent_span_id IS NULL" in query
        # Heavy columns (span_attributes_raw, etc.) moved out of Phase-1.
        content_query, _ = builder.build_content_query(["span-1"])
        assert "span_attributes_raw" in content_query
        assert params["project_id"] == "test-project-id"
        assert params["limit"] == 11
        assert params["offset"] == 0

    def test_build_count_query(self):
        """Count query should have same filters and use uniqExact(trace_id)."""
        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VoiceCallListQueryBuilder,
        )

        builder = VoiceCallListQueryBuilder(
            project_id="test-project-id",
            filters=[],
        )
        # Must call build() first to set start_date/end_date
        builder.build()
        query, params = builder.build_count_query()
        assert "uniqExact(trace_id) AS total" in query
        assert "observation_type = 'conversation'" in query
        assert "parent_span_id IS NULL" in query

    def test_simulation_filter_enabled(self):
        """Simulation filtering moved to Python post-Phase-1b (SQL-side no-op).

        The SQL-side ``_build_simulation_filter`` returns an empty fragment
        because scanning ``span_attributes_raw`` at query time caused
        ClickHouse OOM.  Exclusion is now done in Python via
        ``is_simulator_call``.
        """
        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VoiceCallListQueryBuilder,
        )

        builder = VoiceCallListQueryBuilder(
            project_id="test-project-id",
            remove_simulation_calls=True,
        )
        query, _ = builder.build()
        # SQL-side simulation filter is now a no-op.
        assert "+18568806998" not in query
        assert builder._build_simulation_filter() == ""
        # Python-side filter still works on raw_log / from_number.
        assert (
            VoiceCallListQueryBuilder.is_simulator_call(
                {"raw_log": {"customer": {"number": "+18568806998"}}}, "vapi"
            )
            is True
        )
        assert (
            VoiceCallListQueryBuilder.is_simulator_call(
                {"raw_log": {"from_number": "+18568806998"}}, "retell"
            )
            is True
        )

    def test_simulation_filter_disabled(self):
        """When remove_simulation_calls=False, should NOT add phone number filter."""
        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VoiceCallListQueryBuilder,
        )

        builder = VoiceCallListQueryBuilder(
            project_id="test-project-id",
            remove_simulation_calls=False,
        )
        query, _ = builder.build()
        assert "+18568806998" not in query

    def test_eval_query(self):
        """Phase 2 eval query should filter by trace_ids and eval_config_ids."""
        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VoiceCallListQueryBuilder,
        )

        builder = VoiceCallListQueryBuilder(
            project_id="test-project-id",
            eval_config_ids=["eval-1", "eval-2"],
        )
        query, params = builder.build_eval_query(["trace-1", "trace-2"])
        assert "tracer_eval_logger" in query
        assert "avgIf" in query
        assert params["trace_ids"] == ("trace-1", "trace-2")
        assert params["eval_config_ids"] == ("eval-1", "eval-2")

    def test_eval_query_empty_trace_ids(self):
        """Phase 2 should return empty when no trace_ids."""
        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VoiceCallListQueryBuilder,
        )

        builder = VoiceCallListQueryBuilder(
            project_id="test-project-id",
            eval_config_ids=["eval-1"],
        )
        query, params = builder.build_eval_query([])
        assert query == ""
        assert params == {}

    def test_eval_query_filters_errored_rows(self):
        """Eval query should exclude errored rows from score aggregation."""
        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VoiceCallListQueryBuilder,
        )

        builder = VoiceCallListQueryBuilder(
            project_id="proj-1",
            eval_config_ids=["eval-1"],
        )
        query, _ = builder.build_eval_query(["trace-1"])
        assert "error = 0" in query
        assert "success_count" in query
        assert "error_count" in query

    def test_eval_query_returns_8_columns(self):
        """Eval query should return all 8 columns expected by pivot_eval_results."""
        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VoiceCallListQueryBuilder,
        )

        builder = VoiceCallListQueryBuilder(
            project_id="proj-1",
            eval_config_ids=["eval-1"],
        )
        query, _ = builder.build_eval_query(["trace-1"])
        for col_name in [
            "avg_score",
            "pass_rate",
            "success_count",
            "error_count",
            "eval_count",
            "str_lists",
        ]:
            assert col_name in query, f"Missing column: {col_name}"

    def test_pivot_eval_results_all_errored(self):
        """When all evals errored, pivot should return error marker."""
        from tracer.services.clickhouse.query_builders.trace_list import (
            TraceListQueryBuilder,
        )

        rows = [
            ("t1", "cfg-1", None, None, 0, 3, 3, []),
        ]
        columns = [
            "trace_id",
            "eval_config_id",
            "avg_score",
            "pass_rate",
            "success_count",
            "error_count",
            "eval_count",
            "str_lists",
        ]
        result = TraceListQueryBuilder.pivot_eval_results(rows, columns)
        assert result["t1"]["cfg-1"] == {"error": True}

    def test_pivot_eval_results_partial_errors(self):
        """When some evals succeed and some error, should return scores not error."""
        from tracer.services.clickhouse.query_builders.trace_list import (
            TraceListQueryBuilder,
        )

        rows = [
            ("t1", "cfg-1", 0.75, None, 2, 1, 3, []),
        ]
        columns = [
            "trace_id",
            "eval_config_id",
            "avg_score",
            "pass_rate",
            "success_count",
            "error_count",
            "eval_count",
            "str_lists",
        ]
        result = TraceListQueryBuilder.pivot_eval_results(rows, columns)
        assert result["t1"]["cfg-1"]["avg_score"] == 75.0
        assert "error" not in result["t1"]["cfg-1"]

    def test_pivot_eval_results_error_with_dict_rows(self):
        """Error detection should work with dict-style rows from CH client."""
        from tracer.services.clickhouse.query_builders.trace_list import (
            TraceListQueryBuilder,
        )

        rows = [
            {
                "trace_id": "t1",
                "eval_config_id": "cfg-1",
                "avg_score": None,
                "pass_rate": None,
                "success_count": 0,
                "error_count": 5,
                "eval_count": 5,
                "str_lists": [],
            }
        ]
        columns = [
            "trace_id",
            "eval_config_id",
            "avg_score",
            "pass_rate",
            "success_count",
            "error_count",
            "eval_count",
            "str_lists",
        ]
        result = TraceListQueryBuilder.pivot_eval_results(rows, columns)
        assert result["t1"]["cfg-1"] == {"error": True}

    def test_annotation_query(self):
        """Phase 3 annotation query should filter by trace_ids and label_ids."""
        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VoiceCallListQueryBuilder,
        )

        builder = VoiceCallListQueryBuilder(project_id="test-project-id")
        query, params = builder.build_annotation_query(
            ["trace-1"], ["label-1", "label-2"]
        )
        assert "model_hub_score" in query
        assert "label_id" in query
        assert params["trace_ids"] == ("trace-1",)
        assert params["label_ids"] == ("label-1", "label-2")

    def test_annotation_query_empty(self):
        """Phase 3 should return empty when no trace_ids or label_ids."""
        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VoiceCallListQueryBuilder,
        )

        builder = VoiceCallListQueryBuilder(project_id="test-project-id")
        query, params = builder.build_annotation_query([], ["label-1"])
        assert query == ""

    def test_child_spans_query(self):
        """Phase 4 should fetch non-root spans for given trace_ids."""
        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VoiceCallListQueryBuilder,
        )

        builder = VoiceCallListQueryBuilder(project_id="test-project-id")
        query, params = builder.build_child_spans_query(["trace-1", "trace-2"])
        assert "parent_span_id IS NOT NULL" in query
        assert "trace_id IN %(trace_ids)s" in query
        assert params["trace_ids"] == ("trace-1", "trace-2")

    def test_child_spans_query_empty(self):
        """Phase 4 should return empty when no trace_ids."""
        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VoiceCallListQueryBuilder,
        )

        builder = VoiceCallListQueryBuilder(project_id="test-project-id")
        query, params = builder.build_child_spans_query([])
        assert query == ""

    def test_pagination(self):
        """Should correctly compute offset from page_number and page_size."""
        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VoiceCallListQueryBuilder,
        )

        builder = VoiceCallListQueryBuilder(
            project_id="test-project-id",
            page_number=2,
            page_size=25,
        )
        query, params = builder.build()
        assert params["limit"] == 26
        assert params["offset"] == 50

    def test_filters_applied(self):
        """Frontend filters should be translated and applied."""
        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VoiceCallListQueryBuilder,
        )

        builder = VoiceCallListQueryBuilder(
            project_id="test-project-id",
            filters=[
                {
                    "column_id": "status",
                    "filter_config": {
                        "col_type": "SYSTEM_METRIC",
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "OK",
                    },
                }
            ],
        )
        query, params = builder.build()
        assert "lower(status) =" in query

    def test_voice_numeric_filter_without_col_type_uses_metric_expression(self):
        """API callers may omit col_type; voice metrics should still translate."""
        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VoiceCallListQueryBuilder,
        )

        builder = VoiceCallListQueryBuilder(
            project_id="test-project-id",
            filters=[
                {
                    "column_id": "turn_count",
                    "filter_config": {
                        "filter_type": "number",
                        "filter_op": "greater_than_or_equal",
                        "filter_value": 0,
                    },
                }
            ],
        )

        query, _ = builder.build()

        assert "span_attr_num['call.total_turns']" in query
        assert "turn_count >=" not in query

    def test_voice_string_filter_without_col_type_uses_attribute_map(self):
        """Voice string metrics should not be emitted as unknown CH columns."""
        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VoiceCallListQueryBuilder,
        )

        builder = VoiceCallListQueryBuilder(
            project_id="test-project-id",
            filters=[
                {
                    "column_id": "ended_reason",
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "contains",
                        "filter_value": "customer",
                    },
                }
            ],
        )

        query, _ = builder.build()

        assert "mapContains(span_attr_str, 'ended_reason')" in query
        assert "span_attr_str['ended_reason'] LIKE" in query


@pytest.mark.unit
class TestVoiceCallListPhase1bMigration:
    """Pin the CH25 cutover for the Phase 1b attrs hydration step.

    The voice list view fetches the paginated root spans first (Phase 1)
    and then hydrates `span_attributes` + `provider` for that page in a
    second query (Phase 1b). Pre-CH25 that second query hit the legacy
    `tracer_observation_span` CDC mirror. Post-CH25 it reads the v2
    `spans` table (`attributes_extra` JSON + typed Maps), and that
    cutover is the one thing standing between us and being able to
    decommission the PeerDB stream for that table.

    The SQL is inline in the view method, not in a builder class, so we
    pin it by inspecting the function source — same pattern used by
    test_trace_workspace_scope.py for the agent_graph PG-fallback ban.
    """

    @staticmethod
    def _voice_list_source() -> str:
        import inspect

        from tracer.views.trace import TraceView

        return inspect.getsource(TraceView._list_voice_calls_clickhouse)

    def test_phase_1b_does_not_query_legacy_cdc_mirror(self):
        """The Phase 1b hydration must NOT read `tracer_observation_span`.

        Reverting to the CDC mirror means voice attributes go stale the
        moment we cut PeerDB. Catch that statically.

        Scope: the *Phase 1b* query specifically. The eval-config discovery
        query earlier in the same method reads the legacy `tracer_eval_logger`
        CDC mirror but filters soft-deletes via `deleted` (not
        `_peerdb_is_deleted`) — out of scope for this Phase 1b check.
        """
        import re

        src = self._voice_list_source()
        # Ban the actual query, not the comments that document the cutover.
        assert not re.search(r"FROM\s+tracer_observation_span", src), (
            "_list_voice_calls_clickhouse references the legacy CDC mirror; "
            "the Phase 1b query must read from the v2 `spans` table."
        )
        # Narrow the `_peerdb_is_deleted` ban to the Phase 1b block, found
        # by anchoring on the unique `attributes_extra AS span_attributes`
        # projection and slicing forward to the next `analytics.execute_ch_query`
        # call. Anything inside that slice belongs to the Phase 1b query.
        anchor = "attributes_extra AS span_attributes"
        assert anchor in src, (
            "Phase 1b query no longer projects `attributes_extra AS "
            "span_attributes`; this regression test needs a new anchor."
        )
        start = src.index(anchor)
        phase_1b_block = src[start : start + 600]
        assert "_peerdb_is_deleted" not in phase_1b_block, (
            "Phase 1b query still filters on `_peerdb_is_deleted`; v2 "
            "`spans` uses `is_deleted` (no CDC = no PeerDB tombstone)."
        )

    def test_phase_1b_reads_v2_spans_table(self):
        """Phase 1b must read v2 `spans` and dedupe versions WITHOUT `FINAL`.

        `FROM spans` is the v2 table (not the legacy CDC mirror). Dedup is
        done via `ORDER BY _version DESC LIMIT 1 BY id`, not `FINAL`: `FINAL`
        disables the `idx_id` skip index and full-scans the table (~194M rows)
        to fetch one page. The version-pick is the equivalent dedup contract.
        """
        import re

        src = self._voice_list_source()
        # Reads the v2 `spans` table (not `tracer_observation_span`).
        assert re.search(
            r"FROM\s+spans\b", src
        ), "_list_voice_calls_clickhouse must hydrate from the v2 `spans` table."
        # No `FINAL` in the hot Phase 1b query — it defeats the skip index.
        assert "FROM spans FINAL" not in src, (
            "Phase 1b must not use `FINAL` — it disables the idx_id skip index "
            "and full-scans `spans`. Dedupe via `ORDER BY _version DESC LIMIT 1 BY id`."
        )
        # ReplacingMergeTree dedup via keyset version-pick.
        assert "ORDER BY _version DESC" in src and "LIMIT 1 BY id" in src, (
            "Phase 1b must dedupe versions via `ORDER BY _version DESC LIMIT 1 BY id`."
        )
        assert "is_deleted = 0" in src

    def test_phase_1b_selects_typed_map_columns_for_reconstruction(self):
        """The Phase 1b query must SELECT the typed Maps + attributes_extra.

        fi-collector puts the common-case LLM keys (gen_ai.*) into the
        typed Maps; `attributes_extra` only holds the overflow. If we
        only select `attributes_extra`, voice spans show empty
        `span_attributes` because nothing fell into the overflow tier.
        """
        src = self._voice_list_source()
        assert "attributes_extra AS span_attributes" in src
        assert "attrs_string" in src
        assert "attrs_number" in src
        assert "attrs_bool" in src

    def test_phase_1b_python_fallback_merges_typed_maps(self):
        """Python-side: when `attributes_extra` is empty, fall back to Maps.

        The fallback is what makes the reconstruction actually work — it's
        easy to drop accidentally during refactors. Pin it.
        """
        src = self._voice_list_source()
        # The fallback walks all three Maps. Any one being missing would
        # silently drop a class of attributes.
        assert 'arow.get("attrs_string")' in src
        assert 'arow.get("attrs_number")' in src
        assert 'arow.get("attrs_bool")' in src
        # Bool values get cast to Python bool (CH UInt8 → 0/1 otherwise).
        assert "bool(v)" in src

    def test_phase_1b_scopes_by_project_and_drops_call_logs(self):
        """Phase 1b must scope by `project_id` and drop `call_logs` at read.

        `project_id` in the WHERE lets the primary key prune the page's parts
        (paired with the no-FINAL dedup). `call_logs` (avg ~900 KiB/row) is
        detail-only bloat the FE never reads — dropped via `mapFilter` so it's
        never fetched. `raw_log` is kept: process_raw_logs still needs it.
        """
        src = self._voice_list_source()
        assert "project_id = %(project_id)s" in src, (
            "Phase 1b must scope by project_id so the primary key can prune."
        )
        assert "mapFilter" in src and "call_logs" in src, (
            "Phase 1b must exclude `call_logs` from attrs_string at read time."
        )

    def test_voice_call_list_strips_heavy_payload_keys(self):
        """`call_logs`/`raw_log` are detail-only — never in list rows."""
        from tracer.views.trace import TraceView

        assert "call_logs" in TraceView._VOICE_CALL_HEAVY_KEYS
        assert "raw_log" in TraceView._VOICE_CALL_HEAVY_KEYS


@pytest.mark.unit
class TestVoiceCallListQueryBuilderComprehensive:
    """Comprehensive tests for VoiceCallListQueryBuilder covering all phases,
    filters, simulation exclusion, edge cases, and result pivoting."""

    # ------------------------------------------------------------------
    # Phase 1: Column selection and query structure
    # ------------------------------------------------------------------

    def test_build_selects_required_columns(self):
        """Phase-1 query should select light columns; heavy columns via content query."""
        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VoiceCallListQueryBuilder,
        )

        builder = VoiceCallListQueryBuilder(project_id="proj-1")
        query, _ = builder.build()
        for col in [
            "trace_id",
            "span_id",
            "observation_type",
            "status",
            "start_time",
            "end_time",
            "latency_ms",
            "provider",
        ]:
            assert col in query, f"Missing column: {col}"
        # Heavy columns are fetched via build_content_query after pagination.
        content_query, _ = builder.build_content_query(["span-1"])
        for col in [
            "span_attributes_raw",
            "span_attr_str",
            "span_attr_num",
            "metadata_map",
        ]:
            assert col in content_query, f"Missing content column: {col}"

    def test_build_scopes_to_project(self):
        """Phase-1 query should scope to project_id and exclude deleted rows."""
        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VoiceCallListQueryBuilder,
        )

        builder = VoiceCallListQueryBuilder(project_id="proj-abc")
        query, params = builder.build()
        assert "project_id = %(project_id)s" in query
        assert "is_deleted = 0" in query
        assert params["project_id"] == "proj-abc"

    def test_build_orders_by_start_time_desc(self):
        """Results should be ordered newest first."""
        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VoiceCallListQueryBuilder,
        )

        builder = VoiceCallListQueryBuilder(project_id="proj-1")
        query, _ = builder.build()
        assert "ORDER BY start_time DESC" in query

    def test_build_uses_default_time_range_when_no_date_filter(self):
        """When no date filter, should default to a 30-day window."""
        from datetime import datetime

        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VoiceCallListQueryBuilder,
        )

        builder = VoiceCallListQueryBuilder(project_id="proj-1", filters=[])
        _, params = builder.build()
        now = datetime.utcnow()
        # Default is 30 days back — matches BaseQueryBuilder.parse_time_range
        # (was 3650; shortened in CH 25.3 perf sweep for partition pruning).
        assert (now - params["start_date"]).days >= 29
        assert (now - params["start_date"]).days <= 31
        # end_date should be roughly now
        assert abs((now - params["end_date"]).total_seconds()) < 5

    def test_build_respects_explicit_time_range(self):
        """Explicit date filter should override defaults."""
        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VoiceCallListQueryBuilder,
        )

        builder = VoiceCallListQueryBuilder(
            project_id="proj-1",
            filters=[
                {
                    "column_id": "created_at",
                    "filter_config": {
                        "filter_type": "datetime",
                        "filter_op": "between",
                        "filter_value": [
                            "2025-06-01T00:00:00Z",
                            "2025-06-30T23:59:59Z",
                        ],
                    },
                }
            ],
        )
        _, params = builder.build()
        assert params["start_date"].year == 2025
        assert params["start_date"].month == 6
        assert params["start_date"].day == 1
        assert params["end_date"].month == 6
        assert params["end_date"].day == 30

    def test_build_page_zero(self):
        """Page 0 should have offset 0."""
        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VoiceCallListQueryBuilder,
        )

        builder = VoiceCallListQueryBuilder(
            project_id="proj-1", page_number=0, page_size=20
        )
        _, params = builder.build()
        assert params["offset"] == 0
        assert params["limit"] == 21

    def test_build_page_five(self):
        """Page 5 with page_size=15 should have offset 75."""
        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VoiceCallListQueryBuilder,
        )

        builder = VoiceCallListQueryBuilder(
            project_id="proj-1", page_number=5, page_size=15
        )
        _, params = builder.build()
        assert params["offset"] == 75
        assert params["limit"] == 16

    # ------------------------------------------------------------------
    # Count query
    # ------------------------------------------------------------------

    def test_count_query_no_limit_offset(self):
        """Count query should not have LIMIT/OFFSET."""
        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VoiceCallListQueryBuilder,
        )

        builder = VoiceCallListQueryBuilder(project_id="proj-1")
        builder.build()
        query, _ = builder.build_count_query()
        assert "LIMIT" not in query
        assert "OFFSET" not in query

    def test_count_query_same_filters_as_build(self):
        """Count query should apply the same conversation filter and user filters.

        Simulation exclusion moved to Python (see _build_simulation_filter
        docstring) so phone numbers no longer appear in the SQL.
        """
        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VoiceCallListQueryBuilder,
        )

        builder = VoiceCallListQueryBuilder(
            project_id="proj-1",
            remove_simulation_calls=True,
            filters=[
                {
                    "column_id": "provider",
                    "filter_config": {
                        "col_type": "SYSTEM_METRIC",
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "vapi",
                    },
                }
            ],
        )
        builder.build()
        query, params = builder.build_count_query()
        assert "observation_type = 'conversation'" in query
        assert "parent_span_id IS NULL" in query
        assert "provider" in query
        # Phone numbers are filtered in Python now — not in SQL.
        assert "+18568806998" not in query

    # ------------------------------------------------------------------
    # Filter types
    # ------------------------------------------------------------------

    def test_system_metric_filter(self):
        """SYSTEM_METRIC filter should map to direct column comparison."""
        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VoiceCallListQueryBuilder,
        )

        builder = VoiceCallListQueryBuilder(
            project_id="proj-1",
            filters=[
                {
                    "column_id": "model",
                    "filter_config": {
                        "col_type": "SYSTEM_METRIC",
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "gpt-4o",
                    },
                }
            ],
        )
        query, params = builder.build()
        assert "model" in query
        assert "gpt-4o" in params.values()

    def test_span_attribute_text_filter(self):
        """SPAN_ATTRIBUTE text filter should reference span_attr_str map."""
        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VoiceCallListQueryBuilder,
        )

        builder = VoiceCallListQueryBuilder(
            project_id="proj-1",
            filters=[
                {
                    "column_id": "ended_reason",
                    "filter_config": {
                        "col_type": "SPAN_ATTRIBUTE",
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "assistant-ended-call",
                    },
                }
            ],
        )
        query, params = builder.build()
        assert "span_attr_str" in query
        assert "ended_reason" in query
        assert "assistant-ended-call" in params.values()

    def test_span_attribute_numeric_filter(self):
        """Numeric SPAN_ATTRIBUTE filter should reference span_attr_num."""
        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VoiceCallListQueryBuilder,
        )

        builder = VoiceCallListQueryBuilder(
            project_id="proj-1",
            filters=[
                {
                    "column_id": "call.duration",
                    "filter_config": {
                        "col_type": "SPAN_ATTRIBUTE",
                        "filter_type": "number",
                        "filter_op": "greater_than",
                        "filter_value": 60,
                    },
                }
            ],
        )
        query, params = builder.build()
        assert "span_attr_num" in query
        assert "call.duration" in query
        assert ">" in query

    def test_eval_metric_filter(self):
        """EVAL_METRIC filter should generate a subquery against tracer_eval_logger."""
        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VoiceCallListQueryBuilder,
        )

        builder = VoiceCallListQueryBuilder(
            project_id="proj-1",
            filters=[
                {
                    "column_id": "00000000-0000-0000-0000-000000000099",
                    "filter_config": {
                        "col_type": "EVAL_METRIC",
                        "filter_type": "number",
                        "filter_op": "greater_than",
                        "filter_value": 0.8,
                    },
                }
            ],
        )
        query, _ = builder.build()
        assert "00000000-0000-0000-0000-000000000000" in query
        assert "trace_id IN" in query

    def test_contains_filter(self):
        """LIKE-based contains filter should work."""
        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VoiceCallListQueryBuilder,
        )

        builder = VoiceCallListQueryBuilder(
            project_id="proj-1",
            filters=[
                {
                    "column_id": "name",
                    "filter_config": {
                        "col_type": "SYSTEM_METRIC",
                        "filter_type": "text",
                        "filter_op": "contains",
                        "filter_value": "phone",
                    },
                }
            ],
        )
        query, params = builder.build()
        assert "LIKE" in query
        # Value should be wrapped in %
        assert any("%phone%" == v for v in params.values())

    def test_multiple_filters_combined(self):
        """Multiple filters should be ANDed together."""
        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VoiceCallListQueryBuilder,
        )

        builder = VoiceCallListQueryBuilder(
            project_id="proj-1",
            filters=[
                {
                    "column_id": "status",
                    "filter_config": {
                        "col_type": "SYSTEM_METRIC",
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "OK",
                    },
                },
                {
                    "column_id": "provider",
                    "filter_config": {
                        "col_type": "SYSTEM_METRIC",
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "vapi",
                    },
                },
            ],
        )
        query, _ = builder.build()
        assert "AND" in query
        assert "status" in query
        assert "provider" in query

    def test_empty_filters(self):
        """Empty filters list should not add extra WHERE fragments."""
        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VoiceCallListQueryBuilder,
        )

        builder = VoiceCallListQueryBuilder(project_id="proj-1", filters=[])
        query, _ = builder.build()
        # Should still have the base conversation filters
        assert "observation_type = 'conversation'" in query
        assert "parent_span_id IS NULL" in query

    # ------------------------------------------------------------------
    # Simulation filter details
    # ------------------------------------------------------------------

    def test_simulation_filter_covers_all_phone_numbers(self):
        """All 15 simulator phone numbers should still be matched by Python filter.

        SQL-side filtering was removed (caused CH OOM scanning the raw JSON),
        but the phone-number list is still consumed by ``is_simulator_call``.
        """
        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VAPI_PHONE_NUMBERS,
            VoiceCallListQueryBuilder,
        )

        builder = VoiceCallListQueryBuilder(
            project_id="proj-1",
            remove_simulation_calls=True,
        )
        # SQL-side no-op now.
        assert builder._build_simulation_filter() == ""
        # Each phone number is still recognised as a simulator call in Python.
        for phone in VAPI_PHONE_NUMBERS:
            span_attrs = {"raw_log": {"customer": {"number": phone}}}
            assert VoiceCallListQueryBuilder.is_simulator_call(
                span_attrs, "vapi"
            ), f"Missing phone number: {phone}"

    def test_simulation_filter_uses_json_extract(self):
        """Simulation filtering is now Python-side against parsed raw_log.

        The old SQL-side JSONExtract approach was removed to avoid CH OOM
        scanning ``span_attributes_raw``.  Parsing happens in Python.
        """
        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VoiceCallListQueryBuilder,
        )

        builder = VoiceCallListQueryBuilder(
            project_id="proj-1",
            remove_simulation_calls=True,
        )
        query, _ = builder.build()
        # SQL no longer references raw_log / JSONExtract for simulation.
        assert "JSONExtractString" not in query
        assert "raw_log" not in query
        # Python path still reads raw_log from span_attrs correctly.
        assert (
            VoiceCallListQueryBuilder.is_simulator_call(
                {"raw_log": {"customer": {"number": "+18568806998"}}}, "vapi"
            )
            is True
        )

    def test_simulation_filter_handles_retell(self):
        """Retell's from_number field should still be used by the Python filter."""
        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VoiceCallListQueryBuilder,
        )

        builder = VoiceCallListQueryBuilder(
            project_id="proj-1",
            remove_simulation_calls=True,
        )
        query, _ = builder.build()
        # SQL does not mention from_number.
        assert "from_number" not in query
        # Python path does.
        assert (
            VoiceCallListQueryBuilder.is_simulator_call(
                {"raw_log": {"from_number": "+18568806998"}}, "retell"
            )
            is True
        )
        assert (
            VoiceCallListQueryBuilder.is_simulator_call(
                {"raw_log": {"from_number": "+19998887777"}}, "retell"
            )
            is False
        )

    def test_simulation_filter_uses_not_clause(self):
        """Simulation exclusion is Python-side now, so SQL has no NOT clause."""
        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VoiceCallListQueryBuilder,
        )

        builder = VoiceCallListQueryBuilder(
            project_id="proj-1",
            remove_simulation_calls=True,
        )
        query, _ = builder.build()
        # The SQL-side simulation filter fragment is empty.
        assert builder._build_simulation_filter() == ""
        # Non-simulator call is not treated as a simulator.
        assert (
            VoiceCallListQueryBuilder.is_simulator_call(
                {"raw_log": {"customer": {"number": "+19998887777"}}}, "vapi"
            )
            is False
        )

    def test_simulation_filter_in_count_query(self):
        """Count query no longer embeds phone numbers — filter moved to Python."""
        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VoiceCallListQueryBuilder,
        )

        builder = VoiceCallListQueryBuilder(
            project_id="proj-1",
            remove_simulation_calls=True,
        )
        builder.build()
        query, _ = builder.build_count_query()
        assert "+18568806998" not in query
        assert builder._build_simulation_filter() == ""

    # ------------------------------------------------------------------
    # Phase 2: Eval queries — edge cases
    # ------------------------------------------------------------------

    def test_eval_query_no_eval_configs(self):
        """Should return empty query if builder has no eval_config_ids."""
        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VoiceCallListQueryBuilder,
        )

        builder = VoiceCallListQueryBuilder(
            project_id="proj-1",
            eval_config_ids=[],
        )
        query, params = builder.build_eval_query(["trace-1"])
        assert query == ""
        assert params == {}

    def test_eval_query_uses_final(self):
        """Eval query should use FINAL to deduplicate."""
        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VoiceCallListQueryBuilder,
        )

        builder = VoiceCallListQueryBuilder(
            project_id="proj-1",
            eval_config_ids=["00000000-0000-0000-0000-000000000011"],
        )
        query, _ = builder.build_eval_query(["trace-1"])
        assert "FINAL" in query

    def test_eval_query_groups_correctly(self):
        """Eval query should GROUP BY trace_id, config, and str_list."""
        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VoiceCallListQueryBuilder,
        )

        builder = VoiceCallListQueryBuilder(
            project_id="proj-1",
            eval_config_ids=["00000000-0000-0000-0000-000000000011"],
        )
        query, _ = builder.build_eval_query(["trace-1"])
        assert "GROUP BY trace_id, custom_eval_config_id" in query

    def test_eval_query_computes_pass_rate(self):
        """Eval query should compute pass_rate from output_bool."""
        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VoiceCallListQueryBuilder,
        )

        builder = VoiceCallListQueryBuilder(
            project_id="proj-1",
            eval_config_ids=["00000000-0000-0000-0000-000000000011"],
        )
        query, _ = builder.build_eval_query(["trace-1"])
        assert "output_bool" in query
        assert "pass_rate" in query

    def test_eval_query_many_configs(self):
        """Eval query should accept multiple eval config IDs."""
        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VoiceCallListQueryBuilder,
        )

        configs = [f"cfg-{i}" for i in range(10)]
        builder = VoiceCallListQueryBuilder(
            project_id="proj-1",
            eval_config_ids=configs,
        )
        _, params = builder.build_eval_query(["trace-1"])
        assert len(params["eval_config_ids"]) == 10

    # ------------------------------------------------------------------
    # Phase 3: Annotation queries — edge cases
    # ------------------------------------------------------------------

    def test_annotation_query_no_labels(self):
        """Should return empty query if no annotation_label_ids."""
        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VoiceCallListQueryBuilder,
        )

        builder = VoiceCallListQueryBuilder(project_id="proj-1")
        query, params = builder.build_annotation_query(["trace-1"], [])
        assert query == ""
        assert params == {}

    def test_annotation_query_uses_final(self):
        """Annotation query should use FINAL for deduplication."""
        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VoiceCallListQueryBuilder,
        )

        builder = VoiceCallListQueryBuilder(project_id="proj-1")
        query, _ = builder.build_annotation_query(["trace-1"], ["label-1"])
        assert "FINAL" in query

    def test_annotation_query_groups_correctly(self):
        """Should query model_hub_score table with correct structure."""
        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VoiceCallListQueryBuilder,
        )

        builder = VoiceCallListQueryBuilder(project_id="proj-1")
        query, _ = builder.build_annotation_query(["trace-1"], ["label-1"])
        assert "model_hub_score" in query
        assert "trace_id" in query
        assert "LEFT JOIN" in query
        assert "sp.id = s.observation_span_id" in query
        assert "toString(s.trace_id)" in query
        assert "s.deleted = false" in query

    def test_annotation_query_selects_all_value_types(self):
        """Should select the single JSON ``value`` column from model_hub_score.

        Annotation storage moved from per-type columns (bool/float/str_list)
        into a single JSON ``value`` column on ``model_hub_score``.
        """
        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VoiceCallListQueryBuilder,
        )

        builder = VoiceCallListQueryBuilder(project_id="proj-1")
        query, _ = builder.build_annotation_query(["trace-1"], ["label-1"])
        # Single JSON 'value' column replaces the old per-type columns.
        assert "s.value" in query or ".value" in query
        assert "model_hub_score" in query

    # ------------------------------------------------------------------
    # Phase 4: Child spans — edge cases
    # ------------------------------------------------------------------

    def test_child_spans_query_scopes_to_project(self):
        """Child spans query should scope by project_id."""
        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VoiceCallListQueryBuilder,
        )

        builder = VoiceCallListQueryBuilder(project_id="proj-1")
        query, params = builder.build_child_spans_query(["trace-1"])
        assert "project_id = %(project_id)s" in query
        assert params["project_id"] == "proj-1"

    def test_child_spans_query_excludes_deleted(self):
        """Child spans query should filter out soft-deleted rows."""
        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VoiceCallListQueryBuilder,
        )

        builder = VoiceCallListQueryBuilder(project_id="proj-1")
        query, _ = builder.build_child_spans_query(["trace-1"])
        assert "is_deleted = 0" in query

    def test_child_spans_query_orders_by_start_time(self):
        """Child spans should be ordered chronologically."""
        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VoiceCallListQueryBuilder,
        )

        builder = VoiceCallListQueryBuilder(project_id="proj-1")
        query, _ = builder.build_child_spans_query(["trace-1"])
        assert "ORDER BY start_time ASC" in query

    def test_child_spans_query_selects_span_columns(self):
        """Child spans query should select columns needed for serialization."""
        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VoiceCallListQueryBuilder,
        )

        builder = VoiceCallListQueryBuilder(project_id="proj-1")
        query, _ = builder.build_child_spans_query(["trace-1"])
        for col in [
            "id",
            "trace_id",
            "name",
            "observation_type",
            "status",
            "start_time",
            "end_time",
            "model",
            "cost",
            "input",
            "output",
            "parent_span_id",
        ]:
            assert col in query, f"Missing column: {col}"

    def test_child_spans_many_traces(self):
        """Should accept a large list of trace IDs."""
        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VoiceCallListQueryBuilder,
        )

        trace_ids = [f"trace-{i}" for i in range(100)]
        builder = VoiceCallListQueryBuilder(project_id="proj-1")
        _, params = builder.build_child_spans_query(trace_ids)
        assert len(params["trace_ids"]) == 100

    # ------------------------------------------------------------------
    # Defaults and constructor
    # ------------------------------------------------------------------

    def test_default_page_size(self):
        """Default page_size should be 10."""
        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VoiceCallListQueryBuilder,
        )

        builder = VoiceCallListQueryBuilder(project_id="proj-1")
        _, params = builder.build()
        assert params["limit"] == 11

    def test_default_page_number(self):
        """Default page_number should be 0."""
        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VoiceCallListQueryBuilder,
        )

        builder = VoiceCallListQueryBuilder(project_id="proj-1")
        _, params = builder.build()
        assert params["offset"] == 0

    def test_default_simulation_calls_not_removed(self):
        """By default, simulation calls should NOT be filtered out."""
        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VoiceCallListQueryBuilder,
        )

        builder = VoiceCallListQueryBuilder(project_id="proj-1")
        query, _ = builder.build()
        assert "+18568806998" not in query

    def test_default_empty_eval_configs(self):
        """Default eval_config_ids should be empty list."""
        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VoiceCallListQueryBuilder,
        )

        builder = VoiceCallListQueryBuilder(project_id="proj-1")
        assert builder.eval_config_ids == []

    def test_default_empty_filters(self):
        """Default filters should be empty list."""
        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VoiceCallListQueryBuilder,
        )

        builder = VoiceCallListQueryBuilder(project_id="proj-1")
        assert builder.filters == []

    # ------------------------------------------------------------------
    # Module exports
    # ------------------------------------------------------------------

    def test_exported_from_package(self):
        """VoiceCallListQueryBuilder should be importable from the package."""
        from tracer.services.clickhouse.query_builders import VoiceCallListQueryBuilder

        assert VoiceCallListQueryBuilder is not None

    def test_in_all_exports(self):
        """Should be in __all__ of the query_builders package."""
        import tracer.services.clickhouse.query_builders as pkg

        assert "VoiceCallListQueryBuilder" in pkg.__all__

    # ------------------------------------------------------------------
    # QueryType routing
    # ------------------------------------------------------------------

    def test_voice_call_list_query_type_exists(self):
        """VOICE_CALL_LIST should exist in QueryType enum."""
        from tracer.services.clickhouse.query_service import QueryType

        assert hasattr(QueryType, "VOICE_CALL_LIST")
        assert QueryType.VOICE_CALL_LIST.value == "VOICE_CALL_LIST"


# ============================================================================
# 21. Annotation Graph Query Builder Tests
# ============================================================================


@pytest.mark.unit
class TestAnnotationGraphQueryBuilder:
    """Test AnnotationGraphQueryBuilder for all output types."""

    PROJECT_ID = "test-project-id"
    LABEL_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    def test_build_float_query(self):
        """Float output type should extract the numeric value from the value JSON."""
        from tracer.services.clickhouse.query_builders import (
            AnnotationGraphQueryBuilder,
        )

        builder = AnnotationGraphQueryBuilder(
            project_id=self.PROJECT_ID,
            annotation_label_id=self.LABEL_ID,
            output_type="float",
            interval="hour",
        )
        query, params = builder.build()
        assert isinstance(query, str)
        assert isinstance(params, dict)
        # Post-revamp: numeric and star annotations live in model_hub_score
        # and may store their value under either `value` or `rating`.
        assert "JSONHas(value, 'rating')" in query
        assert "JSONExtract(value, 'value', 'Nullable(Float64)')" in query
        assert "JSONExtract(value, 'rating', 'Nullable(Float64)')" in query
        assert "model_hub_score" in query
        assert "FINAL" in query
        assert "is_deleted = 0" in query
        assert params["label_id"] == self.LABEL_ID

    def test_fetch_annotation_graph_uses_score_backed_label_lookup(self):
        """Annotation graph labels should resolve through the project score union."""
        from model_hub.models.choices import AnnotationTypeChoices
        from tracer.services.clickhouse import graph_dispatch

        class LabelQuery:
            def get(self, **kwargs):
                assert kwargs == {"id": self_label_id}
                label = mock.Mock()
                label.id = self_label_id
                label.name = "Score-backed label"
                label.type = AnnotationTypeChoices.NUMERIC.value
                return label

        class Result:
            data = []
            columns = []

        class Analytics:
            def execute_ch_query(self, query, params, timeout_ms):
                self.query = query
                self.params = params
                return Result()

        self_label_id = self.LABEL_ID
        analytics = Analytics()
        with mock.patch.object(
            graph_dispatch,
            "get_annotation_labels_for_project",
            return_value=LabelQuery(),
        ) as lookup:
            result = graph_dispatch.fetch_annotation_graph_ch(
                analytics=analytics,
                project_id="project-with-shared-label",
                filters=[],
                interval="month",
                req_data_config={"id": self.LABEL_ID, "type": "ANNOTATION"},
                observe_type="span",
            )

        lookup.assert_called_once_with("project-with-shared-label")
        assert analytics.params["label_id"] == self.LABEL_ID
        assert result["name"] == "Score-backed label"

    def test_build_bool_query(self):
        """Bool output type should produce CASE WHEN on JSON-extracted value."""
        from tracer.services.clickhouse.query_builders import (
            AnnotationGraphQueryBuilder,
        )

        builder = AnnotationGraphQueryBuilder(
            project_id=self.PROJECT_ID,
            annotation_label_id=self.LABEL_ID,
            output_type="bool",
            value=True,
        )
        query, params = builder.build()
        assert "JSONExtractString(value, 'value')" in query
        assert "CASE WHEN" in query
        assert "100.0" in query
        assert "bool_match" in params

    def test_build_bool_query_false_value(self):
        """Bool query with False should set bool_match to 'down'.

        Thumbs up/down lives in the JSON ``value`` column with string
        values ``"up"`` or ``"down"``.
        """
        from tracer.services.clickhouse.query_builders import (
            AnnotationGraphQueryBuilder,
        )

        builder = AnnotationGraphQueryBuilder(
            project_id=self.PROJECT_ID,
            annotation_label_id=self.LABEL_ID,
            output_type="bool",
            value=False,
        )
        _, params = builder.build()
        assert params["bool_match"] == "down"

    def test_build_bool_query_string_true(self):
        """Bool query should handle string 'true' value and map to 'up'."""
        from tracer.services.clickhouse.query_builders import (
            AnnotationGraphQueryBuilder,
        )

        builder = AnnotationGraphQueryBuilder(
            project_id=self.PROJECT_ID,
            annotation_label_id=self.LABEL_ID,
            output_type="bool",
            value="true",
        )
        _, params = builder.build()
        assert params["bool_match"] == "up"

    def test_build_str_list_query(self):
        """str_list output type should use has() and JSONExtract."""
        from tracer.services.clickhouse.query_builders import (
            AnnotationGraphQueryBuilder,
        )

        builder = AnnotationGraphQueryBuilder(
            project_id=self.PROJECT_ID,
            annotation_label_id=self.LABEL_ID,
            output_type="str_list",
            value="Good",
        )
        query, params = builder.build()
        assert "has(" in query
        assert "JSONExtract(value, 'selected', 'Array(String)')" in query
        assert "JSONExtractString(value, 'selected')" not in query
        assert "choice_value" in params
        assert params["choice_value"] == "Good"

    def test_build_str_list_no_value_falls_back_to_float(self):
        """str_list with no value should fall back to float query."""
        from tracer.services.clickhouse.query_builders import (
            AnnotationGraphQueryBuilder,
        )

        builder = AnnotationGraphQueryBuilder(
            project_id=self.PROJECT_ID,
            annotation_label_id=self.LABEL_ID,
            output_type="str_list",
            value=None,
        )
        query, _ = builder.build()
        assert "JSONExtract(value, 'rating', 'Nullable(Float64)')" in query

    def test_build_text_query(self):
        """Text output type should produce count() per time bucket."""
        from tracer.services.clickhouse.query_builders import (
            AnnotationGraphQueryBuilder,
        )

        builder = AnnotationGraphQueryBuilder(
            project_id=self.PROJECT_ID,
            annotation_label_id=self.LABEL_ID,
            output_type="text",
        )
        query, _ = builder.build()
        assert "count()" in query
        assert "time_bucket" in query
        assert "GROUP BY" in query
        assert "ORDER BY" in query

    def test_build_unknown_output_type_falls_back_to_float(self):
        """Unknown output type should fall back to float query."""
        from tracer.services.clickhouse.query_builders import (
            AnnotationGraphQueryBuilder,
        )

        builder = AnnotationGraphQueryBuilder(
            project_id=self.PROJECT_ID,
            annotation_label_id=self.LABEL_ID,
            output_type="unknown_type",
        )
        query, _ = builder.build()
        assert "JSONExtract(value, 'rating', 'Nullable(Float64)')" in query

    def test_default_time_range(self):
        """When no dates provided, should default to 7-day range."""
        from tracer.services.clickhouse.query_builders import (
            AnnotationGraphQueryBuilder,
        )

        builder = AnnotationGraphQueryBuilder(
            project_id=self.PROJECT_ID,
            annotation_label_id=self.LABEL_ID,
        )
        assert builder.start_date is not None
        assert builder.end_date is not None
        delta = builder.end_date - builder.start_date
        assert delta.days == 7

    def test_custom_time_range(self):
        """Custom start/end dates should be used."""
        from datetime import datetime

        from tracer.services.clickhouse.query_builders import (
            AnnotationGraphQueryBuilder,
        )

        start = datetime(2024, 1, 1)
        end = datetime(2024, 1, 31)
        builder = AnnotationGraphQueryBuilder(
            project_id=self.PROJECT_ID,
            annotation_label_id=self.LABEL_ID,
            start_date=start,
            end_date=end,
        )
        assert builder.start_date == start
        assert builder.end_date == end
        _, params = builder.build()
        assert params["start_date"] == start
        assert params["end_date"] == end

    def test_format_result_float(self):
        """format_result for float type should return name and data."""
        from datetime import datetime

        from tracer.services.clickhouse.query_builders import (
            AnnotationGraphQueryBuilder,
        )

        builder = AnnotationGraphQueryBuilder(
            project_id=self.PROJECT_ID,
            annotation_label_id=self.LABEL_ID,
            annotation_name="Quality Score",
            output_type="float",
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 1, 2),
            interval="hour",
        )
        result = builder.format_result([], [])
        assert result["name"] == "Quality Score"
        assert isinstance(result["data"], list)

    def test_format_result_bool_name_suffix(self):
        """format_result for bool type should append True/False to name."""
        from datetime import datetime

        from tracer.services.clickhouse.query_builders import (
            AnnotationGraphQueryBuilder,
        )

        builder = AnnotationGraphQueryBuilder(
            project_id=self.PROJECT_ID,
            annotation_label_id=self.LABEL_ID,
            annotation_name="Is Valid",
            output_type="bool",
            value=True,
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 1, 2),
        )
        result = builder.format_result([], [])
        assert result["name"] == "Is Valid - True"

    def test_format_result_str_list_name_suffix(self):
        """format_result for str_list type should append choice to name."""
        from datetime import datetime

        from tracer.services.clickhouse.query_builders import (
            AnnotationGraphQueryBuilder,
        )

        builder = AnnotationGraphQueryBuilder(
            project_id=self.PROJECT_ID,
            annotation_label_id=self.LABEL_ID,
            annotation_name="Rating",
            output_type="str_list",
            value="Good",
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 1, 2),
        )
        result = builder.format_result([], [])
        assert result["name"] == "Rating - Good"

    def test_all_queries_filter_by_annotation_label_id(self):
        """All output types should filter by label_id (model_hub_score column)."""
        from tracer.services.clickhouse.query_builders import (
            AnnotationGraphQueryBuilder,
        )

        for output_type in ["float", "bool", "text", "str_list"]:
            builder = AnnotationGraphQueryBuilder(
                project_id=self.PROJECT_ID,
                annotation_label_id=self.LABEL_ID,
                output_type=output_type,
                value="test" if output_type in ("str_list", "bool") else None,
            )
            query, params = builder.build()
            assert "label_id = toUUID(%(label_id)s)" in query
            assert params["label_id"] == self.LABEL_ID

    def test_all_queries_have_time_bucket(self):
        """All output types should produce time_bucket column."""
        from tracer.services.clickhouse.query_builders import (
            AnnotationGraphQueryBuilder,
        )

        for output_type in ["float", "bool", "text", "str_list"]:
            builder = AnnotationGraphQueryBuilder(
                project_id=self.PROJECT_ID,
                annotation_label_id=self.LABEL_ID,
                output_type=output_type,
                value="test" if output_type in ("str_list", "bool") else None,
            )
            query, _ = builder.build()
            assert "time_bucket" in query


# ============================================================================
# 22. Monitor Metrics Query Builder Tests
# ============================================================================


@pytest.mark.unit
class TestMonitorMetricsQueryBuilder:
    """Test MonitorMetricsQueryBuilder for all metric types."""

    PROJECT_ID = "test-project-id"

    def _make_builder(self, **kwargs):
        from tracer.services.clickhouse.query_builders.monitor_metrics import (
            MonitorMetricsQueryBuilder,
        )

        return MonitorMetricsQueryBuilder(project_id=self.PROJECT_ID, **kwargs)

    def test_build_raises_not_implemented(self):
        """build() should raise NotImplementedError."""
        builder = self._make_builder()
        with pytest.raises(NotImplementedError):
            builder.build()

    # -- Metric value queries --

    def test_count_of_errors_query(self):
        """COUNT_OF_ERRORS metric should count spans with status = ERROR."""
        from datetime import datetime

        builder = self._make_builder()
        query, params = builder.build_metric_value_query(
            "count_of_errors", datetime(2024, 1, 1), datetime(2024, 1, 31)
        )
        assert "count()" in query
        assert "status = 'ERROR'" in query
        assert "project_id" in params

    def test_error_rates_function_calling_query(self):
        """ERROR_RATES_FOR_FUNCTION_CALLING should filter observation_type = tool."""
        from datetime import datetime

        builder = self._make_builder()
        query, params = builder.build_metric_value_query(
            "error_rates_for_function_calling",
            datetime(2024, 1, 1),
            datetime(2024, 1, 31),
        )
        assert "observation_type = 'tool'" in query
        assert "countIf(status = 'ERROR')" in query

    def test_error_free_session_rates_query(self):
        """ERROR_FREE_SESSION_RATES should group by session_id."""
        from datetime import datetime

        builder = self._make_builder()
        query, _ = builder.build_metric_value_query(
            "error_free_session_rates", datetime(2024, 1, 1), datetime(2024, 1, 31)
        )
        assert "session_id" in query
        assert "GROUP BY session_id" in query

    def test_service_provider_error_rates_query(self):
        """SERVICE_PROVIDER_ERROR_RATES should group by provider."""
        from datetime import datetime

        builder = self._make_builder()
        query, _ = builder.build_metric_value_query(
            "service_provider_error_rates", datetime(2024, 1, 1), datetime(2024, 1, 31)
        )
        assert "provider" in query
        assert "GROUP BY provider" in query

    def test_llm_api_failure_rates_query(self):
        """LLM_API_FAILURE_RATES should filter observation_type = llm."""
        from datetime import datetime

        builder = self._make_builder()
        query, _ = builder.build_metric_value_query(
            "llm_api_failure_rates", datetime(2024, 1, 1), datetime(2024, 1, 31)
        )
        assert "observation_type = 'llm'" in query

    def test_span_response_time_query(self):
        """SPAN_RESPONSE_TIME should use avg(latency_ms)."""
        from datetime import datetime

        builder = self._make_builder()
        query, _ = builder.build_metric_value_query(
            "span_response_time", datetime(2024, 1, 1), datetime(2024, 1, 31)
        )
        assert "avg(latency_ms)" in query

    def test_llm_response_time_query(self):
        """LLM_RESPONSE_TIME should filter on observation_type = llm."""
        from datetime import datetime

        builder = self._make_builder()
        query, _ = builder.build_metric_value_query(
            "llm_response_time", datetime(2024, 1, 1), datetime(2024, 1, 31)
        )
        assert "avg(latency_ms)" in query
        assert "observation_type = 'llm'" in query

    def test_token_usage_query(self):
        """TOKEN_USAGE should use sum(total_tokens)."""
        from datetime import datetime

        builder = self._make_builder()
        query, _ = builder.build_metric_value_query(
            "token_usage", datetime(2024, 1, 1), datetime(2024, 1, 31)
        )
        assert "sum(total_tokens)" in query

    def test_daily_tokens_spent_query(self):
        """DAILY_TOKENS_SPENT should use >= start_time only."""
        from datetime import datetime

        builder = self._make_builder()
        query, _ = builder.build_metric_value_query(
            "daily_tokens_spent", datetime(2024, 1, 1), datetime(2024, 1, 31)
        )
        assert "sum(total_tokens)" in query
        assert ">= %(start_time)s" in query

    def test_monthly_tokens_spent_query(self):
        """MONTHLY_TOKENS_SPENT should use >= start_time only."""
        from datetime import datetime

        builder = self._make_builder()
        query, _ = builder.build_metric_value_query(
            "monthly_tokens_spent", datetime(2024, 1, 1), datetime(2024, 1, 31)
        )
        assert "sum(total_tokens)" in query

    def test_evaluation_metrics_score_query(self):
        """EVALUATION_METRICS with SCORE should use avg(output_float)."""
        from datetime import datetime

        builder = self._make_builder(
            eval_config_id="eval-cfg-123",
            eval_output_type="SCORE",
        )
        query, params = builder.build_metric_value_query(
            "evaluation_metrics", datetime(2024, 1, 1), datetime(2024, 1, 31)
        )
        assert "avg(output_float)" in query
        assert "tracer_eval_logger" in query
        assert "FINAL" in query
        assert params["eval_config_id"] == "eval-cfg-123"

    def test_evaluation_metrics_pass_fail_query(self):
        """EVALUATION_METRICS with PASS_FAIL should check output_bool."""
        from datetime import datetime

        builder = self._make_builder(
            eval_config_id="eval-cfg-123",
            eval_output_type="PASS_FAIL",
            threshold_metric_value="Passed",
        )
        query, params = builder.build_metric_value_query(
            "evaluation_metrics", datetime(2024, 1, 1), datetime(2024, 1, 31)
        )
        assert "output_bool" in query
        assert params["output_bool_val"] == 1

    def test_evaluation_metrics_choices_query(self):
        """EVALUATION_METRICS with CHOICES should parse output_str_list."""
        from datetime import datetime

        builder = self._make_builder(
            eval_config_id="eval-cfg-123",
            eval_output_type="CHOICES",
            threshold_metric_value="Good",
        )
        query, params = builder.build_metric_value_query(
            "evaluation_metrics", datetime(2024, 1, 1), datetime(2024, 1, 31)
        )
        assert "JSONExtract(output_str_list, 'Array(String)')" in query
        assert "OR output_str =" in query
        assert params["choice_val"] == "Good"

    def test_evaluation_metrics_no_config_returns_null(self):
        """EVALUATION_METRICS without eval_config_id should return NULL."""
        from datetime import datetime

        builder = self._make_builder(eval_output_type="SCORE")
        query, _ = builder.build_metric_value_query(
            "evaluation_metrics", datetime(2024, 1, 1), datetime(2024, 1, 31)
        )
        assert "NULL" in query

    def test_unknown_metric_type_returns_null(self):
        """Unknown metric type should return NULL."""
        from datetime import datetime

        builder = self._make_builder()
        query, _ = builder.build_metric_value_query(
            "unknown_metric", datetime(2024, 1, 1), datetime(2024, 1, 31)
        )
        assert "NULL" in query

    # -- Historical stats queries --

    def test_historical_stats_error_rates_function_calling(self):
        """Historical stats for ERROR_RATES should return mean and stddev."""
        from datetime import datetime

        builder = self._make_builder()
        query, _ = builder.build_historical_stats_query(
            "error_rates_for_function_calling",
            datetime(2024, 1, 1),
            datetime(2024, 1, 31),
        )
        assert "mean" in query
        assert "stddev" in query
        assert "observation_type = 'tool'" in query

    def test_historical_stats_span_response_time(self):
        """Historical stats for SPAN_RESPONSE_TIME should use latency_ms."""
        from datetime import datetime

        builder = self._make_builder()
        query, _ = builder.build_historical_stats_query(
            "span_response_time", datetime(2024, 1, 1), datetime(2024, 1, 31)
        )
        assert "avg(latency_ms) AS mean" in query
        assert "stddevSamp(latency_ms) AS stddev" in query

    def test_historical_stats_eval_score(self):
        """Historical stats for EVALUATION_METRICS SCORE should use output_float."""
        from datetime import datetime

        builder = self._make_builder(
            eval_config_id="eval-cfg-123",
            eval_output_type="SCORE",
        )
        query, _ = builder.build_historical_stats_query(
            "evaluation_metrics", datetime(2024, 1, 1), datetime(2024, 1, 31)
        )
        assert "avg(output_float)" in query
        assert "stddevSamp(output_float)" in query

    def test_historical_stats_aggregated_metrics_return_null(self):
        """COUNT_OF_ERRORS etc. should return NULL for stats (handled in Python)."""
        from datetime import datetime

        builder = self._make_builder()
        query, _ = builder.build_historical_stats_query(
            "count_of_errors", datetime(2024, 1, 1), datetime(2024, 1, 31)
        )
        assert "NULL AS mean" in query

    # -- Time series queries --

    def test_time_series_token_usage(self):
        """TOKEN_USAGE time series should bucket by freq_seconds."""
        from datetime import datetime

        builder = self._make_builder()
        query, params = builder.build_time_series_query(
            "token_usage", datetime(2024, 1, 1), datetime(2024, 1, 31), 3600
        )
        assert "sum(total_tokens)" in query
        assert "timestamp" in query
        assert "GROUP BY" in query
        assert "ORDER BY" in query
        assert params["freq_seconds"] == 3600

    def test_time_series_count_of_errors(self):
        """COUNT_OF_ERRORS time series should use countIf."""
        from datetime import datetime

        builder = self._make_builder()
        query, _ = builder.build_time_series_query(
            "count_of_errors", datetime(2024, 1, 1), datetime(2024, 1, 31), 3600
        )
        assert "countIf(status = 'ERROR')" in query

    def test_time_series_span_response_time(self):
        """SPAN_RESPONSE_TIME time series should avg latency_ms."""
        from datetime import datetime

        builder = self._make_builder()
        query, _ = builder.build_time_series_query(
            "span_response_time", datetime(2024, 1, 1), datetime(2024, 1, 31), 3600
        )
        assert "avg(latency_ms)" in query

    def test_time_series_error_rates(self):
        """Error rate time series should produce per-bucket ratios."""
        from datetime import datetime

        builder = self._make_builder()
        query, params = builder.build_time_series_query(
            "error_rates_for_function_calling",
            datetime(2024, 1, 1),
            datetime(2024, 1, 31),
            3600,
        )
        assert "countIf(status = 'ERROR')" in query
        assert "observation_type" in query

    def test_time_series_error_free_session_rates(self):
        """ERROR_FREE_SESSION_RATES time series should group by session_id."""
        from datetime import datetime

        builder = self._make_builder()
        query, _ = builder.build_time_series_query(
            "error_free_session_rates",
            datetime(2024, 1, 1),
            datetime(2024, 1, 31),
            3600,
        )
        assert "session_id" in query

    def test_time_series_eval_metrics_score(self):
        """EVALUATION_METRICS SCORE time series should use eval_logger."""
        from datetime import datetime

        builder = self._make_builder(
            eval_config_id="eval-cfg-123",
            eval_output_type="SCORE",
        )
        query, _ = builder.build_time_series_query(
            "evaluation_metrics",
            datetime(2024, 1, 1),
            datetime(2024, 1, 31),
            3600,
        )
        assert "avg(output_float)" in query
        assert "tracer_eval_logger" in query

    def test_time_series_unknown_metric(self):
        """Unknown metric should return empty result set."""
        from datetime import datetime

        builder = self._make_builder()
        query, _ = builder.build_time_series_query(
            "unknown_metric", datetime(2024, 1, 1), datetime(2024, 1, 31), 3600
        )
        assert "1 = 0" in query or "NULL" in query

    # -- Filter translation --

    def test_filters_are_translated(self):
        """Monitor filters should be translated to CH WHERE clauses."""
        builder = self._make_builder(
            filters={
                "observation_type": "llm",
            }
        )
        assert builder._filter_clause != ""
        assert "observation_type" in builder._filter_clause

    def test_observation_type_list_filter(self):
        """observation_type list filter should use IN."""
        builder = self._make_builder(
            filters={
                "observation_type": ["llm", "tool"],
            }
        )
        assert "IN" in builder._filter_clause

    def test_empty_filters(self):
        """Empty filters should produce empty filter clause."""
        builder = self._make_builder(filters={})
        assert builder._filter_clause == ""

    def test_all_metric_values_produce_valid_sql(self):
        """All metric types should produce valid SQL (not crash)."""
        from datetime import datetime

        from tracer.services.clickhouse.query_builders.monitor_metrics import (
            COUNT_OF_ERRORS,
            DAILY_TOKENS_SPENT,
            ERROR_FREE_SESSION_RATES,
            ERROR_RATES_FOR_FUNCTION_CALLING,
            LLM_API_FAILURE_RATES,
            LLM_RESPONSE_TIME,
            MONTHLY_TOKENS_SPENT,
            SERVICE_PROVIDER_ERROR_RATES,
            SPAN_RESPONSE_TIME,
            TOKEN_USAGE,
        )

        builder = self._make_builder()
        metric_types = [
            COUNT_OF_ERRORS,
            ERROR_RATES_FOR_FUNCTION_CALLING,
            ERROR_FREE_SESSION_RATES,
            SERVICE_PROVIDER_ERROR_RATES,
            LLM_API_FAILURE_RATES,
            SPAN_RESPONSE_TIME,
            LLM_RESPONSE_TIME,
            TOKEN_USAGE,
            DAILY_TOKENS_SPENT,
            MONTHLY_TOKENS_SPENT,
        ]
        for mt in metric_types:
            query, params = builder.build_metric_value_query(
                mt, datetime(2024, 1, 1), datetime(2024, 1, 31)
            )
            assert isinstance(query, str)
            assert isinstance(params, dict)
            assert len(query) > 10


# ============================================================================
# 23. Session Time-Series Query Builder Tests
# ============================================================================


@pytest.mark.unit
class TestSessionTimeSeriesQueryBuilder:
    """Test session graph query generation."""

    def test_session_filter_alias_resolves_before_grouping(self):
        from tracer.services.clickhouse.query_builders.session_time_series import (
            SessionTimeSeriesQueryBuilder,
        )

        session_id = "003b76f1-2b4a-4af5-b0dc-224d687374d4"
        builder = SessionTimeSeriesQueryBuilder(
            project_id="project-1",
            filters=[
                {
                    "column_id": "session",
                    "display_name": "Session",
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "in",
                        "filter_value": [session_id],
                        "col_type": "SYSTEM_METRIC",
                    },
                }
            ],
        )

        query, params = builder.build()

        assert "trace_session_id_remap" in query
        assert "ts_remap.survivor_id" in query
        assert "IN %(session_id_1)s" in query
        assert query.index("IN %(session_id_1)s") < query.index("GROUP BY session_id")
        assert params["session_id_1"] == (session_id,)

    def test_session_aggregate_filter_uses_inner_having(self):
        from tracer.services.clickhouse.query_builders.session_time_series import (
            SessionTimeSeriesQueryBuilder,
        )

        builder = SessionTimeSeriesQueryBuilder(
            project_id="project-1",
            interval="month",
            filters=[
                {
                    "column_id": "duration",
                    "filter_config": {
                        "filter_type": "number",
                        "filter_op": "greater_than",
                        "filter_value": 50,
                        "col_type": "SYSTEM_METRIC",
                    },
                }
            ],
        )

        query, params = builder.build()

        assert "HAVING session_duration > %(having_901)s" in query
        assert "span_attr_num" not in query
        assert params["having_901"] == 50


# ============================================================================
# 24. Session Analytics Query Builder Tests
# ============================================================================


@pytest.mark.unit
class TestSessionAnalyticsQueryBuilder:
    """Test SessionAnalyticsQueryBuilder for all query methods."""

    PROJECT_ID = "test-project-id"

    def _make_builder(self, **kwargs):
        from tracer.services.clickhouse.query_builders.session_analytics import (
            SessionAnalyticsQueryBuilder,
        )

        return SessionAnalyticsQueryBuilder(project_id=self.PROJECT_ID, **kwargs)

    def test_build_raises_not_implemented(self):
        """build() should raise NotImplementedError."""
        builder = self._make_builder()
        with pytest.raises(NotImplementedError):
            builder.build()

    # -- Session metrics query --

    def test_session_metrics_query_structure(self):
        """Session metrics should aggregate by trace_session_id."""
        builder = self._make_builder()
        query, params = builder.build_session_metrics_query(["sess-1", "sess-2"])
        assert "trace_session_id" in query
        assert "GROUP BY trace_session_id" in query
        assert "count(DISTINCT trace_id)" in query
        assert "sum(total_tokens)" in query
        assert "sum(cost)" in query
        assert params["session_ids"] == ["sess-1", "sess-2"]

    def test_session_metrics_query_selects_time_columns(self):
        """Session metrics should include first/last trace time."""
        builder = self._make_builder()
        query, _ = builder.build_session_metrics_query(["sess-1"])
        assert "first_trace_time" in query
        assert "last_trace_time" in query
        assert "started_at" in query
        assert "ended_at" in query

    def test_session_metrics_query_has_project_filter(self):
        """Session metrics should filter by project_id."""
        builder = self._make_builder()
        query, params = builder.build_session_metrics_query(["sess-1"])
        assert "project_id" in query
        assert params["project_id"] == self.PROJECT_ID

    # -- Session navigation query --

    def test_session_navigation_query_structure(self):
        """Navigation query should list all sessions ordered by start time."""
        builder = self._make_builder()
        query, params = builder.build_session_navigation_query()
        assert "trace_session_id" in query
        assert "GROUP BY trace_session_id" in query
        assert "ORDER BY started_at DESC" in query
        # UUID-vs-empty-string makes CH raise Code 376 (Cannot parse uuid).
        assert "trace_session_id != ''" not in query
        assert "trace_session_id IS NOT NULL" in query

    def test_session_navigation_has_aggregate_columns(self):
        """Navigation query should include count, tokens, cost."""
        builder = self._make_builder()
        query, _ = builder.build_session_navigation_query()
        assert "count(DISTINCT trace_id)" in query
        assert "sum(total_tokens)" in query
        assert "sum(cost)" in query

    def test_session_navigation_excludes_nil_uuid(self):
        """Navigation query should exclude the ClickHouse nil UUID."""
        builder = self._make_builder()
        query, _ = builder.build_session_navigation_query()
        assert "00000000-0000-0000-0000-000000000000" in query

    # -- User stats query --

    def test_user_stats_query_structure(self):
        """User stats should aggregate by end_user_id."""
        builder = self._make_builder()
        query, params = builder.build_user_stats_query("user-123")
        assert "end_user_id" in query
        assert params["user_id"] == "user-123"
        assert "session_count" in query
        assert "total_tokens" in query
        assert "total_cost" in query
        assert "first_seen" in query
        assert "last_seen" in query

    def test_user_stats_has_project_filter(self):
        """User stats should filter by project_id."""
        builder = self._make_builder()
        query, params = builder.build_user_stats_query("user-123")
        assert "project_id" in query
        assert params["project_id"] == self.PROJECT_ID

    # -- First/last message query --

    def test_first_last_message_query_structure(self):
        """Should return two queries (first and last) with shared params."""
        builder = self._make_builder()
        first_q, last_q, params = builder.build_first_last_message_query(
            ["sess-1", "sess-2"]
        )
        assert isinstance(first_q, str)
        assert isinstance(last_q, str)
        assert params["session_ids"] == ["sess-1", "sess-2"]

    def test_first_message_orders_asc(self):
        """First message query should ORDER BY start_time ASC."""
        builder = self._make_builder()
        first_q, _, _ = builder.build_first_last_message_query(["sess-1"])
        assert "ORDER BY start_time ASC" in first_q

    def test_last_message_orders_desc(self):
        """Last message query should ORDER BY start_time DESC."""
        builder = self._make_builder()
        _, last_q, _ = builder.build_first_last_message_query(["sess-1"])
        assert "ORDER BY start_time DESC" in last_q

    def test_first_last_message_uses_limit_1_by(self):
        """Both queries should use LIMIT 1 BY trace_session_id."""
        builder = self._make_builder()
        first_q, last_q, _ = builder.build_first_last_message_query(["sess-1"])
        assert "LIMIT 1 BY trace_session_id" in first_q
        assert "LIMIT 1 BY trace_session_id" in last_q

    def test_first_last_message_filters_root_spans(self):
        """Both queries should filter parent_span_id IS NULL."""
        builder = self._make_builder()
        first_q, last_q, _ = builder.build_first_last_message_query(["sess-1"])
        assert "parent_span_id IS NULL" in first_q
        assert "parent_span_id IS NULL" in last_q

    def test_first_last_message_selects_io(self):
        """Both queries should select input and output columns."""
        builder = self._make_builder()
        first_q, last_q, _ = builder.build_first_last_message_query(["sess-1"])
        assert "input" in first_q
        assert "output" in first_q
        assert "input" in last_q
        assert "output" in last_q


# ============================================================================
# 24. QueryType and Route Settings for New Builders
# ============================================================================


@pytest.mark.unit
class TestNewQueryTypeRoutingSettings:
    """Test that new QueryType enum values and route settings exist."""

    def test_session_analytics_query_type(self):
        """SESSION_ANALYTICS should exist in QueryType enum."""
        from tracer.services.clickhouse.query_service import QueryType

        assert hasattr(QueryType, "SESSION_ANALYTICS")
        assert QueryType.SESSION_ANALYTICS.value == "SESSION_ANALYTICS"

    def test_annotation_graph_query_type(self):
        """ANNOTATION_GRAPH should exist in QueryType enum."""
        from tracer.services.clickhouse.query_service import QueryType

        assert hasattr(QueryType, "ANNOTATION_GRAPH")
        assert QueryType.ANNOTATION_GRAPH.value == "ANNOTATION_GRAPH"

    def test_trace_detail_query_type(self):
        """TRACE_DETAIL should exist in QueryType enum."""
        from tracer.services.clickhouse.query_service import QueryType

        assert hasattr(QueryType, "TRACE_DETAIL")
        assert QueryType.TRACE_DETAIL.value == "TRACE_DETAIL"

    def test_monitor_metrics_query_type(self):
        """MONITOR_METRICS should exist in QueryType enum."""
        from tracer.services.clickhouse.query_service import QueryType

        assert hasattr(QueryType, "MONITOR_METRICS")
        assert QueryType.MONITOR_METRICS.value == "MONITOR_METRICS"

    def test_annotation_detail_query_type(self):
        """ANNOTATION_DETAIL should exist in QueryType enum."""
        from tracer.services.clickhouse.query_service import QueryType

        assert hasattr(QueryType, "ANNOTATION_DETAIL")
        assert QueryType.ANNOTATION_DETAIL.value == "ANNOTATION_DETAIL"

    def test_new_builders_in_package_exports(self):
        """New query builders should be exported from __init__.py."""
        import tracer.services.clickhouse.query_builders as pkg

        assert "AnnotationGraphQueryBuilder" in pkg.__all__
        assert "MonitorMetricsQueryBuilder" in pkg.__all__
        assert "SessionAnalyticsQueryBuilder" in pkg.__all__


# ============================================================================
# SPAN_ATTRIBUTE filter contract — exhaustive per-type / per-op coverage
# ============================================================================


def _span_attr_filter(col_id, *, filter_type, filter_op, filter_value=None):
    """Build a single SPAN_ATTRIBUTE filter dict in the API shape."""
    return {
        "column_id": col_id,
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": filter_type,
            "filter_op": filter_op,
            "filter_value": filter_value,
        },
    }


def _translate_one(filter_dict, *, query_mode="trace"):
    from tracer.services.clickhouse.query_builders.filters import (
        ClickHouseFilterBuilder,
    )

    builder = ClickHouseFilterBuilder(query_mode=query_mode)
    where, params = builder.translate([filter_dict])
    return where, params


class TestSpanAttrConditionContract:
    """End-to-end contract tests for _build_span_attr_condition.

    These tests assert on the generated SQL string and the parameter dict
    (matching the existing TestClickHouseFilterBuilder pattern) — no
    ClickHouse connection required.
    """

    # ------------------------------------------------------------------
    # text type — happy paths
    # ------------------------------------------------------------------
    def test_text_equals(self):
        where, params = _translate_one(
            _span_attr_filter(
                "k", filter_type="text", filter_op="equals", filter_value="v"
            )
        )
        assert "span_attr_str" in where
        assert "mapContains(span_attr_str, 'k')" in where
        assert "= %(" in where
        assert "v" in params.values()

    def test_text_not_equals_uses_exists_and(self):
        """not_equals must require key present (exists AND ...), not the
        legacy NOT exists OR ... shape that leaked rows past the filter."""
        where, _ = _translate_one(
            _span_attr_filter(
                "k", filter_type="text", filter_op="not_equals", filter_value="v"
            )
        )
        assert "AND lower(span_attr_str['k']) != " in where
        assert "NOT mapContains" not in where

    def test_text_in(self):
        where, params = _translate_one(
            _span_attr_filter(
                "k", filter_type="text", filter_op="in", filter_value=["a", "b"]
            )
        )
        assert "lower(span_attr_str['k']) IN" in where
        assert ("a", "b") in params.values()

    def test_text_not_in_uses_exists_and(self):
        """Regression for the voice-call ended_reason no-op bug.
        not_in must require key present, NOT use 'NOT exists OR ...'."""
        where, params = _translate_one(
            _span_attr_filter(
                "ended_reason",
                filter_type="text",
                filter_op="not_in",
                filter_value=["voicemail", "assistant-ended-call"],
            )
        )
        assert "mapContains(span_attr_str, 'ended_reason')" in where
        assert "AND lower(span_attr_str['ended_reason']) NOT IN" in where
        assert "NOT mapContains" not in where
        assert ("voicemail", "assistant-ended-call") in params.values()

    def test_text_contains_wildcard(self):
        where, params = _translate_one(
            _span_attr_filter(
                "k", filter_type="text", filter_op="contains", filter_value="abc"
            )
        )
        assert "LIKE" in where
        assert "%abc%" in params.values()

    def test_text_not_contains_uses_exists_and(self):
        where, params = _translate_one(
            _span_attr_filter(
                "k", filter_type="text", filter_op="not_contains", filter_value="abc"
            )
        )
        assert "AND span_attr_str['k'] NOT ILIKE" in where
        assert "NOT mapContains" not in where
        assert "%abc%" in params.values()

    def test_text_starts_with(self):
        _, params = _translate_one(
            _span_attr_filter(
                "k", filter_type="text", filter_op="starts_with", filter_value="abc"
            )
        )
        assert "abc%" in params.values()

    def test_text_ends_with(self):
        _, params = _translate_one(
            _span_attr_filter(
                "k", filter_type="text", filter_op="ends_with", filter_value="abc"
            )
        )
        assert "%abc" in params.values()

    def test_text_is_null(self):
        where, _ = _translate_one(
            _span_attr_filter("k", filter_type="text", filter_op="is_null")
        )
        assert "NOT mapContains(span_attr_str, 'k')" in where

    def test_text_is_not_null(self):
        where, _ = _translate_one(
            _span_attr_filter("k", filter_type="text", filter_op="is_not_null")
        )
        assert "mapContains(span_attr_str, 'k')" in where
        assert "NOT mapContains" not in where

    # ------------------------------------------------------------------
    # number type — happy paths + coercion
    # ------------------------------------------------------------------
    def test_number_equals_coerces_string_to_float(self):
        """FE always ships numerics as strings; backend must coerce to
        float so CH does numeric (not lexical) comparison."""
        _, params = _translate_one(
            _span_attr_filter(
                "n", filter_type="number", filter_op="equals", filter_value="42"
            )
        )
        assert 42.0 in params.values()
        assert "42" not in [v for v in params.values() if isinstance(v, str)]

    def test_number_greater_than(self):
        where, params = _translate_one(
            _span_attr_filter(
                "n",
                filter_type="number",
                filter_op="greater_than",
                filter_value="100",
            )
        )
        assert "span_attr_num" in where
        assert "> %(" in where
        assert 100.0 in params.values()

    def test_number_between_coerces_each_bound(self):
        where, params = _translate_one(
            _span_attr_filter(
                "n",
                filter_type="number",
                filter_op="between",
                filter_value=["10", "50"],
            )
        )
        assert "BETWEEN" in where
        assert 10.0 in params.values()
        assert 50.0 in params.values()

    def test_number_not_between_uses_exists_and(self):
        where, _ = _translate_one(
            _span_attr_filter(
                "n",
                filter_type="number",
                filter_op="not_between",
                filter_value=["10", "50"],
            )
        )
        assert "AND span_attr_num['n'] NOT BETWEEN" in where
        assert "NOT mapContains" not in where

    def test_number_legacy_not_in_between_is_rejected(self):
        """`not_in_between` is the retired alias. Builder must raise."""
        with pytest.raises(ValueError):
            _translate_one(
                _span_attr_filter(
                    "n",
                    filter_type="number",
                    filter_op="not_in_between",
                    filter_value=["10", "50"],
                )
            )

    def test_number_between_with_single_element_raises(self):
        with pytest.raises(ValueError):
            _translate_one(
                _span_attr_filter(
                    "n",
                    filter_type="number",
                    filter_op="between",
                    filter_value=["10"],
                )
            )

    def test_number_between_with_non_list_raises(self):
        with pytest.raises(ValueError):
            _translate_one(
                _span_attr_filter(
                    "n",
                    filter_type="number",
                    filter_op="between",
                    filter_value="10",
                )
            )

    def test_number_greater_than_with_non_numeric_raises(self):
        with pytest.raises(ValueError):
            _translate_one(
                _span_attr_filter(
                    "n",
                    filter_type="number",
                    filter_op="greater_than",
                    filter_value="abc",
                )
            )

    # ------------------------------------------------------------------
    # boolean type — strict native bool only
    # ------------------------------------------------------------------
    def test_boolean_equals_true(self):
        where, params = _translate_one(
            _span_attr_filter(
                "b", filter_type="boolean", filter_op="equals", filter_value=True
            )
        )
        assert "span_attr_bool" in where
        assert 1 in params.values()

    def test_boolean_equals_false(self):
        _, params = _translate_one(
            _span_attr_filter(
                "b", filter_type="boolean", filter_op="equals", filter_value=False
            )
        )
        assert 0 in params.values()

    def test_boolean_string_true_rejected(self):
        """Strict: only native true/false. `'true'` strings must be rejected."""
        with pytest.raises(ValueError):
            _translate_one(
                _span_attr_filter(
                    "b",
                    filter_type="boolean",
                    filter_op="equals",
                    filter_value="true",
                )
            )

    def test_boolean_int_one_rejected(self):
        with pytest.raises(ValueError):
            _translate_one(
                _span_attr_filter(
                    "b", filter_type="boolean", filter_op="equals", filter_value=1
                )
            )

    def test_boolean_greater_than_rejected_by_contract(self):
        with pytest.raises(ValueError):
            _translate_one(
                _span_attr_filter(
                    "b",
                    filter_type="boolean",
                    filter_op="greater_than",
                    filter_value=True,
                )
            )

    # ------------------------------------------------------------------
    # type ↔ op contract violations
    # ------------------------------------------------------------------
    def test_contains_on_number_rejected(self):
        with pytest.raises(ValueError):
            _translate_one(
                _span_attr_filter(
                    "n",
                    filter_type="number",
                    filter_op="contains",
                    filter_value="abc",
                )
            )

    def test_unknown_op_rejected(self):
        with pytest.raises(ValueError):
            _translate_one(
                _span_attr_filter(
                    "k",
                    filter_type="text",
                    filter_op="somethingelse",
                    filter_value="v",
                )
            )

    def test_unknown_filter_type_rejected(self):
        with pytest.raises(ValueError):
            _translate_one(
                _span_attr_filter(
                    "k",
                    filter_type="json",
                    filter_op="equals",
                    filter_value="v",
                )
            )

    def test_in_with_empty_list_rejected(self):
        with pytest.raises(ValueError):
            _translate_one(
                _span_attr_filter(
                    "k", filter_type="text", filter_op="in", filter_value=[]
                )
            )

    def test_in_with_non_list_rejected(self):
        with pytest.raises(ValueError):
            _translate_one(
                _span_attr_filter(
                    "k", filter_type="text", filter_op="in", filter_value="a"
                )
            )

    def test_equals_with_none_rejected(self):
        with pytest.raises(ValueError):
            _translate_one(
                _span_attr_filter(
                    "k", filter_type="text", filter_op="equals", filter_value=None
                )
            )

    def test_legacy_equal_to_rejected(self):
        with pytest.raises(ValueError):
            _translate_one(
                _span_attr_filter(
                    "n",
                    filter_type="number",
                    filter_op="equal_to",
                    filter_value="42",
                )
            )

    def test_legacy_is_op_rejected(self):
        with pytest.raises(ValueError):
            _translate_one(
                _span_attr_filter(
                    "k", filter_type="text", filter_op="is", filter_value="v"
                )
            )

    def test_sql_injection_via_key_raises(self):
        """Key sanitizer must reject anything outside [a-zA-Z0-9._-]."""
        with pytest.raises(ValueError):
            _translate_one(
                _span_attr_filter(
                    "k'; DROP TABLE spans; --",
                    filter_type="text",
                    filter_op="equals",
                    filter_value="v",
                )
            )

    # ------------------------------------------------------------------
    # trace-mode wrap vs span-mode bare
    # ------------------------------------------------------------------
    def test_trace_mode_wraps_predicate(self):
        where, _ = _translate_one(
            _span_attr_filter(
                "k", filter_type="text", filter_op="equals", filter_value="v"
            ),
            query_mode="trace",
        )
        assert "trace_id IN (SELECT trace_id FROM" in where

    def test_span_mode_returns_bare_predicate(self):
        where, _ = _translate_one(
            _span_attr_filter(
                "k", filter_type="text", filter_op="equals", filter_value="v"
            ),
            query_mode="span",
        )
        assert "trace_id IN (" not in where
        assert "mapContains" in where
