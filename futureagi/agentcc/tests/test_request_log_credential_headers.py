"""The request log never stores a credential a caller sent in a header.

The gateway sends each request's headers to the log webhook. Ingestion keeps no
credential header's value, whatever the gateway sent, and the case table it shares
with the gateway (``api_contracts/gateway/credential-headers.json``) pins which
names those are.
"""

import json
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from rest_framework import status

from accounts.models import User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import WorkspaceMembership
from agentcc.models import AgentccRequestLog
from agentcc.services.credential_headers import (
    is_credential_header,
    looks_like_credential,
)
from agentcc.services.log_ingestion import _sanitize_headers
from conftest import WorkspaceAwareAPIClient
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

_CASES = json.loads(
    (
        Path(__file__).resolve().parents[3]
        / "api_contracts/gateway/credential-headers.json"
    ).read_text()
)

WEBHOOK = "/agentcc/webhook/logs/"
SECRET = "webhook-secret-test"
# Planted test values, not real credentials. GATEWAY_KEY has a gateway key's
# shape.
GATEWAY_KEY = "sk-agentcc-0f1e2d3c4b5a69788796a5b4c3d2e1f0"
PLANTED = {
    "Proxy-Authorization": "Basic cGxhbnRlZC1wcm94eS1jcmVkZW50aWFs",
    "X-Amz-Security-Token": "planted-amz-security-token-1f2e",
    "X-Vault-Token": "hvs.planted-vault-token-3d4c",
    "X-Secret-Key": "planted-secret-key-5b6a",
    "Ocp-Apim-Subscription-Key": "planted-apim-subscription-7f8e",
    "Lasso-Api-Key": "planted-lasso-key-9d0c",
    "Grayswan-Api-Key": "planted-grayswan-key-1b2a",
    "Apikey": "planted-apikey-3f4e",
    "X-Client-Secret": "planted-client-secret-7b8a",
    "Cookie": "session=planted-cookie-9f0e",
}
# The three headers a client sends its gateway key in.
KEY_FORMS = {
    "Authorization": f"Bearer {GATEWAY_KEY}",
    "X-Api-Key": GATEWAY_KEY,
    "X-Goog-Api-Key": GATEWAY_KEY,
}


def _viewer_client(organization, workspace):
    viewer = User.objects.create_user(
        email=f"viewer-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="Viewer",
        organization=organization,
        organization_role=OrganizationRoles.MEMBER_VIEW_ONLY,
    )
    membership = OrganizationMembership.no_workspace_objects.create(
        user=viewer,
        organization=organization,
        role=OrganizationRoles.MEMBER_VIEW_ONLY,
        level=Level.VIEWER,
        is_active=True,
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=viewer,
        workspace=workspace,
        role=OrganizationRoles.WORKSPACE_MEMBER,
        level=Level.WORKSPACE_MEMBER,
        organization_membership=membership,
        is_active=True,
    )
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=viewer)
    client.set_workspace(workspace)
    return client


def _ingest(api_client, organization, request_id, request_headers, **extra):
    entry = {
        "request_id": request_id,
        "model": "gemini-2.5-flash",
        "provider": "gemini",
        "status_code": 200,
        "latency_ms": 12,
        "request_headers": request_headers,
        "metadata": {"org_id": str(organization.id)},
        **extra,
    }
    with patch("agentcc.views.webhook.AGENTCC_WEBHOOK_SECRET", SECRET):
        response = api_client.post(
            WEBHOOK,
            {"gateway_id": "default", "logs": [entry]},
            format="json",
            HTTP_X_WEBHOOK_SECRET=SECRET,
        )
    assert response.status_code == status.HTTP_200_OK, response.content
    assert response.json()["result"]["ingested"] == 1
    return AgentccRequestLog.no_workspace_objects.get(request_id=request_id)


class TestSanitizeHeaders:
    def test_every_credential_header_is_masked_and_every_plain_one_kept(self):
        headers = {name: [f"planted-{name}"] for name in _CASES["credential"]}
        headers.update({name: [f"plain-{name}"] for name in _CASES["plain"]})

        got = _sanitize_headers(headers)

        assert {
            name: got[name] for name in _CASES["credential"] if got[name] != "***"
        } == {}
        assert {
            name: got[name]
            for name in _CASES["plain"]
            if got[name] != [f"plain-{name}"]
        } == {}

    @pytest.mark.parametrize(
        "headers",
        [
            [["X-Goog-Api-Key", GATEWAY_KEY]],
            f"X-Goog-Api-Key: {GATEWAY_KEY}",
        ],
        ids=["list-of-pairs", "string"],
    )
    def test_headers_that_are_not_an_object_are_not_stored(self, headers):
        # They cannot be checked name by name, so none of them is kept.
        assert _sanitize_headers(headers) is None

    def test_no_headers_stay_no_headers(self):
        assert _sanitize_headers(None) is None
        assert _sanitize_headers({}) == {}


class TestATokenIsReadInEveryWord:
    """ "token" names a token in any word of a name, not only in the last one. Only a
    count of tokens (token followed by a count word, a word ending in "tokens") and a
    tokenizer are left out. The gateway's copy (internal/credentials
    TestLooksLikeCredential) reads the same names."""

    @pytest.mark.parametrize(
        "name",
        [
            "X-Access-Token-V2",
            "access_token_2",
            "slack_token_prod",
            "github_token_1",
            "X-Refresh-Token-Encrypted",
            "service_token_b64",
            "id_token_hint",
            "X-Session-Token-Id",
            "my_auth_token_2",
            "x-token-value",
            "X-TokenValue",
            "x-tokenid",
            "csrftoken",
            "total_token",
            "token_type",
            "x-tokenizer-token",
        ],
    )
    def test_a_name_that_names_a_token_is_a_credential(self, name):
        assert looks_like_credential(name)
        assert is_credential_header(name)

    @pytest.mark.parametrize(
        "name",
        [
            "token_count",
            "token-budget",
            "X-Token-Limit",
            "token_usage",
            "token-used",
            "X-Token-Remaining",
            "token_total",
            "token-max",
            "token_min",
            "token-limit-remaining",
            "x-tokenizer",
            "tokenize",
            "detokenization_mode",
            "tokens_used",
            "prompt_tokens",
            "Tokenizer",
            "X-Token-Count",
            "token_budget",
        ],
    )
    def test_a_count_of_tokens_or_a_tokenizer_is_not(self, name):
        assert not looks_like_credential(name)
        assert not is_credential_header(name)


@pytest.mark.integration
@pytest.mark.api
class TestIngestionStoresNoCredential:
    @pytest.mark.parametrize("form", sorted(KEY_FORMS))
    def test_no_credential_reaches_the_row_or_a_viewer(
        self, api_client, organization, workspace, form
    ):
        # As the gateway sends them: each header's values as a list.
        sent = {name: [value] for name, value in PLANTED.items()}
        sent[form] = [KEY_FORMS[form]]
        sent["Content-Type"] = ["application/json"]
        sent["X-Goog-Api-Client"] = ["genai-js/1.0.0"]

        row = _ingest(
            api_client,
            organization,
            f"hdr-{form}",
            sent,
            response_headers={"Set-Cookie": ["session=planted-upstream-cookie-2c3d"]},
        )

        stored = json.dumps([row.request_headers, row.response_headers])
        assert GATEWAY_KEY not in stored
        assert [name for name, value in PLANTED.items() if value in stored] == []
        assert "planted-upstream-cookie-2c3d" not in stored
        assert row.request_headers[form] == "***"
        assert row.request_headers["X-Goog-Api-Client"] == ["genai-js/1.0.0"]
        assert row.request_headers["Content-Type"] == ["application/json"]

        detail = _viewer_client(organization, workspace).get(
            f"/agentcc/request-logs/{row.id}/"
        )

        assert detail.status_code == status.HTTP_200_OK, detail.content
        body = detail.content.decode()
        assert GATEWAY_KEY not in body
        assert [name for name, value in PLANTED.items() if value in body] == []


@pytest.mark.integration
@pytest.mark.api
class TestDetailShowsNoStoredCredential:
    def test_a_stored_credential_header_shows_a_viewer_no_credential(
        self, organization, workspace
    ):
        # The detail endpoint masks a credential header whatever the row holds.
        row = AgentccRequestLog.no_workspace_objects.create(
            organization=organization,
            request_id="hdr-stored",
            model="gemini-2.5-flash",
            request_headers={
                "X-Goog-Api-Key": [GATEWAY_KEY],
                "X-Amz-Security-Token": [PLANTED["X-Amz-Security-Token"]],
                "Content-Type": ["application/json"],
            },
            response_headers={"Set-Cookie": ["session=planted-upstream-cookie-2c3d"]},
        )

        detail = _viewer_client(organization, workspace).get(
            f"/agentcc/request-logs/{row.id}/"
        )

        assert detail.status_code == status.HTTP_200_OK, detail.content
        body = detail.content.decode()
        assert GATEWAY_KEY not in body
        assert PLANTED["X-Amz-Security-Token"] not in body
        assert "planted-upstream-cookie-2c3d" not in body
        result = detail.json()["result"]
        assert result["request_headers"] == {
            "X-Goog-Api-Key": "***",
            "X-Amz-Security-Token": "***",
            "Content-Type": ["application/json"],
        }
        assert result["response_headers"] == {"Set-Cookie": "***"}
