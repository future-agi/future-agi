from unittest.mock import MagicMock, patch

import pytest

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from conftest import WorkspaceAwareAPIClient
from integrations.services.credentials import CredentialManager
from agentcc.models.provider_credential import AgentccProviderCredential
from agentcc.views.provider_credential import _join_api_endpoint
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles


@pytest.fixture
def secondary_org_context(user):
    org_b = Organization.objects.create(name="Second Organization")
    membership = OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=org_b,
        role=OrganizationRoles.OWNER,
        level=Level.OWNER,
        is_active=True,
    )
    workspace_b = Workspace.objects.create(
        name="Second Workspace",
        organization=org_b,
        is_default=True,
        is_active=True,
        created_by=user,
    )
    WorkspaceMembership.objects.create(
        workspace=workspace_b,
        user=user,
        role=OrganizationRoles.WORKSPACE_ADMIN,
        level=Level.WORKSPACE_ADMIN,
        organization_membership=membership,
        is_active=True,
    )
    return org_b, workspace_b


@pytest.fixture
def secondary_org_client(user, secondary_org_context):
    _, workspace_b = secondary_org_context
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=user)
    client.set_workspace(workspace_b)
    yield client
    client.stop_workspace_injection()


@pytest.mark.integration
@pytest.mark.api
class TestAgentccProviderCredentialOrganizationIsolation:
    def test_list_only_returns_active_request_organization_credentials(
        self, user, secondary_org_context, secondary_org_client
    ):
        org_b, _ = secondary_org_context
        AgentccProviderCredential.no_workspace_objects.create(
            organization=user.organization,
            provider_name="openai",
            display_name="Org A OpenAI",
            encrypted_credentials=CredentialManager.encrypt({"api_key": "sk-org-a"}),
            api_format="openai",
        )
        AgentccProviderCredential.no_workspace_objects.create(
            organization=org_b,
            provider_name="anthropic",
            display_name="Org B Anthropic",
            encrypted_credentials=CredentialManager.encrypt({"api_key": "sk-org-b"}),
            api_format="anthropic",
        )

        response = secondary_org_client.get("/agentcc/provider-credentials/")

        assert response.status_code == 200, response.json()
        result = response.json()["result"]
        if isinstance(result, dict) and "results" in result:
            result = result["results"]
        names = {item["provider_name"] for item in result}
        assert names == {"anthropic"}

    def test_create_uses_active_request_organization(
        self, user, secondary_org_context, secondary_org_client
    ):
        org_b, _ = secondary_org_context

        with patch(
            "agentcc.views.provider_credential.AgentccProviderCredentialViewSet._push_config_to_gateway",
            return_value=True,
        ):
            response = secondary_org_client.post(
                "/agentcc/provider-credentials/",
                {
                    "provider_name": "openai",
                    "display_name": "Org B OpenAI",
                    "credentials": {"api_key": "sk-org-b"},
                    "api_format": "openai",
                },
                format="json",
            )

        assert response.status_code == 201, response.json()

        credential = AgentccProviderCredential.no_workspace_objects.get(
            provider_name="openai", deleted=False
        )
        assert credential.organization_id == org_b.id
        assert credential.organization_id != user.organization_id

    def test_fetch_models_reads_credential_from_active_request_organization(
        self, user, secondary_org_context, secondary_org_client
    ):
        org_b, _ = secondary_org_context
        AgentccProviderCredential.no_workspace_objects.create(
            organization=user.organization,
            provider_name="openai",
            display_name="Org A OpenAI",
            encrypted_credentials=CredentialManager.encrypt({"api_key": "sk-org-a"}),
            api_format="openai",
        )
        AgentccProviderCredential.no_workspace_objects.create(
            organization=org_b,
            provider_name="openai",
            display_name="Org B OpenAI",
            encrypted_credentials=CredentialManager.encrypt({"api_key": "sk-org-b"}),
            api_format="openai",
        )

        with patch(
            "agentcc.views.provider_credential.AgentccProviderCredentialViewSet._fetch_models_from_provider",
            return_value=["gpt-4o"],
        ) as mock_fetch:
            response = secondary_org_client.post(
                "/agentcc/provider-credentials/fetch_models/",
                {"provider_name": "openai"},
                format="json",
            )

        assert response.status_code == 200, response.json()
        args, _ = mock_fetch.call_args
        # Signature: (provider_name, base_url, api_key, api_format, api_path_prefix)
        assert args[0] == "openai"
        assert args[2] == "sk-org-b"

    def test_fetch_models_returns_bad_request_when_saved_credential_cannot_decrypt(
        self, secondary_org_context, secondary_org_client
    ):
        org_b, _ = secondary_org_context
        AgentccProviderCredential.no_workspace_objects.create(
            organization=org_b,
            provider_name="openai",
            display_name="Org B OpenAI",
            encrypted_credentials=b"invalid-ciphertext",
            api_format="openai",
        )

        with (
            patch(
                "agentcc.views.provider_credential.CredentialManager.decrypt",
                side_effect=ValueError("decrypt failed"),
            ),
            patch(
                "agentcc.views.provider_credential.AgentccProviderCredentialViewSet._fetch_models_from_provider",
            ) as mock_fetch,
        ):
            response = secondary_org_client.post(
                "/agentcc/provider-credentials/fetch_models/",
                {"provider_name": "openai"},
                format="json",
            )

        assert response.status_code == 400, response.json()
        assert "could not be decrypted" in response.json()["message"]
        mock_fetch.assert_not_called()

    def test_retrieve_returns_metadata_without_decrypted_credentials(
        self, secondary_org_context, secondary_org_client
    ):
        org_b, _ = secondary_org_context
        cred = AgentccProviderCredential.no_workspace_objects.create(
            organization=org_b,
            provider_name="openai",
            display_name="Org B OpenAI",
            encrypted_credentials=CredentialManager.encrypt({"api_key": "sk-org-b"}),
            api_format="openai",
        )

        response = secondary_org_client.get(
            f"/agentcc/provider-credentials/{cred.id}/"
        )

        assert response.status_code == 200, response.json()
        data = response.json()["result"]
        assert data["provider_name"] == "openai"
        assert data["display_name"] == "Org B OpenAI"
        # Encrypted-credential bytes must not appear in the response.
        assert "encrypted_credentials" not in data
        # Credentials field is masked (present but value redacted); the
        # plaintext api_key must never appear on the wire.
        creds = data.get("credentials") or {}
        assert creds.get("api_key") in (None, "****", "")
        assert "sk-org-b" not in str(data)

    def test_retrieve_cross_tenant_returns_404(
        self, user, secondary_org_context, secondary_org_client
    ):
        # Credential belongs to org A; secondary_org_client is scoped to org B.
        cred = AgentccProviderCredential.no_workspace_objects.create(
            organization=user.organization,
            provider_name="openai",
            display_name="Org A OpenAI",
            encrypted_credentials=CredentialManager.encrypt({"api_key": "sk-org-a"}),
            api_format="openai",
        )

        response = secondary_org_client.get(
            f"/agentcc/provider-credentials/{cred.id}/"
        )
        assert response.status_code == 404

    def test_update_writes_safe_fields_and_pushes_config(
        self, secondary_org_context, secondary_org_client
    ):
        org_b, _ = secondary_org_context
        cred = AgentccProviderCredential.no_workspace_objects.create(
            organization=org_b,
            provider_name="openai",
            display_name="Old Display",
            encrypted_credentials=CredentialManager.encrypt({"api_key": "sk-untouched"}),
            api_format="openai",
            models_list=["gpt-4o-mini"],
        )

        with patch(
            "agentcc.views.provider_credential.AgentccProviderCredentialViewSet._push_config_to_gateway",
            return_value=True,
        ) as mock_push:
            response = secondary_org_client.put(
                f"/agentcc/provider-credentials/{cred.id}/",
                {
                    "provider_name": "openai",
                    "display_name": "New Display",
                    "models_list": ["gpt-4o"],
                    "api_format": "openai",
                },
                format="json",
            )

        assert response.status_code == 200, response.json()
        # PUT is handled as PATCH, so it is wrapped by _gm.success_response.
        data = response.json()["result"]
        assert data["display_name"] == "New Display"

        cred.refresh_from_db()
        assert cred.display_name == "New Display"
        assert cred.models_list == ["gpt-4o"]
        # PUT goes through partial_update, so it pushes to the gateway too.
        assert mock_push.call_count == 1

    def test_patch_updates_single_field_leaving_others_intact(
        self, secondary_org_context, secondary_org_client
    ):
        org_b, _ = secondary_org_context
        cred = AgentccProviderCredential.no_workspace_objects.create(
            organization=org_b,
            provider_name="openai",
            display_name="Original Display",
            encrypted_credentials=CredentialManager.encrypt({"api_key": "sk-org-b"}),
            api_format="openai",
            default_timeout_seconds=30,
            max_concurrent=5,
        )

        with patch(
            "agentcc.views.provider_credential.AgentccProviderCredentialViewSet._push_config_to_gateway",
            return_value=True,
        ):
            response = secondary_org_client.patch(
                f"/agentcc/provider-credentials/{cred.id}/",
                {"default_timeout_seconds": 45},
                format="json",
            )

        assert response.status_code == 200, response.json()
        cred.refresh_from_db()
        assert cred.default_timeout_seconds == 45
        assert cred.max_concurrent == 5  # untouched
        assert cred.display_name == "Original Display"  # untouched

    def test_destroy_soft_deletes_and_pushes_config(
        self, secondary_org_context, secondary_org_client
    ):
        org_b, _ = secondary_org_context
        cred = AgentccProviderCredential.no_workspace_objects.create(
            organization=org_b,
            provider_name="anthropic",
            display_name="To Delete",
            encrypted_credentials=CredentialManager.encrypt({"api_key": "sk-x"}),
            api_format="anthropic",
        )

        with patch(
            "agentcc.views.provider_credential.AgentccProviderCredentialViewSet._push_config_to_gateway",
            return_value=True,
        ) as mock_push:
            response = secondary_org_client.delete(
                f"/agentcc/provider-credentials/{cred.id}/"
            )

        assert response.status_code == 200, response.json()
        assert response.json()["result"]["deleted"] is True
        assert response.json()["result"]["gateway_synced"] is True
        mock_push.assert_called_once()

        cred.refresh_from_db()
        # Soft-delete, not hard-delete.
        assert cred.deleted is True
        assert cred.deleted_at is not None
        # List should now exclude it.
        list_response = secondary_org_client.get("/agentcc/provider-credentials/")
        list_ids = {item["id"] for item in list_response.json()["result"]}
        assert str(cred.id) not in list_ids

    def test_list_unauthenticated(self, api_client, db):
        response = api_client.get("/agentcc/provider-credentials/")
        assert response.status_code in (401, 403)

    def test_create_encrypts_credentials_before_persisting(
        self, secondary_org_context, secondary_org_client
    ):
        org_b, _ = secondary_org_context
        raw_api_key = "sk-plaintext-must-not-appear-at-rest"

        with patch(
            "agentcc.views.provider_credential.AgentccProviderCredentialViewSet._push_config_to_gateway",
            return_value=True,
        ):
            response = secondary_org_client.post(
                "/agentcc/provider-credentials/",
                {
                    "provider_name": "openai",
                    "display_name": "Org B OpenAI",
                    "credentials": {"api_key": raw_api_key},
                    "api_format": "openai",
                },
                format="json",
            )
        assert response.status_code == 201, response.json()

        cred = AgentccProviderCredential.no_workspace_objects.get(
            provider_name="openai", organization=org_b, deleted=False
        )
        # The raw api_key must not sit in the DB in plaintext, either as
        # the encrypted-credentials bytes or anywhere else.
        assert raw_api_key.encode() not in cred.encrypted_credentials
        # But CredentialManager.decrypt should yield the original.
        assert CredentialManager.decrypt(cred.encrypted_credentials) == {
            "api_key": raw_api_key
        }

    def test_api_path_prefix_round_trips_through_credential_api(
        self, secondary_org_context, secondary_org_client
    ):
        org_b, _ = secondary_org_context
        with patch(
            "agentcc.views.provider_credential.AgentccProviderCredentialViewSet._push_config_to_gateway",
            return_value=True,
        ):
            response = secondary_org_client.post(
                "/agentcc/provider-credentials/",
                {
                    "provider_name": "perplexity",
                    "credentials": {"api_key": "sk-perplexity"},
                    "api_format": "openai",
                    "extra_config": {"api_path_prefix": ""},
                },
                format="json",
            )

        assert response.status_code == 201, response.json()
        credential = AgentccProviderCredential.no_workspace_objects.get(
            organization=org_b, provider_name="perplexity", deleted=False
        )
        assert credential.extra_config == {"api_path_prefix": ""}

        read_response = secondary_org_client.get(
            f"/agentcc/provider-credentials/{credential.id}/"
        )
        assert read_response.status_code == 200, read_response.json()
        assert read_response.json()["result"]["extra_config"] == {
            "api_path_prefix": ""
        }

        with patch(
            "agentcc.views.provider_credential.AgentccProviderCredentialViewSet._push_config_to_gateway",
            return_value=True,
        ):
            update_response = secondary_org_client.patch(
                f"/agentcc/provider-credentials/{credential.id}/",
                {"extra_config": {"api_path_prefix": "/v1"}},
                format="json",
            )

        assert update_response.status_code == 200, update_response.json()
        credential.refresh_from_db()
        assert credential.extra_config == {"api_path_prefix": "/v1"}


@pytest.mark.parametrize(
    "base_url,prefix,expected",
    [
        # No prefix stated: the default applies, exactly as the gateway does.
        ("https://provider.example", None, "https://provider.example/v1/models"),
        # The case this PR exists for.
        (
            "https://provider.example",
            "/openai/v1",
            "https://provider.example/openai/v1/models",
        ),
        # Explicitly empty means no version segment at all.
        ("https://provider.example", "", "https://provider.example/models"),
        # A base_url that already carries the prefix must not repeat it.
        ("https://api.openai.com/v1", "/v1", "https://api.openai.com/v1/models"),
        ("https://api.openai.com/v1", None, "https://api.openai.com/v1/models"),
        # Normalisation: missing leading slash, trailing slash, whitespace.
        (
            "https://provider.example/",
            "openai/v1/",
            "https://provider.example/openai/v1/models",
        ),
        ("https://provider.example", "  /v2  ", "https://provider.example/v2/models"),
    ],
)
def test_join_api_endpoint_matches_the_gateway(base_url, prefix, expected):
    assert _join_api_endpoint(base_url, prefix, "/models") == expected


@pytest.mark.integration
@pytest.mark.api
class TestFetchModelsHonoursThePathPrefix:
    """Discovery has to probe the same versioned path the proxy will use."""

    def _credential(self, org, extra_config):
        return AgentccProviderCredential.no_workspace_objects.create(
            organization=org,
            provider_name="perplexity",
            display_name="Perplexity",
            encrypted_credentials=CredentialManager.encrypt({"api_key": "sk-pplx"}),
            api_format="openai",
            base_url="https://provider.example",
            extra_config=extra_config,
        )

    def test_saved_credential_prefix_reaches_the_fetch(
        self, secondary_org_context, secondary_org_client
    ):
        org_b, _ = secondary_org_context
        self._credential(org_b, {"api_path_prefix": "/openai/v1"})

        with patch(
            "agentcc.views.provider_credential.AgentccProviderCredentialViewSet._fetch_models_from_provider",
            return_value=["sonar"],
        ) as mock_fetch:
            response = secondary_org_client.post(
                "/agentcc/provider-credentials/fetch_models/",
                {"provider_name": "perplexity"},
                format="json",
            )

        assert response.status_code == 200, response.json()
        args, _ = mock_fetch.call_args
        assert args[4] == "/openai/v1"

    def test_body_prefix_overrides_the_saved_one_including_an_empty_string(
        self, secondary_org_context, secondary_org_client
    ):
        org_b, _ = secondary_org_context
        self._credential(org_b, {"api_path_prefix": "/v1"})

        with patch(
            "agentcc.views.provider_credential.AgentccProviderCredentialViewSet._fetch_models_from_provider",
            return_value=[],
        ) as mock_fetch:
            response = secondary_org_client.post(
                "/agentcc/provider-credentials/fetch_models/",
                {"provider_name": "perplexity", "api_path_prefix": ""},
                format="json",
            )

        assert response.status_code == 200, response.json()
        args, _ = mock_fetch.call_args
        # "" is the value the user typed, not a missing field to fill from the DB.
        assert args[4] == ""

    def test_a_credential_saved_before_the_field_existed_still_gets_the_default(
        self, secondary_org_context, secondary_org_client
    ):
        org_b, _ = secondary_org_context
        self._credential(org_b, {})

        with patch(
            "agentcc.views.provider_credential.AgentccProviderCredentialViewSet._fetch_models_from_provider",
            return_value=[],
        ) as mock_fetch:
            response = secondary_org_client.post(
                "/agentcc/provider-credentials/fetch_models/",
                {"provider_name": "perplexity"},
                format="json",
            )

        assert response.status_code == 200, response.json()
        args, _ = mock_fetch.call_args
        assert args[4] is None

    def test_the_prefix_lands_in_the_url_discovery_actually_requests(
        self, secondary_org_context, secondary_org_client
    ):
        org_b, _ = secondary_org_context
        self._credential(org_b, {"api_path_prefix": "/openai/v1"})

        session = MagicMock()
        session.get.return_value.json.return_value = {"data": [{"id": "sonar"}]}

        with (
            patch("agentcc.views.provider_credential.ensure_public_http_url"),
            patch(
                "agentcc.views.provider_credential.build_ssrf_safe_session",
                return_value=session,
            ),
        ):
            response = secondary_org_client.post(
                "/agentcc/provider-credentials/fetch_models/",
                {"provider_name": "perplexity"},
                format="json",
            )

        assert response.status_code == 200, response.json()
        assert response.json()["result"]["models"] == ["sonar"]
        called_url = session.get.call_args[0][0]
        assert called_url == "https://provider.example/openai/v1/models"


@pytest.mark.integration
@pytest.mark.api
class TestFetchModelsPrivateProviderURLs:
    """Model discovery for local providers (Ollama, vLLM, a Docker service)."""

    def _fetch(self, client, base_url):
        return client.post(
            "/agentcc/provider-credentials/fetch_models/",
            {"base_url": base_url, "api_key": "sk-local", "api_format": "openai"},
            format="json",
        )

    @pytest.fixture(autouse=True)
    def dns(self, provider_dns):
        return provider_dns({"mock-llm": ["172.20.0.5"]}, passthrough=True)

    def test_private_base_url_is_refused_with_the_opt_in_to_set(
        self, monkeypatch, secondary_org_client
    ):
        monkeypatch.delenv("AGENTCC_ALLOW_PRIVATE_PROVIDER_URLS", raising=False)
        response = self._fetch(secondary_org_client, "http://mock-llm:8080")

        assert response.status_code == 400
        assert "AGENTCC_ALLOW_PRIVATE_PROVIDER_URLS=true" in str(response.json())

    def test_private_base_url_is_fetched_with_the_opt_in(
        self, monkeypatch, secondary_org_client
    ):
        monkeypatch.setenv("AGENTCC_ALLOW_PRIVATE_PROVIDER_URLS", "true")
        session = MagicMock()
        session.get.return_value.json.return_value = {"data": [{"id": "mock-custom"}]}

        with patch(
            "agentcc.views.provider_credential.build_ssrf_safe_session",
            return_value=session,
        ) as build_session:
            response = self._fetch(secondary_org_client, "http://mock-llm:8080")

        assert response.status_code == 200, response.json()
        assert response.json()["result"]["models"] == ["mock-custom"]
        assert build_session.call_args.kwargs["allow_private"] is True
        assert session.get.call_args[0][0] == "http://mock-llm:8080/v1/models"

    def test_metadata_address_is_refused_even_with_the_opt_in(
        self, monkeypatch, secondary_org_client
    ):
        monkeypatch.setenv("AGENTCC_ALLOW_PRIVATE_PROVIDER_URLS", "true")
        response = self._fetch(secondary_org_client, "http://169.254.169.254")

        assert response.status_code == 400
        assert "never allowed" in str(response.json())


@pytest.mark.integration
@pytest.mark.api
class TestFetchModelsKeepsTheSavedKeyOnItsBaseURL:
    """A caller who can use a saved credential cannot point its key elsewhere."""

    def test_saved_key_is_not_sent_to_a_request_supplied_base_url(
        self, secondary_org_context, secondary_org_client
    ):
        org_b, _ = secondary_org_context
        AgentccProviderCredential.no_workspace_objects.create(
            organization=org_b,
            provider_name="openai",
            display_name="OpenAI",
            encrypted_credentials=CredentialManager.encrypt({"api_key": "sk-saved"}),
            api_format="openai",
            base_url="https://api.openai.com/v1",
        )

        with patch(
            "agentcc.views.provider_credential.AgentccProviderCredentialViewSet._fetch_models_from_provider",
            return_value=[],
        ) as mock_fetch:
            response = secondary_org_client.post(
                "/agentcc/provider-credentials/fetch_models/",
                {"provider_name": "openai", "base_url": "https://attacker.example"},
                format="json",
            )
            assert response.status_code == 400
            mock_fetch.assert_not_called()

            # The saved base URL itself, or a key of the caller's own, is fine.
            response = secondary_org_client.post(
                "/agentcc/provider-credentials/fetch_models/",
                {"provider_name": "openai", "base_url": "https://api.openai.com/v1/"},
                format="json",
            )
            assert response.status_code == 200, response.json()
            response = secondary_org_client.post(
                "/agentcc/provider-credentials/fetch_models/",
                {
                    "provider_name": "openai",
                    "base_url": "https://other.example",
                    "api_key": "sk-typed",
                },
                format="json",
            )
            assert response.status_code == 200, response.json()
            assert mock_fetch.call_args[0][2] == "sk-typed"


@pytest.mark.integration
@pytest.mark.api
class TestProviderBaseURLIsCheckedOnSave:
    """A base URL the gateway refuses is refused when the provider is saved,
    rather than saved and then answered with an error on every request."""

    OPT_IN = "AGENTCC_ALLOW_PRIVATE_PROVIDER_URLS"

    @pytest.fixture(autouse=True)
    def dns(self, provider_dns):
        return provider_dns(
            {
                "mock-llm": ["172.20.0.5"],
                "host.docker.internal": ["192.168.65.254"],
                "api.openai.com": ["104.18.6.192"],
            },
            passthrough=True,
        )

    @pytest.fixture(autouse=True)
    def _no_gateway_push(self):
        with (
            patch(
                "agentcc.views.provider_credential.AgentccProviderCredentialViewSet._push_config_to_gateway",
                return_value=True,
            ),
            patch("agentcc.views.gateway.push_org_config", return_value=True),
        ):
            yield

    def _create(self, client, base_url, name="custom"):
        return client.post(
            "/agentcc/provider-credentials/",
            {
                "provider_name": name,
                "credentials": {"api_key": "sk-local"},
                "base_url": base_url,
                "api_format": "openai",
            },
            format="json",
        )

    def _update_provider(self, client, base_url, name="custom", **config):
        return client.post(
            "/agentcc/gateways/default/update-provider/",
            {
                "name": name,
                "config": {
                    "api_key": "sk-local",
                    "api_format": "openai",
                    "models": ["mock-model"],
                    "base_url": base_url,
                    **config,
                },
            },
            format="json",
        )

    def _saved(self, org, name="custom"):
        return AgentccProviderCredential.no_workspace_objects.filter(
            organization=org, provider_name=name, deleted=False
        ).first()

    @pytest.mark.parametrize(
        "base_url",
        [
            "http://mock-llm:8080",
            "http://100.64.77.10:8080",  # RFC 6598 shared address space
            "http://10.0.0.12:11434",
            "http://host.docker.internal:11434",
        ],
    )
    def test_gateway_ui_refuses_a_private_base_url_without_the_opt_in(
        self, monkeypatch, secondary_org_context, secondary_org_client, base_url
    ):
        monkeypatch.delenv(self.OPT_IN, raising=False)
        org_b, _ = secondary_org_context

        response = self._update_provider(secondary_org_client, base_url)

        assert response.status_code == 400, response.json()
        assert f"{self.OPT_IN}=true" in response.json()["message"]
        assert self._saved(org_b) is None

    def test_gateway_ui_saves_a_private_base_url_with_the_opt_in(
        self, monkeypatch, secondary_org_context, secondary_org_client
    ):
        monkeypatch.setenv(self.OPT_IN, "true")
        org_b, _ = secondary_org_context

        response = self._update_provider(secondary_org_client, "http://mock-llm:8080")

        assert response.status_code == 200, response.json()
        assert self._saved(org_b).base_url == "http://mock-llm:8080"

    @pytest.mark.parametrize(
        "base_url",
        [
            "http://127.0.0.1:11434",
            "http://169.254.169.254/latest",
            "http://100.100.100.200",  # Alibaba metadata, inside 100.64.0.0/10
        ],
    )
    def test_gateway_ui_refuses_loopback_and_metadata_even_with_the_opt_in(
        self, monkeypatch, secondary_org_context, secondary_org_client, base_url
    ):
        monkeypatch.setenv(self.OPT_IN, "true")
        org_b, _ = secondary_org_context

        response = self._update_provider(secondary_org_client, base_url)

        assert response.status_code == 400, response.json()
        assert "never allowed" in response.json()["message"]
        assert self._saved(org_b) is None

    def test_gateway_ui_leaves_default_and_unresolvable_urls_to_the_gateway(
        self, monkeypatch, secondary_org_context, secondary_org_client, dns
    ):
        monkeypatch.delenv(self.OPT_IN, raising=False)
        org_b, _ = secondary_org_context

        # No base_url: the provider's default endpoint (config.yaml or built in).
        response = self._update_provider(secondary_org_client, "", name="openai")
        assert response.status_code == 200, response.json()
        dns.assert_not_called()

        # Not resolvable from the backend: the gateway checks it on each request.
        response = self._update_provider(
            secondary_org_client, "https://llm.corp.invalid/v1", name="corp"
        )
        assert response.status_code == 200, response.json()
        assert self._saved(org_b, "corp").base_url == "https://llm.corp.invalid/v1"

    def test_gateway_ui_can_still_edit_a_provider_saved_before_the_check(
        self, monkeypatch, secondary_org_context, secondary_org_client
    ):
        monkeypatch.delenv(self.OPT_IN, raising=False)
        org_b, _ = secondary_org_context
        AgentccProviderCredential.no_workspace_objects.create(
            organization=org_b,
            provider_name="custom",
            display_name="Local vLLM",
            encrypted_credentials=CredentialManager.encrypt({"api_key": "sk-local"}),
            api_format="openai",
            base_url="http://mock-llm:8080",
        )

        listed = secondary_org_client.get("/agentcc/provider-credentials/")
        assert listed.status_code == 200, listed.json()

        # The edit dialog sends the saved base URL back unchanged.
        response = self._update_provider(
            secondary_org_client, "http://mock-llm:8080/", models=["other-model"]
        )
        assert response.status_code == 200, response.json()
        assert self._saved(org_b).models_list == ["other-model"]

        # Pointing it at another private address is a new URL, and is checked.
        response = self._update_provider(secondary_org_client, "http://10.0.0.12:8080")
        assert response.status_code == 400, response.json()
        assert f"{self.OPT_IN}=true" in response.json()["message"]
        assert self._saved(org_b).base_url == "http://mock-llm:8080/"

    def test_credential_api_refuses_a_private_base_url_without_the_opt_in(
        self, monkeypatch, secondary_org_context, secondary_org_client
    ):
        monkeypatch.delenv(self.OPT_IN, raising=False)
        org_b, _ = secondary_org_context

        response = self._create(secondary_org_client, "http://100.64.77.10:8080")

        assert response.status_code == 400, response.json()
        assert f"{self.OPT_IN}=true" in response.json()["message"]
        assert self._saved(org_b) is None

        monkeypatch.setenv(self.OPT_IN, "true")
        response = self._create(secondary_org_client, "http://100.64.77.10:8080")
        assert response.status_code == 201, response.json()
        assert self._saved(org_b).base_url == "http://100.64.77.10:8080"

    def test_credential_api_leaves_a_single_label_host_to_the_opt_in(
        self, monkeypatch, secondary_org_context, secondary_org_client
    ):
        # A Docker service or Kubernetes Service short name has no dot, like
        # the gateway UI's http://mock-llm:8080. Whether it may be saved is the
        # opt-in's decision, not the URL format's.
        monkeypatch.delenv(self.OPT_IN, raising=False)
        org_b, _ = secondary_org_context

        response = self._create(secondary_org_client, "http://mock-llm:8080")

        assert response.status_code == 400, response.json()
        assert f"{self.OPT_IN}=true" in response.json()["message"]
        assert self._saved(org_b) is None

        monkeypatch.setenv(self.OPT_IN, "true")
        response = self._create(secondary_org_client, "http://mock-llm:8080")
        assert response.status_code == 201, response.json()
        cred = self._saved(org_b)
        assert cred.base_url == "http://mock-llm:8080"

        response = secondary_org_client.patch(
            f"/agentcc/provider-credentials/{cred.id}/",
            {"base_url": "http://mock-llm:11434/v1"},
            format="json",
        )
        assert response.status_code == 200, response.json()
        cred.refresh_from_db()
        assert cred.base_url == "http://mock-llm:11434/v1"

    @pytest.mark.parametrize(
        "base_url",
        [
            "mock-llm:8080",
            "ftp://mock-llm/v1",
            "http://:8080",
            "http://mock llm:8080",
            # The gateway's url.Parse refuses control characters, so a saved
            # one would fail every request through the provider.
            "http://mock\x01llm:8080",
            "\x01http://mock-llm:8080",
            "http://mock-llm:8080/v1\x7f",
            "http://mock-llm:80a",
            "http://mock-llm:65536",
        ],
    )
    def test_credential_api_still_refuses_what_is_not_an_http_url_with_a_host(
        self, monkeypatch, secondary_org_context, secondary_org_client, base_url
    ):
        monkeypatch.setenv(self.OPT_IN, "true")
        org_b, _ = secondary_org_context

        response = self._create(secondary_org_client, base_url)

        assert response.status_code == 400, response.json()
        assert "Enter a valid URL." in str(response.json())
        assert self._saved(org_b) is None

    def test_credential_api_checks_a_changed_base_url_only(
        self, monkeypatch, secondary_org_context, secondary_org_client
    ):
        monkeypatch.delenv(self.OPT_IN, raising=False)
        org_b, _ = secondary_org_context
        cred = AgentccProviderCredential.no_workspace_objects.create(
            organization=org_b,
            provider_name="custom",
            encrypted_credentials=CredentialManager.encrypt({"api_key": "sk-local"}),
            api_format="openai",
            base_url="http://10.0.0.12:8080",
        )
        url = f"/agentcc/provider-credentials/{cred.id}/"

        response = secondary_org_client.patch(
            url,
            {"display_name": "Renamed", "base_url": "http://10.0.0.12:8080"},
            format="json",
        )
        assert response.status_code == 200, response.json()

        response = secondary_org_client.patch(
            url, {"base_url": "http://100.64.77.10:8080"}, format="json"
        )
        assert response.status_code == 400, response.json()
        assert f"{self.OPT_IN}=true" in response.json()["message"]
        cred.refresh_from_db()
        assert cred.base_url == "http://10.0.0.12:8080"
        assert cred.display_name == "Renamed"

        response = secondary_org_client.patch(
            url, {"base_url": "https://api.openai.com/v1"}, format="json"
        )
        assert response.status_code == 200, response.json()
        cred.refresh_from_db()
        assert cred.base_url == "https://api.openai.com/v1"

    def test_credential_api_put_is_checked_like_patch(
        self, monkeypatch, secondary_org_context, secondary_org_client
    ):
        monkeypatch.delenv(self.OPT_IN, raising=False)
        org_b, _ = secondary_org_context
        cred = AgentccProviderCredential.no_workspace_objects.create(
            organization=org_b,
            provider_name="custom",
            display_name="Local vLLM",
            encrypted_credentials=CredentialManager.encrypt({"api_key": "sk-local"}),
            api_format="openai",
            base_url="https://api.openai.com/v1",
        )

        response = secondary_org_client.put(
            f"/agentcc/provider-credentials/{cred.id}/",
            {
                "provider_name": "custom",
                "display_name": "Renamed",
                "base_url": "http://100.64.77.10:8080",
            },
            format="json",
        )

        assert response.status_code == 400, response.json()
        assert f"{self.OPT_IN}=true" in response.json()["message"]
        cred.refresh_from_db()
        assert cred.base_url == "https://api.openai.com/v1"
        assert cred.display_name == "Local vLLM"
