"""Contracts of ``GET /integrations/destinations/``: the one destination registry (webhooks DESIGN.md section 11.1),
with what this install and this caller may do with webhooks, so the Settings > Integrations page can render the
Webhooks section only where it works."""

from __future__ import annotations

from rest_framework import serializers

from tfc.utils.api_serializers import StrictInputSerializer


class DestinationsQuery(StrictInputSerializer):
    kind = serializers.CharField(
        required=False, max_length=50, help_text="Only this kind, e.g. webhook or slack"
    )


class DestinationHealthOut(serializers.Serializer):
    state = serializers.ChoiceField(
        choices=["healthy", "failing", "slow", "off", "blocked", "unknown"]
    )
    reason = serializers.CharField(allow_null=True)
    since = serializers.DateTimeField(allow_null=True)


class DestinationUseOut(serializers.Serializer):
    kind = serializers.CharField()
    id = serializers.CharField()
    name = serializers.CharField()


class DestinationStatsOut(serializers.Serializer):
    total = serializers.IntegerField()
    succeeded = serializers.IntegerField()
    failed = serializers.IntegerField()


class DestinationOut(serializers.Serializer):
    kind = serializers.CharField()
    id = serializers.CharField()
    name = serializers.CharField()
    health = DestinationHealthOut()
    used_by = DestinationUseOut(many=True)
    last_used_at = serializers.DateTimeField(allow_null=True)
    org_wide = serializers.BooleanField(required=False)
    stats_24h = DestinationStatsOut(
        required=False,
        help_text="Webhook endpoints: deliveries made in the last 24 hours",
    )


class WebhooksAccessOut(serializers.Serializer):
    available = serializers.BooleanField(
        help_text="False: the Webhooks section is absent on this install"
    )
    reasons = serializers.ListField(child=serializers.CharField())
    can_view = serializers.BooleanField()
    can_manage = serializers.BooleanField(
        help_text="Create, edit, test and redeliver in the current workspace"
    )
    can_admin = serializers.BooleanField(
        help_text="Workspace admin: run targeting, bulk redeliver, resume, the workspace policy"
    )
    org_admin = serializers.BooleanField(
        help_text="Org-wide endpoints and the org policy"
    )
    workspace_id = serializers.CharField(allow_null=True)


class DestinationsOut(serializers.Serializer):
    destinations = DestinationOut(many=True)
    webhooks = WebhooksAccessOut()


class DestinationsResponse(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = DestinationsOut()
