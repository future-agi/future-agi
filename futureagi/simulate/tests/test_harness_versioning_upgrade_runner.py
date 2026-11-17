"""Operator backfill claims, fencing, heartbeats, and restartable outcomes."""

import io
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connections
from django.utils import timezone

from simulate.models import HarnessEnvironmentVersion, HarnessSuiteVersion
from simulate.models import HarnessVersioningBackfillOutcome as Outcome
from simulate.models import HarnessVersioningBackfillProgress as Progress
from simulate.services.harness_versioning import backfill

pytestmark = pytest.mark.django_db(transaction=True)


def runner():
    # The tests-only commit collects before the writer implementation exists.
    from simulate.services.harness_versioning import upgrade_runner

    return upgrade_runner


@pytest.fixture
def environments(user, workspace, monkeypatch, settings):
    from simulate.tests.test_harness_versioning_backfill import (
        _legacy,
        _RealisticStorage,
    )
    from tfc.utils import storage_client

    settings.HARNESS_VERSIONING_ENABLED = True
    storage = _RealisticStorage()
    monkeypatch.setattr(storage_client, "_client", storage)
    return [_legacy(user, workspace, f"upgrade-worker-{i}", storage) for i in range(2)]


def test_two_simultaneous_claimants_have_one_owner(environments):
    service = runner()
    organization_id = environments[0].organization_id
    ready = Barrier(2)

    def compete():
        try:
            ready.wait(timeout=20)
            return service.claim_organization(organization_id, total=2)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(compete) for _ in range(2)]
        claims = [future.result(timeout=30) for future in futures]
    owners = [claim for claim in claims if claim is not None]
    assert len(owners) == 1
    progress = Progress.objects.get(organization_id=organization_id)
    assert progress.run_token == owners[0].run_token
    assert progress.attempt == 1
    assert progress.state == "running"
    assert progress.total == 2
    assert progress.processed == 0


def test_live_claim_does_not_run_or_claim_completion(environments, monkeypatch):
    service = runner()
    claim = service.claim_organization(environments[0].organization_id, total=2)
    calls = []
    monkeypatch.setattr(
        backfill, "backfill_environment", lambda *a, **k: calls.append(a)
    )
    report = service.run_managed([job.id for job in environments], automatic=True)
    assert report.state == "already_running"
    assert report.busy_organizations == 1
    assert report.failed == 0
    assert calls == []
    assert not Outcome.objects.exists()
    progress = Progress.objects.get(organization_id=claim.organization_id)
    assert progress.state == "running"
    assert progress.processed == 0


def test_stale_takeover_fences_old_outcomes_heartbeat_and_finish(environments):
    service = runner()
    job = environments[0]
    old = service.claim_organization(job.organization_id, total=1)
    service.record_started(old, job.id)
    Progress.objects.filter(organization_id=job.organization_id).update(
        heartbeat_at=timezone.now() - timedelta(minutes=6)
    )
    new = service.claim_organization(job.organization_id, total=1)
    assert new is not None
    assert new.run_token != old.run_token
    assert new.attempt == old.attempt + 1
    service.record_started(new, job.id)
    result = backfill.BackfillResult(job_id=str(job.id), outcome="current")
    for operation in (
        lambda: service.record_started(old, job.id),
        lambda: service.record_result(old, result),
        lambda: service.heartbeat(old),
        lambda: service.finish_claim(old),
    ):
        with pytest.raises(service.LeaseLost):
            operation()
    outcome = Outcome.objects.get(environment=job)
    progress = Progress.objects.get(organization_id=job.organization_id)
    assert outcome.run_token == new.run_token
    assert outcome.state == "running"
    assert progress.run_token == new.run_token
    assert progress.processed == 0
    assert progress.state == "running"


def test_timer_heartbeats_while_environment_work_is_blocked(environments):
    service = runner()
    claim = service.claim_organization(environments[0].organization_id, total=2)
    before = timezone.now() - timedelta(minutes=2)
    Progress.objects.filter(organization_id=claim.organization_id).update(
        heartbeat_at=before
    )
    tick = Event()
    with service.Heartbeat(claim, on_tick=tick.set, interval=0.02):
        assert tick.wait(timeout=10), "no heartbeat while the worker was busy"
        progress = Progress.objects.get(organization_id=claim.organization_id)
        assert progress.heartbeat_at > before
        assert progress.state == "running"
        assert progress.processed == 0
    assert service.HEARTBEAT_SECONDS <= 60


@pytest.mark.parametrize(
    "outcome", ["current", "skipped_unreadable", "no_archive", "not_environment"]
)
def test_every_visited_environment_gets_its_actual_outcome(
    environments, monkeypatch, outcome
):
    service = runner()
    job = environments[0]
    monkeypatch.setattr(
        backfill,
        "backfill_environment",
        lambda job_id, **kwargs: backfill.BackfillResult(
            job_id=str(job_id), outcome=outcome
        ),
    )
    report = service.run_managed([job.id], automatic=True)
    persisted = Outcome.objects.get(environment=job)
    progress = Progress.objects.get(organization_id=job.organization_id)
    failed = outcome in backfill.FAILED_OUTCOMES
    assert persisted.outcome_code == outcome
    assert persisted.state == ("failed" if failed else "complete")
    assert report.state == ("failed" if failed else "complete")
    assert progress.processed == progress.total == 1
    assert progress.failed == int(failed)
    assert progress.outcomes == {outcome: 1}


def test_failed_environment_does_not_stop_others_and_rerun_reuses_versions(
    environments, monkeypatch
):
    service = runner()
    broken, healthy = environments
    original = backfill.backfill_environment

    def fail_one(job_id, **kwargs):
        if job_id == broken.id:
            raise RuntimeError("synthetic detail must stay out of automatic output")
        return original(job_id, **kwargs)

    monkeypatch.setattr(backfill, "backfill_environment", fail_one)
    first = service.run_managed([job.id for job in environments], automatic=True)
    assert first.state == "failed"
    assert first.failed == 1
    assert Outcome.objects.get(environment=broken).outcome_code == "error"
    assert Outcome.objects.get(environment=healthy).state == "complete"
    healthy.refresh_from_db()
    pins = (healthy.environment_version_id, healthy.suite_version_id)
    assert all(pins)
    monkeypatch.setattr(backfill, "backfill_environment", original)
    second = service.run_managed([job.id for job in environments], automatic=True)
    assert second.state == "complete"
    assert second.failed == 0
    healthy.refresh_from_db()
    assert (healthy.environment_version_id, healthy.suite_version_id) == pins
    for job in environments:
        assert (
            HarnessEnvironmentVersion.no_workspace_objects.filter(job=job).count() == 1
        )
        assert HarnessSuiteVersion.no_workspace_objects.filter(job=job).count() == 1
        assert Outcome.objects.get(environment=job).state == "complete"
    progress = Progress.objects.get(organization_id=broken.organization_id)
    assert progress.attempt == 2
    assert progress.processed == progress.total == 2
    assert progress.failed == 0


def test_recording_result_twice_does_not_double_count(environments):
    service = runner()
    job = environments[0]
    claim = service.claim_organization(job.organization_id, total=1)
    service.record_started(claim, job.id)
    result = backfill.BackfillResult(job_id=str(job.id), outcome="current")
    service.record_result(claim, result)
    service.record_result(claim, result)
    progress = Progress.objects.get(organization_id=job.organization_id)
    assert progress.processed == 1
    assert progress.skipped == 1
    assert progress.outcomes == {"current": 1}


def test_explicit_off_is_a_distinct_no_write_success(
    environments, settings, monkeypatch
):
    service = runner()
    settings.HARNESS_VERSIONING_ENABLED = False
    calls = []
    monkeypatch.setattr(
        backfill, "backfill_environment", lambda *a, **k: calls.append(a)
    )
    report = service.run_managed(automatic=True)
    assert report.state == "off"
    assert report.failed == 0
    assert calls == []
    assert not Progress.objects.exists()
    assert not Outcome.objects.exists()
    for job in environments:
        job.refresh_from_db()
        assert job.environment_version_id is None
        assert job.suite_version_id is None


def test_auto_command_failure_is_count_only_nonzero_and_restores_logging(
    environments, monkeypatch, caplog
):
    detail = "synthetic-private-backfill-diagnostic"

    def fail(job_id, **kwargs):
        logging.getLogger("synthetic.backfill").error(detail)
        raise RuntimeError(detail)

    monkeypatch.setattr(backfill, "backfill_environment", fail)
    stdout, stderr = io.StringIO(), io.StringIO()
    old_disable = logging.root.manager.disable
    with pytest.raises(CommandError, match="environment.*need attention"):
        call_command(
            "harness_versions_backfill", "--auto", stdout=stdout, stderr=stderr
        )
    text = stdout.getvalue() + stderr.getvalue() + caplog.text
    assert "state=failed" in text
    assert "failed=2" in text
    assert detail not in text
    assert "RuntimeError" not in text
    for job in environments:
        assert str(job.id) not in text
    assert logging.root.manager.disable == old_disable
    assert Outcome.objects.filter(state="failed", outcome_code="error").count() == 2


@pytest.mark.parametrize("option", ["--check", "--json", "--job", "--output"])
def test_auto_rejects_manual_report_options_before_writes(
    environments, option, tmp_path
):
    args = ["--auto", option]
    if option == "--job":
        args.append(str(environments[0].id))
    if option == "--output":
        args.append(str(tmp_path / "report.json"))
    with pytest.raises(CommandError, match="--auto cannot be combined"):
        call_command("harness_versions_backfill", *args)
    assert not Progress.objects.exists()
    assert not Outcome.objects.exists()
    assert not (tmp_path / "report.json").exists()


def test_manual_operator_run_keeps_diagnostics_and_records_outcome(
    environments, monkeypatch, caplog
):
    def fail(job_id, **kwargs):
        raise RuntimeError("manual diagnostic")

    monkeypatch.setattr(backfill, "backfill_environment", fail)
    job = environments[0]
    stderr = io.StringIO()
    with pytest.raises(CommandError):
        call_command("harness_versions_backfill", "--job", str(job.id), stderr=stderr)
    assert f"{job.id}: error (RuntimeError: manual diagnostic)" in stderr.getvalue()
    assert any(record.exc_info for record in caplog.records)
    assert Outcome.objects.get(environment=job).outcome_code == "error"
