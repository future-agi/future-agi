"""The one writer for org-config versions.

Every new version is written here: lock the org, copy its active version,
apply the change, validate the result against the gateway contract, save and
activate it, then push it to the gateway, all in one transaction. A version
the contract rejects never becomes active. The org row lock serializes every
writer and activation for one org, so a second writer waits for the first to
commit and builds on its version.

Two patch modes:

- ``merge``: deep merge into the active version. ``None`` deletes a key (a
  ``None`` section is cleared to its default), and an absent key is kept.
- ``replace_sections``: each section the patch names replaces the active one
  whole (``None`` or ``{}`` clears it); sections it leaves out are copied.
"""

import copy
from collections.abc import Callable, Mapping
from typing import Any, TypedDict

import structlog
from django.db import transaction
from django.db.models import Max

from accounts.models import Organization, User
from agentcc.models import AgentccOrgConfig
from agentcc.org_config_defaults import normalize_cost_tracking_config
from agentcc.services import config_push, guardrail_urls
from agentcc.services.guardrail_env_refs import reject_pushed_guardrail_env_references
from agentcc.services.routing_key_rules import (
    check_routing_keys,
    routing_key_warnings,
)

logger = structlog.get_logger(__name__)

MERGE = "merge"
REPLACE_SECTIONS = "replace_sections"
MODES = (MERGE, REPLACE_SECTIONS)

CONFIG_SECTIONS = (
    "guardrails",
    "routing",
    "cache",
    "rate_limiting",
    "budgets",
    "cost_tracking",
    "ip_acl",
    "alerting",
    "privacy",
    "tool_policy",
    "mcp",
    "a2a",
    "audit",
    "model_database",
    "model_map",
    "managed_models",
    "tools",
    "identity",
)

MANAGED_MODELS_CHANGE = "managed_models_change"
GATEWAY_SYNC_FAILED = "gateway_sync_failed"
GATEWAY_SYNC_FAILED_MESSAGE = (
    "Config saved but gateway sync failed. Changes will apply on next gateway restart."
)

# ``from`` is a keyword, so the functional form is the only way to name it.
# ``param`` names the config key a routing-key warning is about
# (routing_key_rules).
ConfigWarning = TypedDict(
    "ConfigWarning",
    {"code": str, "from": bool, "to": bool, "message": str, "param": str},
    total=False,
)

Push = Callable[[AgentccOrgConfig], bool]
Updater = Callable[[AgentccOrgConfig], None]


class UnknownConfigSections(ValueError):
    def __init__(self, sections):
        self.sections = tuple(sorted(sections))
        super().__init__(f"Unknown config fields: {', '.join(self.sections)}")


class MCPConfigRejected(config_push.OrgConfigRejected):
    """A write's mcp section breaks a rule validate_mcp states: the writer
    reruns those rules under the org lock, for every write path. ``code`` is
    the rule's own code when it has one (vs_id_immutable,
    forward_header_refused)."""

    def __init__(self, message: str, code: str | None = None):
        if code:
            self.code = code
        super().__init__(message)


class ToolsConfigRejected(config_push.OrgConfigRejected):
    """A write's tools section breaks a rule validate_tools states: the writer
    reruns those rules under the org lock, for every write path."""


class ConfigVersionUnavailable(ValueError):
    """The version to activate was deleted, so it cannot become active."""

    def __init__(self, config: AgentccOrgConfig):
        self.config_id = config.pk
        super().__init__(
            f"Config version {config.version} was deleted and cannot be activated."
        )


def write_config_version(
    org: Organization,
    patch: Mapping[str, Any],
    *,
    mode: str = MERGE,
    description: str = "",
    user: User | None = None,
    push: Push | None = None,
    prepare: Updater | None = None,
) -> tuple[AgentccOrgConfig, list[ConfigWarning]]:
    """Write a new active version: the active one with ``patch`` applied.

    ``push`` replaces the default gateway push (``config_push.push_org_config``);
    it takes the saved version and returns whether the gateway accepted it.
    ``prepare`` edits the patched copy before it is validated, inside the
    write's transaction and org lock, so its own writes roll back with a
    rejected version.
    Raises ``OrgConfigRejected`` when the gateway contract rejects the result.
    """
    if mode not in MODES:
        raise ValueError(f"Unknown write mode {mode!r}; use one of {MODES}")
    if not isinstance(patch, Mapping):
        raise TypeError("A config patch must be a JSON object")
    unknown = set(patch) - set(CONFIG_SECTIONS)
    if unknown:
        raise UnknownConfigSections(unknown)

    def apply(config: AgentccOrgConfig) -> None:
        for section, value in patch.items():
            setattr(config, section, _patched_section(config, section, value, mode))
        if prepare is not None:
            prepare(config)

    return write_config_version_with(
        org, apply, description=description, user=user, push=push
    )


def write_config_version_with(
    org: Organization,
    updater: Updater,
    *,
    description: str = "",
    user: User | None = None,
    push: Push | None = None,
) -> tuple[AgentccOrgConfig, list[ConfigWarning]]:
    """Write a new active version that ``updater`` edits in place.

    ``updater`` gets an unsaved copy of the active version (or a default
    version when the org has none) and may change any section.
    Raises ``UnsupportedRoutingKey`` (an ``OrgConfigRejected``) when the
    result holds a new or changed routing key the gateway never runs
    (routing_key_rules).
    """
    with transaction.atomic():
        lock_org(org.pk)
        active = (
            AgentccOrgConfig.no_workspace_objects.select_for_update()
            .filter(organization=org, is_active=True, deleted=False)
            .first()
        )
        # The active version is not always the highest one: after a rollback an
        # older version is active. The org lock serializes writers, so the max
        # is stable here.
        version = (
            AgentccOrgConfig.no_workspace_objects.filter(
                organization=org, deleted=False
            ).aggregate(Max("version"))["version__max"]
            or 0
        ) + 1
        config = _copy_of(active, org)
        config.version = version
        config.is_active = True
        config.created_by = user
        config.change_description = description or f"Config update v{version}"
        updater(config)
        config.mcp = _enforced_mcp(active, config)
        _enforce_tools(active, config)
        _encrypt_secrets(active, config)

        routing_warnings = check_routing_keys(active, config)
        config_push.validate_org_config(config)
        # A save that changes guardrails may not switch on (or newly push) a
        # check holding "${" in a credential, URL or other provider setting; a
        # save that leaves guardrails alone is never refused for one
        # (guardrail_env_refs).
        if config.guardrails != _stored_section(active, "guardrails"):
            reject_pushed_guardrail_env_references(config, active)
        # A guardrail check's callback URL (url/endpoint/base_url) and an alert
        # channel's URL must be well-formed before the gateway dials them. This
        # is the one chokepoint every config write passes (update-config, POST
        # org-configs, toggle- and update-guardrail): only a URL this save
        # introduces or changes is checked, so a save that leaves a stored URL
        # alone — including turning that very check off — is never blocked, and
        # the egress guard still refuses the address at runtime.
        guardrail_urls.reject_changed_bad_guardrail_urls(
            config.guardrails, _stored_section(active, "guardrails")
        )
        guardrail_urls.reject_changed_bad_alerting_urls(
            config.alerting, _stored_section(active, "alerting")
        )
        if active is not None:
            AgentccOrgConfig.no_workspace_objects.filter(
                organization=org, is_active=True, deleted=False
            ).update(is_active=False)
        config.save()

        warnings = _managed_models_change(active, config)
        warnings.extend(routing_warnings)
        warnings.extend(_push_to_gateway(config, push))
    return config, warnings


def activate_config_version(
    config: AgentccOrgConfig, *, push: Push | None = None
) -> tuple[AgentccOrgConfig, list[ConfigWarning]]:
    """Make an existing version the active one and push it.

    Raises ``ConfigVersionUnavailable`` when the version was deleted, and
    ``OrgConfigRejected`` when it holds keys the gateway contract does not
    accept. With no active version before it, the managed-models warning
    compares against managed models off.
    """
    org_id = config.organization_id
    with transaction.atomic():
        lock_org(org_id)
        target = (
            AgentccOrgConfig.no_workspace_objects.select_for_update()
            .filter(pk=config.pk, organization_id=org_id, deleted=False)
            .first()
        )
        if target is None:
            raise ConfigVersionUnavailable(config)
        config = target
        active = (
            AgentccOrgConfig.no_workspace_objects.select_for_update()
            .filter(organization_id=org_id, is_active=True, deleted=False)
            .first()
        )
        if active is not None and active.pk == config.pk:
            return active, []

        config_push.validate_org_config(config)
        # Activation answers 400 naming the field rather than push a check
        # that would refuse every request (guardrail_env_refs).
        reject_pushed_guardrail_env_references(config)
        AgentccOrgConfig.no_workspace_objects.filter(
            organization_id=org_id, is_active=True, deleted=False
        ).update(is_active=False)
        config.is_active = True
        config.save(update_fields=["is_active", "updated_at"])

        # An activation writes no new value, so it is never refused, but it
        # carries the routing-key warnings of the version it activates.
        warnings = _managed_models_change(active, config)
        warnings.extend(routing_key_warnings(config))
        warnings.extend(_push_to_gateway(config, push))
    return config, warnings


def lock_org(org_id: Any) -> None:
    """Take the org's config-write lock for the rest of the transaction.

    Every config writer takes it first, before reading the active version,
    and a caller that locks other rows in the same transaction before it
    writes (a guardrail policy, say) calls this first, so every path takes
    the org lock before any other. Locking the active row instead does not
    serialize writers: once the first commits, the row does not match
    ``is_active=True``, so the second reads no active version and its insert
    breaks the one-active-version constraint.

    The lock is FOR NO KEY UPDATE on the Organization row. It conflicts with
    itself and with FOR UPDATE, so writers still run one at a time, but not
    with the FOR KEY SHARE that inserting a row with a foreign key to the org
    takes. The write holds it across the gateway push, and request logs and
    other org rows keep being inserted meanwhile.
    """
    Organization.objects.select_for_update(no_key=True).only("pk").get(pk=org_id)


def is_gateway_synced(warnings: list[ConfigWarning]) -> bool:
    return all(warning["code"] != GATEWAY_SYNC_FAILED for warning in warnings)


def deep_merge(base: Mapping[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    """Merge ``patch`` into a copy of ``base``. ``None`` deletes a key, including
    inside a dict ``base`` does not have yet: a stored ``None`` would fail the
    section's validation on every later save."""
    merged = copy.deepcopy(dict(base))
    for key, value in patch.items():
        if value is None:
            merged.pop(key, None)
        elif isinstance(value, Mapping):
            current = merged.get(key)
            merged[key] = deep_merge(
                current if isinstance(current, Mapping) else {}, value
            )
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _patched_section(
    config: AgentccOrgConfig, section: str, value: Any, mode: str
) -> Any:
    if value is None:
        return _cleared_section(section)
    if mode == MERGE and isinstance(value, Mapping):
        current = getattr(config, section)
        return deep_merge(current if isinstance(current, Mapping) else {}, value)
    return copy.deepcopy(value)


def _stored_section(active: AgentccOrgConfig | None, section: str) -> Any:
    """``section`` as the active version stores it, or its default when the
    org has no active version yet (which tells what a save changes)."""
    if active is None:
        return _cleared_section(section)
    return getattr(active, section)


def _cleared_section(section: str) -> Any:
    return AgentccOrgConfig._meta.get_field(section).get_default()


def _copy_of(active: AgentccOrgConfig | None, org: Organization) -> AgentccOrgConfig:
    if active is None:
        return AgentccOrgConfig(organization=org)
    sections = {
        section: copy.deepcopy(getattr(active, section)) for section in CONFIG_SECTIONS
    }
    sections["cost_tracking"] = normalize_cost_tracking_config(
        sections["cost_tracking"]
    )
    return AgentccOrgConfig(organization=org, **sections)


def _enforced_mcp(active: AgentccOrgConfig | None, config: AgentccOrgConfig) -> Any:
    """``config.mcp`` with validate_mcp's rules applied against the locked
    active version, so no write path can change a virtual server id, add a
    stdio or credential-bearing server, or forward a refused header. An
    unchanged section is left alone."""
    from rest_framework import serializers

    from agentcc.serializers.org_config import enforce_mcp_write

    stored = active.mcp if active is not None else {}
    if config.mcp == stored:
        return config.mcp
    try:
        return enforce_mcp_write(config.mcp, stored)
    except serializers.ValidationError as exc:
        detail = exc.detail[0] if isinstance(exc.detail, list) else exc.detail
        raise MCPConfigRejected(str(detail), getattr(detail, "code", None)) from exc


def _encrypt_secrets(active: AgentccOrgConfig | None, config: AgentccOrgConfig) -> None:
    """Store every infrastructure secret of ``config`` encrypted, keeping a
    stored one on a blank or a read-back mask and refusing to carry one to a
    host the URL just moved to. Runs on every write path, under the org lock,
    so update-config, POST org-configs/ and the MCP, budget and other
    single-section actions all encrypt a new secret and none stores one in
    clear."""
    from agentcc.services.org_config_secrets import encrypt_config_secrets

    encrypt_config_secrets(config, active)


def _enforce_tools(active: AgentccOrgConfig | None, config: AgentccOrgConfig) -> None:
    """Refuse a changed ``config.tools`` that validate_tools would refuse, so
    update-config cannot store what POST org-configs/ refuses. The byok_only
    managed-glob refusal is an mcp rule, rerun by _enforced_mcp."""
    from rest_framework import serializers

    from agentcc.serializers.org_config import enforce_tools_write

    stored = active.tools if active is not None else {}
    if config.tools == stored:
        return
    try:
        enforce_tools_write(config.tools)
    except serializers.ValidationError as exc:
        details = exc.detail if isinstance(exc.detail, list) else [exc.detail]
        raise ToolsConfigRejected("; ".join(str(d) for d in details)) from exc


def _managed_models_enabled(config: AgentccOrgConfig | None) -> bool:
    managed = config.managed_models if config is not None else None
    return isinstance(managed, Mapping) and managed.get("enabled") is True


def _managed_models_change(
    before: AgentccOrgConfig | None, after: AgentccOrgConfig
) -> list[ConfigWarning]:
    was_enabled = _managed_models_enabled(before)
    is_enabled = _managed_models_enabled(after)
    if was_enabled == is_enabled:
        return []
    return [{"code": MANAGED_MODELS_CHANGE, "from": was_enabled, "to": is_enabled}]


def _push_to_gateway(
    config: AgentccOrgConfig, push: Push | None
) -> list[ConfigWarning]:
    # A failed push keeps the saved version (fail open): the gateway pulls
    # every active config on its next sync. Every push callable reports
    # failure by returning False, so an exception here is a bug, and it rolls
    # the whole write back.
    if push is None:
        synced = config_push.push_org_config(str(config.organization_id), config)
    else:
        synced = push(config)
    if synced:
        return []
    logger.warning(
        "org_config_push_failed",
        org_id=str(config.organization_id),
        version=config.version,
    )
    return [{"code": GATEWAY_SYNC_FAILED, "message": GATEWAY_SYNC_FAILED_MESSAGE}]
