"""Unified alert API; each alert kind retains its own persistence and evaluator."""

from django.db import transaction
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from tfc.ee_gating import EEResource, check_ee_can_create
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_errors import build_error_envelope
from tracer.models.feed_alert import FeedAlertTrigger
from tracer.models.monitor import UserAlertMonitor
from tracer.models.project import Project
from tracer.serializers.monitor import UserAlertMonitorSerializer
from tracer.services.feed_alerts.rules import (
    FeedAlertError,
    create_rule,
    delete_rule,
    get_rule,
    list_rules,
    test_rule,
    update_rule,
)


def _scope(request):
    organization = getattr(request, "organization", None) or request.user.organization
    workspace = getattr(request, "workspace", None)
    if (
        organization is None
        or workspace is None
        or workspace.organization_id != organization.id
    ):
        raise FeedAlertError("Select a workspace to manage alerts.", 400)
    return organization, workspace


def _error_response(exc):
    return Response(
        build_error_envelope(
            str(exc),
            status_code=exc.status_code,
            details={exc.field: [str(exc)]} if exc.field else None,
        ),
        status=exc.status_code,
    )


def _require_write(request, workspace):
    if not request.user.can_write_to_workspace(workspace):
        raise FeedAlertError("Workspace write access is required.", 403)


def _metric_queryset(organization, workspace):
    return UserAlertMonitor.no_workspace_objects.filter(
        organization=organization, workspace=workspace, deleted=False
    )


def _metric_data(monitor):
    data = dict(UserAlertMonitorSerializer(monitor).data)
    data["kind"] = "metric"
    data["enabled"] = not monitor.is_mute
    return data


def _metric_write(request, organization, workspace, monitor=None):
    data = dict(request.validated_data)
    data.pop("kind", None)
    if "enabled" in data:
        enabled = data.pop("enabled")
        if type(enabled) is not bool:
            raise FeedAlertError("Enabled must be true or false.")
        data["is_mute"] = not enabled
    forbidden = {
        "organization",
        "workspace",
        "created_by",
        "deleted",
        "deleted_at",
        "logs",
        "last_checked_at",
    }
    if forbidden.intersection(data):
        raise FeedAlertError("Alert scope and state are managed by the server.")
    project_id = data.get("project", monitor.project_id if monitor else None)
    if (
        project_id
        and not Project.no_workspace_objects.filter(
            pk=project_id,
            organization=organization,
            workspace=workspace,
            trace_type="observe",
            deleted=False,
        ).exists()
    ):
        raise FeedAlertError("Project is not available in this workspace.", 404)
    if monitor is None:
        data["organization"] = organization.id
        data["workspace"] = workspace.id
        data["created_by"] = request.user.id
    serializer = UserAlertMonitorSerializer(
        monitor, data=data, partial=monitor is not None
    )
    if not serializer.is_valid():
        raise FeedAlertError(serializer.errors)
    with transaction.atomic():
        saved = serializer.save(
            organization=organization,
            workspace=workspace,
            **({"created_by": request.user} if monitor is None else {}),
        )
    return _metric_data(saved)


class AlertWriteRequestSerializer(serializers.Serializer):
    """Document the shared metric and Error Feed alert mutation surface."""

    kind = serializers.ChoiceField(choices=["metric", "error_feed"], required=False)
    name = serializers.CharField(required=False)
    enabled = serializers.BooleanField(required=False)
    project_id = serializers.UUIDField(required=False, allow_null=True)
    trigger_type = serializers.CharField(required=False)
    severity_threshold = serializers.ChoiceField(
        choices=["low", "medium", "high", "critical"], required=False
    )
    occurrence_threshold = serializers.IntegerField(
        required=False, min_value=1, max_value=1_000_000
    )
    filters = serializers.JSONField(required=False)
    slack_connection_id = serializers.UUIDField(required=False)
    slack_channel_id = serializers.CharField(required=False)
    cooldown_seconds = serializers.IntegerField(required=False, min_value=0)
    project = serializers.UUIDField(required=False, allow_null=True)
    metric_type = serializers.CharField(required=False)
    metric = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    threshold_type = serializers.CharField(required=False)
    threshold_operator = serializers.CharField(required=False)
    threshold_metric_value = serializers.CharField(
        required=False, allow_null=True, allow_blank=True
    )
    critical_threshold_value = serializers.FloatField(required=False)
    warning_threshold_value = serializers.FloatField(required=False)
    alert_frequency = serializers.IntegerField(required=False)
    auto_threshold_time_window = serializers.IntegerField(required=False)
    slack_webhook_url = serializers.URLField(
        required=False, allow_null=True, allow_blank=True
    )
    slack_notes = serializers.CharField(
        required=False, allow_null=True, allow_blank=True
    )
    is_mute = serializers.BooleanField(required=False)
    notification_emails = serializers.ListField(
        child=serializers.EmailField(), required=False
    )


class AlertTestResultSerializer(serializers.Serializer):
    sent = serializers.BooleanField()
    slack_ts = serializers.CharField()


class AlertTestResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = AlertTestResultSerializer()


class AlertTestRequestSerializer(serializers.Serializer):
    pass


class AlertItemSerializer(serializers.Serializer):
    """Fields returned by the shared metric / Error Feed alert surface."""

    id = serializers.UUIDField()
    kind = serializers.ChoiceField(choices=["metric", "error_feed"])
    name = serializers.CharField()
    enabled = serializers.BooleanField()
    project_id = serializers.UUIDField(required=False, allow_null=True)
    trigger_type = serializers.CharField(required=False)
    severity_threshold = serializers.ChoiceField(
        choices=["low", "medium", "high", "critical"], required=False
    )
    occurrence_threshold = serializers.IntegerField(required=False)
    filters = serializers.JSONField(required=False)
    slack_connection_id = serializers.UUIDField(required=False)
    slack_channel_id = serializers.CharField(required=False)
    slack_channel_name = serializers.CharField(required=False)
    cooldown_seconds = serializers.IntegerField(required=False)
    last_triggered_at = serializers.DateTimeField(required=False, allow_null=True)
    health = serializers.CharField(required=False)
    failure_reason = serializers.CharField(required=False, allow_null=True)
    created_at = serializers.DateTimeField(required=False)
    updated_at = serializers.DateTimeField(required=False)
    project = serializers.UUIDField(required=False)
    organization = serializers.UUIDField(required=False)
    workspace = serializers.UUIDField(required=False, allow_null=True)
    created_by = serializers.UUIDField(required=False, allow_null=True)
    metric_type = serializers.CharField(required=False)
    metric = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    metric_name = serializers.CharField(required=False)
    threshold_type = serializers.CharField(required=False)
    threshold_operator = serializers.CharField(required=False)
    threshold_metric_value = serializers.CharField(
        required=False, allow_null=True, allow_blank=True
    )
    critical_threshold_value = serializers.FloatField(required=False, allow_null=True)
    warning_threshold_value = serializers.FloatField(required=False, allow_null=True)
    alert_frequency = serializers.IntegerField(required=False)
    auto_threshold_time_window = serializers.IntegerField(required=False)
    last_checked_at = serializers.DateTimeField(required=False, allow_null=True)
    slack_webhook_url = serializers.URLField(
        required=False, allow_null=True, allow_blank=True
    )
    slack_notes = serializers.CharField(
        required=False, allow_null=True, allow_blank=True
    )
    is_mute = serializers.BooleanField(required=False)
    deleted = serializers.BooleanField(required=False)
    deleted_at = serializers.DateTimeField(required=False, allow_null=True)
    logs = serializers.ListField(
        child=serializers.JSONField(), required=False, allow_null=True
    )
    notification_emails = serializers.ListField(
        child=serializers.EmailField(), required=False
    )


class AlertListQuerySerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=["metric", "error_feed"], required=False)


class AlertListResultSerializer(serializers.Serializer):
    alerts = AlertItemSerializer(many=True)


class AlertListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = AlertListResultSerializer()


class AlertItemResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = AlertItemSerializer()


class AlertOptionSerializer(serializers.Serializer):
    value = serializers.CharField()
    label = serializers.CharField()


class AlertProjectOptionSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()


class AlertOptionsResultSerializer(serializers.Serializer):
    triggers = AlertOptionSerializer(many=True)
    severities = serializers.ListField(child=serializers.CharField())
    statuses = serializers.ListField(child=serializers.CharField())
    sources = serializers.ListField(child=serializers.CharField())
    projects = AlertProjectOptionSerializer(many=True)


class AlertOptionsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = AlertOptionsResultSerializer()


class AlertDeletedResultSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    deleted = serializers.BooleanField()


class AlertDeletedResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = AlertDeletedResultSerializer()


class AlertView(APIView):
    permission_classes = [IsAuthenticated]

    @validated_request(
        query_serializer=AlertListQuerySerializer,
        responses={200: AlertListResponseSerializer},
    )
    def get(self, request):
        try:
            organization, workspace = _scope(request)
            kind = request.validated_query_data.get("kind")
            alerts = []
            if kind in (None, "metric"):
                alerts.extend(
                    _metric_data(item)
                    for item in _metric_queryset(organization, workspace).order_by(
                        "-created_at"
                    )[:500]
                )
            if kind in (None, "error_feed"):
                try:
                    alerts.extend(
                        list_rules(organization=organization, workspace=workspace)
                    )
                except FeedAlertError as exc:
                    if kind == "error_feed" or exc.status_code != 402:
                        raise
            return Response({"status": True, "result": {"alerts": alerts}})
        except FeedAlertError as exc:
            return _error_response(exc)

    @validated_request(
        AlertWriteRequestSerializer, responses={201: AlertItemResponseSerializer}
    )
    def post(self, request):
        try:
            organization, workspace = _scope(request)
            _require_write(request, workspace)
            kind = request.validated_data.get("kind")
            if kind == "error_feed":
                data = dict(request.validated_data)
                data.pop("kind", None)
                result = create_rule(
                    data=data,
                    organization=organization,
                    workspace=workspace,
                    user=request.user,
                )
            elif kind == "metric":
                check_ee_can_create(
                    EEResource.MONITORS,
                    org_id=str(organization.id),
                    current_count=UserAlertMonitor.no_workspace_objects.filter(
                        organization=organization, deleted=False
                    ).count(),
                )
                result = _metric_write(request, organization, workspace)
            else:
                raise FeedAlertError("Unknown alert kind.")
            return Response({"status": True, "result": result}, status=201)
        except FeedAlertError as exc:
            return _error_response(exc)


class AlertOptionsView(APIView):
    permission_classes = [IsAuthenticated]

    @validated_request(
        query_serializer=AlertListQuerySerializer,
        responses={200: AlertOptionsResponseSerializer},
    )
    def get(self, request):
        try:
            organization, workspace = _scope(request)
            kind = request.validated_query_data.get("kind", "error_feed")
            if kind != "error_feed":
                raise FeedAlertError("Unknown alert kind.")
            from tfc.ee_gating import EEFeature, check_ee_feature

            check_ee_feature(EEFeature.ERROR_FEED, org_id=str(organization.id))
            projects = Project.no_workspace_objects.filter(
                organization=organization,
                workspace=workspace,
                trace_type="observe",
                deleted=False,
            ).values("id", "name")
            return Response(
                {
                    "status": True,
                    "result": {
                        "triggers": [
                            {"value": value, "label": label}
                            for value, label in FeedAlertTrigger.choices
                        ],
                        "severities": ["low", "medium", "high", "critical"],
                        "statuses": [
                            "for_review",
                            "escalating",
                            "acknowledged",
                            "resolved",
                        ],
                        "sources": ["scanner", "eval"],
                        "projects": list(projects),
                    },
                }
            )
        except FeedAlertError as exc:
            return _error_response(exc)


class AlertDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_metric(self, alert_id, organization, workspace):
        monitor = _metric_queryset(organization, workspace).filter(pk=alert_id).first()
        if monitor is None:
            raise FeedAlertError("Alert not found.", 404)
        return monitor

    @validated_request(responses={200: AlertItemResponseSerializer})
    def get(self, request, kind, alert_id):
        try:
            organization, workspace = _scope(request)
            if kind == "error_feed":
                result = get_rule(
                    rule_id=alert_id, organization=organization, workspace=workspace
                )
            elif kind == "metric":
                result = _metric_data(
                    self._get_metric(alert_id, organization, workspace)
                )
            else:
                raise FeedAlertError("Unknown alert kind.", 404)
            return Response({"status": True, "result": result})
        except FeedAlertError as exc:
            return _error_response(exc)

    @validated_request(
        AlertWriteRequestSerializer,
        responses={200: AlertItemResponseSerializer},
        partial_request_validation=True,
    )
    def patch(self, request, kind, alert_id):
        try:
            organization, workspace = _scope(request)
            _require_write(request, workspace)
            if kind == "error_feed":
                result = update_rule(
                    rule_id=alert_id,
                    data=dict(request.validated_data),
                    organization=organization,
                    workspace=workspace,
                    user=request.user,
                )
            elif kind == "metric":
                monitor = self._get_metric(alert_id, organization, workspace)
                result = _metric_write(request, organization, workspace, monitor)
            else:
                raise FeedAlertError("Unknown alert kind.", 404)
            return Response({"status": True, "result": result})
        except FeedAlertError as exc:
            return _error_response(exc)

    @validated_request(responses={200: AlertDeletedResponseSerializer})
    def delete(self, request, kind, alert_id):
        try:
            organization, workspace = _scope(request)
            _require_write(request, workspace)
            if kind == "error_feed":
                result = delete_rule(
                    rule_id=alert_id,
                    organization=organization,
                    workspace=workspace,
                    user=request.user,
                )
            elif kind == "metric":
                monitor = self._get_metric(alert_id, organization, workspace)
                monitor.delete()
                result = {"id": str(alert_id), "deleted": True}
            else:
                raise FeedAlertError("Unknown alert kind.", 404)
            return Response({"status": True, "result": result})
        except FeedAlertError as exc:
            return _error_response(exc)


class ErrorFeedAlertTestView(APIView):
    permission_classes = [IsAuthenticated]

    @validated_request(
        AlertTestRequestSerializer,
        responses={200: AlertTestResponseSerializer},
    )
    def post(self, request, alert_id):
        try:
            organization, workspace = _scope(request)
            _require_write(request, workspace)
            result = test_rule(
                rule_id=alert_id,
                organization=organization,
                workspace=workspace,
                user=request.user,
            )
            return Response({"status": True, "result": result})
        except FeedAlertError as exc:
            return _error_response(exc)
