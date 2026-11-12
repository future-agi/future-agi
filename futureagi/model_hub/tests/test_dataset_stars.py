"""Personal dataset stars persist without widening dataset visibility."""

import uuid
from contextlib import nullcontext

import pytest
from django.apps import apps
from django.test import override_settings
from rest_framework.test import APIClient, APIRequestFactory
from rest_framework.views import APIView

from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from model_hub.models.develop_dataset import Dataset
from model_hub.tests.test_dsx_l1_dataset_list_tenancy import _client, _tenant
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import clear_workspace_context

LIST = "/model-hub/develops/get-datasets/"


def star_url(dataset):
    return f"/model-hub/datasets/{dataset.id}/star/"


def listed(client, **params):
    response = client.get(LIST, {"page_size": 100, **params})
    assert response.status_code == 200, response.content
    return response.data["result"]


def stars():
    return apps.get_model("model_hub", "DatasetStar").objects


def _login_client(actor):
    """Use application-issued tokens and the real workspace write guard."""
    client = APIClient(HTTP_HOST="localhost")
    login = client.post(
        "/accounts/token/",
        {
            "email": actor["user"].email,
            # _tenant owners and _workspace_actor members use different fixtures.
            "password": actor.get("password", "l1-test-password"),
        },
        format="json",
    )
    assert login.status_code == 200, login.content
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {login.data['access']}",
        HTTP_X_ORGANIZATION_ID=str(actor["org"].id),
        HTTP_X_WORKSPACE_ID=str(actor["workspace"].id),
    )
    return client


def _workspace_actor(tenant, role, level):
    clear_workspace_context()
    user = User.objects.create_user(
        email=f"star-role-{uuid.uuid4().hex}@example.test",
        password="synthetic-star-password",
        name="Star role fixture",
        organization=tenant["org"],
        organization_role=role,
    )
    membership, _ = OrganizationMembership.no_workspace_objects.update_or_create(
        user=user,
        organization=tenant["org"],
        defaults={"role": role, "level": level, "is_active": True},
    )
    WorkspaceMembership.no_workspace_objects.update_or_create(
        user=user,
        workspace=tenant["workspace"],
        defaults={
            "role": Level.to_ws_role(level),
            "level": level,
            "is_active": True,
            "organization_membership": membership,
        },
    )
    return {**tenant, "user": user, "password": "synthetic-star-password"}


@pytest.fixture
def actors(db, settings):
    # Existing positive contracts exercise the enabled implementation.
    settings.DSX_DATASET_STARS_ENABLED = True
    a, b = _tenant("star-a"), _tenant("star-b")
    clients = [_client(a), _client(b)]
    yield a, b, clients
    for client in clients:
        client.stop_workspace_injection()
    clear_workspace_context()


@pytest.mark.django_db
def test_star_persists_and_repeated_requests_are_idempotent(actors):
    a, _b, (client, _) = actors
    dataset = a["dataset"]
    assert listed(client)["datasets"][0]["starred"] is False
    for _ in range(2):
        response = client.post(star_url(dataset), {}, format="json")
        assert response.status_code == 200, response.content
        assert response.data["result"] == {
            "dataset_id": str(dataset.id),
            "starred": True,
        }
    assert stars().filter(dataset=dataset, user=a["user"]).count() == 1
    assert listed(client)["datasets"][0]["starred"] is True
    assert listed(client, starred="true")["total_count"] == 1
    for _ in range(2):
        response = client.delete(star_url(dataset))
        assert response.status_code == 200, response.content
        assert response.data["result"]["starred"] is False
    assert not stars().filter(dataset=dataset, user=a["user"]).exists()
    assert listed(client, starred="true")["datasets"] == []
    assert listed(client)["datasets"][0]["starred"] is False


@pytest.mark.django_db
def test_starred_filter_runs_before_pagination_and_preserves_search(actors):
    a, _b, (client, _) = actors
    target = a["dataset"]
    for number in range(3):
        Dataset.no_workspace_objects.create(
            name=f"star-decoy-{number}",
            organization=a["org"],
            workspace=a["workspace"],
            user=a["user"],
        )
    response = client.post(star_url(target), {}, format="json")
    assert response.status_code == 200, response.content
    result = listed(client, starred="true", page_size=1)
    assert [row["id"] for row in result["datasets"]] == [str(target.id)]
    assert result["total_count"] == result["total_pages"] == 1
    assert listed(client, starred="true", page=1, page_size=1)["datasets"] == []
    assert listed(client, starred="true", search_text="star-decoy")["datasets"] == []
    assert listed(client, starred="false")["total_count"] == 4
    assert listed(client)["datasets"][0]["id"] == str(target.id)


@pytest.mark.django_db
def test_foreign_org_cannot_star_or_remove_an_existing_star(actors):
    a, b, (client, other) = actors
    assert client.post(star_url(a["dataset"]), {}, format="json").status_code == 200
    # Positive control proves the second principal can use the endpoint.
    assert other.post(star_url(b["dataset"]), {}, format="json").status_code == 200
    for method in (other.post, other.delete):
        response = method(star_url(a["dataset"]))
        assert response.status_code == 404, response.content
        assert a["dataset"].name.encode() not in response.content
    assert stars().filter(dataset=a["dataset"], user=a["user"]).count() == 1
    assert not stars().filter(dataset=a["dataset"], user=b["user"]).exists()
    assert [row["id"] for row in listed(other, starred="true")["datasets"]] == [
        str(b["dataset"].id)
    ]


@pytest.mark.django_db
def test_star_is_personal_even_for_a_viewer_in_the_same_workspace(actors):
    a, _b, clients = actors
    for forced_client in clients:
        forced_client.stop_workspace_injection()
    viewer = _workspace_actor(a, OrganizationRoles.MEMBER_VIEW_ONLY, Level.VIEWER)
    client, other = _login_client(a), _login_client(viewer)
    try:
        assert client.post(star_url(a["dataset"]), {}, format="json").status_code == 200
        assert listed(other)["datasets"][0]["starred"] is False
        assert listed(other, starred="true")["datasets"] == []
        assert other.post(star_url(a["dataset"]), {}, format="json").status_code == 200
        assert other.delete(star_url(a["dataset"])).status_code == 200
        assert listed(client)["datasets"][0]["starred"] is True
        assert listed(other)["datasets"][0]["starred"] is False
        # Personal preferences must not grant shared dataset write access.
        denied = other.put(
            f"/model-hub/develops/{a['dataset'].id}/edit_dataset_behavior/",
            {"description": "viewer must not edit shared details"},
            format="json",
        )
        assert denied.status_code == 403, denied.content
        a["dataset"].refresh_from_db()
        assert a["dataset"].description == ""
    finally:
        clear_workspace_context()


@pytest.mark.django_db
def test_sibling_workspace_and_deleted_dataset_are_refused(actors):
    a, _b, (client, _) = actors
    clear_workspace_context()
    sibling = Workspace.objects.create(
        name="Sibling stars",
        organization=a["org"],
        created_by=a["user"],
        is_active=True,
        is_default=False,
    )
    target = Dataset.no_workspace_objects.create(
        name="Sibling star target",
        organization=a["org"],
        workspace=sibling,
    )
    for method in (client.post, client.delete):
        response = method(star_url(target))
        assert response.status_code == 404, response.content
    assert not stars().filter(dataset=target).exists()
    assert client.post(star_url(a["dataset"]), {}, format="json").status_code == 200
    Dataset.no_workspace_objects.filter(pk=a["dataset"].pk).update(deleted=True)
    for method in (client.post, client.delete):
        response = method(star_url(a["dataset"]))
        assert response.status_code == 404, response.content
    assert listed(client, starred="true")["datasets"] == []


@pytest.mark.django_db
@pytest.mark.parametrize("body", [{"user_id": "other"}, {"starred": False}])
def test_star_rejects_unknown_body_fields(actors, body):
    a, _b, (client, _) = actors
    response = client.post(star_url(a["dataset"]), body, format="json")
    assert response.status_code == 400, response.content
    assert not stars().filter(dataset=a["dataset"]).exists()


@pytest.mark.django_db
def test_starred_filter_rejects_invalid_boolean(actors):
    _a, _b, (client, _) = actors
    response = client.get(LIST, {"starred": "not-a-boolean"})
    assert response.status_code == 400, response.content


@pytest.fixture
def real_actors(db, settings):
    # Match production request-context cleanup without force_authenticate or
    # the WorkspaceAwareAPIClient patch of APIView.initial.
    settings.MIDDLEWARE = [
        *settings.MIDDLEWARE,
        "tfc.middleware.workspace_context.WorkspaceContextMiddleware",
    ]
    owner, foreign = _tenant("star-gate-a"), _tenant("star-gate-b")
    member = _workspace_actor(owner, OrganizationRoles.MEMBER, Level.MEMBER)
    viewer = _workspace_actor(owner, OrganizationRoles.MEMBER_VIEW_ONLY, Level.VIEWER)
    yield {"owner": owner, "member": member, "viewer": viewer, "foreign": foreign}
    clear_workspace_context()


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["owner", "member", "viewer", "foreign"])
def test_real_client_authenticates_every_workspace_role(real_actors, role):
    """Prove fixture credentials reach the view, independently of the star gate."""
    actor = real_actors[role]
    client = _login_client(actor)
    result = listed(client)
    assert [row["id"] for row in result["datasets"]] == [str(actor["dataset"].id)]
    assert result["datasets"][0]["starred"] is False
    assert not stars().exists()


def _stored_stars():
    return list(
        stars().order_by("pk").values_list("pk", "dataset_id", "user_id", "created_at")
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "explicit_off", [False, True], ids=["default", "override-false"]
)
@pytest.mark.parametrize(
    "role", ["owner", "member", "viewer", "foreign", "anonymous", "invalid-token"]
)
def test_off_refuses_every_role_and_body_without_changes(
    real_actors, settings, explicit_off, role
):
    owner = real_actors["owner"]
    # Seed existing bookmarks so DELETE must preserve them, not merely leave
    # an already-empty table alone. POST for member/viewer would create a row.
    for actor in (owner, real_actors["foreign"]):
        stars().create(dataset=actor["dataset"], user=actor["user"])
    before = _stored_stars()
    clients = {name: _login_client(actor) for name, actor in real_actors.items()}
    client = clients.get(role, APIClient())
    if role == "invalid-token":
        client.credentials(HTTP_AUTHORIZATION="Bearer not-a-valid-token")
    listing_before = {name: listed(c) for name, c in clients.items()}
    flag = (
        override_settings(DSX_DATASET_STARS_ENABLED=False)
        if explicit_off
        else nullcontext()
    )
    with flag:
        assert settings.DSX_DATASET_STARS_ENABLED is False
        for method in ("POST", "DELETE"):
            for body in ("{}", '{"user_id":"other"}', "{"):
                response = client.generic(
                    method,
                    star_url(owner["dataset"]),
                    body,
                    content_type="application/json",
                )
                assert response.status_code == 404, (
                    role,
                    method,
                    body,
                    response.content,
                )
                assert owner["dataset"].name.encode() not in response.content
                assert _stored_stars() == before
        assert {name: listed(c) for name, c in clients.items()} == listing_before


@pytest.mark.unit
@pytest.mark.parametrize(
    "flag",
    [None, False, 0, 1, "true", "false"],
    ids=["none", "false", "zero", "one", "string-true", "string-false"],
)
def test_off_gate_precedes_initial_authentication_and_validation(
    settings, monkeypatch, flag
):
    from model_hub.views.dataset_stars import DatasetStarView

    settings.DSX_DATASET_STARS_ENABLED = flag

    def unexpected_initial(*args, **kwargs):
        raise AssertionError(
            "OFF must not enter authentication, permissions or negotiation"
        )

    monkeypatch.setattr(APIView, "initial", unexpected_initial)
    dataset_id = uuid.uuid4()
    request = APIRequestFactory().post(
        f"/model-hub/datasets/{dataset_id}/star/",
        "{",
        content_type="application/json",
        HTTP_ACCEPT="application/unsupported",
        HTTP_AUTHORIZATION="Bearer not-a-valid-token",
    )
    response = DatasetStarView.as_view()(request, dataset_id=dataset_id)
    assert response.status_code == 404


@pytest.mark.unit
@pytest.mark.parametrize(
    "method", ["POST", "DELETE", "GET", "PUT", "PATCH", "HEAD", "OPTIONS"]
)
def test_absent_setting_refuses_every_method(settings, method):
    from model_hub.views.dataset_stars import DatasetStarView

    del settings.DSX_DATASET_STARS_ENABLED
    dataset_id = uuid.uuid4()
    request = APIRequestFactory().generic(
        method, f"/model-hub/datasets/{dataset_id}/star/"
    )
    response = DatasetStarView.as_view()(request, dataset_id=dataset_id)
    assert response.status_code == 404


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["owner", "member", "viewer"])
def test_switch_is_read_per_request_without_widening_visibility(real_actors, role):
    actor, foreign = real_actors[role], real_actors["foreign"]
    client = _login_client(actor)
    for enabled in (False, True, False, True):
        with override_settings(DSX_DATASET_STARS_ENABLED=enabled):
            before = _stored_stars()
            response = client.post(star_url(actor["dataset"]), {}, format="json")
            assert response.status_code == (200 if enabled else 404), response.content
            if enabled:
                assert (
                    stars().filter(dataset=actor["dataset"], user=actor["user"]).count()
                    == 1
                )
                for method in (client.post, client.delete):
                    denied = method(star_url(foreign["dataset"]))
                    assert denied.status_code == 404, denied.content
                assert not stars().filter(dataset=foreign["dataset"]).exists()
            else:
                assert _stored_stars() == before


@pytest.mark.django_db
@pytest.mark.parametrize(
    "invalid_token", [False, True], ids=["anonymous", "invalid-token"]
)
def test_on_still_requires_authentication(real_actors, invalid_token):
    client = APIClient()
    if invalid_token:
        client.credentials(HTTP_AUTHORIZATION="Bearer not-a-valid-token")
    with override_settings(DSX_DATASET_STARS_ENABLED=True):
        response = client.post(
            star_url(real_actors["owner"]["dataset"]), {}, format="json"
        )
    assert response.status_code == 401, response.content
    assert not stars().exists()
