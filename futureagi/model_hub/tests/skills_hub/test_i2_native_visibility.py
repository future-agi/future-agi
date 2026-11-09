"""Ordinary Simulation selectors never enumerate Skills-owned native evidence."""

import pytest

from model_hub.tests.skills_hub.r3_l04 import conftest as native_fixtures
from model_hub.tests.skills_hub.r3_l04 import test_native_privacy as fixtures
from simulate.models.hosted_harness import HostedHarnessJob
from simulate.selectors.runs import user_runs

pytestmark = pytest.mark.django_db
native_configuration = native_fixtures.native_configuration
evidence = fixtures.evidence


@pytest.mark.parametrize("visibility", ["private", "workspace"])
def test_ordinary_simulation_count_excludes_native_even_when_shared(
    evidence, visibility
):
    evidence.visibility = visibility
    evidence.save(update_fields=["visibility"])
    assert (
        HostedHarnessJob.no_workspace_objects.filter(
            pk=evidence.simulation_job_id
        ).count()
        == 1
    )
    assert (
        HostedHarnessJob.no_workspace_objects.filter(
            user_runs(), pk=evidence.simulation_job_id
        ).count()
        == 0
    )
