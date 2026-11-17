"""Readiness remains pins-based while backfill status is scoped and honest."""

from uuid import uuid4

import pytest
from django.utils import timezone

from simulate.models import HarnessVersioningBackfillOutcome as Outcome
from simulate.models import HarnessVersioningBackfillProgress as Progress
from simulate.models import HostedHarnessJob
from simulate.services.harness_provider import serialize_job
from simulate.services.harness_versioning.backfill import backfill_environment
from simulate.services.hosted_harness_gateway import rewrite_authoring_scenarios
from simulate.tests import test_harness_versioning_record as record_tests
from simulate.tests.test_harness_amend_archive import NAMES
from simulate.tests.test_harness_versioning_backfill import (
    _legacy,
    _RealisticStorage,
    _run,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def environment(user, workspace, monkeypatch, settings):
    from tfc.utils import storage_client

    settings.HARNESS_VERSIONING_ENABLED = True
    storage = _RealisticStorage()
    monkeypatch.setattr(storage_client, "_client", storage)
    return _legacy(user, workspace, "upgrade-api", storage)


def seed_state(environment, state):
    if state == "not_started":
        return
    token = uuid4()
    Progress.objects.create(
        organization_id=environment.organization_id,
        run_token=token,
        state=state,
        total=1,
        processed=int(state == "failed"),
        failed=int(state == "failed"),
        heartbeat_at=timezone.now(),
    )
    Outcome.objects.create(
        environment=environment,
        run_token=token,
        state=state,
        outcome_code="error" if state == "failed" else "",
    )


@pytest.mark.parametrize("state", ["not_started", "running", "failed"])
def test_detail_exposes_backfill_without_making_unpinned_environment_ready(
    environment, auth_client, state
):
    seed_state(environment, state)
    response = auth_client.get(f"/simulate/api/harness-jobs/{environment.id}/")
    assert response.status_code == 200
    flags = response.json()["versioning"]
    assert flags["enabled"] is True
    assert flags["state"] == "enabling"
    assert flags["backfill"]["state"] == state
    assert set(flags["backfill"]) == {"state", "outcome_code", "updated_at", "progress"}
    if state != "not_started":
        assert set(flags["backfill"]["progress"]) == {
            "state",
            "total",
            "processed",
            "succeeded",
            "failed",
            "skipped",
            "heartbeat_at",
            "updated_at",
        }


@pytest.mark.parametrize("state", ["not_started", "running", "failed"])
def test_legacy_run_and_edit_work_with_versioning_on_before_backfill_finishes(
    environment, state
):
    from tfc.utils import storage_client

    seed_state(environment, state)
    storage = storage_client._client
    old_key = environment.payload["metadata"]["authoring_object_key"]
    old_archive = storage.objects[old_key]
    run = _run(environment, key=f"upgrade-legacy-{state}")
    assert run.environment_id == environment.id
    assert run.environment_version_id is None
    assert run.suite_version_id is None
    assert serialize_job(environment)["versioning"]["backfill"]["state"] == state

    new_key = rewrite_authoring_scenarios(
        environment,
        [
            record_tests._scenario(NAMES[0], background_noise="street"),
            record_tests._scenario(NAMES[1]),
        ],
    )
    environment.refresh_from_db()
    assert new_key and new_key != old_key
    assert environment.payload["metadata"]["authoring_object_key"] == new_key
    assert storage.objects[new_key] != old_archive
    assert storage.objects[old_key] == old_archive
    # is_versioned checks existing pins. The legacy archive-write branch stays
    # usable before the worker finishes; an edit is not itself a backfill.
    assert environment.environment_version_id is None
    assert environment.suite_version_id is None
    flags = serialize_job(environment)["versioning"]
    assert flags["state"] == "enabling"
    assert flags["backfill"]["state"] == state

    edited_revision = environment.payload["metadata"]["authoring_revision"]
    assert backfill_environment(environment.id).outcome == "versioned"
    environment.refresh_from_db()
    assert environment.environment_version_id is not None
    assert environment.suite_version_id is not None
    assert (
        edited_revision
        in environment.environment_version.documents["authoring_revisions"]
    )
    assert edited_revision in environment.suite_version.documents["authoring_revisions"]
    assert serialize_job(environment)["versioning"]["state"] == "ready"
    # A later run uses the defaults derived from the edited archive, not the
    # earlier legacy run's archive. Backfill never overwrites that old object.
    after = _run(environment, key=f"upgrade-after-backfill-{state}")
    assert after.environment_version_id == environment.environment_version_id
    assert after.suite_version_id == environment.suite_version_id
    assert storage.objects[old_key] == old_archive


def test_ready_environment_stays_usable_despite_failed_worker(environment, auth_client):
    assert backfill_environment(environment.id).outcome == "versioned"
    environment.refresh_from_db()
    seed_state(environment, "failed")
    flags = serialize_job(environment)["versioning"]
    assert flags["state"] == "ready"
    assert flags["backfill"]["state"] == "failed"
    response = auth_client.get(
        f"/simulate/api/harness-environments/{environment.id}/versions/"
    )
    assert response.status_code == 200
    assert response.json()["environment"]
    assert response.json()["suite"]


def test_explicit_off_wins_over_old_failure(environment, auth_client, settings):
    seed_state(environment, "failed")
    settings.HARNESS_VERSIONING_ENABLED = False
    response = auth_client.get(f"/simulate/api/harness-jobs/{environment.id}/")
    assert response.status_code == 200
    flags = response.json()["versioning"]
    assert flags["state"] == "off"
    assert flags["backfill"] == {
        "state": "off",
        "outcome_code": None,
        "updated_at": None,
        "progress": None,
    }
    versions = auth_client.get(
        f"/simulate/api/harness-environments/{environment.id}/versions/"
    )
    assert versions.status_code == 404
    assert versions.json()["error"] == "versioning_disabled"


def test_detail_does_not_expose_foreign_organization_progress(environment, auth_client):
    from accounts.models import Organization

    foreign = Organization.objects.create(name="Foreign upgrade organization")
    HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
        organization=foreign
    )
    environment.refresh_from_db()
    seed_state(environment, "failed")
    response = auth_client.get(f"/simulate/api/harness-jobs/{environment.id}/")
    assert response.status_code == 404
    assert "versioning" not in response.json()
