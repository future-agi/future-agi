from rest_framework import serializers

from simulate.models.test_execution import CallTranscript
from simulate.semantics import CallExecutionStatus, SupportedProviders

ALLOWED_INGESTION_STATUSES = (
    CallExecutionStatus.COMPLETED.value,
    CallExecutionStatus.FAILED.value,
    CallExecutionStatus.CANCELLED.value,
)


class ALKSimulateTranscriptSegmentSerializer(serializers.Serializer):
    speaker_role = serializers.ChoiceField(choices=CallTranscript.SpeakerRole.values)
    content = serializers.CharField(allow_blank=True)
    start_time_ms = serializers.IntegerField(required=False, default=0, min_value=0)
    end_time_ms = serializers.IntegerField(required=False, default=0, min_value=0)
    confidence_score = serializers.FloatField(
        required=False, allow_null=True, min_value=0.0, max_value=1.0
    )


class ALKSimulateCostBreakdownSerializer(serializers.Serializer):
    stt_cost_cents = serializers.IntegerField(required=False, allow_null=True)
    llm_cost_cents = serializers.IntegerField(required=False, allow_null=True)
    tts_cost_cents = serializers.IntegerField(required=False, allow_null=True)
    storage_cost_cents = serializers.FloatField(required=False, allow_null=True)
    cost_cents = serializers.IntegerField(required=False, allow_null=True)


class ALKSimulateResultSerializer(serializers.Serializer):
    """Payload SDK sends after a call finishes.

    Backend owns metric derivation (conversation metrics, CSAT, evaluations)
    — SDK only reports what it directly observed: transcript, recording URL,
    provider call ids/costs, timing, terminal status.
    """

    status = serializers.ChoiceField(choices=ALLOWED_INGESTION_STATUSES)
    started_at = serializers.DateTimeField(required=False, allow_null=True)
    ended_at = serializers.DateTimeField(required=False, allow_null=True)
    duration_seconds = serializers.IntegerField(
        required=False, allow_null=True, min_value=0
    )
    ended_reason = serializers.CharField(
        required=False, allow_blank=True, max_length=10000
    )
    error_message = serializers.CharField(required=False, allow_blank=True)
    call_summary = serializers.CharField(required=False, allow_blank=True)

    transcript = ALKSimulateTranscriptSegmentSerializer(many=True, required=False)

    recording_url = serializers.URLField(
        required=False, allow_blank=True, max_length=500
    )
    stereo_recording_url = serializers.URLField(
        required=False, allow_blank=True, max_length=500
    )

    costs = ALKSimulateCostBreakdownSerializer(required=False)

    provider_call_data = serializers.JSONField(required=False)
    call_metadata = serializers.JSONField(required=False)

    def validate_provider_call_data(self, value):
        if value is None:
            return value
        if not isinstance(value, dict):
            raise serializers.ValidationError("provider_call_data must be a dict")
        if value and set(value.keys()).issubset(SupportedProviders):
            return value
        return value


class ALKSimulateResultOutcomeSerializer(serializers.Serializer):
    call_execution_id = serializers.UUIDField()
    status = serializers.CharField()
    eval_dispatched = serializers.BooleanField()


class ALKSimulateResultResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = ALKSimulateResultOutcomeSerializer()


class ALKSimulateBatchCreateResultSerializer(serializers.Serializer):
    call_execution_ids = serializers.ListField(child=serializers.UUIDField())
    has_more = serializers.BooleanField()
    batched_scenarios = serializers.ListField(child=serializers.UUIDField())


class ALKSimulateBatchCreateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = ALKSimulateBatchCreateResultSerializer()


class ALKSimulateStartTestExecutionRequestSerializer(serializers.Serializer):
    """Optional scenario subset when starting an ALK test execution.

    Empty body / omitted `scenario_ids` selects every non-deleted scenario
    attached to the run test.
    """

    scenario_ids = serializers.ListField(
        child=serializers.UUIDField(), required=False, allow_empty=True
    )
    simulator_agent_id = serializers.UUIDField(required=False, allow_null=True)


class ALKSimulateStartTestExecutionResultSerializer(serializers.Serializer):
    test_execution_id = serializers.UUIDField()
    run_test_id = serializers.UUIDField()
    scenario_ids = serializers.ListField(child=serializers.UUIDField())
    total_scenarios = serializers.IntegerField()
    status = serializers.CharField()


class ALKSimulateStartTestExecutionResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = ALKSimulateStartTestExecutionResultSerializer()


class ALKSimulateRecordingUploadRequestSerializer(serializers.Serializer):
    """Multipart upload for an ALK-produced recording.

    Sent as ``multipart/form-data`` with the audio bytes attached under
    ``file``; ``filename`` is used only to derive the storage key extension.
    """

    file = serializers.FileField()
    filename = serializers.CharField(required=False, allow_blank=True, max_length=200)


class ALKSimulateRecordingUploadResultSerializer(serializers.Serializer):
    recording_url = serializers.URLField(max_length=1024)
    object_key = serializers.CharField()


class ALKSimulateRecordingUploadResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = ALKSimulateRecordingUploadResultSerializer()
