from rest_framework import serializers

from tfc.capabilities.edition import EditionResource
from tfc.capabilities.registry import OSS_LOCKED_FEATURES
from tfc.licensing.types import (
    DenialReason,
    DeploymentFlavor,
    DisplayMode,
    LicenseState,
    LicenseType,
)
from tfc.utils.api_serializers import ManagementAPIErrorResponseSerializer


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
    license = LicenseDetailsSerializer(required=False, allow_null=True)
    instance_id = serializers.UUIDField(required=False, allow_null=True)


class EditionLimitSerializer(serializers.Serializer):
    limit = serializers.IntegerField(allow_null=True)
    current = serializers.IntegerField(min_value=0)


class EditionLimitsSerializer(serializers.Serializer):
    organizations = EditionLimitSerializer()
    workspaces = EditionLimitSerializer()
    members = EditionLimitSerializer()


class EditionLicenseSerializer(serializers.Serializer):
    """Licence status for admins. Never the raw key: the licence id is masked
    and the key appears only as a short SHA-256 fingerprint."""

    state = serializers.ChoiceField(choices=[state.value for state in LicenseState])
    license_type = serializers.ChoiceField(
        choices=[license_type.value for license_type in LicenseType],
        allow_null=True,
    )
    issued_to = serializers.CharField(allow_blank=True, allow_null=True)
    expires_at = serializers.DateTimeField(allow_null=True)
    grace_ends_at = serializers.DateTimeField(allow_null=True)
    license_id_masked = serializers.CharField(allow_null=True)
    key_fingerprint = serializers.CharField(allow_null=True)


class EditionActivationSerializer(serializers.Serializer):
    method = serializers.ChoiceField(choices=["env_restart"])


class EditionResponseSerializer(serializers.Serializer):
    """GET /api/edition/ (Settings > Plan & License). Cloud answers only
    ``edition: cloud``."""

    edition = serializers.ChoiceField(choices=["community", "enterprise", "cloud"])
    deployment = serializers.ChoiceField(
        choices=["self_hosted", "cloud"], required=False
    )
    limits = EditionLimitsSerializer(required=False)
    over_limit = serializers.BooleanField(required=False)
    enterprise_features = serializers.ListField(
        child=serializers.CharField(), required=False
    )
    contact = serializers.EmailField(required=False)
    activation = EditionActivationSerializer(required=False)
    license = EditionLicenseSerializer(required=False)


class EditionEnvelopeSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = EditionResponseSerializer()


# ---------------------------------------------------------------------------
# HTTP 402 ENTERPRISE_FEATURE_REQUIRED (accounts.authentication
# custom_exception_handler). Swagger 2.0 has no oneOf, so ``error`` uses the
# same ``x-string-or-object`` extension as the other string-or-object fields.
# ---------------------------------------------------------------------------

ENTERPRISE_GATE_FEATURES = [resource.value for resource in EditionResource] + sorted(
    OSS_LOCKED_FEATURES
)


class _StrictEnterpriseGateSerializer(serializers.Serializer):
    """Reject unknown keys in the structured 402 branch."""

    def to_internal_value(self, data):
        if hasattr(data, "keys"):
            unknown = sorted(set(data.keys()) - set(self.fields.keys()))
            if unknown:
                raise serializers.ValidationError(
                    {key: ["Unknown field."] for key in unknown}
                )
        return super().to_internal_value(data)


class EnterpriseGateErrorDetailSerializer(_StrictEnterpriseGateSerializer):
    feature = serializers.ChoiceField(choices=ENTERPRISE_GATE_FEATURES)


class EnterpriseGateErrorObjectSerializer(_StrictEnterpriseGateSerializer):
    code = serializers.CharField()
    message = serializers.CharField()
    detail = EnterpriseGateErrorDetailSerializer()


ENTERPRISE_GATE_ERROR_SCHEMA = {
    "type": "object",
    "description": "String error message, or the structured capability denial.",
    "x-string-or-object": True,
    "required": ["code", "message", "detail"],
    "additionalProperties": False,
    "properties": {
        "code": {"type": "string"},
        "message": {"type": "string"},
        "detail": {
            "type": "object",
            "required": ["feature"],
            "additionalProperties": False,
            "properties": {
                "feature": {"type": "string", "enum": ENTERPRISE_GATE_FEATURES}
            },
        },
    },
}


class EnterpriseGateSerializer(serializers.Serializer):
    """The ``enterprise_gate`` block: what needs Enterprise and where to go."""

    feature = serializers.ChoiceField(choices=ENTERPRISE_GATE_FEATURES)
    edition = serializers.ChoiceField(choices=["community", "enterprise"])
    limit = serializers.IntegerField(allow_null=True)
    current = serializers.IntegerField(allow_null=True)
    requested = serializers.IntegerField(allow_null=True)
    license_state = serializers.ChoiceField(
        choices=[state.value for state in LicenseState]
    )
    contact = serializers.EmailField()
    activation_route = serializers.CharField()


class EnterpriseGateErrorField(serializers.JSONField):
    """The capability denial object; other management errors send a string."""

    def to_internal_value(self, data):
        if isinstance(data, str):
            return data
        if isinstance(data, dict):
            serializer = EnterpriseGateErrorObjectSerializer(data=data)
            serializer.is_valid(raise_exception=True)
            return serializer.validated_data
        raise serializers.ValidationError(
            "Expected a string or a structured error object."
        )

    class Meta:
        swagger_schema_fields = ENTERPRISE_GATE_ERROR_SCHEMA


class EnterpriseGateErrorResponseSerializer(ManagementAPIErrorResponseSerializer):
    """HTTP 402 when creating one more organization, workspace or member needs
    an Enterprise licence. Adds the typed gate to the management error."""

    error = EnterpriseGateErrorField(required=False, allow_null=True)
    upgrade_required = serializers.BooleanField(required=False)
    enterprise_gate = EnterpriseGateSerializer(required=False)
