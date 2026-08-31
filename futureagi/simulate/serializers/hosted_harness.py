from __future__ import annotations

import math

from rest_framework import serializers

_SHA256 = r"^sha256:[0-9a-f]{64}$"


class HarnessIngressRequestSerializer(serializers.Serializer):
    port = serializers.IntegerField(min_value=1, max_value=65535)
    expires_in_seconds = serializers.IntegerField(
        min_value=60, max_value=86400, required=False, default=7200
    )


class HarnessIngressResponseSerializer(serializers.Serializer):
    url = serializers.URLField()
    expires_in_seconds = serializers.IntegerField(min_value=60, max_value=86400)


def _reject_non_finite(value: float) -> None:
    # FloatField min/max never reject NaN/inf: every comparison with NaN is
    # False, so a `score: NaN` slips past bounds and canonicalizes into
    # non-RFC-8259 bytes. Fail it explicitly.
    if not math.isfinite(value):
        raise serializers.ValidationError("must be a finite number")


class HarnessEventSerializer(serializers.Serializer):
    event_id = serializers.RegexField(r"^[A-Za-z0-9_-]{1,64}$")
    job_id = serializers.UUIDField()
    attempt_id = serializers.UUIDField()
    attempt_number = serializers.IntegerField(min_value=1)
    sequence = serializers.IntegerField(min_value=1)
    emitted_at = serializers.DateTimeField()
    stage = serializers.CharField(max_length=64)
    type = serializers.CharField(max_length=64)
    payload = serializers.JSONField()
    digest = serializers.RegexField(_SHA256)


class HarnessEventBatchSerializer(serializers.Serializer):
    schema_version = serializers.ChoiceField(choices=("futureagi.harness-event.v1",))
    events = HarnessEventSerializer(many=True, allow_empty=False, max_length=100)


class HarnessSubGoalSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    held = serializers.BooleanField(allow_null=True)
    reason = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    judged = serializers.BooleanField()


class HarnessMetricEvaluationSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    kind = serializers.ChoiceField(choices=("metric",))
    score = serializers.FloatField(
        min_value=0.0, max_value=1.0, validators=[_reject_non_finite]
    )
    reason = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    grading_error = serializers.BooleanField(required=False)


class HarnessCheckpointEvaluationSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    kind = serializers.ChoiceField(choices=("checkpoint",))
    passed = serializers.BooleanField()
    reason = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    grading_error = serializers.BooleanField(required=False)


class HarnessCallSerializer(serializers.Serializer):
    started_at = serializers.DateTimeField()
    ended_at = serializers.DateTimeField()
    duration_ms = serializers.IntegerField(min_value=0)
    turns = serializers.IntegerField(min_value=0)
    transcript_artifact = serializers.RegexField(
        _SHA256, required=False, allow_null=True
    )
    recording_artifacts = serializers.ListField(
        child=serializers.RegexField(_SHA256), default=list
    )


class HarnessFailureSerializer(serializers.Serializer):
    domain = serializers.ChoiceField(
        choices=(
            "agent",
            "simulator",
            "environment",
            "connectivity",
            "infrastructure",
            "grading",
            "platform_sync",
        )
    )
    stage = serializers.CharField(max_length=64)
    code = serializers.CharField(max_length=128)
    message = serializers.CharField(max_length=2000)


class HarnessResultReceiptSerializer(serializers.Serializer):
    schema_version = serializers.ChoiceField(choices=("futureagi.harness-result.v1",))
    job_id = serializers.UUIDField()
    attempt_id = serializers.UUIDField()
    attempt_number = serializers.IntegerField(min_value=1)
    scenario_key = serializers.CharField(max_length=255)
    scenario_id = serializers.UUIDField()
    scenario_attempt = serializers.IntegerField(min_value=1, max_value=2)
    world_index = serializers.IntegerField(min_value=0, max_value=7, allow_null=True)
    status = serializers.ChoiceField(choices=("passed", "failed", "errored", "skipped"))
    sub_goals = HarnessSubGoalSerializer(many=True)
    evaluations = serializers.JSONField()
    call = HarnessCallSerializer(allow_null=True)
    failure = HarnessFailureSerializer(allow_null=True)
    digest = serializers.RegexField(_SHA256)

    def validate_evaluations(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("evaluations must be a list")
        validated = []
        for item in value:
            serializer_class = (
                HarnessMetricEvaluationSerializer
                if isinstance(item, dict) and item.get("kind") == "metric"
                else HarnessCheckpointEvaluationSerializer
            )
            serializer = serializer_class(data=item)
            serializer.is_valid(raise_exception=True)
            validated.append(serializer.validated_data)
        return validated

    def validate(self, attrs):
        status = attrs["status"]
        if status == "skipped":
            expected = {
                "scenario_attempt": 1,
                "world_index": None,
                "sub_goals": [],
                "evaluations": [],
                "call": None,
                "failure": None,
            }
            mismatched = [key for key, value in expected.items() if attrs[key] != value]
            if mismatched:
                raise serializers.ValidationError(
                    f"invalid skipped receipt fields: {', '.join(mismatched)}"
                )
        elif status == "errored" and attrs["failure"] is None:
            raise serializers.ValidationError("errored receipt requires failure")
        elif status in {"passed", "failed"} and attrs["failure"] is not None:
            raise serializers.ValidationError(
                "passed/failed receipt must not include failure"
            )
        if status != "skipped" and attrs["world_index"] is None:
            raise serializers.ValidationError(
                "world_index is required when scenario ran"
            )
        return attrs


class HarnessManifestEntrySerializer(serializers.Serializer):
    artifact_id = serializers.RegexField(_SHA256)
    kind = serializers.CharField(max_length=32)
    size = serializers.IntegerField(min_value=0)
    scenario_key = serializers.CharField(
        max_length=255, required=False, allow_null=True
    )


class HarnessManifestSerializer(serializers.Serializer):
    schema_version = serializers.ChoiceField(choices=("futureagi.harness-manifest.v1",))
    job_id = serializers.UUIDField()
    attempt_id = serializers.UUIDField()
    attempt_number = serializers.IntegerField(min_value=1)
    entries = HarnessManifestEntrySerializer(many=True)
    complete = serializers.BooleanField()
    digest = serializers.RegexField(_SHA256)


class HarnessProvisionPersonaSerializer(serializers.Serializer):
    scenario_key = serializers.CharField(max_length=255)
    name = serializers.CharField(required=False, allow_blank=True, max_length=255)
    role = serializers.CharField(required=False, allow_blank=True, max_length=255)
    situation = serializers.CharField(required=False, allow_blank=True)
    outcome = serializers.CharField(required=False, allow_blank=True)
    persona = serializers.JSONField(required=False)


class HarnessScenarioProvisionSerializer(serializers.Serializer):
    operation = serializers.ChoiceField(choices=("provision",))
    name = serializers.CharField(max_length=255)
    modality = serializers.ChoiceField(choices=("text", "voice"), default="text")
    description = serializers.CharField(required=False, allow_blank=True)
    personas = HarnessProvisionPersonaSerializer(many=True, allow_empty=False)
    agent_definition_id = serializers.UUIDField(required=False, allow_null=True)
    agent_name = serializers.CharField(required=False, allow_blank=True, max_length=255)

    def validate_personas(self, personas):
        keys = [persona["scenario_key"] for persona in personas]
        if len(keys) != len(set(keys)):
            raise serializers.ValidationError("scenario_key values must be unique")
        return personas


class HarnessScenarioBeginSerializer(serializers.Serializer):
    operation = serializers.ChoiceField(choices=("begin",))
    run_test_id = serializers.UUIDField()
    scenario_keys = serializers.ListField(
        child=serializers.CharField(max_length=255), allow_empty=False
    )


class HarnessScenarioOperationSerializer(serializers.Serializer):
    operation = serializers.ChoiceField(choices=("provision", "begin"))
    name = serializers.CharField(required=False, max_length=255)
    modality = serializers.ChoiceField(choices=("text", "voice"), required=False)
    description = serializers.CharField(required=False, allow_blank=True)
    personas = HarnessProvisionPersonaSerializer(many=True, required=False)
    agent_definition_id = serializers.UUIDField(required=False, allow_null=True)
    agent_name = serializers.CharField(required=False, allow_blank=True, max_length=255)
    run_test_id = serializers.UUIDField(required=False)
    scenario_keys = serializers.ListField(
        child=serializers.CharField(max_length=255), required=False
    )

    def to_internal_value(self, data):
        serializer_class = (
            HarnessScenarioProvisionSerializer
            if isinstance(data, dict) and data.get("operation") == "provision"
            else HarnessScenarioBeginSerializer
        )
        serializer = serializer_class(data=data)
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data


class HarnessEventRejectionSerializer(serializers.Serializer):
    event_id = serializers.CharField()
    sequence = serializers.IntegerField()
    code = serializers.CharField()
    message = serializers.CharField()


class HarnessEventBatchResponseSerializer(serializers.Serializer):
    acked_through_sequence = serializers.IntegerField()
    rejected = HarnessEventRejectionSerializer(many=True)


class HarnessAcceptedResponseSerializer(serializers.Serializer):
    accepted = serializers.BooleanField()
    duplicate = serializers.BooleanField()


class HarnessArtifactUploadResponseSerializer(serializers.Serializer):
    artifact_id = serializers.CharField()
    duplicate = serializers.BooleanField()


class HarnessScenarioRegistrationResponseSerializer(serializers.Serializer):
    scenario_key = serializers.CharField()
    scenario_id = serializers.UUIDField()
    call_execution_id = serializers.UUIDField(required=False)


class HarnessScenarioOperationResultSerializer(serializers.Serializer):
    run_test_id = serializers.UUIDField(required=False)
    test_execution_id = serializers.UUIDField(required=False)
    scenarios = HarnessScenarioRegistrationResponseSerializer(many=True)


class HarnessScenarioOperationResponseSerializer(serializers.Serializer):
    result = HarnessScenarioOperationResultSerializer()
