import json
from pathlib import Path


def _repo_root():
    return Path(__file__).resolve().parents[3]


def _swagger():
    with (
        _repo_root() / "api_contracts" / "openapi" / "swagger.json"
    ).open() as f:
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
    return operation["responses"][status_code]["schema"]["$ref"].rsplit("/", 1)[-1]


def test_accounts_contract_debt_is_fully_burned_down():
    report = _debt_report()

    assert report["by_group"]["accounts"]["mutation_endpoints_without_body_schema"] == 0
    assert report["by_group"]["accounts"]["operations_without_response_schema"] == 0


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
        ("POST", "/accounts/organization/members/role/"): "MemberRoleUpdate",
        ("POST", "/accounts/passkey/register/options/"): "AccountsEmptyRequest",
        ("PATCH", "/accounts/passkeys/{id}/"): "PasskeyRename",
        ("POST", "/accounts/token/refresh/"): "TokenRefreshRequest",
        ("POST", "/accounts/workspace/invite/"): "WorkspaceInvite",
        ("POST", "/accounts/workspaces/"): "WorkspaceCreateRequest",
        ("POST", "/accounts/workspaces/{workspace_id}/members/"): (
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
        ("GET", "/accounts/config/"): "AccountsJSONResponse",
        ("GET", "/accounts/key/get_secret_keys/"): "AccountsPaginatedResponse",
        ("POST", "/accounts/me/timezone/"): "TimezoneResponse",
        ("GET", "/accounts/organization/members/"): "AccountsJSONResponse",
        ("POST", "/accounts/passkey/register/options/"): "PasskeyOptionsResponse",
        ("PATCH", "/accounts/passkeys/{id}/"): "AccountsJSONResponse",
        ("POST", "/accounts/token/refresh/"): "AccountsAccessTokenResponse",
        ("GET", "/accounts/workspace/list/"): "AccountsJSONResponse",
        ("GET", "/accounts/workspaces/"): "AccountsJSONResponse",
        ("POST", "/accounts/workspaces/{workspace_id}/members/"): (
            "AccountsJSONResponse"
        ),
    }

    for (method, path), definition_name in expected.items():
        assert _response_ref(_operation(path, method)) == definition_name
