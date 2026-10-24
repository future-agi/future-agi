"""The Guardrails page's switch (toggle-guardrail) and edit (update-guardrail)
on a guardrails section stored as a checks list.

A policy sync (guardrail_sync) rewrites the org's section from its active
global policies, as a checks list whose entries name their policy
(``_policy``), on every policy create, update, delete, apply or sync. So on
such a section the switch and the edit change the check where the next sync
reads it:

- a check the list took from one of the org's active global policies is
  changed in that policy;
- a check no such policy holds goes into the console's own policy
  (CONSOLE_GUARDRAIL_POLICY), where POST org-configs already keeps the checks
  the console saves;

and the version the switch or the edit writes holds the section a sync writes
(guardrail_sync.policy_guardrails), beside the section's own settings. Both
run inside the org-config write (org_config_writer.write_config_version_with),
after its org lock, so a version the writer then refuses (a check holding a
``${VAR}`` switched on, a bad callback URL) rolls the policy back too.

A checks list no policy wrote (saved as a list through update-config or POST
org-configs) is changed as a list.
"""

import copy

from rest_framework import serializers

from agentcc.models.guardrail_policy import AgentccGuardrailPolicy
from agentcc.serializers.guardrail_policy import (
    ENCRYPTED_SENTINEL,
    SENSITIVE_KEYS,
    AgentccGuardrailPolicySerializer,
)
from agentcc.services.config_push import (
    _RULE_TO_REGISTRY,
    OrgConfigRejected,
    guardrail_policy_id,
    org_guardrail_policies,
)
from agentcc.services.guardrail_credentials import (
    CONSOLE_GUARDRAIL_POLICY,
    GuardrailCredentialsUnreadable,
    _encrypt_new_guardrail_credentials,
    _policy_secrets,
)
from agentcc.services.guardrail_env_refs import reject_rule_env_references
from agentcc.services.guardrail_sync import policy_guardrails
from integrations.services.credentials import CredentialManager

# What a switch adds for a check the section does not hold yet, as the switch
# adds a rule (views/gateway.py toggle_guardrail).
_NEW_CHECK_DEFAULTS = {"action": "block", "threshold": 0.8, "config": {}}

# The keys of a checks-list entry the push and the policy sync own: never
# taken from what an edit sends.
_SECTION_KEYS = ("checks", "enabled", "rules")


class GuardrailPolicyCheckRejected(OrgConfigRejected):
    """A switch or an edit would store a check its guardrail policy refuses
    (validate_checks): a config that is not an object, a bad callback URL."""

    def __init__(self, error):
        detail = error.detail
        while isinstance(detail, (list, dict)) and detail:
            detail = (
                next(iter(detail.values())) if isinstance(detail, dict) else detail[0]
            )
        self.code = getattr(detail, "code", None) or "invalid"
        super().__init__(str(detail))


def stored_checks_list(guardrails):
    """The section's checks when it is stored as a non-empty list, else None."""
    checks = guardrails.get("checks") if isinstance(guardrails, dict) else None
    return checks if isinstance(checks, list) and checks else None


def _names(name):
    """``name`` and the catalog name the push sends a rule name as."""
    return {name, _RULE_TO_REGISTRY.get(name, name)}


def _list_index(checks, name):
    """The index of the list's check ``name`` names, or None. The push keeps
    the last check of a name, so the last one is taken."""
    found = None
    for i, check in enumerate(checks):
        if isinstance(check, dict) and _names(name) & _names(check.get("name")):
            found = i
    return found


def _policy_index(policy, name):
    found = None
    for i, check in enumerate(policy.checks or []):
        if isinstance(check, dict) and check.get("name") == name:
            found = i
    return found


def _live_policies(org, ids):
    """The org's active global policies among ``ids``, locked for this write."""
    return (
        org_guardrail_policies(org.id, ids)
        .filter(is_active=True, scope=AgentccGuardrailPolicy.SCOPE_GLOBAL)
        .select_for_update()
    )


def _owner(org, checks, index):
    """(policy, index in its checks) of the policy the list's check came
    from, when that is one of the org's active global policies and it still
    holds the check; else None."""
    if index is None:
        return None
    entry = checks[index]
    pid = guardrail_policy_id(entry)
    policy = _live_policies(org, {pid}).first() if pid else None
    if policy is None:
        return None
    at = _policy_index(policy, entry.get("name"))
    return None if at is None else (policy, at)


def _console_policy(org, checks):
    """The console's policy, created if need be, when the list is one a
    policy sync wrote (one of its checks names an active global policy of
    the org) and that policy is one a sync merges; else None."""
    ids = {pid for check in checks if (pid := guardrail_policy_id(check))}
    if not ids or not _live_policies(org, ids).exists():
        return None
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
    if not console.is_active or console.scope != AgentccGuardrailPolicy.SCOPE_GLOBAL:
        return None
    return console


def _without_list_keys(check):
    return {
        key: value
        for key, value in (check or {}).items()
        if isinstance(key, str) and not key.startswith("_")
    }


def _edited_fields(sent):
    """The fields of a check an edit sends, as a checks-list check keeps
    them: never its name or a list key (``_policy`` and the like), and its
    threshold under the name the push reads (a checks map calls it
    confidence_threshold)."""
    fields = {
        key: value
        for key, value in sent.items()
        if isinstance(key, str) and not key.startswith("_") and key != "name"
    }
    for alias in ("confidence_threshold", "confidenceThreshold"):
        if alias in fields:
            value = fields.pop(alias)
            fields.setdefault("threshold", value)
    return fields


def _validate(check):
    """The guardrail policy's own checks on one check: its name, a config that
    is an object, no ${VAR} in a credential, URL or other provider setting,
    well-formed callback URLs."""
    try:
        AgentccGuardrailPolicySerializer().validate_checks([check])
    except serializers.ValidationError as e:
        raise GuardrailPolicyCheckRejected(e) from None


def _store_credentials(policy, check):
    """Keep a check's credentials as its policy keeps them: a value sent in
    clear is moved into the policy's encrypted blob (the check keeps the
    sentinel), a value sent as the sentinel keeps the stored one, and a
    credential the check does not name is dropped. A blob that cannot be
    decrypted is never overwritten: a new value is then refused."""
    cfg = check.get("config")
    if not isinstance(cfg, dict):
        return
    name = check["name"]
    clear = {
        key: value
        for key, value in cfg.items()
        if key in SENSITIVE_KEYS and value and value != ENCRYPTED_SENTINEL
    }
    kept = {key for key, value in cfg.items() if value == ENCRYPTED_SENTINEL}
    try:
        blob = _policy_secrets(policy)
    except ValueError:
        if clear:
            raise GuardrailCredentialsUnreadable() from None
        return
    stored = blob.get(name) if isinstance(blob.get(name), dict) else {}
    secrets = {key: value for key, value in stored.items() if key in kept}
    secrets.update(clear)
    cfg.update(dict.fromkeys(clear, ENCRYPTED_SENTINEL))
    if secrets == stored:
        return
    if secrets:
        blob[name] = secrets
    else:
        blob.pop(name, None)
    policy.encrypted_check_configs = CredentialManager.encrypt(blob) if blob else None


def _write_policy_check(org, config, policy, at, check):
    """Store ``check`` in ``policy`` (at index ``at``, or appended), and give
    the version being written the section a policy sync writes now."""
    checks = copy.deepcopy(policy.checks or [])
    if at is None:
        checks.append(check)
    else:
        checks[at] = check
    policy.checks = checks
    policy.save(update_fields=["checks", "encrypted_check_configs", "updated_at"])
    settings = {
        key: value
        for key, value in (config.guardrails or {}).items()
        if key not in _SECTION_KEYS
    }
    merged, _ = policy_guardrails(org)
    config.guardrails = {**settings, **merged}


def switch_in_checks_list(org, config, name, enabled):
    """toggle-guardrail on a section stored as a checks list. Returns False
    when the section is not one, and leaves it to the caller."""
    checks = stored_checks_list(config.guardrails)
    if checks is None:
        return False
    index = _list_index(checks, name)
    owner = _owner(org, checks, index)
    if owner is not None:
        policy, at = owner
        check = {**policy.checks[at], "enabled": enabled}
    else:
        policy, at = _console_policy(org, checks), None
        base = (
            _without_list_keys(checks[index])
            if index is not None
            else {"name": name, **copy.deepcopy(_NEW_CHECK_DEFAULTS)}
        )
        check = {**base, "enabled": enabled}
        if policy is None:
            _put_in_list(checks, index, check)
            return True
        _validate(check)
    _write_policy_check(org, config, policy, at, check)
    return True


def edit_in_checks_list(org, config, name, sent):
    """update-guardrail on a section stored as a checks list. Returns False
    when the section is not one, and leaves it to the caller. A ${VAR} the edit
    sends in a credential, URL or other provider setting is refused naming the
    request's field (config.config.<key>), as on a rules list."""
    checks = stored_checks_list(config.guardrails)
    if checks is None:
        return False
    reject_rule_env_references(name, sent)
    index = _list_index(checks, name)
    owner = _owner(org, checks, index)
    fields = _edited_fields(sent)
    if owner is not None:
        policy, at = owner
        base = policy.checks[at]
    else:
        policy, at = _console_policy(org, checks), None
        base = _without_list_keys(checks[index]) if index is not None else {}
    check = {**base, **fields, "name": base.get("name", name)}
    _validate(check)
    if policy is None:
        _put_in_list(checks, index, check)
        # A credential sent in clear goes into the console policy, as on a
        # rules list; the check keeps the sentinel and names that policy.
        _encrypt_new_guardrail_credentials(org, config)
        return True
    _store_credentials(policy, check)
    _write_policy_check(org, config, policy, at, check)
    return True


def _put_in_list(checks, index, check):
    """Change a list no policy wrote in place (the version's own copy):
    replace the check ``index`` names, or append a new one."""
    if index is None:
        checks.append(check)
    else:
        checks[index] = {
            **{k: v for k, v in checks[index].items() if k.startswith("_")},
            **check,
        }
