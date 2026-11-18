"""Regression tests for round 1 of the Feature 3 review: each reproduces a finding on the head it was raised on."""

import uuid

import pytest
from django.core.cache import cache

from simulate.models import HarnessSuiteVersion, HostedHarnessJob
from simulate.services.harness_scenario_lifecycle import runnable, scenario_lifecycle
from simulate.services.harness_versioning.versions import record_proofs
from simulate.tests.test_harness_affected_scenarios import (
    LOOKUP_KEYS,
    PROVED,
    REFUND_KEYS,
    _suite,
    _ten,
)
from simulate.tests.test_harness_readiness import _environment, _with_a2
from simulate.tests.test_harness_readiness import (  # noqa: F401 - fixture
    storage as storage,
)
from simulate.tests.test_harness_scenario_lifecycle import (
    _copy,
    _members_of,
    _proofs,
    _views,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("readiness_on")]
KEYS = REFUND_KEYS + LOOKUP_KEYS


@pytest.mark.parametrize("blocked", ["no_negative_control", "negative_control_passed"])
def test_a_failed_legacy_reproof_is_not_inherited_by_the_next_suite_version(
    user, workspace, storage, blocked
):
    """B1: legacy v5's case re-proves and fails; v6 holds it unchanged with no proof of its own. It stays Draft."""
    environment = _environment(user, workspace, storage, "rv1-legacy-" + blocked[:8])
    _suite(environment, _ten(), proved=False)
    legacy = environment.suite_version
    HarnessSuiteVersion._base_manager.filter(id=legacy.id).update(lifecycle_legacy=True)
    legacy.refresh_from_db()
    environment.refresh_from_db()
    _proofs(environment, {REFUND_KEYS[0]: {"holds": True}})
    record_proofs(
        legacy,
        environment.environment_version,
        {
            REFUND_KEYS[0]: {
                "format": 2,
                "holds": True,
                "proved": False,
                "blocked_reason": blocked,
            }
        },
        None,
    )
    cache.clear()
    on_legacy = _views(environment)[REFUND_KEYS[0]]
    assert on_legacy["state"] == "draft" and not runnable(on_legacy)

    newer = _copy(legacy, HarnessSuiteVersion, lifecycle_legacy=False)
    _members_of(legacy, newer)

    on_newer = _views(environment, suite=newer)[REFUND_KEYS[0]]
    assert not runnable(on_newer), on_newer["state"]


def test_an_unreached_legacy_case_keeps_legacy_on_the_next_version(
    user, workspace, storage
):
    """B1, the case the mark is for: a re-proof that never reached the case keeps what legacy granted."""
    environment = _environment(user, workspace, storage, "rv1-legacy-unreached")
    _suite(environment, _ten(), proved=False)
    legacy = environment.suite_version
    HarnessSuiteVersion._base_manager.filter(id=legacy.id).update(lifecycle_legacy=True)
    legacy.refresh_from_db()
    environment.refresh_from_db()
    _proofs(environment, {REFUND_KEYS[0]: {"holds": True}})
    record_proofs(
        legacy,
        environment.environment_version,
        {REFUND_KEYS[0]: {"format": 2, "blocked_reason": "validation_incomplete"}},
        None,
    )
    cache.clear()
    newer = _copy(legacy, HarnessSuiteVersion, lifecycle_legacy=False)
    _members_of(legacy, newer)

    on_newer = _views(environment, suite=newer)[REFUND_KEYS[0]]
    assert (on_newer["state"], on_newer["legacy"]) == ("kept_as_written", True)


def _job_for(environment, agent):
    fields = {
        one.name: getattr(environment, one.name)
        for one in HostedHarnessJob._meta.concrete_fields
        if one.name not in ("id", "created_at", "updated_at")
    }
    fields.update(
        agent_version=agent,
        environment_id=environment.id,
        run_id=uuid.uuid4(),
        idempotency_key=str(uuid.uuid4()),
    )
    return HostedHarnessJob._base_manager.create(**fields)


def test_a_partial_validation_keeps_each_case_s_own_agent(user, workspace, storage):
    """B3: every case proved with A1, then one case validated with A2. Only that case counts for A2; the rest still
    count for A1."""
    environment, a2 = _with_a2(user, workspace, storage, "rv1-agent")
    _suite(environment, _ten())
    a1 = environment.agent_version
    suite, version = environment.suite_version, environment.environment_version
    record_proofs(
        suite, version, dict.fromkeys(KEYS, PROVED), _job_for(environment, a1)
    )
    record_proofs(suite, version, {KEYS[0]: PROVED}, _job_for(environment, a2))
    cache.clear()

    def states(agent):
        views = scenario_lifecycle(
            environment,
            suite_version=suite,
            environment_version=version,
            agent_version_id=agent.id,
        )
        return {key: view["state"] for key, view in views.items()}

    on_a2, on_a1 = states(a2), states(a1)
    assert on_a2[KEYS[0]] == "active"
    assert all(on_a2[key] == "draft" for key in KEYS[1:])
    assert on_a1[KEYS[0]] == "draft"
    assert all(on_a1[key] == "active" for key in KEYS[1:])
