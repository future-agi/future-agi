"""Authorized Users routes against primary PostgreSQL and UUID-schema CH25.

No manager, membership compiler, or authorization result is mocked. Telemetry
is synthetic and scoped to a fresh project; cache transport remains in-memory.
"""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from conftest import _ch_test_native_client
from tracer.models.entity_tag_assignment import EntityTagAssignment
from tracer.services.clickhouse.v2.entity_tag_targets import TRACE_ASSIGNMENT_HOUR

pytestmark = [pytest.mark.integration, pytest.mark.django_db]
START = datetime(2026, 8, 1, tzinfo=UTC)
END = START + timedelta(days=1)


def window(*leaves):
    return [
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [START.isoformat(), END.isoformat()],
            },
        },
        *leaves,
    ]


def tag_leaf(value="wanted", operation="in", column="tag", family="SYSTEM_METRIC"):
    config = {"filter_type": "text", "filter_op": operation, "filter_value": value}
    if operation in {"in", "not_in"} and not isinstance(value, list):
        config["filter_value"] = [value]
    if family is not None:
        config["col_type"] = family
    return {"column_id": column, "filter_config": config}


@pytest.fixture
def users_store(observe_project, organization):
    """Use the repository-applied schema, not a string-key substitute."""
    from django.conf import settings

    with _ch_test_native_client(
        database=settings.CLICKHOUSE_V2["CH25_DATABASE"]
    ) as client:
        assert client.execute(
            "SELECT type FROM system.columns WHERE database=currentDatabase() "
            "AND table='traces' AND name='id'"
        ) == [("UUID",)]
        project = observe_project.id
        users = {}

        def seed(label, *, sdk=(), manual=None, legacy=(), raw=None, hours=1):
            user_id, trace_id = uuid4(), uuid4()
            users[label] = (user_id, trace_id)
            client.execute(
                "INSERT INTO end_users (project_id,end_user_id,organization_id,user_id,"
                "user_id_type,first_seen) VALUES",
                [
                    (
                        project,
                        user_id,
                        organization.id,
                        label,
                        "string",
                        START + timedelta(hours=hours),
                    )
                ],
            )
            client.execute(
                "INSERT INTO traces (project_id,id,tags,created_at,_version) VALUES",
                [(project, trace_id, json.dumps(list(legacy)), START, 1)],
            )
            client.execute(
                "INSERT INTO spans (project_id,trace_id,id,observation_type,service_name,"
                "start_time,end_time,parent_span_id,name,end_user_id,tags,attrs_string,"
                "total_tokens,_version) VALUES",
                [
                    (
                        project,
                        str(trace_id),
                        "root",
                        "span",
                        "svc",
                        START + timedelta(hours=hours),
                        START + timedelta(hours=hours, seconds=1),
                        "",
                        label,
                        user_id,
                        json.dumps(list(sdk)),
                        raw or {},
                        1,
                        1,
                    )
                ],
            )
            if manual is not None:
                EntityTagAssignment.objects.create(
                    project=observe_project,
                    entity_kind="trace",
                    trace_id=str(trace_id),
                    span_id="",
                    start_hour=TRACE_ASSIGNMENT_HOUR,
                    observation_type="",
                    service_name="",
                    manual_tags=[{"name": name} for name in manual],
                )
            return user_id, trace_id

        try:
            yield SimpleNamespace(
                client=client, seed=seed, users=users, project=project
            )
        finally:
            for table in ("spans", "traces", "end_users"):
                client.execute(
                    f"ALTER TABLE {table} DELETE WHERE project_id=%(project)s SETTINGS mutations_sync=2",
                    {"project": project},
                )


def users_request(auth_client, store, leaves, **options):
    return auth_client.get(
        "/tracer/users/",
        {
            "project_id": str(store.project),
            "filters": json.dumps(window(*leaves)),
            "requested_columns": "[]",
            "cursor_mode": "true",
            "page_size": 10,
            **options,
        },
    )


def graph_request(auth_client, store, leaves):
    return auth_client.post(
        "/tracer/project/get_users_aggregate_graph_data/",
        {
            "project_id": str(store.project),
            "filters": window(*leaves),
            "interval": "day",
            "req_data_config": {"type": "SYSTEM_METRIC", "id": "active_users"},
        },
        format="json",
    )


def test_authorized_manual_only_users_list_matches_actual_graph(
    auth_client, users_store
):
    users_store.seed("manual-only", manual=["wanted"])
    users_store.seed("negative", hours=2)
    leaf = tag_leaf()
    graph = graph_request(auth_client, users_store, [leaf])
    assert graph.status_code == 200, graph.data
    result = graph.data["result"]
    assert result["query_status"] == "complete", result
    assert sum(point["value"] for point in result["data"]) == 1, result
    response = users_request(auth_client, users_store, [leaf])
    assert response.status_code == 200, response.data
    assert {row["user_id"] for row in response.data["result"]["table"]} == {
        "manual-only"
    }
