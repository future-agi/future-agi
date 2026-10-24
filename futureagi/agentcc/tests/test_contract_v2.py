"""Org-config contract v2: the schema and its golden examples, what a save may
contain, and what config_push adds at push time (meta, per-org managed
upstream keys, and the managed account bootstrap)."""

import copy
import json
import sys
from datetime import datetime
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError
from referencing import Registry, Resource

from agentcc.contracts.gateway_admin import OrgConfig as GatewayOrgConfig
from agentcc.contracts.gateway_admin import contract_default
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.serializers.org_config import (
    AgentccOrgConfigSerializer,
    AgentccOrgConfigWriteSerializer,
)
from agentcc.services.config_push import (
    SecretInConfig,
    _build_payload,
    _transform_guardrails,
    push_org_config,
    validate_org_config,
)

CONTRACTS_DIR = Path(__file__).resolve().parents[3] / "api_contracts" / "gateway"
EXAMPLES_DIR = CONTRACTS_DIR / "examples"
V1_FIXTURE = Path(__file__).resolve().parent / "fixtures/gateway_org_config.full.json"

UPSTREAM_KEYS_MODULE = "agentcc.services.managed_upstream_keys"
CREDITS_MODULE = "ee.usage.services.managed_credits"
SAVE_PUSH = "agentcc.views.org_config.AgentccOrgConfigViewSet._push_config_to_gateway"
GATEWAY_CLIENT = "agentcc.services.config_push.get_gateway_client"
EE_CHECK = "tfc.ee_gating.check_ee_feature"

UPSTREAM_KEYS = {
    "openrouter": {
        "api_key": "test-upstream-key-not-a-real-secret",
        "key_hash": "kh_3b9e",
        "label": "acme-prod",
    }
}


def _load(path):
    return json.loads(path.read_text())


def _golden_org_config():
    return _load(EXAMPLES_DIR / "org-config-v2.json")


def _stored_sections():
    """
    The golden as a save would carry it. Providers come from credentials and
    meta and upstream_keys are added at push time, so a save has none of them.
    Guardrail checks are left out because a save routes them through a
    guardrail policy, which is not what this contract covers.
    """
    sections = _golden_org_config()
    for push_only in ("providers", "meta"):
        sections.pop(push_only)
    sections["managed_models"].pop("upstream_keys")
    sections["guardrails"] = {"tool_calls": sections["guardrails"]["tool_calls"]}
    return sections


def _fake_module(name, **attrs):
    module = ModuleType(name)
    for attr, value in attrs.items():
        setattr(module, attr, value)
    return module


def _schema_validator(schema):
    admin = _load(CONTRACTS_DIR / "agentcc-admin.schema.json")
    registry = Registry().with_resource(admin["$id"], Resource.from_contents(admin))
    return Draft202012Validator(schema, registry=registry)


class TestContractSchema:
    def test_full_v2_config_validates(self):
        GatewayOrgConfig.model_validate(_golden_org_config())

        serializer = AgentccOrgConfigWriteSerializer(data=_stored_sections())
        assert serializer.is_valid(), serializer.errors

    @pytest.mark.parametrize(
        "schema_path",
        sorted(EXAMPLES_DIR.glob("*.schema.json")),
        ids=lambda p: p.name.removesuffix(".schema.json"),
    )
    def test_every_golden_validates_against_its_schema(self, schema_path):
        schema = _load(schema_path)
        Draft202012Validator.check_schema(schema)
        example = _load(schema_path.with_name(schema_path.name.replace(".schema", "")))

        errors = list(_schema_validator(schema).iter_errors(example))

        assert errors == [], [f"{list(e.absolute_path)}: {e.message}" for e in errors]

    def test_every_golden_has_a_schema(self):
        examples = {p.name for p in EXAMPLES_DIR.glob("*.json")}
        schemas = {name for name in examples if name.endswith(".schema.json")}
        assert {name.replace(".schema", "") for name in schemas} == examples - schemas
        # 16 gateway contract goldens and the 15 MCP, tools and capability
        # goldens.
        assert len(schemas) == 31

    def test_settlement_attempts_string_holds_attempt_summaries(self):
        schema = _load(EXAMPLES_DIR / "managed-settlement-v1.schema.json")
        record = _load(EXAMPLES_DIR / "managed-settlement-v1.json")
        attempts_schema = {
            "$defs": schema["$defs"],
            **schema["properties"]["attempts"]["contentSchema"],
        }

        attempts = json.loads(record["attempts"])

        assert attempts and not list(
            Draft202012Validator(attempts_schema).iter_errors(attempts)
        )

    @pytest.mark.parametrize(
        "golden,mutate",
        [
            ("log-entry-v2", lambda d: d.update(kind="outcome")),
            ("outcome-event-v1", lambda d: d.update(prompt="hello")),
            (
                "generation-v1",
                lambda d: d["data"]["routing"]["attempts"][0].update(
                    landed_cost_micros=1
                ),
            ),
            (
                "catalog-endpoints-v1",
                lambda d: d["data"]["endpoints"][0].update(source="scraped"),
            ),
            ("error-402-v1", lambda d: d["error"]["metadata"].pop("scope")),
            (
                "error-402-v1",
                lambda d: d["error"].update(code="managed_billing_unavailable"),
            ),
            ("runtime-token-claims-v1", lambda d: d["claims"].pop("root_run_id")),
            (
                "auto-decision-v2",
                lambda d: d["selector"].pop("agrees_with_deterministic"),
            ),
            ("models-list-v2", lambda d: d["data"][0].pop("pricing_guarantee")),
            ("managed-settlement-v1", lambda d: d.update(usage_estimated="true")),
            (
                "tool-call-blocked-v1",
                lambda d: d["error"]["metadata"].update(action="drop"),
            ),
        ],
    )
    def test_golden_schemas_reject_off_contract_shapes(self, golden, mutate):
        schema = _load(EXAMPLES_DIR / f"{golden}.schema.json")
        example = _load(EXAMPLES_DIR / f"{golden}.json")

        mutate(example)

        assert list(_schema_validator(schema).iter_errors(example))

    @pytest.mark.parametrize(
        "section,value",
        [
            (
                "routing",
                {
                    "rollout": {
                        "version": "v2",
                        "percent": 101,
                        "sticky_by": "session",
                        "routing": {},
                    }
                },
            ),
            (
                "routing",
                {
                    "rollout": {
                        "version": "v2",
                        "percent": 5,
                        "sticky_by": "user",
                        "routing": {},
                    }
                },
            ),
            (
                "routing",
                {
                    "rollout": {
                        "version": "v2",
                        "percent": 5,
                        "sticky_by": "session",
                        "routing": {"rollout": {}},
                    }
                },
            ),
            ("routing", {"agents": {"step_presets": {"dance": "fast"}}}),
            (
                "routing",
                {
                    "rollout": {
                        "version": "v2",
                        "percent": 5,
                        "sticky_by": "session",
                        "routing": {"agents": {"step_presets": {"dance": "fast"}}},
                    }
                },
            ),
            (
                "routing",
                {"rollout": {"version": "v2", "percent": 5, "sticky_by": "session"}},
            ),
            ("routing", {"auto": {"step_profiles": {"dance": {"min_tier": "simple"}}}}),
            ("routing", {"auto": {"step_profiles": {"plan": {"min_tier": "huge"}}}}),
            ("routing", {"data_policy": {"data_collection": "maybe"}}),
            ("routing", {"presets": {"p": {"models": ["a", "b", "c", "d", "e", "f"]}}}),
            ("routing", {"presets": {"p": {"provider": {"data_collection": "maybe"}}}}),
            ("routing", {"fastest": {"enabled": True, "max_hedges": 3}}),
            ("routing", {"auto": {"selector": {"mode": "shadow"}}}),
            ("routing", {"auto": {"preference": "fastest"}}),
            ("routing", {"retry_budget": {"max_retry_ratio": 1.5}}),
            ("routing", {"max_total_attempts": 17}),
            ("privacy", {"content_mode": "maybe"}),
            ("privacy", {"retention": {"request_log_days": 0}}),
            ("guardrails", {"tool_calls": {"on_block": "drop"}}),
            ("guardrails", {"tool_calls": {"apply_to": "everyone"}}),
            ("managed_models", {"enabled": True, "topup": {"min_usd": -1}}),
            ("managed_models", {"enabled": True, "markup_percent": 0}),
            ("rate_limiting", {"per_key_max_parallel": 0}),
        ],
    )
    def test_off_contract_values_are_rejected(self, section, value):
        with pytest.raises(ValidationError):
            GatewayOrgConfig.model_validate({section: value})

        serializer = AgentccOrgConfigWriteSerializer(data={section: value})
        assert not serializer.is_valid()
        assert section in serializer.errors

    @pytest.mark.parametrize(
        "section,value",
        [
            ("managed_models", {"enabled": True, "allow_free": "yes"}),
            ("managed_models", {"enabled": True, "max_call_cost_usd": True}),
            ("budgets", {"keys": {"k": {"limit": 1, "include_byok": "no"}}}),
            ("routing", {"idempotency": {"ttl_seconds": "3600"}}),
            ("privacy", {"retention": {"store_bodies": 0}}),
            (
                "routing",
                {
                    "rollout": {
                        "version": "v2",
                        "percent": "10",
                        "sticky_by": "key",
                        "routing": {},
                    }
                },
            ),
            (
                "routing",
                {
                    "rollout": {
                        "version": "v2",
                        "percent": 10,
                        "sticky_by": "key",
                        "routing": {"auto": {"max_output_per_mtok": True}},
                    }
                },
            ),
        ],
    )
    def test_v2_keys_are_typed_strictly_on_save(self, section, value):
        """The push-time contract coerces types (lax) so older saved configs
        keep validating; a save checks every v2 key's type exactly."""
        GatewayOrgConfig.model_validate({section: value})

        serializer = AgentccOrgConfigWriteSerializer(data={section: value})
        assert not serializer.is_valid()
        assert section in serializer.errors

    def test_rollout_candidate_keeps_the_lax_check_on_older_keys(self):
        """A rollout's candidate routing is a copy of another config's routing,
        which was saved under the lax check; re-saving it must not fail."""
        routing = {
            "rollout": {
                "version": "v7",
                "percent": 20,
                "sticky_by": "session",
                "routing": {"retry": {"enabled": True, "max_retries": "3"}},
            }
        }

        serializer = AgentccOrgConfigWriteSerializer(data={"routing": routing})

        assert serializer.is_valid(), serializer.errors
        GatewayOrgConfig.model_validate({"routing": routing})

    @patch("agentcc.services.config_push._assemble_providers", return_value={})
    def test_existing_configs_validate(self, _providers):
        GatewayOrgConfig.model_validate(_load(V1_FIXTURE))
        for stored in (
            {},
            {"routing": {"strategy": "weighted", "fallbackEnabled": True}},
            {
                "routing": {
                    "auto": {
                        "preference": "quality",
                        "candidates": ["a"],
                        "max_output_per_mtok": 20,
                    }
                }
            },
            {"managed_models": {"enabled": True}},
            {"privacy": {"enabled": True, "mode": "patterns", "redact_patterns": []}},
            {"rate_limiting": {"enabled": True, "per_key_rpm": 60}},
        ):
            serializer = AgentccOrgConfigWriteSerializer(data=stored)
            assert serializer.is_valid(), (stored, serializer.errors)
            validate_org_config(
                AgentccOrgConfig(version=1, **serializer.validated_data)
            )

    def test_topup_min_usd_defaults_to_25(self):
        admin = _load(CONTRACTS_DIR / "agentcc-admin.schema.json")
        declared = admin["$defs"]["ManagedTopupConfig"]["properties"]["min_usd"]

        assert declared["default"] == 25
        assert contract_default("ManagedTopupConfig", "min_usd") == 25

    def test_rollout_routing_is_routing_without_rollout(self):
        defs = _load(CONTRACTS_DIR / "agentcc-admin.schema.json")["$defs"]
        routing = defs["RoutingConfig"]["properties"]
        rollout_routing = defs["RolloutRoutingConfig"]["properties"]

        assert set(routing) - set(rollout_routing) == {"rollout"}
        assert all(rollout_routing[k] == routing[k] for k in rollout_routing)


class TestGuardrailTransformKeepsToolCalls:
    @pytest.mark.parametrize(
        "guardrails",
        [
            {"rules": [{"name": "pii-detector", "action": "block"}]},
            {"checks": [{"name": "pii-detector", "action": "block"}]},
            {"checks": {"pii-detector": {"enabled": True}}},
            {"enabled": True},
        ],
        ids=["rules-list", "checks-list", "checks-dict", "no-checks"],
    )
    def test_tool_calls_survive_every_shape(self, guardrails):
        tool_calls = {"enabled": True, "checks": ["pii-detection"], "on_block": "strip"}

        result = _transform_guardrails({**guardrails, "tool_calls": tool_calls})

        assert result["tool_calls"] == tool_calls


@pytest.mark.integration
@pytest.mark.api
class TestSaveRejectsPushOnlySecrets:
    @pytest.mark.parametrize(
        "payload",
        [
            {"managed_models": {"enabled": True, "upstream_keys": UPSTREAM_KEYS}},
            {"managed_models": {"enabled": True, "upstreamKeys": UPSTREAM_KEYS}},
            {
                "routing": {
                    "rollout": {
                        "version": "v2",
                        "percent": 10,
                        "sticky_by": "session",
                        "routing": {},
                        "managed_models": {
                            "enabled": True,
                            "upstream_keys": UPSTREAM_KEYS,
                        },
                    }
                }
            },
        ],
        ids=["snake", "camel", "rollout-overlay"],
    )
    @patch(SAVE_PUSH, return_value=True)
    def test_save_with_upstream_keys_returns_secret_in_config(
        self, _push, auth_client, user, payload
    ):
        before = AgentccOrgConfig.no_workspace_objects.filter(
            organization=user.organization
        ).count()

        response = auth_client.post("/agentcc/org-configs/", payload, format="json")

        assert response.status_code == 400, response.json()
        assert response.json()["code"] == "secret_in_config"
        assert (
            AgentccOrgConfig.no_workspace_objects.filter(
                organization=user.organization
            ).count()
            == before
        )

    @patch("agentcc.views.gateway.push_org_config", return_value=True)
    def test_update_config_with_upstream_keys_is_rejected(
        self, _push, auth_client, user
    ):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=user.organization,
            version=1,
            is_active=True,
            managed_models={"enabled": True},
        )

        response = auth_client.post(
            "/agentcc/gateways/default/update-config/",
            {"managed_models": {"upstream_keys": UPSTREAM_KEYS}},
            format="json",
        )

        assert response.status_code == 400
        rows = AgentccOrgConfig.no_workspace_objects.filter(
            organization=user.organization
        )
        assert rows.count() == 1
        assert rows.get().managed_models == {"enabled": True}

    def test_validate_org_config_names_the_secret(self):
        config = AgentccOrgConfig(
            version=1, managed_models={"enabled": True, "upstream_keys": UPSTREAM_KEYS}
        )

        with pytest.raises(SecretInConfig) as rejected:
            validate_org_config(config)

        assert rejected.value.code == "secret_in_config"
        assert "managed_models.upstream_keys" in str(rejected.value)
        assert UPSTREAM_KEYS["openrouter"]["api_key"] not in str(rejected.value)

    @patch(SAVE_PUSH, return_value=True)
    @patch(EE_CHECK, return_value=None)
    def test_full_v2_config_saves(self, _ee, _push, auth_client, user):
        response = auth_client.post(
            "/agentcc/org-configs/", _stored_sections(), format="json"
        )

        assert response.status_code == 200, response.json()
        config = AgentccOrgConfig.no_workspace_objects.get(
            organization=user.organization, is_active=True, deleted=False
        )
        assert config.routing["rollout"]["percent"] == 10
        assert config.managed_models["topup"] == {"min_usd": 50}
        validate_org_config(config)


@pytest.mark.integration
class TestPushTimeAdditions:
    @pytest.fixture
    def org_config(self, user):
        return AgentccOrgConfig.no_workspace_objects.create(
            organization=user.organization,
            version=3,
            is_active=True,
            managed_models={"enabled": True},
        )

    def _pushed_payload(self, org_config):
        client = MagicMock()
        with patch(GATEWAY_CLIENT, return_value=client):
            assert push_org_config(str(org_config.organization_id), org_config)
        client.set_org_config.assert_called_once()
        return client.set_org_config.call_args.args[1]

    def test_pushed_payload_meta_matches_config_version(self, org_config):
        payload = self._pushed_payload(org_config)

        assert payload["meta"]["version"] == org_config.version == 3
        assert payload["meta"]["config_id"] == str(org_config.id)
        assert datetime.fromisoformat(payload["meta"]["pushed_at"])

    def test_bulk_payload_meta_has_no_push_time(self, org_config):
        """The bulk sync builds the same payload on every poll; a timestamp
        there would make every org look changed to the gateway each time."""
        payload = _build_payload(str(org_config.organization_id), org_config)

        assert payload["meta"] == {"version": 3, "config_id": str(org_config.id)}

    def test_pushed_payload_carries_assembled_upstream_keys(self, org_config):
        assemble = MagicMock(return_value=copy.deepcopy(UPSTREAM_KEYS))
        module = _fake_module(UPSTREAM_KEYS_MODULE, assemble_for_push=assemble)

        with patch.dict(sys.modules, {UPSTREAM_KEYS_MODULE: module}):
            payload = self._pushed_payload(org_config)

        org_id = str(org_config.organization_id)
        assemble.assert_called_once_with(org_id)
        assert payload["managed_models"]["upstream_keys"] == UPSTREAM_KEYS
        stored = AgentccOrgConfig.no_workspace_objects.get(id=org_config.id)
        assert stored.managed_models == {"enabled": True}
        assert org_config.managed_models == {"enabled": True}
        rendered = json.dumps(AgentccOrgConfigSerializer(stored).data, default=str)
        assert "upstream_keys" not in rendered

    def test_upstream_keys_are_assembled_for_a_managed_rollout_candidate(self, user):
        config = AgentccOrgConfig.no_workspace_objects.create(
            organization=user.organization,
            version=4,
            is_active=True,
            routing={
                "rollout": {
                    "version": "v5",
                    "percent": 10,
                    "sticky_by": "session",
                    "routing": {},
                    "managed_models": {"enabled": True},
                }
            },
        )
        assemble = MagicMock(return_value=copy.deepcopy(UPSTREAM_KEYS))
        module = _fake_module(UPSTREAM_KEYS_MODULE, assemble_for_push=assemble)

        with patch.dict(sys.modules, {UPSTREAM_KEYS_MODULE: module}):
            payload = _build_payload(str(config.organization_id), config)

        assert payload["managed_models"]["upstream_keys"] == UPSTREAM_KEYS

    def test_upstream_keys_are_not_assembled_for_an_org_without_managed_models(
        self, user
    ):
        config = AgentccOrgConfig.no_workspace_objects.create(
            organization=user.organization, version=1, is_active=True
        )
        assemble = MagicMock(return_value=copy.deepcopy(UPSTREAM_KEYS))
        module = _fake_module(UPSTREAM_KEYS_MODULE, assemble_for_push=assemble)

        with patch.dict(sys.modules, {UPSTREAM_KEYS_MODULE: module}):
            payload = _build_payload(str(config.organization_id), config)

        assemble.assert_not_called()
        assert "upstream_keys" not in payload.get("managed_models", {})

    def test_without_the_upstream_key_service_the_payload_is_unchanged(
        self, org_config
    ):
        with patch.dict(sys.modules, {UPSTREAM_KEYS_MODULE: None}):
            payload = self._pushed_payload(org_config)

        assert payload["managed_models"] == {"enabled": True}

    def test_push_with_managed_enabled_ensures_account(
        self, org_config, user, django_capture_on_commit_callbacks
    ):
        ensure = MagicMock()
        credits = _fake_module(CREDITS_MODULE, ensure_account_pushed=ensure)

        # Opted in: called once, on commit, with the org.
        with patch.dict(sys.modules, {CREDITS_MODULE: credits}):
            with django_capture_on_commit_callbacks(execute=False) as callbacks:
                self._pushed_payload(org_config)
            ensure.assert_not_called()
            for callback in callbacks:
                callback()
        ensure.assert_called_once_with(user.organization)

        # Not opted in: never called.
        ensure.reset_mock()
        org_config.managed_models = {"enabled": False}
        with patch.dict(sys.modules, {CREDITS_MODULE: credits}):
            with django_capture_on_commit_callbacks(execute=True):
                self._pushed_payload(org_config)
        ensure.assert_not_called()

        # The ledger service is absent: the push is unchanged.
        org_config.managed_models = {"enabled": True}
        with patch.dict(sys.modules, {CREDITS_MODULE: None}):
            with django_capture_on_commit_callbacks(execute=True):
                payload = self._pushed_payload(org_config)
        assert payload["managed_models"] == {"enabled": True}

        # The bootstrap raising never fails the push.
        failing = _fake_module(
            CREDITS_MODULE,
            ensure_account_pushed=MagicMock(side_effect=RuntimeError("ledger down")),
        )
        with patch.dict(sys.modules, {CREDITS_MODULE: failing}):
            with django_capture_on_commit_callbacks(execute=True):
                self._pushed_payload(org_config)
        failing.ensure_account_pushed.assert_called_once()

    def test_failed_push_does_not_ensure_account(
        self, org_config, django_capture_on_commit_callbacks
    ):
        from agentcc.services.gateway_client import GatewayClientError

        ensure = MagicMock()
        client = MagicMock()
        client.set_org_config.side_effect = GatewayClientError("down", 503)
        credits = _fake_module(CREDITS_MODULE, ensure_account_pushed=ensure)

        with patch.dict(sys.modules, {CREDITS_MODULE: credits}):
            with patch(GATEWAY_CLIENT, return_value=client):
                with django_capture_on_commit_callbacks(execute=True):
                    assert not push_org_config(
                        str(org_config.organization_id), org_config
                    )

        ensure.assert_not_called()
