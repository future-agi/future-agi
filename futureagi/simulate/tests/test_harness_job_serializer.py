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


def _source_errors(source):
    data = _create_data(parallelism=1)
    data["source"] = {**data["source"], **source}
    serializer = HarnessJobCreateSerializer(data=data)
    serializer.is_valid()
    return serializer.errors.get("source", {})


_GITHUB = {"kind": "github", "repository": "acme/agent", "visibility": "public"}


def test_subfolder_is_refused_until_the_sandbox_reads_it(settings):
    settings.HARNESS_SOURCE_SUBFOLDER_ENABLED = False

    errors = _source_errors({**_GITHUB, "subfolder": "agents/support"})

    assert "not supported yet" in str(errors["subfolder"][0])
    assert "subfolder" not in _source_errors({**_GITHUB, "subfolder": ""})


def test_subfolder_only_on_a_github_source(settings):
    settings.HARNESS_SOURCE_SUBFOLDER_ENABLED = True

    errors = _source_errors({"subfolder": "agents/support"})

    assert "only a github source" in str(errors["subfolder"][0])


@pytest.mark.parametrize(
    "subfolder", ["agents/support", "my..agent", "a-b_c.d/e", "", None]
)
def test_subfolder_accepts_a_relative_folder(settings, subfolder):
    settings.HARNESS_SOURCE_SUBFOLDER_ENABLED = True

    assert "subfolder" not in _source_errors({**_GITHUB, "subfolder": subfolder})


@pytest.mark.parametrize(
    "subfolder", ["/agents", "agents/", "../agents", "agents/../x", ".", "a//b", "a b"]
)
def test_subfolder_refuses_an_unsafe_path(settings, subfolder):
    settings.HARNESS_SOURCE_SUBFOLDER_ENABLED = True

    assert "subfolder" in _source_errors({**_GITHUB, "subfolder": subfolder})
