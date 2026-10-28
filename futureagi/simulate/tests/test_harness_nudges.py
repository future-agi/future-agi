"""Nudges: raised after an agent or environment default moves, listed, and skipped."""

import copy
import json
import logging
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from simulate.models import (
    HarnessNudge,
    HostedHarnessConversationMessage,
    HostedHarnessJob,
)
from simulate.services.harness_versioning import nudges
from simulate.tasks import harness_nudges as nudge_tasks
from simulate.tests.test_harness_agent_versions import (
    _a1,
    _create,
    _environment,
    _no_values,
    _preflight,
    _shared,
)
from simulate.tests.test_harness_agent_versions import (
    scheduled as scheduled,  # noqa: F401 - fixture
)
from simulate.tests.test_harness_versioning_backfill import (
    storage as storage,  # noqa: F401 - fixture
)

pytestmark = pytest.mark.django_db

BASE = "/simulate/api/harness-environments"
NEW_VALUE = "val-openai-new"


@pytest.fixture(autouse=True)
def nudges_on(settings):
    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_VERSIONING_NUDGES_ENABLED = True
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = False


@pytest.fixture
def started():
    """Runs each scheduled nudge activity in-process; records its Temporal inputs."""
    calls = []

    def run_raise(*, args, **options):
        calls.append(("raise", args, options))
        nudges.raise_environment_nudge(
            args[0],
            args[1],
            default_moved=args[2],
            outgoing_agent_version_id=args[3],
        )

    def run_reconcile(*, args, **options):
        calls.append(("reconcile", args, options))
        nudges.reconcile_environment_nudges(args[0])

    with (
        patch.object(
            nudge_tasks.raise_harness_environment_nudge,
            "apply_async",
            side_effect=run_raise,
        ),
        patch.object(
            nudge_tasks.reconcile_harness_environment_nudges,
            "apply_async",
            side_effect=run_reconcile,
        ),
    ):
        yield calls


def _nudges(environment, **filters):
    return list(
        HarnessNudge.no_workspace_objects.filter(job=environment, **filters).order_by(
            "created_at"
        )
    )


def _activated(user, workspace, storage, key, django_capture_on_commit_callbacks):
    """A backfilled environment whose default moved from legacy A1 to A2, a new value
    for one variable."""
    [environment] = _shared(user, workspace, storage, key, count=1)
    a1 = _a1(environment)
    a2 = _create(a1, environment, secret_values={"OPENAI_API_KEY": NEW_VALUE})
    with django_capture_on_commit_callbacks(execute=True):
        assert _preflight(a2).status == "passed"
    environment.refresh_from_db()
    assert environment.agent_version_id == a2.id
    return environment, a1, a2


def _checked(version):
    """A version whose check passed, without moving any default: one an offer names."""
    from simulate.models import HarnessAgentVersionRuntime

    HarnessAgentVersionRuntime.no_workspace_objects.filter(
        agent_version=version
    ).update(preflight_status="passed")
    return version


class TestRaise:
    def test_an_activation_raises_one_card_naming_the_variable_only(
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
        environment, a1, a2 = _activated(
            user, workspace, storage, "n-raise", django_capture_on_commit_callbacks
        )

        [nudge] = _nudges(environment)
        assert nudge.state == "pending"
        assert nudge.target == "environment"
        assert nudge.trigger_agent_version_id == a2.id
        assert nudge.base_agent_version_id == a1.id
        assert nudge.base_environment_version_id == environment.environment_version_id
        assert nudge.facts["variables"] == {
            "added": [],
            "removed": [],
            "changed": ["OPENAI_API_KEY"],
            "unknown": [],
        }
        assert nudge.facts["agent_default_moved"] is True
        assert nudge.facts["source"]["changed"] is False
        assert nudge.facts["settings_changed"] == []
        assert "card_message_id" not in nudge.facts
        card = nudge.conversation_message
        assert (card.role, card.kind, card.state) == ("system", "status", "completed")
        assert card.payload["nudge_id"] == str(nudge.id)
        assert "OPENAI_API_KEY" in card.content
        conversation = card.conversation
        assert conversation.state == "cold"
        assert not HostedHarnessConversationMessage.no_workspace_objects.filter(
            conversation=conversation, role="user"
        ).exists()
        # Temporal inputs are ids and a flag.
        [(kind, args, options)] = started
        assert kind == "raise"
        assert args == [str(environment.id), str(a2.id), True, str(a1.id)]
        assert options["task_id"] == f"harness-nudge-{environment.id}-{a2.id}-1"

        response = auth_client.get(f"{BASE}/{environment.id}/nudges/")

        assert response.status_code == 200
        [item] = response.json()
        assert item["card_message_id"] == str(card.id)
        assert item["trigger_agent_version"]["id"] == str(a2.id)
        assert item["closed_at"] is None
        assert item["queued_behind_input"] is False
        assert item["can_stop"] is False
        for text in (
            json.dumps(nudge.facts),
            card.content,
            json.dumps(card.payload),
            response.content.decode(),
            json.dumps([list(map(str, args)) for _k, args, _o in started]),
            caplog.text,
        ):
            _no_values(text)
            assert NEW_VALUE not in text

    def test_a_rerun_and_a_second_activity_keep_one_card(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        django_capture_on_commit_callbacks,
    ):
        environment, _a1_, a2 = _activated(
            user, workspace, storage, "n-twice", django_capture_on_commit_callbacks
        )
        with django_capture_on_commit_callbacks(execute=True):
            assert _preflight(a2).status == "passed"
        for _ in range(2):
            nudges.raise_environment_nudge(environment.id, a2.id, default_moved=True)

        [nudge] = _nudges(environment)

        # A queued rebuild for the same trigger and environment version also stands.
        HarnessNudge.no_workspace_objects.filter(id=nudge.id).update(state="queued")
        assert (
            nudges.raise_environment_nudge(environment.id, a2.id, default_moved=True).id
            == nudge.id
        )
        assert len(_nudges(environment)) == 1

    def test_an_environment_that_did_not_ask_gets_an_offer_and_legacy_sharers_none(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        settings,
        django_capture_on_commit_callbacks,
    ):
        from simulate.tests.test_harness_agent_versions import _defaults, _on_a2

        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        (first, second), _a1_, a2 = _on_a2(user, workspace, storage, "n-offer")
        [left] = _shared(user, workspace, storage, "n-offer-left", count=1)
        a3 = _create(a2, first, secret_values={"OPENAI_API_KEY": "val-openai-v3"})

        with django_capture_on_commit_callbacks(execute=True):
            assert _preflight(a3).status == "passed"

        assert _defaults(first, second) == [a3.id, a2.id]
        [moved] = _nudges(first)
        assert moved.facts["agent_default_moved"] is True
        [offer] = _nudges(second)
        assert offer.trigger_agent_version_id == a3.id
        assert offer.facts["agent_default_moved"] is False
        assert offer.facts["variables"]["changed"] == ["OPENAI_API_KEY"]
        assert "is available" in offer.conversation_message.content
        assert _nudges(left) == []

    def test_a_sharer_that_cannot_pick_the_version_gets_no_offer(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        settings,
        django_capture_on_commit_callbacks,
    ):
        from simulate.models import HarnessAgentVersionRuntime
        from simulate.tests.test_harness_agent_versions import _defaults, _on_a2

        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        (first, second), _a1_, a2 = _on_a2(user, workspace, storage, "n-unpickable")
        side = HarnessAgentVersionRuntime.no_workspace_objects.get(agent_version=a2)
        side.agent_payload = {**side.agent_payload, "definition_owner": "environment"}
        side.save(update_fields=["agent_payload"])
        a3 = _create(a2, first, secret_values={"OPENAI_API_KEY": "val-openai-v3"})

        with django_capture_on_commit_callbacks(execute=True):
            assert _preflight(a3).status == "passed"

        assert _defaults(first, second) == [a3.id, a2.id]
        [moved] = _nudges(first)
        assert moved.facts["agent_default_moved"] is True
        assert _nudges(second) == []
        HostedHarnessJob.no_workspace_objects.filter(id=second.id).update(
            state="completed"
        )
        nudges.reconcile_environment_nudges(second.id)
        assert _nudges(second, trigger_agent_version=a3) == []

    def test_an_environment_move_during_the_raise_reads_the_facts_again(
        self, user, workspace, storage, scheduled
    ):
        [environment] = _shared(user, workspace, storage, "n-mid-raise", count=1)
        e1 = environment.environment_version
        a2 = _create(
            _a1(environment), environment, secret_values={"OPENAI_API_KEY": NEW_VALUE}
        )
        assert _preflight(a2).status == "passed"
        # E2 was built with A2; the head still stands on E1 (built with A1).
        e2 = _contract_change(user, environment, [{"name": "refund"}]).environment
        assert e2.agent_version_id == a2.id
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            environment_version=e1
        )
        real = nudges.diff_facts
        bases = []

        def moved_meanwhile(env, base, new):
            bases.append(base.id)
            if len(bases) == 1:
                HostedHarnessJob.no_workspace_objects.filter(id=env.id).update(
                    environment_version=e2
                )
            return real(env, base, new)

        with patch.object(nudges, "diff_facts", side_effect=moved_meanwhile):
            assert (
                nudges.raise_environment_nudge(
                    environment.id, a2.id, default_moved=True
                )
                is None
            )

        assert bases == [e1.agent_version_id, a2.id]
        assert _nudges(environment, target="environment") == []

    def test_turned_down_activations_get_an_offer_and_a_busy_one_waits(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        settings,
        django_capture_on_commit_callbacks,
    ):
        from simulate.services.harness_versioning.versions import move_default

        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        environment = _environment(user, workspace, "n-turned", storage)
        a1 = _a1(environment)
        a2 = _create(a1, environment, secret_values={"OPENAI_API_KEY": "val-a2-value"})
        assert _preflight(a2).status == "passed"
        a3 = _create(a2, environment, secret_values={"OPENAI_API_KEY": "val-a3-value"})
        move_default(environment, "agent", a1.id, expected_default_id=a2.id)

        with django_capture_on_commit_callbacks(execute=True):
            assert _preflight(a3).status == "passed"

        [offer] = _nudges(environment)
        assert offer.trigger_agent_version_id == a3.id
        assert offer.facts["agent_default_moved"] is False

    def test_a_busy_activation_moves_the_default_once_the_build_completes(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        django_capture_on_commit_callbacks,
    ):
        from simulate.services.harness_versioning import rebuild

        [environment] = _shared(user, workspace, storage, "n-busy", count=1)
        a1 = _a1(environment)
        a2 = _create(a1, environment, secret_values={"OPENAI_API_KEY": NEW_VALUE})
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            state="running"
        )

        with django_capture_on_commit_callbacks(execute=True):
            assert _preflight(a2).status == "passed"

        assert _nudges(environment) == []
        assert started == []
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            state="completed"
        )
        with django_capture_on_commit_callbacks(execute=True):
            nudges.schedule_reconcile(environment.id)
        # A busy activation is retried, not offered.
        assert _nudges(environment) == []

        with django_capture_on_commit_callbacks(execute=True):
            assert rebuild.retry_deferred_activations(a2.id) == {
                str(environment.id): "activated"
            }

        environment.refresh_from_db()
        assert environment.agent_version_id == a2.id
        [card] = _nudges(environment)
        assert card.trigger_agent_version_id == a2.id
        assert card.facts["agent_default_moved"] is True

    def test_an_offer_missed_while_busy_is_found_below_a_newer_version(
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
        from simulate.tests.test_harness_agent_versions import _defaults, _on_a2

        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        (first, second), _a1_, a2 = _on_a2(user, workspace, storage, "n-missed")
        HostedHarnessJob.no_workspace_objects.filter(id=first.id).update(
            state="running"
        )
        a3 = _create(a2, second, secret_values={"OPENAI_API_KEY": "val-openai-v3"})
        with django_capture_on_commit_callbacks(execute=True):
            assert _preflight(a3).status == "passed"
        # A newer version for the other environment, with no entry for this one.
        a4 = _create(a3, second, secret_values={"OPENAI_API_KEY": "val-openai-v4"})
        with django_capture_on_commit_callbacks(execute=True):
            assert _preflight(a4).status == "passed"
        assert _defaults(first, second) == [a2.id, a4.id]
        assert _nudges(first) == []
        HostedHarnessJob.no_workspace_objects.filter(id=first.id).update(
            state="completed"
        )

        nudges.reconcile_environment_nudges(first.id)
        # The offer waits until the default's own card is answered.
        [card] = _nudges(first)
        with django_capture_on_commit_callbacks(execute=True):
            assert _answer(auth_client, first, card, "no").status_code == 200

        [offer] = _nudges(first, trigger_agent_version=a3)
        assert offer.facts["agent_default_moved"] is False

    def test_an_older_offer_waits_behind_a_newer_busy_version(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        settings,
        django_capture_on_commit_callbacks,
    ):
        from simulate.models import HarnessAgentVersionRuntime
        from simulate.tests.test_harness_agent_versions import _on_a2

        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        (first, second), _a1_, a2 = _on_a2(user, workspace, storage, "n-busy-newest")
        a3 = _create(a2, second, secret_values={"OPENAI_API_KEY": "val-openai-v3"})
        with django_capture_on_commit_callbacks(execute=True):
            assert _preflight(a3).status == "passed"
        a4 = _create(a3, second, secret_values={"OPENAI_API_KEY": "val-openai-v4"})
        with django_capture_on_commit_callbacks(execute=True):
            assert _preflight(a4).status == "passed"
        side = HarnessAgentVersionRuntime.no_workspace_objects.get(agent_version=a4)
        side.preflight_report = {
            **(side.preflight_report or {}),
            "activation": [
                {"environment_id": str(first.id), "outcome": "environment_busy"}
            ],
        }
        side.save(update_fields=["preflight_report"])
        first.refresh_from_db()
        assert nudges.reads._activation_outcome(
            HarnessAgentVersionRuntime.no_workspace_objects.get(
                agent_version=a3
            ).preflight_report,
            first.id,
        ) in ("not_requested", "offered")
        assert nudges.agent_version_pickable(first, a4.id)

        # The newest is the deferred retry's: no older version is offered.
        assert nudges._missed_offer(first) is None

    def test_a_busy_offer_is_found_below_a_newer_version_it_cannot_pick(
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
        from simulate.models import HarnessAgentVersionRuntime
        from simulate.tests.test_harness_agent_versions import _defaults, _on_a2

        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        (first, second), _a1_, a2 = _on_a2(user, workspace, storage, "n-busy-lineage")
        side = HarnessAgentVersionRuntime.no_workspace_objects.get(agent_version=a2)
        side.agent_payload = {**side.agent_payload, "definition_owner": "environment"}
        side.save(update_fields=["agent_payload"])
        HostedHarnessJob.no_workspace_objects.filter(id=first.id).update(
            state="running"
        )
        # The busy environment asks for A3; the other one then makes A4 from A2.
        a3 = _create(a2, first, secret_values={"OPENAI_API_KEY": "val-openai-v3"})
        with django_capture_on_commit_callbacks(execute=True):
            assert _preflight(a3).status == "passed"
        a4 = _create(a2, second, secret_values={"OPENAI_API_KEY": "val-openai-v4"})
        with django_capture_on_commit_callbacks(execute=True):
            assert _preflight(a4).status == "passed"
        assert _defaults(first, second) == [a2.id, a4.id]
        outcomes = {
            runtime.agent_version_id: nudges.reads._activation_outcome(
                runtime.preflight_report, first.id
            )
            for runtime in HarnessAgentVersionRuntime.no_workspace_objects.filter(
                agent_version__in=[a3, a4]
            )
        }
        assert outcomes == {a3.id: "environment_busy", a4.id: "not_requested"}
        assert not nudges.agent_version_pickable(first, a4.id)
        HostedHarnessJob.no_workspace_objects.filter(id=first.id).update(
            state="completed"
        )

        nudges.reconcile_environment_nudges(first.id)
        [card] = _nudges(first)
        with django_capture_on_commit_callbacks(execute=True):
            assert _answer(auth_client, first, card, "no").status_code == 200
        # The busy request is moved, not offered.
        assert _nudges(first, trigger_agent_version=a3) == []

        from simulate.services.harness_versioning import rebuild

        with django_capture_on_commit_callbacks(execute=True):
            rebuild.retry_deferred_activations(a3.id)

        first.refresh_from_db()
        assert first.agent_version_id == a3.id
        [moved] = _nudges(first, trigger_agent_version=a3)
        assert moved.facts["agent_default_moved"] is True

    def test_a_skipped_missed_offer_is_not_raised_again_after_e_moves(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        from simulate.tests.test_harness_agent_versions import _on_a2

        (first, environment), _a1_, a2 = _on_a2(
            user, workspace, storage, "n-skip-final"
        )
        a3 = _create(a2, first, secret_values={"OPENAI_API_KEY": "val-openai-v3"})
        with django_capture_on_commit_callbacks(execute=True):
            assert _preflight(a3).status == "passed"
        [offer] = _nudges(environment, trigger_agent_version=a3)
        assert offer.facts["agent_default_moved"] is False
        assert _answer(auth_client, environment, offer, "no").status_code == 200
        with django_capture_on_commit_callbacks(execute=True):
            _contract_change(user, environment, [{"name": "refund"}])

        nudges.reconcile_environment_nudges(environment.id)

        [skipped] = _nudges(environment, target="environment", trigger_agent_version=a3)
        assert skipped.state == "answered_no"

    def test_a_newer_trigger_replaces_the_pending_card(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        environment, _a1_, a2 = _activated(
            user, workspace, storage, "n-replace", django_capture_on_commit_callbacks
        )
        a3 = _create(a2, environment, secret_values={"OPENAI_API_KEY": "val-a3-value"})
        with django_capture_on_commit_callbacks(execute=True):
            assert _preflight(a3).status == "passed"

        old, new = _nudges(environment)
        assert (old.state, new.state) == ("replaced", "pending")
        assert new.trigger_agent_version_id == a3.id
        body = auth_client.get(f"{BASE}/{environment.id}/nudges/").json()
        assert [item["id"] for item in body] == [str(new.id), str(old.id)]
        assert body[1]["replaced_by"] == {
            "id": str(new.id),
            "trigger_label": body[0]["trigger_agent_version"]["label"],
        }
        assert body[0]["replaced_by"] is None
        # Its replacement skipped and a newer card raised: still named when filtered.
        assert _answer(auth_client, environment, new, "no").status_code == 200
        a4 = _create(a3, environment, secret_values={"OPENAI_API_KEY": "val-a4-value"})
        with django_capture_on_commit_callbacks(execute=True):
            assert _preflight(a4).status == "passed"
        body = auth_client.get(
            f"{BASE}/{environment.id}/nudges/", {"state": "pending,replaced"}
        ).json()
        assert [item["state"] for item in body] == ["pending", "replaced"]
        assert body[1]["replaced_by"]["id"] == str(new.id)

    def test_rolling_the_agent_back_to_what_the_environment_was_built_with_closes(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        settings,
        django_capture_on_commit_callbacks,
    ):
        from simulate.services.harness_versioning.versions import move_default

        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        environment, a1, a2 = _activated(
            user, workspace, storage, "n-rollback", django_capture_on_commit_callbacks
        )

        with django_capture_on_commit_callbacks(execute=True):
            move_default(environment, "agent", a1.id, expected_default_id=a2.id)

        [nudge] = _nudges(environment)
        assert nudge.state == "no_change"
        # The closed default card reconciles once; that reconcile schedules nothing.
        assert [call[0] for call in started] == ["raise", "raise", "reconcile"]

    def test_an_offer_stays_when_the_default_moves_back(
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
        from simulate.services.harness_versioning.versions import move_default

        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        environment, a1, a2 = _activated(
            user,
            workspace,
            storage,
            "n-offer-stays",
            django_capture_on_commit_callbacks,
        )
        [card] = _nudges(environment)
        _answer(auth_client, environment, card, "no")
        a3 = _checked(
            _create(a2, environment, secret_values={"OPENAI_API_KEY": "val-a3-value"})
        )
        nudges.raise_environment_nudge(environment.id, a3.id, default_moved=False)

        with django_capture_on_commit_callbacks(execute=True):
            move_default(environment, "agent", a1.id, expected_default_id=a2.id)

        states = {
            one.trigger_agent_version_id: one.state for one in _nudges(environment)
        }
        assert states == {a2.id: "answered_no", a3.id: "pending"}

    def test_move_agent_alone_raises_nothing_and_an_answered_no_change_stands(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        django_capture_on_commit_callbacks,
    ):
        from simulate.services.harness_versioning.versions import move_agent

        environment, a1, a2 = _activated(
            user, workspace, storage, "n-move-agent", django_capture_on_commit_callbacks
        )
        a3 = _create(a2, environment, secret_values={"OPENAI_API_KEY": "val-a3-value"})
        started.clear()
        with django_capture_on_commit_callbacks(execute=True):
            locked = HostedHarnessJob.no_workspace_objects.get(id=environment.id)
            move_agent(locked, a3)
        assert started == []

        environment.refresh_from_db()
        nudge = HarnessNudge.no_workspace_objects.create(
            job=environment,
            organization_id=environment.organization_id,
            target="environment",
            state="no_change",
            trigger_agent_version=a3,
            base_environment_version_id=environment.environment_version_id,
            answered_at=environment.created_at,
        )
        assert (
            nudges.raise_environment_nudge(environment.id, a3.id, default_moved=True).id
            == nudge.id
        )
        nudges.reconcile_environment_nudges(environment.id)
        assert {
            one.state for one in _nudges(environment, trigger_agent_version=a3)
        } == {"no_change"}

    def test_a_skipped_offer_asks_again_when_made_the_default_by_hand(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        environment, _a1_, a2 = _activated(
            user, workspace, storage, "n-skip-offer", django_capture_on_commit_callbacks
        )
        [card] = _nudges(environment)
        _answer(auth_client, environment, card, "no")
        a3 = _checked(
            _create(a2, environment, secret_values={"OPENAI_API_KEY": "val-a3-value"})
        )
        offer = nudges.raise_environment_nudge(
            environment.id, a3.id, default_moved=False
        )
        _answer(auth_client, environment, offer, "no")
        assert nudges.raise_environment_nudge(
            environment.id, a3.id, default_moved=False
        ) == (HarnessNudge.no_workspace_objects.get(id=offer.id))
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            agent_version=a3
        )

        moved = nudges.raise_environment_nudge(
            environment.id, a3.id, default_moved=True
        )

        assert moved.id != offer.id and moved.facts["agent_default_moved"] is True
        _answer(auth_client, environment, moved, "no")
        assert (
            nudges.raise_environment_nudge(environment.id, a3.id, default_moved=True).id
            == moved.id
        )

    @pytest.mark.parametrize("outcome", ["not_requested", "default_moved_by_hand"])
    def test_an_offer_waits_behind_the_pending_default_card(
        self,
        outcome,
        user,
        workspace,
        storage,
        scheduled,
        started,
        settings,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        from simulate.models import HarnessAgentVersionRuntime
        from simulate.tests.test_harness_agent_versions import _on_a2

        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        (first, second), _a1_, a2 = _on_a2(user, workspace, storage, "n-held")
        card = nudges.raise_environment_nudge(second.id, a2.id, default_moved=True)
        a3 = _create(a2, first, secret_values={"OPENAI_API_KEY": "val-openai-v3"})

        with django_capture_on_commit_callbacks(execute=True):
            assert _preflight(a3).status == "passed"
        side = HarnessAgentVersionRuntime.no_workspace_objects.get(agent_version=a3)
        assert nudges.reads._activation_outcome(side.preflight_report, second.id) == (
            "not_requested"
        )
        # A turned-down request is offered the same way.
        side.preflight_report["activation"] = [
            {**entry, "outcome": outcome}
            for entry in side.preflight_report["activation"]
        ]
        side.save(update_fields=["preflight_report"])
        nudges.reconcile_environment_nudges(second.id)
        nudges.reconcile_environment_nudges(second.id)

        assert _nudges(second) == [card]
        card.refresh_from_db()
        assert card.state == "pending"
        with django_capture_on_commit_callbacks(execute=True):
            assert _answer(auth_client, second, card, "no").status_code == 200

        skipped, offer = _nudges(second)
        assert (skipped.id, skipped.state) == (card.id, "answered_no")
        assert (offer.trigger_agent_version_id, offer.state) == (a3.id, "pending")
        assert offer.facts["agent_default_moved"] is False

    def _held(self, user, workspace, storage, key, capture, count=1):
        """Two environments on A2; the second's default card is pending while
        ``count`` newer versions of A2 pass for the first, held behind it."""
        from simulate.tests.test_harness_agent_versions import _on_a2

        (first, second), _a1_, a2 = _on_a2(user, workspace, storage, key)
        card = nudges.raise_environment_nudge(second.id, a2.id, default_moved=True)
        versions = []
        for number in range(count):
            version = _create(
                a2, first, secret_values={"OPENAI_API_KEY": f"val-held-{number}"}
            )
            with capture(execute=True):
                assert _preflight(version).status == "passed"
            versions.append(version)
        nudges.reconcile_environment_nudges(second.id)
        assert _nudges(second) == [card]
        return second, card, versions

    def test_only_the_newest_held_offer_is_raised_and_never_an_older_one(
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
        capture = django_capture_on_commit_callbacks
        second, card, (a3, a4) = self._held(
            user, workspace, storage, "n-newest", capture, count=2
        )

        with capture(execute=True):
            assert _answer(auth_client, second, card, "no").status_code == 200
        [offer] = _nudges(second, trigger_agent_version=a4)
        assert offer.state == "pending"
        nudges.reconcile_environment_nudges(second.id)
        offer.refresh_from_db()
        assert offer.state == "pending"
        assert _answer(auth_client, second, offer, "no").status_code == 200
        nudges.reconcile_environment_nudges(second.id)

        assert _nudges(second, trigger_agent_version=a3) == []
        assert not _nudges(second, state="pending")

    def test_a_held_offer_is_raised_once_the_environment_settles_the_card(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        settings,
        django_capture_on_commit_callbacks,
    ):
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        capture = django_capture_on_commit_callbacks
        second, card, [a3] = self._held(user, workspace, storage, "n-settle", capture)

        # A new environment version built with the default closes its card.
        with capture(execute=True):
            _contract_change(user, second, [{"name": "refund"}])

        card.refresh_from_db()
        assert card.state == "no_change"
        [offer] = _nudges(second, target="environment", state="pending")
        assert offer.trigger_agent_version_id == a3.id

    def test_an_offer_a_default_card_replaced_is_offered_again_after_the_skip(
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
        from simulate.tests.test_harness_agent_versions import _on_a2

        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        (first, second), _a1_, a2 = _on_a2(user, workspace, storage, "n-back")
        a3 = _create(a2, first, secret_values={"OPENAI_API_KEY": "val-openai-v3"})
        with django_capture_on_commit_callbacks(execute=True):
            assert _preflight(a3).status == "passed"
        offer = nudges.raise_environment_nudge(second.id, a3.id, default_moved=False)
        card = nudges.raise_environment_nudge(second.id, a2.id, default_moved=True)
        offer.refresh_from_db()
        assert offer.state == "replaced"

        with django_capture_on_commit_callbacks(execute=True):
            assert _answer(auth_client, second, card, "no").status_code == 200

        [again] = _nudges(second, trigger_agent_version=a3, state="pending")
        assert again.id != offer.id

    def test_an_offer_found_empty_is_not_diffed_again(
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
        capture = django_capture_on_commit_callbacks
        second, card, [a3] = self._held(user, workspace, storage, "n-empty", capture)
        assert _answer(auth_client, second, card, "no").status_code == 200
        diffed = []

        def no_facts(environment, base, new):
            diffed.append(new.id)
            return nudges._no_facts({"kind": "github", "changed": False})

        with patch.object(nudges, "diff_facts", side_effect=no_facts):
            nudges.reconcile_environment_nudges(second.id)
            nudges.reconcile_environment_nudges(second.id)

        assert diffed.count(a3.id) == 1
        [marker] = _nudges(second, trigger_agent_version=a3)
        assert marker.state == "no_change"

    def test_a_stale_default_card_closes_when_the_default_moves_back_to_a_skip(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        environment, _a1_, a2 = _activated(
            user, workspace, storage, "n-stale", django_capture_on_commit_callbacks
        )
        a3 = _checked(
            _create(a2, environment, secret_values={"OPENAI_API_KEY": "val-a3-value"})
        )

        def make_default(version):
            HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
                agent_version=version
            )
            return nudges.raise_environment_nudge(
                environment.id, version.id, default_moved=True
            )

        skipped = make_default(a3)
        _answer(auth_client, environment, skipped, "no")
        stale = make_default(a2)
        assert stale.state == "pending" and stale.trigger_agent_version_id == a2.id

        assert make_default(a3).id == skipped.id

        stale.refresh_from_db()
        assert stale.state == "replaced"
        assert not _nudges(environment, state="pending")

    @pytest.mark.parametrize("newer", ["pending", "answered_no", "no_change"])
    def test_an_older_offer_finishing_late_is_held_behind_a_newer_one(
        self, newer, user, workspace, storage, scheduled, started, auth_client
    ):
        from simulate.tests.test_harness_agent_versions import _on_a2

        (first, second), _a1_, a2 = _on_a2(user, workspace, storage, "n-late")
        a3, a4 = (
            _checked(_create(a2, first, secret_values={"OPENAI_API_KEY": value}))
            for value in ("val-late-3", "val-late-4")
        )
        if newer == "no_change":
            with patch.object(
                nudges,
                "diff_facts",
                return_value=nudges._no_facts({"kind": "github", "changed": False}),
            ):
                assert nudges.raise_environment_nudge(
                    second.id, a4.id, default_moved=False
                ) is None
        else:
            offer = nudges.raise_environment_nudge(second.id, a4.id, default_moved=False)
            if newer == "answered_no":
                assert _answer(auth_client, second, offer, "no").status_code == 200

        assert nudges.raise_environment_nudge(second.id, a3.id, default_moved=False) is None

        assert _nudges(second, trigger_agent_version=a3) == []
        [standing] = _nudges(second, target="environment")
        assert (standing.trigger_agent_version_id, standing.state) == (a4.id, newer)

    def test_an_empty_offer_is_not_named_as_the_replacement(
        self, user, workspace, storage, scheduled, started, auth_client
    ):
        from simulate.tests.test_harness_agent_versions import _on_a2

        (first, second), _a1_, a2 = _on_a2(user, workspace, storage, "n-marker")
        a3, a4, a5 = (
            _checked(_create(a2, first, secret_values={"OPENAI_API_KEY": value}))
            for value in ("val-mark-3", "val-mark-4", "val-mark-5")
        )
        old = nudges.raise_environment_nudge(second.id, a3.id, default_moved=False)
        with patch.object(
            nudges,
            "diff_facts",
            return_value=nudges._no_facts({"kind": "github", "changed": False}),
        ):
            assert nudges.raise_environment_nudge(
                second.id, a4.id, default_moved=False
            ) is None
        new = nudges.raise_environment_nudge(second.id, a5.id, default_moved=False)

        body = auth_client.get(f"{BASE}/{second.id}/nudges/").json()
        assert [item["state"] for item in body] == ["pending", "no_change", "replaced"]
        assert body[2]["id"] == str(old.id)
        assert body[2]["replaced_by"]["id"] == str(new.id)

    @pytest.mark.parametrize(
        "change",
        [
            "running",
            "extending",
            "unversioned",
            "deleted",
            "nudges_off",
            "versioning_off",
        ],
    )
    def test_nothing_is_raised_or_scheduled_while_it_cannot_be(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        settings,
        change,
        django_capture_on_commit_callbacks,
    ):
        [environment] = _shared(user, workspace, storage, f"n-guard-{change}", count=1)
        a1 = _a1(environment)
        a2 = _create(a1, environment, secret_values={"OPENAI_API_KEY": NEW_VALUE})
        updates = {
            "running": {"state": "running"},
            "deleted": {"deleted": True},
            "unversioned": {"environment_version": None},
        }.get(change)
        if updates:
            HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
                **updates
            )
        if change == "extending":
            payload = dict(environment.payload)
            payload["metadata"] = {**payload["metadata"], "scenario_extend": {"n": 1}}
            HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
                payload=payload
            )
        if change == "nudges_off":
            settings.HARNESS_VERSIONING_NUDGES_ENABLED = False
        if change == "versioning_off":
            settings.HARNESS_VERSIONING_ENABLED = False

        with django_capture_on_commit_callbacks(execute=True):
            _preflight(a2)
            nudges.schedule_reconcile(environment.id)
            nudges.on_environment_default_moved(
                environment.id,
                from_environment_version_id=None,
                environment_version_id=environment.environment_version_id,
            )
        assert (
            nudges.raise_environment_nudge(environment.id, a2.id, default_moved=False)
            is None
        )

        assert not HarnessNudge.no_workspace_objects.filter(job=environment).exists()
        if change not in ("running", "extending", "unversioned", "deleted"):
            assert started == []

    def test_a_failing_raise_never_fails_its_trigger(
        self,
        user,
        workspace,
        storage,
        scheduled,
        settings,
        caplog,
        django_capture_on_commit_callbacks,
    ):
        from simulate.services.harness_versioning.versions import move_default

        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        [environment] = _shared(user, workspace, storage, "n-fail", count=1)
        a1 = _a1(environment)
        a2 = _create(a1, environment, secret_values={"OPENAI_API_KEY": NEW_VALUE})
        with (
            patch.object(
                nudge_tasks.raise_harness_environment_nudge,
                "apply_async",
                side_effect=RuntimeError("temporal down"),
            ),
            django_capture_on_commit_callbacks(execute=True),
        ):
            assert _preflight(a2).status == "passed"
            move_default(environment, "agent", a1.id, expected_default_id=a2.id)

        assert "harness_nudge_schedule_failed" in caplog.text
        environment.refresh_from_db()
        assert environment.agent_version_id == a1.id
        assert not HarnessNudge.no_workspace_objects.filter(job=environment).exists()


def _answer(auth_client, environment, nudge, answer, **extra):
    return auth_client.post(
        f"{BASE}/{environment.id}/nudges/{nudge.id}/answer/",
        {"answer": answer, **extra},
        format="json",
    )


class TestAnswer:
    def test_skip_answers_no_and_leaves_the_card(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        environment, _a1_, _a2_ = _activated(
            user, workspace, storage, "n-skip", django_capture_on_commit_callbacks
        )
        [nudge] = _nudges(environment)
        card = nudge.conversation_message

        response = _answer(auth_client, environment, nudge, "no")

        assert response.status_code == 200
        body = response.json()
        assert body["state"] == "answered_no"
        assert body["answered_by"]["id"] == str(user.id)
        assert body["answered_at"] is not None
        nudge.refresh_from_db()
        assert (nudge.state, nudge.answered_by_id) == ("answered_no", user.id)
        card.refresh_from_db()
        assert card.payload["nudge_id"] == str(nudge.id)
        assert card.state == "completed"

        again = _answer(auth_client, environment, nudge, "no")

        assert again.status_code == 409
        assert again.json()["error"] == "nudge_closed"
        assert again.json()["state"] == "answered_no"

    def test_a_replaced_nudge_is_closed(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        environment, _a1_, a2 = _activated(
            user,
            workspace,
            storage,
            "n-skip-closed",
            django_capture_on_commit_callbacks,
        )
        a3 = _checked(
            _create(a2, environment, secret_values={"OPENAI_API_KEY": "val-a3-value"})
        )
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            agent_version=a3
        )
        nudges.raise_environment_nudge(environment.id, a3.id, default_moved=True)
        old = _nudges(environment)[0]

        response = _answer(auth_client, environment, old, "no")

        assert response.status_code == 409
        assert response.json()["state"] == "replaced"

    def test_yes_queues_a_rebuild_only_with_the_nudges_flag(
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
        environment, _a1_, _a2_ = _activated(
            user, workspace, storage, "n-yes", django_capture_on_commit_callbacks
        )
        [nudge] = _nudges(environment)
        settings.HARNESS_VERSIONING_NUDGES_ENABLED = False

        response = _answer(auth_client, environment, nudge, "yes")

        assert response.status_code == 409
        assert response.json()["error"] == "nudges_disabled"
        nudge.refresh_from_db()
        assert nudge.state == "pending"
        settings.HARNESS_VERSIONING_NUDGES_ENABLED = True
        with patch("simulate.services.harness_versioning.rebuild.schedule_nudge_check"):
            response = _answer(auth_client, environment, nudge, "yes")
        assert response.status_code == 200
        assert response.json()["state"] == "queued"
        assert response.json()["can_stop"] is True
        assert _answer(auth_client, environment, nudge, "no").status_code == 409
        settings.HARNESS_VERSIONING_NUDGES_ENABLED = False
        # Skip still works with the nudges flag off; a queued one is not pending.
        other = HarnessNudge.no_workspace_objects.create(
            job=environment,
            organization_id=environment.organization_id,
            target="suite",
            state="pending",
        )
        assert _answer(auth_client, environment, other, "no").status_code == 200

    def test_another_environments_nudge_and_bad_requests(
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
        environment, _a1_, _a2_ = _activated(
            user, workspace, storage, "n-scope", django_capture_on_commit_callbacks
        )
        [other] = _shared(user, workspace, storage, "n-scope-other", count=1)
        [nudge] = _nudges(environment)

        assert _answer(auth_client, other, nudge, "no").json()["error"] == (
            "nudge_not_found"
        )
        assert (
            auth_client.post(
                f"{BASE}/{environment.id}/nudges/{'0' * 36}/answer/",
                {"answer": "no"},
                format="json",
            ).status_code
            == 404
        )
        assert _answer(auth_client, environment, nudge, "maybe").status_code == 400
        assert (
            _answer(auth_client, environment, nudge, "no", extra=1).status_code == 400
        )
        listed = auth_client.get(
            f"{BASE}/{environment.id}/nudges/", {"state": "pending,answered_no"}
        )
        assert [item["id"] for item in listed.json()] == [str(nudge.id)]
        assert (
            auth_client.get(
                f"{BASE}/{environment.id}/nudges/", {"state": "replaced"}
            ).json()
            == []
        )
        assert (
            auth_client.get(
                f"{BASE}/{environment.id}/nudges/", {"state": "pending,nope"}
            ).status_code
            == 400
        )
        settings.HARNESS_VERSIONING_ENABLED = False
        for response in (
            auth_client.get(f"{BASE}/{environment.id}/nudges/"),
            _answer(auth_client, environment, nudge, "no"),
        ):
            assert response.status_code == 404
            assert response.json()["error"] == "versioning_disabled"

    def test_another_workspace_cannot_see_the_environment(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        from accounts.models.workspace import Workspace

        environment, _a1_, _a2_ = _activated(
            user, workspace, storage, "n-ws", django_capture_on_commit_callbacks
        )
        [nudge] = _nudges(environment)
        elsewhere = Workspace.objects.create(
            name="elsewhere", organization=user.organization, created_by=user
        )
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            workspace=elsewhere
        )

        assert auth_client.get(f"{BASE}/{environment.id}/nudges/").status_code == 404
        assert _answer(auth_client, environment, nudge, "no").status_code == 404

    def test_a_skip_waits_for_the_environment_lock(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        django_capture_on_commit_callbacks,
    ):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        environment, _a1_, _a2_ = _activated(
            user, workspace, storage, "n-lock", django_capture_on_commit_callbacks
        )
        [nudge] = _nudges(environment)

        with CaptureQueriesContext(connection) as queries:
            nudges.answer_nudge(environment, nudge.id, answer="no", user=user)

        locks = [q["sql"] for q in queries if "FOR UPDATE" in q["sql"]]
        assert "simulate_hosted_harness_job" in locks[0]
        assert "simulate_harness_nudge" in locks[1]


# --- facts ----------------------------------------------------------------------------

BASE_SHA = "a" * 40
NEW_SHA = "b" * 40
LONG_SECRET = "sk-live-0123456789"
SHORT_SECRET = "zq7x9"


@pytest.fixture
def clean_cache():
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


def _runtime(kind="github", source=None, *, names=(), fingerprints=None, **fields):
    return SimpleNamespace(
        source_kind=kind,
        source=(
            source
            if source is not None
            else {"repository": "acme/agent", "ref": "main", "commit_sha": BASE_SHA}
        ),
        credential_names=list(names),
        credential_fingerprints=dict(fingerprints or {}),
        prompt_hash=fields.pop("prompt_hash", "sha256:same"),
        agent_payload=fields.pop("agent_payload", {}),
        preflight_status=fields.pop("preflight_status", "passed"),
        **fields,
    )


def _facts(base, new, *, secrets=None, environment=None):
    """``diff_facts`` between two made-up side rows, with these values to redact."""
    versions = {"base": base, "new": new}
    environment = environment or SimpleNamespace(id="env", organization_id="org")
    with (
        patch.object(
            nudges,
            "_effective",
            side_effect=lambda _env, version: (
                versions[version.id] if version is not None else None
            ),
        ),
        patch.object(
            nudges,
            "_agent_secrets",
            return_value=secrets if secrets is not None else {},
        ),
    ):
        return nudges.diff_facts(
            environment, SimpleNamespace(id="base"), SimpleNamespace(id="new")
        )


def _github(new_sha=NEW_SHA, **fields):
    return _runtime(
        source={"repository": "acme/agent", "ref": "main", "commit_sha": new_sha},
        **fields,
    )


def _compare_body(status="ahead", files=()):
    return {"status": status, "files": list(files)}


def _gh_file(name, status="modified", patch_text="@@ -1,1 +1,1 @@\n-a\n+b\n", **extra):
    return {
        "filename": name,
        "status": status,
        "additions": 1,
        "deletions": 1,
        **({"patch": patch_text} if patch_text is not None else {}),
        **extra,
    }


def _compares(*bodies):
    from simulate.services import harness_github
    from simulate.services.harness_github import GitHubResponse

    return patch.object(
        harness_github,
        "github_get",
        side_effect=[GitHubResponse("ok", body) for body in bodies],
    )


class TestFacts:
    def test_variables_by_fingerprint_with_names_added_and_removed(self):
        base = _runtime(
            names=["A", "B", "C"], fingerprints={"A": "1", "B": "2", "C": "3"}
        )
        new = _runtime(
            names=["A", "B", "D"], fingerprints={"A": "1", "B": "9", "D": "4"}
        )

        facts = _facts(base, new)

        assert facts["variables"] == {
            "added": ["D"],
            "removed": ["C"],
            "changed": ["B"],
            "unknown": [],
        }
        assert not nudges.facts_empty(facts)
        assert nudges.facts_empty(_facts(base, copy.copy(base)))

    def test_a_github_compare_lists_files_and_omits_secret_patches(self, clean_cache):
        secret_patch = (
            f"@@ -1,2 +1,2 @@\n-key = old\n+client({LONG_SECRET!r})\n"
            f"+short = {SHORT_SECRET}\n+keep zq7x9y\n"
        )
        files = [
            _gh_file("tools/orders.py", patch_text=secret_patch),
            _gh_file("tools/new.py", "added"),
            _gh_file("tools/copy.py", "copied"),
            _gh_file("tools/touched.py", "changed"),
            _gh_file("tools/same.py", "unchanged"),
            _gh_file(".env"),
            _gh_file("deploy/.env.production"),
            _gh_file("secrets/key.pem"),
            _gh_file("creds/adc.json"),
            _gh_file(".env.example"),
            _gh_file("img/logo.png", patch_text=None, additions=0, deletions=0),
        ]
        with _compares(_compare_body("ahead", files)) as github_get:
            facts = _facts(
                _runtime(),
                _github(),
                secrets={
                    "OPENAI_API_KEY": {LONG_SECRET},
                    "PIN": {SHORT_SECRET},
                    "GOOGLE_APPLICATION_CREDENTIALS": {"/work/creds/adc.json"},
                },
            )

        assert github_get.call_args.args == (
            f"/repos/acme/agent/compare/{BASE_SHA}...{NEW_SHA}",
        )
        assert github_get.call_args.kwargs["purpose"] == "compare"
        by_path = {one["path"]: one for one in facts["changed_files"]}
        assert "tools/same.py" not in by_path
        assert by_path["tools/copy.py"]["status"] == "copied"
        assert by_path["tools/touched.py"]["status"] == "modified"
        for path in (
            ".env",
            "deploy/.env.production",
            "secrets/key.pem",
            "creds/adc.json",
        ):
            assert by_path[path]["patch"] is None
            assert by_path[path]["patch_omitted"] == "secret_path"
        assert by_path[".env.example"]["patch"] is not None
        assert by_path["img/logo.png"]["patch_omitted"] == "binary"
        patch_text = by_path["tools/orders.py"]["patch"]
        assert LONG_SECRET not in patch_text
        assert f"short = {SHORT_SECRET}" not in patch_text
        assert "zq7x9y" in patch_text
        assert facts["source"] == {
            "kind": "github",
            "changed": True,
            "from": BASE_SHA,
            "to": NEW_SHA,
        }
        assert facts["files_total"] == len(by_path)
        assert LONG_SECRET not in json.dumps(facts)

    def test_a_roll_back_to_an_ancestor_compares_backwards(self, clean_cache):
        forward = [
            _gh_file(
                "tools/orders.py",
                patch_text="@@ -1,2 +1,3 @@\n keep\n-old\n+new\n+more\n",
                additions=2,
                deletions=1,
            ),
            _gh_file("tools/added.py", "added"),
            _gh_file("tools/new_name.py", "renamed", previous_filename="tools/old.py"),
        ]
        with _compares(
            _compare_body("behind"), _compare_body("ahead", forward)
        ) as github_get:
            facts = _facts(_runtime(), _github())

        first, second = (call.args[0] for call in github_get.call_args_list)
        assert first.endswith(f"{BASE_SHA}...{NEW_SHA}")
        assert second.endswith(f"{NEW_SHA}...{BASE_SHA}")
        assert facts["reversed"] is True
        by_path = {one["path"]: one for one in facts["changed_files"]}
        orders = by_path["tools/orders.py"]
        assert (orders["additions"], orders["deletions"]) == (1, 2)
        assert orders["patch"] == "@@ -1,3 +1,2 @@\n keep\n+old\n-new\n-more\n"
        assert by_path["tools/added.py"]["status"] == "removed"
        assert by_path["tools/old.py"]["previous_path"] == "tools/new_name.py"
        assert not nudges.facts_empty(facts)

    def test_diverged_commits_are_not_linear_after_one_call(self, clean_cache):
        with _compares(_compare_body("diverged")) as github_get:
            facts = _facts(_runtime(), _github())

        assert github_get.call_count == 1
        assert facts["files_unavailable"] == "not_linear"
        assert not nudges.facts_empty(facts)

    def test_patches_are_capped_by_size_budget_and_count(self, clean_cache):
        big = "@@ -1 +1 @@\n+" + "x" * (25 * 1024) + "\n"
        mid = "@@ -1 +1 @@\n+" + "y" * (15 * 1024) + "\n"
        files = [_gh_file("big.py", patch_text=big)] + [
            _gh_file(f"mid{n}.py", patch_text=mid) for n in range(16)
        ]
        with _compares(_compare_body("ahead", files)):
            facts = _facts(_runtime(), _github())
        omitted = [one["patch_omitted"] for one in facts["changed_files"]]
        assert omitted[0] == "too_large"
        assert omitted[1:14] == [None] * 13
        assert set(omitted[14:]) == {"budget"}

        many = [_gh_file(f"f{n}.py") for n in range(300)]
        with _compares(_compare_body("ahead", many)):
            facts = _facts(_runtime(), _github("c" * 40))
        assert facts["files_truncated"] is True

    def test_a_private_repository_makes_no_call(self, clean_cache):
        private = {"source_access": {"visibility": "private"}}
        with _compares() as github_get:
            facts = _facts(_runtime(agent_payload=private), _github())

        assert github_get.call_count == 0
        assert facts["files_unavailable"] == "private_repository"

    def test_rate_limits_errors_and_the_shared_cache(self, clean_cache, settings):
        import requests
        from django.core.cache import cache

        from simulate.services import harness_github

        def response(status_code, body=None, headers=None):
            found = SimpleNamespace(
                status_code=status_code, headers=headers or {}, text=""
            )
            found.json = lambda: body
            return found

        limited = response(403, headers={"X-RateLimit-Remaining": "0"})
        with patch.object(harness_github.requests, "get", side_effect=[limited]):
            facts = _facts(_runtime(), _github())
        assert facts["files_unavailable"] == "rate_limited"
        assert cache.get(harness_github.RATE_RESET_KEY) is not None

        with patch.object(harness_github.requests, "get") as get:
            facts = _facts(_runtime(), _github())
        assert get.call_count == 0
        assert facts["files_unavailable"] == "rate_limited"

        cache.clear()
        with patch.object(
            harness_github.requests, "get", side_effect=requests.Timeout()
        ):
            facts = _facts(_runtime(), _github())
        assert facts["files_unavailable"] == "github_error"

        ok = response(200, _compare_body("ahead", [_gh_file("a.py")]))
        with patch.object(harness_github.requests, "get", side_effect=[ok]) as get:
            first = _facts(_runtime(), _github())
            second = _facts(_runtime(), _github())
        assert get.call_count == 1
        assert first["changed_files"] == second["changed_files"]
        assert get.call_args.kwargs["headers"].get("Authorization") is None

        # An organization past its share of the hour's comparisons reads rate_limited.
        cache.clear()
        settings.HARNESS_GITHUB_COMPARE_SHARE = 1
        with patch.object(harness_github.requests, "get", return_value=ok) as get:
            assert _facts(_runtime(), _github())["files_unavailable"] is None
            facts = _facts(_runtime(), _github(new_sha="c" * 40))
        assert get.call_count == 1
        assert facts["files_unavailable"] == "rate_limited"

    def test_an_upload_is_compared_by_member(self, storage):
        import io
        import tarfile

        def tar(files):
            out = io.BytesIO()
            with tarfile.open(fileobj=out, mode="w:gz") as archive:
                for path, data in files.items():
                    member = tarfile.TarInfo(f"source/{path}")
                    member.size = len(data)
                    archive.addfile(member, io.BytesIO(data))
            return out.getvalue()

        storage.objects["harness-sources/org/old.tar.gz"] = tar(
            {"agent.py": b"a = 1\n", "gone.py": b"x\n", "logo.png": b"\xff\x00"}
        )
        storage.objects["harness-sources/org/new.tar.gz"] = tar(
            {"agent.py": b"a = 2\n", "added.py": b"y\n", "logo.png": b"\xff\x01"}
        )

        def upload(name):
            return _runtime("archive", {"archive_artifact_id": name})

        facts = _facts(upload("old"), upload("new"))

        by_path = {one["path"]: one for one in facts["changed_files"]}
        assert {path: one["status"] for path, one in by_path.items()} == {
            "agent.py": "modified",
            "added.py": "added",
            "gone.py": "removed",
            "logo.png": "modified",
        }
        assert "+a = 2" in by_path["agent.py"]["patch"]
        assert (by_path["agent.py"]["additions"], by_path["agent.py"]["deletions"]) == (
            1,
            1,
        )
        assert by_path["logo.png"]["patch_omitted"] == "binary"
        assert facts["source"]["from"] == "old"

        assert _facts(upload("old"), upload("missing"))["files_unavailable"] == (
            "archive_missing"
        )
        with patch.object(nudges, "NUDGE_DIFF_MAX_INFLATED", 1):
            facts = _facts(upload("old"), upload("new"))
        assert facts["files_unavailable"] == "archive_too_large"

    def test_a_prompt_change_is_diffed_and_redacted(self):
        base = _runtime(
            prompt_hash="sha256:1",
            agent_payload={"config": {"target_system_prompt": "Be kind.\n"}},
        )
        new = _runtime(
            prompt_hash="sha256:2",
            agent_payload={
                "config": {"target_system_prompt": f"Be kind.\nKey {LONG_SECRET}\n"}
            },
        )

        facts = _facts(base, new, secrets={"OPENAI_API_KEY": {LONG_SECRET}})

        assert facts["prompt_changed"] is True
        assert "+Key [REDACTED]" in facts["prompt_patch"]
        assert facts["settings_changed"] == []

    def test_multi_line_and_newline_ended_file_values_are_masked_in_a_patch(self):
        key_id, access_key = "AKIA0123456789ABCD", "wJalr0123456789xyzSECRET"
        ini = (
            f"[default]\naws_access_key_id = {key_id}\n"
            f"aws_secret_access_key = {access_key}\n"
        )
        patch_text = (
            "@@ -0,0 +1,4 @@\n"
            "+[default]\n"
            f"+aws_access_key_id = {key_id}\n"
            f"+aws_secret_access_key = {access_key}\n"
            f'+KEY = "{LONG_SECRET}"\n'
        )

        [redacted] = nudges._redact_texts([patch_text], [ini, f"{LONG_SECRET}\n"])

        for value in (key_id, access_key, LONG_SECRET):
            assert value not in redacted

    @pytest.mark.parametrize(
        "config",
        [
            "[default]\nregion = us-east-1\noutput = json\n",
            "apiVersion: v1\nkind: Config\ncurrent-context: default\nclusters:\n"
            "- name: default\n  cluster:\n    server: https://k8s.example\n",
        ],
        ids=["aws-config", "kubeconfig"],
    )
    def test_a_config_file_value_keeps_ordinary_words_in_a_patch(self, config):
        patch_text = (
            "@@ -0,0 +1,3 @@\n+import json\n"
            '+data = json.load(open("config.json"))\n+mode = "default"\n'
        )

        [redacted] = nudges._redact_texts([patch_text], [config])

        assert redacted == patch_text

    def test_a_config_file_value_of_8_characters_is_masked_anywhere(self):
        # Nothing tells a non-secret value from a secret one, so it's masked too.
        [redacted] = nudges._redact_texts(
            ['+region = "us-east-1"\n'], ["[default]\nregion = us-east-1\n"]
        )

        assert redacted == '+region = "[REDACTED]"\n'

    def test_a_single_line_file_value_is_masked_by_its_value(self):
        [redacted] = nudges._redact_texts(
            [f'+client = Client("{LONG_SECRET}")\n'],
            [f"OPENAI_API_KEY={LONG_SECRET}\n"],
        )

        assert redacted == '+client = Client("[REDACTED]")\n'

    @pytest.mark.parametrize(
        ("value", "secret"),
        [
            (
                '[default]\naws_secret_access_key = "wJalrXUtnFEMIK7MDENG" # rotated\n',
                "wJalrXUtnFEMIK7MDENG",
            ),
            (
                "[default]\naws_secret_access_key = wJalrXUtnFEMIK7MDENG # rotated\n",
                "wJalrXUtnFEMIK7MDENG",
            ),
            ('db:\n  password: "s3cr3tpassw0rd"  # prod\n', "s3cr3tpassw0rd"),
            ("secret='abcdefghijk123';\n", "abcdefghijk123"),
        ],
        ids=["ini-quoted", "ini-unquoted", "yaml", "semicolon"],
    )
    def test_a_file_value_with_an_inline_comment_is_masked_by_its_value(
        self, value, secret
    ):
        [redacted] = nudges._redact_texts([f"+k = '{secret}'\n"], [value])

        assert redacted == "+k = '[REDACTED]'\n"

    def test_settings_changes_are_named_without_values(self):
        base = _runtime(
            agent_payload={"mode": "voice", "config": {"phone_number": "+1555"}}
        )
        new = _runtime(
            agent_payload={
                "mode": "chat",
                "config": {"phone_number": "+1666"},
                "requested_by_environments": ["x"],
            }
        )

        facts = _facts(base, new)

        assert facts["settings_changed"] == ["config.phone_number", "mode"]
        assert "+1666" not in json.dumps(facts)

    def test_patches_are_dropped_when_values_cant_be_read(self, clean_cache):
        with (
            _compares(_compare_body("ahead", [_gh_file("a.py")])),
            patch.object(
                nudges,
                "_effective",
                side_effect=lambda _e, v: {"base": _runtime(), "new": _github()}[v.id],
            ),
            patch.object(nudges, "_agent_secrets", side_effect=RuntimeError("x")),
        ):
            facts = nudges.diff_facts(
                SimpleNamespace(id="env", organization_id="org"),
                SimpleNamespace(id="base"),
                SimpleNamespace(id="new"),
            )

        [one] = facts["changed_files"]
        assert (one["patch"], one["patch_omitted"]) == (None, "redaction_unavailable")

    def test_each_legacy_sharer_compares_its_own_stored_values(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        django_capture_on_commit_callbacks,
    ):
        from simulate.models import HostedHarnessSecret

        environment, a1, a2 = _activated(
            user, workspace, storage, "n-sharer", django_capture_on_commit_callbacks
        )
        [other] = _shared(user, workspace, storage, "n-sharer-y", count=1)
        HostedHarnessJob.no_workspace_objects.filter(id=other.id).update(
            agent_version=a1
        )
        payload = dict(other.payload)
        refs = dict(payload["agent"]["secret_refs"])
        refs["LIVEKIT_API_KEY"] = {**refs["LIVEKIT_API_KEY"], "key": "harness-lk-y"}
        payload["agent"] = {**payload["agent"], "secret_refs": refs}
        HostedHarnessJob.no_workspace_objects.filter(id=other.id).update(
            payload=payload
        )
        HostedHarnessSecret.objects.create(
            organization=user.organization,
            name="harness-lk-y",
            version=refs["LIVEKIT_API_KEY"].get("version") or "1",
            encrypted_value="val-lk-key-y",
        )
        other.refresh_from_db()

        assert nudges.diff_facts(environment, a1, a2)["variables"]["changed"] == [
            "OPENAI_API_KEY"
        ]
        assert nudges.diff_facts(other, a1, a2)["variables"]["changed"] == [
            "LIVEKIT_API_KEY",
            "OPENAI_API_KEY",
        ]

    def test_an_unreadable_legacy_value_may_have_changed(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        django_capture_on_commit_callbacks,
    ):
        from simulate.models import HostedHarnessSecret

        environment, a1, a2 = _activated(
            user, workspace, storage, "n-unknown", django_capture_on_commit_callbacks
        )
        HostedHarnessSecret.objects.filter(organization=user.organization).delete()

        facts = nudges.diff_facts(environment, a1, a2)

        assert facts["variables"]["changed"] == []
        assert "OPENAI_API_KEY" in facts["variables"]["unknown"]
        assert not nudges.facts_empty(facts)
        # The stored facts of the raise are what the nudge computes again.
        [nudge] = _nudges(environment)
        assert nudges.nudge_facts(environment, nudge)["agent_default_moved"] is True

    def test_the_list_never_carries_a_patch(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        environment, _a1_, _a2_ = _activated(
            user, workspace, storage, "n-patchless", django_capture_on_commit_callbacks
        )
        [nudge] = _nudges(environment)
        nudge.facts = {
            **nudge.facts,
            "prompt_changed": True,
            "prompt_patch": "@@ -1 +1 @@\n-a\n+b\n",
            "changed_files": [
                {
                    **nudges._file(
                        "a.py", "modified", additions=1, deletions=1, patch="+b"
                    ),
                    "notes": "kept for the builder",
                }
            ],
            "briefing": "kept for the builder",
        }
        nudge.save(update_fields=["facts"])

        [item] = auth_client.get(f"{BASE}/{environment.id}/nudges/").json()

        assert "prompt_patch" not in item["facts"]
        assert "briefing" not in item["facts"]
        [one] = item["facts"]["changed_files"]
        assert "patch" not in one and one["has_patch"] is True
        assert "notes" not in one
        assert "card_message_id" not in item["facts"]
        from simulate.serializers.harness_environment import HarnessNudgeSerializer

        assert HarnessNudgeSerializer(data=item).is_valid(), item

    def test_the_public_facts_are_the_ones_the_serializers_declare(self):
        from rest_framework import serializers

        from simulate.serializers.harness_environment import (
            HarnessNudgeFactsSerializer,
            HarnessNudgeFileSerializer,
        )

        facts = {
            "tools_changed": True,
            "changed_files": [{"path": "a.py", "owner": "x", "patch": None}],
        }
        with (
            patch.dict(
                HarnessNudgeFactsSerializer._declared_fields,
                tools_changed=serializers.BooleanField(),
            ),
            patch.dict(
                HarnessNudgeFileSerializer._declared_fields,
                owner=serializers.CharField(),
            ),
        ):
            public = nudges._public_facts(facts)

        assert public["tools_changed"] is True
        assert public["changed_files"][0]["owner"] == "x"
        public = nudges._public_facts(facts)
        assert "tools_changed" not in public
        assert "owner" not in public["changed_files"][0]


def _contract_change(user, environment, tools):
    from simulate.services.harness_versioning.versions import record_authoring
    from simulate.tests.test_harness_versioning_record import _authoring

    return record_authoring(
        environment,
        _authoring(contract={"agent": "support", "tools": tools}),
        origin="chat",
        user=user,
    )


class TestSuite:
    def test_environment_moves_raise_the_suite_nudge_unless_proved(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        settings,
        django_capture_on_commit_callbacks,
    ):
        from django.utils import timezone

        from simulate.models import HarnessSuiteValidation
        from simulate.services.harness_versioning.versions import move_default

        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        [environment] = _shared(user, workspace, storage, "n-suite", count=1)
        e1 = environment.environment_version
        with django_capture_on_commit_callbacks(execute=True):
            recorded = _contract_change(user, environment, [{"name": "refund"}])
        e2 = recorded.environment

        [chat] = _nudges(environment, target="suite")
        assert chat.base_environment_version_id == e2.id
        assert chat.facts["from_environment_version_id"] == str(e1.id)
        assert chat.facts["environment_diff"]["contract"]["tools"]["added"]
        assert "Rebuild the scenarios?" in chat.conversation_message.content

        with django_capture_on_commit_callbacks(execute=True):
            move_default(environment, "environment", e1.id, expected_default_id=e2.id)
        chat.refresh_from_db()
        assert chat.state == "replaced"
        [back] = _nudges(environment, target="suite", state="pending")
        assert back.base_environment_version_id == e1.id

        environment.refresh_from_db()
        HarnessSuiteValidation.no_workspace_objects.create(
            suite_version_id=environment.suite_version_id,
            environment_version=e2,
            status="passed",
            proved_at=timezone.now(),
        )
        with django_capture_on_commit_callbacks(execute=True):
            move_default(environment, "environment", e2.id, expected_default_id=e1.id)

        back.refresh_from_db()
        assert back.state == "no_change"
        assert not _nudges(environment, target="suite", state="pending")

    def test_reconcile_closes_a_proved_suite_card_and_raises_a_skipped_one(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        settings,
        django_capture_on_commit_callbacks,
    ):
        from django.utils import timezone

        from simulate.models import HarnessSuiteValidation
        from simulate.services.harness_versioning.versions import move_default

        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        [environment] = _shared(user, workspace, storage, "n-suite-rec", count=1)
        e1 = environment.environment_version
        with django_capture_on_commit_callbacks(execute=True):
            e2 = _contract_change(user, environment, [{"name": "refund"}]).environment
        [card] = _nudges(environment, target="suite")
        environment.refresh_from_db()
        # An extend proves the suite against E2 without moving E.
        HarnessSuiteValidation.no_workspace_objects.create(
            suite_version_id=environment.suite_version_id,
            environment_version=e2,
            status="passed",
            proved_at=timezone.now(),
        )

        nudges.reconcile_environment_nudges(environment.id)

        card.refresh_from_db()
        assert card.state == "no_change"
        # E moves back while the raise can't run (a busy environment): it is skipped.
        with (
            patch.object(nudges, "_raisable", return_value=False),
            django_capture_on_commit_callbacks(execute=True),
        ):
            move_default(environment, "environment", e1.id, expected_default_id=e2.id)
        assert not _nudges(environment, target="suite", state="pending")

        nudges.reconcile_environment_nudges(environment.id)
        nudges.reconcile_environment_nudges(environment.id)

        [skipped] = _nudges(environment, target="suite", state="pending")
        assert skipped.base_environment_version_id == e1.id

    def test_a_suite_move_closes_a_card_the_new_suite_is_proved_against(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        settings,
        django_capture_on_commit_callbacks,
    ):
        from django.utils import timezone

        from simulate.models import HarnessSuiteValidation
        from simulate.services.harness_versioning.versions import (
            move_default,
            record_authoring,
        )
        from simulate.tests.test_harness_amend_archive import NAMES
        from simulate.tests.test_harness_versioning_record import _authoring

        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        [environment] = _shared(user, workspace, storage, "n-suite-move", count=1)
        tools = [{"name": "refund"}]
        with django_capture_on_commit_callbacks(execute=True):
            e2 = _contract_change(user, environment, tools).environment
        environment.refresh_from_db()
        proved = environment.suite_version
        with django_capture_on_commit_callbacks(execute=True):
            record_authoring(
                environment,
                _authoring(
                    names=NAMES[:1], contract={"agent": "support", "tools": tools}
                ),
                origin="manual",
                user=user,
            )
        environment.refresh_from_db()
        assert environment.environment_version_id == e2.id
        assert environment.suite_version_id != proved.id
        [card] = _nudges(environment, target="suite", state="pending")
        HarnessSuiteValidation.no_workspace_objects.create(
            suite_version=proved,
            environment_version=e2,
            status="passed",
            proved_at=timezone.now(),
        )

        with django_capture_on_commit_callbacks(execute=True):
            move_default(
                environment,
                "suite",
                proved.id,
                expected_default_id=environment.suite_version_id,
            )

        card.refresh_from_db()
        assert card.state == "no_change"

    def test_a_scenarios_only_save_and_a_first_record_raise_none(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        django_capture_on_commit_callbacks,
    ):
        from simulate.services.harness_versioning.versions import record_authoring
        from simulate.tests.test_harness_amend_archive import NAMES
        from simulate.tests.test_harness_versioning_backfill import _legacy
        from simulate.tests.test_harness_versioning_record import _authoring

        [environment] = _shared(user, workspace, storage, "n-suite-none", count=1)
        with django_capture_on_commit_callbacks(execute=True):
            record_authoring(
                environment, _authoring(names=NAMES[:1]), origin="manual", user=user
            )
        assert _nudges(environment) == []

        fresh = _legacy(user, workspace, "n-suite-first", storage)
        with django_capture_on_commit_callbacks(execute=True):
            record_authoring(fresh, _authoring(), origin="authoring")
        fresh.refresh_from_db()
        assert fresh.environment_version_id is not None
        assert _nudges(fresh) == []

    def test_an_open_environment_nudge_follows_the_environment(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        settings,
        django_capture_on_commit_callbacks,
    ):
        environment, _a1_, a2 = _activated(
            user, workspace, storage, "n-follow", django_capture_on_commit_callbacks
        )
        [first] = _nudges(environment, target="environment")

        with django_capture_on_commit_callbacks(execute=True):
            other = _contract_change(user, environment, [{"name": "other"}])
        # Built while the default was A1's successor A2, so it records A2.
        first.refresh_from_db()
        assert other.environment.agent_version_id == a2.id
        assert first.state == "no_change"

        HarnessNudge.no_workspace_objects.filter(id=first.id).update(state="pending")
        from simulate.models import HarnessEnvironmentVersion

        HarnessEnvironmentVersion.no_workspace_objects.filter(
            id=other.environment.id
        ).update(agent_version=_a1_)
        with django_capture_on_commit_callbacks(execute=True):
            nudges.on_environment_default_moved(
                environment.id,
                from_environment_version_id=first.base_environment_version_id,
                environment_version_id=other.environment.id,
            )

        first.refresh_from_db()
        assert first.state == "replaced"
        [again] = _nudges(environment, target="environment", state="pending")
        assert again.trigger_agent_version_id == a2.id
        assert again.base_environment_version_id == other.environment.id


class TestReadersAndDeletion:
    @pytest.mark.parametrize(
        "payload,request_id",
        [
            ({"command_kind": "nudge"}, "ok-1"),
            ({"nudge_id": "x"}, "ok-2"),
            ({"origin": "nudge"}, "ok-3"),
            ({"_environment_revision": "x"}, "ok-4"),
            ({}, "nudge-x"),
        ],
    )
    def test_a_forged_nudge_turn_is_refused(self, payload, request_id):
        from simulate.serializers.hosted_harness_conversation import (
            HarnessConversationMessageCreateSerializer,
        )

        serializer = HarnessConversationMessageCreateSerializer(
            data={"content": "hi", "client_request_id": request_id, "payload": payload}
        )

        assert not serializer.is_valid()
        assert set(serializer.errors) <= {"payload", "client_request_id"}

    def test_the_chats_own_payload_is_accepted(self):
        from simulate.serializers.hosted_harness_conversation import (
            HarnessConversationMessageCreateSerializer,
        )

        serializer = HarnessConversationMessageCreateSerializer(
            data={
                "content": "hi",
                "client_request_id": "abc-123",
                "payload": {"mode": "build", "scenario_ids": ["1"]},
            }
        )

        assert serializer.is_valid(), serializer.errors

    def test_a_run_of_the_agent_names_its_open_or_skipped_update(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        from simulate.services.harness_versioning.run_warnings import preview_run
        from simulate.services.hosted_harness import VersionPicks

        environment, a1, a2 = _activated(
            user, workspace, storage, "n-warn", django_capture_on_commit_callbacks
        )
        [nudge] = _nudges(environment)

        def codes(**picks):
            warnings = preview_run(environment, VersionPicks(**picks))["warnings"]
            return {one["code"]: one for one in warnings}

        found = codes()["nudge_open"]
        assert found["piece"] == "agent"
        assert "hasn't been answered" in found["message"]
        body = auth_client.get(f"{BASE}/{environment.id}/run-plan/").json()
        assert "nudge_open" in {one["code"] for one in body["warnings"]}

        _answer(auth_client, environment, nudge, "no")
        assert "was skipped" in codes()["nudge_open"]["message"]
        # The agent the environment was built with has nothing open.
        assert "nudge_open" not in codes(agent_version_id=a1.id)

        HarnessNudge.no_workspace_objects.filter(id=nudge.id).update(state="replaced")
        assert "nudge_open" not in codes()

    def test_deleting_the_environment_closes_its_open_nudges(
        self,
        user,
        workspace,
        storage,
        scheduled,
        started,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        environment, _a1_, _a2_ = _activated(
            user, workspace, storage, "n-delete", django_capture_on_commit_callbacks
        )
        [nudge] = _nudges(environment)
        skipped = HarnessNudge.no_workspace_objects.create(
            job=environment,
            organization_id=environment.organization_id,
            target="suite",
            state="answered_no",
        )

        assert auth_client.delete(f"{BASE}/{environment.id}/").status_code == 204

        nudge.refresh_from_db()
        skipped.refresh_from_db()
        assert (nudge.state, nudge.error) == ("failed", {"code": "environment_deleted"})
        assert skipped.state == "answered_no"


class TestReconcileHooks:
    def test_a_completed_build_and_an_extend_reconcile(
        self, user, workspace, storage, monkeypatch, settings
    ):
        from simulate.models import HostedHarnessAttempt
        from simulate.services.hosted_harness import record_cleanup
        from simulate.services.hosted_harness_gateway import freeze_authoring_archive
        from simulate.tests.test_harness_amend_archive import NAMES, _run_environment
        from simulate.tests.test_harness_versioning_record import _authoring
        from simulate.tests.test_harness_versioning_saves import (
            _mark_extend,
            _versioned_building,
        )

        built = _run_environment(user, workspace, "n-hooks-build")
        HostedHarnessJob.no_workspace_objects.filter(id=built.id).update(
            state="running"
        )
        attempt = HostedHarnessAttempt.no_workspace_objects.get(job=built)
        attempt.terminal_stage = "completed"
        attempt.save(update_fields=["terminal_stage", "updated_at"])
        extended = _versioned_building(
            user, workspace, "n-hooks-extend", monkeypatch, settings
        )
        _mark_extend(extended)

        from simulate.services.harness_versioning import rebuild

        with (
            patch.object(nudges, "schedule_reconcile") as reconcile,
            patch.object(rebuild, "schedule_deferred_activations") as deferred,
        ):
            job = record_cleanup(
                attempt.id,
                provider_ref=attempt.provider_ref or "",
                verified_absent=True,
            )
            assert job.state == HostedHarnessJob.State.COMPLETED
            freeze_authoring_archive(extended, _authoring(NAMES), extending=True)

        assert [call.args for call in reconcile.call_args_list] == [
            (built.id,),
            (extended.id,),
        ]
        # Activations that waited for either to finish are retried at once.
        assert [call.args for call in deferred.call_args_list] == [
            (built.id,),
            (extended.id,),
        ]
