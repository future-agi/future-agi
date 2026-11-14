"""Shared Error Feed alert operations for HTTP and Falcon."""

from __future__ import annotations

import uuid

from django.db import transaction
from django.db.models import OuterRef, Subquery
from rest_framework import serializers

from integrations.models import (
    ConnectionStatus,
    IntegrationConnection,
    IntegrationPlatform,
)
from integrations.services.credentials import CredentialManager
from tfc.ee_gating import EEFeature, FeatureUnavailable, check_ee_feature
from tracer.models.feed_alert import (
    ErrorFeedAlertDelivery,
    ErrorFeedAlertRule,
    FeedAlertTrigger,
)
from tracer.models.project import Project


class FeedAlertError(Exception):
    def __init__(self, message: str, status_code: int = 400, field: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.field = field


_SEVERITIES = {"low", "medium", "high", "critical"}
_FILTER_CHOICES = {
    "sources": {"scanner", "eval"},
    "severities": _SEVERITIES,
    "statuses": {"for_review", "escalating", "acknowledged", "resolved"},
}
_FILTER_KEYS = {*_FILTER_CHOICES, "issue_groups", "issue_categories"}
_WRITE_KEYS = {
    "name",
    "enabled",
    "project_id",
    "trigger_type",
    "severity_threshold",
    "occurrence_threshold",
    "filters",
    "slack_connection_id",
    "slack_channel_id",
    "cooldown_seconds",
}


def _entitled(organization) -> None:
    try:
        check_ee_feature(EEFeature.ERROR_FEED, org_id=str(organization.id))
    except FeatureUnavailable as exc:
        raise FeedAlertError("Error Feed is not available on this plan.", 402) from exc


def _can_write(user, workspace) -> None:
    if not user or not user.can_write_to_workspace(workspace):
        raise FeedAlertError("Workspace write access is required.", 403)


def _project(project_id, organization, workspace):
    if project_id in (None, ""):
        return None
    try:
        project_id = uuid.UUID(str(project_id))
    except (TypeError, ValueError) as exc:
        raise FeedAlertError("Project ID must be a UUID.", field="project_id") from exc
    project = Project.no_workspace_objects.filter(
        pk=project_id,
        organization=organization,
        workspace=workspace,
        trace_type="observe",
        deleted=False,
    ).first()
    if project is None:
        raise FeedAlertError(
            "Project is not available in this workspace.", 404, "project_id"
        )
    return project


def _connection(connection_id, organization, workspace):
    try:
        connection_id = uuid.UUID(str(connection_id))
    except (TypeError, ValueError) as exc:
        raise FeedAlertError(
            "Slack connection ID must be a UUID.", field="slack_connection_id"
        ) from exc
    connection = IntegrationConnection.no_workspace_objects.filter(
        pk=connection_id,
        organization=organization,
        workspace=workspace,
        platform=IntegrationPlatform.SLACK,
        status=ConnectionStatus.ACTIVE,
        deleted=False,
    ).first()
    if connection is None:
        raise FeedAlertError(
            "Connect an active Slack workspace first.", 400, "slack_connection_id"
        )
    return connection


def _channel(connection, channel_id: str, *, join_public: bool = False) -> str:
    if not isinstance(channel_id, str) or not channel_id or len(channel_id) > 80:
        raise FeedAlertError("Choose a Slack channel.", field="slack_channel_id")
    from integrations.services.slack_service import SlackApiError, SlackService

    credentials = CredentialManager.decrypt(bytes(connection.encrypted_credentials))
    try:
        channel = SlackService().validate_channel(
            credentials, channel_id, join_public=join_public
        )
    except SlackApiError as exc:
        if exc.code == "missing_scope":
            raise FeedAlertError(
                "Reconnect Slack to allow the app to join public channels.",
                409,
                "slack_connection_id",
            ) from exc
        if exc.code == "not_in_channel":
            raise FeedAlertError(
                (
                    "For a private channel, add the Slack app to the channel first."
                    if join_public
                    else "The Slack app is no longer in this channel."
                ),
                field="slack_channel_id",
            ) from exc
        raise FeedAlertError(
            f"Slack channel is unavailable ({exc.code}).", field="slack_channel_id"
        ) from exc
    except Exception as exc:
        raise FeedAlertError(
            "Slack channel is unavailable.", field="slack_channel_id"
        ) from exc
    name = channel.get("name") if isinstance(channel, dict) else None
    if not isinstance(name, str) or not name:
        raise FeedAlertError("Slack channel is unavailable.", field="slack_channel_id")
    return name[:255]


def _validate_filters(value) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict) or set(value) - _FILTER_KEYS:
        raise FeedAlertError("Unsupported Error Feed filter.", field="filters")
    result = {}
    for key, items in value.items():
        if (
            not isinstance(items, list)
            or len(items) > 50
            or not all(isinstance(item, str) and 0 < len(item) <= 100 for item in items)
        ):
            raise FeedAlertError(
                f"{key} must be a list of short values.", field=f"filters.{key}"
            )
        if key in _FILTER_CHOICES and set(items) - _FILTER_CHOICES[key]:
            raise FeedAlertError(
                f"Unsupported {key} filter value.", field=f"filters.{key}"
            )
        result[key] = sorted(set(items))
    return result


def _validate_trigger(trigger_type, trigger_value):
    if trigger_type not in FeedAlertTrigger.values:
        raise FeedAlertError("Unsupported Error Feed trigger.", field="trigger_type")
    if trigger_type == FeedAlertTrigger.SEVERITY_REACHED:
        if not isinstance(trigger_value, str) or trigger_value not in _SEVERITIES:
            raise FeedAlertError(
                "Select a severity threshold.", field="severity_threshold"
            )
    elif trigger_type == FeedAlertTrigger.OCCURRENCES_CROSSED:
        if type(trigger_value) is not int or not 1 <= trigger_value <= 1_000_000:
            raise FeedAlertError(
                "Occurrence threshold must be between 1 and 1000000.",
                field="occurrence_threshold",
            )
    elif trigger_value is not None:
        raise FeedAlertError(
            "This trigger does not take a value.", field="trigger_type"
        )


class FeedAlertRuleSerializer(serializers.Serializer):
    """The one Error Feed rule contract used for writes and representations."""

    name = serializers.CharField(max_length=255, trim_whitespace=True)
    enabled = serializers.BooleanField(required=False)
    project_id = serializers.UUIDField(required=False, allow_null=True)
    trigger_type = serializers.ChoiceField(choices=FeedAlertTrigger.choices)
    severity_threshold = serializers.ChoiceField(
        choices=["low", "medium", "high", "critical"], required=False
    )
    occurrence_threshold = serializers.IntegerField(
        required=False, min_value=1, max_value=1_000_000
    )
    filters = serializers.JSONField(required=False)
    slack_connection_id = serializers.UUIDField()
    slack_channel_id = serializers.CharField(max_length=80)
    cooldown_seconds = serializers.IntegerField(
        required=False, min_value=0, max_value=86400
    )

    def to_internal_value(self, data):
        if not isinstance(data, dict):
            raise serializers.ValidationError(
                {"non_field_errors": ["Expected an object."]}
            )
        unknown = set(data) - _WRITE_KEYS
        if unknown:
            raise serializers.ValidationError(
                {field: ["Unsupported alert rule field."] for field in sorted(unknown)}
            )
        return super().to_internal_value(data)

    def to_representation(self, rule):
        latest_status = getattr(rule, "latest_delivery_status", None)
        latest_error = getattr(rule, "latest_delivery_error", None)
        data = {
            "id": str(rule.id),
            "kind": "error_feed",
            "name": rule.name,
            "enabled": rule.enabled,
            "project_id": str(rule.project_id) if rule.project_id else None,
            "trigger_type": rule.trigger_type,
            "filters": rule.filters,
            "slack_connection_id": str(rule.slack_connection_id),
            "slack_channel_id": rule.slack_channel_id,
            "slack_channel_name": rule.slack_channel_name,
            "cooldown_seconds": rule.cooldown_seconds,
            "last_triggered_at": rule.last_triggered_at,
            "health": "failed" if latest_status == "failed" else "healthy",
            "failure_reason": latest_error if latest_status == "failed" else None,
            "created_at": rule.created_at,
            "updated_at": rule.updated_at,
        }
        if rule.trigger_type == FeedAlertTrigger.SEVERITY_REACHED:
            data["severity_threshold"] = rule.trigger_value
        elif rule.trigger_type == FeedAlertTrigger.OCCURRENCES_CROSSED:
            data["occurrence_threshold"] = rule.trigger_value
        return data

    def validate(self, attrs):
        current = self.context.get("current")
        organization = self.context["organization"]
        workspace = self.context["workspace"]
        merged = self.to_representation(current) if current is not None else {}
        merged.update(attrs)
        trigger_type = merged.get("trigger_type")
        if (
            "severity_threshold" in attrs
            and trigger_type != FeedAlertTrigger.SEVERITY_REACHED
        ):
            raise serializers.ValidationError(
                {"severity_threshold": ["This threshold requires a severity trigger."]}
            )
        if (
            "occurrence_threshold" in attrs
            and trigger_type != FeedAlertTrigger.OCCURRENCES_CROSSED
        ):
            raise serializers.ValidationError(
                {
                    "occurrence_threshold": [
                        "This threshold requires an occurrence trigger."
                    ]
                }
            )
        trigger_value = (
            merged.get("severity_threshold")
            if trigger_type == FeedAlertTrigger.SEVERITY_REACHED
            else merged.get("occurrence_threshold")
            if trigger_type == FeedAlertTrigger.OCCURRENCES_CROSSED
            else None
        )
        try:
            _validate_trigger(trigger_type, trigger_value)
            filters = _validate_filters(merged.get("filters", {}))
            project = _project(merged.get("project_id"), organization, workspace)
            connection = _connection(
                merged.get("slack_connection_id"), organization, workspace
            )
            channel_id = merged.get("slack_channel_id")
            channel_name = (
                current.slack_channel_name
                if current is not None
                and current.slack_connection_id == connection.id
                and current.slack_channel_id == channel_id
                else _channel(connection, channel_id, join_public=True)
            )
        except FeedAlertError as exc:
            self._status_code = exc.status_code
            raise serializers.ValidationError(
                {exc.field or "non_field_errors": [str(exc)]}
            ) from exc
        return {
            "name": merged["name"],
            "enabled": merged.get("enabled", True),
            "project": project,
            "trigger_type": merged["trigger_type"],
            "trigger_value": trigger_value,
            "filters": filters,
            "slack_connection": connection,
            "slack_channel_id": channel_id,
            "slack_channel_name": channel_name,
            "cooldown_seconds": merged.get("cooldown_seconds", 3600),
        }


def serialize_rule(rule: ErrorFeedAlertRule) -> dict:
    return dict(FeedAlertRuleSerializer(rule).data)


def _with_delivery_health(queryset):
    latest = ErrorFeedAlertDelivery.no_workspace_objects.filter(
        rule_id=OuterRef("pk")
    ).order_by("-created_at")
    return queryset.annotate(
        latest_delivery_status=Subquery(latest.values("status")[:1]),
        latest_delivery_error=Subquery(latest.values("error_code")[:1]),
    )


def list_rules(*, organization, workspace) -> list[dict]:
    _entitled(organization)
    return [
        serialize_rule(rule)
        for rule in _with_delivery_health(
            ErrorFeedAlertRule.no_workspace_objects.filter(
                organization=organization, workspace=workspace, deleted=False
            )
        ).order_by("-created_at")[:500]
    ]


def get_rule(*, rule_id, organization, workspace) -> dict:
    _entitled(organization)
    rule = _with_delivery_health(
        ErrorFeedAlertRule.no_workspace_objects.filter(
            pk=rule_id, organization=organization, workspace=workspace, deleted=False
        )
    ).first()
    if rule is None:
        raise FeedAlertError("Alert rule not found.", 404)
    return serialize_rule(rule)


def _validated(data, *, organization, workspace, current=None) -> dict:
    serializer = FeedAlertRuleSerializer(
        data=data,
        partial=current is not None,
        context={
            "organization": organization,
            "workspace": workspace,
            "current": current,
        },
    )
    if not serializer.is_valid():
        field, errors = next(iter(serializer.errors.items()))
        message = str(errors[0]) if isinstance(errors, list) else str(errors)
        raise FeedAlertError(message, getattr(serializer, "_status_code", 400), field)
    return serializer.validated_data


def create_rule(*, data, organization, workspace, user) -> dict:
    _entitled(organization)
    _can_write(user, workspace)
    values = _validated(data, organization=organization, workspace=workspace)
    rule = ErrorFeedAlertRule.no_workspace_objects.create(
        organization=organization, workspace=workspace, created_by=user, **values
    )
    return serialize_rule(rule)


def update_rule(*, rule_id, data, organization, workspace, user) -> dict:
    _entitled(organization)
    _can_write(user, workspace)
    with transaction.atomic():
        rule = (
            ErrorFeedAlertRule.no_workspace_objects.select_for_update()
            .filter(
                pk=rule_id,
                organization=organization,
                workspace=workspace,
                deleted=False,
            )
            .first()
        )
        if rule is None:
            raise FeedAlertError("Alert rule not found.", 404)
        values = _validated(
            data, organization=organization, workspace=workspace, current=rule
        )
        for field, value in values.items():
            setattr(rule, field, value)
        rule.save(update_fields=[*values, "updated_at"])
    return serialize_rule(rule)


def delete_rule(*, rule_id, organization, workspace, user) -> dict:
    _entitled(organization)
    _can_write(user, workspace)
    rule = ErrorFeedAlertRule.no_workspace_objects.filter(
        pk=rule_id, organization=organization, workspace=workspace, deleted=False
    ).first()
    if rule is None:
        raise FeedAlertError("Alert rule not found.", 404)
    rule.enabled = False
    rule.save(update_fields=["enabled", "updated_at"])
    rule.delete()
    return {"id": str(rule.id), "deleted": True}


def test_rule(*, rule_id, organization, workspace, user) -> dict:
    _entitled(organization)
    _can_write(user, workspace)
    rule = ErrorFeedAlertRule.no_workspace_objects.filter(
        pk=rule_id, organization=organization, workspace=workspace, deleted=False
    ).first()
    if rule is None:
        raise FeedAlertError("Alert rule not found.", 404)
    connection = _connection(rule.slack_connection_id, organization, workspace)
    _channel(connection, rule.slack_channel_id)
    from integrations.services.slack_service import SlackService

    credentials = CredentialManager.decrypt(bytes(connection.encrypted_credentials))
    try:
        result = SlackService().post_message(
            credentials,
            rule.slack_channel_id,
            f"[Test] Error Feed alert: {rule.name}",
        )
    except Exception as exc:
        raise FeedAlertError("Slack test notification failed.", 502) from exc
    return {"sent": True, "slack_ts": result.get("ts", "")}
