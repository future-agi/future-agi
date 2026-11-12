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
from agentcc.contracts.gateway_admin import BudgetsConfig
from agentcc.models import AgentccOrgConfig
from agentcc.org_config_defaults import normalize_cost_tracking_config
from agentcc.services import cap_ledger_status, config_push, guardrail_urls
from agentcc.services.byok_unpriced_consent import (
    audit_unpriced_change,
    require_unpriced_admin,
)
from agentcc.services.cap_activation import stamp_cap_activation
from agentcc.services.cap_ledger_status import require_cap_ledger_for_activation
from agentcc.services.cap_revision import cap_patch_under_lock
from agentcc.services.config_sync_receipts import collect_sync_warnings
from agentcc.services.guardrail_env_refs import reject_pushed_guardrail_env_references
from agentcc.services.keyword_blocklist_rules import (
    reject_new_unknown_keys,
    section_blocklist_configs,
)
from agentcc.services.managed_models_default import initial_managed_models
from agentcc.services.model_fallback_bounds import collapse_stored_fallback_aliases
from agentcc.services.routing_key_rules import (
    check_routing_keys,
    routing_key_warnings,
)
from agentcc.services.semantic_cache_rules import check_semantic_cache
from agentcc.services.trace_content_consent import (
    audit_capture_change,
    require_capture_admin,
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
GATEWAY_SYNC_FAILED_MESSAGE = "Config saved but gateway sync failed. A gateway connected to this app retries fetching saved configs during its periodic sync."

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
    reruns those rules under the org lock,
    for every write path. ``code`` is the rule's own code when it has one
    (vs_id_immutable, forward_header_refused)."""

    def __init__(self, message: str, code: str | None = None):
        if code:
            self.code = code
        super().__init__(message)


class ToolsConfigRejected(config_push.OrgConfigRejected):
    """A write's tools section breaks a rule validate_tools states (W2c
    integration, V4 IR-5): the writer reruns those rules under the org lock,
    for every write path."""


class ConfigVersionUnavailable(ValueError):
    """The version to activate was deleted, so it cannot become active."""

    def __init__(self, config: AgentccOrgConfig):
        self.config_id = config.pk
        super().__init__(
            f"Config version {config.version} was deleted and cannot be activated."
        )


def preflight_cap_ledger_for_patch(
    org: Organization, patch: Mapping[str, Any], *, mode: str = MERGE
) -> str:
    """Preview only cap flags, with no locks, CAS checks or prepare side effects.

    Callers that already wrap the writer in a transaction must call this before
    taking their org lock and pass the result as ``ledger_status``.
    """
    active = AgentccOrgConfig.no_workspace_objects.filter(
        organization=org, is_active=True, deleted=False
    ).first()
    candidate = AgentccOrgConfig(
        organization=org,
        budgets=BudgetsConfig._normalize_input_aliases(
            active.budgets or {} if active is not None else {}
        ),
    )
    if "budgets" in patch:
        incoming = BudgetsConfig._normalize_input_aliases(patch["budgets"])
        candidate.budgets = _patched_section(candidate, "budgets", incoming, mode)
    return cap_ledger_status.preflight_cap_ledger_for_activation(active, candidate)


def write_config_version(
    org: Organization,
    patch: Mapping[str, Any],
    *,
    mode: str = MERGE,
    description: str = "",
    user: User | None = None,
    push: Push | None = None,
    prepare: Updater | None = None,
    ledger_status: str | None = None,
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
    if ledger_status is None:
        ledger_status = preflight_cap_ledger_for_patch(org, patch, mode=mode)

    def apply(config: AgentccOrgConfig) -> None:
        checked = cap_patch_under_lock(
            config, patch, replace_sections=mode == REPLACE_SECTIONS
        )
        for section, value in checked.items():
            setattr(config, section, _patched_section(config, section, value, mode))
        if prepare is not None:
            prepare(config)

    return write_config_version_with(
        org,
        apply,
        description=description,
        user=user,
        push=push,
        ledger_status=ledger_status,
    )


def write_config_version_with(
    org: Organization,
    updater: Updater,
    *,
    description: str = "",
    user: User | None = None,
    push: Push | None = None,
    ledger_status: str | None = None,
) -> tuple[AgentccOrgConfig, list[ConfigWarning]]:
    """Write a new active version that ``updater`` edits in place.

    ``updater`` gets an unsaved copy of the active version (or a default
    version when the org has none) and may change any section.
    Raises ``UnsupportedRoutingKey`` (an ``OrgConfigRejected``) when the
    result holds a new or changed routing key the gateway never runs
    (routing_key_rules), ``SemanticCacheNotSupported`` for a new
    or changed cache.semantic (semantic_cache_rules) and
    ``KeywordBlocklistUnknownKey`` for a new or changed keyword-blocklist key
    the check never reads (keyword_blocklist_rules).
    """
    if ledger_status is None:
        # Unlike a patch, a callback cannot be previewed safely. Probe before
        # locking only if caps could turn on, and never inside a caller's outer
        # transaction. A nonactivating callback is unaffected by unknown status.
        preview = AgentccOrgConfig.no_workspace_objects.filter(
            organization=org, is_active=True, deleted=False
        ).first()
        ledger_status = "unknown"
        if not cap_ledger_status.caps_enabled(preview):
            ledger_status = cap_ledger_status.get_ledger_status(
                org.pk, fresh=True, require_autocommit=True
            )
    with collect_sync_warnings() as warnings, transaction.atomic():
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
        require_capture_admin(active, config, user)
        config.mcp = _enforced_mcp(active, config)
        _revoke_server_clients_left_behind(org, config.mcp, active)
        _enforce_tools(active, config)
        _encrypt_secrets(active, config)

        routing_warnings = check_routing_keys(active, config)
        # After _encrypt_secrets: a vector-store key saved back as its mask is
        # the stored one again (semantic_cache_rules).
        check_semantic_cache(active, config)
        config_push.validate_org_config(
            config, stored_routing=_stored_section(active, "routing")
        )
        require_unpriced_admin(active, config, user)
        require_cap_ledger_for_activation(active, config, ledger_status=ledger_status)
        stamp_cap_activation(config)
        # A save that changes guardrails may not switch on (or newly push) a
        # check holding "${" where the gateway expands it; a save that leaves
        # guardrails alone is never refused for one (guardrail_env_refs).
        stored_guardrails = _stored_section(active, "guardrails")
        if config.guardrails != stored_guardrails:
            reject_pushed_guardrail_env_references(config, active)
            # Nor store a keyword-blocklist key the check never reads, such
            # as a stored blocklist's id (keyword_blocklist_rules).
            reject_new_unknown_keys(
                section_blocklist_configs(config.guardrails),
                section_blocklist_configs(stored_guardrails),
            )
        # A guardrail check's callback URL (url/endpoint/base_url) and an alert
        # channel's URL must be well-formed before the gateway dials them
        # This is the one chokepoint every config write
        # passes (update-config, POST org-configs, toggle- and
        # update-guardrail): only a URL this save introduces or changes is
        # checked, so a save that leaves a stored URL alone — including turning
        # that very check off — is never blocked, and the egress guard still
        # refuses the address at runtime.
        guardrail_urls.reject_changed_bad_guardrail_urls(
            config.guardrails, stored_guardrails
        )
        guardrail_urls.reject_changed_bad_alerting_urls(
            config.alerting, _stored_section(active, "alerting")
        )
        if active is not None:
            AgentccOrgConfig.no_workspace_objects.filter(
                organization=org, is_active=True, deleted=False
            ).update(is_active=False)
        config.save()
        audit_capture_change(active, config, user)
        audit_unpriced_change(active, config, user)

        warnings.extend(_managed_models_change(active, config))
        warnings.extend(routing_warnings)
        warnings.extend(_push_to_gateway(config, push))
    return config, warnings


def activate_config_version(
    config: AgentccOrgConfig, *, push: Push | None = None, user: User | None = None
) -> tuple[AgentccOrgConfig, list[ConfigWarning]]:
    """Make an existing version the active one and push it.

    Raises ``ConfigVersionUnavailable`` when the version was deleted, and
    ``OrgConfigRejected`` when it holds keys the gateway contract no longer
    accepts, a plaintext MCP secret the active version does not hold, or an
    MCP server whose stored credential it would send to another origin than
    the one the credential was entered for (``_refuse_activated_origin_moves``).
    With no active version before it, the managed-models warning compares
    against managed models off.
    """
    org_id = config.organization_id
    # A restore never asks the gateway for its spend ledger: whether daily caps
    # stay in force is decided under the lock from the two versions alone.
    with collect_sync_warnings() as warnings, transaction.atomic():
        lock_org(org_id)
        target = (
            AgentccOrgConfig.no_workspace_objects.select_for_update()
            .filter(pk=config.pk, organization_id=org_id, deleted=False)
            .first()
        )
        if target is None:
            raise ConfigVersionUnavailable(config)
        from rest_framework.exceptions import ValidationError

        from agentcc.mcp_release_gates import refuse_disabled_config

        config = target
        active = (
            AgentccOrgConfig.no_workspace_objects.select_for_update()
            .filter(organization_id=org_id, is_active=True, deleted=False)
            .first()
        )
        # Daily caps can't be turned on in this release, and a restore is never
        # refused for them: restoring a version with caps on over one with caps
        # off saves a new version, a copy of it with daily caps off. The stored
        # version keeps its value.
        if (
            (active is None or active.pk != config.pk)
            and cap_ledger_status.caps_enabled(config)
            and not cap_ledger_status.caps_enabled(active)
        ):
            config = _restored_with_caps_off(config, user)
        try:
            refuse_disabled_config(
                config.mcp, active.mcp if active is not None else {}, activation=True
            )
        except ValidationError as exc:
            detail = exc.detail[0] if isinstance(exc.detail, list) else exc.detail
            raise MCPConfigRejected(str(detail), getattr(detail, "code", None)) from exc
        if active is None or active.pk != config.pk:
            require_capture_admin(active, config, user)
            _refuse_activated_mcp_secrets(active, config)
        # Even the active version must still reference live, correctly bound
        # grants and meet the cap contract before stamping or returning.
        _refuse_activated_origin_moves(active, config)
        # Activation changes no chain: grandfather the saved target, even when
        # the currently active version has shorter/different fallback chains.
        config_push.validate_org_config(config, stored_routing=config.routing)
        require_unpriced_admin(active, config, user)
        previous_budgets = copy.deepcopy(config.budgets)
        stamp_cap_activation(
            config, advance_revision=active is None or active.pk != config.pk
        )
        if active is not None and active.pk == config.pk:
            if config.budgets != previous_budgets:
                config.save(update_fields=["budgets", "updated_at"])
                return config, _push_to_gateway(config, push)
            return config, []
        # Activation answers 400 naming the field rather than push a check
        # that would refuse every request (guardrail_env_refs).
        reject_pushed_guardrail_env_references(config)
        _revoke_server_clients_left_behind(config.organization, config.mcp, active)
        AgentccOrgConfig.no_workspace_objects.filter(
            organization_id=org_id, is_active=True, deleted=False
        ).update(is_active=False)
        config.is_active = True
        if config.pk is None:
            config.save()
        else:
            config.save(update_fields=["is_active", "budgets", "updated_at"])
        audit_capture_change(active, config, user)
        audit_unpriced_change(active, config, user)

        # An activation writes no new value, so it is never refused, but it
        # carries the routing-key warnings of the version it activates.
        warnings.extend(_managed_models_change(active, config))
        warnings.extend(routing_key_warnings(config))
        warnings.extend(_push_to_gateway(config, push))
    return config, warnings


def _restored_with_caps_off(
    restored: AgentccOrgConfig, user: User | None
) -> AgentccOrgConfig:
    """An unsaved next version: a copy of ``restored`` with daily caps off."""
    config = _copy_of(restored, restored.organization)
    budgets = dict(config.budgets or {})
    budgets.pop("admissionCapsEnabled", None)
    budgets["admission_caps_enabled"] = False
    config.budgets = budgets
    config.version = (
        AgentccOrgConfig.no_workspace_objects.filter(
            organization_id=restored.organization_id, deleted=False
        ).aggregate(Max("version"))["version__max"]
        or 0
    ) + 1
    config.created_by = user
    config.change_description = (
        f"Restored from version {restored.version} with daily caps off"
    )
    return config


def _revoke_server_clients_left_behind(
    org: Organization, mcp: Any, active: AgentccOrgConfig | None
) -> None:
    """Revoke the org OAuth client of each server a write or an activation
    removes or moves to another origin, from the active version's mcp to
    ``mcp`` (a server's client never follows it to another origin). Under
    the caller's lock and in its transaction, so a write that
    fails afterwards revokes nothing. update-mcp-server and remove-mcp-server
    apply the same rule themselves; revoking is idempotent. Shared static
    grants whose effective reference disappears are also retired here, with a
    durable revision-eviction obligation that runs only after commit. This
    covers generic saves and rollback as well as explicit server deletion;
    the read-only bulk sync then carries that same committed config."""
    from agentcc.services import mcp_credentials, mcp_shared_credential_lifecycle

    stored = active.mcp if active is not None else {}
    mcp_shared_credential_lifecycle.revoke_removed_references(org, stored, mcp)
    mcp_credentials.revoke_server_clients_left_behind(org, mcp, stored)


def lock_org(org_id: Any) -> None:
    """Take the org's config-write lock for the rest of the transaction.

    Every config writer takes it first, before reading the active version,
    and a caller that locks other rows in the same transaction before it
    writes (a guardrail policy, say) calls this first, so every path takes
    the org lock before any other. Locking the active row instead does not
    serialize writers: once the first commits, the row no longer matches
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
        if section == "routing":
            current = collapse_stored_fallback_aliases(current, value)
        return deep_merge(current if isinstance(current, Mapping) else {}, value)
    return copy.deepcopy(value)


def _stored_section(active: AgentccOrgConfig | None, section: str) -> Any:
    """``section`` as the active version stores it, or its default when the
    org has no active version yet (used to tell what a save changes)."""
    if active is None:
        return _cleared_section(section)
    return getattr(active, section)


def _cleared_section(section: str) -> Any:
    return AgentccOrgConfig._meta.get_field(section).get_default()


def _copy_of(active: AgentccOrgConfig | None, org: Organization) -> AgentccOrgConfig:
    if active is None:
        # An org's first version: a new org gets Future AGI models on where
        # the deployment offers them (managed_models_default); every other
        # section starts from its default.
        config = AgentccOrgConfig(
            organization=org, managed_models=initial_managed_models(org)
        )
        config.disable_auto_workspace_assignment = True
        return config
    sections = {
        section: copy.deepcopy(getattr(active, section)) for section in CONFIG_SECTIONS
    }
    sections["cost_tracking"] = normalize_cost_tracking_config(
        sections["cost_tracking"]
    )
    config = AgentccOrgConfig(organization=org, **sections)
    config.disable_auto_workspace_assignment = True
    return config


def _enforced_mcp(active: AgentccOrgConfig | None, config: AgentccOrgConfig) -> Any:
    """``config.mcp`` with validate_mcp's rules applied against the locked
    active version, so no write path can change a virtual server id, add a
    stdio or credential-bearing server, or forward a refused header (M2
    IR-4). An unchanged section is left alone."""
    from rest_framework import serializers

    from agentcc.mcp_release_gates import refuse_disabled_config
    from agentcc.serializers.org_config import enforce_mcp_write

    stored = active.mcp if active is not None else {}
    if config.mcp == stored:
        return config.mcp
    try:
        refuse_disabled_config(config.mcp, stored)
        return enforce_mcp_write(config.mcp, stored)
    except serializers.ValidationError as exc:
        detail = exc.detail[0] if isinstance(exc.detail, list) else exc.detail
        raise MCPConfigRejected(str(detail), getattr(detail, "code", None)) from exc


def _encrypt_secrets(active: AgentccOrgConfig | None, config: AgentccOrgConfig) -> None:
    """Store every infrastructure secret of ``config`` encrypted, keeping a
    stored one on a blank or a read-back mask and refusing to carry one to a
    host the URL just moved to. Runs on every write path, under the
    org lock, so update-config, POST org-configs/ and the budget and other
    single-section actions all encrypt a new secret and none stores one in
    clear.

    MCP secrets are the credential vault's (M14), never the config's: it runs
    after ``_enforced_mcp``, whose ``refuse_mcp_secrets`` has already refused
    any new MCP secret, so in the mcp section it only keeps a legacy secret
    stored inline before the vault, encrypted at rest, until
    ``agentcc_mcp_migrate_secrets`` moves it into a vault row."""
    from agentcc.services.org_config_secrets import encrypt_config_secrets

    encrypt_config_secrets(config, active)


def _refuse_activated_mcp_secrets(
    active: AgentccOrgConfig | None, config: AgentccOrgConfig
) -> None:
    """Refuse to activate a version that holds a plaintext MCP secret the
    active version does not (M14 IR-1, at the W3b merge): one saved before the
    credential vault, until agentcc_mcp_migrate_secrets moves it. Only the
    secret check (refuse_mcp_secrets) and the origin check
    (_refuse_activated_origin_moves) run on an activation, never the
    vault-owned-reference guard: activating an older version legitimately
    changes credential references."""
    from rest_framework import serializers

    from agentcc.serializers.org_config import refuse_mcp_secrets

    try:
        refuse_mcp_secrets(config.mcp, active.mcp if active is not None else {})
    except serializers.ValidationError as exc:
        detail = exc.detail[0] if isinstance(exc.detail, list) else exc.detail
        raise MCPConfigRejected(str(detail), getattr(detail, "code", None)) from exc


def _refuse_activated_origin_moves(
    active: AgentccOrgConfig | None, config: AgentccOrgConfig
) -> None:
    """Refuse to activate a version that would send an MCP server's stored
    credential to another origin (scheme, host or port) than the one it was
    entered for (400 mcp_credential_origin_changed: a stored credential
    can't move to another origin). The vault rotates a re-entered credential in place
    under the same id, so an older version at another origin still
    references the live credential.

    Two checks. The save's (refuse_credential_origin_moves) against the
    version being replaced, which also covers a legacy
    clear-text secret. Then refuse_credentials_sent_elsewhere against the
    credential itself: each named shared credential must still be active and
    bound to that server's origin, including after revoke-on-removal.
    The first alone was defeated by activating a version without the server,
    or removing it with a save, before the moved version. The credential is re-entered with a save instead (the message says
    how)."""
    from rest_framework import serializers

    from agentcc.serializers.org_config import (
        refuse_credential_origin_moves,
        refuse_credentials_sent_elsewhere,
    )

    try:
        refuse_credential_origin_moves(
            config.mcp, active.mcp if active is not None else {}
        )
        refuse_credentials_sent_elsewhere(config.mcp, config.organization_id)
    except serializers.ValidationError as exc:
        detail = exc.detail[0] if isinstance(exc.detail, list) else exc.detail
        raise MCPConfigRejected(str(detail), getattr(detail, "code", None)) from exc


def _enforce_tools(active: AgentccOrgConfig | None, config: AgentccOrgConfig) -> None:
    """Refuse a changed ``config.tools`` that validate_tools would refuse, so
    update-config cannot store what POST org-configs/ refuses (V4 IR-5). The
    byok_only managed-glob refusal is an mcp rule, rerun by _enforced_mcp."""
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
