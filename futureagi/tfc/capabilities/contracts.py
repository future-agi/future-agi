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
