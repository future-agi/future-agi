"""An epoch's result in words: the epoch page's read and the pull request's body say the
same sentences, from the patch that ships."""

import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from simulate.models import AgentFixCandidate, AgentFixRun
from simulate.pydantic_schemas.agent_fix import (
    EvalGrading,
    EvalReview,
    InventoryScenario,
    VerdictTier,
)
from simulate.selectors.agent_fix_wording import (
    EVALS_INCOMPLETE,
    FixNames,
    candidate_wording,
    changes_line,
    flaky_protected,
    headline,
    must_pass_check,
    must_pass_line,
    regressions,
)
from simulate.services import agent_fix_engine as engine
from simulate.services import agent_fix_github
from simulate.services.agent_fix_engine import patch_files
from simulate.tests.test_agent_fix_services import (
    _TIERS,
    BASE_AGENT,
    _candidate,
    _diff,
    _fix_run,
    _tree,
)
from simulate.tests.test_agent_fix_services import (  # noqa: F401 - fixtures
    any_suite_may_launch as any_suite_may_launch,
)
from simulate.tests.test_agent_fix_services import (  # noqa: F401 - fixtures
    environment as environment,
)
from simulate.tests.test_agent_fix_services import source_run as source_run
from simulate.tests.test_agent_fix_services import temporal as temporal
from simulate.tests.test_agent_fix_services import versioning_on as versioning_on
from simulate.tests.test_harness_versioning_backfill import (  # noqa: F401 - fixture
    storage as storage,
)

pytestmark = pytest.mark.django_db

BASE = "/simulate/api/agent-fix-runs"
# What an engine that builds on an earlier candidate says of its own last round only.
_LAST_ROUND = "Changed instructions.md only (126 of 200 lines)."
# The whole change against the base, as the candidate's patch stores it.
_SHIPPED = _diff(
    ("agent.py", 20, 5),
    ("config.py", 2, 1),
    ("instructions.md", 9, 2),
    ("tools.py", 4, 0),
)
# Keys and sub-goal ids as the engine sends them: a slug and a snake_case id.
_SLUG = "reschedule-appointment-out-of-hours-recovery"
_NOISE = [
    f"{_SLUG}: slot_unavailable_handled",
    "late-booking: confirms",
    "late-booking: polite",
    "refund: asks",
]
# What the environment calls them; "refund" has no name and "polite" no words.
_SCENARIOS = [
    InventoryScenario(
        key=_SLUG, name="Reschedule out of hours", tests="", use_case="", sub_goals=[]
    ),
    InventoryScenario(
        key="late-booking", name="Late booking", tests="", use_case="", sub_goals=[]
    ),
    InventoryScenario(key="refund", name="", tests="", use_case="", sub_goals=[]),
]
_CHECKS = {
    "slot_unavailable_handled": {
        "name": "slot_unavailable_handled",
        "what": "Says the slot is unavailable and offers another.",
    },
    "confirms": {"name": "confirms", "what": "Confirms the new time"},
    "polite": {"name": "polite", "what": ""},
}
# What must never reach a person: keys and sub-goal ids.
_RAW = (
    _SLUG,
    "slot_unavailable_handled",
    "late-booking",
    "“refund”",
    "“asks”",
)


@pytest.fixture(autouse=True)
def entitled():
    with patch("simulate.views.agent_fix.check_ee_feature") as gate:
        yield gate


@pytest.fixture
def named():
    """The fix's inventory and its environment's sub-goal catalogue, as stored."""
    wording = "simulate.selectors.agent_fix_wording"
    with (
        patch(f"{wording}.inventory", return_value=_SCENARIOS) as scenarios,
        patch(f"{wording}.environment_sub_goals", return_value=_CHECKS) as checks,
    ):
        yield scenarios, checks


def _winner(source_run, storage, *, noise=(), summary=_LAST_ROUND):
    fix_run = _fix_run(
        source_run,
        plan={
            "target": 8,
            "held_back": 4,
            "must_pass": 6,
            "protected": 0,
            "max_candidates": 4,
            "repeats": 2,
            "baseline_spread_pts": 5,
        },
    )
    # Added while the fix runs, as the engine does; it ends ready.
    candidate = _candidate(fix_run)
    AgentFixRun.no_workspace_objects.filter(id=fix_run.id).update(
        state=AgentFixRun.State.READY
    )
    # The patch, and its files as counted when it was stored.
    storage.objects[candidate.patch_object_key] = _SHIPPED
    AgentFixCandidate.no_workspace_objects.filter(id=candidate.id).update(
        changed_files=[list(one) for one in patch_files(_SHIPPED)],
        outcome=AgentFixCandidate.Outcome.WINNER,
        summary=summary,
        verdict={
            "accepted": True,
            "reason": "passed every tier",
            "target_pass_rate": [0.25, 0.75],
            "held_back_pass_rate": [0.17, 0.5],
            "must_pass_regressions": [],
            "must_pass_within_noise": list(noise),
            "tiers": _TIERS,
        },
    )
    return fix_run, AgentFixCandidate.no_workspace_objects.select_related(
        "fix_run"
    ).get(id=candidate.id)


def _read(auth_client, fix_run, review):
    # A read never reads a patch from storage.
    with (
        patch("simulate.serializers.agent_fix.eval_review", return_value=review),
        patch("simulate.services.agent_fix_engine.load_object") as load,
    ):
        response = auth_client.get(f"{BASE}/{fix_run.id}/")
    load.assert_not_called()
    assert response.status_code == 200, response.content
    (candidate,) = response.json()["candidates"]
    return candidate


def _body(candidate, review):
    with patch.object(agent_fix_github, "eval_review", return_value=review):
        return agent_fix_github._description(candidate, _SHIPPED)[1]


_INCOMPLETE = EvalReview(grading=EvalGrading.INCOMPLETE, compared=1)
_DONE = EvalReview(grading=EvalGrading.DONE, compared=1)


class TestOneWording:
    def test_the_change_is_told_from_the_patch_that_ships_not_the_engines_note(
        self, auth_client, source_run, storage
    ):
        fix_run, candidate = _winner(source_run, storage)

        read = _read(auth_client, fix_run, _DONE)
        body = _body(candidate, _DONE)

        shipped = (
            "Changes agent.py, config.py, instructions.md and 1 more file:"
            " 35 lines added, 8 removed."
        )
        assert read["wording"]["changes"] == shipped
        assert f"### What changed\n\n{shipped}\n\n| File |" in body
        # The engine's note stays its own, on its last round, never the change's summary.
        assert read["summary"] == _LAST_ROUND
        assert "instructions.md only" not in read["wording"]["changes"]

    def test_a_must_pass_scenario_within_noise_is_counted_once_on_both(
        self, auth_client, source_run, storage, named
    ):
        fix_run, candidate = _winner(source_run, storage, noise=_NOISE)

        wording = _read(auth_client, fix_run, _DONE)["wording"]
        body = _body(candidate, _DONE)

        # Four checks on three scenarios: three moved, and none broke.
        assert wording["headline"] == (
            "Held-back pass rate 17% → 50%."
            " No must-pass scenario broke (3 moved within normal noise)."
        )
        assert wording["must_pass"] == (
            "None of the 6 must-pass scenarios broke (3 moved within normal noise)."
        )
        assert f"{wording['headline']}\n\n### Verdict" in body
        assert f"| Must-pass scenarios | Passed | {wording['must_pass']} |" in body

    def test_a_caveat_names_the_scenario_and_says_what_its_check_checks(
        self, auth_client, source_run, storage, named
    ):
        fix_run, candidate = _winner(source_run, storage, noise=_NOISE)

        caveats = _read(auth_client, fix_run, _DONE)["wording"]["caveats"]
        body = _body(candidate, _DONE)

        assert caveats == [
            "Must-pass scenario “Reschedule out of hours”: its check “Says the slot is"
            " unavailable and offers another” passed less often, within normal noise,"
            " so it was not counted.",
            "Must-pass scenario “Late booking”: its check “Confirms the new time”"
            " passed less often, within normal noise, so it was not counted.",
            # A check with no words of its own is left out, never shown by its id.
            "Must-pass scenario “Late booking” passed less often, within normal noise,"
            " so it was not counted.",
            # A scenario with no name is not named at all, never by its key.
            "A must-pass scenario passed less often, within normal noise, so it was"
            " not counted.",
        ]
        for caveat in caveats:
            assert f"- {caveat}" in body
        for raw in _RAW:
            assert all(raw not in caveat for caveat in caveats)
            assert raw not in body
        # One lookup each for the read and for the pull request.
        scenarios, checks = named
        assert (scenarios.call_count, checks.call_count) == (2, 2)

    def test_evals_that_did_not_finish_are_a_caveat_on_both(
        self, auth_client, source_run, storage
    ):
        fix_run, candidate = _winner(source_run, storage)

        wording = _read(auth_client, fix_run, _INCOMPLETE)["wording"]
        body = _body(candidate, _INCOMPLETE)

        assert wording["caveats"] == [EVALS_INCOMPLETE]
        assert f"- {EVALS_INCOMPLETE}" in body
        # Said once, in the page's words.
        assert body.count("did not finish") == 1

    def test_every_sentence_the_page_reads_is_in_the_pull_request(
        self, auth_client, source_run, storage, named
    ):
        fix_run, candidate = _winner(source_run, storage, noise=_NOISE)

        wording = _read(auth_client, fix_run, _INCOMPLETE)["wording"]
        body = _body(candidate, _INCOMPLETE)

        said = [
            wording["headline"],
            wording["must_pass"],
            wording["changes"],
            *wording["caveats"],
        ]
        assert all(said)
        for sentence in said:
            assert sentence in body

    def test_files_never_counted_read_as_no_change_line_and_no_patch_is_read(
        self, auth_client, source_run, storage
    ):
        fix_run, candidate = _winner(source_run, storage)
        AgentFixCandidate.no_workspace_objects.filter(id=candidate.id).update(
            changed_files=None
        )

        wording = _read(auth_client, fix_run, _DONE)["wording"]

        assert wording["changes"] is None
        assert wording["headline"].startswith("Held-back pass rate 17% → 50%.")

    def test_a_list_reads_no_patch(self, auth_client, source_run, storage):
        fix_run, _ = _winner(source_run, storage)

        with patch("simulate.services.agent_fix_engine.load_object") as load:
            response = auth_client.get(
                f"{BASE}/", {"environment_id": str(fix_run.environment_id)}
            )

        assert response.status_code == 200, response.content
        assert "wording" not in response.json()["results"][0]["candidates"][0]
        load.assert_not_called()


class TestCountedWhenStored:
    def test_a_path_reads_as_written_and_a_line_counts_once_whatever_it_holds(
        self, auth_client, source_run, storage
    ):
        fix_run = _fix_run(source_run)
        # A form feed inside a line, and a path git would quote.
        candidate = _candidate(
            fix_run,
            tree=_tree({"agent.py": b"PROMPT = 'x\x0cy'\n", "café.py": b"a\nb\n"}),
        )

        assert candidate.changed_files == [["agent.py", 1, 1], ["café.py", 2, 0]]
        stored = storage.objects[candidate.patch_object_key]
        assert [list(one) for one in patch_files(stored)] == candidate.changed_files
        wording = _read(auth_client, fix_run, _DONE)["wording"]
        assert wording["changes"] == (
            "Changes agent.py and café.py: 3 lines added, 1 removed."
        )

    def test_a_non_ascii_path_reads_as_written_in_the_pull_request(
        self, source_run, storage
    ):
        fix_run = _fix_run(source_run)
        candidate = _candidate(fix_run, tree=_tree({**BASE_AGENT, "café.py": b"a\n"}))
        AgentFixRun.no_workspace_objects.filter(id=fix_run.id).update(
            state=AgentFixRun.State.READY
        )
        candidate = AgentFixCandidate.no_workspace_objects.select_related(
            "fix_run"
        ).get(id=candidate.id)

        with patch.object(agent_fix_github, "eval_review", return_value=_DONE):
            title, body = agent_fix_github._description(
                candidate, storage.objects[candidate.patch_object_key]
            )

        # Nothing held back here, so the title carries that mark before the file.
        assert title.endswith(": change café.py")
        assert "Changes café.py: 1 line added, 0 removed." in body
        assert "| `café.py` | +1 | −0 |" in body
        assert "\\303" not in body


class TestCountedOutsideTheLock:
    def test_the_patch_is_counted_before_the_fix_is_locked(self, source_run, storage):
        fix_run = _fix_run(source_run)
        order = []
        count, lock = engine.counted_files, engine._locked_running

        def counting(*args):
            order.append("count")
            return count(*args)

        def locking(*args):
            order.append("lock")
            return lock(*args)

        with (
            patch.object(engine, "counted_files", side_effect=counting),
            patch.object(engine, "_locked_running", side_effect=locking),
        ):
            candidate = _candidate(fix_run)

        assert order == ["count", "lock"]
        assert candidate.changed_files is not None

    def test_a_patch_git_cannot_count_still_opens_its_pull_request(
        self, source_run, storage
    ):
        _, candidate = _winner(source_run, storage)

        with (
            patch.object(
                engine,
                "patch_files",
                side_effect=subprocess.CalledProcessError(1, "git"),
            ),
            patch.object(agent_fix_github, "eval_review", return_value=_DONE),
        ):
            title, body = agent_fix_github._description(candidate, _SHIPPED)

        assert title.endswith(": change the agent")
        assert "### What changed" not in body
        assert "Held-back pass rate 17% → 50%." in body


class TestNoIdsInThePullRequest:
    def test_flaky_noise_and_a_regression_are_said_by_name_only(
        self, source_run, storage, named
    ):
        fix_run, candidate = _winner(
            source_run,
            storage,
            noise=[f"{_SLUG}: slot_unavailable_handled", "refund: asks"],
        )
        tiers = [
            {**tier, "status": "failed"} if tier["name"] == "must-pass" else tier
            for tier in _TIERS
        ]
        AgentFixRun.no_workspace_objects.filter(id=fix_run.id).update(
            report={
                "flaky_protected": [
                    {
                        "key": _SLUG,
                        "sub_goal": "slot_unavailable_handled",
                        "passed": 2,
                        "runs": 3,
                    },
                    {
                        "key": "late-booking",
                        "sub_goal": "polite",
                        "passed": 1,
                        "runs": 3,
                    },
                    {"key": "refund", "sub_goal": "asks", "passed": 1, "runs": 3},
                ]
            }
        )
        AgentFixCandidate.no_workspace_objects.filter(id=candidate.id).update(
            outcome=AgentFixCandidate.Outcome.REJECTED,
            override_reason="The check is being retired.",
            verdict={
                **candidate.verdict,
                "accepted": False,
                "must_pass_regressions": ["late-booking: confirms"],
                "tiers": tiers,
            },
        )
        candidate = AgentFixCandidate.no_workspace_objects.select_related(
            "fix_run"
        ).get(id=candidate.id)

        body = _body(candidate, _DONE)

        # The regression, the noise and the flaky scenarios, each by name.
        assert (
            "- Must-pass scenario “Late booking”: its check “Confirms the new time”\n"
            in body
        )
        assert (
            "- Must-pass scenario “Reschedule out of hours”: its check “Says the slot is"
            " unavailable and offers another” passed less often" in body
        )
        assert (
            "- Protected scenario “Reschedule out of hours” is flaky on the current"
            " version, so this epoch wasn't held to it: its check “Says the slot is"
            " unavailable and offers another” passed 2 of 3 runs." in body
        )
        assert (
            "- Protected scenario “Late booking” is flaky on the current version, so"
            " this epoch wasn't held to it: a check passed 1 of 3 runs." in body
        )
        assert (
            "- 1 protected scenario is flaky on the current version, so this epoch"
            " wasn't held to it." in body
        )
        for raw in (*_RAW, "“polite”", "“confirms”", "confirms:"):
            assert raw not in body


class TestFlakyProtectedOnThePage:
    def test_the_read_says_the_flaky_protected_scenarios_as_the_pull_request(
        self, auth_client, source_run, storage, named
    ):
        fix_run, candidate = _winner(source_run, storage)
        AgentFixRun.no_workspace_objects.filter(id=fix_run.id).update(
            # A whole report, as the platform stores an ended fix's.
            report={
                "ending": "ready",
                "summary": "Found a fix.",
                "winner_candidate_id": str(candidate.id),
                "flaky_protected": [
                    {
                        "key": _SLUG,
                        "sub_goal": "slot_unavailable_handled",
                        "passed": 2,
                        "runs": 3,
                    },
                    {"key": "refund", "sub_goal": "asks", "passed": 1, "runs": 3},
                ],
            }
        )
        candidate.fix_run.refresh_from_db()

        with patch("simulate.serializers.agent_fix.eval_review", return_value=_DONE):
            read = auth_client.get(f"{BASE}/{fix_run.id}/").json()
        body = _body(candidate, _DONE)

        assert read["flaky_protected_text"] == [
            "Protected scenario “Reschedule out of hours” is flaky on the current"
            " version, so this epoch wasn't held to it: its check “Says the slot is"
            " unavailable and offers another” passed 2 of 3 runs.",
            "1 protected scenario is flaky on the current version, so this epoch"
            " wasn't held to it.",
        ]
        for line in read["flaky_protected_text"]:
            assert f"- {line}" in body
        for raw in _RAW:
            assert all(raw not in line for line in read["flaky_protected_text"])

    def test_a_list_says_no_flaky_protected_text(
        self, auth_client, source_run, storage
    ):
        fix_run, _ = _winner(source_run, storage)

        response = auth_client.get(
            f"{BASE}/", {"environment_id": str(fix_run.environment_id)}
        )

        assert "flaky_protected_text" not in response.json()["results"][0]


class TestMustPassWords:
    _PASSED = [VerdictTier(name="must-pass", status="passed")]
    _FAILED = [VerdictTier(name="must-pass", status="failed")]

    def test_noise_is_said_and_never_counted_as_broken(self):
        verdict = {"must_pass_regressions": [], "must_pass_within_noise": ["a: x"]}
        plan = {"must_pass": 6, "protected": 2}

        assert must_pass_line(verdict, plan, self._PASSED) == (
            "None of the 6 must-pass scenarios broke, including the 2 you protected"
            " (1 moved within normal noise)."
        )
        assert must_pass_line({**verdict, "must_pass_within_noise": []}, {}, []) == (
            "No must-pass scenario broke."
        )

    def test_a_break_is_counted_and_a_check_that_did_not_run_says_nothing(self):
        verdict = {"must_pass_regressions": ["a: x", "b: y"]}

        assert must_pass_line(verdict, {}, self._FAILED) == (
            "2 must-pass scenarios broke."
        )
        skipped = [VerdictTier(name="must-pass", status="skipped")]
        assert must_pass_line(verdict, {}, skipped) == ""
        assert headline({**verdict, "target_pass_rate": [0, 0.625]}, self._FAILED) == (
            "Target pass rate 0% → 63%. 2 must-pass scenarios broke."
        )

    def test_a_candidate_stopped_before_must_pass_ran_says_nothing_of_it(self):
        rejected_at_target = [
            VerdictTier(name="static", status="passed"),
            VerdictTier(name="target", status="failed"),
            VerdictTier(name="must-pass", status="skipped"),
        ]
        verdict = {"target_pass_rate": [0.33, 0.33], "must_pass_regressions": []}

        assert headline(verdict, rejected_at_target) == "Target pass rate 33% → 33%."
        assert must_pass_line(verdict, {"must_pass": 6}, rejected_at_target) == ""

    def test_a_scenario_reads_by_its_stored_name_exactly(self, named):
        scenarios, _ = named
        scenarios.return_value = [
            InventoryScenario(
                key="gpt", name="gpt-4o", tests="", use_case="", sub_goals=[]
            ),
            InventoryScenario(
                key="none", name="  ", tests="", use_case="", sub_goals=[]
            ),
        ]

        # As the Scenarios tab shows it; one with no name has none, never its key.
        assert FixNames(SimpleNamespace(environment=None)).scenarios == {
            "gpt": "gpt-4o"
        }

    def test_broken_checks_on_one_scenario_count_once(self):
        verdict = {
            "must_pass_regressions": ["refund-flow: greeted", "refund-flow: refunded"]
        }
        failed = [VerdictTier(name="must-pass", status="failed")]

        assert must_pass_line(verdict, {}, failed) == "1 must-pass scenario broke."
        assert headline({**verdict, "target_pass_rate": [0.2, 0.6]}, failed) == (
            "Target pass rate 20% → 60%. 1 must-pass scenario broke."
        )
        candidate = SimpleNamespace(
            verdict=verdict, fix_run=SimpleNamespace(plan={}), outcome="rejected"
        )
        names = FixNames.__new__(FixNames)
        names.__dict__.update(scenarios={}, checks={})
        wording = candidate_wording(
            candidate, failed, review=None, files=None, names=names
        )
        assert wording.must_pass_broken == 1

    def test_broken_checks_name_their_scenario_once_and_count_the_unnamed(self):
        names = FixNames.__new__(FixNames)
        names.__dict__.update(scenarios={_SLUG: "Reschedule out of hours"}, checks={})

        assert regressions(
            [
                f"{_SLUG}: slot_unavailable_handled",
                f"{_SLUG}: confirms",
                "refund: asks",
                "refund: polite",
                "late: x",
            ],
            names,
        ) == ["Must-pass scenario “Reschedule out of hours”", "2 must-pass scenarios"]
        assert regressions(["refund: asks"], names) == ["A must-pass scenario"]

    def test_flaky_scenarios_that_read_the_same_are_said_once(self):
        names = FixNames.__new__(FixNames)
        names.__dict__.update(
            scenarios={"a": "Late booking", "b": "Late booking"}, checks={}
        )
        report = {
            "flaky_protected": [
                {"key": "a", "sub_goal": "x", "passed": 1, "runs": 3},
                {"key": "b", "sub_goal": "y", "passed": 1, "runs": 3},
            ]
        }

        assert flaky_protected(report, names) == [
            "Protected scenario “Late booking” is flaky on the current version, so this"
            " epoch wasn't held to it: a check passed 1 of 3 runs."
        ]

    def test_a_check_is_named_or_not_shown(self):
        names = FixNames.__new__(FixNames)
        names.__dict__.update(
            scenarios={_SLUG: "Reschedule out of hours"},
            checks={"slot_unavailable_handled": "Offers another slot"},
        )

        assert must_pass_check(f"{_SLUG}: slot_unavailable_handled", names) == (
            "Must-pass scenario “Reschedule out of hours”: its check “Offers another slot”"
        )
        assert must_pass_check(f"{_SLUG}: unknown_check", names) == (
            "Must-pass scenario “Reschedule out of hours”"
        )
        assert must_pass_check("unknown-key: slot_unavailable_handled", names) is None

    def test_the_change_line_names_few_files_and_counts_the_rest(self):
        one = patch_files(_diff(("prompts/booking.md", 1, 0)))

        assert (
            changes_line(one) == "Changes prompts/booking.md: 1 line added, 0 removed."
        )
