from __future__ import annotations

from rest_framework import serializers


class CertificationCampaignCreateSerializer(serializers.Serializer):
    """Admission request for a production certification campaign.

    Idempotency comes from the Idempotency-Key header AND the
    (organization, deployment_id, suite_digest) unique constraint — a retry
    returns the existing campaign instead of duplicating it.
    """

    deployment_id = serializers.CharField(max_length=128)
    deployment_identity = serializers.DictField(required=False, default=dict)
    suite_key = serializers.CharField(
        max_length=64, required=False, default="production_agents_v1"
    )

    def validate_deployment_identity(self, value):
        # Reject anything that looks like raw secret material — identity is
        # descriptive metadata (region, image digest, version), never tokens.
        import json
        import re

        text = json.dumps(value)
        if re.search(
            r"(?i)(api[_-]?key|secret|password|token|bearer\s+[a-z0-9._-]{10,})",
            text,
        ):
            raise serializers.ValidationError(
                "deployment_identity must not contain credentials or tokens"
            )
        return value


class CertificationCaseReadSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    case_key = serializers.CharField()
    sequence = serializers.IntegerField()
    state = serializers.CharField()
    failure_class = serializers.CharField(allow_null=True)
    hosted_harness_job_id = serializers.UUIDField(allow_null=True)
    reconciliation = serializers.DictField(allow_null=True)
    started_at = serializers.DateTimeField(allow_null=True)
    finished_at = serializers.DateTimeField(allow_null=True)


class CertificationCampaignReadSerializer(serializers.Serializer):
    """Secret-safe campaign projection — never exposes secret_refs or
    raw manifest material beyond what a reviewer needs."""

    id = serializers.UUIDField()
    state = serializers.CharField()
    suite_key = serializers.CharField()
    suite_digest = serializers.CharField()
    deployment_id = serializers.CharField()
    deployment_identity = serializers.DictField()
    cases_total = serializers.IntegerField()
    cases_completed = serializers.IntegerField()
    cases_failed = serializers.IntegerField()
    result = serializers.DictField(allow_null=True)
    created_at = serializers.DateTimeField()
    started_at = serializers.DateTimeField(allow_null=True)
    finished_at = serializers.DateTimeField(allow_null=True)
    cancel_requested_at = serializers.DateTimeField(allow_null=True)
    cases = CertificationCaseReadSerializer(many=True)
