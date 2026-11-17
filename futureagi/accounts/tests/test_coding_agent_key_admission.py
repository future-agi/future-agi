"""The dev coding-agent identity must pass the c2.6 admission boundary."""

import base64
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.cache.backends.locmem import LocMemCache
from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.api_key_policy import admit_api_key, authenticate_api_key
from accounts.authentication import (
    APIKeyAuthentication,
    LangfuseBasicAuthentication,
    decode_token,
    generate_encrypted_message,
    is_coding_agent_key,
)
from accounts.models import Organization, OrgApiKey, User
from accounts.models.auth_token import AuthToken, AuthTokenType
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from accounts.services.auth_cache import get_cached_access_token
from accounts.views.keys import GetKeysView, SecretKeyAPIViewSet
from simulate.authentication import CodingAgentNeedsSuiteVersion
from simulate.models import HarnessSuiteVersion, HostedHarnessJob
from tfc.constants.levels import Level
from tfc.middleware.workspace_context import clear_workspace_context

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def clean_context(settings):
    clear_workspace_context()
    settings.HARNESS_VERSIONING_ENABLED = True
    yield
    clear_workspace_context()


@pytest.fixture
def actor():
    org = Organization.objects.create(name=f"coding-{uuid.uuid4().hex}")
    user = User.objects.create(
        email=f"{uuid.uuid4().hex}@example.invalid",
        name="Coding minter",
        organization=org,
        organization_role="Member",
    )
    membership = OrganizationMembership._base_manager.create(
        user=user, organization=org, role="Member", level=Level.MEMBER
    )
    workspace = Workspace._base_manager.create(
        name="Coding workspace", organization=org, created_by=user
    )
    workspace_membership, _ = WorkspaceMembership._base_manager.update_or_create(
        user=user,
        workspace=workspace,
        defaults={
            "organization_membership": membership,
            "level": Level.WORKSPACE_MEMBER,
            "is_active": True,
            "deleted": False,
        },
    )
    environment = HostedHarnessJob._base_manager.create(
        organization=org,
        workspace=workspace,
        run_id=uuid.uuid4(),
        idempotency_key=uuid.uuid4().hex,
        request_digest="digest",
        schema_version="1.6",
        seed=1,
        artifact_level="standard",
        max_artifact_bytes=1024,
        deadline_at=timezone.now() + timedelta(hours=1),
        scenario_count=1,
        payload={"source": {}, "metadata": {}},
    )
    suite = HarnessSuiteVersion._base_manager.create(
        job=environment,
        organization=org,
        workspace=workspace,
        version_number=1,
        status="active",
        origin="manual",
        hash="sha256:" + uuid.uuid4().hex * 2,
        archive_object_key="harness-suites/admission.tar.gz",
        archive_sha256="",
        archive_size=0,
    )
    environment.suite_version = suite
    environment.save(update_fields=["suite_version", "updated_at"])
    return SimpleNamespace(
        user=user, org=org, membership=membership, workspace=workspace,
        workspace_membership=workspace_membership, environment=environment,
    )


@pytest.fixture
def coding_key(actor):
    return OrgApiKey._base_manager.create(
        name="coding key", type="coding_agent", minted_by=actor.user,
        organization=actor.org, workspace=actor.workspace,
        simulation_environment_id=actor.environment.pk,
    )


def key_request(key, *, basic=False, method="GET", route=None, target=None, **headers):
    if basic:
        encoded = base64.b64encode(f"{key.api_key}:{key.secret_key}".encode()).decode()
        headers["HTTP_AUTHORIZATION"] = f"Basic {encoded}"
    else:
        headers.update(HTTP_X_API_KEY=key.api_key, HTTP_X_SECRET_KEY=key.secret_key)
    request = APIRequestFactory().generic(method, "/admission-probe/", **headers)
    request.resolver_match = SimpleNamespace(
        view_name=route or "simulate:harness-environment-detail",
        url_name="harness-environment-detail",
        kwargs={"pk": target or key.simulation_environment_id},
        func=None,
    )
    return request


def authenticate(key, *, basic=False, **kwargs):
    request = key_request(key, basic=basic, **kwargs)
    auth = LangfuseBasicAuthentication() if basic else APIKeyAuthentication()
    user, token = auth.authenticate(request)
    return request, user, token


def browser_token(user):
    token = AuthToken.objects.create(
        user=user, auth_type=AuthTokenType.ACCESS.value, last_used_at=timezone.now()
    )
    return token, generate_encrypted_message({"user_id": str(user.pk), "id": str(token.pk)})


def manage(actor, action, *, key=None, body=None, bearer=None):
    method = "get" if action == "get_secret_keys" else (
        "delete" if action == "delete_secret_key" else "post"
    )
    headers = {"HTTP_AUTHORIZATION": f"Bearer {bearer}"} if bearer else {}
    request = getattr(APIRequestFactory(), method)(
        f"/accounts/key/{action}/",
        body if body is not None else ({"key_id": str(key.pk)} if key else {}),
        format="json", **headers,
    )
    request.organization, request.workspace = actor.org, actor.workspace
    if not bearer:
        force_authenticate(request, user=actor.user)
    return SecretKeyAPIViewSet.as_view({method: action})(request)


@pytest.mark.parametrize("basic", [False, True])
def test_admitted_key_uses_live_minter_without_populating_user(actor, coding_key, basic):
    request, user, token = authenticate(coding_key, basic=basic)
    assert user.pk == actor.user.pk and token is None
    assert user.organization_id == actor.org.pk
    assert request.organization.pk == actor.org.pk
    assert request.workspace.pk == actor.workspace.pk
    assert is_coding_agent_key(request)
    assert request.org_api_key.user_id is None
    assert request.org_api_key.minted_by_id == user.pk
    stored = OrgApiKey._base_manager.get(pk=coding_key.pk)
    assert stored.user_id is None and stored.secret_key == ""
    assert stored.secret_key_hash and not stored.secret_key_ciphertext


@pytest.mark.parametrize("basic", [False, True])
@pytest.mark.parametrize("change", [
    "inactive_minter", "removed_marker", "inactive_member", "deleted_member",
    "inactive_workspace_member", "deleted_workspace_member", "inactive_workspace",
    "deleted_workspace", "wrong_workspace", "null_workspace", "disabled",
    "retired_at", "retired_reason", "deleted", "unsealed", "wrong_secret",
])
def test_admission_refuses_each_stale_authority(actor, coding_key, basic, change):
    if change == "inactive_minter":
        User._base_manager.filter(pk=actor.user.pk).update(is_active=False)
    elif change in {"removed_marker", "inactive_member", "deleted_member"}:
        fields = {"removed_marker": {"removed_at": timezone.now()},
                  "inactive_member": {"is_active": False}, "deleted_member": {"deleted": True}}
        OrganizationMembership._base_manager.filter(pk=actor.membership.pk).update(**fields[change])
    elif change in {"inactive_workspace_member", "deleted_workspace_member"}:
        fields = {"is_active": False} if change.startswith("inactive") else {"deleted": True}
        WorkspaceMembership._base_manager.filter(pk=actor.workspace_membership.pk).update(**fields)
    elif change in {"inactive_workspace", "deleted_workspace"}:
        fields = {"is_active": False} if change.startswith("inactive") else {"deleted": True}
        Workspace._base_manager.filter(pk=actor.workspace.pk).update(**fields)
    elif change == "wrong_workspace":
        foreign = Organization.objects.create(name=f"foreign-{uuid.uuid4().hex}")
        Workspace._base_manager.filter(pk=actor.workspace.pk).update(organization=foreign)
    elif change == "wrong_secret":
        coding_key.secret_key = "not-the-secret"
    else:
        fields = {
            "null_workspace": {"workspace": None}, "disabled": {"enabled": False},
            "retired_at": {"retired_at": timezone.now()}, "retired_reason": {"retired_reason": "retired"},
            "deleted": {"deleted": True},
            "unsealed": {"secret_key_hash": "", "secret_key": coding_key.secret_key},
        }
        OrgApiKey._base_manager.filter(pk=coding_key.pk).update(**fields[change])
    with pytest.raises(AuthenticationFailed):
        authenticate(coding_key, basic=basic)


@pytest.mark.parametrize("basic", [False, True])
@pytest.mark.parametrize("refusal", ["route", "method", "environment", "organization", "missing_match"])
def test_route_and_explicit_organization_guards(actor, coding_key, basic, refusal):
    kwargs = {
        "route": {"route": "accounts:get-secret-keys"},
        "method": {"method": "DELETE"},
        "environment": {"target": uuid.uuid4()},
        "organization": {"HTTP_X_ORGANIZATION_ID": str(uuid.uuid4())},
        "missing_match": {},
    }[refusal]
    request = key_request(coding_key, basic=basic, **kwargs)
    if refusal == "missing_match":
        request.resolver_match = None
    auth = LangfuseBasicAuthentication() if basic else APIKeyAuthentication()
    with pytest.raises(PermissionDenied):
        auth.authenticate(request)


@pytest.mark.parametrize("why", ["no_suite", "versioning_off"])
def test_environment_must_still_hold_back_cases(actor, coding_key, settings, why):
    if why == "no_suite":
        HostedHarnessJob._base_manager.filter(pk=actor.environment.pk).update(suite_version=None)
    else:
        settings.HARNESS_VERSIONING_ENABLED = False
    with pytest.raises(CodingAgentNeedsSuiteVersion):
        authenticate(coding_key)


def test_unavailable_write_pin_and_viewer_keep_ours_denials(actor, coding_key):
    route = "simulate:harness-environment-run"
    WorkspaceMembership._base_manager.filter(pk=actor.workspace_membership.pk).update(is_active=False)
    with pytest.raises(PermissionDenied) as caught:
        authenticate(coding_key, method="POST", route=route)
    assert caught.value.get_codes() == "workspace_unavailable"
    WorkspaceMembership._base_manager.filter(pk=actor.workspace_membership.pk).update(
        is_active=True, level=Level.WORKSPACE_VIEWER
    )
    with pytest.raises(PermissionDenied, match="Write access denied"):
        authenticate(coding_key, method="POST", route=route)


@pytest.mark.parametrize("helper", ["authenticate", "admit"])
def test_other_transports_remain_personal_only(coding_key, helper):
    with pytest.raises(AuthenticationFailed):
        if helper == "authenticate":
            authenticate_api_key(coding_key.api_key, coding_key.secret_key)
        else:
            admit_api_key(coding_key)


@pytest.mark.parametrize("change", ["removed", "inactive"])
def test_warm_bearer_cache_cannot_supply_a_stale_coding_principal(actor, coding_key, change):
    backend = LocMemCache(f"coding-admission-{uuid.uuid4()}", {})
    token, bearer = browser_token(actor.user)
    with patch("accounts.authentication.cache", backend):
        decode_token(bearer)
        cached = get_cached_access_token(token.pk, cache_backend=backend)
        assert cached and cached["non_saml_v1"] is True
        assert "token" not in cached and cached["token_digest"] != bearer
        # Exercise a real bearer cache hit, then a successful key request.
        assert decode_token(bearer)[0].pk == actor.user.pk
        authenticate(coding_key)
        if change == "removed":
            OrganizationMembership._base_manager.filter(pk=actor.membership.pk).update(removed_at=timezone.now())
        else:
            User._base_manager.filter(pk=actor.user.pk).update(is_active=False)
        # Pair auth must not consult a bearer snapshot, even one still resident.
        with patch("accounts.authentication.get_cached_access_token", return_value=cached) as cache_read:
            with pytest.raises(AuthenticationFailed):
                authenticate(coding_key)
            cache_read.assert_not_called()
    backend.clear()


def test_admission_and_route_checks_are_primary_pinned(actor, coding_key):
    from accounts import api_key_policy
    from simulate.authentication import refuse_coding_agent_request
    from tfc.routers import _force_primary_depth

    original = api_key_policy.live_membership

    def membership(*args, **kwargs):
        assert _force_primary_depth.get() > 0
        return original(*args, **kwargs)

    def guard(*args, **kwargs):
        assert _force_primary_depth.get() > 0
        return refuse_coding_agent_request(*args, **kwargs)

    with patch.object(api_key_policy, "live_membership", side_effect=membership), patch(
        "simulate.authentication.refuse_coding_agent_request", side_effect=guard
    ) as route_guard:
        authenticate(coding_key)
        assert route_guard.call_count == 1


def test_personal_key_still_has_personal_defaults_and_no_route_guard(actor):
    key = OrgApiKey._base_manager.create(user=actor.user, organization=actor.org, name="personal")
    with patch("simulate.authentication.refuse_coding_agent_request") as guard:
        admitted = authenticate_api_key(key.api_key, key.secret_key)
        assert admitted.user.pk == actor.user.pk and admitted.type == "user"
        assert admitted.workspace_id is None
        request, user, _ = authenticate(key, route="accounts:any-personal-route")
        assert user.pk == actor.user.pk and not is_coding_agent_key(request)
        guard.assert_not_called()


def test_interactive_issuance_is_sealed_and_environment_pinned(actor):
    _, bearer = browser_token(actor.user)
    response = manage(actor, "generate_secret_key", bearer=bearer, body={
        "key_name": "new coding key", "type": "coding_agent",
        "simulation_environment_id": str(actor.environment.pk),
    })
    assert response.status_code == 200, response.data
    assert response["Cache-Control"] == "no-store"
    result = response.data["result"]
    key = OrgApiKey._base_manager.get(pk=result["key_id"])
    assert key.user_id is None and key.minted_by_id == actor.user.pk
    assert key.workspace_id == actor.workspace.pk and key.type == "coding_agent"
    assert key.secret_key == "" and key.secret_key_hash and not key.secret_key_ciphertext
    key.secret_key = result["secret_key"]
    assert authenticate(key)[1].pk == actor.user.pk


@pytest.mark.parametrize("refusal", ["not_interactive", "foreign_environment", "versioning_off", "wrong_workspace"])
def test_issuance_keeps_session_and_environment_checks(actor, settings, refusal):
    _, bearer = browser_token(actor.user)
    body = {"key_name": "refused coding key", "type": "coding_agent",
            "simulation_environment_id": str(actor.environment.pk)}
    if refusal == "not_interactive":
        bearer = None  # A forced user alone is deliberately not session proof.
    elif refusal == "foreign_environment":
        foreign = Organization.objects.create(name=f"foreign-{uuid.uuid4().hex}")
        HostedHarnessJob._base_manager.filter(pk=actor.environment.pk).update(organization=foreign)
    elif refusal == "versioning_off":
        settings.HARNESS_VERSIONING_ENABLED = False
    else:
        body["workspace_id"] = str(uuid.uuid4())
    before = OrgApiKey._base_manager.count()
    response = manage(actor, "generate_secret_key", body=body, bearer=bearer)
    assert response.status_code in {400, 403, 404, 409}, response.data
    assert OrgApiKey._base_manager.count() == before


@pytest.mark.parametrize("role", ["Member", "Admin", "Owner"])
def test_list_and_mutations_never_cross_organization(actor, coding_key, role):
    OrganizationMembership._base_manager.filter(pk=actor.membership.pk).update(role=role, level=None)
    foreign_org = Organization.objects.create(name=f"foreign-{uuid.uuid4().hex}")
    foreign = OrgApiKey._base_manager.create(
        name="foreign coding key", type="coding_agent", minted_by=actor.user,
        organization=foreign_org, workspace=None, simulation_environment_id=uuid.uuid4(),
    )
    listed = manage(actor, "get_secret_keys")
    assert listed.status_code == 200
    rows = listed.data["result"]["table"]
    assert [str(row["id"]) for row in rows] == [str(coding_key.pk)]
    assert rows[0]["created_by"] == actor.user.name
    assert coding_key.secret_key not in str(listed.data)
    with patch("accounts.views.keys._publish_key_revocation") as publish:
        for action in ("enable_key", "disable_key", "delete_secret_key"):
            response = manage(actor, action, key=foreign)
            assert response.status_code == 400, response.data
        publish.assert_not_called()
    foreign.refresh_from_db()
    assert foreign.enabled and not foreign.deleted


def test_member_scope_and_owner_only_enable_are_preserved(actor, coding_key):
    other = User.objects.create(email=f"{uuid.uuid4().hex}@example.invalid", organization=actor.org)
    other_key = OrgApiKey._base_manager.create(
        name="another minter", type="coding_agent", minted_by=other,
        organization=actor.org, workspace=actor.workspace,
        simulation_environment_id=actor.environment.pk, enabled=False,
    )
    assert manage(actor, "delete_secret_key", key=other_key).status_code == 400
    OrganizationMembership._base_manager.filter(pk=actor.membership.pk).update(level=Level.ADMIN)
    assert manage(actor, "enable_key", key=other_key).status_code == 400
    assert len(manage(actor, "get_secret_keys").data["result"]["table"]) == 2
    with patch("accounts.views.keys._publish_key_revocation") as publish:
        assert manage(actor, "disable_key", key=coding_key).status_code == 200
        assert manage(actor, "enable_key", key=coding_key).status_code == 200
        assert manage(actor, "delete_secret_key", key=coding_key).status_code == 200
        assert publish.call_count == 2
    coding_key.refresh_from_db()
    assert coding_key.deleted and not coding_key.enabled


def test_legacy_keys_endpoint_remains_retired(actor):
    request = APIRequestFactory().get("/accounts/keys/")
    request.organization = actor.org
    force_authenticate(request, user=actor.user)
    assert GetKeysView.as_view()(request).status_code == 410
