"""Rebuild: a nudge answered "yes" runs as one builder turn, then lands or fails."""

import json
import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from simulate.models import (
    HarnessEnvironmentVersion,
    HarnessNudge,
    HostedHarnessConversation,
    HostedHarnessConversationLease,
    HostedHarnessConversationMessage,
    HostedHarnessJob,
)
from simulate.services.harness_versioning import archives, drafts, nudges, rebuild
from simulate.services.hosted_harness import HostedHarnessError, canonical_digest
from simulate.services.hosted_harness_conversation import (
    enqueue_message,
    ensure_conversation,
    issue_conversation_capability,
    pending_commands,
    promote_conversation_checkpoint,
    recover_stalled_conversations,
    serialize_conversation,
)
from simulate.tasks import harness_nudges as nudge_tasks
from simulate.tasks import hosted_harness_conversation as conversation_tasks
from simulate.tests.test_harness_agent_versions import (  # noqa: F401 - fixture
    scheduled as scheduled,
)
from simulate.tests.test_harness_nudges import NEW_VALUE, _activated, _answer
from simulate.tests.test_harness_nudges import (  # noqa: F401 - fixture
    started as started,
)
from simulate.tests.test_harness_versioning_backfill import (  # noqa: F401 - fixture
    storage as storage,
)
from simulate.tests.test_harness_versioning_record import _authoring, _resealed_manifest
from simulate.tests.test_hosted_harness_conversation import (
    _headers,
    _put_conversation_checkpoint,
)

pytestmark = pytest.mark.django_db

BASE = "/simulate/api/harness-environments"
GUEST = "/simulate/api/harness/conversations"
REFUND = {"agent": "support", "tools": [{"name": "refund_order"}]}
UNCHECKED = json.dumps(
    {"sub_goals": [{"name": "refunded", "what": "a refund was issued"}]}
)
# The default sub-goal, checking the tool REFUND adds.
CHECKED = json.dumps(
    {
        "sub_goals": [
            {
                "name": "refunded",
                "what": "a refund was issued",
                "judged": "Pass when refund_order succeeded.",
            }
        ]
    }
)


@pytest.fixture(autouse=True)
def nudges_on(settings):
    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_VERSIONING_NUDGES_ENABLED = True
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = False
    settings.HARNESS_PUBLIC_BASE_URL = "https://platform.example"


@pytest.fixture
def inline(monkeypatch):
    """Rebuild activities run in-process, by name; the runtime and sandbox ones are
    recorded only."""
    calls = []

    def run(task, call):
        def apply_async(*, args, **_options):
            calls.append((task.name, list(args)))
            if call is not None:
                call(*args)

        monkeypatch.setattr(task, "apply_async", apply_async)

    run(nudge_tasks.check_harness_nudges, None)
    run(nudge_tasks.finish_harness_nudge_turn, rebuild.finish_nudge_turn)
    run(nudge_tasks.stop_harness_nudge_sandbox, None)
    run(nudge_tasks.retry_harness_deferred_activations, None)
    run(conversation_tasks.ensure_hosted_harness_conversation_runtime, None)
    run(
        conversation_tasks.promote_hosted_harness_conversation_checkpoint,
        conversation_tasks.promote_hosted_harness_conversation_checkpoint._original_func,
    )
    return calls


def _names(calls, name):
    return [args for task, args in calls if task == name]


def _queued(user, workspace, storage, key, auth_client, capture):
    """A backfilled environment on A2 (a value changed from A1), and its card answered
    yes."""
    environment, a1, a2 = _activated(user, workspace, storage, key, capture)
    [nudge] = HarnessNudge.no_workspace_objects.filter(job=environment)
    response = _answer(auth_client, environment, nudge, "yes")
    assert response.status_code == 200, response.content
    nudge.refresh_from_db()
    environment.refresh_from_db()
    return environment, nudge, a1, a2


def _result(nudge, *, changed=True, passed=True, problems=(), notes="done", **extra):
    return json.dumps(
        {
            "nudge_id": str(nudge.id),
            "stage": nudge.target,
            "changed": changed,
            "notes": notes,
            "checks": {
                "passed": passed,
                "problems": list(problems),
                "warnings": extra.pop("warnings", []),
                "not_checked": extra.pop("not_checked", []),
            },
            "left_out": extra.pop("left_out", []),
        }
    )


def _rebuilt(nudge, *, contract=REFUND, compiled=True, **files):
    """The bundle the rebuild seals, stamped with what it carried."""
    tree = archives.read_archive(_authoring(contract=contract, **files))
    return _resealed_manifest(tree, compiled=compiled, rebuilt=str(nudge.id))


def _saved_world(world):
    """What save_world writes for ``world``: its manifest and a handler per tool."""
    return {
        "manifest.json": json.dumps(world),
        **{
            f"handlers/{name}.py": f"def {name}(world):\n    pass\n"
            for name in world["tools"]
        },
    }


def _turn(
    environment,
    nudge,
    storage,
    *,
    contract=REFUND,
    result=...,
    meanwhile=None,
    lease_agent=None,
    compiled=True,
    **files,
):
    """The rebuild's turn as feedNudgeTurn runs it: dispatched, started, its save
    tagged with the nudge, then landed from the raw checkpoint. ``meanwhile`` runs
    once the turn started; ``lease_agent`` is the agent its sandbox runs as."""
    assert rebuild.dispatch_nudge(environment.id, start_runtime=False)
    nudge.refresh_from_db()
    message = nudge.command_message
    if lease_agent is not None:
        issue_conversation_capability(
            message.conversation,
            endpoint_base_url="https://platform.example",
            provider_ref="sandbox-nudge",
            attempt=None,
            ttl_seconds=600,
            agent_version=lease_agent,
        )
    rebuild.start_nudge_turn(nudge.id, message_id=message.id, started_at=None)
    if meanwhile is not None:
        meanwhile()
    conversation = message.conversation
    token = drafts.compose_workspace(
        environment, conversation, only_draft=nudge.target
    ).token
    if result is not None:
        files[".futureagi-nudge-result.json"] = (
            _result(nudge) if result is ... else result
        )
    if nudge.target == "environment" and contract is not None:
        files.setdefault("sub_goals.json", CHECKED)
    # The rebuild seals its own bundle.
    files.setdefault(
        "environment-bundle/manifest.json",
        _rebuilt(nudge, contract=contract, compiled=compiled, **files),
    )
    from simulate.services.hosted_harness_gateway import (
        with_authoring_basis,
        with_draft_basis,
    )

    body = with_draft_basis(
        with_authoring_basis(
            _authoring(contract=contract, **files),
            message.payload["_environment_revision"],
        ),
        token,
    )
    key, digest = _put_conversation_checkpoint(conversation, storage, body)
    promote_conversation_checkpoint(
        conversation,
        object_key=key,
        digest=digest,
        sequence=10,
        expected_revision=message.payload["_environment_revision"],
        command=message,
    )
    nudge.refresh_from_db()
    rebuild.land_nudge_result(nudge, checkpoint_key=key, turn_event=None)
    nudge.refresh_from_db()
    environment.refresh_from_db()
    return nudge


class TestAnswer:
    def test_rebuild_queues_and_holds_the_environment(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        from simulate.services.harness_versioning.versions import move_default

        environment, nudge, a1, a2 = _queued(
            user,
            workspace,
            storage,
            "r-queue",
            auth_client,
            django_capture_on_commit_callbacks,
        )

        assert nudge.state == "queued"
        assert nudge.answered_by_id == user.id
        assert nudge.base_agent_version_id == a1.id
        assert nudge.base_environment_version_id == environment.environment_version_id
        assert nudge.draft_snapshot == {"answered_default_agent_id": str(a2.id)}
        for move in (
            lambda: move_default(
                environment,
                "environment",
                environment.environment_version_id,
                expected_default_id=None,
            ),
            lambda: drafts.discard_draft(environment, "suite", user=user),
        ):
            with pytest.raises(HostedHarnessError) as refused:
                move()
            assert refused.value.code == "rebuild_running"
        body = auth_client.get(f"{BASE}/{environment.id}/nudges/").json()[0]
        assert (body["state"], body["can_stop"], body["closed_at"]) == (
            "queued",
            True,
            None,
        )

    def test_usage_refused_leaves_the_nudge_pending(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        from ee.usage.exceptions import UsageLimitExceeded
        from ee.usage.schemas.events import CheckResult

        refusal = CheckResult(
            allowed=False,
            error_code="BUDGET_PAUSED",
            dimension="ai_credits",
            reason="Authoring is paused by your budget",
            current_usage=12,
            limit=12,
        )
        environment, _a1, _a2 = _activated(
            user, workspace, storage, "r-usage", django_capture_on_commit_callbacks
        )
        [nudge] = HarnessNudge.no_workspace_objects.filter(job=environment)
        with patch(
            "simulate.services.harness_usage.require_harness_authoring",
            side_effect=UsageLimitExceeded(refusal),
        ):
            response = _answer(auth_client, environment, nudge, "yes")

        assert response.status_code == 402
        nudge.refresh_from_db()
        assert nudge.state == "pending"

    def test_a_building_environment_and_a_closed_nudge_are_refused(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        environment, _a1, _a2 = _activated(
            user, workspace, storage, "r-building", django_capture_on_commit_callbacks
        )
        [nudge] = HarnessNudge.no_workspace_objects.filter(job=environment)
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            state="running"
        )
        response = _answer(auth_client, environment, nudge, "yes")
        assert (response.status_code, response.json()["error"]) == (
            409,
            "environment_building",
        )
        HarnessNudge.no_workspace_objects.filter(id=nudge.id).update(
            state="answered_no"
        )
        response = _answer(auth_client, environment, nudge, "yes")
        assert response.json()["error"] == "nudge_closed"
        assert response.json()["state"] == "answered_no"

    def test_another_target_running_refuses_and_the_same_target_replaces(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        environment, nudge, _a1, _a2 = _queued(
            user,
            workspace,
            storage,
            "r-cross",
            auth_client,
            django_capture_on_commit_callbacks,
        )
        suite = HarnessNudge.no_workspace_objects.create(
            job=environment,
            organization_id=environment.organization_id,
            target="suite",
            state="pending",
            base_environment_version_id=environment.environment_version_id,
        )
        response = _answer(auth_client, environment, suite, "yes")
        assert (response.status_code, response.json()["error"]) == (
            409,
            "rebuild_running",
        )
        newer = HarnessNudge.no_workspace_objects.create(
            job=environment,
            organization_id=environment.organization_id,
            target="environment",
            state="pending",
            facts=nudge.facts,
            trigger_agent_version_id=nudge.trigger_agent_version_id,
            base_agent_version_id=nudge.base_agent_version_id,
        )

        response = _answer(auth_client, environment, newer, "yes")

        assert response.status_code == 200, response.content
        nudge.refresh_from_db()
        assert nudge.state == "replaced"
        assert nudge.error == {"code": "stopped_by_newer", "by": str(newer.id)}
        assert nudge.closed_at is not None

    def test_nothing_to_change_closes_at_once(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        environment, _a1, a2 = _activated(
            user, workspace, storage, "r-empty", django_capture_on_commit_callbacks
        )
        [nudge] = HarnessNudge.no_workspace_objects.filter(job=environment)
        empty = nudges._no_facts({"kind": "github", "changed": False})
        with patch.object(nudges, "nudge_facts", return_value=empty):
            response = _answer(auth_client, environment, nudge, "yes")

        assert response.json()["state"] == "no_change"
        nudge.refresh_from_db()
        assert nudge.summary["agent_default_moved"] is True
        assert "already fits" in nudge.summary["line"]
        assert not HostedHarnessConversationMessage.no_workspace_objects.filter(
            payload__command_kind="nudge"
        ).exists()
        environment.refresh_from_db()
        assert environment.environment_version.agent_version_id == a2.id

    def test_a_stale_draft_is_set_aside(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        environment, _a1, _a2 = _activated(
            user, workspace, storage, "r-aside", django_capture_on_commit_callbacks
        )
        [nudge] = HarnessNudge.no_workspace_objects.filter(job=environment)
        default = environment.environment_version
        stale = HarnessEnvironmentVersion.no_workspace_objects.create(
            job=environment,
            organization_id=environment.organization_id,
            status="draft",
            origin="chat",
            hash="sha256:" + "1" * 64,
            archive_object_key=default.archive_object_key,
            archive_size=1,
            parent=None,
            revision=1,
        )

        assert _answer(auth_client, environment, nudge, "yes").status_code == 200

        stale.refresh_from_db()
        assert (stale.status, stale.discard_reason, stale.nudge_id) == (
            "discarded",
            "set_aside_by_rebuild",
            nudge.id,
        )
        nudge.refresh_from_db()
        assert nudge.summary["set_aside"]["draft_version_id"] == str(stale.id)
        listed = auth_client.get(f"{BASE}/{environment.id}/drafts/").json()
        assert [one["id"] for one in listed["set_aside"]] == [str(stale.id)]


class TestStop:
    def test_stop_ends_a_queued_rebuild_and_rebuild_again_runs_a_new_attempt(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        inline,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        environment, nudge, _a1, _a2 = _queued(
            user,
            workspace,
            storage,
            "r-stop",
            auth_client,
            django_capture_on_commit_callbacks,
        )
        assert rebuild.dispatch_nudge(environment.id, start_runtime=False)
        nudge.refresh_from_db()
        first = nudge.command_message

        with django_capture_on_commit_callbacks(execute=True):
            response = auth_client.post(
                f"{BASE}/{environment.id}/nudges/{nudge.id}/stop/"
            )

        assert response.status_code == 200, response.content
        body = response.json()
        assert (body["state"], body["error"]) == ("failed", {"code": "stopped_by_user"})
        assert body["can_stop"] is False and body["closed_at"] is not None
        first.refresh_from_db()
        assert first.state == "failed"
        drafts.require_no_rebuild(environment)
        again = auth_client.post(f"{BASE}/{environment.id}/nudges/{nudge.id}/stop/")
        assert (again.status_code, again.json()["error"]) == (409, "nudge_closed")

        assert _answer(auth_client, environment, nudge, "yes").status_code == 200
        nudge.refresh_from_db()
        assert (nudge.state, nudge.command_message_id, nudge.error) == (
            "queued",
            None,
            None,
        )
        assert rebuild.dispatch_nudge(environment.id, start_runtime=False)
        nudge.refresh_from_db()
        assert nudge.command_message_id != first.id

    def test_stopping_a_running_turn_restores_the_draft_and_frees_the_chat(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        inline,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        environment, nudge, _a1, _a2 = _queued(
            user,
            workspace,
            storage,
            "r-stop-running",
            auth_client,
            django_capture_on_commit_callbacks,
        )
        assert rebuild.dispatch_nudge(environment.id, start_runtime=False)
        nudge.refresh_from_db()
        conversation = nudge.command_message.conversation
        issue_conversation_capability(
            conversation,
            endpoint_base_url="https://platform.example",
            provider_ref="sandbox-nudge",
            attempt=None,
            ttl_seconds=600,
        )
        rebuild.start_nudge_turn(
            nudge.id, message_id=nudge.command_message_id, started_at=None
        )
        HostedHarnessConversation.no_workspace_objects.filter(
            id=conversation.id
        ).update(state="responding", active_invocation_id="turn-1")
        user_message = enqueue_message(
            environment, content="later", client_request_id="later"
        )[1]

        with django_capture_on_commit_callbacks(execute=True):
            rebuild.stop_rebuild(environment, nudge.id, user=user)

        nudge.refresh_from_db()
        assert nudge.state == "failed"
        assert nudge.usage["attempts"][0]["stopped"] is True
        conversation.refresh_from_db()
        # Waiting for the runtime of the message typed meanwhile.
        assert (conversation.state, conversation.active_invocation_id) == (
            "starting",
            None,
        )
        lease = HostedHarnessConversationLease.no_workspace_objects.get(
            conversation=conversation
        )
        assert lease.state == "expired"
        assert _names(inline, "stop_harness_nudge_sandbox") == [
            [str(conversation.id), "sandbox-nudge"]
        ]
        # The message typed during the rebuild gets a runtime.
        user_message.refresh_from_db()
        assert user_message.state == "queued"
        assert _names(inline, "ensure_hosted_harness_conversation_runtime")


class TestDispatch:
    def test_dispatch_waits_for_an_idle_chat_and_sends_the_briefing(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        environment, nudge, _a1, _a2 = _queued(
            user,
            workspace,
            storage,
            "r-dispatch",
            auth_client,
            django_capture_on_commit_callbacks,
        )
        conversation, waiting, _ = enqueue_message(
            environment, content="hi", client_request_id="hi"
        )
        assert not rebuild.dispatch_nudge(environment.id, start_runtime=False)
        waiting.state = "completed"
        waiting.save(update_fields=["state"])
        HostedHarnessConversation.no_workspace_objects.filter(
            id=conversation.id
        ).update(command_acked_through=waiting.sequence, state="warm_idle")
        issue_conversation_capability(
            conversation,
            endpoint_base_url="https://platform.example",
            provider_ref="sandbox-chat",
            attempt=None,
            ttl_seconds=600,
        )

        from simulate.services import hosted_harness_conversation as service

        with patch.object(
            service, "enqueue_message", wraps=service.enqueue_message
        ) as enqueue:
            assert rebuild.dispatch_nudge(environment.id, start_runtime=False)
        # Read before the locks, not inside them.
        assert enqueue.call_args.kwargs["expected_revision"] is not None

        nudge.refresh_from_db()
        message = nudge.command_message
        assert message.role == "user"
        assert message.payload["command_kind"] == "nudge"
        assert message.client_request_id == (
            f"nudge-{nudge.id}-{int(nudge.answered_at.timestamp() * 1_000_000)}"
        )
        assert set(message.payload) - {"_environment_revision", "_author_id"} == {
            "command_kind",
            "nudge_id",
            "stage",
            "trigger_label",
            "base_labels",
            "user_draft_keys",
        }
        assert "OPENAI_API_KEY" in message.content
        assert NEW_VALUE not in message.content
        conversation.refresh_from_db()
        [shown] = [
            one
            for one in serialize_conversation(conversation)["messages"]
            if one["message_id"] == str(message.id)
        ]
        assert shown["content"] == ""
        [command] = pending_commands(conversation, after=waiting.sequence)["commands"]
        assert command["kind"] == "nudge"
        assert command["payload"]["content"] == message.content
        assert command["payload"]["stage"] == "environment"
        assert (
            HostedHarnessConversationLease.no_workspace_objects.get(
                conversation=conversation
            ).state
            == "expired"
        )
        assert rebuild.nudge_composition(environment, conversation) == "environment"
        assert not rebuild.dispatch_nudge(environment.id, start_runtime=False)

    def test_a_busy_chat_is_waited_for_without_reading_facts_or_checkpoints(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        environment, nudge, _a1, _a2 = _queued(
            user,
            workspace,
            storage,
            "r-dispatch-busy",
            auth_client,
            django_capture_on_commit_callbacks,
        )
        conversation = ensure_conversation(environment)
        HostedHarnessConversation.no_workspace_objects.filter(
            id=conversation.id
        ).update(state="responding")

        with (
            patch.object(nudges, "nudge_facts") as facts,
            patch.object(rebuild, "_promote_finished_turn") as promote,
        ):
            assert not rebuild.dispatch_nudge(environment.id, start_runtime=False)

        assert (facts.call_count, promote.call_count) == (0, 0)
        nudge.refresh_from_db()
        assert (nudge.state, nudge.command_message_id) == ("queued", None)

    def test_a_client_cannot_send_a_rebuild_turn(self, user, auth_client):
        from simulate.serializers.hosted_harness_conversation import (
            HarnessConversationMessageCreateSerializer,
        )

        serializer = HarnessConversationMessageCreateSerializer(
            data={
                "content": "x",
                "client_request_id": "nudge-x",
                "kind": "user_message",
            }
        )
        assert not serializer.is_valid()


class TestLanding:
    def test_a_rebuild_lands_a_new_environment_version(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        environment, nudge, _a1, a2 = _queued(
            user,
            workspace,
            storage,
            "r-land",
            auth_client,
            django_capture_on_commit_callbacks,
        )
        e1 = environment.environment_version

        with django_capture_on_commit_callbacks(execute=True):
            nudge = _turn(environment, nudge, storage)

        assert nudge.state == "applied", (nudge.error, nudge.summary)
        e2 = environment.environment_version
        assert e2.id != e1.id and nudge.result_environment_version_id == e2.id
        assert (e2.origin, e2.nudge_id, e2.agent_version_id) == (
            "nudge",
            nudge.id,
            a2.id,
        )
        assert e2.includes_draft_edits is False
        assert nudge.summary["update"]["contract"]["tools"]["added"] == ["refund_order"]
        assert nudge.summary["draft_edits"] is None
        assert nudge.summary["agent_default_moved"] is True
        assert nudge.closed_at is not None
        assert drafts.open_draft(environment, "environment") is None
        assert nudge.usage["attempts"][0]["command_message_id"] == str(
            nudge.command_message_id
        )
        drafts.require_no_rebuild(environment)
        [suite] = HarnessNudge.no_workspace_objects.filter(
            job=environment, target="suite"
        )
        assert suite.base_environment_version_id == e2.id

    def test_the_rebuild_lands_on_top_of_the_unsaved_edits(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        settings,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        environment, nudge, _a1, _a2 = _queued(
            user,
            workspace,
            storage,
            "r-land-draft",
            auth_client,
            django_capture_on_commit_callbacks,
        )
        from simulate.services.hosted_harness_gateway import with_authoring_basis

        conversation = ensure_conversation(environment)
        token = drafts.compose_workspace(environment, conversation).token
        from simulate.services.hosted_harness_gateway import with_draft_basis

        mine = {"agent": "support", "tools": [{"name": "lookup_order"}]}
        drafts.record_draft(
            environment,
            with_draft_basis(
                with_authoring_basis(_authoring(contract=mine), ""), token
            ),
            origin="chat",
            user=user,
        )
        both = {
            "agent": "support",
            "tools": [{"name": "lookup_order"}, REFUND["tools"][0]],
        }

        world = {"tools": ["lookup_order", "refund_order"], "tables": {"orders": 1}}

        with django_capture_on_commit_callbacks(execute=True):
            nudge = _turn(
                environment,
                nudge,
                storage,
                contract=both,
                **_saved_world(world),
            )

        assert nudge.state == "applied", (nudge.error, nudge.summary)
        # The resealed bundle holds the handlers as built: the new pair certifies.
        assert environment.payload["metadata"]["default_pair_sealed"] is False
        e2 = environment.environment_version
        assert e2.includes_draft_edits is True
        assert e2.snapshot_diff["contract"]["tools"]["added"] == ["lookup_order"]
        assert nudge.summary["update"]["contract"]["tools"]["added"] == ["refund_order"]
        assert nudge.summary["draft_edits"]["contract"]["tools"]["added"] == [
            "lookup_order"
        ]
        diff = auth_client.get(
            f"{BASE}/{environment.id}/versions/environment/{e2.id}/diff/"
        ).json()
        assert diff["includes_draft_edits"] is True
        assert diff["snapshot_diff"]["contract"]["tools"]["added"] == ["lookup_order"]

    def test_no_report_fails_and_puts_the_draft_back(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        environment, nudge, _a1, _a2 = _queued(
            user,
            workspace,
            storage,
            "r-no-report",
            auth_client,
            django_capture_on_commit_callbacks,
        )
        e1 = environment.environment_version_id

        nudge = _turn(environment, nudge, storage, result=None)

        assert (nudge.state, nudge.error) == ("failed", {"code": "no_report"})
        assert environment.environment_version_id == e1
        assert drafts.open_draft(environment, "environment") is None
        assert (
            HarnessEnvironmentVersion.all_objects.filter(
                job=environment, nudge=nudge
            ).values_list("status", "discard_reason")
        ).first() == ("discarded", "rebuild_restored")

    def test_failed_checks_name_problems_in_the_unsaved_edits(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        environment, nudge, _a1, _a2 = _queued(
            user,
            workspace,
            storage,
            "r-checks",
            auth_client,
            django_capture_on_commit_callbacks,
        )
        result = _result(
            nudge,
            passed=False,
            problems=[
                {"item": "refund_order", "kind": "contract", "message": "bad"},
                {"item": "other", "kind": "handler", "message": NEW_VALUE},
                {"item": f"key {NEW_VALUE}", "kind": NEW_VALUE, "message": "m"},
            ],
        )

        nudge = _turn(environment, nudge, storage, result=result)

        assert nudge.state == "failed"
        assert nudge.error["code"] == "checks_failed"
        assert [one["item"] for one in nudge.error["problems"]][:2] == [
            "refund_order",
            "other",
        ]
        assert NEW_VALUE not in json.dumps(nudge.error)
        assert all(not one["in_your_unsaved_edits"] for one in nudge.error["problems"])
        body = auth_client.get(f"{BASE}/{environment.id}/nudges/").json()[0]
        assert body["error"]["problems"][0]["kind"] == "contract"

    def test_an_added_tool_the_world_was_not_saved_with_fails(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        environment, nudge, _a1, _a2 = _queued(
            user,
            workspace,
            storage,
            "r-unsaved-world",
            auth_client,
            django_capture_on_commit_callbacks,
        )
        e1 = environment.environment_version_id
        world = {"tools": [], "tables": {"orders": 1}}

        with django_capture_on_commit_callbacks(execute=True):
            nudge = _turn(
                environment, nudge, storage, **{"manifest.json": json.dumps(world)}
            )

        assert nudge.state == "failed", (nudge.error, nudge.summary)
        assert nudge.error["code"] == "checks_failed"
        assert [
            (one["item"], one["kind"], one["in_your_unsaved_edits"])
            for one in nudge.error["problems"]
        ] == [("refund_order", "world", False)]
        assert environment.environment_version_id == e1
        assert nudge.result_environment_version_id is None
        assert not HarnessEnvironmentVersion.all_objects.filter(
            job=environment, nudge=nudge, status="active"
        ).exists()
        drafts.require_no_rebuild(environment)
        assert not HarnessNudge.no_workspace_objects.filter(
            job=environment, target="suite"
        ).exists()

    @pytest.mark.parametrize(
        "world",
        [
            {"tools": [], "runtime_tools": ["refund_order"], "tables": {"orders": 1}},
            {"tools": ["refund_order"], "tables": {"orders": 1}},
            {"tools": [], "tables": {}},
        ],
        ids=["runtime-tool", "handler", "data-free"],
    )
    def test_an_added_tool_the_world_covers_lands(
        self,
        world,
        user,
        workspace,
        storage,
        scheduled,
        started,
        settings,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        environment, nudge, _a1, _a2 = _queued(
            user,
            workspace,
            storage,
            "r-saved-world",
            auth_client,
            django_capture_on_commit_callbacks,
        )

        with django_capture_on_commit_callbacks(execute=True):
            # ALK leaves a tool-free contract's first tool to a build: unstamped.
            nudge = _turn(
                environment, nudge, storage, compiled=False, **_saved_world(world)
            )

        assert nudge.state == "applied", (nudge.error, nudge.summary)
        # The edit is not compiled into the resealed bundle, so that pair certifies.
        assert environment.payload["metadata"]["default_pair_sealed"] is False

    @pytest.mark.parametrize(
        ("base", "contract", "sub_goals", "state"),
        [
            # Naming the tool is the model's to judge, not a gate: ALK only warns.
            ([], REFUND, None, "applied"),
            ([], REFUND, CHECKED, "applied"),
            (
                REFUND["tools"],
                {"agent": "support", "tools": [{"name": "issue_refund"}]},
                CHECKED.replace("refund_order", "issue_refund"),
                "applied",
            ),
            (REFUND["tools"], {"agent": "support", "tools": []}, CHECKED, "applied"),
        ],
        ids=["unchecked", "checked", "renamed", "removed"],
    )
    def test_a_tool_the_rebuild_added_lands_whether_a_sub_goal_names_it(
        self,
        base,
        contract,
        sub_goals,
        state,
        user,
        workspace,
        storage,
        scheduled,
        started,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        environment, nudge, _a1, _a2 = _queued(
            user,
            workspace,
            storage,
            f"r-untested-{state}-{len(base)}",
            auth_client,
            django_capture_on_commit_callbacks,
        )
        e1 = environment.environment_version
        HarnessEnvironmentVersion.all_objects.filter(id=e1.id).update(
            documents={
                **(e1.documents or {}),
                "contract": {"agent": "support", "tools": base},
            }
        )
        with django_capture_on_commit_callbacks(execute=True):
            nudge = _turn(
                environment,
                nudge,
                storage,
                contract=contract,
                **{"sub_goals.json": sub_goals or UNCHECKED},
            )

        assert nudge.state == state, (nudge.error, nudge.summary)

    def test_changes_reported_but_never_saved_fail(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        environment, nudge, a1, a2 = _queued(
            user,
            workspace,
            storage,
            "r-not-saved",
            auth_client,
            django_capture_on_commit_callbacks,
        )
        e1 = environment.environment_version
        built_with = e1.agent_version_id

        with django_capture_on_commit_callbacks(execute=True):
            nudge = _turn(environment, nudge, storage, contract=None)

        assert nudge.state == "failed", (nudge.error, nudge.summary)
        assert nudge.error["code"] == "checks_failed"
        assert [
            (one["item"], one["kind"], one["in_your_unsaved_edits"])
            for one in nudge.error["problems"]
        ] == [("world", "world", False)]
        e1.refresh_from_db()
        assert environment.environment_version_id == e1.id
        assert e1.agent_version_id == built_with != a2.id
        assert environment.agent_version_id == a2.id

    def test_an_inherited_tool_the_world_does_not_cover_fails_without_changes(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        environment, nudge, _a1, _a2 = _queued(
            user,
            workspace,
            storage,
            "r-inherited-gap",
            auth_client,
            django_capture_on_commit_callbacks,
        )
        e1 = environment.environment_version
        HarnessEnvironmentVersion.all_objects.filter(id=e1.id).update(
            documents={**(e1.documents or {}), "contract": REFUND}
        )
        world = {"tools": [], "tables": {"orders": 1}}
        files = drafts._piece_files

        def with_world(locked, piece, version):
            found = files(locked, piece, version)
            if piece == "environment":
                found = {**found, "manifest.json": json.dumps(world).encode()}
            return found

        with patch.object(drafts, "_piece_files", with_world):
            nudge = _turn(
                environment,
                nudge,
                storage,
                contract=None,
                result=_result(nudge, changed=False),
            )

        assert nudge.state == "failed", (nudge.error, nudge.summary)
        assert nudge.error["code"] == "checks_failed"
        assert [(one["item"], one["kind"]) for one in nudge.error["problems"]] == [
            ("refund_order", "world")
        ]
        assert environment.environment_version_id == e1.id

    def test_rebuild_again_briefs_that_the_earlier_attempt_was_dropped(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        environment, nudge, _a1, _a2 = _queued(
            user,
            workspace,
            storage,
            "r-again-brief",
            auth_client,
            django_capture_on_commit_callbacks,
        )
        line = "An earlier attempt at this update ended without landing"
        assert line not in rebuild.briefing(environment, nudge)

        nudge.usage = {"attempts": [{"command_message_id": "m1"}]}

        assert line in rebuild.briefing(environment, nudge)

    def test_nothing_changed_keeps_the_version_and_records_it_fits(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        environment, nudge, _a1, a2 = _queued(
            user,
            workspace,
            storage,
            "r-unchanged",
            auth_client,
            django_capture_on_commit_callbacks,
        )
        e1 = environment.environment_version_id

        nudge = _turn(environment, nudge, storage, result=_result(nudge, changed=False))

        assert nudge.state == "no_change", nudge.error
        assert environment.environment_version_id == e1
        assert "already fits" in nudge.summary["line"]
        assert environment.environment_version.agent_version_id == a2.id
        assert drafts.open_draft(environment, "environment") is None

    def test_notes_are_redacted_and_never_carry_a_value(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        auth_client,
        caplog,
        django_capture_on_commit_callbacks,
    ):
        caplog.set_level(logging.INFO)
        environment, nudge, _a1, _a2 = _queued(
            user,
            workspace,
            storage,
            "r-secret",
            auth_client,
            django_capture_on_commit_callbacks,
        )

        nudge = _turn(
            environment,
            nudge,
            storage,
            result=_result(
                nudge,
                notes=f"used {NEW_VALUE} to call",
                warnings=[f"saw {NEW_VALUE}"],
                not_checked=[f"OPENAI_API_KEY={NEW_VALUE}", "k" * 600],
                left_out=[{"key": NEW_VALUE, "why": "w"}],
            ),
        )

        assert nudge.state == "applied", nudge.error
        assert len(nudge.summary["not_checked"][1]) == rebuild.PROBLEM_TEXT_MAX
        rows = json.dumps(
            [
                nudge.facts,
                nudge.summary,
                nudge.error,
                nudge.usage,
                nudge.draft_snapshot,
            ],
            default=str,
        )
        assert NEW_VALUE not in rows
        assert NEW_VALUE not in nudge.command_message.content
        assert NEW_VALUE not in caplog.text
        body = json.dumps(auth_client.get(f"{BASE}/{environment.id}/nudges/").json())
        assert NEW_VALUE not in body

    def test_a_write_after_the_rebuild_closed_is_dropped(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        environment, nudge, _a1, _a2 = _queued(
            user,
            workspace,
            storage,
            "r-late",
            auth_client,
            django_capture_on_commit_callbacks,
        )
        assert rebuild.dispatch_nudge(environment.id, start_runtime=False)
        nudge.refresh_from_db()
        message = nudge.command_message
        rebuild.stop_rebuild(environment, nudge.id, user=user)
        conversation = message.conversation
        key, digest = _put_conversation_checkpoint(
            conversation, storage, _authoring(contract=REFUND)
        )

        promote_conversation_checkpoint(
            conversation,
            object_key=key,
            digest=digest,
            sequence=10,
            expected_revision=message.payload["_environment_revision"],
            command=message,
        )

        assert drafts.open_draft(environment, "environment") is None

    def test_a_stopped_attempt_never_lands_after_rebuild_again(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        environment, nudge, _a1, _a2 = _queued(
            user,
            workspace,
            storage,
            "r-again-late",
            auth_client,
            django_capture_on_commit_callbacks,
        )
        assert rebuild.dispatch_nudge(environment.id, start_runtime=False)
        nudge.refresh_from_db()
        first = nudge.command_message
        rebuild.stop_rebuild(environment, nudge.id, user=user)
        assert _answer(auth_client, environment, nudge, "yes").status_code == 200
        conversation = first.conversation
        key, digest = _put_conversation_checkpoint(
            conversation, storage, _authoring(contract=REFUND)
        )

        # The first attempt's turn is published only now, as Rebuild again does.
        promote_conversation_checkpoint(
            conversation,
            object_key=key,
            digest=digest,
            sequence=10,
            expected_revision=first.payload["_environment_revision"],
            command=first,
        )

        assert drafts.open_draft(environment, "environment") is None

    def test_a_run_chat_edit_during_a_rebuild_is_kept_when_it_ends(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        inline,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        environment, nudge, _a1, _a2 = _queued(
            user,
            workspace,
            storage,
            "r-foreign",
            auth_client,
            django_capture_on_commit_callbacks,
        )
        assert rebuild.dispatch_nudge(environment.id, start_runtime=False)
        nudge.refresh_from_db()
        rebuild.start_nudge_turn(
            nudge.id, message_id=nudge.command_message_id, started_at=None
        )
        mine = {"agent": "support", "tools": [{"name": "lookup_order"}]}
        from simulate.services.hosted_harness_gateway import with_authoring_basis

        written = drafts.record_draft(
            environment,
            with_authoring_basis(_authoring(contract=mine), ""),
            origin="chat",
            user=user,
        )

        with django_capture_on_commit_callbacks(execute=True):
            rebuild.stop_rebuild(environment, nudge.id, user=user)

        draft = drafts.open_draft(environment, "environment")
        assert draft is not None and draft.id == written.environment.id
        assert draft.origin == "chat"

        # Rebuild again, on top of that draft: an update to it is kept too.
        assert _answer(auth_client, environment, nudge, "yes").status_code == 200
        assert rebuild.dispatch_nudge(environment.id, start_runtime=False)
        nudge.refresh_from_db()
        rebuild.start_nudge_turn(
            nudge.id, message_id=nudge.command_message_id, started_at=None
        )
        more = {"agent": "support", "tools": [*mine["tools"], {"name": "cancel"}]}
        updated = drafts.record_draft(
            environment,
            with_authoring_basis(_authoring(contract=more), ""),
            origin="chat",
            user=user,
        ).environment

        rebuild.stop_rebuild(environment, nudge.id, user=user)

        draft.refresh_from_db()
        assert (draft.status, draft.revision, draft.hash) == (
            "draft",
            updated.revision,
            updated.hash,
        )

    @pytest.mark.parametrize("ending", ["no_change", "failed", "stopped"])
    def test_unsaved_edits_the_rebuild_reverted_come_back_when_it_ends(
        self,
        ending,
        user,
        workspace,
        storage,
        scheduled,
        started,
        inline,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        environment, nudge, _a1, _a2 = _queued(
            user,
            workspace,
            storage,
            f"r-reverted-{ending}",
            auth_client,
            django_capture_on_commit_callbacks,
        )
        from simulate.services.hosted_harness_gateway import with_authoring_basis

        mine = {"agent": "support", "tools": [{"name": "lookup_order"}]}
        edits = drafts.record_draft(
            environment,
            with_authoring_basis(_authoring(contract=mine), ""),
            origin="chat",
            user=user,
        ).environment

        # The rebuild writes the default back, which discards the unsaved edits.
        if ending == "stopped":
            assert rebuild.dispatch_nudge(environment.id, start_runtime=False)
            nudge.refresh_from_db()
            rebuild.start_nudge_turn(
                nudge.id, message_id=nudge.command_message_id, started_at=None
            )
            drafts.record_draft(
                environment,
                with_authoring_basis(_authoring(), ""),
                origin="nudge",
                nudge=nudge,
            )
            assert drafts.open_draft(environment, "environment") is None
            rebuild.stop_rebuild(environment, nudge.id, user=user)
        else:
            nudge = _turn(
                environment,
                nudge,
                storage,
                contract=None,
                result=None if ending == "failed" else _result(nudge, changed=False),
            )
        nudge.refresh_from_db()

        assert nudge.state == ("no_change" if ending == "no_change" else "failed")
        draft = drafts.open_draft(environment, "environment")
        assert draft is not None and draft.id == edits.id
        assert (draft.origin, draft.hash) == ("chat", edits.hash)
        assert draft.revision > edits.revision

    def test_a_run_chat_edit_after_the_rebuild_reverted_the_edits_is_kept(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        inline,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        environment, nudge, _a1, _a2 = _queued(
            user,
            workspace,
            storage,
            "r-reverted-then-chat",
            auth_client,
            django_capture_on_commit_callbacks,
        )
        from simulate.services.hosted_harness_gateway import with_authoring_basis

        def write(contract=None, **kwargs):
            return drafts.record_draft(
                environment,
                with_authoring_basis(_authoring(contract=contract), ""),
                **kwargs,
            )

        write({"agent": "support", "tools": [{"name": "lookup_order"}]}, origin="chat")
        assert rebuild.dispatch_nudge(environment.id, start_runtime=False)
        nudge.refresh_from_db()
        rebuild.start_nudge_turn(
            nudge.id, message_id=nudge.command_message_id, started_at=None
        )
        write(origin="nudge", nudge=nudge)
        later = write(REFUND, origin="chat", user=user).environment

        rebuild.stop_rebuild(environment, nudge.id, user=user)

        draft = drafts.open_draft(environment, "environment")
        assert draft is not None and draft.id == later.id

    def test_a_restored_draft_keeps_its_origin_and_takes_a_new_revision(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        inline,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        environment, nudge, _a1, _a2 = _queued(
            user,
            workspace,
            storage,
            "r-restore-origin",
            auth_client,
            django_capture_on_commit_callbacks,
        )
        from simulate.services.hosted_harness_gateway import with_authoring_basis

        def write(contract, **kwargs):
            return drafts.record_draft(
                environment,
                with_authoring_basis(_authoring(contract=contract), ""),
                **kwargs,
            ).environment

        mine = {"agent": "support", "tools": [{"name": "lookup_order"}]}
        edits = write(mine, origin="chat", user=user)
        assert rebuild.dispatch_nudge(environment.id, start_runtime=False)
        nudge.refresh_from_db()
        rebuild.start_nudge_turn(
            nudge.id, message_id=nudge.command_message_id, started_at=None
        )
        rebuilt = write(REFUND, origin="nudge", nudge=nudge)
        assert (rebuilt.id, rebuilt.origin) == (edits.id, "nudge")
        # A run's chat composes over the rebuild's write.
        stale = drafts.compose_workspace(
            environment, ensure_conversation(environment)
        ).refs

        with django_capture_on_commit_callbacks(execute=True):
            rebuild.stop_rebuild(environment, nudge.id, user=user)

        draft = drafts.open_draft(environment, "environment")
        assert (draft.id, draft.origin, draft.hash) == (edits.id, "chat", edits.hash)
        assert draft.revision > rebuilt.revision
        write(
            {"agent": "support", "tools": [{"name": "cancel"}]},
            origin="chat",
            user=user,
        )
        with pytest.raises(HostedHarnessError) as refused:
            write(REFUND, expected=stale, origin="chat", user=user)
        assert refused.value.code == "draft_changed"

    def _started(self, user, workspace, storage, key, auth_client, capture):
        environment, nudge, _a1, _a2 = _queued(
            user, workspace, storage, key, auth_client, capture
        )
        assert rebuild.dispatch_nudge(environment.id, start_runtime=False)
        nudge.refresh_from_db()
        message = nudge.command_message
        rebuild.start_nudge_turn(nudge.id, message_id=message.id, started_at=None)
        return environment, nudge, message

    def _rebuild_checkpoint(self, environment, nudge, message, storage, contract):
        from simulate.services.hosted_harness_gateway import (
            with_authoring_basis,
            with_draft_basis,
        )

        token = drafts.compose_workspace(
            environment, message.conversation, only_draft=nudge.target
        ).token
        files = {".futureagi-nudge-result.json": _result(nudge)}
        if contract is not None:
            files["sub_goals.json"] = CHECKED
        files["environment-bundle/manifest.json"] = _rebuilt(
            nudge, contract=contract, **files
        )
        body = with_draft_basis(
            with_authoring_basis(
                _authoring(contract=contract, **files),
                message.payload["_environment_revision"],
            ),
            token,
        )
        return _put_conversation_checkpoint(message.conversation, storage, body)

    def _run_chat_saves(self, environment, user):
        """A run's chat saves over the rebuild's result, with content that passes
        every landing check: only a handler differs."""
        from simulate.services.hosted_harness_gateway import with_authoring_basis

        return drafts.record_draft(
            environment,
            with_authoring_basis(
                _authoring(
                    contract=REFUND,
                    **{
                        "sub_goals.json": CHECKED,
                        "handlers/lookup.py": "def handle(db):\n    return {'chat': 1}\n",
                    },
                ),
                "",
            ),
            origin="chat",
            user=user,
            expected=drafts.draft_refs(environment),
        ).environment

    def _not_landed(self, environment, nudge, before, chat):
        nudge.refresh_from_db()
        environment.refresh_from_db()
        assert (nudge.state, nudge.error) == ("failed", {"code": "draft_changed"})
        assert environment.environment_version_id == before
        assert not HarnessEnvironmentVersion.no_workspace_objects.filter(
            nudge=nudge, status="active"
        ).exists()
        draft = drafts.open_draft(environment, "environment")
        assert (draft.id, draft.origin, draft.hash) == (chat.id, "chat", chat.hash)

    # None: the rebuild's save equals the default, so it leaves no draft.
    @pytest.mark.parametrize("rebuilt", [REFUND, None], ids=["edit", "unchanged"])
    def test_a_run_chat_save_before_the_landing_is_not_landed(
        self,
        rebuilt,
        user,
        workspace,
        storage,
        scheduled,
        started,
        inline,
        settings,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        # Certified, so whichever draft is there could go live.
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        environment, nudge, message = self._started(
            user,
            workspace,
            storage,
            f"r-land-chat-{bool(rebuilt)}",
            auth_client,
            django_capture_on_commit_callbacks,
        )
        before = environment.environment_version_id
        key, digest = self._rebuild_checkpoint(
            environment, nudge, message, storage, rebuilt
        )
        promote_conversation_checkpoint(
            message.conversation,
            object_key=key,
            digest=digest,
            sequence=10,
            expected_revision=message.payload["_environment_revision"],
            command=message,
        )
        # Without an edit, the rebuild's save leaves no draft of its own.
        rebuild_draft = drafts.open_draft(environment, "environment")
        assert (rebuild_draft is None) == (rebuilt is None)
        chat = self._run_chat_saves(environment, user)

        nudge.refresh_from_db()
        rebuild.land_nudge_result(nudge, checkpoint_key=key, turn_event=None)

        self._not_landed(environment, nudge, before, chat)

    # With a draft: the rebuild started on the user's draft and wrote over it.
    @pytest.mark.parametrize("with_draft", [False, True], ids=["default", "draft"])
    def test_a_run_chat_save_of_the_default_before_the_landing_is_not_landed(
        self,
        with_draft,
        user,
        workspace,
        storage,
        scheduled,
        started,
        inline,
        settings,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        from simulate.services.hosted_harness_gateway import with_authoring_basis

        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        environment, nudge, _a1, _a2 = _queued(
            user,
            workspace,
            storage,
            f"r-land-chat-default-{with_draft}",
            auth_client,
            django_capture_on_commit_callbacks,
        )
        if with_draft:
            mine = {"agent": "support", "tools": [{"name": "lookup_order"}]}
            drafts.record_draft(
                environment,
                with_authoring_basis(_authoring(contract=mine), ""),
                origin="chat",
                user=user,
            )
        assert rebuild.dispatch_nudge(environment.id, start_runtime=False)
        nudge.refresh_from_db()
        message = nudge.command_message
        rebuild.start_nudge_turn(nudge.id, message_id=message.id, started_at=None)
        nudge.refresh_from_db()
        assert bool(nudge.draft_snapshot["piece"]["draft_version_id"]) is with_draft
        before = environment.environment_version_id
        key, digest = self._rebuild_checkpoint(
            environment, nudge, message, storage, REFUND
        )
        promote_conversation_checkpoint(
            message.conversation,
            object_key=key,
            digest=digest,
            sequence=10,
            expected_revision=message.payload["_environment_revision"],
            command=message,
        )
        assert drafts.open_draft(environment, "environment") is not None
        # The run's chat puts the default back, which discards the rebuild's draft.
        default = environment.payload["metadata"]["authoring_object_key"]
        drafts.record_draft(
            environment,
            with_authoring_basis(archives.get_archive(default), ""),
            origin="chat",
            user=user,
            expected=drafts.draft_refs(environment),
            pieces={"environment"},
        )
        assert drafts.open_draft(environment, "environment") is None

        nudge.refresh_from_db()
        rebuild.land_nudge_result(nudge, checkpoint_key=key, turn_event=None)

        nudge.refresh_from_db()
        environment.refresh_from_db()
        assert (nudge.state, nudge.error) == ("failed", {"code": "draft_changed"})
        assert environment.environment_version_id == before
        assert drafts.open_draft(environment, "environment") is None

    def test_a_run_chat_save_before_a_landing_retry_is_not_landed(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        inline,
        settings,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        from simulate.models import HostedHarnessConversationEvent
        from simulate.services.hosted_harness_conversation import (
            promote_turn_checkpoint,
        )

        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        environment, nudge, message = self._started(
            user,
            workspace,
            storage,
            "r-land-chat-retry",
            auth_client,
            django_capture_on_commit_callbacks,
        )
        before = environment.environment_version_id
        conversation = message.conversation
        _key, digest = self._rebuild_checkpoint(
            environment, nudge, message, storage, REFUND
        )
        now = timezone.now()
        for sequence, kind, payload in (
            (10, "checkpoint_committed", {"digest": digest}),
            (
                11,
                "turn_completed",
                {"command_message_id": str(message.id), "outcome": "success"},
            ),
        ):
            HostedHarnessConversationEvent.no_workspace_objects.create(
                event_id=f"r-land-chat-retry-{sequence}",
                conversation=conversation,
                sequence=sequence,
                kind=kind,
                payload=payload,
                digest="x",
                emitted_at=now,
            )
        # The first landing: the rebuild's save commits, then reading its result fails.
        with (
            patch.object(rebuild, "_result_files", side_effect=OSError("down")),
            pytest.raises(OSError),
        ):
            promote_turn_checkpoint(str(conversation.id), 11)
        saved = drafts.open_draft(environment, "environment")
        assert saved is not None and saved.origin == "nudge"
        chat = self._run_chat_saves(environment, user)

        # The tick retries the landing later.
        nudge.refresh_from_db()
        HarnessNudge.no_workspace_objects.filter(id=nudge.id).update(
            draft_snapshot={**nudge.draft_snapshot, "turn_sequence": 11}
        )
        HostedHarnessConversationEvent.no_workspace_objects.filter(
            event_id="r-land-chat-retry-11"
        ).update(
            created_at=now - rebuild.NUDGE_LANDING_RETRY_AFTER - timedelta(minutes=1)
        )
        rebuild.check_nudges(environment.id)

        self._not_landed(environment, nudge, before, chat)

    def test_unsaved_edits_the_rebuild_leaves_as_they_are_still_land(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        from simulate.services.hosted_harness_gateway import with_authoring_basis

        environment, nudge, _a1, _a2 = _queued(
            user,
            workspace,
            storage,
            "r-land-untouched",
            auth_client,
            django_capture_on_commit_callbacks,
        )
        before = environment.environment_version_id
        mine = {"agent": "support", "tools": [{"name": "lookup_order"}]}
        edits = drafts.record_draft(
            environment,
            with_authoring_basis(
                _authoring(contract=mine, **{"sub_goals.json": CHECKED}), ""
            ),
            origin="chat",
            user=user,
        ).environment

        nudge = _turn(
            environment,
            nudge,
            storage,
            contract=mine,
            result=_result(nudge, changed=False),
        )

        assert (nudge.state, nudge.error) == ("no_change", None)
        assert environment.environment_version_id == before
        draft = drafts.open_draft(environment, "environment")
        assert (draft.id, draft.origin, draft.hash) == (edits.id, "chat", edits.hash)


def _send(client, conversation, headers, acknowledged, *events):
    signed = []
    for sequence, kind, payload in events:
        event = {
            "schema_version": "futureagi.harness-conversation-event.v1",
            "event_id": f"ce-{conversation.id}-{sequence}",
            "conversation_id": str(conversation.id),
            "sequence": sequence,
            "kind": kind,
            "message_id": None,
            "stage": "scenarios",
            "invocation_id": "turn-1",
            "function_call_id": None,
            "emitted_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "payload": payload,
        }
        event["digest"] = canonical_digest(event)
        signed.append(event)
    return client.post(
        f"{GUEST}/{conversation.id}/events/",
        {
            "schema_version": "futureagi.harness-conversation-event.v1",
            "acknowledged_through": acknowledged,
            "events": signed,
        },
        format="json",
        **headers,
    )


class TestGuestTurn:
    def test_the_guest_runs_the_turn_and_the_fence_holds_chat_until_it_lands(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        inline,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        from simulate.services.hosted_harness_gateway import (
            HostedHarnessGateway,
            with_authoring_basis,
            with_draft_basis,
        )

        environment, nudge, _a1, _a2 = _queued(
            user,
            workspace,
            storage,
            "r-guest",
            auth_client,
            django_capture_on_commit_callbacks,
        )
        assert rebuild.dispatch_nudge(environment.id, start_runtime=False)
        nudge.refresh_from_db()
        command = nudge.command_message
        conversation = command.conversation
        composed = drafts.compose_workspace(
            environment,
            conversation,
            only_draft=rebuild.nudge_composition(environment, conversation),
        )
        capability = issue_conversation_capability(
            conversation,
            endpoint_base_url="https://platform.example",
            provider_ref="sandbox-nudge",
            attempt=None,
            ttl_seconds=600,
        )
        client, headers = APIClient(), _headers(capability)
        polled = client.get(
            f"{GUEST}/{conversation.id}/commands/?after=0", **headers
        ).json()["commands"]
        assert [(one["kind"], one["message_id"]) for one in polled] == [
            ("nudge", str(command.id))
        ]
        with django_capture_on_commit_callbacks(execute=True):
            assert (
                _send(
                    client,
                    conversation,
                    headers,
                    0,
                    (1, "turn_started", {"command_message_id": str(command.id)}),
                ).status_code
                == 200
            )
        nudge.refresh_from_db()
        assert nudge.state == "running" and nudge.draft_snapshot["taken_at"]
        later = enqueue_message(
            environment, content="after", client_request_id="after"
        )[1]
        body = with_draft_basis(
            with_authoring_basis(
                _authoring(
                    contract=REFUND,
                    **{
                        ".futureagi-nudge-result.json": _result(nudge),
                        "sub_goals.json": CHECKED,
                        "environment-bundle/manifest.json": _rebuilt(
                            nudge, **{"sub_goals.json": CHECKED}
                        ),
                    },
                ),
                command.payload["_environment_revision"],
            ),
            composed.token,
        )
        from simulate.tests.test_hosted_harness_conversation import _commit_checkpoint

        checkpoint = _commit_checkpoint(client, conversation, headers, body)
        landing = []
        original = rebuild.land_nudge_result

        def land(nudge, **kwargs):
            # The fence holds before the landing: no sandbox may start.
            landing.append(
                HostedHarnessGateway.ensure_conversation_runtime(
                    None,
                    environment,
                    conversation=conversation,
                    endpoint_base_url="https://platform.example",
                )
            )
            return original(nudge, **kwargs)

        with (
            patch.object(rebuild, "land_nudge_result", side_effect=land),
            django_capture_on_commit_callbacks(execute=True),
        ):
            response = _send(
                client,
                conversation,
                headers,
                command.sequence,
                (2, "checkpoint_committed", checkpoint),
                (
                    3,
                    "turn_completed",
                    {
                        "command_message_id": str(command.id),
                        "outcome": "success",
                        "usage": {"tokens_in": 10, "tokens_out": 5, "usd": 0.01},
                    },
                ),
            )
        assert response.status_code == 200, response.content
        assert landing == [None]
        nudge.refresh_from_db()
        assert nudge.state == "applied", (nudge.error, nudge.summary)
        assert nudge.draft_snapshot["turn_sequence"] == 3
        [attempt] = nudge.usage["attempts"]
        assert (attempt["tokens_in"], attempt["tokens_out"]) == (10, 5)
        assert attempt["sandbox_seconds"] is not None
        # The old lease is gone: the message typed meanwhile waits for a new sandbox.
        assert (
            client.get(
                f"{GUEST}/{conversation.id}/commands/?after=0", **headers
            ).status_code
            != 200
        )
        later.refresh_from_db()
        assert later.state == "queued"
        assert _names(inline, "ensure_hosted_harness_conversation_runtime")
        assert not rebuild.landing_pending(environment)

    def test_a_failed_turn_fails_the_rebuild(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        inline,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        environment, nudge, _a1, _a2 = _queued(
            user,
            workspace,
            storage,
            "r-guest-failed",
            auth_client,
            django_capture_on_commit_callbacks,
        )
        assert rebuild.dispatch_nudge(environment.id, start_runtime=False)
        nudge.refresh_from_db()
        command = nudge.command_message
        conversation = command.conversation
        capability = issue_conversation_capability(
            conversation,
            endpoint_base_url="https://platform.example",
            provider_ref="sandbox-nudge",
            attempt=None,
            ttl_seconds=600,
        )
        client, headers = APIClient(), _headers(capability)
        with django_capture_on_commit_callbacks(execute=True):
            _send(
                client,
                conversation,
                headers,
                0,
                (1, "turn_started", {"command_message_id": str(command.id)}),
                (
                    2,
                    "turn_completed",
                    {"command_message_id": str(command.id), "outcome": "failed"},
                ),
            )

        nudge.refresh_from_db()
        assert (nudge.state, nudge.error) == ("failed", {"code": "turn_failed"})
        assert _names(inline, "finish_harness_nudge_turn") == [
            [str(conversation.id), 2]
        ]


class TestChecks:
    def test_a_command_never_taken_ends_the_rebuild(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        inline,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        environment, nudge, _a1, _a2 = _queued(
            user,
            workspace,
            storage,
            "r-not-started",
            auth_client,
            django_capture_on_commit_callbacks,
        )
        assert rebuild.dispatch_nudge(environment.id, start_runtime=False)
        nudge.refresh_from_db()
        rebuild.check_nudges(environment.id)
        nudge.refresh_from_db()
        assert nudge.state == "queued"
        HostedHarnessConversationMessage.no_workspace_objects.filter(
            id=nudge.command_message_id
        ).update(created_at=timezone.now() - timedelta(minutes=16))

        rebuild.check_nudges(environment.id)

        nudge.refresh_from_db()
        assert (nudge.state, nudge.error) == ("failed", {"code": "rebuild_not_started"})
        drafts.require_no_rebuild(environment)

    def test_a_command_taken_whose_turn_never_started_ends_the_rebuild(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        inline,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        environment, nudge, _a1, _a2 = _queued(
            user,
            workspace,
            storage,
            "r-taken",
            auth_client,
            django_capture_on_commit_callbacks,
        )
        assert rebuild.dispatch_nudge(environment.id, start_runtime=False)
        nudge.refresh_from_db()
        taken = HostedHarnessConversationMessage.no_workspace_objects.filter(
            id=nudge.command_message_id
        )
        taken.update(state="delivered")
        rebuild.check_nudges(environment.id)
        nudge.refresh_from_db()
        assert nudge.state == "queued"
        taken.update(updated_at=timezone.now() - timedelta(minutes=16))

        rebuild.check_nudges(environment.id)

        nudge.refresh_from_db()
        assert (nudge.state, nudge.error) == ("failed", {"code": "rebuild_not_started"})

    def test_a_turn_with_no_sandbox_for_an_hour_is_stuck(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        inline,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        environment, nudge, _a1, _a2 = _queued(
            user,
            workspace,
            storage,
            "r-stuck",
            auth_client,
            django_capture_on_commit_callbacks,
        )
        assert rebuild.dispatch_nudge(environment.id, start_runtime=False)
        nudge.refresh_from_db()
        rebuild.start_nudge_turn(
            nudge.id,
            message_id=nudge.command_message_id,
            started_at=timezone.now() - timedelta(minutes=61),
        )

        rebuild.check_nudges(environment.id)

        nudge.refresh_from_db()
        assert (nudge.state, nudge.error) == ("failed", {"code": "rebuild_stuck"})
        message = HostedHarnessConversationMessage.no_workspace_objects.get(
            id=nudge.command_message_id
        )
        assert message.state == "failed"

    def test_recovery_keeps_messages_waiting_behind_a_rebuild(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        environment, nudge, _a1, _a2 = _queued(
            user,
            workspace,
            storage,
            "r-recovery",
            auth_client,
            django_capture_on_commit_callbacks,
        )
        assert rebuild.dispatch_nudge(environment.id, start_runtime=False)
        nudge.refresh_from_db()
        old = timezone.now() - timedelta(minutes=40)
        HostedHarnessConversationMessage.no_workspace_objects.filter(
            id=nudge.command_message_id
        ).update(created_at=old)
        scheduled_runtimes = []

        counts = recover_stalled_conversations(scheduled_runtimes.append)

        assert counts["abandoned"] == 0
        assert scheduled_runtimes
        # Once the rebuild closed, a message that waited behind it gets a fresh wait.
        rebuild.stop_rebuild(environment, nudge.id, user=user)
        waiting = enqueue_message(environment, content="x", client_request_id="x")[1]
        HostedHarnessConversationMessage.no_workspace_objects.filter(
            id=waiting.id
        ).update(created_at=old)
        counts = recover_stalled_conversations(scheduled_runtimes.append)
        assert counts["abandoned"] == 0
        waiting.refresh_from_db()
        assert waiting.state == "queued"

    def test_deleting_the_environment_ends_its_rebuild(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        inline,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        environment, nudge, _a1, _a2 = _queued(
            user,
            workspace,
            storage,
            "r-delete",
            auth_client,
            django_capture_on_commit_callbacks,
        )
        assert rebuild.dispatch_nudge(environment.id, start_runtime=False)

        with django_capture_on_commit_callbacks(execute=True):
            assert auth_client.delete(f"{BASE}/{environment.id}/").status_code == 204

        nudge.refresh_from_db()
        assert (nudge.state, nudge.error) == ("failed", {"code": "environment_deleted"})
        assert not _names(inline, "ensure_hosted_harness_conversation_runtime")

    def test_deleting_restores_no_draft(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        inline,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        environment, nudge, _a1, _a2 = _queued(
            user,
            workspace,
            storage,
            "r-delete-running",
            auth_client,
            django_capture_on_commit_callbacks,
        )
        assert rebuild.dispatch_nudge(environment.id, start_runtime=False)
        nudge.refresh_from_db()
        rebuild.start_nudge_turn(
            nudge.id, message_id=nudge.command_message_id, started_at=None
        )

        with (
            patch.object(
                rebuild,
                "restore_draft_snapshot",
                side_effect=HostedHarnessError("storage_down", "down"),
            ),
            django_capture_on_commit_callbacks(execute=True),
        ):
            assert auth_client.delete(f"{BASE}/{environment.id}/").status_code == 204

        nudge.refresh_from_db()
        assert (nudge.state, nudge.error) == ("failed", {"code": "environment_deleted"})


class TestReadersAndFlags:
    def test_an_unversioned_environment_has_no_rebuild_state(self, user, workspace):
        from simulate.tests.test_harness_versioning_record import _environment

        environment = _environment(user, workspace, "r-unversioned")
        conversation = ensure_conversation(environment)

        assert rebuild.nudge_composition(environment, conversation) is None
        assert rebuild.wanted_agent_version(environment, conversation) is None
        assert rebuild.landing_pending(environment) is False
        assert rebuild.holds_conversation(conversation) is False

    def test_stop_is_not_found_with_versioning_off(
        self, user, workspace, settings, auth_client
    ):
        from simulate.tests.test_harness_versioning_record import _environment

        environment = _environment(user, workspace, "r-off")
        settings.HARNESS_VERSIONING_ENABLED = False

        response = auth_client.post(
            f"{BASE}/{environment.id}/nudges/00000000-0000-0000-0000-000000000000/stop/"
        )

        assert response.status_code == 404
        assert response.json()["error"] == "versioning_disabled"

    def test_rolling_back_a_version_with_unsaved_edits_offers_to_reapply_them(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        from simulate.services.harness_versioning.versions import move_default

        environment, nudge, _a1, _a2 = _queued(
            user,
            workspace,
            storage,
            "r-reapply",
            auth_client,
            django_capture_on_commit_callbacks,
        )
        e1 = environment.environment_version
        nudge = _turn(environment, nudge, storage)
        e2 = environment.environment_version
        HarnessEnvironmentVersion.no_workspace_objects.filter(id=e2.id).update(
            includes_draft_edits=True, snapshot_diff={"contract": {}}
        )

        with django_capture_on_commit_callbacks(execute=True):
            move_default(environment, "environment", e1.id, expected_default_id=e2.id)

        [offer] = HostedHarnessConversationMessage.no_workspace_objects.filter(
            payload__has_key="reapply"
        )
        assert offer.role == "system"
        assert offer.payload["reapply"] == {
            "piece": "environment",
            "version_id": str(e2.id),
            "label": "v2",
        }


class TestDeferredActivation:
    def test_the_tick_fans_out_open_rebuilds_and_busy_activations(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        inline,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        environment, _nudge, _a1, a2 = _queued(
            user,
            workspace,
            storage,
            "r-tick",
            auth_client,
            django_capture_on_commit_callbacks,
        )
        from simulate.models import HarnessAgentVersionRuntime

        HarnessAgentVersionRuntime.no_workspace_objects.filter(agent_version=a2).update(
            preflight_report={
                "activation": [
                    {
                        "environment_id": str(environment.id),
                        "outcome": "environment_busy",
                    }
                ]
            },
            updated_at=timezone.now() - timedelta(minutes=6),
        )

        assert rebuild.tick_nudges() == {"environments": 1, "versions": 1}

        assert _names(inline, "check_harness_nudges") == [[str(environment.id)]]
        assert _names(inline, "retry_harness_deferred_activations") == [[str(a2.id)]]

    def test_the_tick_takes_the_least_recently_ticked_one_organization_at_a_time(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        inline,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        environment, nudge, _a1, _a2 = _queued(
            user,
            workspace,
            storage,
            "r-tick-fair",
            auth_client,
            django_capture_on_commit_callbacks,
        )
        from simulate.tests.test_harness_versioning_record import _environment

        other = _environment(user, workspace, "r-tick-fair-2")
        # Made after, but waiting longer: it comes first.
        HarnessNudge.no_workspace_objects.create(
            job=other,
            organization_id=other.organization_id,
            target="environment",
            state="queued",
        )
        HarnessNudge.no_workspace_objects.filter(job=other).update(
            updated_at=timezone.now() - timedelta(hours=1)
        )

        rebuild.tick_nudges(limit=1)
        rebuild.tick_nudges(limit=1)

        assert _names(inline, "check_harness_nudges") == [
            [str(other.id)],
            [str(environment.id)],
        ]
        rows = [("a", 1), ("a", 2), ("a", 3), ("b", 4), ("c", 5)]
        assert rebuild._fair(rows, 4) == [1, 4, 5, 2]

    def test_a_retry_closes_a_failed_build_and_waits_on_a_busy_one(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        inline,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        environment, _nudge, _a1, a2 = _queued(
            user,
            workspace,
            storage,
            "r-retry-failed",
            auth_client,
            django_capture_on_commit_callbacks,
        )
        from simulate.models import HarnessAgentVersionRuntime
        from simulate.tests.test_harness_versioning_record import _environment

        failed = _environment(user, workspace, "r-retry-failed-2")
        HostedHarnessJob.no_workspace_objects.filter(id=failed.id).update(
            state="failed"
        )
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            state="running"
        )
        side = HarnessAgentVersionRuntime.no_workspace_objects.filter(agent_version=a2)
        side.update(
            preflight_report={
                "activation": [
                    {"environment_id": str(one), "outcome": "environment_busy"}
                    for one in (environment.id, failed.id)
                ]
            },
            updated_at=timezone.now() - timedelta(minutes=6),
        )

        assert rebuild.retry_deferred_activations(a2.id) == {}

        report = side.get().preflight_report["activation"]
        assert {one["environment_id"]: one for one in report} == {
            str(failed.id): {
                "environment_id": str(failed.id),
                "outcome": "environment_not_ready",
            },
            str(environment.id): {
                "environment_id": str(environment.id),
                "outcome": "environment_busy",
                "attempts": 2,
            },
        }
        # Still busy: the tick waits before retrying it again.
        assert rebuild.tick_nudges()["versions"] == 0

    def test_a_completed_build_retries_activations_that_waited_for_it(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        inline,
        django_capture_on_commit_callbacks,
    ):
        from simulate.models import HarnessAgentVersionRuntime
        from simulate.tests.test_harness_agent_versions import (
            _a1,
            _create,
            _preflight,
            _shared,
        )

        [environment] = _shared(user, workspace, storage, "r-deferred", count=1)
        a2 = _create(
            _a1(environment), environment, secret_values={"OPENAI_API_KEY": NEW_VALUE}
        )
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            state="running"
        )
        with django_capture_on_commit_callbacks(execute=True):
            assert _preflight(a2).status == "passed"
        HarnessAgentVersionRuntime.no_workspace_objects.get(agent_version=a2)

        with django_capture_on_commit_callbacks(execute=True):
            rebuild.schedule_deferred_activations(environment.id)

        assert _names(inline, "retry_harness_deferred_activations") == [[str(a2.id)]]


def _offered(user, workspace, storage, key, auth_client, capture):
    """Two environments on A2; A3 asked for by the first, offered to the second,
    whose offer is answered yes."""
    from simulate.tests.test_harness_agent_versions import _create, _on_a2, _preflight

    (first, second), _a1, a2 = _on_a2(user, workspace, storage, key)
    a3 = _create(a2, first, secret_values={"OPENAI_API_KEY": "val-openai-v3"})
    with capture(execute=True):
        assert _preflight(a3).status == "passed"
    [offer] = HarnessNudge.no_workspace_objects.filter(
        job=second, trigger_agent_version=a3
    )
    assert offer.facts["agent_default_moved"] is False
    response = _answer(auth_client, second, offer, "yes")
    assert response.status_code == 200, response.content
    offer.refresh_from_db()
    second.refresh_from_db()
    return second, offer, a2, a3


class TestAgentDefault:
    def test_landing_an_offer_makes_its_agent_the_default(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        settings,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        from simulate.services.harness_agent_versions import HAND_MOVE_KEY

        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        environment, nudge, a2, a3 = _offered(
            user,
            workspace,
            storage,
            "r-offer",
            auth_client,
            django_capture_on_commit_callbacks,
        )

        nudge = _turn(environment, nudge, storage)

        assert nudge.state == "applied", (nudge.error, nudge.summary)
        assert environment.agent_version_id == a3.id
        assert environment.environment_version.agent_version_id == a3.id
        assert nudge.summary["agent_default_moved"] is True
        assert environment.payload["metadata"][HAND_MOVE_KEY]["to"] == str(a3.id)

    def test_an_agent_not_prepared_for_lands_for_the_current_one(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        environment, nudge, a2, a3 = _offered(
            user,
            workspace,
            storage,
            "r-offer-unprepared",
            auth_client,
            django_capture_on_commit_callbacks,
        )
        e1 = environment.environment_version_id

        nudge = _turn(environment, nudge, storage)

        assert nudge.state == "applied", (nudge.error, nudge.summary)
        assert environment.environment_version_id != e1
        assert environment.agent_version_id == a2.id
        assert environment.environment_version.agent_version_id == a3.id
        assert nudge.summary["agent_default_moved"] is False
        assert nudge.summary["agent_not_moved"] == "not_prepared"

    def test_a_default_moved_by_hand_meanwhile_stays(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        settings,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        environment, nudge, a2, a3 = _offered(
            user,
            workspace,
            storage,
            "r-offer-moved",
            auth_client,
            django_capture_on_commit_callbacks,
        )
        from dataclasses import replace

        from simulate.models import AgentVersion
        from simulate.services.harness_versioning import versions
        from simulate.services.harness_versioning.versions import move_default
        from simulate.services.hosted_harness_conversation import (
            environment_authoring_revision,
        )

        a1 = AgentVersion._base_manager.get(id=nudge.base_agent_version_id)
        assert a1.id not in (a2.id, a3.id)
        revisions = []

        def recomposed(*args):
            # As with a bundle cached for A1: the move composes the pair anew.
            pair = default_pair(*args)
            return replace(pair, revision="sha256:" + "9" * 64)

        default_pair = versions._default_pair

        def move_by_hand():
            revisions.append(environment_authoring_revision(environment))
            with patch.object(versions, "_default_pair", side_effect=recomposed):
                move_default(environment, "agent", a1.id, expected_default_id=a2.id)
            environment.refresh_from_db()
            revisions.append(environment_authoring_revision(environment))

        nudge = _turn(environment, nudge, storage, meanwhile=move_by_hand)

        # The move re-resolved the pair, so the turn's basis is not the current one.
        assert revisions[0] != revisions[1]
        assert nudge.state == "applied", (nudge.error, nudge.summary)
        assert environment.agent_version_id == a1.id
        assert nudge.summary["agent_default_moved"] is False
        assert nudge.summary["agent_not_moved"] == "default_moved_by_hand"

    def test_nothing_to_change_moves_the_agent_and_records_the_fit(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        settings,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        environment, nudge, a2, a3 = _offered(
            user,
            workspace,
            storage,
            "r-offer-fits",
            auth_client,
            django_capture_on_commit_callbacks,
        )
        e1 = environment.environment_version_id

        with django_capture_on_commit_callbacks(execute=True):
            nudge = _turn(
                environment, nudge, storage, result=_result(nudge, changed=False)
            )

        assert nudge.state == "no_change", nudge.error
        assert environment.environment_version_id == e1
        assert environment.agent_version_id == a3.id
        assert environment.environment_version.agent_version_id == a3.id
        assert nudge.summary["agent_default_moved"] is True
        # E1 now fits A3: raising for A3 again gives no new card.
        assert nudges.raise_environment_nudge(
            environment.id, a3.id, default_moved=True
        ) in (None, nudge)
        assert not HarnessNudge.no_workspace_objects.filter(
            job=environment, trigger_agent_version=a3, state="pending"
        ).exists()


def _suite_queued(user, workspace, storage, key, auth_client, capture):
    """An environment on A2 (a new value) whose suite card is answered yes."""
    environment, _a1, _a2 = _activated(user, workspace, storage, key, capture)
    HarnessNudge.no_workspace_objects.filter(job=environment).update(
        state="answered_no"
    )
    suite = HarnessNudge.no_workspace_objects.create(
        job=environment,
        organization_id=environment.organization_id,
        target="suite",
        state="pending",
        base_environment_version_id=environment.environment_version_id,
        base_suite_version_id=environment.suite_version_id,
        facts={"environment_summary": "1 tool added", "environment_diff": {}},
    )
    assert _answer(auth_client, environment, suite, "yes").status_code == 200
    suite.refresh_from_db()
    return environment, suite


class TestSuite:
    def test_a_suite_rebuild_lands_with_proofs_made_since_it_started(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        from simulate.models import HarnessSuiteValidation
        from simulate.tests.test_harness_amend_archive import NAMES

        environment, suite = _suite_queued(
            user,
            workspace,
            storage,
            "r-suite",
            auth_client,
            django_capture_on_commit_callbacks,
        )
        s1 = environment.suite_version_id
        now = timezone.now()
        # A minute before the turn starts: within the build's clock-skew allowance.
        old = (now - timedelta(minutes=1)).isoformat()
        from simulate.tests.test_harness_amend_archive import _key

        made = (now + timedelta(minutes=1)).isoformat()
        proofs = {_key(name): {"holds": True, "proved_at": made} for name in NAMES}
        proofs[_key(NAMES[0])]["proved_at"] = old

        suite = _turn(
            environment,
            suite,
            storage,
            contract=None,
            names=NAMES,
            **{"proofs.json": json.dumps(proofs)},
        )

        assert suite.state == "applied", (suite.error, suite.summary)
        s2 = environment.suite_version
        assert s2.id != s1 and s2.origin == "nudge"
        assert s2.authored_against_id == suite.base_environment_version_id
        validation = HarnessSuiteValidation.no_workspace_objects.get(
            suite_version=s2, environment_version_id=suite.base_environment_version_id
        )
        # The proof made before the turn started does not count.
        assert _key(NAMES[0]) not in validation.proofs
        assert validation.status == "partial"

    def test_a_suite_rebuild_result_never_carries_the_agents_value(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        from simulate.tests.test_harness_amend_archive import NAMES

        environment, suite = _suite_queued(
            user,
            workspace,
            storage,
            "r-suite-secret",
            auth_client,
            django_capture_on_commit_callbacks,
        )

        suite = _turn(
            environment,
            suite,
            storage,
            contract=None,
            names=NAMES,
            result=_result(suite, notes=f"used {NEW_VALUE}", changed=False),
        )

        assert suite.state == "no_change", suite.error
        assert NEW_VALUE not in json.dumps([suite.summary, suite.error])
        body = json.dumps(auth_client.get(f"{BASE}/{environment.id}/nudges/").json())
        assert NEW_VALUE not in body

    def test_a_suite_rebuild_with_no_agent_masks_the_stored_values(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        from simulate.tests.test_harness_agent_versions import OLD
        from simulate.tests.test_harness_amend_archive import NAMES

        environment, suite = _suite_queued(
            user,
            workspace,
            storage,
            "r-suite-no-agent",
            auth_client,
            django_capture_on_commit_callbacks,
        )
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            agent_version=None
        )
        HarnessEnvironmentVersion.all_objects.filter(
            id=environment.environment_version_id
        ).update(agent_version=None)
        environment.refresh_from_db()
        legacy = OLD["OPENAI_API_KEY"]

        suite = _turn(
            environment,
            suite,
            storage,
            contract=None,
            names=NAMES,
            result=_result(suite, notes=f"used {legacy}", changed=False),
        )

        assert suite.state == "no_change", suite.error
        assert legacy not in json.dumps([suite.summary, suite.error])

    def test_the_agent_the_turn_ran_as_is_masked_after_a_move_by_hand(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        from simulate.services.harness_versioning.versions import move_default
        from simulate.tests.test_harness_agent_versions import _create
        from simulate.tests.test_harness_amend_archive import NAMES
        from simulate.tests.test_harness_nudges import _checked

        environment, suite = _suite_queued(
            user,
            workspace,
            storage,
            "r-suite-ran-as",
            auth_client,
            django_capture_on_commit_callbacks,
        )
        a2 = environment.agent_version
        a3 = _checked(
            _create(a2, environment, secret_values={"OPENAI_API_KEY": "val-a3-value"})
        )

        def move_by_hand():
            move_default(environment, "agent", a3.id, expected_default_id=a2.id)
            environment.refresh_from_db()

        suite = _turn(
            environment,
            suite,
            storage,
            contract=None,
            names=NAMES,
            lease_agent=a2,
            meanwhile=move_by_hand,
            result=_result(suite, notes=f"used {NEW_VALUE}", changed=False),
        )

        assert environment.agent_version_id == a3.id
        assert suite.state == "no_change", suite.error
        assert NEW_VALUE not in json.dumps([suite.summary, suite.error])

    @pytest.mark.parametrize(
        ("named", "state"),
        [((), "failed"), (("refunded",), "applied")],
        ids=["unnamed", "named"],
    )
    def test_a_sub_goal_the_environment_added_must_be_named(
        self,
        named,
        state,
        user,
        workspace,
        storage,
        scheduled,
        started,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        from simulate.tests.test_harness_amend_archive import NAMES
        from simulate.tests.test_harness_versioning_record import _scenario

        environment, suite = _suite_queued(
            user,
            workspace,
            storage,
            f"r-suite-goal-{state}",
            auth_client,
            django_capture_on_commit_callbacks,
        )
        HarnessNudge.no_workspace_objects.filter(id=suite.id).update(
            facts={
                **suite.facts,
                "environment_diff": {"sub_goals": {"added": ["refunded"]}},
            }
        )
        suite.refresh_from_db()
        scenarios = [
            _scenario(NAMES[0], sub_goals=list(named)),
            _scenario(NAMES[1]),
        ]
        assert "sub-goal the environment added" in rebuild.briefing(environment, suite)

        suite = _turn(
            environment,
            suite,
            storage,
            contract=None,
            names=NAMES[:2],
            **{
                "scenarios.json": json.dumps(scenarios),
                f"scenarios/{NAMES[0]}/scenario.json": json.dumps(scenarios[0]),
            },
        )

        assert suite.command_message.payload["new_sub_goals"] == ["refunded"]
        assert suite.state == state, (suite.error, suite.summary)
        if state == "failed":
            assert suite.error["code"] == "checks_failed"
            assert [(one["item"], one["kind"]) for one in suite.error["problems"]] == [
                ("refunded", "sub_goal")
            ]

    def test_a_nested_proofs_file_reads_as_none(self, storage):
        body = _authoring(
            **{
                ".futureagi-nudge-result.json": "{}",
                "proofs.json": "[" * 100_000,
            }
        )
        storage.objects["nudge-nested"] = body

        found = rebuild._result_files("nudge-nested", "suite")

        assert found == {rebuild.NUDGE_RESULT: {}, rebuild.PROOFS: None}


class TestSourceFreshness:
    def test_a_sandbox_running_as_another_agent_is_retired(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        django_capture_on_commit_callbacks,
    ):
        from unittest.mock import MagicMock

        from simulate.services.hosted_harness_gateway import HostedHarnessGateway

        environment, a1, a2 = _activated(
            user, workspace, storage, "r-fresh", django_capture_on_commit_callbacks
        )
        conversation = ensure_conversation(environment)
        capability = issue_conversation_capability(
            conversation,
            endpoint_base_url="https://platform.example",
            provider_ref="sandbox-old",
            attempt=None,
            ttl_seconds=3600,
            agent_version=a1,
        )
        HostedHarnessConversationLease.no_workspace_objects.filter(
            id=capability.lease.id
        ).update(state="active", heartbeat_at=timezone.now())
        gateway = HostedHarnessGateway.__new__(HostedHarnessGateway)
        gateway.client = MagicMock(runtime_name="r", runtime_digest="d")
        assert rebuild.wanted_agent_version(environment, conversation) == a2

        with (
            patch.object(gateway, "_delete_chat_sandbox") as deleted,
            patch.object(gateway, "_start_chat_sandbox") as start,
        ):
            gateway.ensure_conversation_runtime(
                environment,
                conversation=conversation,
                endpoint_base_url="https://platform.example",
            )

        deleted.assert_called_once_with("sandbox-old", conversation.id)
        assert start.call_args.kwargs["agent_version"] == a2

    def test_the_sandbox_gets_the_agents_payload_with_names_only(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        django_capture_on_commit_callbacks,
    ):
        from unittest.mock import MagicMock

        from simulate.services.hosted_harness_gateway import (
            HostedHarnessGateway,
            HostedSourceAcquirer,
        )

        environment, _a1, a2 = _activated(
            user, workspace, storage, "r-payload", django_capture_on_commit_callbacks
        )
        before = {key: environment.payload.get(key) for key in ("agent", "source")}
        conversation = ensure_conversation(environment)
        gateway = HostedHarnessGateway.__new__(HostedHarnessGateway)
        gateway.client = MagicMock()
        seen = []

        def acquire(_self, job):
            seen.append(job.payload)
            raise RuntimeError("stop here")

        with (
            patch.object(HostedSourceAcquirer, "acquire", acquire),
            pytest.raises(RuntimeError),
        ):
            gateway._start_chat_sandbox(
                environment,
                conversation=conversation,
                endpoint_base_url="https://platform.example",
                agent_version=a2,
            )

        [payload] = seen
        from simulate.services.harness_credentials import agent_version_secret_ref

        refs = payload["agent"]["secret_refs"]
        assert refs["OPENAI_API_KEY"] == agent_version_secret_ref("OPENAI_API_KEY")
        assert NEW_VALUE not in json.dumps(payload)
        environment.refresh_from_db()
        # The environment's own payload is left as it was.
        assert {key: environment.payload.get(key) for key in before} == before

    def test_the_turn_context_names_the_leases_agent_commit(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        django_capture_on_commit_callbacks,
    ):
        from simulate.models import HarnessAgentVersionRuntime
        from simulate.services.hosted_harness_conversation import build_turn_context

        environment, _a1, a2 = _activated(
            user, workspace, storage, "r-context", django_capture_on_commit_callbacks
        )
        HarnessAgentVersionRuntime.no_workspace_objects.filter(agent_version=a2).update(
            source={"commit_sha": "b" * 40}
        )
        conversation = ensure_conversation(environment)
        issue_conversation_capability(
            conversation,
            endpoint_base_url="https://platform.example",
            provider_ref="sandbox-new",
            attempt=None,
            ttl_seconds=600,
            agent_version=a2,
        )

        context = build_turn_context(conversation)

        assert context["environment"]["source_revision"] == "b" * 40


class TestCompositionAfterClose:
    def test_a_sandbox_composed_for_the_rebuild_cannot_save_over_the_drafts(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        inline,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        from simulate.services.hosted_harness_gateway import (
            with_authoring_basis,
            with_draft_basis,
        )
        from simulate.tests.test_harness_amend_archive import NAMES

        environment, nudge, _a1, _a2 = _queued(
            user,
            workspace,
            storage,
            "r-compose",
            auth_client,
            django_capture_on_commit_callbacks,
        )
        conversation = ensure_conversation(environment)
        token = drafts.compose_workspace(environment, conversation).token
        drafts.record_draft(
            environment,
            with_draft_basis(with_authoring_basis(_authoring(names=NAMES), ""), token),
            origin="chat",
            user=user,
            pieces={"suite"},
        )
        mine = drafts.open_draft(environment, "suite")
        assert rebuild.dispatch_nudge(environment.id, start_runtime=False)
        # A sandbox that started for the rebuild, still about to run.
        started_for_rebuild = drafts.compose_workspace(
            environment, conversation, only_draft="environment"
        ).token
        with django_capture_on_commit_callbacks(execute=True):
            rebuild.stop_rebuild(environment, nudge.id, user=user)
        command = enqueue_message(environment, content="x", client_request_id="x")[1]
        body = with_draft_basis(
            with_authoring_basis(
                _authoring(contract=REFUND), command.payload["_environment_revision"]
            ),
            started_for_rebuild,
        )
        key, digest = _put_conversation_checkpoint(conversation, storage, body)

        with pytest.raises(HostedHarnessError) as refused:
            promote_conversation_checkpoint(
                conversation,
                object_key=key,
                digest=digest,
                sequence=20,
                expected_revision=command.payload["_environment_revision"],
                command=command,
            )

        assert refused.value.code == "draft_changed"
        kept = drafts.open_draft(environment, "suite")
        assert (kept.id, kept.revision, kept.hash) == (
            mine.id,
            mine.revision,
            mine.hash,
        )


class TestMoreEndings:
    def test_a_problem_only_in_the_unsaved_edits_is_tagged(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        from simulate.services.hosted_harness_gateway import (
            with_authoring_basis,
            with_draft_basis,
        )

        environment, nudge, _a1, _a2 = _queued(
            user,
            workspace,
            storage,
            "r-tag",
            auth_client,
            django_capture_on_commit_callbacks,
        )
        conversation = ensure_conversation(environment)
        token = drafts.compose_workspace(environment, conversation).token
        mine = {"agent": "support", "tools": [{"name": "lookup_order"}]}
        drafts.record_draft(
            environment,
            with_draft_basis(
                with_authoring_basis(_authoring(contract=mine), ""), token
            ),
            origin="chat",
            user=user,
        )
        both = {
            "agent": "support",
            "tools": [{"name": "lookup_order"}, REFUND["tools"][0]],
        }
        result = _result(
            nudge,
            passed=False,
            problems=[
                {"item": "lookup_order", "kind": "contract", "message": "x"},
                {"item": "refund_order", "kind": "contract", "message": "y"},
            ],
        )

        nudge = _turn(environment, nudge, storage, contract=both, result=result)

        assert nudge.state == "failed"
        assert [
            (one["item"], one["in_your_unsaved_edits"])
            for one in nudge.error["problems"]
        ] == [("lookup_order", True), ("refund_order", False)]
        # The person's draft is back as it was.
        draft = drafts.open_draft(environment, "environment")
        assert [one["name"] for one in draft.documents["contract"]["tools"]] == [
            "lookup_order"
        ]

    def test_an_interrupted_turn_fails_the_rebuild(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        inline,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        environment, nudge, _a1, _a2 = _queued(
            user,
            workspace,
            storage,
            "r-interrupted",
            auth_client,
            django_capture_on_commit_callbacks,
        )
        assert rebuild.dispatch_nudge(environment.id, start_runtime=False)
        nudge.refresh_from_db()
        command = nudge.command_message
        conversation = command.conversation
        capability = issue_conversation_capability(
            conversation,
            endpoint_base_url="https://platform.example",
            provider_ref="sandbox-nudge",
            attempt=None,
            ttl_seconds=600,
        )
        client, headers = APIClient(), _headers(capability)
        interrupt = enqueue_message(
            environment, content="", client_request_id="stop-it", kind="interrupt"
        )[1]
        with django_capture_on_commit_callbacks(execute=True):
            response = _send(
                client,
                conversation,
                headers,
                0,
                (1, "turn_started", {"command_message_id": str(command.id)}),
                (
                    2,
                    "turn_interrupted",
                    {
                        "command_message_id": str(command.id),
                        "interrupt_message_id": str(interrupt.id),
                    },
                ),
                (
                    3,
                    "turn_completed",
                    {"command_message_id": str(command.id), "outcome": "interrupted"},
                ),
            )
        assert response.status_code == 200, response.content

        nudge.refresh_from_db()
        assert (nudge.state, nudge.error) == ("failed", {"code": "turn_interrupted"})
        assert nudge.usage["attempts"][0]["stopped"] is False

    @pytest.mark.parametrize(
        ("minutes", "landed"), [(6, "retried"), (16, "landing_failed")]
    )
    def test_a_lost_landing_is_retried_then_given_up(
        self,
        minutes,
        landed,
        user,
        workspace,
        storage,
        scheduled,
        started,
        inline,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        from simulate.models import HostedHarnessConversationEvent

        environment, nudge, _a1, _a2 = _queued(
            user,
            workspace,
            storage,
            f"r-lost-{minutes}",
            auth_client,
            django_capture_on_commit_callbacks,
        )
        assert rebuild.dispatch_nudge(environment.id, start_runtime=False)
        nudge.refresh_from_db()
        rebuild.start_nudge_turn(
            nudge.id, message_id=nudge.command_message_id, started_at=None
        )
        conversation = nudge.command_message.conversation
        event = HostedHarnessConversationEvent.no_workspace_objects.create(
            event_id=f"ce-lost-{minutes}",
            conversation=conversation,
            sequence=7,
            kind="turn_completed",
            payload={
                "command_message_id": str(nudge.command_message_id),
                "outcome": "success",
            },
            digest="x",
            emitted_at=timezone.now(),
        )
        HostedHarnessConversationEvent.no_workspace_objects.filter(
            event_id=event.event_id
        ).update(created_at=timezone.now() - timedelta(minutes=minutes))
        HarnessNudge.no_workspace_objects.filter(id=nudge.id).update(
            draft_snapshot={**nudge.draft_snapshot, "turn_sequence": 7}
        )
        assert rebuild.landing_pending(environment)

        with patch(
            "simulate.services.hosted_harness_conversation.promote_turn_checkpoint"
        ) as promote:
            rebuild.check_nudges(environment.id)

        nudge.refresh_from_db()
        if landed == "retried":
            promote.assert_called_once_with(str(conversation.id), 7)
            assert nudge.state == "running"
        else:
            promote.assert_not_called()
            assert (nudge.state, nudge.error) == ("failed", {"code": "landing_failed"})

    def test_a_retried_turn_that_succeeds_is_not_failed_as_interrupted(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        inline,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        environment, nudge, _a1, _a2 = _queued(
            user,
            workspace,
            storage,
            "r-retried",
            auth_client,
            django_capture_on_commit_callbacks,
        )
        assert rebuild.dispatch_nudge(environment.id, start_runtime=False)
        nudge.refresh_from_db()
        command = nudge.command_message
        conversation = command.conversation

        def sandbox(ref):
            capability = issue_conversation_capability(
                conversation,
                endpoint_base_url="https://platform.example",
                provider_ref=ref,
                attempt=None,
                ttl_seconds=600,
            )
            return APIClient(), _headers(capability)

        interrupt = enqueue_message(
            environment, content="", client_request_id="stop-it", kind="interrupt"
        )[1]
        ran = {"command_message_id": str(command.id)}
        client, headers = sandbox("sandbox-a")
        with django_capture_on_commit_callbacks(execute=True):
            first = _send(
                client,
                conversation,
                headers,
                0,
                (1, "turn_started", ran),
                (
                    2,
                    "turn_interrupted",
                    {**ran, "interrupt_message_id": str(interrupt.id)},
                ),
            )
        assert first.status_code == 200, first.content
        # Sandbox A died before its turn ended; B runs the turn again in full.
        client, headers = sandbox("sandbox-b")
        with django_capture_on_commit_callbacks(execute=True):
            again = _send(client, conversation, headers, 0, (3, "turn_started", ran))
        assert again.status_code == 200, again.content
        nudge.refresh_from_db()
        assert "interrupted" not in nudge.draft_snapshot

        with django_capture_on_commit_callbacks(execute=True):
            _send(
                client,
                conversation,
                headers,
                0,
                (4, "turn_completed", {**ran, "outcome": "success"}),
            )

        assert _names(inline, nudge_tasks.finish_harness_nudge_turn.name) == []
        nudge.refresh_from_db()
        assert (nudge.error or {}).get("code") != "turn_interrupted"


class TestChatSandboxFence:
    def test_a_start_whose_claim_was_given_up_deletes_its_sandbox_and_claims_again(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        django_capture_on_commit_callbacks,
    ):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from simulate.services import hosted_harness_gateway as gateway_module

        environment, _a1, _a2 = _activated(
            user, workspace, storage, "r-late-start", django_capture_on_commit_callbacks
        )
        conversation = ensure_conversation(environment)
        leases = HostedHarnessConversationLease.no_workspace_objects

        made = []

        def create(*_args, **_kwargs):
            # A stopped rebuild expires the start's lease while the first sandbox is
            # made; chat claims again at once, and that start goes through.
            if not made:
                leases.filter(conversation=conversation).update(state="expired")
            sandbox = MagicMock(id=f"sandbox-{len(made)}")
            sandbox.process.exec.return_value = SimpleNamespace(exit_code=0)
            made.append(sandbox)
            return sandbox

        gateway = gateway_module.HostedHarnessGateway.__new__(
            gateway_module.HostedHarnessGateway
        )
        gateway.client = MagicMock(
            runtime_name="r",
            runtime_digest="d",
            max_egress_domains=None,
            create_timeout_seconds=300,
        )
        gateway.client.create.side_effect = create
        with (
            patch.object(
                gateway_module.HostedSourceAcquirer,
                "acquire",
                lambda _self, _job: (b"source", None),
            ),
            patch.object(gateway_module, "_platform_simulator_material", dict),
            patch.object(
                gateway_module, "_resolved_egress_domains", lambda *_args: set()
            ),
        ):
            started_lease = gateway.ensure_conversation_runtime(
                environment,
                conversation=conversation,
                endpoint_base_url="https://platform.example",
            )

        gateway.client.get.assert_called_once_with(
            "sandbox-0",
            request_timeout=gateway_module._PROVIDER_POLL_TIMEOUT_SECONDS,
        )
        gateway.client.delete.assert_called_once()
        made[0].fs.upload_file.assert_not_called()
        assert started_lease == leases.get(conversation=conversation)
        assert (started_lease.provider_ref, started_lease.state) == (
            "sandbox-1",
            "starting",
        )
        made[1].process.execute_session_command.assert_called_once()

    def test_events_that_waited_out_a_stop_are_refused(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        django_capture_on_commit_callbacks,
    ):
        from simulate.services.hosted_harness_conversation import (
            ingest_conversation_events,
        )

        environment, _a1, _a2 = _activated(
            user,
            workspace,
            storage,
            "r-late-events",
            django_capture_on_commit_callbacks,
        )
        conversation = ensure_conversation(environment)
        capability = issue_conversation_capability(
            conversation,
            endpoint_base_url="https://platform.example",
            provider_ref="sandbox-stopped",
            attempt=None,
            ttl_seconds=600,
        )
        # Authenticated while active; the stop expired the lease before the lock.
        HostedHarnessConversationLease.no_workspace_objects.filter(
            id=capability.lease.id
        ).update(state="expired")

        with pytest.raises(HostedHarnessError) as refused:
            ingest_conversation_events(conversation, acknowledged_through=0, events=[])

        assert refused.value.code == "conversation_lease_inactive"
        capability.lease.refresh_from_db()
        assert capability.lease.state == "expired"

    def test_a_turn_keeps_its_sandbox_running_as_another_agent_until_it_ends(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        inline,
        django_capture_on_commit_callbacks,
    ):
        from unittest.mock import MagicMock

        from simulate.services.hosted_harness_gateway import HostedHarnessGateway

        environment, a1, _a2 = _activated(
            user, workspace, storage, "r-mid-turn", django_capture_on_commit_callbacks
        )
        conversation = ensure_conversation(environment)
        capability = issue_conversation_capability(
            conversation,
            endpoint_base_url="https://platform.example",
            provider_ref="sandbox-old",
            attempt=None,
            ttl_seconds=3600,
            agent_version=a1,
        )
        HostedHarnessConversationLease.no_workspace_objects.filter(
            id=capability.lease.id
        ).update(state="active", heartbeat_at=timezone.now())
        HostedHarnessConversation.no_workspace_objects.filter(
            id=conversation.id
        ).update(active_invocation_id="turn-1")
        enqueue_message(environment, content="and also", client_request_id="mid")
        gateway = HostedHarnessGateway.__new__(HostedHarnessGateway)
        gateway.client = MagicMock(runtime_name="r", runtime_digest="d")

        with (
            patch.object(gateway, "_delete_chat_sandbox") as deleted,
            patch.object(gateway, "_start_chat_sandbox") as start,
        ):
            kept = gateway.ensure_conversation_runtime(
                environment,
                conversation=conversation,
                endpoint_base_url="https://platform.example",
            )

        assert kept.id == capability.lease.id
        deleted.assert_not_called()
        start.assert_not_called()

        HostedHarnessConversation.no_workspace_objects.filter(
            id=conversation.id
        ).update(active_invocation_id=None)
        rebuild.on_turn_event(str(conversation.id), "turn_completed", {}, 5, None)

        kept.refresh_from_db()
        assert kept.state == "expired"
        assert _names(inline, nudge_tasks.stop_harness_nudge_sandbox.name) == [
            [str(conversation.id), "sandbox-old"]
        ]
        assert _names(
            inline, conversation_tasks.ensure_hosted_harness_conversation_runtime.name
        )

    def test_an_interrupted_turn_of_another_agent_saves_before_its_sandbox_goes(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        inline,
        django_capture_on_commit_callbacks,
    ):
        from simulate.services.hosted_harness_gateway import with_authoring_basis
        from simulate.tests.test_hosted_harness_conversation import _commit_checkpoint

        environment, a1, _a2 = _activated(
            user,
            workspace,
            storage,
            "r-interrupted",
            django_capture_on_commit_callbacks,
        )
        conversation, command, _ = enqueue_message(
            environment, content="add a rule", client_request_id="turn"
        )
        capability = issue_conversation_capability(
            conversation,
            endpoint_base_url="https://platform.example",
            provider_ref="sandbox-old",
            attempt=None,
            ttl_seconds=3600,
            agent_version=a1,
        )
        client, headers = APIClient(), _headers(capability)
        assert (
            client.get(
                f"{GUEST}/{conversation.id}/commands/?after=0", **headers
            ).status_code
            == 200
        )
        interrupt = enqueue_message(
            environment, content="", client_request_id="stop-it", kind="interrupt"
        )[1]
        ran = {"command_message_id": str(command.id)}
        with django_capture_on_commit_callbacks(execute=True):
            stopped = _send(
                client,
                conversation,
                headers,
                0,
                (1, "turn_started", ran),
                (
                    2,
                    "turn_interrupted",
                    {**ran, "interrupt_message_id": str(interrupt.id)},
                ),
            )
        assert stopped.status_code == 200, stopped.content
        assert _names(inline, nudge_tasks.stop_harness_nudge_sandbox.name) == []

        # The interrupted turn's workspace and its ack still reach the platform.
        checkpoint = _commit_checkpoint(
            client,
            conversation,
            headers,
            with_authoring_basis(
                _authoring(contract=REFUND), command.payload["_environment_revision"]
            ),
        )
        with django_capture_on_commit_callbacks(execute=True):
            ended = _send(
                client,
                conversation,
                headers,
                command.sequence,
                (3, "checkpoint_committed", checkpoint),
                (4, "turn_completed", {**ran, "outcome": "interrupted"}),
            )
        assert ended.status_code == 200, ended.content

        conversation.refresh_from_db()
        assert conversation.command_acked_through >= command.sequence
        capability.lease.refresh_from_db()
        assert capability.lease.state == "expired"
        assert _names(inline, nudge_tasks.stop_harness_nudge_sandbox.name) == [
            [str(conversation.id), "sandbox-old"]
        ]
