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
    # Per-turn agent latency (chat runs) — without this field DRF drops it and
    # avg_latency_ms stays null forever.
    latency_ms = serializers.IntegerField(required=False, allow_null=True, min_value=0)
    # Structured tool calls carried on a ``tool_calls`` segment so the agent's
    # real tool activity survives ingestion (list of {id, name, arguments}).
    tool_calls = serializers.JSONField(required=False)
    tool_call_id = serializers.CharField(
        required=False, allow_blank=True, max_length=255
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


class ALKSimulateStatusUpdateSerializer(serializers.Serializer):
    """Lightweight non-terminal status ping — e.g. a case going ONGOING the
    moment its call starts. Deliberately carries no transcript/metrics; the
    terminal result still arrives separately via ``result``."""

    status = serializers.ChoiceField(choices=(CallExecutionStatus.ONGOING.value,))


class ALKSimulateStatusUpdateOutcomeSerializer(serializers.Serializer):
    updated = serializers.BooleanField()


class ALKSimulateStatusUpdateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = ALKSimulateStatusUpdateOutcomeSerializer()


class ALKSimulateBatchCreateResultSerializer(serializers.Serializer):
    call_execution_ids = serializers.ListField(child=serializers.UUIDField())
    has_more = serializers.BooleanField()
    batched_scenarios = serializers.ListField(child=serializers.UUIDField())


class ALKSimulateBatchCreateRequestSerializer(serializers.Serializer):
    count = serializers.IntegerField(required=False, min_value=1)


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


class ALKSimulateProvisionPersonaSerializer(serializers.Serializer):
    name = serializers.CharField(required=False, allow_blank=True, max_length=255)
    role = serializers.CharField(required=False, allow_blank=True, max_length=255)
    situation = serializers.CharField(required=False, allow_blank=True)
    outcome = serializers.CharField(required=False, allow_blank=True)
    # Full persona identity dict (name/role/personality/…); when supplied it is
    # stored verbatim in the scenario dataset's ``persona`` cell so the simulator
    # prompt's {{persona}} placeholder resolves against the real persona.
    persona = serializers.JSONField(required=False)


class ALKSimulateProvisionRunTestRequestSerializer(serializers.Serializer):
    """Provision a chat RunTest for an SDK-first run, two mutually exclusive ways:

    * ``scenario_ids`` — attach existing (natively generated) scenarios to a new
      RunTest. Nothing is fabricated or mutated; the scenarios render with their
      real datasets. Preferred.
    * ``personas`` — a hand-built fallback: one COMPLETED persona-dataset scenario
      per persona (see ``_build_persona_scenario_dataset``). Kept for the offline
      self-contained path; the resulting dataset lacks the generated
      ``column_config`` the UI reads, so prefer ``scenario_ids``.

    Exactly one of the two must be supplied.
    """

    name = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, allow_blank=True)
    personas = ALKSimulateProvisionPersonaSerializer(many=True, required=False)
    scenario_ids = serializers.ListField(
        child=serializers.UUIDField(), required=False, allow_empty=False
    )
    agent_definition_id = serializers.UUIDField(required=False, allow_null=True)
    agent_name = serializers.CharField(required=False, allow_blank=True, max_length=255)

    def validate(self, attrs):
        has_personas = bool(attrs.get("personas"))
        has_scenarios = bool(attrs.get("scenario_ids"))
        if has_personas == has_scenarios:
            raise serializers.ValidationError(
                "provide exactly one of 'scenario_ids' (reuse existing scenarios) "
                "or 'personas' (fabricate a scenario per persona)"
            )
        return attrs


class ALKSimulateProvisionResultSerializer(serializers.Serializer):
    run_test_id = serializers.UUIDField()
    scenario_ids = serializers.ListField(child=serializers.UUIDField())
    agent_definition_id = serializers.UUIDField()


class ALKSimulateProvisionResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = ALKSimulateProvisionResultSerializer()
