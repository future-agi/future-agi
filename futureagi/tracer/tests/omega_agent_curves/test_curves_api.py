"""The curves route's walls and its request rules (learning-service P1-09). The ClickHouse read
is stood in for (conftest ``recording_read``), so each test can say whether anything was read;
the numbers themselves are test_curves_replay.py.
"""

from datetime import UTC, datetime, time, timedelta

import pytest
from django.utils import timezone

from omega_rollout.models import ProductAccess, ProductRollout
from tfc.utils.error_codes import get_error_message
from tracer.services.omega_agents.curves.runs import MAX_RUNS, TooManyRuns
from tracer.tests.omega_agent_curves.conftest import (
    READ,
    RecordingRead,
    agent_project,
    curves_url,
    new_agent_id,
    read_curves,
)

pytestmark = [pytest.mark.integration, pytest.mark.django_db]


def _today():
    return timezone.now().astimezone(UTC).date()


def test_the_answer_reads_the_agent_in_the_callers_project(
    auth_client, project_a, recording_read
):
    agent_id = new_agent_id()

    response = read_curves(auth_client, agent_id, project_a.id)

    assert response.status_code == 200, response.data
    result = response.data["result"]
    assert result["agentId"] == agent_id
    assert result["projectId"] == str(project_a.id)
    assert result["totals"]["n"] == 1
    [call] = recording_read.calls
    assert call["project_id"] == str(project_a.id)
    assert call["agent_id"] == agent_id


def test_the_default_window_is_the_last_30_utc_days_today_included(
    auth_client, project_a, recording_read
):
    response = read_curves(auth_client, new_agent_id(), project_a.id)

    today = _today()
    window = response.data["result"]["window"]
    assert window == {
        "startDate": (today - timedelta(days=29)).isoformat(),
        "endDate": today.isoformat(),
        "timezone": "UTC",
    }
    [call] = recording_read.calls
    assert call["start"] == datetime.combine(
        today - timedelta(days=29), time.min, tzinfo=UTC
    )
    assert call["end"] == datetime.combine(
        today + timedelta(days=1), time.min, tzinfo=UTC
    )


def test_a_chosen_window_reads_whole_utc_days(auth_client, project_a, recording_read):
    response = read_curves(
        auth_client,
        new_agent_id(),
        project_a.id,
        start_date="2026-10-01",
        end_date="2026-10-02",
    )

    assert response.status_code == 200, response.data
    [call] = recording_read.calls
    assert call["start"] == datetime(2026, 10, 1, tzinfo=UTC)
    assert call["end"] == datetime(2026, 10, 3, tzinfo=UTC)


@pytest.mark.parametrize(
    "params",
    [
        {"start_date": "2026-10-05", "end_date": "2026-10-04"},
        {"start_date": "2026-01-01", "end_date": "2026-04-30"},
        {"end_date": "2999-01-01"},
        {"start_date": "yesterday"},
        {"days": "7"},
    ],
)
def test_a_bad_window_or_an_unknown_parameter_is_400_and_reads_nothing(
    auth_client, project_a, recording_read, params
):
    response = read_curves(auth_client, new_agent_id(), project_a.id, **params)

    assert response.status_code == 400
    assert recording_read.calls == []


def test_the_project_id_is_required(auth_client, recording_read):
    response = auth_client.get(curves_url(new_agent_id()))

    assert response.status_code == 400
    assert recording_read.calls == []


@pytest.mark.parametrize("agent_id", ["-starts-with-dash", "bad*id", "x" * 300])
def test_a_malformed_agent_id_is_400_and_reads_nothing(
    auth_client, project_a, recording_read, agent_id
):
    response = auth_client.get(
        f"/tracer/omega/agents/{agent_id}/curves/", {"project_id": str(project_a.id)}
    )

    assert response.status_code == 400
    assert response.data["result"] == get_error_message("OMEGA_AGENT_CURVES_INVALID_ID")
    assert recording_read.calls == []


def test_a_project_that_is_not_an_observe_project_is_404(
    auth_client, organization, workspace, recording_read
):
    experiment = agent_project(organization, workspace, trace_type="experiment")

    response = read_curves(auth_client, new_agent_id(), experiment.id)

    assert response.status_code == 404
    assert response.data["result"] == get_error_message(
        "OMEGA_AGENT_CURVES_PROJECT_NOT_FOUND"
    )
    assert recording_read.calls == []


def test_a_deleted_project_is_404(auth_client, project_a, recording_read):
    project_a.deleted = True
    project_a.save(update_fields=["deleted"])

    response = read_curves(auth_client, new_agent_id(), project_a.id)

    assert response.status_code == 404
    assert recording_read.calls == []


def test_too_many_runs_is_400_with_the_reason(auth_client, project_a, monkeypatch):
    monkeypatch.setattr(READ, RecordingRead(error=TooManyRuns()))

    response = read_curves(auth_client, new_agent_id(), project_a.id)

    assert response.status_code == 400
    assert response.data["result"] == get_error_message(
        "OMEGA_AGENT_CURVES_TOO_MANY_RUNS"
    ).format(MAX_RUNS)


def test_a_failed_read_is_500_and_says_nothing_of_the_cause(
    auth_client, project_a, monkeypatch
):
    monkeypatch.setattr(
        READ, RecordingRead(error=RuntimeError("clickhouse host 10.0.0.7 refused"))
    )

    response = read_curves(auth_client, new_agent_id(), project_a.id)

    assert response.status_code == 500
    assert response.data["result"] == get_error_message("OMEGA_AGENT_CURVES_FAILED")
    assert "10.0.0.7" not in str(response.data)


def test_the_part_is_off_by_default(
    auth_client, project_a, recording_read, settings, monkeypatch
):
    del settings.OMEGA_RELEASED_PARTS
    monkeypatch.delenv("OMEGA_RELEASED_PARTS", raising=False)

    response = read_curves(auth_client, new_agent_id(), project_a.id)

    assert response.status_code == 404
    assert recording_read.calls == []


@pytest.mark.parametrize("released", ["", "agent_observe,deploy_cli,learning_curve"])
def test_the_part_off_is_404_and_reads_nothing(
    auth_client, project_a, recording_read, settings, released
):
    settings.OMEGA_RELEASED_PARTS = released

    response = read_curves(auth_client, new_agent_id(), project_a.id)

    assert response.status_code == 404
    assert str(project_a.id) not in str(response.data)
    assert recording_read.calls == []


def test_an_org_not_admitted_to_the_console_gets_404_and_reads_nothing(
    auth_client, organization, project_a, recording_read
):
    ProductAccess.objects.filter(organization=organization).delete()

    response = read_curves(auth_client, new_agent_id(), project_a.id)

    assert response.status_code == 404
    assert response.data["code"] == "product_not_admitted"
    assert recording_read.calls == []


def test_a_paused_console_still_answers_a_read(auth_client, project_a, recording_read):
    ProductRollout.objects.filter(product="console").update(paused=True)

    response = read_curves(auth_client, new_agent_id(), project_a.id)

    assert response.status_code == 200, response.data
    assert len(recording_read.calls) == 1


def test_no_deployments_admission_is_needed(
    auth_client, organization, project_a, recording_read
):
    assert not ProductAccess.objects.filter(
        organization=organization, product="deployments"
    ).exists()

    response = read_curves(auth_client, new_agent_id(), project_a.id)

    assert response.status_code == 200, response.data


def test_anonymous_is_refused_before_any_work(api_client, project_a, recording_read):
    response = read_curves(api_client, new_agent_id(), project_a.id)

    assert response.status_code in (401, 403)
    assert recording_read.calls == []


def test_a_window_ending_today_is_allowed_and_tomorrow_is_not(
    auth_client, project_a, recording_read
):
    today = _today()
    ok = read_curves(
        auth_client,
        new_agent_id(),
        project_a.id,
        start_date=today.isoformat(),
        end_date=today.isoformat(),
    )
    late = read_curves(
        auth_client,
        new_agent_id(),
        project_a.id,
        end_date=(today + timedelta(days=1)).isoformat(),
    )

    assert ok.status_code == 200, ok.data
    assert late.status_code == 400
    assert len(recording_read.calls) == 1
