"""Agent versions from pushes: which environments watch, when a head is new, and how
its version is made, checked and activated."""

import copy
import dataclasses
import re
import uuid
from unittest.mock import MagicMock, patch
from urllib.parse import unquote

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from simulate.models import AgentVersion, HarnessAgentVersionRuntime, HostedHarnessJob
from simulate.services import harness_agent_changes as changes
from simulate.services import harness_github as github
from simulate.services import harness_provider
from simulate.services.harness_agent_changes import (
    HeadResult,
    WatchedSource,
    github_head,
    observe_repository_head,
    poll_repository_heads,
    receive_push,
    validate_public_github_source,
    watched_sources,
)
from simulate.services.harness_agent_versions import (
    HAND_MOVE_KEY,
    apply_agent_version,
    change_credentials,
    discard_agent_version,
    pinned_credential_values,
    stamp_built_commit,
    version_credential_names,
)
from simulate.services.harness_github import GitHubResponse
from simulate.services.harness_provider import _preflight_source_connectors
from simulate.services.harness_versioning.versions import move_default
from simulate.services.hosted_harness import HostedHarnessError, _soft_delete
from simulate.services.hosted_harness_gateway import _empty_source_archive
from simulate.tests.test_harness_agent_creation import VALUES, _body, _job, _post
from simulate.tests.test_harness_agent_creation import (  # noqa: F401 - fixture
    gates as gates,
)
from simulate.tests.test_harness_agent_versions import (
    OLD,
    _a1,
    _create,
    _defaults,
    _environment,
    _outcomes,
    _passing,
    _preflight,
    _shared,
    _side,
)
from simulate.tests.test_harness_agent_versions import (  # noqa: F401 - fixture
    scheduled as scheduled,
)
from simulate.tests.test_harness_versioning_assembly import (  # noqa: F401 - fixture
    gateway as gateway,
)
from simulate.tests.test_harness_versioning_backfill import _agent_version, _definition
from simulate.tests.test_harness_versioning_backfill import (  # noqa: F401 - fixture
    storage as storage,
)

pytestmark = pytest.mark.django_db

HEAD_X = "a" * 40
HEAD_Y = "d" * 40
HEAD_Z = "e" * 40
_COMMITS = re.compile(
    r"^https://api\.github\.com/(?:repos/([^/]+/[^/]+)|repositories/(\d+))/commits/(.+)$"
)
_REPOSITORY = re.compile(r"^https://api\.github\.com/repos/([^/]+/[^/]+)$")


@pytest.fixture(autouse=True)
def flags(settings):
    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
    settings.GITHUB_API_ENDPOINT = "https://api.github.com"
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def seen_before():
    """Backfilled environments here were seen before: their first sight is tested on
    its own (TestFirstObservation)."""
    with patch.object(changes, "_seed", return_value=False):
        yield


@pytest.fixture(autouse=True)
def identified():
    """Watched repositories here have their GitHub id recorded: recording it is
    tested on its own (TestRepositoryId)."""
    with patch.object(changes, "_identified", return_value=True):
        yield


class _GitHub:
    """Heads by (repository, ref), or ("id:<id>", ref) when read by GitHub's id, and
    repository ids by name; answers with an ETag and honours If-None-Match."""

    def __init__(self):
        self.heads: dict[tuple[str, str], str] = {}
        self.ids: dict[str, int] = {}
        self.calls: list[tuple[str, str, str | None]] = []

    def __call__(self, url, headers=None, timeout=None):
        assert "Authorization" not in (headers or {})
        named = _REPOSITORY.match(url)
        if named:
            self.calls.append((named[1].lower(), "", None))
            response = MagicMock()
            found = self.ids.get(named[1].lower())
            response.status_code, response.headers = (404 if found is None else 200), {}
            response.json.return_value = {"id": found, "private": False}
            return response
        match = _COMMITS.match(url)
        assert match, url
        repository = match[1].lower() if match[1] else f"id:{match[2]}"
        ref = unquote(match[3])
        self.calls.append((repository, ref, (headers or {}).get("If-None-Match")))
        response = MagicMock()
        sha = self.heads.get((repository, ref))
        if sha is None:
            response.status_code, response.headers = 404, {}
            return response
        etag = f'"{sha[:8]}"'
        if (headers or {}).get("If-None-Match") == etag:
            response.status_code, response.headers = 304, {}
            return response
        response.status_code, response.headers, response.text = 200, {"ETag": etag}, sha
        return response


@pytest.fixture
def gh():
    fake = _GitHub()
    with patch.object(github.requests, "get", side_effect=fake):
        yield fake


def _push_rows(environment):
    return list(
        HarnessAgentVersionRuntime.no_workspace_objects.filter(
            trigger="push",
            agent_payload__created_for_environment=str(environment.id),
        ).order_by("created_at")
    )


def _push_versions(definition_id):
    return list(
        HarnessAgentVersionRuntime.no_workspace_objects.filter(
            agent_definition_id=definition_id, trigger="push"
        ).order_by("created_at")
    )


def _observe(environment):
    environment.refresh_from_db()
    return observe_repository_head(environment.agent_version.agent_definition_id)


def _hand_move(environment):
    environment.refresh_from_db()
    return ((environment.payload or {}).get("metadata") or {}).get(HAND_MOVE_KEY)


def _edit_payload(environment, edit):
    payload = dict(environment.payload)
    edit(payload)
    HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
        payload=payload
    )
    environment.refresh_from_db()


class TestGitHubHead:
    def test_a_head_is_cached_with_its_etag_for_a_day(self, gh):
        gh.heads[("acme/agent", "main")] = HEAD_Y
        with patch.object(changes.cache, "set", wraps=cache.set) as stored:
            first = github_head("acme/agent", "main", purpose="t")
        assert first == HeadResult("ok", HEAD_Y)
        key, value, timeout = stored.call_args.args
        assert value == {"etag": f'"{HEAD_Y[:8]}"', "sha": HEAD_Y}
        assert timeout >= 86400

        second = github_head("acme/agent", "main", purpose="t")

        assert second == HeadResult("not_modified", HEAD_Y)
        assert gh.calls[1][2] == f'"{HEAD_Y[:8]}"'

    def test_a_body_that_is_not_a_commit_is_an_error(self, gh):
        gh.heads[("acme/agent", "main")] = "not-a-sha"
        assert github_head("acme/agent", "main", purpose="t").status == "error"

    def test_the_default_branch_and_quoted_refs(self, gh):
        gh.heads[("acme/agent", "HEAD")] = HEAD_X
        gh.heads[("acme/agent", "feature/a b")] = HEAD_Y
        assert github_head("acme/agent", None, purpose="t").sha == HEAD_X
        with patch.object(github.requests, "get", side_effect=gh) as get:
            github_head("acme/agent", "feature/a b", purpose="t")
        assert get.call_args.args[0].endswith("/commits/feature/a%20b")


class TestWatchedSources:
    def test_selection(self, user, workspace, storage):
        environment = _environment(user, workspace, "watch-sel", storage)
        a1 = _a1(environment)

        def watched():
            return [one.environment_id for one in watched_sources()]

        [found] = watched_sources()
        assert found == WatchedSource(
            environment_id=environment.id,
            definition_id=a1.agent_definition_id,
            parent_version_id=a1.id,
            repository="acme/agent",
            ref="main",
            commit_sha=None,
            organization_id=environment.organization_id,
        )
        assert watched_sources(repository="ACME/Agent") == [found]
        assert watched_sources(repository="acme/other") == []

        for edit in (
            lambda p: p["source"].update(ref="refs/heads/main"),
            lambda p: p["source"].update(ref="refs/tags/main"),
        ):
            _edit_payload(environment, edit)
            assert watched_sources()[0].ref == "main"

        original = copy.deepcopy(environment.payload)

        def restore(payload):
            payload.clear()
            payload.update(copy.deepcopy(original))

        refused = [
            lambda p: p["source"].update(visibility="private", installation_id="1"),
            lambda p: p["source"].update(ref="b" * 40),
            lambda p: p.update(
                source={"kind": "archive", "archive_artifact_id": str(uuid.uuid4())}
            ),
            lambda p: p.update(source={"kind": "provider"}),
            lambda p: p["metadata"].update(agent_definition_owner="connected"),
            lambda p: p["metadata"].update(scenario_extend={"id": "x"}),
        ]
        for edit in refused:
            _edit_payload(environment, restore)
            assert watched() == [environment.id]
            _edit_payload(environment, edit)
            assert watched() == [], edit

        _edit_payload(environment, restore)
        for state in ("running", "failed"):
            HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
                state=state
            )
            assert watched() == []
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            state="completed"
        )
        assert watched() == [environment.id]
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            agent_version=None
        )
        assert watched() == []

    def test_a_deleted_environment_is_not_watched(self, user, workspace, storage):
        environment = _environment(user, workspace, "watch-del", storage)
        _soft_delete(environment)
        assert watched_sources() == []

    def test_a_shared_legacy_row_stands_for_each_environments_repository(
        self, user, workspace, storage
    ):
        definition = _definition(user, workspace)
        _agent_version(definition)
        first = _environment(user, workspace, "watch-a", storage, definition=definition)
        second = _environment(
            user,
            workspace,
            "watch-b",
            storage,
            definition=definition,
            edit=lambda p: p["source"].update(repository="acme/other"),
        )
        assert first.agent_version_id == second.agent_version_id

        found = {one.environment_id: one.repository for one in watched_sources()}

        assert found == {first.id: "acme/agent", second.id: "acme/other"}
        assert [
            one.environment_id for one in watched_sources(repository="acme/other")
        ] == [second.id]


class TestPush:
    def test_a_moved_head_makes_a_draft_that_becomes_the_default(
        self,
        user,
        workspace,
        storage,
        gh,
        scheduled,
        django_capture_on_commit_callbacks,
    ):
        environment = _environment(user, workspace, "push-new", storage)
        a1 = _a1(environment)
        gh.heads[("acme/agent", "main")] = HEAD_Y

        with django_capture_on_commit_callbacks(execute=True):
            outcomes = _observe(environment)

        assert outcomes == {str(environment.id): "created"}
        [row] = _push_rows(environment)
        version = row.agent_version
        assert version.status == "draft"
        assert row.source["commit_sha"] == HEAD_Y
        assert row.source["repository"] == "acme/agent"
        assert row.agent_payload["requested_by_environments"] == [str(environment.id)]
        assert row.agent_payload["source_access"]["visibility"] == "public"
        assert row.parent.agent_version_id == a1.id
        assert scheduled.call_count == 1

        assert _preflight(version).status == "passed"

        assert _defaults(environment) == [version.id]
        assert _outcomes(version) == {str(environment.id): "activated"}
        assert _hand_move(environment) is None

    def test_a_version_from_a_push_keeps_the_app_installation(
        self, user, workspace, storage, gh, scheduled
    ):
        environment = _environment(user, workspace, "push-app", storage)
        a1 = HarnessAgentVersionRuntime.no_workspace_objects.get(
            agent_version_id=_a1(environment).id
        )
        a1.agent_payload = {
            **a1.agent_payload,
            "source_access": {
                **a1.agent_payload["source_access"],
                "installation_id": "42",
            },
        }
        a1.save(update_fields=["agent_payload"])
        gh.heads[("acme/agent", "main")] = HEAD_Y

        assert _observe(environment) == {str(environment.id): "created"}

        [row] = _push_rows(environment)
        assert row.agent_payload["source_access"]["installation_id"] == "42"

    def test_one_version_for_environments_with_the_same_facts(
        self, user, workspace, storage, gh, scheduled
    ):
        first, second = _shared(user, workspace, storage, "push-two")
        gh.heads[("acme/agent", "main")] = HEAD_Y

        outcomes = _observe(first)

        assert outcomes == {str(first.id): "created", str(second.id): "reused"}
        [row] = _push_versions(_a1(first).agent_definition_id)
        assert row.agent_payload["requested_by_environments"] == [
            str(first.id),
            str(second.id),
        ]
        # One read of the head for both.
        assert len(gh.calls) == 1
        assert _preflight(row.agent_version).status == "passed"
        assert _defaults(first, second) == [row.agent_version_id] * 2

    def test_other_credential_sets_get_versions_of_their_own(
        self, user, workspace, storage, gh, scheduled
    ):
        first, second = _shared(user, workspace, storage, "push-sets")
        a1 = _a1(first)
        a2 = change_credentials(second, secret_values={"OPENAI_API_KEY": "v2"})
        assert _preflight(a2).status == "passed"
        gh.heads[("acme/agent", "main")] = HEAD_Y

        outcomes = _observe(first)

        assert outcomes == {str(first.id): "created", str(second.id): "created"}
        rows = _push_versions(a2.agent_definition_id)
        assert len(rows) == 2
        for row in rows:
            assert _preflight(row.agent_version).status == "passed"
        by_parent = {row.parent.agent_version_id: row.agent_version_id for row in rows}
        assert _defaults(first, second) == [by_parent[a1.id], by_parent[a2.id]]

    @pytest.mark.parametrize("webhook_first", [True, False])
    def test_webhook_and_poller_on_one_head_make_one_version(
        self, user, workspace, storage, gh, scheduled, webhook_first
    ):
        environment = _environment(
            user, workspace, f"push-both-{webhook_first}", storage
        )
        gh.heads[("acme/agent", "main")] = HEAD_Y
        calls = [
            lambda: receive_push("acme/agent", "refs/heads/main", "main"),
            poll_repository_heads,
        ]
        if not webhook_first:
            calls.reverse()

        first, second = (call() for call in calls)

        assert first == {"created": 1}
        assert second == {"unchanged": 1}
        assert len(_push_rows(environment)) == 1

    def test_a_roll_back_by_hand_is_not_undone(
        self, user, workspace, storage, gh, scheduled
    ):
        environment = _environment(user, workspace, "push-back", storage)
        a1 = _a1(environment)
        gh.heads[("acme/agent", "main")] = HEAD_Y
        _observe(environment)
        [row] = _push_rows(environment)
        assert _preflight(row.agent_version).status == "passed"
        environment.refresh_from_db()
        move_default(
            environment,
            "agent",
            a1.id,
            expected_default_id=row.agent_version_id,
            user=user,
        )

        assert _observe(environment) == {str(environment.id): "unchanged"}
        assert _defaults(environment) == [a1.id]

        a3 = change_credentials(environment, secret_values={"OPENAI_API_KEY": "v3"})
        assert _preflight(a3).status == "passed"
        assert _defaults(environment) == [a3.id]
        assert _observe(environment) == {str(environment.id): "unchanged"}
        assert len(_push_rows(environment)) == 1

    def test_a_failed_check_is_not_repeated(
        self, user, workspace, storage, gh, scheduled
    ):
        environment = _environment(user, workspace, "push-fail", storage)
        gh.heads[("acme/agent", "main")] = HEAD_Y
        _observe(environment)
        [row] = _push_rows(environment)
        failing = _passing(return_value=[{"aliases": ["OPENAI_API_KEY"], "ok": False}])
        assert _preflight(row.agent_version, failing).status == "failed"

        assert _observe(environment) == {str(environment.id): "unchanged"}
        assert len(_push_rows(environment)) == 1

        # Fixed credentials move the default: the head is asked for once more.
        fixed = change_credentials(environment, secret_values={"OPENAI_API_KEY": "v2"})
        assert _preflight(fixed).status == "passed"
        assert _observe(environment) == {str(environment.id): "created"}
        assert _observe(environment) == {str(environment.id): "unchanged"}
        assert len(_push_rows(environment)) == 2

        gh.heads[("acme/agent", "main")] = HEAD_Z
        assert _observe(environment) == {str(environment.id): "created"}

    def test_a_discarded_version_counts_as_seen(
        self, user, workspace, storage, gh, scheduled
    ):
        environment = _environment(user, workspace, "push-discard", storage)
        gh.heads[("acme/agent", "main")] = HEAD_Y
        _observe(environment)
        [row] = _push_rows(environment)
        discard_agent_version(row.agent_version, by_environment=environment.id)

        assert _observe(environment) == {str(environment.id): "unchanged"}
        assert len(_push_rows(environment)) == 1

    def test_a_credential_change_racing_a_push_is_caught_up(
        self, user, workspace, storage, gh, scheduled
    ):
        environment = _environment(user, workspace, "push-race", storage)
        gh.heads[("acme/agent", "main")] = HEAD_Y
        _observe(environment)
        [a2_row] = _push_rows(environment)
        a2 = a2_row.agent_version
        assert _preflight(a2).status == "passed"
        gh.heads[("acme/agent", "main")] = HEAD_Z
        assert _observe(environment) == {str(environment.id): "created"}
        a3 = _push_rows(environment)[1].agent_version
        # Made from A2 while A3 was still checked: Y with new values.
        a4 = change_credentials(
            environment, secret_values={"OPENAI_API_KEY": "v4"}, parent=a2
        )
        assert _preflight(a3).status == "passed"
        assert _preflight(a4).status == "passed"
        assert _defaults(environment) == [a4.id]
        assert _side(a4).source["commit_sha"] == HEAD_Y

        assert _observe(environment) == {str(environment.id): "created"}

        a5_row = _push_rows(environment)[2]
        assert a5_row.parent.agent_version_id == a4.id
        assert a5_row.source["commit_sha"] == HEAD_Z
        assert _preflight(a5_row.agent_version).status == "passed"
        assert _defaults(environment) == [a5_row.agent_version_id]

    @pytest.mark.parametrize("rotation_lands", ["first", "second"])
    def test_a_push_while_a_credential_change_is_checked_keeps_its_values(
        self, user, workspace, storage, gh, scheduled, rotation_lands
    ):
        environment = _environment(
            user, workspace, f"push-rotation-{rotation_lands}", storage
        )
        rotated = change_credentials(
            environment, secret_values={"OPENAI_API_KEY": "v2"}
        )
        gh.heads[("acme/agent", "main")] = HEAD_Y

        assert _observe(environment) == {str(environment.id): "created"}

        [row] = _push_rows(environment)
        assert row.parent.agent_version_id == rotated.id
        pushed = row.agent_version
        order = (rotated, pushed) if rotation_lands == "first" else (pushed, rotated)
        for version in order:
            assert _preflight(version).status == "passed"
        assert _defaults(environment) == [pushed.id]
        assert pinned_credential_values(environment)["OPENAI_API_KEY"] == "v2"
        assert _observe(environment) == {str(environment.id): "unchanged"}

    def test_a_push_on_a_failed_credential_change_is_asked_again_once_fixed(
        self, user, workspace, storage, gh, scheduled
    ):
        environment = _environment(user, workspace, "push-rotation-failed", storage)
        a1 = _a1(environment)
        rotated = change_credentials(
            environment, secret_values={"OPENAI_API_KEY": "bad"}
        )
        gh.heads[("acme/agent", "main")] = HEAD_Y
        assert _observe(environment) == {str(environment.id): "created"}
        [row] = _push_rows(environment)
        # Built on the bad values, it fails with them.
        assert row.parent.agent_version_id == rotated.id
        failing = _passing(return_value=[{"aliases": ["OPENAI_API_KEY"], "ok": False}])
        assert _preflight(rotated, failing).status == "failed"
        assert _preflight(row.agent_version, failing).status == "failed"
        assert _defaults(environment) == [a1.id]
        assert _observe(environment) == {str(environment.id): "unchanged"}

        fixed = change_credentials(environment, secret_values={"OPENAI_API_KEY": "v3"})
        assert _side(fixed).parent.agent_version_id == a1.id
        assert _preflight(fixed).status == "passed"

        assert _observe(environment) == {str(environment.id): "created"}
        again = _push_rows(environment)[-1]
        assert again.parent.agent_version_id == fixed.id
        assert again.source["commit_sha"] == HEAD_Y
        assert _preflight(again.agent_version).status == "passed"
        assert _defaults(environment) == [again.agent_version_id]
        assert pinned_credential_values(environment)["OPENAI_API_KEY"] == "v3"

    def test_a_push_after_a_credential_change_passed_while_busy_keeps_its_values(
        self, user, workspace, storage, gh, scheduled
    ):
        environment = _environment(user, workspace, "push-rotation-busy", storage)
        rotated = change_credentials(
            environment, secret_values={"OPENAI_API_KEY": "v2"}
        )
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            state="running"
        )
        assert _preflight(rotated).status == "passed"
        assert _outcomes(rotated) == {str(environment.id): "environment_busy"}
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            state="completed"
        )
        gh.heads[("acme/agent", "main")] = HEAD_Y

        # The push comes before the deferred move of the change.
        assert _observe(environment) == {str(environment.id): "created"}

        [row] = _push_rows(environment)
        assert row.parent.agent_version_id == rotated.id
        assert _preflight(row.agent_version).status == "passed"
        assert _defaults(environment) == [row.agent_version_id]
        environment.refresh_from_db()
        assert pinned_credential_values(environment)["OPENAI_API_KEY"] == "v2"

    def test_a_pass_while_busy_moves_the_environment_once_it_is_built(
        self, user, workspace, storage, gh, scheduled
    ):
        environment = _environment(user, workspace, "push-busy", storage)
        a1 = _a1(environment)
        gh.heads[("acme/agent", "main")] = HEAD_Y
        _observe(environment)
        [row] = _push_rows(environment)
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            state="running"
        )
        assert _preflight(row.agent_version).status == "passed"
        assert _outcomes(row.agent_version) == {str(environment.id): "environment_busy"}
        assert _defaults(environment) == [a1.id]
        assert observe_repository_head(a1.agent_definition_id) == {}

        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            state="completed"
        )

        assert _observe(environment) == {str(environment.id): "reused"}
        assert _defaults(environment) == [row.agent_version_id]
        assert _hand_move(environment) is None
        assert len(_push_rows(environment)) == 1
        assert _observe(environment) == {str(environment.id): "unchanged"}

    def test_a_move_refused_while_rollback_is_off_is_asked_again(
        self, user, workspace, storage, gh, scheduled
    ):
        environment = _environment(user, workspace, "push-rollback", storage)
        a1 = _a1(environment)
        gh.heads[("acme/agent", "main")] = HEAD_Y
        _observe(environment)
        [row] = _push_rows(environment)
        refused = HostedHarnessError(
            "rollback_unavailable", "no bundle", status_code=409
        )
        with patch(
            "simulate.services.harness_versioning.versions.move_agent",
            side_effect=refused,
        ):
            assert _preflight(row.agent_version).status == "passed"
        assert _outcomes(row.agent_version) == {
            str(environment.id): "rollback_unavailable"
        }
        assert _defaults(environment) == [a1.id]

        assert _observe(environment) == {str(environment.id): "reused"}

        assert _defaults(environment) == [row.agent_version_id]
        assert len(_push_rows(environment)) == 1

    def test_a_refusal_that_never_clears_stops_being_asked(
        self, user, workspace, storage, gh, scheduled
    ):
        environment = _environment(user, workspace, "push-rollback-gone", storage)
        a1 = _a1(environment)
        gh.heads[("acme/agent", "main")] = HEAD_Y
        _observe(environment)
        [row] = _push_rows(environment)
        refused = HostedHarnessError(
            "rollback_unavailable", "archive gone", status_code=409
        )
        with patch(
            "simulate.services.harness_versioning.versions.move_agent",
            side_effect=refused,
        ):
            assert _preflight(row.agent_version).status == "passed"
            assert _observe(environment) == {str(environment.id): "reused"}
            assert _observe(environment) == {str(environment.id): "reused"}

            assert _observe(environment) == {str(environment.id): "unchanged"}

        [entry] = _side(row.agent_version).preflight_report["activation"]
        assert entry == {
            "environment_id": str(environment.id),
            "outcome": "rollback_unavailable",
            "attempts": 3,
        }
        assert _defaults(environment) == [a1.id]
        assert len(_push_rows(environment)) == 1

    def test_a_move_that_failed_on_reuse_is_asked_again(
        self, user, workspace, storage, gh, scheduled
    ):
        environment = _environment(user, workspace, "push-reuse-fail", storage)
        a1 = _a1(environment)
        gh.heads[("acme/agent", "main")] = HEAD_Y
        _observe(environment)
        [row] = _push_rows(environment)
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            state="running"
        )
        assert _preflight(row.agent_version).status == "passed"
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            state="completed"
        )
        with patch(
            "simulate.services.harness_versioning.versions.move_agent",
            side_effect=RuntimeError("storage blip"),
        ):
            assert _observe(environment) == {str(environment.id): "reused"}
        assert _outcomes(row.agent_version) == {str(environment.id): "failed"}
        assert _defaults(environment) == [a1.id]

        assert _observe(environment) == {str(environment.id): "reused"}

        assert _defaults(environment) == [row.agent_version_id]
        assert _hand_move(environment) is None
        assert len(_push_rows(environment)) == 1

    def test_a_branch_pushed_back_to_a_head_it_ran_ends_on_it(
        self, user, workspace, storage, gh, scheduled
    ):
        environment = _environment(user, workspace, "push-reset", storage)
        gh.heads[("acme/agent", "main")] = HEAD_Y
        assert _observe(environment) == {str(environment.id): "created"}
        on_y = _push_rows(environment)[0].agent_version
        assert _preflight(on_y).status == "passed"
        gh.heads[("acme/agent", "main")] = HEAD_Z
        assert _observe(environment) == {str(environment.id): "created"}
        on_z = _push_rows(environment)[1].agent_version
        assert _preflight(on_z).status == "passed"
        assert _defaults(environment) == [on_z.id]

        # Reset to Y, then Z pushed again.
        gh.heads[("acme/agent", "main")] = HEAD_Y
        assert _observe(environment) == {str(environment.id): "reused"}
        assert _defaults(environment) == [on_y.id]
        gh.heads[("acme/agent", "main")] = HEAD_Z

        assert _observe(environment) == {str(environment.id): "reused"}

        assert _defaults(environment) == [on_z.id]
        assert _hand_move(environment) is None
        assert len(_push_rows(environment)) == 2
        assert _observe(environment) == {str(environment.id): "unchanged"}

    def test_another_environments_discard_leaves_this_one_asking(
        self, user, workspace, storage, gh, scheduled
    ):
        first, second = _shared(user, workspace, storage, "push-discard-two")
        a1 = _a1(first)
        gh.heads[("acme/agent", "main")] = HEAD_Y
        assert _observe(first) == {str(first.id): "created", str(second.id): "reused"}
        [shared] = _push_versions(a1.agent_definition_id)
        # Changed before the push version passed, so its move is passed over.
        a3 = change_credentials(
            second, secret_values={"OPENAI_API_KEY": "v3"}, parent=a1
        )
        assert _preflight(a3).status == "passed"
        assert _preflight(shared.agent_version).status == "passed"
        assert _outcomes(shared.agent_version) == {
            str(first.id): "activated",
            str(second.id): "superseded",
        }
        first.refresh_from_db()
        move_default(
            first,
            "agent",
            a1.id,
            expected_default_id=shared.agent_version_id,
            user=user,
        )
        discard_agent_version(shared.agent_version, by_environment=first.id)

        assert _observe(first) == {
            str(first.id): "unchanged",
            str(second.id): "created",
        }

        fresh = _push_versions(a1.agent_definition_id)[-1]
        assert fresh.parent.agent_version_id == a3.id
        assert fresh.source["commit_sha"] == HEAD_Y
        assert _observe(first) == {
            str(first.id): "unchanged",
            str(second.id): "unchanged",
        }

    def test_a_discard_holds_through_a_credential_change(
        self, user, workspace, storage, gh, scheduled
    ):
        environment = _environment(user, workspace, "push-discard-cred", storage)
        gh.heads[("acme/agent", "main")] = HEAD_Y
        _observe(environment)
        [row] = _push_rows(environment)
        discard_agent_version(row.agent_version, by_environment=environment.id)
        a3 = change_credentials(environment, secret_values={"OPENAI_API_KEY": "v3"})
        assert _preflight(a3).status == "passed"
        assert _defaults(environment) == [a3.id]
        assert _hand_move(environment) is None

        assert _observe(environment) == {str(environment.id): "unchanged"}

        assert _defaults(environment) == [a3.id]
        assert len(_push_rows(environment)) == 1
        gh.heads[("acme/agent", "main")] = HEAD_Z
        assert _observe(environment) == {str(environment.id): "created"}

    def test_a_discard_holds_through_a_move_by_hand(
        self, user, workspace, storage, gh, scheduled
    ):
        environment = _environment(user, workspace, "push-discard-hand", storage)
        a1 = _a1(environment)
        gh.heads[("acme/agent", "main")] = HEAD_Y
        _observe(environment)
        [row] = _push_rows(environment)
        discard_agent_version(row.agent_version, by_environment=environment.id)
        a3 = change_credentials(environment, secret_values={"OPENAI_API_KEY": "v3"})
        assert _preflight(a3).status == "passed"
        environment.refresh_from_db()
        move_default(environment, "agent", a1.id, expected_default_id=a3.id, user=user)

        assert _observe(environment) == {str(environment.id): "unchanged"}

        assert _defaults(environment) == [a1.id]
        assert len(_push_rows(environment)) == 1

    def test_another_discard_leaves_a_refused_co_requester_asking(
        self, user, workspace, storage, gh, scheduled
    ):
        first, second = _shared(user, workspace, storage, "push-discard-wait")
        a1 = _a1(first)
        gh.heads[("acme/agent", "main")] = HEAD_Y
        assert _observe(first) == {str(first.id): "created", str(second.id): "reused"}
        [shared] = _push_versions(a1.agent_definition_id)
        refused = HostedHarnessError(
            "rollback_unavailable", "no bundle", status_code=409
        )
        with patch(
            "simulate.services.harness_versioning.versions.move_agent",
            side_effect=refused,
        ):
            assert _preflight(shared.agent_version).status == "passed"
        assert _outcomes(shared.agent_version) == {
            str(first.id): "rollback_unavailable",
            str(second.id): "rollback_unavailable",
        }
        discard_agent_version(shared.agent_version, by_environment=first.id)

        assert _observe(first) == {
            str(first.id): "unchanged",
            str(second.id): "created",
        }

        fresh = _push_versions(a1.agent_definition_id)[-1]
        assert fresh.id != shared.id
        assert fresh.parent.agent_version_id == a1.id
        assert fresh.agent_payload["requested_by_environments"] == [str(second.id)]
        assert _preflight(fresh.agent_version).status == "passed"
        assert _defaults(first, second) == [a1.id, fresh.agent_version_id]
        assert _observe(first) == {
            str(first.id): "unchanged",
            str(second.id): "unchanged",
        }

    def test_another_discard_leaves_a_settled_co_requester_unchanged(
        self, user, workspace, storage, gh, scheduled
    ):
        from simulate.services.hosted_harness import agent_version_pickable

        first, second = _shared(user, workspace, storage, "push-discard-settled")
        a1 = _a1(first)
        gh.heads[("acme/agent", "main")] = HEAD_Y
        assert _observe(first) == {str(first.id): "created", str(second.id): "reused"}
        [shared] = _push_versions(a1.agent_definition_id)
        with patch(
            "simulate.services.hosted_harness.agent_version_pickable",
            side_effect=lambda job, version_id: (
                job.id != second.id and agent_version_pickable(job, version_id)
            ),
        ):
            assert _preflight(shared.agent_version).status == "passed"
        assert _outcomes(shared.agent_version) == {
            str(first.id): "activated",
            str(second.id): "not_bound",
        }
        first.refresh_from_db()
        move_default(
            first,
            "agent",
            a1.id,
            expected_default_id=shared.agent_version_id,
            user=user,
        )
        discard_agent_version(shared.agent_version, by_environment=first.id)

        assert _observe(first) == {
            str(first.id): "unchanged",
            str(second.id): "unchanged",
        }
        assert _push_versions(a1.agent_definition_id) == [shared]
        assert _defaults(first, second) == [a1.id, a1.id]

    def test_another_discard_after_a_default_asked_again_leaves_it_asking(
        self, user, workspace, storage, gh, scheduled
    ):
        from django.utils import timezone

        from simulate.services.hosted_harness import agent_version_pickable

        first, second = _shared(user, workspace, storage, "push-discard-reasked")
        a1 = _a1(first)
        gh.heads[("acme/agent", "main")] = HEAD_Y
        assert _observe(first) == {str(first.id): "created", str(second.id): "reused"}
        [shared] = _push_versions(a1.agent_definition_id)
        with patch(
            "simulate.services.hosted_harness.agent_version_pickable",
            side_effect=lambda job, version_id: (
                job.id != second.id and agent_version_pickable(job, version_id)
            ),
        ):
            assert _preflight(shared.agent_version).status == "passed"
        assert _outcomes(shared.agent_version) == {
            str(first.id): "activated",
            str(second.id): "not_bound",
        }
        # The second one's default asked for again since (a branch pushed back).
        default = HarnessAgentVersionRuntime.no_workspace_objects.get(
            agent_version_id=a1.id
        )
        default.agent_payload = {
            **default.agent_payload,
            "requested_at": {str(second.id): timezone.now().isoformat()},
        }
        default.save(update_fields=["agent_payload"])
        first.refresh_from_db()
        move_default(
            first,
            "agent",
            a1.id,
            expected_default_id=shared.agent_version_id,
            user=user,
        )
        discard_agent_version(shared.agent_version, by_environment=first.id)

        assert _observe(first) == {
            str(first.id): "unchanged",
            str(second.id): "created",
        }


class TestBackgroundReuse:
    def test_a_reuse_found_in_the_background_is_no_move_by_hand(
        self, user, workspace, storage, gh, scheduled
    ):
        first, second = _shared(user, workspace, storage, "reuse-bg")
        HostedHarnessJob.no_workspace_objects.filter(id=second.id).update(
            state="running"
        )
        gh.heads[("acme/agent", "main")] = HEAD_Y
        assert _observe(first) == {str(first.id): "created"}
        [row] = _push_versions(_a1(first).agent_definition_id)
        assert _preflight(row.agent_version).status == "passed"
        HostedHarnessJob.no_workspace_objects.filter(id=second.id).update(
            state="completed"
        )

        assert _observe(first) == {
            str(first.id): "unchanged",
            str(second.id): "reused",
        }

        assert _defaults(first, second) == [row.agent_version_id] * 2
        assert _hand_move(second) is None
        assert _outcomes(row.agent_version)[str(second.id)] == "activated"

    def test_a_reuse_asked_for_by_a_person_is_still_a_move_by_hand(
        self, user, workspace, storage, scheduled
    ):
        first, second = _shared(user, workspace, storage, "reuse-person")
        a2 = _create(_a1(first), first, secret_values={"OPENAI_API_KEY": "v2"})
        assert _preflight(a2).status == "passed"

        assert (
            change_credentials(second, secret_values={"OPENAI_API_KEY": "v2"}).id
            == a2.id
        )

        assert _defaults(second) == [a2.id]
        assert _hand_move(second)["to"] == str(a2.id)

    def test_a_background_reuse_keeps_the_last_move_by_hand(
        self, user, workspace, storage, scheduled
    ):
        first, second = _shared(user, workspace, storage, "reuse-off")
        a1 = _a1(first)
        a2 = _create(a1, first, secret_values={"OPENAI_API_KEY": "v2"})
        assert _preflight(a2).status == "passed"
        a3 = change_credentials(second, secret_values={"OPENAI_API_KEY": "v3"})
        assert _preflight(a3).status == "passed"
        second.refresh_from_db()
        move_default(second, "agent", a1.id, expected_default_id=a3.id, user=user)
        moved = _hand_move(second)

        # The same facts as A2, found in the background after a move by hand.
        again = _create(
            a1, second, secret_values={"OPENAI_API_KEY": "v2"}, explicit_reuse=False
        )

        assert again.id == a2.id
        assert _defaults(second) == [a2.id]
        assert _hand_move(second) == moved


class TestLegacyAndRuns:
    def test_a_legacy_row_is_not_matched_by_its_stored_commit(
        self, user, workspace, storage, gh, scheduled
    ):
        definition = _definition(user, workspace)
        _agent_version(definition)
        first = _environment(
            user,
            workspace,
            "legacy-y",
            storage,
            definition=definition,
            edit=lambda p: p["source"].update(commit_sha=HEAD_Y),
        )
        second = _environment(
            user,
            workspace,
            "legacy-x",
            storage,
            definition=definition,
            edit=lambda p: p["source"].update(commit_sha=HEAD_X),
        )
        assert first.agent_version_id == second.agent_version_id
        assert _side(_a1(first)).source["commit_sha"] == HEAD_Y
        gh.heads[("acme/agent", "main")] = HEAD_Y

        assert _observe(first) == {
            str(first.id): "unchanged",
            str(second.id): "created",
        }

    def test_certify_off_makes_nothing_until_it_is_on(
        self, settings, user, workspace, storage, gh, scheduled
    ):
        environment = _environment(user, workspace, "push-certify", storage)
        gh.heads[("acme/agent", "main")] = HEAD_Y
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = False

        assert _observe(environment) == {str(environment.id): "skipped_not_runnable"}
        assert _push_rows(environment) == []

        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        assert _observe(environment) == {str(environment.id): "created"}

    def test_a_legacy_parents_set_is_the_environments_own(
        self, user, workspace, storage, gh, scheduled
    ):
        environment = _environment(user, workspace, "push-legacy", storage)
        refs = sorted(environment.payload["agent"]["secret_refs"])
        a1 = _a1(environment)
        gh.heads[("acme/agent", "main")] = HEAD_Y
        _observe(environment)
        [row] = _push_rows(environment)

        assert version_credential_names(row) == refs
        assert _preflight(row.agent_version).status == "passed"
        environment.refresh_from_db()
        values = pinned_credential_values(environment)
        for name, value in OLD.items():
            assert values[name] == value
        # The run reads the pushed commit; the snapshot keeps the version's prompt.
        version = row.agent_version
        assert version.configuration_snapshot["description"] == version.description
        assert version.description == a1.description
        payload = dict(environment.payload)
        apply_agent_version(payload, _side(version), environment_id=environment.id)
        assert payload["source"]["commit_sha"] == HEAD_Y
        assert payload["source"]["repository"] == "acme/agent"


def _seen(environment):
    environment.refresh_from_db()
    return environment.payload["metadata"].get("agent_source_seen")


class TestFirstObservation:
    @pytest.fixture
    def seen_before(self):
        yield

    def test_a_backfilled_environment_whose_repository_moved_is_seeded(
        self, user, workspace, storage, gh, scheduled
    ):
        environment = _environment(
            user,
            workspace,
            "seed-legacy",
            storage,
            edit=lambda p: p["source"].update(commit_sha=HEAD_X),
        )
        a1 = _a1(environment)
        before = AgentVersion._base_manager.count()
        gh.heads[("acme/agent", "main")] = HEAD_Y

        assert _observe(environment) == {str(environment.id): "seeded"}

        assert AgentVersion._base_manager.count() == before
        assert _defaults(environment) == [a1.id]
        assert _seen(environment)["commit_sha"] == HEAD_Y
        scheduled.assert_not_called()
        assert _observe(environment) == {str(environment.id): "unchanged"}
        gh.heads[("acme/agent", "main")] = HEAD_Z
        assert _observe(environment) == {str(environment.id): "created"}
        assert _seen(environment)["commit_sha"] == HEAD_Y

    def test_a_backfilled_environment_first_seen_unmoved_takes_the_next_push(
        self, user, workspace, storage, gh, scheduled
    ):
        environment = _environment(
            user,
            workspace,
            "seed-quiet",
            storage,
            edit=lambda p: p["source"].update(commit_sha=HEAD_X),
        )
        gh.heads[("acme/agent", "main")] = HEAD_X

        assert _observe(environment) == {str(environment.id): "unchanged"}
        assert _seen(environment)["commit_sha"] == HEAD_X

        gh.heads[("acme/agent", "main")] = HEAD_Y
        assert _observe(environment) == {str(environment.id): "created"}
        assert len(_push_rows(environment)) == 1
        assert _seen(environment)["commit_sha"] == HEAD_X

    def test_a_branch_reset_to_the_seen_head_after_a_push_ends_on_it(
        self, user, workspace, storage, gh, scheduled
    ):
        environment = _environment(
            user,
            workspace,
            "seed-reset",
            storage,
            edit=lambda p: p["source"].update(commit_sha=HEAD_X),
        )
        gh.heads[("acme/agent", "main")] = HEAD_X
        assert _observe(environment) == {str(environment.id): "unchanged"}
        gh.heads[("acme/agent", "main")] = HEAD_Y
        assert _observe(environment) == {str(environment.id): "created"}
        on_y = _push_rows(environment)[0].agent_version
        assert _preflight(on_y).status == "passed"
        assert _defaults(environment) == [on_y.id]

        gh.heads[("acme/agent", "main")] = HEAD_X

        assert _observe(environment) == {str(environment.id): "created"}
        rows = _push_rows(environment)
        assert len(rows) == 2
        assert rows[1].source["commit_sha"] == HEAD_X

    def test_a_first_sight_seen_meanwhile_is_kept(
        self, user, workspace, storage, gh, scheduled
    ):
        environment = _environment(
            user,
            workspace,
            "seed-race",
            storage,
            edit=lambda p: p["source"].update(commit_sha=HEAD_X),
        )
        [watched] = watched_sources(repository="acme/agent")
        stale = HostedHarnessJob.no_workspace_objects.get(id=environment.id)
        assert changes._seed(environment, watched, HEAD_Y)

        assert changes._seed(stale, watched, HEAD_Z)

        assert _seen(environment)["commit_sha"] == HEAD_Y
        assert _push_rows(environment) == []

    def test_a_credential_version_on_a_backfilled_row_is_seeded_too(
        self, user, workspace, storage, gh, scheduled
    ):
        environment = _environment(user, workspace, "seed-a2", storage)
        a2 = change_credentials(environment, secret_values={"OPENAI_API_KEY": "v2"})
        assert _preflight(a2).status == "passed"
        assert _defaults(environment) == [a2.id]
        gh.heads[("acme/agent", "main")] = HEAD_Y

        assert _observe(environment) == {str(environment.id): "seeded"}
        assert _push_rows(environment) == []

    @pytest.mark.parametrize("stamped", [True, False])
    def test_a_new_environments_build_is_its_starting_point_and_never_seeded(
        self, settings, user, workspace, gh, gates, scheduled, stamped
    ):
        settings.HARNESS_PROVIDER = "hosted"
        api = APIClient()
        api.force_authenticate(user=user)
        environment = _job(_post(api, workspace, _body(values=VALUES)))
        a1 = environment.agent_version
        assert "commit_sha" not in _side(a1).source
        payload = copy.deepcopy(environment.payload)
        payload["source"]["commit_sha"] = HEAD_X
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            payload=payload, state=HostedHarnessJob.State.COMPLETED
        )
        environment.refresh_from_db()
        if stamped:
            stamp_built_commit(environment, HEAD_X)
            assert _side(a1).source["commit_sha"] == HEAD_X
        # Unstamped (as a shared version stays), the build's commit is still the
        # environment's own.
        gh.heads[("acme/agent", "main")] = HEAD_X

        assert _observe(environment) == {str(environment.id): "unchanged"}
        assert _push_rows(environment) == []

        gh.heads[("acme/agent", "main")] = HEAD_Y
        assert _observe(environment) == {str(environment.id): "created"}
        assert _seen(environment) is None
        [row] = _push_rows(environment)
        assert row.parent.agent_version_id == a1.id
        assert _preflight(row.agent_version).status == "passed"
        assert _defaults(environment) == [row.agent_version_id]

        # Reset to the build's commit: back on the version stamped with it, else on
        # a version of its own naming it.
        gh.heads[("acme/agent", "main")] = HEAD_X
        if stamped:
            assert _observe(environment) == {str(environment.id): "reused"}
            assert _defaults(environment) == [a1.id]
            assert len(_push_rows(environment)) == 1
        else:
            assert _observe(environment) == {str(environment.id): "created"}
            assert _push_rows(environment)[1].source["commit_sha"] == HEAD_X

    def test_the_builds_launch_pins_its_version_to_the_fetched_commit(
        self, settings, user, workspace, gates, scheduled, gateway, monkeypatch
    ):
        settings.HARNESS_PROVIDER = "hosted"
        api = APIClient()
        api.force_authenticate(user=user)
        environment = _job(_post(api, workspace, _body(values=VALUES)))
        a1 = environment.agent_version
        assert "commit_sha" not in _side(a1).source
        monkeypatch.setattr(
            "simulate.services.hosted_harness_gateway.HostedSourceAcquirer.acquire",
            lambda _self, _job: (_empty_source_archive(), HEAD_X),
        )

        gateway.launch(environment, endpoint_base_url="https://platform.example.com")

        environment.refresh_from_db()
        assert environment.payload["source"]["commit_sha"] == HEAD_X
        assert _side(a1).source["commit_sha"] == HEAD_X

    def test_a_reset_to_the_build_commit_with_other_credentials_ends_on_them(
        self, settings, user, workspace, gh, gates, scheduled
    ):
        settings.HARNESS_PROVIDER = "hosted"
        api = APIClient()
        api.force_authenticate(user=user)
        environment = _job(_post(api, workspace, _body(values=VALUES)))
        payload = copy.deepcopy(environment.payload)
        payload["source"]["commit_sha"] = HEAD_X
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            payload=payload, state=HostedHarnessJob.State.COMPLETED
        )
        environment.refresh_from_db()
        stamp_built_commit(environment, HEAD_X)
        a2 = change_credentials(environment, secret_values={"OPENAI_API_KEY": "v2"})
        assert _preflight(a2).status == "passed"
        gh.heads[("acme/agent", "main")] = HEAD_Y
        assert _observe(environment) == {str(environment.id): "created"}
        assert _preflight(_push_rows(environment)[0].agent_version).status == "passed"

        gh.heads[("acme/agent", "main")] = HEAD_X

        assert _observe(environment) == {str(environment.id): "reused"}
        assert _defaults(environment) == [a2.id]
        assert len(_push_rows(environment)) == 1


class TestCreationCheck:
    @pytest.mark.parametrize(
        ("response", "code"),
        [
            (GitHubResponse("not_found"), "github_repository_not_found"),
            (GitHubResponse("ok", {"private": True}), "github_private_unsupported"),
        ],
    )
    def test_private_or_unknown_repositories_are_refused(self, response, code):
        with patch.object(changes, "github_get", return_value=response) as get:
            with pytest.raises(HostedHarnessError) as refused:
                validate_public_github_source(
                    {"source": {"kind": "github", "repository": "acme/agent"}}
                )
        assert refused.value.code == code
        assert refused.value.status_code == 422
        assert get.call_args.args == ("/repos/acme/agent",)

    def test_a_private_source_is_refused_without_asking_github(self):
        with patch.object(changes, "github_get") as get:
            with pytest.raises(HostedHarnessError) as refused:
                validate_public_github_source(
                    {
                        "source": {
                            "kind": "github",
                            "repository": "acme/agent",
                            "visibility": "private",
                        }
                    }
                )
        assert refused.value.code == "github_private_unsupported"
        get.assert_not_called()

    @pytest.mark.parametrize("status", ["rate_limited", "budget", "error"])
    def test_github_out_of_reach_never_blocks_a_creation(self, status, caplog):
        with patch.object(changes, "github_get", return_value=GitHubResponse(status)):
            validate_public_github_source(
                {"source": {"kind": "github", "repository": "acme/agent"}}
            )
        assert f"harness_github_check_skipped reason={status}" in caplog.text

    def test_other_sources_are_not_checked(self):
        with patch.object(changes, "github_get") as get:
            validate_public_github_source({"source": {"kind": "archive"}})
        get.assert_not_called()

    @pytest.mark.parametrize("versioning", [True, False])
    def test_creation_refuses_an_unknown_repository_with_versioning_on(
        self, settings, versioning, user, workspace, gates
    ):
        settings.HARNESS_PROVIDER = "hosted"
        settings.HARNESS_VERSIONING_ENABLED = versioning
        api = APIClient()
        api.force_authenticate(user=user)
        before = HostedHarnessJob.no_workspace_objects.count()
        with (
            patch.object(
                changes,
                "validate_public_github_source",
                validate_public_github_source,
            ),
            patch.object(
                changes, "github_get", return_value=GitHubResponse("not_found")
            ) as get,
        ):
            response = _post(api, workspace, _body(values=VALUES))

        if versioning:
            assert response.status_code == 422, response.content
            assert response.json()["error"] == "github_repository_not_found"
            assert HostedHarnessJob.no_workspace_objects.count() == before
        else:
            assert response.status_code == 202, response.content
            get.assert_not_called()

    def test_a_private_source_is_refused_before_it_is_cloned(
        self, settings, user, workspace, gates
    ):
        settings.HARNESS_PROVIDER = "hosted"
        api = APIClient()
        api.force_authenticate(user=user)
        body = _body(values=VALUES)
        body["source"].update(visibility="private", installation_id="1")
        with (
            patch.object(
                changes, "validate_public_github_source", validate_public_github_source
            ),
            patch.object(
                harness_provider,
                "_preflight_source_connectors",
                _preflight_source_connectors,
            ),
            patch(
                "simulate.services.hosted_harness_gateway.HostedSourceAcquirer.acquire",
                return_value=(b"", HEAD_X),
            ) as acquire,
            patch.object(changes, "github_get") as get,
        ):
            response = _post(api, workspace, body)

        assert response.status_code == 422, response.content
        assert response.json()["error"] == "github_private_unsupported"
        acquire.assert_not_called()
        get.assert_not_called()

    def test_a_replay_answers_with_its_job_whatever_github_says_now(
        self, settings, user, workspace, gates
    ):
        settings.HARNESS_PROVIDER = "hosted"
        api = APIClient()
        api.force_authenticate(user=user)
        body, key = _body(values=VALUES), uuid.uuid4().hex
        with patch.object(
            changes, "validate_public_github_source", validate_public_github_source
        ):
            with patch.object(
                changes, "github_get", return_value=GitHubResponse("ok", {})
            ):
                first = _job(_post(api, workspace, body, key=key))
            with patch.object(
                changes, "github_get", return_value=GitHubResponse("not_found")
            ) as get:
                replay = _job(_post(api, workspace, body, key=key))

        assert replay.id == first.id
        get.assert_not_called()

    def test_a_private_job_made_with_versioning_off_replays(
        self, settings, user, workspace, gates
    ):
        settings.HARNESS_PROVIDER = "hosted"
        api = APIClient()
        api.force_authenticate(user=user)
        body, key = _body(values=VALUES), uuid.uuid4().hex
        body["source"].update(visibility="private", installation_id="1")
        with patch.object(
            changes, "validate_public_github_source", validate_public_github_source
        ):
            settings.HARNESS_VERSIONING_ENABLED = False
            first = _job(_post(api, workspace, body, key=key))
            settings.HARNESS_VERSIONING_ENABLED = True
            replay = _job(_post(api, workspace, body, key=key))

        assert replay.id == first.id

    @pytest.mark.parametrize(
        "marker", ["agent_source_seen", "agent_source_repository_id"]
    )
    def test_the_seen_markers_are_reserved(self, marker):
        from simulate.serializers.harness_job import HarnessJobCreateSerializer

        body = _body(metadata={marker: {"commit_sha": HEAD_X}})
        serializer = HarnessJobCreateSerializer(data=body)
        assert not serializer.is_valid()
        assert "metadata" in serializer.errors


class TestFlagsAndDeletion:
    def test_flag_off_reads_nothing(
        self, settings, django_assert_num_queries, user, workspace, storage
    ):
        _environment(user, workspace, "flag-off", storage)
        settings.HARNESS_VERSIONING_ENABLED = False
        with patch.object(github.requests, "get") as get:
            with django_assert_num_queries(0):
                assert poll_repository_heads() == {}
                assert receive_push("acme/agent", "refs/heads/main", "main") == {}
                assert observe_repository_head(uuid.uuid4()) == {}
        get.assert_not_called()

    def test_a_push_to_a_deleted_environments_repository_makes_nothing(
        self, user, workspace, storage, gh, scheduled
    ):
        environment = _environment(user, workspace, "push-deleted", storage)
        before = AgentVersion._base_manager.count()
        gh.heads[("acme/agent", "main")] = HEAD_Y
        _soft_delete(environment)

        assert receive_push("acme/agent", "refs/heads/main", "main") == {}

        assert AgentVersion._base_manager.count() == before
        assert gh.calls == []


def _source(ref, repository="acme/agent", n=0):
    return WatchedSource(
        environment_id=uuid.UUID(int=n + 1),
        definition_id=uuid.UUID(int=1000 + n),
        parent_version_id=uuid.UUID(int=2000 + n),
        repository=repository,
        ref=ref,
        commit_sha=None,
    )


class TestRefsAndBudget:
    @pytest.mark.parametrize(
        ("pushed", "default", "matched"),
        [
            ("refs/heads/main", "main", ["main", None]),
            ("refs/heads/main", "trunk", ["main"]),
            ("refs/heads/dev", "main", ["dev"]),
            ("refs/tags/v1", "main", ["v1"]),
            ("refs/heads/a/../main", "main", []),
            ("refs/heads/-x", "main", []),
        ],
    )
    def test_a_push_matches_its_ref(self, pushed, default, matched):
        sources = [
            _source(ref, n=n) for n, ref in enumerate(["main", None, "dev", "v1"])
        ]
        with (
            patch.object(changes, "watched_sources", return_value=sources),
            patch.object(
                changes, "github_head", return_value=HeadResult("ok", HEAD_Y)
            ) as head,
            patch.object(changes, "observe_repository_head", return_value={}),
        ):
            receive_push("acme/agent", pushed, default)
        assert [call.args[1] for call in head.call_args_list] == matched

    def test_a_push_reads_the_head_again_without_its_etag(self, gh):
        gh.heads[("acme/agent", "main")] = HEAD_Y
        github_head("acme/agent", "main", purpose="poll")
        with (
            patch.object(changes, "watched_sources", return_value=[_source("main")]),
            patch.object(changes, "observe_repository_head", return_value={}),
        ):
            receive_push("acme/agent", "refs/heads/main", "main")
        assert [call[2] for call in gh.calls] == [None, None]

    def test_pushes_to_one_pair_close_together_read_its_head_once(self, gh):
        gh.heads[("acme/agent", "main")] = HEAD_Y
        with (
            patch.object(changes, "watched_sources", return_value=[_source("main")]),
            patch.object(changes, "observe_repository_head", return_value={}) as seen,
        ):
            for _ in range(3):
                receive_push("acme/agent", "refs/heads/main", "main")
        assert len(gh.calls) == 1
        assert seen.call_args.kwargs["heads"] == {}

    def test_the_poller_goes_round_the_oldest_checked_first(self):
        sources = [_source("main", f"acme/r{n:02d}", n) for n in range(12)]
        read = []

        def head(repository, ref, **kwargs):
            read.append(repository)
            assert kwargs["reserve"] == 20
            return HeadResult("ok", HEAD_Y)

        with (
            patch.object(changes, "watched_sources", return_value=sources),
            patch.object(changes, "github_head", side_effect=head),
            patch.object(changes, "observe_repository_head", return_value={}) as seen,
        ):
            poll_repository_heads()
            first = list(read)
            read.clear()
            poll_repository_heads()

        assert len(first) == 5
        assert len(read) == 5
        assert not set(first) & set(read)
        assert seen.call_args.kwargs["purpose"] == "poll"

    @pytest.mark.parametrize("skipped_by", ["cooldown", "budget"])
    def test_a_skipped_push_is_polled_first(self, skipped_by):
        sources = [_source("main", f"acme/r{n:02d}", n) for n in range(12)]
        for n, one in enumerate(sources):
            cache.set(changes._checked_key(one.repository, "main"), 100 + n, 60)
        read = []

        def head(repository, ref, **kwargs):
            read.append(repository)
            if kwargs["purpose"] == "webhook" and skipped_by == "budget":
                return HeadResult("budget")
            return HeadResult("ok", HEAD_Y)

        def watched(repository=None, **kwargs):
            return [one for one in sources if repository in (None, one.repository)]

        with (
            patch.object(changes, "watched_sources", side_effect=watched),
            patch.object(changes, "github_head", side_effect=head),
            patch.object(changes, "observe_repository_head", return_value={}),
        ):
            for _ in range(2 if skipped_by == "cooldown" else 1):
                receive_push("acme/r11", "refs/heads/main", "main")
            read.clear()
            poll_repository_heads()

        assert read[0] == "acme/r11"

    def test_one_owners_pushes_cannot_hold_every_poll_slot(self):
        busy = [_source("main", f"busy/r{n}", n) for n in range(5)]
        others = [_source("main", f"acme/r{n}", 10 + n) for n in range(5)]
        for n, one in enumerate(others + busy):
            cache.set(changes._checked_key(one.repository, "main"), 100 + n, 60)
        read = []

        def head(repository, ref, **kwargs):
            if kwargs["purpose"] == "webhook":
                return HeadResult("budget")
            read.append(repository)
            return HeadResult("ok", HEAD_Y)

        def watched(repository=None, **kwargs):
            return [
                one for one in busy + others if repository in (None, one.repository)
            ]

        with (
            patch.object(changes, "watched_sources", side_effect=watched),
            patch.object(changes, "github_head", side_effect=head),
            patch.object(changes, "observe_repository_head", return_value={}),
        ):
            for _ in range(2):
                for one in busy:
                    receive_push(one.repository, "refs/heads/main", "main")
                poll_repository_heads()

        assert sum(one.startswith("busy/") for one in read) == 5
        assert {f"acme/r{n}" for n in range(5)} <= set(read)

    def test_a_push_to_a_pair_many_environments_watch_counts_once(self):
        sources = [_source("main", "acme/agent", n) for n in range(3)]
        with (
            patch.object(changes, "watched_sources", return_value=sources),
            patch.object(changes, "github_head", return_value=HeadResult("ok", HEAD_Y)),
            patch.object(changes, "observe_repository_head", return_value={}),
        ):
            for _ in range(2):
                receive_push("acme/agent", "refs/heads/main", "main")

        assert cache.get(f"{github._hour_key()}:poll-first:acme") == 1
        assert cache.get(changes._checked_key("acme/agent", "main")) == -1

    def test_many_owners_cannot_poll_first_past_the_hourly_cap(self, settings):
        settings.HARNESS_GITHUB_WEBHOOK_SHARE = 1
        settings.HARNESS_GITHUB_WEBHOOK_BUDGET = 2
        pairs = [("busy/r0", "main"), ("busy/r1", "main")] + [
            (f"owner{n}/agent", "main") for n in range(4)
        ]
        for pair in pairs:
            changes._poll_first(pair)

        first = [p for p in pairs if cache.get(changes._checked_key(*p)) == -1]
        assert first == [("busy/r0", "main"), ("owner0/agent", "main")]

    def test_a_rate_limit_stops_the_tick_and_the_next_ones(self):
        sources = [_source("main", f"acme/r{n:02d}", n) for n in range(4)]
        results = iter([HeadResult("ok", HEAD_Y), HeadResult("rate_limited")])
        with (
            patch.object(changes, "watched_sources", return_value=sources),
            patch.object(
                changes, "github_head", side_effect=lambda *a, **k: next(results)
            ) as head,
            patch.object(changes, "observe_repository_head", return_value={}),
        ):
            assert poll_repository_heads() == {"rate_limited": 1}
            assert head.call_count == 2
            cache.set(github.RATE_RESET_KEY, 10**12, 60)
            assert poll_repository_heads() == {}
            assert head.call_count == 2

    def test_low_remaining_calls_shrink_the_tick(self):
        sources = [_source("main", f"acme/r{n:02d}", n) for n in range(6)]
        cache.set(github.REMAINING_KEY, 22, 60)
        with (
            patch.object(changes, "watched_sources", return_value=sources),
            patch.object(
                changes, "github_head", return_value=HeadResult("ok", HEAD_Y)
            ) as head,
            patch.object(changes, "observe_repository_head", return_value={}),
        ):
            poll_repository_heads()
        assert head.call_count == 2


class TestRepositoryId:
    """A watched source is followed by GitHub's id for its repository, never by a
    name a later owner could take."""

    @pytest.fixture
    def identified(self):
        yield

    def test_a_creation_keeps_the_id_and_no_response_shows_it(
        self, settings, user, workspace, gates
    ):
        settings.HARNESS_PROVIDER = "hosted"
        api = APIClient()
        api.force_authenticate(user=user)
        with (
            patch.object(
                changes, "validate_public_github_source", validate_public_github_source
            ),
            patch.object(
                changes,
                "github_get",
                return_value=GitHubResponse("ok", {"id": 918273645}),
            ) as get,
        ):
            response = _post(api, workspace, _body(values=VALUES))
        job = _job(response)

        assert get.call_args.kwargs["purpose"] == "create"
        assert get.call_args.kwargs["owner"] == str(job.organization_id)
        access = _side(_a1(job)).agent_payload["source_access"]
        assert access["repository_id"] == 918273645
        listed = api.get(
            f"/simulate/api/harness-environments/{job.id}/versions/",
            HTTP_X_WORKSPACE_ID=str(workspace.id),
        )
        assert listed.status_code == 200, listed.content
        for body in (response.content, listed.content):
            assert b"918273645" not in body

    def test_an_unidentified_source_is_identified_first_then_read_by_id(
        self, user, workspace, storage, gh, scheduled
    ):
        environment = _environment(user, workspace, "rid-legacy", storage)
        a1 = _a1(environment)
        before = AgentVersion._base_manager.count()
        gh.ids["acme/agent"] = 77
        gh.heads[("acme/agent", "main")] = HEAD_Y

        assert _observe(environment) == {str(environment.id): "skipped_unidentified"}
        assert AgentVersion._base_manager.count() == before
        metadata = environment.payload.get("metadata") or {}
        environment.refresh_from_db()
        assert environment.payload["metadata"][changes._REPOSITORY_ID_KEY] == {
            "repository": "acme/agent",
            "id": 77,
        }
        assert changes._REPOSITORY_ID_KEY not in metadata

        # The name now leads elsewhere; the id still reads the watched repository.
        gh.heads[("acme/agent", "main")] = HEAD_Z
        gh.heads[("id:77", "main")] = HEAD_Y
        assert _observe(environment) == {str(environment.id): "created"}
        [row] = _push_rows(environment)
        assert row.source["commit_sha"] == HEAD_Y
        assert row.agent_payload["source_access"]["repository_id"] == 77
        assert gh.calls[-1][:2] == ("id:77", "main")
        assert _defaults(environment) == [a1.id]

    def test_a_push_from_another_repository_by_the_same_name_is_ignored(
        self, user, workspace, storage, gh
    ):
        environment = _environment(
            user,
            workspace,
            "rid-push",
            storage,
            edit=lambda p: p.setdefault("metadata", {}).update(
                {changes._REPOSITORY_ID_KEY: {"repository": "acme/agent", "id": 77}}
            ),
        )
        gh.heads[("id:77", "main")] = HEAD_Y

        assert receive_push("acme/agent", "refs/heads/main", "main", 999) == {}
        assert gh.calls == []
        assert receive_push("acme/agent", "refs/heads/main", "main", 77) == {
            "created": 1
        }
        assert _push_rows(environment)


def test_organizations_take_turns_at_the_poll():
    busy = [
        dataclasses.replace(
            _source("main", f"busy/r{n}", n), organization_id=uuid.UUID(int=1)
        )
        for n in range(8)
    ]
    other = dataclasses.replace(
        _source("main", "acme/agent", 20), organization_id=uuid.UUID(int=2)
    )
    for n, one in enumerate(busy):
        cache.set(changes._checked_key(one.repository, "main"), 100 + n, 60)
    cache.set(changes._checked_key(other.repository, "main"), 500, 60)
    read = []

    def head(repository, ref, **kwargs):
        read.append(repository)
        return HeadResult("ok", HEAD_Y)

    with (
        patch.object(changes, "watched_sources", return_value=[*busy, other]),
        patch.object(changes, "github_head", side_effect=head),
        patch.object(changes, "observe_repository_head", return_value={}),
    ):
        poll_repository_heads(budget=2)

    assert read == ["busy/r0", "acme/agent"]
