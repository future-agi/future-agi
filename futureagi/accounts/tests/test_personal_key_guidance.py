"""Retirement responses direct people to the browser key-management page."""

import uuid

import pytest
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.authentication import APIKeyAuthentication
from accounts.models import Organization, OrgApiKey, User
from accounts.views.keys import GetKeysView

pytestmark = pytest.mark.django_db


def test_retirement_guidance_uses_the_keys_page():
    org = Organization.objects.create(name="key-guidance")
    user = User.objects.create(
        email=f"{uuid.uuid4().hex}@example.invalid", organization=org
    )
    request = APIRequestFactory().get("/accounts/keys/")
    request.organization = org
    force_authenticate(request, user=user)
    response = GetKeysView.as_view()(request)
    assert response.status_code == 410
    assert response.data == {
        "code": "org_keys_retired",
        "create_url": "/dashboard/keys",
    }
    assert not OrgApiKey.no_workspace_objects.filter(organization=org).exists()

    key = OrgApiKey.no_workspace_objects.create(organization=org, type="system")
    request = APIRequestFactory().get(
        "/", HTTP_X_API_KEY=key.api_key, HTTP_X_SECRET_KEY=key.secret_key
    )
    with pytest.raises(AuthenticationFailed) as caught:
        APIKeyAuthentication().authenticate(request)
    assert caught.value.get_codes() == "api_key_retired"
    assert caught.value.create_url == "/dashboard/keys"
