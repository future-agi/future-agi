"""N14: independent organization clauses cannot hide behind a second filter."""

from types import SimpleNamespace

import pytest
from django.http import QueryDict

from accounts.tests.test_workspace_landing import landing as _landing_fixture
from tfc.middleware.workspace_context import set_workspace_context, workspace_context
from tfc.utils.base_viewset import BaseModelViewSetMixin
from tracer.models.monitor import UserAlertMonitor, UserAlertMonitorLog
from tracer.models.project import Project
from tracer.tests.test_wsl_merge_monitor import clean_context as _clean_context
from tracer.tests.test_wsl_merge_monitor import records as _records
from tracer.tests.test_wsl_merge_monitor import view_for
from tracer.views.monitor import UserAlertMonitorLogView, UserAlertMonitorView

landing = _landing_fixture
records = _records
clean_context = _clean_context


@pytest.mark.parametrize("selection", ["named", "default"])
@pytest.mark.parametrize("boundary", ["alerts", "logs", "projects"])
def test_monitor_selector_owns_its_organization_filter(
    landing, records, monkeypatch, selection, boundary
):
    selected = landing.other if selection == "named" else landing.default
    expected = [0] if selection == "named" else [1, 2]
    foreign_project, foreign_monitor, foreign_log = records[3]
    # Corrupt only the fixture workspace FK. The workspace predicate now
    # accepts the foreign victim, so this layer's own org clause must deny it.
    Project._base_manager.filter(pk=foreign_project.pk).update(workspace=selected)
    UserAlertMonitor._base_manager.filter(pk=foreign_monitor.pk).update(
        workspace=selected
    )
    foreign_project.refresh_from_db()
    foreign_monitor.refresh_from_db()
    assert foreign_project.organization_id != landing.org.pk
    assert foreign_monitor.organization_id != landing.org.pk
    assert foreign_project.workspace_id == foreign_monitor.workspace_id == selected.pk
    model, column = {
        "projects": (Project, 0),
        "alerts": (UserAlertMonitor, 1),
        "logs": (UserAlertMonitorLog, 2),
    }[boundary]
    prebuilt = model.no_workspace_objects.filter(
        pk__in=[row[column].pk for row in records]
    )
    assert prebuilt.count() == 4
    request = SimpleNamespace(
        user=landing.user,
        organization=landing.org,
        workspace=selected,
        query_params=QueryDict(),
    )
    monitor_view = view_for(UserAlertMonitorView, request)
    log_view = view_for(UserAlertMonitorLogView, request)
    if boundary == "projects":
        monkeypatch.setattr(Project, "no_workspace_objects", prebuilt)
        predicate = monitor_view._workspace_scope_q()
        select = monitor_view._visible_observe_projects
    elif boundary == "alerts":
        monkeypatch.setattr(UserAlertMonitor, "objects", prebuilt)
        predicate = log_view._alert_workspace_scope_q()
        select = log_view._visible_alert_queryset
    else:
        # The test targets LogView's additional clause, not its superclass.
        # A real pre-auth QuerySet avoids masking the deleted clause with the
        # mixin's independently tested organization filter. No SQL is mocked.
        monkeypatch.setattr(
            BaseModelViewSetMixin, "get_queryset", lambda self: prebuilt
        )
        predicate = log_view._workspace_scope_q()
        select = log_view.get_queryset
    with workspace_context(selected, landing.org, landing.user):
        set_workspace_context(require_workspace=True)
        assert prebuilt.filter(predicate, pk=records[3][column].pk).exists()
        assert set(select().values_list("pk", flat=True)) == {
            records[index][column].pk for index in expected
        }
    assert foreign_log.alert_id == foreign_monitor.pk
