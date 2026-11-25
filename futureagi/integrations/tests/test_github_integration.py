import hashlib
import hmac
import json
import uuid
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest
from django.core.cache import cache
from django.db import IntegrityError

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from integrations.models import (
    ConnectionStatus,
    IntegrationConnection,
    IntegrationPlatform,
)
from integrations.services import github_app
from integrations.services.credentials import CredentialManager
from integrations.services.github_app import GitHubAppError

WEBHOOK_URL = "/simulate/api/harness/github/webhook/"
WEBHOOK_SECRET = "github-integration-test-secret"


def _response(status_code, body, text=""):
    return SimpleNamespace(status_code=status_code, json=lambda: body, text=text)


class _GitHub:
    def __init__(self, *, installations=("42",), repositories=(), branches=()):
        self.installations = list(installations)
        self.repositories = list(repositories)
        self.branches = list(branches)
        self.user_token = "user-token"
        self.suspended_at = None
        self.fail_repositories = False
        self.account = {"login": "acme", "id": 1001, "type": "Organization"}
        self.membership_status = 200
        self.membership = {"state": "active", "role": "admin"}
        self.viewer_id = 1001
        self.unreachable_installations = 0
        self.revoke_failure = None
        self.head_status = 200
        self.head_sha = "a" * 40
        self.calls = []

    def __call__(self, method, url, **kwargs):
        self.calls.append((method, url))
        if url == github_app.OAUTH_TOKEN_URL:
            body = {"access_token": self.user_token} if self.user_token else {}
            return _response(200, body)
        if url.endswith("/user/installations"):
            if self.unreachable_installations:
                self.unreachable_installations -= 1
                raise GitHubAppError(
                    "github_unreachable", "GitHub could not be reached", retryable=True
                )
            return _response(
                200,
                {
                    "installations": [
                        {"id": int(i), "account": self.account}
                        for i in self.installations
                    ]
                },
            )
        if "/user/memberships/orgs/" in url:
            return _response(self.membership_status, self.membership)
        if url == f"{github_app.API_URL}/user":
            return _response(200, {"id": self.viewer_id})
        if "/applications/" in url:
            return _response(204, None)
        if url.endswith("/access_tokens"):
            return _response(201, {"token": "installation-token"})
        if url.endswith("/installation/token"):
            if self.revoke_failure == "unreachable":
                raise GitHubAppError(
                    "github_unreachable", "GitHub could not be reached", retryable=True
                )
            if self.revoke_failure == "server_error":
                return _response(502, {})
            return _response(204, None)
        if "/commits/" in url:
            return _response(self.head_status, None, text=f"{self.head_sha}\n")
        if "/app/installations/" in url:
            return _response(
                200, {"account": {"login": "acme"}, "suspended_at": self.suspended_at}
            )
        if url.endswith("/installation/repositories"):
            if self.fail_repositories:
                return _response(502, {})
            return _response(200, {"repositories": self.repositories})
        if url.endswith("/branches"):
            return _response(200, [{"name": name} for name in self.branches])
        raise AssertionError(f"unexpected GitHub call {method} {url}")

    def count(self, fragment):
        return sum(1 for _, url in self.calls if fragment in url)


def _repository(repository_id, name, *, private=False, default_branch="main"):
    return {
        "id": repository_id,
        "full_name": name,
        "private": private,
        "default_branch": default_branch,
    }


@pytest.fixture(autouse=True)
def configured(settings):
    settings.GITHUB_APP_SLUG = "future-agi-test"
    settings.GITHUB_APP_CLIENT_ID = "client-id"
    settings.GITHUB_APP_CLIENT_SECRET = "client-secret"
    settings.GITHUB_APP_WEBHOOK_SECRET = WEBHOOK_SECRET
    settings.APP_BASE_URL = "https://app.example.com"
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def github():
    fake = _GitHub()
    with (
        patch("integrations.services.github_app._send", side_effect=fake),
        patch("integrations.services.github_app._app_jwt", return_value="app-jwt"),
    ):
        yield fake


@pytest.fixture
def github_connection(organization, workspace, user):
    return IntegrationConnection.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        created_by=user,
        platform=IntegrationPlatform.GITHUB,
        display_name="acme",
        host_url=github_app.HOST_URL,
        encrypted_credentials=CredentialManager.encrypt({"installation_id": "42"}),
        external_project_name="42",
        status=ConnectionStatus.ACTIVE,
        backfill_completed=True,
    )


def _state(organization, workspace, user):
    install_url = github_app.start_github_install(
        organization=organization, workspace=workspace, user=user
    )
    return parse_qs(urlparse(install_url).query)["state"][0]


def _handoff(api_client, state, **params):
    """What GitHub's redirect hands the app: the state and the install's facts,
    saved by nobody yet."""
    query = {"state": state, "setup_action": "install", **params}
    response = api_client.get("/integrations/github/callback/", query)
    landed = parse_qs(urlparse(response["Location"]).query)
    assert landed.get("github") == ["complete"], landed
    return {key: values[0] for key, values in landed.items() if key != "github"}


def _complete(client, handoff):
    response = client.post(
        "/integrations/github/install/complete/", handoff, format="json"
    )
    if response.status_code != 200:
        return "error"
    return response.json()["result"]["outcome"]


def _callback(api_client, state, client=None, **params):
    """The whole round trip: GitHub's redirect, then the signed-in user finishing
    it; ``client`` is that user's client (the starting user by default)."""
    return _complete(client, _handoff(api_client, state, **params))


@pytest.mark.django_db
class TestGitHubInstall:
    def test_install_url_names_the_app_and_carries_only_an_opaque_state(
        self, auth_client, workspace, user
    ):
        response = auth_client.post("/integrations/github/install/", {}, format="json")

        assert response.status_code == 200
        install_url = response.json()["result"]["install_url"]
        assert install_url.startswith(
            "https://github.com/apps/future-agi-test/installations/new?state="
        )
        assert str(workspace.id) not in install_url
        assert str(user.id) not in install_url

    def test_install_is_refused_until_the_app_is_configured(
        self, settings, auth_client
    ):
        settings.GITHUB_APP_CLIENT_SECRET = ""

        response = auth_client.post("/integrations/github/install/", {}, format="json")

        assert response.status_code == 400

    def test_callback_saves_the_installation_the_user_can_reach(
        self, api_client, auth_client, organization, workspace, user, github
    ):
        state = _state(organization, workspace, user)

        outcome = _callback(
            api_client, state, client=auth_client, code="code", installation_id="42"
        )

        assert outcome == "connected"
        connection = IntegrationConnection.no_workspace_objects.get(
            platform=IntegrationPlatform.GITHUB
        )
        assert (connection.organization_id, connection.workspace_id) == (
            organization.id,
            workspace.id,
        )
        assert connection.external_project_name == "42"
        assert connection.display_name == "acme"
        assert connection.status == ConnectionStatus.ACTIVE
        assert github.count("/applications/client-id/token") == 1

    def test_callback_refuses_an_installation_the_user_cannot_reach(
        self, api_client, auth_client, organization, workspace, user, github
    ):
        github.installations = ["7"]
        state = _state(organization, workspace, user)

        outcome = _callback(
            api_client, state, client=auth_client, code="code", installation_id="42"
        )

        assert outcome == "error"
        assert not IntegrationConnection.no_workspace_objects.filter(
            platform=IntegrationPlatform.GITHUB
        ).exists()
        assert github.count("/applications/client-id/token") == 1

    def test_callback_without_a_user_authorization_saves_nothing(
        self, api_client, auth_client, organization, workspace, user, github
    ):
        github.user_token = None
        state = _state(organization, workspace, user)

        assert (
            _callback(
                api_client, state, client=auth_client, code="bad", installation_id="42"
            )
            == "error"
        )
        assert not IntegrationConnection.no_workspace_objects.exists()

    def test_a_requested_install_waits_for_an_owner_and_saves_nothing(
        self, api_client, auth_client, organization, workspace, user, github
    ):
        state = _state(organization, workspace, user)

        assert (
            _callback(api_client, state, client=auth_client, setup_action="request")
            == "requested"
        )
        assert not IntegrationConnection.no_workspace_objects.exists()
        assert github.calls == []

    def test_a_state_is_used_once(
        self, api_client, auth_client, organization, workspace, user, github
    ):
        state = _state(organization, workspace, user)
        _callback(
            api_client, state, client=auth_client, code="code", installation_id="42"
        )

        assert _callback(
            api_client, state, client=auth_client, code="code", installation_id="42"
        ) == ("error")

    def test_a_second_github_account_is_refused_while_one_is_connected(
        self,
        api_client,
        auth_client,
        organization,
        workspace,
        user,
        github,
        github_connection,
    ):
        github.installations = ["99"]
        state = _state(organization, workspace, user)

        outcome = _callback(
            api_client, state, client=auth_client, code="code", installation_id="99"
        )

        assert outcome == "already_connected"
        assert list(
            IntegrationConnection.no_workspace_objects.filter(
                platform=IntegrationPlatform.GITHUB
            ).values_list("external_project_name", flat=True)
        ) == ["42"]

    def test_a_suspended_installation_is_saved_paused(
        self, api_client, auth_client, organization, workspace, user, github
    ):
        github.suspended_at = "2026-10-27T10:00:00Z"
        state = _state(organization, workspace, user)

        _callback(
            api_client, state, client=auth_client, code="code", installation_id="42"
        )

        connection = IntegrationConnection.no_workspace_objects.get()
        assert connection.status == ConnectionStatus.PAUSED
        assert connection.status_message == github_app.SUSPENDED_MESSAGE

    def test_a_forwarded_install_link_binds_nothing_for_another_user(
        self, api_client, organization, workspace, user, github
    ):
        from accounts.models.organization_membership import OrganizationMembership
        from accounts.models.user import User
        from tfc.constants.roles import OrganizationRoles

        other = User.objects.create_user(
            email="other@futureagi.com",
            password="testpassword123",
            name="Other User",
            organization=organization,
            organization_role=OrganizationRoles.OWNER,
        )
        OrganizationMembership.no_workspace_objects.get_or_create(
            user=other,
            organization=organization,
            defaults={"role": OrganizationRoles.OWNER},
        )
        state = _state(organization, workspace, user)
        handoff = _handoff(api_client, state, code="code", installation_id="42")
        from rest_framework.test import APIClient

        theirs = APIClient()
        theirs.force_authenticate(user=other)

        response = theirs.post(
            "/integrations/github/install/complete/", handoff, format="json"
        )

        assert response.status_code == 403, response.content
        assert not IntegrationConnection.no_workspace_objects.exists()
        assert github.count("/applications/client-id/token") == 0

    def test_an_organization_admin_connects_the_installation(
        self, api_client, auth_client, organization, workspace, user, github
    ):
        state = _state(organization, workspace, user)

        outcome = _callback(
            api_client, state, client=auth_client, code="code", installation_id="42"
        )

        assert outcome == "connected"
        assert github.count("/user/memberships/orgs/acme") == 1

    @pytest.mark.parametrize(
        ("membership_status", "membership"),
        [
            (200, {"state": "active", "role": "member"}),
            (200, {"state": "pending", "role": "admin"}),
            (403, {}),
            (404, {}),
        ],
    )
    def test_someone_who_only_reads_the_organizations_repos_cannot_connect_it(
        self,
        api_client,
        auth_client,
        organization,
        workspace,
        user,
        github,
        membership_status,
        membership,
    ):
        github.membership_status = membership_status
        github.membership = membership
        state = _state(organization, workspace, user)
        handoff = _handoff(api_client, state, code="code", installation_id="42")

        response = auth_client.post(
            "/integrations/github/install/complete/", handoff, format="json"
        )

        assert response.status_code == 403, response.content
        assert response.json()["code"] == "github_installation_not_admin"
        assert not IntegrationConnection.no_workspace_objects.exists()
        assert github.count("/applications/client-id/token") == 1

    def test_a_personal_installation_connects_only_for_its_owner(
        self, api_client, auth_client, organization, workspace, user, github
    ):
        github.account = {"login": "dana", "id": 5005, "type": "User"}
        github.viewer_id = 6006
        refused = _callback(
            api_client,
            _state(organization, workspace, user),
            client=auth_client,
            code="code",
            installation_id="42",
        )
        github.viewer_id = 5005
        connected = _callback(
            api_client,
            _state(organization, workspace, user),
            client=auth_client,
            code="code",
            installation_id="42",
        )

        assert (refused, connected) == ("error", "connected")
        assert github.count("/user/memberships/orgs/") == 0

    def test_a_github_failure_leaves_the_state_for_a_retry(
        self, api_client, auth_client, organization, workspace, user, github
    ):
        github.unreachable_installations = 1
        state = _state(organization, workspace, user)
        handoff = _handoff(api_client, state, code="code", installation_id="42")

        failed = auth_client.post(
            "/integrations/github/install/complete/", handoff, format="json"
        )
        retried = auth_client.post(
            "/integrations/github/install/complete/", handoff, format="json"
        )

        assert failed.status_code == 502, failed.content
        assert failed.json()["code"] == "github_unreachable"
        assert retried.status_code == 200, retried.content
        assert retried.json()["result"]["outcome"] == "connected"
        assert _complete(auth_client, handoff) == "error"

    def test_a_requested_install_uses_up_its_state(
        self, api_client, auth_client, organization, workspace, user, github
    ):
        state = _state(organization, workspace, user)

        requested = _callback(
            api_client, state, client=auth_client, setup_action="request"
        )
        reused = _callback(
            api_client, state, client=auth_client, code="code", installation_id="42"
        )

        assert (requested, reused) == ("requested", "error")
        assert not IntegrationConnection.no_workspace_objects.exists()

    def test_two_installs_racing_for_one_workspace_never_leak_the_database_error(
        self, api_client, auth_client, organization, workspace, user, github
    ):
        state = _state(organization, workspace, user)
        handoff = _handoff(api_client, state, code="code", installation_id="42")
        duplicate = IntegrityError(
            'duplicate key value violates unique constraint "uq_intconn_org_ws"'
        )

        with patch.object(
            IntegrationConnection.no_workspace_objects,
            "create",
            side_effect=duplicate,
        ):
            response = auth_client.post(
                "/integrations/github/install/complete/", handoff, format="json"
            )

        assert response.status_code == 200, response.content
        assert response.json()["result"]["outcome"] == "already_connected"
        assert "duplicate key" not in response.content.decode()

    def test_a_refusal_carries_its_code(
        self, api_client, organization, workspace, user, github
    ):
        from rest_framework.test import APIClient

        from accounts.models.organization_membership import OrganizationMembership
        from accounts.models.user import User
        from tfc.constants.roles import OrganizationRoles

        other = User.objects.create_user(
            email="someone-else@futureagi.com",
            password="testpassword123",
            name="Someone Else",
            organization=organization,
            organization_role=OrganizationRoles.OWNER,
        )
        OrganizationMembership.no_workspace_objects.get_or_create(
            user=other,
            organization=organization,
            defaults={"role": OrganizationRoles.OWNER},
        )
        handoff = _handoff(
            api_client,
            _state(organization, workspace, user),
            code="code",
            installation_id="42",
        )
        theirs = APIClient()
        theirs.force_authenticate(user=other)

        response = theirs.post(
            "/integrations/github/install/complete/", handoff, format="json"
        )

        assert response.status_code == 403
        assert response.json()["code"] == "github_install_not_yours"

    def test_the_callback_itself_saves_nothing(
        self, api_client, organization, workspace, user, github
    ):
        state = _state(organization, workspace, user)

        _handoff(api_client, state, code="code", installation_id="42")

        assert not IntegrationConnection.no_workspace_objects.exists()
        assert github.calls == []

    def test_the_generic_create_endpoint_refuses_github(self, auth_client):
        response = auth_client.post(
            "/integrations/connections/", {"platform": "github"}, format="json"
        )

        assert response.status_code == 400
        assert not IntegrationConnection.no_workspace_objects.exists()


@pytest.mark.django_db
class TestSourceListing:
    def _repositories_url(self, connection):
        return f"/integrations/connections/{connection.id}/repositories/"

    def test_repositories_are_searched_and_paged_by_fifty(
        self, auth_client, github, github_connection
    ):
        github.repositories = [
            _repository(index, f"acme/agent-{index:03d}") for index in range(1, 61)
        ] + [_repository(500, "acme/website", private=True)]

        first = auth_client.get(
            self._repositories_url(github_connection), {"q": "AGENT"}
        ).json()["result"]
        second = auth_client.get(
            self._repositories_url(github_connection), {"q": "agent", "page": 2}
        ).json()["result"]
        private = auth_client.get(
            self._repositories_url(github_connection), {"q": "website"}
        ).json()["result"]

        assert len(first["repositories"]) == 50
        assert first["next_page"] == 2
        assert len(second["repositories"]) == 10
        assert second["next_page"] is None
        assert private["repositories"] == [
            {
                "id": 500,
                "full_name": "acme/website",
                "default_branch": "main",
                "private": True,
            }
        ]

    def test_repositories_are_read_from_github_once_per_cache_window(
        self, auth_client, github, github_connection
    ):
        github.repositories = [_repository(1, "acme/agent")]

        auth_client.get(self._repositories_url(github_connection))
        auth_client.get(self._repositories_url(github_connection), {"q": "agent"})

        assert github.count("/installation/repositories") == 1
        assert github.count("/access_tokens") == 1
        assert github.count("/installation/token") == 1

    def test_branches_put_the_default_first(
        self, auth_client, github, github_connection
    ):
        github.repositories = [_repository(1, "acme/agent", default_branch="trunk")]
        github.branches = ["feature", "trunk", "release"]

        response = auth_client.get(
            f"{self._repositories_url(github_connection)}1/branches/"
        )

        assert response.status_code == 200
        assert response.json()["result"]["branches"] == [
            {"name": "trunk", "is_default": True},
            {"name": "feature", "is_default": False},
            {"name": "release", "is_default": False},
        ]

    def test_branches_of_a_repository_outside_the_installation_are_not_read(
        self, auth_client, github, github_connection
    ):
        github.repositories = [_repository(1, "acme/agent")]

        response = auth_client.get(
            f"{self._repositories_url(github_connection)}2/branches/"
        )

        assert response.status_code == 404
        assert github.count("/branches") == 0

    def test_a_suspended_connection_lists_nothing(
        self, auth_client, github, github_connection
    ):
        github_connection.status = ConnectionStatus.PAUSED
        github_connection.save(update_fields=["status"])

        response = auth_client.get(self._repositories_url(github_connection))

        assert response.status_code == 409
        assert github.calls == []

    def test_another_organizations_connection_is_not_found(
        self, auth_client, github, user
    ):
        other = Organization.objects.create(name="Other organization")
        other_workspace = Workspace.objects.create(
            name="Other",
            organization=other,
            is_default=True,
            is_active=True,
            created_by=user,
        )
        connection = IntegrationConnection.no_workspace_objects.create(
            organization=other,
            workspace=other_workspace,
            platform=IntegrationPlatform.GITHUB,
            display_name="other",
            host_url=github_app.HOST_URL,
            encrypted_credentials=CredentialManager.encrypt({}),
            external_project_name="77",
            backfill_completed=True,
        )

        response = auth_client.get(self._repositories_url(connection))

        assert response.status_code == 404
        assert github.calls == []

    @pytest.mark.parametrize("failure", ["server_error", "unreachable"])
    def test_a_failed_token_revoke_keeps_the_listing_it_followed(
        self, auth_client, github, github_connection, failure
    ):
        github.repositories = [_repository(1, "acme/agent")]
        github.revoke_failure = failure

        response = auth_client.get(self._repositories_url(github_connection))

        assert response.status_code == 200, response.content
        assert [
            repository["full_name"]
            for repository in response.json()["result"]["repositories"]
        ] == ["acme/agent"]
        assert github.count("/installation/token") == 1

    def test_a_github_failure_answers_unavailable(
        self, auth_client, github, github_connection
    ):
        github.fail_repositories = True

        response = auth_client.get(self._repositories_url(github_connection))

        assert response.status_code == 503
        assert github.count("/installation/token") == 1

    def test_the_connections_list_filters_by_platform(
        self, auth_client, github_connection, integration_connection
    ):
        response = auth_client.get("/integrations/connections/", {"platform": "github"})

        assert [row["id"] for row in response.json()["result"]["connections"]] == [
            str(github_connection.id)
        ]


def _webhook(client, event, body, delivery=None):
    raw = json.dumps(body).encode()
    signature = (
        "sha256=" + hmac.new(WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    )
    headers = {
        "HTTP_X_GITHUB_EVENT": event,
        "HTTP_X_HUB_SIGNATURE_256": signature,
    }
    if delivery != "":
        headers["HTTP_X_GITHUB_DELIVERY"] = delivery or str(uuid.uuid4())
    return client.post(
        WEBHOOK_URL, data=raw, content_type="application/json", **headers
    )


@pytest.mark.django_db
class TestInstallationEvents:
    def test_uninstalling_the_app_removes_the_connection(
        self, client, github_connection
    ):
        response = _webhook(
            client, "installation", {"action": "deleted", "installation": {"id": 42}}
        )

        assert response.status_code == 204
        assert not IntegrationConnection.no_workspace_objects.filter(
            id=github_connection.id
        ).exists()

    def test_suspending_and_unsuspending_follow_github(
        self, client, github, github_connection
    ):
        github.suspended_at = "2026-10-27T10:00:00Z"
        _webhook(
            client, "installation", {"action": "suspend", "installation": {"id": 42}}
        )
        github_connection.refresh_from_db()
        assert github_connection.status == ConnectionStatus.PAUSED

        github.suspended_at = None
        _webhook(
            client,
            "installation",
            {"action": "unsuspend", "installation": {"id": 42}},
        )
        github_connection.refresh_from_db()
        assert github_connection.status == ConnectionStatus.ACTIVE

    def test_a_redelivered_event_is_applied_once(self, client, github_connection):
        delivery = str(uuid.uuid4())
        body = {"action": "suspend", "installation": {"id": 42}}

        with patch(
            "simulate.views.harness_github_webhook.apply_installation_event"
        ) as apply:
            _webhook(client, "installation", body, delivery=delivery)
            _webhook(client, "installation", body, delivery=delivery)
            _webhook(client, "installation", body)

        assert apply.call_count == 2

    def test_events_without_a_delivery_id_are_each_applied(
        self, client, github_connection
    ):
        body = {"action": "suspend", "installation": {"id": 42}}

        with patch(
            "simulate.views.harness_github_webhook.apply_installation_event"
        ) as apply:
            _webhook(client, "installation", body, delivery="")
            _webhook(client, "installation", body, delivery="")

        assert apply.call_count == 2

    def test_a_late_unsuspend_keeps_a_suspended_installation_paused(
        self, client, github, github_connection
    ):
        github_connection.status = ConnectionStatus.PAUSED
        github_connection.save(update_fields=["status"])
        github.suspended_at = "2026-10-27T10:00:00Z"

        _webhook(
            client, "installation", {"action": "unsuspend", "installation": {"id": 42}}
        )

        github_connection.refresh_from_db()
        assert github_connection.status == ConnectionStatus.PAUSED

    def test_an_unreadable_installation_state_pauses_the_connection(
        self, client, github_connection
    ):
        failure = GitHubAppError("github_installation_unreadable", "unreadable")
        with patch(
            "integrations.services.github_app._installation_account",
            side_effect=failure,
        ):
            _webhook(
                client,
                "installation",
                {"action": "unsuspend", "installation": {"id": 42}},
            )

        github_connection.refresh_from_db()
        assert github_connection.status == ConnectionStatus.PAUSED

    def test_other_installations_are_left_alone(self, client, github_connection):
        _webhook(
            client, "installation", {"action": "deleted", "installation": {"id": 7}}
        )

        github_connection.refresh_from_db()
        assert not github_connection.deleted

    def test_changing_the_repository_selection_drops_the_cached_list(
        self, client, github, github_connection
    ):
        github.repositories = [_repository(1, "acme/agent")]
        github_app.list_repositories("42", query="", page=1)
        github.repositories = [_repository(2, "acme/new-agent")]

        _webhook(
            client,
            "installation_repositories",
            {"action": "added", "installation": {"id": 42}},
        )

        assert [
            repository.full_name
            for repository in github_app.list_repositories(
                "42", query="", page=1
            ).repositories
        ] == ["acme/new-agent"]

    def test_an_unsigned_installation_event_changes_nothing(
        self, client, github_connection
    ):
        response = client.post(
            WEBHOOK_URL,
            data=json.dumps({"action": "deleted", "installation": {"id": 42}}),
            content_type="application/json",
            HTTP_X_GITHUB_EVENT="installation",
        )

        assert response.status_code == 204
        github_connection.refresh_from_db()
        assert not github_connection.deleted


@pytest.mark.django_db
class TestPrivateSourceOwnership:
    def _payload(self, installation_id="42"):
        return {
            "source": {
                "kind": "github",
                "repository": "acme/agent",
                "visibility": "private",
                "installation_id": installation_id,
            }
        }

    def test_an_installation_connected_to_the_workspace_is_accepted(
        self, organization, workspace, github_connection
    ):
        from simulate.services.harness_provider import _validate_github_installation

        _validate_github_installation(organization.id, workspace.id, self._payload())

    def test_an_installation_not_connected_here_is_refused(
        self, organization, workspace, github_connection
    ):
        from simulate.services.harness_provider import _validate_github_installation
        from simulate.services.hosted_harness import HostedHarnessError

        with pytest.raises(HostedHarnessError) as refused:
            _validate_github_installation(
                organization.id, workspace.id, self._payload("7")
            )

        assert refused.value.code == "github_installation_not_connected"
        assert refused.value.status_code == 422

    def test_another_workspaces_installation_is_refused(
        self, organization, user, github_connection
    ):
        from simulate.services.harness_provider import _validate_github_installation
        from simulate.services.hosted_harness import HostedHarnessError

        other_workspace = Workspace.objects.create(
            name="Other team",
            organization=organization,
            is_default=False,
            is_active=True,
            created_by=user,
        )

        with pytest.raises(HostedHarnessError) as refused:
            _validate_github_installation(
                organization.id, other_workspace.id, self._payload()
            )

        assert refused.value.code == "github_installation_not_connected"
        assert not github_app.installation_connected(
            organization_id=organization.id,
            workspace_id=other_workspace.id,
            installation_id="42",
        )

    def test_a_suspended_installation_is_refused(
        self, organization, workspace, github_connection
    ):
        from simulate.services.harness_provider import _validate_github_installation
        from simulate.services.hosted_harness import HostedHarnessError

        github_connection.status = ConnectionStatus.PAUSED
        github_connection.save(update_fields=["status"])

        with pytest.raises(HostedHarnessError):
            _validate_github_installation(
                organization.id, workspace.id, self._payload()
            )

    def test_another_organizations_installation_is_refused(
        self, workspace, github_connection
    ):
        from simulate.services.harness_provider import _validate_github_installation
        from simulate.services.hosted_harness import HostedHarnessError

        other = Organization.objects.create(name="Other organization")

        with pytest.raises(HostedHarnessError):
            _validate_github_installation(other.id, workspace.id, self._payload())

    def test_a_public_source_needs_no_connection(self, organization, workspace):
        from simulate.services.harness_provider import _validate_github_installation

        _validate_github_installation(
            organization.id,
            workspace.id,
            {"source": {"kind": "github", "repository": "acme/agent"}},
        )


class TestGatewayCredential:
    def test_a_github_failure_keeps_its_code_as_a_harness_error(self):
        from simulate.services.hosted_harness import HostedHarnessError
        from simulate.services.hosted_harness_gateway import GitHubAppTokenProvider

        failure = GitHubAppError(
            "github_installation_token_failed",
            "GitHub rejected installation 42",
            retryable=True,
        )
        with patch(
            "integrations.services.github_app.installation_token",
            side_effect=failure,
        ):
            with pytest.raises(HostedHarnessError) as raised:
                with GitHubAppTokenProvider.from_settings().credential("42"):
                    pass

        assert raised.value.code == "github_installation_token_failed"
        assert raised.value.status_code == 502
        assert raised.value.retryable is True

    def test_the_token_is_revoked_after_use(self):
        from simulate.services.hosted_harness_gateway import GitHubAppTokenProvider

        with (
            patch(
                "integrations.services.github_app.installation_token",
                return_value="installation-token",
            ),
            patch(
                "integrations.services.github_app.revoke_installation_token"
            ) as revoke,
        ):
            with GitHubAppTokenProvider.from_settings().credential("42") as token:
                assert token == "installation-token"

        revoke.assert_called_once_with("installation-token")


@pytest.mark.django_db(transaction=True)
@patch("integrations.temporal.activities.sync_integration_connection")
def test_github_connections_are_never_synced(sync, github_connection):
    from integrations.temporal.activities import poll_active_integrations

    poll_active_integrations()

    sync.delay.assert_not_called()


class TestBranchHead:
    def test_reads_the_head_by_repository_id(self, github):
        sha = github_app.branch_head(
            "42", repository="acme/agent", repository_id=7, ref="main"
        )

        assert sha == "a" * 40
        assert ("get", f"{github_app.API_URL}/repositories/7/commits/main") in (
            github.calls
        )

    def test_reads_the_head_by_name_without_an_id(self, github):
        github_app.branch_head(
            "42", repository="acme/agent", repository_id=None, ref=None
        )

        assert ("get", f"{github_app.API_URL}/repos/acme/agent/commits/HEAD") in (
            github.calls
        )

    @pytest.mark.parametrize("status_code", [404, 422])
    def test_a_missing_repository_or_branch_is_none(self, github, status_code):
        github.head_status = status_code

        assert (
            github_app.branch_head(
                "42", repository="acme/agent", repository_id=7, ref="gone"
            )
            is None
        )

    def test_a_github_outage_is_retryable(self, github):
        github.head_status = 503

        with pytest.raises(GitHubAppError) as raised:
            github_app.branch_head(
                "42", repository="acme/agent", repository_id=7, ref="main"
            )

        assert raised.value.code == "github_head_unreadable"
        assert raised.value.retryable is True
        assert github.count("/installation/token") == 1
