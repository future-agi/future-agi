import datetime
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework import status

from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from model_hub.models.monitor_alert import MonitorAlert
from model_hub.models.monitors import Monitor


def _make_model(*, organization, workspace, name):
    return AIModel.all_objects.create(
        user_model_id=name,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        organization=organization,
        workspace=workspace,
    )


def _make_monitor(model, *, name):
    return Monitor.objects.create(
        status=True,
        name=name,
        monitor_type=Monitor.MonitorTypes.PERFORMANCE,
        dimension="latency",
        metric="p95",
        current_value=10,
        trigger_value=20,
        ai_model=model,
    )


def _make_alert(monitor, *, created_at):
    alert = MonitorAlert.objects.create(
        monitor=monitor,
        triggered_value=42,
    )
    MonitorAlert.objects.filter(id=alert.id).update(
        created_at=created_at,
        updated_at=created_at,
    )
    alert.refresh_from_db()
    return alert


@pytest.mark.django_db
class TestOverviewContracts:
    @patch("model_hub.views.overview.get_model_hourly_volume")
    @patch("model_hub.views.overview.timezone.now")
    def test_overview_uses_active_workspace_models_for_volume_and_issues(
        self, mock_now, mock_get_model_hourly_volume, auth_client, user, workspace
    ):
        now = timezone.make_aware(datetime.datetime(2026, 6, 1, 12, 30))
        mock_now.return_value = now
        mock_get_model_hourly_volume.side_effect = [
            ([{"x": now, "y": 2}], 2),
            ([{"x": now, "y": 5}], 5),
        ]

        model = _make_model(
            organization=user.organization,
            workspace=workspace,
            name="overview active model",
        )
        monitor = _make_monitor(model, name="overview active monitor")
        _make_alert(monitor, created_at=now - timezone.timedelta(hours=1))
        _make_alert(monitor, created_at=now - timezone.timedelta(hours=2))
        _make_alert(monitor, created_at=now - timezone.timedelta(hours=30))

        other_workspace = Workspace.no_workspace_objects.create(
            name="Hidden overview workspace",
            organization=user.organization,
            is_active=True,
            created_by=user,
        )
        hidden_model = _make_model(
            organization=user.organization,
            workspace=other_workspace,
            name="overview hidden model",
        )
        hidden_monitor = _make_monitor(hidden_model, name="overview hidden monitor")
        for offset in [1, 3, 30, 31]:
            _make_alert(
                hidden_monitor,
                created_at=now - timezone.timedelta(hours=offset),
            )

        response = auth_client.get("/model-hub/overview/")

        assert response.status_code == status.HTTP_200_OK
        payload = response.json()
        assert payload["volume"]["total_count"] == 2
        assert payload["volume"]["change"] == pytest.approx(-33.3333333333)
        assert payload["issues"]["total_count"] == 2
        assert payload["issues"]["change"] == 100
        assert len(payload["issues"]["last_day"]) == 24
        assert sum(point["y"] for point in payload["issues"]["last_day"]) == 2

        first_call = mock_get_model_hourly_volume.call_args_list[0].kwargs
        second_call = mock_get_model_hourly_volume.call_args_list[1].kwargs
        assert first_call["model_ids"] == [str(model.id)]
        assert second_call["model_ids"] == [str(model.id)]
        assert second_call["hours"] == 48
