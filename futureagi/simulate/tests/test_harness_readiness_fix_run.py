"""Run readiness Fix runs: starting and adding to one, its refusals, the run read and
its progress on the readiness rows."""

import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from simulate.models import (
    HarnessNudge,
    HostedHarnessConversation,
    HostedHarnessJob,
    ReadinessFixRun,
)
from simulate.models.readiness_fix import OPEN_LINE
from simulate.serializers.harness_environment import (
    HarnessCapabilitySelectionReportSerializer,
    HarnessReadinessFixResponseSerializer,
)
from simulate.services import harness_readiness
from simulate.services.harness_readiness import fix_groups, fix_runs
from simulate.services.hosted_harness import HostedHarnessError
from simulate.tests.test_harness_readiness import (
    BASE,
    FAILED_PROBE,
    PASSED,
    _certify,
    _copy_version,
    _environment,
    _get,
    _pairing,
    _rows,
)
from simulate.tests.test_harness_readiness import (  # noqa: F401 - fixture
    scheduled as scheduled,
)
from simulate.tests.test_harness_readiness import (  # noqa: F401 - fixture
    storage as storage,
)
from simulate.tests.test_harness_readiness_fix import STATE_MESSAGE

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("readiness_on")]

ROUTING_MESSAGE = (
    "issue_refund failed its probe in the test world: fix issue_refund in the test"
    " world so its probe passes."
)
# The keys of a run the Ready to run panel, the chat and the Run dialog read.
FE_FIELDS = {"id", "state", "updated_at", "rows", "queue", "saved", "draft"}
FE_FIELDS |= {"finished_at", "error"}


@pytest.fixture(autouse=True)
def fix_on(settings, monkeypatch):
    settings.HARNESS_READINESS_FIX_ENABLED = True
    # Production never gives a failed probe to the builder: this only gives the run
    # mechanics a second builder row, independent of ``state``.
    monkeypatch.setitem(fix_groups._BUILDER, "action_failed", ROUTING_MESSAGE)
    with patch(
        "simulate.services.harness_versioning.rebuild.sandbox_provider_configured",
        return_value=True,
    ):
        yield


def _broken(user, workspace, storage, key):
    """An environment whose ``state``, ``reset`` (gated on ``state``) and
    ``tool_routing`` (by ``fix_on``) rows are the builder's to fix."""
    environment = _environment(
        user, workspace, storage, f"{key}-{uuid.uuid4().hex[:6]}"
    )
    _certify(
        environment,
        storage,
        checks={**PASSED, "schema_and_seed": "failed", "action_behavior": "1/2"},
        actions=FAILED_PROBE,
    )
    return environment


def _post(auth_client, environment_id, *, key=None, **body):
    return auth_client.post(
        f"{BASE}/{environment_id}/capabilities/fix/",
        {"group": "builder", "idempotency_key": key or uuid.uuid4().hex, **body},
        format="json",
    )


def _fix_get(auth_client, environment_id):
    return auth_client.get(f"{BASE}/{environment_id}/capabilities/fix/")


def _refused(response, code, status=409):
    assert response.status_code == status, response.content
    assert response.json()["error"] == code


def _run(environment):
    return ReadinessFixRun.no_workspace_objects.get(job=environment)


def _set_state(environment, state):
    ReadinessFixRun.no_workspace_objects.filter(job=environment).update(state=state)


def _could_not_fix(environment, row):
    """The row's open line could not be fixed."""
    run = _run(environment)
    open_lines = [
        one for one in run.lines if one["row_id"] == row and one["state"] in OPEN_LINE
    ]
    open_lines[-1]["state"] = "could_not_fix"
    run.save(update_fields=["lines"])


def _chat(environment, **fields):
    """The environment's one conversation, with ``fields``."""
    HostedHarnessConversation.no_workspace_objects.update_or_create(
        job=environment,
        defaults={
            "organization_id": environment.organization_id,
            "workspace_id": environment.workspace_id,
            **fields,
        },
    )


def _draft(version, **fields):
    return _copy_version(version, status="draft", version_number=None, **fields)


def _closed_run(environment, state, *, lines, **fields):
    return ReadinessFixRun.no_workspace_objects.create(
        job=environment,
        organization_id=environment.organization_id,
        workspace_id=environment.workspace_id,
        state=state,
        base_environment_version_id=environment.environment_version_id,
        base_suite_version_id=environment.suite_version_id,
        lines=[
            {
                "row_id": row,
                "area": "evidence",
                "label": row,
                "state": line_state,
                "reason": None,
                "waiting_on": None,
                "command": 1,
            }
            for row, line_state in lines.items()
        ],
        finished_at=timezone.now(),
        **fields,
    )


class TestRead:
    def test_no_run_reads_null(self, user, workspace, storage, auth_client):
        environment = _broken(user, workspace, storage, "fr-null")

        response = _fix_get(auth_client, environment.id)

        assert (response.status_code, response.json()) == (200, {"fix": None})

    def test_the_newest_run(self, user, workspace, storage, auth_client):
        environment = _broken(user, workspace, storage, "fr-newest")
        _closed_run(environment, "failed", lines={"state": "could_not_fix"})
        newer = _closed_run(environment, "done", lines={"state": "fixed"})

        body = _fix_get(auth_client, environment.id).json()

        assert body["fix"]["id"] == str(newer.id)

    def test_shape_the_panel_reads(self, user, workspace, storage, auth_client):
        environment = _broken(user, workspace, storage, "fr-shape")
        run = _post(auth_client, environment.id, rows=["state"]).json()["fix"]

        body = _fix_get(auth_client, environment.id).json()

        assert body["fix"] == run
        assert FE_FIELDS <= set(run)
        assert set(run) == FE_FIELDS | {
            "group",
            "started_at",
            "lines",
            "pairing",
            "active",
            "accepts_rows",
            "outcome",
        }
        assert (run["active"], run["accepts_rows"], run["outcome"]) == (
            True,
            True,
            None,
        )
        assert run["updated_at"] == _run(environment).state_changed_at.isoformat()
        assert run["pairing"]["environment_version"]["id"] == str(
            environment.environment_version_id
        )
        serializer = HarnessReadinessFixResponseSerializer(data=body)
        assert serializer.is_valid(), serializer.errors

    def test_switched_off_reads_null(
        self, user, workspace, storage, auth_client, settings
    ):
        environment = _broken(user, workspace, storage, "fr-get-off")
        _post(auth_client, environment.id)
        settings.HARNESS_READINESS_FIX_ENABLED = False

        assert _fix_get(auth_client, environment.id).json() == {"fix": None}


class TestStart:
    def test_one_command_for_the_rows_and_a_line_per_row(
        self, user, workspace, storage, auth_client
    ):
        environment = _broken(user, workspace, storage, "fr-start")

        response = _post(auth_client, environment.id, rows=["tool_routing", "state"])

        assert response.status_code == 202, response.content
        fix = response.json()["fix"]
        assert (fix["state"], fix["rows"]) == ("queued", ["tool_routing", "state"])
        assert fix["queue"] == [
            {"message": "Tool calls stay in the test, Starting records"}
        ]
        assert (fix["saved"], fix["draft"], fix["error"]) == (None, False, None)
        run = _run(environment)
        assert len(run.commands) == 1
        assert run.commands[0]["rows"] == ["tool_routing", "state"]
        assert run.commands[0]["message"] == f"{ROUTING_MESSAGE}\n{STATE_MESSAGE}"
        assert run.commands[0]["state"] == "queued"
        assert [(one["row_id"], one["state"], one["command"]) for one in run.lines] == [
            ("tool_routing", "queued", 1),
            ("state", "queued", 1),
        ]
        assert run.started_by == user
        assert (run.base_environment_version_id, run.base_suite_version_id) == (
            environment.environment_version_id,
            environment.suite_version_id,
        )

    def test_no_rows_fixes_every_builder_row_and_sends_each_message_once(
        self, user, workspace, storage, auth_client
    ):
        environment = _broken(user, workspace, storage, "fr-all")

        fix = _post(auth_client, environment.id).json()["fix"]

        # ``reset`` waits on ``state``: the same message, sent once.
        assert fix["rows"] == ["tool_routing", "state", "reset"]
        assert fix["queue"] == [
            {
                "message": "Tool calls stay in the test, Starting records, Starts fresh"
                " for each case"
            }
        ]
        assert [one["state"] for one in fix["lines"]] == ["queued"] * 3

    def test_a_repeated_key_returns_the_run_unchanged(
        self, user, workspace, storage, auth_client
    ):
        environment = _broken(user, workspace, storage, "fr-replay")
        first = _post(auth_client, environment.id, key="k1", rows=["state"]).json()

        again = _post(auth_client, environment.id, key="k1", rows=["state"])

        assert again.status_code == 202
        assert again.json() == first
        assert len(_run(environment).commands) == 1

    def test_a_key_reused_for_another_request_is_refused(
        self, user, workspace, storage, auth_client
    ):
        environment = _broken(user, workspace, storage, "fr-reused")
        _post(auth_client, environment.id, key="k1", rows=["state"])
        before = _run(environment)

        other_rows = _post(auth_client, environment.id, key="k1", rows=["tool_routing"])
        no_rows = _post(auth_client, environment.id, key="k1")
        other_picks = _post(
            auth_client,
            environment.id,
            key="k1",
            rows=["state"],
            environment_version_id=environment.environment_version_id,
        )

        for response in (other_rows, no_rows, other_picks):
            _refused(response, "idempotency_key_reused")
        after = _run(environment)
        assert len(after.commands) == 1
        assert after.lines == before.lines

    def test_rows_in_another_order_replay(self, user, workspace, storage, auth_client):
        environment = _broken(user, workspace, storage, "fr-reorder")
        first = _post(
            auth_client, environment.id, key="k1", rows=["state", "tool_routing"]
        )

        again = _post(
            auth_client, environment.id, key="k1", rows=["tool_routing", "state"]
        )

        assert again.status_code == 202, again.content
        assert again.json() == first.json()
        assert len(_run(environment).commands) == 1

    def test_a_repeated_key_replays_even_once_the_run_ended(
        self, user, workspace, storage, auth_client
    ):
        environment = _broken(user, workspace, storage, "fr-replay-end")
        first = _post(auth_client, environment.id, key="k1").json()["fix"]
        _set_state(environment, "failed")

        again = _post(auth_client, environment.id, key="k1")

        assert again.status_code == 202
        assert again.json()["fix"]["id"] == first["id"]

    def test_switched_off_is_404(self, user, workspace, storage, auth_client, settings):
        environment = _broken(user, workspace, storage, "fr-off")
        settings.HARNESS_READINESS_FIX_ENABLED = False

        _refused(_post(auth_client, environment.id), "fix_disabled", 404)


class TestAppend:
    @pytest.mark.parametrize("state", ["queued", "fixing", "waiting_on_you"])
    def test_adds_a_command_to_an_appendable_run(
        self, user, workspace, storage, auth_client, state
    ):
        environment = _broken(user, workspace, storage, f"fr-app-{state[:4]}")
        first = _post(auth_client, environment.id, rows=["state"]).json()["fix"]
        _set_state(environment, state)
        before = _run(environment).state_changed_at

        fix = _post(auth_client, environment.id, rows=["tool_routing"]).json()["fix"]

        assert (fix["id"], fix["state"]) == (first["id"], state)
        assert fix["rows"] == ["state", "tool_routing"]
        run = _run(environment)
        assert [one["n"] for one in run.commands] == [1, 2]
        assert run.commands[1]["message"] == ROUTING_MESSAGE
        assert run.lines[1] == {
            "row_id": "tool_routing",
            "area": run.lines[1]["area"],
            "label": run.lines[1]["label"],
            "state": "queued",
            "reason": None,
            "waiting_on": None,
            "command": 2,
        }
        assert run.state_changed_at > before

    def test_queue_lists_each_undispatched_command(
        self, user, workspace, storage, auth_client
    ):
        environment = _broken(user, workspace, storage, "fr-queue")
        _post(auth_client, environment.id, rows=["state"])
        run = _run(environment)
        run.commands[0]["state"] = "dispatched"
        run.save(update_fields=["commands"])

        fix = _post(auth_client, environment.id, rows=["tool_routing"]).json()["fix"]

        assert fix["queue"] == [{"message": "Tool calls stay in the test"}]

    @pytest.mark.parametrize("state", ["saving", "checking"])
    def test_refused_while_the_run_saves(
        self, user, workspace, storage, auth_client, state
    ):
        environment = _broken(user, workspace, storage, f"fr-app-{state}")
        _post(auth_client, environment.id, rows=["state"])
        _set_state(environment, state)

        _refused(
            _post(auth_client, environment.id, rows=["tool_routing"]), "fix_running"
        )
        assert len(_run(environment).commands) == 1

    def test_refused_when_every_row_is_open_already(
        self, user, workspace, storage, auth_client
    ):
        environment = _broken(user, workspace, storage, "fr-app-open")
        _post(auth_client, environment.id, rows=["state", "tool_routing"])

        _refused(_post(auth_client, environment.id, rows=["state"]), "fix_running")

    def test_only_the_rows_not_open_are_added(
        self, user, workspace, storage, auth_client
    ):
        environment = _broken(user, workspace, storage, "fr-app-some")
        _post(auth_client, environment.id, rows=["state"])

        _post(auth_client, environment.id, rows=["state", "tool_routing"])

        run = _run(environment)
        assert run.commands[1]["rows"] == ["tool_routing"]
        assert [one["row_id"] for one in run.lines] == ["state", "tool_routing"]

    def test_a_row_that_could_not_be_fixed_can_be_fixed_again(
        self, user, workspace, storage, auth_client
    ):
        environment = _broken(user, workspace, storage, "fr-app-again")
        _post(auth_client, environment.id, rows=["state", "tool_routing"])
        run = _run(environment)
        run.lines[0]["state"] = "could_not_fix"
        run.save(update_fields=["lines"])

        fix = _post(auth_client, environment.id, rows=["state"]).json()["fix"]

        assert fix["rows"] == ["tool_routing", "state"]
        assert _run(environment).commands[1]["rows"] == ["state"]
        # The latest line per row, in the order the rows were first tried.
        assert [(one["row_id"], one["state"]) for one in fix["lines"]] == [
            ("state", "queued"),
            ("tool_routing", "queued"),
        ]
        assert len(_run(environment).lines) == 3

    def test_a_row_is_tried_at_most_three_times(
        self, user, workspace, storage, auth_client
    ):
        environment = _broken(user, workspace, storage, "fr-tries")
        _post(auth_client, environment.id, rows=["state", "tool_routing"])
        for _ in range(2):
            _could_not_fix(environment, "state")
            response = _post(auth_client, environment.id, rows=["state"])
            assert response.status_code == 202, response.content
        _could_not_fix(environment, "state")

        _refused(_post(auth_client, environment.id, rows=["state"]), "fix_tries_used")

        run = _run(environment)
        assert [one["row_id"] for one in run.lines].count("state") == 3
        assert len(run.commands) == 3
        fix = _fix_get(auth_client, environment.id).json()["fix"]
        assert [one["row_id"] for one in fix["lines"]] == ["state", "tool_routing"]

    def test_an_exhausted_row_is_left_out_of_a_post(
        self, user, workspace, storage, auth_client
    ):
        environment = _broken(user, workspace, storage, "fr-tries-mixed")
        _post(auth_client, environment.id, rows=["state", "tool_routing"])
        for _ in range(2):
            _could_not_fix(environment, "state")
            _post(auth_client, environment.id, rows=["state"])
        _could_not_fix(environment, "state")
        _could_not_fix(environment, "tool_routing")

        response = _post(auth_client, environment.id, rows=["state", "tool_routing"])

        assert response.status_code == 202, response.content
        run = _run(environment)
        assert run.commands[-1]["rows"] == ["tool_routing"]
        assert [one["row_id"] for one in run.lines].count("state") == 3

    def test_a_gate_row_rides_on_its_open_gate(
        self, user, workspace, storage, auth_client
    ):
        environment = _broken(user, workspace, storage, "fr-gate")
        _post(auth_client, environment.id, rows=["state"])

        fix = _post(auth_client, environment.id, rows=["reset"]).json()["fix"]

        run = _run(environment)
        assert run.lines[1] == {
            **run.lines[1],
            "state": "waiting_on_other",
            "waiting_on": "state",
            "command": 1,
        }
        # Nothing of its own to send; kept so the key replays.
        assert (run.commands[1]["rows"], run.commands[1]["message"]) == ([], "")
        assert run.commands[1]["state"] == "settled"
        assert run.commands[1]["settled_at"] is None
        assert run.commands[1]["outcome"]["turn"] == "not_needed"
        assert fix["queue"] == [{"message": "Starting records"}]
        assert fix["rows"] == ["state", "reset"]

    def test_a_gate_only_post_replays_unchanged(
        self, user, workspace, storage, auth_client
    ):
        environment = _broken(user, workspace, storage, "fr-gate-replay")
        _post(auth_client, environment.id, rows=["state"])
        first = _post(auth_client, environment.id, key="g1", rows=["reset"])
        before = _run(environment)

        again = _post(auth_client, environment.id, key="g1", rows=["reset"])

        after = _run(environment)
        assert again.status_code == 202
        assert again.json() == first.json()
        assert len(after.commands) == 2
        assert (after.commands, after.lines, after.state_changed_at) == (
            before.commands,
            before.lines,
            before.state_changed_at,
        )


class TestRefusals:
    def test_unsaved_changes_without_a_run(self, user, workspace, storage, auth_client):
        environment = _broken(user, workspace, storage, "fr-unsaved")
        _draft(environment.suite_version)

        _refused(_post(auth_client, environment.id), "unsaved_changes")
        assert not ReadinessFixRun.no_workspace_objects.filter(job=environment).exists()

    def test_a_stale_draft_is_unsaved_changes_too(
        self, user, workspace, storage, auth_client
    ):
        environment = _broken(user, workspace, storage, "fr-stale")
        older = _copy_version(environment.environment_version)
        _draft(older)

        _refused(_post(auth_client, environment.id), "unsaved_changes")

    def test_drafts_do_not_stop_adding_to_a_running_fix(
        self, user, workspace, storage, auth_client
    ):
        environment = _broken(user, workspace, storage, "fr-unsaved-run")
        _post(auth_client, environment.id, rows=["state"])
        _draft(environment.suite_version)

        response = _post(auth_client, environment.id, rows=["tool_routing"])

        assert response.status_code == 202, response.content

    def test_nothing_to_fix(self, user, workspace, storage, auth_client):
        environment = _broken(user, workspace, storage, "fr-nothing")

        # ``start`` is Supported: no fix, so not the builder's.
        _refused(_post(auth_client, environment.id, rows=["start"]), "nothing_to_fix")

    def test_nothing_to_fix_on_a_healthy_environment(
        self, user, workspace, storage, auth_client
    ):
        environment = _environment(user, workspace, storage, "fr-healthy")
        _certify(environment, storage)

        _refused(_post(auth_client, environment.id), "nothing_to_fix")

    def test_nothing_to_fix_beside_a_running_fix_is_not_fix_running(
        self, user, workspace, storage, auth_client
    ):
        environment = _broken(user, workspace, storage, "fr-nothing-run")
        _post(auth_client, environment.id, rows=["state"])

        _refused(_post(auth_client, environment.id, rows=["start"]), "nothing_to_fix")

    def test_an_empty_row_list_is_refused(self, user, workspace, storage, auth_client):
        environment = _broken(user, workspace, storage, "fr-empty-rows")

        response = _post(auth_client, environment.id, rows=[])

        assert response.status_code == 400, response.content
        assert not ReadinessFixRun.no_workspace_objects.filter(job=environment).exists()

    def test_authoring_usage_is_checked_before_the_rows(
        self, user, workspace, storage, auth_client
    ):
        environment = _broken(user, workspace, storage, "fr-usage")

        with patch(
            "simulate.services.harness_readiness.fix_runs.require_harness_authoring",
            side_effect=HostedHarnessError(
                "usage_limit", "Limit reached", status_code=402
            ),
        ):
            response = _post(auth_client, environment.id, rows=["nope"])

        _refused(response, "usage_limit", 402)
        assert not ReadinessFixRun.no_workspace_objects.filter(job=environment).exists()

    def test_chat_unavailable_is_checked_again_under_the_lock(
        self, user, workspace, storage, auth_client
    ):
        environment = _broken(user, workspace, storage, "fr-chat-lock")

        with patch(
            "simulate.services.harness_readiness.fix_runs._chat_unavailable",
            side_effect=[False, True],
        ):
            response = _post(auth_client, environment.id)

        _refused(response, "chat_unavailable")
        assert not ReadinessFixRun.no_workspace_objects.filter(job=environment).exists()

    def test_unknown_rows(self, user, workspace, storage, auth_client):
        environment = _broken(user, workspace, storage, "fr-unknown")

        response = _post(auth_client, environment.id, rows=["state", "nope"])

        _refused(response, "fix_rows_unknown", 400)

    @pytest.mark.parametrize("group", ["needs_you", "recheck"])
    def test_other_groups(self, user, workspace, storage, auth_client, group):
        environment = _broken(user, workspace, storage, f"fr-group-{group[:4]}")

        _refused(
            _post(auth_client, environment.id, group=group),
            "fix_group_unsupported",
            400,
        )

    def test_versions_other_than_the_defaults(
        self, user, workspace, storage, auth_client
    ):
        environment = _broken(user, workspace, storage, "fr-picks")
        other = _copy_version(environment.environment_version)

        response = _post(auth_client, environment.id, environment_version_id=other.id)

        _refused(response, "fix_needs_defaults")

    def test_naming_the_defaults_is_fine(self, user, workspace, storage, auth_client):
        environment = _broken(user, workspace, storage, "fr-picks-ok")

        response = _post(
            auth_client,
            environment.id,
            environment_version_id=environment.environment_version_id,
            suite_version_id=environment.suite_version_id,
        )

        assert response.status_code == 202, response.content

    def test_a_rebuild_in_flight(self, user, workspace, storage, auth_client):
        environment = _broken(user, workspace, storage, "fr-rebuild")
        HarnessNudge.no_workspace_objects.create(
            job=environment,
            organization_id=environment.organization_id,
            workspace_id=environment.workspace_id,
            target="suite",
            state="queued",
        )

        _refused(_post(auth_client, environment.id), "rebuild_running")

    def test_chat_unavailable_while_the_environment_builds(
        self, user, workspace, storage, auth_client
    ):
        environment = _broken(user, workspace, storage, "fr-chat-busy")
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            state=HostedHarnessJob.State.RUNNING
        )

        _refused(_post(auth_client, environment.id), "chat_unavailable")

    def test_chat_unavailable_while_scenarios_are_added(
        self, user, workspace, storage, auth_client
    ):
        environment = _broken(user, workspace, storage, "fr-chat-extend")
        payload = dict(environment.payload)
        payload["metadata"] = {**payload["metadata"], "scenario_extend": {"n": 1}}
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            payload=payload
        )

        _refused(_post(auth_client, environment.id), "chat_unavailable")

    def test_chat_unavailable_once_its_files_are_gone(
        self, user, workspace, storage, auth_client
    ):
        environment = _broken(user, workspace, storage, "fr-chat-retired")
        _chat(environment, state=HostedHarnessConversation.State.RETIRED)

        _refused(_post(auth_client, environment.id), "chat_unavailable")

    def test_chat_unavailable_without_saved_files(
        self, user, workspace, storage, auth_client
    ):
        environment = _broken(user, workspace, storage, "fr-chat-nofiles")
        payload = dict(environment.payload)
        payload["metadata"] = {
            key: value
            for key, value in payload["metadata"].items()
            if key != "authoring_object_key"
        }
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            payload=payload
        )

        _refused(_post(auth_client, environment.id), "chat_unavailable")

    def test_a_chat_workspace_stands_for_saved_files(
        self, user, workspace, storage, auth_client
    ):
        environment = _broken(user, workspace, storage, "fr-chat-ws")
        payload = dict(environment.payload)
        payload["metadata"] = {
            key: value
            for key, value in payload["metadata"].items()
            if key != "authoring_object_key"
        }
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            payload=payload
        )
        _chat(environment, latest_workspace_object_key="chat/ws.tar.gz")

        assert _post(auth_client, environment.id).status_code == 202

    def test_chat_unavailable_without_a_sandbox_provider(
        self, user, workspace, storage, auth_client
    ):
        environment = _broken(user, workspace, storage, "fr-chat-sandbox")

        with patch(
            "simulate.services.harness_versioning.rebuild.sandbox_provider_configured",
            return_value=False,
        ):
            _refused(_post(auth_client, environment.id), "chat_unavailable")

    def test_chat_unavailable_when_edits_do_not_go_to_drafts(
        self, user, workspace, storage, auth_client
    ):
        environment = _broken(user, workspace, storage, "fr-chat-drafts")

        with patch(
            "simulate.services.harness_versioning.drafts.drafts_apply",
            return_value=False,
        ):
            _refused(_post(auth_client, environment.id), "chat_unavailable")


class TestModel:
    def test_one_active_run_per_environment(self, user, workspace, storage):
        environment = _broken(user, workspace, storage, "fr-uniq")
        _closed_run(environment, "queued", lines={})

        with pytest.raises(IntegrityError), transaction.atomic():
            _closed_run(environment, "saving", lines={})

    def test_ended_runs_do_not_count(self, user, workspace, storage):
        environment = _broken(user, workspace, storage, "fr-uniq-ended")
        _closed_run(environment, "done", lines={})
        _closed_run(environment, "failed", lines={})

        _closed_run(environment, "fixing", lines={})

    def test_guards_for_other_writers(self, user, workspace, storage):
        environment = _broken(user, workspace, storage, "fr-guards")
        fix_runs.require_no_active_fix(environment)
        fix_runs.require_no_fix_waiting(environment)
        _closed_run(environment, "fixing", lines={})

        with pytest.raises(HostedHarnessError) as running:
            fix_runs.require_no_active_fix(environment)
        fix_runs.require_no_fix_waiting(environment)
        _set_state(environment, "waiting_on_you")
        # Only while its question is still open in the chat.
        fix_runs.require_no_fix_waiting(environment)
        _chat(
            environment,
            state="waiting_for_user",
            blocking_input={"message_id": str(uuid.uuid4())},
        )
        with pytest.raises(HostedHarnessError) as waiting:
            fix_runs.require_no_fix_waiting(environment)

        assert (running.value.code, running.value.status_code) == ("fix_running", 409)
        assert (waiting.value.code, waiting.value.status_code) == ("fix_waiting", 409)


def _progress(body):
    return {
        row["id"]: row["fix_progress"]["state"]
        for row in body["rows"]
        if "fix_progress" in row
    }


class TestOverlay:
    def test_an_active_run_shows_on_the_defaults(
        self, user, workspace, storage, auth_client
    ):
        environment = _broken(user, workspace, storage, "fr-ov-active")
        fix = _post(auth_client, environment.id, rows=["state"]).json()["fix"]

        body = _get(auth_client, environment.id).json()

        rows = _rows(body)
        assert rows["state"]["fix_progress"] == {
            "state": "queued",
            "reason": None,
            "waiting_on": None,
            "fix_id": fix["id"],
        }
        assert "fix_progress" not in rows["tool_routing"]
        assert "fix_result" not in rows["state"]
        assert HarnessCapabilitySelectionReportSerializer(data=body).is_valid()

    def test_the_cached_report_is_never_changed(
        self, user, workspace, storage, auth_client
    ):
        environment = _broken(user, workspace, storage, "fr-ov-cache")
        _post(auth_client, environment.id, rows=["state"])

        first = _get(auth_client, environment.id).json()
        second = _get(auth_client, environment.id).json()
        cached = harness_readiness.readiness_report(_pairing(environment))

        assert first == second
        assert all("fix_progress" not in row for row in cached["rows"])

    def test_not_on_other_versions(self, user, workspace, storage, auth_client):
        environment = _broken(user, workspace, storage, "fr-ov-other")
        _post(auth_client, environment.id, rows=["state"])
        other = _copy_version(environment.suite_version)

        body = _get(auth_client, environment.id, suite_version_id=other.id).json()

        assert _progress(body) == {}

    def test_an_active_run_follows_the_defaults_off_its_base(
        self, user, workspace, storage, auth_client
    ):
        environment = _broken(user, workspace, storage, "fr-ov-moved")
        _post(auth_client, environment.id, rows=["state"])
        base_suite = environment.suite_version
        moved = _copy_version(base_suite)
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            suite_version_id=moved.id
        )

        current = _get(auth_client, environment.id).json()
        base = _get(auth_client, environment.id, suite_version_id=base_suite.id).json()

        assert _progress(current) == {"state": "queued"}
        assert _progress(base) == {}

    def test_switched_off_shows_nothing(
        self, user, workspace, storage, auth_client, settings
    ):
        environment = _broken(user, workspace, storage, "fr-ov-off")
        _post(auth_client, environment.id, rows=["state"])
        settings.HARNESS_READINESS_FIX_ENABLED = False

        assert _progress(_get(auth_client, environment.id).json()) == {}

    def test_a_saved_run_shows_on_what_it_saved_and_its_result_on_the_base(
        self, user, workspace, storage, auth_client
    ):
        environment = _broken(user, workspace, storage, "fr-ov-saved")
        base_env = environment.environment_version
        saved_env = _copy_version(base_env)
        run = _closed_run(
            environment,
            "done",
            lines={"state": "fixed", "tool_routing": "could_not_fix"},
            result_environment_version=saved_env,
            result_suite_version_id=environment.suite_version_id,
        )
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            environment_version_id=saved_env.id
        )

        result = _get(auth_client, environment.id).json()
        base = _get(auth_client, environment.id, environment_version_id=base_env.id)
        fix = _fix_get(auth_client, environment.id).json()["fix"]

        # ``state`` is still Blocked on the saved version: no "fixed" shown there.
        assert _progress(result) == {"tool_routing": "could_not_fix"}
        rows = _rows(base.json())
        assert _progress(base.json()) == {}
        ref = {"id": str(saved_env.id), "label": f"v{saved_env.version_number}"}
        assert rows["state"]["fix_result"] == {
            "state": "fixed",
            "environment": ref,
            "suite": None,
            # Not re-checked: the run never recorded a finished re-check.
            "checked": False,
            "fix_id": str(run.id),
        }
        assert rows["tool_routing"]["fix_result"]["state"] == "could_not_fix"
        assert (fix["saved"], fix["draft"]) == (
            {"environment": ref, "suite": None},
            False,
        )
        assert HarnessCapabilitySelectionReportSerializer(data=base.json()).is_valid()

    def test_an_unsaved_run_shows_while_its_draft_is_open(
        self, user, workspace, storage, auth_client
    ):
        environment = _broken(user, workspace, storage, "fr-ov-draft")
        draft = _draft(environment.suite_version)
        _closed_run(
            environment,
            "done",
            lines={"state": "waiting_on_you"},
            error={"code": "validation_failed", "message": "It failed"},
        )

        shown = _get(auth_client, environment.id).json()
        fix = _fix_get(auth_client, environment.id).json()["fix"]
        type(draft).no_workspace_objects.filter(id=draft.id).update(status="discarded")
        after = _get(auth_client, environment.id).json()

        assert _progress(shown) == {"state": "waiting_on_you"}
        assert (fix["draft"], fix["saved"]) == (True, None)
        assert _progress(after) == {}
        # Fixable again once the draft is gone.
        assert _post(auth_client, environment.id).status_code == 202

    def test_a_failed_run_shows_while_the_defaults_stay(
        self, user, workspace, storage, auth_client
    ):
        environment = _broken(user, workspace, storage, "fr-ov-failed")
        _closed_run(environment, "failed", lines={"state": "could_not_fix"})

        before = _progress(_get(auth_client, environment.id).json())
        moved = _copy_version(environment.suite_version)
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            suite_version_id=moved.id
        )
        after = _progress(_get(auth_client, environment.id).json())

        assert (before, after) == ({"state": "could_not_fix"}, {})

    @pytest.mark.parametrize(
        ("state", "line"), [("fixing", "fixing"), ("failed", "could_not_fix")]
    )
    def test_a_later_run_keeps_the_saved_result_on_its_base(
        self, user, workspace, storage, auth_client, state, line
    ):
        environment = _broken(user, workspace, storage, f"fr-ov-later-{state[:4]}")
        base_env = environment.environment_version
        saved_env = _copy_version(base_env)
        saved = _closed_run(
            environment,
            "done",
            lines={"state": "fixed"},
            result_environment_version=saved_env,
            result_suite_version_id=environment.suite_version_id,
            autosave={"checked": True},
        )
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            environment_version_id=saved_env.id
        )
        environment.refresh_from_db()
        later = _closed_run(environment, state, lines={"tool_routing": line})

        base = _get(auth_client, environment.id, environment_version_id=base_env.id)
        current = _get(auth_client, environment.id).json()

        result = _rows(base.json())["state"]["fix_result"]
        ref = {"id": str(saved_env.id), "label": f"v{saved_env.version_number}"}
        assert (result["fix_id"], result["environment"], result["checked"]) == (
            str(saved.id),
            ref,
            True,
        )
        assert _progress(current) == {"tool_routing": line}
        assert _rows(current)["tool_routing"]["fix_progress"]["fix_id"] == str(later.id)
        assert HarnessCapabilitySelectionReportSerializer(data=base.json()).is_valid()

    def test_the_newest_run_saved_from_the_base_wins(
        self, user, workspace, storage, auth_client
    ):
        environment = _broken(user, workspace, storage, "fr-ov-newest")
        base_env = environment.environment_version
        older_env = _copy_version(base_env)
        newer_env = _copy_version(older_env)
        for saved_env in (older_env, newer_env):
            newest = _closed_run(
                environment,
                "done",
                lines={"state": "fixed"},
                result_environment_version=saved_env,
                result_suite_version_id=environment.suite_version_id,
            )

        body = _get(auth_client, environment.id).json()

        result = _rows(body)["state"]["fix_result"]
        assert (result["fix_id"], result["environment"]["id"]) == (
            str(newest.id),
            str(newer_env.id),
        )


def _actions(body):
    return {row["id"]: row["fix_action"] for row in body["rows"]}


def _hold_needs_defaults(environment, auth_client):
    """The defaults moved off the read versions, which still need fixes."""
    base_env = environment.environment_version_id
    moved = _copy_version(environment.environment_version)
    HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
        environment_version_id=moved.id
    )
    return {"environment_version_id": base_env}, "state"


def _hold_chat(environment, auth_client):
    _chat(environment, state=HostedHarnessConversation.State.RETIRED)
    return {}, "state"


def _hold_rebuild(environment, auth_client):
    HarnessNudge.no_workspace_objects.create(
        job=environment,
        organization_id=environment.organization_id,
        workspace_id=environment.workspace_id,
        target="suite",
        state="queued",
    )
    return {}, "state"


def _hold_saving(environment, auth_client):
    """A run saving ``state``; ``tool_routing`` is outside it."""
    _post(auth_client, environment.id, rows=["state"])
    _set_state(environment, "saving")
    return {}, "tool_routing"


def _hold_unsaved(environment, auth_client):
    _draft(environment.suite_version)
    return {}, "state"


class TestFixAction:
    def test_the_rows_a_fix_can_take(self, user, workspace, storage, auth_client):
        environment = _broken(user, workspace, storage, "fr-act")

        body = _get(auth_client, environment.id).json()

        actions = _actions(body)
        free = {"available": True, "held": None}
        assert (actions["state"], actions["tool_routing"]) == (free, free)
        # Supported: nothing for the builder chat to fix.
        assert actions["start"] == {"available": False, "held": None}
        serializer = HarnessCapabilitySelectionReportSerializer(data=body)
        assert serializer.is_valid(), serializer.errors

    @pytest.mark.parametrize(
        ("code", "setup"),
        [
            ("fix_needs_defaults", _hold_needs_defaults),
            ("chat_unavailable", _hold_chat),
            ("rebuild_running", _hold_rebuild),
            ("fix_running", _hold_saving),
            ("unsaved_changes", _hold_unsaved),
        ],
    )
    def test_held_says_what_the_post_refuses(
        self, user, workspace, storage, auth_client, code, setup
    ):
        environment = _broken(user, workspace, storage, f"fr-held-{code[:8]}")
        picks, row = setup(environment, auth_client)

        body = _get(auth_client, environment.id, **picks).json()
        response = _post(auth_client, environment.id, rows=[row], **picks)

        assert _actions(body)[row] == {"available": True, "held": code}
        _refused(response, code)
        serializer = HarnessCapabilitySelectionReportSerializer(data=body)
        assert serializer.is_valid(), serializer.errors

    def test_a_row_in_the_running_fix_is_not_available(
        self, user, workspace, storage, auth_client
    ):
        environment = _broken(user, workspace, storage, "fr-act-open")
        _post(auth_client, environment.id, rows=["state"])

        actions = _actions(_get(auth_client, environment.id).json())

        assert actions["state"] == {"available": False, "held": None}
        # The run takes more rows: nothing holds the others.
        assert actions["tool_routing"] == {"available": True, "held": None}

    def test_a_row_that_could_not_be_fixed_is_available_again(
        self, user, workspace, storage, auth_client
    ):
        environment = _broken(user, workspace, storage, "fr-act-again")
        _post(auth_client, environment.id, rows=["state"])
        _could_not_fix(environment, "state")

        actions = _actions(_get(auth_client, environment.id).json())

        assert actions["state"] == {"available": True, "held": None}

    def test_a_row_tried_enough_is_not_available(
        self, user, workspace, storage, auth_client
    ):
        environment = _broken(user, workspace, storage, "fr-act-tries")
        _post(auth_client, environment.id, rows=["state"])
        for _ in range(2):
            _could_not_fix(environment, "state")
            _post(auth_client, environment.id, rows=["state"])
        _could_not_fix(environment, "state")

        actions = _actions(_get(auth_client, environment.id).json())

        assert actions["state"] == {"available": False, "held": None}
        _refused(_post(auth_client, environment.id, rows=["state"]), "fix_tries_used")

    def test_a_failing_hold_check_never_fails_the_read(
        self, user, workspace, storage, auth_client
    ):
        environment = _broken(user, workspace, storage, "fr-act-boom")

        with patch(
            "simulate.services.harness_readiness.fix_runs._chat_unavailable",
            side_effect=RuntimeError("boom"),
        ):
            response = _get(auth_client, environment.id)

        assert response.status_code == 200, response.content
        assert _actions(response.json())["state"] == {"available": True, "held": None}

    def test_switched_off_and_the_cached_report_have_none(
        self, user, workspace, storage, auth_client, settings
    ):
        environment = _broken(user, workspace, storage, "fr-act-off")
        _get(auth_client, environment.id)
        cached = harness_readiness.readiness_report(_pairing(environment))
        settings.HARNESS_READINESS_FIX_ENABLED = False

        body = _get(auth_client, environment.id).json()

        assert all("fix_action" not in row for row in body["rows"])
        assert all("fix_action" not in row for row in cached["rows"])


def _serialized(environment, run):
    environment.refresh_from_db()
    run.refresh_from_db()
    return fix_runs.serialize(run, environment)


def _outcome(environment, run):
    return _serialized(environment, run)["outcome"]


def _saved_run(environment, lines, **fields):
    return _closed_run(
        environment,
        "done",
        lines=lines,
        result_environment_version=_copy_version(environment.environment_version),
        result_suite_version_id=environment.suite_version_id,
        **fields,
    )


def _with_reason(run, row, message):
    for line in run.lines:
        if line["row_id"] == row:
            line["reason"] = {"code": "still_failing", "message": message}
    run.save(update_fields=["lines"])


class TestOutcome:
    def test_none_while_the_run_works(self, user, workspace, storage, auth_client):
        environment = _broken(user, workspace, storage, "fr-out-saving")
        _post(auth_client, environment.id, rows=["state"])
        _set_state(environment, "saving")

        fix = _fix_get(auth_client, environment.id).json()["fix"]

        assert (fix["active"], fix["accepts_rows"], fix["outcome"]) == (
            True,
            False,
            None,
        )

    @pytest.mark.parametrize(
        ("lines", "expected"),
        [
            ({"state": "fixed"}, ("fixed", 1, 1, "Fixed")),
            (
                {"state": "fixed", "tool_routing": "could_not_fix"},
                ("partly_fixed", 1, 2, "Fixed 1 of 2"),
            ),
            ({"tool_routing": "could_not_fix"}, ("could_not_fix", 0, 1, "Still")),
        ],
    )
    def test_a_checked_save_counts_its_lines(
        self, user, workspace, storage, lines, expected
    ):
        environment = _broken(user, workspace, storage, "fr-out-saved")
        run = _saved_run(environment, lines, autosave={"checked": True})
        _with_reason(run, "tool_routing", "Still")

        body = _serialized(environment, run)

        found = body["outcome"]
        assert (found["kind"], found["fixed"], found["total"], found["message"]) == (
            expected
        )
        assert (body["active"], body["accepts_rows"]) == (False, False)
        serializer = HarnessReadinessFixResponseSerializer(data={"fix": body})
        assert serializer.is_valid(), serializer.errors

    @pytest.mark.parametrize("autosave", [{"checked": False}, {}])
    def test_a_save_not_re_checked_says_so(self, user, workspace, storage, autosave):
        environment = _broken(user, workspace, storage, "fr-out-unchecked")
        run = _saved_run(environment, {"state": "fixed"}, autosave=autosave)

        found = _outcome(environment, run)

        assert (found["kind"], found["fixed"], found["total"]) == (
            "saved_unchecked",
            1,
            1,
        )
        assert found["message"]

    def test_unsaved_while_the_fixs_draft_waits(self, user, workspace, storage):
        environment = _broken(user, workspace, storage, "fr-out-unsaved")
        _draft(environment.suite_version)
        run = _closed_run(
            environment,
            "done",
            lines={"state": "waiting_on_you"},
            error={"code": "validation_failed", "message": "It failed"},
        )

        body = _serialized(environment, run)

        assert body["draft"] is True
        assert body["outcome"] == {
            "kind": "unsaved",
            "fixed": 0,
            "total": 1,
            "message": "It failed",
        }

    def test_draft_gone_once_the_draft_is_edited_after_the_end(
        self, user, workspace, storage
    ):
        environment = _broken(user, workspace, storage, "fr-out-edited")
        _draft(environment.environment_version)
        later = _draft(environment.suite_version)
        run = _closed_run(environment, "done", lines={"state": "waiting_on_you"})
        type(later).no_workspace_objects.filter(id=later.id).update(
            updated_at=run.finished_at + timedelta(seconds=1)
        )

        body = _serialized(environment, run)

        # ``draft`` reads only that a draft is open.
        assert (body["draft"], body["outcome"]["kind"]) == (True, "draft_gone")

    def test_draft_gone_once_the_draft_is_discarded(self, user, workspace, storage):
        environment = _broken(user, workspace, storage, "fr-out-discarded")
        draft = _draft(environment.suite_version)
        run = _closed_run(environment, "done", lines={"state": "waiting_on_you"})
        type(draft).no_workspace_objects.filter(id=draft.id).update(status="discarded")

        body = _serialized(environment, run)

        assert (body["draft"], body["outcome"]["kind"]) == (False, "draft_gone")

    def test_a_failed_run_could_not_fix(self, user, workspace, storage):
        environment = _broken(user, workspace, storage, "fr-out-failed")
        run = _closed_run(
            environment,
            "failed",
            lines={"state": "could_not_fix"},
            error={"code": "turn_failed", "message": "The turn failed"},
        )

        found = _outcome(environment, run)

        assert (found["kind"], found["message"]) == ("could_not_fix", "The turn failed")

    def test_a_failed_run_counts_rows_already_supported(self, user, workspace, storage):
        environment = _broken(user, workspace, storage, "fr-out-nochange")
        run = _closed_run(
            environment,
            "failed",
            lines={"state": "fixed"},
            error={"code": "no_changes", "message": "No changes"},
        )

        found = _outcome(environment, run)

        assert found == {
            "kind": "fixed",
            "fixed": 1,
            "total": 1,
            "message": "No changes",
        }

    def test_counts_each_row_once(self, user, workspace, storage):
        environment = _broken(user, workspace, storage, "fr-out-twice")
        run = _saved_run(environment, {"state": "fixed"}, autosave={"checked": True})
        run.lines = [{**run.lines[0], "state": "could_not_fix"}, *run.lines]
        run.save(update_fields=["lines"])

        found = _outcome(environment, run)

        assert (found["kind"], found["fixed"], found["total"]) == ("fixed", 1, 1)
