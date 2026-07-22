from rest_framework import serializers

from tfc.utils.api_serializers import ApiErrorResponseSerializer, EmptyRequestSerializer

ACCOUNTS_ERROR_RESULT_SCHEMA = {
    "type": "object",
    "description": ("String error message or structured account/login error metadata."),
    "x-string-or-object": True,
    "properties": {
        "error": {"type": "string"},
        "error_code": {"type": "string"},
        "message": {"type": "string"},
        "blocked": {"type": "boolean"},
        "remaining_attempts": {"type": "integer"},
        "block_time": {"type": "integer"},
        "block_time_remaining": {"type": "integer"},
    },
}


class AccountsErrorResultField(serializers.JSONField):
    """Account error result supports legacy strings and coded login objects."""

    class Meta:
        swagger_schema_fields = ACCOUNTS_ERROR_RESULT_SCHEMA


class AccountsErrorResponseSerializer(ApiErrorResponseSerializer):
    """Accounts error envelope; kept named for generated API docs."""

    result = AccountsErrorResultField(required=False, allow_null=True)


class AccountsEmptyRequestSerializer(EmptyRequestSerializer):
    """No-body accounts action request; rejects any submitted body fields."""


class AccountsJSONRequestSerializer(serializers.Serializer):
    class Meta:
        swagger_schema_fields = {
            "type": "object",
            "additionalProperties": True,
        }


class SignupRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()
    full_name = serializers.CharField()
    company_name = serializers.CharField(required=False, allow_blank=True)
    password = serializers.CharField(required=False, allow_blank=True, write_only=True)
    allow_email = serializers.BooleanField(required=False, default=False)
    recaptcha_response = serializers.CharField(required=False, allow_blank=True)


class LogoutRequestSerializer(serializers.Serializer):
    refresh = serializers.CharField(required=False, allow_blank=True)


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
    org_name = serializers.CharField(required=False)
    is_first_login = serializers.BooleanField(required=False)
    recovery_codes_warning = serializers.CharField(required=False)


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


class AccountsMessageResultSerializer(serializers.Serializer):
    message = serializers.CharField()


class AccountsMessageResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = AccountsMessageResultSerializer()


class AccountsStringResultResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = serializers.CharField()


class AccountsDirectMessageResponseSerializer(serializers.Serializer):
    message = serializers.CharField()


class AccountsUserProfileResponseSerializer(serializers.Serializer):
    name = serializers.CharField(allow_blank=True, allow_null=True)
    email = serializers.EmailField()
    org_name = serializers.CharField(allow_blank=True, allow_null=True)


class AccountsBulkUserMutationItemSerializer(serializers.Serializer):
    user_id = serializers.UUIDField()
    message = serializers.CharField(required=False)
    error = serializers.CharField(required=False)


class AccountsRedisSetResultSerializer(serializers.Serializer):
    message = serializers.CharField()
    key = serializers.CharField()
    value = serializers.JSONField()


class AccountsRedisSetResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = AccountsRedisSetResultSerializer()


class AccountsRedisDeleteResultSerializer(serializers.Serializer):
    message = serializers.CharField()
    key = serializers.CharField()


class AccountsRedisDeleteResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = AccountsRedisDeleteResultSerializer()


class PublicRegionConfigSerializer(serializers.Serializer):
    code = serializers.CharField()
    label = serializers.CharField()
    app_url = serializers.URLField()


class PublicConfigResultSerializer(serializers.Serializer):
    cloud = serializers.BooleanField()
    region = serializers.CharField(allow_null=True)
    available_regions = PublicRegionConfigSerializer(many=True)


class PublicConfigResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = PublicConfigResultSerializer()


class UserChecksResultSerializer(serializers.Serializer):
    keys = serializers.BooleanField()
    dataset = serializers.BooleanField()
    evaluation = serializers.BooleanField()
    experiment = serializers.BooleanField()
    observe = serializers.BooleanField()
    invite = serializers.BooleanField()


class UserChecksResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = UserChecksResultSerializer()


class UserOnboardingResultSerializer(serializers.Serializer):
    role = serializers.CharField(allow_blank=True)
    goals = serializers.ListField(child=serializers.CharField())
    completed = serializers.BooleanField()


class UserOnboardingResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = UserOnboardingResultSerializer()


class UserOnboardingDataSerializer(serializers.Serializer):
    role = serializers.CharField()
    goals = serializers.ListField(child=serializers.CharField())


class UserOnboardingSaveResultSerializer(serializers.Serializer):
    message = serializers.CharField()
    data = UserOnboardingDataSerializer()


class UserOnboardingSaveResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = UserOnboardingSaveResultSerializer()


class OrganizationSummarySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    display_name = serializers.CharField(allow_blank=True)
    ws_enabled = serializers.BooleanField(required=False)


class WorkspaceSummarySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    display_name = serializers.CharField(allow_blank=True)
    description = serializers.CharField(required=False, allow_blank=True)
    is_default = serializers.BooleanField(required=False)


class OrganizationSelectionItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    display_name = serializers.CharField(allow_blank=True)
    role = serializers.CharField(allow_blank=True, allow_null=True)
    level = serializers.IntegerField(allow_null=True)
    is_selected = serializers.BooleanField()


class OrganizationSelectionListResultSerializer(serializers.Serializer):
    organizations = OrganizationSelectionItemSerializer(many=True)
    total_count = serializers.IntegerField()


class OrganizationSelectionListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = OrganizationSelectionListResultSerializer()


class OrganizationSelectResultSerializer(serializers.Serializer):
    message = serializers.CharField()
    organization = OrganizationSummarySerializer()


class OrganizationSelectResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = OrganizationSelectResultSerializer()


class OrganizationSwitchResultSerializer(serializers.Serializer):
    organization = OrganizationSummarySerializer()
    org_role = serializers.CharField(allow_blank=True, allow_null=True)
    org_level = serializers.IntegerField(allow_null=True)
    workspace_role = serializers.CharField(allow_blank=True, allow_null=True)
    workspace = WorkspaceSummarySerializer(required=False)


class OrganizationSwitchResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = OrganizationSwitchResultSerializer()


class CurrentOrganizationResultSerializer(serializers.Serializer):
    organization = OrganizationSummarySerializer(allow_null=True)
    role = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    level = serializers.IntegerField(required=False, allow_null=True)
    source = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    message = serializers.CharField(required=False)


class CurrentOrganizationResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = CurrentOrganizationResultSerializer()


class OrganizationCreateResultSerializer(serializers.Serializer):
    organization_id = serializers.UUIDField()
    organization_name = serializers.CharField(allow_blank=True)
    workspace_id = serializers.UUIDField()
    message = serializers.CharField()


class OrganizationCreateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = OrganizationCreateResultSerializer()


class OrganizationUpdateResultSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    display_name = serializers.CharField(allow_blank=True)


class OrganizationUpdateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = OrganizationUpdateResultSerializer()


class AdditionalOrganizationCreateResultSerializer(serializers.Serializer):
    organization = OrganizationSummarySerializer()
    workspace = WorkspaceSummarySerializer()
    message = serializers.CharField()


class AdditionalOrganizationCreateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = AdditionalOrganizationCreateResultSerializer()


class AWSMarketplaceSignupResultSerializer(serializers.Serializer):
    message = serializers.CharField()
    user_email = serializers.EmailField()


class AWSMarketplaceSignupResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = AWSMarketplaceSignupResultSerializer()


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


class WebAuthnRelyingPartySerializer(serializers.Serializer):
    id = serializers.CharField(required=False)
    name = serializers.CharField(required=False)


class WebAuthnUserSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField()
    displayName = serializers.CharField(required=False)


class WebAuthnCredentialDescriptorSerializer(serializers.Serializer):
    type = serializers.CharField()
    id = serializers.CharField()
    transports = serializers.ListField(child=serializers.CharField(), required=False)


class WebAuthnPublicKeyCredentialParamSerializer(serializers.Serializer):
    type = serializers.CharField()
    alg = serializers.IntegerField()


class WebAuthnAuthenticatorSelectionSerializer(serializers.Serializer):
    authenticatorAttachment = serializers.CharField(required=False)
    residentKey = serializers.CharField(required=False)
    requireResidentKey = serializers.BooleanField(required=False)
    userVerification = serializers.CharField(required=False)


class WebAuthnExtensionsSerializer(serializers.Serializer):
    appid = serializers.CharField(required=False)
    credProps = serializers.BooleanField(required=False)
    uvm = serializers.BooleanField(required=False)


class PasskeyOptionsResponseSerializer(serializers.Serializer):
    challenge = serializers.CharField()
    timeout = serializers.IntegerField(required=False)
    rp = WebAuthnRelyingPartySerializer(required=False)
    user = WebAuthnUserSerializer(required=False)
    pubKeyCredParams = WebAuthnPublicKeyCredentialParamSerializer(
        many=True, required=False
    )
    excludeCredentials = WebAuthnCredentialDescriptorSerializer(
        many=True, required=False
    )
    allowCredentials = WebAuthnCredentialDescriptorSerializer(many=True, required=False)
    authenticatorSelection = WebAuthnAuthenticatorSelectionSerializer(required=False)
    attestation = serializers.CharField(required=False)
    rpId = serializers.CharField(required=False)
    userVerification = serializers.CharField(required=False)
    extensions = WebAuthnExtensionsSerializer(required=False)
    session_id = serializers.UUIDField(required=False)


class PasskeyCredentialRequestSerializer(serializers.Serializer):
    credential = serializers.JSONField()
    session_id = serializers.CharField(required=False, allow_blank=True)
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


class TOTPSetupResponseSerializer(serializers.Serializer):
    qr_code = serializers.CharField()
    secret = serializers.CharField()
    provisioning_uri = serializers.CharField()


class TOTPConfirmResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    recovery_codes = serializers.ListField(child=serializers.CharField())


class TOTPDisableResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()


class RecoveryCodesRemainingResponseSerializer(serializers.Serializer):
    remaining = serializers.IntegerField()


class RecoveryCodesRegenerateResponseSerializer(serializers.Serializer):
    recovery_codes = serializers.ListField(child=serializers.CharField())


class OrgTwoFactorPolicyResponseSerializer(serializers.Serializer):
    require_2fa = serializers.BooleanField()
    require_2fa_grace_period_days = serializers.IntegerField()
    require_2fa_enforced_at = serializers.DateTimeField(allow_null=True)


class PasskeyRegisterVerifyResponseSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    created_at = serializers.DateTimeField()
    recovery_codes = serializers.ListField(
        child=serializers.CharField(), required=False
    )


class PasskeyRenameResponseSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()


class SecretKeyDataResponseSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    api_key = serializers.CharField()
    secret_key = serializers.CharField()


class SecretKeysResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    data = SecretKeyDataResponseSerializer()


class SecretKeyListMetadataSerializer(serializers.Serializer):
    total_rows = serializers.IntegerField()
    total_pages = serializers.IntegerField()
    page_number = serializers.IntegerField()
    page_size = serializers.IntegerField()


class SecretKeyListItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    key_name = serializers.CharField(allow_blank=True, allow_null=True)
    api_key = serializers.CharField()
    secret_key = serializers.CharField()
    created_by = serializers.CharField(allow_null=True)
    created_at = serializers.DateTimeField()
    enabled = serializers.BooleanField()
    type = serializers.CharField()


class SecretKeyListResultSerializer(serializers.Serializer):
    metadata = SecretKeyListMetadataSerializer()
    table = SecretKeyListItemSerializer(many=True)


class SecretKeyListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = SecretKeyListResultSerializer()


class SecretKeyCreateResultSerializer(serializers.Serializer):
    key_id = serializers.UUIDField()
    key_name = serializers.CharField()
    api_key = serializers.CharField()
    masked_api_key = serializers.CharField()
    secret_key = serializers.CharField()
    masked_secret_key = serializers.CharField()


class SecretKeyCreateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = SecretKeyCreateResultSerializer()


class AcceptInvitationPreviewResponseSerializer(serializers.Serializer):
    valid = serializers.BooleanField()
    email = serializers.EmailField()
    org_name = serializers.CharField()


class UserInfoOrganizationSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    display_name = serializers.CharField(allow_blank=True)
    ws_enabled = serializers.BooleanField(required=False)


class UserInfoTwoFactorMethodsSerializer(serializers.Serializer):
    totp = serializers.BooleanField()
    passkey = serializers.BooleanField()


class UserInfoResponseSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    email = serializers.EmailField()
    name = serializers.CharField(allow_blank=True, allow_null=True)
    organization_role = serializers.CharField(allow_blank=True, allow_null=True)
    organization = UserInfoOrganizationSerializer(allow_null=True)
    created_at = serializers.DateTimeField()
    status = serializers.CharField()
    role = serializers.CharField(allow_blank=True, allow_null=True)
    goals = serializers.ListField(child=serializers.CharField(), required=False)
    remember_me = serializers.BooleanField()
    get_started_completed = serializers.BooleanField()
    onboarding_completed = serializers.BooleanField()
    ws_enabled = serializers.BooleanField()
    requires_org_setup = serializers.BooleanField(required=False)
    default_workspace_id = serializers.UUIDField(allow_null=True)
    default_workspace_name = serializers.CharField(allow_null=True, allow_blank=True)
    default_workspace_display_name = serializers.CharField(
        allow_null=True, allow_blank=True
    )
    default_workspace_role = serializers.CharField(allow_null=True, allow_blank=True)
    org_level = serializers.IntegerField(allow_null=True)
    ws_level = serializers.IntegerField(allow_null=True)
    effective_level = serializers.IntegerField(allow_null=True)
    has_2fa_enabled = serializers.BooleanField(required=False)
    two_factor_methods = UserInfoTwoFactorMethodsSerializer(required=False)
    org_2fa_required = serializers.BooleanField(required=False)
    org_2fa_grace_ends_at = serializers.DateTimeField(required=False)


class AccountOrganizationDetailSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    created_at = serializers.DateTimeField(required=False)
    name = serializers.CharField()
    display_name = serializers.CharField(allow_blank=True)
    is_new = serializers.BooleanField(required=False)
    ws_enabled = serializers.BooleanField(required=False)
    region = serializers.CharField(required=False)
    require_2fa = serializers.BooleanField(required=False)
    require_2fa_grace_period_days = serializers.IntegerField(required=False)
    require_2fa_enforced_at = serializers.DateTimeField(required=False, allow_null=True)


class AccountUserItemResponseSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    email = serializers.EmailField()
    name = serializers.CharField(allow_blank=True, allow_null=True)
    organization_role = serializers.CharField(allow_blank=True, allow_null=True)
    organization = AccountOrganizationDetailSerializer(allow_null=True)
    created_at = serializers.DateTimeField()
    status = serializers.CharField()
    role = serializers.CharField(allow_blank=True, allow_null=True)
    goals = serializers.ListField(child=serializers.CharField(), required=False)


class AccountsPaginatedUserResponseSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.CharField(allow_null=True)
    previous = serializers.CharField(allow_null=True)
    results = AccountUserItemResponseSerializer(many=True)
    total_pages = serializers.IntegerField()
    current_page = serializers.IntegerField()
    total_queries = serializers.IntegerField(required=False)


class AppsmithUserCreateResponseSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField()
    organization_name = serializers.CharField()
    send_credential = serializers.BooleanField()


class AppsmithPasswordUpdateResponseSerializer(serializers.Serializer):
    password = serializers.CharField()


class WorkspaceAdminSummarySerializer(serializers.Serializer):
    name = serializers.CharField(allow_blank=True, allow_null=True)
    id = serializers.UUIDField()


class WorkspaceListItemResponseSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    display_name = serializers.CharField(allow_blank=True)
    admin_names = WorkspaceAdminSummarySerializer(many=True, required=False)
    start_data = serializers.CharField(required=False, allow_blank=True)
    last_update_date = serializers.CharField(required=False, allow_blank=True)
    invite_link = serializers.CharField(required=False, allow_blank=True)
    user_ws_level = serializers.IntegerField(required=False, allow_null=True)
    user_ws_role = serializers.CharField(required=False, allow_null=True)


class WorkspaceListPaginatedResponseSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.CharField(allow_null=True)
    previous = serializers.CharField(allow_null=True)
    results = WorkspaceListItemResponseSerializer(many=True)
    total_pages = serializers.IntegerField()
    current_page = serializers.IntegerField()


class WorkspaceInviteResultItemSerializer(serializers.Serializer):
    email = serializers.EmailField()
    status = serializers.CharField()
    workspaces = serializers.ListField(child=serializers.UUIDField())
    select_all = serializers.BooleanField()
    total_workspaces = serializers.IntegerField()


class WorkspaceInviteErrorItemSerializer(serializers.Serializer):
    email = serializers.EmailField(required=False)
    error = serializers.CharField()


class WorkspaceInviteResultSerializer(serializers.Serializer):
    results = WorkspaceInviteResultItemSerializer(many=True)
    total_invited = serializers.IntegerField()
    select_all = serializers.BooleanField()
    total_workspaces = serializers.IntegerField()
    errors = WorkspaceInviteErrorItemSerializer(many=True, required=False)


class WorkspaceInviteResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = WorkspaceInviteResultSerializer()


class UserListItemResponseSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField(allow_blank=True, allow_null=True)
    email = serializers.EmailField()
    role = serializers.CharField(allow_blank=True, allow_null=True)
    status = serializers.CharField()
    start_date = serializers.CharField(allow_blank=True)
    last_updated_date = serializers.CharField(allow_blank=True)
    workspace_role = serializers.CharField(required=False, allow_null=True)
    workspace_member_since = serializers.CharField(required=False, allow_blank=True)
    invited_by = serializers.CharField(required=False, allow_null=True)


class UserListPaginatedResponseSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.CharField(allow_null=True)
    previous = serializers.CharField(allow_null=True)
    results = UserListItemResponseSerializer(many=True)
    total_pages = serializers.IntegerField()
    current_page = serializers.IntegerField()


class UserRoleUpdateResultSerializer(serializers.Serializer):
    message = serializers.CharField()
    user_id = serializers.UUIDField()
    new_role = serializers.CharField()
    workspace_role = serializers.CharField(required=False, allow_null=True)
    workspace = serializers.CharField(required=False, allow_null=True)
    level = serializers.CharField()


class UserRoleUpdateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = UserRoleUpdateResultSerializer()


class ResendInviteResultSerializer(serializers.Serializer):
    message = serializers.CharField()
    user_id = serializers.UUIDField()
    workspace = serializers.CharField(required=False)


class ResendInviteResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ResendInviteResultSerializer()


class DeleteUserResultSerializer(serializers.Serializer):
    message = serializers.CharField()
    user_id = serializers.UUIDField()
    workspace = serializers.CharField(required=False)
    level = serializers.CharField()


class DeleteUserResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = DeleteUserResultSerializer()


class DeactivateUserResultSerializer(serializers.Serializer):
    message = serializers.CharField()
    user_id = serializers.UUIDField()
    user_email = serializers.EmailField()
    user_name = serializers.CharField(allow_blank=True, allow_null=True)


class DeactivateUserResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = DeactivateUserResultSerializer()


class SwitchWorkspaceResultSerializer(serializers.Serializer):
    message = serializers.CharField()
    workspace = WorkspaceSummarySerializer()
    user_role = serializers.CharField()
    access_type = serializers.CharField()
    organization = serializers.CharField()


class SwitchWorkspaceResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = SwitchWorkspaceResultSerializer()


class TeamWorkspaceSummarySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    role = serializers.CharField()


class TeamUserItemResponseSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    email = serializers.EmailField()
    name = serializers.CharField(allow_blank=True, allow_null=True)
    organization_role = serializers.CharField(allow_blank=True, allow_null=True)
    organization = AccountOrganizationDetailSerializer(required=False, allow_null=True)
    created_at = serializers.CharField()
    status = serializers.CharField()
    role = serializers.CharField(allow_blank=True, allow_null=True)
    goals = serializers.ListField(child=serializers.CharField(), required=False)
    membership_type = serializers.CharField(required=False)
    workspace_role = serializers.CharField(required=False, allow_null=True)
    workspace_member = serializers.BooleanField(required=False)
    workspaces = TeamWorkspaceSummarySerializer(many=True, required=False)


class TeamUsersResultSerializer(serializers.Serializer):
    org_name = serializers.CharField()
    workspace_name = serializers.CharField(required=False)
    results = TeamUserItemResponseSerializer(many=True)
    total = serializers.IntegerField()


class TeamUsersResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = TeamUsersResultSerializer()


class TeamCreateErrorItemSerializer(serializers.Serializer):
    index = serializers.IntegerField(required=False)
    email = serializers.EmailField(required=False)
    error = serializers.CharField()


class TeamCreateResultSerializer(serializers.Serializer):
    created_members = AccountUserItemResponseSerializer(many=True)
    workspace = WorkspaceSummarySerializer()
    errors = TeamCreateErrorItemSerializer(many=True, required=False)


class TeamCreateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = TeamCreateResultSerializer()


class TeamRemoveResultSerializer(serializers.Serializer):
    message = serializers.CharField()
    removed_from = serializers.CharField()


class TeamRemoveResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = TeamRemoveResultSerializer()


class WorkspaceManagementItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    display_name = serializers.CharField(allow_blank=True)
    description = serializers.CharField(allow_blank=True)
    is_default = serializers.BooleanField()
    member_count = serializers.IntegerField()
    created_at = serializers.CharField()
    created_by = serializers.CharField(allow_blank=True, allow_null=True)


class WorkspaceManagementListResultSerializer(serializers.Serializer):
    organization = serializers.CharField()
    workspaces = WorkspaceManagementItemSerializer(many=True)
    total = serializers.IntegerField()


class WorkspaceManagementListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = WorkspaceManagementListResultSerializer()


class WorkspaceCreateUserErrorSerializer(serializers.Serializer):
    email = serializers.EmailField()
    error = serializers.CharField()


class WorkspaceCreateResultSerializer(serializers.Serializer):
    workspace = WorkspaceSummarySerializer()
    message = serializers.CharField()
    added_users = serializers.ListField(child=serializers.EmailField())
    created_users = serializers.ListField(child=serializers.EmailField())
    total_users_added = serializers.IntegerField()
    total_users_created = serializers.IntegerField()
    failed_users = WorkspaceCreateUserErrorSerializer(many=True)
    other_org_users = WorkspaceCreateUserErrorSerializer(many=True)


class WorkspaceCreateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = WorkspaceCreateResultSerializer()


class WorkspaceUpdateResultSerializer(serializers.Serializer):
    workspace = WorkspaceSummarySerializer()
    message = serializers.CharField()


class WorkspaceUpdateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = WorkspaceUpdateResultSerializer()


class WorkspaceDeleteResultSerializer(serializers.Serializer):
    message = serializers.CharField()


class WorkspaceDeleteResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = WorkspaceDeleteResultSerializer()


class WorkspaceMemberItemSerializer(serializers.Serializer):
    user_id = serializers.UUIDField()
    email = serializers.EmailField()
    name = serializers.CharField(allow_blank=True, allow_null=True)
    role = serializers.CharField()
    joined_at = serializers.CharField()
    invited_by = serializers.CharField(allow_null=True)


class WorkspaceMembersListResultSerializer(serializers.Serializer):
    workspace = WorkspaceSummarySerializer()
    members = WorkspaceMemberItemSerializer(many=True)
    total = serializers.IntegerField()


class WorkspaceMembersListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = WorkspaceMembersListResultSerializer()


class WorkspaceMemberAddedItemSerializer(serializers.Serializer):
    email = serializers.EmailField()
    name = serializers.CharField(allow_blank=True, allow_null=True)
    role = serializers.CharField()
    action = serializers.CharField()


class WorkspaceMemberAddErrorSerializer(serializers.Serializer):
    email = serializers.EmailField(required=False, allow_blank=True)
    error = serializers.CharField()


class WorkspaceMembersAddResultSerializer(serializers.Serializer):
    workspace = WorkspaceSummarySerializer()
    added_users = WorkspaceMemberAddedItemSerializer(many=True)
    errors = WorkspaceMemberAddErrorSerializer(many=True, required=False)


class WorkspaceMembersAddResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = WorkspaceMembersAddResultSerializer()


class WorkspaceMemberRemoveResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = AccountsMessageResultSerializer()


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
