"""Validate scenarios: prove every case of a pair on demand, and read where it got to."""

import uuid
from unittest.mock import patch

import pytest
from django.utils import timezone

from simulate.models import HostedHarnessJob
from simulate.services.harness_versioning import validation
from simulate.tests.test_harness_affected_scenarios import _suite, _ten
from simulate.tests.test_harness_readiness import _environment
from simulate.tests.test_harness_readiness import (  # noqa: F401 - fixture
    scheduled as scheduled,
)
from simulate.tests.test_harness_readiness import (  # noqa: F401 - fixture
    storage as storage,
)
from simulate.tests.test_harness_versioning_assembly import _row

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("readiness_on")]
BASE = "/simulate/api/harness-environments"


def _post(client, environment, body=None):
    return client.post(
        f"{BASE}/{environment.id}/scenarios/validate/", body or {}, format="json"
    )


def _get(client, environment, job_id):
    return client.get(
        f"{BASE}/{environment.id}/scenarios/validate/", {"job_id": str(job_id)}
    )


@pytest.fixture
def certified(user, workspace, storage):
    environment = _environment(user, workspace, storage, "va-certified")
    # No case proved yet, so a Validate proves them all.
    _suite(environment, _ten(), proved=False)
    return environment


def test_a_prove_job_starts_and_a_live_one_is_reported(auth_client, certified):
    first = _post(auth_client, certified)
    again = _post(auth_client, certified)

    assert first.status_code == 202, first.content
    assert first.json()["state"] == "validating"
    job = HostedHarnessJob.no_workspace_objects.get(id=first.json()["job_id"])
    assert job.payload["metadata"]["scenario_proving"] == "only"
    assert (again.status_code, again.json()["job_id"]) == (202, first.json()["job_id"])


def test_ready_only_with_if_fresh_after_the_last_change(auth_client, certified):
    started = _post(auth_client, certified).json()
    HostedHarnessJob.no_workspace_objects.filter(id=started["job_id"]).update(
        state=HostedHarnessJob.State.COMPLETED, updated_at=timezone.now()
    )

    fresh = _post(auth_client, certified, {"if_fresh": True})
    anew = _post(auth_client, certified)

    assert (fresh.status_code, fresh.json()["state"]) == (200, "ready")
    assert anew.status_code == 202 and anew.json()["job_id"] != started["job_id"]


def test_unavailable_check_is_reported(auth_client, certified):
    bundle = _row(certified)
    with patch.object(
        validation,
        "start_validation",
        return_value=validation.Validation(
            validation.CHECK_UNAVAILABLE, bundle, "prove"
        ),
    ):
        response = _post(auth_client, certified)
    assert (response.status_code, response.json()["state"]) == (200, "unavailable")
    assert response.json()["message"]


def test_refusals(auth_client, certified, settings):
    foreign = _post(auth_client, certified, {"suite_version_id": str(uuid.uuid4())})
    assert (foreign.status_code, foreign.json()["error"]) == (
        422,
        "version_not_pickable",
    )

    with patch.object(validation, "can_validate", return_value=False):
        cannot = _post(auth_client, certified)
    assert (cannot.status_code, cannot.json()["error"]) == (422, "cannot_validate")

    settings.HARNESS_VERSIONING_ENABLED = False
    disabled = _post(auth_client, certified)
    assert (disabled.status_code, disabled.json()["error"]) == (
        404,
        "versioning_disabled",
    )


def test_status_reads_validating_finished_and_failed(
    auth_client, certified, user, workspace, storage
):
    started = _post(auth_client, certified).json()
    job_id = started["job_id"]
    assert _get(auth_client, certified, job_id).json()["state"] == "validating"

    job = HostedHarnessJob.no_workspace_objects.get(id=job_id)
    job.payload["metadata"]["scenario_proofs_recorded"] = True
    HostedHarnessJob.no_workspace_objects.filter(id=job_id).update(
        state=HostedHarnessJob.State.COMPLETED, payload=job.payload
    )
    finished = _get(auth_client, certified, job_id).json()
    assert (finished["state"], finished["proofs_recorded"], finished["message"]) == (
        "finished",
        True,
        None,
    )

    HostedHarnessJob.no_workspace_objects.filter(id=job_id).update(
        state=HostedHarnessJob.State.FAILED,
        failure={"code": "authoring_runtime_validation_failed"},
    )
    failed = _get(auth_client, certified, job_id).json()
    assert failed["state"] == "failed" and failed["message"]

    other = _environment(user, workspace, storage, "va-other")
    assert _get(auth_client, other, job_id).status_code == 404


def test_validate_one_key_keeps_other_proofs(auth_client, certified):
    from simulate.models import HarnessSuiteValidation
    from simulate.services.harness_versioning.assembly import prove_suite_command
    from simulate.services.harness_versioning.versions import record_proofs
    from simulate.tests.test_harness_affected_scenarios import (
        LOOKUP_KEYS,
        PROVED,
        REFUND_KEYS,
    )

    started = _post(auth_client, certified, {"keys": [REFUND_KEYS[0]]})

    assert started.status_code == 202, started.content
    job = HostedHarnessJob.no_workspace_objects.get(id=started.json()["job_id"])
    assert job.payload["metadata"]["scenario_keys"] == [REFUND_KEYS[0]]
    command = prove_suite_command(keys=job.payload["metadata"]["scenario_keys"])
    assert f"--keys {REFUND_KEYS[0]}" in command
    # The proving writes only the named key; recording merges it over the pair's other proofs.
    record_proofs(
        certified.suite_version,
        certified.environment_version,
        dict.fromkeys(REFUND_KEYS + LOOKUP_KEYS, PROVED),
        None,
    )
    renewed = {**PROVED, "proved_at": "2026-11-15T00:00:00Z"}
    record_proofs(
        certified.suite_version,
        certified.environment_version,
        {REFUND_KEYS[0]: renewed},
        None,
    )
    proofs = HarnessSuiteValidation.no_workspace_objects.get(
        suite_version=certified.suite_version,
        environment_version=certified.environment_version,
    ).proofs
    assert proofs[REFUND_KEYS[0]]["proved_at"] == "2026-11-15T00:00:00Z"
    assert set(REFUND_KEYS + LOOKUP_KEYS) <= set(proofs)


def test_bad_key_is_refused(auth_client, certified):
    for keys in (["has space"], ["x;rm -rf /"], [], [f"k{n}" for n in range(51)]):
        refused = _post(auth_client, certified, {"keys": keys})
        assert refused.status_code == 400, (keys, refused.content)
    assert not HostedHarnessJob.no_workspace_objects.filter(
        payload__metadata__scenario_keys__isnull=False
    ).exists()


def test_a_check_that_ended_on_our_side_says_so_when_read_again(auth_client, certified):
    job_id = _post(auth_client, certified).json()["job_id"]
    job = HostedHarnessJob.no_workspace_objects.get(id=job_id)
    bundle = _row(certified)
    bundle.error = {
        "last_attempt": {"code": validation.CHECK_UNAVAILABLE, "job_id": job_id}
    }
    bundle.save(update_fields=["error"])
    HostedHarnessJob.no_workspace_objects.filter(id=job_id).update(
        state=HostedHarnessJob.State.FAILED,
        assembled_bundle_id=bundle.id,
        failure={"code": "authoring_runtime_validation_failed"},
    )

    read = _get(auth_client, certified, job_id).json()

    assert job.state != HostedHarnessJob.State.FAILED
    assert (read["state"], read["message"]) == (
        "unavailable",
        validation.UNAVAILABLE_REASON,
    )


def test_starting_a_check_that_would_end_on_our_side_says_so(auth_client, certified):
    with patch.object(
        validation,
        "start_validation",
        return_value=validation.Validation(
            validation.CHECK_UNAVAILABLE, _row(certified), "prove"
        ),
    ):
        response = _post(auth_client, certified)

    assert response.json()["message"] == validation.UNAVAILABLE_REASON


@pytest.mark.parametrize(
    ("ours", "reason", "why"),
    [
        (
            False,
            "validation_failed",
            "the agent did not pass this environment's checks",
        ),
        (True, "validation_ended_on_our_side", validation.UNAVAILABLE_REASON),
    ],
)
def test_a_failed_validation_still_says_why_after_a_reload_until_a_newer_one(
    auth_client, certified, ours, reason, why
):
    from simulate.models import HarnessAssembledBundle, HarnessSuiteValidation
    from simulate.services.harness_scenario_lifecycle import scenario_lifecycle

    HarnessSuiteValidation.no_workspace_objects.filter(
        suite_version_id=certified.suite_version_id
    ).delete()
    job_id = _post(auth_client, certified).json()["job_id"]
    HostedHarnessJob.no_workspace_objects.filter(id=job_id).update(
        state=HostedHarnessJob.State.FAILED,
        failure={"code": "authoring_runtime_validation_failed"},
    )
    if ours:
        HarnessAssembledBundle._base_manager.filter(id=_row(certified).id).update(
            error={
                "last_attempt": {"code": validation.CHECK_UNAVAILABLE, "job_id": job_id}
            }
        )

    def unproved():
        certified.refresh_from_db()
        return scenario_lifecycle(
            certified,
            suite_version=certified.suite_version,
            environment_version=certified.environment_version,
        ).values()

    views = list(unproved())
    assert views and all(view["reason"] == reason for view in views)
    assert all(why in view["reason_text"] for view in views)

    _post(auth_client, certified)
    assert {view["reason"] for view in unproved()} == {"not_validated"}


@pytest.mark.parametrize("where", ["validate", "list"])
def test_another_organizations_agent_version_is_refused(
    user, workspace, storage, auth_client, where
):
    """A request names an agent version by id: validation and the scenarios list take only this environment's."""
    from accounts.models import Organization
    from simulate.models import AgentVersion
    from simulate.tests.test_harness_readiness import _with_a2

    environment, theirs = _with_a2(user, workspace, storage, f"rv-scope-{where}")
    other = Organization.objects.create(name="another organization")
    AgentVersion.no_workspace_objects.filter(id=theirs.id).update(organization=other)
    jobs = HostedHarnessJob.no_workspace_objects.count()

    if where == "validate":
        response = _post(auth_client, environment, {"agent_version_id": str(theirs.id)})
    else:
        response = auth_client.get(
            f"/simulate/api/harness-jobs/{environment.id}/scenarios/",
            {"agent_version_id": str(theirs.id)},
        )

    assert response.status_code == 422, response.content
    assert response.json()["error"] == "version_not_pickable"
    assert response.json()["retryable"] is False
    assert HostedHarnessJob.no_workspace_objects.count() == jobs


def test_another_environments_version_gets_one_answer_from_every_endpoint(
    user, workspace, storage, auth_client
):
    """The same mistake, a version that belongs to another environment, answers as a run's pick does: 422
    `version_not_pickable`, naming the piece, not retryable."""
    from simulate.tests.test_harness_readiness import _environment

    mine = _environment(user, workspace, storage, "one-answer-mine")
    other = _environment(user, workspace, storage, "one-answer-other")
    _suite(mine, _ten())
    _suite(other, _ten())
    mine.refresh_from_db()
    other.refresh_from_db()
    pick = {"environment_version_id": str(other.environment_version_id)}
    base = f"/simulate/api/harness-environments/{mine.id}"

    answers = {
        "capabilities": auth_client.get(f"{base}/capabilities/", pick),
        "capabilities/cases": auth_client.get(f"{base}/capabilities/cases/", pick),
        "scenarios list": auth_client.get(
            f"/simulate/api/harness-jobs/{mine.id}/scenarios/", pick
        ),
        "scenarios/validate": _post(auth_client, mine, pick),
    }

    for where, response in answers.items():
        body = response.json()
        body = body.get("result", body) if "error" not in body else body
        assert (
            response.status_code,
            body.get("error"),
            body.get("retryable"),
            body.get("piece"),
        ) == (
            422,
            "version_not_pickable",
            False,
            "environment",
        ), (where, response.content)


def _proving(environment, proofs, *, body=None):
    """Validate scenarios on the default pair, with the job it would start replaced by the keys it was asked for."""
    from types import SimpleNamespace

    from simulate.services.harness_scenario_actions import validate_scenarios
    from simulate.tests.test_harness_scenario_lifecycle import _proofs

    _proofs(environment, proofs)
    asked = {}

    def start(*_args, scenario_keys=None, **_kwargs):
        asked["keys"] = scenario_keys
        return SimpleNamespace(state="validating", job=None)

    with (
        patch.object(validation, "can_validate", lambda *_a: True),
        patch.object(validation, "start_validation", start),
    ):
        answer, status = validate_scenarios(environment, body or {})
    return answer, status, asked


def test_validate_proves_only_the_cases_without_a_proof_of_their_own_and_resumes_after_a_stop(
    user, workspace, storage
):
    from simulate.tests.test_harness_affected_scenarios import (
        LOOKUP_KEYS,
        PROVED,
        REFUND_KEYS,
    )

    environment = _environment(user, workspace, storage, "sv-incremental")
    _suite(environment, _ten(), proved=False)
    unreached = {
        "format": 2,
        "proved": False,
        "blocked_reason": "validation_incomplete",
    }
    # Stopped by the window: the refunds were proved, the lookups never reached.
    stopped = {
        **dict.fromkeys(REFUND_KEYS, PROVED),
        **dict.fromkeys(LOOKUP_KEYS, unreached),
    }

    _answer, status, asked = _proving(environment, stopped)
    _, _, everything = _proving(environment, stopped, body={"reprove_all": True})
    ready, ready_status, nothing = _proving(
        environment, dict.fromkeys(REFUND_KEYS + LOOKUP_KEYS, PROVED)
    )

    assert (status, sorted(asked["keys"])) == (202, sorted(LOOKUP_KEYS))
    assert everything["keys"] is None
    assert (ready["state"], ready_status, nothing) == ("ready", 200, {})


def test_the_validation_status_says_how_many_cases_are_proved_of_how_many(
    auth_client, user, workspace, storage
):
    from simulate.tests.test_harness_affected_scenarios import (
        LOOKUP_KEYS,
        PROVED,
        REFUND_KEYS,
    )
    from simulate.tests.test_harness_scenario_lifecycle import _proofs

    environment = _environment(user, workspace, storage, "sv-progress")
    _suite(environment, _ten(), proved=False)
    _proofs(environment, dict.fromkeys(REFUND_KEYS, PROVED))
    started = _post(auth_client, environment)

    status = _get(auth_client, environment, started.json()["job_id"]).json()

    assert started.status_code == 202, started.content
    assert (status["proved"], status["total"]) == (
        len(REFUND_KEYS),
        len(REFUND_KEYS + LOOKUP_KEYS),
    )
