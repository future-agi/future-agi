"""Outside attempts: a fix a person's own coding agent submits, which an Epoch verifies
without a model call. The brief, the refusals at submit, the attempts' lineage and budget
hold, the two refusals that stop an engine template from before verify mode, the verdict,
and the Epoch read that names a held-back case to a person's signed-in session only."""

import json
import math
import os
from contextlib import ExitStack
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from accounts.models import OrgApiKey
from conftest import WorkspaceAwareAPIClient
from simulate.models import AgentFixCandidate, AgentFixRun, HostedHarnessJob
from simulate.pydantic_schemas.agent_fix import FixRequest, Surface
from simulate.selectors.agent_fix import (
    VERDICT_TIERS,
    base_commit,
    fix_lineage,
    graded_check_runs,
    outside_attempt_hold_usd,
)
from simulate.selectors.agent_fix_outside import HELD_BACK_RULE
from simulate.services import agent_fix_engine as engine
from simulate.services.agent_fix_runs import close_fix_run
from simulate.services.hosted_harness import HostedHarnessError
from simulate.tests.test_agent_fix_api import ENGINE_MODEL, _armed, _engine
from simulate.tests.test_agent_fix_api import (  # noqa: F401 - fixture
    browser_client as browser_client,
)
from simulate.tests.test_agent_fix_services import (
    KEYS,
    _candidate,
    _check,
    _executions,
    _fix_run,
    _receipts,
    _run,
    _tree,
)
from simulate.tests.test_agent_fix_services import (  # noqa: F401 - fixtures
    any_suite_may_launch as any_suite_may_launch,
)
from simulate.tests.test_agent_fix_services import environment as environment
from simulate.tests.test_agent_fix_services import source_run as source_run
from simulate.tests.test_agent_fix_services import temporal as temporal
from simulate.tests.test_agent_fix_services import versioning_on as versioning_on
from simulate.tests.test_harness_agent_versions import _passing
from simulate.tests.test_harness_versioning_backfill import (  # noqa: F401 - fixture
    storage as storage,
)

pytestmark = pytest.mark.django_db

EPOCHS = "/simulate/api/agent-fix-runs"
BASE = {"agent.py": b"PROMPT = ''\n", "prompts/system.md": b"be kind\n"}
FIXED = {**BASE, "agent.py": b"PROMPT = 'refund first'\n"}
State = AgentFixRun.State
HELD, SEEN = KEYS


@pytest.fixture(autouse=True)
def entitled():
    with (
        patch("simulate.views.agent_fix.check_ee_feature"),
        patch("mcp_server.usage_helpers.check_ee_feature"),
    ):
        yield


@pytest.fixture(autouse=True)
def turned_on(settings):
    settings.AGENT_FIX_OUTSIDE_ATTEMPTS_ENABLED = True
    settings.AGENT_FIX_WORST_CASE_USD_PER_EXECUTION = "0.05"
    settings.AGENT_FIX_WORST_CASE_USD_PER_EVAL = "0.02"


@pytest.fixture(autouse=True)
def base_source():
    """The agent's files as the run ran them."""
    with patch("simulate.services.agent_fix_agents.HostedSourceAcquirer") as acquirer:
        acquirer.return_value.acquire.return_value = (_tree(BASE), "")
        yield acquirer


@pytest.fixture
def both_failing(source_run):
    _receipts(source_run, {HELD: {"refunded": False}, SEEN: {"greeted": False}})
    return source_run


@pytest.fixture
def key_client(user, workspace):
    """An API key, as the `fagi` CLI sends it."""
    key = OrgApiKey.no_workspace_objects.create(
        name="cli",
        organization=user.organization,
        workspace=workspace,
        type="user",
        user=user,
    )
    client = WorkspaceAwareAPIClient()
    client.set_workspace(workspace)
    client.defaults["HTTP_X_API_KEY"] = key.api_key
    client.defaults["HTTP_X_SECRET_KEY"] = key.secret_key
    yield client
    client.stop_workspace_injection()


def _submit(client, source_run, files=FIXED, **fields):
    data = {
        "source_run_id": str(source_run.test_execution_id),
        "summary": "refund before closing",
        "base_commit": base_commit(source_run),
        "tree": SimpleUploadedFile("tree.tar.gz", _tree(files)),
        **fields,
    }
    return client.post(
        f"{EPOCHS}/coding-agent-attempts/", data=data, format="multipart"
    )


def _attempt(source_run, number, *, previous=None, state=State.NO_IMPROVEMENT, held=()):
    """An outside attempt made without the route: targets both cases, ``held`` held back."""
    fix_run = _fix_run(source_run, state=state, attempt_of=previous)
    AgentFixRun.no_workspace_objects.filter(id=fix_run.id).update(
        request={
            **fix_run.request,
            "target_scenario_keys": KEYS,
            "held_back_scenario_keys": list(held),
            "outside_candidate": {"summary": f"attempt {number}", "number": number},
        }
    )
    fix_run.refresh_from_db()
    return fix_run


def _running(fix_run):
    AgentFixRun.no_workspace_objects.filter(id=fix_run.id).update(
        state=State.RUNNING, started_at=timezone.now()
    )
    fix_run.refresh_from_db()
    return fix_run


def _add(fix_run, files=FIXED, attempt=1):
    """The engine's candidate upload, in verify mode."""
    with ExitStack() as stack:
        for one in _passing():
            stack.enter_context(one)
        return engine.add_candidate(
            fix_run, attempt=attempt, summary="refund first", tree=_tree(files)
        )[0]


def _finished(fix_run, label, *, state="completed", key=None):
    run = _run(fix_run.environment, keys=[KEYS[0]], key=key or label, fix_run=fix_run)
    HostedHarnessJob.no_workspace_objects.filter(id=run.id).update(
        fix_label=label, state=state
    )
    run.refresh_from_db()
    return run


def _scenario_name(source_run, key):
    return (
        _executions(source_run)
        .get(source_scenario__scenario_key=key)
        .source_scenario.name
    )


class TestSettingOff:
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("get", "coding-agent-brief/?source_run_id={run}"),
            ("post", "coding-agent-attempts/"),
            ("get", "{fix}/verdict/"),
        ],
        ids=["brief", "submit", "verdict"],
    )
    def test_the_routes_answer_not_found(
        self, settings, auth_client, source_run, method, path
    ):
        settings.AGENT_FIX_OUTSIDE_ATTEMPTS_ENABLED = False
        fix_run = _attempt(source_run, 1)
        url = f"{EPOCHS}/" + path.format(
            run=source_run.test_execution_id, fix=fix_run.id
        )

        response = (
            _submit(auth_client, source_run)
            if method == "post"
            else auth_client.get(url)
        )

        assert (response.status_code, response.json()["error"]) == (404, "not_found")
        assert AgentFixRun.no_workspace_objects.count() == 1


class TestSubmit:
    def test_the_first_attempt_verifies_the_tree_on_every_failing_case(
        self, auth_client, source_run, storage
    ):
        response = _submit(auth_client, source_run)

        assert response.status_code == 201, response.content
        fix_run = AgentFixRun.no_workspace_objects.get(id=response.json()["id"])
        request = FixRequest.model_validate(fix_run.request)
        assert fix_run.attempt_of_id is None
        assert request.outside_candidate.model_dump() == {
            "summary": "refund before closing",
            "number": 1,
        }
        assert (request.target_scenario_keys, request.protected_scenario_keys) == (
            [HELD],
            [SEEN],
        )
        assert set(request.surfaces) == set(Surface)
        # The engine reads the base the tree was checked on, and the tree as sent.
        stored = engine.submitted_archive(fix_run)
        assert engine.archive_files(stored) == FIXED
        assert engine.archive_files(engine.source_archive(fix_run)) == BASE

    def test_the_default_budget_is_the_attempt_s_own_hold(
        self, auth_client, source_run
    ):
        response = _submit(auth_client, source_run)

        fix_run = AgentFixRun.no_workspace_objects.get(id=response.json()["id"])
        request = FixRequest.model_validate(fix_run.request)
        hold = outside_attempt_hold_usd(source_run, request, None)
        assert response.json()["request"]["budget_credits"] == math.ceil(hold * 100)
        # Two scenarios, one searched target, no eval: a 0.10 screen, then 0.30 each
        # for the final check, its must-pass re-runs and the baseline.
        assert hold == Decimal("1.00")

    def test_a_given_budget_is_kept(self, auth_client, source_run):
        response = _submit(auth_client, source_run, budget_credits=900)

        assert response.json()["request"]["budget_credits"] == 900

    @pytest.mark.parametrize(
        ("files", "fields", "status", "code"),
        [
            (BASE, {}, 422, "fix_outside_tree_unchanged"),
            (
                {**FIXED, "prompts/system.md": b"never refund\n"},
                {"locked_paths": ["prompts/*"]},
                422,
                "fix_locked_path_changed",
            ),
            (
                {**FIXED, "big.bin": os.urandom(3 * 1024 * 1024)},
                {},
                413,
                "fix_patch_too_large",
            ),
            (
                {"agent.py": FIXED["agent.py"]},
                {"locked_paths": ["prompts/*"]},
                422,
                "fix_locked_path_changed",
            ),
            ({**FIXED, "../escape.py": b"x"}, {}, 400, "fix_tree_invalid"),
            ({**FIXED, "./a.py": b"x"}, {}, 400, "fix_tree_invalid"),
            ({**FIXED, "a//b.py": b"x"}, {}, 400, "fix_tree_invalid"),
            ({**FIXED, "a/./b.py": b"x"}, {}, 400, "fix_tree_invalid"),
            ({**FIXED, "a/b/": b"x"}, {}, 400, "fix_tree_invalid"),
            ({**FIXED, ".git/hooks/post": b"x"}, {}, 422, "fix_outside_tree_untracked"),
            (
                {**FIXED, "__pycache__/agent.pyc": b"\0\1"},
                {},
                422,
                "fix_outside_tree_untracked",
            ),
            (
                {**FIXED, "notes.txt": b"caf\xe9\n"},
                {},
                422,
                "fix_outside_tree_encoding",
            ),
            (
                {**FIXED, ".gitattributes": b"# endings\n* text=auto eol=lf\n"},
                {},
                422,
                "fix_outside_tree_attributes",
            ),
            (
                {**FIXED, "prompts/.gitattributes": b"*.md filter=lfs\n"},
                {},
                422,
                "fix_outside_tree_attributes",
            ),
        ],
        ids=[
            "unchanged",
            "locked-path",
            "patch-size",
            "locked-file-deleted",
            "path-escapes",
            "dot-part",
            "doubled-slash",
            "inner-dot",
            "trailing-slash",
            "under-git",
            "under-pycache",
            "not-utf8",
            "line-endings",
            "filter",
        ],
    )
    def test_a_tree_is_refused_before_any_sandbox(
        self, auth_client, source_run, temporal, files, fields, status, code
    ):
        response = _submit(auth_client, source_run, files, **fields)

        assert (response.status_code, response.json()["error"]) == (status, code)
        assert not AgentFixRun.no_workspace_objects.exists()
        temporal.start.assert_not_called()

    def test_an_unreadable_tree_is_refused(self, auth_client, source_run):
        response = auth_client.post(
            f"{EPOCHS}/coding-agent-attempts/",
            data={
                "source_run_id": str(source_run.test_execution_id),
                "tree": SimpleUploadedFile("tree.tar.gz", b"not a tarball"),
            },
            format="multipart",
        )

        assert (response.status_code, response.json()["error"]) == (
            400,
            "fix_tree_invalid",
        )

    def test_a_file_left_out_is_deleted(self, auth_client, source_run, storage):
        tree = {"agent.py": FIXED["agent.py"]}

        response = _submit(auth_client, source_run, tree)

        assert response.status_code == 201, response.content
        attempt = _running(
            AgentFixRun.no_workspace_objects.get(id=response.json()["id"])
        )
        candidate = _add(attempt, tree)
        patch_body = storage.objects[candidate.patch_object_key]
        assert b"deleted file mode" in patch_body
        assert b"prompts/system.md" in patch_body

    def test_a_candidate_that_deletes_a_locked_file_is_refused(self):
        with pytest.raises(HostedHarnessError) as refused:
            engine.candidate_patch(BASE, {"agent.py": FIXED["agent.py"]}, ["prompts/*"])

        assert refused.value.code == "fix_locked_path_changed"
        assert "prompts/system.md" in refused.value.message

    def test_new_files_are_allowed(self, auth_client, source_run):
        tree = {
            **FIXED,
            "refunds.py": b"x = 1\n",
            # Binary files go in the diff as they are; unsetting an attribute rewrites none.
            "logo.png": b"\x89PNG\0\xff",
            ".gitattributes": b"*.png -text binary\n",
        }

        response = _submit(auth_client, source_run, tree)

        assert response.status_code == 201, response.content

    def test_the_agent_s_own_attributes_left_as_they_are_are_accepted(
        self, auth_client, source_run, base_source
    ):
        attributes = {".gitattributes": b"* text=auto eol=lf\n*.bin filter=lfs\n"}
        base_source.return_value.acquire.return_value = (
            _tree({**BASE, **attributes}),
            "",
        )

        response = _submit(auth_client, source_run, {**FIXED, **attributes})

        assert response.status_code == 201, response.content

    def test_an_attempt_keeps_the_locks_before_it_and_may_add_more(
        self, auth_client, source_run
    ):
        first = _submit(auth_client, source_run, locked_paths=["prompts/*"])
        close_fix_run(first.json()["id"], State.NO_IMPROVEMENT)

        # Sent without the first attempt's lock, the second keeps it.
        unlocked = _submit(
            auth_client,
            source_run,
            {**FIXED, "prompts/system.md": b"never refund\n"},
            locked_paths=["tools.py"],
        )
        second = _submit(auth_client, source_run, locked_paths=["tools.py"])

        assert (unlocked.status_code, unlocked.json()["error"]) == (
            422,
            "fix_locked_path_changed",
        )
        assert second.status_code == 201, second.content
        assert second.json()["request"]["locked_paths"] == ["prompts/*", "tools.py"]
        close_fix_run(second.json()["id"], State.NO_IMPROVEMENT)
        brief = auth_client.get(
            f"{EPOCHS}/coding-agent-brief/",
            {"source_run_id": str(source_run.test_execution_id)},
        ).json()
        assert brief["locked_paths"] == ["prompts/*", "tools.py"]
        assert "`prompts/*`, `tools.py`" in brief["instructions"]

    @pytest.mark.parametrize("sent", ["b" * 40, ""], ids=["other-commit", "none"])
    def test_a_github_agent_s_moved_commit_is_refused(
        self, auth_client, source_run, sent
    ):
        payload = dict(source_run.payload)
        payload["source"] = {**(payload.get("source") or {}), "commit_sha": "a" * 40}
        HostedHarnessJob.no_workspace_objects.filter(id=source_run.id).update(
            payload=payload
        )

        response = _submit(auth_client, source_run, base_commit=sent)

        assert (response.status_code, response.json()["error"]) == (
            409,
            "fix_base_commit_moved",
        )
        assert not AgentFixRun.no_workspace_objects.exists()
        assert _submit(auth_client, source_run, base_commit="a" * 40).status_code == 201

    def test_a_run_takes_four_attempts(self, auth_client, source_run, temporal):
        previous = None
        for number in range(1, 5):
            previous = _attempt(source_run, number, previous=previous)

        response = _submit(auth_client, source_run)

        assert (response.status_code, response.json()["error"]) == (
            409,
            "fix_attempts_used",
        )
        temporal.start.assert_not_called()

    def test_an_engine_epoch_on_the_run_is_not_an_attempt(
        self, auth_client, source_run
    ):
        previous = None
        for number in range(1, 4):
            previous = _attempt(source_run, number, previous=previous)
        # An engine's fix stores no outside candidate, as null since the field exists.
        engine_made = _fix_run(source_run, state=State.NO_IMPROVEMENT)
        AgentFixRun.no_workspace_objects.filter(id=engine_made.id).update(
            request={**engine_made.request, "outside_candidate": None}
        )

        response = _submit(auth_client, source_run)

        assert response.status_code == 201, response.content
        assert response.json()["request"]["outside_candidate"]["number"] == 4
        assert response.json()["attempt_of_id"] == str(previous.id)

    def test_an_outside_attempt_is_not_retried(self, auth_client, source_run):
        failed = _attempt(source_run, 1, state=State.FAILED)

        read = auth_client.get(f"{EPOCHS}/{failed.id}/").json()
        retry = auth_client.post(f"{EPOCHS}/{failed.id}/retry/", format="json")

        assert read["retryable"] is False
        assert (retry.status_code, retry.json()["error"]) == (409, "fix_not_retryable")


class TestLineage:
    def test_the_next_attempt_follows_the_last_and_reuses_only_its_baseline(
        self, auth_client, source_run
    ):
        first = _running(
            AgentFixRun.no_workspace_objects.get(
                id=_submit(auth_client, source_run).json()["id"]
            )
        )
        first_candidate = _add(first)
        baseline = _check(first, label="baseline-r1")
        mine = _check(
            first, label="c1-target", version_id=first_candidate.agent_version_id
        )
        HostedHarnessJob.no_workspace_objects.filter(
            id__in=[baseline.id, mine.id]
        ).update(state="completed")
        close_fix_run(first.id, State.NO_IMPROVEMENT)

        second = _submit(
            auth_client, source_run, {**FIXED, "agent.py": b"PROMPT = 'x'\n"}
        )

        assert second.status_code == 201, second.content
        second = _running(AgentFixRun.no_workspace_objects.get(id=second.json()["id"]))
        assert (second.attempt_of_id, fix_lineage(second)) == (
            first.id,
            [second.id, first.id],
        )
        assert second.suite_version_id == first.suite_version_id
        kept = {"outside_candidate", "budget_usd"}
        assert {
            key: value for key, value in second.request.items() if key not in kept
        } == {key: value for key, value in first.request.items() if key not in kept}
        second_candidate = _add(second, {**FIXED, "agent.py": b"PROMPT = 'x'\n"})
        assert _check(second, label="baseline-r1").id == baseline.id
        # Every attempt names its candidate's runs alike; another version is another run.
        again = _check(
            second, label="c1-target", version_id=second_candidate.agent_version_id
        )
        assert again.id != mine.id

    @pytest.mark.parametrize(
        ("first_won", "evals", "graded"),
        [(True, ["config"], True), (True, [], False), (False, [], False)],
        ids=["first-graded", "first-won-without-evals", "no-winner"],
    )
    def test_a_baseline_a_winner_graded_keeps_its_grading(
        self, auth_client, source_run, first_won, evals, graded
    ):
        first = _running(
            AgentFixRun.no_workspace_objects.get(
                id=_submit(auth_client, source_run).json()["id"]
            )
        )
        won = _add(first)
        baseline = _check(first, label="baseline-r1")
        HostedHarnessJob.no_workspace_objects.filter(id=baseline.id).update(
            state="completed"
        )
        if first_won:
            AgentFixCandidate.no_workspace_objects.filter(id=won.id).update(
                outcome="winner"
            )
            # Its grading queues evals only when the environment has some.
            with (
                patch.object(engine, "runnable_eval_configs", return_value=evals),
                patch.object(engine, "queue_evals_for_finished_calls"),
            ):
                engine._grade_winner(first, won.id)
        baseline.refresh_from_db()
        assert (baseline.graded_at is not None) is graded
        close_fix_run(first.id, State.READY if first_won else State.NO_IMPROVEMENT)
        other = {**FIXED, "agent.py": b"PROMPT = 'x'\n"}
        second = _running(
            AgentFixRun.no_workspace_objects.get(
                id=_submit(auth_client, source_run, other).json()["id"]
            )
        )
        winner = _add(second, other)
        AgentFixCandidate.no_workspace_objects.filter(id=winner.id).update(
            outcome="winner"
        )

        assert _check(second, label="baseline-r1").id == baseline.id

        baseline.refresh_from_db()
        # A graded baseline stays the first attempt's, in its read and its bill. One
        # nothing graded, even under a winner, is the second's to grade, as a retry's is.
        assert baseline.graded_for_id == (None if graded else second.id)
        first = AgentFixRun.no_workspace_objects.get(id=first.id)
        assert graded_check_runs(first, won)[0] == [baseline]
        second = AgentFixRun.no_workspace_objects.get(id=second.id)
        assert graded_check_runs(second, winner)[0] == [baseline]
        with (
            patch.object(engine, "runnable_eval_configs", return_value=["config"]),
            patch.object(engine, "queue_evals_for_finished_calls") as queued,
        ):
            engine._grade_winner(second, winner.id)
        assert [call.args[0].id for call in queued.call_args_list] == (
            [] if graded else [baseline.test_execution_id]
        )
        baseline.refresh_from_db()
        assert baseline.graded_at is not None

    def test_the_hold_holds_each_check_at_its_worst(self, source_run):
        request = FixRequest(
            target_scenario_keys=[f"case-{n}" for n in range(12)],
            held_back_scenario_keys=[f"case-{n}" for n in range(4)],
            failing_sub_goals=["refunded"],
            surfaces=[Surface.PROMPT],
            budget_usd=1,
            max_minutes=30,
        )
        first = _attempt(source_run, 1)
        second = _attempt(source_run, 2, previous=first)
        selectors = "simulate.selectors.agent_fix"

        def hold(previous):
            with (
                patch(f"{selectors}.inventory_size", return_value=30),
                patch(f"{selectors}.runnable_eval_config_ids", return_value=["a", "b"]),
            ):
                return outside_attempt_hold_usd(source_run, request, previous)

        # 30 scenarios, 8 searched, 4 held back, 2 evals: a $1.00 screen, $8.10 for
        # the final check, $4.50 for its must-pass re-runs and $8.10 for the baseline.
        assert hold(None) == Decimal("21.70")
        _finished(first, "baseline-r1")
        _finished(first, "baseline-r2", state="failed")
        # One repeat read again holds its evals only: $0.04 a conversation.
        assert hold(second) == Decimal("20.20")
        _finished(first, "baseline-r2-again", key="r2-again")
        _finished(first, "baseline-r3")
        assert hold(second) == Decimal("17.20")


class TestBrief:
    def test_never_names_a_held_back_case_and_says_how_to_submit(
        self, auth_client, both_failing
    ):
        _attempt(both_failing, 1, held=[HELD])

        response = auth_client.get(
            f"{EPOCHS}/coding-agent-brief/",
            {"source_run_id": str(both_failing.test_execution_id)},
        )

        assert response.status_code == 200, response.content
        body = response.json()
        text = json.dumps(body)
        assert HELD not in text
        # An unnamed scenario reads by its key.
        name = _scenario_name(both_failing, HELD)
        assert not name or name not in text
        assert [one["key"] for one in body["targets"]] == [SEEN]
        assert [one["scenario_key"] for one in body["failures"]] == [SEEN]
        assert body["files"] == sorted(BASE)
        assert body["attempts"] == {"used": 1, "max": 4}
        assert body["blocked"] is None
        instructions = body["instructions"]
        for line in (
            HELD_BACK_RULE,
            f"fagi epoch submit --run {both_failing.test_execution_id}",
            "fagi epoch verdict <attempt-id> --wait",
            "at most 4 attempts",
            "greeted",
        ):
            assert line in instructions

    def test_says_when_the_run_s_attempts_are_used(self, auth_client, source_run):
        previous = None
        for number in range(1, 5):
            previous = _attempt(source_run, number, previous=previous)

        body = auth_client.get(
            f"{EPOCHS}/coding-agent-brief/",
            {"source_run_id": str(source_run.test_execution_id)},
        ).json()

        assert body["blocked"]["code"] == "fix_attempts_used"


SHOWN_TIERS = ("static", "target")


def _detail(name, held):
    """A tier's detail: the static and target tiers run on what the coder was given, every
    other tier on the held-back cases too, so the engine names them there."""
    return f"{name}: 3 of 4" if name in SHOWN_TIERS else f"{name} on {held}"


def _verdict(held):
    """A rejected final check that names ``held`` wherever an engine's verdict can."""
    return {
        "accepted": False,
        "reason": f"must-pass: {held} refunded went down",
        "target_pass_rate": [0.1, 0.9],
        "held_back_pass_rate": [0.2, 0.4],
        "must_pass_regressions": [f"{held}: refunded", f"{SEEN}: greeted"],
        "must_pass_within_noise": [f"{held}: greeted"],
        "tiers": [
            {
                "name": name,
                "status": "failed" if name == "must-pass" else "passed",
                "detail": _detail(name, held),
                "parts": [
                    {"name": "one", "status": "passed", "detail": _detail(name, held)}
                ],
            }
            for name in VERDICT_TIERS
        ],
    }


class TestVerdict:
    def test_the_held_back_tiers_read_as_a_status_only(self, auth_client, source_run):
        attempt = AgentFixRun.no_workspace_objects.get(
            id=_submit(auth_client, source_run).json()["id"]
        )
        candidate = _add(_running(attempt))
        AgentFixCandidate.no_workspace_objects.filter(id=candidate.id).update(
            outcome="rejected", verdict=_verdict(HELD)
        )
        AgentFixRun.no_workspace_objects.filter(id=attempt.id).update(
            request={
                **attempt.request,
                "target_scenario_keys": KEYS,
                "held_back_scenario_keys": [HELD],
            },
            report={
                "ending": "no_improvement",
                "summary": f"no_improvement: {HELD} did not hold",
                "winner_candidate_id": None,
            },
        )
        close_fix_run(attempt.id, State.NO_IMPROVEMENT)

        response = auth_client.get(f"{EPOCHS}/{attempt.id}/verdict/")

        assert response.status_code == 200, response.content
        body = response.json()
        assert HELD not in json.dumps(body)
        assert {tier["name"]: tier["detail"] for tier in body["tiers"]} == {
            name: _detail(name, HELD) if name in SHOWN_TIERS else ""
            for name in VERDICT_TIERS
        }
        # The closing summary joins every failed tier's detail: a candidate's is not served.
        assert body["summary"] == ""
        assert body["must_pass_regressions"] == [f"{SEEN}: greeted"]
        assert (
            body["number"],
            body["state"],
            body["step"],
            body["ending"],
            body["patch_available"],
        ) == (1, "no_improvement", "done", "no_improvement", True)

    def test_a_static_refusal_reads_from_the_report_alone(
        self, auth_client, source_run
    ):
        # The engine's static checks refuse before any candidate is uploaded.
        attempt = AgentFixRun.no_workspace_objects.get(
            id=_submit(auth_client, source_run).json()["id"]
        )
        AgentFixRun.no_workspace_objects.filter(id=attempt.id).update(
            report={
                "ending": "no_improvement",
                "summary": "no_improvement: rejected at the static tier: agent.py does"
                " not parse",
                "winner_candidate_id": None,
            }
        )
        close_fix_run(attempt.id, State.NO_IMPROVEMENT)

        body = auth_client.get(f"{EPOCHS}/{attempt.id}/verdict/").json()

        assert body["summary"].endswith("agent.py does not parse")
        assert (
            body["tiers"],
            body["must_pass_regressions"],
            body["patch_available"],
        ) == (
            [],
            [],
            False,
        )
        assert body["pull_request_url"] == ""
        read = auth_client.get(f"{EPOCHS}/{attempt.id}/").json()
        assert read["report"]["summary"] == body["summary"]

    def test_an_engine_epoch_has_no_attempt_verdict(self, auth_client, source_run):
        fix_run = _fix_run(source_run, state=State.NO_IMPROVEMENT)

        response = auth_client.get(f"{EPOCHS}/{fix_run.id}/verdict/")

        assert response.status_code == 404


class TestOldEngineTemplate:
    """An engine template from before verify mode ignores the outside candidate: it
    proposes its own and calls a model. Both are refused, so it stops."""

    @pytest.fixture
    def attempt(self, auth_client, source_run):
        fix_run = AgentFixRun.no_workspace_objects.get(
            id=_submit(auth_client, source_run).json()["id"]
        )
        return _armed(_running(fix_run))

    @pytest.mark.parametrize(
        ("files", "number", "code"),
        [
            (
                {**FIXED, "agent.py": b"PROMPT = 'mine'\n"},
                "1",
                "fix_outside_tree_mismatch",
            ),
            (FIXED, "2", "fix_outside_attempt_invalid"),
        ],
        ids=["another-tree", "another-attempt"],
    )
    def test_a_candidate_other_than_the_submitted_tree_is_refused(
        self, api_client, attempt, files, number, code
    ):
        response = _engine(
            api_client,
            "post",
            attempt,
            "candidates/",
            data={
                "attempt": number,
                "tree": SimpleUploadedFile("t.tar.gz", _tree(files)),
            },
            format="multipart",
        )

        assert (response.status_code, response.json()["error"]) == (422, code)
        assert not attempt.candidates.exists()

    def test_a_relaunch_gets_the_candidate_and_check_runs_it_made(
        self, api_client, attempt
    ):
        def upload():
            with ExitStack() as stack:
                for one in _passing():
                    stack.enter_context(one)
                return _engine(
                    api_client,
                    "post",
                    attempt,
                    "candidates/",
                    data={
                        "attempt": "1",
                        "tree": SimpleUploadedFile("t.tar.gz", _tree(FIXED)),
                    },
                    format="multipart",
                )

        first, again = upload(), upload()

        assert (first.status_code, again.status_code) == (201, 201), again.content
        assert first.json()["candidate_id"] == again.json()["candidate_id"]
        assert attempt.candidates.count() == 1
        assert _check(attempt, label="baseline-r1").id == (
            _check(attempt, label="baseline-r1").id
        )

    def test_a_model_call_is_refused_without_reaching_the_gateway(
        self, api_client, attempt
    ):
        with patch("simulate.services.agent_fix_models.httpx.post") as gateway:
            response = _engine(
                api_client,
                "post",
                attempt,
                "v1/chat/completions",
                data={"model": ENGINE_MODEL, "messages": []},
                format="json",
            )

        assert (response.status_code, response.json()["error"]) == (
            422,
            "fix_verify_only_no_models",
        )
        gateway.assert_not_called()

    def test_the_engine_reads_the_submitted_tree(self, api_client, attempt):
        response = _engine(api_client, "get", attempt, "submitted/")

        assert response.status_code == 200
        assert response["Content-Type"] == "application/gzip"
        assert engine.archive_files(response.content) == FIXED

    def test_an_engine_epoch_has_no_submitted_tree(self, api_client, source_run):
        fix_run = _armed(_fix_run(source_run))

        response = _engine(api_client, "get", fix_run, "submitted/")

        assert (response.status_code, response.json()["error"]) == (404, "not_found")


@pytest.fixture
def planted(source_run):
    """An Epoch the engine made, with its held-back case named in every field a read of it
    can carry: the request, the report, and its candidate's verdict and override."""
    fix_run = _fix_run(source_run)
    candidate = _candidate(fix_run)
    AgentFixCandidate.no_workspace_objects.filter(id=candidate.id).update(
        outcome="rejected", verdict=_verdict(HELD)
    )
    AgentFixRun.no_workspace_objects.filter(id=fix_run.id).update(
        state=State.NO_IMPROVEMENT,
        request={
            **fix_run.request,
            "target_scenario_keys": KEYS,
            "held_back_scenario_keys": [HELD],
            "variation_parents": {f"{HELD}-v1": HELD},
        },
        plan={
            "target": 1,
            "held_back": 1,
            "must_pass": 1,
            "max_candidates": 1,
            "repeats": 3,
            "baseline_spread_pts": 0,
        },
        report={
            "ending": "no_improvement",
            "summary": f"no_improvement: repeats: {HELD} at 33%, 67%; baseline 17%",
            "winner_candidate_id": None,
        },
    )
    return fix_run


class TestHeldBackOnlyInASession:
    @pytest.mark.parametrize(
        "client", ["auth_client", "key_client"], ids=["mcp", "key"]
    )
    def test_a_read_that_is_not_a_session_names_no_held_back_case(
        self, request, planted, client
    ):
        client = request.getfixturevalue(client)

        one = client.get(f"{EPOCHS}/{planted.id}/")
        page = client.get(f"{EPOCHS}/", {"environment_id": str(planted.environment_id)})

        for response in (one, page):
            assert response.status_code == 200, response.content
            text = json.dumps(response.json())
            assert HELD not in text
            assert "33%" not in text
        body = one.json()
        assert body["request"]["target_scenario_keys"] == [SEEN]
        verdict = body["candidates"][0]["verdict"]
        assert verdict["must_pass_regressions"] == [f"{SEEN}: greeted"]
        assert verdict["held_back_pass_rate"] is None
        assert verdict["reason"] == "must-pass: failed"
        assert body["candidates"][0]["override"]["regressions"] == [f"{SEEN}: greeted"]

    def test_an_mcp_tool_call_names_no_held_back_case(self, auth_client, planted):
        response = auth_client.post(
            "/mcp/internal/tool-call/",
            {"tool_name": "get_epoch", "params": {"id": str(planted.id)}},
            format="json",
        )

        assert response.status_code == 200, response.content
        text = json.dumps(response.json())
        assert SEEN in text
        assert HELD not in text

    def test_a_signed_in_session_still_marks_the_held_back_cases(
        self, browser_client, planted
    ):
        response = browser_client.get(f"{EPOCHS}/{planted.id}/")

        assert response.status_code == 200, response.content
        body = response.json()
        assert body["request"]["held_back_scenario_keys"] == [HELD]
        assert body["request"]["target_scenario_keys"] == KEYS
        verdict = body["candidates"][0]["verdict"]
        assert verdict["must_pass_regressions"] == [
            f"{HELD}: refunded",
            f"{SEEN}: greeted",
        ]
        assert verdict["held_back_pass_rate"] == [0.2, 0.4]
        assert "33%" in body["report"]["summary"]
        assert (
            body["candidates"][0]["override"]["regressions"]
            == verdict["must_pass_regressions"]
        )
