"""ALERT-ACCESS-1 (TEN-1028-03): outbound webhook headers are secrets.

At the base every org Member read the header values an Owner set (they
usually carry the receiver's auth token), and could change the URL and keep
the Owner's headers and signing secret attached. Now:

- a read shows each header's name and a mask, to everyone; the values are
  never read back;
- create, update, delete and Send test are Owner/Admin only; reads stay open;
- sending the mask back keeps the saved value while the URL stays the same;
- a new URL (any change, path included: one host often serves many
  receivers) takes the header values and the secret again, or an explicit
  empty secret.

Synthetic data only: DNS answers are stubbed and no request leaves the test.
"""

from unittest.mock import MagicMock, patch

import pytest

from agentcc.models.webhook import AgentccWebhook
from agentcc.tests.r7_console_a_support import ClientFactory, user_with_level
from agentcc.tests.test_webhook_encrypted_fixtures import (
    make_webhook,
    stored_value,
)
from agentcc.tests.test_webhook_encrypted_fixtures import (
    webhook_encryption_key as webhook_encryption_key,
)
from tfc.constants.levels import Level

URL = "/agentcc/webhooks/"
# Test values, not real credentials; no hex letters.
TOKEN = "Bearer zqxjkvwm-receiver-token"
TEAM = "zq-team-seven"
SECRET = "whsec-zqxjkvwm"
HOOK_URL = "https://hooks.example.test/in"
DNS = {"hooks.example.test": ["93.184.216.34"], "hooks.other.test": ["93.184.216.35"]}


@pytest.fixture(autouse=True)
def dns(provider_dns):
    # Other names (localhost, the test services) go to the real resolver.
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


def _webhook(organization, **overrides):
    fields = {
        "organization": organization,
        "name": "access-hook",
        "url": HOOK_URL,
        "events": ["request.completed"],
        "headers": {"Authorization": TOKEN, "X-Team": TEAM},
        "secret": SECRET,
        **overrides,
    }
    return make_webhook(**fields)


def _shown(client, hook):
    response = client.get(f"{URL}{hook.id}/")
    assert response.status_code == 200, response.content
    return response.json()["result"]


def _console_edit(shown, **changes):
    """What CreateEditWebhookDialog.jsx sends on Update: the form it filled
    from the read, with the headers it showed, and the secret only when one
    was typed."""
    payload = {
        "name": shown["name"],
        "url": shown["url"],
        "events": shown["events"],
        "description": shown.get("description") or "",
        "headers": shown["headers"],
        **changes,
    }
    return payload


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestWebhookHeadersAreMaskedToEveryone:
    def test_reads_show_header_names_and_no_value(
        self, auth_client, member_client, organization
    ):
        hook = _webhook(organization)

        for client in (auth_client, member_client):
            for response in (client.get(URL), client.get(f"{URL}{hook.id}/")):
                assert response.status_code == 200, response.content
                body = response.content.decode()
                assert "zqxjkvwm" not in body and TEAM not in body

            shown = _shown(client, hook)
            assert set(shown["headers"]) == {"Authorization", "X-Team"}
            assert all(shown["headers"].values())
            assert "secret" not in shown

    def test_a_write_answers_masked_headers(self, admin_client):
        response = admin_client.post(
            URL,
            {
                "name": "access-new",
                "url": HOOK_URL,
                "events": ["request.completed"],
                "headers": {"Authorization": TOKEN},
                "secret": SECRET,
            },
            format="json",
        )

        assert response.status_code == 200, response.content
        assert "zqxjkvwm" not in response.content.decode()
        hook = AgentccWebhook.no_workspace_objects.get(name="access-new")
        assert stored_value(hook, "headers") == {"Authorization": TOKEN}
        assert stored_value(hook, "secret") == SECRET


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestWebhookWritesNeedOwnerOrAdmin:
    def test_member_cannot_create(self, member_client):
        response = member_client.post(
            URL,
            {"name": "member-hook", "url": HOOK_URL, "events": ["request.completed"]},
            format="json",
        )

        assert response.status_code == 403, response.content
        assert not AgentccWebhook.no_workspace_objects.filter(
            name="member-hook"
        ).exists()

    @pytest.mark.parametrize(
        "change",
        [{"url": "https://hooks.other.test/in"}, {"headers": {"Authorization": "x"}}],
    )
    def test_member_cannot_change_an_owners_webhook(
        self, member_client, organization, change
    ):
        hook = _webhook(organization)

        response = member_client.patch(f"{URL}{hook.id}/", change, format="json")

        assert response.status_code == 403, response.content
        hook.refresh_from_db()
        assert (
            hook.url,
            stored_value(hook, "headers"),
            stored_value(hook, "secret"),
        ) == (
            HOOK_URL,
            {"Authorization": TOKEN, "X-Team": TEAM},
            SECRET,
        )

    def test_member_cannot_delete(self, member_client, organization):
        hook = _webhook(organization)

        response = member_client.delete(f"{URL}{hook.id}/")

        assert response.status_code == 403, response.content
        hook.refresh_from_db()
        assert hook.deleted is False

    @patch("agentcc.views.webhook_outbound.build_ssrf_safe_session")
    def test_member_cannot_send_a_test(
        self, session_builder, member_client, organization
    ):
        hook = _webhook(organization)

        response = member_client.post(f"{URL}{hook.id}/test/")

        assert response.status_code == 403, response.content
        session_builder.assert_not_called()

    @patch("agentcc.views.webhook_outbound.build_ssrf_safe_session")
    def test_admin_sends_a_test_with_the_saved_values(
        self, session_builder, admin_client, organization
    ):
        hook = _webhook(organization)
        session = MagicMock()
        session.post.return_value = MagicMock(status_code=204)
        session_builder.return_value = session

        response = admin_client.post(f"{URL}{hook.id}/test/")

        assert response.status_code == 200, response.content
        sent = session.post.call_args.kwargs["headers"]
        assert sent["Authorization"] == TOKEN and sent["X-Team"] == TEAM
        assert sent["X-Agentcc-Signature"].startswith("sha256=")


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestWebhookSecretsStayWithTheirUrl:
    def test_a_new_url_alone_is_refused(self, auth_client, organization):
        hook = _webhook(organization)

        response = auth_client.patch(
            f"{URL}{hook.id}/", {"url": "https://hooks.other.test/in"}, format="json"
        )

        assert response.status_code == 400, response.content
        hook.refresh_from_db()
        assert hook.url == HOOK_URL

    def test_a_new_path_on_the_same_host_is_a_new_url(self, auth_client, organization):
        hook = _webhook(organization)

        response = auth_client.patch(
            f"{URL}{hook.id}/",
            {"url": "https://hooks.example.test/someone-else"},
            format="json",
        )

        assert response.status_code == 400, response.content
        hook.refresh_from_db()
        assert hook.url == HOOK_URL

    def test_the_console_edit_to_a_new_url_takes_the_values_again(
        self, auth_client, organization
    ):
        hook = _webhook(organization)
        shown = _shown(auth_client, hook)

        refused = auth_client.patch(
            f"{URL}{hook.id}/",
            _console_edit(shown, url="https://hooks.other.test/in"),
            format="json",
        )
        assert refused.status_code == 400, refused.content
        hook.refresh_from_db()
        assert (hook.url, stored_value(hook, "headers")["Authorization"]) == (
            HOOK_URL,
            TOKEN,
        )

        no_secret = auth_client.patch(
            f"{URL}{hook.id}/",
            _console_edit(
                shown,
                url="https://hooks.other.test/in",
                headers={"Authorization": "Bearer new-zq", "X-Team": "t2"},
            ),
            format="json",
        )
        assert no_secret.status_code == 400, no_secret.content

        accepted = auth_client.patch(
            f"{URL}{hook.id}/",
            _console_edit(
                shown,
                url="https://hooks.other.test/in",
                headers={"Authorization": "Bearer new-zq", "X-Team": "t2"},
                secret="whsec-new-zq",
            ),
            format="json",
        )
        assert accepted.status_code == 200, accepted.content
        hook.refresh_from_db()
        assert hook.url == "https://hooks.other.test/in"
        assert stored_value(hook, "headers") == {
            "Authorization": "Bearer new-zq",
            "X-Team": "t2",
        }
        assert stored_value(hook, "secret") == "whsec-new-zq"

    def test_a_new_url_may_clear_the_secret_and_headers(
        self, auth_client, organization
    ):
        hook = _webhook(organization)

        response = auth_client.patch(
            f"{URL}{hook.id}/",
            {"url": "https://hooks.other.test/in", "headers": {}, "secret": ""},
            format="json",
        )

        assert response.status_code == 200, response.content
        hook.refresh_from_db()
        assert (
            hook.url,
            stored_value(hook, "headers"),
            stored_value(hook, "secret"),
        ) == (
            "https://hooks.other.test/in",
            {},
            "",
        )

    def test_the_console_edit_on_the_same_url_keeps_the_saved_values(
        self, auth_client, organization
    ):
        hook = _webhook(organization)
        shown = _shown(auth_client, hook)

        response = auth_client.patch(
            f"{URL}{hook.id}/",
            _console_edit(shown, name="renamed-hook", description="now described"),
            format="json",
        )

        assert response.status_code == 200, response.content
        hook.refresh_from_db()
        assert hook.name == "renamed-hook"
        assert stored_value(hook, "headers") == {"Authorization": TOKEN, "X-Team": TEAM}
        assert stored_value(hook, "secret") == SECRET

    def test_the_console_edit_may_change_one_header_and_keep_the_other(
        self, auth_client, organization
    ):
        hook = _webhook(organization)
        shown = _shown(auth_client, hook)
        headers = {**shown["headers"], "X-Team": "t3"}

        response = auth_client.patch(
            f"{URL}{hook.id}/", _console_edit(shown, headers=headers), format="json"
        )

        assert response.status_code == 200, response.content
        hook.refresh_from_db()
        assert stored_value(hook, "headers") == {"Authorization": TOKEN, "X-Team": "t3"}

    def test_a_masked_value_under_a_new_header_name_is_refused(
        self, auth_client, organization
    ):
        hook = _webhook(organization)
        shown = _shown(auth_client, hook)
        mask = shown["headers"]["Authorization"]

        response = auth_client.patch(
            f"{URL}{hook.id}/",
            {"headers": {"X-Renamed": mask}},
            format="json",
        )

        assert response.status_code == 400, response.content
        hook.refresh_from_db()
        assert stored_value(hook, "headers") == {"Authorization": TOKEN, "X-Team": TEAM}

    def test_a_webhook_with_no_secret_or_headers_changes_url_freely(
        self, auth_client, organization
    ):
        hook = _webhook(organization, headers={}, secret="")

        response = auth_client.put(
            f"{URL}{hook.id}/",
            {
                "name": "plain-hook",
                "url": "https://hooks.other.test/in",
                "events": ["error.occurred"],
            },
            format="json",
        )

        assert response.status_code == 200, response.content
        hook.refresh_from_db()
        assert hook.url == "https://hooks.other.test/in"
