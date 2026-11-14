import json
from pathlib import Path


def _repo_root():
    return Path(__file__).resolve().parents[3]


def _swagger():
    with (_repo_root() / "api_contracts" / "openapi" / "swagger.json").open() as f:
        return json.load(f)


def _debt_report():
    with (
        _repo_root()
        / "api_contracts"
        / "openapi"
        / "management-api-contract-debt.generated.json"
    ).open() as f:
        return json.load(f)


def _operation(path, method):
    return _swagger()["paths"][path][method.lower()]


def _body_ref(operation):
    body = next(
        parameter
        for parameter in operation.get("parameters", [])
        if parameter.get("in") == "body"
    )
    return body["schema"]["$ref"].rsplit("/", 1)[-1]


def _response_ref(operation, status_code="200"):
    responses = operation["responses"]
    if status_code not in responses:
        status_code = next(code for code in sorted(responses) if code.startswith("2"))
    return responses[status_code]["schema"]["$ref"].rsplit("/", 1)[-1]


def test_accounts_contract_debt_is_fully_burned_down():
    report = _debt_report()
    group_report = report["by_group"]["accounts"]

    assert group_report["mutation_endpoints_without_body_schema"] == 0
    assert group_report["operations_without_response_schema"] == 0
    assert group_report["operations_without_error_response_schema"] == 0
    assert group_report["broad_error_response_schemas"] == 0


def test_accounts_mutations_have_request_contracts():
    expected = {
        ("POST", "/accounts/2fa/totp/confirm/"): "TOTPConfirm",
        ("POST", "/accounts/2fa/verify/totp/"): "TwoFactorVerify",
        ("POST", "/accounts/accept-invitation/{uidb64}/{token}/"): (
            "AcceptInvitationRequest"
        ),
        ("POST", "/accounts/key/generate_secret_key/"): "CreateSecretKey",
        ("POST", "/accounts/me/timezone/"): "TimezoneRequest",
        ("POST", "/accounts/organization/invite/"): "InviteCreate",
        ("DELETE", "/accounts/organization/invite/cancel/"): "InviteCancel",
        ("POST", "/accounts/organization/invite/resend/"): "InviteResend",
        ("DELETE", "/accounts/organization/members/remove/"): "MemberRemove",
        ("POST", "/accounts/organization/members/reactivate/"): "MemberRemove",
        ("POST", "/accounts/organization/members/role/"): "MemberRoleUpdate",
        ("POST", "/accounts/passkey/register/options/"): "AccountsEmptyRequest",
        ("PATCH", "/accounts/passkeys/{id}/"): "PasskeyRename",
        ("POST", "/accounts/token/refresh/"): "TokenRefreshRequest",
        ("POST", "/accounts/workspace/invite/"): "WorkspaceInvite",
        ("DELETE", "/accounts/workspace/{workspace_id}/members/remove/"): (
            "WorkspaceMemberRemove"
        ),
        ("POST", "/accounts/workspace/{workspace_id}/members/role/"): (
            "WorkspaceMemberRoleUpdate"
        ),
        ("POST", "/accounts/workspaces/"): "WorkspaceCreateRequest",
        ("PUT", "/accounts/workspaces/"): "WorkspaceUpdateRequest",
        ("POST", "/accounts/workspaces/{workspace_id}/"): ("WorkspaceCreateRequest"),
        ("PUT", "/accounts/workspaces/{workspace_id}/"): "WorkspaceUpdateRequest",
        ("POST", "/accounts/workspaces/{workspace_id}/members/"): (
            "WorkspaceMembersRequest"
        ),
        ("POST", "/accounts/workspaces/{workspace_id}/members/{member_id}/"): (
            "WorkspaceMembersRequest"
        ),
    }

    for (method, path), definition_name in expected.items():
        assert _body_ref(_operation(path, method)) == definition_name


def test_accounts_endpoints_have_response_contracts():
    expected = {
        ("GET", "/accounts/2fa/status/"): "TwoFactorStatus",
        ("POST", "/accounts/2fa/verify/recovery/"): "AccountsTokenPairResponse",
        ("POST", "/accounts/accept-invitation/{uidb64}/{token}/"): (
            "AccountsTokenPairResponse"
        ),
        ("GET", "/accounts/config/"): "PublicConfigResponse",
        ("GET", "/accounts/key/get_secret_keys/"): "SecretKeyListResponse",
        ("POST", "/accounts/me/timezone/"): "TimezoneResponse",
        ("POST", "/accounts/organization/invite/"): "InviteCreateResponse",
        ("DELETE", "/accounts/organization/invite/cancel/"): "RBACMessageResponse",
        ("POST", "/accounts/organization/invite/resend/"): "RBACMessageResponse",
        ("GET", "/accounts/organization/members/"): "MemberListResponse",
        ("DELETE", "/accounts/organization/members/remove/"): (
            "MemberUserMutationResponse"
        ),
        ("POST", "/accounts/organization/members/reactivate/"): (
            "MemberUserMutationResponse"
        ),
        ("POST", "/accounts/organization/members/role/"): ("MemberRoleUpdateResponse"),
        ("POST", "/accounts/passkey/register/options/"): "PasskeyOptionsResponse",
        ("PATCH", "/accounts/passkeys/{id}/"): "PasskeyRenameResponse",
        ("POST", "/accounts/token/refresh/"): "AccountsAccessTokenResponse",
        ("GET", "/accounts/workspace/{workspace_id}/members/"): (
            "WorkspaceMemberListResponse"
        ),
        ("DELETE", "/accounts/workspace/{workspace_id}/members/remove/"): (
            "MemberUserMutationResponse"
        ),
        ("POST", "/accounts/workspace/{workspace_id}/members/role/"): (
            "WorkspaceMemberRoleUpdateResponse"
        ),
        ("GET", "/accounts/workspace/list/"): "WorkspaceListPaginatedResponse",
        ("GET", "/accounts/workspaces/"): "WorkspaceManagementListResponse",
        ("POST", "/accounts/workspaces/"): "WorkspaceCreateResponse",
        ("PUT", "/accounts/workspaces/"): "WorkspaceUpdateResponse",
        ("DELETE", "/accounts/workspaces/"): "WorkspaceDeleteResponse",
        ("GET", "/accounts/workspaces/{workspace_id}/"): (
            "WorkspaceManagementListResponse"
        ),
        ("POST", "/accounts/workspaces/{workspace_id}/"): ("WorkspaceCreateResponse"),
        ("PUT", "/accounts/workspaces/{workspace_id}/"): "WorkspaceUpdateResponse",
        ("DELETE", "/accounts/workspaces/{workspace_id}/"): ("WorkspaceDeleteResponse"),
        ("GET", "/accounts/workspaces/{workspace_id}/members/"): (
            "WorkspaceMembersListResponse"
        ),
        ("POST", "/accounts/workspaces/{workspace_id}/members/"): (
            "WorkspaceMembersAddResponse"
        ),
        ("DELETE", "/accounts/workspaces/{workspace_id}/members/"): (
            "WorkspaceMemberRemoveResponse"
        ),
        ("GET", "/accounts/workspaces/{workspace_id}/members/{member_id}/"): (
            "WorkspaceMembersListResponse"
        ),
        ("POST", "/accounts/workspaces/{workspace_id}/members/{member_id}/"): (
            "WorkspaceMembersAddResponse"
        ),
        ("DELETE", "/accounts/workspaces/{workspace_id}/members/{member_id}/"): (
            "WorkspaceMemberRemoveResponse"
        ),
    }

    for (method, path), definition_name in expected.items():
        assert _response_ref(_operation(path, method)) == definition_name
