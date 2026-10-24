"""What a custom property schema's rules do.

Required and Allowed Values work through POST /agentcc/custom-properties/
validate/ and stay storable. A Default value has no reader anywhere (the
gateway never fills it, ingestion never fills it, the logs API never shows
it), so a new non-default Default is refused with 400
property_rule_not_enforced. A stored Default stays readable, and an edit that
leaves it as it is (or clears it) saves.
Name, type, description and the request-log tag filters stay.

No test here reaches a network.
"""

import pytest
from rest_framework import status

from agentcc.models import AgentccRequestLog
from agentcc.models.custom_property import AgentccCustomPropertySchema

URL = "/agentcc/custom-properties/"
VALIDATE = "/agentcc/custom-properties/validate/"
METADATA_VALUES = "/agentcc/request-logs/metadata-values/"


def _create(client, **fields):
    body = {"name": "customer_tier", "property_type": "enum", **fields}
    return client.post(URL, body, format="json")


def _result(response):
    assert response.status_code == status.HTTP_200_OK, response.content
    return response.json()["result"]


def _refused_default(response):
    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
    body = response.json()
    assert body["code"] == "property_rule_not_enforced", body
    assert "default_value" in response.content.decode()


@pytest.fixture
def legacy_schema(organization):
    """A stored schema with a Default."""
    return AgentccCustomPropertySchema.no_workspace_objects.create(
        organization=organization,
        name="region",
        property_type="enum",
        required=True,
        allowed_values=["us", "eu"],
        default_value="us",
    )


@pytest.mark.django_db
class TestDefaultRefused:
    def test_create_with_a_default_is_refused(self, auth_client, organization):
        _refused_default(
            _create(
                auth_client,
                required=True,
                allowed_values=["gold", "silver"],
                default_value="silver",
            )
        )
        assert not AgentccCustomPropertySchema.no_workspace_objects.filter(
            organization=organization, name="customer_tier"
        ).exists()

    @pytest.mark.parametrize(
        "property_type,default",
        [("string", "x"), ("number", 0), ("boolean", False)],
    )
    def test_create_refuses_every_type_of_default(
        self, auth_client, property_type, default
    ):
        _refused_default(
            _create(
                auth_client,
                name=f"p_{property_type}",
                property_type=property_type,
                default_value=default,
            )
        )

    def test_create_with_no_default_saves(self, auth_client):
        created = _result(
            _create(auth_client, required=True, allowed_values=["gold", "silver"])
        )
        assert created["default_value"] is None
        created2 = _result(
            _create(
                auth_client,
                name="plan",
                property_type="string",
                default_value=None,
            )
        )
        assert created2["default_value"] is None

    def test_changing_a_stored_default_is_refused(self, auth_client, legacy_schema):
        _refused_default(
            auth_client.patch(
                f"{URL}{legacy_schema.id}/", {"default_value": "eu"}, format="json"
            )
        )
        legacy_schema.refresh_from_db()
        assert legacy_schema.default_value == "us"

    def test_setting_a_default_on_a_schema_without_one_is_refused(
        self, auth_client, organization
    ):
        schema = AgentccCustomPropertySchema.no_workspace_objects.create(
            organization=organization, name="plan", property_type="string"
        )
        _refused_default(
            auth_client.patch(
                f"{URL}{schema.id}/", {"default_value": "pro"}, format="json"
            )
        )


@pytest.mark.django_db
class TestStoredRowsUnchanged:
    def test_a_stored_default_stays_readable(self, auth_client, legacy_schema):
        listed = _result(auth_client.get(URL))
        (row,) = [r for r in listed if r["name"] == "region"]
        assert row["default_value"] == "us"
        detail = _result(auth_client.get(f"{URL}{legacy_schema.id}/"))
        assert detail["default_value"] == "us"

    def test_an_edit_that_keeps_the_stored_default_saves(
        self, auth_client, legacy_schema
    ):
        updated = _result(
            auth_client.patch(
                f"{URL}{legacy_schema.id}/",
                {
                    "description": "Customer region",
                    "required": False,
                    "allowed_values": ["us", "eu", "apac"],
                    "default_value": "us",
                },
                format="json",
            )
        )
        assert updated["description"] == "Customer region"
        assert updated["allowed_values"] == ["us", "eu", "apac"]
        assert updated["default_value"] == "us"

    def test_an_edit_that_omits_the_default_keeps_it(self, auth_client, legacy_schema):
        _result(
            auth_client.patch(
                f"{URL}{legacy_schema.id}/", {"description": "d"}, format="json"
            )
        )
        legacy_schema.refresh_from_db()
        assert legacy_schema.default_value == "us"

    def test_clearing_a_stored_default_saves(self, auth_client, legacy_schema):
        updated = _result(
            auth_client.patch(
                f"{URL}{legacy_schema.id}/", {"default_value": None}, format="json"
            )
        )
        assert updated["default_value"] is None


@pytest.mark.django_db
class TestRequiredAndAllowedValuesStillWork:
    """Both halves: the rules are stored and validate/ applies them."""

    def test_required_and_allowed_values_are_stored(self, auth_client, organization):
        created = _result(
            _create(auth_client, required=True, allowed_values=["gold", "silver"])
        )
        assert created["required"] is True
        assert created["allowed_values"] == ["gold", "silver"]

    def test_validate_applies_allowed_values(self, auth_client):
        _result(_create(auth_client, required=True, allowed_values=["gold", "silver"]))
        refused = _result(
            auth_client.post(
                VALIDATE, {"properties": {"customer_tier": "platinum"}}, format="json"
            )
        )
        assert refused["valid"] is False
        assert any("customer_tier" in e for e in refused["errors"])
        accepted = _result(
            auth_client.post(
                VALIDATE, {"properties": {"customer_tier": "gold"}}, format="json"
            )
        )
        assert accepted == {"valid": True, "errors": []}

    def test_validate_applies_required(self, auth_client):
        _result(_create(auth_client, required=True, allowed_values=["gold", "silver"]))
        missing = _result(auth_client.post(VALIDATE, {"properties": {}}, format="json"))
        assert missing["valid"] is False
        assert missing["errors"] == ["Missing required property: customer_tier"]

    def test_validate_does_not_fill_a_stored_default(self, auth_client, legacy_schema):
        # A stored Default is not applied anywhere, validate/ included: a
        # missing required property is reported, not filled.
        missing = _result(auth_client.post(VALIDATE, {"properties": {}}, format="json"))
        assert missing["errors"] == ["Missing required property: region"]


@pytest.mark.django_db
def test_name_type_description_and_tag_filters_stay(auth_client, organization):
    created = _result(
        auth_client.post(
            URL,
            {
                "name": "team",
                "property_type": "string",
                "description": "Owning team",
            },
            format="json",
        )
    )
    assert (created["name"], created["property_type"], created["description"]) == (
        "team",
        "string",
        "Owning team",
    )
    AgentccRequestLog.objects.create(
        organization=organization,
        request_id="req-team",
        model="gpt-4o",
        provider="openai",
        metadata={"team": "search"},
    )
    values = _result(auth_client.get(METADATA_VALUES))
    assert "team:search" in str(values), values
