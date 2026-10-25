"""MCP contract v1: org config mcp v2 and the MCP, tools and capability contracts,
their golden examples, what validate_mcp accepts and assigns on save, and
the push-only secrets config_push adds at push time (MCP credential secrets,
delegated grants and the IdP client secret)."""

import copy
import json
import re
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError
from referencing import Registry, Resource
from structlog.testing import capture_logs

from agentcc.contracts import gateway_admin as contract
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.serializers.org_config import (
    AgentccOrgConfigSerializer,
    AgentccOrgConfigWriteSerializer,
)
from agentcc.services.config_push import (
    _build_payload,
    push_org_config,
    validate_org_config,
)
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)

CONTRACTS_DIR = Path(__file__).resolve().parents[3] / "api_contracts" / "gateway"
EXAMPLES_DIR = CONTRACTS_DIR / "examples"
V1_FIXTURE = Path(__file__).resolve().parent / "fixtures/gateway_org_config.full.json"

MCP_CREDENTIALS_MODULE = "agentcc.services.mcp_credentials"
GRANTS_MODULE = "agentcc.services.delegated_grants"
GATEWAY_CLIENT = "agentcc.services.config_push.get_gateway_client"
SAVE_PUSH = "agentcc.views.org_config.AgentccOrgConfigViewSet._push_config_to_gateway"

# The MCP, tools and capability goldens.
ADMIN_SCHEMA_ID = "https://futureagi.com/contracts/gateway/agentcc-admin.schema.json"
MCP_GOLDENS = (
    "org-config-mcp-v2",
    "mcp-tool-call-v1",
    "mcp-denial-v1",
    "mcp-admin-status-v1",
    "mcp-admin-tools-v1",
    "mcp-probe-v1",
    "mcp-access-token-claims-v1",
    "mcp-oauth-keys-push-v1",
    "mcp-approval-v1",
    "mcp-revision-v1",
    "mcp-managed-tool-settlement-v1",
    "mcp-upstream-token-v1",
    "org-config-tools-v1",
    "tool-price-card-v1",
    "capability-usage-v1",
)
# Record schemas the gateway's Go tests check emitted values against, through
# internal/contracts/schematest, so they may use only its keywords.
GO_CHECKED_SCHEMAS = (
    "log-entry-v2",
    "mcp-tool-call-v1",
    "mcp-denial-v1",
    "mcp-managed-tool-settlement-v1",
    "tool-price-card-v1",
    "capability-usage-v1",
)
SCHEMATEST_KEYWORDS = frozenset(
    {
        "$ref",
        "$defs",
        "type",
        "const",
        "enum",
        "required",
        "properties",
        "additionalProperties",
        "items",
        "anyOf",
        "pattern",
        "format",
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "minItems",
        "maxItems",
        "$schema",
        "$id",
        "title",
        "description",
        "default",
    }
)
CREDIT_CODES = frozenset({"managed_credits_exhausted", "managed_credits_insufficient"})
VS_ID = re.compile(r"^vs_[0-9A-HJKMNP-TV-Z]{26}$")
SECRET = {"type": "bearer", "header": "Authorization", "value": "test-secret-not-real"}


def _load(path):
    return json.loads(path.read_text())


def _example(name):
    return _load(EXAMPLES_DIR / f"{name}.json")


def _schema(name):
    return _load(EXAMPLES_DIR / f"{name}.schema.json")


def _validator(schema):
    """A validator that resolves the admin schema and every golden schema by
    $id, so one golden's schema can be checked against another's."""
    resources = [_load(CONTRACTS_DIR / "agentcc-admin.schema.json")] + [
        _load(path) for path in EXAMPLES_DIR.glob("*.schema.json")
    ]
    registry = Registry().with_resources(
        (doc["$id"], Resource.from_contents(doc)) for doc in resources
    )
    return Draft202012Validator(schema, registry=registry)


def _errors(schema, instance):
    return [
        f"{list(e.absolute_path)}: {e.message}"
        for e in _validator(schema).iter_errors(instance)
    ]


def _def_schema(name, definition):
    """A schema for one $defs entry of a golden schema."""
    schema = _schema(name)
    return {"$defs": schema["$defs"], "$ref": f"#/$defs/{definition}"}


def _fake_module(name, **attrs):
    module = ModuleType(name)
    for attr, value in attrs.items():
        setattr(module, attr, value)
    return module


def _keywords(node, found):
    if isinstance(node, dict):
        for key, value in node.items():
            found.add(key)
            if key in ("properties", "$defs"):
                for sub in value.values():
                    _keywords(sub, found)
            elif key in ("items", "additionalProperties") and isinstance(value, dict):
                _keywords(value, found)
            elif key == "anyOf":
                for sub in value:
                    _keywords(sub, found)
    return found


def _save(data, organization=None):
    """Run the org-config write serializer as POST org-configs/ does, with the
    org in the request context the way authentication sets it."""
    clear_workspace_context()
    if organization is not None:
        set_workspace_context(organization=organization)
    try:
        serializer = AgentccOrgConfigWriteSerializer(data=data)
        valid = serializer.is_valid()
        return serializer, valid
    finally:
        clear_workspace_context()


def _stored_mcp():
    """The MCP golden as a save carries it: no push-only secret, no ids."""
    mcp = copy.deepcopy(_example("org-config-mcp-v2")["mcp"])
    mcp["servers"]["github"]["credential"].pop("secret")
    mcp["managed_tools"]["credentials"]["fagi.web"].pop("secret")
    for entry in mcp["virtual_servers"].values():
        entry.pop("id")
    return mcp


class TestGoldens:
    @pytest.mark.parametrize("name", MCP_GOLDENS)
    def test_every_mcp_golden_validates(self, name):
        schema = _schema(name)
        Draft202012Validator.check_schema(schema)

        assert _errors(schema, _example(name)) == []

    def test_goldens_use_schema_ids_by_file_name(self):
        for name in MCP_GOLDENS:
            assert _schema(name)["$id"].endswith(f"/examples/{name}.schema.json")
            assert _schema(name)["title"] == name

    @pytest.mark.parametrize("name", GO_CHECKED_SCHEMAS)
    def test_record_schemas_use_only_schematest_keywords(self, name):
        used = _keywords(_schema(name), set())

        assert used <= SCHEMATEST_KEYWORDS, used - SCHEMATEST_KEYWORDS
        for ref in re.findall(r'"\$ref": "([^"]+)"', json.dumps(_schema(name))):
            assert ref.startswith("#/$defs/"), ref

    def test_denial_credits_example_validates_against_error_402(self):
        denials = _example("mcp-denial-v1")["results"]
        credits = [
            d
            for d in denials
            if d["_meta"]["io.futureagi/error"]["code"] in CREDIT_CODES
        ]

        assert credits, "the golden has no credits denial"
        for denial in credits:
            error = {"error": denial["_meta"]["io.futureagi/error"]}
            assert _errors(_schema("error-402-v1"), error) == []

    def test_denial_text_names_its_code(self):
        for denial in _example("mcp-denial-v1")["results"]:
            error = denial["_meta"]["io.futureagi/error"]
            assert denial["content"][0]["text"] == (
                f"{error['message']} [{error['code']}]"
            )

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda d: d.update(isError=False),
            lambda d: d["_meta"]["io.futureagi/error"].update(code="made_up"),
            lambda d: d["_meta"]["io.futureagi/error"]["metadata"].update(
                scope="team:eng"
            ),
            lambda d: d["_meta"]["io.futureagi/error"]["metadata"].pop("limit_source"),
            lambda d: d["_meta"]["io.futureagi/tool_call"].pop("billed_usd"),
            lambda d: d["_meta"].pop("io.futureagi/tool_call"),
        ],
        ids=[
            "not-error",
            "unknown-code",
            "credits-scope-not-org",
            "credits-no-limit-source",
            "no-billed-usd",
            "no-tool-call-meta",
        ],
    )
    def test_denial_schema_rejects_off_contract_shapes(self, mutate):
        denial = copy.deepcopy(_example("mcp-denial-v1")["results"][0])
        mutate(denial)

        assert _errors(_def_schema("mcp-denial-v1", "Denial"), denial)

    def test_managed_tool_settlement_is_a_managed_settlement(self):
        record = _example("mcp-managed-tool-settlement-v1")
        settlement = _schema("managed-settlement-v1")

        assert _errors(settlement, record) == []
        attempts = json.loads(record["attempts"])
        attempts_schema = {
            "$defs": settlement["$defs"],
            **settlement["properties"]["attempts"]["contentSchema"],
        }
        assert not list(Draft202012Validator(attempts_schema).iter_errors(attempts))
        # The settlement mapping for a managed tool call.
        tool_call_id = record["request_id"]
        server_id, tool = record["model"].split("/", 1)
        assert record["generation_id"] == tool_call_id
        assert record["provider"] == "mcp"
        assert record["endpoint_id"] == f"mcp:{server_id}:{tool}"
        assert attempts == [
            {
                "attempt_id": f"{tool_call_id}.1",
                "kind": "primary",
                "provider": f"mcp:{server_id}",
                "model": tool,
                "credential_type": "managed",
                "upstream_micros": int(record["upstream_micros"]),
                "billed_micros": int(record["billed_micros"]),
                "outcome": "success",
            }
        ]

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda r: r.update(provider="openrouter"),
            lambda r: r.update(upstream_fee_bps="550"),
            lambda r: r.update(endpoint_id="openrouter:fagi.web:search"),
            lambda r: r.update(attempts=r["attempts"].replace('"primary"', '"retry"')),
        ],
        ids=["provider", "upstream-fee", "endpoint", "attempt-kind"],
    )
    def test_settlement_mapping_rejects_off_mapping_records(self, mutate):
        record = _example("mcp-managed-tool-settlement-v1")
        mutate(record)

        assert _errors(_schema("mcp-managed-tool-settlement-v1"), record)

    def test_upstream_token_golden_validates(self):
        schema = _schema("mcp-upstream-token-v1")
        golden = _example("mcp-upstream-token-v1")

        assert _errors(schema, golden) == []
        for part, bad in (
            ("success", {"token_type": "bearer"}),
            ("refusal", {"error": "unknown"}),
            ("request", {"org_id": ""}),
        ):
            broken = copy.deepcopy(golden)
            broken[part].update(bad)
            assert _errors(schema, broken), part

    def test_tool_call_record_keeps_names_and_optional_identity_fields(self):
        schema = _schema("mcp-tool-call-v1")
        record = _example("mcp-tool-call-v1")
        for field in ("identity", "account_label", "policy", "flow", "shaped"):
            assert field in record
        # metadata/none: no arguments or result, names kept, identity fields optional.
        bare = {
            k: v
            for k, v in record.items()
            if k
            not in {
                "arguments",
                "result",
                "identity",
                "account_label",
                "policy",
                "flow",
                "shaped",
            }
        }
        bare["content_mode"] = "none"
        assert _errors(schema, bare) == []
        for broken in (
            {**record, "kind": "log"},
            {**record, "billing_mode": "free"},
            {**record, "usage_estimated": True},
            {**record, "session_id": "sess_9c1d"},
            {k: v for k, v in record.items() if k != "tool"},
        ):
            assert _errors(schema, broken)

    def test_admin_shapes_name_servers_by_server_id(self):
        status = _example("mcp-admin-status-v1")
        assert all("server_id" in server for server in status["servers"])
        broken = copy.deepcopy(status)
        broken["servers"][0]["id"] = broken["servers"][0].pop("server_id")
        assert _errors(_schema("mcp-admin-status-v1"), broken)
        tools = _example("mcp-admin-tools-v1")
        assert all("server_id" in tool for tool in tools["tools"])

    def test_oauth_keys_push_refuses_private_keys(self):
        push = _example("mcp-oauth-keys-push-v1")
        push["keys"][0]["d"] = "private"

        assert _errors(_schema("mcp-oauth-keys-push-v1"), push)

    def test_secret_values_in_goldens_are_test_markers(self):
        """No golden may hold anything that looks like a real credential."""
        secret_keys = {"value", "token", "api_key", "access_token", "client_secret"}
        found = []

        def walk(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    if key in secret_keys and isinstance(value, str):
                        found.append(value)
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        for name in MCP_GOLDENS:
            walk(_example(name))
        assert found
        assert all("not-real" in v or "not-a-real" in v for v in found), found


class TestOrgConfigMCPv2:
    def test_golden_validates_against_the_generated_contract(self):
        contract.OrgConfig.model_validate(_example("org-config-mcp-v2"), strict=True)

        serializer, valid = _save({"mcp": _stored_mcp()})
        assert valid, serializer.errors

    @pytest.mark.parametrize(
        "slug", ["Eng", "-eng", "eng_team", "e" * 64, "eng.team", "", "eng\n"]
    )
    def test_virtual_server_slug_outside_pattern_rejected(self, slug):
        serializer, valid = _save({"mcp": {"virtual_servers": {slug: {}}}})

        assert not valid
        assert "mcp" in serializer.errors

    @pytest.mark.parametrize(
        "key", ["virtual_servers", "servers", "managed_tools", "access", "policy"]
    )
    def test_null_sections_are_absent(self, key):
        serializer, valid = _save({"mcp": {"enabled": True, key: None}})

        assert valid, serializer.errors
        assert serializer.validated_data["mcp"][key] is None

    def test_unknown_naming_rejected(self):
        serializer, valid = _save(
            {"mcp": {"virtual_servers": {"eng": {"naming": "camel"}}}}
        )

        assert not valid
        assert "naming" in json.dumps(serializer.errors)

    @pytest.mark.parametrize(
        "mcp",
        [
            {"servers": {"s": {"credential": {"mode": "magic"}}}},
            {"servers": {"s": {"timeout_ms": "30000"}}},
            {"servers": {"s": {"labels": ["secret"]}}},
            {"virtual_servers": {"v": {"approval": {"validity": "forever"}}}},
            {"pinning": {"mode": "strict"}},
            {"flow_control": {"mode": "block"}},
            {"managed_tools": {"enabled": "yes"}},
            {
                "policy": {
                    "rules": [
                        {
                            "id": "r",
                            "match": {"tools": []},
                            "action": "deny",
                            "reason": "x",
                        }
                    ]
                }
            },
            {"access": {"default": "maybe"}},
        ],
    )
    def test_v2_keys_are_typed_strictly(self, mcp):
        serializer, valid = _save({"mcp": mcp})

        assert not valid, mcp
        assert "mcp" in serializer.errors

    def test_private_network_rejected(self):
        serializer, valid = _save(
            {
                "mcp": {
                    "servers": {
                        "internal": {
                            "url": "http://10.0.0.5/mcp",
                            "private_network": True,
                        }
                    }
                }
            }
        )

        assert not valid
        assert "mcp.egress.private_allow" in json.dumps(serializer.errors)
        # The push contract has no such key either, so no save path can store it.
        with pytest.raises(ValidationError):
            contract.OrgConfig.model_validate(
                {"mcp": {"servers": {"internal": {"private_network": True}}}}
            )

    @pytest.mark.parametrize(
        "header",
        [
            "Authorization",
            "cookie",
            "X-API-Key",
            "proxy-authorization",
            "Mcp-Session-Id",
            "mcp-protocol-version",
            "x-agentcc-run-id",
            "X-Omega-Agent-Id",
            # Leading or trailing whitespace around a refused name is refused too.
            " authorization",
            "Authorization ",
            "\tcookie",
            "x-agentcc-run-id\n",
        ],
    )
    def test_forward_header_refused_names(self, header):
        serializer, valid = _save(
            {
                "mcp": {
                    "servers": {
                        "s": {
                            "url": "https://s",
                            "forward_headers": ["x-trace", header],
                        }
                    }
                }
            }
        )

        assert not valid
        error = serializer.errors["mcp"][0]
        assert error.code == "forward_header_refused"
        assert header in str(error)
        assert "x-trace" not in str(error)

    def test_allowed_forward_headers_save(self):
        serializer, valid = _save(
            {
                "mcp": {
                    "servers": {
                        "s": {
                            "url": "https://s",
                            "forward_headers": ["x-trace", "x-request-source"],
                        }
                    }
                }
            }
        )

        assert valid, serializer.errors

    @pytest.mark.parametrize("header", ["x trace", "x-trace:", "x-träce", "x-a\r\nb"])
    def test_forward_header_must_be_a_header_name(self, header):
        serializer, valid = _save(
            {
                "mcp": {
                    "servers": {"s": {"url": "https://s", "forward_headers": [header]}}
                }
            }
        )

        assert not valid
        assert "not an HTTP header name" in json.dumps(serializer.errors)
        # The admin schema states the same rule for every other consumer.
        server_schema = {"$ref": f"{ADMIN_SCHEMA_ID}#/$defs/MCPServerOrgConfig"}
        assert not _errors(server_schema, {"forward_headers": ["x-trace"]})
        assert _errors(server_schema, {"forward_headers": [header]})

    @pytest.mark.parametrize(
        "server",
        [
            {"url": "https://s", "transport": "stdio"},
            {"url": "https://s", "transport": "streamable"},
            {"url": "/usr/local/bin/mcp-server"},
            {"url": "file:///srv/mcp/server.sock"},
            {"url": "https://"},
            {"url": "https://s\n/mcp"},
            {"url": "http://["},
        ],
    )
    def test_new_server_needs_an_http_transport_and_url(self, server):
        serializer, valid = _save({"mcp": {"servers": {"s": server}}})

        assert not valid, server
        assert "mcp.servers.s." in json.dumps(serializer.errors)

    @pytest.mark.parametrize(
        "server",
        [
            {"url": "https://s/mcp", "transport": "http"},
            {"url": "https://s/sse", "transport": "sse"},
            # Plain http stays open: in-cluster sidecars and the console's
            # own form (http://mcp-server:8080) use it; egress is the
            # gateway's control.
            {"url": "http://mcp-server:8080"},
        ],
    )
    def test_new_server_http_endpoints_save(self, server):
        serializer, valid = _save({"mcp": {"servers": {"s": server}}})

        assert valid, serializer.errors

    @pytest.mark.parametrize(
        "glob", ["fagi.web", "search/", "*", "fagi.web/a/b", "search/exa\n"]
    )
    def test_managed_tool_globs_must_name_a_managed_server_or_capability(self, glob):
        serializer, valid = _save({"mcp": {"managed_tools": {"deny": [glob]}}})

        assert not valid
        assert "mcp.managed_tools.deny" in json.dumps(serializer.errors)

    @pytest.mark.parametrize(
        "glob", ["fagi.web/*", "fagi.*/*", "search/exa", "documents.parse/*", "*/exa"]
    )
    def test_managed_tool_globs_accept_both_forms(self, glob):
        serializer, valid = _save({"mcp": {"managed_tools": {"allow": [glob]}}})

        assert valid, serializer.errors

    @pytest.mark.parametrize("server_id", ["GitHub", "github\n"])
    def test_new_server_id_outside_pattern_rejected(self, server_id):
        serializer, valid = _save(
            {"mcp": {"servers": {server_id: {"url": "https://g"}}}}
        )

        assert not valid
        assert "server id" in json.dumps(serializer.errors)


@pytest.mark.integration
class TestVirtualServerIds:
    def test_vs_id_assigned_on_create_and_immutable(self, user):
        org = user.organization

        # A missing id is assigned on create.
        serializer, valid = _save(
            {"mcp": {"virtual_servers": {"eng": {"members": ["github"]}}}}, org
        )
        assert valid, serializer.errors
        assigned = serializer.validated_data["mcp"]["virtual_servers"]["eng"]["id"]
        assert VS_ID.match(assigned)
        AgentccOrgConfig.no_workspace_objects.create(
            organization=org,
            version=1,
            is_active=True,
            mcp=serializer.validated_data["mcp"],
        )

        # ... once: a later save without it keeps the stored id, and a new slug
        # gets its own.
        serializer, valid = _save(
            {
                "mcp": {
                    "virtual_servers": {
                        "eng": {"members": ["github", "issues"]},
                        "ops": {"members": ["pager"]},
                    }
                }
            },
            org,
        )
        assert valid, serializer.errors
        saved = serializer.validated_data["mcp"]["virtual_servers"]
        assert saved["eng"]["id"] == assigned
        assert VS_ID.match(saved["ops"]["id"]) and saved["ops"]["id"] != assigned

        # Sending the stored id back unchanged is fine.
        serializer, valid = _save(
            {"mcp": {"virtual_servers": {"eng": {"id": assigned}}}}, org
        )
        assert valid, serializer.errors

        # A client-set id on a new slug, and a changed id, are 400.
        for virtual_servers in (
            {"new": {"id": "vs_01M4ZEB9E1A6Y8ATM7S5TJZ9YY"}},
            {"eng": {"id": "vs_01M4ZEB9E1A6Y8ATM7S5TJZ9YY"}},
            {"eng": {"id": "not-an-id"}},
        ):
            serializer, valid = _save(
                {"mcp": {"virtual_servers": virtual_servers}}, org
            )
            assert not valid, virtual_servers
            assert serializer.errors["mcp"][0].code == "vs_id_immutable"

    def test_another_orgs_ids_are_not_reused(self, user):
        from accounts.models import Organization

        other = Organization.objects.create(name="Other org")
        AgentccOrgConfig.no_workspace_objects.create(
            organization=other,
            version=1,
            is_active=True,
            mcp={"virtual_servers": {"eng": {"id": "vs_01M4ZEB9E0J3AADR85Z4T4428X"}}},
        )

        serializer, valid = _save(
            {"mcp": {"virtual_servers": {"eng": {}}}}, user.organization
        )

        assert valid, serializer.errors
        assigned = serializer.validated_data["mcp"]["virtual_servers"]["eng"]["id"]
        assert assigned != "vs_01M4ZEB9E0J3AADR85Z4T4428X"
        serializer, valid = _save(
            {
                "mcp": {
                    "virtual_servers": {"eng": {"id": "vs_01M4ZEB9E0J3AADR85Z4T4428X"}}
                }
            },
            user.organization,
        )
        assert not valid

    @patch(SAVE_PUSH, return_value=True)
    def test_post_org_configs_stores_the_assigned_id(self, _push, auth_client, user):
        response = auth_client.post(
            "/agentcc/org-configs/",
            {"mcp": {"enabled": True, "virtual_servers": {"eng": {"members": ["s"]}}}},
            format="json",
        )

        assert response.status_code == 200, response.json()
        stored = AgentccOrgConfig.no_workspace_objects.get(
            organization=user.organization, is_active=True, deleted=False
        )
        assert VS_ID.match(stored.mcp["virtual_servers"]["eng"]["id"])

        response = auth_client.post(
            "/agentcc/org-configs/",
            {
                "mcp": {
                    "virtual_servers": {"eng": {"id": "vs_01M4ZEB9E1A6Y8ATM7S5TJZ9YY"}}
                }
            },
            format="json",
        )
        assert response.status_code == 400
        assert response.json()["code"] == "vs_id_immutable"


@pytest.mark.integration
class TestExistingConfigsValidate:
    @patch("agentcc.services.config_push._assemble_providers", return_value={})
    def test_stored_mcp_configs_save_and_validate(self, _providers):
        fixture_mcp = _load(V1_FIXTURE)["mcp"]
        for stored in (
            {"mcp": {}},
            {"mcp": {"enabled": False}},
            {"mcp": fixture_mcp},
            {
                "mcp": {
                    "enabled": True,
                    "servers": {
                        "docs": {
                            "url": "https://m",
                            "transport": "http",
                            "auth": {
                                "type": "api_key",
                                "header": "X-API-Key",
                                "key": "k",
                            },
                            "tools_cache_ttl": "10m",
                        }
                    },
                }
            },
        ):
            serializer, valid = _save(stored)
            assert valid, (stored, serializer.errors)
            validate_org_config(
                AgentccOrgConfig(version=1, **serializer.validated_data)
            )

    def test_stored_server_id_outside_pattern_keeps_saving(self, user):
        org = user.organization
        stored = {"servers": {"MyServer": {"url": "https://m", "transport": "http"}}}
        AgentccOrgConfig.no_workspace_objects.create(
            organization=org, version=1, is_active=True, mcp=stored
        )

        serializer, valid = _save({"mcp": stored}, org)

        assert valid, serializer.errors

    def test_stored_server_keeps_its_saved_transport_and_url(self, user):
        """The new-server endpoint rule never refuses a server the active
        config already holds, so an org's next save is not blocked by it."""
        org = user.organization
        stored = {"servers": {"old": {"url": "mcp.internal", "transport": "stdio"}}}
        AgentccOrgConfig.no_workspace_objects.create(
            organization=org, version=1, is_active=True, mcp=stored
        )

        serializer, valid = _save({"mcp": stored}, org)
        assert valid, serializer.errors

        added = copy.deepcopy(stored)
        added["servers"]["new"] = {"url": "mcp.internal", "transport": "stdio"}
        serializer, valid = _save({"mcp": added}, org)
        assert not valid
        assert "mcp.servers.new.transport" in json.dumps(serializer.errors)


@pytest.mark.integration
class TestPushAssemblesMCPCredentials:
    @pytest.fixture
    def org_config(self, user):
        mcp = _stored_mcp()
        return AgentccOrgConfig.no_workspace_objects.create(
            organization=user.organization, version=2, is_active=True, mcp=mcp
        )

    def _pushed_payload(self, org_config):
        client = MagicMock()
        with patch(GATEWAY_CLIENT, return_value=client):
            assert push_org_config(str(org_config.organization_id), org_config)
        client.set_org_config.assert_called_once()
        return client.set_org_config.call_args.args[1]

    def test_push_assembles_mcp_credentials_only_at_push(self, org_config):
        stored_before = copy.deepcopy(org_config.mcp)
        managed_secret = {**SECRET, "type": "api_key", "header": "x-api-key"}
        assemble = MagicMock(
            return_value={"github": dict(SECRET), "fagi.web": managed_secret}
        )
        module = _fake_module(MCP_CREDENTIALS_MODULE, assemble_for_push=assemble)

        with patch.dict(sys.modules, {MCP_CREDENTIALS_MODULE: module}):
            # Save-time validation never assembles secrets.
            validate_org_config(org_config)
            assemble.assert_not_called()
            payload = self._pushed_payload(org_config)

        assemble.assert_called_once_with(str(org_config.organization_id))
        github = payload["mcp"]["servers"]["github"]["credential"]
        assert github["secret"] == SECRET
        # The stored credential reference and state travel with the secret.
        assert github["mode"] == "shared" and github["credential_id"] == "cred_7d1e"
        assert (
            payload["mcp"]["managed_tools"]["credentials"]["fagi.web"]["secret"]
            == managed_secret
        )
        # The stored row never carries a secret.
        stored = AgentccOrgConfig.no_workspace_objects.get(id=org_config.id)
        assert stored.mcp == stored_before == org_config.mcp
        rendered = json.dumps(AgentccOrgConfigSerializer(stored).data, default=str)
        assert SECRET["value"] not in rendered and '"secret"' not in rendered

    def test_org_without_servers_never_asks_the_vault(self, user):
        config = AgentccOrgConfig.no_workspace_objects.create(
            organization=user.organization,
            version=1,
            is_active=True,
            mcp={"enabled": True, "virtual_servers": {}},
        )
        assemble = MagicMock(return_value={"github": dict(SECRET)})
        module = _fake_module(MCP_CREDENTIALS_MODULE, assemble_for_push=assemble)

        with patch.dict(sys.modules, {MCP_CREDENTIALS_MODULE: module}):
            payload = _build_payload(str(config.organization_id), config)

        assemble.assert_not_called()
        assert payload["mcp"] == {"enabled": True, "virtual_servers": {}}

    def test_without_the_credential_service_the_payload_is_unchanged(self, org_config):
        with patch.dict(sys.modules, {MCP_CREDENTIALS_MODULE: None}):
            payload = self._pushed_payload(org_config)

        assert "secret" not in payload["mcp"]["servers"]["github"]["credential"]
        assert payload["mcp"]["virtual_servers"] == org_config.mcp["virtual_servers"]

    @pytest.mark.parametrize(
        "assembled",
        [
            {"github": {"type": "bearer"}},
            {"github": {**SECRET, "extra": 1}},
            {"github": "plain-string"},
            {"unknown_server": dict(SECRET)},
            ["github"],
        ],
        ids=["no-value", "extra-field", "not-object", "no-such-server", "not-a-map"],
    )
    def test_invalid_entries_are_dropped_without_logging_values(
        self, org_config, assembled
    ):
        module = _fake_module(
            MCP_CREDENTIALS_MODULE, assemble_for_push=MagicMock(return_value=assembled)
        )

        with (
            patch.dict(sys.modules, {MCP_CREDENTIALS_MODULE: module}),
            capture_logs() as logs,
        ):
            payload = _build_payload(str(org_config.organization_id), org_config)

        assert "secret" not in payload["mcp"]["servers"]["github"]["credential"]
        assert logs and all(
            log["event"] in {"mcp_credential_dropped", "mcp_credentials_invalid"}
            for log in logs
        )
        assert SECRET["value"] not in json.dumps(logs, default=str)

    def test_a_failing_service_pushes_no_secrets(self, org_config):
        module = _fake_module(
            MCP_CREDENTIALS_MODULE,
            assemble_for_push=MagicMock(side_effect=RuntimeError(SECRET["value"])),
        )

        with (
            patch.dict(sys.modules, {MCP_CREDENTIALS_MODULE: module}),
            capture_logs() as logs,
        ):
            payload = _build_payload(str(org_config.organization_id), org_config)

        assert "secret" not in payload["mcp"]["servers"]["github"]["credential"]
        assert [log["event"] for log in logs] == ["mcp_credentials_assemble_failed"]
        assert SECRET["value"] not in json.dumps(logs, default=str)


@pytest.mark.integration
class TestDelegatedGrantsPushOnly:
    GRANT = {
        "grant_id": "grant_1",
        "agent_id": "researcher",
        "grantor_user_id": "user_42",
        "scopes": {"servers": ["github"], "tools": ["github/get_*"]},
        "max_usd": 2.5,
        "expires_at": "2026-11-01T00:00:00Z",
    }

    @pytest.fixture
    def org_config(self, user):
        return AgentccOrgConfig.no_workspace_objects.create(
            organization=user.organization, version=1, is_active=True
        )

    def test_assemble_delegated_grants_lazy_and_push_only(self, org_config):
        org_id = str(org_config.organization_id)

        # ImportError is a no-op.
        with patch.dict(sys.modules, {GRANTS_MODULE: None}):
            assert "identity" not in _build_payload(org_id, org_config)

        assemble = MagicMock(
            return_value={
                "delegated_grants": {"grant_1": dict(self.GRANT)},
                "idp": {"client_secret": "test-idp-secret-not-real"},
            }
        )
        module = _fake_module(GRANTS_MODULE, assemble_for_push=assemble)
        with patch.dict(sys.modules, {GRANTS_MODULE: module}):
            # Never at save.
            validate_org_config(org_config)
            _build_payload(org_id, org_config, with_upstream_keys=False)
            assemble.assert_not_called()

            payload = _build_payload(org_id, org_config)

        assemble.assert_called_once_with(org_id)
        assert payload["identity"]["delegated_grants"]["grant_1"][
            "grantor_user_id"
        ] == ("user_42")
        assert payload["identity"]["idp"] == {
            "client_secret": "test-idp-secret-not-real"
        }

    @pytest.mark.parametrize(
        "assembled",
        [
            {"delegated_grants": {"grant_1": {"grant_id": "grant_1"}}},
            {"delegated_grants": {"other": dict(GRANT)}},
            {"idp": {"client_secret": ""}},
            "not-an-object",
        ],
        ids=["missing-fields", "key-mismatch", "empty-secret", "not-object"],
    )
    def test_invalid_parts_are_dropped(self, org_config, assembled):
        module = _fake_module(
            GRANTS_MODULE, assemble_for_push=MagicMock(return_value=assembled)
        )

        with patch.dict(sys.modules, {GRANTS_MODULE: module}), capture_logs() as logs:
            payload = _build_payload(str(org_config.organization_id), org_config)

        assert "identity" not in payload
        assert logs
        assert "user_42" not in json.dumps(logs, default=str)


class TestToolsContract:
    def test_contract_tools_round_trip(self):
        golden = _example("org-config-tools-v1")
        parsed = contract.OrgConfig.model_validate(golden, strict=True)

        assert parsed.model_dump(by_alias=True, exclude_unset=True) == golden
        cards = _example("tool-price-card-v1")["cards"]
        assert {card["resale_status"] for card in cards} == {
            "native",
            "managed_partner",
            "unconfirmed",
            "byok_only",
        }
        assert any(card.get("owner_price_pending") for card in cards)
        assert (
            _errors(_schema("capability-usage-v1"), _example("capability-usage-v1"))
            == []
        )

    @pytest.mark.parametrize(
        "tools",
        [
            {"capabilities": {"search": {"managed": True}}},
            {"capabilities": {"Search": {"enabled": True}}},
            {"policy": {"retention_override": "forever"}},
            {"policy": {"zdr": True}},
        ],
    )
    def test_off_contract_tools_rejected(self, tools):
        assert _errors(_schema("org-config-tools-v1"), {"tools": tools})

    def test_price_card_rejects_unknown_resale_status(self):
        card = copy.deepcopy(_example("tool-price-card-v1")["cards"][0])
        card["resale_status"] = "resold"

        assert _errors(_def_schema("tool-price-card-v1", "Card"), card)


class TestIdentityAndPolicyKeys:
    MCP_KEYS = {
        "servers": {
            "hr": {
                "url": "https://hr.example.com/mcp",
                "credential": {"mode": "exchange"},
                "oauth": {"adapter": "entra"},
                "labels": ["private_data"],
            },
            "jira": {
                "url": "https://jira.example.com/mcp",
                "credential": {"mode": "id_jag"},
            },
            "crm": {
                "url": "https://crm.example.com/mcp",
                "credential": {
                    "mode": "vendor_end_user",
                    "vendor": "composio",
                    "identity_binding": {"in": "query", "name": "user"},
                },
            },
        },
        "virtual_servers": {
            "people": {
                "members": ["hr@primary", "jira", "fagi.web"],
                "overrides": {"hr/export": {"labels": ["egress"]}},
            }
        },
        "managed_tools": {
            "enabled": True,
            "allow": ["search/exa", "fagi.web/*"],
            "deny": ["browser/*"],
        },
        "flow_control": {"mode": "shadow", "on_egress_after_taint": "approve"},
        "policy": {
            "rules": [
                {
                    "id": "approve-exports",
                    "match": {"tools": ["hr/export*"], "env": ["prod"]},
                    "action": "approve",
                    "reason": "Exports leave the company.",
                    "shadow": True,
                }
            ],
            "combos": [
                {
                    "id": "hr-then-mail",
                    "sequence": ["hr/*", "mail/send"],
                    "within": "run",
                    "action": "deny",
                }
            ],
            "default_tier": {"undeclared": "deny"},
        },
        "shaping": {"per_tool": {"hr/*": {"budget_bytes": 8192, "project": ["/rows"]}}},
    }
    NON_MCP = {
        "budgets": {
            "enabled": True,
            "fail_mode": "closed",
            "no_max_tokens": "reserve_default",
        },
        "routing": {
            "agents": {
                "envelopes": {
                    "defaults": {"max_usd": 1, "max_calls": 50},
                    "on_breach": "pause",
                    "soft_threshold": 0.9,
                    "allow_downgrade": False,
                    "loop": {"action": "stop"},
                }
            }
        },
        "identity": {
            "require_human_sponsor": False,
            "grants": {"max_ttl_days": 30},
            "idp": {
                "issuer": "https://idp.example.com",
                "token_endpoint": "https://idp.example.com/token",
                "client_id": "gw",
            },
        },
    }

    def test_identity_and_policy_keys_round_trip(self):
        config = {"mcp": self.MCP_KEYS, **self.NON_MCP}
        parsed = contract.OrgConfig.model_validate(config, strict=True)

        assert parsed.model_dump(by_alias=True, exclude_unset=True) == config
        assert _errors(_schema("org-config-v2"), config) == []
        # Through the save path: validate_mcp and the v2 routing and budget
        # checks accept every key.
        serializer, valid = _save(
            {
                "mcp": self.MCP_KEYS,
                "budgets": self.NON_MCP["budgets"],
                "routing": self.NON_MCP["routing"],
            }
        )
        assert valid, serializer.errors
        saved = serializer.validated_data
        assert saved["mcp"]["servers"] == self.MCP_KEYS["servers"]
        assert VS_ID.match(saved["mcp"]["virtual_servers"]["people"]["id"])
        assert saved["routing"]["agents"]["envelopes"]["on_breach"] == "pause"

    @pytest.mark.parametrize(
        "config",
        [
            {
                "mcp": {
                    "servers": {
                        "s": {
                            "credential": {
                                "mode": "vendor_end_user",
                                "vendor": "unknown",
                            }
                        }
                    }
                }
            },
            {
                "mcp": {
                    "servers": {
                        "s": {
                            "credential": {
                                "identity_binding": {"in": "cookie", "name": "u"}
                            }
                        }
                    }
                }
            },
            {"mcp": {"servers": {"s": {"oauth": {"adapter": "keycloak"}}}}},
            {
                "mcp": {
                    "policy": {
                        "combos": [
                            {
                                "id": "c",
                                "sequence": ["a"],
                                "within": "run",
                                "action": "deny",
                            }
                        ]
                    }
                }
            },
            {"budgets": {"fail_mode": "half"}},
            {"routing": {"agents": {"envelopes": {"on_breach": "explode"}}}},
            {"routing": {"agents": {"envelopes": {"soft_threshold": 1.5}}}},
            {"identity": {"grants": {"max_ttl_days": 0}}},
        ],
    )
    def test_off_contract_key_values_rejected(self, config):
        assert _errors(_schema("org-config-v2"), config)

    def test_org_config_v2_golden_carries_the_non_mcp_keys(self):
        golden = _example("org-config-v2")

        assert golden["budgets"]["fail_mode"] == "open"
        assert golden["routing"]["agents"]["envelopes"]["on_breach"] == "downgrade"
        assert "client_secret" not in golden["identity"]["idp"]


class TestLogEntryAndRuntimeToken:
    EVIDENCE = {
        "budget_hold": {
            "hold_id": "req_01JD8Z3K4M5N6P7Q8R9S.1",
            "tightest": "team:eng=0.42",
            "enforced": True,
            "levels": 3,
            "held_usd": 0.04,
            "settled_usd": 0.0118,
        },
        "envelope": {
            "run_id": "run_R2",
            "root_run_id": "run_R1",
            "state": "warn",
            "action": "warn",
            "dimension": "usd",
            "used": 0.82,
            "limit": 1.0,
        },
        "cache": {
            "cache_read_tokens": 1024,
            "cache_write_tokens": 0,
            "uncached_input_tokens": 512,
            "cache_miss_usd": 0.0012,
        },
        "tool_schema": {"bytes": 18432, "est_tokens": 4608, "tools": 12},
        "overhead": {"total_ms": 7.5, "auth_ms": 0.4, "policy_ms": 1.1, "log_ms": 0.2},
    }

    def test_log_entry_v2_evidence_fields_validate(self):
        schema = _schema("log-entry-v2")
        existing = _example("log-entry-v2")
        assert _errors(schema, existing) == []

        entry = copy.deepcopy(existing)
        entry["evidence"] = copy.deepcopy(self.EVIDENCE)
        entry["agent"]["identity"] = _example("mcp-tool-call-v1")["identity"]
        entry["eval_export"] = "forbidden"
        entry["endpoint_type"] = "search"
        entry["attempts"][0].update(outcome="hollow", error_class="hollow_completion")
        assert _errors(schema, entry) == []
        # a2a is the value the A2A server sets (a2a/server.go).
        assert _errors(schema, {**entry, "endpoint_type": "a2a"}) == []

        for broken in (
            {**entry, "evidence": {**self.EVIDENCE, "prompt": {"text": "hi"}}},
            {**entry, "eval_export": "maybe"},
            {**entry, "endpoint_type": "telepathy"},
            {
                **entry,
                "evidence": {
                    **self.EVIDENCE,
                    "cache": {"cache_read_tokens": "many"},
                },
            },
            {
                **entry,
                "evidence": {
                    **self.EVIDENCE,
                    "budget_hold": {"hold_id": "h", "messages": [{"content": "x"}]},
                },
            },
        ):
            assert _errors(schema, broken)

    def test_generation_attempts_accept_hollow(self):
        schema = _schema("generation-v1")
        golden = _example("generation-v1")
        attempts = golden["data"]["routing"]["attempts"]
        attempts[0].update(outcome="hollow", error_class="hollow_completion")

        assert _errors(schema, golden) == []

    def test_runtime_token_claims_mcp_scope_and_sponsor(self):
        schema = _schema("runtime-token-claims-v1")
        golden = _example("runtime-token-claims-v1")
        assert _errors(schema, golden) == []

        token = copy.deepcopy(golden)
        token["claims"]["scopes"].append("gateway:mcp")
        token["claims"].update(
            sponsor_user_id="user_42",
            grant_id="grant_1",
            act=[
                {"agent_id": "planner", "run_id": "run_R1", "deployment_id": "dep_1"},
                {"agent_id": "researcher", "run_id": "run_R2"},
            ],
        )
        assert _errors(schema, token) == []

        for mutate in (
            lambda c: c["scopes"].append("gateway:everything"),
            lambda c: c.update(act=[{"agent_id": "planner"}]),
            lambda c: c.update(sponsor_user_id=""),
        ):
            broken = copy.deepcopy(token)
            mutate(broken["claims"])
            assert _errors(schema, broken)
