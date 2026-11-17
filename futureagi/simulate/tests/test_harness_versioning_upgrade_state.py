"""Backfill progress is durable, scoped, and distinct from version readiness."""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.utils import timezone

pytestmark = pytest.mark.django_db


def state_models():
    # Lazy imports keep the tests-only commit collectible before the schema lands.
    from simulate.models import (
        HarnessVersioningBackfillOutcome,
        HarnessVersioningBackfillProgress,
    )

    return HarnessVersioningBackfillProgress, HarnessVersioningBackfillOutcome


@pytest.fixture
def environment(user, workspace, monkeypatch, settings):
    from simulate.tests.test_harness_versioning_backfill import (
        _legacy,
        _RealisticStorage,
    )
    from tfc.utils import storage_client

    settings.HARNESS_VERSIONING_ENABLED = True
    storage = _RealisticStorage()
    monkeypatch.setattr(storage_client, "_client", storage)
    return _legacy(user, workspace, "upgrade-state", storage)


def test_no_record_is_not_started(environment):
    from simulate.services.harness_versioning.upgrade_state import backfill_state

    assert backfill_state(environment) == {
        "state": "not_started",
        "outcome_code": None,
        "updated_at": None,
        "progress": None,
    }


def test_another_organizations_progress_is_not_returned(environment):
    from accounts.models import Organization
    from simulate.services.harness_versioning.upgrade_state import backfill_state

    Progress, _ = state_models()
    other = Organization.objects.create(name="Other backfill organization")
    Progress.objects.create(
        organization=other,
        state="failed",
        total=91,
        processed=91,
        failed=91,
        heartbeat_at=timezone.now(),
    )
    assert backfill_state(environment)["progress"] is None
    assert backfill_state(environment)["state"] == "not_started"


def test_environment_outcome_is_not_inferred_from_organization_failure(environment):
    from simulate.services.harness_versioning.upgrade_state import backfill_state

    Progress, Outcome = state_models()
    token = uuid4()
    Progress.objects.create(
        organization_id=environment.organization_id,
        state="failed",
        run_token=token,
        total=2,
        processed=2,
        succeeded=1,
        failed=1,
        heartbeat_at=timezone.now(),
    )
    Outcome.objects.create(
        environment=environment,
        run_token=token,
        state="complete",
        outcome_code="current",
    )
    result = backfill_state(environment)
    assert result["state"] == "complete"
    assert result["outcome_code"] == "current"
    assert result["progress"]["failed"] == 1
    assert result["progress"]["succeeded"] == 1
    assert "run_token" not in result
    assert "run_token" not in result["progress"]
    assert "organization_id" not in result["progress"]
    assert "notes" not in result


def test_failed_environment_reports_only_its_outcome_code(environment):
    from simulate.services.harness_versioning.upgrade_state import backfill_state

    Progress, Outcome = state_models()
    token = uuid4()
    Progress.objects.create(
        organization_id=environment.organization_id,
        state="failed",
        run_token=token,
        total=1,
        processed=1,
        failed=1,
        heartbeat_at=timezone.now(),
    )
    Outcome.objects.create(
        environment=environment,
        run_token=token,
        state="failed",
        outcome_code="skipped_unreadable",
    )
    result = backfill_state(environment)
    assert result["state"] == "failed"
    assert result["outcome_code"] == "skipped_unreadable"
    environment.refresh_from_db()
    assert environment.environment_version_id is None
    assert environment.suite_version_id is None


def test_stale_running_is_interrupted_without_mutating_the_read(environment):
    from simulate.services.harness_versioning.upgrade_state import backfill_state

    Progress, Outcome = state_models()
    token = uuid4()
    progress = Progress.objects.create(
        organization_id=environment.organization_id,
        state="running",
        run_token=token,
        heartbeat_at=timezone.now() - timedelta(minutes=6),
        total=1,
    )
    outcome = Outcome.objects.create(
        environment=environment, run_token=token, state="running"
    )
    result = backfill_state(environment)
    assert result["state"] == "failed"
    assert result["outcome_code"] == "interrupted"
    assert result["progress"]["state"] == "failed"
    progress.refresh_from_db()
    outcome.refresh_from_db()
    assert progress.state == "running"
    assert outcome.state == "running"


def test_live_running_has_no_completion_claim(environment):
    from simulate.services.harness_versioning.upgrade_state import backfill_state

    Progress, Outcome = state_models()
    token = uuid4()
    Progress.objects.create(
        organization_id=environment.organization_id,
        state="running",
        run_token=token,
        heartbeat_at=timezone.now(),
        total=2,
        processed=1,
        succeeded=1,
    )
    Outcome.objects.create(environment=environment, run_token=token, state="running")
    result = backfill_state(environment)
    assert result["state"] == "running"
    assert result["outcome_code"] is None
    assert result["progress"]["processed"] == 1
    assert result["progress"]["total"] == 2


def test_explicit_off_remains_off_with_existing_records(environment, settings):
    from simulate.services.harness_versioning.upgrade_state import backfill_state

    Progress, Outcome = state_models()
    token = uuid4()
    Progress.objects.create(
        organization_id=environment.organization_id,
        state="complete",
        run_token=token,
        heartbeat_at=timezone.now(),
        total=1,
        processed=1,
        succeeded=1,
    )
    Outcome.objects.create(
        environment=environment,
        run_token=token,
        state="complete",
        outcome_code="current",
    )
    settings.HARNESS_VERSIONING_ENABLED = False
    result = backfill_state(environment)
    assert result["state"] == "off"
    assert result["outcome_code"] is None
    assert result["progress"] is None
