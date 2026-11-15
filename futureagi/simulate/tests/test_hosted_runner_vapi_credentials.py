"""Hosted jobs bind provider and target identity to explicit credential IDs."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from accounts.models import Organization
from accounts.models.workspace import Workspace
from simulate.models import AgentDefinition, AgentVersion, RunTest, Scenarios
from simulate.models import TestExecution as SimTestExecution
from simulate.models.agent_definition import ProviderCredentials
from simulate.services.hosted_runner import (
    HostedRunnerBuildError,
    build_start_runner_job,
    hosted_runner_supports,
)

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


def _agent(organization, workspace):
    return AgentDefinition.objects.create(
        agent_name="Synthetic Vapi target",
        agent_type="voice",
        provider="vapi",
        assistant_id="definition-assistant",
        api_key="synthetic-definition-key-not-for-hosted",
        inbound=True,
        contact_number="",
        description="Synthetic hosted credential regression",
        organization=organization,
        workspace=workspace,
    )


def _version(agent, number, assistant_id):
    return AgentVersion.objects.create(
        agent_definition=agent,
        organization=agent.organization,
        workspace=agent.workspace,
        version_number=number,
        configuration_snapshot={
            "provider": "vapi",
            "agent_type": "voice",
            "assistant_id": assistant_id,
            "agent_name": f"Version {number}",
            "contact_number": "",
            "inbound": True,
        },
    )


@pytest.fixture
def hosted_vapi(organization, workspace, settings):
    settings.LIVEKIT_URL = "wss://synthetic-livekit.test"
    settings.ALK_RUNNER_API_URL = "https://synthetic-sink.test"
    agent = _agent(organization, workspace)
    pinned = _version(agent, 1, "snapshot-assistant-a")
    owner = _version(agent, 2, "snapshot-assistant-b")
    scenario = Scenarios.objects.create(
        name="Synthetic hosted scenario",
        source="test",
        description="Ask about a synthetic order.",
        agent_definition=agent,
        organization=organization,
        workspace=workspace,
    )
    run = RunTest.objects.create(
        name="Pinned Vapi run",
        agent_definition=agent,
        agent_version=pinned,
        organization=organization,
        workspace=workspace,
    )
    run.scenarios.add(scenario)
    execution = SimTestExecution.objects.create(
        run_test=run,
        agent_definition=agent,
        agent_version=pinned,
        scenario_ids=[str(scenario.id)],
    )
    return SimpleNamespace(
        agent=agent,
        pinned=pinned,
        owner=owner,
        run=run,
        execution=execution,
        scenario=scenario,
    )


def _legacy(context, *, assistant_id="", owner=True):
    # Migration 0076 retains the definition FK while linking the row to B.
    return ProviderCredentials.objects.create(
        agent_definition=context.agent,
        agent_version=context.owner if owner else None,
        provider_type="vapi",
        api_key="synthetic-legacy-key-b",
        assistant_id=assistant_id,
    )


def _build(context):
    # The builder must pass a reference, never decrypt or substitute a secret.
    with (
        patch.object(
            ProviderCredentials,
            "get_api_key",
            side_effect=AssertionError("hosted builder must not decrypt credentials"),
        ) as get_api_key,
        patch.object(
            ProviderCredentials,
            "get_api_secret",
            side_effect=AssertionError("hosted builder must not decrypt credentials"),
        ) as get_api_secret,
    ):
        job = build_start_runner_job(
            test_execution_id=str(context.execution.id),
            run_test_id=str(context.run.id),
            scenario_ids=[str(context.scenario.id)],
            mode="voice_webrtc",
        )
        get_api_key.assert_not_called()
        get_api_secret.assert_not_called()
        return job


def _assert_target(job, credentials, assistant_id):
    target = job["voice"]["agent_definition"]
    assert target["transport"] == {"kind": "vapi_websocket"}
    assert target["target"] == {
        "provider": "vapi",
        "assistant_id": assistant_id,
        "api_key_env": "VAPI_API_KEY",
    }
    refs = [
        ref for ref in job["metadata"]["secret_env"] if ref["key"] == "VAPI_API_KEY"
    ]
    assert refs == [
        {
            "key": "VAPI_API_KEY",
            "manager": "provider_credentials",
            "credential_id": str(credentials.id),
            "field": "api_key",
        }
    ]
    serialized = json.dumps(job)
    assert "synthetic-legacy-key-b" not in serialized
    assert "synthetic-own-key-a" not in serialized
    assert "synthetic-definition-key-not-for-hosted" not in serialized
    if credentials.api_key:
        assert credentials.api_key not in serialized


@pytest.mark.parametrize("row_assistant", ["", "row-assistant-b"])
def test_build_hosted_vapi_uses_migration_linked_credential_owner(
    hosted_vapi, row_assistant
):
    context = hosted_vapi
    credentials = _legacy(context, assistant_id=row_assistant)
    assert not ProviderCredentials.objects.filter(agent_version=context.pinned).exists()
    assert credentials.get_api_key() == "synthetic-legacy-key-b"
    before = {
        version.id: version.configuration_snapshot.copy()
        for version in (context.pinned, context.owner)
    }

    job = _build(context)

    _assert_target(job, credentials, row_assistant or "snapshot-assistant-b")
    # Other authored job fields still come from pin A, not credential owner B.
    assert job["voice"]["agent_definition"]["name"] == "Version 1"
    for version in (context.pinned, context.owner):
        version.refresh_from_db()
        assert version.configuration_snapshot == before[version.id]
    credentials.refresh_from_db()
    assert credentials.assistant_id == row_assistant
    assert credentials.agent_version_id == context.owner.id


@pytest.mark.parametrize("row_assistant", ["", "row-assistant-a"])
def test_build_hosted_vapi_prefers_own_row_over_legacy(hosted_vapi, row_assistant):
    _legacy(hosted_vapi, assistant_id="stale-legacy-assistant")
    credentials = ProviderCredentials.objects.create(
        agent_version=hosted_vapi.pinned,
        provider_type="vapi",
        api_key="synthetic-own-key-a",
        assistant_id=row_assistant,
    )

    _assert_target(
        _build(hosted_vapi), credentials, row_assistant or "snapshot-assistant-a"
    )


@pytest.mark.parametrize("row_assistant", ["", "unversioned-row-assistant"])
def test_build_hosted_vapi_unversioned_legacy_uses_definition_identity(
    hosted_vapi, row_assistant
):
    credentials = _legacy(hosted_vapi, assistant_id=row_assistant, owner=False)
    _assert_target(
        _build(hosted_vapi), credentials, row_assistant or "definition-assistant"
    )


@pytest.mark.parametrize("wrong_provider", ["retell", "livekit"])
def test_build_hosted_vapi_rejects_wrong_provider_own_row(hosted_vapi, wrong_provider):
    _legacy(hosted_vapi, assistant_id="valid-but-not-selected")
    ProviderCredentials.objects.create(
        agent_version=hosted_vapi.pinned,
        provider_type=wrong_provider,
        api_key="synthetic-other-provider-key",
        assistant_id="other-provider-assistant",
    )
    with pytest.raises(HostedRunnerBuildError, match="do not match provider"):
        _build(hosted_vapi)


@pytest.mark.parametrize("owner_provider", ["bland", "retell"])
@pytest.mark.parametrize("row_assistant", ["", "row-assistant-b"])
def test_build_hosted_vapi_rejects_incompatible_legacy_owner(
    hosted_vapi, owner_provider, row_assistant
):
    # A VAPI-encoded row does not prove its owning version was authored for Vapi.
    _legacy(hosted_vapi, assistant_id=row_assistant)
    owner = hosted_vapi.owner
    owner.configuration_snapshot["provider"] = owner_provider
    owner.save(update_fields=["configuration_snapshot"])
    with pytest.raises(HostedRunnerBuildError, match="owner does not match provider"):
        _build(hosted_vapi)


@pytest.mark.parametrize("snapshot_provider", ["bland", None])
def test_build_hosted_bland_is_not_routed_as_vapi_by_row_encoding(
    hosted_vapi, snapshot_provider
):
    ProviderCredentials.objects.create(
        agent_version=hosted_vapi.pinned,
        provider_type="vapi",
        api_key="synthetic-bland-key",
        assistant_id="synthetic-bland-assistant",
    )
    if snapshot_provider:
        hosted_vapi.pinned.configuration_snapshot["provider"] = snapshot_provider
    else:
        hosted_vapi.pinned.configuration_snapshot.pop("provider")
        hosted_vapi.agent.provider = "bland"
        hosted_vapi.agent.save(update_fields=["provider"])
    hosted_vapi.pinned.save(update_fields=["configuration_snapshot"])
    with pytest.raises(HostedRunnerBuildError, match="do not match provider"):
        _build(hosted_vapi)


def test_build_hosted_vapi_unversioned_legacy_checks_definition_provider(
    hosted_vapi,
):
    _legacy(hosted_vapi, assistant_id="legacy-assistant", owner=False)
    agent = hosted_vapi.agent
    agent.provider = "retell"
    agent.save(update_fields=["provider"])
    # Pin A is still Vapi, but the unversioned legacy row now belongs to Retell.
    with pytest.raises(HostedRunnerBuildError, match="owner does not match provider"):
        _build(hosted_vapi)


def test_build_hosted_vapi_own_snapshot_outlives_definition_provider(hosted_vapi):
    credentials = _legacy(hosted_vapi)
    agent = hosted_vapi.agent
    agent.provider = "retell"
    agent.save(update_fields=["provider"])
    _assert_target(_build(hosted_vapi), credentials, "snapshot-assistant-b")


def test_build_hosted_vapi_legacy_snapshot_without_provider_uses_definition(
    hosted_vapi,
):
    credentials = _legacy(hosted_vapi)
    for version in (hosted_vapi.pinned, hosted_vapi.owner):
        version.configuration_snapshot.pop("provider")
        version.save(update_fields=["configuration_snapshot"])
    _assert_target(_build(hosted_vapi), credentials, "snapshot-assistant-b")
    for version in (hosted_vapi.pinned, hosted_vapi.owner):
        version.refresh_from_db()
        assert "provider" not in version.configuration_snapshot


def test_build_hosted_vapi_missing_owner_identity_does_not_borrow_pin(hosted_vapi):
    _legacy(hosted_vapi)
    owner = hosted_vapi.owner
    owner.configuration_snapshot.pop("assistant_id")
    owner.save(update_fields=["configuration_snapshot"])
    with pytest.raises(HostedRunnerBuildError, match="missing vapi assistant_id"):
        _build(hosted_vapi)


@pytest.mark.parametrize("scope", ["agent", "organization", "workspace"])
@pytest.mark.parametrize("row_assistant", ["", "foreign-row-assistant"])
def test_build_hosted_vapi_rejects_foreign_credential_owner(
    hosted_vapi, scope, row_assistant
):
    context = hosted_vapi
    owner = context.owner
    if scope == "agent":
        owner.agent_definition = _agent(
            context.agent.organization, context.agent.workspace
        )
    elif scope == "organization":
        owner.organization = Organization.objects.create(name="Synthetic other org")
    else:
        owner.workspace = Workspace.objects.create(
            name="Synthetic other workspace",
            organization=context.agent.organization,
            created_by=context.agent.workspace.created_by,
        )
    owner.save(update_fields=["agent_definition", "organization", "workspace"])
    _legacy(context, assistant_id=row_assistant)
    with pytest.raises(HostedRunnerBuildError, match="outside the agent scope"):
        _build(context)


def test_build_hosted_vapi_rejects_own_row_with_foreign_legacy_link(hosted_vapi):
    other = _agent(hosted_vapi.agent.organization, hosted_vapi.agent.workspace)
    ProviderCredentials.objects.create(
        agent_version=hosted_vapi.pinned,
        agent_definition=other,
        provider_type="vapi",
        api_key="synthetic-own-key-a",
        assistant_id="own-row-assistant",
    )
    with pytest.raises(HostedRunnerBuildError, match="belong to another agent"):
        _build(hosted_vapi)


@pytest.mark.parametrize("raw_key", ["", "enc::synthetic-unreadable-ciphertext"])
def test_build_hosted_vapi_never_replaces_empty_or_unreadable_selected_row(
    hosted_vapi, raw_key
):
    _legacy(hosted_vapi, assistant_id="legacy-assistant-b")
    credentials = ProviderCredentials.objects.create(
        agent_version=hosted_vapi.pinned,
        provider_type="vapi",
        api_key=raw_key,
        assistant_id="own-row-assistant",
    )
    # Secret validation remains at exact-ID resolution in Temporal. Neither
    # raw-empty nor unreadable ciphertext authorizes changing the reference.
    _assert_target(_build(hosted_vapi), credentials, "own-row-assistant")
    credentials.refresh_from_db()
    assert credentials.api_key == raw_key


@pytest.mark.parametrize("provider", ["livekit", "livekit_bridge"])
@pytest.mark.parametrize("provider_source", ["snapshot", "definition"])
def test_build_hosted_livekit_keeps_exact_credential_references(
    hosted_vapi, provider, provider_source
):
    context = hosted_vapi
    if provider_source == "snapshot":
        # The authored provider wins over a stale Vapi definition and legacy row.
        context.pinned.configuration_snapshot["provider"] = provider
    else:
        context.pinned.configuration_snapshot.pop("provider")
        context.agent.provider = provider
        context.agent.save(update_fields=["provider"])
    context.pinned.save(update_fields=["configuration_snapshot"])
    snapshot_before = context.pinned.configuration_snapshot.copy()
    _legacy(context, assistant_id="stale-vapi-assistant")
    credentials = ProviderCredentials.objects.create(
        agent_version=context.pinned,
        provider_type="livekit",
        api_key="synthetic-livekit-key",
        api_secret="synthetic-livekit-secret",
        server_url="wss://synthetic-customer-livekit.test",
        agent_name="synthetic-livekit-agent",
    )
    assert hosted_runner_supports(context.agent, context.pinned)

    job = _build(context)

    assert job["mode"] == "voice_webrtc"
    assert job["metadata"]["provider"] == "livekit"
    assert job["metadata"]["transport"] == "webrtc"
    target = job["voice"]["agent_definition"]
    assert target["transport"] == {"kind": "webrtc"}
    assert target["agent_name"] == "synthetic-livekit-agent"
    assert "target" not in target
    assert "provider_evidence" not in target
    assert job["voice"]["livekit_runtime"] == {
        "url": "wss://synthetic-customer-livekit.test",
        "room_name": f"hosted-{context.execution.id}-{{test_case_id}}",
        "api_key_env": "LIVEKIT_API_KEY",
        "api_secret_env": "LIVEKIT_API_SECRET",
    }
    assert job["metadata"]["secret_env"] == [
        {
            "key": "LIVEKIT_API_KEY",
            "manager": "provider_credentials",
            "credential_id": str(credentials.id),
            "field": "api_key",
        },
        {
            "key": "LIVEKIT_API_SECRET",
            "manager": "provider_credentials",
            "credential_id": str(credentials.id),
            "field": "api_secret",
        },
    ]
    serialized = json.dumps(job)
    for secret in (
        "synthetic-livekit-key",
        "synthetic-livekit-secret",
        "synthetic-legacy-key-b",
        "synthetic-definition-key-not-for-hosted",
        credentials.api_key,
        credentials.api_secret,
    ):
        assert secret not in serialized
    context.pinned.refresh_from_db()
    assert context.pinned.configuration_snapshot == snapshot_before


@pytest.mark.parametrize("provider", ["livekit", "livekit_bridge"])
@pytest.mark.parametrize("wrong_provider", ["vapi", "retell"])
def test_build_hosted_livekit_rejects_wrong_provider_own_row(
    hosted_vapi, provider, wrong_provider
):
    hosted_vapi.pinned.configuration_snapshot["provider"] = provider
    hosted_vapi.pinned.save(update_fields=["configuration_snapshot"])
    ProviderCredentials.objects.create(
        agent_version=hosted_vapi.pinned,
        provider_type=wrong_provider,
        api_key="synthetic-other-provider-key",
        assistant_id="other-provider-assistant",
    )
    with pytest.raises(HostedRunnerBuildError, match="do not match provider"):
        _build(hosted_vapi)
