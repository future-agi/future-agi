"""The model-tools journey through the API, in the order a user walks it: a test condition set in the scenario editor,
saved as the next suite version, a run with a seed that carries the condition to the guest, the trial's World changes
read back, and a re-run from the seed the run shows."""

from unittest.mock import patch

import pytest

from simulate.models import (
    HarnessSuiteMember,
    HostedHarnessJob,
    HostedHarnessStageOutput,
    TestExecution,
)
from simulate.models.test_execution import CallExecution
from simulate.services.hosted_harness_ingestion import TRIAL_STATE_KEY
from simulate.tests import test_harness_versioning_record as record_tests
from simulate.tests.test_harness_amend_archive import NAMES, _key
from simulate.tests.test_harness_versioning_backfill import (
    storage as storage,  # noqa: F401 - fixture
)
from simulate.tests.test_harness_versioning_picks import _environment, _post_run
from simulate.views.run_results_v3 import _execution_payload

pytestmark = pytest.mark.django_db

FAULT = {"action": "issue_refund", "mode": "unavailable", "on_call": 1}
# What the guest seals for a trial whose refund the condition took down: the read went through, nothing committed.
FAULTED = {
    "start": {"state": "seeded", "tables": []},
    "state_changes": [
        {
            "who": "agent",
            "name": "the agent",
            "attempted": 2,
            "committed": 0,
            "read": 1,
            "faulted": 1,
            "refused": 0,
            "duplicate": 0,
            "read_actions": ["lookup_order"],
            "committed_actions": [],
            "no_change": True,
        }
    ],
}


@pytest.fixture(autouse=True)
def versioning_on(settings):
    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = False


def _authored(user, workspace, key, storage):
    """A built, versioned environment whose Scenarios tab lists its suite, as a finished build leaves it."""
    environment = _environment(user, workspace, key, storage)
    HostedHarnessStageOutput.no_workspace_objects.create(
        job=environment,
        title="Scenarios",
        summary="1 pre-authored scenario",
        kind="scenarios",
        data=[{"name": NAMES[0], "scenario_key": _key(NAMES[0]), "tests": "t"}],
    )
    return environment


def _set_fault(auth_client, environment):
    with patch(
        "simulate.services.hosted_harness_gateway.push_scenarios_into_live_sandbox",
        return_value=False,
    ):
        return auth_client.post(
            f"/simulate/api/harness-jobs/{environment.id}/scenarios/amend/",
            {
                "changes": [
                    {
                        "op": "set_field",
                        "scenario": NAMES[0],
                        "field": "faults",
                        "value": [FAULT],
                    }
                ],
                "rework": True,
            },
            format="json",
        )


def _save(environment, settings):
    # As in production: a saved Scenarios-tab edit is never resealed, so its pair is left for the first run to certify.
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
    versions = "simulate.services.harness_versioning.versions"
    with (
        patch(f"{versions}._default_runtime", return_value=object()),
        patch(f"{versions}._cached_sealed", return_value=(None, "")),
    ):
        record_tests._save_drafts(environment)
    environment.refresh_from_db()


def test_a_test_condition_set_in_the_editor_runs_with_its_seed_shows_in_world_changes_and_reruns_with_that_seed(
    auth_client, user, workspace, storage, settings
):
    environment = _authored(user, workspace, "mt-journey", storage)
    before = environment.suite_version_id

    # 1. The editor's Save scenario: the condition lands in the draft, then Save makes it the next suite version.
    amended = _set_fault(auth_client, environment)
    assert amended.status_code == 200, amended.content
    assert amended.json()["receipts"][0]["outcome"] == "applied"
    _save(environment, settings)
    assert environment.suite_version_id != before
    member = HarnessSuiteMember.no_workspace_objects.get(
        suite_version=environment.suite_version, scenario_key=_key(NAMES[0])
    )
    assert member.content["faults"] == [FAULT]

    # 2. Run it with seed 42: the run keeps the seed and hands the guest the scenario's condition.
    run = _post_run(
        auth_client,
        environment,
        {"scenario_ids": [_key(NAMES[0])], "trials": 1, "seed": "42"},
        key="mt-run",
    )
    assert run.status_code == 202, run.content
    job = HostedHarnessJob.no_workspace_objects.get(id=run.json()["job_id"])
    assert job.seed == 42
    assert job.payload["metadata"]["scenario_edits"][_key(NAMES[0])]["faults"] == [
        FAULT
    ]

    # 3. The trial's World changes as the guest sealed it: the faulted call apart from what was committed.
    execution = TestExecution.objects.get(id=run.json()["test_execution_id"])
    # The trial the run registered for the scenario, finished as ingestion leaves it.
    call = CallExecution.objects.filter(test_execution=execution).first()
    assert call is not None
    call.status = CallExecution.CallStatus.COMPLETED
    call.call_metadata = {**(call.call_metadata or {}), TRIAL_STATE_KEY: FAULTED}
    call.save(update_fields=["status", "call_metadata"])
    detail = auth_client.get(f"/simulate/v3/call-executions/{call.id}/")
    assert detail.status_code == 200, detail.content
    [agent] = detail.json()["trial_state"]["state_changes"]
    assert (agent["faulted"], agent["committed"], agent["read_actions"]) == (
        1,
        0,
        ["lookup_order"],
    )

    # 4. Re-run from the seed the run shows: the same seed again, the same condition again.
    shown = _execution_payload(execution, {})["seed"]
    assert shown == "42"
    rerun = _post_run(
        auth_client,
        environment,
        {"scenario_ids": [_key(NAMES[0])], "trials": 1, "seed": shown},
        key="mt-rerun",
    )
    assert rerun.status_code == 202, rerun.content
    again = HostedHarnessJob.no_workspace_objects.get(id=rerun.json()["job_id"])
    assert again.seed == 42
    assert again.payload["metadata"]["scenario_edits"][_key(NAMES[0])]["faults"] == [
        FAULT
    ]


def test_a_test_condition_the_world_cannot_apply_is_refused_with_why_and_the_suite_is_unchanged(
    auth_client, user, workspace, storage
):
    environment = _authored(user, workspace, "mt-journey-refused", storage)
    before = environment.suite_version_id
    with patch(
        "simulate.services.hosted_harness_gateway.push_scenarios_into_live_sandbox",
        return_value=False,
    ):
        response = auth_client.post(
            f"/simulate/api/harness-jobs/{environment.id}/scenarios/amend/",
            {
                "changes": [
                    {
                        "op": "set_field",
                        "scenario": NAMES[0],
                        "field": "faults",
                        "value": [{"action": "issue_refund", "mode": "explode"}],
                    }
                ],
                "rework": True,
            },
            format="json",
        )

    receipt = response.json()["receipts"][0]
    assert (
        receipt["outcome"] == "refused"
        and "choose what happens to the call" in receipt["why"]
    )
    environment.refresh_from_db()
    assert environment.suite_version_id == before


def test_a_trial_s_world_changes_link_its_kept_end_state_and_an_older_trial_reads_as_before():
    from types import SimpleNamespace

    from simulate.selectors.call_detail import trial_state as _trial_state

    def call(metadata):
        return SimpleNamespace(call_metadata=metadata)

    kept = call(
        {
            TRIAL_STATE_KEY: FAULTED,
            "hosted_harness_artifacts": {
                "world_state": {"url": "http://storage/end-state", "sha256": "a" * 64}
            },
        }
    )

    assert _trial_state(kept)["end_state_url"] == "http://storage/end-state"
    assert _trial_state(kept)["state_changes"] == [
        {**one, "name": "The agent"} if one["who"] == "agent" else one
        for one in FAULTED["state_changes"]
    ]
    # A trial whose end state was not kept, and one from before World changes were recorded, read as they did.
    assert "end_state_url" not in _trial_state(call({TRIAL_STATE_KEY: FAULTED}))
    assert _trial_state(call({})) is None and _trial_state(call(None)) is None
