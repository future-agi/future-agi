from rest_framework import serializers

from tfc.licensing.types import (
    DenialReason,
    DeploymentFlavor,
    DisplayMode,
    LicenseState,
    LicenseType,
)


class CapabilityFeatureSerializer(serializers.Serializer):
    display_name = serializers.CharField()
    allowed = serializers.BooleanField()
    reason_code = serializers.ChoiceField(
        choices=[reason.value for reason in DenialReason],
        allow_null=True,
    )
    requires_network = serializers.BooleanField(allow_null=True)
    oss_baseline = serializers.BooleanField()


class LicenseDetailsSerializer(serializers.Serializer):
    issued_to = serializers.CharField(allow_blank=True, allow_null=True)
    band = serializers.CharField(allow_blank=True, allow_null=True)
    license_type = serializers.ChoiceField(
        choices=[license_type.value for license_type in LicenseType],
        allow_null=True,
    )
    expires_at = serializers.DateTimeField(allow_null=True)
    grace_ends_at = serializers.DateTimeField(allow_null=True)
    features_count = serializers.IntegerField(min_value=0)
    state = serializers.ChoiceField(
        choices=[state.value for state in LicenseState],
    )


class ExperimentRunSourceCapabilitySerializer(serializers.Serializer):
    available = serializers.BooleanField()
    reason = serializers.CharField(allow_null=True)

    def validate(self, attrs):
        if attrs["available"] != (attrs["reason"] is None):
            raise serializers.ValidationError(
                "Unavailable sources must include a reason; available sources must not."
            )
        return attrs


class ExperimentRunSourceKindsSerializer(serializers.Serializer):
    prompt = ExperimentRunSourceCapabilitySerializer()
    agent = ExperimentRunSourceCapabilitySerializer(
        help_text="Agent Playground graph versions, not Console-agent targets."
    )
    http = ExperimentRunSourceCapabilitySerializer()
    a2a = ExperimentRunSourceCapabilitySerializer()
    mcp_tool = ExperimentRunSourceCapabilitySerializer()


class ExperimentRunSourcesSerializer(serializers.Serializer):
    workspace_id = serializers.UUIDField(allow_null=True)
    sources = ExperimentRunSourceKindsSerializer()


class CapabilitiesResponseSerializer(serializers.Serializer):
    deployment_flavor = serializers.ChoiceField(
        choices=[flavor.value for flavor in DeploymentFlavor],
    )
    display_mode = serializers.ChoiceField(
        choices=[mode.value for mode in DisplayMode],
    )
    license_state = serializers.ChoiceField(
        choices=[state.value for state in LicenseState],
    )
    features = serializers.DictField(child=CapabilityFeatureSerializer())
    experiment_run_sources = ExperimentRunSourcesSerializer()
    license = LicenseDetailsSerializer(required=False, allow_null=True)
    instance_id = serializers.UUIDField(required=False, allow_null=True)
