"""Unsaved edits (drafts) to an environment's versions, and Save and Discard on them."""

import json
import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.http import QueryDict
from django.urls import Resolver404, resolve
from django.utils import timezone

from accounts.models.organization import Organization
from accounts.services.workspace_membership import ensure_org_membership
from simulate.models import (
    HarnessEnvironmentVersion,
    HarnessNudge,
    HarnessSuiteMember,
    HarnessSuiteValidation,
    HarnessSuiteVersion,
    HostedHarnessConversation,
    HostedHarnessConversationEvent,
    HostedHarnessConversationLease,
    HostedHarnessConversationMessage,
    HostedHarnessJob,
    HostedHarnessScenario,
    HostedHarnessStageOutput,
)
from simulate.services import harness_scenarios
from simulate.services.harness_provider import serialize_job
from simulate.services.harness_versioning import archives, drafts
from simulate.services.harness_versioning.run_warnings import preview_run
from simulate.services.harness_versioning.versions import move_default, record_authoring
from simulate.services.hosted_harness import HostedHarnessError, VersionPicks
from simulate.services.hosted_harness_conversation import (
    enqueue_message,
    ensure_conversation,
    promote_conversation_checkpoint,
)
from simulate.services.hosted_harness_gateway import (
    DRAFT_BASIS_FILE,
    authoring_basis,
    chat_workspace_for,
    draft_basis_token,
    with_authoring_basis,
    with_draft_basis,
)
from simulate.tests import test_harness_versioning_record as record_tests
from simulate.tests.test_harness_amend_archive import NAMES, _key
from simulate.tests.test_harness_versioning_backfill import (  # noqa: F401 - fixture
    storage as storage,
)
from simulate.tests.test_harness_versioning_record import (
    _authoring,
    _resealed_manifest,
    _save_drafts,
)
from simulate.tests.test_harness_versioning_rollback import _built, _rows
from simulate.tests.test_hosted_harness_conversation import _put_conversation_checkpoint

pytestmark = pytest.mark.django_db

BASE = "/simulate/api/harness-environments"
EXTRA = "refund_status"
EXTRA_KEY = _key(EXTRA)
# A chat world edit, and the bundle the chat resealed for it (``_publish`` stamps it
# for the tree it publishes, as ALK's reseal does).
WORLD_EDIT = "create table orders(id int, note text);"
RESEALED = json.dumps({"files": [], "resealed": True})
# The default's own bundle: a chat turn whose reseal failed still holds it.
STALE = json.dumps({"files": []})


@pytest.fixture(autouse=True)
def versioning_on(settings):
    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = False


def _conversation(environment):
    conversation, _command, _ = enqueue_message(
        environment, content="open", client_request_id=f"open-{environment.id}"
    )
    _finish(environment)
    return conversation


def _finish(environment):
    """Every queued command's turn has finished."""
    HostedHarnessConversationMessage.no_workspace_objects.filter(
        conversation__job=environment, role="user"
    ).update(state="completed")


def _publish(
    environment,
    storage,
    names=(*NAMES[:2], EXTRA),
    *,
    sequence,
    token=...,
    conversation=None,
    user=None,
    origin=None,
    contract=None,
    **files,
):
    """A chat turn's save, as a real sandbox makes it: on the composed workspace, so it
    carries that composition's token, unless ``token`` says otherwise, and with the
    bundle the chat resealed for its scenarios, unless ``files`` names another."""
    if files.get("environment-bundle/manifest.json", RESEALED) == RESEALED:
        files["environment-bundle/manifest.json"] = _chat_bundle(
            names, contract=contract, **files
        )
    server_payload = {"_author_id": str(user.id)} if user is not None else {}
    if origin:
        server_payload["_origin"] = origin
    conversation, command, _ = enqueue_message(
        conversation.job if conversation is not None else environment,
        content="edit",
        client_request_id=f"edit-{sequence}",
        server_payload=server_payload,
    )
    if token is ...:
        token = drafts.compose_workspace(environment, conversation).token
    body = with_authoring_basis(
        _authoring(list(names), contract=contract, **files),
        command.payload["_environment_revision"],
    )
    if token:
        body = with_draft_basis(body, token)
    key, digest = _checkpoint(conversation, storage, body)
    try:
        promote_conversation_checkpoint(
            conversation,
            object_key=key,
            digest=digest,
            sequence=sequence,
            expected_revision=command.payload["_environment_revision"],
            command=command,
        )
    finally:
        _finish(environment)
        environment.refresh_from_db()
    return conversation


def _chat_bundle(names, *, contract=None, **files):
    """The bundle a chat reseal makes for this tree, stamped with what it carried."""
    tree = archives.read_archive(_authoring(list(names), contract=contract, **files))
    return _resealed_manifest(tree, **json.loads(RESEALED))


def _checkpoint(conversation, storage, body):
    """Store a chat checkpoint as the conversation's latest workspace."""
    key, digest = _put_conversation_checkpoint(conversation, storage, body)
    HostedHarnessConversation.no_workspace_objects.filter(id=conversation.id).update(
        latest_workspace_object_key=key, latest_workspace_digest=digest
    )
    return key, digest


def _draft(environment, piece="suite"):
    return drafts.open_draft(environment, piece)


def _members(version):
    return dict(
        HarnessSuiteMember.no_workspace_objects.filter(
            suite_version=version
        ).values_list("scenario_key", "number")
    )


class TestChatWritesADraft:
    def test_a_chat_save_makes_one_suite_draft_and_no_version(
        self, user, workspace, storage
    ):
        environment = _built(user, workspace, "d-chat")
        s1 = environment.suite_version
        metadata = dict(environment.payload["metadata"])
        rows = set(
            HostedHarnessScenario.no_workspace_objects.filter(
                job=environment
            ).values_list("scenario_key", flat=True)
        )

        _publish(environment, storage, sequence=10, user=user)

        draft = _draft(environment)
        assert (draft.status, draft.revision, draft.parent_id) == ("draft", 1, s1.id)
        assert draft.origin == "chat" and draft.version_number is None
        assert draft.archive_object_key.startswith("harness-suites/")
        assert draft.created_by == user and draft.last_edited_by == user
        assert _draft(environment, "environment") is None
        assert environment.suite_version_id == s1.id
        assert environment.scenario_count == 2
        assert environment.payload["metadata"]["authoring_object_key"] == (
            metadata["authoring_object_key"]
        )
        assert rows == set(
            HostedHarnessScenario.no_workspace_objects.filter(
                job=environment
            ).values_list("scenario_key", flat=True)
        )
        assert (
            HarnessSuiteVersion.no_workspace_objects.filter(
                job=environment, status="active"
            ).count()
            == 1
        )
        assert not HarnessSuiteValidation.no_workspace_objects.filter(
            suite_version=draft
        ).exists()

    def test_a_second_save_updates_the_same_draft(
        self, user, workspace, storage, django_user_model
    ):
        environment = _built(user, workspace, "d-chat-two")
        conversation = _publish(environment, storage, sequence=10, user=user)
        other = django_user_model.objects.create_user(
            email="second-editor@futureagi.com",
            password="x",
            name="Second Editor",
            organization=user.organization,
        )
        ensure_org_membership(other, user.organization)

        _publish(
            environment,
            storage,
            NAMES,
            sequence=20,
            conversation=conversation,
            user=other,
        )

        [draft] = HarnessSuiteVersion.no_workspace_objects.filter(
            job=environment, status="draft"
        )
        assert draft.revision == 2
        assert (draft.created_by, draft.last_edited_by) == (user, other)

    def test_an_author_outside_the_organization_is_not_recorded(
        self, user, workspace, storage, django_user_model
    ):
        environment = _built(user, workspace, "d-chat-foreign")
        foreign_org = Organization.objects.create(name="Foreign org for drafts")
        foreign = django_user_model.objects.create_user(
            email="foreign-editor@futureagi.com",
            password="x",
            name="Foreign Editor",
            organization=foreign_org,
        )
        ensure_org_membership(foreign, foreign_org)

        _publish(environment, storage, sequence=10, user=foreign)

        draft = _draft(environment)
        assert (draft.created_by, draft.last_edited_by) == (None, None)

    def test_a_legacy_member_without_a_membership_row_is_recorded(
        self, user, workspace, storage, django_user_model
    ):
        environment = _built(user, workspace, "d-chat-legacy")
        legacy = django_user_model.objects.create_user(
            email="legacy-editor@futureagi.com",
            password="x",
            name="Legacy Editor",
            organization=user.organization,
        )

        _publish(environment, storage, sequence=10, user=legacy)

        draft = _draft(environment)
        assert (draft.created_by, draft.last_edited_by) == (legacy, legacy)

    def test_a_deactivated_member_is_not_recorded(
        self, user, workspace, storage, django_user_model
    ):
        environment = _built(user, workspace, "d-chat-deactivated")
        former = django_user_model.objects.create_user(
            email="former-editor@futureagi.com",
            password="x",
            name="Former Editor",
            organization=user.organization,
        )
        membership = ensure_org_membership(former, user.organization)
        type(membership).no_workspace_objects.filter(id=membership.id).update(
            is_active=False
        )

        _publish(environment, storage, sequence=10, user=former)

        draft = _draft(environment)
        assert (draft.created_by, draft.last_edited_by) == (None, None)

    def test_saving_the_defaults_content_discards_the_draft(
        self, user, workspace, storage
    ):
        environment = _built(user, workspace, "d-chat-back")
        conversation = _publish(environment, storage, sequence=10)

        _publish(
            environment, storage, NAMES[:2], sequence=20, conversation=conversation
        )

        assert _draft(environment) is None
        discarded = HarnessSuiteVersion.no_workspace_objects.get(
            job=environment, status="discarded"
        )
        assert discarded.discard_reason == "user"

    def test_a_contract_edit_makes_only_an_environment_draft(
        self, user, workspace, storage
    ):
        environment = _built(user, workspace, "d-chat-contract")

        _publish(
            environment,
            storage,
            NAMES[:2],
            sequence=10,
            contract={"agent": "support", "tools": [{"name": "lookup"}]},
        )

        draft = _draft(environment, "environment")
        assert draft.parent_id == environment.environment_version_id
        assert draft.diff["contract"]["tools"]["added"] == ["lookup"]
        assert _draft(environment) is None

    def test_a_save_from_the_same_sandbox_twice_is_not_a_conflict(
        self, user, workspace, storage
    ):
        environment = _built(user, workspace, "d-chat-same")
        conversation = _conversation(environment)
        token = drafts.compose_workspace(environment, conversation).token

        _publish(
            environment,
            storage,
            sequence=10,
            token=token,
            conversation=conversation,
        )
        _publish(
            environment,
            storage,
            NAMES,
            sequence=20,
            token=token,
            conversation=conversation,
        )

        assert _draft(environment).revision == 2


class TestTwoWriters:
    def test_an_amend_during_a_chat_turn_refuses_the_chats_save(
        self, user, workspace, storage, auth_client
    ):
        environment = _built(user, workspace, "d-two")
        conversation = _conversation(environment)
        token = drafts.compose_workspace(environment, conversation).token
        _amend(auth_client, environment, NAMES[1], "tests", "edited")
        amended = _draft(environment)
        workspace_key = HostedHarnessConversation.no_workspace_objects.get(
            id=conversation.id
        ).latest_workspace_object_key

        with pytest.raises(HostedHarnessError) as refused:
            _publish(
                environment,
                storage,
                sequence=10,
                token=token,
                conversation=conversation,
            )

        assert (refused.value.code, refused.value.status_code) == (
            "draft_changed",
            409,
        )
        assert refused.value.message.startswith("the unsaved changes were edited")
        draft = _draft(environment)
        assert (draft.id, draft.revision, draft.hash) == (
            amended.id,
            1,
            amended.hash,
        )
        conversation.refresh_from_db()
        assert conversation.latest_workspace_object_key != workspace_key
        basis = environment.payload["metadata"]["conversation_checkpoints"][
            str(conversation.id)
        ]["draft_basis"]
        assert basis["token"] != token
        assert basis["suite"] == {"id": str(amended.id), "revision": 1}
        rebased = storage.objects[conversation.latest_workspace_object_key]
        assert draft_basis_token(rebased) == basis["token"]
        files = archives.read_archive(rebased)
        assert json.loads(files["scenarios.json"])[1]["tests"] == "edited"

    def test_a_save_without_a_token_while_a_draft_is_open_is_refused(
        self, user, workspace, storage
    ):
        environment = _built(user, workspace, "d-no-token")
        _publish(environment, storage, sequence=10)

        with pytest.raises(HostedHarnessError) as refused:
            _publish(environment, storage, NAMES, sequence=20, token=None)

        assert refused.value.code == "draft_changed"

    def test_a_save_without_a_token_and_no_draft_records(
        self, user, workspace, storage
    ):
        environment = _built(user, workspace, "d-no-token-ok")

        _publish(environment, storage, sequence=10, token=None)

        assert _draft(environment).revision == 1

    def test_an_old_sandbox_keeps_being_refused_after_a_rebase(
        self, user, workspace, storage
    ):
        environment = _built(user, workspace, "d-old-token")
        conversation = _conversation(environment)
        old = drafts.compose_workspace(environment, conversation).token
        drafts.compose_workspace(environment, conversation)

        for sequence in (10, 20):
            with pytest.raises(HostedHarnessError) as refused:
                _publish(
                    environment,
                    storage,
                    sequence=sequence,
                    token=old,
                    conversation=conversation,
                )
            assert refused.value.code == "draft_changed"
        assert _draft(environment) is None

    def test_a_late_save_from_an_old_sandbox_leaves_the_newer_one_valid(
        self, user, workspace, storage
    ):
        environment = _built(user, workspace, "d-late-old")
        conversation = _conversation(environment)
        old = drafts.compose_workspace(environment, conversation).token
        new = drafts.compose_workspace(environment, conversation).token

        with pytest.raises(HostedHarnessError) as refused:
            _publish(
                environment,
                storage,
                sequence=10,
                token=old,
                conversation=conversation,
            )
        assert refused.value.code == "draft_changed"
        _publish(
            environment,
            storage,
            sequence=20,
            token=new,
            conversation=conversation,
        )

        assert _draft(environment).revision == 1

    def test_an_old_sandbox_restarts_after_a_mid_turn_rebase(
        self, user, workspace, storage, auth_client
    ):
        environment = _built(user, workspace, "d-old-restart")
        conversation = _conversation(environment)
        old = drafts.compose_workspace(environment, conversation).token
        lease = _lease(conversation)
        _amend(auth_client, environment, NAMES[1], "tests", "edited")
        _, command, _ = enqueue_message(
            environment, content="edit", client_request_id="mid-turn"
        )
        HostedHarnessConversation.no_workspace_objects.filter(
            id=conversation.id
        ).update(active_invocation_id="turn-1")
        revision = command.payload["_environment_revision"]

        def save(names, sequence, **kwargs):
            body = with_draft_basis(
                with_authoring_basis(_authoring(list(names)), revision), old
            )
            key, digest = _checkpoint(conversation, storage, body)
            with pytest.raises(HostedHarnessError) as refused:
                promote_conversation_checkpoint(
                    conversation,
                    object_key=key,
                    digest=digest,
                    sequence=sequence,
                    expected_revision=revision,
                    command=command,
                    **kwargs,
                )
            assert refused.value.code == "draft_changed"
            lease.refresh_from_db()
            return lease.state

        # The builder's own publish mid-turn rebases the chat but keeps its sandbox.
        assert save(NAMES, 10, retire_runtime=False) == "active"
        _finish(environment)
        HostedHarnessConversation.no_workspace_objects.filter(
            id=conversation.id
        ).update(active_invocation_id=None)

        assert save(NAMES[:2], 20) == "expired"
        token = draft_basis_token(
            chat_workspace_for(environment, conversation, chat_state=None)
        )
        _publish(
            environment, storage, sequence=30, token=token, conversation=conversation
        )
        assert _draft(environment).revision == 2

    def test_a_stale_basis_still_answers_stale_and_rebases_onto_the_draft(
        self, user, workspace, storage
    ):
        environment = _built(user, workspace, "d-stale-basis")
        conversation = _publish(environment, storage, sequence=10)
        draft = _draft(environment)
        _, command, _ = enqueue_message(
            environment, content="edit", client_request_id="stale"
        )
        body = with_authoring_basis(_authoring(list(NAMES)), "sha256:older")
        key, digest = _checkpoint(conversation, storage, body)

        with pytest.raises(HostedHarnessError) as refused:
            promote_conversation_checkpoint(
                conversation,
                object_key=key,
                digest=digest,
                sequence=20,
                expected_revision="sha256:older",
                command=command,
            )

        assert refused.value.code == "conversation_checkpoint_stale"
        conversation.refresh_from_db()
        rebased = storage.objects[conversation.latest_workspace_object_key]
        assert f"scenarios/{EXTRA}/scenario.json" in archives.read_archive(rebased)
        assert _draft(environment).revision == draft.revision

    def test_a_compose_without_a_publish_never_conflicts_with_the_first_publish(
        self, user, workspace, storage
    ):
        from simulate.services.hosted_harness_conversation import (
            publish_conversation_workspace,
        )

        environment = _built(user, workspace, "d-compose-first")
        conversation = _conversation(environment)
        token = drafts.compose_workspace(environment, conversation).token
        body = with_draft_basis(
            with_authoring_basis(
                _authoring([*NAMES[:2], EXTRA]),
                environment.payload["metadata"]["authoring_revision"],
            ),
            token,
        )
        _key_, digest = _checkpoint(conversation, storage, body)

        # No event acknowledged yet: the save comes at sequence 0.
        published = publish_conversation_workspace(conversation, digest=digest)

        assert published["applied"] is True, published
        assert _draft(environment).revision == 1


class TestReporting:
    def _turn(self, conversation, storage, body, command, sequence):
        from simulate.tests.test_harness_versioning_runs import _chat_turn

        return _chat_turn(conversation, storage, body, sequence, command)

    def test_a_refused_draft_save_is_told_in_chat(self, user, workspace, storage):
        from simulate.tasks import hosted_harness_conversation as tasks

        promote = tasks.promote_hosted_harness_conversation_checkpoint._original_func
        environment = _built(user, workspace, "d-report")
        _publish(environment, storage, sequence=5)
        conversation, command, _ = enqueue_message(
            environment, content="edit", client_request_id="report"
        )
        body = with_authoring_basis(
            _authoring(list(NAMES)), command.payload["_environment_revision"]
        )
        turn = self._turn(conversation, storage, body, command, 10)

        assert promote(str(conversation.id), turn) == ""

        message = HostedHarnessConversationMessage.no_workspace_objects.get(
            conversation=conversation, role="assistant"
        )
        assert message.state == "failed"
        assert message.content.startswith(
            "These changes were not saved: the unsaved changes were edited elsewhere"
        )

    def test_a_stale_save_is_told_in_chat_without_versions_too(
        self, user, workspace, storage, settings
    ):
        from simulate.tasks import hosted_harness_conversation as tasks
        from simulate.tests.test_hosted_harness_conversation import _chat_checkpoint

        settings.HARNESS_VERSIONING_ENABLED = False
        promote = tasks.promote_hosted_harness_conversation_checkpoint._original_func
        environment = record_tests._environment(user, workspace, "d-report-legacy")
        storage.objects[environment.payload["metadata"]["authoring_object_key"]] = (
            _chat_checkpoint(NAMES[:2], one_liner="original")
        )
        conversation, command, _ = enqueue_message(
            environment, content="edit", client_request_id="legacy-stale"
        )
        body = with_authoring_basis(
            _chat_checkpoint(NAMES, one_liner="x"), "sha256:older"
        )
        turn = self._turn(conversation, storage, body, command, 10)

        assert promote(str(conversation.id), turn) == ""

        message = HostedHarnessConversationMessage.no_workspace_objects.get(
            conversation=conversation, role="assistant"
        )
        assert message.content == (
            "These changes were not saved: the environment changed after this chat "
            "workspace was loaded"
        )


def _run_of(environment, key):
    from simulate.services.hosted_harness import create_selected_harness_run

    run, _ = create_selected_harness_run(
        environment, scenario_keys=[_key(NAMES[0])], trials=1, idempotency_key=key
    )
    return run


def _lease(conversation, state="active"):
    return HostedHarnessConversationLease.no_workspace_objects.create(
        conversation=conversation,
        provider_ref=f"sandbox-{conversation.id}",
        state=state,
        token_hash="t" * 64,
        fence_hash="f" * 64,
        expires_at=timezone.now() + timedelta(minutes=10),
        heartbeat_at=timezone.now(),
    )


def _amend(auth_client, environment, name, field, value):
    response = auth_client.post(
        f"/simulate/api/harness-jobs/{environment.id}/scenarios/amend/",
        {
            "changes": [
                {"op": "set_field", "scenario": name, "field": field, "value": value}
            ]
        },
        format="json",
    )
    assert response.status_code == 200, response.content
    environment.refresh_from_db()
    return response.json()


class TestLeases:
    def test_an_amend_leaves_a_running_turns_sandbox_and_expires_an_idle_one(
        self, user, workspace, storage, auth_client, django_capture_on_commit_callbacks
    ):
        environment = _built(user, workspace, "d-leases")
        busy = _conversation(environment)
        HostedHarnessConversation.no_workspace_objects.filter(id=busy.id).update(
            active_invocation_id="turn-1"
        )
        running = _lease(busy)
        run = _run_of(environment, "lease-run")
        idle_conversation = ensure_conversation(run)
        idle = _lease(idle_conversation)

        with django_capture_on_commit_callbacks(execute=True):
            _amend(auth_client, environment, NAMES[0], "tests", "edited")

        running.refresh_from_db()
        idle.refresh_from_db()
        assert running.state == "active"
        assert idle.state == "expired"

        HostedHarnessConversation.no_workspace_objects.filter(id=busy.id).update(
            active_invocation_id=None
        )
        assert drafts.retire_chat_runtime(environment) == 1
        running.refresh_from_db()
        assert running.state == "expired"


class TestSave:
    def test_save_makes_the_draft_the_next_default(
        self, user, workspace, storage, django_capture_on_commit_callbacks
    ):
        environment = _built(user, workspace, "d-save")
        s1 = environment.suite_version
        first_pair = environment.payload["metadata"]["authoring_object_key"]
        _publish(environment, storage, sequence=10)
        draft = _draft(environment)
        number = next(
            one["number"] for one in draft.documents["pairs"] if one["key"] == EXTRA_KEY
        )

        with django_capture_on_commit_callbacks(execute=True):
            body = _save_drafts(environment, user=user)

        suite = environment.suite_version
        assert suite.id == draft.id
        assert (suite.status, suite.version_number, suite.parent_id) == (
            "active",
            2,
            s1.id,
        )
        assert suite.created_by == user and suite.diff["added"] == [EXTRA_KEY]
        assert body["saved"]["suite"]["id"] == str(suite.id)
        assert body["saved"]["suite"]["is_default"] is True
        assert environment.scenario_count == 3
        assert environment.payload["scenario_count"] == 3
        row = HostedHarnessScenario.no_workspace_objects.get(
            job=environment, scenario_key=EXTRA_KEY
        )
        assert row.number == number == max(_members(s1).values()) + 1
        assert _members(suite)[EXTRA_KEY] == number
        assert [
            one["name"]
            for one in HostedHarnessStageOutput.no_workspace_objects.get(
                job=environment, kind="scenarios"
            ).data
        ] == [*NAMES[:2], EXTRA]
        metadata = environment.payload["metadata"]
        assert metadata["authoring_object_key"] != first_pair
        assert f"{environment.environment_version_id}:{suite.id}" in (
            metadata["default_pairs"]
        )
        assert metadata["authoring_revision"] in suite.documents["authoring_revisions"]
        assert not HarnessSuiteValidation.no_workspace_objects.filter(
            suite_version=suite
        ).exists()
        codes = {
            one["code"] for one in preview_run(environment, VersionPicks())["warnings"]
        }
        assert "suite_unproved" in codes

    def test_a_dropped_scenario_is_pruned_on_save(self, user, workspace, storage):
        environment = _built(user, workspace, "d-save-drop")
        _publish(environment, storage, NAMES[:1], sequence=10)

        _save_drafts(environment)

        assert HostedHarnessScenario.all_objects.get(
            job=environment, scenario_key=_key(NAMES[1])
        ).deleted

    def test_save_after_a_roll_back_is_stale(
        self, user, workspace, storage, auth_client
    ):
        environment = _built(user, workspace, "d-stale")
        s1 = environment.suite_version
        _publish(environment, storage, sequence=10)
        _save_drafts(environment)
        _publish(environment, storage, NAMES, sequence=20)
        move_default(
            environment,
            "suite",
            s1.id,
            expected_default_id=environment.suite_version_id,
        )
        environment.refresh_from_db()
        draft = _draft(environment)

        response = auth_client.post(
            f"{BASE}/{environment.id}/drafts/save/",
            {
                "drafts": [
                    {
                        "piece": "suite",
                        "expected_parent_id": str(draft.parent_id),
                        "expected_revision": draft.revision,
                    }
                ],
                "idempotency_key": "stale",
            },
            format="json",
        )

        assert response.status_code == 409
        assert response.json()["error"] == "draft_stale"
        assert response.json()["piece"] == "suite"
        environment.refresh_from_db()
        assert environment.suite_version_id == s1.id
        listed = auth_client.get(f"{BASE}/{environment.id}/drafts/").json()
        assert listed["suite"]["stale"] is True
        assert listed["suite"]["default_label"] == "v1"
        assert listed["suite"]["parent_label"] == "v2"

    @pytest.mark.parametrize(
        "field, value", [("expected_revision", 7), ("expected_parent_id", None)]
    )
    def test_a_changed_draft_refuses_save(self, user, workspace, storage, field, value):
        environment = _built(user, workspace, f"d-changed-{field}")
        _publish(environment, storage, sequence=10)
        draft = _draft(environment)
        wanted = {
            "expected_parent_id": draft.parent_id,
            "expected_revision": draft.revision,
            field: value,
        }

        with pytest.raises(HostedHarnessError) as refused:
            drafts.save_drafts(
                environment,
                {"suite": drafts.SaveRequest(**wanted)},
                idempotency_key="k",
                user=user,
            )

        assert refused.value.code == "draft_changed"

    def test_the_same_key_saves_once(self, user, workspace, storage):
        environment = _built(user, workspace, "d-idem")
        _publish(environment, storage, sequence=10)
        first = _save_drafts(environment, key="same")
        count = HarnessSuiteVersion.no_workspace_objects.filter(job=environment).count()

        again = drafts.save_drafts(
            environment,
            {"suite": drafts.SaveRequest(None)},
            idempotency_key="same",
            user=user,
        )

        assert again["saved"]["suite"]["id"] == first["saved"]["suite"]["id"]
        assert (
            HarnessSuiteVersion.no_workspace_objects.filter(job=environment).count()
            == count
        )
        with pytest.raises(HostedHarnessError) as refused:
            drafts.save_drafts(
                environment,
                {"environment": drafts.SaveRequest(None)},
                idempotency_key="same",
                user=user,
            )
        assert refused.value.code == "idempotency_key_reused"

    def test_only_the_newest_save_keys_are_kept(self, user, workspace, storage):
        environment = _built(user, workspace, "d-idem-trim")
        payload = dict(environment.payload)
        payload["metadata"] = {
            **payload["metadata"],
            "draft_saves": {
                f"old-{index}": {
                    "pieces": {},
                    "at": f"2026-01-01T00:00:{index:02d}+00:00",
                }
                for index in range(50)
            },
        }
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            payload=payload
        )
        environment.refresh_from_db()
        _publish(environment, storage, sequence=10)

        _save_drafts(environment, key="newest")

        kept = environment.payload["metadata"]["draft_saves"]
        assert len(kept) == 50 and "newest" in kept and "old-0" not in kept

    def test_a_draft_equal_to_an_older_version_reuses_it(
        self, user, workspace, storage
    ):
        environment = _built(user, workspace, "d-twin")
        s1 = environment.suite_version
        _publish(environment, storage, sequence=10)
        _save_drafts(environment, key="one")
        _publish(environment, storage, NAMES[:2], sequence=20)
        draft = _draft(environment)

        body = _save_drafts(environment, key="two")

        assert environment.suite_version_id == s1.id
        assert body["saved"]["suite"]["id"] == str(s1.id)
        draft.refresh_from_db()
        assert draft.status == "discarded"
        assert draft.documents["reused_version_id"] == str(s1.id)

    def test_save_and_the_environment_nudge(
        self, user, workspace, storage, django_capture_on_commit_callbacks
    ):
        environment = _built(user, workspace, "d-both")
        e1 = environment.environment_version
        _publish(
            environment,
            storage,
            sequence=10,
            contract={"agent": "support", "tools": [{"name": "lookup"}]},
        )
        with (
            patch(
                "simulate.services.harness_versioning.nudges.on_environment_default_moved"
            ) as moved,
            django_capture_on_commit_callbacks(execute=True),
        ):
            body = _save_drafts(environment)

        e2, s2 = environment.environment_version, environment.suite_version
        assert set(body["saved"]) == {"environment", "suite"}
        assert e2.version_number == 2 and s2.version_number == 2
        assert s2.authored_against_id == e2.id
        moved.assert_called_once_with(
            environment.id,
            from_environment_version_id=e1.id,
            environment_version_id=e2.id,
        )

    def test_a_suite_only_save_raises_no_environment_nudge(
        self, user, workspace, storage, django_capture_on_commit_callbacks
    ):
        environment = _built(user, workspace, "d-suite-only")
        _publish(environment, storage, sequence=10)
        with (
            patch(
                "simulate.services.harness_versioning.nudges.on_environment_default_moved"
            ) as moved,
            django_capture_on_commit_callbacks(execute=True),
        ):
            _save_drafts(environment)

        moved.assert_not_called()

    def test_save_records_the_agent_an_unpinned_environment_is_pinned_to(
        self, user, workspace, storage
    ):
        from simulate.tests.test_harness_versioning_backfill import (
            _agent_version,
            _definition,
        )

        environment = _built(user, workspace, "d-agent")
        _publish(
            environment,
            storage,
            NAMES[:2],
            sequence=10,
            contract={"agent": "support", "tools": [{"name": "lookup"}]},
        )
        agent = _agent_version(_definition(user, workspace))

        def pin(locked):
            locked.agent_version = agent
            return True

        with patch(
            "simulate.services.harness_agent_versions.fill_agent_pin", side_effect=pin
        ):
            _save_drafts(environment)

        assert environment.agent_version_id == agent.id
        assert environment.environment_version.agent_version_id == agent.id

    def test_save_is_refused_while_an_update_runs(self, user, workspace, storage):
        environment = _built(user, workspace, "d-rebuild")
        _publish(environment, storage, sequence=10)
        HarnessNudge.no_workspace_objects.create(
            job=environment,
            organization=environment.organization,
            workspace=environment.workspace,
            target="suite",
            state="running",
        )

        for call in (
            lambda: _save_drafts(environment),
            lambda: drafts.discard_draft(environment, "suite", user=user),
        ):
            with pytest.raises(HostedHarnessError) as refused:
                call()
            assert refused.value.code == "rebuild_running"
        assert _draft(environment) is not None


class TestChatGuard:
    def _environment(self, user, workspace, storage, key):
        environment = _built(user, workspace, key)
        conversation = _publish(environment, storage, sequence=10)
        return environment, conversation

    def _refused(self, environment, user):
        for call in (
            lambda: drafts.save_drafts(
                environment,
                {"suite": drafts.SaveRequest(_draft(environment).parent_id)},
                idempotency_key="guard",
                user=user,
            ),
            lambda: drafts.discard_draft(environment, "suite", user=user),
        ):
            with pytest.raises(HostedHarnessError) as refused:
                call()
            assert refused.value.code == "chat_working"
        assert _draft(environment) is not None

    def test_a_running_turn_holds_save_and_discard(self, user, workspace, storage):
        environment, conversation = self._environment(
            user, workspace, storage, "d-guard-run"
        )
        HostedHarnessConversation.no_workspace_objects.filter(
            id=conversation.id
        ).update(active_invocation_id="turn-2")

        self._refused(environment, user)

    def test_a_queued_command_holds_save(self, user, workspace, storage):
        environment, _conversation_ = self._environment(
            user, workspace, storage, "d-guard-queued"
        )
        enqueue_message(environment, content="more", client_request_id="queued")

        self._refused(environment, user)

    def _turn(self, conversation, *, ago, digest="sha256:unpublished", emitted=None):
        """A finished turn the server received ``ago``; the sandbox stamped it
        ``emitted`` ago, by default the same."""
        when = timezone.now() - ago
        for sequence, kind, payload in (
            (50, "checkpoint_committed", {"digest": digest}),
            (51, "turn_completed", {"outcome": "success"}),
        ):
            HostedHarnessConversationEvent.no_workspace_objects.create(
                event_id=f"{conversation.id}:{sequence}",
                conversation=conversation,
                sequence=sequence,
                kind=kind,
                payload=payload,
                digest=digest,
                emitted_at=when if emitted is None else timezone.now() - emitted,
                invocation_id="turn-9",
            )
        HostedHarnessConversationEvent.no_workspace_objects.filter(
            conversation=conversation, sequence__in=(50, 51)
        ).update(created_at=when)

    def test_a_publish_on_its_way_holds_save_for_a_minute(
        self, user, workspace, storage
    ):
        environment, conversation = self._environment(
            user, workspace, storage, "d-guard-pending"
        )
        self._turn(conversation, ago=timedelta(seconds=5))

        self._refused(environment, user)

    def test_an_old_or_published_turn_does_not_hold_save(
        self, user, workspace, storage
    ):
        environment, conversation = self._environment(
            user, workspace, storage, "d-guard-old"
        )
        self._turn(conversation, ago=timedelta(seconds=61))
        assert not drafts.chat_busy(environment)

        published = environment.payload["metadata"]["conversation_checkpoints"][
            str(conversation.id)
        ]["digest"]
        HostedHarnessConversationEvent.no_workspace_objects.filter(
            conversation=conversation
        ).update(
            created_at=timezone.now(),
            emitted_at=timezone.now(),
            payload={"digest": published},
        )
        HostedHarnessConversationEvent.no_workspace_objects.filter(
            conversation=conversation, kind="turn_completed"
        ).update(payload={"outcome": "success"})
        assert not drafts.chat_busy(environment)

    def test_the_hold_runs_on_the_servers_clock(self, user, workspace, storage):
        environment, conversation = self._environment(
            user, workspace, storage, "d-guard-clock"
        )
        self._turn(conversation, ago=timedelta(seconds=61), emitted=-timedelta(hours=1))
        assert not drafts.chat_busy(environment)

        HostedHarnessConversationEvent.no_workspace_objects.filter(
            conversation=conversation
        ).update(
            created_at=timezone.now(), emitted_at=timezone.now() - timedelta(hours=1)
        )
        assert drafts.chat_busy(environment)

    def test_an_unanswered_question_does_not_hold_save(self, user, workspace, storage):
        environment, conversation = self._environment(
            user, workspace, storage, "d-guard-question"
        )
        question = uuid.uuid4()
        HostedHarnessConversation.no_workspace_objects.filter(
            id=conversation.id
        ).update(
            active_invocation_id="turn-2",
            state=HostedHarnessConversation.State.WAITING_FOR_USER,
            blocking_input={"message_id": str(question), "kind": "question_requested"},
        )
        lease = _lease(conversation)

        assert not drafts.chat_busy(environment)
        assert drafts.retire_chat_runtime(environment) == 0
        lease.refresh_from_db()
        assert lease.state == "active"

        # Once answered, the turn runs on and holds Save again.
        HostedHarnessConversationMessage.no_workspace_objects.create(
            conversation=conversation,
            sequence=99,
            role="user",
            content="yes",
            state="completed",
            reply_to=question,
        )
        assert drafts.chat_busy(environment)

    def test_a_runs_chat_counts_too(self, user, workspace, storage):
        environment, _conversation_ = self._environment(
            user, workspace, storage, "d-guard-runchat"
        )
        run = _run_of(environment, "guard-run")
        HostedHarnessConversation.no_workspace_objects.filter(
            id=ensure_conversation(run).id
        ).update(active_invocation_id="turn-1")

        assert drafts.chat_busy(environment)


class TestPairLadder:
    def test_the_chats_own_bundle_is_used_when_it_was_sealed_for_the_pair(
        self, user, workspace, storage
    ):
        environment = _built(user, workspace, "d-ladder-own")
        _publish(environment, storage, sequence=10)
        kept = _draft(environment).documents["workspace_sealed"]
        assert kept["resealed"] is True

        _save_drafts(environment)

        metadata = environment.payload["metadata"]
        assert metadata["default_pair_sealed"] is True
        assert metadata["sealed_bundle_object_key"] == kept["key"]
        pair = archives.read_archive(storage.objects[metadata["authoring_object_key"]])
        assert (
            pair["environment-bundle/manifest.json"]
            == _chat_bundle((*NAMES[:2], EXTRA)).encode()
        )

    def _certified(self, environment, storage, settings):
        """Save with bundle preparation on: runs certify the unsealed pair it makes."""
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        versions = "simulate.services.harness_versioning.versions"
        with (
            patch(f"{versions}._default_runtime", return_value=object()),
            patch(f"{versions}._cached_sealed", return_value=(None, "")),
        ):
            _save_drafts(environment)
        metadata = environment.payload["metadata"]
        assert metadata["default_pair_sealed"] is False
        assert "sealed_bundle_object_key" not in metadata
        pair = archives.read_archive(storage.objects[metadata["authoring_object_key"]])
        assert not any(path.startswith("environment-bundle/") for path in pair)

    def _not_runnable(self, environment):
        """Certify off: an agent's environment is told to turn preparation on."""
        before = environment.payload["metadata"]["authoring_object_key"]
        with (
            patch.object(drafts, "_default_runtime", return_value=object()),
            pytest.raises(HostedHarnessError) as refused,
        ):
            _save_drafts(environment)
        assert refused.value.code == "draft_not_runnable"
        assert "turn on bundle preparation" in str(refused.value)
        environment.refresh_from_db()
        assert environment.payload["metadata"]["authoring_object_key"] == before

    def test_a_chat_world_edit_runs_the_chats_resealed_bundle(
        self, user, workspace, storage
    ):
        environment = _built(user, workspace, "d-ladder-world")
        _publish(
            environment,
            storage,
            NAMES[:2],
            sequence=10,
            **{"world.sqlite": WORLD_EDIT},
        )
        kept = _draft(environment, "environment").documents["workspace_sealed"]
        assert kept["resealed"] is True

        _save_drafts(environment)

        # No agent is pinned: the chat's own bundle saves without one.
        assert environment.agent_version_id is None
        metadata = environment.payload["metadata"]
        assert metadata["default_pair_sealed"] is True
        assert metadata["sealed_bundle_object_key"] == kept["key"]
        pair = archives.read_archive(storage.objects[metadata["authoring_object_key"]])
        assert (
            pair["environment-bundle/manifest.json"]
            == _chat_bundle(NAMES[:2], **{"world.sqlite": WORLD_EDIT}).encode()
        )

    def test_a_scenario_edit_after_a_chat_world_edit_is_certified(
        self, user, workspace, storage, auth_client, settings
    ):
        environment = _built(user, workspace, "d-ladder-world-amend")
        _publish(
            environment,
            storage,
            NAMES[:2],
            sequence=10,
            **{"world.sqlite": WORLD_EDIT},
        )
        _amend(auth_client, environment, NAMES[0], "tests", "edited")

        # The chat's bundle was sealed for the suite before the edit, and the default's
        # bundle holds the old world: neither is replayed.
        self._not_runnable(environment)
        self._certified(environment, storage, settings)

    def test_a_world_edit_whose_bundle_was_not_resealed_is_certified(
        self, user, workspace, storage, settings
    ):
        environment = _built(user, workspace, "d-ladder-world-stale")
        _publish(
            environment,
            storage,
            NAMES[:2],
            sequence=10,
            **{"schema.sql": WORLD_EDIT, "environment-bundle/manifest.json": STALE},
        )
        kept = _draft(environment, "environment").documents["workspace_sealed"]
        assert kept["resealed"] is False

        self._not_runnable(environment)
        self._certified(environment, storage, settings)

    def test_a_bundle_not_resealed_for_an_added_scenario_is_certified(
        self, user, workspace, storage, settings
    ):
        environment = _built(user, workspace, "d-ladder-carry")
        _publish(
            environment,
            storage,
            sequence=10,
            **{"environment-bundle/manifest.json": STALE},
        )
        assert _draft(environment).documents["workspace_sealed"]["resealed"] is False

        self._not_runnable(environment)
        self._certified(environment, storage, settings)

    def test_a_scenario_edit_the_bundle_does_not_overlay_is_certified(
        self, user, workspace, storage, auth_client, settings
    ):
        environment = _built(user, workspace, "d-ladder-amend")
        _amend(auth_client, environment, NAMES[0], "tests", "edited")

        self._not_runnable(environment)
        self._certified(environment, storage, settings)

    @pytest.mark.parametrize(
        "head_key",
        [None, "harness-bundles/certified.tar.gz"],
        ids=["backfilled", "run"],
    )
    def test_the_defaults_bundle_reads_as_not_resealed_under_any_key(
        self, user, workspace, storage, settings, head_key
    ):
        environment = _built(user, workspace, f"d-ladder-head-{head_key is None}")
        payload = dict(environment.payload)
        metadata = dict(payload["metadata"])
        # A backfilled default keeps no key; a certified one keeps the run's bundle key.
        metadata.pop("sealed_bundle_object_key")
        if head_key:
            metadata["sealed_bundle_object_key"] = head_key
        payload["metadata"] = metadata
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            payload=payload
        )
        environment.refresh_from_db()
        _publish(
            environment,
            storage,
            NAMES[:2],
            sequence=10,
            **{"schema.sql": WORLD_EDIT, "environment-bundle/manifest.json": STALE},
        )
        kept = _draft(environment, "environment").documents["workspace_sealed"]
        assert kept["resealed"] is False

        self._not_runnable(environment)
        self._certified(environment, storage, settings)

    @pytest.mark.parametrize("discarded", [False, True])
    def test_an_earlier_turns_bundle_is_not_resealed_for_a_later_world_edit(
        self, user, workspace, storage, settings, discarded
    ):
        environment = _built(user, workspace, f"d-ladder-two-turns-{discarded}")
        conversation = _publish(
            environment,
            storage,
            NAMES[:2],
            sequence=10,
            **{"world.sqlite": WORLD_EDIT},
        )
        if discarded:
            assert drafts.discard_draft(environment, "environment", user=user)
        # The next turn's reseal failed: it still holds the first turn's bundle.
        _publish(
            environment,
            storage,
            NAMES[:2],
            sequence=11,
            conversation=conversation,
            **{
                "world.sqlite": "create table orders(id int, note text, at text);",
                "environment-bundle/manifest.json": _chat_bundle(
                    NAMES[:2], **{"world.sqlite": WORLD_EDIT}
                ),
            },
        )
        kept = _draft(environment, "environment").documents["workspace_sealed"]
        assert kept["resealed"] is False

        self._not_runnable(environment)
        self._certified(environment, storage, settings)

    def test_an_added_overlay_field_is_certified(
        self, user, workspace, storage, auth_client, settings
    ):
        # The built scenarios have no max_turns: setting one changes what is sealed.
        environment = _built(user, workspace, "d-ladder-overlay-added")
        _amend(auth_client, environment, NAMES[0], "max_turns", 20)

        self._not_runnable(environment)
        self._certified(environment, storage, settings)

    def test_saving_only_the_suite_does_not_run_the_chats_bundle_for_both(
        self, user, workspace, storage
    ):
        environment = _built(user, workspace, "d-ladder-suite-only")
        _publish(
            environment,
            storage,
            sequence=10,
            **{"world.sqlite": WORLD_EDIT},
        )
        draft = _draft(environment)

        with pytest.raises(HostedHarnessError) as refused:
            drafts.save_drafts(
                environment,
                {"suite": drafts.SaveRequest(draft.parent_id, draft.revision)},
                idempotency_key="suite-only",
                user=user,
            )

        assert refused.value.code == "draft_not_runnable"

    def test_an_environment_without_an_agent_is_told_to_make_the_edit_in_chat(
        self, user, workspace, storage, auth_client, settings
    ):
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        environment = _built(user, workspace, "d-ladder-agentless")
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            agent_version=None
        )
        environment.refresh_from_db()
        _amend(auth_client, environment, NAMES[0], "tests", "edited")

        # No agent version to pin: a run has nothing to prepare the pair with.
        with (
            patch(
                "simulate.services.harness_agent_versions.fill_agent_pin",
                return_value=False,
            ),
            pytest.raises(HostedHarnessError) as refused,
        ):
            _save_drafts(environment)

        assert refused.value.code == "draft_not_runnable"
        assert "make them in chat" in str(refused.value)
        assert "bundle preparation" not in str(refused.value)

    def test_an_overlay_only_scenario_edit_carries_the_current_bundle(
        self, user, workspace, storage, auth_client
    ):
        environment = record_tests._environment(user, workspace, "d-ladder-overlay")
        documents = [record_tests._scenario(name, max_turns=8) for name in NAMES[:2]]
        record_authoring(
            environment,
            record_tests._authoring(
                **{"scenarios.json": json.dumps(documents)},
                **{
                    f"scenarios/{one['name']}/scenario.json": json.dumps(one)
                    for one in documents
                },
            ),
            origin="authoring",
        )
        environment.refresh_from_db()
        _rows(environment)
        sealed_before = environment.payload["metadata"]["sealed_bundle_object_key"]
        _amend(auth_client, environment, NAMES[0], "max_turns", 20)

        _save_drafts(environment)

        metadata = environment.payload["metadata"]
        assert metadata["default_pair_sealed"] is True
        assert metadata["sealed_bundle_object_key"] == sealed_before

    def _unsealed(self, user, workspace, key):
        environment = _built(user, workspace, key)
        payload = dict(environment.payload)
        payload["metadata"] = {
            **payload["metadata"],
            "default_pair_sealed": False,
            "default_pairs": {},
        }
        payload["metadata"].pop("sealed_bundle_object_key", None)
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            payload=payload
        )
        environment.refresh_from_db()
        return environment

    def test_an_unsealed_pair_without_preparation_is_not_runnable(
        self, user, workspace, storage
    ):
        environment = self._unsealed(user, workspace, "d-ladder-none")
        _publish(environment, storage, sequence=10)
        HarnessSuiteVersion.no_workspace_objects.filter(
            id=_draft(environment).id
        ).update(documents={**_draft(environment).documents, "workspace_sealed": {}})
        before = environment.suite_version_id

        with pytest.raises(HostedHarnessError) as refused:
            _save_drafts(environment)

        assert (refused.value.code, refused.value.status_code) == (
            "draft_not_runnable",
            422,
        )
        environment.refresh_from_db()
        assert environment.suite_version_id == before
        assert _draft(environment).status == "draft"

    def test_with_certification_an_unsealed_pair_is_made(
        self, user, workspace, storage, settings
    ):
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        environment = self._unsealed(user, workspace, "d-ladder-certify")
        _publish(environment, storage, sequence=10)
        HarnessSuiteVersion.no_workspace_objects.filter(
            id=_draft(environment).id
        ).update(documents={**_draft(environment).documents, "workspace_sealed": {}})

        versions = "simulate.services.harness_versioning.versions"
        with (
            patch(f"{versions}._default_runtime", return_value=object()),
            patch(f"{versions}._cached_sealed", return_value=(None, "")),
        ):
            _save_drafts(environment)

        assert environment.payload["metadata"]["default_pair_sealed"] is False


class TestRetiredKeys:
    def _dropped(self, user, workspace, storage, key):
        environment = _built(user, workspace, key)
        _publish(environment, storage, NAMES[:1], sequence=1)
        _save_drafts(environment, key="drop")
        return environment

    def test_a_chat_draft_may_not_reuse_a_dropped_key(self, user, workspace, storage):
        environment = self._dropped(user, workspace, storage, "d-retired")

        with pytest.raises(HostedHarnessError) as refused:
            _publish(environment, storage, NAMES[:2], sequence=10)

        assert refused.value.code == "scenario_key_retired"
        assert _draft(environment) is None

    def test_a_draft_forced_past_the_check_is_refused_at_save(
        self, user, workspace, storage
    ):
        environment = self._dropped(user, workspace, storage, "d-retired-save")
        with patch.object(drafts, "_refuse"):
            _publish(environment, storage, NAMES[:2], sequence=10)

        with pytest.raises(HostedHarnessError) as refused:
            _save_drafts(environment)

        assert refused.value.code == "scenario_key_retired"

    def test_an_extend_may_bring_a_dropped_key_back_and_saves_it(
        self, user, workspace, storage
    ):
        environment = self._dropped(user, workspace, storage, "d-retired-extend")

        _publish(environment, storage, NAMES[:2], sequence=10, origin="extend")

        draft = _draft(environment)
        assert draft.origin == "extend"
        assert draft.documents["exempt_keys"] == [_key(NAMES[1]).replace("-", "_")]
        assert {one["key"] for one in draft.documents["pairs"]} == {
            _key(NAMES[0]),
            _key(NAMES[1]),
        }
        # A later chat edit of the same draft is not refused for the key Extend placed.
        _publish(environment, storage, NAMES, sequence=20)
        _save_drafts(environment)
        assert not HostedHarnessScenario.all_objects.get(
            job=environment, scenario_key=_key(NAMES[1])
        ).deleted


class TestDiscard:
    def test_discard_sets_the_draft_aside_and_resets_the_workspace(
        self, user, workspace, storage, auth_client, django_capture_on_commit_callbacks
    ):
        environment = _built(user, workspace, "d-discard")
        conversation = _publish(environment, storage, sequence=10)
        lease = _lease(conversation)

        with django_capture_on_commit_callbacks(execute=True):
            response = auth_client.delete(f"{BASE}/{environment.id}/drafts/suite/")

        assert response.status_code == 204
        draft = HarnessSuiteVersion.no_workspace_objects.get(
            job=environment, status="discarded"
        )
        assert (draft.discard_reason, draft.last_edited_by) == ("user", user)
        lease.refresh_from_db()
        assert lease.state == "expired"
        composed = drafts.compose_workspace(environment, conversation)
        assert f"scenarios/{EXTRA}/scenario.json" not in composed.files
        assert (
            auth_client.delete(f"{BASE}/{environment.id}/drafts/suite/").status_code
            == 204
        )


class TestApi:
    def test_lists_the_open_drafts_without_their_documents(
        self, user, workspace, storage, auth_client
    ):
        environment = _built(user, workspace, "d-api")
        _publish(environment, storage, sequence=10, user=user)

        response = auth_client.get(f"{BASE}/{environment.id}/drafts/")

        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"environment", "suite", "set_aside"}
        assert body["environment"] is None and body["set_aside"] == []
        suite = body["suite"]
        assert suite["status"] == "draft" and suite["label"] is None
        assert suite["stale"] is False and suite["revision"] == 1
        assert suite["changes"] == {"added": 1, "removed": 0, "changed": 0}
        assert suite["last_edited_by"]["id"] == str(user.id)
        assert (suite["parent_label"], suite["default_label"]) == ("v1", "v1")
        text = response.content.decode()
        for secret in ("documents", "workspace_sealed", "exempt_keys", "pairs"):
            assert secret not in text

    def test_the_drafts_endpoints_need_the_flag(
        self, user, workspace, storage, auth_client, settings
    ):
        environment = _built(user, workspace, "d-api-off")
        settings.HARNESS_VERSIONING_ENABLED = False

        for response in (
            auth_client.get(f"{BASE}/{environment.id}/drafts/"),
            auth_client.post(
                f"{BASE}/{environment.id}/drafts/save/",
                {
                    "drafts": [{"piece": "suite", "expected_parent_id": None}],
                    "idempotency_key": "k",
                },
                format="json",
            ),
            auth_client.delete(f"{BASE}/{environment.id}/drafts/suite/"),
        ):
            assert response.status_code == 404
            assert response.json()["error"] == "versioning_disabled"

    def test_another_workspace_or_a_run_is_not_found(
        self, user, workspace, storage, auth_client
    ):
        from accounts.models import Workspace

        environment = _built(user, workspace, "d-api-scope")
        other = Workspace.objects.create(
            name="Elsewhere", organization=user.organization, created_by=user
        )
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            workspace=other
        )

        assert auth_client.get(f"{BASE}/{environment.id}/drafts/").status_code == 404
        assert (
            auth_client.delete(f"{BASE}/{environment.id}/drafts/suite/").status_code
            == 404
        )
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            workspace=workspace
        )
        run = _run_of(environment, "api-scope-run")
        assert auth_client.get(f"{BASE}/{run.id}/drafts/").status_code == 404

    def test_routes(self, user, workspace, storage, auth_client):
        environment = _built(user, workspace, "d-api-routes")
        _publish(environment, storage, sequence=10)
        _finish(environment)
        draft = _draft(environment)

        saved = auth_client.post(
            f"{BASE}/{environment.id}/drafts/save/",
            {
                "drafts": [
                    {"piece": "suite", "expected_parent_id": str(draft.parent_id)}
                ],
                "idempotency_key": "route-1",
            },
            format="json",
        )

        assert saved.status_code == 200, saved.content
        assert set(saved.json()) == {"saved", "versions"}
        assert saved.json()["saved"]["suite"]["label"] == "v2"
        # The save route takes only POST; no route takes another piece.
        assert (
            auth_client.delete(f"{BASE}/{environment.id}/drafts/save/").status_code
            == 405
        )
        with pytest.raises(Resolver404):
            resolve(f"{BASE}/{environment.id}/drafts/agent/")
        assert (
            auth_client.post(
                f"{BASE}/{environment.id}/drafts/save/",
                {
                    "drafts": [
                        {"piece": "suite", "expected_parent_id": None},
                        {"piece": "suite", "expected_parent_id": None},
                    ],
                    "idempotency_key": "route-2",
                },
                format="json",
            ).status_code
            == 400
        )
        missing = auth_client.post(
            f"{BASE}/{environment.id}/drafts/save/",
            {
                "drafts": [{"piece": "environment", "expected_parent_id": None}],
                "idempotency_key": "route-3",
            },
            format="json",
        )
        assert (missing.status_code, missing.json()["error"]) == (
            404,
            "draft_not_found",
        )


class TestAmendWritesTheDraft:
    def test_an_amend_goes_to_the_draft_only(
        self, user, workspace, storage, auth_client
    ):
        environment = _built(user, workspace, "d-amend")
        output = HostedHarnessStageOutput.no_workspace_objects.get(
            job=environment, kind="scenarios"
        ).data
        gateway = "simulate.services.hosted_harness_gateway"
        with (
            patch(f"{gateway}.push_scenarios_into_live_sandbox") as push,
            patch(f"{gateway}.rewrite_conversation_scenarios") as conversations,
            patch(f"{gateway}.rewrite_authoring_scenarios") as rewrite,
            patch.object(harness_scenarios, "index_scenarios") as index,
        ):
            body = _amend(auth_client, environment, NAMES[0], "tests", "edited")

        for mock in (push, conversations, rewrite, index):
            mock.assert_not_called()
        draft = _draft(environment)
        assert body["draft"] == {"id": str(draft.id), "revision": 1}
        assert [one["outcome"] for one in body["receipts"]] == ["applied"]
        assert draft.origin == "manual" and draft.last_edited_by == user
        assert (
            HostedHarnessStageOutput.no_workspace_objects.get(
                job=environment, kind="scenarios"
            ).data
            == output
        )

    def test_an_amend_that_cannot_be_saved_refuses_its_receipts(
        self, user, workspace, storage, auth_client
    ):
        environment = _built(user, workspace, "d-amend-empty")

        response = auth_client.post(
            f"/simulate/api/harness-jobs/{environment.id}/scenarios/amend/",
            {
                "changes": [
                    {"op": "drop", "scenario": NAMES[0]},
                    {"op": "drop", "scenario": NAMES[1]},
                ]
            },
            format="json",
        )

        assert response.status_code == 200, response.content
        assert {one["outcome"] for one in response.json()["receipts"]} == {"refused"}
        assert _draft(environment) is None

    def test_a_second_amend_builds_on_the_draft_by_its_numbers(
        self, user, workspace, storage, auth_client
    ):
        environment = _built(user, workspace, "d-amend-two")
        _publish(environment, storage, sequence=10)
        _finish(environment)
        number = next(
            one["number"]
            for one in _draft(environment).documents["pairs"]
            if one["key"] == EXTRA_KEY
        )

        response = auth_client.post(
            f"/simulate/api/harness-jobs/{environment.id}/scenarios/amend/",
            {
                "changes": [
                    {
                        "op": "set_field",
                        "scenarios": [number],
                        "field": "tests",
                        "value": "numbered",
                    }
                ]
            },
            format="json",
        )

        assert response.status_code == 200, response.content
        draft = _draft(environment)
        assert draft.revision == 2
        added = next(
            one for one in draft.documents["scenarios"] if one["name"] == EXTRA
        )
        assert added["tests"] == "numbered"

    def test_an_amend_on_a_stale_draft_replaces_it(
        self, user, workspace, storage, auth_client
    ):
        environment = _built(user, workspace, "d-amend-stale")
        s1 = environment.suite_version
        _publish(environment, storage, sequence=10)
        _save_drafts(environment, key="one")
        _publish(environment, storage, NAMES, sequence=20)
        stale = _draft(environment)
        move_default(
            environment,
            "suite",
            s1.id,
            expected_default_id=environment.suite_version_id,
        )
        environment.refresh_from_db()

        _amend(auth_client, environment, NAMES[0], "tests", "edited")

        stale.refresh_from_db()
        fresh = _draft(environment)
        assert stale.status == "discarded"
        assert stale.documents["replaced_by"] == str(fresh.id)
        assert fresh.parent_id == s1.id


class TestExtend:
    def _extend(self, auth_client, environment, count=2):
        return auth_client.post(
            f"/simulate/api/harness-jobs/{environment.id}/extend/",
            {"count": count, "guidance": "refunds"},
            format="json",
        )

    def test_extend_asks_the_builder_and_changes_nothing_yet(
        self,
        user,
        workspace,
        storage,
        auth_client,
        settings,
        django_capture_on_commit_callbacks,
    ):
        settings.HARNESS_PUBLIC_BASE_URL = "https://platform.example"
        environment = _built(user, workspace, "d-extend")
        with (
            patch(
                "simulate.tasks.hosted_harness_conversation.schedule_conversation_runtime"
            ) as schedule,
            patch(
                "simulate.services.harness_usage.require_harness_authoring",
                return_value=None,
            ),
            django_capture_on_commit_callbacks(execute=True),
        ):
            response = self._extend(auth_client, environment)

        assert response.status_code == 200, response.content
        message = HostedHarnessConversationMessage.no_workspace_objects.get(
            conversation__job=environment, role="user"
        )
        assert message.content == "Add 2 scenarios: refunds"
        assert message.payload["_origin"] == "extend"
        assert message.payload["_author_id"] == str(user.id)
        schedule.assert_called_once()
        environment.refresh_from_db()
        assert "scenario_extend" not in environment.payload["metadata"]
        assert environment.scenario_count == 2
        assert response.json()["job"]["job_id"] == str(environment.id)

    def test_the_cap_counts_what_the_builder_will_save(
        self, user, workspace, storage, auth_client, settings
    ):
        from simulate.services import hosted_harness_conversation

        settings.HARNESS_PUBLIC_BASE_URL = "https://platform.example"
        environment = _built(user, workspace, "d-extend-cap")
        with (
            patch(
                "simulate.tasks.hosted_harness_conversation.schedule_conversation_runtime"
            ),
            patch(
                "simulate.services.harness_usage.require_harness_authoring",
                return_value=None,
            ),
            patch.object(
                hosted_harness_conversation, "CHAT_CHECKPOINT_MAX_SCENARIOS", 4
            ),
        ):
            assert self._extend(auth_client, environment, 2).status_code == 200
            refused = self._extend(auth_client, environment, 3)

        assert refused.status_code == 422
        assert refused.json()["error"] == "scenario_limit_exceeded"

    def test_a_retired_chat_cannot_take_the_request(
        self, user, workspace, storage, auth_client
    ):
        environment = _built(user, workspace, "d-extend-retired")
        HostedHarnessConversation.no_workspace_objects.filter(
            id=ensure_conversation(environment).id
        ).update(state="retired")
        with patch(
            "simulate.services.harness_usage.require_harness_authoring",
            return_value=None,
        ):
            response = self._extend(auth_client, environment)

        assert response.status_code == 409
        assert response.json()["error"] == "conversation_workspace_not_ready"

    def test_an_unfinished_environment_refuses_extend(
        self, user, workspace, storage, auth_client
    ):
        environment = _built(user, workspace, "d-extend-failed")
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            state=HostedHarnessJob.State.FAILED
        )
        with patch(
            "simulate.services.harness_usage.require_harness_authoring",
            return_value=None,
        ):
            response = self._extend(auth_client, environment)

        assert response.status_code == 409
        assert response.json()["error"] == "environment_busy"
        assert not HostedHarnessConversationMessage.no_workspace_objects.filter(
            conversation__job=environment, role="user"
        ).exists()

    def test_the_builders_save_is_an_extend_draft(self, user, workspace, storage):
        environment = _built(user, workspace, "d-extend-draft")

        _publish(environment, storage, sequence=10, origin="extend")

        assert _draft(environment).origin == "extend"


class TestRerun:
    def test_a_refused_draft_save_still_runs_the_saved_versions(
        self, user, workspace, storage
    ):
        from rest_framework.test import APIClient

        from simulate.services.hosted_harness_conversation import (
            issue_conversation_capability,
        )
        from simulate.tests.test_hosted_harness_conversation import _headers

        environment = _built(user, workspace, "d-rerun")
        _publish(environment, storage, sequence=5)
        conversation = HostedHarnessConversation.no_workspace_objects.get(
            job=environment
        )
        body = with_draft_basis(
            with_authoring_basis(
                _authoring(list(NAMES)),
                environment.payload["metadata"]["authoring_revision"],
            ),
            "outdated",
        )
        key, digest = _checkpoint(conversation, storage, body)
        HostedHarnessConversation.no_workspace_objects.filter(
            id=conversation.id
        ).update(
            latest_workspace_object_key=key,
            latest_workspace_digest=digest,
            event_acked_through=100,
        )
        capability = issue_conversation_capability(
            conversation,
            endpoint_base_url="https://platform.example",
            provider_ref="sandbox-rerun",
            attempt=None,
            ttl_seconds=600,
        )
        revision = _draft(environment).revision
        with patch(
            "simulate.services.harness_provider.HostedHarnessProvider.rerun_saved"
        ) as rerun:
            response = APIClient().post(
                f"/simulate/api/harness/conversations/{conversation.id}/rerun/",
                {},
                format="json",
                **_headers(capability),
            )

        assert response.status_code == 202, response.content
        rerun.assert_called_once()
        assert _draft(environment).revision == revision
        assert HostedHarnessConversationMessage.no_workspace_objects.get(
            conversation=conversation, role="assistant"
        ).content.startswith("These changes were not saved:")


class TestScenarioList:
    def test_the_list_shows_the_unsaved_scenarios_numbered_for_save(
        self, user, workspace, storage, auth_client
    ):
        environment = _built(user, workspace, "d-list")
        _publish(environment, storage, (NAMES[0], EXTRA), sequence=10)
        draft = _draft(environment)

        body = auth_client.get(
            f"/simulate/api/harness-jobs/{environment.id}/scenarios/",
            {"group_by": ""},
        ).json()

        rows = {one["scenario_key"]: one for one in body["results"]}
        assert set(rows) == {_key(NAMES[0]), EXTRA_KEY}
        assert rows[EXTRA_KEY]["draft_change"] == "added"
        assert rows[_key(NAMES[0])]["draft_change"] == "unchanged"
        assert rows[EXTRA_KEY]["number"] == 3
        assert body["draft"] == {"id": str(draft.id), "revision": 1}
        coverage = auth_client.get(
            f"/simulate/api/harness-jobs/{environment.id}/scenarios/coverage/"
        )
        assert coverage.status_code == 200

    def test_with_the_flag_off_a_left_draft_is_ignored(
        self, user, workspace, storage, auth_client, settings
    ):
        environment = _built(user, workspace, "d-list-off")
        _publish(environment, storage, sequence=10)
        settings.HARNESS_VERSIONING_ENABLED = False

        body = auth_client.get(
            f"/simulate/api/harness-jobs/{environment.id}/scenarios/"
        ).json()

        assert drafts.draft_rows(environment) is None
        assert "draft" not in body
        assert {one["scenario_key"] for one in body["results"]} == {
            _key(NAMES[0]),
            _key(NAMES[1]),
        }

    def test_rows_and_queryset_filter_alike(self, user, workspace, storage):
        documents = [
            {
                "name": name,
                "scenario_key": _key(name),
                "use_case": "refunds" if index % 2 else "rides",
                "instruction": f"call about {name}",
                "persona": {"gender": "female" if index else "male"},
                "sub_goals": ["paid"] if index else ["asked"],
                "coverage": {"task": "a" if index else "b", "overlay": "none"},
            }
            for index, name in enumerate(NAMES)
        ]
        scratch = record_tests._environment(user, workspace, "d-parity-scratch")
        harness_scenarios.index_scenarios(scratch, documents, prune=True)
        queryset = HostedHarnessScenario.no_workspace_objects.filter(job=scratch)
        rows = harness_scenarios.ScenarioRows(queryset)

        def keys(found):
            return [one.scenario_key for one in found]

        for params in (
            "use_case=refunds",
            "use_case_not=refunds",
            "persona.gender=female&persona.gender=male",
            "sub_goals=paid",
            "coverage.task=b",
        ):
            query = QueryDict(params)
            assert sorted(keys(harness_scenarios.apply_filters(rows, query))) == sorted(
                keys(harness_scenarios.apply_filters(queryset, query))
            ), params
        for term in ("ride airport", "pin", "explain"):
            assert sorted(keys(harness_scenarios.apply_search(rows, term))) == sorted(
                keys(harness_scenarios.apply_search(queryset, term))
            ), term
        for ordering in ("-number", "name", "-name", "bogus"):
            assert keys(harness_scenarios.apply_ordering(rows, ordering)) == keys(
                harness_scenarios.apply_ordering(queryset, ordering)
            ), ordering
        assert harness_scenarios.field_catalogue(rows) == (
            harness_scenarios.field_catalogue(queryset)
        )
        assert harness_scenarios.coverage_grid(rows, "task", "overlay") == (
            harness_scenarios.coverage_grid(queryset, "task", "overlay")
        )
        page = [{"group": "rides", "number": 1}]
        assert harness_scenarios.group_counts(page, rows, "goal") == (
            harness_scenarios.group_counts(page, queryset, "goal")
        )


class TestComposeWorkspace:
    def test_the_draft_is_overlaid_with_both_bases_stamped(
        self, user, workspace, storage
    ):
        environment = _built(user, workspace, "d-compose")
        conversation = _publish(environment, storage, sequence=10)
        draft = _draft(environment)

        body = chat_workspace_for(environment, conversation, chat_state=None)

        files = archives.read_archive(body)
        assert f"scenarios/{EXTRA}/scenario.json" in files
        assert "environment-bundle/manifest.json" in files
        assert (
            authoring_basis(body)
            == environment.payload["metadata"]["authoring_revision"]
        )
        environment.refresh_from_db()
        basis = environment.payload["metadata"]["conversation_checkpoints"][
            str(conversation.id)
        ]["draft_basis"]
        assert draft_basis_token(body) == basis["token"]
        assert basis["suite"] == {"id": str(draft.id), "revision": 1}
        assert basis["environment"] == {"id": None, "revision": 0}

    def test_the_chats_own_state_comes_along_but_not_its_basis_files(
        self, user, workspace, storage
    ):
        environment = _built(user, workspace, "d-compose-state")
        conversation = _conversation(environment)
        state = with_draft_basis(
            _authoring(list(NAMES), **{".futureagi-conversation.json": "{}"}), "old"
        )

        body = chat_workspace_for(environment, conversation, chat_state=state)

        files = archives.read_archive(body)
        assert ".futureagi-conversation.json" in files
        assert f"scenarios/{NAMES[2]}/scenario.json" not in files
        assert draft_basis_token(body) not in ("", "old")
        assert DRAFT_BASIS_FILE in files

    def test_a_stale_draft_is_not_overlaid(self, user, workspace, storage):
        environment = _built(user, workspace, "d-compose-stale")
        s1 = environment.suite_version
        _publish(environment, storage, sequence=10)
        _save_drafts(environment, key="one")
        conversation = _publish(environment, storage, NAMES, sequence=20)
        move_default(
            environment,
            "suite",
            s1.id,
            expected_default_id=environment.suite_version_id,
        )
        environment.refresh_from_db()

        composed = drafts.compose_workspace(environment, conversation)

        assert f"scenarios/{NAMES[2]}/scenario.json" not in composed.files
        assert composed.stale == {"environment": False, "suite": True}

    def test_without_drafts_the_chat_starts_as_before(
        self, user, workspace, storage, settings
    ):
        environment = _built(user, workspace, "d-compose-off")
        conversation = _conversation(environment)
        settings.HARNESS_VERSIONING_ENABLED = False

        body = chat_workspace_for(environment, conversation, chat_state=None)

        assert DRAFT_BASIS_FILE not in archives.read_archive(body)


class TestEnqueue:
    def test_client_underscore_keys_are_stripped_and_server_keys_kept(
        self, user, workspace, storage
    ):
        environment = _built(user, workspace, "d-enqueue")

        _conversation_, message, _ = enqueue_message(
            environment,
            content="hi",
            client_request_id="strip",
            payload={"_author_id": "forged", "_origin": "extend", "mode": "edit"},
            server_payload={"_author_id": str(user.id)},
        )

        assert message.payload["_author_id"] == str(user.id)
        assert "_origin" not in message.payload
        assert message.payload["mode"] == "edit"
        assert message.payload["_environment_revision"] == (
            environment.payload["metadata"]["authoring_revision"]
        )

    def test_a_given_revision_is_stamped_without_reading_storage(
        self, user, workspace, storage
    ):
        environment = _built(user, workspace, "d-enqueue-rev")
        with patch(
            "simulate.services.hosted_harness_conversation.environment_authoring_revision"
        ) as read:
            _conversation_, message, _ = enqueue_message(
                environment,
                content="hi",
                client_request_id="rev",
                expected_revision="r",
            )

        read.assert_not_called()
        assert message.payload["_environment_revision"] == "r"


class TestFlagOff:
    def test_a_left_draft_is_ignored_and_chat_records_versions(
        self, user, workspace, storage, settings
    ):
        environment = _built(user, workspace, "d-off")
        _publish(environment, storage, sequence=10)
        draft = _draft(environment)
        settings.HARNESS_VERSIONING_ENABLED = False

        record_tests.TestChatPromote()._promote(environment, storage, NAMES)

        assert environment.suite_version.version_number == 2
        assert environment.suite_version.origin == "chat"
        draft.refresh_from_db()
        assert draft.status == "draft"


class TestPlanPairs:
    def test_the_plan_matches_what_pairing_writes(self, user, workspace, storage):
        environment = _built(user, workspace, "d-plan")
        HostedHarnessScenario.all_objects.filter(
            job=environment, scenario_key=_key(NAMES[1])
        ).update(deleted=True)
        documents = [
            record_tests._scenario(NAMES[0]),
            record_tests._scenario(NAMES[1]),
            record_tests._scenario(NAMES[2]),
            {"name": "keyless"},
        ]

        before = list(
            HostedHarnessScenario.all_objects.filter(job=environment).values_list(
                "scenario_key", "number", "deleted"
            )
        )
        plan = harness_scenarios.plan_pairs(environment, documents, exact=True)
        assert before == list(
            HostedHarnessScenario.all_objects.filter(job=environment).values_list(
                "scenario_key", "number", "deleted"
            )
        )
        written = harness_scenarios.pair_scenarios(
            environment, documents, prune=True, exact=True
        )

        assert [(one.key, one.number) for one in plan] == [
            (row.scenario_key, row.number) for row, _doc in written
        ]
        assert [one.row.id if one.row else None for one in plan][:2] == [
            row.id for row, _doc in written[:2]
        ]


class TestSecrets:
    def test_the_job_metadata_holds_only_ids_numbers_times_and_the_token(
        self, user, workspace, storage
    ):
        environment = _built(user, workspace, "d-secrets")
        _publish(environment, storage, sequence=10)
        _save_drafts(environment, key="secret-key")

        metadata = serialize_job(environment)["job"]["metadata"]

        [checkpoint] = metadata["conversation_checkpoints"].values()
        basis = checkpoint["draft_basis"]
        assert set(basis) == {"token", "environment", "suite"}
        assert len(basis["token"]) == 32 and int(basis["token"], 16) >= 0
        for piece in ("environment", "suite"):
            assert set(basis[piece]) == {"id", "revision"}
        [saved] = metadata["draft_saves"].values()
        assert set(saved) == {"pieces", "at"}
        assert set(saved["pieces"]) == {"suite"}


class TestStepFourteenInterface:
    def test_activate_drafts_returns_each_piece_and_whether_it_was_made(
        self, user, workspace, storage
    ):
        from django.db import transaction

        environment = _built(user, workspace, "d-activate")
        s1 = environment.suite_version
        _publish(environment, storage, sequence=10)
        nudge = HarnessNudge.no_workspace_objects.create(
            job=environment,
            organization=environment.organization,
            workspace=environment.workspace,
            target="suite",
            state="pending",
        )

        with transaction.atomic():
            locked = HostedHarnessJob.no_workspace_objects.select_for_update().get(
                id=environment.id
            )
            result = drafts.activate_drafts(
                locked, {"suite"}, origin="nudge", user=user, nudge=nudge
            )

        (s2, created) = result["suite"]
        assert created and s2.origin == "nudge" and s2.nudge_id == nudge.id
        environment.refresh_from_db()
        assert environment.suite_version_id == s2.id
        _publish(environment, storage, NAMES[:2], sequence=20)
        with transaction.atomic():
            locked = HostedHarnessJob.no_workspace_objects.select_for_update().get(
                id=environment.id
            )
            assert drafts.activate_drafts(
                locked, {"suite"}, origin=None, user=user
            ) == {"suite": (s1, False)}

    def test_activate_drafts_can_move_the_agent_in_the_same_move(
        self, user, workspace, storage
    ):
        from django.db import transaction

        from simulate.services.harness_versioning.versions import _Pair
        from simulate.tests.test_harness_versioning_backfill import (
            _agent_version,
            _definition,
        )

        environment = _built(user, workspace, "d-activate-agent")
        definition = _definition(user, workspace)
        a1, a2 = _agent_version(definition), _agent_version(definition)
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            agent_version=a1
        )
        environment.refresh_from_db()
        e1, s1 = environment.environment_version, environment.suite_version
        _publish(
            environment,
            storage,
            NAMES[:2],
            sequence=10,
            contract={"agent": "support", "tools": [{"name": "lookup"}]},
        )
        pair = _Pair(key="harness-authoring/pair.tar.gz", revision="r2", sealed=False)
        # Only the move itself keeps the outgoing pair.
        payload = dict(environment.payload)
        payload["metadata"] = {**payload["metadata"], "default_pairs": {}}
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            payload=payload
        )

        with (
            patch.object(drafts, "_default_pair", return_value=pair) as ladder,
            transaction.atomic(),
        ):
            locked = HostedHarnessJob.no_workspace_objects.select_for_update().get(
                id=environment.id
            )
            result = drafts.activate_drafts(
                locked, {"environment"}, origin="nudge", user=user, agent_version=a2
            )

        ladder.assert_called_once()
        (e2, created) = result["environment"]
        environment.refresh_from_db()
        assert created and e2.agent_version_id == a2.id
        assert environment.agent_version_id == a2.id
        assert environment.environment_version_id == e2.id
        kept = environment.payload["metadata"]["default_pairs"]
        assert kept[f"{e1.id}:{s1.id}"]["agent_version_id"] == str(a1.id)
        assert kept[f"{e2.id}:{s1.id}"]["agent_version_id"] == str(a2.id)

    def test_record_draft_writes_only_the_named_pieces(self, user, workspace, storage):
        environment = _built(user, workspace, "d-pieces")
        nudge = HarnessNudge.no_workspace_objects.create(
            job=environment,
            organization=environment.organization,
            workspace=environment.workspace,
            target="suite",
            state="pending",
        )
        body = _authoring(
            [*NAMES[:2], EXTRA],
            contract={"agent": "support", "tools": [{"name": "lookup"}]},
        )

        write = drafts.record_draft(
            environment,
            body,
            origin="nudge",
            pieces={"suite"},
            nudge=nudge,
            expected={"suite": drafts.NO_DRAFT, "environment": drafts.DraftRef("x", 9)},
        )

        assert write.written == {"suite"}
        assert _draft(environment, "environment") is None
        assert _draft(environment).nudge_id == nudge.id

    def test_a_nudge_draft_may_take_a_dropped_key(self, user, workspace, storage):
        environment = TestRetiredKeys()._dropped(user, workspace, storage, "d-nudge")

        write = drafts.record_draft(
            environment, _authoring(list(NAMES[:2])), origin="nudge"
        )

        assert write.written == {"suite"}

    def test_compose_with_only_one_piece(self, user, workspace, storage):
        environment = _built(user, workspace, "d-only")
        conversation = _publish(
            environment,
            storage,
            sequence=10,
            contract={"agent": "support", "tools": [{"name": "lookup"}]},
        )

        composed = drafts.compose_workspace(
            environment, conversation, only_draft="suite"
        )

        assert f"scenarios/{EXTRA}/scenario.json" in composed.files
        contract = json.loads(composed.files["contract.json"])
        assert contract["tools"] == []
        assert set(composed.refs) == {"suite"}

    def test_restore_brings_a_set_aside_draft_back(self, user, workspace, storage):
        from django.db import transaction

        environment = _built(user, workspace, "d-restore")
        _publish(environment, storage, sequence=10)
        draft = _draft(environment)
        key = draft.archive_object_key
        drafts.discard_draft(environment, "suite", user=user)

        with transaction.atomic():
            locked = HostedHarnessJob.no_workspace_objects.select_for_update().get(
                id=environment.id
            )
            restored = drafts.restore_draft(
                locked,
                "suite",
                draft_id=draft.id,
                archive_object_key=key,
                revision=5,
                last_edited_by_id=user.id,
            )

        assert (restored.status, restored.revision) == ("draft", 6)
        assert restored.last_edited_by_id == user.id
        assert restored.hash == draft.hash
        assert {one["key"] for one in restored.documents["pairs"]} == {
            _key(NAMES[0]),
            _key(NAMES[1]),
            EXTRA_KEY,
        }


def test_the_environment_version_is_untouched_by_a_suite_save(user, workspace, storage):
    environment = _built(user, workspace, "d-env-kept")
    e1 = environment.environment_version_id
    _publish(environment, storage, sequence=10)

    _save_drafts(environment)

    assert environment.environment_version_id == e1
    assert not HarnessEnvironmentVersion.no_workspace_objects.filter(
        job=environment, status="draft"
    ).exists()


class TestChatReads:
    def test_publish_and_the_chats_reads_show_the_unsaved_changes(
        self, user, workspace, storage
    ):
        from simulate.services.hosted_harness_conversation import (
            conversation_overview,
            conversation_scenarios,
            publish_conversation_workspace,
            start_conversation_run,
        )

        environment = _built(user, workspace, "d-chat-reads")
        revision = environment.payload["metadata"]["authoring_revision"]
        conversation = _conversation(environment)
        token = drafts.compose_workspace(environment, conversation).token

        def published(names, acked):
            HostedHarnessConversation.no_workspace_objects.filter(
                id=conversation.id
            ).update(event_acked_through=acked)
            body = with_draft_basis(
                with_authoring_basis(_authoring(list(names)), revision), token
            )
            _key_, digest = _checkpoint(conversation, storage, body)
            return publish_conversation_workspace(conversation, digest=digest)

        first = published([*NAMES[:2], EXTRA], 1)

        assert (first["applied"], first["draft"], first["revision"]) == (
            True,
            True,
            revision,
        )
        assert first["scenario_count"] == 3
        assert first["added"] == [{"number": 3, "name": EXTRA}]
        assert (first["removed"], first["changed"]) == ([], [])
        overview = conversation_overview(conversation)
        assert overview["scenario_count"] == 3
        assert overview["drafts"]["environment"] is None
        assert overview["drafts"]["suite"]["stale"] is False
        page = conversation_scenarios(conversation, {"numbers": [3]})
        assert [row["name"] for row in page["rows"]] == [EXTRA]
        assert page["total_scenarios"] == 3

        second = published([NAMES[0], EXTRA], 2)

        assert second["removed"] == [{"number": 2, "name": NAMES[1]}]
        environment.refresh_from_db()
        assert environment.scenario_count == 2
        with (
            patch("simulate.services.harness_usage.require_harness_call_usage"),
            patch("simulate.temporal.client.start_hosted_harness_gateway_workflow"),
        ):
            started = start_conversation_run(
                conversation, numbers=[1], ranges=[], trials=1
            )
        assert started["draft_not_included"] is True

    def test_without_drafts_the_overview_has_none(
        self, user, workspace, storage, settings
    ):
        from simulate.services.hosted_harness_conversation import conversation_overview

        environment = _built(user, workspace, "d-chat-reads-off")
        conversation = _conversation(environment)
        settings.HARNESS_VERSIONING_ENABLED = False

        assert "drafts" not in conversation_overview(conversation)
