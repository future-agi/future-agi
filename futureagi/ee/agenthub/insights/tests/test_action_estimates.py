"""Prices for every billed Insights action, without reading the population."""

from types import SimpleNamespace

import pytest

from ee.agenthub.insights import billing
from tracer.models.insights import InsightConfig, InsightRun, InsightUnit
from tracer.tests.conftest import observe_project as observe_project
from tracer.tests.test_insights_api import insight_enabled as insight_enabled


@pytest.mark.django_db
@pytest.mark.parametrize("action", ["ask", "draft", "preview"])
@pytest.mark.parametrize("cloud", [False, True])
def test_action_estimate_matches_the_billed_step(
    auth_client, observe_project, monkeypatch, action, cloud
):
    monkeypatch.setattr(billing, "applies", lambda: cloud)
    checked = []
    monkeypatch.setattr(billing, "check", lambda org, cost: checked.append(cost))
    monkeypatch.setattr(billing, "credits", lambda cost: cost * 100)
    expected = getattr(billing, f"{action}_cost")()
    response = auth_client.post(
        "/tracer/insights/estimate/",
        {"project_id": str(observe_project.id), "action": action},
        format="json",
    )
    assert response.status_code == 200, response.json()
    data = response.json()["result"]
    assert data["credits_estimate"] == (expected * 100 if cloud else None)
    assert data["credit_limited"] is False
    assert data.get("credit_check_unavailable", False) is False
    assert checked == ([expected] if cloud else [])
    assert data["sample_ids"] == []


@pytest.mark.django_db
@pytest.mark.parametrize("action", ["ask", "draft", "preview"])
def test_action_estimate_explains_a_credit_refusal(
    auth_client, observe_project, monkeypatch, action
):
    monkeypatch.setattr(billing, "applies", lambda: True)
    monkeypatch.setattr(billing, "credits", lambda cost: 2)
    monkeypatch.setattr(billing, "check", lambda *args: SimpleNamespace(
        error_code="FREE_TIER_LIMIT", limit=100, current_usage=99,
    ))
    response = auth_client.post(
        "/tracer/insights/estimate/",
        {"project_id": str(observe_project.id), "action": action}, format="json",
    )
    assert response.status_code == 200, response.json()
    data = response.json()["result"]
    assert data["credit_limited"] is True
    assert data["credit_check_unavailable"] is False
    assert data["credits_left"] == 1
    assert "needs about 2 credits" in data["credit_message"]
    assert data["credit_max_sample"] is None


@pytest.mark.django_db
@pytest.mark.parametrize("action", ["run", "ask", "draft", "preview"])
def test_action_estimate_fails_closed_when_credits_cannot_be_checked(
    auth_client, observe_project, monkeypatch, action,
):
    monkeypatch.setattr(billing, "applies", lambda: True)

    def unavailable(*args):
        raise billing.CreditCheckUnavailable()

    monkeypatch.setattr(billing, "check", unavailable)
    body = {"project_id": str(observe_project.id)}
    body.update(
        {"unit": "sessions", "population_size": 10}
        if action == "run" else {"action": action}
    )
    response = auth_client.post(
        "/tracer/insights/estimate/", body, format="json",
    )
    assert response.status_code == 200, response.json()
    data = response.json()["result"]
    assert data["credit_limited"] is True
    assert data["credit_check_unavailable"] is True
    assert "Please retry in a moment." in data["credit_message"]


@pytest.mark.django_db
@pytest.mark.parametrize("analysed", [0, 3])
def test_failed_history_includes_actual_skips_and_reason(
    auth_client, observe_project, organization, workspace, user, analysed,
):
    config = InsightConfig.objects.create(
        project=observe_project, organization=organization, workspace=workspace,
        created_by=user, name="Failures", unit="sessions", lens="failures",
        question="Where did the agent fail?",
    )
    run = InsightRun.objects.create(
        config=config, project=observe_project, organization=organization,
        workspace=workspace, unit="sessions", status="failed", sample_size=130,
        error="There are no readable sessions in this sample.",
    )
    InsightUnit.objects.bulk_create([
        InsightUnit(run=run, unit_id=f"s{i}", status="skipped") for i in range(125)
    ] + [
        InsightUnit(run=run, unit_id=f"read-{i}", status="done")
        for i in range(analysed)
    ] + [
        InsightUnit(run=run, unit_id="pending", status="pending"),
        InsightUnit(run=run, unit_id="excluded", status="done", included=False),
    ])
    detail = auth_client.get(f"/tracer/insights/runs/{run.id}/").json()["result"]
    history = auth_client.get(f"/tracer/insights/configs/{config.id}/").json()["result"]
    for item in (detail, history["latest_run"], history["runs"][0]):
        assert item["skipped_count"] == 125
        assert item["analysed_count"] == analysed
        assert item["error"] == run.error
