"""Actual authorized Users routes with bounded datetime exclusions and Tag sources."""

import csv
import io
from datetime import timedelta

import pytest

from tracer.tests.test_r2_tags_users import (
    START,
    graph_request,
    tag_leaf,
    users_request,
    window,
)
from tracer.tests.test_r2_tags_users import users_store as _users_store
from tracer.tests.test_r2_tags_users_matrix import _span
from tracer.tests.test_users_datetime_complements import date_leaf

pytestmark = [pytest.mark.integration, pytest.mark.django_db]
users_store = _users_store


def labels(response):
    assert response.status_code == 200, response.data
    result = response.data["result"]
    assert result["query_exact"] is True, result
    return {row["user_id"] for row in result["table"]}


def assert_routes(client, store, leaves, expected):
    assert labels(users_request(client, store, leaves)) == set(expected)
    graph = graph_request(client, store, leaves)
    assert graph.status_code == 200, graph.data
    result = graph.data["result"]
    assert result["query_status"] == "complete", result
    assert sum(point["value"] for point in result["data"]) == len(expected), result


@pytest.mark.parametrize("source", ["sdk", "manual", "legacy", "raw"])
@pytest.mark.parametrize("route", ["list", "graph"])
def test_recorded_route_contrast_and_source_controls(
    auth_client, users_store, source, route
):
    data = {source: ["wanted"]} if source != "raw" else {"raw": {"tag": "wanted"}}
    users_store.seed("at-start", hours=0, **data)
    users_store.seed("inside", hours=2, **data)
    leaf = tag_leaf(family="SPAN_ATTRIBUTE" if source == "raw" else "SYSTEM_METRIC")
    exclusion = date_leaf("not_equals", (START + timedelta(hours=2)).isoformat())
    for leaves, expected in [
        ([leaf], {"at-start", "inside"}),
        ([leaf, exclusion], {"at-start"}),
    ]:
        if route == "list":
            assert labels(users_request(auth_client, users_store, leaves)) == expected
        else:
            graph = graph_request(auth_client, users_store, leaves)
            assert graph.status_code == 200, graph.data
            result = graph.data["result"]
            assert result["query_status"] == "complete", result
            assert sum(point["value"] for point in result["data"]) == len(expected), (
                result
            )


@pytest.mark.parametrize("source", ["sdk", "raw"])
def test_excluded_witness_does_not_qualify_other_allowed_activity(
    auth_client, users_store, source
):
    data = {"sdk": ["wanted"]} if source == "sdk" else {"raw": {"tag": "wanted"}}
    users_store.seed("excluded-witness", hours=2, **data)
    _span(users_store, "excluded-witness", hours=1, span_id="allowed", parent="root")
    users_store.seed("allowed-witness", hours=1, **data)
    _span(
        users_store,
        "allowed-witness",
        hours=2,
        span_id="excluded",
        parent="root",
        **data,
    )
    leaf = tag_leaf(family="SPAN_ATTRIBUTE" if source == "raw" else "SYSTEM_METRIC")
    exclusion = date_leaf("not_equals", (START + timedelta(hours=2)).isoformat())
    assert_routes(auth_client, users_store, [leaf, exclusion], ["allowed-witness"])


@pytest.mark.parametrize(
    "operation,value,expected",
    [
        ("not_equals", "2026-08-01T04:00:00+02:00", ["micro-after", "later"]),
        ("not_between", ["2026-08-01T02:00:00Z", "2026-08-01T03:00:00Z"], ["later"]),
        (
            "not_between",
            ["2026-07-01T00:00:00Z", "2026-07-02T00:00:00Z"],
            ["point", "micro-after", "later"],
        ),
        ("is_null", None, []),
        ("is_not_null", None, ["point", "micro-after", "later"]),
        ("not_between", ["2026-08-01T00:00:00Z", "2026-08-02T00:00:00Z"], []),
    ],
)
def test_canonical_boundaries_and_empty_contract(
    auth_client, users_store, operation, value, expected
):
    users_store.seed("point", sdk=["wanted"], hours=2)
    users_store.seed("micro-after", sdk=["wanted"], hours=2 + 1 / 3_600_000_000)
    users_store.seed("later", sdk=["wanted"], hours=3)
    assert_routes(
        auth_client, users_store, [tag_leaf(), date_leaf(operation, value)], expected
    )


def test_adjacent_overlapping_exclusions_cursor_and_csv(auth_client, users_store):
    for hour in range(5):
        users_store.seed(f"user-{hour}", manual=["wanted"], hours=hour)
    leaves = [
        tag_leaf(),
        date_leaf(
            "not_between",
            [
                (START + timedelta(hours=1)).isoformat(),
                (START + timedelta(hours=2)).isoformat(),
            ],
        ),
        date_leaf(
            "not_between",
            [
                (START + timedelta(hours=2)).isoformat(),
                (START + timedelta(hours=3)).isoformat(),
            ],
        ),
        date_leaf(
            "not_between",
            [
                (START + timedelta(hours=1, minutes=30)).isoformat(),
                (START + timedelta(hours=2, minutes=30)).isoformat(),
            ],
        ),
    ]
    seen, cursors = [], set()
    cursor = None
    for _ in range(6):
        options = {"page_size": 1}
        if cursor:
            options["cursor"] = cursor
        response = users_request(auth_client, users_store, leaves, **options)
        seen.extend(labels(response))
        cursor = response.data["result"]["next_cursor"]
        if cursor is None:
            break
        assert cursor not in cursors
        cursors.add(cursor)
    else:
        pytest.fail("cursor did not exhaust bounded population")
    assert seen == ["user-4", "user-3", "user-0"]
    assert cursors
    exported = users_request(auth_client, users_store, leaves, export=True)
    assert exported.status_code == 200
    body = b"".join(exported.streaming_content).decode()
    assert "# export" not in body
    assert [row["User ID"] for row in csv.DictReader(io.StringIO(body))] == seen


def test_latest_timestamp_corrections_and_tombstones(auth_client, users_store):
    users_store.seed("moved-in", sdk=["wanted"], hours=2.25)
    _span(users_store, "moved-in", sdk=["wanted"], hours=2.5)
    users_store.seed("moved-out", sdk=["wanted"], hours=2.5)
    _span(users_store, "moved-out", sdk=["wanted"], hours=2.25)
    users_store.seed("deleted", sdk=["wanted"], hours=2.25)
    _span(users_store, "deleted", sdk=["wanted"], hours=2.5, deleted=True)
    users_store.seed("tag-cleared", sdk=["wanted"], hours=2.25)
    _span(users_store, "tag-cleared", hours=2.25)
    users_store.seed("reassigned", sdk=["wanted"], hours=2.25)
    users_store.seed("recipient", hours=3)
    # Keep the physical span key, but change its winning user and timestamp.
    _, trace_id = users_store.users["reassigned"]
    recipient_id, _ = users_store.users["recipient"]
    users_store.users["reassigned"] = (recipient_id, trace_id)
    _span(users_store, "reassigned", sdk=["wanted"], hours=2.5)
    exclusion = date_leaf("not_equals", (START + timedelta(hours=2.5)).isoformat())
    assert_routes(auth_client, users_store, [tag_leaf(), exclusion], ["moved-out"])


@pytest.mark.parametrize(
    "family,column", [("SYSTEM_METRIC", "tag"), ("SPAN_ATTRIBUTE", "tags")]
)
def test_exclusion_removes_contributions_not_the_entire_user(
    auth_client, users_store, family, column
):
    data = (
        {"sdk": ["wanted"]}
        if family == "SYSTEM_METRIC"
        else {"raw": {"tags": "wanted"}}
    )
    users_store.seed("mixed-activity", hours=1, **data)
    _span(
        users_store,
        "mixed-activity",
        hours=2,
        span_id="excluded",
        parent="root",
        **data,
    )
    _span(
        users_store, "mixed-activity", hours=3, span_id="allowed", parent="root", **data
    )
    leaves = [
        tag_leaf(family=family, column=column),
        date_leaf("not_equals", (START + timedelta(hours=2)).isoformat()),
    ]
    response = users_request(
        auth_client, users_store, leaves, requested_columns='["total_tokens"]'
    )
    assert labels(response) == {"mixed-activity"}
    assert response.data["result"]["table"][0]["total_tokens"] == 2
    graph = auth_client.post(
        "/tracer/project/get_users_aggregate_graph_data/",
        {
            "project_id": str(users_store.project),
            "filters": window(*leaves),
            "interval": "day",
            "req_data_config": {"type": "SYSTEM_METRIC", "id": "total_tokens"},
        },
        format="json",
    )
    assert graph.status_code == 200, graph.data
    result = graph.data["result"]
    assert result["query_status"] == "complete", result
    assert sum(point["value"] for point in result["data"]) == 2, result
    assert result["query_count"] == 1, result


def test_positive_intersection_remains_conjunctive_with_exclusion(
    auth_client, users_store
):
    for hour in range(4):
        users_store.seed(f"user-{hour}", sdk=["wanted"], hours=hour)
    leaves = [
        tag_leaf(),
        date_leaf(
            "between",
            [
                (START + timedelta(hours=1)).isoformat(),
                (START + timedelta(hours=3)).isoformat(),
            ],
        ),
        date_leaf("not_equals", (START + timedelta(hours=2)).isoformat()),
    ]
    assert_routes(auth_client, users_store, leaves, ["user-1"])


def test_invalid_exclusion_cannot_be_a_successful_unfiltered_route(
    auth_client, users_store
):
    users_store.seed("wanted", sdk=["wanted"])
    leaves = [tag_leaf(), date_leaf("not_equals", "invalid-timestamp")]
    assert users_request(auth_client, users_store, leaves).status_code == 400
    graph = graph_request(auth_client, users_store, leaves)
    assert graph.status_code == 400
    assert graph.data["detail"] == "User filter configuration is invalid"


def test_complement_preserves_colliding_tenant_and_project_scope(
    auth_client, users_store, observe_project, organization, workspace
):
    from tracer.tests.test_r2_tags_users_matrix import (
        TRACE_ASSIGNMENT_HOUR,
        EntityTagAssignment,
        Organization,
        Project,
        _copy_project_telemetry,
    )

    users_store.seed("collision", hours=1)
    sibling = Project.objects.create(
        name="Datetime sibling",
        organization=organization,
        workspace=workspace,
        trace_type="observe",
        model_type=observe_project.model_type,
    )
    foreign_org = Organization.objects.create(name="Datetime foreign")
    foreign = Project.no_workspace_objects.create(
        name="Datetime forbidden",
        organization=foreign_org,
        trace_type="observe",
        model_type=observe_project.model_type,
    )
    exclusion = date_leaf("not_equals", (START + timedelta(hours=2)).isoformat())
    try:
        for project, tag in [(sibling, "wanted"), (foreign, "foreign-only")]:
            _copy_project_telemetry(users_store, project.id, project.organization_id)
            EntityTagAssignment.objects.create(
                project=project,
                entity_kind="trace",
                trace_id=str(users_store.users["collision"][1]),
                span_id="",
                start_hour=TRACE_ASSIGNMENT_HOUR,
                observation_type="",
                service_name="",
                manual_tags=[{"name": tag}],
            )
        assert (
            labels(users_request(auth_client, users_store, [tag_leaf(), exclusion]))
            == set()
        )
        response = users_request(
            auth_client, users_store, [tag_leaf(), exclusion], project_id=""
        )
        assert labels(response) == {"collision"}
        assert {str(row["project_id"]) for row in response.data["result"]["table"]} == {
            str(sibling.id)
        }
        assert (
            labels(
                users_request(
                    auth_client,
                    users_store,
                    [tag_leaf("foreign-only"), exclusion],
                    project_id="",
                )
            )
            == set()
        )
        assert (
            labels(
                users_request(
                    auth_client,
                    users_store,
                    [tag_leaf("foreign-only"), exclusion],
                    project_id=str(foreign.id),
                )
            )
            == set()
        )
    finally:
        for project in (sibling, foreign):
            for table in ("spans", "traces", "end_users"):
                users_store.client.execute(
                    f"ALTER TABLE {table} DELETE WHERE project_id=%(project)s SETTINGS mutations_sync=2",
                    {"project": project.id},
                )
