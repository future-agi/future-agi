import copy

import structlog
from rest_framework import serializers

from agentcc.models.guardrail_policy import AgentccGuardrailPolicy
from agentcc.services import guardrail_urls
from agentcc.services.guardrail_env_refs import (
    GuardrailEnvReference,
    policy_checks_env_reference_path,
)
from agentcc.services.keyword_blocklist_rules import (
    KeywordBlocklistUnknownKey,
    policy_blocklist_configs,
    reject_new_unknown_keys,
)
from integrations.services.credentials import CredentialManager

logger = structlog.get_logger(__name__)

SENSITIVE_KEYS = {"api_key", "secret_key", "access_key", "password"}
ENCRYPTED_SENTINEL = "__encrypted__"

# docs-agentcc-711, item 2: the gateway receives only an org's global
# policies (guardrail_sync), so a policy set to its keys or its projects
# stopped running for every key, the targeted ones included. Key and project
# scope are refused on every write path (create, update, apply); a policy a
# release before this one left at key or project scope still reads back, and
# setting it to global (or agentcc_find_scoped_guardrail_policies --repair
# --apply) restores it.
SCOPED_POLICY_NOT_SUPPORTED = "scoped_policy_not_supported"
SCOPED_POLICY_NOT_SUPPORTED_MESSAGE = (
    "Guardrail policies apply to every key of the organization. Key and "
    "project scope are not supported, so the policy was left unchanged."
)
_SCOPE_HELP = (
    "Always global: the policy runs for every key of the organization. Key "
    "and project scope are not supported (400 scoped_policy_not_supported)."
)
_APPLIED_HELP = (
    "Not used. Kept from earlier releases, which stored the keys or projects "
    "a policy was applied to; a policy runs for every key of the organization."
)
_READ_SCOPE_HELP = (
    "The stored scope. Global for every policy this release writes. A policy "
    "an earlier release left at key or project scope reads back as stored and "
    "is not pushed until it is set to global."
)


class _GlobalScopeField(serializers.ChoiceField):
    """The scope a write may set: global only. A stored key or project scope
    from an earlier release reads back as stored."""

    def __init__(self, **kwargs):
        super().__init__(
            choices=[(AgentccGuardrailPolicy.SCOPE_GLOBAL, "Global")], **kwargs
        )

    def to_internal_value(self, data):
        if data in (
            AgentccGuardrailPolicy.SCOPE_KEY,
            AgentccGuardrailPolicy.SCOPE_PROJECT,
        ):
            raise serializers.ValidationError(
                SCOPED_POLICY_NOT_SUPPORTED_MESSAGE, code=SCOPED_POLICY_NOT_SUPPORTED
            )
        return super().to_internal_value(data)


def _extract_secrets(checks):
    """
    Extract sensitive values from check configs.

    Returns:
        (sanitized_checks, secrets_map)
        sanitized_checks: checks with sensitive values replaced by sentinel
        secrets_map: {check_name: {key: real_value, ...}}
    """
    sanitized = copy.deepcopy(checks)
    secrets = {}
    for check in sanitized:
        name = check.get("name")
        cfg = check.get("config")
        if not name or not isinstance(cfg, dict):
            continue
        check_secrets = {}
        for key in list(cfg.keys()):
            if key in SENSITIVE_KEYS and cfg[key] and cfg[key] != ENCRYPTED_SENTINEL:
                check_secrets[key] = cfg[key]
                cfg[key] = ENCRYPTED_SENTINEL
        if check_secrets:
            secrets[name] = check_secrets
    return sanitized, secrets


def _encrypt_secrets(secrets_map):
    """Encrypt a secrets map to bytes using CredentialManager."""
    if not secrets_map:
        return None
    return CredentialManager.encrypt(secrets_map)


def _decrypt_secrets(encrypted_blob):
    """Decrypt an encrypted blob to a secrets map."""
    if not encrypted_blob:
        return {}
    try:
        return CredentialManager.decrypt(bytes(encrypted_blob))
    except Exception:
        logger.warning("guardrail_credential_decrypt_failed")
        return {}


class AgentccGuardrailPolicySerializer(serializers.ModelSerializer):
    scope = _GlobalScopeField(required=False, help_text=_SCOPE_HELP)
    applied_keys = serializers.JSONField(read_only=True, help_text=_APPLIED_HELP)
    applied_projects = serializers.JSONField(read_only=True, help_text=_APPLIED_HELP)

    class Meta:
        model = AgentccGuardrailPolicy
        fields = [
            "id",
            "organization",
            "name",
            "description",
            "scope",
            "checks",
            "mode",
            "is_active",
            "priority",
            "applied_keys",
            "applied_projects",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "organization",
            "created_at",
            "updated_at",
        ]

    def validate_checks(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("checks must be a JSON array")
        for i, check in enumerate(value):
            if not isinstance(check, dict):
                raise serializers.ValidationError(
                    f"Check at index {i} must be a JSON object"
                )
            if "name" not in check:
                raise serializers.ValidationError(
                    f"Check at index {i} must have a 'name' field"
                )
            # A check's name is a non-empty string, the key the gateway runs
            # it under. A number saved, and guardrail_sync then stored it
            # where the contract refuses it, so every later org-config save
            # answered 400; a list or an object broke every sync; null or ""
            # left the check out of every push (release round 6B, fix (c)).
            name = check["name"]
            if not isinstance(name, str) or not name.strip():
                raise serializers.ValidationError(
                    f"checks[{i}].name must be a non-empty string",
                    code="guardrail_check_name_invalid",
                )
            # A check's config is an object (or absent / null: no settings,
            # pushed as {}). Anything else used to save, and guardrail_sync
            # then stored it in the active org config, where no push could
            # transform it (RF11c).
            config = check.get("config")
            if config is not None and not isinstance(config, dict):
                raise serializers.ValidationError(
                    f"checks[{i}].config must be a JSON object",
                    code="guardrail_check_config_not_an_object",
                )
        # A "${VAR}" in a string the gateway could expand (a futureagi check's
        # credentials or base_url, an external-provider check's top-level
        # settings) would be expanded against the gateway's own environment
        # once the policy is merged into an org config and pushed (security
        # assessment round 1, SSRF lens F1). Refuse it here, on the policy
        # save path, as the org-config write paths do; any other "${" is
        # plain text.
        path = policy_checks_env_reference_path(value, "checks")
        if path:
            raise serializers.ValidationError(
                str(GuardrailEnvReference(path)), code=GuardrailEnvReference.code
            )
        # A check's callback URL must be well-formed before a policy sync
        # pushes it into the org config and the gateway dials it (SSRF-F2,
        # RF13). The policy write path bypasses the config writer, so it is
        # checked here.
        try:
            guardrail_urls.reject_bad_policy_check_urls(value)
        except guardrail_urls.BadGuardrailURL as e:
            raise serializers.ValidationError(str(e), code=e.code) from None
        # A keyword-blocklist check runs on words, whole_word and max_matches
        # only; any other key (a stored blocklist's id) used to save silently
        # and block nothing (docs-agentcc-711, item 1). One the stored policy
        # already holds with the same value is kept.
        stored = self.instance.checks if self.instance is not None else []
        try:
            reject_new_unknown_keys(
                policy_blocklist_configs(value), policy_blocklist_configs(stored)
            )
        except KeywordBlocklistUnknownKey as e:
            raise serializers.ValidationError(str(e), code=e.code) from None
        return value

    def validate_mode(self, value):
        valid = [c[0] for c in AgentccGuardrailPolicy.MODE_CHOICES]
        if value not in valid:
            raise serializers.ValidationError(
                f"mode must be one of: {', '.join(valid)}"
            )
        return value

    def create(self, validated_data):
        checks = validated_data.get("checks", [])
        sanitized, secrets = _extract_secrets(checks)
        validated_data["checks"] = sanitized
        instance = super().create(validated_data)
        if secrets:
            instance.encrypted_check_configs = _encrypt_secrets(secrets)
            instance.save(update_fields=["encrypted_check_configs"])
        return instance

    def update(self, instance, validated_data):
        checks = validated_data.get("checks")
        if checks is not None:
            existing_secrets = _decrypt_secrets(instance.encrypted_check_configs)

            # Capture which sensitive keys the user *explicitly* sent as the
            # sentinel — those mean "keep existing encrypted value". Must do
            # this BEFORE _extract_secrets, which sanitizes everything to
            # sentinel and would otherwise make every key look preserved.
            preserve = {}  # {check_name: set(keys)}
            for check in checks:
                name = check.get("name")
                cfg = check.get("config")
                if not name or not isinstance(cfg, dict):
                    continue
                for key, value in cfg.items():
                    if key in SENSITIVE_KEYS and value == ENCRYPTED_SENTINEL:
                        preserve.setdefault(name, set()).add(key)

            sanitized, new_secrets = _extract_secrets(checks)

            for name, keys in preserve.items():
                for key in keys:
                    if name in existing_secrets and key in existing_secrets[name]:
                        new_secrets.setdefault(name, {})[key] = existing_secrets[name][
                            key
                        ]

            validated_data["checks"] = sanitized
            instance.encrypted_check_configs = (
                _encrypt_secrets(new_secrets) if new_secrets else None
            )

        return super().update(instance, validated_data)


class AgentccGuardrailPolicyReadSerializer(AgentccGuardrailPolicySerializer):
    """A policy as the API returns it: list, retrieve and the create, PUT and
    PATCH responses. Writes go through AgentccGuardrailPolicySerializer, which
    accepts scope global only; a policy an earlier release left at key or
    project scope still reads back as stored, so the response declares every
    stored scope. Output only: no view validates input with it."""

    scope = serializers.ChoiceField(
        choices=AgentccGuardrailPolicy.SCOPE_CHOICES,
        read_only=True,
        help_text=_READ_SCOPE_HELP,
    )

    class Meta(AgentccGuardrailPolicySerializer.Meta):
        ref_name = "AgentccGuardrailPolicyRead"
