"""Write-contract regressions, including CH-only targets and role admission."""

from contextlib import nullcontext
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from clickhouse_driver.errors import NetworkError
from django.db import OperationalError
from rest_framework.parsers import JSONParser
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from tracer.models.observation_span import ObservationSpan
from tracer.models.trace import Trace
from tracer.serializers.entity_tags import (
    EntityTagsResponseSerializer,
    SpanTagsUpdateSerializer,
)
from tracer.services import entity_tag_assignments as assignments
from tracer.services.entity_tags import TagReadUnavailable
from tracer.tests.test_entity_tag_target import PROJECT, TRACE, reader, reference
from tracer.views.observation_span import ObservationSpanView
from tracer.views.trace import TraceTagsUpdateSerializer, TraceView


@pytest.fixture(autouse=True, scope="session")
def _drop_legacy_ch_spans_mvs():
    yield


@pytest.fixture(autouse=True, scope="session")
def _ensure_test_score_tenant_column():
    yield


def payload(**changes):
    return {
        **{
            k: v.isoformat() if isinstance(v, datetime) else v
            for k, v in reference().items()
        },
        "tags": [{"name": "ui", "color": "#123456"}],
        "expected_tag_revision": 0,
        **changes,
    }


def make_request(*, span=False, body=None):
    factory = APIRequestFactory()
    raw = (factory.post if span else factory.patch)(
        "/", payload() if body is None else body, format="json"
    )
    request = Request(raw, parsers=[JSONParser()])
    request.organization = SimpleNamespace(id="org")
    request.workspace = SimpleNamespace(
        id="ws", organization_id="org", is_default=False
    )
    request._user = SimpleNamespace(
        is_authenticated=True, organization=request.organization
    )
    return request


def invoke(request, *, span=False, trace=TRACE):
    view = ObservationSpanView() if span else TraceView()
    view.request = request
    view.kwargs = {} if span else {"pk": trace}
    # Either old telemetry lookup is a regression, even for a hybrid row.
    view.get_queryset = Mock(side_effect=AssertionError("No legacy Trace query"))
    with (
        patch.object(
            ObservationSpan.objects,
            "get",
            side_effect=AssertionError("No legacy span query"),
        ),
        patch.object(
            Trace.objects, "get", side_effect=AssertionError("No legacy Trace query")
        ),
    ):
        return view.update_tags(request, **view.kwargs)


@pytest.fixture
def write_dependencies(monkeypatch):
    backend = reader()
    authorize = Mock(return_value=SimpleNamespace(id=PROJECT))
    save = Mock(return_value=1)
    monkeypatch.setattr(assignments, "_postgres_budget", lambda deadline: nullcontext())
    monkeypatch.setattr(assignments, "_authorize_project", authorize)
    monkeypatch.setattr(assignments, "V2AnalyticsQueryService", lambda: backend)
    monkeypatch.setattr(assignments, "_save_assignment", save)
    return SimpleNamespace(backend=backend, authorize=authorize, save=save)


@pytest.mark.parametrize(
    "tags",
    [
        [],
        ["ui"],
        [{"name": "ui", "color": "#123456"}],
        ["ui", {"name": "ui", "color": "#123456"}],
    ],
)
@pytest.mark.parametrize(
    "serializer_class", [TraceTagsUpdateSerializer, SpanTagsUpdateSerializer]
)
def test_string_rich_and_clear_payloads(serializer_class, tags):
    serializer = serializer_class(data=payload(tags=tags))
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["tags"] == (
        []
        if not tags
        else [
            {
                "name": "ui",
                **(
                    {"color": "#123456"}
                    if any(isinstance(t, dict) for t in tags)
                    else {}
                ),
            }
        ]
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"tags": None},
        {"tags": [12]},
        {"tags": [{"name": "x", "color": "red"}]},
        {"tags": [{"name": "x", "extra": 1}]},
        {"tags": ["x"] * 101},
        {"tags": ["x" * 257]},
        {"tags": ["\n"]},
        {
            "tags": [
                {"name": "x", "color": "#123456"},
                {"name": "x", "color": "#654321"},
            ]
        },
        {"project_id": "bad"},
        {"span_id": 12},
        {"trace_id": ""},
        {"service_name": "s" * 256},
        {"unexpected": 1},
        {"expected_tag_revision": -1},
        {"expected_tag_revision": True},
        {"expected_tag_revision": "0"},
        {"expected_tag_revision": 1.5},
        {"expected_version": 2},
        {"expected_version": "02"},
        {"expected_version": str(2**64)},
        {"start_hour": "2020-01-01T00:01:00Z"},
        {"start_hour": "2020-01-01T00:00:00"},
        {"expected_start_time": "2020-01-01T01:00:00Z"},
        {"expected_start_time": "2020-01-01T00:00:00.1234567Z"},
    ],
)
def test_invalid_scope_or_tags_rejected(changes):
    serializer = TraceTagsUpdateSerializer(data=payload(**changes))
    assert not serializer.is_valid()


@pytest.mark.parametrize("span", [False, True])
def test_ch_only_action_accepts_rich_payload_and_returns_effective_tags(
    write_dependencies, span
):
    response = invoke(make_request(span=span), span=span)
    assert response.status_code == 200, response.data
    assert response.data == {
        "status": True,
        "result": {
            "id": "0123456789abcdef" if span else TRACE,
            "tags": [{"name": "sdk"}, {"name": "ui", "color": "#123456"}],
            "manual_tags": [{"name": "ui", "color": "#123456"}],
            "source_tag_names": ["sdk"],
            "tag_revision": 1,
        },
    }
    serializer = EntityTagsResponseSerializer(data=response.data)
    assert serializer.is_valid(), serializer.errors
    write_dependencies.authorize.assert_called_once()
    target, data, _ = write_dependencies.save.call_args.args
    assert target.key[1] == "trace"
    assert data["tags"] == [{"name": "ui", "color": "#123456"}]
    sql = write_dependencies.backend.execute_ch_query.call_args.args[0]
    assert sql.strip().startswith("SELECT") and "PREWHERE project_id" in sql


@pytest.mark.parametrize("span", [False, True])
@pytest.mark.parametrize(
    "missing",
    [
        "project_id",
        "start_hour",
        "expected_version",
        "expected_tag_revision",
        "service_name",
    ],
)
def test_missing_scope_is_actionable_and_does_not_read(
    write_dependencies, span, missing
):
    body = payload()
    del body[missing]
    response = invoke(make_request(span=span, body=body), span=span)
    assert response.status_code == 400
    assert response.data["code"] == "tag_scope_required"
    write_dependencies.authorize.assert_not_called()
    write_dependencies.backend.execute_ch_query.assert_not_called()
    write_dependencies.save.assert_not_called()


@pytest.mark.parametrize("span", [False, True])
@pytest.mark.parametrize(
    "failure,status,code",
    [
        ("missing", 404, "tag_target_not_found"),
        ("changed", 409, "tag_target_changed"),
        ("unavailable", 503, "tag_target_unavailable"),
        ("incomplete", 503, "tag_target_unavailable"),
        ("revision", 409, "tag_revision_conflict"),
        ("postgres", 503, "tag_assignment_unavailable"),
        ("source", 503, "tag_assignment_unavailable"),
    ],
)
def test_failure_statuses_are_distinct_and_fail_closed(
    write_dependencies, monkeypatch, span, failure, status, code
):
    deps = write_dependencies
    if failure == "missing":
        deps.backend.execute_ch_query.return_value.data = []
    elif failure == "changed":
        deps.backend.execute_ch_query.return_value.data[0]["_version"] = "3"
    elif failure == "unavailable":
        deps.backend.execute_ch_query.side_effect = RuntimeError(
            "private-driver-diagnostic"
        )
    elif failure == "incomplete":
        deps.backend.execute_ch_query.return_value.complete = False
    elif failure == "revision":
        deps.save.side_effect = assignments.TagAssignmentError("tag_revision_conflict")
    elif failure == "postgres":
        deps.save.side_effect = OperationalError("private-driver-diagnostic")
    else:
        monkeypatch.setattr(
            assignments,
            "effective_tags",
            Mock(side_effect=TagReadUnavailable("private-source")),
        )
    response = invoke(make_request(span=span), span=span)
    assert response.status_code == status, response.data
    assert response.data["code"] == code
    assert "private-" not in str(response.data)
    if failure not in ("revision", "postgres"):
        deps.save.assert_not_called()


@pytest.mark.parametrize("span", [False, True])
@pytest.mark.parametrize(
    "error",
    [NetworkError("private-driver-diagnostic"), ValueError("private-config")],
    ids=["unreachable", "invalid-config"],
)
def test_clickhouse_client_unavailable_is_503_not_a_lookup(
    write_dependencies, monkeypatch, span, error
):
    def unavailable():
        raise error

    monkeypatch.setattr(assignments, "V2AnalyticsQueryService", unavailable)
    response = invoke(make_request(span=span), span=span)
    assert response.status_code == 503, response.data
    assert response.data["code"] == "tag_target_unavailable"
    assert "private-" not in str(response.data)
    write_dependencies.save.assert_not_called()


def test_trace_rejects_child_and_url_mismatch(write_dependencies):
    deps = write_dependencies
    deps.backend.execute_ch_query.return_value.data[0]["parent_span_id"] = "parent"
    assert invoke(make_request()).status_code == 409
    deps.save.assert_not_called()
    deps.backend.execute_ch_query.reset_mock()
    assert invoke(make_request(), trace="different-trace").status_code == 400
    deps.backend.execute_ch_query.assert_not_called()
    assert invoke(make_request(span=True), span=True).status_code == 200
    assert deps.save.call_args.args[0].key[1] == "span"


@pytest.fixture
def auth_dependencies(monkeypatch):
    membership = Mock(return_value=SimpleNamespace(organization_id="org"))
    can_write = Mock(return_value=True)
    projects = Mock()
    projects.using.return_value = projects
    projects.only.return_value = projects
    projects.get.return_value = SimpleNamespace(id=PROJECT)
    monkeypatch.setattr(assignments, "get_org_membership", membership)
    monkeypatch.setattr(assignments, "_can_write_to_workspace", can_write)
    monkeypatch.setattr(assignments.Project, "no_workspace_objects", projects)
    return SimpleNamespace(
        membership=membership, can_write=can_write, projects=projects
    )


@pytest.mark.parametrize(
    "failure",
    [
        "missing_org_membership",
        "foreign_org_membership",
        "viewer",
        "missing_workspace",
        "foreign_workspace",
        "no_workspace_access",
        "foreign_project",
    ],
)
def test_unauthorized_scope_cannot_reach_clickhouse(
    auth_dependencies, monkeypatch, failure
):
    deps = auth_dependencies
    request = make_request()
    if failure == "missing_org_membership":
        deps.membership.return_value = None
    elif failure == "foreign_org_membership":
        deps.membership.return_value.organization_id = "other"
    elif failure == "viewer":
        deps.can_write.return_value = False
    elif failure == "missing_workspace":
        request.workspace = None
    elif failure == "foreign_workspace":
        request.workspace.organization_id = "other"
    elif failure == "no_workspace_access":
        deps.can_write.return_value = False
    else:
        deps.projects.get.side_effect = assignments.Project.DoesNotExist
    backend = Mock()
    monkeypatch.setattr(assignments, "_postgres_budget", lambda deadline: nullcontext())
    monkeypatch.setattr(assignments, "V2AnalyticsQueryService", backend)
    response = invoke(request)
    assert response.status_code == (404 if failure == "foreign_project" else 403)
    backend.assert_not_called()


@pytest.mark.parametrize("default", [False, True])
def test_project_lookup_is_explicit_primary_org_and_workspace(
    auth_dependencies, default
):
    request = make_request()
    request.workspace.is_default = default
    assert assignments._authorize_project(request, PROJECT).id == PROJECT
    deps = auth_dependencies
    deps.projects.using.assert_called_once_with("default")
    scope = deps.projects.get.call_args.args[0]
    assert "workspace_id" in str(scope) and "ws" in str(scope)
    assert ("workspace__isnull" in str(scope)) is default
    assert deps.projects.get.call_args.kwargs == {
        "id": PROJECT,
        "organization_id": "org",
        "deleted": False,
    }
