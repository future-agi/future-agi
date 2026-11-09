"""ALERT-ACCESS-1 round 2: webhook headers saved as a list of [name, value] pairs.

Until ALERT-ACCESS-1 the API stored ``headers`` as sent, and delivery's
``dict.update`` reads a list of ``[name, value]`` pairs the same way as a JSON
object, so rows of that shape exist. Round 1 masked and checked JSON objects
only: such a row's header values were read back verbatim, and a URL-only change
kept them for the new URL. Now one function reads both shapes for the read
mask, the masked-value retention, the URL-change check, validation and
delivery. Any other saved shape reads as no headers, still counts as saved
headers for a URL change, and is never sent.

Synthetic data only: DNS answers are stubbed and no request leaves the test.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from agentcc.models.webhook import AgentccWebhook, AgentccWebhookEvent
from agentcc.services.secret_mask import MASKED_SECRET
from agentcc.services.webhook_delivery import deliver_webhook_events
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
HOOK_URL = "https://hooks.example.test/in"
OTHER_URL = "https://hooks.other.test/in"
PAIRS = [["X-Receiver-Token", TOKEN], ["X-Team", TEAM]]
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


def _webhook(organization, headers, **overrides):
    """A row as the API saved it before ALERT-ACCESS-1 (``headers`` as sent).
    No signing secret by default: the URL-change check must not lean on it."""
    return make_webhook(
        organization=organization,
        name=overrides.pop("name", "pairs-hook"),
        url=overrides.pop("url", HOOK_URL),
        events=["error.occurred"],
        headers=headers,
        secret=overrides.pop("secret", ""),
        **overrides,
    )


def _shown(client, hook):
    response = client.get(f"{URL}{hook.id}/")
    assert response.status_code == 200, response.content
    return response.json()["result"]


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestPairListHeadersAreMasked:
    def test_reads_show_names_and_no_value(
        self, auth_client, member_client, organization
    ):
        hook = _webhook(organization, PAIRS)

        for client in (auth_client, member_client):
            for response in (client.get(URL), client.get(f"{URL}{hook.id}/")):
                assert response.status_code == 200, response.content
                body = response.content.decode()
                assert "zqxjkvwm" not in body and TEAM not in body
            assert _shown(client, hook)["headers"] == {
                "X-Receiver-Token": MASKED_SECRET,
                "X-Team": MASKED_SECRET,
            }

    @pytest.mark.parametrize(
        "stored",
        [
            "X-Receiver-Token: Bearer zqxjkvwm-receiver-token",
            [["X-Receiver-Token", TOKEN, "extra"]],
            [[TOKEN]],
            {"nested": {"X-Receiver-Token": TOKEN}},
        ],
    )
    def test_an_unsupported_saved_shape_reads_without_its_values(
        self, member_client, organization, stored
    ):
        hook = _webhook(organization, stored)

        body = member_client.get(f"{URL}{hook.id}/").content.decode()

        assert "zqxjkvwm" not in body
        shown = _shown(member_client, hook)["headers"]
        assert isinstance(shown, dict)
        assert set(shown.values()) <= {MASKED_SECRET}


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestPairListHeadersStayWithTheirUrl:
    def test_a_url_only_change_is_refused(self, auth_client, organization):
        hook = _webhook(organization, PAIRS)

        response = auth_client.patch(
            f"{URL}{hook.id}/", {"url": OTHER_URL}, format="json"
        )

        assert response.status_code == 400, response.content
        hook.refresh_from_db()
        assert (hook.url, stored_value(hook, "headers")) == (HOOK_URL, dict(PAIRS))

    def test_a_put_to_a_new_url_without_headers_is_refused(
        self, auth_client, organization
    ):
        hook = _webhook(organization, PAIRS)

        response = auth_client.put(
            f"{URL}{hook.id}/",
            {"name": "pairs-hook", "url": OTHER_URL, "events": ["error.occurred"]},
            format="json",
        )

        assert response.status_code == 400, response.content
        hook.refresh_from_db()
        assert (hook.url, stored_value(hook, "headers")) == (HOOK_URL, dict(PAIRS))

    def test_masked_values_do_not_follow_a_new_url(self, auth_client, organization):
        hook = _webhook(organization, PAIRS)

        response = auth_client.patch(
            f"{URL}{hook.id}/",
            {"url": OTHER_URL, "headers": {"X-Receiver-Token": MASKED_SECRET}},
            format="json",
        )

        assert response.status_code == 400, response.content
        hook.refresh_from_db()
        assert (hook.url, stored_value(hook, "headers")) == (HOOK_URL, dict(PAIRS))

    def test_a_new_url_with_new_values_replaces_them(self, auth_client, organization):
        hook = _webhook(organization, PAIRS)

        response = auth_client.patch(
            f"{URL}{hook.id}/",
            {"url": OTHER_URL, "headers": {"X-Receiver-Token": "Bearer new-zq"}},
            format="json",
        )

        assert response.status_code == 200, response.content
        hook.refresh_from_db()
        assert hook.url == OTHER_URL
        assert stored_value(hook, "headers") == {"X-Receiver-Token": "Bearer new-zq"}

    def test_an_unsupported_saved_shape_still_blocks_a_url_only_change(
        self, auth_client, organization
    ):
        stored = "X-Receiver-Token: Bearer zqxjkvwm-receiver-token"
        hook = _webhook(organization, stored)

        response = auth_client.patch(
            f"{URL}{hook.id}/", {"url": OTHER_URL}, format="json"
        )

        assert response.status_code == 400, response.content
        hook.refresh_from_db()
        assert (hook.url, stored_value(hook, "headers")) == (HOOK_URL, stored)

    def test_the_console_edit_on_the_same_url_keeps_the_values(
        self, auth_client, organization
    ):
        hook = _webhook(organization, PAIRS)
        shown = _shown(auth_client, hook)

        response = auth_client.patch(
            f"{URL}{hook.id}/",
            {
                "name": "renamed-hook",
                "url": shown["url"],
                "events": shown["events"],
                "description": "",
                "headers": shown["headers"],
            },
            format="json",
        )

        assert response.status_code == 200, response.content
        assert "zqxjkvwm" not in response.content.decode()
        hook.refresh_from_db()
        assert hook.name == "renamed-hook"
        assert stored_value(hook, "headers") == {
            "X-Receiver-Token": TOKEN,
            "X-Team": TEAM,
        }

    def test_a_masked_value_resolves_by_name_in_any_case(
        self, auth_client, organization
    ):
        hook = _webhook(organization, PAIRS)

        response = auth_client.patch(
            f"{URL}{hook.id}/",
            {"headers": {"x-receiver-token": MASKED_SECRET, "X-Team": "t2"}},
            format="json",
        )

        assert response.status_code == 200, response.content
        hook.refresh_from_db()
        assert stored_value(hook, "headers") == {
            "x-receiver-token": TOKEN,
            "X-Team": "t2",
        }


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestPairListHeadersAreAcceptedOnWrite:
    def test_a_pair_list_is_saved_as_an_object(self, admin_client):
        response = admin_client.post(
            URL,
            {
                "name": "pairs-new",
                "url": HOOK_URL,
                "events": ["error.occurred"],
                "headers": [["Authorization", TOKEN]],
            },
            format="json",
        )

        assert response.status_code == 200, response.content
        assert "zqxjkvwm" not in response.content.decode()
        assert response.json()["result"]["headers"] == {"Authorization": MASKED_SECRET}
        hook = AgentccWebhook.no_workspace_objects.get(name="pairs-new")
        assert stored_value(hook, "headers") == {"Authorization": TOKEN}

    def test_the_last_pair_for_a_name_wins_as_delivery_reads_it(self, admin_client):
        response = admin_client.post(
            URL,
            {
                "name": "pairs-dup",
                "url": HOOK_URL,
                "events": ["error.occurred"],
                "headers": [["X-Team", "first"], ["X-Team", "second"]],
            },
            format="json",
        )

        assert response.status_code == 200, response.content
        hook = AgentccWebhook.no_workspace_objects.get(name="pairs-dup")
        assert stored_value(hook, "headers") == {"X-Team": "second"}

    @pytest.mark.parametrize(
        "headers",
        [
            "Authorization: Bearer x",
            [["Authorization"]],
            [["Authorization", "x", "y"]],
            [[7, "x"]],
            ["Authorization"],
            5,
        ],
    )
    def test_other_shapes_are_refused(self, admin_client, headers):
        response = admin_client.post(
            URL,
            {
                "name": "shape-hook",
                "url": HOOK_URL,
                "events": ["error.occurred"],
                "headers": headers,
            },
            format="json",
        )

        assert response.status_code == 400, response.content
        assert not AgentccWebhook.no_workspace_objects.filter(
            name="shape-hook"
        ).exists()


# --- delivery reads the same headers ---------------------------------------------


@pytest.fixture
def posts():
    """Every webhook POST delivery makes, captured, never sent."""
    sent = []
    session = MagicMock()

    def _post(url, data=None, headers=None, timeout=None):
        sent.append({"url": url, "headers": dict(headers or {}), "body": data})
        return MagicMock(status_code=200, text="ok")

    session.post.side_effect = _post
    with (
        patch("agentcc.services.webhook_delivery.ensure_public_http_url"),
        patch(
            "agentcc.services.webhook_delivery.build_ssrf_safe_session",
            return_value=session,
        ),
    ):
        yield sent


def _event(organization, hook):
    return AgentccWebhookEvent.no_workspace_objects.create(
        organization=organization,
        webhook=hook,
        event_type="error.occurred",
        payload={"event": "error.occurred", "request_id": "pairs-1"},
    )


@pytest.mark.integration
@pytest.mark.django_db
class TestDeliverySendsTheSameHeaders:
    def test_a_pair_list_is_sent_as_its_headers(self, organization, posts):
        hook = _webhook(organization, PAIRS)
        event = _event(organization, hook)

        deliver_webhook_events(event_ids=[event.id])

        (post,) = posts
        assert post["headers"]["X-Receiver-Token"] == TOKEN
        assert post["headers"]["X-Team"] == TEAM
        assert json.loads(post["body"])["request_id"] == "pairs-1"
        event.refresh_from_db()
        assert event.status == AgentccWebhookEvent.DELIVERED

    def test_an_unsupported_saved_shape_is_never_sent(self, organization, posts):
        hook = _webhook(
            organization, "X-Receiver-Token: Bearer zqxjkvwm-receiver-token"
        )
        event = _event(organization, hook)

        deliver_webhook_events(event_ids=[event.id])

        assert posts == []
        event.refresh_from_db()
        assert event.status == AgentccWebhookEvent.PENDING
        assert event.attempts == 1
        assert event.last_error
        assert "zqxjkvwm" not in event.last_error
