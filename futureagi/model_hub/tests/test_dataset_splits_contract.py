"""A split saved with a blank description reads back within the published contract."""

# Imported pytest fixtures are injected by name.
# ruff: noqa: F811

import copy
import json
from pathlib import Path

import jsonschema
import pytest

from model_hub.serializers.dataset_splits import (
    DatasetSplitExportResponseSerializer,
    DatasetSplitListResponseSerializer,
    DatasetSplitResponseSerializer,
)
from model_hub.tests.test_dataset_splits import (  # noqa: F401
    assign,
    path,
    result,
    scopes,
)

SPLITS = "/model-hub/datasets/{dataset_id}/splits/"
SPLIT = "/model-hub/datasets/{dataset_id}/splits/{split_id}/"
EXPORT = "/model-hub/datasets/{dataset_id}/splits/export/"


def _resolved(node, definitions):
    """Inline every definition reference; read x-nullable as JSON Schema null."""
    if isinstance(node, list):
        return [_resolved(item, definitions) for item in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        return _resolved(definitions[node["$ref"].rsplit("/", 1)[-1]], definitions)
    out = {key: _resolved(value, definitions) for key, value in node.items()}
    if out.get("x-nullable") and isinstance(out.get("type"), str):
        out["type"] = [out["type"], "null"]
    return out


def published_response(template, method):
    swagger = json.loads(
        (
            Path(__file__).resolve().parents[3] / "api_contracts/openapi/swagger.json"
        ).read_text()
    )
    responses = swagger["paths"][template][method]["responses"]
    schema = (responses.get("200") or responses["201"])["schema"]
    return jsonschema.Draft4Validator(
        _resolved(copy.deepcopy(schema), swagger["definitions"])
    )


def blank_split_reads(client, tenant):
    """Create, list, export and clear a split that never had a description."""
    created = client.post(path(tenant), {"name": "holdout"}, format="json")
    split_id = result(created)["id"]
    result(assign(client, tenant, tenant["rows"][:1], [split_id]))
    described = client.patch(
        path(tenant, f"{split_id}/"), {"description": "Held out"}, format="json"
    )
    assert result(described)["description"] == "Held out"
    cleared = client.patch(
        path(tenant, f"{split_id}/"), {"description": ""}, format="json"
    )
    listed = client.get(path(tenant))
    exported = client.get(path(tenant, "export/"), {"split_id": split_id})
    return split_id, created, cleared, listed, exported


@pytest.mark.django_db
class TestBlankSplitDescriptionContract:
    def test_blank_description_persists_and_reads_back_blank(self, scopes):
        a, b, ca, cb = scopes
        split_id, created, cleared, listed, exported = blank_split_reads(ca, a)
        assert result(created)["description"] == ""
        assert result(cleared)["description"] == ""
        assert [
            (s["id"], s["description"], s["row_count"])
            for s in result(listed)["splits"]
        ] == [(split_id, "", 1)]
        assert result(exported)["split"]["description"] == ""
        # The other organization still cannot read the split.
        foreign = cb.get(path(a, "export/"), {"split_id": split_id})
        assert foreign.status_code in (403, 404), foreign.content
        assert result(cb.get(path(b)))["splits"] == []

    def test_response_serializers_accept_the_blank_description(self, scopes):
        a, _, ca, _ = scopes
        _, created, cleared, listed, exported = blank_split_reads(ca, a)
        for serializer_class, response in (
            (DatasetSplitResponseSerializer, created),
            (DatasetSplitResponseSerializer, cleared),
            (DatasetSplitListResponseSerializer, listed),
            (DatasetSplitExportResponseSerializer, exported),
        ):
            serializer = serializer_class(data=response.json())
            assert serializer.is_valid(), (serializer_class.__name__, serializer.errors)

    def test_published_contract_accepts_the_blank_description(self, scopes):
        a, _, ca, _ = scopes
        _, created, cleared, listed, exported = blank_split_reads(ca, a)
        for template, method, response in (
            (SPLITS, "post", created),
            (SPLIT, "patch", cleared),
            (SPLITS, "get", listed),
            (EXPORT, "get", exported),
        ):
            errors = [
                f"{'/'.join(map(str, error.absolute_path))}: {error.message}"
                for error in published_response(template, method).iter_errors(
                    response.json()
                )
            ]
            assert errors == [], (method, template, errors)
