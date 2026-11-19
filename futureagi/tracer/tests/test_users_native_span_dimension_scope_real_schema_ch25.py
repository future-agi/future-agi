"""Old stored-column versus root-name Users rows on the deployed CH25 schema.

Synthetic historic rows have blank materialized names; only children carry
users. All nine native dimensions are decided in one statement per operator,
for scalar, singleton, organization and empty scopes. The legacy compiler
below changes ONLY Trace Name back to its pre-root-lookup row predicate.
Neither reader nor schema is modified when executing those comparison rows.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from tracer.services.clickhouse.query_builders.filters import ClickHouseFilterBuilder
from tracer.services.clickhouse.query_builders.user_list import (
    USER_NATIVE_SPAN_DIMENSIONS,
)
from tracer.services.clickhouse.trace_name_sql import tagged_trace_name_root_lookups
from tracer.services.clickhouse.v2.query_builders.user_list import (
    UserListQueryBuilderV2,
)
from tracer.tests._users_live_ch import lane_client

pytestmark = pytest.mark.integration
START = datetime(2026, 9, 1, tzinfo=UTC)
END = START + timedelta(days=1)
DIMENSIONS = sorted(USER_NATIVE_SPAN_DIMENSIONS)
OPERATIONS = [
    ("equals", "OK"),
    ("not_equals", "OK"),
    ("in", ["OK", "Error"]),
    ("not_in", ["OK"]),
    ("contains", "R"),
    ("not_contains", "R"),
    ("starts_with", "O"),
    ("ends_with", "K"),
    ("is_null", None),
    ("is_not_null", None),
]


def _date_filter():
    return {
        "column_id": "created_at",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [START.isoformat(), END.isoformat()],
        },
    }


def _leaf(column, operation, value, col_type):
    config = {"filter_type": "text", "filter_op": operation}
    if col_type is not None:
        config["col_type"] = col_type
    if value is not None:
        config["filter_value"] = value
    return {
        "column_id": column,
        "property_id": f"system_attribute:traces:{column}",
        "filter_config": config,
    }


@pytest.fixture(scope="module")
def corpus():
    with lane_client() as client:
        projects = {name: uuid.uuid4() for name in ("one", "two", "excluded")}
        organization = uuid.uuid4()
        shared_trace = str(uuid.uuid4())
        # label, project, ordinary dimension, child's own name, resolved root.
        # C is orphaned; F's root is outside the one-day lookback. D proves an
        # explicit historic stored root name still wins over its own span name.
        records = {
            "A": ("one", "OK", "child OK", "OK"),
            "B": ("two", "Error", "child Error", "Error"),
            "C": ("one", "", "", None),
            "D": ("one", "Error", "child Error", "OK"),
            "E": ("excluded", "Secret", "child Secret", "Secret"),
            "F": ("one", "OK", "child OK", None),
        }
        users = {label: uuid.uuid4() for label in records}
        rows = []
        for label, (scope, native_value, child_name, root_name) in records.items():
            project = projects[scope]
            trace = shared_trace if label in {"A", "B", "E"} else str(uuid.uuid4())
            root = uuid.uuid4().hex
            if label != "C":
                rows.append(
                    (
                        project,
                        trace,
                        root,
                        "",
                        "task" if label == "D" else (root_name or "OK"),
                        START - timedelta(days=2) if label == "F" else START,
                        "root",
                        "native-scope",
                        None,
                        "",
                        "",
                        "",
                        "OK" if label == "D" else "",
                    )
                )
            rows.append(
                (
                    project,
                    trace,
                    uuid.uuid4().hex,
                    root,
                    child_name,
                    START + timedelta(hours=1),
                    native_value,
                    "native-scope",
                    users[label],
                    native_value,
                    native_value,
                    native_value,
                    "",
                )
            )
        try:
            client.execute(
                "INSERT INTO spans (project_id, trace_id, id, parent_span_id, name, "
                "start_time, observation_type, service_name, end_user_id, status, "
                "model, provider, trace_name) VALUES",
                rows,
                settings={"insert_allow_materialized_columns": 1},
            )
            client.execute(
                "INSERT INTO end_users (project_id, end_user_id, organization_id, "
                "user_id, user_id_type, first_seen) VALUES",
                [
                    (
                        projects[record[0]],
                        users[label],
                        organization,
                        label,
                        "string",
                        START,
                    )
                    for label, record in records.items()
                ],
            )
            assert client.execute(
                "SELECT count(), countIf(trace_name = '') FROM spans "
                "WHERE project_id IN %(projects)s AND end_user_id IS NOT NULL",
                {"projects": tuple(projects.values())},
            ) == [(6, 6)]
            yield client, projects, organization, users, records
        finally:
            for table in ("spans", "end_users"):
                client.execute(
                    f"ALTER TABLE {table} DELETE WHERE project_id IN %(projects)s "
                    "SETTINGS mutations_sync = 0",
                    {"projects": tuple(projects.values())},
                )


def _text_matches(value, operation, target):
    """Independent scalar oracle; each synthetic user has exactly one child."""
    lowered = value.lower()
    if operation == "is_null":
        return value == ""
    if operation == "is_not_null":
        return value != ""
    if operation in {"in", "not_in"}:
        positive = lowered in [item.lower() for item in target]
        return positive if operation == "in" else not positive
    target = target.lower()
    if operation in {"equals", "not_equals"}:
        positive = lowered == target
        return positive if operation == "equals" else not positive
    if operation in {"contains", "not_contains"}:
        positive = target in lowered
        return positive if operation == "contains" else not positive
    if operation == "starts_with":
        return lowered.startswith(target)
    assert operation == "ends_with"
    return lowered.endswith(target)


@pytest.mark.parametrize("scope", ["project", "project-set", "organization", "empty"])
@pytest.mark.parametrize(
    "col_type", ["SYSTEM_METRIC", None], ids=["system", "collection"]
)
@pytest.mark.parametrize(("operation", "value"), OPERATIONS)
def test_old_and_new_native_rows_across_authorized_scopes(
    corpus, scope, col_type, operation, value
):
    client, projects, organization, users, records = corpus
    allowed = (
        {"one", "two"}
        if scope == "organization"
        else ({"one"} if scope != "empty" else set())
    )
    scope_kwargs = (
        {"project_id": str(projects["one"])}
        if scope == "project"
        else {"project_ids": [str(projects[name]) for name in sorted(allowed)]}
    )
    leaves = [_leaf(column, operation, value, col_type) for column in DIMENSIONS]
    builder = UserListQueryBuilderV2(
        organization_id=str(organization),
        filters=[*leaves, _date_filter()],
        empty_scope=scope == "empty",
        **scope_kwargs,
    )
    candidates = [str(user) for user in users.values()]
    sql, params = builder.build_native_span_dimension_query(
        candidates, enumerate(leaves)
    )
    assert tagged_trace_name_root_lookups(sql)
    original = ClickHouseFilterBuilder._build_system_metric_condition

    def legacy_column(self, column, filter_type, filter_op, filter_value):
        if column == "trace_name":
            return self._build_column_condition(
                "trace_name", filter_type, filter_op, filter_value
            )
        return original(self, column, filter_type, filter_op, filter_value)

    with patch.object(
        ClickHouseFilterBuilder, "_build_system_metric_condition", legacy_column
    ):
        old_sql, old_params = builder.build_native_span_dimension_query(
            candidates, enumerate(leaves)
        )
    assert tagged_trace_name_root_lookups(old_sql) == []
    labels = {str(user): label for label, user in users.items()}

    def execute(query, query_params):
        return {
            labels[str(row[0])]: tuple(row[1:])
            for row in client.execute(query, query_params)
        }

    old_rows = execute(old_sql, old_params)
    new_rows = execute(sql, params)
    expected_old, expected_new = {}, {}
    for label, (project, native_value, child_name, root_name) in records.items():
        if project not in allowed:
            continue
        before, after = [], []
        for dimension in DIMENSIONS:
            column = USER_NATIVE_SPAN_DIMENSIONS[dimension]
            if column == "trace_name":
                before.append(int(_text_matches("", operation, value)))
                after.append(
                    int(col_type is None and operation == "is_null")
                    if root_name is None
                    else int(_text_matches(root_name, operation, value))
                )
            else:
                result = int(
                    _text_matches(
                        child_name if column == "name" else native_value,
                        operation,
                        value,
                    )
                )
                before.append(result)
                after.append(result)
        expected_old[label], expected_new[label] = tuple(before), tuple(after)
    assert old_rows == expected_old
    assert new_rows == expected_new
    # -s retains exact old/new labelled rows in the lane evidence, not merely
    # membership counts; excluded projects and empty scopes must return no rows.
    print(
        json.dumps(
            {
                "scope": scope,
                "col_type": col_type,
                "operation": operation,
                "dimensions": DIMENSIONS,
                "old": old_rows,
                "new": new_rows,
            },
            sort_keys=True,
        )
    )
