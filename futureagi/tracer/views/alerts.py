"""Unified alert API; each alert kind retains its own persistence and evaluator."""

from django.db import transaction
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from tfc.ee_gating import EEResource, check_ee_can_create
from tfc.utils.api_contracts import validated_request
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
    data = dict(request.data)
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
    trigger_value = serializers.JSONField(required=False)
    filters = serializers.JSONField(required=False)
    slack_connection_id = serializers.UUIDField(required=False)
    slack_channel_id = serializers.CharField(required=False)
    cooldown_seconds = serializers.IntegerField(required=False, min_value=0)
    project = serializers.UUIDField(required=False)
    metric_type = serializers.CharField(required=False)
    threshold_type = serializers.CharField(required=False)
    threshold_operator = serializers.CharField(required=False)
    critical_threshold_value = serializers.FloatField(required=False)
    warning_threshold_value = serializers.FloatField(required=False)
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


class AlertView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            organization, workspace = _scope(request)
            kind = request.query_params.get("kind")
            if kind not in (None, "error_feed", "metric"):
                raise FeedAlertError("Unknown alert kind.")
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
            return Response(
                {"status": False, "error": str(exc)}, status=exc.status_code
            )

    @validated_request(AlertWriteRequestSerializer)
    def post(self, request):
        try:
            organization, workspace = _scope(request)
            _require_write(request, workspace)
            kind = request.data.get("kind")
            if kind == "error_feed":
                data = dict(request.data)
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
            return Response(
                {"status": False, "error": str(exc)}, status=exc.status_code
            )


class AlertOptionsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            organization, workspace = _scope(request)
            kind = request.query_params.get("kind", "error_feed")
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
            return Response(
                {"status": False, "error": str(exc)}, status=exc.status_code
            )


class AlertDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_metric(self, alert_id, organization, workspace):
        monitor = _metric_queryset(organization, workspace).filter(pk=alert_id).first()
        if monitor is None:
            raise FeedAlertError("Alert not found.", 404)
        return monitor

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
            return Response(
                {"status": False, "error": str(exc)}, status=exc.status_code
            )

    @validated_request(AlertWriteRequestSerializer)
    def patch(self, request, kind, alert_id):
        try:
            organization, workspace = _scope(request)
            _require_write(request, workspace)
            if kind == "error_feed":
                result = update_rule(
                    rule_id=alert_id,
                    data=dict(request.data),
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
            return Response(
                {"status": False, "error": str(exc)}, status=exc.status_code
            )

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
            return Response(
                {"status": False, "error": str(exc)}, status=exc.status_code
            )


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
            return Response(
                {"status": False, "error": str(exc)}, status=exc.status_code
            )
