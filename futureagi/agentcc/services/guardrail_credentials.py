"""Guardrail credentials entered through the gateway config.

A guardrail check's credential (SENSITIVE_KEYS) is never stored in an org
config: the config keeps the "__encrypted__" sentinel and the `_policy` id of
the guardrail policy whose encrypted blob holds the value, and the config push
injects it. Every config write that takes a guardrails section runs it through
here: a check may only name a policy the org may name, and a new clear-text
credential moves into the org's console policy. Both run inside the config
write, after its org lock, so a refused version rolls the blob back too.
"""

import structlog

from agentcc.models.guardrail_policy import AgentccGuardrailPolicy
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.serializers.guardrail_policy import ENCRYPTED_SENTINEL, SENSITIVE_KEYS
from agentcc.services.config_push import (
    OrgConfigRejected,
    guardrail_check_items,
    guardrail_policy_id,
    guardrail_policy_ref,
    org_guardrail_policies,
)
from agentcc.services.guardrail_env_refs import reject_guardrail_env_references
from integrations.services.credentials import CredentialManager

logger = structlog.get_logger(__name__)

# The org's guardrail policy that holds credentials entered in the console.
CONSOLE_GUARDRAIL_POLICY = "__ui_guardrails__"


class GuardrailPolicyNotInOrg(OrgConfigRejected):
    """A guardrail check names a policy that is not one of the org's own."""

    code = "guardrail_policy_not_in_org"

    def __init__(self, policy_refs):
        self.policy_ids = tuple(sorted(policy_refs))
        super().__init__(
            f"Guardrail policy {', '.join(self.policy_ids)} is not one of this "
            "organization's policies."
        )


class GuardrailCredentialConflict(OrgConfigRejected):
    """Two checks with one name carry different values for one credential."""

    code = "guardrail_credential_conflict"

    def __init__(self, check_name, key):
        super().__init__(
            f"Guardrail check {check_name} is sent more than once with "
            f"different values for {key}; send one value."
        )


class GuardrailCredentialsUnreadable(OrgConfigRejected):
    """The console policy's stored credentials cannot be decrypted."""

    code = "guardrail_credentials_unreadable"

    def __init__(self):
        super().__init__(
            "The organization's stored guardrail credentials cannot be "
            "decrypted (the encryption key may have changed), so a new one "
            "cannot be added without discarding them. Ask an administrator to "
            "restore the key or clear the stored credentials."
        )


def _guardrail_section_items(guardrails):
    """(name, check) pairs of every check in a guardrails section: its rules
    and its checks, stored as a list or as a map keyed by name."""
    if not isinstance(guardrails, dict):
        return []
    return [
        *guardrail_check_items(guardrails.get("rules")),
        *guardrail_check_items(guardrails.get("checks")),
    ]


def _policy_key(check):
    """What identifies a check's `_policy` when comparing with stored ones:
    the canonical id, the raw value of a string that is not a UUID, and None
    for any other value."""
    pid = guardrail_policy_id(check)
    if pid is not None:
        return pid
    value = check.get("_policy")
    return value if isinstance(value, str) else None


def _reject_unowned_guardrail_policies(org, guardrails):
    """Refuse a guardrails patch whose `_policy` is not the org's to name.

    A check may name one of the org's own live policies. Another org's live
    policy is always refused. Any other id (deleted, unknown, not a UUID) is
    refused with the same answer, unless the org's active version already
    holds it: the console sends saved rules back whole, with the ids they
    were saved with. Such a held id stays inert, because the push reads only
    the org's own policies. Runs inside the config write, where the version
    being replaced is still the active one.
    """
    named = [
        check
        for _, check in _guardrail_section_items(guardrails)
        if check.get("_policy")
    ]
    if not named:
        return
    active = AgentccOrgConfig.no_workspace_objects.filter(
        organization=org, is_active=True, deleted=False
    ).first()
    held = {
        _policy_key(check)
        for _, check in _guardrail_section_items(
            active.guardrails if active is not None else None
        )
    }
    ids = {pid for check in named if (pid := guardrail_policy_id(check))}
    own = {
        str(pid)
        for pid in org_guardrail_policies(org.id, ids).values_list("id", flat=True)
    }
    foreign = {
        str(pid)
        for pid in AgentccGuardrailPolicy.no_workspace_objects.filter(
            id__in=ids - own, deleted=False
        )
        .exclude(organization_id=org.id)
        .values_list("id", flat=True)
    }
    allowed = own | (held - {None} - foreign)
    unowned = {
        guardrail_policy_ref(check)
        for check in named
        if _policy_key(check) not in allowed
    }
    if unowned:
        raise GuardrailPolicyNotInOrg(unowned)


def _decrypted_policy_secrets(policy):
    """A guardrail policy's credentials, {check_name: {key: value}}.

    A blob that cannot be decrypted counts as empty, as in the guardrail
    policy serializer: the push cannot inject its values either.
    """
    try:
        return _policy_secrets(policy)
    except ValueError:
        logger.warning(
            "guardrail_credential_decrypt_failed",
            policy_id=str(policy.id),
            exc_info=True,
        )
        return {}


def _policy_secrets(policy):
    """A guardrail policy's credentials; ValueError when the blob cannot be
    decrypted."""
    if not policy.encrypted_check_configs:
        return {}
    secrets = CredentialManager.decrypt(bytes(policy.encrypted_check_configs))
    return secrets if isinstance(secrets, dict) else {}


def _new_guardrail_credentials(guardrails):
    """(name, check, {key: clear-text value}) for every check that carries a
    credential value (SENSITIVE_KEYS) other than the sentinel.

    The push looks credentials up by check name, so a nameless check holding
    one is refused, and so are two checks with one name that disagree on a
    value: only one of them could keep it.
    """
    new = []
    seen = {}
    for name, check in _guardrail_section_items(guardrails):
        cfg = check.get("config")
        if not isinstance(cfg, dict):
            continue
        secrets = {
            key: value
            for key, value in cfg.items()
            if key in SENSITIVE_KEYS and value and value != ENCRYPTED_SENTINEL
        }
        if not secrets:
            continue
        if not name:
            raise OrgConfigRejected(
                "A guardrail check that holds a credential needs a name."
            )
        for key, value in secrets.items():
            if seen.setdefault((name, key), value) != value:
                raise GuardrailCredentialConflict(name, key)
        new.append((name, check, secrets))
    return new


def _encrypt_new_guardrail_credentials(org, config):
    """Move clear-text guardrail credentials into the org's console policy.

    As POST org-configs/ does, a credential value (SENSITIVE_KEYS) is stored
    only encrypted, in the console policy's blob, and the config keeps the
    "__encrypted__" sentinel and that policy's id. The check's credentials
    already stored under the policy it named move with it, so the one policy
    a check names holds all of them. A console blob that cannot be decrypted
    is refused, never overwritten. Runs inside the config write, after its
    org lock, so a rejected version rolls the blob back too.
    """
    new = _new_guardrail_credentials(config.guardrails)
    if not new:
        return

    console, _ = (
        AgentccGuardrailPolicy.no_workspace_objects.select_for_update().get_or_create(
            organization=org,
            name=CONSOLE_GUARDRAIL_POLICY,
            deleted=False,
            defaults={
                "checks": [],
                "scope": AgentccGuardrailPolicy.SCOPE_GLOBAL,
                "is_active": True,
            },
        )
    )
    try:
        blob = _policy_secrets(console)
    except ValueError:
        logger.warning(
            "guardrail_console_credentials_unreadable", policy_id=str(console.id)
        )
        raise GuardrailCredentialsUnreadable() from None
    named = {guardrail_policy_id(check) for _, check, _ in new} - {
        None,
        str(console.id),
    }
    previous = {
        str(policy.id): _decrypted_policy_secrets(policy)
        for policy in org_guardrail_policies(org.id, named)
    }
    stored_by_name, sent_by_name = {}, {}
    for name, check, secrets in new:
        stored = previous.get(guardrail_policy_id(check), blob).get(name)
        if isinstance(stored, dict):
            stored_by_name.setdefault(name, {}).update(stored)
        sent_by_name.setdefault(name, {}).update(secrets)
        check["config"].update(dict.fromkeys(secrets, ENCRYPTED_SENTINEL))
        check["_policy"] = str(console.id)
    # A value sent now always wins over a stored one.
    for name, sent in sent_by_name.items():
        blob[name] = {**stored_by_name.get(name, {}), **sent}
    console.encrypted_check_configs = CredentialManager.encrypt(blob)
    console.save(update_fields=["encrypted_check_configs", "updated_at"])


def _prepare_guardrails(org, patch_guardrails, config):
    """update-config's guardrails step inside the config write: refuse a "${"
    in a check's credential, URL or other provider setting
    (guardrail_env_refs), refuse ids the org may not name, then encrypt new
    credentials. The "${" check runs on the raw patch, before any credential is
    replaced by the "__encrypted__" sentinel."""
    reject_guardrail_env_references(patch_guardrails)
    _reject_unowned_guardrail_policies(org, patch_guardrails)
    _encrypt_new_guardrail_credentials(org, config)


def log_config_rejected(event, org, error):
    """Log a config write the contract or the guardrail checks refused: its
    code and, for a refused policy id, the id. Never a value."""
    fields = (
        {"policy_ids": error.policy_ids}
        if isinstance(error, GuardrailPolicyNotInOrg)
        else {}
    )
    logger.warning(
        event, org_id=str(org.id), code=getattr(error, "code", None), **fields
    )
