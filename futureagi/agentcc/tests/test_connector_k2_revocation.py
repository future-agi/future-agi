"""A K2 grant is revoked as the registration its own record names (G14b).

The grant records, when it is stored, that the org's own app registration
issued it. Disconnect reads that record, not the server's configuration at
the time:

- After a validated config write puts another Google provider under the
  server's id and the org's registration is then removed, nothing is sent,
  and FutureAGI's platform client is never read, even one holding the very
  app the org registered.
- A registration that cannot be read is told apart from a missing one.
- A grant stored before the record existed still counts as K2 while its
  server's current entry is a pilot's.

Through the real routes and the real config writer; the authorization server
is a loopback fake and the gateway push a recorded fake. Every value is a
fake.
"""

import pytest

from agentcc.services import mcp_connect_clients as connect_clients
from agentcc.services import mcp_credentials as vault
from agentcc.services.org_config_writer import REPLACE_SECTIONS, write_config_version
from agentcc.tests import connector_k2_flow as flow
from agentcc.tests.connector_k2_flow import GMAIL
from agentcc.tests.connector_k2_helpers import add_real_provider, k2_entry

pytestmark = pytest.mark.django_db

k2_settings = flow.k2_settings
servers = flow.servers
k2_bundle = flow.k2_bundle
runtime = flow.runtime
purges = flow.purges

DISCONNECT = f"/agentcc/mcp/connect/{GMAIL}/"


@pytest.fixture(autouse=True)
def _k2(k2_settings, k2_bundle, purges, runtime):
    """The runtime on, the pilot bundle loaded and the gateway purge faked."""


def _sent(monkeypatch):
    """The client kind of every token or revocation request that went out."""
    kinds = []
    real = connect_clients.post

    def post(url, form, client, ref, **kwargs):
        kinds.append(client.kind)
        return real(url, form, client, ref, **kwargs)

    monkeypatch.setattr(connect_clients, "post", post)
    return kinds


def _signed_in(auth_client, organization, user, servers):
    flow.seed(organization, gmail=k2_entry(GMAIL))
    registration = flow.register(organization, user)
    _, page = flow.sign_in(auth_client, servers, GMAIL)
    assert page.status_code == 200
    return registration


def test_a_replaced_server_never_makes_the_grant_a_platform_clients(
    auth_client, organization, user, servers, k2_bundle, purges, monkeypatch
):
    flow.platform_has_the_same_app(monkeypatch)
    other = add_real_provider(k2_bundle, "google-bigquery", "mcp", servers.origin)
    registration = _signed_in(auth_client, organization, user, servers)
    # The same server id now names a provider that is not a pilot's.
    replacement = {
        "url": other["transport"]["url"],
        "transport": "http",
        "catalog_ref": f"{other['app']}/{other['id']}@{other['version']}",
        "credential": {"mode": "per_user"},
    }
    config, _ = write_config_version(
        organization,
        {"mcp": {"enabled": True, "servers": {GMAIL: replacement}}},
        mode=REPLACE_SECTIONS,
        user=user,
        push=lambda _: True,
    )
    vault.revoke_oauth_client(registration, user)
    flow.published_metadata(monkeypatch, servers)
    reads = flow.platform_reads(monkeypatch)
    sent = _sent(monkeypatch)

    gone = auth_client.delete(DISCONNECT)

    assert config.mcp["servers"][GMAIL]["catalog_ref"] == replacement["catalog_ref"]
    assert sent == []
    assert reads == []
    flow.assert_revocation_waits(gone, organization, user, servers, purges)


def test_an_unreadable_registration_is_told_apart_and_nothing_is_sent(
    auth_client, organization, user, servers, purges, monkeypatch
):
    registration = _signed_in(auth_client, organization, user, servers)
    type(registration).objects.filter(pk=registration.pk).update(
        encrypted_secret="enc::not-a-ciphertext"
    )
    sent = _sent(monkeypatch)

    gone = auth_client.delete(DISCONNECT)

    assert sent == []
    flow.assert_revocation_waits(
        gone, organization, user, servers, purges, "org_registration_unreadable"
    )


# --- The record itself (a column this round adds) ------------------------------------


def test_the_grant_records_that_the_registration_issued_it(
    auth_client, organization, user, servers
):
    _signed_in(auth_client, organization, user, servers)

    assert flow.account(organization, user, GMAIL).oauth_org_only is True


def test_a_grant_from_before_the_record_counts_while_its_server_is_a_pilots(
    auth_client, organization, user, servers, purges, monkeypatch
):
    flow.platform_has_the_same_app(monkeypatch)
    registration = _signed_in(auth_client, organization, user, servers)
    row = flow.account(organization, user, GMAIL)
    type(row).objects.filter(pk=row.pk).update(oauth_org_only=False)
    vault.revoke_oauth_client(registration, user)
    reads = flow.platform_reads(monkeypatch)

    gone = auth_client.delete(DISCONNECT)

    assert reads == []
    flow.assert_revocation_waits(gone, organization, user, servers, purges)
