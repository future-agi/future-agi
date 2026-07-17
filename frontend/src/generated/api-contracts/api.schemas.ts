/**
 * Auto-generated from the Django backend OpenAPI schema.
 * To modify these types, update Django serializers/views, regenerate OpenAPI, then run:
 *   yarn contracts:generate
 *
 * Future AGI Management API - management contracts
 * OpenAPI spec version: v1
 */
export interface RecoveryCodesRemainingResponseApi {
  remaining: number;
}

export type AccountsErrorResponseApiType = typeof AccountsErrorResponseApiType[keyof typeof AccountsErrorResponseApiType];


export const AccountsErrorResponseApiType = {
  validation_error: 'validation_error',
  authentication_error: 'authentication_error',
  payment_required: 'payment_required',
  entitlement_error: 'entitlement_error',
  permission_error: 'permission_error',
  not_found: 'not_found',
  conflict: 'conflict',
  client_error: 'client_error',
  rate_limit: 'rate_limit',
  server_error: 'server_error',
  service_unavailable: 'service_unavailable',
  timeout: 'timeout',
  api_error: 'api_error',
} as const;

/**
 * String error message or structured account/login error metadata.
 */
export type AccountsErrorResponseApiResult = {
  error?: string;
  error_code?: string;
  message?: string;
  blocked?: boolean;
  remaining_attempts?: number;
  block_time?: number;
  block_time_remaining?: number;
};

export type AccountsErrorResponseApiDetails = {[key: string]: string[]};

export interface AccountsErrorResponseApi {
  status?: boolean;
  type?: AccountsErrorResponseApiType;
  code?: string;
  detail?: string;
  /** String error message or structured account/login error metadata. */
  result?: AccountsErrorResponseApiResult;
  message?: string;
  error?: string;
  attr?: string;
  details?: AccountsErrorResponseApiDetails;
}

export type ManagementAPIErrorResponseApiType = typeof ManagementAPIErrorResponseApiType[keyof typeof ManagementAPIErrorResponseApiType];


export const ManagementAPIErrorResponseApiType = {
  validation_error: 'validation_error',
  authentication_error: 'authentication_error',
  payment_required: 'payment_required',
  entitlement_error: 'entitlement_error',
  permission_error: 'permission_error',
  not_found: 'not_found',
  conflict: 'conflict',
  client_error: 'client_error',
  rate_limit: 'rate_limit',
  server_error: 'server_error',
  service_unavailable: 'service_unavailable',
  timeout: 'timeout',
  api_error: 'api_error',
} as const;

export type ManagementAPIErrorResponseApiDetails = {[key: string]: string[]};

export interface ManagementAPIErrorResponseApi {
  status?: boolean;
  type?: ManagementAPIErrorResponseApiType;
  code?: string;
  detail?: string;
  result?: string;
  message?: string;
  error?: string;
  attr?: string;
  details?: ManagementAPIErrorResponseApiDetails;
}

export interface RecoveryCodesRegenerateApi {
  /**
     * @minLength 6
     * @maxLength 10
     */
  code?: string;
  /** @minLength 1 */
  password?: string;
}

export interface RecoveryCodesRegenerateResponseApi {
  recovery_codes: string[];
}

export type TwoFactorStatusApiMethods = {[key: string]: string};

export interface TwoFactorStatusApi {
  two_factor_enabled: boolean;
  methods: TwoFactorStatusApiMethods;
  recovery_codes_remaining: number;
}

export interface TOTPDisableApi {
  /**
     * @minLength 6
     * @maxLength 10
     */
  code: string;
}

export interface TOTPDisableResponseApi {
  success: boolean;
}

export interface TOTPConfirmApi {
  /**
     * @minLength 6
     * @maxLength 6
     */
  code: string;
}

export interface TOTPConfirmResponseApi {
  success: boolean;
  recovery_codes: string[];
}

export interface AccountsEmptyRequestApi { [key: string]: unknown }

export interface TOTPSetupResponseApi {
  /** @minLength 1 */
  qr_code: string;
  /** @minLength 1 */
  secret: string;
  /** @minLength 1 */
  provisioning_uri: string;
}

export type TwoFactorPasskeyVerifyRequestApiCredential = { [key: string]: unknown };

export interface TwoFactorPasskeyVerifyRequestApi {
  challenge_token: string;
  credential: TwoFactorPasskeyVerifyRequestApiCredential;
  session_id?: string;
}

export interface AccountsTokenPairResponseApi {
  /** @minLength 1 */
  access?: string;
  /** @minLength 1 */
  refresh?: string;
  requires_two_factor?: boolean;
  challenge_token?: string;
  methods?: string[];
  requires_org_setup?: boolean;
  /** @minLength 1 */
  message?: string;
  new_org?: boolean;
  /** @minLength 1 */
  org_name?: string;
  is_first_login?: boolean;
  /** @minLength 1 */
  recovery_codes_warning?: string;
}

export interface TwoFactorChallengeTokenApi {
  challenge_token: string;
}

export interface WebAuthnRelyingPartyApi {
  /** @minLength 1 */
  id?: string;
  /** @minLength 1 */
  name?: string;
}

export interface WebAuthnUserApi {
  /** @minLength 1 */
  id: string;
  /** @minLength 1 */
  name: string;
  /** @minLength 1 */
  displayName?: string;
}

export interface WebAuthnPublicKeyCredentialParamApi {
  /** @minLength 1 */
  type: string;
  alg: number;
}

export interface WebAuthnCredentialDescriptorApi {
  /** @minLength 1 */
  type: string;
  /** @minLength 1 */
  id: string;
  transports?: string[];
}

export interface WebAuthnAuthenticatorSelectionApi {
  /** @minLength 1 */
  authenticatorAttachment?: string;
  /** @minLength 1 */
  residentKey?: string;
  requireResidentKey?: boolean;
  /** @minLength 1 */
  userVerification?: string;
}

export interface WebAuthnExtensionsApi {
  /** @minLength 1 */
  appid?: string;
  credProps?: boolean;
  uvm?: boolean;
}

export interface PasskeyOptionsResponseApi {
  /** @minLength 1 */
  challenge: string;
  timeout?: number;
  rp?: WebAuthnRelyingPartyApi;
  user?: WebAuthnUserApi;
  pubKeyCredParams?: WebAuthnPublicKeyCredentialParamApi[];
  excludeCredentials?: WebAuthnCredentialDescriptorApi[];
  allowCredentials?: WebAuthnCredentialDescriptorApi[];
  authenticatorSelection?: WebAuthnAuthenticatorSelectionApi;
  /** @minLength 1 */
  attestation?: string;
  /** @minLength 1 */
  rpId?: string;
  /** @minLength 1 */
  userVerification?: string;
  extensions?: WebAuthnExtensionsApi;
  session_id?: string;
}

export interface TwoFactorVerifyApi {
  challenge_token: string;
  /**
     * @minLength 6
     * @maxLength 10
     */
  code: string;
}

export interface AcceptInvitationPreviewResponseApi {
  valid: boolean;
  /** @minLength 1 */
  email: string;
  /** @minLength 1 */
  org_name: string;
}

export interface AcceptInvitationRequestApi {
  /** @minLength 1 */
  new_password: string;
  /** @minLength 1 */
  repeat_password: string;
}

export interface AccountOrganizationDetailApi {
  id: string;
  created_at?: string;
  /** @minLength 1 */
  name: string;
  display_name: string;
  is_new?: boolean;
  ws_enabled?: boolean;
  /** @minLength 1 */
  region?: string;
  require_2fa?: boolean;
  require_2fa_grace_period_days?: number;
  require_2fa_enforced_at?: string;
}

export interface AccountUserItemResponseApi {
  id: string;
  /** @minLength 1 */
  email: string;
  name: string;
  organization_role: string;
  organization: AccountOrganizationDetailApi;
  created_at: string;
  /** @minLength 1 */
  status: string;
  role: string;
  goals?: string[];
}

export interface AccountsPaginatedUserResponseApi {
  count: number;
  /** @minLength 1 */
  next: string;
  /** @minLength 1 */
  previous: string;
  results: AccountUserItemResponseApi[];
  total_pages: number;
  current_page: number;
  total_queries?: number;
}

export interface UserCreateApi {
  /**
     * @minLength 1
     * @maxLength 255
     */
  email: string;
  /**
     * @minLength 8
     * @maxLength 128
     */
  password: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  organization_name: string;
  send_credential: boolean;
}

export interface AppsmithUserCreateResponseApi {
  /** @minLength 1 */
  email: string;
  /** @minLength 1 */
  password: string;
  /** @minLength 1 */
  organization_name: string;
  send_credential: boolean;
}

export interface PasswordValidationApi {
  /**
     * @minLength 8
     * @maxLength 128
     */
  password: string;
}

export interface AppsmithPasswordUpdateResponseApi {
  /** @minLength 1 */
  password: string;
}

export interface SOSLoginApi {
  /**
     * @minLength 1
     * @maxLength 255
     */
  email: string;
}

export interface AWSMarketplaceSignupRequestApi {
  /** @minLength 1 */
  onboarding_token: string;
  /** @minLength 1 */
  email: string;
  /** @minLength 1 */
  full_name: string;
}

export interface AWSMarketplaceSignupResultApi {
  /** @minLength 1 */
  message: string;
  /** @minLength 1 */
  user_email: string;
}

export interface AWSMarketplaceSignupResponseApi {
  status: boolean;
  result: AWSMarketplaceSignupResultApi;
}

export interface PublicRegionConfigApi {
  /** @minLength 1 */
  code: string;
  /** @minLength 1 */
  label: string;
  /** @minLength 1 */
  app_url: string;
}

export interface PublicConfigResultApi {
  cloud: boolean;
  /** @minLength 1 */
  region: string;
  available_regions: PublicRegionConfigApi[];
}

export interface PublicConfigResponseApi {
  status: boolean;
  result: PublicConfigResultApi;
}

export interface UserIdsRequestApi {
  user_ids: string[];
}

export interface AccountsBulkUserMutationItemApi {
  user_id: string;
  /** @minLength 1 */
  message?: string;
  /** @minLength 1 */
  error?: string;
}

export interface UserChecksResultApi {
  keys: boolean;
  dataset: boolean;
  evaluation: boolean;
  experiment: boolean;
  observe: boolean;
  invite: boolean;
}

export interface UserChecksResponseApi {
  status: boolean;
  result: UserChecksResultApi;
}

export interface AccountsUserProfileResponseApi {
  name: string;
  /** @minLength 1 */
  email: string;
  org_name: string;
}

export interface UserSecretKeyApi {
  key_id: string;
}

export interface AccountsStringResultResponseApi {
  status: boolean;
  /** @minLength 1 */
  result: string;
}

export interface CreateSecretKeyApi {
  /**
     * @minLength 1
     * @maxLength 100
     */
  key_name: string;
}

export interface SecretKeyCreateResultApi {
  key_id: string;
  /** @minLength 1 */
  key_name: string;
  /** @minLength 1 */
  api_key: string;
  /** @minLength 1 */
  masked_api_key: string;
  /** @minLength 1 */
  secret_key: string;
  /** @minLength 1 */
  masked_secret_key: string;
}

export interface SecretKeyCreateResponseApi {
  status: boolean;
  result: SecretKeyCreateResultApi;
}

export interface SecretKeyListMetadataApi {
  total_rows: number;
  total_pages: number;
  page_number: number;
  page_size: number;
}

export interface SecretKeyListItemApi {
  id: string;
  key_name: string;
  /** @minLength 1 */
  api_key: string;
  /** @minLength 1 */
  secret_key: string;
  /** @minLength 1 */
  created_by: string;
  created_at: string;
  enabled: boolean;
  /** @minLength 1 */
  type: string;
}

export interface SecretKeyListResultApi {
  metadata: SecretKeyListMetadataApi;
  table: SecretKeyListItemApi[];
}

export interface SecretKeyListResponseApi {
  status: boolean;
  result: SecretKeyListResultApi;
}

export interface SecretKeyDataResponseApi {
  id: string;
  /** @minLength 1 */
  api_key: string;
  /** @minLength 1 */
  secret_key: string;
}

export interface SecretKeysResponseApi {
  /** @minLength 1 */
  status: string;
  data: SecretKeyDataResponseApi;
}

export interface LogoutRequestApi {
  refresh?: string;
}

export interface AccountsMessageResultApi {
  /** @minLength 1 */
  message: string;
}

export interface AccountsMessageResponseApi {
  status: boolean;
  result: AccountsMessageResultApi;
}

export interface TimezoneRequestApi {
  /**
     * @minLength 1
     * @maxLength 64
     */
  timezone: string;
}

export interface TimezoneResponseApi {
  /** @minLength 1 */
  timezone: string;
}

export interface UserOnboardingResultApi {
  role: string;
  goals: string[];
  completed: boolean;
}

export interface UserOnboardingResponseApi {
  status: boolean;
  result: UserOnboardingResultApi;
}

export interface UserOnboardingApi {
  /**
     * @minLength 1
     * @maxLength 255
     */
  role: string;
  goals: string[];
}

export interface UserOnboardingDataApi {
  /** @minLength 1 */
  role: string;
  goals: string[];
}

export interface UserOnboardingSaveResultApi {
  /** @minLength 1 */
  message: string;
  data: UserOnboardingDataApi;
}

export interface UserOnboardingSaveResponseApi {
  status: boolean;
  result: UserOnboardingSaveResultApi;
}

export interface OrgTwoFactorPolicyResponseApi {
  require_2fa: boolean;
  require_2fa_grace_period_days: number;
  require_2fa_enforced_at: string;
}

export interface OrgTwoFactorPolicyApi {
  require_2fa: boolean;
  /**
     * @minimum 1
     * @maximum 30
     */
  require_2fa_grace_period_days?: number;
}

/**
 * Integer org level to grant (Owner=15, Admin=8, Member=3, Viewer=1).
 */
export type InviteCreateApiOrgLevel = typeof InviteCreateApiOrgLevel[keyof typeof InviteCreateApiOrgLevel];


export const InviteCreateApiOrgLevel = {
  NUMBER_15: 15,
  NUMBER_8: 8,
  NUMBER_3: 3,
  NUMBER_1: 1,
} as const;

export type WorkspaceAccessInputApiLevel = typeof WorkspaceAccessInputApiLevel[keyof typeof WorkspaceAccessInputApiLevel];


export const WorkspaceAccessInputApiLevel = {
  NUMBER_8: 8,
  NUMBER_3: 3,
  NUMBER_1: 1,
} as const;

/**
 * List of {"workspace_id": "<uuid>", "level": <int>}.
 */
export interface WorkspaceAccessInputApi {
  workspace_id: string;
  level?: WorkspaceAccessInputApiLevel;
}

export interface InviteCreateApi {
  /**
     * @minItems 1
     * @maxItems 50
     */
  emails: string[];
  /** Integer org level to grant (Owner=15, Admin=8, Member=3, Viewer=1). */
  org_level: InviteCreateApiOrgLevel;
  /** List of {"workspace_id": "<uuid>", "level": <int>}. */
  workspace_access?: WorkspaceAccessInputApi[];
}

export interface InviteCreateResultApi {
  invited: string[];
  already_members?: string[];
}

export interface InviteCreateResponseApi {
  status: boolean;
  result: InviteCreateResultApi;
}

export interface InviteCancelApi {
  invite_id: string;
}

export interface RBACMessageResultApi {
  /** @minLength 1 */
  message: string;
}

export interface RBACMessageResponseApi {
  status: boolean;
  result: RBACMessageResultApi;
}

export type InviteResendApiOrgLevel = typeof InviteResendApiOrgLevel[keyof typeof InviteResendApiOrgLevel];


export const InviteResendApiOrgLevel = {
  NUMBER_15: 15,
  NUMBER_8: 8,
  NUMBER_3: 3,
  NUMBER_1: 1,
} as const;

export interface InviteResendApi {
  invite_id: string;
  org_level?: InviteResendApiOrgLevel;
}

export interface MemberWorkspaceAccessApi {
  workspace_id: string;
  /** @minLength 1 */
  workspace_name: string;
  ws_level: number;
  /** @minLength 1 */
  ws_role: string;
  auto_access?: boolean;
}

export type MemberListItemApiType = typeof MemberListItemApiType[keyof typeof MemberListItemApiType];


export const MemberListItemApiType = {
  member: 'member',
  invite: 'invite',
} as const;

export interface MemberListItemApi {
  id: string;
  name: string;
  /** @minLength 1 */
  email: string;
  org_level?: number;
  /** @minLength 1 */
  org_role?: string;
  ws_level?: number;
  /** @minLength 1 */
  ws_role?: string;
  workspaces?: MemberWorkspaceAccessApi[];
  /** @minLength 1 */
  status: string;
  created_at: string;
  type: MemberListItemApiType;
  auto_access?: boolean;
}

export interface MemberListResultApi {
  results: MemberListItemApi[];
  total: number;
  page: number;
  limit: number;
}

export interface MemberListResponseApi {
  status: boolean;
  result: MemberListResultApi;
}

export interface MemberRemoveApi {
  user_id: string;
}

export interface MemberUserMutationResultApi {
  /** @minLength 1 */
  message: string;
  user_id: string;
}

export interface MemberUserMutationResponseApi {
  status: boolean;
  result: MemberUserMutationResultApi;
}

export type MemberRoleUpdateApiOrgLevel = typeof MemberRoleUpdateApiOrgLevel[keyof typeof MemberRoleUpdateApiOrgLevel];


export const MemberRoleUpdateApiOrgLevel = {
  NUMBER_15: 15,
  NUMBER_8: 8,
  NUMBER_3: 3,
  NUMBER_1: 1,
} as const;

export type MemberRoleUpdateApiWsLevel = typeof MemberRoleUpdateApiWsLevel[keyof typeof MemberRoleUpdateApiWsLevel];


export const MemberRoleUpdateApiWsLevel = {
  NUMBER_8: 8,
  NUMBER_3: 3,
  NUMBER_1: 1,
} as const;

export interface MemberRoleUpdateApi {
  user_id: string;
  org_level?: MemberRoleUpdateApiOrgLevel;
  ws_level?: MemberRoleUpdateApiWsLevel;
  /** Required when updating ws_level. */
  workspace_id?: string;
  /** List of {workspace_id, level} for explicit workspace grants on demotion. */
  workspace_access?: WorkspaceAccessInputApi[];
}

export type MemberRoleUpdateResultApiChanges = { [key: string]: unknown };

export interface MemberRoleUpdateResultApi {
  /** @minLength 1 */
  message: string;
  changes: MemberRoleUpdateResultApiChanges;
}

export interface MemberRoleUpdateResponseApi {
  status: boolean;
  result: MemberRoleUpdateResultApi;
}

export interface OrganizationSelectionItemApi {
  id: string;
  /** @minLength 1 */
  name: string;
  display_name: string;
  role: string;
  level: number;
  is_selected: boolean;
}

export interface OrganizationSelectionListResultApi {
  organizations: OrganizationSelectionItemApi[];
  total_count: number;
}

export interface OrganizationSelectionListResponseApi {
  status: boolean;
  result: OrganizationSelectionListResultApi;
}

export interface OrganizationSwitchRequestApi {
  organization_id: string;
}

export interface OrganizationSummaryApi {
  id: string;
  /** @minLength 1 */
  name: string;
  display_name: string;
  ws_enabled?: boolean;
}

export interface OrganizationSelectResultApi {
  /** @minLength 1 */
  message: string;
  organization: OrganizationSummaryApi;
}

export interface OrganizationSelectResponseApi {
  status: boolean;
  result: OrganizationSelectResultApi;
}

export interface OrganizationNameRequestApi {
  organization_name?: string;
}

export interface OrganizationCreateResultApi {
  organization_id: string;
  organization_name: string;
  workspace_id: string;
  /** @minLength 1 */
  message: string;
}

export interface OrganizationCreateResponseApi {
  status: boolean;
  result: OrganizationCreateResultApi;
}

export interface CurrentOrganizationResultApi {
  organization: OrganizationSummaryApi;
  role?: string;
  level?: number;
  source?: string;
  /** @minLength 1 */
  message?: string;
}

export interface CurrentOrganizationResponseApi {
  status: boolean;
  result: CurrentOrganizationResultApi;
}

export interface OrganizationCreateRequestApi {
  /** @minLength 1 */
  name: string;
  display_name?: string;
}

export interface WorkspaceSummaryApi {
  id: string;
  /** @minLength 1 */
  name: string;
  display_name: string;
  description?: string;
  is_default?: boolean;
}

export interface AdditionalOrganizationCreateResultApi {
  organization: OrganizationSummaryApi;
  workspace: WorkspaceSummaryApi;
  /** @minLength 1 */
  message: string;
}

export interface AdditionalOrganizationCreateResponseApi {
  status: boolean;
  result: AdditionalOrganizationCreateResultApi;
}

export interface OrganizationSwitchResultApi {
  organization: OrganizationSummaryApi;
  org_role: string;
  org_level: number;
  workspace_role: string;
  workspace?: WorkspaceSummaryApi;
}

export interface OrganizationSwitchResponseApi {
  status: boolean;
  result: OrganizationSwitchResultApi;
}

export interface OrganizationUpdateRequestApi {
  name?: string;
  display_name?: string;
}

export interface OrganizationUpdateResultApi {
  id: string;
  /** @minLength 1 */
  name: string;
  display_name: string;
}

export interface OrganizationUpdateResponseApi {
  status: boolean;
  result: OrganizationUpdateResultApi;
}

export type PasskeyCredentialRequestApiCredential = { [key: string]: unknown };

export interface PasskeyCredentialRequestApi {
  credential: PasskeyCredentialRequestApiCredential;
  session_id?: string;
  /** @maxLength 255 */
  name?: string;
}

export type PasskeyRegisterVerifyApiCredential = { [key: string]: unknown };

export interface PasskeyRegisterVerifyApi {
  credential: PasskeyRegisterVerifyApiCredential;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name?: string;
}

export interface PasskeyRegisterVerifyResponseApi {
  id: string;
  /** @minLength 1 */
  name: string;
  created_at: string;
  recovery_codes?: string[];
}

export interface WebAuthnCredentialApi {
  id: string;
  /** @minLength 1 */
  name: string;
  created_at: string;
  last_used_at: string;
}

export interface PasskeyRenameApi {
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
}

export interface PasskeyRenameResponseApi {
  id: string;
  /** @minLength 1 */
  name: string;
}

export interface PasswordResetConfirmRequestApi {
  /** @minLength 1 */
  new_password: string;
  /** @minLength 1 */
  repeat_password: string;
}

export interface PasswordResetInitiateRequestApi {
  /** @minLength 1 */
  email: string;
}

export type RedisKeyRequestApiValue = { [key: string]: unknown };

export interface RedisKeyRequestApi {
  /** @minLength 1 */
  access_token_id: string;
  /** @minLength 1 */
  key: string;
  value?: RedisKeyRequestApiValue;
  /** @minimum 1 */
  expiry?: number;
}

export type AccountsRedisSetResultApiValue = { [key: string]: unknown };

export interface AccountsRedisSetResultApi {
  /** @minLength 1 */
  message: string;
  /** @minLength 1 */
  key: string;
  value: AccountsRedisSetResultApiValue;
}

export interface AccountsRedisSetResponseApi {
  status: boolean;
  result: AccountsRedisSetResultApi;
}

export interface AccountsRedisDeleteResultApi {
  /** @minLength 1 */
  message: string;
  /** @minLength 1 */
  key: string;
}

export interface AccountsRedisDeleteResponseApi {
  status: boolean;
  result: AccountsRedisDeleteResultApi;
}

export interface SignupRequestApi {
  /** @minLength 1 */
  email: string;
  /** @minLength 1 */
  full_name: string;
  company_name?: string;
  password?: string;
  allow_email?: boolean;
  recaptcha_response?: string;
}

export interface TeamWorkspaceSummaryApi {
  id: string;
  /** @minLength 1 */
  name: string;
  /** @minLength 1 */
  role: string;
}

export interface TeamUserItemResponseApi {
  id: string;
  /** @minLength 1 */
  email: string;
  name: string;
  organization_role: string;
  organization?: AccountOrganizationDetailApi;
  /** @minLength 1 */
  created_at: string;
  /** @minLength 1 */
  status: string;
  role: string;
  goals?: string[];
  /** @minLength 1 */
  membership_type?: string;
  /** @minLength 1 */
  workspace_role?: string;
  workspace_member?: boolean;
  workspaces?: TeamWorkspaceSummaryApi[];
}

export interface TeamUsersResultApi {
  /** @minLength 1 */
  org_name: string;
  /** @minLength 1 */
  workspace_name?: string;
  results: TeamUserItemResponseApi[];
  total: number;
}

export interface TeamUsersResponseApi {
  status: boolean;
  result: TeamUsersResultApi;
}

export interface TeamWorkspaceInputApi {
  /** @maxLength 255 */
  name?: string;
  /** @maxLength 255 */
  display_name?: string;
  description?: string;
}

export type CreateMemberApiRole = typeof CreateMemberApiRole[keyof typeof CreateMemberApiRole];


export const CreateMemberApiRole = {
  Owner: 'Owner',
  Admin: 'Admin',
  Member: 'Member',
  Viewer: 'Viewer',
  workspace_admin: 'workspace_admin',
  workspace_member: 'workspace_member',
  workspace_viewer: 'workspace_viewer',
} as const;

export type CreateMemberApiOrganizationRole = typeof CreateMemberApiOrganizationRole[keyof typeof CreateMemberApiOrganizationRole];


export const CreateMemberApiOrganizationRole = {
  Owner: 'Owner',
  Admin: 'Admin',
  Member: 'Member',
  Viewer: 'Viewer',
} as const;

export interface CreateMemberApi {
  /**
     * @minLength 1
     * @maxLength 255
     */
  email: string;
  role?: CreateMemberApiRole;
  organization_role?: CreateMemberApiOrganizationRole;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
}

export interface TeamCreateRequestApi {
  /** @maxLength 255 */
  org_name?: string;
  workspace?: TeamWorkspaceInputApi;
  members?: CreateMemberApi[];
}

export interface TeamCreateErrorItemApi {
  index?: number;
  /** @minLength 1 */
  email?: string;
  /** @minLength 1 */
  error: string;
}

export interface TeamCreateResultApi {
  created_members: AccountUserItemResponseApi[];
  workspace: WorkspaceSummaryApi;
  errors?: TeamCreateErrorItemApi[];
}

export interface TeamCreateResponseApi {
  status: boolean;
  result: TeamCreateResultApi;
}

export interface TeamRemoveResultApi {
  /** @minLength 1 */
  message: string;
  /** @minLength 1 */
  removed_from: string;
}

export interface TeamRemoveResponseApi {
  status: boolean;
  result: TeamRemoveResultApi;
}

export interface LoginRequestApi {
  /** @minLength 1 */
  email: string;
  /** @minLength 1 */
  password: string;
  remember_me?: boolean;
  recaptcha_response?: string;
}

export interface TokenRefreshRequestApi {
  /** @minLength 1 */
  refresh: string;
  recaptcha_response?: string;
  localhost_bypass?: boolean;
}

export interface AccountsAccessTokenResponseApi {
  /** @minLength 1 */
  access: string;
}

export interface UserFullNameUpdateRequestApi {
  full_name?: string;
  name?: string;
}

export interface AccountsDirectMessageResponseApi {
  /** @minLength 1 */
  message: string;
}

export type UpdateUserApiOrganizationRole = typeof UpdateUserApiOrganizationRole[keyof typeof UpdateUserApiOrganizationRole];


export const UpdateUserApiOrganizationRole = {
  Owner: 'Owner',
  Admin: 'Admin',
  Member: 'Member',
  Viewer: 'Viewer',
  workspace_admin: 'workspace_admin',
  workspace_member: 'workspace_member',
  workspace_viewer: 'workspace_viewer',
} as const;

export interface UpdateUserApi {
  user_id: string;
  /** @minLength 1 */
  email?: string;
  /** @minLength 1 */
  name?: string;
  organization_role?: UpdateUserApiOrganizationRole;
}

export interface UserInfoOrganizationApi {
  id: string;
  /** @minLength 1 */
  name: string;
  display_name: string;
  ws_enabled?: boolean;
}

export interface UserInfoTwoFactorMethodsApi {
  totp: boolean;
  passkey: boolean;
}

export interface UserInfoResponseApi {
  id: string;
  /** @minLength 1 */
  email: string;
  name: string;
  organization_role: string;
  organization: UserInfoOrganizationApi;
  created_at: string;
  /** @minLength 1 */
  status: string;
  role: string;
  goals?: string[];
  remember_me: boolean;
  get_started_completed: boolean;
  onboarding_completed: boolean;
  ws_enabled: boolean;
  requires_org_setup?: boolean;
  default_workspace_id: string;
  default_workspace_name: string;
  default_workspace_display_name: string;
  default_workspace_role: string;
  org_level: number;
  ws_level: number;
  effective_level: number;
  has_2fa_enabled?: boolean;
  two_factor_methods?: UserInfoTwoFactorMethodsApi;
  org_2fa_required?: boolean;
  org_2fa_grace_ends_at?: string;
}

export interface DeactivateUserApi {
  user_id: string;
}

export interface DeactivateUserResultApi {
  /** @minLength 1 */
  message: string;
  user_id: string;
  /** @minLength 1 */
  user_email: string;
  user_name: string;
}

export interface DeactivateUserResponseApi {
  status: boolean;
  result: DeactivateUserResultApi;
}

export interface DeleteUserApi {
  user_id: string;
}

export interface DeleteUserResultApi {
  /** @minLength 1 */
  message: string;
  user_id: string;
  /** @minLength 1 */
  workspace?: string;
  /** @minLength 1 */
  level: string;
}

export interface DeleteUserResponseApi {
  status: boolean;
  result: DeleteUserResultApi;
}

export interface UserListItemResponseApi {
  id: string;
  name: string;
  /** @minLength 1 */
  email: string;
  role: string;
  /** @minLength 1 */
  status: string;
  start_date: string;
  last_updated_date: string;
  /** @minLength 1 */
  workspace_role?: string;
  workspace_member_since?: string;
  /** @minLength 1 */
  invited_by?: string;
}

export interface UserListPaginatedResponseApi {
  count: number;
  /** @minLength 1 */
  next: string;
  /** @minLength 1 */
  previous: string;
  results: UserListItemResponseApi[];
  total_pages: number;
  current_page: number;
}

export interface ResendInviteApi {
  user_id: string;
}

export interface ResendInviteResultApi {
  /** @minLength 1 */
  message: string;
  user_id: string;
  /** @minLength 1 */
  workspace?: string;
}

export interface ResendInviteResponseApi {
  status: boolean;
  result: ResendInviteResultApi;
}

export type UserRoleUpdateApiNewRole = typeof UserRoleUpdateApiNewRole[keyof typeof UserRoleUpdateApiNewRole];


export const UserRoleUpdateApiNewRole = {
  Owner: 'Owner',
  Admin: 'Admin',
  Member: 'Member',
  Viewer: 'Viewer',
  workspace_admin: 'workspace_admin',
  workspace_member: 'workspace_member',
  workspace_viewer: 'workspace_viewer',
} as const;

export interface UserRoleUpdateApi {
  user_id: string;
  new_role: UserRoleUpdateApiNewRole;
  workspace_id?: string;
}

export interface UserRoleUpdateResultApi {
  /** @minLength 1 */
  message: string;
  user_id: string;
  /** @minLength 1 */
  new_role: string;
  /** @minLength 1 */
  workspace_role?: string;
  /** @minLength 1 */
  workspace?: string;
  /** @minLength 1 */
  level: string;
}

export interface UserRoleUpdateResponseApi {
  status: boolean;
  result: UserRoleUpdateResultApi;
}

export type WorkspaceInviteApiRole = typeof WorkspaceInviteApiRole[keyof typeof WorkspaceInviteApiRole];


export const WorkspaceInviteApiRole = {
  workspace_member: 'workspace_member',
  workspace_admin: 'workspace_admin',
  workspace_viewer: 'workspace_viewer',
  Member: 'Member',
  Viewer: 'Viewer',
  Owner: 'Owner',
  Admin: 'Admin',
} as const;

export interface WorkspaceInviteApi {
  /** @minItems 1 */
  emails: string[];
  role?: WorkspaceInviteApiRole;
  select_all?: boolean;
  workspace_ids?: string[];
}

export interface WorkspaceInviteResultItemApi {
  /** @minLength 1 */
  email: string;
  /** @minLength 1 */
  status: string;
  workspaces: string[];
  select_all: boolean;
  total_workspaces: number;
}

export interface WorkspaceInviteErrorItemApi {
  /** @minLength 1 */
  email?: string;
  /** @minLength 1 */
  error: string;
}

export interface WorkspaceInviteResultApi {
  results: WorkspaceInviteResultItemApi[];
  total_invited: number;
  select_all: boolean;
  total_workspaces: number;
  errors?: WorkspaceInviteErrorItemApi[];
}

export interface WorkspaceInviteResponseApi {
  status: boolean;
  result: WorkspaceInviteResultApi;
}

export interface WorkspaceAdminSummaryApi {
  name: string;
  id: string;
}

export interface WorkspaceListItemResponseApi {
  id: string;
  /** @minLength 1 */
  name: string;
  display_name: string;
  admin_names?: WorkspaceAdminSummaryApi[];
  start_data?: string;
  last_update_date?: string;
  invite_link?: string;
  user_ws_level?: number;
  /** @minLength 1 */
  user_ws_role?: string;
}

export interface WorkspaceListPaginatedResponseApi {
  count: number;
  /** @minLength 1 */
  next: string;
  /** @minLength 1 */
  previous: string;
  results: WorkspaceListItemResponseApi[];
  total_pages: number;
  current_page: number;
}

export interface SwitchWorkspaceApi {
  old_workspace_id?: string;
  new_workspace_id: string;
}

export interface SwitchWorkspaceResultApi {
  /** @minLength 1 */
  message: string;
  workspace: WorkspaceSummaryApi;
  /** @minLength 1 */
  user_role: string;
  /** @minLength 1 */
  access_type: string;
  /** @minLength 1 */
  organization: string;
}

export interface SwitchWorkspaceResponseApi {
  status: boolean;
  result: SwitchWorkspaceResultApi;
}

export type WorkspaceMemberRowApiType = typeof WorkspaceMemberRowApiType[keyof typeof WorkspaceMemberRowApiType];


export const WorkspaceMemberRowApiType = {
  member: 'member',
  invite: 'invite',
} as const;

export interface WorkspaceMemberRowApi {
  id: string;
  name: string;
  /** @minLength 1 */
  email: string;
  ws_level?: number;
  /** @minLength 1 */
  ws_role?: string;
  org_level?: number;
  /** @minLength 1 */
  org_role?: string;
  /** @minLength 1 */
  status: string;
  created_at: string;
  type: WorkspaceMemberRowApiType;
  auto_access?: boolean;
}

export interface WorkspaceMemberListResultApi {
  results: WorkspaceMemberRowApi[];
  total: number;
  page: number;
  limit: number;
}

export interface WorkspaceMemberListResponseApi {
  status: boolean;
  result: WorkspaceMemberListResultApi;
}

export interface WorkspaceMemberRemoveApi {
  user_id: string;
}

export type WorkspaceMemberRoleUpdateApiWsLevel = typeof WorkspaceMemberRoleUpdateApiWsLevel[keyof typeof WorkspaceMemberRoleUpdateApiWsLevel];


export const WorkspaceMemberRoleUpdateApiWsLevel = {
  NUMBER_8: 8,
  NUMBER_3: 3,
  NUMBER_1: 1,
} as const;

export interface WorkspaceMemberRoleUpdateApi {
  user_id: string;
  ws_level: WorkspaceMemberRoleUpdateApiWsLevel;
}

export interface WorkspaceMemberRoleUpdateResultApi {
  /** @minLength 1 */
  message: string;
  user_id: string;
  ws_level: number;
  /** @minLength 1 */
  ws_role: string;
}

export interface WorkspaceMemberRoleUpdateResponseApi {
  status: boolean;
  result: WorkspaceMemberRoleUpdateResultApi;
}

export interface WorkspaceManagementItemApi {
  id: string;
  /** @minLength 1 */
  name: string;
  display_name: string;
  description: string;
  is_default: boolean;
  member_count: number;
  /** @minLength 1 */
  created_at: string;
  created_by: string;
}

export interface WorkspaceManagementListResultApi {
  /** @minLength 1 */
  organization: string;
  workspaces: WorkspaceManagementItemApi[];
  total: number;
}

export interface WorkspaceManagementListResponseApi {
  status: boolean;
  result: WorkspaceManagementListResultApi;
}

export interface WorkspaceCreateRequestApi {
  /** @minLength 1 */
  name: string;
  display_name?: string;
  description?: string;
  emails?: string[];
  role?: string;
}

export interface WorkspaceCreateUserErrorApi {
  /** @minLength 1 */
  email: string;
  /** @minLength 1 */
  error: string;
}

export interface WorkspaceCreateResultApi {
  workspace: WorkspaceSummaryApi;
  /** @minLength 1 */
  message: string;
  added_users: string[];
  created_users: string[];
  total_users_added: number;
  total_users_created: number;
  failed_users: WorkspaceCreateUserErrorApi[];
  other_org_users: WorkspaceCreateUserErrorApi[];
}

export interface WorkspaceCreateResponseApi {
  status: boolean;
  result: WorkspaceCreateResultApi;
}

export interface WorkspaceUpdateRequestApi {
  name?: string;
  display_name?: string;
  description?: string;
}

export interface WorkspaceUpdateResultApi {
  workspace: WorkspaceSummaryApi;
  /** @minLength 1 */
  message: string;
}

export interface WorkspaceUpdateResponseApi {
  status: boolean;
  result: WorkspaceUpdateResultApi;
}

export interface WorkspaceDeleteResultApi {
  /** @minLength 1 */
  message: string;
}

export interface WorkspaceDeleteResponseApi {
  status: boolean;
  result: WorkspaceDeleteResultApi;
}

export interface WorkspaceMemberItemApi {
  user_id: string;
  /** @minLength 1 */
  email: string;
  name: string;
  /** @minLength 1 */
  role: string;
  /** @minLength 1 */
  joined_at: string;
  /** @minLength 1 */
  invited_by: string;
}

export interface WorkspaceMembersListResultApi {
  workspace: WorkspaceSummaryApi;
  members: WorkspaceMemberItemApi[];
  total: number;
}

export interface WorkspaceMembersListResponseApi {
  status: boolean;
  result: WorkspaceMembersListResultApi;
}

export type WorkspaceMembersRequestApiUsersItem = {[key: string]: string};

export interface WorkspaceMembersRequestApi {
  users: WorkspaceMembersRequestApiUsersItem[];
}

export interface WorkspaceMemberAddedItemApi {
  /** @minLength 1 */
  email: string;
  name: string;
  /** @minLength 1 */
  role: string;
  /** @minLength 1 */
  action: string;
}

export interface WorkspaceMemberAddErrorApi {
  email?: string;
  /** @minLength 1 */
  error: string;
}

export interface WorkspaceMembersAddResultApi {
  workspace: WorkspaceSummaryApi;
  added_users: WorkspaceMemberAddedItemApi[];
  errors?: WorkspaceMemberAddErrorApi[];
}

export interface WorkspaceMembersAddResponseApi {
  status: boolean;
  result: WorkspaceMembersAddResultApi;
}

export interface WorkspaceMemberRemoveResponseApi {
  status: boolean;
  result: AccountsMessageResultApi;
}

export type NodeExecutionDataApiPayload = { [key: string]: unknown };

export type NodeExecutionDataApiValidationErrors = { [key: string]: unknown };

export interface NodeExecutionDataApi {
  readonly port_id?: string;
  /** @minLength 1 */
  readonly port_key?: string;
  /** @minLength 1 */
  readonly port_direction?: string;
  readonly payload?: NodeExecutionDataApiPayload;
  readonly is_valid?: boolean;
  readonly validation_errors?: NodeExecutionDataApiValidationErrors;
}

export interface NodeExecutionDetailResultApi {
  readonly node_execution_id?: string;
  readonly node_id?: string;
  /** @minLength 1 */
  readonly node_name?: string;
  /** @minLength 1 */
  readonly node_type?: string;
  /** @minLength 1 */
  readonly status?: string;
  readonly started_at?: string;
  readonly completed_at?: string;
  readonly duration_seconds?: number;
  /** @minLength 1 */
  readonly error_message?: string;
  readonly inputs?: readonly NodeExecutionDataApi[];
  readonly outputs?: readonly NodeExecutionDataApi[];
}

export interface NodeExecutionDetailResponseApi {
  status?: boolean;
  result: NodeExecutionDetailResultApi;
}

export interface AgentPlaygroundErrorResponseApi {
  status?: boolean;
  result?: string;
  message?: string;
  error?: string;
}

export interface UserBriefApi {
  readonly id?: string;
  /** @minLength 1 */
  readonly name?: string;
  /** @minLength 1 */
  readonly email?: string;
}

export interface GraphListApi {
  readonly id?: string;
  /**
     * Display name
     * @minLength 1
     */
  readonly name?: string;
  /** @minLength 1 */
  readonly description?: string;
  readonly is_template?: boolean;
  readonly created_at?: string;
  readonly updated_at?: string;
  created_by?: UserBriefApi;
  readonly collaborators?: readonly UserBriefApi[];
  readonly active_version_id?: string;
  readonly active_version_number?: number;
  readonly node_count?: number;
}

export interface GraphCreateApi {
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  description?: string;
}

export interface TraceToGraphRequestApi {
  trace_id: string;
}

export interface TraceToGraphResultApi {
  graph_id: string;
  version_id: string;
}

export interface TraceToGraphResponseApi {
  status?: boolean;
  result: TraceToGraphResultApi;
}

export interface CellUpdateApi {
  value?: string;
}

export interface ExecuteRequestApi {
  /** Optional list of row IDs to execute. If omitted, all rows are executed. */
  row_ids?: string[];
  /** @minLength 1 */
  task_queue?: string;
}

export type GraphExecutionListApiStatus = typeof GraphExecutionListApiStatus[keyof typeof GraphExecutionListApiStatus];


export const GraphExecutionListApiStatus = {
  pending: 'pending',
  running: 'running',
  success: 'success',
  failed: 'failed',
  cancelled: 'cancelled',
} as const;

export interface GraphExecutionListApi {
  readonly id?: string;
  readonly status?: GraphExecutionListApiStatus;
  readonly started_at?: string;
  readonly completed_at?: string;
  readonly graph_version?: string;
  readonly created_at?: string;
}

export interface PaginationMetadataApi {
  total_count: number;
  current_page: number;
  page_size: number;
  total_pages: number;
  next_page?: number;
}

export interface GraphExecutionListResultApi {
  readonly executions?: readonly GraphExecutionListApi[];
  metadata?: PaginationMetadataApi;
}

export interface GraphExecutionListResponseApi {
  status?: boolean;
  result: GraphExecutionListResultApi;
}

export type GraphExecutionDetailResultApiStatus = typeof GraphExecutionDetailResultApiStatus[keyof typeof GraphExecutionDetailResultApiStatus];


export const GraphExecutionDetailResultApiStatus = {
  pending: 'pending',
  running: 'running',
  success: 'success',
  failed: 'failed',
  cancelled: 'cancelled',
} as const;

export type GraphExecutionDetailResultApiInputPayload = { [key: string]: unknown };

export type GraphExecutionDetailResultApiOutputPayload = { [key: string]: unknown };

export type GraphExecutionDetailResultApiNodesItem = { [key: string]: unknown };

export type GraphExecutionDetailResultApiNodeConnectionsItem = { [key: string]: unknown };

export interface GraphExecutionDetailResultApi {
  readonly id?: string;
  readonly status?: GraphExecutionDetailResultApiStatus;
  readonly input_payload?: GraphExecutionDetailResultApiInputPayload;
  readonly output_payload?: GraphExecutionDetailResultApiOutputPayload;
  readonly started_at?: string;
  readonly completed_at?: string;
  /** @minLength 1 */
  readonly error_message?: string;
  readonly nodes?: readonly GraphExecutionDetailResultApiNodesItem[];
  readonly node_connections?: readonly GraphExecutionDetailResultApiNodeConnectionsItem[];
}

export interface GraphExecutionDetailResponseApi {
  status?: boolean;
  result: GraphExecutionDetailResultApi;
}

export interface GraphDetailApi {
  readonly id?: string;
  /**
     * Display name
     * @minLength 1
     */
  readonly name?: string;
  /** @minLength 1 */
  readonly description?: string;
  readonly is_template?: boolean;
  readonly created_at?: string;
  readonly updated_at?: string;
  readonly active_version?: string;
}

export interface GraphUpdateApi {
  /**
     * @minLength 1
     * @maxLength 255
     */
  name?: string;
  description?: string;
}

export interface CreateNodeConnectionApi {
  /** FE-generated UUID */
  id: string;
  source_node_id: string;
  target_node_id: string;
}

export type CreateNodeApiType = typeof CreateNodeApiType[keyof typeof CreateNodeApiType];


export const CreateNodeApiType = {
  subgraph: 'subgraph',
  atomic: 'atomic',
} as const;

export type CreateNodeApiPosition = { [key: string]: unknown };

/**
 * Type of content item
 */
export type MessageContentItemApiType = typeof MessageContentItemApiType[keyof typeof MessageContentItemApiType];


export const MessageContentItemApiType = {
  text: 'text',
  image_url: 'image_url',
  audio_url: 'audio_url',
  pdf_url: 'pdf_url',
} as const;

/**
 * Array of content items
 */
export interface MessageContentItemApi {
  /** Type of content item */
  type: MessageContentItemApiType;
  /** Text content (required when type=text) */
  text?: string;
  /**
     * Image URL (required when type=image_url)
     * @minLength 1
     */
  image_url?: string;
  /**
     * Audio URL (required when type=audio_url)
     * @minLength 1
     */
  audio_url?: string;
  /**
     * PDF URL (required when type=pdf_url)
     * @minLength 1
     */
  pdf_url?: string;
}

/**
 * Array of message objects with id, role, and content array
 */
export interface MessageApi {
  /**
     * Unique identifier for the message (frontend-provided)
     * @minLength 1
     */
  id: string;
  /**
     * Message role (e.g., 'system', 'user', 'assistant')
     * @minLength 1
     */
  role: string;
  /** Array of content items */
  content: MessageContentItemApi[];
}

/**
 * String or JSON object.
 */
export type PromptTemplateDataApiResponseFormat = string | { [key: string]: unknown };

/**
 * JSON Schema (Draft 7) for structured outputs. Required when response_format='json_schema'. Example: {'type': 'object', 'properties': {...}, 'required': [...]}
 */
export type PromptTemplateDataApiResponseSchema = { [key: string]: unknown };

export type PromptTemplateDataApiToolsItem = {[key: string]: string};

export type PromptTemplateDataApiToolChoice = { [key: string]: unknown };

export type PromptTemplateDataApiModelDetail = {[key: string]: string};

export type PromptTemplateDataApiVariableNames = {[key: string]: string};

export type PromptTemplateDataApiMetadata = {[key: string]: string};

export interface PromptTemplateDataApi {
  prompt_template_id?: string;
  prompt_version_id?: string;
  /** Array of message objects with id, role, and content array */
  messages: MessageApi[];
  /** String or JSON object. */
  response_format?: PromptTemplateDataApiResponseFormat;
  /** JSON Schema (Draft 7) for structured outputs. Required when response_format='json_schema'. Example: {'type': 'object', 'properties': {...}, 'required': [...]} */
  response_schema?: PromptTemplateDataApiResponseSchema;
  model?: string;
  temperature?: number;
  max_tokens?: number;
  top_p?: number;
  frequency_penalty?: number;
  presence_penalty?: number;
  output_format?: string;
  tools?: PromptTemplateDataApiToolsItem[];
  tool_choice?: PromptTemplateDataApiToolChoice;
  model_detail?: PromptTemplateDataApiModelDetail;
  variable_names?: PromptTemplateDataApiVariableNames;
  metadata?: PromptTemplateDataApiMetadata;
  commit_message?: string;
  /** Template format: 'mustache' or 'jinja' */
  template_format?: string;
  save_prompt_version?: boolean;
}

export type PortCreateApiDirection = typeof PortCreateApiDirection[keyof typeof PortCreateApiDirection];


export const PortCreateApiDirection = {
  input: 'input',
  output: 'output',
} as const;

export type PortCreateApiDataSchema = { [key: string]: unknown };

export interface PortCreateApi {
  /** FE-generated UUID */
  id: string;
  /**
     * @minLength 1
     * @maxLength 100
     */
  key: string;
  /**
     * @minLength 1
     * @maxLength 100
     */
  display_name: string;
  direction: PortCreateApiDirection;
  data_schema?: PortCreateApiDataSchema;
  ref_port_id?: string;
}

/**
 * List of input mappings from port display_name to source reference
 */
export interface InputMappingApi {
  /**
     * Input port display_name
     * @minLength 1
     */
  key: string;
  /**
     * Source reference in format "NodeName.port_display_name" or null
     * @minLength 1
     */
  value?: string;
}

export interface CreateNodeApi {
  /** FE-generated UUID for the node */
  id: string;
  type: CreateNodeApiType;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  node_template_id?: string;
  ref_graph_version_id?: string;
  position?: CreateNodeApiPosition;
  source_node_id?: string;
  prompt_template?: PromptTemplateDataApi;
  ports?: PortCreateApi[];
  /** List of input mappings from port display_name to source reference */
  input_mappings?: InputMappingApi[];
}

/**
 * 'subgraph' for subgraph nodes, 'atomic' for nodes using a NodeTemplate
 */
export type NodeReadApiType = typeof NodeReadApiType[keyof typeof NodeReadApiType];


export const NodeReadApiType = {
  subgraph: 'subgraph',
  atomic: 'atomic',
} as const;

/**
 * Node-specific configuration (validated against node_template.config_schema for atomic nodes)
 */
export type NodeReadApiConfig = { [key: string]: unknown };

/**
 * UI coordinates {"x": 0, "y": 0}
 */
export type NodeReadApiPosition = { [key: string]: unknown };

export type PortReadApiDirection = typeof PortReadApiDirection[keyof typeof PortReadApiDirection];


export const PortReadApiDirection = {
  input: 'input',
  output: 'output',
} as const;

/**
 * JSON Schema for validation
 */
export type PortReadApiDataSchema = { [key: string]: unknown };

export type PortReadApiDefaultValue = { [key: string]: unknown };

export type PortReadApiMetadata = { [key: string]: unknown };

export interface PortReadApi {
  readonly id?: string;
  /**
     * Identifier (e.g., 'prompt', 'result')
     * @minLength 1
     */
  readonly key?: string;
  /**
     * User-facing name for the port
     * @minLength 1
     */
  readonly display_name?: string;
  readonly direction?: PortReadApiDirection;
  /** JSON Schema for validation */
  readonly data_schema?: PortReadApiDataSchema;
  readonly required?: boolean;
  readonly default_value?: PortReadApiDefaultValue;
  readonly metadata?: PortReadApiMetadata;
  readonly ref_port_id?: string;
}

export interface NodeReadApi {
  readonly id?: string;
  /** 'subgraph' for subgraph nodes, 'atomic' for nodes using a NodeTemplate */
  readonly type?: NodeReadApiType;
  /**
     * Display name
     * @minLength 1
     */
  readonly name?: string;
  /** Node-specific configuration (validated against node_template.config_schema for atomic nodes) */
  readonly config?: NodeReadApiConfig;
  /** UI coordinates {"x": 0, "y": 0} */
  readonly position?: NodeReadApiPosition;
  readonly node_template_id?: string;
  readonly ref_graph_version_id?: string;
  /** @minLength 1 */
  readonly ref_graph_name?: string;
  readonly ref_graph_id?: string;
  readonly prompt_template?: string;
  readonly node_connection?: string;
  readonly input_mappings?: string;
  readonly ports?: readonly PortReadApi[];
}

export type UpdateNodeApiPosition = { [key: string]: unknown };

export interface UpdateNodeApi {
  /**
     * @minLength 1
     * @maxLength 255
     */
  name?: string;
  position?: UpdateNodeApiPosition;
  prompt_template?: PromptTemplateDataApi;
  ref_graph_version_id?: string;
  /** List of input mappings from port display_name to source reference */
  input_mappings?: InputMappingApi[];
  /** Replace all OUTPUT ports with this new set (input ports preserved) */
  ports?: PortCreateApi[];
}

export interface UpdatePortApi {
  /**
     * @minLength 1
     * @maxLength 100
     */
  display_name: string;
}

export type NodeTemplateListApiCategories = { [key: string]: unknown };

export interface NodeTemplateListApi {
  readonly id?: string;
  /** @minLength 1 */
  readonly name?: string;
  /** @minLength 1 */
  readonly display_name?: string;
  /** @minLength 1 */
  readonly description?: string;
  /** @minLength 1 */
  readonly icon?: string;
  readonly categories?: NodeTemplateListApiCategories;
}

export type NodeTemplateDetailApiCategories = { [key: string]: unknown };

export type NodeTemplateDetailApiInputDefinition = { [key: string]: unknown };

export type NodeTemplateDetailApiOutputDefinition = { [key: string]: unknown };

export type NodeTemplateDetailApiInputMode = typeof NodeTemplateDetailApiInputMode[keyof typeof NodeTemplateDetailApiInputMode];


export const NodeTemplateDetailApiInputMode = {
  strict: 'strict',
  extensible: 'extensible',
  dynamic: 'dynamic',
} as const;

export type NodeTemplateDetailApiOutputMode = typeof NodeTemplateDetailApiOutputMode[keyof typeof NodeTemplateDetailApiOutputMode];


export const NodeTemplateDetailApiOutputMode = {
  strict: 'strict',
  extensible: 'extensible',
  dynamic: 'dynamic',
} as const;

/**
 * JSON Schema for Node.config validation
 */
export type NodeTemplateDetailApiConfigSchema = { [key: string]: unknown };

export interface NodeTemplateDetailApi {
  readonly id?: string;
  /** @minLength 1 */
  readonly name?: string;
  /** @minLength 1 */
  readonly display_name?: string;
  /** @minLength 1 */
  readonly description?: string;
  /** @minLength 1 */
  readonly icon?: string;
  readonly categories?: NodeTemplateDetailApiCategories;
  readonly input_definition?: NodeTemplateDetailApiInputDefinition;
  readonly output_definition?: NodeTemplateDetailApiOutputDefinition;
  readonly input_mode?: NodeTemplateDetailApiInputMode;
  readonly output_mode?: NodeTemplateDetailApiOutputMode;
  /** JSON Schema for Node.config validation */
  readonly config_schema?: NodeTemplateDetailApiConfigSchema;
}

export type AgentccRequestLogApiMetadata = { [key: string]: unknown };

export interface AgentccRequestLogApi {
  readonly id?: string;
  /** @minLength 1 */
  readonly request_id?: string;
  /** @minLength 1 */
  readonly model?: string;
  /** @minLength 1 */
  readonly provider?: string;
  /** @minLength 1 */
  readonly resolved_model?: string;
  readonly latency_ms?: number;
  readonly started_at?: string;
  readonly input_tokens?: number;
  readonly output_tokens?: number;
  readonly total_tokens?: number;
  readonly cost?: string;
  readonly status_code?: number;
  readonly is_stream?: boolean;
  readonly is_error?: boolean;
  /** @minLength 1 */
  readonly error_message?: string;
  readonly cache_hit?: boolean;
  readonly fallback_used?: boolean;
  readonly guardrail_triggered?: boolean;
  /** @minLength 1 */
  readonly api_key_id?: string;
  /** @minLength 1 */
  readonly user_id?: string;
  /** @minLength 1 */
  readonly session_id?: string;
  /** @minLength 1 */
  readonly routing_strategy?: string;
  readonly metadata?: AgentccRequestLogApiMetadata;
  readonly organization?: string;
  readonly workspace?: string;
  readonly created_at?: string;
}

export type AgentccAPIKeyApiStatus = typeof AgentccAPIKeyApiStatus[keyof typeof AgentccAPIKeyApiStatus];


export const AgentccAPIKeyApiStatus = {
  active: 'active',
  revoked: 'revoked',
  expired: 'expired',
} as const;

export type AgentccAPIKeyApiAllowedModels = { [key: string]: unknown };

export type AgentccAPIKeyApiAllowedProviders = { [key: string]: unknown };

export type AgentccAPIKeyApiMetadata = { [key: string]: unknown };

export interface AgentccAPIKeyApi {
  readonly id?: string;
  project?: string;
  user?: string;
  /** @minLength 1 */
  readonly gateway_key_id?: string;
  /** @minLength 1 */
  readonly key_prefix?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  /** @maxLength 255 */
  owner?: string;
  readonly status?: AgentccAPIKeyApiStatus;
  allowed_models?: AgentccAPIKeyApiAllowedModels;
  allowed_providers?: AgentccAPIKeyApiAllowedProviders;
  metadata?: AgentccAPIKeyApiMetadata;
  last_used_at?: string;
  expires_at?: string;
  readonly organization?: string;
  readonly workspace?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
}

export type APIKeyBulkItemApiMetadata = {[key: string]: string};

export interface APIKeyBulkItemApi {
  /** @minLength 1 */
  id: string;
  /** @minLength 1 */
  name: string;
  owner: string;
  /** @minLength 1 */
  key_hash: string;
  models: string[];
  providers: string[];
  metadata: APIKeyBulkItemApiMetadata;
}

export interface APIKeyBulkResponseApi {
  status: boolean;
  result: APIKeyBulkItemApi[];
}

export type AgentccErrorResponseApiType = typeof AgentccErrorResponseApiType[keyof typeof AgentccErrorResponseApiType];


export const AgentccErrorResponseApiType = {
  validation_error: 'validation_error',
  authentication_error: 'authentication_error',
  payment_required: 'payment_required',
  entitlement_error: 'entitlement_error',
  permission_error: 'permission_error',
  not_found: 'not_found',
  conflict: 'conflict',
  client_error: 'client_error',
  rate_limit: 'rate_limit',
  server_error: 'server_error',
  service_unavailable: 'service_unavailable',
  timeout: 'timeout',
  api_error: 'api_error',
} as const;

export type AgentccErrorResponseApiDetails = {[key: string]: string[]};

export interface AgentccErrorResponseApi {
  status?: boolean;
  type?: AgentccErrorResponseApiType;
  code?: string;
  detail?: string;
  result?: string;
  message?: string;
  error?: string;
  attr?: string;
  details?: AgentccErrorResponseApiDetails;
}

export type AgentccBlocklistApiWords = { [key: string]: unknown };

export interface AgentccBlocklistApi {
  readonly id?: string;
  readonly organization?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  description?: string;
  words?: AgentccBlocklistApiWords;
  is_active?: boolean;
  readonly created_at?: string;
  readonly updated_at?: string;
}

export type AgentccCustomPropertySchemaApiPropertyType = typeof AgentccCustomPropertySchemaApiPropertyType[keyof typeof AgentccCustomPropertySchemaApiPropertyType];


export const AgentccCustomPropertySchemaApiPropertyType = {
  string: 'string',
  number: 'number',
  boolean: 'boolean',
  enum: 'enum',
} as const;

export type AgentccCustomPropertySchemaApiAllowedValues = { [key: string]: unknown };

export type AgentccCustomPropertySchemaApiDefaultValue = { [key: string]: unknown };

export interface AgentccCustomPropertySchemaApi {
  readonly id?: string;
  readonly organization?: string;
  project?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  description?: string;
  property_type?: AgentccCustomPropertySchemaApiPropertyType;
  required?: boolean;
  allowed_values?: AgentccCustomPropertySchemaApiAllowedValues;
  default_value?: AgentccCustomPropertySchemaApiDefaultValue;
  readonly created_at?: string;
  readonly updated_at?: string;
}

export type AgentccEmailAlertApiRecipients = { [key: string]: unknown };

export type AgentccEmailAlertApiEvents = { [key: string]: unknown };

export type AgentccEmailAlertApiThresholds = { [key: string]: unknown };

export type AgentccEmailAlertApiProvider = typeof AgentccEmailAlertApiProvider[keyof typeof AgentccEmailAlertApiProvider];


export const AgentccEmailAlertApiProvider = {
  sendgrid: 'sendgrid',
  resend: 'resend',
  smtp: 'smtp',
} as const;

export interface AgentccEmailAlertApi {
  readonly id?: string;
  readonly organization?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  recipients?: AgentccEmailAlertApiRecipients;
  events?: AgentccEmailAlertApiEvents;
  thresholds?: AgentccEmailAlertApiThresholds;
  provider?: AgentccEmailAlertApiProvider;
  readonly provider_config?: string;
  is_active?: boolean;
  /**
     * @minimum -2147483648
     * @maximum 2147483647
     */
  cooldown_minutes?: number;
  readonly last_triggered_at?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
}

export interface GatewaySummaryResultApi {
  /** @minLength 1 */
  id: string;
  /** @minLength 1 */
  name: string;
  /** @minLength 1 */
  base_url: string;
  /** @minLength 1 */
  status: string;
  provider_count?: number;
  model_count?: number;
}

export interface GatewayListResponseApi {
  status: boolean;
  result: GatewaySummaryResultApi[];
}

export type AgentccListResultResponseApiResultItem = { [key: string]: unknown };

export interface AgentccListResultResponseApi {
  status: boolean;
  result: AgentccListResultResponseApiResultItem[];
}

export interface GatewayDetailResponseApi {
  status: boolean;
  result: GatewaySummaryResultApi;
}

export interface GatewayBatchRequestApi {
  /** @minLength 1 */
  batch_id: string;
}

export interface GatewayBatchCancelResultApi {
  /** @minLength 1 */
  batch_id: string;
  /** @minLength 1 */
  status: string;
}

export interface GatewayBatchCancelResponseApi {
  status: boolean;
  result: GatewayBatchCancelResultApi;
}

export type GatewayConfigProviderApiModelsItem = { [key: string]: unknown };

export interface GatewayConfigProviderApi {
  id: string;
  /** @minLength 1 */
  name: string;
  /** @minLength 1 */
  display_name: string;
  base_url: string;
  /** Gateway protocol adapter name. This intentionally remains a string because self-hosted/custom providers may register adapters outside the built-in openai/anthropic/gemini/google set. */
  api_format: string;
  models: GatewayConfigProviderApiModelsItem[];
  is_active: boolean;
  default_timeout: number;
  max_concurrent: number;
  conn_pool_size: number;
}

export interface GatewayStatusApi {
  /** @minLength 1 */
  status: string;
}

export type GatewayConfigResultApiGuardrails = { [key: string]: unknown };

export type GatewayConfigResultApiRouting = { [key: string]: unknown };

export type GatewayConfigResultApiCache = { [key: string]: unknown };

export type GatewayConfigResultApiRateLimiting = { [key: string]: unknown };

export type GatewayConfigResultApiBudgets = { [key: string]: unknown };

export type GatewayConfigResultApiCostTracking = { [key: string]: unknown };

export type GatewayConfigResultApiIpAcl = { [key: string]: unknown };

export type GatewayConfigResultApiAlerting = { [key: string]: unknown };

export type GatewayConfigResultApiPrivacy = { [key: string]: unknown };

export type GatewayConfigResultApiToolPolicy = { [key: string]: unknown };

export type GatewayConfigResultApiMcp = { [key: string]: unknown };

export type GatewayConfigResultApiA2a = { [key: string]: unknown };

export type GatewayConfigResultApiAudit = { [key: string]: unknown };

export type GatewayConfigResultApiModelDatabase = { [key: string]: unknown };

export type GatewayConfigResultApiModelMap = { [key: string]: unknown };

export type GatewayConfigResultApiProviders = {[key: string]: GatewayConfigProviderApi};

export interface GatewayConfigResultApi {
  id?: string;
  organization?: string;
  version?: number;
  guardrails?: GatewayConfigResultApiGuardrails;
  routing?: GatewayConfigResultApiRouting;
  cache?: GatewayConfigResultApiCache;
  rate_limiting?: GatewayConfigResultApiRateLimiting;
  budgets?: GatewayConfigResultApiBudgets;
  cost_tracking?: GatewayConfigResultApiCostTracking;
  ip_acl?: GatewayConfigResultApiIpAcl;
  alerting?: GatewayConfigResultApiAlerting;
  privacy?: GatewayConfigResultApiPrivacy;
  tool_policy?: GatewayConfigResultApiToolPolicy;
  mcp?: GatewayConfigResultApiMcp;
  a2a?: GatewayConfigResultApiA2a;
  audit?: GatewayConfigResultApiAudit;
  model_database?: GatewayConfigResultApiModelDatabase;
  model_map?: GatewayConfigResultApiModelMap;
  is_active?: boolean;
  created_by?: string;
  change_description?: string;
  created_at?: string;
  updated_at?: string;
  providers: GatewayConfigResultApiProviders;
  gateway: GatewayStatusApi;
}

export interface GatewayConfigResponseApi {
  status: boolean;
  result: GatewayConfigResultApi;
}

export interface GatewayBatchSummaryApi {
  total_cost: number;
  total_input_tokens: number;
  total_output_tokens: number;
  completed: number;
  failed: number;
  cancelled: number;
}

export type GatewayBatchDetailResultApiResultsItem = { [key: string]: unknown };

export interface GatewayBatchDetailResultApi {
  /** @minLength 1 */
  batch_id: string;
  /** @minLength 1 */
  status: string;
  total: number;
  max_concurrency: number;
  created_at: string;
  completed_at?: string;
  results?: GatewayBatchDetailResultApiResultsItem[];
  summary?: GatewayBatchSummaryApi;
}

export interface GatewayBatchDetailResponseApi {
  status: boolean;
  result: GatewayBatchDetailResultApi;
}

export interface AgentccEmptyRequestApi { [key: string]: unknown }

export type GatewayConfiguredProviderApiModelsItem = { [key: string]: unknown };

export interface GatewayConfiguredProviderApi {
  /** @minLength 1 */
  name: string;
  display_name?: string;
  models?: GatewayConfiguredProviderApiModelsItem[];
  status?: string;
}

export interface GatewayConfiguredProvidersApi {
  providers: GatewayConfiguredProviderApi[];
}

export type GatewayHealthResultApiHealth = { [key: string]: unknown };

export interface GatewayHealthResultApi {
  /** @minLength 1 */
  status: string;
  health?: GatewayHealthResultApiHealth;
  providers: GatewayConfiguredProvidersApi;
  provider_count: number;
  model_count: number;
}

export interface GatewayHealthResponseApi {
  status: boolean;
  result: GatewayHealthResultApi;
}

export type GatewayMCPStatusResultApiServersItem = { [key: string]: unknown };

export interface GatewayMCPStatusResultApi {
  enabled: boolean;
  sessions: number;
  tools: number;
  resources: number;
  prompts: number;
  /** Gateway MCP server statuses are adapter-specific objects; the Django fallback normalizes configured servers to objects with id and status. */
  servers: GatewayMCPStatusResultApiServersItem[];
}

export interface GatewayMCPStatusResponseApi {
  status: boolean;
  result: GatewayMCPStatusResultApi;
}

export type GatewayProviderStatusApiModelsItem = { [key: string]: unknown };

export interface GatewayProviderStatusApi {
  /**
     * Provider key/name used by the gateway, not a database UUID.
     * @minLength 1
     */
  id: string;
  /** @minLength 1 */
  name: string;
  /** @minLength 1 */
  status: string;
  healthy: boolean;
  /** @minLength 1 */
  circuit_state: string;
  display_name?: string;
  base_url?: string;
  /** Gateway protocol adapter name. This intentionally remains a string because self-hosted/custom providers may register adapters outside the built-in openai/anthropic/gemini/google set. */
  api_format?: string;
  models?: GatewayProviderStatusApiModelsItem[];
  request_count?: number;
  avg_latency?: number;
  error_rate?: number;
}

export interface GatewayProvidersResultApi {
  providers: GatewayProviderStatusApi[];
}

export interface GatewayProvidersResponseApi {
  status: boolean;
  result: GatewayProvidersResultApi;
}

export interface GatewayMutationResultApi {
  status?: boolean;
  version?: number;
  gateway_synced?: boolean;
  gateway_warning?: string;
  action?: string;
  provider?: string;
  guardrail?: string;
  budget?: string;
  server?: string;
  enabled?: boolean;
}

export interface GatewayMutationResponseApi {
  status: boolean;
  result: GatewayMutationResultApi;
}

export interface GatewayBudgetRemoveRequestApi {
  /** @minLength 1 */
  level: string;
}

export interface GatewayMCPServerRemoveRequestApi {
  /** @minLength 1 */
  server_id: string;
}

export interface GatewayNameRequestApi {
  /** @minLength 1 */
  name: string;
}

export type GatewayBudgetSetRequestApiConfig = {[key: string]: { [key: string]: unknown }};

export interface GatewayBudgetSetRequestApi {
  /** @minLength 1 */
  level: string;
  config: GatewayBudgetSetRequestApiConfig;
}

export type GatewayBatchSubmitRequestApiRequestsItem = {[key: string]: { [key: string]: unknown }};

export interface GatewayBatchSubmitRequestApi {
  requests: GatewayBatchSubmitRequestApiRequestsItem[];
  /** @minimum 1 */
  max_concurrency?: number;
}

export interface GatewayBatchSubmitResultApi {
  /** @minLength 1 */
  batch_id: string;
  /** @minLength 1 */
  status: string;
  total: number;
  max_concurrency: number;
  created_at: string;
}

export interface GatewayBatchSubmitResponseApi {
  status: boolean;
  result: GatewayBatchSubmitResultApi;
}

export type GatewayMCPToolTestRequestApiArguments = {[key: string]: { [key: string]: unknown }};

export interface GatewayMCPToolTestRequestApi {
  /** @minLength 1 */
  name: string;
  arguments?: GatewayMCPToolTestRequestApiArguments;
}

export interface GatewayMCPToolTestContentApi {
  /** @minLength 1 */
  type: string;
  text?: string;
  data?: string;
  mimeType?: string;
}

export type GatewayMCPToolTestResultApiGuardrailPre = typeof GatewayMCPToolTestResultApiGuardrailPre[keyof typeof GatewayMCPToolTestResultApiGuardrailPre];


export const GatewayMCPToolTestResultApiGuardrailPre = {
  pass: 'pass',
  blocked: 'blocked',
  skipped: 'skipped',
} as const;

export type GatewayMCPToolTestResultApiGuardrailPost = typeof GatewayMCPToolTestResultApiGuardrailPost[keyof typeof GatewayMCPToolTestResultApiGuardrailPost];


export const GatewayMCPToolTestResultApiGuardrailPost = {
  pass: 'pass',
  blocked: 'blocked',
  skipped: 'skipped',
} as const;

export interface GatewayMCPToolTestResultApi {
  content?: GatewayMCPToolTestContentApi[];
  is_error?: boolean;
  duration_ms?: number;
  guardrail_pre?: GatewayMCPToolTestResultApiGuardrailPre;
  guardrail_post?: GatewayMCPToolTestResultApiGuardrailPost;
  error?: string;
  server?: string;
}

export interface GatewayMCPToolTestResponseApi {
  status: boolean;
  result: GatewayMCPToolTestResultApi;
}

export interface GatewayPlaygroundTestRequestApi {
  /** @minLength 1 */
  prompt: string;
  model?: string;
  system_prompt?: string;
}

export type GatewayPlaygroundTestResultApiBody = { [key: string]: unknown };

export type GatewayPlaygroundTestResultApiGuardrailHeaders = {[key: string]: string};

export interface GatewayPlaygroundTestResultApi {
  status_code: number;
  body: GatewayPlaygroundTestResultApiBody;
  guardrail_headers: GatewayPlaygroundTestResultApiGuardrailHeaders;
  /** @minLength 1 */
  model: string;
  blocked: boolean;
  warned: boolean;
}

export interface GatewayPlaygroundTestResponseApi {
  status: boolean;
  result: GatewayPlaygroundTestResultApi;
}

export interface GatewayToggleGuardrailRequestApi {
  /** @minLength 1 */
  name: string;
  enabled: boolean;
}

export type GatewayConfigPatchRequestApiGuardrails = {[key: string]: { [key: string]: unknown }};

export type GatewayConfigPatchRequestApiRouting = {[key: string]: { [key: string]: unknown }};

export type GatewayConfigPatchRequestApiCache = {[key: string]: { [key: string]: unknown }};

export type GatewayConfigPatchRequestApiRateLimiting = {[key: string]: { [key: string]: unknown }};

export type GatewayConfigPatchRequestApiBudgets = {[key: string]: { [key: string]: unknown }};

export type GatewayConfigPatchRequestApiCostTracking = {[key: string]: { [key: string]: unknown }};

export type GatewayConfigPatchRequestApiIpAcl = {[key: string]: { [key: string]: unknown }};

export type GatewayConfigPatchRequestApiAlerting = {[key: string]: { [key: string]: unknown }};

export type GatewayConfigPatchRequestApiPrivacy = {[key: string]: { [key: string]: unknown }};

export type GatewayConfigPatchRequestApiToolPolicy = {[key: string]: { [key: string]: unknown }};

export type GatewayConfigPatchRequestApiMcp = {[key: string]: { [key: string]: unknown }};

export type GatewayConfigPatchRequestApiA2a = {[key: string]: { [key: string]: unknown }};

export type GatewayConfigPatchRequestApiAudit = {[key: string]: { [key: string]: unknown }};

export type GatewayConfigPatchRequestApiModelDatabase = {[key: string]: { [key: string]: unknown }};

export type GatewayConfigPatchRequestApiModelMap = {[key: string]: { [key: string]: unknown }};

export interface GatewayConfigPatchRequestApi {
  guardrails?: GatewayConfigPatchRequestApiGuardrails;
  routing?: GatewayConfigPatchRequestApiRouting;
  cache?: GatewayConfigPatchRequestApiCache;
  rate_limiting?: GatewayConfigPatchRequestApiRateLimiting;
  budgets?: GatewayConfigPatchRequestApiBudgets;
  cost_tracking?: GatewayConfigPatchRequestApiCostTracking;
  ip_acl?: GatewayConfigPatchRequestApiIpAcl;
  alerting?: GatewayConfigPatchRequestApiAlerting;
  privacy?: GatewayConfigPatchRequestApiPrivacy;
  tool_policy?: GatewayConfigPatchRequestApiToolPolicy;
  mcp?: GatewayConfigPatchRequestApiMcp;
  a2a?: GatewayConfigPatchRequestApiA2a;
  audit?: GatewayConfigPatchRequestApiAudit;
  model_database?: GatewayConfigPatchRequestApiModelDatabase;
  model_map?: GatewayConfigPatchRequestApiModelMap;
}

export type GatewayNamedConfigRequestApiConfig = {[key: string]: { [key: string]: unknown }};

export interface GatewayNamedConfigRequestApi {
  /** @minLength 1 */
  name: string;
  config: GatewayNamedConfigRequestApiConfig;
}

export type GatewayMCPGuardrailsUpdateRequestApiConfig = {[key: string]: { [key: string]: unknown }};

export interface GatewayMCPGuardrailsUpdateRequestApi {
  config: GatewayMCPGuardrailsUpdateRequestApiConfig;
}

export type GatewayMCPServerUpdateRequestApiConfig = {[key: string]: { [key: string]: unknown }};

export interface GatewayMCPServerUpdateRequestApi {
  /** @minLength 1 */
  server_id: string;
  config: GatewayMCPServerUpdateRequestApiConfig;
}

export type GatewayProviderUpdateRequestApiConfig = {[key: string]: { [key: string]: unknown }};

export interface GatewayProviderUpdateRequestApi {
  /** @minLength 1 */
  name: string;
  config: GatewayProviderUpdateRequestApiConfig;
}

export interface PIIEntityApi {
  /** @minLength 1 */
  id: string;
  /** @minLength 1 */
  label: string;
  /** @minLength 1 */
  category: string;
}

export interface PIIEntitiesResponseApi {
  status: boolean;
  result: PIIEntityApi[];
}

export interface TopicCategoryApi {
  /** @minLength 1 */
  id: string;
  /** @minLength 1 */
  label: string;
  subcategories: string[];
}

export interface TopicCategoriesResponseApi {
  status: boolean;
  result: TopicCategoryApi[];
}

export interface ValidateCELRequestApi {
  /** @minLength 1 */
  expression: string;
}

export interface ValidateCELResultApi {
  /** @minLength 1 */
  expression: string;
  valid: boolean;
  error?: string;
}

export interface ValidateCELResponseApi {
  status: boolean;
  result: ValidateCELResultApi;
}

export type AgentccGuardrailFeedbackApiFeedback = typeof AgentccGuardrailFeedbackApiFeedback[keyof typeof AgentccGuardrailFeedbackApiFeedback];


export const AgentccGuardrailFeedbackApiFeedback = {
  correct: 'correct',
  false_positive: 'false_positive',
  false_negative: 'false_negative',
  unsure: 'unsure',
} as const;

export interface AgentccGuardrailFeedbackApi {
  readonly id?: string;
  readonly organization?: string;
  request_log: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  check_name: string;
  feedback: AgentccGuardrailFeedbackApiFeedback;
  comment?: string;
  readonly created_by?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
}

export type AgentccGuardrailPolicyApiScope = typeof AgentccGuardrailPolicyApiScope[keyof typeof AgentccGuardrailPolicyApiScope];


export const AgentccGuardrailPolicyApiScope = {
  global: 'global',
  project: 'project',
  key: 'key',
} as const;

export type AgentccGuardrailPolicyApiChecks = { [key: string]: unknown };

export type AgentccGuardrailPolicyApiMode = typeof AgentccGuardrailPolicyApiMode[keyof typeof AgentccGuardrailPolicyApiMode];


export const AgentccGuardrailPolicyApiMode = {
  enforce: 'enforce',
  monitor: 'monitor',
} as const;

export type AgentccGuardrailPolicyApiAppliedKeys = { [key: string]: unknown };

export type AgentccGuardrailPolicyApiAppliedProjects = { [key: string]: unknown };

export interface AgentccGuardrailPolicyApi {
  readonly id?: string;
  readonly organization?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  description?: string;
  scope?: AgentccGuardrailPolicyApiScope;
  checks?: AgentccGuardrailPolicyApiChecks;
  mode?: AgentccGuardrailPolicyApiMode;
  is_active?: boolean;
  /**
     * @minimum -2147483648
     * @maximum 2147483647
     */
  priority?: number;
  applied_keys?: AgentccGuardrailPolicyApiAppliedKeys;
  applied_projects?: AgentccGuardrailPolicyApiAppliedProjects;
  readonly created_at?: string;
  readonly updated_at?: string;
}

export type AgentccOrgConfigApiGuardrails = { [key: string]: unknown };

export type AgentccOrgConfigApiRouting = { [key: string]: unknown };

export type AgentccOrgConfigApiCache = { [key: string]: unknown };

export type AgentccOrgConfigApiRateLimiting = { [key: string]: unknown };

export type AgentccOrgConfigApiBudgets = { [key: string]: unknown };

export type AgentccOrgConfigApiCostTracking = { [key: string]: unknown };

export type AgentccOrgConfigApiIpAcl = { [key: string]: unknown };

export type AgentccOrgConfigApiAlerting = { [key: string]: unknown };

export type AgentccOrgConfigApiPrivacy = { [key: string]: unknown };

export type AgentccOrgConfigApiToolPolicy = { [key: string]: unknown };

export type AgentccOrgConfigApiMcp = { [key: string]: unknown };

export type AgentccOrgConfigApiA2a = { [key: string]: unknown };

export type AgentccOrgConfigApiAudit = { [key: string]: unknown };

export type AgentccOrgConfigApiModelDatabase = { [key: string]: unknown };

export type AgentccOrgConfigApiModelMap = { [key: string]: unknown };

export interface AgentccOrgConfigApi {
  readonly id?: string;
  readonly organization?: string;
  readonly version?: number;
  guardrails?: AgentccOrgConfigApiGuardrails;
  routing?: AgentccOrgConfigApiRouting;
  cache?: AgentccOrgConfigApiCache;
  rate_limiting?: AgentccOrgConfigApiRateLimiting;
  budgets?: AgentccOrgConfigApiBudgets;
  cost_tracking?: AgentccOrgConfigApiCostTracking;
  ip_acl?: AgentccOrgConfigApiIpAcl;
  alerting?: AgentccOrgConfigApiAlerting;
  privacy?: AgentccOrgConfigApiPrivacy;
  tool_policy?: AgentccOrgConfigApiToolPolicy;
  mcp?: AgentccOrgConfigApiMcp;
  a2a?: AgentccOrgConfigApiA2a;
  audit?: AgentccOrgConfigApiAudit;
  model_database?: AgentccOrgConfigApiModelDatabase;
  model_map?: AgentccOrgConfigApiModelMap;
  readonly is_active?: boolean;
  readonly created_by?: string;
  change_description?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
}

export type OrgConfigBulkItemApiProviders = {[key: string]: { [key: string]: unknown }};

export type OrgConfigBulkItemApiGuardrails = { [key: string]: unknown };

export type OrgConfigBulkItemApiRouting = { [key: string]: unknown };

export type OrgConfigBulkItemApiCache = { [key: string]: unknown };

export type OrgConfigBulkItemApiRateLimiting = { [key: string]: unknown };

export type OrgConfigBulkItemApiBudgets = { [key: string]: unknown };

export type OrgConfigBulkItemApiCostTracking = { [key: string]: unknown };

export type OrgConfigBulkItemApiIpAcl = { [key: string]: unknown };

export type OrgConfigBulkItemApiAlerting = { [key: string]: unknown };

export type OrgConfigBulkItemApiPrivacy = { [key: string]: unknown };

export type OrgConfigBulkItemApiToolPolicy = { [key: string]: unknown };

export type OrgConfigBulkItemApiMcp = { [key: string]: unknown };

export type OrgConfigBulkItemApiA2a = { [key: string]: unknown };

export type OrgConfigBulkItemApiAudit = { [key: string]: unknown };

export type OrgConfigBulkItemApiModelDatabase = { [key: string]: unknown };

export type OrgConfigBulkItemApiModelMap = { [key: string]: unknown };

export interface OrgConfigBulkItemApi {
  providers: OrgConfigBulkItemApiProviders;
  guardrails: OrgConfigBulkItemApiGuardrails;
  routing: OrgConfigBulkItemApiRouting;
  cache: OrgConfigBulkItemApiCache;
  rate_limiting: OrgConfigBulkItemApiRateLimiting;
  budgets: OrgConfigBulkItemApiBudgets;
  cost_tracking: OrgConfigBulkItemApiCostTracking;
  ip_acl: OrgConfigBulkItemApiIpAcl;
  alerting: OrgConfigBulkItemApiAlerting;
  privacy: OrgConfigBulkItemApiPrivacy;
  tool_policy: OrgConfigBulkItemApiToolPolicy;
  mcp: OrgConfigBulkItemApiMcp;
  a2a: OrgConfigBulkItemApiA2a;
  audit: OrgConfigBulkItemApiAudit;
  model_database: OrgConfigBulkItemApiModelDatabase;
  model_map: OrgConfigBulkItemApiModelMap;
}

export type OrgConfigBulkResponseApiResult = {[key: string]: OrgConfigBulkItemApi};

export interface OrgConfigBulkResponseApi {
  status: boolean;
  result: OrgConfigBulkResponseApiResult;
}

export type AgentccProviderCredentialApiModelsList = { [key: string]: unknown };

export type AgentccProviderCredentialApiExtraConfig = { [key: string]: unknown };

export interface AgentccProviderCredentialApi {
  readonly id?: string;
  readonly organization?: string;
  readonly workspace?: string;
  /**
     * @minLength 1
     * @maxLength 100
     */
  provider_name: string;
  /** @maxLength 255 */
  display_name?: string;
  readonly credentials?: string;
  /** @maxLength 500 */
  base_url?: string;
  /**
     * @minLength 1
     * @maxLength 50
     */
  api_format?: string;
  models_list?: AgentccProviderCredentialApiModelsList;
  /**
     * @minimum -2147483648
     * @maximum 2147483647
     */
  default_timeout_seconds?: number;
  /**
     * @minimum -2147483648
     * @maximum 2147483647
     */
  max_concurrent?: number;
  /**
     * @minimum -2147483648
     * @maximum 2147483647
     */
  conn_pool_size?: number;
  extra_config?: AgentccProviderCredentialApiExtraConfig;
  is_active?: boolean;
  last_rotated_at?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
}

export type AgentccRequestLogDetailApiMetadata = { [key: string]: unknown };

export type AgentccRequestLogDetailApiRequestBody = { [key: string]: unknown };

export type AgentccRequestLogDetailApiResponseBody = { [key: string]: unknown };

export type AgentccRequestLogDetailApiRequestHeaders = { [key: string]: unknown };

export type AgentccRequestLogDetailApiResponseHeaders = { [key: string]: unknown };

export type AgentccRequestLogDetailApiGuardrailResults = { [key: string]: unknown };

export interface AgentccRequestLogDetailApi {
  readonly id?: string;
  /** @minLength 1 */
  readonly request_id?: string;
  /** @minLength 1 */
  readonly model?: string;
  /** @minLength 1 */
  readonly provider?: string;
  /** @minLength 1 */
  readonly resolved_model?: string;
  readonly latency_ms?: number;
  readonly started_at?: string;
  readonly input_tokens?: number;
  readonly output_tokens?: number;
  readonly total_tokens?: number;
  readonly cost?: string;
  readonly status_code?: number;
  readonly is_stream?: boolean;
  readonly is_error?: boolean;
  /** @minLength 1 */
  readonly error_message?: string;
  readonly cache_hit?: boolean;
  readonly fallback_used?: boolean;
  readonly guardrail_triggered?: boolean;
  /** @minLength 1 */
  readonly api_key_id?: string;
  /** @minLength 1 */
  readonly user_id?: string;
  /** @minLength 1 */
  readonly session_id?: string;
  /** @minLength 1 */
  readonly routing_strategy?: string;
  readonly metadata?: AgentccRequestLogDetailApiMetadata;
  readonly request_body?: AgentccRequestLogDetailApiRequestBody;
  readonly response_body?: AgentccRequestLogDetailApiResponseBody;
  readonly request_headers?: AgentccRequestLogDetailApiRequestHeaders;
  readonly response_headers?: AgentccRequestLogDetailApiResponseHeaders;
  readonly guardrail_results?: AgentccRequestLogDetailApiGuardrailResults;
  readonly organization?: string;
  readonly workspace?: string;
  readonly created_at?: string;
}

export type AgentccRoutingPolicyApiConfig = { [key: string]: unknown };

export interface AgentccRoutingPolicyApi {
  readonly id?: string;
  readonly organization?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  description?: string;
  readonly version?: number;
  config?: AgentccRoutingPolicyApiConfig;
  is_active?: boolean;
  readonly created_by?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
}

export type AgentccSessionApiStatus = typeof AgentccSessionApiStatus[keyof typeof AgentccSessionApiStatus];


export const AgentccSessionApiStatus = {
  active: 'active',
  closed: 'closed',
} as const;

export type AgentccSessionApiMetadata = { [key: string]: unknown };

export interface AgentccSessionApi {
  readonly id?: string;
  readonly organization?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  session_id: string;
  /** @maxLength 255 */
  name?: string;
  status?: AgentccSessionApiStatus;
  metadata?: AgentccSessionApiMetadata;
  readonly created_at?: string;
  readonly updated_at?: string;
}

export type AgentccShadowExperimentApiStatus = typeof AgentccShadowExperimentApiStatus[keyof typeof AgentccShadowExperimentApiStatus];


export const AgentccShadowExperimentApiStatus = {
  active: 'active',
  paused: 'paused',
  completed: 'completed',
} as const;

/**
 * Extra configuration
 */
export type AgentccShadowExperimentApiConfig = { [key: string]: unknown };

export interface AgentccShadowExperimentApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 128
     */
  name: string;
  description?: string;
  /**
     * Production model being tested against
     * @minLength 1
     * @maxLength 255
     */
  source_model: string;
  /**
     * Shadow model receiving mirrored traffic
     * @minLength 1
     * @maxLength 255
     */
  shadow_model: string;
  /**
     * Provider for the shadow model
     * @minLength 1
     * @maxLength 128
     */
  shadow_provider: string;
  /** Fraction of traffic to mirror (0.0–1.0) */
  sample_rate?: number;
  status?: AgentccShadowExperimentApiStatus;
  readonly total_comparisons?: number;
  /** Extra configuration */
  config?: AgentccShadowExperimentApiConfig;
  readonly created_by?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
}

export interface AgentccShadowResultApi {
  readonly id?: string;
  readonly experiment?: string;
  /** @minLength 1 */
  readonly request_id?: string;
  /** @minLength 1 */
  readonly source_model?: string;
  /** @minLength 1 */
  readonly shadow_model?: string;
  /** @minLength 1 */
  readonly source_response?: string;
  /** @minLength 1 */
  readonly shadow_response?: string;
  readonly source_latency_ms?: number;
  readonly shadow_latency_ms?: number;
  readonly source_tokens?: number;
  readonly shadow_tokens?: number;
  readonly source_status_code?: number;
  readonly shadow_status_code?: number;
  /** @minLength 1 */
  readonly shadow_error?: string;
  /** @minLength 1 */
  readonly prompt_hash?: string;
  readonly created_at?: string;
}

export type SpendSummaryResultApiPeriod = typeof SpendSummaryResultApiPeriod[keyof typeof SpendSummaryResultApiPeriod];


export const SpendSummaryResultApiPeriod = {
  daily: 'daily',
  weekly: 'weekly',
  monthly: 'monthly',
  total: 'total',
} as const;

export type SpendSummaryOrgApiPerKey = {[key: string]: number};

export type SpendSummaryOrgApiPerUser = {[key: string]: number};

export type SpendSummaryOrgApiPerModel = {[key: string]: number};

export interface SpendSummaryOrgApi {
  total_spend: number;
  per_key: SpendSummaryOrgApiPerKey;
  per_user: SpendSummaryOrgApiPerUser;
  per_model: SpendSummaryOrgApiPerModel;
}

export type SpendSummaryResultApiOrgs = {[key: string]: SpendSummaryOrgApi};

export interface SpendSummaryResultApi {
  period: SpendSummaryResultApiPeriod;
  period_start: string;
  orgs: SpendSummaryResultApiOrgs;
}

export interface SpendSummaryResponseApi {
  status: boolean;
  result: SpendSummaryResultApi;
}

export type AgentccWebhookEventApiPayload = { [key: string]: unknown };

export type AgentccWebhookEventApiStatus = typeof AgentccWebhookEventApiStatus[keyof typeof AgentccWebhookEventApiStatus];


export const AgentccWebhookEventApiStatus = {
  pending: 'pending',
  delivered: 'delivered',
  failed: 'failed',
  dead_letter: 'dead_letter',
} as const;

export interface AgentccWebhookEventApi {
  readonly id?: string;
  readonly organization?: string;
  readonly webhook?: string;
  /** @minLength 1 */
  readonly webhook_name?: string;
  /** @minLength 1 */
  readonly event_type?: string;
  readonly payload?: AgentccWebhookEventApiPayload;
  readonly status?: AgentccWebhookEventApiStatus;
  readonly attempts?: number;
  readonly max_attempts?: number;
  readonly last_attempt_at?: string;
  readonly last_response_code?: number;
  /** @minLength 1 */
  readonly last_error?: string;
  readonly next_retry_at?: string;
  readonly created_at?: string;
}

export type WebhookLogsRequestApiLogsItem = {[key: string]: string};

export interface WebhookLogsRequestApi {
  gateway_id?: string;
  logs?: WebhookLogsRequestApiLogsItem[];
}

export interface WebhookIngestResultApi {
  ingested: number;
}

export interface WebhookIngestResponseApi {
  status: boolean;
  result: WebhookIngestResultApi;
}

export type ShadowResultsWebhookRequestApiResultsItem = {[key: string]: string};

export interface ShadowResultsWebhookRequestApi {
  results?: ShadowResultsWebhookRequestApiResultsItem[];
}

export type AgentccWebhookApiEvents = { [key: string]: unknown };

export type AgentccWebhookApiHeaders = { [key: string]: unknown };

export interface AgentccWebhookApi {
  readonly id?: string;
  readonly organization?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  /**
     * @minLength 1
     * @maxLength 2048
     */
  url: string;
  /** @maxLength 255 */
  secret?: string;
  events?: AgentccWebhookApiEvents;
  is_active?: boolean;
  headers?: AgentccWebhookApiHeaders;
  description?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
}

export interface ToolParameterApi {
  /** @minLength 1 */
  readonly name?: string;
  /** @minLength 1 */
  readonly type?: string;
  readonly description?: string;
  readonly required?: boolean;
}

export type ToolDiscoveryItemApiReturns = { [key: string]: unknown };

export type ToolDiscoveryItemApiMetadata = { [key: string]: unknown };

export interface ToolDiscoveryItemApi {
  /** @minLength 1 */
  readonly name?: string;
  /** @minLength 1 */
  readonly category?: string;
  readonly description?: string;
  readonly parameters?: readonly ToolParameterApi[];
  readonly returns?: ToolDiscoveryItemApiReturns;
  readonly metadata?: ToolDiscoveryItemApiMetadata;
}

export interface ToolDiscoveryResultApi {
  readonly tools?: readonly ToolDiscoveryItemApi[];
  readonly categories?: readonly string[];
  readonly total?: number;
}

export interface ToolDiscoveryResponseApi {
  status?: boolean;
  result: ToolDiscoveryResultApi;
}

export type ApiDetailErrorResponseApiType = typeof ApiDetailErrorResponseApiType[keyof typeof ApiDetailErrorResponseApiType];


export const ApiDetailErrorResponseApiType = {
  validation_error: 'validation_error',
  authentication_error: 'authentication_error',
  payment_required: 'payment_required',
  entitlement_error: 'entitlement_error',
  permission_error: 'permission_error',
  not_found: 'not_found',
  conflict: 'conflict',
  client_error: 'client_error',
  rate_limit: 'rate_limit',
  server_error: 'server_error',
  service_unavailable: 'service_unavailable',
  timeout: 'timeout',
  api_error: 'api_error',
} as const;

export type ApiDetailErrorResponseApiDetails = {[key: string]: string[]};

export interface ApiDetailErrorResponseApi {
  status?: boolean;
  type?: ApiDetailErrorResponseApiType;
  code?: string;
  detail: string;
  /** @minLength 1 */
  result?: string;
  /** @minLength 1 */
  message?: string;
  error?: string;
  attr?: string;
  details?: ApiDetailErrorResponseApiDetails;
}

export type ApiTextErrorResponseApiType = typeof ApiTextErrorResponseApiType[keyof typeof ApiTextErrorResponseApiType];


export const ApiTextErrorResponseApiType = {
  validation_error: 'validation_error',
  authentication_error: 'authentication_error',
  payment_required: 'payment_required',
  entitlement_error: 'entitlement_error',
  permission_error: 'permission_error',
  not_found: 'not_found',
  conflict: 'conflict',
  client_error: 'client_error',
  rate_limit: 'rate_limit',
  server_error: 'server_error',
  service_unavailable: 'service_unavailable',
  timeout: 'timeout',
  api_error: 'api_error',
} as const;

export type ApiTextErrorResponseApiDetails = {[key: string]: string[]};

export interface ApiTextErrorResponseApi {
  status?: boolean;
  type?: ApiTextErrorResponseApiType;
  code?: string;
  detail?: string;
  /** @minLength 1 */
  result?: string;
  /** @minLength 1 */
  message?: string;
  error?: string;
  attr?: string;
  details?: ApiTextErrorResponseApiDetails;
}

export type DeploymentInfoResultApiMode = typeof DeploymentInfoResultApiMode[keyof typeof DeploymentInfoResultApiMode];


export const DeploymentInfoResultApiMode = {
  oss: 'oss',
  ee: 'ee',
  cloud: 'cloud',
} as const;

export interface DeploymentInfoResultApi {
  mode: DeploymentInfoResultApiMode;
}

export interface DeploymentInfoResponseApi {
  status?: boolean;
  result: DeploymentInfoResultApi;
}

export type ClickHouseHealthResponseApiStatus = typeof ClickHouseHealthResponseApiStatus[keyof typeof ClickHouseHealthResponseApiStatus];


export const ClickHouseHealthResponseApiStatus = {
  healthy: 'healthy',
  degraded: 'degraded',
  unhealthy: 'unhealthy',
  disabled: 'disabled',
} as const;

export type ClickHouseHealthResponseApiCdcLag = {[key: string]: number};

export type ClickHouseHealthResponseApiRouting = {[key: string]: { [key: string]: unknown }};

export interface ClickHouseHealthResponseApi {
  status: ClickHouseHealthResponseApiStatus;
  clickhouse_connected: boolean;
  cdc_lag: ClickHouseHealthResponseApiCdcLag;
  routing: ClickHouseHealthResponseApiRouting;
  /** @minLength 1 */
  error?: string;
}

export type ClickHouseHealthErrorResponseApiType = typeof ClickHouseHealthErrorResponseApiType[keyof typeof ClickHouseHealthErrorResponseApiType];


export const ClickHouseHealthErrorResponseApiType = {
  validation_error: 'validation_error',
  authentication_error: 'authentication_error',
  payment_required: 'payment_required',
  entitlement_error: 'entitlement_error',
  permission_error: 'permission_error',
  not_found: 'not_found',
  conflict: 'conflict',
  client_error: 'client_error',
  rate_limit: 'rate_limit',
  server_error: 'server_error',
  service_unavailable: 'service_unavailable',
  timeout: 'timeout',
  api_error: 'api_error',
} as const;

export type ClickHouseHealthErrorResponseApiDetails = {[key: string]: string[]};

export type ClickHouseHealthErrorResponseApiHealthStatus = typeof ClickHouseHealthErrorResponseApiHealthStatus[keyof typeof ClickHouseHealthErrorResponseApiHealthStatus];


export const ClickHouseHealthErrorResponseApiHealthStatus = {
  healthy: 'healthy',
  degraded: 'degraded',
  unhealthy: 'unhealthy',
  disabled: 'disabled',
} as const;

export type ClickHouseHealthErrorResponseApiCdcLag = {[key: string]: number};

export type ClickHouseHealthErrorResponseApiRouting = {[key: string]: { [key: string]: unknown }};

export interface ClickHouseHealthErrorResponseApi {
  status?: boolean;
  type?: ClickHouseHealthErrorResponseApiType;
  code?: string;
  detail?: string;
  /** @minLength 1 */
  result?: string;
  /** @minLength 1 */
  message?: string;
  error?: string;
  attr?: string;
  details?: ClickHouseHealthErrorResponseApiDetails;
  health_status?: ClickHouseHealthErrorResponseApiHealthStatus;
  clickhouse_connected?: boolean;
  cdc_lag?: ClickHouseHealthErrorResponseApiCdcLag;
  routing?: ClickHouseHealthErrorResponseApiRouting;
}

export type LangfuseHealthResponseApiStatus = typeof LangfuseHealthResponseApiStatus[keyof typeof LangfuseHealthResponseApiStatus];


export const LangfuseHealthResponseApiStatus = {
  OK: 'OK',
} as const;

export interface LangfuseHealthResponseApi {
  status: LangfuseHealthResponseApiStatus;
  /** @minLength 1 */
  version: string;
}

export type LangfuseIngestionEventApiBody = { [key: string]: unknown };

export interface LangfuseIngestionEventApi {
  id?: string;
  /** @minLength 1 */
  type: string;
  body?: LangfuseIngestionEventApiBody;
  timestamp?: string;
}

export interface LangfuseIngestionRequestApi {
  batch: LangfuseIngestionEventApi[];
}

export interface LangfuseIngestionSuccessApi {
  /** @minLength 1 */
  id: string;
  status: number;
}

export interface LangfuseIngestionErrorApi {
  /** @minLength 1 */
  id: string;
  status: number;
  /** @minLength 1 */
  message: string;
}

export interface LangfuseIngestionResponseApi {
  successes: LangfuseIngestionSuccessApi[];
  errors: LangfuseIngestionErrorApi[];
}

export type LangfuseTracesResponseApiDataItem = { [key: string]: unknown };

export interface LangfuseTracesMetaApi {
  page: number;
  limit: number;
  total_items: number;
  total_pages: number;
}

export interface LangfuseTracesResponseApi {
  data: LangfuseTracesResponseApiDataItem[];
  meta: LangfuseTracesMetaApi;
}

export type SpanAttributeDetailResponseApiType = typeof SpanAttributeDetailResponseApiType[keyof typeof SpanAttributeDetailResponseApiType];


export const SpanAttributeDetailResponseApiType = {
  string: 'string',
  number: 'number',
  boolean: 'boolean',
} as const;

export type SpanAttributeTopValueApiValue = { [key: string]: unknown };

export interface SpanAttributeTopValueApi {
  value: SpanAttributeTopValueApiValue;
  count: number;
  percentage: number;
}

export interface SpanAttributeDetailResponseApi {
  /** @minLength 1 */
  key: string;
  type: SpanAttributeDetailResponseApiType;
  count: number;
  unique_values?: number;
  top_values?: SpanAttributeTopValueApi[];
  min?: number;
  max?: number;
  avg?: number;
  p50?: number;
  p95?: number;
}

export type SpanAttributeKeyApiType = typeof SpanAttributeKeyApiType[keyof typeof SpanAttributeKeyApiType];


export const SpanAttributeKeyApiType = {
  string: 'string',
  number: 'number',
  boolean: 'boolean',
} as const;

export interface SpanAttributeKeyApi {
  /** @minLength 1 */
  key: string;
  type: SpanAttributeKeyApiType;
  count: number;
}

export interface SpanAttributeKeysResponseApi {
  result: SpanAttributeKeyApi[];
}

export type SpanAttributeValueApiValue = { [key: string]: unknown };

export interface SpanAttributeValueApi {
  value: SpanAttributeValueApiValue;
  count: number;
}

export interface SpanAttributeValuesResponseApi {
  result: SpanAttributeValueApi[];
}

export interface CallWebsocketRequestApi {
  /** @minLength 1 */
  message: string;
  send_to_uuid?: boolean;
  uuid?: string;
}

export interface CallWebsocketResponseApi {
  status?: boolean;
  /** @minLength 1 */
  result: string;
}

export type CallWebsocketErrorResponseApiType = typeof CallWebsocketErrorResponseApiType[keyof typeof CallWebsocketErrorResponseApiType];


export const CallWebsocketErrorResponseApiType = {
  validation_error: 'validation_error',
  authentication_error: 'authentication_error',
  payment_required: 'payment_required',
  entitlement_error: 'entitlement_error',
  permission_error: 'permission_error',
  not_found: 'not_found',
  conflict: 'conflict',
  client_error: 'client_error',
  rate_limit: 'rate_limit',
  server_error: 'server_error',
  service_unavailable: 'service_unavailable',
  timeout: 'timeout',
  api_error: 'api_error',
} as const;

export type CallWebsocketErrorResponseApiDetails = {[key: string]: string[]};

export interface CallWebsocketErrorResponseApi {
  status?: boolean;
  type?: CallWebsocketErrorResponseApiType;
  code?: string;
  detail?: string;
  /** @minLength 1 */
  result: string;
  /** @minLength 1 */
  message: string;
  error?: string;
  attr?: string;
  details?: CallWebsocketErrorResponseApiDetails;
}

export interface FalconConversationListApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  title?: string;
  /** @maxLength 500 */
  context_page?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
  readonly message_count?: number;
  readonly last_message_at?: string;
}

export interface ConversationListResponseApi {
  status: boolean;
  results: FalconConversationListApi[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
}

export type FalconErrorResponseApiType = typeof FalconErrorResponseApiType[keyof typeof FalconErrorResponseApiType];


export const FalconErrorResponseApiType = {
  validation_error: 'validation_error',
  authentication_error: 'authentication_error',
  payment_required: 'payment_required',
  entitlement_error: 'entitlement_error',
  permission_error: 'permission_error',
  not_found: 'not_found',
  conflict: 'conflict',
  client_error: 'client_error',
  rate_limit: 'rate_limit',
  server_error: 'server_error',
  service_unavailable: 'service_unavailable',
  timeout: 'timeout',
  api_error: 'api_error',
} as const;

export type FalconErrorResponseApiDetails = {[key: string]: string[]};

export interface FalconErrorResponseApi {
  status?: boolean;
  type?: FalconErrorResponseApiType;
  code?: string;
  detail?: string;
  /** @minLength 1 */
  result?: string;
  /** @minLength 1 */
  message?: string;
  error?: string;
  attr?: string;
  details?: FalconErrorResponseApiDetails;
}

export interface ConversationCreateRequestApi {
  /** @maxLength 255 */
  title?: string;
  /** @maxLength 500 */
  context_page?: string;
  hidden?: boolean;
}

export type FalconMessageApiRole = typeof FalconMessageApiRole[keyof typeof FalconMessageApiRole];


export const FalconMessageApiRole = {
  user: 'user',
  assistant: 'assistant',
  system: 'system',
} as const;

export type FalconMessageApiThoughts = { [key: string]: unknown };

export type FalconMessageApiToolCalls = { [key: string]: unknown };

export type FalconMessageApiCompletionCard = { [key: string]: unknown };

export type FalconMessageApiFiles = { [key: string]: unknown };

export interface FalconMessageApi {
  readonly id?: string;
  conversation: string;
  role: FalconMessageApiRole;
  /** @minLength 1 */
  content?: string;
  thoughts?: FalconMessageApiThoughts;
  tool_calls?: FalconMessageApiToolCalls;
  completion_card?: FalconMessageApiCompletionCard;
  files?: FalconMessageApiFiles;
  /** @maxLength 20 */
  feedback?: string;
  /**
     * @minimum 0
     * @maximum 2147483647
     */
  input_tokens?: number;
  /**
     * @minimum 0
     * @maximum 2147483647
     */
  output_tokens?: number;
  /** @maxLength 100 */
  model_used?: string;
  /**
     * @minimum 0
     * @maximum 2147483647
     */
  latency_ms?: number;
  readonly created_at?: string;
  readonly updated_at?: string;
}

export type FalconConversationDetailApiMetadata = { [key: string]: unknown };

export interface FalconConversationDetailApi {
  readonly id?: string;
  readonly user?: string;
  readonly organization?: string;
  readonly workspace?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  title?: string;
  /** @maxLength 500 */
  context_page?: string;
  metadata?: FalconConversationDetailApiMetadata;
  readonly messages?: readonly FalconMessageApi[];
  readonly created_at?: string;
  readonly updated_at?: string;
}

export interface ConversationDetailResponseApi {
  status: boolean;
  result: FalconConversationDetailApi;
}

export interface ConversationUpdateRequestApi {
  /** @maxLength 255 */
  title?: string;
}

export interface StreamStatusResultApi {
  /** @minLength 1 */
  stream_status: string;
}

export interface StreamStatusResponseApi {
  status: boolean;
  result: StreamStatusResultApi;
}

export interface FileUploadResultApi {
  id: string;
  /** @minLength 1 */
  name: string;
  size: number;
  /** @minLength 1 */
  content_type: string;
  /** @minLength 1 */
  url: string;
}

export interface FileUploadResponseApi {
  status: boolean;
  result: FileUploadResultApi;
}

export type MCPConnectorListApiTransport = typeof MCPConnectorListApiTransport[keyof typeof MCPConnectorListApiTransport];


export const MCPConnectorListApiTransport = {
  sse: 'sse',
  streamable_http: 'streamable_http',
} as const;

export type MCPConnectorListApiAuthType = typeof MCPConnectorListApiAuthType[keyof typeof MCPConnectorListApiAuthType];


export const MCPConnectorListApiAuthType = {
  none: 'none',
  api_key: 'api_key',
  bearer: 'bearer',
  oauth: 'oauth',
} as const;

export interface MCPConnectorListApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 100
     */
  name: string;
  /**
     * @minLength 1
     * @maxLength 200
     */
  server_url: string;
  transport?: MCPConnectorListApiTransport;
  auth_type?: MCPConnectorListApiAuthType;
  is_active?: boolean;
  is_verified?: boolean;
  readonly tool_count?: string;
  last_discovery_at?: string;
  last_error?: string;
  readonly created_at?: string;
}

export interface MCPConnectorListResponseApi {
  status: boolean;
  results: MCPConnectorListApi[];
}

export type MCPConnectorCreateApiTransport = typeof MCPConnectorCreateApiTransport[keyof typeof MCPConnectorCreateApiTransport];


export const MCPConnectorCreateApiTransport = {
  sse: 'sse',
  streamable_http: 'streamable_http',
} as const;

export type MCPConnectorCreateApiAuthType = typeof MCPConnectorCreateApiAuthType[keyof typeof MCPConnectorCreateApiAuthType];


export const MCPConnectorCreateApiAuthType = {
  none: 'none',
  api_key: 'api_key',
  bearer: 'bearer',
  oauth: 'oauth',
} as const;

export interface MCPConnectorCreateApi {
  /**
     * @minLength 1
     * @maxLength 100
     */
  name: string;
  /** @minLength 1 */
  server_url: string;
  transport?: MCPConnectorCreateApiTransport;
  auth_type?: MCPConnectorCreateApiAuthType;
  /** @maxLength 100 */
  auth_header_name?: string;
  auth_header_value?: string;
}

export type MCPConnectorDetailApiTransport = typeof MCPConnectorDetailApiTransport[keyof typeof MCPConnectorDetailApiTransport];


export const MCPConnectorDetailApiTransport = {
  sse: 'sse',
  streamable_http: 'streamable_http',
} as const;

export type MCPConnectorDetailApiAuthType = typeof MCPConnectorDetailApiAuthType[keyof typeof MCPConnectorDetailApiAuthType];


export const MCPConnectorDetailApiAuthType = {
  none: 'none',
  api_key: 'api_key',
  bearer: 'bearer',
  oauth: 'oauth',
} as const;

export type MCPConnectorDetailApiDiscoveredTools = { [key: string]: unknown };

export type MCPConnectorDetailApiEnabledToolNames = { [key: string]: unknown };

export interface MCPConnectorDetailApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 100
     */
  name: string;
  /**
     * @minLength 1
     * @maxLength 200
     */
  server_url: string;
  transport?: MCPConnectorDetailApiTransport;
  auth_type?: MCPConnectorDetailApiAuthType;
  /** @maxLength 100 */
  auth_header_name?: string;
  is_active?: boolean;
  is_verified?: boolean;
  discovered_tools?: MCPConnectorDetailApiDiscoveredTools;
  enabled_tool_names?: MCPConnectorDetailApiEnabledToolNames;
  last_discovery_at?: string;
  last_error?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
}

export interface MCPConnectorDetailResponseApi {
  status: boolean;
  result: MCPConnectorDetailApi;
}

export type MCPConnectorUpdateRequestApiTransport = typeof MCPConnectorUpdateRequestApiTransport[keyof typeof MCPConnectorUpdateRequestApiTransport];


export const MCPConnectorUpdateRequestApiTransport = {
  sse: 'sse',
  streamable_http: 'streamable_http',
} as const;

export type MCPConnectorUpdateRequestApiAuthType = typeof MCPConnectorUpdateRequestApiAuthType[keyof typeof MCPConnectorUpdateRequestApiAuthType];


export const MCPConnectorUpdateRequestApiAuthType = {
  none: 'none',
  api_key: 'api_key',
  bearer: 'bearer',
  oauth: 'oauth',
} as const;

export interface MCPConnectorUpdateRequestApi {
  /** @maxLength 100 */
  name?: string;
  /** @minLength 1 */
  server_url?: string;
  transport?: MCPConnectorUpdateRequestApiTransport;
  auth_type?: MCPConnectorUpdateRequestApiAuthType;
  /** @maxLength 100 */
  auth_header_name?: string;
  auth_header_value?: string;
  is_active?: boolean;
}

export interface FalconEmptyRequestApi { [key: string]: unknown }

export interface MCPConnectorAuthenticateResponseApi {
  status: boolean;
  result?: MCPConnectorDetailApi;
  auth_type?: string;
  /** @minLength 1 */
  authorization_url?: string;
  message?: string;
}

export interface MCPConnectorDiscoverResponseApi {
  status: boolean;
  result: MCPConnectorDetailApi;
  discovered_count: number;
}

export interface MCPConnectorTestResultApi {
  success: boolean;
  status_code?: number;
  error?: string;
}

export interface MCPConnectorTestResponseApi {
  status: boolean;
  result?: MCPConnectorTestResultApi;
  error?: string;
}

export interface MCPConnectorToolsApi {
  enabled_tool_names: string[];
}

export type FalconMemoryApiSource = typeof FalconMemoryApiSource[keyof typeof FalconMemoryApiSource];


export const FalconMemoryApiSource = {
  user: 'user',
  agent: 'agent',
  init: 'init',
} as const;

export interface FalconMemoryApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 200
     */
  key: string;
  /** @minLength 1 */
  value: string;
  source?: FalconMemoryApiSource;
  readonly created_at?: string;
}

export interface FalconMemoryListResponseApi {
  status: boolean;
  results: FalconMemoryApi[];
}

export interface FalconMemoryCreateApi {
  /**
     * @minLength 1
     * @maxLength 200
     */
  key: string;
  /** @minLength 1 */
  value: string;
}

export interface FalconMemoryDetailResponseApi {
  status: boolean;
  result: FalconMemoryApi;
}

export type MessageFeedbackApiFeedback = typeof MessageFeedbackApiFeedback[keyof typeof MessageFeedbackApiFeedback];


export const MessageFeedbackApiFeedback = {
  thumbs_up: 'thumbs_up',
  thumbs_down: 'thumbs_down',
  '': '',
} as const;

export interface MessageFeedbackApi {
  feedback: MessageFeedbackApiFeedback;
}

export interface MessageFeedbackResultApi {
  feedback: string;
}

export interface MessageFeedbackResponseApi {
  status: boolean;
  result: MessageFeedbackResultApi;
}

export interface QuickAnalysisApi {
  /**
     * @minLength 1
     * @maxLength 8000
     */
  prompt: string;
}

export interface QuickAnalysisResponseApi {
  status: boolean;
  /** @minLength 1 */
  result: string;
}

export type SkillListApiToolNames = { [key: string]: unknown };

export type SkillListApiTriggerPhrases = { [key: string]: unknown };

export interface SkillListApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 100
     */
  name: string;
  /**
     * @minLength 1
     * @maxLength 100
     * @pattern ^[-a-zA-Z0-9_]+$
     */
  slug: string;
  /** @minLength 1 */
  description?: string;
  /**
     * @minLength 1
     * @maxLength 50
     */
  icon?: string;
  is_builtin?: boolean;
  is_active?: boolean;
  tool_names?: SkillListApiToolNames;
  trigger_phrases?: SkillListApiTriggerPhrases;
  readonly created_at?: string;
  readonly created_by_display?: string;
}

export interface SkillListResponseApi {
  status: boolean;
  results: SkillListApi[];
}

export interface SkillCreateApi {
  /**
     * @minLength 1
     * @maxLength 100
     */
  name: string;
  description?: string;
  /**
     * @minLength 1
     * @maxLength 50
     */
  icon?: string;
  /** @minLength 1 */
  instructions: string;
  tool_names?: string[];
  /** @minItems 1 */
  trigger_phrases: string[];
}

export type SkillDetailApiToolNames = { [key: string]: unknown };

export type SkillDetailApiExampleTrajectories = { [key: string]: unknown };

export type SkillDetailApiTriggerPhrases = { [key: string]: unknown };

export interface SkillDetailApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 100
     */
  name: string;
  /**
     * @minLength 1
     * @maxLength 100
     * @pattern ^[-a-zA-Z0-9_]+$
     */
  slug: string;
  /** @minLength 1 */
  description?: string;
  /**
     * @minLength 1
     * @maxLength 50
     */
  icon?: string;
  is_builtin?: boolean;
  is_active?: boolean;
  /** @minLength 1 */
  instructions?: string;
  tool_names?: SkillDetailApiToolNames;
  example_trajectories?: SkillDetailApiExampleTrajectories;
  trigger_phrases?: SkillDetailApiTriggerPhrases;
  readonly created_at?: string;
  readonly updated_at?: string;
  readonly created_by_display?: string;
}

export interface SkillDetailResponseApi {
  status: boolean;
  result: SkillDetailApi;
}

export interface SkillUpdateRequestApi {
  /**
     * @minLength 1
     * @maxLength 100
     */
  name?: string;
  description?: string;
  /**
     * @minLength 1
     * @maxLength 50
     */
  icon?: string;
  /** @minLength 1 */
  instructions?: string;
  tool_names?: string[];
  trigger_phrases?: string[];
  is_active?: boolean;
}

export interface HealthCheckResponseApi {
  status?: boolean;
  /** @minLength 1 */
  result: string;
}

export type IntegrationConnectionListApiPlatform = typeof IntegrationConnectionListApiPlatform[keyof typeof IntegrationConnectionListApiPlatform];


export const IntegrationConnectionListApiPlatform = {
  langfuse: 'langfuse',
  datadog: 'datadog',
  posthog: 'posthog',
  pagerduty: 'pagerduty',
  mixpanel: 'mixpanel',
  cloud_storage: 'cloud_storage',
  message_queue: 'message_queue',
  linear: 'linear',
} as const;

export type IntegrationConnectionListApiStatus = typeof IntegrationConnectionListApiStatus[keyof typeof IntegrationConnectionListApiStatus];


export const IntegrationConnectionListApiStatus = {
  active: 'active',
  paused: 'paused',
  error: 'error',
  syncing: 'syncing',
  backfilling: 'backfilling',
} as const;

export type IntegrationConnectionListApiBackfillProgress = { [key: string]: unknown };

export interface IntegrationConnectionListApi {
  readonly id?: string;
  platform: IntegrationConnectionListApiPlatform;
  /**
     * @minLength 1
     * @maxLength 255
     */
  display_name: string;
  /**
     * @minLength 1
     * @maxLength 500
     */
  host_url: string;
  status?: IntegrationConnectionListApiStatus;
  status_message?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  external_project_name: string;
  last_synced_at?: string;
  /**
     * @minimum 0
     * @maximum 2147483647
     */
  total_traces_synced?: number;
  /**
     * @minimum 0
     * @maximum 2147483647
     */
  total_spans_synced?: number;
  /**
     * @minimum 0
     * @maximum 2147483647
     */
  total_scores_synced?: number;
  backfill_completed?: boolean;
  backfill_progress?: IntegrationConnectionListApiBackfillProgress;
  /**
     * @minimum 60
     * @maximum 1800
     */
  sync_interval_seconds?: number;
  readonly created_at?: string;
}

export interface IntegrationConnectionListResultApi {
  metadata: PaginationMetadataApi;
  connections: IntegrationConnectionListApi[];
}

export interface IntegrationConnectionListResponseApi {
  status?: boolean;
  result: IntegrationConnectionListResultApi;
}

export type IntegrationErrorResponseApiType = typeof IntegrationErrorResponseApiType[keyof typeof IntegrationErrorResponseApiType];


export const IntegrationErrorResponseApiType = {
  validation_error: 'validation_error',
  authentication_error: 'authentication_error',
  payment_required: 'payment_required',
  entitlement_error: 'entitlement_error',
  permission_error: 'permission_error',
  not_found: 'not_found',
  conflict: 'conflict',
  client_error: 'client_error',
  rate_limit: 'rate_limit',
  server_error: 'server_error',
  service_unavailable: 'service_unavailable',
  timeout: 'timeout',
  api_error: 'api_error',
} as const;

export type IntegrationErrorResponseApiDetails = {[key: string]: string[]};

export interface IntegrationErrorResponseApi {
  status?: boolean;
  type?: IntegrationErrorResponseApiType;
  code?: string;
  detail?: string;
  result?: string;
  message?: string;
  error?: string;
  attr?: string;
  details?: IntegrationErrorResponseApiDetails;
}

export type IntegrationConnectionCreateApiPlatform = typeof IntegrationConnectionCreateApiPlatform[keyof typeof IntegrationConnectionCreateApiPlatform];


export const IntegrationConnectionCreateApiPlatform = {
  langfuse: 'langfuse',
  datadog: 'datadog',
  posthog: 'posthog',
  pagerduty: 'pagerduty',
  mixpanel: 'mixpanel',
  cloud_storage: 'cloud_storage',
  message_queue: 'message_queue',
  linear: 'linear',
} as const;

export type IntegrationConnectionCreateApiCredentials = { [key: string]: unknown };

export type IntegrationConnectionCreateApiBackfillOption = typeof IntegrationConnectionCreateApiBackfillOption[keyof typeof IntegrationConnectionCreateApiBackfillOption];


export const IntegrationConnectionCreateApiBackfillOption = {
  all: 'all',
  from_date: 'from_date',
  new_only: 'new_only',
} as const;

export type IntegrationConnectionCreateApiExportConfig = { [key: string]: unknown };

export interface IntegrationConnectionCreateApi {
  platform: IntegrationConnectionCreateApiPlatform;
  /**
     * @minLength 1
     * @maxLength 500
     */
  host_url?: string;
  /**
     * @minLength 1
     * @maxLength 500
     */
  public_key?: string;
  /**
     * @minLength 1
     * @maxLength 500
     */
  secret_key?: string;
  ca_certificate?: string;
  credentials?: IntegrationConnectionCreateApiCredentials;
  /** Existing FutureAGI project ID. If null, a new project is created. */
  project_id?: string;
  /** Name for the new project (used when project_id is null). */
  new_project_name?: string;
  backfill_option?: IntegrationConnectionCreateApiBackfillOption;
  backfill_from_date?: string;
  backfill_to_date?: string;
  /**
     * @minimum 60
     * @maximum 1800
     */
  sync_interval_seconds?: number;
  display_name?: string;
  external_project_name?: string;
  export_config?: IntegrationConnectionCreateApiExportConfig;
}

export type IntegrationConnectionDetailApiPlatform = typeof IntegrationConnectionDetailApiPlatform[keyof typeof IntegrationConnectionDetailApiPlatform];


export const IntegrationConnectionDetailApiPlatform = {
  langfuse: 'langfuse',
  datadog: 'datadog',
  posthog: 'posthog',
  pagerduty: 'pagerduty',
  mixpanel: 'mixpanel',
  cloud_storage: 'cloud_storage',
  message_queue: 'message_queue',
  linear: 'linear',
} as const;

export type IntegrationConnectionDetailApiStatus = typeof IntegrationConnectionDetailApiStatus[keyof typeof IntegrationConnectionDetailApiStatus];


export const IntegrationConnectionDetailApiStatus = {
  active: 'active',
  paused: 'paused',
  error: 'error',
  syncing: 'syncing',
  backfilling: 'backfilling',
} as const;

export type IntegrationConnectionDetailApiSyncCursor = { [key: string]: unknown };

export type IntegrationConnectionDetailApiBackfillProgress = { [key: string]: unknown };

export interface IntegrationConnectionDetailApi {
  readonly id?: string;
  platform: IntegrationConnectionDetailApiPlatform;
  /**
     * @minLength 1
     * @maxLength 255
     */
  display_name: string;
  /**
     * @minLength 1
     * @maxLength 500
     */
  host_url: string;
  status?: IntegrationConnectionDetailApiStatus;
  status_message?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  external_project_name: string;
  project?: string;
  readonly project_name?: string;
  readonly public_key_display?: string;
  readonly secret_key_display?: string;
  last_synced_at?: string;
  sync_cursor?: IntegrationConnectionDetailApiSyncCursor;
  /**
     * @minimum 60
     * @maximum 1800
     */
  sync_interval_seconds?: number;
  last_error_notified_at?: string;
  backfill_from?: string;
  backfill_completed?: boolean;
  backfill_progress?: IntegrationConnectionDetailApiBackfillProgress;
  /**
     * @minimum 0
     * @maximum 2147483647
     */
  total_traces_synced?: number;
  /**
     * @minimum 0
     * @maximum 2147483647
     */
  total_spans_synced?: number;
  /**
     * @minimum 0
     * @maximum 2147483647
     */
  total_scores_synced?: number;
  readonly created_at?: string;
  readonly updated_at?: string;
  created_by?: string;
}

export interface IntegrationConnectionDetailResponseApi {
  status?: boolean;
  result: IntegrationConnectionDetailApi;
}

export type ValidateCredentialsApiPlatform = typeof ValidateCredentialsApiPlatform[keyof typeof ValidateCredentialsApiPlatform];


export const ValidateCredentialsApiPlatform = {
  langfuse: 'langfuse',
  datadog: 'datadog',
  posthog: 'posthog',
  pagerduty: 'pagerduty',
  mixpanel: 'mixpanel',
  cloud_storage: 'cloud_storage',
  message_queue: 'message_queue',
  linear: 'linear',
} as const;

export type ValidateCredentialsApiCredentials = { [key: string]: unknown };

export interface ValidateCredentialsApi {
  platform: ValidateCredentialsApiPlatform;
  /**
     * @minLength 1
     * @maxLength 500
     */
  host_url?: string;
  /**
     * @minLength 1
     * @maxLength 500
     */
  public_key?: string;
  /**
     * @minLength 1
     * @maxLength 500
     */
  secret_key?: string;
  ca_certificate?: string;
  credentials?: ValidateCredentialsApiCredentials;
}

export interface IntegrationValidationProjectApi {
  id?: string;
  name?: string;
}

export interface IntegrationValidationViewerApi {
  id?: string;
  name?: string;
  email?: string;
}

export interface IntegrationValidationResultApi {
  valid: boolean;
  projects?: IntegrationValidationProjectApi[];
  /** @minimum 0 */
  total_traces?: number;
  error?: string;
  viewer?: IntegrationValidationViewerApi;
}

export interface IntegrationValidationResponseApi {
  status?: boolean;
  result: IntegrationValidationResultApi;
}

export interface IntegrationConnectionUpdateApi {
  /**
     * @minLength 1
     * @maxLength 255
     */
  display_name?: string;
  /**
     * @minLength 1
     * @maxLength 500
     */
  public_key?: string;
  /**
     * @minLength 1
     * @maxLength 500
     */
  secret_key?: string;
  /**
     * @minLength 1
     * @maxLength 500
     */
  host_url?: string;
  ca_certificate?: string;
  /**
     * @minimum 60
     * @maximum 3600
     */
  sync_interval_seconds?: number;
}

export interface IntegrationEmptyRequestApi { [key: string]: unknown }

export interface IntegrationMessageResultApi {
  /** @minLength 1 */
  message: string;
}

export interface IntegrationMessageResponseApi {
  status?: boolean;
  result: IntegrationMessageResultApi;
}

export type SyncLogApiStatus = typeof SyncLogApiStatus[keyof typeof SyncLogApiStatus];


export const SyncLogApiStatus = {
  success: 'success',
  partial: 'partial',
  failed: 'failed',
  rate_limited: 'rate_limited',
  no_new_data: 'no_new_data',
} as const;

export type SyncLogApiErrorDetails = { [key: string]: unknown };

export interface SyncLogApi {
  readonly id?: string;
  readonly connection?: string;
  readonly status?: SyncLogApiStatus;
  readonly started_at?: string;
  readonly completed_at?: string;
  readonly traces_fetched?: number;
  readonly traces_created?: number;
  readonly traces_updated?: number;
  readonly spans_synced?: number;
  readonly scores_synced?: number;
  /** @minLength 1 */
  readonly error_message?: string;
  readonly error_details?: SyncLogApiErrorDetails;
  readonly sync_from?: string;
  readonly sync_to?: string;
}

export interface SyncLogListResultApi {
  metadata: PaginationMetadataApi;
  sync_logs: SyncLogApi[];
}

export interface SyncLogListResponseApi {
  status?: boolean;
  result: SyncLogListResultApi;
}

export interface MCPUsageSummaryApi {
  total_calls: number;
  total_sessions: number;
  avg_latency_ms: number;
  error_rate: number;
  active_sessions: number;
}

export interface MCPAnalyticsSummaryResponseApi {
  status?: boolean;
  result: MCPUsageSummaryApi;
}

export type MCPErrorResponseApiType = typeof MCPErrorResponseApiType[keyof typeof MCPErrorResponseApiType];


export const MCPErrorResponseApiType = {
  validation_error: 'validation_error',
  authentication_error: 'authentication_error',
  payment_required: 'payment_required',
  entitlement_error: 'entitlement_error',
  permission_error: 'permission_error',
  not_found: 'not_found',
  conflict: 'conflict',
  client_error: 'client_error',
  rate_limit: 'rate_limit',
  server_error: 'server_error',
  service_unavailable: 'service_unavailable',
  timeout: 'timeout',
  api_error: 'api_error',
} as const;

export type MCPErrorResponseApiDetails = {[key: string]: string[]};

export interface MCPErrorResponseApi {
  status?: boolean;
  type?: MCPErrorResponseApiType;
  code?: string;
  detail?: string;
  result?: string;
  message?: string;
  error?: string;
  attr?: string;
  details?: MCPErrorResponseApiDetails;
  retry_after?: number;
}

export interface MCPUsageTimelineApi {
  timestamp: string;
  call_count: number;
}

export interface MCPAnalyticsTimelineResponseApi {
  status?: boolean;
  result: MCPUsageTimelineApi[];
}

export interface MCPUsageToolBreakdownApi {
  /** @minLength 1 */
  tool_name: string;
  call_count: number;
  avg_latency_ms: number;
  error_rate: number;
}

export interface MCPAnalyticsToolsResponseApi {
  status?: boolean;
  result: MCPUsageToolBreakdownApi[];
}

export type MCPConnectionResultApiConnectionMode = typeof MCPConnectionResultApiConnectionMode[keyof typeof MCPConnectionResultApiConnectionMode];


export const MCPConnectionResultApiConnectionMode = {
  remote: 'remote',
  stdio: 'stdio',
} as const;

export type MCPToolGroupConfigApiEnabledGroups = { [key: string]: unknown };

export type MCPToolGroupConfigApiDisabledTools = { [key: string]: unknown };

export interface MCPToolGroupConfigApi {
  enabled_groups?: MCPToolGroupConfigApiEnabledGroups;
  disabled_tools?: MCPToolGroupConfigApiDisabledTools;
  readonly available_groups?: string;
}

export interface MCPConnectionResultApi {
  readonly id?: string;
  connection_mode?: MCPConnectionResultApiConnectionMode;
  is_active?: boolean;
  /** @maxLength 100 */
  client_name?: string;
  /** @maxLength 50 */
  client_version?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
  tool_config?: MCPToolGroupConfigApi;
  /** @minLength 1 */
  readonly mcp_url?: string;
}

export interface MCPConnectionResponseApi {
  status?: boolean;
  result: MCPConnectionResultApi;
}

export type MCPConnectionUpdateApiConnectionMode = typeof MCPConnectionUpdateApiConnectionMode[keyof typeof MCPConnectionUpdateApiConnectionMode];


export const MCPConnectionUpdateApiConnectionMode = {
  remote: 'remote',
  stdio: 'stdio',
} as const;

export interface MCPConnectionUpdateApi {
  connection_mode?: MCPConnectionUpdateApiConnectionMode;
  is_active?: boolean;
}

export interface MCPToolGroupsResponseApi {
  status?: boolean;
  result: MCPToolGroupConfigApi;
}

export interface MCPToolGroupConfigUpdateApi {
  enabled_groups?: string[];
  disabled_tools?: string[];
}

export interface MCPHealthResultApi {
  healthy: boolean;
  tool_count: number;
  /** @minLength 1 */
  version: string;
}

export interface MCPHealthResponseApi {
  status?: boolean;
  result: MCPHealthResultApi;
}

export type MCPToolCallRequestApiParams = {[key: string]: string};

export interface MCPToolCallRequestApi {
  /** @minLength 1 */
  tool_name: string;
  params?: MCPToolCallRequestApiParams;
  session_id?: string;
}

export type MCPToolCallResultApiData = { [key: string]: unknown };

export interface MCPToolCallResultApi {
  content: string;
  data: MCPToolCallResultApiData;
  is_error: boolean;
  error_code: string;
}

export interface MCPToolCallResponseApi {
  status: boolean;
  result: MCPToolCallResultApi;
  session_id: string;
}

export interface MCPToolListResultApi {
  tools: ToolDiscoveryItemApi[];
  total: number;
  session_id: string;
}

export interface MCPToolListResponseApi {
  status?: boolean;
  result: MCPToolListResultApi;
}

export interface MCPToolGroupChoiceApi {
  /** @minLength 1 */
  slug: string;
  /** @minLength 1 */
  name: string;
  /** @minLength 1 */
  description: string;
  checked?: boolean;
  enabled?: boolean;
}

export interface MCPOAuthApproveInfoResultApi {
  /** @minLength 1 */
  client_name: string;
  /** @minLength 1 */
  client_id: string;
  scopes: string[];
  /** @minLength 1 */
  redirect_uri: string;
  available_groups: MCPToolGroupChoiceApi[];
}

export interface MCPOAuthApproveInfoResponseApi {
  status?: boolean;
  result: MCPOAuthApproveInfoResultApi;
}

export interface MCPOAuthApproveRequestApi {
  /** @minLength 1 */
  request_id: string;
  approved?: boolean;
  selected_groups?: string[];
}

export interface MCPOAuthRedirectResultApi {
  /** @minLength 1 */
  redirect_url: string;
}

export interface MCPOAuthRedirectResponseApi {
  status?: boolean;
  result: MCPOAuthRedirectResultApi;
}

export interface MCPOAuthAuthorizeResponseResultApi {
  /** @minLength 1 */
  client_name: string;
  /** @minLength 1 */
  client_id: string;
  /** @minLength 1 */
  redirect_uri: string;
  state: string;
  available_groups: MCPToolGroupChoiceApi[];
}

export interface MCPOAuthAuthorizeResponseApi {
  status?: boolean;
  result: MCPOAuthAuthorizeResponseResultApi;
}

export interface MCPOAuthConsentRequestApi {
  /** @minLength 1 */
  client_id: string;
  /** @minLength 1 */
  redirect_uri: string;
  state?: string;
  approved?: boolean;
  selected_groups?: string[];
}

export type MCPOAuthTokenRequestApiGrantType = typeof MCPOAuthTokenRequestApiGrantType[keyof typeof MCPOAuthTokenRequestApiGrantType];


export const MCPOAuthTokenRequestApiGrantType = {
  authorization_code: 'authorization_code',
  refresh_token: 'refresh_token',
} as const;

export interface MCPOAuthTokenRequestApi {
  grant_type: MCPOAuthTokenRequestApiGrantType;
  /** @minLength 1 */
  code?: string;
  /** @minLength 1 */
  refresh_token?: string;
  /** @minLength 1 */
  client_id: string;
  /** @minLength 1 */
  client_secret: string;
  /** @minLength 1 */
  redirect_uri?: string;
}

export type MCPOAuthTokenResponseApiTokenType = typeof MCPOAuthTokenResponseApiTokenType[keyof typeof MCPOAuthTokenResponseApiTokenType];


export const MCPOAuthTokenResponseApiTokenType = {
  Bearer: 'Bearer',
} as const;

export interface MCPOAuthTokenResponseApi {
  /** @minLength 1 */
  access_token: string;
  token_type: MCPOAuthTokenResponseApiTokenType;
  expires_in: number;
  /** @minLength 1 */
  refresh_token?: string;
  scope: string;
}

export interface MCPOAuthTokenErrorResponseApi {
  /** @minLength 1 */
  error: string;
  /** @minLength 1 */
  error_description?: string;
}

export type MCPSessionApiStatus = typeof MCPSessionApiStatus[keyof typeof MCPSessionApiStatus];


export const MCPSessionApiStatus = {
  active: 'active',
  idle: 'idle',
  disconnected: 'disconnected',
  revoked: 'revoked',
} as const;

export type MCPSessionApiTransport = typeof MCPSessionApiTransport[keyof typeof MCPSessionApiTransport];


export const MCPSessionApiTransport = {
  streamable_http: 'streamable_http',
  sse: 'sse',
  stdio: 'stdio',
} as const;

export interface MCPSessionApi {
  readonly id?: string;
  status?: MCPSessionApiStatus;
  transport?: MCPSessionApiTransport;
  /** @maxLength 100 */
  client_name?: string;
  /** @maxLength 50 */
  client_version?: string;
  /** @maxLength 50 */
  client_os?: string;
  readonly started_at?: string;
  readonly last_activity_at?: string;
  ended_at?: string;
  /**
     * @minimum 0
     * @maximum 2147483647
     */
  tool_call_count?: number;
  /**
     * @minimum 0
     * @maximum 2147483647
     */
  error_count?: number;
}

export interface MCPSessionListResponseApi {
  status?: boolean;
  result: MCPSessionApi[];
}

export interface MCPSessionRevokeResultApi {
  /** @minLength 1 */
  message: string;
}

export interface MCPSessionRevokeResponseApi {
  status?: boolean;
  result: MCPSessionRevokeResultApi;
}

export type AIEvalWriterRequestApiOutputFormat = typeof AIEvalWriterRequestApiOutputFormat[keyof typeof AIEvalWriterRequestApiOutputFormat];


export const AIEvalWriterRequestApiOutputFormat = {
  prompt: 'prompt',
  messages: 'messages',
  test_data: 'test_data',
} as const;

export interface AIEvalWriterRequestApi {
  /** @minLength 1 */
  description: string;
  output_format?: AIEvalWriterRequestApiOutputFormat;
}

export type AIEvalWriterResultApiMessagesItem = {[key: string]: string};

export type AIEvalWriterResultApiTestData = {[key: string]: string};

export interface AIEvalWriterResultApi {
  /** @minLength 1 */
  prompt?: string;
  messages?: AIEvalWriterResultApiMessagesItem[];
  test_data?: AIEvalWriterResultApiTestData;
}

export interface AIEvalWriterResponseApi {
  status?: boolean;
  result: AIEvalWriterResultApi;
}

export type ModelHubErrorResponseApiType = typeof ModelHubErrorResponseApiType[keyof typeof ModelHubErrorResponseApiType];


export const ModelHubErrorResponseApiType = {
  validation_error: 'validation_error',
  authentication_error: 'authentication_error',
  payment_required: 'payment_required',
  entitlement_error: 'entitlement_error',
  permission_error: 'permission_error',
  not_found: 'not_found',
  conflict: 'conflict',
  client_error: 'client_error',
  rate_limit: 'rate_limit',
  server_error: 'server_error',
  service_unavailable: 'service_unavailable',
  timeout: 'timeout',
  api_error: 'api_error',
} as const;

export type ModelHubErrorResponseApiDetails = {[key: string]: string[]};

export interface ModelHubErrorResponseApi {
  status?: boolean;
  type?: ModelHubErrorResponseApiType;
  code?: string;
  detail?: string;
  result?: string;
  message?: string;
  error?: string;
  attr?: string;
  details?: ModelHubErrorResponseApiDetails;
}

export type AIFilterRequestApiMode = typeof AIFilterRequestApiMode[keyof typeof AIFilterRequestApiMode];


export const AIFilterRequestApiMode = {
  build_filters: 'build_filters',
  select_fields: 'select_fields',
  smart: 'smart',
} as const;

export type AIFilterRequestApiSource = typeof AIFilterRequestApiSource[keyof typeof AIFilterRequestApiSource];


export const AIFilterRequestApiSource = {
  traces: 'traces',
  dataset: 'dataset',
} as const;

export type AIFilterSchemaFieldApiChoicesItem = { [key: string]: unknown };

export type AIFilterSchemaFieldApiChoiceLabels = {[key: string]: string};

export interface AIFilterSchemaFieldApi {
  /** @minLength 1 */
  field: string;
  label?: string;
  type?: string;
  category?: string;
  operators?: string[];
  choices?: AIFilterSchemaFieldApiChoicesItem[];
  choice_labels?: AIFilterSchemaFieldApiChoiceLabels;
}

export interface AIFilterRequestApi {
  mode?: AIFilterRequestApiMode;
  /** @minLength 1 */
  query: string;
  schema: AIFilterSchemaFieldApi[];
  source?: AIFilterRequestApiSource;
  project_id?: string;
  dataset_id?: string;
}

/**
 * Any valid JSON value.
 */
export type AIFilterConditionApiValue = { [key: string]: unknown };

export interface AIFilterConditionApi {
  /** @minLength 1 */
  field: string;
  /** @minLength 1 */
  operator: string;
  /** Any valid JSON value. */
  value?: AIFilterConditionApiValue;
}

export interface AIFilterResultApi {
  filters?: AIFilterConditionApi[];
  fields?: string[];
}

export interface AIFilterResponseApi {
  status?: boolean;
  result: AIFilterResultApi;
}

export type AnnotationQueueApiStatus = typeof AnnotationQueueApiStatus[keyof typeof AnnotationQueueApiStatus];


export const AnnotationQueueApiStatus = {
  draft: 'draft',
  active: 'active',
  paused: 'paused',
  completed: 'completed',
} as const;

export type AnnotationQueueApiAssignmentStrategy = typeof AnnotationQueueApiAssignmentStrategy[keyof typeof AnnotationQueueApiAssignmentStrategy];


export const AnnotationQueueApiAssignmentStrategy = {
  manual: 'manual',
  round_robin: 'round_robin',
  load_balanced: 'load_balanced',
} as const;

export type AnnotationQueueApiAnnotatorRoles = {[key: string]: { [key: string]: unknown }};

export interface QueueLabelNestedApi {
  readonly id?: string;
  label_id: string;
  /** @minLength 1 */
  readonly name?: string;
  /** @minLength 1 */
  readonly type?: string;
  required?: boolean;
  /**
     * @minimum -2147483648
     * @maximum 2147483647
     */
  order?: number;
}

export interface QueueAnnotatorNestedApi {
  readonly id?: string;
  user_id: string;
  /** @minLength 1 */
  readonly name?: string;
  /** @minLength 1 */
  readonly email?: string;
  /** @minLength 1 */
  role?: string;
  readonly roles?: string;
}

export interface AnnotationQueueApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  description?: string;
  instructions?: string;
  readonly status?: AnnotationQueueApiStatus;
  assignment_strategy?: AnnotationQueueApiAssignmentStrategy;
  /**
     * @minimum -2147483648
     * @maximum 2147483647
     */
  annotations_required?: number;
  /**
     * @minimum -2147483648
     * @maximum 2147483647
     */
  reservation_timeout_minutes?: number;
  requires_review?: boolean;
  /** When enabled, all queue members can annotate any item without explicit assignment. */
  auto_assign?: boolean;
  readonly organization?: string;
  readonly project?: string;
  readonly dataset?: string;
  readonly agent_definition?: string;
  readonly is_default?: boolean;
  readonly labels?: readonly QueueLabelNestedApi[];
  readonly annotators?: readonly QueueAnnotatorNestedApi[];
  /** @minItems 1 */
  label_ids: string[];
  annotator_ids?: string[];
  annotator_roles?: AnnotationQueueApiAnnotatorRoles;
  readonly label_count?: number;
  readonly annotator_count?: number;
  readonly item_count?: number;
  readonly completed_count?: number;
  readonly created_by?: string;
  /** @minLength 1 */
  readonly created_by_name?: string;
  readonly viewer_role?: string;
  readonly viewer_roles?: string;
  readonly deleted?: boolean;
  readonly created_at?: string;
}

export interface QueueForSourceQueueApi {
  id: string;
  /** @minLength 1 */
  name: string;
  instructions: string;
  is_default: boolean;
}

export interface QueueForSourceItemApi {
  id: string;
  /** @minLength 1 */
  status: string;
  /** @minLength 1 */
  source_type: string;
  /** @minLength 1 */
  source_id: string;
}

export type QueueLabelResultApiSettings = { [key: string]: unknown };

export interface QueueLabelResultApi {
  id: string;
  /** @minLength 1 */
  name: string;
  /** @minLength 1 */
  type: string;
  settings: QueueLabelResultApiSettings;
  description?: string;
  allow_notes: boolean;
  required: boolean;
  order: number;
}

export type QueueForSourceEntryApiExistingScores = {[key: string]: { [key: string]: unknown }};

export type QueueForSourceEntryApiExistingLabelNotes = {[key: string]: string};

export type QueueForSourceEntryApiSpanNotesItem = { [key: string]: unknown };

export interface QueueForSourceEntryApi {
  queue: QueueForSourceQueueApi;
  item: QueueForSourceItemApi;
  labels: QueueLabelResultApi[];
  existing_scores: QueueForSourceEntryApiExistingScores;
  existing_notes: string;
  existing_label_notes: QueueForSourceEntryApiExistingLabelNotes;
  span_notes: QueueForSourceEntryApiSpanNotesItem[];
  /** @minLength 1 */
  span_notes_source_id?: string;
}

export interface QueueForSourceResponseApi {
  status?: boolean;
  result: QueueForSourceEntryApi[];
}

export interface QueueDefaultRequestApi {
  project_id?: string;
  dataset_id?: string;
  agent_definition_id?: string;
}

export interface QueueDefaultQueueApi {
  id: string;
  /** @minLength 1 */
  name: string;
  description?: string;
  instructions?: string;
  /** @minLength 1 */
  status: string;
  is_default: boolean;
}

export type QueueDefaultResultApiAction = typeof QueueDefaultResultApiAction[keyof typeof QueueDefaultResultApiAction];


export const QueueDefaultResultApiAction = {
  created: 'created',
  restored: 'restored',
  fetched: 'fetched',
} as const;

export interface QueueDefaultResultApi {
  queue: QueueDefaultQueueApi;
  labels: QueueLabelResultApi[];
  created: boolean;
  action: QueueDefaultResultApiAction;
}

export interface QueueDefaultResponseApi {
  status?: boolean;
  result: QueueDefaultResultApi;
}

export interface QueueLabelRequestApi {
  label_id: string;
  required?: boolean;
}

export interface QueueAddLabelResultApi {
  label: QueueLabelResultApi;
  created: boolean;
  reopened_items: number;
  /** @minLength 1 */
  queue_status: string;
}

export interface QueueAddLabelResponseApi {
  status?: boolean;
  result: QueueAddLabelResultApi;
}

export interface QueueAgreementLabelApi {
  label_name: string;
  label_type: string;
  agreement_pct: number;
  cohens_kappa: number;
  disagreement_count: number;
  disagreement_items: string[];
}

export interface QueueAgreementAnnotatorPairApi {
  /** @minLength 1 */
  annotator_1_id: string;
  /** @minLength 1 */
  annotator_2_id: string;
  agreement_pct: number;
  total_comparisons: number;
}

export type QueueAgreementResultApiLabels = {[key: string]: QueueAgreementLabelApi};

export interface QueueAgreementResultApi {
  overall_agreement: number;
  labels: QueueAgreementResultApiLabels;
  annotator_pairs: QueueAgreementAnnotatorPairApi[];
}

export interface QueueAgreementResponseApi {
  status?: boolean;
  result: QueueAgreementResultApi;
}

export interface QueueAnalyticsThroughputDailyApi {
  /** @minLength 1 */
  date: string;
  count: number;
}

export interface QueueAnalyticsThroughputApi {
  daily: QueueAnalyticsThroughputDailyApi[];
  total_completed: number;
  avg_per_day: number;
}

export interface QueueAnalyticsAnnotatorPerformanceApi {
  /** @minLength 1 */
  user_id?: string;
  name?: string;
  completed: number;
  last_active?: string;
}

export type QueueAnalyticsResultApiLabelDistribution = {[key: string]: { [key: string]: unknown }};

export type QueueAnalyticsResultApiStatusBreakdown = {[key: string]: number};

export interface QueueAnalyticsResultApi {
  throughput: QueueAnalyticsThroughputApi;
  annotator_performance: QueueAnalyticsAnnotatorPerformanceApi[];
  label_distribution: QueueAnalyticsResultApiLabelDistribution;
  status_breakdown: QueueAnalyticsResultApiStatusBreakdown;
  total: number;
}

export interface QueueAnalyticsResponseApi {
  status?: boolean;
  result: QueueAnalyticsResultApi;
}

export interface QueueExportFieldApi {
  /** @minLength 1 */
  id: string;
  /** @minLength 1 */
  label: string;
  /** @minLength 1 */
  column: string;
  /** @minLength 1 */
  data_type: string;
  /** @minLength 1 */
  group: string;
  default: boolean;
  path?: string;
  source_type?: string;
  kind?: string;
  label_id?: string;
  slot?: number;
  eval_key?: string;
  expand_fields?: string[];
}

export interface QueueExportDefaultMappingApi {
  /** @minLength 1 */
  field: string;
  /** @minLength 1 */
  column: string;
  enabled: boolean;
}

export interface QueueExportFieldsResultApi {
  fields: QueueExportFieldApi[];
  default_mapping: QueueExportDefaultMappingApi[];
}

export interface QueueExportFieldsResponseApi {
  status?: boolean;
  result: QueueExportFieldsResultApi;
}

export interface QueueExportColumnMappingApi {
  field?: string;
  id?: string;
  column?: string;
  enabled?: boolean;
}

export interface QueueExportToDatasetRequestApi {
  dataset_id?: string;
  dataset_name?: string;
  status_filter?: string;
  column_mapping?: QueueExportColumnMappingApi[];
}

export interface QueueExportToDatasetResultApi {
  dataset_id: string;
  /** @minLength 1 */
  dataset_name: string;
  rows_created: number;
  columns: string[];
}

export interface QueueExportToDatasetResponseApi {
  status?: boolean;
  result: QueueExportToDatasetResultApi;
}

export type QueueExportAnnotationsResponseApiResultItem = { [key: string]: unknown };

export interface QueueExportAnnotationsResponseApi {
  status?: boolean;
  result: QueueExportAnnotationsResponseApiResultItem[];
}

export interface QueueHardDeleteRequestApi {
  force: boolean;
  /** @minLength 1 */
  confirm_name: string;
}

export interface QueueHardDeleteResultApi {
  deleted: boolean;
  hard_deleted?: boolean;
  archived?: boolean;
  queue_id: string;
}

export interface QueueHardDeleteResponseApi {
  status?: boolean;
  result: QueueHardDeleteResultApi;
}

export interface QueueProgressAnnotatorStatApi {
  user_id: string;
  /** @minLength 1 */
  name?: string;
  completed: number;
  pending: number;
  in_progress: number;
  in_review: number;
  annotations_count: number;
}

export interface QueueProgressUserProgressApi {
  total: number;
  completed: number;
  pending: number;
  in_progress: number;
  in_review: number;
  skipped: number;
  progress_pct: number;
}

export interface QueueProgressResultApi {
  total: number;
  pending: number;
  in_progress: number;
  in_review: number;
  completed: number;
  skipped: number;
  progress_pct: number;
  annotator_stats: QueueProgressAnnotatorStatApi[];
  user_progress: QueueProgressUserProgressApi;
}

export interface QueueProgressResponseApi {
  status?: boolean;
  result: QueueProgressResultApi;
}

export interface QueueRemoveLabelResultApi {
  removed: boolean;
}

export interface QueueRemoveLabelResponseApi {
  status?: boolean;
  result: QueueRemoveLabelResultApi;
}

export interface EmptyRequestApi { [key: string]: unknown }

export interface QueueStatusResponseApi {
  status?: boolean;
  result: AnnotationQueueApi;
}

export type QueueStatusRequestApiStatus = typeof QueueStatusRequestApiStatus[keyof typeof QueueStatusRequestApiStatus];


export const QueueStatusRequestApiStatus = {
  draft: 'draft',
  active: 'active',
  paused: 'paused',
  completed: 'completed',
} as const;

export interface QueueStatusRequestApi {
  status: QueueStatusRequestApiStatus;
}

export type AutomationRuleApiSourceType = typeof AutomationRuleApiSourceType[keyof typeof AutomationRuleApiSourceType];


export const AutomationRuleApiSourceType = {
  dataset_row: 'dataset_row',
  trace: 'trace',
  observation_span: 'observation_span',
  prototype_run: 'prototype_run',
  call_execution: 'call_execution',
  trace_session: 'trace_session',
} as const;

export type AutomationRuleApiTriggerFrequency = typeof AutomationRuleApiTriggerFrequency[keyof typeof AutomationRuleApiTriggerFrequency];


export const AutomationRuleApiTriggerFrequency = {
  manual: 'manual',
  hourly: 'hourly',
  daily: 'daily',
  weekly: 'weekly',
  monthly: 'monthly',
} as const;

export type AutomationRuleConditionsApiOperator = typeof AutomationRuleConditionsApiOperator[keyof typeof AutomationRuleConditionsApiOperator];


export const AutomationRuleConditionsApiOperator = {
  and: 'and',
} as const;

export interface AutomationRuleScopeApi {
  dataset_id?: string;
  project_id?: string;
  is_voice_call?: boolean;
  remove_simulation_calls?: boolean;
}

export type AutomationRuleConditionsApiFilterItemFilterConfig = {
  /** Canonical field type, for example text, number, boolean, datetime, categorical, thumbs, annotator, or array. */
  filter_type: string;
  /** Canonical operator from api_contracts/filter_contract.json, for example equals, not_equals, in, not_in, between, not_between, is_null, or is_not_null. */
  filter_op: string;
  /** Scalar, list, range tuple, boolean, or null depending on filter_op and filter_type. */
  filter_value?: unknown;
  /** Column family such as SYSTEM_METRIC, SPAN_ATTRIBUTE, EVAL_METRIC, ANNOTATION, or NORMAL. */
  col_type?: string;
};

export type AutomationRuleConditionsApiFilterItem = {
  /** Column or attribute id to filter on. */
  column_id: string;
  /** Optional UI label for chips and saved views. */
  display_name?: string;
  /** Optional source surface for mixed-source filters, for example traces, datasets, or simulation. */
  source?: string;
  /** Optional metric output type metadata used by eval and annotation filters. */
  output_type?: string;
  filter_config: AutomationRuleConditionsApiFilterItemFilterConfig;
};

export type AutomationRuleConditionsApiRulesItem = {
  /** @minLength 1 */
  field: string;
  /** @minLength 1 */
  op?: string;
  /** Rule comparison value. Can be a scalar, list, object, boolean, or null depending on the operator. */
  value?: unknown;
};

export interface AutomationRuleConditionsApi {
  operator?: AutomationRuleConditionsApiOperator;
  filter?: AutomationRuleConditionsApiFilterItem[];
  scope?: AutomationRuleScopeApi;
  rules?: AutomationRuleConditionsApiRulesItem[];
}

export interface AutomationRuleApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  readonly queue?: string;
  source_type: AutomationRuleApiSourceType;
  conditions?: AutomationRuleConditionsApi;
  enabled?: boolean;
  trigger_frequency?: AutomationRuleApiTriggerFrequency;
  readonly organization?: string;
  readonly created_by?: string;
  /** @minLength 1 */
  readonly created_by_name?: string;
  readonly last_triggered_at?: string;
  readonly trigger_count?: number;
  readonly created_at?: string;
}

export interface AutomationRuleEvaluateResultApi {
  matched: number;
  added: number;
  duplicates: number;
  truncated?: boolean;
  error?: string;
}

export interface AutomationRuleEvaluateResponseApi {
  status?: boolean;
  result: AutomationRuleEvaluateResultApi;
}

export interface AutomationRuleEvaluateAcceptedResponseApi {
  /** @minLength 1 */
  status: string;
  /** @minLength 1 */
  workflow_id: string;
  /** @minLength 1 */
  message: string;
}

export type QueueItemApiSourceType = typeof QueueItemApiSourceType[keyof typeof QueueItemApiSourceType];


export const QueueItemApiSourceType = {
  dataset_row: 'dataset_row',
  trace: 'trace',
  observation_span: 'observation_span',
  prototype_run: 'prototype_run',
  call_execution: 'call_execution',
  trace_session: 'trace_session',
} as const;

export type QueueItemApiStatus = typeof QueueItemApiStatus[keyof typeof QueueItemApiStatus];


export const QueueItemApiStatus = {
  pending: 'pending',
  in_progress: 'in_progress',
  completed: 'completed',
  skipped: 'skipped',
} as const;

export type QueueItemApiMetadata = { [key: string]: unknown };

export interface QueueItemApi {
  readonly id?: string;
  readonly queue?: string;
  source_type: QueueItemApiSourceType;
  /** @minLength 1 */
  source_id?: string;
  status?: QueueItemApiStatus;
  readonly workflow_status?: string;
  readonly workflow_status_label?: string;
  /**
     * @minimum -2147483648
     * @maximum 2147483647
     */
  priority?: number;
  /**
     * @minimum -2147483648
     * @maximum 2147483647
     */
  order?: number;
  metadata?: QueueItemApiMetadata;
  assigned_to?: string;
  /** @minLength 1 */
  readonly assigned_to_name?: string;
  readonly assigned_users?: string;
  reserved_by?: string;
  /** @minLength 1 */
  readonly reserved_by_name?: string;
  reservation_expires_at?: string;
  /** @maxLength 20 */
  review_status?: string;
  reviewed_by?: string;
  /** @minLength 1 */
  readonly reviewed_by_name?: string;
  reviewed_at?: string;
  review_notes?: string;
  readonly source_preview?: string;
  readonly comment_count?: string;
  readonly open_feedback_count?: string;
  readonly created_at?: string;
}

export type AddQueueItemApiSourceType = typeof AddQueueItemApiSourceType[keyof typeof AddQueueItemApiSourceType];


export const AddQueueItemApiSourceType = {
  call_execution: 'call_execution',
  dataset_row: 'dataset_row',
  observation_span: 'observation_span',
  prototype_run: 'prototype_run',
  trace: 'trace',
  trace_session: 'trace_session',
} as const;

export interface AddQueueItemApi {
  source_type: AddQueueItemApiSourceType;
  /** @minLength 1 */
  source_id: string;
}

export type SelectionApiMode = typeof SelectionApiMode[keyof typeof SelectionApiMode];


export const SelectionApiMode = {
  filter: 'filter',
} as const;

export type SelectionApiSourceType = typeof SelectionApiSourceType[keyof typeof SelectionApiSourceType];


export const SelectionApiSourceType = {
  call_execution: 'call_execution',
  observation_span: 'observation_span',
  trace: 'trace',
  trace_session: 'trace_session',
} as const;

export type SelectionApiFilterItemFilterConfig = {
  /** Canonical field type, for example text, number, boolean, datetime, categorical, thumbs, annotator, or array. */
  filter_type: string;
  /** Canonical operator from api_contracts/filter_contract.json, for example equals, not_equals, in, not_in, between, not_between, is_null, or is_not_null. */
  filter_op: string;
  /** Scalar, list, range tuple, boolean, or null depending on filter_op and filter_type. */
  filter_value?: unknown;
  /** Column family such as SYSTEM_METRIC, SPAN_ATTRIBUTE, EVAL_METRIC, ANNOTATION, or NORMAL. */
  col_type?: string;
};

export type SelectionApiFilterItem = {
  /** Column or attribute id to filter on. */
  column_id: string;
  /** Optional UI label for chips and saved views. */
  display_name?: string;
  /** Optional source surface for mixed-source filters, for example traces, datasets, or simulation. */
  source?: string;
  /** Optional metric output type metadata used by eval and annotation filters. */
  output_type?: string;
  filter_config: SelectionApiFilterItemFilterConfig;
};

export interface SelectionApi {
  mode: SelectionApiMode;
  source_type: SelectionApiSourceType;
  project_id: string;
  filter?: SelectionApiFilterItem[];
  exclude_ids?: string[];
  remove_simulation_calls?: boolean;
  is_voice_call?: boolean;
}

export interface AddItemsApi {
  items?: AddQueueItemApi[];
  selection?: SelectionApi;
  project_id?: string;
}

export interface QueueAddItemsResultApi {
  added: number;
  duplicates: number;
  errors: string[];
  /** @minLength 1 */
  queue_status: string;
  total_matching?: number;
}

export interface QueueAddItemsResponseApi {
  status?: boolean;
  result: QueueAddItemsResultApi;
}

export type ApiSelectionTooLargeErrorApiType = typeof ApiSelectionTooLargeErrorApiType[keyof typeof ApiSelectionTooLargeErrorApiType];


export const ApiSelectionTooLargeErrorApiType = {
  selection_too_large: 'selection_too_large',
  export_too_large: 'export_too_large',
  items_too_large: 'items_too_large',
} as const;

export type ApiSelectionTooLargeDetailApiType = typeof ApiSelectionTooLargeDetailApiType[keyof typeof ApiSelectionTooLargeDetailApiType];


export const ApiSelectionTooLargeDetailApiType = {
  selection_too_large: 'selection_too_large',
  export_too_large: 'export_too_large',
  items_too_large: 'items_too_large',
} as const;

export interface ApiSelectionTooLargeDetailApi {
  type: ApiSelectionTooLargeDetailApiType;
  /** @minLength 1 */
  message: string;
  total_matching: number;
  cap: number;
}

export interface ApiSelectionTooLargeErrorApi {
  status?: boolean;
  /** @minLength 1 */
  result?: string;
  type?: ApiSelectionTooLargeErrorApiType;
  /** @minLength 1 */
  code?: string;
  /** @minLength 1 */
  detail?: string;
  /** @minLength 1 */
  message: string;
  error: ApiSelectionTooLargeDetailApi;
}

export type AssignItemsApiAction = typeof AssignItemsApiAction[keyof typeof AssignItemsApiAction];


export const AssignItemsApiAction = {
  add: 'add',
  set: 'set',
  remove: 'remove',
} as const;

export interface AssignItemsApi {
  /** @minItems 1 */
  item_ids: string[];
  user_ids?: string[];
  action?: AssignItemsApiAction;
}

export interface QueueAssignItemsResultApi {
  assigned: number;
}

export interface QueueAssignItemsResponseApi {
  status?: boolean;
  result: QueueAssignItemsResultApi;
}

export interface BulkRemoveItemsApi {
  /** @minItems 1 */
  item_ids: string[];
}

export interface QueueBulkRemoveItemsResultApi {
  removed: number;
}

export interface QueueBulkRemoveItemsResponseApi {
  status?: boolean;
  result: QueueBulkRemoveItemsResultApi;
}

export type BulkReviewItemsRequestApiAction = typeof BulkReviewItemsRequestApiAction[keyof typeof BulkReviewItemsRequestApiAction];


export const BulkReviewItemsRequestApiAction = {
  approve: 'approve',
  request_changes: 'request_changes',
  reject: 'reject',
} as const;

export interface BulkReviewItemsRequestApi {
  /** @minItems 1 */
  item_ids: string[];
  action: BulkReviewItemsRequestApiAction;
  notes?: string;
}

export interface QueueBulkReviewItemsErrorApi {
  /** @minLength 1 */
  item_id: string;
  /** @minLength 1 */
  error: string;
}

export type QueueBulkReviewItemsResultApiAction = typeof QueueBulkReviewItemsResultApiAction[keyof typeof QueueBulkReviewItemsResultApiAction];


export const QueueBulkReviewItemsResultApiAction = {
  approve: 'approve',
  request_changes: 'request_changes',
  reject: 'reject',
} as const;

export interface QueueBulkReviewItemsResultApi {
  reviewed: number;
  reviewed_item_ids: string[];
  errors: QueueBulkReviewItemsErrorApi[];
  action: QueueBulkReviewItemsResultApiAction;
}

export interface QueueBulkReviewItemsResponseApi {
  status?: boolean;
  result: QueueBulkReviewItemsResultApi;
}

export type QueueNextItemResultApiItem = { [key: string]: unknown };

export interface QueueNextItemResultApi {
  item: QueueNextItemResultApiItem;
}

export interface QueueNextItemResponseApi {
  status?: boolean;
  result: QueueNextItemResultApi;
}

export type QueueAnnotateDetailResultApiItem = { [key: string]: unknown };

export type QueueAnnotateDetailResultApiQueue = { [key: string]: unknown };

export type QueueAnnotateDetailResultApiLabelsItem = { [key: string]: unknown };

export type QueueAnnotateDetailResultApiAnnotationsItem = { [key: string]: unknown };

export type QueueAnnotateDetailResultApiReviewCommentsItem = { [key: string]: unknown };

export type QueueAnnotateDetailResultApiReviewThreadsItem = { [key: string]: unknown };

export type QueueAnnotateDetailResultApiSpanNotesItem = { [key: string]: unknown };

export type QueueAnnotateDetailResultApiProgress = { [key: string]: unknown };

export interface QueueAnnotateDetailResultApi {
  item: QueueAnnotateDetailResultApiItem;
  queue: QueueAnnotateDetailResultApiQueue;
  labels: QueueAnnotateDetailResultApiLabelsItem[];
  annotations: QueueAnnotateDetailResultApiAnnotationsItem[];
  review_comments: QueueAnnotateDetailResultApiReviewCommentsItem[];
  review_threads: QueueAnnotateDetailResultApiReviewThreadsItem[];
  existing_notes: string;
  span_notes: QueueAnnotateDetailResultApiSpanNotesItem[];
  /** @minLength 1 */
  span_notes_source_id?: string;
  progress: QueueAnnotateDetailResultApiProgress;
  /** @minLength 1 */
  next_item_id?: string;
  /** @minLength 1 */
  prev_item_id?: string;
}

export interface QueueAnnotateDetailResponseApi {
  status?: boolean;
  result: QueueAnnotateDetailResultApi;
}

export type ScoreApiSourceType = typeof ScoreApiSourceType[keyof typeof ScoreApiSourceType];


export const ScoreApiSourceType = {
  dataset_row: 'dataset_row',
  trace: 'trace',
  observation_span: 'observation_span',
  prototype_run: 'prototype_run',
  call_execution: 'call_execution',
  trace_session: 'trace_session',
} as const;

export type ScoreApiScoreSource = typeof ScoreApiScoreSource[keyof typeof ScoreApiScoreSource];


export const ScoreApiScoreSource = {
  human: 'human',
  api: 'api',
  auto: 'auto',
  imported: 'imported',
} as const;

export type ScoreApiLabelSettings = { [key: string]: unknown };

export type ScoreApiValue = { [key: string]: unknown };

export type ScoreApiValueHistory = { [key: string]: unknown };

export interface ScoreApi {
  readonly id?: string;
  source_type: ScoreApiSourceType;
  readonly source_id?: string;
  readonly label_id?: string;
  /** @minLength 1 */
  readonly label_name?: string;
  /** @minLength 1 */
  readonly label_type?: string;
  readonly label_settings?: ScoreApiLabelSettings;
  readonly label_allow_notes?: boolean;
  value: ScoreApiValue;
  value_history?: ScoreApiValueHistory;
  score_source?: ScoreApiScoreSource;
  notes?: string;
  readonly annotator?: string;
  /** @minLength 1 */
  readonly annotator_name?: string;
  /** @minLength 1 */
  readonly annotator_email?: string;
  readonly queue_item?: string;
  readonly queue_id?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
}

export interface QueueItemAnnotationsResponseApi {
  status?: boolean;
  result: ScoreApi[];
}

export type ImportAnnotationEntryApiValue = { [key: string]: unknown };

export interface ImportAnnotationEntryApi {
  label_id: string;
  value: ImportAnnotationEntryApiValue;
  notes?: string;
  score_source?: string;
}

export interface ImportAnnotationsApi {
  annotations: ImportAnnotationEntryApi[];
  annotator_id?: string;
}

export interface QueueImportAnnotationsResultApi {
  imported: number;
}

export interface QueueImportAnnotationsResponseApi {
  status?: boolean;
  result: QueueImportAnnotationsResultApi;
}

export type SubmitAnnotationEntryApiValue = { [key: string]: unknown };

export interface SubmitAnnotationEntryApi {
  label_id: string;
  value: SubmitAnnotationEntryApiValue;
  notes?: string;
}

export interface SubmitAnnotationsApi {
  annotations: SubmitAnnotationEntryApi[];
  notes?: string;
  item_notes?: string;
}

export interface QueueSubmitAnnotationsResultApi {
  submitted: number;
}

export interface QueueSubmitAnnotationsResponseApi {
  status?: boolean;
  result: QueueSubmitAnnotationsResultApi;
}

export interface QueueItemNavigationRequestApi {
  exclude?: string[];
  exclude_review_status?: string;
  include_completed?: boolean;
}

export type QueueNavigationResultApiNextItem = { [key: string]: unknown };

export interface QueueNavigationResultApi {
  completed_item_id?: string;
  skipped_item_id?: string;
  next_item: QueueNavigationResultApiNextItem;
}

export interface QueueNavigationResponseApi {
  status?: boolean;
  result: QueueNavigationResultApi;
}

export type QueueDiscussionResultApiReviewCommentsItem = { [key: string]: unknown };

export type QueueDiscussionResultApiReviewThreadsItem = { [key: string]: unknown };

export type QueueDiscussionResultApiComment = { [key: string]: unknown };

export type QueueDiscussionResultApiThread = { [key: string]: unknown };

export interface QueueDiscussionResultApi {
  review_comments: QueueDiscussionResultApiReviewCommentsItem[];
  review_threads: QueueDiscussionResultApiReviewThreadsItem[];
  comment?: QueueDiscussionResultApiComment;
  thread?: QueueDiscussionResultApiThread;
}

export interface QueueDiscussionResponseApi {
  status?: boolean;
  result: QueueDiscussionResultApi;
}

export interface DiscussionCommentRequestApi {
  comment?: string;
  label_id?: string;
  target_annotator_id?: string;
  thread_id?: string;
  mentioned_user_ids?: string[];
}

export interface DiscussionReactionRequestApi {
  /** @maxLength 16 */
  emoji?: string;
}

export interface DiscussionThreadStatusRequestApi {
  comment?: string;
}

export interface QueueReleaseReservationResultApi {
  released: boolean;
}

export interface QueueReleaseReservationResponseApi {
  status?: boolean;
  result: QueueReleaseReservationResultApi;
}

export type ReviewItemRequestApiAction = typeof ReviewItemRequestApiAction[keyof typeof ReviewItemRequestApiAction];


export const ReviewItemRequestApiAction = {
  approve: 'approve',
  request_changes: 'request_changes',
  reject: 'reject',
  comment: 'comment',
} as const;

export interface ReviewLabelCommentRequestApi {
  label_id?: string;
  target_annotator_id?: string;
  comment?: string;
}

export interface ReviewItemRequestApi {
  action: ReviewItemRequestApiAction;
  notes?: string;
  label_comments?: ReviewLabelCommentRequestApi[];
}

export type QueueReviewItemResultApiNextItem = { [key: string]: unknown };

export type QueueReviewItemResultApiReviewCommentsItem = { [key: string]: unknown };

export type QueueReviewItemResultApiReviewThreadsItem = { [key: string]: unknown };

export interface QueueReviewItemResultApi {
  reviewed_item_id: string;
  /** @minLength 1 */
  action: string;
  next_item: QueueReviewItemResultApiNextItem;
  review_comments: QueueReviewItemResultApiReviewCommentsItem[];
  review_threads: QueueReviewItemResultApiReviewThreadsItem[];
}

export interface QueueReviewItemResponseApi {
  status?: boolean;
  result: QueueReviewItemResultApi;
}

export type UserApiOrganizationRole = typeof UserApiOrganizationRole[keyof typeof UserApiOrganizationRole];


export const UserApiOrganizationRole = {
  Owner: 'Owner',
  Admin: 'Admin',
  Member: 'Member',
  Viewer: 'Viewer',
  workspace_admin: 'workspace_admin',
  workspace_member: 'workspace_member',
  workspace_viewer: 'workspace_viewer',
} as const;

export interface OrganizationApi {
  readonly id?: string;
  readonly created_at?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  /** @maxLength 255 */
  display_name?: string;
  is_new?: boolean;
  ws_enabled?: boolean;
  /**
     * @minLength 1
     * @maxLength 16
     */
  region?: string;
  require_2fa?: boolean;
  /**
     * @minimum 0
     * @maximum 32767
     */
  require_2fa_grace_period_days?: number;
  require_2fa_enforced_at?: string;
}

/**
 * List of user's goals for using the platform
 */
export type UserApiGoals = { [key: string]: unknown };

export interface UserApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 254
     */
  email: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  organization_role?: UserApiOrganizationRole;
  organization?: OrganizationApi;
  readonly created_at?: string;
  readonly status?: string;
  /**
     * User's job role (e.g., Data Scientist, ML Engineer, or custom role)
     * @maxLength 255
     */
  role?: string;
  /** List of user's goals for using the platform */
  goals?: UserApiGoals;
}

export type MonitorApiMonitorType = typeof MonitorApiMonitorType[keyof typeof MonitorApiMonitorType];


export const MonitorApiMonitorType = {
  Analytics: 'Analytics',
  DataDrift: 'DataDrift',
  Performance: 'Performance',
} as const;

export interface MonitorApi {
  readonly id?: number;
  /** Indicates if the alert is executed */
  status?: boolean;
  /**
     * Name of the monitor
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  monitor_type: MonitorApiMonitorType;
  /**
     * Dimension of the monitor
     * @minLength 1
     * @maxLength 255
     */
  dimension: string;
  /**
     * Metric used by the monitor
     * @minLength 1
     * @maxLength 255
     */
  metric: string;
  /** Current value of the metric */
  current_value: number;
  /** Value at which the alert is triggered */
  trigger_value: number;
  /** Indicates if the monitor is muted */
  is_mute?: boolean;
  readonly created_at?: string;
  readonly updated_at?: string;
}

export type AIModelApiModelType = typeof AIModelApiModelType[keyof typeof AIModelApiModelType];


export const AIModelApiModelType = {
  Numeric: 'Numeric',
  ScoreCategorical: 'ScoreCategorical',
  Ranking: 'Ranking',
  BinaryClassification: 'BinaryClassification',
  Regression: 'Regression',
  ObjectDetection: 'ObjectDetection',
  Segmentation: 'Segmentation',
  GenerativeLLM: 'GenerativeLLM',
  GenerativeImage: 'GenerativeImage',
  GenerativeVideo: 'GenerativeVideo',
  TTS: 'TTS',
  STT: 'STT',
  MultiModal: 'MultiModal',
} as const;

export type AIModelApiBaselineModelEnvironment = typeof AIModelApiBaselineModelEnvironment[keyof typeof AIModelApiBaselineModelEnvironment];


export const AIModelApiBaselineModelEnvironment = {
  Production: 'Production',
  Training: 'Training',
  Validation: 'Validation',
  Corpus: 'Corpus',
} as const;

export interface AIModelApi {
  readonly id?: string;
  readonly monitors?: readonly MonitorApi[];
  readonly created_at?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  user_model_id: string;
  deleted?: boolean;
  model_type: AIModelApiModelType;
  baseline_model_environment?: AIModelApiBaselineModelEnvironment;
  /** @maxLength 255 */
  baseline_model_version?: string;
  default_metric?: string;
  organization: string;
  workspace?: string;
}

export interface AnnotationTaskApi {
  readonly id?: string;
  readonly assigned_users?: readonly UserApi[];
  readonly created_at?: string;
  readonly updated_at?: string;
  ai_model?: AIModelApi;
  /**
     * @minLength 1
     * @maxLength 255
     */
  task_name: string;
}

export type AnnotationsLabelsApiType = typeof AnnotationsLabelsApiType[keyof typeof AnnotationsLabelsApiType];


export const AnnotationsLabelsApiType = {
  text: 'text',
  numeric: 'numeric',
  categorical: 'categorical',
  star: 'star',
  thumbs_up_down: 'thumbs_up_down',
} as const;

export type AnnotationsLabelsApiSettings = { [key: string]: unknown };

export interface AnnotationsLabelsApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  type: AnnotationsLabelsApiType;
  readonly organization?: string;
  settings?: AnnotationsLabelsApiSettings;
  project?: string;
  description?: string;
  allow_notes?: boolean;
  readonly created_at?: string;
  readonly trace_annotations_count?: number;
  readonly annotation_count?: number;
  readonly archived?: boolean;
}

export interface AnnotationLabelCreateResponseApi {
  status?: boolean;
  result: AnnotationsLabelsApi;
}

export interface AnnotationLabelRestoreResponseApi {
  status?: boolean;
  result: AnnotationsLabelsApi;
}

export type AnnotationsApiStaticFields = { [key: string]: unknown };

export type AnnotationsApiResponseFields = { [key: string]: unknown };

export interface AnnotationsApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  readonly assigned_users?: string;
  readonly organization?: string;
  readonly labels?: string;
  columns?: string[];
  static_fields?: AnnotationsApiStaticFields;
  response_fields?: AnnotationsApiResponseFields;
  dataset?: string;
  readonly summary?: string;
  readonly created_at?: string;
  /**
     * @minimum -2147483648
     * @maximum 2147483647
     */
  responses?: number;
  readonly lowest_unfinished_row?: string;
  readonly label_requirements?: string;
}

export interface BulkDestroyAnnotationsRequestApi {
  annotation_ids: string[];
}

export interface BulkDestroyAnnotationsResultApi {
  /** @minLength 1 */
  message: string;
  deleted_count: number;
  errors?: string[];
}

export interface BulkDestroyAnnotationsResponseApi {
  status?: boolean;
  result: BulkDestroyAnnotationsResultApi;
}

export interface PreviewAnnotationsRequestApi {
  dataset_id: string;
  static_column?: string[];
  response_column?: string[];
}

export type PreviewAnnotationFieldApiValue = { [key: string]: unknown };

export interface PreviewAnnotationFieldApi {
  column_id: string;
  /** @minLength 1 */
  column_name: string;
  /** @minLength 1 */
  data_type: string;
  value: PreviewAnnotationFieldApiValue;
}

export interface PreviewAnnotationDataApi {
  static_fields: PreviewAnnotationFieldApi[];
  response_fields: PreviewAnnotationFieldApi[];
}

export interface PreviewAnnotationsResultApi {
  row_id: string;
  row_number: number;
  preview_data: PreviewAnnotationDataApi;
}

export interface PreviewAnnotationsResponseApi {
  status?: boolean;
  result: PreviewAnnotationsResultApi;
}

export interface ResetAnnotationsRequestApi {
  row_id: string;
}

export interface AnnotationActionMessageResultApi {
  /** @minLength 1 */
  message: string;
}

export interface AnnotationActionMessageResponseApi {
  status?: boolean;
  result: AnnotationActionMessageResultApi;
}

export type AnnotationLabelValueUpdateApiValue = { [key: string]: unknown };

export interface AnnotationLabelValueUpdateApi {
  row_id: string;
  label_id: string;
  value: AnnotationLabelValueUpdateApiValue;
  description?: string;
  column_id: string;
  time_taken?: number;
}

export type AnnotationResponseFieldUpdateApiValue = { [key: string]: unknown };

export interface AnnotationResponseFieldUpdateApi {
  row_id: string;
  column_id: string;
  value: AnnotationResponseFieldUpdateApiValue;
}

export interface UpdateAnnotationCellsRequestApi {
  label_values?: AnnotationLabelValueUpdateApi[];
  response_field_values?: AnnotationResponseFieldUpdateApi[];
}

export type ApiKeyResponseApiMaskedActualKey = { [key: string]: unknown };

export interface ApiKeyResponseApi {
  id?: string;
  /**
     * @minLength 1
     * @maxLength 50
     */
  provider: string;
  organization?: string;
  masked_actual_key?: ApiKeyResponseApiMaskedActualKey;
}

export interface ApiKeyListResponseApi {
  count: number;
  next?: string;
  previous?: string;
  results: ApiKeyResponseApi[];
}

export type ApiKeyRequestApiConfigJson = { [key: string]: unknown };

export interface ApiKeyRequestApi {
  /**
     * @minLength 1
     * @maxLength 50
     */
  provider: string;
  /** @maxLength 2500 */
  key?: string;
  config_json?: ApiKeyRequestApiConfigJson;
}

export interface ApiKeySuccessResponseApi {
  status: boolean;
  result: ApiKeyResponseApi;
}

export type ModelParameterSliderApiDefault = { [key: string]: unknown };

export interface ModelParameterSliderApi {
  /** @minLength 1 */
  label: string;
  min?: number;
  max?: number;
  step?: number;
  default?: ModelParameterSliderApiDefault;
  description?: string;
}

export type ModelParameterChoiceApiOptionsItem = { [key: string]: unknown };

export type ModelParameterChoiceApiDefault = { [key: string]: unknown };

export interface ModelParameterChoiceApi {
  /** @minLength 1 */
  label: string;
  options: ModelParameterChoiceApiOptionsItem[];
  default?: ModelParameterChoiceApiDefault;
  description?: string;
}

export interface ModelParameterBooleanApi {
  /** @minLength 1 */
  label: string;
  default?: boolean;
  description?: string;
}

export type ModelParameterTextInputApiDefault = { [key: string]: unknown };

export interface ModelParameterTextInputApi {
  /** @minLength 1 */
  label: string;
  default?: ModelParameterTextInputApiDefault;
  placeholder?: string;
  description?: string;
}

export interface ModelParameterResponseFormatApi {
  /** @minLength 1 */
  value: string;
}

export interface ModelParameterReasoningApi {
  dropdowns?: ModelParameterChoiceApi[];
  sliders?: ModelParameterSliderApi[];
}

export interface ModelParametersResultApi {
  sliders?: ModelParameterSliderApi[];
  dropdowns?: ModelParameterChoiceApi[];
  booleans?: ModelParameterBooleanApi[];
  boolean?: ModelParameterBooleanApi[];
  checkboxes?: ModelParameterBooleanApi[];
  text_inputs?: ModelParameterTextInputApi[];
  responseFormat?: ModelParameterResponseFormatApi[];
  reasoning?: ModelParameterReasoningApi;
}

export interface ModelParametersResponseApi {
  status: boolean;
  result: ModelParametersResultApi;
}

export interface LiteLLMVoiceOptionApi {
  /** @minLength 1 */
  id: string;
  /** @minLength 1 */
  name: string;
  /** @minLength 1 */
  type: string;
}

export interface LiteLLMModelVoicesResultApi {
  /** @minLength 1 */
  model_name: string;
  provider: string;
  custom_voice_supported: boolean;
  supported_voices: LiteLLMVoiceOptionApi[];
  supported_formats: string[];
  /** @minLength 1 */
  default_voice?: string;
  /** @minLength 1 */
  default_format?: string;
}

export interface LiteLLMModelVoicesResponseApi {
  status: boolean;
  result: LiteLLMModelVoicesResultApi;
}

export type ModelHubPaginatedResponseApiResultsItem = { [key: string]: unknown };

export interface ModelHubPaginatedResponseApi {
  count: number;
  next?: string;
  previous?: string;
  results: ModelHubPaginatedResponseApiResultsItem[];
}

export type CellErrorLocalizerResultApiErrorAnalysis = { [key: string]: unknown };

export type CellErrorLocalizerResultApiInputData = { [key: string]: unknown };

export type CellErrorLocalizerResultApiInputTypes = { [key: string]: unknown };

export interface CellErrorLocalizerResultApi {
  task_id?: string;
  cell_id: string;
  status?: string;
  error_analysis?: CellErrorLocalizerResultApiErrorAnalysis;
  selected_input_key?: string;
  input_data?: CellErrorLocalizerResultApiInputData;
  input_types?: CellErrorLocalizerResultApiInputTypes;
  error_message?: string;
}

export interface CellErrorLocalizerResponseApi {
  status: boolean;
  result: CellErrorLocalizerResultApi;
}

export interface ModelHubEmptyRequestApi { [key: string]: unknown }

export type ColumnConfigResultApiTemplateConfig = { [key: string]: unknown };

export type ColumnConfigResultApiConfig = { [key: string]: unknown };

export type ColumnConfigResultApiPromptConfig = { [key: string]: unknown };

export type ColumnConfigResultApiMessages = { [key: string]: unknown };

/**
 * String or JSON object.
 */
export type ColumnConfigResultApiResponseFormat = string | { [key: string]: unknown };

export type ColumnConfigResultApiOptimizedKPrompts = { [key: string]: unknown };

export type ColumnConfigResultApiModelConfig = { [key: string]: unknown };

export type ColumnConfigResultApiUserEvalTemplateIdsItem = { [key: string]: unknown };

export type ColumnConfigResultApiOptimisationConfig = { [key: string]: unknown };

export type ColumnConfigResultApiExperimentDatasetConfig = { [key: string]: unknown };

export interface ColumnConfigResultApi {
  /** @minLength 1 */
  name: string;
  template?: string;
  template_config?: ColumnConfigResultApiTemplateConfig;
  description?: string;
  config?: ColumnConfigResultApiConfig;
  status?: string;
  prompt_config?: ColumnConfigResultApiPromptConfig;
  model?: string;
  messages?: ColumnConfigResultApiMessages;
  output_format?: string;
  temperature?: number;
  frequency_penalty?: number;
  presence_penalty?: number;
  max_tokens?: number;
  top_p?: number;
  /** String or JSON object. */
  response_format?: ColumnConfigResultApiResponseFormat;
  tool_choice?: string;
  tools?: string[];
  optimize_type?: string;
  optimized_k_prompts?: ColumnConfigResultApiOptimizedKPrompts;
  model_config?: ColumnConfigResultApiModelConfig;
  user_eval_template_ids?: ColumnConfigResultApiUserEvalTemplateIdsItem[];
  optimisation_name?: string;
  optimisation_config?: ColumnConfigResultApiOptimisationConfig;
  experiment_dataset?: string;
  experiment_dataset_config?: ColumnConfigResultApiExperimentDatasetConfig;
}

export interface ColumnConfigResponseApi {
  status: boolean;
  result: ColumnConfigResultApi;
}

export type OperationConfigResultApiMetadata = { [key: string]: unknown };

export interface OperationConfigResultApi {
  column_id: string;
  metadata: OperationConfigResultApiMetadata;
}

export interface OperationConfigResponseApi {
  status: boolean;
  result: OperationConfigResultApi;
}

export type RerunOperationRequestApiConfig = { [key: string]: unknown };

export interface RerunOperationRequestApi {
  /** @minLength 1 */
  operation_type: string;
  config?: RerunOperationRequestApiConfig;
}

export interface RerunOperationResultApi {
  /** @minLength 1 */
  message: string;
  column_id: string;
  /** @minLength 1 */
  status: string;
}

export interface RerunOperationResponseApi {
  status: boolean;
  result: RerunOperationResultApi;
}

export type CustomEvalTemplateCreateApiTemplateType = typeof CustomEvalTemplateCreateApiTemplateType[keyof typeof CustomEvalTemplateCreateApiTemplateType];


export const CustomEvalTemplateCreateApiTemplateType = {
  Llm: 'Llm',
  Futureagi: 'Futureagi',
  Function: 'Function',
} as const;

export type CustomEvalTemplateCreateApiOutputType = typeof CustomEvalTemplateCreateApiOutputType[keyof typeof CustomEvalTemplateCreateApiOutputType];


export const CustomEvalTemplateCreateApiOutputType = {
  'Pass/Fail': 'Pass/Fail',
  score: 'score',
  choices: 'choices',
} as const;

export type CustomEvalTemplateCreateApiConfig = {[key: string]: string};

export type CustomEvalTemplateCreateApiChoices = {[key: string]: string};

export interface CustomEvalTemplateCreateApi {
  template_type?: CustomEvalTemplateCreateApiTemplateType;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  description?: string;
  tags?: string[];
  /** @maxLength 100000 */
  criteria?: string;
  output_type?: CustomEvalTemplateCreateApiOutputType;
  required_keys: string[];
  config?: CustomEvalTemplateCreateApiConfig;
  check_internet?: boolean;
  choices?: CustomEvalTemplateCreateApiChoices;
  multi_choice?: boolean;
  /** @maxLength 500 */
  template_id?: string;
}

export interface CustomEvalTemplateCreateResponseResultApi {
  eval_template_id: string;
}

export interface CustomEvalTemplateCreateResponseApi {
  status: boolean;
  result: CustomEvalTemplateCreateResponseResultApi;
}

export interface CustomMetricListItemApi {
  id: string;
  /** @minLength 1 */
  name: string;
  /** @minLength 1 */
  evaluation_type: string;
}

export interface CustomMetricListResponseApi {
  metrics: CustomMetricListItemApi[];
}

export type CustomMetricMutationRequestApiDatasets = { [key: string]: unknown };

export interface CustomMetricMutationRequestApi {
  id?: string;
  model_id?: string;
  name?: string;
  prompt?: string;
  metric_type?: string;
  evaluation_type?: string;
  datasets?: CustomMetricMutationRequestApiDatasets;
}

export interface ModelHubStatusResponseApi {
  /** @minLength 1 */
  status: string;
}

export interface MetricTagOptionApi {
  /** @minLength 1 */
  label: string;
  /** @minLength 1 */
  value: string;
}

export interface CustomMetricTestRequestApi {
  /** @minLength 1 */
  prompt: string;
}

export type CustomMetricTestResponseApiPrompts = { [key: string]: unknown };

export interface CustomMetricTestResponseApi {
  /** @minLength 1 */
  status: string;
  prompts?: CustomMetricTestResponseApiPrompts;
}

export interface CustomAIModelApi {
  readonly id?: string;
  readonly created_at?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  user_model_id: string;
  deleted?: boolean;
  /**
     * @minLength 1
     * @maxLength 50
     */
  provider: string;
  input_token_cost: number;
  output_token_cost: number;
  readonly config_json?: string;
  readonly user?: string;
  readonly updated_at?: string;
}

export interface CustomAIModelUpdateRequestApi {
  model_name?: string;
  input_token_cost?: number;
  output_token_cost?: number;
}

export type CustomAIModelCreateRequestApiConfigJson = { [key: string]: unknown };

export interface CustomAIModelCreateRequestApi {
  /** @minLength 1 */
  model_provider: string;
  /** @minLength 1 */
  model_name: string;
  input_token_cost?: number;
  output_token_cost?: number;
  config_json?: CustomAIModelCreateRequestApiConfigJson;
  key?: string;
}

export interface CustomAIModelCreateResponseDataApi {
  id: string;
}

export interface CustomAIModelCreateResponseApi {
  /** @minLength 1 */
  status: string;
  /** @minLength 1 */
  message: string;
  data: CustomAIModelCreateResponseDataApi;
}

export interface CustomAIModelDeleteRequestApi {
  ids?: string[];
}

export interface ModelHubStringResultResponseApi {
  status: boolean;
  /** @minLength 1 */
  result: string;
}

export type CustomAIModelEditResultApiKey = { [key: string]: unknown };

export type CustomAIModelEditResultApiConfigJson = { [key: string]: unknown };

export interface CustomAIModelEditResultApi {
  /** @minLength 1 */
  model_name: string;
  input_token_cost?: number;
  output_token_cost?: number;
  /** @minLength 1 */
  model_provider: string;
  key?: CustomAIModelEditResultApiKey;
  config_json?: CustomAIModelEditResultApiConfigJson;
}

export interface CustomAIModelEditResponseApi {
  status: boolean;
  result: CustomAIModelEditResultApi;
}

export type CustomAIModelEditRequestApiConfigJson = { [key: string]: unknown };

export interface CustomAIModelEditRequestApi {
  id: string;
  model_name?: string;
  input_token_cost?: number;
  output_token_cost?: number;
  config_json?: CustomAIModelEditRequestApiConfigJson;
  key?: string;
}

export interface CustomAIModelBaselineRequestApi {
  environment?: string;
  model_version?: string;
}

export interface ModelHubStatusMessageResponseApi {
  /** @minLength 1 */
  status: string;
  /** @minLength 1 */
  message: string;
}

export interface CustomAIModelDefaultMetricRequestApi {
  metric_id: string;
}

export type DatasetOptimizationListApiOptimizerAlgorithm = typeof DatasetOptimizationListApiOptimizerAlgorithm[keyof typeof DatasetOptimizationListApiOptimizerAlgorithm];


export const DatasetOptimizationListApiOptimizerAlgorithm = {
  random_search: 'random_search',
  bayesian: 'bayesian',
  metaprompt: 'metaprompt',
  protegi: 'protegi',
  promptwizard: 'promptwizard',
  gepa: 'gepa',
} as const;

export type DatasetOptimizationListApiStatus = typeof DatasetOptimizationListApiStatus[keyof typeof DatasetOptimizationListApiStatus];


export const DatasetOptimizationListApiStatus = {
  not_started: 'not_started',
  pending: 'pending',
  running: 'running',
  completed: 'completed',
  failed: 'failed',
  cancelled: 'cancelled',
} as const;

/**
 * Optimizer-specific configuration (num_trials, etc.)
 */
export type DatasetOptimizationListApiOptimizerConfig = { [key: string]: unknown };

export interface DatasetOptimizationListApi {
  readonly id?: string;
  /** @minLength 1 */
  optimization_name: string;
  started_at: string;
  readonly trial_count?: string;
  optimizer_algorithm?: DatasetOptimizationListApiOptimizerAlgorithm;
  readonly optimizer_model_id?: string;
  readonly column_id?: string;
  status?: DatasetOptimizationListApiStatus;
  error_message?: string;
  /** Optimizer-specific configuration (num_trials, etc.) */
  optimizer_config?: DatasetOptimizationListApiOptimizerConfig;
  best_score?: number;
  baseline_score?: number;
}

export type DatasetOptimizationCreateApiOptimizerAlgorithm = typeof DatasetOptimizationCreateApiOptimizerAlgorithm[keyof typeof DatasetOptimizationCreateApiOptimizerAlgorithm];


export const DatasetOptimizationCreateApiOptimizerAlgorithm = {
  random_search: 'random_search',
  bayesian: 'bayesian',
  metaprompt: 'metaprompt',
  protegi: 'protegi',
  promptwizard: 'promptwizard',
  gepa: 'gepa',
} as const;

/**
 * Optimizer-specific configuration (num_trials, etc.)
 */
export type DatasetOptimizationCreateApiOptimizerConfig = { [key: string]: unknown };

export interface DatasetOptimizationCreateApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  column_id: string;
  optimizer_algorithm: DatasetOptimizationCreateApiOptimizerAlgorithm;
  optimizer_model_id?: string;
  /** Optimizer-specific configuration (num_trials, etc.) */
  optimizer_config?: DatasetOptimizationCreateApiOptimizerConfig;
  user_eval_template_ids?: string[];
  readonly created_at?: string;
}

export type DatasetOptimizationDetailApiStatus = typeof DatasetOptimizationDetailApiStatus[keyof typeof DatasetOptimizationDetailApiStatus];


export const DatasetOptimizationDetailApiStatus = {
  not_started: 'not_started',
  pending: 'pending',
  running: 'running',
  completed: 'completed',
  failed: 'failed',
  cancelled: 'cancelled',
} as const;

export type DatasetOptimizationParameterItemApiValue = { [key: string]: unknown };

export interface DatasetOptimizationParameterItemApi {
  /** @minLength 1 */
  key: string;
  /** @minLength 1 */
  label: string;
  description: string;
  value: DatasetOptimizationParameterItemApiValue;
}

export interface DatasetOptimizationTrialEvalScoreApi {
  score?: number;
  percentage_change?: number;
}

export type DatasetOptimizationTrialTableRowApiEvalScores = {[key: string]: DatasetOptimizationTrialEvalScoreApi};

export interface DatasetOptimizationTrialTableRowApi {
  /** @minLength 1 */
  id: string;
  /** @minLength 1 */
  trial: string;
  prompt: string;
  is_best: boolean;
  score_percentage_change?: number;
  eval_scores: DatasetOptimizationTrialTableRowApiEvalScores;
}

export interface DatasetOptimizationColumnConfigItemApi {
  /** @minLength 1 */
  id: string;
  /** @minLength 1 */
  name: string;
  is_visible: boolean;
}

export interface DatasetOptimizationEvalTemplateItemApi {
  /** @minLength 1 */
  id: string;
  /** @minLength 1 */
  eval_id: string;
  /** @minLength 1 */
  name: string;
  /** @minLength 1 */
  template_id: string;
}

export type DatasetOptimizationDetailApiConfiguration = { [key: string]: unknown };

export interface DatasetOptimizationDetailApi {
  /** @minLength 1 */
  readonly optimiser_name?: string;
  /** @minLength 1 */
  readonly optimiser_type?: string;
  /** @minLength 1 */
  readonly model?: string;
  /** @minLength 1 */
  readonly provider_logo?: string;
  readonly configuration?: DatasetOptimizationDetailApiConfiguration;
  status?: DatasetOptimizationDetailApiStatus;
  error_message?: string;
  readonly start_time?: string;
  readonly parameters?: readonly DatasetOptimizationParameterItemApi[];
  /** @minLength 1 */
  readonly column_id?: string;
  /** @minLength 1 */
  readonly column_name?: string;
  best_score?: number;
  baseline_score?: number;
  readonly table?: readonly DatasetOptimizationTrialTableRowApi[];
  readonly column_config?: readonly DatasetOptimizationColumnConfigItemApi[];
  /** @minLength 1 */
  readonly optimizer_model_id?: string;
  readonly user_eval_templates?: readonly DatasetOptimizationEvalTemplateItemApi[];
}

export interface DatasetOptimizationDetailApiResponseApi {
  status: boolean;
  result: DatasetOptimizationDetailApi;
}

export type DatasetOptimizationApiOptimizerAlgorithm = typeof DatasetOptimizationApiOptimizerAlgorithm[keyof typeof DatasetOptimizationApiOptimizerAlgorithm];


export const DatasetOptimizationApiOptimizerAlgorithm = {
  random_search: 'random_search',
  bayesian: 'bayesian',
  metaprompt: 'metaprompt',
  protegi: 'protegi',
  promptwizard: 'promptwizard',
  gepa: 'gepa',
} as const;

/**
 * Optimizer-specific configuration (num_trials, etc.)
 */
export type DatasetOptimizationApiOptimizerConfig = { [key: string]: unknown };

export type DatasetOptimizationApiStatus = typeof DatasetOptimizationApiStatus[keyof typeof DatasetOptimizationApiStatus];


export const DatasetOptimizationApiStatus = {
  not_started: 'not_started',
  pending: 'pending',
  running: 'running',
  completed: 'completed',
  failed: 'failed',
  cancelled: 'cancelled',
} as const;

export interface DatasetOptimizationApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  /** Column being optimized */
  column?: string;
  optimizer_algorithm?: DatasetOptimizationApiOptimizerAlgorithm;
  /** Model used for optimization (separate from eval model) */
  optimizer_model?: string;
  /** Optimizer-specific configuration (num_trials, etc.) */
  optimizer_config?: DatasetOptimizationApiOptimizerConfig;
  status?: DatasetOptimizationApiStatus;
  error_message?: string;
  best_score?: number;
  baseline_score?: number;
  optimized_k_prompts?: string[];
  readonly created_at?: string;
}

export interface DatasetColumnDetailItemApi {
  id: string;
  /** @minLength 1 */
  name: string;
  data_type?: string;
}

export interface DatasetColumnDetailResultApi {
  columns: DatasetColumnDetailItemApi[];
}

export interface DatasetColumnDetailResponseApi {
  status: boolean;
  result: DatasetColumnDetailResultApi;
}

export interface AnnotationSummaryHeaderApi {
  dataset_coverage?: number;
  completion_eta?: number;
  overall_agreement?: number;
}

export type AnnotationSummaryResultApiLabelsItem = { [key: string]: unknown };

export type AnnotationSummaryResultApiAnnotatorsItem = { [key: string]: unknown };

export interface AnnotationSummaryResultApi {
  labels?: AnnotationSummaryResultApiLabelsItem[];
  annotators?: AnnotationSummaryResultApiAnnotatorsItem[];
  header?: AnnotationSummaryHeaderApi;
}

export interface AnnotationSummaryResponseApi {
  status?: boolean;
  result: AnnotationSummaryResultApi;
}

export type DatasetEvalStatsMetricApiOutput = { [key: string]: unknown };

export interface DatasetEvalStatsMetricApi {
  id?: string;
  /** @minLength 1 */
  name: string;
  total_cells?: number;
  output: DatasetEvalStatsMetricApiOutput;
}

export type DatasetEvalStatsItemApiTotalAvg = { [key: string]: unknown };

export type DatasetEvalStatsItemApiTotalChoicesAvg = { [key: string]: unknown };

export interface DatasetEvalStatsItemApi {
  id: string;
  /** @minLength 1 */
  name: string;
  /** @minLength 1 */
  output_type: string;
  result: DatasetEvalStatsMetricApi[];
  total_pass_rate?: number;
  total_avg?: DatasetEvalStatsItemApiTotalAvg;
  total_choices_avg?: DatasetEvalStatsItemApiTotalChoicesAvg;
  is_numeric_eval?: boolean;
  is_numeric_eval_percentage?: boolean;
}

export interface DatasetEvalStatsResponseApi {
  status: boolean;
  result: DatasetEvalStatsItemApi[];
}

export type JsonColumnSchemaEntryApiSample = { [key: string]: unknown };

export interface JsonColumnSchemaEntryApi {
  /** @minLength 1 */
  name: string;
  keys?: string[];
  sample?: JsonColumnSchemaEntryApiSample;
  max_array_count?: number;
  max_images_count?: number;
}

export type DatasetJsonSchemaResponseApiResult = {[key: string]: JsonColumnSchemaEntryApi};

export interface DatasetJsonSchemaResponseApi {
  status: boolean;
  result: DatasetJsonSchemaResponseApiResult;
}

export interface DatasetRunPromptStatsPromptApi {
  id: string;
  /** @minLength 1 */
  name: string;
  input_token: number;
  output_token: number;
  total_token: number;
}

export interface DatasetRunPromptStatsResultApi {
  avg_tokens: number;
  avg_cost: number;
  avg_time: number;
  prompts: DatasetRunPromptStatsPromptApi[];
}

export interface DatasetRunPromptStatsResponseApi {
  status: boolean;
  result: DatasetRunPromptStatsResultApi;
}

export type CompareEvalsListRequestApiEvalType = typeof CompareEvalsListRequestApiEvalType[keyof typeof CompareEvalsListRequestApiEvalType];


export const CompareEvalsListRequestApiEvalType = {
  user: 'user',
} as const;

export interface CompareEvalsListRequestApi {
  search_text?: string;
  eval_type: CompareEvalsListRequestApiEvalType;
  dataset_ids: string[];
}

export type CompareEvalListResultApiEvalsItem = { [key: string]: unknown };

export interface CompareEvalListResultApi {
  evals: CompareEvalListResultApiEvalsItem[];
}

export interface CompareEvalListResponseApi {
  status: boolean;
  result: CompareEvalListResultApi;
}

export type ComparePreviewRunEvalRequestApiConfig = { [key: string]: unknown };

export type ComparePreviewRunEvalRequestApiDatasetInfo = { [key: string]: unknown };

export interface ComparePreviewRunEvalRequestApi {
  config: ComparePreviewRunEvalRequestApiConfig;
  model?: string;
  template_id: string;
  dataset_ids: string[];
  dataset_info?: ComparePreviewRunEvalRequestApiDatasetInfo;
  source?: string;
}

export type EvalPreviewResultApiResponsesItem = { [key: string]: unknown };

export interface EvalPreviewResultApi {
  responses: EvalPreviewResultApiResponsesItem[];
}

export interface EvalPreviewResponseApi {
  status: boolean;
  result: EvalPreviewResultApi;
}

export type CompareDatasetRowResultApiTableItem = { [key: string]: unknown };

export interface CompareDatasetRowResultApi {
  prev_row_id?: string;
  next_row_id?: string;
  table: CompareDatasetRowResultApiTableItem[];
}

export interface CompareDatasetRowResponseApi {
  status: boolean;
  result: CompareDatasetRowResultApi;
}

export interface CompareDatasetDeleteResultApi {
  /** @minLength 1 */
  message: string;
}

export interface CompareDatasetDeleteResponseApi {
  status: boolean;
  result: CompareDatasetDeleteResultApi;
}

export type DatasetExplanationSummaryResponseResultApiResponse = { [key: string]: unknown };

export interface DatasetExplanationSummaryResponseResultApi {
  response: DatasetExplanationSummaryResponseResultApiResponse;
  last_updated: string;
  /** @minLength 1 */
  status: string;
  row_count: number;
  min_rows_required: number;
}

export interface DatasetExplanationSummaryResponseApi {
  status: boolean;
  result: DatasetExplanationSummaryResponseResultApi;
}

export interface BaseColumnsResponseResultApi {
  base_columns: string[];
}

export interface BaseColumnsResponseApi {
  status: boolean;
  result: BaseColumnsResponseResultApi;
}

export interface HuggingFaceDatasetDetailRequestApi {
  /** @minLength 1 */
  dataset_id: string;
}

export interface HuggingFaceDatasetDetailApi {
  /** @minLength 1 */
  id: string;
  /** @minLength 1 */
  name: string;
  description: string;
  downloads: number;
  likes: number;
  tags: string[];
  /** @minLength 1 */
  author?: string;
}

export interface HuggingFaceDatasetDetailResponseResultApi {
  /** @minLength 1 */
  message: string;
  dataset: HuggingFaceDatasetDetailApi;
}

export interface HuggingFaceDatasetDetailResponseApi {
  status: boolean;
  result: HuggingFaceDatasetDetailResponseResultApi;
}

export type HuggingFaceDatasetListRequestApiFilterParams = { [key: string]: unknown };

export interface HuggingFaceDatasetListRequestApi {
  search_query?: string;
  filter_params?: HuggingFaceDatasetListRequestApiFilterParams;
}

export interface HuggingFaceDatasetListItemApi {
  /** @minLength 1 */
  id: string;
  /** @minLength 1 */
  name: string;
  downloads: number;
  likes: number;
  /** @minLength 1 */
  author?: string;
}

export interface HuggingFaceDatasetListResponseResultApi {
  /** @minLength 1 */
  message: string;
  total_datasets: number;
  datasets: HuggingFaceDatasetListItemApi[];
}

export interface HuggingFaceDatasetListResponseApi {
  status: boolean;
  result: HuggingFaceDatasetListResponseResultApi;
}

export type AddApiColumnRequestApiConfig = { [key: string]: unknown };

export interface AddApiColumnRequestApi {
  /** @minLength 1 */
  column_name: string;
  config: AddApiColumnRequestApiConfig;
  concurrency?: number;
}

export interface DynamicColumnCreateResultApi {
  /** @minLength 1 */
  message: string;
  new_column_id: string;
  /** @minLength 1 */
  new_column_name: string;
}

export interface DynamicColumnCreateResponseApi {
  status: boolean;
  result: DynamicColumnCreateResultApi;
}

export type VectorDBColumnRequestApiEmbeddingConfig = { [key: string]: unknown };

export interface VectorDBColumnRequestApi {
  column_id: string;
  new_column_name?: string;
  /** @minLength 1 */
  sub_type: string;
  /** @minLength 1 */
  api_key: string;
  collection_name?: string;
  url?: string;
  search_type?: string;
  key?: string;
  limit?: number;
  index_name?: string;
  top_k?: number;
  namespace?: string;
  embedding_config?: VectorDBColumnRequestApiEmbeddingConfig;
  concurrency?: number;
  query_key?: string;
  vector_length?: number;
}

export interface ClassifyColumnRequestApi {
  column_id: string;
  labels: string[];
  /** @minLength 1 */
  language_model_id?: string;
  concurrency?: number;
  new_column_name?: string;
}

export type CompareDatasetApiDatasetInfo = { [key: string]: unknown };

export interface CompareDatasetApi {
  compare_id?: string;
  page_size?: number;
  current_page_index?: number;
  /** @minLength 1 */
  base_column_name: string;
  dataset_info?: CompareDatasetApiDatasetInfo;
  common_column_names?: string[];
  dataset_ids: string[];
}

export interface CompareDatasetMetadataApi {
  compare_id: string;
  total_rows: number;
  total_pages: number;
}

export type CompareDatasetResultApiColumnConfigItem = { [key: string]: unknown };

export type CompareDatasetResultApiTableItem = { [key: string]: unknown };

export interface CompareDatasetResultApi {
  metadata?: CompareDatasetMetadataApi;
  column_config?: CompareDatasetResultApiColumnConfigItem[];
  table?: CompareDatasetResultApiTableItem[];
}

export interface CompareDatasetResponseApi {
  status: boolean;
  result: CompareDatasetResultApi;
}

export type CompareExperimentEvalRequestApiConfig = { [key: string]: unknown };

export type CompareExperimentEvalRequestApiCompositeWeightOverrides = { [key: string]: unknown };

export interface CompareExperimentEvalRequestApi {
  /**
     * @minLength 1
     * @maxLength 50
     */
  name: string;
  /**
     * @minLength 1
     * @maxLength 500
     */
  template_id: string;
  config: CompareExperimentEvalRequestApiConfig;
  kb_id?: string;
  error_localizer?: boolean;
  /** @maxLength 100 */
  model?: string;
  eval_type?: string;
  run?: boolean;
  save_as_template?: boolean;
  experiment_id?: string;
  composite_weight_overrides?: CompareExperimentEvalRequestApiCompositeWeightOverrides;
  dataset_ids?: string[];
}

export interface DevelopDatasetMessageResponseApi {
  status: boolean;
  /** @minLength 1 */
  result: string;
}

export interface CompareStartEvalsRequestApi {
  user_eval_names: string[];
  dataset_ids?: string[];
}

export type CompareDatasetStatsRequestApiStatType = typeof CompareDatasetStatsRequestApiStatType[keyof typeof CompareDatasetStatsRequestApiStatType];


export const CompareDatasetStatsRequestApiStatType = {
  evaluation: 'evaluation',
  run_prompt: 'run_prompt',
} as const;

export interface CompareDatasetStatsRequestApi {
  /** @minLength 1 */
  base_column_name: string;
  dataset_ids: string[];
  stat_type?: CompareDatasetStatsRequestApiStatType;
}

export type CompareDatasetStatsResponseApiResultItem = { [key: string]: unknown };

export type CompareDatasetStatsResponseApiResult = {[key: string]: CompareDatasetStatsResponseApiResultItem[]};

export interface CompareDatasetStatsResponseApi {
  status: boolean;
  result: CompareDatasetStatsResponseApiResult;
}

export type ConditionalColumnRequestApiConfigItem = { [key: string]: unknown };

export interface ConditionalColumnRequestApi {
  config: ConditionalColumnRequestApiConfigItem[];
  /** @minLength 1 */
  new_column_name: string;
  concurrency?: number;
}

export type DerivedVariableDetailApiSchema = { [key: string]: unknown };

export type DerivedVariableDetailApiRawSample = { [key: string]: unknown };

export interface DerivedVariableDetailApi {
  paths?: string[];
  schema?: DerivedVariableDetailApiSchema;
  full_variables?: string[];
  raw_sample?: DerivedVariableDetailApiRawSample;
  is_json?: boolean;
}

export type DatasetDerivedVariablesResultApiDerivedVariables = {[key: string]: DerivedVariableDetailApi};

export interface DatasetDerivedVariablesResultApi {
  derived_variables: DatasetDerivedVariablesResultApiDerivedVariables;
}

export interface DatasetDerivedVariablesResponseApi {
  status: boolean;
  result: DatasetDerivedVariablesResultApi;
}

export interface DuplicateRowsRequestApi {
  row_ids?: string[];
  selected_all_rows?: boolean;
  /** @minimum 1 */
  num_copies?: number;
}

export interface DuplicateRowsResultApi {
  /** @minLength 1 */
  message: string;
  source_rows: number;
  copies_per_row: number;
  total_new_rows: number;
  new_row_ids: string[];
}

export interface DuplicateRowsResponseApi {
  status: boolean;
  result: DuplicateRowsResultApi;
}

export interface DuplicateDatasetRequestApi {
  row_ids?: string[];
  selected_all_rows?: boolean;
  /** @minLength 1 */
  name: string;
}

export interface DuplicateDatasetResultApi {
  /** @minLength 1 */
  message: string;
  new_dataset_id: string;
  /** @minLength 1 */
  new_dataset_name: string;
  columns_copied: number;
  rows_copied: number;
}

export interface DuplicateDatasetResponseApi {
  status: boolean;
  result: DuplicateDatasetResultApi;
}

export interface ExtractEntitiesRequestApi {
  column_id: string;
  /** @minLength 1 */
  instruction: string;
  /** @minLength 1 */
  language_model_id?: string;
  concurrency?: number;
  new_column_name?: string;
}

export interface DynamicColumnMessageResultApi {
  /** @minLength 1 */
  message: string;
}

export interface DynamicColumnMessageResponseApi {
  status: boolean;
  result: DynamicColumnMessageResultApi;
}

export interface MergeDatasetRequestApi {
  row_ids?: string[];
  selected_all_rows?: boolean;
  target_dataset_id: string;
}

export interface MergeDatasetResultApi {
  /** @minLength 1 */
  message: string;
  rows_added: number;
  new_columns_created: number;
  columns_mapped: number;
}

export interface MergeDatasetResponseApi {
  status: boolean;
  result: MergeDatasetResultApi;
}

export type PreviewDatasetOperationRequestApiConfig = { [key: string]: unknown };

export interface PreviewDatasetOperationRequestApi {
  column_id?: string;
  json_key?: string;
  labels?: string[];
  instruction?: string;
  language_model_id?: string;
  config?: PreviewDatasetOperationRequestApiConfig;
  code?: string;
}

export type PreviewDatasetOperationResultItemApiInput = { [key: string]: unknown };

export type PreviewDatasetOperationResultItemApiOutput = { [key: string]: unknown };

export type PreviewDatasetOperationResultItemApiDetails = { [key: string]: unknown };

export interface PreviewDatasetOperationResultItemApi {
  row_id: string;
  input?: PreviewDatasetOperationResultItemApiInput;
  output?: PreviewDatasetOperationResultItemApiOutput;
  details?: PreviewDatasetOperationResultItemApiDetails;
}

export interface PreviewDatasetOperationResultApi {
  /** @minLength 1 */
  message: string;
  preview_results: PreviewDatasetOperationResultItemApi[];
  sample_size: number;
}

export interface PreviewDatasetOperationResponseApi {
  status: boolean;
  result: PreviewDatasetOperationResultApi;
}

export interface DeleteEvalTemplateApi {
  eval_template_id: string;
}

export type AddAsNewDatasetRequestApiColumns = { [key: string]: unknown };

export interface AddAsNewDatasetRequestApi {
  dataset_id: string;
  name?: string;
  columns?: AddAsNewDatasetRequestApiColumns;
}

export interface DatasetCopyResultApi {
  /** @minLength 1 */
  message: string;
  dataset_id: string;
  /** @minLength 1 */
  dataset_name: string;
}

export interface DatasetCopyResponseApi {
  status: boolean;
  result: DatasetCopyResultApi;
}

export interface AddRowsFromFileRequestApi {
  readonly file?: string;
  dataset_id: string;
  model_type?: string;
}

export interface DatasetSdkRowsRequestApi {
  dataset_name?: string;
  dataset_id?: string;
}

export type DatasetApiModelType = typeof DatasetApiModelType[keyof typeof DatasetApiModelType];


export const DatasetApiModelType = {
  Numeric: 'Numeric',
  ScoreCategorical: 'ScoreCategorical',
  Ranking: 'Ranking',
  BinaryClassification: 'BinaryClassification',
  Regression: 'Regression',
  ObjectDetection: 'ObjectDetection',
  Segmentation: 'Segmentation',
  GenerativeLLM: 'GenerativeLLM',
  GenerativeImage: 'GenerativeImage',
  GenerativeVideo: 'GenerativeVideo',
  TTS: 'TTS',
  STT: 'STT',
  MultiModal: 'MultiModal',
} as const;

export type DatasetApiSource = typeof DatasetApiSource[keyof typeof DatasetApiSource];


export const DatasetApiSource = {
  demo: 'demo',
  build: 'build',
  sdk: 'sdk',
  observe: 'observe',
  knowledge_base: 'knowledge_base',
  scenario: 'scenario',
  experiment_snapshot: 'experiment_snapshot',
  graph: 'graph',
} as const;

export interface DatasetApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 2000
     */
  name: string;
  organization: string;
  model_type?: DatasetApiModelType;
  source?: DatasetApiSource;
  user?: string;
}

export interface DatasetSdkRowsCodeApi {
  /** @minLength 1 */
  python_add_row: string;
  /** @minLength 1 */
  python_add_col: string;
  /** @minLength 1 */
  typescript_add_col: string;
  /** @minLength 1 */
  typescript_add_row: string;
  /** @minLength 1 */
  curl_add_col: string;
  /** @minLength 1 */
  curl_add_row: string;
}

export type DatasetSdkRowsResultApiApiKeys = { [key: string]: unknown };

export interface DatasetSdkRowsResultApi {
  api_keys: DatasetSdkRowsResultApiApiKeys;
  dataset: DatasetApi;
  code: DatasetSdkRowsCodeApi;
}

export interface DatasetSdkRowsResponseApi {
  status: boolean;
  result: DatasetSdkRowsResultApi;
}

/**
 * Tool selection mode: 'auto' or 'required'.
 */
export type PromptConfigApiToolChoice = typeof PromptConfigApiToolChoice[keyof typeof PromptConfigApiToolChoice];


export const PromptConfigApiToolChoice = {
  auto: 'auto',
  required: 'required',
} as const;

/**
 * Output format type.
 */
export type PromptConfigApiOutputFormat = typeof PromptConfigApiOutputFormat[keyof typeof PromptConfigApiOutputFormat];


export const PromptConfigApiOutputFormat = {
  array: 'array',
  string: 'string',
  number: 'number',
  object: 'object',
  audio: 'audio',
  image: 'image',
} as const;

export type PromptConfigApiRunPromptConfig = {[key: string]: { [key: string]: unknown }};

export type PromptConfigApiMessagesItem = {[key: string]: { [key: string]: unknown }};

/**
 * String or JSON object.
 */
export type PromptConfigApiResponseFormat = string | { [key: string]: unknown };

export type PromptConfigApiToolsItem = {[key: string]: { [key: string]: unknown }};

export interface PromptConfigApi {
  /** @maxLength 255 */
  model?: string;
  run_prompt_config?: PromptConfigApiRunPromptConfig;
  /** List of messages with format [{'role': 'user/assistant', 'content': 'text'}] */
  messages?: PromptConfigApiMessagesItem[];
  /**
     * Controls the randomness. Value between 0 and 2.
     * @minimum 0
     * @maximum 2
     */
  temperature?: number;
  /**
     * Penalty for word repetition. Value between -2 and 2.
     * @minimum -2
     * @maximum 2
     */
  frequency_penalty?: number;
  /**
     * Penalty for new word usage. Value between -2 and 2.
     * @minimum -2
     * @maximum 2
     */
  presence_penalty?: number;
  /**
     * Maximum number of tokens to generate. Null = use provider default.
     * @minimum 1
     * @maximum 65536
     */
  max_tokens?: number;
  /**
     * Controls diversity via nucleus sampling. Value between 0 and 1.
     * @minimum 0
     * @maximum 1
     */
  top_p?: number;
  /** String or JSON object. */
  response_format?: PromptConfigApiResponseFormat;
  /** Tool selection mode: 'auto' or 'required'. */
  tool_choice?: PromptConfigApiToolChoice;
  /** List of tools with tool properties if available. */
  tools?: PromptConfigApiToolsItem[];
  /** Output format type. */
  output_format?: PromptConfigApiOutputFormat;
  /**
     * Number of concurrent operations allowed. Maximum 10.
     * @minimum 1
     * @maximum 10
     */
  concurrency?: number;
}

export interface AddRunPromptApi {
  dataset_id: string;
  /** @minLength 1 */
  name: string;
  config?: PromptConfigApi;
}

export interface CloneDatasetRequestApi {
  new_dataset_name?: string;
}

export interface HuggingFaceDatasetCreateRequestApi {
  name?: string;
  model_type?: string;
  /** @minimum 0 */
  num_rows?: number;
  /** @minLength 1 */
  huggingface_dataset_name: string;
  huggingface_dataset_config?: string;
  /** @minLength 1 */
  huggingface_dataset_split: string;
}

export interface DatasetCreateStartedResultApi {
  /** @minLength 1 */
  message: string;
  dataset_id: string;
  /** @minLength 1 */
  dataset_name: string;
  dataset_model_type?: string;
}

export interface DatasetCreateStartedResponseApi {
  status: boolean;
  result: DatasetCreateStartedResultApi;
}

export interface CreateDatasetFromLocalFileRequestApi {
  readonly file?: string;
  new_dataset_name?: string;
  model_type?: string;
  source?: string;
}

export interface LocalFileDatasetCreateStartedResultApi {
  /** @minLength 1 */
  message: string;
  dataset_id: string;
  /** @minLength 1 */
  dataset_name: string;
  dataset_model_type?: string;
  /** @minLength 1 */
  processing_status: string;
  estimated_rows: number;
  estimated_columns: number;
}

export interface LocalFileDatasetCreateStartedResponseApi {
  status: boolean;
  result: LocalFileDatasetCreateStartedResultApi;
}

export interface ManualDatasetCreateRequestApi {
  /** @minLength 1 */
  dataset_name: string;
  /** @minimum 1 */
  number_of_rows?: number;
  /** @minimum 1 */
  number_of_columns?: number;
}

export interface ManualDatasetCreateResultApi {
  /** @minLength 1 */
  message: string;
  dataset_id: string;
  rows_created: number;
  columns_created: number;
}

export interface ManualDatasetCreateResponseApi {
  status: boolean;
  result: ManualDatasetCreateResultApi;
}

export interface CreateEmptyDatasetRequestApi {
  /** @minLength 1 */
  new_dataset_name: string;
  model_type?: string;
  is_sdk?: boolean;
  /**
     * @minimum 0
     * @maximum 10
     */
  row?: number;
}

export type SyntheticDatasetColumnApiProperty = { [key: string]: unknown };

export interface SyntheticDatasetColumnApi {
  /** @minLength 1 */
  name: string;
  /** @minLength 1 */
  data_type: string;
  description: string;
  property: SyntheticDatasetColumnApiProperty;
  skip?: boolean;
  is_new?: boolean;
}

export interface SyntheticDatasetPayloadApi {
  name?: string;
  description: string;
  objective: string;
  patterns: string;
}

export interface SyntheticDatasetCreationApi {
  num_rows: number;
  columns: SyntheticDatasetColumnApi[];
  dataset: SyntheticDatasetPayloadApi;
  kb_id?: string;
}

export interface SyntheticDatasetCreateStartedResultApi {
  /** @minLength 1 */
  message: string;
  data: DatasetApi;
}

export interface SyntheticDatasetCreateStartedResponseApi {
  status: boolean;
  result: SyntheticDatasetCreateStartedResultApi;
}

export interface DatasetCreationProgressResultApi {
  dataset_id: string;
  /** @minLength 1 */
  dataset_name: string;
  /** @minLength 1 */
  processing_status: string;
  is_processing: boolean;
  is_completed: boolean;
  is_failed: boolean;
  original_filename?: string;
  estimated_rows?: number;
  estimated_columns?: number;
  queued_at?: string;
  started_at?: string;
  completed_at?: string;
  failed_at?: string;
  error_message?: string;
}

export interface DatasetCreationProgressResponseApi {
  status: boolean;
  result: DatasetCreationProgressResultApi;
}

export interface EditRunPromptColumnApi {
  dataset_id: string;
  column_id: string;
  /** @minLength 1 */
  name?: string;
  config?: PromptConfigApi;
}

export interface DatasetCellDataRequestApi {
  row_ids: string[];
  column_ids: string[];
}

export type DatasetCellInnerMetadataApiErrorAnalysis = { [key: string]: unknown };

export interface DatasetCellInnerMetadataApi {
  explanation?: string;
  error_analysis?: DatasetCellInnerMetadataApiErrorAnalysis;
  selected_input_key?: string;
}

export type DatasetCellMetadataApiCost = { [key: string]: unknown };

export interface DatasetCellMetadataApi {
  response_time_ms?: number;
  token_count?: number;
  cost?: DatasetCellMetadataApiCost;
  cell_metadata?: DatasetCellInnerMetadataApi;
  reason?: string;
}

export type DatasetCellValueApiCellValue = { [key: string]: unknown };

export type DatasetCellValueApiCellDiffValue = { [key: string]: unknown };

export type DatasetCellValueApiValueInfos = { [key: string]: unknown };

export type DatasetCellValueApiFeedbackInfo = { [key: string]: unknown };

export interface DatasetCellValueApi {
  cell_value?: DatasetCellValueApiCellValue;
  cell_diff_value?: DatasetCellValueApiCellDiffValue;
  status?: string;
  value_infos?: DatasetCellValueApiValueInfos;
  feedback_info?: DatasetCellValueApiFeedbackInfo;
  metadata?: DatasetCellMetadataApi;
}

export type DatasetCellDataResponseApiResult = {[key: string]: {[key: string]: DatasetCellValueApi}};

export interface DatasetCellDataResponseApi {
  status: boolean;
  result: DatasetCellDataResponseApiResult;
}

export interface DatasetNameItemApi {
  dataset_id: string;
  /** @minLength 1 */
  name: string;
  model_type?: string;
}

export interface DatasetNamesResultApi {
  datasets: DatasetNameItemApi[];
}

export interface DatasetNamesResponseApi {
  status: boolean;
  result: DatasetNamesResultApi;
}

export interface DatasetListItemApi {
  id: string;
  /** @minLength 1 */
  name: string;
  number_of_datapoints: number;
  number_of_experiments: number;
  number_of_optimisations: number;
  derived_datasets: number;
  /** @minLength 1 */
  created_at: string;
  /** @minLength 1 */
  dataset_type: string;
}

export interface DatasetListResultApi {
  datasets: DatasetListItemApi[];
  total_pages: number;
  total_count: number;
}

export interface DatasetListResponseApi {
  status: boolean;
  result: DatasetListResultApi;
}

export interface HuggingFaceDatasetConfigRequestApi {
  /** @minLength 1 */
  dataset_path: string;
}

export type HuggingFaceDatasetConfigResultApiDatasetInfo = { [key: string]: unknown };

export interface HuggingFaceDatasetConfigResultApi {
  /** @minLength 1 */
  message: string;
  dataset_info: HuggingFaceDatasetConfigResultApiDatasetInfo;
}

export interface HuggingFaceDatasetConfigResponseApi {
  status: boolean;
  result: HuggingFaceDatasetConfigResultApi;
}

export interface DatasetRowDiffRequestApi {
  experiment_id: string;
  column_ids: string[];
  row_ids: string[];
  compare_column_ids: string[];
}

export type ExperimentRowCellInnerMetadataApiErrorAnalysis = { [key: string]: unknown };

export interface ExperimentRowCellInnerMetadataApi {
  explanation?: string;
  error_analysis?: ExperimentRowCellInnerMetadataApiErrorAnalysis;
  selected_input_key?: string;
}

export type ExperimentRowCellMetadataApiCost = { [key: string]: unknown };

export interface ExperimentRowCellMetadataApi {
  response_time_ms?: number;
  token_count?: number;
  cost?: ExperimentRowCellMetadataApiCost;
  cell_metadata?: ExperimentRowCellInnerMetadataApi;
  reason?: string;
}

export type ExperimentRowCellApiCellValue = { [key: string]: unknown };

export type ExperimentRowCellApiCellDiffValue = { [key: string]: unknown };

export type ExperimentRowCellApiValueInfos = { [key: string]: unknown };

export interface ExperimentRowCellApi {
  cell_value?: ExperimentRowCellApiCellValue;
  cell_diff_value?: ExperimentRowCellApiCellDiffValue;
  status?: string;
  metadata?: ExperimentRowCellMetadataApi;
  value_infos?: ExperimentRowCellApiValueInfos;
}

export type ExperimentRowDiffResponseApiResult = {[key: string]: {[key: string]: ExperimentRowCellApi}};

export interface ExperimentRowDiffResponseApi {
  status: boolean;
  result: ExperimentRowDiffResponseApiResult;
}

export type EvalFunctionListResultApiFunctionsItem = { [key: string]: unknown };

export interface EvalFunctionListResultApi {
  functions: EvalFunctionListResultApiFunctionsItem[];
}

export interface EvalFunctionListResponseApi {
  status: boolean;
  result: EvalFunctionListResultApi;
}

export interface PreviewRunPromptApi {
  dataset_id: string;
  /** @minLength 1 */
  name: string;
  config?: PromptConfigApi;
  /** @minimum 1 */
  first_n_rows?: number;
  /** List of row indices to preview. Must contain at least one integer. */
  row_indices?: number[];
}

export type RunPromptColumnPreviewResultApiResponsesItem = { [key: string]: unknown };

export type RunPromptColumnPreviewResultApiTokenUsage = { [key: string]: unknown };

export type RunPromptColumnPreviewResultApiCost = { [key: string]: unknown };

export interface RunPromptColumnPreviewResultApi {
  responses: RunPromptColumnPreviewResultApiResponsesItem[];
  token_usage: RunPromptColumnPreviewResultApiTokenUsage;
  cost: RunPromptColumnPreviewResultApiCost;
}

export interface RunPromptColumnPreviewResponseApi {
  status: boolean;
  result: RunPromptColumnPreviewResultApi;
}

export interface ProviderStatusItemApi {
  /** @minLength 1 */
  provider: string;
  /** @minLength 1 */
  display_name: string;
  has_key: boolean;
  masked_key?: string;
  logo_url?: string;
  /** @minLength 1 */
  type: string;
  id?: string;
}

export interface ProviderStatusResultApi {
  providers: ProviderStatusItemApi[];
}

export interface ProviderStatusResponseApi {
  status: boolean;
  result: ProviderStatusResultApi;
}

export type RunPromptColumnConfigResultApiConfig = { [key: string]: unknown };

export interface RunPromptColumnConfigResultApi {
  config: RunPromptColumnConfigResultApiConfig;
}

export interface RunPromptColumnConfigResponseApi {
  status: boolean;
  result: RunPromptColumnConfigResultApi;
}

export type RunPromptToolOptionApiConfig = { [key: string]: unknown };

export interface RunPromptToolOptionApi {
  /** @minLength 1 */
  id: string;
  /** @minLength 1 */
  name: string;
  yaml_config?: string;
  config?: RunPromptToolOptionApiConfig;
  config_type?: string;
  description?: string;
}

export type RunPromptChoiceOptionApiValue = { [key: string]: unknown };

export interface RunPromptChoiceOptionApi {
  value: RunPromptChoiceOptionApiValue;
  /** @minLength 1 */
  label: string;
}

export type RunPromptOptionsResultApiModelsItem = { [key: string]: unknown };

export type RunPromptOptionsResultApiToolConfig = { [key: string]: unknown };

export interface RunPromptOptionsResultApi {
  models: RunPromptOptionsResultApiModelsItem[];
  tool_config: RunPromptOptionsResultApiToolConfig;
  available_tools: RunPromptToolOptionApi[];
  output_formats: RunPromptChoiceOptionApi[];
  tool_choices: RunPromptChoiceOptionApi[];
}

export interface RunPromptOptionsResponseApi {
  status: boolean;
  result: RunPromptOptionsResultApi;
}

export type DatasetAddColumnsRequestApiNewColumnsDataItem = { [key: string]: unknown };

export interface DatasetAddColumnsRequestApi {
  new_columns_data: DatasetAddColumnsRequestApiNewColumnsDataItem[];
}

export type ColumnApiDataType = typeof ColumnApiDataType[keyof typeof ColumnApiDataType];


export const ColumnApiDataType = {
  text: 'text',
  boolean: 'boolean',
  integer: 'integer',
  float: 'float',
  json: 'json',
  array: 'array',
  image: 'image',
  images: 'images',
  datetime: 'datetime',
  audio: 'audio',
  document: 'document',
  others: 'others',
  persona: 'persona',
} as const;

export type ColumnApiSource = typeof ColumnApiSource[keyof typeof ColumnApiSource];


export const ColumnApiSource = {
  evaluation: 'evaluation',
  evaluation_tags: 'evaluation_tags',
  evaluation_reason: 'evaluation_reason',
  run_prompt: 'run_prompt',
  experiment: 'experiment',
  optimisation: 'optimisation',
  experiment_evaluation: 'experiment_evaluation',
  experiment_evaluation_tags: 'experiment_evaluation_tags',
  optimisation_evaluation: 'optimisation_evaluation',
  annotation_label: 'annotation_label',
  optimisation_evaluation_tags: 'optimisation_evaluation_tags',
  extracted_json: 'extracted_json',
  classification: 'classification',
  extracted_entities: 'extracted_entities',
  api_call: 'api_call',
  python_code: 'python_code',
  vector_db: 'vector_db',
  conditional: 'conditional',
  eval_playground: 'eval_playground',
  OTHERS: 'OTHERS',
} as const;

export interface ColumnApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 2000
     */
  name: string;
  data_type: ColumnApiDataType;
  dataset?: string;
  source: ColumnApiSource;
  /** @maxLength 2000 */
  source_id?: string;
}

export interface DatasetColumnsMutationResultApi {
  /** @minLength 1 */
  message: string;
  data?: ColumnApi[];
}

export interface DatasetColumnsMutationResponseApi {
  status: boolean;
  result: DatasetColumnsMutationResultApi;
}

export interface DatasetAddEmptyColumnsRequestApi {
  /** @minimum 0 */
  num_cols?: number;
}

export interface DatasetAddEmptyRowsRequestApi {
  /** @minimum 1 */
  num_rows?: number;
}

export type DatasetMultipleStaticColumnsRequestApiColumnsItem = { [key: string]: unknown };

export interface DatasetMultipleStaticColumnsRequestApi {
  columns: DatasetMultipleStaticColumnsRequestApiColumnsItem[];
}

export type DatasetAddRowsRequestApiRowsItem = { [key: string]: unknown };

export interface DatasetAddRowsRequestApi {
  rows: DatasetAddRowsRequestApiRowsItem[];
}

export type DatasetAddRowsFromExistingRequestApiColumnMapping = {[key: string]: string};

export interface DatasetAddRowsFromExistingRequestApi {
  source_dataset_id: string;
  column_mapping: DatasetAddRowsFromExistingRequestApiColumnMapping;
}

export interface DatasetRowsImportedResultApi {
  /** @minLength 1 */
  message: string;
  rows_added: number;
}

export interface DatasetRowsImportedResponseApi {
  status: boolean;
  result: DatasetRowsImportedResultApi;
}

export interface HuggingFaceAddRowsRequestApi {
  /** @minimum 0 */
  num_rows?: number;
  /** @minLength 1 */
  huggingface_dataset_name: string;
  /** @minLength 1 */
  huggingface_dataset_config: string;
  /** @minLength 1 */
  huggingface_dataset_split: string;
}

export interface DatasetRowsImportMessageResultApi {
  /** @minLength 1 */
  message: string;
}

export interface DatasetRowsImportMessageResponseApi {
  status: boolean;
  result: DatasetRowsImportMessageResultApi;
}

export interface DatasetStaticColumnRequestApi {
  /** @minLength 1 */
  new_column_name: string;
  /** @minLength 1 */
  column_type: string;
  source?: string;
}

export interface SyntheticDataApi {
  num_rows: number;
  columns: SyntheticDatasetColumnApi[];
  dataset: SyntheticDatasetPayloadApi;
  kb_id?: string;
  fill_existing_rows?: boolean;
}

export type UserEvalMutationRequestApiConfig = { [key: string]: unknown };

export type UserEvalMutationRequestApiCompositeWeightOverrides = { [key: string]: unknown };

export interface UserEvalMutationRequestApi {
  /**
     * @minLength 1
     * @maxLength 50
     */
  name: string;
  /**
     * @minLength 1
     * @maxLength 500
     */
  template_id: string;
  config: UserEvalMutationRequestApiConfig;
  kb_id?: string;
  error_localizer?: boolean;
  /** @maxLength 100 */
  model?: string;
  eval_type?: string;
  run?: boolean;
  save_as_template?: boolean;
  experiment_id?: string;
  composite_weight_overrides?: UserEvalMutationRequestApiCompositeWeightOverrides;
}

export type UserEvalUpdateRequestApiConfig = { [key: string]: unknown };

export type UserEvalUpdateRequestApiCompositeWeightOverrides = { [key: string]: unknown };

export interface UserEvalUpdateRequestApi {
  /** @maxLength 50 */
  name?: string;
  /** @maxLength 500 */
  template_id?: string;
  config: UserEvalUpdateRequestApiConfig;
  kb_id?: string;
  error_localizer?: boolean;
  /** @maxLength 100 */
  model?: string;
  eval_type?: string;
  run?: boolean;
  save_as_template?: boolean;
  experiment_id?: string;
  composite_weight_overrides?: UserEvalUpdateRequestApiCompositeWeightOverrides;
  pinned_version_id?: string;
}

export type DatasetBehaviorRequestApiColumnConfig = { [key: string]: unknown };

export type DatasetBehaviorRequestApiDatasetConfig = { [key: string]: unknown };

export interface DatasetBehaviorRequestApi {
  dataset_name?: string;
  column_order?: string[];
  column_config?: DatasetBehaviorRequestApiColumnConfig;
  dataset_config?: DatasetBehaviorRequestApiDatasetConfig;
}

export interface ExtractJsonColumnRequestApi {
  column_id: string;
  /** @minLength 1 */
  json_key: string;
  new_column_name?: string;
  concurrency?: number;
}

export type DatasetTableMetadataApiStatus = { [key: string]: unknown };

export interface DatasetTableMetadataApi {
  /** @minLength 1 */
  dataset_name: string;
  experiment_id?: string;
  /** @minLength 1 */
  experiment_name?: string;
  total_rows?: number;
  total_pages?: number;
  error_messages?: string[];
  status?: DatasetTableMetadataApiStatus;
}

export type DatasetTableColumnApiMetadata = { [key: string]: unknown };

export type DatasetTableColumnApiChoicesMap = { [key: string]: unknown };

export interface DatasetTableColumnApi {
  /** @minLength 1 */
  id: string;
  name: string;
  /** @minLength 1 */
  data_type: string;
  is_visible: boolean;
  is_frozen: boolean;
  /** @minLength 1 */
  source_type: string;
  /** @minLength 1 */
  origin_type: string;
  /** @minLength 1 */
  source_id: string;
  order_index: number;
  /** @minLength 1 */
  status: string;
  average_score: number;
  reason_column: boolean;
  is_numeric_eval: boolean;
  is_numeric_eval_percentage: boolean;
  eval_tag?: string[];
  metadata: DatasetTableColumnApiMetadata;
  choices_map: DatasetTableColumnApiChoicesMap;
}

export interface DatasetTableRowApi {
  row_id: string;
}

export type DatasetTableResultApiDatasetConfig = { [key: string]: unknown };

export interface DatasetTableResultApi {
  metadata?: DatasetTableMetadataApi;
  column_config: DatasetTableColumnApi[];
  table?: DatasetTableRowApi[];
  dataset_config?: DatasetTableResultApiDatasetConfig;
  synthetic_dataset?: boolean;
  synthetic_dataset_percentage?: number;
  synthetic_regenerate?: boolean;
  is_processing_data?: boolean;
}

export interface DatasetTableResponseApi {
  status: boolean;
  result: DatasetTableResultApi;
}

export type DatasetRowDataRequestApiFiltersItemFilterConfig = {
  /** Canonical field type, for example text, number, boolean, datetime, categorical, thumbs, annotator, or array. */
  filter_type: string;
  /** Canonical operator from api_contracts/filter_contract.json, for example equals, not_equals, in, not_in, between, not_between, is_null, or is_not_null. */
  filter_op: string;
  /** Scalar, list, range tuple, boolean, or null depending on filter_op and filter_type. */
  filter_value?: unknown;
  /** Column family such as SYSTEM_METRIC, SPAN_ATTRIBUTE, EVAL_METRIC, ANNOTATION, or NORMAL. */
  col_type?: string;
};

export type DatasetRowDataRequestApiFiltersItem = {
  /** Column or attribute id to filter on. */
  column_id: string;
  /** Optional UI label for chips and saved views. */
  display_name?: string;
  /** Optional source surface for mixed-source filters, for example traces, datasets, or simulation. */
  source?: string;
  /** Optional metric output type metadata used by eval and annotation filters. */
  output_type?: string;
  filter_config: DatasetRowDataRequestApiFiltersItemFilterConfig;
};

export type DatasetRowDataRequestApiSortItemType = typeof DatasetRowDataRequestApiSortItemType[keyof typeof DatasetRowDataRequestApiSortItemType];


export const DatasetRowDataRequestApiSortItemType = {
  ascending: 'ascending',
  descending: 'descending',
} as const;

export type DatasetRowDataRequestApiSortItem = {
  column_id: string;
  type?: DatasetRowDataRequestApiSortItemType;
};

export interface DatasetRowDataRequestApi {
  filters?: DatasetRowDataRequestApiFiltersItem[];
  sort?: DatasetRowDataRequestApiSortItem[];
  row_id: string;
}

export interface DatasetRowNavigationApi {
  row_id?: string[];
}

export type DatasetRowDataResultApiCurrent = { [key: string]: unknown };

export interface DatasetRowDataResultApi {
  next: DatasetRowNavigationApi;
  current: DatasetRowDataResultApiCurrent;
}

export interface DatasetRowDataResponseApi {
  status: boolean;
  result: DatasetRowDataResultApi;
}

export type EvalStructureApiMapping = { [key: string]: unknown };

export type EvalStructureApiConfig = { [key: string]: unknown };

export type EvalStructureApiParams = { [key: string]: unknown };

export type EvalStructureApiFunctionParamsSchema = { [key: string]: unknown };

export type EvalStructureApiModels = { [key: string]: unknown };

export type EvalStructureApiOutput = { [key: string]: unknown };

export type EvalStructureApiConfigParamsDesc = { [key: string]: unknown };

export type EvalStructureApiConfigParamsOption = { [key: string]: unknown };

export type EvalStructureApiChoices = { [key: string]: unknown };

export type EvalStructureApiRunConfig = { [key: string]: unknown };

export interface EvalStructureApi {
  id: string;
  template_id: string;
  /** @minLength 1 */
  name: string;
  description?: string;
  eval_tags?: string[];
  /** @minLength 1 */
  template_name?: string;
  required_keys?: string[];
  optional_keys?: string[];
  variable_keys?: string[];
  run_prompt_column?: boolean;
  mapping?: EvalStructureApiMapping;
  config?: EvalStructureApiConfig;
  params?: EvalStructureApiParams;
  function_params_schema?: EvalStructureApiFunctionParamsSchema;
  eval_type_id?: string;
  eval_type?: string;
  reason_column?: boolean;
  models?: EvalStructureApiModels;
  selected_model?: string;
  output?: EvalStructureApiOutput;
  config_params_desc?: EvalStructureApiConfigParamsDesc;
  config_params_option?: EvalStructureApiConfigParamsOption;
  kb_id?: string;
  error_localizer?: boolean;
  choices?: EvalStructureApiChoices;
  api_key_available?: boolean;
  run_config?: EvalStructureApiRunConfig;
}

export interface EvalStructureResultApi {
  eval: EvalStructureApi;
}

export interface EvalStructureResponseApi {
  status: boolean;
  result: EvalStructureResultApi;
}

export type EvalListResultApiEvalsItem = { [key: string]: unknown };

export interface EvalListResultApi {
  evals: EvalListResultApiEvalsItem[];
  eval_recommendations?: string[];
}

export interface EvalListResponseApi {
  status: boolean;
  result: EvalListResultApi;
}

export type PreviewRunEvalRequestApiConfig = { [key: string]: unknown };

export interface PreviewRunEvalRequestApi {
  config: PreviewRunEvalRequestApiConfig;
  template_id: string;
  model?: string;
  sdk_uuid?: string;
  source?: string;
  protect_flash?: boolean;
}

export interface StartEvalsProcessRequestApi {
  user_eval_ids: string[];
  experiment_id?: string;
  failed_only?: boolean;
}

export interface StopUserEvalRequestApi {
  experiment_id?: string;
}

export type SyntheticDatasetConfigPayloadApiColumnsItem = { [key: string]: unknown };

export type SyntheticDatasetConfigPayloadApiDataset = { [key: string]: unknown };

export interface SyntheticDatasetConfigPayloadApi {
  num_rows?: number;
  columns?: SyntheticDatasetConfigPayloadApiColumnsItem[];
  dataset?: SyntheticDatasetConfigPayloadApiDataset;
  kb_id?: string;
}

export interface SyntheticDatasetConfigResultApi {
  /** @minLength 1 */
  message: string;
  data: SyntheticDatasetConfigPayloadApi;
}

export interface SyntheticDatasetConfigResponseApi {
  status: boolean;
  result: SyntheticDatasetConfigResultApi;
}

export interface SyntheticDatasetConfigApi {
  num_rows: number;
  columns: SyntheticDatasetColumnApi[];
  dataset: SyntheticDatasetPayloadApi;
  kb_id?: string;
  regenerate?: boolean;
}

export interface SyntheticDatasetUpdateDataApi {
  dataset_id: string;
  /** @minLength 1 */
  dataset_name: string;
  num_rows?: number;
  num_columns?: number;
}

export interface SyntheticDatasetUpdateResultApi {
  /** @minLength 1 */
  message: string;
  data: SyntheticDatasetUpdateDataApi;
}

export interface SyntheticDatasetUpdateResponseApi {
  status: boolean;
  result: SyntheticDatasetUpdateResultApi;
}

export interface DatasetUpdateCellValueRequestApi {
  row_id: string;
  column_id: string;
  /** New cell value. Accepts JSON primitives or multipart file uploads. */
  new_value?: string;
}

export interface DatasetUpdateColumnNameRequestApi {
  /** @minLength 1 */
  new_column_name: string;
}

export interface DatasetUpdateColumnTypeRequestApi {
  /** @minLength 1 */
  new_column_type: string;
  preview?: boolean;
  force_update?: boolean;
}

export type ColumnTypeConversionResultApiInvalidValuesItem = { [key: string]: unknown };

export type ColumnTypeConversionResultApiValidConversionSamples = { [key: string]: unknown };

export interface ColumnTypeConversionResultApi {
  /** @minLength 1 */
  message?: string;
  column_id?: string;
  /** @minLength 1 */
  new_data_type?: string;
  /** @minLength 1 */
  status?: string;
  invalid_count?: number;
  invalid_values?: ColumnTypeConversionResultApiInvalidValuesItem[];
  valid_conversion_samples?: ColumnTypeConversionResultApiValidConversionSamples;
}

export interface ColumnTypeConversionResponseApi {
  status: boolean;
  result: ColumnTypeConversionResultApi;
}

export interface CreateDatasetFromExperimentRequestApi {
  name?: string;
  model_type?: string;
}

export interface DuplicateEvalTemplateApi {
  eval_template_id: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
}

export interface DuplicateEvalTemplateResponseResultApi {
  /** @minLength 1 */
  message: string;
  eval_template_id: string;
}

export interface DuplicateEvalTemplateResponseApi {
  status: boolean;
  result: DuplicateEvalTemplateResponseResultApi;
}

export interface EmbeddingConfigOptionApi {
  /** @minLength 1 */
  type: string;
  required: boolean;
  /** @minLength 1 */
  description: string;
  default?: string;
}

export type EmbeddingProviderApiConfigSchema = {[key: string]: EmbeddingConfigOptionApi};

export interface EmbeddingProviderApi {
  /** @minLength 1 */
  name: string;
  /** @minLength 1 */
  description: string;
  requires_api_key: boolean;
  config_schema: EmbeddingProviderApiConfigSchema;
}

export type EmbeddingsResponseResultApiEmbeddings = {[key: string]: EmbeddingProviderApi};

export interface EmbeddingsResponseResultApi {
  embeddings?: EmbeddingsResponseResultApiEmbeddings;
  embedding?: EmbeddingProviderApi;
}

export interface EmbeddingsResponseApi {
  status: boolean;
  result: EmbeddingsResponseResultApi;
}

export interface EvalGroupApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  readonly organization?: string;
  readonly workspace?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
  description?: string;
  readonly created_by?: string;
  is_sample?: boolean;
}

export type EvalPlayGroundApiConfig = { [key: string]: unknown };

export type EvalPlayGroundApiParams = { [key: string]: unknown };

export type EvalPlayGroundApiMapping = { [key: string]: unknown };

export type EvalPlayGroundApiMappingPaths = { [key: string]: unknown };

export type EvalPlayGroundApiInputDataTypes = { [key: string]: unknown };

export type EvalPlayGroundApiRowContext = { [key: string]: unknown };

export type EvalPlayGroundApiSpanContext = { [key: string]: unknown };

export type EvalPlayGroundApiTraceContext = { [key: string]: unknown };

export type EvalPlayGroundApiSessionContext = { [key: string]: unknown };

export type EvalPlayGroundApiCallContext = { [key: string]: unknown };

export interface EvalPlayGroundApi {
  template_id: string;
  /** @maxLength 100 */
  model?: string;
  kb_id?: string;
  error_localizer?: boolean;
  config?: EvalPlayGroundApiConfig;
  params?: EvalPlayGroundApiParams;
  mapping?: EvalPlayGroundApiMapping;
  mapping_paths?: EvalPlayGroundApiMappingPaths;
  input_data_types?: EvalPlayGroundApiInputDataTypes;
  row_context?: EvalPlayGroundApiRowContext;
  span_context?: EvalPlayGroundApiSpanContext;
  trace_context?: EvalPlayGroundApiTraceContext;
  session_context?: EvalPlayGroundApiSessionContext;
  call_context?: EvalPlayGroundApiCallContext;
  span_id?: string;
  trace_id?: string;
  session_id?: string;
  call_id?: string;
}

export type EvalExecutionResponseResultApiOutput = { [key: string]: unknown };

export type EvalExecutionResponseResultApiResult = { [key: string]: unknown };

export type EvalExecutionResponseResultApiScore = { [key: string]: unknown };

export type EvalExecutionResponseResultApiMetadata = { [key: string]: unknown };

export type EvalExecutionResponseResultApiErrorLocalizer = { [key: string]: unknown };

export type EvalExecutionResponseResultApiErrorDetails = { [key: string]: unknown };

export interface EvalExecutionResponseResultApi {
  output?: EvalExecutionResponseResultApiOutput;
  result?: EvalExecutionResponseResultApiResult;
  reason?: string;
  score?: EvalExecutionResponseResultApiScore;
  metadata?: EvalExecutionResponseResultApiMetadata;
  log_id?: string;
  error_localizer?: EvalExecutionResponseResultApiErrorLocalizer;
  error_details?: EvalExecutionResponseResultApiErrorDetails;
}

export interface EvalExecutionResponseApi {
  status: boolean;
  result: EvalExecutionResponseResultApi;
}

export interface EvalPlayGroundFeedbackApi {
  log_id: string;
  /** @minLength 1 */
  action_type: string;
  /** @minLength 1 */
  value: string;
  /** @minLength 1 */
  explanation?: string;
}

export interface EvalPlaygroundFeedbackResponseResultApi {
  /** @minLength 1 */
  message: string;
  feedback_id: string;
}

export interface EvalPlaygroundFeedbackResponseApi {
  status: boolean;
  result: EvalPlaygroundFeedbackResponseResultApi;
}

export interface EvalCodeSnippetResponseResultApi {
  /** @minLength 1 */
  python: string;
  /** @minLength 1 */
  curl: string;
  /** @minLength 1 */
  javascript: string;
}

export interface EvalCodeSnippetResponseApi {
  status: boolean;
  result: EvalCodeSnippetResponseResultApi;
}

export interface EvalSummaryTemplateApi {
  id: string;
  /** @minLength 1 */
  name: string;
  description: string;
  /** @minLength 1 */
  criteria: string;
}

export interface EvalSummaryTemplateListResponseResultApi {
  templates: EvalSummaryTemplateApi[];
}

export interface EvalSummaryTemplateListResponseApi {
  status: boolean;
  result: EvalSummaryTemplateListResponseResultApi;
}

export interface EvalSummaryTemplateMutationRequestApi {
  name?: string;
  description?: string;
  criteria?: string;
}

export interface EvalSummaryTemplateResponseApi {
  status: boolean;
  result: EvalSummaryTemplateApi;
}

export interface EvalSummaryTemplateDeleteResponseResultApi {
  deleted: boolean;
}

export interface EvalSummaryTemplateDeleteResponseApi {
  status: boolean;
  result: EvalSummaryTemplateDeleteResponseResultApi;
}

export type EvalTemplateApiOwner = typeof EvalTemplateApiOwner[keyof typeof EvalTemplateApiOwner];


export const EvalTemplateApiOwner = {
  system: 'system',
  user: 'user',
} as const;

export type EvalTemplateApiConfig = { [key: string]: unknown };

export interface EvalTemplateApi {
  /**
     * @minLength 1
     * @maxLength 50
     */
  name: string;
  owner?: EvalTemplateApiOwner;
  config: EvalTemplateApiConfig;
  eval_tags?: string[];
}

export interface EvalTemplateBulkDeleteRequestApi {
  template_ids: string[];
}

export interface EvalTemplateBulkDeleteResponseResultApi {
  deleted_count: number;
}

export interface EvalTemplateBulkDeleteResponseApi {
  status: boolean;
  result: EvalTemplateBulkDeleteResponseResultApi;
}

export type CompositeEvalAdhocExecuteRequestApiMapping = { [key: string]: unknown };

export type CompositeEvalAdhocExecuteRequestApiConfig = { [key: string]: unknown };

export type CompositeEvalAdhocExecuteRequestApiInputDataTypes = { [key: string]: unknown };

export type CompositeEvalAdhocExecuteRequestApiSpanContext = { [key: string]: unknown };

export type CompositeEvalAdhocExecuteRequestApiTraceContext = { [key: string]: unknown };

export type CompositeEvalAdhocExecuteRequestApiSessionContext = { [key: string]: unknown };

export type CompositeEvalAdhocExecuteRequestApiCallContext = { [key: string]: unknown };

export type CompositeEvalAdhocExecuteRequestApiRowContext = { [key: string]: unknown };

export type CompositeEvalAdhocExecuteRequestApiAggregationFunction = typeof CompositeEvalAdhocExecuteRequestApiAggregationFunction[keyof typeof CompositeEvalAdhocExecuteRequestApiAggregationFunction];


export const CompositeEvalAdhocExecuteRequestApiAggregationFunction = {
  weighted_avg: 'weighted_avg',
  avg: 'avg',
  min: 'min',
  max: 'max',
  pass_rate: 'pass_rate',
} as const;

export type CompositeEvalAdhocExecuteRequestApiCompositeChildAxis = typeof CompositeEvalAdhocExecuteRequestApiCompositeChildAxis[keyof typeof CompositeEvalAdhocExecuteRequestApiCompositeChildAxis];


export const CompositeEvalAdhocExecuteRequestApiCompositeChildAxis = {
  '': '',
  pass_fail: 'pass_fail',
  percentage: 'percentage',
  choices: 'choices',
  code: 'code',
} as const;

export type CompositeEvalAdhocExecuteRequestApiChildWeights = { [key: string]: unknown };

export type CompositeEvalAdhocExecuteRequestApiChildConfigs = { [key: string]: unknown };

export interface CompositeEvalAdhocExecuteRequestApi {
  mapping: CompositeEvalAdhocExecuteRequestApiMapping;
  model?: string;
  config?: CompositeEvalAdhocExecuteRequestApiConfig;
  error_localizer?: boolean;
  input_data_types?: CompositeEvalAdhocExecuteRequestApiInputDataTypes;
  span_context?: CompositeEvalAdhocExecuteRequestApiSpanContext;
  trace_context?: CompositeEvalAdhocExecuteRequestApiTraceContext;
  session_context?: CompositeEvalAdhocExecuteRequestApiSessionContext;
  call_context?: CompositeEvalAdhocExecuteRequestApiCallContext;
  row_context?: CompositeEvalAdhocExecuteRequestApiRowContext;
  child_template_ids: string[];
  aggregation_enabled?: boolean;
  aggregation_function?: CompositeEvalAdhocExecuteRequestApiAggregationFunction;
  composite_child_axis?: CompositeEvalAdhocExecuteRequestApiCompositeChildAxis;
  child_weights?: CompositeEvalAdhocExecuteRequestApiChildWeights;
  child_configs?: CompositeEvalAdhocExecuteRequestApiChildConfigs;
  pass_threshold?: number;
}

export type CompositeChildResultApiOutput = { [key: string]: unknown };

export type CompositeChildResultApiErrorLocalizerResult = { [key: string]: unknown };

export interface CompositeChildResultApi {
  child_id: string;
  /** @minLength 1 */
  child_name: string;
  order: number;
  score?: number;
  output?: CompositeChildResultApiOutput;
  reason?: string;
  output_type?: string;
  /** @minLength 1 */
  status: string;
  error?: string;
  log_id?: string;
  weight?: number;
  error_localizer_result?: CompositeChildResultApiErrorLocalizerResult;
}

export type CompositeEvalExecuteResponseResultApiErrorLocalizerResults = { [key: string]: unknown };

export interface CompositeEvalExecuteResponseResultApi {
  composite_id?: string;
  /** @minLength 1 */
  composite_name: string;
  aggregation_enabled: boolean;
  aggregation_function?: string;
  aggregate_score?: number;
  aggregate_pass?: boolean;
  children: CompositeChildResultApi[];
  summary?: string;
  error_localizer_results?: CompositeEvalExecuteResponseResultApiErrorLocalizerResults;
  total_children: number;
  completed_children: number;
  failed_children: number;
  evaluation_id?: string;
}

export interface CompositeEvalExecuteResponseApi {
  status: boolean;
  result: CompositeEvalExecuteResponseResultApi;
}

export type CompositeEvalCreateRequestApiAggregationFunction = typeof CompositeEvalCreateRequestApiAggregationFunction[keyof typeof CompositeEvalCreateRequestApiAggregationFunction];


export const CompositeEvalCreateRequestApiAggregationFunction = {
  weighted_avg: 'weighted_avg',
  avg: 'avg',
  min: 'min',
  max: 'max',
  pass_rate: 'pass_rate',
} as const;

export type CompositeEvalCreateRequestApiChildWeights = { [key: string]: unknown };

export type CompositeEvalCreateRequestApiChildPinnedVersions = { [key: string]: unknown };

export type CompositeEvalCreateRequestApiChildConfigs = { [key: string]: unknown };

export type CompositeEvalCreateRequestApiCompositeChildAxis = typeof CompositeEvalCreateRequestApiCompositeChildAxis[keyof typeof CompositeEvalCreateRequestApiCompositeChildAxis];


export const CompositeEvalCreateRequestApiCompositeChildAxis = {
  '': '',
  pass_fail: 'pass_fail',
  percentage: 'percentage',
  choices: 'choices',
  code: 'code',
} as const;

export interface CompositeEvalCreateRequestApi {
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  description?: string;
  tags?: string[];
  child_template_ids: string[];
  aggregation_enabled?: boolean;
  aggregation_function?: CompositeEvalCreateRequestApiAggregationFunction;
  child_weights?: CompositeEvalCreateRequestApiChildWeights;
  child_pinned_versions?: CompositeEvalCreateRequestApiChildPinnedVersions;
  child_configs?: CompositeEvalCreateRequestApiChildConfigs;
  composite_child_axis?: CompositeEvalCreateRequestApiCompositeChildAxis;
}

export type CompositeChildItemApiConfig = { [key: string]: unknown };

export interface CompositeChildItemApi {
  child_id: string;
  /** @minLength 1 */
  child_name: string;
  order: number;
  /** @minLength 1 */
  eval_type?: string;
  pinned_version_id?: string;
  pinned_version_number?: number;
  weight?: number;
  config?: CompositeChildItemApiConfig;
  required_keys?: string[];
}

export interface CompositeEvalCreateResponseResultApi {
  id: string;
  /** @minLength 1 */
  name: string;
  /** @minLength 1 */
  template_type?: string;
  aggregation_enabled: boolean;
  /** @minLength 1 */
  aggregation_function: string;
  composite_child_axis?: string;
  children: CompositeChildItemApi[];
}

export interface CompositeEvalCreateResponseApi {
  status: boolean;
  result: CompositeEvalCreateResponseResultApi;
}

export type EvalTemplateCreateV2RequestApiEvalType = typeof EvalTemplateCreateV2RequestApiEvalType[keyof typeof EvalTemplateCreateV2RequestApiEvalType];


export const EvalTemplateCreateV2RequestApiEvalType = {
  llm: 'llm',
  code: 'code',
  agent: 'agent',
} as const;

export type EvalTemplateCreateV2RequestApiOutputType = typeof EvalTemplateCreateV2RequestApiOutputType[keyof typeof EvalTemplateCreateV2RequestApiOutputType];


export const EvalTemplateCreateV2RequestApiOutputType = {
  pass_fail: 'pass_fail',
  percentage: 'percentage',
  deterministic: 'deterministic',
} as const;

export type EvalTemplateCreateV2RequestApiChoiceScores = { [key: string]: unknown };

export type EvalTemplateCreateV2RequestApiCodeLanguage = typeof EvalTemplateCreateV2RequestApiCodeLanguage[keyof typeof EvalTemplateCreateV2RequestApiCodeLanguage];


export const EvalTemplateCreateV2RequestApiCodeLanguage = {
  python: 'python',
  javascript: 'javascript',
} as const;

export type EvalTemplateCreateV2RequestApiMessagesItem = { [key: string]: unknown };

export type EvalTemplateCreateV2RequestApiFewShotExamplesItem = { [key: string]: unknown };

export type EvalTemplateCreateV2RequestApiMode = typeof EvalTemplateCreateV2RequestApiMode[keyof typeof EvalTemplateCreateV2RequestApiMode];


export const EvalTemplateCreateV2RequestApiMode = {
  auto: 'auto',
  agent: 'agent',
  quick: 'quick',
} as const;

export type EvalTemplateCreateV2RequestApiTools = { [key: string]: unknown };

export type EvalTemplateCreateV2RequestApiDataInjection = { [key: string]: unknown };

export type EvalTemplateCreateV2RequestApiSummary = { [key: string]: unknown };

export type EvalTemplateCreateV2RequestApiTemplateFormat = typeof EvalTemplateCreateV2RequestApiTemplateFormat[keyof typeof EvalTemplateCreateV2RequestApiTemplateFormat];


export const EvalTemplateCreateV2RequestApiTemplateFormat = {
  mustache: 'mustache',
  jinja: 'jinja',
} as const;

export interface EvalTemplateCreateV2RequestApi {
  /** @maxLength 255 */
  name?: string;
  is_draft?: boolean;
  eval_type?: EvalTemplateCreateV2RequestApiEvalType;
  /** @maxLength 100000 */
  instructions?: string;
  /** @minLength 1 */
  model?: string;
  output_type?: EvalTemplateCreateV2RequestApiOutputType;
  /**
     * @minimum 0
     * @maximum 1
     */
  pass_threshold?: number;
  choice_scores?: EvalTemplateCreateV2RequestApiChoiceScores;
  description?: string;
  tags?: string[];
  check_internet?: boolean;
  /** @maxLength 100000 */
  code?: string;
  code_language?: EvalTemplateCreateV2RequestApiCodeLanguage;
  messages?: EvalTemplateCreateV2RequestApiMessagesItem[];
  few_shot_examples?: EvalTemplateCreateV2RequestApiFewShotExamplesItem[];
  mode?: EvalTemplateCreateV2RequestApiMode;
  tools?: EvalTemplateCreateV2RequestApiTools;
  knowledge_bases?: string[];
  data_injection?: EvalTemplateCreateV2RequestApiDataInjection;
  summary?: EvalTemplateCreateV2RequestApiSummary;
  error_localizer_enabled?: boolean;
  template_format?: EvalTemplateCreateV2RequestApiTemplateFormat;
}

export interface EvalTemplateCreateResponseResultApi {
  id: string;
  /** @minLength 1 */
  name: string;
  /** @minLength 1 */
  version: string;
}

export interface EvalTemplateCreateResponseApi {
  status: boolean;
  result: EvalTemplateCreateResponseResultApi;
}

export interface EvalTemplateListChartsRequestApi {
  template_ids: string[];
}

export interface EvalTemplateChartPointApi {
  /** @minLength 1 */
  timestamp: string;
  value: number;
}

export interface EvalTemplateListChartsItemApi {
  chart: EvalTemplateChartPointApi[];
  error_rate: EvalTemplateChartPointApi[];
  run_count: number;
}

export type EvalTemplateListChartsResponseResultApiCharts = {[key: string]: EvalTemplateListChartsItemApi};

export interface EvalTemplateListChartsResponseResultApi {
  charts: EvalTemplateListChartsResponseResultApiCharts;
}

export interface EvalTemplateListChartsResponseApi {
  status: boolean;
  result: EvalTemplateListChartsResponseResultApi;
}

export type EvalListRequestApiOwnerFilter = typeof EvalListRequestApiOwnerFilter[keyof typeof EvalListRequestApiOwnerFilter];


export const EvalListRequestApiOwnerFilter = {
  all: 'all',
  user: 'user',
  system: 'system',
} as const;

export type EvalListRequestApiSortBy = typeof EvalListRequestApiSortBy[keyof typeof EvalListRequestApiSortBy];


export const EvalListRequestApiSortBy = {
  name: 'name',
  updated_at: 'updated_at',
  created_at: 'created_at',
} as const;

export type EvalListRequestApiSortOrder = typeof EvalListRequestApiSortOrder[keyof typeof EvalListRequestApiSortOrder];


export const EvalListRequestApiSortOrder = {
  asc: 'asc',
  desc: 'desc',
} as const;

export type EvalListFiltersApiEvalTypeItem = typeof EvalListFiltersApiEvalTypeItem[keyof typeof EvalListFiltersApiEvalTypeItem];


export const EvalListFiltersApiEvalTypeItem = {
  llm: 'llm',
  code: 'code',
  agent: 'agent',
} as const;

export type EvalListFiltersApiEvalTypeNotItem = typeof EvalListFiltersApiEvalTypeNotItem[keyof typeof EvalListFiltersApiEvalTypeNotItem];


export const EvalListFiltersApiEvalTypeNotItem = {
  llm: 'llm',
  code: 'code',
  agent: 'agent',
} as const;

export type EvalListFiltersApiOutputTypeItem = typeof EvalListFiltersApiOutputTypeItem[keyof typeof EvalListFiltersApiOutputTypeItem];


export const EvalListFiltersApiOutputTypeItem = {
  pass_fail: 'pass_fail',
  percentage: 'percentage',
  deterministic: 'deterministic',
} as const;

export type EvalListFiltersApiOutputTypeNotItem = typeof EvalListFiltersApiOutputTypeNotItem[keyof typeof EvalListFiltersApiOutputTypeNotItem];


export const EvalListFiltersApiOutputTypeNotItem = {
  pass_fail: 'pass_fail',
  percentage: 'percentage',
  deterministic: 'deterministic',
} as const;

export type EvalListFiltersApiTemplateTypeItem = typeof EvalListFiltersApiTemplateTypeItem[keyof typeof EvalListFiltersApiTemplateTypeItem];


export const EvalListFiltersApiTemplateTypeItem = {
  single: 'single',
  composite: 'composite',
} as const;

export type EvalListFiltersApiTemplateTypeNotItem = typeof EvalListFiltersApiTemplateTypeNotItem[keyof typeof EvalListFiltersApiTemplateTypeNotItem];


export const EvalListFiltersApiTemplateTypeNotItem = {
  single: 'single',
  composite: 'composite',
} as const;

export interface EvalListFiltersApi {
  eval_type?: EvalListFiltersApiEvalTypeItem[];
  eval_type_not?: EvalListFiltersApiEvalTypeNotItem[];
  output_type?: EvalListFiltersApiOutputTypeItem[];
  output_type_not?: EvalListFiltersApiOutputTypeNotItem[];
  template_type?: EvalListFiltersApiTemplateTypeItem[];
  template_type_not?: EvalListFiltersApiTemplateTypeNotItem[];
  tags?: string[];
  tags_not?: string[];
  created_by?: string[];
  created_by_not?: string[];
  names?: string[];
  names_not?: string[];
}

export interface EvalListRequestApi {
  /** @minimum 0 */
  page?: number;
  /**
     * @minimum 1
     * @maximum 100
     */
  page_size?: number;
  search?: string;
  owner_filter?: EvalListRequestApiOwnerFilter;
  filters?: EvalListFiltersApi;
  sort_by?: EvalListRequestApiSortBy;
  sort_order?: EvalListRequestApiSortOrder;
}

export interface EvalTemplateListItemApi {
  id: string;
  /** @minLength 1 */
  name: string;
  /** @minLength 1 */
  template_type: string;
  /** @minLength 1 */
  eval_type: string;
  /** @minLength 1 */
  output_type: string;
  /** @minLength 1 */
  owner: string;
  /** @minLength 1 */
  created_by_name: string;
  version_count: number;
  /** @minLength 1 */
  current_version: string;
  /** @minLength 1 */
  last_updated: string;
  thirty_day_chart: EvalTemplateChartPointApi[];
  thirty_day_error_rate: EvalTemplateChartPointApi[];
  thirty_day_run_count: number;
  tags: string[];
}

export interface EvalTemplateListResponseResultApi {
  items: EvalTemplateListItemApi[];
  total: number;
  page: number;
  page_size: number;
}

export interface EvalTemplateListResponseApi {
  status: boolean;
  result: EvalTemplateListResponseResultApi;
}

export interface CompositeEvalDetailResponseResultApi {
  id: string;
  /** @minLength 1 */
  name: string;
  /** @minLength 1 */
  template_type?: string;
  aggregation_enabled: boolean;
  /** @minLength 1 */
  aggregation_function: string;
  composite_child_axis?: string;
  children: CompositeChildItemApi[];
  description?: string;
  tags?: string[];
  created_at?: string;
  updated_at?: string;
  version_number?: number;
}

export interface CompositeEvalDetailResponseApi {
  status: boolean;
  result: CompositeEvalDetailResponseResultApi;
}

export type CompositeEvalUpdateRequestApiAggregationFunction = typeof CompositeEvalUpdateRequestApiAggregationFunction[keyof typeof CompositeEvalUpdateRequestApiAggregationFunction];


export const CompositeEvalUpdateRequestApiAggregationFunction = {
  weighted_avg: 'weighted_avg',
  avg: 'avg',
  min: 'min',
  max: 'max',
  pass_rate: 'pass_rate',
} as const;

export type CompositeEvalUpdateRequestApiChildWeights = { [key: string]: unknown };

export type CompositeEvalUpdateRequestApiChildPinnedVersions = { [key: string]: unknown };

export type CompositeEvalUpdateRequestApiChildConfigs = { [key: string]: unknown };

export type CompositeEvalUpdateRequestApiCompositeChildAxis = typeof CompositeEvalUpdateRequestApiCompositeChildAxis[keyof typeof CompositeEvalUpdateRequestApiCompositeChildAxis];


export const CompositeEvalUpdateRequestApiCompositeChildAxis = {
  '': '',
  pass_fail: 'pass_fail',
  percentage: 'percentage',
  choices: 'choices',
  code: 'code',
} as const;

export interface CompositeEvalUpdateRequestApi {
  /**
     * @minLength 1
     * @maxLength 255
     */
  name?: string;
  description?: string;
  tags?: string[];
  aggregation_enabled?: boolean;
  aggregation_function?: CompositeEvalUpdateRequestApiAggregationFunction;
  child_template_ids?: string[];
  child_weights?: CompositeEvalUpdateRequestApiChildWeights;
  child_pinned_versions?: CompositeEvalUpdateRequestApiChildPinnedVersions;
  child_configs?: CompositeEvalUpdateRequestApiChildConfigs;
  composite_child_axis?: CompositeEvalUpdateRequestApiCompositeChildAxis;
}

export type CompositeEvalExecuteRequestApiMapping = { [key: string]: unknown };

export type CompositeEvalExecuteRequestApiConfig = { [key: string]: unknown };

export type CompositeEvalExecuteRequestApiInputDataTypes = { [key: string]: unknown };

export type CompositeEvalExecuteRequestApiSpanContext = { [key: string]: unknown };

export type CompositeEvalExecuteRequestApiTraceContext = { [key: string]: unknown };

export type CompositeEvalExecuteRequestApiSessionContext = { [key: string]: unknown };

export type CompositeEvalExecuteRequestApiCallContext = { [key: string]: unknown };

export type CompositeEvalExecuteRequestApiRowContext = { [key: string]: unknown };

export interface CompositeEvalExecuteRequestApi {
  mapping: CompositeEvalExecuteRequestApiMapping;
  model?: string;
  config?: CompositeEvalExecuteRequestApiConfig;
  error_localizer?: boolean;
  input_data_types?: CompositeEvalExecuteRequestApiInputDataTypes;
  span_context?: CompositeEvalExecuteRequestApiSpanContext;
  trace_context?: CompositeEvalExecuteRequestApiTraceContext;
  session_context?: CompositeEvalExecuteRequestApiSessionContext;
  call_context?: CompositeEvalExecuteRequestApiCallContext;
  row_context?: CompositeEvalExecuteRequestApiRowContext;
}

export type EvalTemplateDetailResponseResultApiChoiceScores = { [key: string]: unknown };

export type EvalTemplateDetailResponseResultApiChoices = { [key: string]: unknown };

export type EvalTemplateDetailResponseResultApiConfig = { [key: string]: unknown };

export interface EvalTemplateDetailResponseResultApi {
  id: string;
  /** @minLength 1 */
  name: string;
  description?: string;
  /** @minLength 1 */
  template_type: string;
  /** @minLength 1 */
  eval_type: string;
  instructions?: string;
  model?: string;
  /** @minLength 1 */
  output_type: string;
  pass_threshold: number;
  choice_scores?: EvalTemplateDetailResponseResultApiChoiceScores;
  choices?: EvalTemplateDetailResponseResultApiChoices;
  multi_choice: boolean;
  code?: string;
  code_language?: string;
  required_keys: string[];
  /** @minLength 1 */
  owner: string;
  /** @minLength 1 */
  created_by_name: string;
  version_count: number;
  /** @minLength 1 */
  current_version: string;
  tags: string[];
  check_internet: boolean;
  error_localizer_enabled: boolean;
  /** @minLength 1 */
  template_format: string;
  aggregation_enabled: boolean;
  /** @minLength 1 */
  aggregation_function: string;
  composite_child_axis?: string;
  config?: EvalTemplateDetailResponseResultApiConfig;
  /** @minLength 1 */
  created_at: string;
  /** @minLength 1 */
  updated_at: string;
}

export interface EvalTemplateDetailResponseApi {
  status: boolean;
  result: EvalTemplateDetailResponseResultApi;
}

export interface EvalFeedbackListItemApi {
  id: string;
  value: string;
  explanation: string;
  source: string;
  source_id: string;
  action_type: string;
  user_name: string;
  /** @minLength 1 */
  created_at: string;
  user_eval_metric_id: string;
  custom_eval_config_id: string;
  experiment_id: string;
}

export interface EvalFeedbackListResponseResultApi {
  template_id: string;
  items: EvalFeedbackListItemApi[];
  total: number;
  page: number;
  page_size: number;
}

export interface EvalFeedbackListResponseApi {
  status: boolean;
  result: EvalFeedbackListResponseResultApi;
}

export interface GroundTruthRoleMappingApi {
  /** @minLength 1 */
  output?: string;
  /** @minLength 1 */
  explanation?: string;
  /**
     * Legacy alias for `output`.
     * @minLength 1
     */
  expected_output?: string;
  /**
     * Legacy alias for `explanation`.
     * @minLength 1
     */
  reasoning?: string;
  /**
     * Legacy alias for `explanation`.
     * @minLength 1
     */
  reason?: string;
}

/**
 * Map of template variable name to GT column name (string) or list of column names.
 */
export type GroundTruthItemApiVariableMapping = { [key: string]: unknown };

export interface GroundTruthItemApi {
  id: string;
  /** @minLength 1 */
  name: string;
  description?: string;
  file_name?: string;
  columns: string[];
  row_count: number;
  /** Map of template variable name to GT column name (string) or list of column names. */
  variable_mapping?: GroundTruthItemApiVariableMapping;
  role_mapping?: GroundTruthRoleMappingApi;
  /** @minLength 1 */
  embedding_status?: string;
  embedded_row_count?: number;
  /** @minLength 1 */
  storage_type?: string;
  created_at?: string;
  embeddings_stale?: boolean;
  is_active?: boolean;
  enabled?: boolean;
  max_examples?: number;
  similarity_threshold?: number;
}

export interface GroundTruthListResponseResultApi {
  template_id: string;
  items: GroundTruthItemApi[];
  total: number;
}

export interface GroundTruthListResponseApi {
  status: boolean;
  result: GroundTruthListResponseResultApi;
}

export type GroundTruthUploadRequestApiDataItem = { [key: string]: unknown };

export type GroundTruthUploadRequestApiVariableMapping = { [key: string]: unknown };

export type GroundTruthUploadRequestApiRoleMapping = { [key: string]: unknown };

export interface GroundTruthUploadRequestApi {
  readonly file?: string;
  /** @maxLength 255 */
  name?: string;
  description?: string;
  file_name?: string;
  columns?: string[];
  data?: GroundTruthUploadRequestApiDataItem[];
  variable_mapping?: GroundTruthUploadRequestApiVariableMapping;
  role_mapping?: GroundTruthUploadRequestApiRoleMapping;
}

export interface GroundTruthUploadResponseResultApi {
  id: string;
  /** @minLength 1 */
  name: string;
  row_count: number;
  columns: string[];
  /** @minLength 1 */
  embedding_status: string;
}

export interface GroundTruthUploadResponseApi {
  status: boolean;
  result: GroundTruthUploadResponseResultApi;
}

export type EvalTemplateUpdateV2RequestApiEvalType = typeof EvalTemplateUpdateV2RequestApiEvalType[keyof typeof EvalTemplateUpdateV2RequestApiEvalType];


export const EvalTemplateUpdateV2RequestApiEvalType = {
  llm: 'llm',
  code: 'code',
  agent: 'agent',
} as const;

export type EvalTemplateUpdateV2RequestApiOutputType = typeof EvalTemplateUpdateV2RequestApiOutputType[keyof typeof EvalTemplateUpdateV2RequestApiOutputType];


export const EvalTemplateUpdateV2RequestApiOutputType = {
  pass_fail: 'pass_fail',
  percentage: 'percentage',
  deterministic: 'deterministic',
} as const;

export type EvalTemplateUpdateV2RequestApiChoiceScores = { [key: string]: unknown };

export type EvalTemplateUpdateV2RequestApiCodeLanguage = typeof EvalTemplateUpdateV2RequestApiCodeLanguage[keyof typeof EvalTemplateUpdateV2RequestApiCodeLanguage];


export const EvalTemplateUpdateV2RequestApiCodeLanguage = {
  python: 'python',
  javascript: 'javascript',
} as const;

export type EvalTemplateUpdateV2RequestApiMessagesItem = { [key: string]: unknown };

export type EvalTemplateUpdateV2RequestApiFewShotExamplesItem = { [key: string]: unknown };

export type EvalTemplateUpdateV2RequestApiMode = typeof EvalTemplateUpdateV2RequestApiMode[keyof typeof EvalTemplateUpdateV2RequestApiMode];


export const EvalTemplateUpdateV2RequestApiMode = {
  auto: 'auto',
  agent: 'agent',
  quick: 'quick',
} as const;

export type EvalTemplateUpdateV2RequestApiTools = { [key: string]: unknown };

export type EvalTemplateUpdateV2RequestApiDataInjection = { [key: string]: unknown };

export type EvalTemplateUpdateV2RequestApiSummary = { [key: string]: unknown };

export type EvalTemplateUpdateV2RequestApiTemplateFormat = typeof EvalTemplateUpdateV2RequestApiTemplateFormat[keyof typeof EvalTemplateUpdateV2RequestApiTemplateFormat];


export const EvalTemplateUpdateV2RequestApiTemplateFormat = {
  mustache: 'mustache',
  jinja: 'jinja',
} as const;

export interface EvalTemplateUpdateV2RequestApi {
  /**
     * @minLength 1
     * @maxLength 255
     */
  name?: string;
  eval_type?: EvalTemplateUpdateV2RequestApiEvalType;
  /** @minLength 1 */
  instructions?: string;
  /** @minLength 1 */
  model?: string;
  output_type?: EvalTemplateUpdateV2RequestApiOutputType;
  /**
     * @minimum 0
     * @maximum 1
     */
  pass_threshold?: number;
  choice_scores?: EvalTemplateUpdateV2RequestApiChoiceScores;
  multi_choice?: boolean;
  description?: string;
  tags?: string[];
  check_internet?: boolean;
  code?: string;
  code_language?: EvalTemplateUpdateV2RequestApiCodeLanguage;
  messages?: EvalTemplateUpdateV2RequestApiMessagesItem[];
  few_shot_examples?: EvalTemplateUpdateV2RequestApiFewShotExamplesItem[];
  mode?: EvalTemplateUpdateV2RequestApiMode;
  tools?: EvalTemplateUpdateV2RequestApiTools;
  knowledge_bases?: string[];
  data_injection?: EvalTemplateUpdateV2RequestApiDataInjection;
  summary?: EvalTemplateUpdateV2RequestApiSummary;
  error_localizer_enabled?: boolean;
  publish?: boolean;
  template_format?: EvalTemplateUpdateV2RequestApiTemplateFormat;
}

export interface EvalTemplateUpdateResponseResultApi {
  id: string;
  /** @minLength 1 */
  name: string;
  updated: boolean;
}

export interface EvalTemplateUpdateResponseApi {
  status: boolean;
  result: EvalTemplateUpdateResponseResultApi;
}

export interface EvalUsageStatsApi {
  total_runs: number;
  runs_period: number;
  success_count: number;
  error_count: number;
  pass_rate: number;
}

export interface EvalUsageChartPointApi {
  /** @minLength 1 */
  timestamp: string;
  calls?: number;
  avg_latency_ms?: number;
  avg_score?: number;
  pass_count?: number;
  fail_count?: number;
}

export interface EvalUsageNumberCellApi {
  cell_value?: number;
}

export interface EvalUsageStringCellApi {
  cell_value?: string;
}

export interface EvalUsageFeedbackApi {
  id: string;
  value?: string;
  explanation?: string;
  action_type?: string;
  created_at?: string;
  user?: string;
}

export interface EvalUsageFeedbackCellApi {
  cell_value?: EvalUsageFeedbackApi;
}

export type EvalUsageWarningsCellApiCellValueItem = { [key: string]: unknown };

export interface EvalUsageWarningsCellApi {
  cell_value?: EvalUsageWarningsCellApiCellValueItem[];
}

export type EvalUsageLogItemDetailApiInputVariables = {[key: string]: string};

export type EvalUsageLogItemDetailApiOutput = { [key: string]: unknown };

export type EvalUsageLogItemDetailApiMappings = {[key: string]: string};

/**
 * String or JSON object.
 */
export type EvalUsageLogItemDetailApiModel = string | { [key: string]: unknown };

export interface EvalUsageLogItemDetailApi {
  input_variables?: EvalUsageLogItemDetailApiInputVariables;
  output?: EvalUsageLogItemDetailApiOutput;
  warnings?: string[];
  mappings?: EvalUsageLogItemDetailApiMappings;
  /** String or JSON object. */
  model?: EvalUsageLogItemDetailApiModel;
  /** @minLength 1 */
  version_id?: string;
  version_number?: number;
  children?: string[];
  aggregation_function?: string;
  total_children?: number;
  completed_children?: number;
  failed_children?: number;
}

export interface EvalUsageTableRowApi {
  /** @minLength 1 */
  row_id: string;
  score?: EvalUsageNumberCellApi;
  result?: EvalUsageStringCellApi;
  input?: EvalUsageStringCellApi;
  reason?: EvalUsageStringCellApi;
  source?: EvalUsageStringCellApi;
  version?: EvalUsageStringCellApi;
  feedback?: EvalUsageFeedbackCellApi;
  created_at?: EvalUsageStringCellApi;
  status?: EvalUsageStringCellApi;
  warnings?: EvalUsageWarningsCellApi;
  detail?: EvalUsageLogItemDetailApi;
  composite?: boolean;
  aggregate_pass?: boolean;
  [key: string]: unknown;
 }

export interface EvalUsagePaginationApi {
  total: number;
  page: number;
  page_size: number;
}

export interface EvalUsageStatsResponseResultApi {
  template_id: string;
  is_composite: boolean;
  stats: EvalUsageStatsApi;
  chart: EvalUsageChartPointApi[];
  table: EvalUsageTableRowApi[];
  logs: EvalUsagePaginationApi;
}

export interface EvalUsageStatsResponseApi {
  status: boolean;
  result: EvalUsageStatsResponseResultApi;
}

export type EvalTemplateVersionItemApiConfigSnapshot = { [key: string]: unknown };

export interface EvalTemplateVersionItemApi {
  id: string;
  version_number: number;
  is_default: boolean;
  criteria?: string;
  model?: string;
  config_snapshot?: EvalTemplateVersionItemApiConfigSnapshot;
  created_by_name?: string;
  created_at?: string;
}

export interface EvalTemplateVersionListResponseResultApi {
  template_id: string;
  versions: EvalTemplateVersionItemApi[];
  total: number;
}

export interface EvalTemplateVersionListResponseApi {
  status: boolean;
  result: EvalTemplateVersionListResponseResultApi;
}

export type EvalTemplateVersionCreateRequestApiConfigSnapshot = { [key: string]: unknown };

export interface EvalTemplateVersionCreateRequestApi {
  criteria?: string;
  model?: string;
  config_snapshot?: EvalTemplateVersionCreateRequestApiConfigSnapshot;
}

export interface EvalTemplateVersionResponseResultApi {
  id: string;
  version_number: number;
  is_default: boolean;
}

export interface EvalTemplateVersionResponseApi {
  status: boolean;
  result: EvalTemplateVersionResponseResultApi;
}

export interface EvalTemplateVersionRestoreResponseResultApi {
  id: string;
  version_number: number;
  is_default: boolean;
  restored_from: number;
}

export interface EvalTemplateVersionRestoreResponseApi {
  status: boolean;
  result: EvalTemplateVersionRestoreResponseResultApi;
}

export type EvalUserTemplateApiConfig = { [key: string]: unknown };

export interface EvalUserTemplateApi {
  /**
     * @minLength 1
     * @maxLength 50
     */
  name: string;
  /**
     * @minLength 1
     * @maxLength 500
     */
  template_id: string;
  /**
     * @minLength 1
     * @maxLength 500
     */
  dataset_id: string;
  config: EvalUserTemplateApiConfig;
  /**
     * @minLength 1
     * @maxLength 100
     */
  model?: string;
}

export interface SingleRowEvaluationRequestApi {
  user_eval_metric_ids?: string[];
  row_ids?: string[];
  selected_all_rows?: boolean;
}

export interface SingleRowEvaluationResponseResultApi {
  /** @minLength 1 */
  success: string;
}

export interface SingleRowEvaluationResponseApi {
  status: boolean;
  result: SingleRowEvaluationResponseResultApi;
}

export interface ExperimentsTableGetApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
}

export type ExperimentsTableApiPromptConfig = { [key: string]: unknown };

export interface ExperimentsTableApi {
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  dataset_id: string;
  prompt_config?: ExperimentsTableApiPromptConfig;
  user_eval_template_ids?: string[];
  column_id: string;
}

export interface ExperimentLegacyDetailResponseApi {
  status: boolean;
  result: ExperimentsTableApi;
}

export interface ExperimentStringResultResponseApi {
  status: boolean;
  /** @minLength 1 */
  result: string;
}

export type ExperimentsTableUpdateApiPromptConfig = { [key: string]: unknown };

export interface ExperimentsTableUpdateApi {
  experiment_id: string;
  re_run?: boolean;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  dataset_id: string;
  prompt_config?: ExperimentsTableUpdateApiPromptConfig;
  user_eval_template_ids?: string[];
  column_id: string;
}

export type ExperimentListApiStatus = typeof ExperimentListApiStatus[keyof typeof ExperimentListApiStatus];


export const ExperimentListApiStatus = {
  NotStarted: 'NotStarted',
  Queued: 'Queued',
  Running: 'Running',
  Completed: 'Completed',
  Editing: 'Editing',
  Inactive: 'Inactive',
  Failed: 'Failed',
  PartialRun: 'PartialRun',
  ExperimentEvaluation: 'ExperimentEvaluation',
  Uploading: 'Uploading',
  PartialExtracted: 'PartialExtracted',
  Processing: 'Processing',
  Deleting: 'Deleting',
  PartialCompleted: 'PartialCompleted',
  OptimizationEvaluation: 'OptimizationEvaluation',
  Error: 'Error',
  Cancelled: 'Cancelled',
} as const;

export interface ExperimentListApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  status?: ExperimentListApiStatus;
  readonly eval_templates_count?: string;
  readonly created_at?: string;
  readonly models_count?: string;
  dataset: string;
}

export interface ExperimentRerunRequestApi {
  experiment_ids: string[];
  use_temporal?: boolean;
  /** @minimum 1 */
  max_concurrent_rows?: number;
}

export type ExperimentCreateV2ApiExperimentType = typeof ExperimentCreateV2ApiExperimentType[keyof typeof ExperimentCreateV2ApiExperimentType];


export const ExperimentCreateV2ApiExperimentType = {
  llm: 'llm',
  tts: 'tts',
  stt: 'stt',
  image: 'image',
} as const;

/**
 * String or JSON object.
 */
export type PromptModelParamsApiResponseFormat = string | { [key: string]: unknown };

export interface PromptModelParamsApi {
  temperature?: number;
  max_tokens?: number;
  top_p?: number;
  frequency_penalty?: number;
  presence_penalty?: number;
  /** String or JSON object. */
  response_format?: PromptModelParamsApiResponseFormat;
  [key: string]: unknown;
 }

/**
 * Any valid JSON value.
 */
export type PromptConfigurationApiToolsItem = { [key: string]: unknown };

/**
 * Any valid JSON value.
 */
export type PromptConfigurationApiModelDetail = { [key: string]: unknown };

export interface PromptConfigurationApi {
  tool_choice?: string;
  template_format?: string;
  tools?: PromptConfigurationApiToolsItem[];
  output_format?: string;
  model_type?: string;
  /** Any valid JSON value. */
  model_detail?: PromptConfigurationApiModelDetail;
  voice_id?: string;
  [key: string]: unknown;
 }

/**
 * Plain text string or array of content-part objects.
 */
export type MessageItemApiContent = string | unknown[];

/**
 * Any valid JSON value.
 */
export type MessageItemApiToolCalls = { [key: string]: unknown };

export interface MessageItemApi {
  /** @minLength 1 */
  role: string;
  /** Plain text string or array of content-part objects. */
  content: MessageItemApiContent;
  /** @minLength 1 */
  name?: string;
  /** Any valid JSON value. */
  tool_calls?: MessageItemApiToolCalls;
  /** @minLength 1 */
  tool_call_id?: string;
  /** @minLength 1 */
  id?: string;
  [key: string]: unknown;
 }

/**
 * String or JSON object.
 */
export type PromptConfigEntryApiModel = string | { [key: string]: unknown };

export interface PromptConfigEntryApi {
  id?: string;
  name?: string;
  prompt_id?: string;
  prompt_version?: string;
  agent_id?: string;
  agent_version?: string;
  /** String or JSON object. */
  model?: PromptConfigEntryApiModel;
  model_params?: PromptModelParamsApi;
  configuration?: PromptConfigurationApi;
  /** @minLength 1 */
  output_format?: string;
  messages?: MessageItemApi[];
  voice_input_column_id?: string;
}

export type EvalMetricEntryApiConfig = { [key: string]: unknown };

export type EvalMetricEntryApiCompositeWeightOverrides = { [key: string]: unknown };

export interface EvalMetricEntryApi {
  id?: string;
  template_id: string;
  /**
     * @minLength 1
     * @maxLength 2000
     */
  name: string;
  config: EvalMetricEntryApiConfig;
  /** @maxLength 255 */
  model?: string;
  error_localizer?: boolean;
  kb_id?: string;
  composite_weight_overrides?: EvalMetricEntryApiCompositeWeightOverrides;
}

export interface ExperimentCreateV2Api {
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  dataset_id: string;
  column_id?: string;
  experiment_type?: ExperimentCreateV2ApiExperimentType;
  prompt_config: PromptConfigEntryApi[];
  user_eval_metrics: EvalMetricEntryApi[];
}

export type ExperimentListV2ApiStatus = typeof ExperimentListV2ApiStatus[keyof typeof ExperimentListV2ApiStatus];


export const ExperimentListV2ApiStatus = {
  NotStarted: 'NotStarted',
  Queued: 'Queued',
  Running: 'Running',
  Completed: 'Completed',
  Editing: 'Editing',
  Inactive: 'Inactive',
  Failed: 'Failed',
  PartialRun: 'PartialRun',
  ExperimentEvaluation: 'ExperimentEvaluation',
  Uploading: 'Uploading',
  PartialExtracted: 'PartialExtracted',
  Processing: 'Processing',
  Deleting: 'Deleting',
  PartialCompleted: 'PartialCompleted',
  OptimizationEvaluation: 'OptimizationEvaluation',
  Error: 'Error',
  Cancelled: 'Cancelled',
} as const;

/**
 * Determines how the experiment executes: llm, tts, stt, or image.
 */
export type ExperimentListV2ApiExperimentType = typeof ExperimentListV2ApiExperimentType[keyof typeof ExperimentListV2ApiExperimentType];


export const ExperimentListV2ApiExperimentType = {
  llm: 'llm',
  tts: 'tts',
  stt: 'stt',
  image: 'image',
} as const;

export interface ExperimentListV2Api {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  status?: ExperimentListV2ApiStatus;
  /** Determines how the experiment executes: llm, tts, stt, or image. */
  experiment_type?: ExperimentListV2ApiExperimentType;
  readonly eval_templates_count?: string;
  readonly created_at?: string;
  readonly models_count?: string;
  readonly agents_count?: string;
  dataset: string;
}

export interface ExperimentNameSuggestionResultApi {
  /** @minLength 1 */
  suggested_name: string;
}

export interface ExperimentNameSuggestionResponseApi {
  status: boolean;
  result: ExperimentNameSuggestionResultApi;
}

export interface ExperimentNameValidationResultApi {
  is_valid: boolean;
  message?: string;
}

export interface ExperimentNameValidationResponseApi {
  status: boolean;
  result: ExperimentNameValidationResultApi;
}

/**
 * Determines how the experiment executes: llm, tts, stt, or image.
 */
export type ExperimentDetailV2ApiExperimentType = typeof ExperimentDetailV2ApiExperimentType[keyof typeof ExperimentDetailV2ApiExperimentType];


export const ExperimentDetailV2ApiExperimentType = {
  llm: 'llm',
  tts: 'tts',
  stt: 'stt',
  image: 'image',
} as const;

export type ExperimentDetailV2ApiStatus = typeof ExperimentDetailV2ApiStatus[keyof typeof ExperimentDetailV2ApiStatus];


export const ExperimentDetailV2ApiStatus = {
  NotStarted: 'NotStarted',
  Queued: 'Queued',
  Running: 'Running',
  Completed: 'Completed',
  Editing: 'Editing',
  Inactive: 'Inactive',
  Failed: 'Failed',
  PartialRun: 'PartialRun',
  ExperimentEvaluation: 'ExperimentEvaluation',
  Uploading: 'Uploading',
  PartialExtracted: 'PartialExtracted',
  Processing: 'Processing',
  Deleting: 'Deleting',
  PartialCompleted: 'PartialCompleted',
  OptimizationEvaluation: 'OptimizationEvaluation',
  Error: 'Error',
  Cancelled: 'Cancelled',
} as const;

export interface ExperimentDetailV2Api {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  readonly dataset_id?: string;
  readonly column_id?: string;
  /** Determines how the experiment executes: llm, tts, stt, or image. */
  experiment_type?: ExperimentDetailV2ApiExperimentType;
  status?: ExperimentDetailV2ApiStatus;
  readonly snapshot_dataset_id?: string;
  readonly prompt_configs?: string;
  readonly agent_configs?: string;
  readonly user_eval_metrics?: string;
  readonly created_at?: string;
}

export interface ExperimentV2DetailResponseApi {
  status: boolean;
  result: ExperimentDetailV2Api;
}

export interface ExperimentUpdateV2Api {
  column_id?: string;
  prompt_config?: PromptConfigEntryApi[];
  user_eval_metrics?: EvalMetricEntryApi[];
}

export type ExperimentComparisonWeightsRequestApiWeights = { [key: string]: unknown };

export interface ExperimentComparisonWeightsRequestApi {
  eval_template_ids?: string[];
  weights?: ExperimentComparisonWeightsRequestApiWeights;
}

export type ExperimentComparisonColumnMetricApiAvgScore = { [key: string]: unknown };

export interface ExperimentComparisonColumnMetricApi {
  column_id: string;
  /** @minLength 1 */
  column_name: string;
  avg_completion_tokens: number;
  avg_total_tokens: number;
  avg_response_time: number;
  avg_score?: ExperimentComparisonColumnMetricApiAvgScore;
}

export type ExperimentComparisonDatasetMetricApiNormalizedScores = { [key: string]: unknown };

export interface ExperimentComparisonDatasetMetricApi {
  dataset_id: string;
  avg_completion_tokens?: number;
  avg_total_tokens?: number;
  avg_response_time?: number;
  avg_score?: number;
  columns?: ExperimentComparisonColumnMetricApi[];
  normalized_scores?: ExperimentComparisonDatasetMetricApiNormalizedScores;
  overall_rating?: number;
  rank?: number;
  rank_suffix?: string;
  total_datasets?: number;
}

export type ExperimentDatasetComparisonResultApiWeightsApplied = { [key: string]: unknown };

export interface ExperimentDatasetComparisonResultApi {
  experiment_id: string;
  /** @minLength 1 */
  experiment_name: string;
  total_datasets: number;
  weights_applied?: ExperimentDatasetComparisonResultApiWeightsApplied;
  dataset_comparisons: ExperimentComparisonDatasetMetricApi[];
}

export interface ExperimentDatasetComparisonResponseApi {
  status: boolean;
  result: ExperimentDatasetComparisonResultApi;
}

export interface ExperimentComparisonRawMetricsApi {
  avg_completion_tokens?: number;
  avg_total_tokens?: number;
  avg_response_time?: number;
  avg_score?: number;
}

export interface ExperimentComparisonNormalizedMetricsApi {
  completion_tokens?: number;
  total_tokens?: number;
  response_time?: number;
  score?: number;
}

export interface ExperimentComparisonMetricsApi {
  raw: ExperimentComparisonRawMetricsApi;
  normalized: ExperimentComparisonNormalizedMetricsApi;
}

export type ExperimentComparisonWeightsApiScores = { [key: string]: unknown };

export interface ExperimentComparisonWeightsApi {
  response_time?: number;
  scores?: ExperimentComparisonWeightsApiScores;
  total_tokens?: number;
  completion_tokens?: number;
}

export type ExperimentComparisonDetailApiScoresWeight = { [key: string]: unknown };

export interface ExperimentComparisonDetailApi {
  scores_weight?: ExperimentComparisonDetailApiScoresWeight;
  experiment_dataset_id?: string;
  rank?: number;
  rank_suffix?: string;
  metrics: ExperimentComparisonMetricsApi;
  weights: ExperimentComparisonWeightsApi;
  overall_rating?: number;
}

export interface ExperimentComparisonDetailsResultApi {
  experiment_id: string;
  total_comparisons: number;
  comparisons: ExperimentComparisonDetailApi[];
}

export interface ExperimentComparisonDetailsResponseApi {
  status: boolean;
  result: ExperimentComparisonDetailsResultApi;
}

export type ExperimentDerivedVariablesResultApiDerivedVariables = {[key: string]: string[]};

export interface ExperimentDerivedVariablesResultApi {
  version?: string;
  derived_variables?: ExperimentDerivedVariablesResultApiDerivedVariables;
}

export interface ExperimentDerivedVariablesResponseApi {
  status: boolean;
  result: ExperimentDerivedVariablesResultApi;
}

export interface ExperimentEvaluationTokenUsageApi {
  avg_completion_tokens: number;
  avg_prompt_tokens: number;
  avg_total_tokens: number;
  total_tokens: number;
}

export type ExperimentEvaluationColumnStatsApiAvgScore = { [key: string]: unknown };

export interface ExperimentEvaluationColumnStatsApi {
  /** @minLength 1 */
  column_name: string;
  column_id: string;
  total_rows: number;
  success_rate: number;
  avg_response_time: number;
  token_usage: ExperimentEvaluationTokenUsageApi;
  avg_score?: ExperimentEvaluationColumnStatsApiAvgScore;
}

export interface ExperimentEvaluationStatsResultApi {
  experiment_id: string;
  /** @minLength 1 */
  experiment_name: string;
  evaluation_id: string;
  /** @minLength 1 */
  evaluation_name: string;
  evaluation_template_id: string;
  dataset_id: string;
  /** @minLength 1 */
  dataset_name: string;
  evaluation_columns: ExperimentEvaluationColumnStatsApi[];
}

export interface ExperimentEvaluationStatsResponseApi {
  status: boolean;
  result: ExperimentEvaluationStatsResultApi;
}

export type FeedbackApiSource = typeof FeedbackApiSource[keyof typeof FeedbackApiSource];


export const FeedbackApiSource = {
  dataset: 'dataset',
  prompt: 'prompt',
  sdk: 'sdk',
  trace: 'trace',
  experiment: 'experiment',
  observe: 'observe',
  eval_playground: 'eval_playground',
} as const;

export interface FeedbackApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  source_id: string;
  source: FeedbackApiSource;
  user_eval_metric?: string;
  /** @minLength 1 */
  value: string;
  explanation?: string;
  /** @maxLength 255 */
  row_id?: string;
  custom_eval_config_id?: string;
  feedback_improvement?: string;
  /** @maxLength 255 */
  action_type?: string;
}

export interface ExperimentFeedbackCreateResultApi {
  id: string;
}

export interface ExperimentFeedbackCreateResponseApi {
  status: boolean;
  result: ExperimentFeedbackCreateResultApi;
}

export type ExperimentFeedbackDetailItemApiValue = { [key: string]: unknown };

export interface ExperimentFeedbackDetailItemApi {
  id: string;
  value?: ExperimentFeedbackDetailItemApiValue;
  comment?: string;
  created_at: string;
  action_type?: string;
}

export interface ExperimentFeedbackDetailsResultApi {
  feedback: ExperimentFeedbackDetailItemApi[];
  total_count: number;
}

export interface ExperimentFeedbackDetailsResponseApi {
  status: boolean;
  result: ExperimentFeedbackDetailsResultApi;
}

export interface ExperimentFeedbackTemplateResultApi {
  /** @minLength 1 */
  output_type?: string;
  eval_description?: string;
  /** @minLength 1 */
  eval_name: string;
  /** @minLength 1 */
  user_eval_name: string;
  choices?: string[];
  multi_choice?: boolean;
}

export interface ExperimentFeedbackTemplateResponseApi {
  status: boolean;
  result: ExperimentFeedbackTemplateResultApi;
}

export type ExperimentFeedbackSubmitRequestApiActionType = typeof ExperimentFeedbackSubmitRequestApiActionType[keyof typeof ExperimentFeedbackSubmitRequestApiActionType];


export const ExperimentFeedbackSubmitRequestApiActionType = {
  retune: 'retune',
  recalculate_row: 'recalculate_row',
  recalculate_dataset: 'recalculate_dataset',
  retune_recalculate: 'retune_recalculate',
} as const;

export interface ExperimentFeedbackSubmitRequestApi {
  action_type: ExperimentFeedbackSubmitRequestApiActionType;
  feedback_id: string;
  user_eval_metric_id: string;
  value?: string;
  explanation?: string;
}

export interface ExperimentFeedbackSubmitResultApi {
  /** @minLength 1 */
  message: string;
  /** @minLength 1 */
  action_type: string;
  user_eval_metric_id: string;
  workflow_id?: string;
}

export interface ExperimentFeedbackSubmitResponseApi {
  status: boolean;
  result: ExperimentFeedbackSubmitResultApi;
}

export type ExperimentJsonSchemaResponseApiResult = {[key: string]: JsonColumnSchemaEntryApi};

export interface ExperimentJsonSchemaResponseApi {
  status: boolean;
  result: ExperimentJsonSchemaResponseApiResult;
}

export interface RerunCellEntryApi {
  column_id: string;
  row_id: string;
}

export interface ExperimentRerunCellsApi {
  source_ids?: string[];
  cells?: RerunCellEntryApi[];
  user_eval_metric_ids?: string[];
  failed_only?: boolean;
  /** @minimum 1 */
  max_concurrent_rows?: number;
}

export interface ExperimentWorkflowResultApi {
  /** @minLength 1 */
  message: string;
  workflow_id?: string;
}

export interface ExperimentWorkflowResponseApi {
  status: boolean;
  result: ExperimentWorkflowResultApi;
}

export type ExperimentTableRowsColumnConfigApiGroup = { [key: string]: unknown };

export type ExperimentTableRowsColumnConfigApiAverageScore = { [key: string]: unknown };

export type ExperimentTableRowsColumnConfigApiChoicesMap = { [key: string]: unknown };

export interface ExperimentTableRowsColumnConfigApi {
  /** @minLength 1 */
  id: string;
  /** @minLength 1 */
  name: string;
  origin_type?: string;
  data_type?: string;
  status?: string;
  group?: ExperimentTableRowsColumnConfigApiGroup;
  average_score?: ExperimentTableRowsColumnConfigApiAverageScore;
  dataset_id?: string;
  choices_map?: ExperimentTableRowsColumnConfigApiChoicesMap;
  is_base_column?: boolean;
  output_type?: string;
  eval_template_id?: string;
  source_id?: string;
  is_agent?: boolean;
  is_final?: boolean;
}

export interface ExperimentTableRowApi {
  row_id: string;
}

export type ExperimentTableRowsMetadataApiDescription = {[key: string]: string};

export interface ExperimentTableRowsMetadataApi {
  total_rows?: number;
  dataset?: string;
  dataset_name?: string;
  column?: string;
  total_pages?: number;
  description?: ExperimentTableRowsMetadataApiDescription;
}

export interface ExperimentTableRowsResultApi {
  column_config: ExperimentTableRowsColumnConfigApi[];
  table?: ExperimentTableRowApi[];
  metadata?: ExperimentTableRowsMetadataApi;
  output_format?: string;
  status?: string;
  next_row_ids?: string[];
}

export interface ExperimentTableRowsResponseApi {
  status: boolean;
  result: ExperimentTableRowsResultApi;
}

export interface ExperimentStatsColumnConfigApi {
  status?: string;
  /** @minLength 1 */
  name: string;
  reverse_output?: boolean;
  output_type?: string;
  eval_template_id?: string;
}

export interface ExperimentStatsMetadataApi {
  is_winner_chosen: boolean;
}

export type ExperimentStatsResultApiTableDataItem = { [key: string]: unknown };

export interface ExperimentStatsResultApi {
  column_config: ExperimentStatsColumnConfigApi[];
  table_data: ExperimentStatsResultApiTableDataItem[];
  metadata: ExperimentStatsMetadataApi;
}

export interface ExperimentStatsResponseApi {
  status: boolean;
  result: ExperimentStatsResultApi;
}

export interface ExperimentStopWorkflowsCancelledApi {
  main: boolean;
  reruns: boolean;
}

export interface ExperimentStopResultApi {
  /** @minLength 1 */
  message: string;
  experiment_id: string;
  workflows_cancelled: ExperimentStopWorkflowsCancelledApi;
}

export interface ExperimentStopResponseApi {
  status: boolean;
  result: ExperimentStopResultApi;
}

export interface ExperimentAddEvalResultApi {
  /** @minLength 1 */
  message: string;
  eval_id: string;
}

export interface ExperimentAddEvalResponseApi {
  status: boolean;
  result: ExperimentAddEvalResultApi;
}

export interface ExperimentAdditionalEvaluationsRequestApi {
  eval_template_ids: string[];
}

export interface ExperimentMessageResultApi {
  /** @minLength 1 */
  message: string;
}

export interface ExperimentMessageResponseApi {
  status: boolean;
  result: ExperimentMessageResultApi;
}

export interface FeedbackDetailsItemApi {
  id: string;
  value: string;
  comment: string;
  /** @minLength 1 */
  created_at: string;
  action_type: string;
}

export interface FeedbackDetailsResultApi {
  feedback: FeedbackDetailsItemApi[];
  total_count: number;
}

export interface FeedbackDetailsResponseApi {
  status: boolean;
  result: FeedbackDetailsResultApi;
}

export type ColumnValuesRequestApiColumnPlaceholders = { [key: string]: unknown };

export interface ColumnValuesRequestApi {
  dataset_id: string;
  column_placeholders: ColumnValuesRequestApiColumnPlaceholders;
}

export interface ColumnValuesItemApi {
  column_id: string;
  /** @minLength 1 */
  column_name: string;
  values: string[];
}

export type ColumnValuesResponseResultApiResult = {[key: string]: ColumnValuesItemApi};

export interface ColumnValuesResponseResultApi {
  result: ColumnValuesResponseResultApiResult;
}

export interface ColumnValuesResponseApi {
  status: boolean;
  result: ColumnValuesResponseResultApi;
}

export type EvalConfigApiEvalTypeId = { [key: string]: unknown };

export type EvalConfigApiReasonColumn = { [key: string]: unknown };

export type EvalConfigApiModels = { [key: string]: unknown };

export type EvalConfigApiMapping = { [key: string]: unknown };

export type EvalConfigApiConfig = { [key: string]: unknown };

export type EvalConfigApiParams = { [key: string]: unknown };

export type EvalConfigApiFunctionParamsSchema = { [key: string]: unknown };

export type EvalConfigApiConfigParamsDesc = { [key: string]: unknown };

export type EvalConfigApiConfigParamsOption = { [key: string]: unknown };

export type EvalConfigApiParamModalities = { [key: string]: unknown };

export type EvalConfigApiChoices = { [key: string]: unknown };

export type EvalConfigApiKbId = { [key: string]: unknown };

export type EvalConfigApiRunConfig = { [key: string]: unknown };

export interface EvalConfigApi {
  id: string;
  template_id: string;
  /** @minLength 1 */
  name: string;
  /** @minLength 1 */
  owner?: string;
  /** @minLength 1 */
  type?: string;
  /** @minLength 1 */
  eval_type?: string;
  eval_type_id?: EvalConfigApiEvalTypeId;
  function_eval?: boolean;
  reason_column?: EvalConfigApiReasonColumn;
  eval_tags?: string[];
  description?: string;
  criteria?: string;
  model?: string;
  models?: EvalConfigApiModels;
  selected_model?: string;
  required_keys: string[];
  optional_keys?: string[];
  variable_keys?: string[];
  run_prompt_column?: boolean;
  /** @minLength 1 */
  template_name: string;
  mapping: EvalConfigApiMapping;
  config: EvalConfigApiConfig;
  params?: EvalConfigApiParams;
  function_params_schema?: EvalConfigApiFunctionParamsSchema;
  output?: string;
  config_params_desc?: EvalConfigApiConfigParamsDesc;
  config_params_option?: EvalConfigApiConfigParamsOption;
  param_modalities?: EvalConfigApiParamModalities;
  choices?: EvalConfigApiChoices;
  check_internet?: boolean;
  kb_id?: EvalConfigApiKbId;
  error_localizer?: boolean;
  api_key_available?: boolean;
  run_config?: EvalConfigApiRunConfig;
}

export interface ModelHubEvalConfigResponseResultApi {
  eval: EvalConfigApi;
  /** @minLength 1 */
  owner?: string;
  /** @minLength 1 */
  type?: string;
}

export interface ModelHubEvalConfigResponseApi {
  status: boolean;
  result: ModelHubEvalConfigResponseResultApi;
}

export type EvalApiLogRowResponseResultApiValues = { [key: string]: unknown };

export type EvalApiLogRowResponseResultApiOutput = { [key: string]: unknown };

export type EvalApiLogRowResponseResultApiInputDataTypes = { [key: string]: unknown };

export type EvalApiLogRowResponseResultApiErrorDetails = { [key: string]: unknown };

export interface EvalApiLogRowResponseResultApi {
  log_id: string;
  created_at: string;
  evaluation_id: string;
  source?: string;
  required_keys: string[];
  values: EvalApiLogRowResponseResultApiValues;
  output: EvalApiLogRowResponseResultApiOutput;
  input_data_types: EvalApiLogRowResponseResultApiInputDataTypes;
  error_details?: EvalApiLogRowResponseResultApiErrorDetails;
  error_localizer_status?: string;
  error_localizer_message?: string;
  dataset_id?: string;
  span_id?: string;
  trace_id?: string;
  prompt_id?: string;
  optimization_id?: string;
  experiment_id?: string;
}

export interface EvalApiLogRowResponseApi {
  status: boolean;
  result: EvalApiLogRowResponseResultApi;
}

export type UpdateColumnConfigApiColumnConfigItem = {[key: string]: string};

export interface UpdateColumnConfigApi {
  eval_id: string;
  column_config?: UpdateColumnConfigApiColumnConfigItem[];
  /**
     * @minLength 1
     * @maxLength 50
     */
  source?: string;
}

export interface EvalApiLogTableMetadataApi {
  total_rows: number;
  total_pages: number;
}

export type EvalApiLogTableResponseResultApiTableItem = { [key: string]: unknown };

export type EvalApiLogTableResponseResultApiColumnConfigItem = { [key: string]: unknown };

export interface EvalApiLogTableResponseResultApi {
  table: EvalApiLogTableResponseResultApiTableItem[];
  column_config: EvalApiLogTableResponseResultApiColumnConfigItem[];
  metadata?: EvalApiLogTableMetadataApi;
}

export interface EvalApiLogTableResponseApi {
  status: boolean;
  result: EvalApiLogTableResponseResultApi;
}

export type EvalMetricCountApiCountGraphData = { [key: string]: unknown };

export interface EvalMetricCountApi {
  api_call_count: number;
  count_graph_data?: EvalMetricCountApiCountGraphData;
}

export type EvalMetricAverageApiAverage = { [key: string]: unknown };

export type EvalMetricAverageApiAvgGraphData = { [key: string]: unknown };

export interface EvalMetricAverageApi {
  average: EvalMetricAverageApiAverage;
  avg_graph_data?: EvalMetricAverageApiAvgGraphData;
}

export type EvalMetricResponseResultApiErrorRate = { [key: string]: unknown };

export interface EvalMetricResponseResultApi {
  base_eval_template_id: string;
  api_call_count: EvalMetricCountApi;
  average: EvalMetricAverageApi;
  error_rate?: EvalMetricResponseResultApiErrorRate;
}

export interface EvalMetricResponseApi {
  status: boolean;
  result: EvalMetricResponseResultApi;
}

export type EvalMetricRequestApiFiltersItemFilterConfig = {
  /** Canonical field type, for example text, number, boolean, datetime, categorical, thumbs, annotator, or array. */
  filter_type: string;
  /** Canonical operator from api_contracts/filter_contract.json, for example equals, not_equals, in, not_in, between, not_between, is_null, or is_not_null. */
  filter_op: string;
  /** Scalar, list, range tuple, boolean, or null depending on filter_op and filter_type. */
  filter_value?: unknown;
  /** Column family such as SYSTEM_METRIC, SPAN_ATTRIBUTE, EVAL_METRIC, ANNOTATION, or NORMAL. */
  col_type?: string;
};

export type EvalMetricRequestApiFiltersItem = {
  /** Column or attribute id to filter on. */
  column_id: string;
  /** Optional UI label for chips and saved views. */
  display_name?: string;
  /** Optional source surface for mixed-source filters, for example traces, datasets, or simulation. */
  source?: string;
  /** Optional metric output type metadata used by eval and annotation filters. */
  output_type?: string;
  filter_config: EvalMetricRequestApiFiltersItemFilterConfig;
};

export interface EvalMetricRequestApi {
  eval_template_id: string;
  filters?: EvalMetricRequestApiFiltersItem[];
}

export interface EvalTemplateNamesRequestApi {
  search_text?: string;
}

export interface EvalTemplateNameItemApi {
  id: string;
  /** @minLength 1 */
  name: string;
  description?: string;
}

export interface EvalTemplateNamesResponseApi {
  status: boolean;
  result: EvalTemplateNameItemApi[];
}

export type LegacyEvalTemplatesRequestApiSortItem = { [key: string]: unknown };

export interface LegacyEvalTemplatesRequestApi {
  page_size?: number;
  current_page_index?: number;
  search_text?: string;
  sort?: LegacyEvalTemplatesRequestApiSortItem[];
}

export type LegacyEvalTemplateAverageApiAverage = { [key: string]: unknown };

export type LegacyEvalTemplateAverageApiAvgGraphDataItem = { [key: string]: unknown };

export interface LegacyEvalTemplateAverageApi {
  average?: LegacyEvalTemplateAverageApiAverage;
  avg_graph_data: LegacyEvalTemplateAverageApiAvgGraphDataItem[];
}

export type LegacyEvalTemplateItemApiErrorRateItem = { [key: string]: unknown };

export interface LegacyEvalTemplateItemApi {
  id: string;
  max_axis?: number;
  /** @minLength 1 */
  eval_template_name: string;
  average: LegacyEvalTemplateAverageApi;
  error_rate: LegacyEvalTemplateItemApiErrorRateItem[];
  last30_run: number;
  /** @minLength 1 */
  updated_at: string;
}

export interface LegacyEvalTemplatesResponseResultApi {
  row_data: LegacyEvalTemplateItemApi[];
  total_rows: number;
  data_available: boolean;
}

export interface LegacyEvalTemplatesResponseApi {
  status: boolean;
  result: LegacyEvalTemplatesResponseResultApi;
}

export interface GroundTruthDeleteResponseResultApi {
  deleted: boolean;
  id: string;
}

export interface GroundTruthDeleteResponseApi {
  status: boolean;
  result: GroundTruthDeleteResponseResultApi;
}

export type GroundTruthDataResponseResultApiRowsItem = { [key: string]: unknown };

export interface GroundTruthDataResponseResultApi {
  id: string;
  page: number;
  page_size: number;
  total_rows: number;
  total_pages: number;
  columns: string[];
  rows: GroundTruthDataResponseResultApiRowsItem[];
}

export interface GroundTruthDataResponseApi {
  status: boolean;
  result: GroundTruthDataResponseResultApi;
}

export interface GroundTruthEmbedResponseResultApi {
  id: string;
  /** @minLength 1 */
  embedding_status: string;
  /** @minLength 1 */
  message: string;
}

export interface GroundTruthEmbedResponseApi {
  status: boolean;
  result: GroundTruthEmbedResponseResultApi;
}

/**
 * Map of template variable name to GT column name (string) or list of column names. Keys are dynamic per-template.
 */
export type GroundTruthSetupRequestApiVariableMapping = { [key: string]: unknown };

export interface GroundTruthSetupRequestApi {
  /** Map of template variable name to GT column name (string) or list of column names. Keys are dynamic per-template. */
  variable_mapping: GroundTruthSetupRequestApiVariableMapping;
  role_mapping: GroundTruthRoleMappingApi;
  /**
     * @minimum 1
     * @maximum 20
     */
  max_examples: number;
  enabled?: boolean;
}

export interface GroundTruthRuntimeConfigApi {
  enabled: boolean;
  ground_truth_id: string;
  /**
     * @minimum 1
     * @maximum 20
     */
  max_examples: number;
  /**
     * @minimum 0
     * @maximum 1
     */
  similarity_threshold: number;
}

/**
 * Map of template variable name to GT column name (string) or list of column names.
 */
export type GroundTruthSetupResponseResultApiVariableMapping = { [key: string]: unknown };

export interface GroundTruthSetupResponseResultApi {
  id: string;
  template_id: string;
  /** Map of template variable name to GT column name (string) or list of column names. */
  variable_mapping?: GroundTruthSetupResponseResultApiVariableMapping;
  role_mapping?: GroundTruthRoleMappingApi;
  /** @minLength 1 */
  embedding_status: string;
  embeddings_stale?: boolean;
  config: GroundTruthRuntimeConfigApi;
}

export interface GroundTruthSetupResponseApi {
  status: boolean;
  result: GroundTruthSetupResponseResultApi;
}

export interface GroundTruthStatusResponseResultApi {
  id: string;
  /** @minLength 1 */
  embedding_status: string;
  embedded_row_count: number;
  total_rows: number;
  progress_percent: number;
  embeddings_stale?: boolean;
}

export interface GroundTruthStatusResponseApi {
  status: boolean;
  result: GroundTruthStatusResponseResultApi;
}

export interface KnowledgeBaseItemApi {
  id: string;
  /** @minLength 1 */
  name: string;
  /** @minLength 1 */
  embedding_model: string;
  chunk_size: number;
  organization: string;
  created_at: string;
  updated_at: string;
}

export interface KnowledgeBasePaginatedResultApi {
  count: number;
  next?: string;
  previous?: string;
  results: KnowledgeBaseItemApi[];
  total_pages: number;
  current_page: number;
  total_queries?: number;
}

export interface KnowledgeBaseListResponseApi {
  status: boolean;
  result: KnowledgeBasePaginatedResultApi;
}

export type KnowledgeBaseCreateApiEmbeddingModel = typeof KnowledgeBaseCreateApiEmbeddingModel[keyof typeof KnowledgeBaseCreateApiEmbeddingModel];


export const KnowledgeBaseCreateApiEmbeddingModel = {
  'BAAI/bge-small-en-v15': 'BAAI/bge-small-en-v1.5',
} as const;

export interface KnowledgeBaseCreateApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 2000
     */
  name: string;
  embedding_model?: KnowledgeBaseCreateApiEmbeddingModel;
  /**
     * @minimum 0
     * @maximum 2147483647
     */
  chunk_size: number;
  organization?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
}

export interface KnowledgeBaseResponseApi {
  status: boolean;
  result: KnowledgeBaseItemApi;
}

export interface EmbeddingModelOptionApi {
  /** @minLength 1 */
  value: string;
  /** @minLength 1 */
  label: string;
}

export interface KnowledgeBaseEmbeddingModelsResponseApi {
  status: number;
  result: EmbeddingModelOptionApi[];
}

export type KnowledgeBaseApiEmbeddingModel = typeof KnowledgeBaseApiEmbeddingModel[keyof typeof KnowledgeBaseApiEmbeddingModel];


export const KnowledgeBaseApiEmbeddingModel = {
  'BAAI/bge-small-en-v15': 'BAAI/bge-small-en-v1.5',
} as const;

export interface KnowledgeBaseApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 2000
     */
  name: string;
  embedding_model?: KnowledgeBaseApiEmbeddingModel;
  /**
     * @minimum 0
     * @maximum 2147483647
     */
  chunk_size: number;
  readonly organization?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
}

export interface LegacyKnowledgeBaseSdkCodeResultApi {
  /** @minLength 1 */
  code: string;
}

export interface LegacyKnowledgeBaseSdkCodeResponseApi {
  status: boolean;
  result: LegacyKnowledgeBaseSdkCodeResultApi;
}

export interface LegacyKnowledgeBaseMutationRequestApi {
  name?: string;
  kb_id?: string;
  files?: string[];
}

export interface LegacyKnowledgeBaseCreateResultApi {
  /** @minLength 1 */
  detail: string;
  kb_id: string;
  /** @minLength 1 */
  kb_name: string;
  file_ids: string[];
}

export interface LegacyKnowledgeBaseCreateResponseApi {
  status: boolean;
  result: LegacyKnowledgeBaseCreateResultApi;
}

export interface LegacyKnowledgeBaseMutationResultApi {
  id: string;
  /** @minLength 1 */
  name: string;
  organization: string;
  /** @minLength 1 */
  status: string;
  files: string[];
  updated_at: string;
  created_by: string;
  last_error: string;
}

export interface LegacyKnowledgeBaseMutationResponseApi {
  status: boolean;
  result: LegacyKnowledgeBaseMutationResultApi;
}

export type LegacyKnowledgeBaseFilesRequestApiSortItem = { [key: string]: unknown };

export interface LegacyKnowledgeBaseFilesRequestApi {
  kb_id: string;
  search?: string;
  sort?: LegacyKnowledgeBaseFilesRequestApiSortItem[];
  page_number?: number;
  page_size?: number;
}

export interface LegacyKnowledgeBaseFileRowApi {
  id: string;
  /** @minLength 1 */
  name: string;
  file_size: number;
  /** @minLength 1 */
  status: string;
  updated: string;
  updated_by: string;
  error?: string;
}

export interface LegacyKnowledgeBaseFilesResultApi {
  table_data: LegacyKnowledgeBaseFileRowApi[];
  last_updated: string;
  /** @minLength 1 */
  status: string;
  status_count: number;
  total_rows: number;
}

export interface LegacyKnowledgeBaseFilesResponseApi {
  status: boolean;
  result: LegacyKnowledgeBaseFilesResultApi;
}

export interface LegacyKnowledgeBaseTableColumnApi {
  /** @minLength 1 */
  id: string;
  /** @minLength 1 */
  name: string;
}

export interface LegacyKnowledgeBaseTableRowApi {
  id: string;
  /** @minLength 1 */
  name: string;
  files_uploaded: number;
  /** @minLength 1 */
  status: string;
  error?: string;
  updated_at: string;
  created_by: string;
}

export interface LegacyKnowledgeBaseTableResultApi {
  column_config?: LegacyKnowledgeBaseTableColumnApi[];
  table_data?: LegacyKnowledgeBaseTableRowApi[];
  total_rows?: number;
}

export interface LegacyKnowledgeBaseTableResponseApi {
  status: boolean;
  result: LegacyKnowledgeBaseTableResultApi;
}

export interface LegacyKnowledgeBaseOptionApi {
  id: string;
  /** @minLength 1 */
  name: string;
}

export interface LegacyKnowledgeBaseListResultApi {
  table_data: LegacyKnowledgeBaseOptionApi[];
}

export interface LegacyKnowledgeBaseListResponseApi {
  status: boolean;
  result: LegacyKnowledgeBaseListResultApi;
}

export type MetricsByColumnItemApiMapping = { [key: string]: unknown };

export type MetricsByColumnItemApiParams = { [key: string]: unknown };

export type MetricsByColumnItemApiRunConfig = { [key: string]: unknown };

export interface MetricsByColumnItemApi {
  id: string;
  /** @minLength 1 */
  name: string;
  /** @minLength 1 */
  template_name: string;
  /** @minLength 1 */
  eval_template_name: string;
  eval_required_keys: string[];
  eval_template_tags: string[];
  description?: string;
  model?: string;
  column_id?: string;
  updated_at: string;
  eval_group?: string;
  status?: string;
  /** @minLength 1 */
  eval_type: string;
  /** @minLength 1 */
  template_type: string;
  template_id: string;
  /** @minLength 1 */
  owner: string;
  mapping: MetricsByColumnItemApiMapping;
  params: MetricsByColumnItemApiParams;
  error_localizer: boolean;
  run_config: MetricsByColumnItemApiRunConfig;
  /** @minLength 1 */
  output_type: string;
  aggregation_function?: string;
  aggregation_enabled?: boolean;
  children_count?: number;
}

export interface MetricsByColumnResponseApi {
  status: boolean;
  result: MetricsByColumnItemApi[];
}

export type OptimizationDatasetApiMessagesItem = { [key: string]: unknown };

export type OptimizationDatasetApiModelConfig = { [key: string]: unknown };

export type OptimizationDatasetApiOptimizeType = typeof OptimizationDatasetApiOptimizeType[keyof typeof OptimizationDatasetApiOptimizeType];


export const OptimizationDatasetApiOptimizeType = {
  PROMPT_TEMPLATE: 'PROMPT_TEMPLATE',
  RIGHT_ANSWER: 'RIGHT_ANSWER',
  RAG_PROMPT_TEMPLATE: 'RAG_PROMPT_TEMPLATE',
} as const;

export type OptimizationDatasetApiUserEvalTemplateMapping = { [key: string]: unknown };

export type OptimizationDatasetApiStatus = typeof OptimizationDatasetApiStatus[keyof typeof OptimizationDatasetApiStatus];


export const OptimizationDatasetApiStatus = {
  NotStarted: 'NotStarted',
  Queued: 'Queued',
  Running: 'Running',
  Completed: 'Completed',
  Editing: 'Editing',
  Inactive: 'Inactive',
  Failed: 'Failed',
  PartialRun: 'PartialRun',
  ExperimentEvaluation: 'ExperimentEvaluation',
  Uploading: 'Uploading',
  PartialExtracted: 'PartialExtracted',
  Processing: 'Processing',
  Deleting: 'Deleting',
  PartialCompleted: 'PartialCompleted',
  OptimizationEvaluation: 'OptimizationEvaluation',
  Error: 'Error',
  Cancelled: 'Cancelled',
} as const;

export interface OptimizationDatasetApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 2000
     */
  name: string;
  dataset_id: string;
  column_id?: string;
  /** List of messages with format [{'role': 'user/assistant', 'content': 'text'}] */
  messages?: OptimizationDatasetApiMessagesItem[];
  user_eval_template_ids?: string[];
  model_config: OptimizationDatasetApiModelConfig;
  optimize_type: OptimizationDatasetApiOptimizeType;
  user_eval_template_mapping?: OptimizationDatasetApiUserEvalTemplateMapping;
  /** @maxLength 2000 */
  prompt_name?: string;
  readonly created_at?: string;
  status?: OptimizationDatasetApiStatus;
}

export type OptimizationDatasetGetApiMessagesItem = { [key: string]: unknown };

export type OptimizationDatasetGetApiModelConfig = { [key: string]: unknown };

export type OptimizationDatasetGetApiOptimizeType = typeof OptimizationDatasetGetApiOptimizeType[keyof typeof OptimizationDatasetGetApiOptimizeType];


export const OptimizationDatasetGetApiOptimizeType = {
  PROMPT_TEMPLATE: 'PROMPT_TEMPLATE',
  RIGHT_ANSWER: 'RIGHT_ANSWER',
  RAG_PROMPT_TEMPLATE: 'RAG_PROMPT_TEMPLATE',
} as const;

export type OptimizationDatasetGetApiStatus = typeof OptimizationDatasetGetApiStatus[keyof typeof OptimizationDatasetGetApiStatus];


export const OptimizationDatasetGetApiStatus = {
  NotStarted: 'NotStarted',
  Queued: 'Queued',
  Running: 'Running',
  Completed: 'Completed',
  Editing: 'Editing',
  Inactive: 'Inactive',
  Failed: 'Failed',
  PartialRun: 'PartialRun',
  ExperimentEvaluation: 'ExperimentEvaluation',
  Uploading: 'Uploading',
  PartialExtracted: 'PartialExtracted',
  Processing: 'Processing',
  Deleting: 'Deleting',
  PartialCompleted: 'PartialCompleted',
  OptimizationEvaluation: 'OptimizationEvaluation',
  Error: 'Error',
  Cancelled: 'Cancelled',
} as const;

export type OptimizationDatasetGetApiUserEvalTemplateMapping = { [key: string]: unknown };

export interface OptimizationDatasetGetApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 2000
     */
  name: string;
  dataset: string;
  column?: string;
  /** List of messages with format [{'role': 'user/assistant', 'content': 'text'}] */
  messages?: OptimizationDatasetGetApiMessagesItem[];
  user_eval_template_ids?: string[];
  model_config?: OptimizationDatasetGetApiModelConfig;
  optimize_type: OptimizationDatasetGetApiOptimizeType;
  readonly status?: OptimizationDatasetGetApiStatus;
  readonly created_at?: string;
  optimized_k_prompts?: string[];
  user_eval_template_mapping?: OptimizationDatasetGetApiUserEvalTemplateMapping;
  /** @maxLength 2000 */
  prompt_name?: string;
}

export type OptimizationDetailApiUserEvalTemplateMapping = { [key: string]: unknown };

export interface OptimizationDetailApi {
  readonly id?: string;
  readonly created_at?: string;
  readonly optimized_k_prompts?: string;
  readonly user_eval_template_ids?: string;
  user_eval_template_mapping?: OptimizationDetailApiUserEvalTemplateMapping;
  readonly optimized_columns?: string;
  readonly evaluation_columns?: string;
}

export type OptimizeDatasetKbApiKnowledgeBaseMetrics = { [key: string]: unknown };

export type OptimizeDatasetKbApiKnowledgeBaseFilters = { [key: string]: unknown };

export type OptimizeDatasetKbApiVariables = { [key: string]: unknown };

export type OptimizeDatasetKbApiStatus = typeof OptimizeDatasetKbApiStatus[keyof typeof OptimizeDatasetKbApiStatus];


export const OptimizeDatasetKbApiStatus = {
  not_started: 'not_started',
  pending: 'pending',
  running: 'running',
  completed: 'completed',
  failed: 'failed',
  cancelled: 'cancelled',
} as const;

export interface OptimizeDatasetKbApi {
  readonly id?: string;
  knowledge_base_metrics?: OptimizeDatasetKbApiKnowledgeBaseMetrics;
  knowledge_base_filters?: OptimizeDatasetKbApiKnowledgeBaseFilters;
  optimized_k_prompts?: string[];
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  /** @maxLength 2000 */
  prompt?: string;
  variables?: OptimizeDatasetKbApiVariables;
  status?: OptimizeDatasetKbApiStatus;
}

export type OptimizeDatasetKnowledgeBaseDetailResultApiKnowledgeBaseMetrics = { [key: string]: unknown };

export type OptimizeDatasetKnowledgeBaseDetailResultApiVariables = { [key: string]: unknown };

export interface OptimizeDatasetKnowledgeBaseDetailResultApi {
  /** @minLength 1 */
  name: string;
  prompt: string;
  knowledge_base_filters: string[];
  knowledge_base_metrics: OptimizeDatasetKnowledgeBaseDetailResultApiKnowledgeBaseMetrics;
  variables: OptimizeDatasetKnowledgeBaseDetailResultApiVariables;
  /** @minLength 1 */
  status: string;
  optimized_k_prompts: string[];
}

export interface OptimizeDatasetKnowledgeBaseDetailResponseApi {
  status: boolean;
  result: OptimizeDatasetKnowledgeBaseDetailResultApi;
}

export type OptimizeDatasetKnowledgeBaseRequestApiKnowledgeBaseMetrics = { [key: string]: unknown };

export type OptimizeDatasetKnowledgeBaseRequestApiVariables = { [key: string]: unknown };

export interface OptimizeDatasetKnowledgeBaseRequestApi {
  name?: string;
  knowledge_base_metrics?: OptimizeDatasetKnowledgeBaseRequestApiKnowledgeBaseMetrics;
  knowledge_base_filters?: string[];
  prompt?: string;
  variables?: OptimizeDatasetKnowledgeBaseRequestApiVariables;
}

export interface OptimizeDatasetKnowledgeBaseCreateResponseApi {
  status: boolean;
  result: string;
}

export type OptimizeDatasetApiOptimizeType = typeof OptimizeDatasetApiOptimizeType[keyof typeof OptimizeDatasetApiOptimizeType];


export const OptimizeDatasetApiOptimizeType = {
  PromptTemplate: 'PromptTemplate',
  RightAnswer: 'RightAnswer',
  RagPromptTemplate: 'RagPromptTemplate',
} as const;

export type OptimizeDatasetApiEnvironment = typeof OptimizeDatasetApiEnvironment[keyof typeof OptimizeDatasetApiEnvironment];


export const OptimizeDatasetApiEnvironment = {
  Production: 'Production',
  Training: 'Training',
  Validation: 'Validation',
  Corpus: 'Corpus',
} as const;

export type OptimizeDatasetApiStatus = typeof OptimizeDatasetApiStatus[keyof typeof OptimizeDatasetApiStatus];


export const OptimizeDatasetApiStatus = {
  not_started: 'not_started',
  pending: 'pending',
  running: 'running',
  completed: 'completed',
  failed: 'failed',
  cancelled: 'cancelled',
} as const;

export interface MetricSerializerNameAndIdApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 100
     */
  name: string;
}

export interface OptimizeDatasetApi {
  readonly id?: string;
  readonly created_at?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  optimize_type: OptimizeDatasetApiOptimizeType;
  environment: OptimizeDatasetApiEnvironment;
  /**
     * @minLength 1
     * @maxLength 255
     */
  version: string;
  status?: OptimizeDatasetApiStatus;
  metrics: MetricSerializerNameAndIdApi[];
  start_date?: string;
  end_date?: string;
}

export interface OptimizeDatasetPaginatedResponseApi {
  count: number;
  next?: string;
  previous?: string;
  results: OptimizeDatasetApi[];
  total_pages?: number;
  current_page?: number;
}

export type OptimizeDatasetMutationRequestApiVariables = { [key: string]: unknown };

export interface OptimizeDatasetMutationRequestApi {
  name: string;
  start_date: string;
  end_date: string;
  model: string;
  optimize_type: string;
  environment: string;
  version: string;
  metrics: string[];
  prompt?: string;
  variables?: OptimizeDatasetMutationRequestApiVariables;
}

export interface OptimizeDatasetCreateDataApi {
  id: string;
}

export interface OptimizeDatasetCreateResponseApi {
  /** @minLength 1 */
  status: string;
  /** @minLength 1 */
  message: string;
  data: OptimizeDatasetCreateDataApi;
}

export type OptimizeDatasetColumnConfigResponseApiColumnsItem = { [key: string]: unknown };

export interface OptimizeDatasetColumnConfigResponseApi {
  columns: OptimizeDatasetColumnConfigResponseApiColumnsItem[];
  /** @minLength 1 */
  status: string;
}

export type OptimizeDatasetColumnConfigUpdateRequestApiColumnsItem = { [key: string]: unknown };

export interface OptimizeDatasetColumnConfigUpdateRequestApi {
  columns: OptimizeDatasetColumnConfigUpdateRequestApiColumnsItem[];
}

export interface OptimizeDatasetColumnConfigUpdateResponseApi {
  /** @minLength 1 */
  message: string;
  /** @minLength 1 */
  status: string;
}

export interface OptimizeDatasetPageRequestApi {
  /** @minimum 1 */
  page?: number;
  /** @minimum 1 */
  limit?: number;
}

export interface OptimizeDatasetTemplateResultApi {
  /** @minLength 1 */
  metric_name: string;
  templates: number[];
  old_template: number;
}

export interface OptimizeDatasetTemplateResultsResponseApi {
  k_prompts: string[];
  results: OptimizeDatasetTemplateResultApi[];
}

export interface OptimizeDatasetDetailResponseApi {
  /** @minLength 1 */
  status: string;
  data: OptimizeDatasetApi;
}

export type DevelopAnnotationsUserApiOrganizationRole = typeof DevelopAnnotationsUserApiOrganizationRole[keyof typeof DevelopAnnotationsUserApiOrganizationRole];


export const DevelopAnnotationsUserApiOrganizationRole = {
  Owner: 'Owner',
  Admin: 'Admin',
  Member: 'Member',
  Viewer: 'Viewer',
  workspace_admin: 'workspace_admin',
  workspace_member: 'workspace_member',
  workspace_viewer: 'workspace_viewer',
} as const;

export interface DevelopAnnotationsUserApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 254
     */
  email: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  organization_role?: DevelopAnnotationsUserApiOrganizationRole;
  is_active?: boolean;
  is_staff?: boolean;
}

export type ModelHubOverviewResponseApiVersions = { [key: string]: unknown };

export type OverviewPointApiX = { [key: string]: unknown };

export interface OverviewPointApi {
  x: OverviewPointApiX;
  y: number;
}

export interface OverviewVolumeApi {
  total_count: number;
  change: number;
  volume: OverviewPointApi[];
}

export interface OverviewIssuesApi {
  total_count: number;
  change: number;
  last_day: OverviewPointApi[];
}

export interface ModelHubOverviewResponseApi {
  volume: OverviewVolumeApi;
  issues: OverviewIssuesApi;
  versions: ModelHubOverviewResponseApiVersions;
}

export type PerformanceFilterApiType = typeof PerformanceFilterApiType[keyof typeof PerformanceFilterApiType];


export const PerformanceFilterApiType = {
  property: 'property',
  performanceMetric: 'performanceMetric',
  performanceTag: 'performanceTag',
} as const;

export type PerformanceFilterApiDatatype = typeof PerformanceFilterApiDatatype[keyof typeof PerformanceFilterApiDatatype];


export const PerformanceFilterApiDatatype = {
  string: 'string',
  number: 'number',
} as const;

export type PerformanceFilterApiOperator = typeof PerformanceFilterApiOperator[keyof typeof PerformanceFilterApiOperator];


export const PerformanceFilterApiOperator = {
  equal: 'equal',
  notEqual: 'notEqual',
  greaterThan: 'greaterThan',
  greaterThanEqualTo: 'greaterThanEqualTo',
  lessThan: 'lessThan',
  lessThanEqualTo: 'lessThanEqualTo',
} as const;

export type PerformanceFilterApiValuesItem = { [key: string]: unknown };

export interface PerformanceFilterApi {
  type: PerformanceFilterApiType;
  datatype: PerformanceFilterApiDatatype;
  operator: PerformanceFilterApiOperator;
  values?: PerformanceFilterApiValuesItem[];
  key: string;
  key_id: string;
}

export interface PerformanceDatasetApi {
  /** @minLength 1 */
  environment: string;
  /** @minLength 1 */
  version: string;
  metric_id: string;
  filters?: PerformanceFilterApi[];
}

export interface PerformanceDetailsRequestApi {
  dataset: PerformanceDatasetApi;
  filters?: PerformanceFilterApi[];
  /** @minimum 1 */
  page?: number;
  /** @minLength 1 */
  start_date: string;
  /** @minLength 1 */
  end_date: string;
}

export type PerformanceDetailsResponseApiResultItem = { [key: string]: unknown };

export interface PerformanceDetailsResponseApi {
  result: PerformanceDetailsResponseApiResultItem[];
  processing_count: number;
  count: number;
  is_next: boolean;
  page: number;
}

export interface PerformanceExportRequestApi {
  dataset: PerformanceDatasetApi;
  filters?: PerformanceFilterApi[];
  /** @minimum 1 */
  page?: number;
  /** @minLength 1 */
  start_date: string;
  /** @minLength 1 */
  end_date: string;
}

export interface PerformanceMetricOptionApi {
  id: string;
  /** @minLength 1 */
  name: string;
}

export type PerformancePropertyOptionApiValuesItem = { [key: string]: unknown };

export interface PerformancePropertyOptionApi {
  id: string;
  /** @minLength 1 */
  name: string;
  /** @minLength 1 */
  datatype: string;
  values: PerformancePropertyOptionApiValuesItem[];
}

export interface PerformanceOptionsResultApi {
  performance_metric: PerformanceMetricOptionApi[];
  properties: PerformancePropertyOptionApi[];
  meta_tags: string[];
  performance_tags: string[];
}

export interface PerformanceOptionsResponseApi {
  status: boolean;
  result: PerformanceOptionsResultApi;
}

export type PerformanceReportApiDatasets = { [key: string]: unknown };

export type PerformanceReportApiFilters = { [key: string]: unknown };

export type PerformanceReportApiBreakdown = { [key: string]: unknown };

export interface PerformanceReportApi {
  readonly id?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
  deleted?: boolean;
  deleted_at?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  datasets?: PerformanceReportApiDatasets;
  filters?: PerformanceReportApiFilters;
  breakdown?: PerformanceReportApiBreakdown;
  /**
     * @minLength 1
     * @maxLength 255
     */
  aggregation: string;
  start_date: string;
  end_date: string;
  model: string;
  organization: string;
  workspace?: string;
}

export interface PerformanceReportPaginatedResponseApi {
  count: number;
  next?: string;
  previous?: string;
  results: PerformanceReportApi[];
  total_pages?: number;
  current_page?: number;
}

export type PerformanceReportCreateApiDatasets = { [key: string]: unknown };

export type PerformanceReportCreateApiFilters = { [key: string]: unknown };

export type PerformanceReportCreateApiBreakdown = { [key: string]: unknown };

export interface PerformanceReportCreateApi {
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  datasets?: PerformanceReportCreateApiDatasets;
  filters?: PerformanceReportCreateApiFilters;
  breakdown?: PerformanceReportCreateApiBreakdown;
  /**
     * @minLength 1
     * @maxLength 255
     */
  aggregation: string;
  start_date: string;
  end_date: string;
}

export interface PerformanceReportCreateResponseApi {
  status: boolean;
  result: PerformanceReportApi;
}

export type PerformanceTagDistributionRequestApiAggBy = typeof PerformanceTagDistributionRequestApiAggBy[keyof typeof PerformanceTagDistributionRequestApiAggBy];


export const PerformanceTagDistributionRequestApiAggBy = {
  hourly: 'hourly',
  daily: 'daily',
  weekly: 'weekly',
  monthly: 'monthly',
} as const;

export type PerformanceTagDistributionRequestApiGraphType = typeof PerformanceTagDistributionRequestApiGraphType[keyof typeof PerformanceTagDistributionRequestApiGraphType];


export const PerformanceTagDistributionRequestApiGraphType = {
  all: 'all',
  good: 'good',
  bad: 'bad',
} as const;

export interface PerformanceTagDistributionRequestApi {
  dataset: PerformanceDatasetApi;
  filters?: PerformanceFilterApi[];
  agg_by: PerformanceTagDistributionRequestApiAggBy;
  /** @minLength 1 */
  start_date: string;
  /** @minLength 1 */
  end_date: string;
  graph_type: PerformanceTagDistributionRequestApiGraphType;
}

export type PerformanceQueryRequestApiAggBy = typeof PerformanceQueryRequestApiAggBy[keyof typeof PerformanceQueryRequestApiAggBy];


export const PerformanceQueryRequestApiAggBy = {
  hourly: 'hourly',
  daily: 'daily',
  weekly: 'weekly',
  monthly: 'monthly',
} as const;

export interface PerformanceBreakdownApi {
  /** @minLength 1 */
  key: string;
  /** @minLength 1 */
  key_id: string;
}

export interface PerformanceQueryRequestApi {
  datasets: PerformanceDatasetApi[];
  filters?: PerformanceFilterApi[];
  breakdown?: PerformanceBreakdownApi[];
  agg_by: PerformanceQueryRequestApiAggBy;
  /** @minLength 1 */
  start_date: string;
  /** @minLength 1 */
  end_date: string;
}

export type PromptBaseTemplateApiPromptConfigSnapshot = { [key: string]: unknown };

export interface PromptBaseTemplateApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  readonly organization?: string;
  readonly workspace?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
  readonly is_sample?: boolean;
  prompt_version?: string;
  /** @maxLength 255 */
  category?: string;
  prompt_config_snapshot?: PromptBaseTemplateApiPromptConfigSnapshot;
  readonly created_by?: string;
}

export interface PromptExecutionApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 2000
     */
  name: string;
  readonly updated_at?: string;
  readonly model?: string;
  readonly collaborators?: string;
  readonly model_detail?: string;
  prompt_folder?: string;
  is_sample?: boolean;
  readonly prompt_folder_name?: string;
  readonly created_by?: string;
}

export interface PromptFolderApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  readonly organization?: string;
  readonly workspace?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
  readonly is_sample?: boolean;
  parent_folder?: string;
  readonly created_by?: string;
}

export type PromptHistoryExecutionApiOutput = { [key: string]: unknown };

export type PromptHistoryExecutionApiMetadata = { [key: string]: unknown };

export type PromptHistoryExecutionApiEvaluationResults = { [key: string]: unknown };

export type PromptHistoryExecutionApiEvaluationConfigs = { [key: string]: unknown };

export type PromptHistoryExecutionApiPlaceholders = { [key: string]: unknown };

export interface PromptHistoryExecutionApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 50
     */
  template_version: string;
  readonly output?: PromptHistoryExecutionApiOutput;
  readonly prompt_config_snapshot?: string;
  readonly template_name?: string;
  original_template?: string;
  metadata?: PromptHistoryExecutionApiMetadata;
  readonly variable_names?: string;
  evaluation_results?: PromptHistoryExecutionApiEvaluationResults;
  evaluation_configs?: PromptHistoryExecutionApiEvaluationConfigs;
  readonly created_at?: string;
  is_default?: boolean;
  commit_message?: string;
  readonly updated_at?: string;
  is_draft?: boolean;
  readonly labels?: string;
  placeholders?: PromptHistoryExecutionApiPlaceholders;
  prompt_base_template?: string;
}

export type PromptLabelApiType = typeof PromptLabelApiType[keyof typeof PromptLabelApiType];


export const PromptLabelApiType = {
  system: 'system',
  custom: 'custom',
} as const;

export type PromptLabelApiMetadata = { [key: string]: unknown };

export interface PromptLabelApi {
  readonly id?: string;
  readonly organization?: string;
  /**
     * @minLength 1
     * @maxLength 2000
     */
  name: string;
  type: PromptLabelApiType;
  metadata?: PromptLabelApiMetadata;
  readonly created_at?: string;
  readonly updated_at?: string;
}

export type ModelHubTextErrorResponseApiType = typeof ModelHubTextErrorResponseApiType[keyof typeof ModelHubTextErrorResponseApiType];


export const ModelHubTextErrorResponseApiType = {
  validation_error: 'validation_error',
  authentication_error: 'authentication_error',
  payment_required: 'payment_required',
  entitlement_error: 'entitlement_error',
  permission_error: 'permission_error',
  not_found: 'not_found',
  conflict: 'conflict',
  client_error: 'client_error',
  rate_limit: 'rate_limit',
  server_error: 'server_error',
  service_unavailable: 'service_unavailable',
  timeout: 'timeout',
  api_error: 'api_error',
} as const;

export type ModelHubTextErrorResponseApiDetails = {[key: string]: string[]};

export interface ModelHubTextErrorResponseApi {
  status?: boolean;
  type?: ModelHubTextErrorResponseApiType;
  code?: string;
  detail?: string;
  result?: string;
  message?: string;
  error?: string;
  attr?: string;
  details?: ModelHubTextErrorResponseApiDetails;
}

export type PromptTemplateApiVariableNames = { [key: string]: unknown };

export type PromptTemplateApiPlaceholders = { [key: string]: unknown };

export interface PromptTemplateApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 2000
     */
  name: string;
  description?: string;
  variable_names?: PromptTemplateApiVariableNames;
  readonly organization?: string;
  prompt_folder?: string;
  placeholders?: PromptTemplateApiPlaceholders;
  readonly created_by?: string;
}

export type DerivedVariablePreviewRequestApiContent = { [key: string]: unknown };

export interface DerivedVariablePreviewRequestApi {
  content: DerivedVariablePreviewRequestApiContent;
  /** @minLength 1 */
  column_name?: string;
}

export interface DerivedVariableDetailResponseApi {
  status: boolean;
  result: DerivedVariableDetailApi;
}

export type PromptDerivedVariablesResultApiDerivedVariables = {[key: string]: string[]};

export interface PromptDerivedVariablesResultApi {
  /** @minLength 1 */
  version: string;
  derived_variables: PromptDerivedVariablesResultApiDerivedVariables;
}

export interface PromptDerivedVariablesResponseApi {
  status: boolean;
  result: PromptDerivedVariablesResultApi;
}

export interface DerivedVariableExtractRequestApi {
  /** @minLength 1 */
  version: string;
  /** @minLength 1 */
  column_name?: string;
  output_index?: number;
  response_format_type?: string;
}

export interface PromptMetricsMetadataApi {
  total_rows: number;
}

export type PromptMetricsResultApiTableItem = { [key: string]: unknown };

export type PromptMetricsResultApiConfig = { [key: string]: unknown };

export interface PromptMetricsResultApi {
  prompt_template_id?: string;
  /** @minLength 1 */
  prompt_template_name?: string;
  table: PromptMetricsResultApiTableItem[];
  config: PromptMetricsResultApiConfig;
  metadata: PromptMetricsMetadataApi;
}

export interface PromptMetricsResponseApi {
  status: boolean;
  result: PromptMetricsResultApi;
}

export interface PromptMetricsEmptyScreenResultApi {
  /** @minLength 1 */
  python: string;
  /** @minLength 1 */
  typescript: string;
}

export interface PromptMetricsEmptyScreenResponseApi {
  status: boolean;
  result: PromptMetricsEmptyScreenResultApi;
}

export type UserResponseSchemaApiSchema = { [key: string]: unknown };

export type UserResponseSchemaApiSchemaType = typeof UserResponseSchemaApiSchemaType[keyof typeof UserResponseSchemaApiSchemaType];


export const UserResponseSchemaApiSchemaType = {
  json: 'json',
  yaml: 'yaml',
} as const;

export interface UserResponseSchemaApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 2000
     */
  name: string;
  description?: string;
  schema?: UserResponseSchemaApiSchema;
  readonly organization?: string;
  schema_type?: UserResponseSchemaApiSchemaType;
}

export interface RunPromptForRowsRequestApi {
  run_prompt_ids: string[];
  row_ids?: string[];
  selected_all_rows?: boolean;
}

export interface ModelHubSuccessMessageResultApi {
  /** @minLength 1 */
  success: string;
}

export interface ModelHubSuccessMessageResponseApi {
  status: boolean;
  result: ModelHubSuccessMessageResultApi;
}

export type LitellmApiMessagesItem = {[key: string]: string};

/**
 * Output format type. Defaults to 'string'.
 */
export type LitellmApiOutputFormat = typeof LitellmApiOutputFormat[keyof typeof LitellmApiOutputFormat];


export const LitellmApiOutputFormat = {
  array: 'array',
  string: 'string',
  number: 'number',
  object: 'object',
  audio: 'audio',
  image: 'image',
} as const;

/**
 * String or JSON object.
 */
export type LitellmApiResponseFormat = string | { [key: string]: unknown };

/**
 * Tool selection mode: 'auto' or 'required'.
 */
export type LitellmApiToolChoice = typeof LitellmApiToolChoice[keyof typeof LitellmApiToolChoice];


export const LitellmApiToolChoice = {
  auto: 'auto',
  required: 'required',
} as const;

export type LitellmApiToolsItem = {[key: string]: string};

export interface LitellmApi {
  /**
     * @minLength 1
     * @maxLength 255
     */
  dataset_id: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  model: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  concurrency?: number;
  /** List of messages with format [{'role': 'user/assistant', 'content': 'text'}] */
  messages: LitellmApiMessagesItem[];
  /** Output format type. Defaults to 'string'. */
  output_format?: LitellmApiOutputFormat;
  /**
     * Controls the randomness. Value between 0 and 1.
     * @minimum 0
     * @maximum 1
     */
  temperature?: number;
  /**
     * Penalty for word repetition. Value between -2 and 2.
     * @minimum -2
     * @maximum 2
     */
  frequency_penalty?: number;
  /**
     * Penalty for new word usage. Value between -2 and 2.
     * @minimum -2
     * @maximum 2
     */
  presence_penalty?: number;
  /**
     * Maximum number of tokens to generate. Null = use provider default.
     * @minimum 1
     * @maximum 65536
     */
  max_tokens?: number;
  /**
     * Controls diversity via nucleus sampling. Value between 0 and 1.
     * @minimum 0
     * @maximum 1
     */
  top_p?: number;
  /** String or JSON object. */
  response_format?: LitellmApiResponseFormat;
  /** Tool selection mode: 'auto' or 'required'. */
  tool_choice?: LitellmApiToolChoice;
  /** List of tools with tool properties if available. */
  tools?: LitellmApiToolsItem[];
}

export type CreateScoreApiSourceType = typeof CreateScoreApiSourceType[keyof typeof CreateScoreApiSourceType];


export const CreateScoreApiSourceType = {
  dataset_row: 'dataset_row',
  trace: 'trace',
  observation_span: 'observation_span',
  prototype_run: 'prototype_run',
  call_execution: 'call_execution',
  trace_session: 'trace_session',
} as const;

export type CreateScoreApiValue = { [key: string]: unknown };

export type CreateScoreApiScoreSource = typeof CreateScoreApiScoreSource[keyof typeof CreateScoreApiScoreSource];


export const CreateScoreApiScoreSource = {
  human: 'human',
  api: 'api',
  auto: 'auto',
  imported: 'imported',
} as const;

export interface CreateScoreApi {
  source_type: CreateScoreApiSourceType;
  /** @minLength 1 */
  source_id: string;
  label_id: string;
  value: CreateScoreApiValue;
  notes?: string;
  score_source?: CreateScoreApiScoreSource;
  queue_item_id?: string;
}

export interface ScoreResponseApi {
  status?: boolean;
  result: ScoreApi;
}

export type BulkCreateScoresApiSourceType = typeof BulkCreateScoresApiSourceType[keyof typeof BulkCreateScoresApiSourceType];


export const BulkCreateScoresApiSourceType = {
  dataset_row: 'dataset_row',
  trace: 'trace',
  observation_span: 'observation_span',
  prototype_run: 'prototype_run',
  call_execution: 'call_execution',
  trace_session: 'trace_session',
} as const;

export type BulkCreateScoreItemApiScoreSource = typeof BulkCreateScoreItemApiScoreSource[keyof typeof BulkCreateScoreItemApiScoreSource];


export const BulkCreateScoreItemApiScoreSource = {
  human: 'human',
  api: 'api',
  auto: 'auto',
  imported: 'imported',
} as const;

export type BulkCreateScoreItemApiValue = { [key: string]: unknown };

export interface BulkCreateScoreItemApi {
  label_id: string;
  value: BulkCreateScoreItemApiValue;
  notes?: string;
  score_source?: BulkCreateScoreItemApiScoreSource;
}

export interface BulkCreateScoresApi {
  source_type: BulkCreateScoresApiSourceType;
  /** @minLength 1 */
  source_id: string;
  scores: BulkCreateScoreItemApi[];
  notes?: string;
  span_notes?: string;
  span_notes_source_id?: string;
  queue_item_id?: string;
}

export interface BulkCreateScoresResultApi {
  scores: ScoreApi[];
  errors: string[];
}

export interface BulkCreateScoresResponseApi {
  status?: boolean;
  result: BulkCreateScoresResultApi;
}

export type ScoreForSourceResponseApiSpanNotesItem = { [key: string]: unknown };

export interface ScoreForSourceResponseApi {
  status?: boolean;
  result: ScoreApi[];
  span_notes?: ScoreForSourceResponseApiSpanNotesItem[];
}

export type UpdateScoreApiValue = { [key: string]: unknown };

export type UpdateScoreApiScoreSource = typeof UpdateScoreApiScoreSource[keyof typeof UpdateScoreApiScoreSource];


export const UpdateScoreApiScoreSource = {
  human: 'human',
  api: 'api',
  auto: 'auto',
  imported: 'imported',
} as const;

export interface UpdateScoreApi {
  value?: UpdateScoreApiValue;
  notes?: string;
  score_source?: UpdateScoreApiScoreSource;
}

export type ScoreDeleteResponseApiResult = {[key: string]: boolean};

export interface ScoreDeleteResponseApi {
  status?: boolean;
  result: ScoreDeleteResponseApiResult;
}

export type SecretApiSecretType = typeof SecretApiSecretType[keyof typeof SecretApiSecretType];


export const SecretApiSecretType = {
  API_KEY: 'API_KEY',
  PASSWORD: 'PASSWORD',
  TOKEN: 'TOKEN',
  OTHER: 'OTHER',
} as const;

export interface SecretApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  description?: string;
  secret_type?: SecretApiSecretType;
  /** @minLength 1 */
  readonly secret_type_display?: string;
  /** @maxLength 2500 */
  key?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
}

export type TestEvalTemplateApiConfig = { [key: string]: unknown };

export type TestEvalTemplateApiChoices = {[key: string]: string};

export type TestEvalTemplateApiInputDataTypes = { [key: string]: unknown };

export type TestEvalTemplateApiVariableKeys = { [key: string]: unknown };

export type TestEvalTemplateApiMapping = { [key: string]: unknown };

export type TestEvalTemplateApiConfigParamsDesc = { [key: string]: unknown };

export type TestEvalTemplateApiConfigParamsOption = { [key: string]: unknown };

export interface TestEvalTemplateApi {
  config: TestEvalTemplateApiConfig;
  /** @maxLength 100 */
  model?: string;
  eval_tags?: string[];
  criteria?: string;
  multi_choice?: boolean;
  choices?: TestEvalTemplateApiChoices;
  input_data_types?: TestEvalTemplateApiInputDataTypes;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  /** @maxLength 255 */
  description?: string;
  /**
     * @minLength 1
     * @maxLength 50
     */
  output_type: string;
  check_internet?: boolean;
  required_keys?: string[];
  /** @minLength 1 */
  template_type?: string;
  /** @maxLength 100 */
  eval_type_id?: string;
  template_id?: string;
  eval_type?: string;
  error_localizer?: boolean;
  reason_column?: boolean;
  optional_keys?: string[];
  variable_keys?: TestEvalTemplateApiVariableKeys;
  run_prompt_column?: boolean;
  template_name?: string;
  mapping?: TestEvalTemplateApiMapping;
  output?: string;
  config_params_desc?: TestEvalTemplateApiConfigParamsDesc;
  config_params_option?: TestEvalTemplateApiConfigParamsOption;
}

export type ToolsApiConfig = { [key: string]: unknown };

export type ToolsApiConfigType = typeof ToolsApiConfigType[keyof typeof ToolsApiConfigType];


export const ToolsApiConfigType = {
  json: 'json',
  yaml: 'yaml',
} as const;

export interface ToolsApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  description: string;
  config: ToolsApiConfig;
  config_type?: ToolsApiConfigType;
  readonly organization?: string;
}

export type TTSVoiceApiVoiceType = typeof TTSVoiceApiVoiceType[keyof typeof TTSVoiceApiVoiceType];


export const TTSVoiceApiVoiceType = {
  system: 'system',
  custom: 'custom',
} as const;

export interface TTSVoiceApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  /** @maxLength 255 */
  description?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  voice_id: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  provider: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  model: string;
  readonly voice_type?: TTSVoiceApiVoiceType;
  readonly created_at?: string;
  readonly updated_at?: string;
}

export type UpdateEvalTemplateApiChoicesMap = {[key: string]: string};

export type UpdateEvalTemplateApiConfig = { [key: string]: unknown };

export interface UpdateEvalTemplateApi {
  eval_template_id: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  description?: string;
  criteria?: string;
  eval_tags?: string[];
  multi_choice?: boolean;
  function_eval?: boolean;
  choices_map?: UpdateEvalTemplateApiChoicesMap;
  config?: UpdateEvalTemplateApiConfig;
  /**
     * @minLength 1
     * @maxLength 100
     */
  model?: string;
  check_internet?: boolean;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name?: string;
  required_keys?: string[];
  /** @maxLength 100 */
  eval_type_id?: string;
  template_id?: string;
  error_localizer_enabled?: boolean;
}

export interface LegacyEvalTemplateUpdateResponseApi {
  status: boolean;
  /** @minLength 1 */
  result: string;
}

export type UploadFileApiType = typeof UploadFileApiType[keyof typeof UploadFileApiType];


export const UploadFileApiType = {
  image: 'image',
  audio: 'audio',
  pdf: 'pdf',
  text: 'text',
} as const;

export interface UploadFileApi {
  files?: string[];
  links?: string[];
  type: UploadFileApiType;
}

export interface UploadedFileResultApi {
  url?: string;
  file_name?: string;
  error?: string;
}

export interface UploadFileResponseApi {
  status: boolean;
  result: UploadedFileResultApi[];
}

export interface SAMLErrorResponseApi {
  status: boolean;
  /** @minLength 1 */
  result?: string;
  /** @minLength 1 */
  message?: string;
}

export interface SAMLUrlResultApi {
  /** @minLength 1 */
  url: string;
}

export interface SAMLUrlResponseApi {
  status: boolean;
  result: SAMLUrlResultApi;
}

export type SamlApiIdentityType = typeof SamlApiIdentityType[keyof typeof SamlApiIdentityType];


export const SamlApiIdentityType = {
  NUMBER_1: 1,
  NUMBER_2: 2,
  NUMBER_3: 3,
} as const;

export interface SamlApi {
  /** @maxLength 250 */
  name?: string;
  /** @minLength 1 */
  readonly id?: string;
  readonly identity_type?: SamlApiIdentityType;
  is_enabled?: boolean;
}

export interface SAMLIDPUploadListResultApi {
  count: number;
  /** @minLength 1 */
  next?: string;
  /** @minLength 1 */
  previous?: string;
  results: SamlApi[];
  /** @minLength 1 */
  acs_url: string;
  /** @minLength 1 */
  audience_url: string;
}

export interface SAMLIDPUploadListResponseApi {
  status: boolean;
  result: SAMLIDPUploadListResultApi;
}

export interface SAMLStringResponseApi {
  status: boolean;
  /** @minLength 1 */
  result: string;
}

export interface SAMLIDPUploadDetailResultApi {
  is_enabled: boolean;
  identity_type: number;
  /** @minLength 1 */
  name: string;
  /** @minLength 1 */
  acs_url: string;
  /** @minLength 1 */
  audience_url: string;
}

export interface SAMLIDPUploadDetailResponseApi {
  status: boolean;
  result: SAMLIDPUploadDetailResultApi;
}

export type ConfigureEvaluationsApiInputs = {[key: string]: string};

export type ConfigureEvaluationsApiConfig = {[key: string]: string};

export interface ConfigureEvaluationsApi {
  /** @minLength 1 */
  eval_templates: string;
  inputs: ConfigureEvaluationsApiInputs;
  model_name?: string;
  config?: ConfigureEvaluationsApiConfig;
}

export interface SDKConfigureEvaluationsRequestApi {
  eval_config: ConfigureEvaluationsApi;
  /** @minLength 1 */
  platform: string;
  custom_eval_name?: string;
  [key: string]: { [key: string]: unknown };
}

export interface SDKMessageResultApi {
  /** @minLength 1 */
  message: string;
}

export interface SDKConfigureEvaluationsResponseApi {
  status: boolean;
  result: SDKMessageResultApi;
}

export type SDKErrorResponseApiErrors = {[key: string]: string[]};

export interface SDKErrorResponseApi {
  status: boolean;
  result?: string;
  message?: string;
  errors?: SDKErrorResponseApiErrors;
}

export type SDKStandaloneEvalRequestApiConfig = {[key: string]: string};

export interface SDKStandaloneEvalInputApi {
  input?: string;
  /** @minimum 1 */
  max_tokens?: number;
  [key: string]: { [key: string]: unknown };
}

export interface SDKStandaloneEvalRequestApi {
  inputs: SDKStandaloneEvalInputApi[];
  config: SDKStandaloneEvalRequestApiConfig;
  protect_flash?: boolean;
}

export type SDKStandaloneEvalResultItemApiEvaluationsItem = { [key: string]: unknown };

export interface SDKStandaloneEvalResultItemApi {
  evaluations: SDKStandaloneEvalResultItemApiEvaluationsItem[];
}

export interface SDKStandaloneEvalResponseApi {
  status: boolean;
  result: SDKStandaloneEvalResultItemApi[];
}

export type SDKEvalTemplateApiEvalTags = { [key: string]: unknown };

export type SDKEvalTemplateApiConfig = { [key: string]: unknown };

export type SDKEvalTemplateApiCriteria = { [key: string]: unknown };

export type SDKEvalTemplateApiChoices = { [key: string]: unknown };

export interface SDKEvalTemplateApi {
  /** @minLength 1 */
  id: string;
  /** @minLength 1 */
  name: string;
  description: string;
  organization: string;
  owner: string;
  eval_tags?: SDKEvalTemplateApiEvalTags;
  config?: SDKEvalTemplateApiConfig;
  eval_id: string;
  criteria?: SDKEvalTemplateApiCriteria;
  choices?: SDKEvalTemplateApiChoices;
  multi_choice?: boolean;
}

export interface SDKEvalTemplateResponseApi {
  status: boolean;
  result: SDKEvalTemplateApi;
}

export type SDKCICDEvaluationRunsResultApiStatus = typeof SDKCICDEvaluationRunsResultApiStatus[keyof typeof SDKCICDEvaluationRunsResultApiStatus];


export const SDKCICDEvaluationRunsResultApiStatus = {
  processing: 'processing',
  completed: 'completed',
} as const;

export type SDKCICDEvaluationRunSummaryApiResultsSummary = {[key: string]: string};

export interface SDKCICDEvaluationRunSummaryApi {
  id: string;
  /** @minLength 1 */
  project: string;
  /** @minLength 1 */
  version: string;
  results_summary: SDKCICDEvaluationRunSummaryApiResultsSummary;
}

export interface SDKCICDEvaluationRunsResultApi {
  /** @minLength 1 */
  message: string;
  status: SDKCICDEvaluationRunsResultApiStatus;
  evaluation_runs?: SDKCICDEvaluationRunSummaryApi[];
}

export interface SDKCICDEvaluationRunsResponseApi {
  status: boolean;
  result: SDKCICDEvaluationRunsResultApi;
}

export type CICDEvaluationItemApiInputs = {[key: string]: string};

export type CICDEvaluationItemApiConfig = {[key: string]: string};

export interface CICDEvaluationItemApi {
  /** @minLength 1 */
  eval_template: string;
  inputs: CICDEvaluationItemApiInputs;
  model_name?: string;
  config?: CICDEvaluationItemApiConfig;
}

export interface CICDJobApi {
  /** @minLength 1 */
  project_name: string;
  /** @minLength 1 */
  version: string;
  eval_data: CICDEvaluationItemApi[];
}

export interface SDKCICDEvaluationRunAcceptedApi {
  /** @minLength 1 */
  message: string;
  /** @minLength 1 */
  project_name: string;
  /** @minLength 1 */
  version: string;
  evaluation_run_id: string;
}

export interface SDKCICDEvaluationRunAcceptedResponseApi {
  status: boolean;
  result: SDKCICDEvaluationRunAcceptedApi;
}

export interface SDKGetEvalsResponseApi {
  status: boolean;
  result: SDKEvalTemplateApi[];
}

export type SDKStandaloneEvalV2ResultApiResult = { [key: string]: unknown };

export interface SDKStandaloneEvalV2ResultApi {
  /** @minLength 1 */
  eval_status: string;
  result: SDKStandaloneEvalV2ResultApiResult;
}

export interface SDKStandaloneEvalV2ResponseApi {
  status: boolean;
  result: SDKStandaloneEvalV2ResultApi;
}

export type SDKStandaloneEvalV2RequestApiInputs = {[key: string]: string};

export type SDKStandaloneEvalV2RequestApiConfig = {[key: string]: string};

export interface SDKStandaloneEvalV2RequestApi {
  /** @minLength 1 */
  eval_name: string;
  inputs: SDKStandaloneEvalV2RequestApiInputs;
  model?: string;
  span_id?: string;
  custom_eval_name?: string;
  trace_eval?: boolean;
  is_async?: boolean;
  error_localizer?: boolean;
  config?: SDKStandaloneEvalV2RequestApiConfig;
}

export type SDKSimulationAnalyticsResultApiEvalResultsItem = { [key: string]: unknown };

export type SDKSimulationAnalyticsResultApiEvalAverages = { [key: string]: unknown };

export type SDKSimulationAnalyticsResultApiSystemSummary = { [key: string]: unknown };

export type SDKSimulationAnalyticsResultApiEvalExplanationSummary = { [key: string]: unknown };

export interface SDKSimulationAnalyticsResultApi {
  execution_id?: string;
  /** @minLength 1 */
  run_test_name: string;
  /** @minLength 1 */
  status?: string;
  /** @minLength 1 */
  message?: string;
  eval_results: SDKSimulationAnalyticsResultApiEvalResultsItem[];
  eval_averages: SDKSimulationAnalyticsResultApiEvalAverages;
  system_summary: SDKSimulationAnalyticsResultApiSystemSummary;
  eval_explanation_summary?: SDKSimulationAnalyticsResultApiEvalExplanationSummary;
  eval_explanation_summary_status?: string;
}

export interface SDKSimulationAnalyticsResponseApi {
  status: boolean;
  result: SDKSimulationAnalyticsResultApi;
}

/**
 * Current status of the test execution
 */
export type ExecutionMetricsApiStatus = typeof ExecutionMetricsApiStatus[keyof typeof ExecutionMetricsApiStatus];


export const ExecutionMetricsApiStatus = {
  pending: 'pending',
  running: 'running',
  completed: 'completed',
  failed: 'failed',
  cancelled: 'cancelled',
  cancelling: 'cancelling',
  evaluating: 'evaluating',
} as const;

export interface ExecutionMetricsApi {
  execution_id: string;
  /** Current status of the test execution */
  readonly status?: ExecutionMetricsApiStatus;
  /** When the test execution started */
  readonly started_at?: string;
  /** When the test execution completed */
  readonly completed_at?: string;
  /** Total number of calls to be made */
  readonly total_calls?: number;
  /** Number of successfully completed calls */
  readonly completed_calls?: number;
  /** Number of failed calls */
  readonly failed_calls?: number;
  readonly metrics?: string;
}

export type SDKSimulationMetricsResultApiLatency = { [key: string]: unknown };

export type SDKSimulationMetricsResultApiCost = { [key: string]: unknown };

export type SDKSimulationMetricsResultApiConversation = { [key: string]: unknown };

export type SDKSimulationMetricsResultApiChatMetrics = { [key: string]: unknown };

export type SDKSimulationMetricsResultApiMetrics = { [key: string]: unknown };

export interface SDKSimulationMetricsResultApi {
  call_execution_id?: string;
  execution_id?: string;
  /** @minLength 1 */
  status?: string;
  duration_seconds?: number;
  started_at?: string;
  completed_at?: string;
  total_calls?: number;
  completed_calls?: number;
  failed_calls?: number;
  latency?: SDKSimulationMetricsResultApiLatency;
  cost?: SDKSimulationMetricsResultApiCost;
  conversation?: SDKSimulationMetricsResultApiConversation;
  chat_metrics?: SDKSimulationMetricsResultApiChatMetrics;
  metrics?: SDKSimulationMetricsResultApiMetrics;
  total_pages?: number;
  current_page?: number;
  count?: number;
  results?: ExecutionMetricsApi[];
}

export interface SDKSimulationMetricsResponseApi {
  status: boolean;
  result: SDKSimulationMetricsResultApi;
}

/**
 * Current status of the test execution
 */
export type ExecutionRunsApiStatus = typeof ExecutionRunsApiStatus[keyof typeof ExecutionRunsApiStatus];


export const ExecutionRunsApiStatus = {
  pending: 'pending',
  running: 'running',
  completed: 'completed',
  failed: 'failed',
  cancelled: 'cancelled',
  cancelling: 'cancelling',
  evaluating: 'evaluating',
} as const;

export interface ExecutionRunsApi {
  execution_id: string;
  /** Current status of the test execution */
  readonly status?: ExecutionRunsApiStatus;
  /** When the test execution started */
  readonly started_at?: string;
  /** When the test execution completed */
  readonly completed_at?: string;
  /** Total number of calls to be made */
  readonly total_calls?: number;
  /** Number of successfully completed calls */
  readonly completed_calls?: number;
  /** Number of failed calls */
  readonly failed_calls?: number;
  readonly eval_results?: string;
}

export type SDKSimulationRunsResultApiEvalOutputs = { [key: string]: unknown };

export type SDKSimulationRunsResultApiEvalResultsItem = { [key: string]: unknown };

export type SDKSimulationRunsResultApiLatency = { [key: string]: unknown };

export type SDKSimulationRunsResultApiCost = { [key: string]: unknown };

export type SDKSimulationRunsResultApiCallResults = { [key: string]: unknown };

export type SDKSimulationRunsResultApiEvalExplanationSummary = { [key: string]: unknown };

export interface SDKSimulationRunsResultApi {
  call_execution_id?: string;
  execution_id?: string;
  scenario_id?: string;
  scenario_name?: string;
  /** @minLength 1 */
  status?: string;
  started_at?: string;
  completed_at?: string;
  duration_seconds?: number;
  ended_reason?: string;
  call_summary?: string;
  total_calls?: number;
  completed_calls?: number;
  failed_calls?: number;
  eval_outputs?: SDKSimulationRunsResultApiEvalOutputs;
  eval_results?: SDKSimulationRunsResultApiEvalResultsItem[];
  latency?: SDKSimulationRunsResultApiLatency;
  cost?: SDKSimulationRunsResultApiCost;
  call_results?: SDKSimulationRunsResultApiCallResults;
  eval_explanation_summary?: SDKSimulationRunsResultApiEvalExplanationSummary;
  eval_explanation_summary_status?: string;
  total_pages?: number;
  current_page?: number;
  count?: number;
  results?: ExecutionRunsApi[];
}

export interface SDKSimulationRunsResponseApi {
  status: boolean;
  result: SDKSimulationRunsResultApi;
}

export type AgentDefinitionListResponseApiAgentType = typeof AgentDefinitionListResponseApiAgentType[keyof typeof AgentDefinitionListResponseApiAgentType];


export const AgentDefinitionListResponseApiAgentType = {
  voice: 'voice',
  text: 'text',
} as const;

/**
 * Language of the agent
 */
export type AgentDefinitionListResponseApiLanguage = typeof AgentDefinitionListResponseApiLanguage[keyof typeof AgentDefinitionListResponseApiLanguage];


export const AgentDefinitionListResponseApiLanguage = {
  ar: 'ar',
  bg: 'bg',
  zh: 'zh',
  cs: 'cs',
  da: 'da',
  nl: 'nl',
  en: 'en',
  fi: 'fi',
  fr: 'fr',
  de: 'de',
  el: 'el',
  hi: 'hi',
  hu: 'hu',
  id: 'id',
  it: 'it',
  ja: 'ja',
  ko: 'ko',
  ms: 'ms',
  no: 'no',
  pl: 'pl',
  pt: 'pt',
  ro: 'ro',
  ru: 'ru',
  sk: 'sk',
  es: 'es',
  sv: 'sv',
  tr: 'tr',
  uk: 'uk',
  vi: 'vi',
} as const;

/**
 * Language of the agent
 */
export type AgentDefinitionListResponseApiLanguagesItem = typeof AgentDefinitionListResponseApiLanguagesItem[keyof typeof AgentDefinitionListResponseApiLanguagesItem];


export const AgentDefinitionListResponseApiLanguagesItem = {
  ar: 'ar',
  bg: 'bg',
  zh: 'zh',
  cs: 'cs',
  da: 'da',
  nl: 'nl',
  en: 'en',
  fi: 'fi',
  fr: 'fr',
  de: 'de',
  el: 'el',
  hi: 'hi',
  hu: 'hu',
  id: 'id',
  it: 'it',
  ja: 'ja',
  ko: 'ko',
  ms: 'ms',
  no: 'no',
  pl: 'pl',
  pt: 'pt',
  ro: 'ro',
  ru: 'ru',
  sk: 'sk',
  es: 'es',
  sv: 'sv',
  tr: 'tr',
  uk: 'uk',
  vi: 'vi',
} as const;

/**
 * Headers to be sent to the websocket server
 */
export type AgentDefinitionListResponseApiWebsocketHeaders = { [key: string]: unknown };

/**
 * Details of the model
 */
export type AgentDefinitionListResponseApiModelDetails = { [key: string]: unknown };

export interface AgentDefinitionListResponseApi {
  readonly id?: string;
  /**
     * Name of the AI agent
     * @minLength 1
     */
  readonly agent_name?: string;
  readonly agent_type?: AgentDefinitionListResponseApiAgentType;
  /**
     * Phone number associated with the AI agent
     * @minLength 1
     */
  readonly contact_number?: string;
  /** Whether the agent handles inbound calls */
  readonly inbound?: boolean;
  /**
     * Detailed description of the AI agent's purpose and capabilities
     * @minLength 1
     */
  readonly description?: string;
  /**
     * External identifier for the assistant
     * @minLength 1
     */
  readonly assistant_id?: string;
  /**
     * Provider of the AI agent
     * @minLength 1
     */
  readonly provider?: string;
  /** Language of the agent */
  readonly language?: AgentDefinitionListResponseApiLanguage;
  readonly languages?: readonly AgentDefinitionListResponseApiLanguagesItem[];
  /**
     * WebSocket URL for real-time communication with the agent
     * @minLength 1
     */
  readonly websocket_url?: string;
  /** Headers to be sent to the websocket server */
  readonly websocket_headers?: AgentDefinitionListResponseApiWebsocketHeaders;
  readonly workspace?: string;
  readonly knowledge_base?: string;
  /** Organization this agent definition belongs to */
  readonly organization?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
  readonly latest_version?: string;
  readonly latest_version_id?: string;
  /** Details of the model */
  readonly model_details?: AgentDefinitionListResponseApiModelDetails;
  /**
     * Model of the agent
     * @minLength 1
     */
  readonly model?: string;
}

export type ApiErrorWithDetailsResponseApiType = typeof ApiErrorWithDetailsResponseApiType[keyof typeof ApiErrorWithDetailsResponseApiType];


export const ApiErrorWithDetailsResponseApiType = {
  validation_error: 'validation_error',
  authentication_error: 'authentication_error',
  payment_required: 'payment_required',
  entitlement_error: 'entitlement_error',
  permission_error: 'permission_error',
  not_found: 'not_found',
  conflict: 'conflict',
  client_error: 'client_error',
  rate_limit: 'rate_limit',
  server_error: 'server_error',
  service_unavailable: 'service_unavailable',
  timeout: 'timeout',
  api_error: 'api_error',
} as const;

export type ApiErrorWithDetailsResponseApiDetails = {[key: string]: string[]};

export interface ApiErrorWithDetailsResponseApi {
  status?: boolean;
  type?: ApiErrorWithDetailsResponseApiType;
  code?: string;
  detail?: string;
  /** @minLength 1 */
  result?: string;
  /** @minLength 1 */
  message?: string;
  error?: string;
  attr?: string;
  details?: ApiErrorWithDetailsResponseApiDetails;
}

export interface AgentDefinitionBulkDeleteRequestApi {
  /**
     * List of agent definition UUIDs to delete.
     * @minItems 1
     */
  agent_ids: string[];
}

export interface AgentDefinitionBulkDeleteResponseApi {
  /** @minLength 1 */
  readonly message?: string;
  readonly agents_updated?: number;
  readonly versions_updated?: number;
}

/**
 * The type of agent. One of: voice, text.
 */
export type AgentDefinitionCreateRequestApiAgentType = typeof AgentDefinitionCreateRequestApiAgentType[keyof typeof AgentDefinitionCreateRequestApiAgentType];


export const AgentDefinitionCreateRequestApiAgentType = {
  voice: 'voice',
  text: 'text',
} as const;

export type AgentDefinitionCreateRequestApiAuthenticationMethod = typeof AgentDefinitionCreateRequestApiAuthenticationMethod[keyof typeof AgentDefinitionCreateRequestApiAuthenticationMethod];


export const AgentDefinitionCreateRequestApiAuthenticationMethod = {
  api_key: 'api_key',
} as const;

export type AgentDefinitionCreateRequestApiModelDetails = { [key: string]: unknown };

export type AgentDefinitionCreateRequestApiWebsocketHeaders = { [key: string]: unknown };

export type AgentDefinitionCreateRequestApiLivekitConfigJson = { [key: string]: unknown };

export interface AgentDefinitionCreateRequestApi {
  /**
     * @minLength 1
     * @maxLength 255
     */
  agent_name: string;
  /** The type of agent. One of: voice, text. */
  agent_type: AgentDefinitionCreateRequestApiAgentType;
  /** @minLength 1 */
  commit_message: string;
  inbound?: boolean;
  description?: string;
  provider?: string;
  api_key?: string;
  assistant_id?: string;
  authentication_method?: AgentDefinitionCreateRequestApiAuthenticationMethod;
  language?: string;
  languages?: string[];
  contact_number?: string;
  knowledge_base?: string;
  observability_enabled?: boolean;
  model?: string;
  model_details?: AgentDefinitionCreateRequestApiModelDetails;
  websocket_url?: string;
  websocket_headers?: AgentDefinitionCreateRequestApiWebsocketHeaders;
  replay_session_id?: string;
  /** @maxLength 500 */
  livekit_url?: string;
  livekit_api_key?: string;
  livekit_api_secret?: string;
  livekit_agent_name?: string;
  livekit_config_json?: AgentDefinitionCreateRequestApiLivekitConfigJson;
  /** @minimum 1 */
  livekit_max_concurrency?: number;
}

export type AgentDefinitionResponseApiAgentType = typeof AgentDefinitionResponseApiAgentType[keyof typeof AgentDefinitionResponseApiAgentType];


export const AgentDefinitionResponseApiAgentType = {
  voice: 'voice',
  text: 'text',
} as const;

/**
 * Language of the agent
 */
export type AgentDefinitionResponseApiLanguage = typeof AgentDefinitionResponseApiLanguage[keyof typeof AgentDefinitionResponseApiLanguage];


export const AgentDefinitionResponseApiLanguage = {
  ar: 'ar',
  bg: 'bg',
  zh: 'zh',
  cs: 'cs',
  da: 'da',
  nl: 'nl',
  en: 'en',
  fi: 'fi',
  fr: 'fr',
  de: 'de',
  el: 'el',
  hi: 'hi',
  hu: 'hu',
  id: 'id',
  it: 'it',
  ja: 'ja',
  ko: 'ko',
  ms: 'ms',
  no: 'no',
  pl: 'pl',
  pt: 'pt',
  ro: 'ro',
  ru: 'ru',
  sk: 'sk',
  es: 'es',
  sv: 'sv',
  tr: 'tr',
  uk: 'uk',
  vi: 'vi',
} as const;

/**
 * Language of the agent
 */
export type AgentDefinitionResponseApiLanguagesItem = typeof AgentDefinitionResponseApiLanguagesItem[keyof typeof AgentDefinitionResponseApiLanguagesItem];


export const AgentDefinitionResponseApiLanguagesItem = {
  ar: 'ar',
  bg: 'bg',
  zh: 'zh',
  cs: 'cs',
  da: 'da',
  nl: 'nl',
  en: 'en',
  fi: 'fi',
  fr: 'fr',
  de: 'de',
  el: 'el',
  hi: 'hi',
  hu: 'hu',
  id: 'id',
  it: 'it',
  ja: 'ja',
  ko: 'ko',
  ms: 'ms',
  no: 'no',
  pl: 'pl',
  pt: 'pt',
  ro: 'ro',
  ru: 'ru',
  sk: 'sk',
  es: 'es',
  sv: 'sv',
  tr: 'tr',
  uk: 'uk',
  vi: 'vi',
} as const;

export type AgentDefinitionResponseApiAuthenticationMethod = typeof AgentDefinitionResponseApiAuthenticationMethod[keyof typeof AgentDefinitionResponseApiAuthenticationMethod];


export const AgentDefinitionResponseApiAuthenticationMethod = {
  api_key: 'api_key',
} as const;

/**
 * Headers to be sent to the websocket server
 */
export type AgentDefinitionResponseApiWebsocketHeaders = { [key: string]: unknown };

/**
 * Details of the model
 */
export type AgentDefinitionResponseApiModelDetails = { [key: string]: unknown };

export interface AgentDefinitionResponseApi {
  readonly id?: string;
  /**
     * Name of the AI agent
     * @minLength 1
     */
  readonly agent_name?: string;
  readonly agent_type?: AgentDefinitionResponseApiAgentType;
  /**
     * Phone number associated with the AI agent
     * @minLength 1
     */
  readonly contact_number?: string;
  /** Whether the agent handles inbound calls */
  readonly inbound?: boolean;
  /**
     * Detailed description of the AI agent's purpose and capabilities
     * @minLength 1
     */
  readonly description?: string;
  /**
     * External identifier for the assistant
     * @minLength 1
     */
  readonly assistant_id?: string;
  /**
     * Provider of the AI agent
     * @minLength 1
     */
  readonly provider?: string;
  /** Language of the agent */
  readonly language?: AgentDefinitionResponseApiLanguage;
  readonly languages?: readonly AgentDefinitionResponseApiLanguagesItem[];
  readonly authentication_method?: AgentDefinitionResponseApiAuthenticationMethod;
  /**
     * WebSocket URL for real-time communication with the agent
     * @minLength 1
     */
  readonly websocket_url?: string;
  /** Headers to be sent to the websocket server */
  readonly websocket_headers?: AgentDefinitionResponseApiWebsocketHeaders;
  readonly workspace?: string;
  readonly knowledge_base?: string;
  /** Organization this agent definition belongs to */
  readonly organization?: string;
  readonly api_key?: string;
  readonly observability_provider?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
  /**
     * Model of the agent
     * @minLength 1
     */
  readonly model?: string;
  /** Details of the model */
  readonly model_details?: AgentDefinitionResponseApiModelDetails;
  readonly livekit_url?: string;
  readonly livekit_api_key?: string;
  readonly livekit_agent_name?: string;
  readonly livekit_config_json?: string;
  readonly livekit_max_concurrency?: string;
}

export interface AgentDefinitionCreateResponseApi {
  /** @minLength 1 */
  readonly message?: string;
  agent?: AgentDefinitionResponseApi;
}

export interface AgentDefinitionDeleteResponseApi {
  /** @minLength 1 */
  readonly message?: string;
}

export type AgentDefinitionEditRequestApiAgentType = typeof AgentDefinitionEditRequestApiAgentType[keyof typeof AgentDefinitionEditRequestApiAgentType];


export const AgentDefinitionEditRequestApiAgentType = {
  voice: 'voice',
  text: 'text',
} as const;

export type AgentDefinitionEditRequestApiAuthenticationMethod = typeof AgentDefinitionEditRequestApiAuthenticationMethod[keyof typeof AgentDefinitionEditRequestApiAuthenticationMethod];


export const AgentDefinitionEditRequestApiAuthenticationMethod = {
  api_key: 'api_key',
} as const;

export type AgentDefinitionEditRequestApiModelDetails = { [key: string]: unknown };

export type AgentDefinitionEditRequestApiWebsocketHeaders = { [key: string]: unknown };

export type AgentDefinitionEditRequestApiLivekitConfigJson = { [key: string]: unknown };

export interface AgentDefinitionEditRequestApi {
  /**
     * @minLength 1
     * @maxLength 255
     */
  agent_name?: string;
  agent_type?: AgentDefinitionEditRequestApiAgentType;
  description?: string;
  provider?: string;
  api_key?: string;
  assistant_id?: string;
  authentication_method?: AgentDefinitionEditRequestApiAuthenticationMethod;
  language?: string;
  languages?: string[];
  contact_number?: string;
  inbound?: boolean;
  knowledge_base?: string;
  model?: string;
  model_details?: AgentDefinitionEditRequestApiModelDetails;
  websocket_url?: string;
  websocket_headers?: AgentDefinitionEditRequestApiWebsocketHeaders;
  /** @maxLength 500 */
  livekit_url?: string;
  livekit_api_key?: string;
  livekit_api_secret?: string;
  livekit_agent_name?: string;
  livekit_config_json?: AgentDefinitionEditRequestApiLivekitConfigJson;
  /** @minimum 1 */
  livekit_max_concurrency?: number;
}

export interface AgentDefinitionEditResponseApi {
  /** @minLength 1 */
  readonly message?: string;
  agent?: AgentDefinitionResponseApi;
}

/**
 * Current status of this version
 */
export type AgentVersionListResponseApiStatus = typeof AgentVersionListResponseApiStatus[keyof typeof AgentVersionListResponseApiStatus];


export const AgentVersionListResponseApiStatus = {
  draft: 'draft',
  active: 'active',
  archived: 'archived',
  deprecated: 'deprecated',
} as const;

export interface AgentVersionListResponseApi {
  readonly id?: string;
  /** Version number of the agent */
  readonly version_number?: number;
  /**
     * Human-readable version name (e.g., 'v1.2.3')
     * @minLength 1
     */
  readonly version_name?: string;
  readonly version_name_display?: string;
  /** Current status of this version */
  readonly status?: AgentVersionListResponseApiStatus;
  /** @minLength 1 */
  readonly status_display?: string;
  /** Performance score (0.0 to 10.0) */
  readonly score?: string;
  /** Number of tests run for this version */
  readonly test_count?: number;
  /** Test pass rate percentage */
  readonly pass_rate?: string;
  /**
     * Description of changes in this version
     * @minLength 1
     */
  readonly description?: string;
  /**
     * Commit message for the agent version
     * @minLength 1
     */
  readonly commit_message?: string;
  readonly is_active?: string;
  readonly is_latest?: string;
  readonly created_at?: string;
}

export type AgentVersionCreateRequestApiAgentType = typeof AgentVersionCreateRequestApiAgentType[keyof typeof AgentVersionCreateRequestApiAgentType];


export const AgentVersionCreateRequestApiAgentType = {
  voice: 'voice',
  text: 'text',
} as const;

export type AgentVersionCreateRequestApiAuthenticationMethod = typeof AgentVersionCreateRequestApiAuthenticationMethod[keyof typeof AgentVersionCreateRequestApiAuthenticationMethod];


export const AgentVersionCreateRequestApiAuthenticationMethod = {
  api_key: 'api_key',
} as const;

export type AgentVersionCreateRequestApiModelDetails = { [key: string]: unknown };

export type AgentVersionCreateRequestApiLivekitConfigJson = { [key: string]: unknown };

export interface AgentVersionCreateRequestApi {
  /**
     * @minLength 1
     * @maxLength 255
     */
  agent_name?: string;
  agent_type?: AgentVersionCreateRequestApiAgentType;
  description?: string;
  provider?: string;
  api_key?: string;
  assistant_id?: string;
  authentication_method?: AgentVersionCreateRequestApiAuthenticationMethod;
  language?: string;
  languages?: string[];
  contact_number?: string;
  inbound?: boolean;
  knowledge_base?: string;
  model?: string;
  model_details?: AgentVersionCreateRequestApiModelDetails;
  /** @maxLength 500 */
  livekit_url?: string;
  /** @maxLength 255 */
  livekit_api_key?: string;
  /** @maxLength 500 */
  livekit_api_secret?: string;
  /** @maxLength 255 */
  livekit_agent_name?: string;
  livekit_config_json?: AgentVersionCreateRequestApiLivekitConfigJson;
  /** @minimum 1 */
  livekit_max_concurrency?: number;
  commit_message?: string;
  observability_enabled?: boolean;
}

/**
 * Current status of this version
 */
export type AgentVersionResponseApiStatus = typeof AgentVersionResponseApiStatus[keyof typeof AgentVersionResponseApiStatus];


export const AgentVersionResponseApiStatus = {
  draft: 'draft',
  active: 'active',
  archived: 'archived',
  deprecated: 'deprecated',
} as const;

/**
 * Snapshot of agent configuration at this version
 */
export type AgentVersionResponseApiConfigurationSnapshot = { [key: string]: unknown };

export interface AgentVersionResponseApi {
  readonly id?: string;
  /** Version number of the agent */
  readonly version_number?: number;
  /**
     * Human-readable version name (e.g., 'v1.2.3')
     * @minLength 1
     */
  readonly version_name?: string;
  readonly version_name_display?: string;
  /** Current status of this version */
  readonly status?: AgentVersionResponseApiStatus;
  /** @minLength 1 */
  readonly status_display?: string;
  /** Performance score (0.0 to 10.0) */
  readonly score?: string;
  /** Number of tests run for this version */
  readonly test_count?: number;
  /** Test pass rate percentage */
  readonly pass_rate?: string;
  /**
     * Description of changes in this version
     * @minLength 1
     */
  readonly description?: string;
  /**
     * Commit message for the agent version
     * @minLength 1
     */
  readonly commit_message?: string;
  /**
     * Detailed release notes for this version
     * @minLength 1
     */
  readonly release_notes?: string;
  /** Parent agent definition */
  readonly agent_definition?: string;
  /** Organization this version belongs to */
  readonly organization?: string;
  /** Snapshot of agent configuration at this version */
  readonly configuration_snapshot?: AgentVersionResponseApiConfigurationSnapshot;
  readonly is_active?: string;
  readonly is_latest?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
}

export interface AgentVersionCreateResponseApi {
  /** @minLength 1 */
  readonly message?: string;
  version?: AgentVersionResponseApi;
}

export interface AgentVersionActivateResponseApi {
  /** @minLength 1 */
  readonly message?: string;
  version?: AgentVersionResponseApi;
}

/**
 * Current status of the call
 */
export type CallExecutionApiStatus = typeof CallExecutionApiStatus[keyof typeof CallExecutionApiStatus];


export const CallExecutionApiStatus = {
  pending: 'pending',
  queued: 'queued',
  ongoing: 'ongoing',
  completed: 'completed',
  failed: 'failed',
  analyzing: 'analyzing',
  cancelled: 'cancelled',
} as const;

/**
 * Additional metadata about the call
 */
export type CallExecutionApiCallMetadata = { [key: string]: unknown };

/**
 * Complete call data from the provider. Format: dict[provider_name, data] where provider_name must be from SupportedProviders
 */
export type CallExecutionApiProviderCallData = { [key: string]: unknown };

/**
 * Call analysis data from the service provider
 */
export type CallExecutionApiAnalysisData = { [key: string]: unknown };

/**
 * Call evaluation data from the service provider
 */
export type CallExecutionApiEvaluationData = { [key: string]: unknown };

/**
 * Evaluation output
 */
export type CallExecutionApiEvalOutputs = { [key: string]: unknown };

/**
 * Type of simulation call
 */
export type CallExecutionApiSimulationCallType = typeof CallExecutionApiSimulationCallType[keyof typeof CallExecutionApiSimulationCallType];


export const CallExecutionApiSimulationCallType = {
  voice: 'voice',
  text: 'text',
} as const;

export type CallExecutionErrorLocalizerTaskApiEvalResult = { [key: string]: unknown };

export type CallExecutionErrorLocalizerTaskApiInputData = { [key: string]: unknown };

export type CallExecutionErrorLocalizerTaskApiInputTypes = { [key: string]: unknown };

export type CallExecutionErrorLocalizerTaskApiErrorAnalysis = { [key: string]: unknown };

export interface CallExecutionErrorLocalizerTaskApi {
  /** @minLength 1 */
  task_id: string;
  eval_config_id: string;
  status: string;
  eval_result: CallExecutionErrorLocalizerTaskApiEvalResult;
  eval_explanation?: string;
  input_data?: CallExecutionErrorLocalizerTaskApiInputData;
  input_keys?: string[];
  input_types?: CallExecutionErrorLocalizerTaskApiInputTypes;
  rule_prompt?: string;
  error_analysis?: CallExecutionErrorLocalizerTaskApiErrorAnalysis;
  selected_input_key?: string;
  error_message?: string;
  created_at?: string;
  updated_at?: string;
}

export interface CallExecutionApi {
  readonly id?: string;
  /**
     * Phone number called (null for TEXT/chat simulations)
     * @maxLength 20
     */
  phone_number?: string;
  /** @minLength 1 */
  readonly service_provider_call_id?: string;
  /** Current status of the call */
  status?: CallExecutionApiStatus;
  /** When the call started */
  started_at?: string;
  /** When the call completed */
  completed_at?: string;
  /**
     * Duration of the call in seconds
     * @minimum -2147483648
     * @maximum 2147483647
     */
  duration_seconds?: number;
  /**
     * URL to the call recording
     * @maxLength 500
     */
  recording_url?: string;
  /**
     * Cost of the call in cents
     * @minimum -2147483648
     * @maximum 2147483647
     */
  cost_cents?: number;
  /** Additional metadata about the call */
  call_metadata?: CallExecutionApiCallMetadata;
  /** Error message if the call failed */
  error_message?: string;
  /** @minLength 1 */
  readonly scenario_name?: string;
  readonly transcripts?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
  /** Complete call data from the provider. Format: dict[provider_name, data] where provider_name must be from SupportedProviders */
  provider_call_data?: CallExecutionApiProviderCallData;
  /**
     * Stereo recording URL from Vapi
     * @maxLength 500
     */
  stereo_recording_url?: string;
  /**
     * Reason why the call ended
     * @maxLength 10000
     */
  ended_reason?: string;
  /**
     * STT cost in cents
     * @minimum -2147483648
     * @maximum 2147483647
     */
  stt_cost_cents?: number;
  /**
     * LLM cost in cents
     * @minimum -2147483648
     * @maximum 2147483647
     */
  llm_cost_cents?: number;
  /**
     * TTS cost in cents
     * @minimum -2147483648
     * @maximum 2147483647
     */
  tts_cost_cents?: number;
  /** Overall call performance score */
  overall_score?: number;
  /**
     * Average response time in milliseconds
     * @minimum -2147483648
     * @maximum 2147483647
     */
  response_time_ms?: number;
  readonly response_time_seconds?: string;
  /**
     * Assistant ID used for the call (system side)
     * @maxLength 255
     */
  assistant_id?: string;
  /**
     * Customer phone number (E.164 format)
     * @maxLength 20
     */
  customer_number?: string;
  /**
     * Type of call (e.g., outboundPhoneCall)
     * @maxLength 50
     */
  call_type?: string;
  /** When the call ended */
  ended_at?: string;
  /** Call analysis data from the service provider */
  analysis_data?: CallExecutionApiAnalysisData;
  /** Call evaluation data from the service provider */
  evaluation_data?: CallExecutionApiEvaluationData;
  /**
     * Number of messages in the call
     * @minimum -2147483648
     * @maximum 2147483647
     */
  message_count?: number;
  /** Whether transcript is available */
  transcript_available?: boolean;
  /** Whether recording is available */
  recording_available?: boolean;
  /** Evaluation output */
  eval_outputs?: CallExecutionApiEvalOutputs;
  /** Get error localizer tasks for this call execution. */
  readonly error_localizer_tasks?: readonly CallExecutionErrorLocalizerTaskApi[];
  /** Call summary from the service */
  call_summary?: string;
  agent_version?: string;
  /**
     * Total customer-reported cost in cents
     * @minimum -2147483648
     * @maximum 2147483647
     */
  customer_cost_cents?: number;
  readonly system_metrics?: string;
  readonly cost_breakdown?: string;
  /**
     * Customer call ID if available
     * @maxLength 255
     */
  customer_call_id?: string;
  /** Type of simulation call */
  simulation_call_type?: CallExecutionApiSimulationCallType;
  readonly processing_skipped?: string;
  readonly processing_skip_reason?: string;
}

export interface AgentVersionDeleteResponseApi {
  /** @minLength 1 */
  readonly message?: string;
}

export type EvalTemplateSummaryApiOutput = { [key: string]: unknown };

export interface EvalTemplateSummaryApi {
  /** @minLength 1 */
  name: string;
  /** @minLength 1 */
  id: string;
  total_cells: number;
  output: EvalTemplateSummaryApiOutput;
}

export interface EvalSummaryResponseApi {
  status?: boolean;
  result: EvalTemplateSummaryApi[];
}

export type EvalErrorResponseApiType = typeof EvalErrorResponseApiType[keyof typeof EvalErrorResponseApiType];


export const EvalErrorResponseApiType = {
  validation_error: 'validation_error',
  authentication_error: 'authentication_error',
  payment_required: 'payment_required',
  entitlement_error: 'entitlement_error',
  permission_error: 'permission_error',
  not_found: 'not_found',
  conflict: 'conflict',
  client_error: 'client_error',
  rate_limit: 'rate_limit',
  server_error: 'server_error',
  service_unavailable: 'service_unavailable',
  timeout: 'timeout',
  api_error: 'api_error',
} as const;

export type EvalErrorResponseApiDetails = {[key: string]: string[]};

export interface EvalErrorResponseApi {
  status?: boolean;
  type?: EvalErrorResponseApiType;
  code?: string;
  detail?: string;
  /** @minLength 1 */
  result?: string;
  /** @minLength 1 */
  message?: string;
  error?: string;
  attr?: string;
  details?: EvalErrorResponseApiDetails;
}

export type AgentVersionRestoreResponseApiAgent = {[key: string]: string};

export interface AgentVersionRestoreResponseApi {
  /** @minLength 1 */
  readonly message?: string;
  readonly agent?: AgentVersionRestoreResponseApiAgent;
  version?: AgentVersionResponseApi;
}

export type AgentDefinitionApiAgentType = typeof AgentDefinitionApiAgentType[keyof typeof AgentDefinitionApiAgentType];


export const AgentDefinitionApiAgentType = {
  voice: 'voice',
  text: 'text',
} as const;

/**
 * Language of the agent
 */
export type AgentDefinitionApiLanguage = typeof AgentDefinitionApiLanguage[keyof typeof AgentDefinitionApiLanguage];


export const AgentDefinitionApiLanguage = {
  ar: 'ar',
  bg: 'bg',
  zh: 'zh',
  cs: 'cs',
  da: 'da',
  nl: 'nl',
  en: 'en',
  fi: 'fi',
  fr: 'fr',
  de: 'de',
  el: 'el',
  hi: 'hi',
  hu: 'hu',
  id: 'id',
  it: 'it',
  ja: 'ja',
  ko: 'ko',
  ms: 'ms',
  no: 'no',
  pl: 'pl',
  pt: 'pt',
  ro: 'ro',
  ru: 'ru',
  sk: 'sk',
  es: 'es',
  sv: 'sv',
  tr: 'tr',
  uk: 'uk',
  vi: 'vi',
} as const;

/**
 * Language of the agent
 */
export type AgentDefinitionApiLanguagesItem = typeof AgentDefinitionApiLanguagesItem[keyof typeof AgentDefinitionApiLanguagesItem];


export const AgentDefinitionApiLanguagesItem = {
  ar: 'ar',
  bg: 'bg',
  zh: 'zh',
  cs: 'cs',
  da: 'da',
  nl: 'nl',
  en: 'en',
  fi: 'fi',
  fr: 'fr',
  de: 'de',
  el: 'el',
  hi: 'hi',
  hu: 'hu',
  id: 'id',
  it: 'it',
  ja: 'ja',
  ko: 'ko',
  ms: 'ms',
  no: 'no',
  pl: 'pl',
  pt: 'pt',
  ro: 'ro',
  ru: 'ru',
  sk: 'sk',
  es: 'es',
  sv: 'sv',
  tr: 'tr',
  uk: 'uk',
  vi: 'vi',
} as const;

export type AgentDefinitionApiAuthenticationMethod = typeof AgentDefinitionApiAuthenticationMethod[keyof typeof AgentDefinitionApiAuthenticationMethod];


export const AgentDefinitionApiAuthenticationMethod = {
  api_key: 'api_key',
} as const;

/**
 * Headers to be sent to the websocket server
 */
export type AgentDefinitionApiWebsocketHeaders = { [key: string]: unknown };

/**
 * Details of the model
 */
export type AgentDefinitionApiModelDetails = { [key: string]: unknown };

export interface AgentDefinitionApi {
  readonly id?: string;
  /**
     * Name of the AI agent
     * @minLength 1
     * @maxLength 255
     */
  agent_name: string;
  agent_type?: AgentDefinitionApiAgentType;
  /**
     * Phone number associated with the AI agent
     * @maxLength 50
     */
  contact_number?: string;
  /** Whether the agent handles inbound calls */
  inbound: boolean;
  /**
     * Detailed description of the AI agent's purpose and capabilities
     * @minLength 1
     */
  description: string;
  /**
     * External identifier for the assistant
     * @maxLength 255
     */
  assistant_id?: string;
  /**
     * Provider of the AI agent
     * @maxLength 255
     */
  provider?: string;
  /** Language of the agent */
  language?: AgentDefinitionApiLanguage;
  languages?: AgentDefinitionApiLanguagesItem[];
  authentication_method?: AgentDefinitionApiAuthenticationMethod;
  /**
     * WebSocket URL for real-time communication with the agent
     * @maxLength 500
     */
  websocket_url?: string;
  /** Headers to be sent to the websocket server */
  websocket_headers?: AgentDefinitionApiWebsocketHeaders;
  readonly workspace?: string;
  knowledge_base?: string;
  /** Organization this agent definition belongs to */
  readonly organization?: string;
  /**
     * API key for the agent
     * @maxLength 255
     */
  api_key?: string;
  readonly observability_provider?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
  /**
     * Model of the agent
     * @maxLength 255
     */
  model?: string;
  /** Details of the model */
  model_details?: AgentDefinitionApiModelDetails;
  /** @maxLength 500 */
  livekit_url?: string;
  /** @maxLength 255 */
  livekit_api_key?: string;
  livekit_api_secret?: string;
}

/**
 * Voice provider. One of: vapi, retell, eleven_labs, others.
 */
export type FetchAssistantRequestApiProvider = typeof FetchAssistantRequestApiProvider[keyof typeof FetchAssistantRequestApiProvider];


export const FetchAssistantRequestApiProvider = {
  vapi: 'vapi',
  retell: 'retell',
  eleven_labs: 'eleven_labs',
  others: 'others',
} as const;

export interface FetchAssistantRequestApi {
  /** @minLength 1 */
  assistant_id: string;
  /** @minLength 1 */
  api_key: string;
  agent_id?: string;
  /** Voice provider. One of: vapi, retell, eleven_labs, others. */
  provider?: FetchAssistantRequestApiProvider;
}

export interface FetchAssistantResponseApi {
  /** @minLength 1 */
  readonly name?: string;
  /** @minLength 1 */
  readonly assistant_id?: string;
  /** @minLength 1 */
  readonly prompt?: string;
  /** @minLength 1 */
  readonly provider?: string;
  /** @minLength 1 */
  readonly commit_message?: string;
}

export type AgentPromptOptimiserRunListApiOptimiserType = typeof AgentPromptOptimiserRunListApiOptimiserType[keyof typeof AgentPromptOptimiserRunListApiOptimiserType];


export const AgentPromptOptimiserRunListApiOptimiserType = {
  random_search: 'random_search',
  gepa: 'gepa',
  protegi: 'protegi',
  bayesian: 'bayesian',
  metaprompt: 'metaprompt',
  promptwizard: 'promptwizard',
} as const;

export type AgentPromptOptimiserRunListApiStatus = typeof AgentPromptOptimiserRunListApiStatus[keyof typeof AgentPromptOptimiserRunListApiStatus];


export const AgentPromptOptimiserRunListApiStatus = {
  pending: 'pending',
  running: 'running',
  completed: 'completed',
  failed: 'failed',
} as const;

export type AgentPromptOptimiserRunListApiConfiguration = { [key: string]: unknown };

export interface AgentPromptOptimiserRunListApi {
  readonly id?: string;
  /** @minLength 1 */
  optimisation_name: string;
  started_at: string;
  readonly no_of_trials?: string;
  optimiser_type: AgentPromptOptimiserRunListApiOptimiserType;
  status?: AgentPromptOptimiserRunListApiStatus;
  error_message?: string;
  configuration?: AgentPromptOptimiserRunListApiConfiguration;
  /**
     * LLM model used for the optimiser run
     * @minLength 1
     * @maxLength 255
     */
  model: string;
}

export type AgentPromptOptimiserRunCreateApiOptimiserType = typeof AgentPromptOptimiserRunCreateApiOptimiserType[keyof typeof AgentPromptOptimiserRunCreateApiOptimiserType];


export const AgentPromptOptimiserRunCreateApiOptimiserType = {
  random_search: 'random_search',
  gepa: 'gepa',
  protegi: 'protegi',
  bayesian: 'bayesian',
  metaprompt: 'metaprompt',
  promptwizard: 'promptwizard',
} as const;

export type AgentPromptOptimiserRunCreateApiConfiguration = { [key: string]: unknown };

export interface AgentPromptOptimiserRunCreateApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  test_execution_id: string;
  optimiser_type: AgentPromptOptimiserRunCreateApiOptimiserType;
  /**
     * LLM model used for the optimiser run
     * @minLength 1
     * @maxLength 255
     */
  model: string;
  configuration?: AgentPromptOptimiserRunCreateApiConfiguration;
  readonly created_at?: string;
  readonly updated_at?: string;
}

export interface AgentPromptOptimiserConfigurationApi {
  num_gradients?: number;
  errors_per_gradient?: number;
  prompts_per_gradient?: number;
  beam_size?: number;
  num_rounds?: number;
  num_variations?: number;
  max_metric_calls?: number;
  min_examples?: number;
  max_examples?: number;
  n_trials?: number;
  task_description?: string;
  mutate_rounds?: number;
  refine_iterations?: number;
}

export type AgentPromptOptimiserParameterApiValue = { [key: string]: unknown };

export interface AgentPromptOptimiserParameterApi {
  /** @minLength 1 */
  readonly key?: string;
  /** @minLength 1 */
  readonly label?: string;
  readonly value?: AgentPromptOptimiserParameterApiValue;
  readonly description?: string;
}

export interface AgentPromptOptimiserTrialEvalScoreApi {
  readonly score?: number;
  readonly percentage_change?: number;
}

/**
 * Dynamic eval-config UUID keys are returned as top-level row keys at runtime.
 */
export type AgentPromptOptimiserTrialTableRowApiEvalScores = {[key: string]: AgentPromptOptimiserTrialEvalScoreApi};

export interface AgentPromptOptimiserTrialTableRowApi {
  readonly id?: string;
  /** @minLength 1 */
  readonly trial?: string;
  readonly score?: number;
  readonly score_percentage_change?: number;
  readonly prompt?: string;
  readonly is_best?: boolean;
  /** Dynamic eval-config UUID keys are returned as top-level row keys at runtime. */
  readonly eval_scores?: AgentPromptOptimiserTrialTableRowApiEvalScores;
}

export interface AgentPromptOptimiserColumnConfigApi {
  /** @minLength 1 */
  readonly id?: string;
  /** @minLength 1 */
  readonly name?: string;
  readonly is_visible?: boolean;
}

export interface AgentPromptOptimiserRunDetailResultApi {
  /** @minLength 1 */
  readonly optimiser_name?: string;
  /** @minLength 1 */
  readonly optimiser_type?: string;
  /** @minLength 1 */
  readonly model?: string;
  /** @minLength 1 */
  readonly provider_logo?: string;
  configuration?: AgentPromptOptimiserConfigurationApi;
  readonly parameters?: readonly AgentPromptOptimiserParameterApi[];
  readonly start_time?: string;
  /** @minLength 1 */
  readonly status?: string;
  /** @minLength 1 */
  readonly error_message?: string;
  readonly table?: readonly AgentPromptOptimiserTrialTableRowApi[];
  readonly column_config?: readonly AgentPromptOptimiserColumnConfigApi[];
}

export interface AgentPromptOptimiserRunDetailResponseApi {
  status?: boolean;
  result: AgentPromptOptimiserRunDetailResultApi;
}

export type AgentPromptOptimiserRunApiOptimiserType = typeof AgentPromptOptimiserRunApiOptimiserType[keyof typeof AgentPromptOptimiserRunApiOptimiserType];


export const AgentPromptOptimiserRunApiOptimiserType = {
  random_search: 'random_search',
  gepa: 'gepa',
  protegi: 'protegi',
  bayesian: 'bayesian',
  metaprompt: 'metaprompt',
  promptwizard: 'promptwizard',
} as const;

export type AgentPromptOptimiserRunApiStatus = typeof AgentPromptOptimiserRunApiStatus[keyof typeof AgentPromptOptimiserRunApiStatus];


export const AgentPromptOptimiserRunApiStatus = {
  pending: 'pending',
  running: 'running',
  completed: 'completed',
  failed: 'failed',
} as const;

export type AgentPromptOptimiserRunApiResult = { [key: string]: unknown };

export type AgentPromptOptimiserRunApiConfiguration = { [key: string]: unknown };

export interface AgentPromptOptimiserRunApi {
  readonly id?: string;
  agent_optimiser: string;
  agent_optimiser_run: string;
  test_execution: string;
  optimiser_type: AgentPromptOptimiserRunApiOptimiserType;
  /**
     * LLM model used for the optimiser run
     * @minLength 1
     * @maxLength 255
     */
  model: string;
  status?: AgentPromptOptimiserRunApiStatus;
  result?: AgentPromptOptimiserRunApiResult;
  configuration?: AgentPromptOptimiserRunApiConfiguration;
}

export type AgentPromptOptimiserRunModelResponseApiOptimiserType = typeof AgentPromptOptimiserRunModelResponseApiOptimiserType[keyof typeof AgentPromptOptimiserRunModelResponseApiOptimiserType];


export const AgentPromptOptimiserRunModelResponseApiOptimiserType = {
  random_search: 'random_search',
  gepa: 'gepa',
  protegi: 'protegi',
  bayesian: 'bayesian',
  metaprompt: 'metaprompt',
  promptwizard: 'promptwizard',
} as const;

export type AgentPromptOptimiserRunModelResponseApiStatus = typeof AgentPromptOptimiserRunModelResponseApiStatus[keyof typeof AgentPromptOptimiserRunModelResponseApiStatus];


export const AgentPromptOptimiserRunModelResponseApiStatus = {
  pending: 'pending',
  running: 'running',
  completed: 'completed',
  failed: 'failed',
} as const;

export interface AgentPromptOptimiserComponentEvalResultApi {
  score?: number;
  reason?: string;
}

export type AgentPromptOptimiserIndividualResultMetadataApiComponentEvals = {[key: string]: AgentPromptOptimiserComponentEvalResultApi};

export interface AgentPromptOptimiserIndividualResultMetadataApi {
  input?: string;
  output?: string;
  component_evals?: AgentPromptOptimiserIndividualResultMetadataApiComponentEvals;
}

export interface AgentPromptOptimiserIndividualResultApi {
  score?: number;
  reason?: string;
  metadata?: AgentPromptOptimiserIndividualResultMetadataApi;
}

export type AgentPromptOptimiserRawTrialResultApiIndividualResults = {[key: string]: AgentPromptOptimiserIndividualResultApi};

export interface AgentPromptOptimiserRawTrialResultApi {
  prompt?: string;
  average_score?: number;
  is_baseline?: boolean;
  individual_results?: AgentPromptOptimiserRawTrialResultApiIndividualResults;
}

export interface AgentPromptOptimiserRawResultApi {
  history?: AgentPromptOptimiserRawTrialResultApi[];
  best_prompt?: string;
  final_score?: number;
  best_score?: number;
  trials_run?: number;
  error?: string;
}

export interface AgentPromptOptimiserRunModelResponseApi {
  readonly id?: string;
  readonly agent_optimiser?: string;
  readonly agent_optimiser_run?: string;
  readonly test_execution?: string;
  readonly optimiser_type?: AgentPromptOptimiserRunModelResponseApiOptimiserType;
  /** @minLength 1 */
  readonly model?: string;
  readonly status?: AgentPromptOptimiserRunModelResponseApiStatus;
  result?: AgentPromptOptimiserRawResultApi;
  configuration?: AgentPromptOptimiserConfigurationApi;
}

export interface AgentPromptOptimiserGraphEvaluationApi {
  readonly trial_id?: string;
  readonly trial_number?: number;
  /** @minLength 1 */
  readonly trial_name?: string;
  readonly score?: number;
}

export interface AgentPromptOptimiserGraphSeriesApi {
  /** @minLength 1 */
  readonly name?: string;
  readonly evaluations?: readonly AgentPromptOptimiserGraphEvaluationApi[];
}

/**
 * Dictionary keyed by eval-config UUID.
 */
export type AgentPromptOptimiserGraphResponseApiResult = {[key: string]: AgentPromptOptimiserGraphSeriesApi};

export interface AgentPromptOptimiserGraphResponseApi {
  status?: boolean;
  /** Dictionary keyed by eval-config UUID. */
  result: AgentPromptOptimiserGraphResponseApiResult;
}

export type AgentPromptOptimiserRunStepApiStatus = typeof AgentPromptOptimiserRunStepApiStatus[keyof typeof AgentPromptOptimiserRunStepApiStatus];


export const AgentPromptOptimiserRunStepApiStatus = {
  pending: 'pending',
  running: 'running',
  completed: 'completed',
  failed: 'failed',
} as const;

export type AgentPromptOptimiserRunStepApiMetadata = { [key: string]: unknown };

export interface AgentPromptOptimiserRunStepApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  description?: string;
  status?: AgentPromptOptimiserRunStepApiStatus;
  metadata?: AgentPromptOptimiserRunStepApiMetadata;
  /**
     * @minimum -2147483648
     * @maximum 2147483647
     */
  step_number: number;
  readonly created_at?: string;
  readonly updated_at?: string;
}

export interface AgentPromptOptimiserRunStepsResponseApi {
  status?: boolean;
  result: AgentPromptOptimiserRunStepApi[];
}

export interface AgentPromptOptimiserTrialEvaluationRowApi {
  readonly id?: string;
  /** @minLength 1 */
  readonly eval_name?: string;
  readonly eval_template_description?: string;
  readonly score?: number;
  readonly score_percentage_change?: number;
}

export interface AgentPromptOptimiserTrialEvaluationsResultApi {
  /** @minLength 1 */
  readonly trial_name?: string;
  /** @minLength 1 */
  readonly optimisation_name?: string;
  readonly created_at?: string;
  readonly score?: number;
  readonly score_percentage_change?: number;
  readonly table?: readonly AgentPromptOptimiserTrialEvaluationRowApi[];
  readonly column_config?: readonly AgentPromptOptimiserColumnConfigApi[];
}

export interface AgentPromptOptimiserTrialEvaluationsResponseApi {
  status?: boolean;
  result: AgentPromptOptimiserTrialEvaluationsResultApi;
}

export interface AgentPromptOptimiserTrialPromptResultApi {
  /** @minLength 1 */
  readonly trial_name?: string;
  /** @minLength 1 */
  readonly optimisation_name?: string;
  readonly created_at?: string;
  readonly score?: number;
  readonly score_percentage_change?: number;
  /** @minLength 1 */
  readonly trial_prompt?: string;
  /** @minLength 1 */
  readonly base_prompt?: string;
}

export interface AgentPromptOptimiserTrialPromptResponseApi {
  status?: boolean;
  result: AgentPromptOptimiserTrialPromptResultApi;
}

/**
 * Dynamic eval-config UUID keys are returned as top-level row keys at runtime.
 */
export type AgentPromptOptimiserTrialScenarioRowApiEvalScores = {[key: string]: number};

export interface AgentPromptOptimiserTrialScenarioRowApi {
  readonly id?: string;
  readonly input_text?: string;
  readonly output_text?: string;
  /** Dynamic eval-config UUID keys are returned as top-level row keys at runtime. */
  readonly eval_scores?: AgentPromptOptimiserTrialScenarioRowApiEvalScores;
}

export interface AgentPromptOptimiserTrialScenariosResultApi {
  /** @minLength 1 */
  readonly trial_name?: string;
  /** @minLength 1 */
  readonly optimisation_name?: string;
  readonly created_at?: string;
  readonly score?: number;
  readonly score_percentage_change?: number;
  readonly table?: readonly AgentPromptOptimiserTrialScenarioRowApi[];
  readonly column_config?: readonly AgentPromptOptimiserColumnConfigApi[];
}

export interface AgentPromptOptimiserTrialScenariosResponseApi {
  status?: boolean;
  result: AgentPromptOptimiserTrialScenariosResultApi;
}

export type CallExecutionErrorResponseApiType = typeof CallExecutionErrorResponseApiType[keyof typeof CallExecutionErrorResponseApiType];


export const CallExecutionErrorResponseApiType = {
  validation_error: 'validation_error',
  authentication_error: 'authentication_error',
  payment_required: 'payment_required',
  entitlement_error: 'entitlement_error',
  permission_error: 'permission_error',
  not_found: 'not_found',
  conflict: 'conflict',
  client_error: 'client_error',
  rate_limit: 'rate_limit',
  server_error: 'server_error',
  service_unavailable: 'service_unavailable',
  timeout: 'timeout',
  api_error: 'api_error',
} as const;

export type CallExecutionErrorResponseApiDetails = {[key: string]: string[]};

export interface CallExecutionErrorResponseApi {
  status?: boolean;
  type?: CallExecutionErrorResponseApiType;
  code?: string;
  detail?: string;
  /** @minLength 1 */
  result?: string;
  /** @minLength 1 */
  message?: string;
  error?: string;
  attr?: string;
  details?: CallExecutionErrorResponseApiDetails;
}

export type LiveKitCallConfigResponseApiCallMetadata = {[key: string]: { [key: string]: unknown }};

export type LiveKitCallConfigResponseApiProviderCallData = {[key: string]: { [key: string]: unknown }};

export interface LiveKitCallConfigResponseApi {
  id: string;
  call_metadata: LiveKitCallConfigResponseApiCallMetadata;
  provider_call_data: LiveKitCallConfigResponseApiProviderCallData;
  /** @minLength 1 */
  status: string;
  ended_reason: string;
  duration_seconds: number;
}

export type LiveKitErrorResponseApiType = typeof LiveKitErrorResponseApiType[keyof typeof LiveKitErrorResponseApiType];


export const LiveKitErrorResponseApiType = {
  validation_error: 'validation_error',
  authentication_error: 'authentication_error',
  payment_required: 'payment_required',
  entitlement_error: 'entitlement_error',
  permission_error: 'permission_error',
  not_found: 'not_found',
  conflict: 'conflict',
  client_error: 'client_error',
  rate_limit: 'rate_limit',
  server_error: 'server_error',
  service_unavailable: 'service_unavailable',
  timeout: 'timeout',
  api_error: 'api_error',
} as const;

export type LiveKitErrorResponseApiDetails = {[key: string]: string[]};

export interface LiveKitErrorResponseApi {
  status?: boolean;
  type?: LiveKitErrorResponseApiType;
  code?: string;
  detail?: string;
  /** @minLength 1 */
  result?: string;
  /** @minLength 1 */
  message?: string;
  error?: string;
  attr?: string;
  details?: LiveKitErrorResponseApiDetails;
}

export type LiveKitCallExecutionUpdateRequestApiProviderCallData = {[key: string]: { [key: string]: unknown }};

export interface LiveKitCallExecutionUpdateRequestApi {
  provider_call_data?: LiveKitCallExecutionUpdateRequestApiProviderCallData;
  started_at?: string;
  completed_at?: string;
  ended_at?: string;
  /** @minimum 0 */
  duration_seconds?: number;
  ended_reason?: string;
  service_provider_call_id?: string;
}

export interface LiveKitOkResponseApi {
  ok: boolean;
}

export interface LiveKitListenerTokenResultApi {
  /** @minLength 1 */
  token: string;
  /** @minLength 1 */
  url: string;
  /** @minLength 1 */
  room_name: string;
}

export interface LiveKitListenerTokenResponseApi {
  status?: boolean;
  result: LiveKitListenerTokenResultApi;
}

export type LiveKitPhoneResolutionResponseApiCallMetadata = {[key: string]: { [key: string]: unknown }};

export type LiveKitPhoneResolutionResponseApiProviderCallData = {[key: string]: { [key: string]: unknown }};

export interface LiveKitPhoneResolutionResponseApi {
  call_id: string;
  call_metadata: LiveKitPhoneResolutionResponseApiCallMetadata;
  provider_call_data: LiveKitPhoneResolutionResponseApiProviderCallData;
  /** @minLength 1 */
  status: string;
}

export interface LiveKitTemporalSignalRequestApi {
  workflow_id?: string;
  call_id?: string;
  /** @minLength 1 */
  status?: string;
  /** @minimum 0 */
  duration_seconds?: number;
  end_reason?: string;
}

export interface LiveKitTranscriptRowApi {
  /** @minLength 1 */
  role?: string;
  /** @minLength 1 */
  content?: string;
  start_time_ms?: number;
  end_time_ms?: number;
}

export interface LiveKitTranscriptsRequestApi {
  /** @minLength 1 */
  role?: string;
  /** @minLength 1 */
  content?: string;
  start_time_ms?: number;
  end_time_ms?: number;
  transcripts?: LiveKitTranscriptRowApi[];
}

export interface LiveKitTranscriptCreatedResponseApi {
  id?: string;
  created?: number;
}

export interface ValidateLiveKitCredentialsRequestApi {
  /** @minLength 1 */
  livekit_url: string;
  /** @minLength 1 */
  api_key: string;
  /** @minLength 1 */
  api_secret: string;
  agent_name?: string;
  agent_definition_id?: string;
}

export interface ValidateLiveKitCredentialsResultApi {
  valid: boolean;
  error?: string;
}

export interface ValidateLiveKitCredentialsResponseApi {
  status?: boolean;
  result: ValidateLiveKitCredentialsResultApi;
}

/**
 * Type of persona (system or workspace-level)
 */
export type PersonaListApiPersonaType = typeof PersonaListApiPersonaType[keyof typeof PersonaListApiPersonaType];


export const PersonaListApiPersonaType = {
  system: 'system',
  workspace: 'workspace',
} as const;

/**
 * List of genders for the persona (e.g., ['male'], ['female'])
 */
export type PersonaListApiGender = { [key: string]: unknown };

/**
 * List of age groups for the persona (e.g., ['18-25'], ['25-32'])
 */
export type PersonaListApiAgeGroup = { [key: string]: unknown };

/**
 * List of occupations/professions for the persona (e.g., ['Engineer'], ['Teacher'])
 */
export type PersonaListApiOccupation = { [key: string]: unknown };

/**
 * List of locations for the persona (e.g., ['United States'], ['Canada'])
 */
export type PersonaListApiLocation = { [key: string]: unknown };

/**
 * List of personality types for the persona (e.g., ['Friendly and cooperative'])
 */
export type PersonaListApiPersonality = { [key: string]: unknown };

/**
 * List of communication styles for the persona (e.g., ['Direct and concise'])
 */
export type PersonaListApiCommunicationStyle = { [key: string]: unknown };

/**
 * List of languages the persona speaks (e.g., ['English', 'Hindi'])
 */
export type PersonaListApiLanguages = { [key: string]: unknown };

/**
 * List of accents for the persona (e.g., ['American'], ['Australian'])
 */
export type PersonaListApiAccent = { [key: string]: unknown };

/**
 * List of conversation speeds (e.g., ['1.0'], ['1.25'])
 */
export type PersonaListApiConversationSpeed = { [key: string]: unknown };

/**
 * List of sensitivities for detecting when persona finished speaking (e.g., ['5'], ['6'])
 */
export type PersonaListApiFinishedSpeakingSensitivity = { [key: string]: unknown };

/**
 * List of sensitivities for allowing interruptions (e.g., ['5'], ['6'])
 */
export type PersonaListApiInterruptSensitivity = { [key: string]: unknown };

/**
 * List of keywords/tags describing the persona (e.g., ['Knowledgeable', 'Patient', 'Helpful'])
 */
export type PersonaListApiKeywords = { [key: string]: unknown };

/**
 * Additional metadata for the persona (speech clarity, base emotion, etc.)
 */
export type PersonaListApiMetadata = { [key: string]: unknown };

/**
 * Punctuation style for the persona
 */
export type PersonaListApiPunctuation = typeof PersonaListApiPunctuation[keyof typeof PersonaListApiPunctuation];


export const PersonaListApiPunctuation = {
  clean: 'clean',
  minimal: 'minimal',
  expressive: 'expressive',
  erratic: 'erratic',
} as const;

/**
 * Slang usage for the persona
 */
export type PersonaListApiSlangUsage = typeof PersonaListApiSlangUsage[keyof typeof PersonaListApiSlangUsage];


export const PersonaListApiSlangUsage = {
  none: 'none',
  moderate: 'moderate',
  heavy: 'heavy',
  light: 'light',
} as const;

/**
 * Typos frequency for the persona
 */
export type PersonaListApiTyposFrequency = typeof PersonaListApiTyposFrequency[keyof typeof PersonaListApiTyposFrequency];


export const PersonaListApiTyposFrequency = {
  none: 'none',
  rare: 'rare',
  occasional: 'occasional',
  frequent: 'frequent',
} as const;

/**
 * Regional mix for the persona
 */
export type PersonaListApiRegionalMix = typeof PersonaListApiRegionalMix[keyof typeof PersonaListApiRegionalMix];


export const PersonaListApiRegionalMix = {
  none: 'none',
  moderate: 'moderate',
  heavy: 'heavy',
  light: 'light',
} as const;

/**
 * Emoji usage for the persona
 */
export type PersonaListApiEmojiUsage = typeof PersonaListApiEmojiUsage[keyof typeof PersonaListApiEmojiUsage];


export const PersonaListApiEmojiUsage = {
  never: 'never',
  light: 'light',
  regular: 'regular',
  heavy: 'heavy',
} as const;

/**
 * Tone for the persona
 */
export type PersonaListApiTone = typeof PersonaListApiTone[keyof typeof PersonaListApiTone];


export const PersonaListApiTone = {
  formal: 'formal',
  casual: 'casual',
  neutral: 'neutral',
} as const;

/**
 * Verbosity for the persona
 */
export type PersonaListApiVerbosity = typeof PersonaListApiVerbosity[keyof typeof PersonaListApiVerbosity];


export const PersonaListApiVerbosity = {
  brief: 'brief',
  balanced: 'balanced',
  detailed: 'detailed',
} as const;

export interface PersonaListApi {
  readonly id?: string;
  /** Type of persona (system or workspace-level) */
  readonly persona_type?: PersonaListApiPersonaType;
  /** @minLength 1 */
  readonly persona_type_display?: string;
  /**
     * Name of the persona
     * @minLength 1
     */
  readonly name?: string;
  /**
     * Description of the persona
     * @minLength 1
     */
  readonly description?: string;
  /** List of genders for the persona (e.g., ['male'], ['female']) */
  readonly gender?: PersonaListApiGender;
  /** List of age groups for the persona (e.g., ['18-25'], ['25-32']) */
  readonly age_group?: PersonaListApiAgeGroup;
  /** List of occupations/professions for the persona (e.g., ['Engineer'], ['Teacher']) */
  readonly occupation?: PersonaListApiOccupation;
  /** List of locations for the persona (e.g., ['United States'], ['Canada']) */
  readonly location?: PersonaListApiLocation;
  /** List of personality types for the persona (e.g., ['Friendly and cooperative']) */
  readonly personality?: PersonaListApiPersonality;
  /** List of communication styles for the persona (e.g., ['Direct and concise']) */
  readonly communication_style?: PersonaListApiCommunicationStyle;
  /** Whether the persona supports multiple languages */
  readonly multilingual?: boolean;
  /** List of languages the persona speaks (e.g., ['English', 'Hindi']) */
  readonly languages?: PersonaListApiLanguages;
  /** List of accents for the persona (e.g., ['American'], ['Australian']) */
  readonly accent?: PersonaListApiAccent;
  /** List of conversation speeds (e.g., ['1.0'], ['1.25']) */
  readonly conversation_speed?: PersonaListApiConversationSpeed;
  /** Whether background sound is enabled (null=not specified, True/False for enabled/disabled) */
  readonly background_sound?: boolean;
  /** List of sensitivities for detecting when persona finished speaking (e.g., ['5'], ['6']) */
  readonly finished_speaking_sensitivity?: PersonaListApiFinishedSpeakingSensitivity;
  /** List of sensitivities for allowing interruptions (e.g., ['5'], ['6']) */
  readonly interrupt_sensitivity?: PersonaListApiInterruptSensitivity;
  /** List of keywords/tags describing the persona (e.g., ['Knowledgeable', 'Patient', 'Helpful']) */
  readonly keywords?: PersonaListApiKeywords;
  /** Additional metadata for the persona (speech clarity, base emotion, etc.) */
  readonly metadata?: PersonaListApiMetadata;
  /**
     * Additional instructions for how this persona should behave
     * @minLength 1
     */
  readonly additional_instruction?: string;
  /** Whether this is a default/recommended persona */
  readonly is_default?: boolean;
  readonly created_at?: string;
  readonly updated_at?: string;
  readonly simulation_type?: string;
  /** Punctuation style for the persona */
  readonly punctuation?: PersonaListApiPunctuation;
  /** Slang usage for the persona */
  readonly slang_usage?: PersonaListApiSlangUsage;
  /** Typos frequency for the persona */
  readonly typos_frequency?: PersonaListApiTyposFrequency;
  /** Regional mix for the persona */
  readonly regional_mix?: PersonaListApiRegionalMix;
  /** Emoji usage for the persona */
  readonly emoji_usage?: PersonaListApiEmojiUsage;
  /** Tone for the persona */
  readonly tone?: PersonaListApiTone;
  /** Verbosity for the persona */
  readonly verbosity?: PersonaListApiVerbosity;
}

export type PersonaCreateApiCustomProperties = { [key: string]: unknown };

export interface PersonaCreateApi {
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  /** @minLength 1 */
  description: string;
  gender?: string[];
  age_group?: string[];
  location?: string[];
  profession?: string[];
  personality?: string[];
  communication_style?: string[];
  accent?: string[];
  multilingual?: boolean;
  language?: string[];
  conversation_speed?: string[];
  background_sound?: boolean;
  finished_speaking_sensitivity?: string[];
  interrupt_sensitivity?: string[];
  keywords?: string[];
  custom_properties?: PersonaCreateApiCustomProperties;
  additional_instruction?: string;
  simulation_type?: string;
  tone?: string;
  punctuation?: string;
  slang_usage?: string;
  typos_frequency?: string;
  regional_mix?: string;
  emoji_usage?: string;
  verbosity?: string;
}

export interface PersonaDuplicateRequestApi {
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
}

/**
 * Type of persona (system or workspace-level)
 */
export type PersonaApiPersonaType = typeof PersonaApiPersonaType[keyof typeof PersonaApiPersonaType];


export const PersonaApiPersonaType = {
  system: 'system',
  workspace: 'workspace',
} as const;

/**
 * Type of simulation for the persona
 */
export type PersonaApiSimulationType = typeof PersonaApiSimulationType[keyof typeof PersonaApiSimulationType];


export const PersonaApiSimulationType = {
  voice: 'voice',
  text: 'text',
} as const;

/**
 * Punctuation style for the persona
 */
export type PersonaApiPunctuation = typeof PersonaApiPunctuation[keyof typeof PersonaApiPunctuation];


export const PersonaApiPunctuation = {
  clean: 'clean',
  minimal: 'minimal',
  expressive: 'expressive',
  erratic: 'erratic',
} as const;

/**
 * Slang usage for the persona
 */
export type PersonaApiSlangUsage = typeof PersonaApiSlangUsage[keyof typeof PersonaApiSlangUsage];


export const PersonaApiSlangUsage = {
  none: 'none',
  moderate: 'moderate',
  heavy: 'heavy',
  light: 'light',
} as const;

/**
 * Typos frequency for the persona
 */
export type PersonaApiTyposFrequency = typeof PersonaApiTyposFrequency[keyof typeof PersonaApiTyposFrequency];


export const PersonaApiTyposFrequency = {
  none: 'none',
  rare: 'rare',
  occasional: 'occasional',
  frequent: 'frequent',
} as const;

/**
 * Regional mix for the persona
 */
export type PersonaApiRegionalMix = typeof PersonaApiRegionalMix[keyof typeof PersonaApiRegionalMix];


export const PersonaApiRegionalMix = {
  none: 'none',
  moderate: 'moderate',
  heavy: 'heavy',
  light: 'light',
} as const;

/**
 * Emoji usage for the persona
 */
export type PersonaApiEmojiUsage = typeof PersonaApiEmojiUsage[keyof typeof PersonaApiEmojiUsage];


export const PersonaApiEmojiUsage = {
  never: 'never',
  light: 'light',
  regular: 'regular',
  heavy: 'heavy',
} as const;

/**
 * Tone for the persona
 */
export type PersonaApiTone = typeof PersonaApiTone[keyof typeof PersonaApiTone];


export const PersonaApiTone = {
  formal: 'formal',
  casual: 'casual',
  neutral: 'neutral',
} as const;

/**
 * Verbosity for the persona
 */
export type PersonaApiVerbosity = typeof PersonaApiVerbosity[keyof typeof PersonaApiVerbosity];


export const PersonaApiVerbosity = {
  brief: 'brief',
  balanced: 'balanced',
  detailed: 'detailed',
} as const;

/**
 * List of genders for the persona (e.g., ['male'], ['female'])
 */
export type PersonaApiGender = { [key: string]: unknown };

/**
 * List of age groups for the persona (e.g., ['18-25'], ['25-32'])
 */
export type PersonaApiAgeGroup = { [key: string]: unknown };

/**
 * List of occupations/professions for the persona (e.g., ['Engineer'], ['Teacher'])
 */
export type PersonaApiOccupation = { [key: string]: unknown };

/**
 * List of locations for the persona (e.g., ['United States'], ['Canada'])
 */
export type PersonaApiLocation = { [key: string]: unknown };

/**
 * List of personality types for the persona (e.g., ['Friendly and cooperative'])
 */
export type PersonaApiPersonality = { [key: string]: unknown };

/**
 * List of communication styles for the persona (e.g., ['Direct and concise'])
 */
export type PersonaApiCommunicationStyle = { [key: string]: unknown };

/**
 * List of languages the persona speaks (e.g., ['English', 'Hindi'])
 */
export type PersonaApiLanguages = { [key: string]: unknown };

/**
 * List of accents for the persona (e.g., ['American'], ['Australian'])
 */
export type PersonaApiAccent = { [key: string]: unknown };

/**
 * List of conversation speeds (e.g., ['1.0'], ['1.25'])
 */
export type PersonaApiConversationSpeed = { [key: string]: unknown };

/**
 * List of sensitivities for detecting when persona finished speaking (e.g., ['5'], ['6'])
 */
export type PersonaApiFinishedSpeakingSensitivity = { [key: string]: unknown };

/**
 * List of sensitivities for allowing interruptions (e.g., ['5'], ['6'])
 */
export type PersonaApiInterruptSensitivity = { [key: string]: unknown };

/**
 * List of keywords/tags describing the persona (e.g., ['Knowledgeable', 'Patient', 'Helpful'])
 */
export type PersonaApiKeywords = { [key: string]: unknown };

/**
 * Additional metadata for the persona (speech clarity, base emotion, etc.)
 */
export type PersonaApiMetadata = { [key: string]: unknown };

export type PersonaApiCustomProperties = { [key: string]: unknown };

export interface PersonaApi {
  readonly id?: string;
  /** Type of persona (system or workspace-level) */
  readonly persona_type?: PersonaApiPersonaType;
  /** @minLength 1 */
  readonly persona_type_display?: string;
  /**
     * Name of the persona
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  /** Description of the persona */
  description?: string;
  /** List of genders for the persona (e.g., ['male'], ['female']) */
  gender?: PersonaApiGender;
  /** List of age groups for the persona (e.g., ['18-25'], ['25-32']) */
  age_group?: PersonaApiAgeGroup;
  /** List of occupations/professions for the persona (e.g., ['Engineer'], ['Teacher']) */
  occupation?: PersonaApiOccupation;
  /** List of locations for the persona (e.g., ['United States'], ['Canada']) */
  location?: PersonaApiLocation;
  /** List of personality types for the persona (e.g., ['Friendly and cooperative']) */
  personality?: PersonaApiPersonality;
  /** List of communication styles for the persona (e.g., ['Direct and concise']) */
  communication_style?: PersonaApiCommunicationStyle;
  /** Whether the persona supports multiple languages */
  multilingual?: boolean;
  /** List of languages the persona speaks (e.g., ['English', 'Hindi']) */
  languages?: PersonaApiLanguages;
  /** List of accents for the persona (e.g., ['American'], ['Australian']) */
  accent?: PersonaApiAccent;
  /** List of conversation speeds (e.g., ['1.0'], ['1.25']) */
  conversation_speed?: PersonaApiConversationSpeed;
  /** Whether background sound is enabled (null=not specified, True/False for enabled/disabled) */
  background_sound?: boolean;
  /** List of sensitivities for detecting when persona finished speaking (e.g., ['5'], ['6']) */
  finished_speaking_sensitivity?: PersonaApiFinishedSpeakingSensitivity;
  /** List of sensitivities for allowing interruptions (e.g., ['5'], ['6']) */
  interrupt_sensitivity?: PersonaApiInterruptSensitivity;
  /** List of keywords/tags describing the persona (e.g., ['Knowledgeable', 'Patient', 'Helpful']) */
  keywords?: PersonaApiKeywords;
  /** Additional metadata for the persona (speech clarity, base emotion, etc.) */
  metadata?: PersonaApiMetadata;
  /** Additional instructions for how this persona should behave */
  additional_instruction?: string;
  /** Whether this is a default/recommended persona */
  readonly is_default?: boolean;
  readonly created_at?: string;
  readonly updated_at?: string;
  profession?: string[];
  language?: string[];
  custom_properties?: PersonaApiCustomProperties;
  /** Type of simulation for the persona */
  readonly simulation_type?: PersonaApiSimulationType;
  /** Punctuation style for the persona */
  punctuation?: PersonaApiPunctuation;
  /** Slang usage for the persona */
  slang_usage?: PersonaApiSlangUsage;
  /** Typos frequency for the persona */
  typos_frequency?: PersonaApiTyposFrequency;
  /** Regional mix for the persona */
  regional_mix?: PersonaApiRegionalMix;
  /** Emoji usage for the persona */
  emoji_usage?: PersonaApiEmojiUsage;
  /** Tone for the persona */
  tone?: PersonaApiTone;
  /** Verbosity for the persona */
  verbosity?: PersonaApiVerbosity;
}

export interface PersonaDuplicateResponseApi {
  status?: boolean;
  result?: PersonaApi;
}

export interface PersonaFieldOptionsApi {
  readonly gender_choices?: string;
  readonly age_group_choices?: string;
  readonly location_choices?: string;
  readonly profession_choices?: string;
  readonly personality_choices?: string;
  readonly communication_style_choices?: string;
  readonly accent_choices?: string;
  readonly language_choices?: string;
  readonly conversation_speed_choices?: string;
  readonly tone_choices?: string;
  readonly verbosity_choices?: string;
  readonly punctuation_choices?: string;
  readonly emoji_usage_choices?: string;
  readonly slang_usage_choices?: string;
  readonly typos_frequency_choices?: string;
  readonly regional_mix_choices?: string;
}

export type RunTestResponseApiAgentVersion = {[key: string]: string};

export type RunTestResponseApiAgentDefinitionDetail = {[key: string]: string};

/**
 * Source type for the test run: agent_definition or prompt
 */
export type RunTestResponseApiSourceType = typeof RunTestResponseApiSourceType[keyof typeof RunTestResponseApiSourceType];


export const RunTestResponseApiSourceType = {
  agent_definition: 'agent_definition',
  prompt: 'prompt',
} as const;

export type RunTestResponseApiPromptTemplateDetail = {[key: string]: string};

export type RunTestResponseApiPromptVersionDetail = {[key: string]: string};

export type RunTestResponseApiScenariosDetailItem = {[key: string]: string};

export type RunTestResponseApiSimulatorAgentDetail = {[key: string]: string};

export type SimulateEvalConfigResponseApiConfig = { [key: string]: unknown };

export type SimulateEvalConfigResponseApiMapping = { [key: string]: unknown };

export type SimulateEvalConfigResponseApiFiltersItemFilterConfig = {
  /** Canonical field type, for example text, number, boolean, datetime, categorical, thumbs, annotator, or array. */
  filter_type: string;
  /** Canonical operator from api_contracts/filter_contract.json, for example equals, not_equals, in, not_in, between, not_between, is_null, or is_not_null. */
  filter_op: string;
  /** Scalar, list, range tuple, boolean, or null depending on filter_op and filter_type. */
  filter_value?: unknown;
  /** Column family such as SYSTEM_METRIC, SPAN_ATTRIBUTE, EVAL_METRIC, ANNOTATION, or NORMAL. */
  col_type?: string;
};

export type SimulateEvalConfigResponseApiFiltersItem = {
  /** Column or attribute id to filter on. */
  column_id: string;
  /** Optional UI label for chips and saved views. */
  display_name?: string;
  /** Optional source surface for mixed-source filters, for example traces, datasets, or simulation. */
  source?: string;
  /** Optional metric output type metadata used by eval and annotation filters. */
  output_type?: string;
  filter_config: SimulateEvalConfigResponseApiFiltersItemFilterConfig;
};

export interface SimulateEvalConfigResponseApi {
  readonly id?: string;
  /** @minLength 1 */
  readonly name?: string;
  readonly config?: SimulateEvalConfigResponseApiConfig;
  readonly mapping?: SimulateEvalConfigResponseApiMapping;
  readonly filters?: readonly SimulateEvalConfigResponseApiFiltersItem[];
  readonly error_localizer?: boolean;
  /** @minLength 1 */
  readonly model?: string;
  /** @minLength 1 */
  readonly status?: string;
  /** @minLength 1 */
  readonly eval_group?: string;
  readonly template_id?: string;
}

export interface RunTestResponseApi {
  readonly id?: string;
  /**
     * Name of the test run
     * @minLength 1
     */
  readonly name?: string;
  /**
     * Description of the test run
     * @minLength 1
     */
  readonly description?: string;
  /** Agent definition for this test run */
  readonly agent_definition?: string;
  readonly agent_version?: RunTestResponseApiAgentVersion;
  readonly agent_definition_detail?: RunTestResponseApiAgentDefinitionDetail;
  /** Source type for the test run: agent_definition or prompt */
  readonly source_type?: RunTestResponseApiSourceType;
  /** @minLength 1 */
  readonly source_type_display?: string;
  /** Prompt template for this test run (only for prompt source type) */
  readonly prompt_template?: string;
  readonly prompt_template_detail?: RunTestResponseApiPromptTemplateDetail;
  /** Prompt version for this test run (only for prompt source type) */
  readonly prompt_version?: string;
  readonly prompt_version_detail?: RunTestResponseApiPromptVersionDetail;
  /** Scenarios to run in this test */
  readonly scenarios?: readonly string[];
  readonly scenarios_detail?: readonly RunTestResponseApiScenariosDetailItem[];
  /** IDs of dataset rows to run evaluations on */
  readonly dataset_row_ids?: readonly string[];
  /** Simulator agent for this test run (derived from scenarios) */
  readonly simulator_agent?: string;
  readonly simulator_agent_detail?: RunTestResponseApiSimulatorAgentDetail;
  readonly simulate_eval_configs?: readonly string[];
  readonly simulate_eval_configs_detail?: readonly SimulateEvalConfigResponseApi[];
  readonly evals_detail?: readonly SimulateEvalConfigResponseApi[];
  /** Organization this test run belongs to */
  readonly organization?: string;
  /** Enable automatic tool evaluation for this test run */
  readonly enable_tool_evaluation?: boolean;
  readonly created_at?: string;
  readonly updated_at?: string;
  readonly last_run_at?: string;
  readonly deleted?: boolean;
  readonly deleted_at?: string;
}

export type RunTestErrorResponseApiType = typeof RunTestErrorResponseApiType[keyof typeof RunTestErrorResponseApiType];


export const RunTestErrorResponseApiType = {
  validation_error: 'validation_error',
  authentication_error: 'authentication_error',
  payment_required: 'payment_required',
  entitlement_error: 'entitlement_error',
  permission_error: 'permission_error',
  not_found: 'not_found',
  conflict: 'conflict',
  client_error: 'client_error',
  rate_limit: 'rate_limit',
  server_error: 'server_error',
  service_unavailable: 'service_unavailable',
  timeout: 'timeout',
  api_error: 'api_error',
} as const;

export type RunTestErrorResponseApiDetails = {[key: string]: string[]};

export interface RunTestErrorResponseApi {
  status?: boolean;
  type?: RunTestErrorResponseApiType;
  code?: string;
  detail?: string;
  /** @minLength 1 */
  result?: string;
  /** @minLength 1 */
  message?: string;
  error?: string;
  attr?: string;
  details?: RunTestErrorResponseApiDetails;
}

/**
 * Current status of the test execution
 */
export type TestExecutionApiStatus = typeof TestExecutionApiStatus[keyof typeof TestExecutionApiStatus];


export const TestExecutionApiStatus = {
  pending: 'pending',
  running: 'running',
  completed: 'completed',
  failed: 'failed',
  cancelled: 'cancelled',
  cancelling: 'cancelling',
  evaluating: 'evaluating',
} as const;

/**
 * Additional metadata about the execution
 */
export type TestExecutionApiExecutionMetadata = { [key: string]: unknown };

/**
 * List of scenario IDs that were executed in this run
 */
export type TestExecutionApiScenarioIds = { [key: string]: unknown };

export interface TestExecutionApi {
  readonly id?: string;
  /** The run test being executed */
  run_test: string;
  /** @minLength 1 */
  readonly run_test_name?: string;
  /** @minLength 1 */
  readonly agent_definition_name?: string;
  /** Current status of the test execution */
  status?: TestExecutionApiStatus;
  error_reason?: string;
  /** When the test execution started */
  started_at?: string;
  /** When the test execution completed */
  completed_at?: string;
  /**
     * Total number of scenarios in this execution
     * @minimum -2147483648
     * @maximum 2147483647
     */
  total_scenarios?: number;
  /**
     * Total number of calls to be made
     * @minimum -2147483648
     * @maximum 2147483647
     */
  total_calls?: number;
  /**
     * Number of successfully completed calls
     * @minimum -2147483648
     * @maximum 2147483647
     */
  completed_calls?: number;
  /**
     * Number of failed calls
     * @minimum -2147483648
     * @maximum 2147483647
     */
  failed_calls?: number;
  /** Additional metadata about the execution */
  execution_metadata?: TestExecutionApiExecutionMetadata;
  readonly duration_seconds?: string;
  readonly success_rate?: string;
  readonly calls?: readonly CallExecutionApi[];
  readonly created_at?: string;
  /** List of scenario IDs that were executed in this run */
  scenario_ids?: TestExecutionApiScenarioIds;
  /** @minLength 1 */
  readonly simulator_agent_name?: string;
  readonly simulator_agent_id?: string;
  /** @minLength 1 */
  readonly agent_definition_used_name?: string;
  readonly agent_definition_used_id?: string;
  readonly calls_attempted?: string;
  readonly calls_connected_percentage?: string;
}

/**
 * Current status of the call
 */
export type CallExecutionDetailApiStatus = typeof CallExecutionDetailApiStatus[keyof typeof CallExecutionDetailApiStatus];


export const CallExecutionDetailApiStatus = {
  pending: 'pending',
  queued: 'queued',
  ongoing: 'ongoing',
  completed: 'completed',
  failed: 'failed',
  analyzing: 'analyzing',
  cancelled: 'cancelled',
} as const;

/**
 * number | bool | string | list[string] | null
 */
export type CallExecutionEvalMetricApiValue = { [key: string]: unknown };

export type CallExecutionEvalMetricApiErrorAnalysis = { [key: string]: unknown };

export type CallExecutionEvalMetricApiInputData = { [key: string]: unknown };

export type CallExecutionEvalMetricApiInputTypes = { [key: string]: unknown };

export interface CallExecutionEvalMetricApi {
  id?: string;
  name?: string;
  /** number | bool | string | list[string] | null */
  value?: CallExecutionEvalMetricApiValue;
  reason?: string;
  type?: string;
  template_type?: string;
  visible?: boolean;
  error?: boolean;
  status?: string;
  skipped?: boolean;
  error_localizer?: boolean;
  error_analysis?: CallExecutionEvalMetricApiErrorAnalysis;
  error_localizer_status?: string;
  error_localizer_message?: string;
  selected_input_key?: string;
  input_data?: CallExecutionEvalMetricApiInputData;
  input_types?: CallExecutionEvalMetricApiInputTypes;
}

/**
 * Get evaluation metrics in a format suitable for the UI
 */
export type CallExecutionDetailApiEvalMetrics = {[key: string]: CallExecutionEvalMetricApi};

/**
 * Tool evaluation output - separate from standard evaluations
 */
export type CallExecutionDetailApiToolOutputs = { [key: string]: unknown };

/**
 * Detailed cost breakdown from customer call data
 */
export type CallExecutionDetailApiCustomerCostBreakdown = { [key: string]: unknown };

/**
 * Latency metrics from customer call data
 */
export type CallExecutionDetailApiCustomerLatencyMetrics = { [key: string]: unknown };

/**
 * Type of simulation call
 */
export type CallExecutionDetailApiSimulationCallType = typeof CallExecutionDetailApiSimulationCallType[keyof typeof CallExecutionDetailApiSimulationCallType];


export const CallExecutionDetailApiSimulationCallType = {
  voice: 'voice',
  text: 'text',
} as const;

export interface CallExecutionDetailApi {
  readonly id?: string;
  /** @minLength 1 */
  readonly service_provider_call_id?: string;
  readonly session_id?: string;
  readonly timestamp?: string;
  readonly call_type?: string;
  /** Current status of the call */
  status?: CallExecutionDetailApiStatus;
  readonly duration?: string;
  /**
     * Duration of the call in seconds
     * @minimum -2147483648
     * @maximum 2147483647
     */
  duration_seconds?: number;
  readonly start_time?: string;
  readonly transcript?: string;
  /** @minLength 1 */
  readonly scenario?: string;
  readonly overall_score?: string;
  readonly response_time?: string;
  /**
     * Average response time in milliseconds
     * @minimum -2147483648
     * @maximum 2147483647
     */
  response_time_ms?: number;
  /** @minLength 1 */
  readonly audio_url?: string;
  /** @minLength 1 */
  readonly customer_name?: string;
  readonly eval_outputs?: string;
  /** Get evaluation metrics in a format suitable for the UI */
  readonly eval_metrics?: CallExecutionDetailApiEvalMetrics;
  readonly scenario_columns?: string;
  /**
     * Reason why the call ended
     * @maxLength 10000
     */
  ended_reason?: string;
  /** @minLength 1 */
  readonly simulator_agent_name?: string;
  readonly simulator_agent_id?: string;
  /** @minLength 1 */
  readonly agent_definition_used_name?: string;
  readonly agent_definition_used_id?: string;
  /** Call summary from the service */
  call_summary?: string;
  readonly recordings?: string;
  readonly test_execution_id?: string;
  readonly scenario_id?: string;
  readonly scenario_graph?: string;
  readonly scenario_graph_id?: string;
  readonly avg_agent_latency?: number;
  /**
     * Average agent latency in milliseconds (time taken by agent to respond after user's pause)
     * @minimum -2147483648
     * @maximum 2147483647
     */
  avg_agent_latency_ms?: number;
  /**
     * Number of times user interrupted the AI
     * @minimum -2147483648
     * @maximum 2147483647
     */
  user_interruption_count?: number;
  /** Rate of user interruptions (interruptions per minute) */
  user_interruption_rate?: number;
  /** User's words per minute */
  user_wpm?: number;
  /** Bot's words per minute */
  bot_wpm?: number;
  /** Ratio of bot speaking time to user speaking time */
  talk_ratio?: number;
  /**
     * Number of times AI interrupted the user
     * @minimum -2147483648
     * @maximum 2147483647
     */
  ai_interruption_count?: number;
  /** Rate of AI interruptions (interruptions per minute) */
  ai_interruption_rate?: number;
  readonly avg_stop_time_after_interruption?: number;
  readonly total_tokens?: string;
  readonly input_tokens?: string;
  readonly output_tokens?: string;
  readonly avg_latency_ms?: string;
  readonly turn_count?: string;
  readonly agent_talk_percentage?: string;
  readonly csat_score?: string;
  readonly processing_skipped?: string;
  readonly processing_skip_reason?: string;
  readonly rerun_snapshots?: string;
  readonly is_snapshot?: string;
  readonly snapshot_timestamp?: string;
  readonly rerun_type?: string;
  readonly original_call_execution_id?: string;
  /** Tool evaluation output - separate from standard evaluations */
  tool_outputs?: CallExecutionDetailApiToolOutputs;
  /**
     * Cost of the call in cents
     * @minimum -2147483648
     * @maximum 2147483647
     */
  cost_cents?: number;
  /**
     * Total customer-reported cost in cents
     * @minimum -2147483648
     * @maximum 2147483647
     */
  customer_cost_cents?: number;
  /** Detailed cost breakdown from customer call data */
  customer_cost_breakdown?: CallExecutionDetailApiCustomerCostBreakdown;
  /** Latency metrics from customer call data */
  customer_latency_metrics?: CallExecutionDetailApiCustomerLatencyMetrics;
  /**
     * Customer call ID if available
     * @maxLength 255
     */
  customer_call_id?: string;
  /** Type of simulation call */
  simulation_call_type?: CallExecutionDetailApiSimulationCallType;
  readonly provider?: string;
  /**
     * Phone number called (null for TEXT/chat simulations)
     * @maxLength 20
     */
  phone_number?: string;
}

export type CallExecutionStatusUpdateApiStatus = typeof CallExecutionStatusUpdateApiStatus[keyof typeof CallExecutionStatusUpdateApiStatus];


export const CallExecutionStatusUpdateApiStatus = {
  pending: 'pending',
  queued: 'queued',
  ongoing: 'ongoing',
  completed: 'completed',
  failed: 'failed',
  analyzing: 'analyzing',
  cancelled: 'cancelled',
} as const;

export interface CallExecutionStatusUpdateApi {
  status: CallExecutionStatusUpdateApiStatus;
  ended_reason?: string;
}

export type CallBranchAnalysisResponseApiAnalysis = {[key: string]: string};

export interface CallBranchAnalysisResponseApi {
  readonly call_execution_id?: string;
  readonly scenario_id?: string;
  /** @minLength 1 */
  readonly scenario_name?: string;
  readonly analysis?: CallBranchAnalysisResponseApiAnalysis;
  readonly analyzed_at?: string;
}

export type ErrorResponseApiType = typeof ErrorResponseApiType[keyof typeof ErrorResponseApiType];


export const ErrorResponseApiType = {
  validation_error: 'validation_error',
  authentication_error: 'authentication_error',
  payment_required: 'payment_required',
  entitlement_error: 'entitlement_error',
  permission_error: 'permission_error',
  not_found: 'not_found',
  conflict: 'conflict',
  client_error: 'client_error',
  rate_limit: 'rate_limit',
  server_error: 'server_error',
  service_unavailable: 'service_unavailable',
  timeout: 'timeout',
  api_error: 'api_error',
} as const;

export type ErrorResponseApiDetails = {[key: string]: string[]};

export interface ErrorResponseApi {
  status?: boolean;
  type?: ErrorResponseApiType;
  code?: string;
  detail?: string;
  /** @minLength 1 */
  result?: string;
  /** @minLength 1 */
  message?: string;
  error?: string;
  attr?: string;
  details?: ErrorResponseApiDetails;
}

export type CallBranchDeviationCreateResponseApiDeviationData = {[key: string]: string};

export interface CallBranchDeviationCreateResponseApi {
  readonly call_execution_id?: string;
  readonly scenario_graph_id?: string;
  readonly deviation_data?: CallBranchDeviationCreateResponseApiDeviationData;
  /** @minLength 1 */
  readonly message?: string;
}

export type SendChatRequestApiMetrics = {[key: string]: string};

export type ChatMessageContractApiRole = typeof ChatMessageContractApiRole[keyof typeof ChatMessageContractApiRole];


export const ChatMessageContractApiRole = {
  user: 'user',
  assistant: 'assistant',
  tool: 'tool',
} as const;

export interface ChatToolCallFunctionApi {
  /** @minLength 1 */
  name: string;
  /** @minLength 1 */
  arguments: string;
}

export interface ChatToolCallApi {
  /** @minLength 1 */
  id: string;
  /** @minLength 1 */
  type: string;
  function: ChatToolCallFunctionApi;
}

export type ChatMessageContractApiMetadata = {[key: string]: string};

export interface ChatMessageContractApi {
  role: ChatMessageContractApiRole;
  content?: string;
  tool_call_id?: string;
  name?: string;
  metadata?: ChatMessageContractApiMetadata;
  tool_calls?: ChatToolCallApi[];
}

export interface SendChatRequestApi {
  messages?: ChatMessageContractApi[];
  metrics?: SendChatRequestApiMetrics;
  initiate_chat?: boolean;
}

export interface ChatSendMessageResultApi {
  input_message?: ChatMessageContractApi[];
  output_message?: ChatMessageContractApi[];
  message_history: ChatMessageContractApi[];
  chat_ended?: boolean;
}

export interface ChatSendMessageResponseApi {
  status?: boolean;
  result: ChatSendMessageResultApi;
}

export interface CallExecutionDeleteResponseApi {
  /** @minLength 1 */
  readonly message?: string;
}

export type ErrorLocalizerTaskResponseApiEvalResult = { [key: string]: unknown };

export type ErrorLocalizerTaskResponseApiInputData = { [key: string]: unknown };

export type ErrorLocalizerTaskResponseApiInputKeys = { [key: string]: unknown };

export type ErrorLocalizerTaskResponseApiInputTypes = { [key: string]: unknown };

export type ErrorLocalizerTaskResponseApiErrorAnalysis = { [key: string]: unknown };

export interface ErrorLocalizerTaskResponseApi {
  readonly task_id?: string;
  /** @minLength 1 */
  readonly eval_config_id?: string;
  readonly status?: string;
  readonly eval_result?: ErrorLocalizerTaskResponseApiEvalResult;
  /** @minLength 1 */
  readonly eval_explanation?: string;
  readonly input_data?: ErrorLocalizerTaskResponseApiInputData;
  readonly input_keys?: ErrorLocalizerTaskResponseApiInputKeys;
  readonly input_types?: ErrorLocalizerTaskResponseApiInputTypes;
  /** @minLength 1 */
  readonly rule_prompt?: string;
  readonly error_analysis?: ErrorLocalizerTaskResponseApiErrorAnalysis;
  /** @minLength 1 */
  readonly selected_input_key?: string;
  /** @minLength 1 */
  readonly error_message?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
  /** @minLength 1 */
  readonly eval_template_name?: string;
  readonly eval_template_id?: string;
}

export interface CallExecutionErrorLocalizerTasksResponseApi {
  readonly call_execution_id?: string;
  readonly error_localizer_tasks?: readonly ErrorLocalizerTaskResponseApi[];
  readonly total_tasks?: number;
}

export type CallLogEntryResponseApiAttributes = {[key: string]: string};

export type CallLogEntryResponseApiPayload = {[key: string]: string};

export interface CallLogEntryResponseApi {
  /** @minLength 1 */
  readonly id?: string;
  /** @minLength 1 */
  readonly logged_at?: string;
  /** @minLength 1 */
  readonly level?: string;
  /** @minLength 1 */
  readonly severity_text?: string;
  /** @minLength 1 */
  readonly category?: string;
  /** @minLength 1 */
  readonly body?: string;
  readonly attributes?: CallLogEntryResponseApiAttributes;
  readonly payload?: CallLogEntryResponseApiPayload;
}

export interface CallExecutionLogsResponseApi {
  readonly results?: readonly CallLogEntryResponseApi[];
  /** @minLength 1 */
  readonly source?: string;
  readonly ingestion_pending?: boolean;
}

export type SessionComparisonResultApiComparisonMetrics = { [key: string]: unknown };

export type SessionComparisonResultApiComparisonTranscripts = { [key: string]: unknown };

export type SessionComparisonResultApiComparisonRecordings = { [key: string]: unknown };

export interface SessionComparisonResultApi {
  readonly comparison_metrics?: SessionComparisonResultApiComparisonMetrics;
  readonly comparison_transcripts?: SessionComparisonResultApiComparisonTranscripts;
  readonly comparison_recordings?: SessionComparisonResultApiComparisonRecordings;
}

export interface SessionComparisonResponseApi {
  status?: boolean;
  result: SessionComparisonResultApi;
}

/**
 * Role of the speaker (user or assistant)
 */
export type CallTranscriptApiSpeakerRole = typeof CallTranscriptApiSpeakerRole[keyof typeof CallTranscriptApiSpeakerRole];


export const CallTranscriptApiSpeakerRole = {
  user: 'user',
  assistant: 'assistant',
  system: 'system',
  tool_calls: 'tool_calls',
  tool_call_result: 'tool_call_result',
  unknown: 'unknown',
} as const;

export interface CallTranscriptApi {
  readonly id?: string;
  /** Role of the speaker (user or assistant) */
  speaker_role?: CallTranscriptApiSpeakerRole;
  /**
     * Transcript content
     * @minLength 1
     */
  content: string;
  /**
     * Start time of this transcript segment in milliseconds
     * @minimum -9223372036854776000
     * @maximum 9223372036854776000
     */
  start_time_ms?: number;
  readonly start_time_seconds?: string;
  /**
     * End time of this transcript segment in milliseconds
     * @minimum -9223372036854776000
     * @maximum 9223372036854776000
     */
  end_time_ms?: number;
  readonly end_time_seconds?: string;
  /** Confidence score for this transcript segment */
  confidence_score?: number;
  readonly created_at?: string;
}

export interface CallTranscriptResponseApi {
  readonly call_execution_id?: string;
  /** @minLength 1 */
  readonly phone_number?: string;
  /** @minLength 1 */
  readonly status?: string;
  readonly transcripts?: readonly CallTranscriptApi[];
  readonly total_transcripts?: number;
}

export interface PromptSimulationScenarioItemApi {
  readonly id?: string;
  /** @minLength 1 */
  readonly name?: string;
  readonly description?: string;
  /** @minLength 1 */
  readonly scenario_type?: string;
  readonly dataset_id?: string;
  readonly created_at?: string;
}

export interface PromptSimulationScenariosResultApi {
  readonly count?: number;
  readonly page?: number;
  readonly limit?: number;
  readonly results?: readonly PromptSimulationScenarioItemApi[];
}

export interface PromptSimulationScenariosResponseApi {
  status?: boolean;
  result: PromptSimulationScenariosResultApi;
}

export interface PromptSimulationTemplateSummaryApi {
  readonly id?: string;
  /** @minLength 1 */
  readonly name?: string;
}

export interface PromptSimulationListResultApi {
  readonly count?: number;
  readonly page?: number;
  readonly limit?: number;
  readonly results?: readonly RunTestResponseApi[];
  prompt_template?: PromptSimulationTemplateSummaryApi;
}

export interface PromptSimulationListResponseApi {
  status?: boolean;
  result: PromptSimulationListResultApi;
}

/**
 * Template-specific configuration parameters.
 */
export type EvalConfigDefinitionApiConfig = { [key: string]: unknown };

/**
 * Maps test execution data fields to the evaluation template's expected inputs.
 */
export type EvalConfigDefinitionApiMapping = { [key: string]: unknown };

export type EvalConfigDefinitionApiFiltersItemFilterConfig = {
  /** Canonical field type, for example text, number, boolean, datetime, categorical, thumbs, annotator, or array. */
  filter_type: string;
  /** Canonical operator from api_contracts/filter_contract.json, for example equals, not_equals, in, not_in, between, not_between, is_null, or is_not_null. */
  filter_op: string;
  /** Scalar, list, range tuple, boolean, or null depending on filter_op and filter_type. */
  filter_value?: unknown;
  /** Column family such as SYSTEM_METRIC, SPAN_ATTRIBUTE, EVAL_METRIC, ANNOTATION, or NORMAL. */
  col_type?: string;
};

export type EvalConfigDefinitionApiFiltersItem = {
  /** Column or attribute id to filter on. */
  column_id: string;
  /** Optional UI label for chips and saved views. */
  display_name?: string;
  /** Optional source surface for mixed-source filters, for example traces, datasets, or simulation. */
  source?: string;
  /** Optional metric output type metadata used by eval and annotation filters. */
  output_type?: string;
  filter_config: EvalConfigDefinitionApiFiltersItemFilterConfig;
};

export interface EvalConfigDefinitionApi {
  /** UUID of the evaluation template to use. */
  template_id: string;
  /** Name for this evaluation configuration. Defaults to 'Eval-<template_id>' if omitted. */
  name?: string;
  /** Template-specific configuration parameters. */
  config?: EvalConfigDefinitionApiConfig;
  /** Maps test execution data fields to the evaluation template's expected inputs. */
  mapping?: EvalConfigDefinitionApiMapping;
  /** Canonical filter list to restrict which test results are evaluated. */
  filters?: EvalConfigDefinitionApiFiltersItem[];
  /** Enables granular error localization on evaluation failures. */
  error_localizer?: boolean;
  /**
     * Model to use for running this evaluation.
     * @minLength 1
     */
  model?: string;
  /** Knowledge base file to use for this evaluation. */
  kb_id?: string;
  /** Eval group that created this evaluation config. */
  eval_group?: string;
}

export interface CreatePromptSimulationRequestApi {
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  description?: string;
  /**
     * Prompt version ID (UUID) or template_version string
     * @minLength 1
     * @maxLength 255
     */
  prompt_version_id: string;
  scenario_ids: string[];
  dataset_row_ids?: string[];
  /** Evaluation configurations to create */
  evaluations_config?: EvalConfigDefinitionApi[];
  /** Enable automatic tool evaluation for this simulation run */
  enable_tool_evaluation?: boolean;
}

export interface PromptSimulationRunResponseApi {
  status?: boolean;
  result: RunTestResponseApi;
}

export interface PromptSimulationUpdateRequestApi {
  /**
     * @minLength 1
     * @maxLength 255
     */
  prompt_version_id?: string;
  scenario_ids?: string[];
  /**
     * @minLength 1
     * @maxLength 255
     */
  name?: string;
  description?: string;
  enable_tool_evaluation?: boolean;
}

export interface ExecutePromptSimulationRequestApi {
  scenario_ids?: string[];
  select_all?: boolean;
}

export interface ExecutePromptSimulationResultApi {
  /** @minLength 1 */
  readonly message?: string;
  readonly execution_id?: string;
  readonly run_test_id?: string;
  /** @minLength 1 */
  readonly status?: string;
  readonly total_scenarios?: number;
  readonly total_calls?: number;
  scenario_ids: string[];
}

export interface ExecutePromptSimulationResponseApi {
  status?: boolean;
  result: ExecutePromptSimulationResultApi;
}

export type AllActiveTestsApiActiveTests = {[key: string]: string};

export interface AllActiveTestsApi {
  active_tests: AllActiveTestsApiActiveTests;
  total_active: number;
}

export interface CreateRunTestApi {
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  description?: string;
  agent_definition_id: string;
  scenario_ids: string[];
  dataset_row_ids?: string[];
  eval_config_ids?: string[];
  /** Evaluation configurations to create */
  evaluations_config?: EvalConfigDefinitionApi[];
  /** Enable automatic tool evaluation for this test run */
  enable_tool_evaluation?: boolean;
  /** Optional replay session ID to mark as completed after run test creation */
  replay_session_id?: string;
  /** Optional agent version to bind to this test run */
  agent_version?: string;
}

export interface RunTestNameResultApi {
  run_test_id: string;
  /** @minLength 1 */
  run_test_name: string;
}

export interface RunTestNameResponseApi {
  status?: boolean;
  result: RunTestNameResultApi;
}

export interface UpdateRunTestApi {
  /**
     * @minLength 1
     * @maxLength 255
     */
  name?: string;
  description?: string;
  agent_definition_id?: string;
  scenario_ids?: string[];
  dataset_row_ids?: string[];
  eval_config_ids?: string[];
}

export interface RunTestMessageResponseApi {
  /** @minLength 1 */
  readonly message?: string;
}

/**
 * Run test metadata
 */
export type RunTestAnalyticsApiRunTestInfo = {[key: string]: string};

export type RunTestAnalyticsApiFailRateTrendsItem = {[key: string]: string};

export type RunTestAnalyticsApiEvaluationScoreTrendsItem = {[key: string]: string};

export type RunTestAnalyticsApiPerformanceComparisonItem = {[key: string]: string};

/**
 * Aggregate performance summary
 */
export type RunTestAnalyticsApiSummaryStats = {[key: string]: string};

export interface RunTestAnalyticsApi {
  /** Run test metadata */
  run_test_info: RunTestAnalyticsApiRunTestInfo;
  /** Fail-rate trend points */
  fail_rate_trends: RunTestAnalyticsApiFailRateTrendsItem[];
  /** Evaluation score trend points */
  evaluation_score_trends: RunTestAnalyticsApiEvaluationScoreTrendsItem[];
  /** Per-execution performance rows */
  performance_comparison: RunTestAnalyticsApiPerformanceComparisonItem[];
  /** Aggregate performance summary */
  summary_stats?: RunTestAnalyticsApiSummaryStats;
}

export type RunTestCallExecutionsResponseApiResultsItem = {[key: string]: string};

export interface RunTestCallExecutionsResponseApi {
  readonly count?: number;
  /** @minLength 1 */
  readonly next?: string;
  /** @minLength 1 */
  readonly previous?: string;
  readonly results?: readonly RunTestCallExecutionsResponseApiResultsItem[];
  readonly total_pages?: number;
  readonly current_page?: number;
}

export interface RunTestChatExecutionResultApi {
  /** @minLength 1 */
  message: string;
  execution_id: string;
  run_test_id: string;
  /** @minLength 1 */
  status: string;
  total_scenarios: string[];
}

export interface RunTestChatExecutionResponseApi {
  status?: boolean;
  result: RunTestChatExecutionResultApi;
}

export interface RunTestComponentsUpdateApi {
  agent_definition_id?: string;
  version?: string;
  simulator_agent_id?: string;
  scenarios?: string[];
  enable_tool_evaluation?: boolean;
}

export interface TestExecutionBulkDeleteApi {
  /** List of specific test execution IDs to delete */
  test_execution_ids?: string[];
  /** Whether to delete all test executions in the run test */
  select_all?: boolean;
}

export interface TestExecutionBulkDeleteResponseApi {
  /** @minLength 1 */
  readonly message?: string;
  readonly run_test_id?: string;
  readonly deleted_count?: number;
  readonly deleted_ids?: readonly string[];
}

export interface AddEvalConfigsRequestApi {
  /**
     * Array of evaluation configuration objects to add. At least one required.
     * @minItems 1
     */
  evaluations_config: EvalConfigDefinitionApi[];
}

export type EvalConfigResponseApiStatus = typeof EvalConfigResponseApiStatus[keyof typeof EvalConfigResponseApiStatus];


export const EvalConfigResponseApiStatus = {
  NotStarted: 'NotStarted',
  Queued: 'Queued',
  Running: 'Running',
  Completed: 'Completed',
  Editing: 'Editing',
  Inactive: 'Inactive',
  Failed: 'Failed',
  PartialRun: 'PartialRun',
  ExperimentEvaluation: 'ExperimentEvaluation',
  Uploading: 'Uploading',
  PartialExtracted: 'PartialExtracted',
  Processing: 'Processing',
  Deleting: 'Deleting',
  PartialCompleted: 'PartialCompleted',
  OptimizationEvaluation: 'OptimizationEvaluation',
  Error: 'Error',
  Cancelled: 'Cancelled',
} as const;

export type EvalConfigResponseApiConfig = { [key: string]: unknown };

export type EvalConfigResponseApiMapping = { [key: string]: unknown };

export type EvalConfigResponseApiFilters = { [key: string]: unknown };

export interface EvalConfigResponseApi {
  readonly id?: string;
  /** @maxLength 255 */
  name?: string;
  config?: EvalConfigResponseApiConfig;
  mapping?: EvalConfigResponseApiMapping;
  filters?: EvalConfigResponseApiFilters;
  error_localizer?: boolean;
  /** @maxLength 255 */
  model?: string;
  status?: EvalConfigResponseApiStatus;
  readonly eval_group?: string;
  readonly template_id?: string;
}

export interface AddEvalConfigsResponseApi {
  /** @minLength 1 */
  message: string;
  created_eval_configs: EvalConfigResponseApi[];
  run_test_id: string;
  /** Non-fatal issues encountered while processing individual configs. */
  warnings?: string[];
}

export interface DeleteEvalConfigResponseApi {
  /** @minLength 1 */
  message: string;
}

export type EvalConfigStructureApiEvalTags = { [key: string]: unknown };

export type EvalConfigStructureApiMapping = {[key: string]: string};

export type EvalConfigStructureApiConfig = {[key: string]: string};

export type EvalConfigStructureApiParams = { [key: string]: unknown };

export type EvalConfigStructureApiFunctionParamsSchema = { [key: string]: unknown };

export type EvalConfigStructureApiModels = { [key: string]: unknown };

export type EvalConfigStructureApiOutput = { [key: string]: unknown };

export type EvalConfigStructureApiConfigParamsDesc = {[key: string]: string};

export type EvalConfigStructureApiConfigParamsOption = {[key: string]: string};

export interface EvalConfigStructureApi {
  readonly id?: string;
  readonly template_id?: string;
  /** @minLength 1 */
  readonly name?: string;
  readonly reason_column?: boolean;
  readonly eval_tags?: EvalConfigStructureApiEvalTags;
  readonly description?: string;
  required_keys: string[];
  optional_keys: string[];
  variable_keys: string[];
  readonly run_prompt_column?: boolean;
  /** @minLength 1 */
  readonly template_name?: string;
  readonly mapping?: EvalConfigStructureApiMapping;
  readonly config?: EvalConfigStructureApiConfig;
  readonly params?: EvalConfigStructureApiParams;
  readonly function_params_schema?: EvalConfigStructureApiFunctionParamsSchema;
  readonly models?: EvalConfigStructureApiModels;
  /** @minLength 1 */
  readonly selected_model?: string;
  readonly error_localizer?: boolean;
  readonly kb_id?: string;
  readonly output?: EvalConfigStructureApiOutput;
  readonly config_params_desc?: EvalConfigStructureApiConfigParamsDesc;
  readonly config_params_option?: EvalConfigStructureApiConfigParamsOption;
  readonly api_key_available?: boolean;
}

export interface EvalConfigStructureResultApi {
  eval: EvalConfigStructureApi;
}

export interface EvalConfigStructureResponseApi {
  status?: boolean;
  result: EvalConfigStructureResultApi;
}

/**
 * Updated evaluation configuration parameters.
 */
export type EvalConfigUpdateRequestApiConfig = { [key: string]: unknown };

/**
 * Updated field mapping between test data and evaluation inputs.
 */
export type EvalConfigUpdateRequestApiMapping = { [key: string]: unknown };

export type EvalConfigUpdateRequestApiFiltersItemFilterConfig = {
  /** Canonical field type, for example text, number, boolean, datetime, categorical, thumbs, annotator, or array. */
  filter_type: string;
  /** Canonical operator from api_contracts/filter_contract.json, for example equals, not_equals, in, not_in, between, not_between, is_null, or is_not_null. */
  filter_op: string;
  /** Scalar, list, range tuple, boolean, or null depending on filter_op and filter_type. */
  filter_value?: unknown;
  /** Column family such as SYSTEM_METRIC, SPAN_ATTRIBUTE, EVAL_METRIC, ANNOTATION, or NORMAL. */
  col_type?: string;
};

export type EvalConfigUpdateRequestApiFiltersItem = {
  /** Column or attribute id to filter on. */
  column_id: string;
  /** Optional UI label for chips and saved views. */
  display_name?: string;
  /** Optional source surface for mixed-source filters, for example traces, datasets, or simulation. */
  source?: string;
  /** Optional metric output type metadata used by eval and annotation filters. */
  output_type?: string;
  filter_config: EvalConfigUpdateRequestApiFiltersItemFilterConfig;
};

export interface EvalConfigUpdateRequestApi {
  /** Updated evaluation configuration parameters. */
  config?: EvalConfigUpdateRequestApiConfig;
  /** Updated field mapping between test data and evaluation inputs. */
  mapping?: EvalConfigUpdateRequestApiMapping;
  /**
     * Model to use for evaluations.
     * @minLength 1
     */
  model?: string;
  /** Enable granular error localization in evaluation results. */
  error_localizer?: boolean;
  /** UUID of a knowledge base to use for grounding. Pass null to clear. Switching template_id without providing an explicit kb_id will clear the KB association. */
  kb_id?: string;
  /** UUID of the evaluation template to switch to. */
  template_id?: string;
  /** Updated canonical filter list to restrict which test results are evaluated. */
  filters?: EvalConfigUpdateRequestApiFiltersItem[];
  /**
     * Updated name for the evaluation configuration.
     * @minLength 1
     */
  name?: string;
  /** When true, triggers an immediate rerun after updating. Defaults to false. */
  run?: boolean;
  /** UUID of the test execution to rerun against. Required when run is true. */
  test_execution_id?: string;
}

export interface EvalConfigUpdateResponseApi {
  /** @minLength 1 */
  message: string;
  eval_config_id: string;
  run_test_id: string;
  test_execution_id?: string;
  call_execution_count?: number;
  /** @minLength 1 */
  note?: string;
}

export type EvalSummaryComparisonResponseApiResult = {[key: string]: EvalTemplateSummaryApi[]};

export interface EvalSummaryComparisonResponseApi {
  status?: boolean;
  result: EvalSummaryComparisonResponseApiResult;
}

export interface ExecuteRunTestApi {
  scenario_ids?: string[];
  simulator_id?: string;
  select_all?: boolean;
}

export interface RunTestExecutionResponseApi {
  /** @minLength 1 */
  readonly message?: string;
  readonly execution_id?: string;
  readonly run_test_id?: string;
  /** @minLength 1 */
  readonly status?: string;
  readonly total_scenarios?: number;
  readonly total_calls?: number;
  readonly scenario_ids?: readonly string[];
}

export interface TestExecutionItemResponseApi {
  /** @minLength 1 */
  readonly id?: string;
  /** @minLength 1 */
  readonly status?: string;
  /** @minLength 1 */
  readonly scenarios?: string;
  /** @minLength 1 */
  readonly start_time?: string;
  readonly duration?: number;
  /** @minLength 1 */
  readonly error_reason?: string;
  readonly success_rate?: number;
  readonly avg_response_time?: number;
  readonly calls?: number;
  readonly calls_attempted?: number;
  readonly connected_calls?: number;
  /** @minLength 1 */
  readonly agent_version?: string;
  /** @minLength 1 */
  readonly agent_definition?: string;
  readonly calls_connected_percentage?: number;
  readonly total_chats?: number;
  /** @minLength 1 */
  readonly agent_type?: string;
  readonly total_number_of_fagi_agent_turns?: number;
  /** @minLength 1 */
  readonly source_type?: string;
}

/**
 * Type of rerun: evaluation only or call plus evaluation
 */
export type TestExecutionRerunApiRerunType = typeof TestExecutionRerunApiRerunType[keyof typeof TestExecutionRerunApiRerunType];


export const TestExecutionRerunApiRerunType = {
  eval_only: 'eval_only',
  call_and_eval: 'call_and_eval',
} as const;

export interface TestExecutionRerunApi {
  /** Type of rerun: evaluation only or call plus evaluation */
  rerun_type: TestExecutionRerunApiRerunType;
  /** List of specific test execution IDs to rerun */
  test_execution_ids?: string[];
  /** Whether to rerun all test executions in the run test */
  select_all?: boolean;
}

export type TestExecutionRerunResultApiFailedRerunsItem = {[key: string]: string};

export interface TestExecutionRerunResultApi {
  readonly test_execution_id?: string;
  readonly success_count?: number;
  readonly failure_count?: number;
  readonly successful_reruns?: readonly string[];
  readonly failed_reruns?: readonly TestExecutionRerunResultApiFailedRerunsItem[];
  /** @minLength 1 */
  readonly dispatch_error?: string;
  readonly skipped?: boolean;
  /** @minLength 1 */
  readonly reason?: string;
}

export interface TestExecutionRerunResponseApi {
  /** @minLength 1 */
  readonly message?: string;
  readonly run_test_id?: string;
  /** @minLength 1 */
  readonly rerun_type?: string;
  readonly total_test_executions?: number;
  readonly results?: readonly TestExecutionRerunResultApi[];
  readonly overall_success_count?: number;
  readonly overall_failure_count?: number;
}

export interface RunNewEvalsOnTestExecutionApi {
  /** List of specific test execution IDs to run evaluations on */
  test_execution_ids?: string[];
  /** Whether to run evaluations on all test executions in the run test */
  select_all?: boolean;
  /** List of SimulateEvalConfig IDs to run on the test executions */
  eval_config_ids: string[];
  /** Whether to enable tool evaluation for this run (if not provided, uses the run test's current setting) */
  enable_tool_evaluation?: boolean;
}

export interface RunNewEvalsResponseApi {
  /** @minLength 1 */
  message: string;
  run_test_id: string;
  call_execution_count: number;
}

export interface RunTestScenarioItemResponseApi {
  /** @minLength 1 */
  readonly id?: string;
  /** @minLength 1 */
  readonly name?: string;
  readonly row_count?: number;
}

export interface ChatSDKCodeResultApi {
  /** @minLength 1 */
  installation_guide: string;
  /** @minLength 1 */
  sdk_code: string;
  run_test_id: string;
  /** @minLength 1 */
  run_test_name: string;
}

export interface ChatSDKCodeResponseApi {
  status?: boolean;
  result: ChatSDKCodeResultApi;
}

export type TestExecutionStatusApiScenariosItem = {[key: string]: string};

export interface TestExecutionStatusApi {
  /** @minLength 1 */
  run_test_id: string;
  /** @minLength 1 */
  execution_id: string;
  /** @minLength 1 */
  status: string;
  total_scenarios: number;
  total_calls: number;
  completed_calls: number;
  failed_calls: number;
  success_rate: number;
  start_time: string;
  end_time: string;
  scenarios: TestExecutionStatusApiScenariosItem[];
  /** @minLength 1 */
  error: string;
}

/**
 * Type of scenario (graph, script, or dataset)
 */
export type ScenarioResponseApiScenarioType = typeof ScenarioResponseApiScenarioType[keyof typeof ScenarioResponseApiScenarioType];


export const ScenarioResponseApiScenarioType = {
  graph: 'graph',
  script: 'script',
  dataset: 'dataset',
} as const;

/**
 * Source type for the scenario: agent_definition or prompt
 */
export type ScenarioResponseApiSourceType = typeof ScenarioResponseApiSourceType[keyof typeof ScenarioResponseApiSourceType];


export const ScenarioResponseApiSourceType = {
  agent_definition: 'agent_definition',
  prompt: 'prompt',
} as const;

/**
 * Status of the scenario
 */
export type ScenarioResponseApiStatus = typeof ScenarioResponseApiStatus[keyof typeof ScenarioResponseApiStatus];


export const ScenarioResponseApiStatus = {
  NotStarted: 'NotStarted',
  Queued: 'Queued',
  Running: 'Running',
  Completed: 'Completed',
  Editing: 'Editing',
  Inactive: 'Inactive',
  Failed: 'Failed',
  PartialRun: 'PartialRun',
  ExperimentEvaluation: 'ExperimentEvaluation',
  Uploading: 'Uploading',
  PartialExtracted: 'PartialExtracted',
  Processing: 'Processing',
  Deleting: 'Deleting',
  PartialCompleted: 'PartialCompleted',
  OptimizationEvaluation: 'OptimizationEvaluation',
  Error: 'Error',
  Cancelled: 'Cancelled',
} as const;

export interface ScenarioResponseApi {
  readonly id?: string;
  /**
     * Name of the scenario
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  /** Optional description of the scenario */
  description?: string;
  /**
     * Source content or reference for the scenario
     * @minLength 1
     */
  source: string;
  /** Type of scenario (graph, script, or dataset) */
  scenario_type?: ScenarioResponseApiScenarioType;
  /** @minLength 1 */
  readonly scenario_type_display?: string;
  /** Source type for the scenario: agent_definition or prompt */
  source_type?: ScenarioResponseApiSourceType;
  /** @minLength 1 */
  readonly source_type_display?: string;
  /** Organization this scenario belongs to */
  readonly organization?: string;
  /** Dataset associated with this scenario (only for dataset type scenarios) */
  dataset?: string;
  readonly dataset_rows?: string;
  readonly dataset_column_config?: string;
  readonly graph?: string;
  readonly agent?: string;
  /** Prompt template associated with this scenario (only for prompt source type) */
  prompt_template?: string;
  readonly prompt_template_detail?: string;
  /** Prompt version associated with this scenario (only for prompt source type) */
  prompt_version?: string;
  readonly prompt_version_detail?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
  readonly deleted?: boolean;
  /** Status of the scenario */
  status?: ScenarioResponseApiStatus;
  readonly deleted_at?: string;
  readonly agent_type?: string;
}

export interface ScenarioListResponseApi {
  readonly count?: number;
  /** @minLength 1 */
  readonly next?: string;
  /** @minLength 1 */
  readonly previous?: string;
  readonly results?: readonly ScenarioResponseApi[];
}

export type ScenarioErrorResponseApiType = typeof ScenarioErrorResponseApiType[keyof typeof ScenarioErrorResponseApiType];


export const ScenarioErrorResponseApiType = {
  validation_error: 'validation_error',
  authentication_error: 'authentication_error',
  payment_required: 'payment_required',
  entitlement_error: 'entitlement_error',
  permission_error: 'permission_error',
  not_found: 'not_found',
  conflict: 'conflict',
  client_error: 'client_error',
  rate_limit: 'rate_limit',
  server_error: 'server_error',
  service_unavailable: 'service_unavailable',
  timeout: 'timeout',
  api_error: 'api_error',
} as const;

export type ScenarioErrorResponseApiDetails = {[key: string]: string[]};

export interface ScenarioErrorResponseApi {
  status?: boolean;
  type?: ScenarioErrorResponseApiType;
  code?: string;
  detail?: string;
  /** @minLength 1 */
  result?: string;
  /** @minLength 1 */
  message?: string;
  error?: string;
  attr?: string;
  details?: ScenarioErrorResponseApiDetails;
}

export type ScenarioCreateRequestApiKind = typeof ScenarioCreateRequestApiKind[keyof typeof ScenarioCreateRequestApiKind];


export const ScenarioCreateRequestApiKind = {
  graph: 'graph',
  script: 'script',
  dataset: 'dataset',
} as const;

export type ScenarioCreateRequestApiGraph = { [key: string]: unknown };

export type ScenarioCreateRequestApiSourceType = typeof ScenarioCreateRequestApiSourceType[keyof typeof ScenarioCreateRequestApiSourceType];


export const ScenarioCreateRequestApiSourceType = {
  agent_definition: 'agent_definition',
  prompt: 'prompt',
} as const;

export type ColumnDefinitionApiDataType = typeof ColumnDefinitionApiDataType[keyof typeof ColumnDefinitionApiDataType];


export const ColumnDefinitionApiDataType = {
  text: 'text',
  boolean: 'boolean',
  integer: 'integer',
  float: 'float',
  json: 'json',
  array: 'array',
  image: 'image',
  images: 'images',
  datetime: 'datetime',
  audio: 'audio',
  document: 'document',
  others: 'others',
  persona: 'persona',
} as const;

export interface ColumnDefinitionApi {
  /**
     * @minLength 1
     * @maxLength 50
     */
  name: string;
  data_type: ColumnDefinitionApiDataType;
  /**
     * @minLength 1
     * @maxLength 200
     */
  description: string;
}

export interface ScenarioCreateRequestApi {
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  description?: string;
  dataset_id?: string;
  kind?: ScenarioCreateRequestApiKind;
  /** @minLength 1 */
  script_url?: string;
  agent_definition_id?: string;
  agent_definition_version_id?: string;
  custom_instruction?: string;
  /**
     * @minimum 10
     * @maximum 20000
     */
  no_of_rows?: number;
  generate_graph?: boolean;
  graph?: ScenarioCreateRequestApiGraph;
  source_type?: ScenarioCreateRequestApiSourceType;
  prompt_template_id?: string;
  prompt_version_id?: string;
  add_persona_automatically?: boolean;
  personas?: string[];
  /** @maxItems 10 */
  custom_columns?: ColumnDefinitionApi[];
  /**
     * @minLength 1
     * @maxLength 255
     */
  agent_name?: string;
  agent_prompt?: string;
  /**
     * @minLength 1
     * @maxLength 100
     */
  voice_provider?: string;
  /**
     * @minLength 1
     * @maxLength 100
     */
  voice_name?: string;
  /**
     * @minLength 1
     * @maxLength 100
     */
  model?: string;
  llm_temperature?: number;
  initial_message?: string;
  max_call_duration_in_minutes?: number;
  interrupt_sensitivity?: number;
  conversation_speed?: number;
  finished_speaking_sensitivity?: number;
  initial_message_delay?: number;
}

export type ScenarioCreateResponseApiStatus = typeof ScenarioCreateResponseApiStatus[keyof typeof ScenarioCreateResponseApiStatus];


export const ScenarioCreateResponseApiStatus = {
  processing: 'processing',
} as const;

export interface ScenarioCreateResponseApi {
  /** @minLength 1 */
  readonly message?: string;
  scenario?: ScenarioResponseApi;
  readonly status?: ScenarioCreateResponseApiStatus;
}

export type ScenarioDetailResponseApiScenarioType = typeof ScenarioDetailResponseApiScenarioType[keyof typeof ScenarioDetailResponseApiScenarioType];


export const ScenarioDetailResponseApiScenarioType = {
  graph: 'graph',
  script: 'script',
  dataset: 'dataset',
} as const;

export type ScenarioDetailResponseApiStatus = typeof ScenarioDetailResponseApiStatus[keyof typeof ScenarioDetailResponseApiStatus];


export const ScenarioDetailResponseApiStatus = {
  NotStarted: 'NotStarted',
  Queued: 'Queued',
  Running: 'Running',
  Completed: 'Completed',
  Editing: 'Editing',
  Inactive: 'Inactive',
  Failed: 'Failed',
  PartialRun: 'PartialRun',
  ExperimentEvaluation: 'ExperimentEvaluation',
  Uploading: 'Uploading',
  PartialExtracted: 'PartialExtracted',
  Processing: 'Processing',
  Deleting: 'Deleting',
  PartialCompleted: 'PartialCompleted',
  OptimizationEvaluation: 'OptimizationEvaluation',
  Error: 'Error',
  Cancelled: 'Cancelled',
} as const;

export type ScenarioDetailResponseApiGraph = {[key: string]: string};

export interface DatasetColumnConfigEntryApi {
  /** @minLength 1 */
  readonly name?: string;
  /** @minLength 1 */
  readonly type?: string;
}

export type ScenarioDetailResponseApiDatasetColumnConfig = {[key: string]: DatasetColumnConfigEntryApi};

export type ScenarioPromptItemApiRole = typeof ScenarioPromptItemApiRole[keyof typeof ScenarioPromptItemApiRole];


export const ScenarioPromptItemApiRole = {
  system: 'system',
  user: 'user',
  assistant: 'assistant',
} as const;

export interface ScenarioPromptItemApi {
  readonly role?: ScenarioPromptItemApiRole;
  /** @minLength 1 */
  readonly content?: string;
}

export interface ScenarioDetailResponseApi {
  readonly id?: string;
  /** @minLength 1 */
  readonly name?: string;
  /** @minLength 1 */
  readonly description?: string;
  /** @minLength 1 */
  readonly source?: string;
  readonly scenario_type?: ScenarioDetailResponseApiScenarioType;
  readonly dataset_id?: string;
  readonly organization?: string;
  readonly dataset?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
  readonly deleted?: boolean;
  readonly deleted_at?: string;
  readonly status?: ScenarioDetailResponseApiStatus;
  /** @minLength 1 */
  readonly agent_type?: string;
  readonly graph?: ScenarioDetailResponseApiGraph;
  readonly prompts?: readonly ScenarioPromptItemApi[];
  readonly dataset_rows?: number;
  readonly dataset_column_config?: ScenarioDetailResponseApiDatasetColumnConfig;
}

export interface ScenarioAddColumnsRequestApi {
  columns: ColumnDefinitionApi[];
}

export interface ScenarioAddColumnsResponseApi {
  /** @minLength 1 */
  readonly message?: string;
  readonly scenario_id?: string;
  readonly dataset_id?: string;
  readonly columns?: readonly string[];
}

export interface ScenarioAddRowsRequestApi {
  /**
     * @minimum 10
     * @maximum 20000
     */
  num_rows: number;
  description?: string;
}

export interface ScenarioAddRowsResponseApi {
  /** @minLength 1 */
  readonly message?: string;
  readonly scenario_id?: string;
  readonly dataset_id?: string;
  readonly num_rows?: number;
}

export interface ScenarioDeleteResponseApi {
  /** @minLength 1 */
  readonly message?: string;
}

export type ScenarioEditRequestApiGraph = { [key: string]: unknown };

export interface ScenarioEditRequestApi {
  /** @maxLength 255 */
  name?: string;
  description?: string;
  graph?: ScenarioEditRequestApiGraph;
  prompt?: string;
}

export interface ScenarioEditResponseApi {
  /** @minLength 1 */
  readonly message?: string;
  scenario?: ScenarioResponseApi;
}

export interface ScenarioEditPromptsRequestApi {
  /**
     * @minLength 1
     * @maxLength 10000
     */
  prompts: string;
}

export interface ScenarioPromptsUpdateResponseApi {
  /** @minLength 1 */
  readonly message?: string;
  /** @minLength 1 */
  readonly prompts?: string;
}

export interface SimulatorAgentApi {
  readonly id?: string;
  /**
     * Name of the simulator agent
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  /**
     * System prompt for the agent
     * @minLength 1
     */
  prompt: string;
  /**
     * Voice service provider
     * @minLength 1
     * @maxLength 100
     */
  voice_provider: string;
  /**
     * Specific voice to use
     * @minLength 1
     * @maxLength 100
     */
  voice_name: string;
  /**
     * Sensitivity for interruption detection (0-1)
     * @minimum 0
     * @maximum 11
     */
  interrupt_sensitivity?: number;
  /**
     * Speed of conversation (0.1-3.0)
     * @minimum 0.1
     * @maximum 2
     */
  conversation_speed?: number;
  /**
     * Sensitivity for detecting when speaker has finished (0-1)
     * @minimum 0
     * @maximum 11
     */
  finished_speaking_sensitivity?: number;
  /**
     * LLM model to use
     * @minLength 1
     * @maxLength 100
     */
  model: string;
  /**
     * Temperature setting for LLM (0-2)
     * @minimum 0
     * @maximum 2
     */
  llm_temperature?: number;
  /**
     * Maximum call duration in minutes (1-180)
     * @minimum 0
     * @maximum 180
     */
  max_call_duration_in_minutes?: number;
  /**
     * Delay before initial message in seconds (0-60)
     * @minimum 0
     * @maximum 60
     */
  initial_message_delay?: number;
  /** Initial message to send when conversation starts */
  initial_message?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
  /** Organization this simulator agent belongs to */
  readonly organization?: string;
  readonly deleted?: boolean;
  readonly deleted_at?: string;
  readonly logo_url?: string;
}

export interface SimulatorAgentListResponseApi {
  readonly count?: number;
  /** @minLength 1 */
  readonly next?: string;
  /** @minLength 1 */
  readonly previous?: string;
  readonly results?: readonly SimulatorAgentApi[];
  readonly total_pages?: number;
  readonly current_page?: number;
}

export interface SimulatorAgentValidationErrorResponseApi {[key: string]: string[]}

export interface SimulatorAgentDeleteResponseApi {
  /** @minLength 1 */
  readonly message?: string;
}

export type TestExecutionDetailResponseApiResultsItem = {[key: string]: string};

export type TestExecutionDetailResponseApiColumnOrderItem = {[key: string]: string};

export interface TestExecutionDetailResponseApi {
  readonly count?: number;
  /** @minLength 1 */
  readonly next?: string;
  /** @minLength 1 */
  readonly previous?: string;
  /** Call execution rows may include dynamic eval/scenario columns. */
  readonly results?: readonly TestExecutionDetailResponseApiResultsItem[];
  readonly total_pages?: number;
  readonly current_page?: number;
  readonly column_order?: readonly TestExecutionDetailResponseApiColumnOrderItem[];
  readonly error_messages?: readonly string[];
  /** @minLength 1 */
  readonly status?: string;
  /** @minLength 1 */
  readonly provider?: string;
  /** @minLength 1 */
  readonly agent_type?: string;
}

/**
 * Fail rate data for scatter plot chart
 */
export type TestExecutionAnalyticsApiFailRateOverTestRuns = {[key: string]: string};

/**
 * Evaluation categories data for line graph chart
 */
export type TestExecutionAnalyticsApiEvaluationCategoriesOverTestRuns = {[key: string]: string};

/**
 * Metadata about the analytics data
 */
export type TestExecutionAnalyticsApiMetadata = {[key: string]: string};

export interface TestExecutionAnalyticsApi {
  /** Fail rate data for scatter plot chart */
  fail_rate_over_test_runs: TestExecutionAnalyticsApiFailRateOverTestRuns;
  /** Evaluation categories data for line graph chart */
  evaluation_categories_over_test_runs: TestExecutionAnalyticsApiEvaluationCategoriesOverTestRuns;
  /** Metadata about the analytics data */
  metadata: TestExecutionAnalyticsApiMetadata;
}

export interface CancelTestExecutionResponseApi {
  success: boolean;
  /** @minLength 1 */
  message: string;
  test_execution_id: string;
}

export interface TestExecutionChatBatchResultApi {
  call_execution_ids: string[];
  has_more: boolean;
  batched_scenarios: string[];
}

export interface TestExecutionChatBatchResponseApi {
  status?: boolean;
  result: TestExecutionChatBatchResultApi;
}

export interface ColumnOrderApi {
  /** @minLength 1 */
  column_name: string;
  /** @minLength 1 */
  id: string;
  visible: boolean;
}

export interface TestExecutionColumnOrderApi {
  column_order: ColumnOrderApi[];
}

export interface TestExecutionColumnOrderResponseApi {
  /** @minLength 1 */
  readonly message?: string;
  readonly column_order?: readonly ColumnOrderApi[];
}

export interface EvalExplanationClusterApi {
  /** @minLength 1 */
  readonly kind?: string;
  /** @minLength 1 */
  readonly confidence?: string;
  /** @minLength 1 */
  readonly theme?: string;
  /** @minLength 1 */
  readonly guidance?: string;
  /** @minLength 1 */
  readonly evidenceSummary?: string;
  readonly eval_config_id?: string;
  readonly eval_template_id?: string;
  /** @minLength 1 */
  readonly eval_name?: string;
}

export type EvalExplanationSummaryResultApiResponse = {[key: string]: EvalExplanationClusterApi[]};

export interface EvalExplanationSummaryResultApi {
  response: EvalExplanationSummaryResultApiResponse;
  last_updated: string;
  /** @minLength 1 */
  status: string;
}

export interface EvalExplanationSummaryResponseApi {
  status?: boolean;
  result: EvalExplanationSummaryResultApi;
}

export interface EvalExplanationSummaryRefreshResultApi {
  /** @minLength 1 */
  message: string;
}

export interface EvalExplanationSummaryRefreshResponseApi {
  status?: boolean;
  result: EvalExplanationSummaryRefreshResultApi;
}

export type RunTestKPIsResponseApiScenarioGraphs = {[key: string]: {[key: string]: { [key: string]: unknown }}};

export interface RunTestKPIsResponseApi {
  readonly total_calls?: number;
  readonly avg_score?: number;
  readonly avg_response?: number;
  readonly calls_attempted?: number;
  readonly connected_calls?: number;
  readonly calls_connected_percentage?: number;
  readonly scenario_graphs?: RunTestKPIsResponseApiScenarioGraphs;
  /** @minLength 1 */
  readonly agent_type?: string;
  readonly is_inbound?: boolean;
  readonly avg_agent_latency?: number;
  readonly avg_user_interruption_count?: number;
  readonly avg_user_interruption_rate?: number;
  readonly avg_user_wpm?: number;
  readonly avg_bot_wpm?: number;
  readonly avg_talk_ratio?: number;
  readonly avg_ai_interruption_count?: number;
  readonly avg_ai_interruption_rate?: number;
  readonly avg_stop_time_after_interruption?: number;
  readonly agent_talk_percentage?: number;
  readonly customer_talk_percentage?: number;
  readonly avg_total_tokens?: number;
  readonly avg_input_tokens?: number;
  readonly avg_output_tokens?: number;
  readonly avg_chat_latency_ms?: number;
  readonly avg_turn_count?: number;
  readonly avg_csat_score?: number;
  readonly failed_calls?: number;
  readonly total_duration?: number;
}

export type OptimiserAnalysisResultPayloadApiResponse = {[key: string]: { [key: string]: unknown }};

export interface OptimiserAnalysisResultPayloadApi {
  response: OptimiserAnalysisResultPayloadApiResponse;
  /** @minLength 1 */
  status: string;
  last_updated?: string;
  message?: string;
}

export interface OptimiserAnalysisResponseApi {
  status?: boolean;
  result: OptimiserAnalysisResultPayloadApi;
}

export interface OptimiserAnalysisRefreshResultApi {
  /** @minLength 1 */
  message: string;
  /** @minLength 1 */
  status: string;
}

export interface OptimiserAnalysisRefreshResponseApi {
  status?: boolean;
  result: OptimiserAnalysisRefreshResultApi;
}

/**
 * Performance metrics including pass rate, total test runs, and latest fail rate
 */
export type PerformanceSummaryApiTestRunPerformanceMetrics = {[key: string]: number};

/**
 * List of top performing scenarios with their performance scores
 */
export type PerformanceSummaryApiTopPerformingScenariosItem = {[key: string]: string};

export interface PerformanceSummaryApi {
  /** Performance metrics including pass rate, total test runs, and latest fail rate */
  test_run_performance_metrics: PerformanceSummaryApiTestRunPerformanceMetrics;
  /** List of top performing scenarios */
  top_performing_scenarios: PerformanceSummaryApiTopPerformingScenariosItem[];
}

/**
 * Type of rerun: evaluation only or call plus evaluation
 */
export type CallExecutionRerunApiRerunType = typeof CallExecutionRerunApiRerunType[keyof typeof CallExecutionRerunApiRerunType];


export const CallExecutionRerunApiRerunType = {
  eval_only: 'eval_only',
  call_and_eval: 'call_and_eval',
} as const;

export interface CallExecutionRerunApi {
  /** Type of rerun: evaluation only or call plus evaluation */
  rerun_type: CallExecutionRerunApiRerunType;
  /** List of specific call execution IDs to rerun */
  call_execution_ids?: string[];
  /** Whether to rerun all call executions in the test execution */
  select_all?: boolean;
}

export interface FailedRerunItemApi {
  call_execution_id: string;
  /** @minLength 1 */
  error: string;
}

export interface RerunCallsResponseApi {
  /** @minLength 1 */
  message: string;
  test_execution_id: string;
  /** @minLength 1 */
  rerun_type: string;
  total_processed: number;
  successful_reruns: string[];
  failed_reruns: FailedRerunItemApi[];
  success_count: number;
  failure_count: number;
  /** @minLength 1 */
  dispatch_error?: string;
}

export interface TestExecutionTranscriptCallApi {
  readonly call_execution_id?: string;
  /** @minLength 1 */
  readonly phone_number?: string;
  /** @minLength 1 */
  readonly status?: string;
  readonly transcripts?: readonly CallTranscriptApi[];
  readonly total_transcripts?: number;
  /** @minLength 1 */
  readonly scenario_name?: string;
}

export interface TestExecutionTranscriptsResponseApi {
  readonly test_execution_id?: string;
  readonly calls?: readonly TestExecutionTranscriptCallApi[];
  readonly total_calls?: number;
  readonly total_transcripts?: number;
}

export interface BulkAnnotationAnnotationRequestApi {
  annotation_label_id: string;
  value?: string;
  value_float?: number;
  value_bool?: boolean;
  value_str_list?: string[];
}

export interface BulkAnnotationNoteRequestApi {
  /** @minLength 1 */
  text: string;
}

export interface BulkAnnotationRecordRequestApi {
  /** @minLength 1 */
  observation_span_id: string;
  annotations?: BulkAnnotationAnnotationRequestApi[];
  notes?: BulkAnnotationNoteRequestApi[];
}

export interface BulkAnnotationRequestApi {
  records: BulkAnnotationRecordRequestApi[];
}

export type BulkAnnotationResponseResultApiWarningsItem = { [key: string]: unknown };

export type BulkAnnotationResponseResultApiErrorsItem = { [key: string]: unknown };

export interface BulkAnnotationResponseResultApi {
  /** @minLength 1 */
  message: string;
  annotations_created: number;
  annotations_updated: number;
  notes_created: number;
  succeeded_count: number;
  errors_count: number;
  warnings_count: number;
  warnings?: BulkAnnotationResponseResultApiWarningsItem[];
  errors?: BulkAnnotationResponseResultApiErrorsItem[];
}

export interface BulkAnnotationResponseApi {
  status?: boolean;
  result: BulkAnnotationResponseResultApi;
}

export type ApiErrorResponseApiType = typeof ApiErrorResponseApiType[keyof typeof ApiErrorResponseApiType];


export const ApiErrorResponseApiType = {
  validation_error: 'validation_error',
  authentication_error: 'authentication_error',
  payment_required: 'payment_required',
  entitlement_error: 'entitlement_error',
  permission_error: 'permission_error',
  not_found: 'not_found',
  conflict: 'conflict',
  client_error: 'client_error',
  rate_limit: 'rate_limit',
  server_error: 'server_error',
  service_unavailable: 'service_unavailable',
  timeout: 'timeout',
  api_error: 'api_error',
} as const;

export type ApiErrorResponseApiDetails = {[key: string]: string[]};

export interface ApiErrorResponseApi {
  status?: boolean;
  type?: ApiErrorResponseApiType;
  code?: string;
  detail?: string;
  result?: string;
  message?: string;
  error?: string;
  attr?: string;
  details?: ApiErrorResponseApiDetails;
}

export type FetchGraphApiFiltersItemFilterConfig = {
  /** Canonical field type, for example text, number, boolean, datetime, categorical, thumbs, annotator, or array. */
  filter_type: string;
  /** Canonical operator from api_contracts/filter_contract.json, for example equals, not_equals, in, not_in, between, not_between, is_null, or is_not_null. */
  filter_op: string;
  /** Scalar, list, range tuple, boolean, or null depending on filter_op and filter_type. */
  filter_value?: unknown;
  /** Column family such as SYSTEM_METRIC, SPAN_ATTRIBUTE, EVAL_METRIC, ANNOTATION, or NORMAL. */
  col_type?: string;
};

export type FetchGraphApiFiltersItem = {
  /** Column or attribute id to filter on. */
  column_id: string;
  /** Optional UI label for chips and saved views. */
  display_name?: string;
  /** Optional source surface for mixed-source filters, for example traces, datasets, or simulation. */
  source?: string;
  /** Optional metric output type metadata used by eval and annotation filters. */
  output_type?: string;
  filter_config: FetchGraphApiFiltersItemFilterConfig;
};

export interface FetchGraphApi {
  /** @minLength 1 */
  interval: string;
  filters?: FetchGraphApiFiltersItem[];
  property?: string;
  req_data_config: string;
  project_id: string;
}

export type CustomEvalConfigApiConfig = { [key: string]: unknown };

export type CustomEvalConfigApiMapping = { [key: string]: unknown };

export type CustomEvalConfigApiFilters = { [key: string]: unknown };

export interface CustomEvalConfigApi {
  readonly id?: string;
  eval_template: string;
  /** @maxLength 255 */
  name?: string;
  config?: CustomEvalConfigApiConfig;
  mapping?: CustomEvalConfigApiMapping;
  project: string;
  filters?: CustomEvalConfigApiFilters;
  error_localizer?: boolean;
  kb_id?: string;
  /** @maxLength 255 */
  model?: string;
  readonly eval_group?: string;
}

export interface DashboardApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  description?: string;
  readonly workspace?: string;
  created_by?: UserApi;
  updated_by?: UserApi;
  readonly created_at?: string;
  readonly updated_at?: string;
  readonly widget_count?: string;
}

export interface DashboardCreateUpdateApi {
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  description?: string;
}

/**
 * Any valid JSON value.
 */
export type DashboardMetricCatalogItemApiChoicesItem = { [key: string]: unknown };

export interface DashboardMetricCatalogItemApi {
  /** @minLength 1 */
  name: string;
  display_name?: string;
  category?: string;
  source?: string;
  sources?: string[];
  type?: string;
  unit?: string;
  output_type?: string;
  choices?: DashboardMetricCatalogItemApiChoicesItem[];
  allowed_aggregations?: string[];
  data_type?: string;
}

export interface DashboardMetricsCatalogResultApi {
  metrics: DashboardMetricCatalogItemApi[];
}

export interface DashboardMetricsCatalogResponseApi {
  status?: boolean;
  result: DashboardMetricsCatalogResultApi;
}

export type DashboardQueryApiWorkflow = typeof DashboardQueryApiWorkflow[keyof typeof DashboardQueryApiWorkflow];


export const DashboardQueryApiWorkflow = {
  observability: 'observability',
  dataset: 'dataset',
  simulation: 'simulation',
} as const;

export type DashboardQueryApiGranularity = typeof DashboardQueryApiGranularity[keyof typeof DashboardQueryApiGranularity];


export const DashboardQueryApiGranularity = {
  minute: 'minute',
  hour: 'hour',
  day: 'day',
  week: 'week',
  month: 'month',
} as const;

export type DashboardQueryApiFiltersItemFilterConfig = {
  /** Canonical field type, for example text, number, boolean, datetime, categorical, thumbs, annotator, or array. */
  filter_type: string;
  /** Canonical operator from api_contracts/filter_contract.json, for example equals, not_equals, in, not_in, between, not_between, is_null, or is_not_null. */
  filter_op: string;
  /** Scalar, list, range tuple, boolean, or null depending on filter_op and filter_type. */
  filter_value?: unknown;
  /** Column family such as SYSTEM_METRIC, SPAN_ATTRIBUTE, EVAL_METRIC, ANNOTATION, or NORMAL. */
  col_type?: string;
};

export type DashboardQueryApiFiltersItem = {
  /** Column or attribute id to filter on. */
  column_id: string;
  /** Optional UI label for chips and saved views. */
  display_name?: string;
  /** Optional source surface for mixed-source filters, for example traces, datasets, or simulation. */
  source?: string;
  /** Optional metric output type metadata used by eval and annotation filters. */
  output_type?: string;
  filter_config: DashboardQueryApiFiltersItemFilterConfig;
};

export type DashboardTimeRangeApiPreset = typeof DashboardTimeRangeApiPreset[keyof typeof DashboardTimeRangeApiPreset];


export const DashboardTimeRangeApiPreset = {
  '30m': '30m',
  '6h': '6h',
  today: 'today',
  yesterday: 'yesterday',
  '7D': '7D',
  '30D': '30D',
  '3M': '3M',
  '6M': '6M',
  '12M': '12M',
} as const;

export interface DashboardTimeRangeApi {
  preset?: DashboardTimeRangeApiPreset;
  custom_start?: string;
  custom_end?: string;
}

export type DashboardMetricApiType = typeof DashboardMetricApiType[keyof typeof DashboardMetricApiType];


export const DashboardMetricApiType = {
  system_metric: 'system_metric',
  eval_metric: 'eval_metric',
  annotation_metric: 'annotation_metric',
  custom_attribute: 'custom_attribute',
  custom_column: 'custom_column',
} as const;

export type DashboardMetricApiSource = typeof DashboardMetricApiSource[keyof typeof DashboardMetricApiSource];


export const DashboardMetricApiSource = {
  traces: 'traces',
  datasets: 'datasets',
  simulation: 'simulation',
  both: 'both',
  all: 'all',
} as const;

export type DashboardMetricApiAggregation = typeof DashboardMetricApiAggregation[keyof typeof DashboardMetricApiAggregation];


export const DashboardMetricApiAggregation = {
  avg: 'avg',
  median: 'median',
  max: 'max',
  min: 'min',
  p25: 'p25',
  p50: 'p50',
  p75: 'p75',
  p90: 'p90',
  p95: 'p95',
  p99: 'p99',
  count: 'count',
  count_distinct: 'count_distinct',
  sum: 'sum',
  pass_rate: 'pass_rate',
  fail_rate: 'fail_rate',
  pass_count: 'pass_count',
  fail_count: 'fail_count',
  true_rate: 'true_rate',
} as const;

export type DashboardMetricApiAttributeType = typeof DashboardMetricApiAttributeType[keyof typeof DashboardMetricApiAttributeType];


export const DashboardMetricApiAttributeType = {
  string: 'string',
  text: 'text',
  number: 'number',
  float: 'float',
  integer: 'integer',
  boolean: 'boolean',
  datetime: 'datetime',
  date: 'date',
} as const;

export type DashboardMetricApiDataType = typeof DashboardMetricApiDataType[keyof typeof DashboardMetricApiDataType];


export const DashboardMetricApiDataType = {
  string: 'string',
  text: 'text',
  number: 'number',
  float: 'float',
  integer: 'integer',
  boolean: 'boolean',
  datetime: 'datetime',
  date: 'date',
} as const;

export type DashboardMetricApiFiltersItemFilterConfig = {
  /** Canonical field type, for example text, number, boolean, datetime, categorical, thumbs, annotator, or array. */
  filter_type: string;
  /** Canonical operator from api_contracts/filter_contract.json, for example equals, not_equals, in, not_in, between, not_between, is_null, or is_not_null. */
  filter_op: string;
  /** Scalar, list, range tuple, boolean, or null depending on filter_op and filter_type. */
  filter_value?: unknown;
  /** Column family such as SYSTEM_METRIC, SPAN_ATTRIBUTE, EVAL_METRIC, ANNOTATION, or NORMAL. */
  col_type?: string;
};

export type DashboardMetricApiFiltersItem = {
  /** Column or attribute id to filter on. */
  column_id: string;
  /** Optional UI label for chips and saved views. */
  display_name?: string;
  /** Optional source surface for mixed-source filters, for example traces, datasets, or simulation. */
  source?: string;
  /** Optional metric output type metadata used by eval and annotation filters. */
  output_type?: string;
  filter_config: DashboardMetricApiFiltersItemFilterConfig;
};

export interface DashboardMetricApi {
  id?: string;
  /** @minLength 1 */
  name: string;
  display_name?: string;
  type: DashboardMetricApiType;
  source?: DashboardMetricApiSource;
  aggregation?: DashboardMetricApiAggregation;
  unit?: string;
  output_type?: string;
  eval_key?: string;
  config_id?: string;
  label_id?: string;
  attribute_key?: string;
  attribute_type?: DashboardMetricApiAttributeType;
  column_id?: string;
  data_type?: DashboardMetricApiDataType;
  filters?: DashboardMetricApiFiltersItem[];
}

export type DashboardBreakdownApiType = typeof DashboardBreakdownApiType[keyof typeof DashboardBreakdownApiType];


export const DashboardBreakdownApiType = {
  system_metric: 'system_metric',
  eval_metric: 'eval_metric',
  annotation_metric: 'annotation_metric',
  custom_attribute: 'custom_attribute',
  custom_column: 'custom_column',
} as const;

export type DashboardBreakdownApiSource = typeof DashboardBreakdownApiSource[keyof typeof DashboardBreakdownApiSource];


export const DashboardBreakdownApiSource = {
  traces: 'traces',
  datasets: 'datasets',
  simulation: 'simulation',
  both: 'both',
  all: 'all',
} as const;

export type DashboardBreakdownApiAttributeType = typeof DashboardBreakdownApiAttributeType[keyof typeof DashboardBreakdownApiAttributeType];


export const DashboardBreakdownApiAttributeType = {
  string: 'string',
  text: 'text',
  number: 'number',
  float: 'float',
  integer: 'integer',
  boolean: 'boolean',
  datetime: 'datetime',
  date: 'date',
} as const;

export type DashboardBreakdownApiDataType = typeof DashboardBreakdownApiDataType[keyof typeof DashboardBreakdownApiDataType];


export const DashboardBreakdownApiDataType = {
  string: 'string',
  text: 'text',
  number: 'number',
  float: 'float',
  integer: 'integer',
  boolean: 'boolean',
  datetime: 'datetime',
  date: 'date',
} as const;

export interface DashboardBreakdownApi {
  /** @minLength 1 */
  name: string;
  display_name?: string;
  type?: DashboardBreakdownApiType;
  source?: DashboardBreakdownApiSource;
  output_type?: string;
  label_id?: string;
  config_id?: string;
  eval_key?: string;
  attribute_key?: string;
  attribute_type?: DashboardBreakdownApiAttributeType;
  column_id?: string;
  data_type?: DashboardBreakdownApiDataType;
}

export interface DashboardQueryApi {
  workflow?: DashboardQueryApiWorkflow;
  project_ids?: string[];
  time_range: DashboardTimeRangeApi;
  granularity?: DashboardQueryApiGranularity;
  metrics: DashboardMetricApi[];
  filters?: DashboardQueryApiFiltersItem[];
  breakdowns?: DashboardBreakdownApi[];
}

export type DashboardQueryMetricResultApiAggregation = typeof DashboardQueryMetricResultApiAggregation[keyof typeof DashboardQueryMetricResultApiAggregation];


export const DashboardQueryMetricResultApiAggregation = {
  avg: 'avg',
  median: 'median',
  max: 'max',
  min: 'min',
  p25: 'p25',
  p50: 'p50',
  p75: 'p75',
  p90: 'p90',
  p95: 'p95',
  p99: 'p99',
  count: 'count',
  count_distinct: 'count_distinct',
  sum: 'sum',
  pass_rate: 'pass_rate',
  fail_rate: 'fail_rate',
  pass_count: 'pass_count',
  fail_count: 'fail_count',
  true_rate: 'true_rate',
} as const;

export interface DashboardQuerySeriesPointApi {
  /** @minLength 1 */
  timestamp: string;
  value: number;
}

export interface DashboardQuerySeriesApi {
  /** @minLength 1 */
  name: string;
  data: DashboardQuerySeriesPointApi[];
}

export interface DashboardQueryMetricResultApi {
  id: string;
  name: string;
  aggregation: DashboardQueryMetricResultApiAggregation;
  unit: string;
  series: DashboardQuerySeriesApi[];
}

export interface DashboardQueryTimeRangeResultApi {
  /** @minLength 1 */
  start: string;
  /** @minLength 1 */
  end: string;
}

export type DashboardQueryResultApiGranularity = typeof DashboardQueryResultApiGranularity[keyof typeof DashboardQueryResultApiGranularity];


export const DashboardQueryResultApiGranularity = {
  minute: 'minute',
  hour: 'hour',
  day: 'day',
  week: 'week',
  month: 'month',
} as const;

export interface DashboardQueryResultApi {
  metrics: DashboardQueryMetricResultApi[];
  time_range: DashboardQueryTimeRangeResultApi;
  granularity: DashboardQueryResultApiGranularity;
}

export interface DashboardQueryApiResponseApi {
  status?: boolean;
  result: DashboardQueryResultApi;
}

export type DashboardWidgetApiQueryConfig = { [key: string]: unknown };

export type DashboardWidgetApiChartConfig = { [key: string]: unknown };

export interface DashboardWidgetApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name?: string;
  description?: string;
  /**
     * @minimum -2147483648
     * @maximum 2147483647
     */
  position?: number;
  /**
     * @minimum -2147483648
     * @maximum 2147483647
     */
  width?: number;
  /**
     * @minimum -2147483648
     * @maximum 2147483647
     */
  height?: number;
  query_config?: DashboardWidgetApiQueryConfig;
  chart_config?: DashboardWidgetApiChartConfig;
  readonly created_by?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
}

export interface DashboardPreviewQueryApi {
  query_config: DashboardQueryApi;
}

export interface DashboardDetailApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  description?: string;
  readonly workspace?: string;
  created_by?: UserApi;
  updated_by?: UserApi;
  readonly created_at?: string;
  readonly updated_at?: string;
  readonly widgets?: string;
}

export type ObserveDatasetApiModelType = typeof ObserveDatasetApiModelType[keyof typeof ObserveDatasetApiModelType];


export const ObserveDatasetApiModelType = {
  Numeric: 'Numeric',
  ScoreCategorical: 'ScoreCategorical',
  Ranking: 'Ranking',
  BinaryClassification: 'BinaryClassification',
  Regression: 'Regression',
  ObjectDetection: 'ObjectDetection',
  Segmentation: 'Segmentation',
  GenerativeLLM: 'GenerativeLLM',
  GenerativeImage: 'GenerativeImage',
  GenerativeVideo: 'GenerativeVideo',
  TTS: 'TTS',
  STT: 'STT',
  MultiModal: 'MultiModal',
} as const;

export type ObserveDatasetApiSource = typeof ObserveDatasetApiSource[keyof typeof ObserveDatasetApiSource];


export const ObserveDatasetApiSource = {
  demo: 'demo',
  build: 'build',
  sdk: 'sdk',
  observe: 'observe',
  knowledge_base: 'knowledge_base',
  scenario: 'scenario',
  experiment_snapshot: 'experiment_snapshot',
  graph: 'graph',
} as const;

export interface ObserveDatasetApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 2000
     */
  name: string;
  readonly organization?: string;
  model_type?: ObserveDatasetApiModelType;
  source?: ObserveDatasetApiSource;
  readonly user?: string;
}

export type EvalTaskApiFiltersFiltersItemFilterConfig = {
  /** Canonical field type, for example text, number, boolean, datetime, categorical, thumbs, annotator, or array. */
  filter_type: string;
  /** Canonical operator from api_contracts/filter_contract.json, for example equals, not_equals, in, not_in, between, not_between, is_null, or is_not_null. */
  filter_op: string;
  /** Scalar, list, range tuple, boolean, or null depending on filter_op and filter_type. */
  filter_value?: unknown;
  /** Column family such as SYSTEM_METRIC, SPAN_ATTRIBUTE, EVAL_METRIC, ANNOTATION, or NORMAL. */
  col_type?: string;
};

export type EvalTaskApiFiltersFiltersItem = {
  /** Column or attribute id to filter on. */
  column_id: string;
  /** Optional UI label for chips and saved views. */
  display_name?: string;
  /** Optional source surface for mixed-source filters, for example traces, datasets, or simulation. */
  source?: string;
  /** Optional metric output type metadata used by eval and annotation filters. */
  output_type?: string;
  filter_config: EvalTaskApiFiltersFiltersItemFilterConfig;
};

export type EvalTaskApiFiltersSpanAttributesFiltersItemFilterConfig = {
  /** Canonical field type, for example text, number, boolean, datetime, categorical, thumbs, annotator, or array. */
  filter_type: string;
  /** Canonical operator from api_contracts/filter_contract.json, for example equals, not_equals, in, not_in, between, not_between, is_null, or is_not_null. */
  filter_op: string;
  /** Scalar, list, range tuple, boolean, or null depending on filter_op and filter_type. */
  filter_value?: unknown;
  /** Column family such as SYSTEM_METRIC, SPAN_ATTRIBUTE, EVAL_METRIC, ANNOTATION, or NORMAL. */
  col_type?: string;
};

export type EvalTaskApiFiltersSpanAttributesFiltersItem = {
  /** Column or attribute id to filter on. */
  column_id: string;
  /** Optional UI label for chips and saved views. */
  display_name?: string;
  /** Optional source surface for mixed-source filters, for example traces, datasets, or simulation. */
  source?: string;
  /** Optional metric output type metadata used by eval and annotation filters. */
  output_type?: string;
  filter_config: EvalTaskApiFiltersSpanAttributesFiltersItemFilterConfig;
};

export type EvalTaskApiFilters = {
  /** Project scope for the evaluation task. */
  project_id?: string;
  /**
     * Inclusive start/end ISO timestamps.
     * @minItems 2
     * @maxItems 2
     */
  date_range?: string[];
  /** Lower-bound ISO timestamp for legacy task filters. */
  created_at?: string;
  /** Trace session id(s) to constrain the task. */
  session_id?: string[];
  /** Trace id(s) to constrain linked-source tasks. */
  trace_id?: string[];
  /** Observation span id(s) to constrain linked-source tasks. */
  span_id?: string[];
  /** Observation span type(s), for example llm, tool, or chain. */
  observation_type?: string[];
  filters?: EvalTaskApiFiltersFiltersItem[];
  span_attributes_filters?: EvalTaskApiFiltersSpanAttributesFiltersItem[];
};

export type EvalTaskApiRunType = typeof EvalTaskApiRunType[keyof typeof EvalTaskApiRunType];


export const EvalTaskApiRunType = {
  continuous: 'continuous',
  historical: 'historical',
} as const;

export type EvalTaskApiRowType = typeof EvalTaskApiRowType[keyof typeof EvalTaskApiRowType];


export const EvalTaskApiRowType = {
  spans: 'spans',
  traces: 'traces',
  sessions: 'sessions',
  voiceCalls: 'voiceCalls',
} as const;

export type EvalTaskApiStatus = typeof EvalTaskApiStatus[keyof typeof EvalTaskApiStatus];


export const EvalTaskApiStatus = {
  pending: 'pending',
  running: 'running',
  completed: 'completed',
  failed: 'failed',
  paused: 'paused',
  deleted: 'deleted',
} as const;

export type EvalTaskApiEvalsDetails = { [key: string]: unknown };

export type EvalTaskApiFailedSpans = { [key: string]: unknown };

export interface EvalTaskApi {
  readonly id?: string;
  project: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  filters?: EvalTaskApiFilters;
  /**
     * @minimum 1
     * @maximum 100
     */
  sampling_rate: number;
  last_run?: string;
  /**
     * @minimum 1
     * @maximum 1000000
     */
  spans_limit?: number;
  run_type: EvalTaskApiRunType;
  row_type?: EvalTaskApiRowType;
  status?: EvalTaskApiStatus;
  start_time?: string;
  end_time?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
  evals_details?: EvalTaskApiEvalsDetails;
  evals: string[];
  failed_spans?: EvalTaskApiFailedSpans;
  readonly progress?: string;
}

export interface EvalTaskCreateResultApi {
  id: string;
}

export interface EvalTaskCreateResponseApi {
  status?: boolean;
  result: EvalTaskCreateResultApi;
}

export interface EvalTaskDeleteRequestApi {
  eval_task_ids: string[];
}

export interface EvalTaskMessageResultApi {
  /** @minLength 1 */
  message: string;
}

export interface EvalTaskMessageResponseApi {
  status?: boolean;
  result: EvalTaskMessageResultApi;
}

export type EvalTaskUpdateRequestApiFiltersFiltersItemFilterConfig = {
  /** Canonical field type, for example text, number, boolean, datetime, categorical, thumbs, annotator, or array. */
  filter_type: string;
  /** Canonical operator from api_contracts/filter_contract.json, for example equals, not_equals, in, not_in, between, not_between, is_null, or is_not_null. */
  filter_op: string;
  /** Scalar, list, range tuple, boolean, or null depending on filter_op and filter_type. */
  filter_value?: unknown;
  /** Column family such as SYSTEM_METRIC, SPAN_ATTRIBUTE, EVAL_METRIC, ANNOTATION, or NORMAL. */
  col_type?: string;
};

export type EvalTaskUpdateRequestApiFiltersFiltersItem = {
  /** Column or attribute id to filter on. */
  column_id: string;
  /** Optional UI label for chips and saved views. */
  display_name?: string;
  /** Optional source surface for mixed-source filters, for example traces, datasets, or simulation. */
  source?: string;
  /** Optional metric output type metadata used by eval and annotation filters. */
  output_type?: string;
  filter_config: EvalTaskUpdateRequestApiFiltersFiltersItemFilterConfig;
};

export type EvalTaskUpdateRequestApiFiltersSpanAttributesFiltersItemFilterConfig = {
  /** Canonical field type, for example text, number, boolean, datetime, categorical, thumbs, annotator, or array. */
  filter_type: string;
  /** Canonical operator from api_contracts/filter_contract.json, for example equals, not_equals, in, not_in, between, not_between, is_null, or is_not_null. */
  filter_op: string;
  /** Scalar, list, range tuple, boolean, or null depending on filter_op and filter_type. */
  filter_value?: unknown;
  /** Column family such as SYSTEM_METRIC, SPAN_ATTRIBUTE, EVAL_METRIC, ANNOTATION, or NORMAL. */
  col_type?: string;
};

export type EvalTaskUpdateRequestApiFiltersSpanAttributesFiltersItem = {
  /** Column or attribute id to filter on. */
  column_id: string;
  /** Optional UI label for chips and saved views. */
  display_name?: string;
  /** Optional source surface for mixed-source filters, for example traces, datasets, or simulation. */
  source?: string;
  /** Optional metric output type metadata used by eval and annotation filters. */
  output_type?: string;
  filter_config: EvalTaskUpdateRequestApiFiltersSpanAttributesFiltersItemFilterConfig;
};

export type EvalTaskUpdateRequestApiFilters = {
  /** Project scope for the evaluation task. */
  project_id?: string;
  /**
     * Inclusive start/end ISO timestamps.
     * @minItems 2
     * @maxItems 2
     */
  date_range?: string[];
  /** Lower-bound ISO timestamp for legacy task filters. */
  created_at?: string;
  /** Trace session id(s) to constrain the task. */
  session_id?: string[];
  /** Trace id(s) to constrain linked-source tasks. */
  trace_id?: string[];
  /** Observation span id(s) to constrain linked-source tasks. */
  span_id?: string[];
  /** Observation span type(s), for example llm, tool, or chain. */
  observation_type?: string[];
  filters?: EvalTaskUpdateRequestApiFiltersFiltersItem[];
  span_attributes_filters?: EvalTaskUpdateRequestApiFiltersSpanAttributesFiltersItem[];
};

export type EvalTaskUpdateRequestApiRunType = typeof EvalTaskUpdateRequestApiRunType[keyof typeof EvalTaskUpdateRequestApiRunType];


export const EvalTaskUpdateRequestApiRunType = {
  continuous: 'continuous',
  historical: 'historical',
} as const;

export type EvalTaskUpdateRequestApiRowType = typeof EvalTaskUpdateRequestApiRowType[keyof typeof EvalTaskUpdateRequestApiRowType];


export const EvalTaskUpdateRequestApiRowType = {
  spans: 'spans',
  traces: 'traces',
  sessions: 'sessions',
  voiceCalls: 'voiceCalls',
} as const;

export type EvalTaskUpdateRequestApiStatus = typeof EvalTaskUpdateRequestApiStatus[keyof typeof EvalTaskUpdateRequestApiStatus];


export const EvalTaskUpdateRequestApiStatus = {
  pending: 'pending',
  running: 'running',
  completed: 'completed',
  failed: 'failed',
  paused: 'paused',
  deleted: 'deleted',
} as const;

export type EvalTaskUpdateRequestApiEditType = typeof EvalTaskUpdateRequestApiEditType[keyof typeof EvalTaskUpdateRequestApiEditType];


export const EvalTaskUpdateRequestApiEditType = {
  edit_rerun: 'edit_rerun',
  fresh_run: 'fresh_run',
} as const;

export interface EvalTaskUpdateRequestApi {
  /**
     * @minLength 1
     * @maxLength 255
     */
  name?: string;
  filters?: EvalTaskUpdateRequestApiFilters;
  /**
     * @minimum 1
     * @maximum 100
     */
  sampling_rate?: number;
  /**
     * @minimum 1
     * @maximum 1000000
     */
  spans_limit?: number;
  run_type?: EvalTaskUpdateRequestApiRunType;
  row_type?: EvalTaskUpdateRequestApiRowType;
  status?: EvalTaskUpdateRequestApiStatus;
  evals?: string[];
  edit_type: EvalTaskUpdateRequestApiEditType;
  eval_task_id: string;
}

export type EvalTaskUpdateResultApiEditType = typeof EvalTaskUpdateResultApiEditType[keyof typeof EvalTaskUpdateResultApiEditType];


export const EvalTaskUpdateResultApiEditType = {
  edit_rerun: 'edit_rerun',
  fresh_run: 'fresh_run',
} as const;

export interface EvalTaskUpdateResultApi {
  /** @minLength 1 */
  message: string;
  edit_type: EvalTaskUpdateResultApiEditType;
  task_id: string;
}

export interface EvalTaskUpdateResponseApi {
  status?: boolean;
  result: EvalTaskUpdateResultApi;
}

export interface LinearTeamApi {
  /** @minLength 1 */
  id: string;
  /** @minLength 1 */
  name: string;
  key?: string;
}

export interface LinearTeamsResultApi {
  connected: boolean;
  teams: LinearTeamApi[];
}

export interface LinearTeamsResponseApi {
  status?: boolean;
  result: LinearTeamsResultApi;
}

export interface ErrorNameApi {
  /** @minLength 1 */
  name: string;
  type: string;
}

export interface TrendPointApi {
  timestamp: string;
  value: number;
  users: number;
}

export interface FeedListRowApi {
  /** @minLength 1 */
  cluster_id: string;
  /** @minLength 1 */
  source: string;
  /** @minLength 1 */
  modality: string;
  error: ErrorNameApi;
  /** @minLength 1 */
  status: string;
  /** @minLength 1 */
  severity: string;
  occurrences: number;
  trace_count: number;
  /** @minLength 1 */
  fix_layer: string;
  users_affected: number;
  sessions: number;
  first_seen: string;
  last_seen: string;
  trends: TrendPointApi[];
  assignees: string[];
  /** @minLength 1 */
  model: string;
  /** @minLength 1 */
  model_version: string;
  /** @minLength 1 */
  project: string;
  /** @minLength 1 */
  project_id: string;
  /** @minLength 1 */
  environment: string;
  eval_score: number;
  /** @minLength 1 */
  trace_id: string;
  /** @minLength 1 */
  external_issue_url: string;
  /** @minLength 1 */
  external_issue_id: string;
}

export interface FeedListResponseApi {
  data: FeedListRowApi[];
  total: number;
  limit: number;
  offset: number;
}

export interface FeedListApiResponseApi {
  status?: boolean;
  result: FeedListResponseApi;
}

export interface FeedStatsApi {
  total_errors: number;
  escalating: number;
  for_review: number;
  acknowledged: number;
  resolved: number;
  affected_users: number;
}

export interface FeedStatsApiResponseApi {
  status?: boolean;
  result: FeedStatsApi;
}

export interface TracePreviewApi {
  /** @minLength 1 */
  trace_id: string;
  /** @minLength 1 */
  input: string;
  /** @minLength 1 */
  output: string;
}

export type RcaTrailStepApiArgs = { [key: string]: unknown };

export type RcaTrailStepApiResult = { [key: string]: unknown };

export interface RcaTrailStepApi {
  /** @minLength 1 */
  type: string;
  text?: string;
  /** @minLength 1 */
  call_id?: string;
  /** @minLength 1 */
  tool?: string;
  args?: RcaTrailStepApiArgs;
  result?: RcaTrailStepApiResult;
  synthesis?: string;
  fix?: string;
  /** @minLength 1 */
  confidence?: string;
}

export interface RcaSummaryApi {
  /** @minLength 1 */
  synthesis?: string;
  /** @minLength 1 */
  fix?: string;
  /** @minLength 1 */
  confidence?: string;
  evidence_trace_ids?: string[];
  analyzed_at?: string;
  failures_at_run?: number;
  trace?: RcaTrailStepApi[];
}

export interface FeedDetailCoreApi {
  row: FeedListRowApi;
  /** @minLength 1 */
  description: string;
  success_trace: TracePreviewApi;
  representative_trace: TracePreviewApi;
  rca?: RcaSummaryApi;
}

export interface FeedDetailApiResponseApi {
  status?: boolean;
  result: FeedDetailCoreApi;
}

export type FeedUpdateBodyApiStatus = typeof FeedUpdateBodyApiStatus[keyof typeof FeedUpdateBodyApiStatus];


export const FeedUpdateBodyApiStatus = {
  escalating: 'escalating',
  for_review: 'for_review',
  acknowledged: 'acknowledged',
  resolved: 'resolved',
} as const;

export type FeedUpdateBodyApiSeverity = typeof FeedUpdateBodyApiSeverity[keyof typeof FeedUpdateBodyApiSeverity];


export const FeedUpdateBodyApiSeverity = {
  critical: 'critical',
  high: 'high',
  medium: 'medium',
  low: 'low',
} as const;

export interface FeedUpdateBodyApi {
  project_id?: string;
  status?: FeedUpdateBodyApiStatus;
  severity?: FeedUpdateBodyApiSeverity;
  /** @minLength 1 */
  assignee?: string;
}

export interface CreateLinearIssueApi {
  /** @minLength 1 */
  team_id: string;
  trace_id?: string;
  title?: string;
  description?: string;
  priority?: number;
}

export interface CreateLinearIssueResultApi {
  already_linked?: boolean;
  /** @minLength 1 */
  issue_id?: string;
  /** @minLength 1 */
  issue_url?: string;
  /** @minLength 1 */
  issue_title?: string;
}

export interface CreateLinearIssueResponseApi {
  status?: boolean;
  result: CreateLinearIssueResultApi;
}

export interface DeepAnalysisBodyApi {
  /** @minLength 1 */
  trace_id: string;
  force?: boolean;
}

export interface DeepAnalysisDispatchResponseApi {
  /** @minLength 1 */
  status: string;
  /** @minLength 1 */
  trace_id: string;
}

export interface DeepAnalysisDispatchApiResponseApi {
  status?: boolean;
  result: DeepAnalysisDispatchResponseApi;
}

export interface EventsOverTimePointApi {
  /** @minLength 1 */
  date: string;
  errors: number;
  passing: number;
  users: number;
}

export interface PatternInsightEvidenceApi {
  /** @minLength 1 */
  test?: string;
  /** @minLength 1 */
  baseline?: string;
  /** @minLength 1 */
  tool?: string;
  z?: number;
  p_value?: number;
  ks_stat?: number;
  fail_median?: number;
  baseline_median?: number;
  fail_pct?: number;
  baseline_pct?: number;
  hits?: number;
  total?: number;
  missing_in?: number;
  traces_with_tools?: number;
}

export interface PatternInsightApi {
  /** @minLength 1 */
  title?: string;
  /** @minLength 1 */
  value: string;
  caption: string;
  evidence?: PatternInsightEvidenceApi;
}

export interface KeyMomentApi {
  /** @minLength 1 */
  kevinified: string;
  verbatim: string;
}

export interface PatternSummaryApi {
  insights: PatternInsightApi[];
  key_moments: KeyMomentApi[];
}

export interface TraceSummaryApi {
  eval_score: number;
  latency_ms: number;
  turns: number;
  /** @minLength 1 */
  model: string;
  input_tokens: number;
  output_tokens: number;
}

export type TraceEvidenceApiFailReelItem = {[key: string]: string};

export type TraceEvidenceApiPassReelItem = {[key: string]: string};

export interface TraceEvidenceApi {
  /** @minLength 1 */
  input: string;
  /** @minLength 1 */
  output: string;
  fail_reel: TraceEvidenceApiFailReelItem[];
  pass_reel: TraceEvidenceApiPassReelItem[];
  /** @minLength 1 */
  judge_reason?: string;
  score?: number;
}

export type AgentFlowGraphApiNodesItem = {[key: string]: string};

export type AgentFlowGraphApiEdgesItem = {[key: string]: string};

export interface AgentFlowGraphApi {
  nodes: AgentFlowGraphApiNodesItem[];
  edges: AgentFlowGraphApiEdgesItem[];
}

export type RepresentativeTraceApiRootCausesItem = {[key: string]: string};

export type RepresentativeTraceApiRecommendationsItem = {[key: string]: string};

export type RepresentativeTraceApiWhatChanged = {[key: string]: string};

export interface RepresentativeTraceApi {
  /** @minLength 1 */
  id: string;
  /** @minLength 1 */
  status: string;
  timestamp: string;
  summary: TraceSummaryApi;
  evidence: TraceEvidenceApi;
  agent_flow: AgentFlowGraphApi;
  root_causes: RepresentativeTraceApiRootCausesItem[];
  recommendations: RepresentativeTraceApiRecommendationsItem[];
  what_changed: RepresentativeTraceApiWhatChanged;
}

export interface OverviewResponseApi {
  events_over_time: EventsOverTimePointApi[];
  pattern_summary: PatternSummaryApi;
  representative_traces: RepresentativeTraceApi[];
  representative_total?: number;
}

export interface OverviewApiResponseApi {
  status?: boolean;
  result: OverviewResponseApi;
}

export interface RootCauseApi {
  rank: number;
  /** @minLength 1 */
  title: string;
  /** @minLength 1 */
  description: string;
}

export interface RecommendationApi {
  /** @minLength 1 */
  id: string;
  /** @minLength 1 */
  title: string;
  description: string;
  /** @minLength 1 */
  priority: string;
  root_cause_link: number;
  /** @minLength 1 */
  immediate_fix: string;
  /** @minLength 1 */
  insights: string;
  evidence: string[];
}

export interface DeepAnalysisResponseApi {
  /** @minLength 1 */
  status: string;
  /** @minLength 1 */
  trace_id: string;
  root_causes: RootCauseApi[];
  recommendations: RecommendationApi[];
  /** @minLength 1 */
  immediate_fix: string;
}

export interface DeepAnalysisApiResponseApi {
  status?: boolean;
  result: DeepAnalysisResponseApi;
}

export interface SidebarTimelineApi {
  first_seen: string;
  last_seen: string;
  age_days: number;
}

export interface SidebarAIMetadataApi {
  /** @minLength 1 */
  model: string;
  /** @minLength 1 */
  model_version: string;
  /** @minLength 1 */
  project: string;
  eval_score: number;
  /** @minLength 1 */
  trace_id: string;
}

export interface EvaluationResultApi {
  /** @minLength 1 */
  label: string;
  /** @minLength 1 */
  type: string;
  /** @minLength 1 */
  result: string;
  score: number;
  /** @minLength 1 */
  value: string;
}

export interface CoOccurringIssueApi {
  /** @minLength 1 */
  id: string;
  /** @minLength 1 */
  title: string;
  type: string;
  co_occurrence: number;
  count: number;
  /** @minLength 1 */
  severity: string;
}

export interface FeedSidebarApi {
  timeline: SidebarTimelineApi;
  ai_metadata: SidebarAIMetadataApi;
  evaluations: EvaluationResultApi[];
  co_occurring_issues: CoOccurringIssueApi[];
}

export interface FeedSidebarApiResponseApi {
  status?: boolean;
  result: FeedSidebarApi;
}

export interface TracesAggregatesApi {
  total_traces: number;
  failing_traces: number;
  passing_traces: number;
  avg_score: number;
  p50_latency: number;
  p95_latency: number;
  avg_turns: number;
}

export interface TracesListRowApi {
  /** @minLength 1 */
  id: string;
  /** @minLength 1 */
  input: string;
  timestamp: string;
  latency_ms: number;
  tokens: number;
  cost: number;
  score: number;
  turns: number;
}

export interface TracesTabResponseApi {
  aggregates: TracesAggregatesApi;
  traces: TracesListRowApi[];
  total: number;
}

export interface TracesTabApiResponseApi {
  status?: boolean;
  result: TracesTabResponseApi;
}

export interface TrendMetricApi {
  /** @minLength 1 */
  label: string;
  /** @minLength 1 */
  value: string;
  delta: number;
  unit: string;
}

export interface ScoreTrendApi {
  /** @minLength 1 */
  label: string;
  current: number;
  prev: number;
  sparkline: number[];
}

export interface HeatmapCellApi {
  day: number;
  hour: number;
  value: number;
}

export interface TrendsTabResponseApi {
  metrics: TrendMetricApi[];
  events_over_time: EventsOverTimePointApi[];
  score_trends: ScoreTrendApi[];
  activity_heatmap: HeatmapCellApi[][];
}

export interface TrendsTabApiResponseApi {
  status?: boolean;
  result: TrendsTabResponseApi;
}

export type AnnotationLabelResponseApiSettings = { [key: string]: unknown };

export interface AnnotationLabelResponseApi {
  id: string;
  /** @minLength 1 */
  name: string;
  /** @minLength 1 */
  type: string;
  description?: string;
  settings?: AnnotationLabelResponseApiSettings;
}

export interface GetAnnotationLabelsResponseApi {
  status?: boolean;
  result: AnnotationLabelResponseApi[];
}

export type ImagineAnalysisItemApiStatus = typeof ImagineAnalysisItemApiStatus[keyof typeof ImagineAnalysisItemApiStatus];


export const ImagineAnalysisItemApiStatus = {
  pending: 'pending',
  running: 'running',
  completed: 'completed',
  failed: 'failed',
} as const;

export interface ImagineAnalysisItemApi {
  id: string;
  /**
     * @minLength 1
     * @maxLength 100
     */
  widget_id: string;
  status: ImagineAnalysisItemApiStatus;
  content?: string;
  error?: string;
}

export interface ImagineAnalysisResultApi {
  analyses: ImagineAnalysisItemApi[];
}

export interface ImagineAnalysisResponseApi {
  status?: boolean;
  result: ImagineAnalysisResultApi;
}

export interface WidgetAnalysisApi {
  /**
     * @minLength 1
     * @maxLength 100
     */
  widget_id: string;
  /**
     * @minLength 1
     * @maxLength 8000
     */
  prompt: string;
}

export interface TriggerAnalysisApi {
  saved_view_id: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  trace_id: string;
  project_id: string;
  widgets: WidgetAnalysisApi[];
}

export type ObservabilityProviderApiProvider = typeof ObservabilityProviderApiProvider[keyof typeof ObservabilityProviderApiProvider];


export const ObservabilityProviderApiProvider = {
  vapi: 'vapi',
  eleven_labs: 'eleven_labs',
  retell: 'retell',
  livekit: 'livekit',
  others: 'others',
  bland: 'bland',
  twilio: 'twilio',
} as const;

export type ObservabilityProviderApiMetadata = { [key: string]: unknown };

export interface ObservabilityProviderApi {
  readonly id?: string;
  readonly project?: string;
  /**
     * Name of the project. If it doesn't exist, it will be created.
     * @minLength 1
     */
  project_name?: string;
  provider: ObservabilityProviderApiProvider;
  enabled?: boolean;
  readonly organization?: string;
  readonly workspace?: string;
  metadata?: ObservabilityProviderApiMetadata;
  readonly created_at?: string;
  readonly updated_at?: string;
}

export type VerifyApiKeyRequestApiProvider = typeof VerifyApiKeyRequestApiProvider[keyof typeof VerifyApiKeyRequestApiProvider];


export const VerifyApiKeyRequestApiProvider = {
  vapi: 'vapi',
  retell: 'retell',
} as const;

export interface VerifyApiKeyRequestApi {
  provider: VerifyApiKeyRequestApiProvider;
  api_key?: string;
  agent_id?: string;
}

export interface VerifyResponseApi {
  status?: boolean;
  /** @minLength 1 */
  result: string;
}

export type VerifyAssistantIdRequestApiProvider = typeof VerifyAssistantIdRequestApiProvider[keyof typeof VerifyAssistantIdRequestApiProvider];


export const VerifyAssistantIdRequestApiProvider = {
  vapi: 'vapi',
  retell: 'retell',
} as const;

export interface VerifyAssistantIdRequestApi {
  provider: VerifyAssistantIdRequestApiProvider;
  assistant_id?: string;
  api_key?: string;
  agent_id?: string;
}

export type ObservationSpanApiObservationType = typeof ObservationSpanApiObservationType[keyof typeof ObservationSpanApiObservationType];


export const ObservationSpanApiObservationType = {
  tool: 'tool',
  chain: 'chain',
  llm: 'llm',
  retriever: 'retriever',
  embedding: 'embedding',
  agent: 'agent',
  reranker: 'reranker',
  unknown: 'unknown',
  guardrail: 'guardrail',
  evaluator: 'evaluator',
  conversation: 'conversation',
} as const;

export type ObservationSpanApiInput = { [key: string]: unknown };

export type ObservationSpanApiOutput = { [key: string]: unknown };

export type ObservationSpanApiModelParameters = { [key: string]: unknown };

export type ObservationSpanApiStatus = typeof ObservationSpanApiStatus[keyof typeof ObservationSpanApiStatus];


export const ObservationSpanApiStatus = {
  UNSET: 'UNSET',
  OK: 'OK',
  ERROR: 'ERROR',
} as const;

export type ObservationSpanApiTags = { [key: string]: unknown };

export type ObservationSpanApiMetadata = { [key: string]: unknown };

export type ObservationSpanApiSpanEvents = { [key: string]: unknown };

export type ObservationSpanApiEvalStatus = typeof ObservationSpanApiEvalStatus[keyof typeof ObservationSpanApiEvalStatus];


export const ObservationSpanApiEvalStatus = {
  NotStarted: 'NotStarted',
  Queued: 'Queued',
  Running: 'Running',
  Completed: 'Completed',
  Editing: 'Editing',
  Inactive: 'Inactive',
  Failed: 'Failed',
  PartialRun: 'PartialRun',
  ExperimentEvaluation: 'ExperimentEvaluation',
  Uploading: 'Uploading',
  PartialExtracted: 'PartialExtracted',
  Processing: 'Processing',
  Deleting: 'Deleting',
  PartialCompleted: 'PartialCompleted',
  OptimizationEvaluation: 'OptimizationEvaluation',
  Error: 'Error',
  Cancelled: 'Cancelled',
} as const;

export interface ObservationSpanApi {
  /** @minLength 1 */
  readonly id?: string;
  project: string;
  project_version?: string;
  trace: string;
  /** @maxLength 255 */
  parent_span_id?: string;
  /**
     * @minLength 1
     * @maxLength 2000
     */
  name: string;
  observation_type: ObservationSpanApiObservationType;
  start_time?: string;
  end_time?: string;
  input?: ObservationSpanApiInput;
  output?: ObservationSpanApiOutput;
  /** @maxLength 255 */
  model?: string;
  model_parameters?: ObservationSpanApiModelParameters;
  /**
     * @minimum -2147483648
     * @maximum 2147483647
     */
  latency_ms?: number;
  org_id?: string;
  org_user_id?: string;
  /**
     * @minimum -2147483648
     * @maximum 2147483647
     */
  prompt_tokens?: number;
  /**
     * @minimum -2147483648
     * @maximum 2147483647
     */
  completion_tokens?: number;
  /**
     * @minimum -2147483648
     * @maximum 2147483647
     */
  total_tokens?: number;
  response_time?: number;
  /** @maxLength 255 */
  eval_id?: string;
  cost?: number;
  status?: ObservationSpanApiStatus;
  status_message?: string;
  tags?: ObservationSpanApiTags;
  metadata?: ObservationSpanApiMetadata;
  span_events?: ObservationSpanApiSpanEvents;
  /** @maxLength 255 */
  provider?: string;
  readonly provider_logo?: string;
  readonly span_attributes?: string;
  custom_eval_config?: string;
  eval_status?: ObservationSpanApiEvalStatus;
  prompt_version?: string;
}

export type AddObservationSpanAnnotationsApiAnnotationValues = {[key: string]: { [key: string]: unknown }};

export interface AddObservationSpanAnnotationsApi {
  observation_span_id?: string;
  trace_id?: string;
  annotation_values: AddObservationSpanAnnotationsApiAnnotationValues;
  notes?: string;
}

export interface ObservationAttributeListResponseApi {
  status?: boolean;
  result: string[];
}

export type ObserveGraphDataRequestApiFiltersItemFilterConfig = {
  /** Canonical field type, for example text, number, boolean, datetime, categorical, thumbs, annotator, or array. */
  filter_type: string;
  /** Canonical operator from api_contracts/filter_contract.json, for example equals, not_equals, in, not_in, between, not_between, is_null, or is_not_null. */
  filter_op: string;
  /** Scalar, list, range tuple, boolean, or null depending on filter_op and filter_type. */
  filter_value?: unknown;
  /** Column family such as SYSTEM_METRIC, SPAN_ATTRIBUTE, EVAL_METRIC, ANNOTATION, or NORMAL. */
  col_type?: string;
};

export type ObserveGraphDataRequestApiFiltersItem = {
  /** Column or attribute id to filter on. */
  column_id: string;
  /** Optional UI label for chips and saved views. */
  display_name?: string;
  /** Optional source surface for mixed-source filters, for example traces, datasets, or simulation. */
  source?: string;
  /** Optional metric output type metadata used by eval and annotation filters. */
  output_type?: string;
  filter_config: ObserveGraphDataRequestApiFiltersItemFilterConfig;
};

export type ObserveGraphDataRequestApiInterval = typeof ObserveGraphDataRequestApiInterval[keyof typeof ObserveGraphDataRequestApiInterval];


export const ObserveGraphDataRequestApiInterval = {
  hour: 'hour',
  day: 'day',
  week: 'week',
  month: 'month',
} as const;

export type ObserveGraphDataRequestApiReqDataConfigType = typeof ObserveGraphDataRequestApiReqDataConfigType[keyof typeof ObserveGraphDataRequestApiReqDataConfigType];


export const ObserveGraphDataRequestApiReqDataConfigType = {
  SYSTEM_METRIC: 'SYSTEM_METRIC',
  EVAL: 'EVAL',
  ANNOTATION: 'ANNOTATION',
} as const;

export type ObserveGraphDataRequestApiReqDataConfig = {
  id: string;
  type: ObserveGraphDataRequestApiReqDataConfigType;
  output_type?: string;
  eval_output_type?: string;
  choices?: string[];
  value?: unknown;
  filter_op?: string;
  filter_value?: unknown;
};

export interface ObserveGraphDataRequestApi {
  project_id: string;
  filters?: ObserveGraphDataRequestApiFiltersItem[];
  interval?: ObserveGraphDataRequestApiInterval;
  property?: string;
  req_data_config: ObserveGraphDataRequestApiReqDataConfig;
}

export interface ObserveGraphDataPointApi {
  /** @minLength 1 */
  timestamp: string;
  value: number;
  primary_traffic?: number;
}

export interface ObserveGraphDataResultApi {
  metric_name: string;
  data: ObserveGraphDataPointApi[];
}

export interface ObserveGraphDataResponseApi {
  status?: boolean;
  result: ObserveGraphDataResultApi;
}

export type RootSpansResponseApiResult = {[key: string]: string};

export interface RootSpansResponseApi {
  status?: boolean;
  result: RootSpansResponseApiResult;
}

export type ProjectVersionApiMetadata = { [key: string]: unknown };

export type ProjectVersionApiError = { [key: string]: unknown };

export type ProjectVersionApiEvalTags = { [key: string]: unknown };

export interface ProjectVersionApi {
  readonly id?: string;
  project: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  metadata?: ProjectVersionApiMetadata;
  start_time?: string;
  end_time?: string;
  error?: ProjectVersionApiError;
  eval_tags?: ProjectVersionApiEvalTags;
  avg_eval_score?: number;
  /** @minLength 1 */
  readonly version?: string;
  annotations?: string;
}

export type ProjectApiModelType = typeof ProjectApiModelType[keyof typeof ProjectApiModelType];


export const ProjectApiModelType = {
  Numeric: 'Numeric',
  ScoreCategorical: 'ScoreCategorical',
  Ranking: 'Ranking',
  BinaryClassification: 'BinaryClassification',
  Regression: 'Regression',
  ObjectDetection: 'ObjectDetection',
  Segmentation: 'Segmentation',
  GenerativeLLM: 'GenerativeLLM',
  GenerativeImage: 'GenerativeImage',
  GenerativeVideo: 'GenerativeVideo',
  TTS: 'TTS',
  STT: 'STT',
  MultiModal: 'MultiModal',
} as const;

export type ProjectApiTraceType = typeof ProjectApiTraceType[keyof typeof ProjectApiTraceType];


export const ProjectApiTraceType = {
  experiment: 'experiment',
  observe: 'observe',
} as const;

export type ProjectApiMetadata = { [key: string]: unknown };

/**
 * Any valid JSON value.
 */
export type ProjectApiConfig = { [key: string]: unknown };

export type ProjectApiSource = typeof ProjectApiSource[keyof typeof ProjectApiSource];


export const ProjectApiSource = {
  demo: 'demo',
  prototype: 'prototype',
  simulator: 'simulator',
} as const;

/**
 * Any valid JSON value.
 */
export type ProjectApiSessionConfig = { [key: string]: unknown };

/**
 * Any valid JSON value.
 */
export type ProjectApiTags = { [key: string]: unknown };

export interface ProjectApi {
  readonly id?: string;
  model_type: ProjectApiModelType;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  trace_type: ProjectApiTraceType;
  metadata?: ProjectApiMetadata;
  readonly organization?: string;
  readonly workspace?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
  /** Any valid JSON value. */
  config?: ProjectApiConfig;
  source?: ProjectApiSource;
  /** Any valid JSON value. */
  session_config?: ProjectApiSessionConfig;
  /** Any valid JSON value. */
  tags?: ProjectApiTags;
}

export type ProjectUserGraphDataRequestApiFiltersItemFilterConfig = {
  /** Canonical field type, for example text, number, boolean, datetime, categorical, thumbs, annotator, or array. */
  filter_type: string;
  /** Canonical operator from api_contracts/filter_contract.json, for example equals, not_equals, in, not_in, between, not_between, is_null, or is_not_null. */
  filter_op: string;
  /** Scalar, list, range tuple, boolean, or null depending on filter_op and filter_type. */
  filter_value?: unknown;
  /** Column family such as SYSTEM_METRIC, SPAN_ATTRIBUTE, EVAL_METRIC, ANNOTATION, or NORMAL. */
  col_type?: string;
};

export type ProjectUserGraphDataRequestApiFiltersItem = {
  /** Column or attribute id to filter on. */
  column_id: string;
  /** Optional UI label for chips and saved views. */
  display_name?: string;
  /** Optional source surface for mixed-source filters, for example traces, datasets, or simulation. */
  source?: string;
  /** Optional metric output type metadata used by eval and annotation filters. */
  output_type?: string;
  filter_config: ProjectUserGraphDataRequestApiFiltersItemFilterConfig;
};

export interface ProjectUserGraphDataRequestApi {
  /** @minLength 1 */
  interval?: string;
  filters?: ProjectUserGraphDataRequestApiFiltersItem[];
}

export type ProjectUserMetricsRequestApiFiltersItemFilterConfig = {
  /** Canonical field type, for example text, number, boolean, datetime, categorical, thumbs, annotator, or array. */
  filter_type: string;
  /** Canonical operator from api_contracts/filter_contract.json, for example equals, not_equals, in, not_in, between, not_between, is_null, or is_not_null. */
  filter_op: string;
  /** Scalar, list, range tuple, boolean, or null depending on filter_op and filter_type. */
  filter_value?: unknown;
  /** Column family such as SYSTEM_METRIC, SPAN_ATTRIBUTE, EVAL_METRIC, ANNOTATION, or NORMAL. */
  col_type?: string;
};

export type ProjectUserMetricsRequestApiFiltersItem = {
  /** Column or attribute id to filter on. */
  column_id: string;
  /** Optional UI label for chips and saved views. */
  display_name?: string;
  /** Optional source surface for mixed-source filters, for example traces, datasets, or simulation. */
  source?: string;
  /** Optional metric output type metadata used by eval and annotation filters. */
  output_type?: string;
  filter_config: ProjectUserMetricsRequestApiFiltersItemFilterConfig;
};

export interface ProjectUserMetricsRequestApi {
  end_user_id: string;
  project_id: string;
  /** @minLength 1 */
  interval?: string;
  filters?: ProjectUserMetricsRequestApiFiltersItem[];
}

export type ProjectUsersAggregateGraphDataRequestApiFiltersItemFilterConfig = {
  /** Canonical field type, for example text, number, boolean, datetime, categorical, thumbs, annotator, or array. */
  filter_type: string;
  /** Canonical operator from api_contracts/filter_contract.json, for example equals, not_equals, in, not_in, between, not_between, is_null, or is_not_null. */
  filter_op: string;
  /** Scalar, list, range tuple, boolean, or null depending on filter_op and filter_type. */
  filter_value?: unknown;
  /** Column family such as SYSTEM_METRIC, SPAN_ATTRIBUTE, EVAL_METRIC, ANNOTATION, or NORMAL. */
  col_type?: string;
};

export type ProjectUsersAggregateGraphDataRequestApiFiltersItem = {
  /** Column or attribute id to filter on. */
  column_id: string;
  /** Optional UI label for chips and saved views. */
  display_name?: string;
  /** Optional source surface for mixed-source filters, for example traces, datasets, or simulation. */
  source?: string;
  /** Optional metric output type metadata used by eval and annotation filters. */
  output_type?: string;
  filter_config: ProjectUsersAggregateGraphDataRequestApiFiltersItemFilterConfig;
};

export type ProjectUsersAggregateGraphDataRequestApiReqDataConfigType = typeof ProjectUsersAggregateGraphDataRequestApiReqDataConfigType[keyof typeof ProjectUsersAggregateGraphDataRequestApiReqDataConfigType];


export const ProjectUsersAggregateGraphDataRequestApiReqDataConfigType = {
  SYSTEM_METRIC: 'SYSTEM_METRIC',
  EVAL: 'EVAL',
  ANNOTATION: 'ANNOTATION',
} as const;

export type ProjectUsersAggregateGraphDataRequestApiReqDataConfig = {
  id: string;
  type: ProjectUsersAggregateGraphDataRequestApiReqDataConfigType;
  output_type?: string;
  eval_output_type?: string;
  choices?: string[];
  value?: unknown;
  filter_op?: string;
  filter_value?: unknown;
};

export interface ProjectUsersAggregateGraphDataRequestApi {
  project_id: string;
  /** @minLength 1 */
  interval?: string;
  filters?: ProjectUsersAggregateGraphDataRequestApiFiltersItem[];
  /** @minLength 1 */
  property?: string;
  req_data_config?: ProjectUsersAggregateGraphDataRequestApiReqDataConfig;
}

export interface ProjectIdListItemApi {
  id: string;
  /** @minLength 1 */
  name: string;
  /** @minLength 1 */
  trace_type: string;
}

export interface ProjectIdListResultApi {
  projects: ProjectIdListItemApi[];
}

export interface ProjectIdListResponseApi {
  status?: boolean;
  result: ProjectIdListResultApi;
}

export type ProjectDetailResultApiModelType = typeof ProjectDetailResultApiModelType[keyof typeof ProjectDetailResultApiModelType];


export const ProjectDetailResultApiModelType = {
  Numeric: 'Numeric',
  ScoreCategorical: 'ScoreCategorical',
  Ranking: 'Ranking',
  BinaryClassification: 'BinaryClassification',
  Regression: 'Regression',
  ObjectDetection: 'ObjectDetection',
  Segmentation: 'Segmentation',
  GenerativeLLM: 'GenerativeLLM',
  GenerativeImage: 'GenerativeImage',
  GenerativeVideo: 'GenerativeVideo',
  TTS: 'TTS',
  STT: 'STT',
  MultiModal: 'MultiModal',
} as const;

export type ProjectDetailResultApiTraceType = typeof ProjectDetailResultApiTraceType[keyof typeof ProjectDetailResultApiTraceType];


export const ProjectDetailResultApiTraceType = {
  experiment: 'experiment',
  observe: 'observe',
} as const;

export type ProjectDetailResultApiSource = typeof ProjectDetailResultApiSource[keyof typeof ProjectDetailResultApiSource];


export const ProjectDetailResultApiSource = {
  demo: 'demo',
  prototype: 'prototype',
  simulator: 'simulator',
} as const;

export type ProjectDetailResultApiMetadata = { [key: string]: unknown };

/**
 * Any valid JSON value.
 */
export type ProjectDetailResultApiConfig = { [key: string]: unknown };

/**
 * Any valid JSON value.
 */
export type ProjectDetailResultApiSessionConfig = { [key: string]: unknown };

/**
 * Any valid JSON value.
 */
export type ProjectDetailResultApiTags = { [key: string]: unknown };

export interface ProjectDetailResultApi {
  readonly id?: string;
  model_type: ProjectDetailResultApiModelType;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  trace_type: ProjectDetailResultApiTraceType;
  metadata?: ProjectDetailResultApiMetadata;
  readonly organization?: string;
  readonly workspace?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
  /** Any valid JSON value. */
  config?: ProjectDetailResultApiConfig;
  source?: ProjectDetailResultApiSource;
  /** Any valid JSON value. */
  session_config?: ProjectDetailResultApiSessionConfig;
  /** Any valid JSON value. */
  tags?: ProjectDetailResultApiTags;
  sampling_rate: number;
}

export interface ProjectDetailResponseApi {
  status?: boolean;
  result: ProjectDetailResultApi;
}

export type ReplaySessionListApiReplayType = typeof ReplaySessionListApiReplayType[keyof typeof ReplaySessionListApiReplayType];


export const ReplaySessionListApiReplayType = {
  session: 'session',
  trace: 'trace',
} as const;

export type ReplaySessionListApiCurrentStep = typeof ReplaySessionListApiCurrentStep[keyof typeof ReplaySessionListApiCurrentStep];


export const ReplaySessionListApiCurrentStep = {
  init: 'init',
  generating: 'generating',
  completed: 'completed',
} as const;

export interface ReplaySessionListApi {
  readonly id?: string;
  project: string;
  /** @minLength 1 */
  readonly project_name?: string;
  replay_type: ReplaySessionListApiReplayType;
  current_step?: ReplaySessionListApiCurrentStep;
  readonly created_at?: string;
}

export type CreateReplaySessionApiReplayType = typeof CreateReplaySessionApiReplayType[keyof typeof CreateReplaySessionApiReplayType];


export const CreateReplaySessionApiReplayType = {
  session: 'session',
  trace: 'trace',
} as const;

export interface CreateReplaySessionApi {
  project_id: string;
  replay_type?: CreateReplaySessionApiReplayType;
  ids?: string[];
  select_all?: boolean;
}

export type ReplaySessionApiReplayType = typeof ReplaySessionApiReplayType[keyof typeof ReplaySessionApiReplayType];


export const ReplaySessionApiReplayType = {
  session: 'session',
  trace: 'trace',
} as const;

export type ReplaySessionApiIds = { [key: string]: unknown };

export type ReplaySessionApiCurrentStep = typeof ReplaySessionApiCurrentStep[keyof typeof ReplaySessionApiCurrentStep];


export const ReplaySessionApiCurrentStep = {
  init: 'init',
  generating: 'generating',
  completed: 'completed',
} as const;

export type AgentDefinitionNestedApiAgentType = typeof AgentDefinitionNestedApiAgentType[keyof typeof AgentDefinitionNestedApiAgentType];


export const AgentDefinitionNestedApiAgentType = {
  voice: 'voice',
  text: 'text',
} as const;

export interface AgentDefinitionNestedApi {
  readonly id?: string;
  /**
     * Name of the AI agent
     * @minLength 1
     * @maxLength 255
     */
  agent_name: string;
  agent_type?: AgentDefinitionNestedApiAgentType;
  /**
     * Detailed description of the AI agent's purpose and capabilities
     * @minLength 1
     */
  description: string;
  readonly version_name?: string;
}

/**
 * Status of the scenario
 */
export type ScenarioNestedApiStatus = typeof ScenarioNestedApiStatus[keyof typeof ScenarioNestedApiStatus];


export const ScenarioNestedApiStatus = {
  NotStarted: 'NotStarted',
  Queued: 'Queued',
  Running: 'Running',
  Completed: 'Completed',
  Editing: 'Editing',
  Inactive: 'Inactive',
  Failed: 'Failed',
  PartialRun: 'PartialRun',
  ExperimentEvaluation: 'ExperimentEvaluation',
  Uploading: 'Uploading',
  PartialExtracted: 'PartialExtracted',
  Processing: 'Processing',
  Deleting: 'Deleting',
  PartialCompleted: 'PartialCompleted',
  OptimizationEvaluation: 'OptimizationEvaluation',
  Error: 'Error',
  Cancelled: 'Cancelled',
} as const;

export interface ScenarioNestedApi {
  readonly id?: string;
  /**
     * Name of the scenario
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  /** Status of the scenario */
  status?: ScenarioNestedApiStatus;
  /** Optional description of the scenario */
  description?: string;
}

export interface RunTestNestedApi {
  readonly id?: string;
  /**
     * Name of the test run
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  /** Description of the test run */
  description?: string;
}

export interface ReplaySessionApi {
  readonly id?: string;
  project: string;
  replay_type: ReplaySessionApiReplayType;
  ids?: ReplaySessionApiIds;
  select_all?: boolean;
  readonly current_step?: ReplaySessionApiCurrentStep;
  agent_definition?: AgentDefinitionNestedApi;
  scenario?: ScenarioNestedApi;
  run_test?: RunTestNestedApi;
}

export type GenerateScenarioApiAgentType = typeof GenerateScenarioApiAgentType[keyof typeof GenerateScenarioApiAgentType];


export const GenerateScenarioApiAgentType = {
  text: 'text',
  voice: 'voice',
} as const;

export type GenerateScenarioApiCustomColumnsItem = {[key: string]: string};

export type GenerateScenarioApiGraph = {[key: string]: string};

export interface GenerateScenarioApi {
  /**
     * @minLength 1
     * @maxLength 255
     */
  agent_name: string;
  agent_description?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  scenario_name: string;
  agent_type?: GenerateScenarioApiAgentType;
  /**
     * @minimum 1
     * @maximum 1000
     */
  no_of_rows?: number;
  personas?: string[];
  custom_columns?: GenerateScenarioApiCustomColumnsItem[];
  graph?: GenerateScenarioApiGraph;
  generate_graph?: boolean;
}

export interface SavedViewDefaultTabApi {
  /** @minLength 1 */
  key: string;
  /** @minLength 1 */
  label: string;
  /** @minLength 1 */
  tab_type: string;
}

export type SavedViewListApiTabType = typeof SavedViewListApiTabType[keyof typeof SavedViewListApiTabType];


export const SavedViewListApiTabType = {
  traces: 'traces',
  spans: 'spans',
  voice: 'voice',
  imagine: 'imagine',
  users: 'users',
  user_detail: 'user_detail',
  sessions: 'sessions',
} as const;

export type SavedViewListApiVisibility = typeof SavedViewListApiVisibility[keyof typeof SavedViewListApiVisibility];


export const SavedViewListApiVisibility = {
  personal: 'personal',
  project: 'project',
} as const;

export interface SavedViewCreatorApi {
  readonly id?: string;
  /** @minLength 1 */
  readonly name?: string;
  /** @minLength 1 */
  readonly email?: string;
}

export type SavedViewListApiConfig = { [key: string]: unknown };

export interface SavedViewListApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  tab_type: SavedViewListApiTabType;
  visibility?: SavedViewListApiVisibility;
  /**
     * @minimum -2147483648
     * @maximum 2147483647
     */
  position?: number;
  /** @maxLength 50 */
  icon?: string;
  config?: SavedViewListApiConfig;
  created_by?: SavedViewCreatorApi;
  readonly created_at?: string;
  readonly updated_at?: string;
}

export interface SavedViewListResultApi {
  default_tabs: SavedViewDefaultTabApi[];
  custom_views: SavedViewListApi[];
}

export interface SavedViewListResponseApi {
  status?: boolean;
  result: SavedViewListResultApi;
}

export type SavedViewDetailApiTabType = typeof SavedViewDetailApiTabType[keyof typeof SavedViewDetailApiTabType];


export const SavedViewDetailApiTabType = {
  traces: 'traces',
  spans: 'spans',
  voice: 'voice',
  imagine: 'imagine',
  users: 'users',
  user_detail: 'user_detail',
  sessions: 'sessions',
} as const;

export type SavedViewDetailApiVisibility = typeof SavedViewDetailApiVisibility[keyof typeof SavedViewDetailApiVisibility];


export const SavedViewDetailApiVisibility = {
  personal: 'personal',
  project: 'project',
} as const;

export type SavedViewDetailApiConfig = { [key: string]: unknown };

export interface SavedViewDetailApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
  tab_type: SavedViewDetailApiTabType;
  visibility?: SavedViewDetailApiVisibility;
  /**
     * @minimum -2147483648
     * @maximum 2147483647
     */
  position?: number;
  /** @maxLength 50 */
  icon?: string;
  config?: SavedViewDetailApiConfig;
  readonly project?: string;
  created_by?: SavedViewCreatorApi;
  updated_by?: SavedViewCreatorApi;
  readonly created_at?: string;
  readonly updated_at?: string;
}

export interface SavedViewDetailResponseApi {
  status?: boolean;
  result: SavedViewDetailApi;
}

export interface SavedViewMessageResultApi {
  /** @minLength 1 */
  message: string;
}

export interface SavedViewMessageResponseApi {
  status?: boolean;
  result: SavedViewMessageResultApi;
}

export type SharedLinkListApiResourceType = typeof SharedLinkListApiResourceType[keyof typeof SharedLinkListApiResourceType];


export const SharedLinkListApiResourceType = {
  trace: 'trace',
  dashboard: 'dashboard',
  eval_run: 'eval_run',
  dataset: 'dataset',
  project: 'project',
} as const;

export type SharedLinkListApiAccessType = typeof SharedLinkListApiAccessType[keyof typeof SharedLinkListApiAccessType];


export const SharedLinkListApiAccessType = {
  public: 'public',
  restricted: 'restricted',
} as const;

export interface SharedLinkListApi {
  readonly id?: string;
  readonly resource_type?: SharedLinkListApiResourceType;
  /** @minLength 1 */
  readonly resource_id?: string;
  /** @minLength 1 */
  readonly token?: string;
  readonly access_type?: SharedLinkListApiAccessType;
  readonly is_active?: boolean;
  readonly expires_at?: string;
  readonly created_by?: string;
  readonly created_at?: string;
  readonly access_count?: string;
  readonly share_url?: string;
}

export type SharedLinkCreateApiResourceType = typeof SharedLinkCreateApiResourceType[keyof typeof SharedLinkCreateApiResourceType];


export const SharedLinkCreateApiResourceType = {
  trace: 'trace',
  dashboard: 'dashboard',
  project: 'project',
} as const;

export type SharedLinkCreateApiAccessType = typeof SharedLinkCreateApiAccessType[keyof typeof SharedLinkCreateApiAccessType];


export const SharedLinkCreateApiAccessType = {
  public: 'public',
  restricted: 'restricted',
} as const;

export interface SharedLinkCreateApi {
  resource_type: SharedLinkCreateApiResourceType;
  /**
     * @minLength 1
     * @maxLength 255
     */
  resource_id: string;
  access_type?: SharedLinkCreateApiAccessType;
  expires_at?: string;
  /** Emails to grant access to (for restricted links). */
  emails?: string[];
}

export type SharedLinkDetailApiResourceType = typeof SharedLinkDetailApiResourceType[keyof typeof SharedLinkDetailApiResourceType];


export const SharedLinkDetailApiResourceType = {
  trace: 'trace',
  dashboard: 'dashboard',
  eval_run: 'eval_run',
  dataset: 'dataset',
  project: 'project',
} as const;

export type SharedLinkDetailApiAccessType = typeof SharedLinkDetailApiAccessType[keyof typeof SharedLinkDetailApiAccessType];


export const SharedLinkDetailApiAccessType = {
  public: 'public',
  restricted: 'restricted',
} as const;

export interface SharedLinkAccessApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 254
     */
  email: string;
  readonly user?: string;
  readonly granted_by?: string;
  readonly created_at?: string;
}

export interface SharedLinkDetailApi {
  readonly id?: string;
  readonly resource_type?: SharedLinkDetailApiResourceType;
  /** @minLength 1 */
  readonly resource_id?: string;
  /** @minLength 1 */
  readonly token?: string;
  readonly access_type?: SharedLinkDetailApiAccessType;
  readonly is_active?: boolean;
  readonly expires_at?: string;
  readonly created_by?: string;
  readonly created_at?: string;
  readonly access_list?: readonly SharedLinkAccessApi[];
  readonly share_url?: string;
}

export type SharedLinkUpdateApiAccessType = typeof SharedLinkUpdateApiAccessType[keyof typeof SharedLinkUpdateApiAccessType];


export const SharedLinkUpdateApiAccessType = {
  public: 'public',
  restricted: 'restricted',
} as const;

export interface SharedLinkUpdateApi {
  access_type?: SharedLinkUpdateApiAccessType;
  is_active?: boolean;
  expires_at?: string;
}

export interface AddAccessApi {
  /** @minItems 1 */
  emails: string[];
}

export type SharedLinkResolveResponseApiResourceType = typeof SharedLinkResolveResponseApiResourceType[keyof typeof SharedLinkResolveResponseApiResourceType];


export const SharedLinkResolveResponseApiResourceType = {
  trace: 'trace',
  dashboard: 'dashboard',
  eval_run: 'eval_run',
  dataset: 'dataset',
  project: 'project',
} as const;

export type SharedLinkResolveResponseApiAccessType = typeof SharedLinkResolveResponseApiAccessType[keyof typeof SharedLinkResolveResponseApiAccessType];


export const SharedLinkResolveResponseApiAccessType = {
  public: 'public',
  restricted: 'restricted',
} as const;

export type SharedLinkResolvedTraceApiInput = { [key: string]: unknown };

export type SharedLinkResolvedTraceApiOutput = { [key: string]: unknown };

export type SharedLinkResolvedTraceApiMetadata = { [key: string]: unknown };

export type SharedLinkResolvedTraceApiTags = { [key: string]: unknown };

export interface SharedLinkResolvedTraceApi {
  /** @minLength 1 */
  id: string;
  name?: string;
  /** @minLength 1 */
  project_id: string;
  input?: SharedLinkResolvedTraceApiInput;
  output?: SharedLinkResolvedTraceApiOutput;
  metadata?: SharedLinkResolvedTraceApiMetadata;
  tags?: SharedLinkResolvedTraceApiTags;
  /** @minLength 1 */
  created_at?: string;
}

export interface SharedLinkResolvedSummaryApi {
  total_spans?: number;
}

export type SharedLinkResolvedDataApiObservationSpansItem = { [key: string]: unknown };

export type SharedLinkResolvedDataApiMetadata = { [key: string]: unknown };

export type SharedLinkResolvedDataApiConfig = { [key: string]: unknown };

export type SharedLinkResolvedDataApiSessionConfig = { [key: string]: unknown };

export type SharedLinkResolvedDataApiTags = { [key: string]: unknown };

export type SharedLinkResolvedDataApiCreatedBy = { [key: string]: unknown };

export type SharedLinkResolvedDataApiUpdatedBy = { [key: string]: unknown };

export type SharedLinkResolvedDataApiWidgetsItem = { [key: string]: unknown };

export interface SharedLinkResolvedDataApi {
  trace?: SharedLinkResolvedTraceApi;
  observation_spans?: SharedLinkResolvedDataApiObservationSpansItem[];
  summary?: SharedLinkResolvedSummaryApi;
  /** @minLength 1 */
  id?: string;
  /** @minLength 1 */
  name?: string;
  /** @minLength 1 */
  trace_type?: string;
  /** @minLength 1 */
  model_type?: string;
  metadata?: SharedLinkResolvedDataApiMetadata;
  config?: SharedLinkResolvedDataApiConfig;
  session_config?: SharedLinkResolvedDataApiSessionConfig;
  tags?: SharedLinkResolvedDataApiTags;
  /** @minLength 1 */
  organization?: string;
  /** @minLength 1 */
  url_path?: string;
  description?: string;
  /** @minLength 1 */
  workspace?: string;
  created_by?: SharedLinkResolvedDataApiCreatedBy;
  updated_by?: SharedLinkResolvedDataApiUpdatedBy;
  /** @minLength 1 */
  created_at?: string;
  /** @minLength 1 */
  updated_at?: string;
  widgets?: SharedLinkResolvedDataApiWidgetsItem[];
  widget_count?: number;
}

export interface SharedLinkResolveResponseApi {
  resource_type: SharedLinkResolveResponseApiResourceType;
  /** @minLength 1 */
  resource_id: string;
  access_type: SharedLinkResolveResponseApiAccessType;
  data: SharedLinkResolvedDataApi;
}

export type SharedLinkResolveErrorApiType = typeof SharedLinkResolveErrorApiType[keyof typeof SharedLinkResolveErrorApiType];


export const SharedLinkResolveErrorApiType = {
  validation_error: 'validation_error',
  authentication_error: 'authentication_error',
  payment_required: 'payment_required',
  entitlement_error: 'entitlement_error',
  permission_error: 'permission_error',
  not_found: 'not_found',
  conflict: 'conflict',
  client_error: 'client_error',
  rate_limit: 'rate_limit',
  server_error: 'server_error',
  service_unavailable: 'service_unavailable',
  timeout: 'timeout',
  api_error: 'api_error',
} as const;

export type SharedLinkResolveErrorApiDetails = {[key: string]: string[]};

export interface SharedLinkResolveErrorApi {
  status?: boolean;
  type?: SharedLinkResolveErrorApiType;
  code?: string;
  detail?: string;
  /** @minLength 1 */
  result?: string;
  /** @minLength 1 */
  message?: string;
  error?: string;
  attr?: string;
  details?: SharedLinkResolveErrorApiDetails;
}

export type TraceAnnotationValueResponseApiAnnotationValue = { [key: string]: unknown };

export type TraceAnnotationValueResponseApiSettings = { [key: string]: unknown };

export interface TraceAnnotationValueResponseApi {
  id: string;
  /** @minLength 1 */
  annotation_label_name: string;
  annotation_value: TraceAnnotationValueResponseApiAnnotationValue;
  annotation_label_id: string;
  /** @minLength 1 */
  annotator?: string;
  annotator_id?: string;
  /** @minLength 1 */
  updated_by?: string;
  updated_at?: string;
  /** @minLength 1 */
  annotation_type: string;
  settings?: TraceAnnotationValueResponseApiSettings;
}

export interface TraceAnnotationNoteResponseApi {
  id: string;
  notes: string;
  /** @minLength 1 */
  created_by_annotator: string;
  /** @minLength 1 */
  created_by_user: string;
  created_by_user_id: string;
  updated_at: string;
}

export interface GetTraceAnnotationValuesResultApi {
  annotations: TraceAnnotationValueResponseApi[];
  notes: TraceAnnotationNoteResponseApi[];
}

export interface GetTraceAnnotationValuesResponseApi {
  status?: boolean;
  result: GetTraceAnnotationValuesResultApi;
}

export type TraceErrorAnalysisResultApiSummary = { [key: string]: unknown };

export type TraceErrorAnalysisResultApiErrorsItem = { [key: string]: unknown };

export type TraceErrorAnalysisResultApiGroupedErrorsItem = { [key: string]: unknown };

export type TraceErrorAnalysisResultApiScores = { [key: string]: unknown };

export type TraceErrorAnalysisResultApiMemoryContext = { [key: string]: unknown };

export interface TraceErrorAnalysisResultApi {
  analysis_exists: boolean;
  /** @minLength 1 */
  trace_id: string;
  /** @minLength 1 */
  message?: string;
  analysis_id?: string;
  analysis_date?: string;
  agent_version?: string;
  memory_enhanced?: boolean;
  summary?: TraceErrorAnalysisResultApiSummary;
  errors?: TraceErrorAnalysisResultApiErrorsItem[];
  grouped_errors?: TraceErrorAnalysisResultApiGroupedErrorsItem[];
  scores?: TraceErrorAnalysisResultApiScores;
  memory_context?: TraceErrorAnalysisResultApiMemoryContext;
}

export interface TraceErrorAnalysisResponseApi {
  status?: boolean;
  result: TraceErrorAnalysisResultApi;
}

export type TraceErrorTaskResponseResultApiStatus = typeof TraceErrorTaskResponseResultApiStatus[keyof typeof TraceErrorTaskResponseResultApiStatus];


export const TraceErrorTaskResponseResultApiStatus = {
  running: 'running',
  waiting: 'waiting',
  paused: 'paused',
} as const;

export interface TraceErrorTaskResponseResultApi {
  project_id: string;
  /** @minLength 1 */
  project_name: string;
  sampling_rate: number;
  status: TraceErrorTaskResponseResultApiStatus;
  is_active?: boolean;
  total_traces_analyzed?: number;
  total_errors_found?: number;
  failed_analyses?: number;
  last_run_at?: string;
  created?: boolean;
}

export interface TraceErrorTaskResponseApi {
  status?: boolean;
  result: TraceErrorTaskResponseResultApi;
}

export type TraceErrorTaskUpdateRequestApiStatus = typeof TraceErrorTaskUpdateRequestApiStatus[keyof typeof TraceErrorTaskUpdateRequestApiStatus];


export const TraceErrorTaskUpdateRequestApiStatus = {
  waiting: 'waiting',
  paused: 'paused',
} as const;

export interface TraceErrorTaskUpdateRequestApi {
  /**
     * @minimum 0
     * @maximum 1
     */
  sampling_rate: number;
  status?: TraceErrorTaskUpdateRequestApiStatus;
}

export type TraceErrorTaskUpdateResultApiStatus = typeof TraceErrorTaskUpdateResultApiStatus[keyof typeof TraceErrorTaskUpdateResultApiStatus];


export const TraceErrorTaskUpdateResultApiStatus = {
  running: 'running',
  waiting: 'waiting',
  paused: 'paused',
} as const;

export interface TraceErrorTaskUpdateResultApi {
  /** @minLength 1 */
  message: string;
  project_id: string;
  /** @minLength 1 */
  project_name: string;
  sampling_rate: number;
  status: TraceErrorTaskUpdateResultApiStatus;
  /** @minLength 1 */
  action: string;
  old_rate: number;
  new_rate: number;
}

export interface TraceErrorTaskUpdateResponseApi {
  status?: boolean;
  result: TraceErrorTaskUpdateResultApi;
}

export interface TraceSessionApi {
  readonly id?: string;
  project: string;
  bookmarked?: boolean;
  /** @maxLength 255 */
  name?: string;
  readonly created_at?: string;
}

export type TraceSessionGraphDataRequestApiFiltersItemFilterConfig = {
  /** Canonical field type, for example text, number, boolean, datetime, categorical, thumbs, annotator, or array. */
  filter_type: string;
  /** Canonical operator from api_contracts/filter_contract.json, for example equals, not_equals, in, not_in, between, not_between, is_null, or is_not_null. */
  filter_op: string;
  /** Scalar, list, range tuple, boolean, or null depending on filter_op and filter_type. */
  filter_value?: unknown;
  /** Column family such as SYSTEM_METRIC, SPAN_ATTRIBUTE, EVAL_METRIC, ANNOTATION, or NORMAL. */
  col_type?: string;
};

export type TraceSessionGraphDataRequestApiFiltersItem = {
  /** Column or attribute id to filter on. */
  column_id: string;
  /** Optional UI label for chips and saved views. */
  display_name?: string;
  /** Optional source surface for mixed-source filters, for example traces, datasets, or simulation. */
  source?: string;
  /** Optional metric output type metadata used by eval and annotation filters. */
  output_type?: string;
  filter_config: TraceSessionGraphDataRequestApiFiltersItemFilterConfig;
};

export type TraceSessionGraphDataRequestApiInterval = typeof TraceSessionGraphDataRequestApiInterval[keyof typeof TraceSessionGraphDataRequestApiInterval];


export const TraceSessionGraphDataRequestApiInterval = {
  hour: 'hour',
  day: 'day',
  week: 'week',
  month: 'month',
} as const;

export type TraceSessionGraphDataRequestApiReqDataConfigType = typeof TraceSessionGraphDataRequestApiReqDataConfigType[keyof typeof TraceSessionGraphDataRequestApiReqDataConfigType];


export const TraceSessionGraphDataRequestApiReqDataConfigType = {
  SYSTEM_METRIC: 'SYSTEM_METRIC',
  EVAL: 'EVAL',
  ANNOTATION: 'ANNOTATION',
} as const;

export type TraceSessionGraphDataRequestApiReqDataConfig = {
  id: string;
  type: TraceSessionGraphDataRequestApiReqDataConfigType;
  output_type?: string;
  eval_output_type?: string;
  choices?: string[];
  value?: unknown;
  filter_op?: string;
  filter_value?: unknown;
};

export interface TraceSessionGraphDataRequestApi {
  project_id: string;
  filters?: TraceSessionGraphDataRequestApiFiltersItem[];
  interval?: TraceSessionGraphDataRequestApiInterval;
  property?: string;
  req_data_config: TraceSessionGraphDataRequestApiReqDataConfig;
}

export type TraceApiMetadata = { [key: string]: unknown };

export type TraceApiInput = { [key: string]: unknown };

export type TraceApiOutput = { [key: string]: unknown };

export type TraceApiError = { [key: string]: unknown };

export type TraceApiTags = { [key: string]: unknown };

export interface TraceApi {
  readonly id?: string;
  project: string;
  project_version?: string;
  /** @maxLength 2000 */
  name?: string;
  metadata?: TraceApiMetadata;
  input?: TraceApiInput;
  output?: TraceApiOutput;
  error?: TraceApiError;
  session?: string;
  /** @maxLength 255 */
  external_id?: string;
  tags?: TraceApiTags;
}

export interface TraceObserveListMetadataApi {
  total_rows: number;
}

/**
 * Any valid JSON value.
 */
export type TraceObserveColumnConfigApiSettings = { [key: string]: unknown };

/**
 * Any valid JSON value.
 */
export type TraceObserveColumnConfigApiChoicesMap = { [key: string]: unknown };

/**
 * Any valid JSON value.
 */
export type TraceObserveColumnConfigApiAnnotators = { [key: string]: unknown };

export interface TraceObserveColumnConfigApi {
  /** @minLength 1 */
  id: string;
  /** @minLength 1 */
  name: string;
  is_visible: boolean;
  /** @minLength 1 */
  group_by?: string;
  /** @minLength 1 */
  output_type?: string;
  reverse_output?: boolean;
  /** @minLength 1 */
  annotation_label_type?: string;
  choices?: string[];
  /** Any valid JSON value. */
  settings?: TraceObserveColumnConfigApiSettings;
  /** Any valid JSON value. */
  choices_map?: TraceObserveColumnConfigApiChoicesMap;
  /** @minLength 1 */
  eval_template_id?: string;
  /** Any valid JSON value. */
  annotators?: TraceObserveColumnConfigApiAnnotators;
  /** @minLength 1 */
  source_field?: string;
  /** @minLength 1 */
  parent_eval_id?: string;
}

export type TraceObserveListResultApiTableItem = {[key: string]: { [key: string]: unknown }};

export interface TraceObserveListResultApi {
  metadata: TraceObserveListMetadataApi;
  table: TraceObserveListResultApiTableItem[];
  config: TraceObserveColumnConfigApi[];
}

export interface TraceObserveListResponseApi {
  status: boolean;
  result: TraceObserveListResultApi;
}

export type TraceDetailResultApiTrace = { [key: string]: unknown };

export type TraceDetailResultApiObservationSpansItem = { [key: string]: unknown };

export type TraceDetailResultApiSummary = { [key: string]: unknown };

export type TraceDetailResultApiGraph = { [key: string]: unknown };

export interface TraceDetailResultApi {
  trace: TraceDetailResultApiTrace;
  observation_spans: TraceDetailResultApiObservationSpansItem[];
  summary: TraceDetailResultApiSummary;
  graph: TraceDetailResultApiGraph;
}

export interface TraceDetailResponseApi {
  status?: boolean;
  result: TraceDetailResultApi;
}

export interface TraceTagsUpdateApi {
  tags: string[];
}

export type UserAlertMonitorLogApiType = typeof UserAlertMonitorLogApiType[keyof typeof UserAlertMonitorLogApiType];


export const UserAlertMonitorLogApiType = {
  critical: 'critical',
  warning: 'warning',
} as const;

export interface UserAlertMonitorLogApi {
  readonly id?: string;
  resolved_by?: UserApi;
  readonly created_at?: string;
  type: UserAlertMonitorLogApiType;
  /** @minLength 1 */
  message: string;
  resolved?: boolean;
  resolved_at?: string;
  /** @maxLength 200 */
  link?: string;
  time_window_start?: string;
  time_window_end?: string;
}

export type UserAlertMonitorLogWriteRequestApiType = typeof UserAlertMonitorLogWriteRequestApiType[keyof typeof UserAlertMonitorLogWriteRequestApiType];


export const UserAlertMonitorLogWriteRequestApiType = {
  critical: 'critical',
  warning: 'warning',
} as const;

export interface UserAlertMonitorLogWriteRequestApi {
  alert: string;
  type: UserAlertMonitorLogWriteRequestApiType;
  /** @minLength 1 */
  message: string;
  resolved?: boolean;
  resolved_at?: string;
  link?: string;
  time_window_start?: string;
  time_window_end?: string;
}

export type UserAlertMonitorLogWriteResponseApiType = typeof UserAlertMonitorLogWriteResponseApiType[keyof typeof UserAlertMonitorLogWriteResponseApiType];


export const UserAlertMonitorLogWriteResponseApiType = {
  critical: 'critical',
  warning: 'warning',
} as const;

export interface UserAlertMonitorLogWriteResponseApi {
  id: string;
  alert: string;
  type: UserAlertMonitorLogWriteResponseApiType;
  /** @minLength 1 */
  message: string;
  resolved: boolean;
  resolved_at?: string;
  resolved_by?: UserApi;
  link?: string;
  time_window_start?: string;
  time_window_end?: string;
  created_at: string;
}

export interface UserAlertMonitorLogResolveRequestApi {
  log_ids?: string[];
  select_all?: boolean;
  exclude_ids?: string[];
}

export interface UserAlertMonitorLogResolveResponseApi {
  status?: boolean;
  /** @minLength 1 */
  result: string;
}

export type UserAlertMonitorApiMetricType = typeof UserAlertMonitorApiMetricType[keyof typeof UserAlertMonitorApiMetricType];


export const UserAlertMonitorApiMetricType = {
  count_of_errors: 'count_of_errors',
  error_rates_for_function_calling: 'error_rates_for_function_calling',
  error_free_session_rates: 'error_free_session_rates',
  service_provider_error_rates: 'service_provider_error_rates',
  llm_api_failure_rates: 'llm_api_failure_rates',
  span_response_time: 'span_response_time',
  llm_response_time: 'llm_response_time',
  token_usage: 'token_usage',
  daily_tokens_spent: 'daily_tokens_spent',
  monthly_tokens_spent: 'monthly_tokens_spent',
  evaluation_metrics: 'evaluation_metrics',
} as const;

export type UserAlertMonitorApiThresholdOperator = typeof UserAlertMonitorApiThresholdOperator[keyof typeof UserAlertMonitorApiThresholdOperator];


export const UserAlertMonitorApiThresholdOperator = {
  greater_than: 'greater_than',
  less_than: 'less_than',
} as const;

/**
 * Method to set the threshold for the monitor (Static or Percentage change).
 */
export type UserAlertMonitorApiThresholdType = typeof UserAlertMonitorApiThresholdType[keyof typeof UserAlertMonitorApiThresholdType];


export const UserAlertMonitorApiThresholdType = {
  static: 'static',
  percentage_change: 'percentage_change',
} as const;

export type UserAlertMonitorApiFilters = { [key: string]: unknown };

export type UserAlertMonitorApiLogsItem = { [key: string]: unknown };

export interface UserAlertMonitorApi {
  readonly id?: string;
  project: string;
  /** @minLength 1 */
  name: string;
  readonly metric_name?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
  deleted?: boolean;
  deleted_at?: string;
  metric_type: UserAlertMonitorApiMetricType;
  /**
     * Id of the evaluation template.
     * @maxLength 2556
     */
  metric?: string;
  threshold_operator: UserAlertMonitorApiThresholdOperator;
  /** Method to set the threshold for the monitor (Static or Percentage change). */
  threshold_type?: UserAlertMonitorApiThresholdType;
  /**
     * For choice and pass/fail evals, the specific metric value to monitor.
     * @maxLength 255
     */
  threshold_metric_value?: string;
  /** @minimum 0 */
  critical_threshold_value?: number;
  /** @minimum 0 */
  warning_threshold_value?: number;
  /**
     * Frequency of alert checks in minutes.
     * @minimum 5
     * @maximum 2147483647
     */
  alert_frequency?: number;
  /**
     * For auto-thresholding. The time window in minutes to calculate the historical mean
     * @minimum 0
     * @maximum 2147483647
     */
  auto_threshold_time_window?: number;
  /** The last time the monitor was checked for alerts. */
  last_checked_at?: string;
  notification_emails?: string[];
  /** @maxLength 200 */
  slack_webhook_url?: string;
  slack_notes?: string;
  is_mute?: boolean;
  filters?: UserAlertMonitorApiFilters;
  logs?: UserAlertMonitorApiLogsItem[];
  organization: string;
  workspace?: string;
  created_by?: string;
}

export interface UserAlertMonitorBulkMuteRequestApi {
  ids?: string[];
  is_mute?: boolean;
  select_all?: boolean;
  exclude_ids?: string[];
}

export interface UserAlertMonitorDuplicateApi {
  id: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
}

export interface UserAlertMonitorDuplicateResultApi {
  id: string;
  /** @minLength 1 */
  message: string;
}

export interface UserAlertMonitorDuplicateResponseApi {
  status?: boolean;
  result: UserAlertMonitorDuplicateResultApi;
}

export interface UserAlertMonitorMetricOptionApi {
  /** @minLength 1 */
  readonly id?: string;
  /** @minLength 1 */
  readonly name?: string;
  /** @minLength 1 */
  readonly metric_type?: string;
  readonly output_type?: string;
}

export interface UserAlertMonitorMetricOptionsResponseApi {
  status?: boolean;
  readonly result?: readonly UserAlertMonitorMetricOptionApi[];
}

export type UserAlertMonitorPreviewGraphApiMetricType = typeof UserAlertMonitorPreviewGraphApiMetricType[keyof typeof UserAlertMonitorPreviewGraphApiMetricType];


export const UserAlertMonitorPreviewGraphApiMetricType = {
  count_of_errors: 'count_of_errors',
  error_rates_for_function_calling: 'error_rates_for_function_calling',
  error_free_session_rates: 'error_free_session_rates',
  service_provider_error_rates: 'service_provider_error_rates',
  llm_api_failure_rates: 'llm_api_failure_rates',
  span_response_time: 'span_response_time',
  llm_response_time: 'llm_response_time',
  token_usage: 'token_usage',
  daily_tokens_spent: 'daily_tokens_spent',
  monthly_tokens_spent: 'monthly_tokens_spent',
  evaluation_metrics: 'evaluation_metrics',
} as const;

export type UserAlertMonitorPreviewGraphApiThresholdOperator = typeof UserAlertMonitorPreviewGraphApiThresholdOperator[keyof typeof UserAlertMonitorPreviewGraphApiThresholdOperator];


export const UserAlertMonitorPreviewGraphApiThresholdOperator = {
  greater_than: 'greater_than',
  less_than: 'less_than',
} as const;

/**
 * Method to set the threshold for the monitor (Static or Percentage change).
 */
export type UserAlertMonitorPreviewGraphApiThresholdType = typeof UserAlertMonitorPreviewGraphApiThresholdType[keyof typeof UserAlertMonitorPreviewGraphApiThresholdType];


export const UserAlertMonitorPreviewGraphApiThresholdType = {
  static: 'static',
  percentage_change: 'percentage_change',
} as const;

export type UserAlertMonitorPreviewGraphApiFilters = { [key: string]: unknown };

export type UserAlertMonitorPreviewGraphApiLogsItem = { [key: string]: unknown };

export interface UserAlertMonitorPreviewGraphApi {
  readonly id?: string;
  project: string;
  name?: string;
  readonly metric_name?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
  deleted?: boolean;
  deleted_at?: string;
  metric_type: UserAlertMonitorPreviewGraphApiMetricType;
  /**
     * Id of the evaluation template.
     * @maxLength 2556
     */
  metric?: string;
  threshold_operator: UserAlertMonitorPreviewGraphApiThresholdOperator;
  /** Method to set the threshold for the monitor (Static or Percentage change). */
  threshold_type?: UserAlertMonitorPreviewGraphApiThresholdType;
  /**
     * For choice and pass/fail evals, the specific metric value to monitor.
     * @maxLength 255
     */
  threshold_metric_value?: string;
  /** @minimum 0 */
  critical_threshold_value?: number;
  /** @minimum 0 */
  warning_threshold_value?: number;
  /**
     * Frequency of alert checks in minutes.
     * @minimum 5
     * @maximum 2147483647
     */
  alert_frequency?: number;
  /**
     * For auto-thresholding. The time window in minutes to calculate the historical mean
     * @minimum 0
     * @maximum 2147483647
     */
  auto_threshold_time_window?: number;
  /** The last time the monitor was checked for alerts. */
  last_checked_at?: string;
  notification_emails?: string[];
  /** @maxLength 200 */
  slack_webhook_url?: string;
  slack_notes?: string;
  is_mute?: boolean;
  filters?: UserAlertMonitorPreviewGraphApiFilters;
  logs?: UserAlertMonitorPreviewGraphApiLogsItem[];
  organization: string;
  workspace?: string;
  created_by?: string;
}

export type UsersResultApiTableItem = { [key: string]: unknown };

export interface UsersResultApi {
  table: UsersResultApiTableItem[];
  total_count: number;
  total_pages: number;
}

export interface UsersResponseApi {
  status?: boolean;
  result: UsersResultApi;
}

export interface UserCodeExampleResponseApi {
  status?: boolean;
  /** @minLength 1 */
  result: string;
}

export type OTLPHealthResponseApiStatus = typeof OTLPHealthResponseApiStatus[keyof typeof OTLPHealthResponseApiStatus];


export const OTLPHealthResponseApiStatus = {
  healthy: 'healthy',
} as const;

export interface OTLPHealthResponseApi {
  status: OTLPHealthResponseApiStatus;
  /** @minLength 1 */
  service: string;
}

export type WebhookRequestApiCall = { [key: string]: unknown };

export interface WebhookRequestApi {
  call: WebhookRequestApiCall;
}

export interface WebhookResponseApi {
  status?: boolean;
  /** @minLength 1 */
  result: string;
}

export type AdminCustomPlanResponseApiResult = {[key: string]: string};

export interface AdminCustomPlanResponseApi {
  status: boolean;
  result: AdminCustomPlanResponseApiResult;
}

export type UsageErrorResponseApiDetails = {[key: string]: string[]};

export interface UsageErrorResponseApi {
  status: boolean;
  result?: string;
  message?: string;
  error?: string;
  detail?: string;
  details?: UsageErrorResponseApiDetails;
}

export type AdminCustomPlanRequestApiEntitlements = {[key: string]: { [key: string]: unknown }};

export interface AdminCustomPricingTierApi {
  tier_start: string;
  tier_end?: string;
  price_per_unit: string;
  display_unit?: string;
}

export type AdminCustomPlanRequestApiPricing = {[key: string]: AdminCustomPricingTierApi[]};

export interface AdminCustomPlanRequestApi {
  organization_id: string;
  platform_fee?: string;
  /** @minimum 1 */
  platform_fee_billing_cycle?: number;
  contract_end_date?: string;
  start_date?: string;
  entitlements?: AdminCustomPlanRequestApiEntitlements;
  pricing?: AdminCustomPlanRequestApiPricing;
  create_stripe_subscription?: boolean;
}

export type AdminEntitlementsResponseApiResult = {[key: string]: string};

export interface AdminEntitlementsResponseApi {
  status: boolean;
  result: AdminEntitlementsResponseApiResult;
}

export interface AdminEntitlementMutationRequestApi {
  organization_id: string;
  /** @minLength 1 */
  feature: string;
  value_int?: number;
  value_bool?: boolean;
}

export interface AdminEntitlementMutationResultApi {
  id: number;
  /** @minLength 1 */
  feature: string;
  created: boolean;
  value_int?: number;
  value_bool?: boolean;
}

export interface AdminEntitlementMutationResponseApi {
  status: boolean;
  result: AdminEntitlementMutationResultApi;
}

export interface AdminInvoiceRequestApi {
  organization_id: string;
  /**
     * @minLength 1
     * @pattern ^\d{4}-\d{2}$
     */
  period: string;
}

export interface AdminInvoiceGenerateResultApi {
  created: number;
  skipped: number;
  errors: number;
  invoice_id?: string;
  /** @minLength 1 */
  total?: string;
  /** @minLength 1 */
  status?: string;
  period_start?: string;
  period_end?: string;
  line_items_count?: number;
}

export interface AdminInvoiceGenerateResponseApi {
  status: boolean;
  result: AdminInvoiceGenerateResultApi;
}

export type AdminInvoiceLineItemApiTierBreakdown = { [key: string]: unknown };

export interface AdminInvoiceLineItemApi {
  /** @minLength 1 */
  line_type: string;
  dimension?: string;
  /** @minLength 1 */
  description: string;
  /** @minLength 1 */
  quantity: string;
  unit?: string;
  /** @minLength 1 */
  unit_price: string;
  /** @minLength 1 */
  amount: string;
  tier_breakdown?: AdminInvoiceLineItemApiTierBreakdown;
}

export interface AdminInvoicePreviewResultApi {
  org_id: string;
  /** @minLength 1 */
  period: string;
  /** @minLength 1 */
  plan: string;
  backfill_ran: boolean;
  usage_summary_count: number;
  invoice_exists: boolean;
  /** @minLength 1 */
  platform_fee: string;
  /** @minLength 1 */
  usage_total: string;
  /** @minLength 1 */
  credits_applied: string;
  /** @minLength 1 */
  subtotal: string;
  /** @minLength 1 */
  tax: string;
  /** @minLength 1 */
  total: string;
  line_items: AdminInvoiceLineItemApi[];
}

export interface AdminInvoicePreviewResponseApi {
  status: boolean;
  result: AdminInvoicePreviewResultApi;
}

export interface AdminPricingTierApi {
  id?: number;
  /** @minLength 1 */
  dimension: string;
  tier_start: string;
  tier_end?: string;
  price_per_unit: string;
  display_unit?: string;
}

export interface AdminPricingListResultApi {
  pricing: AdminPricingTierApi[];
}

export interface AdminPricingListResponseApi {
  status: boolean;
  result: AdminPricingListResultApi;
}

export interface AdminPricingMutationRequestApi {
  organization_id: string;
  /** @minLength 1 */
  dimension: string;
  tier_start: string;
  tier_end?: string;
  price_per_unit: string;
  display_unit?: string;
}

export interface AdminPricingMutationResultApi {
  id: number;
  /** @minLength 1 */
  dimension: string;
  created: boolean;
}

export interface AdminPricingMutationResponseApi {
  status: boolean;
  result: AdminPricingMutationResultApi;
}

export type APICallCountResultApiData = {[key: string]: number};

export interface APICallCountResultApi {
  data: APICallCountResultApiData;
}

export interface APICallCountResponseApi {
  status: boolean;
  result: APICallCountResultApi;
}

export type UsageAPICallTypeApiName = typeof UsageAPICallTypeApiName[keyof typeof UsageAPICallTypeApiName];


export const UsageAPICallTypeApiName = {
  prompt_bench: 'prompt_bench',
  dataset_protect: 'dataset_protect',
  dataset_protect_flash: 'dataset_protect_flash',
  turing_large_evaluator: 'turing_large_evaluator',
  turing_small_evaluator: 'turing_small_evaluator',
  turing_flash_evaluator: 'turing_flash_evaluator',
  protect_evaluator: 'protect_evaluator',
  protect_flash_evaluator: 'protect_flash_evaluator',
  code_evaluator: 'code_evaluator',
  user_add: 'user_add',
  observe_add: 'observe_add',
  prototype_add: 'prototype_add',
  dataset_add: 'dataset_add',
  row_add: 'row_add',
  knowledge_base: 'knowledge_base',
  synthetic_data_generation: 'synthetic_data_generation',
  error_localizer: 'error_localizer',
  auto_annotation: 'auto_annotation',
  dataset_evaluation: 'dataset_evaluation',
  experiment_evaluation: 'experiment_evaluation',
  optimisation_evaluation: 'optimisation_evaluation',
  eval_explanation: 'eval_explanation',
  dataset_run_prompt: 'dataset_run_prompt',
  dataset_optimization: 'dataset_optimization',
  dataset_experiment: 'dataset_experiment',
  voice_call: 'voice_call',
  text_call: 'text_call',
  wallet_refund: 'wallet_refund',
  wallet_refill: 'wallet_refill',
  wallet_auto_recharge: 'wallet_auto_recharge',
  wallet_add_funds: 'wallet_add_funds',
  trace_error_analysis: 'trace_error_analysis',
} as const;

export interface UsageAPICallTypeApi {
  readonly id?: number;
  name: UsageAPICallTypeApiName;
  description?: string;
}

export interface APICallTypeListResponseApi {
  status: boolean;
  result: UsageAPICallTypeApi[];
}

export interface UsageEmptyRequestApi { [key: string]: unknown }

export interface UsageMessageResultApi {
  /** @minLength 1 */
  message: string;
}

export interface UsageMessageResponseApi {
  status: boolean;
  result: UsageMessageResultApi;
}

export interface CheckoutSessionResultApi {
  /** @minLength 1 */
  session_id?: string;
  /** @minLength 1 */
  url?: string;
  /** @minLength 1 */
  status?: string;
  /** @minLength 1 */
  message?: string;
}

export interface CheckoutSessionResponseApi {
  status?: boolean;
  result?: CheckoutSessionResultApi;
  /** @minLength 1 */
  session_id?: string;
  /** @minLength 1 */
  url?: string;
}

export interface BillingPortalResponseApi {
  /** @minLength 1 */
  url: string;
}

export interface CheckoutSessionRequestApi {
  /** @minLength 1 */
  subscription_type?: string;
}

export interface CustomPaymentCheckoutRequestApi {
  amount: string;
}

export interface DownloadInvoiceRequestApi {
  /** @minLength 1 */
  invoice_id: string;
}

export interface DownloadInvoiceResultApi {
  /** @minLength 1 */
  invoice_pdf_url: string;
}

export interface DownloadInvoiceResponseApi {
  status: boolean;
  result: DownloadInvoiceResultApi;
}

export type EELicenseGrantApiBand = typeof EELicenseGrantApiBand[keyof typeof EELicenseGrantApiBand];


export const EELicenseGrantApiBand = {
  team: 'team',
  business: 'business',
  enterprise: 'enterprise',
  enterprise_plus: 'enterprise_plus',
} as const;

export interface EELicenseGrantApi {
  id: string;
  /** @minLength 1 */
  customer_name: string;
  band: EELicenseGrantApiBand;
  /** @minLength 1 */
  billing_interval: string;
  features: string[];
  max_traces_monthly?: number;
  max_gateway_monthly?: number;
  issued_at: string;
  expires_at: string;
  /** @minLength 1 */
  status: string;
}

export interface EELicenseListResultApi {
  licenses: EELicenseGrantApi[];
}

export interface EELicenseListResponseApi {
  status: boolean;
  result: EELicenseListResultApi;
}

export type EELicenseCreateRequestApiBand = typeof EELicenseCreateRequestApiBand[keyof typeof EELicenseCreateRequestApiBand];


export const EELicenseCreateRequestApiBand = {
  team: 'team',
  business: 'business',
  enterprise: 'enterprise',
  enterprise_plus: 'enterprise_plus',
} as const;

export type EELicenseCreateRequestApiBillingInterval = typeof EELicenseCreateRequestApiBillingInterval[keyof typeof EELicenseCreateRequestApiBillingInterval];


export const EELicenseCreateRequestApiBillingInterval = {
  monthly: 'monthly',
  yearly: 'yearly',
} as const;

export interface EELicenseCreateRequestApi {
  band: EELicenseCreateRequestApiBand;
  customer_name?: string;
  billing_interval?: EELicenseCreateRequestApiBillingInterval;
}

export interface EELicenseCreateResultApi {
  grant_id: string;
  /** @minLength 1 */
  jwt_key: string;
  /** @minLength 1 */
  key_hash: string;
  /** @minLength 1 */
  band: string;
  expires_at: string;
  features: string[];
}

export interface EELicenseCreateResponseApi {
  status: boolean;
  result: EELicenseCreateResultApi;
}

export interface EELicenseRevokeRequestApi {
  reason?: string;
}

export interface EELicenseRevokeResultApi {
  revoked: boolean;
  grant_id: string;
}

export interface EELicenseRevokeResponseApi {
  status: boolean;
  result: EELicenseRevokeResultApi;
}

export interface AutoReloadSettingsDataApi {
  autoreload_enabled: boolean;
  autoreload_wallet_amount: string;
  autoreload_wallet_threshold: string;
}

export interface AutoReloadSettingsResponseApi {
  /** @minLength 1 */
  status: string;
  data: AutoReloadSettingsDataApi;
}

export interface BillingInfoApi {
  name?: string;
  email?: string;
  company?: string;
  billing_address1?: string;
  billing_address2?: string;
  city?: string;
  state?: string;
  country?: string;
  postal_code?: string;
  tax_id?: string;
}

export interface OrganizationBillingLegacyResponseApi {
  /** @minLength 1 */
  status: string;
  billing_info: BillingInfoApi;
}

export interface CustomerInvoiceApi {
  /** @minLength 1 */
  date: string;
  /** @minLength 1 */
  id: string;
  is_invoice_available: boolean;
  /** @minLength 1 */
  amount: string;
  receipt_url?: string;
  /** @minLength 1 */
  payment_type: string;
}

export interface CustomerInvoicesResultApi {
  invoices: CustomerInvoiceApi[];
  total: number;
}

export interface CustomerInvoicesResponseApi {
  status: boolean;
  result: CustomerInvoicesResultApi;
}

export interface LastFourDigitsResultApi {
  last4: string;
}

export interface LastFourDigitsResponseApi {
  status: boolean;
  result: LastFourDigitsResultApi;
}

export interface WalletBalanceResponseApi {
  wallet_balance: string;
}

export type PricingCalculationResponseApiResult = {[key: string]: number};

export interface PricingCalculationResponseApi {
  status: boolean;
  result: PricingCalculationResponseApiResult;
}

export interface UsageOrganizationBillingApi {
  readonly id?: number;
  readonly organization?: string;
  /** @maxLength 100 */
  billing_contact_name?: string;
  /** @maxLength 254 */
  billing_contact_email?: string;
  /** @maxLength 100 */
  company?: string;
  /** @maxLength 255 */
  billing_address1?: string;
  /** @maxLength 255 */
  billing_address2?: string;
  /** @maxLength 100 */
  city?: string;
  /** @maxLength 100 */
  state?: string;
  /** @maxLength 100 */
  country?: string;
  /** @maxLength 20 */
  postal_code?: string;
  /** @maxLength 50 */
  tax_id?: string;
}

export interface OrganizationBillingListResponseApi {
  status: boolean;
  result: UsageOrganizationBillingApi[];
}

export interface OrganizationBillingDetailResponseApi {
  status: boolean;
  result: UsageOrganizationBillingApi;
}

export interface UsageOrganizationApi {
  readonly id?: string;
  /**
     * @minLength 1
     * @maxLength 255
     */
  name: string;
}

export interface OrganizationListResponseApi {
  status: boolean;
  result: UsageOrganizationApi[];
}

export type UsageOrganizationSubscriptionApiStatus = typeof UsageOrganizationSubscriptionApiStatus[keyof typeof UsageOrganizationSubscriptionApiStatus];


export const UsageOrganizationSubscriptionApiStatus = {
  active: 'active',
  past_due: 'past_due',
  canceled: 'canceled',
  inactive: 'inactive',
} as const;

export type UsageOrganizationSubscriptionApiSubscriptionFutureTier = typeof UsageOrganizationSubscriptionApiSubscriptionFutureTier[keyof typeof UsageOrganizationSubscriptionApiSubscriptionFutureTier];


export const UsageOrganizationSubscriptionApiSubscriptionFutureTier = {
  free: 'free',
  basic: 'basic',
  basic_yearly: 'basic_yearly',
  custom: 'custom',
} as const;

export interface UsageOrganizationSubscriptionApi {
  readonly id?: number;
  organization: string;
  readonly subscription_tier?: string;
  /** @maxLength 100 */
  custom_subscription_id?: string;
  status?: UsageOrganizationSubscriptionApiStatus;
  /** Price of the subscription. */
  subscription_price?: string;
  wallet_balance?: string;
  /** Amount to refill the wallet every month. */
  wallet_refill_amount?: string;
  /** Next due date for renewal. */
  next_renewal_date?: string;
  subscription_future_tier?: UsageOrganizationSubscriptionApiSubscriptionFutureTier;
  /** Next due date for renewal. */
  subscription_future_start_date?: string;
  /** Price of the future subscription. */
  subscription_future_price?: string;
  /**
     * Stripe customer ID for test mode. NULL values are allowed.
     * @maxLength 100
     */
  stripe_customer_id_test?: string;
  /**
     * Stripe customer ID for live mode. NULL values are allowed.
     * @maxLength 100
     */
  stripe_customer_id_live?: string;
  auto_recharge_enabled?: boolean;
  /** Amount to refill the wallet every month. */
  auto_recharge_amount?: string;
  /** Threshold to trigger auto recharge. */
  auto_recharge_threshold?: string;
  /** @maxLength 100 */
  payment_method_id?: string;
  last_refill_date?: string;
  /** Amount of the last refill. */
  last_refill_amount?: string;
}

export interface OrganizationSubscriptionListResponseApi {
  status: boolean;
  result: UsageOrganizationSubscriptionApi[];
}

export type UsageOrganizationSubscriptionCreateApiSubscriptionFutureTier = typeof UsageOrganizationSubscriptionCreateApiSubscriptionFutureTier[keyof typeof UsageOrganizationSubscriptionCreateApiSubscriptionFutureTier];


export const UsageOrganizationSubscriptionCreateApiSubscriptionFutureTier = {
  free: 'free',
  basic: 'basic',
  basic_yearly: 'basic_yearly',
  custom: 'custom',
} as const;

export type UsageOrganizationSubscriptionCreateApiStatus = typeof UsageOrganizationSubscriptionCreateApiStatus[keyof typeof UsageOrganizationSubscriptionCreateApiStatus];


export const UsageOrganizationSubscriptionCreateApiStatus = {
  active: 'active',
  past_due: 'past_due',
  canceled: 'canceled',
  inactive: 'inactive',
} as const;

export interface UsageOrganizationSubscriptionCreateApi {
  /** Next due date for renewal. */
  next_renewal_date?: string;
  /** Price of the subscription. */
  subscription_price?: string;
  subscription_future_tier?: UsageOrganizationSubscriptionCreateApiSubscriptionFutureTier;
  /** Next due date for renewal. */
  subscription_future_start_date?: string;
  /** Price of the future subscription. */
  subscription_future_price?: string;
  status?: UsageOrganizationSubscriptionCreateApiStatus;
  /** Amount to refill the wallet every month. */
  wallet_refill_amount?: string;
  wallet_balance?: string;
  /**
     * Stripe customer ID for test mode. NULL values are allowed.
     * @maxLength 100
     */
  stripe_customer_id_test?: string;
  /**
     * Stripe customer ID for live mode. NULL values are allowed.
     * @maxLength 100
     */
  stripe_customer_id_live?: string;
  auto_recharge_enabled?: boolean;
  /** Amount to refill the wallet every month. */
  auto_recharge_amount?: string;
  /** Threshold to trigger auto recharge. */
  auto_recharge_threshold?: string;
  /** @maxLength 100 */
  payment_method_id?: string;
  /** @maxLength 100 */
  custom_subscription_id?: string;
  organization: string;
  subscription_tier: number;
}

export interface OrganizationSubscriptionMutationResponseApi {
  status: boolean;
  result: UsageOrganizationSubscriptionCreateApi;
}

export interface UsageStringResponseApi {
  status: boolean;
  /** @minLength 1 */
  result: string;
}

export interface PricingCardDetailsResultApi {
  business_monthly_price: number;
  business_yearly_price: number;
  discount_percentage: number;
  custom_price?: number;
}

export interface PricingCardDetailsResponseApi {
  status: boolean;
  result: PricingCardDetailsResultApi;
}

export interface UsagePricingCreateApi {
  readonly id?: number;
  api_call_type: number;
  price_per_call: string;
  organization?: string;
}

export interface PricingListResponseApi {
  status: boolean;
  result: UsagePricingCreateApi[];
}

export interface PricingDetailResponseApi {
  status: boolean;
  result: UsagePricingCreateApi;
}

export interface UsagePricingApi {
  readonly id?: number;
  readonly api_call_type?: string;
  price_per_call: string;
  organization?: string;
}

export interface PricingReadResponseApi {
  status: boolean;
  result: UsagePricingApi;
}

export interface UsageRateLimitApi {
  readonly id?: number;
  readonly api_call_type?: string;
  organization?: string;
  /**
     * Max calls per minute
     * @minimum 0
     * @maximum 2147483647
     */
  minute_limit?: number;
  /**
     * Max calls per hour
     * @minimum 0
     * @maximum 2147483647
     */
  hour_limit?: number;
  /**
     * Max calls per day
     * @minimum 0
     * @maximum 2147483647
     */
  day_limit?: number;
  /**
     * Max calls per month
     * @minimum 0
     * @maximum 2147483647
     */
  month_limit?: number;
  readonly subscription_tier?: string;
}

export interface RateLimitListResponseApi {
  status: boolean;
  result: UsageRateLimitApi[];
}

export interface UsageRateLimitCreateApi {
  readonly id?: number;
  api_call_type: number;
  organization?: string;
  /**
     * Max calls per minute
     * @minimum 0
     * @maximum 2147483647
     */
  minute_limit?: number;
  /**
     * Max calls per hour
     * @minimum 0
     * @maximum 2147483647
     */
  hour_limit?: number;
  /**
     * Max calls per day
     * @minimum 0
     * @maximum 2147483647
     */
  day_limit?: number;
  /**
     * Max calls per month
     * @minimum 0
     * @maximum 2147483647
     */
  month_limit?: number;
  subscription_tier: number;
}

export interface RateLimitMutationResponseApi {
  status: boolean;
  result: UsageRateLimitCreateApi;
}

export interface RateLimitDetailResponseApi {
  status: boolean;
  result: UsageRateLimitApi;
}

export interface UsageResourceLimitApi {
  readonly id?: number;
  readonly resource_type?: string;
  readonly subscription_tier?: string;
  /**
     * Limit for the resource
     * @minimum 0
     * @maximum 2147483647
     */
  limit: number;
  organization?: string;
}

export interface ResourceLimitListResponseApi {
  status: boolean;
  result: UsageResourceLimitApi[];
}

export interface UsageResourceLimitCreateApi {
  readonly id?: number;
  resource_type: number;
  subscription_tier: number;
  /**
     * Limit for the resource
     * @minimum 0
     * @maximum 2147483647
     */
  limit: number;
  organization?: string;
}

export interface ResourceLimitMutationResponseApi {
  status: boolean;
  result: UsageResourceLimitCreateApi;
}

export interface ResourceLimitDetailResponseApi {
  status: boolean;
  result: UsageResourceLimitApi;
}

export type UsageResourceTypeApiName = typeof UsageResourceTypeApiName[keyof typeof UsageResourceTypeApiName];


export const UsageResourceTypeApiName = {
  project: 'project',
  dataset: 'dataset',
  logs: 'logs',
  rows: 'rows',
  columns: 'columns',
  users: 'users',
  traces: 'traces',
  observe: 'observe',
  prototypes: 'prototypes',
  knowledge_base: 'knowledge_base',
} as const;

export interface UsageResourceTypeApi {
  readonly id?: number;
  name: UsageResourceTypeApiName;
  description?: string;
}

export interface ResourceTypeListResponseApi {
  status: boolean;
  result: UsageResourceTypeApi[];
}

export type SubscriptionPlansResultApiStatus = typeof SubscriptionPlansResultApiStatus[keyof typeof SubscriptionPlansResultApiStatus];


export const SubscriptionPlansResultApiStatus = {
  success: 'success',
  error: 'error',
} as const;

export type SubscriptionPlansResultApiData = { [key: string]: unknown };

export interface SubscriptionPlansResultApi {
  status: SubscriptionPlansResultApiStatus;
  data?: SubscriptionPlansResultApiData;
  /** @minLength 1 */
  current_subscription?: string;
  message?: string;
}

export interface SubscriptionPlansResponseApi {
  status: boolean;
  result: SubscriptionPlansResultApi;
}

export interface SubscriptionStatusResultApi {
  next_renewal_date?: string;
  /** @minLength 1 */
  subscription_status?: string;
  /** @minLength 1 */
  tier?: string;
  subscription_price?: string;
  /** @minLength 1 */
  subscription_future_tier?: string;
  subscription_future_start_date?: string;
  subscription_future_price?: string;
}

export interface SubscriptionStatusResponseApi {
  status: boolean;
  result: SubscriptionStatusResultApi;
}

export type UsageSubscriptionTierApiName = typeof UsageSubscriptionTierApiName[keyof typeof UsageSubscriptionTierApiName];


export const UsageSubscriptionTierApiName = {
  free: 'free',
  basic: 'basic',
  basic_yearly: 'basic_yearly',
  custom: 'custom',
} as const;

export interface UsageSubscriptionTierApi {
  readonly id?: number;
  readonly name?: UsageSubscriptionTierApiName;
  description: string;
  /** @maxLength 100 */
  stripe_price_id?: string;
  /** Amount to refill the wallet every month. */
  wallet_refill_amount?: string;
}

export interface SubscriptionTierListResponseApi {
  status: boolean;
  result: UsageSubscriptionTierApi[];
}

export interface SubscriptionTierDetailResponseApi {
  status: boolean;
  result: UsageSubscriptionTierApi;
}

export interface AutoReloadSettingsRequestApi {
  autoreload_enabled: boolean;
  autoreload_walletamount: string;
  autoreload_walletthreshold: string;
}

export type AutoReloadUpdateResponseApiStatus = typeof AutoReloadUpdateResponseApiStatus[keyof typeof AutoReloadUpdateResponseApiStatus];


export const AutoReloadUpdateResponseApiStatus = {
  success: 'success',
} as const;

export interface AutoReloadUpdateResponseApi {
  status: AutoReloadUpdateResponseApiStatus;
  /** @minLength 1 */
  message: string;
}

export interface UpdateOrganizationBillingRequestApi {
  name?: string;
  email?: string;
  company?: string;
  billing_address1?: string;
  billing_address2?: string;
  city?: string;
  state?: string;
  country?: string;
  postal_code?: string;
  tax_id?: string;
}

export interface UpdateBillingDetailsResultApi {
  /** @minLength 1 */
  message: string;
}

export interface UpdateBillingDetailsResponseApi {
  status: boolean;
  result: UpdateBillingDetailsResultApi;
}

export type UsageSummaryResponseApiResult = {[key: string]: string};

export interface UsageSummaryResponseApi {
  status: boolean;
  result: UsageSummaryResponseApiResult;
}

export type AddonRequestApiPlan = typeof AddonRequestApiPlan[keyof typeof AddonRequestApiPlan];


export const AddonRequestApiPlan = {
  boost: 'boost',
  scale: 'scale',
  enterprise: 'enterprise',
} as const;

export interface AddonRequestApi {
  plan?: AddonRequestApiPlan;
}

export interface AddonPostResultApi {
  /** @minLength 1 */
  subscription_id: string;
  /** @minLength 1 */
  plan: string;
}

export interface AddonPostResponseApi {
  status: boolean;
  result: AddonPostResultApi;
}

export type UsageInvoiceLineItemApiTierBreakdown = { [key: string]: unknown };

export interface UsageInvoiceLineItemApi {
  /** @minLength 1 */
  line_type: string;
  /** @minLength 1 */
  dimension?: string;
  /** @minLength 1 */
  description: string;
  quantity: string;
  unit?: string;
  unit_price: string;
  amount: string;
  tier_breakdown?: UsageInvoiceLineItemApiTierBreakdown;
  credit_id?: number;
}

export interface UsageBillingOverviewResultApi {
  org_id?: string;
  /** @minLength 1 */
  period?: string;
  /** @minLength 1 */
  plan?: string;
  platform_fee?: string;
  usage_total?: string;
  credits_applied?: string;
  subtotal?: string;
  tax?: string;
  total?: string;
  line_items?: UsageInvoiceLineItemApi[];
  /** @minLength 1 */
  error?: string;
  pending_cancel?: boolean;
  /** @minLength 1 */
  cancel_at?: string;
}

export interface UsageBillingOverviewResponseApi {
  status: boolean;
  result: UsageBillingOverviewResultApi;
}

export interface UsageBudgetApi {
  id: number;
  /** @minLength 1 */
  name: string;
  /** @minLength 1 */
  scope?: string;
  threshold_value: string;
  /** @minLength 1 */
  action: string;
  notify_emails?: string[];
  is_active?: boolean;
  /** @minLength 1 */
  last_triggered_period?: string;
  last_triggered_at?: string;
  created_at?: string;
}

export interface UsageBudgetListResultApi {
  budgets: UsageBudgetApi[];
}

export interface UsageBudgetListResponseApi {
  status: boolean;
  result: UsageBudgetListResultApi;
}

export type UsageBudgetMutationRequestApiAction = typeof UsageBudgetMutationRequestApiAction[keyof typeof UsageBudgetMutationRequestApiAction];


export const UsageBudgetMutationRequestApiAction = {
  notify: 'notify',
  warn: 'warn',
  pause: 'pause',
} as const;

export interface UsageBudgetMutationRequestApi {
  /** @minLength 1 */
  name?: string;
  /** @minLength 1 */
  scope?: string;
  threshold_value?: string;
  action?: UsageBudgetMutationRequestApiAction;
  notify_emails?: string[];
  notify_slack_webhook?: string;
  is_active?: boolean;
}

export interface UsageBudgetMutationResultApi {
  id: number;
  /** @minLength 1 */
  name: string;
  /** @minLength 1 */
  scope?: string;
  /** @minLength 1 */
  threshold_value: string;
  /** @minLength 1 */
  action: string;
  is_active?: boolean;
}

export interface UsageBudgetMutationResponseApi {
  status: boolean;
  result: UsageBudgetMutationResultApi;
}

export interface UsageBudgetDeleteResultApi {
  deleted: boolean;
}

export interface UsageBudgetDeleteResponseApi {
  status: boolean;
  result: UsageBudgetDeleteResultApi;
}

export interface PlanResultApi {
  /** @minLength 1 */
  plan: string;
}

export interface PlanResponseApi {
  status: boolean;
  result: PlanResultApi;
}

export interface UsageInvoiceSummaryApi {
  id: string;
  period_start: string;
  period_end: string;
  /** @minLength 1 */
  plan: string;
  platform_fee: string;
  usage_total: string;
  credits_applied: string;
  subtotal: string;
  tax: string;
  total: string;
  /** @minLength 1 */
  status: string;
  stripe_invoice_url?: string;
  stripe_pdf_url?: string;
  created_at: string;
}

export interface UsageInvoiceListResultApi {
  invoices: UsageInvoiceSummaryApi[];
}

export interface UsageInvoiceListResponseApi {
  status: boolean;
  result: UsageInvoiceListResultApi;
}

export interface UsageInvoiceDetailApi {
  id: string;
  period_start: string;
  period_end: string;
  /** @minLength 1 */
  plan: string;
  platform_fee: number;
  usage_total: number;
  credits_applied: number;
  subtotal: number;
  tax: number;
  total: number;
  /** @minLength 1 */
  status: string;
  stripe_pdf_url?: string;
}

export interface UsageInvoiceDetailResultApi {
  invoice: UsageInvoiceDetailApi;
  line_items: UsageInvoiceLineItemApi[];
}

export interface UsageInvoiceDetailResponseApi {
  status: boolean;
  result: UsageInvoiceDetailResultApi;
}

export interface UsageNotificationActionApi {
  /** @minLength 1 */
  label: string;
  /** @minLength 1 */
  url: string;
}

export interface UsageNotificationBannerApi {
  /** @minLength 1 */
  id: string;
  /** @minLength 1 */
  type: string;
  /** @minLength 1 */
  message: string;
  action?: UsageNotificationActionApi;
  dismissible?: boolean;
}

export interface UsageNotificationsResultApi {
  banners: UsageNotificationBannerApi[];
}

export interface UsageNotificationsResponseApi {
  status: boolean;
  result: UsageNotificationsResultApi;
}

export interface PaymentMethodApi {
  /** @minLength 1 */
  id: string;
  brand?: string;
  last4?: string;
  exp_month?: number;
  exp_year?: number;
  is_default?: boolean;
}

export interface PaymentMethodsResponseApi {
  status: boolean;
  result: PaymentMethodApi[];
}

export interface UpgradeToPaygPostResultApi {
  /** @minLength 1 */
  checkout_url: string;
}

export interface PaymentMethodCheckoutResponseApi {
  status: boolean;
  result: UpgradeToPaygPostResultApi;
}

export interface SetupIntentConfirmRequestApi {
  /** @minLength 1 */
  session_id: string;
}

export interface PaymentMethodConfirmResultApi {
  /** @minLength 1 */
  payment_method_id: string;
  set_as_default: boolean;
}

export interface PaymentMethodConfirmResponseApi {
  status: boolean;
  result: PaymentMethodConfirmResultApi;
}

export type UsagePlanOptionApiFeatures = {[key: string]: { [key: string]: unknown }};

export interface UsagePlanOptionApi {
  /** @minLength 1 */
  key: string;
  /** @minLength 1 */
  display_name: string;
  platform_fee_monthly: number;
  is_current: boolean;
  features: UsagePlanOptionApiFeatures;
}

export interface UsagePricingTierApi {
  up_to?: number;
  price_per_unit: number;
}

export interface UsagePricingDimensionApi {
  /** @minLength 1 */
  display_name: string;
  /** @minLength 1 */
  display_unit: string;
  tiers: UsagePricingTierApi[];
}

export type UsageCustomPlanDetailsApiFeatures = {[key: string]: { [key: string]: unknown }};

export type UsageCustomPlanDetailsApiPricing = {[key: string]: { [key: string]: unknown }};

export interface UsageCustomPlanDetailsApi {
  platform_fee: number;
  platform_fee_billing_cycle: number;
  per_charge_amount: number;
  /** @minLength 1 */
  contract_end_date?: string;
  features: UsageCustomPlanDetailsApiFeatures;
  pricing: UsageCustomPlanDetailsApiPricing;
}

export type UsagePlansAndAddonsResultApiPricing = {[key: string]: UsagePricingDimensionApi};

export interface UsagePlansAndAddonsResultApi {
  /** @minLength 1 */
  current_plan: string;
  /** @minLength 1 */
  billing_interval: string;
  tiers: UsagePlanOptionApi[];
  addons: UsagePlanOptionApi[];
  pricing: UsagePlansAndAddonsResultApiPricing;
  isCustomPricing: boolean;
  customDetails?: UsageCustomPlanDetailsApi;
  pending_cancel: boolean;
  /** @minLength 1 */
  cancel_at?: string;
}

export interface UsagePlansAndAddonsResponseApi {
  status: boolean;
  result: UsagePlansAndAddonsResultApi;
}

export type StripeWebhookRequestApiData = {[key: string]: string};

export interface StripeWebhookRequestApi {
  /** @minLength 1 */
  id?: string;
  /** @minLength 1 */
  type?: string;
  data?: StripeWebhookRequestApiData;
}

export interface StripeWebhookResultApi {
  event_type?: string;
  action?: string;
  status?: string;
  message?: string;
}

export interface StripeWebhookResponseApi {
  /** @minLength 1 */
  status: string;
  result?: StripeWebhookResultApi;
}

export interface UpgradeToPaygPostResponseApi {
  status: boolean;
  result: UpgradeToPaygPostResultApi;
}

export interface UpgradeToPaygConfirmRequestApi {
  /** @minLength 1 */
  session_id: string;
}

export interface UsageTierBreakdownApi {
  tier_start?: number;
  tier_end?: number;
  quantity?: number;
  rate?: number;
  cost?: number;
}

export interface UsageOverviewDimensionApi {
  /** @minLength 1 */
  key: string;
  /** @minLength 1 */
  display_name: string;
  /** @minLength 1 */
  display_unit: string;
  current_usage: number;
  current_usage_raw: number;
  free_allowance: number;
  projected_usage: number;
  estimated_cost: number;
  tier_breakdown?: UsageTierBreakdownApi[];
  usage_pct: number;
}

export interface UsageOverviewResultApi {
  /** @minLength 1 */
  plan: string;
  /** @minLength 1 */
  plan_display_name: string;
  platform_fee: number;
  /** @minLength 1 */
  period: string;
  /** @minLength 1 */
  billing_period_start: string;
  /** @minLength 1 */
  billing_period_end: string;
  total_estimated_cost: number;
  total_with_platform: number;
  dimensions: UsageOverviewDimensionApi[];
  pending_cancel: boolean;
  /** @minLength 1 */
  cancel_at?: string;
}

export interface UsageOverviewResponseApi {
  status: boolean;
  result: UsageOverviewResultApi;
}

export interface UsageTimeSeriesPointApi {
  /** @minLength 1 */
  date: string;
  usage: number;
}

export interface UsageTimeSeriesResultApi {
  /** @minLength 1 */
  dimension: string;
  /** @minLength 1 */
  period: string;
  /** @minLength 1 */
  period_end: string;
  series: UsageTimeSeriesPointApi[];
}

export interface UsageTimeSeriesResponseApi {
  status: boolean;
  result: UsageTimeSeriesResultApi;
}

export interface UsageWorkspaceBreakdownItemApi {
  workspace_id?: string;
  /** @minLength 1 */
  workspace_name: string;
  usage: number;
}

export interface UsageWorkspaceBreakdownResultApi {
  /** @minLength 1 */
  dimension: string;
  /** @minLength 1 */
  period: string;
  /** @minLength 1 */
  period_end: string;
  workspaces: UsageWorkspaceBreakdownItemApi[];
}

export interface UsageWorkspaceBreakdownResponseApi {
  status: boolean;
  result: UsageWorkspaceBreakdownResultApi;
}

export interface StripeWebhookLegacyResponseApi {
  status: boolean;
  result?: StripeWebhookResultApi;
}

export type AccountsAwsMarketplaceLaunchSoftwareCreateBody = {
  'x-amzn-marketplace-token': string;
  'x-amzn-marketplace-product-id'?: string;
  'x-amzn-marketplace-agreement-id'?: string;
};

export type AccountsAwsMarketplaceVerifyTokenCreateBody = {
  'x-amzn-marketplace-token': string;
  'x-amzn-marketplace-product-id'?: string;
  'x-amzn-marketplace-agreement-id'?: string;
};

export type AccountsOrganizationMembersListParams = {
/**
 * @minimum 1
 */
page?: number;
/**
 * @minimum 1
 * @maximum 100
 */
limit?: number;
search?: string;
filter_status?: AccountsOrganizationMembersListFilterStatusItem[];
filter_role?: string[];
sort?: AccountsOrganizationMembersListSort;
};

export type AccountsOrganizationMembersListFilterStatusItem = typeof AccountsOrganizationMembersListFilterStatusItem[keyof typeof AccountsOrganizationMembersListFilterStatusItem];


export const AccountsOrganizationMembersListFilterStatusItem = {
  Active: 'Active',
  Pending: 'Pending',
  Expired: 'Expired',
  Deactivated: 'Deactivated',
} as const;

export type AccountsOrganizationMembersListSort = typeof AccountsOrganizationMembersListSort[keyof typeof AccountsOrganizationMembersListSort];


export const AccountsOrganizationMembersListSort = {
  name: 'name',
  '-name': '-name',
  email: 'email',
  '-email': '-email',
  status: 'status',
  '-status': '-status',
  type: 'type',
  '-type': '-type',
  date_joined: 'date_joined',
  '-date_joined': '-date_joined',
  created_at: 'created_at',
  '-created_at': '-created_at',
  org_level: 'org_level',
  '-org_level': '-org_level',
} as const;

export type AccountsUserListListParams = {
/**
 * @minimum 1
 */
page?: number;
/**
 * @minimum 1
 * @maximum 100
 */
limit?: number;
search?: string;
sort?: string;
workspace_id?: string;
filter_status?: AccountsUserListListFilterStatusItem[];
filter_role?: AccountsUserListListFilterRoleItem[];
};

export type AccountsUserListListFilterStatusItem = typeof AccountsUserListListFilterStatusItem[keyof typeof AccountsUserListListFilterStatusItem];


export const AccountsUserListListFilterStatusItem = {
  All_status: 'All status',
  Active: 'Active',
  Inactive: 'Inactive',
  Pending: 'Pending',
  Expired: 'Expired',
  Request_Pending: 'Request Pending',
  Request_Expired: 'Request Expired',
} as const;

export type AccountsUserListListFilterRoleItem = typeof AccountsUserListListFilterRoleItem[keyof typeof AccountsUserListListFilterRoleItem];


export const AccountsUserListListFilterRoleItem = {
  Owner: 'Owner',
  Admin: 'Admin',
  Member: 'Member',
  Viewer: 'Viewer',
  workspace_admin: 'workspace_admin',
  workspace_member: 'workspace_member',
  workspace_viewer: 'workspace_viewer',
} as const;

export type AccountsWorkspaceListListParams = {
/**
 * @minimum 1
 */
page?: number;
/**
 * @minimum 1
 * @maximum 100
 */
limit?: number;
search?: string;
sort?: string;
};

export type AccountsWorkspaceMembersListParams = {
/**
 * @minimum 1
 */
page?: number;
/**
 * @minimum 1
 * @maximum 100
 */
limit?: number;
search?: string;
filter_status?: AccountsWorkspaceMembersListFilterStatusItem[];
filter_role?: string[];
sort?: AccountsWorkspaceMembersListSort;
};

export type AccountsWorkspaceMembersListFilterStatusItem = typeof AccountsWorkspaceMembersListFilterStatusItem[keyof typeof AccountsWorkspaceMembersListFilterStatusItem];


export const AccountsWorkspaceMembersListFilterStatusItem = {
  Active: 'Active',
  Pending: 'Pending',
  Expired: 'Expired',
} as const;

export type AccountsWorkspaceMembersListSort = typeof AccountsWorkspaceMembersListSort[keyof typeof AccountsWorkspaceMembersListSort];


export const AccountsWorkspaceMembersListSort = {
  name: 'name',
  '-name': '-name',
  email: 'email',
  '-email': '-email',
  status: 'status',
  '-status': '-status',
  type: 'type',
  '-type': '-type',
  date_joined: 'date_joined',
  '-date_joined': '-date_joined',
  created_at: 'created_at',
  '-created_at': '-created_at',
  ws_level: 'ws_level',
  '-ws_level': '-ws_level',
} as const;

export type AgentPlaygroundGraphsListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentPlaygroundGraphsList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: GraphListApi[];
};

export type AgentPlaygroundGraphsExecutionsListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentPlaygroundGraphsVersionsReadParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentPlaygroundGraphsVersionsRead200 = {
  count: number;
  next?: string;
  previous?: string;
  results: GraphListApi[];
};

export type AgentPlaygroundGraphsVersionsReadParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentPlaygroundGraphsVersionsRead200 = {
  count: number;
  next?: string;
  previous?: string;
  results: GraphListApi[];
};

export type AgentPlaygroundGraphsVersionsNodesPossibleEdgeMappingsParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentPlaygroundGraphsVersionsNodesPossibleEdgeMappings200 = {
  count: number;
  next?: string;
  previous?: string;
  results: NodeReadApi[];
};

export type AgentPlaygroundNodeTemplatesListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentPlaygroundNodeTemplatesList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: NodeTemplateListApi[];
};

export type AgentccAnalyticsCostBreakdownParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccAnalyticsCostBreakdown200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccRequestLogApi[];
};

export type AgentccAnalyticsErrorBreakdownParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccAnalyticsErrorBreakdown200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccRequestLogApi[];
};

export type AgentccAnalyticsGuardrailOverviewParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccAnalyticsGuardrailOverview200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccRequestLogApi[];
};

export type AgentccAnalyticsGuardrailRulesParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccAnalyticsGuardrailRules200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccRequestLogApi[];
};

export type AgentccAnalyticsGuardrailTrendsParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccAnalyticsGuardrailTrends200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccRequestLogApi[];
};

export type AgentccAnalyticsLatencyStatsParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccAnalyticsLatencyStats200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccRequestLogApi[];
};

export type AgentccAnalyticsModelComparisonParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccAnalyticsModelComparison200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccRequestLogApi[];
};

export type AgentccAnalyticsOverviewParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccAnalyticsOverview200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccRequestLogApi[];
};

export type AgentccAnalyticsUsageTimeseriesParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccAnalyticsUsageTimeseries200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccRequestLogApi[];
};

export type AgentccApiKeysListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccApiKeysList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccAPIKeyApi[];
};

export type AgentccBlocklistsListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccBlocklistsList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccBlocklistApi[];
};

export type AgentccCustomPropertiesListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccCustomPropertiesList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccCustomPropertySchemaApi[];
};

export type AgentccEmailAlertsListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccEmailAlertsList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccEmailAlertApi[];
};

export type AgentccGuardrailConfigsPiiEntitiesParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccGuardrailConfigsTopicsParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccGuardrailFeedbackListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccGuardrailFeedbackList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccGuardrailFeedbackApi[];
};

export type AgentccGuardrailFeedbackSummaryParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccGuardrailFeedbackSummary200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccGuardrailFeedbackApi[];
};

export type AgentccGuardrailPoliciesListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccGuardrailPoliciesList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccGuardrailPolicyApi[];
};

export type AgentccOrgConfigsListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccOrgConfigsList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccOrgConfigApi[];
};

export type AgentccOrgConfigsActiveParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccOrgConfigsActive200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccOrgConfigApi[];
};

export type AgentccProviderCredentialsListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccProviderCredentialsList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccProviderCredentialApi[];
};

export type AgentccRequestLogsListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccRequestLogsList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccRequestLogApi[];
};

export type AgentccRequestLogsExportParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccRequestLogsExport200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccRequestLogApi[];
};

export type AgentccRequestLogsSearchParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccRequestLogsSearch200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccRequestLogApi[];
};

export type AgentccRequestLogsSessionsParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccRequestLogsSessions200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccRequestLogApi[];
};

export type AgentccRequestLogsSessionDetailParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccRequestLogsSessionDetail200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccRequestLogApi[];
};

export type AgentccRoutingPoliciesListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccRoutingPoliciesList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccRoutingPolicyApi[];
};

export type AgentccSessionsListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccSessionsList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccSessionApi[];
};

export type AgentccShadowExperimentsListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccShadowExperimentsList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccShadowExperimentApi[];
};

export type AgentccShadowResultsListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccShadowResultsList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccShadowResultApi[];
};

export type AgentccSpendSummaryListParams = {
period?: AgentccSpendSummaryListPeriod;
};

export type AgentccSpendSummaryListPeriod = typeof AgentccSpendSummaryListPeriod[keyof typeof AgentccSpendSummaryListPeriod];


export const AgentccSpendSummaryListPeriod = {
  daily: 'daily',
  weekly: 'weekly',
  monthly: 'monthly',
  total: 'total',
} as const;

export type AgentccWebhookEventsListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccWebhookEventsList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccWebhookEventApi[];
};

export type AgentccWebhooksListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type AgentccWebhooksList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentccWebhookApi[];
};

export type ApiTracesSpanAttributeDetailListParams = {
project_id: string;
/**
 * @minLength 1
 */
key: string;
};

export type ApiTracesSpanAttributeKeysListParams = {
project_id: string;
};

export type ApiTracesSpanAttributeValuesListParams = {
project_id: string;
/**
 * @minLength 1
 */
key: string;
q?: string;
/**
 * @minimum 1
 * @maximum 500
 */
limit?: number;
};

export type FalconAiFilesUploadCreateBody = {
  /** File to upload into the Falcon conversation context. */
  file: Blob;
};

export type FalconAiMcpConnectorsOauthCallbackListParams = {
code?: string;
state?: string;
error?: string;
error_description?: string;
};

export type IntegrationsConnectionsListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
/**
 * @minimum 0
 */
page_number?: number;
/**
 * @minimum 1
 * @maximum 100
 */
page_size?: number;
};

export type IntegrationsSyncLogsListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
/**
 * @minimum 0
 */
page_number?: number;
/**
 * @minimum 1
 * @maximum 100
 */
page_size?: number;
connection_id?: string;
};

export type ModelHubAnnotationQueuesListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
status?: string;
search?: string;
include_counts?: boolean;
archived?: boolean;
/**
 * @minimum 1
 */
page_size?: number;
};

export type ModelHubAnnotationQueuesList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AnnotationQueueApi[];
};

export type ModelHubAnnotationQueuesForSourceParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
source_type?: ModelHubAnnotationQueuesForSourceSourceType;
source_id?: string;
sources?: string;
};

export type ModelHubAnnotationQueuesForSourceSourceType = typeof ModelHubAnnotationQueuesForSourceSourceType[keyof typeof ModelHubAnnotationQueuesForSourceSourceType];


export const ModelHubAnnotationQueuesForSourceSourceType = {
  call_execution: 'call_execution',
  dataset_row: 'dataset_row',
  observation_span: 'observation_span',
  prototype_run: 'prototype_run',
  trace: 'trace',
  trace_session: 'trace_session',
} as const;

export type ModelHubAnnotationQueuesExportAnnotationsParams = {
export_format?: ModelHubAnnotationQueuesExportAnnotationsExportFormat;
status?: string;
};

export type ModelHubAnnotationQueuesExportAnnotationsExportFormat = typeof ModelHubAnnotationQueuesExportAnnotationsExportFormat[keyof typeof ModelHubAnnotationQueuesExportAnnotationsExportFormat];


export const ModelHubAnnotationQueuesExportAnnotationsExportFormat = {
  json: 'json',
  csv: 'csv',
} as const;

export type ModelHubAnnotationQueuesAutomationRulesListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubAnnotationQueuesAutomationRulesList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AutomationRuleApi[];
};

export type ModelHubAnnotationQueuesItemsListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
status?: string[];
source_type?: string[];
assigned_to?: string;
review_status?: string;
ordering?: ModelHubAnnotationQueuesItemsListOrdering;
};

export type ModelHubAnnotationQueuesItemsListOrdering = typeof ModelHubAnnotationQueuesItemsListOrdering[keyof typeof ModelHubAnnotationQueuesItemsListOrdering];


export const ModelHubAnnotationQueuesItemsListOrdering = {
  created_at: 'created_at',
  '-created_at': '-created_at',
} as const;

export type ModelHubAnnotationQueuesItemsList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: QueueItemApi[];
};

export type ModelHubAnnotationQueuesItemsNextItemParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
exclude?: string;
before?: string;
review_status?: string;
exclude_review_status?: string;
include_completed?: boolean;
view_mode?: string;
include_all_annotations?: boolean;
};

export type ModelHubAnnotationQueuesItemsAnnotateDetailParams = {
annotator_id?: string;
include_completed?: boolean;
view_mode?: string;
review_status?: string;
exclude_review_status?: string;
include_all_annotations?: boolean;
reserve?: boolean;
};

export type ModelHubAnnotationTasksListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
/**
 * Optional AI model id to filter annotation tasks.
 */
predictive_journey?: string;
};

export type ModelHubAnnotationTasksList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AnnotationTaskApi[];
};

export type ModelHubAnnotationsLabelsListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
dataset?: string;
project_id?: string;
type?: ModelHubAnnotationsLabelsListType;
search?: string;
include_usage_count?: boolean;
include_archived?: boolean;
archived?: boolean;
};

export type ModelHubAnnotationsLabelsListType = typeof ModelHubAnnotationsLabelsListType[keyof typeof ModelHubAnnotationsLabelsListType];


export const ModelHubAnnotationsLabelsListType = {
  text: 'text',
  numeric: 'numeric',
  categorical: 'categorical',
  star: 'star',
  thumbs_up_down: 'thumbs_up_down',
} as const;

export type ModelHubAnnotationsListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubAnnotationsList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AnnotationsApi[];
};

export type ModelHubAnnotationsAnnotateRowParams = {
/**
 * @minimum 0
 */
row_order: number;
};

export type ModelHubApiKeysListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubDatasetOptimizationListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubDatasetOptimizationList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: DatasetOptimizationListApi[];
};

export type ModelHubDevelopsGetDatasetsListParams = {
search_text?: string;
/**
 * @minimum 0
 */
page?: number;
/**
 * @minimum 1
 * @maximum 100
 */
page_size?: number;
sort?: string;
};

export type ModelHubDevelopsGetDatasetTableListParams = {
/**
 * @minLength 1
 */
filters?: string;
/**
 * @minLength 1
 */
sort?: string;
search?: string;
/**
 * @minimum 1
 */
page_size?: number;
/**
 * @minimum 0
 */
current_page_index?: number;
column_config_only?: boolean;
};

export type ModelHubDevelopsGetEvalStructureReadParams = {
eval_type: ModelHubDevelopsGetEvalStructureReadEvalType;
};

export type ModelHubDevelopsGetEvalStructureReadEvalType = typeof ModelHubDevelopsGetEvalStructureReadEvalType[keyof typeof ModelHubDevelopsGetEvalStructureReadEvalType];


export const ModelHubDevelopsGetEvalStructureReadEvalType = {
  preset: 'preset',
  user: 'user',
  previously_configured: 'previously_configured',
} as const;

export type ModelHubEvalGroupsListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubEvalGroupsList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: EvalGroupApi[];
};

export type ModelHubEvalTemplatesUsageListParams = {
/**
 * @minimum 0
 * @maximum 10000
 */
page?: number;
/**
 * @minimum 1
 * @maximum 100
 */
page_size?: number;
period?: ModelHubEvalTemplatesUsageListPeriod;
start_date?: string;
end_date?: string;
};

export type ModelHubEvalTemplatesUsageListPeriod = typeof ModelHubEvalTemplatesUsageListPeriod[keyof typeof ModelHubEvalTemplatesUsageListPeriod];


export const ModelHubEvalTemplatesUsageListPeriod = {
  '30m': '30m',
  '6h': '6h',
  '1d': '1d',
  '7d': '7d',
  '30d': '30d',
  '90d': '90d',
  '180d': '180d',
  '365d': '365d',
} as const;

export type ModelHubExperimentDetailListParams = {
/**
 * A search term.
 */
search?: string;
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubExperimentDetailList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: ExperimentsTableGetApi[];
};

export type ModelHubExperimentsDataListParams = {
created_at?: string;
status?: string;
dataset_id?: string;
/**
 * Which field to use when ordering the results.
 */
ordering?: string;
/**
 * A search term.
 */
search?: string;
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubExperimentsDataList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: ExperimentListApi[];
};

export type ModelHubExperimentsV2ListListParams = {
created_at?: string;
status?: string;
dataset_id?: string;
/**
 * A search term.
 */
search?: string;
/**
 * Which field to use when ordering the results.
 */
ordering?: string;
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubExperimentsV2ListList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: ExperimentListV2Api[];
};

export type ModelHubFeedbackListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubFeedbackList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: FeedbackApi[];
};

export type ModelHubFeedbackGetFeedbackDetailsParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubFeedbackGetFeedbackSummaryParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubFeedbackGetFeedbackSummary200 = {
  count: number;
  next?: string;
  previous?: string;
  results: FeedbackApi[];
};

export type ModelHubFeedbackGetTemplateParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubFeedbackGetTemplate200 = {
  count: number;
  next?: string;
  previous?: string;
  results: FeedbackApi[];
};

export type ModelHubGetEvalConfigListParams = {
eval_id: string;
};

export type ModelHubGetEvalLogsDetailsListParams = {
eval_template_id: string;
/**
 * @minimum 1
 */
page_size?: number;
/**
 * @minimum 0
 */
current_page_index?: number;
source?: ModelHubGetEvalLogsDetailsListSource;
search?: string;
/**
 * @minLength 1
 */
filters?: string;
/**
 * @minLength 1
 */
sort?: string;
};

export type ModelHubGetEvalLogsDetailsListSource = typeof ModelHubGetEvalLogsDetailsListSource[keyof typeof ModelHubGetEvalLogsDetailsListSource];


export const ModelHubGetEvalLogsDetailsListSource = {
  logs: 'logs',
  feedback: 'feedback',
  eval_playground: 'eval_playground',
} as const;

export type ModelHubGetEvalMetricsListParams = {
eval_template_id: string;
/**
 * @minLength 1
 */
filters?: string;
};

export type ModelHubKbListParams = {
/**
 * A search term.
 */
search?: string;
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubKbSupportedEmbeddingModelsParams = {
/**
 * A search term.
 */
search?: string;
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubKbSupportedEmbeddingModelsParams = {
/**
 * A search term.
 */
search?: string;
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubOptimisationListParams = {
optimize_type?: string;
status?: string;
/**
 * A search term.
 */
search?: string;
/**
 * Which field to use when ordering the results.
 */
ordering?: string;
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubOptimisationList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: OptimizationDatasetApi[];
};

export type ModelHubOptimizeDatasetListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubOptimizeDatasetList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: OptimizeDatasetKbApi[];
};

export type ModelHubOptimizeDatasetReadParams = {
/**
 * @minLength 1
 */
filters?: string;
/**
 * @minimum 1
 */
page?: number;
/**
 * @minimum 1
 */
limit?: number;
};

export type ModelHubOptimizeDatasetPromptTemplateExploreCreate200ResultsItem = {
  id?: string;
  input?: string;
  output?: string;
  right_answer?: string;
  [key: string]: number;
};

export type ModelHubOptimizeDatasetPromptTemplateExploreCreate200 = {
  results: ModelHubOptimizeDatasetPromptTemplateExploreCreate200ResultsItem[];
  count: number;
  total_pages: number;
  current_page: number;
  next: boolean;
  previous: boolean;
  message: string;
};

export type ModelHubOptimizeDatasetRightAnswersCreate200ResultsItem = {
  id?: string;
  input?: string;
  output?: string;
  right_answer?: string;
  [key: string]: number;
};

export type ModelHubOptimizeDatasetRightAnswersCreate200 = {
  results: ModelHubOptimizeDatasetRightAnswersCreate200ResultsItem[];
  count: number;
  total_pages: number;
  current_page: number;
  next: boolean;
  previous: boolean;
  message: string;
};

export type ModelHubOrganizationsUsersListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
/**
 * Filter organization users by name or email.
 */
search?: string;
/**
 * Filter users by active status.
 */
is_active?: boolean;
};

export type ModelHubOrganizationsUsersList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: DevelopAnnotationsUserApi[];
};

/**
 * Tag distribution chart data. `all` returns `good` and `bad`; single-tag views return the selected distribution series.
 */
export type ModelHubPerformanceTagDistributionCreate200Result = {
  good?: string[][];
  bad?: string[][];
  [key: string]: string[][];
};

export type ModelHubPerformanceTagDistributionCreate200 = {
  status: boolean;
  /** Tag distribution chart data. `all` returns `good` and `bad`; single-tag views return the selected distribution series. */
  result: ModelHubPerformanceTagDistributionCreate200Result;
};

/**
 * Map of dataset or breakdown label to chart rows.
 */
export type ModelHubPerformanceCreate200 = {[key: string]: string[][]};

export type ModelHubPromptBaseTemplatesListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubPromptBaseTemplatesList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: PromptBaseTemplateApi[];
};

export type ModelHubPromptBaseTemplatesGetAllCategoriesParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubPromptBaseTemplatesGetAllCategories200 = {
  count: number;
  next?: string;
  previous?: string;
  results: PromptBaseTemplateApi[];
};

export type ModelHubPromptExecutionsListParams = {
name?: string;
/**
 * A search term.
 */
search?: string;
/**
 * Which field to use when ordering the results.
 */
ordering?: string;
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubPromptExecutionsList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: PromptExecutionApi[];
};

export type ModelHubPromptFoldersListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubPromptFoldersList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: PromptFolderApi[];
};

export type ModelHubPromptHistoryExecutionsListParams = {
template_name?: string;
template_version?: string;
created_at?: string;
/**
 * A search term.
 */
search?: string;
/**
 * Which field to use when ordering the results.
 */
ordering?: string;
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubPromptHistoryExecutionsList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: PromptHistoryExecutionApi[];
};

export type ModelHubPromptHistoryExecutionsGetExecutionDetailsParams = {
template_name?: string;
template_version?: string;
created_at?: string;
/**
 * A search term.
 */
search?: string;
/**
 * Which field to use when ordering the results.
 */
ordering?: string;
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubPromptHistoryExecutionsGetExecutionDetails200 = {
  count: number;
  next?: string;
  previous?: string;
  results: PromptHistoryExecutionApi[];
};

export type ModelHubPromptLabelsListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubPromptLabelsList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: PromptLabelApi[];
};

export type ModelHubPromptLabelsGetByNameParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubPromptLabelsGetByName200 = {
  count: number;
  next?: string;
  previous?: string;
  results: PromptLabelApi[];
};

export type ModelHubPromptLabelsTemplateLabelsParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubPromptLabelsTemplateLabels200 = {
  count: number;
  next?: string;
  previous?: string;
  results: PromptLabelApi[];
};

export type ModelHubPromptTemplatesListParams = {
name?: string;
version?: string;
created_at?: string;
/**
 * A search term.
 */
search?: string;
/**
 * Which field to use when ordering the results.
 */
ordering?: string;
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubPromptTemplatesList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: PromptTemplateApi[];
};

export type ModelHubPromptTemplatesGetTemplateByNameParams = {
name?: string;
version?: string;
created_at?: string;
/**
 * A search term.
 */
search?: string;
/**
 * Which field to use when ordering the results.
 */
ordering?: string;
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubPromptTemplatesGetTemplateByName200 = {
  count: number;
  next?: string;
  previous?: string;
  results: PromptTemplateApi[];
};

export type ModelHubPromptMetricsListParams = {
prompt_template_id: string;
/**
 * @minLength 1
 */
filters?: string;
search_term?: string;
/**
 * @minimum 0
 */
page_number?: number;
/**
 * @minimum 1
 * @maximum 100
 */
page_size?: number;
};

export type ModelHubPromptSpanMetricsListParams = {
prompt_template_id: string;
/**
 * @minLength 1
 */
filters?: string;
search_term?: string;
/**
 * @minimum 0
 */
page_number?: number;
/**
 * @minimum 1
 * @maximum 100
 */
page_size?: number;
};

export type ModelHubResponseSchemaListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubResponseSchemaList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: UserResponseSchemaApi[];
};

export type ModelHubScoresListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
source_type?: ModelHubScoresListSourceType;
source_id?: string;
label_id?: string;
annotator_id?: string;
};

export type ModelHubScoresListSourceType = typeof ModelHubScoresListSourceType[keyof typeof ModelHubScoresListSourceType];


export const ModelHubScoresListSourceType = {
  dataset_row: 'dataset_row',
  trace: 'trace',
  observation_span: 'observation_span',
  prototype_run: 'prototype_run',
  call_execution: 'call_execution',
  trace_session: 'trace_session',
} as const;

export type ModelHubScoresList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: ScoreApi[];
};

export type ModelHubScoresForSourceParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
source_type: ModelHubScoresForSourceSourceType;
/**
 * @minLength 1
 */
source_id: string;
};

export type ModelHubScoresForSourceSourceType = typeof ModelHubScoresForSourceSourceType[keyof typeof ModelHubScoresForSourceSourceType];


export const ModelHubScoresForSourceSourceType = {
  dataset_row: 'dataset_row',
  trace: 'trace',
  observation_span: 'observation_span',
  prototype_run: 'prototype_run',
  call_execution: 'call_execution',
  trace_session: 'trace_session',
} as const;

export type ModelHubSecretsListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubSecretsList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: SecretApi[];
};

export type ModelHubToolsListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubToolsList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: ToolsApi[];
};

export type ModelHubTtsVoicesListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type ModelHubTtsVoicesList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: TTSVoiceApi[];
};

export type Saml2AuthAcsCreateBodyOne = {
  /** Base64-encoded SAML response from the identity provider. */
  SAMLResponse: string;
  /** Relay state configured for the organization IdP. */
  RelayState?: string;
};

export type Saml2AuthAcsCreateBodyTwo = {
  /** Base64-encoded SAML response from the identity provider. */
  SAMLResponse: string;
  /** Relay state configured for the organization IdP. */
  RelayState?: string;
};

export type Saml2AuthAuthCallbackListParams = {
code?: string;
};

export type Saml2AuthAuthReadParams = {
code?: string;
};

export type Saml2AuthGithubCallbackListParams = {
code?: string;
};

export type Saml2AuthGithubReadParams = {
code?: string;
};

export type Saml2AuthIdpLoginListParams = {
/**
 * @minLength 1
 */
email: string;
next?: string;
};

export type Saml2AuthIdpUploadsListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type Saml2AuthIdpUploadsCreateBodyOne = {
  /** Display name for the identity provider. */
  name?: string;
  /** Identity provider type. */
  identity_type: number;
  /** Whether this IdP is enabled. */
  is_enabled?: boolean;
  /** SAML metadata XML file. */
  file?: Blob;
};

export type Saml2AuthIdpUploadsCreateBodyTwo = {
  /** Display name for the identity provider. */
  name?: string;
  /** Identity provider type. */
  identity_type: number;
  /** Whether this IdP is enabled. */
  is_enabled?: boolean;
  /** SAML metadata XML file. */
  file?: Blob;
};

export type Saml2AuthIdpUploadsUpdateBodyOne = {
  /** Display name for the identity provider. */
  name?: string;
  /** Identity provider type. */
  identity_type: number;
  /** Whether this IdP is enabled. */
  is_enabled?: boolean;
  /** SAML metadata XML file. */
  file?: Blob;
};

export type Saml2AuthIdpUploadsUpdateBodyTwo = {
  /** Display name for the identity provider. */
  name?: string;
  /** Identity provider type. */
  identity_type: number;
  /** Whether this IdP is enabled. */
  is_enabled?: boolean;
  /** SAML metadata XML file. */
  file?: Blob;
};

export type Saml2AuthLoginListParams = {
provider: Saml2AuthLoginListProvider;
};

export type Saml2AuthLoginListProvider = typeof Saml2AuthLoginListProvider[keyof typeof Saml2AuthLoginListProvider];


export const Saml2AuthLoginListProvider = {
  google: 'google',
  github: 'github',
  microsoft: 'microsoft',
} as const;

export type Saml2AuthReadParams = {
provider: Saml2AuthReadProvider;
};

export type Saml2AuthReadProvider = typeof Saml2AuthReadProvider[keyof typeof Saml2AuthReadProvider];


export const Saml2AuthReadProvider = {
  google: 'google',
  github: 'github',
  microsoft: 'microsoft',
} as const;

export type Saml2AuthMicrosoftCallbackListParams = {
code?: string;
};

export type Saml2AuthMicrosoftReadParams = {
code?: string;
};

export type SdkApiV1EvaluatePipelineListParams = {
/**
 * @minLength 1
 */
project_name: string;
/**
 * @minLength 1
 */
versions: string;
};

export type SdkApiV1NewEvalListParams = {
eval_id: string;
};

export type SdkApiV1SimulationAnalyticsListParams = {
/**
 * @minLength 1
 */
run_test_name?: string;
execution_id?: string;
/**
 * @minLength 1
 */
eval_name?: string;
summary?: boolean;
};

export type SdkApiV1SimulationMetricsListParams = {
/**
 * @minLength 1
 */
run_test_name?: string;
execution_id?: string;
call_execution_id?: string;
};

export type SdkApiV1SimulationRunsListParams = {
/**
 * @minLength 1
 */
run_test_name?: string;
execution_id?: string;
call_execution_id?: string;
/**
 * @minLength 1
 */
eval_name?: string;
summary?: boolean;
};

export type SimulateAgentDefinitionsListParams = {
search?: string;
agent_type?: SimulateAgentDefinitionsListAgentType;
agent_definition_id?: string;
/**
 * @minimum 1
 */
page?: number;
/**
 * @minimum 1
 */
limit?: number;
};

export type SimulateAgentDefinitionsListAgentType = typeof SimulateAgentDefinitionsListAgentType[keyof typeof SimulateAgentDefinitionsListAgentType];


export const SimulateAgentDefinitionsListAgentType = {
  voice: 'voice',
  text: 'text',
} as const;

export type SimulateApiAgentDefinitionOperationsListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type SimulateApiAgentDefinitionOperationsList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentDefinitionResponseApi[];
};

export type SimulateApiAgentPromptOptimiserListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type SimulateApiAgentPromptOptimiserList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: AgentPromptOptimiserRunListApi[];
};

export type SimulateApiCallExecutionsListParams = {
search?: string;
status?: string;
test_execution_id?: string;
/**
 * @minimum 1
 */
page?: number;
/**
 * @minimum 1
 */
limit?: number;
};

/**
 * LiveKit webhook payload verified against the Authorization JWT.
 */
export type SimulateApiLivekitWebhookCreateBody = { [key: string]: unknown };

export type SimulateApiPersonasListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type SimulateApiPersonasList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: PersonaListApi[];
};

export type SimulateApiPersonasFieldOptionsParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type SimulateApiPersonasFieldOptions200 = {
  count: number;
  next?: string;
  previous?: string;
  results: PersonaFieldOptionsApi[];
};

export type SimulateApiPersonasSystemPersonasParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type SimulateApiPersonasSystemPersonas200 = {
  count: number;
  next?: string;
  previous?: string;
  results: PersonaApi[];
};

export type SimulateApiPersonasWorkspacePersonasParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type SimulateApiPersonasWorkspacePersonas200 = {
  count: number;
  next?: string;
  previous?: string;
  results: PersonaApi[];
};

export type SimulateApiRunTestsListParams = {
search?: string;
simulation_type?: SimulateApiRunTestsListSimulationType;
prompt_template_id?: string;
/**
 * @minimum 1
 */
page?: number;
/**
 * @minimum 1
 */
limit?: number;
};

export type SimulateApiRunTestsListSimulationType = typeof SimulateApiRunTestsListSimulationType[keyof typeof SimulateApiRunTestsListSimulationType];


export const SimulateApiRunTestsListSimulationType = {
  agent_definition: 'agent_definition',
  prompt: 'prompt',
} as const;

export type SimulateExportReadParams = {
/**
 * Export source type.
 */
type: SimulateExportReadType;
/**
 * Optional call-execution search term.
 */
search?: string;
/**
 * Optional call-execution status filter.
 */
status?: string;
};

export type SimulateExportReadType = typeof SimulateExportReadType[keyof typeof SimulateExportReadType];


export const SimulateExportReadType = {
  runtest: 'runtest',
  testexecution: 'testexecution',
} as const;

export type SimulateRunTestsListParams = {
search?: string;
simulation_type?: SimulateRunTestsListSimulationType;
prompt_template_id?: string;
/**
 * @minimum 1
 */
page?: number;
/**
 * @minimum 1
 */
limit?: number;
};

export type SimulateRunTestsListSimulationType = typeof SimulateRunTestsListSimulationType[keyof typeof SimulateRunTestsListSimulationType];


export const SimulateRunTestsListSimulationType = {
  agent_definition: 'agent_definition',
  prompt: 'prompt',
} as const;

export type SimulateRunTestsEvalSummaryComparisonListParams = {
/**
 * JSON-encoded array of test execution UUIDs to compare. Example: ["uuid1","uuid2"]. Must be URL-encoded.
 * @minLength 1
 */
execution_ids: string;
};

export type SimulateRunTestsEvalSummaryListParams = {
/**
 * UUID of a specific test execution to scope the summary to. If omitted, aggregates across all executions.
 */
execution_id?: string;
};

export type SimulateScenariosListParams = {
search?: string;
agent_definition_id?: string;
/**
 * @minLength 1
 */
agent_type?: string;
/**
 * @minimum 1
 */
page?: number;
/**
 * @minimum 1
 */
limit?: number;
};

export type SimulateScenariosGetColumnsListParams = {
search?: string;
agent_definition_id?: string;
/**
 * @minLength 1
 */
agent_type?: string;
/**
 * @minimum 1
 */
page?: number;
/**
 * @minimum 1
 */
limit?: number;
};

export type SimulateTestExecutionsReadParams = {
search?: string;
/**
 * @minLength 1
 */
filters?: string;
/**
 * @minLength 1
 */
row_groups?: string;
/**
 * @minLength 1
 */
group_keys?: string;
/**
 * @minimum 1
 */
page?: number;
/**
 * @minimum 1
 */
limit?: number;
};

export type TracerChartsFetchGraphParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerChartsFetchGraph200 = {
  count: number;
  next?: string;
  previous?: string;
  results: FetchGraphApi[];
};

export type TracerCustomEvalConfigListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerCustomEvalConfigList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: CustomEvalConfigApi[];
};

export type TracerCustomEvalConfigListCustomEvalConfigsParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerCustomEvalConfigListCustomEvalConfigs200 = {
  count: number;
  next?: string;
  previous?: string;
  results: CustomEvalConfigApi[];
};

export type TracerDashboardListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerDashboardList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: DashboardApi[];
};

export type TracerDashboardFilterValuesParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerDashboardFilterValues200 = {
  count: number;
  next?: string;
  previous?: string;
  results: DashboardApi[];
};

export type TracerDashboardMetricsParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerDashboardSimulationAgentsParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerDashboardSimulationAgents200 = {
  count: number;
  next?: string;
  previous?: string;
  results: DashboardApi[];
};

export type TracerDashboardWidgetsListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerDashboardWidgetsList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: DashboardWidgetApi[];
};

export type TracerDatasetListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerDatasetList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: ObserveDatasetApi[];
};

export type TracerEvalTaskListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerEvalTaskList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: EvalTaskApi[];
};

export type TracerEvalTaskGetEvalDetailsParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerEvalTaskGetEvalDetails200 = {
  count: number;
  next?: string;
  previous?: string;
  results: EvalTaskApi[];
};

export type TracerEvalTaskGetEvalTaskLogsParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerEvalTaskGetEvalTaskLogs200 = {
  count: number;
  next?: string;
  previous?: string;
  results: EvalTaskApi[];
};

export type TracerEvalTaskGetUsageParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerEvalTaskGetUsage200 = {
  count: number;
  next?: string;
  previous?: string;
  results: EvalTaskApi[];
};

export type TracerEvalTaskListEvalTasksParams = {
project_id?: string;
name?: string;
/**
 * @minLength 1
 */
filters?: string;
/**
 * @minLength 1
 */
sort_params?: string;
/**
 * @minimum 0
 */
page_number?: number;
/**
 * @minimum 1
 * @maximum 500
 */
page_size?: number;
};

export type TracerEvalTaskListEvalTasksWithProjectNameParams = {
project_id?: string;
name?: string;
/**
 * @minLength 1
 */
filters?: string;
/**
 * @minLength 1
 */
sort_params?: string;
/**
 * @minimum 0
 */
page_number?: number;
/**
 * @minimum 1
 * @maximum 500
 */
page_size?: number;
};

export type TracerEvalTaskPauseEvalTaskParams = {
eval_task_id: string;
};

export type TracerEvalTaskUnpauseEvalTaskParams = {
eval_task_id: string;
};

export type TracerFeedIssuesListParams = {
project_id?: string;
search?: string;
status?: TracerFeedIssuesListStatus;
severity?: TracerFeedIssuesListSeverity;
fix_layer?: string;
source?: TracerFeedIssuesListSource;
issue_group?: string;
/**
 * @minimum 1
 */
time_range_days?: number;
sort_by?: TracerFeedIssuesListSortBy;
sort_dir?: TracerFeedIssuesListSortDir;
/**
 * @minimum 1
 * @maximum 200
 */
limit?: number;
/**
 * @minimum 0
 */
offset?: number;
};

export type TracerFeedIssuesListStatus = typeof TracerFeedIssuesListStatus[keyof typeof TracerFeedIssuesListStatus];


export const TracerFeedIssuesListStatus = {
  escalating: 'escalating',
  for_review: 'for_review',
  acknowledged: 'acknowledged',
  resolved: 'resolved',
} as const;

export type TracerFeedIssuesListSeverity = typeof TracerFeedIssuesListSeverity[keyof typeof TracerFeedIssuesListSeverity];


export const TracerFeedIssuesListSeverity = {
  critical: 'critical',
  high: 'high',
  medium: 'medium',
  low: 'low',
} as const;

export type TracerFeedIssuesListSource = typeof TracerFeedIssuesListSource[keyof typeof TracerFeedIssuesListSource];


export const TracerFeedIssuesListSource = {
  scanner: 'scanner',
  eval: 'eval',
} as const;

export type TracerFeedIssuesListSortBy = typeof TracerFeedIssuesListSortBy[keyof typeof TracerFeedIssuesListSortBy];


export const TracerFeedIssuesListSortBy = {
  last_seen: 'last_seen',
  first_seen: 'first_seen',
  error_count: 'error_count',
  unique_traces: 'unique_traces',
  severity: 'severity',
} as const;

export type TracerFeedIssuesListSortDir = typeof TracerFeedIssuesListSortDir[keyof typeof TracerFeedIssuesListSortDir];


export const TracerFeedIssuesListSortDir = {
  asc: 'asc',
  desc: 'desc',
} as const;

export type TracerFeedIssuesStatsListParams = {
project_id?: string;
/**
 * @minimum 1
 */
time_range_days?: number;
};

export type TracerFeedIssuesReadParams = {
project_id?: string;
};

export type TracerFeedIssuesOverviewListParams = {
/**
 * @minimum 1
 * @maximum 200
 */
rep_limit?: number;
};

export type TracerFeedIssuesRootCauseListParams = {
/**
 * @minLength 1
 */
trace_id: string;
};

export type TracerFeedIssuesSidebarListParams = {
/**
 * @minLength 1
 */
trace_id?: string;
};

export type TracerFeedIssuesTracesListParams = {
/**
 * @minimum 1
 * @maximum 500
 */
limit?: number;
/**
 * @minimum 0
 */
offset?: number;
};

export type TracerFeedIssuesTrendsListParams = {
/**
 * @minimum 1
 * @maximum 90
 */
days?: number;
};

export type TracerGetAnnotationLabelsListParams = {
project_id?: string;
};

export type TracerImagineAnalysisListParams = {
saved_view_id: string;
/**
 * @minLength 1
 * @maxLength 255
 */
trace_id: string;
};

export type TracerObservabilityProviderListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerObservabilityProviderList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: ObservabilityProviderApi[];
};

export type TracerObservationSpanListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerObservationSpanList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: ObservationSpanApi[];
};

export type TracerObservationSpanGetEvalAttributesListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
/**
 * @minLength 1
 */
filters: string;
row_type?: TracerObservationSpanGetEvalAttributesListRowType;
};

export type TracerObservationSpanGetEvalAttributesListRowType = typeof TracerObservationSpanGetEvalAttributesListRowType[keyof typeof TracerObservationSpanGetEvalAttributesListRowType];


export const TracerObservationSpanGetEvalAttributesListRowType = {
  spans: 'spans',
  traces: 'traces',
  sessions: 'sessions',
  voiceCalls: 'voiceCalls',
} as const;

export type TracerObservationSpanGetEvaluationDetailsParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerObservationSpanGetEvaluationDetails200 = {
  count: number;
  next?: string;
  previous?: string;
  results: ObservationSpanApi[];
};

export type TracerObservationSpanGetObservationSpanFieldsParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerObservationSpanGetObservationSpanFields200 = {
  count: number;
  next?: string;
  previous?: string;
  results: ObservationSpanApi[];
};

export type TracerObservationSpanGetSpanAttributesListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
/**
 * @minLength 1
 */
filters: string;
row_type?: TracerObservationSpanGetSpanAttributesListRowType;
};

export type TracerObservationSpanGetSpanAttributesListRowType = typeof TracerObservationSpanGetSpanAttributesListRowType[keyof typeof TracerObservationSpanGetSpanAttributesListRowType];


export const TracerObservationSpanGetSpanAttributesListRowType = {
  spans: 'spans',
  traces: 'traces',
  sessions: 'sessions',
  voiceCalls: 'voiceCalls',
} as const;

export type TracerObservationSpanGetSpansExportDataParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerObservationSpanGetSpansExportData200 = {
  count: number;
  next?: string;
  previous?: string;
  results: ObservationSpanApi[];
};

export type TracerObservationSpanGetTraceIdByIndexSpansAsBaseParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
/**
 * @minLength 1
 */
span_id: string;
project_version_id: string;
/**
 * @minLength 1
 */
filters?: string;
};

export type TracerObservationSpanGetTraceIdByIndexSpansAsBase200 = {
  count: number;
  next?: string;
  previous?: string;
  results: ObservationSpanApi[];
};

export type TracerObservationSpanGetTraceIdByIndexSpansAsObserveParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
/**
 * @minLength 1
 */
span_id: string;
project_id: string;
user_id?: string;
/**
 * @minLength 1
 */
filters?: string;
};

export type TracerObservationSpanGetTraceIdByIndexSpansAsObserve200 = {
  count: number;
  next?: string;
  previous?: string;
  results: ObservationSpanApi[];
};

export type TracerObservationSpanListSpansParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerObservationSpanListSpans200 = {
  count: number;
  next?: string;
  previous?: string;
  results: ObservationSpanApi[];
};

export type TracerObservationSpanListSpansObserveParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
project_id?: string;
user_id?: string;
/**
 * @minLength 1
 */
filters?: string;
/**
 * @minimum 0
 */
page_number?: number;
/**
 * @minimum 1
 * @maximum 500
 */
page_size?: number;
};

export type TracerObservationSpanListSpansObserve200 = {
  count: number;
  next?: string;
  previous?: string;
  results: ObservationSpanApi[];
};

export type TracerObservationSpanRetrieveLoadingParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerObservationSpanRetrieveLoading200 = {
  count: number;
  next?: string;
  previous?: string;
  results: ObservationSpanApi[];
};

export type TracerObservationSpanRootSpansParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
trace_ids: string[];
project_ids?: string[];
};

export type TracerProjectVersionListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerProjectVersionList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: ProjectVersionApi[];
};

export type TracerProjectVersionGetProjectVersionIdsParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerProjectVersionGetProjectVersionIds200 = {
  count: number;
  next?: string;
  previous?: string;
  results: ProjectVersionApi[];
};

export type TracerProjectVersionGetRunInsightsParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerProjectVersionGetRunInsights200 = {
  count: number;
  next?: string;
  previous?: string;
  results: ProjectVersionApi[];
};

export type TracerProjectVersionListRunsParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerProjectVersionListRuns200 = {
  count: number;
  next?: string;
  previous?: string;
  results: ProjectVersionApi[];
};

export type TracerProjectListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerProjectList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: ProjectApi[];
};

export type TracerProjectFetchSystemMetricsParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerProjectFetchSystemMetrics200 = {
  count: number;
  next?: string;
  previous?: string;
  results: ProjectApi[];
};

export type TracerProjectGetGraphDataParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
project_id: string;
/**
 * @minLength 1
 */
interval?: string;
/**
 * @minLength 1
 */
filters?: string;
};

export type TracerProjectGetGraphData200 = {
  count: number;
  next?: string;
  previous?: string;
  results: ProjectApi[];
};

export type TracerProjectGetUserGraphDataParams = {
project_id: string;
end_user_id: string;
};

export type TracerProjectListProjectIdsParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerProjectListProjectsParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerProjectListProjects200 = {
  count: number;
  next?: string;
  previous?: string;
  results: ProjectApi[];
};

export type TracerProjectProjectSdkCodeParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerProjectProjectSdkCode200 = {
  count: number;
  next?: string;
  previous?: string;
  results: ProjectApi[];
};

export type TracerSavedViewsListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerSharedLinksListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerSharedLinksList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: SharedLinkListApi[];
};

export type TracerTraceAnnotationGetAnnotationValuesParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
/**
 * @minLength 1
 * @maxLength 255
 */
observation_span_id?: string;
trace_id?: string;
annotators?: string;
exclude_annotators?: string;
};

export type TracerTraceSessionListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerTraceSessionList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: TraceSessionApi[];
};

export type TracerTraceSessionGetSessionFilterValuesParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerTraceSessionGetSessionFilterValues200 = {
  count: number;
  next?: string;
  previous?: string;
  results: TraceSessionApi[];
};

export type TracerTraceSessionGetTraceSessionExportDataParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerTraceSessionGetTraceSessionExportData200 = {
  count: number;
  next?: string;
  previous?: string;
  results: TraceSessionApi[];
};

export type TracerTraceSessionListSessionsParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
project_id?: string;
user_id?: string;
bookmarked?: boolean;
/**
 * @minLength 1
 */
filters?: string;
/**
 * @minLength 1
 */
sort_params?: string;
/**
 * @minimum 0
 */
page_number?: number;
/**
 * @minimum 1
 * @maximum 500
 */
page_size?: number;
interval?: string;
};

export type TracerTraceSessionListSessions200 = {
  count: number;
  next?: string;
  previous?: string;
  results: TraceSessionApi[];
};

export type TracerTraceListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerTraceList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: TraceApi[];
};

export type TracerTraceAgentGraphParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
project_id: string;
/**
 * @minLength 1
 */
filters?: string;
};

export type TracerTraceAgentGraph200 = {
  count: number;
  next?: string;
  previous?: string;
  results: TraceApi[];
};

export type TracerTraceGetEvalNamesParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerTraceGetEvalNames200 = {
  count: number;
  next?: string;
  previous?: string;
  results: TraceApi[];
};

export type TracerTraceGetPropertiesParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerTraceGetProperties200 = {
  count: number;
  next?: string;
  previous?: string;
  results: TraceApi[];
};

export type TracerTraceGetTraceExportDataParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerTraceGetTraceExportData200 = {
  count: number;
  next?: string;
  previous?: string;
  results: TraceApi[];
};

export type TracerTraceGetTraceIdByIndexParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
trace_id: string;
project_version_id: string;
/**
 * @minLength 1
 */
filters?: string;
};

export type TracerTraceGetTraceIdByIndex200 = {
  count: number;
  next?: string;
  previous?: string;
  results: TraceApi[];
};

export type TracerTraceGetTraceIdByIndexObserveParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
trace_id: string;
project_id: string;
/**
 * @minLength 1
 */
filters?: string;
};

export type TracerTraceGetTraceIdByIndexObserve200 = {
  count: number;
  next?: string;
  previous?: string;
  results: TraceApi[];
};

export type TracerTraceListTracesParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
project_version_id: string;
trace_ids?: string;
/**
 * @minLength 1
 */
filters?: string;
/**
 * @minLength 1
 */
sort_params?: string;
/**
 * @minimum 0
 */
page_number?: number;
/**
 * @minimum 1
 * @maximum 500
 */
page_size?: number;
};

export type TracerTraceListTraces200 = {
  count: number;
  next?: string;
  previous?: string;
  results: TraceApi[];
};

export type TracerTraceListTracesOfSessionParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
project_id?: string;
project_version_id?: string;
session_id?: string;
/**
 * @minLength 1
 */
filters?: string;
/**
 * @minimum 0
 */
page_number?: number;
/**
 * @minimum 1
 * @maximum 500
 */
page_size?: number;
interval?: string;
};

export type TracerTraceListVoiceCallsParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerTraceListVoiceCalls200 = {
  count: number;
  next?: string;
  previous?: string;
  results: TraceApi[];
};

export type TracerTraceVoiceCallDetailParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerTraceVoiceCallDetail200 = {
  count: number;
  next?: string;
  previous?: string;
  results: TraceApi[];
};

export type TracerUserAlertLogsListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerUserAlertLogsList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: UserAlertMonitorLogApi[];
};

export type TracerUserAlertLogsListAllParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerUserAlertLogsListAll200 = {
  count: number;
  next?: string;
  previous?: string;
  results: UserAlertMonitorLogApi[];
};

export type TracerUserAlertsListParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerUserAlertsList200 = {
  count: number;
  next?: string;
  previous?: string;
  results: UserAlertMonitorApi[];
};

export type TracerUserAlertsListMonitorsParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerUserAlertsListMonitors200 = {
  count: number;
  next?: string;
  previous?: string;
  results: UserAlertMonitorApi[];
};

export type TracerUserAlertsMetricOptionsParams = {
/**
 * A page number within the paginated result set.
 */
page?: number;
/**
 * Number of results to return per page.
 */
limit?: number;
};

export type TracerUsersListParams = {
project_id?: string;
search?: string;
/**
 * @minimum 1
 * @maximum 500
 */
page_size?: number;
/**
 * @minimum 0
 */
current_page_index?: number;
/**
 * @minLength 1
 */
sort_params?: string;
/**
 * @minLength 1
 */
filters?: string;
export?: boolean;
};

export type UsageAdminCustomPlanListParams = {
organization_id: string;
/**
 * @minLength 1
 */
dimension?: string;
};

export type UsageAdminEntitlementsListParams = {
organization_id: string;
/**
 * @minLength 1
 */
feature?: string;
};

export type UsageAdminEntitlementsDeleteParams = {
organization_id: string;
/**
 * @minLength 1
 */
feature?: string;
};

export type UsageAdminPricingListParams = {
organization_id: string;
/**
 * @minLength 1
 */
dimension?: string;
};

export type UsageAdminPricingDeleteParams = {
organization_id: string;
/**
 * @minLength 1
 */
dimension?: string;
};

export type UsageApiCallCountListParams = {
year?: number;
/**
 * @minimum 1
 * @maximum 12
 */
month?: number;
/**
 * @minLength 1
 */
api_call_type?: string;
};

export type UsageUsageSummaryListParams = {
/**
 * @minimum 1
 * @maximum 12
 */
month?: number;
year?: number;
};

export type UsageV2UsageOverviewListParams = {
/**
 * @minLength 1
 * @pattern ^\d{4}-\d{2}$
 */
period?: string;
/**
 * @minLength 1
 * @pattern ^\d{4}-\d{2}$
 */
period_end?: string;
workspace_id?: string;
};

export type UsageV2UsageTimeSeriesListParams = {
/**
 * @minLength 1
 */
dimension: string;
/**
 * @minLength 1
 * @pattern ^\d{4}-\d{2}$
 */
period?: string;
/**
 * @minLength 1
 * @pattern ^\d{4}-\d{2}$
 */
period_end?: string;
};

export type UsageV2UsageWorkspaceBreakdownListParams = {
/**
 * @minLength 1
 */
dimension: string;
/**
 * @minLength 1
 * @pattern ^\d{4}-\d{2}$
 */
period?: string;
/**
 * @minLength 1
 * @pattern ^\d{4}-\d{2}$
 */
period_end?: string;
};

export type UsageWorkspaceEvalSummaryListParams = {
/**
 * @minimum 1
 * @maximum 12
 */
month?: number;
year?: number;
workspace_id: string;
};

export type UsageWorkspaceUsageSummaryListParams = {
/**
 * @minimum 1
 * @maximum 12
 */
month?: number;
year?: number;
};
