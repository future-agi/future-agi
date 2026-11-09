"""AM-C20 item 2 and CN7 (the CW1 merge): the gateway places a pushed MCP
credential secret with a credential template, in a header, a query parameter
or a secret URL variable of the server's url, in one part or several, and a
managed tool credential in a header only. Django's push applies the same rule
(mcp_credential_placement, held to the gateway's by the shared placement
vectors): a secret the gateway can place is pushed whole; one it cannot is
refused at push, the write that pushes it answers 400 naming the field and is
rolled back, and every other payload (the gateway's bulk sync) leaves the
secret out. It is never sent elsewhere or in part.

These tests fake the vault's assembly with what a push form carries, so they
cover placements no vault writer stores yet (parts, R5)."""

import copy
import json
from unittest.mock import MagicMock, patch

import pytest
from structlog.testing import capture_logs

from agentcc.models.mcp_credential import AgentccMCPCredential
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services.config_push import (
    OrgConfigRejected,
    _build_payload,
    push_org_config,
)

GATEWAY_CLIENT = "agentcc.services.config_push.get_gateway_client"
ASSEMBLE = "agentcc.services.mcp_credentials.assemble_for_push"
GATEWAY = "/agentcc/gateways/default"
CREDENTIALS = "/agentcc/mcp/credentials/"
CODE = "mcp_credential_placement_unsupported"
VALUE = "test-placement-secret-not-real"
GITHUB_URL = "https://mcp.github.example.com/mcp"

HOOKS_URL = "https://hooks.example.com/mcp/{token}"

STORED_MCP = {
    "enabled": True,
    "servers": {
        "github": {
            "url": GITHUB_URL,
            "transport": "http",
            "credential": {
                "mode": "shared",
                "credential_id": "cred_7d1e",
                "state": "active",
            },
        },
        "docs": {
            "url": "https://docs.example.com/mcp",
            "credential": {
                "mode": "shared",
                "credential_id": "cred_d0c5",
                "header": "X-Api-Key",
                "state": "active",
            },
        },
        "hooks": {
            "url": HOOKS_URL,
            "credential": {
                "mode": "shared",
                "credential_id": "cred_400c",
                "state": "active",
            },
        },
    },
    "managed_tools": {
        "enabled": True,
        "credentials": {
            "fagi.web": {
                "mode": "shared",
                "credential_id": "cred_2",
                "state": "active",
            }
        },
    },
}

# (assembled secrets, the field the refusal names): placements the contract
# admits but the gateway cannot apply exactly as stated, so the push refuses
# them (an ``in`` outside the contract's enum never reaches the rule: the
# contract check drops it first, mcp_credential_dropped).
REFUSED = {
    "a-header-placement-on-a-query-name": (
        {
            "github": {
                "type": "api_key",
                "in": "query",
                "name": "api_key",
                "header": "X-Key",
            }
        },
        "mcp.servers.github.credential.secret.header",
    ),
    "a-bearer-in-a-query-parameter": (
        {"github": {"type": "bearer", "in": "query", "name": "api_key"}},
        "mcp.servers.github.credential.secret.type",
    ),
    "a-url-variable-the-url-does-not-hold": (
        {"github": {"type": "api_key", "in": "url_variable", "name": "token"}},
        "mcp.servers.github.credential.secret.name",
    ),
    "a-reserved-header": (
        {"github": {"type": "api_key", "in": "header", "name": "Traceparent"}},
        "mcp.servers.github.credential.secret.name",
    ),
    "a-part-without-a-valid-name": (
        {
            "github": {
                "type": "api_key",
                "in": "header",
                "name": "X-Api-Key",
                "parts": [{"in": "query", "name": "a b", "value": VALUE}],
            }
        },
        "mcp.servers.github.credential.secret.parts[0].name",
    ),
    "name-is-not-the-header": (
        {
            "github": {
                "type": "api_key",
                "in": "header",
                "name": "X-Api-Key",
                "header": "X-Other",
            }
        },
        "mcp.servers.github.credential.secret.header",
    ),
    "bearer-in-a-named-header": (
        {
            "github": {
                "type": "bearer",
                "in": "header",
                "name": "X-Api-Key",
                "header": "X-Api-Key",
            }
        },
        "mcp.servers.github.credential.secret.name",
    ),
    "name-without-in": (
        {"github": {"type": "api_key", "name": "X-Api-Key", "header": "X-Other"}},
        "mcp.servers.github.credential.secret.name",
    ),
    "managed": (
        {"fagi.web": {"type": "api_key", "in": "query", "name": "key"}},
        "mcp.managed_tools.credentials.fagi.web.secret.in",
    ),
}

# Placements the gateway applies as stated: they push unchanged, on github
# (no credential header of its own).
ACCEPTED = {
    "push-form-today": {"type": "bearer", "header": "Authorization"},
    "api-key-header": {"type": "api_key", "header": "x-api-key"},
    "in-header-name-equal": {
        "type": "api_key",
        "header": "x-api-key",
        "in": "header",
        "name": "X-API-Key",
    },
    "in-header-no-name": {"type": "api_key", "header": "x-api-key", "in": "header"},
    "bearer-named-authorization": {
        "type": "bearer",
        "in": "header",
        "name": "authorization",
    },
    "in-header-with-a-name-and-no-header": {
        "type": "api_key",
        "in": "header",
        "name": "X-Api-Key",
    },
    "query": {"type": "api_key", "in": "query", "name": "api_key"},
    "parts": {
        "type": "api_key",
        "header": "DD-API-KEY",
        "in": "header",
        "name": "DD-API-KEY",
        "parts": [{"in": "header", "name": "DD-APPLICATION-KEY", "value": VALUE}],
    },
}


def _with_values(assembled):
    return {
        server_id: {**secret, "value": VALUE} for server_id, secret in assembled.items()
    }


@pytest.fixture
def org_config(user):
    return AgentccOrgConfig.no_workspace_objects.create(
        organization=user.organization,
        version=2,
        is_active=True,
        mcp=copy.deepcopy(STORED_MCP),
    )


@pytest.mark.integration
class TestPushRefusesUnappliedPlacement:
    @pytest.mark.parametrize("assembled,field", REFUSED.values(), ids=REFUSED.keys())
    def test_a_push_is_refused_naming_the_field(self, org_config, assembled, field):
        client = MagicMock()
        with (
            patch(ASSEMBLE, MagicMock(return_value=_with_values(assembled))),
            patch(GATEWAY_CLIENT, return_value=client),
            pytest.raises(OrgConfigRejected) as refused,
        ):
            push_org_config(str(org_config.organization_id), org_config)

        assert refused.value.code == CODE
        assert refused.value.param == field
        assert field in str(refused.value)
        assert VALUE not in str(refused.value)
        client.set_org_config.assert_not_called()

    @pytest.mark.parametrize("secret", ACCEPTED.values(), ids=ACCEPTED.keys())
    def test_an_applied_placement_pushes_unchanged(self, org_config, secret):
        client = MagicMock()
        secret = {**secret, "value": VALUE}
        with (
            patch(ASSEMBLE, MagicMock(return_value={"github": dict(secret)})),
            patch(GATEWAY_CLIENT, return_value=client),
            capture_logs() as logs,
        ):
            assert push_org_config(str(org_config.organization_id), org_config)

        payload = client.set_org_config.call_args.args[1]
        assert payload["mcp"]["servers"]["github"]["credential"]["secret"] == secret
        assert not [log for log in logs if log["event"] == "mcp_credential_dropped"]

    def test_a_url_variable_pushes_where_the_url_holds_it(self, org_config):
        """A secret URL variable fills the {token} of its server's url
        (hooks): the gateway renders it, so the push carries it whole and the
        stored url is never rewritten."""
        client = MagicMock()
        secret = {
            "type": "api_key",
            "in": "url_variable",
            "name": "token",
            "value": VALUE,
        }
        with (
            patch(ASSEMBLE, MagicMock(return_value={"hooks": dict(secret)})),
            patch(GATEWAY_CLIENT, return_value=client),
        ):
            assert push_org_config(str(org_config.organization_id), org_config)

        server = client.set_org_config.call_args.args[1]["mcp"]["servers"]["hooks"]
        assert server["credential"]["secret"] == secret
        assert server["url"] == HOOKS_URL

    def test_a_placeholder_nothing_fills_is_refused_naming_the_url(self, org_config):
        """hooks's url holds {token}; a query key there leaves it unfilled,
        which the gateway refuses naming the server's url."""
        client = MagicMock()
        secret = {"type": "api_key", "in": "query", "name": "k", "value": VALUE}
        with (
            patch(ASSEMBLE, MagicMock(return_value={"hooks": secret})),
            patch(GATEWAY_CLIENT, return_value=client),
            pytest.raises(OrgConfigRejected) as refused,
        ):
            push_org_config(str(org_config.organization_id), org_config)

        assert refused.value.param == "mcp.servers.hooks.url"
        assert VALUE not in str(refused.value)
        client.set_org_config.assert_not_called()

    def test_a_name_equal_to_the_credential_header_pushes(self, org_config):
        """The gateway sends a secret with no header of its own in the server
        credential's header (docs: X-Api-Key)."""
        client = MagicMock()
        secret = {
            "type": "api_key",
            "in": "header",
            "name": "x-api-key",
            "value": VALUE,
        }
        with (
            patch(ASSEMBLE, MagicMock(return_value={"docs": dict(secret)})),
            patch(GATEWAY_CLIENT, return_value=client),
        ):
            assert push_org_config(str(org_config.organization_id), org_config)

        payload = client.set_org_config.call_args.args[1]
        assert payload["mcp"]["servers"]["docs"]["credential"]["secret"] == secret

    @pytest.mark.parametrize("assembled,field", REFUSED.values(), ids=REFUSED.keys())
    def test_the_bulk_payload_leaves_the_secret_out(self, org_config, assembled, field):
        """The gateway's bulk sync builds every org's payload: an org there is
        never dropped for a credential (a missing org loses its config at the
        gateway), so the secret is left out and logged by field only."""
        with (
            patch(ASSEMBLE, MagicMock(return_value=_with_values(assembled))),
            capture_logs() as logs,
        ):
            payload = _build_payload(str(org_config.organization_id), org_config)

        assert VALUE not in json.dumps(payload)
        assert payload["mcp"]["servers"]["github"]["credential"]["mode"] == "shared"
        dropped = [log for log in logs if log["event"] == "mcp_credential_dropped"]
        assert len(dropped) == 1
        assert dropped[0]["reason"].startswith(f"{field}: ")
        assert VALUE not in json.dumps(logs, default=str)


def _seed(org):
    return AgentccOrgConfig.no_workspace_objects.create(
        organization=org,
        version=1,
        is_active=True,
        mcp={"servers": {"github": {"url": GITHUB_URL, "transport": "http"}}},
    )


def _versions(org):
    return AgentccOrgConfig._base_manager.filter(organization=org).count()


@pytest.fixture
def pushes():
    """Every org config the gateway was sent, as (org_id, payload)."""
    client = MagicMock()
    sent = []
    client.set_org_config.side_effect = lambda org_id, payload: sent.append(
        (org_id, copy.deepcopy(payload))
    )
    with patch(GATEWAY_CLIENT, return_value=client):
        yield sent


REFUSED_SECRET = {"github": {"type": "bearer", "in": "query", "name": "api_key"}}
REFUSED_FIELD = "mcp.servers.github.credential.secret.type"


def _assert_refused(response, field=REFUSED_FIELD):
    body = response.json()
    assert response.status_code == 400, body
    assert body["code"] == CODE
    assert body["param"] == field
    assert field in body["message"]
    assert VALUE not in json.dumps(body)


@pytest.mark.integration
@pytest.mark.api
class TestTheWriteThatPushesItAnswers400:
    """The writes that put a shared MCP credential into a push: the vault's
    create and rotate, and the console's update-mcp-server."""

    def test_vault_create(self, auth_client, user, pushes):
        org = user.organization
        _seed(org)
        before = _versions(org)

        with patch(ASSEMBLE, MagicMock(return_value=_with_values(REFUSED_SECRET))):
            response = auth_client.post(
                CREDENTIALS,
                {"server_id": "github", "kind": "bearer", "value": VALUE},
                format="json",
            )

        _assert_refused(response)
        assert not AgentccMCPCredential.objects.filter(organization=org).exists()
        assert _versions(org) == before
        assert pushes == []

    def test_vault_rotate(self, auth_client, user, pushes):
        org = user.organization
        _seed(org)
        created = auth_client.post(
            CREDENTIALS,
            {"server_id": "github", "kind": "bearer", "value": "test-first-not-real"},
            format="json",
        )
        assert created.status_code == 201, created.json()
        credential_id = created.json()["result"]["id"]
        pushed = len(pushes)

        with patch(ASSEMBLE, MagicMock(return_value=_with_values(REFUSED_SECRET))):
            response = auth_client.post(
                f"{CREDENTIALS}{credential_id}/rotate/",
                {"value": VALUE},
                format="json",
            )

        _assert_refused(response)
        row = AgentccMCPCredential.objects.get(pk=credential_id)
        assert row.version == 1 and row.rotated_at is None
        assert len(pushes) == pushed

    def test_update_mcp_server(self, auth_client, user, pushes):
        org = user.organization
        _seed(org)
        before = _versions(org)

        with patch(ASSEMBLE, MagicMock(return_value=_with_values(REFUSED_SECRET))):
            response = auth_client.post(
                f"{GATEWAY}/update-mcp-server/",
                {
                    "server_id": "github",
                    "config": {
                        "url": GITHUB_URL,
                        "transport": "http",
                        "auth": {"type": "bearer", "token": VALUE},
                    },
                },
                format="json",
            )

        _assert_refused(response)
        assert not AgentccMCPCredential.objects.filter(organization=org).exists()
        assert _versions(org) == before
        assert pushes == []


# --- AM-C22 (the CW2 preamble): the catalog's rules, applied by Django --------
#
# A server added from the catalog (its catalog_ref) is placed by the parts its
# provider declares in CN6's Django copy, the bundle the gateway embeds: close
# /api-key@1.0.0 declares the secret Close-API-Key and the constant Close-Scope
# (mcp.read), which the gateway adds and Django never pushes. These are the CW1
# review's four probe cases (P2), and a part close does not declare: Django
# refuses each at push naming the field the gateway's admin PUT names, so the
# write that pushes it answers 400 and rolls back, and the bulk sync leaves the
# secret out. Nothing is faked but the gateway client (and, here, the vault's
# assembly); the catalog is the real copy.

CLOSE_URL = "https://mcp.close.example.com/mcp"
CLOSE_REF = "close/api-key@1.0.0"
CLOSE_KEY = {"type": "api_key", "header": "Close-API-Key"}

CATALOG_REFUSED = {
    "a-provider-the-catalog-does-not-have": (
        "nope-app/api-key@1.0.0",
        CLOSE_KEY,
        "mcp.servers.close.catalog_ref",
    ),
    "a-bearer-token-for-close": (
        CLOSE_REF,
        {"type": "bearer", "header": "Authorization"},
        "mcp.servers.close.credential.secret.parts",
    ),
    "another-major-version": (
        "close/api-key@9.0.0",
        CLOSE_KEY,
        "mcp.servers.close.catalog_ref",
    ),
    "the-close-scope-constant-pushed": (
        CLOSE_REF,
        {
            "type": "api_key",
            "in": "header",
            "name": "Close-API-Key",
            "parts": [{"in": "header", "name": "Close-Scope", "value": "mcp.read"}],
        },
        "mcp.servers.close.credential.secret.parts[0].name",
    ),
    "a-part-close-does-not-declare": (
        CLOSE_REF,
        {**CLOSE_KEY, "parts": [{"in": "header", "name": "X-Extra", "value": VALUE}]},
        "mcp.servers.close.credential.secret.parts[0].name",
    ),
}


def _close_server(catalog_ref, *, key="catalog_ref"):
    return {
        "url": CLOSE_URL,
        "transport": "http",
        key: catalog_ref,
        "credential": {
            "mode": "shared",
            "credential_id": "cred_c105",
            "state": "active",
        },
    }


def _close_config(user, catalog_ref, *, key="catalog_ref"):
    mcp = copy.deepcopy(STORED_MCP)
    mcp["servers"]["close"] = _close_server(catalog_ref, key=key)
    return AgentccOrgConfig.no_workspace_objects.create(
        organization=user.organization, version=2, is_active=True, mcp=mcp
    )


@pytest.mark.integration
class TestPushAppliesTheCatalog:
    @pytest.mark.parametrize(
        "catalog_ref,secret,field",
        CATALOG_REFUSED.values(),
        ids=CATALOG_REFUSED.keys(),
    )
    def test_a_catalog_key_the_gateway_would_refuse_is_refused_at_push(
        self, user, catalog_ref, secret, field
    ):
        config = _close_config(user, catalog_ref)
        client = MagicMock()
        with (
            patch(
                ASSEMBLE, MagicMock(return_value={"close": {**secret, "value": VALUE}})
            ),
            patch(GATEWAY_CLIENT, return_value=client),
            pytest.raises(OrgConfigRejected) as refused,
        ):
            push_org_config(str(config.organization_id), config)

        assert refused.value.code == CODE
        assert refused.value.param == field
        assert VALUE not in str(refused.value)
        client.set_org_config.assert_not_called()

    def test_a_camel_spelled_catalog_ref_is_read(self, user):
        """The contract reads catalogRef too, and the push sends it as
        catalog_ref, so Django checks the spelling the config was saved with."""
        config = _close_config(user, "nope-app/api-key@1.0.0", key="catalogRef")
        client = MagicMock()
        with (
            patch(
                ASSEMBLE,
                MagicMock(return_value={"close": {**CLOSE_KEY, "value": VALUE}}),
            ),
            patch(GATEWAY_CLIENT, return_value=client),
            pytest.raises(OrgConfigRejected) as refused,
        ):
            push_org_config(str(config.organization_id), config)

        assert refused.value.param == "mcp.servers.close.catalog_ref"
        client.set_org_config.assert_not_called()

    @pytest.mark.parametrize("catalog_ref", [CLOSE_REF, "close/api-key@1.4.2"])
    def test_the_close_key_pushes_without_its_constant(self, user, catalog_ref):
        """The key the provider declares, in the major version the catalog
        has, pushes as assembled: Close-Scope is never pushed (the gateway
        adds it from the catalog it embeds)."""
        config = _close_config(user, catalog_ref)
        client = MagicMock()
        secret = {**CLOSE_KEY, "value": VALUE}
        with (
            patch(ASSEMBLE, MagicMock(return_value={"close": dict(secret)})),
            patch(GATEWAY_CLIENT, return_value=client),
        ):
            assert push_org_config(str(config.organization_id), config)

        server = client.set_org_config.call_args.args[1]["mcp"]["servers"]["close"]
        assert server["catalog_ref"] == catalog_ref
        assert server["credential"]["secret"] == secret
        assert "Close-Scope" not in json.dumps(server)

    @pytest.mark.parametrize(
        "catalog_ref,secret,field",
        CATALOG_REFUSED.values(),
        ids=CATALOG_REFUSED.keys(),
    )
    def test_the_bulk_payload_leaves_a_refused_catalog_key_out(
        self, user, catalog_ref, secret, field
    ):
        """The gateway's bulk sync never loads a key its pool would refuse
        (which would take the org's whole MCP section down after a restart):
        the secret is left out and logged by field only."""
        config = _close_config(user, catalog_ref)
        with (
            patch(
                ASSEMBLE, MagicMock(return_value={"close": {**secret, "value": VALUE}})
            ),
            capture_logs() as logs,
        ):
            payload = _build_payload(str(config.organization_id), config)

        server = payload["mcp"]["servers"]["close"]
        assert "secret" not in server["credential"]
        assert VALUE not in json.dumps(payload)
        dropped = [log for log in logs if log["event"] == "mcp_credential_dropped"]
        assert [log["reason"].split(": ")[0] for log in dropped] == [field]
        assert VALUE not in json.dumps(logs, default=str)


def _seed_close(org, catalog_ref=CLOSE_REF):
    return AgentccOrgConfig.no_workspace_objects.create(
        organization=org,
        version=1,
        is_active=True,
        mcp={
            "servers": {
                "close": {
                    "url": CLOSE_URL,
                    "transport": "http",
                    "catalog_ref": catalog_ref,
                }
            }
        },
    )


def _store_close_key(auth_client):
    created = auth_client.post(
        CREDENTIALS,
        {
            "server_id": "close",
            "kind": "api_key",
            "header_name": "Close-API-Key",
            "value": "test-close-first-not-real",
        },
        format="json",
    )
    assert created.status_code == 201, created.json()
    return created.json()["result"]["id"]


def _active(org):
    return AgentccOrgConfig.no_workspace_objects.get(organization=org, is_active=True)


@pytest.mark.integration
@pytest.mark.api
class TestAWriteThatPushesARefusedCatalogKeyAnswers400:
    """Every write that pushes a catalog server's key the gateway would refuse
    answers 400 naming the field and rolls back (AM-C22: a refused config
    never becomes the active one): the vault's create and rotate, and the
    generic writers (update-config, POST org-configs/ and activate). The
    vault assembles the real stored key; the catalog is the real copy."""

    def test_vault_create_of_the_close_key_pushes(self, auth_client, user, pushes):
        _seed_close(user.organization)
        _store_close_key(auth_client)

        server = pushes[-1][1]["mcp"]["servers"]["close"]
        assert server["credential"]["secret"]["header"] == "Close-API-Key"
        assert server["catalog_ref"] == CLOSE_REF

    @pytest.mark.parametrize(
        "catalog_ref,body,field",
        [
            (
                "close/api-key@9.0.0",
                {"kind": "api_key", "header_name": "Close-API-Key"},
                "mcp.servers.close.catalog_ref",
            ),
            (
                "nope-app/api-key@1.0.0",
                {"kind": "api_key", "header_name": "Close-API-Key"},
                "mcp.servers.close.catalog_ref",
            ),
            (
                CLOSE_REF,
                {"kind": "bearer"},
                "mcp.servers.close.credential.secret.parts",
            ),
            (
                CLOSE_REF,
                {"kind": "api_key", "header_name": "X-Close-Key"},
                "mcp.servers.close.credential.secret.parts",
            ),
        ],
        ids=["another-major", "unknown-provider", "bearer", "mistyped-header"],
    )
    def test_vault_create_of_a_refused_catalog_key(
        self, auth_client, user, pushes, catalog_ref, body, field
    ):
        org = user.organization
        _seed_close(org, catalog_ref)
        before = _versions(org)

        response = auth_client.post(
            CREDENTIALS, {"server_id": "close", **body, "value": VALUE}, format="json"
        )

        _assert_refused(response, field)
        assert not AgentccMCPCredential.objects.filter(organization=org).exists()
        assert _versions(org) == before
        assert pushes == []

    def test_vault_rotate_into_another_header(self, auth_client, user, pushes):
        org = user.organization
        _seed_close(org)
        credential_id = _store_close_key(auth_client)
        pushed = len(pushes)

        response = auth_client.post(
            f"{CREDENTIALS}{credential_id}/rotate/",
            {"value": VALUE, "header_name": "X-Close-Key"},
            format="json",
        )

        _assert_refused(response, "mcp.servers.close.credential.secret.parts")
        row = AgentccMCPCredential.objects.get(pk=credential_id)
        assert row.version == 1 and row.rotated_at is None
        assert len(pushes) == pushed

    def test_update_config_of_a_refused_catalog_ref(self, auth_client, user, pushes):
        org = user.organization
        _seed_close(org)
        _store_close_key(auth_client)
        before, pushed = _versions(org), len(pushes)

        response = auth_client.post(
            f"{GATEWAY}/update-config/",
            {"mcp": {"servers": {"close": {"catalog_ref": "close/api-key@9.0.0"}}}},
            format="json",
        )

        _assert_refused(response, "mcp.servers.close.catalog_ref")
        assert _versions(org) == before
        assert _active(org).mcp["servers"]["close"]["catalog_ref"] == CLOSE_REF
        assert len(pushes) == pushed

    def test_org_config_create_of_a_refused_catalog_ref(
        self, auth_client, user, pushes
    ):
        org = user.organization
        _seed_close(org)
        _store_close_key(auth_client)
        before, pushed = _versions(org), len(pushes)
        mcp = copy.deepcopy(_active(org).mcp)
        mcp["servers"]["close"]["catalog_ref"] = "nope-app/api-key@1.0.0"

        response = auth_client.post(
            "/agentcc/org-configs/", {"mcp": mcp}, format="json"
        )

        _assert_refused(response, "mcp.servers.close.catalog_ref")
        assert _versions(org) == before
        assert _active(org).mcp["servers"]["close"]["catalog_ref"] == CLOSE_REF
        assert len(pushes) == pushed

    def test_org_config_activate_of_a_refused_catalog_ref(
        self, auth_client, user, pushes
    ):
        org = user.organization
        _seed_close(org)
        _store_close_key(auth_client)
        active = _active(org)
        mcp = copy.deepcopy(active.mcp)
        mcp["servers"]["close"]["catalog_ref"] = "close/api-key@9.0.0"
        older = AgentccOrgConfig.no_workspace_objects.create(
            organization=org, version=active.version + 1, is_active=False, mcp=mcp
        )
        pushed = len(pushes)

        response = auth_client.post(
            f"/agentcc/org-configs/{older.pk}/activate/", {}, format="json"
        )

        _assert_refused(response, "mcp.servers.close.catalog_ref")
        assert _active(org).pk == active.pk
        older.refresh_from_db()
        assert not older.is_active
        assert len(pushes) == pushed
