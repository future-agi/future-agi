"""
Repair stored org configs that hold keys the gateway contract rejects.

Several dashboard screens wrote keys the per-org gateway contract does not
have. A config that holds one fails gateway sync, and a save that carries it
forward is now rejected, so stored rows are repaired here.

- Providers routing view: `routing.default_strategy`. The contract field is
  `strategy`.
- Routing policy sync: `routing.policies`.
- Budgets dialog: a level the per-org config has no field for (`per_key`,
  `per_user`, `per_model`, or any other name sent as the level).
- MCP guardrails tab: `mcp.guardrails.custom_patterns`, a gateway-wide setting.
- MCP server dialog: stdio servers. The per-org config has no command field.
- Monitoring alert dialogs: rules and channels stored as name-keyed objects,
  where the Settings editor expects lists.
"""

from django.db import migrations

# Providers-view names -> the per-org names the Settings routing tab writes.
# "random" and "priority" have no per-org strategy and are dropped.
_PER_ORG_STRATEGY = {
    "round-robin": "round_robin",
    "weighted": "weighted",
    "least-latency": "least_latency",
}
# Top-level budget keys the gateway contract accepts, as of this migration.
# set-budget stored the level name as a top-level key, so any other key is a
# level with no per-org field.
_BUDGET_KEYS = frozenset(
    {
        "enabled",
        "default_period",
        "defaultPeriod",
        "warn_threshold",
        "warnThreshold",
        "org_limit",
        "orgLimit",
        "org_period",
        "orgPeriod",
        "hard_limit",
        "hardLimit",
        "action",
        "action_mode",
        "actionMode",
        "on_exceed",
        "onExceed",
        "organization",
        "org",
        "teams",
        "users",
        "keys",
        "tags",
    }
)


def _repair_routing(routing):
    if not isinstance(routing, dict):
        return False
    changed = False
    if "default_strategy" in routing:
        legacy = routing.pop("default_strategy")
        if "strategy" not in routing and legacy in _PER_ORG_STRATEGY:
            routing["strategy"] = _PER_ORG_STRATEGY[legacy]
        changed = True
    if "policies" in routing:
        del routing["policies"]
        changed = True
    return changed


def _repair_budgets(budgets):
    if not isinstance(budgets, dict):
        return False
    rejected = [key for key in budgets if key not in _BUDGET_KEYS]
    for key in rejected:
        del budgets[key]
    return bool(rejected)


def _repair_mcp(mcp):
    if not isinstance(mcp, dict):
        return False
    changed = False
    guardrails = mcp.get("guardrails")
    if isinstance(guardrails, dict) and "custom_patterns" in guardrails:
        del guardrails["custom_patterns"]
        changed = True
    servers = mcp.get("servers")
    if isinstance(servers, dict):
        stdio = [
            name
            for name, server in servers.items()
            if isinstance(server, dict)
            and (server.get("transport") == "stdio" or "command" in server)
        ]
        for name in stdio:
            del servers[name]
        changed = changed or bool(stdio)
    return changed


def _as_list(entries):
    """Name-keyed object -> list of entries that carry their name."""
    return [
        {"name": name, **entry} if isinstance(entry, dict) else {"name": name}
        for name, entry in entries.items()
    ]


def _repair_alerting(alerting):
    if not isinstance(alerting, dict):
        return False
    changed = False
    for key in ("rules", "channels"):
        if isinstance(alerting.get(key), dict):
            alerting[key] = _as_list(alerting[key])
            changed = True
    return changed


_REPAIRS = {
    "routing": _repair_routing,
    "budgets": _repair_budgets,
    "mcp": _repair_mcp,
    "alerting": _repair_alerting,
}


def _repair(config):
    """Repair `config` in place; return the model fields that changed."""
    return [
        field for field, repair in _REPAIRS.items() if repair(getattr(config, field))
    ]


def drop_rejected_keys(apps, schema_editor):
    AgentccOrgConfig = apps.get_model("prism", "AgentccOrgConfig")
    # Every stored version, not only the active one: an old version can be
    # activated again. _base_manager skips the soft-delete and workspace filters.
    for config in AgentccOrgConfig._base_manager.iterator():
        changed = _repair(config)
        if changed:
            config.save(update_fields=changed)


class Migration(migrations.Migration):

    dependencies = [
        ("prism", "0027_request_log_metadata_gin"),
    ]

    operations = [
        migrations.RunPython(
            drop_rejected_keys,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
