"""A run sends only cases validated on its own pair, and a pair with no proofs is validated first."""

from unittest.mock import patch

import pytest
from django.utils import timezone

from simulate.models import (
    HarnessAssembledBundle,
    HarnessSuiteValidation,
    HostedHarnessJob,
    HostedHarnessScenario,
)
from simulate.services.harness_versioning import validation
from simulate.services.hosted_harness import HostedHarnessError
from simulate.tests.test_harness_affected_scenarios import (
    LOOKUP_KEYS,
    PROVED,
    REFUND_KEYS,
    _built,
    _environment,
    _suite,
    _ten,
)
from simulate.tests.test_harness_readiness import (  # noqa: F401 - fixture
    readiness_on as readiness_on,
)
from simulate.tests.test_harness_readiness import (  # noqa: F401 - fixture
    scheduled as scheduled,
)
from simulate.tests.test_harness_readiness import (  # noqa: F401 - fixture
    storage as storage,
)
from simulate.tests.test_harness_versioning_assembly import _row
from simulate.tests.test_harness_versioning_runs import _run

pytestmark = pytest.mark.django_db
KEYS = REFUND_KEYS + LOOKUP_KEYS


def _without_proofs(environment):
    HarnessSuiteValidation.no_workspace_objects.filter(
        suite_version=environment.suite_version
    ).delete()


def _proving_jobs(environment):
    return list(
        HostedHarnessJob.no_workspace_objects.filter(
            environment_id=environment.id, payload__metadata__certify_only=True
        ).values_list("payload__metadata__scenario_proving", flat=True)
    )


def test_never_certified_pair_starts_validation(user, workspace, storage):
    environment = _environment(user, workspace, storage, "gate-never")
    _suite(environment, _ten())
    _without_proofs(environment)
    HarnessAssembledBundle._base_manager.filter(id=_row(environment).id).update(
        state="pending", object_key=None
    )
    _built(environment)
    before = _proving_jobs(environment)

    with pytest.raises(HostedHarnessError) as refused:
        _run(environment, KEYS, key="never")

    assert (refused.value.code, refused.value.status_code) == ("pair_validating", 409)
    assert refused.value.details["retry_after"] >= 60
    assert _proving_jobs(environment)[len(before) :] == ["with_certify"]


def test_certified_pair_without_proofs_starts_prove_job(user, workspace, storage):
    environment = _environment(user, workspace, storage, "gate-certified")
    _suite(environment, _ten())
    _without_proofs(environment)

    with pytest.raises(HostedHarnessError) as refused:
        _run(environment, KEYS, key="certified")

    assert refused.value.code == "pair_validating"
    assert "only" in _proving_jobs(environment)


def test_some_cases_proved_run_those(user, workspace, storage):
    environment = _environment(user, workspace, storage, "gate-some")
    _suite(environment, _ten())
    HarnessSuiteValidation.no_workspace_objects.filter(
        suite_version=environment.suite_version
    ).update(proofs=dict.fromkeys(LOOKUP_KEYS, PROVED))

    run = _run(environment, KEYS, key="some")

    chosen = run.payload["metadata"]["readiness_selection"]
    assert chosen["held_back"]["by"] == {"not_validated": 4}
    assert chosen["running"] == 6


def test_all_cases_blocked_names_the_action_only_for_validation(
    user, workspace, storage
):
    from simulate.services.harness_readiness import selection

    environment = _environment(user, workspace, storage, "gate-action")
    _suite(environment, _ten())
    HarnessSuiteValidation.no_workspace_objects.filter(
        suite_version=environment.suite_version
    ).update(proofs=dict.fromkeys(LOOKUP_KEYS, PROVED))
    with patch.object(validation, "live_check", return_value=object()):
        with pytest.raises(HostedHarnessError) as refused:
            _run(environment, REFUND_KEYS, key="unvalidated")
    assert refused.value.code == "all_cases_blocked"
    assert refused.value.details["action"] == "validate_scenarios"
    assert refused.value.message.endswith("Validate scenarios to run them.")

    HarnessSuiteValidation.no_workspace_objects.filter(
        suite_version=environment.suite_version
    ).update(proofs=dict.fromkeys(KEYS, PROVED))

    HostedHarnessScenario.no_workspace_objects.filter(
        job=environment, scenario_key__in=REFUND_KEYS
    ).update(archived_at=timezone.now())
    with pytest.raises(HostedHarnessError) as archived:
        _run(environment, REFUND_KEYS, key="archived")
    assert archived.value.code == "all_cases_blocked"
    assert "action" not in archived.value.details
    assert (
        selection.ARCHIVED_ROW in archived.value.details["selection"]["held_back"]["by"]
    )


def test_check_unavailable_reads_validation_unavailable(user, workspace, storage):
    environment = _environment(user, workspace, storage, "gate-unavailable")
    _suite(environment, _ten())
    _without_proofs(environment)
    bundle = HarnessAssembledBundle._base_manager.get(id=_row(environment).id)

    with patch.object(
        validation,
        "start_validation",
        return_value=validation.Validation(
            validation.CHECK_UNAVAILABLE, bundle, "prove"
        ),
    ):
        run = _run(environment, KEYS, key="unavailable")

    assert run.payload["metadata"]["readiness_selection"]["running"] == len(KEYS)


def _lifecycle(state, reason=None):
    return {"state": state, "reason": reason, "reason_text": None}


def test_default_pair_runs_only_active():
    from simulate.services.harness_scenario_lifecycle import runnable

    assert runnable(_lifecycle("active"), default_pair=True) is True
    assert runnable(_lifecycle("validated"), default_pair=True) is False
    assert runnable(_lifecycle("draft", "not_validated"), default_pair=True) is False
    assert (
        runnable(_lifecycle("draft", "validation_unavailable"), default_pair=True)
        is True
    )


def test_candidate_pair_runs_validated():
    from simulate.services.harness_scenario_lifecycle import runnable

    assert runnable(_lifecycle("validated"), default_pair=False) is True
    assert runnable(_lifecycle("draft", "in_draft"), default_pair=False) is False


def test_kept_as_written_still_runs():
    from simulate.services.harness_scenario_lifecycle import runnable

    assert runnable(_lifecycle("kept_as_written"), default_pair=True) is True
    assert runnable(_lifecycle("kept_as_written"), default_pair=False) is True
    assert runnable(_lifecycle("archived"), default_pair=False) is False


def test_the_dialog_preview_holds_back_what_the_run_holds_back(
    user, workspace, storage
):
    from simulate.services.harness_readiness import selection
    from simulate.services.harness_readiness.report import resolve_pairing

    environment = _environment(user, workspace, storage, "gate-preview")
    _suite(environment, _ten())
    HarnessSuiteValidation.no_workspace_objects.filter(
        suite_version=environment.suite_version
    ).update(proofs=dict.fromkeys(LOOKUP_KEYS, PROVED))
    HostedHarnessScenario.no_workspace_objects.filter(
        job=environment, scenario_key=LOOKUP_KEYS[0]
    ).update(archived_at=timezone.now())

    preview = selection.selection_report(
        resolve_pairing(environment), keys=KEYS, trials=1
    )["selection"]
    run = _run(environment, KEYS, key="preview")
    sent = run.payload["metadata"]["readiness_selection"]

    assert (
        preview["held_back"]["by"]
        == sent["held_back"]["by"]
        == {
            "not_validated": 4,
            selection.ARCHIVED_ROW: 1,
        }
    )
    assert sorted(preview["held_back"]["keys"]) == sorted(sent["held_back"]["keys"])
    assert preview["running"] == sent["running"] == 5
    assert (
        preview["reasons"][selection.ARCHIVED_ROW]
        == sent["reasons"][selection.ARCHIVED_ROW]
    )


def test_the_preview_says_why_each_held_back_case_cannot_run(user, workspace, storage):
    from simulate.services.harness_readiness import selection
    from simulate.services.harness_readiness.report import resolve_pairing
    from simulate.services.harness_scenario_lifecycle import REASON_TEXTS

    environment = _environment(user, workspace, storage, "gate-reasons")
    _suite(environment, _ten())
    failing = {"format": 2, "proved": False}
    HarnessSuiteValidation.no_workspace_objects.filter(
        suite_version=environment.suite_version
    ).update(
        proofs={
            **dict.fromkeys(LOOKUP_KEYS, PROVED),
            REFUND_KEYS[0]: {**failing, "blocked_reason": "no_negative_control"},
            REFUND_KEYS[1]: {**failing, "blocked_reason": "vacuous"},
        }
    )

    preview = selection.selection_report(
        resolve_pairing(environment), keys=KEYS, trials=1
    )["selection"]

    shown = preview["reasons"][selection.NOT_VALIDATED_ROW]
    assert REASON_TEXTS["no_negative_control"] in shown
    assert REASON_TEXTS["vacuous"] in shown
