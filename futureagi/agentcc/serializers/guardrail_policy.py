import copy

import structlog
from rest_framework import serializers

from integrations.services.credentials import CredentialManager
from agentcc.models.guardrail_policy import AgentccGuardrailPolicy

logger = structlog.get_logger(__name__)

SENSITIVE_KEYS = {"api_key", "secret_key", "access_key", "password"}
ENCRYPTED_SENTINEL = "__encrypted__"


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
        return value

    def validate_scope(self, value):
        valid = [c[0] for c in AgentccGuardrailPolicy.SCOPE_CHOICES]
        if value not in valid:
            raise serializers.ValidationError(
                f"scope must be one of: {', '.join(valid)}"
            )
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
