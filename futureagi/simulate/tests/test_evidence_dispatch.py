"""Evidence drafting runs in a throwaway job of the import's own: its chat, memory
and checkpoints never touch the environment's chat, it holds up no Save and shows
in no list, and once its turn settles everything it held is deleted.

Real chats and checkpoints on an environment built to versions; sandboxes and the
scheduler are stand-ins.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from simulate.models import (
    EvidenceImport,
    EvidenceImportCase,
    HostedHarnessConversation,
    HostedHarnessConversationEvent,
    HostedHarnessConversationMessage,
    HostedHarnessConversationTranscript,
    HostedHarnessJob,
)
from simulate.selectors.runs import user_environment_runs
from simulate.services import evidence_drafts
from simulate.services.harness_versioning import drafts
from simulate.services.hosted_harness import HostedHarnessError, create_hosted_job
from simulate.services.hosted_harness_conversation import (
    enqueue_message,
    issue_conversation_capability,
)
from simulate.services.hosted_harness_gateway import rewrite_conversation_scenarios
from simulate.tests.test_evidence_drafts import (  # noqa: F401 - fixtures
    INJECTED,
    _end_turn,
    _promote,
    _save,
)
from simulate.tests.test_evidence_drafts import cases as cases
from simulate.tests.test_evidence_drafts import environment as environment
from simulate.tests.test_evidence_drafts import storage as storage
from simulate.tests.test_evidence_drafts import versioning_on as versioning_on
from simulate.tests.test_evidence_import_api import _evidence
from simulate.tests.test_harness_versioning_drafts import _lease
from simulate.tests.test_hosted_harness_channels import _payload

pytestmark = pytest.mark.django_db

BASE_URL = "https://platform.example"


@pytest.fixture(autouse=True)
def deletable(storage):
    """The fake object store, with the listing and removal forgetting needs."""
    storage.list_objects = lambda _bucket, prefix="", recursive=False: [
        SimpleNamespace(object_name=key)
        for key in list(storage.objects)
        if key.startswith(prefix)
    ]
    storage.remove_object = lambda _bucket, key: storage.objects.pop(key, None)
    return storage


@pytest.fixture
def scheduled():
    with (
        patch(
            "simulate.tasks.hosted_harness_conversation.schedule_conversation_runtime"
        ) as runtime,
        patch("simulate.tasks.evidence_drafts.schedule_forget") as forget_task,
    ):
        yield SimpleNamespace(runtime=runtime, forget=forget_task)


def _start(cases, user):
    return evidence_drafts.start_drafting(
        cases[0].evidence_import,
        [str(case.id) for case in cases],
        endpoint_base_url=BASE_URL,
        user=user,
    )


def _command(conversation):
    return HostedHarnessConversationMessage.no_workspace_objects.get(
        conversation=conversation, role="user", payload___origin=evidence_drafts.ORIGIN
    )


def _gone(conversation):
    job_id = conversation.job_id
    return {
        "job": HostedHarnessJob._base_manager.filter(id=job_id).exists(),
        "conversation": HostedHarnessConversation._base_manager.filter(
            id=conversation.id
        ).exists(),
        "messages": HostedHarnessConversationMessage._base_manager.filter(
            conversation_id=conversation.id
        ).exists(),
        "events": HostedHarnessConversationEvent._base_manager.filter(
            conversation_id=conversation.id
        ).exists(),
        "transcripts": HostedHarnessConversationTranscript._base_manager.filter(
            conversation_id=conversation.id
        ).exists(),
    }


def _remembered(conversation):
    """What the builder's chat memory holds, as the session store keeps it."""
    HostedHarnessConversationTranscript.no_workspace_objects.create(
        conversation=conversation,
        project_key="builder",
        provider_session_id=f"{conversation.id}-builder",
        entries=[{"role": "assistant", "content": "remember: refund twice"}],
    )


class TestStart:
    def test_drafting_runs_in_a_job_of_the_imports_own(
        self, environment, cases, user, scheduled, django_capture_on_commit_callbacks
    ):
        with django_capture_on_commit_callbacks(execute=True):
            conversation = _start(cases, user)

        child = conversation.job
        assert child.environment_id == environment.id
        assert child.state == HostedHarnessJob.State.COMPLETED
        assert (
            EvidenceImport.objects.get(id=cases[0].evidence_import_id).drafting_job
            == child
        )
        command = _command(conversation)
        assert command.payload["_case_ids"] == [str(case.id) for case in cases]
        assert command.payload["from_platform"] is True
        assert evidence_drafts.EVIDENCE_PATH in command.content
        assert evidence_drafts.scenario_key_for(cases[0]) in command.content
        scheduled.runtime.assert_called_once_with(str(conversation.id), BASE_URL)
        # The environment's own chat holds nothing of it.
        assert not HostedHarnessConversationMessage.no_workspace_objects.filter(
            conversation__job=environment
        ).exists()

    def test_a_second_draft_while_one_runs_is_refused(self, cases, user, scheduled):
        _start(cases[:1], user)

        with pytest.raises(HostedHarnessError) as refused:
            _start(cases[1:], user)

        assert refused.value.code == "evidence_drafting_busy"

    def test_only_waiting_cases_can_be_drafted(self, cases, user, scheduled):
        EvidenceImportCase.objects.filter(id=cases[0].id).update(status="blocked")

        with pytest.raises(HostedHarnessError) as refused:
            _start(cases, user)

        assert refused.value.code == "evidence_case_not_pending"
        assert (
            EvidenceImport.objects.get(id=cases[0].evidence_import_id).drafting_job
            is None
        )


class TestOutOfTheWay:
    def test_the_drafting_job_is_in_no_list_and_holds_up_no_save(
        self, environment, cases, user, scheduled
    ):
        conversation = _start(cases, user)

        assert conversation.job_id not in set(
            user_environment_runs(environment).values_list("id", flat=True)
        )
        assert conversation.id not in {c.id for c in drafts._conversations(environment)}
        drafts.require_chat_idle(environment)

    def test_an_ordinary_run_chat_is_still_listed_and_still_holds_up_save(
        self, environment, user, scheduled
    ):
        """The filter keys on the evidence marker, not on being a child job."""
        run, _ = create_hosted_job(
            environment.organization,
            _payload(),
            idempotency_key="ordinary-run",
            workspace=environment.workspace,
        )
        HostedHarnessJob.no_workspace_objects.filter(id=run.id).update(
            environment=environment, state=HostedHarnessJob.State.COMPLETED
        )
        enqueue_message(run, content="hello", client_request_id="ordinary-1")

        assert run.id in set(
            user_environment_runs(environment).values_list("id", flat=True)
        )
        with pytest.raises(HostedHarnessError) as refused:
            drafts.require_chat_idle(environment)
        assert refused.value.code == "chat_working"

    def test_a_scenarios_tab_edit_never_rewrites_the_drafting_chat(
        self, environment, cases, user, scheduled, storage
    ):
        conversation = _start(cases, user)
        _save(conversation, _command(conversation), storage, [])
        rewritten = []

        def record(body, _suite):
            rewritten.append(body)

        with patch(
            "simulate.services.hosted_harness_gateway._rewritten_authoring_archive",
            side_effect=record,
        ):
            rewrite_conversation_scenarios(environment, [])

        conversation.refresh_from_db()
        assert (
            storage.objects[conversation.latest_workspace_object_key] not in rewritten
        )


class TestTheTurnCannotReachTheEnvironment:
    def test_the_capability_leaves_out_publishing_and_runs(
        self, cases, user, scheduled
    ):
        conversation = _start(cases, user)

        capability = issue_conversation_capability(
            conversation,
            endpoint_base_url=BASE_URL,
            provider_ref="sandbox-1",
            attempt=None,
            ttl_seconds=600,
        )

        endpoints = capability.document["endpoints"]
        assert not {"publish", "run_start"} & set(endpoints)
        # The sandbox client refuses a capability without these; the server
        # refuses the calls instead (test_rerun_and_adjust_are_refused).
        assert {
            "commands",
            "events",
            "workspace",
            "run_status",
            "rerun",
            "adjust",
            "session_store",
            "session_store_append",
        } <= set(endpoints)

    def test_rerun_and_adjust_are_refused_over_the_capability(
        self, cases, user, scheduled
    ):
        # The capability offers both (the sandbox client requires them); the
        # server is what refuses a drafting chat's call.
        conversation = _start(cases, user)
        capability = issue_conversation_capability(
            conversation,
            endpoint_base_url=BASE_URL,
            provider_ref="sandbox-1",
            attempt=None,
            ttl_seconds=600,
        )
        headers = {
            "HTTP_AUTHORIZATION": f"Bearer {capability.token}",
            "HTTP_X_HARNESS_CONVERSATION_FENCE": capability.fence,
        }
        base = f"/simulate/api/harness/conversations/{conversation.id}"

        adjusted = APIClient().post(
            f"{base}/adjust/",
            {
                "instruction": "Add a failed payment scenario",
                "client_request_id": "evidence-adjust-1",
            },
            format="json",
            **headers,
        )
        rerun = APIClient().post(f"{base}/rerun/", {}, format="json", **headers)

        assert (adjusted.status_code, adjusted.data["error"]) == (
            409,
            "evidence_turn_refused",
        )
        assert (rerun.status_code, rerun.data["error"]) == (
            409,
            "evidence_turn_refused",
        )

    def test_rerun_and_adjust_are_refused(self, environment, cases, user, scheduled):
        conversation = _start(cases, user)

        with pytest.raises(HostedHarnessError) as refused:
            evidence_drafts.refuse_for_drafting_job(conversation.job)

        assert refused.value.code == "evidence_turn_refused"
        evidence_drafts.refuse_for_drafting_job(environment)

    def test_only_the_drafting_chat_reads_the_evidence(
        self, environment, cases, user, scheduled
    ):
        conversation = _start(cases, user)
        parent, _command_, _ = enqueue_message(
            environment, content="hello", client_request_id="parent-1"
        )
        found = {case.source_id: _evidence(case.source_id) for case in cases}

        with patch(
            "simulate.selectors.evidence_import.observe_evidence", return_value=found
        ):
            evidence = evidence_drafts.sandbox_evidence(conversation)

        text = evidence.decode()
        assert all(f"== case {case.id}" in text for case in cases)
        assert "jane.doe@example.com" not in text
        assert evidence_drafts.sandbox_evidence(parent) is None

    def test_an_injected_turn_leaves_nothing_in_the_environments_chat(
        self, environment, cases, user, scheduled, storage
    ):
        """The transcript told the builder to add a rule and remember it: the
        environment's own chat, memory, workspace and drafts hold none of it."""
        parent, _parent_command, _ = enqueue_message(
            environment, content="hello", client_request_id="parent-2"
        )
        parent_before = (
            parent.latest_workspace_object_key,
            HostedHarnessConversationTranscript.no_workspace_objects.filter(
                conversation=parent
            ).count(),
        )
        conversation = _start(cases, user)
        command = _command(conversation)
        _remembered(conversation)
        key, digest = _save(
            conversation,
            command,
            storage,
            [evidence_drafts.scenario_key_for(case) for case in cases],
            contract=INJECTED,
        )
        _end_turn(conversation, command)

        _promote(conversation, command, key, digest, 70)
        evidence_drafts.forget(str(cases[0].evidence_import_id))

        parent.refresh_from_db()
        assert (
            parent.latest_workspace_object_key,
            HostedHarnessConversationTranscript.no_workspace_objects.filter(
                conversation=parent
            ).count(),
        ) == parent_before
        assert drafts.open_draft(environment, "environment") is None
        assert drafts.open_draft(environment, "suite") is None
        assert {
            c.status
            for c in EvidenceImportCase.objects.filter(id__in=[c.id for c in cases])
        } == {"blocked"}


class TestSettleAndForget:
    def test_a_finished_turn_lands_then_everything_it_held_is_deleted(
        self,
        environment,
        cases,
        user,
        scheduled,
        storage,
        django_capture_on_commit_callbacks,
    ):
        conversation = _start(cases, user)
        command = _command(conversation)
        _remembered(conversation)
        _lease(conversation)
        key_one = evidence_drafts.scenario_key_for(cases[0])
        key, digest = _save(conversation, command, storage, [key_one])
        _end_turn(conversation, command)

        with django_capture_on_commit_callbacks(execute=True):
            _promote(conversation, command, key, digest, 71)
        scheduled.forget.assert_called_once_with(str(cases[0].evidence_import_id))
        with patch(
            "simulate.services.hosted_harness_gateway.HostedHarnessGateway"
        ) as gateway:
            evidence_drafts.forget(str(cases[0].evidence_import_id))

        gateway.return_value._delete_chat_sandbox.assert_called_once()
        assert not any(_gone(conversation).values())
        prefix = (
            f"harness-conversations/{conversation.organization_id}/{conversation.id}/"
        )
        assert not [k for k in storage.objects if k.startswith(prefix)]
        environment.refresh_from_db()
        checkpoints = (
            environment.payload["metadata"].get("conversation_checkpoints") or {}
        )
        assert str(conversation.id) not in checkpoints
        drafted = EvidenceImportCase.objects.get(id=cases[0].id)
        assert drafted.status == "drafted"
        assert drafted.proposal_object_key in storage.objects
        assert (
            EvidenceImport.objects.get(id=cases[0].evidence_import_id).drafting_job
            is None
        )

    def test_a_failed_turn_blocks_its_cases_and_settles(
        self, cases, user, scheduled, django_capture_on_commit_callbacks
    ):
        conversation = _start(cases, user)
        command = _command(conversation)
        lease = _lease(conversation)

        with django_capture_on_commit_callbacks(execute=True):
            evidence_drafts.turn_ended(str(conversation.id), str(command.id), "failed")

        assert {
            (c.status, c.reason.split(" Builder's reply")[0])
            for c in EvidenceImportCase.objects.filter(id__in=[c.id for c in cases])
        } == {("blocked", evidence_drafts.TURN_FAILED)}
        scheduled.forget.assert_called_once_with(str(cases[0].evidence_import_id))
        lease.refresh_from_db()
        assert lease.state == "expired"

    def test_a_failed_turn_says_what_the_builder_said(
        self, cases, user, scheduled, django_capture_on_commit_callbacks
    ):
        conversation = _start(cases, user)
        command = _command(conversation)
        # As the events ingest stores the sandbox's reply: no reply_to.
        HostedHarnessConversationMessage.no_workspace_objects.create(
            conversation=conversation,
            sequence=command.sequence + 1,
            role="assistant",
            content="I could not draft these cases.",
            state="completed",
        )

        with django_capture_on_commit_callbacks(execute=True):
            evidence_drafts.turn_ended(str(conversation.id), str(command.id), "failed")

        assert {
            c.reason
            for c in EvidenceImportCase.objects.filter(id__in=[c.id for c in cases])
        } == {
            f"{evidence_drafts.TURN_FAILED} Builder's reply: "
            '"I could not draft these cases."'
        }

    def test_a_long_reply_is_cut_at_a_word_and_never_through_a_value(
        self, cases, user, scheduled, django_capture_on_commit_callbacks
    ):
        conversation = _start(cases, user)
        command = _command(conversation)
        words = "I tried every record I could find " * 4
        HostedHarnessConversationMessage.no_workspace_objects.create(
            conversation=conversation,
            sequence=command.sequence + 1,
            role="assistant",
            content=f"{words}then wrote to jane.doe@example.com about it " * 3,
            state="completed",
        )

        with django_capture_on_commit_callbacks(execute=True):
            evidence_drafts.turn_ended(str(conversation.id), str(command.id), "failed")

        (reason,) = {
            c.reason
            for c in EvidenceImportCase.objects.filter(id__in=[c.id for c in cases])
        }
        opening = f"{evidence_drafts.TURN_FAILED} Builder's reply: \""
        assert len(reason) <= evidence_drafts.REASON_LENGTH
        assert reason.startswith(opening) and reason.endswith('…"')
        quoted = reason.removeprefix(opening).removesuffix('…"')
        # Whole words only, and the address is a whole token, never a fragment.
        allowed = {*f"{words} then wrote to about it".split(), "<EMAIL_ADDRESS>"}
        assert set(quoted.split()) <= allowed
        assert "jane" not in reason and "example" not in reason

    def test_a_turn_that_never_ends_is_settled_and_deleted_by_the_sweep(
        self, cases, user, scheduled
    ):
        conversation = _start(cases, user)
        HostedHarnessJob._base_manager.filter(id=conversation.job_id).update(
            created_at=timezone.now()
            - evidence_drafts.DRAFTING_TIMEOUT
            - timedelta(minutes=1)
        )

        swept = evidence_drafts.sweep()

        assert swept == {"timed_out": 1, "forgotten": 1}
        assert {
            c.reason
            for c in EvidenceImportCase.objects.filter(id__in=[c.id for c in cases])
        } == {evidence_drafts.TIMED_OUT}
        assert not any(_gone(conversation).values())

    def test_a_young_turn_is_left_to_finish(self, cases, user, scheduled):
        conversation = _start(cases, user)

        assert evidence_drafts.sweep() == {"timed_out": 0, "forgotten": 0}
        assert _gone(conversation)["job"]

    def test_a_deletion_that_stopped_part_way_is_finished_by_the_sweep(
        self, cases, user, scheduled, storage
    ):
        conversation = _start(cases, user)
        command = _command(conversation)
        _save(conversation, command, storage, [])
        EvidenceImportCase.objects.filter(id__in=[c.id for c in cases]).update(
            status="blocked", reason="x"
        )
        storage.remove_object = lambda _bucket, _key: (_ for _ in ()).throw(
            OSError("store down")
        )

        with pytest.raises(OSError):
            evidence_drafts.forget(str(cases[0].evidence_import_id))
        assert _gone(conversation)["job"]

        storage.remove_object = lambda _bucket, key: storage.objects.pop(key, None)
        assert evidence_drafts.sweep() == {"timed_out": 0, "forgotten": 1}
        assert not any(_gone(conversation).values())


def test_the_turn_says_what_each_case_records(cases, user, scheduled):
    conversation = evidence_drafts.start_drafting(
        cases[0].evidence_import,
        [str(case.id) for case in cases],
        endpoint_base_url=BASE_URL,
        user=user,
        shows={str(cases[0].id): "failure", str(cases[1].id): "success"},
    )

    content = _command(conversation).content
    assert evidence_drafts.SHOWN["failure"] in content
    assert evidence_drafts.SHOWN["success"] in content
    assert "contradicts what the person says it records" in content
    # Production sources are regression evidence, and never called simulated.
    assert "Draft one regression scenario" in content
    assert "from the production evidence" in content
    assert "Simulator project" not in content


def test_a_simulator_projects_calls_are_never_called_production(cases, user, scheduled):
    from simulate.selectors.evidence_import import evidence_document, import_bundle

    evidence_import = cases[0].evidence_import
    case_ids = [str(case.id) for case in cases]
    found = {case.source_id: _evidence(case.source_id) for case in cases}
    with patch(
        "simulate.selectors.evidence_import.observe_evidence", return_value=found
    ):
        # A production project's session.
        production = import_bundle(evidence_import)
        production_text = evidence_document(evidence_import, case_ids)
        # A Simulator project's call.
        project = evidence_import.project
        project.source = "simulator"
        project.save(update_fields=["source"])
        evidence_import.source_kind = "call"
        evidence_import.save(update_fields=["source_kind"])
        simulated = import_bundle(evidence_import)
        simulated_text = evidence_document(evidence_import, case_ids)

    assert production["simulated"] is False
    assert "These are production sessions: each case is regression evidence." in (
        production_text
    )
    assert "Simulator project" not in production_text
    assert simulated["simulated"] is True
    assert (
        "These calls come from a Simulator project: simulated, not production."
        in simulated_text
    )
    assert "production calls" not in simulated_text

    # The builder is told the same, and asked for tests, never regression tests.
    cases[0].evidence_import.refresh_from_db()
    conversation = evidence_drafts.start_drafting(
        cases[0].evidence_import,
        case_ids,
        endpoint_base_url=BASE_URL,
        user=user,
        shows={str(cases[0].id): "failure", str(cases[1].id): "success"},
    )
    content = _command(conversation).content
    assert "from the simulated calls" in content
    assert "not production" in content
    assert "production evidence" not in content
    assert evidence_drafts.SHOWN_SIMULATED["failure"] in content
    assert evidence_drafts.SHOWN_SIMULATED["success"] in content
    assert "regression" not in content


def test_the_bundle_says_while_drafting_runs(cases, user, scheduled):
    from simulate.selectors.evidence_import import import_bundle

    evidence_import = cases[0].evidence_import
    found = {case.source_id: _evidence(case.source_id) for case in cases}
    with patch(
        "simulate.selectors.evidence_import.observe_evidence", return_value=found
    ):
        assert import_bundle(evidence_import)["drafting"] is False
        _start(cases, user)
        evidence_import.refresh_from_db()
        assert import_bundle(evidence_import)["drafting"] is True


def test_the_draft_answer_says_drafting_has_started(
    environment, cases, user, scheduled, versioning_on, settings
):
    # The review keeps this answer and asks again only while it says drafting, so
    # an answer read before the drafting job was linked would leave it waiting.
    settings.HARNESS_PUBLIC_BASE_URL = BASE_URL
    evidence_import = cases[0].evidence_import
    api = APIClient()
    api.force_authenticate(user=user)
    api.credentials(HTTP_X_WORKSPACE_ID=str(environment.workspace_id))
    found = {case.source_id: _evidence(case.source_id) for case in cases}

    with patch(
        "simulate.selectors.evidence_import.observe_evidence", return_value=found
    ):
        response = api.post(
            f"/simulate/api/harness-environments/{environment.id}"
            f"/evidence-imports/{evidence_import.id}/draft/",
            {"case_ids": [str(case.id) for case in cases]},
            format="json",
        )

    assert response.status_code == 202, response.content
    assert response.json()["drafting"] is True
