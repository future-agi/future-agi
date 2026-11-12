"""GET datasets/{dataset_id}/rows/{row_id}/ reads one row, scoped and read-only.

The read returns get-row-data's values for the same row: its id and order,
``row_meta`` and, under ``cells``, every column entry get-row-data returns.
Another organization, another workspace, a deleted row or dataset, and a row
of another dataset are all 404 and never leak the row's values.
"""

import json
import uuid
from pathlib import Path

import pytest
from django.urls import include, path
from drf_yasg import openapi
from drf_yasg.codecs import OpenAPICodecJson
from drf_yasg.generators import OpenAPISchemaGenerator
from rest_framework import status

from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from model_hub.models.choices import (
    DatasetSourceChoices,
    DataTypeChoices,
    SourceChoices,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.tests.test_dsx_l1_dataset_list_tenancy import _client, _tenant
from tfc.constants.levels import Level
from tfc.middleware.workspace_context import clear_workspace_context

TEMPLATE = "/model-hub/datasets/{dataset_id}/rows/{row_id}/"


@pytest.fixture(autouse=True)
def row_read_on(settings):
    """This module is the unit's ON arm: the route exists only while the
    server switch is on. OFF (the default) is pinned in
    test_dataset_row_source_gate.py."""

    settings.DSX_DATASET_ROW_SOURCE_ENABLED = True


def _url(dataset_id, row_id):
    return TEMPLATE.format(dataset_id=dataset_id, row_id=row_id)


def _get_row_data(client, dataset_id, row_id):
    response = client.post(
        f"/model-hub/develops/{dataset_id}/get-row-data/",
        {"row_id": str(row_id)},
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK, response.content
    return response.json()["result"]["current"]


def _as_row_read(current):
    """get-row-data's ``current`` in the row read's shape."""

    return {
        "row_id": current["row_id"],
        "order": current["order"],
        "row_meta": current.get("row_meta", {}),
        "cells": {
            key: value
            for key, value in current.items()
            if key not in {"row_id", "order", "row_meta"}
        },
    }


@pytest.fixture
def tenants(db):
    a = _tenant("row-read-a")
    b = _tenant("row-read-b")
    clients = {"a": _client(a), "b": _client(b)}
    yield a, b, clients
    for client in clients.values():
        client.stop_workspace_injection()
    clear_workspace_context()


def _second_workspace(tenant, label):
    """A non-default workspace in the tenant's organization, with its own row."""

    workspace = Workspace.objects.create(
        name=f"Row read {label}",
        organization=tenant["org"],
        is_default=False,
        is_active=True,
        created_by=tenant["user"],
    )
    WorkspaceMembership.no_workspace_objects.get_or_create(
        user=tenant["user"],
        workspace=workspace,
        defaults={
            "role": "Workspace Owner",
            "level": Level.OWNER,
            "is_active": True,
            "organization_membership": OrganizationMembership.no_workspace_objects.get(
                user=tenant["user"], organization=tenant["org"]
            ),
        },
    )
    dataset = Dataset.no_workspace_objects.create(
        name=f"row-read-{label}-{uuid.uuid4().hex[:6]}",
        organization=tenant["org"],
        workspace=workspace,
        user=tenant["user"],
        source=DatasetSourceChoices.BUILD.value,
    )
    column = Column.no_workspace_objects.create(
        name="question",
        data_type=DataTypeChoices.TEXT.value,
        dataset=dataset,
        source=SourceChoices.OTHERS.value,
    )
    row = Row.no_workspace_objects.create(dataset=dataset, order=0)
    Cell.no_workspace_objects.create(
        dataset=dataset, row=row, column=column, value=f"{label}-other-workspace"
    )
    return workspace, dataset, row


@pytest.mark.django_db
class TestDatasetRowRead:
    def test_owner_reads_the_get_row_data_values_of_the_row(self, tenants):
        a, _, clients = tenants
        row = a["rows"][1]
        meta = {"metadata": {"response_time": 120, "usage": {"total_tokens": 7}}}
        Cell.no_workspace_objects.filter(row=row).update(value_infos=meta)

        response = clients["a"].get(_url(a["dataset"].id, row.id))

        assert response.status_code == status.HTTP_200_OK, response.content
        body = response.json()
        assert body["status"] is True
        result = body["result"]
        assert result == _as_row_read(
            _get_row_data(clients["a"], a["dataset"].id, row.id)
        )
        assert result["row_id"] == str(row.id)
        assert result["order"] == row.order
        assert result["row_meta"] == {}
        assert list(result["cells"]) == [str(a["column"].id)]
        cell = result["cells"][str(a["column"].id)]
        assert cell["cell_value"] == "row-read-a-value-1"
        assert cell["cell_id"] == str(a["cells"][1].id)
        assert cell["metadata"]["response_time_ms"] == 120
        assert cell["metadata"]["token_count"] == 7
        # Only this row's values, never a neighbour's.
        assert "row-read-a-value-0" not in json.dumps(result)

    @pytest.mark.parametrize(
        "stored",
        [
            '{"metadata":"not-json"}',
            {"metadata": "not-json"},
            "not-json",
        ],
        ids=["json-string", "object", "plain-string"],
    )
    def test_value_infos_that_do_not_parse_are_returned_as_stored(
        self, tenants, stored
    ):
        # get-row-data returns a cell's value_infos exactly as stored when it,
        # or the metadata inside it, does not parse; the row read does too.
        a, _, clients = tenants
        row = a["rows"][1]
        Cell.no_workspace_objects.filter(row=row).update(value_infos=stored)

        response = clients["a"].get(_url(a["dataset"].id, row.id))

        assert response.status_code == status.HTTP_200_OK, response.content
        result = response.json()["result"]
        assert result == _as_row_read(
            _get_row_data(clients["a"], a["dataset"].id, row.id)
        )
        cell = result["cells"][str(a["column"].id)]
        assert cell["value_infos"] == stored
        assert cell["metadata"]["response_time_ms"] is None
        assert cell["metadata"]["token_count"] is None

    def test_a_deleted_column_is_left_out_as_get_row_data_does(self, tenants):
        a, _, clients = tenants
        row = a["rows"][0]
        hidden = Column.no_workspace_objects.create(
            name="retired",
            data_type=DataTypeChoices.TEXT.value,
            dataset=a["dataset"],
            source=SourceChoices.OTHERS.value,
            deleted=True,
        )
        Cell.no_workspace_objects.create(
            dataset=a["dataset"], row=row, column=hidden, value="retired value"
        )

        result = clients["a"].get(_url(a["dataset"].id, row.id)).json()["result"]

        assert str(hidden.id) not in result["cells"]
        assert result == _as_row_read(
            _get_row_data(clients["a"], a["dataset"].id, row.id)
        )

    def test_the_read_never_writes(self, tenants):
        a, _, clients = tenants
        row = a["rows"][0]
        url = _url(a["dataset"].id, row.id)
        for method in ("post", "put", "patch", "delete"):
            response = getattr(clients["a"], method)(url, {}, format="json")
            assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED, method
        row.refresh_from_db()
        assert row.deleted is False
        assert Cell.no_workspace_objects.get(id=a["cells"][0].id).value == (
            "row-read-a-value-0"
        )

    def test_another_organization_gets_404_and_no_values(self, tenants):
        a, b, clients = tenants
        row = a["rows"][0]

        foreign = clients["b"].get(_url(a["dataset"].id, row.id))
        assert foreign.status_code == status.HTTP_404_NOT_FOUND
        assert "row-read-a-value" not in foreign.content.decode()

        # Its own dataset id cannot reach another organization's row either.
        mixed = clients["b"].get(_url(b["dataset"].id, row.id))
        assert mixed.status_code == status.HTTP_404_NOT_FOUND
        assert "row-read-a-value" not in mixed.content.decode()

        # Positive control: the same request as the owner succeeds.
        assert clients["a"].get(_url(a["dataset"].id, row.id)).status_code == 200

    def test_another_workspace_gets_404(self, tenants):
        a, _, clients = tenants
        workspace, dataset, other_row = _second_workspace(a, "a")

        # The default workspace cannot read the other workspace's row ...
        hidden = clients["a"].get(_url(dataset.id, other_row.id))
        assert hidden.status_code == status.HTTP_404_NOT_FOUND
        assert "a-other-workspace" not in hidden.content.decode()

        # ... and the other workspace cannot read the default workspace's row.
        clients["a"].set_workspace(workspace)
        try:
            response = clients["a"].get(_url(a["dataset"].id, a["rows"][0].id))
            assert response.status_code == status.HTTP_404_NOT_FOUND
            assert "row-read-a-value" not in response.content.decode()
            # Positive control: the other workspace reads its own row.
            own = clients["a"].get(_url(dataset.id, other_row.id))
            assert own.status_code == status.HTTP_200_OK, own.content
            column = Column.no_workspace_objects.get(dataset=dataset)
            assert own.json()["result"]["cells"][str(column.id)]["cell_value"] == (
                "a-other-workspace"
            )
        finally:
            clients["a"].set_workspace(a["workspace"])

    def test_deleted_rows_and_datasets_and_other_datasets_rows_are_404(self, tenants):
        a, _, clients = tenants
        row = a["rows"][0]
        assert clients["a"].get(_url(a["dataset"].id, row.id)).status_code == 200

        other = Dataset.no_workspace_objects.create(
            name=f"row-read-other-{uuid.uuid4().hex[:6]}",
            organization=a["org"],
            workspace=a["workspace"],
            user=a["user"],
            source=DatasetSourceChoices.BUILD.value,
        )
        assert clients["a"].get(_url(other.id, row.id)).status_code == 404

        Row.no_workspace_objects.filter(id=row.id).update(deleted=True)
        assert clients["a"].get(_url(a["dataset"].id, row.id)).status_code == 404

        live = a["rows"][1]
        assert clients["a"].get(_url(a["dataset"].id, live.id)).status_code == 200
        Dataset.no_workspace_objects.filter(id=a["dataset"].id).update(deleted=True)
        gone = clients["a"].get(_url(a["dataset"].id, live.id))
        assert gone.status_code == 404
        assert "row-read-a-value" not in gone.content.decode()

    def test_unknown_ids_are_404(self, tenants):
        a, _, clients = tenants
        assert clients["a"].get(_url(uuid.uuid4(), uuid.uuid4())).status_code == 404
        assert clients["a"].get(_url(a["dataset"].id, uuid.uuid4())).status_code == 404


def _repo_root():
    # model_hub/tests/<file>.py -> parents[3] == monorepo root (holds api_contracts/)
    return Path(__file__).resolve().parents[3]


def _on_schema():
    """The route's schema as the repository's generator emits it while ON."""

    from model_hub import urls

    patterns = [
        p for p in urls.urlpatterns if getattr(p, "name", None) == "dataset-row-read"
    ]
    assert len(patterns) == 1
    generator = OpenAPISchemaGenerator(
        openapi.Info(title="Row read", default_version="v1"),
        patterns=[path("model-hub/", include(patterns))],
    )
    return json.loads(
        OpenAPICodecJson(validators=[]).encode(
            generator.get_schema(request=None, public=True)
        )
    )


def test_the_checked_in_off_contract_omits_the_route():
    # The checked-in contract is generated with the switch unset (OFF).
    swagger = json.loads(
        (_repo_root() / "api_contracts" / "openapi" / "swagger.json").read_text()
    )
    assert TEMPLATE not in swagger["paths"]
    assert "DatasetRowReadResponse" not in swagger["definitions"]


def test_the_public_contract_documents_the_get_operation():
    swagger = _on_schema()
    suffix = "/datasets/{dataset_id}/rows/{row_id}/"
    found = [item for key, item in swagger["paths"].items() if key.endswith(suffix)]
    assert len(found) == 1, swagger["paths"]
    operations = found[0]
    assert set(operations) - {"parameters"} == {"get"}
    get = operations["get"]
    assert get["responses"]["200"]["schema"]["$ref"] == (
        "#/definitions/DatasetRowReadResponse"
    )
    assert "404" in get["responses"]
    definitions = swagger["definitions"]

    def resolve(schema):
        return definitions[schema["$ref"].rsplit("/", 1)[-1]]

    result = resolve(definitions["DatasetRowReadResponse"]["properties"]["result"])
    assert set(result["required"]) == {"row_id", "order", "row_meta", "cells"}
    # Every column's value: a map from column id to one declared cell entry.
    cells = result["properties"]["cells"]
    assert cells["type"] == "object"
    cell = resolve(cells["additionalProperties"])
    assert set(cell["required"]) == {
        "cell_id",
        "cell_value",
        "metadata",
        "status",
        "value_infos",
        "feedback_info",
    }
    assert cell["properties"]["cell_value"]["x-nullable"] is True
    assert cell["properties"]["value_infos"]["x-json-value"] is True
    assert cell["properties"]["feedback_info"]["x-json-value"] is True
    metadata = resolve(cell["properties"]["metadata"])
    assert set(metadata["properties"]) == {
        "response_time_ms",
        "token_count",
        "annotation",
    }
    # The row's import source.
    source = resolve(resolve(result["properties"]["row_meta"])["properties"]["source"])
    assert set(source["required"]) == {"type", "label"}
    assert {"trace", "span", "session"} <= set(source["properties"]["type"]["enum"])
    assert set(source["properties"]) == {
        "type",
        "label",
        "project_id",
        "trace_id",
        "span_id",
        "session_id",
        "file_name",
        "expired",
    }
