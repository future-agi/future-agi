"""Validations write the release checks a reference passes and a wrong answer fails."""

import pytest
from django.utils import timezone

from simulate.models import (
    HarnessEnvironmentVersion,
    HarnessSuiteMember,
    HarnessSuiteValidation,
)
from simulate.services.harness_readiness.release import (
    record_proof_release_checks,
    record_release_check,
    release_checks,
)
from simulate.tests.test_harness_affected_scenarios import _suite, _ten
from simulate.tests.test_harness_readiness import _environment
from simulate.tests.test_harness_readiness import (  # noqa: F401 - fixture
    storage as storage,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("readiness_on")]
FAILED_CONTROL = {"name": "twice", "failed": True, "assumed": []}


def _entry(**fields):
    return {
        "format": 2,
        "holds": True,
        "proved": True,
        "ready": True,
        "solvable": True,
        "assumed": [],
        **fields,
    }


def _record(environment, proofs):
    HarnessSuiteValidation.no_workspace_objects.update_or_create(
        suite_version=environment.suite_version,
        environment_version=environment.environment_version,
        defaults={"status": "passed", "proved_at": timezone.now(), "proofs": proofs},
    )
    record_proof_release_checks(environment.environment_version)
    version = HarnessEnvironmentVersion._base_manager.get(
        id=environment.environment_version_id
    )
    return release_checks(version)["checks"]


def _two_cases(environment):
    _suite(environment, _ten()[:2], proved=False)
    keys = list(
        HarnessSuiteMember.no_workspace_objects.filter(
            suite_version=environment.suite_version
        ).values_list("scenario_key", flat=True)
    )
    return keys


def test_reference_and_wrong_answer_written(user, workspace, storage):
    environment = _environment(user, workspace, storage, "rc-written")
    first, second = _two_cases(environment)

    passed = _record(
        environment,
        {
            first: _entry(outcome="change", controls=[FAILED_CONTROL]),
            second: _entry(outcome="no_change", controls=[FAILED_CONTROL]),
        },
    )
    assert (passed["reference_passes"], passed["wrong_answer_fails"]) == (
        "passed",
        "passed",
    )

    failed = _record(
        environment,
        {
            first: _entry(
                solvable=False,
                proved=False,
                controls=[{"name": "twice", "failed": False, "assumed": []}],
            ),
            second: _entry(outcome="no_change", controls=[FAILED_CONTROL]),
        },
    )
    assert (failed["reference_passes"], failed["wrong_answer_fails"]) == (
        "failed",
        "failed",
    )

    pending = _record(
        environment,
        {
            first: _entry(
                assumed=["place_order"],
                proved=False,
                outcome="change",
                controls=[FAILED_CONTROL],
            ),
            second: {"holds": True},
        },
    )
    assert (pending["reference_passes"], pending["wrong_answer_fails"]) == (
        "pending",
        "pending",
    )

    # Every case has a format-2 proof, but one solution never ran: the reference is not
    # shown to pass (LC-4).
    assumed = _record(
        environment,
        {
            first: _entry(
                assumed=["place_order"],
                proved=False,
                kept_as_written=True,
                outcome="change",
                controls=[FAILED_CONTROL],
            ),
            second: _entry(outcome="no_change", controls=[FAILED_CONTROL]),
        },
    )
    assert assumed["reference_passes"] == "pending"


def test_record_release_check_keeps_other_keys(user, workspace, storage):
    environment = _environment(user, workspace, storage, "rc-keys")
    version = environment.environment_version
    HarnessEnvironmentVersion._base_manager.filter(id=version.id).update(
        documents={
            **(version.documents or {}),
            "release_checks": {"hidden_checks_kept": "passed"},
        }
    )

    record_release_check(version.id, "reference_passes", "passed", job_id=None)
    written = HarnessEnvironmentVersion._base_manager.get(id=version.id).documents[
        "release_checks"
    ]
    assert written == {"hidden_checks_kept": "passed", "reference_passes": "passed"}

    record_release_check(version.id, "reference_passes", None, job_id=None)
    cleared = HarnessEnvironmentVersion._base_manager.get(id=version.id).documents[
        "release_checks"
    ]
    assert cleared == {"hidden_checks_kept": "passed"}
