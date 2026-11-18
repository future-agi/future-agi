"""Every scenario lifecycle state and move, as one table: what each move needs (a proof at submit, or a validation of
the suite against an agent and environment pairing), which moves are refused, and what a change of the agent,
environment or suite version does to a case. Each row reads the case's derived state and whether a run of its pair
may pick it."""

import uuid
from dataclasses import replace

import pytest
from django.core.cache import cache
from django.utils import timezone

from simulate.models import (
    HarnessEnvironmentVersion,
    HarnessSuiteValidation,
    HarnessSuiteVersion,
    HostedHarnessScenario,
)
from simulate.serializers.harness_environment import (
    HarnessLifecycleCountsSerializer,
    HarnessScenarioLifecycleSerializer,
)
from simulate.services.harness_readiness import selection
from simulate.services.harness_readiness.report import resolve_pairing
from simulate.services.harness_scenario_lifecycle import runnable, scenario_lifecycle
from simulate.tests.test_harness_affected_scenarios import (
    LOOKUP_KEYS,
    PROVED,
    REFUND_KEYS,
    _suite,
    _ten,
)
from simulate.tests.test_harness_readiness import _environment
from simulate.tests.test_harness_readiness import (  # noqa: F401 - fixture
    storage as storage,
)
from simulate.tests.test_harness_scenario_lifecycle import _copy, _members_of

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("readiness_on")]
KEYS = REFUND_KEYS + LOOKUP_KEYS
CASE = REFUND_KEYS[0]
NO_CONTROL = {
    "format": 2,
    "holds": False,
    "proved": False,
    "kept_as_written": False,
    "blocked_reason": "no_negative_control",
}
CONTROL_PASSED = {
    "format": 2,
    "holds": False,
    "proved": False,
    "blocked_reason": "negative_control_passed",
    "controls": [{"name": "two refunds", "why": "refunds twice", "failed": False}],
}
KEPT = {
    "format": 2,
    "holds": True,
    "proved": False,
    "kept_as_written": True,
    "blocked_reason": "solution_assumed",
    "assumed": ["issue_refund"],
}


def _record(environment, proofs, *, suite=None, version=None, by_job=True):
    """A finished validation's proofs on this pair, made by the environment's run job (its agent version)."""
    HarnessSuiteValidation.no_workspace_objects.update_or_create(
        suite_version=suite or environment.suite_version,
        environment_version=version or environment.environment_version,
        defaults={
            "status": "partial",
            "proved_at": timezone.now(),
            "proofs": proofs,
            "job": environment if by_job else None,
        },
    )
    cache.clear()


def _state(environment, *, suite=None, version=None, agent=None):
    view = scenario_lifecycle(
        environment,
        suite_version=suite or environment.suite_version,
        environment_version=version or environment.environment_version,
        agent_version_id=agent,
    )[CASE]
    return view["state"], view["reason"], runnable(view)


def _base(user, workspace, storage, key):
    environment = _environment(user, workspace, storage, key)
    # A suite with no proof yet: each move records what its proof or validation found.
    _suite(environment, _ten(), proved=False)
    environment.refresh_from_db()
    return environment


def _candidate(environment, model, number):
    version = (
        environment.environment_version
        if model is HarnessEnvironmentVersion
        else environment.suite_version
    )
    return _copy(
        version,
        model,
        status="candidate",
        version_number=(version.version_number or 0) + number,
        hash=version.hash[:-6] + f"cand{number:02d}",
    )


def _archive(environment, key=CASE):
    HostedHarnessScenario.no_workspace_objects.filter(
        job=environment, scenario_key=key
    ).update(
        archived_at=timezone.now(),
        archive_reason="replaced",
        archived_content={"name": key},
    )


def _restore(environment, key=CASE):
    HostedHarnessScenario.no_workspace_objects.filter(
        job=environment, scenario_key=key
    ).update(archived_at=None, archive_reason="", archived_content=None)


# (move, setup, expected (state, reason, runnable)). Each setup starts from a suite with no proof of the case.
MOVES = [
    ("draft: never validated", lambda env: None, ("draft", "not_validated", False)),
    (
        "draft -> active: proved at submit on the saved pair",
        lambda env: _record(env, dict.fromkeys(KEYS, PROVED)),
        ("active", None, True),
    ),
    (
        "draft stays draft: no negative control",
        lambda env: _record(env, {**dict.fromkeys(KEYS, PROVED), CASE: NO_CONTROL}),
        ("draft", "no_negative_control", False),
    ),
    (
        "draft stays draft: the negative control passed",
        lambda env: _record(env, {**dict.fromkeys(KEYS, PROVED), CASE: CONTROL_PASSED}),
        ("draft", "negative_control_passed", False),
    ),
    (
        "draft -> kept as written: a step could not execute",
        lambda env: _record(env, {**dict.fromkeys(KEYS, PROVED), CASE: KEPT}),
        ("kept_as_written", None, True),
    ),
    (
        "active -> archived",
        lambda env: (_record(env, dict.fromkeys(KEYS, PROVED)), _archive(env)),
        ("archived", "archived", False),
    ),
    (
        "archived -> active: restored with its proof",
        lambda env: (
            _record(env, dict.fromkeys(KEYS, PROVED)),
            _archive(env),
            _restore(env),
        ),
        ("active", None, True),
    ),
]


@pytest.mark.parametrize(
    ("move", "setup", "expected"), MOVES, ids=[one[0] for one in MOVES]
)
def test_each_move_on_the_saved_pair(user, workspace, storage, move, setup, expected):
    environment = _base(user, workspace, storage, f"lt-{abs(hash(move)) % 10**8}")
    setup(environment)

    assert _state(environment) == expected


def test_validated_on_unsaved_versions_is_not_active_until_saved(
    user, workspace, storage
):
    """Proved on a candidate environment version: Validated, held back from runs; saved, the same proof reads
    Active."""
    environment = _base(user, workspace, storage, "lt-candidate")
    candidate = _candidate(environment, HarnessEnvironmentVersion, 7)
    _record(environment, dict.fromkeys(KEYS, PROVED), version=candidate)

    assert _state(environment, version=candidate) == ("validated", None, False)

    HarnessEnvironmentVersion._base_manager.filter(id=candidate.id).update(
        status="active"
    )
    candidate.refresh_from_db()
    assert _state(environment, version=candidate) == ("active", None, True)


@pytest.mark.parametrize("piece", ["suite", "environment"])
def test_a_new_suite_or_environment_version_needs_the_case_proved_again(
    user, workspace, storage, piece
):
    """A proof counts only for the suite and environment version it was made on: a new version of either reads
    Draft until it is validated."""
    environment = _base(user, workspace, storage, f"lt-new-{piece}")
    _record(environment, dict.fromkeys(KEYS, PROVED))
    model = HarnessSuiteVersion if piece == "suite" else HarnessEnvironmentVersion
    newer = _copy(
        environment.suite_version
        if piece == "suite"
        else environment.environment_version,
        model,
        version_number=50,
        hash=("next" + uuid.uuid4().hex)[:64],
    )
    if piece == "suite":
        _members_of(environment.suite_version, newer)
    state = (
        _state(environment, version=newer)
        if piece == "environment"
        else _state(environment, suite=newer)
    )

    assert state == ("draft", "not_validated", False)
    # The proof on the old pair still holds there.
    assert _state(environment) == ("active", None, True)


def test_a_proof_from_another_agent_version_reads_draft_until_validated_with_this_one(
    user, workspace, storage
):
    environment = _base(user, workspace, storage, "lt-agent")
    _record(environment, dict.fromkeys(KEYS, PROVED))
    other = uuid.uuid4()

    assert _state(environment) == ("active", None, True)
    assert _state(environment, agent=other) == ("draft", "proved_on_other_agent", False)


def test_a_proof_whose_agent_was_not_recorded_keeps_its_state(
    user, workspace, storage, monkeypatch
):
    """An older validation kept no job: which agent made it is not known, so it is read as before."""
    from simulate.services import agent_setup

    # The other agent's tools were read: every tool the cases call.
    monkeypatch.setattr(
        agent_setup,
        "stored_agent",
        lambda *_a, **_k: {
            "tools": [{"name": "lookup_order"}, {"name": "issue_refund"}]
        },
    )
    environment = _base(user, workspace, storage, "lt-agent-unknown")
    _record(environment, dict.fromkeys(KEYS, PROVED), by_job=False)

    assert _state(environment, agent=uuid.uuid4()) == ("active", None, True)


def test_a_run_holds_back_each_case_that_may_not_run_with_its_reason(
    user, workspace, storage
):
    """Refused moves, through the run gate: one proved case runs; a case without a negative control, an archived
    case and a never-validated case are each held back under their own reason."""
    environment = _base(user, workspace, storage, "lt-gate")
    proved, no_control, archived, never = (
        REFUND_KEYS[0],
        REFUND_KEYS[1],
        LOOKUP_KEYS[0],
        LOOKUP_KEYS[1],
    )
    proofs = dict.fromkeys(KEYS, PROVED)
    proofs[no_control] = NO_CONTROL
    proofs.pop(never)
    _record(environment, proofs)
    _archive(environment, archived)

    plan = selection.select_for_run(
        resolve_pairing(environment),
        keys=[proved, no_control, archived, never],
        trials=1,
    )

    assert proved not in plan.held
    assert plan.held == frozenset({no_control, archived, never})
    assert plan.view["held_back"]["by"] == {
        "not_validated": 1,
        "proof_failed": 1,
        "scenario_archived": 1,
    }
    assert "negative control" in plan.view["reasons"]["proof_failed"]
    assert "Not validated" in plan.view["reasons"]["not_validated"]


def test_a_validated_case_on_unsaved_versions_is_held_back_as_not_saved(
    user, workspace, storage
):
    environment = _base(user, workspace, storage, "lt-gate-candidate")
    candidate = _candidate(environment, HarnessEnvironmentVersion, 9)
    _record(environment, dict.fromkeys(KEYS, PROVED), version=candidate)
    pairing = replace(resolve_pairing(environment), environment_version=candidate)

    plan = selection.select_for_run(pairing, keys=[CASE], trials=1)

    assert plan.held == frozenset({CASE})
    assert plan.view["held_back"]["by"] == {"not_active": 1}
    assert "save them to run it" in plan.view["reasons"]["not_active"]


def test_dropped_and_restored_cases_are_served_with_why_and_whether_proved_again(
    user, workspace, storage, auth_client
):
    """A rebuild dropped one case for a lost tool (listed with its reason); a case its parent had dropped is back
    under its key and runs only once proved again on these versions."""
    environment = _base(user, workspace, storage, "lt-dropped")
    suite = environment.suite_version
    record = {
        "name": "Cancel a ride",
        "missing": {"tools": ["cancel_booking"]},
        "agent_version": "v5",
        "reason": "agent v5 no longer has cancel_booking",
    }
    parent = _copy(suite, HarnessSuiteVersion, documents={"dropped": {CASE: record}})
    HarnessSuiteVersion._base_manager.filter(id=suite.id).update(
        parent=parent,
        documents={**(suite.documents or {}), "dropped": {"cancel-a-ride": record}},
    )
    environment.refresh_from_db()

    def listed():
        body = auth_client.get(
            f"/simulate/api/harness-jobs/{environment.id}/scenarios/"
        ).json()
        return body.get("result", body)

    first = listed()
    # What only the grader reads, for the editor to group apart from what the agent and actors see.
    assert {"solution", "sub_goals", "negative_controls"} <= set(
        first["private_fields"]
    )
    assert "persona" not in first["private_fields"]
    # Every lifecycle the list serves reads as its declared shape.
    for row in first["results"]:
        lifecycle = HarnessScenarioLifecycleSerializer(data=row["lifecycle"])
        assert lifecycle.is_valid(), lifecycle.errors
    assert HarnessLifecycleCountsSerializer(data=first["lifecycle_counts"]).is_valid()
    # One counter of proved cases per response.
    assert "proof_counts" not in first
    assert first["dropped"] == [
        {
            "key": "cancel-a-ride",
            "name": "Cancel a ride",
            "missing": {"tools": ["cancel_booking"]},
            "agent_version": "v5",
            "reason": "agent v5 no longer has cancel_booking",
            "restore_failed": "",
        }
    ]
    assert first["restored"] == [
        {"key": CASE, "name": first["restored"][0]["name"], "proved": False}
    ]

    _record(environment, dict.fromkeys(KEYS, PROVED))
    assert listed()["restored"][0]["proved"] is True


def test_kept_as_written_runs_labelled_as_not_proved(user, workspace, storage):
    """Decision of 18 Nov: a case kept as written runs, and every read says it is not proved and why."""
    environment = _base(user, workspace, storage, "lt-kept-label")
    _record(environment, {**dict.fromkeys(KEYS, PROVED), CASE: KEPT})

    view = scenario_lifecycle(
        environment,
        suite_version=environment.suite_version,
        environment_version=environment.environment_version,
    )[CASE]

    assert runnable(view)
    assert view["proof"]["proved"] is False
    assert view["warning"] == "solution_assumed"
    assert view["warning_text"].startswith("Kept as written:")


@pytest.mark.parametrize(
    "entry, state, text",
    [
        # ALK's verdict decides; a code this release has no words for still reads kept as written.
        (
            {
                "format": 2,
                "holds": True,
                "proved": False,
                "kept_as_written": True,
                "blocked_reason": "new_lane",
            },
            "kept_as_written",
            "Kept as written: part of this case could not be proved here.",
        ),
        # An entry from a harness that does not write the verdict reads not proved.
        (
            {
                "format": 2,
                "holds": True,
                "proved": False,
                "blocked_reason": "solution_assumed",
                "assumed": ["refund"],
            },
            "draft",
            "Not proved on these versions: validate it again to see why.",
        ),
        # One assumed tool reads in the singular.
        (
            {**KEPT, "assumed": ["issue_refund"], "solution_steps": 1},
            "kept_as_written",
            "Kept as written: 1 of 1 solution steps never ran, because issue_refund has no endpoint here.",
        ),
    ],
)
def test_the_lifecycle_reads_alks_verdict_and_never_shows_a_raw_code(
    user, workspace, storage, entry, state, text
):
    environment = _base(user, workspace, storage, f"lt-verdict-{state}-{len(entry)}")
    _record(environment, {**dict.fromkeys(KEYS, PROVED), CASE: entry})

    view = scenario_lifecycle(
        environment,
        suite_version=environment.suite_version,
        environment_version=environment.environment_version,
    )[CASE]

    assert view["state"] == state
    assert (view["warning_text"] or view["reason_text"]) == text
    served = HarnessScenarioLifecycleSerializer(data=view)
    assert served.is_valid(), served.errors


def test_a_proof_with_controls_and_leaks_serves_their_declared_shape(
    user, workspace, storage
):
    environment = _base(user, workspace, storage, "lt-shape")
    leaked = {
        **CONTROL_PASSED,
        "leaks": [
            {
                "who": "simulated person",
                "field": "instruction",
                "grader_field": "tests",
                "excerpt": "x",
            }
        ],
    }
    _record(environment, {**dict.fromkeys(KEYS, PROVED), CASE: leaked})

    view = scenario_lifecycle(
        environment,
        suite_version=environment.suite_version,
        environment_version=environment.environment_version,
    )[CASE]

    assert view["proof"]["controls"][0]["name"] == "two refunds"
    served = HarnessScenarioLifecycleSerializer(data=view)
    assert served.is_valid(), served.errors
