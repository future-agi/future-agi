"""Guardrail credentials stay inside their org.

A guardrail check names the policy whose encrypted blob holds its credentials
(`_policy`), and the config push swaps the check's "__encrypted__" sentinels for
the decrypted values. The push reads only the pushed org's own live policies,
and update-config refuses an id outside the org (another org's, a deleted or
unknown one) and never stores a credential in clear text.
"""

import json
import uuid
from unittest.mock import MagicMock, patch

import pytest
from rest_framework import status
from structlog.testing import capture_logs

from accounts.models import Organization
from agentcc.models.guardrail_policy import AgentccGuardrailPolicy
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services import config_push
from integrations.services.credentials import CredentialManager

URL = "/agentcc/gateways/default/update-config/"
SENTINEL = "__encrypted__"
CONSOLE_POLICY = "__ui_guardrails__"

# Test values, not real credentials.
ORG_B_SECRET = "org-b-guard-key-test-value"
OWN_SECRET = "own-org-guard-key-test-value"
NEW_SECRET = "clear-text-guard-key-test-value"


def _policy(org, name, secrets=None, **fields):
    return AgentccGuardrailPolicy.no_workspace_objects.create(
        organization=org,
        name=name,
        checks=[{"name": "lakera-guard"}],
        encrypted_check_configs=(
            CredentialManager.encrypt(secrets) if secrets is not None else None
        ),
        **fields,
    )


def _check(policy_id, **config):
    return {
        "name": "lakera-guard",
        "enabled": True,
        "action": "block",
        "config": {"api_key": SENTINEL, **config},
        "_policy": policy_id,
    }


def _guardrails(shape, check):
    """One check in each stored shape the push reads."""
    if shape == "rules":
        return {"rules": [check]}
    if shape == "checks_list":
        return {"enabled": True, "checks": [check]}
    entry = {key: value for key, value in check.items() if key != "name"}
    return {"checks": {check["name"]: entry}}


def _pushed_check(payload):
    return payload["guardrails"]["checks"]["lakera-guard"]


def _config(org, guardrails, version=1, is_active=True):
    return AgentccOrgConfig.no_workspace_objects.create(
        organization=org, version=version, is_active=is_active, guardrails=guardrails
    )


def _active(org):
    return AgentccOrgConfig.no_workspace_objects.get(
        organization=org, is_active=True, deleted=False
    )


@pytest.fixture
def org_b(db):
    return Organization.objects.create(name="Other Organization")


@pytest.fixture
def org_b_policy(org_b):
    return _policy(org_b, "org-b-guard", {"lakera-guard": {"api_key": ORG_B_SECRET}})


@pytest.fixture
def gateway():
    """The gateway client every push path uses; records what it was sent."""
    client = MagicMock()
    with patch.object(config_push, "get_gateway_client", return_value=client):
        yield client


@pytest.fixture
def decrypt_spy():
    real_decrypt = CredentialManager.decrypt
    with patch.object(CredentialManager, "decrypt", side_effect=real_decrypt) as spy:
        yield spy


def _decrypted_blobs(spy):
    return [bytes(call.args[0]) for call in spy.call_args_list]


def _payload_sent_for(gateway, org):
    sent = {
        call.args[0]: call.args[1] for call in gateway.set_org_config.call_args_list
    }
    return sent[str(org.id)]


def _run_push_path(path, org, config, gateway):
    """Run one push path for `org`; the payload it built, or None for save-time
    validation, which builds one but sends nothing."""
    if path == "push_org_config":
        assert config_push.push_org_config(str(org.id), config) is True
        return _payload_sent_for(gateway, org)
    if path == "push_all_org_configs":
        config_push.push_all_org_configs()
        return _payload_sent_for(gateway, org)
    if path == "bulk_sync":
        # The builder views/org_config_bulk.py serves for every active config.
        return config_push._build_payload(str(org.id), config)
    config_push.validate_org_config(config)
    return None


PUSH_PATHS = ["push_org_config", "push_all_org_configs", "bulk_sync", "save_validation"]
SHAPES = ["rules", "checks_list", "checks_map"]


@pytest.mark.integration
class TestPushReadsOnlyTheOrgsOwnPolicies:
    @pytest.mark.parametrize("path", PUSH_PATHS)
    @pytest.mark.parametrize("shape", SHAPES)
    def test_org_a_cannot_obtain_org_b_guardrail_credential(
        self, organization, org_b_policy, gateway, decrypt_spy, shape, path
    ):
        config = _config(organization, _guardrails(shape, _check(str(org_b_policy.id))))

        with capture_logs() as logs:
            payload = _run_push_path(path, organization, config, gateway)

        assert bytes(org_b_policy.encrypted_check_configs) not in _decrypted_blobs(
            decrypt_spy
        )
        if payload is not None:
            assert ORG_B_SECRET not in json.dumps(payload)
            # The sentinel is dropped, as for a missing secret.
            assert "api_key" not in _pushed_check(payload)["config"]
        refused = [log for log in logs if log["event"] == "guardrail_policy_not_in_org"]
        assert refused
        assert all(log["policy_id"] == str(org_b_policy.id) for log in refused)
        assert all(set(log) <= {"event", "log_level", "policy_id"} for log in refused)
        assert ORG_B_SECRET not in repr(logs)

    @pytest.mark.parametrize("shape", SHAPES)
    def test_own_org_policy_credential_is_injected(self, organization, gateway, shape):
        own = _policy(
            organization, "own-guard", {"lakera-guard": {"api_key": OWN_SECRET}}
        )
        config = _config(organization, _guardrails(shape, _check(str(own.id))))

        assert config_push.push_org_config(str(organization.id), config) is True

        pushed = _pushed_check(_payload_sent_for(gateway, organization))
        assert pushed["config"]["api_key"] == OWN_SECRET
        # The stored config keeps the sentinel.
        config.refresh_from_db()
        assert OWN_SECRET not in json.dumps(config.guardrails)

    @pytest.mark.parametrize(
        "policy_id",
        [str(uuid.uuid4()), "not-a-uuid", "deleted"],
        ids=["unknown", "not_a_uuid", "deleted"],
    )
    def test_unknown_or_deleted_policy_drops_the_sentinel(
        self, organization, gateway, decrypt_spy, policy_id
    ):
        if policy_id == "deleted":
            deleted = _policy(
                organization,
                "deleted-guard",
                {"lakera-guard": {"api_key": OWN_SECRET}},
                deleted=True,
            )
            policy_id = str(deleted.id)
        config = _config(organization, _guardrails("rules", _check(policy_id)))

        assert config_push.push_org_config(str(organization.id), config) is True

        payload = _payload_sent_for(gateway, organization)
        assert "api_key" not in _pushed_check(payload)["config"]
        assert OWN_SECRET not in json.dumps(payload)
        assert decrypt_spy.call_count == 0

    @pytest.mark.parametrize(
        ("value", "logged"),
        [
            ("not-a-uuid", "not-a-uu... (10 chars)"),
            ("x" * 200, "xxxxxxxx... (200 chars)"),
            ({"api_key": OWN_SECRET}, "<dict>"),
        ],
        ids=["short_string", "long_string", "object"],
    )
    def test_a_policy_value_that_is_not_a_uuid_is_logged_by_reference(
        self, organization, gateway, decrypt_spy, value, logged
    ):
        config = _config(organization, _guardrails("rules", _check(value)))

        with capture_logs() as logs:
            assert config_push.push_org_config(str(organization.id), config) is True

        refused = [log for log in logs if log["event"] == "guardrail_policy_not_in_org"]
        assert refused == [
            {
                "event": "guardrail_policy_not_in_org",
                "log_level": "warning",
                "policy_id": logged,
                "reason": "not_a_uuid",
            }
        ]
        assert OWN_SECRET not in repr(logs)
        payload = _payload_sent_for(gateway, organization)
        assert "api_key" not in _pushed_check(payload)["config"]
        assert decrypt_spy.call_count == 0

    def test_without_an_org_no_policy_is_read(self, organization, decrypt_spy):
        own = _policy(
            organization, "own-guard", {"lakera-guard": {"api_key": OWN_SECRET}}
        )

        result = config_push._transform_guardrails(
            _guardrails("rules", _check(str(own.id)))
        )

        assert "api_key" not in result["checks"]["lakera-guard"]["config"]
        assert decrypt_spy.call_count == 0


@pytest.mark.integration
@pytest.mark.api
@patch("agentcc.views.gateway.push_org_config", return_value=True)
class TestUpdateConfigGuardrailCredentials:
    @pytest.mark.parametrize("shape", SHAPES)
    def test_update_config_rejects_foreign_policy_id(
        self, push, auth_client, organization, org_b_policy, decrypt_spy, shape
    ):
        _config(organization, {})

        response = auth_client.post(
            URL,
            {"guardrails": _guardrails(shape, _check(str(org_b_policy.id)))},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.json()
        assert response.json()["code"] == "guardrail_policy_not_in_org"
        assert str(org_b_policy.id) in response.json()["message"]
        assert list(
            AgentccOrgConfig.no_workspace_objects.filter(
                organization=organization
            ).values_list("version", "is_active")
        ) == [(1, True)]
        assert bytes(org_b_policy.encrypted_check_configs) not in _decrypted_blobs(
            decrypt_spy
        )
        push.assert_not_called()

    @pytest.mark.parametrize(
        "kind", ["foreign", "held_foreign", "unknown", "deleted", "not_a_uuid"]
    )
    def test_update_config_rejects_every_policy_id_the_org_does_not_own(
        self, push, auth_client, organization, org_b_policy, kind
    ):
        # One answer for all of them, so a response never tells whether an id
        # is a live policy of another org. Another org's policy is refused
        # even when the active version already holds its id.
        policy_id, named_as = {
            "foreign": (str(org_b_policy.id), str(org_b_policy.id)),
            "held_foreign": (str(org_b_policy.id), str(org_b_policy.id)),
            "unknown": (str(uuid.uuid4()),) * 2,
            "deleted": (str(_policy(organization, "deleted-guard", deleted=True).id),)
            * 2,
            "not_a_uuid": ("not-a-uuid", "not-a-uu... (10 chars)"),
        }[kind]
        _config(
            organization,
            {"rules": [_check(policy_id)]} if kind == "held_foreign" else {},
        )

        with capture_logs() as logs:
            response = auth_client.post(
                URL,
                {"guardrails": {"rules": [{**_check(policy_id), "enabled": False}]}},
                format="json",
            )

        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.json()
        body = response.json()
        assert body["code"] == "guardrail_policy_not_in_org"
        assert body["message"] == (
            f"Guardrail policy {named_as} is not one of this organization's policies."
        )
        assert _active(organization).version == 1
        push.assert_not_called()
        rejected = [log for log in logs if log["event"] == "update_config_rejected"]
        assert rejected == [
            {
                "event": "update_config_rejected",
                "log_level": "warning",
                "org_id": str(organization.id),
                "code": "guardrail_policy_not_in_org",
                "policy_ids": (named_as,),
            }
        ]

    @pytest.mark.parametrize("held", ["unknown", "deleted", "not_a_uuid"])
    def test_update_config_accepts_an_unknown_id_the_active_version_holds(
        self, push, auth_client, organization, gateway, decrypt_spy, held
    ):
        # The console sends saved rules back whole, with their ids; an id the
        # active version already holds that names no live policy is accepted
        # and stays inert at push.
        deleted = _policy(
            organization,
            "deleted-guard",
            {"lakera-guard": {"api_key": OWN_SECRET}},
            deleted=True,
        )
        policy_id = {
            "unknown": str(uuid.uuid4()),
            "deleted": str(deleted.id),
            "not_a_uuid": "not-a-uuid",
        }[held]
        _config(organization, {"rules": [_check(policy_id)]})
        rule = {**_check(policy_id), "enabled": False}

        response = auth_client.post(
            URL, {"guardrails": {"rules": [rule]}}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        active = _active(organization)
        assert active.version == 2
        assert active.guardrails["rules"] == [rule]
        config_push.push_org_config(str(organization.id), active)
        pushed = _pushed_check(_payload_sent_for(gateway, organization))
        assert "api_key" not in pushed["config"]
        assert OWN_SECRET not in json.dumps(pushed)
        assert decrypt_spy.call_count == 0

    @pytest.mark.parametrize("shape", SHAPES)
    def test_update_config_guardrail_credential_encrypted_or_rejected(
        self, push, auth_client, organization, gateway, caplog, shape
    ):
        _config(organization, {})
        check = _check(None, api_key=NEW_SECRET)
        del check["_policy"]

        with capture_logs() as logs:
            response = auth_client.post(
                URL, {"guardrails": _guardrails(shape, check)}, format="json"
            )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert NEW_SECRET not in json.dumps(response.json())
        assert NEW_SECRET not in repr(logs)
        assert NEW_SECRET not in caplog.text

        active = _active(organization)
        assert NEW_SECRET not in json.dumps(active.guardrails)
        stored = _guardrails_entry(active.guardrails, shape)
        assert stored["config"]["api_key"] == SENTINEL
        console = AgentccGuardrailPolicy.no_workspace_objects.get(
            organization=organization, name=CONSOLE_POLICY, deleted=False
        )
        assert stored["_policy"] == str(console.id)
        assert CredentialManager.decrypt(bytes(console.encrypted_check_configs)) == {
            "lakera-guard": {"api_key": NEW_SECRET}
        }

        # The gateway still receives the credential.
        config_push.push_org_config(str(organization.id), active)
        pushed = _pushed_check(_payload_sent_for(gateway, organization))
        assert pushed["config"]["api_key"] == NEW_SECRET

    def test_new_credential_joins_the_checks_stored_credentials(
        self, push, auth_client, organization, gateway
    ):
        own = _policy(
            organization, "own-guard", {"lakera-guard": {"secret_key": OWN_SECRET}}
        )
        console = _policy(
            organization,
            CONSOLE_POLICY,
            {"other-guard": {"password": "other-guard-test-value"}},
        )
        _config(organization, {})
        check = _check(str(own.id), secret_key=SENTINEL)
        check["config"]["api_key"] = NEW_SECRET

        response = auth_client.post(
            URL, {"guardrails": {"rules": [check]}}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        rule = _active(organization).guardrails["rules"][0]
        assert rule["_policy"] == str(console.id)
        assert rule["config"] == {"api_key": SENTINEL, "secret_key": SENTINEL}
        console.refresh_from_db()
        assert CredentialManager.decrypt(bytes(console.encrypted_check_configs)) == {
            "other-guard": {"password": "other-guard-test-value"},
            "lakera-guard": {"secret_key": OWN_SECRET, "api_key": NEW_SECRET},
        }
        config_push.push_org_config(str(organization.id), _active(organization))
        pushed = _pushed_check(_payload_sent_for(gateway, organization))
        assert pushed["config"]["api_key"] == NEW_SECRET
        assert pushed["config"]["secret_key"] == OWN_SECRET

    def test_sentinels_only_leave_the_policies_alone(
        self, push, auth_client, organization
    ):
        own = _policy(
            organization, "own-guard", {"lakera-guard": {"api_key": OWN_SECRET}}
        )
        _config(organization, {})

        response = auth_client.post(
            URL, {"guardrails": {"rules": [_check(str(own.id))]}}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _active(organization).guardrails["rules"][0]["_policy"] == str(own.id)
        assert not AgentccGuardrailPolicy.no_workspace_objects.filter(
            organization=organization, name=CONSOLE_POLICY
        ).exists()

    def test_a_rejected_save_stores_no_credential(
        self, push, auth_client, organization
    ):
        _config(organization, {})
        check = _check(None, api_key=NEW_SECRET)
        del check["_policy"]

        response = auth_client.post(
            URL,
            {
                "guardrails": {"rules": [check]},
                # The gateway contract has no such routing key.
                "routing": {"default_strategy": "round-robin"},
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert NEW_SECRET not in json.dumps(response.json())
        assert not AgentccGuardrailPolicy.no_workspace_objects.filter(
            organization=organization, name=CONSOLE_POLICY
        ).exists()
        assert _active(organization).version == 1

    def test_a_credential_on_a_nameless_check_is_refused(
        self, push, auth_client, organization
    ):
        # The push looks credentials up by check name, so a nameless check
        # could only keep its credential in clear text.
        _config(organization, {})

        response = auth_client.post(
            URL,
            {"guardrails": {"rules": [{"config": {"api_key": NEW_SECRET}}]}},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "needs a name" in response.json()["message"]
        assert NEW_SECRET not in json.dumps(response.json())
        assert _active(organization).version == 1

    def test_an_unreadable_console_blob_is_refused_not_overwritten(
        self, push, auth_client, organization
    ):
        # After a key rotation the stored blob cannot be read; writing a new
        # one would drop every other check's stored credential.
        console = _policy(organization, CONSOLE_POLICY)
        AgentccGuardrailPolicy.no_workspace_objects.filter(pk=console.pk).update(
            encrypted_check_configs=b"not-a-fernet-token"
        )
        _config(organization, {})
        check = _check(None, api_key=NEW_SECRET)
        del check["_policy"]

        response = auth_client.post(
            URL, {"guardrails": {"rules": [check]}}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.json()
        assert response.json()["code"] == "guardrail_credentials_unreadable"
        assert NEW_SECRET not in json.dumps(response.json())
        console.refresh_from_db()
        assert bytes(console.encrypted_check_configs) == b"not-a-fernet-token"
        assert _active(organization).version == 1
        push.assert_not_called()

    def test_conflicting_credentials_for_one_check_are_refused(
        self, push, auth_client, organization
    ):
        # The push looks a credential up by check name, so two checks with
        # one name could only both get one of the two values.
        _config(organization, {})
        rule = _check(None, api_key=NEW_SECRET)
        del rule["_policy"]
        again = {**rule, "config": {"api_key": "other-clear-text-test-value"}}

        response = auth_client.post(
            URL, {"guardrails": {"rules": [rule, again]}}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.json()
        assert response.json()["code"] == "guardrail_credential_conflict"
        text = json.dumps(response.json())
        assert NEW_SECRET not in text
        assert "other-clear-text-test-value" not in text
        assert not AgentccGuardrailPolicy.no_workspace_objects.filter(
            organization=organization, name=CONSOLE_POLICY
        ).exists()
        assert _active(organization).version == 1

    def test_one_credential_sent_twice_for_a_check_is_stored_once(
        self, push, auth_client, organization, gateway
    ):
        _config(organization, {})
        rule = _check(None, api_key=NEW_SECRET)
        del rule["_policy"]
        again = {**rule, "config": {"api_key": NEW_SECRET}, "action": "warn"}

        response = auth_client.post(
            URL, {"guardrails": {"rules": [rule, again]}}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        active = _active(organization)
        assert NEW_SECRET not in json.dumps(active.guardrails)
        console = AgentccGuardrailPolicy.no_workspace_objects.get(
            organization=organization, name=CONSOLE_POLICY, deleted=False
        )
        assert CredentialManager.decrypt(bytes(console.encrypted_check_configs)) == {
            "lakera-guard": {"api_key": NEW_SECRET}
        }
        assert [rule["_policy"] for rule in active.guardrails["rules"]] == [
            str(console.id),
            str(console.id),
        ]

    def test_a_sent_credential_wins_over_a_stored_one_for_the_same_check(
        self, push, auth_client, organization
    ):
        # The second entry names a policy that stores an older api_key; the
        # api_key sent with the first entry is the one kept.
        own = _policy(
            organization, "own-guard", {"lakera-guard": {"api_key": OWN_SECRET}}
        )
        _config(organization, {})
        first = _check(None, api_key=NEW_SECRET)
        del first["_policy"]
        second = _check(str(own.id), secret_key="second-secret-test-value")

        response = auth_client.post(
            URL, {"guardrails": {"rules": [first, second]}}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        console = AgentccGuardrailPolicy.no_workspace_objects.get(
            organization=organization, name=CONSOLE_POLICY, deleted=False
        )
        assert CredentialManager.decrypt(bytes(console.encrypted_check_configs)) == {
            "lakera-guard": {
                "api_key": NEW_SECRET,
                "secret_key": "second-secret-test-value",
            }
        }


def _guardrails_entry(guardrails, shape):
    if shape == "rules":
        return guardrails["rules"][0]
    if shape == "checks_list":
        return guardrails["checks"][0]
    return guardrails["checks"]["lakera-guard"]
