"""A limit copied from an existing provider is not a new setting."""

from unittest.mock import patch

import pytest
from rest_framework import serializers

from accounts.models.organization import Organization
from agentcc.models import AgentccProviderCredential
from integrations.services.credentials import CredentialManager

pytestmark = [pytest.mark.integration, pytest.mark.api]

CREDENTIALS = "/agentcc/provider-credentials/"
CONFIG = "/agentcc/gateways/default/config/"
UPDATE = "/agentcc/gateways/default/update-provider/"
NAME = "limit-round-trip"
COLUMNS = ("default_timeout_seconds", "max_concurrent", "conn_pool_size")
CASES = [(method, key) for method in ("put", "patch") for key in COLUMNS] + [
    ("gateway", key) for key in ("default_timeout", *COLUMNS)
]


def column(key):
    return "default_timeout_seconds" if key == "default_timeout" else key


def read_key(key):
    return "default_timeout" if column(key) == "default_timeout_seconds" else key


@pytest.fixture
def make_row():
    def create(org, **fields):
        return AgentccProviderCredential.no_workspace_objects.create(
            organization=org,
            provider_name=NAME,
            encrypted_credentials=CredentialManager.encrypt({"api_key": "fixture"}),
            models_list=["original"],
            **fields,
        )

    return create


@pytest.fixture
def pushes():
    with (
        patch(
            "agentcc.views.gateway.AgentccGatewayViewSet._push_current_config",
            return_value=True,
        ) as gateway,
        patch(
            "agentcc.views.provider_credential.AgentccProviderCredentialViewSet._push_config_to_gateway",
            return_value=True,
        ) as direct,
    ):
        yield {"gateway": gateway, "put": direct, "patch": direct, "post": direct}


def snapshot(row):
    # Inspect the seeded row independently of the request's workspace context.
    return AgentccProviderCredential._base_manager.filter(pk=row.pk).values().get()


def read(client, method, row):
    if method == "gateway":
        response = client.get(CONFIG)
        assert response.status_code == 200
        return response.data["result"]["providers"][NAME]
    response = client.get(f"{CREDENTIALS}{row.pk}/")
    assert response.status_code == 200
    return response.data["result"]


def write(client, method, row, source, limits):
    if method == "gateway":
        # Copy only editable fields, not the whole read representation into extra_config.
        config = {read_key(key): source[read_key(key)] for key in COLUMNS}
        # Exercise the requested timeout spelling, not a second implicit echo.
        # Tests for precedence explicitly supply both aliases themselves.
        if "default_timeout_seconds" in limits:
            config.pop("default_timeout")
        config.update(models=[*source["models"], "additional"], **limits)
        return client.post(UPDATE, {"name": NAME, "config": config}, format="json")
    return getattr(client, method)(
        f"{CREDENTIALS}{row.pk}/", {"display_name": "Renamed", **limits}, format="json"
    )


def details(method, key, *, fractional=False):
    if method == "gateway":
        return {"config": [f"{key}: Must be greater than or equal to zero."]}
    message = (
        "A valid integer is required."
        if fractional
        else "Ensure this value is greater than or equal to 0."
    )
    return {key: [message]}


@pytest.mark.parametrize(("method", "key"), CASES)
@pytest.mark.parametrize("as_string", [False, True])
def test_read_then_echo(
    auth_client, organization, make_row, pushes, method, key, as_string
):
    row = make_row(organization, **{column(key): -1})
    source = read(auth_client, method, row)
    value = source[read_key(key) if method == "gateway" else key]
    assert value == -1
    response = write(
        auth_client, method, row, source, {key: str(value) if as_string else value}
    )
    assert response.status_code == 200, response.data.get("details")
    saved = snapshot(row)
    assert saved[column(key)] == -1 and saved["id"] == row.pk
    if method == "gateway":
        assert saved["models_list"] == ["original", "additional"]
    else:
        assert saved["display_name"] == "Renamed"
    pushes[method].assert_called_once()


@pytest.mark.parametrize(("method", "key"), CASES)
@pytest.mark.parametrize(
    ("stored", "submitted"), [(-1, -2), (5, -1), (0, -1), (-1, "-1.5"), (-1, -1.5)]
)
def test_changed_negative(
    auth_client, organization, make_row, pushes, method, key, stored, submitted
):
    row = make_row(organization, **{column(key): stored})
    before = snapshot(row)
    source = read(auth_client, method, row)
    response = write(auth_client, method, row, source, {key: submitted})
    assert response.status_code == 400
    assert response.data["details"] == details(
        method, key, fractional=submitted in (-1.5, "-1.5")
    )
    assert snapshot(row) == before
    pushes[method].assert_not_called()


@pytest.mark.parametrize(
    ("method", "key", "other"),
    [(m, k, COLUMNS[(COLUMNS.index(column(k)) + 1) % len(COLUMNS)]) for m, k in CASES]
    + [(m, "conn_pool_size", "max_concurrent") for m in ("put", "patch", "gateway")],
)
@pytest.mark.parametrize("include_echo", [True, False])
def test_only_same_field_is_grandfathered(
    auth_client, organization, make_row, pushes, method, key, other, include_echo
):
    row = make_row(organization, **{column(key): 100, other: -1})
    before = snapshot(row)
    source = read(auth_client, method, row)
    limits = {key: -1}
    if include_echo:
        limits[other] = source[read_key(other) if method == "gateway" else other]
    if method == "gateway" and not include_echo:
        # Send only the new negative; the other column is not an implicit exception.
        response = auth_client.post(
            UPDATE,
            {"name": NAME, "config": {**limits, "models": source["models"]}},
            format="json",
        )
    else:
        response = write(auth_client, method, row, source, limits)
    assert response.status_code == 400
    assert snapshot(row) == before
    pushes[method].assert_not_called()
    assert response.data["details"] == details(method, key)


@pytest.mark.parametrize(
    ("name", "message"),
    [({}, "This field is required."), ({"name": ""}, "This field may not be blank.")],
)
def test_negative_config_with_invalid_name(
    auth_client, organization, make_row, pushes, name, message
):
    make_row(organization, max_concurrent=-1)
    rows = AgentccProviderCredential._base_manager.filter(
        organization=organization
    ).order_by("pk")
    before = list(rows.values())
    response = auth_client.post(
        UPDATE, {**name, "config": {"max_concurrent": -1}}, format="json"
    )
    assert response.status_code == 400
    assert list(rows.values()) == before
    pushes["gateway"].assert_not_called()
    assert response.data["details"] == {
        "name": [message],
        **details("gateway", "max_concurrent"),
    }


NAME_SAMPLES = [
    None,
    True,
    False,
    [],
    {},
    [[]],
    {"nested": []},
    7,
    1.5,
    0,
    -1,
    "sample",
]


class ReferenceRequest(serializers.Serializer):
    """The original gateway name field, with a valid config to isolate its result."""

    name = serializers.CharField()
    config = serializers.DictField()


@pytest.mark.parametrize("name", NAME_SAMPLES)
@pytest.mark.parametrize("pool", [100, -1])
def test_gateway_name_field_contract(auth_client, organization, pushes, name, pool):
    reference = ReferenceRequest(data={"name": name, "config": {"conn_pool_size": 100}})
    accepted = reference.is_valid()
    rows = AgentccProviderCredential._base_manager.filter(organization=organization)
    before = list(rows.order_by("pk").values())
    response = auth_client.post(
        UPDATE,
        {"name": name, "config": {"conn_pool_size": pool, "api_key": "fixture"}},
        format="json",
    )
    if accepted and pool >= 0:
        assert response.status_code == 200
        saved = rows.get(provider_name=reference.validated_data["name"])
        assert saved.conn_pool_size == pool
        pushes["gateway"].assert_called_once()
    else:
        assert response.status_code == 400
        assert list(rows.order_by("pk").values()) == before
        pushes["gateway"].assert_not_called()
        expected = dict(reference.errors)
        if pool < 0:
            expected.update(details("gateway", "conn_pool_size"))
        assert response.data["details"] == expected


@pytest.mark.parametrize("name", NAME_SAMPLES)
@pytest.mark.parametrize("method", ["post", "put", "patch"])
def test_direct_negative_limit_name_controls(
    auth_client, organization, make_row, pushes, name, method
):
    row = make_row(organization, conn_pool_size=100)
    rows = AgentccProviderCredential._base_manager.filter(
        organization=organization
    ).order_by("pk")
    before = list(rows.values())
    payload = {"provider_name": name, "conn_pool_size": -1}
    expected = details(method, "conn_pool_size")
    if method == "post":
        reference = ReferenceRequest(data={"name": name, "config": {}})
        if not reference.is_valid():
            expected["provider_name"] = reference.errors["name"]
        payload["credentials"] = {"api_key": "fixture"}
        url = CREDENTIALS
    else:
        # Edits identify the provider by its URL, not the name in the body.
        url = f"{CREDENTIALS}{row.pk}/"
    response = getattr(auth_client, method)(url, payload, format="json")
    assert response.status_code == 400
    assert list(rows.values()) == before
    pushes[method].assert_not_called()
    assert response.data["details"] == expected


@pytest.mark.parametrize(
    ("limits", "expected"),
    [
        ({"default_timeout": -1}, -1),
        ({"default_timeout": -1, "default_timeout_seconds": 30}, 30),
        ({"default_timeout_seconds": -1, "default_timeout": 30}, -1),
    ],
)
def test_echo_does_not_change_timeout_precedence(
    auth_client, organization, make_row, pushes, limits, expected
):
    row = make_row(organization, default_timeout_seconds=-1)
    source = read(auth_client, "gateway", row)
    assert source["default_timeout"] == -1
    response = write(auth_client, "gateway", row, source, limits)
    assert response.status_code == 200, response.data.get("details")
    assert snapshot(row)["default_timeout_seconds"] == expected
    pushes["gateway"].assert_called_once()


@pytest.mark.parametrize(
    ("method", "key"),
    [("post", k) for k in COLUMNS]
    + [("gateway", k) for k in ("default_timeout", *COLUMNS)],
)
@pytest.mark.parametrize("hidden", ["none", "other_org", "deleted"])
def test_negative_create_has_no_comparison_row(
    auth_client, organization, make_row, pushes, method, key, hidden
):
    if hidden != "none":
        org = (
            Organization.objects.create(name="Other")
            if hidden == "other_org"
            else organization
        )
        make_row(org, deleted=hidden == "deleted", **{column(key): -1})
    rows = AgentccProviderCredential._base_manager.filter(provider_name=NAME).order_by(
        "pk"
    )
    before = list(rows.values())
    if method == "gateway":
        response = auth_client.post(
            UPDATE,
            {"name": NAME, "config": {"api_key": "fixture", key: -1}},
            format="json",
        )
    else:
        response = auth_client.post(
            CREDENTIALS,
            {"provider_name": NAME, "credentials": {"api_key": "fixture"}, key: -1},
            format="json",
        )
    assert response.status_code == 400
    assert response.data["details"] == details(method, key)
    assert list(rows.values()) == before
    pushes[method].assert_not_called()


@pytest.mark.parametrize(("method", "key"), CASES)
def test_other_organization_cannot_supply_echo(
    auth_client, organization, make_row, pushes, method, key
):
    row = make_row(organization, **{column(key): 5})
    other = make_row(Organization.objects.create(name="Other"), **{column(key): -1})
    before = [snapshot(row), snapshot(other)]
    source = read(auth_client, method, row)
    response = write(auth_client, method, row, source, {key: -1})
    assert response.status_code == 400
    assert response.data["details"] == details(method, key)
    if method != "gateway":
        response = write(auth_client, method, other, source, {key: -1})
        assert response.status_code == 404
    assert [snapshot(row), snapshot(other)] == before
    pushes[method].assert_not_called()
