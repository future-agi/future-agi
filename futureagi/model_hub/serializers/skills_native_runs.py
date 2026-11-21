"""J2 application verification contracts (native matched runs)."""

from rest_framework import serializers as s

from model_hub.serializers.skills_hub_r3 import NativeConfiguration
from model_hub.serializers.skills_hub_v11 import Integer
from model_hub.serializers.skills_hub_v12 import ContextSerializer, Digest


class RuntimeStatus(ContextSerializer):
    available = s.BooleanField()
    runtime = s.CharField(allow_null=True)
    evidence_class = s.CharField(allow_null=True)


class ApplicationTarget(ContextSerializer):
    binding_id = s.UUIDField()
    name = s.CharField()
    revision = Integer(min_value=1)
    selector = s.JSONField()
    qualified = s.BooleanField()
    configuration_digest = Digest(allow_null=True)
    configuration = s.JSONField(allow_null=True)


class ApplicationTargets(ContextSerializer):
    runtime = RuntimeStatus()
    targets = ApplicationTarget(many=True)


class RuntimeConfiguration(ContextSerializer):
    configuration = NativeConfiguration()


class CreateTargetInput(ContextSerializer):
    version = Integer(min_value=1)


class QualifyTargetInput(ContextSerializer):
    revision = Integer(min_value=1)
    configuration = NativeConfiguration()


class NativePlanInput(ContextSerializer):
    binding_id = s.UUIDField()
    binding_revision = Integer(min_value=1)
    candidate_version = Integer(min_value=1)
    baselines = s.DictField(child=Integer(min_value=1))
    case_ids = s.ListField(child=s.UUIDField(), min_length=1, max_length=20)
    repetitions = s.ChoiceField(choices=[1, 3])
    historical_case_ids = s.ListField(
        child=s.UUIDField(), max_length=20, required=False, default=list
    )

    def validate_baselines(self, value):
        if not 1 <= len(value) <= 2 or set(value) - {"reference", "production"}:
            raise s.ValidationError("Choose one or two baseline arms.")
        return value


class NativeRunInput(NativePlanInput):
    execution_fingerprint = Digest()
    acknowledge_spend = s.BooleanField()


class MissingInput(ContextSerializer):
    case_id = s.UUIDField()
    input = s.CharField()
    detail = s.CharField()


class NativeArm(ContextSerializer):
    variant = s.CharField()
    version = Integer(min_value=1)
    version_id = s.UUIDField()
    digest = Digest()
    catalog_members = Integer(min_value=0, required=False)


class NativePlanCase(ContextSerializer):
    id = s.UUIDField()
    title = s.CharField()
    historical = s.BooleanField()
    expected_skill = s.CharField(allow_null=True)
    expected_action = s.CharField(allow_null=True)


class NativePlan(ContextSerializer):
    execution_fingerprint = Digest()
    runtime = RuntimeStatus()
    target = ApplicationTarget()
    arms = NativeArm(many=True)
    cases = NativePlanCase(many=True)
    cells = Integer(min_value=1)
    ceiling_usd = s.CharField()
    reproducible = s.BooleanField()
    reproduction = s.ChoiceField(choices=["ready", "not_reproduced"])
    missing_inputs = MissingInput(many=True)


class NativeRunCreated(ContextSerializer):
    evidence_id = s.UUIDField()


class NativeRunSummary(ContextSerializer):
    id = s.UUIDField()
    job_id = s.UUIDField()
    status = s.CharField()
    failure = s.CharField(allow_null=True)
    created_at = s.DateTimeField()
    visibility = s.CharField()
    candidate_digest = Digest()
    execution_fingerprint = Digest()
    configuration_digest = Digest()
    annotation_revision = Integer(min_value=0)
    application_binding_id = s.UUIDField(allow_null=True)
    arms = NativeArm(many=True)
    planned = Integer(min_value=0)
    received = Integer(min_value=0)


class NativeRunList(ContextSerializer):
    results = NativeRunSummary(many=True)


class NativeRunDetail(NativeRunSummary):
    result = s.JSONField(required=False)
    decision = s.JSONField(required=False)
    runtime = s.JSONField(required=False, allow_null=True)
    cases = s.JSONField(required=False)
    cells = s.JSONField(required=False)


class ShareNativeInput(ContextSerializer):
    acknowledge_snapshot_sharing = s.BooleanField()


# --- Authenticated guest channel request bodies ---
# Shape is validated here (refused with the service's own codes); the services keep
# the exact checks (health must equal the planned execution spec; observations must
# carry a matching digest) and still receive the raw body. They stay plain
# serializers (unknown guest keys are not refused here) but publish under the
# Skills schema namespace like every other Skills definition.


class GuestHealthInput(s.Serializer):
    class Meta:
        ref_name = "SkillsContextGuestHealthInput"

    protocol = s.CharField()
    adapter_module = s.CharField()
    configuration_digest = s.CharField()
    execution_fingerprint = s.CharField()
    code_digest = s.CharField()
    image_digest = s.CharField()
    packages = s.JSONField()
    checkpoint_digest = s.CharField()
    read_only_source = s.BooleanField()
    read_only_context = s.BooleanField()
    separate_writable_state = s.BooleanField()
    controlled_paid_egress = s.BooleanField()


class GuestObservationInput(s.Serializer):
    class Meta:
        ref_name = "SkillsContextGuestObservationInput"

    schema = s.IntegerField()
    protocol = s.CharField()
    execution_fingerprint = s.CharField()
    case_id = s.CharField()
    variant = s.CharField()
    repeat_index = s.IntegerField(min_value=0)
    application = s.DictField()
    business = s.DictField()
    observation_digest = s.CharField()


class GuestFailureInput(s.Serializer):
    class Meta:
        ref_name = "SkillsContextGuestFailureInput"

    reason = s.CharField(required=False, allow_blank=True, allow_null=True)


# --- Authenticated guest channel responses ---
# Health and failure reports are acknowledged; an observation also reports how
# many of the planned cells have arrived.


class GuestAccepted(ContextSerializer):
    accepted = s.BooleanField()


class GuestObservationAccepted(GuestAccepted):
    received = Integer(min_value=0)
    planned = Integer(min_value=0)
