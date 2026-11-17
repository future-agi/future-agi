"""Response contracts of the webhooks API: ``result`` of FA's success envelope ``{status: true, result: …}``,
documented in the OpenAPI contract the frontend and the SDK generate from, and checked by ``validated_request`` in
DEBUG."""

from __future__ import annotations

from rest_framework import serializers

# ---- responses (``result`` of FA's success envelope) -------------------------------------------------------------


def envelope(name: str, result: serializers.Field) -> type[serializers.Serializer]:
    return type(
        name,
        (serializers.Serializer,),
        {
            "status": serializers.BooleanField(default=True),
            "result": result,
            "__module__": __name__,
        },
    )


class SubscriptionOut(serializers.Serializer):
    event_type = serializers.CharField()
    filters = serializers.DictField()


class EndpointWarningOut(serializers.Serializer):
    code = serializers.ChoiceField(choices=["webhook_url_unencrypted"])
    message = serializers.CharField()


class EndpointOut(serializers.Serializer):
    warnings = EndpointWarningOut(many=True, required=False)
    id = serializers.CharField()
    kind = serializers.CharField()
    name = serializers.CharField()
    description = serializers.CharField(allow_blank=True)
    workspace_id = serializers.CharField(allow_null=True)
    org_wide = serializers.BooleanField()
    url_host = serializers.CharField(allow_blank=True)
    url_scheme = serializers.ChoiceField(choices=["http", "https"], allow_null=True)
    url = serializers.CharField(
        allow_null=True, help_text="Query values masked; admins only"
    )
    header_names = serializers.ListField(child=serializers.CharField(), allow_null=True)
    subscriptions = SubscriptionOut(many=True)
    status = serializers.CharField()
    disabled_reason = serializers.CharField(allow_null=True)
    disabled_at = serializers.DateTimeField(allow_null=True)
    payload_mode = serializers.CharField()
    include_people_names = serializers.BooleanField()
    accepts_run_targeting = serializers.BooleanField()
    api_version = serializers.CharField()
    timeout_ms = serializers.IntegerField()
    rate_limit_per_s = serializers.IntegerField()
    max_in_flight = serializers.IntegerField()
    max_age_s = serializers.IntegerField()
    owner_id = serializers.CharField(allow_null=True)
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()
    can_manage = serializers.BooleanField()
    health = serializers.DictField(allow_null=True)
    secrets = serializers.ListField(
        child=serializers.DictField(), allow_null=True, required=False
    )
    backlog = serializers.DictField(required=False)


class CreateWarningOut(serializers.Serializer):
    code = serializers.ChoiceField(
        choices=["webhook_url_unencrypted", "WEBHOOK_URL_QUERY_SECRET"]
    )
    message = serializers.CharField()
    params = serializers.ListField(child=serializers.CharField(), required=False)


class EndpointCreatedOut(serializers.Serializer):
    endpoint = EndpointOut()
    secret = serializers.CharField(help_text="whsec_…, shown once")
    warnings = CreateWarningOut(many=True)


class EndpointListOut(serializers.Serializer):
    results = EndpointOut(many=True)


class ResumeOut(serializers.Serializer):
    mode = serializers.CharField()
    sent = serializers.IntegerField()
    skipped = serializers.IntegerField()
    past_max_age = serializers.IntegerField()
    backlog_capped = serializers.IntegerField()


class RotateOut(serializers.Serializer):
    secret = serializers.CharField()
    id = serializers.CharField()
    tail = serializers.CharField()
    previous = serializers.DictField(allow_null=True)
    revoked_tails = serializers.ListField(child=serializers.CharField())


class RevokeOut(serializers.Serializer):
    revoked_tails = serializers.ListField(child=serializers.CharField())


class TestOut(serializers.Serializer):
    event_id = serializers.CharField()
    delivery_id = serializers.CharField(allow_null=True)
    status_code = serializers.IntegerField(allow_null=True, required=False)
    outcome = serializers.CharField(required=False)
    latency_ms = serializers.IntegerField(allow_null=True, required=False)
    response_excerpt = serializers.CharField(allow_null=True, required=False)


class PreviewOut(serializers.Serializer):
    method = serializers.CharField()
    headers = serializers.ListField(
        child=serializers.ListField(child=serializers.CharField())
    )
    body = serializers.CharField()
    body_bytes = serializers.IntegerField()


class DeliveryOut(serializers.Serializer):
    id = serializers.CharField()
    event_id = serializers.CharField()
    event_type = serializers.CharField()
    subject = serializers.CharField(allow_null=True)
    endpoint_id = serializers.CharField()
    seq = serializers.IntegerField(allow_null=True)
    status = serializers.CharField()
    attempts_count = serializers.IntegerField()
    is_test = serializers.BooleanField()
    last_attempt = serializers.DictField(allow_null=True)
    body = serializers.CharField(allow_null=True, required=False)


class DeliveryPageOut(serializers.Serializer):
    results = DeliveryOut(many=True)
    next_cursor = serializers.CharField(allow_null=True)


class DeliveryDetailOut(DeliveryOut):
    request_headers = serializers.DictField()
    attempts = serializers.ListField(child=serializers.DictField())


class RedeliverOut(serializers.Serializer):
    delivery_id = serializers.CharField()
    queued = serializers.BooleanField()
    attempt = serializers.IntegerField()


class BulkRedeliverOut(serializers.Serializer):
    count = serializers.IntegerField()
    queued = serializers.IntegerField()
    expired = serializers.IntegerField()
    dry_run = serializers.BooleanField()


class ActivityActorOut(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField()


class ActivityOut(serializers.Serializer):
    id = serializers.CharField()
    action = serializers.CharField()
    at = serializers.DateTimeField()
    actor = ActivityActorOut(allow_null=True)
    system = serializers.BooleanField(
        help_text="No person did it (the dispatcher, a health rule, offboarding)."
    )
    changed = serializers.ListField(
        child=serializers.CharField(), help_text="The names of the fields that changed."
    )
    changes = serializers.DictField(
        allow_null=True,
        help_text="Before and after per field; only for those who see the endpoint's URL, else null.",
    )
    metadata = serializers.DictField()


class ActivityPageOut(serializers.Serializer):
    results = ActivityOut(many=True)
    next_cursor = serializers.CharField(allow_null=True)


class EventOut(serializers.Serializer):
    id = serializers.CharField()
    type = serializers.CharField()
    api_version = serializers.CharField()
    timestamp = serializers.DateTimeField()
    workspace_id = serializers.CharField(allow_null=True)
    subject = serializers.CharField(allow_null=True)
    data = serializers.DictField()
    delivery_count = serializers.IntegerField()


class EventPageOut(serializers.Serializer):
    results = EventOut(many=True)
    next_cursor = serializers.CharField(allow_null=True)


class EventTypeOut(serializers.Serializer):
    type = serializers.CharField()
    group = serializers.CharField()
    description = serializers.CharField()
    filters = serializers.ListField(child=serializers.CharField())
    schema = serializers.DictField()


class EventTypesOut(serializers.Serializer):
    results = EventTypeOut(many=True)
    groups = serializers.ListField(child=serializers.CharField())
    shared_schemas = serializers.DictField(
        help_text="The schemas the per-type schemas refer to by $id (the envelope, deployment.run)"
    )


class EgressOut(serializers.Serializer):
    hosted = serializers.BooleanField()
    regions = serializers.ListField(child=serializers.DictField())


class UrlPolicyOut(serializers.Serializer):
    http_allowed = serializers.BooleanField()
    private_addresses_blocked = serializers.BooleanField()


class PolicyOut(serializers.Serializer):
    url_policy = UrlPolicyOut()
    scope = serializers.CharField()
    workspace_id = serializers.CharField(allow_null=True)
    allowed_hosts = serializers.ListField(child=serializers.CharField())
    members_can_manage = serializers.BooleanField()
    effective = serializers.DictField()
    blocked_endpoint_ids = serializers.ListField(
        child=serializers.CharField(), required=False
    )
    downgraded_endpoint_ids = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        help_text="Full-mode endpoints moved to thin because full_payload_allowed is off.",
    )


EndpointResponse = envelope("WebhookEndpointResponse", EndpointOut())
EndpointCreatedResponse = envelope(
    "WebhookEndpointCreatedResponse", EndpointCreatedOut()
)
EndpointListResponse = envelope("WebhookEndpointListResponse", EndpointListOut())
ResumeResponse = envelope("WebhookResumeResponse", ResumeOut())
RotateResponse = envelope("WebhookRotateSecretResponse", RotateOut())
RevokeResponse = envelope("WebhookRevokePreviousResponse", RevokeOut())
TestResponse = envelope("WebhookTestResponse", TestOut())
PreviewResponse = envelope("WebhookPreviewResponse", PreviewOut())
DeliveryPageResponse = envelope("WebhookDeliveryPageResponse", DeliveryPageOut())
DeliveryDetailResponse = envelope("WebhookDeliveryDetailResponse", DeliveryDetailOut())
RedeliverResponse = envelope("WebhookRedeliverResponse", RedeliverOut())
BulkRedeliverResponse = envelope("WebhookBulkRedeliverResponse", BulkRedeliverOut())
EventPageResponse = envelope("WebhookEventPageResponse", EventPageOut())
ActivityPageResponse = envelope("WebhookActivityPageResponse", ActivityPageOut())
EventTypesResponse = envelope("WebhookEventTypesResponse", EventTypesOut())
EgressResponse = envelope("WebhookEgressResponse", EgressOut())
PolicyResponse = envelope("WebhookPolicyResponse", PolicyOut())
DeletedResponse = envelope(
    "WebhookEndpointDeletedResponse",
    serializers.DictField(help_text="{deleted: true, cancelled_deliveries}"),
)
