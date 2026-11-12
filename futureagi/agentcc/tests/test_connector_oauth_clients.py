"""OAuth client sources for catalog connectors (CN3).

``resolve`` picks the client the connect flow uses: the org's own client (an
``oauth_client`` vault row), else FutureAGI's platform client from operator
secrets (env names or the file the operator names), else ``None``, after which
``client_source`` picks CIMD, then DCR, or refuses. Where an organization sets
the host of the token or revocation endpoint, the platform client is never a
source. Every value here is a fake. Refusals are also checked as the app's
Sentry would report them, local variables included.
"""

import dataclasses
import json
import logging
import os
import pickle
from functools import partial

import pytest
import sentry_sdk
from cryptography.fernet import Fernet
from rest_framework.renderers import JSONRenderer
from sentry_sdk.scrubber import DEFAULT_DENYLIST, EventScrubber
from sentry_sdk.transport import Transport
from structlog.testing import capture_logs

from accounts.models import Organization
from agentcc.models.mcp_credential import AgentccMCPCredential
from agentcc.services import connector_oauth_clients as clients
from agentcc.services.credential_manager import ENCRYPTED_PREFIX, encrypt_token
from tfc.logging.sentry import SENSITIVE_KEY_SUBSTRINGS, _get_before_send

pytestmark = pytest.mark.integration

Kind = AgentccMCPCredential.Kind
Scope = AgentccMCPCredential.Scope
Status = AgentccMCPCredential.Status

PLATFORM_ID = "fake-cn3-platform-client-id"
PLATFORM_SECRET = "fake-cn3-platform-secret-not-real"
PLATFORM_SECRET_ROTATED = "fake-cn3-platform-secret-rotated-not-real"
FILE_ID = "fake-cn3-file-client-id"
FILE_SECRET = "fake-cn3-file-secret-not-real"
FILE_SECRET_ROTATED = "fake-cn3-file-secret-rotated-not-real"
ORG_CLIENT_ID = "fake-cn3-org-client-id"
ORG_SECRET = "fake-cn3-org-secret-not-real"
SERVER_CLIENT_ID = "fake-cn3-server-client-id"
SERVER_SECRET = "fake-cn3-server-secret-not-real"
DECOY_SECRET = "fake-cn3-decoy-secret-not-real"
NOTION_SECRET = "fake-cn3-notion-secret-not-real"
CANARY = "fake-cn3-canary-value-not-real"
ALL_REGISTRATIONS = ("preregistered", "cimd", "dcr")
# The endpoints a client secret is sent to, as the catalog writes them, URL
# variables unfilled. The catalog fixes FIXED_HOST's host. Snowflake's token
# endpoint is on each customer's account host, which an organization fills in
# as {accountUrl} when it adds the connector.
FIXED_HOST = {
    "token_url": "https://auth.example.com/oauth/token",
    "revocation_url": "https://auth.example.com/oauth/revoke",
}
ORG_SET_HOST = {
    "token_url": "https://{accountUrl}/oauth/token-request",
    "revocation_url": None,
}


@pytest.fixture(autouse=True)
def _no_operator_secrets(monkeypatch):
    """Start every test with no platform client and no decoy in the environment."""
    for name in list(os.environ):
        if name.startswith(
            ("AGENTCC_CONNECTOR_CLIENT", "OMEGA_INTEGRATION_", "OMEGA_CONNECTOR_")
        ):
            monkeypatch.delenv(name)


def _platform_env(monkeypatch, group, client_id=PLATFORM_ID, secret=PLATFORM_SECRET):
    id_name, secret_name = clients.env_names(group)
    monkeypatch.setenv(id_name, client_id)
    monkeypatch.setenv(secret_name, secret)


def _secrets_file(tmp_path, monkeypatch, document):
    path = tmp_path / "connector-clients.json"
    path.write_text(document if isinstance(document, str) else json.dumps(document))
    monkeypatch.setenv(clients.CLIENTS_FILE_ENV, str(path))
    return path


def _org_client(
    org, key, client_id=ORG_CLIENT_ID, secret=ORG_SECRET, *, encrypted=None, **fields
):
    """An org's own OAuth client row, written straight to the vault model."""
    return AgentccMCPCredential.objects.create(
        organization=org,
        server_id=key,
        scope=fields.pop("scope", Scope.SHARED),
        kind=fields.pop("kind", Kind.OAUTH_CLIENT),
        oauth_client_id=client_id,
        encrypted_secret=encrypted or encrypt_token(secret),
        **fields,
    )


def _dump(value):
    return json.dumps(value, default=str)


def test_org_client_wins_over_platform(organization, monkeypatch):
    _platform_env(monkeypatch, "linear")
    other_org = Organization.objects.create(name="CN3 other org")
    row = _org_client(organization, clients.org_client_key(client_group="linear"))

    ref = clients.resolve(organization, "linear", None, **FIXED_HOST)
    assert (ref.source, ref.origin, ref.client_group) == ("org", "group", "linear")
    assert (ref.client_id, ref.credential_id) == (ORG_CLIENT_ID, str(row.pk))
    assert ref.token_exchange_secret() == ORG_SECRET
    assert (
        clients.client_source(
            ref, registration=ALL_REGISTRATIONS, client_group="linear", **FIXED_HOST
        )
        == "org"
    )

    # An org without a client of its own gets the platform client, never this org's.
    other = clients.resolve(other_org, "linear", None, **FIXED_HOST)
    assert (other.source, other.origin, other.client_id) == (
        "platform",
        "env",
        PLATFORM_ID,
    )
    assert other.credential_id is None
    assert other.token_exchange_secret() == PLATFORM_SECRET
    assert (
        clients.client_source(
            other, registration=ALL_REGISTRATIONS, client_group="linear", **FIXED_HOST
        )
        == "platform"
    )

    # A client the org keyed to one server beats its group client, for that server only.
    server_row = _org_client(
        organization,
        clients.org_client_key(server_id="linear-eng"),
        client_id=SERVER_CLIENT_ID,
        secret=SERVER_SECRET,
    )
    for_server = clients.resolve(
        organization, "linear", None, server_id="linear-eng", **FIXED_HOST
    )
    assert (for_server.source, for_server.origin, for_server.client_id) == (
        "org",
        "server",
        SERVER_CLIENT_ID,
    )
    assert for_server.credential_id == str(server_row.pk)
    assert for_server.token_exchange_secret() == SERVER_SECRET
    for_other_server = clients.resolve(
        organization, "linear", None, server_id="linear-ops", **FIXED_HOST
    )
    assert (for_other_server.origin, for_other_server.client_id) == (
        "group",
        ORG_CLIENT_ID,
    )

    # Once the org revokes its client, the platform client is next.
    row.status = Status.REVOKED
    row.save(update_fields=["status"])
    after = clients.resolve(organization, "linear", None, **FIXED_HOST)
    assert (after.source, after.client_id) == ("platform", PLATFORM_ID)


def test_org_client_coexists_with_the_servers_own_credentials(organization):
    """The vault allows one active shared row per server id, whatever its kind,
    so an org client must not be keyed by the bare server id: that slot holds
    the server's own shared secret or shared OAuth token."""
    _org_client(organization, "linear-eng", kind=Kind.OAUTH)  # a shared token
    _org_client(organization, "linear-ops", kind=Kind.BEARER)  # a shared secret
    for server_id in ("linear-eng", "linear-ops"):
        _org_client(
            organization,
            clients.org_client_key(server_id=server_id),
            client_id=SERVER_CLIENT_ID,
            secret=SERVER_SECRET,
        )
        ref = clients.resolve(
            organization, "linear", None, server_id=server_id, **FIXED_HOST
        )
        assert (ref.source, ref.origin, ref.client_id) == (
            "org",
            "server",
            SERVER_CLIENT_ID,
        )
        assert ref.token_exchange_secret() == SERVER_SECRET


def test_unreadable_org_client_never_falls_back_to_platform(organization, monkeypatch):
    """An org that brought its own client never has its users sent to ours."""
    _platform_env(monkeypatch, "linear")
    key = clients.org_client_key(client_group="linear")
    # Well-formed ciphertext under a key this deployment does not hold.
    foreign = Fernet(Fernet.generate_key()).encrypt(
        json.dumps({"v": ORG_SECRET}).encode()
    )
    row = _org_client(
        organization, key, encrypted=ENCRYPTED_PREFIX + foreign.decode("ascii")
    )

    with pytest.raises(clients.OrgClientUnreadable) as err:
        clients.resolve(organization, "linear", None, **FIXED_HOST)
    assert err.value.code == "connector_oauth_client_unreadable"
    assert str(row.pk) in str(err.value)

    # A row without a client id is just as unusable.
    row.status = Status.REVOKED
    row.save(update_fields=["status"])
    _org_client(organization, key, client_id="")
    with pytest.raises(clients.OrgClientUnreadable):
        clients.resolve(organization, "linear", None, **FIXED_HOST)


def test_platform_client_from_env_names_only(organization, monkeypatch, settings):
    assert clients.env_names("google") == (
        "AGENTCC_CONNECTOR_CLIENT_GOOGLE_ID",
        "AGENTCC_CONNECTOR_CLIENT_GOOGLE_SECRET",
    )
    assert clients.env_names("google-workspace") == (
        "AGENTCC_CONNECTOR_CLIENT_GOOGLE_WORKSPACE_ID",
        "AGENTCC_CONNECTOR_CLIENT_GOOGLE_WORKSPACE_SECRET",
    )

    # Decoys, none of them a source: Django settings with the same names, Omega
    # EE's two env schemes, the app's own names while the app is in a group, and
    # another org's client.
    settings.AGENTCC_CONNECTOR_CLIENT_GOOGLE_ID = "fake-cn3-settings-client-id"
    settings.AGENTCC_CONNECTOR_CLIENT_GOOGLE_SECRET = DECOY_SECRET
    for name in (
        "OMEGA_INTEGRATION_GOOGLE",
        "OMEGA_INTEGRATION_GMAIL",
        "OMEGA_CONNECTOR_GMAIL",
    ):
        monkeypatch.setenv(f"{name}_CLIENT_ID", "fake-cn3-omega-client-id")
        monkeypatch.setenv(f"{name}_CLIENT_SECRET", DECOY_SECRET)
    monkeypatch.setenv("AGENTCC_CONNECTOR_CLIENT_GMAIL_ID", "fake-cn3-app-client-id")
    monkeypatch.setenv("AGENTCC_CONNECTOR_CLIENT_GMAIL_SECRET", DECOY_SECRET)
    _org_client(
        Organization.objects.create(name="CN3 decoy org"),
        clients.org_client_key(client_group="google"),
    )
    assert clients.resolve(organization, "gmail", "google", **FIXED_HOST) is None

    monkeypatch.setenv("AGENTCC_CONNECTOR_CLIENT_GOOGLE_ID", PLATFORM_ID)
    monkeypatch.setenv("AGENTCC_CONNECTOR_CLIENT_GOOGLE_SECRET", PLATFORM_SECRET)
    ref = clients.resolve(organization, "gmail", "google", **FIXED_HOST)
    assert (ref.source, ref.origin, ref.client_group, ref.client_id) == (
        "platform",
        "env",
        "google",
        PLATFORM_ID,
    )
    assert ref.credential_id is None
    assert ref.token_exchange_secret() == PLATFORM_SECRET

    # Read at call time: a rotated secret is used on the next resolve, no restart.
    monkeypatch.setenv(
        "AGENTCC_CONNECTOR_CLIENT_GOOGLE_SECRET", PLATFORM_SECRET_ROTATED
    )
    rotated = clients.resolve(organization, "gmail", "google", **FIXED_HOST)
    assert rotated.token_exchange_secret() == PLATFORM_SECRET_ROTATED

    # An empty value is unset, the way a chart renders a key nobody filled.
    monkeypatch.setenv("AGENTCC_CONNECTOR_CLIENT_GOOGLE_ID", "")
    monkeypatch.setenv("AGENTCC_CONNECTOR_CLIENT_GOOGLE_SECRET", "")
    assert clients.resolve(organization, "gmail", "google", **FIXED_HOST) is None


def test_platform_client_from_operator_secrets_file(
    organization, monkeypatch, tmp_path
):
    path = _secrets_file(
        tmp_path,
        monkeypatch,
        {
            "google": {"client_id": FILE_ID, "client_secret": FILE_SECRET},
            "slack": {"client_id": FILE_ID, "client_secret": FILE_SECRET},
        },
    )
    ref = clients.resolve(organization, "gmail", "google", **FIXED_HOST)
    assert (ref.source, ref.origin, ref.client_group, ref.client_id) == (
        "platform",
        "file",
        "google",
        FILE_ID,
    )
    assert ref.token_exchange_secret() == FILE_SECRET

    # Read at call time, so a secret rotated in place (a mounted Secret) is picked up.
    path.write_text(
        json.dumps(
            {"google": {"client_id": FILE_ID, "client_secret": FILE_SECRET_ROTATED}}
        )
    )
    assert (
        clients.resolve(
            organization, "gmail", "google", **FIXED_HOST
        ).token_exchange_secret()
        == FILE_SECRET_ROTATED
    )
    # A group the file does not name has no platform client.
    assert clients.resolve(organization, "linear", None, **FIXED_HOST) is None

    # The environment wins over the file for the same group.
    _platform_env(monkeypatch, "google")
    env_ref = clients.resolve(organization, "gmail", "google", **FIXED_HOST)
    assert (env_ref.origin, env_ref.client_id) == ("env", PLATFORM_ID)
    assert env_ref.token_exchange_secret() == PLATFORM_SECRET


def test_half_configured_platform_client_refused(organization, monkeypatch, tmp_path):
    """Half a client is refused, never merged with the file and never skipped."""
    id_name, secret_name = clients.env_names("google")
    _secrets_file(
        tmp_path,
        monkeypatch,
        {"google": {"client_id": FILE_ID, "client_secret": FILE_SECRET}},
    )

    monkeypatch.setenv(id_name, PLATFORM_ID)
    with pytest.raises(clients.PlatformClientMisconfigured) as err:
        clients.resolve(organization, "gmail", "google", **FIXED_HOST)
    assert err.value.code == "connector_oauth_client_misconfigured"
    assert secret_name in str(err.value)

    monkeypatch.delenv(id_name)
    monkeypatch.setenv(secret_name, PLATFORM_SECRET)
    with pytest.raises(clients.PlatformClientMisconfigured) as err:
        clients.resolve(organization, "gmail", "google", **FIXED_HOST)
    assert id_name in str(err.value)

    # A value with a trailing newline is refused, never trimmed into another value.
    monkeypatch.setenv(id_name, PLATFORM_ID)
    monkeypatch.setenv(secret_name, PLATFORM_SECRET + "\n")
    with pytest.raises(clients.PlatformClientMisconfigured) as err:
        clients.resolve(organization, "gmail", "google", **FIXED_HOST)
    assert secret_name in str(err.value)
    assert PLATFORM_SECRET not in str(err.value)


_GOOD_ENTRY = {"client_id": FILE_ID, "client_secret": FILE_SECRET}


_ENTRY_SHAPE = "exactly client_id and client_secret"
_VALUE_SHAPE = "non-empty string"


@pytest.mark.parametrize(
    "document, reason",
    [
        pytest.param("{not json " + FILE_SECRET, "not UTF-8 JSON", id="not-json"),
        pytest.param(json.dumps([FILE_SECRET]), "one JSON object", id="not-an-object"),
        pytest.param(
            json.dumps({"Bad_" + FILE_SECRET: _GOOD_ENTRY}),
            "not a client group",
            id="bad-group-name",
        ),
        pytest.param(
            json.dumps({"google": [FILE_SECRET]}), _ENTRY_SHAPE, id="entry-not-object"
        ),
        pytest.param(
            json.dumps({"google": {"client_secret": FILE_SECRET}}),
            _ENTRY_SHAPE,
            id="missing-id",
        ),
        pytest.param(
            json.dumps({"google": {**_GOOD_ENTRY, "scopes": []}}),
            _ENTRY_SHAPE,
            id="unknown-field",
        ),
        pytest.param(
            json.dumps({"google": {"client_id": FILE_ID, "client_secret": 42}}),
            _VALUE_SHAPE,
            id="secret-not-string",
        ),
        pytest.param(
            json.dumps({"google": {**_GOOD_ENTRY, "client_secret": " " + FILE_SECRET}}),
            _VALUE_SHAPE,
            id="secret-padded",
        ),
        # Valid apart from its size (over 1 MiB), so only the size cap refuses it.
        pytest.param(
            json.dumps({f"g{n:05d}": _GOOD_ENTRY for n in range(12_000)}),
            "larger than",
            id="too-large",
        ),
        pytest.param(
            json.dumps({"google": _GOOD_ENTRY})[:-1] + ",}",
            "not UTF-8 JSON",
            id="stray-comma",
        ),
        # json.loads would keep the last of two keys without a word.
        pytest.param(
            '{"google": '
            + json.dumps(_GOOD_ENTRY)
            + ', "google": '
            + json.dumps(_GOOD_ENTRY)
            + "}",
            "has the key 'google' twice",
            id="repeated-group",
        ),
        pytest.param(
            json.dumps({"google": _GOOD_ENTRY})[:-2]
            + ', "client_secret": "'
            + FILE_SECRET
            + '"}}',
            "has the key 'client_secret' twice",
            id="repeated-field",
        ),
        # Deep nesting is a RecursionError, which is not a ValueError.
        pytest.param(
            json.dumps({"google": _GOOD_ENTRY})[:-1]
            + ', "deep": '
            + "[" * 100_000
            + "]" * 100_000
            + "}",
            "nests too deeply",
            id="too-deep",
        ),
    ],
)
def test_malformed_secrets_file_refused(
    organization, monkeypatch, tmp_path, document, reason
):
    """The whole file is refused, loudly, for its own reason, naming the
    setting and never a value."""
    path = _secrets_file(tmp_path, monkeypatch, document)
    with (
        capture_logs() as logs,
        pytest.raises(clients.PlatformClientMisconfigured) as err,
    ):
        clients.resolve(organization, "gmail", "google", **FIXED_HOST)
    message = str(err.value)
    assert clients.CLIENTS_FILE_ENV in message
    assert reason in message
    assert FILE_SECRET not in message
    # The file's path is for the operator: in the server log, not in the message.
    assert str(path) not in message
    warnings = [e for e in logs if e["event"] == "connector_oauth_client_misconfigured"]
    assert [e["path"] for e in warnings] == [str(path)]
    # Raised outside any handler, so nothing is chained to it (a JSONDecodeError
    # keeps the whole document in its doc attribute).
    assert err.value.__context__ is None
    assert err.value.__cause__ is None


def test_unreadable_secrets_file_location_refused(organization, monkeypatch, tmp_path):
    missing = str(tmp_path / "missing.json")
    for path, reason in (
        ("connector-clients.json", "absolute"),
        (missing, "cannot be read"),
    ):
        monkeypatch.setenv(clients.CLIENTS_FILE_ENV, path)
        with (
            capture_logs() as logs,
            pytest.raises(clients.PlatformClientMisconfigured, match=reason) as err,
        ):
            clients.resolve(organization, "gmail", "google", **FIXED_HOST)
        assert path not in str(err.value)
        assert [e["path"] for e in logs] == [path]


def test_none_means_cimd_then_dcr(organization):
    assert clients.resolve(organization, "linear", None, **FIXED_HOST) is None

    pick = partial(clients.client_source, None, client_group="linear", **FIXED_HOST)
    assert pick(registration=ALL_REGISTRATIONS) == "cimd"
    assert pick(registration=("dcr", "cimd")) == "cimd"
    assert pick(registration=("preregistered", "dcr")) == "dcr"

    # Nothing left to try: the connect is refused, naming what would fix it.
    with pytest.raises(clients.OAuthClientUnavailable) as err:
        pick(registration=("preregistered",))
    assert err.value.code == "oauth_client_unavailable"
    assert "AGENTCC_CONNECTOR_CLIENT_LINEAR_ID" in str(err.value)
    assert "AGENTCC_CONNECTOR_CLIENT_LINEAR_SECRET" in str(err.value)
    with pytest.raises(clients.OAuthClientUnavailable):
        pick(registration=())

    # Methods outside the catalog's list are refused, as is a bare string.
    for registration in (("magic",), "cimd"):
        with pytest.raises(clients.ConnectorOAuthClientError):
            pick(registration=registration)


def test_configured_client_used_only_where_preregistered_clients_are_accepted(
    organization, monkeypatch
):
    _platform_env(monkeypatch, "linear")
    ref = clients.resolve(organization, "linear", None, **FIXED_HOST)
    pick = partial(clients.client_source, ref, client_group="linear", **FIXED_HOST)
    assert pick(registration=("preregistered",)) == "platform"
    # An authorization server that takes only CIMD or DCR never sees our client.
    assert pick(registration=("cimd", "dcr")) == "cimd"
    with pytest.raises(clients.OAuthClientUnavailable):
        pick(registration=())
    # A client resolved for one group is never used for another.
    with pytest.raises(clients.ConnectorOAuthClientError):
        clients.client_source(
            ref, registration=ALL_REGISTRATIONS, client_group="google", **FIXED_HOST
        )


def test_secret_never_in_repr_or_logs(organization, monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    _platform_env(monkeypatch, "google")
    _org_client(organization, clients.org_client_key(client_group="linear"))
    secrets = (PLATFORM_SECRET, ORG_SECRET, FILE_SECRET, DECOY_SECRET)
    shown = []
    with capture_logs() as logs:
        platform = clients.resolve(organization, "gmail", "google", **FIXED_HOST)
        org = clients.resolve(organization, "linear", None, **FIXED_HOST)
        for ref in (platform, org):
            as_dict = dataclasses.asdict(ref)
            shown += [repr(ref), str(ref), f"{ref}", repr(vars(ref)), repr(as_dict)]
            # Nothing can serialise it into a response, a cache or a queue.
            with pytest.raises(TypeError):
                json.dumps(as_dict)
            with pytest.raises(TypeError):
                JSONRenderer().render(as_dict)
            with pytest.raises(TypeError):
                pickle.dumps(ref)

        # Every refusal names a setting or a row, never a value.
        monkeypatch.setenv("AGENTCC_CONNECTOR_CLIENT_SLACK_SECRET", DECOY_SECRET)
        _secrets_file(
            tmp_path,
            monkeypatch,
            {"notion": {"client_id": FILE_ID, "client_secret": FILE_SECRET + "\t"}},
        )
        foreign = Fernet(Fernet.generate_key()).encrypt(b'{"v": "x"}')
        _org_client(
            organization,
            clients.org_client_key(client_group="asana"),
            encrypted=ENCRYPTED_PREFIX + foreign.decode("ascii"),
        )
        for app_id in ("slack", "notion", "asana"):
            with pytest.raises(clients.ConnectorOAuthClientError) as err:
                clients.resolve(organization, app_id, None, **FIXED_HOST)
            shown += [str(err.value), repr(err.value)]
        # Where an organization sets the token host, the platform client is
        # passed over and the refusal names the catalog field.
        _platform_env(monkeypatch, "snowflake")
        assert clients.resolve(organization, "snowflake", None, **ORG_SET_HOST) is None
        with pytest.raises(clients.OrgClientRequired) as err:
            clients.client_source(
                None,
                registration=("preregistered",),
                client_group="snowflake",
                **ORG_SET_HOST,
            )
        shown += [str(err.value), repr(err.value)]

    # The secrets are there for the token exchange, and only there.
    assert platform.token_exchange_secret() == PLATFORM_SECRET
    assert org.token_exchange_secret() == ORG_SECRET
    events = {entry["event"] for entry in logs}
    assert {
        "connector_oauth_client_resolved",
        "connector_oauth_client_misconfigured",
        "connector_oauth_client_unreadable",
        "connector_oauth_platform_client_not_used",
    } <= events
    rendered = "\n".join(shown) + _dump(logs) + caplog.text
    assert not any(secret in rendered for secret in secrets)


def test_client_group_shared_by_apps(organization, monkeypatch):
    _platform_env(monkeypatch, "google")
    apps = ("gmail", "google-calendar", "google-drive")
    shared = {
        (ref.source, ref.client_group, ref.client_id)
        for ref in (
            clients.resolve(organization, app, "google", **FIXED_HOST) for app in apps
        )
    }
    assert shared == {("platform", "google", PLATFORM_ID)}
    # Without its group an app has its own client: google-drive alone has none.
    assert clients.resolve(organization, "google-drive", None, **FIXED_HOST) is None

    # One org client for the group serves every app in it, ahead of the platform's.
    _org_client(organization, clients.org_client_key(client_group="google"))
    shared = {
        (ref.source, ref.client_group, ref.client_id)
        for ref in (
            clients.resolve(organization, app, "google", **FIXED_HOST) for app in apps
        )
    }
    assert shared == {("org", "google", ORG_CLIENT_ID)}


def test_rows_that_are_not_the_orgs_client_are_never_used(organization, user):
    key = clients.org_client_key(client_group="linear")
    _org_client(Organization.objects.create(name="CN3 neighbour org"), key)
    _org_client(organization, key, kind=Kind.BEARER)
    _org_client(organization, key, account_label="second")
    _org_client(organization, key, scope=Scope.USER, user=user)
    _org_client(organization, key, status=Status.NEEDS_REAUTH)
    assert clients.resolve(organization, "linear", None, **FIXED_HOST) is None


@pytest.mark.parametrize(
    "app_id, client_group, server_id",
    [
        pytest.param("Gmail", None, None, id="app-upper-case"),
        pytest.param("gmail", "google_mail", None, id="group-underscore"),
        pytest.param("gmail", "g", None, id="group-too-short"),
        pytest.param("gmail", "", None, id="group-empty"),
        pytest.param("gmail", "../google", None, id="group-path"),
        pytest.param("gmail", "google", "group:google", id="server-id-aliases-group"),
        pytest.param("gmail", "google", "", id="server-id-empty"),
        pytest.param("gmail", "google", "s" * 65, id="server-id-too-long"),
        # Fits the column alone, but not with its "server:" namespace.
        pytest.param("gmail", "google", "s" * 58, id="server-key-too-long"),
    ],
)
def test_malformed_references_refused(organization, app_id, client_group, server_id):
    with pytest.raises(clients.ConnectorOAuthClientError):
        clients.resolve(
            organization, app_id, client_group, server_id=server_id, **FIXED_HOST
        )


def test_org_client_key():
    assert clients.org_client_key(client_group="google") == "group:google"
    assert clients.org_client_key(server_id="github") == "server:github"
    assert clients.org_client_key(server_id="fagi.search") == "server:fagi.search"
    for kwargs in ({}, {"client_group": "google", "server_id": "github"}):
        with pytest.raises(clients.ConnectorOAuthClientError):
            clients.org_client_key(**kwargs)
    with pytest.raises(clients.ConnectorOAuthClientError):
        clients.env_names("google_mail")


def test_resolve_needs_a_saved_organization(monkeypatch):
    """Refused the same way whether or not a platform client is set, so a
    missing organization never depends on how the deployment is configured."""
    for configured in (False, True):
        if configured:
            _platform_env(monkeypatch, "linear")
        for org in (None, Organization(name="CN3 unsaved org"), "an-org-id"):
            with pytest.raises(clients.ConnectorOAuthClientError, match="saved"):
                clients.resolve(org, "linear", None, **FIXED_HOST)


def test_org_client_for_the_server_is_never_passed_over(organization):
    """A probe records which of CIMD and DCR an authorization server accepts,
    and cannot see whether it accepts pre-registered clients. So an org client
    keyed to the server is used whatever ``registration`` says. A client for a
    whole group spans providers, and is passed over, with a warning, where its
    provider's server does not accept pre-registered clients."""
    group_row = _org_client(organization, clients.org_client_key(client_group="linear"))
    server_row = _org_client(
        organization,
        clients.org_client_key(server_id="linear-mcp"),
        client_id=SERVER_CLIENT_ID,
        secret=SERVER_SECRET,
    )
    for_server = clients.resolve(
        organization, "linear", None, server_id="linear-mcp", **FIXED_HOST
    )
    assert (for_server.origin, for_server.credential_id) == (
        "server",
        str(server_row.pk),
    )
    pick = partial(
        clients.client_source, for_server, client_group="linear", **FIXED_HOST
    )
    with capture_logs() as logs:
        for registration in (ALL_REGISTRATIONS, ("cimd", "dcr"), ("dcr",), ()):
            assert pick(registration=registration) == "org"
    assert logs == []

    for_group = clients.resolve(
        organization, "linear", None, server_id="linear-api", **FIXED_HOST
    )
    assert (for_group.origin, for_group.credential_id) == ("group", str(group_row.pk))
    pick = partial(
        clients.client_source, for_group, client_group="linear", **FIXED_HOST
    )
    assert pick(registration=("preregistered", "dcr")) == "org"
    with capture_logs() as logs:
        assert pick(registration=("cimd", "dcr")) == "cimd"
        with pytest.raises(clients.OAuthClientUnavailable):
            pick(registration=())
    assert [(e["event"], e["log_level"], e["credential_id"]) for e in logs] == [
        ("connector_oauth_org_client_passed_over", "warning", str(group_row.pk))
    ] * 2


# --- Hosts an organization sets ------------------------------------------------
# A client secret goes to the provider's token endpoint and, on disconnect, to
# its revocation endpoint. Where an organization fills in either one's host, it
# would choose where FutureAGI's platform client secret goes.


@pytest.mark.parametrize(
    "endpoints, field, variable",
    [
        pytest.param(ORG_SET_HOST, "tokenUrl", "{accountUrl}", id="token-host"),
        pytest.param(
            {
                "token_url": FIXED_HOST["token_url"],
                "revocation_url": "https://{accountUrl}/oauth/revoke",
            },
            "revocation.url",
            "{accountUrl}",
            id="revocation-host",
        ),
        pytest.param(
            {
                "token_url": "https://{tenant}.auth.example.com/oauth/token",
                "revocation_url": None,
            },
            "tokenUrl",
            "{tenant}",
            id="part-of-the-host",
        ),
        pytest.param(
            {
                "token_url": "https://auth.example.com:{port}/oauth/token",
                "revocation_url": None,
            },
            "tokenUrl",
            "{port}",
            id="port",
        ),
        # A value straight after the host, such as "@elsewhere.example", makes
        # the host what follows the "@".
        pytest.param(
            {"token_url": "https://auth.example.com{path}", "revocation_url": None},
            "tokenUrl",
            "{path}",
            id="straight-after-the-host",
        ),
        pytest.param(
            {"token_url": "{baseUrl}/oauth/token", "revocation_url": None},
            "tokenUrl",
            "{baseUrl}",
            id="scheme-and-host",
        ),
    ],
)
def test_platform_client_never_sent_to_a_host_an_org_sets(
    organization, monkeypatch, tmp_path, endpoints, field, variable
):
    """FutureAGI's platform client is not a source there, however it is
    configured, so a connect without the organization's own client uses CIMD
    or DCR where accepted, and is otherwise refused with a clear error."""
    _platform_env(monkeypatch, "snowflake")
    _secrets_file(
        tmp_path,
        monkeypatch,
        {"snowflake": {"client_id": FILE_ID, "client_secret": FILE_SECRET}},
    )
    with capture_logs() as logs:
        assert clients.resolve(organization, "snowflake", None, **endpoints) is None
    assert [(e["event"], e["endpoint"], e["variable"]) for e in logs] == [
        ("connector_oauth_platform_client_not_used", field, variable)
    ]
    # Not even read: a broken platform client for the group refuses nothing.
    monkeypatch.setenv(
        "AGENTCC_CONNECTOR_CLIENT_SNOWFLAKE_SECRET", PLATFORM_SECRET + "\n"
    )
    assert clients.resolve(organization, "snowflake", None, **endpoints) is None

    pick = partial(clients.client_source, None, client_group="snowflake", **endpoints)
    with pytest.raises(clients.OrgClientRequired) as err:
        pick(registration=("preregistered",))
    assert err.value.code == "connector_oauth_org_client_required"
    assert isinstance(err.value, clients.OAuthClientUnavailable)
    message = str(err.value)
    assert field in message
    assert variable in message
    assert "own OAuth client" in message
    # Setting a platform client would not help, so the message never says so.
    assert "AGENTCC_CONNECTOR_CLIENT_" not in message
    assert PLATFORM_SECRET not in message
    # CIMD and DCR send no client secret of ours, and still apply.
    assert pick(registration=ALL_REGISTRATIONS) == "cimd"
    assert pick(registration=("preregistered", "dcr")) == "dcr"


@pytest.mark.parametrize(
    "endpoints",
    [
        pytest.param(
            {
                "token_url": "https://oauth2.googleapis.com/token",
                "revocation_url": "https://oauth2.googleapis.com/revoke",
            },
            id="google",
        ),
        # A variable in the path or the query leaves the host to the catalog,
        # as with Microsoft Entra's tenant.
        pytest.param(
            {
                "token_url": "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
                "revocation_url": None,
            },
            id="path-variable",
        ),
        pytest.param(
            {
                "token_url": "https://auth.example.com/oauth/token?realm={realm}",
                "revocation_url": "https://auth.example.com/oauth/revoke#{x}",
            },
            id="query-and-fragment-variables",
        ),
        pytest.param(
            {"token_url": "https://auth.example.com", "revocation_url": None},
            id="no-path",
        ),
    ],
)
def test_fixed_host_provider_gets_the_platform_client(
    organization, monkeypatch, endpoints
):
    _platform_env(monkeypatch, "google")
    with capture_logs() as logs:
        ref = clients.resolve(organization, "gmail", "google", **endpoints)
    assert (ref.source, ref.origin, ref.client_id) == ("platform", "env", PLATFORM_ID)
    assert ref.token_exchange_secret() == PLATFORM_SECRET
    assert [e["event"] for e in logs] == ["connector_oauth_client_resolved"]
    assert (
        clients.client_source(
            ref, registration=("preregistered",), client_group="google", **endpoints
        )
        == "platform"
    )


def test_org_client_works_where_the_org_sets_the_host(organization, monkeypatch):
    """The organization's own client, for the group or for one server, is used
    there; another organization without one is refused, never sent to ours."""
    _platform_env(monkeypatch, "snowflake")
    group_row = _org_client(
        organization, clients.org_client_key(client_group="snowflake")
    )
    ref = clients.resolve(organization, "snowflake", None, **ORG_SET_HOST)
    assert (ref.source, ref.origin, ref.client_id) == ("org", "group", ORG_CLIENT_ID)
    assert ref.credential_id == str(group_row.pk)
    assert ref.token_exchange_secret() == ORG_SECRET
    pick = partial(
        clients.client_source,
        registration=("preregistered",),
        client_group="snowflake",
        **ORG_SET_HOST,
    )
    assert pick(ref) == "org"

    server_row = _org_client(
        organization,
        clients.org_client_key(server_id="snowflake-acme"),
        client_id=SERVER_CLIENT_ID,
        secret=SERVER_SECRET,
    )
    for_server = clients.resolve(
        organization, "snowflake", None, server_id="snowflake-acme", **ORG_SET_HOST
    )
    assert (for_server.source, for_server.origin, for_server.client_id) == (
        "org",
        "server",
        SERVER_CLIENT_ID,
    )
    assert for_server.credential_id == str(server_row.pk)
    assert for_server.token_exchange_secret() == SERVER_SECRET
    assert pick(for_server) == "org"

    neighbour = Organization.objects.create(name="CN3 Snowflake neighbour")
    assert clients.resolve(neighbour, "snowflake", None, **ORG_SET_HOST) is None
    with pytest.raises(clients.OrgClientRequired):
        pick(None)


def test_platform_client_for_other_endpoints_refused(organization, monkeypatch):
    """A platform client resolved with other endpoints is never used for a
    provider whose host an organization sets: a caller's mistake, refused."""
    _platform_env(monkeypatch, "snowflake")
    ref = clients.resolve(organization, "snowflake", None, **FIXED_HOST)
    assert ref.source == "platform"
    for registration in (("preregistered",), ALL_REGISTRATIONS):
        with pytest.raises(clients.ConnectorOAuthClientError, match="tokenUrl") as err:
            clients.client_source(
                ref, registration=registration, client_group="snowflake", **ORG_SET_HOST
            )
        assert type(err.value) is clients.ConnectorOAuthClientError
        assert PLATFORM_SECRET not in str(err.value)


def test_endpoints_must_be_stated(organization):
    """Where the client secret goes is always stated: there is no default."""
    with pytest.raises(TypeError):
        clients.resolve(organization, "linear", None)
    with pytest.raises(TypeError):
        clients.client_source(
            None, registration=ALL_REGISTRATIONS, client_group="linear"
        )
    for endpoints in (
        {"token_url": "", "revocation_url": None},
        {"token_url": None, "revocation_url": None},
        {"token_url": FIXED_HOST["token_url"], "revocation_url": ""},
        {"token_url": FIXED_HOST["token_url"], "revocation_url": 42},
    ):
        with pytest.raises(clients.ConnectorOAuthClientError):
            clients.resolve(organization, "linear", None, **endpoints)
        with pytest.raises(clients.ConnectorOAuthClientError):
            clients.client_source(
                None, registration=ALL_REGISTRATIONS, client_group="linear", **endpoints
            )


# --- Error reports -------------------------------------------------------------
# The app's Sentry (tfc/logging/sentry.py, init_sentry) reports every frame's
# local variables (include_local_variables) and redacts only locals whose name
# is on its denylist. These helpers stay well away from the fake values above:
# a report's source context quotes the lines around each frame.

# init_sentry's EventScrubber denylist.
APP_DENYLIST = list(DEFAULT_DENYLIST) + [
    k for k in SENSITIVE_KEY_SUBSTRINGS if k not in DEFAULT_DENYLIST
]


class _SendNothing(Transport):
    def capture_envelope(self, envelope):
        pass


class _ErrorReports:
    """Sentry events for refusals, built by a real ``sentry_sdk.Client``: local
    variables and source context on, then init_sentry's EventScrubber and
    before_send ("app"), or no scrubbing at all ("unscrubbed"). The client is
    bound to a scope for each capture only, so the process's own Sentry state is
    untouched, and its transport sends nothing."""

    def __init__(self, pipeline):
        self.events = []
        if pipeline == "app":
            scrubber = EventScrubber(denylist=APP_DENYLIST, recursive=True)
            app_before_send = _get_before_send()
        else:
            scrubber = EventScrubber(denylist=[], send_default_pii=True)
            app_before_send = None

        def keep(event, hint):
            if app_before_send is not None:
                event = app_before_send(event, hint)
            if event is not None:
                self.events.append(event)
            return None  # never sent

        self.client = sentry_sdk.Client(
            dsn="https://public@127.0.0.1:9/1",
            transport=_SendNothing,
            default_integrations=False,
            enable_backpressure_handling=False,
            send_default_pii=True,
            include_local_variables=True,
            include_source_context=True,
            event_scrubber=scrubber,
            before_send=keep,
        )

    def refusal(self, call):
        """The report of the refusal ``call()`` raises, captured where a view
        or a task would report it."""
        sent = len(self.events)
        try:
            call()
        except clients.ConnectorOAuthClientError:
            with sentry_sdk.new_scope() as scope:
                scope.set_client(self.client)
                scope.capture_exception()
        else:
            pytest.fail("expected a refusal")
        assert len(self.events) == sent + 1
        return self.events[-1]


@pytest.fixture(params=["app", "unscrubbed"])
def error_reports(request):
    reports = _ErrorReports(request.param)
    yield reports
    reports.client.close()


def _frames(event):
    return [
        frame
        for exception in event["exception"]["values"]
        for frame in exception["stacktrace"]["frames"]
    ]


def _assert_clean(
    report, refusal, values, raised_in=("_platform_client", "_org_client"), held="group"
):
    """One refusal with nothing chained; the module's raising frame is in the
    report with its locals (``held`` among them); and none of ``values`` is
    anywhere in it."""
    (exception,) = report["exception"]["values"]
    assert exception["type"] == refusal
    raising = [
        frame
        for frame in exception["stacktrace"]["frames"]
        if frame["function"] in raised_in
    ]
    assert raising and held in raising[-1]["vars"]
    rendered = json.dumps(report, default=str)
    assert [value for value in values if value in rendered] == []


def _canary():
    held = CANARY  # under an ordinary name, as a parsed file or a value would be
    raise clients.ConnectorOAuthClientError(f"canary {len(held)}")


def test_secret_never_in_error_reports(
    organization, monkeypatch, tmp_path, error_reports
):
    """No frame on a refusal's traceback holds a secret, under any name: the
    report is clean even with no scrubber at all."""
    # The canary proves that a frame's locals reach the report, so the checks
    # below can fail.
    canary = error_reports.refusal(_canary)
    held = [f["vars"]["held"] for f in _frames(canary) if f["function"] == "_canary"]
    assert len(held) == 1
    assert CANARY in held[0]

    secrets = (PLATFORM_SECRET, FILE_SECRET, NOTION_SECRET, ORG_SECRET)
    # A secret pasted with a trailing newline (echo into a Secret), and a good
    # secret beside a bad client id.
    id_name, secret_name = clients.env_names("google")
    for client_id, secret in (
        (PLATFORM_ID, PLATFORM_SECRET + "\n"),
        (PLATFORM_ID + " ", PLATFORM_SECRET),
    ):
        monkeypatch.setenv(id_name, client_id)
        monkeypatch.setenv(secret_name, secret)
        report = error_reports.refusal(
            partial(clients.resolve, organization, "gmail", "google", **FIXED_HOST)
        )
        _assert_clean(report, "PlatformClientMisconfigured", secrets)
    monkeypatch.delenv(id_name)
    monkeypatch.delenv(secret_name)

    # A secrets file with one bad entry, a stray comma, a repeated key, or
    # nesting too deep. Any connect reads the whole file, good entries included.
    good = json.dumps({"google": _GOOD_ENTRY})
    bad_entry = {"client_id": FILE_ID, "client_secret": NOTION_SECRET + "\t"}
    for document, app_id, group in (
        (json.dumps({"google": _GOOD_ENTRY, "notion": bad_entry}), "gmail", "google"),
        (good[:-1] + ",}", "slack", None),
        (good[:-1] + ', "google": ' + json.dumps(_GOOD_ENTRY) + "}", "gmail", "google"),
        (good[:-1] + ', "deep": ' + "[" * 100_000 + "]" * 100_000 + "}", "slack", None),
    ):
        _secrets_file(tmp_path, monkeypatch, document)
        report = error_reports.refusal(
            partial(clients.resolve, organization, app_id, group, **FIXED_HOST)
        )
        _assert_clean(report, "PlatformClientMisconfigured", secrets)
    monkeypatch.delenv(clients.CLIENTS_FILE_ENV)

    # An org client under a key this deployment does not hold: no ciphertext
    # in the report either.
    token = Fernet(Fernet.generate_key()).encrypt(
        json.dumps({"v": ORG_SECRET}).encode()
    )
    ciphertext = token.decode("ascii")
    _org_client(
        organization,
        clients.org_client_key(client_group="linear"),
        encrypted=ENCRYPTED_PREFIX + ciphertext,
    )
    report = error_reports.refusal(
        partial(clients.resolve, organization, "linear", None, **FIXED_HOST)
    )
    _assert_clean(report, "OrgClientUnreadable", (*secrets, ciphertext))

    # A platform client resolved with other endpoints, refused for a provider
    # whose token host an organization sets: the refusing frame holds the
    # client, and the report shows it without its secret.
    _platform_env(monkeypatch, "snowflake")
    platform = clients.resolve(organization, "snowflake", None, **FIXED_HOST)
    report = error_reports.refusal(
        partial(
            clients.client_source,
            platform,
            registration=ALL_REGISTRATIONS,
            client_group="snowflake",
            **ORG_SET_HOST,
        )
    )
    _assert_clean(
        report,
        "ConnectorOAuthClientError",
        secrets,
        raised_in=("client_source",),
        held="resolved",
    )
    # The client did reach the report: its id (not a secret) is there.
    (frame,) = [f for f in _frames(report) if f["function"] == "client_source"]
    assert PLATFORM_ID in frame["vars"]["resolved"]
