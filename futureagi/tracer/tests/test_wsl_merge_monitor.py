"""N14: exercise monitor defenses directly, without auth masking a query leak."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.http import QueryDict
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models.organization import Organization
from accounts.tests.test_workspace_landing import landing as _landing_fixture
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
    workspace_context,
)
from tracer.models.monitor import UserAlertMonitor, UserAlertMonitorLog
from tracer.models.project import Project
from tracer.views.monitor import UserAlertMonitorLogView, UserAlertMonitorView

landing = _landing_fixture


@pytest.fixture(autouse=True)
def clean_context():
    clear_workspace_context()
    yield
    clear_workspace_context()


@pytest.fixture
def records(landing):
    foreign = Organization.objects.create(name="Foreign monitor owner")
    result = []
    for name, org, workspace in (
        ("named", landing.org, landing.other),
        ("default", landing.org, landing.default),
        ("legacy", landing.org, None),
        ("foreign", foreign, None),
    ):
        project = Project.objects.create(
            name=name,
            organization=org,
            workspace=workspace,
            model_type="GenerativeLLM",
            trace_type="observe",
        )
        monitor = UserAlertMonitor.objects.create(
            name=name,
            organization=org,
            workspace=workspace,
            project=project,
            metric_type="count_of_errors",
            threshold_operator="greater_than",
            threshold_type="static",
            critical_threshold_value=0.1,
            alert_frequency=60,
        )
        log = UserAlertMonitorLog.objects.create(
            alert=monitor, type="critical", message=name
        )
        result.append((project, monitor, log))
    return result


def view_for(cls, request, action="list"):
    view = cls()
    view.request = request
    view.kwargs = {}
    view.format_kwarg = None
    view.action = action
    return view


@pytest.mark.parametrize("selection", ["empty", "named", "default"])
def test_monitor_and_log_direct_selectors_keep_empty_scope(landing, records, selection):
    projects = Project.no_workspace_objects.filter(pk__in=[r[0].pk for r in records])
    monitors = UserAlertMonitor.no_workspace_objects.filter(
        pk__in=[r[1].pk for r in records]
    )
    logs = UserAlertMonitorLog.no_workspace_objects.filter(
        pk__in=[r[2].pk for r in records]
    )
    selected = {"empty": None, "named": landing.other, "default": landing.default}[
        selection
    ]
    expected = {"empty": [], "named": [0], "default": [1, 2]}[selection]
    request = SimpleNamespace(
        user=landing.user,
        organization=landing.org,
        workspace=selected,
        query_params=QueryDict(),
    )
    monitor_view = view_for(UserAlertMonitorView, request)
    log_view = view_for(UserAlertMonitorLogView, request)
    with workspace_context(selected, landing.org, landing.user):
        set_workspace_context(require_workspace=True)
        assert set(
            projects.filter(
                monitor_view._workspace_scope_q(), organization=landing.org
            ).values_list("pk", flat=True)
        ) == {records[i][0].pk for i in expected}
        assert set(
            monitors.filter(
                log_view._alert_workspace_scope_q(), organization=landing.org
            ).values_list("pk", flat=True)
        ) == {records[i][1].pk for i in expected}
        assert set(
            logs.filter(
                log_view._workspace_scope_q(), alert__organization=landing.org
            ).values_list("pk", flat=True)
        ) == {records[i][2].pk for i in expected}
        assert set(
            monitor_view._visible_observe_projects().values_list("pk", flat=True)
        ) == {records[i][0].pk for i in expected}
        assert set(log_view._visible_alert_queryset().values_list("pk", flat=True)) == {
            records[i][1].pk for i in expected
        }
        assert set(log_view.get_queryset().values_list("pk", flat=True)) == {
            records[i][2].pk for i in expected
        }
        page, count = monitor_view.get_queryset()
        assert count == len(expected)
        assert {row.pk for row in page} == {records[i][1].pk for i in expected}
        log_view.action = "create"
        choices = log_view.get_serializer().fields["alert"].queryset
        assert set(choices.values_list("pk", flat=True)) == {
            records[i][1].pk for i in expected
        }


def test_empty_log_routes_refuse_real_and_unknown_ids_without_mutation(
    landing, records
):
    factory = APIRequestFactory()
    replies = []
    with workspace_context(None, landing.org, landing.user):
        set_workspace_context(require_workspace=True)
        for log_id in (records[0][2].pk, uuid4()):
            request = factory.post(
                "/tracer/monitor-logs/resolve/",
                {"log_ids": [str(log_id)]},
                format="json",
            )
            request.organization, request.workspace = landing.org, None
            force_authenticate(request, user=landing.user)
            response = UserAlertMonitorLogView.as_view({"post": "mark_as_resolved"})(
                request
            )
            assert response.status_code == 400, response.data
            replies.append(response.data)
        assert replies[0] == replies[1]
        request = factory.get("/tracer/monitor-logs/all/")
        request.organization, request.workspace = landing.org, None
        force_authenticate(request, user=landing.user)
        response = UserAlertMonitorLogView.as_view({"get": "list_all"})(request)
        assert response.status_code == 200, response.data
        assert response.data["result"] == []
        assert not UserAlertMonitorLog._base_manager.filter(
            pk__in=[r[2].pk for r in records], resolved=True
        ).exists()


@pytest.mark.parametrize("selection", ["named", "default"])
def test_accessible_log_resolution_mutates_only_visible_rows(
    landing, records, selection
):
    selected = landing.other if selection == "named" else landing.default
    indices = [0] if selection == "named" else [1, 2]
    ids = [r[2].pk for r in records]
    factory = APIRequestFactory()
    with workspace_context(selected, landing.org, landing.user):
        set_workspace_context(require_workspace=True)
        request = factory.get("/tracer/monitor-logs/all/")
        request.organization, request.workspace = landing.org, selected
        force_authenticate(request, user=landing.user)
        response = UserAlertMonitorLogView.as_view({"get": "list_all"})(request)
        assert response.status_code == 200, response.data
        assert {str(row["id"]) for row in response.data["result"]} == {
            str(ids[i]) for i in indices
        }
        request = factory.post(
            "/tracer/monitor-logs/resolve/",
            {"log_ids": [str(pk) for pk in ids]},
            format="json",
        )
        request.organization, request.workspace = landing.org, selected
        force_authenticate(request, user=landing.user)
        response = UserAlertMonitorLogView.as_view({"post": "mark_as_resolved"})(
            request
        )
        assert response.status_code == 200, response.data
    for i, pk in enumerate(ids):
        row = UserAlertMonitorLog._base_manager.get(pk=pk)
        assert row.resolved is (i in indices)
        assert row.resolved_by_id == (landing.user.pk if i in indices else None)
        assert (row.resolved_at is not None) is (i in indices)


def test_explicit_none_org_is_not_replaced_by_ambient_monitor_org(landing, records):
    request = SimpleNamespace(
        user=landing.user,
        organization=None,
        workspace=landing.default,
        query_params=QueryDict(),
    )
    monitor_view = view_for(UserAlertMonitorView, request)
    log_view = view_for(UserAlertMonitorLogView, request)
    # Nonempty ambient scope deliberately keeps the empty-workspace guard out
    # of this explicit-None organization witness.
    with workspace_context(landing.default, landing.org, landing.user):
        set_workspace_context(require_workspace=True)
        assert monitor_view._current_organization() is None
        assert log_view._current_organization() is None
        assert not monitor_view._visible_observe_projects().exists()
        assert not log_view._visible_alert_queryset().exists()
        assert not log_view.get_queryset().exists()
        page, count = monitor_view.get_queryset()
        assert count == 0
        assert list(page) == []
        log_view.action = "create"
        assert not log_view.get_serializer().fields["alert"].queryset.exists()


@pytest.mark.parametrize("action", ["create", "update", "partial_update"])
@pytest.mark.parametrize("many", [False, True])
@pytest.mark.parametrize("selection", ["empty", "named", "default"])
def test_monitor_log_alert_relation_validates_the_exact_caller_scope(
    landing, records, action, many, selection
):
    selected = {"empty": None, "named": landing.other, "default": landing.default}[
        selection
    ]
    expected = {"empty": [], "named": [0], "default": [1, 2]}[selection]
    request = SimpleNamespace(
        user=landing.user,
        organization=landing.org,
        workspace=selected,
        query_params=QueryDict(),
    )
    view = view_for(UserAlertMonitorLogView, request, action)
    with workspace_context(selected, landing.org, landing.user):
        set_workspace_context(require_workspace=True)
        serializer = view.get_serializer(many=many)
        field = getattr(serializer, "child", serializer).fields["alert"]
        assert set(field.queryset.values_list("pk", flat=True)) == {
            records[i][1].pk for i in expected
        }
        for index, (_, monitor, _) in enumerate(records):
            if index in expected:
                assert field.run_validation(str(monitor.pk)).pk == monitor.pk
            else:
                with pytest.raises(ValidationError) as refused:
                    field.run_validation(str(monitor.pk))
                assert refused.value.get_codes() == ["does_not_exist"]
