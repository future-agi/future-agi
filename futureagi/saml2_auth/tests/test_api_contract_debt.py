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


def _form_param_names(operation):
    return {
        parameter["name"]
        for parameter in operation.get("parameters", [])
        if parameter.get("in") == "formData"
    }


def _response_ref(operation, status_code="200"):
    return operation["responses"][status_code]["schema"]["$ref"].rsplit("/", 1)[-1]


def test_saml_contract_debt_is_fully_burned_down():
    report = _debt_report()
    saml_report = report["by_group"]["saml2_auth"]

    assert [
        item
        for item in report["mutation_endpoints_without_body_schema"]
        if item["tags"] == ["saml2_auth"]
    ] == []
    assert [
        item
        for item in report["operations_without_response_schema"]
        if item["tags"] == ["saml2_auth"]
    ] == []
    assert saml_report["operations_without_error_response_schema"] == 0
    assert saml_report["broad_error_response_schemas"] == 0


def test_saml_form_mutations_have_form_data_contracts():
    assert _form_param_names(_operation("/saml2_auth/acs/", "POST")) == {
        "SAMLResponse",
        "RelayState",
    }
    assert _form_param_names(_operation("/saml2_auth/idp-uploads/", "POST")) == {
        "file",
        "identity_type",
        "is_enabled",
        "name",
    }
    assert _form_param_names(_operation("/saml2_auth/idp-uploads/{id}/", "PUT")) == {
        "file",
        "identity_type",
        "is_enabled",
        "name",
    }


def test_saml_json_endpoints_have_response_contracts():
    assert _response_ref(_operation("/saml2_auth/idp-login/", "GET")) == (
        "SAMLUrlResponse"
    )
    assert _response_ref(_operation("/saml2_auth/login/", "GET")) == ("SAMLUrlResponse")
    assert _response_ref(_operation("/saml2_auth/idp-uploads/", "POST")) == (
        "SAMLStringResponse"
    )
    assert _response_ref(_operation("/saml2_auth/idp-uploads/{id}/", "PUT")) == (
        "SAMLStringResponse"
    )
    assert _response_ref(_operation("/saml2_auth/idp-uploads/", "GET")) == (
        "SAMLIDPUploadListResponse"
    )
    assert _response_ref(_operation("/saml2_auth/idp-uploads/{id}/", "GET")) == (
        "SAMLIDPUploadDetailResponse"
    )
    assert _response_ref(_operation("/saml2_auth/idp-uploads/{id}/", "DELETE")) == (
        "SAMLStringResponse"
    )
    assert _response_ref(_operation("/saml2_auth/idp-uploads/", "POST"), "400") == (
        "SAMLErrorResponse"
    )


def test_saml_oauth_callbacks_are_documented_as_redirects():
    assert set(_operation("/saml2_auth/acs/", "POST")["responses"]) == {
        "302",
        "400",
        "default",
    }

    for path in (
        "/saml2_auth/auth/callback/",
        "/saml2_auth/auth/callback{format}",
        "/saml2_auth/github/callback/",
        "/saml2_auth/github/callback{format}",
        "/saml2_auth/microsoft/callback/",
        "/saml2_auth/microsoft/callback{format}",
    ):
        assert set(_operation(path, "GET")["responses"]) == {
            "302",
            "400",
            "default",
        }


def test_saml_idp_login_rejects_unknown_query_param(api_client):
    response = api_client.get(
        "/saml2_auth/idp-login/",
        {"email": "person@example.com", "legacy": "1"},
    )

    assert response.status_code == 400
    assert response.json()["details"]["legacy"] == ["Unknown field."]


def test_saml_social_login_rejects_unknown_query_param(api_client):
    response = api_client.get(
        "/saml2_auth/login/",
        {"provider": "google", "legacy": "1"},
    )

    assert response.status_code == 400
    assert response.json()["details"]["legacy"] == ["Unknown field."]


def test_saml_format_suffix_public_boundaries(api_client, monkeypatch):
    def fail_provider_exchange(*args, **kwargs):
        raise AssertionError("missing callback code should not contact provider")

    monkeypatch.setattr("saml2_auth.views.requests.post", fail_provider_exchange)

    login_response = api_client.get(
        "/saml2_auth/login.json",
        {"provider": "slack"},
    )
    assert login_response.status_code == 400
    assert "provider" in login_response.json()["details"]

    for path in (
        "/saml2_auth/auth/callback.json",
        "/saml2_auth/github/callback.json",
        "/saml2_auth/microsoft/callback.json",
    ):
        response = api_client.get(path)
        assert response.status_code == 302
        assert response["Location"]
        assert "sso_token" not in response["Location"]
