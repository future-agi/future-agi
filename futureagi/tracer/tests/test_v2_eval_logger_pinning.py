"""CH25 eval reads must follow the deployed eval-table topology."""

from __future__ import annotations

import re
from pathlib import Path
from unittest import mock

import pytest

from tracer.services.clickhouse.v2.query_builders.filters import (
    ClickHouseFilterBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.span_list import (
    SpanListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.user_list import (
    UserListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.voice_call_list import (
    VoiceCallListQueryBuilderV2,
)

pytestmark = pytest.mark.unit

PROJECT_ID = "11111111-1111-1111-1111-111111111111"
ORGANIZATION_ID = "33333333-3333-3333-3333-333333333333"
EVAL_CONFIG_ID = "22222222-2222-2222-2222-222222222222"
FORBIDDEN_V2_SCHEMA_COLUMNS = frozenset(
    {"status", "skipped_reason", "config_hash", "attempts"}
)


@pytest.fixture(params=("legacy", "v2"))
def eval_table_shape(request, settings):
    """Exercise the production table shape and the future v2 shape."""

    if request.param == "legacy":
        settings.CH25_EVAL_LOGGER_TABLE = "tracer_eval_logger"
    else:
        settings.CH25_EVAL_LOGGER_TABLE = "tracer_eval_logger_v2"
    return request.param


def _assert_eval_sql_matches_shape(
    sql: str, shape: str, *, requires_version_replay: bool = True
) -> None:
    if shape == "legacy":
        assert re.search(r"FROM\s+tracer_eval_logger(?:\s|$)", sql)
        assert "tracer_eval_logger_v2" not in sql
        if requires_version_replay:
            assert "_peerdb_version" in sql
    else:
        assert "tracer_eval_logger_v2" in sql
        assert "_peerdb_version" not in sql
        assert "_peerdb_is_deleted" not in sql


def _schema_011_columns() -> set[str]:
    ddl_path = (
        Path(__file__).parents[1]
        / "services"
        / "clickhouse"
        / "v2"
        / "schema"
        / "011_eval_logger_v2.sql"
    )
    ddl = ddl_path.read_text()
    table_block = re.search(
        r"CREATE TABLE IF NOT EXISTS tracer_eval_logger_v2\s*\((.*?)\)\s*ENGINE",
        ddl,
        re.DOTALL,
    )
    assert table_block is not None
    return set(
        re.findall(r"^\s{4}([a-z][a-z0-9_]*)\s+", table_block.group(1), re.MULTILINE)
    )


def _patch_eval_config_resolution():
    values = mock.MagicMock()
    values.__iter__ = lambda self: iter([EVAL_CONFIG_ID])
    values.first.return_value = None
    fake_qs = mock.MagicMock()
    fake_qs.exists.return_value = True
    fake_qs.filter.return_value = fake_qs
    fake_qs.values_list.return_value = values
    objects = mock.MagicMock()
    objects.filter.return_value = fake_qs
    template_manager = mock.MagicMock()
    template_manager.filter.return_value.values.return_value.first.return_value = None
    return (
        mock.patch(
            "tracer.models.custom_eval_config.CustomEvalConfig.objects", objects
        ),
        mock.patch(
            "model_hub.models.evals_metric.EvalTemplate.no_workspace_objects",
            template_manager,
        ),
    )


@pytest.mark.parametrize("builder_kind", ("trace", "span", "voice", "user"))
def test_v2_list_eval_queries_follow_eval_table_setting(builder_kind, eval_table_shape):
    builders = {
        "trace": lambda: TraceListQueryBuilderV2(
            project_id=PROJECT_ID, eval_config_ids=[EVAL_CONFIG_ID]
        ),
        "span": lambda: SpanListQueryBuilderV2(
            project_id=PROJECT_ID, eval_config_ids=[EVAL_CONFIG_ID]
        ),
        "voice": lambda: VoiceCallListQueryBuilderV2(
            project_id=PROJECT_ID, eval_config_ids=[EVAL_CONFIG_ID]
        ),
        "user": lambda: UserListQueryBuilderV2(
            organization_id=ORGANIZATION_ID, project_id=PROJECT_ID
        ),
    }
    builder = builders[builder_kind]()
    if builder_kind == "span":
        sql, _ = builder.build_eval_query(["span-1"])
    elif builder_kind == "user":
        # User eval reads are tenant-authorized at runtime and deliberately
        # compile to no SQL without a finite eval-config scope.
        sql, _ = builder.build_eval_query(
            ["end-user-1"], allowed_eval_config_ids=[EVAL_CONFIG_ID]
        )
    else:
        sql, _ = builder.build_eval_query(["trace-1"])

    _assert_eval_sql_matches_shape(
        sql,
        eval_table_shape,
        requires_version_replay=builder_kind != "user",
    )
    if builder_kind == "user":
        if eval_table_shape == "legacy":
            assert "eval_scan._peerdb_is_deleted = 0" in sql
            assert "eval_scan.deleted = 0 OR eval_scan.deleted IS NULL" in sql
            assert "eval_scan.is_deleted = 0" not in sql
        else:
            assert "eval_scan.is_deleted = 0" in sql
            assert "_peerdb_is_deleted" not in sql
            assert "eval_scan.deleted" not in sql


@pytest.mark.parametrize("builder_kind", ("trace", "span", "voice"))
def test_v2_list_eval_queries_only_reference_schema_011_physical_columns(
    builder_kind, settings
):
    settings.CH25_EVAL_LOGGER_TABLE = "tracer_eval_logger_v2"
    schema_columns = _schema_011_columns()
    assert FORBIDDEN_V2_SCHEMA_COLUMNS.isdisjoint(schema_columns)

    builders = {
        "trace": TraceListQueryBuilderV2(
            project_id=PROJECT_ID, eval_config_ids=[EVAL_CONFIG_ID]
        ),
        "span": SpanListQueryBuilderV2(
            project_id=PROJECT_ID, eval_config_ids=[EVAL_CONFIG_ID]
        ),
        "voice": VoiceCallListQueryBuilderV2(
            project_id=PROJECT_ID, eval_config_ids=[EVAL_CONFIG_ID]
        ),
    }
    if builder_kind == "span":
        sql, _ = builders[builder_kind].build_eval_query(["span-1"])
    else:
        sql, _ = builders[builder_kind].build_eval_query(["trace-1"])

    # Status-shaped response columns remain stable, but their values are
    # synthesized because schema 011 does not carry lifecycle fields.
    if builder_kind == "voice":
        assert "'completed' AS latest_status" in sql
        assert "CAST(NULL AS Nullable(String)) AS latest_skipped_reason" in sql
    else:
        assert "'completed' AS status" in sql
        assert "CAST(NULL AS Nullable(String)) AS skipped_reason" in sql
    assert "argMax(status," not in sql
    assert "argMax(tuple(skipped_reason)" not in sql
    assert "eval_scan.status" not in sql
    assert "eval_scan.skipped_reason" not in sql
    assert "config_hash" not in sql
    assert "attempts" not in sql


@pytest.mark.parametrize(
    ("query_mode", "filter_kind"),
    (
        (ClickHouseFilterBuilderV2.QUERY_MODE_TRACE, "eval"),
        (ClickHouseFilterBuilderV2.QUERY_MODE_SPAN, "eval"),
        (ClickHouseFilterBuilderV2.QUERY_MODE_TRACE, "has_eval"),
        (ClickHouseFilterBuilderV2.QUERY_MODE_SPAN, "has_eval"),
    ),
)
def test_v2_filter_eval_probes_follow_eval_table_setting(
    query_mode, filter_kind, eval_table_shape
):
    builder = ClickHouseFilterBuilderV2(
        project_id=PROJECT_ID,
        query_mode=query_mode,
        score_date_scope=False,
    )
    if filter_kind == "eval":
        filters = [
            {
                "column_id": EVAL_CONFIG_ID,
                "filter_config": {
                    "col_type": "EVAL_METRIC",
                    "filter_type": "number",
                    "filter_op": "equals",
                    "filter_value": 80,
                },
            }
        ]
        patch_config, patch_template = _patch_eval_config_resolution()
        with patch_config, patch_template:
            sql, _ = builder.translate(filters)
    else:
        sql, _ = builder.translate(
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

    _assert_eval_sql_matches_shape(sql, eval_table_shape)
    if eval_table_shape == "legacy":
        assert "ORDER BY eval_scan._peerdb_version DESC" in sql
        assert "latest_eval._peerdb_is_deleted = 0" in sql
        assert "(latest_eval.deleted = 0 OR latest_eval.deleted IS NULL)" in sql
    else:
        assert "ORDER BY eval_scan._version DESC" in sql
        assert "latest_eval.is_deleted = 0" in sql
    assert not any(column in sql for column in ("config_hash", "attempts"))
