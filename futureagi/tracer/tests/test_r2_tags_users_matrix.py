"""C2-1 bounded Users tag regressions through authorized, real-store routes.

The small matrix varies public wire shapes, not mocked compiler decisions. All
telemetry is disposable and project-scoped; the only failure injection is a real
PostgreSQL statement cancellation at the assignment-read I/O boundary.
"""

import csv
import io
import json
from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection

from accounts.models.organization import Organization
from tracer.models.entity_tag_assignment import EntityTagAssignment
from tracer.models.project import Project
from tracer.services.clickhouse.v2.entity_tag_targets import TRACE_ASSIGNMENT_HOUR
from tracer.tests.test_r2_tags_users import (
    START,
    graph_request,
    tag_leaf,
    users_request,
    window,
)
from tracer.tests.test_r2_tags_users import (
    users_store as _users_store,
)

pytestmark = [pytest.mark.integration, pytest.mark.django_db]
users_store = _users_store  # Register the parent's real-store fixture.


def _request(client, store, leaves, *, method="get", **options):
    if method == "get":
        return users_request(client, store, leaves, **options)
    return client.post(
        "/tracer/users/",
        {
            "project_id": str(store.project),
            "filters": window(*leaves),
            "requested_columns": [],
            "cursor_mode": True,
            "page_size": 10,
            **options,
        },
        format="json",
    )


def _labels(response):
    assert response.status_code == 200, getattr(response, "data", response)
    result = response.data["result"]
    assert result["query_exact"] is True, result
    return {row["user_id"] for row in result["table"]}


def _assert_graph_membership(client, store, leaves, expected):
    # Partition the independently seeded user universe, rather than relying on
    # one equal total that could hide a false-positive/false-negative swap.
    for labels, count in (
        (set(expected), len(expected)),
        (set(store.users) - set(expected), 0),
    ):
        if not labels:
            continue
        identity = tag_leaf(sorted(labels), column="user_id")
        graph = graph_request(client, store, [*leaves, identity])
        assert graph.status_code == 200, graph.data
        result = graph.data["result"]
        assert result["query_status"] == "complete", result
        assert sum(point["value"] for point in result["data"]) == count, result


def _span(
    store,
    label,
    *,
    sdk=(),
    raw=None,
    hours=1,
    version=2,
    deleted=False,
    span_id="root",
    parent="",
    extra=None,
):
    """Insert another physical version/identity without changing auth fixtures."""
    user_id, trace_id = store.users[label]
    store.client.execute(
        "INSERT INTO spans (project_id,trace_id,id,observation_type,service_name,"
        "start_time,end_time,parent_span_id,name,end_user_id,tags,attrs_string,"
        "attributes_extra,total_tokens,is_deleted,_version) VALUES",
        [
            (
                store.project,
                str(trace_id),
                span_id,
                "span",
                "svc",
                START + timedelta(hours=hours),
                START + timedelta(hours=hours, seconds=1),
                parent,
                label,
                user_id,
                json.dumps(list(sdk)),
                raw or {},
                json.dumps(extra or {}),
                1,
                int(deleted),
                version,
            )
        ],
    )


@pytest.mark.parametrize("method,column", [("get", "tag"), ("post", "tags")])
def test_cursor_continuation_and_csv_preserve_tag_membership(
    auth_client, users_store, method, column
):
    users_store.seed("sdk", sdk=["wanted"], hours=1)
    users_store.seed("manual", manual=["wanted"], hours=2)
    users_store.seed("legacy", legacy=["wanted"], hours=3)
    users_store.seed("raw-only", raw={column: "wanted"}, hours=4)
    leaves = [tag_leaf(column=column)]
    seen, cursors = [], set()
    cursor = None
    for _ in range(6):
        options = {"page_size": 1}
        if cursor:
            options["cursor"] = cursor
        response = _request(auth_client, users_store, leaves, method=method, **options)
        labels = _labels(response)
        assert len(labels) <= 1
        seen.extend(labels)
        cursor = response.data["result"]["next_cursor"]
        if cursor is None:
            break
        assert cursor not in cursors
        cursors.add(cursor)
    else:
        pytest.fail("finite three-user population did not exhaust its cursor")
    assert seen == ["legacy", "manual", "sdk"]
    assert cursors, "a page-size-one request must exercise signed continuation"

    exported = _request(auth_client, users_store, leaves, method=method, export=True)
    assert exported.status_code == 200, getattr(exported, "data", exported)
    assert exported["Content-Type"].startswith("text/csv")
    body = b"".join(exported.streaming_content).decode()
    assert "# export" not in body, body
    assert [row["User ID"] for row in csv.DictReader(io.StringIO(body))] == seen


@pytest.mark.parametrize("column,family", [("tag", None), ("tags", "SYSTEM_METRIC")])
def test_native_sources_empty_assignment_and_null_membership(
    auth_client, users_store, column, family
):
    users_store.seed("sdk", sdk=["wanted"])
    users_store.seed("manual", manual=["wanted"])
    users_store.seed("legacy", legacy=["wanted"])
    users_store.seed("empty-suppresses-legacy", manual=[], legacy=["wanted"])
    users_store.seed("empty-keeps-sdk", sdk=["wanted"], manual=[], legacy=["old"])
    users_store.seed("raw-is-not-native", raw={"tag": "wanted", "tags": "wanted"})
    users_store.seed("sdk-attribute", raw={"tag.tags": '["wanted"]'})
    users_store.seed("sdk-json")
    _span(users_store, "sdk-json", extra={"tag": {"tags": ["wanted"]}})
    expected = {
        "sdk",
        "manual",
        "legacy",
        "empty-keeps-sdk",
        "sdk-attribute",
        "sdk-json",
    }
    leaf = tag_leaf(column=column, family=family)
    assert _labels(users_request(auth_client, users_store, [leaf])) == expected
    _assert_graph_membership(auth_client, users_store, [leaf], expected)
    assert _labels(
        users_request(
            auth_client,
            users_store,
            [tag_leaf(operation="is_null", value=None, column=column, family=family)],
        )
    ) == {"empty-suppresses-legacy", "raw-is-not-native"}


def test_replacement_removal_tombstones_and_latest_source_do_not_resurrect_tags(
    auth_client, users_store
):
    users_store.seed("replacement", manual=["wanted"], legacy=["wanted"])
    users_store.seed("removal", manual=["wanted"], legacy=["wanted"])
    users_store.seed("sdk-survives-removal", sdk=["wanted"], manual=["wanted"])
    users_store.seed("latest-cleared", sdk=["wanted"])
    users_store.seed("latest-added")
    users_store.seed("deleted", sdk=["wanted"], manual=["wanted"])
    users_store.seed("legacy-cleared", legacy=["wanted"])
    for label, tags in [
        ("replacement", ["replacement"]),
        ("removal", []),
        ("sdk-survives-removal", []),
    ]:
        EntityTagAssignment.objects.filter(
            project_id=users_store.project, trace_id=str(users_store.users[label][1])
        ).update(manual_tags=[{"name": name} for name in tags], revision=2)
    _span(users_store, "latest-cleared")
    _span(users_store, "latest-added", sdk=["wanted"])
    _span(users_store, "deleted", sdk=["wanted"], deleted=True)
    users_store.client.execute(
        "INSERT INTO traces (project_id,id,tags,created_at,_version) VALUES",
        [(users_store.project, users_store.users["legacy-cleared"][1], "[]", START, 2)],
    )
    assert _labels(users_request(auth_client, users_store, [tag_leaf()])) == {
        "sdk-survives-removal",
        "latest-added",
    }
    assert _labels(
        users_request(auth_client, users_store, [tag_leaf("replacement")])
    ) == {"replacement"}
    _assert_graph_membership(
        auth_client, users_store, [tag_leaf()], {"sdk-survives-removal", "latest-added"}
    )


@pytest.mark.parametrize("column", ["tag", "tags"])
def test_explicit_attribute_and_native_leaves_keep_distinct_negative_families(
    auth_client, users_store, column
):
    users_store.seed("native-only", manual=["wanted"])
    users_store.seed("attribute-only", raw={column: "wanted"})
    users_store.seed("both", manual=["wanted"], raw={column: "wanted"})
    users_store.seed("native-other-attribute", manual=["wanted"], raw={column: "other"})
    users_store.seed("different-spans", manual=["wanted"], raw={column: "wanted"})
    _span(
        users_store,
        "different-spans",
        span_id="child",
        parent="root",
        raw={column: "other"},
        hours=2,
        version=1,
    )
    attribute = tag_leaf(column=column, family="SPAN_ATTRIBUTE")
    native = tag_leaf(column=column)
    assert _labels(users_request(auth_client, users_store, [attribute])) == {
        "attribute-only",
        "both",
        "different-spans",
    }
    assert _labels(users_request(auth_client, users_store, [native, attribute])) == {
        "both",
        "different-spans",
    }
    # Missing attributes are not a witness for the raw negative; a second
    # non-matching span must not hide an excluded value on another live span.
    raw_negative = tag_leaf(column=column, family="SPAN_ATTRIBUTE", operation="not_in")
    assert _labels(users_request(auth_client, users_store, [native, raw_negative])) == {
        "native-other-attribute"
    }
    # Declared SYSTEM_METRIC remains existential per span: the untagged child
    # is a valid negative witness. Legacy/unqualified leaves retain the graph's
    # whole-user anti-membership, so the tagged root forbids that same user.
    native_negative = tag_leaf(column=column, operation="not_in")
    assert _labels(
        users_request(auth_client, users_store, [attribute, native_negative])
    ) == {"attribute-only", "different-spans"}
    unqualified_negative = tag_leaf(column=column, operation="not_in", family=None)
    assert _labels(
        users_request(auth_client, users_store, [attribute, unqualified_negative])
    ) == {"attribute-only"}
    _assert_graph_membership(
        auth_client,
        users_store,
        [attribute, native_negative],
        {"attribute-only", "different-spans"},
    )
    _assert_graph_membership(
        auth_client, users_store, [attribute, unqualified_negative], {"attribute-only"}
    )


@pytest.mark.parametrize("column", ["tag", "tags"])
def test_property_only_custom_tag_identity_remains_raw(
    auth_client, users_store, column
):
    users_store.seed("raw-only", raw={column: "wanted"})
    users_store.seed("native-only", manual=["wanted"])
    users_store.seed("both", raw={column: "wanted"}, manual=["wanted"])
    raw = tag_leaf(column=column, family=None)
    raw["property_id"] = f"custom_attribute:{column}"
    assert _labels(users_request(auth_client, users_store, [raw])) == {
        "raw-only",
        "both",
    }
    assert _labels(
        users_request(auth_client, users_store, [tag_leaf(column=column), raw])
    ) == {"both"}


def test_tag_membership_obeys_supported_half_open_window(auth_client, users_store):
    users_store.seed("at-start", sdk=["wanted"], hours=0)
    users_store.seed("inside", manual=["wanted"], hours=2)
    users_store.seed("at-end", manual=["wanted"], hours=24)
    users_store.seed("before-start", sdk=["wanted"], hours=-1)
    users_store.seed("outside-match", hours=3)
    _span(
        users_store, "outside-match", span_id="old", sdk=["wanted"], hours=-1, version=1
    )
    assert _labels(users_request(auth_client, users_store, [tag_leaf()])) == {
        "at-start",
        "inside",
    }
    assert _labels(
        users_request(auth_client, users_store, [tag_leaf(operation="not_in")])
    ) == {"outside-match"}


def _copy_project_telemetry(store, target, organization_id):
    """Reuse UUID identities across scopes to catch uncorrelated tag membership."""
    params = {"source": store.project, "target": target, "org": organization_id}
    store.client.execute(
        "INSERT INTO end_users (project_id,end_user_id,organization_id,user_id,"
        "user_id_type,first_seen) SELECT %(target)s,end_user_id,%(org)s,user_id,"
        "user_id_type,first_seen FROM end_users WHERE project_id=%(source)s",
        params,
    )
    store.client.execute(
        "INSERT INTO traces (project_id,id,tags,created_at,_version) "
        "SELECT %(target)s,id,tags,created_at,_version FROM traces WHERE project_id=%(source)s",
        params,
    )
    store.client.execute(
        "INSERT INTO spans (project_id,trace_id,id,observation_type,service_name,"
        "start_time,end_time,parent_span_id,name,end_user_id,tags,attrs_string,"
        "total_tokens,_version) SELECT %(target)s,trace_id,id,observation_type,service_name,"
        "start_time,end_time,parent_span_id,name,end_user_id,tags,attrs_string,"
        "total_tokens,_version FROM spans WHERE project_id=%(source)s",
        params,
    )


def test_project_and_tenant_scope_with_colliding_telemetry_identities(
    auth_client, users_store, observe_project, organization, workspace
):
    users_store.seed("same-identity")
    sibling = Project.objects.create(
        name="Tag matrix sibling",
        organization=organization,
        workspace=workspace,
        trace_type="observe",
        model_type=observe_project.model_type,
    )
    other_org = Organization.objects.create(name="Tag matrix foreign tenant")
    foreign = Project.no_workspace_objects.create(
        name="Tag matrix forbidden",
        organization=other_org,
        trace_type="observe",
        model_type=observe_project.model_type,
    )
    try:
        for project in (sibling, foreign):
            _copy_project_telemetry(users_store, project.id, project.organization_id)
            EntityTagAssignment.objects.create(
                project=project,
                entity_kind="trace",
                trace_id=str(users_store.users["same-identity"][1]),
                span_id="",
                start_hour=TRACE_ASSIGNMENT_HOUR,
                observation_type="",
                service_name="",
                manual_tags=[
                    {"name": "wanted" if project == sibling else "foreign-only"}
                ],
            )
        assert _labels(users_request(auth_client, users_store, [tag_leaf()])) == set()
        response = users_request(auth_client, users_store, [tag_leaf()], project_id="")
        assert _labels(response) == {"same-identity"}
        assert {str(row["project_id"]) for row in response.data["result"]["table"]} == {
            str(sibling.id)
        }
        assert (
            _labels(
                users_request(
                    auth_client, users_store, [tag_leaf("foreign-only")], project_id=""
                )
            )
            == set()
        )
        assert (
            _labels(
                users_request(
                    auth_client,
                    users_store,
                    [tag_leaf("foreign-only")],
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


@pytest.mark.parametrize("method", ["get", "post"])
def test_unsupported_tag_operation_is_sanitized_400(auth_client, users_store, method):
    users_store.seed("live", manual=["wanted"])
    response = _request(
        auth_client, users_store, [tag_leaf(operation="starts_with")], method=method
    )
    assert response.status_code == 400, response.data
    assert response.data["code"] == "invalid", response.data
    assert "User filter configuration is invalid" in str(response.data)


@pytest.mark.parametrize("export", [False, True])
def test_actual_primary_assignment_timeout_is_503_before_list_or_csv(
    auth_client, users_store, export
):
    users_store.seed("manual", manual=["wanted"])
    cancellations = []

    def timeout_assignment_select(execute, sql, params, many, context):
        if (
            sql.lstrip().upper().startswith("SELECT")
            and 'FROM "tracer_entity_tag_assignment"' in sql
        ):
            assert context["connection"].alias == "default"
            try:
                # Exceed Users' own production statement ceiling. The read
                # policy recalculates it immediately before this real SQL.
                return execute("SELECT pg_sleep(120)", None, False, context)
            except DatabaseError as exc:
                cancellations.append(exc.__cause__.sqlstate)
                raise
        return execute(sql, params, many, context)

    with connection.execute_wrapper(timeout_assignment_select):
        response = _request(
            auth_client, users_store, [tag_leaf()], method="post", export=export
        )
    assert cancellations == ["57014"]
    assert response.status_code == 503, response.data
    assert response.data["code"] == "service_unavailable", response.data
    assert not response.streaming
    assert "pg_sleep" not in response.content.decode()


def test_2049_live_manual_assignments_are_permanent_budget_400(
    auth_client, users_store
):
    from tracer.services.clickhouse.v2.effective_tags import MAX_TAG_OVERLAY_KEYS

    assert MAX_TAG_OVERLAY_KEYS == 2048
    user_id, _ = users_store.seed("live")
    trace_ids = [uuid4() for _ in range(MAX_TAG_OVERLAY_KEYS + 1)]
    users_store.client.execute(
        "INSERT INTO spans (project_id,trace_id,id,observation_type,service_name,"
        "start_time,end_time,parent_span_id,name,end_user_id,tags,_version) VALUES",
        [
            (
                users_store.project,
                str(trace),
                "root",
                "span",
                "svc",
                START + timedelta(hours=1),
                START + timedelta(hours=1, seconds=1),
                "",
                "live",
                user_id,
                "[]",
                1,
            )
            for trace in trace_ids
        ],
    )
    EntityTagAssignment.objects.bulk_create(
        [
            EntityTagAssignment(
                project_id=users_store.project,
                entity_kind="trace",
                trace_id=str(trace),
                span_id="",
                start_hour=TRACE_ASSIGNMENT_HOUR,
                observation_type="",
                service_name="",
                manual_tags=[{"name": "wanted"}],
            )
            for trace in trace_ids
        ]
    )
    response = users_request(auth_client, users_store, [tag_leaf()])
    assert response.status_code == 400, response.data
    assert response.data["code"] == "tag_filter_too_broad", response.data
    assert not response.streaming


@pytest.mark.parametrize("method", ["get", "post"])
def test_numbered_native_tag_requests_retain_explicit_cursor_requirement(
    auth_client, users_store, method
):
    users_store.seed("manual", manual=["wanted"])
    response = _request(
        auth_client, users_store, [tag_leaf()], method=method, cursor_mode=False
    )
    assert response.status_code == 422, response.data
    assert response.data["code"] == "user_filter_requires_cursor", response.data


@pytest.mark.parametrize("export", [False, True])
def test_cursor_and_export_do_not_silently_apply_unsupported_sort(
    auth_client, users_store, export
):
    users_store.seed("manual", manual=["wanted"])
    response = _request(
        auth_client,
        users_store,
        [tag_leaf()],
        method="post",
        export=export,
        sort_params=[{"column_id": "total_tokens", "direction": "desc"}],
    )
    assert response.status_code == 422, response.data
    assert response.data["code"] == "cursor_sort_unsupported", response.data


def test_actual_users_driver_render_reuses_shared_tag_literals(
    auth_client, users_store, monkeypatch
):
    from clickhouse_driver import Client

    from tracer.services.clickhouse.v2 import effective_tags

    name = "needle'\\雪"
    users_store.seed("escaped", sdk=[name])
    original = Client.substitute_params
    rendered = []

    def observe_render(self, query, params, context):
        sql = original(self, query, params, context)
        if "AS native_leaf_1" in query and "__effective_tag_bind_" in query:
            rendered.append(sql)
        return sql

    monkeypatch.setattr(Client, "substitute_params", observe_render)
    assert _labels(
        users_request(
            auth_client, users_store, [tag_leaf(name), tag_leaf(name, column="tags")]
        )
    ) == {"escaped"}
    assert rendered, (
        "the production executor must prepare and driver-render the native decision"
    )
    literal = effective_tags._collection_literal([name])
    for sql in rendered:
        assert sql.count(literal) == 1
        assert len(sql.encode("utf-8")) < effective_tags.MAX_TAG_QUERY_BYTES
