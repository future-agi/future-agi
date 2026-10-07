from __future__ import annotations

import pytest

from simulate.models import HostedHarnessJob, HostedHarnessSecret
from simulate.services import harness_credential_probes
from simulate.services.harness_credentials import (
    HOSTED_FILE_KEY_PREFIX,
    store_secret_values,
)
from simulate.services.hosted_harness import create_hosted_job

from .test_hosted_harness_channels import _payload

ENVIRONMENTS = "/simulate/api/harness-environments"


class _Response:
    def __init__(self, status_code):
        self.status_code = status_code

    def json(self):
        return {"name": "Clinic agent", "agent_name": "Clinic agent"}


@pytest.fixture
def providers(monkeypatch):
    statuses: dict[str, int] = {}
    calls: list[str] = []

    def answer(url):
        calls.append(url)
        for marker, code in statuses.items():
            if marker in url:
                return _Response(code)
        return _Response(200)

    monkeypatch.setattr(
        harness_credential_probes.requests,
        "request",
        lambda method, url, **kwargs: answer(url),
    )
    monkeypatch.setattr(
        harness_credential_probes.requests, "get", lambda url, **kwargs: answer(url)
    )
    return statuses, calls


@pytest.fixture
def environment(db, user, workspace):
    refs = store_secret_values(
        user.organization,
        {"VAPI_API_KEY": "vapi-old", "DEEPGRAM_API_KEY": "deepgram-old"},
    )
    job, _ = create_hosted_job(
        user.organization,
        _payload(
            agent={
                "connector": "vapi",
                "mode": "connect_only",
                "config": {
                    "assistant_id": "asst_1",
                    "phone_number": "+14155550100",
                    "dynamic_variables": {"tier": "gold"},
                },
                "secret_refs": refs,
            }
        ),
        idempotency_key="env-configuration",
        workspace=workspace,
    )
    HostedHarnessJob.no_workspace_objects.filter(id=job.id).update(
        state=HostedHarnessJob.State.COMPLETED
    )
    job.refresh_from_db()
    return job


def _patch(client, environment, body):
    return client.patch(
        f"{ENVIRONMENTS}/{environment.id}/configuration/", body, format="json"
    )


def _secret(environment, alias):
    environment.refresh_from_db()
    ref = environment.payload["agent"]["secret_refs"][alias]
    return HostedHarnessSecret.no_workspace_objects.get(
        organization=environment.organization, name=ref["key"]
    ).get_value()


def _statuses(response):
    return {
        alias: check["status"]
        for check in response.json()["checks"]
        for alias in check["aliases"]
    }


def test_replacing_a_key_checks_it_and_the_next_run_reads_the_new_value(
    auth_client, environment, providers
):
    _, calls = providers

    response = _patch(
        auth_client,
        environment,
        {"environment_values": {"DEEPGRAM_API_KEY": "deepgram-new"}},
    )

    assert response.status_code == 200, response.content
    assert _statuses(response) == {"DEEPGRAM_API_KEY": "accepted"}
    assert _secret(environment, "DEEPGRAM_API_KEY") == "deepgram-new"
    assert _secret(environment, "VAPI_API_KEY") == "vapi-old"
    assert calls == ["https://api.deepgram.com/v1/projects"]
    assert "deepgram-new" not in response.content.decode()
    assert response.json()["environment"]["settings"]["agent"]["secrets"] == [
        "DEEPGRAM_API_KEY",
        "VAPI_API_KEY",
    ]


def test_a_key_the_environment_never_had_can_be_added(
    auth_client, environment, providers
):
    response = _patch(
        auth_client,
        environment,
        {"environment_values": {"GEMINI_API_KEY": "gemini-new"}},
    )

    assert response.status_code == 200, response.content
    assert _statuses(response) == {"GEMINI_API_KEY": "accepted"}
    assert _secret(environment, "GEMINI_API_KEY") == "gemini-new"


def test_a_rejected_key_saves_nothing(auth_client, environment, providers):
    statuses, _ = providers
    statuses["api.deepgram.com"] = 401
    before = environment.payload
    secrets_before = HostedHarnessSecret.no_workspace_objects.count()

    response = _patch(
        auth_client, environment, {"environment_values": {"DEEPGRAM_API_KEY": "wrong"}}
    )

    assert response.status_code == 400, response.content
    assert response.json()["error"] == "credential_rejected"
    assert _statuses(response) == {"DEEPGRAM_API_KEY": "rejected"}
    environment.refresh_from_db()
    assert environment.payload == before
    assert HostedHarnessSecret.no_workspace_objects.count() == secrets_before


def test_a_stale_key_the_edit_did_not_touch_does_not_block_it(
    auth_client, environment, providers
):
    statuses, calls = providers
    statuses["api.vapi.ai"] = 401

    response = _patch(
        auth_client,
        environment,
        {"environment_values": {"DEEPGRAM_API_KEY": "deepgram-new"}},
    )

    assert response.status_code == 200, response.content
    assert not any("vapi" in url for url in calls)


def test_a_new_provider_key_rechecks_the_assistant_it_must_reach(
    auth_client, environment, providers
):
    statuses, calls = providers
    statuses["api.vapi.ai/assistant/asst_1"] = 404

    response = _patch(
        auth_client, environment, {"environment_values": {"VAPI_API_KEY": "vapi-other"}}
    )

    assert response.status_code == 400, response.content
    assert "https://api.vapi.ai/assistant/asst_1" in calls
    assert _secret(environment, "VAPI_API_KEY") == "vapi-old"


def test_a_key_no_provider_check_covers_is_saved_unchecked(
    auth_client, environment, providers
):
    _, calls = providers

    response = _patch(
        auth_client, environment, {"environment_values": {"CRM_API_KEY": "crm-value"}}
    )

    assert response.status_code == 200, response.content
    assert _statuses(response) == {"CRM_API_KEY": "not_checked"}
    assert calls == []
    assert _secret(environment, "CRM_API_KEY") == "crm-value"


def test_connection_settings_change_without_a_key_check(
    auth_client, environment, providers
):
    _, calls = providers

    response = _patch(
        auth_client,
        environment,
        {
            "config": {
                "phone_number": "+14155550199",
                "dynamic_variables": {"tier": "silver", "region": "eu"},
            }
        },
    )

    assert response.status_code == 200, response.content
    assert calls == []
    environment.refresh_from_db()
    config = environment.payload["agent"]["config"]
    assert config["phone_number"] == "+14155550199"
    assert config["dynamic_variables"] == {"tier": "silver", "region": "eu"}
    assert config["assistant_id"] == "asst_1"


def test_a_connection_setting_the_environment_never_had_needs_a_rebuild(
    auth_client, environment, providers
):
    response = _patch(
        auth_client, environment, {"config": {"livekit_url": "wss://agent.example.com"}}
    )

    assert response.status_code == 400, response.content
    assert response.json()["error"] == "configuration_needs_rebuild"


@pytest.mark.parametrize(
    "field", ["assistant_id", "target_system_prompt", "connector", "inbound"]
)
def test_changing_what_the_agent_is_needs_a_rebuild(
    auth_client, environment, providers, field
):
    response = _patch(auth_client, environment, {"config": {field: "other"}})

    assert response.status_code == 400, response.content
    assert "needs a rebuild" in response.content.decode()
    environment.refresh_from_db()
    assert environment.payload["agent"]["config"]["assistant_id"] == "asst_1"


@pytest.mark.parametrize(
    "values",
    [{"DEEPGRAM_API_KEY": "  "}, {"SIMULATOR_DEEPGRAM_API_KEY": "ours"}],
)
def test_keys_cannot_be_removed_or_take_a_platform_name(
    auth_client, environment, providers, values
):
    response = _patch(auth_client, environment, {"environment_values": values})

    assert response.status_code == 400, response.content
    assert _secret(environment, "DEEPGRAM_API_KEY") == "deepgram-old"


def test_an_unknown_field_is_refused(auth_client, environment, providers):
    response = _patch(auth_client, environment, {"source": {"kind": "github"}})

    assert response.status_code == 400, response.content


def test_an_empty_edit_is_refused(auth_client, environment, providers):
    response = _patch(auth_client, environment, {})

    assert response.status_code == 400, response.content


@pytest.mark.parametrize(
    "state", [HostedHarnessJob.State.FAILED, HostedHarnessJob.State.RUNNING]
)
def test_only_a_built_environment_is_edited_here(
    auth_client, environment, providers, state
):
    HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(state=state)

    response = _patch(
        auth_client,
        environment,
        {"environment_values": {"DEEPGRAM_API_KEY": "deepgram-new"}},
    )

    assert response.status_code == 409, response.content
    assert response.json()["error"] == "environment_not_completed"


def test_a_replaced_credential_file_is_saved_unchecked(
    auth_client, environment, providers
):
    key = f"{HOSTED_FILE_KEY_PREFIX}replacement"
    HostedHarnessSecret.objects.create(
        organization=environment.organization,
        name=key,
        version="1",
        encrypted_value='{"type": "service_account"}',
    )
    ref = {
        "manager": "platform-vault",
        "key": key,
        "version": "1",
        "purpose": "target_provider",
    }

    response = _patch(
        auth_client,
        environment,
        {"credential_files": {"GOOGLE_APPLICATION_CREDENTIALS_JSON": ref}},
    )

    assert response.status_code == 200, response.content
    assert _statuses(response) == {"GOOGLE_APPLICATION_CREDENTIALS_JSON": "not_checked"}
    assert response.json()["environment"]["settings"]["agent"]["credential_files"] == [
        {"environment_name": "GOOGLE_APPLICATION_CREDENTIALS_JSON"}
    ]


def test_a_credential_file_must_be_one_this_organization_uploaded(
    auth_client, environment, providers
):
    ref = {
        "manager": "platform-vault",
        "key": f"{HOSTED_FILE_KEY_PREFIX}missing",
        "version": "1",
        "purpose": "target_provider",
    }

    response = _patch(
        auth_client,
        environment,
        {"credential_files": {"GOOGLE_APPLICATION_CREDENTIALS_JSON": ref}},
    )

    assert response.status_code == 400, response.content
    assert response.json()["error"] == "credential_file_not_found"


def test_each_edit_is_recorded_by_name_never_by_value(
    auth_client, environment, providers
):
    _patch(
        auth_client,
        environment,
        {
            "environment_values": {"DEEPGRAM_API_KEY": "deepgram-new"},
            "config": {"dynamic_variables": {"tier": "silver"}},
        },
    )

    environment.refresh_from_db()
    (change,) = environment.payload["metadata"]["config_changes"]
    assert change["secrets"] == ["DEEPGRAM_API_KEY"]
    assert change["config"] == ["dynamic_variables"]
    assert change["credential_files"] == []
    assert change["user_id"]
    assert "deepgram-new" not in str(environment.payload)


@pytest.mark.parametrize("value", [True, False])
def test_who_speaks_first_changes_without_a_key_check(
    auth_client, environment, providers, value
):
    _, calls = providers

    response = _patch(
        auth_client, environment, {"config": {"target_speaks_first": value}}
    )

    assert response.status_code == 200, response.content
    assert calls == []
    environment.refresh_from_db()
    assert environment.payload["agent"]["config"]["target_speaks_first"] is value


def test_who_speaks_first_must_be_true_or_false(auth_client, environment, providers):
    response = _patch(
        auth_client, environment, {"config": {"target_speaks_first": "yes"}}
    )

    assert response.status_code == 400, response.content
    environment.refresh_from_db()
    assert "target_speaks_first" not in environment.payload["agent"]["config"]
