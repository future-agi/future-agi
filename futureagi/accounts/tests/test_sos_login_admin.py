import json
import uuid

import pytest
from django.contrib import admin
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory
from django.urls import reverse
from structlog.testing import capture_logs

from accounts.admin import SOSLoginProxy
from accounts.models.auth_token import AuthToken, AuthTokenType
from accounts.models.organization import Organization
from accounts.models.user import User

CHANGELIST_URL = "/admin/accounts/sosloginproxy/"
LOGIN_URL = "/admin/accounts/sosloginproxy/login/"
COPY_LINK_URL = "/admin/accounts/sosloginproxy/copy-link/"


@pytest.fixture
def sos_admin():
    return admin.site._registry[SOSLoginProxy]


def _make_user(organization, label, **flags):
    return User.objects.create(
        email=f"{label}-{uuid.uuid4().hex[:8]}@futureagi.com",
        name=label.title(),
        organization=organization,
        is_active=True,
        **flags,
    )


@pytest.fixture
def sos_operator(db, organization):
    """Staff, not superuser — staff status is the SOS grant."""
    return _make_user(organization, "operator", is_staff=True, is_superuser=False)


@pytest.fixture
def superuser(db, organization):
    return _make_user(organization, "root", is_staff=True, is_superuser=True)


@pytest.fixture
def non_staff_user(db, organization):
    return _make_user(organization, "customer", is_staff=False, is_superuser=False)


def _request(rf_method, url, user, data=None):
    request = rf_method(url, data) if data is not None else rf_method(url)
    request.user = user
    request.session = {}
    request._messages = FallbackStorage(request)
    return request


@pytest.mark.django_db
class TestSOSLoginAdminAccess:
    def test_staff_may_open_the_page(self, sos_admin, sos_operator):
        request = _request(RequestFactory().get, CHANGELIST_URL, sos_operator)
        assert sos_admin.sos_login_view(request).status_code == 200

    def test_superuser_may_open_the_page(self, sos_admin, superuser):
        request = _request(RequestFactory().get, CHANGELIST_URL, superuser)
        assert sos_admin.sos_login_view(request).status_code == 200

    def test_non_staff_is_rejected(self, sos_admin, non_staff_user):
        request = _request(RequestFactory().get, CHANGELIST_URL, non_staff_user)
        with pytest.raises(PermissionDenied):
            sos_admin.sos_login_view(request)

    def test_inactive_staff_is_rejected(self, sos_admin, sos_operator):
        sos_operator.is_active = False
        request = _request(RequestFactory().get, CHANGELIST_URL, sos_operator)

        assert sos_admin.has_module_permission(request) is False
        with pytest.raises(PermissionDenied):
            sos_admin.sos_login_view(request)

    def test_module_visible_to_staff_hidden_from_others(
        self, sos_admin, sos_operator, non_staff_user
    ):
        allowed = _request(RequestFactory().get, CHANGELIST_URL, sos_operator)
        denied = _request(RequestFactory().get, CHANGELIST_URL, non_staff_user)

        assert sos_admin.has_module_permission(allowed) is True
        assert sos_admin.has_view_permission(allowed) is True
        assert sos_admin.has_module_permission(denied) is False
        assert sos_admin.has_view_permission(denied) is False

    def test_add_change_and_delete_are_never_allowed(self, sos_admin, sos_operator):
        request = _request(RequestFactory().get, CHANGELIST_URL, sos_operator)
        assert sos_admin.has_add_permission(request) is False
        assert sos_admin.has_change_permission(request) is False
        assert sos_admin.has_delete_permission(request) is False

    def test_login_endpoint_rejects_get(self, sos_admin, sos_operator):
        request = _request(RequestFactory().get, LOGIN_URL, sos_operator)
        assert sos_admin.start_sos_session_view(request).status_code == 405

    def test_login_endpoint_rejects_non_staff(self, sos_admin, non_staff_user, user):
        request = _request(
            RequestFactory().post, LOGIN_URL, non_staff_user, {"user_id": str(user.id)}
        )

        response = sos_admin.start_sos_session_view(request)

        assert response.status_code == 302
        assert AuthToken.objects.filter(user=user).count() == 0


@pytest.mark.django_db
class TestSOSAdminIndexIsolation:
    """Non-superusers see the SOS entry and nothing else on the admin index."""

    def _models_for(self, account):
        request = _request(RequestFactory().get, "/admin/", account)
        return [
            model["object_name"]
            for app in admin.site.get_app_list(request)
            for model in app["models"]
        ]

    def test_staff_sees_only_the_sos_entry(self, sos_operator):
        sos_operator.user_permissions.add(
            Permission.objects.get(
                codename="view_organization",
                content_type=ContentType.objects.get_for_model(Organization),
            )
        )
        operator = User.objects.get(pk=sos_operator.pk)  # drop the perm cache

        assert operator.has_perm("accounts.view_organization")
        assert self._models_for(operator) == ["SOSLoginProxy"]

    def test_superuser_sees_the_full_admin(self, superuser):
        models = self._models_for(superuser)
        assert "SOSLoginProxy" in models
        assert len(models) > 1


@pytest.mark.django_db
class TestSOSLoginAdminSearch:
    def test_no_query_lists_nobody(self, sos_admin, sos_operator, user):
        request = _request(RequestFactory().get, CHANGELIST_URL, sos_operator)
        context = sos_admin.sos_login_view(request).context_data
        assert context["users"] == []
        assert context["total_count"] == 0

    def test_search_matches_email_name_and_organization(
        self, sos_admin, sos_operator, user
    ):
        rf = RequestFactory()

        by_email = sos_admin.sos_login_view(
            _request(rf.get, f"{CHANGELIST_URL}?q={user.email}", sos_operator)
        ).context_data
        assert user in by_email["users"]

        by_org = sos_admin.sos_login_view(
            _request(
                rf.get, f"{CHANGELIST_URL}?q={user.organization.name}", sos_operator
            )
        ).context_data
        assert user in by_org["users"]

    def test_inactive_users_are_excluded(self, sos_admin, sos_operator, organization):
        inactive = User.objects.create(
            email=f"gone-{uuid.uuid4().hex[:8]}@futureagi.com",
            name="Gone",
            organization=organization,
            is_active=False,
        )
        request = _request(
            RequestFactory().get, f"{CHANGELIST_URL}?q={inactive.email}", sos_operator
        )
        assert sos_admin.sos_login_view(request).context_data["users"] == []

    def test_results_are_capped_and_truncation_is_reported(
        self, sos_admin, sos_operator, organization, monkeypatch
    ):
        monkeypatch.setattr(type(sos_admin), "RESULT_LIMIT", 2)
        for i in range(4):
            User.objects.create(
                email=f"bulk-{i}-{uuid.uuid4().hex[:6]}@futureagi.com",
                name="Bulk",
                organization=organization,
                is_active=True,
            )

        request = _request(
            RequestFactory().get, f"{CHANGELIST_URL}?q=Bulk", sos_operator
        )
        context = sos_admin.sos_login_view(request).context_data

        assert len(context["users"]) == 2
        assert context["total_count"] == 4
        assert context["truncated"] is True


@pytest.mark.django_db
class TestSOSLoginAdminSession:
    def test_starts_session_and_redirects_to_frontend_sos_route(
        self, sos_admin, sos_operator, user, settings
    ):
        settings.APP_URL = "app.futureagi.com"
        request = _request(
            RequestFactory().post, LOGIN_URL, sos_operator, {"user_id": str(user.id)}
        )

        response = sos_admin.start_sos_session_view(request)

        assert response.status_code == 302
        assert "/sos?" in response["Location"]
        assert "app.futureagi.com" in response["Location"]
        assert "access=" in response["Location"]
        assert "refresh=" in response["Location"]

    def test_mints_one_access_and_one_refresh_token_for_the_target(
        self, sos_admin, sos_operator, user, settings
    ):
        settings.APP_URL = "app.futureagi.com"
        request = _request(
            RequestFactory().post, LOGIN_URL, sos_operator, {"user_id": str(user.id)}
        )

        sos_admin.start_sos_session_view(request)

        assert (
            AuthToken.objects.filter(
                user=user, auth_type=AuthTokenType.ACCESS.value, is_active=True
            ).count()
            == 1
        )
        assert (
            AuthToken.objects.filter(
                user=user, auth_type=AuthTokenType.REFRESH.value, is_active=True
            ).count()
            == 1
        )
        assert AuthToken.objects.filter(user=sos_operator).count() == 0

    def test_does_not_revoke_the_targets_existing_session(
        self, sos_admin, sos_operator, user, settings
    ):
        settings.APP_URL = "app.futureagi.com"
        existing = AuthToken.objects.create(
            user=user, auth_type=AuthTokenType.REFRESH.value, is_active=True
        )
        request = _request(
            RequestFactory().post, LOGIN_URL, sos_operator, {"user_id": str(user.id)}
        )

        sos_admin.start_sos_session_view(request)

        existing.refresh_from_db()
        assert existing.is_active is True

    def test_unknown_user_redirects_without_minting_tokens(
        self, sos_admin, sos_operator, settings
    ):
        settings.APP_URL = "app.futureagi.com"
        request = _request(
            RequestFactory().post,
            LOGIN_URL,
            sos_operator,
            {"user_id": str(uuid.uuid4())},
        )

        response = sos_admin.start_sos_session_view(request)

        assert response.status_code == 302
        assert response["Location"] == "../"
        assert AuthToken.objects.count() == 0

    def test_malformed_user_id_is_handled(self, sos_admin, sos_operator, settings):
        settings.APP_URL = "app.futureagi.com"
        request = _request(
            RequestFactory().post, LOGIN_URL, sos_operator, {"user_id": "not-a-uuid"}
        )

        response = sos_admin.start_sos_session_view(request)

        assert response.status_code == 302
        assert AuthToken.objects.count() == 0

    def test_inactive_target_cannot_be_impersonated(
        self, sos_admin, sos_operator, organization, settings
    ):
        settings.APP_URL = "app.futureagi.com"
        inactive = User.objects.create(
            email=f"gone-{uuid.uuid4().hex[:8]}@futureagi.com",
            name="Gone",
            organization=organization,
            is_active=False,
        )
        request = _request(
            RequestFactory().post,
            LOGIN_URL,
            sos_operator,
            {"user_id": str(inactive.id)},
        )

        response = sos_admin.start_sos_session_view(request)

        assert response.status_code == 302
        assert AuthToken.objects.filter(user=inactive).count() == 0

    def test_missing_app_url_aborts_before_minting_tokens(
        self, sos_admin, sos_operator, user, settings
    ):
        settings.APP_URL = None
        request = _request(
            RequestFactory().post, LOGIN_URL, sos_operator, {"user_id": str(user.id)}
        )

        response = sos_admin.start_sos_session_view(request)

        assert response.status_code == 302
        assert response["Location"] == "../"
        assert AuthToken.objects.filter(user=user).count() == 0

    def test_cross_organization_target_is_reachable(
        self, sos_admin, sos_operator, settings
    ):
        settings.APP_URL = "app.futureagi.com"
        other_org = Organization.objects.create(name=f"Other {uuid.uuid4().hex[:6]}")
        target = User.objects.create(
            email=f"cust-{uuid.uuid4().hex[:8]}@futureagi.com",
            name="Customer",
            organization=other_org,
            is_active=True,
        )
        request = _request(
            RequestFactory().post, LOGIN_URL, sos_operator, {"user_id": str(target.id)}
        )

        response = sos_admin.start_sos_session_view(request)

        assert response.status_code == 302
        assert "/sos?" in response["Location"]
        assert AuthToken.objects.filter(user=target).count() == 2


@pytest.mark.django_db
class TestSOSCopyLink:
    def test_returns_the_same_handoff_url_as_the_redirect_flow(
        self, sos_admin, sos_operator, user, settings
    ):
        settings.APP_URL = "app.futureagi.com"
        request = _request(
            RequestFactory().post,
            COPY_LINK_URL,
            sos_operator,
            {"user_id": str(user.id)},
        )

        response = sos_admin.copy_sos_link_view(request)

        assert response.status_code == 200
        url = json.loads(response.content)["url"]
        assert "app.futureagi.com/sos?" in url
        assert "access=" in url
        assert "refresh=" in url

    def test_mints_a_token_pair_for_the_target(
        self, sos_admin, sos_operator, user, settings
    ):
        settings.APP_URL = "app.futureagi.com"
        request = _request(
            RequestFactory().post,
            COPY_LINK_URL,
            sos_operator,
            {"user_id": str(user.id)},
        )

        sos_admin.copy_sos_link_view(request)

        assert (
            AuthToken.objects.filter(
                user=user, auth_type=AuthTokenType.ACCESS.value, is_active=True
            ).count()
            == 1
        )
        assert (
            AuthToken.objects.filter(
                user=user, auth_type=AuthTokenType.REFRESH.value, is_active=True
            ).count()
            == 1
        )

    def test_rejects_get(self, sos_admin, sos_operator):
        request = _request(RequestFactory().get, COPY_LINK_URL, sos_operator)

        assert sos_admin.copy_sos_link_view(request).status_code == 405

    def test_rejects_non_staff(self, sos_admin, non_staff_user, user, settings):
        settings.APP_URL = "app.futureagi.com"
        request = _request(
            RequestFactory().post,
            COPY_LINK_URL,
            non_staff_user,
            {"user_id": str(user.id)},
        )

        response = sos_admin.copy_sos_link_view(request)

        assert response.status_code == 403
        assert AuthToken.objects.count() == 0

    def test_unknown_user_returns_400_without_minting_tokens(
        self, sos_admin, sos_operator, settings
    ):
        settings.APP_URL = "app.futureagi.com"
        request = _request(
            RequestFactory().post,
            COPY_LINK_URL,
            sos_operator,
            {"user_id": str(uuid.uuid4())},
        )

        response = sos_admin.copy_sos_link_view(request)

        assert response.status_code == 400
        assert json.loads(response.content)["error"] == "Active user not found."
        assert AuthToken.objects.count() == 0

    def test_missing_app_url_returns_400_before_minting_tokens(
        self, sos_admin, sos_operator, user, settings
    ):
        settings.APP_URL = None
        request = _request(
            RequestFactory().post,
            COPY_LINK_URL,
            sos_operator,
            {"user_id": str(user.id)},
        )

        response = sos_admin.copy_sos_link_view(request)

        assert response.status_code == 400
        assert AuthToken.objects.filter(user=user).count() == 0

    def test_does_not_revoke_the_targets_existing_session(
        self, sos_admin, sos_operator, user, settings
    ):
        settings.APP_URL = "app.futureagi.com"
        existing = AuthToken.objects.create(
            user=user, auth_type=AuthTokenType.REFRESH.value, is_active=True
        )
        request = _request(
            RequestFactory().post,
            COPY_LINK_URL,
            sos_operator,
            {"user_id": str(user.id)},
        )

        sos_admin.copy_sos_link_view(request)

        existing.refresh_from_db()
        assert existing.is_active is True


@pytest.mark.django_db
class TestSOSAuditLog:
    """Every SOS mint emits `sos_login_started`, whichever path issued it.

    The shared-key Appsmith path is the one with no operator identity, so it
    is the path where a missing audit line would matter most.
    """

    def _started(self, logs):
        return next(entry for entry in logs if entry["event"] == "sos_login_started")

    def test_admin_path_records_the_operator(
        self, sos_admin, sos_operator, user, settings
    ):
        settings.APP_URL = "app.futureagi.com"
        request = _request(
            RequestFactory().post, LOGIN_URL, sos_operator, {"user_id": str(user.id)}
        )

        with capture_logs() as logs:
            sos_admin.start_sos_session_view(request)

        entry = self._started(logs)
        assert entry["operator_id"] == str(sos_operator.id)
        assert entry["operator_email"] == sos_operator.email
        assert entry["target_user_id"] == str(user.id)
        assert entry["source"] == "django_admin"

    def test_appsmith_path_records_an_unattributed_mint(
        self, client, user, monkeypatch
    ):
        monkeypatch.setenv("API_KEY", "test-api-key")

        with capture_logs() as logs:
            response = client.post(
                reverse("sos_login"),
                data=json.dumps({"email": user.email}),
                content_type="application/json",
                HTTP_X_API_KEY="test-api-key",
            )

        assert response.status_code == 200, response.content
        entry = self._started(logs)
        assert entry["operator_id"] is None
        assert entry["operator_email"] is None
        assert entry["target_user_id"] == str(user.id)
        assert entry["source"] == "appsmith_api"
