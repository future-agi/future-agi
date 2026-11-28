"""The retired support endpoints are no longer routed.

The shared-key support routes under ``/accounts/appsmith/`` and the cache
maintenance route ``/accounts/redis-key/`` were removed. These tests pin that:
the paths no longer resolve, and requests to them get 404 whether anonymous,
signed in, or carrying the old ``X-API-KEY`` header with a matching server key.
"""

import pytest
from django.urls import Resolver404, resolve
from rest_framework import status

ZERO_UUID = "00000000-0000-4000-8000-000000000456"

REMOVED_ROUTES = [
    ("get", "/accounts/appsmith/users/"),
    ("post", "/accounts/appsmith/users/"),
    ("patch", "/accounts/appsmith/users/"),
    ("post", "/accounts/appsmith/users/login"),
    ("get", f"/accounts/appsmith/users/{ZERO_UUID}/"),
    ("post", f"/accounts/appsmith/users/{ZERO_UUID}/"),
    ("patch", f"/accounts/appsmith/users/{ZERO_UUID}/"),
    ("post", "/accounts/redis-key/"),
    ("delete", "/accounts/redis-key/"),
]

REMOVED_PATHS = sorted({path for _, path in REMOVED_ROUTES})


@pytest.mark.parametrize("path", REMOVED_PATHS)
def test_removed_support_path_does_not_resolve(path):
    with pytest.raises(Resolver404):
        resolve(path)


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
@pytest.mark.parametrize("method,path", REMOVED_ROUTES)
def test_removed_support_route_returns_404_with_legacy_key(
    api_client, method, path, monkeypatch
):
    monkeypatch.setenv("API_KEY", "synthetic-support-key")

    response = getattr(api_client, method)(
        path, {}, format="json", HTTP_X_API_KEY="synthetic-support-key"
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
@pytest.mark.parametrize("method,path", REMOVED_ROUTES)
def test_removed_support_route_returns_404_when_signed_in(auth_client, method, path):
    response = getattr(auth_client, method)(path, {}, format="json")

    assert response.status_code == status.HTTP_404_NOT_FOUND
