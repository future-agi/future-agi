"""Move only key-scoped MCP authority as part of an explicit key replacement."""

from copy import deepcopy
from functools import partial

from django.db import DEFAULT_DB_ALIAS, router, transaction

from accounts.models import Organization
from agentcc.models import AgentccOrgConfig
from agentcc.services import config_push, org_config_writer
from agentcc.services.key_issuance import KeyTransactionUnavailable


def lock_rotation_config(org, *, using):
    """Take the config writer's lock BEFORE locking the source key.

    The shared writer owns a default-database transaction. Refuse a split
    transaction rather than commit a config independently of its key/audit.
    """
    if (
        using != DEFAULT_DB_ALIAS
        or router.db_for_write(Organization, instance=org) != using
        or router.db_for_write(AgentccOrgConfig) != using
        or not transaction.get_connection(using).in_atomic_block
    ):
        raise KeyTransactionUnavailable("Key transaction unavailable")
    org_config_writer.lock_org(org.pk)


def replace_mcp_key(mcp, old_id, new_id):
    """Copy exact ID matches, keeping list order, narrowing and other fields.

    Include both spellings the gateway contract accepts, without normalizing
    stored JSON or rewriting user/agent IDs that happen to equal a key ID.
    """
    result = deepcopy(mcp)
    if not isinstance(result, dict) or not old_id or old_id == new_id:
        return result
    for server_field in ("virtual_servers", "virtualServers"):
        servers = result.get(server_field)
        if not isinstance(servers, dict):
            continue
        for server in servers.values():
            access = server.get("access") if isinstance(server, dict) else None
            if not isinstance(access, dict):
                continue
            _replace_ids(access, "keys", old_id, new_id)
            rules = access.get("rules")
            if not isinstance(rules, list):
                continue
            for rule in rules:
                match = rule.get("match") if isinstance(rule, dict) else None
                if isinstance(match, dict):
                    for key_field in ("key_ids", "keyIds"):
                        _replace_ids(match, key_field, old_id, new_id)
    return result


def _replace_ids(holder, field, old_id, new_id):
    ids = holder.get(field)
    if isinstance(ids, list):
        holder[field] = [new_id if item == old_id else item for item in ids]


def stage_mcp_rotation(org, old_id, new_id, *, user, using):
    """Under lock_rotation_config, version grants in the key's transaction.

    Return a post-commit prerequisite for issuance, or None if no grants/rules
    refer to the retired ID. Never push a version that may still roll back.
    """
    active = (
        AgentccOrgConfig.no_workspace_objects.using(using).select_for_update()
        .filter(organization=org, is_active=True, deleted=False).first()
    )
    if active is None:
        return None
    migrated = replace_mcp_key(active.mcp, old_id, new_id)
    if migrated == active.mcp:
        return None

    def migrate(config):
        config.mcp = replace_mcp_key(config.mcp, old_id, new_id)

    org_config_writer.write_config_version_with(
        org, migrate, user=user, description="MCP access carried to replacement key",
        # False truthfully marks this write unsynced. The issuance prerequisite
        # below, not the writer's warning list, must obtain the actual ACK.
        push=lambda _: False,
    )
    return partial(_push_current_config, org, using=using)


def _push_current_config(org, *, using):
    """Push the latest committed version under the normal config-write lock.

    A config edit can win the lock between rotation's commit and this callback;
    never overwrite it at the gateway with the earlier rotation snapshot.
    """
    with transaction.atomic(using=using):
        lock_rotation_config(org, using=using)
        current = (
            AgentccOrgConfig.no_workspace_objects.using(using).select_for_update()
            .filter(organization=org, is_active=True, deleted=False).first()
        )
        return (
            current is not None
            and config_push.push_org_config(str(org.pk), current) is True
        )
