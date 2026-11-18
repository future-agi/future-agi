"""The Scenarios tab counts proved cases apart from cases kept as written."""

import pytest
from django.utils import timezone

from simulate.models import HarnessSuiteValidation
from simulate.services.harness_versioning.versions import proof_counts
from simulate.tests.test_harness_affected_scenarios import (
    LOOKUP_KEYS,
    REFUND_KEYS,
    _suite,
    _ten,
)
from simulate.tests.test_harness_readiness import _environment
from simulate.tests.test_harness_readiness import (  # noqa: F401 - fixture
    storage as storage,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("readiness_on")]


def test_proof_counts_on_list(user, workspace, storage):
    environment = _environment(user, workspace, storage, "proof-counts")
    _suite(environment, _ten())
    proofs = {key: {"format": 2, "holds": True, "proved": True} for key in REFUND_KEYS}
    proofs[LOOKUP_KEYS[0]] = {
        "format": 2,
        "holds": True,
        "proved": False,
        "kept_as_written": True,
    }
    proofs[LOOKUP_KEYS[1]] = {"holds": True}
    proofs[LOOKUP_KEYS[2]] = {
        "format": 2,
        "holds": False,
        "proved": False,
        "blocked_reason": "not_solvable",
    }
    HarnessSuiteValidation.no_workspace_objects.update_or_create(
        suite_version=environment.suite_version,
        environment_version=environment.environment_version,
        defaults={"status": "partial", "proved_at": timezone.now(), "proofs": proofs},
    )

    counts = proof_counts(environment)

    assert counts == {"proved": 5, "kept_as_written": 1, "not_proved": 4}


def test_judged_only_counts_as_kept(user, workspace, storage):
    from simulate.services.harness_scenario_lifecycle import (
        lifecycle_counts,
        scenario_lifecycle,
    )

    environment = _environment(user, workspace, storage, "proof-counts-judged")
    _suite(environment, _ten())
    proofs = {
        key: {"format": 2, "holds": True, "proved": True}
        for key in REFUND_KEYS + LOOKUP_KEYS
    }
    proofs[LOOKUP_KEYS[0]] = {
        "format": 2,
        "holds": True,
        "proved": False,
        "kept_as_written": True,
        "judged_only": True,
        "blocked_reason": "judged_only",
    }
    HarnessSuiteValidation.no_workspace_objects.update_or_create(
        suite_version=environment.suite_version,
        environment_version=environment.environment_version,
        defaults={"status": "partial", "proved_at": timezone.now(), "proofs": proofs},
    )

    assert proof_counts(environment)["kept_as_written"] == 1
    views = scenario_lifecycle(
        environment,
        suite_version=environment.suite_version,
        environment_version=environment.environment_version,
    )
    counts = lifecycle_counts(views.values())
    assert (counts["kept_as_written"], counts["proved"]) == (1, len(proofs) - 1)
