import copy
import uuid

import structlog
from rest_framework import serializers

from agentcc.models import AgentccAPIKey
from agentcc.models.guardrail_policy import AgentccGuardrailPolicy
from integrations.services.credentials import CredentialManager

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
            "runs_on_gateway",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "organization",
            # Set by the policy API on every save; a key policy saved before
            # the gateway ran them stays dormant until saved again.
            "runs_on_gateway",
            "created_at",
            "updated_at",
        ]

    def validate_name(self, value):
        if value.startswith("__"):
            raise serializers.ValidationError("Names starting with __ are reserved")
        return value

    def validate_applied_keys(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("applied_keys must be a list")
        normalized = []
        malformed = []
        for key_id in value:
            try:
                parsed = str(uuid.UUID(key_id)) if isinstance(key_id, str) else None
            except (ValueError, AttributeError):
                parsed = None
            if parsed is None:
                malformed.append(str(key_id))
            elif parsed not in normalized:
                normalized.append(parsed)
        org = self.context.get("organization")
        known = set()
        if org is not None and normalized:
            known = {
                str(key_id)
                for key_id in AgentccAPIKey.no_workspace_objects.filter(
                    organization=org, deleted=False, id__in=normalized
                ).values_list("id", flat=True)
            }
        invalid = malformed + [key_id for key_id in normalized if key_id not in known]
        if invalid:
            raise serializers.ValidationError(
                f"Unknown or unauthorized key IDs: {invalid}"
            )
        return normalized

    def validate(self, attrs):
        if self.instance is not None and not ({"scope", "applied_keys"} & attrs.keys()):
            # A change that leaves scope and keys alone (Active, mode, checks)
            # is not refused for a pairing an older version saved.
            return attrs
        scope = attrs.get("scope", self.instance.scope if self.instance else "global")
        keys = attrs.get(
            "applied_keys", self.instance.applied_keys if self.instance else []
        )
        if scope == AgentccGuardrailPolicy.SCOPE_KEY and not keys:
            raise serializers.ValidationError(
                {"applied_keys": "Key policies require at least one applied key"}
            )
        if scope != AgentccGuardrailPolicy.SCOPE_KEY and keys:
            raise serializers.ValidationError(
                {"applied_keys": "Only key policies may have applied keys"}
            )
        # Project policies are persisted but not evaluated by the gateway yet.
        return attrs

    def _is_org_config_row(self):
        # The org config editor (views/org_config.py) stores org-wide checks in
        # the reserved __ui_guardrails__ row, and re-sends every org check on
        # each settings save, including values the Overview tab and the
        # update-guardrail API wrote (any action, a threshold such as 50).
        # Those writers are not bounded like this API, so refusing such a
        # value here would block every later settings save. The gateway
        # contract still validates that row's checks before they go live.
        instance = self.instance
        return isinstance(
            instance, AgentccGuardrailPolicy
        ) and instance.name.startswith("__")

    def validate_checks(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("checks must be a JSON array")
        strict = not self._is_org_config_row()
        for i, check in enumerate(value):
            if not isinstance(check, dict):
                raise serializers.ValidationError(
                    f"Check at index {i} must be a JSON object"
                )
            if not isinstance(check.get("name"), str) or not check["name"].strip():
                raise serializers.ValidationError(
                    f"Check at index {i} must have a non-empty name"
                )
            if not strict:
                continue
            if "action" in check and check["action"] not in ("block", "warn", "log"):
                raise serializers.ValidationError(
                    f"Check at index {i} has invalid action"
                )
            for field in ("threshold", "confidence_threshold"):
                if field in check and (
                    isinstance(check[field], bool)
                    or not isinstance(check[field], (int, float))
                    or not 0 <= check[field] <= 1
                ):
                    raise serializers.ValidationError(
                        f"Check at index {i} has invalid {field}"
                    )
            if "enabled" in check and not isinstance(check["enabled"], bool):
                raise serializers.ValidationError(
                    f"Check at index {i} has invalid enabled"
                )
            if "config" in check and not isinstance(check["config"], dict):
                raise serializers.ValidationError(
                    f"Check at index {i} has invalid config"
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
