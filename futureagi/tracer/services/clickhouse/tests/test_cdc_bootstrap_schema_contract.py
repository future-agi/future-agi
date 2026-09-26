"""Offline CREATE contracts only; not bootstrap, migration, or CDC qualification."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path


def _ddl(name: str) -> str:
    # Read literal DDL without importing schema.py, Django, or database clients.
    schema = Path(__file__).resolve().parents[1] / "schema.py"
    assignments = [
        node.value
        for node in ast.parse(schema.read_text(encoding="utf-8")).body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == name
            for target in node.targets
        )
    ]
    assert len(assignments) == 1, name
    ddl = ast.literal_eval(assignments[0])
    assert isinstance(ddl, str), name
    return ddl


def _assert_column(ddl: str, name: str, declaration: str) -> None:
    # Whole declarations also reject duplicates, wrong defaults, and name aliases.
    matches = re.findall(rf"^[ \t]*{re.escape(name)}[ \t]+([^\n]+),[ \t]*$", ddl, re.M)
    assert matches == [declaration], name


def test_score_keeps_developai_and_tracer_project_scopes_distinct_and_nullable():
    ddl = _ddl("CDC_MODEL_HUB_SCORE")
    _assert_column(ddl, "project_id", "Nullable(UUID)")  # DevelopAI FK, not tracing.
    _assert_column(ddl, "tracer_project_id", "Nullable(UUID)")


def test_enduser_metadata_default_is_a_json_object_not_escaped_template_braces():
    ddl = _ddl("CDC_TRACER_ENDUSER")
    defaults = re.findall(r"^\s*metadata String DEFAULT '([^']*)',", ddl, re.M)
    assert len(defaults) == 1
    assert json.loads(defaults[0]) == {}


def test_score_history_defaults_to_json_array_without_changing_value():
    ddl = _ddl("CDC_MODEL_HUB_SCORE")
    _assert_column(ddl, "value", "String DEFAULT '{}'")
    _assert_column(ddl, "value_history", "String DEFAULT '[]'")


def test_score_source_targets_remain_nullable_without_sentinel_defaults():
    ddl = _ddl("CDC_MODEL_HUB_SCORE")
    for name in (
        "trace_id",
        "trace_session_id",
        "call_execution_id",
        "dataset_row_id",
        "prototype_run_id",
        "queue_item_id",
    ):
        _assert_column(ddl, name, "Nullable(UUID)")
    _assert_column(ddl, "observation_span_id", "Nullable(String)")


def test_agent_speaking_order_preserves_null_false_true_without_default():
    ddl = _ddl("CDC_SIMULATE_AGENT_DEFINITION")
    # NULL means derive from inbound/outbound; it must not silently become false.
    _assert_column(ddl, "target_speaks_first", "Nullable(UInt8)")
    _assert_column(ddl, "inbound", "UInt8 DEFAULT 0")


def test_eval_span_trace_session_targets_keep_nullable_foreign_keys():
    ddl = _ddl("CDC_EVAL_LOGGER")
    _assert_column(ddl, "trace_id", "Nullable(UUID)")
    _assert_column(ddl, "observation_span_id", "Nullable(String)")
    _assert_column(ddl, "trace_session_id", "Nullable(UUID)")
    _assert_column(ddl, "target_type", "LowCardinality(String) DEFAULT 'span'")


def test_simulation_prompt_targets_do_not_require_agent_identifiers():
    for name in ("CDC_SIMULATE_SCENARIOS", "CDC_SIMULATE_RUN_TEST"):
        ddl = _ddl(name)
        for column in (
            "agent_definition_id",
            "prompt_template_id",
            "prompt_version_id",
        ):
            _assert_column(ddl, column, "Nullable(UUID)")
        if name == "CDC_SIMULATE_RUN_TEST":
            _assert_column(ddl, "agent_version_id", "Nullable(UUID)")
            _assert_column(ddl, "source_type", "Nullable(String)")
        else:
            _assert_column(
                ddl, "source_type", "LowCardinality(String) DEFAULT 'agent_definition'"
            )


def test_legacy_session_cdc_contains_source_fields_not_native_session_facts():
    # Independently checked against public.trace_session information_schema on
    # the local stack: eight PG fields, plus the three PeerDB transport fields.
    # External identity/first_seen belong to native trace_sessions (018), not
    # this CDC source. Requiring invented columns prevents safe retained startup.
    ddl = _ddl("CDC_TRACE_SESSION")
    columns = set(re.findall(r"^    (\w+)\s+\S", ddl, re.M))
    assert columns == {
        "id",
        "project_id",
        "name",
        "bookmarked",
        "deleted",
        "deleted_at",
        "created_at",
        "updated_at",
        "_peerdb_synced_at",
        "_peerdb_is_deleted",
        "_peerdb_version",
    }
    _assert_column(ddl, "project_id", "UUID")
    _assert_column(ddl, "name", "Nullable(String)")
    _assert_column(ddl, "deleted_at", "Nullable(DateTime64(3))")


def test_agent_version_pass_rate_preserves_postgres_precision():
    # AgentVersion.pass_rate is DecimalField(max_digits=5, decimal_places=2).
    # Local PG information_schema independently confirms numeric(5,2), nullable.
    # Reusing score's decimal(3,1) silently rounds valid percentages, e.g. 99.99.
    ddl = _ddl("CDC_SIMULATE_AGENT_VERSION")
    _assert_column(ddl, "pass_rate", "Nullable(Decimal(5, 2))")
    _assert_column(ddl, "score", "Nullable(Decimal(3, 1))")
