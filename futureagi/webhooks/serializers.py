"""Request and response contracts of the webhooks API (DESIGN.md section 10).

Requests are ``StrictInputSerializer``s, so unknown fields are refused. Responses use FA's success envelope
``{status: true, result: …}``; the response serializers below document ``result`` in the OpenAPI contract the
frontend and the SDK generate from, and ``validated_request`` checks them in DEBUG.
"""

from __future__ import annotations

from rest_framework import serializers

from tfc.utils.api_serializers import StrictInputSerializer
from webhooks.api_validation import FILTER_KEYS
from webhooks.core.fanout import CATALOG
from webhooks.core.signing import MAX_OVERLAP_S

EVENT_TYPES = tuple(CATALOG)
SUBSCRIPTION_TYPES = tuple(FILTER_KEYS)
DELIVERY_STATUSES = (
    "pending",
    "sending",
    "retrying",
    "succeeded",
    "failed",
    "paused",
    "skipped",
    "cancelled",
)


# ---- requests ----------------------------------------------------------------------------------------------------


class SubscriptionInput(StrictInputSerializer):
    event_type = serializers.CharField(
        max_length=100, help_text="A catalog type or prompt.* / deployment.run.*"
    )
    filters = serializers.DictField(required=False, default=dict)


class EndpointCreateRequest(StrictInputSerializer):
    name = serializers.CharField(max_length=200)
    description = serializers.CharField(
        required=False, allow_blank=True, max_length=2000
    )
    url = serializers.CharField(max_length=4096, trim_whitespace=False)
    kind = serializers.ChoiceField(choices=["http"], required=False)
    workspace_id = serializers.UUIDField(
        required=False,
        allow_null=True,
        help_text="Absent: the current workspace. Null: org-wide (org admins).",
    )
    subscriptions = SubscriptionInput(many=True)
    headers = serializers.DictField(
        child=serializers.CharField(allow_blank=True, trim_whitespace=False),
        required=False,
    )
    payload_mode = serializers.ChoiceField(choices=["thin", "full"], required=False)
    include_people_names = serializers.BooleanField(required=False)
    accepts_run_targeting = serializers.BooleanField(required=False)
    timeout_ms = serializers.IntegerField(
        min_value=5000, max_value=30000, required=False
    )
    rate_limit_per_s = serializers.IntegerField(
        min_value=1, max_value=100, required=False
    )
    max_in_flight = serializers.IntegerField(min_value=1, max_value=16, required=False)
    max_age_s = serializers.IntegerField(
        min_value=3600, max_value=259200, required=False
    )
    secret = serializers.CharField(
        required=False, max_length=200, trim_whitespace=False
    )

    def validate_subscriptions(self, value):
        return [dict(v) for v in value]


class EndpointUpdateRequest(StrictInputSerializer):
    name = serializers.CharField(max_length=200, required=False)
    description = serializers.CharField(
        required=False, allow_blank=True, max_length=2000
    )
    url = serializers.CharField(max_length=4096, required=False, trim_whitespace=False)
    subscriptions = SubscriptionInput(many=True, required=False)
    headers = serializers.DictField(
        child=serializers.CharField(allow_blank=True, trim_whitespace=False),
        required=False,
        allow_null=True,
    )
    payload_mode = serializers.ChoiceField(choices=["thin", "full"], required=False)
    include_people_names = serializers.BooleanField(required=False)
    accepts_run_targeting = serializers.BooleanField(required=False)
    timeout_ms = serializers.IntegerField(
        min_value=5000, max_value=30000, required=False
    )
    rate_limit_per_s = serializers.IntegerField(
        min_value=1, max_value=100, required=False
    )
    max_in_flight = serializers.IntegerField(min_value=1, max_value=16, required=False)
    max_age_s = serializers.IntegerField(
        min_value=3600, max_value=259200, required=False
    )

    def validate_subscriptions(self, value):
        return [dict(v) for v in value]


class EndpointListQuery(StrictInputSerializer):
    workspace = serializers.UUIDField(required=False)
    event_type = serializers.ChoiceField(choices=SUBSCRIPTION_TYPES, required=False)
    status = serializers.ChoiceField(
        choices=["enabled", "disabled_manual", "disabled_auto", "blocked_by_policy"],
        required=False,
    )
    kind = serializers.ChoiceField(choices=["http", "github_dispatch"], required=False)


class ToggleRequest(StrictInputSerializer):
    reason = serializers.CharField(required=False, allow_blank=True, max_length=500)


class ResumeRequest(StrictInputSerializer):
    mode = serializers.ChoiceField(choices=["send", "skip"])


class RotateRequest(StrictInputSerializer):
    overlap_seconds = serializers.IntegerField(
        min_value=0, max_value=MAX_OVERLAP_S, required=False
    )
    secret = serializers.CharField(
        required=False, max_length=200, trim_whitespace=False
    )


class TestRequest(StrictInputSerializer):
    event_type = serializers.CharField(max_length=100)


class PreviewRequest(StrictInputSerializer):
    event_type = serializers.CharField(max_length=100)
    payload_mode = serializers.ChoiceField(choices=["thin", "full"], required=False)
    include_people_names = serializers.BooleanField(required=False)
    headers = serializers.DictField(
        child=serializers.CharField(allow_blank=True, trim_whitespace=False),
        required=False,
    )


class DeliveryListQuery(StrictInputSerializer):
    endpoint = serializers.CharField(max_length=40, required=False)
    status = serializers.ChoiceField(choices=DELIVERY_STATUSES, required=False)
    event_type = serializers.ChoiceField(choices=EVENT_TYPES, required=False)
    event = serializers.CharField(max_length=40, required=False)
    subject = serializers.CharField(max_length=200, required=False)
    since = serializers.DateTimeField(required=False)
    until = serializers.DateTimeField(required=False)
    seq_from = serializers.IntegerField(min_value=1, required=False)
    seq_to = serializers.IntegerField(min_value=1, required=False)
    include_body = serializers.BooleanField(required=False)
    cursor = serializers.CharField(max_length=40, required=False)
    limit = serializers.IntegerField(min_value=1, max_value=100, required=False)


class BulkRedeliverRequest(StrictInputSerializer):
    """The filtered view of one endpoint's log: the same filters as ``DeliveryListQuery`` (WH-3 F5)."""

    endpoint = serializers.CharField(max_length=40)
    status = serializers.ChoiceField(choices=["failed", "succeeded", "skipped"])
    event_type = serializers.ChoiceField(choices=EVENT_TYPES, required=False)
    subject = serializers.CharField(max_length=200, required=False)
    since = serializers.DateTimeField(required=False)
    until = serializers.DateTimeField(required=False)
    seq_from = serializers.IntegerField(min_value=1, required=False)
    seq_to = serializers.IntegerField(min_value=1, required=False)
    dry_run = serializers.BooleanField(required=False, default=False)


class ActivityQuery(StrictInputSerializer):
    cursor = serializers.UUIDField(required=False)
    limit = serializers.IntegerField(min_value=1, max_value=100, required=False)


class EventListQuery(StrictInputSerializer):
    type = serializers.ChoiceField(choices=EVENT_TYPES, required=False)
    subject = serializers.CharField(max_length=200, required=False)
    since = serializers.DateTimeField(required=False)
    until = serializers.DateTimeField(required=False)
    cursor = serializers.CharField(max_length=40, required=False)
    limit = serializers.IntegerField(min_value=1, max_value=100, required=False)


class PolicyRequest(StrictInputSerializer):
    allowed_hosts = serializers.ListField(
        child=serializers.CharField(max_length=260), required=False
    )
    members_can_manage = serializers.BooleanField(required=False)
    full_payload_allowed = serializers.BooleanField(required=False)
    max_endpoints_per_workspace = serializers.IntegerField(
        min_value=1, max_value=200, required=False
    )
    log_retention_days = serializers.IntegerField(
        min_value=7, max_value=365, required=False
    )


class WorkspacePolicyRequest(StrictInputSerializer):
    allowed_hosts = serializers.ListField(
        child=serializers.CharField(max_length=260), required=False
    )
    members_can_manage = serializers.BooleanField(required=False)
