"""Saved report starts reserve one database run before starting Temporal."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock

import pytest
from django.db import close_old_connections, connection, connections
from django.utils import timezone
from rest_framework.test import APIClient

from ee.agenthub.insights import agent, billing, pipeline, report
from tracer.models.insights import InsightConfig, InsightRun, InsightUnit
from tracer.tests.test_insights_api import RUNS, SAVED_TAXONOMY, population
from tracer.views import insights as views


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    from tfc.capabilities import service
    from tfc.capabilities.service import CapabilityDecision

    monkeypatch.setattr("ee.licensing.activation_client._activate", lambda: None)
    monkeypatch.setattr(service, "_configured", True)
    monkeypatch.setattr(
        service,
        "check",
        lambda feature_id, **kw: CapabilityDecision(
            allowed=True, feature_id=feature_id
        ),
    )
    monkeypatch.setattr(views, "_spend", lambda *args: True)
    monkeypatch.setattr(views, "_credits_refused", lambda *args, **kw: None)
    monkeypatch.setattr(
        agent, "resolve_engine", lambda: {"kind": "builtin", "version": "builtin/v1"}
    )


def _config(project, user):
    return InsightConfig.objects.create(
        project=project,
        organization=project.organization,
        workspace=project.workspace,
        created_by=user,
        name="Failures",
        unit="sessions",
        lens="failures",
        question="Where does the agent fail?",
        sample_size=10,
        taxonomy=SAVED_TAXONOMY,
    )


def _body(project, config, *, rediscover=False):
    units = [{**unit, "time": timezone.now().isoformat()} for unit in population(10)]
    return {
        "project_id": str(project.id),
        "config_id": str(config.id),
        "population": units,
        "sample_size": 10,
        "reset_taxonomy": rediscover,
    }


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("rediscover", [False, True])
def test_concurrent_requests_reserve_one_run_and_start_once(
    user,
    organization,
    workspace,
    observe_project,
    monkeypatch,
    rediscover,
):
    if connection.vendor != "postgresql":
        pytest.skip("Concurrent row-lock behavior requires PostgreSQL")
    config = _config(observe_project, user)
    login = APIClient().post(
        "/accounts/token/",
        {"email": user.email, "password": "testpassword123", "remember_me": True},
        format="json",
    )
    assert login.status_code == 200
    token = login.data["access"]
    body = _body(observe_project, config, rediscover=rediscover)
    barrier = Barrier(2)
    guard = Lock()
    pids, preflight_atomic, starts, start_atomic = [], [], [], []

    def resolve_engine():
        preflight_atomic.append(connection.in_atomic_block)
        barrier.wait(timeout=15)
        return {"kind": "builtin", "version": "builtin/v1"}

    def start(run_id):
        with guard:
            starts.append(run_id)
            start_atomic.append(connection.in_atomic_block)
        assert InsightRun.objects.filter(id=run_id).exists()
        return f"insight-{run_id}"

    monkeypatch.setattr(agent, "resolve_engine", resolve_engine)
    monkeypatch.setattr("tfc.temporal.insights.client.start_insight_run", start)

    def request():
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                pid = cursor.fetchone()[0]
            with guard:
                pids.append(pid)
            client = APIClient()
            client.credentials(
                HTTP_AUTHORIZATION=f"Bearer {token}",
                HTTP_X_ORGANIZATION_ID=str(organization.id),
                HTTP_X_WORKSPACE_ID=str(workspace.id),
            )
            response = client.post(RUNS, body, format="json")
            return response.status_code, response.json()
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: request(), range(2)))
    assert len(set(pids)) == 2
    assert preflight_atomic == [False, False]
    assert [code for code, _ in responses] == [200, 200], responses
    run_ids = {data["result"]["run_id"] for _, data in responses}
    assert len(run_ids) == 1
    run = InsightRun.objects.get(config=config)
    assert run_ids == {str(run.id)}
    assert starts == [str(run.id)]
    assert start_atomic == [False]
    assert InsightUnit.objects.filter(run=run).count() == 10
    assert run.taxonomy_mode == ("discover" if rediscover else "classify")
    config.refresh_from_db()
    assert config.taxonomy == ({} if rediscover else SAVED_TAXONOMY)

    # Settle the one reserved report using the real billing event builder.
    # Model output and external event delivery are the only replaced boundaries.
    emitted = []
    monkeypatch.setattr("ee.usage.services.emitter.emit", emitted.append)
    monkeypatch.setattr(billing, "applies", lambda: True)
    monkeypatch.setattr(
        report, "write", lambda *args, **kw: {"title": "Done", "findings": []}
    )
    monkeypatch.setattr(pipeline, "_forget_evidence", lambda *args: None)
    InsightRun.objects.filter(id=run.id).update(status="running", cost_usd=0.1)
    pipeline.finish(str(run.id))
    assert len(emitted) == 1
    assert emitted[0].properties["source_id"] == str(run.id)
    assert emitted[0].amount == pytest.approx(billing.credits(0.1))


@pytest.mark.django_db
def test_failed_start_preserves_taxonomy_and_allows_a_later_retry(
    auth_client,
    observe_project,
    user,
    monkeypatch,
):
    config = _config(observe_project, user)
    body = _body(observe_project, config, rediscover=True)

    def unavailable(run_id):
        raise RuntimeError("Temporal unavailable")

    monkeypatch.setattr("tfc.temporal.insights.client.start_insight_run", unavailable)
    failed = auth_client.post(RUNS, body, format="json")
    assert failed.status_code == 200
    first = InsightRun.objects.get(id=failed.json()["result"]["run_id"])
    assert first.status == "failed"
    config.refresh_from_db()
    assert config.taxonomy == SAVED_TAXONOMY

    starts = []
    monkeypatch.setattr(
        "tfc.temporal.insights.client.start_insight_run",
        lambda run_id: starts.append(run_id) or f"insight-{run_id}",
    )
    retried = auth_client.post(RUNS, body, format="json")
    assert retried.status_code == 200
    second = retried.json()["result"]["run_id"]
    assert second != str(first.id)
    assert starts == [second]
    config.refresh_from_db()
    assert config.taxonomy == {}


@pytest.mark.django_db
def test_report_deleted_during_preflight_is_not_reserved(
    auth_client, observe_project, user, monkeypatch
):
    config = _config(observe_project, user)

    def resolve_engine():
        InsightConfig.objects.filter(id=config.id).update(
            deleted=True, deleted_at=timezone.now()
        )
        return {"kind": "builtin", "version": "builtin/v1"}

    monkeypatch.setattr(agent, "resolve_engine", resolve_engine)
    response = auth_client.post(RUNS, _body(observe_project, config), format="json")
    assert response.status_code == 404
    assert not InsightRun.objects.filter(config_id=config.id).exists()


@pytest.mark.django_db
def test_rediscover_does_not_erase_taxonomy_written_during_startup(
    auth_client,
    observe_project,
    user,
    monkeypatch,
):
    config = _config(observe_project, user)
    replacement = {"tops": [{"id": "new-top"}], "subs": [{"id": "new-sub"}]}

    def start(run_id):
        InsightConfig.objects.filter(id=config.id).update(taxonomy=replacement)
        return f"insight-{run_id}"

    monkeypatch.setattr("tfc.temporal.insights.client.start_insight_run", start)
    response = auth_client.post(
        RUNS, _body(observe_project, config, rediscover=True), format="json"
    )
    assert response.status_code == 200
    config.refresh_from_db()
    assert config.taxonomy == replacement
