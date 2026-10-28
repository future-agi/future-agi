from rest_framework import serializers

from agentcc.guardrail_options import (
    GuardrailOptionsError,
    validate_guardrail_collection,
)
from agentcc.models import AgentccOrgConfig
from agentcc.org_config_defaults import (
    default_cache_config,
    default_cost_tracking_config,
    normalize_cache_config,
    normalize_cost_tracking_config,
)


class AgentccOrgConfigSerializer(serializers.ModelSerializer):
    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["cost_tracking"] = normalize_cost_tracking_config(
            data.get("cost_tracking")
        )
        data["cache"] = normalize_cache_config(data.get("cache"))
        return data

    class Meta:
        model = AgentccOrgConfig
        fields = [
            "id",
            "organization",
            "version",
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
            "is_active",
            "created_by",
            "change_description",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "organization",
            "version",
            "is_active",
            "created_by",
            "created_at",
            "updated_at",
        ]


class AgentccOrgConfigWriteSerializer(serializers.Serializer):
    """Serializer for creating a new org config version."""

    guardrails = serializers.JSONField(required=False, default=dict)
    routing = serializers.JSONField(required=False, default=dict)
    cache = serializers.JSONField(required=False, default=default_cache_config)
    rate_limiting = serializers.JSONField(required=False, default=dict)
    budgets = serializers.JSONField(required=False, default=dict)
    cost_tracking = serializers.JSONField(
        required=False, default=default_cost_tracking_config
    )
    ip_acl = serializers.JSONField(required=False, default=dict)
    alerting = serializers.JSONField(required=False, default=dict)
    privacy = serializers.JSONField(required=False, default=dict)
    tool_policy = serializers.JSONField(required=False, default=dict)
    mcp = serializers.JSONField(required=False, default=dict)
    a2a = serializers.JSONField(required=False, default=dict)
    audit = serializers.JSONField(required=False, default=dict)
    model_database = serializers.JSONField(required=False, default=dict)
    model_map = serializers.JSONField(required=False, default=dict)
    change_description = serializers.CharField(
        required=False, default="", allow_blank=True
    )

    def validate_guardrails(self, value):
        try:
            validate_guardrail_collection(value)
        except GuardrailOptionsError as error:
            raise serializers.ValidationError(str(error)) from error
        return value

    def validate_routing(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("routing must be a JSON object")
        return value

    def validate_cache(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("cache must be a JSON object")
        return normalize_cache_config(value)

    def validate_rate_limiting(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("rate_limiting must be a JSON object")
        return value

    def validate_budgets(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("budgets must be a JSON object")
        valid_periods = {"daily", "weekly", "monthly", "total"}
        # Validate hierarchical budget levels: teams, users, keys, tags.
        for level_key in ("teams", "users", "keys", "tags"):
            level_map = value.get(level_key)
            if level_map is None:
                continue
            if not isinstance(level_map, dict):
                raise serializers.ValidationError(
                    f"budgets.{level_key} must be a JSON object"
                )
            for name, entry in level_map.items():
                if not isinstance(entry, dict):
                    raise serializers.ValidationError(
                        f"budgets.{level_key}.{name} must be a JSON object"
                    )
                if "limit" in entry:
                    try:
                        float(entry["limit"])
                    except (TypeError, ValueError):
                        raise serializers.ValidationError(
                            f"budgets.{level_key}.{name}.limit must be a number"
                        )
                if "period" in entry and entry["period"] not in valid_periods:
                    raise serializers.ValidationError(
                        f"budgets.{level_key}.{name}.period must be one of: "
                        f"{', '.join(sorted(valid_periods))}"
                    )
                if "hard" in entry and not isinstance(entry["hard"], bool):
                    raise serializers.ValidationError(
                        f"budgets.{level_key}.{name}.hard must be a boolean"
                    )
                per_model = entry.get("per_model")
                if per_model is not None:
                    if not isinstance(per_model, dict):
                        raise serializers.ValidationError(
                            f"budgets.{level_key}.{name}.per_model must be a JSON object"
                        )
                    for model_name, model_limit in per_model.items():
                        try:
                            float(model_limit)
                        except (TypeError, ValueError):
                            raise serializers.ValidationError(
                                f"budgets.{level_key}.{name}.per_model.{model_name} must be a number"
                            )
        return value

    def validate_cost_tracking(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("cost_tracking must be a JSON object")
        return normalize_cost_tracking_config(value)

    def validate_ip_acl(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("ip_acl must be a JSON object")
        return value

    def validate_alerting(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("alerting must be a JSON object")
        return value

    def validate_privacy(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("privacy must be a JSON object")
        return value

    def validate_tool_policy(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("tool_policy must be a JSON object")
        return value

    def validate_mcp(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("mcp must be a JSON object")
        return value

    def validate_a2a(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("a2a must be a JSON object")
        return value

    def validate_audit(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("audit must be a JSON object")
        valid_severities = {"info", "warn", "error", "critical"}
        if value.get("min_severity") and value["min_severity"] not in valid_severities:
            raise serializers.ValidationError(
                f"min_severity must be one of: {', '.join(sorted(valid_severities))}"
            )
        valid_sink_types = {"stdout", "file", "webhook"}
        for sink in value.get("sinks", []):
            if not isinstance(sink, dict):
                raise serializers.ValidationError("Each sink must be a JSON object")
            sink_type = sink.get("type", "stdout")
            if sink_type not in valid_sink_types:
                raise serializers.ValidationError(
                    f"Sink type must be one of: {', '.join(sorted(valid_sink_types))}"
                )
        return value

    def validate_model_database(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("model_database must be a JSON object")
        overrides = value.get("overrides", {})
        if overrides and not isinstance(overrides, dict):
            raise serializers.ValidationError(
                "model_database.overrides must be a JSON object"
            )
        for model_id, override in overrides.items():
            if not isinstance(override, dict):
                raise serializers.ValidationError(
                    f"Override for model '{model_id}' must be a JSON object"
                )
        return value

    def validate_model_map(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("model_map must be a JSON object")
        for model_name, provider_id in value.items():
            if not isinstance(model_name, str) or not isinstance(provider_id, str):
                raise serializers.ValidationError(
                    "model_map must be a mapping of string model names to string provider IDs"
                )
        return value
