"""Bulk API-key sync endpoint before the database is migrated.

A new install's database has no gateway key table until the bootstrap job has
migrated it. With bundled datastores the Helm chart runs that job as a
post-install hook, which `helm install --wait` starts only once every workload
reports ready, the gateway included; and the gateway reports ready only once
it knows the app's keys. The endpoint therefore says, in a form the gateway
acts on, that the app has no key table yet (503, code migrations_pending):
it has made no key. agentcc-gateway's internal/auth/sync.go reads the answer.
"""

import secrets
from unittest.mock import patch

import pytest
from django.db import connection
from rest_framework import status
from rest_framework.test import APIClient

from agentcc.models import AgentccAPIKey

# The code both sides pin: agentcc-gateway internal/auth/sync.go
# MigrationsPendingCode, and agentcc/views/api_key_bulk.py.
MIGRATIONS_PENDING_CODE = "migrations_pending"

ADMIN_TOKEN = secrets.token_hex(32)


@pytest.fixture(autouse=True)
def set_agentcc_admin_token():
    with patch("agentcc.permissions.AGENTCC_ADMIN_TOKEN", ADMIN_TOKEN):
        yield


@pytest.fixture
def admin_client():
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {ADMIN_TOKEN}")
    return client


@pytest.fixture
def no_key_table():
    """The database as it is before the bootstrap job: no agentcc_api_key."""
    real = connection.introspection.table_names

    def without_key_table(*args, **kwargs):
        return [
            name
            for name in real(*args, **kwargs)
            if name != AgentccAPIKey._meta.db_table
        ]

    with patch.object(
        connection.introspection, "table_names", side_effect=without_key_table
    ):
        yield


@pytest.mark.django_db
class TestAPIKeyBulkBeforeMigrations:
    def test_no_key_table_answers_503_migrations_pending(
        self, admin_client, no_key_table
    ):
        with patch(
            "agentcc.views.api_key_bulk.gateway_loadable_keys",
            side_effect=AssertionError("queried a table that does not exist"),
        ):
            response = admin_client.get("/agentcc/api-keys/bulk/")

        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        body = response.json()
        assert body["status"] is False
        assert body["code"] == MIGRATIONS_PENDING_CODE
        # The gateway reads the code from at most the first 1 KiB of the body.
        assert len(response.content) < 1024
        assert "relation" not in response.content.decode()

    def test_without_the_admin_token_the_state_is_not_disclosed(self, no_key_table):
        response = APIClient().get("/agentcc/api-keys/bulk/")
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )
        assert MIGRATIONS_PENDING_CODE not in response.content.decode()

    def test_a_migrated_database_lists_its_keys(self, admin_client):
        response = admin_client.get("/agentcc/api-keys/bulk/")
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["status"] is True

    def test_a_failing_query_on_a_migrated_database_is_not_migrations_pending(
        self, admin_client
    ):
        # Any other failure stays a plain error: the gateway must not read it
        # as "the app has no keys".
        with patch(
            "agentcc.views.api_key_bulk.gateway_loadable_keys",
            side_effect=RuntimeError("boom"),
        ):
            response = admin_client.get("/agentcc/api-keys/bulk/")
        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert MIGRATIONS_PENDING_CODE not in response.content.decode()
