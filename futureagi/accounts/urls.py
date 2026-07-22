from django.urls import include, path
from rest_framework.routers import DefaultRouter

from accounts.views.appsmith import SOSLoginView, UserApiView
from accounts.views.aws_marketplace import (
    aws_marketplace_launch_software,
    aws_marketplace_signup,
    aws_marketplace_verify_token,
)
from accounts.views.config import public_config
from accounts.views.keys import GetKeysView, SecretKeyAPIViewSet
from accounts.views.organization_selection import (
    OrganizationSelectionView,
    SwitchOrganizationView,
    get_current_organization,
)
from accounts.views.organization_views import (
    CreateAdditionalOrganizationView,
    OrganizationCreateAPIView,
    OrganizationUpdateAPIView,
)
from accounts.views.rbac_views import (
    InviteCancelAPIView,
    InviteCreateAPIView,
    InviteResendAPIView,
    MemberListAPIView,
    MemberReactivateAPIView,
    MemberRemoveAPIView,
    MemberRoleUpdateAPIView,
    WorkspaceMemberListAPIView,
    WorkspaceMemberRemoveAPIView,
    WorkspaceMemberRoleUpdateAPIView,
)
from accounts.views.signup import (
    accept_invitation_mail,
    activate_account,
    delete_users,
    get_user_profile_details,
    initiate_password_reset,
    resend_invitation_emails,
    reset_password_confirm,
    update_user,
    update_user_full_name,
    user_logout,
    user_signup,
)
from accounts.views.two_factor_views import (
    OrgTwoFactorPolicyView,
    RecoveryCodesRegenerateView,
    RecoveryCodesView,
    TOTPConfirmView,
    TOTPDisableView,
    TOTPSetupView,
    TwoFactorStatusView,
    TwoFactorVerifyPasskeyOptionsView,
    TwoFactorVerifyPasskeyView,
    TwoFactorVerifyRecoveryView,
    TwoFactorVerifyTOTPView,
)
from accounts.views.user import (
    CustomTokenObtainPairView,
    CustomTokenRefreshView,
    FirstChecksView,
    get_user_info,
    manage_redis_key,
    user_onboarding,
)
from accounts.views.webauthn_views import (
    PasskeyAuthenticateOptionsView,
    PasskeyAuthenticateVerifyView,
    PasskeyDetailView,
    PasskeyListView,
    PasskeyRegisterOptionsView,
    PasskeyRegisterVerifyView,
)
from accounts.views.workspace import WorkspaceManagementView, WorkspaceMembershipView
from accounts.views.workspace_management import (
    DeactivateUserAPIView,
    DeleteUserAPIView,
    ManageTeamView,
    ResendInviteAPIView,
    SwitchWorkspaceAPIView,
    UserListAPIView,
    UserRoleUpdateAPIView,
    WorkspaceInviteAPIView,
    WorkspaceListAPIView,
)

router = DefaultRouter()
router.register("key", SecretKeyAPIViewSet, basename="user-secret-keys")

auth_urls = [
    path("token/", CustomTokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("token/refresh/", CustomTokenRefreshView.as_view(), name="token_refresh"),
    path("redis-key/", manage_redis_key, name="manage-redis-key"),
]

signup_urls = [
    path("signup/", user_signup, name="user-signup"),
    path("logout/", user_logout, name="user-logout"),
    path("activate/<uidb64>/<token>/", activate_account, name="activate-account"),
    path(
        "password-reset-initiate/",
        initiate_password_reset,
        name="initiate-password-reset",
    ),
    path(
        "password-reset-confirm/<uidb64>/<token>/",
        reset_password_confirm,
        name="reset-password-confirm",
    ),
    path(
        "resend-invitation-emails/",
        resend_invitation_emails,
        name="resend-invitation-emails",
    ),
    path(
        "accept-invitation/<uidb64>/<token>/",
        accept_invitation_mail,
        name="accept_invitation_email",
    ),
    path("delete-users/", delete_users, name="delete-users"),
    path("update-user/", update_user, name="update-user"),
    path("update-user-full-name/", update_user_full_name, name="update-user-full-name"),
    path(
        "get-user-profile-details/",
        get_user_profile_details,
        name="get-user-profile-details",
    ),
]

user_urls = [
    path("user-info/", get_user_info, name="user-info"),
    path("first-checks/", FirstChecksView.as_view(), name="first-checks"),
    path("onboarding/", user_onboarding, name="user-onboarding"),
    path(
        "me/timezone/",
        __import__(
            "accounts.views.annotation_notifications", fromlist=["UserTimezoneView"]
        ).UserTimezoneView.as_view(),
        name="user-timezone",
    ),
    path(
        "notifications/unsubscribe/",
        __import__(
            "accounts.views.annotation_notifications",
            fromlist=["UnsubscribeAnnotationDigestView"],
        ).UnsubscribeAnnotationDigestView.as_view(),
        name="annotation-digest-unsubscribe",
    ),
    path(
        "notifications/snooze/",
        __import__(
            "accounts.views.annotation_notifications",
            fromlist=["SnoozeAnnotationDigestView"],
        ).SnoozeAnnotationDigestView.as_view(),
        name="annotation-digest-snooze",
    ),
]

team_urls = [
    path("team/users/", ManageTeamView.as_view(), name="manage_team_users"),
    path("workspaces/", WorkspaceManagementView.as_view(), name="workspace_management"),
    path(
        "workspaces/<uuid:workspace_id>/",
        WorkspaceManagementView.as_view(),
        name="workspace_detail",
    ),
    path(
        "workspaces/<uuid:workspace_id>/members/",
        WorkspaceMembershipView.as_view(),
        name="workspace_members",
    ),
    path(
        "workspaces/<uuid:workspace_id>/members/<uuid:member_id>/",
        WorkspaceMembershipView.as_view(),
        name="workspace_member_detail",
    ),
    path(
        "team/users/<uuid:member_id>/",
        ManageTeamView.as_view(),
        name="manage_team_users",
    ),
    # New workspace management APIs
    path("workspace/list/", WorkspaceListAPIView.as_view(), name="workspace_list"),
    path(
        "workspace/invite/", WorkspaceInviteAPIView.as_view(), name="workspace_invite"
    ),
    path("user/list/", UserListAPIView.as_view(), name="user_list"),
    path("user/role/update/", UserRoleUpdateAPIView.as_view(), name="user_role_update"),
    path("user/resend-invite/", ResendInviteAPIView.as_view(), name="resend_invite"),
    path("user/delete/", DeleteUserAPIView.as_view(), name="delete_user"),
    path("user/deactivate/", DeactivateUserAPIView.as_view(), name="deactivate_user"),
    path(
        "workspace/switch/", SwitchWorkspaceAPIView.as_view(), name="switch_workspace"
    ),
]

# New RBAC endpoints (Phase 2 — run alongside old endpoints)
rbac_urls = [
    path(
        "organization/invite/",
        InviteCreateAPIView.as_view(),
        name="rbac_invite_create",
    ),
    path(
        "organization/invite/resend/",
        InviteResendAPIView.as_view(),
        name="rbac_invite_resend",
    ),
    path(
        "organization/invite/cancel/",
        InviteCancelAPIView.as_view(),
        name="rbac_invite_cancel",
    ),
    path(
        "organization/members/",
        MemberListAPIView.as_view(),
        name="rbac_member_list",
    ),
    path(
        "organization/members/role/",
        MemberRoleUpdateAPIView.as_view(),
        name="rbac_member_role_update",
    ),
    path(
        "organization/members/remove/",
        MemberRemoveAPIView.as_view(),
        name="rbac_member_remove",
    ),
    path(
        "organization/members/reactivate/",
        MemberReactivateAPIView.as_view(),
        name="rbac_member_reactivate",
    ),
]

# Workspace-scoped member endpoints
workspace_member_urls = [
    path(
        "workspace/<uuid:workspace_id>/members/",
        WorkspaceMemberListAPIView.as_view(),
        name="workspace_member_list",
    ),
    path(
        "workspace/<uuid:workspace_id>/members/role/",
        WorkspaceMemberRoleUpdateAPIView.as_view(),
        name="workspace_member_role_update",
    ),
    path(
        "workspace/<uuid:workspace_id>/members/remove/",
        WorkspaceMemberRemoveAPIView.as_view(),
        name="workspace_member_remove",
    ),
]

appsmith_urls = [
    path("appsmith/users/", UserApiView.as_view(), name="all_users"),
    path("appsmith/users/<uuid:user_id>/", UserApiView.as_view(), name="all_users"),
    path("appsmith/users/login", SOSLoginView.as_view(), name="sos_login"),
]

keys_urls = [
    path("keys/", GetKeysView.as_view(), name="user-api-keys"),
]
# Organization selection URLs
organization_urls = [
    path(
        "organizations/",
        OrganizationSelectionView.as_view(),
        name="organization_selection",
    ),
    path(
        "organizations/switch/",
        SwitchOrganizationView.as_view(),
        name="switch_organization",
    ),
    path(
        "organizations/current/", get_current_organization, name="current_organization"
    ),
    path(
        "organizations/create/",
        OrganizationCreateAPIView.as_view(),
        name="organization-create",
    ),
    path(
        "organizations/new/",
        CreateAdditionalOrganizationView.as_view(),
        name="organization-create-additional",
    ),
    path(
        "organizations/update/",
        OrganizationUpdateAPIView.as_view(),
        name="organization-update",
    ),
]

aws_marketplace_urls = [
    path(
        "aws-marketplace/verify-token/",
        aws_marketplace_verify_token,
        name="aws-marketplace-verify-token",
    ),
    path(
        "aws-marketplace/signup/", aws_marketplace_signup, name="aws-marketplace-signup"
    ),
    path(
        "aws-marketplace/launch-software/",
        aws_marketplace_launch_software,
        name="aws-marketplace-launch-software",
    ),
]

config_urls = [
    path("config/", public_config, name="public-config"),
]

# 2FA Management
two_factor_urls = [
    path("2fa/status/", TwoFactorStatusView.as_view(), name="2fa_status"),
    path("2fa/totp/setup/", TOTPSetupView.as_view(), name="2fa_totp_setup"),
    path("2fa/totp/confirm/", TOTPConfirmView.as_view(), name="2fa_totp_confirm"),
    path("2fa/totp/", TOTPDisableView.as_view(), name="2fa_totp_disable"),
    path("2fa/verify/totp/", TwoFactorVerifyTOTPView.as_view(), name="2fa_verify_totp"),
    path(
        "2fa/verify/recovery/",
        TwoFactorVerifyRecoveryView.as_view(),
        name="2fa_verify_recovery",
    ),
    path(
        "2fa/verify/passkey/options/",
        TwoFactorVerifyPasskeyOptionsView.as_view(),
        name="2fa_verify_passkey_options",
    ),
    path(
        "2fa/verify/passkey/",
        TwoFactorVerifyPasskeyView.as_view(),
        name="2fa_verify_passkey",
    ),
    path(
        "2fa/recovery-codes/",
        RecoveryCodesView.as_view(),
        name="2fa_recovery_codes",
    ),
    path(
        "2fa/recovery-codes/regenerate/",
        RecoveryCodesRegenerateView.as_view(),
        name="2fa_recovery_regenerate",
    ),
]

# Passkey Management
passkey_urls = [
    path(
        "passkey/register/options/",
        PasskeyRegisterOptionsView.as_view(),
        name="passkey_register_options",
    ),
    path(
        "passkey/register/verify/",
        PasskeyRegisterVerifyView.as_view(),
        name="passkey_register_verify",
    ),
    path("passkeys/", PasskeyListView.as_view(), name="passkey_list"),
    path("passkeys/<uuid:pk>/", PasskeyDetailView.as_view(), name="passkey_detail"),
    path(
        "passkey/authenticate/options/",
        PasskeyAuthenticateOptionsView.as_view(),
        name="passkey_auth_options",
    ),
    path(
        "passkey/authenticate/verify/",
        PasskeyAuthenticateVerifyView.as_view(),
        name="passkey_auth_verify",
    ),
]

# Organization 2FA Policy
org_2fa_urls = [
    path(
        "organization/2fa-policy/",
        OrgTwoFactorPolicyView.as_view(),
        name="org_2fa_policy",
    ),
]

urlpatterns = (
    auth_urls
    + signup_urls
    + user_urls
    + keys_urls
    + appsmith_urls
    + team_urls
    + rbac_urls
    + workspace_member_urls
    + organization_urls
    + aws_marketplace_urls
    + config_urls
    + two_factor_urls
    + passkey_urls
    + org_2fa_urls
    + [path("", include(router.urls))]
)
