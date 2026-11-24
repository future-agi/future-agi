"""An epoch's result in words: the epoch page's read and the pull request's body say the
same sentences, from the patch that ships."""

from unittest.mock import patch

import pytest

from simulate.models import AgentFixCandidate, AgentFixRun
from simulate.pydantic_schemas.agent_fix import EvalGrading, EvalReview, VerdictTier
from simulate.selectors.agent_fix_wording import (
    EVALS_INCOMPLETE,
    changed_files,
    changes_line,
    headline,
    must_pass_line,
)
from simulate.services import agent_fix_github
from simulate.tests.test_agent_fix_services import _TIERS, _candidate, _diff, _fix_run
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
_NOISE = ["late-booking: confirms", "late-booking: polite", "refund: asks"]


@pytest.fixture(autouse=True)
def entitled():
    with patch("simulate.views.agent_fix.check_ee_feature") as gate:
        yield gate


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
    storage.objects[candidate.patch_object_key] = _SHIPPED
    AgentFixCandidate.no_workspace_objects.filter(id=candidate.id).update(
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
    with patch("simulate.serializers.agent_fix.eval_review", return_value=review):
        response = auth_client.get(f"{BASE}/{fix_run.id}/")
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
        self, auth_client, source_run, storage
    ):
        fix_run, candidate = _winner(source_run, storage, noise=_NOISE)

        wording = _read(auth_client, fix_run, _DONE)["wording"]
        body = _body(candidate, _DONE)

        # Three checks on two scenarios: two moved, and none broke.
        assert wording["headline"] == (
            "Held-back pass rate 17% → 50%."
            " No must-pass scenario broke (2 moved within normal noise)."
        )
        assert wording["must_pass"] == (
            "None of the 6 must-pass scenarios broke (2 moved within normal noise)."
        )
        assert f"{wording['headline']}\n\n### Verdict" in body
        assert f"| Must-pass scenarios | Passed | {wording['must_pass']} |" in body
        assert wording["caveats"] == [
            "Must-pass scenario “late-booking”: its “confirms” check passed less often,"
            " within normal noise, so it was not counted.",
            "Must-pass scenario “late-booking”: its “polite” check passed less often,"
            " within normal noise, so it was not counted.",
            "Must-pass scenario “refund”: its “asks” check passed less often, within"
            " normal noise, so it was not counted.",
        ]

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
        self, auth_client, source_run, storage
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

    def test_a_patch_that_cannot_be_read_leaves_the_rest_of_the_read(
        self, auth_client, source_run, storage
    ):
        fix_run, candidate = _winner(source_run, storage)
        del storage.objects[candidate.patch_object_key]

        wording = _read(auth_client, fix_run, _DONE)["wording"]

        assert wording["changes"] is None
        assert wording["headline"].startswith("Held-back pass rate 17% → 50%.")

    def test_a_list_reads_no_patch(self, auth_client, source_run, storage):
        fix_run, _ = _winner(source_run, storage)

        with patch("simulate.selectors.agent_fix_wording.load_object") as load:
            response = auth_client.get(
                f"{BASE}/", {"environment_id": str(fix_run.environment_id)}
            )

        assert response.status_code == 200, response.content
        assert "wording" not in response.json()["results"][0]["candidates"][0]
        load.assert_not_called()


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

    def test_the_change_line_names_few_files_and_counts_the_rest(self):
        one = changed_files(_diff(("prompts/booking.md", 1, 0)))

        assert (
            changes_line(one) == "Changes prompts/booking.md: 1 line added, 0 removed."
        )
