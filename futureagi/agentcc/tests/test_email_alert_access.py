"""ALERT-ACCESS-1 (TEN-1028-01, TEN-1028-02): who may change an email alert,
what a read shows of its secrets, and where its SMTP credentials may go.

At the base any org Member could create, retarget, delete or test an alert
(IsAuthenticated only); a host or port change kept the stored SMTP username
and password; the SMTP host was never checked; the Send test answered the raw
provider error; and a read showed 8 characters of a stored password or API
key. Now:

- create, update, delete and Send test are Owner/Admin only; reads stay open;
- a read shows no character of a stored password or API key, and sending that
  masked value back keeps the stored one;
- a new SMTP host or port takes the username and password again (the saved
  ones are only for the destination they were saved for); a provider change
  drops the saved credentials;
- an SMTP host is refused on save and again on send when it is (or resolves
  to) loopback, link-local, cloud metadata or, without the self-hosted
  opt-in, a private address; the send connects to the address it checked;
- the Send test answers a fixed message, never the provider's text.

Synthetic data only: DNS answers are stubbed, sockets are refused in-process,
no mail leaves the test.
"""

import ipaddress
import smtplib
import socket
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet

from agentcc.models.email_alert import AgentccEmailAlert
from agentcc.services.email_sender import deliver_email
from agentcc.services.url_safety import ALLOW_PRIVATE_PROVIDER_URLS_ENV
from agentcc.tests.r7_console_a_support import ClientFactory, user_with_level
from integrations.services.credentials import CredentialManager
from tfc.constants.levels import Level

URL = "/agentcc/email-alerts/"
# Test values, not real credentials; no hex letters, so no UUID in a response
# can contain a slice of them by chance.
PASSWORD = "zyxwvutsrqpo"
API_KEY = "re_zqxjkvwmzqxjkvwm"
SMTP_CONFIG = {
    "host": "smtp.public.test",
    "port": 587,
    "username": "alerts-user",
    "password": PASSWORD,
    "from_email": "alerts@example.com",
    "use_tls": True,
}
DNS = {
    "smtp.public.test": ["93.184.216.34"],
    "smtp.other.test": ["93.184.216.35"],
    "smtp.lan.test": ["10.0.0.5"],
    "smtp.meta.test": ["169.254.169.254"],
    "smtp.loop.test": ["127.0.0.1"],
}
# Hosts a save or a send must refuse with the opt-in off.
REFUSED_HOSTS = [
    "127.0.0.1",
    "localhost",
    "smtp.loop.test",
    "169.254.169.254",
    "smtp.meta.test",
    "metadata.google.internal",
    "10.0.0.5",
    "smtp.lan.test",
]


@pytest.fixture(autouse=True)
def _encryption_key(settings):
    settings.INTEGRATION_ENCRYPTION_KEY = Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def dns(provider_dns, monkeypatch):
    monkeypatch.delenv(ALLOW_PRIVATE_PROVIDER_URLS_ENV, raising=False)
    # Other names (localhost, the test services) go to the real resolver;
    # *.invalid never resolves.
    return provider_dns(DNS, passthrough=True)


@pytest.fixture
def clients():
    factory = ClientFactory()
    yield factory
    factory.close()


@pytest.fixture
def member_client(organization, workspace, clients):
    member, member_workspace = user_with_level(organization, Level.MEMBER)
    return clients(member, member_workspace)


@pytest.fixture
def admin_client(organization, workspace, clients):
    admin, admin_workspace = user_with_level(organization, Level.ADMIN)
    return clients(admin, admin_workspace)


def _alert(organization, provider="smtp", config=None, **fields):
    return AgentccEmailAlert.no_workspace_objects.create(
        organization=organization,
        name=fields.pop("name", "access-alert"),
        recipients=fields.pop("recipients", ["ops@example.com"]),
        events=["error.occurred"],
        provider=provider,
        encrypted_config=CredentialManager.encrypt(
            dict(SMTP_CONFIG) if config is None else config
        ),
        **fields,
    )


def _stored(alert):
    alert.refresh_from_db()
    return CredentialManager.decrypt(bytes(alert.encrypted_config))


def _payload(**overrides):
    return {
        "name": "access-new",
        "recipients": ["ops@example.com"],
        "events": ["error.occurred"],
        "provider": "smtp",
        "provider_config": dict(SMTP_CONFIG),
        **overrides,
    }


def _console_edit(alert, **provider_config):
    """What EmailAlertDialog.jsx sends on Update for an SMTP alert: the host,
    port, from address and the username it shows, TLS on, and the password
    only when one was typed."""
    config = {
        "host": SMTP_CONFIG["host"],
        "port": SMTP_CONFIG["port"],
        "from_email": SMTP_CONFIG["from_email"],
        "use_tls": True,
        "username": SMTP_CONFIG["username"],
        **provider_config,
    }
    return {
        "name": alert.name,
        "recipients": alert.recipients,
        "events": alert.events,
        "thresholds": {},
        "provider": "smtp",
        "provider_config": config,
        "cooldown_minutes": 5,
        "is_active": True,
    }


def _slices(secret):
    return [secret[i : i + 4] for i in range(len(secret) - 3)]


# --- who may change an alert ------------------------------------------------------


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestEmailAlertWritesNeedOwnerOrAdmin:
    def test_member_cannot_create(self, member_client):
        response = member_client.post(URL, _payload(), format="json")

        assert response.status_code == 403, response.content
        assert not AgentccEmailAlert.no_workspace_objects.filter(
            name="access-new"
        ).exists()

    @pytest.mark.parametrize("method", ["patch", "put"])
    def test_member_cannot_retarget_an_owners_alert(
        self, member_client, organization, method
    ):
        alert = _alert(organization)

        response = getattr(member_client, method)(
            f"{URL}{alert.id}/",
            _payload(
                name=alert.name,
                provider_config={"host": "smtp.other.test", "port": 2525},
            ),
            format="json",
        )

        assert response.status_code == 403, response.content
        stored = _stored(alert)
        assert (stored["host"], stored["port"]) == ("smtp.public.test", 587)
        assert stored["password"] == PASSWORD

    def test_member_cannot_delete(self, member_client, organization):
        alert = _alert(organization)

        response = member_client.delete(f"{URL}{alert.id}/")

        assert response.status_code == 403, response.content
        alert.refresh_from_db()
        assert alert.deleted is False

    def test_member_cannot_send_a_test(self, member_client, organization):
        alert = _alert(organization)

        with patch("agentcc.services.email_sender._send_via_smtp") as smtp:
            response = member_client.post(f"{URL}{alert.id}/test/", {}, format="json")

        assert response.status_code == 403, response.content
        smtp.assert_not_called()

    def test_member_still_reads(self, member_client, organization):
        alert = _alert(organization)

        listed = member_client.get(URL)
        shown = member_client.get(f"{URL}{alert.id}/")

        assert listed.status_code == 200, listed.content
        assert [row["id"] for row in listed.json()["result"]] == [str(alert.id)]
        assert shown.status_code == 200, shown.content
        assert shown.json()["result"]["provider_config"]["host"] == "smtp.public.test"

    def test_admin_creates_updates_tests_and_deletes(self, admin_client):
        created = admin_client.post(URL, _payload(), format="json")
        assert created.status_code == 200, created.content
        alert_id = created.json()["result"]["id"]

        updated = admin_client.patch(
            f"{URL}{alert_id}/", {"cooldown_minutes": 9}, format="json"
        )
        assert updated.status_code == 200, updated.content

        with patch(
            "agentcc.services.email_sender._send_via_smtp",
            return_value={"success": True},
        ):
            tested = admin_client.post(f"{URL}{alert_id}/test/", {}, format="json")
        assert tested.status_code == 200, tested.content

        deleted = admin_client.delete(f"{URL}{alert_id}/")
        assert deleted.status_code == 200, deleted.content


# --- what a read shows of a secret --------------------------------------------------


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestEmailAlertReadsShowNoSecret:
    @pytest.mark.parametrize(
        ("provider", "config", "secret_key"),
        [
            ("smtp", SMTP_CONFIG, "password"),
            ("resend", {"api_key": API_KEY, "from_email": "a@example.com"}, "api_key"),
            (
                "sendgrid",
                {"api_key": API_KEY, "from_email": "a@example.com"},
                "api_key",
            ),
        ],
    )
    def test_no_character_of_a_stored_secret_is_read(
        self, auth_client, member_client, organization, provider, config, secret_key
    ):
        alert = _alert(organization, provider=provider, config=dict(config))
        secret = config[secret_key]

        for client in (auth_client, member_client):
            for response in (client.get(URL), client.get(f"{URL}{alert.id}/")):
                assert response.status_code == 200, response.content
                body = response.content.decode()
                leaked = [piece for piece in _slices(secret) if piece in body]
                assert leaked == [], f"{provider} read shows {leaked}"

        shown = auth_client.get(f"{URL}{alert.id}/").json()["result"]["provider_config"]
        # It still says a secret is set.
        assert shown[secret_key]
        assert shown["from_email"] == config["from_email"]

    def test_a_write_answers_the_masked_config(self, auth_client):
        response = auth_client.post(URL, _payload(), format="json")

        assert response.status_code == 200, response.content
        body = response.content.decode()
        assert [piece for piece in _slices(PASSWORD) if piece in body] == []

    def test_sending_the_masked_value_back_keeps_the_stored_secret(
        self, auth_client, organization
    ):
        alert = _alert(organization)
        shown = auth_client.get(f"{URL}{alert.id}/").json()["result"]["provider_config"]

        response = auth_client.patch(
            f"{URL}{alert.id}/", {"provider_config": shown}, format="json"
        )

        assert response.status_code == 200, response.content
        assert _stored(alert)["password"] == PASSWORD

    def test_sending_the_masked_api_key_back_keeps_it(self, auth_client, organization):
        alert = _alert(
            organization,
            provider="resend",
            config={"api_key": API_KEY, "from_email": "a@example.com"},
        )
        shown = auth_client.get(f"{URL}{alert.id}/").json()["result"]["provider_config"]

        response = auth_client.put(
            f"{URL}{alert.id}/",
            _payload(name=alert.name, provider="resend", provider_config=shown),
            format="json",
        )

        assert response.status_code == 200, response.content
        assert _stored(alert)["api_key"] == API_KEY


# --- where saved SMTP credentials may go -------------------------------------------


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestSmtpCredentialsStayWithTheirDestination:
    @pytest.mark.parametrize(
        "change",
        [{"host": "smtp.other.test"}, {"port": 2525}, {"host": "SMTP.OTHER.TEST."}],
    )
    def test_a_new_host_or_port_without_credentials_is_refused(
        self, auth_client, organization, change
    ):
        alert = _alert(organization)

        response = auth_client.patch(
            f"{URL}{alert.id}/", {"provider_config": change}, format="json"
        )

        assert response.status_code == 400, response.content
        stored = _stored(alert)
        assert (stored["host"], stored["port"]) == ("smtp.public.test", 587)
        assert stored["password"] == PASSWORD

    def test_the_console_edit_to_a_new_host_takes_the_password_again(
        self, auth_client, organization
    ):
        alert = _alert(organization)

        refused = auth_client.patch(
            f"{URL}{alert.id}/",
            _console_edit(alert, host="smtp.other.test"),
            format="json",
        )
        assert refused.status_code == 400, refused.content
        assert "password" in refused.json()["message"].lower()
        assert _stored(alert)["host"] == "smtp.public.test"

        accepted = auth_client.patch(
            f"{URL}{alert.id}/",
            _console_edit(alert, host="smtp.other.test", password="new-pass-zq"),
            format="json",
        )
        assert accepted.status_code == 200, accepted.content
        stored = _stored(alert)
        assert stored["host"] == "smtp.other.test"
        assert (stored["username"], stored["password"]) == (
            "alerts-user",
            "new-pass-zq",
        )

    def test_new_credentials_with_a_new_host_replace_the_saved_ones(
        self, auth_client, organization
    ):
        alert = _alert(organization)

        response = auth_client.patch(
            f"{URL}{alert.id}/",
            {
                "provider_config": {
                    "host": "smtp.other.test",
                    "port": 465,
                    "username": "other-user",
                    "password": "other-pass-zq",
                }
            },
            format="json",
        )

        assert response.status_code == 200, response.content
        stored = _stored(alert)
        assert stored == {
            **SMTP_CONFIG,
            "host": "smtp.other.test",
            "port": 465,
            "username": "other-user",
            "password": "other-pass-zq",
        }

    def test_the_console_edit_on_the_same_host_keeps_the_password(
        self, auth_client, organization
    ):
        alert = _alert(organization)

        response = auth_client.patch(
            f"{URL}{alert.id}/",
            _console_edit(alert, from_email="ops@example.com"),
            format="json",
        )

        assert response.status_code == 200, response.content
        stored = _stored(alert)
        assert stored["password"] == PASSWORD
        assert stored["from_email"] == "ops@example.com"

    def test_an_alert_without_saved_credentials_may_change_host(
        self, auth_client, organization
    ):
        config = {
            k: v for k, v in SMTP_CONFIG.items() if k not in ("username", "password")
        }
        alert = _alert(organization, config=config)

        response = auth_client.patch(
            f"{URL}{alert.id}/",
            {"provider_config": {"host": "smtp.other.test"}},
            format="json",
        )

        assert response.status_code == 200, response.content
        assert _stored(alert)["host"] == "smtp.other.test"

    def test_a_provider_change_drops_the_saved_credentials(
        self, auth_client, organization
    ):
        alert = _alert(organization)

        response = auth_client.patch(
            f"{URL}{alert.id}/",
            {"provider": "resend", "provider_config": {"api_key": "re_new_zq"}},
            format="json",
        )

        assert response.status_code == 200, response.content
        stored = _stored(alert)
        assert "password" not in stored and "username" not in stored
        assert stored["api_key"] == "re_new_zq"
        assert stored["from_email"] == SMTP_CONFIG["from_email"]

    def test_a_provider_change_alone_drops_the_saved_api_key(
        self, auth_client, organization
    ):
        alert = _alert(
            organization,
            provider="resend",
            config={"api_key": API_KEY, "from_email": "a@example.com"},
        )

        response = auth_client.patch(
            f"{URL}{alert.id}/", {"provider": "sendgrid"}, format="json"
        )

        assert response.status_code == 200, response.content
        assert _stored(alert) == {"from_email": "a@example.com"}


# --- which SMTP hosts may be saved ---------------------------------------------------


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestSmtpHostIsCheckedOnSave:
    @pytest.mark.parametrize("host", REFUSED_HOSTS)
    def test_an_internal_host_is_not_created(self, auth_client, host):
        response = auth_client.post(
            URL,
            _payload(provider_config={**SMTP_CONFIG, "host": host}),
            format="json",
        )

        assert response.status_code == 400, response.content
        assert not AgentccEmailAlert.no_workspace_objects.filter(
            name="access-new"
        ).exists()

    @pytest.mark.parametrize("host", REFUSED_HOSTS)
    def test_an_internal_host_is_not_saved_on_update(
        self, auth_client, organization, host
    ):
        alert = _alert(organization)

        response = auth_client.patch(
            f"{URL}{alert.id}/",
            {"provider_config": {**SMTP_CONFIG, "host": host}},
            format="json",
        )

        assert response.status_code == 400, response.content
        assert _stored(alert)["host"] == "smtp.public.test"

    @pytest.mark.parametrize("port", [6379, 5432])
    def test_a_database_port_is_refused(self, auth_client, port):
        response = auth_client.post(
            URL,
            _payload(provider_config={**SMTP_CONFIG, "port": port}),
            format="json",
        )

        assert response.status_code == 400, response.content

    def test_a_private_host_needs_the_self_hosted_opt_in(
        self, auth_client, monkeypatch
    ):
        monkeypatch.setenv(ALLOW_PRIVATE_PROVIDER_URLS_ENV, "true")

        private = auth_client.post(
            URL,
            _payload(provider_config={**SMTP_CONFIG, "host": "smtp.lan.test"}),
            format="json",
        )
        loopback = auth_client.post(
            URL,
            _payload(
                name="access-loop", provider_config={**SMTP_CONFIG, "host": "127.0.0.1"}
            ),
            format="json",
        )

        assert private.status_code == 200, private.content
        assert loopback.status_code == 400, loopback.content

    @pytest.mark.parametrize(
        "host", ["smtp.public.test", "smtp.not-resolvable-here.invalid"]
    )
    def test_a_public_or_unresolvable_host_is_saved(self, auth_client, host):
        response = auth_client.post(
            URL,
            _payload(provider_config={**SMTP_CONFIG, "host": host}),
            format="json",
        )

        assert response.status_code == 200, response.content

    def test_a_host_saved_before_stays_editable(self, auth_client, organization):
        alert = _alert(organization, config={**SMTP_CONFIG, "host": "10.0.0.5"})

        renamed = auth_client.patch(
            f"{URL}{alert.id}/", {"name": "renamed-alert"}, format="json"
        )
        new_sender = auth_client.patch(
            f"{URL}{alert.id}/",
            _console_edit(alert, host="10.0.0.5", from_email="ops@example.com"),
            format="json",
        )

        assert renamed.status_code == 200, renamed.content
        assert new_sender.status_code == 200, new_sender.content
        stored = _stored(alert)
        assert stored["host"] == "10.0.0.5"
        assert stored["password"] == PASSWORD


# --- the send checks the host again and connects to what it checked ----------------


def _answer(addr, port):
    family = socket.AF_INET6 if ":" in addr else socket.AF_INET
    number = int(port) if isinstance(port, int) or str(port or "").isdigit() else 0
    return (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (addr, number))


@pytest.fixture
def refuse_connects():
    """Stub smtplib's actual dial seam before the process socket guard."""
    connects = []

    def create_connection(address, *args, **kwargs):
        connects.append(address)
        raise OSError("synthetic: no network in this test")

    with patch("socket.create_connection", create_connection):
        yield connects


@pytest.mark.django_db
class TestSmtpSendChecksAndPinsTheAddress:
    @pytest.mark.parametrize("host", REFUSED_HOSTS)
    def test_an_internal_host_is_never_connected(
        self, organization, refuse_connects, host
    ):
        alert = _alert(organization, config={**SMTP_CONFIG, "host": host})

        result = deliver_email(alert, ["ops@example.com"], "subject", "<p>body</p>")

        assert result["success"] is False
        assert refuse_connects == []

    def test_the_connect_goes_to_the_address_that_was_checked(
        self, organization, refuse_connects
    ):
        # The first answer is public; every later one is loopback.
        lookups = []

        def getaddrinfo(host, port=None, *args, **kwargs):
            lookups.append(host)
            try:
                return [_answer(str(ipaddress.ip_address(host)), port)]
            except ValueError:
                pass
            if host != "mail.rebind.test":
                raise socket.gaierror(socket.EAI_NONAME, "no such host")
            first = lookups.count(host) == 1
            return [_answer("93.184.216.34" if first else "127.0.0.1", port)]

        alert = _alert(organization, config={**SMTP_CONFIG, "host": "mail.rebind.test"})
        with patch("socket.getaddrinfo", getaddrinfo):
            result = deliver_email(alert, ["ops@example.com"], "subject", "<p>body</p>")

        assert result["success"] is False
        assert refuse_connects == [("93.184.216.34", 587)]
        assert lookups.count("mail.rebind.test") == 1


# --- the Send test shows no provider text --------------------------------------------


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestSendTestAnswersAFixedMessage:
    def test_an_smtp_failure_is_reported_without_its_text(
        self, auth_client, organization
    ):
        alert = _alert(organization)
        failure = smtplib.SMTPAuthenticationError(
            535, b"5.7.8 zq-banner-from-mail-host 10.9.8.7"
        )

        with patch("agentcc.services.email_sender._send_via_smtp", side_effect=failure):
            response = auth_client.post(f"{URL}{alert.id}/test/", {}, format="json")

        assert response.status_code == 400, response.content
        body = response.content.decode()
        for raw in ("zq-banner", "10.9.8.7", "535", "SMTPAuthentication"):
            assert raw not in body

    def test_a_provider_rejection_is_reported_without_its_body(
        self, auth_client, organization
    ):
        alert = _alert(
            organization,
            provider="sendgrid",
            config={"api_key": API_KEY, "from_email": "a@example.com"},
        )
        rejected = MagicMock(status_code=401, text="zq-provider-body: account 4242")

        with patch(
            "agentcc.services.email_sender.requests.post", return_value=rejected
        ):
            response = auth_client.post(f"{URL}{alert.id}/test/", {}, format="json")

        assert response.status_code == 400, response.content
        body = response.content.decode()
        assert "zq-provider-body" not in body and "4242" not in body

    def test_a_refused_host_is_reported_without_its_address(
        self, auth_client, organization
    ):
        alert = _alert(organization, config={**SMTP_CONFIG, "host": "smtp.meta.test"})
        # smtplib connects through socket.create_connection; the request's own
        # clients (database, cache) do not.
        dialed = []

        def create_connection(address, *args, **kwargs):
            dialed.append(address)
            raise OSError("synthetic: no network in this test")

        with patch("socket.create_connection", create_connection):
            response = auth_client.post(f"{URL}{alert.id}/test/", {}, format="json")

        assert response.status_code == 400, response.content
        assert "169.254" not in response.content.decode()
        assert dialed == []

    def test_a_fixed_reason_is_still_given(self, auth_client, organization):
        alert = _alert(organization, recipients=[])

        response = auth_client.post(f"{URL}{alert.id}/test/", {}, format="json")

        assert response.status_code == 400, response.content
        assert response.json()["message"] == "No recipients configured."
