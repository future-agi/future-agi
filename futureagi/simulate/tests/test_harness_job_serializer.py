from __future__ import annotations

import json
from pathlib import Path

import pytest

from simulate.serializers.harness_job import (
    HarnessJobCreateSerializer,
    HarnessJobInfoSerializer,
    HarnessPreflightSerializer,
)
from simulate.tests.test_hosted_harness_parallelism import _create_data
from tfc.utils.serializer_fields import JsonValueField

RESERVED_MESSAGE = "metadata contains reserved execution-control keys"


def _errors(metadata, serializer_cls=HarnessJobCreateSerializer):
    data = _create_data(parallelism=1)
    data["metadata"] = metadata
    serializer = serializer_cls(data=data)
    serializer.is_valid()
    return serializer.errors


def _swagger_definitions():
    path = (
        Path(__file__).resolve().parents[3]
        / "api_contracts"
        / "openapi"
        / "swagger.json"
    )
    with path.open() as schema_file:
        return json.load(schema_file)["definitions"]


def test_observability_refusal_is_a_reserved_metadata_key():
    errors = _errors({"observability_refusal": {"code": "bound", "details": {}}})

    assert [str(message) for message in errors["metadata"]] == [RESERVED_MESSAGE]


def test_the_observability_choice_is_not_a_reserved_metadata_key():
    assert "metadata" not in _errors({"observability_enabled": True})
    assert "metadata" not in _errors({})


@pytest.mark.parametrize(
    "serializer_cls", [HarnessJobCreateSerializer, HarnessPreflightSerializer]
)
def test_the_create_and_preflight_bodies_accept_the_observability_choice(
    serializer_cls,
):
    data = _create_data(parallelism=1)
    data["metadata"] = {"observability_enabled": True, "name": "x"}
    serializer = serializer_cls(data=data)

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["metadata"]["observability_enabled"] is True
    assert serializer.validated_data["metadata"]["name"] == "x"


def test_a_null_metadata_value_is_still_accepted():
    assert "metadata" not in _errors({"name": None})


@pytest.mark.parametrize(
    "serializer_cls",
    [
        HarnessJobCreateSerializer,
        HarnessPreflightSerializer,
        HarnessJobInfoSerializer,
    ],
)
def test_metadata_values_are_open_json_in_the_serializer(serializer_cls):
    assert isinstance(serializer_cls().fields["metadata"].child, JsonValueField)


@pytest.mark.parametrize(
    "definition", ["HarnessJobCreate", "HarnessPreflight", "HarnessJobInfo"]
)
def test_metadata_values_are_open_json_in_the_generated_contract(definition):
    values = _swagger_definitions()[definition]["properties"]["metadata"][
        "additionalProperties"
    ]

    assert values["x-json-value"] is True


# What the scenario screens read: open objects carry any JSON value, and a change's `affects` is its scenario keys or
# `all`, so the generated types match what the server sends.
OPEN_OBJECTS = [
    ("HarnessEnvironmentWorld", "runtime"),
    ("HarnessEnvironmentScenario", "fixture"),
    ("HarnessEnvironmentScenario", "persona"),
    ("HarnessEnvironmentSettings", "source"),
    ("HarnessEnvironmentSettings", "runtime"),
    ("HarnessEnvironmentSettings", "security"),
    ("HarnessEnvironmentSettings", "artifacts"),
]


@pytest.mark.parametrize(("definition", "field"), OPEN_OBJECTS)
def test_the_scenario_screens_open_objects_carry_any_json_value(definition, field):
    values = _swagger_definitions()[definition]["properties"][field][
        "additionalProperties"
    ]

    assert values["x-json-value"] is True


@pytest.mark.parametrize("field", ["patch", "sources"])
def test_a_review_change_s_patch_and_sources_carry_any_json_value(field):
    items = _swagger_definitions()["HarnessReviewChange"]["properties"][field][
        "items"
    ]

    assert items["additionalProperties"]["x-json-value"] is True


def test_a_review_change_affects_its_scenario_keys_or_all():
    from simulate.serializers.harness_environment import HarnessReviewChangeSerializer

    field = HarnessReviewChangeSerializer().fields["affects"]

    affects = _swagger_definitions()["HarnessReviewChange"]["properties"]["affects"]

    assert affects["x-string-or-array"] is True
    assert field.to_representation(["refund"]) == ["refund"]
    assert field.to_representation("all") == "all"


def test_the_run_warning_declares_the_scenarios_it_names():
    keys = _swagger_definitions()["HarnessRunWarning"]["properties"]["scenario_keys"]

    assert keys["type"] == "array" and keys["items"]["type"] == "string"


def test_a_readiness_fix_can_open_the_agent_s_versions():
    category = _swagger_definitions()["HarnessCapabilityFix"]["properties"]["category"]

    assert "agent_version" in category["enum"]
