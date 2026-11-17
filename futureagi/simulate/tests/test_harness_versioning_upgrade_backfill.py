"""A retried upgrade repairs either missing pin without duplicating versions."""

import pytest

from simulate.models import HarnessEnvironmentVersion, HarnessSuiteVersion
from simulate.services.harness_versioning import backfill, versions

pytestmark = pytest.mark.django_db


@pytest.fixture
def backfilled_environment(user, workspace, monkeypatch, settings):
    from simulate.tests.test_harness_versioning_backfill import (
        _legacy,
        _RealisticStorage,
    )
    from tfc.utils import storage_client

    settings.HARNESS_VERSIONING_ENABLED = True
    storage = _RealisticStorage()
    monkeypatch.setattr(storage_client, "_client", storage)
    environment = _legacy(user, workspace, "upgrade-partial-pins", storage)
    result = backfill.backfill_environment(environment.id)
    assert result.outcome not in backfill.FAILED_OUTCOMES
    environment.refresh_from_db()
    assert environment.environment_version_id is not None
    assert environment.suite_version_id is not None
    return environment


@pytest.mark.parametrize(
    ("keep_environment", "keep_suite"),
    [(False, False), (False, True), (True, False), (True, True)],
)
def test_current_backfill_requires_both_pins(
    backfilled_environment, keep_environment, keep_suite
):
    environment = backfilled_environment
    if not keep_environment:
        environment.environment_version_id = None
    if not keep_suite:
        environment.suite_version_id = None
    environment.save(update_fields=["environment_version", "suite_version"])
    revision = (environment.payload.get("metadata") or {}).get("authoring_revision", "")
    assert backfill._unchanged(backfill.default_models(), environment, revision) is (
        keep_environment and keep_suite
    )


@pytest.mark.parametrize(
    ("keep_environment", "keep_suite"),
    [(False, False), (False, True), (True, False)],
)
def test_resume_restores_missing_pins_and_reuses_the_recorded_versions(
    backfilled_environment, keep_environment, keep_suite
):
    environment = backfilled_environment
    expected_environment = environment.environment_version_id
    expected_suite = environment.suite_version_id
    environment_count = HarnessEnvironmentVersion.no_workspace_objects.filter(
        job=environment
    ).count()
    suite_count = HarnessSuiteVersion.no_workspace_objects.filter(
        job=environment
    ).count()
    if not keep_environment:
        environment.environment_version_id = None
    if not keep_suite:
        environment.suite_version_id = None
    environment.save(update_fields=["environment_version", "suite_version"])
    assert versions.pins_runs(environment) is False

    result = backfill.backfill_environment(environment.id)
    assert result.outcome not in backfill.FAILED_OUTCOMES
    environment.refresh_from_db()
    assert environment.environment_version_id == expected_environment
    assert environment.suite_version_id == expected_suite
    assert versions.pins_runs(environment) is True
    assert (
        HarnessEnvironmentVersion.no_workspace_objects.filter(job=environment).count()
        == environment_count
    )
    assert (
        HarnessSuiteVersion.no_workspace_objects.filter(job=environment).count()
        == suite_count
    )

    again = backfill.backfill_environment(environment.id)
    assert again.outcome == "current"
    environment.refresh_from_db()
    assert environment.environment_version_id == expected_environment
    assert environment.suite_version_id == expected_suite
