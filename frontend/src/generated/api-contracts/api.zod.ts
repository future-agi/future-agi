/**
 * Auto-generated from the Django backend OpenAPI schema.
 * To modify these types, update Django serializers/views, regenerate OpenAPI, then run:
 *   yarn contracts:generate
 *
 * Future AGI Management API - management contracts
 * OpenAPI spec version: v1
 */
import * as zod from 'zod';

/**
 * GET /accounts/2fa/recovery-codes/ - Get remaining count.
 */
export const Accounts2faRecoveryCodesListResponse = zod.object({
  "remaining": zod.number()
})


/**
 * POST /accounts/2fa/recovery-codes/regenerate/ - Generate new codes.
 */
export const accounts2faRecoveryCodesRegenerateCreateBodyCodeMin = 6;
export const accounts2faRecoveryCodesRegenerateCreateBodyCodeMax = 10;




export const Accounts2faRecoveryCodesRegenerateCreateBody = zod.object({
  "code": zod.string().min(accounts2faRecoveryCodesRegenerateCreateBodyCodeMin).max(accounts2faRecoveryCodesRegenerateCreateBodyCodeMax).optional(),
  "password": zod.string().min(1).optional()
})




export const Accounts2faRecoveryCodesRegenerateCreateResponse = zod.object({
  "recovery_codes": zod.array(zod.string().min(1))
})


/**
 * GET /accounts/2fa/status/ - Current 2FA status.
 */
export const Accounts2faStatusListResponse = zod.object({
  "two_factor_enabled": zod.boolean(),
  "methods": zod.record(zod.string(), zod.string()),
  "recovery_codes_remaining": zod.number()
})


/**
 * DELETE /accounts/2fa/totp/ - Disable TOTP.
 */
export const accounts2faTotpDeleteBodyCodeMin = 6;
export const accounts2faTotpDeleteBodyCodeMax = 10;



export const Accounts2faTotpDeleteBody = zod.object({
  "code": zod.string().min(accounts2faTotpDeleteBodyCodeMin).max(accounts2faTotpDeleteBodyCodeMax)
})

export const Accounts2faTotpDeleteResponse = zod.object({
  "success": zod.boolean()
})


/**
 * POST /accounts/2fa/totp/confirm/ - Confirm TOTP with code.
 */
export const accounts2faTotpConfirmCreateBodyCodeMin = 6;
export const accounts2faTotpConfirmCreateBodyCodeMax = 6;



export const Accounts2faTotpConfirmCreateBody = zod.object({
  "code": zod.string().min(accounts2faTotpConfirmCreateBodyCodeMin).max(accounts2faTotpConfirmCreateBodyCodeMax)
})




export const Accounts2faTotpConfirmCreateResponse = zod.object({
  "success": zod.boolean(),
  "recovery_codes": zod.array(zod.string().min(1))
})


/**
 * POST /accounts/2fa/totp/setup/ - Begin TOTP setup.
 */
export const Accounts2faTotpSetupCreateBody = zod.object({

})






export const Accounts2faTotpSetupCreateResponse = zod.object({
  "qr_code": zod.string().min(1),
  "secret": zod.string().min(1),
  "provisioning_uri": zod.string().min(1)
})


/**
 * POST /accounts/2fa/verify/passkey/ - Verify passkey as 2FA during login.
 */
export const Accounts2faVerifyPasskeyCreateBody = zod.object({
  "challenge_token": zod.string().uuid(),
  "credential": zod.object({

}).passthrough(),
  "session_id": zod.string().optional()
})









export const Accounts2faVerifyPasskeyCreateResponse = zod.object({
  "access": zod.string().min(1).optional(),
  "refresh": zod.string().min(1).optional(),
  "requires_two_factor": zod.boolean().optional(),
  "challenge_token": zod.string().uuid().optional(),
  "methods": zod.array(zod.string().min(1)).optional(),
  "requires_org_setup": zod.boolean().optional(),
  "message": zod.string().min(1).optional(),
  "new_org": zod.boolean().optional(),
  "org_name": zod.string().min(1).optional(),
  "is_first_login": zod.boolean().optional(),
  "recovery_codes_warning": zod.string().min(1).optional()
})


/**
 * POST /accounts/2fa/verify/passkey/options/ - Get WebAuthn options for passkey as 2FA.
 */
export const Accounts2faVerifyPasskeyOptionsCreateBody = zod.object({
  "challenge_token": zod.string().uuid()
})























export const Accounts2faVerifyPasskeyOptionsCreateResponse = zod.object({
  "challenge": zod.string().min(1),
  "timeout": zod.number().optional(),
  "rp": zod.object({
  "id": zod.string().min(1).optional(),
  "name": zod.string().min(1).optional()
}).optional(),
  "user": zod.object({
  "id": zod.string().min(1),
  "name": zod.string().min(1),
  "displayName": zod.string().min(1).optional()
}).optional(),
  "pubKeyCredParams": zod.array(zod.object({
  "type": zod.string().min(1),
  "alg": zod.number()
})).optional(),
  "excludeCredentials": zod.array(zod.object({
  "type": zod.string().min(1),
  "id": zod.string().min(1),
  "transports": zod.array(zod.string().min(1)).optional()
})).optional(),
  "allowCredentials": zod.array(zod.object({
  "type": zod.string().min(1),
  "id": zod.string().min(1),
  "transports": zod.array(zod.string().min(1)).optional()
})).optional(),
  "authenticatorSelection": zod.object({
  "authenticatorAttachment": zod.string().min(1).optional(),
  "residentKey": zod.string().min(1).optional(),
  "requireResidentKey": zod.boolean().optional(),
  "userVerification": zod.string().min(1).optional()
}).optional(),
  "attestation": zod.string().min(1).optional(),
  "rpId": zod.string().min(1).optional(),
  "userVerification": zod.string().min(1).optional(),
  "extensions": zod.object({
  "appid": zod.string().min(1).optional(),
  "credProps": zod.boolean().optional(),
  "uvm": zod.boolean().optional()
}).optional(),
  "session_id": zod.string().uuid().optional()
})


/**
 * POST /accounts/2fa/verify/recovery/ - Verify recovery code during login.
 */
export const accounts2faVerifyRecoveryCreateBodyCodeMin = 6;
export const accounts2faVerifyRecoveryCreateBodyCodeMax = 10;



export const Accounts2faVerifyRecoveryCreateBody = zod.object({
  "challenge_token": zod.string().uuid(),
  "code": zod.string().min(accounts2faVerifyRecoveryCreateBodyCodeMin).max(accounts2faVerifyRecoveryCreateBodyCodeMax)
})









export const Accounts2faVerifyRecoveryCreateResponse = zod.object({
  "access": zod.string().min(1).optional(),
  "refresh": zod.string().min(1).optional(),
  "requires_two_factor": zod.boolean().optional(),
  "challenge_token": zod.string().uuid().optional(),
  "methods": zod.array(zod.string().min(1)).optional(),
  "requires_org_setup": zod.boolean().optional(),
  "message": zod.string().min(1).optional(),
  "new_org": zod.boolean().optional(),
  "org_name": zod.string().min(1).optional(),
  "is_first_login": zod.boolean().optional(),
  "recovery_codes_warning": zod.string().min(1).optional()
})


/**
 * POST /accounts/2fa/verify/totp/ - Verify TOTP during login (Phase 2).
 */
export const accounts2faVerifyTotpCreateBodyCodeMin = 6;
export const accounts2faVerifyTotpCreateBodyCodeMax = 10;



export const Accounts2faVerifyTotpCreateBody = zod.object({
  "challenge_token": zod.string().uuid(),
  "code": zod.string().min(accounts2faVerifyTotpCreateBodyCodeMin).max(accounts2faVerifyTotpCreateBodyCodeMax)
})









export const Accounts2faVerifyTotpCreateResponse = zod.object({
  "access": zod.string().min(1).optional(),
  "refresh": zod.string().min(1).optional(),
  "requires_two_factor": zod.boolean().optional(),
  "challenge_token": zod.string().uuid().optional(),
  "methods": zod.array(zod.string().min(1)).optional(),
  "requires_org_setup": zod.boolean().optional(),
  "message": zod.string().min(1).optional(),
  "new_org": zod.boolean().optional(),
  "org_name": zod.string().min(1).optional(),
  "is_first_login": zod.boolean().optional(),
  "recovery_codes_warning": zod.string().min(1).optional()
})


/**
 * GET  â€” validate the token without consuming it. Returns org info so the
       frontend can render a "Set Password" page.
POST â€” set the user's password, activate the account, accept the invite,
       and return JWT tokens for auto-login.
 * @summary Accept an invitation link.
 */
export const AccountsAcceptInvitationReadParams = zod.object({
  "uidb64": zod.string(),
  "token": zod.string()
})





export const AccountsAcceptInvitationReadResponse = zod.object({
  "valid": zod.boolean(),
  "email": zod.string().email().min(1),
  "org_name": zod.string().min(1)
})


/**
 * GET  â€” validate the token without consuming it. Returns org info so the
       frontend can render a "Set Password" page.
POST â€” set the user's password, activate the account, accept the invite,
       and return JWT tokens for auto-login.
 * @summary Accept an invitation link.
 */
export const AccountsAcceptInvitationCreateParams = zod.object({
  "uidb64": zod.string(),
  "token": zod.string()
})





export const AccountsAcceptInvitationCreateBody = zod.object({
  "new_password": zod.string().min(1),
  "repeat_password": zod.string().min(1)
})









export const AccountsAcceptInvitationCreateResponse = zod.object({
  "access": zod.string().min(1).optional(),
  "refresh": zod.string().min(1).optional(),
  "requires_two_factor": zod.boolean().optional(),
  "challenge_token": zod.string().uuid().optional(),
  "methods": zod.array(zod.string().min(1)).optional(),
  "requires_org_setup": zod.boolean().optional(),
  "message": zod.string().min(1).optional(),
  "new_org": zod.boolean().optional(),
  "org_name": zod.string().min(1).optional(),
  "is_first_login": zod.boolean().optional(),
  "recovery_codes_warning": zod.string().min(1).optional()
})











export const AccountsAppsmithUsersListResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().min(1),
  "previous": zod.string().min(1),
  "results": zod.array(zod.object({
  "id": zod.string().uuid(),
  "email": zod.string().email().min(1),
  "name": zod.string(),
  "organization_role": zod.string(),
  "organization": zod.object({
  "id": zod.string().uuid(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "name": zod.string().min(1),
  "display_name": zod.string(),
  "is_new": zod.boolean().optional(),
  "ws_enabled": zod.boolean().optional(),
  "region": zod.string().min(1).optional(),
  "require_2fa": zod.boolean().optional(),
  "require_2fa_grace_period_days": zod.number().optional(),
  "require_2fa_enforced_at": zod.string().datetime({"offset":true}).optional()
}),
  "created_at": zod.string().datetime({"offset":true}),
  "status": zod.string().min(1),
  "role": zod.string(),
  "goals": zod.array(zod.string().min(1)).optional()
})),
  "total_pages": zod.number(),
  "current_page": zod.number(),
  "total_queries": zod.number().optional()
})


export const AccountsAppsmithUsersCreateParams = zod.object({
  "user_id": zod.string()
})

export const accountsAppsmithUsersCreateBodyEmailMax = 255;

export const accountsAppsmithUsersCreateBodyPasswordMin = 8;
export const accountsAppsmithUsersCreateBodyPasswordMax = 128;

export const accountsAppsmithUsersCreateBodyOrganizationNameMax = 255;



export const AccountsAppsmithUsersCreateBody = zod.object({
  "email": zod.string().email().min(1).max(accountsAppsmithUsersCreateBodyEmailMax),
  "password": zod.string().min(accountsAppsmithUsersCreateBodyPasswordMin).max(accountsAppsmithUsersCreateBodyPasswordMax),
  "organization_name": zod.string().min(1).max(accountsAppsmithUsersCreateBodyOrganizationNameMax),
  "send_credential": zod.boolean()
})


export const AccountsAppsmithUsersPartialUpdateParams = zod.object({
  "user_id": zod.string()
})

export const accountsAppsmithUsersPartialUpdateBodyPasswordMin = 8;
export const accountsAppsmithUsersPartialUpdateBodyPasswordMax = 128;



export const AccountsAppsmithUsersPartialUpdateBody = zod.object({
  "password": zod.string().min(accountsAppsmithUsersPartialUpdateBodyPasswordMin).max(accountsAppsmithUsersPartialUpdateBodyPasswordMax)
})


export const accountsAppsmithUsersLoginCreateBodyEmailMax = 255;



export const AccountsAppsmithUsersLoginCreateBody = zod.object({
  "email": zod.string().email().min(1).max(accountsAppsmithUsersLoginCreateBodyEmailMax)
})









export const AccountsAppsmithUsersLoginCreateResponse = zod.object({
  "access": zod.string().min(1).optional(),
  "refresh": zod.string().min(1).optional(),
  "requires_two_factor": zod.boolean().optional(),
  "challenge_token": zod.string().uuid().optional(),
  "methods": zod.array(zod.string().min(1)).optional(),
  "requires_org_setup": zod.boolean().optional(),
  "message": zod.string().min(1).optional(),
  "new_org": zod.boolean().optional(),
  "org_name": zod.string().min(1).optional(),
  "is_first_login": zod.boolean().optional(),
  "recovery_codes_warning": zod.string().min(1).optional()
})


export const AccountsAppsmithUsersReadParams = zod.object({
  "user_id": zod.string()
})










export const AccountsAppsmithUsersReadResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().min(1),
  "previous": zod.string().min(1),
  "results": zod.array(zod.object({
  "id": zod.string().uuid(),
  "email": zod.string().email().min(1),
  "name": zod.string(),
  "organization_role": zod.string(),
  "organization": zod.object({
  "id": zod.string().uuid(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "name": zod.string().min(1),
  "display_name": zod.string(),
  "is_new": zod.boolean().optional(),
  "ws_enabled": zod.boolean().optional(),
  "region": zod.string().min(1).optional(),
  "require_2fa": zod.boolean().optional(),
  "require_2fa_grace_period_days": zod.number().optional(),
  "require_2fa_enforced_at": zod.string().datetime({"offset":true}).optional()
}),
  "created_at": zod.string().datetime({"offset":true}),
  "status": zod.string().min(1),
  "role": zod.string(),
  "goals": zod.array(zod.string().min(1)).optional()
})),
  "total_pages": zod.number(),
  "current_page": zod.number(),
  "total_queries": zod.number().optional()
})


/**
 * This endpoint is called after token verification to create a new user account
for an AWS Marketplace customer.
 * @summary Complete AWS Marketplace customer signup
 */





export const AccountsAwsMarketplaceSignupCreateBody = zod.object({
  "onboarding_token": zod.string().min(1),
  "email": zod.string().email().min(1),
  "full_name": zod.string().min(1)
})





export const AccountsAwsMarketplaceSignupCreateResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "message": zod.string().min(1),
  "user_email": zod.string().email().min(1)
})
})


/**
 * Self-hosted returns cloud=false with no region info.
Cloud returns the current region and available regions list.
 * @summary Public (unauthenticated) endpoint returning platform config.
Used by the frontend to decide whether to show region UI.
 */






export const AccountsConfigListResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "cloud": zod.boolean(),
  "region": zod.string().min(1),
  "available_regions": zod.array(zod.object({
  "code": zod.string().min(1),
  "label": zod.string().min(1),
  "app_url": zod.string().url().min(1)
}))
})
})


export const AccountsDeleteUsersDeleteBody = zod.object({
  "user_ids": zod.array(zod.string().uuid())
})





export const AccountsDeleteUsersDeleteResponseItem = zod.object({
  "user_id": zod.string().uuid(),
  "message": zod.string().min(1).optional(),
  "error": zod.string().min(1).optional()
})
export const AccountsDeleteUsersDeleteResponse = zod.array(AccountsDeleteUsersDeleteResponseItem)


export const AccountsFirstChecksListResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "keys": zod.boolean(),
  "dataset": zod.boolean(),
  "evaluation": zod.boolean(),
  "experiment": zod.boolean(),
  "observe": zod.boolean(),
  "invite": zod.boolean()
})
})





export const AccountsGetUserProfileDetailsListResponse = zod.object({
  "name": zod.string(),
  "email": zod.string().email().min(1),
  "org_name": zod.string()
})


export const AccountsKeyDeleteSecretKeyBody = zod.object({
  "key_id": zod.string().uuid()
})




export const AccountsKeyDeleteSecretKeyResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.string().min(1)
})


export const AccountsKeyDisableKeyBody = zod.object({
  "key_id": zod.string().uuid()
})




export const AccountsKeyDisableKeyResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.string().min(1)
})


export const AccountsKeyEnableKeyBody = zod.object({
  "key_id": zod.string().uuid()
})




export const AccountsKeyEnableKeyResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.string().min(1)
})


export const accountsKeyGenerateSecretKeyBodyKeyNameMax = 100;



export const AccountsKeyGenerateSecretKeyBody = zod.object({
  "key_name": zod.string().min(1).max(accountsKeyGenerateSecretKeyBodyKeyNameMax)
})








export const AccountsKeyGenerateSecretKeyResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "key_id": zod.string().uuid(),
  "key_name": zod.string().min(1),
  "api_key": zod.string().min(1),
  "masked_api_key": zod.string().min(1),
  "secret_key": zod.string().min(1),
  "masked_secret_key": zod.string().min(1)
})
})








export const AccountsKeyGetSecretKeysResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "metadata": zod.object({
  "total_rows": zod.number(),
  "total_pages": zod.number(),
  "page_number": zod.number(),
  "page_size": zod.number()
}),
  "table": zod.array(zod.object({
  "id": zod.string().uuid(),
  "key_name": zod.string(),
  "api_key": zod.string().min(1),
  "secret_key": zod.string().min(1),
  "created_by": zod.string().min(1),
  "created_at": zod.string().datetime({"offset":true}),
  "enabled": zod.boolean(),
  "type": zod.string().min(1)
}))
})
})







export const AccountsKeysListResponse = zod.object({
  "status": zod.string().min(1),
  "data": zod.object({
  "id": zod.string().uuid(),
  "api_key": zod.string().min(1),
  "secret_key": zod.string().min(1)
})
})


export const AccountsLogoutCreateBody = zod.object({
  "refresh": zod.string().optional()
})




export const AccountsLogoutCreateResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "message": zod.string().min(1)
})
})


/**
 * Capture the browser's IANA timezone for the authenticated user.
 */
export const accountsMeTimezoneCreateBodyTimezoneMax = 64;



export const AccountsMeTimezoneCreateBody = zod.object({
  "timezone": zod.string().min(1).max(accountsMeTimezoneCreateBodyTimezoneMax)
})




export const AccountsMeTimezoneCreateResponse = zod.object({
  "timezone": zod.string().min(1)
})


/**
 * Snooze the realtime track for N days (default 7). Daily still fires.
 */
export const AccountsNotificationsSnoozeListResponse = zod.string()


/**
 * One-click unsubscribe from both digest tracks. Token is HMAC-signed.
 */
export const AccountsNotificationsUnsubscribeListResponse = zod.string()


/**
 * Handle user onboarding data (role and goals)
 */



export const AccountsOnboardingListResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "role": zod.string(),
  "goals": zod.array(zod.string().min(1)),
  "completed": zod.boolean()
})
})


/**
 * Handle user onboarding data (role and goals)
 */
export const accountsOnboardingCreateBodyRoleMax = 255;

export const accountsOnboardingCreateBodyGoalsItemMax = 255;



export const AccountsOnboardingCreateBody = zod.object({
  "role": zod.string().min(1).max(accountsOnboardingCreateBodyRoleMax),
  "goals": zod.array(zod.string().min(1).max(accountsOnboardingCreateBodyGoalsItemMax))
})






export const AccountsOnboardingCreateResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "message": zod.string().min(1),
  "data": zod.object({
  "role": zod.string().min(1),
  "goals": zod.array(zod.string().min(1))
})
})
})


/**
 * GET is available to all authenticated members (read policy).
PUT is admin-gated inline (Level.ADMIN+) rather than via a permission
class so that a single view can serve both roles without splitting.
 * @summary GET/PUT /accounts/organization/2fa-policy/ - Org 2FA policy.
 */
export const AccountsOrganization2faPolicyListResponse = zod.object({
  "require_2fa": zod.boolean(),
  "require_2fa_grace_period_days": zod.number(),
  "require_2fa_enforced_at": zod.string().datetime({"offset":true})
})


/**
 * GET is available to all authenticated members (read policy).
PUT is admin-gated inline (Level.ADMIN+) rather than via a permission
class so that a single view can serve both roles without splitting.
 * @summary GET/PUT /accounts/organization/2fa-policy/ - Org 2FA policy.
 */
export const accountsOrganization2faPolicyUpdateBodyRequire2faGracePeriodDaysMax = 30;



export const AccountsOrganization2faPolicyUpdateBody = zod.object({
  "require_2fa": zod.boolean(),
  "require_2fa_grace_period_days": zod.number().min(1).max(accountsOrganization2faPolicyUpdateBodyRequire2faGracePeriodDaysMax).optional()
})

export const AccountsOrganization2faPolicyUpdateResponse = zod.object({
  "require_2fa": zod.boolean(),
  "require_2fa_grace_period_days": zod.number(),
  "require_2fa_enforced_at": zod.string().datetime({"offset":true})
})


/**
 * Create invites for one or more email addresses.
Also dual-writes to legacy User/membership records for backward compat.
 * @summary POST /accounts/organization/invite/
 */

export const accountsOrganizationInviteCreateBodyEmailsMax = 50;

export const accountsOrganizationInviteCreateBodyWorkspaceAccessDefault = [];

export const AccountsOrganizationInviteCreateBody = zod.object({
  "emails": zod.array(zod.string().email().min(1)).min(1).max(accountsOrganizationInviteCreateBodyEmailsMax),
  "org_level": zod.union([zod.literal(15),zod.literal(8),zod.literal(3),zod.literal(1)]).describe('Integer org level to grant (Owner=15, Admin=8, Member=3, Viewer=1).'),
  "workspace_access": zod.array(zod.object({
  "workspace_id": zod.string().uuid(),
  "level": zod.union([zod.literal(8),zod.literal(3),zod.literal(1)]).optional()
}).describe('List of {\"workspace_id\": \"<uuid>\", \"level\": <int>}.')).default(accountsOrganizationInviteCreateBodyWorkspaceAccessDefault).describe('List of {\"workspace_id\": \"<uuid>\", \"level\": <int>}.')
})





export const AccountsOrganizationInviteCreateResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "invited": zod.array(zod.string().email().min(1)),
  "already_members": zod.array(zod.string().email().min(1)).optional()
})
})


/**
 * DELETE /accounts/organization/invite/cancel/
Hard deletes the invite record.
 */
export const AccountsOrganizationInviteCancelDeleteBody = zod.object({
  "invite_id": zod.string().uuid()
})




export const AccountsOrganizationInviteCancelDeleteResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "message": zod.string().min(1)
})
})


/**
 * POST /accounts/organization/invite/resend/
Resets expiration and resends the invite email.
 */
export const AccountsOrganizationInviteResendCreateBody = zod.object({
  "invite_id": zod.string().uuid(),
  "org_level": zod.union([zod.literal(15),zod.literal(8),zod.literal(3),zod.literal(1)]).optional()
})




export const AccountsOrganizationInviteResendCreateResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "message": zod.string().min(1)
})
})


/**
 * Returns UNION of active members + pending/expired invites.
Status is derived at query time (Active / Pending / Expired).
 * @summary GET /accounts/organization/members/
 */
export const accountsOrganizationMembersListQueryPageDefault = 1;

export const accountsOrganizationMembersListQueryLimitDefault = 20;
export const accountsOrganizationMembersListQueryLimitMax = 100;

export const accountsOrganizationMembersListQuerySearchDefault = ``;
export const accountsOrganizationMembersListQueryFilterStatusDefault = [];
export const accountsOrganizationMembersListQueryFilterRoleDefault = [];
export const accountsOrganizationMembersListQuerySortDefault = `-created_at`;

export const AccountsOrganizationMembersListQueryParams = zod.object({
  "page": zod.number().min(1).default(accountsOrganizationMembersListQueryPageDefault),
  "limit": zod.number().min(1).max(accountsOrganizationMembersListQueryLimitMax).default(accountsOrganizationMembersListQueryLimitDefault),
  "search": zod.string().default(accountsOrganizationMembersListQuerySearchDefault),
  "filter_status": zod.array(zod.enum(['Active', 'Pending', 'Expired', 'Deactivated'])).default(accountsOrganizationMembersListQueryFilterStatusDefault),
  "filter_role": zod.array(zod.string().min(1)).default(accountsOrganizationMembersListQueryFilterRoleDefault),
  "sort": zod.enum(['name', '-name', 'email', '-email', 'status', '-status', 'type', '-type', 'date_joined', '-date_joined', 'created_at', '-created_at', 'org_level', '-org_level']).default(accountsOrganizationMembersListQuerySortDefault)
})









export const AccountsOrganizationMembersListResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "results": zod.array(zod.object({
  "id": zod.string().uuid(),
  "name": zod.string(),
  "email": zod.string().email().min(1),
  "org_level": zod.number().optional(),
  "org_role": zod.string().min(1).optional(),
  "ws_level": zod.number().optional(),
  "ws_role": zod.string().min(1).optional(),
  "workspaces": zod.array(zod.object({
  "workspace_id": zod.string().uuid(),
  "workspace_name": zod.string().min(1),
  "ws_level": zod.number(),
  "ws_role": zod.string().min(1),
  "auto_access": zod.boolean().optional()
})).optional(),
  "status": zod.string().min(1),
  "created_at": zod.string(),
  "type": zod.enum(['member', 'invite']),
  "auto_access": zod.boolean().optional()
})),
  "total": zod.number(),
  "page": zod.number(),
  "limit": zod.number()
})
})


/**
 * Re-activates a deactivated org membership and restores workspace
memberships that were soft-deactivated during removal.  If no prior
workspace memberships exist, the user is added to the default workspace.
 * @summary POST /accounts/organization/members/reactivate/
 */
export const AccountsOrganizationMembersReactivateCreateBody = zod.object({
  "user_id": zod.string().uuid()
})




export const AccountsOrganizationMembersReactivateCreateResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "message": zod.string().min(1),
  "user_id": zod.string().uuid()
})
})


/**
 * Soft-deactivates OrganizationMembership and cascades to workspace
memberships.  Signals handle Redis clear + audit log.
 * @summary DELETE /accounts/organization/members/remove/
 */
export const AccountsOrganizationMembersRemoveDeleteBody = zod.object({
  "user_id": zod.string().uuid()
})




export const AccountsOrganizationMembersRemoveDeleteResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "message": zod.string().min(1),
  "user_id": zod.string().uuid()
})
})


/**
 * Update a member's org level and/or workspace level.
 * @summary POST /accounts/organization/members/role/
 */
export const accountsOrganizationMembersRoleCreateBodyWorkspaceAccessDefault = [];

export const AccountsOrganizationMembersRoleCreateBody = zod.object({
  "user_id": zod.string().uuid(),
  "org_level": zod.union([zod.literal(15),zod.literal(8),zod.literal(3),zod.literal(1)]).optional(),
  "ws_level": zod.union([zod.literal(8),zod.literal(3),zod.literal(1)]).optional(),
  "workspace_id": zod.string().uuid().optional().describe('Required when updating ws_level.'),
  "workspace_access": zod.array(zod.object({
  "workspace_id": zod.string().uuid(),
  "level": zod.union([zod.literal(8),zod.literal(3),zod.literal(1)]).optional()
}).describe('List of {\"workspace_id\": \"<uuid>\", \"level\": <int>}.')).default(accountsOrganizationMembersRoleCreateBodyWorkspaceAccessDefault).describe('List of {workspace_id, level} for explicit workspace grants on demotion.')
})




export const AccountsOrganizationMembersRoleCreateResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "message": zod.string().min(1),
  "changes": zod.object({

}).passthrough()
})
})


/**
 * Get all organizations the user has access to.
 */



export const AccountsOrganizationsListResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "organizations": zod.array(zod.object({
  "id": zod.string().uuid(),
  "name": zod.string().min(1),
  "display_name": zod.string(),
  "role": zod.string(),
  "level": zod.number(),
  "is_selected": zod.boolean()
})),
  "total_count": zod.number()
})
})


/**
 * Select an organization for the current session.
 */
export const AccountsOrganizationsCreateBody = zod.object({
  "organization_id": zod.string().uuid()
})





export const AccountsOrganizationsCreateResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "message": zod.string().min(1),
  "organization": zod.object({
  "id": zod.string().uuid(),
  "name": zod.string().min(1),
  "display_name": zod.string(),
  "ws_enabled": zod.boolean().optional()
})
})
})


/**
 * For users who were removed from their org and want to start fresh.
Only accessible to authenticated users with no current organization.
 * @summary POST /accounts/organizations/create/
 */
export const AccountsOrganizationsCreateCreateBody = zod.object({
  "organization_name": zod.string().optional()
})


/**
 * Get the currently selected organization for the user.
 */




export const AccountsOrganizationsCurrentListResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "organization": zod.object({
  "id": zod.string().uuid(),
  "name": zod.string().min(1),
  "display_name": zod.string(),
  "ws_enabled": zod.boolean().optional()
}),
  "role": zod.string().optional(),
  "level": zod.number().optional(),
  "source": zod.string().optional(),
  "message": zod.string().min(1).optional()
})
})


/**
 * Create a new organization for an already-authenticated user.
Unlike OrganizationCreateAPIView (which is for org-less users),
this allows any user to create additional organizations.
The user becomes Owner of the new org via OrganizationMembership.
Does NOT change user.organization FK (primary org stays the same).
 * @summary POST /accounts/organizations/new/
 */



export const AccountsOrganizationsNewCreateBody = zod.object({
  "name": zod.string().min(1),
  "display_name": zod.string().optional()
})


/**
 * Returns the target org's last-used workspace (from orgWorkspaceMap)
or its default workspace, so the frontend can update both contexts.
 * @summary Switch to a different organization.
 */
export const AccountsOrganizationsSwitchCreateBody = zod.object({
  "organization_id": zod.string().uuid()
})





export const AccountsOrganizationsSwitchCreateResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "organization": zod.object({
  "id": zod.string().uuid(),
  "name": zod.string().min(1),
  "display_name": zod.string(),
  "ws_enabled": zod.boolean().optional()
}),
  "org_role": zod.string(),
  "org_level": zod.number(),
  "workspace_role": zod.string(),
  "workspace": zod.object({
  "id": zod.string().uuid(),
  "name": zod.string().min(1),
  "display_name": zod.string(),
  "description": zod.string().optional(),
  "is_default": zod.boolean().optional()
}).optional()
})
})


/**
 * Update the current organization's name/display_name.
Only accessible to Owner or Admin of the organization.
 * @summary PATCH /accounts/organizations/update/
 */
export const AccountsOrganizationsUpdatePartialUpdateBody = zod.object({
  "name": zod.string().optional(),
  "display_name": zod.string().optional()
})




export const AccountsOrganizationsUpdatePartialUpdateResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "id": zod.string().uuid(),
  "name": zod.string().min(1),
  "display_name": zod.string()
})
})


/**
 * POST /accounts/passkey/authenticate/options/ - Passwordless auth options.
 */
export const AccountsPasskeyAuthenticateOptionsCreateBody = zod.object({

})























export const AccountsPasskeyAuthenticateOptionsCreateResponse = zod.object({
  "challenge": zod.string().min(1),
  "timeout": zod.number().optional(),
  "rp": zod.object({
  "id": zod.string().min(1).optional(),
  "name": zod.string().min(1).optional()
}).optional(),
  "user": zod.object({
  "id": zod.string().min(1),
  "name": zod.string().min(1),
  "displayName": zod.string().min(1).optional()
}).optional(),
  "pubKeyCredParams": zod.array(zod.object({
  "type": zod.string().min(1),
  "alg": zod.number()
})).optional(),
  "excludeCredentials": zod.array(zod.object({
  "type": zod.string().min(1),
  "id": zod.string().min(1),
  "transports": zod.array(zod.string().min(1)).optional()
})).optional(),
  "allowCredentials": zod.array(zod.object({
  "type": zod.string().min(1),
  "id": zod.string().min(1),
  "transports": zod.array(zod.string().min(1)).optional()
})).optional(),
  "authenticatorSelection": zod.object({
  "authenticatorAttachment": zod.string().min(1).optional(),
  "residentKey": zod.string().min(1).optional(),
  "requireResidentKey": zod.boolean().optional(),
  "userVerification": zod.string().min(1).optional()
}).optional(),
  "attestation": zod.string().min(1).optional(),
  "rpId": zod.string().min(1).optional(),
  "userVerification": zod.string().min(1).optional(),
  "extensions": zod.object({
  "appid": zod.string().min(1).optional(),
  "credProps": zod.boolean().optional(),
  "uvm": zod.boolean().optional()
}).optional(),
  "session_id": zod.string().uuid().optional()
})


/**
 * POST /accounts/passkey/authenticate/verify/ - Passwordless auth verify.
 */
export const accountsPasskeyAuthenticateVerifyCreateBodyNameMax = 255;



export const AccountsPasskeyAuthenticateVerifyCreateBody = zod.object({
  "credential": zod.object({

}).passthrough(),
  "session_id": zod.string().optional(),
  "name": zod.string().max(accountsPasskeyAuthenticateVerifyCreateBodyNameMax).optional()
})









export const AccountsPasskeyAuthenticateVerifyCreateResponse = zod.object({
  "access": zod.string().min(1).optional(),
  "refresh": zod.string().min(1).optional(),
  "requires_two_factor": zod.boolean().optional(),
  "challenge_token": zod.string().uuid().optional(),
  "methods": zod.array(zod.string().min(1)).optional(),
  "requires_org_setup": zod.boolean().optional(),
  "message": zod.string().min(1).optional(),
  "new_org": zod.boolean().optional(),
  "org_name": zod.string().min(1).optional(),
  "is_first_login": zod.boolean().optional(),
  "recovery_codes_warning": zod.string().min(1).optional()
})


/**
 * POST /accounts/passkey/register/options/ - Get registration options.
 */
export const AccountsPasskeyRegisterOptionsCreateBody = zod.object({

})























export const AccountsPasskeyRegisterOptionsCreateResponse = zod.object({
  "challenge": zod.string().min(1),
  "timeout": zod.number().optional(),
  "rp": zod.object({
  "id": zod.string().min(1).optional(),
  "name": zod.string().min(1).optional()
}).optional(),
  "user": zod.object({
  "id": zod.string().min(1),
  "name": zod.string().min(1),
  "displayName": zod.string().min(1).optional()
}).optional(),
  "pubKeyCredParams": zod.array(zod.object({
  "type": zod.string().min(1),
  "alg": zod.number()
})).optional(),
  "excludeCredentials": zod.array(zod.object({
  "type": zod.string().min(1),
  "id": zod.string().min(1),
  "transports": zod.array(zod.string().min(1)).optional()
})).optional(),
  "allowCredentials": zod.array(zod.object({
  "type": zod.string().min(1),
  "id": zod.string().min(1),
  "transports": zod.array(zod.string().min(1)).optional()
})).optional(),
  "authenticatorSelection": zod.object({
  "authenticatorAttachment": zod.string().min(1).optional(),
  "residentKey": zod.string().min(1).optional(),
  "requireResidentKey": zod.boolean().optional(),
  "userVerification": zod.string().min(1).optional()
}).optional(),
  "attestation": zod.string().min(1).optional(),
  "rpId": zod.string().min(1).optional(),
  "userVerification": zod.string().min(1).optional(),
  "extensions": zod.object({
  "appid": zod.string().min(1).optional(),
  "credProps": zod.boolean().optional(),
  "uvm": zod.boolean().optional()
}).optional(),
  "session_id": zod.string().uuid().optional()
})


/**
 * POST /accounts/passkey/register/verify/ - Verify registration.
 */
export const accountsPasskeyRegisterVerifyCreateBodyNameDefault = ``;
export const accountsPasskeyRegisterVerifyCreateBodyNameMax = 255;



export const AccountsPasskeyRegisterVerifyCreateBody = zod.object({
  "credential": zod.object({

}).passthrough(),
  "name": zod.string().min(1).max(accountsPasskeyRegisterVerifyCreateBodyNameMax).default(accountsPasskeyRegisterVerifyCreateBodyNameDefault)
})


/**
 * GET /accounts/passkeys/ - List user's passkeys.
 */



export const AccountsPasskeysListResponseItem = zod.object({
  "id": zod.string().uuid(),
  "name": zod.string().min(1),
  "created_at": zod.string().datetime({"offset":true}),
  "last_used_at": zod.string().datetime({"offset":true})
})
export const AccountsPasskeysListResponse = zod.array(AccountsPasskeysListResponseItem)


/**
 * PATCH/DELETE /accounts/passkeys/<uuid:pk>/ - Rename or delete.
 */
export const AccountsPasskeysPartialUpdateParams = zod.object({
  "id": zod.string()
})

export const accountsPasskeysPartialUpdateBodyNameMax = 255;



export const AccountsPasskeysPartialUpdateBody = zod.object({
  "name": zod.string().min(1).max(accountsPasskeysPartialUpdateBodyNameMax)
})




export const AccountsPasskeysPartialUpdateResponse = zod.object({
  "id": zod.string().uuid(),
  "name": zod.string().min(1)
})


/**
 * PATCH/DELETE /accounts/passkeys/<uuid:pk>/ - Rename or delete.
 */
export const AccountsPasskeysDeleteParams = zod.object({
  "id": zod.string()
})


export const AccountsPasswordResetConfirmCreateParams = zod.object({
  "uidb64": zod.string(),
  "token": zod.string()
})





export const AccountsPasswordResetConfirmCreateBody = zod.object({
  "new_password": zod.string().min(1),
  "repeat_password": zod.string().min(1)
})




export const AccountsPasswordResetConfirmCreateResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "message": zod.string().min(1)
})
})





export const AccountsPasswordResetInitiateCreateBody = zod.object({
  "email": zod.string().email().min(1)
})




export const AccountsPasswordResetInitiateCreateResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "message": zod.string().min(1)
})
})







export const AccountsRedisKeyCreateBody = zod.object({
  "access_token_id": zod.string().min(1),
  "key": zod.string().min(1),
  "value": zod.object({

}).passthrough().optional(),
  "expiry": zod.number().min(1).optional()
})





export const AccountsRedisKeyCreateResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "message": zod.string().min(1),
  "key": zod.string().min(1),
  "value": zod.object({

}).passthrough()
})
})







export const AccountsRedisKeyDeleteBody = zod.object({
  "access_token_id": zod.string().min(1),
  "key": zod.string().min(1),
  "value": zod.object({

}).passthrough().optional(),
  "expiry": zod.number().min(1).optional()
})





export const AccountsRedisKeyDeleteResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "message": zod.string().min(1),
  "key": zod.string().min(1)
})
})


export const AccountsResendInvitationEmailsCreateBody = zod.object({
  "user_ids": zod.array(zod.string().uuid())
})





export const AccountsResendInvitationEmailsCreateResponseItem = zod.object({
  "user_id": zod.string().uuid(),
  "message": zod.string().min(1).optional(),
  "error": zod.string().min(1).optional()
})
export const AccountsResendInvitationEmailsCreateResponse = zod.array(AccountsResendInvitationEmailsCreateResponseItem)




export const accountsSignupCreateBodyAllowEmailDefault = false;

export const AccountsSignupCreateBody = zod.object({
  "email": zod.string().email().min(1),
  "full_name": zod.string().min(1),
  "company_name": zod.string().optional(),
  "password": zod.string().optional(),
  "allow_email": zod.boolean().default(accountsSignupCreateBodyAllowEmailDefault),
  "recaptcha_response": zod.string().optional()
})




export const AccountsSignupCreateResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "message": zod.string().min(1)
})
})
















export const AccountsTeamUsersListResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "org_name": zod.string().min(1),
  "workspace_name": zod.string().min(1).optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid(),
  "email": zod.string().email().min(1),
  "name": zod.string(),
  "organization_role": zod.string(),
  "organization": zod.object({
  "id": zod.string().uuid(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "name": zod.string().min(1),
  "display_name": zod.string(),
  "is_new": zod.boolean().optional(),
  "ws_enabled": zod.boolean().optional(),
  "region": zod.string().min(1).optional(),
  "require_2fa": zod.boolean().optional(),
  "require_2fa_grace_period_days": zod.number().optional(),
  "require_2fa_enforced_at": zod.string().datetime({"offset":true}).optional()
}).optional(),
  "created_at": zod.string().min(1),
  "status": zod.string().min(1),
  "role": zod.string(),
  "goals": zod.array(zod.string().min(1)).optional(),
  "membership_type": zod.string().min(1).optional(),
  "workspace_role": zod.string().min(1).optional(),
  "workspace_member": zod.boolean().optional(),
  "workspaces": zod.array(zod.object({
  "id": zod.string().uuid(),
  "name": zod.string().min(1),
  "role": zod.string().min(1)
})).optional()
})),
  "total": zod.number()
})
})


export const AccountsTeamUsersCreateParams = zod.object({
  "member_id": zod.string()
})

export const accountsTeamUsersCreateBodyOrgNameMax = 255;

export const accountsTeamUsersCreateBodyWorkspaceNameMax = 255;

export const accountsTeamUsersCreateBodyWorkspaceDisplayNameMax = 255;

export const accountsTeamUsersCreateBodyMembersItemEmailMax = 255;

export const accountsTeamUsersCreateBodyMembersItemNameMax = 255;

export const accountsTeamUsersCreateBodyMembersDefault = [];

export const AccountsTeamUsersCreateBody = zod.object({
  "org_name": zod.string().max(accountsTeamUsersCreateBodyOrgNameMax).optional(),
  "workspace": zod.object({
  "name": zod.string().max(accountsTeamUsersCreateBodyWorkspaceNameMax).optional(),
  "display_name": zod.string().max(accountsTeamUsersCreateBodyWorkspaceDisplayNameMax).optional(),
  "description": zod.string().optional()
}).optional(),
  "members": zod.array(zod.object({
  "email": zod.string().email().min(1).max(accountsTeamUsersCreateBodyMembersItemEmailMax),
  "role": zod.enum(['Owner', 'Admin', 'Member', 'Viewer', 'workspace_admin', 'workspace_member', 'workspace_viewer']).optional(),
  "organization_role": zod.enum(['Owner', 'Admin', 'Member', 'Viewer']).optional(),
  "name": zod.string().min(1).max(accountsTeamUsersCreateBodyMembersItemNameMax)
})).default(accountsTeamUsersCreateBodyMembersDefault)
})


export const AccountsTeamUsersDeleteParams = zod.object({
  "member_id": zod.string()
})





export const AccountsTeamUsersDeleteResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "message": zod.string().min(1),
  "removed_from": zod.string().min(1)
})
})


export const AccountsTeamUsersReadParams = zod.object({
  "member_id": zod.string()
})















export const AccountsTeamUsersReadResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "org_name": zod.string().min(1),
  "workspace_name": zod.string().min(1).optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid(),
  "email": zod.string().email().min(1),
  "name": zod.string(),
  "organization_role": zod.string(),
  "organization": zod.object({
  "id": zod.string().uuid(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "name": zod.string().min(1),
  "display_name": zod.string(),
  "is_new": zod.boolean().optional(),
  "ws_enabled": zod.boolean().optional(),
  "region": zod.string().min(1).optional(),
  "require_2fa": zod.boolean().optional(),
  "require_2fa_grace_period_days": zod.number().optional(),
  "require_2fa_enforced_at": zod.string().datetime({"offset":true}).optional()
}).optional(),
  "created_at": zod.string().min(1),
  "status": zod.string().min(1),
  "role": zod.string(),
  "goals": zod.array(zod.string().min(1)).optional(),
  "membership_type": zod.string().min(1).optional(),
  "workspace_role": zod.string().min(1).optional(),
  "workspace_member": zod.boolean().optional(),
  "workspaces": zod.array(zod.object({
  "id": zod.string().uuid(),
  "name": zod.string().min(1),
  "role": zod.string().min(1)
})).optional()
})),
  "total": zod.number()
})
})




export const accountsTokenCreateBodyRememberMeDefault = false;

export const AccountsTokenCreateBody = zod.object({
  "email": zod.string().email().min(1),
  "password": zod.string().min(1),
  "remember_me": zod.boolean().default(accountsTokenCreateBodyRememberMeDefault),
  "recaptcha_response": zod.string().optional()
})









export const AccountsTokenCreateResponse = zod.object({
  "access": zod.string().min(1).optional(),
  "refresh": zod.string().min(1).optional(),
  "requires_two_factor": zod.boolean().optional(),
  "challenge_token": zod.string().uuid().optional(),
  "methods": zod.array(zod.string().min(1)).optional(),
  "requires_org_setup": zod.boolean().optional(),
  "message": zod.string().min(1).optional(),
  "new_org": zod.boolean().optional(),
  "org_name": zod.string().min(1).optional(),
  "is_first_login": zod.boolean().optional(),
  "recovery_codes_warning": zod.string().min(1).optional()
})



export const accountsTokenRefreshCreateBodyLocalhostBypassDefault = false;

export const AccountsTokenRefreshCreateBody = zod.object({
  "refresh": zod.string().min(1),
  "recaptcha_response": zod.string().optional(),
  "localhost_bypass": zod.boolean().default(accountsTokenRefreshCreateBodyLocalhostBypassDefault)
})




export const AccountsTokenRefreshCreateResponse = zod.object({
  "access": zod.string().min(1)
})


export const AccountsUpdateUserFullNameCreateBody = zod.object({
  "full_name": zod.string().optional(),
  "name": zod.string().optional()
})




export const AccountsUpdateUserFullNameCreateResponse = zod.object({
  "message": zod.string().min(1)
})






export const AccountsUpdateUserCreateBody = zod.object({
  "user_id": zod.string().uuid(),
  "email": zod.string().email().min(1).optional(),
  "name": zod.string().min(1).optional(),
  "organization_role": zod.enum(['Owner', 'Admin', 'Member', 'Viewer', 'workspace_admin', 'workspace_member', 'workspace_viewer']).optional()
})




export const AccountsUpdateUserCreateResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.string().min(1)
})








export const AccountsUserInfoListResponse = zod.object({
  "id": zod.string().uuid(),
  "email": zod.string().email().min(1),
  "name": zod.string(),
  "organization_role": zod.string(),
  "organization": zod.object({
  "id": zod.string().uuid(),
  "name": zod.string().min(1),
  "display_name": zod.string(),
  "ws_enabled": zod.boolean().optional()
}),
  "created_at": zod.string().datetime({"offset":true}),
  "status": zod.string().min(1),
  "role": zod.string(),
  "goals": zod.array(zod.string().min(1)).optional(),
  "remember_me": zod.boolean(),
  "get_started_completed": zod.boolean(),
  "onboarding_completed": zod.boolean(),
  "ws_enabled": zod.boolean(),
  "requires_org_setup": zod.boolean().optional(),
  "default_workspace_id": zod.string().uuid(),
  "default_workspace_name": zod.string(),
  "default_workspace_display_name": zod.string(),
  "default_workspace_role": zod.string(),
  "org_level": zod.number(),
  "ws_level": zod.number(),
  "effective_level": zod.number(),
  "has_2fa_enabled": zod.boolean().optional(),
  "two_factor_methods": zod.object({
  "totp": zod.boolean(),
  "passkey": zod.boolean()
}).optional(),
  "org_2fa_required": zod.boolean().optional(),
  "org_2fa_grace_ends_at": zod.string().datetime({"offset":true}).optional()
})


/**
 * Deactivate user by marking is_active as False
 */
export const AccountsUserDeactivateCreateBody = zod.object({
  "user_id": zod.string().uuid()
})





export const AccountsUserDeactivateCreateResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "message": zod.string().min(1),
  "user_id": zod.string().uuid(),
  "user_email": zod.string().email().min(1),
  "user_name": zod.string()
})
})


/**
 * Delete user or remove invite at organization or workspace level
 */
export const AccountsUserDeleteCreateBody = zod.object({
  "user_id": zod.string().uuid()
})






export const AccountsUserDeleteCreateResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "message": zod.string().min(1),
  "user_id": zod.string().uuid(),
  "workspace": zod.string().min(1).optional(),
  "level": zod.string().min(1)
})
})


/**
 * Get paginated list of users with filtering at workspace level
 */
export const accountsUserListListQueryPageDefault = 1;

export const accountsUserListListQueryLimitDefault = 10;
export const accountsUserListListQueryLimitMax = 100;

export const accountsUserListListQuerySearchDefault = ``;
export const accountsUserListListQuerySortDefault = [];
export const accountsUserListListQueryFilterStatusDefault = [];
export const accountsUserListListQueryFilterRoleDefault = [];

export const AccountsUserListListQueryParams = zod.object({
  "page": zod.number().min(1).default(accountsUserListListQueryPageDefault),
  "limit": zod.number().min(1).max(accountsUserListListQueryLimitMax).default(accountsUserListListQueryLimitDefault),
  "search": zod.string().default(accountsUserListListQuerySearchDefault),
  "sort": zod.string().default(accountsUserListListQuerySortDefault),
  "workspace_id": zod.string().uuid().optional(),
  "filter_status": zod.array(zod.enum(['All status', 'Active', 'Inactive', 'Pending', 'Expired', 'Request Pending', 'Request Expired'])).default(accountsUserListListQueryFilterStatusDefault),
  "filter_role": zod.array(zod.enum(['Owner', 'Admin', 'Member', 'Viewer', 'workspace_admin', 'workspace_member', 'workspace_viewer'])).default(accountsUserListListQueryFilterRoleDefault)
})









export const AccountsUserListListResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().min(1),
  "previous": zod.string().min(1),
  "results": zod.array(zod.object({
  "id": zod.string().uuid(),
  "name": zod.string(),
  "email": zod.string().email().min(1),
  "role": zod.string(),
  "status": zod.string().min(1),
  "start_date": zod.string(),
  "last_updated_date": zod.string(),
  "workspace_role": zod.string().min(1).optional(),
  "workspace_member_since": zod.string().optional(),
  "invited_by": zod.string().min(1).optional()
})),
  "total_pages": zod.number(),
  "current_page": zod.number()
})


/**
 * Resend invitation email with workspace context
 */
export const AccountsUserResendInviteCreateBody = zod.object({
  "user_id": zod.string().uuid()
})





export const AccountsUserResendInviteCreateResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "message": zod.string().min(1),
  "user_id": zod.string().uuid(),
  "workspace": zod.string().min(1).optional()
})
})


/**
 * Update user role at organization or workspace level
 */
export const AccountsUserRoleUpdateCreateBody = zod.object({
  "user_id": zod.string().uuid(),
  "new_role": zod.enum(['Owner', 'Admin', 'Member', 'Viewer', 'workspace_admin', 'workspace_member', 'workspace_viewer']),
  "workspace_id": zod.string().uuid().optional()
})








export const AccountsUserRoleUpdateCreateResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "message": zod.string().min(1),
  "user_id": zod.string().uuid(),
  "new_role": zod.string().min(1),
  "workspace_role": zod.string().min(1).optional(),
  "workspace": zod.string().min(1).optional(),
  "level": zod.string().min(1)
})
})


/**
 * Invite users to workspaces
 */


export const accountsWorkspaceInviteCreateBodyRoleDefault = `workspace_member`;
export const accountsWorkspaceInviteCreateBodySelectAllDefault = false;

export const AccountsWorkspaceInviteCreateBody = zod.object({
  "emails": zod.array(zod.string().email().min(1)).min(1),
  "role": zod.enum(['workspace_member', 'workspace_admin', 'workspace_viewer', 'Member', 'Viewer', 'Owner', 'Admin']).default(accountsWorkspaceInviteCreateBodyRoleDefault),
  "select_all": zod.boolean().default(accountsWorkspaceInviteCreateBodySelectAllDefault),
  "workspace_ids": zod.array(zod.string().uuid()).optional()
})







export const AccountsWorkspaceInviteCreateResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "results": zod.array(zod.object({
  "email": zod.string().email().min(1),
  "status": zod.string().min(1),
  "workspaces": zod.array(zod.string().uuid()),
  "select_all": zod.boolean(),
  "total_workspaces": zod.number()
})),
  "total_invited": zod.number(),
  "select_all": zod.boolean(),
  "total_workspaces": zod.number(),
  "errors": zod.array(zod.object({
  "email": zod.string().email().min(1).optional(),
  "error": zod.string().min(1)
})).optional()
})
})


/**
 * Get paginated list of workspaces
 */
export const accountsWorkspaceListListQueryPageDefault = 1;

export const accountsWorkspaceListListQueryLimitDefault = 10;
export const accountsWorkspaceListListQueryLimitMax = 100;

export const accountsWorkspaceListListQuerySearchDefault = ``;
export const accountsWorkspaceListListQuerySortDefault = ``;

export const AccountsWorkspaceListListQueryParams = zod.object({
  "page": zod.number().min(1).default(accountsWorkspaceListListQueryPageDefault),
  "limit": zod.number().min(1).max(accountsWorkspaceListListQueryLimitMax).default(accountsWorkspaceListListQueryLimitDefault),
  "search": zod.string().default(accountsWorkspaceListListQuerySearchDefault),
  "sort": zod.string().default(accountsWorkspaceListListQuerySortDefault)
})







export const AccountsWorkspaceListListResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().min(1),
  "previous": zod.string().min(1),
  "results": zod.array(zod.object({
  "id": zod.string().uuid(),
  "name": zod.string().min(1),
  "display_name": zod.string(),
  "admin_names": zod.array(zod.object({
  "name": zod.string(),
  "id": zod.string().uuid()
})).optional(),
  "start_data": zod.string().optional(),
  "last_update_date": zod.string().optional(),
  "invite_link": zod.string().optional(),
  "user_ws_level": zod.number().optional(),
  "user_ws_role": zod.string().min(1).optional()
})),
  "total_pages": zod.number(),
  "current_page": zod.number()
})


/**
 * Switch to a different workspace with proper validation
 */
export const AccountsWorkspaceSwitchCreateBody = zod.object({
  "old_workspace_id": zod.string().uuid().optional(),
  "new_workspace_id": zod.string().uuid()
})








export const AccountsWorkspaceSwitchCreateResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "message": zod.string().min(1),
  "workspace": zod.object({
  "id": zod.string().uuid(),
  "name": zod.string().min(1),
  "display_name": zod.string(),
  "description": zod.string().optional(),
  "is_default": zod.boolean().optional()
}),
  "user_role": zod.string().min(1),
  "access_type": zod.string().min(1),
  "organization": zod.string().min(1)
})
})


/**
 * Returns members of a specific workspace.
Org Admin+ users who auto-access are included with derived WS Admin role.
 * @summary GET /accounts/workspace/<workspace_id>/members/
 */
export const AccountsWorkspaceMembersListParams = zod.object({
  "workspace_id": zod.string()
})

export const accountsWorkspaceMembersListQueryPageDefault = 1;

export const accountsWorkspaceMembersListQueryLimitDefault = 20;
export const accountsWorkspaceMembersListQueryLimitMax = 100;

export const accountsWorkspaceMembersListQuerySearchDefault = ``;
export const accountsWorkspaceMembersListQueryFilterStatusDefault = [];
export const accountsWorkspaceMembersListQueryFilterRoleDefault = [];
export const accountsWorkspaceMembersListQuerySortDefault = `-created_at`;

export const AccountsWorkspaceMembersListQueryParams = zod.object({
  "page": zod.number().min(1).default(accountsWorkspaceMembersListQueryPageDefault),
  "limit": zod.number().min(1).max(accountsWorkspaceMembersListQueryLimitMax).default(accountsWorkspaceMembersListQueryLimitDefault),
  "search": zod.string().default(accountsWorkspaceMembersListQuerySearchDefault),
  "filter_status": zod.array(zod.enum(['Active', 'Pending', 'Expired'])).default(accountsWorkspaceMembersListQueryFilterStatusDefault),
  "filter_role": zod.array(zod.string().min(1)).default(accountsWorkspaceMembersListQueryFilterRoleDefault),
  "sort": zod.enum(['name', '-name', 'email', '-email', 'status', '-status', 'type', '-type', 'date_joined', '-date_joined', 'created_at', '-created_at', 'ws_level', '-ws_level']).default(accountsWorkspaceMembersListQuerySortDefault)
})







export const AccountsWorkspaceMembersListResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "results": zod.array(zod.object({
  "id": zod.string().uuid(),
  "name": zod.string(),
  "email": zod.string().email().min(1),
  "ws_level": zod.number().optional(),
  "ws_role": zod.string().min(1).optional(),
  "org_level": zod.number().optional(),
  "org_role": zod.string().min(1).optional(),
  "status": zod.string().min(1),
  "created_at": zod.string(),
  "type": zod.enum(['member', 'invite']),
  "auto_access": zod.boolean().optional()
})),
  "total": zod.number(),
  "page": zod.number(),
  "limit": zod.number()
})
})


/**
 * Remove a member from a workspace only (keeps org membership).
 * @summary DELETE /accounts/workspace/<workspace_id>/members/remove/
 */
export const AccountsWorkspaceMembersRemoveDeleteParams = zod.object({
  "workspace_id": zod.string()
})

export const AccountsWorkspaceMembersRemoveDeleteBody = zod.object({
  "user_id": zod.string().uuid()
})




export const AccountsWorkspaceMembersRemoveDeleteResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "message": zod.string().min(1),
  "user_id": zod.string().uuid()
})
})


/**
 * Update a member's workspace role.
 * @summary POST /accounts/workspace/<workspace_id>/members/role/
 */
export const AccountsWorkspaceMembersRoleCreateParams = zod.object({
  "workspace_id": zod.string()
})

export const AccountsWorkspaceMembersRoleCreateBody = zod.object({
  "user_id": zod.string().uuid(),
  "ws_level": zod.union([zod.literal(8),zod.literal(3),zod.literal(1)])
})





export const AccountsWorkspaceMembersRoleCreateResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "message": zod.string().min(1),
  "user_id": zod.string().uuid(),
  "ws_level": zod.number(),
  "ws_role": zod.string().min(1)
})
})


/**
 * Get workspaces for the current organization
 */





export const AccountsWorkspacesListResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "organization": zod.string().min(1),
  "workspaces": zod.array(zod.object({
  "id": zod.string().uuid(),
  "name": zod.string().min(1),
  "display_name": zod.string(),
  "description": zod.string(),
  "is_default": zod.boolean(),
  "member_count": zod.number(),
  "created_at": zod.string().min(1),
  "created_by": zod.string()
})),
  "total": zod.number()
})
})


/**
 * Create a new workspace
 */
export const AccountsWorkspacesCreateParams = zod.object({
  "workspace_id": zod.string()
})



export const accountsWorkspacesCreateBodyEmailsDefault = [];

export const AccountsWorkspacesCreateBody = zod.object({
  "name": zod.string().min(1),
  "display_name": zod.string().optional(),
  "description": zod.string().optional(),
  "emails": zod.array(zod.string().email().min(1)).default(accountsWorkspacesCreateBodyEmailsDefault),
  "role": zod.string().optional()
})


/**
 * Update workspace details
 */
export const AccountsWorkspacesUpdateParams = zod.object({
  "workspace_id": zod.string()
})

export const AccountsWorkspacesUpdateBody = zod.object({
  "name": zod.string().optional(),
  "display_name": zod.string().optional(),
  "description": zod.string().optional()
})





export const AccountsWorkspacesUpdateResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "workspace": zod.object({
  "id": zod.string().uuid(),
  "name": zod.string().min(1),
  "display_name": zod.string(),
  "description": zod.string().optional(),
  "is_default": zod.boolean().optional()
}),
  "message": zod.string().min(1)
})
})


/**
 * Delete a workspace
 */
export const AccountsWorkspacesDeleteParams = zod.object({
  "workspace_id": zod.string()
})




export const AccountsWorkspacesDeleteResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "message": zod.string().min(1)
})
})


/**
 * Get workspaces for the current organization
 */
export const AccountsWorkspacesReadParams = zod.object({
  "workspace_id": zod.string()
})






export const AccountsWorkspacesReadResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "organization": zod.string().min(1),
  "workspaces": zod.array(zod.object({
  "id": zod.string().uuid(),
  "name": zod.string().min(1),
  "display_name": zod.string(),
  "description": zod.string(),
  "is_default": zod.boolean(),
  "member_count": zod.number(),
  "created_at": zod.string().min(1),
  "created_by": zod.string()
})),
  "total": zod.number()
})
})


/**
 * Get members of a specific workspace
 */
export const AccountsWorkspacesMembersListParams = zod.object({
  "workspace_id": zod.string()
})








export const AccountsWorkspacesMembersListResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "workspace": zod.object({
  "id": zod.string().uuid(),
  "name": zod.string().min(1),
  "display_name": zod.string(),
  "description": zod.string().optional(),
  "is_default": zod.boolean().optional()
}),
  "members": zod.array(zod.object({
  "user_id": zod.string().uuid(),
  "email": zod.string().email().min(1),
  "name": zod.string(),
  "role": zod.string().min(1),
  "joined_at": zod.string().min(1),
  "invited_by": zod.string().min(1)
})),
  "total": zod.number()
})
})


/**
 * Add users to workspace
 */
export const AccountsWorkspacesMembersCreateParams = zod.object({
  "workspace_id": zod.string(),
  "member_id": zod.string()
})

export const AccountsWorkspacesMembersCreateBody = zod.object({
  "users": zod.array(zod.record(zod.string(), zod.string()))
})


/**
 * Remove user from workspace
 */
export const AccountsWorkspacesMembersDeleteParams = zod.object({
  "workspace_id": zod.string(),
  "member_id": zod.string()
})




export const AccountsWorkspacesMembersDeleteResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "message": zod.string().min(1)
})
})


/**
 * Get members of a specific workspace
 */
export const AccountsWorkspacesMembersReadParams = zod.object({
  "workspace_id": zod.string(),
  "member_id": zod.string()
})








export const AccountsWorkspacesMembersReadResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "workspace": zod.object({
  "id": zod.string().uuid(),
  "name": zod.string().min(1),
  "display_name": zod.string(),
  "description": zod.string().optional(),
  "is_default": zod.boolean().optional()
}),
  "members": zod.array(zod.object({
  "user_id": zod.string().uuid(),
  "email": zod.string().email().min(1),
  "name": zod.string(),
  "role": zod.string().min(1),
  "joined_at": zod.string().min(1),
  "invited_by": zod.string().min(1)
})),
  "total": zod.number()
})
})


/**
 * All execution data (inputs/outputs) organized by port.
 * @summary Returns detailed results for a specific node execution.
 */
export const AgentPlaygroundExecutionsNodeDetailParams = zod.object({
  "execution_id": zod.string(),
  "node_execution_id": zod.string()
})

export const agentPlaygroundExecutionsNodeDetailResponseStatusDefault = true;









export const AgentPlaygroundExecutionsNodeDetailResponse = zod.object({
  "status": zod.boolean().default(agentPlaygroundExecutionsNodeDetailResponseStatusDefault),
  "result": zod.object({
  "node_execution_id": zod.string().uuid().optional(),
  "node_id": zod.string().uuid().optional(),
  "node_name": zod.string().min(1).optional(),
  "node_type": zod.string().min(1).optional(),
  "status": zod.string().min(1).optional(),
  "started_at": zod.string().datetime({"offset":true}).optional(),
  "completed_at": zod.string().datetime({"offset":true}).optional(),
  "duration_seconds": zod.number().optional(),
  "error_message": zod.string().min(1).optional(),
  "inputs": zod.array(zod.object({
  "port_id": zod.string().uuid().optional(),
  "port_key": zod.string().min(1).optional(),
  "port_direction": zod.string().min(1).optional(),
  "payload": zod.object({

}).passthrough().optional(),
  "is_valid": zod.boolean().optional(),
  "validation_errors": zod.object({

}).passthrough().optional()
})).optional(),
  "outputs": zod.array(zod.object({
  "port_id": zod.string().uuid().optional(),
  "port_key": zod.string().min(1).optional(),
  "port_direction": zod.string().min(1).optional(),
  "payload": zod.object({

}).passthrough().optional(),
  "is_valid": zod.boolean().optional(),
  "validation_errors": zod.object({

}).passthrough().optional()
})).optional()
})
})


/**
 * List all graphs for the user's org/workspace.
 */
export const AgentPlaygroundGraphsListQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})









export const AgentPlaygroundGraphsListResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).optional().describe('Display name'),
  "description": zod.string().min(1).optional(),
  "is_template": zod.boolean().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional(),
  "created_by": zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).optional(),
  "email": zod.string().email().min(1).optional()
}).optional(),
  "collaborators": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).optional(),
  "email": zod.string().email().min(1).optional()
})).optional(),
  "active_version_id": zod.string().uuid().optional(),
  "active_version_number": zod.number().optional(),
  "node_count": zod.number().optional()
}))
})


/**
 * Request body: {name, description (optional)}
 * @summary Create a new graph with an empty draft version (v1).
 */
export const agentPlaygroundGraphsCreateBodyNameMax = 255;



export const AgentPlaygroundGraphsCreateBody = zod.object({
  "name": zod.string().min(1).max(agentPlaygroundGraphsCreateBodyNameMax),
  "description": zod.string().optional()
})


/**
 * Accepts a list of graph IDs. Before deleting, checks if any graph version
being deleted is referenced by nodes in graphs outside the deletion set.
If all referencing graphs are also being deleted, it's allowed; otherwise blocked.
 * @summary Bulk soft-delete graphs with reference validation.
 */




export const AgentPlaygroundGraphsBulkDeleteBody = zod.object({
  "created_by": zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).optional(),
  "email": zod.string().email().min(1).optional()
}).optional()
})


/**
 * Create a new agent playground graph from a trace's LLM spans.
 * @summary POST /agent-playground/graphs/from-trace/
 */
export const AgentPlaygroundGraphsFromTraceCreateBody = zod.object({
  "trace_id": zod.string().uuid()
})


/**
 * Accepts an optional ``version_id`` query parameter.  When provided,
only columns whose names match the exposed input ports of that
version are returned (and cells are filtered accordingly).  When
omitted the latest version (by version_number) is used.
 * @summary Get full dataset detail: info, columns, and all rows with cells.
 */
export const AgentPlaygroundGraphsDatasetReadParams = zod.object({
  "graph_id": zod.string()
})

export const agentPlaygroundGraphsDatasetReadResponseValueDefault = ``;

export const AgentPlaygroundGraphsDatasetReadResponse = zod.object({
  "value": zod.string().default(agentPlaygroundGraphsDatasetReadResponseValueDefault)
})


/**
 * Update a single cell value.
 */
export const AgentPlaygroundGraphsDatasetUpdateCellParams = zod.object({
  "graph_id": zod.string(),
  "cell_id": zod.string()
})

export const agentPlaygroundGraphsDatasetUpdateCellBodyValueDefault = ``;

export const AgentPlaygroundGraphsDatasetUpdateCellBody = zod.object({
  "value": zod.string().default(agentPlaygroundGraphsDatasetUpdateCellBodyValueDefault)
})

export const agentPlaygroundGraphsDatasetUpdateCellResponseValueDefault = ``;

export const AgentPlaygroundGraphsDatasetUpdateCellResponse = zod.object({
  "value": zod.string().default(agentPlaygroundGraphsDatasetUpdateCellResponseValueDefault)
})


/**
 * Trigger graph execution for dataset rows using the active graph version.
 */
export const AgentPlaygroundGraphsDatasetExecuteParams = zod.object({
  "graph_id": zod.string()
})

export const agentPlaygroundGraphsDatasetExecuteBodyTaskQueueDefault = `tasks_l`;



export const AgentPlaygroundGraphsDatasetExecuteBody = zod.object({
  "row_ids": zod.array(zod.string().uuid()).optional().describe('Optional list of row IDs to execute. If omitted, all rows are executed.'),
  "task_queue": zod.string().min(1).default(agentPlaygroundGraphsDatasetExecuteBodyTaskQueueDefault)
})


/**
 * Create a single row with empty cells pre-created for every column.
 */
export const AgentPlaygroundGraphsDatasetCreateRowParams = zod.object({
  "graph_id": zod.string()
})

export const agentPlaygroundGraphsDatasetCreateRowBodyValueDefault = ``;

export const AgentPlaygroundGraphsDatasetCreateRowBody = zod.object({
  "value": zod.string().default(agentPlaygroundGraphsDatasetCreateRowBodyValueDefault)
})


/**
 * Bulk delete rows by IDs.
 */
export const AgentPlaygroundGraphsDatasetRowsDeleteRowsParams = zod.object({
  "graph_id": zod.string()
})


/**
 * List all executions for a graph, with optional status filter.
 */
export const AgentPlaygroundGraphsExecutionsListParams = zod.object({
  "graph_id": zod.string()
})

export const AgentPlaygroundGraphsExecutionsListQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})

export const agentPlaygroundGraphsExecutionsListResponseStatusDefault = true;

export const AgentPlaygroundGraphsExecutionsListResponse = zod.object({
  "status": zod.boolean().default(agentPlaygroundGraphsExecutionsListResponseStatusDefault),
  "result": zod.object({
  "executions": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "status": zod.enum(['pending', 'running', 'success', 'failed', 'cancelled']).optional(),
  "started_at": zod.string().datetime({"offset":true}).optional(),
  "completed_at": zod.string().datetime({"offset":true}).optional(),
  "graph_version": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional()
})).optional(),
  "metadata": zod.object({
  "total_count": zod.number(),
  "current_page": zod.number(),
  "page_size": zod.number(),
  "total_pages": zod.number(),
  "next_page": zod.number().optional()
}).optional()
})
})


/**
 * Response includes basic execution data, the graph version DAG
(nodes with ports, edges), and each node's execution status.
Subgraph nodes include a nested ``sub_graph`` with their inner
graph version and execution details (recursive).
 * @summary Returns the full Graph execution detail.
 */
export const AgentPlaygroundGraphsExecutionsReadParams = zod.object({
  "graph_id": zod.string(),
  "execution_id": zod.string()
})

export const agentPlaygroundGraphsExecutionsReadResponseStatusDefault = true;


export const AgentPlaygroundGraphsExecutionsReadResponse = zod.object({
  "status": zod.boolean().default(agentPlaygroundGraphsExecutionsReadResponseStatusDefault),
  "result": zod.object({
  "id": zod.string().uuid().optional(),
  "status": zod.enum(['pending', 'running', 'success', 'failed', 'cancelled']).optional(),
  "input_payload": zod.object({

}).passthrough().optional(),
  "output_payload": zod.object({

}).passthrough().optional(),
  "started_at": zod.string().datetime({"offset":true}).optional(),
  "completed_at": zod.string().datetime({"offset":true}).optional(),
  "error_message": zod.string().min(1).optional(),
  "nodes": zod.array(zod.object({

}).passthrough()).optional(),
  "node_connections": zod.array(zod.object({

}).passthrough()).optional()
})
})


/**
 * Returns full nested structure: nodesâ†’ports, edges.
 * @summary Get graph detail with the active version expanded (or latest draft if no active).
 */
export const AgentPlaygroundGraphsReadParams = zod.object({
  "id": zod.string()
})





export const AgentPlaygroundGraphsReadResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).optional().describe('Display name'),
  "description": zod.string().min(1).optional(),
  "is_template": zod.boolean().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional(),
  "active_version": zod.string().optional()
})


/**
 * ViewSet for Graph CRUD and version management.
 */
export const AgentPlaygroundGraphsUpdateParams = zod.object({
  "id": zod.string()
})

export const agentPlaygroundGraphsUpdateBodyNameMax = 255;



export const AgentPlaygroundGraphsUpdateBody = zod.object({
  "name": zod.string().min(1).max(agentPlaygroundGraphsUpdateBodyNameMax).optional(),
  "description": zod.string().optional()
})

export const agentPlaygroundGraphsUpdateResponseNameMax = 255;



export const AgentPlaygroundGraphsUpdateResponse = zod.object({
  "name": zod.string().min(1).max(agentPlaygroundGraphsUpdateResponseNameMax).optional(),
  "description": zod.string().optional()
})


/**
 * ViewSet for Graph CRUD and version management.
 */
export const AgentPlaygroundGraphsPartialUpdateParams = zod.object({
  "id": zod.string()
})

export const agentPlaygroundGraphsPartialUpdateBodyNameMax = 255;



export const AgentPlaygroundGraphsPartialUpdateBody = zod.object({
  "name": zod.string().min(1).max(agentPlaygroundGraphsPartialUpdateBodyNameMax).optional(),
  "description": zod.string().optional()
})

export const agentPlaygroundGraphsPartialUpdateResponseNameMax = 255;



export const AgentPlaygroundGraphsPartialUpdateResponse = zod.object({
  "name": zod.string().min(1).max(agentPlaygroundGraphsPartialUpdateResponseNameMax).optional(),
  "description": zod.string().optional()
})


/**
 * Soft-delete a graph through the router detail route with cascade validation.
 */
export const AgentPlaygroundGraphsDeleteParams = zod.object({
  "id": zod.string()
})


/**
 * Returns non-template graphs whose non-draft versions (active or inactive)
can be used as `ref_graph_version` without creating a cycle.
 */
export const AgentPlaygroundGraphsReferenceableGraphsParams = zod.object({
  "id": zod.string()
})









export const AgentPlaygroundGraphsReferenceableGraphsResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).optional().describe('Display name'),
  "description": zod.string().min(1).optional(),
  "is_template": zod.boolean().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional(),
  "created_by": zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).optional(),
  "email": zod.string().email().min(1).optional()
}).optional(),
  "collaborators": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).optional(),
  "email": zod.string().email().min(1).optional()
})).optional(),
  "active_version_id": zod.string().uuid().optional(),
  "active_version_number": zod.number().optional(),
  "node_count": zod.number().optional()
})


/**
 * Get a specific version with full nested structure (nodesâ†’ports, edges).
 */
export const AgentPlaygroundGraphsVersionsReadParams = zod.object({
  "id": zod.string(),
  "version_id": zod.string()
})

export const AgentPlaygroundGraphsVersionsReadQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})









export const AgentPlaygroundGraphsVersionsReadResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).optional().describe('Display name'),
  "description": zod.string().min(1).optional(),
  "is_template": zod.boolean().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional(),
  "created_by": zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).optional(),
  "email": zod.string().email().min(1).optional()
}).optional(),
  "collaborators": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).optional(),
  "email": zod.string().email().min(1).optional()
})).optional(),
  "active_version_id": zod.string().uuid().optional(),
  "active_version_number": zod.number().optional(),
  "node_count": zod.number().optional()
}))
})


/**
 * Create a new draft version (version_number = max + 1) with optional nodes and edges.
 */
export const AgentPlaygroundGraphsVersionsCreateParams = zod.object({
  "id": zod.string()
})





export const AgentPlaygroundGraphsVersionsCreateBody = zod.object({
  "created_by": zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).optional(),
  "email": zod.string().email().min(1).optional()
}).optional()
})


/**
 * Updates commit_message and/or promotes draft â†’ active.
Content changes (nodes, ports, edges) are done via granular CRUD or create_version.
 * @summary Metadata-only update endpoint (PUT/PATCH).
 */
export const AgentPlaygroundGraphsVersionsUpdateParams = zod.object({
  "id": zod.string(),
  "version_id": zod.string()
})





export const AgentPlaygroundGraphsVersionsUpdateBody = zod.object({
  "created_by": zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).optional(),
  "email": zod.string().email().min(1).optional()
}).optional()
})









export const AgentPlaygroundGraphsVersionsUpdateResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).optional().describe('Display name'),
  "description": zod.string().min(1).optional(),
  "is_template": zod.boolean().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional(),
  "created_by": zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).optional(),
  "email": zod.string().email().min(1).optional()
}).optional(),
  "collaborators": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).optional(),
  "email": zod.string().email().min(1).optional()
})).optional(),
  "active_version_id": zod.string().uuid().optional(),
  "active_version_number": zod.number().optional(),
  "node_count": zod.number().optional()
})


/**
 * Updates commit_message and/or promotes draft â†’ active.
Content changes (nodes, ports, edges) are done via granular CRUD or create_version.
 * @summary Metadata-only update endpoint (PUT/PATCH).
 */
export const AgentPlaygroundGraphsVersionsPartialUpdateParams = zod.object({
  "id": zod.string(),
  "version_id": zod.string()
})





export const AgentPlaygroundGraphsVersionsPartialUpdateBody = zod.object({
  "created_by": zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).optional(),
  "email": zod.string().email().min(1).optional()
}).optional()
})









export const AgentPlaygroundGraphsVersionsPartialUpdateResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).optional().describe('Display name'),
  "description": zod.string().min(1).optional(),
  "is_template": zod.boolean().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional(),
  "created_by": zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).optional(),
  "email": zod.string().email().min(1).optional()
}).optional(),
  "collaborators": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).optional(),
  "email": zod.string().email().min(1).optional()
})).optional(),
  "active_version_id": zod.string().uuid().optional(),
  "active_version_number": zod.number().optional(),
  "node_count": zod.number().optional()
})


/**
 * Cannot delete if this is the only version for the graph.
Can delete active version - graph will then have no active version.
 * @summary Soft-delete a specific version and its content (nodes, ports, edges).
 */
export const AgentPlaygroundGraphsVersionsDeleteParams = zod.object({
  "id": zod.string(),
  "version_id": zod.string()
})


/**
 * Promote an inactive version to active.
The currently active version (if any) is set to inactive.
 * @summary POST /graphs/{graph_id}/versions/{version_id}/activate/
 */
export const AgentPlaygroundGraphsVersionsActivateVersionParams = zod.object({
  "id": zod.string(),
  "version_id": zod.string()
})





export const AgentPlaygroundGraphsVersionsActivateVersionBody = zod.object({
  "created_by": zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).optional(),
  "email": zod.string().email().min(1).optional()
}).optional()
})


/**
 * POST /graphs/{pk}/versions/{version_id}/node-connections/
 */
export const AgentPlaygroundGraphsVersionsNodeConnectionsCreateParams = zod.object({
  "id": zod.string(),
  "version_id": zod.string()
})

export const AgentPlaygroundGraphsVersionsNodeConnectionsCreateBody = zod.object({
  "id": zod.string().uuid().describe('FE-generated UUID'),
  "source_node_id": zod.string().uuid(),
  "target_node_id": zod.string().uuid()
})


/**
 * DELETE /graphs/{pk}/versions/{version_id}/node-connections/{nc_id}/
 */
export const AgentPlaygroundGraphsVersionsNodeConnectionsDeleteParams = zod.object({
  "id": zod.string(),
  "version_id": zod.string(),
  "nc_id": zod.string()
})


/**
 * POST /graphs/{pk}/versions/{version_id}/nodes/
 */
export const AgentPlaygroundGraphsVersionsNodesCreateParams = zod.object({
  "id": zod.string(),
  "version_id": zod.string()
})

export const agentPlaygroundGraphsVersionsNodesCreateBodyNameMax = 255;

export const agentPlaygroundGraphsVersionsNodesCreateBodyPositionDefault = {  };




export const agentPlaygroundGraphsVersionsNodesCreateBodyPromptTemplateResponseFormatDefault = `text`;
export const agentPlaygroundGraphsVersionsNodesCreateBodyPromptTemplateSavePromptVersionDefault = false;
export const agentPlaygroundGraphsVersionsNodesCreateBodyPortsItemKeyMax = 100;

export const agentPlaygroundGraphsVersionsNodesCreateBodyPortsItemDisplayNameMax = 100;

export const agentPlaygroundGraphsVersionsNodesCreateBodyPortsItemDataSchemaDefault = {  };
export const agentPlaygroundGraphsVersionsNodesCreateBodyPortsDefault = [];

export const agentPlaygroundGraphsVersionsNodesCreateBodyInputMappingsDefault = [];

export const AgentPlaygroundGraphsVersionsNodesCreateBody = zod.object({
  "id": zod.string().uuid().describe('FE-generated UUID for the node'),
  "type": zod.enum(['subgraph', 'atomic']),
  "name": zod.string().min(1).max(agentPlaygroundGraphsVersionsNodesCreateBodyNameMax),
  "node_template_id": zod.string().uuid().optional(),
  "ref_graph_version_id": zod.string().uuid().optional(),
  "position": zod.object({

}).passthrough().default(agentPlaygroundGraphsVersionsNodesCreateBodyPositionDefault),
  "source_node_id": zod.string().uuid().optional(),
  "prompt_template": zod.object({
  "prompt_template_id": zod.string().uuid().optional(),
  "prompt_version_id": zod.string().uuid().optional(),
  "messages": zod.array(zod.object({
  "id": zod.string().min(1).describe('Unique identifier for the message (frontend-provided)'),
  "role": zod.string().min(1).describe('Message role (e.g., \'system\', \'user\', \'assistant\')'),
  "content": zod.array(zod.object({
  "type": zod.enum(['text', 'image_url', 'audio_url', 'pdf_url']).describe('Type of content item'),
  "text": zod.string().optional().describe('Text content (required when type=text)'),
  "image_url": zod.string().url().min(1).optional().describe('Image URL (required when type=image_url)'),
  "audio_url": zod.string().url().min(1).optional().describe('Audio URL (required when type=audio_url)'),
  "pdf_url": zod.string().url().min(1).optional().describe('PDF URL (required when type=pdf_url)')
}).describe('Array of content items')).describe('Array of content items')
}).describe('Array of message objects with id, role, and content array')).describe('Array of message objects with id, role, and content array'),
  "response_format": zod.union([zod.string(), zod.object({}).passthrough()]).default(agentPlaygroundGraphsVersionsNodesCreateBodyPromptTemplateResponseFormatDefault).describe('String or JSON object.'),
  "response_schema": zod.object({

}).passthrough().optional().describe('JSON Schema (Draft 7) for structured outputs. Required when response_format=\'json_schema\'. Example: {\'type\': \'object\', \'properties\': {...}, \'required\': [...]}'),
  "model": zod.string().optional(),
  "temperature": zod.number().optional(),
  "max_tokens": zod.number().optional(),
  "top_p": zod.number().optional(),
  "frequency_penalty": zod.number().optional(),
  "presence_penalty": zod.number().optional(),
  "output_format": zod.string().optional(),
  "tools": zod.array(zod.record(zod.string(), zod.string())).optional(),
  "tool_choice": zod.object({

}).passthrough().optional(),
  "model_detail": zod.record(zod.string(), zod.string()).optional(),
  "variable_names": zod.record(zod.string(), zod.string()).optional(),
  "metadata": zod.record(zod.string(), zod.string()).optional(),
  "commit_message": zod.string().optional(),
  "template_format": zod.string().optional().describe('Template format: \'mustache\' or \'jinja\''),
  "save_prompt_version": zod.boolean().default(agentPlaygroundGraphsVersionsNodesCreateBodyPromptTemplateSavePromptVersionDefault)
}).optional(),
  "ports": zod.array(zod.object({
  "id": zod.string().uuid().describe('FE-generated UUID'),
  "key": zod.string().min(1).max(agentPlaygroundGraphsVersionsNodesCreateBodyPortsItemKeyMax),
  "display_name": zod.string().min(1).max(agentPlaygroundGraphsVersionsNodesCreateBodyPortsItemDisplayNameMax),
  "direction": zod.enum(['input', 'output']),
  "data_schema": zod.object({

}).passthrough().default(agentPlaygroundGraphsVersionsNodesCreateBodyPortsItemDataSchemaDefault),
  "ref_port_id": zod.string().uuid().optional()
})).default(agentPlaygroundGraphsVersionsNodesCreateBodyPortsDefault),
  "input_mappings": zod.array(zod.object({
  "key": zod.string().min(1).describe('Input port display_name'),
  "value": zod.string().min(1).optional().describe('Source reference in format \"NodeName.port_display_name\" or null')
}).describe('List of input mappings from port display_name to source reference')).default(agentPlaygroundGraphsVersionsNodesCreateBodyInputMappingsDefault).describe('List of input mappings from port display_name to source reference')
})


/**
 * GET /graphs/{pk}/versions/{version_id}/nodes/{node_id}/
 */
export const AgentPlaygroundGraphsVersionsNodesReadParams = zod.object({
  "id": zod.string(),
  "version_id": zod.string(),
  "node_id": zod.string()
})







export const AgentPlaygroundGraphsVersionsNodesReadResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "type": zod.enum(['subgraph', 'atomic']).optional().describe('\'subgraph\' for subgraph nodes, \'atomic\' for nodes using a NodeTemplate'),
  "name": zod.string().min(1).optional().describe('Display name'),
  "config": zod.object({

}).passthrough().optional().describe('Node-specific configuration (validated against node_template.config_schema for atomic nodes)'),
  "position": zod.object({

}).passthrough().optional().describe('UI coordinates {\"x\": 0, \"y\": 0}'),
  "node_template_id": zod.string().uuid().optional(),
  "ref_graph_version_id": zod.string().uuid().optional(),
  "ref_graph_name": zod.string().min(1).optional(),
  "ref_graph_id": zod.string().uuid().optional(),
  "prompt_template": zod.string().optional(),
  "node_connection": zod.string().optional(),
  "input_mappings": zod.string().optional(),
  "ports": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "key": zod.string().min(1).optional().describe('Identifier (e.g., \'prompt\', \'result\')'),
  "display_name": zod.string().min(1).optional().describe('User-facing name for the port'),
  "direction": zod.enum(['input', 'output']).optional(),
  "data_schema": zod.object({

}).passthrough().optional().describe('JSON Schema for validation'),
  "required": zod.boolean().optional(),
  "default_value": zod.object({

}).passthrough().optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "ref_port_id": zod.string().uuid().optional()
})).optional()
})


/**
 * PATCH /graphs/{pk}/versions/{version_id}/nodes/{node_id}/
 */
export const AgentPlaygroundGraphsVersionsNodesPartialUpdateParams = zod.object({
  "id": zod.string(),
  "version_id": zod.string(),
  "node_id": zod.string()
})

export const agentPlaygroundGraphsVersionsNodesPartialUpdateBodyNameMax = 255;






export const agentPlaygroundGraphsVersionsNodesPartialUpdateBodyPromptTemplateResponseFormatDefault = `text`;
export const agentPlaygroundGraphsVersionsNodesPartialUpdateBodyPromptTemplateSavePromptVersionDefault = false;

export const agentPlaygroundGraphsVersionsNodesPartialUpdateBodyPortsItemKeyMax = 100;

export const agentPlaygroundGraphsVersionsNodesPartialUpdateBodyPortsItemDisplayNameMax = 100;

export const agentPlaygroundGraphsVersionsNodesPartialUpdateBodyPortsItemDataSchemaDefault = {  };

export const AgentPlaygroundGraphsVersionsNodesPartialUpdateBody = zod.object({
  "name": zod.string().min(1).max(agentPlaygroundGraphsVersionsNodesPartialUpdateBodyNameMax).optional(),
  "position": zod.object({

}).passthrough().optional(),
  "prompt_template": zod.object({
  "prompt_template_id": zod.string().uuid().optional(),
  "prompt_version_id": zod.string().uuid().optional(),
  "messages": zod.array(zod.object({
  "id": zod.string().min(1).describe('Unique identifier for the message (frontend-provided)'),
  "role": zod.string().min(1).describe('Message role (e.g., \'system\', \'user\', \'assistant\')'),
  "content": zod.array(zod.object({
  "type": zod.enum(['text', 'image_url', 'audio_url', 'pdf_url']).describe('Type of content item'),
  "text": zod.string().optional().describe('Text content (required when type=text)'),
  "image_url": zod.string().url().min(1).optional().describe('Image URL (required when type=image_url)'),
  "audio_url": zod.string().url().min(1).optional().describe('Audio URL (required when type=audio_url)'),
  "pdf_url": zod.string().url().min(1).optional().describe('PDF URL (required when type=pdf_url)')
}).describe('Array of content items')).describe('Array of content items')
}).describe('Array of message objects with id, role, and content array')).describe('Array of message objects with id, role, and content array'),
  "response_format": zod.union([zod.string(), zod.object({}).passthrough()]).default(agentPlaygroundGraphsVersionsNodesPartialUpdateBodyPromptTemplateResponseFormatDefault).describe('String or JSON object.'),
  "response_schema": zod.object({

}).passthrough().optional().describe('JSON Schema (Draft 7) for structured outputs. Required when response_format=\'json_schema\'. Example: {\'type\': \'object\', \'properties\': {...}, \'required\': [...]}'),
  "model": zod.string().optional(),
  "temperature": zod.number().optional(),
  "max_tokens": zod.number().optional(),
  "top_p": zod.number().optional(),
  "frequency_penalty": zod.number().optional(),
  "presence_penalty": zod.number().optional(),
  "output_format": zod.string().optional(),
  "tools": zod.array(zod.record(zod.string(), zod.string())).optional(),
  "tool_choice": zod.object({

}).passthrough().optional(),
  "model_detail": zod.record(zod.string(), zod.string()).optional(),
  "variable_names": zod.record(zod.string(), zod.string()).optional(),
  "metadata": zod.record(zod.string(), zod.string()).optional(),
  "commit_message": zod.string().optional(),
  "template_format": zod.string().optional().describe('Template format: \'mustache\' or \'jinja\''),
  "save_prompt_version": zod.boolean().default(agentPlaygroundGraphsVersionsNodesPartialUpdateBodyPromptTemplateSavePromptVersionDefault)
}).optional(),
  "ref_graph_version_id": zod.string().uuid().optional(),
  "input_mappings": zod.array(zod.object({
  "key": zod.string().min(1).describe('Input port display_name'),
  "value": zod.string().min(1).optional().describe('Source reference in format \"NodeName.port_display_name\" or null')
}).describe('List of input mappings from port display_name to source reference')).optional().describe('List of input mappings from port display_name to source reference'),
  "ports": zod.array(zod.object({
  "id": zod.string().uuid().describe('FE-generated UUID'),
  "key": zod.string().min(1).max(agentPlaygroundGraphsVersionsNodesPartialUpdateBodyPortsItemKeyMax),
  "display_name": zod.string().min(1).max(agentPlaygroundGraphsVersionsNodesPartialUpdateBodyPortsItemDisplayNameMax),
  "direction": zod.enum(['input', 'output']),
  "data_schema": zod.object({

}).passthrough().default(agentPlaygroundGraphsVersionsNodesPartialUpdateBodyPortsItemDataSchemaDefault),
  "ref_port_id": zod.string().uuid().optional()
})).optional().describe('Replace all OUTPUT ports with this new set (input ports preserved)')
})

export const agentPlaygroundGraphsVersionsNodesPartialUpdateResponseNameMax = 255;






export const agentPlaygroundGraphsVersionsNodesPartialUpdateResponsePromptTemplateResponseFormatDefault = `text`;
export const agentPlaygroundGraphsVersionsNodesPartialUpdateResponsePromptTemplateSavePromptVersionDefault = false;

export const agentPlaygroundGraphsVersionsNodesPartialUpdateResponsePortsItemKeyMax = 100;

export const agentPlaygroundGraphsVersionsNodesPartialUpdateResponsePortsItemDisplayNameMax = 100;

export const agentPlaygroundGraphsVersionsNodesPartialUpdateResponsePortsItemDataSchemaDefault = {  };

export const AgentPlaygroundGraphsVersionsNodesPartialUpdateResponse = zod.object({
  "name": zod.string().min(1).max(agentPlaygroundGraphsVersionsNodesPartialUpdateResponseNameMax).optional(),
  "position": zod.object({

}).passthrough().optional(),
  "prompt_template": zod.object({
  "prompt_template_id": zod.string().uuid().optional(),
  "prompt_version_id": zod.string().uuid().optional(),
  "messages": zod.array(zod.object({
  "id": zod.string().min(1).describe('Unique identifier for the message (frontend-provided)'),
  "role": zod.string().min(1).describe('Message role (e.g., \'system\', \'user\', \'assistant\')'),
  "content": zod.array(zod.object({
  "type": zod.enum(['text', 'image_url', 'audio_url', 'pdf_url']).describe('Type of content item'),
  "text": zod.string().optional().describe('Text content (required when type=text)'),
  "image_url": zod.string().url().min(1).optional().describe('Image URL (required when type=image_url)'),
  "audio_url": zod.string().url().min(1).optional().describe('Audio URL (required when type=audio_url)'),
  "pdf_url": zod.string().url().min(1).optional().describe('PDF URL (required when type=pdf_url)')
}).describe('Array of content items')).describe('Array of content items')
}).describe('Array of message objects with id, role, and content array')).describe('Array of message objects with id, role, and content array'),
  "response_format": zod.union([zod.string(), zod.object({}).passthrough()]).default(agentPlaygroundGraphsVersionsNodesPartialUpdateResponsePromptTemplateResponseFormatDefault).describe('String or JSON object.'),
  "response_schema": zod.object({

}).passthrough().optional().describe('JSON Schema (Draft 7) for structured outputs. Required when response_format=\'json_schema\'. Example: {\'type\': \'object\', \'properties\': {...}, \'required\': [...]}'),
  "model": zod.string().optional(),
  "temperature": zod.number().optional(),
  "max_tokens": zod.number().optional(),
  "top_p": zod.number().optional(),
  "frequency_penalty": zod.number().optional(),
  "presence_penalty": zod.number().optional(),
  "output_format": zod.string().optional(),
  "tools": zod.array(zod.record(zod.string(), zod.string())).optional(),
  "tool_choice": zod.object({

}).passthrough().optional(),
  "model_detail": zod.record(zod.string(), zod.string()).optional(),
  "variable_names": zod.record(zod.string(), zod.string()).optional(),
  "metadata": zod.record(zod.string(), zod.string()).optional(),
  "commit_message": zod.string().optional(),
  "template_format": zod.string().optional().describe('Template format: \'mustache\' or \'jinja\''),
  "save_prompt_version": zod.boolean().default(agentPlaygroundGraphsVersionsNodesPartialUpdateResponsePromptTemplateSavePromptVersionDefault)
}).optional(),
  "ref_graph_version_id": zod.string().uuid().optional(),
  "input_mappings": zod.array(zod.object({
  "key": zod.string().min(1).describe('Input port display_name'),
  "value": zod.string().min(1).optional().describe('Source reference in format \"NodeName.port_display_name\" or null')
}).describe('List of input mappings from port display_name to source reference')).optional().describe('List of input mappings from port display_name to source reference'),
  "ports": zod.array(zod.object({
  "id": zod.string().uuid().describe('FE-generated UUID'),
  "key": zod.string().min(1).max(agentPlaygroundGraphsVersionsNodesPartialUpdateResponsePortsItemKeyMax),
  "display_name": zod.string().min(1).max(agentPlaygroundGraphsVersionsNodesPartialUpdateResponsePortsItemDisplayNameMax),
  "direction": zod.enum(['input', 'output']),
  "data_schema": zod.object({

}).passthrough().default(agentPlaygroundGraphsVersionsNodesPartialUpdateResponsePortsItemDataSchemaDefault),
  "ref_port_id": zod.string().uuid().optional()
})).optional().describe('Replace all OUTPUT ports with this new set (input ports preserved)')
})


/**
 * DELETE /graphs/{pk}/versions/{version_id}/nodes/{node_id}/
 */
export const AgentPlaygroundGraphsVersionsNodesDeleteParams = zod.object({
  "id": zod.string(),
  "version_id": zod.string(),
  "node_id": zod.string()
})


/**
 * Returns all source nodes that have NodeConnections targeting this node,
along with their output ports. This helps the frontend build UI for
creating edges between specific ports.
 * @summary GET /graphs/{pk}/versions/{version_id}/nodes/{node_id}/possible-edge-mappings/
 */
export const AgentPlaygroundGraphsVersionsNodesPossibleEdgeMappingsParams = zod.object({
  "id": zod.string(),
  "version_id": zod.string(),
  "node_id": zod.string()
})

export const AgentPlaygroundGraphsVersionsNodesPossibleEdgeMappingsQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})







export const AgentPlaygroundGraphsVersionsNodesPossibleEdgeMappingsResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "type": zod.enum(['subgraph', 'atomic']).optional().describe('\'subgraph\' for subgraph nodes, \'atomic\' for nodes using a NodeTemplate'),
  "name": zod.string().min(1).optional().describe('Display name'),
  "config": zod.object({

}).passthrough().optional().describe('Node-specific configuration (validated against node_template.config_schema for atomic nodes)'),
  "position": zod.object({

}).passthrough().optional().describe('UI coordinates {\"x\": 0, \"y\": 0}'),
  "node_template_id": zod.string().uuid().optional(),
  "ref_graph_version_id": zod.string().uuid().optional(),
  "ref_graph_name": zod.string().min(1).optional(),
  "ref_graph_id": zod.string().uuid().optional(),
  "prompt_template": zod.string().optional(),
  "node_connection": zod.string().optional(),
  "input_mappings": zod.string().optional(),
  "ports": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "key": zod.string().min(1).optional().describe('Identifier (e.g., \'prompt\', \'result\')'),
  "display_name": zod.string().min(1).optional().describe('User-facing name for the port'),
  "direction": zod.enum(['input', 'output']).optional(),
  "data_schema": zod.object({

}).passthrough().optional().describe('JSON Schema for validation'),
  "required": zod.boolean().optional(),
  "default_value": zod.object({

}).passthrough().optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "ref_port_id": zod.string().uuid().optional()
})).optional()
}))
})


/**
 * PATCH /graphs/{pk}/versions/{version_id}/ports/{port_id}/
 */
export const AgentPlaygroundGraphsVersionsPortsPartialUpdateParams = zod.object({
  "id": zod.string(),
  "version_id": zod.string(),
  "port_id": zod.string()
})

export const agentPlaygroundGraphsVersionsPortsPartialUpdateBodyDisplayNameMax = 100;



export const AgentPlaygroundGraphsVersionsPortsPartialUpdateBody = zod.object({
  "display_name": zod.string().min(1).max(agentPlaygroundGraphsVersionsPortsPartialUpdateBodyDisplayNameMax)
})

export const agentPlaygroundGraphsVersionsPortsPartialUpdateResponseDisplayNameMax = 100;



export const AgentPlaygroundGraphsVersionsPortsPartialUpdateResponse = zod.object({
  "display_name": zod.string().min(1).max(agentPlaygroundGraphsVersionsPortsPartialUpdateResponseDisplayNameMax)
})


/**
 * Returns lightweight representation: id, name, display_name, description, icon, categories.
 * @summary List all node templates.
 */
export const AgentPlaygroundNodeTemplatesListQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})







export const AgentPlaygroundNodeTemplatesListResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).optional(),
  "display_name": zod.string().min(1).optional(),
  "description": zod.string().min(1).optional(),
  "icon": zod.string().url().min(1).optional(),
  "categories": zod.object({

}).passthrough().optional()
}))
})


/**
 * Returns full template detail: + input_definition, output_definition,
input_mode, output_mode, config_schema.
 * @summary Get a single node template with full details.
 */
export const AgentPlaygroundNodeTemplatesReadParams = zod.object({
  "id": zod.string()
})







export const AgentPlaygroundNodeTemplatesReadResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).optional(),
  "display_name": zod.string().min(1).optional(),
  "description": zod.string().min(1).optional(),
  "icon": zod.string().url().min(1).optional(),
  "categories": zod.object({

}).passthrough().optional(),
  "input_definition": zod.object({

}).passthrough().optional(),
  "output_definition": zod.object({

}).passthrough().optional(),
  "input_mode": zod.enum(['strict', 'extensible', 'dynamic']).optional(),
  "output_mode": zod.enum(['strict', 'extensible', 'dynamic']).optional(),
  "config_schema": zod.object({

}).passthrough().optional().describe('JSON Schema for Node.config validation')
})


/**
 * Cost breakdown by dimension.
 */
export const AgentccAnalyticsCostBreakdownQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})












export const AgentccAnalyticsCostBreakdownResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "request_id": zod.string().min(1).optional(),
  "model": zod.string().min(1).optional(),
  "provider": zod.string().min(1).optional(),
  "resolved_model": zod.string().min(1).optional(),
  "latency_ms": zod.number().optional(),
  "started_at": zod.string().datetime({"offset":true}).optional(),
  "input_tokens": zod.number().optional(),
  "output_tokens": zod.number().optional(),
  "total_tokens": zod.number().optional(),
  "cost": zod.string().optional(),
  "status_code": zod.number().optional(),
  "is_stream": zod.boolean().optional(),
  "is_error": zod.boolean().optional(),
  "error_message": zod.string().min(1).optional(),
  "cache_hit": zod.boolean().optional(),
  "fallback_used": zod.boolean().optional(),
  "guardrail_triggered": zod.boolean().optional(),
  "api_key_id": zod.string().min(1).optional(),
  "user_id": zod.string().min(1).optional(),
  "session_id": zod.string().min(1).optional(),
  "routing_strategy": zod.string().min(1).optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "organization": zod.string().uuid().optional(),
  "workspace": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional()
}))
})


/**
 * Error analysis with breakdown and timeseries.
 */
export const AgentccAnalyticsErrorBreakdownQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})












export const AgentccAnalyticsErrorBreakdownResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "request_id": zod.string().min(1).optional(),
  "model": zod.string().min(1).optional(),
  "provider": zod.string().min(1).optional(),
  "resolved_model": zod.string().min(1).optional(),
  "latency_ms": zod.number().optional(),
  "started_at": zod.string().datetime({"offset":true}).optional(),
  "input_tokens": zod.number().optional(),
  "output_tokens": zod.number().optional(),
  "total_tokens": zod.number().optional(),
  "cost": zod.string().optional(),
  "status_code": zod.number().optional(),
  "is_stream": zod.boolean().optional(),
  "is_error": zod.boolean().optional(),
  "error_message": zod.string().min(1).optional(),
  "cache_hit": zod.boolean().optional(),
  "fallback_used": zod.boolean().optional(),
  "guardrail_triggered": zod.boolean().optional(),
  "api_key_id": zod.string().min(1).optional(),
  "user_id": zod.string().min(1).optional(),
  "session_id": zod.string().min(1).optional(),
  "routing_strategy": zod.string().min(1).optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "organization": zod.string().uuid().optional(),
  "workspace": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional()
}))
})


/**
 * Guardrail aggregate KPIs.
 */
export const AgentccAnalyticsGuardrailOverviewQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})












export const AgentccAnalyticsGuardrailOverviewResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "request_id": zod.string().min(1).optional(),
  "model": zod.string().min(1).optional(),
  "provider": zod.string().min(1).optional(),
  "resolved_model": zod.string().min(1).optional(),
  "latency_ms": zod.number().optional(),
  "started_at": zod.string().datetime({"offset":true}).optional(),
  "input_tokens": zod.number().optional(),
  "output_tokens": zod.number().optional(),
  "total_tokens": zod.number().optional(),
  "cost": zod.string().optional(),
  "status_code": zod.number().optional(),
  "is_stream": zod.boolean().optional(),
  "is_error": zod.boolean().optional(),
  "error_message": zod.string().min(1).optional(),
  "cache_hit": zod.boolean().optional(),
  "fallback_used": zod.boolean().optional(),
  "guardrail_triggered": zod.boolean().optional(),
  "api_key_id": zod.string().min(1).optional(),
  "user_id": zod.string().min(1).optional(),
  "session_id": zod.string().min(1).optional(),
  "routing_strategy": zod.string().min(1).optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "organization": zod.string().uuid().optional(),
  "workspace": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional()
}))
})


/**
 * Per-rule guardrail trigger breakdown.
 */
export const AgentccAnalyticsGuardrailRulesQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})












export const AgentccAnalyticsGuardrailRulesResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "request_id": zod.string().min(1).optional(),
  "model": zod.string().min(1).optional(),
  "provider": zod.string().min(1).optional(),
  "resolved_model": zod.string().min(1).optional(),
  "latency_ms": zod.number().optional(),
  "started_at": zod.string().datetime({"offset":true}).optional(),
  "input_tokens": zod.number().optional(),
  "output_tokens": zod.number().optional(),
  "total_tokens": zod.number().optional(),
  "cost": zod.string().optional(),
  "status_code": zod.number().optional(),
  "is_stream": zod.boolean().optional(),
  "is_error": zod.boolean().optional(),
  "error_message": zod.string().min(1).optional(),
  "cache_hit": zod.boolean().optional(),
  "fallback_used": zod.boolean().optional(),
  "guardrail_triggered": zod.boolean().optional(),
  "api_key_id": zod.string().min(1).optional(),
  "user_id": zod.string().min(1).optional(),
  "session_id": zod.string().min(1).optional(),
  "routing_strategy": zod.string().min(1).optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "organization": zod.string().uuid().optional(),
  "workspace": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional()
}))
})


/**
 * Time-bucketed guardrail trigger trends.
 */
export const AgentccAnalyticsGuardrailTrendsQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})












export const AgentccAnalyticsGuardrailTrendsResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "request_id": zod.string().min(1).optional(),
  "model": zod.string().min(1).optional(),
  "provider": zod.string().min(1).optional(),
  "resolved_model": zod.string().min(1).optional(),
  "latency_ms": zod.number().optional(),
  "started_at": zod.string().datetime({"offset":true}).optional(),
  "input_tokens": zod.number().optional(),
  "output_tokens": zod.number().optional(),
  "total_tokens": zod.number().optional(),
  "cost": zod.string().optional(),
  "status_code": zod.number().optional(),
  "is_stream": zod.boolean().optional(),
  "is_error": zod.boolean().optional(),
  "error_message": zod.string().min(1).optional(),
  "cache_hit": zod.boolean().optional(),
  "fallback_used": zod.boolean().optional(),
  "guardrail_triggered": zod.boolean().optional(),
  "api_key_id": zod.string().min(1).optional(),
  "user_id": zod.string().min(1).optional(),
  "session_id": zod.string().min(1).optional(),
  "routing_strategy": zod.string().min(1).optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "organization": zod.string().uuid().optional(),
  "workspace": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional()
}))
})


/**
 * Latency percentiles and timeseries.
 */
export const AgentccAnalyticsLatencyStatsQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})












export const AgentccAnalyticsLatencyStatsResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "request_id": zod.string().min(1).optional(),
  "model": zod.string().min(1).optional(),
  "provider": zod.string().min(1).optional(),
  "resolved_model": zod.string().min(1).optional(),
  "latency_ms": zod.number().optional(),
  "started_at": zod.string().datetime({"offset":true}).optional(),
  "input_tokens": zod.number().optional(),
  "output_tokens": zod.number().optional(),
  "total_tokens": zod.number().optional(),
  "cost": zod.string().optional(),
  "status_code": zod.number().optional(),
  "is_stream": zod.boolean().optional(),
  "is_error": zod.boolean().optional(),
  "error_message": zod.string().min(1).optional(),
  "cache_hit": zod.boolean().optional(),
  "fallback_used": zod.boolean().optional(),
  "guardrail_triggered": zod.boolean().optional(),
  "api_key_id": zod.string().min(1).optional(),
  "user_id": zod.string().min(1).optional(),
  "session_id": zod.string().min(1).optional(),
  "routing_strategy": zod.string().min(1).optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "organization": zod.string().uuid().optional(),
  "workspace": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional()
}))
})


/**
 * Side-by-side model performance comparison.
 */
export const AgentccAnalyticsModelComparisonQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})












export const AgentccAnalyticsModelComparisonResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "request_id": zod.string().min(1).optional(),
  "model": zod.string().min(1).optional(),
  "provider": zod.string().min(1).optional(),
  "resolved_model": zod.string().min(1).optional(),
  "latency_ms": zod.number().optional(),
  "started_at": zod.string().datetime({"offset":true}).optional(),
  "input_tokens": zod.number().optional(),
  "output_tokens": zod.number().optional(),
  "total_tokens": zod.number().optional(),
  "cost": zod.string().optional(),
  "status_code": zod.number().optional(),
  "is_stream": zod.boolean().optional(),
  "is_error": zod.boolean().optional(),
  "error_message": zod.string().min(1).optional(),
  "cache_hit": zod.boolean().optional(),
  "fallback_used": zod.boolean().optional(),
  "guardrail_triggered": zod.boolean().optional(),
  "api_key_id": zod.string().min(1).optional(),
  "user_id": zod.string().min(1).optional(),
  "session_id": zod.string().min(1).optional(),
  "routing_strategy": zod.string().min(1).optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "organization": zod.string().uuid().optional(),
  "workspace": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional()
}))
})


/**
 * KPI cards with trend comparison.
 */
export const AgentccAnalyticsOverviewQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})












export const AgentccAnalyticsOverviewResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "request_id": zod.string().min(1).optional(),
  "model": zod.string().min(1).optional(),
  "provider": zod.string().min(1).optional(),
  "resolved_model": zod.string().min(1).optional(),
  "latency_ms": zod.number().optional(),
  "started_at": zod.string().datetime({"offset":true}).optional(),
  "input_tokens": zod.number().optional(),
  "output_tokens": zod.number().optional(),
  "total_tokens": zod.number().optional(),
  "cost": zod.string().optional(),
  "status_code": zod.number().optional(),
  "is_stream": zod.boolean().optional(),
  "is_error": zod.boolean().optional(),
  "error_message": zod.string().min(1).optional(),
  "cache_hit": zod.boolean().optional(),
  "fallback_used": zod.boolean().optional(),
  "guardrail_triggered": zod.boolean().optional(),
  "api_key_id": zod.string().min(1).optional(),
  "user_id": zod.string().min(1).optional(),
  "session_id": zod.string().min(1).optional(),
  "routing_strategy": zod.string().min(1).optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "organization": zod.string().uuid().optional(),
  "workspace": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional()
}))
})


/**
 * Time-bucketed usage data for charts.
 */
export const AgentccAnalyticsUsageTimeseriesQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})












export const AgentccAnalyticsUsageTimeseriesResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "request_id": zod.string().min(1).optional(),
  "model": zod.string().min(1).optional(),
  "provider": zod.string().min(1).optional(),
  "resolved_model": zod.string().min(1).optional(),
  "latency_ms": zod.number().optional(),
  "started_at": zod.string().datetime({"offset":true}).optional(),
  "input_tokens": zod.number().optional(),
  "output_tokens": zod.number().optional(),
  "total_tokens": zod.number().optional(),
  "cost": zod.string().optional(),
  "status_code": zod.number().optional(),
  "is_stream": zod.boolean().optional(),
  "is_error": zod.boolean().optional(),
  "error_message": zod.string().min(1).optional(),
  "cache_hit": zod.boolean().optional(),
  "fallback_used": zod.boolean().optional(),
  "guardrail_triggered": zod.boolean().optional(),
  "api_key_id": zod.string().min(1).optional(),
  "user_id": zod.string().min(1).optional(),
  "session_id": zod.string().min(1).optional(),
  "routing_strategy": zod.string().min(1).optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "organization": zod.string().uuid().optional(),
  "workspace": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional()
}))
})


export const AgentccApiKeysListQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})



export const agentccApiKeysListResponseResultsItemNameMax = 255;

export const agentccApiKeysListResponseResultsItemOwnerMax = 255;



export const AgentccApiKeysListResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "project": zod.string().uuid().optional(),
  "user": zod.string().uuid().optional(),
  "gateway_key_id": zod.string().min(1).optional(),
  "key_prefix": zod.string().min(1).optional(),
  "name": zod.string().min(1).max(agentccApiKeysListResponseResultsItemNameMax),
  "owner": zod.string().max(agentccApiKeysListResponseResultsItemOwnerMax).optional(),
  "status": zod.enum(['active', 'revoked', 'expired']).optional(),
  "allowed_models": zod.object({

}).passthrough().optional(),
  "allowed_providers": zod.object({

}).passthrough().optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "last_used_at": zod.string().datetime({"offset":true}).optional(),
  "expires_at": zod.string().datetime({"offset":true}).optional(),
  "organization": zod.string().uuid().optional(),
  "workspace": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
}))
})


export const agentccApiKeysCreateBodyNameMax = 255;

export const agentccApiKeysCreateBodyOwnerMax = 255;



export const AgentccApiKeysCreateBody = zod.object({
  "project": zod.string().uuid().optional(),
  "user": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(agentccApiKeysCreateBodyNameMax),
  "owner": zod.string().max(agentccApiKeysCreateBodyOwnerMax).optional(),
  "allowed_models": zod.object({

}).passthrough().optional(),
  "allowed_providers": zod.object({

}).passthrough().optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "last_used_at": zod.string().datetime({"offset":true}).optional(),
  "expires_at": zod.string().datetime({"offset":true}).optional()
})


/**
 * Bulk endpoint for gateway startup key sync.
Returns all active keys with their hashes so the gateway can restore
its in-memory KeyStore on restart.

Authenticated by admin token (not user JWT).
 */







export const AgentccApiKeysBulkListResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.array(zod.object({
  "id": zod.string().min(1),
  "name": zod.string().min(1),
  "owner": zod.string(),
  "key_hash": zod.string().min(1),
  "models": zod.array(zod.string().min(1)),
  "providers": zod.array(zod.string().min(1)),
  "metadata": zod.record(zod.string(), zod.string())
}))
})


export const agentccApiKeysSyncBodyNameMax = 255;

export const agentccApiKeysSyncBodyOwnerMax = 255;



export const AgentccApiKeysSyncBody = zod.object({
  "project": zod.string().uuid().optional(),
  "user": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(agentccApiKeysSyncBodyNameMax),
  "owner": zod.string().max(agentccApiKeysSyncBodyOwnerMax).optional(),
  "allowed_models": zod.object({

}).passthrough().optional(),
  "allowed_providers": zod.object({

}).passthrough().optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "last_used_at": zod.string().datetime({"offset":true}).optional(),
  "expires_at": zod.string().datetime({"offset":true}).optional()
})


export const AgentccApiKeysReadParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc api key.')
})



export const agentccApiKeysReadResponseNameMax = 255;

export const agentccApiKeysReadResponseOwnerMax = 255;



export const AgentccApiKeysReadResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "project": zod.string().uuid().optional(),
  "user": zod.string().uuid().optional(),
  "gateway_key_id": zod.string().min(1).optional(),
  "key_prefix": zod.string().min(1).optional(),
  "name": zod.string().min(1).max(agentccApiKeysReadResponseNameMax),
  "owner": zod.string().max(agentccApiKeysReadResponseOwnerMax).optional(),
  "status": zod.enum(['active', 'revoked', 'expired']).optional(),
  "allowed_models": zod.object({

}).passthrough().optional(),
  "allowed_providers": zod.object({

}).passthrough().optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "last_used_at": zod.string().datetime({"offset":true}).optional(),
  "expires_at": zod.string().datetime({"offset":true}).optional(),
  "organization": zod.string().uuid().optional(),
  "workspace": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})


export const AgentccApiKeysUpdateParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc api key.')
})

export const agentccApiKeysUpdateBodyNameMax = 255;

export const agentccApiKeysUpdateBodyOwnerMax = 255;



export const AgentccApiKeysUpdateBody = zod.object({
  "project": zod.string().uuid().optional(),
  "user": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(agentccApiKeysUpdateBodyNameMax),
  "owner": zod.string().max(agentccApiKeysUpdateBodyOwnerMax).optional(),
  "allowed_models": zod.object({

}).passthrough().optional(),
  "allowed_providers": zod.object({

}).passthrough().optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "last_used_at": zod.string().datetime({"offset":true}).optional(),
  "expires_at": zod.string().datetime({"offset":true}).optional()
})



export const agentccApiKeysUpdateResponseNameMax = 255;

export const agentccApiKeysUpdateResponseOwnerMax = 255;



export const AgentccApiKeysUpdateResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "project": zod.string().uuid().optional(),
  "user": zod.string().uuid().optional(),
  "gateway_key_id": zod.string().min(1).optional(),
  "key_prefix": zod.string().min(1).optional(),
  "name": zod.string().min(1).max(agentccApiKeysUpdateResponseNameMax),
  "owner": zod.string().max(agentccApiKeysUpdateResponseOwnerMax).optional(),
  "status": zod.enum(['active', 'revoked', 'expired']).optional(),
  "allowed_models": zod.object({

}).passthrough().optional(),
  "allowed_providers": zod.object({

}).passthrough().optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "last_used_at": zod.string().datetime({"offset":true}).optional(),
  "expires_at": zod.string().datetime({"offset":true}).optional(),
  "organization": zod.string().uuid().optional(),
  "workspace": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})


export const AgentccApiKeysPartialUpdateParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc api key.')
})

export const agentccApiKeysPartialUpdateBodyNameMax = 255;

export const agentccApiKeysPartialUpdateBodyOwnerMax = 255;



export const AgentccApiKeysPartialUpdateBody = zod.object({
  "project": zod.string().uuid().optional(),
  "user": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(agentccApiKeysPartialUpdateBodyNameMax),
  "owner": zod.string().max(agentccApiKeysPartialUpdateBodyOwnerMax).optional(),
  "allowed_models": zod.object({

}).passthrough().optional(),
  "allowed_providers": zod.object({

}).passthrough().optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "last_used_at": zod.string().datetime({"offset":true}).optional(),
  "expires_at": zod.string().datetime({"offset":true}).optional()
})



export const agentccApiKeysPartialUpdateResponseNameMax = 255;

export const agentccApiKeysPartialUpdateResponseOwnerMax = 255;



export const AgentccApiKeysPartialUpdateResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "project": zod.string().uuid().optional(),
  "user": zod.string().uuid().optional(),
  "gateway_key_id": zod.string().min(1).optional(),
  "key_prefix": zod.string().min(1).optional(),
  "name": zod.string().min(1).max(agentccApiKeysPartialUpdateResponseNameMax),
  "owner": zod.string().max(agentccApiKeysPartialUpdateResponseOwnerMax).optional(),
  "status": zod.enum(['active', 'revoked', 'expired']).optional(),
  "allowed_models": zod.object({

}).passthrough().optional(),
  "allowed_providers": zod.object({

}).passthrough().optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "last_used_at": zod.string().datetime({"offset":true}).optional(),
  "expires_at": zod.string().datetime({"offset":true}).optional(),
  "organization": zod.string().uuid().optional(),
  "workspace": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})


export const AgentccApiKeysDeleteParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc api key.')
})


export const AgentccApiKeysRevokeParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc api key.')
})

export const agentccApiKeysRevokeBodyNameMax = 255;

export const agentccApiKeysRevokeBodyOwnerMax = 255;



export const AgentccApiKeysRevokeBody = zod.object({
  "project": zod.string().uuid().optional(),
  "user": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(agentccApiKeysRevokeBodyNameMax),
  "owner": zod.string().max(agentccApiKeysRevokeBodyOwnerMax).optional(),
  "allowed_models": zod.object({

}).passthrough().optional(),
  "allowed_providers": zod.object({

}).passthrough().optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "last_used_at": zod.string().datetime({"offset":true}).optional(),
  "expires_at": zod.string().datetime({"offset":true}).optional()
})


/**
 * CRUD for named word blocklists. Org-scoped.
 */
export const AgentccBlocklistsListQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})

export const agentccBlocklistsListResponseResultsItemNameMax = 255;



export const AgentccBlocklistsListResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "organization": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(agentccBlocklistsListResponseResultsItemNameMax),
  "description": zod.string().optional(),
  "words": zod.object({

}).passthrough().optional(),
  "is_active": zod.boolean().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
}))
})


/**
 * CRUD for named word blocklists. Org-scoped.
 */
export const agentccBlocklistsCreateBodyNameMax = 255;



export const AgentccBlocklistsCreateBody = zod.object({
  "name": zod.string().min(1).max(agentccBlocklistsCreateBodyNameMax),
  "description": zod.string().optional(),
  "words": zod.object({

}).passthrough().optional(),
  "is_active": zod.boolean().optional()
})


/**
 * CRUD for named word blocklists. Org-scoped.
 */
export const AgentccBlocklistsReadParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc blocklist.')
})

export const agentccBlocklistsReadResponseNameMax = 255;



export const AgentccBlocklistsReadResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "organization": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(agentccBlocklistsReadResponseNameMax),
  "description": zod.string().optional(),
  "words": zod.object({

}).passthrough().optional(),
  "is_active": zod.boolean().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})


/**
 * CRUD for named word blocklists. Org-scoped.
 */
export const AgentccBlocklistsUpdateParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc blocklist.')
})

export const agentccBlocklistsUpdateBodyNameMax = 255;



export const AgentccBlocklistsUpdateBody = zod.object({
  "name": zod.string().min(1).max(agentccBlocklistsUpdateBodyNameMax),
  "description": zod.string().optional(),
  "words": zod.object({

}).passthrough().optional(),
  "is_active": zod.boolean().optional()
})

export const agentccBlocklistsUpdateResponseNameMax = 255;



export const AgentccBlocklistsUpdateResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "organization": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(agentccBlocklistsUpdateResponseNameMax),
  "description": zod.string().optional(),
  "words": zod.object({

}).passthrough().optional(),
  "is_active": zod.boolean().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})


/**
 * CRUD for named word blocklists. Org-scoped.
 */
export const AgentccBlocklistsPartialUpdateParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc blocklist.')
})

export const agentccBlocklistsPartialUpdateBodyNameMax = 255;



export const AgentccBlocklistsPartialUpdateBody = zod.object({
  "name": zod.string().min(1).max(agentccBlocklistsPartialUpdateBodyNameMax),
  "description": zod.string().optional(),
  "words": zod.object({

}).passthrough().optional(),
  "is_active": zod.boolean().optional()
})

export const agentccBlocklistsPartialUpdateResponseNameMax = 255;



export const AgentccBlocklistsPartialUpdateResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "organization": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(agentccBlocklistsPartialUpdateResponseNameMax),
  "description": zod.string().optional(),
  "words": zod.object({

}).passthrough().optional(),
  "is_active": zod.boolean().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})


/**
 * CRUD for named word blocklists. Org-scoped.
 */
export const AgentccBlocklistsDeleteParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc blocklist.')
})


/**
 * Add words to the blocklist (deduplicates).
 */
export const AgentccBlocklistsAddWordsParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc blocklist.')
})

export const agentccBlocklistsAddWordsBodyNameMax = 255;



export const AgentccBlocklistsAddWordsBody = zod.object({
  "name": zod.string().min(1).max(agentccBlocklistsAddWordsBodyNameMax),
  "description": zod.string().optional(),
  "words": zod.object({

}).passthrough().optional(),
  "is_active": zod.boolean().optional()
})


/**
 * Remove words from the blocklist.
 */
export const AgentccBlocklistsRemoveWordsParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc blocklist.')
})

export const agentccBlocklistsRemoveWordsBodyNameMax = 255;



export const AgentccBlocklistsRemoveWordsBody = zod.object({
  "name": zod.string().min(1).max(agentccBlocklistsRemoveWordsBodyNameMax),
  "description": zod.string().optional(),
  "words": zod.object({

}).passthrough().optional(),
  "is_active": zod.boolean().optional()
})


/**
 * CRUD for custom property schemas. Org-scoped.
 */
export const AgentccCustomPropertiesListQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})

export const agentccCustomPropertiesListResponseResultsItemNameMax = 255;



export const AgentccCustomPropertiesListResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "organization": zod.string().uuid().optional(),
  "project": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(agentccCustomPropertiesListResponseResultsItemNameMax),
  "description": zod.string().optional(),
  "property_type": zod.enum(['string', 'number', 'boolean', 'enum']).optional(),
  "required": zod.boolean().optional(),
  "allowed_values": zod.object({

}).passthrough().optional(),
  "default_value": zod.object({

}).passthrough().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
}))
})


/**
 * CRUD for custom property schemas. Org-scoped.
 */
export const agentccCustomPropertiesCreateBodyNameMax = 255;



export const AgentccCustomPropertiesCreateBody = zod.object({
  "project": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(agentccCustomPropertiesCreateBodyNameMax),
  "description": zod.string().optional(),
  "property_type": zod.enum(['string', 'number', 'boolean', 'enum']).optional(),
  "required": zod.boolean().optional(),
  "allowed_values": zod.object({

}).passthrough().optional(),
  "default_value": zod.object({

}).passthrough().optional()
})


/**
 * Validate a set of custom properties against the org's schemas.
 */
export const agentccCustomPropertiesValidateBodyNameMax = 255;



export const AgentccCustomPropertiesValidateBody = zod.object({
  "project": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(agentccCustomPropertiesValidateBodyNameMax),
  "description": zod.string().optional(),
  "property_type": zod.enum(['string', 'number', 'boolean', 'enum']).optional(),
  "required": zod.boolean().optional(),
  "allowed_values": zod.object({

}).passthrough().optional(),
  "default_value": zod.object({

}).passthrough().optional()
})


/**
 * CRUD for custom property schemas. Org-scoped.
 */
export const AgentccCustomPropertiesReadParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc custom property schema.')
})

export const agentccCustomPropertiesReadResponseNameMax = 255;



export const AgentccCustomPropertiesReadResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "organization": zod.string().uuid().optional(),
  "project": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(agentccCustomPropertiesReadResponseNameMax),
  "description": zod.string().optional(),
  "property_type": zod.enum(['string', 'number', 'boolean', 'enum']).optional(),
  "required": zod.boolean().optional(),
  "allowed_values": zod.object({

}).passthrough().optional(),
  "default_value": zod.object({

}).passthrough().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})


/**
 * CRUD for custom property schemas. Org-scoped.
 */
export const AgentccCustomPropertiesUpdateParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc custom property schema.')
})

export const agentccCustomPropertiesUpdateBodyNameMax = 255;



export const AgentccCustomPropertiesUpdateBody = zod.object({
  "project": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(agentccCustomPropertiesUpdateBodyNameMax),
  "description": zod.string().optional(),
  "property_type": zod.enum(['string', 'number', 'boolean', 'enum']).optional(),
  "required": zod.boolean().optional(),
  "allowed_values": zod.object({

}).passthrough().optional(),
  "default_value": zod.object({

}).passthrough().optional()
})

export const agentccCustomPropertiesUpdateResponseNameMax = 255;



export const AgentccCustomPropertiesUpdateResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "organization": zod.string().uuid().optional(),
  "project": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(agentccCustomPropertiesUpdateResponseNameMax),
  "description": zod.string().optional(),
  "property_type": zod.enum(['string', 'number', 'boolean', 'enum']).optional(),
  "required": zod.boolean().optional(),
  "allowed_values": zod.object({

}).passthrough().optional(),
  "default_value": zod.object({

}).passthrough().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})


/**
 * CRUD for custom property schemas. Org-scoped.
 */
export const AgentccCustomPropertiesPartialUpdateParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc custom property schema.')
})

export const agentccCustomPropertiesPartialUpdateBodyNameMax = 255;



export const AgentccCustomPropertiesPartialUpdateBody = zod.object({
  "project": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(agentccCustomPropertiesPartialUpdateBodyNameMax),
  "description": zod.string().optional(),
  "property_type": zod.enum(['string', 'number', 'boolean', 'enum']).optional(),
  "required": zod.boolean().optional(),
  "allowed_values": zod.object({

}).passthrough().optional(),
  "default_value": zod.object({

}).passthrough().optional()
})

export const agentccCustomPropertiesPartialUpdateResponseNameMax = 255;



export const AgentccCustomPropertiesPartialUpdateResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "organization": zod.string().uuid().optional(),
  "project": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(agentccCustomPropertiesPartialUpdateResponseNameMax),
  "description": zod.string().optional(),
  "property_type": zod.enum(['string', 'number', 'boolean', 'enum']).optional(),
  "required": zod.boolean().optional(),
  "allowed_values": zod.object({

}).passthrough().optional(),
  "default_value": zod.object({

}).passthrough().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})


/**
 * CRUD for custom property schemas. Org-scoped.
 */
export const AgentccCustomPropertiesDeleteParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc custom property schema.')
})


/**
 * CRUD + test for email alert configurations. Org-scoped.
 */
export const AgentccEmailAlertsListQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})

export const agentccEmailAlertsListResponseResultsItemNameMax = 255;

export const agentccEmailAlertsListResponseResultsItemCooldownMinutesMin = -2147483648;
export const agentccEmailAlertsListResponseResultsItemCooldownMinutesMax = 2147483647;



export const AgentccEmailAlertsListResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "organization": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(agentccEmailAlertsListResponseResultsItemNameMax),
  "recipients": zod.object({

}).passthrough().optional(),
  "events": zod.object({

}).passthrough().optional(),
  "thresholds": zod.object({

}).passthrough().optional(),
  "provider": zod.enum(['sendgrid', 'resend', 'smtp']).optional(),
  "provider_config": zod.string().optional(),
  "is_active": zod.boolean().optional(),
  "cooldown_minutes": zod.number().min(agentccEmailAlertsListResponseResultsItemCooldownMinutesMin).max(agentccEmailAlertsListResponseResultsItemCooldownMinutesMax).optional(),
  "last_triggered_at": zod.string().datetime({"offset":true}).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
}))
})


/**
 * CRUD + test for email alert configurations. Org-scoped.
 */
export const agentccEmailAlertsCreateBodyNameMax = 255;

export const agentccEmailAlertsCreateBodyCooldownMinutesMin = -2147483648;
export const agentccEmailAlertsCreateBodyCooldownMinutesMax = 2147483647;



export const AgentccEmailAlertsCreateBody = zod.object({
  "name": zod.string().min(1).max(agentccEmailAlertsCreateBodyNameMax),
  "recipients": zod.object({

}).passthrough().optional(),
  "events": zod.object({

}).passthrough().optional(),
  "thresholds": zod.object({

}).passthrough().optional(),
  "provider": zod.enum(['sendgrid', 'resend', 'smtp']).optional(),
  "is_active": zod.boolean().optional(),
  "cooldown_minutes": zod.number().min(agentccEmailAlertsCreateBodyCooldownMinutesMin).max(agentccEmailAlertsCreateBodyCooldownMinutesMax).optional()
})


/**
 * CRUD + test for email alert configurations. Org-scoped.
 */
export const AgentccEmailAlertsReadParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc email alert.')
})

export const agentccEmailAlertsReadResponseNameMax = 255;

export const agentccEmailAlertsReadResponseCooldownMinutesMin = -2147483648;
export const agentccEmailAlertsReadResponseCooldownMinutesMax = 2147483647;



export const AgentccEmailAlertsReadResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "organization": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(agentccEmailAlertsReadResponseNameMax),
  "recipients": zod.object({

}).passthrough().optional(),
  "events": zod.object({

}).passthrough().optional(),
  "thresholds": zod.object({

}).passthrough().optional(),
  "provider": zod.enum(['sendgrid', 'resend', 'smtp']).optional(),
  "provider_config": zod.string().optional(),
  "is_active": zod.boolean().optional(),
  "cooldown_minutes": zod.number().min(agentccEmailAlertsReadResponseCooldownMinutesMin).max(agentccEmailAlertsReadResponseCooldownMinutesMax).optional(),
  "last_triggered_at": zod.string().datetime({"offset":true}).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})


/**
 * CRUD + test for email alert configurations. Org-scoped.
 */
export const AgentccEmailAlertsUpdateParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc email alert.')
})

export const agentccEmailAlertsUpdateBodyNameMax = 255;

export const agentccEmailAlertsUpdateBodyCooldownMinutesMin = -2147483648;
export const agentccEmailAlertsUpdateBodyCooldownMinutesMax = 2147483647;



export const AgentccEmailAlertsUpdateBody = zod.object({
  "name": zod.string().min(1).max(agentccEmailAlertsUpdateBodyNameMax),
  "recipients": zod.object({

}).passthrough().optional(),
  "events": zod.object({

}).passthrough().optional(),
  "thresholds": zod.object({

}).passthrough().optional(),
  "provider": zod.enum(['sendgrid', 'resend', 'smtp']).optional(),
  "is_active": zod.boolean().optional(),
  "cooldown_minutes": zod.number().min(agentccEmailAlertsUpdateBodyCooldownMinutesMin).max(agentccEmailAlertsUpdateBodyCooldownMinutesMax).optional()
})

export const agentccEmailAlertsUpdateResponseNameMax = 255;

export const agentccEmailAlertsUpdateResponseCooldownMinutesMin = -2147483648;
export const agentccEmailAlertsUpdateResponseCooldownMinutesMax = 2147483647;



export const AgentccEmailAlertsUpdateResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "organization": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(agentccEmailAlertsUpdateResponseNameMax),
  "recipients": zod.object({

}).passthrough().optional(),
  "events": zod.object({

}).passthrough().optional(),
  "thresholds": zod.object({

}).passthrough().optional(),
  "provider": zod.enum(['sendgrid', 'resend', 'smtp']).optional(),
  "provider_config": zod.string().optional(),
  "is_active": zod.boolean().optional(),
  "cooldown_minutes": zod.number().min(agentccEmailAlertsUpdateResponseCooldownMinutesMin).max(agentccEmailAlertsUpdateResponseCooldownMinutesMax).optional(),
  "last_triggered_at": zod.string().datetime({"offset":true}).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})


/**
 * CRUD + test for email alert configurations. Org-scoped.
 */
export const AgentccEmailAlertsPartialUpdateParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc email alert.')
})

export const agentccEmailAlertsPartialUpdateBodyNameMax = 255;

export const agentccEmailAlertsPartialUpdateBodyCooldownMinutesMin = -2147483648;
export const agentccEmailAlertsPartialUpdateBodyCooldownMinutesMax = 2147483647;



export const AgentccEmailAlertsPartialUpdateBody = zod.object({
  "name": zod.string().min(1).max(agentccEmailAlertsPartialUpdateBodyNameMax),
  "recipients": zod.object({

}).passthrough().optional(),
  "events": zod.object({

}).passthrough().optional(),
  "thresholds": zod.object({

}).passthrough().optional(),
  "provider": zod.enum(['sendgrid', 'resend', 'smtp']).optional(),
  "is_active": zod.boolean().optional(),
  "cooldown_minutes": zod.number().min(agentccEmailAlertsPartialUpdateBodyCooldownMinutesMin).max(agentccEmailAlertsPartialUpdateBodyCooldownMinutesMax).optional()
})

export const agentccEmailAlertsPartialUpdateResponseNameMax = 255;

export const agentccEmailAlertsPartialUpdateResponseCooldownMinutesMin = -2147483648;
export const agentccEmailAlertsPartialUpdateResponseCooldownMinutesMax = 2147483647;



export const AgentccEmailAlertsPartialUpdateResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "organization": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(agentccEmailAlertsPartialUpdateResponseNameMax),
  "recipients": zod.object({

}).passthrough().optional(),
  "events": zod.object({

}).passthrough().optional(),
  "thresholds": zod.object({

}).passthrough().optional(),
  "provider": zod.enum(['sendgrid', 'resend', 'smtp']).optional(),
  "provider_config": zod.string().optional(),
  "is_active": zod.boolean().optional(),
  "cooldown_minutes": zod.number().min(agentccEmailAlertsPartialUpdateResponseCooldownMinutesMin).max(agentccEmailAlertsPartialUpdateResponseCooldownMinutesMax).optional(),
  "last_triggered_at": zod.string().datetime({"offset":true}).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})


/**
 * CRUD + test for email alert configurations. Org-scoped.
 */
export const AgentccEmailAlertsDeleteParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc email alert.')
})


/**
 * Send a test email using this alert's configuration.
 */
export const AgentccEmailAlertsTestParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc email alert.')
})

export const agentccEmailAlertsTestBodyNameMax = 255;

export const agentccEmailAlertsTestBodyCooldownMinutesMin = -2147483648;
export const agentccEmailAlertsTestBodyCooldownMinutesMax = 2147483647;



export const AgentccEmailAlertsTestBody = zod.object({
  "name": zod.string().min(1).max(agentccEmailAlertsTestBodyNameMax),
  "recipients": zod.object({

}).passthrough().optional(),
  "events": zod.object({

}).passthrough().optional(),
  "thresholds": zod.object({

}).passthrough().optional(),
  "provider": zod.enum(['sendgrid', 'resend', 'smtp']).optional(),
  "is_active": zod.boolean().optional(),
  "cooldown_minutes": zod.number().min(agentccEmailAlertsTestBodyCooldownMinutesMin).max(agentccEmailAlertsTestBodyCooldownMinutesMax).optional()
})


/**
 * Stateless proxy to the Go gateway (configured via env vars).
No DB model â€” returns a virtual singleton gateway with live health.
 */






export const AgentccGatewaysListResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.array(zod.object({
  "id": zod.string().min(1),
  "name": zod.string().min(1),
  "base_url": zod.string().url().min(1),
  "status": zod.string().min(1),
  "provider_count": zod.number().optional(),
  "model_count": zod.number().optional()
}))
})


/**
 * Return eval templates compatible with the FI protect guardrail.
 */
export const AgentccGatewaysProtectTemplatesResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.array(zod.object({

}).passthrough())
})


/**
 * Accept any pk and ignore it â€” there is only one gateway.
 */
export const AgentccGatewaysReadParams = zod.object({
  "id": zod.string()
})







export const AgentccGatewaysReadResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "id": zod.string().min(1),
  "name": zod.string().min(1),
  "base_url": zod.string().url().min(1),
  "status": zod.string().min(1),
  "provider_count": zod.number().optional(),
  "model_count": zod.number().optional()
})
})


/**
 * Stateless proxy to the Go gateway (configured via env vars).
No DB model â€” returns a virtual singleton gateway with live health.
 */
export const AgentccGatewaysCancelBatchParams = zod.object({
  "id": zod.string()
})




export const AgentccGatewaysCancelBatchBody = zod.object({
  "batch_id": zod.string().min(1)
})





export const AgentccGatewaysCancelBatchResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "batch_id": zod.string().min(1),
  "status": zod.string().min(1)
})
})


/**
 * Stateless proxy to the Go gateway (configured via env vars).
No DB model â€” returns a virtual singleton gateway with live health.
 */
export const AgentccGatewaysConfigParams = zod.object({
  "id": zod.string()
})






export const AgentccGatewaysConfigResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "id": zod.string().uuid().optional(),
  "organization": zod.string().uuid().optional(),
  "version": zod.number().optional(),
  "guardrails": zod.object({

}).passthrough().optional(),
  "routing": zod.object({

}).passthrough().optional(),
  "cache": zod.object({

}).passthrough().optional(),
  "rate_limiting": zod.object({

}).passthrough().optional(),
  "budgets": zod.object({

}).passthrough().optional(),
  "cost_tracking": zod.object({

}).passthrough().optional(),
  "ip_acl": zod.object({

}).passthrough().optional(),
  "alerting": zod.object({

}).passthrough().optional(),
  "privacy": zod.object({

}).passthrough().optional(),
  "tool_policy": zod.object({

}).passthrough().optional(),
  "mcp": zod.object({

}).passthrough().optional(),
  "a2a": zod.object({

}).passthrough().optional(),
  "audit": zod.object({

}).passthrough().optional(),
  "model_database": zod.object({

}).passthrough().optional(),
  "model_map": zod.object({

}).passthrough().optional(),
  "is_active": zod.boolean().optional(),
  "created_by": zod.string().uuid().optional(),
  "change_description": zod.string().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional(),
  "providers": zod.record(zod.string(), zod.object({
  "id": zod.string().uuid(),
  "name": zod.string().min(1),
  "display_name": zod.string().min(1),
  "base_url": zod.string(),
  "api_format": zod.string().describe('Gateway protocol adapter name. This intentionally remains a string because self-hosted\/custom providers may register adapters outside the built-in openai\/anthropic\/gemini\/google set.'),
  "models": zod.array(zod.object({

}).passthrough()),
  "is_active": zod.boolean(),
  "default_timeout": zod.number(),
  "max_concurrent": zod.number(),
  "conn_pool_size": zod.number()
})),
  "gateway": zod.object({
  "status": zod.string().min(1)
})
})
})


/**
 * Stateless proxy to the Go gateway (configured via env vars).
No DB model â€” returns a virtual singleton gateway with live health.
 */
export const AgentccGatewaysGetBatchParams = zod.object({
  "id": zod.string()
})





export const AgentccGatewaysGetBatchResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "batch_id": zod.string().min(1),
  "status": zod.string().min(1),
  "total": zod.number(),
  "max_concurrency": zod.number(),
  "created_at": zod.string().datetime({"offset":true}),
  "completed_at": zod.string().datetime({"offset":true}).optional(),
  "results": zod.array(zod.object({

}).passthrough()).optional(),
  "summary": zod.object({
  "total_cost": zod.number(),
  "total_input_tokens": zod.number(),
  "total_output_tokens": zod.number(),
  "completed": zod.number(),
  "failed": zod.number(),
  "cancelled": zod.number()
}).optional()
})
})


/**
 * Stateless proxy to the Go gateway (configured via env vars).
No DB model â€” returns a virtual singleton gateway with live health.
 */
export const AgentccGatewaysHealthCheckParams = zod.object({
  "id": zod.string()
})

export const AgentccGatewaysHealthCheckBody = zod.object({

})





export const AgentccGatewaysHealthCheckResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "status": zod.string().min(1),
  "health": zod.object({

}).passthrough().optional(),
  "providers": zod.object({
  "providers": zod.array(zod.object({
  "name": zod.string().min(1),
  "display_name": zod.string().optional(),
  "models": zod.array(zod.object({

}).passthrough()).optional(),
  "status": zod.string().optional()
}))
}),
  "provider_count": zod.number(),
  "model_count": zod.number()
})
})


/**
 * Stateless proxy to the Go gateway (configured via env vars).
No DB model â€” returns a virtual singleton gateway with live health.
 */
export const AgentccGatewaysMcpPromptsParams = zod.object({
  "id": zod.string()
})

export const AgentccGatewaysMcpPromptsResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.array(zod.object({

}).passthrough())
})


/**
 * Stateless proxy to the Go gateway (configured via env vars).
No DB model â€” returns a virtual singleton gateway with live health.
 */
export const AgentccGatewaysMcpResourcesParams = zod.object({
  "id": zod.string()
})

export const AgentccGatewaysMcpResourcesResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.array(zod.object({

}).passthrough())
})


/**
 * Stateless proxy to the Go gateway (configured via env vars).
No DB model â€” returns a virtual singleton gateway with live health.
 */
export const AgentccGatewaysMcpStatusParams = zod.object({
  "id": zod.string()
})

export const AgentccGatewaysMcpStatusResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "enabled": zod.boolean(),
  "sessions": zod.number(),
  "tools": zod.number(),
  "resources": zod.number(),
  "prompts": zod.number(),
  "servers": zod.array(zod.object({

}).passthrough()).describe('Gateway MCP server statuses are adapter-specific objects; the Django fallback normalizes configured servers to objects with id and status.')
})
})


/**
 * Stateless proxy to the Go gateway (configured via env vars).
No DB model â€” returns a virtual singleton gateway with live health.
 */
export const AgentccGatewaysMcpToolsParams = zod.object({
  "id": zod.string()
})

export const AgentccGatewaysMcpToolsResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.array(zod.object({

}).passthrough())
})


/**
 * Stateless proxy to the Go gateway (configured via env vars).
No DB model â€” returns a virtual singleton gateway with live health.
 */
export const AgentccGatewaysProvidersParams = zod.object({
  "id": zod.string()
})







export const AgentccGatewaysProvidersResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "providers": zod.array(zod.object({
  "id": zod.string().min(1).describe('Provider key\/name used by the gateway, not a database UUID.'),
  "name": zod.string().min(1),
  "status": zod.string().min(1),
  "healthy": zod.boolean(),
  "circuit_state": zod.string().min(1),
  "display_name": zod.string().optional(),
  "base_url": zod.string().optional(),
  "api_format": zod.string().optional().describe('Gateway protocol adapter name. This intentionally remains a string because self-hosted\/custom providers may register adapters outside the built-in openai\/anthropic\/gemini\/google set.'),
  "models": zod.array(zod.object({

}).passthrough()).optional(),
  "request_count": zod.number().optional(),
  "avg_latency": zod.number().optional(),
  "error_rate": zod.number().optional()
}))
})
})


/**
 * Re-push this org's config to the gateway.
 */
export const AgentccGatewaysReloadParams = zod.object({
  "id": zod.string()
})

export const AgentccGatewaysReloadBody = zod.object({

})

export const AgentccGatewaysReloadResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "status": zod.boolean().optional(),
  "version": zod.number().optional(),
  "gateway_synced": zod.boolean().optional(),
  "gateway_warning": zod.string().optional(),
  "action": zod.string().optional(),
  "provider": zod.string().optional(),
  "guardrail": zod.string().optional(),
  "budget": zod.string().optional(),
  "server": zod.string().optional(),
  "enabled": zod.boolean().optional()
})
})


/**
 * Stateless proxy to the Go gateway (configured via env vars).
No DB model â€” returns a virtual singleton gateway with live health.
 */
export const AgentccGatewaysRemoveBudgetParams = zod.object({
  "id": zod.string()
})




export const AgentccGatewaysRemoveBudgetBody = zod.object({
  "level": zod.string().min(1)
})

export const AgentccGatewaysRemoveBudgetResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "status": zod.boolean().optional(),
  "version": zod.number().optional(),
  "gateway_synced": zod.boolean().optional(),
  "gateway_warning": zod.string().optional(),
  "action": zod.string().optional(),
  "provider": zod.string().optional(),
  "guardrail": zod.string().optional(),
  "budget": zod.string().optional(),
  "server": zod.string().optional(),
  "enabled": zod.boolean().optional()
})
})


/**
 * Stateless proxy to the Go gateway (configured via env vars).
No DB model â€” returns a virtual singleton gateway with live health.
 */
export const AgentccGatewaysRemoveMcpServerParams = zod.object({
  "id": zod.string()
})




export const AgentccGatewaysRemoveMcpServerBody = zod.object({
  "server_id": zod.string().min(1)
})

export const AgentccGatewaysRemoveMcpServerResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "status": zod.boolean().optional(),
  "version": zod.number().optional(),
  "gateway_synced": zod.boolean().optional(),
  "gateway_warning": zod.string().optional(),
  "action": zod.string().optional(),
  "provider": zod.string().optional(),
  "guardrail": zod.string().optional(),
  "budget": zod.string().optional(),
  "server": zod.string().optional(),
  "enabled": zod.boolean().optional()
})
})


/**
 * Soft-delete a provider credential and push config.
 */
export const AgentccGatewaysRemoveProviderParams = zod.object({
  "id": zod.string()
})




export const AgentccGatewaysRemoveProviderBody = zod.object({
  "name": zod.string().min(1)
})

export const AgentccGatewaysRemoveProviderResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "status": zod.boolean().optional(),
  "version": zod.number().optional(),
  "gateway_synced": zod.boolean().optional(),
  "gateway_warning": zod.string().optional(),
  "action": zod.string().optional(),
  "provider": zod.string().optional(),
  "guardrail": zod.string().optional(),
  "budget": zod.string().optional(),
  "server": zod.string().optional(),
  "enabled": zod.boolean().optional()
})
})


/**
 * Stateless proxy to the Go gateway (configured via env vars).
No DB model â€” returns a virtual singleton gateway with live health.
 */
export const AgentccGatewaysSetBudgetParams = zod.object({
  "id": zod.string()
})




export const AgentccGatewaysSetBudgetBody = zod.object({
  "level": zod.string().min(1),
  "config": zod.record(zod.string(), zod.object({

}).passthrough())
})

export const AgentccGatewaysSetBudgetResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "status": zod.boolean().optional(),
  "version": zod.number().optional(),
  "gateway_synced": zod.boolean().optional(),
  "gateway_warning": zod.string().optional(),
  "action": zod.string().optional(),
  "provider": zod.string().optional(),
  "guardrail": zod.string().optional(),
  "budget": zod.string().optional(),
  "server": zod.string().optional(),
  "enabled": zod.boolean().optional()
})
})


/**
 * Stateless proxy to the Go gateway (configured via env vars).
No DB model â€” returns a virtual singleton gateway with live health.
 */
export const AgentccGatewaysSubmitBatchParams = zod.object({
  "id": zod.string()
})

export const agentccGatewaysSubmitBatchBodyMaxConcurrencyDefault = 5;



export const AgentccGatewaysSubmitBatchBody = zod.object({
  "requests": zod.array(zod.record(zod.string(), zod.object({

}).passthrough())),
  "max_concurrency": zod.number().min(1).default(agentccGatewaysSubmitBatchBodyMaxConcurrencyDefault)
})





export const AgentccGatewaysSubmitBatchResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "batch_id": zod.string().min(1),
  "status": zod.string().min(1),
  "total": zod.number(),
  "max_concurrency": zod.number(),
  "created_at": zod.string().datetime({"offset":true})
})
})


/**
 * Stateless proxy to the Go gateway (configured via env vars).
No DB model â€” returns a virtual singleton gateway with live health.
 */
export const AgentccGatewaysTestMcpToolParams = zod.object({
  "id": zod.string()
})


export const agentccGatewaysTestMcpToolBodyArgumentsDefault = {  };

export const AgentccGatewaysTestMcpToolBody = zod.object({
  "name": zod.string().min(1),
  "arguments": zod.record(zod.string(), zod.object({

}).passthrough()).default(agentccGatewaysTestMcpToolBodyArgumentsDefault)
})




export const AgentccGatewaysTestMcpToolResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "content": zod.array(zod.object({
  "type": zod.string().min(1),
  "text": zod.string().optional(),
  "data": zod.string().optional(),
  "mimeType": zod.string().optional()
})).optional(),
  "is_error": zod.boolean().optional(),
  "duration_ms": zod.number().optional(),
  "guardrail_pre": zod.enum(['pass', 'blocked', 'skipped']).optional(),
  "guardrail_post": zod.enum(['pass', 'blocked', 'skipped']).optional(),
  "error": zod.string().optional(),
  "server": zod.string().optional()
})
})


/**
 * Send a real chat completion through the gateway to test guardrails.
 */
export const AgentccGatewaysTestPlaygroundParams = zod.object({
  "id": zod.string()
})




export const AgentccGatewaysTestPlaygroundBody = zod.object({
  "prompt": zod.string().min(1),
  "model": zod.string().optional(),
  "system_prompt": zod.string().optional()
})





export const AgentccGatewaysTestPlaygroundResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "status_code": zod.number(),
  "body": zod.object({

}).passthrough(),
  "guardrail_headers": zod.record(zod.string(), zod.string().min(1)),
  "model": zod.string().min(1),
  "blocked": zod.boolean(),
  "warned": zod.boolean()
})
})


/**
 * Stateless proxy to the Go gateway (configured via env vars).
No DB model â€” returns a virtual singleton gateway with live health.
 */
export const AgentccGatewaysToggleGuardrailParams = zod.object({
  "id": zod.string()
})




export const AgentccGatewaysToggleGuardrailBody = zod.object({
  "name": zod.string().min(1),
  "enabled": zod.boolean()
})

export const AgentccGatewaysToggleGuardrailResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "status": zod.boolean().optional(),
  "version": zod.number().optional(),
  "gateway_synced": zod.boolean().optional(),
  "gateway_warning": zod.string().optional(),
  "action": zod.string().optional(),
  "provider": zod.string().optional(),
  "guardrail": zod.string().optional(),
  "budget": zod.string().optional(),
  "server": zod.string().optional(),
  "enabled": zod.boolean().optional()
})
})


/**
 * Patch one or more JSON fields on the org's active config and push.
 */
export const AgentccGatewaysUpdateConfigParams = zod.object({
  "id": zod.string()
})

export const AgentccGatewaysUpdateConfigBody = zod.object({
  "guardrails": zod.record(zod.string(), zod.object({

}).passthrough()).optional(),
  "routing": zod.record(zod.string(), zod.object({

}).passthrough()).optional(),
  "cache": zod.record(zod.string(), zod.object({

}).passthrough()).optional(),
  "rate_limiting": zod.record(zod.string(), zod.object({

}).passthrough()).optional(),
  "budgets": zod.record(zod.string(), zod.object({

}).passthrough()).optional(),
  "cost_tracking": zod.record(zod.string(), zod.object({

}).passthrough()).optional(),
  "ip_acl": zod.record(zod.string(), zod.object({

}).passthrough()).optional(),
  "alerting": zod.record(zod.string(), zod.object({

}).passthrough()).optional(),
  "privacy": zod.record(zod.string(), zod.object({

}).passthrough()).optional(),
  "tool_policy": zod.record(zod.string(), zod.object({

}).passthrough()).optional(),
  "mcp": zod.record(zod.string(), zod.object({

}).passthrough()).optional(),
  "a2a": zod.record(zod.string(), zod.object({

}).passthrough()).optional(),
  "audit": zod.record(zod.string(), zod.object({

}).passthrough()).optional(),
  "model_database": zod.record(zod.string(), zod.object({

}).passthrough()).optional(),
  "model_map": zod.record(zod.string(), zod.object({

}).passthrough()).optional()
})

export const AgentccGatewaysUpdateConfigResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "status": zod.boolean().optional(),
  "version": zod.number().optional(),
  "gateway_synced": zod.boolean().optional(),
  "gateway_warning": zod.string().optional(),
  "action": zod.string().optional(),
  "provider": zod.string().optional(),
  "guardrail": zod.string().optional(),
  "budget": zod.string().optional(),
  "server": zod.string().optional(),
  "enabled": zod.boolean().optional()
})
})


/**
 * Stateless proxy to the Go gateway (configured via env vars).
No DB model â€” returns a virtual singleton gateway with live health.
 */
export const AgentccGatewaysUpdateGuardrailParams = zod.object({
  "id": zod.string()
})




export const AgentccGatewaysUpdateGuardrailBody = zod.object({
  "name": zod.string().min(1),
  "config": zod.record(zod.string(), zod.object({

}).passthrough().describe('Any valid JSON value.'))
})

export const AgentccGatewaysUpdateGuardrailResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "status": zod.boolean().optional(),
  "version": zod.number().optional(),
  "gateway_synced": zod.boolean().optional(),
  "gateway_warning": zod.string().optional(),
  "action": zod.string().optional(),
  "provider": zod.string().optional(),
  "guardrail": zod.string().optional(),
  "budget": zod.string().optional(),
  "server": zod.string().optional(),
  "enabled": zod.boolean().optional()
})
})


/**
 * Stateless proxy to the Go gateway (configured via env vars).
No DB model â€” returns a virtual singleton gateway with live health.
 */
export const AgentccGatewaysUpdateMcpGuardrailsParams = zod.object({
  "id": zod.string()
})

export const AgentccGatewaysUpdateMcpGuardrailsBody = zod.object({
  "config": zod.record(zod.string(), zod.object({

}).passthrough())
})

export const AgentccGatewaysUpdateMcpGuardrailsResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "status": zod.boolean().optional(),
  "version": zod.number().optional(),
  "gateway_synced": zod.boolean().optional(),
  "gateway_warning": zod.string().optional(),
  "action": zod.string().optional(),
  "provider": zod.string().optional(),
  "guardrail": zod.string().optional(),
  "budget": zod.string().optional(),
  "server": zod.string().optional(),
  "enabled": zod.boolean().optional()
})
})


/**
 * Stateless proxy to the Go gateway (configured via env vars).
No DB model â€” returns a virtual singleton gateway with live health.
 */
export const AgentccGatewaysUpdateMcpServerParams = zod.object({
  "id": zod.string()
})




export const AgentccGatewaysUpdateMcpServerBody = zod.object({
  "server_id": zod.string().min(1),
  "config": zod.record(zod.string(), zod.object({

}).passthrough())
})

export const AgentccGatewaysUpdateMcpServerResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "status": zod.boolean().optional(),
  "version": zod.number().optional(),
  "gateway_synced": zod.boolean().optional(),
  "gateway_warning": zod.string().optional(),
  "action": zod.string().optional(),
  "provider": zod.string().optional(),
  "guardrail": zod.string().optional(),
  "budget": zod.string().optional(),
  "server": zod.string().optional(),
  "enabled": zod.boolean().optional()
})
})


/**
 * Add or update a provider credential for the org, then push config.
 */
export const AgentccGatewaysUpdateProviderParams = zod.object({
  "id": zod.string()
})




export const AgentccGatewaysUpdateProviderBody = zod.object({
  "name": zod.string().min(1),
  "config": zod.record(zod.string(), zod.object({

}).passthrough().describe('Any valid JSON value.'))
})

export const AgentccGatewaysUpdateProviderResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "status": zod.boolean().optional(),
  "version": zod.number().optional(),
  "gateway_synced": zod.boolean().optional(),
  "gateway_warning": zod.string().optional(),
  "action": zod.string().optional(),
  "provider": zod.string().optional(),
  "guardrail": zod.string().optional(),
  "budget": zod.string().optional(),
  "server": zod.string().optional(),
  "enabled": zod.boolean().optional()
})
})


/**
 * List all available PII entity types.
 */
export const AgentccGuardrailConfigsPiiEntitiesQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})






export const AgentccGuardrailConfigsPiiEntitiesResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.array(zod.object({
  "id": zod.string().min(1),
  "label": zod.string().min(1),
  "category": zod.string().min(1)
}))
})


/**
 * List topic restriction categories.
 */
export const AgentccGuardrailConfigsTopicsQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})






export const AgentccGuardrailConfigsTopicsResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.array(zod.object({
  "id": zod.string().min(1),
  "label": zod.string().min(1),
  "subcategories": zod.array(zod.string().min(1))
}))
})


/**
 * Validate a CEL expression syntax.
 */



export const AgentccGuardrailConfigsValidateCelBody = zod.object({
  "expression": zod.string().min(1)
})




export const AgentccGuardrailConfigsValidateCelResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "expression": zod.string().min(1),
  "valid": zod.boolean(),
  "error": zod.string().optional()
})
})


/**
 * Feedback on guardrail decisions. Org-scoped.
 */
export const AgentccGuardrailFeedbackListQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})

export const agentccGuardrailFeedbackListResponseResultsItemCheckNameMax = 255;



export const AgentccGuardrailFeedbackListResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "organization": zod.string().uuid().optional(),
  "request_log": zod.string().uuid(),
  "check_name": zod.string().min(1).max(agentccGuardrailFeedbackListResponseResultsItemCheckNameMax),
  "feedback": zod.enum(['correct', 'false_positive', 'false_negative', 'unsure']),
  "comment": zod.string().optional(),
  "created_by": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
}))
})


/**
 * Feedback on guardrail decisions. Org-scoped.
 */
export const agentccGuardrailFeedbackCreateBodyCheckNameMax = 255;



export const AgentccGuardrailFeedbackCreateBody = zod.object({
  "request_log": zod.string().uuid(),
  "check_name": zod.string().min(1).max(agentccGuardrailFeedbackCreateBodyCheckNameMax),
  "feedback": zod.enum(['correct', 'false_positive', 'false_negative', 'unsure']),
  "comment": zod.string().optional()
})


/**
 * Aggregate feedback stats per check_name.
 */
export const AgentccGuardrailFeedbackSummaryQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})

export const agentccGuardrailFeedbackSummaryResponseResultsItemCheckNameMax = 255;



export const AgentccGuardrailFeedbackSummaryResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "organization": zod.string().uuid().optional(),
  "request_log": zod.string().uuid(),
  "check_name": zod.string().min(1).max(agentccGuardrailFeedbackSummaryResponseResultsItemCheckNameMax),
  "feedback": zod.enum(['correct', 'false_positive', 'false_negative', 'unsure']),
  "comment": zod.string().optional(),
  "created_by": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
}))
})


/**
 * Feedback on guardrail decisions. Org-scoped.
 */
export const AgentccGuardrailFeedbackReadParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc guardrail feedback.')
})

export const agentccGuardrailFeedbackReadResponseCheckNameMax = 255;



export const AgentccGuardrailFeedbackReadResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "organization": zod.string().uuid().optional(),
  "request_log": zod.string().uuid(),
  "check_name": zod.string().min(1).max(agentccGuardrailFeedbackReadResponseCheckNameMax),
  "feedback": zod.enum(['correct', 'false_positive', 'false_negative', 'unsure']),
  "comment": zod.string().optional(),
  "created_by": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})


/**
 * Feedback on guardrail decisions. Org-scoped.
 */
export const AgentccGuardrailFeedbackUpdateParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc guardrail feedback.')
})

export const agentccGuardrailFeedbackUpdateBodyCheckNameMax = 255;



export const AgentccGuardrailFeedbackUpdateBody = zod.object({
  "request_log": zod.string().uuid(),
  "check_name": zod.string().min(1).max(agentccGuardrailFeedbackUpdateBodyCheckNameMax),
  "feedback": zod.enum(['correct', 'false_positive', 'false_negative', 'unsure']),
  "comment": zod.string().optional()
})

export const agentccGuardrailFeedbackUpdateResponseCheckNameMax = 255;



export const AgentccGuardrailFeedbackUpdateResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "organization": zod.string().uuid().optional(),
  "request_log": zod.string().uuid(),
  "check_name": zod.string().min(1).max(agentccGuardrailFeedbackUpdateResponseCheckNameMax),
  "feedback": zod.enum(['correct', 'false_positive', 'false_negative', 'unsure']),
  "comment": zod.string().optional(),
  "created_by": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})


/**
 * Feedback on guardrail decisions. Org-scoped.
 */
export const AgentccGuardrailFeedbackPartialUpdateParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc guardrail feedback.')
})

export const agentccGuardrailFeedbackPartialUpdateBodyCheckNameMax = 255;



export const AgentccGuardrailFeedbackPartialUpdateBody = zod.object({
  "request_log": zod.string().uuid(),
  "check_name": zod.string().min(1).max(agentccGuardrailFeedbackPartialUpdateBodyCheckNameMax),
  "feedback": zod.enum(['correct', 'false_positive', 'false_negative', 'unsure']),
  "comment": zod.string().optional()
})

export const agentccGuardrailFeedbackPartialUpdateResponseCheckNameMax = 255;



export const AgentccGuardrailFeedbackPartialUpdateResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "organization": zod.string().uuid().optional(),
  "request_log": zod.string().uuid(),
  "check_name": zod.string().min(1).max(agentccGuardrailFeedbackPartialUpdateResponseCheckNameMax),
  "feedback": zod.enum(['correct', 'false_positive', 'false_negative', 'unsure']),
  "comment": zod.string().optional(),
  "created_by": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})


/**
 * Feedback on guardrail decisions. Org-scoped.
 */
export const AgentccGuardrailFeedbackDeleteParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc guardrail feedback.')
})


/**
 * CRUD for reusable guardrail policies. Org-scoped.
 */
export const AgentccGuardrailPoliciesListQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})

export const agentccGuardrailPoliciesListResponseResultsItemNameMax = 255;

export const agentccGuardrailPoliciesListResponseResultsItemPriorityMin = -2147483648;
export const agentccGuardrailPoliciesListResponseResultsItemPriorityMax = 2147483647;



export const AgentccGuardrailPoliciesListResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "organization": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(agentccGuardrailPoliciesListResponseResultsItemNameMax),
  "description": zod.string().optional(),
  "scope": zod.enum(['global', 'project', 'key']).optional(),
  "checks": zod.object({

}).passthrough().optional(),
  "mode": zod.enum(['enforce', 'monitor']).optional(),
  "is_active": zod.boolean().optional(),
  "priority": zod.number().min(agentccGuardrailPoliciesListResponseResultsItemPriorityMin).max(agentccGuardrailPoliciesListResponseResultsItemPriorityMax).optional(),
  "applied_keys": zod.object({

}).passthrough().optional(),
  "applied_projects": zod.object({

}).passthrough().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
}))
})


/**
 * CRUD for reusable guardrail policies. Org-scoped.
 */
export const agentccGuardrailPoliciesCreateBodyNameMax = 255;

export const agentccGuardrailPoliciesCreateBodyPriorityMin = -2147483648;
export const agentccGuardrailPoliciesCreateBodyPriorityMax = 2147483647;



export const AgentccGuardrailPoliciesCreateBody = zod.object({
  "name": zod.string().min(1).max(agentccGuardrailPoliciesCreateBodyNameMax),
  "description": zod.string().optional(),
  "scope": zod.enum(['global', 'project', 'key']).optional(),
  "checks": zod.object({

}).passthrough().optional(),
  "mode": zod.enum(['enforce', 'monitor']).optional(),
  "is_active": zod.boolean().optional(),
  "priority": zod.number().min(agentccGuardrailPoliciesCreateBodyPriorityMin).max(agentccGuardrailPoliciesCreateBodyPriorityMax).optional(),
  "applied_keys": zod.object({

}).passthrough().optional(),
  "applied_projects": zod.object({

}).passthrough().optional()
})


/**
 * Manual trigger to resync all policies to gateway.
 */
export const agentccGuardrailPoliciesSyncBodyNameMax = 255;

export const agentccGuardrailPoliciesSyncBodyPriorityMin = -2147483648;
export const agentccGuardrailPoliciesSyncBodyPriorityMax = 2147483647;



export const AgentccGuardrailPoliciesSyncBody = zod.object({
  "name": zod.string().min(1).max(agentccGuardrailPoliciesSyncBodyNameMax),
  "description": zod.string().optional(),
  "scope": zod.enum(['global', 'project', 'key']).optional(),
  "checks": zod.object({

}).passthrough().optional(),
  "mode": zod.enum(['enforce', 'monitor']).optional(),
  "is_active": zod.boolean().optional(),
  "priority": zod.number().min(agentccGuardrailPoliciesSyncBodyPriorityMin).max(agentccGuardrailPoliciesSyncBodyPriorityMax).optional(),
  "applied_keys": zod.object({

}).passthrough().optional(),
  "applied_projects": zod.object({

}).passthrough().optional()
})


/**
 * CRUD for reusable guardrail policies. Org-scoped.
 */
export const AgentccGuardrailPoliciesReadParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc guardrail policy.')
})

export const agentccGuardrailPoliciesReadResponseNameMax = 255;

export const agentccGuardrailPoliciesReadResponsePriorityMin = -2147483648;
export const agentccGuardrailPoliciesReadResponsePriorityMax = 2147483647;



export const AgentccGuardrailPoliciesReadResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "organization": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(agentccGuardrailPoliciesReadResponseNameMax),
  "description": zod.string().optional(),
  "scope": zod.enum(['global', 'project', 'key']).optional(),
  "checks": zod.object({

}).passthrough().optional(),
  "mode": zod.enum(['enforce', 'monitor']).optional(),
  "is_active": zod.boolean().optional(),
  "priority": zod.number().min(agentccGuardrailPoliciesReadResponsePriorityMin).max(agentccGuardrailPoliciesReadResponsePriorityMax).optional(),
  "applied_keys": zod.object({

}).passthrough().optional(),
  "applied_projects": zod.object({

}).passthrough().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})


/**
 * CRUD for reusable guardrail policies. Org-scoped.
 */
export const AgentccGuardrailPoliciesUpdateParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc guardrail policy.')
})

export const agentccGuardrailPoliciesUpdateBodyNameMax = 255;

export const agentccGuardrailPoliciesUpdateBodyPriorityMin = -2147483648;
export const agentccGuardrailPoliciesUpdateBodyPriorityMax = 2147483647;



export const AgentccGuardrailPoliciesUpdateBody = zod.object({
  "name": zod.string().min(1).max(agentccGuardrailPoliciesUpdateBodyNameMax),
  "description": zod.string().optional(),
  "scope": zod.enum(['global', 'project', 'key']).optional(),
  "checks": zod.object({

}).passthrough().optional(),
  "mode": zod.enum(['enforce', 'monitor']).optional(),
  "is_active": zod.boolean().optional(),
  "priority": zod.number().min(agentccGuardrailPoliciesUpdateBodyPriorityMin).max(agentccGuardrailPoliciesUpdateBodyPriorityMax).optional(),
  "applied_keys": zod.object({

}).passthrough().optional(),
  "applied_projects": zod.object({

}).passthrough().optional()
})

export const agentccGuardrailPoliciesUpdateResponseNameMax = 255;

export const agentccGuardrailPoliciesUpdateResponsePriorityMin = -2147483648;
export const agentccGuardrailPoliciesUpdateResponsePriorityMax = 2147483647;



export const AgentccGuardrailPoliciesUpdateResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "organization": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(agentccGuardrailPoliciesUpdateResponseNameMax),
  "description": zod.string().optional(),
  "scope": zod.enum(['global', 'project', 'key']).optional(),
  "checks": zod.object({

}).passthrough().optional(),
  "mode": zod.enum(['enforce', 'monitor']).optional(),
  "is_active": zod.boolean().optional(),
  "priority": zod.number().min(agentccGuardrailPoliciesUpdateResponsePriorityMin).max(agentccGuardrailPoliciesUpdateResponsePriorityMax).optional(),
  "applied_keys": zod.object({

}).passthrough().optional(),
  "applied_projects": zod.object({

}).passthrough().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})


/**
 * CRUD for reusable guardrail policies. Org-scoped.
 */
export const AgentccGuardrailPoliciesPartialUpdateParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc guardrail policy.')
})

export const agentccGuardrailPoliciesPartialUpdateBodyNameMax = 255;

export const agentccGuardrailPoliciesPartialUpdateBodyPriorityMin = -2147483648;
export const agentccGuardrailPoliciesPartialUpdateBodyPriorityMax = 2147483647;



export const AgentccGuardrailPoliciesPartialUpdateBody = zod.object({
  "name": zod.string().min(1).max(agentccGuardrailPoliciesPartialUpdateBodyNameMax),
  "description": zod.string().optional(),
  "scope": zod.enum(['global', 'project', 'key']).optional(),
  "checks": zod.object({

}).passthrough().optional(),
  "mode": zod.enum(['enforce', 'monitor']).optional(),
  "is_active": zod.boolean().optional(),
  "priority": zod.number().min(agentccGuardrailPoliciesPartialUpdateBodyPriorityMin).max(agentccGuardrailPoliciesPartialUpdateBodyPriorityMax).optional(),
  "applied_keys": zod.object({

}).passthrough().optional(),
  "applied_projects": zod.object({

}).passthrough().optional()
})

export const agentccGuardrailPoliciesPartialUpdateResponseNameMax = 255;

export const agentccGuardrailPoliciesPartialUpdateResponsePriorityMin = -2147483648;
export const agentccGuardrailPoliciesPartialUpdateResponsePriorityMax = 2147483647;



export const AgentccGuardrailPoliciesPartialUpdateResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "organization": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(agentccGuardrailPoliciesPartialUpdateResponseNameMax),
  "description": zod.string().optional(),
  "scope": zod.enum(['global', 'project', 'key']).optional(),
  "checks": zod.object({

}).passthrough().optional(),
  "mode": zod.enum(['enforce', 'monitor']).optional(),
  "is_active": zod.boolean().optional(),
  "priority": zod.number().min(agentccGuardrailPoliciesPartialUpdateResponsePriorityMin).max(agentccGuardrailPoliciesPartialUpdateResponsePriorityMax).optional(),
  "applied_keys": zod.object({

}).passthrough().optional(),
  "applied_projects": zod.object({

}).passthrough().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})


/**
 * CRUD for reusable guardrail policies. Org-scoped.
 */
export const AgentccGuardrailPoliciesDeleteParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc guardrail policy.')
})


/**
 * Apply this policy to specific keys or projects.
 */
export const AgentccGuardrailPoliciesApplyParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc guardrail policy.')
})

export const agentccGuardrailPoliciesApplyBodyNameMax = 255;

export const agentccGuardrailPoliciesApplyBodyPriorityMin = -2147483648;
export const agentccGuardrailPoliciesApplyBodyPriorityMax = 2147483647;



export const AgentccGuardrailPoliciesApplyBody = zod.object({
  "name": zod.string().min(1).max(agentccGuardrailPoliciesApplyBodyNameMax),
  "description": zod.string().optional(),
  "scope": zod.enum(['global', 'project', 'key']).optional(),
  "checks": zod.object({

}).passthrough().optional(),
  "mode": zod.enum(['enforce', 'monitor']).optional(),
  "is_active": zod.boolean().optional(),
  "priority": zod.number().min(agentccGuardrailPoliciesApplyBodyPriorityMin).max(agentccGuardrailPoliciesApplyBodyPriorityMax).optional(),
  "applied_keys": zod.object({

}).passthrough().optional(),
  "applied_projects": zod.object({

}).passthrough().optional()
})


/**
 * Per-org gateway configuration management. Configs are versioned and immutable.
 */
export const AgentccOrgConfigsListQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})

export const AgentccOrgConfigsListResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "organization": zod.string().uuid().optional(),
  "version": zod.number().optional(),
  "guardrails": zod.object({

}).passthrough().optional(),
  "routing": zod.object({

}).passthrough().optional(),
  "cache": zod.object({

}).passthrough().optional(),
  "rate_limiting": zod.object({

}).passthrough().optional(),
  "budgets": zod.object({

}).passthrough().optional(),
  "cost_tracking": zod.object({

}).passthrough().optional(),
  "ip_acl": zod.object({

}).passthrough().optional(),
  "alerting": zod.object({

}).passthrough().optional(),
  "privacy": zod.object({

}).passthrough().optional(),
  "tool_policy": zod.object({

}).passthrough().optional(),
  "mcp": zod.object({

}).passthrough().optional(),
  "a2a": zod.object({

}).passthrough().optional(),
  "audit": zod.object({

}).passthrough().optional(),
  "model_database": zod.object({

}).passthrough().optional(),
  "model_map": zod.object({

}).passthrough().optional(),
  "is_active": zod.boolean().optional(),
  "created_by": zod.string().uuid().optional(),
  "change_description": zod.string().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
}))
})


/**
 * Create a new config version. Auto-increments version and activates it.
 */
export const AgentccOrgConfigsCreateBody = zod.object({
  "guardrails": zod.object({

}).passthrough().optional(),
  "routing": zod.object({

}).passthrough().optional(),
  "cache": zod.object({

}).passthrough().optional(),
  "rate_limiting": zod.object({

}).passthrough().optional(),
  "budgets": zod.object({

}).passthrough().optional(),
  "cost_tracking": zod.object({

}).passthrough().optional(),
  "ip_acl": zod.object({

}).passthrough().optional(),
  "alerting": zod.object({

}).passthrough().optional(),
  "privacy": zod.object({

}).passthrough().optional(),
  "tool_policy": zod.object({

}).passthrough().optional(),
  "mcp": zod.object({

}).passthrough().optional(),
  "a2a": zod.object({

}).passthrough().optional(),
  "audit": zod.object({

}).passthrough().optional(),
  "model_database": zod.object({

}).passthrough().optional(),
  "model_map": zod.object({

}).passthrough().optional(),
  "change_description": zod.string().optional()
})


/**
 * Get the currently active config for the requesting user's org.
 */
export const AgentccOrgConfigsActiveQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})

export const AgentccOrgConfigsActiveResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "organization": zod.string().uuid().optional(),
  "version": zod.number().optional(),
  "guardrails": zod.object({

}).passthrough().optional(),
  "routing": zod.object({

}).passthrough().optional(),
  "cache": zod.object({

}).passthrough().optional(),
  "rate_limiting": zod.object({

}).passthrough().optional(),
  "budgets": zod.object({

}).passthrough().optional(),
  "cost_tracking": zod.object({

}).passthrough().optional(),
  "ip_acl": zod.object({

}).passthrough().optional(),
  "alerting": zod.object({

}).passthrough().optional(),
  "privacy": zod.object({

}).passthrough().optional(),
  "tool_policy": zod.object({

}).passthrough().optional(),
  "mcp": zod.object({

}).passthrough().optional(),
  "a2a": zod.object({

}).passthrough().optional(),
  "audit": zod.object({

}).passthrough().optional(),
  "model_database": zod.object({

}).passthrough().optional(),
  "model_map": zod.object({

}).passthrough().optional(),
  "is_active": zod.boolean().optional(),
  "created_by": zod.string().uuid().optional(),
  "change_description": zod.string().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
}))
})


/**
 * Bulk endpoint for gateway startup sync.
Returns all active org configs keyed by org ID.
Authenticated by admin token (not user JWT).
 */
export const AgentccOrgConfigsBulkListResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.record(zod.string(), zod.object({
  "providers": zod.record(zod.string(), zod.object({

}).passthrough()),
  "guardrails": zod.object({

}).passthrough(),
  "routing": zod.object({

}).passthrough(),
  "cache": zod.object({

}).passthrough(),
  "rate_limiting": zod.object({

}).passthrough(),
  "budgets": zod.object({

}).passthrough(),
  "cost_tracking": zod.object({

}).passthrough(),
  "ip_acl": zod.object({

}).passthrough(),
  "alerting": zod.object({

}).passthrough(),
  "privacy": zod.object({

}).passthrough(),
  "tool_policy": zod.object({

}).passthrough(),
  "mcp": zod.object({

}).passthrough(),
  "a2a": zod.object({

}).passthrough(),
  "audit": zod.object({

}).passthrough(),
  "model_database": zod.object({

}).passthrough(),
  "model_map": zod.object({

}).passthrough()
}))
})


/**
 * Per-org gateway configuration management. Configs are versioned and immutable.
 */
export const AgentccOrgConfigsReadParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc org config.')
})

export const AgentccOrgConfigsReadResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "organization": zod.string().uuid().optional(),
  "version": zod.number().optional(),
  "guardrails": zod.object({

}).passthrough().optional(),
  "routing": zod.object({

}).passthrough().optional(),
  "cache": zod.object({

}).passthrough().optional(),
  "rate_limiting": zod.object({

}).passthrough().optional(),
  "budgets": zod.object({

}).passthrough().optional(),
  "cost_tracking": zod.object({

}).passthrough().optional(),
  "ip_acl": zod.object({

}).passthrough().optional(),
  "alerting": zod.object({

}).passthrough().optional(),
  "privacy": zod.object({

}).passthrough().optional(),
  "tool_policy": zod.object({

}).passthrough().optional(),
  "mcp": zod.object({

}).passthrough().optional(),
  "a2a": zod.object({

}).passthrough().optional(),
  "audit": zod.object({

}).passthrough().optional(),
  "model_database": zod.object({

}).passthrough().optional(),
  "model_map": zod.object({

}).passthrough().optional(),
  "is_active": zod.boolean().optional(),
  "created_by": zod.string().uuid().optional(),
  "change_description": zod.string().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})


/**
 * Disabled â€” configs are immutable versions. Create a new one instead.
 */
export const AgentccOrgConfigsUpdateParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc org config.')
})

export const AgentccOrgConfigsUpdateBody = zod.object({
  "guardrails": zod.object({

}).passthrough().optional(),
  "routing": zod.object({

}).passthrough().optional(),
  "cache": zod.object({

}).passthrough().optional(),
  "rate_limiting": zod.object({

}).passthrough().optional(),
  "budgets": zod.object({

}).passthrough().optional(),
  "cost_tracking": zod.object({

}).passthrough().optional(),
  "ip_acl": zod.object({

}).passthrough().optional(),
  "alerting": zod.object({

}).passthrough().optional(),
  "privacy": zod.object({

}).passthrough().optional(),
  "tool_policy": zod.object({

}).passthrough().optional(),
  "mcp": zod.object({

}).passthrough().optional(),
  "a2a": zod.object({

}).passthrough().optional(),
  "audit": zod.object({

}).passthrough().optional(),
  "model_database": zod.object({

}).passthrough().optional(),
  "model_map": zod.object({

}).passthrough().optional(),
  "change_description": zod.string().optional()
})

export const AgentccOrgConfigsUpdateResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "organization": zod.string().uuid().optional(),
  "version": zod.number().optional(),
  "guardrails": zod.object({

}).passthrough().optional(),
  "routing": zod.object({

}).passthrough().optional(),
  "cache": zod.object({

}).passthrough().optional(),
  "rate_limiting": zod.object({

}).passthrough().optional(),
  "budgets": zod.object({

}).passthrough().optional(),
  "cost_tracking": zod.object({

}).passthrough().optional(),
  "ip_acl": zod.object({

}).passthrough().optional(),
  "alerting": zod.object({

}).passthrough().optional(),
  "privacy": zod.object({

}).passthrough().optional(),
  "tool_policy": zod.object({

}).passthrough().optional(),
  "mcp": zod.object({

}).passthrough().optional(),
  "a2a": zod.object({

}).passthrough().optional(),
  "audit": zod.object({

}).passthrough().optional(),
  "model_database": zod.object({

}).passthrough().optional(),
  "model_map": zod.object({

}).passthrough().optional(),
  "is_active": zod.boolean().optional(),
  "created_by": zod.string().uuid().optional(),
  "change_description": zod.string().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})


/**
 * Per-org gateway configuration management. Configs are versioned and immutable.
 */
export const AgentccOrgConfigsPartialUpdateParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc org config.')
})

export const AgentccOrgConfigsPartialUpdateBody = zod.object({
  "guardrails": zod.object({

}).passthrough().optional(),
  "routing": zod.object({

}).passthrough().optional(),
  "cache": zod.object({

}).passthrough().optional(),
  "rate_limiting": zod.object({

}).passthrough().optional(),
  "budgets": zod.object({

}).passthrough().optional(),
  "cost_tracking": zod.object({

}).passthrough().optional(),
  "ip_acl": zod.object({

}).passthrough().optional(),
  "alerting": zod.object({

}).passthrough().optional(),
  "privacy": zod.object({

}).passthrough().optional(),
  "tool_policy": zod.object({

}).passthrough().optional(),
  "mcp": zod.object({

}).passthrough().optional(),
  "a2a": zod.object({

}).passthrough().optional(),
  "audit": zod.object({

}).passthrough().optional(),
  "model_database": zod.object({

}).passthrough().optional(),
  "model_map": zod.object({

}).passthrough().optional(),
  "change_description": zod.string().optional()
})

export const AgentccOrgConfigsPartialUpdateResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "organization": zod.string().uuid().optional(),
  "version": zod.number().optional(),
  "guardrails": zod.object({

}).passthrough().optional(),
  "routing": zod.object({

}).passthrough().optional(),
  "cache": zod.object({

}).passthrough().optional(),
  "rate_limiting": zod.object({

}).passthrough().optional(),
  "budgets": zod.object({

}).passthrough().optional(),
  "cost_tracking": zod.object({

}).passthrough().optional(),
  "ip_acl": zod.object({

}).passthrough().optional(),
  "alerting": zod.object({

}).passthrough().optional(),
  "privacy": zod.object({

}).passthrough().optional(),
  "tool_policy": zod.object({

}).passthrough().optional(),
  "mcp": zod.object({

}).passthrough().optional(),
  "a2a": zod.object({

}).passthrough().optional(),
  "audit": zod.object({

}).passthrough().optional(),
  "model_database": zod.object({

}).passthrough().optional(),
  "model_map": zod.object({

}).passthrough().optional(),
  "is_active": zod.boolean().optional(),
  "created_by": zod.string().uuid().optional(),
  "change_description": zod.string().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})


/**
 * Soft-delete a config version. Cannot delete the active version.
 */
export const AgentccOrgConfigsDeleteParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc org config.')
})


/**
 * Roll back to a specific config version by activating it.
 */
export const AgentccOrgConfigsActivateParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc org config.')
})

export const AgentccOrgConfigsActivateBody = zod.object({
  "guardrails": zod.object({

}).passthrough().optional(),
  "routing": zod.object({

}).passthrough().optional(),
  "cache": zod.object({

}).passthrough().optional(),
  "rate_limiting": zod.object({

}).passthrough().optional(),
  "budgets": zod.object({

}).passthrough().optional(),
  "cost_tracking": zod.object({

}).passthrough().optional(),
  "ip_acl": zod.object({

}).passthrough().optional(),
  "alerting": zod.object({

}).passthrough().optional(),
  "privacy": zod.object({

}).passthrough().optional(),
  "tool_policy": zod.object({

}).passthrough().optional(),
  "mcp": zod.object({

}).passthrough().optional(),
  "a2a": zod.object({

}).passthrough().optional(),
  "audit": zod.object({

}).passthrough().optional(),
  "model_database": zod.object({

}).passthrough().optional(),
  "model_map": zod.object({

}).passthrough().optional(),
  "change_description": zod.string().optional()
})


/**
 * Compare this config version with another. Pass ?compare_to=<uuid>.
 */
export const AgentccOrgConfigsDiffParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc org config.')
})

export const AgentccOrgConfigsDiffResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "organization": zod.string().uuid().optional(),
  "version": zod.number().optional(),
  "guardrails": zod.object({

}).passthrough().optional(),
  "routing": zod.object({

}).passthrough().optional(),
  "cache": zod.object({

}).passthrough().optional(),
  "rate_limiting": zod.object({

}).passthrough().optional(),
  "budgets": zod.object({

}).passthrough().optional(),
  "cost_tracking": zod.object({

}).passthrough().optional(),
  "ip_acl": zod.object({

}).passthrough().optional(),
  "alerting": zod.object({

}).passthrough().optional(),
  "privacy": zod.object({

}).passthrough().optional(),
  "tool_policy": zod.object({

}).passthrough().optional(),
  "mcp": zod.object({

}).passthrough().optional(),
  "a2a": zod.object({

}).passthrough().optional(),
  "audit": zod.object({

}).passthrough().optional(),
  "model_database": zod.object({

}).passthrough().optional(),
  "model_map": zod.object({

}).passthrough().optional(),
  "is_active": zod.boolean().optional(),
  "created_by": zod.string().uuid().optional(),
  "change_description": zod.string().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})


export const AgentccProviderCredentialsListQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})

export const agentccProviderCredentialsListResponseResultsItemProviderNameMax = 100;

export const agentccProviderCredentialsListResponseResultsItemDisplayNameMax = 255;

export const agentccProviderCredentialsListResponseResultsItemBaseUrlMax = 500;

export const agentccProviderCredentialsListResponseResultsItemApiFormatMax = 50;

export const agentccProviderCredentialsListResponseResultsItemDefaultTimeoutSecondsMin = -2147483648;
export const agentccProviderCredentialsListResponseResultsItemDefaultTimeoutSecondsMax = 2147483647;

export const agentccProviderCredentialsListResponseResultsItemMaxConcurrentMin = -2147483648;
export const agentccProviderCredentialsListResponseResultsItemMaxConcurrentMax = 2147483647;

export const agentccProviderCredentialsListResponseResultsItemConnPoolSizeMin = -2147483648;
export const agentccProviderCredentialsListResponseResultsItemConnPoolSizeMax = 2147483647;



export const AgentccProviderCredentialsListResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "organization": zod.string().uuid().optional(),
  "workspace": zod.string().uuid().optional(),
  "provider_name": zod.string().min(1).max(agentccProviderCredentialsListResponseResultsItemProviderNameMax),
  "display_name": zod.string().max(agentccProviderCredentialsListResponseResultsItemDisplayNameMax).optional(),
  "credentials": zod.string().optional(),
  "base_url": zod.string().url().max(agentccProviderCredentialsListResponseResultsItemBaseUrlMax).optional(),
  "api_format": zod.string().min(1).max(agentccProviderCredentialsListResponseResultsItemApiFormatMax).optional(),
  "models_list": zod.object({

}).passthrough().optional(),
  "default_timeout_seconds": zod.number().min(agentccProviderCredentialsListResponseResultsItemDefaultTimeoutSecondsMin).max(agentccProviderCredentialsListResponseResultsItemDefaultTimeoutSecondsMax).optional(),
  "max_concurrent": zod.number().min(agentccProviderCredentialsListResponseResultsItemMaxConcurrentMin).max(agentccProviderCredentialsListResponseResultsItemMaxConcurrentMax).optional(),
  "conn_pool_size": zod.number().min(agentccProviderCredentialsListResponseResultsItemConnPoolSizeMin).max(agentccProviderCredentialsListResponseResultsItemConnPoolSizeMax).optional(),
  "extra_config": zod.object({

}).passthrough().optional(),
  "is_active": zod.boolean().optional(),
  "last_rotated_at": zod.string().datetime({"offset":true}).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
}))
})


export const agentccProviderCredentialsCreateBodyProviderNameMax = 100;

export const agentccProviderCredentialsCreateBodyDisplayNameMax = 255;

export const agentccProviderCredentialsCreateBodyBaseUrlMax = 500;

export const agentccProviderCredentialsCreateBodyApiFormatMax = 50;

export const agentccProviderCredentialsCreateBodyDefaultTimeoutSecondsMin = -2147483648;
export const agentccProviderCredentialsCreateBodyDefaultTimeoutSecondsMax = 2147483647;

export const agentccProviderCredentialsCreateBodyMaxConcurrentMin = -2147483648;
export const agentccProviderCredentialsCreateBodyMaxConcurrentMax = 2147483647;

export const agentccProviderCredentialsCreateBodyConnPoolSizeMin = -2147483648;
export const agentccProviderCredentialsCreateBodyConnPoolSizeMax = 2147483647;



export const AgentccProviderCredentialsCreateBody = zod.object({
  "provider_name": zod.string().min(1).max(agentccProviderCredentialsCreateBodyProviderNameMax),
  "display_name": zod.string().max(agentccProviderCredentialsCreateBodyDisplayNameMax).optional(),
  "base_url": zod.string().url().max(agentccProviderCredentialsCreateBodyBaseUrlMax).optional(),
  "api_format": zod.string().min(1).max(agentccProviderCredentialsCreateBodyApiFormatMax).optional(),
  "models_list": zod.object({

}).passthrough().optional(),
  "default_timeout_seconds": zod.number().min(agentccProviderCredentialsCreateBodyDefaultTimeoutSecondsMin).max(agentccProviderCredentialsCreateBodyDefaultTimeoutSecondsMax).optional(),
  "max_concurrent": zod.number().min(agentccProviderCredentialsCreateBodyMaxConcurrentMin).max(agentccProviderCredentialsCreateBodyMaxConcurrentMax).optional(),
  "conn_pool_size": zod.number().min(agentccProviderCredentialsCreateBodyConnPoolSizeMin).max(agentccProviderCredentialsCreateBodyConnPoolSizeMax).optional(),
  "extra_config": zod.object({

}).passthrough().optional(),
  "is_active": zod.boolean().optional(),
  "last_rotated_at": zod.string().datetime({"offset":true}).optional()
})


/**
 * Two modes:
- provider_name: look up stored credential by provider name for this org.
- api_key + base_url + api_format: use raw values (for create-mode).
 * @summary Fetch available models from a provider's API.
 */
export const agentccProviderCredentialsFetchModelsBodyProviderNameMax = 100;

export const agentccProviderCredentialsFetchModelsBodyDisplayNameMax = 255;

export const agentccProviderCredentialsFetchModelsBodyBaseUrlMax = 500;

export const agentccProviderCredentialsFetchModelsBodyApiFormatMax = 50;

export const agentccProviderCredentialsFetchModelsBodyDefaultTimeoutSecondsMin = -2147483648;
export const agentccProviderCredentialsFetchModelsBodyDefaultTimeoutSecondsMax = 2147483647;

export const agentccProviderCredentialsFetchModelsBodyMaxConcurrentMin = -2147483648;
export const agentccProviderCredentialsFetchModelsBodyMaxConcurrentMax = 2147483647;

export const agentccProviderCredentialsFetchModelsBodyConnPoolSizeMin = -2147483648;
export const agentccProviderCredentialsFetchModelsBodyConnPoolSizeMax = 2147483647;



export const AgentccProviderCredentialsFetchModelsBody = zod.object({
  "provider_name": zod.string().min(1).max(agentccProviderCredentialsFetchModelsBodyProviderNameMax),
  "display_name": zod.string().max(agentccProviderCredentialsFetchModelsBodyDisplayNameMax).optional(),
  "base_url": zod.string().url().max(agentccProviderCredentialsFetchModelsBodyBaseUrlMax).optional(),
  "api_format": zod.string().min(1).max(agentccProviderCredentialsFetchModelsBodyApiFormatMax).optional(),
  "models_list": zod.object({

}).passthrough().optional(),
  "default_timeout_seconds": zod.number().min(agentccProviderCredentialsFetchModelsBodyDefaultTimeoutSecondsMin).max(agentccProviderCredentialsFetchModelsBodyDefaultTimeoutSecondsMax).optional(),
  "max_concurrent": zod.number().min(agentccProviderCredentialsFetchModelsBodyMaxConcurrentMin).max(agentccProviderCredentialsFetchModelsBodyMaxConcurrentMax).optional(),
  "conn_pool_size": zod.number().min(agentccProviderCredentialsFetchModelsBodyConnPoolSizeMin).max(agentccProviderCredentialsFetchModelsBodyConnPoolSizeMax).optional(),
  "extra_config": zod.object({

}).passthrough().optional(),
  "is_active": zod.boolean().optional(),
  "last_rotated_at": zod.string().datetime({"offset":true}).optional()
})


export const AgentccProviderCredentialsReadParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc provider credential.')
})

export const agentccProviderCredentialsReadResponseProviderNameMax = 100;

export const agentccProviderCredentialsReadResponseDisplayNameMax = 255;

export const agentccProviderCredentialsReadResponseBaseUrlMax = 500;

export const agentccProviderCredentialsReadResponseApiFormatMax = 50;

export const agentccProviderCredentialsReadResponseDefaultTimeoutSecondsMin = -2147483648;
export const agentccProviderCredentialsReadResponseDefaultTimeoutSecondsMax = 2147483647;

export const agentccProviderCredentialsReadResponseMaxConcurrentMin = -2147483648;
export const agentccProviderCredentialsReadResponseMaxConcurrentMax = 2147483647;

export const agentccProviderCredentialsReadResponseConnPoolSizeMin = -2147483648;
export const agentccProviderCredentialsReadResponseConnPoolSizeMax = 2147483647;



export const AgentccProviderCredentialsReadResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "organization": zod.string().uuid().optional(),
  "workspace": zod.string().uuid().optional(),
  "provider_name": zod.string().min(1).max(agentccProviderCredentialsReadResponseProviderNameMax),
  "display_name": zod.string().max(agentccProviderCredentialsReadResponseDisplayNameMax).optional(),
  "credentials": zod.string().optional(),
  "base_url": zod.string().url().max(agentccProviderCredentialsReadResponseBaseUrlMax).optional(),
  "api_format": zod.string().min(1).max(agentccProviderCredentialsReadResponseApiFormatMax).optional(),
  "models_list": zod.object({

}).passthrough().optional(),
  "default_timeout_seconds": zod.number().min(agentccProviderCredentialsReadResponseDefaultTimeoutSecondsMin).max(agentccProviderCredentialsReadResponseDefaultTimeoutSecondsMax).optional(),
  "max_concurrent": zod.number().min(agentccProviderCredentialsReadResponseMaxConcurrentMin).max(agentccProviderCredentialsReadResponseMaxConcurrentMax).optional(),
  "conn_pool_size": zod.number().min(agentccProviderCredentialsReadResponseConnPoolSizeMin).max(agentccProviderCredentialsReadResponseConnPoolSizeMax).optional(),
  "extra_config": zod.object({

}).passthrough().optional(),
  "is_active": zod.boolean().optional(),
  "last_rotated_at": zod.string().datetime({"offset":true}).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})


export const AgentccProviderCredentialsUpdateParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc provider credential.')
})

export const agentccProviderCredentialsUpdateBodyProviderNameMax = 100;

export const agentccProviderCredentialsUpdateBodyDisplayNameMax = 255;

export const agentccProviderCredentialsUpdateBodyBaseUrlMax = 500;

export const agentccProviderCredentialsUpdateBodyApiFormatMax = 50;

export const agentccProviderCredentialsUpdateBodyDefaultTimeoutSecondsMin = -2147483648;
export const agentccProviderCredentialsUpdateBodyDefaultTimeoutSecondsMax = 2147483647;

export const agentccProviderCredentialsUpdateBodyMaxConcurrentMin = -2147483648;
export const agentccProviderCredentialsUpdateBodyMaxConcurrentMax = 2147483647;

export const agentccProviderCredentialsUpdateBodyConnPoolSizeMin = -2147483648;
export const agentccProviderCredentialsUpdateBodyConnPoolSizeMax = 2147483647;



export const AgentccProviderCredentialsUpdateBody = zod.object({
  "provider_name": zod.string().min(1).max(agentccProviderCredentialsUpdateBodyProviderNameMax),
  "display_name": zod.string().max(agentccProviderCredentialsUpdateBodyDisplayNameMax).optional(),
  "base_url": zod.string().url().max(agentccProviderCredentialsUpdateBodyBaseUrlMax).optional(),
  "api_format": zod.string().min(1).max(agentccProviderCredentialsUpdateBodyApiFormatMax).optional(),
  "models_list": zod.object({

}).passthrough().optional(),
  "default_timeout_seconds": zod.number().min(agentccProviderCredentialsUpdateBodyDefaultTimeoutSecondsMin).max(agentccProviderCredentialsUpdateBodyDefaultTimeoutSecondsMax).optional(),
  "max_concurrent": zod.number().min(agentccProviderCredentialsUpdateBodyMaxConcurrentMin).max(agentccProviderCredentialsUpdateBodyMaxConcurrentMax).optional(),
  "conn_pool_size": zod.number().min(agentccProviderCredentialsUpdateBodyConnPoolSizeMin).max(agentccProviderCredentialsUpdateBodyConnPoolSizeMax).optional(),
  "extra_config": zod.object({

}).passthrough().optional(),
  "is_active": zod.boolean().optional(),
  "last_rotated_at": zod.string().datetime({"offset":true}).optional()
})

export const agentccProviderCredentialsUpdateResponseProviderNameMax = 100;

export const agentccProviderCredentialsUpdateResponseDisplayNameMax = 255;

export const agentccProviderCredentialsUpdateResponseBaseUrlMax = 500;

export const agentccProviderCredentialsUpdateResponseApiFormatMax = 50;

export const agentccProviderCredentialsUpdateResponseDefaultTimeoutSecondsMin = -2147483648;
export const agentccProviderCredentialsUpdateResponseDefaultTimeoutSecondsMax = 2147483647;

export const agentccProviderCredentialsUpdateResponseMaxConcurrentMin = -2147483648;
export const agentccProviderCredentialsUpdateResponseMaxConcurrentMax = 2147483647;

export const agentccProviderCredentialsUpdateResponseConnPoolSizeMin = -2147483648;
export const agentccProviderCredentialsUpdateResponseConnPoolSizeMax = 2147483647;



export const AgentccProviderCredentialsUpdateResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "organization": zod.string().uuid().optional(),
  "workspace": zod.string().uuid().optional(),
  "provider_name": zod.string().min(1).max(agentccProviderCredentialsUpdateResponseProviderNameMax),
  "display_name": zod.string().max(agentccProviderCredentialsUpdateResponseDisplayNameMax).optional(),
  "credentials": zod.string().optional(),
  "base_url": zod.string().url().max(agentccProviderCredentialsUpdateResponseBaseUrlMax).optional(),
  "api_format": zod.string().min(1).max(agentccProviderCredentialsUpdateResponseApiFormatMax).optional(),
  "models_list": zod.object({

}).passthrough().optional(),
  "default_timeout_seconds": zod.number().min(agentccProviderCredentialsUpdateResponseDefaultTimeoutSecondsMin).max(agentccProviderCredentialsUpdateResponseDefaultTimeoutSecondsMax).optional(),
  "max_concurrent": zod.number().min(agentccProviderCredentialsUpdateResponseMaxConcurrentMin).max(agentccProviderCredentialsUpdateResponseMaxConcurrentMax).optional(),
  "conn_pool_size": zod.number().min(agentccProviderCredentialsUpdateResponseConnPoolSizeMin).max(agentccProviderCredentialsUpdateResponseConnPoolSizeMax).optional(),
  "extra_config": zod.object({

}).passthrough().optional(),
  "is_active": zod.boolean().optional(),
  "last_rotated_at": zod.string().datetime({"offset":true}).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})


export const AgentccProviderCredentialsPartialUpdateParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc provider credential.')
})

export const agentccProviderCredentialsPartialUpdateBodyProviderNameMax = 100;

export const agentccProviderCredentialsPartialUpdateBodyDisplayNameMax = 255;

export const agentccProviderCredentialsPartialUpdateBodyBaseUrlMax = 500;

export const agentccProviderCredentialsPartialUpdateBodyApiFormatMax = 50;

export const agentccProviderCredentialsPartialUpdateBodyDefaultTimeoutSecondsMin = -2147483648;
export const agentccProviderCredentialsPartialUpdateBodyDefaultTimeoutSecondsMax = 2147483647;

export const agentccProviderCredentialsPartialUpdateBodyMaxConcurrentMin = -2147483648;
export const agentccProviderCredentialsPartialUpdateBodyMaxConcurrentMax = 2147483647;

export const agentccProviderCredentialsPartialUpdateBodyConnPoolSizeMin = -2147483648;
export const agentccProviderCredentialsPartialUpdateBodyConnPoolSizeMax = 2147483647;



export const AgentccProviderCredentialsPartialUpdateBody = zod.object({
  "provider_name": zod.string().min(1).max(agentccProviderCredentialsPartialUpdateBodyProviderNameMax),
  "display_name": zod.string().max(agentccProviderCredentialsPartialUpdateBodyDisplayNameMax).optional(),
  "base_url": zod.string().url().max(agentccProviderCredentialsPartialUpdateBodyBaseUrlMax).optional(),
  "api_format": zod.string().min(1).max(agentccProviderCredentialsPartialUpdateBodyApiFormatMax).optional(),
  "models_list": zod.object({

}).passthrough().optional(),
  "default_timeout_seconds": zod.number().min(agentccProviderCredentialsPartialUpdateBodyDefaultTimeoutSecondsMin).max(agentccProviderCredentialsPartialUpdateBodyDefaultTimeoutSecondsMax).optional(),
  "max_concurrent": zod.number().min(agentccProviderCredentialsPartialUpdateBodyMaxConcurrentMin).max(agentccProviderCredentialsPartialUpdateBodyMaxConcurrentMax).optional(),
  "conn_pool_size": zod.number().min(agentccProviderCredentialsPartialUpdateBodyConnPoolSizeMin).max(agentccProviderCredentialsPartialUpdateBodyConnPoolSizeMax).optional(),
  "extra_config": zod.object({

}).passthrough().optional(),
  "is_active": zod.boolean().optional(),
  "last_rotated_at": zod.string().datetime({"offset":true}).optional()
})

export const agentccProviderCredentialsPartialUpdateResponseProviderNameMax = 100;

export const agentccProviderCredentialsPartialUpdateResponseDisplayNameMax = 255;

export const agentccProviderCredentialsPartialUpdateResponseBaseUrlMax = 500;

export const agentccProviderCredentialsPartialUpdateResponseApiFormatMax = 50;

export const agentccProviderCredentialsPartialUpdateResponseDefaultTimeoutSecondsMin = -2147483648;
export const agentccProviderCredentialsPartialUpdateResponseDefaultTimeoutSecondsMax = 2147483647;

export const agentccProviderCredentialsPartialUpdateResponseMaxConcurrentMin = -2147483648;
export const agentccProviderCredentialsPartialUpdateResponseMaxConcurrentMax = 2147483647;

export const agentccProviderCredentialsPartialUpdateResponseConnPoolSizeMin = -2147483648;
export const agentccProviderCredentialsPartialUpdateResponseConnPoolSizeMax = 2147483647;



export const AgentccProviderCredentialsPartialUpdateResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "organization": zod.string().uuid().optional(),
  "workspace": zod.string().uuid().optional(),
  "provider_name": zod.string().min(1).max(agentccProviderCredentialsPartialUpdateResponseProviderNameMax),
  "display_name": zod.string().max(agentccProviderCredentialsPartialUpdateResponseDisplayNameMax).optional(),
  "credentials": zod.string().optional(),
  "base_url": zod.string().url().max(agentccProviderCredentialsPartialUpdateResponseBaseUrlMax).optional(),
  "api_format": zod.string().min(1).max(agentccProviderCredentialsPartialUpdateResponseApiFormatMax).optional(),
  "models_list": zod.object({

}).passthrough().optional(),
  "default_timeout_seconds": zod.number().min(agentccProviderCredentialsPartialUpdateResponseDefaultTimeoutSecondsMin).max(agentccProviderCredentialsPartialUpdateResponseDefaultTimeoutSecondsMax).optional(),
  "max_concurrent": zod.number().min(agentccProviderCredentialsPartialUpdateResponseMaxConcurrentMin).max(agentccProviderCredentialsPartialUpdateResponseMaxConcurrentMax).optional(),
  "conn_pool_size": zod.number().min(agentccProviderCredentialsPartialUpdateResponseConnPoolSizeMin).max(agentccProviderCredentialsPartialUpdateResponseConnPoolSizeMax).optional(),
  "extra_config": zod.object({

}).passthrough().optional(),
  "is_active": zod.boolean().optional(),
  "last_rotated_at": zod.string().datetime({"offset":true}).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})


export const AgentccProviderCredentialsDeleteParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc provider credential.')
})


/**
 * Rotate credentials â€” accepts new credentials, encrypts, and updates.
 */
export const AgentccProviderCredentialsRotateParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc provider credential.')
})

export const agentccProviderCredentialsRotateBodyProviderNameMax = 100;

export const agentccProviderCredentialsRotateBodyDisplayNameMax = 255;

export const agentccProviderCredentialsRotateBodyBaseUrlMax = 500;

export const agentccProviderCredentialsRotateBodyApiFormatMax = 50;

export const agentccProviderCredentialsRotateBodyDefaultTimeoutSecondsMin = -2147483648;
export const agentccProviderCredentialsRotateBodyDefaultTimeoutSecondsMax = 2147483647;

export const agentccProviderCredentialsRotateBodyMaxConcurrentMin = -2147483648;
export const agentccProviderCredentialsRotateBodyMaxConcurrentMax = 2147483647;

export const agentccProviderCredentialsRotateBodyConnPoolSizeMin = -2147483648;
export const agentccProviderCredentialsRotateBodyConnPoolSizeMax = 2147483647;



export const AgentccProviderCredentialsRotateBody = zod.object({
  "provider_name": zod.string().min(1).max(agentccProviderCredentialsRotateBodyProviderNameMax),
  "display_name": zod.string().max(agentccProviderCredentialsRotateBodyDisplayNameMax).optional(),
  "base_url": zod.string().url().max(agentccProviderCredentialsRotateBodyBaseUrlMax).optional(),
  "api_format": zod.string().min(1).max(agentccProviderCredentialsRotateBodyApiFormatMax).optional(),
  "models_list": zod.object({

}).passthrough().optional(),
  "default_timeout_seconds": zod.number().min(agentccProviderCredentialsRotateBodyDefaultTimeoutSecondsMin).max(agentccProviderCredentialsRotateBodyDefaultTimeoutSecondsMax).optional(),
  "max_concurrent": zod.number().min(agentccProviderCredentialsRotateBodyMaxConcurrentMin).max(agentccProviderCredentialsRotateBodyMaxConcurrentMax).optional(),
  "conn_pool_size": zod.number().min(agentccProviderCredentialsRotateBodyConnPoolSizeMin).max(agentccProviderCredentialsRotateBodyConnPoolSizeMax).optional(),
  "extra_config": zod.object({

}).passthrough().optional(),
  "is_active": zod.boolean().optional(),
  "last_rotated_at": zod.string().datetime({"offset":true}).optional()
})


export const AgentccRequestLogsListQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})












export const AgentccRequestLogsListResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "request_id": zod.string().min(1).optional(),
  "model": zod.string().min(1).optional(),
  "provider": zod.string().min(1).optional(),
  "resolved_model": zod.string().min(1).optional(),
  "latency_ms": zod.number().optional(),
  "started_at": zod.string().datetime({"offset":true}).optional(),
  "input_tokens": zod.number().optional(),
  "output_tokens": zod.number().optional(),
  "total_tokens": zod.number().optional(),
  "cost": zod.string().optional(),
  "status_code": zod.number().optional(),
  "is_stream": zod.boolean().optional(),
  "is_error": zod.boolean().optional(),
  "error_message": zod.string().min(1).optional(),
  "cache_hit": zod.boolean().optional(),
  "fallback_used": zod.boolean().optional(),
  "guardrail_triggered": zod.boolean().optional(),
  "api_key_id": zod.string().min(1).optional(),
  "user_id": zod.string().min(1).optional(),
  "session_id": zod.string().min(1).optional(),
  "routing_strategy": zod.string().min(1).optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "organization": zod.string().uuid().optional(),
  "workspace": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional()
}))
})


/**
 * Export filtered request logs as CSV or JSON.
 */
export const AgentccRequestLogsExportQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})












export const AgentccRequestLogsExportResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "request_id": zod.string().min(1).optional(),
  "model": zod.string().min(1).optional(),
  "provider": zod.string().min(1).optional(),
  "resolved_model": zod.string().min(1).optional(),
  "latency_ms": zod.number().optional(),
  "started_at": zod.string().datetime({"offset":true}).optional(),
  "input_tokens": zod.number().optional(),
  "output_tokens": zod.number().optional(),
  "total_tokens": zod.number().optional(),
  "cost": zod.string().optional(),
  "status_code": zod.number().optional(),
  "is_stream": zod.boolean().optional(),
  "is_error": zod.boolean().optional(),
  "error_message": zod.string().min(1).optional(),
  "cache_hit": zod.boolean().optional(),
  "fallback_used": zod.boolean().optional(),
  "guardrail_triggered": zod.boolean().optional(),
  "api_key_id": zod.string().min(1).optional(),
  "user_id": zod.string().min(1).optional(),
  "session_id": zod.string().min(1).optional(),
  "routing_strategy": zod.string().min(1).optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "organization": zod.string().uuid().optional(),
  "workspace": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional()
}))
})


/**
 * Full-text search across model, provider, error_message, request_id.
 */
export const AgentccRequestLogsSearchQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})












export const AgentccRequestLogsSearchResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "request_id": zod.string().min(1).optional(),
  "model": zod.string().min(1).optional(),
  "provider": zod.string().min(1).optional(),
  "resolved_model": zod.string().min(1).optional(),
  "latency_ms": zod.number().optional(),
  "started_at": zod.string().datetime({"offset":true}).optional(),
  "input_tokens": zod.number().optional(),
  "output_tokens": zod.number().optional(),
  "total_tokens": zod.number().optional(),
  "cost": zod.string().optional(),
  "status_code": zod.number().optional(),
  "is_stream": zod.boolean().optional(),
  "is_error": zod.boolean().optional(),
  "error_message": zod.string().min(1).optional(),
  "cache_hit": zod.boolean().optional(),
  "fallback_used": zod.boolean().optional(),
  "guardrail_triggered": zod.boolean().optional(),
  "api_key_id": zod.string().min(1).optional(),
  "user_id": zod.string().min(1).optional(),
  "session_id": zod.string().min(1).optional(),
  "routing_strategy": zod.string().min(1).optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "organization": zod.string().uuid().optional(),
  "workspace": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional()
}))
})


/**
 * Aggregate request logs by session_id.
 */
export const AgentccRequestLogsSessionsQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})












export const AgentccRequestLogsSessionsResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "request_id": zod.string().min(1).optional(),
  "model": zod.string().min(1).optional(),
  "provider": zod.string().min(1).optional(),
  "resolved_model": zod.string().min(1).optional(),
  "latency_ms": zod.number().optional(),
  "started_at": zod.string().datetime({"offset":true}).optional(),
  "input_tokens": zod.number().optional(),
  "output_tokens": zod.number().optional(),
  "total_tokens": zod.number().optional(),
  "cost": zod.string().optional(),
  "status_code": zod.number().optional(),
  "is_stream": zod.boolean().optional(),
  "is_error": zod.boolean().optional(),
  "error_message": zod.string().min(1).optional(),
  "cache_hit": zod.boolean().optional(),
  "fallback_used": zod.boolean().optional(),
  "guardrail_triggered": zod.boolean().optional(),
  "api_key_id": zod.string().min(1).optional(),
  "user_id": zod.string().min(1).optional(),
  "session_id": zod.string().min(1).optional(),
  "routing_strategy": zod.string().min(1).optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "organization": zod.string().uuid().optional(),
  "workspace": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional()
}))
})


/**
 * Get all logs for a specific session.
 */
export const AgentccRequestLogsSessionDetailParams = zod.object({
  "session_id": zod.string()
})

export const AgentccRequestLogsSessionDetailQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})












export const AgentccRequestLogsSessionDetailResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "request_id": zod.string().min(1).optional(),
  "model": zod.string().min(1).optional(),
  "provider": zod.string().min(1).optional(),
  "resolved_model": zod.string().min(1).optional(),
  "latency_ms": zod.number().optional(),
  "started_at": zod.string().datetime({"offset":true}).optional(),
  "input_tokens": zod.number().optional(),
  "output_tokens": zod.number().optional(),
  "total_tokens": zod.number().optional(),
  "cost": zod.string().optional(),
  "status_code": zod.number().optional(),
  "is_stream": zod.boolean().optional(),
  "is_error": zod.boolean().optional(),
  "error_message": zod.string().min(1).optional(),
  "cache_hit": zod.boolean().optional(),
  "fallback_used": zod.boolean().optional(),
  "guardrail_triggered": zod.boolean().optional(),
  "api_key_id": zod.string().min(1).optional(),
  "user_id": zod.string().min(1).optional(),
  "session_id": zod.string().min(1).optional(),
  "routing_strategy": zod.string().min(1).optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "organization": zod.string().uuid().optional(),
  "workspace": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional()
}))
})


export const AgentccRequestLogsReadParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc request log.')
})












export const AgentccRequestLogsReadResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "request_id": zod.string().min(1).optional(),
  "model": zod.string().min(1).optional(),
  "provider": zod.string().min(1).optional(),
  "resolved_model": zod.string().min(1).optional(),
  "latency_ms": zod.number().optional(),
  "started_at": zod.string().datetime({"offset":true}).optional(),
  "input_tokens": zod.number().optional(),
  "output_tokens": zod.number().optional(),
  "total_tokens": zod.number().optional(),
  "cost": zod.string().optional(),
  "status_code": zod.number().optional(),
  "is_stream": zod.boolean().optional(),
  "is_error": zod.boolean().optional(),
  "error_message": zod.string().min(1).optional(),
  "cache_hit": zod.boolean().optional(),
  "fallback_used": zod.boolean().optional(),
  "guardrail_triggered": zod.boolean().optional(),
  "api_key_id": zod.string().min(1).optional(),
  "user_id": zod.string().min(1).optional(),
  "session_id": zod.string().min(1).optional(),
  "routing_strategy": zod.string().min(1).optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "request_body": zod.object({

}).passthrough().optional(),
  "response_body": zod.object({

}).passthrough().optional(),
  "request_headers": zod.object({

}).passthrough().optional(),
  "response_headers": zod.object({

}).passthrough().optional(),
  "guardrail_results": zod.object({

}).passthrough().optional(),
  "organization": zod.string().uuid().optional(),
  "workspace": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional()
})


/**
 * Routing policy management with version history. Org-scoped.
 */
export const AgentccRoutingPoliciesListQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})

export const agentccRoutingPoliciesListResponseResultsItemNameMax = 255;



export const AgentccRoutingPoliciesListResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "organization": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(agentccRoutingPoliciesListResponseResultsItemNameMax),
  "description": zod.string().optional(),
  "version": zod.number().optional(),
  "config": zod.object({

}).passthrough().optional(),
  "is_active": zod.boolean().optional(),
  "created_by": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
}))
})


/**
 * Create a new routing policy. Auto-increments version for same name.
 */
export const agentccRoutingPoliciesCreateBodyNameMax = 255;



export const AgentccRoutingPoliciesCreateBody = zod.object({
  "name": zod.string().min(1).max(agentccRoutingPoliciesCreateBodyNameMax),
  "description": zod.string().optional(),
  "config": zod.object({

}).passthrough().optional(),
  "is_active": zod.boolean().optional()
})


/**
 * Manual sync all active routing policies to gateway.
 */
export const agentccRoutingPoliciesSyncBodyNameMax = 255;



export const AgentccRoutingPoliciesSyncBody = zod.object({
  "name": zod.string().min(1).max(agentccRoutingPoliciesSyncBodyNameMax),
  "description": zod.string().optional(),
  "config": zod.object({

}).passthrough().optional(),
  "is_active": zod.boolean().optional()
})


/**
 * Routing policy management with version history. Org-scoped.
 */
export const AgentccRoutingPoliciesReadParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc routing policy.')
})

export const agentccRoutingPoliciesReadResponseNameMax = 255;



export const AgentccRoutingPoliciesReadResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "organization": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(agentccRoutingPoliciesReadResponseNameMax),
  "description": zod.string().optional(),
  "version": zod.number().optional(),
  "config": zod.object({

}).passthrough().optional(),
  "is_active": zod.boolean().optional(),
  "created_by": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})


/**
 * Updates are disabled â€” create a new version instead.
 */
export const AgentccRoutingPoliciesUpdateParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc routing policy.')
})

export const agentccRoutingPoliciesUpdateBodyNameMax = 255;



export const AgentccRoutingPoliciesUpdateBody = zod.object({
  "name": zod.string().min(1).max(agentccRoutingPoliciesUpdateBodyNameMax),
  "description": zod.string().optional(),
  "config": zod.object({

}).passthrough().optional(),
  "is_active": zod.boolean().optional()
})

export const agentccRoutingPoliciesUpdateResponseNameMax = 255;



export const AgentccRoutingPoliciesUpdateResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "organization": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(agentccRoutingPoliciesUpdateResponseNameMax),
  "description": zod.string().optional(),
  "version": zod.number().optional(),
  "config": zod.object({

}).passthrough().optional(),
  "is_active": zod.boolean().optional(),
  "created_by": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})


/**
 * Routing policy management with version history. Org-scoped.
 */
export const AgentccRoutingPoliciesPartialUpdateParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc routing policy.')
})

export const agentccRoutingPoliciesPartialUpdateBodyNameMax = 255;



export const AgentccRoutingPoliciesPartialUpdateBody = zod.object({
  "name": zod.string().min(1).max(agentccRoutingPoliciesPartialUpdateBodyNameMax),
  "description": zod.string().optional(),
  "config": zod.object({

}).passthrough().optional(),
  "is_active": zod.boolean().optional()
})

export const agentccRoutingPoliciesPartialUpdateResponseNameMax = 255;



export const AgentccRoutingPoliciesPartialUpdateResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "organization": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(agentccRoutingPoliciesPartialUpdateResponseNameMax),
  "description": zod.string().optional(),
  "version": zod.number().optional(),
  "config": zod.object({

}).passthrough().optional(),
  "is_active": zod.boolean().optional(),
  "created_by": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})


/**
 * Routing policy management with version history. Org-scoped.
 */
export const AgentccRoutingPoliciesDeleteParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc routing policy.')
})


/**
 * Activate a specific version (rollback).
 */
export const AgentccRoutingPoliciesActivateParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc routing policy.')
})

export const agentccRoutingPoliciesActivateBodyNameMax = 255;



export const AgentccRoutingPoliciesActivateBody = zod.object({
  "name": zod.string().min(1).max(agentccRoutingPoliciesActivateBodyNameMax),
  "description": zod.string().optional(),
  "config": zod.object({

}).passthrough().optional(),
  "is_active": zod.boolean().optional()
})


/**
 * Session management â€” create, list, retrieve sessions with stats.
 */
export const AgentccSessionsListQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})

export const agentccSessionsListResponseResultsItemSessionIdMax = 255;

export const agentccSessionsListResponseResultsItemNameMax = 255;



export const AgentccSessionsListResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "organization": zod.string().uuid().optional(),
  "session_id": zod.string().min(1).max(agentccSessionsListResponseResultsItemSessionIdMax),
  "name": zod.string().max(agentccSessionsListResponseResultsItemNameMax).optional(),
  "status": zod.enum(['active', 'closed']).optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
}))
})


/**
 * Session management â€” create, list, retrieve sessions with stats.
 */
export const agentccSessionsCreateBodySessionIdMax = 255;

export const agentccSessionsCreateBodyNameMax = 255;



export const AgentccSessionsCreateBody = zod.object({
  "session_id": zod.string().min(1).max(agentccSessionsCreateBodySessionIdMax),
  "name": zod.string().max(agentccSessionsCreateBodyNameMax).optional(),
  "status": zod.enum(['active', 'closed']).optional(),
  "metadata": zod.object({

}).passthrough().optional()
})


/**
 * Session management â€” create, list, retrieve sessions with stats.
 */
export const AgentccSessionsReadParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc session.')
})

export const agentccSessionsReadResponseSessionIdMax = 255;

export const agentccSessionsReadResponseNameMax = 255;



export const AgentccSessionsReadResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "organization": zod.string().uuid().optional(),
  "session_id": zod.string().min(1).max(agentccSessionsReadResponseSessionIdMax),
  "name": zod.string().max(agentccSessionsReadResponseNameMax).optional(),
  "status": zod.enum(['active', 'closed']).optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})


/**
 * Session management â€” create, list, retrieve sessions with stats.
 */
export const AgentccSessionsUpdateParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc session.')
})

export const agentccSessionsUpdateBodySessionIdMax = 255;

export const agentccSessionsUpdateBodyNameMax = 255;



export const AgentccSessionsUpdateBody = zod.object({
  "session_id": zod.string().min(1).max(agentccSessionsUpdateBodySessionIdMax),
  "name": zod.string().max(agentccSessionsUpdateBodyNameMax).optional(),
  "status": zod.enum(['active', 'closed']).optional(),
  "metadata": zod.object({

}).passthrough().optional()
})

export const agentccSessionsUpdateResponseSessionIdMax = 255;

export const agentccSessionsUpdateResponseNameMax = 255;



export const AgentccSessionsUpdateResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "organization": zod.string().uuid().optional(),
  "session_id": zod.string().min(1).max(agentccSessionsUpdateResponseSessionIdMax),
  "name": zod.string().max(agentccSessionsUpdateResponseNameMax).optional(),
  "status": zod.enum(['active', 'closed']).optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})


/**
 * Session management â€” create, list, retrieve sessions with stats.
 */
export const AgentccSessionsPartialUpdateParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc session.')
})

export const agentccSessionsPartialUpdateBodySessionIdMax = 255;

export const agentccSessionsPartialUpdateBodyNameMax = 255;



export const AgentccSessionsPartialUpdateBody = zod.object({
  "session_id": zod.string().min(1).max(agentccSessionsPartialUpdateBodySessionIdMax),
  "name": zod.string().max(agentccSessionsPartialUpdateBodyNameMax).optional(),
  "status": zod.enum(['active', 'closed']).optional(),
  "metadata": zod.object({

}).passthrough().optional()
})

export const agentccSessionsPartialUpdateResponseSessionIdMax = 255;

export const agentccSessionsPartialUpdateResponseNameMax = 255;



export const AgentccSessionsPartialUpdateResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "organization": zod.string().uuid().optional(),
  "session_id": zod.string().min(1).max(agentccSessionsPartialUpdateResponseSessionIdMax),
  "name": zod.string().max(agentccSessionsPartialUpdateResponseNameMax).optional(),
  "status": zod.enum(['active', 'closed']).optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})


/**
 * Session management â€” create, list, retrieve sessions with stats.
 */
export const AgentccSessionsDeleteParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc session.')
})


/**
 * Close a session.
 */
export const AgentccSessionsCloseParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc session.')
})

export const agentccSessionsCloseBodySessionIdMax = 255;

export const agentccSessionsCloseBodyNameMax = 255;



export const AgentccSessionsCloseBody = zod.object({
  "session_id": zod.string().min(1).max(agentccSessionsCloseBodySessionIdMax),
  "name": zod.string().max(agentccSessionsCloseBodyNameMax).optional(),
  "status": zod.enum(['active', 'closed']).optional(),
  "metadata": zod.object({

}).passthrough().optional()
})


/**
 * List all request logs for this session.
 */
export const AgentccSessionsRequestsParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc session.')
})

export const agentccSessionsRequestsResponseSessionIdMax = 255;

export const agentccSessionsRequestsResponseNameMax = 255;



export const AgentccSessionsRequestsResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "organization": zod.string().uuid().optional(),
  "session_id": zod.string().min(1).max(agentccSessionsRequestsResponseSessionIdMax),
  "name": zod.string().max(agentccSessionsRequestsResponseNameMax).optional(),
  "status": zod.enum(['active', 'closed']).optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})


/**
 * CRUD for shadow experiments with pause/resume/complete lifecycle actions.
 */
export const AgentccShadowExperimentsListQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})

export const agentccShadowExperimentsListResponseResultsItemNameMax = 128;

export const agentccShadowExperimentsListResponseResultsItemSourceModelMax = 255;

export const agentccShadowExperimentsListResponseResultsItemShadowModelMax = 255;

export const agentccShadowExperimentsListResponseResultsItemShadowProviderMax = 128;



export const AgentccShadowExperimentsListResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(agentccShadowExperimentsListResponseResultsItemNameMax),
  "description": zod.string().optional(),
  "source_model": zod.string().min(1).max(agentccShadowExperimentsListResponseResultsItemSourceModelMax).describe('Production model being tested against'),
  "shadow_model": zod.string().min(1).max(agentccShadowExperimentsListResponseResultsItemShadowModelMax).describe('Shadow model receiving mirrored traffic'),
  "shadow_provider": zod.string().min(1).max(agentccShadowExperimentsListResponseResultsItemShadowProviderMax).describe('Provider for the shadow model'),
  "sample_rate": zod.number().optional().describe('Fraction of traffic to mirror (0.0â€“1.0)'),
  "status": zod.enum(['active', 'paused', 'completed']).optional(),
  "total_comparisons": zod.number().optional(),
  "config": zod.object({

}).passthrough().optional().describe('Extra configuration'),
  "created_by": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
}))
})


/**
 * CRUD for shadow experiments with pause/resume/complete lifecycle actions.
 */
export const agentccShadowExperimentsCreateBodyNameMax = 128;

export const agentccShadowExperimentsCreateBodySourceModelMax = 255;

export const agentccShadowExperimentsCreateBodyShadowModelMax = 255;

export const agentccShadowExperimentsCreateBodyShadowProviderMax = 128;



export const AgentccShadowExperimentsCreateBody = zod.object({
  "name": zod.string().min(1).max(agentccShadowExperimentsCreateBodyNameMax),
  "description": zod.string().optional(),
  "source_model": zod.string().min(1).max(agentccShadowExperimentsCreateBodySourceModelMax).describe('Production model being tested against'),
  "shadow_model": zod.string().min(1).max(agentccShadowExperimentsCreateBodyShadowModelMax).describe('Shadow model receiving mirrored traffic'),
  "shadow_provider": zod.string().min(1).max(agentccShadowExperimentsCreateBodyShadowProviderMax).describe('Provider for the shadow model'),
  "sample_rate": zod.number().optional().describe('Fraction of traffic to mirror (0.0â€“1.0)'),
  "status": zod.enum(['active', 'paused', 'completed']).optional(),
  "config": zod.object({

}).passthrough().optional().describe('Extra configuration')
})


/**
 * CRUD for shadow experiments with pause/resume/complete lifecycle actions.
 */
export const AgentccShadowExperimentsReadParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc shadow experiment.')
})

export const agentccShadowExperimentsReadResponseNameMax = 128;

export const agentccShadowExperimentsReadResponseSourceModelMax = 255;

export const agentccShadowExperimentsReadResponseShadowModelMax = 255;

export const agentccShadowExperimentsReadResponseShadowProviderMax = 128;



export const AgentccShadowExperimentsReadResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(agentccShadowExperimentsReadResponseNameMax),
  "description": zod.string().optional(),
  "source_model": zod.string().min(1).max(agentccShadowExperimentsReadResponseSourceModelMax).describe('Production model being tested against'),
  "shadow_model": zod.string().min(1).max(agentccShadowExperimentsReadResponseShadowModelMax).describe('Shadow model receiving mirrored traffic'),
  "shadow_provider": zod.string().min(1).max(agentccShadowExperimentsReadResponseShadowProviderMax).describe('Provider for the shadow model'),
  "sample_rate": zod.number().optional().describe('Fraction of traffic to mirror (0.0â€“1.0)'),
  "status": zod.enum(['active', 'paused', 'completed']).optional(),
  "total_comparisons": zod.number().optional(),
  "config": zod.object({

}).passthrough().optional().describe('Extra configuration'),
  "created_by": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})


/**
 * CRUD for shadow experiments with pause/resume/complete lifecycle actions.
 */
export const AgentccShadowExperimentsUpdateParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc shadow experiment.')
})

export const agentccShadowExperimentsUpdateBodyNameMax = 128;

export const agentccShadowExperimentsUpdateBodySourceModelMax = 255;

export const agentccShadowExperimentsUpdateBodyShadowModelMax = 255;

export const agentccShadowExperimentsUpdateBodyShadowProviderMax = 128;



export const AgentccShadowExperimentsUpdateBody = zod.object({
  "name": zod.string().min(1).max(agentccShadowExperimentsUpdateBodyNameMax),
  "description": zod.string().optional(),
  "source_model": zod.string().min(1).max(agentccShadowExperimentsUpdateBodySourceModelMax).describe('Production model being tested against'),
  "shadow_model": zod.string().min(1).max(agentccShadowExperimentsUpdateBodyShadowModelMax).describe('Shadow model receiving mirrored traffic'),
  "shadow_provider": zod.string().min(1).max(agentccShadowExperimentsUpdateBodyShadowProviderMax).describe('Provider for the shadow model'),
  "sample_rate": zod.number().optional().describe('Fraction of traffic to mirror (0.0â€“1.0)'),
  "status": zod.enum(['active', 'paused', 'completed']).optional(),
  "config": zod.object({

}).passthrough().optional().describe('Extra configuration')
})

export const agentccShadowExperimentsUpdateResponseNameMax = 128;

export const agentccShadowExperimentsUpdateResponseSourceModelMax = 255;

export const agentccShadowExperimentsUpdateResponseShadowModelMax = 255;

export const agentccShadowExperimentsUpdateResponseShadowProviderMax = 128;



export const AgentccShadowExperimentsUpdateResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(agentccShadowExperimentsUpdateResponseNameMax),
  "description": zod.string().optional(),
  "source_model": zod.string().min(1).max(agentccShadowExperimentsUpdateResponseSourceModelMax).describe('Production model being tested against'),
  "shadow_model": zod.string().min(1).max(agentccShadowExperimentsUpdateResponseShadowModelMax).describe('Shadow model receiving mirrored traffic'),
  "shadow_provider": zod.string().min(1).max(agentccShadowExperimentsUpdateResponseShadowProviderMax).describe('Provider for the shadow model'),
  "sample_rate": zod.number().optional().describe('Fraction of traffic to mirror (0.0â€“1.0)'),
  "status": zod.enum(['active', 'paused', 'completed']).optional(),
  "total_comparisons": zod.number().optional(),
  "config": zod.object({

}).passthrough().optional().describe('Extra configuration'),
  "created_by": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})


/**
 * CRUD for shadow experiments with pause/resume/complete lifecycle actions.
 */
export const AgentccShadowExperimentsPartialUpdateParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc shadow experiment.')
})

export const agentccShadowExperimentsPartialUpdateBodyNameMax = 128;

export const agentccShadowExperimentsPartialUpdateBodySourceModelMax = 255;

export const agentccShadowExperimentsPartialUpdateBodyShadowModelMax = 255;

export const agentccShadowExperimentsPartialUpdateBodyShadowProviderMax = 128;



export const AgentccShadowExperimentsPartialUpdateBody = zod.object({
  "name": zod.string().min(1).max(agentccShadowExperimentsPartialUpdateBodyNameMax),
  "description": zod.string().optional(),
  "source_model": zod.string().min(1).max(agentccShadowExperimentsPartialUpdateBodySourceModelMax).describe('Production model being tested against'),
  "shadow_model": zod.string().min(1).max(agentccShadowExperimentsPartialUpdateBodyShadowModelMax).describe('Shadow model receiving mirrored traffic'),
  "shadow_provider": zod.string().min(1).max(agentccShadowExperimentsPartialUpdateBodyShadowProviderMax).describe('Provider for the shadow model'),
  "sample_rate": zod.number().optional().describe('Fraction of traffic to mirror (0.0â€“1.0)'),
  "status": zod.enum(['active', 'paused', 'completed']).optional(),
  "config": zod.object({

}).passthrough().optional().describe('Extra configuration')
})

export const agentccShadowExperimentsPartialUpdateResponseNameMax = 128;

export const agentccShadowExperimentsPartialUpdateResponseSourceModelMax = 255;

export const agentccShadowExperimentsPartialUpdateResponseShadowModelMax = 255;

export const agentccShadowExperimentsPartialUpdateResponseShadowProviderMax = 128;



export const AgentccShadowExperimentsPartialUpdateResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(agentccShadowExperimentsPartialUpdateResponseNameMax),
  "description": zod.string().optional(),
  "source_model": zod.string().min(1).max(agentccShadowExperimentsPartialUpdateResponseSourceModelMax).describe('Production model being tested against'),
  "shadow_model": zod.string().min(1).max(agentccShadowExperimentsPartialUpdateResponseShadowModelMax).describe('Shadow model receiving mirrored traffic'),
  "shadow_provider": zod.string().min(1).max(agentccShadowExperimentsPartialUpdateResponseShadowProviderMax).describe('Provider for the shadow model'),
  "sample_rate": zod.number().optional().describe('Fraction of traffic to mirror (0.0â€“1.0)'),
  "status": zod.enum(['active', 'paused', 'completed']).optional(),
  "total_comparisons": zod.number().optional(),
  "config": zod.object({

}).passthrough().optional().describe('Extra configuration'),
  "created_by": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})


/**
 * CRUD for shadow experiments with pause/resume/complete lifecycle actions.
 */
export const AgentccShadowExperimentsDeleteParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc shadow experiment.')
})


/**
 * Complete an experiment (no more results will be collected).
 */
export const AgentccShadowExperimentsCompleteParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc shadow experiment.')
})

export const agentccShadowExperimentsCompleteBodyNameMax = 128;

export const agentccShadowExperimentsCompleteBodySourceModelMax = 255;

export const agentccShadowExperimentsCompleteBodyShadowModelMax = 255;

export const agentccShadowExperimentsCompleteBodyShadowProviderMax = 128;



export const AgentccShadowExperimentsCompleteBody = zod.object({
  "name": zod.string().min(1).max(agentccShadowExperimentsCompleteBodyNameMax),
  "description": zod.string().optional(),
  "source_model": zod.string().min(1).max(agentccShadowExperimentsCompleteBodySourceModelMax).describe('Production model being tested against'),
  "shadow_model": zod.string().min(1).max(agentccShadowExperimentsCompleteBodyShadowModelMax).describe('Shadow model receiving mirrored traffic'),
  "shadow_provider": zod.string().min(1).max(agentccShadowExperimentsCompleteBodyShadowProviderMax).describe('Provider for the shadow model'),
  "sample_rate": zod.number().optional().describe('Fraction of traffic to mirror (0.0â€“1.0)'),
  "status": zod.enum(['active', 'paused', 'completed']).optional(),
  "config": zod.object({

}).passthrough().optional().describe('Extra configuration')
})

export const agentccShadowExperimentsCompleteResponseNameMax = 128;

export const agentccShadowExperimentsCompleteResponseSourceModelMax = 255;

export const agentccShadowExperimentsCompleteResponseShadowModelMax = 255;

export const agentccShadowExperimentsCompleteResponseShadowProviderMax = 128;



export const AgentccShadowExperimentsCompleteResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(agentccShadowExperimentsCompleteResponseNameMax),
  "description": zod.string().optional(),
  "source_model": zod.string().min(1).max(agentccShadowExperimentsCompleteResponseSourceModelMax).describe('Production model being tested against'),
  "shadow_model": zod.string().min(1).max(agentccShadowExperimentsCompleteResponseShadowModelMax).describe('Shadow model receiving mirrored traffic'),
  "shadow_provider": zod.string().min(1).max(agentccShadowExperimentsCompleteResponseShadowProviderMax).describe('Provider for the shadow model'),
  "sample_rate": zod.number().optional().describe('Fraction of traffic to mirror (0.0â€“1.0)'),
  "status": zod.enum(['active', 'paused', 'completed']).optional(),
  "total_comparisons": zod.number().optional(),
  "config": zod.object({

}).passthrough().optional().describe('Extra configuration'),
  "created_by": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})


/**
 * Pause an active experiment.
 */
export const AgentccShadowExperimentsPauseParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc shadow experiment.')
})

export const agentccShadowExperimentsPauseBodyNameMax = 128;

export const agentccShadowExperimentsPauseBodySourceModelMax = 255;

export const agentccShadowExperimentsPauseBodyShadowModelMax = 255;

export const agentccShadowExperimentsPauseBodyShadowProviderMax = 128;



export const AgentccShadowExperimentsPauseBody = zod.object({
  "name": zod.string().min(1).max(agentccShadowExperimentsPauseBodyNameMax),
  "description": zod.string().optional(),
  "source_model": zod.string().min(1).max(agentccShadowExperimentsPauseBodySourceModelMax).describe('Production model being tested against'),
  "shadow_model": zod.string().min(1).max(agentccShadowExperimentsPauseBodyShadowModelMax).describe('Shadow model receiving mirrored traffic'),
  "shadow_provider": zod.string().min(1).max(agentccShadowExperimentsPauseBodyShadowProviderMax).describe('Provider for the shadow model'),
  "sample_rate": zod.number().optional().describe('Fraction of traffic to mirror (0.0â€“1.0)'),
  "status": zod.enum(['active', 'paused', 'completed']).optional(),
  "config": zod.object({

}).passthrough().optional().describe('Extra configuration')
})

export const agentccShadowExperimentsPauseResponseNameMax = 128;

export const agentccShadowExperimentsPauseResponseSourceModelMax = 255;

export const agentccShadowExperimentsPauseResponseShadowModelMax = 255;

export const agentccShadowExperimentsPauseResponseShadowProviderMax = 128;



export const AgentccShadowExperimentsPauseResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(agentccShadowExperimentsPauseResponseNameMax),
  "description": zod.string().optional(),
  "source_model": zod.string().min(1).max(agentccShadowExperimentsPauseResponseSourceModelMax).describe('Production model being tested against'),
  "shadow_model": zod.string().min(1).max(agentccShadowExperimentsPauseResponseShadowModelMax).describe('Shadow model receiving mirrored traffic'),
  "shadow_provider": zod.string().min(1).max(agentccShadowExperimentsPauseResponseShadowProviderMax).describe('Provider for the shadow model'),
  "sample_rate": zod.number().optional().describe('Fraction of traffic to mirror (0.0â€“1.0)'),
  "status": zod.enum(['active', 'paused', 'completed']).optional(),
  "total_comparisons": zod.number().optional(),
  "config": zod.object({

}).passthrough().optional().describe('Extra configuration'),
  "created_by": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})


/**
 * Resume a paused experiment.
 */
export const AgentccShadowExperimentsResumeParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc shadow experiment.')
})

export const agentccShadowExperimentsResumeBodyNameMax = 128;

export const agentccShadowExperimentsResumeBodySourceModelMax = 255;

export const agentccShadowExperimentsResumeBodyShadowModelMax = 255;

export const agentccShadowExperimentsResumeBodyShadowProviderMax = 128;



export const AgentccShadowExperimentsResumeBody = zod.object({
  "name": zod.string().min(1).max(agentccShadowExperimentsResumeBodyNameMax),
  "description": zod.string().optional(),
  "source_model": zod.string().min(1).max(agentccShadowExperimentsResumeBodySourceModelMax).describe('Production model being tested against'),
  "shadow_model": zod.string().min(1).max(agentccShadowExperimentsResumeBodyShadowModelMax).describe('Shadow model receiving mirrored traffic'),
  "shadow_provider": zod.string().min(1).max(agentccShadowExperimentsResumeBodyShadowProviderMax).describe('Provider for the shadow model'),
  "sample_rate": zod.number().optional().describe('Fraction of traffic to mirror (0.0â€“1.0)'),
  "status": zod.enum(['active', 'paused', 'completed']).optional(),
  "config": zod.object({

}).passthrough().optional().describe('Extra configuration')
})

export const agentccShadowExperimentsResumeResponseNameMax = 128;

export const agentccShadowExperimentsResumeResponseSourceModelMax = 255;

export const agentccShadowExperimentsResumeResponseShadowModelMax = 255;

export const agentccShadowExperimentsResumeResponseShadowProviderMax = 128;



export const AgentccShadowExperimentsResumeResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(agentccShadowExperimentsResumeResponseNameMax),
  "description": zod.string().optional(),
  "source_model": zod.string().min(1).max(agentccShadowExperimentsResumeResponseSourceModelMax).describe('Production model being tested against'),
  "shadow_model": zod.string().min(1).max(agentccShadowExperimentsResumeResponseShadowModelMax).describe('Shadow model receiving mirrored traffic'),
  "shadow_provider": zod.string().min(1).max(agentccShadowExperimentsResumeResponseShadowProviderMax).describe('Provider for the shadow model'),
  "sample_rate": zod.number().optional().describe('Fraction of traffic to mirror (0.0â€“1.0)'),
  "status": zod.enum(['active', 'paused', 'completed']).optional(),
  "total_comparisons": zod.number().optional(),
  "config": zod.object({

}).passthrough().optional().describe('Extra configuration'),
  "created_by": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})


/**
 * Aggregate metrics comparing production vs shadow for this experiment.
 */
export const AgentccShadowExperimentsStatsParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc shadow experiment.')
})

export const agentccShadowExperimentsStatsResponseNameMax = 128;

export const agentccShadowExperimentsStatsResponseSourceModelMax = 255;

export const agentccShadowExperimentsStatsResponseShadowModelMax = 255;

export const agentccShadowExperimentsStatsResponseShadowProviderMax = 128;



export const AgentccShadowExperimentsStatsResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(agentccShadowExperimentsStatsResponseNameMax),
  "description": zod.string().optional(),
  "source_model": zod.string().min(1).max(agentccShadowExperimentsStatsResponseSourceModelMax).describe('Production model being tested against'),
  "shadow_model": zod.string().min(1).max(agentccShadowExperimentsStatsResponseShadowModelMax).describe('Shadow model receiving mirrored traffic'),
  "shadow_provider": zod.string().min(1).max(agentccShadowExperimentsStatsResponseShadowProviderMax).describe('Provider for the shadow model'),
  "sample_rate": zod.number().optional().describe('Fraction of traffic to mirror (0.0â€“1.0)'),
  "status": zod.enum(['active', 'paused', 'completed']).optional(),
  "total_comparisons": zod.number().optional(),
  "config": zod.object({

}).passthrough().optional().describe('Extra configuration'),
  "created_by": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})


/**
 * Read-only viewset for shadow results. Paginated.
 */
export const AgentccShadowResultsListQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})










export const AgentccShadowResultsListResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "experiment": zod.string().uuid().optional(),
  "request_id": zod.string().min(1).optional(),
  "source_model": zod.string().min(1).optional(),
  "shadow_model": zod.string().min(1).optional(),
  "source_response": zod.string().min(1).optional(),
  "shadow_response": zod.string().min(1).optional(),
  "source_latency_ms": zod.number().optional(),
  "shadow_latency_ms": zod.number().optional(),
  "source_tokens": zod.number().optional(),
  "shadow_tokens": zod.number().optional(),
  "source_status_code": zod.number().optional(),
  "shadow_status_code": zod.number().optional(),
  "shadow_error": zod.string().min(1).optional(),
  "prompt_hash": zod.string().min(1).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional()
}))
})


/**
 * Read-only viewset for shadow results. Paginated.
 */
export const AgentccShadowResultsReadParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc shadow result.')
})










export const AgentccShadowResultsReadResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "experiment": zod.string().uuid().optional(),
  "request_id": zod.string().min(1).optional(),
  "source_model": zod.string().min(1).optional(),
  "shadow_model": zod.string().min(1).optional(),
  "source_response": zod.string().min(1).optional(),
  "shadow_response": zod.string().min(1).optional(),
  "source_latency_ms": zod.number().optional(),
  "shadow_latency_ms": zod.number().optional(),
  "source_tokens": zod.number().optional(),
  "shadow_tokens": zod.number().optional(),
  "source_status_code": zod.number().optional(),
  "shadow_status_code": zod.number().optional(),
  "shadow_error": zod.string().min(1).optional(),
  "prompt_hash": zod.string().min(1).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional()
})


/**
 * Returns aggregated spend data so the gateway can seed budget counters
on startup.  Authenticated by admin token (not user JWT).

GET /agentcc/spend-summary/?period=monthly
 */
export const AgentccSpendSummaryListQueryParams = zod.object({
  "period": zod.enum(['daily', 'weekly', 'monthly', 'total']).optional()
})

export const AgentccSpendSummaryListResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "period": zod.enum(['daily', 'weekly', 'monthly', 'total']),
  "period_start": zod.string().datetime({"offset":true}),
  "orgs": zod.record(zod.string(), zod.object({
  "total_spend": zod.number(),
  "per_key": zod.record(zod.string(), zod.number()),
  "per_user": zod.record(zod.string(), zod.number()),
  "per_model": zod.record(zod.string(), zod.number())
}))
})
})


/**
 * Read-only view of webhook event delivery records.
 */
export const AgentccWebhookEventsListQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})






export const AgentccWebhookEventsListResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "organization": zod.string().uuid().optional(),
  "webhook": zod.string().uuid().optional(),
  "webhook_name": zod.string().min(1).optional(),
  "event_type": zod.string().min(1).optional(),
  "payload": zod.object({

}).passthrough().optional(),
  "status": zod.enum(['pending', 'delivered', 'failed', 'dead_letter']).optional(),
  "attempts": zod.number().optional(),
  "max_attempts": zod.number().optional(),
  "last_attempt_at": zod.string().datetime({"offset":true}).optional(),
  "last_response_code": zod.number().optional(),
  "last_error": zod.string().min(1).optional(),
  "next_retry_at": zod.string().datetime({"offset":true}).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional()
}))
})


/**
 * Read-only view of webhook event delivery records.
 */
export const AgentccWebhookEventsReadParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc webhook event.')
})






export const AgentccWebhookEventsReadResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "organization": zod.string().uuid().optional(),
  "webhook": zod.string().uuid().optional(),
  "webhook_name": zod.string().min(1).optional(),
  "event_type": zod.string().min(1).optional(),
  "payload": zod.object({

}).passthrough().optional(),
  "status": zod.enum(['pending', 'delivered', 'failed', 'dead_letter']).optional(),
  "attempts": zod.number().optional(),
  "max_attempts": zod.number().optional(),
  "last_attempt_at": zod.string().datetime({"offset":true}).optional(),
  "last_response_code": zod.number().optional(),
  "last_error": zod.string().min(1).optional(),
  "next_retry_at": zod.string().datetime({"offset":true}).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional()
})


/**
 * Manually retry a failed webhook event delivery.
 */
export const AgentccWebhookEventsRetryParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc webhook event.')
})

export const AgentccWebhookEventsRetryBody = zod.object({

}).passthrough()


export const AgentccWebhookLogsCreateBody = zod.object({
  "gateway_id": zod.string().optional(),
  "logs": zod.array(zod.record(zod.string(), zod.string())).optional()
})

export const AgentccWebhookLogsCreateResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "ingested": zod.number()
})
})


/**
 * Receives shadow result batches from the Agentcc Go gateway flusher.
Auth via X-Webhook-Secret header.
 */
export const AgentccWebhookShadowResultsCreateBody = zod.object({
  "results": zod.array(zod.record(zod.string(), zod.string())).optional()
})

export const AgentccWebhookShadowResultsCreateResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "ingested": zod.number()
})
})


/**
 * CRUD for outbound webhook endpoints. Org-scoped.
 */
export const AgentccWebhooksListQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})

export const agentccWebhooksListResponseResultsItemNameMax = 255;

export const agentccWebhooksListResponseResultsItemUrlMax = 2048;

export const agentccWebhooksListResponseResultsItemSecretMax = 255;



export const AgentccWebhooksListResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "organization": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(agentccWebhooksListResponseResultsItemNameMax),
  "url": zod.string().url().min(1).max(agentccWebhooksListResponseResultsItemUrlMax),
  "secret": zod.string().max(agentccWebhooksListResponseResultsItemSecretMax).optional(),
  "events": zod.object({

}).passthrough().optional(),
  "is_active": zod.boolean().optional(),
  "headers": zod.object({

}).passthrough().optional(),
  "description": zod.string().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
}))
})


/**
 * CRUD for outbound webhook endpoints. Org-scoped.
 */
export const agentccWebhooksCreateBodyNameMax = 255;

export const agentccWebhooksCreateBodyUrlMax = 2048;

export const agentccWebhooksCreateBodySecretMax = 255;



export const AgentccWebhooksCreateBody = zod.object({
  "name": zod.string().min(1).max(agentccWebhooksCreateBodyNameMax),
  "url": zod.string().url().min(1).max(agentccWebhooksCreateBodyUrlMax),
  "secret": zod.string().max(agentccWebhooksCreateBodySecretMax).optional(),
  "events": zod.object({

}).passthrough().optional(),
  "is_active": zod.boolean().optional(),
  "headers": zod.object({

}).passthrough().optional(),
  "description": zod.string().optional()
})


/**
 * CRUD for outbound webhook endpoints. Org-scoped.
 */
export const AgentccWebhooksReadParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc webhook.')
})

export const agentccWebhooksReadResponseNameMax = 255;

export const agentccWebhooksReadResponseUrlMax = 2048;

export const agentccWebhooksReadResponseSecretMax = 255;



export const AgentccWebhooksReadResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "organization": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(agentccWebhooksReadResponseNameMax),
  "url": zod.string().url().min(1).max(agentccWebhooksReadResponseUrlMax),
  "secret": zod.string().max(agentccWebhooksReadResponseSecretMax).optional(),
  "events": zod.object({

}).passthrough().optional(),
  "is_active": zod.boolean().optional(),
  "headers": zod.object({

}).passthrough().optional(),
  "description": zod.string().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})


/**
 * CRUD for outbound webhook endpoints. Org-scoped.
 */
export const AgentccWebhooksUpdateParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc webhook.')
})

export const agentccWebhooksUpdateBodyNameMax = 255;

export const agentccWebhooksUpdateBodyUrlMax = 2048;

export const agentccWebhooksUpdateBodySecretMax = 255;



export const AgentccWebhooksUpdateBody = zod.object({
  "name": zod.string().min(1).max(agentccWebhooksUpdateBodyNameMax),
  "url": zod.string().url().min(1).max(agentccWebhooksUpdateBodyUrlMax),
  "secret": zod.string().max(agentccWebhooksUpdateBodySecretMax).optional(),
  "events": zod.object({

}).passthrough().optional(),
  "is_active": zod.boolean().optional(),
  "headers": zod.object({

}).passthrough().optional(),
  "description": zod.string().optional()
})

export const agentccWebhooksUpdateResponseNameMax = 255;

export const agentccWebhooksUpdateResponseUrlMax = 2048;

export const agentccWebhooksUpdateResponseSecretMax = 255;



export const AgentccWebhooksUpdateResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "organization": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(agentccWebhooksUpdateResponseNameMax),
  "url": zod.string().url().min(1).max(agentccWebhooksUpdateResponseUrlMax),
  "secret": zod.string().max(agentccWebhooksUpdateResponseSecretMax).optional(),
  "events": zod.object({

}).passthrough().optional(),
  "is_active": zod.boolean().optional(),
  "headers": zod.object({

}).passthrough().optional(),
  "description": zod.string().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})


/**
 * CRUD for outbound webhook endpoints. Org-scoped.
 */
export const AgentccWebhooksPartialUpdateParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc webhook.')
})

export const agentccWebhooksPartialUpdateBodyNameMax = 255;

export const agentccWebhooksPartialUpdateBodyUrlMax = 2048;

export const agentccWebhooksPartialUpdateBodySecretMax = 255;



export const AgentccWebhooksPartialUpdateBody = zod.object({
  "name": zod.string().min(1).max(agentccWebhooksPartialUpdateBodyNameMax),
  "url": zod.string().url().min(1).max(agentccWebhooksPartialUpdateBodyUrlMax),
  "secret": zod.string().max(agentccWebhooksPartialUpdateBodySecretMax).optional(),
  "events": zod.object({

}).passthrough().optional(),
  "is_active": zod.boolean().optional(),
  "headers": zod.object({

}).passthrough().optional(),
  "description": zod.string().optional()
})

export const agentccWebhooksPartialUpdateResponseNameMax = 255;

export const agentccWebhooksPartialUpdateResponseUrlMax = 2048;

export const agentccWebhooksPartialUpdateResponseSecretMax = 255;



export const AgentccWebhooksPartialUpdateResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "organization": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(agentccWebhooksPartialUpdateResponseNameMax),
  "url": zod.string().url().min(1).max(agentccWebhooksPartialUpdateResponseUrlMax),
  "secret": zod.string().max(agentccWebhooksPartialUpdateResponseSecretMax).optional(),
  "events": zod.object({

}).passthrough().optional(),
  "is_active": zod.boolean().optional(),
  "headers": zod.object({

}).passthrough().optional(),
  "description": zod.string().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})


/**
 * CRUD for outbound webhook endpoints. Org-scoped.
 */
export const AgentccWebhooksDeleteParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc webhook.')
})


/**
 * Send a test event to the webhook endpoint.
 */
export const AgentccWebhooksTestParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this agentcc webhook.')
})

export const agentccWebhooksTestBodyNameMax = 255;

export const agentccWebhooksTestBodyUrlMax = 2048;

export const agentccWebhooksTestBodySecretMax = 255;



export const AgentccWebhooksTestBody = zod.object({
  "name": zod.string().min(1).max(agentccWebhooksTestBodyNameMax),
  "url": zod.string().url().min(1).max(agentccWebhooksTestBodyUrlMax),
  "secret": zod.string().max(agentccWebhooksTestBodySecretMax).optional(),
  "events": zod.object({

}).passthrough().optional(),
  "is_active": zod.boolean().optional(),
  "headers": zod.object({

}).passthrough().optional(),
  "description": zod.string().optional()
})


/**
 * Lists all registered AI tools for discovery and debugging.
 */
export const aiToolsToolsListResponseStatusDefault = true;






export const AiToolsToolsListResponse = zod.object({
  "status": zod.boolean().default(aiToolsToolsListResponseStatusDefault),
  "result": zod.object({
  "tools": zod.array(zod.object({
  "name": zod.string().min(1).optional(),
  "category": zod.string().min(1).optional(),
  "description": zod.string().optional(),
  "parameters": zod.array(zod.object({
  "name": zod.string().min(1).optional(),
  "type": zod.string().min(1).optional(),
  "description": zod.string().optional(),
  "required": zod.boolean().optional()
})).optional(),
  "returns": zod.object({

}).passthrough().optional(),
  "metadata": zod.object({

}).passthrough().optional()
})).optional(),
  "categories": zod.array(zod.string().min(1)).optional(),
  "total": zod.number().optional()
})
})


/**
 * Returns ``{"mode": "oss"|"ee"|"cloud"}``. No auth â€” public config.
 * @summary Public deployment-mode probe used by the frontend to gate UI.
 */
export const apiDeploymentInfoListResponseStatusDefault = true;

export const ApiDeploymentInfoListResponse = zod.object({
  "status": zod.boolean().default(apiDeploymentInfoListResponseStatusDefault),
  "result": zod.object({
  "mode": zod.enum(['oss', 'ee', 'cloud'])
})
})


/**
 * Returns JSON with:
- status: healthy | degraded | unhealthy | disabled
- clickhouse_connected: bool
- cdc_lag: per-table replication lag in seconds
- routing: per-query-type routing configuration

No authentication required (intended for infrastructure monitoring).
 * @summary Health check endpoint for the ClickHouse analytics backend.
 */



export const ApiHealthClickhouseListResponse = zod.object({
  "status": zod.enum(['healthy', 'degraded', 'unhealthy', 'disabled']),
  "clickhouse_connected": zod.boolean(),
  "cdc_lag": zod.record(zod.string(), zod.number()),
  "routing": zod.record(zod.string(), zod.object({

}).passthrough()),
  "error": zod.string().min(1).optional()
})


/**
 * Returns the same JSON shape as Langfuse::

    {"status": "OK", "version": "1.0.0"}

When called with valid credentials (Basic Auth or API key headers)
it returns 200.  Invalid / missing credentials return 401 via DRF's
authentication layer.
 * @summary Langfuse-compatible ``GET /api/public/health`` with authentication.
 */



export const ApiPublicHealthListResponse = zod.object({
  "status": zod.enum(['OK']),
  "version": zod.string().min(1)
})


/**
 * Accepts batch events from Langfuse SDK / compatible clients (e.g. Vapi)
and ingests them as traces, observation spans, and scores.

Returns ``207 Multi-Status`` with per-event success/error reporting,
matching the Langfuse ingestion API contract.
 * @summary Langfuse-compatible ``POST /api/public/ingestion`` endpoint.
 */



export const ApiPublicIngestionCreateBody = zod.object({
  "batch": zod.array(zod.object({
  "id": zod.string().optional(),
  "type": zod.string().min(1),
  "body": zod.object({

}).passthrough().optional(),
  "timestamp": zod.string().optional()
}))
})


/**
 * Vapi validates Langfuse credentials by calling this endpoint with
``?limit=1``.      Returns an empty list with standard pagination
metadata so the credential check passes.
 * @summary Langfuse-compatible ``GET /api/public/traces``.
 */
export const ApiPublicTracesListResponse = zod.object({
  "data": zod.array(zod.object({

}).passthrough()),
  "meta": zod.object({
  "page": zod.number(),
  "limit": zod.number(),
  "total_items": zod.number(),
  "total_pages": zod.number()
})
})


/**
 * Determines the attribute type by probing which map contains the key, then
returns type-appropriate statistics:
  - string: top values with percentages
  - number: min, max, avg, p50, p95
  - boolean: true/false distribution

GET /api/traces/span-attribute-detail/?project_id=<uuid>&key=<attr_key>
 * @summary Full detail for a specific span attribute key.
 */



export const ApiTracesSpanAttributeDetailListQueryParams = zod.object({
  "project_id": zod.string().uuid(),
  "key": zod.string().min(1)
})




export const ApiTracesSpanAttributeDetailListResponse = zod.object({
  "key": zod.string().min(1),
  "type": zod.enum(['string', 'number', 'boolean']),
  "count": zod.number(),
  "unique_values": zod.number().optional(),
  "top_values": zod.array(zod.object({
  "value": zod.object({

}).passthrough(),
  "count": zod.number(),
  "percentage": zod.number()
})).optional(),
  "min": zod.number().optional(),
  "max": zod.number().optional(),
  "avg": zod.number().optional(),
  "p50": zod.number().optional(),
  "p95": zod.number().optional()
})


/**
 * Returns every distinct key across the string, number, and boolean attribute
maps together with its inferred type and occurrence count.

GET /api/traces/span-attribute-keys/?project_id=<uuid>
 * @summary Discover all span attribute keys for a project.
 */
export const ApiTracesSpanAttributeKeysListQueryParams = zod.object({
  "project_id": zod.string().uuid()
})




export const ApiTracesSpanAttributeKeysListResponse = zod.object({
  "result": zod.array(zod.object({
  "key": zod.string().min(1),
  "type": zod.enum(['string', 'number', 'boolean']),
  "count": zod.number()
}))
})


/**
 * Returns the most frequent values for the given string attribute key,
with optional prefix search filtering.

GET /api/traces/span-attribute-values/?project_id=<uuid>&key=<attr_key>[&q=<search>][&limit=50]
 * @summary Get top values for a specific span attribute key.
 */

export const apiTracesSpanAttributeValuesListQueryLimitMax = 500;



export const ApiTracesSpanAttributeValuesListQueryParams = zod.object({
  "project_id": zod.string().uuid(),
  "key": zod.string().min(1),
  "q": zod.string().optional(),
  "limit": zod.number().min(1).max(apiTracesSpanAttributeValuesListQueryLimitMax).optional()
})

export const ApiTracesSpanAttributeValuesListResponse = zod.object({
  "result": zod.array(zod.object({
  "value": zod.object({

}).passthrough(),
  "count": zod.number()
}))
})



export const callWebsocketCreateBodySendToUuidDefault = false;

export const CallWebsocketCreateBody = zod.object({
  "message": zod.string().min(1),
  "send_to_uuid": zod.boolean().default(callWebsocketCreateBodySendToUuidDefault),
  "uuid": zod.string().optional()
})

export const callWebsocketCreateResponseStatusDefault = true;


export const CallWebsocketCreateResponse = zod.object({
  "status": zod.boolean().default(callWebsocketCreateResponseStatusDefault),
  "result": zod.string().min(1)
})


/**
 * List and create conversations.
 */
export const falconAiConversationsListResponseResultsItemTitleMax = 255;

export const falconAiConversationsListResponseResultsItemContextPageMax = 500;



export const FalconAiConversationsListResponse = zod.object({
  "status": zod.boolean(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "title": zod.string().min(1).max(falconAiConversationsListResponseResultsItemTitleMax).optional(),
  "context_page": zod.string().max(falconAiConversationsListResponseResultsItemContextPageMax).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional(),
  "message_count": zod.number().optional(),
  "last_message_at": zod.string().datetime({"offset":true}).optional()
})),
  "total": zod.number(),
  "limit": zod.number(),
  "offset": zod.number(),
  "has_more": zod.boolean()
})


/**
 * List and create conversations.
 */
export const falconAiConversationsCreateBodyTitleMax = 255;

export const falconAiConversationsCreateBodyContextPageMax = 500;

export const falconAiConversationsCreateBodyHiddenDefault = false;

export const FalconAiConversationsCreateBody = zod.object({
  "title": zod.string().max(falconAiConversationsCreateBodyTitleMax).optional(),
  "context_page": zod.string().max(falconAiConversationsCreateBodyContextPageMax).optional(),
  "hidden": zod.boolean().default(falconAiConversationsCreateBodyHiddenDefault)
})


/**
 * Get, update, or delete a conversation.
 */
export const FalconAiConversationsReadParams = zod.object({
  "conversation_id": zod.string()
})

export const falconAiConversationsReadResponseResultTitleMax = 255;

export const falconAiConversationsReadResponseResultContextPageMax = 500;


export const falconAiConversationsReadResponseResultMessagesItemFeedbackMax = 20;

export const falconAiConversationsReadResponseResultMessagesItemInputTokensMin = 0;
export const falconAiConversationsReadResponseResultMessagesItemInputTokensMax = 2147483647;

export const falconAiConversationsReadResponseResultMessagesItemOutputTokensMin = 0;
export const falconAiConversationsReadResponseResultMessagesItemOutputTokensMax = 2147483647;

export const falconAiConversationsReadResponseResultMessagesItemModelUsedMax = 100;

export const falconAiConversationsReadResponseResultMessagesItemLatencyMsMin = 0;
export const falconAiConversationsReadResponseResultMessagesItemLatencyMsMax = 2147483647;



export const FalconAiConversationsReadResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "id": zod.string().uuid().optional(),
  "user": zod.string().uuid().optional(),
  "organization": zod.string().uuid().optional(),
  "workspace": zod.string().uuid().optional(),
  "title": zod.string().min(1).max(falconAiConversationsReadResponseResultTitleMax).optional(),
  "context_page": zod.string().max(falconAiConversationsReadResponseResultContextPageMax).optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "messages": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "conversation": zod.string().uuid(),
  "role": zod.enum(['user', 'assistant', 'system']),
  "content": zod.string().min(1).optional(),
  "thoughts": zod.object({

}).passthrough().optional(),
  "tool_calls": zod.object({

}).passthrough().optional(),
  "completion_card": zod.object({

}).passthrough().optional(),
  "files": zod.object({

}).passthrough().optional(),
  "feedback": zod.string().max(falconAiConversationsReadResponseResultMessagesItemFeedbackMax).optional(),
  "input_tokens": zod.number().min(falconAiConversationsReadResponseResultMessagesItemInputTokensMin).max(falconAiConversationsReadResponseResultMessagesItemInputTokensMax).optional(),
  "output_tokens": zod.number().min(falconAiConversationsReadResponseResultMessagesItemOutputTokensMin).max(falconAiConversationsReadResponseResultMessagesItemOutputTokensMax).optional(),
  "model_used": zod.string().max(falconAiConversationsReadResponseResultMessagesItemModelUsedMax).optional(),
  "latency_ms": zod.number().min(falconAiConversationsReadResponseResultMessagesItemLatencyMsMin).max(falconAiConversationsReadResponseResultMessagesItemLatencyMsMax).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})
})


/**
 * Get, update, or delete a conversation.
 */
export const FalconAiConversationsPartialUpdateParams = zod.object({
  "conversation_id": zod.string()
})

export const falconAiConversationsPartialUpdateBodyTitleMax = 255;



export const FalconAiConversationsPartialUpdateBody = zod.object({
  "title": zod.string().max(falconAiConversationsPartialUpdateBodyTitleMax).optional()
})

export const falconAiConversationsPartialUpdateResponseResultTitleMax = 255;

export const falconAiConversationsPartialUpdateResponseResultContextPageMax = 500;


export const falconAiConversationsPartialUpdateResponseResultMessagesItemFeedbackMax = 20;

export const falconAiConversationsPartialUpdateResponseResultMessagesItemInputTokensMin = 0;
export const falconAiConversationsPartialUpdateResponseResultMessagesItemInputTokensMax = 2147483647;

export const falconAiConversationsPartialUpdateResponseResultMessagesItemOutputTokensMin = 0;
export const falconAiConversationsPartialUpdateResponseResultMessagesItemOutputTokensMax = 2147483647;

export const falconAiConversationsPartialUpdateResponseResultMessagesItemModelUsedMax = 100;

export const falconAiConversationsPartialUpdateResponseResultMessagesItemLatencyMsMin = 0;
export const falconAiConversationsPartialUpdateResponseResultMessagesItemLatencyMsMax = 2147483647;



export const FalconAiConversationsPartialUpdateResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "id": zod.string().uuid().optional(),
  "user": zod.string().uuid().optional(),
  "organization": zod.string().uuid().optional(),
  "workspace": zod.string().uuid().optional(),
  "title": zod.string().min(1).max(falconAiConversationsPartialUpdateResponseResultTitleMax).optional(),
  "context_page": zod.string().max(falconAiConversationsPartialUpdateResponseResultContextPageMax).optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "messages": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "conversation": zod.string().uuid(),
  "role": zod.enum(['user', 'assistant', 'system']),
  "content": zod.string().min(1).optional(),
  "thoughts": zod.object({

}).passthrough().optional(),
  "tool_calls": zod.object({

}).passthrough().optional(),
  "completion_card": zod.object({

}).passthrough().optional(),
  "files": zod.object({

}).passthrough().optional(),
  "feedback": zod.string().max(falconAiConversationsPartialUpdateResponseResultMessagesItemFeedbackMax).optional(),
  "input_tokens": zod.number().min(falconAiConversationsPartialUpdateResponseResultMessagesItemInputTokensMin).max(falconAiConversationsPartialUpdateResponseResultMessagesItemInputTokensMax).optional(),
  "output_tokens": zod.number().min(falconAiConversationsPartialUpdateResponseResultMessagesItemOutputTokensMin).max(falconAiConversationsPartialUpdateResponseResultMessagesItemOutputTokensMax).optional(),
  "model_used": zod.string().max(falconAiConversationsPartialUpdateResponseResultMessagesItemModelUsedMax).optional(),
  "latency_ms": zod.number().min(falconAiConversationsPartialUpdateResponseResultMessagesItemLatencyMsMin).max(falconAiConversationsPartialUpdateResponseResultMessagesItemLatencyMsMax).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})
})


/**
 * Get, update, or delete a conversation.
 */
export const FalconAiConversationsDeleteParams = zod.object({
  "conversation_id": zod.string()
})


/**
 * Check if there is an active or recent agent stream for a conversation.
 */
export const FalconAiConversationsStreamStatusListParams = zod.object({
  "conversation_id": zod.string()
})




export const FalconAiConversationsStreamStatusListResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "stream_status": zod.string().min(1)
})
})


/**
 * Upload a file for use in Falcon AI conversations.
 */
export const FalconAiFilesUploadCreateBody = zod.object({
  "file": zod.instanceof(File)
})


/**
 * List and create MCP connectors.
 */
export const falconAiMcpConnectorsListResponseResultsItemNameMax = 100;

export const falconAiMcpConnectorsListResponseResultsItemServerUrlMax = 200;



export const FalconAiMcpConnectorsListResponse = zod.object({
  "status": zod.boolean(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(falconAiMcpConnectorsListResponseResultsItemNameMax),
  "server_url": zod.string().url().min(1).max(falconAiMcpConnectorsListResponseResultsItemServerUrlMax),
  "transport": zod.enum(['sse', 'streamable_http']).optional(),
  "auth_type": zod.enum(['none', 'api_key', 'bearer', 'oauth']).optional(),
  "is_active": zod.boolean().optional(),
  "is_verified": zod.boolean().optional(),
  "tool_count": zod.string().optional(),
  "last_discovery_at": zod.string().datetime({"offset":true}).optional(),
  "last_error": zod.string().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional()
}))
})


/**
 * List and create MCP connectors.
 */
export const falconAiMcpConnectorsCreateBodyNameMax = 100;


export const falconAiMcpConnectorsCreateBodyTransportDefault = `streamable_http`;
export const falconAiMcpConnectorsCreateBodyAuthTypeDefault = `none`;
export const falconAiMcpConnectorsCreateBodyAuthHeaderNameDefault = `Authorization`;
export const falconAiMcpConnectorsCreateBodyAuthHeaderNameMax = 100;

export const falconAiMcpConnectorsCreateBodyAuthHeaderValueDefault = ``;

export const FalconAiMcpConnectorsCreateBody = zod.object({
  "name": zod.string().min(1).max(falconAiMcpConnectorsCreateBodyNameMax),
  "server_url": zod.string().url().min(1),
  "transport": zod.enum(['sse', 'streamable_http']).default(falconAiMcpConnectorsCreateBodyTransportDefault),
  "auth_type": zod.enum(['none', 'api_key', 'bearer', 'oauth']).default(falconAiMcpConnectorsCreateBodyAuthTypeDefault),
  "auth_header_name": zod.string().max(falconAiMcpConnectorsCreateBodyAuthHeaderNameMax).default(falconAiMcpConnectorsCreateBodyAuthHeaderNameDefault),
  "auth_header_value": zod.string().default(falconAiMcpConnectorsCreateBodyAuthHeaderValueDefault)
})


/**
 * Get, update, or delete an MCP connector.
 */
export const FalconAiMcpConnectorsReadParams = zod.object({
  "connector_id": zod.string()
})

export const falconAiMcpConnectorsReadResponseResultNameMax = 100;

export const falconAiMcpConnectorsReadResponseResultServerUrlMax = 200;

export const falconAiMcpConnectorsReadResponseResultAuthHeaderNameMax = 100;



export const FalconAiMcpConnectorsReadResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(falconAiMcpConnectorsReadResponseResultNameMax),
  "server_url": zod.string().url().min(1).max(falconAiMcpConnectorsReadResponseResultServerUrlMax),
  "transport": zod.enum(['sse', 'streamable_http']).optional(),
  "auth_type": zod.enum(['none', 'api_key', 'bearer', 'oauth']).optional(),
  "auth_header_name": zod.string().max(falconAiMcpConnectorsReadResponseResultAuthHeaderNameMax).optional(),
  "is_active": zod.boolean().optional(),
  "is_verified": zod.boolean().optional(),
  "discovered_tools": zod.object({

}).passthrough().optional(),
  "enabled_tool_names": zod.object({

}).passthrough().optional(),
  "last_discovery_at": zod.string().datetime({"offset":true}).optional(),
  "last_error": zod.string().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})
})


/**
 * Get, update, or delete an MCP connector.
 */
export const FalconAiMcpConnectorsPartialUpdateParams = zod.object({
  "connector_id": zod.string()
})

export const falconAiMcpConnectorsPartialUpdateBodyNameMax = 100;


export const falconAiMcpConnectorsPartialUpdateBodyAuthHeaderNameMax = 100;



export const FalconAiMcpConnectorsPartialUpdateBody = zod.object({
  "name": zod.string().max(falconAiMcpConnectorsPartialUpdateBodyNameMax).optional(),
  "server_url": zod.string().url().min(1).optional(),
  "transport": zod.enum(['sse', 'streamable_http']).optional(),
  "auth_type": zod.enum(['none', 'api_key', 'bearer', 'oauth']).optional(),
  "auth_header_name": zod.string().max(falconAiMcpConnectorsPartialUpdateBodyAuthHeaderNameMax).optional(),
  "auth_header_value": zod.string().optional(),
  "is_active": zod.boolean().optional()
})

export const falconAiMcpConnectorsPartialUpdateResponseResultNameMax = 100;

export const falconAiMcpConnectorsPartialUpdateResponseResultServerUrlMax = 200;

export const falconAiMcpConnectorsPartialUpdateResponseResultAuthHeaderNameMax = 100;



export const FalconAiMcpConnectorsPartialUpdateResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(falconAiMcpConnectorsPartialUpdateResponseResultNameMax),
  "server_url": zod.string().url().min(1).max(falconAiMcpConnectorsPartialUpdateResponseResultServerUrlMax),
  "transport": zod.enum(['sse', 'streamable_http']).optional(),
  "auth_type": zod.enum(['none', 'api_key', 'bearer', 'oauth']).optional(),
  "auth_header_name": zod.string().max(falconAiMcpConnectorsPartialUpdateResponseResultAuthHeaderNameMax).optional(),
  "is_active": zod.boolean().optional(),
  "is_verified": zod.boolean().optional(),
  "discovered_tools": zod.object({

}).passthrough().optional(),
  "enabled_tool_names": zod.object({

}).passthrough().optional(),
  "last_discovery_at": zod.string().datetime({"offset":true}).optional(),
  "last_error": zod.string().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})
})


/**
 * Get, update, or delete an MCP connector.
 */
export const FalconAiMcpConnectorsDeleteParams = zod.object({
  "connector_id": zod.string()
})


/**
 * For OAuth connectors, returns the authorization URL to open in a browser.
For API key connectors, tests the connection and returns status.
 * @summary Initiate or re-initiate authentication for a connector.
 */
export const FalconAiMcpConnectorsAuthenticateCreateParams = zod.object({
  "connector_id": zod.string()
})

export const FalconAiMcpConnectorsAuthenticateCreateBody = zod.object({

}).passthrough()

export const falconAiMcpConnectorsAuthenticateCreateResponseResultNameMax = 100;

export const falconAiMcpConnectorsAuthenticateCreateResponseResultServerUrlMax = 200;

export const falconAiMcpConnectorsAuthenticateCreateResponseResultAuthHeaderNameMax = 100;




export const FalconAiMcpConnectorsAuthenticateCreateResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(falconAiMcpConnectorsAuthenticateCreateResponseResultNameMax),
  "server_url": zod.string().url().min(1).max(falconAiMcpConnectorsAuthenticateCreateResponseResultServerUrlMax),
  "transport": zod.enum(['sse', 'streamable_http']).optional(),
  "auth_type": zod.enum(['none', 'api_key', 'bearer', 'oauth']).optional(),
  "auth_header_name": zod.string().max(falconAiMcpConnectorsAuthenticateCreateResponseResultAuthHeaderNameMax).optional(),
  "is_active": zod.boolean().optional(),
  "is_verified": zod.boolean().optional(),
  "discovered_tools": zod.object({

}).passthrough().optional(),
  "enabled_tool_names": zod.object({

}).passthrough().optional(),
  "last_discovery_at": zod.string().datetime({"offset":true}).optional(),
  "last_error": zod.string().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
}).optional(),
  "auth_type": zod.string().optional(),
  "authorization_url": zod.string().url().min(1).optional(),
  "message": zod.string().optional()
})


/**
 * Discover tools from an external MCP server.
 */
export const FalconAiMcpConnectorsDiscoverCreateParams = zod.object({
  "connector_id": zod.string()
})

export const FalconAiMcpConnectorsDiscoverCreateBody = zod.object({

}).passthrough()

export const falconAiMcpConnectorsDiscoverCreateResponseResultNameMax = 100;

export const falconAiMcpConnectorsDiscoverCreateResponseResultServerUrlMax = 200;

export const falconAiMcpConnectorsDiscoverCreateResponseResultAuthHeaderNameMax = 100;



export const FalconAiMcpConnectorsDiscoverCreateResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(falconAiMcpConnectorsDiscoverCreateResponseResultNameMax),
  "server_url": zod.string().url().min(1).max(falconAiMcpConnectorsDiscoverCreateResponseResultServerUrlMax),
  "transport": zod.enum(['sse', 'streamable_http']).optional(),
  "auth_type": zod.enum(['none', 'api_key', 'bearer', 'oauth']).optional(),
  "auth_header_name": zod.string().max(falconAiMcpConnectorsDiscoverCreateResponseResultAuthHeaderNameMax).optional(),
  "is_active": zod.boolean().optional(),
  "is_verified": zod.boolean().optional(),
  "discovered_tools": zod.object({

}).passthrough().optional(),
  "enabled_tool_names": zod.object({

}).passthrough().optional(),
  "last_discovery_at": zod.string().datetime({"offset":true}).optional(),
  "last_error": zod.string().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
}),
  "discovered_count": zod.number()
})


/**
 * This is a GET endpoint that the browser is redirected to after the user
approves the OAuth consent. It exchanges the authorization code for
tokens, then returns an HTML page that posts a message to the opener
window and closes itself.

Uses AllowAny because this is a browser redirect with state validation.
 * @summary Handle the OAuth 2.1 redirect callback from an authorization server.
 */
export const FalconAiMcpConnectorsOauthCallbackListParams = zod.object({
  "connector_id": zod.string()
})

export const FalconAiMcpConnectorsOauthCallbackListQueryParams = zod.object({
  "code": zod.string().optional(),
  "state": zod.string().optional(),
  "error": zod.string().optional(),
  "error_description": zod.string().optional()
})

export const FalconAiMcpConnectorsOauthCallbackListResponse = zod.string()


/**
 * Test connection to an external MCP server.
 */
export const FalconAiMcpConnectorsTestCreateParams = zod.object({
  "connector_id": zod.string()
})

export const FalconAiMcpConnectorsTestCreateBody = zod.object({

}).passthrough()

export const FalconAiMcpConnectorsTestCreateResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "success": zod.boolean(),
  "status_code": zod.number().optional(),
  "error": zod.string().optional()
}).optional(),
  "error": zod.string().optional()
})


/**
 * Enable or disable specific tools on a connector.
 */
export const FalconAiMcpConnectorsToolsPartialUpdateParams = zod.object({
  "connector_id": zod.string()
})




export const FalconAiMcpConnectorsToolsPartialUpdateBody = zod.object({
  "enabled_tool_names": zod.array(zod.string().min(1))
})

export const falconAiMcpConnectorsToolsPartialUpdateResponseResultNameMax = 100;

export const falconAiMcpConnectorsToolsPartialUpdateResponseResultServerUrlMax = 200;

export const falconAiMcpConnectorsToolsPartialUpdateResponseResultAuthHeaderNameMax = 100;



export const FalconAiMcpConnectorsToolsPartialUpdateResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(falconAiMcpConnectorsToolsPartialUpdateResponseResultNameMax),
  "server_url": zod.string().url().min(1).max(falconAiMcpConnectorsToolsPartialUpdateResponseResultServerUrlMax),
  "transport": zod.enum(['sse', 'streamable_http']).optional(),
  "auth_type": zod.enum(['none', 'api_key', 'bearer', 'oauth']).optional(),
  "auth_header_name": zod.string().max(falconAiMcpConnectorsToolsPartialUpdateResponseResultAuthHeaderNameMax).optional(),
  "is_active": zod.boolean().optional(),
  "is_verified": zod.boolean().optional(),
  "discovered_tools": zod.object({

}).passthrough().optional(),
  "enabled_tool_names": zod.object({

}).passthrough().optional(),
  "last_discovery_at": zod.string().datetime({"offset":true}).optional(),
  "last_error": zod.string().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})
})


/**
 * List and create workspace memories.
 */
export const falconAiMemoryListResponseResultsItemKeyMax = 200;




export const FalconAiMemoryListResponse = zod.object({
  "status": zod.boolean(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "key": zod.string().min(1).max(falconAiMemoryListResponseResultsItemKeyMax),
  "value": zod.string().min(1),
  "source": zod.enum(['user', 'agent', 'init']).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional()
}))
})


/**
 * List and create workspace memories.
 */
export const falconAiMemoryCreateBodyKeyMax = 200;




export const FalconAiMemoryCreateBody = zod.object({
  "key": zod.string().min(1).max(falconAiMemoryCreateBodyKeyMax),
  "value": zod.string().min(1)
})

export const falconAiMemoryCreateResponseResultKeyMax = 200;




export const FalconAiMemoryCreateResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "id": zod.string().uuid().optional(),
  "key": zod.string().min(1).max(falconAiMemoryCreateResponseResultKeyMax),
  "value": zod.string().min(1),
  "source": zod.enum(['user', 'agent', 'init']).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional()
})
})


/**
 * Delete a workspace memory.
 */
export const FalconAiMemoryDeleteParams = zod.object({
  "memory_id": zod.string()
})


/**
 * Update feedback on a message.
 */
export const FalconAiMessagesFeedbackCreateParams = zod.object({
  "message_id": zod.string()
})

export const FalconAiMessagesFeedbackCreateBody = zod.object({
  "feedback": zod.enum(['thumbs_up', 'thumbs_down', ''])
})

export const FalconAiMessagesFeedbackCreateResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "feedback": zod.string()
})
})


export const falconAiQuickAnalysisCreateBodyPromptMax = 8000;



export const FalconAiQuickAnalysisCreateBody = zod.object({
  "prompt": zod.string().min(1).max(falconAiQuickAnalysisCreateBodyPromptMax)
})




export const FalconAiQuickAnalysisCreateResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.string().min(1)
})


/**
 * List and create skills.
 */
export const falconAiSkillsListResponseResultsItemNameMax = 100;

export const falconAiSkillsListResponseResultsItemSlugMax = 100;


export const falconAiSkillsListResponseResultsItemSlugRegExp = new RegExp('^[-a-zA-Z0-9_]+$');

export const falconAiSkillsListResponseResultsItemIconMax = 50;



export const FalconAiSkillsListResponse = zod.object({
  "status": zod.boolean(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(falconAiSkillsListResponseResultsItemNameMax),
  "slug": zod.string().min(1).max(falconAiSkillsListResponseResultsItemSlugMax).regex(falconAiSkillsListResponseResultsItemSlugRegExp),
  "description": zod.string().min(1).optional(),
  "icon": zod.string().min(1).max(falconAiSkillsListResponseResultsItemIconMax).optional(),
  "is_builtin": zod.boolean().optional(),
  "is_active": zod.boolean().optional(),
  "tool_names": zod.object({

}).passthrough().optional(),
  "trigger_phrases": zod.object({

}).passthrough().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "created_by_display": zod.string().optional()
}))
})


/**
 * List and create skills.
 */
export const falconAiSkillsCreateBodyNameMax = 100;

export const falconAiSkillsCreateBodyDescriptionDefault = ``;
export const falconAiSkillsCreateBodyIconDefault = `mdi:star`;
export const falconAiSkillsCreateBodyIconMax = 50;



export const falconAiSkillsCreateBodyToolNamesDefault = [];



export const FalconAiSkillsCreateBody = zod.object({
  "name": zod.string().min(1).max(falconAiSkillsCreateBodyNameMax),
  "description": zod.string().default(falconAiSkillsCreateBodyDescriptionDefault),
  "icon": zod.string().min(1).max(falconAiSkillsCreateBodyIconMax).default(falconAiSkillsCreateBodyIconDefault),
  "instructions": zod.string().min(1),
  "tool_names": zod.array(zod.string().min(1)).default(falconAiSkillsCreateBodyToolNamesDefault),
  "trigger_phrases": zod.array(zod.string().min(1)).min(1)
})


/**
 * Get, update, or delete a skill.
 */
export const FalconAiSkillsReadParams = zod.object({
  "skill_id": zod.string()
})

export const falconAiSkillsReadResponseResultNameMax = 100;

export const falconAiSkillsReadResponseResultSlugMax = 100;


export const falconAiSkillsReadResponseResultSlugRegExp = new RegExp('^[-a-zA-Z0-9_]+$');

export const falconAiSkillsReadResponseResultIconMax = 50;




export const FalconAiSkillsReadResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(falconAiSkillsReadResponseResultNameMax),
  "slug": zod.string().min(1).max(falconAiSkillsReadResponseResultSlugMax).regex(falconAiSkillsReadResponseResultSlugRegExp),
  "description": zod.string().min(1).optional(),
  "icon": zod.string().min(1).max(falconAiSkillsReadResponseResultIconMax).optional(),
  "is_builtin": zod.boolean().optional(),
  "is_active": zod.boolean().optional(),
  "instructions": zod.string().min(1).optional(),
  "tool_names": zod.object({

}).passthrough().optional(),
  "example_trajectories": zod.object({

}).passthrough().optional(),
  "trigger_phrases": zod.object({

}).passthrough().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional(),
  "created_by_display": zod.string().optional()
})
})


/**
 * Get, update, or delete a skill.
 */
export const FalconAiSkillsPartialUpdateParams = zod.object({
  "skill_id": zod.string()
})

export const falconAiSkillsPartialUpdateBodyNameMax = 100;

export const falconAiSkillsPartialUpdateBodyIconMax = 50;






export const FalconAiSkillsPartialUpdateBody = zod.object({
  "name": zod.string().min(1).max(falconAiSkillsPartialUpdateBodyNameMax).optional(),
  "description": zod.string().optional(),
  "icon": zod.string().min(1).max(falconAiSkillsPartialUpdateBodyIconMax).optional(),
  "instructions": zod.string().min(1).optional(),
  "tool_names": zod.array(zod.string().min(1)).optional(),
  "trigger_phrases": zod.array(zod.string().min(1)).optional(),
  "is_active": zod.boolean().optional()
})

export const falconAiSkillsPartialUpdateResponseResultNameMax = 100;

export const falconAiSkillsPartialUpdateResponseResultSlugMax = 100;


export const falconAiSkillsPartialUpdateResponseResultSlugRegExp = new RegExp('^[-a-zA-Z0-9_]+$');

export const falconAiSkillsPartialUpdateResponseResultIconMax = 50;




export const FalconAiSkillsPartialUpdateResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(falconAiSkillsPartialUpdateResponseResultNameMax),
  "slug": zod.string().min(1).max(falconAiSkillsPartialUpdateResponseResultSlugMax).regex(falconAiSkillsPartialUpdateResponseResultSlugRegExp),
  "description": zod.string().min(1).optional(),
  "icon": zod.string().min(1).max(falconAiSkillsPartialUpdateResponseResultIconMax).optional(),
  "is_builtin": zod.boolean().optional(),
  "is_active": zod.boolean().optional(),
  "instructions": zod.string().min(1).optional(),
  "tool_names": zod.object({

}).passthrough().optional(),
  "example_trajectories": zod.object({

}).passthrough().optional(),
  "trigger_phrases": zod.object({

}).passthrough().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional(),
  "created_by_display": zod.string().optional()
})
})


/**
 * Get, update, or delete a skill.
 */
export const FalconAiSkillsDeleteParams = zod.object({
  "skill_id": zod.string()
})


/**
 * GET method for health check.
Returns 200 OK with a simple status message.
 */
export const healthListResponseStatusDefault = true;


export const HealthListResponse = zod.object({
  "status": zod.boolean().default(healthListResponseStatusDefault),
  "result": zod.string().min(1)
})


/**
 * API endpoints for managing integration connections.
 */
export const integrationsConnectionsListQueryPageNumberDefault = 0;
export const integrationsConnectionsListQueryPageNumberMin = 0;

export const integrationsConnectionsListQueryPageSizeDefault = 20;
export const integrationsConnectionsListQueryPageSizeMax = 100;



export const IntegrationsConnectionsListQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.'),
  "page_number": zod.number().min(integrationsConnectionsListQueryPageNumberMin).default(integrationsConnectionsListQueryPageNumberDefault),
  "page_size": zod.number().min(1).max(integrationsConnectionsListQueryPageSizeMax).default(integrationsConnectionsListQueryPageSizeDefault)
})

export const integrationsConnectionsListResponseStatusDefault = true;
export const integrationsConnectionsListResponseResultConnectionsItemDisplayNameMax = 255;

export const integrationsConnectionsListResponseResultConnectionsItemHostUrlMax = 500;

export const integrationsConnectionsListResponseResultConnectionsItemExternalProjectNameMax = 255;

export const integrationsConnectionsListResponseResultConnectionsItemTotalTracesSyncedMin = 0;
export const integrationsConnectionsListResponseResultConnectionsItemTotalTracesSyncedMax = 2147483647;

export const integrationsConnectionsListResponseResultConnectionsItemTotalSpansSyncedMin = 0;
export const integrationsConnectionsListResponseResultConnectionsItemTotalSpansSyncedMax = 2147483647;

export const integrationsConnectionsListResponseResultConnectionsItemTotalScoresSyncedMin = 0;
export const integrationsConnectionsListResponseResultConnectionsItemTotalScoresSyncedMax = 2147483647;

export const integrationsConnectionsListResponseResultConnectionsItemSyncIntervalSecondsMin = 60;
export const integrationsConnectionsListResponseResultConnectionsItemSyncIntervalSecondsMax = 1800;



export const IntegrationsConnectionsListResponse = zod.object({
  "status": zod.boolean().default(integrationsConnectionsListResponseStatusDefault),
  "result": zod.object({
  "metadata": zod.object({
  "total_count": zod.number(),
  "current_page": zod.number(),
  "page_size": zod.number(),
  "total_pages": zod.number(),
  "next_page": zod.number().optional()
}),
  "connections": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "platform": zod.enum(['langfuse', 'datadog', 'posthog', 'pagerduty', 'mixpanel', 'cloud_storage', 'message_queue', 'linear']),
  "display_name": zod.string().min(1).max(integrationsConnectionsListResponseResultConnectionsItemDisplayNameMax),
  "host_url": zod.string().url().min(1).max(integrationsConnectionsListResponseResultConnectionsItemHostUrlMax),
  "status": zod.enum(['active', 'paused', 'error', 'syncing', 'backfilling']).optional(),
  "status_message": zod.string().optional(),
  "external_project_name": zod.string().min(1).max(integrationsConnectionsListResponseResultConnectionsItemExternalProjectNameMax),
  "last_synced_at": zod.string().datetime({"offset":true}).optional(),
  "total_traces_synced": zod.number().min(integrationsConnectionsListResponseResultConnectionsItemTotalTracesSyncedMin).max(integrationsConnectionsListResponseResultConnectionsItemTotalTracesSyncedMax).optional(),
  "total_spans_synced": zod.number().min(integrationsConnectionsListResponseResultConnectionsItemTotalSpansSyncedMin).max(integrationsConnectionsListResponseResultConnectionsItemTotalSpansSyncedMax).optional(),
  "total_scores_synced": zod.number().min(integrationsConnectionsListResponseResultConnectionsItemTotalScoresSyncedMin).max(integrationsConnectionsListResponseResultConnectionsItemTotalScoresSyncedMax).optional(),
  "backfill_completed": zod.boolean().optional(),
  "backfill_progress": zod.object({

}).passthrough().optional(),
  "sync_interval_seconds": zod.number().min(integrationsConnectionsListResponseResultConnectionsItemSyncIntervalSecondsMin).max(integrationsConnectionsListResponseResultConnectionsItemSyncIntervalSecondsMax).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional()
}))
})
})


/**
 * API endpoints for managing integration connections.
 */
export const integrationsConnectionsCreateBodyHostUrlDefault = ``;
export const integrationsConnectionsCreateBodyHostUrlMax = 500;

export const integrationsConnectionsCreateBodyPublicKeyDefault = ``;
export const integrationsConnectionsCreateBodyPublicKeyMax = 500;

export const integrationsConnectionsCreateBodySecretKeyDefault = ``;
export const integrationsConnectionsCreateBodySecretKeyMax = 500;

export const integrationsConnectionsCreateBodyCaCertificateDefault = ``;
export const integrationsConnectionsCreateBodyCredentialsDefault = {  };
export const integrationsConnectionsCreateBodyNewProjectNameDefault = ``;
export const integrationsConnectionsCreateBodyBackfillOptionDefault = `new_only`;
export const integrationsConnectionsCreateBodySyncIntervalSecondsDefault = 300;
export const integrationsConnectionsCreateBodySyncIntervalSecondsMin = 60;
export const integrationsConnectionsCreateBodySyncIntervalSecondsMax = 1800;

export const integrationsConnectionsCreateBodyDisplayNameDefault = ``;
export const integrationsConnectionsCreateBodyExternalProjectNameDefault = ``;
export const integrationsConnectionsCreateBodyExportConfigDefault = {  };

export const IntegrationsConnectionsCreateBody = zod.object({
  "platform": zod.enum(['langfuse', 'datadog', 'posthog', 'pagerduty', 'mixpanel', 'cloud_storage', 'message_queue', 'linear']),
  "host_url": zod.string().url().min(1).max(integrationsConnectionsCreateBodyHostUrlMax).default(integrationsConnectionsCreateBodyHostUrlDefault),
  "public_key": zod.string().min(1).max(integrationsConnectionsCreateBodyPublicKeyMax).default(integrationsConnectionsCreateBodyPublicKeyDefault),
  "secret_key": zod.string().min(1).max(integrationsConnectionsCreateBodySecretKeyMax).default(integrationsConnectionsCreateBodySecretKeyDefault),
  "ca_certificate": zod.string().default(integrationsConnectionsCreateBodyCaCertificateDefault),
  "credentials": zod.object({

}).passthrough().default(integrationsConnectionsCreateBodyCredentialsDefault),
  "project_id": zod.string().uuid().optional().describe('Existing FutureAGI project ID. If null, a new project is created.'),
  "new_project_name": zod.string().default(integrationsConnectionsCreateBodyNewProjectNameDefault).describe('Name for the new project (used when project_id is null).'),
  "backfill_option": zod.enum(['all', 'from_date', 'new_only']).default(integrationsConnectionsCreateBodyBackfillOptionDefault),
  "backfill_from_date": zod.string().datetime({"offset":true}).optional(),
  "backfill_to_date": zod.string().datetime({"offset":true}).optional(),
  "sync_interval_seconds": zod.number().min(integrationsConnectionsCreateBodySyncIntervalSecondsMin).max(integrationsConnectionsCreateBodySyncIntervalSecondsMax).default(integrationsConnectionsCreateBodySyncIntervalSecondsDefault),
  "display_name": zod.string().default(integrationsConnectionsCreateBodyDisplayNameDefault),
  "external_project_name": zod.string().default(integrationsConnectionsCreateBodyExternalProjectNameDefault),
  "export_config": zod.object({

}).passthrough().default(integrationsConnectionsCreateBodyExportConfigDefault)
})


/**
 * Validate platform credentials without creating a connection.
 */
export const integrationsConnectionsValidateCredentialsBodyHostUrlDefault = ``;
export const integrationsConnectionsValidateCredentialsBodyHostUrlMax = 500;

export const integrationsConnectionsValidateCredentialsBodyPublicKeyDefault = ``;
export const integrationsConnectionsValidateCredentialsBodyPublicKeyMax = 500;

export const integrationsConnectionsValidateCredentialsBodySecretKeyDefault = ``;
export const integrationsConnectionsValidateCredentialsBodySecretKeyMax = 500;

export const integrationsConnectionsValidateCredentialsBodyCaCertificateDefault = ``;
export const integrationsConnectionsValidateCredentialsBodyCredentialsDefault = {  };

export const IntegrationsConnectionsValidateCredentialsBody = zod.object({
  "platform": zod.enum(['langfuse', 'datadog', 'posthog', 'pagerduty', 'mixpanel', 'cloud_storage', 'message_queue', 'linear']),
  "host_url": zod.string().url().min(1).max(integrationsConnectionsValidateCredentialsBodyHostUrlMax).default(integrationsConnectionsValidateCredentialsBodyHostUrlDefault),
  "public_key": zod.string().min(1).max(integrationsConnectionsValidateCredentialsBodyPublicKeyMax).default(integrationsConnectionsValidateCredentialsBodyPublicKeyDefault),
  "secret_key": zod.string().min(1).max(integrationsConnectionsValidateCredentialsBodySecretKeyMax).default(integrationsConnectionsValidateCredentialsBodySecretKeyDefault),
  "ca_certificate": zod.string().default(integrationsConnectionsValidateCredentialsBodyCaCertificateDefault),
  "credentials": zod.object({

}).passthrough().default(integrationsConnectionsValidateCredentialsBodyCredentialsDefault)
})

export const integrationsConnectionsValidateCredentialsResponseStatusDefault = true;
export const integrationsConnectionsValidateCredentialsResponseResultTotalTracesMin = 0;



export const IntegrationsConnectionsValidateCredentialsResponse = zod.object({
  "status": zod.boolean().default(integrationsConnectionsValidateCredentialsResponseStatusDefault),
  "result": zod.object({
  "valid": zod.boolean(),
  "projects": zod.array(zod.object({
  "id": zod.string().optional(),
  "name": zod.string().optional()
})).optional(),
  "total_traces": zod.number().min(integrationsConnectionsValidateCredentialsResponseResultTotalTracesMin).optional(),
  "error": zod.string().optional(),
  "viewer": zod.object({
  "id": zod.string().optional(),
  "name": zod.string().optional(),
  "email": zod.string().email().optional()
}).optional()
})
})


/**
 * API endpoints for managing integration connections.
 */
export const IntegrationsConnectionsReadParams = zod.object({
  "id": zod.string()
})

export const integrationsConnectionsReadResponseDisplayNameMax = 255;

export const integrationsConnectionsReadResponseHostUrlMax = 500;

export const integrationsConnectionsReadResponseExternalProjectNameMax = 255;

export const integrationsConnectionsReadResponseSyncIntervalSecondsMin = 60;
export const integrationsConnectionsReadResponseSyncIntervalSecondsMax = 1800;

export const integrationsConnectionsReadResponseTotalTracesSyncedMin = 0;
export const integrationsConnectionsReadResponseTotalTracesSyncedMax = 2147483647;

export const integrationsConnectionsReadResponseTotalSpansSyncedMin = 0;
export const integrationsConnectionsReadResponseTotalSpansSyncedMax = 2147483647;

export const integrationsConnectionsReadResponseTotalScoresSyncedMin = 0;
export const integrationsConnectionsReadResponseTotalScoresSyncedMax = 2147483647;



export const IntegrationsConnectionsReadResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "platform": zod.enum(['langfuse', 'datadog', 'posthog', 'pagerduty', 'mixpanel', 'cloud_storage', 'message_queue', 'linear']),
  "display_name": zod.string().min(1).max(integrationsConnectionsReadResponseDisplayNameMax),
  "host_url": zod.string().url().min(1).max(integrationsConnectionsReadResponseHostUrlMax),
  "status": zod.enum(['active', 'paused', 'error', 'syncing', 'backfilling']).optional(),
  "status_message": zod.string().optional(),
  "external_project_name": zod.string().min(1).max(integrationsConnectionsReadResponseExternalProjectNameMax),
  "project": zod.string().uuid().optional(),
  "project_name": zod.string().optional(),
  "public_key_display": zod.string().optional(),
  "secret_key_display": zod.string().optional(),
  "last_synced_at": zod.string().datetime({"offset":true}).optional(),
  "sync_cursor": zod.object({

}).passthrough().optional(),
  "sync_interval_seconds": zod.number().min(integrationsConnectionsReadResponseSyncIntervalSecondsMin).max(integrationsConnectionsReadResponseSyncIntervalSecondsMax).optional(),
  "last_error_notified_at": zod.string().datetime({"offset":true}).optional(),
  "backfill_from": zod.string().datetime({"offset":true}).optional(),
  "backfill_completed": zod.boolean().optional(),
  "backfill_progress": zod.object({

}).passthrough().optional(),
  "total_traces_synced": zod.number().min(integrationsConnectionsReadResponseTotalTracesSyncedMin).max(integrationsConnectionsReadResponseTotalTracesSyncedMax).optional(),
  "total_spans_synced": zod.number().min(integrationsConnectionsReadResponseTotalSpansSyncedMin).max(integrationsConnectionsReadResponseTotalSpansSyncedMax).optional(),
  "total_scores_synced": zod.number().min(integrationsConnectionsReadResponseTotalScoresSyncedMin).max(integrationsConnectionsReadResponseTotalScoresSyncedMax).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional(),
  "created_by": zod.string().uuid().optional()
})


/**
 * API endpoints for managing integration connections.
 */
export const IntegrationsConnectionsUpdateParams = zod.object({
  "id": zod.string()
})

export const integrationsConnectionsUpdateBodyDisplayNameMax = 255;

export const integrationsConnectionsUpdateBodyPublicKeyMax = 500;

export const integrationsConnectionsUpdateBodySecretKeyMax = 500;

export const integrationsConnectionsUpdateBodyHostUrlMax = 500;

export const integrationsConnectionsUpdateBodySyncIntervalSecondsMin = 60;
export const integrationsConnectionsUpdateBodySyncIntervalSecondsMax = 3600;



export const IntegrationsConnectionsUpdateBody = zod.object({
  "display_name": zod.string().min(1).max(integrationsConnectionsUpdateBodyDisplayNameMax).optional(),
  "public_key": zod.string().min(1).max(integrationsConnectionsUpdateBodyPublicKeyMax).optional(),
  "secret_key": zod.string().min(1).max(integrationsConnectionsUpdateBodySecretKeyMax).optional(),
  "host_url": zod.string().url().min(1).max(integrationsConnectionsUpdateBodyHostUrlMax).optional(),
  "ca_certificate": zod.string().optional(),
  "sync_interval_seconds": zod.number().min(integrationsConnectionsUpdateBodySyncIntervalSecondsMin).max(integrationsConnectionsUpdateBodySyncIntervalSecondsMax).optional()
})

export const integrationsConnectionsUpdateResponseStatusDefault = true;
export const integrationsConnectionsUpdateResponseResultDisplayNameMax = 255;

export const integrationsConnectionsUpdateResponseResultHostUrlMax = 500;

export const integrationsConnectionsUpdateResponseResultExternalProjectNameMax = 255;

export const integrationsConnectionsUpdateResponseResultSyncIntervalSecondsMin = 60;
export const integrationsConnectionsUpdateResponseResultSyncIntervalSecondsMax = 1800;

export const integrationsConnectionsUpdateResponseResultTotalTracesSyncedMin = 0;
export const integrationsConnectionsUpdateResponseResultTotalTracesSyncedMax = 2147483647;

export const integrationsConnectionsUpdateResponseResultTotalSpansSyncedMin = 0;
export const integrationsConnectionsUpdateResponseResultTotalSpansSyncedMax = 2147483647;

export const integrationsConnectionsUpdateResponseResultTotalScoresSyncedMin = 0;
export const integrationsConnectionsUpdateResponseResultTotalScoresSyncedMax = 2147483647;



export const IntegrationsConnectionsUpdateResponse = zod.object({
  "status": zod.boolean().default(integrationsConnectionsUpdateResponseStatusDefault),
  "result": zod.object({
  "id": zod.string().uuid().optional(),
  "platform": zod.enum(['langfuse', 'datadog', 'posthog', 'pagerduty', 'mixpanel', 'cloud_storage', 'message_queue', 'linear']),
  "display_name": zod.string().min(1).max(integrationsConnectionsUpdateResponseResultDisplayNameMax),
  "host_url": zod.string().url().min(1).max(integrationsConnectionsUpdateResponseResultHostUrlMax),
  "status": zod.enum(['active', 'paused', 'error', 'syncing', 'backfilling']).optional(),
  "status_message": zod.string().optional(),
  "external_project_name": zod.string().min(1).max(integrationsConnectionsUpdateResponseResultExternalProjectNameMax),
  "project": zod.string().uuid().optional(),
  "project_name": zod.string().optional(),
  "public_key_display": zod.string().optional(),
  "secret_key_display": zod.string().optional(),
  "last_synced_at": zod.string().datetime({"offset":true}).optional(),
  "sync_cursor": zod.object({

}).passthrough().optional(),
  "sync_interval_seconds": zod.number().min(integrationsConnectionsUpdateResponseResultSyncIntervalSecondsMin).max(integrationsConnectionsUpdateResponseResultSyncIntervalSecondsMax).optional(),
  "last_error_notified_at": zod.string().datetime({"offset":true}).optional(),
  "backfill_from": zod.string().datetime({"offset":true}).optional(),
  "backfill_completed": zod.boolean().optional(),
  "backfill_progress": zod.object({

}).passthrough().optional(),
  "total_traces_synced": zod.number().min(integrationsConnectionsUpdateResponseResultTotalTracesSyncedMin).max(integrationsConnectionsUpdateResponseResultTotalTracesSyncedMax).optional(),
  "total_spans_synced": zod.number().min(integrationsConnectionsUpdateResponseResultTotalSpansSyncedMin).max(integrationsConnectionsUpdateResponseResultTotalSpansSyncedMax).optional(),
  "total_scores_synced": zod.number().min(integrationsConnectionsUpdateResponseResultTotalScoresSyncedMin).max(integrationsConnectionsUpdateResponseResultTotalScoresSyncedMax).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional(),
  "created_by": zod.string().uuid().optional()
})
})


/**
 * API endpoints for managing integration connections.
 */
export const IntegrationsConnectionsPartialUpdateParams = zod.object({
  "id": zod.string()
})

export const integrationsConnectionsPartialUpdateBodyDisplayNameMax = 255;

export const integrationsConnectionsPartialUpdateBodyPublicKeyMax = 500;

export const integrationsConnectionsPartialUpdateBodySecretKeyMax = 500;

export const integrationsConnectionsPartialUpdateBodyHostUrlMax = 500;

export const integrationsConnectionsPartialUpdateBodySyncIntervalSecondsMin = 60;
export const integrationsConnectionsPartialUpdateBodySyncIntervalSecondsMax = 3600;



export const IntegrationsConnectionsPartialUpdateBody = zod.object({
  "display_name": zod.string().min(1).max(integrationsConnectionsPartialUpdateBodyDisplayNameMax).optional(),
  "public_key": zod.string().min(1).max(integrationsConnectionsPartialUpdateBodyPublicKeyMax).optional(),
  "secret_key": zod.string().min(1).max(integrationsConnectionsPartialUpdateBodySecretKeyMax).optional(),
  "host_url": zod.string().url().min(1).max(integrationsConnectionsPartialUpdateBodyHostUrlMax).optional(),
  "ca_certificate": zod.string().optional(),
  "sync_interval_seconds": zod.number().min(integrationsConnectionsPartialUpdateBodySyncIntervalSecondsMin).max(integrationsConnectionsPartialUpdateBodySyncIntervalSecondsMax).optional()
})

export const integrationsConnectionsPartialUpdateResponseStatusDefault = true;
export const integrationsConnectionsPartialUpdateResponseResultDisplayNameMax = 255;

export const integrationsConnectionsPartialUpdateResponseResultHostUrlMax = 500;

export const integrationsConnectionsPartialUpdateResponseResultExternalProjectNameMax = 255;

export const integrationsConnectionsPartialUpdateResponseResultSyncIntervalSecondsMin = 60;
export const integrationsConnectionsPartialUpdateResponseResultSyncIntervalSecondsMax = 1800;

export const integrationsConnectionsPartialUpdateResponseResultTotalTracesSyncedMin = 0;
export const integrationsConnectionsPartialUpdateResponseResultTotalTracesSyncedMax = 2147483647;

export const integrationsConnectionsPartialUpdateResponseResultTotalSpansSyncedMin = 0;
export const integrationsConnectionsPartialUpdateResponseResultTotalSpansSyncedMax = 2147483647;

export const integrationsConnectionsPartialUpdateResponseResultTotalScoresSyncedMin = 0;
export const integrationsConnectionsPartialUpdateResponseResultTotalScoresSyncedMax = 2147483647;



export const IntegrationsConnectionsPartialUpdateResponse = zod.object({
  "status": zod.boolean().default(integrationsConnectionsPartialUpdateResponseStatusDefault),
  "result": zod.object({
  "id": zod.string().uuid().optional(),
  "platform": zod.enum(['langfuse', 'datadog', 'posthog', 'pagerduty', 'mixpanel', 'cloud_storage', 'message_queue', 'linear']),
  "display_name": zod.string().min(1).max(integrationsConnectionsPartialUpdateResponseResultDisplayNameMax),
  "host_url": zod.string().url().min(1).max(integrationsConnectionsPartialUpdateResponseResultHostUrlMax),
  "status": zod.enum(['active', 'paused', 'error', 'syncing', 'backfilling']).optional(),
  "status_message": zod.string().optional(),
  "external_project_name": zod.string().min(1).max(integrationsConnectionsPartialUpdateResponseResultExternalProjectNameMax),
  "project": zod.string().uuid().optional(),
  "project_name": zod.string().optional(),
  "public_key_display": zod.string().optional(),
  "secret_key_display": zod.string().optional(),
  "last_synced_at": zod.string().datetime({"offset":true}).optional(),
  "sync_cursor": zod.object({

}).passthrough().optional(),
  "sync_interval_seconds": zod.number().min(integrationsConnectionsPartialUpdateResponseResultSyncIntervalSecondsMin).max(integrationsConnectionsPartialUpdateResponseResultSyncIntervalSecondsMax).optional(),
  "last_error_notified_at": zod.string().datetime({"offset":true}).optional(),
  "backfill_from": zod.string().datetime({"offset":true}).optional(),
  "backfill_completed": zod.boolean().optional(),
  "backfill_progress": zod.object({

}).passthrough().optional(),
  "total_traces_synced": zod.number().min(integrationsConnectionsPartialUpdateResponseResultTotalTracesSyncedMin).max(integrationsConnectionsPartialUpdateResponseResultTotalTracesSyncedMax).optional(),
  "total_spans_synced": zod.number().min(integrationsConnectionsPartialUpdateResponseResultTotalSpansSyncedMin).max(integrationsConnectionsPartialUpdateResponseResultTotalSpansSyncedMax).optional(),
  "total_scores_synced": zod.number().min(integrationsConnectionsPartialUpdateResponseResultTotalScoresSyncedMin).max(integrationsConnectionsPartialUpdateResponseResultTotalScoresSyncedMax).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional(),
  "created_by": zod.string().uuid().optional()
})
})


/**
 * API endpoints for managing integration connections.
 */
export const IntegrationsConnectionsDeleteParams = zod.object({
  "id": zod.string()
})


/**
 * Pause syncing for this connection.
 */
export const IntegrationsConnectionsPauseParams = zod.object({
  "id": zod.string()
})

export const IntegrationsConnectionsPauseBody = zod.object({

})

export const integrationsConnectionsPauseResponseStatusDefault = true;
export const integrationsConnectionsPauseResponseResultDisplayNameMax = 255;

export const integrationsConnectionsPauseResponseResultHostUrlMax = 500;

export const integrationsConnectionsPauseResponseResultExternalProjectNameMax = 255;

export const integrationsConnectionsPauseResponseResultSyncIntervalSecondsMin = 60;
export const integrationsConnectionsPauseResponseResultSyncIntervalSecondsMax = 1800;

export const integrationsConnectionsPauseResponseResultTotalTracesSyncedMin = 0;
export const integrationsConnectionsPauseResponseResultTotalTracesSyncedMax = 2147483647;

export const integrationsConnectionsPauseResponseResultTotalSpansSyncedMin = 0;
export const integrationsConnectionsPauseResponseResultTotalSpansSyncedMax = 2147483647;

export const integrationsConnectionsPauseResponseResultTotalScoresSyncedMin = 0;
export const integrationsConnectionsPauseResponseResultTotalScoresSyncedMax = 2147483647;



export const IntegrationsConnectionsPauseResponse = zod.object({
  "status": zod.boolean().default(integrationsConnectionsPauseResponseStatusDefault),
  "result": zod.object({
  "id": zod.string().uuid().optional(),
  "platform": zod.enum(['langfuse', 'datadog', 'posthog', 'pagerduty', 'mixpanel', 'cloud_storage', 'message_queue', 'linear']),
  "display_name": zod.string().min(1).max(integrationsConnectionsPauseResponseResultDisplayNameMax),
  "host_url": zod.string().url().min(1).max(integrationsConnectionsPauseResponseResultHostUrlMax),
  "status": zod.enum(['active', 'paused', 'error', 'syncing', 'backfilling']).optional(),
  "status_message": zod.string().optional(),
  "external_project_name": zod.string().min(1).max(integrationsConnectionsPauseResponseResultExternalProjectNameMax),
  "project": zod.string().uuid().optional(),
  "project_name": zod.string().optional(),
  "public_key_display": zod.string().optional(),
  "secret_key_display": zod.string().optional(),
  "last_synced_at": zod.string().datetime({"offset":true}).optional(),
  "sync_cursor": zod.object({

}).passthrough().optional(),
  "sync_interval_seconds": zod.number().min(integrationsConnectionsPauseResponseResultSyncIntervalSecondsMin).max(integrationsConnectionsPauseResponseResultSyncIntervalSecondsMax).optional(),
  "last_error_notified_at": zod.string().datetime({"offset":true}).optional(),
  "backfill_from": zod.string().datetime({"offset":true}).optional(),
  "backfill_completed": zod.boolean().optional(),
  "backfill_progress": zod.object({

}).passthrough().optional(),
  "total_traces_synced": zod.number().min(integrationsConnectionsPauseResponseResultTotalTracesSyncedMin).max(integrationsConnectionsPauseResponseResultTotalTracesSyncedMax).optional(),
  "total_spans_synced": zod.number().min(integrationsConnectionsPauseResponseResultTotalSpansSyncedMin).max(integrationsConnectionsPauseResponseResultTotalSpansSyncedMax).optional(),
  "total_scores_synced": zod.number().min(integrationsConnectionsPauseResponseResultTotalScoresSyncedMin).max(integrationsConnectionsPauseResponseResultTotalScoresSyncedMax).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional(),
  "created_by": zod.string().uuid().optional()
})
})


/**
 * Resume syncing for a paused connection.
 */
export const IntegrationsConnectionsResumeParams = zod.object({
  "id": zod.string()
})

export const IntegrationsConnectionsResumeBody = zod.object({

})

export const integrationsConnectionsResumeResponseStatusDefault = true;
export const integrationsConnectionsResumeResponseResultDisplayNameMax = 255;

export const integrationsConnectionsResumeResponseResultHostUrlMax = 500;

export const integrationsConnectionsResumeResponseResultExternalProjectNameMax = 255;

export const integrationsConnectionsResumeResponseResultSyncIntervalSecondsMin = 60;
export const integrationsConnectionsResumeResponseResultSyncIntervalSecondsMax = 1800;

export const integrationsConnectionsResumeResponseResultTotalTracesSyncedMin = 0;
export const integrationsConnectionsResumeResponseResultTotalTracesSyncedMax = 2147483647;

export const integrationsConnectionsResumeResponseResultTotalSpansSyncedMin = 0;
export const integrationsConnectionsResumeResponseResultTotalSpansSyncedMax = 2147483647;

export const integrationsConnectionsResumeResponseResultTotalScoresSyncedMin = 0;
export const integrationsConnectionsResumeResponseResultTotalScoresSyncedMax = 2147483647;



export const IntegrationsConnectionsResumeResponse = zod.object({
  "status": zod.boolean().default(integrationsConnectionsResumeResponseStatusDefault),
  "result": zod.object({
  "id": zod.string().uuid().optional(),
  "platform": zod.enum(['langfuse', 'datadog', 'posthog', 'pagerduty', 'mixpanel', 'cloud_storage', 'message_queue', 'linear']),
  "display_name": zod.string().min(1).max(integrationsConnectionsResumeResponseResultDisplayNameMax),
  "host_url": zod.string().url().min(1).max(integrationsConnectionsResumeResponseResultHostUrlMax),
  "status": zod.enum(['active', 'paused', 'error', 'syncing', 'backfilling']).optional(),
  "status_message": zod.string().optional(),
  "external_project_name": zod.string().min(1).max(integrationsConnectionsResumeResponseResultExternalProjectNameMax),
  "project": zod.string().uuid().optional(),
  "project_name": zod.string().optional(),
  "public_key_display": zod.string().optional(),
  "secret_key_display": zod.string().optional(),
  "last_synced_at": zod.string().datetime({"offset":true}).optional(),
  "sync_cursor": zod.object({

}).passthrough().optional(),
  "sync_interval_seconds": zod.number().min(integrationsConnectionsResumeResponseResultSyncIntervalSecondsMin).max(integrationsConnectionsResumeResponseResultSyncIntervalSecondsMax).optional(),
  "last_error_notified_at": zod.string().datetime({"offset":true}).optional(),
  "backfill_from": zod.string().datetime({"offset":true}).optional(),
  "backfill_completed": zod.boolean().optional(),
  "backfill_progress": zod.object({

}).passthrough().optional(),
  "total_traces_synced": zod.number().min(integrationsConnectionsResumeResponseResultTotalTracesSyncedMin).max(integrationsConnectionsResumeResponseResultTotalTracesSyncedMax).optional(),
  "total_spans_synced": zod.number().min(integrationsConnectionsResumeResponseResultTotalSpansSyncedMin).max(integrationsConnectionsResumeResponseResultTotalSpansSyncedMax).optional(),
  "total_scores_synced": zod.number().min(integrationsConnectionsResumeResponseResultTotalScoresSyncedMin).max(integrationsConnectionsResumeResponseResultTotalScoresSyncedMax).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional(),
  "created_by": zod.string().uuid().optional()
})
})


/**
 * Trigger an immediate sync for this connection.
 */
export const IntegrationsConnectionsSyncNowParams = zod.object({
  "id": zod.string()
})

export const IntegrationsConnectionsSyncNowBody = zod.object({

})

export const integrationsConnectionsSyncNowResponseStatusDefault = true;


export const IntegrationsConnectionsSyncNowResponse = zod.object({
  "status": zod.boolean().default(integrationsConnectionsSyncNowResponseStatusDefault),
  "result": zod.object({
  "message": zod.string().min(1)
})
})


/**
 * Read-only viewset for sync logs.
 */
export const integrationsSyncLogsListQueryPageNumberDefault = 0;
export const integrationsSyncLogsListQueryPageNumberMin = 0;

export const integrationsSyncLogsListQueryPageSizeDefault = 20;
export const integrationsSyncLogsListQueryPageSizeMax = 100;



export const IntegrationsSyncLogsListQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.'),
  "page_number": zod.number().min(integrationsSyncLogsListQueryPageNumberMin).default(integrationsSyncLogsListQueryPageNumberDefault),
  "page_size": zod.number().min(1).max(integrationsSyncLogsListQueryPageSizeMax).default(integrationsSyncLogsListQueryPageSizeDefault),
  "connection_id": zod.string().uuid().optional()
})

export const integrationsSyncLogsListResponseStatusDefault = true;


export const IntegrationsSyncLogsListResponse = zod.object({
  "status": zod.boolean().default(integrationsSyncLogsListResponseStatusDefault),
  "result": zod.object({
  "metadata": zod.object({
  "total_count": zod.number(),
  "current_page": zod.number(),
  "page_size": zod.number(),
  "total_pages": zod.number(),
  "next_page": zod.number().optional()
}),
  "sync_logs": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "connection": zod.string().uuid().optional(),
  "status": zod.enum(['success', 'partial', 'failed', 'rate_limited', 'no_new_data']).optional(),
  "started_at": zod.string().datetime({"offset":true}).optional(),
  "completed_at": zod.string().datetime({"offset":true}).optional(),
  "traces_fetched": zod.number().optional(),
  "traces_created": zod.number().optional(),
  "traces_updated": zod.number().optional(),
  "spans_synced": zod.number().optional(),
  "scores_synced": zod.number().optional(),
  "error_message": zod.string().min(1).optional(),
  "error_details": zod.object({

}).passthrough().optional(),
  "sync_from": zod.string().datetime({"offset":true}).optional(),
  "sync_to": zod.string().datetime({"offset":true}).optional()
}))
})
})


/**
 * Read-only viewset for sync logs.
 */
export const IntegrationsSyncLogsReadParams = zod.object({
  "id": zod.string()
})




export const IntegrationsSyncLogsReadResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "connection": zod.string().uuid().optional(),
  "status": zod.enum(['success', 'partial', 'failed', 'rate_limited', 'no_new_data']).optional(),
  "started_at": zod.string().datetime({"offset":true}).optional(),
  "completed_at": zod.string().datetime({"offset":true}).optional(),
  "traces_fetched": zod.number().optional(),
  "traces_created": zod.number().optional(),
  "traces_updated": zod.number().optional(),
  "spans_synced": zod.number().optional(),
  "scores_synced": zod.number().optional(),
  "error_message": zod.string().min(1).optional(),
  "error_details": zod.object({

}).passthrough().optional(),
  "sync_from": zod.string().datetime({"offset":true}).optional(),
  "sync_to": zod.string().datetime({"offset":true}).optional()
})


/**
 * Usage summary (total calls, sessions, latency).
 */
export const mcpAnalyticsSummaryListResponseStatusDefault = true;

export const McpAnalyticsSummaryListResponse = zod.object({
  "status": zod.boolean().default(mcpAnalyticsSummaryListResponseStatusDefault),
  "result": zod.object({
  "total_calls": zod.number(),
  "total_sessions": zod.number(),
  "avg_latency_ms": zod.number(),
  "error_rate": zod.number(),
  "active_sessions": zod.number()
})
})


/**
 * Tool calls over time (hourly buckets).
 */
export const mcpAnalyticsTimelineListResponseStatusDefault = true;

export const McpAnalyticsTimelineListResponse = zod.object({
  "status": zod.boolean().default(mcpAnalyticsTimelineListResponseStatusDefault),
  "result": zod.array(zod.object({
  "timestamp": zod.string().datetime({"offset":true}),
  "call_count": zod.number()
}))
})


/**
 * Per-tool usage breakdown.
 */
export const mcpAnalyticsToolsListResponseStatusDefault = true;


export const McpAnalyticsToolsListResponse = zod.object({
  "status": zod.boolean().default(mcpAnalyticsToolsListResponseStatusDefault),
  "result": zod.array(zod.object({
  "tool_name": zod.string().min(1),
  "call_count": zod.number(),
  "avg_latency_ms": zod.number(),
  "error_rate": zod.number()
}))
})


/**
 * Get or update MCP connection configuration.
 */
export const mcpConfigListResponseStatusDefault = true;
export const mcpConfigListResponseResultClientNameMax = 100;

export const mcpConfigListResponseResultClientVersionMax = 50;




export const McpConfigListResponse = zod.object({
  "status": zod.boolean().default(mcpConfigListResponseStatusDefault),
  "result": zod.object({
  "id": zod.string().uuid().optional(),
  "connection_mode": zod.enum(['remote', 'stdio']).optional(),
  "is_active": zod.boolean().optional(),
  "client_name": zod.string().max(mcpConfigListResponseResultClientNameMax).optional(),
  "client_version": zod.string().max(mcpConfigListResponseResultClientVersionMax).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional(),
  "tool_config": zod.object({
  "enabled_groups": zod.object({

}).passthrough().optional(),
  "disabled_tools": zod.object({

}).passthrough().optional(),
  "available_groups": zod.string().optional()
}).optional(),
  "mcp_url": zod.string().min(1).optional()
})
})


/**
 * Get or update MCP connection configuration.
 */
export const McpConfigUpdateBody = zod.object({
  "connection_mode": zod.enum(['remote', 'stdio']).optional(),
  "is_active": zod.boolean().optional()
})

export const mcpConfigUpdateResponseStatusDefault = true;
export const mcpConfigUpdateResponseResultClientNameMax = 100;

export const mcpConfigUpdateResponseResultClientVersionMax = 50;




export const McpConfigUpdateResponse = zod.object({
  "status": zod.boolean().default(mcpConfigUpdateResponseStatusDefault),
  "result": zod.object({
  "id": zod.string().uuid().optional(),
  "connection_mode": zod.enum(['remote', 'stdio']).optional(),
  "is_active": zod.boolean().optional(),
  "client_name": zod.string().max(mcpConfigUpdateResponseResultClientNameMax).optional(),
  "client_version": zod.string().max(mcpConfigUpdateResponseResultClientVersionMax).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional(),
  "tool_config": zod.object({
  "enabled_groups": zod.object({

}).passthrough().optional(),
  "disabled_tools": zod.object({

}).passthrough().optional(),
  "available_groups": zod.string().optional()
}).optional(),
  "mcp_url": zod.string().min(1).optional()
})
})


/**
 * Get or update tool group configuration.
 */
export const mcpConfigToolGroupsListResponseStatusDefault = true;

export const McpConfigToolGroupsListResponse = zod.object({
  "status": zod.boolean().default(mcpConfigToolGroupsListResponseStatusDefault),
  "result": zod.object({
  "enabled_groups": zod.object({

}).passthrough().optional(),
  "disabled_tools": zod.object({

}).passthrough().optional(),
  "available_groups": zod.string().optional()
})
})


/**
 * Get or update tool group configuration.
 */




export const McpConfigToolGroupsUpdateBody = zod.object({
  "enabled_groups": zod.array(zod.string().min(1)).optional(),
  "disabled_tools": zod.array(zod.string().min(1)).optional()
})

export const mcpConfigToolGroupsUpdateResponseStatusDefault = true;

export const McpConfigToolGroupsUpdateResponse = zod.object({
  "status": zod.boolean().default(mcpConfigToolGroupsUpdateResponseStatusDefault),
  "result": zod.object({
  "enabled_groups": zod.object({

}).passthrough().optional(),
  "disabled_tools": zod.object({

}).passthrough().optional(),
  "available_groups": zod.string().optional()
})
})


/**
 * Unauthenticated health check for MCP server.
 */
export const mcpHealthListResponseStatusDefault = true;


export const McpHealthListResponse = zod.object({
  "status": zod.boolean().default(mcpHealthListResponseStatusDefault),
  "result": zod.object({
  "healthy": zod.boolean(),
  "tool_count": zod.number(),
  "version": zod.string().min(1)
})
})


/**
 * Execute a tool call via internal API (used by stdio proxy).
 */

export const mcpInternalToolCallCreateBodyParamsDefault = {  };

export const McpInternalToolCallCreateBody = zod.object({
  "tool_name": zod.string().min(1),
  "params": zod.record(zod.string(), zod.string()).default(mcpInternalToolCallCreateBodyParamsDefault),
  "session_id": zod.string().uuid().optional()
})

export const McpInternalToolCallCreateResponse = zod.object({
  "status": zod.boolean(),
  "result": zod.object({
  "content": zod.string(),
  "data": zod.object({

}).passthrough(),
  "is_error": zod.boolean(),
  "error_code": zod.string()
}),
  "session_id": zod.string().uuid()
})


/**
 * List available tools for the authenticated user.
 */
export const mcpInternalToolsListResponseStatusDefault = true;





export const McpInternalToolsListResponse = zod.object({
  "status": zod.boolean().default(mcpInternalToolsListResponseStatusDefault),
  "result": zod.object({
  "tools": zod.array(zod.object({
  "name": zod.string().min(1).optional(),
  "category": zod.string().min(1).optional(),
  "description": zod.string().optional(),
  "parameters": zod.array(zod.object({
  "name": zod.string().min(1).optional(),
  "type": zod.string().min(1).optional(),
  "description": zod.string().optional(),
  "required": zod.boolean().optional()
})).optional(),
  "returns": zod.object({

}).passthrough().optional(),
  "metadata": zod.object({

}).passthrough().optional()
})),
  "total": zod.number(),
  "session_id": zod.string().uuid()
})
})


/**
 * Called by the frontend consent page to display what the client is requesting.
Public endpoint - no auth required (just shows what's being requested).
 * @summary GET /mcp/oauth/approve-info/ - Get approval request details.
 */
export const mcpOauthApproveInfoListResponseStatusDefault = true;








export const McpOauthApproveInfoListResponse = zod.object({
  "status": zod.boolean().default(mcpOauthApproveInfoListResponseStatusDefault),
  "result": zod.object({
  "client_name": zod.string().min(1),
  "client_id": zod.string().min(1),
  "scopes": zod.array(zod.string().min(1)),
  "redirect_uri": zod.string().min(1),
  "available_groups": zod.array(zod.object({
  "slug": zod.string().min(1),
  "name": zod.string().min(1),
  "description": zod.string().min(1),
  "checked": zod.boolean().optional(),
  "enabled": zod.boolean().optional()
}))
})
})


/**
 * Called by the frontend consent page when the user approves or denies.
Requires JWT auth (authenticated user).
 * @summary POST /mcp/oauth/approve/ - Process user approval decision.
 */

export const mcpOauthApproveCreateBodyApprovedDefault = false;
export const mcpOauthApproveCreateBodySelectedGroupsDefault = [];

export const McpOauthApproveCreateBody = zod.object({
  "request_id": zod.string().min(1),
  "approved": zod.boolean().default(mcpOauthApproveCreateBodyApprovedDefault),
  "selected_groups": zod.array(zod.string().min(1)).default(mcpOauthApproveCreateBodySelectedGroupsDefault)
})

export const mcpOauthApproveCreateResponseStatusDefault = true;


export const McpOauthApproveCreateResponse = zod.object({
  "status": zod.boolean().default(mcpOauthApproveCreateResponseStatusDefault),
  "result": zod.object({
  "redirect_url": zod.string().min(1)
})
})


/**
 * GET /mcp/oauth/authorize/ â€” Return consent screen data.
 */
export const mcpOauthAuthorizeListResponseStatusDefault = true;







export const McpOauthAuthorizeListResponse = zod.object({
  "status": zod.boolean().default(mcpOauthAuthorizeListResponseStatusDefault),
  "result": zod.object({
  "client_name": zod.string().min(1),
  "client_id": zod.string().min(1),
  "redirect_uri": zod.string().min(1),
  "state": zod.string(),
  "available_groups": zod.array(zod.object({
  "slug": zod.string().min(1),
  "name": zod.string().min(1),
  "description": zod.string().min(1),
  "checked": zod.boolean().optional(),
  "enabled": zod.boolean().optional()
}))
})
})


/**
 * POST /mcp/oauth/consent/ â€” Process user consent decision.
 */


export const mcpOauthConsentCreateBodyApprovedDefault = false;
export const mcpOauthConsentCreateBodySelectedGroupsDefault = [];

export const McpOauthConsentCreateBody = zod.object({
  "client_id": zod.string().min(1),
  "redirect_uri": zod.string().min(1),
  "state": zod.string().optional(),
  "approved": zod.boolean().default(mcpOauthConsentCreateBodyApprovedDefault),
  "selected_groups": zod.array(zod.string().min(1)).default(mcpOauthConsentCreateBodySelectedGroupsDefault)
})

export const mcpOauthConsentCreateResponseStatusDefault = true;


export const McpOauthConsentCreateResponse = zod.object({
  "status": zod.boolean().default(mcpOauthConsentCreateResponseStatusDefault),
  "result": zod.object({
  "redirect_url": zod.string().min(1)
})
})


/**
 * POST /mcp/oauth/token/ â€” Exchange code or refresh token for access token.
 */







export const McpOauthTokenCreateBody = zod.object({
  "grant_type": zod.enum(['authorization_code', 'refresh_token']),
  "code": zod.string().min(1).optional(),
  "refresh_token": zod.string().min(1).optional(),
  "client_id": zod.string().min(1),
  "client_secret": zod.string().min(1),
  "redirect_uri": zod.string().min(1).optional()
})





export const McpOauthTokenCreateResponse = zod.object({
  "access_token": zod.string().min(1),
  "token_type": zod.enum(['Bearer']),
  "expires_in": zod.number(),
  "refresh_token": zod.string().min(1).optional(),
  "scope": zod.string()
})


/**
 * List active and recent MCP sessions.
 */
export const mcpSessionsListResponseStatusDefault = true;
export const mcpSessionsListResponseResultItemClientNameMax = 100;

export const mcpSessionsListResponseResultItemClientVersionMax = 50;

export const mcpSessionsListResponseResultItemClientOsMax = 50;

export const mcpSessionsListResponseResultItemToolCallCountMin = 0;
export const mcpSessionsListResponseResultItemToolCallCountMax = 2147483647;

export const mcpSessionsListResponseResultItemErrorCountMin = 0;
export const mcpSessionsListResponseResultItemErrorCountMax = 2147483647;



export const McpSessionsListResponse = zod.object({
  "status": zod.boolean().default(mcpSessionsListResponseStatusDefault),
  "result": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "status": zod.enum(['active', 'idle', 'disconnected', 'revoked']).optional(),
  "transport": zod.enum(['streamable_http', 'sse', 'stdio']).optional(),
  "client_name": zod.string().max(mcpSessionsListResponseResultItemClientNameMax).optional(),
  "client_version": zod.string().max(mcpSessionsListResponseResultItemClientVersionMax).optional(),
  "client_os": zod.string().max(mcpSessionsListResponseResultItemClientOsMax).optional(),
  "started_at": zod.string().datetime({"offset":true}).optional(),
  "last_activity_at": zod.string().datetime({"offset":true}).optional(),
  "ended_at": zod.string().datetime({"offset":true}).optional(),
  "tool_call_count": zod.number().min(mcpSessionsListResponseResultItemToolCallCountMin).max(mcpSessionsListResponseResultItemToolCallCountMax).optional(),
  "error_count": zod.number().min(mcpSessionsListResponseResultItemErrorCountMin).max(mcpSessionsListResponseResultItemErrorCountMax).optional()
}))
})


/**
 * Revoke a specific MCP session.
 */
export const McpSessionsDeleteParams = zod.object({
  "session_id": zod.string()
})

export const mcpSessionsDeleteResponseStatusDefault = true;


export const McpSessionsDeleteResponse = zod.object({
  "status": zod.boolean().default(mcpSessionsDeleteResponseStatusDefault),
  "result": zod.object({
  "message": zod.string().min(1)
})
})


/**
 * POST /model-hub/ai-eval-writer/
 */

export const modelHubAiEvalWriterCreateBodyOutputFormatDefault = `prompt`;

export const ModelHubAiEvalWriterCreateBody = zod.object({
  "description": zod.string().min(1),
  "output_format": zod.enum(['prompt', 'messages', 'test_data']).default(modelHubAiEvalWriterCreateBodyOutputFormatDefault)
})

export const modelHubAiEvalWriterCreateResponseStatusDefault = true;


export const ModelHubAiEvalWriterCreateResponse = zod.object({
  "status": zod.boolean().default(modelHubAiEvalWriterCreateResponseStatusDefault),
  "result": zod.object({
  "prompt": zod.string().min(1).optional(),
  "messages": zod.array(zod.record(zod.string(), zod.string())).optional(),
  "test_data": zod.record(zod.string(), zod.string()).optional()
})
})


/**
 * Request body:
{
    "query": "show me LLM evals that are pass/fail",
    "schema": [
        {
            "field": "eval_type",
            "label": "Eval Type",
            "type": "enum",
            "operators": ["is", "is_not"],
            "choices": ["llm", "code", "agent"]
        },
        ...
    ]
}
 * @summary POST /model-hub/ai-filter/
 */
export const modelHubAiFilterCreateBodyModeDefault = `build_filters`;


export const modelHubAiFilterCreateBodySchemaItemOperatorsDefault = [];
export const modelHubAiFilterCreateBodySchemaItemChoicesDefault = [];
export const modelHubAiFilterCreateBodySchemaItemChoiceLabelsDefault = {  };
export const modelHubAiFilterCreateBodySourceDefault = `traces`;

export const ModelHubAiFilterCreateBody = zod.object({
  "mode": zod.enum(['build_filters', 'select_fields', 'smart']).default(modelHubAiFilterCreateBodyModeDefault),
  "query": zod.string().min(1),
  "schema": zod.array(zod.object({
  "field": zod.string().min(1),
  "label": zod.string().optional(),
  "type": zod.string().optional(),
  "category": zod.string().optional(),
  "operators": zod.array(zod.string().min(1)).default(modelHubAiFilterCreateBodySchemaItemOperatorsDefault),
  "choices": zod.array(zod.object({

}).passthrough()).default(modelHubAiFilterCreateBodySchemaItemChoicesDefault),
  "choice_labels": zod.record(zod.string(), zod.string().min(1)).default(modelHubAiFilterCreateBodySchemaItemChoiceLabelsDefault)
})),
  "source": zod.enum(['traces', 'dataset']).default(modelHubAiFilterCreateBodySourceDefault),
  "project_id": zod.string().uuid().optional(),
  "dataset_id": zod.string().uuid().optional()
})

export const modelHubAiFilterCreateResponseStatusDefault = true;




export const ModelHubAiFilterCreateResponse = zod.object({
  "status": zod.boolean().default(modelHubAiFilterCreateResponseStatusDefault),
  "result": zod.object({
  "filters": zod.array(zod.object({
  "field": zod.string().min(1),
  "operator": zod.string().min(1),
  "value": zod.object({

}).passthrough().optional().describe('Any valid JSON value.')
})).optional(),
  "fields": zod.array(zod.string().min(1)).optional()
})
})


export const modelHubAnnotationQueuesListQueryArchivedDefault = false;


export const ModelHubAnnotationQueuesListQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.'),
  "status": zod.string().optional(),
  "search": zod.string().optional(),
  "include_counts": zod.boolean().optional(),
  "archived": zod.boolean().default(modelHubAnnotationQueuesListQueryArchivedDefault),
  "page_size": zod.number().min(1).optional()
})

export const modelHubAnnotationQueuesListResponseResultsItemNameMax = 255;

export const modelHubAnnotationQueuesListResponseResultsItemAnnotationsRequiredMin = -2147483648;
export const modelHubAnnotationQueuesListResponseResultsItemAnnotationsRequiredMax = 2147483647;

export const modelHubAnnotationQueuesListResponseResultsItemReservationTimeoutMinutesMin = -2147483648;
export const modelHubAnnotationQueuesListResponseResultsItemReservationTimeoutMinutesMax = 2147483647;



export const modelHubAnnotationQueuesListResponseResultsItemLabelsItemOrderMin = -2147483648;
export const modelHubAnnotationQueuesListResponseResultsItemLabelsItemOrderMax = 2147483647;



export const modelHubAnnotationQueuesListResponseResultsItemAnnotatorsItemRoleDefault = `annotator`;


export const modelHubAnnotationQueuesListResponseResultsItemAnnotatorIdsDefault = [];
export const modelHubAnnotationQueuesListResponseResultsItemAnnotatorRolesDefault = {  };


export const ModelHubAnnotationQueuesListResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(modelHubAnnotationQueuesListResponseResultsItemNameMax),
  "description": zod.string().optional(),
  "instructions": zod.string().optional(),
  "status": zod.enum(['draft', 'active', 'paused', 'completed']).optional(),
  "assignment_strategy": zod.enum(['manual', 'round_robin', 'load_balanced']).optional(),
  "annotations_required": zod.number().min(modelHubAnnotationQueuesListResponseResultsItemAnnotationsRequiredMin).max(modelHubAnnotationQueuesListResponseResultsItemAnnotationsRequiredMax).optional(),
  "reservation_timeout_minutes": zod.number().min(modelHubAnnotationQueuesListResponseResultsItemReservationTimeoutMinutesMin).max(modelHubAnnotationQueuesListResponseResultsItemReservationTimeoutMinutesMax).optional(),
  "requires_review": zod.boolean().optional(),
  "auto_assign": zod.boolean().optional().describe('When enabled, all queue members can annotate any item without explicit assignment.'),
  "organization": zod.string().uuid().optional(),
  "project": zod.string().uuid().optional(),
  "dataset": zod.string().uuid().optional(),
  "agent_definition": zod.string().uuid().optional(),
  "is_default": zod.boolean().optional(),
  "labels": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "label_id": zod.string().uuid(),
  "name": zod.string().min(1).optional(),
  "type": zod.string().min(1).optional(),
  "required": zod.boolean().optional(),
  "order": zod.number().min(modelHubAnnotationQueuesListResponseResultsItemLabelsItemOrderMin).max(modelHubAnnotationQueuesListResponseResultsItemLabelsItemOrderMax).optional()
})).optional(),
  "annotators": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "user_id": zod.string().uuid(),
  "name": zod.string().min(1).optional(),
  "email": zod.string().email().min(1).optional(),
  "role": zod.string().min(1).default(modelHubAnnotationQueuesListResponseResultsItemAnnotatorsItemRoleDefault),
  "roles": zod.string().optional()
})).optional(),
  "label_ids": zod.array(zod.string().uuid()).min(1),
  "annotator_ids": zod.array(zod.string().uuid()).default(modelHubAnnotationQueuesListResponseResultsItemAnnotatorIdsDefault),
  "annotator_roles": zod.record(zod.string(), zod.object({

}).passthrough()).default(modelHubAnnotationQueuesListResponseResultsItemAnnotatorRolesDefault),
  "label_count": zod.number().optional(),
  "annotator_count": zod.number().optional(),
  "item_count": zod.number().optional(),
  "completed_count": zod.number().optional(),
  "created_by": zod.string().uuid().optional(),
  "created_by_name": zod.string().min(1).optional(),
  "viewer_role": zod.string().optional(),
  "viewer_roles": zod.string().optional(),
  "deleted": zod.boolean().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional()
}))
})


export const modelHubAnnotationQueuesCreateBodyNameMax = 255;

export const modelHubAnnotationQueuesCreateBodyAnnotationsRequiredMin = -2147483648;
export const modelHubAnnotationQueuesCreateBodyAnnotationsRequiredMax = 2147483647;

export const modelHubAnnotationQueuesCreateBodyReservationTimeoutMinutesMin = -2147483648;
export const modelHubAnnotationQueuesCreateBodyReservationTimeoutMinutesMax = 2147483647;


export const modelHubAnnotationQueuesCreateBodyAnnotatorIdsDefault = [];
export const modelHubAnnotationQueuesCreateBodyAnnotatorRolesDefault = {  };

export const ModelHubAnnotationQueuesCreateBody = zod.object({
  "name": zod.string().min(1).max(modelHubAnnotationQueuesCreateBodyNameMax),
  "description": zod.string().optional(),
  "instructions": zod.string().optional(),
  "assignment_strategy": zod.enum(['manual', 'round_robin', 'load_balanced']).optional(),
  "annotations_required": zod.number().min(modelHubAnnotationQueuesCreateBodyAnnotationsRequiredMin).max(modelHubAnnotationQueuesCreateBodyAnnotationsRequiredMax).optional(),
  "reservation_timeout_minutes": zod.number().min(modelHubAnnotationQueuesCreateBodyReservationTimeoutMinutesMin).max(modelHubAnnotationQueuesCreateBodyReservationTimeoutMinutesMax).optional(),
  "requires_review": zod.boolean().optional(),
  "auto_assign": zod.boolean().optional().describe('When enabled, all queue members can annotate any item without explicit assignment.'),
  "label_ids": zod.array(zod.string().uuid()).min(1),
  "annotator_ids": zod.array(zod.string().uuid()).default(modelHubAnnotationQueuesCreateBodyAnnotatorIdsDefault),
  "annotator_roles": zod.record(zod.string(), zod.object({

}).passthrough()).default(modelHubAnnotationQueuesCreateBodyAnnotatorRolesDefault)
})


/**
 * Find annotation queues for a given source that the current user can annotate.
Includes queues where:
- The source is a queue item AND the user is an annotator in that queue
  (regardless of whether the item is explicitly assigned to them)

Query params:
  - source_type, source_id  (single source)
  - OR sources (JSON array of {source_type, source_id} objects for multi-source lookup)
 */
export const ModelHubAnnotationQueuesForSourceQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.'),
  "source_type": zod.enum(['call_execution', 'dataset_row', 'observation_span', 'prototype_run', 'trace', 'trace_session']).optional(),
  "source_id": zod.string().optional(),
  "sources": zod.string().optional()
})

export const modelHubAnnotationQueuesForSourceResponseStatusDefault = true;









export const ModelHubAnnotationQueuesForSourceResponse = zod.object({
  "status": zod.boolean().default(modelHubAnnotationQueuesForSourceResponseStatusDefault),
  "result": zod.array(zod.object({
  "queue": zod.object({
  "id": zod.string().uuid(),
  "name": zod.string().min(1),
  "instructions": zod.string(),
  "is_default": zod.boolean()
}),
  "item": zod.object({
  "id": zod.string().uuid(),
  "status": zod.string().min(1),
  "source_type": zod.string().min(1),
  "source_id": zod.string().min(1)
}),
  "labels": zod.array(zod.object({
  "id": zod.string().uuid(),
  "name": zod.string().min(1),
  "type": zod.string().min(1),
  "settings": zod.object({

}).passthrough(),
  "description": zod.string().optional(),
  "allow_notes": zod.boolean(),
  "required": zod.boolean(),
  "order": zod.number()
})),
  "existing_scores": zod.record(zod.string(), zod.object({

}).passthrough()),
  "existing_notes": zod.string(),
  "existing_label_notes": zod.record(zod.string(), zod.string().min(1)),
  "span_notes": zod.array(zod.object({

}).passthrough()),
  "span_notes_source_id": zod.string().min(1).optional()
}))
})


/**
 * Get or create the default annotation queue for a project, dataset, or agent definition.
Default queues are open to all org members (no annotator restriction).

Body params (one of):
  - project_id
  - dataset_id
  - agent_definition_id
 */
export const ModelHubAnnotationQueuesGetOrCreateDefaultBody = zod.object({
  "project_id": zod.string().uuid().optional(),
  "dataset_id": zod.string().uuid().optional(),
  "agent_definition_id": zod.string().uuid().optional()
})

export const modelHubAnnotationQueuesGetOrCreateDefaultResponseStatusDefault = true;





export const ModelHubAnnotationQueuesGetOrCreateDefaultResponse = zod.object({
  "status": zod.boolean().default(modelHubAnnotationQueuesGetOrCreateDefaultResponseStatusDefault),
  "result": zod.object({
  "queue": zod.object({
  "id": zod.string().uuid(),
  "name": zod.string().min(1),
  "description": zod.string().optional(),
  "instructions": zod.string().optional(),
  "status": zod.string().min(1),
  "is_default": zod.boolean()
}),
  "labels": zod.array(zod.object({
  "id": zod.string().uuid(),
  "name": zod.string().min(1),
  "type": zod.string().min(1),
  "settings": zod.object({

}).passthrough(),
  "description": zod.string().optional(),
  "allow_notes": zod.boolean(),
  "required": zod.boolean(),
  "order": zod.number()
})),
  "created": zod.boolean(),
  "action": zod.enum(['created', 'restored', 'fetched'])
})
})


export const ModelHubAnnotationQueuesReadParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this annotation queue.')
})

export const modelHubAnnotationQueuesReadResponseNameMax = 255;

export const modelHubAnnotationQueuesReadResponseAnnotationsRequiredMin = -2147483648;
export const modelHubAnnotationQueuesReadResponseAnnotationsRequiredMax = 2147483647;

export const modelHubAnnotationQueuesReadResponseReservationTimeoutMinutesMin = -2147483648;
export const modelHubAnnotationQueuesReadResponseReservationTimeoutMinutesMax = 2147483647;



export const modelHubAnnotationQueuesReadResponseLabelsItemOrderMin = -2147483648;
export const modelHubAnnotationQueuesReadResponseLabelsItemOrderMax = 2147483647;



export const modelHubAnnotationQueuesReadResponseAnnotatorsItemRoleDefault = `annotator`;


export const modelHubAnnotationQueuesReadResponseAnnotatorIdsDefault = [];
export const modelHubAnnotationQueuesReadResponseAnnotatorRolesDefault = {  };


export const ModelHubAnnotationQueuesReadResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(modelHubAnnotationQueuesReadResponseNameMax),
  "description": zod.string().optional(),
  "instructions": zod.string().optional(),
  "status": zod.enum(['draft', 'active', 'paused', 'completed']).optional(),
  "assignment_strategy": zod.enum(['manual', 'round_robin', 'load_balanced']).optional(),
  "annotations_required": zod.number().min(modelHubAnnotationQueuesReadResponseAnnotationsRequiredMin).max(modelHubAnnotationQueuesReadResponseAnnotationsRequiredMax).optional(),
  "reservation_timeout_minutes": zod.number().min(modelHubAnnotationQueuesReadResponseReservationTimeoutMinutesMin).max(modelHubAnnotationQueuesReadResponseReservationTimeoutMinutesMax).optional(),
  "requires_review": zod.boolean().optional(),
  "auto_assign": zod.boolean().optional().describe('When enabled, all queue members can annotate any item without explicit assignment.'),
  "organization": zod.string().uuid().optional(),
  "project": zod.string().uuid().optional(),
  "dataset": zod.string().uuid().optional(),
  "agent_definition": zod.string().uuid().optional(),
  "is_default": zod.boolean().optional(),
  "labels": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "label_id": zod.string().uuid(),
  "name": zod.string().min(1).optional(),
  "type": zod.string().min(1).optional(),
  "required": zod.boolean().optional(),
  "order": zod.number().min(modelHubAnnotationQueuesReadResponseLabelsItemOrderMin).max(modelHubAnnotationQueuesReadResponseLabelsItemOrderMax).optional()
})).optional(),
  "annotators": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "user_id": zod.string().uuid(),
  "name": zod.string().min(1).optional(),
  "email": zod.string().email().min(1).optional(),
  "role": zod.string().min(1).default(modelHubAnnotationQueuesReadResponseAnnotatorsItemRoleDefault),
  "roles": zod.string().optional()
})).optional(),
  "label_ids": zod.array(zod.string().uuid()).min(1),
  "annotator_ids": zod.array(zod.string().uuid()).default(modelHubAnnotationQueuesReadResponseAnnotatorIdsDefault),
  "annotator_roles": zod.record(zod.string(), zod.object({

}).passthrough()).default(modelHubAnnotationQueuesReadResponseAnnotatorRolesDefault),
  "label_count": zod.number().optional(),
  "annotator_count": zod.number().optional(),
  "item_count": zod.number().optional(),
  "completed_count": zod.number().optional(),
  "created_by": zod.string().uuid().optional(),
  "created_by_name": zod.string().min(1).optional(),
  "viewer_role": zod.string().optional(),
  "viewer_roles": zod.string().optional(),
  "deleted": zod.boolean().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional()
})


/**
 * Only managers of the queue may update queue settings.
 */
export const ModelHubAnnotationQueuesUpdateParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this annotation queue.')
})

export const modelHubAnnotationQueuesUpdateBodyNameMax = 255;

export const modelHubAnnotationQueuesUpdateBodyAnnotationsRequiredMin = -2147483648;
export const modelHubAnnotationQueuesUpdateBodyAnnotationsRequiredMax = 2147483647;

export const modelHubAnnotationQueuesUpdateBodyReservationTimeoutMinutesMin = -2147483648;
export const modelHubAnnotationQueuesUpdateBodyReservationTimeoutMinutesMax = 2147483647;


export const modelHubAnnotationQueuesUpdateBodyAnnotatorIdsDefault = [];
export const modelHubAnnotationQueuesUpdateBodyAnnotatorRolesDefault = {  };

export const ModelHubAnnotationQueuesUpdateBody = zod.object({
  "name": zod.string().min(1).max(modelHubAnnotationQueuesUpdateBodyNameMax),
  "description": zod.string().optional(),
  "instructions": zod.string().optional(),
  "assignment_strategy": zod.enum(['manual', 'round_robin', 'load_balanced']).optional(),
  "annotations_required": zod.number().min(modelHubAnnotationQueuesUpdateBodyAnnotationsRequiredMin).max(modelHubAnnotationQueuesUpdateBodyAnnotationsRequiredMax).optional(),
  "reservation_timeout_minutes": zod.number().min(modelHubAnnotationQueuesUpdateBodyReservationTimeoutMinutesMin).max(modelHubAnnotationQueuesUpdateBodyReservationTimeoutMinutesMax).optional(),
  "requires_review": zod.boolean().optional(),
  "auto_assign": zod.boolean().optional().describe('When enabled, all queue members can annotate any item without explicit assignment.'),
  "label_ids": zod.array(zod.string().uuid()).min(1),
  "annotator_ids": zod.array(zod.string().uuid()).default(modelHubAnnotationQueuesUpdateBodyAnnotatorIdsDefault),
  "annotator_roles": zod.record(zod.string(), zod.object({

}).passthrough()).default(modelHubAnnotationQueuesUpdateBodyAnnotatorRolesDefault)
})

export const modelHubAnnotationQueuesUpdateResponseNameMax = 255;

export const modelHubAnnotationQueuesUpdateResponseAnnotationsRequiredMin = -2147483648;
export const modelHubAnnotationQueuesUpdateResponseAnnotationsRequiredMax = 2147483647;

export const modelHubAnnotationQueuesUpdateResponseReservationTimeoutMinutesMin = -2147483648;
export const modelHubAnnotationQueuesUpdateResponseReservationTimeoutMinutesMax = 2147483647;



export const modelHubAnnotationQueuesUpdateResponseLabelsItemOrderMin = -2147483648;
export const modelHubAnnotationQueuesUpdateResponseLabelsItemOrderMax = 2147483647;



export const modelHubAnnotationQueuesUpdateResponseAnnotatorsItemRoleDefault = `annotator`;


export const modelHubAnnotationQueuesUpdateResponseAnnotatorIdsDefault = [];
export const modelHubAnnotationQueuesUpdateResponseAnnotatorRolesDefault = {  };


export const ModelHubAnnotationQueuesUpdateResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(modelHubAnnotationQueuesUpdateResponseNameMax),
  "description": zod.string().optional(),
  "instructions": zod.string().optional(),
  "status": zod.enum(['draft', 'active', 'paused', 'completed']).optional(),
  "assignment_strategy": zod.enum(['manual', 'round_robin', 'load_balanced']).optional(),
  "annotations_required": zod.number().min(modelHubAnnotationQueuesUpdateResponseAnnotationsRequiredMin).max(modelHubAnnotationQueuesUpdateResponseAnnotationsRequiredMax).optional(),
  "reservation_timeout_minutes": zod.number().min(modelHubAnnotationQueuesUpdateResponseReservationTimeoutMinutesMin).max(modelHubAnnotationQueuesUpdateResponseReservationTimeoutMinutesMax).optional(),
  "requires_review": zod.boolean().optional(),
  "auto_assign": zod.boolean().optional().describe('When enabled, all queue members can annotate any item without explicit assignment.'),
  "organization": zod.string().uuid().optional(),
  "project": zod.string().uuid().optional(),
  "dataset": zod.string().uuid().optional(),
  "agent_definition": zod.string().uuid().optional(),
  "is_default": zod.boolean().optional(),
  "labels": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "label_id": zod.string().uuid(),
  "name": zod.string().min(1).optional(),
  "type": zod.string().min(1).optional(),
  "required": zod.boolean().optional(),
  "order": zod.number().min(modelHubAnnotationQueuesUpdateResponseLabelsItemOrderMin).max(modelHubAnnotationQueuesUpdateResponseLabelsItemOrderMax).optional()
})).optional(),
  "annotators": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "user_id": zod.string().uuid(),
  "name": zod.string().min(1).optional(),
  "email": zod.string().email().min(1).optional(),
  "role": zod.string().min(1).default(modelHubAnnotationQueuesUpdateResponseAnnotatorsItemRoleDefault),
  "roles": zod.string().optional()
})).optional(),
  "label_ids": zod.array(zod.string().uuid()).min(1),
  "annotator_ids": zod.array(zod.string().uuid()).default(modelHubAnnotationQueuesUpdateResponseAnnotatorIdsDefault),
  "annotator_roles": zod.record(zod.string(), zod.object({

}).passthrough()).default(modelHubAnnotationQueuesUpdateResponseAnnotatorRolesDefault),
  "label_count": zod.number().optional(),
  "annotator_count": zod.number().optional(),
  "item_count": zod.number().optional(),
  "completed_count": zod.number().optional(),
  "created_by": zod.string().uuid().optional(),
  "created_by_name": zod.strin×oxë}›Ê×¬¢h­µç\Š
K›Z[ŠJK›X^
˜XÙ\‘]˜[\ÚÕ\]T™\ÜÛœÙTÜ[œÓ[Z]X^
K›Ü[Û˜[

Kˆœ[—Ý\HŽˆ›Ù™[[JÉØÛÛ[[Ý\ÉË	Ú\ÝÜšXØ[	×JKˆœ›Ý×Ý\HŽˆ›Ù™[[JÉÜÜ[œÉË	Ý˜XÙ\ÉË	ÜÙ\ÜÚ[ÛœÉË	Ý›ÚXÙPØ[É×JK™Y˜][
˜XÙ\‘]˜[\ÚÕ\]T™\ÜÛœÙT›ÝÕ\QY˜][
KˆœÝ]\ÈŽˆ›Ù™[[JÉÜ[™[™ÉË	Ü[›š[™ÉË	ØÛÛ\]Y	Ë	Ù˜Z[Y	Ë	Ü]\ÙY	Ë	Ù[]Y	×JK›Ü[Û˜[

KˆœÝ\Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™[™Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ\]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™]˜[×Ù]Z[ÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ™]˜[ÈŽˆ›Ù˜\œ˜^J›ÙœÝš[™Ê
K]ZY

JKˆ™˜Z[YÜÜ[œÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆœ›ÙÜ™\ÜÈŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

BŸJB‚‚™^ÜÛÛœÝ˜XÙ\‘]˜[\ÚÔ\X[\]T\˜[\ÈH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
BŸJB‚™^ÜÛÛœÝ˜XÙ\‘]˜[\ÚÔ\X[\]P›ÙS˜[YSX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\‘]˜[\ÚÔ\X[\]P›ÙQš[\œÑ]T˜[™ÙSZ[ˆHŽÂ™^ÜÛÛœÝ˜XÙ\‘]˜[\ÚÔ\X[\]P›ÙQš[\œÑ]T˜[™ÙSX^HŽÂ‚™^ÜÛÛœÝ˜XÙ\‘]˜[\ÚÔ\X[\]P›ÙQš[\œÑY˜][HÈNÂ™^ÜÛÛœÝ˜XÙ\‘]˜[\ÚÔ\X[\]P›ÙTØ[\[™Ô˜]SX^HLÂ‚™^ÜÛÛœÝ˜XÙ\‘]˜[\ÚÔ\X[\]P›ÙTÜ[œÓ[Z]X^HLÂ‚™^ÜÛÛœÝ˜XÙ\‘]˜[\ÚÔ\X[\]P›ÙT›ÝÕ\QY˜][HÜ[œØÂ‚™^ÜÛÛœÝ˜XÙ\‘]˜[\ÚÔ\X[\]P›ÙHH›Ù›Øš™XÝ
Âˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\‘]˜[\ÚÔ\X[\]P›ÙS˜[YSX^
Kˆ™š[\œÈŽˆ›Ù›Øš™XÝ
Âˆœ›Ú™XÝÚYŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	Ô›Ú™XÝØÛÜH›ÜˆH]˜[X][Ûˆ\ÚË‰ÊKˆ™]WÜ˜[™ÙHŽˆ›Ù˜\œ˜^J›ÙœÝš[™Ê
JK›Z[Š˜XÙ\‘]˜[\ÚÔ\X[\]P›ÙQš[\œÑ]T˜[™ÙSZ[ŠK›X^
˜XÙ\‘]˜[\ÚÔ\X[\]P›ÙQš[\œÑ]T˜[™ÙSX^
K›Ü[Û˜[

K™\ØÜšX™J	Ò[˜Û\Ú]™HÝ\Ù[™TÓÈ[Y\Ý[\Ë‰ÊKˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	ÓÝÙ\‹X›Ý[™TÓÈ[Y\Ý[\›ÜˆYØXÞH\ÚÈš[\œË‰ÊKˆœÙ\ÜÚ[Û—ÚYŽˆ›Ù˜\œ˜^J›ÙœÝš[™Ê
JK›Ü[Û˜[

K™\ØÜšX™J	Õ˜XÙHÙ\ÜÚ[ÛˆY
ÊHÈÛÛœÝ˜Z[ˆH\ÚË‰ÊKˆ˜XÙWÚYŽˆ›Ù˜\œ˜^J›ÙœÝš[™Ê
JK›Ü[Û˜[

K™\ØÜšX™J	Õ˜XÙHY
ÊHÈÛÛœÝ˜Z[ˆ[šÙY\ÛÝ\˜ÙH\ÚÜË‰ÊKˆœÜ[—ÚYŽˆ›Ù˜\œ˜^J›ÙœÝš[™Ê
JK›Ü[Û˜[

K™\ØÜšX™J	ÓØœÙ\˜][ÛˆÜ[ˆY
ÊHÈÛÛœÝ˜Z[ˆ[šÙY\ÛÝ\˜ÙH\ÚÜË‰ÊKˆ›ØœÙ\˜][Û—Ý\HŽˆ›Ù˜\œ˜^J›ÙœÝš[™Ê
JK›Ü[Û˜[

K™\ØÜšX™J	ÓØœÙ\˜][ÛˆÜ[ˆ\JÊK›Üˆ^[\HKÛÛÜˆÚZ[‹‰ÊKˆ™š[\œÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
Âˆ˜ÛÛ[[—ÚYŽˆ›ÙœÝš[™Ê
K™\ØÜšX™J	ÐÛÛ[[ˆÜˆ]šX]HYÈš[\ˆÛ‹‰ÊKˆ™\Ü^WÛ˜[YHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	ÓÜ[Û˜[RHX™[›ÜˆÚ\È[™Ø]™YšY]ÜË‰ÊKˆœÛÝ\˜ÙHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	ÓÜ[Û˜[ÛÝ\˜ÙHÝ\™˜XÙH›ÜˆZ^Y\ÛÝ\˜ÙHš[\œË›Üˆ^[\H˜XÙ\Ë]\Ù]ËÜˆÚ[][][Û‹‰ÊKˆ›Ý]]Ý\HŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	ÓÜ[Û˜[Y]šXÈÝ]]\HY]Y]H\ÙYžH]˜[[™[››Ý][Ûˆš[\œË‰ÊKˆ™š[\—ØÛÛ™šYÈŽˆ›Ù›Øš™XÝ
Âˆ™š[\—Ý\HŽˆ›ÙœÝš[™Ê
K™\ØÜšX™J	ÐØ[›ÛšXØ[šY[\K›Üˆ^[\H^[X™\‹›ÛÛX[‹]][YKØ]YÛÜšXØ[[XœË[››Ý]Ü‹Üˆ\œ˜^K‰ÊKˆ™š[\—ÛÜŽˆ›ÙœÝš[™Ê
K™\ØÜšX™J	ÐØ[›ÛšXØ[Ü\˜]Üˆœ›ÛH\WØÛÛ˜XÝ×Ùš[\—ØÛÛ˜XÝšœÛÛ‹›Üˆ^[\H\]X[Ë›ÝÙ\]X[Ë[‹›ÝÚ[‹™]ÙY[‹›ÝØ™]ÙY[‹\×Û[Üˆ\×Û›ÝÛ[‰ÊKˆ™š[\—Ý˜[YHŽˆ›Ù[šÛ›ÝÛŠ
K›Ü[Û˜[

K™\ØÜšX™J	ÔØØ[\‹\Ý˜[™ÙH\K›ÛÛX[‹Üˆ[\[™[™ÈÛˆš[\—ÛÜ[™š[\—Ý\K‰ÊKˆ˜ÛÛÝ\HŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	ÐÛÛ[[ˆ˜[Z[HÝXÚ\ÈÖTÕSWÓQU’PËÔS—ÐU’P•UKUSÓQU’PËS““ÕUSÓ‹Üˆ“Ô“PS‰ÊBŸJBŸJJK›Ü[Û˜[

KˆœÜ[—Ø]šX]\×Ùš[\œÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
Âˆ˜ÛÛ[[—ÚYŽˆ›ÙœÝš[™Ê
K™\ØÜšX™J	ÐÛÛ[[ˆÜˆ]šX]HYÈš[\ˆÛ‹‰ÊKˆ™\Ü^WÛ˜[YHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	ÓÜ[Û˜[RHX™[›ÜˆÚ\È[™Ø]™YšY]ÜË‰ÊKˆœÛÝ\˜ÙHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	ÓÜ[Û˜[ÛÝ\˜ÙHÝ\™˜XÙH›ÜˆZ^Y\ÛÝ\˜ÙHš[\œË›Üˆ^[\H˜XÙ\Ë]\Ù]ËÜˆÚ[][][Û‹‰ÊKˆ›Ý]]Ý\HŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	ÓÜ[Û˜[Y]šXÈÝ]]\HY]Y]H\ÙYžH]˜[[™[››Ý][Ûˆš[\œË‰ÊKˆ™š[\—ØÛÛ™šYÈŽˆ›Ù›Øš™XÝ
Âˆ™š[\—Ý\HŽˆ›ÙœÝš[™Ê
K™\ØÜšX™J	ÐØ[›ÛšXØ[šY[\K›Üˆ^[\H^[X™\‹›ÛÛX[‹]][YKØ]YÛÜšXØ[[XœË[››Ý]Ü‹Üˆ\œ˜^K‰ÊKˆ™š[\—ÛÜŽˆ›ÙœÝš[™Ê
K™\ØÜšX™J	ÐØ[›ÛšXØ[Ü\˜]Üˆœ›ÛH\WØÛÛ˜XÝ×Ùš[\—ØÛÛ˜XÝšœÛÛ‹›Üˆ^[\H\]X[Ë›ÝÙ\]X[Ë[‹›ÝÚ[‹™]ÙY[‹›ÝØ™]ÙY[‹\×Û[Üˆ\×Û›ÝÛ[‰ÊKˆ™š[\—Ý˜[YHŽˆ›Ù[šÛ›ÝÛŠ
K›Ü[Û˜[

K™\ØÜšX™J	ÔØØ[\‹\Ý˜[™ÙH\K›ÛÛX[‹Üˆ[\[™[™ÈÛˆš[\—ÛÜ[™š[\—Ý\K‰ÊKˆ˜ÛÛÝ\HŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	ÐÛÛ[[ˆ˜[Z[HÝXÚ\ÈÖTÕSWÓQU’PËÔS—ÐU’P•UKUSÓQU’PËS““ÕUSÓ‹Üˆ“Ô“PS‰ÊBŸJBŸJJK›Ü[Û˜[

BŸJK™Y˜][
˜XÙ\‘]˜[\ÚÔ\X[\]P›ÙQš[\œÑY˜][
KˆœØ[\[™×Ü˜]HŽˆ›Ù›[X™\Š
K›Z[ŠJK›X^
˜XÙ\‘]˜[\ÚÔ\X[\]P›ÙTØ[\[™Ô˜]SX^
Kˆ›\ÝÜ[ˆŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

KˆœÜ[œ×Û[Z]Žˆ›Ù›[X™\Š
K›Z[ŠJK›X^
˜XÙ\‘]˜[\ÚÔ\X[\]P›ÙTÜ[œÓ[Z]X^
K›Ü[Û˜[

Kˆœ[—Ý\HŽˆ›Ù™[[JÉØÛÛ[[Ý\ÉË	Ú\ÝÜšXØ[	×JKˆœ›Ý×Ý\HŽˆ›Ù™[[JÉÜÜ[œÉË	Ý˜XÙ\ÉË	ÜÙ\ÜÚ[ÛœÉË	Ý›ÚXÙPØ[É×JK™Y˜][
˜XÙ\‘]˜[\ÚÔ\X[\]P›ÙT›ÝÕ\QY˜][
KˆœÝ]\ÈŽˆ›Ù™[[JÉÜ[™[™ÉË	Ü[›š[™ÉË	ØÛÛ\]Y	Ë	Ù˜Z[Y	Ë	Ü]\ÙY	Ë	Ù[]Y	×JK›Ü[Û˜[

KˆœÝ\Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™[™Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™]˜[×Ù]Z[ÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ™]˜[ÈŽˆ›Ù˜\œ˜^J›ÙœÝš[™Ê
K]ZY

JKˆ™˜Z[YÜÜ[œÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

BŸJB‚™^ÜÛÛœÝ˜XÙ\‘]˜[\ÚÔ\X[\]T™\ÜÛœÙS˜[YSX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\‘]˜[\ÚÔ\X[\]T™\ÜÛœÙQš[\œÑ]T˜[™ÙSZ[ˆHŽÂ™^ÜÛÛœÝ˜XÙ\‘]˜[\ÚÔ\X[\]T™\ÜÛœÙQš[\œÑ]T˜[™ÙSX^HŽÂ‚™^ÜÛÛœÝ˜XÙ\‘]˜[\ÚÔ\X[\]T™\ÜÛœÙQš[\œÑY˜][HÈNÂ™^ÜÛÛœÝ˜XÙ\‘]˜[\ÚÔ\X[\]T™\ÜÛœÙTØ[\[™Ô˜]SX^HLÂ‚™^ÜÛÛœÝ˜XÙ\‘]˜[\ÚÔ\X[\]T™\ÜÛœÙTÜ[œÓ[Z]X^HLÂ‚™^ÜÛÛœÝ˜XÙ\‘]˜[\ÚÔ\X[\]T™\ÜÛœÙT›ÝÕ\QY˜][HÜ[œØÂ‚™^ÜÛÛœÝ˜XÙ\‘]˜[\ÚÔ\X[\]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\‘]˜[\ÚÔ\X[\]T™\ÜÛœÙS˜[YSX^
Kˆ™š[\œÈŽˆ›Ù›Øš™XÝ
Âˆœ›Ú™XÝÚYŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	Ô›Ú™XÝØÛÜH›ÜˆH]˜[X][Ûˆ\ÚË‰ÊKˆ™]WÜ˜[™ÙHŽˆ›Ù˜\œ˜^J›ÙœÝš[™Ê
JK›Z[Š˜XÙ\‘]˜[\ÚÔ\X[\]T™\ÜÛœÙQš[\œÑ]T˜[™ÙSZ[ŠK›X^
˜XÙ\‘]˜[\ÚÔ\X[\]T™\ÜÛœÙQš[\œÑ]T˜[™ÙSX^
K›Ü[Û˜[

K™\ØÜšX™J	Ò[˜Û\Ú]™HÝ\Ù[™TÓÈ[Y\Ý[\Ë‰ÊKˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	ÓÝÙ\‹X›Ý[™TÓÈ[Y\Ý[\›ÜˆYØXÞH\ÚÈš[\œË‰ÊKˆœÙ\ÜÚ[Û—ÚYŽˆ›Ù˜\œ˜^J›ÙœÝš[™Ê
JK›Ü[Û˜[

K™\ØÜšX™J	Õ˜XÙHÙ\ÜÚ[ÛˆY
ÊHÈÛÛœÝ˜Z[ˆH\ÚË‰ÊKˆ˜XÙWÚYŽˆ›Ù˜\œ˜^J›ÙœÝš[™Ê
JK›Ü[Û˜[

K™\ØÜšX™J	Õ˜XÙHY
ÊHÈÛÛœÝ˜Z[ˆ[šÙY\ÛÝ\˜ÙH\ÚÜË‰ÊKˆœÜ[—ÚYŽˆ›Ù˜\œ˜^J›ÙœÝš[™Ê
JK›Ü[Û˜[

K™\ØÜšX™J	ÓØœÙ\˜][ÛˆÜ[ˆY
ÊHÈÛÛœÝ˜Z[ˆ[šÙY\ÛÝ\˜ÙH\ÚÜË‰ÊKˆ›ØœÙ\˜][Û—Ý\HŽˆ›Ù˜\œ˜^J›ÙœÝš[™Ê
JK›Ü[Û˜[

K™\ØÜšX™J	ÓØœÙ\˜][ÛˆÜ[ˆ\JÊK›Üˆ^[\HKÛÛÜˆÚZ[‹‰ÊKˆ™š[\œÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
Âˆ˜ÛÛ[[—ÚYŽˆ›ÙœÝš[™Ê
K™\ØÜšX™J	ÐÛÛ[[ˆÜˆ]šX]HYÈš[\ˆÛ‹‰ÊKˆ™\Ü^WÛ˜[YHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	ÓÜ[Û˜[RHX™[›ÜˆÚ\È[™Ø]™YšY]ÜË‰ÊKˆœÛÝ\˜ÙHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	ÓÜ[Û˜[ÛÝ\˜ÙHÝ\™˜XÙH›ÜˆZ^Y\ÛÝ\˜ÙHš[\œË›Üˆ^[\H˜XÙ\Ë]\Ù]ËÜˆÚ[][][Û‹‰ÊKˆ›Ý]]Ý\HŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	ÓÜ[Û˜[Y]šXÈÝ]]\HY]Y]H\ÙYžH]˜[[™[››Ý][Ûˆš[\œË‰ÊKˆ™š[\—ØÛÛ™šYÈŽˆ›Ù›Øš™XÝ
Âˆ™š[\—Ý\HŽˆ›ÙœÝš[™Ê
K™\ØÜšX™J	ÐØ[›ÛšXØ[šY[\K›Üˆ^[\H^[X™\‹›ÛÛX[‹]][YKØ]YÛÜšXØ[[XœË[››Ý]Ü‹Üˆ\œ˜^K‰ÊKˆ™š[\—ÛÜŽˆ›ÙœÝš[™Ê
K™\ØÜšX™J	ÐØ[›ÛšXØ[Ü\˜]Üˆœ›ÛH\WØÛÛ˜XÝ×Ùš[\—ØÛÛ˜XÝšœÛÛ‹›Üˆ^[\H\]X[Ë›ÝÙ\]X[Ë[‹›ÝÚ[‹™]ÙY[‹›ÝØ™]ÙY[‹\×Û[Üˆ\×Û›ÝÛ[‰ÊKˆ™š[\—Ý˜[YHŽˆ›Ù[šÛ›ÝÛŠ
K›Ü[Û˜[

K™\ØÜšX™J	ÔØØ[\‹\Ý˜[™ÙH\K›ÛÛX[‹Üˆ[\[™[™ÈÛˆš[\—ÛÜ[™š[\—Ý\K‰ÊKˆ˜ÛÛÝ\HŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	ÐÛÛ[[ˆ˜[Z[HÝXÚ\ÈÖTÕSWÓQU’PËÔS—ÐU’P•UKUSÓQU’PËS““ÕUSÓ‹Üˆ“Ô“PS‰ÊBŸJBŸJJK›Ü[Û˜[

KˆœÜ[—Ø]šX]\×Ùš[\œÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
Âˆ˜ÛÛ[[—ÚYŽˆ›ÙœÝš[™Ê
K™\ØÜšX™J	ÐÛÛ[[ˆÜˆ]šX]HYÈš[\ˆÛ‹‰ÊKˆ™\Ü^WÛ˜[YHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	ÓÜ[Û˜[RHX™[›ÜˆÚ\È[™Ø]™YšY]ÜË‰ÊKˆœÛÝ\˜ÙHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	ÓÜ[Û˜[ÛÝ\˜ÙHÝ\™˜XÙH›ÜˆZ^Y\ÛÝ\˜ÙHš[\œË›Üˆ^[\H˜XÙ\Ë]\Ù]ËÜˆÚ[][][Û‹‰ÊKˆ›Ý]]Ý\HŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	ÓÜ[Û˜[Y]šXÈÝ]]\HY]Y]H\ÙYžH]˜[[™[››Ý][Ûˆš[\œË‰ÊKˆ™š[\—ØÛÛ™šYÈŽˆ›Ù›Øš™XÝ
Âˆ™š[\—Ý\HŽˆ›ÙœÝš[™Ê
K™\ØÜšX™J	ÐØ[›ÛšXØ[šY[\K›Üˆ^[\H^[X™\‹›ÛÛX[‹]][YKØ]YÛÜšXØ[[XœË[››Ý]Ü‹Üˆ\œ˜^K‰ÊKˆ™š[\—ÛÜŽˆ›ÙœÝš[™Ê
K™\ØÜšX™J	ÐØ[›ÛšXØ[Ü\˜]Üˆœ›ÛH\WØÛÛ˜XÝ×Ùš[\—ØÛÛ˜XÝšœÛÛ‹›Üˆ^[\H\]X[Ë›ÝÙ\]X[Ë[‹›ÝÚ[‹™]ÙY[‹›ÝØ™]ÙY[‹\×Û[Üˆ\×Û›ÝÛ[‰ÊKˆ™š[\—Ý˜[YHŽˆ›Ù[šÛ›ÝÛŠ
K›Ü[Û˜[

K™\ØÜšX™J	ÔØØ[\‹\Ý˜[™ÙH\K›ÛÛX[‹Üˆ[\[™[™ÈÛˆš[\—ÛÜ[™š[\—Ý\K‰ÊKˆ˜ÛÛÝ\HŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	ÐÛÛ[[ˆ˜[Z[HÝXÚ\ÈÖTÕSWÓQU’PËÔS—ÐU’P•UKUSÓQU’PËS““ÕUSÓ‹Üˆ“Ô“PS‰ÊBŸJBŸJJK›Ü[Û˜[

BŸJK™Y˜][
˜XÙ\‘]˜[\ÚÔ\X[\]T™\ÜÛœÙQš[\œÑY˜][
KˆœØ[\[™×Ü˜]HŽˆ›Ù›[X™\Š
K›Z[ŠJK›X^
˜XÙ\‘]˜[\ÚÔ\X[\]T™\ÜÛœÙTØ[\[™Ô˜]SX^
Kˆ›\ÝÜ[ˆŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

KˆœÜ[œ×Û[Z]Žˆ›Ù›[X™\Š
K›Z[ŠJK›X^
˜XÙ\‘]˜[\ÚÔ\X[\]T™\ÜÛœÙTÜ[œÓ[Z]X^
K›Ü[Û˜[

Kˆœ[—Ý\HŽˆ›Ù™[[JÉØÛÛ[[Ý\ÉË	Ú\ÝÜšXØ[	×JKˆœ›Ý×Ý\HŽˆ›Ù™[[JÉÜÜ[œÉË	Ý˜XÙ\ÉË	ÜÙ\ÜÚ[ÛœÉË	Ý›ÚXÙPØ[É×JK™Y˜][
˜XÙ\‘]˜[\ÚÔ\X[\]T™\ÜÛœÙT›ÝÕ\QY˜][
KˆœÝ]\ÈŽˆ›Ù™[[JÉÜ[™[™ÉË	Ü[›š[™ÉË	ØÛÛ\]Y	Ë	Ù˜Z[Y	Ë	Ü]\ÙY	Ë	Ù[]Y	×JK›Ü[Û˜[

KˆœÝ\Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™[™Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ\]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™]˜[×Ù]Z[ÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ™]˜[ÈŽˆ›Ù˜\œ˜^J›ÙœÝš[™Ê
K]ZY

JKˆ™˜Z[YÜÜ[œÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆœ›ÙÜ™\ÜÈŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

BŸJB‚‚™^ÜÛÛœÝ˜XÙ\‘]˜[\ÚÑ[]T\˜[\ÈH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
BŸJB‚‚‹ÊŠ‚ˆ
ˆ™]\›œÈH\ÝÙˆ[™X\ˆX[\È›ÜˆHX[HXÚÙ\ˆ›ÜÝÛ‹‚”™\]Z\™\È[ˆXÝ]™H[™X\ˆ[YÜ˜][Ûˆ›ÜˆH\Ù\‰ÜÈÜ™Ë‚ˆ
ˆÝ[[X\žHÑUÝ˜XÙ\‹Ù™YYÚ[YÜ˜][ÛœËÛ[™X\‹ÝX[\ËÂˆ
‹Â™^ÜÛÛœÝ˜XÙ\‘™YY[YÜ˜][ÛœÓ[™X\•X[\Ó\Ý™\ÜÛœÙTÝ]\ÑY˜][HYNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\‘™YY[YÜ˜][ÛœÓ[™X\•X[\Ó\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
K™Y˜][
˜XÙ\‘™YY[YÜ˜][ÛœÓ[™X\•X[\Ó\Ý™\ÜÛœÙTÝ]\ÑY˜][
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆ˜ÛÛ›™XÝYŽˆ›Ù˜›ÛÛX[Š
KˆX[\ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆšÙ^HŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

BŸJJBŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆÑUÝ˜XÙ\‹Ù™YYÚ\ÜÝY\ËÈ8 %YÚ[˜]YÛ\Ý\ˆ\ÝÚ]š[\œËÜÛÜ‚ˆ
‹Â‚™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\Ó\Ý]Y\žTÛÜžQY˜][H\ÝÜÙY[˜Â™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\Ó\Ý]Y\žTÛÜ\‘Y˜][H\ØØÂ™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\Ó\Ý]Y\žS[Z]Y˜][HNÂ™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\Ó\Ý]Y\žS[Z]X^HŒÂ‚™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\Ó\Ý]Y\žSÙ™œÙ]Y˜][HÂ™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\Ó\Ý]Y\žSÙ™œÙ]Z[ˆHÂ‚‚‚™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\Ó\Ý]Y\žT\˜[\ÈH›Ù›Øš™XÝ
Âˆœ›Ú™XÝÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

KˆœÙX\˜ÚŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆœÝ]\ÈŽˆ›Ù™[[JÉÙ\ØØ[][™ÉË	Ù›Ü—Ü™]šY]ÉË	ØXÚÛ›ÝÛYÙY	Ë	Ü™\ÛÛ™Y	×JK›Ü[Û˜[

KˆœÙ]™\š]HŽˆ›Ù™[[JÉØÜš]XØ[	Ë	ÚYÚ	Ë	ÛYY][IË	ÛÝÉ×JK›Ü[Û˜[

Kˆ™š^Û^Y\ˆŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆœÛÝ\˜ÙHŽˆ›Ù™[[JÉÜØØ[›™\‰Ë	Ù]˜[	×JK›Ü[Û˜[

Kˆš\ÜÝYWÙÜ›Ý\Žˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ[YWÜ˜[™ÙWÙ^\ÈŽˆ›Ù›[X™\Š
K›Z[ŠJK›Ü[Û˜[

KˆœÛÜØžHŽˆ›Ù™[[JÉÛ\ÝÜÙY[‰Ë	Ùš\œÝÜÙY[‰Ë	Ù\œ›Ü—ØÛÝ[	Ë	Ý[š\]YWÝ˜XÙ\ÉË	ÜÙ]™\š]I×JK™Y˜][
˜XÙ\‘™YY\ÜÝY\Ó\Ý]Y\žTÛÜžQY˜][
KˆœÛÜÙ\ˆŽˆ›Ù™[[JÉØ\ØÉË	Ù\ØÉ×JK™Y˜][
˜XÙ\‘™YY\ÜÝY\Ó\Ý]Y\žTÛÜ\‘Y˜][
Kˆ›[Z]Žˆ›Ù›[X™\Š
K›Z[ŠJK›X^
˜XÙ\‘™YY\ÜÝY\Ó\Ý]Y\žS[Z]X^
K™Y˜][
˜XÙ\‘™YY\ÜÝY\Ó\Ý]Y\žS[Z]Y˜][
Kˆ›Ù™œÙ]Žˆ›Ù›[X™\Š
K›Z[Š˜XÙ\‘™YY\ÜÝY\Ó\Ý]Y\žSÙ™œÙ]Z[ŠK™Y˜][
˜XÙ\‘™YY\ÜÝY\Ó\Ý]Y\žSÙ™œÙ]Y˜][
BŸJB‚™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\Ó\Ý™\ÜÛœÙTÝ]\ÑY˜][HYNÂ‚‚‚‚‚‚‚‚‚‚‚‚‚‚‚‚‚™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\Ó\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
K™Y˜][
˜XÙ\‘™YY\ÜÝY\Ó\Ý™\ÜÛœÙTÝ]\ÑY˜][
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆ™]HŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
Âˆ˜Û\Ý\—ÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœÛÝ\˜ÙHŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ›[Ù[]HŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ™\œ›ÜˆŽˆ›Ù›Øš™XÝ
Âˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ\HŽˆ›ÙœÝš[™Ê
BŸJKˆœÝ]\ÈŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœÙ]™\š]HŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ›ØØÝ\œ™[˜Ù\ÈŽˆ›Ù›[X™\Š
Kˆ˜XÙWØÛÝ[Žˆ›Ù›[X™\Š
Kˆ™š^Û^Y\ˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ\Ù\œ×ØY™™XÝYŽˆ›Ù›[X™\Š
KˆœÙ\ÜÚ[ÛœÈŽˆ›Ù›[X™\Š
Kˆ™š\œÝÜÙY[ˆŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JKˆ›\ÝÜÙY[ˆŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JKˆ™[™ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
Âˆ[Y\Ý[\Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JKˆ˜[YHŽˆ›Ù›[X™\Š
Kˆ\Ù\œÈŽˆ›Ù›[X™\Š
BŸJJKˆ˜\ÜÚYÛ™Y\ÈŽˆ›Ù˜\œ˜^J›ÙœÝš[™Ê
K›Z[ŠJJKˆ›[Ù[Žˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ›[Ù[Ý™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœ›Ú™XÝÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ™[š\›Û›Y[Žˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ™]˜[ÜØÛÜ™HŽˆ›Ù›[X™\Š
Kˆ˜XÙWÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ™^\›˜[Ú\ÜÝYWÝ\›Žˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ™^\›˜[Ú\ÜÝYWÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJJKˆÝ[Žˆ›Ù›[X™\Š
Kˆ›[Z]Žˆ›Ù›[X™\Š
Kˆ›Ù™œÙ]Žˆ›Ù›[X™\Š
BŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆÑUÝ˜XÙ\‹Ù™YYÚ\ÜÝY\ËÜÝ]ËÈ8 %ÜÝ]È˜\ˆÝ[Ë‚ˆ
‹Â‚‚‚™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\ÔÝ]Ó\Ý]Y\žT\˜[\ÈH›Ù›Øš™XÝ
Âˆœ›Ú™XÝÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ[YWÜ˜[™ÙWÙ^\ÈŽˆ›Ù›[X™\Š
K›Z[ŠJK›Ü[Û˜[

BŸJB‚™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\ÔÝ]Ó\Ý™\ÜÛœÙTÝ]\ÑY˜][HYNÂ‚™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\ÔÝ]Ó\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
K™Y˜][
˜XÙ\‘™YY\ÜÝY\ÔÝ]Ó\Ý™\ÜÛœÙTÝ]\ÑY˜][
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
ÂˆÝ[Ù\œ›ÜœÈŽˆ›Ù›[X™\Š
Kˆ™\ØØ[][™ÈŽˆ›Ù›[X™\Š
Kˆ™›Ü—Ü™]šY]ÈŽˆ›Ù›[X™\Š
Kˆ˜XÚÛ›ÝÛYÙYŽˆ›Ù›[X™\Š
Kˆœ™\ÛÛ™YŽˆ›Ù›[X™\Š
Kˆ˜Y™™XÝYÝ\Ù\œÈŽˆ›Ù›[X™\Š
BŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆÑU
ÈUÒÝ˜XÙ\‹Ù™YYÚ\ÜÝY\ËÞØÛ\Ý\—ÚYKÂˆ
‹Â™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\Ô™XY\˜[\ÈH›Ù›Øš™XÝ
Âˆ˜Û\Ý\—ÚYŽˆ›ÙœÝš[™Ê
BŸJB‚™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\Ô™XY]Y\žT\˜[\ÈH›Ù›Øš™XÝ
Âˆœ›Ú™XÝÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJB‚™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\Ô™XY™\ÜÛœÙTÝ]\ÑY˜][HYNÂ‚‚‚‚‚‚‚‚‚‚‚‚‚‚‚‚‚‚‚‚‚‚‚‚‚‚‚‚‚‚‚‚™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\Ô™XY™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
K™Y˜][
˜XÙ\‘™YY\ÜÝY\Ô™XY™\ÜÛœÙTÝ]\ÑY˜][
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆœ›ÝÈŽˆ›Ù›Øš™XÝ
Âˆ˜Û\Ý\—ÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœÛÝ\˜ÙHŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ›[Ù[]HŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ™\œ›ÜˆŽˆ›Ù›Øš™XÝ
Âˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ\HŽˆ›ÙœÝš[™Ê
BŸJKˆœÝ]\ÈŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœÙ]™\š]HŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ›ØØÝ\œ™[˜Ù\ÈŽˆ›Ù›[X™\Š
Kˆ˜XÙWØÛÝ[Žˆ›Ù›[X™\Š
Kˆ™š^Û^Y\ˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ\Ù\œ×ØY™™XÝYŽˆ›Ù›[X™\Š
KˆœÙ\ÜÚ[ÛœÈŽˆ›Ù›[X™\Š
Kˆ™š\œÝÜÙY[ˆŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JKˆ›\ÝÜÙY[ˆŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JKˆ™[™ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
Âˆ[Y\Ý[\Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JKˆ˜[YHŽˆ›Ù›[X™\Š
Kˆ\Ù\œÈŽˆ›Ù›[X™\Š
BŸJJKˆ˜\ÜÚYÛ™Y\ÈŽˆ›Ù˜\œ˜^J›ÙœÝš[™Ê
K›Z[ŠJJKˆ›[Ù[Žˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ›[Ù[Ý™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœ›Ú™XÝÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ™[š\›Û›Y[Žˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ™]˜[ÜØÛÜ™HŽˆ›Ù›[X™\Š
Kˆ˜XÙWÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ™^\›˜[Ú\ÜÝYWÝ\›Žˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ™^\›˜[Ú\ÜÝYWÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJKˆ™\ØÜš\[ÛˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœÝXØÙ\Ü×Ý˜XÙHŽˆ›Ù›Øš™XÝ
Âˆ˜XÙWÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆš[œ]Žˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ›Ý]]Žˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJKˆœ™\™\Ù[]]™WÝ˜XÙHŽˆ›Ù›Øš™XÝ
Âˆ˜XÙWÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆš[œ]Žˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ›Ý]]Žˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJKˆœ˜ØHŽˆ›Ù›Øš™XÝ
ÂˆœÞ[\Ú\ÈŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ™š^Žˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ˜ÛÛ™šY[˜ÙHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ™]šY[˜ÙWÝ˜XÙWÚYÈŽˆ›Ù˜\œ˜^J›ÙœÝš[™Ê
K›Z[ŠJJK›Ü[Û˜[

Kˆ˜[˜[^™YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™˜Z[\™\×Ø]Ü[ˆŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ˜XÙHŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
Âˆ\HŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ^Žˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ˜Ø[ÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

KˆÛÛŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ˜\™ÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÞ[\Ú\ÈŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ™š^Žˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ˜ÛÛ™šY[˜ÙHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

BŸJJK›Ü[Û˜[

BŸJK›Ü[Û˜[

BŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆÑU
ÈUÒÝ˜XÙ\‹Ù™YYÚ\ÜÝY\ËÞØÛ\Ý\—ÚYKÂˆ
‹Â™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\Ô\X[\]T\˜[\ÈH›Ù›Øš™XÝ
Âˆ˜Û\Ý\—ÚYŽˆ›ÙœÝš[™Ê
BŸJB‚‚‚‚™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\Ô\X[\]P›ÙHH›Ù›Øš™XÝ
Âˆœ›Ú™XÝÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

KˆœÝ]\ÈŽˆ›Ù™[[JÉÙ\ØØ[][™ÉË	Ù›Ü—Ü™]šY]ÉË	ØXÚÛ›ÝÛYÙY	Ë	Ü™\ÛÛ™Y	×JK›Ü[Û˜[

KˆœÙ]™\š]HŽˆ›Ù™[[JÉØÜš]XØ[	Ë	ÚYÚ	Ë	ÛYY][IË	ÛÝÉ×JK›Ü[Û˜[

Kˆ˜\ÜÚYÛ™YHŽˆ›ÙœÝš[™Ê
K™[XZ[

K›Z[ŠJK›Ü[Û˜[

BŸJB‚™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\Ô\X[\]T™\ÜÛœÙTÝ]\ÑY˜][HYNÂ‚‚‚‚‚‚‚‚‚‚‚‚‚‚‚‚‚‚‚‚‚‚‚‚‚‚‚‚‚‚‚‚™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\Ô\X[\]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
K™Y˜][
˜XÙ\‘™YY\ÜÝY\Ô\X[\]T™\ÜÛœÙTÝ]\ÑY˜][
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆœ›ÝÈŽˆ›Ù›Øš™XÝ
Âˆ˜Û\Ý\—ÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœÛÝ\˜ÙHŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ›[Ù[]HŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ™\œ›ÜˆŽˆ›Ù›Øš™XÝ
Âˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ\HŽˆ›ÙœÝš[™Ê
BŸJKˆœÝ]\ÈŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœÙ]™\š]HŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ›ØØÝ\œ™[˜Ù\ÈŽˆ›Ù›[X™\Š
Kˆ˜XÙWØÛÝ[Žˆ›Ù›[X™\Š
Kˆ™š^Û^Y\ˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ\Ù\œ×ØY™™XÝYŽˆ›Ù›[X™\Š
KˆœÙ\ÜÚ[ÛœÈŽˆ›Ù›[X™\Š
Kˆ™š\œÝÜÙY[ˆŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JKˆ›\ÝÜÙY[ˆŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JKˆ™[™ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
Âˆ[Y\Ý[\Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JKˆ˜[YHŽˆ›Ù›[X™\Š
Kˆ\Ù\œÈŽˆ›Ù›[X™\Š
BŸJJKˆ˜\ÜÚYÛ™Y\ÈŽˆ›Ù˜\œ˜^J›ÙœÝš[™Ê
K›Z[ŠJJKˆ›[Ù[Žˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ›[Ù[Ý™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœ›Ú™XÝÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ™[š\›Û›Y[Žˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ™]˜[ÜØÛÜ™HŽˆ›Ù›[X™\Š
Kˆ˜XÙWÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ™^\›˜[Ú\ÜÝYWÝ\›Žˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ™^\›˜[Ú\ÜÝYWÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJKˆ™\ØÜš\[ÛˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœÝXØÙ\Ü×Ý˜XÙHŽˆ›Ù›Øš™XÝ
Âˆ˜XÙWÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆš[œ]Žˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ›Ý]]Žˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJKˆœ™\™\Ù[]]™WÝ˜XÙHŽˆ›Ù›Øš™XÝ
Âˆ˜XÙWÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆš[œ]Žˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ›Ý]]Žˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJKˆœ˜ØHŽˆ›Ù›Øš™XÝ
ÂˆœÞ[\Ú\ÈŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ™š^Žˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ˜ÛÛ™šY[˜ÙHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ™]šY[˜ÙWÝ˜XÙWÚYÈŽˆ›Ù˜\œ˜^J›ÙœÝš[™Ê
K›Z[ŠJJK›Ü[Û˜[

Kˆ˜[˜[^™YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™˜Z[\™\×Ø]Ü[ˆŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ˜XÙHŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
Âˆ\HŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ^Žˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ˜Ø[ÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

KˆÛÛŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ˜\™ÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÞ[\Ú\ÈŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ™š^Žˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ˜ÛÛ™šY[˜ÙHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

BŸJJK›Ü[Û˜[

BŸJK›Ü[Û˜[

BŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆÔÕÝ˜XÙ\‹Ù™YYÚ\ÜÝY\ËÞØÛ\Ý\—ÚYKØÜ™X]K[[™X\‹Z\ÜÝYKÂˆ
‹Â™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\ÐÜ™X]S[™X\’\ÜÝYPÜ™X]T\˜[\ÈH›Ù›Øš™XÝ
Âˆ˜Û\Ý\—ÚYŽˆ›ÙœÝš[™Ê
BŸJB‚‚™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\ÐÜ™X]S[™X\’\ÜÝYPÜ™X]P›ÙTš[Üš]QY˜][HÂ‚™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\ÐÜ™X]S[™X\’\ÜÝYPÜ™X]P›ÙHH›Ù›Øš™XÝ
ÂˆX[WÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ˜XÙWÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ]HŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ™\ØÜš\[ÛˆŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆœš[Üš]HŽˆ›Ù›[X™\Š
K™Y˜][
˜XÙ\‘™YY\ÜÝY\ÐÜ™X]S[™X\’\ÜÝYPÜ™X]P›ÙTš[Üš]QY˜][
BŸJB‚™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\ÐÜ™X]S[™X\’\ÜÝYPÜ™X]T™\ÜÛœÙTÝ]\ÑY˜][HYNÂ‚‚‚‚™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\ÐÜ™X]S[™X\’\ÜÝYPÜ™X]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
K™Y˜][
˜XÙ\‘™YY\ÜÝY\ÐÜ™X]S[™X\’\ÜÝYPÜ™X]T™\ÜÛœÙTÝ]\ÑY˜][
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆ˜[™XYWÛ[šÙYŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆš\ÜÝYWÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆš\ÜÝYWÝ\›Žˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆš\ÜÝYWÝ]HŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

BŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆÔÕÝ˜XÙ\‹Ù™YYÚ\ÜÝY\ËÞØÛ\Ý\—ÚYKÙY\X[˜[\Ú\ËÂˆ
‹Â™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\ÑY\[˜[\Ú\ÐÜ™X]T\˜[\ÈH›Ù›Øš™XÝ
Âˆ˜Û\Ý\—ÚYŽˆ›ÙœÝš[™Ê
BŸJB‚‚™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\ÑY\[˜[\Ú\ÐÜ™X]P›ÙQ›Ü˜ÙQY˜][H˜[ÙNÂ‚™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\ÑY\[˜[\Ú\ÐÜ™X]P›ÙHH›Ù›Øš™XÝ
Âˆ˜XÙWÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ™›Ü˜ÙHŽˆ›Ù˜›ÛÛX[Š
K™Y˜][
˜XÙ\‘™YY\ÜÝY\ÑY\[˜[\Ú\ÐÜ™X]P›ÙQ›Ü˜ÙQY˜][
BŸJB‚™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\ÑY\[˜[\Ú\ÐÜ™X]T™\ÜÛœÙTÝ]\ÑY˜][HYNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\ÑY\[˜[\Ú\ÐÜ™X]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
K™Y˜][
˜XÙ\‘™YY\ÜÝY\ÑY\[˜[\Ú\ÐÜ™X]T™\ÜÛœÙTÝ]\ÑY˜][
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ˜XÙWÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆÑUÝ˜XÙ\‹Ù™YYÚ\ÜÝY\ËÞØÛ\Ý\—ÚYKÛÝ™\šY]ËÂˆ
‹Â™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\ÓÝ™\šY]Ó\Ý\˜[\ÈH›Ù›Øš™XÝ
Âˆ˜Û\Ý\—ÚYŽˆ›ÙœÝš[™Ê
BŸJB‚™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\ÓÝ™\šY]Ó\Ý]Y\žT™\[Z]Y˜][HŒÂ™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\ÓÝ™\šY]Ó\Ý]Y\žT™\[Z]X^HŒÂ‚‚‚™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\ÓÝ™\šY]Ó\Ý]Y\žT\˜[\ÈH›Ù›Øš™XÝ
Âˆœ™\Û[Z]Žˆ›Ù›[X™\Š
K›Z[ŠJK›X^
˜XÙ\‘™YY\ÜÝY\ÓÝ™\šY]Ó\Ý]Y\žT™\[Z]X^
K™Y˜][
˜XÙ\‘™YY\ÜÝY\ÓÝ™\šY]Ó\Ý]Y\žT™\[Z]Y˜][
BŸJB‚™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\ÓÝ™\šY]Ó\Ý™\ÜÛœÙTÝ]\ÑY˜][HYNÂ™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\ÓÝ™\šY]Ó\Ý™\ÜÛœÙT™\Ý[]\›”Ý[[X\žR[œÚYÚÒ][U]QY˜][HÂ‚‚‚‚‚‚‚‚‚‚‚‚™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\ÓÝ™\šY]Ó\Ý™\ÜÛœÙT™\Ý[™\™\Ù[]]™UÝ[Y˜][HÂ‚™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\ÓÝ™\šY]Ó\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
K™Y˜][
˜XÙ\‘™YY\ÜÝY\ÓÝ™\šY]Ó\Ý™\ÜÛœÙTÝ]\ÑY˜][
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆ™]™[×ÛÝ™\—Ý[YHŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
Âˆ™]HŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ™\œ›ÜœÈŽˆ›Ù›[X™\Š
Kˆœ\ÜÚ[™ÈŽˆ›Ù›[X™\Š
Kˆ\Ù\œÈŽˆ›Ù›[X™\Š
BŸJJKˆœ]\›—ÜÝ[[X\žHŽˆ›Ù›Øš™XÝ
Âˆš[œÚYÚÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
Âˆ]HŽˆ›ÙœÝš[™Ê
K›Z[ŠJK™Y˜][
˜XÙ\‘™YY\ÜÝY\ÓÝ™\šY]Ó\Ý™\ÜÛœÙT™\Ý[]\›”Ý[[X\žR[œÚYÚÒ][U]QY˜][
Kˆ˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ˜Ø\[ÛˆŽˆ›ÙœÝš[™Ê
Kˆ™]šY[˜ÙHŽˆ›Ù›Øš™XÝ
Âˆ\ÝŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ˜˜\Ù[[™HŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

KˆÛÛŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

KˆžˆŽˆ›Ù›[X™\Š
K›Ü[Û˜[

KˆœÝ˜[YHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

KˆšÜ×ÜÝ]Žˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ™˜Z[ÛYYX[ˆŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ˜˜\Ù[[™WÛYYX[ˆŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ™˜Z[ÜÝŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ˜˜\Ù[[™WÜÝŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆš]ÈŽˆ›Ù›[X™\Š
K›Ü[Û˜[

KˆÝ[Žˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ›Z\ÜÚ[™×Ú[ˆŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ˜XÙ\×ÝÚ]ÝÛÛÈŽˆ›Ù›[X™\Š
K›Ü[Û˜[

BŸJK›Ü[Û˜[

BŸJJKˆšÙ^WÛ[ÛY[ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšÙ]š[šYšYYŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ™\˜˜][HŽˆ›ÙœÝš[™Ê
BŸJJBŸJKˆœ™\™\Ù[]]™WÝ˜XÙ\ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœÝ]\ÈŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ[Y\Ý[\Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JKˆœÝ[[X\žHŽˆ›Ù›Øš™XÝ
Âˆ™]˜[ÜØÛÜ™HŽˆ›Ù›[X™\Š
Kˆ›][˜ÞWÛ\ÈŽˆ›Ù›[X™\Š
Kˆ\›œÈŽˆ›Ù›[X™\Š
Kˆ›[Ù[Žˆ›ÙœÝš[™Ê
K›Z[ŠJKˆš[œ]ÝÚÙ[œÈŽˆ›Ù›[X™\Š
Kˆ›Ý]]ÝÚÙ[œÈŽˆ›Ù›[X™\Š
BŸJKˆ™]šY[˜ÙHŽˆ›Ù›Øš™XÝ
Âˆš[œ]Žˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ›Ý]]Žˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ™˜Z[Ü™Y[Žˆ›Ù˜\œ˜^J›Ùœ™XÛÜ™
›ÙœÝš[™Ê
K›ÙœÝš[™Ê
JJKˆœ\Ü×Ü™Y[Žˆ›Ù˜\œ˜^J›Ùœ™XÛÜ™
›ÙœÝš[™Ê
K›ÙœÝš[™Ê
JJKˆšYÙWÜ™X\ÛÛˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

KˆœØÛÜ™HŽˆ›Ù›[X™\Š
K›Ü[Û˜[

BŸJKˆ˜YÙ[Ù›ÝÈŽˆ›Ù›Øš™XÝ
Âˆ››Ù\ÈŽˆ›Ù˜\œ˜^J›Ùœ™XÛÜ™
›ÙœÝš[™Ê
K›ÙœÝš[™Ê
JJKˆ™YÙ\ÈŽˆ›Ù˜\œ˜^J›Ùœ™XÛÜ™
›ÙœÝš[™Ê
K›ÙœÝš[™Ê
JJBŸJKˆœ›ÛÝØØ]\Ù\ÈŽˆ›Ù˜\œ˜^J›Ùœ™XÛÜ™
›ÙœÝš[™Ê
K›ÙœÝš[™Ê
JJKˆœ™XÛÛ[Y[™][ÛœÈŽˆ›Ù˜\œ˜^J›Ùœ™XÛÜ™
›ÙœÝš[™Ê
K›ÙœÝš[™Ê
JJKˆÚ]ØÚ[™ÙYŽˆ›Ùœ™XÛÜ™
›ÙœÝš[™Ê
K›ÙœÝš[™Ê
JBŸJJKˆœ™\™\Ù[]]™WÝÝ[Žˆ›Ù›[X™\Š
K™Y˜][
˜XÙ\‘™YY\ÜÝY\ÓÝ™\šY]Ó\Ý™\ÜÛœÙT™\Ý[™\™\Ù[]]™UÝ[Y˜][
BŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆ™XYØXÚYY\X[˜[\Ú\È™\Ý[È›ÜˆHÚ[™ÛH˜XÙHÚ][ˆB˜Û\Ý\‹ˆHœ›Û[™]È\ÈÛˆ[Ý[
ÈÚÝÈ^\Ý[™È™\Ý[ÊB˜[™ÛÈ]Y\ˆHÔÕÈÙY\X[˜[\Ú\ËÈ[[Ý]\Ø›\Â™œ›ÛH[›š[™ØÈÛ™XÜˆ˜Z[Y‚ˆ
ˆÝ[[X\žHÑUÝ˜XÙ\‹Ù™YYÚ\ÜÝY\ËÞØÛ\Ý\—ÚYKÜ›ÛÝXØ]\ÙKÏÝ˜XÙWÚYVˆ
‹Â™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\Ô›ÛÝØ]\ÙS\Ý\˜[\ÈH›Ù›Øš™XÝ
Âˆ˜Û\Ý\—ÚYŽˆ›ÙœÝš[™Ê
BŸJB‚‚‚‚™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\Ô›ÛÝØ]\ÙS\Ý]Y\žT\˜[\ÈH›Ù›Øš™XÝ
Âˆ˜XÙWÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJB‚™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\Ô›ÛÝØ]\ÙS\Ý™\ÜÛœÙTÝ]\ÑY˜][HYNÂ‚‚‚‚‚‚‚‚‚‚‚‚™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\Ô›ÛÝØ]\ÙS\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
K™Y˜][
˜XÙ\‘™YY\ÜÝY\Ô›ÛÝØ]\ÙS\Ý™\ÜÛœÙTÝ]\ÑY˜][
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ˜XÙWÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœ›ÛÝØØ]\Ù\ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
Âˆœ˜[šÈŽˆ›Ù›[X™\Š
Kˆ]HŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ™\ØÜš\[ÛˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJJKˆœ™XÛÛ[Y[™][ÛœÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ]HŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ™\ØÜš\[ÛˆŽˆ›ÙœÝš[™Ê
Kˆœš[Üš]HŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœ›ÛÝØØ]\ÙWÛ[šÈŽˆ›Ù›[X™\Š
Kˆš[[YYX]WÙš^Žˆ›ÙœÝš[™Ê
K›Z[ŠJKˆš[œÚYÚÈŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ™]šY[˜ÙHŽˆ›Ù˜\œ˜^J›ÙœÝš[™Ê
K›Z[ŠJJBŸJJKˆš[[YYX]WÙš^Žˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆXØÙ\È[ˆÜ[Û˜[Ý˜XÙWÚYX]Y\žH\˜[KˆÚ[ˆ™\Ù[B˜XÙK[]™[ÙXÝ[ÛœÈ
RHY]Y]H
È]˜[X][ÛœÊH\™HÛÛ\]Y›Ü‚]˜XÙH[œÝXYÙˆHÛ\Ý\‰ÜÈ]\ÝÙY\[™ÈHÚYX˜\ˆ[‚œÞ[˜ÈÚ]HÝ™\šY]ÈX‰ÜÈ˜XÙHÙ[XÝ[Û‹‚ˆ
ˆÝ[[X\žHÑUÝ˜XÙ\‹Ù™YYÚ\ÜÝY\ËÞØÛ\Ý\—ÚYKÜÚYX˜\‹Âˆ
‹Â™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\ÔÚYX˜\“\Ý\˜[\ÈH›Ù›Øš™XÝ
Âˆ˜Û\Ý\—ÚYŽˆ›ÙœÝš[™Ê
BŸJB‚‚‚‚™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\ÔÚYX˜\“\Ý]Y\žT\˜[\ÈH›Ù›Øš™XÝ
Âˆ˜XÙWÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

BŸJB‚™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\ÔÚYX˜\“\Ý™\ÜÛœÙTÝ]\ÑY˜][HYNÂ‚‚‚‚‚‚‚‚‚‚‚‚™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\ÔÚYX˜\“\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
K™Y˜][
˜XÙ\‘™YY\ÜÝY\ÔÚYX˜\“\Ý™\ÜÛœÙTÝ]\ÑY˜][
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆ[Y[[™HŽˆ›Ù›Øš™XÝ
Âˆ™š\œÝÜÙY[ˆŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JKˆ›\ÝÜÙY[ˆŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JKˆ˜YÙWÙ^\ÈŽˆ›Ù›[X™\Š
BŸJKˆ˜ZWÛY]Y]HŽˆ›Ù›Øš™XÝ
Âˆ›[Ù[Žˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ›[Ù[Ý™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ™]˜[ÜØÛÜ™HŽˆ›Ù›[X™\Š
Kˆ˜XÙWÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJKˆ™]˜[X][ÛœÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
Âˆ›X™[Žˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ\HŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœ™\Ý[Žˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœØÛÜ™HŽˆ›Ù›[X™\Š
Kˆ˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJJKˆ˜Û×ÛØØÝ\œš[™×Ú\ÜÝY\ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ]HŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ\HŽˆ›ÙœÝš[™Ê
Kˆ˜Û×ÛØØÝ\œ™[˜ÙHŽˆ›Ù›[X™\Š
Kˆ˜ÛÝ[Žˆ›Ù›[X™\Š
KˆœÙ]™\š]HŽˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJJBŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆÑUÝ˜XÙ\‹Ù™YYÚ\ÜÝY\ËÞØÛ\Ý\—ÚYKÝ˜XÙ\ËÂˆ
‹Â™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\Õ˜XÙ\Ó\Ý\˜[\ÈH›Ù›Øš™XÝ
Âˆ˜Û\Ý\—ÚYŽˆ›ÙœÝš[™Ê
BŸJB‚™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\Õ˜XÙ\Ó\Ý]Y\žS[Z]Y˜][HLÂ™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\Õ˜XÙ\Ó\Ý]Y\žS[Z]X^HLÂ‚™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\Õ˜XÙ\Ó\Ý]Y\žSÙ™œÙ]Y˜][HÂ™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\Õ˜XÙ\Ó\Ý]Y\žSÙ™œÙ]Z[ˆHÂ‚‚‚™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\Õ˜XÙ\Ó\Ý]Y\žT\˜[\ÈH›Ù›Øš™XÝ
Âˆ›[Z]Žˆ›Ù›[X™\Š
K›Z[ŠJK›X^
˜XÙ\‘™YY\ÜÝY\Õ˜XÙ\Ó\Ý]Y\žS[Z]X^
K™Y˜][
˜XÙ\‘™YY\ÜÝY\Õ˜XÙ\Ó\Ý]Y\žS[Z]Y˜][
Kˆ›Ù™œÙ]Žˆ›Ù›[X™\Š
K›Z[Š˜XÙ\‘™YY\ÜÝY\Õ˜XÙ\Ó\Ý]Y\žSÙ™œÙ]Z[ŠK™Y˜][
˜XÙ\‘™YY\ÜÝY\Õ˜XÙ\Ó\Ý]Y\žSÙ™œÙ]Y˜][
BŸJB‚™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\Õ˜XÙ\Ó\Ý™\ÜÛœÙTÝ]\ÑY˜][HYNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\Õ˜XÙ\Ó\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
K™Y˜][
˜XÙ\‘™YY\ÜÝY\Õ˜XÙ\Ó\Ý™\ÜÛœÙTÝ]\ÑY˜][
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆ˜YÙÜ™YØ]\ÈŽˆ›Ù›Øš™XÝ
ÂˆÝ[Ý˜XÙ\ÈŽˆ›Ù›[X™\Š
Kˆ™˜Z[[™×Ý˜XÙ\ÈŽˆ›Ù›[X™\Š
Kˆœ\ÜÚ[™×Ý˜XÙ\ÈŽˆ›Ù›[X™\Š
Kˆ˜]™×ÜØÛÜ™HŽˆ›Ù›[X™\Š
KˆœLÛ][˜ÞHŽˆ›Ù›[X™\Š
KˆœMWÛ][˜ÞHŽˆ›Ù›[X™\Š
Kˆ˜]™×Ý\›œÈŽˆ›Ù›[X™\Š
BŸJKˆ˜XÙ\ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆš[œ]Žˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ[Y\Ý[\Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JKˆ›][˜ÞWÛ\ÈŽˆ›Ù›[X™\Š
KˆÚÙ[œÈŽˆ›Ù›[X™\Š
Kˆ˜ÛÜÝŽˆ›Ù›[X™\Š
KˆœØÛÜ™HŽˆ›Ù›[X™\Š
Kˆ\›œÈŽˆ›Ù›[X™\Š
BŸJJKˆÝ[Žˆ›Ù›[X™\Š
BŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆÑUÝ˜XÙ\‹Ù™YYÚ\ÜÝY\ËÞØÛ\Ý\—ÚYKÝ™[™ËÂˆ
‹Â™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\Õ™[™Ó\Ý\˜[\ÈH›Ù›Øš™XÝ
Âˆ˜Û\Ý\—ÚYŽˆ›ÙœÝš[™Ê
BŸJB‚™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\Õ™[™Ó\Ý]Y\žQ^\ÑY˜][HMÂ™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\Õ™[™Ó\Ý]Y\žQ^\ÓX^HLÂ‚‚‚™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\Õ™[™Ó\Ý]Y\žT\˜[\ÈH›Ù›Øš™XÝ
Âˆ™^\ÈŽˆ›Ù›[X™\Š
K›Z[ŠJK›X^
˜XÙ\‘™YY\ÜÝY\Õ™[™Ó\Ý]Y\žQ^\ÓX^
K™Y˜][
˜XÙ\‘™YY\ÜÝY\Õ™[™Ó\Ý]Y\žQ^\ÑY˜][
BŸJB‚™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\Õ™[™Ó\Ý™\ÜÛœÙTÝ]\ÑY˜][HYNÂ‚‚‚‚‚™^ÜÛÛœÝ˜XÙ\‘™YY\ÜÝY\Õ™[™Ó\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
K™Y˜][
˜XÙ\‘™YY\ÜÝY\Õ™[™Ó\Ý™\ÜÛœÙTÝ]\ÑY˜][
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆ›Y]šXÜÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
Âˆ›X™[Žˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ™[HŽˆ›Ù›[X™\Š
Kˆ[š]Žˆ›ÙœÝš[™Ê
BŸJJKˆ™]™[×ÛÝ™\—Ý[YHŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
Âˆ™]HŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ™\œ›ÜœÈŽˆ›Ù›[X™\Š
Kˆœ\ÜÚ[™ÈŽˆ›Ù›[X™\Š
Kˆ\Ù\œÈŽˆ›Ù›[X™\Š
BŸJJKˆœØÛÜ™WÝ™[™ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
Âˆ›X™[Žˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ˜Ý\œ™[Žˆ›Ù›[X™\Š
Kˆœ™]ˆŽˆ›Ù›[X™\Š
KˆœÜ\šÛ[™HŽˆ›Ù˜\œ˜^J›Ù›[X™\Š
JBŸJJKˆ˜XÝ]š]WÚX]X\Žˆ›Ù˜\œ˜^J›Ù˜\œ˜^J›Ù›Øš™XÝ
Âˆ™^HŽˆ›Ù›[X™\Š
KˆšÝ\ˆŽˆ›Ù›[X™\Š
Kˆ˜[YHŽˆ›Ù›[X™\Š
BŸJJJBŸJBŸJB‚‚™^ÜÛÛœÝ˜XÙ\‘Ù][››Ý][Û“X™[Ó\Ý]Y\žT\˜[\ÈH›Ù›Øš™XÝ
Âˆœ›Ú™XÝÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJB‚™^ÜÛÛœÝ˜XÙ\‘Ù][››Ý][Û“X™[Ó\Ý™\ÜÛœÙTÝ]\ÑY˜][HYNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\‘Ù][››Ý][Û“X™[Ó\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
K™Y˜][
˜XÙ\‘Ù][››Ý][Û“X™[Ó\Ý™\ÜÛœÙTÝ]\ÑY˜][
Kˆœ™\Ý[Žˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ\HŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ™\ØÜš\[ÛˆŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆœÙ][™ÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

BŸJJBŸJB‚‚‹ÊŠ‚ˆ
ˆÛ›Üˆ[˜[\Ú\È™\Ý[Ë‚ˆ
‹Â™^ÜÛÛœÝ˜XÙ\’[XYÚ[™P[˜[\Ú\Ó\Ý]Y\žU˜XÙRYX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\’[XYÚ[™P[˜[\Ú\Ó\Ý]Y\žT\˜[\ÈH›Ù›Øš™XÝ
ÂˆœØ]™YÝšY]×ÚYŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ˜XÙWÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\’[XYÚ[™P[˜[\Ú\Ó\Ý]Y\žU˜XÙRYX^
BŸJB‚™^ÜÛÛœÝ˜XÙ\’[XYÚ[™P[˜[\Ú\Ó\Ý™\ÜÛœÙTÝ]\ÑY˜][HYNÂ™^ÜÛÛœÝ˜XÙ\’[XYÚ[™P[˜[\Ú\Ó\Ý™\ÜÛœÙT™\Ý[[˜[\Ù\Ò][UÚYÙ]YX^HLÂ‚‚‚™^ÜÛÛœÝ˜XÙ\’[XYÚ[™P[˜[\Ú\Ó\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
K™Y˜][
˜XÙ\’[XYÚ[™P[˜[\Ú\Ó\Ý™\ÜÛœÙTÝ]\ÑY˜][
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆ˜[˜[\Ù\ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

KˆÚYÙ]ÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\’[XYÚ[™P[˜[\Ú\Ó\Ý™\ÜÛœÙT™\Ý[[˜[\Ù\Ò][UÚYÙ]YX^
KˆœÝ]\ÈŽˆ›Ù™[[JÉÜ[™[™ÉË	Ü[›š[™ÉË	ØÛÛ\]Y	Ë	Ù˜Z[Y	×JKˆ˜ÛÛ[Žˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ™\œ›ÜˆŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

BŸJJBŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆšYÙÙ\ˆ[˜[\Ú\È›ÜˆÚYÙ]ËˆÜ™X]\Èˆ™XÛÜ™È
ÈÝ\È[\Ü˜[ÛÜšÙ›ÝÜË‚ˆ
‹Â™^ÜÛÛœÝ˜XÙ\’[XYÚ[™P[˜[\Ú\ÐÜ™X]P›ÙU˜XÙRYX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\’[XYÚ[™P[˜[\Ú\ÐÜ™X]P›ÙUÚYÙ]Ò][UÚYÙ]YX^HLÂ‚™^ÜÛÛœÝ˜XÙ\’[XYÚ[™P[˜[\Ú\ÐÜ™X]P›ÙUÚYÙ]Ò][T›Û\X^HÂ‚‚‚™^ÜÛÛœÝ˜XÙ\’[XYÚ[™P[˜[\Ú\ÐÜ™X]P›ÙHH›Ù›Øš™XÝ
ÂˆœØ]™YÝšY]×ÚYŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ˜XÙWÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\’[XYÚ[™P[˜[\Ú\ÐÜ™X]P›ÙU˜XÙRYX^
Kˆœ›Ú™XÝÚYŽˆ›ÙœÝš[™Ê
K]ZY

KˆÚYÙ]ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆÚYÙ]ÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\’[XYÚ[™P[˜[\Ú\ÐÜ™X]P›ÙUÚYÙ]Ò][UÚYÙ]YX^
Kˆœ›Û\Žˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\’[XYÚ[™P[˜[\Ú\ÐÜ™X]P›ÙUÚYÙ]Ò][T›Û\X^
BŸJJBŸJB‚™^ÜÛÛœÝ˜XÙ\’[XYÚ[™P[˜[\Ú\ÐÜ™X]T™\ÜÛœÙTÝ]\ÑY˜][HYNÂ™^ÜÛÛœÝ˜XÙ\’[XYÚ[™P[˜[\Ú\ÐÜ™X]T™\ÜÛœÙT™\Ý[[˜[\Ù\Ò][UÚYÙ]YX^HLÂ‚‚‚™^ÜÛÛœÝ˜XÙ\’[XYÚ[™P[˜[\Ú\ÐÜ™X]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
K™Y˜][
˜XÙ\’[XYÚ[™P[˜[\Ú\ÐÜ™X]T™\ÜÛœÙTÝ]\ÑY˜][
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆ˜[˜[\Ù\ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

KˆÚYÙ]ÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\’[XYÚ[™P[˜[\Ú\ÐÜ™X]T™\ÜÛœÙT™\Ý[[˜[\Ù\Ò][UÚYÙ]YX^
KˆœÝ]\ÈŽˆ›Ù™[[JÉÜ[™[™ÉË	Ü[›š[™ÉË	ØÛÛ\]Y	Ë	Ù˜Z[Y	×JKˆ˜ÛÛ[Žˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ™\œ›ÜˆŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

BŸJJBŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆTH[™Ú[È›ÜˆX[˜YÚ[™ÈØœÙ\˜Xš[]H›ÝšY\œË‚ˆ
‹Â™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜Xš[]T›ÝšY\“\Ý]Y\žT\˜[\ÈH›Ù›Øš™XÝ
ÂˆœYÙHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	ÐHYÙH[X™\ˆÚ][ˆHYÚ[˜]Y™\Ý[Ù]‰ÊKˆ›[Z]Žˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	Ó[X™\ˆÙˆ™\Ý[ÈÈ™]\›ˆ\ˆYÙK‰ÊBŸJB‚‚‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜Xš[]T›ÝšY\“\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
Âˆ˜ÛÝ[Žˆ›Ù›[X™\Š
Kˆ›™^Žˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™]š[Ý\ÈŽˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™\Ý[ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Ú™XÝÛ˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

K™\ØÜšX™J	Ó˜[YHÙˆH›Ú™XÝˆYˆ]Ù\Û—	Ý^\Ý]Ú[™HÜ™X]Y‰ÊKˆœ›ÝšY\ˆŽˆ›Ù™[[JÉÝ˜\IË	Ù[]™[—ÛXœÉË	Ü™][	Ë	Û]™ZÚ]	Ë	ÛÝ\œÉË	Ø›[™	Ë	ÝÚ[[É×JKˆ™[˜X›YŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ›Ü™Ø[š^˜][ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

KˆÛÜšÜÜXÙHŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ\]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

BŸJJBŸJB‚‚‹ÊŠ‚ˆ
ˆTH[™Ú[È›ÜˆX[˜YÚ[™ÈØœÙ\˜Xš[]H›ÝšY\œË‚ˆ
‹Â‚‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜Xš[]T›ÝšY\Ü™X]P›ÙHH›Ù›Øš™XÝ
Âˆœ›Ú™XÝÛ˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

K™\ØÜšX™J	Ó˜[YHÙˆH›Ú™XÝˆYˆ]Ù\Û—	Ý^\Ý]Ú[™HÜ™X]Y‰ÊKˆœ›ÝšY\ˆŽˆ›Ù™[[JÉÝ˜\IË	Ù[]™[—ÛXœÉË	Ü™][	Ë	Û]™ZÚ]	Ë	ÛÝ\œÉË	Ø›[™	Ë	ÝÚ[[É×JKˆ™[˜X›YŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

BŸJB‚‚‹ÊŠ‚ˆ
ˆTH[™Ú[È›ÜˆX[˜YÚ[™ÈØœÙ\˜Xš[]H›ÝšY\œË‚ˆ
‹Â™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜Xš[]T›ÝšY\•™\šYžP\RÙ^P›ÙHH›Ù›Øš™XÝ
Âˆœ›ÝšY\ˆŽˆ›Ù™[[JÉÝ˜\IË	Ü™][	Ë	Ø›[™	×JKˆ˜\WÚÙ^HŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ˜YÙ[ÚYŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

BŸJB‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜Xš[]T›ÝšY\•™\šYžP\RÙ^T™\ÜÛœÙTÝ]\ÑY˜][HYNÂ‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜Xš[]T›ÝšY\•™\šYžP\RÙ^T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
K™Y˜][
˜XÙ\“ØœÙ\˜Xš[]T›ÝšY\•™\šYžP\RÙ^T™\ÜÛœÙTÝ]\ÑY˜][
Kˆœ™\Ý[Žˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJB‚‚‹ÊŠ‚ˆ
ˆTH[™Ú[È›ÜˆX[˜YÚ[™ÈØœÙ\˜Xš[]H›ÝšY\œË‚ˆ
‹Â™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜Xš[]T›ÝšY\•™\šYžP\ÜÚ\Ý[Y›ÙHH›Ù›Øš™XÝ
Âˆœ›ÝšY\ˆŽˆ›Ù™[[JÉÝ˜\IË	Ü™][	Ë	Ø›[™	×JKˆ˜\ÜÚ\Ý[ÚYŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ˜\WÚÙ^HŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ˜YÙ[ÚYŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

BŸJB‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜Xš[]T›ÝšY\•™\šYžP\ÜÚ\Ý[Y™\ÜÛœÙTÝ]\ÑY˜][HYNÂ‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜Xš[]T›ÝšY\•™\šYžP\ÜÚ\Ý[Y™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
K™Y˜][
˜XÙ\“ØœÙ\˜Xš[]T›ÝšY\•™\šYžP\ÜÚ\Ý[Y™\ÜÛœÙTÝ]\ÑY˜][
Kˆœ™\Ý[Žˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJB‚‚‹ÊŠ‚ˆ
ˆTH[™Ú[È›ÜˆX[˜YÚ[™ÈØœÙ\˜Xš[]H›ÝšY\œË‚ˆ
‹Â™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜Xš[]T›ÝšY\”™XY\˜[\ÈH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
BŸJB‚‚‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜Xš[]T›ÝšY\”™XY™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Ú™XÝÛ˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

K™\ØÜšX™J	Ó˜[YHÙˆH›Ú™XÝˆYˆ]Ù\Û—	Ý^\Ý]Ú[™HÜ™X]Y‰ÊKˆœ›ÝšY\ˆŽˆ›Ù™[[JÉÝ˜\IË	Ù[]™[—ÛXœÉË	Ü™][	Ë	Û]™ZÚ]	Ë	ÛÝ\œÉË	Ø›[™	Ë	ÝÚ[[É×JKˆ™[˜X›YŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ›Ü™Ø[š^˜][ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

KˆÛÜšÜÜXÙHŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ\]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

BŸJB‚‚‹ÊŠ‚ˆ
ˆTH[™Ú[È›ÜˆX[˜YÚ[™ÈØœÙ\˜Xš[]H›ÝšY\œË‚ˆ
‹Â™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜Xš[]T›ÝšY\•\]T\˜[\ÈH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
BŸJB‚‚‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜Xš[]T›ÝšY\•\]P›ÙHH›Ù›Øš™XÝ
Âˆœ›Ú™XÝÛ˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

K™\ØÜšX™J	Ó˜[YHÙˆH›Ú™XÝˆYˆ]Ù\Û—	Ý^\Ý]Ú[™HÜ™X]Y‰ÊKˆœ›ÝšY\ˆŽˆ›Ù™[[JÉÝ˜\IË	Ù[]™[—ÛXœÉË	Ü™][	Ë	Û]™ZÚ]	Ë	ÛÝ\œÉË	Ø›[™	Ë	ÝÚ[[É×JKˆ™[˜X›YŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

BŸJB‚‚‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜Xš[]T›ÝšY\•\]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Ú™XÝÛ˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

K™\ØÜšX™J	Ó˜[YHÙˆH›Ú™XÝˆYˆ]Ù\Û—	Ý^\Ý]Ú[™HÜ™X]Y‰ÊKˆœ›ÝšY\ˆŽˆ›Ù™[[JÉÝ˜\IË	Ù[]™[—ÛXœÉË	Ü™][	Ë	Û]™ZÚ]	Ë	ÛÝ\œÉË	Ø›[™	Ë	ÝÚ[[É×JKˆ™[˜X›YŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ›Ü™Ø[š^˜][ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

KˆÛÜšÜÜXÙHŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ\]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

BŸJB‚‚‹ÊŠ‚ˆ
ˆTH[™Ú[È›ÜˆX[˜YÚ[™ÈØœÙ\˜Xš[]H›ÝšY\œË‚ˆ
‹Â™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜Xš[]T›ÝšY\”\X[\]T\˜[\ÈH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
BŸJB‚‚‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜Xš[]T›ÝšY\”\X[\]P›ÙHH›Ù›Øš™XÝ
Âˆœ›Ú™XÝÛ˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

K™\ØÜšX™J	Ó˜[YHÙˆH›Ú™XÝˆYˆ]Ù\Û—	Ý^\Ý]Ú[™HÜ™X]Y‰ÊKˆœ›ÝšY\ˆŽˆ›Ù™[[JÉÝ˜\IË	Ù[]™[—ÛXœÉË	Ü™][	Ë	Û]™ZÚ]	Ë	ÛÝ\œÉË	Ø›[™	Ë	ÝÚ[[É×JKˆ™[˜X›YŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

BŸJB‚‚‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜Xš[]T›ÝšY\”\X[\]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Ú™XÝÛ˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

K™\ØÜšX™J	Ó˜[YHÙˆH›Ú™XÝˆYˆ]Ù\Û—	Ý^\Ý]Ú[™HÜ™X]Y‰ÊKˆœ›ÝšY\ˆŽˆ›Ù™[[JÉÝ˜\IË	Ù[]™[—ÛXœÉË	Ü™][	Ë	Û]™ZÚ]	Ë	ÛÝ\œÉË	Ø›[™	Ë	ÝÚ[[É×JKˆ™[˜X›YŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ›Ü™Ø[š^˜][ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

KˆÛÜšÜÜXÙHŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ\]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

BŸJB‚‚‹ÊŠ‚ˆ
ˆTH[™Ú[È›ÜˆX[˜YÚ[™ÈØœÙ\˜Xš[]H›ÝšY\œË‚ˆ
‹Â™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜Xš[]T›ÝšY\‘[]T\˜[\ÈH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
BŸJB‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[“\Ý]Y\žT\˜[\ÈH›Ù›Øš™XÝ
ÂˆœYÙHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	ÐHYÙH[X™\ˆÚ][ˆHYÚ[˜]Y™\Ý[Ù]‰ÊKˆ›[Z]Žˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	Ó[X™\ˆÙˆ™\Ý[ÈÈ™]\›ˆ\ˆYÙK‰ÊBŸJB‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[“\Ý™\ÜÛœÙT™\Ý[Ò][T\™[Ü[’YX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[“\Ý™\ÜÛœÙT™\Ý[Ò][S˜[YSX^HŒÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[“\Ý™\ÜÛœÙT™\Ý[Ò][S[Ù[X^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[“\Ý™\ÜÛœÙT™\Ý[Ò][S][˜ÞS\ÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[“\Ý™\ÜÛœÙT™\Ý[Ò][S][˜ÞS\ÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[“\Ý™\ÜÛœÙT™\Ý[Ò][T›Û\ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[“\Ý™\ÜÛœÙT™\Ý[Ò][T›Û\ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[“\Ý™\ÜÛœÙT™\Ý[Ò][PÛÛ\][Û•ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[“\Ý™\ÜÛœÙT™\Ý[Ò][PÛÛ\][Û•ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[“\Ý™\ÜÛœÙT™\Ý[Ò][UÝ[ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[“\Ý™\ÜÛœÙT™\Ý[Ò][UÝ[ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[“\Ý™\ÜÛœÙT™\Ý[Ò][Q]˜[YX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[“\Ý™\ÜÛœÙT™\Ý[Ò][T›ÝšY\“X^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[“\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
Âˆ˜ÛÝ[Žˆ›Ù›[X™\Š
Kˆ›™^Žˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™]š[Ý\ÈŽˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™\Ý[ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ›Ú™XÝÝ™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜XÙHŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ\™[ÜÜ[—ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[“\Ý™\ÜÛœÙT™\Ý[Ò][T\™[Ü[’YX^
K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[“\Ý™\ÜÛœÙT™\Ý[Ò][S˜[YSX^
Kˆ›ØœÙ\˜][Û—Ý\HŽˆ›Ù™[[JÉÝÛÛ	Ë	ØÚZ[‰Ë	ÛIË	Ü™]šY]™\‰Ë	Ù[X™Y[™ÉË	ØYÙ[	Ë	Ü™\˜[šÙ\‰Ë	Ý[šÛ›ÝÛ‰Ë	ÙÝX\™˜Z[	Ë	Ù]˜[X]Ü‰Ë	ØÛÛ™\œØ][Û‰×JKˆœÝ\Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™[™Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆš[œ]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Ý]]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›[Ù[Žˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[“\Ý™\ÜÛœÙT™\Ý[Ò][S[Ù[X^
K›Ü[Û˜[

Kˆ›[Ù[Ü\˜[Y]\œÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›][˜ÞWÛ\ÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[“\Ý™\ÜÛœÙT™\Ý[Ò][S][˜ÞS\ÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[“\Ý™\ÜÛœÙT™\Ý[Ò][S][˜ÞS\ÓX^
K›Ü[Û˜[

Kˆ›Ü™×ÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›Ü™×Ý\Ù\—ÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Û\ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[“\Ý™\ÜÛœÙT™\Ý[Ò][T›Û\ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[“\Ý™\ÜÛœÙT™\Ý[Ò][T›Û\ÚÙ[œÓX^
K›Ü[Û˜[

Kˆ˜ÛÛ\][Û—ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[“\Ý™\ÜÛœÙT™\Ý[Ò][PÛÛ\][Û•ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[“\Ý™\ÜÛœÙT™\Ý[Ò][PÛÛ\][Û•ÚÙ[œÓX^
K›Ü[Û˜[

KˆÝ[ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[“\Ý™\ÜÛœÙT™\Ý[Ò][UÝ[ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[“\Ý™\ÜÛœÙT™\Ý[Ò][UÝ[ÚÙ[œÓX^
K›Ü[Û˜[

Kˆœ™\ÜÛœÙWÝ[YHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ™]˜[ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[“\Ý™\ÜÛœÙT™\Ý[Ò][Q]˜[YX^
K›Ü[Û˜[

Kˆ˜ÛÜÝŽˆ›Ù›[X™\Š
K›Ü[Û˜[

KˆœÝ]\ÈŽˆ›Ù™[[JÉÕS”ÑU	Ë	ÓÒÉË	ÑT”“Ô‰×JK›Ü[Û˜[

KˆœÝ]\×ÛY\ÜØYÙHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÜ[—Ù]™[ÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆœ›ÝšY\ˆŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[“\Ý™\ÜÛœÙT™\Ý[Ò][T›ÝšY\“X^
K›Ü[Û˜[

Kˆœ›ÝšY\—ÛÙÛÈŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆœÜ[—Ø]šX]\ÈŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ˜Ý\ÝÛWÙ]˜[ØÛÛ™šYÈŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ™]˜[ÜÝ]\ÈŽˆ›Ù™[[JÉÓ›ÝÝ\Y	Ë	Ô]Y]YY	Ë	Ô[›š[™ÉË	ÐÛÛ\]Y	Ë	ÑY][™ÉË	Ò[˜XÝ]™IË	Ñ˜Z[Y	Ë	Ô\X[[‰Ë	Ñ^\š[Y[]˜[X][Û‰Ë	Õ\ØY[™ÉË	Ô\X[^˜XÝY	Ë	Ô›ØÙ\ÜÚ[™ÉË	Ñ[][™ÉË	Ô\X[ÛÛ\]Y	Ë	ÓÜ[Z^˜][Û‘]˜[X][Û‰Ë	Ñ\œ›Ü‰Ë	ÐØ[˜Ù[Y	×JK›Ü[Û˜[

Kˆœ›Û\Ý™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJJBŸJB‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[Ü™X]P›ÙT\™[Ü[’YX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[Ü™X]P›ÙS˜[YSX^HŒÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[Ü™X]P›ÙS[Ù[X^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[Ü™X]P›ÙS][˜ÞS\ÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[Ü™X]P›ÙS][˜ÞS\ÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[Ü™X]P›ÙT›Û\ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[Ü™X]P›ÙT›Û\ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[Ü™X]P›ÙPÛÛ\][Û•ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[Ü™X]P›ÙPÛÛ\][Û•ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[Ü™X]P›ÙUÝ[ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[Ü™X]P›ÙUÝ[ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[Ü™X]P›ÙQ]˜[YX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[Ü™X]P›ÙT›ÝšY\“X^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[Ü™X]P›ÙHH›Ù›Øš™XÝ
Âˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ›Ú™XÝÝ™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜XÙHŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ\™[ÜÜ[—ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[Ü™X]P›ÙT\™[Ü[’YX^
K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[Ü™X]P›ÙS˜[YSX^
Kˆ›ØœÙ\˜][Û—Ý\HŽˆ›Ù™[[JÉÝÛÛ	Ë	ØÚZ[‰Ë	ÛIË	Ü™]šY]™\‰Ë	Ù[X™Y[™ÉË	ØYÙ[	Ë	Ü™\˜[šÙ\‰Ë	Ý[šÛ›ÝÛ‰Ë	ÙÝX\™˜Z[	Ë	Ù]˜[X]Ü‰Ë	ØÛÛ™\œØ][Û‰×JKˆœÝ\Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™[™Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆš[œ]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Ý]]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›[Ù[Žˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[Ü™X]P›ÙS[Ù[X^
K›Ü[Û˜[

Kˆ›[Ù[Ü\˜[Y]\œÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›][˜ÞWÛ\ÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[Ü™X]P›ÙS][˜ÞS\ÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[Ü™X]P›ÙS][˜ÞS\ÓX^
K›Ü[Û˜[

Kˆ›Ü™×ÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›Ü™×Ý\Ù\—ÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Û\ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[Ü™X]P›ÙT›Û\ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[Ü™X]P›ÙT›Û\ÚÙ[œÓX^
K›Ü[Û˜[

Kˆ˜ÛÛ\][Û—ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[Ü™X]P›ÙPÛÛ\][Û•ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[Ü™X]P›ÙPÛÛ\][Û•ÚÙ[œÓX^
K›Ü[Û˜[

KˆÝ[ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[Ü™X]P›ÙUÝ[ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[Ü™X]P›ÙUÝ[ÚÙ[œÓX^
K›Ü[Û˜[

Kˆœ™\ÜÛœÙWÝ[YHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ™]˜[ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[Ü™X]P›ÙQ]˜[YX^
K›Ü[Û˜[

Kˆ˜ÛÜÝŽˆ›Ù›[X™\Š
K›Ü[Û˜[

KˆœÝ]\ÈŽˆ›Ù™[[JÉÕS”ÑU	Ë	ÓÒÉË	ÑT”“Ô‰×JK›Ü[Û˜[

KˆœÝ]\×ÛY\ÜØYÙHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÜ[—Ù]™[ÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆœ›ÝšY\ˆŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[Ü™X]P›ÙT›ÝšY\“X^
K›Ü[Û˜[

Kˆ˜Ý\ÝÛWÙ]˜[ØÛÛ™šYÈŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ™]˜[ÜÝ]\ÈŽˆ›Ù™[[JÉÓ›ÝÝ\Y	Ë	Ô]Y]YY	Ë	Ô[›š[™ÉË	ÐÛÛ\]Y	Ë	ÑY][™ÉË	Ò[˜XÝ]™IË	Ñ˜Z[Y	Ë	Ô\X[[‰Ë	Ñ^\š[Y[]˜[X][Û‰Ë	Õ\ØY[™ÉË	Ô\X[^˜XÝY	Ë	Ô›ØÙ\ÜÚ[™ÉË	Ñ[][™ÉË	Ô\X[ÛÛ\]Y	Ë	ÓÜ[Z^˜][Û‘]˜[X][Û‰Ë	Ñ\œ›Ü‰Ë	ÐØ[˜Ù[Y	×JK›Ü[Û˜[

Kˆœ›Û\Ý™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJB‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[Y[››Ý][ÛœÐ›ÙHH›Ù›Øš™XÝ
Âˆ›ØœÙ\˜][Û—ÜÜ[—ÚYŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ˜XÙWÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜[››Ý][Û—Ý˜[Y\ÈŽˆ›Ùœ™XÛÜ™
›ÙœÝš[™Ê
K›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

JKˆ››Ý\ÈŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

BŸJB‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[[ÐÜ™X]P›ÙT\™[Ü[’YX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[[ÐÜ™X]P›ÙS˜[YSX^HŒÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[[ÐÜ™X]P›ÙS[Ù[X^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[[ÐÜ™X]P›ÙS][˜ÞS\ÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[[ÐÜ™X]P›ÙS][˜ÞS\ÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[[ÐÜ™X]P›ÙT›Û\ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[[ÐÜ™X]P›ÙT›Û\ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[[ÐÜ™X]P›ÙPÛÛ\][Û•ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[[ÐÜ™X]P›ÙPÛÛ\][Û•ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[[ÐÜ™X]P›ÙUÝ[ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[[ÐÜ™X]P›ÙUÝ[ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[[ÐÜ™X]P›ÙQ]˜[YX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[[ÐÜ™X]P›ÙT›ÝšY\“X^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[[ÐÜ™X]P›ÙHH›Ù›Øš™XÝ
Âˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ›Ú™XÝÝ™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜XÙHŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ\™[ÜÜ[—ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[[ÐÜ™X]P›ÙT\™[Ü[’YX^
K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[[ÐÜ™X]P›ÙS˜[YSX^
Kˆ›ØœÙ\˜][Û—Ý\HŽˆ›Ù™[[JÉÝÛÛ	Ë	ØÚZ[‰Ë	ÛIË	Ü™]šY]™\‰Ë	Ù[X™Y[™ÉË	ØYÙ[	Ë	Ü™\˜[šÙ\‰Ë	Ý[šÛ›ÝÛ‰Ë	ÙÝX\™˜Z[	Ë	Ù]˜[X]Ü‰Ë	ØÛÛ™\œØ][Û‰×JKˆœÝ\Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™[™Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆš[œ]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Ý]]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›[Ù[Žˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[[ÐÜ™X]P›ÙS[Ù[X^
K›Ü[Û˜[

Kˆ›[Ù[Ü\˜[Y]\œÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›][˜ÞWÛ\ÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[[ÐÜ™X]P›ÙS][˜ÞS\ÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[[ÐÜ™X]P›ÙS][˜ÞS\ÓX^
K›Ü[Û˜[

Kˆ›Ü™×ÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›Ü™×Ý\Ù\—ÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Û\ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[[ÐÜ™X]P›ÙT›Û\ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[[ÐÜ™X]P›ÙT›Û\ÚÙ[œÓX^
K›Ü[Û˜[

Kˆ˜ÛÛ\][Û—ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[[ÐÜ™X]P›ÙPÛÛ\][Û•ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[[ÐÜ™X]P›ÙPÛÛ\][Û•ÚÙ[œÓX^
K›Ü[Û˜[

KˆÝ[ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[[ÐÜ™X]P›ÙUÝ[ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[[ÐÜ™X]P›ÙUÝ[ÚÙ[œÓX^
K›Ü[Û˜[

Kˆœ™\ÜÛœÙWÝ[YHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ™]˜[ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[[ÐÜ™X]P›ÙQ]˜[YX^
K›Ü[Û˜[

Kˆ˜ÛÜÝŽˆ›Ù›[X™\Š
K›Ü[Û˜[

KˆœÝ]\ÈŽˆ›Ù™[[JÉÕS”ÑU	Ë	ÓÒÉË	ÑT”“Ô‰×JK›Ü[Û˜[

KˆœÝ]\×ÛY\ÜØYÙHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÜ[—Ù]™[ÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆœ›ÝšY\ˆŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[[ÐÜ™X]P›ÙT›ÝšY\“X^
K›Ü[Û˜[

Kˆ˜Ý\ÝÛWÙ]˜[ØÛÛ™šYÈŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ™]˜[ÜÝ]\ÈŽˆ›Ù™[[JÉÓ›ÝÝ\Y	Ë	Ô]Y]YY	Ë	Ô[›š[™ÉË	ÐÛÛ\]Y	Ë	ÑY][™ÉË	Ò[˜XÝ]™IË	Ñ˜Z[Y	Ë	Ô\X[[‰Ë	Ñ^\š[Y[]˜[X][Û‰Ë	Õ\ØY[™ÉË	Ô\X[^˜XÝY	Ë	Ô›ØÙ\ÜÚ[™ÉË	Ñ[][™ÉË	Ô\X[ÛÛ\]Y	Ë	ÓÜ[Z^˜][Û‘]˜[X][Û‰Ë	Ñ\œ›Ü‰Ë	ÐØ[˜Ù[Y	×JK›Ü[Û˜[

Kˆœ›Û\Ý™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJB‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[Ü™X]SÝ[Ü[›ÙT\™[Ü[’YX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[Ü™X]SÝ[Ü[›ÙS˜[YSX^HŒÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[Ü™X]SÝ[Ü[›ÙS[Ù[X^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[Ü™X]SÝ[Ü[›ÙS][˜ÞS\ÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[Ü™X]SÝ[Ü[›ÙS][˜ÞS\ÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[Ü™X]SÝ[Ü[›ÙT›Û\ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[Ü™X]SÝ[Ü[›ÙT›Û\ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[Ü™X]SÝ[Ü[›ÙPÛÛ\][Û•ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[Ü™X]SÝ[Ü[›ÙPÛÛ\][Û•ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[Ü™X]SÝ[Ü[›ÙUÝ[ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[Ü™X]SÝ[Ü[›ÙUÝ[ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[Ü™X]SÝ[Ü[›ÙQ]˜[YX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[Ü™X]SÝ[Ü[›ÙT›ÝšY\“X^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[Ü™X]SÝ[Ü[›ÙHH›Ù›Øš™XÝ
Âˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ›Ú™XÝÝ™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜XÙHŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ\™[ÜÜ[—ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[Ü™X]SÝ[Ü[›ÙT\™[Ü[’YX^
K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[Ü™X]SÝ[Ü[›ÙS˜[YSX^
Kˆ›ØœÙ\˜][Û—Ý\HŽˆ›Ù™[[JÉÝÛÛ	Ë	ØÚZ[‰Ë	ÛIË	Ü™]šY]™\‰Ë	Ù[X™Y[™ÉË	ØYÙ[	Ë	Ü™\˜[šÙ\‰Ë	Ý[šÛ›ÝÛ‰Ë	ÙÝX\™˜Z[	Ë	Ù]˜[X]Ü‰Ë	ØÛÛ™\œØ][Û‰×JKˆœÝ\Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™[™Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆš[œ]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Ý]]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›[Ù[Žˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[Ü™X]SÝ[Ü[›ÙS[Ù[X^
K›Ü[Û˜[

Kˆ›[Ù[Ü\˜[Y]\œÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›][˜ÞWÛ\ÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[Ü™X]SÝ[Ü[›ÙS][˜ÞS\ÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[Ü™X]SÝ[Ü[›ÙS][˜ÞS\ÓX^
K›Ü[Û˜[

Kˆ›Ü™×ÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›Ü™×Ý\Ù\—ÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Û\ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[Ü™X]SÝ[Ü[›ÙT›Û\ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[Ü™X]SÝ[Ü[›ÙT›Û\ÚÙ[œÓX^
K›Ü[Û˜[

Kˆ˜ÛÛ\][Û—ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[Ü™X]SÝ[Ü[›ÙPÛÛ\][Û•ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[Ü™X]SÝ[Ü[›ÙPÛÛ\][Û•ÚÙ[œÓX^
K›Ü[Û˜[

KˆÝ[ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[Ü™X]SÝ[Ü[›ÙUÝ[ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[Ü™X]SÝ[Ü[›ÙUÝ[ÚÙ[œÓX^
K›Ü[Û˜[

Kˆœ™\ÜÛœÙWÝ[YHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ™]˜[ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[Ü™X]SÝ[Ü[›ÙQ]˜[YX^
K›Ü[Û˜[

Kˆ˜ÛÜÝŽˆ›Ù›[X™\Š
K›Ü[Û˜[

KˆœÝ]\ÈŽˆ›Ù™[[JÉÕS”ÑU	Ë	ÓÒÉË	ÑT”“Ô‰×JK›Ü[Û˜[

KˆœÝ]\×ÛY\ÜØYÙHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÜ[—Ù]™[ÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆœ›ÝšY\ˆŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[Ü™X]SÝ[Ü[›ÙT›ÝšY\“X^
K›Ü[Û˜[

Kˆ˜Ý\ÝÛWÙ]˜[ØÛÛ™šYÈŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ™]˜[ÜÝ]\ÈŽˆ›Ù™[[JÉÓ›ÝÝ\Y	Ë	Ô]Y]YY	Ë	Ô[›š[™ÉË	ÐÛÛ\]Y	Ë	ÑY][™ÉË	Ò[˜XÝ]™IË	Ñ˜Z[Y	Ë	Ô\X[[‰Ë	Ñ^\š[Y[]˜[X][Û‰Ë	Õ\ØY[™ÉË	Ô\X[^˜XÝY	Ë	Ô›ØÙ\ÜÚ[™ÉË	Ñ[][™ÉË	Ô\X[ÛÛ\]Y	Ë	ÓÜ[Z^˜][Û‘]˜[X][Û‰Ë	Ñ\œ›Ü‰Ë	ÐØ[˜Ù[Y	×JK›Ü[Û˜[

Kˆœ›Û\Ý™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJB‚‚‹ÊŠ‚ˆ
ˆ]Y\žH\˜[\Î‚ˆš[\œÎˆ”ÓÓˆÈœ›Ú™XÝÚYŽˆ]ZYˆŸH
™\]Z\™Y
Bˆ›Ý×Ý\NˆÜ[œÈ˜XÙ\ÈÙ\ÜÚ[ÛœÈ
Y˜][Ü[œÎÂˆ›ÚXÙPØ[È[X\Ù\ÈÈÜ[œÊBˆ
ˆÝ[[X\žH]šX]H]ÈH]˜[XÚÙ\ˆ^ÜÙ\È\ˆ›Ý×Ý\K‚ˆ
‹Â‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]]˜[]šX]\Ó\Ý]Y\žT›ÝÕ\QY˜][HÜ[œØÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]]˜[]šX]\Ó\Ý]Y\žT\˜[\ÈH›Ù›Øš™XÝ
ÂˆœYÙHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	ÐHYÙH[X™\ˆÚ][ˆHYÚ[˜]Y™\Ý[Ù]‰ÊKˆ›[Z]Žˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	Ó[X™\ˆÙˆ™\Ý[ÈÈ™]\›ˆ\ˆYÙK‰ÊKˆ™š[\œÈŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœ›Ý×Ý\HŽˆ›Ù™[[JÉÜÜ[œÉË	Ý˜XÙ\ÉË	ÜÙ\ÜÚ[ÛœÉË	Ý›ÚXÙPØ[É×JK™Y˜][
˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]]˜[]šX]\Ó\Ý]Y\žT›ÝÕ\QY˜][
BŸJB‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]]˜[]šX]\Ó\Ý™\ÜÛœÙTÝ]\ÑY˜][HYNÂ‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]]˜[]šX]\Ó\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
K™Y˜][
˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]]˜[]šX]\Ó\Ý™\ÜÛœÙTÝ]\ÑY˜][
Kˆœ™\Ý[Žˆ›Ù˜\œ˜^J›ÙœÝš[™Ê
K›Z[ŠJJBŸJB‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]]˜[X][Û‘]Z[Ô]Y\žT\˜[\ÈH›Ù›Øš™XÝ
ÂˆœYÙHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	ÐHYÙH[X™\ˆÚ][ˆHYÚ[˜]Y™\Ý[Ù]‰ÊKˆ›[Z]Žˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	Ó[X™\ˆÙˆ™\Ý[ÈÈ™]\›ˆ\ˆYÙK‰ÊBŸJB‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]]˜[X][Û‘]Z[Ô™\ÜÛœÙT™\Ý[Ò][T\™[Ü[’YX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]]˜[X][Û‘]Z[Ô™\ÜÛœÙT™\Ý[Ò][S˜[YSX^HŒÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]]˜[X][Û‘]Z[Ô™\ÜÛœÙT™\Ý[Ò][S[Ù[X^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]]˜[X][Û‘]Z[Ô™\ÜÛœÙT™\Ý[Ò][S][˜ÞS\ÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]]˜[X][Û‘]Z[Ô™\ÜÛœÙT™\Ý[Ò][S][˜ÞS\ÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]]˜[X][Û‘]Z[Ô™\ÜÛœÙT™\Ý[Ò][T›Û\ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]]˜[X][Û‘]Z[Ô™\ÜÛœÙT™\Ý[Ò][T›Û\ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]]˜[X][Û‘]Z[Ô™\ÜÛœÙT™\Ý[Ò][PÛÛ\][Û•ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]]˜[X][Û‘]Z[Ô™\ÜÛœÙT™\Ý[Ò][PÛÛ\][Û•ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]]˜[X][Û‘]Z[Ô™\ÜÛœÙT™\Ý[Ò][UÝ[ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]]˜[X][Û‘]Z[Ô™\ÜÛœÙT™\Ý[Ò][UÝ[ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]]˜[X][Û‘]Z[Ô™\ÜÛœÙT™\Ý[Ò][Q]˜[YX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]]˜[X][Û‘]Z[Ô™\ÜÛœÙT™\Ý[Ò][T›ÝšY\“X^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]]˜[X][Û‘]Z[Ô™\ÜÛœÙHH›Ù›Øš™XÝ
Âˆ˜ÛÝ[Žˆ›Ù›[X™\Š
Kˆ›™^Žˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™]š[Ý\ÈŽˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™\Ý[ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ›Ú™XÝÝ™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜XÙHŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ\™[ÜÜ[—ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]]˜[X][Û‘]Z[Ô™\ÜÛœÙT™\Ý[Ò][T\™[Ü[’YX^
K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]]˜[X][Û‘]Z[Ô™\ÜÛœÙT™\Ý[Ò][S˜[YSX^
Kˆ›ØœÙ\˜][Û—Ý\HŽˆ›Ù™[[JÉÝÛÛ	Ë	ØÚZ[‰Ë	ÛIË	Ü™]šY]™\‰Ë	Ù[X™Y[™ÉË	ØYÙ[	Ë	Ü™\˜[šÙ\‰Ë	Ý[šÛ›ÝÛ‰Ë	ÙÝX\™˜Z[	Ë	Ù]˜[X]Ü‰Ë	ØÛÛ™\œØ][Û‰×JKˆœÝ\Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™[™Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆš[œ]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Ý]]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›[Ù[Žˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]]˜[X][Û‘]Z[Ô™\ÜÛœÙT™\Ý[Ò][S[Ù[X^
K›Ü[Û˜[

Kˆ›[Ù[Ü\˜[Y]\œÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›][˜ÞWÛ\ÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]]˜[X][Û‘]Z[Ô™\ÜÛœÙT™\Ý[Ò][S][˜ÞS\ÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]]˜[X][Û‘]Z[Ô™\ÜÛœÙT™\Ý[Ò][S][˜ÞS\ÓX^
K›Ü[Û˜[

Kˆ›Ü™×ÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›Ü™×Ý\Ù\—ÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Û\ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]]˜[X][Û‘]Z[Ô™\ÜÛœÙT™\Ý[Ò][T›Û\ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]]˜[X][Û‘]Z[Ô™\ÜÛœÙT™\Ý[Ò][T›Û\ÚÙ[œÓX^
K›Ü[Û˜[

Kˆ˜ÛÛ\][Û—ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]]˜[X][Û‘]Z[Ô™\ÜÛœÙT™\Ý[Ò][PÛÛ\][Û•ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]]˜[X][Û‘]Z[Ô™\ÜÛœÙT™\Ý[Ò][PÛÛ\][Û•ÚÙ[œÓX^
K›Ü[Û˜[

KˆÝ[ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]]˜[X][Û‘]Z[Ô™\ÜÛœÙT™\Ý[Ò][UÝ[ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]]˜[X][Û‘]Z[Ô™\ÜÛœÙT™\Ý[Ò][UÝ[ÚÙ[œÓX^
K›Ü[Û˜[

Kˆœ™\ÜÛœÙWÝ[YHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ™]˜[ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]]˜[X][Û‘]Z[Ô™\ÜÛœÙT™\Ý[Ò][Q]˜[YX^
K›Ü[Û˜[

Kˆ˜ÛÜÝŽˆ›Ù›[X™\Š
K›Ü[Û˜[

KˆœÝ]\ÈŽˆ›Ù™[[JÉÕS”ÑU	Ë	ÓÒÉË	ÑT”“Ô‰×JK›Ü[Û˜[

KˆœÝ]\×ÛY\ÜØYÙHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÜ[—Ù]™[ÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆœ›ÝšY\ˆŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]]˜[X][Û‘]Z[Ô™\ÜÛœÙT™\Ý[Ò][T›ÝšY\“X^
K›Ü[Û˜[

Kˆœ›ÝšY\—ÛÙÛÈŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆœÜ[—Ø]šX]\ÈŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ˜Ý\ÝÛWÙ]˜[ØÛÛ™šYÈŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ™]˜[ÜÝ]\ÈŽˆ›Ù™[[JÉÓ›ÝÝ\Y	Ë	Ô]Y]YY	Ë	Ô[›š[™ÉË	ÐÛÛ\]Y	Ë	ÑY][™ÉË	Ò[˜XÝ]™IË	Ñ˜Z[Y	Ë	Ô\X[[‰Ë	Ñ^\š[Y[]˜[X][Û‰Ë	Õ\ØY[™ÉË	Ô\X[^˜XÝY	Ë	Ô›ØÙ\ÜÚ[™ÉË	Ñ[][™ÉË	Ô\X[ÛÛ\]Y	Ë	ÓÜ[Z^˜][Û‘]˜[X][Û‰Ë	Ñ\œ›Ü‰Ë	ÐØ[˜Ù[Y	×JK›Ü[Û˜[

Kˆœ›Û\Ý™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJJBŸJB‚‚‹ÊŠ‚ˆ
ˆ™]Ú]H›ÜˆHØœÙ\™HÜ˜\Ú]Ü[Z^™Y]Y\šY\Âˆ
‹Â™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]Ü˜\Y]ÙÐ›ÙQš[\œÑY˜][H×NÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]Ü˜\Y]ÙÐ›ÙR[\˜[Y˜][H^XÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]Ü˜\Y]ÙÐ›ÙT›Ü\QY˜][H]™\˜YÙXÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]Ü˜\Y]ÙÐ›ÙHH›Ù›Øš™XÝ
Âˆœ›Ú™XÝÚYŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ™š[\œÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
Âˆ˜ÛÛ[[—ÚYŽˆ›ÙœÝš[™Ê
K™\ØÜšX™J	ÐÛÛ[[ˆÜˆ]šX]HYÈš[\ˆÛ‹‰ÊKˆ™\Ü^WÛ˜[YHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	ÓÜ[Û˜[RHX™[›ÜˆÚ\È[™Ø]™YšY]ÜË‰ÊKˆœÛÝ\˜ÙHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	ÓÜ[Û˜[ÛÝ\˜ÙHÝ\™˜XÙH›ÜˆZ^Y\ÛÝ\˜ÙHš[\œË›Üˆ^[\H˜XÙ\Ë]\Ù]ËÜˆÚ[][][Û‹‰ÊKˆ›Ý]]Ý\HŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	ÓÜ[Û˜[Y]šXÈÝ]]\HY]Y]H\ÙYžH]˜[[™[››Ý][Ûˆš[\œË‰ÊKˆ™š[\—ØÛÛ™šYÈŽˆ›Ù›Øš™XÝ
Âˆ™š[\—Ý\HŽˆ›ÙœÝš[™Ê
K™\ØÜšX™J	ÐØ[›ÛšXØ[šY[\K›Üˆ^[\H^[X™\‹›ÛÛX[‹]][YKØ]YÛÜšXØ[[XœË[››Ý]Ü‹Üˆ\œ˜^K‰ÊKˆ™š[\—ÛÜŽˆ›ÙœÝš[™Ê
K™\ØÜšX™J	ÐØ[›ÛšXØ[Ü\˜]Üˆœ›ÛH\WØÛÛ˜XÝ×Ùš[\—ØÛÛ˜XÝšœÛÛ‹›Üˆ^[\H\]X[Ë›ÝÙ\]X[Ë[‹›ÝÚ[‹™]ÙY[‹›ÝØ™]ÙY[‹\×Û[Üˆ\×Û›ÝÛ[‰ÊKˆ™š[\—Ý˜[YHŽˆ›Ù[šÛ›ÝÛŠ
K›Ü[Û˜[

K™\ØÜšX™J	ÔØØ[\‹\Ý˜[™ÙH\K›ÛÛX[‹Üˆ[\[™[™ÈÛˆš[\—ÛÜ[™š[\—Ý\K‰ÊKˆ˜ÛÛÝ\HŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	ÐÛÛ[[ˆ˜[Z[HÝXÚ\ÈÖTÕSWÓQU’PËÔS—ÐU’P•UKUSÓQU’PËS““ÕUSÓ‹Üˆ“Ô“PS‰ÊBŸJBŸJJK™Y˜][
˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]Ü˜\Y]ÙÐ›ÙQš[\œÑY˜][
Kˆš[\˜[Žˆ›Ù™[[JÉÚÝ\‰Ë	Ù^IË	ÝÙYZÉË	Û[Û	×JK™Y˜][
˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]Ü˜\Y]ÙÐ›ÙR[\˜[Y˜][
Kˆœ›Ü\HŽˆ›ÙœÝš[™Ê
K™Y˜][
˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]Ü˜\Y]ÙÐ›ÙT›Ü\QY˜][
Kˆœ™\WÙ]WØÛÛ™šYÈŽˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
Kˆ\HŽˆ›Ù™[[JÉÔÖTÕSWÓQU’PÉË	ÑUS	Ë	ÐS““ÕUSÓ‰×JKˆ›Ý]]Ý\HŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ™]˜[ÛÝ]]Ý\HŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ˜ÚÚXÙ\ÈŽˆ›Ù˜\œ˜^J›ÙœÝš[™Ê
JK›Ü[Û˜[

Kˆ˜[YHŽˆ›Ù[šÛ›ÝÛŠ
K›Ü[Û˜[

Kˆ™š[\—ÛÜŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ™š[\—Ý˜[YHŽˆ›Ù[šÛ›ÝÛŠ
K›Ü[Û˜[

BŸJBŸJB‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]Ü˜\Y]ÙÔ™\ÜÛœÙTÝ]\ÑY˜][HYNÂ‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]Ü˜\Y]ÙÔ™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
K™Y˜][
˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]Ü˜\Y]ÙÔ™\ÜÛœÙTÝ]\ÑY˜][
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆ›Y]šX×Û˜[YHŽˆ›ÙœÝš[™Ê
Kˆ™]HŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
Âˆ[Y\Ý[\Žˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ˜[YHŽˆ›Ù›[X™\Š
Kˆœš[X\žWÝ˜Y™šXÈŽˆ›Ù›[X™\Š
K›Ü[Û˜[

BŸJJBŸJBŸJB‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]ØœÙ\˜][Û”Ü[‘šY[Ô]Y\žT\˜[\ÈH›Ù›Øš™XÝ
ÂˆœYÙHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	ÐHYÙH[X™\ˆÚ][ˆHYÚ[˜]Y™\Ý[Ù]‰ÊKˆ›[Z]Žˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	Ó[X™\ˆÙˆ™\Ý[ÈÈ™]\›ˆ\ˆYÙK‰ÊBŸJB‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]ØœÙ\˜][Û”Ü[‘šY[Ô™\ÜÛœÙT™\Ý[Ò][T\™[Ü[’YX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]ØœÙ\˜][Û”Ü[‘šY[Ô™\ÜÛœÙT™\Ý[Ò][S˜[YSX^HŒÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]ØœÙ\˜][Û”Ü[‘šY[Ô™\ÜÛœÙT™\Ý[Ò][S[Ù[X^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]ØœÙ\˜][Û”Ü[‘šY[Ô™\ÜÛœÙT™\Ý[Ò][S][˜ÞS\ÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]ØœÙ\˜][Û”Ü[‘šY[Ô™\ÜÛœÙT™\Ý[Ò][S][˜ÞS\ÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]ØœÙ\˜][Û”Ü[‘šY[Ô™\ÜÛœÙT™\Ý[Ò][T›Û\ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]ØœÙ\˜][Û”Ü[‘šY[Ô™\ÜÛœÙT™\Ý[Ò][T›Û\ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]ØœÙ\˜][Û”Ü[‘šY[Ô™\ÜÛœÙT™\Ý[Ò][PÛÛ\][Û•ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]ØœÙ\˜][Û”Ü[‘šY[Ô™\ÜÛœÙT™\Ý[Ò][PÛÛ\][Û•ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]ØœÙ\˜][Û”Ü[‘šY[Ô™\ÜÛœÙT™\Ý[Ò][UÝ[ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]ØœÙ\˜][Û”Ü[‘šY[Ô™\ÜÛœÙT™\Ý[Ò][UÝ[ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]ØœÙ\˜][Û”Ü[‘šY[Ô™\ÜÛœÙT™\Ý[Ò][Q]˜[YX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]ØœÙ\˜][Û”Ü[‘šY[Ô™\ÜÛœÙT™\Ý[Ò][T›ÝšY\“X^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]ØœÙ\˜][Û”Ü[‘šY[Ô™\ÜÛœÙHH›Ù›Øš™XÝ
Âˆ˜ÛÝ[Žˆ›Ù›[X™\Š
Kˆ›™^Žˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™]š[Ý\ÈŽˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™\Ý[ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ›Ú™XÝÝ™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜XÙHŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ\™[ÜÜ[—ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]ØœÙ\˜][Û”Ü[‘šY[Ô™\ÜÛœÙT™\Ý[Ò][T\™[Ü[’YX^
K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]ØœÙ\˜][Û”Ü[‘šY[Ô™\ÜÛœÙT™\Ý[Ò][S˜[YSX^
Kˆ›ØœÙ\˜][Û—Ý\HŽˆ›Ù™[[JÉÝÛÛ	Ë	ØÚZ[‰Ë	ÛIË	Ü™]šY]™\‰Ë	Ù[X™Y[™ÉË	ØYÙ[	Ë	Ü™\˜[šÙ\‰Ë	Ý[šÛ›ÝÛ‰Ë	ÙÝX\™˜Z[	Ë	Ù]˜[X]Ü‰Ë	ØÛÛ™\œØ][Û‰×JKˆœÝ\Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™[™Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆš[œ]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Ý]]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›[Ù[Žˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]ØœÙ\˜][Û”Ü[‘šY[Ô™\ÜÛœÙT™\Ý[Ò][S[Ù[X^
K›Ü[Û˜[

Kˆ›[Ù[Ü\˜[Y]\œÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›][˜ÞWÛ\ÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]ØœÙ\˜][Û”Ü[‘šY[Ô™\ÜÛœÙT™\Ý[Ò][S][˜ÞS\ÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]ØœÙ\˜][Û”Ü[‘šY[Ô™\ÜÛœÙT™\Ý[Ò][S][˜ÞS\ÓX^
K›Ü[Û˜[

Kˆ›Ü™×ÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›Ü™×Ý\Ù\—ÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Û\ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]ØœÙ\˜][Û”Ü[‘šY[Ô™\ÜÛœÙT™\Ý[Ò][T›Û\ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]ØœÙ\˜][Û”Ü[‘šY[Ô™\ÜÛœÙT™\Ý[Ò][T›Û\ÚÙ[œÓX^
K›Ü[Û˜[

Kˆ˜ÛÛ\][Û—ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]ØœÙ\˜][Û”Ü[‘šY[Ô™\ÜÛœÙT™\Ý[Ò][PÛÛ\][Û•ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]ØœÙ\˜][Û”Ü[‘šY[Ô™\ÜÛœÙT™\Ý[Ò][PÛÛ\][Û•ÚÙ[œÓX^
K›Ü[Û˜[

KˆÝ[ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]ØœÙ\˜][Û”Ü[‘šY[Ô™\ÜÛœÙT™\Ý[Ò][UÝ[ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]ØœÙ\˜][Û”Ü[‘šY[Ô™\ÜÛœÙT™\Ý[Ò][UÝ[ÚÙ[œÓX^
K›Ü[Û˜[

Kˆœ™\ÜÛœÙWÝ[YHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ™]˜[ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]ØœÙ\˜][Û”Ü[‘šY[Ô™\ÜÛœÙT™\Ý[Ò][Q]˜[YX^
K›Ü[Û˜[

Kˆ˜ÛÜÝŽˆ›Ù›[X™\Š
K›Ü[Û˜[

KˆœÝ]\ÈŽˆ›Ù™[[JÉÕS”ÑU	Ë	ÓÒÉË	ÑT”“Ô‰×JK›Ü[Û˜[

KˆœÝ]\×ÛY\ÜØYÙHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÜ[—Ù]™[ÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆœ›ÝšY\ˆŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]ØœÙ\˜][Û”Ü[‘šY[Ô™\ÜÛœÙT™\Ý[Ò][T›ÝšY\“X^
K›Ü[Û˜[

Kˆœ›ÝšY\—ÛÙÛÈŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆœÜ[—Ø]šX]\ÈŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ˜Ý\ÝÛWÙ]˜[ØÛÛ™šYÈŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ™]˜[ÜÝ]\ÈŽˆ›Ù™[[JÉÓ›ÝÝ\Y	Ë	Ô]Y]YY	Ë	Ô[›š[™ÉË	ÐÛÛ\]Y	Ë	ÑY][™ÉË	Ò[˜XÝ]™IË	Ñ˜Z[Y	Ë	Ô\X[[‰Ë	Ñ^\š[Y[]˜[X][Û‰Ë	Õ\ØY[™ÉË	Ô\X[^˜XÝY	Ë	Ô›ØÙ\ÜÚ[™ÉË	Ñ[][™ÉË	Ô\X[ÛÛ\]Y	Ë	ÓÜ[Z^˜][Û‘]˜[X][Û‰Ë	Ñ\œ›Ü‰Ë	ÐØ[˜Ù[Y	×JK›Ü[Û˜[

Kˆœ›Û\Ý™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJJBŸJB‚‚‹ÊŠ‚ˆ
ˆ]Y\žH\˜[\Î‚ˆš[\œÎˆ”ÓÓˆÈœ›Ú™XÝÚYŽˆ]ZYˆŸH
™\]Z\™Y
Bˆ
ˆÝ[[X\žH\Ý[˜ÝÜ[—Ø]šX]\ÈÙ^\È›ÜˆH›Ú™XÝ
Ü[œÈÝ\™˜XÙJK‚ˆ
‹Â‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]Ü[]šX]\Ó\Ý]Y\žT›ÝÕ\QY˜][HÜ[œØÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]Ü[]šX]\Ó\Ý]Y\žT\˜[\ÈH›Ù›Øš™XÝ
ÂˆœYÙHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	ÐHYÙH[X™\ˆÚ][ˆHYÚ[˜]Y™\Ý[Ù]‰ÊKˆ›[Z]Žˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	Ó[X™\ˆÙˆ™\Ý[ÈÈ™]\›ˆ\ˆYÙK‰ÊKˆ™š[\œÈŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœ›Ý×Ý\HŽˆ›Ù™[[JÉÜÜ[œÉË	Ý˜XÙ\ÉË	ÜÙ\ÜÚ[ÛœÉË	Ý›ÚXÙPØ[É×JK™Y˜][
˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]Ü[]šX]\Ó\Ý]Y\žT›ÝÕ\QY˜][
BŸJB‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]Ü[]šX]\Ó\Ý™\ÜÛœÙTÝ]\ÑY˜][HYNÂ‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]Ü[]šX]\Ó\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
K™Y˜][
˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]Ü[]šX]\Ó\Ý™\ÜÛœÙTÝ]\ÑY˜][
Kˆœ™\Ý[Žˆ›Ù˜\œ˜^J›ÙœÝš[™Ê
K›Z[ŠJJBŸJB‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]Ü[œÑ^Ü]T]Y\žT\˜[\ÈH›Ù›Øš™XÝ
ÂˆœYÙHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	ÐHYÙH[X™\ˆÚ][ˆHYÚ[˜]Y™\Ý[Ù]‰ÊKˆ›[Z]Žˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	Ó[X™\ˆÙˆ™\Ý[ÈÈ™]\›ˆ\ˆYÙK‰ÊBŸJB‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]Ü[œÑ^Ü]T™\ÜÛœÙT™\Ý[Ò][T\™[Ü[’YX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]Ü[œÑ^Ü]T™\ÜÛœÙT™\Ý[Ò][S˜[YSX^HŒÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]Ü[œÑ^Ü]T™\ÜÛœÙT™\Ý[Ò][S[Ù[X^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]Ü[œÑ^Ü]T™\ÜÛœÙT™\Ý[Ò][S][˜ÞS\ÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]Ü[œÑ^Ü]T™\ÜÛœÙT™\Ý[Ò][S][˜ÞS\ÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]Ü[œÑ^Ü]T™\ÜÛœÙT™\Ý[Ò][T›Û\ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]Ü[œÑ^Ü]T™\ÜÛœÙT™\Ý[Ò][T›Û\ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]Ü[œÑ^Ü]T™\ÜÛœÙT™\Ý[Ò][PÛÛ\][Û•ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]Ü[œÑ^Ü]T™\ÜÛœÙT™\Ý[Ò][PÛÛ\][Û•ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]Ü[œÑ^Ü]T™\ÜÛœÙT™\Ý[Ò][UÝ[ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]Ü[œÑ^Ü]T™\ÜÛœÙT™\Ý[Ò][UÝ[ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]Ü[œÑ^Ü]T™\ÜÛœÙT™\Ý[Ò][Q]˜[YX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]Ü[œÑ^Ü]T™\ÜÛœÙT™\Ý[Ò][T›ÝšY\“X^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]Ü[œÑ^Ü]T™\ÜÛœÙHH›Ù›Øš™XÝ
Âˆ˜ÛÝ[Žˆ›Ù›[X™\Š
Kˆ›™^Žˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™]š[Ý\ÈŽˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™\Ý[ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ›Ú™XÝÝ™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜XÙHŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ\™[ÜÜ[—ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]Ü[œÑ^Ü]T™\ÜÛœÙT™\Ý[Ò][T\™[Ü[’YX^
K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]Ü[œÑ^Ü]T™\ÜÛœÙT™\Ý[Ò][S˜[YSX^
Kˆ›ØœÙ\˜][Û—Ý\HŽˆ›Ù™[[JÉÝÛÛ	Ë	ØÚZ[‰Ë	ÛIË	Ü™]šY]™\‰Ë	Ù[X™Y[™ÉË	ØYÙ[	Ë	Ü™\˜[šÙ\‰Ë	Ý[šÛ›ÝÛ‰Ë	ÙÝX\™˜Z[	Ë	Ù]˜[X]Ü‰Ë	ØÛÛ™\œØ][Û‰×JKˆœÝ\Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™[™Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆš[œ]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Ý]]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›[Ù[Žˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]Ü[œÑ^Ü]T™\ÜÛœÙT™\Ý[Ò][S[Ù[X^
K›Ü[Û˜[

Kˆ›[Ù[Ü\˜[Y]\œÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›][˜ÞWÛ\ÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]Ü[œÑ^Ü]T™\ÜÛœÙT™\Ý[Ò][S][˜ÞS\ÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]Ü[œÑ^Ü]T™\ÜÛœÙT™\Ý[Ò][S][˜ÞS\ÓX^
K›Ü[Û˜[

Kˆ›Ü™×ÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›Ü™×Ý\Ù\—ÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Û\ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]Ü[œÑ^Ü]T™\ÜÛœÙT™\Ý[Ò][T›Û\ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]Ü[œÑ^Ü]T™\ÜÛœÙT™\Ý[Ò][T›Û\ÚÙ[œÓX^
K›Ü[Û˜[

Kˆ˜ÛÛ\][Û—ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]Ü[œÑ^Ü]T™\ÜÛœÙT™\Ý[Ò][PÛÛ\][Û•ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]Ü[œÑ^Ü]T™\ÜÛœÙT™\Ý[Ò][PÛÛ\][Û•ÚÙ[œÓX^
K›Ü[Û˜[

KˆÝ[ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]Ü[œÑ^Ü]T™\ÜÛœÙT™\Ý[Ò][UÝ[ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]Ü[œÑ^Ü]T™\ÜÛœÙT™\Ý[Ò][UÝ[ÚÙ[œÓX^
K›Ü[Û˜[

Kˆœ™\ÜÛœÙWÝ[YHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ™]˜[ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]Ü[œÑ^Ü]T™\ÜÛœÙT™\Ý[Ò][Q]˜[YX^
K›Ü[Û˜[

Kˆ˜ÛÜÝŽˆ›Ù›[X™\Š
K›Ü[Û˜[

KˆœÝ]\ÈŽˆ›Ù™[[JÉÕS”ÑU	Ë	ÓÒÉË	ÑT”“Ô‰×JK›Ü[Û˜[

KˆœÝ]\×ÛY\ÜØYÙHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÜ[—Ù]™[ÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆœ›ÝšY\ˆŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]Ü[œÑ^Ü]T™\ÜÛœÙT™\Ý[Ò][T›ÝšY\“X^
K›Ü[Û˜[

Kˆœ›ÝšY\—ÛÙÛÈŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆœÜ[—Ø]šX]\ÈŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ˜Ý\ÝÛWÙ]˜[ØÛÛ™šYÈŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ™]˜[ÜÝ]\ÈŽˆ›Ù™[[JÉÓ›ÝÝ\Y	Ë	Ô]Y]YY	Ë	Ô[›š[™ÉË	ÐÛÛ\]Y	Ë	ÑY][™ÉË	Ò[˜XÝ]™IË	Ñ˜Z[Y	Ë	Ô\X[[‰Ë	Ñ^\š[Y[]˜[X][Û‰Ë	Õ\ØY[™ÉË	Ô\X[^˜XÝY	Ë	Ô›ØÙ\ÜÚ[™ÉË	Ñ[][™ÉË	Ô\X[ÛÛ\]Y	Ë	ÓÜ[Z^˜][Û‘]˜[X][Û‰Ë	Ñ\œ›Ü‰Ë	ÐØ[˜Ù[Y	×JK›Ü[Û˜[

Kˆœ›Û\Ý™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJJBŸJB‚‚‹ÊŠ‚ˆ
ˆÙ]H™]š[Ý\È[™™^Ü[ˆYžH[™^›Üˆ›Û‹[ØœÙ\™H›Ú™XÝË‚“Z\œ›ÜœÈH]Y\žKÙš[\ˆÙÚXÈÙˆ\ÝÜÜ[œË‚ˆ
‹Â‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\Ð˜\ÙT]Y\žQš[\œÑY˜][H×XÂ‚‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\Ð˜\ÙT]Y\žT\˜[\ÈH›Ù›Øš™XÝ
ÂˆœYÙHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	ÐHYÙH[X™\ˆÚ][ˆHYÚ[˜]Y™\Ý[Ù]‰ÊKˆ›[Z]Žˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	Ó[X™\ˆÙˆ™\Ý[ÈÈ™]\›ˆ\ˆYÙK‰ÊKˆœÜ[—ÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœ›Ú™XÝÝ™\œÚ[Û—ÚYŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ™š[\œÈŽˆ›ÙœÝš[™Ê
K›Z[ŠJK™Y˜][
˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\Ð˜\ÙT]Y\žQš[\œÑY˜][
BŸJB‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\Ð˜\ÙT™\ÜÛœÙT™\Ý[Ò][T\™[Ü[’YX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\Ð˜\ÙT™\ÜÛœÙT™\Ý[Ò][S˜[YSX^HŒÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\Ð˜\ÙT™\ÜÛœÙT™\Ý[Ò][S[Ù[X^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\Ð˜\ÙT™\ÜÛœÙT™\Ý[Ò][S][˜ÞS\ÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\Ð˜\ÙT™\ÜÛœÙT™\Ý[Ò][S][˜ÞS\ÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\Ð˜\ÙT™\ÜÛœÙT™\Ý[Ò][T›Û\ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\Ð˜\ÙT™\ÜÛœÙT™\Ý[Ò][T›Û\ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\Ð˜\ÙT™\ÜÛœÙT™\Ý[Ò][PÛÛ\][Û•ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\Ð˜\ÙT™\ÜÛœÙT™\Ý[Ò][PÛÛ\][Û•ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\Ð˜\ÙT™\ÜÛœÙT™\Ý[Ò][UÝ[ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\Ð˜\ÙT™\ÜÛœÙT™\Ý[Ò][UÝ[ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\Ð˜\ÙT™\ÜÛœÙT™\Ý[Ò][Q]˜[YX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\Ð˜\ÙT™\ÜÛœÙT™\Ý[Ò][T›ÝšY\“X^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\Ð˜\ÙT™\ÜÛœÙHH›Ù›Øš™XÝ
Âˆ˜ÛÝ[Žˆ›Ù›[X™\Š
Kˆ›™^Žˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™]š[Ý\ÈŽˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™\Ý[ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ›Ú™XÝÝ™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜XÙHŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ\™[ÜÜ[—ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\Ð˜\ÙT™\ÜÛœÙT™\Ý[Ò][T\™[Ü[’YX^
K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\Ð˜\ÙT™\ÜÛœÙT™\Ý[Ò][S˜[YSX^
Kˆ›ØœÙ\˜][Û—Ý\HŽˆ›Ù™[[JÉÝÛÛ	Ë	ØÚZ[‰Ë	ÛIË	Ü™]šY]™\‰Ë	Ù[X™Y[™ÉË	ØYÙ[	Ë	Ü™\˜[šÙ\‰Ë	Ý[šÛ›ÝÛ‰Ë	ÙÝX\™˜Z[	Ë	Ù]˜[X]Ü‰Ë	ØÛÛ™\œØ][Û‰×JKˆœÝ\Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™[™Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆš[œ]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Ý]]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›[Ù[Žˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\Ð˜\ÙT™\ÜÛœÙT™\Ý[Ò][S[Ù[X^
K›Ü[Û˜[

Kˆ›[Ù[Ü\˜[Y]\œÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›][˜ÞWÛ\ÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\Ð˜\ÙT™\ÜÛœÙT™\Ý[Ò][S][˜ÞS\ÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\Ð˜\ÙT™\ÜÛœÙT™\Ý[Ò][S][˜ÞS\ÓX^
K›Ü[Û˜[

Kˆ›Ü™×ÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›Ü™×Ý\Ù\—ÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Û\ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\Ð˜\ÙT™\ÜÛœÙT™\Ý[Ò][T›Û\ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\Ð˜\ÙT™\ÜÛœÙT™\Ý[Ò][T›Û\ÚÙ[œÓX^
K›Ü[Û˜[

Kˆ˜ÛÛ\][Û—ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\Ð˜\ÙT™\ÜÛœÙT™\Ý[Ò][PÛÛ\][Û•ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\Ð˜\ÙT™\ÜÛœÙT™\Ý[Ò][PÛÛ\][Û•ÚÙ[œÓX^
K›Ü[Û˜[

KˆÝ[ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\Ð˜\ÙT™\ÜÛœÙT™\Ý[Ò][UÝ[ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\Ð˜\ÙT™\ÜÛœÙT™\Ý[Ò][UÝ[ÚÙ[œÓX^
K›Ü[Û˜[

Kˆœ™\ÜÛœÙWÝ[YHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ™]˜[ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\Ð˜\ÙT™\ÜÛœÙT™\Ý[Ò][Q]˜[YX^
K›Ü[Û˜[

Kˆ˜ÛÜÝŽˆ›Ù›[X™\Š
K›Ü[Û˜[

KˆœÝ]\ÈŽˆ›Ù™[[JÉÕS”ÑU	Ë	ÓÒÉË	ÑT”“Ô‰×JK›Ü[Û˜[

KˆœÝ]\×ÛY\ÜØYÙHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÜ[—Ù]™[ÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆœ›ÝšY\ˆŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\Ð˜\ÙT™\ÜÛœÙT™\Ý[Ò][T›ÝšY\“X^
K›Ü[Û˜[

Kˆœ›ÝšY\—ÛÙÛÈŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆœÜ[—Ø]šX]\ÈŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ˜Ý\ÝÛWÙ]˜[ØÛÛ™šYÈŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ™]˜[ÜÝ]\ÈŽˆ›Ù™[[JÉÓ›ÝÝ\Y	Ë	Ô]Y]YY	Ë	Ô[›š[™ÉË	ÐÛÛ\]Y	Ë	ÑY][™ÉË	Ò[˜XÝ]™IË	Ñ˜Z[Y	Ë	Ô\X[[‰Ë	Ñ^\š[Y[]˜[X][Û‰Ë	Õ\ØY[™ÉË	Ô\X[^˜XÝY	Ë	Ô›ØÙ\ÜÚ[™ÉË	Ñ[][™ÉË	Ô\X[ÛÛ\]Y	Ë	ÓÜ[Z^˜][Û‘]˜[X][Û‰Ë	Ñ\œ›Ü‰Ë	ÐØ[˜Ù[Y	×JK›Ü[Û˜[

Kˆœ›Û\Ý™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJJBŸJB‚‚‹ÊŠ‚ˆ
ˆÙ]H™]š[Ý\È[™™^˜XÙHYžH[™^›ÜˆØœÙ\™H›Ú™XÝË‚“Z\œ›ÜœÈH]Y\žKÙš[\ˆÙÚXÈÙˆ\ÝÜÜ[œ×Ø\×ÛØœÙ\™K‚ˆ
‹Â‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\ÓØœÙ\™T]Y\žQš[\œÑY˜][H×XÂ‚‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\ÓØœÙ\™T]Y\žT\˜[\ÈH›Ù›Øš™XÝ
ÂˆœYÙHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	ÐHYÙH[X™\ˆÚ][ˆHYÚ[˜]Y™\Ý[Ù]‰ÊKˆ›[Z]Žˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	Ó[X™\ˆÙˆ™\Ý[ÈÈ™]\›ˆ\ˆYÙK‰ÊKˆœÜ[—ÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœ›Ú™XÝÚYŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ\Ù\—ÚYŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ™š[\œÈŽˆ›ÙœÝš[™Ê
K›Z[ŠJK™Y˜][
˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\ÓØœÙ\™T]Y\žQš[\œÑY˜][
BŸJB‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\ÓØœÙ\™T™\ÜÛœÙT™\Ý[Ò][T\™[Ü[’YX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\ÓØœÙ\™T™\ÜÛœÙT™\Ý[Ò][S˜[YSX^HŒÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\ÓØœÙ\™T™\ÜÛœÙT™\Ý[Ò][S[Ù[X^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\ÓØœÙ\™T™\ÜÛœÙT™\Ý[Ò][S][˜ÞS\ÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\ÓØœÙ\™T™\ÜÛœÙT™\Ý[Ò][S][˜ÞS\ÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\ÓØœÙ\™T™\ÜÛœÙT™\Ý[Ò][T›Û\ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\ÓØœÙ\™T™\ÜÛœÙT™\Ý[Ò][T›Û\ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\ÓØœÙ\™T™\ÜÛœÙT™\Ý[Ò][PÛÛ\][Û•ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\ÓØœÙ\™T™\ÜÛœÙT™\Ý[Ò][PÛÛ\][Û•ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\ÓØœÙ\™T™\ÜÛœÙT™\Ý[Ò][UÝ[ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\ÓØœÙ\™T™\ÜÛœÙT™\Ý[Ò][UÝ[ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\ÓØœÙ\™T™\ÜÛœÙT™\Ý[Ò][Q]˜[YX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\ÓØœÙ\™T™\ÜÛœÙT™\Ý[Ò][T›ÝšY\“X^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\ÓØœÙ\™T™\ÜÛœÙHH›Ù›Øš™XÝ
Âˆ˜ÛÝ[Žˆ›Ù›[X™\Š
Kˆ›™^Žˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™]š[Ý\ÈŽˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™\Ý[ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ›Ú™XÝÝ™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜XÙHŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ\™[ÜÜ[—ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\ÓØœÙ\™T™\ÜÛœÙT™\Ý[Ò][T\™[Ü[’YX^
K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\ÓØœÙ\™T™\ÜÛœÙT™\Ý[Ò][S˜[YSX^
Kˆ›ØœÙ\˜][Û—Ý\HŽˆ›Ù™[[JÉÝÛÛ	Ë	ØÚZ[‰Ë	ÛIË	Ü™]šY]™\‰Ë	Ù[X™Y[™ÉË	ØYÙ[	Ë	Ü™\˜[šÙ\‰Ë	Ý[šÛ›ÝÛ‰Ë	ÙÝX\™˜Z[	Ë	Ù]˜[X]Ü‰Ë	ØÛÛ™\œØ][Û‰×JKˆœÝ\Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™[™Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆš[œ]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Ý]]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›[Ù[Žˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\ÓØœÙ\™T™\ÜÛœÙT™\Ý[Ò][S[Ù[X^
K›Ü[Û˜[

Kˆ›[Ù[Ü\˜[Y]\œÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›][˜ÞWÛ\ÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\ÓØœÙ\™T™\ÜÛœÙT™\Ý[Ò][S][˜ÞS\ÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\ÓØœÙ\™T™\ÜÛœÙT™\Ý[Ò][S][˜ÞS\ÓX^
K›Ü[Û˜[

Kˆ›Ü™×ÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›Ü™×Ý\Ù\—ÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Û\ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\ÓØœÙ\™T™\ÜÛœÙT™\Ý[Ò][T›Û\ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\ÓØœÙ\™T™\ÜÛœÙT™\Ý[Ò][T›Û\ÚÙ[œÓX^
K›Ü[Û˜[

Kˆ˜ÛÛ\][Û—ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\ÓØœÙ\™T™\ÜÛœÙT™\Ý[Ò][PÛÛ\][Û•ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\ÓØœÙ\™T™\ÜÛœÙT™\Ý[Ò][PÛÛ\][Û•ÚÙ[œÓX^
K›Ü[Û˜[

KˆÝ[ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\ÓØœÙ\™T™\ÜÛœÙT™\Ý[Ò][UÝ[ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\ÓØœÙ\™T™\ÜÛœÙT™\Ý[Ò][UÝ[ÚÙ[œÓX^
K›Ü[Û˜[

Kˆœ™\ÜÛœÙWÝ[YHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ™]˜[ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\ÓØœÙ\™T™\ÜÛœÙT™\Ý[Ò][Q]˜[YX^
K›Ü[Û˜[

Kˆ˜ÛÜÝŽˆ›Ù›[X™\Š
K›Ü[Û˜[

KˆœÝ]\ÈŽˆ›Ù™[[JÉÕS”ÑU	Ë	ÓÒÉË	ÑT”“Ô‰×JK›Ü[Û˜[

KˆœÝ]\×ÛY\ÜØYÙHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÜ[—Ù]™[ÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆœ›ÝšY\ˆŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[‘Ù]˜XÙRYžR[™^Ü[œÐ\ÓØœÙ\™T™\ÜÛœÙT™\Ý[Ò][T›ÝšY\“X^
K›Ü[Û˜[

Kˆœ›ÝšY\—ÛÙÛÈŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆœÜ[—Ø]šX]\ÈŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ˜Ý\ÝÛWÙ]˜[ØÛÛ™šYÈŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ™]˜[ÜÝ]\ÈŽˆ›Ù™[[JÉÓ›ÝÝ\Y	Ë	Ô]Y]YY	Ë	Ô[›š[™ÉË	ÐÛÛ\]Y	Ë	ÑY][™ÉË	Ò[˜XÝ]™IË	Ñ˜Z[Y	Ë	Ô\X[[‰Ë	Ñ^\š[Y[]˜[X][Û‰Ë	Õ\ØY[™ÉË	Ô\X[^˜XÝY	Ë	Ô›ØÙ\ÜÚ[™ÉË	Ñ[][™ÉË	Ô\X[ÛÛ\]Y	Ë	ÓÜ[Z^˜][Û‘]˜[X][Û‰Ë	Ñ\œ›Ü‰Ë	ÐØ[˜Ù[Y	×JK›Ü[Û˜[

Kˆœ›Û\Ý™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJJBŸJB‚‚‹ÊŠ‚ˆ
ˆ\ÝÜ[œÈš[\™YžH›Ú™XÝQ[™›Ú™XÝ™\œÚ[ÛˆQÚ]Ü[Z^™Y]Y\šY\Ë‚ˆ
‹Â™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÔ]Y\žT\˜[\ÈH›Ù›Øš™XÝ
ÂˆœYÙHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	ÐHYÙH[X™\ˆÚ][ˆHYÚ[˜]Y™\Ý[Ù]‰ÊKˆ›[Z]Žˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	Ó[X™\ˆÙˆ™\Ý[ÈÈ™]\›ˆ\ˆYÙK‰ÊBŸJB‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÔ™\ÜÛœÙT™\Ý[Ò][T\™[Ü[’YX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÔ™\ÜÛœÙT™\Ý[Ò][S˜[YSX^HŒÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÔ™\ÜÛœÙT™\Ý[Ò][S[Ù[X^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÔ™\ÜÛœÙT™\Ý[Ò][S][˜ÞS\ÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÔ™\ÜÛœÙT™\Ý[Ò][S][˜ÞS\ÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÔ™\ÜÛœÙT™\Ý[Ò][T›Û\ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÔ™\ÜÛœÙT™\Ý[Ò][T›Û\ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÔ™\ÜÛœÙT™\Ý[Ò][PÛÛ\][Û•ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÔ™\ÜÛœÙT™\Ý[Ò][PÛÛ\][Û•ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÔ™\ÜÛœÙT™\Ý[Ò][UÝ[ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÔ™\ÜÛœÙT™\Ý[Ò][UÝ[ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÔ™\ÜÛœÙT™\Ý[Ò][Q]˜[YX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÔ™\ÜÛœÙT™\Ý[Ò][T›ÝšY\“X^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÔ™\ÜÛœÙHH›Ù›Øš™XÝ
Âˆ˜ÛÝ[Žˆ›Ù›[X™\Š
Kˆ›™^Žˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™]š[Ý\ÈŽˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™\Ý[ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ›Ú™XÝÝ™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜XÙHŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ\™[ÜÜ[—ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÔ™\ÜÛœÙT™\Ý[Ò][T\™[Ü[’YX^
K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÔ™\ÜÛœÙT™\Ý[Ò][S˜[YSX^
Kˆ›ØœÙ\˜][Û—Ý\HŽˆ›Ù™[[JÉÝÛÛ	Ë	ØÚZ[‰Ë	ÛIË	Ü™]šY]™\‰Ë	Ù[X™Y[™ÉË	ØYÙ[	Ë	Ü™\˜[šÙ\‰Ë	Ý[šÛ›ÝÛ‰Ë	ÙÝX\™˜Z[	Ë	Ù]˜[X]Ü‰Ë	ØÛÛ™\œØ][Û‰×JKˆœÝ\Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™[™Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆš[œ]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Ý]]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›[Ù[Žˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÔ™\ÜÛœÙT™\Ý[Ò][S[Ù[X^
K›Ü[Û˜[

Kˆ›[Ù[Ü\˜[Y]\œÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›][˜ÞWÛ\ÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÔ™\ÜÛœÙT™\Ý[Ò][S][˜ÞS\ÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÔ™\ÜÛœÙT™\Ý[Ò][S][˜ÞS\ÓX^
K›Ü[Û˜[

Kˆ›Ü™×ÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›Ü™×Ý\Ù\—ÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Û\ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÔ™\ÜÛœÙT™\Ý[Ò][T›Û\ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÔ™\ÜÛœÙT™\Ý[Ò][T›Û\ÚÙ[œÓX^
K›Ü[Û˜[

Kˆ˜ÛÛ\][Û—ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÔ™\ÜÛœÙT™\Ý[Ò][PÛÛ\][Û•ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÔ™\ÜÛœÙT™\Ý[Ò][PÛÛ\][Û•ÚÙ[œÓX^
K›Ü[Û˜[

KˆÝ[ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÔ™\ÜÛœÙT™\Ý[Ò][UÝ[ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÔ™\ÜÛœÙT™\Ý[Ò][UÝ[ÚÙ[œÓX^
K›Ü[Û˜[

Kˆœ™\ÜÛœÙWÝ[YHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ™]˜[ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÔ™\ÜÛœÙT™\Ý[Ò][Q]˜[YX^
K›Ü[Û˜[

Kˆ˜ÛÜÝŽˆ›Ù›[X™\Š
K›Ü[Û˜[

KˆœÝ]\ÈŽˆ›Ù™[[JÉÕS”ÑU	Ë	ÓÒÉË	ÑT”“Ô‰×JK›Ü[Û˜[

KˆœÝ]\×ÛY\ÜØYÙHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÜ[—Ù]™[ÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆœ›ÝšY\ˆŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÔ™\ÜÛœÙT™\Ý[Ò][T›ÝšY\“X^
K›Ü[Û˜[

Kˆœ›ÝšY\—ÛÙÛÈŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆœÜ[—Ø]šX]\ÈŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ˜Ý\ÝÛWÙ]˜[ØÛÛ™šYÈŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ™]˜[ÜÝ]\ÈŽˆ›Ù™[[JÉÓ›ÝÝ\Y	Ë	Ô]Y]YY	Ë	Ô[›š[™ÉË	ÐÛÛ\]Y	Ë	ÑY][™ÉË	Ò[˜XÝ]™IË	Ñ˜Z[Y	Ë	Ô\X[[‰Ë	Ñ^\š[Y[]˜[X][Û‰Ë	Õ\ØY[™ÉË	Ô\X[^˜XÝY	Ë	Ô›ØÙ\ÜÚ[™ÉË	Ñ[][™ÉË	Ô\X[ÛÛ\]Y	Ë	ÓÜ[Z^˜][Û‘]˜[X][Û‰Ë	Ñ\œ›Ü‰Ë	ÐØ[˜Ù[Y	×JK›Ü[Û˜[

Kˆœ›Û\Ý™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJJBŸJB‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÓØœÙ\™T]Y\žQš[\œÑY˜][H×XÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÓØœÙ\™T]Y\žTYÙS[X™\‘Y˜][HÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÓØœÙ\™T]Y\žTYÙS[X™\“Z[ˆHÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÓØœÙ\™T]Y\žTYÙTÚ^™QY˜][HÌÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÓØœÙ\™T]Y\žTYÙTÚ^™SX^HLÂ‚‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÓØœÙ\™T]Y\žT\˜[\ÈH›Ù›Øš™XÝ
ÂˆœYÙHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	ÐHYÙH[X™\ˆÚ][ˆHYÚ[˜]Y™\Ý[Ù]‰ÊKˆ›[Z]Žˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	Ó[X™\ˆÙˆ™\Ý[ÈÈ™]\›ˆ\ˆYÙK‰ÊKˆœ›Ú™XÝÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ\Ù\—ÚYŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ™š[\œÈŽˆ›ÙœÝš[™Ê
K›Z[ŠJK™Y˜][
˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÓØœÙ\™T]Y\žQš[\œÑY˜][
KˆœYÙWÛ[X™\ˆŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÓØœÙ\™T]Y\žTYÙS[X™\“Z[ŠK™Y˜][
˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÓØœÙ\™T]Y\žTYÙS[X™\‘Y˜][
KˆœYÙWÜÚ^™HŽˆ›Ù›[X™\Š
K›Z[ŠJK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÓØœÙ\™T]Y\žTYÙTÚ^™SX^
K™Y˜][
˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÓØœÙ\™T]Y\žTYÙTÚ^™QY˜][
BŸJB‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÓØœÙ\™T™\ÜÛœÙT™\Ý[Ò][T\™[Ü[’YX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÓØœÙ\™T™\ÜÛœÙT™\Ý[Ò][S˜[YSX^HŒÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÓØœÙ\™T™\ÜÛœÙT™\Ý[Ò][S[Ù[X^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÓØœÙ\™T™\ÜÛœÙT™\Ý[Ò][S][˜ÞS\ÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÓØœÙ\™T™\ÜÛœÙT™\Ý[Ò][S][˜ÞS\ÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÓØœÙ\™T™\ÜÛœÙT™\Ý[Ò][T›Û\ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÓØœÙ\™T™\ÜÛœÙT™\Ý[Ò][T›Û\ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÓØœÙ\™T™\ÜÛœÙT™\Ý[Ò][PÛÛ\][Û•ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÓØœÙ\™T™\ÜÛœÙT™\Ý[Ò][PÛÛ\][Û•ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÓØœÙ\™T™\ÜÛœÙT™\Ý[Ò][UÝ[ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÓØœÙ\™T™\ÜÛœÙT™\Ý[Ò][UÝ[ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÓØœÙ\™T™\ÜÛœÙT™\Ý[Ò][Q]˜[YX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÓØœÙ\™T™\ÜÛœÙT™\Ý[Ò][T›ÝšY\“X^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÓØœÙ\™T™\ÜÛœÙHH›Ù›Øš™XÝ
Âˆ˜ÛÝ[Žˆ›Ù›[X™\Š
Kˆ›™^Žˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™]š[Ý\ÈŽˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™\Ý[ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ›Ú™XÝÝ™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜XÙHŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ\™[ÜÜ[—ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÓØœÙ\™T™\ÜÛœÙT™\Ý[Ò][T\™[Ü[’YX^
K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÓØœÙ\™T™\ÜÛœÙT™\Ý[Ò][S˜[YSX^
Kˆ›ØœÙ\˜][Û—Ý\HŽˆ›Ù™[[JÉÝÛÛ	Ë	ØÚZ[‰Ë	ÛIË	Ü™]šY]™\‰Ë	Ù[X™Y[™ÉË	ØYÙ[	Ë	Ü™\˜[šÙ\‰Ë	Ý[šÛ›ÝÛ‰Ë	ÙÝX\™˜Z[	Ë	Ù]˜[X]Ü‰Ë	ØÛÛ™\œØ][Û‰×JKˆœÝ\Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™[™Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆš[œ]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Ý]]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›[Ù[Žˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÓØœÙ\™T™\ÜÛœÙT™\Ý[Ò][S[Ù[X^
K›Ü[Û˜[

Kˆ›[Ù[Ü\˜[Y]\œÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›][˜ÞWÛ\ÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÓØœÙ\™T™\ÜÛœÙT™\Ý[Ò][S][˜ÞS\ÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÓØœÙ\™T™\ÜÛœÙT™\Ý[Ò][S][˜ÞS\ÓX^
K›Ü[Û˜[

Kˆ›Ü™×ÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›Ü™×Ý\Ù\—ÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Û\ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÓØœÙ\™T™\ÜÛœÙT™\Ý[Ò][T›Û\ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÓØœÙ\™T™\ÜÛœÙT™\Ý[Ò][T›Û\ÚÙ[œÓX^
K›Ü[Û˜[

Kˆ˜ÛÛ\][Û—ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÓØœÙ\™T™\ÜÛœÙT™\Ý[Ò][PÛÛ\][Û•ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÓØœÙ\™T™\ÜÛœÙT™\Ý[Ò][PÛÛ\][Û•ÚÙ[œÓX^
K›Ü[Û˜[

KˆÝ[ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÓØœÙ\™T™\ÜÛœÙT™\Ý[Ò][UÝ[ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÓØœÙ\™T™\ÜÛœÙT™\Ý[Ò][UÝ[ÚÙ[œÓX^
K›Ü[Û˜[

Kˆœ™\ÜÛœÙWÝ[YHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ™]˜[ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÓØœÙ\™T™\ÜÛœÙT™\Ý[Ò][Q]˜[YX^
K›Ü[Û˜[

Kˆ˜ÛÜÝŽˆ›Ù›[X™\Š
K›Ü[Û˜[

KˆœÝ]\ÈŽˆ›Ù™[[JÉÕS”ÑU	Ë	ÓÒÉË	ÑT”“Ô‰×JK›Ü[Û˜[

KˆœÝ]\×ÛY\ÜØYÙHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÜ[—Ù]™[ÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆœ›ÝšY\ˆŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[“\ÝÜ[œÓØœÙ\™T™\ÜÛœÙT™\Ý[Ò][T›ÝšY\“X^
K›Ü[Û˜[

Kˆœ›ÝšY\—ÛÙÛÈŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆœÜ[—Ø]šX]\ÈŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ˜Ý\ÝÛWÙ]˜[ØÛÛ™šYÈŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ™]˜[ÜÝ]\ÈŽˆ›Ù™[[JÉÓ›ÝÝ\Y	Ë	Ô]Y]YY	Ë	Ô[›š[™ÉË	ÐÛÛ\]Y	Ë	ÑY][™ÉË	Ò[˜XÝ]™IË	Ñ˜Z[Y	Ë	Ô\X[[‰Ë	Ñ^\š[Y[]˜[X][Û‰Ë	Õ\ØY[™ÉË	Ô\X[^˜XÝY	Ë	Ô›ØÙ\ÜÚ[™ÉË	Ñ[][™ÉË	Ô\X[ÛÛ\]Y	Ë	ÓÜ[Z^˜][Û‘]˜[X][Û‰Ë	Ñ\œ›Ü‰Ë	ÐØ[˜Ù[Y	×JK›Ü[Û˜[

Kˆœ›Û\Ý™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJJBŸJB‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”™]šY]™SØY[™Ô]Y\žT\˜[\ÈH›Ù›Øš™XÝ
ÂˆœYÙHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	ÐHYÙH[X™\ˆÚ][ˆHYÚ[˜]Y™\Ý[Ù]‰ÊKˆ›[Z]Žˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	Ó[X™\ˆÙˆ™\Ý[ÈÈ™]\›ˆ\ˆYÙK‰ÊBŸJB‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”™]šY]™SØY[™Ô™\ÜÛœÙT™\Ý[Ò][T\™[Ü[’YX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”™]šY]™SØY[™Ô™\ÜÛœÙT™\Ý[Ò][S˜[YSX^HŒÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”™]šY]™SØY[™Ô™\ÜÛœÙT™\Ý[Ò][S[Ù[X^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”™]šY]™SØY[™Ô™\ÜÛœÙT™\Ý[Ò][S][˜ÞS\ÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”™]šY]™SØY[™Ô™\ÜÛœÙT™\Ý[Ò][S][˜ÞS\ÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”™]šY]™SØY[™Ô™\ÜÛœÙT™\Ý[Ò][T›Û\ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”™]šY]™SØY[™Ô™\ÜÛœÙT™\Ý[Ò][T›Û\ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”™]šY]™SØY[™Ô™\ÜÛœÙT™\Ý[Ò][PÛÛ\][Û•ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”™]šY]™SØY[™Ô™\ÜÛœÙT™\Ý[Ò][PÛÛ\][Û•ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”™]šY]™SØY[™Ô™\ÜÛœÙT™\Ý[Ò][UÝ[ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”™]šY]™SØY[™Ô™\ÜÛœÙT™\Ý[Ò][UÝ[ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”™]šY]™SØY[™Ô™\ÜÛœÙT™\Ý[Ò][Q]˜[YX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”™]šY]™SØY[™Ô™\ÜÛœÙT™\Ý[Ò][T›ÝšY\“X^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”™]šY]™SØY[™Ô™\ÜÛœÙHH›Ù›Øš™XÝ
Âˆ˜ÛÝ[Žˆ›Ù›[X™\Š
Kˆ›™^Žˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™]š[Ý\ÈŽˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™\Ý[ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ›Ú™XÝÝ™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜XÙHŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ\™[ÜÜ[—ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[”™]šY]™SØY[™Ô™\ÜÛœÙT™\Ý[Ò][T\™[Ü[’YX^
K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[”™]šY]™SØY[™Ô™\ÜÛœÙT™\Ý[Ò][S˜[YSX^
Kˆ›ØœÙ\˜][Û—Ý\HŽˆ›Ù™[[JÉÝÛÛ	Ë	ØÚZ[‰Ë	ÛIË	Ü™]šY]™\‰Ë	Ù[X™Y[™ÉË	ØYÙ[	Ë	Ü™\˜[šÙ\‰Ë	Ý[šÛ›ÝÛ‰Ë	ÙÝX\™˜Z[	Ë	Ù]˜[X]Ü‰Ë	ØÛÛ™\œØ][Û‰×JKˆœÝ\Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™[™Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆš[œ]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Ý]]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›[Ù[Žˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[”™]šY]™SØY[™Ô™\ÜÛœÙT™\Ý[Ò][S[Ù[X^
K›Ü[Û˜[

Kˆ›[Ù[Ü\˜[Y]\œÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›][˜ÞWÛ\ÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[”™]šY]™SØY[™Ô™\ÜÛœÙT™\Ý[Ò][S][˜ÞS\ÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[”™]šY]™SØY[™Ô™\ÜÛœÙT™\Ý[Ò][S][˜ÞS\ÓX^
K›Ü[Û˜[

Kˆ›Ü™×ÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›Ü™×Ý\Ù\—ÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Û\ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[”™]šY]™SØY[™Ô™\ÜÛœÙT™\Ý[Ò][T›Û\ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[”™]šY]™SØY[™Ô™\ÜÛœÙT™\Ý[Ò][T›Û\ÚÙ[œÓX^
K›Ü[Û˜[

Kˆ˜ÛÛ\][Û—ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[”™]šY]™SØY[™Ô™\ÜÛœÙT™\Ý[Ò][PÛÛ\][Û•ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[”™]šY]™SØY[™Ô™\ÜÛœÙT™\Ý[Ò][PÛÛ\][Û•ÚÙ[œÓX^
K›Ü[Û˜[

KˆÝ[ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[”™]šY]™SØY[™Ô™\ÜÛœÙT™\Ý[Ò][UÝ[ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[”™]šY]™SØY[™Ô™\ÜÛœÙT™\Ý[Ò][UÝ[ÚÙ[œÓX^
K›Ü[Û˜[

Kˆœ™\ÜÛœÙWÝ[YHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ™]˜[ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[”™]šY]™SØY[™Ô™\ÜÛœÙT™\Ý[Ò][Q]˜[YX^
K›Ü[Û˜[

Kˆ˜ÛÜÝŽˆ›Ù›[X™\Š
K›Ü[Û˜[

KˆœÝ]\ÈŽˆ›Ù™[[JÉÕS”ÑU	Ë	ÓÒÉË	ÑT”“Ô‰×JK›Ü[Û˜[

KˆœÝ]\×ÛY\ÜØYÙHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÜ[—Ù]™[ÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆœ›ÝšY\ˆŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[”™]šY]™SØY[™Ô™\ÜÛœÙT™\Ý[Ò][T›ÝšY\“X^
K›Ü[Û˜[

Kˆœ›ÝšY\—ÛÙÛÈŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆœÜ[—Ø]šX]\ÈŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ˜Ý\ÝÛWÙ]˜[ØÛÛ™šYÈŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ™]˜[ÜÝ]\ÈŽˆ›Ù™[[JÉÓ›ÝÝ\Y	Ë	Ô]Y]YY	Ë	Ô[›š[™ÉË	ÐÛÛ\]Y	Ë	ÑY][™ÉË	Ò[˜XÝ]™IË	Ñ˜Z[Y	Ë	Ô\X[[‰Ë	Ñ^\š[Y[]˜[X][Û‰Ë	Õ\ØY[™ÉË	Ô\X[^˜XÝY	Ë	Ô›ØÙ\ÜÚ[™ÉË	Ñ[][™ÉË	Ô\X[ÛÛ\]Y	Ë	ÓÜ[Z^˜][Û‘]˜[X][Û‰Ë	Ñ\œ›Ü‰Ë	ÐØ[˜Ù[Y	×JK›Ü[Û˜[

Kˆœ›Û\Ý™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJJBŸJB‚‚‹ÊŠ‚ˆ
ˆÚ]™[ˆH\ÝÙˆ˜XÙWÚYË™]\›ˆH›ÛÝÜ[ˆQ›ÜˆXXÚ˜XÙK‚”›ÛÝÜ[ˆHHÜ[ˆÚ\™H\™[ÜÜ[—ÚYTÈ•S›Üˆ]˜XÙK‚‚”]Y\žH\˜[\È
™\X]Y
Nˆ˜XÙWÚYÈ
™\]Z\™YÝ˜XÙWÚYÏOY‰˜XÙWÚYÏOYŠH
ÈÜ[Û˜[›Ú™XÝÚYÈ
[™\ÈHÒœØØ[ŠKˆ™\ÜÛœÙNˆÈœ™\Ý[ŽˆÈ˜XÙWÚYˆŽˆÜ[—ÚYˆ‹‹‹ˆHBˆ
‹Â‚‚‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”›ÛÝÜ[œÔ]Y\žT\˜[\ÈH›Ù›Øš™XÝ
ÂˆœYÙHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	ÐHYÙH[X™\ˆÚ][ˆHYÚ[˜]Y™\Ý[Ù]‰ÊKˆ›[Z]Žˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	Ó[X™\ˆÙˆ™\Ý[ÈÈ™]\›ˆ\ˆYÙK‰ÊKˆ˜XÙWÚYÈŽˆ›Ù˜\œ˜^J›ÙœÝš[™Ê
K›Z[ŠJJKˆœ›Ú™XÝÚYÈŽˆ›Ù˜\œ˜^J›ÙœÝš[™Ê
K›Z[ŠJJK›Ü[Û˜[

BŸJB‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”›ÛÝÜ[œÔ™\ÜÛœÙTÝ]\ÑY˜][HYNÂ‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”›ÛÝÜ[œÔ™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
K™Y˜][
˜XÙ\“ØœÙ\˜][Û”Ü[”›ÛÝÜ[œÔ™\ÜÛœÙTÝ]\ÑY˜][
Kˆœ™\Ý[Žˆ›Ùœ™XÛÜ™
›ÙœÝš[™Ê
K›ÙœÝš[™Ê
K›Z[ŠJJBŸJB‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”ÝX›Z]™YY˜XÚÐ›ÙT\™[Ü[’YX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”ÝX›Z]™YY˜XÚÐ›ÙS˜[YSX^HŒÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”ÝX›Z]™YY˜XÚÐ›ÙS[Ù[X^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”ÝX›Z]™YY˜XÚÐ›ÙS][˜ÞS\ÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”ÝX›Z]™YY˜XÚÐ›ÙS][˜ÞS\ÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”ÝX›Z]™YY˜XÚÐ›ÙT›Û\ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”ÝX›Z]™YY˜XÚÐ›ÙT›Û\ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”ÝX›Z]™YY˜XÚÐ›ÙPÛÛ\][Û•ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”ÝX›Z]™YY˜XÚÐ›ÙPÛÛ\][Û•ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”ÝX›Z]™YY˜XÚÐ›ÙUÝ[ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”ÝX›Z]™YY˜XÚÐ›ÙUÝ[ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”ÝX›Z]™YY˜XÚÐ›ÙQ]˜[YX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”ÝX›Z]™YY˜XÚÐ›ÙT›ÝšY\“X^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”ÝX›Z]™YY˜XÚÐ›ÙHH›Ù›Øš™XÝ
Âˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ›Ú™XÝÝ™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜XÙHŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ\™[ÜÜ[—ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[”ÝX›Z]™YY˜XÚÐ›ÙT\™[Ü[’YX^
K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[”ÝX›Z]™YY˜XÚÐ›ÙS˜[YSX^
Kˆ›ØœÙ\˜][Û—Ý\HŽˆ›Ù™[[JÉÝÛÛ	Ë	ØÚZ[‰Ë	ÛIË	Ü™]šY]™\‰Ë	Ù[X™Y[™ÉË	ØYÙ[	Ë	Ü™\˜[šÙ\‰Ë	Ý[šÛ›ÝÛ‰Ë	ÙÝX\™˜Z[	Ë	Ù]˜[X]Ü‰Ë	ØÛÛ™\œØ][Û‰×JKˆœÝ\Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™[™Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆš[œ]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Ý]]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›[Ù[Žˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[”ÝX›Z]™YY˜XÚÐ›ÙS[Ù[X^
K›Ü[Û˜[

Kˆ›[Ù[Ü\˜[Y]\œÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›][˜ÞWÛ\ÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[”ÝX›Z]™YY˜XÚÐ›ÙS][˜ÞS\ÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[”ÝX›Z]™YY˜XÚÐ›ÙS][˜ÞS\ÓX^
K›Ü[Û˜[

Kˆ›Ü™×ÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›Ü™×Ý\Ù\—ÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Û\ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[”ÝX›Z]™YY˜XÚÐ›ÙT›Û\ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[”ÝX›Z]™YY˜XÚÐ›ÙT›Û\ÚÙ[œÓX^
K›Ü[Û˜[

Kˆ˜ÛÛ\][Û—ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[”ÝX›Z]™YY˜XÚÐ›ÙPÛÛ\][Û•ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[”ÝX›Z]™YY˜XÚÐ›ÙPÛÛ\][Û•ÚÙ[œÓX^
K›Ü[Û˜[

KˆÝ[ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[”ÝX›Z]™YY˜XÚÐ›ÙUÝ[ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[”ÝX›Z]™YY˜XÚÐ›ÙUÝ[ÚÙ[œÓX^
K›Ü[Û˜[

Kˆœ™\ÜÛœÙWÝ[YHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ™]˜[ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[”ÝX›Z]™YY˜XÚÐ›ÙQ]˜[YX^
K›Ü[Û˜[

Kˆ˜ÛÜÝŽˆ›Ù›[X™\Š
K›Ü[Û˜[

KˆœÝ]\ÈŽˆ›Ù™[[JÉÕS”ÑU	Ë	ÓÒÉË	ÑT”“Ô‰×JK›Ü[Û˜[

KˆœÝ]\×ÛY\ÜØYÙHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÜ[—Ù]™[ÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆœ›ÝšY\ˆŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[”ÝX›Z]™YY˜XÚÐ›ÙT›ÝšY\“X^
K›Ü[Û˜[

Kˆ˜Ý\ÝÛWÙ]˜[ØÛÛ™šYÈŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ™]˜[ÜÝ]\ÈŽˆ›Ù™[[JÉÓ›ÝÝ\Y	Ë	Ô]Y]YY	Ë	Ô[›š[™ÉË	ÐÛÛ\]Y	Ë	ÑY][™ÉË	Ò[˜XÝ]™IË	Ñ˜Z[Y	Ë	Ô\X[[‰Ë	Ñ^\š[Y[]˜[X][Û‰Ë	Õ\ØY[™ÉË	Ô\X[^˜XÝY	Ë	Ô›ØÙ\ÜÚ[™ÉË	Ñ[][™ÉË	Ô\X[ÛÛ\]Y	Ë	ÓÜ[Z^˜][Û‘]˜[X][Û‰Ë	Ñ\œ›Ü‰Ë	ÐØ[˜Ù[Y	×JK›Ü[Û˜[

Kˆœ›Û\Ý™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJB‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”ÝX›Z]™YY˜XÚÐXÝ[Û•\P›ÙT\™[Ü[’YX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”ÝX›Z]™YY˜XÚÐXÝ[Û•\P›ÙS˜[YSX^HŒÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”ÝX›Z]™YY˜XÚÐXÝ[Û•\P›ÙS[Ù[X^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”ÝX›Z]™YY˜XÚÐXÝ[Û•\P›ÙS][˜ÞS\ÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”ÝX›Z]™YY˜XÚÐXÝ[Û•\P›ÙS][˜ÞS\ÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”ÝX›Z]™YY˜XÚÐXÝ[Û•\P›ÙT›Û\ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”ÝX›Z]™YY˜XÚÐXÝ[Û•\P›ÙT›Û\ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”ÝX›Z]™YY˜XÚÐXÝ[Û•\P›ÙPÛÛ\][Û•ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”ÝX›Z]™YY˜XÚÐXÝ[Û•\P›ÙPÛÛ\][Û•ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”ÝX›Z]™YY˜XÚÐXÝ[Û•\P›ÙUÝ[ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”ÝX›Z]™YY˜XÚÐXÝ[Û•\P›ÙUÝ[ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”ÝX›Z]™YY˜XÚÐXÝ[Û•\P›ÙQ]˜[YX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”ÝX›Z]™YY˜XÚÐXÝ[Û•\P›ÙT›ÝšY\“X^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”ÝX›Z]™YY˜XÚÐXÝ[Û•\P›ÙHH›Ù›Øš™XÝ
Âˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ›Ú™XÝÝ™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜XÙHŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ\™[ÜÜ[—ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[”ÝX›Z]™YY˜XÚÐXÝ[Û•\P›ÙT\™[Ü[’YX^
K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[”ÝX›Z]™YY˜XÚÐXÝ[Û•\P›ÙS˜[YSX^
Kˆ›ØœÙ\˜][Û—Ý\HŽˆ›Ù™[[JÉÝÛÛ	Ë	ØÚZ[‰Ë	ÛIË	Ü™]šY]™\‰Ë	Ù[X™Y[™ÉË	ØYÙ[	Ë	Ü™\˜[šÙ\‰Ë	Ý[šÛ›ÝÛ‰Ë	ÙÝX\™˜Z[	Ë	Ù]˜[X]Ü‰Ë	ØÛÛ™\œØ][Û‰×JKˆœÝ\Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™[™Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆš[œ]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Ý]]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›[Ù[Žˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[”ÝX›Z]™YY˜XÚÐXÝ[Û•\P›ÙS[Ù[X^
K›Ü[Û˜[

Kˆ›[Ù[Ü\˜[Y]\œÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›][˜ÞWÛ\ÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[”ÝX›Z]™YY˜XÚÐXÝ[Û•\P›ÙS][˜ÞS\ÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[”ÝX›Z]™YY˜XÚÐXÝ[Û•\P›ÙS][˜ÞS\ÓX^
K›Ü[Û˜[

Kˆ›Ü™×ÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›Ü™×Ý\Ù\—ÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Û\ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[”ÝX›Z]™YY˜XÚÐXÝ[Û•\P›ÙT›Û\ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[”ÝX›Z]™YY˜XÚÐXÝ[Û•\P›ÙT›Û\ÚÙ[œÓX^
K›Ü[Û˜[

Kˆ˜ÛÛ\][Û—ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[”ÝX›Z]™YY˜XÚÐXÝ[Û•\P›ÙPÛÛ\][Û•ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[”ÝX›Z]™YY˜XÚÐXÝ[Û•\P›ÙPÛÛ\][Û•ÚÙ[œÓX^
K›Ü[Û˜[

KˆÝ[ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[”ÝX›Z]™YY˜XÚÐXÝ[Û•\P›ÙUÝ[ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[”ÝX›Z]™YY˜XÚÐXÝ[Û•\P›ÙUÝ[ÚÙ[œÓX^
K›Ü[Û˜[

Kˆœ™\ÜÛœÙWÝ[YHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ™]˜[ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[”ÝX›Z]™YY˜XÚÐXÝ[Û•\P›ÙQ]˜[YX^
K›Ü[Û˜[

Kˆ˜ÛÜÝŽˆ›Ù›[X™\Š
K›Ü[Û˜[

KˆœÝ]\ÈŽˆ›Ù™[[JÉÕS”ÑU	Ë	ÓÒÉË	ÑT”“Ô‰×JK›Ü[Û˜[

KˆœÝ]\×ÛY\ÜØYÙHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÜ[—Ù]™[ÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆœ›ÝšY\ˆŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[”ÝX›Z]™YY˜XÚÐXÝ[Û•\P›ÙT›ÝšY\“X^
K›Ü[Û˜[

Kˆ˜Ý\ÝÛWÙ]˜[ØÛÛ™šYÈŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ™]˜[ÜÝ]\ÈŽˆ›Ù™[[JÉÓ›ÝÝ\Y	Ë	Ô]Y]YY	Ë	Ô[›š[™ÉË	ÐÛÛ\]Y	Ë	ÑY][™ÉË	Ò[˜XÝ]™IË	Ñ˜Z[Y	Ë	Ô\X[[‰Ë	Ñ^\š[Y[]˜[X][Û‰Ë	Õ\ØY[™ÉË	Ô\X[^˜XÝY	Ë	Ô›ØÙ\ÜÚ[™ÉË	Ñ[][™ÉË	Ô\X[ÛÛ\]Y	Ë	ÓÜ[Z^˜][Û‘]˜[X][Û‰Ë	Ñ\œ›Ü‰Ë	ÐØ[˜Ù[Y	×JK›Ü[Û˜[

Kˆœ›Û\Ý™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJB‚‚‹ÊŠ‚ˆ
ˆ\]HYÜÈ›Üˆ[ˆØœÙ\˜][ÛˆÜ[‹‚ˆ
‹Â™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[•\]UYÜÐ›ÙT\™[Ü[’YX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[•\]UYÜÐ›ÙS˜[YSX^HŒÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[•\]UYÜÐ›ÙS[Ù[X^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[•\]UYÜÐ›ÙS][˜ÞS\ÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[•\]UYÜÐ›ÙS][˜ÞS\ÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[•\]UYÜÐ›ÙT›Û\ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[•\]UYÜÐ›ÙT›Û\ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[•\]UYÜÐ›ÙPÛÛ\][Û•ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[•\]UYÜÐ›ÙPÛÛ\][Û•ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[•\]UYÜÐ›ÙUÝ[ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[•\]UYÜÐ›ÙUÝ[ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[•\]UYÜÐ›ÙQ]˜[YX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[•\]UYÜÐ›ÙT›ÝšY\“X^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[•\]UYÜÐ›ÙHH›Ù›Øš™XÝ
Âˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ›Ú™XÝÝ™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜XÙHŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ\™[ÜÜ[—ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[•\]UYÜÐ›ÙT\™[Ü[’YX^
K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[•\]UYÜÐ›ÙS˜[YSX^
Kˆ›ØœÙ\˜][Û—Ý\HŽˆ›Ù™[[JÉÝÛÛ	Ë	ØÚZ[‰Ë	ÛIË	Ü™]šY]™\‰Ë	Ù[X™Y[™ÉË	ØYÙ[	Ë	Ü™\˜[šÙ\‰Ë	Ý[šÛ›ÝÛ‰Ë	ÙÝX\™˜Z[	Ë	Ù]˜[X]Ü‰Ë	ØÛÛ™\œØ][Û‰×JKˆœÝ\Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™[™Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆš[œ]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Ý]]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›[Ù[Žˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[•\]UYÜÐ›ÙS[Ù[X^
K›Ü[Û˜[

Kˆ›[Ù[Ü\˜[Y]\œÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›][˜ÞWÛ\ÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[•\]UYÜÐ›ÙS][˜ÞS\ÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[•\]UYÜÐ›ÙS][˜ÞS\ÓX^
K›Ü[Û˜[

Kˆ›Ü™×ÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›Ü™×Ý\Ù\—ÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Û\ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[•\]UYÜÐ›ÙT›Û\ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[•\]UYÜÐ›ÙT›Û\ÚÙ[œÓX^
K›Ü[Û˜[

Kˆ˜ÛÛ\][Û—ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[•\]UYÜÐ›ÙPÛÛ\][Û•ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[•\]UYÜÐ›ÙPÛÛ\][Û•ÚÙ[œÓX^
K›Ü[Û˜[

KˆÝ[ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[•\]UYÜÐ›ÙUÝ[ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[•\]UYÜÐ›ÙUÝ[ÚÙ[œÓX^
K›Ü[Û˜[

Kˆœ™\ÜÛœÙWÝ[YHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ™]˜[ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[•\]UYÜÐ›ÙQ]˜[YX^
K›Ü[Û˜[

Kˆ˜ÛÜÝŽˆ›Ù›[X™\Š
K›Ü[Û˜[

KˆœÝ]\ÈŽˆ›Ù™[[JÉÕS”ÑU	Ë	ÓÒÉË	ÑT”“Ô‰×JK›Ü[Û˜[

KˆœÝ]\×ÛY\ÜØYÙHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÜ[—Ù]™[ÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆœ›ÝšY\ˆŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[•\]UYÜÐ›ÙT›ÝšY\“X^
K›Ü[Û˜[

Kˆ˜Ý\ÝÛWÙ]˜[ØÛÛ™šYÈŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ™]˜[ÜÝ]\ÈŽˆ›Ù™[[JÉÓ›ÝÝ\Y	Ë	Ô]Y]YY	Ë	Ô[›š[™ÉË	ÐÛÛ\]Y	Ë	ÑY][™ÉË	Ò[˜XÝ]™IË	Ñ˜Z[Y	Ë	Ô\X[[‰Ë	Ñ^\š[Y[]˜[X][Û‰Ë	Õ\ØY[™ÉË	Ô\X[^˜XÝY	Ë	Ô›ØÙ\ÜÚ[™ÉË	Ñ[][™ÉË	Ô\X[ÛÛ\]Y	Ë	ÓÜ[Z^˜][Û‘]˜[X][Û‰Ë	Ñ\œ›Ü‰Ë	ÐØ[˜Ù[Y	×JK›Ü[Û˜[

Kˆœ›Û\Ý™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJB‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”™XY\˜[\ÈH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
BŸJB‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”™XY™\ÜÛœÙT\™[Ü[’YX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”™XY™\ÜÛœÙS˜[YSX^HŒÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”™XY™\ÜÛœÙS[Ù[X^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”™XY™\ÜÛœÙS][˜ÞS\ÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”™XY™\ÜÛœÙS][˜ÞS\ÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”™XY™\ÜÛœÙT›Û\ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”™XY™\ÜÛœÙT›Û\ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”™XY™\ÜÛœÙPÛÛ\][Û•ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”™XY™\ÜÛœÙPÛÛ\][Û•ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”™XY™\ÜÛœÙUÝ[ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”™XY™\ÜÛœÙUÝ[ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”™XY™\ÜÛœÙQ]˜[YX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”™XY™\ÜÛœÙT›ÝšY\“X^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”™XY™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ›Ú™XÝÝ™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜XÙHŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ\™[ÜÜ[—ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[”™XY™\ÜÛœÙT\™[Ü[’YX^
K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[”™XY™\ÜÛœÙS˜[YSX^
Kˆ›ØœÙ\˜][Û—Ý\HŽˆ›Ù™[[JÉÝÛÛ	Ë	ØÚZ[‰Ë	ÛIË	Ü™]šY]™\‰Ë	Ù[X™Y[™ÉË	ØYÙ[	Ë	Ü™\˜[šÙ\‰Ë	Ý[šÛ›ÝÛ‰Ë	ÙÝX\™˜Z[	Ë	Ù]˜[X]Ü‰Ë	ØÛÛ™\œØ][Û‰×JKˆœÝ\Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™[™Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆš[œ]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Ý]]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›[Ù[Žˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[”™XY™\ÜÛœÙS[Ù[X^
K›Ü[Û˜[

Kˆ›[Ù[Ü\˜[Y]\œÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›][˜ÞWÛ\ÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[”™XY™\ÜÛœÙS][˜ÞS\ÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[”™XY™\ÜÛœÙS][˜ÞS\ÓX^
K›Ü[Û˜[

Kˆ›Ü™×ÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›Ü™×Ý\Ù\—ÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Û\ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[”™XY™\ÜÛœÙT›Û\ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[”™XY™\ÜÛœÙT›Û\ÚÙ[œÓX^
K›Ü[Û˜[

Kˆ˜ÛÛ\][Û—ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[”™XY™\ÜÛœÙPÛÛ\][Û•ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[”™XY™\ÜÛœÙPÛÛ\][Û•ÚÙ[œÓX^
K›Ü[Û˜[

KˆÝ[ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[”™XY™\ÜÛœÙUÝ[ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[”™XY™\ÜÛœÙUÝ[ÚÙ[œÓX^
K›Ü[Û˜[

Kˆœ™\ÜÛœÙWÝ[YHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ™]˜[ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[”™XY™\ÜÛœÙQ]˜[YX^
K›Ü[Û˜[

Kˆ˜ÛÜÝŽˆ›Ù›[X™\Š
K›Ü[Û˜[

KˆœÝ]\ÈŽˆ›Ù™[[JÉÕS”ÑU	Ë	ÓÒÉË	ÑT”“Ô‰×JK›Ü[Û˜[

KˆœÝ]\×ÛY\ÜØYÙHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÜ[—Ù]™[ÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆœ›ÝšY\ˆŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[”™XY™\ÜÛœÙT›ÝšY\“X^
K›Ü[Û˜[

Kˆœ›ÝšY\—ÛÙÛÈŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆœÜ[—Ø]šX]\ÈŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ˜Ý\ÝÛWÙ]˜[ØÛÛ™šYÈŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ™]˜[ÜÝ]\ÈŽˆ›Ù™[[JÉÓ›ÝÝ\Y	Ë	Ô]Y]YY	Ë	Ô[›š[™ÉË	ÐÛÛ\]Y	Ë	ÑY][™ÉË	Ò[˜XÝ]™IË	Ñ˜Z[Y	Ë	Ô\X[[‰Ë	Ñ^\š[Y[]˜[X][Û‰Ë	Õ\ØY[™ÉË	Ô\X[^˜XÝY	Ë	Ô›ØÙ\ÜÚ[™ÉË	Ñ[][™ÉË	Ô\X[ÛÛ\]Y	Ë	ÓÜ[Z^˜][Û‘]˜[X][Û‰Ë	Ñ\œ›Ü‰Ë	ÐØ[˜Ù[Y	×JK›Ü[Û˜[

Kˆœ›Û\Ý™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJB‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[•\]T\˜[\ÈH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
BŸJB‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[•\]P›ÙT\™[Ü[’YX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[•\]P›ÙS˜[YSX^HŒÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[•\]P›ÙS[Ù[X^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[•\]P›ÙS][˜ÞS\ÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[•\]P›ÙS][˜ÞS\ÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[•\]P›ÙT›Û\ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[•\]P›ÙT›Û\ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[•\]P›ÙPÛÛ\][Û•ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[•\]P›ÙPÛÛ\][Û•ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[•\]P›ÙUÝ[ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[•\]P›ÙUÝ[ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[•\]P›ÙQ]˜[YX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[•\]P›ÙT›ÝšY\“X^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[•\]P›ÙHH›Ù›Øš™XÝ
Âˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ›Ú™XÝÝ™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜XÙHŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ\™[ÜÜ[—ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[•\]P›ÙT\™[Ü[’YX^
K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[•\]P›ÙS˜[YSX^
Kˆ›ØœÙ\˜][Û—Ý\HŽˆ›Ù™[[JÉÝÛÛ	Ë	ØÚZ[‰Ë	ÛIË	Ü™]šY]™\‰Ë	Ù[X™Y[™ÉË	ØYÙ[	Ë	Ü™\˜[šÙ\‰Ë	Ý[šÛ›ÝÛ‰Ë	ÙÝX\™˜Z[	Ë	Ù]˜[X]Ü‰Ë	ØÛÛ™\œØ][Û‰×JKˆœÝ\Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™[™Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆš[œ]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Ý]]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›[Ù[Žˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[•\]P›ÙS[Ù[X^
K›Ü[Û˜[

Kˆ›[Ù[Ü\˜[Y]\œÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›][˜ÞWÛ\ÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[•\]P›ÙS][˜ÞS\ÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[•\]P›ÙS][˜ÞS\ÓX^
K›Ü[Û˜[

Kˆ›Ü™×ÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›Ü™×Ý\Ù\—ÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Û\ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[•\]P›ÙT›Û\ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[•\]P›ÙT›Û\ÚÙ[œÓX^
K›Ü[Û˜[

Kˆ˜ÛÛ\][Û—ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[•\]P›ÙPÛÛ\][Û•ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[•\]P›ÙPÛÛ\][Û•ÚÙ[œÓX^
K›Ü[Û˜[

KˆÝ[ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[•\]P›ÙUÝ[ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[•\]P›ÙUÝ[ÚÙ[œÓX^
K›Ü[Û˜[

Kˆœ™\ÜÛœÙWÝ[YHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ™]˜[ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[•\]P›ÙQ]˜[YX^
K›Ü[Û˜[

Kˆ˜ÛÜÝŽˆ›Ù›[X™\Š
K›Ü[Û˜[

KˆœÝ]\ÈŽˆ›Ù™[[JÉÕS”ÑU	Ë	ÓÒÉË	ÑT”“Ô‰×JK›Ü[Û˜[

KˆœÝ]\×ÛY\ÜØYÙHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÜ[—Ù]™[ÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆœ›ÝšY\ˆŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[•\]P›ÙT›ÝšY\“X^
K›Ü[Û˜[

Kˆ˜Ý\ÝÛWÙ]˜[ØÛÛ™šYÈŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ™]˜[ÜÝ]\ÈŽˆ›Ù™[[JÉÓ›ÝÝ\Y	Ë	Ô]Y]YY	Ë	Ô[›š[™ÉË	ÐÛÛ\]Y	Ë	ÑY][™ÉË	Ò[˜XÝ]™IË	Ñ˜Z[Y	Ë	Ô\X[[‰Ë	Ñ^\š[Y[]˜[X][Û‰Ë	Õ\ØY[™ÉË	Ô\X[^˜XÝY	Ë	Ô›ØÙ\ÜÚ[™ÉË	Ñ[][™ÉË	Ô\X[ÛÛ\]Y	Ë	ÓÜ[Z^˜][Û‘]˜[X][Û‰Ë	Ñ\œ›Ü‰Ë	ÐØ[˜Ù[Y	×JK›Ü[Û˜[

Kˆœ›Û\Ý™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJB‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[•\]T™\ÜÛœÙT\™[Ü[’YX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[•\]T™\ÜÛœÙS˜[YSX^HŒÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[•\]T™\ÜÛœÙS[Ù[X^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[•\]T™\ÜÛœÙS][˜ÞS\ÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[•\]T™\ÜÛœÙS][˜ÞS\ÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[•\]T™\ÜÛœÙT›Û\ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[•\]T™\ÜÛœÙT›Û\ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[•\]T™\ÜÛœÙPÛÛ\][Û•ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[•\]T™\ÜÛœÙPÛÛ\][Û•ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[•\]T™\ÜÛœÙUÝ[ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[•\]T™\ÜÛœÙUÝ[ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[•\]T™\ÜÛœÙQ]˜[YX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[•\]T™\ÜÛœÙT›ÝšY\“X^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[•\]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ›Ú™XÝÝ™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜XÙHŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ\™[ÜÜ[—ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[•\]T™\ÜÛœÙT\™[Ü[’YX^
K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[•\]T™\ÜÛœÙS˜[YSX^
Kˆ›ØœÙ\˜][Û—Ý\HŽˆ›Ù™[[JÉÝÛÛ	Ë	ØÚZ[‰Ë	ÛIË	Ü™]šY]™\‰Ë	Ù[X™Y[™ÉË	ØYÙ[	Ë	Ü™\˜[šÙ\‰Ë	Ý[šÛ›ÝÛ‰Ë	ÙÝX\™˜Z[	Ë	Ù]˜[X]Ü‰Ë	ØÛÛ™\œØ][Û‰×JKˆœÝ\Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™[™Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆš[œ]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Ý]]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›[Ù[Žˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[•\]T™\ÜÛœÙS[Ù[X^
K›Ü[Û˜[

Kˆ›[Ù[Ü\˜[Y]\œÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›][˜ÞWÛ\ÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[•\]T™\ÜÛœÙS][˜ÞS\ÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[•\]T™\ÜÛœÙS][˜ÞS\ÓX^
K›Ü[Û˜[

Kˆ›Ü™×ÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›Ü™×Ý\Ù\—ÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Û\ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[•\]T™\ÜÛœÙT›Û\ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[•\]T™\ÜÛœÙT›Û\ÚÙ[œÓX^
K›Ü[Û˜[

Kˆ˜ÛÛ\][Û—ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[•\]T™\ÜÛœÙPÛÛ\][Û•ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[•\]T™\ÜÛœÙPÛÛ\][Û•ÚÙ[œÓX^
K›Ü[Û˜[

KˆÝ[ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[•\]T™\ÜÛœÙUÝ[ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[•\]T™\ÜÛœÙUÝ[ÚÙ[œÓX^
K›Ü[Û˜[

Kˆœ™\ÜÛœÙWÝ[YHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ™]˜[ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[•\]T™\ÜÛœÙQ]˜[YX^
K›Ü[Û˜[

Kˆ˜ÛÜÝŽˆ›Ù›[X™\Š
K›Ü[Û˜[

KˆœÝ]\ÈŽˆ›Ù™[[JÉÕS”ÑU	Ë	ÓÒÉË	ÑT”“Ô‰×JK›Ü[Û˜[

KˆœÝ]\×ÛY\ÜØYÙHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÜ[—Ù]™[ÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆœ›ÝšY\ˆŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[•\]T™\ÜÛœÙT›ÝšY\“X^
K›Ü[Û˜[

Kˆœ›ÝšY\—ÛÙÛÈŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆœÜ[—Ø]šX]\ÈŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ˜Ý\ÝÛWÙ]˜[ØÛÛ™šYÈŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ™]˜[ÜÝ]\ÈŽˆ›Ù™[[JÉÓ›ÝÝ\Y	Ë	Ô]Y]YY	Ë	Ô[›š[™ÉË	ÐÛÛ\]Y	Ë	ÑY][™ÉË	Ò[˜XÝ]™IË	Ñ˜Z[Y	Ë	Ô\X[[‰Ë	Ñ^\š[Y[]˜[X][Û‰Ë	Õ\ØY[™ÉË	Ô\X[^˜XÝY	Ë	Ô›ØÙ\ÜÚ[™ÉË	Ñ[][™ÉË	Ô\X[ÛÛ\]Y	Ë	ÓÜ[Z^˜][Û‘]˜[X][Û‰Ë	Ñ\œ›Ü‰Ë	ÐØ[˜Ù[Y	×JK›Ü[Û˜[

Kˆœ›Û\Ý™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJB‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”\X[\]T\˜[\ÈH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
BŸJB‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”\X[\]P›ÙT\™[Ü[’YX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”\X[\]P›ÙS˜[YSX^HŒÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”\X[\]P›ÙS[Ù[X^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”\X[\]P›ÙS][˜ÞS\ÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”\X[\]P›ÙS][˜ÞS\ÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”\X[\]P›ÙT›Û\ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”\X[\]P›ÙT›Û\ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”\X[\]P›ÙPÛÛ\][Û•ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”\X[\]P›ÙPÛÛ\][Û•ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”\X[\]P›ÙUÝ[ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”\X[\]P›ÙUÝ[ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”\X[\]P›ÙQ]˜[YX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”\X[\]P›ÙT›ÝšY\“X^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”\X[\]P›ÙHH›Ù›Øš™XÝ
Âˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ›Ú™XÝÝ™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜XÙHŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ\™[ÜÜ[—ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[”\X[\]P›ÙT\™[Ü[’YX^
K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[”\X[\]P›ÙS˜[YSX^
Kˆ›ØœÙ\˜][Û—Ý\HŽˆ›Ù™[[JÉÝÛÛ	Ë	ØÚZ[‰Ë	ÛIË	Ü™]šY]™\‰Ë	Ù[X™Y[™ÉË	ØYÙ[	Ë	Ü™\˜[šÙ\‰Ë	Ý[šÛ›ÝÛ‰Ë	ÙÝX\™˜Z[	Ë	Ù]˜[X]Ü‰Ë	ØÛÛ™\œØ][Û‰×JKˆœÝ\Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™[™Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆš[œ]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Ý]]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›[Ù[Žˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[”\X[\]P›ÙS[Ù[X^
K›Ü[Û˜[

Kˆ›[Ù[Ü\˜[Y]\œÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›][˜ÞWÛ\ÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[”\X[\]P›ÙS][˜ÞS\ÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[”\X[\]P›ÙS][˜ÞS\ÓX^
K›Ü[Û˜[

Kˆ›Ü™×ÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›Ü™×Ý\Ù\—ÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Û\ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[”\X[\]P›ÙT›Û\ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[”\X[\]P›ÙT›Û\ÚÙ[œÓX^
K›Ü[Û˜[

Kˆ˜ÛÛ\][Û—ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[”\X[\]P›ÙPÛÛ\][Û•ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[”\X[\]P›ÙPÛÛ\][Û•ÚÙ[œÓX^
K›Ü[Û˜[

KˆÝ[ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[”\X[\]P›ÙUÝ[ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[”\X[\]P›ÙUÝ[ÚÙ[œÓX^
K›Ü[Û˜[

Kˆœ™\ÜÛœÙWÝ[YHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ™]˜[ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[”\X[\]P›ÙQ]˜[YX^
K›Ü[Û˜[

Kˆ˜ÛÜÝŽˆ›Ù›[X™\Š
K›Ü[Û˜[

KˆœÝ]\ÈŽˆ›Ù™[[JÉÕS”ÑU	Ë	ÓÒÉË	ÑT”“Ô‰×JK›Ü[Û˜[

KˆœÝ]\×ÛY\ÜØYÙHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÜ[—Ù]™[ÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆœ›ÝšY\ˆŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[”\X[\]P›ÙT›ÝšY\“X^
K›Ü[Û˜[

Kˆ˜Ý\ÝÛWÙ]˜[ØÛÛ™šYÈŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ™]˜[ÜÝ]\ÈŽˆ›Ù™[[JÉÓ›ÝÝ\Y	Ë	Ô]Y]YY	Ë	Ô[›š[™ÉË	ÐÛÛ\]Y	Ë	ÑY][™ÉË	Ò[˜XÝ]™IË	Ñ˜Z[Y	Ë	Ô\X[[‰Ë	Ñ^\š[Y[]˜[X][Û‰Ë	Õ\ØY[™ÉË	Ô\X[^˜XÝY	Ë	Ô›ØÙ\ÜÚ[™ÉË	Ñ[][™ÉË	Ô\X[ÛÛ\]Y	Ë	ÓÜ[Z^˜][Û‘]˜[X][Û‰Ë	Ñ\œ›Ü‰Ë	ÐØ[˜Ù[Y	×JK›Ü[Û˜[

Kˆœ›Û\Ý™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJB‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”\X[\]T™\ÜÛœÙT\™[Ü[’YX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”\X[\]T™\ÜÛœÙS˜[YSX^HŒÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”\X[\]T™\ÜÛœÙS[Ù[X^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”\X[\]T™\ÜÛœÙS][˜ÞS\ÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”\X[\]T™\ÜÛœÙS][˜ÞS\ÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”\X[\]T™\ÜÛœÙT›Û\ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”\X[\]T™\ÜÛœÙT›Û\ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”\X[\]T™\ÜÛœÙPÛÛ\][Û•ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”\X[\]T™\ÜÛœÙPÛÛ\][Û•ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”\X[\]T™\ÜÛœÙUÝ[ÚÙ[œÓZ[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”\X[\]T™\ÜÛœÙUÝ[ÚÙ[œÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”\X[\]T™\ÜÛœÙQ]˜[YX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”\X[\]T™\ÜÛœÙT›ÝšY\“X^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[”\X[\]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ›Ú™XÝÝ™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜XÙHŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ\™[ÜÜ[—ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[”\X[\]T™\ÜÛœÙT\™[Ü[’YX^
K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[”\X[\]T™\ÜÛœÙS˜[YSX^
Kˆ›ØœÙ\˜][Û—Ý\HŽˆ›Ù™[[JÉÝÛÛ	Ë	ØÚZ[‰Ë	ÛIË	Ü™]šY]™\‰Ë	Ù[X™Y[™ÉË	ØYÙ[	Ë	Ü™\˜[šÙ\‰Ë	Ý[šÛ›ÝÛ‰Ë	ÙÝX\™˜Z[	Ë	Ù]˜[X]Ü‰Ë	ØÛÛ™\œØ][Û‰×JKˆœÝ\Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™[™Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆš[œ]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Ý]]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›[Ù[Žˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[”\X[\]T™\ÜÛœÙS[Ù[X^
K›Ü[Û˜[

Kˆ›[Ù[Ü\˜[Y]\œÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›][˜ÞWÛ\ÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[”\X[\]T™\ÜÛœÙS][˜ÞS\ÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[”\X[\]T™\ÜÛœÙS][˜ÞS\ÓX^
K›Ü[Û˜[

Kˆ›Ü™×ÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›Ü™×Ý\Ù\—ÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Û\ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[”\X[\]T™\ÜÛœÙT›Û\ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[”\X[\]T™\ÜÛœÙT›Û\ÚÙ[œÓX^
K›Ü[Û˜[

Kˆ˜ÛÛ\][Û—ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[”\X[\]T™\ÜÛœÙPÛÛ\][Û•ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[”\X[\]T™\ÜÛœÙPÛÛ\][Û•ÚÙ[œÓX^
K›Ü[Û˜[

KˆÝ[ÝÚÙ[œÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\“ØœÙ\˜][Û”Ü[”\X[\]T™\ÜÛœÙUÝ[ÚÙ[œÓZ[ŠK›X^
˜XÙ\“ØœÙ\˜][Û”Ü[”\X[\]T™\ÜÛœÙUÝ[ÚÙ[œÓX^
K›Ü[Û˜[

Kˆœ™\ÜÛœÙWÝ[YHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ™]˜[ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[”\X[\]T™\ÜÛœÙQ]˜[YX^
K›Ü[Û˜[

Kˆ˜ÛÜÝŽˆ›Ù›[X™\Š
K›Ü[Û˜[

KˆœÝ]\ÈŽˆ›Ù™[[JÉÕS”ÑU	Ë	ÓÒÉË	ÑT”“Ô‰×JK›Ü[Û˜[

KˆœÝ]\×ÛY\ÜØYÙHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÜ[—Ù]™[ÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆœ›ÝšY\ˆŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\“ØœÙ\˜][Û”Ü[”\X[\]T™\ÜÛœÙT›ÝšY\“X^
K›Ü[Û˜[

Kˆœ›ÝšY\—ÛÙÛÈŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆœÜ[—Ø]šX]\ÈŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ˜Ý\ÝÛWÙ]˜[ØÛÛ™šYÈŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ™]˜[ÜÝ]\ÈŽˆ›Ù™[[JÉÓ›ÝÝ\Y	Ë	Ô]Y]YY	Ë	Ô[›š[™ÉË	ÐÛÛ\]Y	Ë	ÑY][™ÉË	Ò[˜XÝ]™IË	Ñ˜Z[Y	Ë	Ô\X[[‰Ë	Ñ^\š[Y[]˜[X][Û‰Ë	Õ\ØY[™ÉË	Ô\X[^˜XÝY	Ë	Ô›ØÙ\ÜÚ[™ÉË	Ñ[][™ÉË	Ô\X[ÛÛ\]Y	Ë	ÓÜ[Z^˜][Û‘]˜[X][Û‰Ë	Ñ\œ›Ü‰Ë	ÐØ[˜Ù[Y	×JK›Ü[Û˜[

Kˆœ›Û\Ý™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJB‚‚™^ÜÛÛœÝ˜XÙ\“ØœÙ\˜][Û”Ü[‘[]T\˜[\ÈH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
BŸJB‚‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ™\œÚ[Û“\Ý]Y\žT\˜[\ÈH›Ù›Øš™XÝ
ÂˆœYÙHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	ÐHYÙH[X™\ˆÚ][ˆHYÚ[˜]Y™\Ý[Ù]‰ÊKˆ›[Z]Žˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	Ó[X™\ˆÙˆ™\Ý[ÈÈ™]\›ˆ\ˆYÙK‰ÊBŸJB‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ™\œÚ[Û“\Ý™\ÜÛœÙT™\Ý[Ò][S˜[YSX^HMNÂ‚‚‚‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ™\œÚ[Û“\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
Âˆ˜ÛÝ[Žˆ›Ù›[X™\Š
Kˆ›™^Žˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™]š[Ý\ÈŽˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™\Ý[ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\”›Ú™XÝ™\œÚ[Û“\Ý™\ÜÛœÙT™\Ý[Ò][S˜[YSX^
Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÝ\Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™[™Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™\œ›ÜˆŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ™]˜[ÝYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ˜]™×Ù]˜[ÜØÛÜ™HŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ˜[››Ý][ÛœÈŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJJBŸJB‚‚‹ÊŠ‚ˆ
ˆÜ™X]HH™]È›Ú™XÝ™\œÚ[Û‹‚ˆ
‹Â™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ™\œÚ[ÛÜ™X]P›ÙS˜[YSX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ™\œÚ[ÛÜ™X]P›ÙHH›Ù›Øš™XÝ
Âˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\”›Ú™XÝ™\œÚ[ÛÜ™X]P›ÙS˜[YSX^
Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÝ\Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™[™Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™\œ›ÜˆŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ™]˜[ÝYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ˜]™×Ù]˜[ÜØÛÜ™HŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ˜[››Ý][ÛœÈŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJB‚‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ™\œÚ[ÛY[››Ý][ÛœÐ›ÙS˜[YSX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ™\œÚ[ÛY[››Ý][ÛœÐ›ÙHH›Ù›Øš™XÝ
Âˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\”›Ú™XÝ™\œÚ[ÛY[››Ý][ÛœÐ›ÙS˜[YSX^
Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÝ\Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™[™Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™\œ›ÜˆŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ™]˜[ÝYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ˜]™×Ù]˜[ÜØÛÜ™HŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ˜[››Ý][ÛœÈŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJB‚‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ™\œÚ[Û‘[]T[œÐ›ÙS˜[YSX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ™\œÚ[Û‘[]T[œÐ›ÙHH›Ù›Øš™XÝ
Âˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\”›Ú™XÝ™\œÚ[Û‘[]T[œÐ›ÙS˜[YSX^
Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÝ\Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™[™Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™\œ›ÜˆŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ™]˜[ÝYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ˜]™×Ù]˜[ÜØÛÜ™HŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ˜[››Ý][ÛœÈŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJB‚‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ™\œÚ[Û‘Ù]^Ü]P›ÙS˜[YSX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ™\œÚ[Û‘Ù]^Ü]P›ÙHH›Ù›Øš™XÝ
Âˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\”›Ú™XÝ™\œÚ[Û‘Ù]^Ü]P›ÙS˜[YSX^
Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÝ\Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™[™Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™\œ›ÜˆŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ™]˜[ÝYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ˜]™×Ù]˜[ÜØÛÜ™HŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ˜[››Ý][ÛœÈŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJB‚‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ™\œÚ[Û‘Ù]›Ú™XÝ™\œÚ[Û’YÔ]Y\žT\˜[\ÈH›Ù›Øš™XÝ
ÂˆœYÙHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	ÐHYÙH[X™\ˆÚ][ˆHYÚ[˜]Y™\Ý[Ù]‰ÊKˆ›[Z]Žˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	Ó[X™\ˆÙˆ™\Ý[ÈÈ™]\›ˆ\ˆYÙK‰ÊBŸJB‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ™\œÚ[Û‘Ù]›Ú™XÝ™\œÚ[Û’YÔ™\ÜÛœÙT™\Ý[Ò][S˜[YSX^HMNÂ‚‚‚‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ™\œÚ[Û‘Ù]›Ú™XÝ™\œÚ[Û’YÔ™\ÜÛœÙHH›Ù›Øš™XÝ
Âˆ˜ÛÝ[Žˆ›Ù›[X™\Š
Kˆ›™^Žˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™]š[Ý\ÈŽˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™\Ý[ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\”›Ú™XÝ™\œÚ[Û‘Ù]›Ú™XÝ™\œÚ[Û’YÔ™\ÜÛœÙT™\Ý[Ò][S˜[YSX^
Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÝ\Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™[™Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™\œ›ÜˆŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ™]˜[ÝYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ˜]™×Ù]˜[ÜØÛÜ™HŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ˜[››Ý][ÛœÈŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJJBŸJB‚‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ™\œÚ[Û‘Ù][’[œÚYÚÔ]Y\žT\˜[\ÈH›Ù›Øš™XÝ
ÂˆœYÙHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	ÐHYÙH[X™\ˆÚ][ˆHYÚ[˜]Y™\Ý[Ù]‰ÊKˆ›[Z]Žˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	Ó[X™\ˆÙˆ™\Ý[ÈÈ™]\›ˆ\ˆYÙK‰ÊBŸJB‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ™\œÚ[Û‘Ù][’[œÚYÚÔ™\ÜÛœÙT™\Ý[Ò][S˜[YSX^HMNÂ‚‚‚‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ™\œÚ[Û‘Ù][’[œÚYÚÔ™\ÜÛœÙHH›Ù›Øš™XÝ
Âˆ˜ÛÝ[Žˆ›Ù›[X™\Š
Kˆ›™^Žˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™]š[Ý\ÈŽˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™\Ý[ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\”›Ú™XÝ™\œÚ[Û‘Ù][’[œÚYÚÔ™\ÜÛœÙT™\Ý[Ò][S˜[YSX^
Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÝ\Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™[™Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™\œ›ÜˆŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ™]˜[ÝYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ˜]™×Ù]˜[ÜØÛÜ™HŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ˜[››Ý][ÛœÈŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJJBŸJB‚‚‹ÊŠ‚ˆ
ˆÙ]HYÚ[˜]Y\ÝÙˆ[›Ú™XÝÈ›ÜˆHÜ™Ø[š^˜][Û‹‚ˆ
‹Â™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ™\œÚ[Û“\Ý[œÔ]Y\žT\˜[\ÈH›Ù›Øš™XÝ
ÂˆœYÙHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	ÐHYÙH[X™\ˆÚ][ˆHYÚ[˜]Y™\Ý[Ù]‰ÊKˆ›[Z]Žˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	Ó[X™\ˆÙˆ™\Ý[ÈÈ™]\›ˆ\ˆYÙK‰ÊBŸJB‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ™\œÚ[Û“\Ý[œÔ™\ÜÛœÙT™\Ý[Ò][S˜[YSX^HMNÂ‚‚‚‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ™\œÚ[Û“\Ý[œÔ™\ÜÛœÙHH›Ù›Øš™XÝ
Âˆ˜ÛÝ[Žˆ›Ù›[X™\Š
Kˆ›™^Žˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™]š[Ý\ÈŽˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™\Ý[ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\”›Ú™XÝ™\œÚ[Û“\Ý[œÔ™\ÜÛœÙT™\Ý[Ò][S˜[YSX^
Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÝ\Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™[™Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™\œ›ÜˆŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ™]˜[ÝYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ˜]™×Ù]˜[ÜØÛÜ™HŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ˜[››Ý][ÛœÈŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJJBŸJB‚‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ™\œÚ[Û”›Ú™XÝ™\œÚ[Û•Ú[›™\›ÙS˜[YSX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ™\œÚ[Û”›Ú™XÝ™\œÚ[Û•Ú[›™\›ÙHH›Ù›Øš™XÝ
Âˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\”›Ú™XÝ™\œÚ[Û”›Ú™XÝ™\œÚ[Û•Ú[›™\›ÙS˜[YSX^
Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÝ\Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™[™Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™\œ›ÜˆŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ™]˜[ÝYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ˜]™×Ù]˜[ÜØÛÜ™HŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ˜[››Ý][ÛœÈŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJB‚‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ™\œÚ[Û•\]T›Ú™XÝ™\œÚ[ÛÛÛ™šYÐ›ÙS˜[YSX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ™\œÚ[Û•\]T›Ú™XÝ™\œÚ[ÛÛÛ™šYÐ›ÙHH›Ù›Øš™XÝ
Âˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\”›Ú™XÝ™\œÚ[Û•\]T›Ú™XÝ™\œÚ[ÛÛÛ™šYÐ›ÙS˜[YSX^
Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÝ\Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™[™Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™\œ›ÜˆŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ™]˜[ÝYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ˜]™×Ù]˜[ÜØÛÜ™HŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ˜[››Ý][ÛœÈŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJB‚‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ™\œÚ[Û”™XY\˜[\ÈH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
BŸJB‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ™\œÚ[Û”™XY™\ÜÛœÙS˜[YSX^HMNÂ‚‚‚‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ™\œÚ[Û”™XY™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\”›Ú™XÝ™\œÚ[Û”™XY™\ÜÛœÙS˜[YSX^
Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÝ\Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™[™Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™\œ›ÜˆŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ™]˜[ÝYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ˜]™×Ù]˜[ÜØÛÜ™HŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ˜[››Ý][ÛœÈŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJB‚‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ™\œÚ[Û•\]T\˜[\ÈH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
BŸJB‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ™\œÚ[Û•\]P›ÙS˜[YSX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ™\œÚ[Û•\]P›ÙHH›Ù›Øš™XÝ
Âˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\”›Ú™XÝ™\œÚ[Û•\]P›ÙS˜[YSX^
Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÝ\Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™[™Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™\œ›ÜˆŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ™]˜[ÝYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ˜]™×Ù]˜[ÜØÛÜ™HŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ˜[››Ý][ÛœÈŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJB‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ™\œÚ[Û•\]T™\ÜÛœÙS˜[YSX^HMNÂ‚‚‚‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ™\œÚ[Û•\]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\”›Ú™XÝ™\œÚ[Û•\]T™\ÜÛœÙS˜[YSX^
Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÝ\Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™[™Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™\œ›ÜˆŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ™]˜[ÝYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ˜]™×Ù]˜[ÜØÛÜ™HŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ˜[››Ý][ÛœÈŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJB‚‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ™\œÚ[Û”\X[\]T\˜[\ÈH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
BŸJB‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ™\œÚ[Û”\X[\]P›ÙS˜[YSX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ™\œÚ[Û”\X[\]P›ÙHH›Ù›Øš™XÝ
Âˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\”›Ú™XÝ™\œÚ[Û”\X[\]P›ÙS˜[YSX^
Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÝ\Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™[™Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™\œ›ÜˆŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ™]˜[ÝYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ˜]™×Ù]˜[ÜØÛÜ™HŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ˜[››Ý][ÛœÈŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJB‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ™\œÚ[Û”\X[\]T™\ÜÛœÙS˜[YSX^HMNÂ‚‚‚‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ™\œÚ[Û”\X[\]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\”›Ú™XÝ™\œÚ[Û”\X[\]T™\ÜÛœÙS˜[YSX^
Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÝ\Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™[™Ý[YHŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™\œ›ÜˆŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ™]˜[ÝYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ˜]™×Ù]˜[ÜØÛÜ™HŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ˜[››Ý][ÛœÈŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJB‚‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ™\œÚ[Û‘[]T\˜[\ÈH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
BŸJB‚‚‹ÊŠ‚ˆ
ˆÙ]HYÚ[˜]Y\ÝÙˆ[›Ú™XÝÈ›ÜˆHÜ™Ø[š^˜][Û‹‚ˆ
‹Â™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ\Ý]Y\žT\˜[\ÈH›Ù›Øš™XÝ
ÂˆœYÙHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	ÐHYÙH[X™\ˆÚ][ˆHYÚ[˜]Y™\Ý[Ù]‰ÊKˆ›[Z]Žˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	Ó[X™\ˆÙˆ™\Ý[ÈÈ™]\›ˆ\ˆYÙK‰ÊBŸJB‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ\Ý™\ÜÛœÙT™\Ý[Ò][S˜[YSX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
Âˆ˜ÛÝ[Žˆ›Ù›[X™\Š
Kˆ›™^Žˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™]š[Ý\ÈŽˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™\Ý[ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›[Ù[Ý\HŽˆ›Ù™[[JÉÓ[Y\šXÉË	ÔØÛÜ™PØ]YÛÜšXØ[	Ë	Ô˜[šÚ[™ÉË	Ðš[˜\žPÛ\ÜÚYšXØ][Û‰Ë	Ô™YÜ™\ÜÚ[Û‰Ë	ÓØš™XÝ]XÝ[Û‰Ë	ÔÙYÛY[][Û‰Ë	ÑÙ[™\˜]]™SIË	ÑÙ[™\˜]]™R[XYÙIË	ÑÙ[™\˜]]™UšY[ÉË	ÕÉË	ÔÕ	Ë	Ó][S[Ù[	×JKˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\”›Ú™XÝ\Ý™\ÜÛœÙT™\Ý[Ò][S˜[YSX^
Kˆ˜XÙWÝ\HŽˆ›Ù™[[JÉÙ^\š[Y[	Ë	ÛØœÙ\™I×JKˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Ü™Ø[š^˜][ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

KˆÛÜšÜÜXÙHŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ\]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ˜ÛÛ™šYÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ð[žH˜[Y”ÓÓˆ˜[YK‰ÊKˆœÛÝ\˜ÙHŽˆ›Ù™[[JÉÙ[[ÉË	Ü›ÝÝ\IË	ÜÚ[][]Ü‰×JK›Ü[Û˜[

KˆœÙ\ÜÚ[Û—ØÛÛ™šYÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ð[žH˜[Y”ÓÓˆ˜[YK‰ÊKˆYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ð[žH˜[Y”ÓÓˆ˜[YK‰ÊBŸJJBŸJB‚‚‹ÊŠ‚ˆ
ˆÜ™X]HH™]È›Ú™XÝ‚ˆ
‹Â™^ÜÛÛœÝ˜XÙ\”›Ú™XÝÜ™X]P›ÙS˜[YSX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝÜ™X]P›ÙHH›Ù›Øš™XÝ
Âˆ›[Ù[Ý\HŽˆ›Ù™[[JÉÓ[Y\šXÉË	ÔØÛÜ™PØ]YÛÜšXØ[	Ë	Ô˜[šÚ[™ÉË	Ðš[˜\žPÛ\ÜÚYšXØ][Û‰Ë	Ô™YÜ™\ÜÚ[Û‰Ë	ÓØš™XÝ]XÝ[Û‰Ë	ÔÙYÛY[][Û‰Ë	ÑÙ[™\˜]]™SIË	ÑÙ[™\˜]]™R[XYÙIË	ÑÙ[™\˜]]™UšY[ÉË	ÕÉË	ÔÕ	Ë	Ó][S[Ù[	×JKˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\”›Ú™XÝÜ™X]P›ÙS˜[YSX^
Kˆ˜XÙWÝ\HŽˆ›Ù™[[JÉÙ^\š[Y[	Ë	ÛØœÙ\™I×JKˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ˜ÛÛ™šYÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ð[žH˜[Y”ÓÓˆ˜[YK‰ÊKˆœÛÝ\˜ÙHŽˆ›Ù™[[JÉÙ[[ÉË	Ü›ÝÝ\IË	ÜÚ[][]Ü‰×JK›Ü[Û˜[

KˆœÙ\ÜÚ[Û—ØÛÛ™šYÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ð[žH˜[Y”ÓÓˆ˜[YK‰ÊKˆYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ð[žH˜[Y”ÓÓˆ˜[YK‰ÊBŸJB‚‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ™]ÚÞ\Ý[SY]šXÜÔ]Y\žT\˜[\ÈH›Ù›Øš™XÝ
ÂˆœYÙHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	ÐHYÙH[X™\ˆÚ][ˆHYÚ[˜]Y™\Ý[Ù]‰ÊKˆ›[Z]Žˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	Ó[X™\ˆÙˆ™\Ý[ÈÈ™]\›ˆ\ˆYÙK‰ÊBŸJB‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ™]ÚÞ\Ý[SY]šXÜÔ™\ÜÛœÙT™\Ý[Ò][S˜[YSX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ™]ÚÞ\Ý[SY]šXÜÔ™\ÜÛœÙHH›Ù›Øš™XÝ
Âˆ˜ÛÝ[Žˆ›Ù›[X™\Š
Kˆ›™^Žˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™]š[Ý\ÈŽˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™\Ý[ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›[Ù[Ý\HŽˆ›Ù™[[JÉÓ[Y\šXÉË	ÔØÛÜ™PØ]YÛÜšXØ[	Ë	Ô˜[šÚ[™ÉË	Ðš[˜\žPÛ\ÜÚYšXØ][Û‰Ë	Ô™YÜ™\ÜÚ[Û‰Ë	ÓØš™XÝ]XÝ[Û‰Ë	ÔÙYÛY[][Û‰Ë	ÑÙ[™\˜]]™SIË	ÑÙ[™\˜]]™R[XYÙIË	ÑÙ[™\˜]]™UšY[ÉË	ÕÉË	ÔÕ	Ë	Ó][S[Ù[	×JKˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\”›Ú™XÝ™]ÚÞ\Ý[SY]šXÜÔ™\ÜÛœÙT™\Ý[Ò][S˜[YSX^
Kˆ˜XÙWÝ\HŽˆ›Ù™[[JÉÙ^\š[Y[	Ë	ÛØœÙ\™I×JKˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Ü™Ø[š^˜][ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

KˆÛÜšÜÜXÙHŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ\]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ˜ÛÛ™šYÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ð[žH˜[Y”ÓÓˆ˜[YK‰ÊKˆœÛÝ\˜ÙHŽˆ›Ù™[[JÉÙ[[ÉË	Ü›ÝÝ\IË	ÜÚ[][]Ü‰×JK›Ü[Û˜[

KˆœÙ\ÜÚ[Û—ØÛÛ™šYÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ð[žH˜[Y”ÓÓˆ˜[YK‰ÊKˆYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ð[žH˜[Y”ÓÓˆ˜[YK‰ÊBŸJJBŸJB‚‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝÙ]Ü˜\]T]Y\žR[\˜[Y˜][HÝ\˜Â‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝÙ]Ü˜\]T]Y\žQš[\œÑY˜][H×XÂ‚‚‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝÙ]Ü˜\]T]Y\žT\˜[\ÈH›Ù›Øš™XÝ
ÂˆœYÙHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	ÐHYÙH[X™\ˆÚ][ˆHYÚ[˜]Y™\Ý[Ù]‰ÊKˆ›[Z]Žˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	Ó[X™\ˆÙˆ™\Ý[ÈÈ™]\›ˆ\ˆYÙK‰ÊKˆœ›Ú™XÝÚYŽˆ›ÙœÝš[™Ê
K]ZY

Kˆš[\˜[Žˆ›ÙœÝš[™Ê
K›Z[ŠJK™Y˜][
˜XÙ\”›Ú™XÝÙ]Ü˜\]T]Y\žR[\˜[Y˜][
Kˆ™š[\œÈŽˆ›ÙœÝš[™Ê
K›Z[ŠJK™Y˜][
˜XÙ\”›Ú™XÝÙ]Ü˜\]T]Y\žQš[\œÑY˜][
BŸJB‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝÙ]Ü˜\]T™\ÜÛœÙT™\Ý[Ò][S˜[YSX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝÙ]Ü˜\]T™\ÜÛœÙHH›Ù›Øš™XÝ
Âˆ˜ÛÝ[Žˆ›Ù›[X™\Š
Kˆ›™^Žˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™]š[Ý\ÈŽˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™\Ý[ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›[Ù[Ý\HŽˆ›Ù™[[JÉÓ[Y\šXÉË	ÔØÛÜ™PØ]YÛÜšXØ[	Ë	Ô˜[šÚ[™ÉË	Ðš[˜\žPÛ\ÜÚYšXØ][Û‰Ë	Ô™YÜ™\ÜÚ[Û‰Ë	ÓØš™XÝ]XÝ[Û‰Ë	ÔÙYÛY[][Û‰Ë	ÑÙ[™\˜]]™SIË	ÑÙ[™\˜]]™R[XYÙIË	ÑÙ[™\˜]]™UšY[ÉË	ÕÉË	ÔÕ	Ë	Ó][S[Ù[	×JKˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\”›Ú™XÝÙ]Ü˜\]T™\ÜÛœÙT™\Ý[Ò][S˜[YSX^
Kˆ˜XÙWÝ\HŽˆ›Ù™[[JÉÙ^\š[Y[	Ë	ÛØœÙ\™I×JKˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Ü™Ø[š^˜][ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

KˆÛÜšÜÜXÙHŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ\]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ˜ÛÛ™šYÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ð[žH˜[Y”ÓÓˆ˜[YK‰ÊKˆœÛÝ\˜ÙHŽˆ›Ù™[[JÉÙ[[ÉË	Ü›ÝÝ\IË	ÜÚ[][]Ü‰×JK›Ü[Û˜[

KˆœÙ\ÜÚ[Û—ØÛÛ™šYÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ð[žH˜[Y”ÓÓˆ˜[YK‰ÊKˆYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ð[žH˜[Y”ÓÓˆ˜[YK‰ÊBŸJJBŸJB‚‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝÙ]\Ù\‘Ü˜\]T]Y\žT\˜[\ÈH›Ù›Øš™XÝ
Âˆœ›Ú™XÝÚYŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ™[™Ý\Ù\—ÚYŽˆ›ÙœÝš[™Ê
K]ZY

BŸJB‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝÙ]\Ù\‘Ü˜\]P›ÙR[\˜[Y˜][HÝ\˜Â‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝÙ]\Ù\‘Ü˜\]P›ÙQš[\œÑY˜][H×NÂ‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝÙ]\Ù\‘Ü˜\]P›ÙHH›Ù›Øš™XÝ
Âˆš[\˜[Žˆ›ÙœÝš[™Ê
K›Z[ŠJK™Y˜][
˜XÙ\”›Ú™XÝÙ]\Ù\‘Ü˜\]P›ÙR[\˜[Y˜][
Kˆ™š[\œÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
Âˆ˜ÛÛ[[—ÚYŽˆ›ÙœÝš[™Ê
K™\ØÜšX™J	ÐÛÛ[[ˆÜˆ]šX]HYÈš[\ˆÛ‹‰ÊKˆ™\Ü^WÛ˜[YHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	ÓÜ[Û˜[RHX™[›ÜˆÚ\È[™Ø]™YšY]ÜË‰ÊKˆœÛÝ\˜ÙHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	ÓÜ[Û˜[ÛÝ\˜ÙHÝ\™˜XÙH›ÜˆZ^Y\ÛÝ\˜ÙHš[\œË›Üˆ^[\H˜XÙ\Ë]\Ù]ËÜˆÚ[][][Û‹‰ÊKˆ›Ý]]Ý\HŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	ÓÜ[Û˜[Y]šXÈÝ]]\HY]Y]H\ÙYžH]˜[[™[››Ý][Ûˆš[\œË‰ÊKˆ™š[\—ØÛÛ™šYÈŽˆ›Ù›Øš™XÝ
Âˆ™š[\—Ý\HŽˆ›ÙœÝš[™Ê
K™\ØÜšX™J	ÐØ[›ÛšXØ[šY[\K›Üˆ^[\H^[X™\‹›ÛÛX[‹]][YKØ]YÛÜšXØ[[XœË[››Ý]Ü‹Üˆ\œ˜^K‰ÊKˆ™š[\—ÛÜŽˆ›ÙœÝš[™Ê
K™\ØÜšX™J	ÐØ[›ÛšXØ[Ü\˜]Üˆœ›ÛH\WØÛÛ˜XÝ×Ùš[\—ØÛÛ˜XÝšœÛÛ‹›Üˆ^[\H\]X[Ë›ÝÙ\]X[Ë[‹›ÝÚ[‹™]ÙY[‹›ÝØ™]ÙY[‹\×Û[Üˆ\×Û›ÝÛ[‰ÊKˆ™š[\—Ý˜[YHŽˆ›Ù[šÛ›ÝÛŠ
K›Ü[Û˜[

K™\ØÜšX™J	ÔØØ[\‹\Ý˜[™ÙH\K›ÛÛX[‹Üˆ[\[™[™ÈÛˆš[\—ÛÜ[™š[\—Ý\K‰ÊKˆ˜ÛÛÝ\HŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	ÐÛÛ[[ˆ˜[Z[HÝXÚ\ÈÖTÕSWÓQU’PËÔS—ÐU’P•UKUSÓQU’PËS““ÕUSÓ‹Üˆ“Ô“PS‰ÊBŸJBŸJJK™Y˜][
˜XÙ\”›Ú™XÝÙ]\Ù\‘Ü˜\]P›ÙQš[\œÑY˜][
BŸJB‚‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝÙ]\Ù\“Y]šXÜÐ›ÙR[\˜[Y˜][H^XÂ‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝÙ]\Ù\“Y]šXÜÐ›ÙQš[\œÑY˜][H×NÂ‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝÙ]\Ù\“Y]šXÜÐ›ÙHH›Ù›Øš™XÝ
Âˆ™[™Ý\Ù\—ÚYŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ›Ú™XÝÚYŽˆ›ÙœÝš[™Ê
K]ZY

Kˆš[\˜[Žˆ›ÙœÝš[™Ê
K›Z[ŠJK™Y˜][
˜XÙ\”›Ú™XÝÙ]\Ù\“Y]šXÜÐ›ÙR[\˜[Y˜][
Kˆ™š[\œÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
Âˆ˜ÛÛ[[—ÚYŽˆ›ÙœÝš[™Ê
K™\ØÜšX™J	ÐÛÛ[[ˆÜˆ]šX]HYÈš[\ˆÛ‹‰ÊKˆ™\Ü^WÛ˜[YHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	ÓÜ[Û˜[RHX™[›ÜˆÚ\È[™Ø]™YšY]ÜË‰ÊKˆœÛÝ\˜ÙHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	ÓÜ[Û˜[ÛÝ\˜ÙHÝ\™˜XÙH›ÜˆZ^Y\ÛÝ\˜ÙHš[\œË›Üˆ^[\H˜XÙ\Ë]\Ù]ËÜˆÚ[][][Û‹‰ÊKˆ›Ý]]Ý\HŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	ÓÜ[Û˜[Y]šXÈÝ]]\HY]Y]H\ÙYžH]˜[[™[››Ý][Ûˆš[\œË‰ÊKˆ™š[\—ØÛÛ™šYÈŽˆ›Ù›Øš™XÝ
Âˆ™š[\—Ý\HŽˆ›ÙœÝš[™Ê
K™\ØÜšX™J	ÐØ[›ÛšXØ[šY[\K›Üˆ^[\H^[X™\‹›ÛÛX[‹]][YKØ]YÛÜšXØ[[XœË[››Ý]Ü‹Üˆ\œ˜^K‰ÊKˆ™š[\—ÛÜŽˆ›ÙœÝš[™Ê
K™\ØÜšX™J	ÐØ[›ÛšXØ[Ü\˜]Üˆœ›ÛH\WØÛÛ˜XÝ×Ùš[\—ØÛÛ˜XÝšœÛÛ‹›Üˆ^[\H\]X[Ë›ÝÙ\]X[Ë[‹›ÝÚ[‹™]ÙY[‹›ÝØ™]ÙY[‹\×Û[Üˆ\×Û›ÝÛ[‰ÊKˆ™š[\—Ý˜[YHŽˆ›Ù[šÛ›ÝÛŠ
K›Ü[Û˜[

K™\ØÜšX™J	ÔØØ[\‹\Ý˜[™ÙH\K›ÛÛX[‹Üˆ[\[™[™ÈÛˆš[\—ÛÜ[™š[\—Ý\K‰ÊKˆ˜ÛÛÝ\HŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	ÐÛÛ[[ˆ˜[Z[HÝXÚ\ÈÖTÕSWÓQU’PËÔS—ÐU’P•UKUSÓQU’PËS““ÕUSÓ‹Üˆ“Ô“PS‰ÊBŸJBŸJJK™Y˜][
˜XÙ\”›Ú™XÝÙ]\Ù\“Y]šXÜÐ›ÙQš[\œÑY˜][
BŸJB‚‚‹ÊŠ‚ˆ
ˆÝ\ÜÈÖTÕSWÓQU’PËUS[™S““ÕUSÓˆ\\Ë‚[Y]šXÜÈ\™HYÙÜ™YØ]Y]H\Ù\ˆ]™[‚ˆ
ˆÝ[[X\žH™]Ú[YK\Ù\šY\ÈYÙÜ™YØ]H\Ù\ˆY]šXÜÈ›ÜˆHØœÙ\™HÜ˜\‚ˆ
‹Â™^ÜÛÛœÝ˜XÙ\”›Ú™XÝÙ]\Ù\œÐYÙÜ™YØ]QÜ˜\]P›ÙR[\˜[Y˜][H^XÂ‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝÙ]\Ù\œÐYÙÜ™YØ]QÜ˜\]P›ÙQš[\œÑY˜][H×NÂ™^ÜÛÛœÝ˜XÙ\”›Ú™XÝÙ]\Ù\œÐYÙÜ™YØ]QÜ˜\]P›ÙT›Ü\QY˜][H]™\˜YÙXÂ‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝÙ]\Ù\œÐYÙÜ™YØ]QÜ˜\]P›ÙT™\Q]PÛÛ™šYÑY˜][HÈNÂ‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝÙ]\Ù\œÐYÙÜ™YØ]QÜ˜\]P›ÙHH›Ù›Øš™XÝ
Âˆœ›Ú™XÝÚYŽˆ›ÙœÝš[™Ê
K]ZY

Kˆš[\˜[Žˆ›ÙœÝš[™Ê
K›Z[ŠJK™Y˜][
˜XÙ\”›Ú™XÝÙ]\Ù\œÐYÙÜ™YØ]QÜ˜\]P›ÙR[\˜[Y˜][
Kˆ™š[\œÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
Âˆ˜ÛÛ[[—ÚYŽˆ›ÙœÝš[™Ê
K™\ØÜšX™J	ÐÛÛ[[ˆÜˆ]šX]HYÈš[\ˆÛ‹‰ÊKˆ™\Ü^WÛ˜[YHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	ÓÜ[Û˜[RHX™[›ÜˆÚ\È[™Ø]™YšY]ÜË‰ÊKˆœÛÝ\˜ÙHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	ÓÜ[Û˜[ÛÝ\˜ÙHÝ\™˜XÙH›ÜˆZ^Y\ÛÝ\˜ÙHš[\œË›Üˆ^[\H˜XÙ\Ë]\Ù]ËÜˆÚ[][][Û‹‰ÊKˆ›Ý]]Ý\HŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	ÓÜ[Û˜[Y]šXÈÝ]]\HY]Y]H\ÙYžH]˜[[™[››Ý][Ûˆš[\œË‰ÊKˆ™š[\—ØÛÛ™šYÈŽˆ›Ù›Øš™XÝ
Âˆ™š[\—Ý\HŽˆ›ÙœÝš[™Ê
K™\ØÜšX™J	ÐØ[›ÛšXØ[šY[\K›Üˆ^[\H^[X™\‹›ÛÛX[‹]][YKØ]YÛÜšXØ[[XœË[››Ý]Ü‹Üˆ\œ˜^K‰ÊKˆ™š[\—ÛÜŽˆ›ÙœÝš[™Ê
K™\ØÜšX™J	ÐØ[›ÛšXØ[Ü\˜]Üˆœ›ÛH\WØÛÛ˜XÝ×Ùš[\—ØÛÛ˜XÝšœÛÛ‹›Üˆ^[\H\]X[Ë›ÝÙ\]X[Ë[‹›ÝÚ[‹™]ÙY[‹›ÝØ™]ÙY[‹\×Û[Üˆ\×Û›ÝÛ[‰ÊKˆ™š[\—Ý˜[YHŽˆ›Ù[šÛ›ÝÛŠ
K›Ü[Û˜[

K™\ØÜšX™J	ÔØØ[\‹\Ý˜[™ÙH\K›ÛÛX[‹Üˆ[\[™[™ÈÛˆš[\—ÛÜ[™š[\—Ý\K‰ÊKˆ˜ÛÛÝ\HŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	ÐÛÛ[[ˆ˜[Z[HÝXÚ\ÈÖTÕSWÓQU’PËÔS—ÐU’P•UKUSÓQU’PËS““ÕUSÓ‹Üˆ“Ô“PS‰ÊBŸJBŸJJK™Y˜][
˜XÙ\”›Ú™XÝÙ]\Ù\œÐYÙÜ™YØ]QÜ˜\]P›ÙQš[\œÑY˜][
Kˆœ›Ü\HŽˆ›ÙœÝš[™Ê
K›Z[ŠJK™Y˜][
˜XÙ\”›Ú™XÝÙ]\Ù\œÐYÙÜ™YØ]QÜ˜\]P›ÙT›Ü\QY˜][
Kˆœ™\WÙ]WØÛÛ™šYÈŽˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
Kˆ\HŽˆ›Ù™[[JÉÔÖTÕSWÓQU’PÉË	ÑUS	Ë	ÐS““ÕUSÓ‰×JKˆ›Ý]]Ý\HŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ™]˜[ÛÝ]]Ý\HŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ˜ÚÚXÙ\ÈŽˆ›Ù˜\œ˜^J›ÙœÝš[™Ê
JK›Ü[Û˜[

Kˆ˜[YHŽˆ›Ù[šÛ›ÝÛŠ
K›Ü[Û˜[

Kˆ™š[\—ÛÜŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ™š[\—Ý˜[YHŽˆ›Ù[šÛ›ÝÛŠ
K›Ü[Û˜[

BŸJK™Y˜][
˜XÙ\”›Ú™XÝÙ]\Ù\œÐYÙÜ™YØ]QÜ˜\]P›ÙT™\Q]PÛÛ™šYÑY˜][
BŸJB‚‚‹ÊŠ‚ˆ
ˆ\Ý›Ú™XÝYÈ›ÜˆHÚ]™[ˆ›Ú™XÝ‚ˆ
‹Â™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ\Ý›Ú™XÝYÔ]Y\žT\˜[\ÈH›Ù›Øš™XÝ
ÂˆœYÙHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	ÐHYÙH[X™\ˆÚ][ˆHYÚ[˜]Y™\Ý[Ù]‰ÊKˆ›[Z]Žˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	Ó[X™\ˆÙˆ™\Ý[ÈÈ™]\›ˆ\ˆYÙK‰ÊBŸJB‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ\Ý›Ú™XÝYÔ™\ÜÛœÙTÝ]\ÑY˜][HYNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ\Ý›Ú™XÝYÔ™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
K™Y˜][
˜XÙ\”›Ú™XÝ\Ý›Ú™XÝYÔ™\ÜÛœÙTÝ]\ÑY˜][
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆœ›Ú™XÝÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ˜XÙWÝ\HŽˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJJBŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆ›Û[YHÛÝ[ÈÛÛYHœ›ÛHÛXÚÒÝ\ÙH
˜\Ý
H[œÝXYÙˆHÂ’“ÒSˆÛˆØœÙ\˜][Û—ÜÜ[œÈ
Ø\ÈLŠÈÙXÛÛ™ÊK‚ˆ
ˆÝ[[X\žH\Ý›Ú™XÝÈš[\™YžHÜ™Ø[š^˜][ÛˆQ‚ˆ
‹Â™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ\Ý›Ú™XÝÔ]Y\žT\˜[\ÈH›Ù›Øš™XÝ
ÂˆœYÙHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	ÐHYÙH[X™\ˆÚ][ˆHYÚ[˜]Y™\Ý[Ù]‰ÊKˆ›[Z]Žˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	Ó[X™\ˆÙˆ™\Ý[ÈÈ™]\›ˆ\ˆYÙK‰ÊBŸJB‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ\Ý›Ú™XÝÔ™\ÜÛœÙT™\Ý[Ò][S˜[YSX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ\Ý›Ú™XÝÔ™\ÜÛœÙHH›Ù›Øš™XÝ
Âˆ˜ÛÝ[Žˆ›Ù›[X™\Š
Kˆ›™^Žˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™]š[Ý\ÈŽˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™\Ý[ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›[Ù[Ý\HŽˆ›Ù™[[JÉÓ[Y\šXÉË	ÔØÛÜ™PØ]YÛÜšXØ[	Ë	Ô˜[šÚ[™ÉË	Ðš[˜\žPÛ\ÜÚYšXØ][Û‰Ë	Ô™YÜ™\ÜÚ[Û‰Ë	ÓØš™XÝ]XÝ[Û‰Ë	ÔÙYÛY[][Û‰Ë	ÑÙ[™\˜]]™SIË	ÑÙ[™\˜]]™R[XYÙIË	ÑÙ[™\˜]]™UšY[ÉË	ÕÉË	ÔÕ	Ë	Ó][S[Ù[	×JKˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\”›Ú™XÝ\Ý›Ú™XÝÔ™\ÜÛœÙT™\Ý[Ò][S˜[YSX^
Kˆ˜XÙWÝ\HŽˆ›Ù™[[JÉÙ^\š[Y[	Ë	ÛØœÙ\™I×JKˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Ü™Ø[š^˜][ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

KˆÛÜšÜÜXÙHŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ\]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ˜ÛÛ™šYÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ð[žH˜[Y”ÓÓˆ˜[YK‰ÊKˆœÛÝ\˜ÙHŽˆ›Ù™[[JÉÙ[[ÉË	Ü›ÝÝ\IË	ÜÚ[][]Ü‰×JK›Ü[Û˜[

KˆœÙ\ÜÚ[Û—ØÛÛ™šYÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ð[žH˜[Y”ÓÓˆ˜[YK‰ÊKˆYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ð[žH˜[Y”ÓÓˆ˜[YK‰ÊBŸJJBŸJB‚‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ›Ú™XÝÙÐÛÙT]Y\žT\˜[\ÈH›Ù›Øš™XÝ
ÂˆœYÙHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	ÐHYÙH[X™\ˆÚ][ˆHYÚ[˜]Y™\Ý[Ù]‰ÊKˆ›[Z]Žˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	Ó[X™\ˆÙˆ™\Ý[ÈÈ™]\›ˆ\ˆYÙK‰ÊBŸJB‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ›Ú™XÝÙÐÛÙT™\ÜÛœÙT™\Ý[Ò][S˜[YSX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ›Ú™XÝÙÐÛÙT™\ÜÛœÙHH›Ù›Øš™XÝ
Âˆ˜ÛÝ[Žˆ›Ù›[X™\Š
Kˆ›™^Žˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™]š[Ý\ÈŽˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™\Ý[ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›[Ù[Ý\HŽˆ›Ù™[[JÉÓ[Y\šXÉË	ÔØÛÜ™PØ]YÛÜšXØ[	Ë	Ô˜[šÚ[™ÉË	Ðš[˜\žPÛ\ÜÚYšXØ][Û‰Ë	Ô™YÜ™\ÜÚ[Û‰Ë	ÓØš™XÝ]XÝ[Û‰Ë	ÔÙYÛY[][Û‰Ë	ÑÙ[™\˜]]™SIË	ÑÙ[™\˜]]™R[XYÙIË	ÑÙ[™\˜]]™UšY[ÉË	ÕÉË	ÔÕ	Ë	Ó][S[Ù[	×JKˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\”›Ú™XÝ›Ú™XÝÙÐÛÙT™\ÜÛœÙT™\Ý[Ò][S˜[YSX^
Kˆ˜XÙWÝ\HŽˆ›Ù™[[JÉÙ^\š[Y[	Ë	ÛØœÙ\™I×JKˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Ü™Ø[š^˜][ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

KˆÛÜšÜÜXÙHŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ\]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ˜ÛÛ™šYÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ð[žH˜[Y”ÓÓˆ˜[YK‰ÊKˆœÛÝ\˜ÙHŽˆ›Ù™[[JÉÙ[[ÉË	Ü›ÝÝ\IË	ÜÚ[][]Ü‰×JK›Ü[Û˜[

KˆœÙ\ÜÚ[Û—ØÛÛ™šYÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ð[žH˜[Y”ÓÓˆ˜[YK‰ÊKˆYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ð[žH˜[Y”ÓÓˆ˜[YK‰ÊBŸJJBŸJB‚‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ\]T›Ú™XÝÛÛ™šYÐ›ÙS˜[YSX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ\]T›Ú™XÝÛÛ™šYÐ›ÙHH›Ù›Øš™XÝ
Âˆ›[Ù[Ý\HŽˆ›Ù™[[JÉÓ[Y\šXÉË	ÔØÛÜ™PØ]YÛÜšXØ[	Ë	Ô˜[šÚ[™ÉË	Ðš[˜\žPÛ\ÜÚYšXØ][Û‰Ë	Ô™YÜ™\ÜÚ[Û‰Ë	ÓØš™XÝ]XÝ[Û‰Ë	ÔÙYÛY[][Û‰Ë	ÑÙ[™\˜]]™SIË	ÑÙ[™\˜]]™R[XYÙIË	ÑÙ[™\˜]]™UšY[ÉË	ÕÉË	ÔÕ	Ë	Ó][S[Ù[	×JKˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\”›Ú™XÝ\]T›Ú™XÝÛÛ™šYÐ›ÙS˜[YSX^
Kˆ˜XÙWÝ\HŽˆ›Ù™[[JÉÙ^\š[Y[	Ë	ÛØœÙ\™I×JKˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ˜ÛÛ™šYÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ð[žH˜[Y”ÓÓˆ˜[YK‰ÊKˆœÛÝ\˜ÙHŽˆ›Ù™[[JÉÙ[[ÉË	Ü›ÝÝ\IË	ÜÚ[][]Ü‰×JK›Ü[Û˜[

KˆœÙ\ÜÚ[Û—ØÛÛ™šYÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ð[žH˜[Y”ÓÓˆ˜[YK‰ÊKˆYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ð[žH˜[Y”ÓÓˆ˜[YK‰ÊBŸJB‚‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ\]T›Ú™XÝ˜[YP›ÙS˜[YSX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ\]T›Ú™XÝ˜[YP›ÙHH›Ù›Øš™XÝ
Âˆ›[Ù[Ý\HŽˆ›Ù™[[JÉÓ[Y\šXÉË	ÔØÛÜ™PØ]YÛÜšXØ[	Ë	Ô˜[šÚ[™ÉË	Ðš[˜\žPÛ\ÜÚYšXØ][Û‰Ë	Ô™YÜ™\ÜÚ[Û‰Ë	ÓØš™XÝ]XÝ[Û‰Ë	ÔÙYÛY[][Û‰Ë	ÑÙ[™\˜]]™SIË	ÑÙ[™\˜]]™R[XYÙIË	ÑÙ[™\˜]]™UšY[ÉË	ÕÉË	ÔÕ	Ë	Ó][S[Ù[	×JKˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\”›Ú™XÝ\]T›Ú™XÝ˜[YP›ÙS˜[YSX^
Kˆ˜XÙWÝ\HŽˆ›Ù™[[JÉÙ^\š[Y[	Ë	ÛØœÙ\™I×JKˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ˜ÛÛ™šYÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ð[žH˜[Y”ÓÓˆ˜[YK‰ÊKˆœÛÝ\˜ÙHŽˆ›Ù™[[JÉÙ[[ÉË	Ü›ÝÝ\IË	ÜÚ[][]Ü‰×JK›Ü[Û˜[

KˆœÙ\ÜÚ[Û—ØÛÛ™šYÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ð[žH˜[Y”ÓÓˆ˜[YK‰ÊKˆYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ð[žH˜[Y”ÓÓˆ˜[YK‰ÊBŸJB‚‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ\]T›Ú™XÝÙ\ÜÚ[ÛÛÛ™šYÐ›ÙS˜[YSX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ\]T›Ú™XÝÙ\ÜÚ[ÛÛÛ™šYÐ›ÙHH›Ù›Øš™XÝ
Âˆ›[Ù[Ý\HŽˆ›Ù™[[JÉÓ[Y\šXÉË	ÔØÛÜ™PØ]YÛÜšXØ[	Ë	Ô˜[šÚ[™ÉË	Ðš[˜\žPÛ\ÜÚYšXØ][Û‰Ë	Ô™YÜ™\ÜÚ[Û‰Ë	ÓØš™XÝ]XÝ[Û‰Ë	ÔÙYÛY[][Û‰Ë	ÑÙ[™\˜]]™SIË	ÑÙ[™\˜]]™R[XYÙIË	ÑÙ[™\˜]]™UšY[ÉË	ÕÉË	ÔÕ	Ë	Ó][S[Ù[	×JKˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\”›Ú™XÝ\]T›Ú™XÝÙ\ÜÚ[ÛÛÛ™šYÐ›ÙS˜[YSX^
Kˆ˜XÙWÝ\HŽˆ›Ù™[[JÉÙ^\š[Y[	Ë	ÛØœÙ\™I×JKˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ˜ÛÛ™šYÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ð[žH˜[Y”ÓÓˆ˜[YK‰ÊKˆœÛÝ\˜ÙHŽˆ›Ù™[[JÉÙ[[ÉË	Ü›ÝÝ\IË	ÜÚ[][]Ü‰×JK›Ü[Û˜[

KˆœÙ\ÜÚ[Û—ØÛÛ™šYÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ð[žH˜[Y”ÓÓˆ˜[YK‰ÊKˆYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ð[žH˜[Y”ÓÓˆ˜[YK‰ÊBŸJB‚‚‹ÊŠ‚ˆ
ˆÙ]HÚ[™ÛH›Ú™XÝžHQÚ]Ø[\[™È˜]K‚ˆ
‹Â™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ™XY\˜[\ÈH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
BŸJB‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ™XY™\ÜÛœÙTÝ]\ÑY˜][HYNÂ™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ™XY™\ÜÛœÙT™\Ý[˜[YSX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ™XY™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
K™Y˜][
˜XÙ\”›Ú™XÝ™XY™\ÜÛœÙTÝ]\ÑY˜][
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›[Ù[Ý\HŽˆ›Ù™[[JÉÓ[Y\šXÉË	ÔØÛÜ™PØ]YÛÜšXØ[	Ë	Ô˜[šÚ[™ÉË	Ðš[˜\žPÛ\ÜÚYšXØ][Û‰Ë	Ô™YÜ™\ÜÚ[Û‰Ë	ÓØš™XÝ]XÝ[Û‰Ë	ÔÙYÛY[][Û‰Ë	ÑÙ[™\˜]]™SIË	ÑÙ[™\˜]]™R[XYÙIË	ÑÙ[™\˜]]™UšY[ÉË	ÕÉË	ÔÕ	Ë	Ó][S[Ù[	×JKˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\”›Ú™XÝ™XY™\ÜÛœÙT™\Ý[˜[YSX^
Kˆ˜XÙWÝ\HŽˆ›Ù™[[JÉÙ^\š[Y[	Ë	ÛØœÙ\™I×JKˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Ü™Ø[š^˜][ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

KˆÛÜšÜÜXÙHŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ\]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ˜ÛÛ™šYÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ð[žH˜[Y”ÓÓˆ˜[YK‰ÊKˆœÛÝ\˜ÙHŽˆ›Ù™[[JÉÙ[[ÉË	Ü›ÝÝ\IË	ÜÚ[][]Ü‰×JK›Ü[Û˜[

KˆœÙ\ÜÚ[Û—ØÛÛ™šYÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ð[žH˜[Y”ÓÓˆ˜[YK‰ÊKˆYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ð[žH˜[Y”ÓÓˆ˜[YK‰ÊKˆœØ[\[™×Ü˜]HŽˆ›Ù›[X™\Š
BŸJBŸJB‚‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ\]T\˜[\ÈH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
BŸJB‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ\]P›ÙS˜[YSX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ\]P›ÙHH›Ù›Øš™XÝ
Âˆ›[Ù[Ý\HŽˆ›Ù™[[JÉÓ[Y\šXÉË	ÔØÛÜ™PØ]YÛÜšXØ[	Ë	Ô˜[šÚ[™ÉË	Ðš[˜\žPÛ\ÜÚYšXØ][Û‰Ë	Ô™YÜ™\ÜÚ[Û‰Ë	ÓØš™XÝ]XÝ[Û‰Ë	ÔÙYÛY[][Û‰Ë	ÑÙ[™\˜]]™SIË	ÑÙ[™\˜]]™R[XYÙIË	ÑÙ[™\˜]]™UšY[ÉË	ÕÉË	ÔÕ	Ë	Ó][S[Ù[	×JKˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\”›Ú™XÝ\]P›ÙS˜[YSX^
Kˆ˜XÙWÝ\HŽˆ›Ù™[[JÉÙ^\š[Y[	Ë	ÛØœÙ\™I×JKˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ˜ÛÛ™šYÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ð[žH˜[Y”ÓÓˆ˜[YK‰ÊKˆœÛÝ\˜ÙHŽˆ›Ù™[[JÉÙ[[ÉË	Ü›ÝÝ\IË	ÜÚ[][]Ü‰×JK›Ü[Û˜[

KˆœÙ\ÜÚ[Û—ØÛÛ™šYÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ð[žH˜[Y”ÓÓˆ˜[YK‰ÊKˆYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ð[žH˜[Y”ÓÓˆ˜[YK‰ÊBŸJB‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ\]T™\ÜÛœÙS˜[YSX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ\]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›[Ù[Ý\HŽˆ›Ù™[[JÉÓ[Y\šXÉË	ÔØÛÜ™PØ]YÛÜšXØ[	Ë	Ô˜[šÚ[™ÉË	Ðš[˜\žPÛ\ÜÚYšXØ][Û‰Ë	Ô™YÜ™\ÜÚ[Û‰Ë	ÓØš™XÝ]XÝ[Û‰Ë	ÔÙYÛY[][Û‰Ë	ÑÙ[™\˜]]™SIË	ÑÙ[™\˜]]™R[XYÙIË	ÑÙ[™\˜]]™UšY[ÉË	ÕÉË	ÔÕ	Ë	Ó][S[Ù[	×JKˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\”›Ú™XÝ\]T™\ÜÛœÙS˜[YSX^
Kˆ˜XÙWÝ\HŽˆ›Ù™[[JÉÙ^\š[Y[	Ë	ÛØœÙ\™I×JKˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Ü™Ø[š^˜][ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

KˆÛÜšÜÜXÙHŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ\]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ˜ÛÛ™šYÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ð[žH˜[Y”ÓÓˆ˜[YK‰ÊKˆœÛÝ\˜ÙHŽˆ›Ù™[[JÉÙ[[ÉË	Ü›ÝÝ\IË	ÜÚ[][]Ü‰×JK›Ü[Û˜[

KˆœÙ\ÜÚ[Û—ØÛÛ™šYÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ð[žH˜[Y”ÓÓˆ˜[YK‰ÊKˆYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ð[žH˜[Y”ÓÓˆ˜[YK‰ÊBŸJB‚‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ\X[\]T\˜[\ÈH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
BŸJB‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ\X[\]P›ÙS˜[YSX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ\X[\]P›ÙHH›Ù›Øš™XÝ
Âˆ›[Ù[Ý\HŽˆ›Ù™[[JÉÓ[Y\šXÉË	ÔØÛÜ™PØ]YÛÜšXØ[	Ë	Ô˜[šÚ[™ÉË	Ðš[˜\žPÛ\ÜÚYšXØ][Û‰Ë	Ô™YÜ™\ÜÚ[Û‰Ë	ÓØš™XÝ]XÝ[Û‰Ë	ÔÙYÛY[][Û‰Ë	ÑÙ[™\˜]]™SIË	ÑÙ[™\˜]]™R[XYÙIË	ÑÙ[™\˜]]™UšY[ÉË	ÕÉË	ÔÕ	Ë	Ó][S[Ù[	×JKˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\”›Ú™XÝ\X[\]P›ÙS˜[YSX^
Kˆ˜XÙWÝ\HŽˆ›Ù™[[JÉÙ^\š[Y[	Ë	ÛØœÙ\™I×JKˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ˜ÛÛ™šYÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ð[žH˜[Y”ÓÓˆ˜[YK‰ÊKˆœÛÝ\˜ÙHŽˆ›Ù™[[JÉÙ[[ÉË	Ü›ÝÝ\IË	ÜÚ[][]Ü‰×JK›Ü[Û˜[

KˆœÙ\ÜÚ[Û—ØÛÛ™šYÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ð[žH˜[Y”ÓÓˆ˜[YK‰ÊKˆYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ð[žH˜[Y”ÓÓˆ˜[YK‰ÊBŸJB‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ\X[\]T™\ÜÛœÙS˜[YSX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ\X[\]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›[Ù[Ý\HŽˆ›Ù™[[JÉÓ[Y\šXÉË	ÔØÛÜ™PØ]YÛÜšXØ[	Ë	Ô˜[šÚ[™ÉË	Ðš[˜\žPÛ\ÜÚYšXØ][Û‰Ë	Ô™YÜ™\ÜÚ[Û‰Ë	ÓØš™XÝ]XÝ[Û‰Ë	ÔÙYÛY[][Û‰Ë	ÑÙ[™\˜]]™SIË	ÑÙ[™\˜]]™R[XYÙIË	ÑÙ[™\˜]]™UšY[ÉË	ÕÉË	ÔÕ	Ë	Ó][S[Ù[	×JKˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\”›Ú™XÝ\X[\]T™\ÜÛœÙS˜[YSX^
Kˆ˜XÙWÝ\HŽˆ›Ù™[[JÉÙ^\š[Y[	Ë	ÛØœÙ\™I×JKˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Ü™Ø[š^˜][ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

KˆÛÜšÜÜXÙHŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ\]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ˜ÛÛ™šYÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ð[žH˜[Y”ÓÓˆ˜[YK‰ÊKˆœÛÝ\˜ÙHŽˆ›Ù™[[JÉÙ[[ÉË	Ü›ÝÝ\IË	ÜÚ[][]Ü‰×JK›Ü[Û˜[

KˆœÙ\ÜÚ[Û—ØÛÛ™šYÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ð[žH˜[Y”ÓÓˆ˜[YK‰ÊKˆYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ð[žH˜[Y”ÓÓˆ˜[YK‰ÊBŸJB‚‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ[]T\˜[\ÈH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
BŸJB‚‚‹ÊŠ‚ˆ
ˆ\]HYÜÈ›ÜˆH›Ú™XÝ‚ˆ
‹Â™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ\]UYÜÔ\˜[\ÈH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
BŸJB‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ\]UYÜÐ›ÙS˜[YSX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ\]UYÜÐ›ÙHH›Ù›Øš™XÝ
Âˆ›[Ù[Ý\HŽˆ›Ù™[[JÉÓ[Y\šXÉË	ÔØÛÜ™PØ]YÛÜšXØ[	Ë	Ô˜[šÚ[™ÉË	Ðš[˜\žPÛ\ÜÚYšXØ][Û‰Ë	Ô™YÜ™\ÜÚ[Û‰Ë	ÓØš™XÝ]XÝ[Û‰Ë	ÔÙYÛY[][Û‰Ë	ÑÙ[™\˜]]™SIË	ÑÙ[™\˜]]™R[XYÙIË	ÑÙ[™\˜]]™UšY[ÉË	ÕÉË	ÔÕ	Ë	Ó][S[Ù[	×JKˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\”›Ú™XÝ\]UYÜÐ›ÙS˜[YSX^
Kˆ˜XÙWÝ\HŽˆ›Ù™[[JÉÙ^\š[Y[	Ë	ÛØœÙ\™I×JKˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ˜ÛÛ™šYÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ð[žH˜[Y”ÓÓˆ˜[YK‰ÊKˆœÛÝ\˜ÙHŽˆ›Ù™[[JÉÙ[[ÉË	Ü›ÝÝ\IË	ÜÚ[][]Ü‰×JK›Ü[Û˜[

KˆœÙ\ÜÚ[Û—ØÛÛ™šYÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ð[žH˜[Y”ÓÓˆ˜[YK‰ÊKˆYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ð[žH˜[Y”ÓÓˆ˜[YK‰ÊBŸJB‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ\]UYÜÔ™\ÜÛœÙS˜[YSX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\”›Ú™XÝ\]UYÜÔ™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›[Ù[Ý\HŽˆ›Ù™[[JÉÓ[Y\šXÉË	ÔØÛÜ™PØ]YÛÜšXØ[	Ë	Ô˜[šÚ[™ÉË	Ðš[˜\žPÛ\ÜÚYšXØ][Û‰Ë	Ô™YÜ™\ÜÚ[Û‰Ë	ÓØš™XÝ]XÝ[Û‰Ë	ÔÙYÛY[][Û‰Ë	ÑÙ[™\˜]]™SIË	ÑÙ[™\˜]]™R[XYÙIË	ÑÙ[™\˜]]™UšY[ÉË	ÕÉË	ÔÕ	Ë	Ó][S[Ù[	×JKˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\”›Ú™XÝ\]UYÜÔ™\ÜÛœÙS˜[YSX^
Kˆ˜XÙWÝ\HŽˆ›Ù™[[JÉÙ^\š[Y[	Ë	ÛØœÙ\™I×JKˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Ü™Ø[š^˜][ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

KˆÛÜšÜÜXÙHŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ\]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ˜ÛÛ™šYÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ð[žH˜[Y”ÓÓˆ˜[YK‰ÊKˆœÛÝ\˜ÙHŽˆ›Ù™[[JÉÙ[[ÉË	Ü›ÝÝ\IË	ÜÚ[][]Ü‰×JK›Ü[Û˜[

KˆœÙ\ÜÚ[Û—ØÛÛ™šYÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ð[žH˜[Y”ÓÓˆ˜[YK‰ÊKˆYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ð[žH˜[Y”ÓÓˆ˜[YK‰ÊBŸJB‚‚‹ÊŠ‚ˆ
ˆ]Y\žH\˜[\Î‚ˆ›Ú™XÝÚYˆ]ZY
Ü[Û˜[
HHš[\ˆžH›Ú™XÝˆYÙNˆ[
Ü[Û˜[
HHYÙH[X™\‚ˆ[Z]ˆ[
Ü[Û˜[
HH][\È\ˆYÙBˆ
ˆÝ[[X\žH\Ý™\^HÙ\ÜÚ[ÛœÈ›ÜˆH›Ú™XÝÚ]YÚ[˜][Û‹‚ˆ
‹Â‚‚‚™^ÜÛÛœÝ˜XÙ\”™\^TÙ\ÜÚ[Û“\Ý™\ÜÛœÙR][HH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ›Ú™XÝÛ˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆœ™\^WÝ\HŽˆ›Ù™[[JÉÜÙ\ÜÚ[Û‰Ë	Ý˜XÙI×JKˆ˜Ý\œ™[ÜÝ\Žˆ›Ù™[[JÉÚ[š]	Ë	ÙÙ[™\˜][™ÉË	ØÛÛ\]Y	×JK›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

BŸJB™^ÜÛÛœÝ˜XÙ\”™\^TÙ\ÜÚ[Û“\Ý™\ÜÛœÙHH›Ù˜\œ˜^J˜XÙ\”™\^TÙ\ÜÚ[Û“\Ý™\ÜÛœÙR][JB‚‚‹ÊŠ‚ˆ
ˆ™\]Y\Ý›ÙN‚ˆ›Ú™XÝÚYˆ]ZY
™\]Z\™Y
Bˆ™\^WÝ\NˆœÙ\ÜÚ[ÛˆˆÜˆ˜XÙHˆ
Y˜][ˆœÙ\ÜÚ[ÛˆŠBˆYÎˆ\ÝÙˆ]ZYÈ
™\]Z\™YYˆÙ[XÝØ[Y˜[ÙJBˆÙ[XÝØ[ˆ›ÛÛ
Y˜][ˆ˜[ÙJBˆ
ˆÝ[[X\žHÜ™X]HH™]È™\^HÙ\ÜÚ[Ûˆ[ˆS’UÝ]K‚ˆ
‹Â™^ÜÛÛœÝ˜XÙ\”™\^TÙ\ÜÚ[ÛÜ™X]P›ÙT™\^U\QY˜][HÙ\ÜÚ[Û˜Â™^ÜÛÛœÝ˜XÙ\”™\^TÙ\ÜÚ[ÛÜ™X]P›ÙRYÑY˜][H×NÂ™^ÜÛÛœÝ˜XÙ\”™\^TÙ\ÜÚ[ÛÜ™X]P›ÙTÙ[XÝ[Y˜][H˜[ÙNÂ‚™^ÜÛÛœÝ˜XÙ\”™\^TÙ\ÜÚ[ÛÜ™X]P›ÙHH›Ù›Øš™XÝ
Âˆœ›Ú™XÝÚYŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ™\^WÝ\HŽˆ›Ù™[[JÉÜÙ\ÜÚ[Û‰Ë	Ý˜XÙI×JK™Y˜][
˜XÙ\”™\^TÙ\ÜÚ[ÛÜ™X]P›ÙT™\^U\QY˜][
KˆšYÈŽˆ›Ù˜\œ˜^J›ÙœÝš[™Ê
K]ZY

JK™Y˜][
˜XÙ\”™\^TÙ\ÜÚ[ÛÜ™X]P›ÙRYÑY˜][
KˆœÙ[XÝØ[Žˆ›Ù˜›ÛÛX[Š
K™Y˜][
˜XÙ\”™\^TÙ\ÜÚ[ÛÜ™X]P›ÙTÙ[XÝ[Y˜][
BŸJB‚‚‹ÊŠ‚ˆ
ˆ]Y\žH\˜[\Î‚ˆ›Ú™XÝÚYˆ]ZY
™\]Z\™Y
Bˆ
ˆÝ[[X\žHÙ][Ý\ÝÛH]˜[ÛÛ™šYÜÈ›ÜˆH›Ú™XÝÚ]]˜Z[X›H[Ù[È\ˆ]˜[[\]K‚ˆ
‹Â™^ÜÛÛœÝ˜XÙ\”™\^TÙ\ÜÚ[Û‘Ù]]˜[ÛÛ™šYÜÔ™\ÜÛœÙPYÙ[Yš[š][ÛYÙ[˜[YSX^HMNÂ‚‚™^ÜÛÛœÝ˜XÙ\”™\^TÙ\ÜÚ[Û‘Ù]]˜[ÛÛ™šYÜÔ™\ÜÛœÙTØÙ[˜\š[Ó˜[YSX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\”™\^TÙ\ÜÚ[Û‘Ù]]˜[ÛÛ™šYÜÔ™\ÜÛœÙT[•\Ý˜[YSX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\”™\^TÙ\ÜÚ[Û‘Ù]]˜[ÛÛ™šYÜÔ™\ÜÛœÙR][HH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ™\^WÝ\HŽˆ›Ù™[[JÉÜÙ\ÜÚ[Û‰Ë	Ý˜XÙI×JKˆšYÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÙ[XÝØ[Žˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ˜Ý\œ™[ÜÝ\Žˆ›Ù™[[JÉÚ[š]	Ë	ÙÙ[™\˜][™ÉË	ØÛÛ\]Y	×JK›Ü[Û˜[

Kˆ˜YÙ[ÙYš[š][ÛˆŽˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜YÙ[Û˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\”™\^TÙ\ÜÚ[Û‘Ù]]˜[ÛÛ™šYÜÔ™\ÜÛœÙPYÙ[Yš[š][ÛYÙ[˜[YSX^
K™\ØÜšX™J	Ó˜[YHÙˆHRHYÙ[	ÊKˆ˜YÙ[Ý\HŽˆ›Ù™[[JÉÝ›ÚXÙIË	Ý^	×JK›Ü[Û˜[

Kˆ™\ØÜš\[ÛˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJK™\ØÜšX™J	Ñ]Z[Y\ØÜš\[ÛˆÙˆHRHYÙ[	ÜÈ\œÜÙH[™Ø\Xš[]Y\ÉÊKˆ™\œÚ[Û—Û˜[YHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

BŸJK›Ü[Û˜[

KˆœØÙ[˜\š[ÈŽˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\”™\^TÙ\ÜÚ[Û‘Ù]]˜[ÛÛ™šYÜÔ™\ÜÛœÙTØÙ[˜\š[Ó˜[YSX^
K™\ØÜšX™J	Ó˜[YHÙˆHØÙ[˜\š[ÉÊKˆœÝ]\ÈŽˆ›Ù™[[JÉÓ›ÝÝ\Y	Ë	Ô]Y]YY	Ë	Ô[›š[™ÉË	ÐÛÛ\]Y	Ë	ÑY][™ÉË	Ò[˜XÝ]™IË	Ñ˜Z[Y	Ë	Ô\X[[‰Ë	Ñ^\š[Y[]˜[X][Û‰Ë	Õ\ØY[™ÉË	Ô\X[^˜XÝY	Ë	Ô›ØÙ\ÜÚ[™ÉË	Ñ[][™ÉË	Ô\X[ÛÛ\]Y	Ë	ÓÜ[Z^˜][Û‘]˜[X][Û‰Ë	Ñ\œ›Ü‰Ë	ÐØ[˜Ù[Y	×JK›Ü[Û˜[

K™\ØÜšX™J	ÔÝ]\ÈÙˆHØÙ[˜\š[ÉÊKˆ™\ØÜš\[ÛˆŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	ÓÜ[Û˜[\ØÜš\[ÛˆÙˆHØÙ[˜\š[ÉÊBŸJK›Ü[Û˜[

Kˆœ[—Ý\ÝŽˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\”™\^TÙ\ÜÚ[Û‘Ù]]˜[ÛÛ™šYÜÔ™\ÜÛœÙT[•\Ý˜[YSX^
K™\ØÜšX™J	Ó˜[YHÙˆH\Ý[‰ÊKˆ™\ØÜš\[ÛˆŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	Ñ\ØÜš\[ÛˆÙˆH\Ý[‰ÊBŸJK›Ü[Û˜[

BŸJB™^ÜÛÛœÝ˜XÙ\”™\^TÙ\ÜÚ[Û‘Ù]]˜[ÛÛ™šYÜÔ™\ÜÛœÙHH›Ù˜\œ˜^J˜XÙ\”™\^TÙ\ÜÚ[Û‘Ù]]˜[ÛÛ™šYÜÔ™\ÜÛœÙR][JB‚‚‹ÊŠ‚ˆ
ˆT“\˜[\Î‚ˆÎˆ™\^HÙ\ÜÚ[Ûˆ]ZYˆ
ˆÝ[[X\žHÙ]HÚ[™ÛH™\^HÙ\ÜÚ[ÛˆÚ][™[]Y]K‚ˆ
‹Â™^ÜÛÛœÝ˜XÙ\”™\^TÙ\ÜÚ[Û”™XY\˜[\ÈH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
BŸJB‚™^ÜÛÛœÝ˜XÙ\”™\^TÙ\ÜÚ[Û”™XY™\ÜÛœÙPYÙ[Yš[š][ÛYÙ[˜[YSX^HMNÂ‚‚™^ÜÛÛœÝ˜XÙ\”™\^TÙ\ÜÚ[Û”™XY™\ÜÛœÙTØÙ[˜\š[Ó˜[YSX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\”™\^TÙ\ÜÚ[Û”™XY™\ÜÛœÙT[•\Ý˜[YSX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\”™\^TÙ\ÜÚ[Û”™XY™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ™\^WÝ\HŽˆ›Ù™[[JÉÜÙ\ÜÚ[Û‰Ë	Ý˜XÙI×JKˆšYÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÙ[XÝØ[Žˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ˜Ý\œ™[ÜÝ\Žˆ›Ù™[[JÉÚ[š]	Ë	ÙÙ[™\˜][™ÉË	ØÛÛ\]Y	×JK›Ü[Û˜[

Kˆ˜YÙ[ÙYš[š][ÛˆŽˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜YÙ[Û˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\”™\^TÙ\ÜÚ[Û”™XY™\ÜÛœÙPYÙ[Yš[š][ÛYÙ[˜[YSX^
K™\ØÜšX™J	Ó˜[YHÙˆHRHYÙ[	ÊKˆ˜YÙ[Ý\HŽˆ›Ù™[[JÉÝ›ÚXÙIË	Ý^	×JK›Ü[Û˜[

Kˆ™\ØÜš\[ÛˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJK™\ØÜšX™J	Ñ]Z[Y\ØÜš\[ÛˆÙˆHRHYÙ[	ÜÈ\œÜÙH[™Ø\Xš[]Y\ÉÊKˆ™\œÚ[Û—Û˜[YHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

BŸJK›Ü[Û˜[

KˆœØÙ[˜\š[ÈŽˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\”™\^TÙ\ÜÚ[Û”™XY™\ÜÛœÙTØÙ[˜\š[Ó˜[YSX^
K™\ØÜšX™J	Ó˜[YHÙˆHØÙ[˜\š[ÉÊKˆœÝ]\ÈŽˆ›Ù™[[JÉÓ›ÝÝ\Y	Ë	Ô]Y]YY	Ë	Ô[›š[™ÉË	ÐÛÛ\]Y	Ë	ÑY][™ÉË	Ò[˜XÝ]™IË	Ñ˜Z[Y	Ë	Ô\X[[‰Ë	Ñ^\š[Y[]˜[X][Û‰Ë	Õ\ØY[™ÉË	Ô\X[^˜XÝY	Ë	Ô›ØÙ\ÜÚ[™ÉË	Ñ[][™ÉË	Ô\X[ÛÛ\]Y	Ë	ÓÜ[Z^˜][Û‘]˜[X][Û‰Ë	Ñ\œ›Ü‰Ë	ÐØ[˜Ù[Y	×JK›Ü[Û˜[

K™\ØÜšX™J	ÔÝ]\ÈÙˆHØÙ[˜\š[ÉÊKˆ™\ØÜš\[ÛˆŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	ÓÜ[Û˜[\ØÜš\[ÛˆÙˆHØÙ[˜\š[ÉÊBŸJK›Ü[Û˜[

Kˆœ[—Ý\ÝŽˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\”™\^TÙ\ÜÚ[Û”™XY™\ÜÛœÙT[•\Ý˜[YSX^
K™\ØÜšX™J	Ó˜[YHÙˆH\Ý[‰ÊKˆ™\ØÜš\[ÛˆŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	Ñ\ØÜš\[ÛˆÙˆH\Ý[‰ÊBŸJK›Ü[Û˜[

BŸJB‚‚‹ÊŠ‚ˆ
ˆT“\˜[\Î‚ˆÎˆ™\^HÙ\ÜÚ[Ûˆ]ZY‚”™\]Y\Ý›ÙN‚ˆYÙ[Û˜[YNˆÝš[™È
™\]Z\™Y
BˆYÙ[Ù\ØÜš\[ÛŽˆÝš[™È
Ü[Û˜[
BˆØÙ[˜\š[×Û˜[YNˆÝš[™È
™\]Z\™Y
BˆYÙ[Ý\Nˆ^ˆÜˆ›ÚXÙHˆ
Y˜][ˆ^ŠBˆ›×ÛÙ—Ü›ÝÜÎˆ[
Y˜][ˆŒ
Bˆ\œÛÛ˜\Îˆ\ÝÙˆ]ZYÈ
Ü[Û˜[
BˆÝ\ÝÛWØÛÛ[[œÎˆ\ÝÙˆXÝÈ
Ü[Û˜[
BˆÜ˜\ˆXÝ
Ü[Û˜[
BˆÙ[™\˜]WÙÜ˜\ˆ›ÛÛ
Y˜][ˆYJBˆ
ˆÝ[[X\žHÜ™X]HYÙ[Yš[š][Ûˆ
ÈØÙ[˜\š[È[™Ý\Ù[™\˜][ÛˆÛÜšÙ›ÝË‚“[Ý™\È™\^HÙ\ÜÚ[ÛˆÈÑS‘TUS‘ÈÝ]K‚ˆ
‹Â™^ÜÛÛœÝ˜XÙ\”™\^TÙ\ÜÚ[Û‘Ù[™\˜]TØÙ[˜\š[Ô\˜[\ÈH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
BŸJB‚™^ÜÛÛœÝ˜XÙ\”™\^TÙ\ÜÚ[Û‘Ù[™\˜]TØÙ[˜\š[Ð›ÙPYÙ[˜[YSX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\”™\^TÙ\ÜÚ[Û‘Ù[™\˜]TØÙ[˜\š[Ð›ÙPYÙ[\ØÜš\[Û‘Y˜][HÂ™^ÜÛÛœÝ˜XÙ\”™\^TÙ\ÜÚ[Û‘Ù[™\˜]TØÙ[˜\š[Ð›ÙTØÙ[˜\š[Ó˜[YSX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\”™\^TÙ\ÜÚ[Û‘Ù[™\˜]TØÙ[˜\š[Ð›ÙPYÙ[\QY˜][H^Â™^ÜÛÛœÝ˜XÙ\”™\^TÙ\ÜÚ[Û‘Ù[™\˜]TØÙ[˜\š[Ð›ÙS›ÓÙ”›ÝÜÑY˜][HŒÂ™^ÜÛÛœÝ˜XÙ\”™\^TÙ\ÜÚ[Û‘Ù[™\˜]TØÙ[˜\š[Ð›ÙS›ÓÙ”›ÝÜÓX^HLÂ‚™^ÜÛÛœÝ˜XÙ\”™\^TÙ\ÜÚ[Û‘Ù[™\˜]TØÙ[˜\š[Ð›ÙT\œÛÛ˜\ÑY˜][H×NÂ™^ÜÛÛœÝ˜XÙ\”™\^TÙ\ÜÚ[Û‘Ù[™\˜]TØÙ[˜\š[Ð›ÙPÝ\ÝÛPÛÛ[[œÑY˜][H×NÂ™^ÜÛÛœÝ˜XÙ\”™\^TÙ\ÜÚ[Û‘Ù[™\˜]TØÙ[˜\š[Ð›ÙQÙ[™\˜]QÜ˜\Y˜][HYNÂ‚™^ÜÛÛœÝ˜XÙ\”™\^TÙ\ÜÚ[Û‘Ù[™\˜]TØÙ[˜\š[Ð›ÙHH›Ù›Øš™XÝ
Âˆ˜YÙ[Û˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\”™\^TÙ\ÜÚ[Û‘Ù[™\˜]TØÙ[˜\š[Ð›ÙPYÙ[˜[YSX^
Kˆ˜YÙ[Ù\ØÜš\[ÛˆŽˆ›ÙœÝš[™Ê
K™Y˜][
˜XÙ\”™\^TÙ\ÜÚ[Û‘Ù[™\˜]TØÙ[˜\š[Ð›ÙPYÙ[\ØÜš\[Û‘Y˜][
KˆœØÙ[˜\š[×Û˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\”™\^TÙ\ÜÚ[Û‘Ù[™\˜]TØÙ[˜\š[Ð›ÙTØÙ[˜\š[Ó˜[YSX^
Kˆ˜YÙ[Ý\HŽˆ›Ù™[[JÉÝ^	Ë	Ý›ÚXÙI×JK™Y˜][
˜XÙ\”™\^TÙ\ÜÚ[Û‘Ù[™\˜]TØÙ[˜\š[Ð›ÙPYÙ[\QY˜][
Kˆ››×ÛÙ—Ü›ÝÜÈŽˆ›Ù›[X™\Š
K›Z[ŠJK›X^
˜XÙ\”™\^TÙ\ÜÚ[Û‘Ù[™\˜]TØÙ[˜\š[Ð›ÙS›ÓÙ”›ÝÜÓX^
K™Y˜][
˜XÙ\”™\^TÙ\ÜÚ[Û‘Ù[™\˜]TØÙ[˜\š[Ð›ÙS›ÓÙ”›ÝÜÑY˜][
Kˆœ\œÛÛ˜\ÈŽˆ›Ù˜\œ˜^J›ÙœÝš[™Ê
K]ZY

JK™Y˜][
˜XÙ\”™\^TÙ\ÜÚ[Û‘Ù[™\˜]TØÙ[˜\š[Ð›ÙT\œÛÛ˜\ÑY˜][
Kˆ˜Ý\ÝÛWØÛÛ[[œÈŽˆ›Ù˜\œ˜^J›Ùœ™XÛÜ™
›ÙœÝš[™Ê
K›ÙœÝš[™Ê
JJK™Y˜][
˜XÙ\”™\^TÙ\ÜÚ[Û‘Ù[™\˜]TØÙ[˜\š[Ð›ÙPÝ\ÝÛPÛÛ[[œÑY˜][
Kˆ™Ü˜\Žˆ›Ùœ™XÛÜ™
›ÙœÝš[™Ê
K›ÙœÝš[™Ê
JK›Ü[Û˜[

Kˆ™Ù[™\˜]WÙÜ˜\Žˆ›Ù˜›ÛÛX[Š
K™Y˜][
˜XÙ\”™\^TÙ\ÜÚ[Û‘Ù[™\˜]TØÙ[˜\š[Ð›ÙQÙ[™\˜]QÜ˜\Y˜][
BŸJB‚‚™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÓ\Ý]Y\žT\˜[\ÈH›Ù›Øš™XÝ
ÂˆœYÙHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	ÐHYÙH[X™\ˆÚ][ˆHYÚ[˜]Y™\Ý[Ù]‰ÊKˆ›[Z]Žˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	Ó[X™\ˆÙˆ™\Ý[ÈÈ™]\›ˆ\ˆYÙK‰ÊBŸJB‚™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÓ\Ý™\ÜÛœÙTÝ]\ÑY˜][HYNÂ‚‚™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÓ\Ý™\ÜÛœÙT™\Ý[Ý\ÝÛUšY]ÜÒ][S˜[YSX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÓ\Ý™\ÜÛœÙT™\Ý[Ý\ÝÛUšY]ÜÒ][TÜÚ][Û“Z[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÓ\Ý™\ÜÛœÙT™\Ý[Ý\ÝÛUšY]ÜÒ][TÜÚ][Û“X^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÓ\Ý™\ÜÛœÙT™\Ý[Ý\ÝÛUšY]ÜÒ][RXÛÛ“X^HLÂ‚‚‚‚‚™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÓ\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
K™Y˜][
˜XÙ\”Ø]™YšY]ÜÓ\Ý™\ÜÛœÙTÝ]\ÑY˜][
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆ™Y˜][ÝXœÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšÙ^HŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ›X™[Žˆ›ÙœÝš[™Ê
K›Z[ŠJKˆX—Ý\HŽˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJJKˆ˜Ý\ÝÛWÝšY]ÜÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\”Ø]™YšY]ÜÓ\Ý™\ÜÛœÙT™\Ý[Ý\ÝÛUšY]ÜÒ][S˜[YSX^
KˆX—Ý\HŽˆ›Ù™[[JÉÝ˜XÙ\ÉË	ÜÜ[œÉË	Ý›ÚXÙIË	Ú[XYÚ[™IË	Ý\Ù\œÉË	Ý\Ù\—Ù]Z[	Ë	ÜÙ\ÜÚ[ÛœÉ×JKˆš\ÚXš[]HŽˆ›Ù™[[JÉÜ\œÛÛ˜[	Ë	Ü›Ú™XÝ	×JK›Ü[Û˜[

KˆœÜÚ][ÛˆŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\”Ø]™YšY]ÜÓ\Ý™\ÜÛœÙT™\Ý[Ý\ÝÛUšY]ÜÒ][TÜÚ][Û“Z[ŠK›X^
˜XÙ\”Ø]™YšY]ÜÓ\Ý™\ÜÛœÙT™\Ý[Ý\ÝÛUšY]ÜÒ][TÜÚ][Û“X^
K›Ü[Û˜[

KˆšXÛÛˆŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\”Ø]™YšY]ÜÓ\Ý™\ÜÛœÙT™\Ý[Ý\ÝÛUšY]ÜÒ][RXÛÛ“X^
K›Ü[Û˜[

Kˆ˜ÛÛ™šYÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ˜Ü™X]YØžHŽˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ™[XZ[Žˆ›ÙœÝš[™Ê
K™[XZ[

K›Z[ŠJK›Ü[Û˜[

BŸJK›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ\]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

BŸJJBŸJBŸJB‚‚™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÐÜ™X]P›ÙS˜[YSX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÐÜ™X]P›ÙTÜÚ][Û“Z[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÐÜ™X]P›ÙTÜÚ][Û“X^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÐÜ™X]P›ÙRXÛÛ“X^HLÂ‚‚‚‚‚™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÐÜ™X]P›ÙHH›Ù›Øš™XÝ
Âˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\”Ø]™YšY]ÜÐÜ™X]P›ÙS˜[YSX^
KˆX—Ý\HŽˆ›Ù™[[JÉÝ˜XÙ\ÉË	ÜÜ[œÉË	Ý›ÚXÙIË	Ú[XYÚ[™IË	Ý\Ù\œÉË	Ý\Ù\—Ù]Z[	Ë	ÜÙ\ÜÚ[ÛœÉ×JKˆš\ÚXš[]HŽˆ›Ù™[[JÉÜ\œÛÛ˜[	Ë	Ü›Ú™XÝ	×JK›Ü[Û˜[

KˆœÜÚ][ÛˆŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\”Ø]™YšY]ÜÐÜ™X]P›ÙTÜÚ][Û“Z[ŠK›X^
˜XÙ\”Ø]™YšY]ÜÐÜ™X]P›ÙTÜÚ][Û“X^
K›Ü[Û˜[

KˆšXÛÛˆŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\”Ø]™YšY]ÜÐÜ™X]P›ÙRXÛÛ“X^
K›Ü[Û˜[

Kˆ˜ÛÛ™šYÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ˜Ü™X]YØžHŽˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ™[XZ[Žˆ›ÙœÝš[™Ê
K™[XZ[

K›Z[ŠJK›Ü[Û˜[

BŸJK›Ü[Û˜[

BŸJB‚™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÐÜ™X]T™\ÜÛœÙTÝ]\ÑY˜][HYNÂ™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÐÜ™X]T™\ÜÛœÙT™\Ý[˜[YSX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÐÜ™X]T™\ÜÛœÙT™\Ý[ÜÚ][Û“Z[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÐÜ™X]T™\ÜÛœÙT™\Ý[ÜÚ][Û“X^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÐÜ™X]T™\ÜÛœÙT™\Ý[XÛÛ“X^HLÂ‚‚‚‚‚‚‚™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÐÜ™X]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
K™Y˜][
˜XÙ\”Ø]™YšY]ÜÐÜ™X]T™\ÜÛœÙTÝ]\ÑY˜][
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\”Ø]™YšY]ÜÐÜ™X]T™\ÜÛœÙT™\Ý[˜[YSX^
KˆX—Ý\HŽˆ›Ù™[[JÉÝ˜XÙ\ÉË	ÜÜ[œÉË	Ý›ÚXÙIË	Ú[XYÚ[™IË	Ý\Ù\œÉË	Ý\Ù\—Ù]Z[	Ë	ÜÙ\ÜÚ[ÛœÉ×JKˆš\ÚXš[]HŽˆ›Ù™[[JÉÜ\œÛÛ˜[	Ë	Ü›Ú™XÝ	×JK›Ü[Û˜[

KˆœÜÚ][ÛˆŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\”Ø]™YšY]ÜÐÜ™X]T™\ÜÛœÙT™\Ý[ÜÚ][Û“Z[ŠK›X^
˜XÙ\”Ø]™YšY]ÜÐÜ™X]T™\ÜÛœÙT™\Ý[ÜÚ][Û“X^
K›Ü[Û˜[

KˆšXÛÛˆŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\”Ø]™YšY]ÜÐÜ™X]T™\ÜÛœÙT™\Ý[XÛÛ“X^
K›Ü[Û˜[

Kˆ˜ÛÛ™šYÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜Ü™X]YØžHŽˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ™[XZ[Žˆ›ÙœÝš[™Ê
K™[XZ[

K›Z[ŠJK›Ü[Û˜[

BŸJK›Ü[Û˜[

Kˆ\]YØžHŽˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ™[XZ[Žˆ›ÙœÝš[™Ê
K™[XZ[

K›Z[ŠJK›Ü[Û˜[

BŸJK›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ\]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

BŸJBŸJB‚‚™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÔ™[Ü™\›ÙS˜[YSX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÔ™[Ü™\›ÙTÜÚ][Û“Z[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÔ™[Ü™\›ÙTÜÚ][Û“X^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÔ™[Ü™\›ÙRXÛÛ“X^HLÂ‚‚‚‚‚™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÔ™[Ü™\›ÙHH›Ù›Øš™XÝ
Âˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\”Ø]™YšY]ÜÔ™[Ü™\›ÙS˜[YSX^
KˆX—Ý\HŽˆ›Ù™[[JÉÝ˜XÙ\ÉË	ÜÜ[œÉË	Ý›ÚXÙIË	Ú[XYÚ[™IË	Ý\Ù\œÉË	Ý\Ù\—Ù]Z[	Ë	ÜÙ\ÜÚ[ÛœÉ×JKˆš\ÚXš[]HŽˆ›Ù™[[JÉÜ\œÛÛ˜[	Ë	Ü›Ú™XÝ	×JK›Ü[Û˜[

KˆœÜÚ][ÛˆŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\”Ø]™YšY]ÜÔ™[Ü™\›ÙTÜÚ][Û“Z[ŠK›X^
˜XÙ\”Ø]™YšY]ÜÔ™[Ü™\›ÙTÜÚ][Û“X^
K›Ü[Û˜[

KˆšXÛÛˆŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\”Ø]™YšY]ÜÔ™[Ü™\›ÙRXÛÛ“X^
K›Ü[Û˜[

Kˆ˜ÛÛ™šYÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ˜Ü™X]YØžHŽˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ™[XZ[Žˆ›ÙœÝš[™Ê
K™[XZ[

K›Z[ŠJK›Ü[Û˜[

BŸJK›Ü[Û˜[

BŸJB‚™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÔ™[Ü™\”™\ÜÛœÙTÝ]\ÑY˜][HYNÂ‚‚™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÔ™[Ü™\”™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
K™Y˜][
˜XÙ\”Ø]™YšY]ÜÔ™[Ü™\”™\ÜÛœÙTÝ]\ÑY˜][
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆ›Y\ÜØYÙHŽˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJBŸJB‚‚™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÔ™XY\˜[\ÈH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
BŸJB‚™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÔ™XY™\ÜÛœÙTÝ]\ÑY˜][HYNÂ™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÔ™XY™\ÜÛœÙT™\Ý[˜[YSX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÔ™XY™\ÜÛœÙT™\Ý[ÜÚ][Û“Z[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÔ™XY™\ÜÛœÙT™\Ý[ÜÚ][Û“X^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÔ™XY™\ÜÛœÙT™\Ý[XÛÛ“X^HLÂ‚‚‚‚‚‚‚™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÔ™XY™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
K™Y˜][
˜XÙ\”Ø]™YšY]ÜÔ™XY™\ÜÛœÙTÝ]\ÑY˜][
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\”Ø]™YšY]ÜÔ™XY™\ÜÛœÙT™\Ý[˜[YSX^
KˆX—Ý\HŽˆ›Ù™[[JÉÝ˜XÙ\ÉË	ÜÜ[œÉË	Ý›ÚXÙIË	Ú[XYÚ[™IË	Ý\Ù\œÉË	Ý\Ù\—Ù]Z[	Ë	ÜÙ\ÜÚ[ÛœÉ×JKˆš\ÚXš[]HŽˆ›Ù™[[JÉÜ\œÛÛ˜[	Ë	Ü›Ú™XÝ	×JK›Ü[Û˜[

KˆœÜÚ][ÛˆŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\”Ø]™YšY]ÜÔ™XY™\ÜÛœÙT™\Ý[ÜÚ][Û“Z[ŠK›X^
˜XÙ\”Ø]™YšY]ÜÔ™XY™\ÜÛœÙT™\Ý[ÜÚ][Û“X^
K›Ü[Û˜[

KˆšXÛÛˆŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\”Ø]™YšY]ÜÔ™XY™\ÜÛœÙT™\Ý[XÛÛ“X^
K›Ü[Û˜[

Kˆ˜ÛÛ™šYÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜Ü™X]YØžHŽˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ™[XZ[Žˆ›ÙœÝš[™Ê
K™[XZ[

K›Z[ŠJK›Ü[Û˜[

BŸJK›Ü[Û˜[

Kˆ\]YØžHŽˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ™[XZ[Žˆ›ÙœÝš[™Ê
K™[XZ[

K›Z[ŠJK›Ü[Û˜[

BŸJK›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ\]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

BŸJBŸJB‚‚™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÕ\]T\˜[\ÈH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
BŸJB‚™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÕ\]P›ÙS˜[YSX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÕ\]P›ÙTÜÚ][Û“Z[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÕ\]P›ÙTÜÚ][Û“X^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÕ\]P›ÙRXÛÛ“X^HLÂ‚‚‚‚‚™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÕ\]P›ÙHH›Ù›Øš™XÝ
Âˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\”Ø]™YšY]ÜÕ\]P›ÙS˜[YSX^
KˆX—Ý\HŽˆ›Ù™[[JÉÝ˜XÙ\ÉË	ÜÜ[œÉË	Ý›ÚXÙIË	Ú[XYÚ[™IË	Ý\Ù\œÉË	Ý\Ù\—Ù]Z[	Ë	ÜÙ\ÜÚ[ÛœÉ×JKˆš\ÚXš[]HŽˆ›Ù™[[JÉÜ\œÛÛ˜[	Ë	Ü›Ú™XÝ	×JK›Ü[Û˜[

KˆœÜÚ][ÛˆŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\”Ø]™YšY]ÜÕ\]P›ÙTÜÚ][Û“Z[ŠK›X^
˜XÙ\”Ø]™YšY]ÜÕ\]P›ÙTÜÚ][Û“X^
K›Ü[Û˜[

KˆšXÛÛˆŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\”Ø]™YšY]ÜÕ\]P›ÙRXÛÛ“X^
K›Ü[Û˜[

Kˆ˜ÛÛ™šYÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ˜Ü™X]YØžHŽˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ™[XZ[Žˆ›ÙœÝš[™Ê
K™[XZ[

K›Z[ŠJK›Ü[Û˜[

BŸJK›Ü[Û˜[

BŸJB‚™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÕ\]T™\ÜÛœÙTÝ]\ÑY˜][HYNÂ™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÕ\]T™\ÜÛœÙT™\Ý[˜[YSX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÕ\]T™\ÜÛœÙT™\Ý[ÜÚ][Û“Z[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÕ\]T™\ÜÛœÙT™\Ý[ÜÚ][Û“X^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÕ\]T™\ÜÛœÙT™\Ý[XÛÛ“X^HLÂ‚‚‚‚‚‚‚™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÕ\]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
K™Y˜][
˜XÙ\”Ø]™YšY]ÜÕ\]T™\ÜÛœÙTÝ]\ÑY˜][
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\”Ø]™YšY]ÜÕ\]T™\ÜÛœÙT™\Ý[˜[YSX^
KˆX—Ý\HŽˆ›Ù™[[JÉÝ˜XÙ\ÉË	ÜÜ[œÉË	Ý›ÚXÙIË	Ú[XYÚ[™IË	Ý\Ù\œÉË	Ý\Ù\—Ù]Z[	Ë	ÜÙ\ÜÚ[ÛœÉ×JKˆš\ÚXš[]HŽˆ›Ù™[[JÉÜ\œÛÛ˜[	Ë	Ü›Ú™XÝ	×JK›Ü[Û˜[

KˆœÜÚ][ÛˆŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\”Ø]™YšY]ÜÕ\]T™\ÜÛœÙT™\Ý[ÜÚ][Û“Z[ŠK›X^
˜XÙ\”Ø]™YšY]ÜÕ\]T™\ÜÛœÙT™\Ý[ÜÚ][Û“X^
K›Ü[Û˜[

KˆšXÛÛˆŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\”Ø]™YšY]ÜÕ\]T™\ÜÛœÙT™\Ý[XÛÛ“X^
K›Ü[Û˜[

Kˆ˜ÛÛ™šYÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜Ü™X]YØžHŽˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ™[XZ[Žˆ›ÙœÝš[™Ê
K™[XZ[

K›Z[ŠJK›Ü[Û˜[

BŸJK›Ü[Û˜[

Kˆ\]YØžHŽˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ™[XZ[Žˆ›ÙœÝš[™Ê
K™[XZ[

K›Z[ŠJK›Ü[Û˜[

BŸJK›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ\]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

BŸJBŸJB‚‚™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÔ\X[\]T\˜[\ÈH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
BŸJB‚™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÔ\X[\]P›ÙS˜[YSX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÔ\X[\]P›ÙTÜÚ][Û“Z[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÔ\X[\]P›ÙTÜÚ][Û“X^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÔ\X[\]P›ÙRXÛÛ“X^HLÂ‚‚‚‚‚™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÔ\X[\]P›ÙHH›Ù›Øš™XÝ
Âˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\”Ø]™YšY]ÜÔ\X[\]P›ÙS˜[YSX^
KˆX—Ý\HŽˆ›Ù™[[JÉÝ˜XÙ\ÉË	ÜÜ[œÉË	Ý›ÚXÙIË	Ú[XYÚ[™IË	Ý\Ù\œÉË	Ý\Ù\—Ù]Z[	Ë	ÜÙ\ÜÚ[ÛœÉ×JKˆš\ÚXš[]HŽˆ›Ù™[[JÉÜ\œÛÛ˜[	Ë	Ü›Ú™XÝ	×JK›Ü[Û˜[

KˆœÜÚ][ÛˆŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\”Ø]™YšY]ÜÔ\X[\]P›ÙTÜÚ][Û“Z[ŠK›X^
˜XÙ\”Ø]™YšY]ÜÔ\X[\]P›ÙTÜÚ][Û“X^
K›Ü[Û˜[

KˆšXÛÛˆŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\”Ø]™YšY]ÜÔ\X[\]P›ÙRXÛÛ“X^
K›Ü[Û˜[

Kˆ˜ÛÛ™šYÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ˜Ü™X]YØžHŽˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ™[XZ[Žˆ›ÙœÝš[™Ê
K™[XZ[

K›Z[ŠJK›Ü[Û˜[

BŸJK›Ü[Û˜[

BŸJB‚™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÔ\X[\]T™\ÜÛœÙTÝ]\ÑY˜][HYNÂ™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÔ\X[\]T™\ÜÛœÙT™\Ý[˜[YSX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÔ\X[\]T™\ÜÛœÙT™\Ý[ÜÚ][Û“Z[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÔ\X[\]T™\ÜÛœÙT™\Ý[ÜÚ][Û“X^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÔ\X[\]T™\ÜÛœÙT™\Ý[XÛÛ“X^HLÂ‚‚‚‚‚‚‚™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÔ\X[\]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
K™Y˜][
˜XÙ\”Ø]™YšY]ÜÔ\X[\]T™\ÜÛœÙTÝ]\ÑY˜][
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\”Ø]™YšY]ÜÔ\X[\]T™\ÜÛœÙT™\Ý[˜[YSX^
KˆX—Ý\HŽˆ›Ù™[[JÉÝ˜XÙ\ÉË	ÜÜ[œÉË	Ý›ÚXÙIË	Ú[XYÚ[™IË	Ý\Ù\œÉË	Ý\Ù\—Ù]Z[	Ë	ÜÙ\ÜÚ[ÛœÉ×JKˆš\ÚXš[]HŽˆ›Ù™[[JÉÜ\œÛÛ˜[	Ë	Ü›Ú™XÝ	×JK›Ü[Û˜[

KˆœÜÚ][ÛˆŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\”Ø]™YšY]ÜÔ\X[\]T™\ÜÛœÙT™\Ý[ÜÚ][Û“Z[ŠK›X^
˜XÙ\”Ø]™YšY]ÜÔ\X[\]T™\ÜÛœÙT™\Ý[ÜÚ][Û“X^
K›Ü[Û˜[

KˆšXÛÛˆŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\”Ø]™YšY]ÜÔ\X[\]T™\ÜÛœÙT™\Ý[XÛÛ“X^
K›Ü[Û˜[

Kˆ˜ÛÛ™šYÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜Ü™X]YØžHŽˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ™[XZ[Žˆ›ÙœÝš[™Ê
K™[XZ[

K›Z[ŠJK›Ü[Û˜[

BŸJK›Ü[Û˜[

Kˆ\]YØžHŽˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ™[XZ[Žˆ›ÙœÝš[™Ê
K™[XZ[

K›Z[ŠJK›Ü[Û˜[

BŸJK›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ\]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

BŸJBŸJB‚‚™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÑ[]T\˜[\ÈH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
BŸJB‚™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÑ[]T™\ÜÛœÙTÝ]\ÑY˜][HYNÂ‚‚™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÑ[]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
K™Y˜][
˜XÙ\”Ø]™YšY]ÜÑ[]T™\ÜÛœÙTÝ]\ÑY˜][
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆ›Y\ÜØYÙHŽˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJBŸJB‚‚™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÑ\XØ]T\˜[\ÈH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
BŸJB‚™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÑ\XØ]P›ÙS˜[YSX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÑ\XØ]P›ÙTÜÚ][Û“Z[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÑ\XØ]P›ÙTÜÚ][Û“X^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÑ\XØ]P›ÙRXÛÛ“X^HLÂ‚‚‚‚‚™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÑ\XØ]P›ÙHH›Ù›Øš™XÝ
Âˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\”Ø]™YšY]ÜÑ\XØ]P›ÙS˜[YSX^
KˆX—Ý\HŽˆ›Ù™[[JÉÝ˜XÙ\ÉË	ÜÜ[œÉË	Ý›ÚXÙIË	Ú[XYÚ[™IË	Ý\Ù\œÉË	Ý\Ù\—Ù]Z[	Ë	ÜÙ\ÜÚ[ÛœÉ×JKˆš\ÚXš[]HŽˆ›Ù™[[JÉÜ\œÛÛ˜[	Ë	Ü›Ú™XÝ	×JK›Ü[Û˜[

KˆœÜÚ][ÛˆŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\”Ø]™YšY]ÜÑ\XØ]P›ÙTÜÚ][Û“Z[ŠK›X^
˜XÙ\”Ø]™YšY]ÜÑ\XØ]P›ÙTÜÚ][Û“X^
K›Ü[Û˜[

KˆšXÛÛˆŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\”Ø]™YšY]ÜÑ\XØ]P›ÙRXÛÛ“X^
K›Ü[Û˜[

Kˆ˜ÛÛ™šYÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ˜Ü™X]YØžHŽˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ™[XZ[Žˆ›ÙœÝš[™Ê
K™[XZ[

K›Z[ŠJK›Ü[Û˜[

BŸJK›Ü[Û˜[

BŸJB‚™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÑ\XØ]T™\ÜÛœÙTÝ]\ÑY˜][HYNÂ™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÑ\XØ]T™\ÜÛœÙT™\Ý[˜[YSX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÑ\XØ]T™\ÜÛœÙT™\Ý[ÜÚ][Û“Z[ˆHLŒMÍÍÂ™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÑ\XØ]T™\ÜÛœÙT™\Ý[ÜÚ][Û“X^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÑ\XØ]T™\ÜÛœÙT™\Ý[XÛÛ“X^HLÂ‚‚‚‚‚‚‚™^ÜÛÛœÝ˜XÙ\”Ø]™YšY]ÜÑ\XØ]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
K™Y˜][
˜XÙ\”Ø]™YšY]ÜÑ\XØ]T™\ÜÛœÙTÝ]\ÑY˜][
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\”Ø]™YšY]ÜÑ\XØ]T™\ÜÛœÙT™\Ý[˜[YSX^
KˆX—Ý\HŽˆ›Ù™[[JÉÝ˜XÙ\ÉË	ÜÜ[œÉË	Ý›ÚXÙIË	Ú[XYÚ[™IË	Ý\Ù\œÉË	Ý\Ù\—Ù]Z[	Ë	ÜÙ\ÜÚ[ÛœÉ×JKˆš\ÚXš[]HŽˆ›Ù™[[JÉÜ\œÛÛ˜[	Ë	Ü›Ú™XÝ	×JK›Ü[Û˜[

KˆœÜÚ][ÛˆŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\”Ø]™YšY]ÜÑ\XØ]T™\ÜÛœÙT™\Ý[ÜÚ][Û“Z[ŠK›X^
˜XÙ\”Ø]™YšY]ÜÑ\XØ]T™\ÜÛœÙT™\Ý[ÜÚ][Û“X^
K›Ü[Û˜[

KˆšXÛÛˆŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\”Ø]™YšY]ÜÑ\XØ]T™\ÜÛœÙT™\Ý[XÛÛ“X^
K›Ü[Û˜[

Kˆ˜ÛÛ™šYÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜Ü™X]YØžHŽˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ™[XZ[Žˆ›ÙœÝš[™Ê
K™[XZ[

K›Z[ŠJK›Ü[Û˜[

BŸJK›Ü[Û˜[

Kˆ\]YØžHŽˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ™[XZ[Žˆ›ÙœÝš[™Ê
K™[XZ[

K›Z[ŠJK›Ü[Û˜[

BŸJK›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ\]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

BŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆÔ•Q›ÜˆÚ\™Y[šÜËˆ™\]Z\™\È]][XØ][Û‹‚ˆ
‹Â™^ÜÛÛœÝ˜XÙ\”Ú\™Y[šÜÓ\Ý]Y\žT\˜[\ÈH›Ù›Øš™XÝ
ÂˆœYÙHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	ÐHYÙH[X™\ˆÚ][ˆHYÚ[˜]Y™\Ý[Ù]‰ÊKˆ›[Z]Žˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	Ó[X™\ˆÙˆ™\Ý[ÈÈ™]\›ˆ\ˆYÙK‰ÊBŸJB‚‚‚‚‚™^ÜÛÛœÝ˜XÙ\”Ú\™Y[šÜÓ\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
Âˆ˜ÛÝ[Žˆ›Ù›[X™\Š
Kˆ›™^Žˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™]š[Ý\ÈŽˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™\Ý[ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ™\ÛÝ\˜ÙWÝ\HŽˆ›Ù™[[JÉÝ˜XÙIË	Ù\Ú›Ø\™	Ë	Ù]˜[Ü[‰Ë	Ù]\Ù]	Ë	Ü›Ú™XÝ	×JK›Ü[Û˜[

Kˆœ™\ÛÝ\˜ÙWÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

KˆÚÙ[ˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ˜XØÙ\Ü×Ý\HŽˆ›Ù™[[JÉÜX›XÉË	Ü™\ÝšXÝY	×JK›Ü[Û˜[

Kˆš\×ØXÝ]™HŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ™^\™\×Ø]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ˜Ü™X]YØžHŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ˜XØÙ\Ü×ØÛÝ[Žˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆœÚ\™WÝ\›Žˆ›ÙœÝš[™Ê
K›Ü[Û˜[

BŸJJBŸJB‚‚‹ÊŠ‚ˆ
ˆÔ•Q›ÜˆÚ\™Y[šÜËˆ™\]Z\™\È]][XØ][Û‹‚ˆ
‹Â™^ÜÛÛœÝ˜XÙ\”Ú\™Y[šÜÐÜ™X]P›ÙT™\ÛÝ\˜ÙRYX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\”Ú\™Y[šÜÐÜ™X]P›ÙPXØÙ\ÜÕ\QY˜][H™\ÝšXÝYÂ™^ÜÛÛœÝ˜XÙ\”Ú\™Y[šÜÐÜ™X]P›ÙQ[XZ[ÑY˜][H×NÂ‚™^ÜÛÛœÝ˜XÙ\”Ú\™Y[šÜÐÜ™X]P›ÙHH›Ù›Øš™XÝ
Âˆœ™\ÛÝ\˜ÙWÝ\HŽˆ›Ù™[[JÉÝ˜XÙIË	Ù\Ú›Ø\™	Ë	Ü›Ú™XÝ	×JKˆœ™\ÛÝ\˜ÙWÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\”Ú\™Y[šÜÐÜ™X]P›ÙT™\ÛÝ\˜ÙRYX^
Kˆ˜XØÙ\Ü×Ý\HŽˆ›Ù™[[JÉÜX›XÉË	Ü™\ÝšXÝY	×JK™Y˜][
˜XÙ\”Ú\™Y[šÜÐÜ™X]P›ÙPXØÙ\ÜÕ\QY˜][
Kˆ™^\™\×Ø]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™[XZ[ÈŽˆ›Ù˜\œ˜^J›ÙœÝš[™Ê
K™[XZ[

K›Z[ŠJJK™Y˜][
˜XÙ\”Ú\™Y[šÜÐÜ™X]P›ÙQ[XZ[ÑY˜][
K™\ØÜšX™J	Ñ[XZ[ÈÈÜ˜[XØÙ\ÜÈÈ
›Üˆ™\ÝšXÝY[šÜÊK‰ÊBŸJB‚‚‹ÊŠ‚ˆ
ˆÔ•Q›ÜˆÚ\™Y[šÜËˆ™\]Z\™\È]][XØ][Û‹‚ˆ
‹Â™^ÜÛÛœÝ˜XÙ\”Ú\™Y[šÜÔ™XY\˜[\ÈH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K™\ØÜšX™J	ÐHURQÝš[™ÈY[YžZ[™È\ÈÚ\™Y[šË‰ÊBŸJB‚‚‚™^ÜÛÛœÝ˜XÙ\”Ú\™Y[šÜÔ™XY™\ÜÛœÙPXØÙ\ÜÓ\Ý][Q[XZ[X^HMÂ‚‚‚™^ÜÛÛœÝ˜XÙ\”Ú\™Y[šÜÔ™XY™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ™\ÛÝ\˜ÙWÝ\HŽˆ›Ù™[[JÉÝ˜XÙIË	Ù\Ú›Ø\™	Ë	Ù]˜[Ü[‰Ë	Ù]\Ù]	Ë	Ü›Ú™XÝ	×JK›Ü[Û˜[

Kˆœ™\ÛÝ\˜ÙWÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

KˆÚÙ[ˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ˜XØÙ\Ü×Ý\HŽˆ›Ù™[[JÉÜX›XÉË	Ü™\ÝšXÝY	×JK›Ü[Û˜[

Kˆš\×ØXÝ]™HŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ™^\™\×Ø]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ˜Ü™X]YØžHŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ˜XØÙ\Ü×Û\ÝŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ™[XZ[Žˆ›ÙœÝš[™Ê
K™[XZ[

K›Z[ŠJK›X^
˜XÙ\”Ú\™Y[šÜÔ™XY™\ÜÛœÙPXØÙ\ÜÓ\Ý][Q[XZ[X^
Kˆ\Ù\ˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ™Ü˜[YØžHŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

BŸJJK›Ü[Û˜[

KˆœÚ\™WÝ\›Žˆ›ÙœÝš[™Ê
K›Ü[Û˜[

BŸJB‚‚‹ÊŠ‚ˆ
ˆÔ•Q›ÜˆÚ\™Y[šÜËˆ™\]Z\™\È]][XØ][Û‹‚ˆ
‹Â™^ÜÛÛœÝ˜XÙ\”Ú\™Y[šÜÕ\]T\˜[\ÈH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K™\ØÜšX™J	ÐHURQÝš[™ÈY[YžZ[™È\ÈÚ\™Y[šË‰ÊBŸJB‚™^ÜÛÛœÝ˜XÙ\”Ú\™Y[šÜÕ\]P›ÙHH›Ù›Øš™XÝ
Âˆ˜XØÙ\Ü×Ý\HŽˆ›Ù™[[JÉÜX›XÉË	Ü™\ÝšXÝY	×JK›Ü[Û˜[

Kˆš\×ØXÝ]™HŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ™^\™\×Ø]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

BŸJB‚™^ÜÛÛœÝ˜XÙ\”Ú\™Y[šÜÕ\]T™\ÜÛœÙHH›Ù›Øš™XÝ
Âˆ˜XØÙ\Ü×Ý\HŽˆ›Ù™[[JÉÜX›XÉË	Ü™\ÝšXÝY	×JK›Ü[Û˜[

Kˆš\×ØXÝ]™HŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ™^\™\×Ø]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

BŸJB‚‚‹ÊŠ‚ˆ
ˆÔ•Q›ÜˆÚ\™Y[šÜËˆ™\]Z\™\È]][XØ][Û‹‚ˆ
‹Â™^ÜÛÛœÝ˜XÙ\”Ú\™Y[šÜÔ\X[\]T\˜[\ÈH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K™\ØÜšX™J	ÐHURQÝš[™ÈY[YžZ[™È\ÈÚ\™Y[šË‰ÊBŸJB‚™^ÜÛÛœÝ˜XÙ\”Ú\™Y[šÜÔ\X[\]P›ÙHH›Ù›Øš™XÝ
Âˆ˜XØÙ\Ü×Ý\HŽˆ›Ù™[[JÉÜX›XÉË	Ü™\ÝšXÝY	×JK›Ü[Û˜[

Kˆš\×ØXÝ]™HŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ™^\™\×Ø]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

BŸJB‚™^ÜÛÛœÝ˜XÙ\”Ú\™Y[šÜÔ\X[\]T™\ÜÛœÙHH›Ù›Øš™XÝ
Âˆ˜XØÙ\Ü×Ý\HŽˆ›Ù™[[JÉÜX›XÉË	Ü™\ÝšXÝY	×JK›Ü[Û˜[

Kˆš\×ØXÝ]™HŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ™^\™\×Ø]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

BŸJB‚‚‹ÊŠ‚ˆ
ˆÔ•Q›ÜˆÚ\™Y[šÜËˆ™\]Z\™\È]][XØ][Û‹‚ˆ
‹Â™^ÜÛÛœÝ˜XÙ\”Ú\™Y[šÜÑ[]T\˜[\ÈH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K™\ØÜšX™J	ÐHURQÝš[™ÈY[YžZ[™È\ÈÚ\™Y[šË‰ÊBŸJB‚‚‹ÊŠ‚ˆ
ˆY[XZ[
ÊHÈHPÓÙˆHÚ\™Y[šË‚ˆ
‹Â™^ÜÛÛœÝ˜XÙ\”Ú\™Y[šÜÐYXØÙ\ÜÔ\˜[\ÈH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K™\ØÜšX™J	ÐHURQÝš[™ÈY[YžZ[™È\ÈÚ\™Y[šË‰ÊBŸJB‚‚‚‚‚™^ÜÛÛœÝ˜XÙ\”Ú\™Y[šÜÐYXØÙ\ÜÐ›ÙHH›Ù›Øš™XÝ
Âˆ™[XZ[ÈŽˆ›Ù˜\œ˜^J›ÙœÝš[™Ê
K™[XZ[

K›Z[ŠJJK›Z[ŠJBŸJB‚‚‹ÊŠ‚ˆ
ˆ™[[Ý™H[ˆ[XZ[œ›ÛHHPÓ‚ˆ
‹Â™^ÜÛÛœÝ˜XÙ\”Ú\™Y[šÜÔ™[[Ý™PXØÙ\ÜÔ\˜[\ÈH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K™\ØÜšX™J	ÐHURQÝš[™ÈY[YžZ[™È\ÈÚ\™Y[šË‰ÊKˆ˜XØÙ\Ü×ÚYŽˆ›ÙœÝš[™Ê
BŸJB‚‚‹ÊŠ‚ˆ
ˆ™\ÛÛ™HHÚ\™HÚÙ[ˆÈH[™\›Z[™È™\ÛÝ\˜ÙH]K‚‹HX›XÈ[šÜÎˆ›È]]™YYY‹H™\ÝšXÝY[šÜÎˆ\Ù\ˆ]\Ý™H]][XØ]Y
È[XZ[[ˆPÓˆ
‹Â™^ÜÛÛœÝ˜XÙ\”Ú\™Y™XY\˜[\ÈH›Ù›Øš™XÝ
ÂˆÚÙ[ˆŽˆ›ÙœÝš[™Ê
BŸJB‚‚‚‚‚‚‚‚‚‚‚‚‚‚‚‚™^ÜÛÛœÝ˜XÙ\”Ú\™Y™XY™\ÜÛœÙHH›Ù›Øš™XÝ
Âˆœ™\ÛÝ\˜ÙWÝ\HŽˆ›Ù™[[JÉÝ˜XÙIË	Ù\Ú›Ø\™	Ë	Ù]˜[Ü[‰Ë	Ù]\Ù]	Ë	Ü›Ú™XÝ	×JKˆœ™\ÛÝ\˜ÙWÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ˜XØÙ\Ü×Ý\HŽˆ›Ù™[[JÉÜX›XÉË	Ü™\ÝšXÝY	×JKˆ™]HŽˆ›Ù›Øš™XÝ
Âˆ˜XÙHŽˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆœ›Ú™XÝÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆš[œ]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Ý]]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

BŸJK›Ü[Û˜[

Kˆ›ØœÙ\˜][Û—ÜÜ[œÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

JK›Ü[Û˜[

KˆœÝ[[X\žHŽˆ›Ù›Øš™XÝ
ÂˆÝ[ÜÜ[œÈŽˆ›Ù›[X™\Š
K›Ü[Û˜[

BŸJK›Ü[Û˜[

KˆšYŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ˜XÙWÝ\HŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ›[Ù[Ý\HŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ˜ÛÛ™šYÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÙ\ÜÚ[Û—ØÛÛ™šYÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Ü™Ø[š^˜][ÛˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ\›Ü]Žˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ™\ØÜš\[ÛˆŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆÛÜšÜÜXÙHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ˜Ü™X]YØžHŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ\]YØžHŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ\]YØ]Žˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

KˆÚYÙ]ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

JK›Ü[Û˜[

KˆÚYÙ]ØÛÝ[Žˆ›Ù›[X™\Š
K›Ü[Û˜[

BŸJBŸJB‚‚™^ÜÛÛœÝ˜XÙ\•˜XÙP[››Ý][Û‘Ù][››Ý][Û•˜[Y\Ô]Y\žSØœÙ\˜][Û”Ü[’YX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\•˜XÙP[››Ý][Û‘Ù][››Ý][Û•˜[Y\Ô]Y\žP[››Ý]ÜœÑY˜][H×NÂ™^ÜÛÛœÝ˜XÙ\•˜XÙP[››Ý][Û‘Ù][››Ý][Û•˜[Y\Ô]Y\žQ^ÛYP[››Ý]ÜœÑY˜][H×NÂ‚™^ÜÛÛœÝ˜XÙ\•˜XÙP[››Ý][Û‘Ù][››Ý][Û•˜[Y\Ô]Y\žT\˜[\ÈH›Ù›Øš™XÝ
ÂˆœYÙHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	ÐHYÙH[X™\ˆÚ][ˆHYÚ[˜]Y™\Ý[Ù]‰ÊKˆ›[Z]Žˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	Ó[X™\ˆÙˆ™\Ý[ÈÈ™]\›ˆ\ˆYÙK‰ÊKˆ›ØœÙ\˜][Û—ÜÜ[—ÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\•˜XÙP[››Ý][Û‘Ù][››Ý][Û•˜[Y\Ô]Y\žSØœÙ\˜][Û”Ü[’YX^
K›Ü[Û˜[

Kˆ˜XÙWÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜[››Ý]ÜœÈŽˆ›ÙœÝš[™Ê
K™Y˜][
˜XÙ\•˜XÙP[››Ý][Û‘Ù][››Ý][Û•˜[Y\Ô]Y\žP[››Ý]ÜœÑY˜][
Kˆ™^ÛYWØ[››Ý]ÜœÈŽˆ›ÙœÝš[™Ê
K™Y˜][
˜XÙ\•˜XÙP[››Ý][Û‘Ù][››Ý][Û•˜[Y\Ô]Y\žQ^ÛYP[››Ý]ÜœÑY˜][
BŸJB‚™^ÜÛÛœÝ˜XÙ\•˜XÙP[››Ý][Û‘Ù][››Ý][Û•˜[Y\Ô™\ÜÛœÙTÝ]\ÑY˜][HYNÂ‚‚‚‚‚‚‚™^ÜÛÛœÝ˜XÙ\•˜XÙP[››Ý][Û‘Ù][››Ý][Û•˜[Y\Ô™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
K™Y˜][
˜XÙ\•˜XÙP[››Ý][Û‘Ù][››Ý][Û•˜[Y\Ô™\ÜÛœÙTÝ]\ÑY˜][
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆ˜[››Ý][ÛœÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ˜[››Ý][Û—ÛX™[Û˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ˜[››Ý][Û—Ý˜[YHŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

Kˆ˜[››Ý][Û—ÛX™[ÚYŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ˜[››Ý]ÜˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ˜[››Ý]Ü—ÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ\]YØžHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ\]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ˜[››Ý][Û—Ý\HŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœÙ][™ÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

BŸJJKˆ››Ý\ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ››Ý\ÈŽˆ›ÙœÝš[™Ê
Kˆ˜Ü™X]YØžWØ[››Ý]ÜˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ˜Ü™X]YØžWÝ\Ù\ˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ˜Ü™X]YØžWÝ\Ù\—ÚYŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ\]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JBŸJJBŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆÙ]\œ›Üˆ[˜[\Ú\È›ÜˆHÜXÚYšXÈ˜XÙB‘ÑUØ\KÝ˜XÙKY\œ›Ü‹X[˜[\Ú\ËÏ˜XÙWÚY‹Âˆ
‹Â™^ÜÛÛœÝ˜XÙ\•˜XÙQ\œ›Ü[˜[\Ú\Ô™XY\˜[\ÈH›Ù›Øš™XÝ
Âˆ˜XÙWÚYŽˆ›ÙœÝš[™Ê
BŸJB‚™^ÜÛÛœÝ˜XÙ\•˜XÙQ\œ›Ü[˜[\Ú\Ô™XY™\ÜÛœÙTÝ]\ÑY˜][HYNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\•˜XÙQ\œ›Ü[˜[\Ú\Ô™XY™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
K™Y˜][
˜XÙ\•˜XÙQ\œ›Ü[˜[\Ú\Ô™XY™\ÜÛœÙTÝ]\ÑY˜][
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆ˜[˜[\Ú\×Ù^\ÝÈŽˆ›Ù˜›ÛÛX[Š
Kˆ˜XÙWÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ›Y\ÜØYÙHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ˜[˜[\Ú\×ÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜[˜[\Ú\×Ù]HŽˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ˜YÙ[Ý™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ›Y[[ÜžWÙ[š[˜ÙYŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

KˆœÝ[[X\žHŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ™\œ›ÜœÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

JK›Ü[Û˜[

Kˆ™Ü›Ý\YÙ\œ›ÜœÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

JK›Ü[Û˜[

KˆœØÛÜ™\ÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Y[[ÜžWØÛÛ^Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

BŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆÙ]Ý\œ™[\ÚÈÛÛ™šYÝ\˜][Ûˆ›ÜˆH›Ú™XÝ‘ÑUØ\KÝ˜XÙKY\œ›Ü‹]\ÚËÏ›Ú™XÝÚY‹Âˆ
‹Â™^ÜÛÛœÝ˜XÙ\•˜XÙQ\œ›Ü•\ÚÔ™XY\˜[\ÈH›Ù›Øš™XÝ
Âˆœ›Ú™XÝÚYŽˆ›ÙœÝš[™Ê
BŸJB‚™^ÜÛÛœÝ˜XÙ\•˜XÙQ\œ›Ü•\ÚÔ™XY™\ÜÛœÙTÝ]\ÑY˜][HYNÂ‚‚™^ÜÛÛœÝ˜XÙ\•˜XÙQ\œ›Ü•\ÚÔ™XY™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
K™Y˜][
˜XÙ\•˜XÙQ\œ›Ü•\ÚÔ™XY™\ÜÛœÙTÝ]\ÑY˜][
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆœ›Ú™XÝÚYŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ›Ú™XÝÛ˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœØ[\[™×Ü˜]HŽˆ›Ù›[X™\Š
KˆœÝ]\ÈŽˆ›Ù™[[JÉÜ[›š[™ÉË	ÝØZ][™ÉË	Ü]\ÙY	×JKˆš\×ØXÝ]™HŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

KˆÝ[Ý˜XÙ\×Ø[˜[^™YŽˆ›Ù›[X™\Š
K›Ü[Û˜[

KˆÝ[Ù\œ›Üœ×Ù›Ý[™Žˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ™˜Z[YØ[˜[\Ù\ÈŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ›\ÝÜ[—Ø]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ˜Ü™X]YŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

BŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆ™\]Y\Ý›ÙN‚žÂˆœØ[\[™×Ü˜]HŽˆŒ‹ËÈ™\]Z\™YˆLBˆœÝ]\ÈŽˆØZ][™ÈˆËÈÜ[Û˜[ˆØZ][™ÈˆÜˆœ]\ÙY‚ŸBˆ
ˆÝ[[X\žH\]H\ÚÈÛÛ™šYÝ\˜][Ûˆ›ÜˆH›Ú™XÝ”ÔÕØ\KÝ˜XÙKY\œ›Ü‹]\ÚËÏ›Ú™XÝÚY‹Âˆ
‹Â™^ÜÛÛœÝ˜XÙ\•˜XÙQ\œ›Ü•\ÚÐÜ™X]T\˜[\ÈH›Ù›Øš™XÝ
Âˆœ›Ú™XÝÚYŽˆ›ÙœÝš[™Ê
BŸJB‚™^ÜÛÛœÝ˜XÙ\•˜XÙQ\œ›Ü•\ÚÐÜ™X]P›ÙTØ[\[™Ô˜]SZ[ˆHÂ™^ÜÛÛœÝ˜XÙ\•˜XÙQ\œ›Ü•\ÚÐÜ™X]P›ÙTØ[\[™Ô˜]SX^HNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\•˜XÙQ\œ›Ü•\ÚÐÜ™X]P›ÙHH›Ù›Øš™XÝ
ÂˆœØ[\[™×Ü˜]HŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\•˜XÙQ\œ›Ü•\ÚÐÜ™X]P›ÙTØ[\[™Ô˜]SZ[ŠK›X^
˜XÙ\•˜XÙQ\œ›Ü•\ÚÐÜ™X]P›ÙTØ[\[™Ô˜]SX^
KˆœÝ]\ÈŽˆ›Ù™[[JÉÝØZ][™ÉË	Ü]\ÙY	×JK›Ü[Û˜[

BŸJB‚™^ÜÛÛœÝ˜XÙ\•˜XÙQ\œ›Ü•\ÚÐÜ™X]T™\ÜÛœÙTÝ]\ÑY˜][HYNÂ‚‚‚‚™^ÜÛÛœÝ˜XÙ\•˜XÙQ\œ›Ü•\ÚÐÜ™X]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
K™Y˜][
˜XÙ\•˜XÙQ\œ›Ü•\ÚÐÜ™X]T™\ÜÛœÙTÝ]\ÑY˜][
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆ›Y\ÜØYÙHŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœ›Ú™XÝÚYŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ›Ú™XÝÛ˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœØ[\[™×Ü˜]HŽˆ›Ù›[X™\Š
KˆœÝ]\ÈŽˆ›Ù™[[JÉÜ[›š[™ÉË	ÝØZ][™ÉË	Ü]\ÙY	×JKˆ˜XÝ[ÛˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ›ÛÜ˜]HŽˆ›Ù›[X™\Š
Kˆ›™]×Ü˜]HŽˆ›Ù›[X™\Š
BŸJBŸJB‚‚™^ÜÛÛœÝ˜XÙ\•˜XÙTÙ\ÜÚ[Û“\Ý]Y\žT\˜[\ÈH›Ù›Øš™XÝ
ÂˆœYÙHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	ÐHYÙH[X™\ˆÚ][ˆHYÚ[˜]Y™\Ý[Ù]‰ÊKˆ›[Z]Žˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	Ó[X™\ˆÙˆ™\Ý[ÈÈ™]\›ˆ\ˆYÙK‰ÊBŸJB‚™^ÜÛÛœÝ˜XÙ\•˜XÙTÙ\ÜÚ[Û“\Ý™\ÜÛœÙT™\Ý[Ò][S˜[YSX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\•˜XÙTÙ\ÜÚ[Û“\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
Âˆ˜ÛÝ[Žˆ›Ù›[X™\Š
Kˆ›™^Žˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™]š[Ý\ÈŽˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™\Ý[ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ˜›ÛÚÛX\šÙYŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•˜XÙTÙ\ÜÚ[Û“\Ý™\ÜÛœÙT™\Ý[Ò][S˜[YSX^
K›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

BŸJJBŸJB‚‚™^ÜÛÛœÝ˜XÙ\•˜XÙTÙ\ÜÚ[ÛÜ™X]P›ÙS˜[YSX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\•˜XÙTÙ\ÜÚ[ÛÜ™X]P›ÙHH›Ù›Øš™XÝ
Âˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ˜›ÛÚÛX\šÙYŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•˜XÙTÙ\ÜÚ[ÛÜ™X]P›ÙS˜[YSX^
K›Ü[Û˜[

BŸJB‚‚‹ÊŠ‚ˆ
ˆ™]\›ˆ\Ý[˜Ý˜[Y\È›ÜˆHÙ\ÜÚ[Û‹[]™[ÛÛ[[‹‚•\ÙYžHHš[\ˆ[™[	ÜÈ˜[YHXÚÙ\ˆ›ÜˆÙ\ÜÚ[Û‹\ÜXÚYšXÈšY[ÂŠÙ\ÜÚ[Û—ÚY\Ù\—ÚYš\œÝÛY\ÜØYÙK]ËŠK‚‚”]Y\žH\˜[\Î‚ˆ›Ú™XÝÚYˆ™\]Z\™YˆÛÛ[[ŽˆØ[›ÛšXØ[Ù\ÜÚ[ÛˆÛÛ[[ˆ˜[YKK™ËˆœÙ\ÜÚ[Û—ÚY‚ˆÙX\˜ÚˆÜ[Û˜[ÙX\˜ÚÝXœÝš[™ÂˆYÙNˆYÙH[X™\ˆ
X˜\ÙY
KY˜][ˆYÙWÜÚ^™NˆY˜][Lˆ
‹Â™^ÜÛÛœÝ˜XÙ\•˜XÙTÙ\ÜÚ[Û‘Ù]Ù\ÜÚ[Û‘š[\•˜[Y\Ô]Y\žT\˜[\ÈH›Ù›Øš™XÝ
ÂˆœYÙHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	ÐHYÙH[X™\ˆÚ][ˆHYÚ[˜]Y™\Ý[Ù]‰ÊKˆ›[Z]Žˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	Ó[X™\ˆÙˆ™\Ý[ÈÈ™]\›ˆ\ˆYÙK‰ÊBŸJB‚™^ÜÛÛœÝ˜XÙ\•˜XÙTÙ\ÜÚ[Û‘Ù]Ù\ÜÚ[Û‘š[\•˜[Y\Ô™\ÜÛœÙT™\Ý[Ò][S˜[YSX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\•˜XÙTÙ\ÜÚ[Û‘Ù]Ù\ÜÚ[Û‘š[\•˜[Y\Ô™\ÜÛœÙHH›Ù›Øš™XÝ
Âˆ˜ÛÝ[Žˆ›Ù›[X™\Š
Kˆ›™^Žˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™]š[Ý\ÈŽˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™\Ý[ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ˜›ÛÚÛX\šÙYŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•˜XÙTÙ\ÜÚ[Û‘Ù]Ù\ÜÚ[Û‘š[\•˜[Y\Ô™\ÜÛœÙT™\Ý[Ò][S˜[YSX^
K›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

BŸJJBŸJB‚‚‹ÊŠ‚ˆ
ˆÝ\ÜÈHØ[YHY]šXÈ\\È\ÈH˜XÙHÜ˜\[™Ú[‚‹HÖTÕSWÓQU’PÎˆ][˜ÞKÚÙ[œËÛÜÝ\œ›Ü—Ü˜]KÙ\ÜÚ[Û—ØÛÝ[ˆ]™×Ù\˜][Û‹]™×Ý˜XÙ\×Ü\—ÜÙ\ÜÚ[Ûˆ8 %[YÙÜ™YØ]Y]Ù\ÜÚ[Ûˆ]™[‹HUSˆ]˜[ØÛÜ™\È]™\˜YÙYXÜ›ÜÜÈÙ\ÜÚ[ÛœÂ‹HS““ÕUSÓŽˆ[››Ý][ÛˆØÛÜ™\È]™\˜YÙYXÜ›ÜÜÈÙ\ÜÚ[ÛœÂ‚”™\ÜÛœÙHÚ\HX]Ú\È˜XÙHÜ˜\ˆÛY]šX×Û˜[YK]NˆÞÝ[Y\Ý[\˜[YKš[X\žWÝ˜Y™šXßW_Bˆ
ˆÝ[[X\žH™]Ú[YK\Ù\šY\ÈÙ\ÜÚ[ÛˆY]šXÜÈ›ÜˆHØœÙ\™HÜ˜\‚ˆ
‹Â™^ÜÛÛœÝ˜XÙ\•˜XÙTÙ\ÜÚ[Û‘Ù]Ù\ÜÚ[Û‘Ü˜\]P›ÙQš[\œÑY˜][H×NÂ™^ÜÛÛœÝ˜XÙ\•˜XÙTÙ\ÜÚ[Û‘Ù]Ù\ÜÚ[Û‘Ü˜\]P›ÙR[\˜[Y˜][H^XÂ™^ÜÛÛœÝ˜XÙ\•˜XÙTÙ\ÜÚ[Û‘Ù]Ù\ÜÚ[Û‘Ü˜\]P›ÙT›Ü\QY˜][H]™\˜YÙXÂ‚™^ÜÛÛœÝ˜XÙ\•˜XÙTÙ\ÜÚ[Û‘Ù]Ù\ÜÚ[Û‘Ü˜\]P›ÙHH›Ù›Øš™XÝ
Âˆœ›Ú™XÝÚYŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ™š[\œÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
Âˆ˜ÛÛ[[—ÚYŽˆ›ÙœÝš[™Ê
K™\ØÜšX™J	ÐÛÛ[[ˆÜˆ]šX]HYÈš[\ˆÛ‹‰ÊKˆ™\Ü^WÛ˜[YHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	ÓÜ[Û˜[RHX™[›ÜˆÚ\È[™Ø]™YšY]ÜË‰ÊKˆœÛÝ\˜ÙHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	ÓÜ[Û˜[ÛÝ\˜ÙHÝ\™˜XÙH›ÜˆZ^Y\ÛÝ\˜ÙHš[\œË›Üˆ^[\H˜XÙ\Ë]\Ù]ËÜˆÚ[][][Û‹‰ÊKˆ›Ý]]Ý\HŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	ÓÜ[Û˜[Y]šXÈÝ]]\HY]Y]H\ÙYžH]˜[[™[››Ý][Ûˆš[\œË‰ÊKˆ™š[\—ØÛÛ™šYÈŽˆ›Ù›Øš™XÝ
Âˆ™š[\—Ý\HŽˆ›ÙœÝš[™Ê
K™\ØÜšX™J	ÐØ[›ÛšXØ[šY[\K›Üˆ^[\H^[X™\‹›ÛÛX[‹]][YKØ]YÛÜšXØ[[XœË[››Ý]Ü‹Üˆ\œ˜^K‰ÊKˆ™š[\—ÛÜŽˆ›ÙœÝš[™Ê
K™\ØÜšX™J	ÐØ[›ÛšXØ[Ü\˜]Üˆœ›ÛH\WØÛÛ˜XÝ×Ùš[\—ØÛÛ˜XÝšœÛÛ‹›Üˆ^[\H\]X[Ë›ÝÙ\]X[Ë[‹›ÝÚ[‹™]ÙY[‹›ÝØ™]ÙY[‹\×Û[Üˆ\×Û›ÝÛ[‰ÊKˆ™š[\—Ý˜[YHŽˆ›Ù[šÛ›ÝÛŠ
K›Ü[Û˜[

K™\ØÜšX™J	ÔØØ[\‹\Ý˜[™ÙH\K›ÛÛX[‹Üˆ[\[™[™ÈÛˆš[\—ÛÜ[™š[\—Ý\K‰ÊKˆ˜ÛÛÝ\HŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	ÐÛÛ[[ˆ˜[Z[HÝXÚ\ÈÖTÕSWÓQU’PËÔS—ÐU’P•UKUSÓQU’PËS““ÕUSÓ‹Üˆ“Ô“PS‰ÊBŸJBŸJJK™Y˜][
˜XÙ\•˜XÙTÙ\ÜÚ[Û‘Ù]Ù\ÜÚ[Û‘Ü˜\]P›ÙQš[\œÑY˜][
Kˆš[\˜[Žˆ›Ù™[[JÉÚÝ\‰Ë	Ù^IË	ÝÙYZÉË	Û[Û	×JK™Y˜][
˜XÙ\•˜XÙTÙ\ÜÚ[Û‘Ù]Ù\ÜÚ[Û‘Ü˜\]P›ÙR[\˜[Y˜][
Kˆœ›Ü\HŽˆ›ÙœÝš[™Ê
K™Y˜][
˜XÙ\•˜XÙTÙ\ÜÚ[Û‘Ù]Ù\ÜÚ[Û‘Ü˜\]P›ÙT›Ü\QY˜][
Kˆœ™\WÙ]WØÛÛ™šYÈŽˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
Kˆ\HŽˆ›Ù™[[JÉÔÖTÕSWÓQU’PÉË	ÑUS	Ë	ÐS““ÕUSÓ‰×JKˆ›Ý]]Ý\HŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ™]˜[ÛÝ]]Ý\HŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ˜ÚÚXÙ\ÈŽˆ›Ù˜\œ˜^J›ÙœÝš[™Ê
JK›Ü[Û˜[

Kˆ˜[YHŽˆ›Ù[šÛ›ÝÛŠ
K›Ü[Û˜[

Kˆ™š[\—ÛÜŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ™š[\—Ý˜[YHŽˆ›Ù[šÛ›ÝÛŠ
K›Ü[Û˜[

BŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆ^Ü˜XÙ\Èš[\™YžH›Ú™XÝQ[™›Ú™XÝ™\œÚ[ÛˆQÚ]Ü[Z^™Y]Y\šY\Ë‚ˆ
‹Â™^ÜÛÛœÝ˜XÙ\•˜XÙTÙ\ÜÚ[Û‘Ù]˜XÙTÙ\ÜÚ[Û‘^Ü]T]Y\žT\˜[\ÈH›Ù›Øš™XÝ
ÂˆœYÙHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	ÐHYÙH[X™\ˆÚ][ˆHYÚ[˜]Y™\Ý[Ù]‰ÊKˆ›[Z]Žˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	Ó[X™\ˆÙˆ™\Ý[ÈÈ™]\›ˆ\ˆYÙK‰ÊBŸJB‚™^ÜÛÛœÝ˜XÙ\•˜XÙTÙ\ÜÚ[Û‘Ù]˜XÙTÙ\ÜÚ[Û‘^Ü]T™\ÜÛœÙT™\Ý[Ò][S˜[YSX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\•˜XÙTÙ\ÜÚ[Û‘Ù]˜XÙTÙ\ÜÚ[Û‘^Ü]T™\ÜÛœÙHH›Ù›Øš™XÝ
Âˆ˜ÛÝ[Žˆ›Ù›[X™\Š
Kˆ›™^Žˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™]š[Ý\ÈŽˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™\Ý[ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ˜›ÛÚÛX\šÙYŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•˜XÙTÙ\ÜÚ[Û‘Ù]˜XÙTÙ\ÜÚ[Û‘^Ü]T™\ÜÛœÙT™\Ý[Ò][S˜[YSX^
K›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

BŸJJBŸJB‚‚‹ÊŠ‚ˆ
ˆ\Ý˜XÙ\Èš[\™YžH›Ú™XÝQ[™›Ú™XÝ™\œÚ[ÛˆQÚ]Ü[Z^™Y]Y\šY\Ë‚ˆ
‹Â™^ÜÛÛœÝ˜XÙ\•˜XÙTÙ\ÜÚ[Û“\ÝÙ\ÜÚ[ÛœÔ]Y\žQš[\œÑY˜][H×XÂ‚™^ÜÛÛœÝ˜XÙ\•˜XÙTÙ\ÜÚ[Û“\ÝÙ\ÜÚ[ÛœÔ]Y\žTÛÜ\˜[\ÑY˜][H×XÂ‚™^ÜÛÛœÝ˜XÙ\•˜XÙTÙ\ÜÚ[Û“\ÝÙ\ÜÚ[ÛœÔ]Y\žTYÙS[X™\‘Y˜][HÂ™^ÜÛÛœÝ˜XÙ\•˜XÙTÙ\ÜÚ[Û“\ÝÙ\ÜÚ[ÛœÔ]Y\žTYÙS[X™\“Z[ˆHÂ‚™^ÜÛÛœÝ˜XÙ\•˜XÙTÙ\ÜÚ[Û“\ÝÙ\ÜÚ[ÛœÔ]Y\žTYÙTÚ^™QY˜][HÌÂ™^ÜÛÛœÝ˜XÙ\•˜XÙTÙ\ÜÚ[Û“\ÝÙ\ÜÚ[ÛœÔ]Y\žTYÙTÚ^™SX^HLÂ‚‚‚™^ÜÛÛœÝ˜XÙ\•˜XÙTÙ\ÜÚ[Û“\ÝÙ\ÜÚ[ÛœÔ]Y\žT\˜[\ÈH›Ù›Øš™XÝ
ÂˆœYÙHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	ÐHYÙH[X™\ˆÚ][ˆHYÚ[˜]Y™\Ý[Ù]‰ÊKˆ›[Z]Žˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	Ó[X™\ˆÙˆ™\Ý[ÈÈ™]\›ˆ\ˆYÙK‰ÊKˆœ›Ú™XÝÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ\Ù\—ÚYŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ˜›ÛÚÛX\šÙYŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ™š[\œÈŽˆ›ÙœÝš[™Ê
K›Z[ŠJK™Y˜][
˜XÙ\•˜XÙTÙ\ÜÚ[Û“\ÝÙ\ÜÚ[ÛœÔ]Y\žQš[\œÑY˜][
KˆœÛÜÜ\˜[\ÈŽˆ›ÙœÝš[™Ê
K›Z[ŠJK™Y˜][
˜XÙ\•˜XÙTÙ\ÜÚ[Û“\ÝÙ\ÜÚ[ÛœÔ]Y\žTÛÜ\˜[\ÑY˜][
KˆœYÙWÛ[X™\ˆŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\•˜XÙTÙ\ÜÚ[Û“\ÝÙ\ÜÚ[ÛœÔ]Y\žTYÙS[X™\“Z[ŠK™Y˜][
˜XÙ\•˜XÙTÙ\ÜÚ[Û“\ÝÙ\ÜÚ[ÛœÔ]Y\žTYÙS[X™\‘Y˜][
KˆœYÙWÜÚ^™HŽˆ›Ù›[X™\Š
K›Z[ŠJK›X^
˜XÙ\•˜XÙTÙ\ÜÚ[Û“\ÝÙ\ÜÚ[ÛœÔ]Y\žTYÙTÚ^™SX^
K™Y˜][
˜XÙ\•˜XÙTÙ\ÜÚ[Û“\ÝÙ\ÜÚ[ÛœÔ]Y\žTYÙTÚ^™QY˜][
Kˆš[\˜[Žˆ›ÙœÝš[™Ê
K›Ü[Û˜[

BŸJB‚™^ÜÛÛœÝ˜XÙ\•˜XÙTÙ\ÜÚ[Û“\ÝÙ\ÜÚ[ÛœÔ™\ÜÛœÙT™\Ý[Ò][S˜[YSX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\•˜XÙTÙ\ÜÚ[Û“\ÝÙ\ÜÚ[ÛœÔ™\ÜÛœÙHH›Ù›Øš™XÝ
Âˆ˜ÛÝ[Žˆ›Ù›[X™\Š
Kˆ›™^Žˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™]š[Ý\ÈŽˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™\Ý[ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ˜›ÛÚÛX\šÙYŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•˜XÙTÙ\ÜÚ[Û“\ÝÙ\ÜÚ[ÛœÔ™\ÜÛœÙT™\Ý[Ò][S˜[YSX^
K›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

BŸJJBŸJB‚‚™^ÜÛÛœÝ˜XÙ\•˜XÙTÙ\ÜÚ[Û”™XY\˜[\ÈH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
BŸJB‚™^ÜÛÛœÝ˜XÙ\•˜XÙTÙ\ÜÚ[Û”™XY™\ÜÛœÙS˜[YSX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\•˜XÙTÙ\ÜÚ[Û”™XY™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ˜›ÛÚÛX\šÙYŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•˜XÙTÙ\ÜÚ[Û”™XY™\ÜÛœÙS˜[YSX^
K›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

BŸJB‚‚™^ÜÛÛœÝ˜XÙ\•˜XÙTÙ\ÜÚ[Û•\]T\˜[\ÈH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
BŸJB‚™^ÜÛÛœÝ˜XÙ\•˜XÙTÙ\ÜÚ[Û•\]P›ÙS˜[YSX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\•˜XÙTÙ\ÜÚ[Û•\]P›ÙHH›Ù›Øš™XÝ
Âˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ˜›ÛÚÛX\šÙYŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•˜XÙTÙ\ÜÚ[Û•\]P›ÙS˜[YSX^
K›Ü[Û˜[

BŸJB‚™^ÜÛÛœÝ˜XÙ\•˜XÙTÙ\ÜÚ[Û•\]T™\ÜÛœÙS˜[YSX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\•˜XÙTÙ\ÜÚ[Û•\]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ˜›ÛÚÛX\šÙYŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•˜XÙTÙ\ÜÚ[Û•\]T™\ÜÛœÙS˜[YSX^
K›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

BŸJB‚‚™^ÜÛÛœÝ˜XÙ\•˜XÙTÙ\ÜÚ[Û”\X[\]T\˜[\ÈH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
BŸJB‚™^ÜÛÛœÝ˜XÙ\•˜XÙTÙ\ÜÚ[Û”\X[\]P›ÙS˜[YSX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\•˜XÙTÙ\ÜÚ[Û”\X[\]P›ÙHH›Ù›Øš™XÝ
Âˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ˜›ÛÚÛX\šÙYŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•˜XÙTÙ\ÜÚ[Û”\X[\]P›ÙS˜[YSX^
K›Ü[Û˜[

BŸJB‚™^ÜÛÛœÝ˜XÙ\•˜XÙTÙ\ÜÚ[Û”\X[\]T™\ÜÛœÙS˜[YSX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\•˜XÙTÙ\ÜÚ[Û”\X[\]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ˜›ÛÚÛX\šÙYŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•˜XÙTÙ\ÜÚ[Û”\X[\]T™\ÜÛœÙS˜[YSX^
K›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

BŸJB‚‚™^ÜÛÛœÝ˜XÙ\•˜XÙTÙ\ÜÚ[Û‘[]T\˜[\ÈH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
BŸJB‚‚‹ÊŠ‚ˆ
ˆÙ\ÜÚ[Û‹[]™[]˜[™\Ý[È\™HØ[YÙ™ˆœ›ÛHÜ[‹Ý˜XÙHÝ\™˜XÙ\Â˜žH\™Ù]Ý\OIÜÙ\ÜÚ[Û‰Ø8 %\È[™Ú[\ÈHÛ›HXÙB^H\X\‹‚‚”]Y\žH\˜[\Î‚ˆYÙH
[Z[™^YY˜][
BˆYÙWÜÚ^™H
[Y˜][KX^L
Bˆ
ˆÝ[[X\žHÙ\ÜÚ[Û‹\ØÛÜY]˜[ÙÈ™YY›Üˆ˜XÙ\Ñ˜]Ù\‰ÜÈ‘]˜[ÈˆX‹‚ˆ
‹Â™^ÜÛÛœÝ˜XÙ\•˜XÙTÙ\ÜÚ[Û‘]˜[ÙÜÔ\˜[\ÈH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
BŸJB‚™^ÜÛÛœÝ˜XÙ\•˜XÙTÙ\ÜÚ[Û‘]˜[ÙÜÔ™\ÜÛœÙS˜[YSX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\•˜XÙTÙ\ÜÚ[Û‘]˜[ÙÜÔ™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ˜›ÛÚÛX\šÙYŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•˜XÙTÙ\ÜÚ[Û‘]˜[ÙÜÔ™\ÜÛœÙS˜[YSX^
K›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

BŸJB‚‚™^ÜÛÛœÝ˜XÙ\•˜XÙS\Ý]Y\žT\˜[\ÈH›Ù›Øš™XÝ
ÂˆœYÙHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	ÐHYÙH[X™\ˆÚ][ˆHYÚ[˜]Y™\Ý[Ù]‰ÊKˆ›[Z]Žˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	Ó[X™\ˆÙˆ™\Ý[ÈÈ™]\›ˆ\ˆYÙK‰ÊBŸJB‚™^ÜÛÛœÝ˜XÙ\•˜XÙS\Ý™\ÜÛœÙT™\Ý[Ò][S˜[YSX^HŒÂ‚™^ÜÛÛœÝ˜XÙ\•˜XÙS\Ý™\ÜÛœÙT™\Ý[Ò][Q^\›˜[YX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\•˜XÙS\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
Âˆ˜ÛÝ[Žˆ›Ù›[X™\Š
Kˆ›™^Žˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™]š[Ý\ÈŽˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™\Ý[ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ›Ú™XÝÝ™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•˜XÙS\Ý™\ÜÛœÙT™\Ý[Ò][S˜[YSX^
K›Ü[Û˜[

Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆš[œ]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Ý]]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ™\œ›ÜˆŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÙ\ÜÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ™^\›˜[ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•˜XÙS\Ý™\ÜÛœÙT™\Ý[Ò][Q^\›˜[YX^
K›Ü[Û˜[

KˆYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

BŸJJBŸJB‚‚™^ÜÛÛœÝ˜XÙ\•˜XÙPÜ™X]P›ÙS˜[YSX^HŒÂ‚™^ÜÛÛœÝ˜XÙ\•˜XÙPÜ™X]P›ÙQ^\›˜[YX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\•˜XÙPÜ™X]P›ÙHH›Ù›Øš™XÝ
Âˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ›Ú™XÝÝ™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•˜XÙPÜ™X]P›ÙS˜[YSX^
K›Ü[Û˜[

Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆš[œ]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Ý]]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ™\œ›ÜˆŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÙ\ÜÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ™^\›˜[ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•˜XÙPÜ™X]P›ÙQ^\›˜[YX^
K›Ü[Û˜[

KˆYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

BŸJB‚‚‹ÊŠ‚ˆ
ˆÛÛ\]\È›Ù\È
\Ý[˜ÝÜ[ˆ\\ËÛ˜[Y\ÊH[™YÙ\È
\™[8¡¤˜Ú[˜[œÚ][ÛœÊHXÜ›ÜÜÈ[˜XÙ\È[ˆHÚ]™[ˆ[YHÚ[™ÝË‚ˆ
ˆÝ[[X\žH™]\›ˆHYÙÜ™YØ]HYÙ[Ü˜\›ÜˆH›Ú™XÝ‚ˆ
‹Â™^ÜÛÛœÝ˜XÙ\•˜XÙPYÙ[Ü˜\]Y\žQš[\œÑY˜][H×XÂ‚‚‚™^ÜÛÛœÝ˜XÙ\•˜XÙPYÙ[Ü˜\]Y\žT\˜[\ÈH›Ù›Øš™XÝ
ÂˆœYÙHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	ÐHYÙH[X™\ˆÚ][ˆHYÚ[˜]Y™\Ý[Ù]‰ÊKˆ›[Z]Žˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	Ó[X™\ˆÙˆ™\Ý[ÈÈ™]\›ˆ\ˆYÙK‰ÊKˆœ›Ú™XÝÚYŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ™š[\œÈŽˆ›ÙœÝš[™Ê
K›Z[ŠJK™Y˜][
˜XÙ\•˜XÙPYÙ[Ü˜\]Y\žQš[\œÑY˜][
BŸJB‚™^ÜÛÛœÝ˜XÙ\•˜XÙPYÙ[Ü˜\™\ÜÛœÙT™\Ý[Ò][S˜[YSX^HŒÂ‚™^ÜÛÛœÝ˜XÙ\•˜XÙPYÙ[Ü˜\™\ÜÛœÙT™\Ý[Ò][Q^\›˜[YX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\•˜XÙPYÙ[Ü˜\™\ÜÛœÙHH›Ù›Øš™XÝ
Âˆ˜ÛÝ[Žˆ›Ù›[X™\Š
Kˆ›™^Žˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™]š[Ý\ÈŽˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™\Ý[ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ›Ú™XÝÝ™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•˜XÙPYÙ[Ü˜\™\ÜÛœÙT™\Ý[Ò][S˜[YSX^
K›Ü[Û˜[

Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆš[œ]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Ý]]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ™\œ›ÜˆŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÙ\ÜÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ™^\›˜[ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•˜XÙPYÙ[Ü˜\™\ÜÛœÙT™\Ý[Ò][Q^\›˜[YX^
K›Ü[Û˜[

KˆYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

BŸJJBŸJB‚‚™^ÜÛÛœÝ˜XÙ\•˜XÙP[ÐÜ™X]P›ÙS˜[YSX^HŒÂ‚™^ÜÛÛœÝ˜XÙ\•˜XÙP[ÐÜ™X]P›ÙQ^\›˜[YX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\•˜XÙP[ÐÜ™X]P›ÙHH›Ù›Øš™XÝ
Âˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ›Ú™XÝÝ™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•˜XÙP[ÐÜ™X]P›ÙS˜[YSX^
K›Ü[Û˜[

Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆš[œ]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Ý]]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ™\œ›ÜˆŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÙ\ÜÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ™^\›˜[ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•˜XÙP[ÐÜ™X]P›ÙQ^\›˜[YX^
K›Ü[Û˜[

KˆYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

BŸJB‚‚‹ÊŠ‚ˆ
ˆÛÛ\\™H˜XÙ\ÈXÜ›ÜÜÈ›Ú™XÝ™\œÚ[ÛœÈÚ]Ü[Z^™Y]Y\šY\Ë‚ˆ
‹Â™^ÜÛÛœÝ˜XÙ\•˜XÙPÛÛ\\™U˜XÙ\Ð›ÙS˜[YSX^HŒÂ‚™^ÜÛÛœÝ˜XÙ\•˜XÙPÛÛ\\™U˜XÙ\Ð›ÙQ^\›˜[YX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\•˜XÙPÛÛ\\™U˜XÙ\Ð›ÙHH›Ù›Øš™XÝ
Âˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ›Ú™XÝÝ™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•˜XÙPÛÛ\\™U˜XÙ\Ð›ÙS˜[YSX^
K›Ü[Û˜[

Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆš[œ]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Ý]]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ™\œ›ÜˆŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÙ\ÜÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ™^\›˜[ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•˜XÙPÛÛ\\™U˜XÙ\Ð›ÙQ^\›˜[YX^
K›Ü[Û˜[

KˆYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

BŸJB‚‚‹ÊŠ‚ˆ
ˆ™]Ú[]˜[X][Ûˆ[\]H˜[Y\Ë‚ˆ
‹Â™^ÜÛÛœÝ˜XÙ\•˜XÙQÙ]]˜[˜[Y\Ô]Y\žT\˜[\ÈH›Ù›Øš™XÝ
ÂˆœYÙHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	ÐHYÙH[X™\ˆÚ][ˆHYÚ[˜]Y™\Ý[Ù]‰ÊKˆ›[Z]Žˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	Ó[X™\ˆÙˆ™\Ý[ÈÈ™]\›ˆ\ˆYÙK‰ÊBŸJB‚™^ÜÛÛœÝ˜XÙ\•˜XÙQÙ]]˜[˜[Y\Ô™\ÜÛœÙT™\Ý[Ò][S˜[YSX^HŒÂ‚™^ÜÛÛœÝ˜XÙ\•˜XÙQÙ]]˜[˜[Y\Ô™\ÜÛœÙT™\Ý[Ò][Q^\›˜[YX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\•˜XÙQÙ]]˜[˜[Y\Ô™\ÜÛœÙHH›Ù›Øš™XÝ
Âˆ˜ÛÝ[Žˆ›Ù›[X™\Š
Kˆ›™^Žˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™]š[Ý\ÈŽˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™\Ý[ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ›Ú™XÝÝ™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•˜XÙQÙ]]˜[˜[Y\Ô™\ÜÛœÙT™\Ý[Ò][S˜[YSX^
K›Ü[Û˜[

Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆš[œ]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Ý]]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ™\œ›ÜˆŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÙ\ÜÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ™^\›˜[ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•˜XÙQÙ]]˜[˜[Y\Ô™\ÜÛœÙT™\Ý[Ò][Q^\›˜[YX^
K›Ü[Û˜[

KˆYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

BŸJJBŸJB‚‚‹ÊŠ‚ˆ
ˆ™]Ú]H›ÜˆHØœÙ\™HÜ˜\Ú]Ü[Z^™Y]Y\šY\Âˆ
‹Â™^ÜÛÛœÝ˜XÙ\•˜XÙQÙ]Ü˜\Y]ÙÐ›ÙQš[\œÑY˜][H×NÂ™^ÜÛÛœÝ˜XÙ\•˜XÙQÙ]Ü˜\Y]ÙÐ›ÙR[\˜[Y˜][H^XÂ™^ÜÛÛœÝ˜XÙ\•˜XÙQÙ]Ü˜\Y]ÙÐ›ÙT›Ü\QY˜][H]™\˜YÙXÂ‚™^ÜÛÛœÝ˜XÙ\•˜XÙQÙ]Ü˜\Y]ÙÐ›ÙHH›Ù›Øš™XÝ
Âˆœ›Ú™XÝÚYŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ™š[\œÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
Âˆ˜ÛÛ[[—ÚYŽˆ›ÙœÝš[™Ê
K™\ØÜšX™J	ÐÛÛ[[ˆÜˆ]šX]HYÈš[\ˆÛ‹‰ÊKˆ™\Ü^WÛ˜[YHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	ÓÜ[Û˜[RHX™[›ÜˆÚ\È[™Ø]™YšY]ÜË‰ÊKˆœÛÝ\˜ÙHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	ÓÜ[Û˜[ÛÝ\˜ÙHÝ\™˜XÙH›ÜˆZ^Y\ÛÝ\˜ÙHš[\œË›Üˆ^[\H˜XÙ\Ë]\Ù]ËÜˆÚ[][][Û‹‰ÊKˆ›Ý]]Ý\HŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	ÓÜ[Û˜[Y]šXÈÝ]]\HY]Y]H\ÙYžH]˜[[™[››Ý][Ûˆš[\œË‰ÊKˆ™š[\—ØÛÛ™šYÈŽˆ›Ù›Øš™XÝ
Âˆ™š[\—Ý\HŽˆ›ÙœÝš[™Ê
K™\ØÜšX™J	ÐØ[›ÛšXØ[šY[\K›Üˆ^[\H^[X™\‹›ÛÛX[‹]][YKØ]YÛÜšXØ[[XœË[››Ý]Ü‹Üˆ\œ˜^K‰ÊKˆ™š[\—ÛÜŽˆ›ÙœÝš[™Ê
K™\ØÜšX™J	ÐØ[›ÛšXØ[Ü\˜]Üˆœ›ÛH\WØÛÛ˜XÝ×Ùš[\—ØÛÛ˜XÝšœÛÛ‹›Üˆ^[\H\]X[Ë›ÝÙ\]X[Ë[‹›ÝÚ[‹™]ÙY[‹›ÝØ™]ÙY[‹\×Û[Üˆ\×Û›ÝÛ[‰ÊKˆ™š[\—Ý˜[YHŽˆ›Ù[šÛ›ÝÛŠ
K›Ü[Û˜[

K™\ØÜšX™J	ÔØØ[\‹\Ý˜[™ÙH\K›ÛÛX[‹Üˆ[\[™[™ÈÛˆš[\—ÛÜ[™š[\—Ý\K‰ÊKˆ˜ÛÛÝ\HŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	ÐÛÛ[[ˆ˜[Z[HÝXÚ\ÈÖTÕSWÓQU’PËÔS—ÐU’P•UKUSÓQU’PËS““ÕUSÓ‹Üˆ“Ô“PS‰ÊBŸJBŸJJK™Y˜][
˜XÙ\•˜XÙQÙ]Ü˜\Y]ÙÐ›ÙQš[\œÑY˜][
Kˆš[\˜[Žˆ›Ù™[[JÉÚÝ\‰Ë	Ù^IË	ÝÙYZÉË	Û[Û	×JK™Y˜][
˜XÙ\•˜XÙQÙ]Ü˜\Y]ÙÐ›ÙR[\˜[Y˜][
Kˆœ›Ü\HŽˆ›ÙœÝš[™Ê
K™Y˜][
˜XÙ\•˜XÙQÙ]Ü˜\Y]ÙÐ›ÙT›Ü\QY˜][
Kˆœ™\WÙ]WØÛÛ™šYÈŽˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
Kˆ\HŽˆ›Ù™[[JÉÔÖTÕSWÓQU’PÉË	ÑUS	Ë	ÐS““ÕUSÓ‰×JKˆ›Ý]]Ý\HŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ™]˜[ÛÝ]]Ý\HŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ˜ÚÚXÙ\ÈŽˆ›Ù˜\œ˜^J›ÙœÝš[™Ê
JK›Ü[Û˜[

Kˆ˜[YHŽˆ›Ù[šÛ›ÝÛŠ
K›Ü[Û˜[

Kˆ™š[\—ÛÜŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ™š[\—Ý˜[YHŽˆ›Ù[šÛ›ÝÛŠ
K›Ü[Û˜[

BŸJBŸJB‚™^ÜÛÛœÝ˜XÙ\•˜XÙQÙ]Ü˜\Y]ÙÔ™\ÜÛœÙTÝ]\ÑY˜][HYNÂ‚‚™^ÜÛÛœÝ˜XÙ\•˜XÙQÙ]Ü˜\Y]ÙÔ™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
K™Y˜][
˜XÙ\•˜XÙQÙ]Ü˜\Y]ÙÔ™\ÜÛœÙTÝ]\ÑY˜][
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆ›Y]šX×Û˜[YHŽˆ›ÙœÝš[™Ê
Kˆ™]HŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
Âˆ[Y\Ý[\Žˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ˜[YHŽˆ›Ù›[X™\Š
Kˆœš[X\žWÝ˜Y™šXÈŽˆ›Ù›[X™\Š
K›Ü[Û˜[

BŸJJBŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆ™]Ú[›Ü\Y\È›ÜˆÜ˜\[™Ë‚ˆ
‹Â™^ÜÛÛœÝ˜XÙ\•˜XÙQÙ]›Ü\Y\Ô]Y\žT\˜[\ÈH›Ù›Øš™XÝ
ÂˆœYÙHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	ÐHYÙH[X™\ˆÚ][ˆHYÚ[˜]Y™\Ý[Ù]‰ÊKˆ›[Z]Žˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	Ó[X™\ˆÙˆ™\Ý[ÈÈ™]\›ˆ\ˆYÙK‰ÊBŸJB‚™^ÜÛÛœÝ˜XÙ\•˜XÙQÙ]›Ü\Y\Ô™\ÜÛœÙT™\Ý[Ò][S˜[YSX^HŒÂ‚™^ÜÛÛœÝ˜XÙ\•˜XÙQÙ]›Ü\Y\Ô™\ÜÛœÙT™\Ý[Ò][Q^\›˜[YX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\•˜XÙQÙ]›Ü\Y\Ô™\ÜÛœÙHH›Ù›Øš™XÝ
Âˆ˜ÛÝ[Žˆ›Ù›[X™\Š
Kˆ›™^Žˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™]š[Ý\ÈŽˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™\Ý[ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ›Ú™XÝÝ™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•˜XÙQÙ]›Ü\Y\Ô™\ÜÛœÙT™\Ý[Ò][S˜[YSX^
K›Ü[Û˜[

Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆš[œ]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Ý]]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ™\œ›ÜˆŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÙ\ÜÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ™^\›˜[ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•˜XÙQÙ]›Ü\Y\Ô™\ÜÛœÙT™\Ý[Ò][Q^\›˜[YX^
K›Ü[Û˜[

KˆYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

BŸJJBŸJB‚‚‹ÊŠ‚ˆ
ˆ^Ü˜XÙ\Èš[\™YžH›Ú™XÝQÚ]Ü[Z^™Y]Y\šY\Ë‚]]ËY]XÝÈ›ÚXÙKØÛÛ™\œØ][Ûˆ›Ú™XÝÈ[™^ÜÈ›ÚXÙK\ÜXÚYšXÈšY[Ë‚ˆ
‹Â™^ÜÛÛœÝ˜XÙ\•˜XÙQÙ]˜XÙQ^Ü]T]Y\žT\˜[\ÈH›Ù›Øš™XÝ
ÂˆœYÙHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	ÐHYÙH[X™\ˆÚ][ˆHYÚ[˜]Y™\Ý[Ù]‰ÊKˆ›[Z]Žˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	Ó[X™\ˆÙˆ™\Ý[ÈÈ™]\›ˆ\ˆYÙK‰ÊBŸJB‚™^ÜÛÛœÝ˜XÙ\•˜XÙQÙ]˜XÙQ^Ü]T™\ÜÛœÙT™\Ý[Ò][S˜[YSX^HŒÂ‚™^ÜÛÛœÝ˜XÙ\•˜XÙQÙ]˜XÙQ^Ü]T™\ÜÛœÙT™\Ý[Ò][Q^\›˜[YX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\•˜XÙQÙ]˜XÙQ^Ü]T™\ÜÛœÙHH›Ù›Øš™XÝ
Âˆ˜ÛÝ[Žˆ›Ù›[X™\Š
Kˆ›™^Žˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™]š[Ý\ÈŽˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™\Ý[ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ›Ú™XÝÝ™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•˜XÙQÙ]˜XÙQ^Ü]T™\ÜÛœÙT™\Ý[Ò][S˜[YSX^
K›Ü[Û˜[

Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆš[œ]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Ý]]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ™\œ›ÜˆŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÙ\ÜÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ™^\›˜[ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•˜XÙQÙ]˜XÙQ^Ü]T™\ÜÛœÙT™\Ý[Ò][Q^\›˜[YX^
K›Ü[Û˜[

KˆYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

BŸJJBŸJB‚‚‹ÊŠ‚ˆ
ˆÙ]H™]š[Ý\È[™™^˜XÙHYžH[™^\Ú[™ÈY™šXÚY[]X˜\ÙH]Y\šY\Ë‚ˆ
‹Â™^ÜÛÛœÝ˜XÙ\•˜XÙQÙ]˜XÙRYžR[™^]Y\žQš[\œÑY˜][H×XÂ‚‚‚™^ÜÛÛœÝ˜XÙ\•˜XÙQÙ]˜XÙRYžR[™^]Y\žT\˜[\ÈH›Ù›Øš™XÝ
ÂˆœYÙHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	ÐHYÙH[X™\ˆÚ][ˆHYÚ[˜]Y™\Ý[Ù]‰ÊKˆ›[Z]Žˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	Ó[X™\ˆÙˆ™\Ý[ÈÈ™]\›ˆ\ˆYÙK‰ÊKˆ˜XÙWÚYŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ›Ú™XÝÝ™\œÚ[Û—ÚYŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ™š[\œÈŽˆ›ÙœÝš[™Ê
K›Z[ŠJK™Y˜][
˜XÙ\•˜XÙQÙ]˜XÙRYžR[™^]Y\žQš[\œÑY˜][
BŸJB‚™^ÜÛÛœÝ˜XÙ\•˜XÙQÙ]˜XÙRYžR[™^™\ÜÛœÙT™\Ý[Ò][S˜[YSX^HŒÂ‚™^ÜÛÛœÝ˜XÙ\•˜XÙQÙ]˜XÙRYžR[™^™\ÜÛœÙT™\Ý[Ò][Q^\›˜[YX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\•˜XÙQÙ]˜XÙRYžR[™^™\ÜÛœÙHH›Ù›Øš™XÝ
Âˆ˜ÛÝ[Žˆ›Ù›[X™\Š
Kˆ›™^Žˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™]š[Ý\ÈŽˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™\Ý[ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ›Ú™XÝÝ™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•˜XÙQÙ]˜XÙRYžR[™^™\ÜÛœÙT™\Ý[Ò][S˜[YSX^
K›Ü[Û˜[

Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆš[œ]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Ý]]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ™\œ›ÜˆŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÙ\ÜÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ™^\›˜[ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•˜XÙQÙ]˜XÙRYžR[™^™\ÜÛœÙT™\Ý[Ò][Q^\›˜[YX^
K›Ü[Û˜[

KˆYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

BŸJJBŸJB‚‚‹ÊŠ‚ˆ
ˆÙ]H™]š[Ý\È[™™^˜XÙHYžH[™^‚ˆ
‹Â™^ÜÛÛœÝ˜XÙ\•˜XÙQÙ]˜XÙRYžR[™^ØœÙ\™T]Y\žQš[\œÑY˜][H×XÂ‚‚‚™^ÜÛÛœÝ˜XÙ\•˜XÙQÙ]˜XÙRYžR[™^ØœÙ\™T]Y\žT\˜[\ÈH›Ù›Øš™XÝ
ÂˆœYÙHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	ÐHYÙH[X™\ˆÚ][ˆHYÚ[˜]Y™\Ý[Ù]‰ÊKˆ›[Z]Žˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	Ó[X™\ˆÙˆ™\Ý[ÈÈ™]\›ˆ\ˆYÙK‰ÊKˆ˜XÙWÚYŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ›Ú™XÝÚYŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ™š[\œÈŽˆ›ÙœÝš[™Ê
K›Z[ŠJK™Y˜][
˜XÙ\•˜XÙQÙ]˜XÙRYžR[™^ØœÙ\™T]Y\žQš[\œÑY˜][
BŸJB‚™^ÜÛÛœÝ˜XÙ\•˜XÙQÙ]˜XÙRYžR[™^ØœÙ\™T™\ÜÛœÙT™\Ý[Ò][S˜[YSX^HŒÂ‚™^ÜÛÛœÝ˜XÙ\•˜XÙQÙ]˜XÙRYžR[™^ØœÙ\™T™\ÜÛœÙT™\Ý[Ò][Q^\›˜[YX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\•˜XÙQÙ]˜XÙRYžR[™^ØœÙ\™T™\ÜÛœÙHH›Ù›Øš™XÝ
Âˆ˜ÛÝ[Žˆ›Ù›[X™\Š
Kˆ›™^Žˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™]š[Ý\ÈŽˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™\Ý[ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ›Ú™XÝÝ™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•˜XÙQÙ]˜XÙRYžR[™^ØœÙ\™T™\ÜÛœÙT™\Ý[Ò][S˜[YSX^
K›Ü[Û˜[

Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆš[œ]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Ý]]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ™\œ›ÜˆŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÙ\ÜÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ™^\›˜[ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•˜XÙQÙ]˜XÙRYžR[™^ØœÙ\™T™\ÜÛœÙT™\Ý[Ò][Q^\›˜[YX^
K›Ü[Û˜[

KˆYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

BŸJJBŸJB‚‚‹ÊŠ‚ˆ
ˆ\Ý˜XÙ\Èš[\™YžH›Ú™XÝQ[™›Ú™XÝ™\œÚ[ÛˆQÚ]Ü[Z^™Y]Y\šY\Ë‚ˆ
‹Â™^ÜÛÛœÝ˜XÙ\•˜XÙS\Ý˜XÙ\Ô]Y\žU˜XÙRYÑY˜][H×NÂ™^ÜÛÛœÝ˜XÙ\•˜XÙS\Ý˜XÙ\Ô]Y\žQš[\œÑY˜][H×XÂ‚™^ÜÛÛœÝ˜XÙ\•˜XÙS\Ý˜XÙ\Ô]Y\žTÛÜ\˜[\ÑY˜][H×XÂ‚™^ÜÛÛœÝ˜XÙ\•˜XÙS\Ý˜XÙ\Ô]Y\žTYÙS[X™\‘Y˜][HÂ™^ÜÛÛœÝ˜XÙ\•˜XÙS\Ý˜XÙ\Ô]Y\žTYÙS[X™\“Z[ˆHÂ‚™^ÜÛÛœÝ˜XÙ\•˜XÙS\Ý˜XÙ\Ô]Y\žTYÙTÚ^™QY˜][HÌÂ™^ÜÛÛœÝ˜XÙ\•˜XÙS\Ý˜XÙ\Ô]Y\žTYÙTÚ^™SX^HLÂ‚‚‚™^ÜÛÛœÝ˜XÙ\•˜XÙS\Ý˜XÙ\Ô]Y\žT\˜[\ÈH›Ù›Øš™XÝ
ÂˆœYÙHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	ÐHYÙH[X™\ˆÚ][ˆHYÚ[˜]Y™\Ý[Ù]‰ÊKˆ›[Z]Žˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	Ó[X™\ˆÙˆ™\Ý[ÈÈ™]\›ˆ\ˆYÙK‰ÊKˆœ›Ú™XÝÝ™\œÚ[Û—ÚYŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ˜XÙWÚYÈŽˆ›ÙœÝš[™Ê
K™Y˜][
˜XÙ\•˜XÙS\Ý˜XÙ\Ô]Y\žU˜XÙRYÑY˜][
Kˆ™š[\œÈŽˆ›ÙœÝš[™Ê
K›Z[ŠJK™Y˜][
˜XÙ\•˜XÙS\Ý˜XÙ\Ô]Y\žQš[\œÑY˜][
KˆœÛÜÜ\˜[\ÈŽˆ›ÙœÝš[™Ê
K›Z[ŠJK™Y˜][
˜XÙ\•˜XÙS\Ý˜XÙ\Ô]Y\žTÛÜ\˜[\ÑY˜][
KˆœYÙWÛ[X™\ˆŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\•˜XÙS\Ý˜XÙ\Ô]Y\žTYÙS[X™\“Z[ŠK™Y˜][
˜XÙ\•˜XÙS\Ý˜XÙ\Ô]Y\žTYÙS[X™\‘Y˜][
KˆœYÙWÜÚ^™HŽˆ›Ù›[X™\Š
K›Z[ŠJK›X^
˜XÙ\•˜XÙS\Ý˜XÙ\Ô]Y\žTYÙTÚ^™SX^
K™Y˜][
˜XÙ\•˜XÙS\Ý˜XÙ\Ô]Y\žTYÙTÚ^™QY˜][
BŸJB‚™^ÜÛÛœÝ˜XÙ\•˜XÙS\Ý˜XÙ\Ô™\ÜÛœÙT™\Ý[Ò][S˜[YSX^HŒÂ‚™^ÜÛÛœÝ˜XÙ\•˜XÙS\Ý˜XÙ\Ô™\ÜÛœÙT™\Ý[Ò][Q^\›˜[YX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\•˜XÙS\Ý˜XÙ\Ô™\ÜÛœÙHH›Ù›Øš™XÝ
Âˆ˜ÛÝ[Žˆ›Ù›[X™\Š
Kˆ›™^Žˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™]š[Ý\ÈŽˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™\Ý[ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ›Ú™XÝÝ™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•˜XÙS\Ý˜XÙ\Ô™\ÜÛœÙT™\Ý[Ò][S˜[YSX^
K›Ü[Û˜[

Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆš[œ]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Ý]]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ™\œ›ÜˆŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÙ\ÜÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ™^\›˜[ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•˜XÙS\Ý˜XÙ\Ô™\ÜÛœÙT™\Ý[Ò][Q^\›˜[YX^
K›Ü[Û˜[

KˆYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

BŸJJBŸJB‚‚‹ÊŠ‚ˆ
ˆ\Ý˜XÙ\Èš[\™YžH›Ú™XÝQÚ]Ü[Z^™Y]Y\šY\Ë‚ˆ
‹Â™^ÜÛÛœÝ˜XÙ\•˜XÙS\Ý˜XÙ\ÓÙ”Ù\ÜÚ[Û”]Y\žQš[\œÑY˜][H×XÂ‚™^ÜÛÛœÝ˜XÙ\•˜XÙS\Ý˜XÙ\ÓÙ”Ù\ÜÚ[Û”]Y\žTYÙS[X™\‘Y˜][HÂ™^ÜÛÛœÝ˜XÙ\•˜XÙS\Ý˜XÙ\ÓÙ”Ù\ÜÚ[Û”]Y\žTYÙS[X™\“Z[ˆHÂ‚™^ÜÛÛœÝ˜XÙ\•˜XÙS\Ý˜XÙ\ÓÙ”Ù\ÜÚ[Û”]Y\žTYÙTÚ^™QY˜][HÌÂ™^ÜÛÛœÝ˜XÙ\•˜XÙS\Ý˜XÙ\ÓÙ”Ù\ÜÚ[Û”]Y\žTYÙTÚ^™SX^HLÂ‚‚‚™^ÜÛÛœÝ˜XÙ\•˜XÙS\Ý˜XÙ\ÓÙ”Ù\ÜÚ[Û”]Y\žT\˜[\ÈH›Ù›Øš™XÝ
ÂˆœYÙHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	ÐHYÙH[X™\ˆÚ][ˆHYÚ[˜]Y™\Ý[Ù]‰ÊKˆ›[Z]Žˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	Ó[X™\ˆÙˆ™\Ý[ÈÈ™]\›ˆ\ˆYÙK‰ÊKˆœ›Ú™XÝÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Ú™XÝÝ™\œÚ[Û—ÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

KˆœÙ\ÜÚ[Û—ÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ™š[\œÈŽˆ›ÙœÝš[™Ê
K›Z[ŠJK™Y˜][
˜XÙ\•˜XÙS\Ý˜XÙ\ÓÙ”Ù\ÜÚ[Û”]Y\žQš[\œÑY˜][
KˆœYÙWÛ[X™\ˆŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\•˜XÙS\Ý˜XÙ\ÓÙ”Ù\ÜÚ[Û”]Y\žTYÙS[X™\“Z[ŠK™Y˜][
˜XÙ\•˜XÙS\Ý˜XÙ\ÓÙ”Ù\ÜÚ[Û”]Y\žTYÙS[X™\‘Y˜][
KˆœYÙWÜÚ^™HŽˆ›Ù›[X™\Š
K›Z[ŠJK›X^
˜XÙ\•˜XÙS\Ý˜XÙ\ÓÙ”Ù\ÜÚ[Û”]Y\žTYÙTÚ^™SX^
K™Y˜][
˜XÙ\•˜XÙS\Ý˜XÙ\ÓÙ”Ù\ÜÚ[Û”]Y\žTYÙTÚ^™QY˜][
Kˆš[\˜[Žˆ›ÙœÝš[™Ê
K›Ü[Û˜[

BŸJB‚‚‚‚‚‚‚‚‚‚‚‚™^ÜÛÛœÝ˜XÙ\•˜XÙS\Ý˜XÙ\ÓÙ”Ù\ÜÚ[Û”™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
ÂˆÝ[Ü›ÝÜÈŽˆ›Ù›[X™\Š
BŸJKˆX›HŽˆ›Ù˜\œ˜^J›Ùœ™XÛÜ™
›ÙœÝš[™Ê
K›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K™\ØÜšX™J	Ð[žH˜[Y”ÓÓˆ˜[YK‰ÊJJKˆ˜ÛÛ™šYÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆš\×Ýš\ÚX›HŽˆ›Ù˜›ÛÛX[Š
Kˆ™Ü›Ý\ØžHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ›Ý]]Ý\HŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆœ™]™\œÙWÛÝ]]Žˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ˜[››Ý][Û—ÛX™[Ý\HŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ˜ÚÚXÙ\ÈŽˆ›Ù˜\œ˜^J›ÙœÝš[™Ê
K›Z[ŠJJK›Ü[Û˜[

KˆœÙ][™ÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ð[žH˜[Y”ÓÓˆ˜[YK‰ÊKˆ˜ÚÚXÙ\×ÛX\Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ð[žH˜[Y”ÓÓˆ˜[YK‰ÊKˆ™]˜[Ý[\]WÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ˜[››Ý]ÜœÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ð[žH˜[Y”ÓÓˆ˜[YK‰ÊKˆœÛÝ\˜ÙWÙšY[Žˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆœ\™[Ù]˜[ÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

BŸJJBŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆ\Ý›ÚXÙKØÛÛ™\œØ][Ûˆ˜XÙ\È›ÜˆH›Ú™XÝ[ˆ[ˆÜ[Z^™YØ^H[™œ™]\›ˆH™\ÜÛœÙHÚ[Z[\ˆÈH›ÝšYYØ[Øš™XÝØÚ[XK‚‚”]Y\žH\˜[\Î‚‹H›Ú™XÝÚY
™\]Z\™Y
B‹HYÙH
KX˜\ÙYÜ[Û˜[Y˜][JB‹HYÙWÜÚ^™H
Ü[Û˜[Y˜][Ì
Bˆ
‹Â™^ÜÛÛœÝ˜XÙ\•˜XÙS\Ý›ÚXÙPØ[Ô]Y\žT\˜[\ÈH›Ù›Øš™XÝ
ÂˆœYÙHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	ÐHYÙH[X™\ˆÚ][ˆHYÚ[˜]Y™\Ý[Ù]‰ÊKˆ›[Z]Žˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	Ó[X™\ˆÙˆ™\Ý[ÈÈ™]\›ˆ\ˆYÙK‰ÊBŸJB‚™^ÜÛÛœÝ˜XÙ\•˜XÙS\Ý›ÚXÙPØ[Ô™\ÜÛœÙT™\Ý[Ò][S˜[YSX^HŒÂ‚™^ÜÛÛœÝ˜XÙ\•˜XÙS\Ý›ÚXÙPØ[Ô™\ÜÛœÙT™\Ý[Ò][Q^\›˜[YX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\•˜XÙS\Ý›ÚXÙPØ[Ô™\ÜÛœÙHH›Ù›Øš™XÝ
Âˆ˜ÛÝ[Žˆ›Ù›[X™\Š
Kˆ›™^Žˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™]š[Ý\ÈŽˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™\Ý[ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ›Ú™XÝÝ™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•˜XÙS\Ý›ÚXÙPØ[Ô™\ÜÛœÙT™\Ý[Ò][S˜[YSX^
K›Ü[Û˜[

Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆš[œ]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Ý]]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ™\œ›ÜˆŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÙ\ÜÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ™^\›˜[ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•˜XÙS\Ý›ÚXÙPØ[Ô™\ÜÛœÙT™\Ý[Ò][Q^\›˜[YX^
K›Ü[Û˜[

KˆYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

BŸJJBŸJB‚‚‹ÊŠ‚ˆ
ˆ]Y\žH\˜[\Î‚‹H˜XÙWÚY
™\]Z\™Y
H8 %URQÙˆH›ÚXÙHØ[˜XÙK‚ˆ
ˆÝ[[X\žH™]\›ˆHX]žHÈ]Z[[Û›HšY[È›ÜˆHÚ[™ÛH›ÚXÙHØ[‚ˆ
‹Â™^ÜÛÛœÝ˜XÙ\•˜XÙU›ÚXÙPØ[]Z[]Y\žT\˜[\ÈH›Ù›Øš™XÝ
ÂˆœYÙHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	ÐHYÙH[X™\ˆÚ][ˆHYÚ[˜]Y™\Ý[Ù]‰ÊKˆ›[Z]Žˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	Ó[X™\ˆÙˆ™\Ý[ÈÈ™]\›ˆ\ˆYÙK‰ÊBŸJB‚™^ÜÛÛœÝ˜XÙ\•˜XÙU›ÚXÙPØ[]Z[™\ÜÛœÙT™\Ý[Ò][S˜[YSX^HŒÂ‚™^ÜÛÛœÝ˜XÙ\•˜XÙU›ÚXÙPØ[]Z[™\ÜÛœÙT™\Ý[Ò][Q^\›˜[YX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\•˜XÙU›ÚXÙPØ[]Z[™\ÜÛœÙHH›Ù›Øš™XÝ
Âˆ˜ÛÝ[Žˆ›Ù›[X™\Š
Kˆ›™^Žˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™]š[Ý\ÈŽˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™\Ý[ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ›Ú™XÝÝ™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•˜XÙU›ÚXÙPØ[]Z[™\ÜÛœÙT™\Ý[Ò][S˜[YSX^
K›Ü[Û˜[

Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆš[œ]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Ý]]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ™\œ›ÜˆŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÙ\ÜÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ™^\›˜[ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•˜XÙU›ÚXÙPØ[]Z[™\ÜÛœÙT™\Ý[Ò][Q^\›˜[YX^
K›Ü[Û˜[

KˆYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

BŸJJBŸJB‚‚‹ÊŠ‚ˆ
ˆ™]šY]™HH˜XÙHžH]ÈQ‚ˆ
‹Â™^ÜÛÛœÝ˜XÙ\•˜XÙT™XY\˜[\ÈH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
BŸJB‚™^ÜÛÛœÝ˜XÙ\•˜XÙT™XY™\ÜÛœÙTÝ]\ÑY˜][HYNÂ‚™^ÜÛÛœÝ˜XÙ\•˜XÙT™XY™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
K™Y˜][
˜XÙ\•˜XÙT™XY™\ÜÛœÙTÝ]\ÑY˜][
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆ˜XÙHŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

Kˆ›ØœÙ\˜][Û—ÜÜ[œÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

JKˆœÝ[[X\žHŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

Kˆ™Ü˜\Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

BŸJBŸJB‚‚™^ÜÛÛœÝ˜XÙ\•˜XÙU\]T\˜[\ÈH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
BŸJB‚™^ÜÛÛœÝ˜XÙ\•˜XÙU\]P›ÙS˜[YSX^HŒÂ‚™^ÜÛÛœÝ˜XÙ\•˜XÙU\]P›ÙQ^\›˜[YX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\•˜XÙU\]P›ÙHH›Ù›Øš™XÝ
Âˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ›Ú™XÝÝ™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•˜XÙU\]P›ÙS˜[YSX^
K›Ü[Û˜[

Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆš[œ]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Ý]]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ™\œ›ÜˆŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÙ\ÜÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ™^\›˜[ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•˜XÙU\]P›ÙQ^\›˜[YX^
K›Ü[Û˜[

KˆYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

BŸJB‚™^ÜÛÛœÝ˜XÙ\•˜XÙU\]T™\ÜÛœÙS˜[YSX^HŒÂ‚™^ÜÛÛœÝ˜XÙ\•˜XÙU\]T™\ÜÛœÙQ^\›˜[YX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\•˜XÙU\]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ›Ú™XÝÝ™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•˜XÙU\]T™\ÜÛœÙS˜[YSX^
K›Ü[Û˜[

Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆš[œ]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Ý]]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ™\œ›ÜˆŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÙ\ÜÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ™^\›˜[ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•˜XÙU\]T™\ÜÛœÙQ^\›˜[YX^
K›Ü[Û˜[

KˆYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

BŸJB‚‚™^ÜÛÛœÝ˜XÙ\•˜XÙT\X[\]T\˜[\ÈH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
BŸJB‚™^ÜÛÛœÝ˜XÙ\•˜XÙT\X[\]P›ÙS˜[YSX^HŒÂ‚™^ÜÛÛœÝ˜XÙ\•˜XÙT\X[\]P›ÙQ^\›˜[YX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\•˜XÙT\X[\]P›ÙHH›Ù›Øš™XÝ
Âˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ›Ú™XÝÝ™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•˜XÙT\X[\]P›ÙS˜[YSX^
K›Ü[Û˜[

Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆš[œ]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Ý]]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ™\œ›ÜˆŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÙ\ÜÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ™^\›˜[ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•˜XÙT\X[\]P›ÙQ^\›˜[YX^
K›Ü[Û˜[

KˆYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

BŸJB‚™^ÜÛÛœÝ˜XÙ\•˜XÙT\X[\]T™\ÜÛœÙS˜[YSX^HŒÂ‚™^ÜÛÛœÝ˜XÙ\•˜XÙT\X[\]T™\ÜÛœÙQ^\›˜[YX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\•˜XÙT\X[\]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ›Ú™XÝÝ™\œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•˜XÙT\X[\]T™\ÜÛœÙS˜[YSX^
K›Ü[Û˜[

Kˆ›Y]Y]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆš[œ]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›Ý]]Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ™\œ›ÜˆŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

KˆœÙ\ÜÚ[ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ™^\›˜[ÚYŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•˜XÙT\X[\]T™\ÜÛœÙQ^\›˜[YX^
K›Ü[Û˜[

KˆYÜÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

BŸJB‚‚™^ÜÛÛœÝ˜XÙ\•˜XÙQ[]T\˜[\ÈH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
BŸJB‚‚‹ÊŠ‚ˆ
ˆ\]HYÜÈ›ÜˆH˜XÙK‚ˆ
‹Â™^ÜÛÛœÝ˜XÙ\•˜XÙU\]UYÜÔ\˜[\ÈH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
BŸJB‚‚‚‚™^ÜÛÛœÝ˜XÙ\•˜XÙU\]UYÜÐ›ÙHH›Ù›Øš™XÝ
ÂˆYÜÈŽˆ›Ù˜\œ˜^J›ÙœÝš[™Ê
K›Z[ŠJJBŸJB‚‚‚‚™^ÜÛÛœÝ˜XÙ\•˜XÙU\]UYÜÔ™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆYÜÈŽˆ›Ù˜\œ˜^J›ÙœÝš[™Ê
K›Z[ŠJJBŸJB‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÓ\Ý]Y\žT\˜[\ÈH›Ù›Øš™XÝ
ÂˆœYÙHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	ÐHYÙH[X™\ˆÚ][ˆHYÚ[˜]Y™\Ý[Ù]‰ÊKˆ›[Z]Žˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	Ó[X™\ˆÙˆ™\Ý[ÈÈ™]\›ˆ\ˆYÙK‰ÊBŸJB‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÓ\Ý™\ÜÛœÙT™\Ý[Ò][T™\ÛÛ™YžQ[XZ[X^HMÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÓ\Ý™\ÜÛœÙT™\Ý[Ò][T™\ÛÛ™YžS˜[YSX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÓ\Ý™\ÜÛœÙT™\Ý[Ò][T™\ÛÛ™YžSÜ™Ø[š^˜][Û“˜[YSX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÓ\Ý™\ÜÛœÙT™\Ý[Ò][T™\ÛÛ™YžSÜ™Ø[š^˜][Û‘\Ü^S˜[YSX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÓ\Ý™\ÜÛœÙT™\Ý[Ò][T™\ÛÛ™YžSÜ™Ø[š^˜][Û”™YÚ[Û“X^HMŽÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÓ\Ý™\ÜÛœÙT™\Ý[Ò][T™\ÛÛ™YžSÜ™Ø[š^˜][Û”™\]Z\™L™˜QÜ˜XÙT\š[Ù^\ÓZ[ˆHÂ™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÓ\Ý™\ÜÛœÙT™\Ý[Ò][T™\ÛÛ™YžSÜ™Ø[š^˜][Û”™\]Z\™L™˜QÜ˜XÙT\š[Ù^\ÓX^HÌÍÎÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÓ\Ý™\ÜÛœÙT™\Ý[Ò][T™\ÛÛ™YžT›ÛSX^HMNÂ‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÓ\Ý™\ÜÛœÙT™\Ý[Ò][S[šÓX^HŒÂ‚‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÓ\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
Âˆ˜ÛÝ[Žˆ›Ù›[X™\Š
Kˆ›™^Žˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™]š[Ý\ÈŽˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™\Ý[ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ™\ÛÛ™YØžHŽˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ™[XZ[Žˆ›ÙœÝš[™Ê
K™[XZ[

K›Z[ŠJK›X^
˜XÙ\•\Ù\[\ÙÜÓ\Ý™\ÜÛœÙT™\Ý[Ò][T™\ÛÛ™YžQ[XZ[X^
Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\•\Ù\[\ÙÜÓ\Ý™\ÜÛœÙT™\Ý[Ò][T™\ÛÛ™YžS˜[YSX^
Kˆ›Ü™Ø[š^˜][Û—Ü›ÛHŽˆ›Ù™[[JÉÓÝÛ™\‰Ë	ÐYZ[‰Ë	ÓY[X™\‰Ë	ÕšY]Ù\‰Ë	ÝÛÜšÜÜXÙWØYZ[‰Ë	ÝÛÜšÜÜXÙWÛY[X™\‰Ë	ÝÛÜšÜÜXÙWÝšY]Ù\‰×JK›Ü[Û˜[

Kˆ›Ü™Ø[š^˜][ÛˆŽˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\•\Ù\[\ÙÜÓ\Ý™\ÜÛœÙT™\Ý[Ò][T™\ÛÛ™YžSÜ™Ø[š^˜][Û“˜[YSX^
Kˆ™\Ü^WÛ˜[YHŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•\Ù\[\ÙÜÓ\Ý™\ÜÛœÙT™\Ý[Ò][T™\ÛÛ™YžSÜ™Ø[š^˜][Û‘\Ü^S˜[YSX^
K›Ü[Û˜[

Kˆš\×Û™]ÈŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

KˆÜ×Ù[˜X›YŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆœ™YÚ[ÛˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\•\Ù\[\ÙÜÓ\Ý™\ÜÛœÙT™\Ý[Ò][T™\ÛÛ™YžSÜ™Ø[š^˜][Û”™YÚ[Û“X^
K›Ü[Û˜[

Kˆœ™\]Z\™WÌ™˜HŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆœ™\]Z\™WÌ™˜WÙÜ˜XÙWÜ\š[ÙÙ^\ÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\•\Ù\[\ÙÜÓ\Ý™\ÜÛœÙT™\Ý[Ò][T™\ÛÛ™YžSÜ™Ø[š^˜][Û”™\]Z\™L™˜QÜ˜XÙT\š[Ù^\ÓZ[ŠK›X^
˜XÙ\•\Ù\[\ÙÜÓ\Ý™\ÜÛœÙT™\Ý[Ò][T™\ÛÛ™YžSÜ™Ø[š^˜][Û”™\]Z\™L™˜QÜ˜XÙT\š[Ù^\ÓX^
K›Ü[Û˜[

Kˆœ™\]Z\™WÌ™˜WÙ[™›Ü˜ÙYØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

BŸJK›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

KˆœÝ]\ÈŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆœ›ÛHŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•\Ù\[\ÙÜÓ\Ý™\ÜÛœÙT™\Ý[Ò][T™\ÛÛ™YžT›ÛSX^
K›Ü[Û˜[

K™\ØÜšX™J	Õ\Ù\—	ÜÈ›Øˆ›ÛH
K™Ë‹]HØÚY[\ÝS[™Ú[™Y\‹ÜˆÝ\ÝÛH›ÛJIÊKˆ™ÛØ[ÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ó\ÝÙˆ\Ù\—	ÜÈÛØ[È›Üˆ\Ú[™ÈH]›Ü›IÊBŸJK›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ\HŽˆ›Ù™[[JÉØÜš]XØ[	Ë	ÝØ\›š[™É×JKˆ›Y\ÜØYÙHŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœ™\ÛÛ™YŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆœ™\ÛÛ™YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ›[šÈŽˆ›ÙœÝš[™Ê
K\›

K›X^
˜XÙ\•\Ù\[\ÙÜÓ\Ý™\ÜÛœÙT™\Ý[Ò][S[šÓX^
K›Ü[Û˜[

Kˆ[YWÝÚ[™Ý×ÜÝ\Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ[YWÝÚ[™Ý×Ù[™Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

BŸJJBŸJB‚‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÐÜ™X]P›ÙT™\ÛÛ™YY˜][H˜[ÙNÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÐÜ™X]P›ÙHH›Ù›Øš™XÝ
Âˆ˜[\Žˆ›ÙœÝš[™Ê
K]ZY

Kˆ\HŽˆ›Ù™[[JÉØÜš]XØ[	Ë	ÝØ\›š[™É×JKˆ›Y\ÜØYÙHŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœ™\ÛÛ™YŽˆ›Ù˜›ÛÛX[Š
K™Y˜][
˜XÙ\•\Ù\[\ÙÜÐÜ™X]P›ÙT™\ÛÛ™YY˜][
Kˆœ™\ÛÛ™YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ›[šÈŽˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆ[YWÝÚ[™Ý×ÜÝ\Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ[YWÝÚ[™Ý×Ù[™Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

BŸJB‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÓ\Ý[]Y\žT\˜[\ÈH›Ù›Øš™XÝ
ÂˆœYÙHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	ÐHYÙH[X™\ˆÚ][ˆHYÚ[˜]Y™\Ý[Ù]‰ÊKˆ›[Z]Žˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	Ó[X™\ˆÙˆ™\Ý[ÈÈ™]\›ˆ\ˆYÙK‰ÊBŸJB‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÓ\Ý[™\ÜÛœÙT™\Ý[Ò][T™\ÛÛ™YžQ[XZ[X^HMÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÓ\Ý[™\ÜÛœÙT™\Ý[Ò][T™\ÛÛ™YžS˜[YSX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÓ\Ý[™\ÜÛœÙT™\Ý[Ò][T™\ÛÛ™YžSÜ™Ø[š^˜][Û“˜[YSX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÓ\Ý[™\ÜÛœÙT™\Ý[Ò][T™\ÛÛ™YžSÜ™Ø[š^˜][Û‘\Ü^S˜[YSX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÓ\Ý[™\ÜÛœÙT™\Ý[Ò][T™\ÛÛ™YžSÜ™Ø[š^˜][Û”™YÚ[Û“X^HMŽÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÓ\Ý[™\ÜÛœÙT™\Ý[Ò][T™\ÛÛ™YžSÜ™Ø[š^˜][Û”™\]Z\™L™˜QÜ˜XÙT\š[Ù^\ÓZ[ˆHÂ™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÓ\Ý[™\ÜÛœÙT™\Ý[Ò][T™\ÛÛ™YžSÜ™Ø[š^˜][Û”™\]Z\™L™˜QÜ˜XÙT\š[Ù^\ÓX^HÌÍÎÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÓ\Ý[™\ÜÛœÙT™\Ý[Ò][T™\ÛÛ™YžT›ÛSX^HMNÂ‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÓ\Ý[™\ÜÛœÙT™\Ý[Ò][S[šÓX^HŒÂ‚‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÓ\Ý[™\ÜÛœÙHH›Ù›Øš™XÝ
Âˆ˜ÛÝ[Žˆ›Ù›[X™\Š
Kˆ›™^Žˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™]š[Ý\ÈŽˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™\Ý[ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ™\ÛÛ™YØžHŽˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ™[XZ[Žˆ›ÙœÝš[™Ê
K™[XZ[

K›Z[ŠJK›X^
˜XÙ\•\Ù\[\ÙÜÓ\Ý[™\ÜÛœÙT™\Ý[Ò][T™\ÛÛ™YžQ[XZ[X^
Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\•\Ù\[\ÙÜÓ\Ý[™\ÜÛœÙT™\Ý[Ò][T™\ÛÛ™YžS˜[YSX^
Kˆ›Ü™Ø[š^˜][Û—Ü›ÛHŽˆ›Ù™[[JÉÓÝÛ™\‰Ë	ÐYZ[‰Ë	ÓY[X™\‰Ë	ÕšY]Ù\‰Ë	ÝÛÜšÜÜXÙWØYZ[‰Ë	ÝÛÜšÜÜXÙWÛY[X™\‰Ë	ÝÛÜšÜÜXÙWÝšY]Ù\‰×JK›Ü[Û˜[

Kˆ›Ü™Ø[š^˜][ÛˆŽˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\•\Ù\[\ÙÜÓ\Ý[™\ÜÛœÙT™\Ý[Ò][T™\ÛÛ™YžSÜ™Ø[š^˜][Û“˜[YSX^
Kˆ™\Ü^WÛ˜[YHŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•\Ù\[\ÙÜÓ\Ý[™\ÜÛœÙT™\Ý[Ò][T™\ÛÛ™YžSÜ™Ø[š^˜][Û‘\Ü^S˜[YSX^
K›Ü[Û˜[

Kˆš\×Û™]ÈŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

KˆÜ×Ù[˜X›YŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆœ™YÚ[ÛˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\•\Ù\[\ÙÜÓ\Ý[™\ÜÛœÙT™\Ý[Ò][T™\ÛÛ™YžSÜ™Ø[š^˜][Û”™YÚ[Û“X^
K›Ü[Û˜[

Kˆœ™\]Z\™WÌ™˜HŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆœ™\]Z\™WÌ™˜WÙÜ˜XÙWÜ\š[ÙÙ^\ÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\•\Ù\[\ÙÜÓ\Ý[™\ÜÛœÙT™\Ý[Ò][T™\ÛÛ™YžSÜ™Ø[š^˜][Û”™\]Z\™L™˜QÜ˜XÙT\š[Ù^\ÓZ[ŠK›X^
˜XÙ\•\Ù\[\ÙÜÓ\Ý[™\ÜÛœÙT™\Ý[Ò][T™\ÛÛ™YžSÜ™Ø[š^˜][Û”™\]Z\™L™˜QÜ˜XÙT\š[Ù^\ÓX^
K›Ü[Û˜[

Kˆœ™\]Z\™WÌ™˜WÙ[™›Ü˜ÙYØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

BŸJK›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

KˆœÝ]\ÈŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆœ›ÛHŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•\Ù\[\ÙÜÓ\Ý[™\ÜÛœÙT™\Ý[Ò][T™\ÛÛ™YžT›ÛSX^
K›Ü[Û˜[

K™\ØÜšX™J	Õ\Ù\—	ÜÈ›Øˆ›ÛH
K™Ë‹]HØÚY[\ÝS[™Ú[™Y\‹ÜˆÝ\ÝÛH›ÛJIÊKˆ™ÛØ[ÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ó\ÝÙˆ\Ù\—	ÜÈÛØ[È›Üˆ\Ú[™ÈH]›Ü›IÊBŸJK›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ\HŽˆ›Ù™[[JÉØÜš]XØ[	Ë	ÝØ\›š[™É×JKˆ›Y\ÜØYÙHŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœ™\ÛÛ™YŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆœ™\ÛÛ™YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ›[šÈŽˆ›ÙœÝš[™Ê
K\›

K›X^
˜XÙ\•\Ù\[\ÙÜÓ\Ý[™\ÜÛœÙT™\Ý[Ò][S[šÓX^
K›Ü[Û˜[

Kˆ[YWÝÚ[™Ý×ÜÝ\Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ[YWÝÚ[™Ý×Ù[™Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

BŸJJBŸJB‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÓX\šÐ\Ô™\ÛÛ™Y›ÙSÙÒYÑY˜][H×NÂ™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÓX\šÐ\Ô™\ÛÛ™Y›ÙTÙ[XÝ[Y˜][H˜[ÙNÂ™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÓX\šÐ\Ô™\ÛÛ™Y›ÙQ^ÛYRYÑY˜][H×NÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÓX\šÐ\Ô™\ÛÛ™Y›ÙHH›Ù›Øš™XÝ
Âˆ›Ù×ÚYÈŽˆ›Ù˜\œ˜^J›ÙœÝš[™Ê
K]ZY

JK™Y˜][
˜XÙ\•\Ù\[\ÙÜÓX\šÐ\Ô™\ÛÛ™Y›ÙSÙÒYÑY˜][
KˆœÙ[XÝØ[Žˆ›Ù˜›ÛÛX[Š
K™Y˜][
˜XÙ\•\Ù\[\ÙÜÓX\šÐ\Ô™\ÛÛ™Y›ÙTÙ[XÝ[Y˜][
Kˆ™^ÛYWÚYÈŽˆ›Ù˜\œ˜^J›ÙœÝš[™Ê
K]ZY

JK™Y˜][
˜XÙ\•\Ù\[\ÙÜÓX\šÐ\Ô™\ÛÛ™Y›ÙQ^ÛYRYÑY˜][
BŸJB‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÓX\šÐ\Ô™\ÛÛ™Y™\ÜÛœÙTÝ]\ÑY˜][HYNÂ‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÓX\šÐ\Ô™\ÛÛ™Y™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
K™Y˜][
˜XÙ\•\Ù\[\ÙÜÓX\šÐ\Ô™\ÛÛ™Y™\ÜÛœÙTÝ]\ÑY˜][
Kˆœ™\Ý[Žˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJB‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÔ™XY\˜[\ÈH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
BŸJB‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÔ™XY™\ÜÛœÙT™\ÛÛ™YžQ[XZ[X^HMÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÔ™XY™\ÜÛœÙT™\ÛÛ™YžS˜[YSX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÔ™XY™\ÜÛœÙT™\ÛÛ™YžSÜ™Ø[š^˜][Û“˜[YSX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÔ™XY™\ÜÛœÙT™\ÛÛ™YžSÜ™Ø[š^˜][Û‘\Ü^S˜[YSX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÔ™XY™\ÜÛœÙT™\ÛÛ™YžSÜ™Ø[š^˜][Û”™YÚ[Û“X^HMŽÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÔ™XY™\ÜÛœÙT™\ÛÛ™YžSÜ™Ø[š^˜][Û”™\]Z\™L™˜QÜ˜XÙT\š[Ù^\ÓZ[ˆHÂ™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÔ™XY™\ÜÛœÙT™\ÛÛ™YžSÜ™Ø[š^˜][Û”™\]Z\™L™˜QÜ˜XÙT\š[Ù^\ÓX^HÌÍÎÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÔ™XY™\ÜÛœÙT™\ÛÛ™YžT›ÛSX^HMNÂ‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÔ™XY™\ÜÛœÙS[šÓX^HŒÂ‚‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÔ™XY™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ™\ÛÛ™YØžHŽˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ™[XZ[Žˆ›ÙœÝš[™Ê
K™[XZ[

K›Z[ŠJK›X^
˜XÙ\•\Ù\[\ÙÜÔ™XY™\ÜÛœÙT™\ÛÛ™YžQ[XZ[X^
Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\•\Ù\[\ÙÜÔ™XY™\ÜÛœÙT™\ÛÛ™YžS˜[YSX^
Kˆ›Ü™Ø[š^˜][Û—Ü›ÛHŽˆ›Ù™[[JÉÓÝÛ™\‰Ë	ÐYZ[‰Ë	ÓY[X™\‰Ë	ÕšY]Ù\‰Ë	ÝÛÜšÜÜXÙWØYZ[‰Ë	ÝÛÜšÜÜXÙWÛY[X™\‰Ë	ÝÛÜšÜÜXÙWÝšY]Ù\‰×JK›Ü[Û˜[

Kˆ›Ü™Ø[š^˜][ÛˆŽˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\•\Ù\[\ÙÜÔ™XY™\ÜÛœÙT™\ÛÛ™YžSÜ™Ø[š^˜][Û“˜[YSX^
Kˆ™\Ü^WÛ˜[YHŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•\Ù\[\ÙÜÔ™XY™\ÜÛœÙT™\ÛÛ™YžSÜ™Ø[š^˜][Û‘\Ü^S˜[YSX^
K›Ü[Û˜[

Kˆš\×Û™]ÈŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

KˆÜ×Ù[˜X›YŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆœ™YÚ[ÛˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\•\Ù\[\ÙÜÔ™XY™\ÜÛœÙT™\ÛÛ™YžSÜ™Ø[š^˜][Û”™YÚ[Û“X^
K›Ü[Û˜[

Kˆœ™\]Z\™WÌ™˜HŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆœ™\]Z\™WÌ™˜WÙÜ˜XÙWÜ\š[ÙÙ^\ÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\•\Ù\[\ÙÜÔ™XY™\ÜÛœÙT™\ÛÛ™YžSÜ™Ø[š^˜][Û”™\]Z\™L™˜QÜ˜XÙT\š[Ù^\ÓZ[ŠK›X^
˜XÙ\•\Ù\[\ÙÜÔ™XY™\ÜÛœÙT™\ÛÛ™YžSÜ™Ø[š^˜][Û”™\]Z\™L™˜QÜ˜XÙT\š[Ù^\ÓX^
K›Ü[Û˜[

Kˆœ™\]Z\™WÌ™˜WÙ[™›Ü˜ÙYØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

BŸJK›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

KˆœÝ]\ÈŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆœ›ÛHŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•\Ù\[\ÙÜÔ™XY™\ÜÛœÙT™\ÛÛ™YžT›ÛSX^
K›Ü[Û˜[

K™\ØÜšX™J	Õ\Ù\—	ÜÈ›Øˆ›ÛH
K™Ë‹]HØÚY[\ÝS[™Ú[™Y\‹ÜˆÝ\ÝÛH›ÛJIÊKˆ™ÛØ[ÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ó\ÝÙˆ\Ù\—	ÜÈÛØ[È›Üˆ\Ú[™ÈH]›Ü›IÊBŸJK›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ\HŽˆ›Ù™[[JÉØÜš]XØ[	Ë	ÝØ\›š[™É×JKˆ›Y\ÜØYÙHŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœ™\ÛÛ™YŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆœ™\ÛÛ™YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ›[šÈŽˆ›ÙœÝš[™Ê
K\›

K›X^
˜XÙ\•\Ù\[\ÙÜÔ™XY™\ÜÛœÙS[šÓX^
K›Ü[Û˜[

Kˆ[YWÝÚ[™Ý×ÜÝ\Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ[YWÝÚ[™Ý×Ù[™Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

BŸJB‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÕ\]T\˜[\ÈH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
BŸJB‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÕ\]P›ÙT™\ÛÛ™YY˜][H˜[ÙNÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÕ\]P›ÙHH›Ù›Øš™XÝ
Âˆ˜[\Žˆ›ÙœÝš[™Ê
K]ZY

Kˆ\HŽˆ›Ù™[[JÉØÜš]XØ[	Ë	ÝØ\›š[™É×JKˆ›Y\ÜØYÙHŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœ™\ÛÛ™YŽˆ›Ù˜›ÛÛX[Š
K™Y˜][
˜XÙ\•\Ù\[\ÙÜÕ\]P›ÙT™\ÛÛ™YY˜][
Kˆœ™\ÛÛ™YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ›[šÈŽˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆ[YWÝÚ[™Ý×ÜÝ\Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ[YWÝÚ[™Ý×Ù[™Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

BŸJB‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÕ\]T™\ÜÛœÙT™\ÛÛ™YžQ[XZ[X^HMÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÕ\]T™\ÜÛœÙT™\ÛÛ™YžS˜[YSX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÕ\]T™\ÜÛœÙT™\ÛÛ™YžSÜ™Ø[š^˜][Û“˜[YSX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÕ\]T™\ÜÛœÙT™\ÛÛ™YžSÜ™Ø[š^˜][Û‘\Ü^S˜[YSX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÕ\]T™\ÜÛœÙT™\ÛÛ™YžSÜ™Ø[š^˜][Û”™YÚ[Û“X^HMŽÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÕ\]T™\ÜÛœÙT™\ÛÛ™YžSÜ™Ø[š^˜][Û”™\]Z\™L™˜QÜ˜XÙT\š[Ù^\ÓZ[ˆHÂ™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÕ\]T™\ÜÛœÙT™\ÛÛ™YžSÜ™Ø[š^˜][Û”™\]Z\™L™˜QÜ˜XÙT\š[Ù^\ÓX^HÌÍÎÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÕ\]T™\ÜÛœÙT™\ÛÛ™YžT›ÛSX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÕ\]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ˜[\Žˆ›ÙœÝš[™Ê
K]ZY

Kˆ\HŽˆ›Ù™[[JÉØÜš]XØ[	Ë	ÝØ\›š[™É×JKˆ›Y\ÜØYÙHŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœ™\ÛÛ™YŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\ÛÛ™YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆœ™\ÛÛ™YØžHŽˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ™[XZ[Žˆ›ÙœÝš[™Ê
K™[XZ[

K›Z[ŠJK›X^
˜XÙ\•\Ù\[\ÙÜÕ\]T™\ÜÛœÙT™\ÛÛ™YžQ[XZ[X^
Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\•\Ù\[\ÙÜÕ\]T™\ÜÛœÙT™\ÛÛ™YžS˜[YSX^
Kˆ›Ü™Ø[š^˜][Û—Ü›ÛHŽˆ›Ù™[[JÉÓÝÛ™\‰Ë	ÐYZ[‰Ë	ÓY[X™\‰Ë	ÕšY]Ù\‰Ë	ÝÛÜšÜÜXÙWØYZ[‰Ë	ÝÛÜšÜÜXÙWÛY[X™\‰Ë	ÝÛÜšÜÜXÙWÝšY]Ù\‰×JK›Ü[Û˜[

Kˆ›Ü™Ø[š^˜][ÛˆŽˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\•\Ù\[\ÙÜÕ\]T™\ÜÛœÙT™\ÛÛ™YžSÜ™Ø[š^˜][Û“˜[YSX^
Kˆ™\Ü^WÛ˜[YHŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•\Ù\[\ÙÜÕ\]T™\ÜÛœÙT™\ÛÛ™YžSÜ™Ø[š^˜][Û‘\Ü^S˜[YSX^
K›Ü[Û˜[

Kˆš\×Û™]ÈŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

KˆÜ×Ù[˜X›YŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆœ™YÚ[ÛˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\•\Ù\[\ÙÜÕ\]T™\ÜÛœÙT™\ÛÛ™YžSÜ™Ø[š^˜][Û”™YÚ[Û“X^
K›Ü[Û˜[

Kˆœ™\]Z\™WÌ™˜HŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆœ™\]Z\™WÌ™˜WÙÜ˜XÙWÜ\š[ÙÙ^\ÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\•\Ù\[\ÙÜÕ\]T™\ÜÛœÙT™\ÛÛ™YžSÜ™Ø[š^˜][Û”™\]Z\™L™˜QÜ˜XÙT\š[Ù^\ÓZ[ŠK›X^
˜XÙ\•\Ù\[\ÙÜÕ\]T™\ÜÛœÙT™\ÛÛ™YžSÜ™Ø[š^˜][Û”™\]Z\™L™˜QÜ˜XÙT\š[Ù^\ÓX^
K›Ü[Û˜[

Kˆœ™\]Z\™WÌ™˜WÙ[™›Ü˜ÙYØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

BŸJK›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

KˆœÝ]\ÈŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆœ›ÛHŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•\Ù\[\ÙÜÕ\]T™\ÜÛœÙT™\ÛÛ™YžT›ÛSX^
K›Ü[Û˜[

K™\ØÜšX™J	Õ\Ù\—	ÜÈ›Øˆ›ÛH
K™Ë‹]HØÚY[\ÝS[™Ú[™Y\‹ÜˆÝ\ÝÛH›ÛJIÊKˆ™ÛØ[ÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ó\ÝÙˆ\Ù\—	ÜÈÛØ[È›Üˆ\Ú[™ÈH]›Ü›IÊBŸJK›Ü[Û˜[

Kˆ›[šÈŽˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆ[YWÝÚ[™Ý×ÜÝ\Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ[YWÝÚ[™Ý×Ù[™Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JBŸJB‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÔ\X[\]T\˜[\ÈH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
BŸJB‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÔ\X[\]P›ÙT™\ÛÛ™YY˜][H˜[ÙNÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÔ\X[\]P›ÙHH›Ù›Øš™XÝ
Âˆ˜[\Žˆ›ÙœÝš[™Ê
K]ZY

Kˆ\HŽˆ›Ù™[[JÉØÜš]XØ[	Ë	ÝØ\›š[™É×JKˆ›Y\ÜØYÙHŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœ™\ÛÛ™YŽˆ›Ù˜›ÛÛX[Š
K™Y˜][
˜XÙ\•\Ù\[\ÙÜÔ\X[\]P›ÙT™\ÛÛ™YY˜][
Kˆœ™\ÛÛ™YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ›[šÈŽˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆ[YWÝÚ[™Ý×ÜÝ\Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ[YWÝÚ[™Ý×Ù[™Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

BŸJB‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÔ\X[\]T™\ÜÛœÙT™\ÛÛ™YžQ[XZ[X^HMÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÔ\X[\]T™\ÜÛœÙT™\ÛÛ™YžS˜[YSX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÔ\X[\]T™\ÜÛœÙT™\ÛÛ™YžSÜ™Ø[š^˜][Û“˜[YSX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÔ\X[\]T™\ÜÛœÙT™\ÛÛ™YžSÜ™Ø[š^˜][Û‘\Ü^S˜[YSX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÔ\X[\]T™\ÜÛœÙT™\ÛÛ™YžSÜ™Ø[š^˜][Û”™YÚ[Û“X^HMŽÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÔ\X[\]T™\ÜÛœÙT™\ÛÛ™YžSÜ™Ø[š^˜][Û”™\]Z\™L™˜QÜ˜XÙT\š[Ù^\ÓZ[ˆHÂ™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÔ\X[\]T™\ÜÛœÙT™\ÛÛ™YžSÜ™Ø[š^˜][Û”™\]Z\™L™˜QÜ˜XÙT\š[Ù^\ÓX^HÌÍÎÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÔ\X[\]T™\ÜÛœÙT™\ÛÛ™YžT›ÛSX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÔ\X[\]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ˜[\Žˆ›ÙœÝš[™Ê
K]ZY

Kˆ\HŽˆ›Ù™[[JÉØÜš]XØ[	Ë	ÝØ\›š[™É×JKˆ›Y\ÜØYÙHŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœ™\ÛÛ™YŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\ÛÛ™YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆœ™\ÛÛ™YØžHŽˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ™[XZ[Žˆ›ÙœÝš[™Ê
K™[XZ[

K›Z[ŠJK›X^
˜XÙ\•\Ù\[\ÙÜÔ\X[\]T™\ÜÛœÙT™\ÛÛ™YžQ[XZ[X^
Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\•\Ù\[\ÙÜÔ\X[\]T™\ÜÛœÙT™\ÛÛ™YžS˜[YSX^
Kˆ›Ü™Ø[š^˜][Û—Ü›ÛHŽˆ›Ù™[[JÉÓÝÛ™\‰Ë	ÐYZ[‰Ë	ÓY[X™\‰Ë	ÕšY]Ù\‰Ë	ÝÛÜšÜÜXÙWØYZ[‰Ë	ÝÛÜšÜÜXÙWÛY[X™\‰Ë	ÝÛÜšÜÜXÙWÝšY]Ù\‰×JK›Ü[Û˜[

Kˆ›Ü™Ø[š^˜][ÛˆŽˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\•\Ù\[\ÙÜÔ\X[\]T™\ÜÛœÙT™\ÛÛ™YžSÜ™Ø[š^˜][Û“˜[YSX^
Kˆ™\Ü^WÛ˜[YHŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•\Ù\[\ÙÜÔ\X[\]T™\ÜÛœÙT™\ÛÛ™YžSÜ™Ø[š^˜][Û‘\Ü^S˜[YSX^
K›Ü[Û˜[

Kˆš\×Û™]ÈŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

KˆÜ×Ù[˜X›YŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆœ™YÚ[ÛˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\•\Ù\[\ÙÜÔ\X[\]T™\ÜÛœÙT™\ÛÛ™YžSÜ™Ø[š^˜][Û”™YÚ[Û“X^
K›Ü[Û˜[

Kˆœ™\]Z\™WÌ™˜HŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆœ™\]Z\™WÌ™˜WÙÜ˜XÙWÜ\š[ÙÙ^\ÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\•\Ù\[\ÙÜÔ\X[\]T™\ÜÛœÙT™\ÛÛ™YžSÜ™Ø[š^˜][Û”™\]Z\™L™˜QÜ˜XÙT\š[Ù^\ÓZ[ŠK›X^
˜XÙ\•\Ù\[\ÙÜÔ\X[\]T™\ÜÛœÙT™\ÛÛ™YžSÜ™Ø[š^˜][Û”™\]Z\™L™˜QÜ˜XÙT\š[Ù^\ÓX^
K›Ü[Û˜[

Kˆœ™\]Z\™WÌ™˜WÙ[™›Ü˜ÙYØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

BŸJK›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

KˆœÝ]\ÈŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆœ›ÛHŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•\Ù\[\ÙÜÔ\X[\]T™\ÜÛœÙT™\ÛÛ™YžT›ÛSX^
K›Ü[Û˜[

K™\ØÜšX™J	Õ\Ù\—	ÜÈ›Øˆ›ÛH
K™Ë‹]HØÚY[\ÝS[™Ú[™Y\‹ÜˆÝ\ÝÛH›ÛJIÊKˆ™ÛØ[ÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ó\ÝÙˆ\Ù\—	ÜÈÛØ[È›Üˆ\Ú[™ÈH]›Ü›IÊBŸJK›Ü[Û˜[

Kˆ›[šÈŽˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆ[YWÝÚ[™Ý×ÜÝ\Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ[YWÝÚ[™Ý×Ù[™Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JBŸJB‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÑ[]T\˜[\ÈH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
BŸJB‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÓ\Ý›Ü[\\˜[\ÈH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
BŸJB‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÓ\Ý›Ü[\™\ÜÛœÙT™\ÛÛ™YžQ[XZ[X^HMÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÓ\Ý›Ü[\™\ÜÛœÙT™\ÛÛ™YžS˜[YSX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÓ\Ý›Ü[\™\ÜÛœÙT™\ÛÛ™YžSÜ™Ø[š^˜][Û“˜[YSX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÓ\Ý›Ü[\™\ÜÛœÙT™\ÛÛ™YžSÜ™Ø[š^˜][Û‘\Ü^S˜[YSX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÓ\Ý›Ü[\™\ÜÛœÙT™\ÛÛ™YžSÜ™Ø[š^˜][Û”™YÚ[Û“X^HMŽÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÓ\Ý›Ü[\™\ÜÛœÙT™\ÛÛ™YžSÜ™Ø[š^˜][Û”™\]Z\™L™˜QÜ˜XÙT\š[Ù^\ÓZ[ˆHÂ™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÓ\Ý›Ü[\™\ÜÛœÙT™\ÛÛ™YžSÜ™Ø[š^˜][Û”™\]Z\™L™˜QÜ˜XÙT\š[Ù^\ÓX^HÌÍÎÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÓ\Ý›Ü[\™\ÜÛœÙT™\ÛÛ™YžT›ÛSX^HMNÂ‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÓ\Ý›Ü[\™\ÜÛœÙS[šÓX^HŒÂ‚‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÙÜÓ\Ý›Ü[\™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ™\ÛÛ™YØžHŽˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ™[XZ[Žˆ›ÙœÝš[™Ê
K™[XZ[

K›Z[ŠJK›X^
˜XÙ\•\Ù\[\ÙÜÓ\Ý›Ü[\™\ÜÛœÙT™\ÛÛ™YžQ[XZ[X^
Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\•\Ù\[\ÙÜÓ\Ý›Ü[\™\ÜÛœÙT™\ÛÛ™YžS˜[YSX^
Kˆ›Ü™Ø[š^˜][Û—Ü›ÛHŽˆ›Ù™[[JÉÓÝÛ™\‰Ë	ÐYZ[‰Ë	ÓY[X™\‰Ë	ÕšY]Ù\‰Ë	ÝÛÜšÜÜXÙWØYZ[‰Ë	ÝÛÜšÜÜXÙWÛY[X™\‰Ë	ÝÛÜšÜÜXÙWÝšY]Ù\‰×JK›Ü[Û˜[

Kˆ›Ü™Ø[š^˜][ÛˆŽˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\•\Ù\[\ÙÜÓ\Ý›Ü[\™\ÜÛœÙT™\ÛÛ™YžSÜ™Ø[š^˜][Û“˜[YSX^
Kˆ™\Ü^WÛ˜[YHŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•\Ù\[\ÙÜÓ\Ý›Ü[\™\ÜÛœÙT™\ÛÛ™YžSÜ™Ø[š^˜][Û‘\Ü^S˜[YSX^
K›Ü[Û˜[

Kˆš\×Û™]ÈŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

KˆÜ×Ù[˜X›YŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆœ™YÚ[ÛˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\•\Ù\[\ÙÜÓ\Ý›Ü[\™\ÜÛœÙT™\ÛÛ™YžSÜ™Ø[š^˜][Û”™YÚ[Û“X^
K›Ü[Û˜[

Kˆœ™\]Z\™WÌ™˜HŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆœ™\]Z\™WÌ™˜WÙÜ˜XÙWÜ\š[ÙÙ^\ÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\•\Ù\[\ÙÜÓ\Ý›Ü[\™\ÜÛœÙT™\ÛÛ™YžSÜ™Ø[š^˜][Û”™\]Z\™L™˜QÜ˜XÙT\š[Ù^\ÓZ[ŠK›X^
˜XÙ\•\Ù\[\ÙÜÓ\Ý›Ü[\™\ÜÛœÙT™\ÛÛ™YžSÜ™Ø[š^˜][Û”™\]Z\™L™˜QÜ˜XÙT\š[Ù^\ÓX^
K›Ü[Û˜[

Kˆœ™\]Z\™WÌ™˜WÙ[™›Ü˜ÙYØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

BŸJK›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

KˆœÝ]\ÈŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆœ›ÛHŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•\Ù\[\ÙÜÓ\Ý›Ü[\™\ÜÛœÙT™\ÛÛ™YžT›ÛSX^
K›Ü[Û˜[

K™\ØÜšX™J	Õ\Ù\—	ÜÈ›Øˆ›ÛH
K™Ë‹]HØÚY[\ÝS[™Ú[™Y\‹ÜˆÝ\ÝÛH›ÛJIÊKˆ™ÛØ[ÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

K™\ØÜšX™J	Ó\ÝÙˆ\Ù\—	ÜÈÛØ[È›Üˆ\Ú[™ÈH]›Ü›IÊBŸJK›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ\HŽˆ›Ù™[[JÉØÜš]XØ[	Ë	ÝØ\›š[™É×JKˆ›Y\ÜØYÙHŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœ™\ÛÛ™YŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆœ™\ÛÛ™YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ›[šÈŽˆ›ÙœÝš[™Ê
K\›

K›X^
˜XÙ\•\Ù\[\ÙÜÓ\Ý›Ü[\™\ÜÛœÙS[šÓX^
K›Ü[Û˜[

Kˆ[YWÝÚ[™Ý×ÜÝ\Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ[YWÝÚ[™Ý×Ù[™Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

BŸJB‚‚‹ÊŠ‚ˆ
ˆÙ]Ü]Y\ž\Ù]™]\›œÈ
YÙWÜ]Y\ž\Ù]Ý[ØÛÝ[
X›Üˆ\Ýœ™\]Y\ÝÈ™XØ]\ÙH\ÝÛ[Ûš]ÜœØ[ÛÈ™YYÈHÝ[ÛÝ[ˆ‘‰ÜÂ™Y˜][\Ý^XÝÈÛ›HH]Y\ž\Ù]ÛÈÙY\H›ÛÝ[™Ú[™^XÚ][œÝXYÙˆ][™È‘ˆÙ\šX[^™HH\H[˜ÛÜœ™XÝK‚ˆ
ˆÝ[[X\žH™]\›ˆHYÚ[˜]Y›ÛÝ[Ûš]Üˆ\Ý‚ˆ
‹Â™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ó\Ý]Y\žT\˜[\ÈH›Ù›Øš™XÝ
ÂˆœYÙHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	ÐHYÙH[X™\ˆÚ][ˆHYÚ[˜]Y™\Ý[Ù]‰ÊKˆ›[Z]Žˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	Ó[X™\ˆÙˆ™\Ý[ÈÈ™]\›ˆ\ˆYÙK‰ÊBŸJB‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ó\Ý™\ÜÛœÙT™\Ý[Ò][SY]šXÓX^HMMŽÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ó\Ý™\ÜÛœÙT™\Ý[Ò][U™\ÚÛY]šXÕ˜[YSX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ó\Ý™\ÜÛœÙT™\Ý[Ò][PÜš]XØ[™\ÚÛ˜[YSZ[ˆHÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ó\Ý™\ÜÛœÙT™\Ý[Ò][UØ\›š[™Õ™\ÚÛ˜[YSZ[ˆHÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ó\Ý™\ÜÛœÙT™\Ý[Ò][P[\œ™\]Y[˜ÞSZ[ˆHNÂ™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ó\Ý™\ÜÛœÙT™\Ý[Ò][P[\œ™\]Y[˜ÞSX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ó\Ý™\ÜÛœÙT™\Ý[Ò][P]]Õ™\ÚÛ[YUÚ[™ÝÓZ[ˆHÂ™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ó\Ý™\ÜÛœÙT™\Ý[Ò][P]]Õ™\ÚÛ[YUÚ[™ÝÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ó\Ý™\ÜÛœÙT™\Ý[Ò][S›ÝYšXØ][Û‘[XZ[Ò][SX^HMÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ó\Ý™\ÜÛœÙT™\Ý[Ò][TÛXÚÕÙXšÛÚÕ\›X^HŒÂ‚‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ó\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
Âˆ˜ÛÝ[Žˆ›Ù›[X™\Š
Kˆ›™^Žˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™]š[Ý\ÈŽˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™\Ý[ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ›Y]šX×Û˜[YHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ\]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™[]YŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ™[]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ›Y]šX×Ý\HŽˆ›Ù™[[JÉØÛÝ[ÛÙ—Ù\œ›ÜœÉË	Ù\œ›Ü—Ü˜]\×Ù›Ü—Ù[˜Ý[Û—ØØ[[™ÉË	Ù\œ›Ü—Ùœ™YWÜÙ\ÜÚ[Û—Ü˜]\ÉË	ÜÙ\šXÙWÜ›ÝšY\—Ù\œ›Ü—Ü˜]\ÉË	ÛWØ\WÙ˜Z[\™WÜ˜]\ÉË	ÜÜ[—Ü™\ÜÛœÙWÝ[YIË	ÛWÜ™\ÜÛœÙWÝ[YIË	ÝÚÙ[—Ý\ØYÙIË	ÙZ[WÝÚÙ[œ×ÜÜ[	Ë	Û[ÛWÝÚÙ[œ×ÜÜ[	Ë	Ù]˜[X][Û—ÛY]šXÜÉ×JKˆ›Y]šXÈŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•\Ù\[\Ó\Ý™\ÜÛœÙT™\Ý[Ò][SY]šXÓX^
K›Ü[Û˜[

K™\ØÜšX™J	ÒYÙˆH]˜[X][Ûˆ[\]K‰ÊKˆ™\ÚÛÛÜ\˜]ÜˆŽˆ›Ù™[[JÉÙÜ™X]\—Ý[‰Ë	Û\Ü×Ý[‰×JKˆ™\ÚÛÝ\HŽˆ›Ù™[[JÉÜÝ]XÉË	Ü\˜Ù[YÙWØÚ[™ÙI×JK›Ü[Û˜[

K™\ØÜšX™J	ÓY]ÙÈÙ]H™\ÚÛ›ÜˆH[Ûš]Üˆ
Ý]XÈÜˆ\˜Ù[YÙHÚ[™ÙJK‰ÊKˆ™\ÚÛÛY]šX×Ý˜[YHŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•\Ù\[\Ó\Ý™\ÜÛœÙT™\Ý[Ò][U™\ÚÛY]šXÕ˜[YSX^
K›Ü[Û˜[

K™\ØÜšX™J	Ñ›ÜˆÚÚXÙH[™\Ü×Ù˜Z[]˜[ËHÜXÚYšXÈY]šXÈ˜[YHÈ[Ûš]Ü‹‰ÊKˆ˜Üš]XØ[Ý™\ÚÛÝ˜[YHŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\•\Ù\[\Ó\Ý™\ÜÛœÙT™\Ý[Ò][PÜš]XØ[™\ÚÛ˜[YSZ[ŠK›Ü[Û˜[

KˆØ\›š[™×Ý™\ÚÛÝ˜[YHŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\•\Ù\[\Ó\Ý™\ÜÛœÙT™\Ý[Ò][UØ\›š[™Õ™\ÚÛ˜[YSZ[ŠK›Ü[Û˜[

Kˆ˜[\Ùœ™\]Y[˜ÞHŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\•\Ù\[\Ó\Ý™\ÜÛœÙT™\Ý[Ò][P[\œ™\]Y[˜ÞSZ[ŠK›X^
˜XÙ\•\Ù\[\Ó\Ý™\ÜÛœÙT™\Ý[Ò][P[\œ™\]Y[˜ÞSX^
K›Ü[Û˜[

K™\ØÜšX™J	Ñœ™\]Y[˜ÞHÙˆ[\ÚXÚÜÈ[ˆZ[]\Ë‰ÊKˆ˜]]×Ý™\ÚÛÝ[YWÝÚ[™ÝÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\•\Ù\[\Ó\Ý™\ÜÛœÙT™\Ý[Ò][P]]Õ™\ÚÛ[YUÚ[™ÝÓZ[ŠK›X^
˜XÙ\•\Ù\[\Ó\Ý™\ÜÛœÙT™\Ý[Ò][P]]Õ™\ÚÛ[YUÚ[™ÝÓX^
K›Ü[Û˜[

K™\ØÜšX™J	Ñ›Üˆ]]Ë]™\ÚÛ[™ËˆH[YHÚ[™ÝÈ[ˆZ[]\ÈÈØ[Ý[]HH\ÝÜšXØ[YX[‰ÊKˆ›\ÝØÚXÚÙYØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

K™\ØÜšX™J	ÕH\Ý[YHH[Ûš]ÜˆØ\ÈÚXÚÙY›Üˆ[\Ë‰ÊKˆ››ÝYšXØ][Û—Ù[XZ[ÈŽˆ›Ù˜\œ˜^J›ÙœÝš[™Ê
K™[XZ[

K›Z[ŠJK›X^
˜XÙ\•\Ù\[\Ó\Ý™\ÜÛœÙT™\Ý[Ò][S›ÝYšXØ][Û‘[XZ[Ò][SX^
JK›Ü[Û˜[

KˆœÛXÚ×ÝÙXšÛÚ×Ý\›Žˆ›ÙœÝš[™Ê
K\›

K›X^
˜XÙ\•\Ù\[\Ó\Ý™\ÜÛœÙT™\Ý[Ò][TÛXÚÕÙXšÛÚÕ\›X^
K›Ü[Û˜[

KˆœÛXÚ×Û›Ý\ÈŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆš\×Û]]HŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ™š[\œÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›ÙÜÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

JK›Ü[Û˜[

Kˆ›Ü™Ø[š^˜][ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

KˆÛÜšÜÜXÙHŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜Ü™X]YØžHŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJJBŸJB‚‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÐÜ™X]P›ÙSY]šXÓX^HMMŽÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÐÜ™X]P›ÙU™\ÚÛY]šXÕ˜[YSX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÐÜ™X]P›ÙPÜš]XØ[™\ÚÛ˜[YSZ[ˆHÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÐÜ™X]P›ÙUØ\›š[™Õ™\ÚÛ˜[YSZ[ˆHÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÐÜ™X]P›ÙP[\œ™\]Y[˜ÞSZ[ˆHNÂ™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÐÜ™X]P›ÙP[\œ™\]Y[˜ÞSX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÐÜ™X]P›ÙP]]Õ™\ÚÛ[YUÚ[™ÝÓZ[ˆHÂ™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÐÜ™X]P›ÙP]]Õ™\ÚÛ[YUÚ[™ÝÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÐÜ™X]P›ÙS›ÝYšXØ][Û‘[XZ[Ò][SX^HMÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÐÜ™X]P›ÙTÛXÚÕÙXšÛÚÕ\›X^HŒÂ‚‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÐÜ™X]P›ÙHH›Ù›Øš™XÝ
Âˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ™[]YŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ™[]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ›Y]šX×Ý\HŽˆ›Ù™[[JÉØÛÝ[ÛÙ—Ù\œ›ÜœÉË	Ù\œ›Ü—Ü˜]\×Ù›Ü—Ù[˜Ý[Û—ØØ[[™ÉË	Ù\œ›Ü—Ùœ™YWÜÙ\ÜÚ[Û—Ü˜]\ÉË	ÜÙ\šXÙWÜ›ÝšY\—Ù\œ›Ü—Ü˜]\ÉË	ÛWØ\WÙ˜Z[\™WÜ˜]\ÉË	ÜÜ[—Ü™\ÜÛœÙWÝ[YIË	ÛWÜ™\ÜÛœÙWÝ[YIË	ÝÚÙ[—Ý\ØYÙIË	ÙZ[WÝÚÙ[œ×ÜÜ[	Ë	Û[ÛWÝÚÙ[œ×ÜÜ[	Ë	Ù]˜[X][Û—ÛY]šXÜÉ×JKˆ›Y]šXÈŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•\Ù\[\ÐÜ™X]P›ÙSY]šXÓX^
K›Ü[Û˜[

K™\ØÜšX™J	ÒYÙˆH]˜[X][Ûˆ[\]K‰ÊKˆ™\ÚÛÛÜ\˜]ÜˆŽˆ›Ù™[[JÉÙÜ™X]\—Ý[‰Ë	Û\Ü×Ý[‰×JKˆ™\ÚÛÝ\HŽˆ›Ù™[[JÉÜÝ]XÉË	Ü\˜Ù[YÙWØÚ[™ÙI×JK›Ü[Û˜[

K™\ØÜšX™J	ÓY]ÙÈÙ]H™\ÚÛ›ÜˆH[Ûš]Üˆ
Ý]XÈÜˆ\˜Ù[YÙHÚ[™ÙJK‰ÊKˆ™\ÚÛÛY]šX×Ý˜[YHŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•\Ù\[\ÐÜ™X]P›ÙU™\ÚÛY]šXÕ˜[YSX^
K›Ü[Û˜[

K™\ØÜšX™J	Ñ›ÜˆÚÚXÙH[™\Ü×Ù˜Z[]˜[ËHÜXÚYšXÈY]šXÈ˜[YHÈ[Ûš]Ü‹‰ÊKˆ˜Üš]XØ[Ý™\ÚÛÝ˜[YHŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\•\Ù\[\ÐÜ™X]P›ÙPÜš]XØ[™\ÚÛ˜[YSZ[ŠK›Ü[Û˜[

KˆØ\›š[™×Ý™\ÚÛÝ˜[YHŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\•\Ù\[\ÐÜ™X]P›ÙUØ\›š[™Õ™\ÚÛ˜[YSZ[ŠK›Ü[Û˜[

Kˆ˜[\Ùœ™\]Y[˜ÞHŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\•\Ù\[\ÐÜ™X]P›ÙP[\œ™\]Y[˜ÞSZ[ŠK›X^
˜XÙ\•\Ù\[\ÐÜ™X]P›ÙP[\œ™\]Y[˜ÞSX^
K›Ü[Û˜[

K™\ØÜšX™J	Ñœ™\]Y[˜ÞHÙˆ[\ÚXÚÜÈ[ˆZ[]\Ë‰ÊKˆ˜]]×Ý™\ÚÛÝ[YWÝÚ[™ÝÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\•\Ù\[\ÐÜ™X]P›ÙP]]Õ™\ÚÛ[YUÚ[™ÝÓZ[ŠK›X^
˜XÙ\•\Ù\[\ÐÜ™X]P›ÙP]]Õ™\ÚÛ[YUÚ[™ÝÓX^
K›Ü[Û˜[

K™\ØÜšX™J	Ñ›Üˆ]]Ë]™\ÚÛ[™ËˆH[YHÚ[™ÝÈ[ˆZ[]\ÈÈØ[Ý[]HH\ÝÜšXØ[YX[‰ÊKˆ›\ÝØÚXÚÙYØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

K™\ØÜšX™J	ÕH\Ý[YHH[Ûš]ÜˆØ\ÈÚXÚÙY›Üˆ[\Ë‰ÊKˆ››ÝYšXØ][Û—Ù[XZ[ÈŽˆ›Ù˜\œ˜^J›ÙœÝš[™Ê
K™[XZ[

K›Z[ŠJK›X^
˜XÙ\•\Ù\[\ÐÜ™X]P›ÙS›ÝYšXØ][Û‘[XZ[Ò][SX^
JK›Ü[Û˜[

KˆœÛXÚ×ÝÙXšÛÚ×Ý\›Žˆ›ÙœÝš[™Ê
K\›

K›X^
˜XÙ\•\Ù\[\ÐÜ™X]P›ÙTÛXÚÕÙXšÛÚÕ\›X^
K›Ü[Û˜[

KˆœÛXÚ×Û›Ý\ÈŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆš\×Û]]HŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ™š[\œÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›ÙÜÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

JK›Ü[Û˜[

Kˆ›Ü™Ø[š^˜][ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

KˆÛÜšÜÜXÙHŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜Ü™X]YØžHŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJB‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ð[Ó]]P›ÙRYÑY˜][H×NÂ™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ð[Ó]]P›ÙR\Ó]]QY˜][HYNÂ™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ð[Ó]]P›ÙTÙ[XÝ[Y˜][H˜[ÙNÂ™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ð[Ó]]P›ÙQ^ÛYRYÑY˜][H×NÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ð[Ó]]P›ÙHH›Ù›Øš™XÝ
ÂˆšYÈŽˆ›Ù˜\œ˜^J›ÙœÝš[™Ê
K]ZY

JK™Y˜][
˜XÙ\•\Ù\[\Ð[Ó]]P›ÙRYÑY˜][
Kˆš\×Û]]HŽˆ›Ù˜›ÛÛX[Š
K™Y˜][
˜XÙ\•\Ù\[\Ð[Ó]]P›ÙR\Ó]]QY˜][
KˆœÙ[XÝØ[Žˆ›Ù˜›ÛÛX[Š
K™Y˜][
˜XÙ\•\Ù\[\Ð[Ó]]P›ÙTÙ[XÝ[Y˜][
Kˆ™^ÛYWÚYÈŽˆ›Ù˜\œ˜^J›ÙœÝš[™Ê
K]ZY

JK™Y˜][
˜XÙ\•\Ù\[\Ð[Ó]]P›ÙQ^ÛYRYÑY˜][
BŸJB‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ñ\XØ]P›ÙS˜[YSX^HMNÂ‚‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ñ\XØ]P›ÙHH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
˜XÙ\•\Ù\[\Ñ\XØ]P›ÙS˜[YSX^
BŸJB‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ñ\XØ]T™\ÜÛœÙTÝ]\ÑY˜][HYNÂ‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ñ\XØ]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
K™Y˜][
˜XÙ\•\Ù\[\Ñ\XØ]T™\ÜÛœÙTÝ]\ÑY˜][
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ›Y\ÜØYÙHŽˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJBŸJB‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ó\Ý[Ûš]ÜœÔ]Y\žT\˜[\ÈH›Ù›Øš™XÝ
ÂˆœYÙHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	ÐHYÙH[X™\ˆÚ][ˆHYÚ[˜]Y™\Ý[Ù]‰ÊKˆ›[Z]Žˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	Ó[X™\ˆÙˆ™\Ý[ÈÈ™]\›ˆ\ˆYÙK‰ÊBŸJB‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ó\Ý[Ûš]ÜœÔ™\ÜÛœÙT™\Ý[Ò][SY]šXÓX^HMMŽÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ó\Ý[Ûš]ÜœÔ™\ÜÛœÙT™\Ý[Ò][U™\ÚÛY]šXÕ˜[YSX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ó\Ý[Ûš]ÜœÔ™\ÜÛœÙT™\Ý[Ò][PÜš]XØ[™\ÚÛ˜[YSZ[ˆHÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ó\Ý[Ûš]ÜœÔ™\ÜÛœÙT™\Ý[Ò][UØ\›š[™Õ™\ÚÛ˜[YSZ[ˆHÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ó\Ý[Ûš]ÜœÔ™\ÜÛœÙT™\Ý[Ò][P[\œ™\]Y[˜ÞSZ[ˆHNÂ™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ó\Ý[Ûš]ÜœÔ™\ÜÛœÙT™\Ý[Ò][P[\œ™\]Y[˜ÞSX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ó\Ý[Ûš]ÜœÔ™\ÜÛœÙT™\Ý[Ò][P]]Õ™\ÚÛ[YUÚ[™ÝÓZ[ˆHÂ™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ó\Ý[Ûš]ÜœÔ™\ÜÛœÙT™\Ý[Ò][P]]Õ™\ÚÛ[YUÚ[™ÝÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ó\Ý[Ûš]ÜœÔ™\ÜÛœÙT™\Ý[Ò][S›ÝYšXØ][Û‘[XZ[Ò][SX^HMÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ó\Ý[Ûš]ÜœÔ™\ÜÛœÙT™\Ý[Ò][TÛXÚÕÙXšÛÚÕ\›X^HŒÂ‚‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ó\Ý[Ûš]ÜœÔ™\ÜÛœÙHH›Ù›Øš™XÝ
Âˆ˜ÛÝ[Žˆ›Ù›[X™\Š
Kˆ›™^Žˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™]š[Ý\ÈŽˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ™\Ý[ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ›Y]šX×Û˜[YHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ\]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™[]YŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ™[]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ›Y]šX×Ý\HŽˆ›Ù™[[JÉØÛÝ[ÛÙ—Ù\œ›ÜœÉË	Ù\œ›Ü—Ü˜]\×Ù›Ü—Ù[˜Ý[Û—ØØ[[™ÉË	Ù\œ›Ü—Ùœ™YWÜÙ\ÜÚ[Û—Ü˜]\ÉË	ÜÙ\šXÙWÜ›ÝšY\—Ù\œ›Ü—Ü˜]\ÉË	ÛWØ\WÙ˜Z[\™WÜ˜]\ÉË	ÜÜ[—Ü™\ÜÛœÙWÝ[YIË	ÛWÜ™\ÜÛœÙWÝ[YIË	ÝÚÙ[—Ý\ØYÙIË	ÙZ[WÝÚÙ[œ×ÜÜ[	Ë	Û[ÛWÝÚÙ[œ×ÜÜ[	Ë	Ù]˜[X][Û—ÛY]šXÜÉ×JKˆ›Y]šXÈŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•\Ù\[\Ó\Ý[Ûš]ÜœÔ™\ÜÛœÙT™\Ý[Ò][SY]šXÓX^
K›Ü[Û˜[

K™\ØÜšX™J	ÒYÙˆH]˜[X][Ûˆ[\]K‰ÊKˆ™\ÚÛÛÜ\˜]ÜˆŽˆ›Ù™[[JÉÙÜ™X]\—Ý[‰Ë	Û\Ü×Ý[‰×JKˆ™\ÚÛÝ\HŽˆ›Ù™[[JÉÜÝ]XÉË	Ü\˜Ù[YÙWØÚ[™ÙI×JK›Ü[Û˜[

K™\ØÜšX™J	ÓY]ÙÈÙ]H™\ÚÛ›ÜˆH[Ûš]Üˆ
Ý]XÈÜˆ\˜Ù[YÙHÚ[™ÙJK‰ÊKˆ™\ÚÛÛY]šX×Ý˜[YHŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•\Ù\[\Ó\Ý[Ûš]ÜœÔ™\ÜÛœÙT™\Ý[Ò][U™\ÚÛY]šXÕ˜[YSX^
K›Ü[Û˜[

K™\ØÜšX™J	Ñ›ÜˆÚÚXÙH[™\Ü×Ù˜Z[]˜[ËHÜXÚYšXÈY]šXÈ˜[YHÈ[Ûš]Ü‹‰ÊKˆ˜Üš]XØ[Ý™\ÚÛÝ˜[YHŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\•\Ù\[\Ó\Ý[Ûš]ÜœÔ™\ÜÛœÙT™\Ý[Ò][PÜš]XØ[™\ÚÛ˜[YSZ[ŠK›Ü[Û˜[

KˆØ\›š[™×Ý™\ÚÛÝ˜[YHŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\•\Ù\[\Ó\Ý[Ûš]ÜœÔ™\ÜÛœÙT™\Ý[Ò][UØ\›š[™Õ™\ÚÛ˜[YSZ[ŠK›Ü[Û˜[

Kˆ˜[\Ùœ™\]Y[˜ÞHŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\•\Ù\[\Ó\Ý[Ûš]ÜœÔ™\ÜÛœÙT™\Ý[Ò][P[\œ™\]Y[˜ÞSZ[ŠK›X^
˜XÙ\•\Ù\[\Ó\Ý[Ûš]ÜœÔ™\ÜÛœÙT™\Ý[Ò][P[\œ™\]Y[˜ÞSX^
K›Ü[Û˜[

K™\ØÜšX™J	Ñœ™\]Y[˜ÞHÙˆ[\ÚXÚÜÈ[ˆZ[]\Ë‰ÊKˆ˜]]×Ý™\ÚÛÝ[YWÝÚ[™ÝÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\•\Ù\[\Ó\Ý[Ûš]ÜœÔ™\ÜÛœÙT™\Ý[Ò][P]]Õ™\ÚÛ[YUÚ[™ÝÓZ[ŠK›X^
˜XÙ\•\Ù\[\Ó\Ý[Ûš]ÜœÔ™\ÜÛœÙT™\Ý[Ò][P]]Õ™\ÚÛ[YUÚ[™ÝÓX^
K›Ü[Û˜[

K™\ØÜšX™J	Ñ›Üˆ]]Ë]™\ÚÛ[™ËˆH[YHÚ[™ÝÈ[ˆZ[]\ÈÈØ[Ý[]HH\ÝÜšXØ[YX[‰ÊKˆ›\ÝØÚXÚÙYØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

K™\ØÜšX™J	ÕH\Ý[YHH[Ûš]ÜˆØ\ÈÚXÚÙY›Üˆ[\Ë‰ÊKˆ››ÝYšXØ][Û—Ù[XZ[ÈŽˆ›Ù˜\œ˜^J›ÙœÝš[™Ê
K™[XZ[

K›Z[ŠJK›X^
˜XÙ\•\Ù\[\Ó\Ý[Ûš]ÜœÔ™\ÜÛœÙT™\Ý[Ò][S›ÝYšXØ][Û‘[XZ[Ò][SX^
JK›Ü[Û˜[

KˆœÛXÚ×ÝÙXšÛÚ×Ý\›Žˆ›ÙœÝš[™Ê
K\›

K›X^
˜XÙ\•\Ù\[\Ó\Ý[Ûš]ÜœÔ™\ÜÛœÙT™\Ý[Ò][TÛXÚÕÙXšÛÚÕ\›X^
K›Ü[Û˜[

KˆœÛXÚ×Û›Ý\ÈŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆš\×Û]]HŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ™š[\œÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›ÙÜÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

JK›Ü[Û˜[

Kˆ›Ü™Ø[š^˜][ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

KˆÛÜšÜÜXÙHŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜Ü™X]YØžHŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJJBŸJB‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÓY]šXÓÜ[ÛœÔ]Y\žT\˜[\ÈH›Ù›Øš™XÝ
ÂˆœYÙHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	ÐHYÙH[X™\ˆÚ][ˆHYÚ[˜]Y™\Ý[Ù]‰ÊKˆ›[Z]Žˆ›Ù›[X™\Š
K›Ü[Û˜[

K™\ØÜšX™J	Ó[X™\ˆÙˆ™\Ý[ÈÈ™]\›ˆ\ˆYÙK‰ÊBŸJB‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÓY]šXÓÜ[ÛœÔ™\ÜÛœÙTÝ]\ÑY˜][HYNÂ‚‚‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÓY]šXÓÜ[ÛœÔ™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
K™Y˜][
˜XÙ\•\Ù\[\ÓY]šXÓÜ[ÛœÔ™\ÜÛœÙTÝ]\ÑY˜][
Kˆœ™\Ý[Žˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ›Y]šX×Ý\HŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ›Ý]]Ý\HŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

BŸJJK›Ü[Û˜[

BŸJB‚‚‹ÊŠ‚ˆ
ˆ™]\›œÈ[YK\Ù\šY\È]H›ÜˆH[\Ü˜\žH[Ûš]Ü‰ÜÈY]šXËÝZ]X›H›ÜˆÜ˜\[™ÈH™]šY]Ë‚XØÙ\È[Ûš]ÜˆÛÛ™šYÝ\˜][Ûˆ[ˆH™\]Y\Ý›ÙK‚ˆ
‹Â™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ô™]šY]ÑÜ˜\›ÙSY]šXÓX^HMMŽÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ô™]šY]ÑÜ˜\›ÙU™\ÚÛY]šXÕ˜[YSX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ô™]šY]ÑÜ˜\›ÙPÜš]XØ[™\ÚÛ˜[YSZ[ˆHÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ô™]šY]ÑÜ˜\›ÙUØ\›š[™Õ™\ÚÛ˜[YSZ[ˆHÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ô™]šY]ÑÜ˜\›ÙP[\œ™\]Y[˜ÞSZ[ˆHNÂ™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ô™]šY]ÑÜ˜\›ÙP[\œ™\]Y[˜ÞSX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ô™]šY]ÑÜ˜\›ÙP]]Õ™\ÚÛ[YUÚ[™ÝÓZ[ˆHÂ™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ô™]šY]ÑÜ˜\›ÙP]]Õ™\ÚÛ[YUÚ[™ÝÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ô™]šY]ÑÜ˜\›ÙS›ÝYšXØ][Û‘[XZ[Ò][SX^HMÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ô™]šY]ÑÜ˜\›ÙTÛXÚÕÙXšÛÚÕ\›X^HŒÂ‚‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ô™]šY]ÑÜ˜\›ÙHH›Ù›Øš™XÝ
Âˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ™[]YŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ™[]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ›Y]šX×Ý\HŽˆ›Ù™[[JÉØÛÝ[ÛÙ—Ù\œ›ÜœÉË	Ù\œ›Ü—Ü˜]\×Ù›Ü—Ù[˜Ý[Û—ØØ[[™ÉË	Ù\œ›Ü—Ùœ™YWÜÙ\ÜÚ[Û—Ü˜]\ÉË	ÜÙ\šXÙWÜ›ÝšY\—Ù\œ›Ü—Ü˜]\ÉË	ÛWØ\WÙ˜Z[\™WÜ˜]\ÉË	ÜÜ[—Ü™\ÜÛœÙWÝ[YIË	ÛWÜ™\ÜÛœÙWÝ[YIË	ÝÚÙ[—Ý\ØYÙIË	ÙZ[WÝÚÙ[œ×ÜÜ[	Ë	Û[ÛWÝÚÙ[œ×ÜÜ[	Ë	Ù]˜[X][Û—ÛY]šXÜÉ×JKˆ›Y]šXÈŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•\Ù\[\Ô™]šY]ÑÜ˜\›ÙSY]šXÓX^
K›Ü[Û˜[

K™\ØÜšX™J	ÒYÙˆH]˜[X][Ûˆ[\]K‰ÊKˆ™\ÚÛÛÜ\˜]ÜˆŽˆ›Ù™[[JÉÙÜ™X]\—Ý[‰Ë	Û\Ü×Ý[‰×JKˆ™\ÚÛÝ\HŽˆ›Ù™[[JÉÜÝ]XÉË	Ü\˜Ù[YÙWØÚ[™ÙI×JK›Ü[Û˜[

K™\ØÜšX™J	ÓY]ÙÈÙ]H™\ÚÛ›ÜˆH[Ûš]Üˆ
Ý]XÈÜˆ\˜Ù[YÙHÚ[™ÙJK‰ÊKˆ™\ÚÛÛY]šX×Ý˜[YHŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•\Ù\[\Ô™]šY]ÑÜ˜\›ÙU™\ÚÛY]šXÕ˜[YSX^
K›Ü[Û˜[

K™\ØÜšX™J	Ñ›ÜˆÚÚXÙH[™\Ü×Ù˜Z[]˜[ËHÜXÚYšXÈY]šXÈ˜[YHÈ[Ûš]Ü‹‰ÊKˆ˜Üš]XØ[Ý™\ÚÛÝ˜[YHŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\•\Ù\[\Ô™]šY]ÑÜ˜\›ÙPÜš]XØ[™\ÚÛ˜[YSZ[ŠK›Ü[Û˜[

KˆØ\›š[™×Ý™\ÚÛÝ˜[YHŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\•\Ù\[\Ô™]šY]ÑÜ˜\›ÙUØ\›š[™Õ™\ÚÛ˜[YSZ[ŠK›Ü[Û˜[

Kˆ˜[\Ùœ™\]Y[˜ÞHŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\•\Ù\[\Ô™]šY]ÑÜ˜\›ÙP[\œ™\]Y[˜ÞSZ[ŠK›X^
˜XÙ\•\Ù\[\Ô™]šY]ÑÜ˜\›ÙP[\œ™\]Y[˜ÞSX^
K›Ü[Û˜[

K™\ØÜšX™J	Ñœ™\]Y[˜ÞHÙˆ[\ÚXÚÜÈ[ˆZ[]\Ë‰ÊKˆ˜]]×Ý™\ÚÛÝ[YWÝÚ[™ÝÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\•\Ù\[\Ô™]šY]ÑÜ˜\›ÙP]]Õ™\ÚÛ[YUÚ[™ÝÓZ[ŠK›X^
˜XÙ\•\Ù\[\Ô™]šY]ÑÜ˜\›ÙP]]Õ™\ÚÛ[YUÚ[™ÝÓX^
K›Ü[Û˜[

K™\ØÜšX™J	Ñ›Üˆ]]Ë]™\ÚÛ[™ËˆH[YHÚ[™ÝÈ[ˆZ[]\ÈÈØ[Ý[]HH\ÝÜšXØ[YX[‰ÊKˆ›\ÝØÚXÚÙYØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

K™\ØÜšX™J	ÕH\Ý[YHH[Ûš]ÜˆØ\ÈÚXÚÙY›Üˆ[\Ë‰ÊKˆ››ÝYšXØ][Û—Ù[XZ[ÈŽˆ›Ù˜\œ˜^J›ÙœÝš[™Ê
K™[XZ[

K›Z[ŠJK›X^
˜XÙ\•\Ù\[\Ô™]šY]ÑÜ˜\›ÙS›ÝYšXØ][Û‘[XZ[Ò][SX^
JK›Ü[Û˜[

KˆœÛXÚ×ÝÙXšÛÚ×Ý\›Žˆ›ÙœÝš[™Ê
K\›

K›X^
˜XÙ\•\Ù\[\Ô™]šY]ÑÜ˜\›ÙTÛXÚÕÙXšÛÚÕ\›X^
K›Ü[Û˜[

KˆœÛXÚ×Û›Ý\ÈŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆš\×Û]]HŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ™š[\œÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›ÙÜÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

JK›Ü[Û˜[

Kˆ›Ü™Ø[š^˜][ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

KˆÛÜšÜÜXÙHŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜Ü™X]YØžHŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJB‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ô™XY\˜[\ÈH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
BŸJB‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ô™XY™\ÜÛœÙSY]šXÓX^HMMŽÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ô™XY™\ÜÛœÙU™\ÚÛY]šXÕ˜[YSX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ô™XY™\ÜÛœÙPÜš]XØ[™\ÚÛ˜[YSZ[ˆHÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ô™XY™\ÜÛœÙUØ\›š[™Õ™\ÚÛ˜[YSZ[ˆHÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ô™XY™\ÜÛœÙP[\œ™\]Y[˜ÞSZ[ˆHNÂ™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ô™XY™\ÜÛœÙP[\œ™\]Y[˜ÞSX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ô™XY™\ÜÛœÙP]]Õ™\ÚÛ[YUÚ[™ÝÓZ[ˆHÂ™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ô™XY™\ÜÛœÙP]]Õ™\ÚÛ[YUÚ[™ÝÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ô™XY™\ÜÛœÙS›ÝYšXØ][Û‘[XZ[Ò][SX^HMÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ô™XY™\ÜÛœÙTÛXÚÕÙXšÛÚÕ\›X^HŒÂ‚‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ô™XY™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ›Y]šX×Û˜[YHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ\]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™[]YŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ™[]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ›Y]šX×Ý\HŽˆ›Ù™[[JÉØÛÝ[ÛÙ—Ù\œ›ÜœÉË	Ù\œ›Ü—Ü˜]\×Ù›Ü—Ù[˜Ý[Û—ØØ[[™ÉË	Ù\œ›Ü—Ùœ™YWÜÙ\ÜÚ[Û—Ü˜]\ÉË	ÜÙ\šXÙWÜ›ÝšY\—Ù\œ›Ü—Ü˜]\ÉË	ÛWØ\WÙ˜Z[\™WÜ˜]\ÉË	ÜÜ[—Ü™\ÜÛœÙWÝ[YIË	ÛWÜ™\ÜÛœÙWÝ[YIË	ÝÚÙ[—Ý\ØYÙIË	ÙZ[WÝÚÙ[œ×ÜÜ[	Ë	Û[ÛWÝÚÙ[œ×ÜÜ[	Ë	Ù]˜[X][Û—ÛY]šXÜÉ×JKˆ›Y]šXÈŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•\Ù\[\Ô™XY™\ÜÛœÙSY]šXÓX^
K›Ü[Û˜[

K™\ØÜšX™J	ÒYÙˆH]˜[X][Ûˆ[\]K‰ÊKˆ™\ÚÛÛÜ\˜]ÜˆŽˆ›Ù™[[JÉÙÜ™X]\—Ý[‰Ë	Û\Ü×Ý[‰×JKˆ™\ÚÛÝ\HŽˆ›Ù™[[JÉÜÝ]XÉË	Ü\˜Ù[YÙWØÚ[™ÙI×JK›Ü[Û˜[

K™\ØÜšX™J	ÓY]ÙÈÙ]H™\ÚÛ›ÜˆH[Ûš]Üˆ
Ý]XÈÜˆ\˜Ù[YÙHÚ[™ÙJK‰ÊKˆ™\ÚÛÛY]šX×Ý˜[YHŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•\Ù\[\Ô™XY™\ÜÛœÙU™\ÚÛY]šXÕ˜[YSX^
K›Ü[Û˜[

K™\ØÜšX™J	Ñ›ÜˆÚÚXÙH[™\Ü×Ù˜Z[]˜[ËHÜXÚYšXÈY]šXÈ˜[YHÈ[Ûš]Ü‹‰ÊKˆ˜Üš]XØ[Ý™\ÚÛÝ˜[YHŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\•\Ù\[\Ô™XY™\ÜÛœÙPÜš]XØ[™\ÚÛ˜[YSZ[ŠK›Ü[Û˜[

KˆØ\›š[™×Ý™\ÚÛÝ˜[YHŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\•\Ù\[\Ô™XY™\ÜÛœÙUØ\›š[™Õ™\ÚÛ˜[YSZ[ŠK›Ü[Û˜[

Kˆ˜[\Ùœ™\]Y[˜ÞHŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\•\Ù\[\Ô™XY™\ÜÛœÙP[\œ™\]Y[˜ÞSZ[ŠK›X^
˜XÙ\•\Ù\[\Ô™XY™\ÜÛœÙP[\œ™\]Y[˜ÞSX^
K›Ü[Û˜[

K™\ØÜšX™J	Ñœ™\]Y[˜ÞHÙˆ[\ÚXÚÜÈ[ˆZ[]\Ë‰ÊKˆ˜]]×Ý™\ÚÛÝ[YWÝÚ[™ÝÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\•\Ù\[\Ô™XY™\ÜÛœÙP]]Õ™\ÚÛ[YUÚ[™ÝÓZ[ŠK›X^
˜XÙ\•\Ù\[\Ô™XY™\ÜÛœÙP]]Õ™\ÚÛ[YUÚ[™ÝÓX^
K›Ü[Û˜[

K™\ØÜšX™J	Ñ›Üˆ]]Ë]™\ÚÛ[™ËˆH[YHÚ[™ÝÈ[ˆZ[]\ÈÈØ[Ý[]HH\ÝÜšXØ[YX[‰ÊKˆ›\ÝØÚXÚÙYØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

K™\ØÜšX™J	ÕH\Ý[YHH[Ûš]ÜˆØ\ÈÚXÚÙY›Üˆ[\Ë‰ÊKˆ››ÝYšXØ][Û—Ù[XZ[ÈŽˆ›Ù˜\œ˜^J›ÙœÝš[™Ê
K™[XZ[

K›Z[ŠJK›X^
˜XÙ\•\Ù\[\Ô™XY™\ÜÛœÙS›ÝYšXØ][Û‘[XZ[Ò][SX^
JK›Ü[Û˜[

KˆœÛXÚ×ÝÙXšÛÚ×Ý\›Žˆ›ÙœÝš[™Ê
K\›

K›X^
˜XÙ\•\Ù\[\Ô™XY™\ÜÛœÙTÛXÚÕÙXšÛÚÕ\›X^
K›Ü[Û˜[

KˆœÛXÚ×Û›Ý\ÈŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆš\×Û]]HŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ™š[\œÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›ÙÜÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

JK›Ü[Û˜[

Kˆ›Ü™Ø[š^˜][ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

KˆÛÜšÜÜXÙHŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜Ü™X]YØžHŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJB‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Õ\]T\˜[\ÈH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
BŸJB‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Õ\]P›ÙSY]šXÓX^HMMŽÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Õ\]P›ÙU™\ÚÛY]šXÕ˜[YSX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Õ\]P›ÙPÜš]XØ[™\ÚÛ˜[YSZ[ˆHÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Õ\]P›ÙUØ\›š[™Õ™\ÚÛ˜[YSZ[ˆHÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Õ\]P›ÙP[\œ™\]Y[˜ÞSZ[ˆHNÂ™^ÜÛÛœÝ˜XÙ\•\Ù\[\Õ\]P›ÙP[\œ™\]Y[˜ÞSX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Õ\]P›ÙP]]Õ™\ÚÛ[YUÚ[™ÝÓZ[ˆHÂ™^ÜÛÛœÝ˜XÙ\•\Ù\[\Õ\]P›ÙP]]Õ™\ÚÛ[YUÚ[™ÝÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Õ\]P›ÙS›ÝYšXØ][Û‘[XZ[Ò][SX^HMÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Õ\]P›ÙTÛXÚÕÙXšÛÚÕ\›X^HŒÂ‚‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Õ\]P›ÙHH›Ù›Øš™XÝ
Âˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ™[]YŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ™[]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ›Y]šX×Ý\HŽˆ›Ù™[[JÉØÛÝ[ÛÙ—Ù\œ›ÜœÉË	Ù\œ›Ü—Ü˜]\×Ù›Ü—Ù[˜Ý[Û—ØØ[[™ÉË	Ù\œ›Ü—Ùœ™YWÜÙ\ÜÚ[Û—Ü˜]\ÉË	ÜÙ\šXÙWÜ›ÝšY\—Ù\œ›Ü—Ü˜]\ÉË	ÛWØ\WÙ˜Z[\™WÜ˜]\ÉË	ÜÜ[—Ü™\ÜÛœÙWÝ[YIË	ÛWÜ™\ÜÛœÙWÝ[YIË	ÝÚÙ[—Ý\ØYÙIË	ÙZ[WÝÚÙ[œ×ÜÜ[	Ë	Û[ÛWÝÚÙ[œ×ÜÜ[	Ë	Ù]˜[X][Û—ÛY]šXÜÉ×JKˆ›Y]šXÈŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•\Ù\[\Õ\]P›ÙSY]šXÓX^
K›Ü[Û˜[

K™\ØÜšX™J	ÒYÙˆH]˜[X][Ûˆ[\]K‰ÊKˆ™\ÚÛÛÜ\˜]ÜˆŽˆ›Ù™[[JÉÙÜ™X]\—Ý[‰Ë	Û\Ü×Ý[‰×JKˆ™\ÚÛÝ\HŽˆ›Ù™[[JÉÜÝ]XÉË	Ü\˜Ù[YÙWØÚ[™ÙI×JK›Ü[Û˜[

K™\ØÜšX™J	ÓY]ÙÈÙ]H™\ÚÛ›ÜˆH[Ûš]Üˆ
Ý]XÈÜˆ\˜Ù[YÙHÚ[™ÙJK‰ÊKˆ™\ÚÛÛY]šX×Ý˜[YHŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•\Ù\[\Õ\]P›ÙU™\ÚÛY]šXÕ˜[YSX^
K›Ü[Û˜[

K™\ØÜšX™J	Ñ›ÜˆÚÚXÙH[™\Ü×Ù˜Z[]˜[ËHÜXÚYšXÈY]šXÈ˜[YHÈ[Ûš]Ü‹‰ÊKˆ˜Üš]XØ[Ý™\ÚÛÝ˜[YHŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\•\Ù\[\Õ\]P›ÙPÜš]XØ[™\ÚÛ˜[YSZ[ŠK›Ü[Û˜[

KˆØ\›š[™×Ý™\ÚÛÝ˜[YHŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\•\Ù\[\Õ\]P›ÙUØ\›š[™Õ™\ÚÛ˜[YSZ[ŠK›Ü[Û˜[

Kˆ˜[\Ùœ™\]Y[˜ÞHŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\•\Ù\[\Õ\]P›ÙP[\œ™\]Y[˜ÞSZ[ŠK›X^
˜XÙ\•\Ù\[\Õ\]P›ÙP[\œ™\]Y[˜ÞSX^
K›Ü[Û˜[

K™\ØÜšX™J	Ñœ™\]Y[˜ÞHÙˆ[\ÚXÚÜÈ[ˆZ[]\Ë‰ÊKˆ˜]]×Ý™\ÚÛÝ[YWÝÚ[™ÝÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\•\Ù\[\Õ\]P›ÙP]]Õ™\ÚÛ[YUÚ[™ÝÓZ[ŠK›X^
˜XÙ\•\Ù\[\Õ\]P›ÙP]]Õ™\ÚÛ[YUÚ[™ÝÓX^
K›Ü[Û˜[

K™\ØÜšX™J	Ñ›Üˆ]]Ë]™\ÚÛ[™ËˆH[YHÚ[™ÝÈ[ˆZ[]\ÈÈØ[Ý[]HH\ÝÜšXØ[YX[‰ÊKˆ›\ÝØÚXÚÙYØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

K™\ØÜšX™J	ÕH\Ý[YHH[Ûš]ÜˆØ\ÈÚXÚÙY›Üˆ[\Ë‰ÊKˆ››ÝYšXØ][Û—Ù[XZ[ÈŽˆ›Ù˜\œ˜^J›ÙœÝš[™Ê
K™[XZ[

K›Z[ŠJK›X^
˜XÙ\•\Ù\[\Õ\]P›ÙS›ÝYšXØ][Û‘[XZ[Ò][SX^
JK›Ü[Û˜[

KˆœÛXÚ×ÝÙXšÛÚ×Ý\›Žˆ›ÙœÝš[™Ê
K\›

K›X^
˜XÙ\•\Ù\[\Õ\]P›ÙTÛXÚÕÙXšÛÚÕ\›X^
K›Ü[Û˜[

KˆœÛXÚ×Û›Ý\ÈŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆš\×Û]]HŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ™š[\œÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›ÙÜÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

JK›Ü[Û˜[

Kˆ›Ü™Ø[š^˜][ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

KˆÛÜšÜÜXÙHŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜Ü™X]YØžHŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJB‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Õ\]T™\ÜÛœÙSY]šXÓX^HMMŽÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Õ\]T™\ÜÛœÙU™\ÚÛY]šXÕ˜[YSX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Õ\]T™\ÜÛœÙPÜš]XØ[™\ÚÛ˜[YSZ[ˆHÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Õ\]T™\ÜÛœÙUØ\›š[™Õ™\ÚÛ˜[YSZ[ˆHÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Õ\]T™\ÜÛœÙP[\œ™\]Y[˜ÞSZ[ˆHNÂ™^ÜÛÛœÝ˜XÙ\•\Ù\[\Õ\]T™\ÜÛœÙP[\œ™\]Y[˜ÞSX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Õ\]T™\ÜÛœÙP]]Õ™\ÚÛ[YUÚ[™ÝÓZ[ˆHÂ™^ÜÛÛœÝ˜XÙ\•\Ù\[\Õ\]T™\ÜÛœÙP]]Õ™\ÚÛ[YUÚ[™ÝÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Õ\]T™\ÜÛœÙS›ÝYšXØ][Û‘[XZ[Ò][SX^HMÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Õ\]T™\ÜÛœÙTÛXÚÕÙXšÛÚÕ\›X^HŒÂ‚‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Õ\]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ›Y]šX×Û˜[YHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ\]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™[]YŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ™[]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ›Y]šX×Ý\HŽˆ›Ù™[[JÉØÛÝ[ÛÙ—Ù\œ›ÜœÉË	Ù\œ›Ü—Ü˜]\×Ù›Ü—Ù[˜Ý[Û—ØØ[[™ÉË	Ù\œ›Ü—Ùœ™YWÜÙ\ÜÚ[Û—Ü˜]\ÉË	ÜÙ\šXÙWÜ›ÝšY\—Ù\œ›Ü—Ü˜]\ÉË	ÛWØ\WÙ˜Z[\™WÜ˜]\ÉË	ÜÜ[—Ü™\ÜÛœÙWÝ[YIË	ÛWÜ™\ÜÛœÙWÝ[YIË	ÝÚÙ[—Ý\ØYÙIË	ÙZ[WÝÚÙ[œ×ÜÜ[	Ë	Û[ÛWÝÚÙ[œ×ÜÜ[	Ë	Ù]˜[X][Û—ÛY]šXÜÉ×JKˆ›Y]šXÈŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•\Ù\[\Õ\]T™\ÜÛœÙSY]šXÓX^
K›Ü[Û˜[

K™\ØÜšX™J	ÒYÙˆH]˜[X][Ûˆ[\]K‰ÊKˆ™\ÚÛÛÜ\˜]ÜˆŽˆ›Ù™[[JÉÙÜ™X]\—Ý[‰Ë	Û\Ü×Ý[‰×JKˆ™\ÚÛÝ\HŽˆ›Ù™[[JÉÜÝ]XÉË	Ü\˜Ù[YÙWØÚ[™ÙI×JK›Ü[Û˜[

K™\ØÜšX™J	ÓY]ÙÈÙ]H™\ÚÛ›ÜˆH[Ûš]Üˆ
Ý]XÈÜˆ\˜Ù[YÙHÚ[™ÙJK‰ÊKˆ™\ÚÛÛY]šX×Ý˜[YHŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•\Ù\[\Õ\]T™\ÜÛœÙU™\ÚÛY]šXÕ˜[YSX^
K›Ü[Û˜[

K™\ØÜšX™J	Ñ›ÜˆÚÚXÙH[™\Ü×Ù˜Z[]˜[ËHÜXÚYšXÈY]šXÈ˜[YHÈ[Ûš]Ü‹‰ÊKˆ˜Üš]XØ[Ý™\ÚÛÝ˜[YHŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\•\Ù\[\Õ\]T™\ÜÛœÙPÜš]XØ[™\ÚÛ˜[YSZ[ŠK›Ü[Û˜[

KˆØ\›š[™×Ý™\ÚÛÝ˜[YHŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\•\Ù\[\Õ\]T™\ÜÛœÙUØ\›š[™Õ™\ÚÛ˜[YSZ[ŠK›Ü[Û˜[

Kˆ˜[\Ùœ™\]Y[˜ÞHŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\•\Ù\[\Õ\]T™\ÜÛœÙP[\œ™\]Y[˜ÞSZ[ŠK›X^
˜XÙ\•\Ù\[\Õ\]T™\ÜÛœÙP[\œ™\]Y[˜ÞSX^
K›Ü[Û˜[

K™\ØÜšX™J	Ñœ™\]Y[˜ÞHÙˆ[\ÚXÚÜÈ[ˆZ[]\Ë‰ÊKˆ˜]]×Ý™\ÚÛÝ[YWÝÚ[™ÝÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\•\Ù\[\Õ\]T™\ÜÛœÙP]]Õ™\ÚÛ[YUÚ[™ÝÓZ[ŠK›X^
˜XÙ\•\Ù\[\Õ\]T™\ÜÛœÙP]]Õ™\ÚÛ[YUÚ[™ÝÓX^
K›Ü[Û˜[

K™\ØÜšX™J	Ñ›Üˆ]]Ë]™\ÚÛ[™ËˆH[YHÚ[™ÝÈ[ˆZ[]\ÈÈØ[Ý[]HH\ÝÜšXØ[YX[‰ÊKˆ›\ÝØÚXÚÙYØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

K™\ØÜšX™J	ÕH\Ý[YHH[Ûš]ÜˆØ\ÈÚXÚÙY›Üˆ[\Ë‰ÊKˆ››ÝYšXØ][Û—Ù[XZ[ÈŽˆ›Ù˜\œ˜^J›ÙœÝš[™Ê
K™[XZ[

K›Z[ŠJK›X^
˜XÙ\•\Ù\[\Õ\]T™\ÜÛœÙS›ÝYšXØ][Û‘[XZ[Ò][SX^
JK›Ü[Û˜[

KˆœÛXÚ×ÝÙXšÛÚ×Ý\›Žˆ›ÙœÝš[™Ê
K\›

K›X^
˜XÙ\•\Ù\[\Õ\]T™\ÜÛœÙTÛXÚÕÙXšÛÚÕ\›X^
K›Ü[Û˜[

KˆœÛXÚ×Û›Ý\ÈŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆš\×Û]]HŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ™š[\œÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›ÙÜÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

JK›Ü[Û˜[

Kˆ›Ü™Ø[š^˜][ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

KˆÛÜšÜÜXÙHŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜Ü™X]YØžHŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJB‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ô\X[\]T\˜[\ÈH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
BŸJB‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ô\X[\]P›ÙSY]šXÓX^HMMŽÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ô\X[\]P›ÙU™\ÚÛY]šXÕ˜[YSX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ô\X[\]P›ÙPÜš]XØ[™\ÚÛ˜[YSZ[ˆHÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ô\X[\]P›ÙUØ\›š[™Õ™\ÚÛ˜[YSZ[ˆHÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ô\X[\]P›ÙP[\œ™\]Y[˜ÞSZ[ˆHNÂ™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ô\X[\]P›ÙP[\œ™\]Y[˜ÞSX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ô\X[\]P›ÙP]]Õ™\ÚÛ[YUÚ[™ÝÓZ[ˆHÂ™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ô\X[\]P›ÙP]]Õ™\ÚÛ[YUÚ[™ÝÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ô\X[\]P›ÙS›ÝYšXØ][Û‘[XZ[Ò][SX^HMÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ô\X[\]P›ÙTÛXÚÕÙXšÛÚÕ\›X^HŒÂ‚‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ô\X[\]P›ÙHH›Ù›Øš™XÝ
Âˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ™[]YŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ™[]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ›Y]šX×Ý\HŽˆ›Ù™[[JÉØÛÝ[ÛÙ—Ù\œ›ÜœÉË	Ù\œ›Ü—Ü˜]\×Ù›Ü—Ù[˜Ý[Û—ØØ[[™ÉË	Ù\œ›Ü—Ùœ™YWÜÙ\ÜÚ[Û—Ü˜]\ÉË	ÜÙ\šXÙWÜ›ÝšY\—Ù\œ›Ü—Ü˜]\ÉË	ÛWØ\WÙ˜Z[\™WÜ˜]\ÉË	ÜÜ[—Ü™\ÜÛœÙWÝ[YIË	ÛWÜ™\ÜÛœÙWÝ[YIË	ÝÚÙ[—Ý\ØYÙIË	ÙZ[WÝÚÙ[œ×ÜÜ[	Ë	Û[ÛWÝÚÙ[œ×ÜÜ[	Ë	Ù]˜[X][Û—ÛY]šXÜÉ×JKˆ›Y]šXÈŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•\Ù\[\Ô\X[\]P›ÙSY]šXÓX^
K›Ü[Û˜[

K™\ØÜšX™J	ÒYÙˆH]˜[X][Ûˆ[\]K‰ÊKˆ™\ÚÛÛÜ\˜]ÜˆŽˆ›Ù™[[JÉÙÜ™X]\—Ý[‰Ë	Û\Ü×Ý[‰×JKˆ™\ÚÛÝ\HŽˆ›Ù™[[JÉÜÝ]XÉË	Ü\˜Ù[YÙWØÚ[™ÙI×JK›Ü[Û˜[

K™\ØÜšX™J	ÓY]ÙÈÙ]H™\ÚÛ›ÜˆH[Ûš]Üˆ
Ý]XÈÜˆ\˜Ù[YÙHÚ[™ÙJK‰ÊKˆ™\ÚÛÛY]šX×Ý˜[YHŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•\Ù\[\Ô\X[\]P›ÙU™\ÚÛY]šXÕ˜[YSX^
K›Ü[Û˜[

K™\ØÜšX™J	Ñ›ÜˆÚÚXÙH[™\Ü×Ù˜Z[]˜[ËHÜXÚYšXÈY]šXÈ˜[YHÈ[Ûš]Ü‹‰ÊKˆ˜Üš]XØ[Ý™\ÚÛÝ˜[YHŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\•\Ù\[\Ô\X[\]P›ÙPÜš]XØ[™\ÚÛ˜[YSZ[ŠK›Ü[Û˜[

KˆØ\›š[™×Ý™\ÚÛÝ˜[YHŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\•\Ù\[\Ô\X[\]P›ÙUØ\›š[™Õ™\ÚÛ˜[YSZ[ŠK›Ü[Û˜[

Kˆ˜[\Ùœ™\]Y[˜ÞHŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\•\Ù\[\Ô\X[\]P›ÙP[\œ™\]Y[˜ÞSZ[ŠK›X^
˜XÙ\•\Ù\[\Ô\X[\]P›ÙP[\œ™\]Y[˜ÞSX^
K›Ü[Û˜[

K™\ØÜšX™J	Ñœ™\]Y[˜ÞHÙˆ[\ÚXÚÜÈ[ˆZ[]\Ë‰ÊKˆ˜]]×Ý™\ÚÛÝ[YWÝÚ[™ÝÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\•\Ù\[\Ô\X[\]P›ÙP]]Õ™\ÚÛ[YUÚ[™ÝÓZ[ŠK›X^
˜XÙ\•\Ù\[\Ô\X[\]P›ÙP]]Õ™\ÚÛ[YUÚ[™ÝÓX^
K›Ü[Û˜[

K™\ØÜšX™J	Ñ›Üˆ]]Ë]™\ÚÛ[™ËˆH[YHÚ[™ÝÈ[ˆZ[]\ÈÈØ[Ý[]HH\ÝÜšXØ[YX[‰ÊKˆ›\ÝØÚXÚÙYØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

K™\ØÜšX™J	ÕH\Ý[YHH[Ûš]ÜˆØ\ÈÚXÚÙY›Üˆ[\Ë‰ÊKˆ››ÝYšXØ][Û—Ù[XZ[ÈŽˆ›Ù˜\œ˜^J›ÙœÝš[™Ê
K™[XZ[

K›Z[ŠJK›X^
˜XÙ\•\Ù\[\Ô\X[\]P›ÙS›ÝYšXØ][Û‘[XZ[Ò][SX^
JK›Ü[Û˜[

KˆœÛXÚ×ÝÙXšÛÚ×Ý\›Žˆ›ÙœÝš[™Ê
K\›

K›X^
˜XÙ\•\Ù\[\Ô\X[\]P›ÙTÛXÚÕÙXšÛÚÕ\›X^
K›Ü[Û˜[

KˆœÛXÚ×Û›Ý\ÈŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆš\×Û]]HŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ™š[\œÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›ÙÜÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

JK›Ü[Û˜[

Kˆ›Ü™Ø[š^˜][ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

KˆÛÜšÜÜXÙHŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜Ü™X]YØžHŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJB‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ô\X[\]T™\ÜÛœÙSY]šXÓX^HMMŽÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ô\X[\]T™\ÜÛœÙU™\ÚÛY]šXÕ˜[YSX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ô\X[\]T™\ÜÛœÙPÜš]XØ[™\ÚÛ˜[YSZ[ˆHÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ô\X[\]T™\ÜÛœÙUØ\›š[™Õ™\ÚÛ˜[YSZ[ˆHÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ô\X[\]T™\ÜÛœÙP[\œ™\]Y[˜ÞSZ[ˆHNÂ™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ô\X[\]T™\ÜÛœÙP[\œ™\]Y[˜ÞSX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ô\X[\]T™\ÜÛœÙP]]Õ™\ÚÛ[YUÚ[™ÝÓZ[ˆHÂ™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ô\X[\]T™\ÜÛœÙP]]Õ™\ÚÛ[YUÚ[™ÝÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ô\X[\]T™\ÜÛœÙS›ÝYšXØ][Û‘[XZ[Ò][SX^HMÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ô\X[\]T™\ÜÛœÙTÛXÚÕÙXšÛÚÕ\›X^HŒÂ‚‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ô\X[\]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ›Y]šX×Û˜[YHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ\]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™[]YŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ™[]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ›Y]šX×Ý\HŽˆ›Ù™[[JÉØÛÝ[ÛÙ—Ù\œ›ÜœÉË	Ù\œ›Ü—Ü˜]\×Ù›Ü—Ù[˜Ý[Û—ØØ[[™ÉË	Ù\œ›Ü—Ùœ™YWÜÙ\ÜÚ[Û—Ü˜]\ÉË	ÜÙ\šXÙWÜ›ÝšY\—Ù\œ›Ü—Ü˜]\ÉË	ÛWØ\WÙ˜Z[\™WÜ˜]\ÉË	ÜÜ[—Ü™\ÜÛœÙWÝ[YIË	ÛWÜ™\ÜÛœÙWÝ[YIË	ÝÚÙ[—Ý\ØYÙIË	ÙZ[WÝÚÙ[œ×ÜÜ[	Ë	Û[ÛWÝÚÙ[œ×ÜÜ[	Ë	Ù]˜[X][Û—ÛY]šXÜÉ×JKˆ›Y]šXÈŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•\Ù\[\Ô\X[\]T™\ÜÛœÙSY]šXÓX^
K›Ü[Û˜[

K™\ØÜšX™J	ÒYÙˆH]˜[X][Ûˆ[\]K‰ÊKˆ™\ÚÛÛÜ\˜]ÜˆŽˆ›Ù™[[JÉÙÜ™X]\—Ý[‰Ë	Û\Ü×Ý[‰×JKˆ™\ÚÛÝ\HŽˆ›Ù™[[JÉÜÝ]XÉË	Ü\˜Ù[YÙWØÚ[™ÙI×JK›Ü[Û˜[

K™\ØÜšX™J	ÓY]ÙÈÙ]H™\ÚÛ›ÜˆH[Ûš]Üˆ
Ý]XÈÜˆ\˜Ù[YÙHÚ[™ÙJK‰ÊKˆ™\ÚÛÛY]šX×Ý˜[YHŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•\Ù\[\Ô\X[\]T™\ÜÛœÙU™\ÚÛY]šXÕ˜[YSX^
K›Ü[Û˜[

K™\ØÜšX™J	Ñ›ÜˆÚÚXÙH[™\Ü×Ù˜Z[]˜[ËHÜXÚYšXÈY]šXÈ˜[YHÈ[Ûš]Ü‹‰ÊKˆ˜Üš]XØ[Ý™\ÚÛÝ˜[YHŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\•\Ù\[\Ô\X[\]T™\ÜÛœÙPÜš]XØ[™\ÚÛ˜[YSZ[ŠK›Ü[Û˜[

KˆØ\›š[™×Ý™\ÚÛÝ˜[YHŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\•\Ù\[\Ô\X[\]T™\ÜÛœÙUØ\›š[™Õ™\ÚÛ˜[YSZ[ŠK›Ü[Û˜[

Kˆ˜[\Ùœ™\]Y[˜ÞHŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\•\Ù\[\Ô\X[\]T™\ÜÛœÙP[\œ™\]Y[˜ÞSZ[ŠK›X^
˜XÙ\•\Ù\[\Ô\X[\]T™\ÜÛœÙP[\œ™\]Y[˜ÞSX^
K›Ü[Û˜[

K™\ØÜšX™J	Ñœ™\]Y[˜ÞHÙˆ[\ÚXÚÜÈ[ˆZ[]\Ë‰ÊKˆ˜]]×Ý™\ÚÛÝ[YWÝÚ[™ÝÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\•\Ù\[\Ô\X[\]T™\ÜÛœÙP]]Õ™\ÚÛ[YUÚ[™ÝÓZ[ŠK›X^
˜XÙ\•\Ù\[\Ô\X[\]T™\ÜÛœÙP]]Õ™\ÚÛ[YUÚ[™ÝÓX^
K›Ü[Û˜[

K™\ØÜšX™J	Ñ›Üˆ]]Ë]™\ÚÛ[™ËˆH[YHÚ[™ÝÈ[ˆZ[]\ÈÈØ[Ý[]HH\ÝÜšXØ[YX[‰ÊKˆ›\ÝØÚXÚÙYØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

K™\ØÜšX™J	ÕH\Ý[YHH[Ûš]ÜˆØ\ÈÚXÚÙY›Üˆ[\Ë‰ÊKˆ››ÝYšXØ][Û—Ù[XZ[ÈŽˆ›Ù˜\œ˜^J›ÙœÝš[™Ê
K™[XZ[

K›Z[ŠJK›X^
˜XÙ\•\Ù\[\Ô\X[\]T™\ÜÛœÙS›ÝYšXØ][Û‘[XZ[Ò][SX^
JK›Ü[Û˜[

KˆœÛXÚ×ÝÙXšÛÚ×Ý\›Žˆ›ÙœÝš[™Ê
K\›

K›X^
˜XÙ\•\Ù\[\Ô\X[\]T™\ÜÛœÙTÛXÚÕÙXšÛÚÕ\›X^
K›Ü[Û˜[

KˆœÛXÚ×Û›Ý\ÈŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆš\×Û]]HŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ™š[\œÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›ÙÜÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

JK›Ü[Û˜[

Kˆ›Ü™Ø[š^˜][ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

KˆÛÜšÜÜXÙHŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜Ü™X]YØžHŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJB‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ñ[]T\˜[\ÈH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
BŸJB‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ó[Ûš]Ü‘]Z[Ô\˜[\ÈH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
BŸJB‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ó[Ûš]Ü‘]Z[Ô™\ÜÛœÙSY]šXÓX^HMMŽÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ó[Ûš]Ü‘]Z[Ô™\ÜÛœÙU™\ÚÛY]šXÕ˜[YSX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ó[Ûš]Ü‘]Z[Ô™\ÜÛœÙPÜš]XØ[™\ÚÛ˜[YSZ[ˆHÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ó[Ûš]Ü‘]Z[Ô™\ÜÛœÙUØ\›š[™Õ™\ÚÛ˜[YSZ[ˆHÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ó[Ûš]Ü‘]Z[Ô™\ÜÛœÙP[\œ™\]Y[˜ÞSZ[ˆHNÂ™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ó[Ûš]Ü‘]Z[Ô™\ÜÛœÙP[\œ™\]Y[˜ÞSX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ó[Ûš]Ü‘]Z[Ô™\ÜÛœÙP]]Õ™\ÚÛ[YUÚ[™ÝÓZ[ˆHÂ™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ó[Ûš]Ü‘]Z[Ô™\ÜÛœÙP]]Õ™\ÚÛ[YUÚ[™ÝÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ó[Ûš]Ü‘]Z[Ô™\ÜÛœÙS›ÝYšXØ][Û‘[XZ[Ò][SX^HMÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ó[Ûš]Ü‘]Z[Ô™\ÜÛœÙTÛXÚÕÙXšÛÚÕ\›X^HŒÂ‚‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\Ó[Ûš]Ü‘]Z[Ô™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ›Y]šX×Û˜[YHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ\]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™[]YŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ™[]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ›Y]šX×Ý\HŽˆ›Ù™[[JÉØÛÝ[ÛÙ—Ù\œ›ÜœÉË	Ù\œ›Ü—Ü˜]\×Ù›Ü—Ù[˜Ý[Û—ØØ[[™ÉË	Ù\œ›Ü—Ùœ™YWÜÙ\ÜÚ[Û—Ü˜]\ÉË	ÜÙ\šXÙWÜ›ÝšY\—Ù\œ›Ü—Ü˜]\ÉË	ÛWØ\WÙ˜Z[\™WÜ˜]\ÉË	ÜÜ[—Ü™\ÜÛœÙWÝ[YIË	ÛWÜ™\ÜÛœÙWÝ[YIË	ÝÚÙ[—Ý\ØYÙIË	ÙZ[WÝÚÙ[œ×ÜÜ[	Ë	Û[ÛWÝÚÙ[œ×ÜÜ[	Ë	Ù]˜[X][Û—ÛY]šXÜÉ×JKˆ›Y]šXÈŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•\Ù\[\Ó[Ûš]Ü‘]Z[Ô™\ÜÛœÙSY]šXÓX^
K›Ü[Û˜[

K™\ØÜšX™J	ÒYÙˆH]˜[X][Ûˆ[\]K‰ÊKˆ™\ÚÛÛÜ\˜]ÜˆŽˆ›Ù™[[JÉÙÜ™X]\—Ý[‰Ë	Û\Ü×Ý[‰×JKˆ™\ÚÛÝ\HŽˆ›Ù™[[JÉÜÝ]XÉË	Ü\˜Ù[YÙWØÚ[™ÙI×JK›Ü[Û˜[

K™\ØÜšX™J	ÓY]ÙÈÙ]H™\ÚÛ›ÜˆH[Ûš]Üˆ
Ý]XÈÜˆ\˜Ù[YÙHÚ[™ÙJK‰ÊKˆ™\ÚÛÛY]šX×Ý˜[YHŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•\Ù\[\Ó[Ûš]Ü‘]Z[Ô™\ÜÛœÙU™\ÚÛY]šXÕ˜[YSX^
K›Ü[Û˜[

K™\ØÜšX™J	Ñ›ÜˆÚÚXÙH[™\Ü×Ù˜Z[]˜[ËHÜXÚYšXÈY]šXÈ˜[YHÈ[Ûš]Ü‹‰ÊKˆ˜Üš]XØ[Ý™\ÚÛÝ˜[YHŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\•\Ù\[\Ó[Ûš]Ü‘]Z[Ô™\ÜÛœÙPÜš]XØ[™\ÚÛ˜[YSZ[ŠK›Ü[Û˜[

KˆØ\›š[™×Ý™\ÚÛÝ˜[YHŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\•\Ù\[\Ó[Ûš]Ü‘]Z[Ô™\ÜÛœÙUØ\›š[™Õ™\ÚÛ˜[YSZ[ŠK›Ü[Û˜[

Kˆ˜[\Ùœ™\]Y[˜ÞHŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\•\Ù\[\Ó[Ûš]Ü‘]Z[Ô™\ÜÛœÙP[\œ™\]Y[˜ÞSZ[ŠK›X^
˜XÙ\•\Ù\[\Ó[Ûš]Ü‘]Z[Ô™\ÜÛœÙP[\œ™\]Y[˜ÞSX^
K›Ü[Û˜[

K™\ØÜšX™J	Ñœ™\]Y[˜ÞHÙˆ[\ÚXÚÜÈ[ˆZ[]\Ë‰ÊKˆ˜]]×Ý™\ÚÛÝ[YWÝÚ[™ÝÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\•\Ù\[\Ó[Ûš]Ü‘]Z[Ô™\ÜÛœÙP]]Õ™\ÚÛ[YUÚ[™ÝÓZ[ŠK›X^
˜XÙ\•\Ù\[\Ó[Ûš]Ü‘]Z[Ô™\ÜÛœÙP]]Õ™\ÚÛ[YUÚ[™ÝÓX^
K›Ü[Û˜[

K™\ØÜšX™J	Ñ›Üˆ]]Ë]™\ÚÛ[™ËˆH[YHÚ[™ÝÈ[ˆZ[]\ÈÈØ[Ý[]HH\ÝÜšXØ[YX[‰ÊKˆ›\ÝØÚXÚÙYØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

K™\ØÜšX™J	ÕH\Ý[YHH[Ûš]ÜˆØ\ÈÚXÚÙY›Üˆ[\Ë‰ÊKˆ››ÝYšXØ][Û—Ù[XZ[ÈŽˆ›Ù˜\œ˜^J›ÙœÝš[™Ê
K™[XZ[

K›Z[ŠJK›X^
˜XÙ\•\Ù\[\Ó[Ûš]Ü‘]Z[Ô™\ÜÛœÙS›ÝYšXØ][Û‘[XZ[Ò][SX^
JK›Ü[Û˜[

KˆœÛXÚ×ÝÙXšÛÚ×Ý\›Žˆ›ÙœÝš[™Ê
K\›

K›X^
˜XÙ\•\Ù\[\Ó[Ûš]Ü‘]Z[Ô™\ÜÛœÙTÛXÚÕÙXšÛÚÕ\›X^
K›Ü[Û˜[

KˆœÛXÚ×Û›Ý\ÈŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆš\×Û]]HŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ™š[\œÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›ÙÜÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

JK›Ü[Û˜[

Kˆ›Ü™Ø[š^˜][ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

KˆÛÜšÜÜXÙHŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜Ü™X]YØžHŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJB‚‚‹ÊŠ‚ˆ
ˆXØÙ\ÈÝ\Ù]X[™[™Ù]X]Y\žH\˜[Y]\œÈ
TÓÈŒH›Ü›X]
K‚’Yˆ›Ý›ÝšYY]Y˜][ÈÈH\ÝÈ^\Ë‚ˆ
ˆÝ[[X\žH™]\›œÈ[YK\Ù\šY\È]H›ÜˆH[Ûš]Ü‰ÜÈY]šXËÝZ]X›H›ÜˆÜ˜\[™Ë‚ˆ
‹Â™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÑÜ˜\]T\˜[\ÈH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
BŸJB‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÑÜ˜\]T™\ÜÛœÙSY]šXÓX^HMMŽÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÑÜ˜\]T™\ÜÛœÙU™\ÚÛY]šXÕ˜[YSX^HMNÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÑÜ˜\]T™\ÜÛœÙPÜš]XØ[™\ÚÛ˜[YSZ[ˆHÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÑÜ˜\]T™\ÜÛœÙUØ\›š[™Õ™\ÚÛ˜[YSZ[ˆHÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÑÜ˜\]T™\ÜÛœÙP[\œ™\]Y[˜ÞSZ[ˆHNÂ™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÑÜ˜\]T™\ÜÛœÙP[\œ™\]Y[˜ÞSX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÑÜ˜\]T™\ÜÛœÙP]]Õ™\ÚÛ[YUÚ[™ÝÓZ[ˆHÂ™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÑÜ˜\]T™\ÜÛœÙP]]Õ™\ÚÛ[YUÚ[™ÝÓX^HŒMÍÍÎÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÑÜ˜\]T™\ÜÛœÙS›ÝYšXØ][Û‘[XZ[Ò][SX^HMÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÑÜ˜\]T™\ÜÛœÙTÛXÚÕÙXšÛÚÕ\›X^HŒÂ‚‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\[\ÑÜ˜\]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ›Ú™XÝŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ›Y]šX×Û˜[YHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ\]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ™[]YŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ™[]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ›Y]šX×Ý\HŽˆ›Ù™[[JÉØÛÝ[ÛÙ—Ù\œ›ÜœÉË	Ù\œ›Ü—Ü˜]\×Ù›Ü—Ù[˜Ý[Û—ØØ[[™ÉË	Ù\œ›Ü—Ùœ™YWÜÙ\ÜÚ[Û—Ü˜]\ÉË	ÜÙ\šXÙWÜ›ÝšY\—Ù\œ›Ü—Ü˜]\ÉË	ÛWØ\WÙ˜Z[\™WÜ˜]\ÉË	ÜÜ[—Ü™\ÜÛœÙWÝ[YIË	ÛWÜ™\ÜÛœÙWÝ[YIË	ÝÚÙ[—Ý\ØYÙIË	ÙZ[WÝÚÙ[œ×ÜÜ[	Ë	Û[ÛWÝÚÙ[œ×ÜÜ[	Ë	Ù]˜[X][Û—ÛY]šXÜÉ×JKˆ›Y]šXÈŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•\Ù\[\ÑÜ˜\]T™\ÜÛœÙSY]šXÓX^
K›Ü[Û˜[

K™\ØÜšX™J	ÒYÙˆH]˜[X][Ûˆ[\]K‰ÊKˆ™\ÚÛÛÜ\˜]ÜˆŽˆ›Ù™[[JÉÙÜ™X]\—Ý[‰Ë	Û\Ü×Ý[‰×JKˆ™\ÚÛÝ\HŽˆ›Ù™[[JÉÜÝ]XÉË	Ü\˜Ù[YÙWØÚ[™ÙI×JK›Ü[Û˜[

K™\ØÜšX™J	ÓY]ÙÈÙ]H™\ÚÛ›ÜˆH[Ûš]Üˆ
Ý]XÈÜˆ\˜Ù[YÙHÚ[™ÙJK‰ÊKˆ™\ÚÛÛY]šX×Ý˜[YHŽˆ›ÙœÝš[™Ê
K›X^
˜XÙ\•\Ù\[\ÑÜ˜\]T™\ÜÛœÙU™\ÚÛY]šXÕ˜[YSX^
K›Ü[Û˜[

K™\ØÜšX™J	Ñ›ÜˆÚÚXÙH[™\Ü×Ù˜Z[]˜[ËHÜXÚYšXÈY]šXÈ˜[YHÈ[Ûš]Ü‹‰ÊKˆ˜Üš]XØ[Ý™\ÚÛÝ˜[YHŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\•\Ù\[\ÑÜ˜\]T™\ÜÛœÙPÜš]XØ[™\ÚÛ˜[YSZ[ŠK›Ü[Û˜[

KˆØ\›š[™×Ý™\ÚÛÝ˜[YHŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\•\Ù\[\ÑÜ˜\]T™\ÜÛœÙUØ\›š[™Õ™\ÚÛ˜[YSZ[ŠK›Ü[Û˜[

Kˆ˜[\Ùœ™\]Y[˜ÞHŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\•\Ù\[\ÑÜ˜\]T™\ÜÛœÙP[\œ™\]Y[˜ÞSZ[ŠK›X^
˜XÙ\•\Ù\[\ÑÜ˜\]T™\ÜÛœÙP[\œ™\]Y[˜ÞSX^
K›Ü[Û˜[

K™\ØÜšX™J	Ñœ™\]Y[˜ÞHÙˆ[\ÚXÚÜÈ[ˆZ[]\Ë‰ÊKˆ˜]]×Ý™\ÚÛÝ[YWÝÚ[™ÝÈŽˆ›Ù›[X™\Š
K›Z[Š˜XÙ\•\Ù\[\ÑÜ˜\]T™\ÜÛœÙP]]Õ™\ÚÛ[YUÚ[™ÝÓZ[ŠK›X^
˜XÙ\•\Ù\[\ÑÜ˜\]T™\ÜÛœÙP]]Õ™\ÚÛ[YUÚ[™ÝÓX^
K›Ü[Û˜[

K™\ØÜšX™J	Ñ›Üˆ]]Ë]™\ÚÛ[™ËˆH[YHÚ[™ÝÈ[ˆZ[]\ÈÈØ[Ý[]HH\ÝÜšXØ[YX[‰ÊKˆ›\ÝØÚXÚÙYØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

K™\ØÜšX™J	ÕH\Ý[YHH[Ûš]ÜˆØ\ÈÚXÚÙY›Üˆ[\Ë‰ÊKˆ››ÝYšXØ][Û—Ù[XZ[ÈŽˆ›Ù˜\œ˜^J›ÙœÝš[™Ê
K™[XZ[

K›Z[ŠJK›X^
˜XÙ\•\Ù\[\ÑÜ˜\]T™\ÜÛœÙS›ÝYšXØ][Û‘[XZ[Ò][SX^
JK›Ü[Û˜[

KˆœÛXÚ×ÝÙXšÛÚ×Ý\›Žˆ›ÙœÝš[™Ê
K\›

K›X^
˜XÙ\•\Ù\[\ÑÜ˜\]T™\ÜÛœÙTÛXÚÕÙXšÛÚÕ\›X^
K›Ü[Û˜[

KˆœÛXÚ×Û›Ý\ÈŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆš\×Û]]HŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ™š[\œÈŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ›ÙÜÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

JK›Ü[Û˜[

Kˆ›Ü™Ø[š^˜][ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

KˆÛÜšÜÜXÙHŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜Ü™X]YØžHŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJB‚‚‹ÊŠ‚ˆ
ˆ\Ý˜XÙ\Èš[\™YžH›Ú™XÝQÚ]Ü[Z^™Y]Y\šY\Ë‚ˆ
‹Â™^ÜÛÛœÝ˜XÙ\•\Ù\œÓ\Ý]Y\žTYÙTÚ^™SX^HLÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\œÓ\Ý]Y\žPÝ\œ™[YÙR[™^Z[ˆHÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\œÓ\Ý]Y\žTÛÜ\˜[\ÑY˜][H×XÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\œÓ\Ý]Y\žQš[\œÑY˜][H×XÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\œÓ\Ý]Y\žQ^ÜY˜][H˜[ÙNÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\œÓ\Ý]Y\žT\˜[\ÈH›Ù›Øš™XÝ
Âˆœ›Ú™XÝÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

KˆœÙX\˜ÚŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆœYÙWÜÚ^™HŽˆ›Ù›[X™\Š
K›Z[ŠJK›X^
˜XÙ\•\Ù\œÓ\Ý]Y\žTYÙTÚ^™SX^
K›Ü[Û˜[

Kˆ˜Ý\œ™[ÜYÙWÚ[™^Žˆ›Ù›[X™\Š
K›Z[Š˜XÙ\•\Ù\œÓ\Ý]Y\žPÝ\œ™[YÙR[™^Z[ŠK›Ü[Û˜[

KˆœÛÜÜ\˜[\ÈŽˆ›ÙœÝš[™Ê
K›Z[ŠJK™Y˜][
˜XÙ\•\Ù\œÓ\Ý]Y\žTÛÜ\˜[\ÑY˜][
Kˆ™š[\œÈŽˆ›ÙœÝš[™Ê
K›Z[ŠJK™Y˜][
˜XÙ\•\Ù\œÓ\Ý]Y\žQš[\œÑY˜][
Kˆ™^ÜŽˆ›Ù˜›ÛÛX[Š
K™Y˜][
˜XÙ\•\Ù\œÓ\Ý]Y\žQ^ÜY˜][
BŸJB‚™^ÜÛÛœÝ˜XÙ\•\Ù\œÓ\Ý™\ÜÛœÙTÝ]\ÑY˜][HYNÂ‚™^ÜÛÛœÝ˜XÙ\•\Ù\œÓ\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
K™Y˜][
˜XÙ\•\Ù\œÓ\Ý™\ÜÛœÙTÝ]\ÑY˜][
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
ÂˆX›HŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

JKˆÝ[ØÛÝ[Žˆ›Ù›[X™\Š
KˆÝ[ÜYÙ\ÈŽˆ›Ù›[X™\Š
BŸJBŸJB‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\œÑÙ]ÛÙQ^[\S\Ý™\ÜÛœÙTÝ]\ÑY˜][HYNÂ‚‚™^ÜÛÛœÝ˜XÙ\•\Ù\œÑÙ]ÛÙQ^[\S\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
K™Y˜][
˜XÙ\•\Ù\œÑÙ]ÛÙQ^[\S\Ý™\ÜÛœÙTÝ]\ÑY˜][
Kˆœ™\Ý[Žˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJB‚‚‹ÊŠ‚ˆ
ˆX[ÚXÚÈH[Ø^\È™]\›œÈÒË‚ˆ
‹Â‚‚‚™^ÜÛÛœÝ˜XÙ\•ŒRX[\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù™[[JÉÚX[I×JKˆœÙ\šXÙHŽˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJB‚‚™^ÜÛÛœÝ˜XÙ\•ÙXšÛÚÐÜ™X]P›ÙHH›Ù›Øš™XÝ
Âˆ˜Ø[Žˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

BŸJB‚™^ÜÛÛœÝ˜XÙ\•ÙXšÛÚÐÜ™X]T™\ÜÛœÙTÝ]\ÑY˜][HYNÂ‚‚™^ÜÛÛœÝ˜XÙ\•ÙXšÛÚÐÜ™X]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
K™Y˜][
˜XÙ\•ÙXšÛÚÐÜ™X]T™\ÜÛœÙTÝ]\ÑY˜][
Kˆœ™\Ý[Žˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJB‚‚‹ÊŠ‚ˆ
ˆÙ]Ý\ÝÛH[ˆÝ[[X\žH›Üˆ[ˆÜ™Ë‚ˆ
‹Â‚‚‚™^ÜÛÛœÝ\ØYÙPYZ[Ý\ÝÛT[“\Ý]Y\žT\˜[\ÈH›Ù›Øš™XÝ
Âˆ›Ü™Ø[š^˜][Û—ÚYŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ™[Y[œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

BŸJB‚™^ÜÛÛœÝ\ØYÙPYZ[Ý\ÝÛT[“\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ùœ™XÛÜ™
›ÙœÝš[™Ê
K›ÙœÝš[™Ê
JBŸJB‚‚‹ÊŠ‚ˆ
ˆ™\]Y\Ý›ÙN‚žÂˆ›Ü™Ø[š^˜][Û—ÚYŽˆ]ZY‹ˆœ]›Ü›WÙ™YHŽˆMLŒˆ™[][Y[ÈŽˆÈ›[Ûš]ÜœÈŽˆLKš\×ÚÛ›ÝÛYÙWØ˜\ÙHŽˆYK‹‹ŸKˆœšXÚ[™ÈŽˆÂˆ˜ZWØÜ™Y]ÈŽˆÂˆÈY\—ÜÝ\ŽˆY\—Ù[™ŽˆLœšXÙWÜ\—Ý[š]ŽˆŒ_KˆÈY\—ÜÝ\ŽˆLY\—Ù[™Žˆ[œšXÙWÜ\—Ý[š]ŽˆŒßBˆK‹‹‚ˆBŸBˆ
ˆÝ[[X\žHÜ™X]H[Ý\ÝÛH[‹‚ˆ
‹Â‚‚‚™^ÜÛÛœÝ\ØYÙPYZ[Ý\ÝÛT[Ü™X]P›ÙHH›Ù›Øš™XÝ
Âˆ›Ü™Ø[š^˜][Û—ÚYŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ]›Ü›WÙ™YHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆœ]›Ü›WÙ™YWØš[[™×ØÞXÛHŽˆ›Ù›[X™\Š
K›Z[ŠJK›Ü[Û˜[

Kˆ˜ÛÛ˜XÝÙ[™Ù]HŽˆ›ÙœÝš[™Ê
K™]J
K›Ü[Û˜[

KˆœÝ\Ù]HŽˆ›ÙœÝš[™Ê
K™]J
K›Ü[Û˜[

Kˆ™[][Y[ÈŽˆ›Ùœ™XÛÜ™
›ÙœÝš[™Ê
K›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

JK›Ü[Û˜[

KˆœšXÚ[™ÈŽˆ›Ùœ™XÛÜ™
›ÙœÝš[™Ê
K›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆY\—ÜÝ\Žˆ›ÙœÝš[™Ê
KˆY\—Ù[™Žˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆœšXÙWÜ\—Ý[š]Žˆ›ÙœÝš[™Ê
Kˆ™\Ü^WÝ[š]Žˆ›ÙœÝš[™Ê
K›Ü[Û˜[

BŸJJJK›Ü[Û˜[

Kˆ˜Ü™X]WÜÝš\WÜÝXœØÜš\[ÛˆŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

BŸJB‚™^ÜÛÛœÝ\ØYÙPYZ[Ý\ÝÛT[Ü™X]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ùœ™XÛÜ™
›ÙœÝš[™Ê
K›ÙœÝš[™Ê
JBŸJB‚‚‹ÊŠ‚ˆ
ˆ\]H^\Ý[™ÈÝ\ÝÛH[ˆ
[][Y[ËÜšXÚ[™ËÙ™YJK‚ˆ
‹Â‚‚‚™^ÜÛÛœÝ\ØYÙPYZ[Ý\ÝÛT[•\]P›ÙHH›Ù›Øš™XÝ
Âˆ›Ü™Ø[š^˜][Û—ÚYŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ]›Ü›WÙ™YHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆœ]›Ü›WÙ™YWØš[[™×ØÞXÛHŽˆ›Ù›[X™\Š
K›Z[ŠJK›Ü[Û˜[

Kˆ˜ÛÛ˜XÝÙ[™Ù]HŽˆ›ÙœÝš[™Ê
K™]J
K›Ü[Û˜[

KˆœÝ\Ù]HŽˆ›ÙœÝš[™Ê
K™]J
K›Ü[Û˜[

Kˆ™[][Y[ÈŽˆ›Ùœ™XÛÜ™
›ÙœÝš[™Ê
K›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

JK›Ü[Û˜[

KˆœšXÚ[™ÈŽˆ›Ùœ™XÛÜ™
›ÙœÝš[™Ê
K›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆY\—ÜÝ\Žˆ›ÙœÝš[™Ê
KˆY\—Ù[™Žˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆœšXÙWÜ\—Ý[š]Žˆ›ÙœÝš[™Ê
Kˆ™\Ü^WÝ[š]Žˆ›ÙœÝš[™Ê
K›Ü[Û˜[

BŸJJJK›Ü[Û˜[

Kˆ˜Ü™X]WÜÝš\WÜÝXœØÜš\[ÛˆŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

BŸJB‚™^ÜÛÛœÝ\ØYÙPYZ[Ý\ÝÛT[•\]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ùœ™XÛÜ™
›ÙœÝš[™Ê
K›ÙœÝš[™Ê
JBŸJB‚‚‹ÊŠ‚ˆ
ˆ\Ý[][Y[Ý™\œšY\È›Üˆ[ˆÜ™Ë‚ˆ
‹Â‚‚‚™^ÜÛÛœÝ\ØYÙPYZ[‘[][Y[Ó\Ý]Y\žT\˜[\ÈH›Ù›Øš™XÝ
Âˆ›Ü™Ø[š^˜][Û—ÚYŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ™™X]\™HŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

BŸJB‚™^ÜÛÛœÝ\ØYÙPYZ[‘[][Y[Ó\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ùœ™XÛÜ™
›ÙœÝš[™Ê
K›ÙœÝš[™Ê
JBŸJB‚‚‹ÊŠ‚ˆ
ˆÜ™X]HÜˆ\]H[ˆ[][Y[Ý™\œšYK‚ˆ
‹Â‚‚‚™^ÜÛÛœÝ\ØYÙPYZ[‘[][Y[ÐÜ™X]P›ÙHH›Ù›Øš™XÝ
Âˆ›Ü™Ø[š^˜][Û—ÚYŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ™™X]\™HŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ˜[YWÚ[Žˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ˜[YWØ›ÛÛŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

BŸJB‚‚‚‚™^ÜÛÛœÝ\ØYÙPYZ[‘[][Y[ÐÜ™X]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›Ù›[X™\Š
Kˆ™™X]\™HŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ˜Ü™X]YŽˆ›Ù˜›ÛÛX[Š
Kˆ˜[YWÚ[Žˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ˜[YWØ›ÛÛŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

BŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆ™[[Ý™H[ˆ[][Y[Ý™\œšYH
˜[È˜XÚÈÈ[ˆY˜][
K‚ˆ
‹Â‚‚‚™^ÜÛÛœÝ\ØYÙPYZ[‘[][Y[Ñ[]T]Y\žT\˜[\ÈH›Ù›Øš™XÝ
Âˆ›Ü™Ø[š^˜][Û—ÚYŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ™™X]\™HŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

BŸJB‚™^ÜÛÛœÝ\ØYÙPYZ[‘[][Y[Ñ[]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ùœ™XÛÜ™
›ÙœÝš[™Ê
K›ÙœÝš[™Ê
JBŸJB‚‚‹ÊŠ‚ˆ
ˆÙ[™\˜]H[›ÚXÙH›Üˆ[ˆÜ™ÊÜ\š[Ù‚ˆ
‹Â‚‚™^ÜÛÛœÝ\ØYÙPYZ[’[›ÚXÙQÙ[™\˜]PÜ™X]P›ÙT\š[Ù™YÑ^H™]È™YÑ^
	×—ÍKWÌŸI	ÊNÂ‚‚™^ÜÛÛœÝ\ØYÙPYZ[’[›ÚXÙQÙ[™\˜]PÜ™X]P›ÙHH›Ù›Øš™XÝ
Âˆ›Ü™Ø[š^˜][Û—ÚYŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ\š[ÙŽˆ›ÙœÝš[™Ê
K›Z[ŠJKœ™YÙ^
\ØYÙPYZ[’[›ÚXÙQÙ[™\˜]PÜ™X]P›ÙT\š[Ù™YÑ^
BŸJB‚‚‚‚‚™^ÜÛÛœÝ\ØYÙPYZ[’[›ÚXÙQÙ[™\˜]PÜ™X]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆ˜Ü™X]YŽˆ›Ù›[X™\Š
KˆœÚÚ\YŽˆ›Ù›[X™\Š
Kˆ™\œ›ÜœÈŽˆ›Ù›[X™\Š
Kˆš[›ÚXÙWÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

KˆÝ[Žˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

KˆœÝ]\ÈŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆœ\š[ÙÜÝ\Žˆ›ÙœÝš[™Ê
K™]J
K›Ü[Û˜[

Kˆœ\š[ÙÙ[™Žˆ›ÙœÝš[™Ê
K™]J
K›Ü[Û˜[

Kˆ›[™WÚ][\×ØÛÝ[Žˆ›Ù›[X™\Š
K›Ü[Û˜[

BŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆ™]šY]È[›ÚXÙH›Üˆ[ˆÜ™ÊÜ\š[Ù
›ÈÚYHY™™XÝÊK‚ˆ
‹Â‚‚™^ÜÛÛœÝ\ØYÙPYZ[’[›ÚXÙT™]šY]ÐÜ™X]P›ÙT\š[Ù™YÑ^H™]È™YÑ^
	×—ÍKWÌŸI	ÊNÂ‚‚™^ÜÛÛœÝ\ØYÙPYZ[’[›ÚXÙT™]šY]ÐÜ™X]P›ÙHH›Ù›Øš™XÝ
Âˆ›Ü™Ø[š^˜][Û—ÚYŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ\š[ÙŽˆ›ÙœÝš[™Ê
K›Z[ŠJKœ™YÙ^
\ØYÙPYZ[’[›ÚXÙT™]šY]ÐÜ™X]P›ÙT\š[Ù™YÑ^
BŸJB‚‚‚‚‚‚‚‚‚‚‚‚‚‚‚‚™^ÜÛÛœÝ\ØYÙPYZ[’[›ÚXÙT™]šY]ÐÜ™X]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆ›Ü™×ÚYŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ\š[ÙŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœ[ˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ˜˜XÚÙš[Ü˜[ˆŽˆ›Ù˜›ÛÛX[Š
Kˆ\ØYÙWÜÝ[[X\žWØÛÝ[Žˆ›Ù›[X™\Š
Kˆš[›ÚXÙWÙ^\ÝÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ]›Ü›WÙ™YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ\ØYÙWÝÝ[Žˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ˜Ü™Y]×Ø\YYŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœÝXÝ[Žˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ^Žˆ›ÙœÝš[™Ê
K›Z[ŠJKˆÝ[Žˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ›[™WÚ][\ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
Âˆ›[™WÝ\HŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ™[Y[œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ™\ØÜš\[ÛˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœ]X[]HŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ[š]Žˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ[š]ÜšXÙHŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ˜[[Ý[Žˆ›ÙœÝš[™Ê
K›Z[ŠJKˆY\—Øœ™XZÙÝÛˆŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

BŸJJBŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆ\ÝÝ\ÝÛHšXÚ[™ÈY\œÈ›Üˆ[ˆÜ™Ë‚ˆ
‹Â‚‚‚™^ÜÛÛœÝ\ØYÙPYZ[”šXÚ[™Ó\Ý]Y\žT\˜[\ÈH›Ù›Øš™XÝ
Âˆ›Ü™Ø[š^˜][Û—ÚYŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ™[Y[œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

BŸJB‚‚‚‚™^ÜÛÛœÝ\ØYÙPYZ[”šXÚ[™Ó\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
ÂˆœšXÚ[™ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ™[Y[œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆY\—ÜÝ\Žˆ›ÙœÝš[™Ê
KˆY\—Ù[™Žˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆœšXÙWÜ\—Ý[š]Žˆ›ÙœÝš[™Ê
Kˆ™\Ü^WÝ[š]Žˆ›ÙœÝš[™Ê
K›Ü[Û˜[

BŸJJBŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆÜ™X]HÜˆ\]HHšXÚ[™ÈY\‹‚ˆ
‹Â‚‚‚™^ÜÛÛœÝ\ØYÙPYZ[”šXÚ[™ÐÜ™X]P›ÙHH›Ù›Øš™XÝ
Âˆ›Ü™Ø[š^˜][Û—ÚYŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ™[Y[œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆY\—ÜÝ\Žˆ›ÙœÝš[™Ê
KˆY\—Ù[™Žˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆœšXÙWÜ\—Ý[š]Žˆ›ÙœÝš[™Ê
Kˆ™\Ü^WÝ[š]Žˆ›ÙœÝš[™Ê
K›Ü[Û˜[

BŸJB‚‚‚‚™^ÜÛÛœÝ\ØYÙPYZ[”šXÚ[™ÐÜ™X]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›Ù›[X™\Š
Kˆ™[Y[œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ˜Ü™X]YŽˆ›Ù˜›ÛÛX[Š
BŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆ™[[Ý™H[Ý\ÝÛHšXÚ[™ÈY\œÈ›ÜˆH[Y[œÚ[Û‹‚ˆ
‹Â‚‚‚™^ÜÛÛœÝ\ØYÙPYZ[”šXÚ[™Ñ[]T]Y\žT\˜[\ÈH›Ù›Øš™XÝ
Âˆ›Ü™Ø[š^˜][Û—ÚYŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ™[Y[œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

BŸJB‚™^ÜÛÛœÝ\ØYÙPYZ[”šXÚ[™Ñ[]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ùœ™XÛÜ™
›ÙœÝš[™Ê
K›ÙœÝš[™Ê
JBŸJB‚‚™^ÜÛÛœÝ\ØYÙP\PØ[ÛÝ[\Ý]Y\žS[ÛX^HLŽÂ‚‚‚‚™^ÜÛÛœÝ\ØYÙP\PØ[ÛÝ[\Ý]Y\žT\˜[\ÈH›Ù›Øš™XÝ
ÂˆžYX\ˆŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ›[ÛŽˆ›Ù›[X™\Š
K›Z[ŠJK›X^
\ØYÙP\PØ[ÛÝ[\Ý]Y\žS[ÛX^
K›Ü[Û˜[

Kˆ˜\WØØ[Ý\HŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

BŸJB‚™^ÜÛÛœÝ\ØYÙP\PØ[ÛÝ[\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆ™]HŽˆ›Ùœ™XÛÜ™
›ÙœÝš[™Ê
K›Ù›[X™\Š
JBŸJBŸJB‚‚™^ÜÛÛœÝ\ØYÙP\PØ[\S\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ›˜[YHŽˆ›Ù™[[JÉÜ›Û\Ø™[˜Ú	Ë	Ù]\Ù]Ü›ÝXÝ	Ë	Ù]\Ù]Ü›ÝXÝÙ›\Ú	Ë	Ý\š[™×Û\™ÙWÙ]˜[X]Ü‰Ë	Ý\š[™×ÜÛX[Ù]˜[X]Ü‰Ë	Ý\š[™×Ù›\ÚÙ]˜[X]Ü‰Ë	Ü›ÝXÝÙ]˜[X]Ü‰Ë	Ü›ÝXÝÙ›\ÚÙ]˜[X]Ü‰Ë	ØÛÙWÙ]˜[X]Ü‰Ë	Ý\Ù\—ØY	Ë	ÛØœÙ\™WØY	Ë	Ü›ÝÝ\WØY	Ë	Ù]\Ù]ØY	Ë	Ü›Ý×ØY	Ë	ÚÛ›ÝÛYÙWØ˜\ÙIË	ÜÞ[]X×Ù]WÙÙ[™\˜][Û‰Ë	Ù\œ›Ü—ÛØØ[^™\‰Ë	Ø]]×Ø[››Ý][Û‰Ë	Ù]\Ù]Ù]˜[X][Û‰Ë	Ù^\š[Y[Ù]˜[X][Û‰Ë	ÛÜ[Z\Ø][Û—Ù]˜[X][Û‰Ë	Ù]˜[Ù^[˜][Û‰Ë	Ù]\Ù]Ü[—Ü›Û\	Ë	Ù]\Ù]ÛÜ[Z^˜][Û‰Ë	Ù]\Ù]Ù^\š[Y[	Ë	Ý›ÚXÙWØØ[	Ë	Ý^ØØ[	Ë	ÝØ[]Ü™Y[™	Ë	ÝØ[]Ü™Yš[	Ë	ÝØ[]Ø]]×Ü™XÚ\™ÙIË	ÝØ[]ØYÙ[™ÉË	Ý˜XÙWÙ\œ›Ü—Ø[˜[\Ú\É×JKˆ™\ØÜš\[ÛˆŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

BŸJJBŸJB‚‚™^ÜÛÛœÝ\ØYÙPØ[˜Ù[ÝXœØÜš\[ÛÜ™X]P›ÙHH›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

B‚‚‚‚™^ÜÛÛœÝ\ØYÙPØ[˜Ù[ÝXœØÜš\[ÛÜ™X]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆ›Y\ÜØYÙHŽˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJBŸJB‚‚™^ÜÛÛœÝ\ØYÙPÜ™X]P]]Ô™XÚ\™ÙTÙ\ÜÚ[ÛÜ™X]P›ÙHH›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

B‚‚‚‚‚‚‚‚‚™^ÜÛÛœÝ\ØYÙPÜ™X]P]]Ô™XÚ\™ÙTÙ\ÜÚ[ÛÜ™X]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
ÂˆœÙ\ÜÚ[Û—ÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ\›Žˆ›ÙœÝš[™Ê
K\›

K›Z[ŠJK›Ü[Û˜[

KˆœÝ]\ÈŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ›Y\ÜØYÙHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

BŸJK›Ü[Û˜[

KˆœÙ\ÜÚ[Û—ÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ\›Žˆ›ÙœÝš[™Ê
K\›

K›Z[ŠJK›Ü[Û˜[

BŸJB‚‚™^ÜÛÛœÝ\ØYÙPÜ™X]Pš[[™ÔÜ[Ù\ÜÚ[ÛÜ™X]P›ÙHH›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

B‚‚‚‚™^ÜÛÛœÝ\ØYÙPÜ™X]Pš[[™ÔÜ[Ù\ÜÚ[ÛÜ™X]T™\ÜÛœÙHH›Ù›Øš™XÝ
Âˆ\›Žˆ›ÙœÝš[™Ê
K\›

K›Z[ŠJBŸJB‚‚‚‚‚™^ÜÛÛœÝ\ØYÙPÜ™X]PÚXÚÛÝ]Ù\ÜÚ[ÛÜ™X]P›ÙHH›Ù›Øš™XÝ
ÂˆœÝXœØÜš\[Û—Ý\HŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

BŸJB‚‚‚‚‚‚‚‚‚™^ÜÛÛœÝ\ØYÙPÜ™X]PÚXÚÛÝ]Ù\ÜÚ[ÛÜ™X]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
ÂˆœÙ\ÜÚ[Û—ÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ\›Žˆ›ÙœÝš[™Ê
K\›

K›Z[ŠJK›Ü[Û˜[

KˆœÝ]\ÈŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ›Y\ÜØYÙHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

BŸJK›Ü[Û˜[

KˆœÙ\ÜÚ[Û—ÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ\›Žˆ›ÙœÝš[™Ê
K\›

K›Z[ŠJK›Ü[Û˜[

BŸJB‚‚™^ÜÛÛœÝ\ØYÙPÜ™X]PÝ\ÝÛT^[Y[ÚXÚÛÝ]Ù\ÜÚ[ÛÜ™X]P›ÙHH›Ù›Øš™XÝ
Âˆ˜[[Ý[Žˆ›ÙœÝš[™Ê
BŸJB‚‚‚‚‚‚‚‚‚™^ÜÛÛœÝ\ØYÙPÜ™X]PÝ\ÝÛT^[Y[ÚXÚÛÝ]Ù\ÜÚ[ÛÜ™X]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
ÂˆœÙ\ÜÚ[Û—ÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ\›Žˆ›ÙœÝš[™Ê
K\›

K›Z[ŠJK›Ü[Û˜[

KˆœÝ]\ÈŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ›Y\ÜØYÙHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

BŸJK›Ü[Û˜[

KˆœÙ\ÜÚ[Û—ÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ\›Žˆ›ÙœÝš[™Ê
K\›

K›Z[ŠJK›Ü[Û˜[

BŸJB‚‚‚‚‚™^ÜÛÛœÝ\ØYÙQÝÛ›ØY[›ÚXÙPÜ™X]P›ÙHH›Ù›Øš™XÝ
Âˆš[›ÚXÙWÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJB‚‚‚‚™^ÜÛÛœÝ\ØYÙQÝÛ›ØY[›ÚXÙPÜ™X]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆš[›ÚXÙWÜ—Ý\›Žˆ›ÙœÝš[™Ê
K\›

K›Z[ŠJBŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆ\ÝÜˆÜ™X]HQHXÙ[œÙ\Ë‚ˆ
‹Â‚‚‚‚‚‚™^ÜÛÛœÝ\ØYÙQYSXÙ[œÙ\Ó\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆ›XÙ[œÙ\ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

Kˆ˜Ý\ÝÛY\—Û˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ˜˜[™Žˆ›Ù™[[JÉÝX[IË	Ø\Ú[™\ÜÉË	Ù[\œš\ÙIË	Ù[\œš\ÙWÜ\É×JKˆ˜š[[™×Ú[\˜[Žˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ™™X]\™\ÈŽˆ›Ù˜\œ˜^J›ÙœÝš[™Ê
K›Z[ŠJJKˆ›X^Ý˜XÙ\×Û[ÛHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ›X^ÙØ]]Ø^WÛ[ÛHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆš\ÜÝYYØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JKˆ™^\™\×Ø]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JKˆœÝ]\ÈŽˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJJBŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆ\ÝÜˆÜ™X]HQHXÙ[œÙ\Ë‚ˆ
‹Â™^ÜÛÛœÝ\ØYÙQYSXÙ[œÙ\ÐÜ™X]P›ÙHH›Ù›Øš™XÝ
Âˆ˜˜[™Žˆ›Ù™[[JÉÝX[IË	Ø\Ú[™\ÜÉË	Ù[\œš\ÙIË	Ù[\œš\ÙWÜ\É×JKˆ˜Ý\ÝÛY\—Û˜[YHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ˜š[[™×Ú[\˜[Žˆ›Ù™[[JÉÛ[ÛIË	ÞYX\›I×JK›Ü[Û˜[

BŸJB‚‚‚‚‚‚‚™^ÜÛÛœÝ\ØYÙQYSXÙ[œÙ\ÐÜ™X]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆ™Ü˜[ÚYŽˆ›ÙœÝš[™Ê
K]ZY

KˆšÝÚÙ^HŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆšÙ^WÚ\ÚŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ˜˜[™Žˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ™^\™\×Ø]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JKˆ™™X]\™\ÈŽˆ›Ù˜\œ˜^J›ÙœÝš[™Ê
K›Z[ŠJJBŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆ™]›ÚÙH[ˆQHXÙ[œÙK‚ˆ
‹Â™^ÜÛÛœÝ\ØYÙQYSXÙ[œÙ\Ô™]›ÚÙPÜ™X]T\˜[\ÈH›Ù›Øš™XÝ
Âˆ™Ü˜[ÚYŽˆ›ÙœÝš[™Ê
BŸJB‚™^ÜÛÛœÝ\ØYÙQYSXÙ[œÙ\Ô™]›ÚÙPÜ™X]P›ÙHH›Ù›Øš™XÝ
Âˆœ™X\ÛÛˆŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

BŸJB‚™^ÜÛÛœÝ\ØYÙQYSXÙ[œÙ\Ô™]›ÚÙPÜ™X]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆœ™]›ÚÙYŽˆ›Ù˜›ÛÛX[Š
Kˆ™Ü˜[ÚYŽˆ›ÙœÝš[™Ê
K]ZY

BŸJBŸJB‚‚‚‚‚™^ÜÛÛœÝ\ØYÙQÙ]]]Ô™[ØYÙ][™ÜÓ\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ™]HŽˆ›Ù›Øš™XÝ
Âˆ˜]]Ü™[ØYÙ[˜X›YŽˆ›Ù˜›ÛÛX[Š
Kˆ˜]]Ü™[ØYÝØ[]Ø[[Ý[Žˆ›ÙœÝš[™Ê
Kˆ˜]]Ü™[ØYÝØ[]Ý™\ÚÛŽˆ›ÙœÝš[™Ê
BŸJBŸJB‚‚‚‚‚™^ÜÛÛœÝ\ØYÙQÙ]š[[™Ñ]Z[Ó\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ˜š[[™×Ú[™›ÈŽˆ›Ù›Øš™XÝ
Âˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ™[XZ[Žˆ›ÙœÝš[™Ê
K™[XZ[

K›Ü[Û˜[

Kˆ˜ÛÛ\[žHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ˜š[[™×ØY™\ÜÌHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ˜š[[™×ØY™\ÜÌˆŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ˜Ú]HŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆœÝ]HŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ˜ÛÝ[žHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆœÜÝ[ØÛÙHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ^ÚYŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

BŸJBŸJB‚‚‚‚‚‚‚‚™^ÜÛÛœÝ\ØYÙQÙ]Ý\ÝÛY\’[›ÚXÙ\Ó\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆš[›ÚXÙ\ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
Âˆ™]HŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆšYŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆš\×Ú[›ÚXÙWØ]˜Z[X›HŽˆ›Ù˜›ÛÛX[Š
Kˆ˜[[Ý[Žˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœ™XÙZ\Ý\›Žˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆœ^[Y[Ý\HŽˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJJKˆÝ[Žˆ›Ù›[X™\Š
BŸJBŸJB‚‚™^ÜÛÛœÝ\ØYÙQÙ]\Ý›Ý\‘YÚ]Ó\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆ›\ÝŽˆ›ÙœÝš[™Ê
BŸJBŸJB‚‚™^ÜÛÛœÝ\ØYÙQÙ]Ø[]˜[[˜ÙS\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆØ[]Ø˜[[˜ÙHŽˆ›ÙœÝš[™Ê
BŸJB‚‚™^ÜÛÛœÝ\ØYÙQÙ]]\ÝšXÙ\Ó\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ùœ™XÛÜ™
›ÙœÝš[™Ê
K›Ù›[X™\Š
JBŸJB‚‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Ûš[[™Ó\Ý™\ÜÛœÙT™\Ý[][Pš[[™ÐÛÛXÝ˜[YSX^HLÂ‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Ûš[[™Ó\Ý™\ÜÛœÙT™\Ý[][Pš[[™ÐÛÛXÝ[XZ[X^HMÂ‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Ûš[[™Ó\Ý™\ÜÛœÙT™\Ý[][PÛÛ\[žSX^HLÂ‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Ûš[[™Ó\Ý™\ÜÛœÙT™\Ý[][Pš[[™ÐY™\ÜÌSX^HMNÂ‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Ûš[[™Ó\Ý™\ÜÛœÙT™\Ý[][Pš[[™ÐY™\ÜÌ“X^HMNÂ‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Ûš[[™Ó\Ý™\ÜÛœÙT™\Ý[][PÚ]SX^HLÂ‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Ûš[[™Ó\Ý™\ÜÛœÙT™\Ý[][TÝ]SX^HLÂ‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Ûš[[™Ó\Ý™\ÜÛœÙT™\Ý[][PÛÝ[žSX^HLÂ‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Ûš[[™Ó\Ý™\ÜÛœÙT™\Ý[][TÜÝ[ÛÙSX^HŒÂ‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Ûš[[™Ó\Ý™\ÜÛœÙT™\Ý[][U^YX^HLÂ‚‚‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Ûš[[™Ó\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ›Ü™Ø[š^˜][ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜š[[™×ØÛÛXÝÛ˜[YHŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Ûš[[™Ó\Ý™\ÜÛœÙT™\Ý[][Pš[[™ÐÛÛXÝ˜[YSX^
K›Ü[Û˜[

Kˆ˜š[[™×ØÛÛXÝÙ[XZ[Žˆ›ÙœÝš[™Ê
K™[XZ[

K›X^
\ØYÙSÜ™Ø[š^˜][Ûš[[™Ó\Ý™\ÜÛœÙT™\Ý[][Pš[[™ÐÛÛXÝ[XZ[X^
K›Ü[Û˜[

Kˆ˜ÛÛ\[žHŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Ûš[[™Ó\Ý™\ÜÛœÙT™\Ý[][PÛÛ\[žSX^
K›Ü[Û˜[

Kˆ˜š[[™×ØY™\ÜÌHŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Ûš[[™Ó\Ý™\ÜÛœÙT™\Ý[][Pš[[™ÐY™\ÜÌSX^
K›Ü[Û˜[

Kˆ˜š[[™×ØY™\ÜÌˆŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Ûš[[™Ó\Ý™\ÜÛœÙT™\Ý[][Pš[[™ÐY™\ÜÌ“X^
K›Ü[Û˜[

Kˆ˜Ú]HŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Ûš[[™Ó\Ý™\ÜÛœÙT™\Ý[][PÚ]SX^
K›Ü[Û˜[

KˆœÝ]HŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Ûš[[™Ó\Ý™\ÜÛœÙT™\Ý[][TÝ]SX^
K›Ü[Û˜[

Kˆ˜ÛÝ[žHŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Ûš[[™Ó\Ý™\ÜÛœÙT™\Ý[][PÛÝ[žSX^
K›Ü[Û˜[

KˆœÜÝ[ØÛÙHŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Ûš[[™Ó\Ý™\ÜÛœÙT™\Ý[][TÜÝ[ÛÙSX^
K›Ü[Û˜[

Kˆ^ÚYŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Ûš[[™Ó\Ý™\ÜÛœÙT™\Ý[][U^YX^
K›Ü[Û˜[

BŸJJBŸJB‚‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô\X[\]T\˜[\ÈH›Ù›Øš™XÝ
Âˆ˜š[[™×ÚYŽˆ›ÙœÝš[™Ê
BŸJB‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô\X[\]P›ÙPš[[™ÐÛÛXÝ˜[YSX^HLÂ‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô\X[\]P›ÙPš[[™ÐÛÛXÝ[XZ[X^HMÂ‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô\X[\]P›ÙPÛÛ\[žSX^HLÂ‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô\X[\]P›ÙPš[[™ÐY™\ÜÌSX^HMNÂ‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô\X[\]P›ÙPš[[™ÐY™\ÜÌ“X^HMNÂ‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô\X[\]P›ÙPÚ]SX^HLÂ‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô\X[\]P›ÙTÝ]SX^HLÂ‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô\X[\]P›ÙPÛÝ[žSX^HLÂ‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô\X[\]P›ÙTÜÝ[ÛÙSX^HŒÂ‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô\X[\]P›ÙU^YX^HLÂ‚‚‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô\X[\]P›ÙHH›Ù›Øš™XÝ
Âˆ˜š[[™×ØÛÛXÝÛ˜[YHŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô\X[\]P›ÙPš[[™ÐÛÛXÝ˜[YSX^
K›Ü[Û˜[

Kˆ˜š[[™×ØÛÛXÝÙ[XZ[Žˆ›ÙœÝš[™Ê
K™[XZ[

K›X^
\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô\X[\]P›ÙPš[[™ÐÛÛXÝ[XZ[X^
K›Ü[Û˜[

Kˆ˜ÛÛ\[žHŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô\X[\]P›ÙPÛÛ\[žSX^
K›Ü[Û˜[

Kˆ˜š[[™×ØY™\ÜÌHŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô\X[\]P›ÙPš[[™ÐY™\ÜÌSX^
K›Ü[Û˜[

Kˆ˜š[[™×ØY™\ÜÌˆŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô\X[\]P›ÙPš[[™ÐY™\ÜÌ“X^
K›Ü[Û˜[

Kˆ˜Ú]HŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô\X[\]P›ÙPÚ]SX^
K›Ü[Û˜[

KˆœÝ]HŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô\X[\]P›ÙTÝ]SX^
K›Ü[Û˜[

Kˆ˜ÛÝ[žHŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô\X[\]P›ÙPÛÝ[žSX^
K›Ü[Û˜[

KˆœÜÝ[ØÛÙHŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô\X[\]P›ÙTÜÝ[ÛÙSX^
K›Ü[Û˜[

Kˆ^ÚYŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô\X[\]P›ÙU^YX^
K›Ü[Û˜[

BŸJB‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô\X[\]T™\ÜÛœÙT™\Ý[š[[™ÐÛÛXÝ˜[YSX^HLÂ‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô\X[\]T™\ÜÛœÙT™\Ý[š[[™ÐÛÛXÝ[XZ[X^HMÂ‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô\X[\]T™\ÜÛœÙT™\Ý[ÛÛ\[žSX^HLÂ‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô\X[\]T™\ÜÛœÙT™\Ý[š[[™ÐY™\ÜÌSX^HMNÂ‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô\X[\]T™\ÜÛœÙT™\Ý[š[[™ÐY™\ÜÌ“X^HMNÂ‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô\X[\]T™\ÜÛœÙT™\Ý[Ú]SX^HLÂ‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô\X[\]T™\ÜÛœÙT™\Ý[Ý]SX^HLÂ‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô\X[\]T™\ÜÛœÙT™\Ý[ÛÝ[žSX^HLÂ‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô\X[\]T™\ÜÛœÙT™\Ý[ÜÝ[ÛÙSX^HŒÂ‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô\X[\]T™\ÜÛœÙT™\Ý[^YX^HLÂ‚‚‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô\X[\]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ›Ü™Ø[š^˜][ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜š[[™×ØÛÛXÝÛ˜[YHŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô\X[\]T™\ÜÛœÙT™\Ý[š[[™ÐÛÛXÝ˜[YSX^
K›Ü[Û˜[

Kˆ˜š[[™×ØÛÛXÝÙ[XZ[Žˆ›ÙœÝš[™Ê
K™[XZ[

K›X^
\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô\X[\]T™\ÜÛœÙT™\Ý[š[[™ÐÛÛXÝ[XZ[X^
K›Ü[Û˜[

Kˆ˜ÛÛ\[žHŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô\X[\]T™\ÜÛœÙT™\Ý[ÛÛ\[žSX^
K›Ü[Û˜[

Kˆ˜š[[™×ØY™\ÜÌHŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô\X[\]T™\ÜÛœÙT™\Ý[š[[™ÐY™\ÜÌSX^
K›Ü[Û˜[

Kˆ˜š[[™×ØY™\ÜÌˆŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô\X[\]T™\ÜÛœÙT™\Ý[š[[™ÐY™\ÜÌ“X^
K›Ü[Û˜[

Kˆ˜Ú]HŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô\X[\]T™\ÜÛœÙT™\Ý[Ú]SX^
K›Ü[Û˜[

KˆœÝ]HŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô\X[\]T™\ÜÛœÙT™\Ý[Ý]SX^
K›Ü[Û˜[

Kˆ˜ÛÝ[žHŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô\X[\]T™\ÜÛœÙT™\Ý[ÛÝ[žSX^
K›Ü[Û˜[

KˆœÜÝ[ØÛÙHŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô\X[\]T™\ÜÛœÙT™\Ý[ÜÝ[ÛÙSX^
K›Ü[Û˜[

Kˆ^ÚYŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô\X[\]T™\ÜÛœÙT™\Ý[^YX^
K›Ü[Û˜[

BŸJBŸJB‚‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô™XY\˜[\ÈH›Ù›Øš™XÝ
Âˆ˜š[[™×ÚYŽˆ›ÙœÝš[™Ê
BŸJB‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô™XY™\ÜÛœÙT™\Ý[][Pš[[™ÐÛÛXÝ˜[YSX^HLÂ‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô™XY™\ÜÛœÙT™\Ý[][Pš[[™ÐÛÛXÝ[XZ[X^HMÂ‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô™XY™\ÜÛœÙT™\Ý[][PÛÛ\[žSX^HLÂ‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô™XY™\ÜÛœÙT™\Ý[][Pš[[™ÐY™\ÜÌSX^HMNÂ‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô™XY™\ÜÛœÙT™\Ý[][Pš[[™ÐY™\ÜÌ“X^HMNÂ‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô™XY™\ÜÛœÙT™\Ý[][PÚ]SX^HLÂ‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô™XY™\ÜÛœÙT™\Ý[][TÝ]SX^HLÂ‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô™XY™\ÜÛœÙT™\Ý[][PÛÝ[žSX^HLÂ‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô™XY™\ÜÛœÙT™\Ý[][TÜÝ[ÛÙSX^HŒÂ‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô™XY™\ÜÛœÙT™\Ý[][U^YX^HLÂ‚‚‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô™XY™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ›Ü™Ø[š^˜][ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ˜š[[™×ØÛÛXÝÛ˜[YHŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô™XY™\ÜÛœÙT™\Ý[][Pš[[™ÐÛÛXÝ˜[YSX^
K›Ü[Û˜[

Kˆ˜š[[™×ØÛÛXÝÙ[XZ[Žˆ›ÙœÝš[™Ê
K™[XZ[

K›X^
\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô™XY™\ÜÛœÙT™\Ý[][Pš[[™ÐÛÛXÝ[XZ[X^
K›Ü[Û˜[

Kˆ˜ÛÛ\[žHŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô™XY™\ÜÛœÙT™\Ý[][PÛÛ\[žSX^
K›Ü[Û˜[

Kˆ˜š[[™×ØY™\ÜÌHŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô™XY™\ÜÛœÙT™\Ý[][Pš[[™ÐY™\ÜÌSX^
K›Ü[Û˜[

Kˆ˜š[[™×ØY™\ÜÌˆŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô™XY™\ÜÛœÙT™\Ý[][Pš[[™ÐY™\ÜÌ“X^
K›Ü[Û˜[

Kˆ˜Ú]HŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô™XY™\ÜÛœÙT™\Ý[][PÚ]SX^
K›Ü[Û˜[

KˆœÝ]HŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô™XY™\ÜÛœÙT™\Ý[][TÝ]SX^
K›Ü[Û˜[

Kˆ˜ÛÝ[žHŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô™XY™\ÜÛœÙT™\Ý[][PÛÝ[žSX^
K›Ü[Û˜[

KˆœÜÝ[ØÛÙHŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô™XY™\ÜÛœÙT™\Ý[][TÜÝ[ÛÙSX^
K›Ü[Û˜[

Kˆ^ÚYŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Ûš[[™Ô™XY™\ÜÛœÙT™\Ý[][U^YX^
K›Ü[Û˜[

BŸJJBŸJB‚‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Û‘š[\“\Ý™\ÜÛœÙT™\Ý[][S˜[YSX^HMNÂ‚‚‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Û‘š[\“\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
\ØYÙSÜ™Ø[š^˜][Û‘š[\“\Ý™\ÜÛœÙT™\Ý[][S˜[YSX^
BŸJJBŸJB‚‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[Û“\Ý™\ÜÛœÙT™\Ý[][PÝ\ÝÛTÝXœØÜš\[Û’YX^HLÂ‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[Û“\Ý™\ÜÛœÙT™\Ý[][TÝš\PÝ\ÝÛY\’Y\ÝX^HLÂ‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[Û“\Ý™\ÜÛœÙT™\Ý[][TÝš\PÝ\ÝÛY\’Y]™SX^HLÂ‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[Û“\Ý™\ÜÛœÙT™\Ý[][T^[Y[Y]ÙYX^HLÂ‚‚‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[Û“\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ›Ü™Ø[š^˜][ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

KˆœÝXœØÜš\[Û—ÝY\ˆŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ˜Ý\ÝÛWÜÝXœØÜš\[Û—ÚYŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[Û“\Ý™\ÜÛœÙT™\Ý[][PÝ\ÝÛTÝXœØÜš\[Û’YX^
K›Ü[Û˜[

KˆœÝ]\ÈŽˆ›Ù™[[JÉØXÝ]™IË	Ü\ÝÙYIË	ØØ[˜Ù[Y	Ë	Ú[˜XÝ]™I×JK›Ü[Û˜[

KˆœÝXœØÜš\[Û—ÜšXÙHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	ÔšXÙHÙˆHÝXœØÜš\[Û‹‰ÊKˆØ[]Ø˜[[˜ÙHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆØ[]Ü™Yš[Ø[[Ý[Žˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	Ð[[Ý[È™Yš[HØ[]]™\žH[Û‰ÊKˆ›™^Ü™[™]Ø[Ù]HŽˆ›ÙœÝš[™Ê
K™]J
K›Ü[Û˜[

K™\ØÜšX™J	Ó™^YH]H›Üˆ™[™]Ø[‰ÊKˆœÝXœØÜš\[Û—Ù]\™WÝY\ˆŽˆ›Ù™[[JÉÙœ™YIË	Ø˜\ÚXÉË	Ø˜\ÚX×ÞYX\›IË	ØÝ\ÝÛI×JK›Ü[Û˜[

KˆœÝXœØÜš\[Û—Ù]\™WÜÝ\Ù]HŽˆ›ÙœÝš[™Ê
K™]J
K›Ü[Û˜[

K™\ØÜšX™J	Ó™^YH]H›Üˆ™[™]Ø[‰ÊKˆœÝXœØÜš\[Û—Ù]\™WÜšXÙHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	ÔšXÙHÙˆH]\™HÝXœØÜš\[Û‹‰ÊKˆœÝš\WØÝ\ÝÛY\—ÚYÝ\ÝŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[Û“\Ý™\ÜÛœÙT™\Ý[][TÝš\PÝ\ÝÛY\’Y\ÝX^
K›Ü[Û˜[

K™\ØÜšX™J	ÔÝš\HÝ\ÝÛY\ˆQ›Üˆ\Ý[ÙKˆ•S˜[Y\È\™H[ÝÙY‰ÊKˆœÝš\WØÝ\ÝÛY\—ÚYÛ]™HŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[Û“\Ý™\ÜÛœÙT™\Ý[][TÝš\PÝ\ÝÛY\’Y]™SX^
K›Ü[Û˜[

K™\ØÜšX™J	ÔÝš\HÝ\ÝÛY\ˆQ›Üˆ]™H[ÙKˆ•S˜[Y\È\™H[ÝÙY‰ÊKˆ˜]]×Ü™XÚ\™ÙWÙ[˜X›YŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ˜]]×Ü™XÚ\™ÙWØ[[Ý[Žˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	Ð[[Ý[È™Yš[HØ[]]™\žH[Û‰ÊKˆ˜]]×Ü™XÚ\™ÙWÝ™\ÚÛŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	Õ™\ÚÛÈšYÙÙ\ˆ]]È™XÚ\™ÙK‰ÊKˆœ^[Y[ÛY]ÙÚYŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[Û“\Ý™\ÜÛœÙT™\Ý[][T^[Y[Y]ÙYX^
K›Ü[Û˜[

Kˆ›\ÝÜ™Yš[Ù]HŽˆ›ÙœÝš[™Ê
K™]J
K›Ü[Û˜[

Kˆ›\ÝÜ™Yš[Ø[[Ý[Žˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	Ð[[Ý[ÙˆH\Ý™Yš[‰ÊBŸJJBŸJB‚‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[ÛÜ™X]T\˜[\ÈH›Ù›Øš™XÝ
Âˆ›Ü™Ø[š^˜][Û—ÜÝXœØÜš\[Û—ÚYŽˆ›ÙœÝš[™Ê
BŸJB‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[ÛÜ™X]P›ÙTÝš\PÝ\ÝÛY\’Y\ÝX^HLÂ‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[ÛÜ™X]P›ÙTÝš\PÝ\ÝÛY\’Y]™SX^HLÂ‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[ÛÜ™X]P›ÙT^[Y[Y]ÙYX^HLÂ‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[ÛÜ™X]P›ÙPÝ\ÝÛTÝXœØÜš\[Û’YX^HLÂ‚‚‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[ÛÜ™X]P›ÙHH›Ù›Øš™XÝ
Âˆ›™^Ü™[™]Ø[Ù]HŽˆ›ÙœÝš[™Ê
K™]J
K›Ü[Û˜[

K™\ØÜšX™J	Ó™^YH]H›Üˆ™[™]Ø[‰ÊKˆœÝXœØÜš\[Û—ÜšXÙHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	ÔšXÙHÙˆHÝXœØÜš\[Û‹‰ÊKˆœÝXœØÜš\[Û—Ù]\™WÝY\ˆŽˆ›Ù™[[JÉÙœ™YIË	Ø˜\ÚXÉË	Ø˜\ÚX×ÞYX\›IË	ØÝ\ÝÛI×JK›Ü[Û˜[

KˆœÝXœØÜš\[Û—Ù]\™WÜÝ\Ù]HŽˆ›ÙœÝš[™Ê
K™]J
K›Ü[Û˜[

K™\ØÜšX™J	Ó™^YH]H›Üˆ™[™]Ø[‰ÊKˆœÝXœØÜš\[Û—Ù]\™WÜšXÙHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	ÔšXÙHÙˆH]\™HÝXœØÜš\[Û‹‰ÊKˆœÝ]\ÈŽˆ›Ù™[[JÉØXÝ]™IË	Ü\ÝÙYIË	ØØ[˜Ù[Y	Ë	Ú[˜XÝ]™I×JK›Ü[Û˜[

KˆØ[]Ü™Yš[Ø[[Ý[Žˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	Ð[[Ý[È™Yš[HØ[]]™\žH[Û‰ÊKˆØ[]Ø˜[[˜ÙHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆœÝš\WØÝ\ÝÛY\—ÚYÝ\ÝŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[ÛÜ™X]P›ÙTÝš\PÝ\ÝÛY\’Y\ÝX^
K›Ü[Û˜[

K™\ØÜšX™J	ÔÝš\HÝ\ÝÛY\ˆQ›Üˆ\Ý[ÙKˆ•S˜[Y\È\™H[ÝÙY‰ÊKˆœÝš\WØÝ\ÝÛY\—ÚYÛ]™HŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[ÛÜ™X]P›ÙTÝš\PÝ\ÝÛY\’Y]™SX^
K›Ü[Û˜[

K™\ØÜšX™J	ÔÝš\HÝ\ÝÛY\ˆQ›Üˆ]™H[ÙKˆ•S˜[Y\È\™H[ÝÙY‰ÊKˆ˜]]×Ü™XÚ\™ÙWÙ[˜X›YŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ˜]]×Ü™XÚ\™ÙWØ[[Ý[Žˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	Ð[[Ý[È™Yš[HØ[]]™\žH[Û‰ÊKˆ˜]]×Ü™XÚ\™ÙWÝ™\ÚÛŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	Õ™\ÚÛÈšYÙÙ\ˆ]]È™XÚ\™ÙK‰ÊKˆœ^[Y[ÛY]ÙÚYŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[ÛÜ™X]P›ÙT^[Y[Y]ÙYX^
K›Ü[Û˜[

Kˆ˜Ý\ÝÛWÜÝXœØÜš\[Û—ÚYŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[ÛÜ™X]P›ÙPÝ\ÝÛTÝXœØÜš\[Û’YX^
K›Ü[Û˜[

Kˆ›Ü™Ø[š^˜][ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

KˆœÝXœØÜš\[Û—ÝY\ˆŽˆ›Ù›[X™\Š
BŸJB‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[ÛÜ™X]T™\ÜÛœÙT™\Ý[Ýš\PÝ\ÝÛY\’Y\ÝX^HLÂ‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[ÛÜ™X]T™\ÜÛœÙT™\Ý[Ýš\PÝ\ÝÛY\’Y]™SX^HLÂ‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[ÛÜ™X]T™\ÜÛœÙT™\Ý[^[Y[Y]ÙYX^HLÂ‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[ÛÜ™X]T™\ÜÛœÙT™\Ý[Ý\ÝÛTÝXœØÜš\[Û’YX^HLÂ‚‚‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[ÛÜ™X]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆ›™^Ü™[™]Ø[Ù]HŽˆ›ÙœÝš[™Ê
K™]J
K›Ü[Û˜[

K™\ØÜšX™J	Ó™^YH]H›Üˆ™[™]Ø[‰ÊKˆœÝXœØÜš\[Û—ÜšXÙHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	ÔšXÙHÙˆHÝXœØÜš\[Û‹‰ÊKˆœÝXœØÜš\[Û—Ù]\™WÝY\ˆŽˆ›Ù™[[JÉÙœ™YIË	Ø˜\ÚXÉË	Ø˜\ÚX×ÞYX\›IË	ØÝ\ÝÛI×JK›Ü[Û˜[

KˆœÝXœØÜš\[Û—Ù]\™WÜÝ\Ù]HŽˆ›ÙœÝš[™Ê
K™]J
K›Ü[Û˜[

K™\ØÜšX™J	Ó™^YH]H›Üˆ™[™]Ø[‰ÊKˆœÝXœØÜš\[Û—Ù]\™WÜšXÙHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	ÔšXÙHÙˆH]\™HÝXœØÜš\[Û‹‰ÊKˆœÝ]\ÈŽˆ›Ù™[[JÉØXÝ]™IË	Ü\ÝÙYIË	ØØ[˜Ù[Y	Ë	Ú[˜XÝ]™I×JK›Ü[Û˜[

KˆØ[]Ü™Yš[Ø[[Ý[Žˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	Ð[[Ý[È™Yš[HØ[]]™\žH[Û‰ÊKˆØ[]Ø˜[[˜ÙHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆœÝš\WØÝ\ÝÛY\—ÚYÝ\ÝŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[ÛÜ™X]T™\ÜÛœÙT™\Ý[Ýš\PÝ\ÝÛY\’Y\ÝX^
K›Ü[Û˜[

K™\ØÜšX™J	ÔÝš\HÝ\ÝÛY\ˆQ›Üˆ\Ý[ÙKˆ•S˜[Y\È\™H[ÝÙY‰ÊKˆœÝš\WØÝ\ÝÛY\—ÚYÛ]™HŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[ÛÜ™X]T™\ÜÛœÙT™\Ý[Ýš\PÝ\ÝÛY\’Y]™SX^
K›Ü[Û˜[

K™\ØÜšX™J	ÔÝš\HÝ\ÝÛY\ˆQ›Üˆ]™H[ÙKˆ•S˜[Y\È\™H[ÝÙY‰ÊKˆ˜]]×Ü™XÚ\™ÙWÙ[˜X›YŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ˜]]×Ü™XÚ\™ÙWØ[[Ý[Žˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	Ð[[Ý[È™Yš[HØ[]]™\žH[Û‰ÊKˆ˜]]×Ü™XÚ\™ÙWÝ™\ÚÛŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	Õ™\ÚÛÈšYÙÙ\ˆ]]È™XÚ\™ÙK‰ÊKˆœ^[Y[ÛY]ÙÚYŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[ÛÜ™X]T™\ÜÛœÙT™\Ý[^[Y[Y]ÙYX^
K›Ü[Û˜[

Kˆ˜Ý\ÝÛWÜÝXœØÜš\[Û—ÚYŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[ÛÜ™X]T™\ÜÛœÙT™\Ý[Ý\ÝÛTÝXœØÜš\[Û’YX^
K›Ü[Û˜[

Kˆ›Ü™Ø[š^˜][ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

KˆœÝXœØÜš\[Û—ÝY\ˆŽˆ›Ù›[X™\Š
BŸJBŸJB‚‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[Û”\X[\]T\˜[\ÈH›Ù›Øš™XÝ
Âˆ›Ü™Ø[š^˜][Û—ÜÝXœØÜš\[Û—ÚYŽˆ›ÙœÝš[™Ê
BŸJB‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[Û”\X[\]P›ÙTÝš\PÝ\ÝÛY\’Y\ÝX^HLÂ‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[Û”\X[\]P›ÙTÝš\PÝ\ÝÛY\’Y]™SX^HLÂ‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[Û”\X[\]P›ÙT^[Y[Y]ÙYX^HLÂ‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[Û”\X[\]P›ÙPÝ\ÝÛTÝXœØÜš\[Û’YX^HLÂ‚‚‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[Û”\X[\]P›ÙHH›Ù›Øš™XÝ
Âˆ›™^Ü™[™]Ø[Ù]HŽˆ›ÙœÝš[™Ê
K™]J
K›Ü[Û˜[

K™\ØÜšX™J	Ó™^YH]H›Üˆ™[™]Ø[‰ÊKˆœÝXœØÜš\[Û—ÜšXÙHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	ÔšXÙHÙˆHÝXœØÜš\[Û‹‰ÊKˆœÝXœØÜš\[Û—Ù]\™WÝY\ˆŽˆ›Ù™[[JÉÙœ™YIË	Ø˜\ÚXÉË	Ø˜\ÚX×ÞYX\›IË	ØÝ\ÝÛI×JK›Ü[Û˜[

KˆœÝXœØÜš\[Û—Ù]\™WÜÝ\Ù]HŽˆ›ÙœÝš[™Ê
K™]J
K›Ü[Û˜[

K™\ØÜšX™J	Ó™^YH]H›Üˆ™[™]Ø[‰ÊKˆœÝXœØÜš\[Û—Ù]\™WÜšXÙHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	ÔšXÙHÙˆH]\™HÝXœØÜš\[Û‹‰ÊKˆœÝ]\ÈŽˆ›Ù™[[JÉØXÝ]™IË	Ü\ÝÙYIË	ØØ[˜Ù[Y	Ë	Ú[˜XÝ]™I×JK›Ü[Û˜[

KˆØ[]Ü™Yš[Ø[[Ý[Žˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	Ð[[Ý[È™Yš[HØ[]]™\žH[Û‰ÊKˆØ[]Ø˜[[˜ÙHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆœÝš\WØÝ\ÝÛY\—ÚYÝ\ÝŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[Û”\X[\]P›ÙTÝš\PÝ\ÝÛY\’Y\ÝX^
K›Ü[Û˜[

K™\ØÜšX™J	ÔÝš\HÝ\ÝÛY\ˆQ›Üˆ\Ý[ÙKˆ•S˜[Y\È\™H[ÝÙY‰ÊKˆœÝš\WØÝ\ÝÛY\—ÚYÛ]™HŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[Û”\X[\]P›ÙTÝš\PÝ\ÝÛY\’Y]™SX^
K›Ü[Û˜[

K™\ØÜšX™J	ÔÝš\HÝ\ÝÛY\ˆQ›Üˆ]™H[ÙKˆ•S˜[Y\È\™H[ÝÙY‰ÊKˆ˜]]×Ü™XÚ\™ÙWÙ[˜X›YŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ˜]]×Ü™XÚ\™ÙWØ[[Ý[Žˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	Ð[[Ý[È™Yš[HØ[]]™\žH[Û‰ÊKˆ˜]]×Ü™XÚ\™ÙWÝ™\ÚÛŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	Õ™\ÚÛÈšYÙÙ\ˆ]]È™XÚ\™ÙK‰ÊKˆœ^[Y[ÛY]ÙÚYŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[Û”\X[\]P›ÙT^[Y[Y]ÙYX^
K›Ü[Û˜[

Kˆ˜Ý\ÝÛWÜÝXœØÜš\[Û—ÚYŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[Û”\X[\]P›ÙPÝ\ÝÛTÝXœØÜš\[Û’YX^
K›Ü[Û˜[

Kˆ›Ü™Ø[š^˜][ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

KˆœÝXœØÜš\[Û—ÝY\ˆŽˆ›Ù›[X™\Š
BŸJB‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[Û”\X[\]T™\ÜÛœÙT™\Ý[Ýš\PÝ\ÝÛY\’Y\ÝX^HLÂ‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[Û”\X[\]T™\ÜÛœÙT™\Ý[Ýš\PÝ\ÝÛY\’Y]™SX^HLÂ‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[Û”\X[\]T™\ÜÛœÙT™\Ý[^[Y[Y]ÙYX^HLÂ‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[Û”\X[\]T™\ÜÛœÙT™\Ý[Ý\ÝÛTÝXœØÜš\[Û’YX^HLÂ‚‚‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[Û”\X[\]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆ›™^Ü™[™]Ø[Ù]HŽˆ›ÙœÝš[™Ê
K™]J
K›Ü[Û˜[

K™\ØÜšX™J	Ó™^YH]H›Üˆ™[™]Ø[‰ÊKˆœÝXœØÜš\[Û—ÜšXÙHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	ÔšXÙHÙˆHÝXœØÜš\[Û‹‰ÊKˆœÝXœØÜš\[Û—Ù]\™WÝY\ˆŽˆ›Ù™[[JÉÙœ™YIË	Ø˜\ÚXÉË	Ø˜\ÚX×ÞYX\›IË	ØÝ\ÝÛI×JK›Ü[Û˜[

KˆœÝXœØÜš\[Û—Ù]\™WÜÝ\Ù]HŽˆ›ÙœÝš[™Ê
K™]J
K›Ü[Û˜[

K™\ØÜšX™J	Ó™^YH]H›Üˆ™[™]Ø[‰ÊKˆœÝXœØÜš\[Û—Ù]\™WÜšXÙHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	ÔšXÙHÙˆH]\™HÝXœØÜš\[Û‹‰ÊKˆœÝ]\ÈŽˆ›Ù™[[JÉØXÝ]™IË	Ü\ÝÙYIË	ØØ[˜Ù[Y	Ë	Ú[˜XÝ]™I×JK›Ü[Û˜[

KˆØ[]Ü™Yš[Ø[[Ý[Žˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	Ð[[Ý[È™Yš[HØ[]]™\žH[Û‰ÊKˆØ[]Ø˜[[˜ÙHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆœÝš\WØÝ\ÝÛY\—ÚYÝ\ÝŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[Û”\X[\]T™\ÜÛœÙT™\Ý[Ýš\PÝ\ÝÛY\’Y\ÝX^
K›Ü[Û˜[

K™\ØÜšX™J	ÔÝš\HÝ\ÝÛY\ˆQ›Üˆ\Ý[ÙKˆ•S˜[Y\È\™H[ÝÙY‰ÊKˆœÝš\WØÝ\ÝÛY\—ÚYÛ]™HŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[Û”\X[\]T™\ÜÛœÙT™\Ý[Ýš\PÝ\ÝÛY\’Y]™SX^
K›Ü[Û˜[

K™\ØÜšX™J	ÔÝš\HÝ\ÝÛY\ˆQ›Üˆ]™H[ÙKˆ•S˜[Y\È\™H[ÝÙY‰ÊKˆ˜]]×Ü™XÚ\™ÙWÙ[˜X›YŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ˜]]×Ü™XÚ\™ÙWØ[[Ý[Žˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	Ð[[Ý[È™Yš[HØ[]]™\žH[Û‰ÊKˆ˜]]×Ü™XÚ\™ÙWÝ™\ÚÛŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	Õ™\ÚÛÈšYÙÙ\ˆ]]È™XÚ\™ÙK‰ÊKˆœ^[Y[ÛY]ÙÚYŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[Û”\X[\]T™\ÜÛœÙT™\Ý[^[Y[Y]ÙYX^
K›Ü[Û˜[

Kˆ˜Ý\ÝÛWÜÝXœØÜš\[Û—ÚYŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[Û”\X[\]T™\ÜÛœÙT™\Ý[Ý\ÝÛTÝXœØÜš\[Û’YX^
K›Ü[Û˜[

Kˆ›Ü™Ø[š^˜][ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

KˆœÝXœØÜš\[Û—ÝY\ˆŽˆ›Ù›[X™\Š
BŸJBŸJB‚‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[Û‘[]T\˜[\ÈH›Ù›Øš™XÝ
Âˆ›Ü™Ø[š^˜][Û—ÜÝXœØÜš\[Û—ÚYŽˆ›ÙœÝš[™Ê
BŸJB‚‚‚‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[Û‘[]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJB‚‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[Û”™XY\˜[\ÈH›Ù›Øš™XÝ
Âˆ›Ü™Ø[š^˜][Û—ÜÝXœØÜš\[Û—ÚYŽˆ›ÙœÝš[™Ê
BŸJB‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[Û”™XY™\ÜÛœÙT™\Ý[][PÝ\ÝÛTÝXœØÜš\[Û’YX^HLÂ‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[Û”™XY™\ÜÛœÙT™\Ý[][TÝš\PÝ\ÝÛY\’Y\ÝX^HLÂ‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[Û”™XY™\ÜÛœÙT™\Ý[][TÝš\PÝ\ÝÛY\’Y]™SX^HLÂ‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[Û”™XY™\ÜÛœÙT™\Ý[][T^[Y[Y]ÙYX^HLÂ‚‚‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[Û”™XY™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ›Ü™Ø[š^˜][ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

KˆœÝXœØÜš\[Û—ÝY\ˆŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ˜Ý\ÝÛWÜÝXœØÜš\[Û—ÚYŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[Û”™XY™\ÜÛœÙT™\Ý[][PÝ\ÝÛTÝXœØÜš\[Û’YX^
K›Ü[Û˜[

KˆœÝ]\ÈŽˆ›Ù™[[JÉØXÝ]™IË	Ü\ÝÙYIË	ØØ[˜Ù[Y	Ë	Ú[˜XÝ]™I×JK›Ü[Û˜[

KˆœÝXœØÜš\[Û—ÜšXÙHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	ÔšXÙHÙˆHÝXœØÜš\[Û‹‰ÊKˆØ[]Ø˜[[˜ÙHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆØ[]Ü™Yš[Ø[[Ý[Žˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	Ð[[Ý[È™Yš[HØ[]]™\žH[Û‰ÊKˆ›™^Ü™[™]Ø[Ù]HŽˆ›ÙœÝš[™Ê
K™]J
K›Ü[Û˜[

K™\ØÜšX™J	Ó™^YH]H›Üˆ™[™]Ø[‰ÊKˆœÝXœØÜš\[Û—Ù]\™WÝY\ˆŽˆ›Ù™[[JÉÙœ™YIË	Ø˜\ÚXÉË	Ø˜\ÚX×ÞYX\›IË	ØÝ\ÝÛI×JK›Ü[Û˜[

KˆœÝXœØÜš\[Û—Ù]\™WÜÝ\Ù]HŽˆ›ÙœÝš[™Ê
K™]J
K›Ü[Û˜[

K™\ØÜšX™J	Ó™^YH]H›Üˆ™[™]Ø[‰ÊKˆœÝXœØÜš\[Û—Ù]\™WÜšXÙHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	ÔšXÙHÙˆH]\™HÝXœØÜš\[Û‹‰ÊKˆœÝš\WØÝ\ÝÛY\—ÚYÝ\ÝŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[Û”™XY™\ÜÛœÙT™\Ý[][TÝš\PÝ\ÝÛY\’Y\ÝX^
K›Ü[Û˜[

K™\ØÜšX™J	ÔÝš\HÝ\ÝÛY\ˆQ›Üˆ\Ý[ÙKˆ•S˜[Y\È\™H[ÝÙY‰ÊKˆœÝš\WØÝ\ÝÛY\—ÚYÛ]™HŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[Û”™XY™\ÜÛœÙT™\Ý[][TÝš\PÝ\ÝÛY\’Y]™SX^
K›Ü[Û˜[

K™\ØÜšX™J	ÔÝš\HÝ\ÝÛY\ˆQ›Üˆ]™H[ÙKˆ•S˜[Y\È\™H[ÝÙY‰ÊKˆ˜]]×Ü™XÚ\™ÙWÙ[˜X›YŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ˜]]×Ü™XÚ\™ÙWØ[[Ý[Žˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	Ð[[Ý[È™Yš[HØ[]]™\žH[Û‰ÊKˆ˜]]×Ü™XÚ\™ÙWÝ™\ÚÛŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	Õ™\ÚÛÈšYÙÙ\ˆ]]È™XÚ\™ÙK‰ÊKˆœ^[Y[ÛY]ÙÚYŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙSÜ™Ø[š^˜][Û”ÝXœØÜš\[Û”™XY™\ÜÛœÙT™\Ý[][T^[Y[Y]ÙYX^
K›Ü[Û˜[

Kˆ›\ÝÜ™Yš[Ù]HŽˆ›ÙœÝš[™Ê
K™]J
K›Ü[Û˜[

Kˆ›\ÝÜ™Yš[Ø[[Ý[Žˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	Ð[[Ý[ÙˆH\Ý™Yš[‰ÊBŸJJBŸJB‚‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][ÛœÓ\Ý™\ÜÛœÙT™\Ý[][S˜[YSX^HMNÂ‚‚‚™^ÜÛÛœÝ\ØYÙSÜ™Ø[š^˜][ÛœÓ\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›X^
\ØYÙSÜ™Ø[š^˜][ÛœÓ\Ý™\ÜÛœÙT™\Ý[][S˜[YSX^
BŸJJBŸJB‚‚™^ÜÛÛœÝ\ØYÙTšXÚ[™ÐØ\™]Z[ÐÜ™X]P›ÙHH›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

B‚™^ÜÛÛœÝ\ØYÙTšXÚ[™ÐØ\™]Z[ÐÜ™X]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆ˜\Ú[™\Ü×Û[ÛWÜšXÙHŽˆ›Ù›[X™\Š
Kˆ˜\Ú[™\Ü×ÞYX\›WÜšXÙHŽˆ›Ù›[X™\Š
Kˆ™\ØÛÝ[Ü\˜Ù[YÙHŽˆ›Ù›[X™\Š
Kˆ˜Ý\ÝÛWÜšXÙHŽˆ›Ù›[X™\Š
K›Ü[Û˜[

BŸJBŸJB‚‚™^ÜÛÛœÝ\ØYÙTšXÚ[™Ó\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ˜\WØØ[Ý\HŽˆ›Ù›[X™\Š
KˆœšXÙWÜ\—ØØ[Žˆ›ÙœÝš[™Ê
Kˆ›Ü™Ø[š^˜][ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJJBŸJB‚‚™^ÜÛÛœÝ\ØYÙTšXÚ[™ÐÜ™X]T\˜[\ÈH›Ù›Øš™XÝ
ÂˆœšXÚ[™×ÚYŽˆ›ÙœÝš[™Ê
BŸJB‚™^ÜÛÛœÝ\ØYÙTšXÚ[™ÐÜ™X]P›ÙHH›Ù›Øš™XÝ
Âˆ˜\WØØ[Ý\HŽˆ›Ù›[X™\Š
KˆœšXÙWÜ\—ØØ[Žˆ›ÙœÝš[™Ê
Kˆ›Ü™Ø[š^˜][ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJB‚™^ÜÛÛœÝ\ØYÙTšXÚ[™ÐÜ™X]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ˜\WØØ[Ý\HŽˆ›Ù›[X™\Š
KˆœšXÙWÜ\—ØØ[Žˆ›ÙœÝš[™Ê
Kˆ›Ü™Ø[š^˜][ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJBŸJB‚‚™^ÜÛÛœÝ\ØYÙTšXÚ[™Ô\X[\]T\˜[\ÈH›Ù›Øš™XÝ
ÂˆœšXÚ[™×ÚYŽˆ›ÙœÝš[™Ê
BŸJB‚™^ÜÛÛœÝ\ØYÙTšXÚ[™Ô\X[\]P›ÙHH›Ù›Øš™XÝ
Âˆ˜\WØØ[Ý\HŽˆ›Ù›[X™\Š
KˆœšXÙWÜ\—ØØ[Žˆ›ÙœÝš[™Ê
Kˆ›Ü™Ø[š^˜][ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJB‚™^ÜÛÛœÝ\ØYÙTšXÚ[™Ô\X[\]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ˜\WØØ[Ý\HŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆœšXÙWÜ\—ØØ[Žˆ›ÙœÝš[™Ê
Kˆ›Ü™Ø[š^˜][ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJBŸJB‚‚™^ÜÛÛœÝ\ØYÙTšXÚ[™Ñ[]T\˜[\ÈH›Ù›Øš™XÝ
ÂˆœšXÚ[™×ÚYŽˆ›ÙœÝš[™Ê
BŸJB‚‚‚‚™^ÜÛÛœÝ\ØYÙTšXÚ[™Ñ[]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJB‚‚™^ÜÛÛœÝ\ØYÙTšXÚ[™Ô™XY\˜[\ÈH›Ù›Øš™XÝ
ÂˆœšXÚ[™×ÚYŽˆ›ÙœÝš[™Ê
BŸJB‚™^ÜÛÛœÝ\ØYÙTšXÚ[™Ô™XY™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ˜\WØØ[Ý\HŽˆ›Ù›[X™\Š
KˆœšXÙWÜ\—ØØ[Žˆ›ÙœÝš[™Ê
Kˆ›Ü™Ø[š^˜][ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJJBŸJB‚‚™^ÜÛÛœÝ\ØYÙT˜]S[Z]Ó\Ý™\ÜÛœÙT™\Ý[][SZ[]S[Z]Z[ˆHÂ™^ÜÛÛœÝ\ØYÙT˜]S[Z]Ó\Ý™\ÜÛœÙT™\Ý[][SZ[]S[Z]X^HŒMÍÍÎÂ‚™^ÜÛÛœÝ\ØYÙT˜]S[Z]Ó\Ý™\ÜÛœÙT™\Ý[][RÝ\“[Z]Z[ˆHÂ™^ÜÛÛœÝ\ØYÙT˜]S[Z]Ó\Ý™\ÜÛœÙT™\Ý[][RÝ\“[Z]X^HŒMÍÍÎÂ‚™^ÜÛÛœÝ\ØYÙT˜]S[Z]Ó\Ý™\ÜÛœÙT™\Ý[][Q^S[Z]Z[ˆHÂ™^ÜÛÛœÝ\ØYÙT˜]S[Z]Ó\Ý™\ÜÛœÙT™\Ý[][Q^S[Z]X^HŒMÍÍÎÂ‚™^ÜÛÛœÝ\ØYÙT˜]S[Z]Ó\Ý™\ÜÛœÙT™\Ý[][S[Û[Z]Z[ˆHÂ™^ÜÛÛœÝ\ØYÙT˜]S[Z]Ó\Ý™\ÜÛœÙT™\Ý[][S[Û[Z]X^HŒMÍÍÎÂ‚‚‚™^ÜÛÛœÝ\ØYÙT˜]S[Z]Ó\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ˜\WØØ[Ý\HŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ›Ü™Ø[š^˜][ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›Z[]WÛ[Z]Žˆ›Ù›[X™\Š
K›Z[Š\ØYÙT˜]S[Z]Ó\Ý™\ÜÛœÙT™\Ý[][SZ[]S[Z]Z[ŠK›X^
\ØYÙT˜]S[Z]Ó\Ý™\ÜÛœÙT™\Ý[][SZ[]S[Z]X^
K›Ü[Û˜[

K™\ØÜšX™J	ÓX^Ø[È\ˆZ[]IÊKˆšÝ\—Û[Z]Žˆ›Ù›[X™\Š
K›Z[Š\ØYÙT˜]S[Z]Ó\Ý™\ÜÛœÙT™\Ý[][RÝ\“[Z]Z[ŠK›X^
\ØYÙT˜]S[Z]Ó\Ý™\ÜÛœÙT™\Ý[][RÝ\“[Z]X^
K›Ü[Û˜[

K™\ØÜšX™J	ÓX^Ø[È\ˆÝ\‰ÊKˆ™^WÛ[Z]Žˆ›Ù›[X™\Š
K›Z[Š\ØYÙT˜]S[Z]Ó\Ý™\ÜÛœÙT™\Ý[][Q^S[Z]Z[ŠK›X^
\ØYÙT˜]S[Z]Ó\Ý™\ÜÛœÙT™\Ý[][Q^S[Z]X^
K›Ü[Û˜[

K™\ØÜšX™J	ÓX^Ø[È\ˆ^IÊKˆ›[ÛÛ[Z]Žˆ›Ù›[X™\Š
K›Z[Š\ØYÙT˜]S[Z]Ó\Ý™\ÜÛœÙT™\Ý[][S[Û[Z]Z[ŠK›X^
\ØYÙT˜]S[Z]Ó\Ý™\ÜÛœÙT™\Ý[][S[Û[Z]X^
K›Ü[Û˜[

K™\ØÜšX™J	ÓX^Ø[È\ˆ[Û	ÊKˆœÝXœØÜš\[Û—ÝY\ˆŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

BŸJJBŸJB‚‚™^ÜÛÛœÝ\ØYÙT˜]S[Z]ÐÜ™X]T\˜[\ÈH›Ù›Øš™XÝ
Âˆœ˜]WÛ[Z]ÚYŽˆ›ÙœÝš[™Ê
BŸJB‚™^ÜÛÛœÝ\ØYÙT˜]S[Z]ÐÜ™X]P›ÙSZ[]S[Z]Z[ˆHÂ™^ÜÛÛœÝ\ØYÙT˜]S[Z]ÐÜ™X]P›ÙSZ[]S[Z]X^HŒMÍÍÎÂ‚™^ÜÛÛœÝ\ØYÙT˜]S[Z]ÐÜ™X]P›ÙRÝ\“[Z]Z[ˆHÂ™^ÜÛÛœÝ\ØYÙT˜]S[Z]ÐÜ™X]P›ÙRÝ\“[Z]X^HŒMÍÍÎÂ‚™^ÜÛÛœÝ\ØYÙT˜]S[Z]ÐÜ™X]P›ÙQ^S[Z]Z[ˆHÂ™^ÜÛÛœÝ\ØYÙT˜]S[Z]ÐÜ™X]P›ÙQ^S[Z]X^HŒMÍÍÎÂ‚™^ÜÛÛœÝ\ØYÙT˜]S[Z]ÐÜ™X]P›ÙS[Û[Z]Z[ˆHÂ™^ÜÛÛœÝ\ØYÙT˜]S[Z]ÐÜ™X]P›ÙS[Û[Z]X^HŒMÍÍÎÂ‚‚‚™^ÜÛÛœÝ\ØYÙT˜]S[Z]ÐÜ™X]P›ÙHH›Ù›Øš™XÝ
Âˆ˜\WØØ[Ý\HŽˆ›Ù›[X™\Š
Kˆ›Ü™Ø[š^˜][ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›Z[]WÛ[Z]Žˆ›Ù›[X™\Š
K›Z[Š\ØYÙT˜]S[Z]ÐÜ™X]P›ÙSZ[]S[Z]Z[ŠK›X^
\ØYÙT˜]S[Z]ÐÜ™X]P›ÙSZ[]S[Z]X^
K›Ü[Û˜[

K™\ØÜšX™J	ÓX^Ø[È\ˆZ[]IÊKˆšÝ\—Û[Z]Žˆ›Ù›[X™\Š
K›Z[Š\ØYÙT˜]S[Z]ÐÜ™X]P›ÙRÝ\“[Z]Z[ŠK›X^
\ØYÙT˜]S[Z]ÐÜ™X]P›ÙRÝ\“[Z]X^
K›Ü[Û˜[

K™\ØÜšX™J	ÓX^Ø[È\ˆÝ\‰ÊKˆ™^WÛ[Z]Žˆ›Ù›[X™\Š
K›Z[Š\ØYÙT˜]S[Z]ÐÜ™X]P›ÙQ^S[Z]Z[ŠK›X^
\ØYÙT˜]S[Z]ÐÜ™X]P›ÙQ^S[Z]X^
K›Ü[Û˜[

K™\ØÜšX™J	ÓX^Ø[È\ˆ^IÊKˆ›[ÛÛ[Z]Žˆ›Ù›[X™\Š
K›Z[Š\ØYÙT˜]S[Z]ÐÜ™X]P›ÙS[Û[Z]Z[ŠK›X^
\ØYÙT˜]S[Z]ÐÜ™X]P›ÙS[Û[Z]X^
K›Ü[Û˜[

K™\ØÜšX™J	ÓX^Ø[È\ˆ[Û	ÊKˆœÝXœØÜš\[Û—ÝY\ˆŽˆ›Ù›[X™\Š
BŸJB‚™^ÜÛÛœÝ\ØYÙT˜]S[Z]ÐÜ™X]T™\ÜÛœÙT™\Ý[Z[]S[Z]Z[ˆHÂ™^ÜÛÛœÝ\ØYÙT˜]S[Z]ÐÜ™X]T™\ÜÛœÙT™\Ý[Z[]S[Z]X^HŒMÍÍÎÂ‚™^ÜÛÛœÝ\ØYÙT˜]S[Z]ÐÜ™X]T™\ÜÛœÙT™\Ý[Ý\“[Z]Z[ˆHÂ™^ÜÛÛœÝ\ØYÙT˜]S[Z]ÐÜ™X]T™\ÜÛœÙT™\Ý[Ý\“[Z]X^HŒMÍÍÎÂ‚™^ÜÛÛœÝ\ØYÙT˜]S[Z]ÐÜ™X]T™\ÜÛœÙT™\Ý[^S[Z]Z[ˆHÂ™^ÜÛÛœÝ\ØYÙT˜]S[Z]ÐÜ™X]T™\ÜÛœÙT™\Ý[^S[Z]X^HŒMÍÍÎÂ‚™^ÜÛÛœÝ\ØYÙT˜]S[Z]ÐÜ™X]T™\ÜÛœÙT™\Ý[[Û[Z]Z[ˆHÂ™^ÜÛÛœÝ\ØYÙT˜]S[Z]ÐÜ™X]T™\ÜÛœÙT™\Ý[[Û[Z]X^HŒMÍÍÎÂ‚‚‚™^ÜÛÛœÝ\ØYÙT˜]S[Z]ÐÜ™X]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ˜\WØØ[Ý\HŽˆ›Ù›[X™\Š
Kˆ›Ü™Ø[š^˜][ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›Z[]WÛ[Z]Žˆ›Ù›[X™\Š
K›Z[Š\ØYÙT˜]S[Z]ÐÜ™X]T™\ÜÛœÙT™\Ý[Z[]S[Z]Z[ŠK›X^
\ØYÙT˜]S[Z]ÐÜ™X]T™\ÜÛœÙT™\Ý[Z[]S[Z]X^
K›Ü[Û˜[

K™\ØÜšX™J	ÓX^Ø[È\ˆZ[]IÊKˆšÝ\—Û[Z]Žˆ›Ù›[X™\Š
K›Z[Š\ØYÙT˜]S[Z]ÐÜ™X]T™\ÜÛœÙT™\Ý[Ý\“[Z]Z[ŠK›X^
\ØYÙT˜]S[Z]ÐÜ™X]T™\ÜÛœÙT™\Ý[Ý\“[Z]X^
K›Ü[Û˜[

K™\ØÜšX™J	ÓX^Ø[È\ˆÝ\‰ÊKˆ™^WÛ[Z]Žˆ›Ù›[X™\Š
K›Z[Š\ØYÙT˜]S[Z]ÐÜ™X]T™\ÜÛœÙT™\Ý[^S[Z]Z[ŠK›X^
\ØYÙT˜]S[Z]ÐÜ™X]T™\ÜÛœÙT™\Ý[^S[Z]X^
K›Ü[Û˜[

K™\ØÜšX™J	ÓX^Ø[È\ˆ^IÊKˆ›[ÛÛ[Z]Žˆ›Ù›[X™\Š
K›Z[Š\ØYÙT˜]S[Z]ÐÜ™X]T™\ÜÛœÙT™\Ý[[Û[Z]Z[ŠK›X^
\ØYÙT˜]S[Z]ÐÜ™X]T™\ÜÛœÙT™\Ý[[Û[Z]X^
K›Ü[Û˜[

K™\ØÜšX™J	ÓX^Ø[È\ˆ[Û	ÊKˆœÝXœØÜš\[Û—ÝY\ˆŽˆ›Ù›[X™\Š
BŸJBŸJB‚‚™^ÜÛÛœÝ\ØYÙT˜]S[Z]Ô\X[\]T\˜[\ÈH›Ù›Øš™XÝ
Âˆœ˜]WÛ[Z]ÚYŽˆ›ÙœÝš[™Ê
BŸJB‚™^ÜÛÛœÝ\ØYÙT˜]S[Z]Ô\X[\]P›ÙSZ[]S[Z]Z[ˆHÂ™^ÜÛÛœÝ\ØYÙT˜]S[Z]Ô\X[\]P›ÙSZ[]S[Z]X^HŒMÍÍÎÂ‚™^ÜÛÛœÝ\ØYÙT˜]S[Z]Ô\X[\]P›ÙRÝ\“[Z]Z[ˆHÂ™^ÜÛÛœÝ\ØYÙT˜]S[Z]Ô\X[\]P›ÙRÝ\“[Z]X^HŒMÍÍÎÂ‚™^ÜÛÛœÝ\ØYÙT˜]S[Z]Ô\X[\]P›ÙQ^S[Z]Z[ˆHÂ™^ÜÛÛœÝ\ØYÙT˜]S[Z]Ô\X[\]P›ÙQ^S[Z]X^HŒMÍÍÎÂ‚™^ÜÛÛœÝ\ØYÙT˜]S[Z]Ô\X[\]P›ÙS[Û[Z]Z[ˆHÂ™^ÜÛÛœÝ\ØYÙT˜]S[Z]Ô\X[\]P›ÙS[Û[Z]X^HŒMÍÍÎÂ‚‚‚™^ÜÛÛœÝ\ØYÙT˜]S[Z]Ô\X[\]P›ÙHH›Ù›Øš™XÝ
Âˆ˜\WØØ[Ý\HŽˆ›Ù›[X™\Š
Kˆ›Ü™Ø[š^˜][ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›Z[]WÛ[Z]Žˆ›Ù›[X™\Š
K›Z[Š\ØYÙT˜]S[Z]Ô\X[\]P›ÙSZ[]S[Z]Z[ŠK›X^
\ØYÙT˜]S[Z]Ô\X[\]P›ÙSZ[]S[Z]X^
K›Ü[Û˜[

K™\ØÜšX™J	ÓX^Ø[È\ˆZ[]IÊKˆšÝ\—Û[Z]Žˆ›Ù›[X™\Š
K›Z[Š\ØYÙT˜]S[Z]Ô\X[\]P›ÙRÝ\“[Z]Z[ŠK›X^
\ØYÙT˜]S[Z]Ô\X[\]P›ÙRÝ\“[Z]X^
K›Ü[Û˜[

K™\ØÜšX™J	ÓX^Ø[È\ˆÝ\‰ÊKˆ™^WÛ[Z]Žˆ›Ù›[X™\Š
K›Z[Š\ØYÙT˜]S[Z]Ô\X[\]P›ÙQ^S[Z]Z[ŠK›X^
\ØYÙT˜]S[Z]Ô\X[\]P›ÙQ^S[Z]X^
K›Ü[Û˜[

K™\ØÜšX™J	ÓX^Ø[È\ˆ^IÊKˆ›[ÛÛ[Z]Žˆ›Ù›[X™\Š
K›Z[Š\ØYÙT˜]S[Z]Ô\X[\]P›ÙS[Û[Z]Z[ŠK›X^
\ØYÙT˜]S[Z]Ô\X[\]P›ÙS[Û[Z]X^
K›Ü[Û˜[

K™\ØÜšX™J	ÓX^Ø[È\ˆ[Û	ÊKˆœÝXœØÜš\[Û—ÝY\ˆŽˆ›Ù›[X™\Š
BŸJB‚™^ÜÛÛœÝ\ØYÙT˜]S[Z]Ô\X[\]T™\ÜÛœÙT™\Ý[Z[]S[Z]Z[ˆHÂ™^ÜÛÛœÝ\ØYÙT˜]S[Z]Ô\X[\]T™\ÜÛœÙT™\Ý[Z[]S[Z]X^HŒMÍÍÎÂ‚™^ÜÛÛœÝ\ØYÙT˜]S[Z]Ô\X[\]T™\ÜÛœÙT™\Ý[Ý\“[Z]Z[ˆHÂ™^ÜÛÛœÝ\ØYÙT˜]S[Z]Ô\X[\]T™\ÜÛœÙT™\Ý[Ý\“[Z]X^HŒMÍÍÎÂ‚™^ÜÛÛœÝ\ØYÙT˜]S[Z]Ô\X[\]T™\ÜÛœÙT™\Ý[^S[Z]Z[ˆHÂ™^ÜÛÛœÝ\ØYÙT˜]S[Z]Ô\X[\]T™\ÜÛœÙT™\Ý[^S[Z]X^HŒMÍÍÎÂ‚™^ÜÛÛœÝ\ØYÙT˜]S[Z]Ô\X[\]T™\ÜÛœÙT™\Ý[[Û[Z]Z[ˆHÂ™^ÜÛÛœÝ\ØYÙT˜]S[Z]Ô\X[\]T™\ÜÛœÙT™\Ý[[Û[Z]X^HŒMÍÍÎÂ‚‚‚™^ÜÛÛœÝ\ØYÙT˜]S[Z]Ô\X[\]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ˜\WØØ[Ý\HŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ›Ü™Ø[š^˜][ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›Z[]WÛ[Z]Žˆ›Ù›[X™\Š
K›Z[Š\ØYÙT˜]S[Z]Ô\X[\]T™\ÜÛœÙT™\Ý[Z[]S[Z]Z[ŠK›X^
\ØYÙT˜]S[Z]Ô\X[\]T™\ÜÛœÙT™\Ý[Z[]S[Z]X^
K›Ü[Û˜[

K™\ØÜšX™J	ÓX^Ø[È\ˆZ[]IÊKˆšÝ\—Û[Z]Žˆ›Ù›[X™\Š
K›Z[Š\ØYÙT˜]S[Z]Ô\X[\]T™\ÜÛœÙT™\Ý[Ý\“[Z]Z[ŠK›X^
\ØYÙT˜]S[Z]Ô\X[\]T™\ÜÛœÙT™\Ý[Ý\“[Z]X^
K›Ü[Û˜[

K™\ØÜšX™J	ÓX^Ø[È\ˆÝ\‰ÊKˆ™^WÛ[Z]Žˆ›Ù›[X™\Š
K›Z[Š\ØYÙT˜]S[Z]Ô\X[\]T™\ÜÛœÙT™\Ý[^S[Z]Z[ŠK›X^
\ØYÙT˜]S[Z]Ô\X[\]T™\ÜÛœÙT™\Ý[^S[Z]X^
K›Ü[Û˜[

K™\ØÜšX™J	ÓX^Ø[È\ˆ^IÊKˆ›[ÛÛ[Z]Žˆ›Ù›[X™\Š
K›Z[Š\ØYÙT˜]S[Z]Ô\X[\]T™\ÜÛœÙT™\Ý[[Û[Z]Z[ŠK›X^
\ØYÙT˜]S[Z]Ô\X[\]T™\ÜÛœÙT™\Ý[[Û[Z]X^
K›Ü[Û˜[

K™\ØÜšX™J	ÓX^Ø[È\ˆ[Û	ÊKˆœÝXœØÜš\[Û—ÝY\ˆŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

BŸJBŸJB‚‚™^ÜÛÛœÝ\ØYÙT˜]S[Z]Ñ[]T\˜[\ÈH›Ù›Øš™XÝ
Âˆœ˜]WÛ[Z]ÚYŽˆ›ÙœÝš[™Ê
BŸJB‚‚‚‚™^ÜÛÛœÝ\ØYÙT˜]S[Z]Ñ[]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJB‚‚™^ÜÛÛœÝ\ØYÙT˜]S[Z]Ô™XY\˜[\ÈH›Ù›Øš™XÝ
Âˆœ˜]WÛ[Z]ÚYŽˆ›ÙœÝš[™Ê
BŸJB‚™^ÜÛÛœÝ\ØYÙT˜]S[Z]Ô™XY™\ÜÛœÙT™\Ý[][SZ[]S[Z]Z[ˆHÂ™^ÜÛÛœÝ\ØYÙT˜]S[Z]Ô™XY™\ÜÛœÙT™\Ý[][SZ[]S[Z]X^HŒMÍÍÎÂ‚™^ÜÛÛœÝ\ØYÙT˜]S[Z]Ô™XY™\ÜÛœÙT™\Ý[][RÝ\“[Z]Z[ˆHÂ™^ÜÛÛœÝ\ØYÙT˜]S[Z]Ô™XY™\ÜÛœÙT™\Ý[][RÝ\“[Z]X^HŒMÍÍÎÂ‚™^ÜÛÛœÝ\ØYÙT˜]S[Z]Ô™XY™\ÜÛœÙT™\Ý[][Q^S[Z]Z[ˆHÂ™^ÜÛÛœÝ\ØYÙT˜]S[Z]Ô™XY™\ÜÛœÙT™\Ý[][Q^S[Z]X^HŒMÍÍÎÂ‚™^ÜÛÛœÝ\ØYÙT˜]S[Z]Ô™XY™\ÜÛœÙT™\Ý[][S[Û[Z]Z[ˆHÂ™^ÜÛÛœÝ\ØYÙT˜]S[Z]Ô™XY™\ÜÛœÙT™\Ý[][S[Û[Z]X^HŒMÍÍÎÂ‚‚‚™^ÜÛÛœÝ\ØYÙT˜]S[Z]Ô™XY™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ˜\WØØ[Ý\HŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ›Ü™Ø[š^˜][ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆ›Z[]WÛ[Z]Žˆ›Ù›[X™\Š
K›Z[Š\ØYÙT˜]S[Z]Ô™XY™\ÜÛœÙT™\Ý[][SZ[]S[Z]Z[ŠK›X^
\ØYÙT˜]S[Z]Ô™XY™\ÜÛœÙT™\Ý[][SZ[]S[Z]X^
K›Ü[Û˜[

K™\ØÜšX™J	ÓX^Ø[È\ˆZ[]IÊKˆšÝ\—Û[Z]Žˆ›Ù›[X™\Š
K›Z[Š\ØYÙT˜]S[Z]Ô™XY™\ÜÛœÙT™\Ý[][RÝ\“[Z]Z[ŠK›X^
\ØYÙT˜]S[Z]Ô™XY™\ÜÛœÙT™\Ý[][RÝ\“[Z]X^
K›Ü[Û˜[

K™\ØÜšX™J	ÓX^Ø[È\ˆÝ\‰ÊKˆ™^WÛ[Z]Žˆ›Ù›[X™\Š
K›Z[Š\ØYÙT˜]S[Z]Ô™XY™\ÜÛœÙT™\Ý[][Q^S[Z]Z[ŠK›X^
\ØYÙT˜]S[Z]Ô™XY™\ÜÛœÙT™\Ý[][Q^S[Z]X^
K›Ü[Û˜[

K™\ØÜšX™J	ÓX^Ø[È\ˆ^IÊKˆ›[ÛÛ[Z]Žˆ›Ù›[X™\Š
K›Z[Š\ØYÙT˜]S[Z]Ô™XY™\ÜÛœÙT™\Ý[][S[Û[Z]Z[ŠK›X^
\ØYÙT˜]S[Z]Ô™XY™\ÜÛœÙT™\Ý[][S[Û[Z]X^
K›Ü[Û˜[

K™\ØÜšX™J	ÓX^Ø[È\ˆ[Û	ÊKˆœÝXœØÜš\[Û—ÝY\ˆŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

BŸJJBŸJB‚‚™^ÜÛÛœÝ\ØYÙT™\ÛÝ\˜ÙS[Z]Ó\Ý™\ÜÛœÙT™\Ý[][S[Z]Z[ˆHÂ™^ÜÛÛœÝ\ØYÙT™\ÛÝ\˜ÙS[Z]Ó\Ý™\ÜÛœÙT™\Ý[][S[Z]X^HŒMÍÍÎÂ‚‚‚™^ÜÛÛœÝ\ØYÙT™\ÛÝ\˜ÙS[Z]Ó\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆœ™\ÛÝ\˜ÙWÝ\HŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆœÝXœØÜš\[Û—ÝY\ˆŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ›[Z]Žˆ›Ù›[X™\Š
K›Z[Š\ØYÙT™\ÛÝ\˜ÙS[Z]Ó\Ý™\ÜÛœÙT™\Ý[][S[Z]Z[ŠK›X^
\ØYÙT™\ÛÝ\˜ÙS[Z]Ó\Ý™\ÜÛœÙT™\Ý[][S[Z]X^
K™\ØÜšX™J	Ó[Z]›ÜˆH™\ÛÝ\˜ÙIÊKˆ›Ü™Ø[š^˜][ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJJBŸJB‚‚™^ÜÛÛœÝ\ØYÙT™\ÛÝ\˜ÙS[Z]ÐÜ™X]T\˜[\ÈH›Ù›Øš™XÝ
Âˆœ™\ÛÝ\˜ÙWÛ[Z]ÚYŽˆ›ÙœÝš[™Ê
BŸJB‚™^ÜÛÛœÝ\ØYÙT™\ÛÝ\˜ÙS[Z]ÐÜ™X]P›ÙS[Z]Z[ˆHÂ™^ÜÛÛœÝ\ØYÙT™\ÛÝ\˜ÙS[Z]ÐÜ™X]P›ÙS[Z]X^HŒMÍÍÎÂ‚‚‚™^ÜÛÛœÝ\ØYÙT™\ÛÝ\˜ÙS[Z]ÐÜ™X]P›ÙHH›Ù›Øš™XÝ
Âˆœ™\ÛÝ\˜ÙWÝ\HŽˆ›Ù›[X™\Š
KˆœÝXœØÜš\[Û—ÝY\ˆŽˆ›Ù›[X™\Š
Kˆ›[Z]Žˆ›Ù›[X™\Š
K›Z[Š\ØYÙT™\ÛÝ\˜ÙS[Z]ÐÜ™X]P›ÙS[Z]Z[ŠK›X^
\ØYÙT™\ÛÝ\˜ÙS[Z]ÐÜ™X]P›ÙS[Z]X^
K™\ØÜšX™J	Ó[Z]›ÜˆH™\ÛÝ\˜ÙIÊKˆ›Ü™Ø[š^˜][ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJB‚™^ÜÛÛœÝ\ØYÙT™\ÛÝ\˜ÙS[Z]ÐÜ™X]T™\ÜÛœÙT™\Ý[[Z]Z[ˆHÂ™^ÜÛÛœÝ\ØYÙT™\ÛÝ\˜ÙS[Z]ÐÜ™X]T™\ÜÛœÙT™\Ý[[Z]X^HŒMÍÍÎÂ‚‚‚™^ÜÛÛœÝ\ØYÙT™\ÛÝ\˜ÙS[Z]ÐÜ™X]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆœ™\ÛÝ\˜ÙWÝ\HŽˆ›Ù›[X™\Š
KˆœÝXœØÜš\[Û—ÝY\ˆŽˆ›Ù›[X™\Š
Kˆ›[Z]Žˆ›Ù›[X™\Š
K›Z[Š\ØYÙT™\ÛÝ\˜ÙS[Z]ÐÜ™X]T™\ÜÛœÙT™\Ý[[Z]Z[ŠK›X^
\ØYÙT™\ÛÝ\˜ÙS[Z]ÐÜ™X]T™\ÜÛœÙT™\Ý[[Z]X^
K™\ØÜšX™J	Ó[Z]›ÜˆH™\ÛÝ\˜ÙIÊKˆ›Ü™Ø[š^˜][ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJBŸJB‚‚™^ÜÛÛœÝ\ØYÙT™\ÛÝ\˜ÙS[Z]Ô\X[\]T\˜[\ÈH›Ù›Øš™XÝ
Âˆœ™\ÛÝ\˜ÙWÛ[Z]ÚYŽˆ›ÙœÝš[™Ê
BŸJB‚™^ÜÛÛœÝ\ØYÙT™\ÛÝ\˜ÙS[Z]Ô\X[\]P›ÙS[Z]Z[ˆHÂ™^ÜÛÛœÝ\ØYÙT™\ÛÝ\˜ÙS[Z]Ô\X[\]P›ÙS[Z]X^HŒMÍÍÎÂ‚‚‚™^ÜÛÛœÝ\ØYÙT™\ÛÝ\˜ÙS[Z]Ô\X[\]P›ÙHH›Ù›Øš™XÝ
Âˆœ™\ÛÝ\˜ÙWÝ\HŽˆ›Ù›[X™\Š
KˆœÝXœØÜš\[Û—ÝY\ˆŽˆ›Ù›[X™\Š
Kˆ›[Z]Žˆ›Ù›[X™\Š
K›Z[Š\ØYÙT™\ÛÝ\˜ÙS[Z]Ô\X[\]P›ÙS[Z]Z[ŠK›X^
\ØYÙT™\ÛÝ\˜ÙS[Z]Ô\X[\]P›ÙS[Z]X^
K™\ØÜšX™J	Ó[Z]›ÜˆH™\ÛÝ\˜ÙIÊKˆ›Ü™Ø[š^˜][ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJB‚™^ÜÛÛœÝ\ØYÙT™\ÛÝ\˜ÙS[Z]Ô\X[\]T™\ÜÛœÙT™\Ý[[Z]Z[ˆHÂ™^ÜÛÛœÝ\ØYÙT™\ÛÝ\˜ÙS[Z]Ô\X[\]T™\ÜÛœÙT™\Ý[[Z]X^HŒMÍÍÎÂ‚‚‚™^ÜÛÛœÝ\ØYÙT™\ÛÝ\˜ÙS[Z]Ô\X[\]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆœ™\ÛÝ\˜ÙWÝ\HŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆœÝXœØÜš\[Û—ÝY\ˆŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ›[Z]Žˆ›Ù›[X™\Š
K›Z[Š\ØYÙT™\ÛÝ\˜ÙS[Z]Ô\X[\]T™\ÜÛœÙT™\Ý[[Z]Z[ŠK›X^
\ØYÙT™\ÛÝ\˜ÙS[Z]Ô\X[\]T™\ÜÛœÙT™\Ý[[Z]X^
K™\ØÜšX™J	Ó[Z]›ÜˆH™\ÛÝ\˜ÙIÊKˆ›Ü™Ø[š^˜][ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJBŸJB‚‚™^ÜÛÛœÝ\ØYÙT™\ÛÝ\˜ÙS[Z]Ñ[]T\˜[\ÈH›Ù›Øš™XÝ
Âˆœ™\ÛÝ\˜ÙWÛ[Z]ÚYŽˆ›ÙœÝš[™Ê
BŸJB‚‚‚‚™^ÜÛÛœÝ\ØYÙT™\ÛÝ\˜ÙS[Z]Ñ[]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJB‚‚™^ÜÛÛœÝ\ØYÙT™\ÛÝ\˜ÙS[Z]Ô™XY\˜[\ÈH›Ù›Øš™XÝ
Âˆœ™\ÛÝ\˜ÙWÛ[Z]ÚYŽˆ›ÙœÝš[™Ê
BŸJB‚™^ÜÛÛœÝ\ØYÙT™\ÛÝ\˜ÙS[Z]Ô™XY™\ÜÛœÙT™\Ý[][S[Z]Z[ˆHÂ™^ÜÛÛœÝ\ØYÙT™\ÛÝ\˜ÙS[Z]Ô™XY™\ÜÛœÙT™\Ý[][S[Z]X^HŒMÍÍÎÂ‚‚‚™^ÜÛÛœÝ\ØYÙT™\ÛÝ\˜ÙS[Z]Ô™XY™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆœ™\ÛÝ\˜ÙWÝ\HŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆœÝXœØÜš\[Û—ÝY\ˆŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ›[Z]Žˆ›Ù›[X™\Š
K›Z[Š\ØYÙT™\ÛÝ\˜ÙS[Z]Ô™XY™\ÜÛœÙT™\Ý[][S[Z]Z[ŠK›X^
\ØYÙT™\ÛÝ\˜ÙS[Z]Ô™XY™\ÜÛœÙT™\Ý[][S[Z]X^
K™\ØÜšX™J	Ó[Z]›ÜˆH™\ÛÝ\˜ÙIÊKˆ›Ü™Ø[š^˜][ÛˆŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJJBŸJB‚‚™^ÜÛÛœÝ\ØYÙT™\ÛÝ\˜ÙU\S\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ›˜[YHŽˆ›Ù™[[JÉÜ›Ú™XÝ	Ë	Ù]\Ù]	Ë	ÛÙÜÉË	Ü›ÝÜÉË	ØÛÛ[[œÉË	Ý\Ù\œÉË	Ý˜XÙ\ÉË	ÛØœÙ\™IË	Ü›ÝÝ\\ÉË	ÚÛ›ÝÛYÙWØ˜\ÙI×JKˆ™\ØÜš\[ÛˆŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

BŸJJBŸJB‚‚‚‚‚™^ÜÛÛœÝ\ØYÙTÝXœØÜš\[Û”[œÓ\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù™[[JÉÜÝXØÙ\ÜÉË	Ù\œ›Ü‰×JKˆ™]HŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ˜Ý\œ™[ÜÝXœØÜš\[ÛˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ›Y\ÜØYÙHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

BŸJBŸJB‚‚‚‚‚‚‚™^ÜÛÛœÝ\ØYÙTÝXœØÜš\[Û”Ý]\Ó\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆ›™^Ü™[™]Ø[Ù]HŽˆ›ÙœÝš[™Ê
K™]J
K›Ü[Û˜[

KˆœÝXœØÜš\[Û—ÜÝ]\ÈŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

KˆY\ˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

KˆœÝXœØÜš\[Û—ÜšXÙHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆœÝXœØÜš\[Û—Ù]\™WÝY\ˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

KˆœÝXœØÜš\[Û—Ù]\™WÜÝ\Ù]HŽˆ›ÙœÝš[™Ê
K™]J
K›Ü[Û˜[

KˆœÝXœØÜš\[Û—Ù]\™WÜšXÙHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

BŸJBŸJB‚‚™^ÜÛÛœÝ\ØYÙTÝXœØÜš\[Û•Y\“\Ý™\ÜÛœÙT™\Ý[][TÝš\TšXÙRYX^HLÂ‚‚‚™^ÜÛÛœÝ\ØYÙTÝXœØÜš\[Û•Y\“\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ›˜[YHŽˆ›Ù™[[JÉÙœ™YIË	Ø˜\ÚXÉË	Ø˜\ÚX×ÞYX\›IË	ØÝ\ÝÛI×JK›Ü[Û˜[

Kˆ™\ØÜš\[ÛˆŽˆ›ÙœÝš[™Ê
KˆœÝš\WÜšXÙWÚYŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙTÝXœØÜš\[Û•Y\“\Ý™\ÜÛœÙT™\Ý[][TÝš\TšXÙRYX^
K›Ü[Û˜[

KˆØ[]Ü™Yš[Ø[[Ý[Žˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	Ð[[Ý[È™Yš[HØ[]]™\žH[Û‰ÊBŸJJBŸJB‚‚™^ÜÛÛœÝ\ØYÙTÝXœØÜš\[Û•Y\Ü™X]T\˜[\ÈH›Ù›Øš™XÝ
ÂˆœÝXœØÜš\[Û—ÚYŽˆ›ÙœÝš[™Ê
BŸJB‚™^ÜÛÛœÝ\ØYÙTÝXœØÜš\[Û•Y\Ü™X]P›ÙTÝš\TšXÙRYX^HLÂ‚‚‚™^ÜÛÛœÝ\ØYÙTÝXœØÜš\[Û•Y\Ü™X]P›ÙHH›Ù›Øš™XÝ
Âˆ™\ØÜš\[ÛˆŽˆ›ÙœÝš[™Ê
KˆœÝš\WÜšXÙWÚYŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙTÝXœØÜš\[Û•Y\Ü™X]P›ÙTÝš\TšXÙRYX^
K›Ü[Û˜[

KˆØ[]Ü™Yš[Ø[[Ý[Žˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	Ð[[Ý[È™Yš[HØ[]]™\žH[Û‰ÊBŸJB‚™^ÜÛÛœÝ\ØYÙTÝXœØÜš\[Û•Y\Ü™X]T™\ÜÛœÙT™\Ý[Ýš\TšXÙRYX^HLÂ‚‚‚™^ÜÛÛœÝ\ØYÙTÝXœØÜš\[Û•Y\Ü™X]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ›˜[YHŽˆ›Ù™[[JÉÙœ™YIË	Ø˜\ÚXÉË	Ø˜\ÚX×ÞYX\›IË	ØÝ\ÝÛI×JK›Ü[Û˜[

Kˆ™\ØÜš\[ÛˆŽˆ›ÙœÝš[™Ê
KˆœÝš\WÜšXÙWÚYŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙTÝXœØÜš\[Û•Y\Ü™X]T™\ÜÛœÙT™\Ý[Ýš\TšXÙRYX^
K›Ü[Û˜[

KˆØ[]Ü™Yš[Ø[[Ý[Žˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	Ð[[Ý[È™Yš[HØ[]]™\žH[Û‰ÊBŸJBŸJB‚‚™^ÜÛÛœÝ\ØYÙTÝXœØÜš\[Û•Y\”\X[\]T\˜[\ÈH›Ù›Øš™XÝ
ÂˆœÝXœØÜš\[Û—ÚYŽˆ›ÙœÝš[™Ê
BŸJB‚™^ÜÛÛœÝ\ØYÙTÝXœØÜš\[Û•Y\”\X[\]P›ÙTÝš\TšXÙRYX^HLÂ‚‚‚™^ÜÛÛœÝ\ØYÙTÝXœØÜš\[Û•Y\”\X[\]P›ÙHH›Ù›Øš™XÝ
Âˆ™\ØÜš\[ÛˆŽˆ›ÙœÝš[™Ê
KˆœÝš\WÜšXÙWÚYŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙTÝXœØÜš\[Û•Y\”\X[\]P›ÙTÝš\TšXÙRYX^
K›Ü[Û˜[

KˆØ[]Ü™Yš[Ø[[Ý[Žˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	Ð[[Ý[È™Yš[HØ[]]™\žH[Û‰ÊBŸJB‚™^ÜÛÛœÝ\ØYÙTÝXœØÜš\[Û•Y\”\X[\]T™\ÜÛœÙT™\Ý[Ýš\TšXÙRYX^HLÂ‚‚‚™^ÜÛÛœÝ\ØYÙTÝXœØÜš\[Û•Y\”\X[\]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ›˜[YHŽˆ›Ù™[[JÉÙœ™YIË	Ø˜\ÚXÉË	Ø˜\ÚX×ÞYX\›IË	ØÝ\ÝÛI×JK›Ü[Û˜[

Kˆ™\ØÜš\[ÛˆŽˆ›ÙœÝš[™Ê
KˆœÝš\WÜšXÙWÚYŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙTÝXœØÜš\[Û•Y\”\X[\]T™\ÜÛœÙT™\Ý[Ýš\TšXÙRYX^
K›Ü[Û˜[

KˆØ[]Ü™Yš[Ø[[Ý[Žˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	Ð[[Ý[È™Yš[HØ[]]™\žH[Û‰ÊBŸJBŸJB‚‚™^ÜÛÛœÝ\ØYÙTÝXœØÜš\[Û•Y\‘[]T\˜[\ÈH›Ù›Øš™XÝ
ÂˆœÝXœØÜš\[Û—ÚYŽˆ›ÙœÝš[™Ê
BŸJB‚‚‚‚™^ÜÛÛœÝ\ØYÙTÝXœØÜš\[Û•Y\‘[]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJB‚‚™^ÜÛÛœÝ\ØYÙTÝXœØÜš\[Û•Y\”™XY\˜[\ÈH›Ù›Øš™XÝ
ÂˆœÝXœØÜš\[Û—ÚYŽˆ›ÙœÝš[™Ê
BŸJB‚™^ÜÛÛœÝ\ØYÙTÝXœØÜš\[Û•Y\”™XY™\ÜÛœÙT™\Ý[][TÝš\TšXÙRYX^HLÂ‚‚‚™^ÜÛÛœÝ\ØYÙTÝXœØÜš\[Û•Y\”™XY™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ›˜[YHŽˆ›Ù™[[JÉÙœ™YIË	Ø˜\ÚXÉË	Ø˜\ÚX×ÞYX\›IË	ØÝ\ÝÛI×JK›Ü[Û˜[

Kˆ™\ØÜš\[ÛˆŽˆ›ÙœÝš[™Ê
KˆœÝš\WÜšXÙWÚYŽˆ›ÙœÝš[™Ê
K›X^
\ØYÙTÝXœØÜš\[Û•Y\”™XY™\ÜÛœÙT™\Ý[][TÝš\TšXÙRYX^
K›Ü[Û˜[

KˆØ[]Ü™Yš[Ø[[Ý[Žˆ›ÙœÝš[™Ê
K›Ü[Û˜[

K™\ØÜšX™J	Ð[[Ý[È™Yš[HØ[]]™\žH[Û‰ÊBŸJJBŸJB‚‚™^ÜÛÛœÝ\ØYÙU\]P]]Ô™[ØYÙ][™ÜÐÜ™X]P›ÙHH›Ù›Øš™XÝ
Âˆ˜]]Ü™[ØYÙ[˜X›YŽˆ›Ù˜›ÛÛX[Š
Kˆ˜]]Ü™[ØYÝØ[][[Ý[Žˆ›ÙœÝš[™Ê
Kˆ˜]]Ü™[ØYÝØ[]™\ÚÛŽˆ›ÙœÝš[™Ê
BŸJB‚‚‚‚™^ÜÛÛœÝ\ØYÙU\]P]]Ô™[ØYÙ][™ÜÐÜ™X]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù™[[JÉÜÝXØÙ\ÜÉ×JKˆ›Y\ÜØYÙHŽˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJB‚‚™^ÜÛÛœÝ\ØYÙU\]Pš[[™Ñ]Z[ÐÜ™X]P›ÙHH›Ù›Øš™XÝ
Âˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ™[XZ[Žˆ›ÙœÝš[™Ê
K™[XZ[

K›Ü[Û˜[

Kˆ˜ÛÛ\[žHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ˜š[[™×ØY™\ÜÌHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ˜š[[™×ØY™\ÜÌˆŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ˜Ú]HŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆœÝ]HŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ˜ÛÝ[žHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆœÜÝ[ØÛÙHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ^ÚYŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

BŸJB‚‚‚‚™^ÜÛÛœÝ\ØYÙU\]Pš[[™Ñ]Z[ÐÜ™X]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆ›Y\ÜØYÙHŽˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJBŸJB‚‚™^ÜÛÛœÝ\ØYÙU\ØYÙTÝ[[X\žS\Ý]Y\žS[ÛX^HLŽÂ‚‚‚™^ÜÛÛœÝ\ØYÙU\ØYÙTÝ[[X\žS\Ý]Y\žT\˜[\ÈH›Ù›Øš™XÝ
Âˆ›[ÛŽˆ›Ù›[X™\Š
K›Z[ŠJK›X^
\ØYÙU\ØYÙTÝ[[X\žS\Ý]Y\žS[ÛX^
K›Ü[Û˜[

KˆžYX\ˆŽˆ›Ù›[X™\Š
K›Ü[Û˜[

BŸJB‚™^ÜÛÛœÝ\ØYÙU\ØYÙTÝ[[X\žS\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ùœ™XÛÜ™
›ÙœÝš[™Ê
K›ÙœÝš[™Ê
JBŸJB‚‚‹ÊŠ‚ˆ
ˆYÜˆ™[[Ý™H[ˆY[ÛˆÝXœØÜš\[Û‹‚ˆ
‹Â™^ÜÛÛœÝ\ØYÙUŒYYÛÜ™X]P›ÙHH›Ù›Øš™XÝ
Âˆœ[ˆŽˆ›Ù™[[JÉØ›ÛÜÝ	Ë	ÜØØ[IË	Ù[\œš\ÙI×JK›Ü[Û˜[

BŸJB‚‚‚‚‚™^ÜÛÛœÝ\ØYÙUŒYYÛÜ™X]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
ÂˆœÝXœØÜš\[Û—ÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœ[ˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆ™Z[œÝ]HH™]š[Ý\ÛK\ØÚY[YY[ÛˆØ[˜Ù[][Û‹‚ˆ
‹Â™^ÜÛÛœÝ\ØYÙUŒYYÛ•\]P›ÙHH›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

B‚‚‚‚™^ÜÛÛœÝ\ØYÙUŒYYÛ•\]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆ›Y\ÜØYÙHŽˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆYÜˆ™[[Ý™H[ˆY[ÛˆÝXœØÜš\[Û‹‚ˆ
‹Â‚‚‚™^ÜÛÛœÝ\ØYÙUŒYYÛ‘[]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆ›Y\ÜØYÙHŽˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆYÜˆ™[[Ý™H[ˆY[ÛˆÝXœØÜš\[Û‹‚ˆ
‹Â™^ÜÛÛœÝ\ØYÙUŒYÛÜ™X]P›ÙHH›Ù›Øš™XÝ
Âˆœ[ˆŽˆ›Ù™[[JÉØ›ÛÜÝ	Ë	ÜØØ[IË	Ù[\œš\ÙI×JK›Ü[Û˜[

BŸJB‚‚‚‚‚™^ÜÛÛœÝ\ØYÙUŒYÛÜ™X]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
ÂˆœÝXœØÜš\[Û—ÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœ[ˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆ™Z[œÝ]HH™]š[Ý\ÛK\ØÚY[YY[ÛˆØ[˜Ù[][Û‹‚ˆ
‹Â™^ÜÛÛœÝ\ØYÙUŒYÛ•\]P›ÙHH›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

B‚‚‚‚™^ÜÛÛœÝ\ØYÙUŒYÛ•\]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆ›Y\ÜØYÙHŽˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆYÜˆ™[[Ý™H[ˆY[ÛˆÝXœØÜš\[Û‹‚ˆ
‹Â‚‚‚™^ÜÛÛœÝ\ØYÙUŒYÛ‘[]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆ›Y\ÜØYÙHŽˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆ™]\›œÈ]›Ü›H™YH
È\‹Y[Y[œÚ[ÛˆÛÜÝÈ
ÈÜ™Y]È
ÈÝ[‚ˆ
ˆÝ[[X\žHÙ]Ý\œ™[š[[™È\š[ÙÛÜÝœ™XZÙÝÛˆ›ÜˆHš[[™ÈYÙK‚ˆ
‹Â‚‚‚‚‚‚‚‚‚™^ÜÛÛœÝ\ØYÙUŒš[[™ÓÝ™\šY]Ó\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆ›Ü™×ÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

Kˆœ\š[ÙŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆœ[ˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆœ]›Ü›WÙ™YHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ\ØYÙWÝÝ[Žˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ˜Ü™Y]×Ø\YYŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆœÝXÝ[Žˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ^Žˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆÝ[Žˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ›[™WÚ][\ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
Âˆ›[™WÝ\HŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ™[Y[œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ™\ØÜš\[ÛˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœ]X[]HŽˆ›ÙœÝš[™Ê
Kˆ[š]Žˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ[š]ÜšXÙHŽˆ›ÙœÝš[™Ê
Kˆ˜[[Ý[Žˆ›ÙœÝš[™Ê
KˆY\—Øœ™XZÙÝÛˆŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ˜Ü™Y]ÚYŽˆ›Ù›[X™\Š
K›Ü[Û˜[

BŸJJK›Ü[Û˜[

Kˆ™\œ›ÜˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆœ[™[™×ØØ[˜Ù[Žˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ˜Ø[˜Ù[Ø]Žˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

BŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆ\ÝÜˆÜ™X]H\ØYÙHYÙ]Ë‚ˆ
‹Â‚‚‚‚‚‚‚™^ÜÛÛœÝ\ØYÙUŒYÙ]Ó\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆ˜YÙ]ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›Ù›[X™\Š
Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœØÛÜHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ™\ÚÛÝ˜[YHŽˆ›ÙœÝš[™Ê
Kˆ˜XÝ[ÛˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ››ÝYžWÙ[XZ[ÈŽˆ›Ù˜\œ˜^J›ÙœÝš[™Ê
K™[XZ[

K›Z[ŠJJK›Ü[Û˜[

Kˆš\×ØXÝ]™HŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

Kˆ›\ÝÝšYÙÙ\™YÜ\š[ÙŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ›\ÝÝšYÙÙ\™YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JK›Ü[Û˜[

BŸJJBŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆ\ÝÜˆÜ™X]H\ØYÙHYÙ]Ë‚ˆ
‹Â‚‚‚‚‚™^ÜÛÛœÝ\ØYÙUŒYÙ]ÐÜ™X]P›ÙHH›Ù›Øš™XÝ
Âˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

KˆœØÛÜHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ™\ÚÛÝ˜[YHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ˜XÝ[ÛˆŽˆ›Ù™[[JÉÛ›ÝYžIË	ÝØ\›‰Ë	Ü]\ÙI×JK›Ü[Û˜[

Kˆ››ÝYžWÙ[XZ[ÈŽˆ›Ù˜\œ˜^J›ÙœÝš[™Ê
K™[XZ[

K›Z[ŠJJK›Ü[Û˜[

Kˆ››ÝYžWÜÛXÚ×ÝÙXšÛÚÈŽˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆš\×ØXÝ]™HŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

BŸJB‚‚‚‚‚‚‚™^ÜÛÛœÝ\ØYÙUŒYÙ]ÐÜ™X]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›Ù›[X™\Š
Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœØÛÜHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ™\ÚÛÝ˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ˜XÝ[ÛˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆš\×ØXÝ]™HŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

BŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆ\]HÜˆ[]HHYÙ]‚ˆ
‹Â™^ÜÛÛœÝ\ØYÙUŒYÙ]Õ\]T\˜[\ÈH›Ù›Øš™XÝ
Âˆ˜YÙ]ÚYŽˆ›ÙœÝš[™Ê
BŸJB‚‚‚‚‚‚™^ÜÛÛœÝ\ØYÙUŒYÙ]Õ\]P›ÙHH›Ù›Øš™XÝ
Âˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

KˆœØÛÜHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ™\ÚÛÝ˜[YHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ˜XÝ[ÛˆŽˆ›Ù™[[JÉÛ›ÝYžIË	ÝØ\›‰Ë	Ü]\ÙI×JK›Ü[Û˜[

Kˆ››ÝYžWÙ[XZ[ÈŽˆ›Ù˜\œ˜^J›ÙœÝš[™Ê
K™[XZ[

K›Z[ŠJJK›Ü[Û˜[

Kˆ››ÝYžWÜÛXÚ×ÝÙXšÛÚÈŽˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆš\×ØXÝ]™HŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

BŸJB‚‚‚‚‚‚‚™^ÜÛÛœÝ\ØYÙUŒYÙ]Õ\]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›Ù›[X™\Š
Kˆ›˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœØÛÜHŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ™\ÚÛÝ˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ˜XÝ[ÛˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆš\×ØXÝ]™HŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

BŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆ\]HÜˆ[]HHYÙ]‚ˆ
‹Â™^ÜÛÛœÝ\ØYÙUŒYÙ]Ñ[]T\˜[\ÈH›Ù›Øš™XÝ
Âˆ˜YÙ]ÚYŽˆ›ÙœÝš[™Ê
BŸJB‚™^ÜÛÛœÝ\ØYÙUŒYÙ]Ñ[]P›ÙHH›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

B‚™^ÜÛÛœÝ\ØYÙUŒYÙ]Ñ[]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆ™[]YŽˆ›Ù˜›ÛÛX[Š
BŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆØ[˜Ù[È[žHXÝ]™HÝš\HÝXœØÜš\[Ûˆ[™™\Ù]È[ˆÈœ™YK‚’Yˆ\Ù\ˆ\È[ˆY[Û‹]]\Ý™H™[[Ý™Yš\œÝ‚ˆ
ˆÝ[[X\žHÝÛ™Ü˜YHœ›ÛHVQÈÈœ™YK‚ˆ
‹Â™^ÜÛÛœÝ\ØYÙUŒ‘ÝÛ™Ü˜YUÑœ™YPÜ™X]P›ÙHH›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

B‚‚‚‚™^ÜÛÛœÝ\ØYÙUŒ‘ÝÛ™Ü˜YUÑœ™YPÜ™X]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆœ[ˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆÙ][›ÚXÙH\ÝÜžH›ÜˆHš[[™ÈYÙK‚ˆ
‹Â‚‚‚‚™^ÜÛÛœÝ\ØYÙUŒ’[›ÚXÙ\Ó\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆš[›ÚXÙ\ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ\š[ÙÜÝ\Žˆ›ÙœÝš[™Ê
K™]J
Kˆœ\š[ÙÙ[™Žˆ›ÙœÝš[™Ê
K™]J
Kˆœ[ˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœ]›Ü›WÙ™YHŽˆ›ÙœÝš[™Ê
Kˆ\ØYÙWÝÝ[Žˆ›ÙœÝš[™Ê
Kˆ˜Ü™Y]×Ø\YYŽˆ›ÙœÝš[™Ê
KˆœÝXÝ[Žˆ›ÙœÝš[™Ê
Kˆ^Žˆ›ÙœÝš[™Ê
KˆÝ[Žˆ›ÙœÝš[™Ê
KˆœÝ]\ÈŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœÝš\WÚ[›ÚXÙWÝ\›Žˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

KˆœÝš\WÜ—Ý\›Žˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

Kˆ˜Ü™X]YØ]Žˆ›ÙœÝš[™Ê
K™]][YJÈ›Ù™œÙ]ŽY_JBŸJJBŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆÙ][›ÚXÙH]Z[Ú][™H][\Ë‚ˆ
‹Â™^ÜÛÛœÝ\ØYÙUŒ’[›ÚXÙ\Ô™XY\˜[\ÈH›Ù›Øš™XÝ
Âˆš[›ÚXÙWÚYŽˆ›ÙœÝš[™Ê
BŸJB‚‚‚‚‚‚‚‚™^ÜÛÛœÝ\ØYÙUŒ’[›ÚXÙ\Ô™XY™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆš[›ÚXÙHŽˆ›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K]ZY

Kˆœ\š[ÙÜÝ\Žˆ›ÙœÝš[™Ê
K™]J
Kˆœ\š[ÙÙ[™Žˆ›ÙœÝš[™Ê
K™]J
Kˆœ[ˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœ]›Ü›WÙ™YHŽˆ›Ù›[X™\Š
Kˆ\ØYÙWÝÝ[Žˆ›Ù›[X™\Š
Kˆ˜Ü™Y]×Ø\YYŽˆ›Ù›[X™\Š
KˆœÝXÝ[Žˆ›Ù›[X™\Š
Kˆ^Žˆ›Ù›[X™\Š
KˆÝ[Žˆ›Ù›[X™\Š
KˆœÝ]\ÈŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœÝš\WÜ—Ý\›Žˆ›ÙœÝš[™Ê
K\›

K›Ü[Û˜[

BŸJKˆ›[™WÚ][\ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
Âˆ›[™WÝ\HŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ™[Y[œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ™\ØÜš\[ÛˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœ]X[]HŽˆ›ÙœÝš[™Ê
Kˆ[š]Žˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ[š]ÜšXÙHŽˆ›ÙœÝš[™Ê
Kˆ˜[[Ý[Žˆ›ÙœÝš[™Ê
KˆY\—Øœ™XZÙÝÛˆŽˆ›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

K›Ü[Û˜[

Kˆ˜Ü™Y]ÚYŽˆ›Ù›[X™\Š
K›Ü[Û˜[

BŸJJBŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆ\ÙYžHHœ›Û[™ÈÚÝÈš[[™ÈØ\›š[™ÜËØ[\Ë‚ˆ
ˆÝ[[X\žHÙ]XÝ]™H›ÝYšXØ][Ûˆ˜[›™\œÈ›ÜˆHÜ™Ë‚ˆ
‹Â‚‚‚‚‚‚‚™^ÜÛÛœÝ\ØYÙUŒ“›ÝYšXØ][ÛœÓ\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆ˜˜[›™\œÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ\HŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ›Y\ÜØYÙHŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ˜XÝ[ÛˆŽˆ›Ù›Øš™XÝ
Âˆ›X™[Žˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ\›Žˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJK›Ü[Û˜[

Kˆ™\ÛZ\ÜÚX›HŽˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

BŸJJBŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆ\Ý^[Y[Y]ÙÈÜˆÜ™X]HHÝš\HÚXÚÛÝ]Ù\ÜÚ[Ûˆ›ÜˆY[™ÈHØ\™‚ˆ
‹Â‚‚‚™^ÜÛÛœÝ\ØYÙUŒ”^[Y[Y]ÙÓ\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ˜œ˜[™Žˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ›\ÝŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ™^Û[ÛŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ™^ÞYX\ˆŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆš\×ÙY˜][Žˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

BŸJJBŸJB‚‚‹ÊŠ‚ˆ
ˆX[˜YÙHHÜXÚYšXÈ^[Y[Y]Ù‚ˆ
‹Â™^ÜÛÛœÝ\ØYÙUŒ”^[Y[Y]ÙÐÜ™X]T\˜[\ÈH›Ù›Øš™XÝ
ÂˆœWÚYŽˆ›ÙœÝš[™Ê
BŸJB‚™^ÜÛÛœÝ\ØYÙUŒ”^[Y[Y]ÙÐÜ™X]P›ÙHH›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

B‚‚‚‚™^ÜÛÛœÝ\ØYÙUŒ”^[Y[Y]ÙÐÜ™X]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆ›Y\ÜØYÙHŽˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆ™\šYšY\ÈHÙ\ÜÚ[Û‹[™YˆHÝ\ÝÛY\ˆ\È›ÈY˜][^[Y[›Y]ÙY]Ù]ÈH™]ÛKX]XÚYØ\™\ÈHY˜][ˆ\š]BÚ]\Ü˜YUÔ^YÕšY]Ëœ]ÛÈ›ÝØ\™XÛÛXÝ[Ûˆ›ÝÜÈ›ÙXÙBHØ[YH[™Ý]K‚ˆ
ˆÝ[[X\žHÛÛ™š\›HHÛÛ\]YÚXÚÛÝ]Ù]\Ù\ÜÚ[Û‹‚ˆ
‹Â‚‚‚™^ÜÛÛœÝ\ØYÙUŒ”^[Y[Y]ÙÕ\]P›ÙHH›Ù›Øš™XÝ
ÂˆœÙ\ÜÚ[Û—ÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJB‚‚‚‚™^ÜÛÛœÝ\ØYÙUŒ”^[Y[Y]ÙÕ\]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆœ^[Y[ÛY]ÙÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœÙ]Ø\×ÙY˜][Žˆ›Ù˜›ÛÛX[Š
BŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆ\Ý^[Y[Y]ÙÈÜˆÜ™X]HHÝš\HÚXÚÛÝ]Ù\ÜÚ[Ûˆ›ÜˆY[™ÈHØ\™‚ˆ
‹Â‚‚‚™^ÜÛÛœÝ\ØYÙUŒ”^[Y[Y]ÙÔÙ]\[[\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ˜œ˜[™Žˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ›\ÝŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ™^Û[ÛŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ™^ÞYX\ˆŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆš\×ÙY˜][Žˆ›Ù˜›ÛÛX[Š
K›Ü[Û˜[

BŸJJBŸJB‚‚‹ÊŠ‚ˆ
ˆ\Ý^[Y[Y]ÙÈÜˆÜ™X]HHÝš\HÚXÚÛÝ]Ù\ÜÚ[Ûˆ›ÜˆY[™ÈHØ\™‚ˆ
‹Â™^ÜÛÛœÝ\ØYÙUŒ”^[Y[Y]ÙÔÙ]\[[Ü™X]P›ÙHH›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

B‚‚‚‚™^ÜÛÛœÝ\ØYÙUŒ”^[Y[Y]ÙÔÙ]\[[Ü™X]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆ˜ÚXÚÛÝ]Ý\›Žˆ›ÙœÝš[™Ê
K\›

K›Z[ŠJBŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆ™\šYšY\ÈHÙ\ÜÚ[Û‹[™YˆHÝ\ÝÛY\ˆ\È›ÈY˜][^[Y[›Y]ÙY]Ù]ÈH™]ÛKX]XÚYØ\™\ÈHY˜][ˆ\š]BÚ]\Ü˜YUÔ^YÕšY]Ëœ]ÛÈ›ÝØ\™XÛÛXÝ[Ûˆ›ÝÜÈ›ÙXÙBHØ[YH[™Ý]K‚ˆ
ˆÝ[[X\žHÛÛ™š\›HHÛÛ\]YÚXÚÛÝ]Ù]\Ù\ÜÚ[Û‹‚ˆ
‹Â‚‚‚™^ÜÛÛœÝ\ØYÙUŒ”^[Y[Y]ÙÔÙ]\[[\]P›ÙHH›Ù›Øš™XÝ
ÂˆœÙ\ÜÚ[Û—ÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJB‚‚‚‚™^ÜÛÛœÝ\ØYÙUŒ”^[Y[Y]ÙÔÙ]\[[\]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆœ^[Y[ÛY]ÙÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœÙ]Ø\×ÙY˜][Žˆ›Ù˜›ÛÛX[Š
BŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆX[˜YÙHHÜXÚYšXÈ^[Y[Y]Ù‚ˆ
‹Â™^ÜÛÛœÝ\ØYÙUŒ”^[Y[Y]ÙÑ[]T\˜[\ÈH›Ù›Øš™XÝ
ÂˆœWÚYŽˆ›ÙœÝš[™Ê
BŸJB‚‚‚‚™^ÜÛÛœÝ\ØYÙUŒ”^[Y[Y]ÙÑ[]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆ›Y\ÜØYÙHŽˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆX[˜YÙHHÜXÚYšXÈ^[Y[Y]Ù‚ˆ
‹Â™^ÜÛÛœÝ\ØYÙUŒ”^[Y[Y]ÙÑY˜][Ü™X]T\˜[\ÈH›Ù›Øš™XÝ
ÂˆœWÚYŽˆ›ÙœÝš[™Ê
BŸJB‚™^ÜÛÛœÝ\ØYÙUŒ”^[Y[Y]ÙÑY˜][Ü™X]P›ÙHH›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

B‚‚‚‚™^ÜÛÛœÝ\ØYÙUŒ”^[Y[Y]ÙÑY˜][Ü™X]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆ›Y\ÜØYÙHŽˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆX[˜YÙHHÜXÚYšXÈ^[Y[Y]Ù‚ˆ
‹Â™^ÜÛÛœÝ\ØYÙUŒ”^[Y[Y]ÙÑY˜][[]T\˜[\ÈH›Ù›Øš™XÝ
ÂˆœWÚYŽˆ›ÙœÝš[™Ê
BŸJB‚‚‚‚™^ÜÛÛœÝ\ØYÙUŒ”^[Y[Y]ÙÑY˜][[]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆ›Y\ÜØYÙHŽˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆ™]\›œÈˆY\œÈ
ÈÈY[ÛœÈÚ]™X]\™\Ë[Z]ËšXÚ[™Ë‚ˆ
ˆÝ[[X\žHÙ][ˆÛÛ\\š\ÛÛˆ]H›ÜˆHšXÚ[™ÈYÙK‚ˆ
‹Â‚‚‚‚‚‚‚‚‚‚‚‚™^ÜÛÛœÝ\ØYÙUŒ”[œÐ[™YÛœÓ\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆ˜Ý\œ™[Ü[ˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ˜š[[™×Ú[\˜[Žˆ›ÙœÝš[™Ê
K›Z[ŠJKˆY\œÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšÙ^HŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ™\Ü^WÛ˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœ]›Ü›WÙ™YWÛ[ÛHŽˆ›Ù›[X™\Š
Kˆš\×ØÝ\œ™[Žˆ›Ù˜›ÛÛX[Š
Kˆ™™X]\™\ÈŽˆ›Ùœ™XÛÜ™
›ÙœÝš[™Ê
K›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

JBŸJJKˆ˜YÛœÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšÙ^HŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ™\Ü^WÛ˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœ]›Ü›WÙ™YWÛ[ÛHŽˆ›Ù›[X™\Š
Kˆš\×ØÝ\œ™[Žˆ›Ù˜›ÛÛX[Š
Kˆ™™X]\™\ÈŽˆ›Ùœ™XÛÜ™
›ÙœÝš[™Ê
K›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

JBŸJJKˆœšXÚ[™ÈŽˆ›Ùœ™XÛÜ™
›ÙœÝš[™Ê
K›Ù›Øš™XÝ
Âˆ™\Ü^WÛ˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ™\Ü^WÝ[š]Žˆ›ÙœÝš[™Ê
K›Z[ŠJKˆY\œÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
Âˆ\ÝÈŽˆ›Ù›[X™\Š
K›Ü[Û˜[

KˆœšXÙWÜ\—Ý[š]Žˆ›Ù›[X™\Š
BŸJJBŸJJKˆš\ÐÝ\ÝÛTšXÚ[™ÈŽˆ›Ù˜›ÛÛX[Š
Kˆ˜Ý\ÝÛQ]Z[ÈŽˆ›Ù›Øš™XÝ
Âˆœ]›Ü›WÙ™YHŽˆ›Ù›[X™\Š
Kˆœ]›Ü›WÙ™YWØš[[™×ØÞXÛHŽˆ›Ù›[X™\Š
Kˆœ\—ØÚ\™ÙWØ[[Ý[Žˆ›Ù›[X™\Š
Kˆ˜ÛÛ˜XÝÙ[™Ù]HŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ™™X]\™\ÈŽˆ›Ùœ™XÛÜ™
›ÙœÝš[™Ê
K›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

JKˆœšXÚ[™ÈŽˆ›Ùœ™XÛÜ™
›ÙœÝš[™Ê
K›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

JBŸJK›Ü[Û˜[

Kˆœ[™[™×ØØ[˜Ù[Žˆ›Ù˜›ÛÛX[Š
Kˆ˜Ø[˜Ù[Ø]Žˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

BŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆYÜˆ™[[Ý™H[ˆY[ÛˆÝXœØÜš\[Û‹‚ˆ
‹Â™^ÜÛÛœÝ\ØYÙUŒ”™Z[œÝ]PYÛÜ™X]P›ÙHH›Ù›Øš™XÝ
Âˆœ[ˆŽˆ›Ù™[[JÉØ›ÛÜÝ	Ë	ÜØØ[IË	Ù[\œš\ÙI×JK›Ü[Û˜[

BŸJB‚‚‚‚‚™^ÜÛÛœÝ\ØYÙUŒ”™Z[œÝ]PYÛÜ™X]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
ÂˆœÝXœØÜš\[Û—ÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœ[ˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆ™Z[œÝ]HH™]š[Ý\ÛK\ØÚY[YY[ÛˆØ[˜Ù[][Û‹‚ˆ
‹Â™^ÜÛÛœÝ\ØYÙUŒ”™Z[œÝ]PYÛ•\]P›ÙHH›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

B‚‚‚‚™^ÜÛÛœÝ\ØYÙUŒ”™Z[œÝ]PYÛ•\]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆ›Y\ÜØYÙHŽˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆYÜˆ™[[Ý™H[ˆY[ÛˆÝXœØÜš\[Û‹‚ˆ
‹Â‚‚‚™^ÜÛÛœÝ\ØYÙUŒ”™Z[œÝ]PYÛ‘[]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆ›Y\ÜØYÙHŽˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆYÜˆ™[[Ý™H[ˆY[ÛˆÝXœØÜš\[Û‹‚ˆ
‹Â™^ÜÛÛœÝ\ØYÙUŒ”™[[Ý™PYÛÜ™X]P›ÙHH›Ù›Øš™XÝ
Âˆœ[ˆŽˆ›Ù™[[JÉØ›ÛÜÝ	Ë	ÜØØ[IË	Ù[\œš\ÙI×JK›Ü[Û˜[

BŸJB‚‚‚‚‚™^ÜÛÛœÝ\ØYÙUŒ”™[[Ý™PYÛÜ™X]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
ÂˆœÝXœØÜš\[Û—ÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœ[ˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆ™Z[œÝ]HH™]š[Ý\ÛK\ØÚY[YY[ÛˆØ[˜Ù[][Û‹‚ˆ
‹Â™^ÜÛÛœÝ\ØYÙUŒ”™[[Ý™PYÛ•\]P›ÙHH›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

B‚‚‚‚™^ÜÛÛœÝ\ØYÙUŒ”™[[Ý™PYÛ•\]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆ›Y\ÜØYÙHŽˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆYÜˆ™[[Ý™H[ˆY[ÛˆÝXœØÜš\[Û‹‚ˆ
‹Â‚‚‚™^ÜÛÛœÝ\ØYÙUŒ”™[[Ý™PYÛ‘[]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆ›Y\ÜØYÙHŽˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆ›È]]8 %Ýš\H]][XØ]\ÈšXHÚYÛ˜]\™HXY\‹‚TUšY]Ë˜\×ÝšY]Ê
H]]ËX\Y\ÈÜÜ™—Ù^[\‚ˆ
ˆÝ[[X\žH[™HÝš\HÙXšÛÚÈ]™[Ë‚ˆ
‹Â‚‚‚‚™^ÜÛÛœÝ\ØYÙUŒ”Ýš\UÙXšÛÚÐÜ™X]P›ÙHH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ\HŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ™]HŽˆ›Ùœ™XÛÜ™
›ÙœÝš[™Ê
K›ÙœÝš[™Ê
JK›Ü[Û˜[

BŸJB‚‚‚‚™^ÜÛÛœÝ\ØYÙUŒ”Ýš\UÙXšÛÚÐÜ™X]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆ™]™[Ý\HŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ˜XÝ[ÛˆŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆœÝ]\ÈŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ›Y\ÜØYÙHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

BŸJK›Ü[Û˜[

BŸJB‚‚‹ÊŠ‚ˆ
ˆÜ™X]HHÝš\HÚXÚÛÝ]Ù\ÜÚ[Ûˆ[ˆÙ]\[ÙH›ÜˆØ\™ÛÛXÝ[Û‹‚ˆ
‹Â™^ÜÛÛœÝ\ØYÙUŒ•\Ü˜YUÔ^YÐÜ™X]P›ÙHH›Ù›Øš™XÝ
Â‚ŸJKœ\ÜÝ›ÝYÚ

B‚‚‚‚™^ÜÛÛœÝ\ØYÙUŒ•\Ü˜YUÔ^YÐÜ™X]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆ˜ÚXÚÛÝ]Ý\›Žˆ›ÙœÝš[™Ê
K\›

K›Z[ŠJBŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆÛÛ™š\›H\Ü˜YHY\ˆÝš\HÚXÚÛÝ]ÝXØÙYYË‚ˆ
‹Â‚‚‚™^ÜÛÛœÝ\ØYÙUŒ•\Ü˜YUÔ^YÕ\]P›ÙHH›Ù›Øš™XÝ
ÂˆœÙ\ÜÚ[Û—ÚYŽˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJB‚‚‚‚™^ÜÛÛœÝ\ØYÙUŒ•\Ü˜YUÔ^YÕ\]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆœ[ˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆ™]\›œÈ\‹Y[Y[œÚ[ÛˆÝ[Ëœ™YH[ÝØ[˜Ù\Ë\Ý[X]YÛÜÝË˜[™›Ú™XÝY[ÛY[™\ØYÙK‚‚”]Y\žH\˜[\Î‚ˆ\š[ÙˆVVVKSSH
Y˜][ˆÝ\œ™[[Û
BˆÛÜšÜÜXÙWÚYˆÜ[Û˜[
š[\ˆžHÛÜšÜÜXÙJBˆ
ˆÝ[[X\žHÙ]\ØYÙHÝ™\šY]È›ÜˆHÝ\œ™[š[[™È\š[Ù‚ˆ
‹Â‚‚™^ÜÛÛœÝ\ØYÙUŒ•\ØYÙSÝ™\šY]Ó\Ý]Y\žT\š[Ù™YÑ^H™]È™YÑ^
	×—ÍKWÌŸI	ÊNÂ‚‚™^ÜÛÛœÝ\ØYÙUŒ•\ØYÙSÝ™\šY]Ó\Ý]Y\žT\š[Ù[™™YÑ^H™]È™YÑ^
	×—ÍKWÌŸI	ÊNÂ‚‚™^ÜÛÛœÝ\ØYÙUŒ•\ØYÙSÝ™\šY]Ó\Ý]Y\žT\˜[\ÈH›Ù›Øš™XÝ
Âˆœ\š[ÙŽˆ›ÙœÝš[™Ê
K›Z[ŠJKœ™YÙ^
\ØYÙUŒ•\ØYÙSÝ™\šY]Ó\Ý]Y\žT\š[Ù™YÑ^
K›Ü[Û˜[

Kˆœ\š[ÙÙ[™Žˆ›ÙœÝš[™Ê
K›Z[ŠJKœ™YÙ^
\ØYÙUŒ•\ØYÙSÝ™\šY]Ó\Ý]Y\žT\š[Ù[™™YÑ^
K›Ü[Û˜[

KˆÛÜšÜÜXÙWÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

BŸJB‚‚‚‚‚‚‚‚‚‚‚‚™^ÜÛÛœÝ\ØYÙUŒ•\ØYÙSÝ™\šY]Ó\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆœ[ˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœ[—Ù\Ü^WÛ˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœ]›Ü›WÙ™YHŽˆ›Ù›[X™\Š
Kˆœ\š[ÙŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ˜š[[™×Ü\š[ÙÜÝ\Žˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ˜š[[™×Ü\š[ÙÙ[™Žˆ›ÙœÝš[™Ê
K›Z[ŠJKˆÝ[Ù\Ý[X]YØÛÜÝŽˆ›Ù›[X™\Š
KˆÝ[ÝÚ]Ü]›Ü›HŽˆ›Ù›[X™\Š
Kˆ™[Y[œÚ[ÛœÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆšÙ^HŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ™\Ü^WÛ˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ™\Ü^WÝ[š]Žˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ˜Ý\œ™[Ý\ØYÙHŽˆ›Ù›[X™\Š
Kˆ˜Ý\œ™[Ý\ØYÙWÜ˜]ÈŽˆ›Ù›[X™\Š
Kˆ™œ™YWØ[ÝØ[˜ÙHŽˆ›Ù›[X™\Š
Kˆœ›Ú™XÝYÝ\ØYÙHŽˆ›Ù›[X™\Š
Kˆ™\Ý[X]YØÛÜÝŽˆ›Ù›[X™\Š
KˆY\—Øœ™XZÙÝÛˆŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆY\—ÜÝ\Žˆ›Ù›[X™\Š
K›Ü[Û˜[

KˆY\—Ù[™Žˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆœ]X[]HŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆœ˜]HŽˆ›Ù›[X™\Š
K›Ü[Û˜[

Kˆ˜ÛÜÝŽˆ›Ù›[X™\Š
K›Ü[Û˜[

BŸJJK›Ü[Û˜[

Kˆ\ØYÙWÜÝŽˆ›Ù›[X™\Š
BŸJJKˆœ[™[™×ØØ[˜Ù[Žˆ›Ù˜›ÛÛX[Š
Kˆ˜Ø[˜Ù[Ø]Žˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

BŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆ]Y\žH\˜[\Î‚ˆ[Y[œÚ[ÛŽˆ™\]Z\™Y
K™Ë‹œÝÜ˜YÙH‹˜ZWØÜ™Y]ÈŠBˆ\š[ÙˆVVVKSSH
Y˜][ˆÝ\œ™[[Û
Bˆ\š[ÙÙ[™ˆVVVKSSH
Ü[Û˜[ÈY˜][ÈÈ\š[Ù›ÜˆHÚ[™ÛH[Û
Bˆ
ˆÝ[[X\žHÙ]Z[H\ØYÙH[YK\Ù\šY\È›ÜˆH[Y[œÚ[Û‹‚ˆ
‹Â‚‚‚™^ÜÛÛœÝ\ØYÙUŒ•\ØYÙU[YTÙ\šY\Ó\Ý]Y\žT\š[Ù™YÑ^H™]È™YÑ^
	×—ÍKWÌŸI	ÊNÂ‚‚™^ÜÛÛœÝ\ØYÙUŒ•\ØYÙU[YTÙ\šY\Ó\Ý]Y\žT\š[Ù[™™YÑ^H™]È™YÑ^
	×—ÍKWÌŸI	ÊNÂ‚‚™^ÜÛÛœÝ\ØYÙUŒ•\ØYÙU[YTÙ\šY\Ó\Ý]Y\žT\˜[\ÈH›Ù›Øš™XÝ
Âˆ™[Y[œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœ\š[ÙŽˆ›ÙœÝš[™Ê
K›Z[ŠJKœ™YÙ^
\ØYÙUŒ•\ØYÙU[YTÙ\šY\Ó\Ý]Y\žT\š[Ù™YÑ^
K›Ü[Û˜[

Kˆœ\š[ÙÙ[™Žˆ›ÙœÝš[™Ê
K›Z[ŠJKœ™YÙ^
\ØYÙUŒ•\ØYÙU[YTÙ\šY\Ó\Ý]Y\žT\š[Ù[™™YÑ^
K›Ü[Û˜[

BŸJB‚‚‚‚‚‚‚™^ÜÛÛœÝ\ØYÙUŒ•\ØYÙU[YTÙ\šY\Ó\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆ™[Y[œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœ\š[ÙŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœ\š[ÙÙ[™Žˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœÙ\šY\ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
Âˆ™]HŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ\ØYÙHŽˆ›Ù›[X™\Š
BŸJJBŸJBŸJB‚‚‹ÊŠ‚ˆ
ˆ]Y\žH\˜[\Î‚ˆ[Y[œÚ[ÛŽˆ™\]Z\™Yˆ\š[ÙˆVVVKSSH
Y˜][ˆÝ\œ™[[Û
Bˆ\š[ÙÙ[™ˆVVVKSSH
Ü[Û˜[ÈY˜][ÈÈ\š[Ù›ÜˆHÚ[™ÛH[Û
Bˆ
ˆÝ[[X\žHÙ]\‹]ÛÜšÜÜXÙH\ØYÙHœ™XZÙÝÛˆ›ÜˆH[Y[œÚ[Û‹‚ˆ
‹Â‚‚‚™^ÜÛÛœÝ\ØYÙUŒ•\ØYÙUÛÜšÜÜXÙPœ™XZÙÝÛ“\Ý]Y\žT\š[Ù™YÑ^H™]È™YÑ^
	×—ÍKWÌŸI	ÊNÂ‚‚™^ÜÛÛœÝ\ØYÙUŒ•\ØYÙUÛÜšÜÜXÙPœ™XZÙÝÛ“\Ý]Y\žT\š[Ù[™™YÑ^H™]È™YÑ^
	×—ÍKWÌŸI	ÊNÂ‚‚™^ÜÛÛœÝ\ØYÙUŒ•\ØYÙUÛÜšÜÜXÙPœ™XZÙÝÛ“\Ý]Y\žT\˜[\ÈH›Ù›Øš™XÝ
Âˆ™[Y[œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœ\š[ÙŽˆ›ÙœÝš[™Ê
K›Z[ŠJKœ™YÙ^
\ØYÙUŒ•\ØYÙUÛÜšÜÜXÙPœ™XZÙÝÛ“\Ý]Y\žT\š[Ù™YÑ^
K›Ü[Û˜[

Kˆœ\š[ÙÙ[™Žˆ›ÙœÝš[™Ê
K›Z[ŠJKœ™YÙ^
\ØYÙUŒ•\ØYÙUÛÜšÜÜXÙPœ™XZÙÝÛ“\Ý]Y\žT\š[Ù[™™YÑ^
K›Ü[Û˜[

BŸJB‚‚‚‚‚‚‚™^ÜÛÛœÝ\ØYÙUŒ•\ØYÙUÛÜšÜÜXÙPœ™XZÙÝÛ“\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆ™[Y[œÚ[ÛˆŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœ\š[ÙŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆœ\š[ÙÙ[™Žˆ›ÙœÝš[™Ê
K›Z[ŠJKˆÛÜšÜÜXÙ\ÈŽˆ›Ù˜\œ˜^J›Ù›Øš™XÝ
ÂˆÛÜšÜÜXÙWÚYŽˆ›ÙœÝš[™Ê
K]ZY

K›Ü[Û˜[

KˆÛÜšÜÜXÙWÛ˜[YHŽˆ›ÙœÝš[™Ê
K›Z[ŠJKˆ\ØYÙHŽˆ›Ù›[X™\Š
BŸJJBŸJBŸJB‚‚‚‚‚‚™^ÜÛÛœÝ\ØYÙUÙXšÛÚÐÜ™X]P›ÙHH›Ù›Øš™XÝ
ÂˆšYŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ\HŽˆ›ÙœÝš[™Ê
K›Z[ŠJK›Ü[Û˜[

Kˆ™]HŽˆ›Ùœ™XÛÜ™
›ÙœÝš[™Ê
K›ÙœÝš[™Ê
JK›Ü[Û˜[

BŸJB‚™^ÜÛÛœÝ\ØYÙUÙXšÛÚÐÜ™X]T™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ù›Øš™XÝ
Âˆ™]™[Ý\HŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ˜XÝ[ÛˆŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

KˆœÝ]\ÈŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

Kˆ›Y\ÜØYÙHŽˆ›ÙœÝš[™Ê
K›Ü[Û˜[

BŸJK›Ü[Û˜[

BŸJB‚‚™^ÜÛÛœÝ\ØYÙUÛÜšÜÜXÙQ]˜[Ý[[X\žS\Ý]Y\žS[ÛX^HLŽÂ‚‚‚™^ÜÛÛœÝ\ØYÙUÛÜšÜÜXÙQ]˜[Ý[[X\žS\Ý]Y\žT\˜[\ÈH›Ù›Øš™XÝ
Âˆ›[ÛŽˆ›Ù›[X™\Š
K›Z[ŠJK›X^
\ØYÙUÛÜšÜÜXÙQ]˜[Ý[[X\žS\Ý]Y\žS[ÛX^
K›Ü[Û˜[

KˆžYX\ˆŽˆ›Ù›[X™\Š
K›Ü[Û˜[

KˆÛÜšÜÜXÙWÚYŽˆ›ÙœÝš[™Ê
K]ZY

BŸJB‚™^ÜÛÛœÝ\ØYÙUÛÜšÜÜXÙQ]˜[Ý[[X\žS\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ùœ™XÛÜ™
›ÙœÝš[™Ê
K›ÙœÝš[™Ê
JBŸJB‚‚™^ÜÛÛœÝ\ØYÙUÛÜšÜÜXÙU\ØYÙTÝ[[X\žS\Ý]Y\žS[ÛX^HLŽÂ‚‚‚™^ÜÛÛœÝ\ØYÙUÛÜšÜÜXÙU\ØYÙTÝ[[X\žS\Ý]Y\žT\˜[\ÈH›Ù›Øš™XÝ
Âˆ›[ÛŽˆ›Ù›[X™\Š
K›Z[ŠJK›X^
\ØYÙUÛÜšÜÜXÙU\ØYÙTÝ[[X\žS\Ý]Y\žS[ÛX^
K›Ü[Û˜[

KˆžYX\ˆŽˆ›Ù›[X™\Š
K›Ü[Û˜[

BŸJB‚™^ÜÛÛœÝ\ØYÙUÛÜšÜÜXÙU\ØYÙTÝ[[X\žS\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù˜›ÛÛX[Š
Kˆœ™\Ý[Žˆ›Ùœ™XÛÜ™
›ÙœÝš[™Ê
K›ÙœÝš[™Ê
JBŸJB‚‚‹ÊŠ‚ˆ
ˆX[ÚXÚÈH[Ø^\È™]\›œÈÒË‚ˆ
‹Â‚‚‚™^ÜÛÛœÝŒRX[\Ý™\ÜÛœÙHH›Ù›Øš™XÝ
ÂˆœÝ]\ÈŽˆ›Ù™[[JÉÚX[I×JKˆœÙ\šXÙHŽˆ›ÙœÝš[™Ê
K›Z[ŠJBŸJB