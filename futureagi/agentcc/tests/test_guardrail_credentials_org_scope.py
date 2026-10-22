"""Guardrail credentials stay inside their org.

A guardrail check's credentials live in its policy's encrypted blob, and the
config push injects them only from the pushed org's own live policies.
update-config, update-guardrail and POST org-configs refuse an id the org may
not name with 400
guardrail_policy_not_in_org: one answer for another org's, a deleted, an
unknown and a non-UUID id, so a response never tells whether an id is another
org's live policy. An id the org's active version already holds, and that is
no org's live policy, is still accepted (the console sends saved checks back
whole), and stays inert at push.

No value here is a real credential.
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

UPDATE_CONFIG = "/agentcc/gateways/default/update-config/"
UPDATE_GUARDRAIL = "/agentcc/gateways/default/update-guardrail/"
ORG_CONFIGS = "/agentcc/org-configs/"
SENTINEL = "__encrypted__"
CODE = "guardrail_policy_not_in_org"

# Test values, not real credentials.
ORG_B_SECRET = "t-org-b-guard-key-test-value"
OWN_SECRET = "t-own-org-guard-key-test-value"
ATTACKER_URL = "https://collector.attacker.example"


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
        "config": {
            "provider": "lakera",
            "endpoint": ATTACKER_URL,
            "api_key": SENTINEL,
            **config,
        },
        "_policy": policy_id,
    }


def _guardrails(shape, check):
    """One check in each stored shape the push reads."""
    if shape == "rules":
        return {"rules": [check]}
    if shape == "checks_list":
        return {"checks": [check]}
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


def _versions(org):
    return list(
        AgentccOrgConfig.no_workspace_objects.filter(organization=org)
        .order_by("version")
        .values_list("version", "is_active")
    )


@pytest.fixture
def org_b(db):
    return Organization.objects.create(name="Other organization")


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
        # A stored config that names another org's policy.
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
        refused = [log for log in logs if log["event"] == CODE]
        assert refused
        assert all(log["policy_id"] == str(org_b_policy.id) for log in refused)
        assert all(set(log) <= {"event", "log_level", "policy_id"} for log in refused)
        assert ORG_B_SECRET not in repr(logs)

    @pytest.mark.parametrize("shape", SHAPES)
    def test_own_org_policy_credential_still_injected(
        self, organization, gateway, shape
    ):
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
        # A value that is not a UUID drops the sentinel too.
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

        refused = [log for log in logs if log["event"] == CODE]
        assert refused == [
            {
                "event": CODE,
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


def _assert_refused(response, named_as):
    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.json()
    body = response.json()
    assert body["code"] == CODE, body
    assert body["message"] == (
        f"Guardrail policy {named_as} is not one of this organization's policies."
    )
    assert ORG_B_SECRET not in json.dumps(body)


@pytest.mark.integration
@pytest.mark.api
class TestUpdateConfigRefusesPolicyIdsTheOrgDoesNotOwn:
    def test_a_foreign_policy_is_refused_and_never_decrypted(
        self, auth_client, organization, org_b_policy, gateway, decrypt_spy
    ):
        # The request names org B's policy id; it is refused before any decrypt.
        _config(organization, {})

        response = auth_client.post(
            UPDATE_CONFIG,
            {
                "guardrails": {
                    "checks": {
                        "x": {
                            "enabled": True,
                            "action": "log",
                            "_policy": str(org_b_policy.id),
                            "config": {
                                "provider": "futureagi",
                                "base_url": ATTACKER_URL,
                                "api_key": SENTINEL,
                                "secret_key": "s",
                                "eval_id": "e",
                            },
                        }
                    }
                }
            },
            format="json",
        )

        _assert_refused(response, str(org_b_policy.id))
        assert _versions(organization) == [(1, True)]
        assert bytes(org_b_policy.encrypted_check_configs) not in _decrypted_blobs(
            decrypt_spy
        )
        gateway.set_org_config.assert_not_called()

    @pytest.mark.parametrize("shape", SHAPES)
    def test_foreign_policy_id_in_every_shape(
        self, auth_client, organization, org_b_policy, gateway, decrypt_spy, shape
    ):
        _config(organization, {})

        response = auth_client.post(
            UPDATE_CONFIG,
            {"guardrails": _guardrails(shape, _check(str(org_b_policy.id)))},
            format="json",
        )

        _assert_refused(response, str(org_b_policy.id))
        assert _versions(organization) == [(1, True)]
        assert bytes(org_b_policy.encrypted_check_configs) not in _decrypted_blobs(
            decrypt_spy
        )
        gateway.set_org_config.assert_not_called()

    @pytest.mark.parametrize(
        "kind", ["foreign", "held_foreign", "unknown", "deleted", "not_a_uuid"]
    )
    def test_every_policy_id_the_org_does_not_own_gets_one_answer(
        self, auth_client, organization, org_b_policy, gateway, kind
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
                UPDATE_CONFIG,
                {"guardrails": {"rules": [{**_check(policy_id), "enabled": False}]}},
                format="json",
            )

        _assert_refused(response, named_as)
        assert _active(organization).version == 1
        gateway.set_org_config.assert_not_called()
        rejected = [log for log in logs if log["event"] == "update_config_rejected"]
        assert rejected == [
            {
                "event": "update_config_rejected",
                "log_level": "warning",
                "org_id": str(organization.id),
                "code": CODE,
                "policy_ids": (named_as,),
            }
        ]

    @pytest.mark.parametrize("held", ["unknown", "deleted", "not_a_uuid"])
    def test_an_unknown_id_the_active_version_holds_is_accepted_and_inert(
        self, auth_client, organization, gateway, decrypt_spy, held
    ):
        # The console sends saved checks back whole, with their ids; an id the
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
        rule = {**_check(policy_id), "action": "warn"}

        response = auth_client.post(
            UPDATE_CONFIG, {"guardrails": {"rules": [rule]}}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        active = _active(organization)
        assert active.version == 2
        assert active.guardrails["rules"] == [rule]
        pushed = _pushed_check(_payload_sent_for(gateway, organization))
        assert "api_key" not in pushed["config"]
        assert OWN_SECRET not in json.dumps(pushed)
        assert decrypt_spy.call_count == 0

    def test_the_orgs_own_policy_is_accepted_and_injected(
        self, auth_client, organization, gateway
    ):
        own = _policy(
            organization, "own-guard", {"lakera-guard": {"api_key": OWN_SECRET}}
        )
        _config(organization, {})

        response = auth_client.post(
            UPDATE_CONFIG,
            {"guardrails": {"rules": [_check(str(own.id))]}},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _active(organization).guardrails["rules"][0]["_policy"] == str(own.id)
        pushed = _pushed_check(_payload_sent_for(gateway, organization))
        assert pushed["config"]["api_key"] == OWN_SECRET


@pytest.mark.integration
@pytest.mark.api
class TestUpdateGuardrailRefusesPolicyIdsTheOrgDoesNotOwn:
    @pytest.mark.parametrize("kind", ["foreign", "unknown"])
    @pytest.mark.parametrize("stored", ["rules", "checks_map"])
    def test_refused(
        self,
        auth_client,
        organization,
        org_b_policy,
        gateway,
        decrypt_spy,
        kind,
        stored,
    ):
        policy_id = str(org_b_policy.id) if kind == "foreign" else str(uuid.uuid4())
        clean = {
            "enabled": True,
            "action": "block",
            "config": {"words": ["bad"]},
        }
        _config(
            organization,
            {"rules": [{"name": "keyword-blocklist", **clean}]}
            if stored == "rules"
            else {"checks": {"keyword-blocklist": clean}},
        )
        rule = {key: value for key, value in _check(policy_id).items() if key != "name"}

        with capture_logs() as logs:
            response = auth_client.post(
                UPDATE_GUARDRAIL,
                {"name": "lakera-guard", "config": rule},
                format="json",
            )

        _assert_refused(response, policy_id)
        assert _versions(organization) == [(1, True)]
        assert bytes(org_b_policy.encrypted_check_configs) not in _decrypted_blobs(
            decrypt_spy
        )
        gateway.set_org_config.assert_not_called()
        assert [log for log in logs if log["event"] == "update_guardrail_rejected"] == [
            {
                "event": "update_guardrail_rejected",
                "log_level": "warning",
                "org_id": str(organization.id),
                "code": CODE,
                "policy_ids": (policy_id,),
            }
        ]

    def test_a_map_check_stored_with_a_foreign_id_is_refused_when_edited(
        self, auth_client, organization, org_b_policy, gateway, decrypt_spy
    ):
        # The edit keeps the map entry's stored `_policy`; another org's id is
        # refused even when the active version already holds it.
        _config(organization, _guardrails("checks_map", _check(str(org_b_policy.id))))

        response = auth_client.post(
            UPDATE_GUARDRAIL,
            {"name": "lakera-guard", "config": {"action": "warn"}},
            format="json",
        )

        _assert_refused(response, str(org_b_policy.id))
        assert _versions(organization) == [(1, True)]
        assert bytes(org_b_policy.encrypted_check_configs) not in _decrypted_blobs(
            decrypt_spy
        )
        gateway.set_org_config.assert_not_called()

    def test_the_orgs_own_policy_is_accepted_and_injected(
        self, auth_client, organization, gateway
    ):
        own = _policy(
            organization, "own-guard", {"lakera-guard": {"api_key": OWN_SECRET}}
        )
        _config(organization, {})
        rule = {
            key: value for key, value in _check(str(own.id)).items() if key != "name"
        }

        response = auth_client.post(
            UPDATE_GUARDRAIL,
            {"name": "lakera-guard", "config": rule},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        pushed = _pushed_check(_payload_sent_for(gateway, organization))
        assert pushed["config"]["api_key"] == OWN_SECRET


# Each shape POST org-configs takes a guardrails section in. The checks map and
# the rules list go through the console policy, which overwrites `_policy`;
# a checks list keeps the `_policy` it is sent.
POST_SHAPES = ["checks_map", "rules", "checks_list"]


@pytest.mark.integration
@pytest.mark.api
class TestPostOrgConfigsRefusesPolicyIdsTheOrgDoesNotOwn:
    @pytest.mark.parametrize("shape", POST_SHAPES)
    def test_foreign_policy_id_refused_as_sent(
        self, auth_client, organization, org_b_policy, gateway, decrypt_spy, shape
    ):
        _config(organization, {})

        with capture_logs() as logs:
            response = auth_client.post(
                ORG_CONFIGS,
                {"guardrails": _guardrails(shape, _check(str(org_b_policy.id)))},
                format="json",
            )

        _assert_refused(response, str(org_b_policy.id))
        assert _versions(organization) == [(1, True)]
        # Nothing was written for this org: no console policy either.
        assert not AgentccGuardrailPolicy.no_workspace_objects.filter(
            organization=organization
        ).exists()
        assert bytes(org_b_policy.encrypted_check_configs) not in _decrypted_blobs(
            decrypt_spy
        )
        gateway.set_org_config.assert_not_called()
        assert [
            log for log in logs if log["event"] == "org_config_create_rejected"
        ] == [
            {
                "event": "org_config_create_rejected",
                "log_level": "warning",
                "org_id": str(organization.id),
                "code": CODE,
                "policy_ids": (str(org_b_policy.id),),
            }
        ]

    @pytest.mark.parametrize("kind", ["foreign", "unknown"])
    def test_a_raw_post_cannot_store_an_id_for_update_config(
        self, auth_client, organization, org_b_policy, gateway, kind
    ):
        # A raw POST of a foreign or an unknown id is refused with one answer, so
        # no sequence of requests tells whether an id is a live policy.
        policy_id = str(org_b_policy.id) if kind == "foreign" else str(uuid.uuid4())
        _config(organization, {})
        check = _check(policy_id)

        planted = auth_client.post(
            ORG_CONFIGS, {"guardrails": {"checks": [check]}}, format="json"
        )
        probe = auth_client.post(
            UPDATE_CONFIG, {"guardrails": {"rules": [check]}}, format="json"
        )

        for response in (planted, probe):
            _assert_refused(response, policy_id)
        assert _versions(organization) == [(1, True)]
        assert policy_id not in json.dumps(_active(organization).guardrails)
        gateway.set_org_config.assert_not_called()

    def test_a_held_id_sent_back_whole_is_accepted(
        self, auth_client, organization, gateway
    ):
        # The Guardrails page posts the active checks back with the ids they
        # were saved with, including a policy deleted since.
        deleted = _policy(organization, "deleted-guard", deleted=True)
        stored = _guardrails("checks_list", _check(str(deleted.id)))
        _config(organization, stored)

        response = auth_client.post(
            ORG_CONFIGS,
            {"guardrails": stored, "routing": {"strategy": "round_robin"}},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _versions(organization) == [(1, False), (2, True)]
        pushed = _pushed_check(_payload_sent_for(gateway, organization))
        assert "api_key" not in pushed["config"]

    def test_the_orgs_own_policy_is_accepted_and_injected(
        self, auth_client, organization, gateway
    ):
        own = _policy(
            organization, "own-guard", {"lakera-guard": {"api_key": OWN_SECRET}}
        )
        _config(organization, {})

        response = auth_client.post(
            ORG_CONFIGS,
            {"guardrails": _guardrails("checks_list", _check(str(own.id)))},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        pushed = _pushed_check(_payload_sent_for(gateway, organization))
        assert pushed["config"]["api_key"] == OWN_SECRET
