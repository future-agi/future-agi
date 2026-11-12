"""Blank credential merge regressions for the gateway update-provider action.

The cases use the release line's save contract
(services/provider_validation.py): its api formats only (no systemone or
typesafe, so it has no keyless provider), an explicit base_url for a custom
provider name, and its credential keys (a non-blank api_secret is not a
Bedrock key there and is refused naming the key). The BYOK property is unchanged: a blank field is
absent, never stored over a saved value.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from agentcc.models.provider_credential import AgentccProviderCredential
from integrations.services.credentials import CredentialManager

pytestmark = pytest.mark.django_db

REQUIRED_MESSAGE = "API key or provider credentials are required"
GENERIC_URL = "https://decider.example.com"
BLANKS = [pytest.param("", id="empty"), pytest.param(" \t\r\n ", id="whitespace")]
BEDROCK_FIELDS = (
    "api_secret",
    "access_key",
    "secret_key",
    "region",
    "aws_access_key_id",
    "aws_secret_access_key",
    "aws_region",
    "aws_session_token",
)
# A blank api_secret is dropped before IR's credential-key check; a non-blank
# one is not an IR credential key (test_nonblank_api_secret_is_refused_by_name).
ROTATION_FIELDS = tuple(field for field in BEDROCK_FIELDS if field != "api_secret")


def _field_cases(fields):
    return [
        pytest.param((field, "bedrock", "bedrock", ""), id=field) for field in fields
    ] + [
        # IR needs an explicit endpoint for a provider name it does not know.
        pytest.param(
            ("aws_secret_access_key", "custom-bedrock", "bedrock", GENERIC_URL),
            id="aws_secret_access_key-format-bedrock",
        ),
    ]


FIELD_CASES = _field_cases(BEDROCK_FIELDS)
ROTATION_CASES = _field_cases(ROTATION_FIELDS)
# Vertex JSON has real preparation/rotation controls below; it cannot use the
# arbitrary non-string/non-JSON rotation values of the Bedrock fixture.
API_KEY_CASES = [
    pytest.param("openai", "openai", "https://api.openai.com/v1", id="openai"),
    pytest.param("custom-openai", "openai", GENERIC_URL, id="generic-openai"),
    pytest.param("anthropic", "anthropic", "", id="anthropic"),
    pytest.param("custom-anthropic", "anthropic", GENERIC_URL, id="generic-anthropic"),
]
GENERIC_FORMATS = ["openai", "anthropic"]
KEYLESS_CONFIGS = [
    pytest.param({}, id="omitted"),
    pytest.param({"api_key": ""}, id="empty"),
    pytest.param({"api_key": "   "}, id="whitespace"),
]


@pytest.fixture
def gateway():
    """Mock only the remote gateway client; persist and encrypt real test rows."""
    client = MagicMock()
    with patch("agentcc.services.config_push.get_gateway_client", return_value=client):
        yield client


@pytest.fixture(autouse=True)
def public_provider_dns(provider_dns):
    """Use IR's validator seam without replacing the process socket guard."""
    return provider_dns(
        {
            "decider.example.com": ["93.184.216.34"],
            "api.openai.com": ["93.184.216.34"],
            "us-central1-aiplatform.googleapis.com": ["93.184.216.34"],
        }
    )


@pytest.fixture(params=FIELD_CASES)
def credential_case(request):
    """One reachable credential field class per case, through the real endpoint."""
    return request.param


@pytest.fixture(params=ROTATION_CASES)
def rotation_case(request):
    """A field IR stores when sent non-blank (every IR Bedrock credential key)."""
    return request.param


def _save(client, name, api_format, fields, base_url=""):
    return client.post(
        "/agentcc/gateways/default/update-provider/",
        {
            "name": name,
            "config": {"base_url": base_url, "api_format": api_format, **fields},
        },
        format="json",
    )


def _store(organization, name, api_format, credentials, base_url=""):
    return AgentccProviderCredential.no_workspace_objects.create(
        organization=organization,
        provider_name=name,
        base_url=base_url,
        api_format=api_format,
        encrypted_credentials=CredentialManager.encrypt(credentials),
    )


def _persisted(credential):
    credential.refresh_from_db()
    return CredentialManager.decrypt(bytes(credential.encrypted_credentials))


def _initial_credentials(field):
    if field == "service_account_json":
        return {"api_key": "stored-key", field: "stored-secret"}
    credentials = {
        "aws_access_key_id": "stored-access",
        "aws_secret_access_key": "stored-secret",
        "aws_region": "us-east-1",
    }
    credentials.setdefault(field, "stored-secret")
    return credentials


def _keepalive(field, stored):
    if field == "service_account_json":
        key = "api_key"
    elif field == "aws_access_key_id":
        key = "aws_region"
    else:
        key = "aws_access_key_id"
    return {key: stored[key]}


def _assert_required(response):
    assert response.status_code == 400, response.json()
    assert REQUIRED_MESSAGE in response.content.decode("utf-8")


@pytest.mark.parametrize("blank", BLANKS)
def test_blank_field_preserves_stored_value(
    auth_client, organization, gateway, credential_case, blank
):
    field, name, api_format, base_url = credential_case
    stored = _initial_credentials(field)
    credential = _store(organization, name, api_format, stored, base_url)

    response = _save(
        auth_client,
        name,
        api_format,
        {**_keepalive(field, stored), field: blank},
        base_url,
    )

    assert response.status_code == 200, response.json()
    assert _persisted(credential) == stored
    assert credential.extra_config == {}


def test_blank_only_update_does_not_reencrypt(
    auth_client, organization, gateway, credential_case
):
    field, name, api_format, base_url = credential_case
    stored = _initial_credentials(field)
    credential = _store(organization, name, api_format, stored, base_url)
    ciphertext = bytes(credential.encrypted_credentials)

    response = _save(auth_client, name, api_format, {field: "   "}, base_url)

    assert response.status_code == 200, response.json()
    assert _persisted(credential) == stored
    assert bytes(credential.encrypted_credentials) == ciphertext


def test_nonblank_field_rotation_preserves_surrounding_whitespace(
    auth_client, organization, gateway, rotation_case
):
    field, name, api_format, base_url = rotation_case
    stored = _initial_credentials(field)
    credential = _store(organization, name, api_format, stored, base_url)
    replacement = "  rotated-secret \t"

    response = _save(auth_client, name, api_format, {field: replacement}, base_url)

    assert response.status_code == 200, response.json()
    assert _persisted(credential) == {**stored, field: replacement}


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(False, id="false"),
        pytest.param({"fixture": "prepared-value"}, id="object"),
    ],
)
def test_nonstring_field_values_pass_through(
    auth_client, organization, gateway, rotation_case, value
):
    field, name, api_format, base_url = rotation_case
    stored = _initial_credentials(field)
    credential = _store(organization, name, api_format, stored, base_url)

    response = _save(auth_client, name, api_format, {field: value}, base_url)

    assert response.status_code == 200, response.json()
    persisted = _persisted(credential)
    assert persisted == {**stored, field: value}
    assert type(persisted[field]) is type(value)


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("  rotated-secret \t", id="string"),
        pytest.param(False, id="false"),
        pytest.param({"fixture": "prepared-value"}, id="object"),
    ],
)
def test_nonblank_api_secret_is_refused_by_name(
    auth_client, organization, gateway, value
):
    """IR (NG-58): api_secret is not a Bedrock credential key. A non-blank one
    is refused naming only the key; nothing is stored, re-encrypted or pushed."""
    stored = _initial_credentials("aws_secret_access_key")
    credential = _store(organization, "bedrock", "bedrock", stored)
    ciphertext = bytes(credential.encrypted_credentials)

    response = _save(auth_client, "bedrock", "bedrock", {"api_secret": value})

    assert response.status_code == 400
    body = response.content.decode("utf-8")
    assert "api_secret" in body
    assert "stored-secret" not in body and "rotated-secret" not in body
    assert _persisted(credential) == stored
    assert bytes(credential.encrypted_credentials) == ciphertext
    gateway.set_org_config.assert_not_called()


def test_omitted_fields_keep_stored_values(
    auth_client, organization, gateway, credential_case
):
    field, name, api_format, base_url = credential_case
    stored = _initial_credentials(field)
    credential = _store(organization, name, api_format, stored, base_url)
    ciphertext = bytes(credential.encrypted_credentials)

    response = _save(
        auth_client, name, api_format, {"models": ["updated-model"]}, base_url
    )

    assert response.status_code == 200, response.json()
    assert _persisted(credential) == stored
    assert bytes(credential.encrypted_credentials) == ciphertext
    assert credential.models_list == ["updated-model"]


BEDROCK_NAMES = [
    pytest.param("bedrock", "", id="bedrock"),
    pytest.param("custom-bedrock", GENERIC_URL, id="custom-bedrock"),
]


@pytest.mark.parametrize("name,base_url", BEDROCK_NAMES)
@pytest.mark.parametrize("blank", BLANKS)
def test_blank_only_bedrock_create_is_refused(
    auth_client, organization, gateway, name, base_url, blank
):
    response = _save(
        auth_client, name, "bedrock", dict.fromkeys(BEDROCK_FIELDS, blank), base_url
    )

    _assert_required(response)
    assert not AgentccProviderCredential.no_workspace_objects.filter(
        organization=organization, provider_name=name, deleted=False
    ).exists()
    gateway.set_org_config.assert_not_called()


@pytest.mark.parametrize("name,base_url", BEDROCK_NAMES)
@pytest.mark.parametrize("blank", BLANKS)
def test_bedrock_create_drops_blank_fields(
    auth_client, organization, gateway, name, base_url, blank
):
    fields = dict.fromkeys(BEDROCK_FIELDS, blank)
    fields["aws_access_key_id"] = "  rotated-access  "

    response = _save(auth_client, name, "bedrock", fields, base_url)

    assert response.status_code == 200, response.json()
    credential = AgentccProviderCredential.no_workspace_objects.get(
        organization=organization, provider_name=name, deleted=False
    )
    assert _persisted(credential) == {"aws_access_key_id": fields["aws_access_key_id"]}
    assert credential.extra_config == {}


@pytest.mark.parametrize("api_format", GENERIC_FORMATS)
@pytest.mark.parametrize("blank", BLANKS)
def test_generic_blank_api_key_cannot_erase_a_stored_key(
    auth_client, organization, gateway, api_format, blank
):
    """IR has no keyless provider: a blank key on a custom endpoint is refused
    before any save, so neither the key nor the sent metadata is written."""
    stored = {"api_key": "  stored-key  "}
    credential = _store(organization, "custom-generic", api_format, stored, GENERIC_URL)
    ciphertext = bytes(credential.encrypted_credentials)
    updated_at = credential.updated_at
    with patch.object(AgentccProviderCredential, "save", autospec=True) as save:
        response = _save(
            auth_client,
            "custom-generic",
            api_format,
            {"api_key": blank, "models": ["must-not-be-saved"]},
            GENERIC_URL,
        )

    _assert_required(response)
    save.assert_not_called()
    assert _persisted(credential) == stored
    assert bytes(credential.encrypted_credentials) == ciphertext
    assert credential.updated_at == updated_at
    assert credential.models_list == []
    gateway.set_org_config.assert_not_called()


@pytest.mark.parametrize("api_format", GENERIC_FORMATS)
@pytest.mark.parametrize("key_config", KEYLESS_CONFIGS)
def test_generic_create_without_a_key_is_refused(
    auth_client, organization, gateway, api_format, key_config
):
    """IR has no keyless provider: an omitted or blank key on create is refused,
    with no row written and no config push."""
    response = _save(auth_client, "custom-generic", api_format, key_config, GENERIC_URL)

    _assert_required(response)
    assert not AgentccProviderCredential.no_workspace_objects.filter(
        organization=organization, provider_name="custom-generic", deleted=False
    ).exists()
    gateway.set_org_config.assert_not_called()


@pytest.mark.parametrize("name,api_format,base_url", API_KEY_CASES)
def test_nonblank_api_key_rotation_still_strips(
    auth_client, organization, gateway, name, api_format, base_url
):
    credential = _store(
        organization, name, api_format, {"api_key": "stored-key"}, base_url
    )

    response = _save(
        auth_client, name, api_format, {"api_key": "  rotated-key  "}, base_url
    )

    assert response.status_code == 200, response.json()
    assert _persisted(credential) == {"api_key": "rotated-key"}


@pytest.mark.parametrize("name,api_format,base_url", API_KEY_CASES)
def test_omitted_api_key_keeps_stored_key(
    auth_client, organization, gateway, name, api_format, base_url
):
    stored = {"api_key": "stored-key"}
    credential = _store(organization, name, api_format, stored, base_url)
    ciphertext = bytes(credential.encrypted_credentials)

    response = _save(
        auth_client, name, api_format, {"models": ["updated-model"]}, base_url
    )

    assert response.status_code == 200, response.json()
    assert _persisted(credential) == stored
    assert bytes(credential.encrypted_credentials) == ciphertext


# IR has no keyless format, so a blank key is refused for every provider.
@pytest.mark.parametrize("name,api_format,base_url", API_KEY_CASES)
@pytest.mark.parametrize("blank", BLANKS)
def test_vendor_blank_api_key_is_still_refused(
    auth_client, organization, gateway, name, api_format, base_url, blank
):
    stored = {"api_key": "stored-key"}
    credential = _store(organization, name, api_format, stored, base_url)
    ciphertext = bytes(credential.encrypted_credentials)
    updated_at = credential.updated_at

    response = _save(auth_client, name, api_format, {"api_key": blank}, base_url)

    _assert_required(response)
    assert _persisted(credential) == stored
    assert bytes(credential.encrypted_credentials) == ciphertext
    assert credential.updated_at == updated_at
    gateway.set_org_config.assert_not_called()


VERTEX_FIELDS = {"gcp_project": "fixture-project", "gcp_location": "us-central1"}
VERTEX_URL = (
    "https://us-central1-aiplatform.googleapis.com/v1beta1/"
    "projects/fixture-project/locations/us-central1"
)
PROVIDER_FAMILIES = [
    pytest.param("bedrock", "bedrock", "", id="bedrock"),
    pytest.param("custom-bedrock", "bedrock", GENERIC_URL, id="custom-bedrock"),
    pytest.param("azure", "azure", GENERIC_URL, id="azure"),
    pytest.param("openai", "openai", "https://api.openai.com/v1", id="openai"),
    pytest.param("anthropic", "anthropic", "", id="anthropic"),
    pytest.param("custom-generic", "openai", GENERIC_URL, id="generic"),
    pytest.param("vertex", "gemini", VERTEX_URL, id="vertex"),
]


def _aligned_credential(organization, name, api_format, credentials, base_url):
    credential = _store(organization, name, api_format, credentials, base_url)
    credential.display_name = name
    credential.default_timeout_seconds = 60
    credential.max_concurrent = 100
    credential.conn_pool_size = 100
    credential.models_list = []
    credential.extra_config = {}
    credential.is_active = True
    credential.save()
    return credential


@pytest.fixture
def valid_service_account_json():
    """Ephemeral key generated only when the test runs; no embedded key material."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return json.dumps(
        {
            "type": "service_account",
            "project_id": VERTEX_FIELDS["gcp_project"],
            "client_email": "fixture" + "@" + "example.invalid",
            "private_key": private_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ).decode(),
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    )


@pytest.mark.parametrize("blank", BLANKS)
def test_vertex_blank_service_account_preserves_ciphertext_without_push(
    auth_client, organization, gateway, blank
):
    stored = {"service_account_json": "-".join(("stored", "fixture"))}
    credential = _aligned_credential(
        organization, "vertex", "gemini", stored, VERTEX_URL
    )
    ciphertext = bytes(credential.encrypted_credentials)
    updated_at = credential.updated_at
    with (
        patch.object(
            CredentialManager, "encrypt", wraps=CredentialManager.encrypt
        ) as encrypt,
        patch("agentcc.views.gateway.push_org_config") as push,
    ):
        response = _save(
            auth_client,
            "vertex",
            "gemini",
            {**VERTEX_FIELDS, "service_account_json": blank},
            VERTEX_URL,
        )

    assert response.status_code == 200, response.json()
    assert _persisted(credential) == stored
    assert bytes(credential.encrypted_credentials) == ciphertext
    assert credential.updated_at == updated_at
    encrypt.assert_not_called()
    push.assert_not_called()
    gateway.set_org_config.assert_not_called()


def test_vertex_valid_service_account_rotates(
    auth_client, organization, gateway, valid_service_account_json
):
    credential = _aligned_credential(
        organization,
        "vertex",
        "gemini",
        {"service_account_json": "-".join(("stored", "fixture"))},
        VERTEX_URL,
    )
    ciphertext = bytes(credential.encrypted_credentials)
    with patch("agentcc.views.gateway.push_org_config", return_value=True) as push:
        response = _save(
            auth_client,
            "vertex",
            "gemini",
            {**VERTEX_FIELDS, "service_account_json": valid_service_account_json},
            VERTEX_URL,
        )

    assert response.status_code == 200, response.json()
    assert json.loads(_persisted(credential)["service_account_json"]) == json.loads(
        valid_service_account_json
    )
    assert bytes(credential.encrypted_credentials) != ciphertext
    push.assert_called_once()


@pytest.mark.parametrize("blank", BLANKS)
def test_vertex_blank_only_create_is_refused(auth_client, organization, gateway, blank):
    response = _save(
        auth_client,
        "vertex",
        "gemini",
        {**VERTEX_FIELDS, "service_account_json": blank},
        VERTEX_URL,
    )
    _assert_required(response)
    assert not AgentccProviderCredential.no_workspace_objects.filter(
        organization=organization, provider_name="vertex", deleted=False
    ).exists()
    gateway.set_org_config.assert_not_called()


def test_vertex_nonblank_malformed_json_is_value_free(
    auth_client, organization, gateway
):
    stored = {"service_account_json": "-".join(("stored", "fixture"))}
    credential = _aligned_credential(
        organization, "vertex", "gemini", stored, VERTEX_URL
    )
    ciphertext = bytes(credential.encrypted_credentials)
    malformed = "{" + "synthetic malformed diagnostic" + "!"
    with patch("agentcc.views.gateway.logger") as logger:
        response = _save(
            auth_client,
            "vertex",
            "gemini",
            {**VERTEX_FIELDS, "service_account_json": malformed},
            VERTEX_URL,
        )
    assert response.status_code == 400
    assert response.json()["detail"] == "Gateway request failed. (internal_error)"
    assert malformed not in json.dumps(response.json()) + repr(logger.mock_calls)
    assert _persisted(credential) == stored
    assert bytes(credential.encrypted_credentials) == ciphertext
    gateway.set_org_config.assert_not_called()


@pytest.mark.parametrize("name,api_format,base_url", PROVIDER_FAMILIES)
@pytest.mark.parametrize("field", BEDROCK_FIELDS + ("service_account_json",))
@pytest.mark.parametrize("blank", BLANKS)
def test_all_families_normalize_nonkey_blanks_before_preparation(
    auth_client, organization, gateway, name, api_format, base_url, field, blank
):
    from agentcc.views.gateway import _prepare_vertex_provider_config

    stored = {
        "api_key": "-".join(("fixture", "key")),
        field: "-".join(("fixture", "value")),
    }
    credential = _aligned_credential(organization, name, api_format, stored, base_url)
    ciphertext = bytes(credential.encrypted_credentials)

    def prepare(config):
        # The blank field is absent at the parser boundary, not filtered later.
        assert field not in config
        return _prepare_vertex_provider_config(config)

    fields = {field: blank}
    if name == "vertex":
        fields.update(VERTEX_FIELDS)
    with (
        patch(
            "agentcc.views.gateway._prepare_vertex_provider_config", side_effect=prepare
        ) as preparer,
        patch.object(
            CredentialManager, "encrypt", wraps=CredentialManager.encrypt
        ) as encrypt,
        patch("agentcc.views.gateway.push_org_config") as push,
    ):
        response = _save(auth_client, name, api_format, fields, base_url)

    assert response.status_code == 200, response.json()
    assert _persisted(credential) == stored
    assert bytes(credential.encrypted_credentials) == ciphertext
    if name == "vertex":
        preparer.assert_called_once()
    else:
        preparer.assert_not_called()
    encrypt.assert_not_called()
    push.assert_not_called()
    gateway.set_org_config.assert_not_called()


@pytest.mark.parametrize("blank", BLANKS)
def test_vertex_blank_json_does_not_discard_metadata_update(
    auth_client, organization, gateway, blank
):
    stored = {"service_account_json": "-".join(("stored", "fixture"))}
    credential = _aligned_credential(
        organization, "vertex", "gemini", stored, VERTEX_URL
    )
    ciphertext = bytes(credential.encrypted_credentials)
    with patch("agentcc.views.gateway.push_org_config", return_value=True) as push:
        response = _save(
            auth_client,
            "vertex",
            "gemini",
            {
                **VERTEX_FIELDS,
                "service_account_json": blank,
                "models": ["updated-model"],
            },
            VERTEX_URL,
        )
    assert response.status_code == 200, response.json()
    assert _persisted(credential) == stored
    assert bytes(credential.encrypted_credentials) == ciphertext
    assert credential.models_list == ["updated-model"]
    push.assert_called_once()


@pytest.mark.parametrize("blank", BLANKS)
def test_vertex_blank_json_with_omitted_metadata_resets_and_pushes_once(
    auth_client, organization, gateway, blank
):
    stored = {"service_account_json": "-".join(("stored", "fixture"))}
    credential = _store(organization, "vertex", "gemini", stored, VERTEX_URL)
    # Deliberately not aligned to update_provider's full-replacement defaults.
    credential.models_list = ["stored-model"]
    credential.display_name = "Stored Vertex"
    credential.default_timeout_seconds = 37
    credential.max_concurrent = 7
    credential.conn_pool_size = 11
    credential.extra_config = {}
    credential.is_active = True
    credential.save()
    ciphertext = bytes(credential.encrypted_credentials)
    original_save = AgentccProviderCredential.save

    # Start the spies after setup and delegate to the real save so refresh_from_db
    # verifies the persisted replacement, not just assignments to a mocked row.
    with (
        patch.object(
            AgentccProviderCredential, "save", autospec=True, side_effect=original_save
        ) as save,
        patch.object(
            CredentialManager, "encrypt", wraps=CredentialManager.encrypt
        ) as encrypt,
        patch("agentcc.views.gateway.push_org_config", return_value=True) as push,
    ):
        response = _save(
            auth_client,
            "vertex",
            "gemini",
            {**VERTEX_FIELDS, "service_account_json": blank},
            VERTEX_URL,
        )

    assert response.status_code == 200, response.json()
    assert response.json()["result"]["action"] == "updated"
    assert response.json()["result"]["gateway_synced"] is True
    save.assert_called_once()
    assert save.call_args.args[0].pk == credential.pk
    assert _persisted(credential) == stored
    assert bytes(credential.encrypted_credentials) == ciphertext
    assert credential.models_list == []
    assert credential.display_name == "vertex"
    assert credential.default_timeout_seconds == 60
    assert credential.max_concurrent == 100
    assert credential.conn_pool_size == 100
    assert credential.base_url == VERTEX_URL
    assert credential.api_format == "gemini"
    assert credential.extra_config == {}
    assert credential.is_active is True
    encrypt.assert_not_called()
    push.assert_called_once()
    assert push.call_args.args[0] == str(organization.id)
    gateway.set_org_config.assert_not_called()


@pytest.mark.parametrize("blank", BLANKS)
def test_vertex_blank_json_with_identical_nondefault_metadata_is_unchanged(
    auth_client, organization, gateway, blank
):
    stored = {"service_account_json": "-".join(("stored", "fixture"))}
    credential = _store(organization, "vertex", "gemini", stored, VERTEX_URL)
    credential.models_list = ["stored-model"]
    credential.display_name = "Stored Vertex"
    credential.default_timeout_seconds = 37
    credential.max_concurrent = 7
    credential.conn_pool_size = 11
    credential.extra_config = {}
    credential.is_active = True
    credential.save()
    ciphertext = bytes(credential.encrypted_credentials)
    updated_at = credential.updated_at
    metadata = {
        field: getattr(credential, field)
        for field in (
            "base_url",
            "api_format",
            "models_list",
            "display_name",
            "default_timeout_seconds",
            "max_concurrent",
            "conn_pool_size",
            "extra_config",
            "is_active",
        )
    }
    original_save = AgentccProviderCredential.save

    with (
        patch.object(
            AgentccProviderCredential, "save", autospec=True, side_effect=original_save
        ) as save,
        patch.object(
            CredentialManager, "encrypt", wraps=CredentialManager.encrypt
        ) as encrypt,
        patch("agentcc.views.gateway.push_org_config", return_value=True) as push,
    ):
        response = _save(
            auth_client,
            "vertex",
            metadata["api_format"],
            {
                **VERTEX_FIELDS,
                "service_account_json": blank,
                "models": metadata["models_list"],
                "display_name": metadata["display_name"],
                "default_timeout_seconds": metadata["default_timeout_seconds"],
                "max_concurrent": metadata["max_concurrent"],
                "conn_pool_size": metadata["conn_pool_size"],
                # Extra config is flat on the wire; is_active is always True in
                # the replacement defaults, not a request-config field.
                **metadata["extra_config"],
            },
            metadata["base_url"],
        )

    assert response.status_code == 200, response.json()
    assert response.json()["result"]["action"] == "unchanged"
    assert response.json()["result"]["gateway_synced"] is False
    save.assert_not_called()
    assert _persisted(credential) == stored
    assert bytes(credential.encrypted_credentials) == ciphertext
    assert credential.updated_at == updated_at
    assert {field: getattr(credential, field) for field in metadata} == metadata
    encrypt.assert_not_called()
    push.assert_not_called()
    gateway.set_org_config.assert_not_called()
