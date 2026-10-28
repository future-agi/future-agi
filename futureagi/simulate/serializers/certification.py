from __future__ import annotations

import hashlib

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
        required_components = {
            "futureagi-application-backend",
            "futureagi-application-temporal-worker-simulation-runner",
        }
        namespace = value.get("namespace")
        helm = value.get("helm_release")
        snapshot = value.get("alk_snapshot")
        components = value.get("components")
        digest = value.get("release_digest")
        if not isinstance(namespace, str) or not namespace:
            raise serializers.ValidationError("namespace is required")
        if not isinstance(helm, dict) or not isinstance(helm.get("name"), str):
            raise serializers.ValidationError("Helm release name is required")
        if not isinstance(helm.get("revision"), int) or helm["revision"] < 1:
            raise serializers.ValidationError("Helm release revision must be positive")
        if not isinstance(snapshot, dict) or not all(
            isinstance(snapshot.get(key), str) and snapshot[key]
            for key in ("name", "digest")
        ):
            raise serializers.ValidationError(
                "ALK snapshot name and digest are required"
            )
        if not isinstance(components, list):
            raise serializers.ValidationError("components must be a list")
        by_name = {
            component.get("name"): component
            for component in components
            if isinstance(component, dict)
        }
        if set(by_name) != required_components:
            raise serializers.ValidationError(
                "components must contain exactly the backend and simulation runner"
            )
        if any(
            not isinstance(by_name[name].get("images"), dict)
            or not by_name[name]["images"]
            or any(
                not isinstance(image_id, str) or "@sha256:" not in image_id
                for image_id in by_name[name]["images"].values()
            )
            for name in required_components
        ):
            raise serializers.ValidationError(
                "component images must be nonempty maps of immutable sha256 references"
            )
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise serializers.ValidationError("release_digest must be lowercase sha256")
        stable_identity = {
            "namespace": namespace,
            "helm_release": helm,
            "components": components,
        }
        actual = hashlib.sha256(
            json.dumps(stable_identity, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if actual != digest:
            raise serializers.ValidationError(
                "release_digest does not match the immutable release identity"
            )
        return value

    def validate(self, attrs):
        identity = attrs["deployment_identity"]
        expected = (
            f"gke-release:{identity['release_digest'][:16]}:"
            f"{identity['alk_snapshot']['digest']}"
        )
        if attrs["deployment_id"] != expected:
            raise serializers.ValidationError(
                {
                    "deployment_id": "does not match the release and ALK snapshot identity"
                }
            )
        return attrs


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
