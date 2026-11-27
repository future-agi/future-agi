"""The run page's call filter, read by the run page's views and by an ask."""

from __future__ import annotations

import json
import uuid

from rest_framework import serializers

from simulate.services.run_dashboard_v3 import GOAL_OUTCOMES
from simulate.services.run_results_v3 import OUTCOME_LABELS


class JsonObjectField(serializers.JSONField):
    """Accept a JSON object both in query strings and request bodies."""

    swagger_empty_input_default = "{}"

    def to_internal_value(self, data):
        if data in (None, ""):
            return {}
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError as exc:
                raise serializers.ValidationError("Value must be valid JSON.") from exc
        if not isinstance(data, dict):
            raise serializers.ValidationError("Value must be an object.")
        return data


class RunCallFiltersSerializer(serializers.Serializer):
    search = serializers.CharField(default="", allow_blank=True, required=False)
    filters = JsonObjectField(default=dict, required=False)
    ordering = serializers.ChoiceField(
        choices=[
            "started_at",
            "-started_at",
            "duration_seconds",
            "-duration_seconds",
            "latency_ms",
            "-latency_ms",
            "turn_count",
            "-turn_count",
            "tokens",
            "-tokens",
            "cost_cents",
            "-cost_cents",
            "scenario",
            "-scenario",
            "goal",
            "-goal",
            "outcome",
            "-outcome",
        ],
        default="-started_at",
        required=False,
    )

    def validate_filters(self, value):
        allowed = {
            "goal",
            "goal_not",
            "sub_goal",
            "sub_goal_not",
            "actor",
            "actor_not",
            "status",
            "status_not",
            "goal_outcome",
            "call_execution_id",
        }
        unknown = set(value) - allowed
        if unknown:
            raise serializers.ValidationError(
                f"Unsupported filters: {', '.join(sorted(unknown))}."
            )
        for field, selected in value.items():
            if not isinstance(selected, list) or not all(
                isinstance(item, str) for item in selected
            ):
                raise serializers.ValidationError(
                    f"Filter '{field}' must be a list of strings."
                )
            # Refuses null and surrogate characters, which the database cannot hold.
            serializers.ListField(
                child=serializers.CharField(allow_blank=True)
            ).run_validation(selected)
        invalid_statuses = {
            *value.get("status", []),
            *value.get("status_not", []),
        } - set(OUTCOME_LABELS)
        if invalid_statuses:
            raise serializers.ValidationError(
                f"Unsupported statuses: {', '.join(sorted(invalid_statuses))}."
            )
        invalid_goal_outcomes = set(value.get("goal_outcome", [])) - set(GOAL_OUTCOMES)
        if invalid_goal_outcomes:
            raise serializers.ValidationError(
                "Unsupported goal outcomes: "
                f"{', '.join(sorted(invalid_goal_outcomes))}."
            )
        for call_id in value.get("call_execution_id", []):
            try:
                uuid.UUID(call_id)
            except ValueError as exc:
                raise serializers.ValidationError(
                    f"Invalid call_execution_id: {call_id}."
                ) from exc
        return value
