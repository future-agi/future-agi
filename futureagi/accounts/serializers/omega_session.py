from rest_framework import serializers

from accounts.omega_session import AUDIENCE, LIFETIME_SECONDS, MAX_BATCH
from tfc.utils.api_serializers import StrictInputSerializer


class OmegaSessionExchangeRequestSerializer(StrictInputSerializer):
    audience = serializers.ChoiceField(choices=[AUDIENCE])
    # Batched one-use keys (CP P7, D2): up to MAX_BATCH in one call, each single use.
    count = serializers.IntegerField(min_value=1, max_value=MAX_BATCH, required=False)

    class Meta:
        swagger_schema_fields = {"additionalProperties": False}


class OmegaSessionExchangeResponseSerializer(serializers.Serializer):
    accessToken = serializers.CharField(max_length=8192)
    tokenType = serializers.ChoiceField(choices=["Bearer"])
    expiresIn = serializers.IntegerField(min_value=1, max_value=LIFETIME_SECONDS)
    # Present only when `count` was sent: every key, `accessToken` being the first.
    accessTokens = serializers.ListField(
        child=serializers.CharField(max_length=8192),
        min_length=1,
        max_length=MAX_BATCH,
        required=False,
    )
