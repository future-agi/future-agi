"""An account's organization context comes only from an active membership.

When an administrator deactivates a membership (``DELETE
/accounts/organization/members/remove/``), the account keeps signing in and its
user record keeps naming that organization. Every request it makes must still
resolve to no organization of which it is not an active member: a list returns
none of that organization's rows, and a write naming one of its ids is answered
exactly as one naming an unknown id. Checked here for each way an account
authenticates, on the production request path (``MIDDLEWARE`` and
``REST_FRAMEWORK`` as ``tfc.settings.settings`` defines them, sign-in through
``/accounts/token/``, WebSocket and MCP requests through ``tfc.asgi``):

- ``jwt``: the bearer token the web app and the SDK send;
- ``api-key``: the account's own ``X-Api-Key``/``X-Secret-Key`` pair;
- ``langfuse-basic``: the same pair as HTTP Basic credentials;
- ``websocket``: ``ws/connect/`` with the account's token;
- ``simulation-updates``: ``ws/simulation-updates/`` with the account's token;
- ``falcon-ai``: ``ws/falcon-ai/`` with the account's token and its former
  workspace, as the web app connects;
- ``mcp-oauth`` and ``mcp-api-key``: ``/mcp`` with an MCP OAuth bearer token
  or the account's MCP key pair.

An invitation that was sent and then cancelled leaves the same state by another
road: the invited account is created with its organization field naming the
organization, and cancelling removes (soft-deletes) its membership rows. Once
the account sets a password through the reset link and signs in, it must
resolve to no organization either, and no request may write a membership row
for it.

A member removed from A who is still an active member of B is served B, and
the rows its writes create carry B only, including where a view reads the
organization from ``request.user.organization``.

Each conformance test has a control: an active member of the same organization
keeps its access on the same path. The ``api-key`` and ``langfuse-basic`` paths
are controls themselves: dev and main already refuse a member whose membership
was deactivated there, and this fix must keep refusing it.

CONTROL, an account older than memberships keeps working: an account whose
organization field names its organization and that has no membership rows at
all (the AWS Marketplace sign-up and the Appsmith user endpoint create such
accounts) is served its organization, by a bearer token and by the
organization's system key, and its first request gives it its organization
membership.

The sign-up walk drives an account with no organization (a member removed
from its only organization) through every route the web app calls before the
account has an organization (the org-removed and setup-org screens, the auth
guard and the providers above them) up to its first organization, and checks
that none of them refuses it. It is a guard for the allowlist: plain dev
and plain main refuse no request with ``no_active_organization`` and mark no
view, so it passes there by construction.
"""

import base64
import ipaddress
import itertools
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from asgiref.sync import sync_to_async
from django.urls import URLPattern, URLResolver, get_resolver, resolve
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models.aws_marketplace import AWSMarketplaceCustomer
from accounts.models.organization import Organization
from accounts.models.organization_invite import InviteStatus, OrganizationInvite
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import OrgApiKey, User
from accounts.models.workspace import Workspace, WorkspaceMembership
from model_hub.models.ai_model import AIModel
from model_hub.models.annotation_queues import AnnotationQueue, QueueItem
from model_hub.models.choices import AnnotationTypeChoices, DatasetSourceChoices
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.develop_dataset import Dataset
from model_hub.models.score import Score
from tfc.constants.levels import Level
from tfc.middleware.workspace_context import clear_workspace_context
from tfc.utils.tests.production_path import (
    PASSWORD,
    make_tenant,
    removed_member,
    same_answer,
    sign_in,
    use_production_request_settings,
)
from tracer.models.observation_span import ObservationSpan
from tracer.models.project import Project
from tracer.models.trace import Trace

DATASETS_URL = "/model-hub/develops/get-datasets/"
LANGFUSE_HEALTH_URL = "/api/public/health"
NO_ACTIVE_ORGANIZATION = "no_active_organization"

# Separate browsers, separate addresses (198.18.128.0/17 lies in the
# 198.18.0.0/15 test range); the sign-in monitor counts sign-ins per address.
_ADDRESSES = itertools.count(1)


def _address():
    return str(ipaddress.ip_address("198.18.128.0") + next(_ADDRESSES))


def _clone_url(dataset_id):
    return f"/model-hub/develops/clone-dataset/{dataset_id}/"


def _refused(response):
    """The answer authentication gives a signed-in account with no organization."""
    if response.status_code != status.HTTP_403_FORBIDDEN:
        return False
    try:
        return response.json().get("code") == NO_ACTIVE_ORGANIZATION
    except ValueError:
        return False


def _member(tenant, label):
    """An active member of ``tenant`` (not its owner), signed in to it."""
    clear_workspace_context()
    user = User.objects.create_user(
        email=f"member-{label.lower()}-{uuid.uuid4().hex[:8]}@example.com",
        password=PASSWORD,
        name=f"Member {label}",
        organization=tenant.organization,
        organization_role="Member",
    )
    membership = OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=tenant.organization,
        role="Member",
        level=Level.MEMBER,
        is_active=True,
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=user,
        workspace=tenant.workspace,
        role="Workspace Member",
        level=Level.WORKSPACE_MEMBER,
        is_active=True,
        organization_membership=membership,
    )
    clear_workspace_context()
    return user


def _removed_team_member(tenant, label):
    """A member of a second (not default) workspace of ``tenant`` whom its
    owner removed through the API. Its own key pairs still exist."""
    clear_workspace_context()
    team = Workspace.no_workspace_objects.create(
        name=f"Tenant {label} team workspace",
        organization=tenant.organization,
        is_default=False,
        is_active=True,
        created_by=tenant.user,
    )
    user = User.objects.create_user(
        email=f"removed-team-{label.lower()}-{uuid.uuid4().hex[:8]}@example.com",
        password=PASSWORD,
        name=f"Removed {label} Team Member",
        organization=tenant.organization,
        organization_role="Member",
    )
    membership = OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=tenant.organization,
        role="Member",
        level=Level.MEMBER,
        is_active=True,
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=user,
        workspace=team,
        role="Workspace Member",
        level=Level.WORKSPACE_MEMBER,
        is_active=True,
        organization_membership=membership,
    )
    owner = sign_in(tenant.user.email, workspace=tenant.workspace)
    removed = owner.delete(
        "/accounts/organization/members/remove/",
        {"user_id": str(user.id)},
        format="json",
    )
    assert removed.status_code == status.HTTP_200_OK, removed.content
    clear_workspace_context()
    return user, team


def _key(tenant, user, key_type="user", workspace=None):
    clear_workspace_context()
    key = OrgApiKey.no_workspace_objects.create(
        name=f"{key_type} key",
        organization=tenant.organization,
        workspace=workspace or tenant.workspace,
        user=user,
        type=key_type,
    )
    clear_workspace_context()
    return key


def _key_client(key):
    client = APIClient(enforce_csrf_checks=True, REMOTE_ADDR=_address())
    client.credentials(HTTP_X_API_KEY=key.api_key, HTTP_X_SECRET_KEY=key.secret_key)
    return client


def _basic_client(key):
    client = APIClient(enforce_csrf_checks=True, REMOTE_ADDR=_address())
    pair = base64.b64encode(f"{key.api_key}:{key.secret_key}".encode()).decode()
    client.credentials(HTTP_AUTHORIZATION=f"Basic {pair}")
    return client


def _tokens(email):
    """Access and refresh tokens from the login endpoint."""
    client = APIClient(enforce_csrf_checks=True, REMOTE_ADDR=_address())
    login = client.post(
        "/accounts/token/", {"email": email, "password": PASSWORD}, format="json"
    )
    assert login.status_code == status.HTTP_200_OK, login.content
    return login.json()["access"], login.json().get("refresh")


def _active_workspace_memberships(user, tenant):
    return WorkspaceMembership.no_workspace_objects.filter(
        user=user, workspace__organization=tenant.organization, is_active=True
    ).count()


def _dataset(tenant, name):
    clear_workspace_context()
    dataset = Dataset.no_workspace_objects.create(
        name=name,
        organization=tenant.organization,
        workspace=tenant.workspace,
        source=DatasetSourceChoices.BUILD.value,
    )
    clear_workspace_context()
    return dataset


def _marketplace_account(label):
    """An account created by the AWS Marketplace sign-up.

    ``process_aws_signup`` is the step the sign-up endpoint runs after its AWS
    entitlement lookup: ``_create_user_account`` creates the user with its
    organization field set (role Owner) and no membership rows, and the
    sign-in path creates none either. The password it e-mails is ``PASSWORD``
    here. Returns the account and its organization.
    """
    from accounts.aws_marketplace_utils import process_aws_signup

    clear_workspace_context()
    account_id = uuid.uuid4().hex[:12]
    organization = Organization.objects.create(
        name=f"AWS Account {account_id}", display_name=f"AWS Account {account_id}"
    )
    customer = AWSMarketplaceCustomer.objects.create(
        customer_identifier=f"conformance-{label.lower()}-{account_id}",
        customer_aws_account_id=account_id,
        product_code="conformance-product",
        organization=organization,
    )
    email = f"marketplace-{label.lower()}-{account_id}@example.com"
    with (
        patch(
            "accounts.aws_marketplace_utils.generate_password", return_value=PASSWORD
        ),
        patch("tfc.temporal.drop_in.start_activity"),
    ):
        process_aws_signup(email, f"Marketplace {label}", customer.customer_identifier)
    clear_workspace_context()
    user = User.objects.get(email=email)
    assert user.organization_id == organization.id
    assert not OrganizationMembership.no_workspace_objects.filter(user=user).exists()
    return user, organization


def _active_organization_membership(user, organization):
    return OrganizationMembership.no_workspace_objects.filter(
        user=user, organization=organization, is_active=True
    ).exists()


def _default_workspace_dataset(organization, name):
    """A dataset in the organization's default workspace (which the account's
    first request created)."""
    clear_workspace_context()
    workspace = Workspace.no_workspace_objects.get(
        organization=organization, is_default=True, is_active=True
    )
    dataset = Dataset.no_workspace_objects.create(
        name=name,
        organization=organization,
        workspace=workspace,
        source=DatasetSourceChoices.BUILD.value,
    )
    clear_workspace_context()
    return dataset


def _bearer_client(email):
    """A client signed in through ``/accounts/token/`` that sends only the
    bearer token, as the web app's login does before it knows an
    organization."""
    access, refresh = _tokens(email)
    client = APIClient(enforce_csrf_checks=True, REMOTE_ADDR=_address())
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    return client, refresh


def _build_world():
    a = make_tenant("A")
    b = make_tenant("B")
    removed_user, removed_client = removed_member(a, "A")
    team_removed_user, a_team = _removed_team_member(a, "A")
    member = _member(a, "A")
    return SimpleNamespace(
        a=a,
        b=b,
        a_team=a_team,
        team_removed_user=team_removed_user,
        a_dataset=_dataset(a, "A data"),
        removed_user=removed_user,
        removed_client=removed_client,
        member=member,
    )


def _join_b(world, user):
    """Make ``user`` an active member of B's default workspace."""
    clear_workspace_context()
    membership = OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=world.b.organization,
        role="Member",
        level=Level.MEMBER,
        is_active=True,
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=user,
        workspace=world.b.workspace,
        role="Workspace Member",
        level=Level.WORKSPACE_MEMBER,
        is_active=True,
        organization_membership=membership,
    )
    clear_workspace_context()


def _membership_rows(user):
    """Every organization and workspace membership row of ``user``, in any
    state (the base managers keep removed rows)."""
    clear_workspace_context()
    fields = ("id", "is_active", "deleted")
    return (
        sorted(
            OrganizationMembership._base_manager.filter(user=user).values_list(*fields)
        ),
        sorted(
            WorkspaceMembership._base_manager.filter(user=user).values_list(*fields)
        ),
    )


def _cancelled_invitee(world, invitation="with-workspace"):
    """An account invited to A whose invitation A's owner then cancelled, and
    which afterwards set a password through the reset link.

    The invitation creates the account inactive, its organization field naming
    A, with an inactive organization membership and, for ``with-workspace``
    (an invitation to A's workspace, as the web app's invite dialog sends
    it), an inactive membership of that workspace; cancelling it removes
    (soft-deletes) those rows; the password reset activates the account.
    ``organization-only`` invites to the organization alone. Returns the
    account.
    """
    owner = sign_in(world.a.user.email, workspace=world.a.workspace)
    email = f"cancelled-{uuid.uuid4().hex[:8]}@example.com"
    body = {"emails": [email], "org_level": Level.MEMBER}
    if invitation == "with-workspace":
        body["workspace_access"] = [
            {"workspace_id": str(world.a.workspace.id), "level": Level.WORKSPACE_MEMBER}
        ]
    invited = owner.post("/accounts/organization/invite/", body, format="json")
    assert invited.status_code == status.HTTP_200_OK, invited.content
    clear_workspace_context()
    invite = OrganizationInvite.objects.get(
        organization=world.a.organization,
        target_email=email,
        status=InviteStatus.PENDING,
    )
    cancelled = owner.delete(
        "/accounts/organization/invite/cancel/",
        {"invite_id": str(invite.id)},
        format="json",
    )
    assert cancelled.status_code == status.HTTP_200_OK, cancelled.content
    clear_workspace_context()
    user = User.objects.get(email=email)
    assert user.organization_id == world.a.organization.id
    organization_rows, workspace_rows = _membership_rows(user)
    assert organization_rows
    assert bool(workspace_rows) == (invitation == "with-workspace")
    assert all(
        deleted and not is_active
        for _, is_active, deleted in organization_rows + workspace_rows
    )

    # forget-password-view, then the reset link from the e-mail (as Cloud
    # sends it).
    anonymous = APIClient(enforce_csrf_checks=True, REMOTE_ADDR=_address())
    with (
        patch("accounts.views.signup.is_oss", return_value=False),
        patch("accounts.views.signup.email_helper") as mailed,
    ):
        initiated = anonymous.post(
            "/accounts/password-reset-initiate/", {"email": email}, format="json"
        )
    assert initiated.status_code == status.HTTP_200_OK, initiated.content
    link = mailed.call_args.args[2]
    confirmed = anonymous.post(
        f"/accounts/password-reset-confirm/{link['uid']}/{link['token']}/",
        {"new_password": PASSWORD, "repeat_password": PASSWORD},
        format="json",
    )
    assert confirmed.status_code == status.HTTP_200_OK, confirmed.content
    clear_workspace_context()
    user.refresh_from_db()
    assert user.is_active
    return user


def _observe_span(tenant, tag):
    """A span in a project of ``tenant``, with a star label on that project."""
    clear_workspace_context()
    project = Project.no_workspace_objects.create(
        name=f"{tag} project",
        organization=tenant.organization,
        workspace=tenant.workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )
    trace = Trace.no_workspace_objects.create(project=project, name=f"{tag} trace")
    span = ObservationSpan.no_workspace_objects.create(
        id=f"span_{uuid.uuid4().hex[:16]}",
        project=project,
        trace=trace,
        name=f"{tag} span",
        observation_type="llm",
        start_time=timezone.now() - timedelta(seconds=5),
        end_time=timezone.now(),
        status="OK",
    )
    label = AnnotationsLabels.no_workspace_objects.create(
        name=f"{tag} rating",
        type=AnnotationTypeChoices.STAR.value,
        settings={"no_of_stars": 5},
        organization=tenant.organization,
        workspace=tenant.workspace,
        project=project,
    )
    clear_workspace_context()
    return SimpleNamespace(project=project, span=span, label=label)


@pytest.fixture
def world(db, settings, monkeypatch):
    monkeypatch.setenv("FUTURE_AGI_TELEMETRY_DISABLED", "true")
    use_production_request_settings(settings)
    data = _build_world()
    yield data
    clear_workspace_context()


@pytest.fixture
def production_request(db, settings, monkeypatch):
    """The production request path, with no tenants built."""
    monkeypatch.setenv("FUTURE_AGI_TELEMETRY_DISABLED", "true")
    use_production_request_settings(settings)
    yield
    clear_workspace_context()


@pytest.fixture
def live_world(transactional_db, settings, monkeypatch):
    """The same tenants, committed, for requests served on other threads
    (WebSocket consumers and the MCP app)."""
    monkeypatch.setenv("FUTURE_AGI_TELEMETRY_DISABLED", "true")
    use_production_request_settings(settings)
    data = _build_world()
    data.removed_token, _ = _tokens(data.removed_user.email)
    data.member_token, _ = _tokens(data.member.email)
    yield data
    clear_workspace_context()


# ------------------------------------------------- HTTP authentication paths


def _removed_account(world, path):
    """The removed member each path signs in as. A key pair names its own
    workspace, so the key paths use the member of A's second workspace: the
    workspace lookup then has no earlier row of that account to find."""
    return world.removed_user if path == "jwt" else world.team_removed_user


def _http_client(world, path, user):
    if path == "jwt":
        if user is world.removed_user:
            return world.removed_client
        return sign_in(user.email, workspace=world.a.workspace)
    workspace = world.a_team if user is world.team_removed_user else None
    key = _key(world.a, user, workspace=workspace)
    if path == "api-key":
        return _key_client(key)
    return _basic_client(key)


def _assert_lists_no_row_and_rejects_like_unknown(world, client):
    listed = client.get(DATASETS_URL)
    assert str(world.a_dataset.id) not in listed.content.decode(), listed.content

    before = Dataset.no_workspace_objects.filter(
        organization=world.a.organization
    ).count()
    unknown_id = uuid.uuid4()
    body = {"new_dataset_name": "copy of the former data"}
    foreign = client.post(_clone_url(world.a_dataset.id), body, format="json")
    unknown = client.post(_clone_url(unknown_id), body, format="json")

    assert foreign.status_code >= 400, foreign.content
    assert same_answer(foreign, world.a_dataset.id, unknown, unknown_id), (
        foreign.content,
        unknown.content,
    )
    assert (
        Dataset.no_workspace_objects.filter(organization=world.a.organization).count()
        == before
    )


def _assert_resolves_to_no_organization(world, path):
    account = _removed_account(world, path)
    client = _http_client(world, path, account)
    memberships_before = _active_workspace_memberships(account, world.a)

    if path == "langfuse-basic":
        response = client.get(LANGFUSE_HEALTH_URL)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED, response.content
        assert response.json()["code"] == "authentication_failed"
        assert "create_url" not in response.json()
        assert response["WWW-Authenticate"] == "ApiKey"
    else:
        _assert_lists_no_row_and_rejects_like_unknown(world, client)

    # No request writes an access row for the account. (Removal leaves the
    # row of A's second workspace active, as on dev and main; it grants
    # nothing without an active organization membership.)
    assert _active_workspace_memberships(account, world.a) == memberships_before
    # The user record itself is left as it was.
    account.refresh_from_db()
    assert account.organization_id == world.a.organization.id


@pytest.mark.integration
@pytest.mark.api
class TestInactiveMembershipResolvesToNoOrganization:
    @pytest.mark.parametrize("path", ["jwt"])
    def test_inactive_membership_resolves_to_no_organization(self, world, path):
        _assert_resolves_to_no_organization(world, path)

    def test_inactive_membership_jwt_request_is_refused_with_its_code(self, world):
        """The refusal names its reason, so the web app can tell it from a
        permission error."""
        response = world.removed_client.get(DATASETS_URL)

        assert _refused(response), response.content


@pytest.mark.integration
@pytest.mark.api
class TestInactiveMembershipKeyPathsStayRefused:
    """An inactive member's personal key is refused before workspace lookup.

    Header and Langfuse Basic credentials cannot list the former organization's
    data or create access rows. Basic now reports an authentication failure
    rather than letting a stale membership reach the workspace permission check.
    """

    @pytest.mark.parametrize("path", ["api-key", "langfuse-basic"])
    def test_control_inactive_membership_key_paths_stay_refused(self, world, path):
        _assert_resolves_to_no_organization(world, path)


@pytest.mark.integration
@pytest.mark.api
class TestActiveMemberKeepsAccess:
    @pytest.mark.parametrize("path", ["jwt", "api-key", "langfuse-basic"])
    def test_active_member_keeps_access(self, world, path):
        client = _http_client(world, path, world.member)

        if path == "langfuse-basic":
            response = client.get(LANGFUSE_HEALTH_URL)
            assert response.status_code == status.HTTP_200_OK, response.content
            return
        listed = client.get(DATASETS_URL)
        assert listed.status_code == status.HTTP_200_OK, listed.content
        assert str(world.a_dataset.id) in listed.content.decode()

    def test_owner_keeps_access_and_the_personal_api_key_works(self, world):
        owner = sign_in(world.a.user.email, workspace=world.a.workspace)
        listed = owner.get(DATASETS_URL)
        assert listed.status_code == status.HTTP_200_OK, listed.content
        assert str(world.a_dataset.id) in listed.content.decode()

        from accounts.tests.personal_keys import personal_key_for

        clear_workspace_context()
        personal_key = personal_key_for(
            world.a.user, world.a.organization, workspace=world.a.workspace
        )
        clear_workspace_context()
        listed = _key_client(personal_key).get(DATASETS_URL)
        assert listed.status_code == status.HTTP_200_OK, listed.content
        assert str(world.a_dataset.id) in listed.content.decode()
        assert personal_key.user_id == world.a.user.pk
        assert personal_key.type == "user"

    def test_member_active_elsewhere_resolves_that_organization(self, world):
        """A member removed from A who still belongs to B is answered for B,
        and its record's former organization reaches nothing."""
        user = world.removed_user
        _join_b(world, user)
        b_dataset = _dataset(world.b, "B data")
        client = sign_in(user.email, workspace=world.b.workspace)

        listed = client.get(DATASETS_URL)

        assert listed.status_code == status.HTTP_200_OK, listed.content
        assert str(b_dataset.id) in listed.content.decode()
        assert str(world.a_dataset.id) not in listed.content.decode()


@pytest.mark.integration
@pytest.mark.api
class TestAccountOlderThanMembershipsKeepsWorking:
    """CONTROL: an account older than memberships keeps working.

    Its organization field names its organization and it has no membership
    rows at all, as the AWS Marketplace sign-up and the Appsmith user endpoint
    create accounts. Plain dev and plain main serve it its organization, and
    its first request creates its organization membership; this fix must keep
    both.
    """

    def test_control_account_older_than_memberships_keeps_working(
        self, production_request
    ):
        user, organization = _marketplace_account("Jwt")
        client, _ = _bearer_client(user.email)

        info = client.get("/accounts/user-info/")

        assert info.status_code == status.HTTP_200_OK, info.content
        assert info.json().get("requires_org_setup") is not True, info.content
        assert info.json()["organization"]["id"] == str(organization.id)
        assert _active_organization_membership(user, organization)

        dataset = _default_workspace_dataset(organization, "Marketplace data")
        listed = client.get(DATASETS_URL)
        assert listed.status_code == status.HTTP_200_OK, listed.content
        assert str(dataset.id) in listed.content.decode()

    def test_legacy_system_key_cannot_heal_an_account_older_than_memberships(
        self, production_request
    ):
        """A retired pair cannot become the first account or create membership."""
        user, organization = _marketplace_account("Key")
        clear_workspace_context()
        system_key = OrgApiKey.no_workspace_objects.create(
            organization=organization, type="system"
        )
        clear_workspace_context()
        client = _key_client(system_key)

        first = client.get(DATASETS_URL)

        assert first.status_code == status.HTTP_401_UNAUTHORIZED, first.content
        assert first.json()["code"] == "api_key_retired"
        assert first.json()["create_url"] == "/dashboard/keys"
        assert not _active_organization_membership(user, organization)
        assert not Workspace.no_workspace_objects.filter(
            organization=organization
        ).exists()
        # Seed pre-existing data explicitly: refused authentication must not
        # bootstrap the default workspace needed by this second read probe.
        Workspace.no_workspace_objects.create(
            name="Stored workspace",
            organization=organization,
            created_by=user,
            is_default=True,
            is_active=True,
        )
        dataset = _default_workspace_dataset(organization, "Marketplace key data")
        listed = client.get(DATASETS_URL)
        assert listed.status_code == status.HTTP_401_UNAUTHORIZED, listed.content
        assert str(dataset.id) not in listed.content.decode()
        assert not _active_organization_membership(user, organization)


INVITATIONS = ("organization-only", "with-workspace")


@pytest.mark.integration
@pytest.mark.api
class TestCancelledInvitationGrantsNothing:
    @pytest.mark.django_db(transaction=True)
    @pytest.mark.parametrize("invitation", INVITATIONS)
    def test_cancelled_invitation_grants_no_organization_after_a_password_reset(
        self, live_world, invitation
    ):
        """An invitation sent, then cancelled; the invitee then sets a
        password through the reset link and signs in through
        ``/accounts/token/``, sending only the bearer token as the web app
        does. Its record still names A, but its membership rows are all
        removed: the request is refused with ``no_active_organization``,
        lists none of A's rows, and writes no membership row. (Committed
        rows, as in production: a failed insert must not roll back the
        others.)"""
        world = live_world
        user = _cancelled_invitee(world, invitation)
        rows_before = _membership_rows(user)
        client, _ = _bearer_client(user.email)

        listed = client.get(DATASETS_URL)

        assert _refused(listed), listed.content
        assert str(world.a_dataset.id) not in listed.content.decode()
        assert _membership_rows(user) == rows_before
        user.refresh_from_db()
        assert user.organization_id == world.a.organization.id

    def test_cancelled_invitation_membership_is_not_recreated(self, world):
        """The single create path for workspace memberships (which the
        authentication fallback and the workspace member views call) creates
        no organization membership next to the removed one."""
        from accounts.services.workspace_membership import create_workspace_membership

        user = _cancelled_invitee(world)
        organization_rows, _ = _membership_rows(user)

        # A's second workspace: the invitee never had a row there.
        workspace_membership = create_workspace_membership(
            workspace=world.a_team,
            user=user,
            role="Workspace Member",
            level=Level.WORKSPACE_MEMBER,
        )

        assert _membership_rows(user)[0] == organization_rows
        assert workspace_membership.organization_membership_id is None


def _add_annotations(client, b):
    return client.post(
        "/tracer/observation-span/add_annotations/",
        {"observation_span_id": b.span.id, "annotation_values": {str(b.label.id): 4}},
        format="json",
    )


# Writes whose view stamps rows with ``request.user.organization``
# (tracer/views/observation_span.py add_annotations: the Score, its default
# annotation queue and queue item).
WRITES_IN_B = {"observation-span-add-annotations": _add_annotations}


def _annotation_rows(organization):
    """How many scores, annotation queues and queue items carry
    ``organization``, removed ones included."""
    clear_workspace_context()
    return tuple(
        model._base_manager.filter(organization=organization).count()
        for model in (Score, AnnotationQueue, QueueItem)
    )


@pytest.mark.integration
@pytest.mark.api
class TestRemovedMemberActiveInBWritesOnlyToB:
    @pytest.mark.parametrize("write", list(WRITES_IN_B))
    def test_removed_member_active_in_b_write_lands_only_in_b(self, world, write):
        """A member removed from A who is an active member of B annotates a
        span of B. Every row the write creates carries B, and none carries A,
        the organization its user record still names."""
        user = world.removed_user
        _join_b(world, user)
        b = _observe_span(world.b, "B")
        client = sign_in(user.email, workspace=world.b.workspace)
        a_rows_before = _annotation_rows(world.a.organization)

        response = WRITES_IN_B[write](client, b)

        assert response.status_code == status.HTTP_200_OK, response.content
        result = response.json().get("result", response.json())
        assert str(b.label.id) in result["success_labels"], response.content
        clear_workspace_context()
        written = {
            "score": Score._base_manager.filter(observation_span_id=b.span.id),
            "queue item": QueueItem._base_manager.filter(observation_span_id=b.span.id),
            "annotation queue": AnnotationQueue._base_manager.filter(project=b.project),
        }
        organizations = {
            kind: set(rows.values_list("organization_id", flat=True))
            for kind, rows in written.items()
        }
        assert organizations == {kind: {world.b.organization.id} for kind in written}, (
            organizations
        )
        assert _annotation_rows(world.a.organization) == a_rows_before
        user.refresh_from_db()
        assert user.organization_id == world.a.organization.id

    def test_control_member_still_active_in_its_legacy_organization_keeps_it(
        self, world
    ):
        """CONTROL: a member active in A, the organization its user record
        names, and also in B, keeps A as ``request.user.organization`` while
        it acts in B, as on dev and main (the annotation view hands that
        organization to the default-queue lookup)."""
        from model_hub.utils import annotation_queue_helpers

        user = world.member
        _join_b(world, user)
        b = _observe_span(world.b, "B")
        client = sign_in(user.email, workspace=world.b.workspace)

        with patch.object(
            annotation_queue_helpers,
            "resolve_default_queue_item_for_source",
            wraps=annotation_queue_helpers.resolve_default_queue_item_for_source,
        ) as resolve:
            response = _add_annotations(client, b)

        assert response.status_code == status.HTTP_200_OK, response.content
        assert {call.args[2].id for call in resolve.call_args_list} == {
            world.a.organization.id
        }


# ----------------------------------------------- WebSocket and MCP (tfc.asgi)


def _communicator(path, token, query=""):
    from saml2_auth.tests.ws_helpers import WebsocketCommunicator
    from tfc.asgi import application

    return WebsocketCommunicator(application, f"{path}?token={token}{query}")


@asynccontextmanager
async def _mcp_session(monkeypatch, headers):
    """An MCP client session over ``/mcp``, routed by ``tfc.asgi``."""
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    from mcp_server import mcp_app
    from tfc.asgi import application

    monkeypatch.setattr(mcp_app, "_streamable_app", None)
    monkeypatch.setattr(mcp_app, "_session_manager", None)
    mcp_app.get_mcp_streamable_app()
    async with (
        mcp_app._session_manager.run(),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application),
            base_url="http://localhost",
            headers=headers,
        ) as http_client,
        streamable_http_client(
            "http://localhost/mcp", http_client=http_client
        ) as streams,
    ):
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            yield session


def _mcp_headers(world, path, user):
    if path == "mcp-oauth":
        from mcp_server.oauth_utils import generate_oauth_token

        token, _ = generate_oauth_token(
            user.id,
            world.a.organization.id,
            world.a.workspace.id,
            "conformance-client",
            "context datasets",
        )
        return {"Authorization": f"Bearer {token}"}
    # Preserve the actor's current membership, including removed-member cases.
    key = _key(world.a, user, key_type="user")
    return {"X-Api-Key": key.api_key, "X-Secret-Key": key.secret_key}


def _mcp_connections(user):
    from mcp_server.models.connection import MCPConnection

    return MCPConnection.no_workspace_objects.filter(user=user).count()


SOCKET_PATHS = ("websocket", "simulation-updates", "falcon-ai")


def _falcon_consumer_is_routed():
    """Whether ``tfc.asgi`` serves ``ws/falcon-ai/`` with the Falcon AI
    consumer (not the EE upgrade stub)."""
    from ee.falcon_ai.consumers import FalconAIConsumer
    from sockets.routing import websocket_urlpatterns

    return any(
        getattr(pattern.callback, "consumer_class", None) is FalconAIConsumer
        for pattern in websocket_urlpatterns
    )


def _open_socket_gates(monkeypatch, path):
    """Hold open what each consumer decides after the organization.

    - ``falcon-ai``: the organization's EE entitlement (a licence check),
      which would otherwise close the socket with 4003 for every account;
    - ``simulation-updates``: the Redis subscription that forwards updates
      once the socket is accepted.
    """
    if path == "falcon-ai":
        assert _falcon_consumer_is_routed(), "ws/falcon-ai/ is served by the stub"
        monkeypatch.setattr(
            "ee.falcon_ai.consumers.check_ee_feature", lambda *args, **kwargs: None
        )
    elif path == "simulation-updates":
        from sockets.simulation_consumer import SimulationUpdateConsumer

        async def _no_subscription(consumer):
            return None

        monkeypatch.setattr(
            SimulationUpdateConsumer, "_subscribe_redis", _no_subscription
        )


def _socket_route(world, path):
    if path == "simulation-updates":
        return "/ws/simulation-updates/", f"&test_id={uuid.uuid4()}"
    if path == "falcon-ai":
        # The web app sends the workspace the session last used.
        return "/ws/falcon-ai/", f"&workspace_id={world.a.workspace.id}"
    return "/ws/connect/", ""


async def _socket_answer(token, route="/ws/connect/", query=""):
    communicator = _communicator(route, token, query)
    connected, code = await communicator.connect(timeout=5)
    if connected:
        await communicator.disconnect()
    return connected, code


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
class TestInactiveMembershipResolvesToNoOrganizationLive:
    @pytest.mark.parametrize("path", [*SOCKET_PATHS, "mcp-oauth", "mcp-api-key"])
    async def test_inactive_membership_resolves_to_no_organization(
        self, live_world, monkeypatch, path
    ):
        world = live_world
        if path in SOCKET_PATHS:
            _open_socket_gates(monkeypatch, path)
            route, query = _socket_route(world, path)
            connected, code = await _socket_answer(world.removed_token, route, query)
            # The consumers' existing answer for an account with no
            # organization: closed before joining any organization's group.
            assert (connected, code) == (False, 4002)
            return

        account = world.removed_user
        headers = await sync_to_async(_mcp_headers)(world, path, account)
        if path == "mcp-api-key":
            import httpx

            from mcp_server import mcp_app
            from tfc.asgi import application

            monkeypatch.setattr(mcp_app, "_streamable_app", None)
            monkeypatch.setattr(mcp_app, "_session_manager", None)
            mcp_app.get_mcp_streamable_app()
            rows_before = await sync_to_async(_membership_rows)(account)
            # Authentication refuses the inactive person's key before an MCP
            # session can initialize, list tools, or call a tool.
            requests = (
                (
                    "initialize",
                    {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "membership-check", "version": "1"},
                    },
                ),
                ("tools/list", {}),
                ("tools/call", {"name": "list_datasets", "arguments": {}}),
            )
            async with (
                mcp_app._session_manager.run(),
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=application),
                    base_url="http://localhost",
                    headers={
                        **headers,
                        "Accept": "application/json, text/event-stream",
                    },
                ) as client,
            ):
                for index, (method, params) in enumerate(requests, 1):
                    response = await client.post(
                        "/mcp",
                        json={
                            "jsonrpc": "2.0",
                            "id": index,
                            "method": method,
                            "params": params,
                        },
                    )
                    assert response.status_code == 401, response.content
                    assert response.json()["error"] == "invalid_token"
                    assert "create_url" not in response.json()
                    assert "result" not in response.json()
                    assert str(world.a_dataset.id) not in response.text
            assert await sync_to_async(_mcp_connections)(account) == 0
            assert await sync_to_async(_membership_rows)(account) == rows_before
            return
        async with _mcp_session(monkeypatch, headers) as session:
            tools = await session.list_tools()
            called = await session.call_tool("list_datasets", {})

        assert tools.tools == []
        assert called.isError is True
        assert str(world.a_dataset.id) not in str(called.structuredContent)
        assert await sync_to_async(_mcp_connections)(account) == 0


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
class TestActiveMemberKeepsAccessLive:
    @pytest.mark.parametrize("path", [*SOCKET_PATHS, "mcp-oauth", "mcp-api-key"])
    async def test_active_member_keeps_access(self, live_world, monkeypatch, path):
        world = live_world
        if path in SOCKET_PATHS:
            _open_socket_gates(monkeypatch, path)
            route, query = _socket_route(world, path)
            connected, code = await _socket_answer(world.member_token, route, query)
            assert connected is True, code
            return

        headers = await sync_to_async(_mcp_headers)(world, path, world.member)
        async with _mcp_session(monkeypatch, headers) as session:
            tools = await session.list_tools()

        assert tools.tools, "an active member lists its enabled tools"


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
class TestCancelledInvitationGrantsNothingLive:
    async def test_cancelled_invitation_socket_resolves_no_organization(
        self, live_world
    ):
        """The same invitee, signed in after the password reset, opens
        ``ws/connect/``: it is closed before joining any organization's group,
        and no membership row is written."""
        user = await sync_to_async(_cancelled_invitee)(live_world)
        rows_before = await sync_to_async(_membership_rows)(user)
        token, _ = await sync_to_async(_tokens)(user.email)

        connected, code = await _socket_answer(token)

        assert (connected, code) == (False, 4002)
        assert await sync_to_async(_membership_rows)(user) == rows_before


# ------------------------------------------- sign-up to the first organization


def _url_patterns(resolver=None, prefix=""):
    resolver = resolver or get_resolver()
    for pattern in resolver.url_patterns:
        if isinstance(pattern, URLResolver):
            yield from _url_patterns(pattern, prefix + str(pattern.pattern))
        elif isinstance(pattern, URLPattern):
            yield prefix + str(pattern.pattern), pattern.callback


def _views_allowing_no_organization():
    """Every mounted view that serves an account with no organization."""
    views = set()
    for _, callback in _url_patterns():
        view_cls = getattr(callback, "cls", None)
        if getattr(view_cls, "allows_no_organization", False):
            views.add(view_cls)
    return views


class _Walk:
    """Requests of the web app, each recorded with the view that served it."""

    def __init__(self):
        self.views = set()

    def __call__(self, client, method, path, body=None):
        response = getattr(client, method)(path, body, format="json")
        match = resolve(path.split("?")[0])
        self.views.add(getattr(match.func, "cls", None))
        assert not _refused(response), (method, path, response.content)
        # A view that serves the request must also answer it without an error.
        assert response.status_code < 500, (method, path, response.content)
        return response


@pytest.mark.integration
@pytest.mark.api
class TestSignUpToFirstOrganization:
    @patch.dict("os.environ", {"ENV_TYPE": "local"})
    @patch("tfc.temporal.drop_in.start_activity")
    def test_sign_up_to_first_organization_passes_every_allowlisted_view(
        self, _start_activity, world
    ):
        """Sign-up, then each route the web app calls for an account with no
        organization (frontend file:line in the comments), then the first
        organization. None of them answers with the no-organization refusal
        or with a server error, and the walk covers every view that carries
        the marker.

        A guard for the allowlist, not a red-to-green test: plain dev and
        plain main refuse nothing with ``no_active_organization`` and mark no
        view, so it passes there by construction."""
        walk = _Walk()
        anonymous = APIClient(enforce_csrf_checks=True, REMOTE_ADDR=_address())

        # jwt-register-view (auth-provider.jsx:295): the e-mail sign-up, which
        # creates the account's first organization itself.
        email = f"walk-{uuid.uuid4().hex[:8]}@example.com"
        walk(
            anonymous,
            "post",
            "/accounts/signup/",
            {
                "email": email,
                "password": PASSWORD,
                "full_name": "Walk Sign-up",
                "company_name": "Walk Org",
            },
        )
        signed_up = User.objects.get(email=email)
        assert OrganizationMembership.no_workspace_objects.filter(
            user=signed_up, is_active=True
        ).exists()

        # E-mail, OAuth and SSO sign-ups all create the account's first
        # organization. An account has none once its memberships are all
        # inactive: here a member that A, its only organization, removed
        # (user-info answers requires_org_setup).
        user = world.removed_user
        access, refresh = _tokens(user.email)
        client = APIClient(enforce_csrf_checks=True, REMOTE_ADDR=_address())
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        unknown = uuid.uuid4().hex

        # auth-provider.jsx:116 and :251 (initialize, login): the auth guard
        # (auth-guard.jsx:162) routes requires_org_setup to org-removed.
        info = walk(client, "get", "/accounts/user-info/")
        assert info.json().get("requires_org_setup") is True, info.content
        # OrganizationContext.jsx:166 (seedFromMembership).
        walk(client, "get", "/accounts/organizations/")
        # RegionSelect.jsx:16 (public config) and useDeploymentMode.js:18,
        # which the auth guard's usePostLoginPath calls.
        walk(client, "get", "/accounts/config/")
        walk(client, "get", "/api/deployment-info/")
        # auth/context/jwt/utils.js:93 (token refresh).
        walk(client, "post", "/accounts/token/refresh/", {"refresh": refresh})
        # setup-org.jsx:275 (onboarding answers).
        walk(client, "get", "/accounts/onboarding/")
        # invite-accepted.jsx:46 and :95: an invitation link opened while
        # signed in (an unknown link here, answered as such).
        walk(client, "get", f"/accounts/accept-invitation/{unknown}/{unknown}/")
        walk(
            client,
            "post",
            f"/accounts/accept-invitation/{unknown}/{unknown}/",
            {"new_password": PASSWORD, "repeat_password": PASSWORD},
        )
        # The account pages that may still carry the session's token:
        # activation, password reset (forget-password-view, password reset
        # view) and signing in again. The activation link is well formed and
        # names no account (an undecodable one answers 500 on plain dev and
        # plain main as well, outside this change).
        unknown_uid = urlsafe_base64_encode(force_bytes(uuid.uuid4()))
        walk(client, "get", f"/accounts/activate/{unknown_uid}/{unknown}/")
        walk(
            client,
            "post",
            "/accounts/password-reset-initiate/",
            {"email": "not-an-address"},
        )
        walk(
            client,
            "post",
            f"/accounts/password-reset-confirm/{unknown}/{unknown}/",
            {"new_password": PASSWORD, "repeat_password": PASSWORD},
        )
        walk(
            client,
            "post",
            "/accounts/signup/",
            {"email": user.email, "password": PASSWORD, "full_name": "Again"},
        )
        # routes/sections/index.jsx:88, the public shared viewer (no auth
        # guard), which sends the session's token: utils/axios.js:1854.
        walk(client, "get", f"/tracer/shared/{unknown}/")
        # OrganizationContext.jsx:200 (switch) before the account has an
        # organization: the view answers that this account cannot act in its
        # former organization.
        switched = walk(
            client,
            "post",
            "/accounts/organizations/switch/",
            {"organization_id": str(world.a.organization.id)},
        )
        assert switched.status_code == status.HTTP_403_FORBIDDEN, switched.content
        # auth-provider.jsx:346 (logout) in a second session of the account (a
        # second browser), so this session stays signed in.
        other_access, other_refresh = _tokens(user.email)
        other = APIClient(enforce_csrf_checks=True, REMOTE_ADDR=_address())
        other.credentials(HTTP_AUTHORIZATION=f"Bearer {other_access}")
        logged_out = walk(
            other, "post", "/accounts/logout/", {"refresh": other_refresh}
        )
        assert logged_out.status_code == status.HTTP_200_OK, logged_out.content
        # org-removed.jsx:24: the first organization.
        created = walk(
            client,
            "post",
            "/accounts/organizations/create/",
            {"organization_name": "Walk First Org"},
        )
        assert created.status_code == status.HTTP_201_CREATED, created.content
        organization_id = created.json().get("result", created.json())[
            "organization_id"
        ]

        # After creation: initialize() again, then setup-org.jsx.
        info = walk(client, "get", "/accounts/user-info/")
        assert info.json().get("requires_org_setup") is not True, info.content
        assert info.json()["organization"]["id"] == organization_id
        # OrganizationContext.jsx:200 (switch to the new organization).
        walk(
            client,
            "post",
            "/accounts/organizations/switch/",
            {"organization_id": organization_id},
        )
        # setup-org.jsx:313 (save onboarding answers), :192 and :263 (members).
        walk(
            client,
            "post",
            "/accounts/onboarding/",
            {"role": "ML Engineer", "goals": ["Evaluations"]},
        )
        walk(client, "get", "/accounts/team/users/")
        listed = walk(client, "get", DATASETS_URL)
        assert listed.status_code == status.HTTP_200_OK, listed.content
        # auth-provider.jsx:346 (logout).
        walk(client, "post", "/accounts/logout/", {"refresh": refresh})

        missing = {
            view.__name__ for view in _views_allowing_no_organization() - walk.views
        }
        assert not missing, f"allowlisted views the walk does not reach: {missing}"

    def test_marketplace_sign_up_reaches_its_organization(self, production_request):
        """CONTROL: an account older than memberships keeps working. The AWS
        Marketplace sign-up (jwt-register-view.jsx:185, marketplaceRegister)
        creates the account with its organization field only; the e-mailed
        password then signs it in (auth-provider.jsx:251, login), and the web
        app's first requests are served that organization."""
        walk = _Walk()
        user, organization = _marketplace_account("Walk")
        client, refresh = _bearer_client(user.email)

        # auth-provider.jsx:251 (login) and :116 (initialize).
        info = walk(client, "get", "/accounts/user-info/")
        assert info.status_code == status.HTTP_200_OK, info.content
        assert info.json().get("requires_org_setup") is not True, info.content
        assert info.json()["organization"]["id"] == str(organization.id)
        assert _active_organization_membership(user, organization)
        # OrganizationContext.jsx:166 (seedFromMembership).
        organizations = walk(client, "get", "/accounts/organizations/")
        assert organizations.status_code == status.HTTP_200_OK, organizations.content
        assert str(organization.id) in organizations.content.decode()
        # setup-org.jsx:275 (onboarding answers).
        walk(client, "get", "/accounts/onboarding/")
        listed = walk(client, "get", DATASETS_URL)
        assert listed.status_code == status.HTTP_200_OK, listed.content
        # auth-provider.jsx:346 (logout).
        walk(client, "post", "/accounts/logout/", {"refresh": refresh})
