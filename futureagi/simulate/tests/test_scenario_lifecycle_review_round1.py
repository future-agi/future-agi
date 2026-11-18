"""Regression tests for round 1 of the Feature 3 review: each reproduces a finding on the head it was raised on."""

import uuid
from unittest.mock import patch

import pytest
from django.core.cache import cache

from simulate.models import (
    HarnessAssembledBundle,
    HarnessSuiteValidation,
    HarnessSuiteVersion,
    HostedHarnessJob,
)
from simulate.services.harness_scenario_lifecycle import runnable, scenario_lifecycle
from simulate.services.harness_versioning import validation
from simulate.services.harness_versioning.versions import record_proofs
from simulate.services.hosted_harness import HostedHarnessError
from simulate.tests.test_harness_affected_scenarios import (
    LOOKUP_KEYS,
    PROVED,
    REFUND_KEYS,
    _built,
    _suite,
    _ten,
)
from simulate.tests.test_harness_readiness import _environment, _with_a2
from simulate.tests.test_harness_readiness import (  # noqa: F401 - fixture
    scheduled as scheduled,
)
from simulate.tests.test_harness_readiness import (  # noqa: F401 - fixture
    storage as storage,
)
from simulate.tests.test_harness_run_gate import _preview_next, _proving_jobs
from simulate.tests.test_harness_scenario_lifecycle import (
    _copy,
    _members_of,
    _proofs,
    _views,
)
from simulate.tests.test_harness_versioning_assembly import _row
from simulate.tests.test_harness_versioning_runs import _run

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
    # Each case's proof names the versions it was made with.
    proofs = {
        key: view["proof"]
        for key, view in scenario_lifecycle(
            environment, suite_version=suite, environment_version=version
        ).items()
    }
    assert proofs[KEYS[0]]["agent_version_id"] == str(a2.id)
    assert proofs[KEYS[1]]["agent_version_id"] == str(a1.id)
    assert proofs[KEYS[1]]["environment_version_id"] == str(version.id)
    assert proofs[KEYS[1]]["suite_version_id"] == str(suite.id)
    assert on_a2[KEYS[0]] == "active"
    assert all(on_a2[key] == "draft" for key in KEYS[1:])
    assert on_a1[KEYS[0]] == "draft"
    assert all(on_a1[key] == "active" for key in KEYS[1:])


LEAK = {
    "format": 2,
    "proved": False,
    "blocked_reason": "answer_leaked",
    "leaks": [
        {"who": "agent", "field": "opening", "excerpt": "R-1", "grader_field": "checks"}
    ],
}


def _leaky(environment):
    HarnessSuiteValidation.no_workspace_objects.filter(
        suite_version=environment.suite_version
    ).update(
        proofs={
            **dict.fromkeys(LOOKUP_KEYS, PROVED),
            **dict.fromkeys(REFUND_KEYS, LEAK),
        }
    )


def test_a_known_defect_is_refused_not_validated_again(user, workspace, storage):
    """B2 (a): a run of cases whose proof found a leaked answer is refused with that reason; it starts no validation,
    which would only repeat the verdict."""
    environment = _environment(user, workspace, storage, "rv1-leak-loop")
    _suite(environment, _ten())
    _leaky(environment)
    before = _proving_jobs(environment)

    assert _preview_next(environment, REFUND_KEYS) is None
    with pytest.raises(HostedHarnessError) as refused:
        _run(environment, REFUND_KEYS, key="leak-1")

    assert refused.value.code == "all_cases_blocked"
    assert refused.value.details["selection"]["held_back"]["by"] == {
        "proof_failed": len(REFUND_KEYS)
    }
    assert "gives the answer away" in refused.value.message
    assert "action" not in refused.value.details
    assert _proving_jobs(environment) == before


def test_a_known_defect_never_runs_as_unvalidated(user, workspace, storage):
    """B2 (b): when the pair's last check ended on our side, unvalidated cases may run; a case with a known defect
    still does not."""
    environment = _environment(user, workspace, storage, "rv1-leak-runs")
    _suite(environment, _ten())
    _leaky(environment)
    HarnessAssembledBundle._base_manager.filter(id=_row(environment).id).update(
        state="pending",
        object_key=None,
        error={
            "last_attempt": {"code": validation.CHECK_UNAVAILABLE, "template": "t:1"}
        },
    )
    _built(environment)
    with patch.object(validation, "_template", return_value="t:1"):
        assert _preview_next(environment, REFUND_KEYS) is None
        with pytest.raises(HostedHarnessError) as refused:
            _run(environment, REFUND_KEYS, key="leak-2")

    assert refused.value.code == "all_cases_blocked"


def test_a_legacy_holds_entry_never_counts_as_proved(user, workspace, storage):
    """Review round 1, SF4: an older entry's `holds` reads Kept as written, so its proof summary and counts never say
    proved."""
    from simulate.services.harness_scenario_lifecycle import lifecycle_counts
    from simulate.services.harness_versioning.versions import proof_passes

    assert proof_passes({"holds": True}) is False
    assert proof_passes({"format": 2, "proved": True}) is True
    environment = _environment(user, workspace, storage, "rv1-legacy-holds")
    _suite(environment, _ten(), proved=False)
    _proofs(environment, dict.fromkeys(KEYS, {"holds": True}))

    assert all(not view["proof"]["proved"] for view in _views(environment).values())
    assert lifecycle_counts(_views(environment).values())["proved"] == 0


def test_validation_unavailable_is_read_for_the_pairing_s_own_agent(
    user, workspace, storage
):
    """Review round 1, SF2: whether a case may run unvalidated depends on the pairing's agent, not the default."""
    environment, a2 = _with_a2(user, workspace, storage, "rv1-unavailable-agent")
    _suite(environment, _ten(), proved=False)
    suite, version = environment.suite_version, environment.environment_version

    def reason(agent):
        return scenario_lifecycle(
            environment,
            suite_version=suite,
            environment_version=version,
            agent_version_id=agent.id,
        )[KEYS[0]]["reason"]

    with patch.object(
        validation,
        "can_validate",
        side_effect=lambda env, agent_id: str(agent_id) != str(a2.id),
    ):
        assert reason(environment.agent_version) == "not_validated"
        assert reason(a2) == "validation_unavailable"
