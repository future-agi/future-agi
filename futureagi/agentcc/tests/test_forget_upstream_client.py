"""forget_upstream_client: an operator forgets the client a vendor's
authorization server registered for us, so the next sign-in there registers
a new one.

The command deletes exactly the client stored for one issuer and one
redirect URI, prints its row id and the count (never the client id), and does
nothing when no client matches. The vendor is ``FakeVendor``, answered in
place of the guarded fetcher, so no test reaches a network.
"""

from io import StringIO
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from agentcc.models.mcp_upstream_oauth import AgentccMCPUpstreamClient
from agentcc.services import gateway_client
from agentcc.services import mcp_upstream_oauth as connect
from agentcc.tests.mcp_upstream_oauth_helpers import (
    ADMIN_TOKEN,
    CALLBACK,
    CONSOLE,
    INTERNAL_SECRET,
    ISSUER,
    SERVER_ID,
    START,
    FakeVendor,
    seed_config,
)

pytestmark = pytest.mark.django_db

COMMAND = "forget_upstream_client"
OTHER_ISSUER = "https://auth.other.example"
OTHER_REDIRECT = "https://api.other.test/agentcc/mcp/connect/callback/"


@pytest.fixture(autouse=True)
def _settings(settings, monkeypatch):
    settings.AGENTCC_MCP_CONNECT_CALLBACK_URL = CALLBACK
    settings.AGENTCC_MCP_CONNECT_CONSOLE_URL = CONSOLE
    monkeypatch.setenv(connect.INTERNAL_SECRET_ENV, INTERNAL_SECRET)
    monkeypatch.setattr(gateway_client, "AGENTCC_ADMIN_TOKEN", ADMIN_TOKEN)


@pytest.fixture
def purges():
    calls = []

    def purge(org_id, server_id, user_id=None):
        calls.append((org_id, server_id, user_id))
        return True

    with patch.object(connect.gateway_credentials, "purge", purge):
        yield calls


@pytest.fixture
def config(organization, user):
    return seed_config(organization)


def _seed(issuer, redirect, client_id):
    return AgentccMCPUpstreamClient.objects.create(
        issuer=issuer, redirect_uri=redirect, client_id=client_id
    )


def _forget(**options):
    out = StringIO()
    call_command(COMMAND, stdout=out, **options)
    return out.getvalue()


def _pks():
    return set(AgentccMCPUpstreamClient.objects.values_list("pk", flat=True))


def _no_op(issuer, redirect):
    return (
        f"no registered client for issuer {issuer} redirect {redirect}; "
        "nothing deleted\n"
    )


def _started_client_id(client):
    """The client id a sign-in start sends the member to the vendor with."""
    response = client.post(START.format(SERVER_ID), {}, format="json")
    assert response.status_code == 200, response.json()
    url = response.json()["result"]["authorize_url"]
    return parse_qs(urlsplit(url).query)["client_id"][0]


# --- Property: it acts only on both an issuer and a redirect --------------------


@pytest.mark.parametrize(
    "options, missing",
    [
        ({}, "--issuer"),
        ({"redirect": CALLBACK}, "--issuer"),
        ({"issuer": ISSUER}, "--redirect"),
        ({"issuer": "   ", "redirect": CALLBACK}, "--issuer"),
        ({"issuer": ISSUER, "redirect": ""}, "--redirect"),
    ],
)
def test_it_refuses_without_both_an_issuer_and_a_redirect(options, missing):
    _seed(ISSUER, CALLBACK, "test-client-kept-1")
    _seed(ISSUER, OTHER_REDIRECT, "test-client-kept-2")
    before = _pks()

    with pytest.raises(CommandError, match=missing):
        _forget(**options)

    assert _pks() == before
    assert len(before) == 2


# --- Property: it deletes exactly the one matching client -----------------------


def test_it_deletes_only_the_client_for_that_issuer_and_redirect():
    gone = _seed(ISSUER, CALLBACK, "test-client-forgotten")
    same_issuer = _seed(ISSUER, OTHER_REDIRECT, "test-client-other-redirect")
    same_redirect = _seed(OTHER_ISSUER, CALLBACK, "test-client-other-issuer")

    out = _forget(issuer=ISSUER, redirect=CALLBACK)

    assert out == (
        f"forgot client row {gone.pk} for issuer {ISSUER} redirect {CALLBACK}; "
        "deleted 1\n"
    )
    assert "test-client-forgotten" not in out
    assert _pks() == {same_issuer.pk, same_redirect.pk}


@pytest.mark.parametrize(
    "issuer, redirect",
    [
        (ISSUER, CALLBACK.rstrip("/")),
        (ISSUER + "/", CALLBACK),
        (ISSUER.replace("auth.vendor", "AUTH.Vendor"), CALLBACK),
        (ISSUER, CALLBACK.replace("api.example", "API.Example")),
    ],
)
def test_a_near_miss_deletes_nothing(issuer, redirect):
    kept = _seed(ISSUER, CALLBACK, "test-client-kept")

    out = _forget(issuer=issuer, redirect=redirect)

    assert out == _no_op(issuer, redirect)
    assert _pks() == {kept.pk}


def test_with_no_client_stored_for_it_the_command_is_a_no_op():
    _seed(OTHER_ISSUER, OTHER_REDIRECT, "test-client-elsewhere")
    before = _pks()

    out = _forget(issuer=ISSUER, redirect=CALLBACK)

    assert out == _no_op(ISSUER, CALLBACK)
    assert _pks() == before


# --- Property: the next sign-in at that vendor registers a new client -----------


def test_after_it_the_next_sign_in_registers_a_new_client(
    auth_client, organization, user, config, purges
):
    vendor = FakeVendor(cimd=False, dcr=True)
    with patch.object(connect.mcp_safe_fetch, "fetch", vendor.fetch):
        first = _started_client_id(auth_client)
        assert len(vendor.registrations) == 1
        assert _started_client_id(auth_client) == first
        assert len(vendor.registrations) == 1
        stored = AgentccMCPUpstreamClient.objects.get()
        assert (stored.issuer, stored.redirect_uri) == (ISSUER, CALLBACK)
        assert stored.client_id == first

        out = _forget(issuer=ISSUER, redirect=CALLBACK)
        assert out.startswith(f"forgot client row {stored.pk} ")
        assert first not in out
        assert not AgentccMCPUpstreamClient.objects.exists()

        again = _started_client_id(auth_client)

    assert len(vendor.registrations) == 2
    assert again != first
    renewed = AgentccMCPUpstreamClient.objects.get()
    assert (renewed.issuer, renewed.redirect_uri) == (ISSUER, CALLBACK)
    assert renewed.client_id == again
