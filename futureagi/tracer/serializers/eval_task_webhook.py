"""Shared create/edit webhook field validation."""

import math

from rest_framework import serializers

from tfc.utils.api_serializers import StrictInputSerializer
from tfc.utils.ssrf_guard import SsrfBlocked
from tfc.utils.webhook_delivery import validate_webhook_url


class WebhookURLField(serializers.URLField):
    def run_validation(self, data=serializers.empty):
        value = super().run_validation(data)
        if value:
            try:
                # Saving a task must never wait on destination DNS. Delivery
                # resolves again under a deadline and pins a public address.
                validate_webhook_url(value, resolve=False, https_only=True)
            except SsrfBlocked as exc:
                raise serializers.ValidationError(str(exc)) from None
        return value


class WebhookConditionSerializer(StrictInputSerializer):
    eval_config_id = serializers.UUIDField()
    operator = serializers.ChoiceField(choices=["gt", "lt", "eq", "fail"])
    value = serializers.JSONField(required=False, allow_null=True)

    def validate(self, attrs):
        operator = attrs["operator"]
        value = attrs.get("value")
        if operator in ("gt", "lt") and (
            type(value) not in (int, float)
            or (type(value) is float and not math.isfinite(value))
        ):
            raise serializers.ValidationError(
                {"value": "A finite numeric threshold is required."}
            )
        if operator == "eq" and (
            type(value) not in (str, bool, int, float)
            or (type(value) is float and not math.isfinite(value))
        ):
            raise serializers.ValidationError(
                {"value": "A string, boolean or finite number is required."}
            )
        if operator == "fail":
            attrs["value"] = None
        attrs["eval_config_id"] = str(attrs["eval_config_id"])
        return attrs

    def to_internal_value(self, data):
        # The enclosing task may be PATCHed, but a supplied rule is a complete
        # replacement JSON object, not a partially updated nested resource.
        if not isinstance(data, dict) or not {
            "eval_config_id", "operator"
        } <= data.keys():
            raise serializers.ValidationError(
                "eval_config_id and operator are required."
            )
        return super().to_internal_value(data)


def validate_task_webhook(attrs, instance=None):
    condition = attrs.get(
        "webhook_condition", getattr(instance, "webhook_condition", None)
    )
    url = attrs.get("webhook_url", getattr(instance, "webhook_url", ""))
    if url and not condition:
        raise serializers.ValidationError(
            {"webhook_condition": "A condition is required when a webhook URL is set."}
        )
    if not condition:
        return
    configs = attrs.get("evals")
    if configs is None:
        configs = instance.evals.using("default").all() if instance is not None else []
    project = attrs.get("project", getattr(instance, "project", None))
    if not any(
        str(config.id) == condition["eval_config_id"]
        and project is not None
        and config.project_id == project.id
        for config in configs
    ):
        raise serializers.ValidationError(
            {"webhook_condition": "Choose an evaluation configured on this task in its project."}
        )
