from rest_framework import serializers


class AccountsErrorResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(required=False)
    result = serializers.JSONField(required=False, allow_null=True)
    message = serializers.JSONField(required=False, allow_null=True)
    error = serializers.JSONField(required=False, allow_null=True)
    detail = serializers.JSONField(required=False, allow_null=True)


class AccountsEmptyRequestSerializer(serializers.Serializer):
    pass


class AccountsJSONRequestSerializer(serializers.Serializer):
    class Meta:
        swagger_schema_fields = {
            "type": "object",
            "additionalProperties": True,
        }


class AccountsJSONResponseSerializer(serializers.Serializer):
    status = serializers.JSONField(required=False)
    result = serializers.JSONField(required=False, allow_null=True)
    data = serializers.JSONField(required=False, allow_null=True)
    detail = serializers.JSONField(required=False, allow_null=True)
    message = serializers.JSONField(required=False, allow_null=True)
    error = serializers.JSONField(required=False, allow_null=True)


class AccountsTokenPairResponseSerializer(serializers.Serializer):
    access = serializers.CharField(required=False)
    refresh = serializers.CharField(required=False)
    requires_two_factor = serializers.BooleanField(required=False)
    challenge_token = serializers.UUIDField(required=False)
    methods = serializers.ListField(child=serializers.CharField(), required=False)
    requires_org_setup = serializers.BooleanField(required=False)
    message = serializers.CharField(required=False)
    new_org = serializers.BooleanField(required=False)


class AccountsAccessTokenResponseSerializer(serializers.Serializer):
    access = serializers.CharField()


class LoginRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField()
    remember_me = serializers.BooleanField(required=False, default=False)
    recaptcha_response = serializers.CharField(required=False, allow_blank=True)


class TokenRefreshRequestSerializer(serializers.Serializer):
    refresh = serializers.CharField()
    recaptcha_response = serializers.CharField(required=False, allow_blank=True)
    localhost_bypass = serializers.BooleanField(required=False, default=False)


class RedisKeyRequestSerializer(serializers.Serializer):
    access_token_id = serializers.CharField()
    key = serializers.CharField()
    value = serializers.JSONField(required=False)
    expiry = serializers.IntegerField(required=False, min_value=1)


class TimezoneRequestSerializer(serializers.Serializer):
    timezone = serializers.CharField(max_length=64)


class TimezoneResponseSerializer(serializers.Serializer):
    timezone = serializers.CharField()


class OrganizationNameRequestSerializer(serializers.Serializer):
    organization_name = serializers.CharField(required=False, allow_blank=True)


class OrganizationCreateRequestSerializer(serializers.Serializer):
    name = serializers.CharField()
    display_name = serializers.CharField(required=False, allow_blank=True)


class OrganizationUpdateRequestSerializer(serializers.Serializer):
    name = serializers.CharField(required=False, allow_blank=True)
    display_name = serializers.CharField(required=False, allow_blank=True)


class OrganizationSwitchRequestSerializer(serializers.Serializer):
    organization_id = serializers.UUIDField()


class UserFullNameUpdateRequestSerializer(serializers.Serializer):
    full_name = serializers.CharField(required=False, allow_blank=True)
    name = serializers.CharField(required=False, allow_blank=True)


class PasswordResetInitiateRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()


class PasswordResetConfirmRequestSerializer(serializers.Serializer):
    new_password = serializers.CharField(write_only=True)
    repeat_password = serializers.CharField(write_only=True)


class AcceptInvitationRequestSerializer(serializers.Serializer):
    new_password = serializers.CharField(write_only=True)
    repeat_password = serializers.CharField(write_only=True)


class UserIdsRequestSerializer(serializers.Serializer):
    user_ids = serializers.ListField(child=serializers.UUIDField())


class WorkspaceCreateRequestSerializer(serializers.Serializer):
    name = serializers.CharField()
    display_name = serializers.CharField(required=False, allow_blank=True)
    description = serializers.CharField(required=False, allow_blank=True)
    emails = serializers.ListField(
        child=serializers.EmailField(), required=False, default=list
    )
    role = serializers.CharField(required=False, allow_blank=True)


class WorkspaceUpdateRequestSerializer(serializers.Serializer):
    name = serializers.CharField(required=False, allow_blank=True)
    display_name = serializers.CharField(required=False, allow_blank=True)
    description = serializers.CharField(required=False, allow_blank=True)


class WorkspaceMembersRequestSerializer(serializers.Serializer):
    users = serializers.ListField(child=serializers.DictField())


class AWSMarketplaceTokenFormSerializer(serializers.Serializer):
    x_amzn_marketplace_token = serializers.CharField()
    x_amzn_marketplace_product_id = serializers.CharField(required=False)
    x_amzn_marketplace_agreement_id = serializers.CharField(required=False)


class AWSMarketplaceSignupRequestSerializer(serializers.Serializer):
    onboarding_token = serializers.CharField()
    email = serializers.EmailField()
    full_name = serializers.CharField()


class AWSMarketplaceLaunchRequestSerializer(serializers.Serializer):
    x_amzn_marketplace_token = serializers.CharField()


class PasskeyOptionsResponseSerializer(serializers.Serializer):
    class Meta:
        swagger_schema_fields = {
            "type": "object",
            "additionalProperties": True,
        }


class PasskeyCredentialRequestSerializer(serializers.Serializer):
    credential = serializers.JSONField()
    name = serializers.CharField(max_length=255, required=False, allow_blank=True)


class TwoFactorPasskeyVerifyRequestSerializer(serializers.Serializer):
    challenge_token = serializers.UUIDField()
    credential = serializers.JSONField()
    session_id = serializers.CharField(required=False, allow_blank=True)


class PasskeyRenameRequestSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)


class PasskeyListResponseSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    created_at = serializers.DateTimeField()
    last_used_at = serializers.DateTimeField(allow_null=True)


class AccountsPaginatedResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(required=False)
    result = serializers.JSONField(required=False, allow_null=True)
    metadata = serializers.JSONField(required=False, allow_null=True)
    table = serializers.JSONField(required=False, allow_null=True)


ACCOUNTS_ERROR_RESPONSES = {
    400: AccountsErrorResponseSerializer,
    401: AccountsErrorResponseSerializer,
    403: AccountsErrorResponseSerializer,
    404: AccountsErrorResponseSerializer,
    500: AccountsErrorResponseSerializer,
}
