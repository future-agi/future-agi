"""An expired person key at the MCP tool-call route, which uses the project's
default authenticators, answers with the distinct api_key_expired refusal."""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.tests.personal_keys import personal_key_for

pytestmark = pytest.mark.django_db

TOOL_CALL = "/mcp/internal/tool-call/"


@pytest.mark.parametrize(
    "enabled", [True, False], ids=["still-enabled", "disabled-by-expiry"]
)
def test_expired_personal_key_on_tool_call_answers_api_key_expired(
    user, workspace, enabled
):
    key = personal_key_for(
        user,
        user.organization,
        workspace=workspace,
        name=f"tool-call-expiry-{uuid4().hex[:8]}",
        api_key=f"tool-call-expiry-{uuid4().hex}",
        secret_key=uuid4().hex,
        enabled=enabled,
        expires_at=timezone.now() - timedelta(minutes=1),
    )
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=key.api_key, HTTP_X_SECRET_KEY=key.secret_key)

    response = client.post(
        TOOL_CALL, {"name": "whoami", "arguments": {}}, format="json"
    )

    assert response.status_code == 401
    assert "api_key_expired" in response.content.decode()
