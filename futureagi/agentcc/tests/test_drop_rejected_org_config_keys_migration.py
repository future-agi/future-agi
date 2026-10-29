"""The 0028 data migration repairs stored org configs the gateway contract rejects."""

import importlib
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.apps import apps as django_apps

from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services.config_push import validate_org_config

migration = importlib.import_module(
    "agentcc.migrations.0028_drop_rejected_org_config_keys"
)

# One stored config holding every key the dashboard used to write and the
# gateway contract rejects.
REJECTED_EVERYWHERE = {
    "routing": {"default_strategy": "least-latency", "policies": {"p": {}}},
    "budgets": {
        "org_limit": {"limit": 100, "on_exceed": "warn"},
        "per_key": {"limit": 10, "on_exceed": "warn"},
    },
    "mcp": {
        "guardrails": {"enabled": True, "custom_patterns": []},
        "servers": {
            "local": {"transport": "stdio", "command": "npx", "args": ["-y", "x"]},
            "remote": {"transport": "http", "url": "http://mcp"},
        },
    },
    "alerting": {
        "rules": {
            "Error Rate - Warn": {
                "name": "Error Rate - Warn",
                "metric": "error_rate",
                "condition": ">",
                "threshold": 10,
                "window": "5m",
                "severity": "warning",
                "enabled": True,
            }
        }
    },
}


def _stored(routing=None, budgets=None, mcp=None, alerting=None):
    return SimpleNamespace(routing=routing, budgets=budgets, mcp=mcp, alerting=alerting)


class TestRepair:
    @pytest.mark.parametrize(
        "legacy,expected",
        [
            ("round-robin", "round_robin"),
            ("least-latency", "least_latency"),
            ("weighted", "weighted"),
        ],
    )
    def test_carries_a_known_strategy_into_the_contract_field(self, legacy, expected):
        config = _stored(routing={"default_strategy": legacy, "failover": {}})

        assert migration._repair(config) == ["routing"]
        assert config.routing == {"strategy": expected, "failover": {}}

    @pytest.mark.parametrize("legacy", ["priority", "random", None, 7])
    def test_drops_a_strategy_with_no_per_org_name(self, legacy):
        config = _stored(routing={"default_strategy": legacy})

        assert migration._repair(config) == ["routing"]
        assert config.routing == {}

    def test_keeps_a_strategy_that_is_already_set(self):
        config = _stored(
            routing={"default_strategy": "least-latency", "strategy": "weighted"}
        )

        migration._repair(config)

        assert config.routing == {"strategy": "weighted"}

    def test_drops_synced_routing_policies(self):
        config = _stored(routing={"policies": {"fastest": {}}, "strategy": "weighted"})

        assert migration._repair(config) == ["routing"]
        assert config.routing == {"strategy": "weighted"}

    def test_drops_every_budget_level_the_gateway_has_no_field_for(self):
        kept = {
            "enabled": True,
            "org_limit": {"limit": 100, "on_exceed": "warn"},
            "hardLimit": {"limit": 150},
            "teams": {"platform": {"limit": 20, "period": "monthly"}},
        }
        config = _stored(
            budgets={
                **kept,
                "per_key": {"limit": 1},
                "per_user": {"limit": 2},
                "per_model": {"limit": 3},
                "perUser": {"limit": 4},
                "100": {"limit": 5},
            }
        )

        assert migration._repair(config) == ["budgets"]
        assert config.budgets == kept

    def test_drops_custom_patterns_and_keeps_the_other_guardrail_settings(self):
        config = _stored(
            mcp={"guardrails": {"enabled": True, "custom_patterns": ["a.*"]}}
        )

        assert migration._repair(config) == ["mcp"]
        assert config.mcp == {"guardrails": {"enabled": True}}

    def test_drops_stdio_servers_and_keeps_http_servers(self):
        http = {"transport": "http", "url": "http://mcp"}
        config = _stored(
            mcp={
                "servers": {
                    "by-transport": {"transport": "stdio"},
                    "by-command": {"command": "npx", "args": ["x"]},
                    "remote": http,
                }
            }
        )

        assert migration._repair(config) == ["mcp"]
        assert config.mcp == {"servers": {"remote": http}}

    def test_turns_keyed_rules_and_channels_into_lists(self):
        config = _stored(
            alerting={
                "enabled": True,
                "rules": {"r1": {"metric": "error_count", "severity": "info"}},
                "channels": {"ops": {"type": "webhook", "url": "http://hook"}},
            }
        )

        assert migration._repair(config) == ["alerting"]
        assert config.alerting == {
            "enabled": True,
            "rules": [{"name": "r1", "metric": "error_count", "severity": "info"}],
            "channels": [{"name": "ops", "type": "webhook", "url": "http://hook"}],
        }

    def test_reports_every_field_it_changed(self):
        config = _stored(**{k: dict(v) for k, v in REJECTED_EVERYWHERE.items()})

        assert migration._repair(config) == ["routing", "budgets", "mcp", "alerting"]

    @pytest.mark.parametrize(
        "fields",
        [
            {},
            {"routing": {}, "budgets": {}, "mcp": {}, "alerting": {}},
            {
                "routing": {"strategy": "round_robin"},
                "budgets": {"org_limit": {"limit": 5}},
                "mcp": {
                    "guardrails": {"enabled": True},
                    "servers": {"remote": {"transport": "http", "url": "u"}},
                },
                "alerting": {"rules": [{"name": "r", "metric": "error_count"}]},
            },
            {"routing": [], "budgets": "x", "mcp": 3, "alerting": []},
            {"mcp": {"guardrails": None, "servers": []}, "alerting": {"rules": None}},
        ],
    )
    def test_leaves_a_clean_or_unexpected_row_alone(self, fields):
        config = _stored(**fields)
        before = repr(vars(config))

        assert migration._repair(config) == []
        assert repr(vars(config)) == before


@pytest.mark.integration
class TestDropRejectedKeys:
    def test_repairs_every_stored_version_and_only_those(self, organization):
        active = AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=3, is_active=True, **REJECTED_EVERYWHERE
        )
        inactive = AgentccOrgConfig.no_workspace_objects.create(
            organization=organization,
            version=2,
            is_active=False,
            routing={"default_strategy": "priority"},
        )
        deleted = AgentccOrgConfig.no_workspace_objects.create(
            organization=organization,
            version=1,
            is_active=False,
            deleted=True,
            budgets={"per_model": {"limit": 3}},
        )
        clean = AgentccOrgConfig.no_workspace_objects.create(
            organization=organization,
            version=4,
            is_active=False,
            routing={"strategy": "weighted"},
            mcp={"servers": {"remote": {"transport": "http", "url": "http://mcp"}}},
        )
        clean_updated_at = clean.updated_at

        migration.drop_rejected_keys(django_apps, None)

        rows = {
            row.version: row
            for row in AgentccOrgConfig._base_manager.filter(organization=organization)
        }
        repaired = rows[active.version]
        assert repaired.routing == {"strategy": "least_latency"}
        assert repaired.budgets == {"org_limit": {"limit": 100, "on_exceed": "warn"}}
        assert repaired.mcp == {
            "guardrails": {"enabled": True},
            "servers": {"remote": {"transport": "http", "url": "http://mcp"}},
        }
        assert repaired.alerting == {
            "rules": [
                {
                    "name": "Error Rate - Warn",
                    "metric": "error_rate",
                    "condition": ">",
                    "threshold": 10,
                    "window": "5m",
                    "severity": "warning",
                    "enabled": True,
                }
            ]
        }
        assert rows[inactive.version].routing == {}
        assert rows[deleted.version].budgets == {}
        assert rows[clean.version].routing == {"strategy": "weighted"}
        assert rows[clean.version].updated_at == clean_updated_at

    @patch("agentcc.services.config_push._assemble_providers", return_value={})
    def test_a_repaired_row_passes_the_gateway_contract(self, _providers, organization):
        config = AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True, **REJECTED_EVERYWHERE
        )

        migration.drop_rejected_keys(django_apps, None)

        config.refresh_from_db()
        validate_org_config(config)

    def test_a_second_run_changes_nothing(self, organization):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True, **REJECTED_EVERYWHERE
        )

        migration.drop_rejected_keys(django_apps, None)
        first = AgentccOrgConfig._base_manager.filter(organization=organization).values(
            "routing", "budgets", "mcp", "alerting"
        )[0]
        migration.drop_rejected_keys(django_apps, None)
        second = AgentccOrgConfig._base_manager.filter(
            organization=organization
        ).values("routing", "budgets", "mcp", "alerting")[0]

        assert first == second
