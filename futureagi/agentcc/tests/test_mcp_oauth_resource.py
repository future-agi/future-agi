"""OAuth resource persistence; the tests need a database."""

from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.utils import timezone

from agentcc.models.mcp_credential import AgentccMCPCredential as Credential
from agentcc.services import mcp_upstream_oauth as connect
from agentcc.services import mcp_upstream_tokens as tokens
from agentcc.tests.mcp_upstream_oauth_helpers import ISSUER, SERVER_URL
from agentcc.tests.test_mcp_shared_oauth import (
    _settings, config, purges, pushes, vendor,
)
from agentcc.tests.test_mcp_walk_oauth import ask, connected

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("mode", ["user", "shared"])
@pytest.mark.parametrize("resource", [
    "",  # Explicitly no RFC 8707 resource, not a legacy unknown grant.
    "https://mcp.vendor.example/",  # Preserve the slash; do not normalize.
    SERVER_URL,
    SERVER_URL + "?audience=" + "a" * 600,
])
def test_reconnect_records_exact_resource_and_refresh_never_retargets(
    auth_client, organization, user, config, vendor, purges, pushes, mode, resource
):
    old = connected(auth_client, vendor, mode)
    assert old.oauth_resource == SERVER_URL
    # Model the discovery/declared-provider result used by both connect stores.
    found = replace(connect.discover(SERVER_URL), resource=resource)
    with patch.object(connect, "discover", return_value=found):
        row = connected(auth_client, vendor, mode)
    assert row.pk == old.pk  # The in-place reconnect updates the resource too.
    row.refresh_from_db()
    assert row.oauth_resource == resource
    # Two refreshes exercise rotated refresh tokens without changing the grant.
    for _ in range(2):
        row.token_expires_at = timezone.now() + timedelta(seconds=90)
        row.save(update_fields=["token_expires_at"])
        assert ask(organization, user, mode).outcome == "refreshed"
        form = vendor.refreshes[-1]
        if resource:
            assert form["resource"] == resource
        else:
            assert "resource" not in form
        row.refresh_from_db()
        assert row.oauth_resource == resource
        assert row.status == "active"


@pytest.mark.parametrize("mode", ["user", "shared"])
@pytest.mark.parametrize("declared", [False, True])
def test_legacy_unknown_resource_keeps_previous_refresh_behavior(
    auth_client, organization, user, config, vendor, purges, pushes, mode, declared
):
    row = connected(auth_client, vendor, mode)
    # Rows predating the additive migration, including old-code inserts, are NULL.
    Credential.objects.filter(pk=row.pk).update(oauth_resource=None)
    row.refresh_from_db()
    pilot = SimpleNamespace(token_url=ISSUER + "/token") if declared else None
    with patch.object(tokens.connector_k2, "pilot", return_value=pilot):
        result = tokens._refresh(row, SERVER_URL)
    assert result.tokens is not None
    form = vendor.refreshes[-1]
    if declared:
        assert "resource" not in form
    else:
        assert form["resource"] == SERVER_URL
    row.refresh_from_db()
    assert row.oauth_resource is None  # Refresh cannot infer the original grant.


def test_resource_column_distinguishes_unknown_from_explicitly_absent():
    field = Credential._meta.get_field("oauth_resource")
    assert field.null and field.blank
    assert field.get_default() is None
    assert field.db_default is None
    assert field.max_length == 2048
