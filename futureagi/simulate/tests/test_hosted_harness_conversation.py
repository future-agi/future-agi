from __future__ import annotations

import hashlib
import io
import json
import tarfile
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from rest_framework.test import APIClient

from simulate.models import HostedHarnessConversationMessage, HostedHarnessJob
from simulate.services.hosted_harness import (
    HostedHarnessError,
    canonical_digest,
    create_hosted_job,
    create_selected_harness_run,
)
from simulate.services.hosted_harness_conversation import (
    enqueue_message,
    issue_conversation_capability,
    serialize_conversation,
)

BASE = "/simulate/api/harness/conversations"


def _payload():
    return {
        "schema_version": "futureagi.harness-job.v1",
        "source": {
            "kind": "remote",
            "endpoint": "https://agent.example.com",
            "visibility": "public",
        },
        "agent": {"connector": "vapi", "config": {}, "secret_refs": {}},
        "scenario_count": 1,
        "seed": 7,
        "runtime": {
            "isolation": "dedicated_vm",
            "cpu_units": 2,
            "memory_mb": 4096,
            "parallelism": 1,
            "concurrency_weight": 1,
            "max_duration_seconds": 600,
            "network_policy": "live",
        },
        "security": {
            "untrusted_source": True,
            "read_only_source": True,
            "allow_privileged": False,
            "allow_host_runtime_control": False,
            "allowed_egress_domains": ["agent.example.com"],
        },
        "retry": {
            "max_infrastructure_attempts": 2,
            "initial_backoff_seconds": 1,
            "max_backoff_seconds": 15,
            "retryable_domains": ["infrastructure", "connectivity"],
        },
        "artifacts": {
            "level": "full",
            "retention_days": 30,
            "allow_bundle_download": False,
            "max_artifact_bytes": 1024,
        },
        "metadata": {},
    }


def _headers(capability):
    return {
        "HTTP_AUTHORIZATION": f"Bearer {capability.token}",
        "HTTP_X_HARNESS_CONVERSATION_FENCE": capability.fence,
    }


@pytest.mark.django_db
def test_message_submission_is_durable_ordered_and_idempotent(organization):
    job, _ = create_hosted_job(
        organization,
        _payload(),
        idempotency_key="conversation-message-order",
    )
    conversation = job.conversation

    conversation, first, created = enqueue_message(
        job,
        content="Add five payment-failure scenarios",
        client_request_id="message-1",
    )
    assert created
    assert first.sequence == 1
    assert first.state == HostedHarnessConversationMessage.State.QUEUED

    _conversation, replay, created = enqueue_message(
        job,
        content="Add five payment-failure scenarios",
        client_request_id="message-1",
    )
    assert not created
    assert replay.id == first.id

    _conversation, second, created = enqueue_message(
        job,
        content="Focus them on card declines",
        client_request_id="message-2",
    )
    assert created
    assert second.sequence == 2
    assert [
        item["content"] for item in serialize_conversation(conversation)["messages"]
    ] == [
        "Add five payment-failure scenarios",
        "Focus them on card declines",
    ]


@pytest.mark.django_db
def test_running_job_accepts_chat_before_any_runtime_or_workspace_exists(
    organization,
):
    job, _ = create_hosted_job(
        organization,
        _payload(),
        idempotency_key="active-control-conversation",
    )

    serialized = serialize_conversation(job.conversation)

    # Messages queue until the job's sandbox can run chat; the UI must not freeze meanwhile.
    assert serialized["runtime"]["available"] is True
    assert serialized["runtime"]["state"] == "cold"

    job.state = HostedHarnessJob.State.COMPLETED
    job.save(update_fields=["state", "updated_at"])
    job.conversation.refresh_from_db()
    assert serialize_conversation(job.conversation)["runtime"]["available"] is False


@pytest.mark.django_db
def test_guest_command_and_event_channel_projects_streamed_reply(organization):
    job, _ = create_hosted_job(
        organization,
        _payload(),
        idempotency_key="conversation-stream",
    )
    conversation, command, _ = enqueue_message(
        job,
        content="Add five scenarios",
        client_request_id="stream-message",
    )
    capability = issue_conversation_capability(
        conversation,
        endpoint_base_url="https://platform.example",
        provider_ref="sandbox-1",
        attempt=None,
        ttl_seconds=600,
    )
    client = APIClient()
    headers = _headers(capability)

    pending = client.get(
        f"{BASE}/{conversation.id}/commands/?after=0",
        **headers,
    )
    assert pending.status_code == 200, pending.content
    assert pending.json()["commands"][0]["message_id"] == str(command.id)

    assistant_message_id = str(uuid.uuid4())
    emitted_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    event = {
        "schema_version": "futureagi.harness-conversation-event.v1",
        "event_id": "ce-stream-1",
        "conversation_id": str(conversation.id),
        "sequence": 1,
        "kind": "assistant_delta",
        "message_id": assistant_message_id,
        "stage": "scenarios",
        "invocation_id": "turn-1",
        "function_call_id": None,
        "emitted_at": emitted_at,
        "payload": {"text": "Sure — I'll add five scenarios.", "partial": True},
    }
    event["digest"] = canonical_digest(event)
    response = client.post(
        f"{BASE}/{conversation.id}/events/",
        {
            "schema_version": "futureagi.harness-conversation-event.v1",
            "acknowledged_through": 1,
            "events": [event],
        },
        format="json",
        **headers,
    )
    assert response.status_code == 200, response.content
    assert response.json() == {"acked_through_sequence": 1}

    conversation.refresh_from_db()
    projected = conversation.messages.get(id=assistant_message_id)
    assert projected.content == "Sure — I'll add five scenarios."
    assert projected.state == HostedHarnessConversationMessage.State.STREAMING
    command.refresh_from_db()
    assert command.state == HostedHarnessConversationMessage.State.DELIVERED

    activity = {
        "schema_version": "futureagi.harness-conversation-event.v1",
        "event_id": "ce-activity-1",
        "conversation_id": str(conversation.id),
        "sequence": 2,
        "kind": "authoring_activity",
        "message_id": None,
        "stage": "authoring",
        "invocation_id": None,
        "function_call_id": None,
        "emitted_at": emitted_at,
        "payload": {"event_type": "stage_changed", "event": {"stage": "authoring"}},
    }
    activity["digest"] = canonical_digest(activity)
    activity_response = client.post(
        f"{BASE}/{conversation.id}/events/",
        {
            "schema_version": "futureagi.harness-conversation-event.v1",
            "acknowledged_through": 1,
            "events": [activity],
        },
        format="json",
        **headers,
    )
    assert activity_response.status_code == 200, activity_response.content
    assert activity_response.json() == {"acked_through_sequence": 2}
    assert conversation.events.filter(event_id="ce-activity-1").exists()

    duplicate = client.post(
        f"{BASE}/{conversation.id}/events/",
        {
            "schema_version": "futureagi.harness-conversation-event.v1",
            "acknowledged_through": 1,
            "events": [event],
        },
        format="json",
        **headers,
    )
    assert duplicate.status_code == 200, duplicate.content
    projected.refresh_from_db()
    assert projected.content == "Sure — I'll add five scenarios."


@pytest.mark.django_db
def test_conversation_agent_can_request_active_run_adjustment(
    organization, monkeypatch
):
    job, _ = create_hosted_job(
        organization,
        _payload(),
        idempotency_key="conversation-agent-adjustment",
    )
    capability = issue_conversation_capability(
        job.conversation,
        endpoint_base_url="https://platform.example",
        provider_ref="sandbox-adjustment",
        attempt=None,
        ttl_seconds=600,
    )

    def adjust(_self, active_job, request):
        payload = dict(active_job.payload)
        metadata = dict(payload.get("metadata") or {})
        metadata["adjustments"] = [
            {
                "adjustment_id": str(uuid.uuid4()),
                "client_request_id": request["client_request_id"],
                "instruction": request["instruction"],
                "status": "pending",
            }
        ]
        payload["metadata"] = metadata
        active_job.payload = payload
        active_job.save(update_fields=["payload", "updated_at"])
        return active_job

    from simulate.services.hosted_harness_gateway import HostedHarnessGateway

    monkeypatch.setattr(HostedHarnessGateway, "adjust", adjust)
    monkeypatch.setattr(
        "simulate.services.hosted_harness_gateway.get_sandbox_provider",
        lambda: SimpleNamespace(name="test-provider"),
    )
    response = APIClient().post(
        f"{BASE}/{job.conversation.id}/adjust/",
        {
            "instruction": "Add a failed payment scenario",
            "client_request_id": "agent-adjustment-1",
        },
        format="json",
        **_headers(capability),
    )

    assert response.status_code == 200, response.content
    assert response.json()["instruction"] == "Add a failed payment scenario"
    assert response.json()["status"] == "pending"


@pytest.mark.django_db
def test_claude_session_store_round_trips_and_deduplicates_entries(organization):
    job, _ = create_hosted_job(
        organization,
        _payload(),
        idempotency_key="conversation-session-store",
    )
    capability = issue_conversation_capability(
        job.conversation,
        endpoint_base_url="https://platform.example",
        provider_ref="sandbox-session-store",
        attempt=None,
        ttl_seconds=600,
    )
    client = APIClient()
    headers = _headers(capability)
    endpoint = f"{BASE}/{job.conversation.id}/session-store"
    key = {
        "project_key": "futureagi-conversation",
        "session_id": "claude-session-1",
        "subpath": "",
    }
    first = {"type": "user", "uuid": "entry-1", "message": "hello"}
    second = {"type": "assistant", "uuid": "entry-2", "message": "hi"}

    appended = client.post(
        f"{endpoint}/append/",
        {**key, "entries": [first, second]},
        format="json",
        **headers,
    )
    assert appended.status_code == 200, appended.content
    assert appended.json() == {"appended": 2}

    replayed = client.post(
        f"{endpoint}/append/",
        {**key, "entries": [first]},
        format="json",
        **headers,
    )
    assert replayed.status_code == 200, replayed.content
    assert replayed.json() == {"appended": 0}

    child = client.post(
        f"{endpoint}/append/",
        {
            **key,
            "subpath": "subagents/reviewer",
            "entries": [{"type": "user", "uuid": "entry-3"}],
        },
        format="json",
        **headers,
    )
    assert child.status_code == 200, child.content

    loaded = client.get(endpoint + "/", key, **headers)
    assert loaded.status_code == 200, loaded.content
    assert loaded.json() == {
        "entries": [first, second],
        "subkeys": ["subagents/reviewer"],
    }


@pytest.mark.django_db
def test_conversation_runtime_lease_starts_once_and_reuses_warm_sandbox(
    organization, monkeypatch
):
    job, _ = create_hosted_job(
        organization,
        _payload(),
        idempotency_key="conversation-runtime-lease",
    )
    job.state = HostedHarnessJob.State.FAILED
    job.current_stage = "failed"
    job.save(update_fields=["state", "current_stage", "updated_at"])
    conversation = job.conversation
    uploads = {}
    commands = []
    preparation_commands = []
    process_checks = []

    class Files:
        def upload_file(self, body, path):
            uploads[path] = body

        def download_file(self, path, timeout):
            process_checks.append(("download", path, timeout))
            return b"chat-command"

    class Process:
        def exec(self, command, timeout):
            del timeout
            preparation_commands.append(command)
            return SimpleNamespace(exit_code=0, result="")

        def create_session(self, name):
            assert name == "alk-chat"

        def execute_session_command(self, name, request):
            assert name == "alk-chat"
            commands.append(request.command)
            return SimpleNamespace(cmd_id="chat-command")

        def get_session_command(self, name, command_id, request_timeout):
            process_checks.append(("command", name, command_id, request_timeout))
            return SimpleNamespace(exit_code=None)

    sandbox = SimpleNamespace(id="sandbox-chat-1", fs=Files(), process=Process())

    class Client:
        name = "e2b"
        runtime_name = "alk-hosted-production:build-1"
        runtime_digest = "build-1"
        create_timeout_seconds = 300
        max_egress_domains = None

        def __init__(self):
            self.created = 0
            self.renewed = []
            self.deleted = []

        def create(self, _params, timeout):
            del timeout
            self.created += 1
            return sandbox

        def get(self, provider_ref, request_timeout):
            del request_timeout
            assert provider_ref == sandbox.id
            return sandbox

        def renew_ttl(self, target, ttl_seconds):
            self.renewed.append((target.id, ttl_seconds))

        def delete(self, target, *_args, **_kwargs):
            self.deleted.append(target.id)
            return True

    from simulate.services import hosted_harness_gateway as gateway_module

    monkeypatch.setattr(
        gateway_module,
        "load_workspace_archive",
        lambda _conversation: None,
    )
    monkeypatch.setattr(
        gateway_module,
        "_authoring_archive_for",
        lambda _job: None,
    )
    monkeypatch.setattr(
        gateway_module.HostedSourceAcquirer,
        "acquire",
        lambda _self, _job: (b"source", None),
    )
    monkeypatch.setattr(
        gateway_module,
        "_platform_simulator_material",
        lambda: (
            {
                "ALK_HARNESS": "vertex-gemini",
                "ALK_HARNESS_MODEL": "gemini-3.7-flash",
            },
            None,
        ),
    )
    monkeypatch.setattr(
        gateway_module,
        "_resolved_egress_domains",
        lambda *_args: {f"host-{index}.example" for index in range(25)},
    )

    gateway = object.__new__(gateway_module.HostedHarnessGateway)
    gateway.client = Client()

    lease = gateway.ensure_conversation_runtime(
        job,
        conversation=conversation,
        endpoint_base_url="https://platform.example",
    )
    assert lease.provider_ref == sandbox.id
    # Starting until the process polls once, so a slow cold boot is not restarted as dead.
    assert lease.state == "starting"
    type(lease).no_workspace_objects.filter(id=lease.id).update(state="active")
    assert lease.control_only is True
    assert gateway.client.created == 1
    assert "fi.alk.harness.hosted_chat_entrypoint" in commands[0]
    assert "tar -xzf /work/source.tar.gz -C /work" in preparation_commands[0]
    assert (
        "tar -xzf /work/conversation.tar.gz -C /work/authoring"
        in preparation_commands[0]
    )
    assert "/run/futureagi/conversation.json" in uploads
    capability_document = json.loads(uploads["/run/futureagi/conversation.json"])
    assert capability_document["turn_context"]["capabilities"]["control_only"] is True
    assert (
        b"request_user_input: true" in uploads["/run/futureagi/chat-capabilities.yaml"]
    )
    with tarfile.open(
        fileobj=io.BytesIO(uploads["/work/conversation.tar.gz"]),
        mode="r:gz",
    ) as archive:
        assert archive.getnames() == []

    first_deadline = lease.expires_at
    reused = gateway.ensure_conversation_runtime(
        job,
        conversation=conversation,
        endpoint_base_url="https://platform.example",
    )
    assert reused.id == lease.id
    assert gateway.client.created == 1
    assert gateway.client.deleted == []
    # Liveness is the process's own polling, not a provider probe; a message renews idle time.
    assert process_checks == []
    assert gateway.client.renewed == [(sandbox.id, 1800)]
    reused.refresh_from_db()
    assert reused.expires_at >= first_deadline

    type(reused).no_workspace_objects.filter(id=reused.id).update(
        heartbeat_at=reused.heartbeat_at - timedelta(minutes=5)
    )
    restarted = gateway.ensure_conversation_runtime(
        job,
        conversation=conversation,
        endpoint_base_url="https://platform.example",
    )
    assert restarted.state == "starting"
    assert gateway.client.created == 2
    # The dead process's own sandbox is reclaimed rather than left running to its deadline.
    assert gateway.client.deleted == [sandbox.id]


def _chat_checkpoint(names, *, one_liner):
    """A chat workspace as the runtime packs it: the suite plus the chat's own private state."""
    from simulate.tests.test_harness_amend_archive import _key

    documents = [
        {
            "name": name,
            "scenario_key": _key(name),
            "instruction": f"call about {name}",
            "tests": "t",
            "persona": {"name": name.title(), "role": "customer"},
        }
        for name in names
    ]
    entries = {
        "contract.json": json.dumps({"one_liner": one_liner}).encode(),
        "scenarios.json": json.dumps(documents).encode(),
        "cost.json": b"{}",
        ".futureagi-conversation.json": b"{}",
        ".claude/sessions/1.key": b"private",
    }
    for document in documents:
        folder = f"scenarios/{document['name']}"
        entries[f"{folder}/scenario.json"] = json.dumps(document).encode()
        entries[f"{folder}/ready.py"] = b"def ready(world):\n    return True\n"
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as archive:
        for path, data in entries.items():
            member = tarfile.TarInfo(path)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return out.getvalue()


def _commit_checkpoint(client, conversation, headers, body):
    digest = "sha256:" + hashlib.sha256(body).hexdigest()
    response = client.generic(
        "PUT",
        f"{BASE}/{conversation.id}/workspace/",
        body,
        content_type="application/gzip",
        HTTP_X_WORKSPACE_DIGEST=digest,
        HTTP_X_WORKSPACE_SIZE=str(len(body)),
        **headers,
    )
    assert response.status_code == 200, response.content
    return {"digest": digest, "size": len(body)}


def _send_events(client, conversation, headers, *events):
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
    response = client.post(
        f"{BASE}/{conversation.id}/events/",
        {
            "schema_version": "futureagi.harness-conversation-event.v1",
            "acknowledged_through": 1,
            "events": signed,
        },
        format="json",
        **headers,
    )
    assert response.status_code == 200, response.content


class _Stored(io.BytesIO):
    def release_conn(self):
        pass


class _Storage:
    """Object storage kept in memory; the suite has no reachable bucket."""

    def __init__(self):
        self.objects = {}

    def bucket_exists(self, _bucket):
        return False

    def make_bucket(self, _bucket):
        pass

    def set_bucket_policy(self, _bucket, _policy):
        pass

    def put_object(self, bucket_name, object_name, data, length, content_type=None):
        self.objects[object_name] = data.read(length)

    def get_object(self, _bucket, object_name):
        return _Stored(self.objects[object_name])


@pytest.fixture
def storage(monkeypatch):
    from tfc.utils import storage_client

    fake = _Storage()
    monkeypatch.setattr(storage_client, "_client", fake)
    return fake


def _archive_paths(body):
    with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as archive:
        return {
            member.name.removeprefix("./")
            for member in archive.getmembers()
            if member.isfile()
        }


def _prime_environment_archive(environment, storage, body):
    key = environment.payload["metadata"]["authoring_object_key"]
    storage.objects[key] = body


def _put_conversation_checkpoint(conversation, storage, body):
    from simulate.services.hosted_harness_conversation import _workspace_object_key

    digest = "sha256:" + hashlib.sha256(body).hexdigest()
    key = _workspace_object_key(conversation, digest)
    storage.objects[key] = body
    return key, digest


@pytest.mark.django_db
def test_completed_chat_turn_publishes_its_checkpoint_to_the_environment(
    user, workspace, storage, monkeypatch, django_capture_on_commit_callbacks
):
    from simulate.models import HostedHarnessScenario
    from simulate.tasks import hosted_harness_conversation as tasks
    from simulate.tests.test_harness_amend_archive import (
        NAMES,
        _key,
        _run_environment,
    )

    promote = tasks.promote_hosted_harness_conversation_checkpoint
    monkeypatch.setattr(
        promote, "apply_async", lambda args, **_: promote._original_func(*args)
    )
    environment = _run_environment(user, workspace, "chat-publish")
    _prime_environment_archive(
        environment, storage, _chat_checkpoint(NAMES[:2], one_liner="original")
    )
    conversation, command, _ = enqueue_message(
        environment, content="Add a PIN scenario", client_request_id="add-pin"
    )
    capability = issue_conversation_capability(
        conversation,
        endpoint_base_url="https://platform.example",
        provider_ref="sandbox-chat",
        attempt=None,
        ttl_seconds=600,
    )
    client, headers = APIClient(), _headers(capability)

    # A checkpoint taken mid-turn is the chat's scratch state, not a finished suite.
    halfway = _commit_checkpoint(
        client, conversation, headers, _chat_checkpoint(NAMES, one_liner="halfway")
    )
    with django_capture_on_commit_callbacks(execute=True):
        _send_events(client, conversation, headers, (1, "checkpoint_committed", halfway))
    environment.refresh_from_db()
    assert environment.scenario_count == 2

    final = _commit_checkpoint(
        client, conversation, headers, _chat_checkpoint(NAMES, one_liner="finished")
    )
    with django_capture_on_commit_callbacks(execute=True):
        _send_events(
            client,
            conversation,
            headers,
            (2, "checkpoint_committed", final),
            (
                3,
                "turn_completed",
                {"command_message_id": str(command.id), "outcome": "success"},
            ),
        )

    environment.refresh_from_db()
    assert environment.scenario_count == 3
    rows = {
        row.scenario_key: row
        for row in HostedHarnessScenario.no_workspace_objects.filter(job=environment)
    }
    assert set(rows) == {_key(name) for name in NAMES}
    added = rows[_key(NAMES[2])]
    assert added.scenario_id is not None and added.dataset_row_id is not None
    outputs = {one["kind"]: one["data"] for one in environment.stage_outputs}
    assert [one["name"] for one in outputs["scenarios"]] == list(NAMES)
    assert outputs["contract"]["one_liner"] == "finished"
    promoted = _archive_paths(
        storage.objects[environment.payload["metadata"]["authoring_object_key"]]
    )
    assert f"scenarios/{NAMES[2]}/ready.py" in promoted
    assert not [path for path in promoted if path.startswith(".") or path == "cost.json"]

    run, created = create_selected_harness_run(
        environment,
        scenario_keys=[_key(NAMES[2])],
        trials=2,
        idempotency_key="after-chat",
    )
    assert created
    assert [
        (entry["scenario_key"], entry["trial_index"])
        for entry in run.payload["metadata"]["execution_manifest"]
    ] == [(_key(NAMES[2]), 1), (_key(NAMES[2]), 2)]

    # A redelivered promotion neither registers the new scenario twice nor rebinds it.
    promote._original_func(str(conversation.id), 3)
    assert HostedHarnessScenario.all_objects.filter(job=environment).count() == 3
    added_again = HostedHarnessScenario.no_workspace_objects.get(id=added.id)
    assert added_again.dataset_row_id == added.dataset_row_id


@pytest.mark.django_db
def test_chat_run_tool_publishes_the_checkpoint_before_the_run_starts(
    user, workspace, storage, monkeypatch
):
    from simulate.models import HostedHarnessScenario
    from simulate.services.harness_provider import HostedHarnessProvider
    from simulate.tests.test_harness_amend_archive import (
        NAMES,
        _key,
        _run_environment,
    )

    environment = _run_environment(user, workspace, "chat-run-tool")
    _prime_environment_archive(
        environment, storage, _chat_checkpoint(NAMES[:2], one_liner="original")
    )
    conversation, _command, _ = enqueue_message(
        environment, content="Run it", client_request_id="run-tool"
    )
    capability = issue_conversation_capability(
        conversation,
        endpoint_base_url="https://platform.example",
        provider_ref="sandbox-run-tool",
        attempt=None,
        ttl_seconds=600,
    )
    client, headers = APIClient(), _headers(capability)
    _commit_checkpoint(
        client, conversation, headers, _chat_checkpoint(NAMES, one_liner="run me")
    )
    captured = {}

    def rerun_saved(_self, job_id, *, organization, workspace, environment_values):
        del organization, workspace, environment_values
        rerun_job = HostedHarnessJob.no_workspace_objects.get(id=job_id)
        captured["scenario_count"] = rerun_job.scenario_count
        captured["runnable"] = sorted(
            HostedHarnessScenario.no_workspace_objects.filter(
                job=rerun_job, scenario__isnull=False
            ).values_list("scenario_key", flat=True)
        )
        return {}

    monkeypatch.setattr(HostedHarnessProvider, "rerun_saved", rerun_saved)
    response = client.post(
        f"{BASE}/{conversation.id}/rerun/", {}, format="json", **headers
    )

    assert response.status_code == 202, response.content
    assert captured == {
        "scenario_count": 3,
        "runnable": sorted(_key(name) for name in NAMES),
    }


@pytest.mark.django_db
def test_ui_scenario_edit_refreshes_the_saved_chat_workspace(user, workspace, storage):
    from simulate.services.hosted_harness_gateway import (
        rewrite_authoring_scenarios,
        rewrite_conversation_scenarios,
    )
    from simulate.tests.test_harness_amend_archive import (
        NAMES,
        _key,
        _run_environment,
    )

    environment = _run_environment(user, workspace, "chat-ui-reconcile")
    original = _chat_checkpoint(NAMES[:2], one_liner="original")
    _prime_environment_archive(environment, storage, original)
    conversation = environment.conversation
    old_key, old_digest = _put_conversation_checkpoint(conversation, storage, original)
    conversation.latest_workspace_object_key = old_key
    conversation.latest_workspace_digest = old_digest
    conversation.latest_scenario_count = 2
    conversation.save(
        update_fields=[
            "latest_workspace_object_key",
            "latest_workspace_digest",
            "latest_scenario_count",
            "updated_at",
        ]
    )
    suite = [
        {
            "name": name,
            "scenario_key": _key(name),
            "instruction": f"call about {name}",
            "tests": "edited in the UI" if name == NAMES[0] else "t",
            "persona": {"name": name.title(), "role": "customer"},
        }
        for name in NAMES[:2]
    ]

    rewrite_authoring_scenarios(environment, suite)
    assert rewrite_conversation_scenarios(environment, suite) == 1

    conversation.refresh_from_db()
    assert conversation.latest_workspace_object_key != old_key
    body = storage.objects[conversation.latest_workspace_object_key]
    with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as archive:
        scenarios = json.loads(archive.extractfile("scenarios.json").read())
    assert scenarios[0]["tests"] == "edited in the UI"


@pytest.mark.django_db
def test_intervening_environment_edit_fences_a_stale_chat_checkpoint(
    user, workspace, storage
):
    from simulate.services.hosted_harness_conversation import (
        promote_conversation_checkpoint,
    )
    from simulate.services.hosted_harness_gateway import store_authoring_archive
    from simulate.tests.test_harness_amend_archive import NAMES, _run_environment

    environment = _run_environment(user, workspace, "chat-stale-fence")
    original = _chat_checkpoint(NAMES[:2], one_liner="original")
    _prime_environment_archive(environment, storage, original)
    conversation, command, _ = enqueue_message(
        environment, content="Explain the suite", client_request_id="read-only"
    )
    expected_revision = command.payload["_environment_revision"]
    key, digest = _put_conversation_checkpoint(conversation, storage, original)

    # This represents a UI edit committed after the chat command was queued.
    edited = _chat_checkpoint(NAMES[:2], one_liner="edited in the UI")
    edited_key = store_authoring_archive(environment, edited, advance_lifecycle=False)

    with pytest.raises(HostedHarnessError) as error:
        promote_conversation_checkpoint(
            conversation,
            object_key=key,
            digest=digest,
            sequence=5,
            expected_revision=expected_revision,
        )

    assert error.value.code == "conversation_checkpoint_stale"
    environment.refresh_from_db()
    assert environment.payload["metadata"]["authoring_object_key"] == edited_key


@pytest.mark.django_db
def test_stale_workspace_basis_is_rejected_even_for_a_later_message(
    user, workspace, storage
):
    from simulate.services.hosted_harness_conversation import (
        promote_conversation_checkpoint,
    )
    from simulate.services.hosted_harness_gateway import (
        authoring_basis,
        store_authoring_archive,
        with_authoring_basis,
    )
    from simulate.tests.test_harness_amend_archive import NAMES, _run_environment

    environment = _run_environment(user, workspace, "chat-stale-basis")
    original = _chat_checkpoint(NAMES[:2], one_liner="original")
    original_key = store_authoring_archive(
        environment, original, advance_lifecycle=False
    )
    original_revision = environment.payload["metadata"]["authoring_revision"]
    conversation = environment.conversation
    stale_workspace = with_authoring_basis(original, original_revision)
    key, digest = _put_conversation_checkpoint(conversation, storage, stale_workspace)
    conversation.latest_workspace_object_key = key
    conversation.latest_workspace_digest = digest
    conversation.save(
        update_fields=[
            "latest_workspace_object_key",
            "latest_workspace_digest",
            "updated_at",
        ]
    )

    edited = _chat_checkpoint(NAMES[:2], one_liner="edited in the UI")
    edited_key = store_authoring_archive(environment, edited, advance_lifecycle=False)
    assert edited_key != original_key
    # Simulates a failed workspace reconciliation: the message starts after the edit,
    # but the chat runtime still owns the old workspace.
    _conversation, command, _ = enqueue_message(
        environment, content="Explain the suite", client_request_id="after-edit"
    )
    assert command.payload["_environment_revision"] != original_revision

    with pytest.raises(HostedHarnessError) as error:
        promote_conversation_checkpoint(
            conversation,
            object_key=key,
            digest=digest,
            sequence=9,
            expected_revision=command.payload["_environment_revision"],
        )

    assert error.value.code == "conversation_checkpoint_stale"
    environment.refresh_from_db()
    conversation.refresh_from_db()
    assert environment.payload["metadata"]["authoring_object_key"] == edited_key
    assert conversation.latest_workspace_object_key != key
    rebased = storage.objects[conversation.latest_workspace_object_key]
    assert (
        authoring_basis(rebased) == environment.payload["metadata"]["authoring_revision"]
    )



@pytest.mark.django_db
def test_each_conversation_has_its_own_checkpoint_sequence_clock(
    user, workspace, storage
):
    from simulate.models import HostedHarnessScenario
    from simulate.services.hosted_harness_conversation import (
        promote_conversation_checkpoint,
    )
    from simulate.services.hosted_harness_gateway import with_authoring_basis
    from simulate.tests.test_harness_amend_archive import (
        NAMES,
        _key,
        _run_environment,
    )

    environment = _run_environment(user, workspace, "chat-independent-clocks")
    original = _chat_checkpoint(NAMES[:2], one_liner="original")
    _prime_environment_archive(environment, storage, original)
    first_conversation, first_command, _ = enqueue_message(
        environment, content="Add one", client_request_id="first-conversation"
    )
    first = with_authoring_basis(
        _chat_checkpoint(NAMES, one_liner="first"),
        first_command.payload["_environment_revision"],
    )
    first_key, first_digest = _put_conversation_checkpoint(
        first_conversation, storage, first
    )
    promote_conversation_checkpoint(
        first_conversation,
        object_key=first_key,
        digest=first_digest,
        sequence=100,
        expected_revision=first_command.payload["_environment_revision"],
    )

    child, _ = create_selected_harness_run(
        environment,
        scenario_keys=[_key(NAMES[0])],
        trials=1,
        idempotency_key="child-conversation",
    )
    child_conversation, child_command, _ = enqueue_message(
        child, content="Add another", client_request_id="second-conversation"
    )
    late_name = "late_child_scenario"
    second = with_authoring_basis(
        _chat_checkpoint((*NAMES, late_name), one_liner="second"),
        child_command.payload["_environment_revision"],
    )
    second_key, second_digest = _put_conversation_checkpoint(
        child_conversation, storage, second
    )
    promote_conversation_checkpoint(
        child_conversation,
        object_key=second_key,
        digest=second_digest,
        sequence=5,
        expected_revision=child_command.payload["_environment_revision"],
    )

    environment.refresh_from_db()
    assert environment.scenario_count == 4
    assert HostedHarnessScenario.no_workspace_objects.filter(
        job=environment, scenario_key=_key(late_name)
    ).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("state", ["starting", "degraded"])
def test_recovery_abandons_old_commands_without_redelivering_on_resend(
    organization, state
):
    from django.utils import timezone

    from simulate.services.hosted_harness_conversation import (
        recover_stalled_conversations,
    )

    job, _ = create_hosted_job(organization, _payload(), idempotency_key="abandon")
    conversation, original, _ = enqueue_message(
        job, content="Add a scenario", client_request_id="original"
    )
    conversation.state = state
    conversation.save()
    original.created_at = timezone.now() - timedelta(minutes=16)
    original.save(update_fields=["created_at"])
    scheduled = []
    counts = recover_stalled_conversations(scheduled.append)
    original.refresh_from_db()
    assert original.state == "failed"
    assert counts["abandoned"] == 1
    assert scheduled == []
    conversation, retry, _ = enqueue_message(
        job, content="Add a scenario", client_request_id="retry"
    )
    capability = issue_conversation_capability(
        conversation,
        endpoint_base_url="https://platform.example",
        provider_ref="replacement",
        attempt=None,
        ttl_seconds=600,
    )
    response = APIClient().get(
        f"{BASE}/{conversation.id}/commands/?after=0", **_headers(capability)
    )
    assert [row["message_id"] for row in response.json()["commands"]] == [str(retry.id)]


@pytest.mark.django_db
def test_recovery_reaches_stalled_chat_past_live_candidates(organization):
    from django.utils import timezone

    from simulate.models import HostedHarnessConversationLease
    from simulate.services.hosted_harness_conversation import (
        recover_stalled_conversations,
    )

    for index in range(3):
        job, _ = create_hosted_job(
            organization, _payload(), idempotency_key=f"live-{index}"
        )
        conversation, _, _ = enqueue_message(
            job, content="Explain", client_request_id=f"message-{index}"
        )
        capability = issue_conversation_capability(
            conversation,
            endpoint_base_url="https://platform.example",
            provider_ref=f"live-{index}",
            attempt=None,
            ttl_seconds=600,
        )
        HostedHarnessConversationLease.no_workspace_objects.filter(
            id=capability.lease.id
        ).update(state="active", heartbeat_at=timezone.now())
    job, _ = create_hosted_job(organization, _payload(), idempotency_key="stalled")
    stalled, _, _ = enqueue_message(job, content="Explain", client_request_id="stalled")
    type(stalled).no_workspace_objects.filter(id=stalled.id).update(
        updated_at=timezone.now() - timedelta(seconds=61)
    )
    scheduled = []
    recover_stalled_conversations(scheduled.append, limit=2)
    recover_stalled_conversations(scheduled.append, limit=2)
    assert scheduled == [str(stalled.id)]


@pytest.mark.django_db
def test_replacement_capability_preserves_unacknowledged_invocation(organization):
    from simulate.services.hosted_harness_conversation import settle_interrupted_turn

    job, _ = create_hosted_job(organization, _payload(), idempotency_key="resume")
    conversation, _, _ = enqueue_message(
        job, content="Continue", client_request_id="resume"
    )
    conversation.active_invocation_id = "gemini-interrupted-turn"
    conversation.save()
    reply = conversation.messages.create(
        sequence=conversation.next_message_sequence,
        role="assistant",
        kind="message",
        state="streaming",
        content="Working",
    )
    settle_interrupted_turn(conversation)
    conversation.save(update_fields=["active_invocation_id"])
    capability = issue_conversation_capability(
        conversation,
        endpoint_base_url="https://platform.example",
        provider_ref="replacement",
        attempt=None,
        ttl_seconds=600,
    )
    reply.refresh_from_db()
    assert reply.state == "failed"
    assert (
        capability.document["turn_context"]["active_invocation_id"]
        == "gemini-interrupted-turn"
    )


@pytest.mark.django_db
def test_message_is_rejected_not_queued_without_public_callback_url(
    organization, settings
):
    from simulate.services.harness_provider import HostedHarnessProvider

    settings.HARNESS_PUBLIC_BASE_URL = ""
    job, _ = create_hosted_job(
        organization, _payload(), idempotency_key="no-public-url"
    )
    provider = HostedHarnessProvider()
    provider._job = lambda _request, _pk: job
    request = SimpleNamespace(
        validated_data={
            "content": "Explain",
            "client_request_id": "no-url",
            "kind": "user_message",
            "payload": {},
        },
        build_absolute_uri=lambda _path: "http://localhost:8000/",
    )
    response = provider.send_message(request, job.id)
    assert response.status_code == 500
    assert response.data["error"] == "public_base_url_missing"
    assert not HostedHarnessConversationMessage.no_workspace_objects.filter(
        conversation__job=job
    ).exists()


@pytest.mark.django_db
def test_command_poll_activates_starting_lease_and_throttles_heartbeat(organization):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from simulate.models import HostedHarnessConversationLease
    from simulate.services.hosted_harness_conversation import runtime_start_pending

    job, _ = create_hosted_job(organization, _payload(), idempotency_key="heartbeat")
    conversation, _, _ = enqueue_message(
        job, content="Explain", client_request_id="heartbeat"
    )
    capability = issue_conversation_capability(
        conversation,
        endpoint_base_url="https://platform.example",
        provider_ref="booting",
        attempt=None,
        ttl_seconds=1200,
    )
    capability.lease.heartbeat_at -= timedelta(seconds=100)
    capability.lease.save(update_fields=["heartbeat_at"])
    assert runtime_start_pending(capability.lease)
    client = APIClient()
    first = client.get(
        f"{BASE}/{conversation.id}/commands/?after=0", **_headers(capability)
    )
    assert first.status_code == 200
    lease = HostedHarnessConversationLease.no_workspace_objects.get(
        id=capability.lease.id
    )
    assert lease.state == "active"
    with CaptureQueriesContext(connection) as queries:
        second = client.get(
            f"{BASE}/{conversation.id}/commands/?after=0", **_headers(capability)
        )
    assert second.json()["commands"] == first.json()["commands"]
    assert not any(query["sql"].startswith("UPDATE") for query in queries)


def _storage_error(code):
    from minio.error import S3Error

    return S3Error(None, code, "storage said no", "key", "request", "host")


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("storage_code", "permanent"), [("NoSuchKey", True), ("SlowDown", False)]
)
def test_chat_on_run_whose_saved_files_are_gone_fails_at_once(
    organization, monkeypatch, storage_code, permanent
):
    from simulate.services import hosted_harness_gateway as gateway_module

    job, _ = create_hosted_job(organization, _payload(), idempotency_key="gone")
    job.state = HostedHarnessJob.State.COMPLETED
    job.payload["metadata"]["authoring_object_key"] = (
        "harness-authoring/org/gone.tar.gz"
    )
    job.save()
    conversation, message, _ = enqueue_message(
        job, content="whats up?", client_request_id="gone"
    )

    def get_object(*_args):
        raise _storage_error(storage_code)

    monkeypatch.setattr(
        gateway_module,
        "get_storage_client",
        lambda: SimpleNamespace(get_object=get_object),
    )
    gateway = object.__new__(gateway_module.HostedHarnessGateway)
    gateway.client = SimpleNamespace(runtime_name="", runtime_digest="")
    with pytest.raises(gateway_module.HostedHarnessError):
        gateway.ensure_conversation_runtime(
            job, conversation=conversation, endpoint_base_url="https://platform.example"
        )

    message.refresh_from_db()
    conversation.refresh_from_db()
    reply = conversation.messages.order_by("-sequence").first()
    runtime = serialize_conversation(conversation)["runtime"]
    if permanent:
        assert message.state == "failed"
        assert reply.role == "assistant" and "no longer available" in reply.content
        assert runtime["available"] is False
        assert conversation.state == "retired"
    else:
        # A transient storage error keeps the message for the recovery sweep to retry.
        assert message.state == "queued"
        assert runtime["available"] is True


@pytest.mark.django_db
def test_missing_checkpoint_object_falls_back_instead_of_retiring(
    organization, monkeypatch
):
    from minio.error import S3Error

    from simulate.services import hosted_harness_conversation as conv_module

    job, _ = create_hosted_job(organization, _payload(), idempotency_key="ckpt-gone")
    conversation, _, _ = enqueue_message(
        job, content="hi", client_request_id="ckpt-gone"
    )
    type(conversation).no_workspace_objects.filter(id=conversation.id).update(
        latest_workspace_object_key="harness-checkpoints/org/gone.tar.gz"
    )
    conversation.refresh_from_db()

    def raise_code(code):
        def get_object(*_args):
            raise _storage_error(code)

        monkeypatch.setattr(
            conv_module,
            "get_storage_client",
            lambda: SimpleNamespace(get_object=get_object),
        )

    # A gone checkpoint is treated as no checkpoint, so chat falls back to the
    # authoring archive rather than retiring.
    raise_code("NoSuchKey")
    assert conv_module.load_workspace_archive(conversation) is None
    # A transient storage error still propagates for the recovery sweep to retry.
    raise_code("SlowDown")
    with pytest.raises(S3Error):
        conv_module.load_workspace_archive(conversation)


@pytest.mark.django_db
def test_send_message_rejected_after_conversation_retired(organization):
    from simulate.services.harness_provider import HostedHarnessProvider

    job, _ = create_hosted_job(organization, _payload(), idempotency_key="retired-409")
    job.state = HostedHarnessJob.State.COMPLETED
    job.payload["metadata"]["authoring_object_key"] = (
        "harness-authoring/org/gone.tar.gz"
    )
    job.save()
    conversation, _, _ = enqueue_message(
        job, content="hi", client_request_id="retired-409"
    )
    type(conversation).no_workspace_objects.filter(id=conversation.id).update(
        state="retired"
    )

    provider = HostedHarnessProvider()
    provider._job = lambda _request, _pk: job
    request = SimpleNamespace(
        validated_data={
            "content": "let me back in",
            "client_request_id": "again",
            "kind": "user_message",
            "payload": {},
        },
        build_absolute_uri=lambda _path: "https://platform.example/",
    )
    response = provider.send_message(request, job.id)
    assert response.status_code == 409
    assert response.data["error"] == "conversation_workspace_not_ready"
    assert "rebuild" in response.data["message"]
    assert not HostedHarnessConversationMessage.no_workspace_objects.filter(
        conversation=conversation, client_request_id="again"
    ).exists()
