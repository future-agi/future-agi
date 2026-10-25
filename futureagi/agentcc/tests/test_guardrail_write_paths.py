"""Every guardrail write path stores credentials encrypted and only names the
org's own policies, and every config write path takes the org lock first.
"""

import ast
import inspect
import json
import logging
import re
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from django.db import connection
from rest_framework import status
from structlog.testing import capture_logs

from accounts.models import Organization
from agentcc.models.guardrail_policy import AgentccGuardrailPolicy
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.models.provider_credential import AgentccProviderCredential
from agentcc.services import config_push, guardrail_credentials
from agentcc.views import gateway as gateway_views
from agentcc.views import org_config as org_config_views
from integrations.services.credentials import CredentialManager

GATEWAY = "/agentcc/gateways/default"
UPDATE_GUARDRAIL = f"{GATEWAY}/update-guardrail/"
UPDATE_CONFIG = f"{GATEWAY}/update-config/"
ORG_CONFIGS = "/agentcc/org-configs/"
SENTINEL = "__encrypted__"

# Test values, not real credentials.
NEW_SECRET = "clear-text-guard-key-test-value"
ORG_B_SECRET = "org-b-guard-key-test-value"
PROVIDER_KEY = "provider-key-test-value"

SERVICE_HELPERS = (
    "GuardrailPolicyNotInOrg",
    "GuardrailCredentialConflict",
    "GuardrailCredentialsUnreadable",
    "_guardrail_section_items",
    "_policy_key",
    "_reject_unowned_guardrail_policies",
    "_decrypted_policy_secrets",
    "_policy_secrets",
    "_new_guardrail_credentials",
    "_encrypt_new_guardrail_credentials",
    "_prepare_guardrails",
)
AGENTCC_DIR = Path(guardrail_credentials.__file__).resolve().parents[1]


def _console(org):
    return AgentccGuardrailPolicy.no_workspace_objects.get(
        organization=org,
        name=guardrail_credentials.CONSOLE_GUARDRAIL_POLICY,
        deleted=False,
    )


def _console_secrets(org):
    return CredentialManager.decrypt(bytes(_console(org).encrypted_check_configs))


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


def _lakera(policy_id=None, **config):
    check = {
        "name": "lakera-guard",
        "enabled": True,
        "action": "block",
        "config": {"api_key": NEW_SECRET, **config},
    }
    if policy_id is not None:
        check["_policy"] = policy_id
    return check


def _pii(policy_id=None):
    entry = {"enabled": True, "action": "mask"}
    if policy_id is not None:
        entry["_policy"] = policy_id
    return entry


@pytest.fixture
def org_b(db):
    return Organization.objects.create(name="Other organization")


@pytest.fixture
def org_b_policy(org_b):
    return AgentccGuardrailPolicy.no_workspace_objects.create(
        organization=org_b,
        name="org-b-guard",
        checks=[{"name": "lakera-guard"}],
        encrypted_check_configs=CredentialManager.encrypt(
            {"lakera-guard": {"api_key": ORG_B_SECRET}}
        ),
    )


@pytest.fixture
def gateway():
    """Every push goes to a mock that records the payload it was sent."""
    client = MagicMock()
    with (
        patch.object(config_push, "get_gateway_client", return_value=client),
        patch.object(gateway_views, "get_gateway_client", return_value=client),
    ):
        yield client


@pytest.fixture
def decrypt_spy():
    real_decrypt = CredentialManager.decrypt
    with patch.object(CredentialManager, "decrypt", side_effect=real_decrypt) as spy:
        yield spy


def _decrypted_blobs(spy):
    return [bytes(call.args[0]) for call in spy.call_args_list]


def _pushed_guardrails(gateway, org):
    sent = [
        call.args[1]
        for call in gateway.set_org_config.call_args_list
        if call.args[0] == str(org.id)
    ]
    return sent[-1]["guardrails"]


def _fragments(secret):
    # An error message can truncate a value in the middle (pydantic's
    # `input_value=...`), so its head and tail count as a leak too.
    return {secret, secret[:16], secret[-16:]}


def _assert_secret_nowhere(secret, response, caplog, org):
    """Not in the response, the stored versions or the logs. The logs are
    read from caplog, where structlog renders each event with its traceback
    (a capture_logs() block would swallow them unrendered)."""
    for fragment in _fragments(secret):
        assert fragment not in json.dumps(response.json())
        assert fragment not in caplog.text
        for guardrails in AgentccOrgConfig.all_objects.filter(
            organization=org
        ).values_list("guardrails", flat=True):
            assert fragment not in json.dumps(guardrails)


@pytest.mark.integration
class TestHelpersLiveInTheService:
    def test_helpers_live_in_guardrail_credentials_service(self):
        for name in SERVICE_HELPERS:
            helper = getattr(guardrail_credentials, name)
            assert helper.__module__ == "agentcc.services.guardrail_credentials"
            assert name not in _defined_names(gateway_views), name
        # The views route and delegate to the service's own functions.
        for view, names in (
            (
                gateway_views,
                (
                    "_prepare_guardrails",
                    "_reject_unowned_guardrail_policies",
                    "_encrypt_new_guardrail_credentials",
                ),
            ),
            (org_config_views, ("_prepare_guardrails",)),
        ):
            for name in names:
                assert getattr(view, name) is getattr(guardrail_credentials, name)
        assert org_config_views.CONSOLE_GUARDRAIL_POLICY == "__ui_guardrails__"
        # The name is spelled out once in the app's code (migrations and
        # tests aside), in the service.
        spelled = [
            path.relative_to(AGENTCC_DIR).as_posix()
            for path in AGENTCC_DIR.rglob("*.py")
            if "tests" not in path.parts and "migrations" not in path.parts
            for _ in range(path.read_text().count('"__ui_guardrails__"'))
        ]
        assert spelled == ["services/guardrail_credentials.py"]


def _defined_names(module):
    tree = ast.parse(inspect.getsource(module))
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.ClassDef))
    }


@pytest.mark.integration
@pytest.mark.api
class TestUpdateGuardrail:
    def test_update_guardrail_encrypts_new_credential(
        self, auth_client, organization, gateway, caplog
    ):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True
        )
        rule = _lakera()
        del rule["name"]

        caplog.set_level(logging.INFO)
        response = auth_client.post(
            UPDATE_GUARDRAIL,
            {"name": "lakera-guard", "config": rule},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        _assert_secret_nowhere(NEW_SECRET, response, caplog, organization)
        console = _console(organization)
        stored = _active(organization).guardrails["rules"]
        assert stored == [
            {
                "enabled": True,
                "action": "block",
                "config": {"api_key": SENTINEL},
                "_policy": str(console.id),
                "name": "lakera-guard",
            }
        ]
        assert _console_secrets(organization) == {
            "lakera-guard": {"api_key": NEW_SECRET}
        }
        # The gateway still gets the credential, from the console policy.
        pushed = _pushed_guardrails(gateway, organization)
        assert pushed["checks"]["lakera-guard"]["config"]["api_key"] == NEW_SECRET

    def test_update_guardrail_keeps_a_sent_back_sentinel(
        self, auth_client, organization, gateway
    ):
        # The console's edit dialog sends the stored rule back whole, with
        # its sentinel and `_policy`; nothing new is encrypted.
        console = AgentccGuardrailPolicy.no_workspace_objects.create(
            organization=organization,
            name=guardrail_credentials.CONSOLE_GUARDRAIL_POLICY,
            checks=[],
            encrypted_check_configs=CredentialManager.encrypt(
                {"lakera-guard": {"api_key": NEW_SECRET}}
            ),
        )
        stored_rule = _lakera(str(console.id), api_key=SENTINEL)
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization,
            version=1,
            is_active=True,
            guardrails={"rules": [stored_rule]},
        )

        response = auth_client.post(
            UPDATE_GUARDRAIL,
            {"name": "lakera-guard", "config": {**stored_rule, "action": "warn"}},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _active(organization).guardrails["rules"] == [
            {**stored_rule, "action": "warn"}
        ]
        assert _console_secrets(organization) == {
            "lakera-guard": {"api_key": NEW_SECRET}
        }

    @pytest.mark.parametrize("kind", ["foreign", "unknown"])
    def test_update_guardrail_rejects_foreign_policy_id(
        self, auth_client, organization, org_b_policy, gateway, decrypt_spy, kind
    ):
        policy_id = str(org_b_policy.id) if kind == "foreign" else str(uuid.uuid4())
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True
        )

        with capture_logs() as logs:
            response = auth_client.post(
                UPDATE_GUARDRAIL,
                {
                    "name": "lakera-guard",
                    "config": _lakera(policy_id, api_key=SENTINEL),
                },
                format="json",
            )

        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.json()
        assert response.json()["code"] == "guardrail_policy_not_in_org"
        assert response.json()["message"] == (
            f"Guardrail policy {policy_id} is not one of this organization's policies."
        )
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
                "code": "guardrail_policy_not_in_org",
                "policy_ids": (policy_id,),
            }
        ]


# Each raw POST shape a guardrails section can take, with the check under
# test outside the checks map.
RAW_SHAPES = {
    "rules_beside_checks_map": lambda check: {
        "checks": {"pii-detector": _pii()},
        "rules": [check],
    },
    "checks_list_beside_rules": lambda check: {
        "rules": [{"name": "pii-detector", "enabled": True, "action": "mask"}],
        "checks": [check],
    },
    "checks_list": lambda check: {"enabled": True, "checks": [check]},
}


def _stored_raw_check(guardrails):
    return guardrails["checks"][0]


@pytest.mark.integration
@pytest.mark.api
@patch(
    "agentcc.views.org_config.AgentccOrgConfigViewSet._push_config_to_gateway",
    return_value=True,
)
class TestPostOrgConfigsRawBranches:
    def test_post_org_configs_raw_rules_and_checks_dict_encrypted_and_scoped(
        self, push, auth_client, organization, org_b_policy, decrypt_spy, caplog
    ):
        # The gateway contract has no `rules` beside a checks map, so this
        # shape is never saved; the credential is encrypted before that check
        # runs, so the refusal (and its logged traceback) never carries it.
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True
        )
        shape = "rules_beside_checks_map"

        caplog.set_level(logging.INFO)
        response = auth_client.post(
            ORG_CONFIGS,
            {"guardrails": RAW_SHAPES[shape](_lakera())},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.json()
        assert "guardrails.rules" in response.json()["message"]
        _assert_secret_nowhere(NEW_SECRET, response, caplog, organization)
        assert _versions(organization) == [(1, True)]
        # The console policy write rolled back with the refused version.
        assert not AgentccGuardrailPolicy.no_workspace_objects.filter(
            organization=organization
        ).exists()
        push.assert_not_called()

        self._assert_scoped(
            push, auth_client, organization, org_b_policy, decrypt_spy, shape
        )

    @pytest.mark.parametrize("shape", ["checks_list", "checks_list_beside_rules"])
    def test_post_org_configs_checks_list_encrypted_and_scoped(
        self, push, auth_client, organization, org_b_policy, decrypt_spy, caplog, shape
    ):
        self._assert_encrypted(push, auth_client, organization, caplog, shape)
        self._assert_scoped(
            push, auth_client, organization, org_b_policy, decrypt_spy, shape
        )

    def test_processed_checks_map_routes_through_the_console_policy(
        self, push, auth_client, organization, caplog
    ):
        check = _lakera()
        del check["name"]

        caplog.set_level(logging.INFO)
        response = auth_client.post(
            ORG_CONFIGS,
            {"guardrails": {"checks": {"lakera-guard": check}}},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        _assert_secret_nowhere(NEW_SECRET, response, caplog, organization)
        stored = _active(organization).guardrails["checks"]["lakera-guard"]
        assert stored["config"] == {"api_key": SENTINEL}
        assert stored["_policy"] == str(_console(organization).id)
        assert _console_secrets(organization) == {
            "lakera-guard": {"api_key": NEW_SECRET}
        }

    def test_processed_checks_map_refuses_a_foreign_policy_id(
        self, push, auth_client, organization, org_b_policy, decrypt_spy
    ):
        response = auth_client.post(
            ORG_CONFIGS,
            {"guardrails": {"checks": {"pii-detector": _pii(str(org_b_policy.id))}}},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.json()
        assert response.json()["code"] == "guardrail_policy_not_in_org"
        assert not AgentccOrgConfig.no_workspace_objects.filter(
            organization=organization
        ).exists()
        # The console policy upsert rolled back with the refused version.
        assert not AgentccGuardrailPolicy.no_workspace_objects.filter(
            organization=organization
        ).exists()
        assert bytes(org_b_policy.encrypted_check_configs) not in _decrypted_blobs(
            decrypt_spy
        )
        push.assert_not_called()

    @staticmethod
    def _assert_encrypted(push, auth_client, organization, caplog, shape):
        caplog.set_level(logging.INFO)
        response = auth_client.post(
            ORG_CONFIGS,
            {"guardrails": RAW_SHAPES[shape](_lakera())},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        _assert_secret_nowhere(NEW_SECRET, response, caplog, organization)
        stored = _stored_raw_check(_active(organization).guardrails)
        assert stored["config"] == {"api_key": SENTINEL}
        assert stored["_policy"] == str(_console(organization).id)
        assert _console_secrets(organization)["lakera-guard"] == {"api_key": NEW_SECRET}
        push.reset_mock()

    @staticmethod
    def _assert_scoped(
        push, auth_client, organization, org_b_policy, decrypt_spy, shape
    ):
        before = _versions(organization)

        response = auth_client.post(
            ORG_CONFIGS,
            {
                "guardrails": RAW_SHAPES[shape](
                    _lakera(str(org_b_policy.id), api_key=SENTINEL)
                )
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.json()
        assert response.json()["code"] == "guardrail_policy_not_in_org"
        assert str(org_b_policy.id) in response.json()["message"]
        assert _versions(organization) == before
        assert bytes(org_b_policy.encrypted_check_configs) not in _decrypted_blobs(
            decrypt_spy
        )
        push.assert_not_called()


@pytest.mark.integration
@pytest.mark.api
@patch("agentcc.views.gateway.push_org_config", return_value=True)
@patch(
    "agentcc.views.org_config.AgentccOrgConfigViewSet._push_config_to_gateway",
    return_value=True,
)
class TestRawPostCannotPlantHeldId:
    @pytest.mark.parametrize("kind", ["foreign", "unknown"])
    @pytest.mark.parametrize("shape", sorted(RAW_SHAPES))
    def test_raw_post_cannot_plant_held_id(
        self,
        _post_push,
        _update_push,
        auth_client,
        organization,
        org_b_policy,
        kind,
        shape,
    ):
        # Both requests give one answer for a foreign id and an unknown id.
        policy_id = str(org_b_policy.id) if kind == "foreign" else str(uuid.uuid4())
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True
        )
        check = _lakera(policy_id, api_key=SENTINEL)

        planted = auth_client.post(
            ORG_CONFIGS, {"guardrails": RAW_SHAPES[shape](check)}, format="json"
        )
        probe = auth_client.post(
            UPDATE_CONFIG, {"guardrails": {"rules": [check]}}, format="json"
        )

        refusal = (
            f"Guardrail policy {policy_id} is not one of this organization's policies."
        )
        for response in (planted, probe):
            assert response.status_code == status.HTTP_400_BAD_REQUEST, response.json()
            assert response.json()["code"] == "guardrail_policy_not_in_org"
            assert response.json()["message"] == refusal
        assert _versions(organization) == [(1, True)]
        assert policy_id not in json.dumps(_active(organization).guardrails)


# --- Org lock first -------------------------------------------------------

_ORG_LOCK = re.compile(
    r'^SELECT .* FROM "accounts_organization" .*FOR (NO KEY )?UPDATE', re.S
)
_CONFIG_ROW_LOCK = re.compile(
    r'^(SELECT .* FROM "agentcc_(org_config|guardrail_policy)" .*FOR (NO KEY )?UPDATE'
    r'|(UPDATE|INSERT INTO|DELETE FROM) "agentcc_(org_config|guardrail_policy)")',
    re.S,
)


class _TransactionRecorder:
    """Groups the SQL a request runs by the transaction it runs in: the
    outermost atomic block, or None for autocommit."""

    def __init__(self):
        self.transactions = []  # [(outermost atomic or None, [sql, ...])]

    def __call__(self, execute, sql, params, many, context):
        outer = connection.atomic_blocks[0] if connection.in_atomic_block else None
        if (
            outer is None
            or not self.transactions
            or self.transactions[-1][0] is not outer
        ):
            self.transactions.append((outer, []))
        self.transactions[-1][1].append(sql)
        return execute(sql, params, many, context)

    def violations(self):
        """Config-row locks and writes taken before the org lock in their
        transaction, or outside any transaction."""
        found = []
        for outer, statements in self.transactions:
            org_locked = False
            for sql in statements:
                if outer is not None and _ORG_LOCK.match(sql):
                    org_locked = True
                elif _CONFIG_ROW_LOCK.match(sql) and not org_locked:
                    found.append(sql.split(" WHERE ")[0][:160])
        return found


def _setup_active(org, **sections):
    return AgentccOrgConfig.no_workspace_objects.create(
        organization=org, version=1, is_active=True, **sections
    )


def _setup_inactive(org):
    _setup_active(org)
    return AgentccOrgConfig.no_workspace_objects.create(
        organization=org, version=2, is_active=False
    )


def _setup_credential(org):
    return AgentccProviderCredential.no_workspace_objects.create(
        organization=org,
        provider_name="openai",
        display_name="OpenAI",
        encrypted_credentials=CredentialManager.encrypt({"api_key": PROVIDER_KEY}),
        api_format="openai",
    )


def _setup_mcp(org):
    _setup_active(org, mcp={"servers": {"docs": {"url": "https://mcp.example.com"}}})


# name -> (setup(org) -> object or None, method, url template, body). The url
# template is filled with the setup's object id. Setups that create no active
# config exercise the paths that create version 1 themselves.
WRITE_PATHS = {
    "org-config-post-section": (
        _setup_active,
        "post",
        ORG_CONFIGS,
        {"cache": {"enabled": True}},
    ),
    "org-config-post-checks-map": (
        _setup_active,
        "post",
        ORG_CONFIGS,
        {
            "guardrails": {
                "checks": {"lakera-guard": {"config": {"api_key": NEW_SECRET}}}
            }
        },
    ),
    **{
        f"org-config-post-{shape}": (
            _setup_active,
            "post",
            ORG_CONFIGS,
            {"guardrails": build(_lakera())},
        )
        for shape, build in RAW_SHAPES.items()
        # The gateway contract refuses that shape, so it never writes.
        if shape != "rules_beside_checks_map"
    },
    "org-config-activate": (
        _setup_inactive,
        "post",
        f"{ORG_CONFIGS}{{id}}/activate/",
        {},
    ),
    "org-config-delete": (_setup_inactive, "delete", f"{ORG_CONFIGS}{{id}}/", None),
    "update-config": (
        _setup_active,
        "post",
        UPDATE_CONFIG,
        {"cache": {"enabled": True}},
    ),
    "update-config-guardrail-credential": (
        _setup_active,
        "post",
        UPDATE_CONFIG,
        {"guardrails": {"rules": [_lakera()]}},
    ),
    "update-guardrail": (
        _setup_active,
        "post",
        UPDATE_GUARDRAIL,
        {"name": "lakera-guard", "config": {"config": {"api_key": NEW_SECRET}}},
    ),
    "toggle-guardrail": (
        _setup_active,
        "post",
        f"{GATEWAY}/toggle-guardrail/",
        {"name": "pii-detector", "enabled": True},
    ),
    "set-budget": (
        _setup_active,
        "post",
        f"{GATEWAY}/set-budget/",
        {"level": "org_limit", "config": {"limit": 100, "on_exceed": "warn"}},
    ),
    "remove-budget": (
        lambda org: _setup_active(org, budgets={"org_limit": {"limit": 100}}),
        "post",
        f"{GATEWAY}/remove-budget/",
        {"level": "org_limit"},
    ),
    "update-mcp-server": (
        _setup_active,
        "post",
        f"{GATEWAY}/update-mcp-server/",
        {"server_id": "docs", "config": {"url": "https://mcp.example.com"}},
    ),
    "remove-mcp-server": (
        _setup_mcp,
        "post",
        f"{GATEWAY}/remove-mcp-server/",
        {"server_id": "docs"},
    ),
    "update-mcp-guardrails": (
        _setup_active,
        "post",
        f"{GATEWAY}/update-mcp-guardrails/",
        {"config": {"enabled": True}},
    ),
    "reload-without-config": (lambda org: None, "post", f"{GATEWAY}/reload/", {}),
    "update-provider-without-config": (
        lambda org: None,
        "post",
        f"{GATEWAY}/update-provider/",
        {"name": "anthropic", "config": {"api_key": PROVIDER_KEY}},
    ),
    "remove-provider-without-config": (
        _setup_credential,
        "post",
        f"{GATEWAY}/remove-provider/",
        {"name": "openai"},
    ),
    "credential-create-without-config": (
        lambda org: None,
        "post",
        "/agentcc/provider-credentials/",
        {
            "provider_name": "anthropic",
            "display_name": "Anthropic",
            "credentials": {"api_key": PROVIDER_KEY},
            "api_format": "anthropic",
        },
    ),
    "credential-rotate-without-config": (
        _setup_credential,
        "post",
        "/agentcc/provider-credentials/{id}/rotate/",
        {"credentials": {"api_key": PROVIDER_KEY}},
    ),
    "credential-update-without-config": (
        _setup_credential,
        "patch",
        "/agentcc/provider-credentials/{id}/",
        {"display_name": "Renamed"},
    ),
    "credential-delete-without-config": (
        _setup_credential,
        "delete",
        "/agentcc/provider-credentials/{id}/",
        None,
    ),
}


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db(transaction=True)
class TestOrgLockFirst:
    @pytest.mark.parametrize("path", sorted(WRITE_PATHS))
    def test_every_config_write_path_takes_org_lock_first(
        self, auth_client, organization, gateway, path
    ):
        setup, method, url, body = WRITE_PATHS[path]
        target = setup(organization)
        url = url.format(id=getattr(target, "pk", ""))
        recorder = _TransactionRecorder()

        with connection.execute_wrapper(recorder):
            if body is None:
                response = getattr(auth_client, method)(url)
            else:
                response = getattr(auth_client, method)(url, body, format="json")

        assert response.status_code in (200, 201), response.json()
        assert any(
            _CONFIG_ROW_LOCK.match(sql)
            for _, statements in recorder.transactions
            for sql in statements
        ), "the path wrote no config row; the test checks nothing"
        assert recorder.violations() == []
