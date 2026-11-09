"""Schema saves and complete write batches retain tenant and persistence boundaries."""

import copy

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.authentication import generate_encrypted_message
from accounts.models.auth_token import AuthToken, AuthTokenType
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.tests.test_dsx_l1_dataset_list_tenancy import _tenant
from tfc.middleware.workspace_context import clear_workspace_context


def _client(tenant):
    # Exercise real authentication: it binds user, organization and workspace
    # for serializer validation, unlike force_authenticate's request-only fixture.
    token = AuthToken.objects.create(
        user=tenant["user"],
        auth_type=AuthTokenType.ACCESS.value,
        is_active=True,
        last_used_at=timezone.now(),
    )
    bearer = generate_encrypted_message(
        {"user_id": str(tenant["user"].id), "id": str(token.id)}
    )
    client = APIClient()
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {bearer}",
        HTTP_X_ORGANIZATION_ID=str(tenant["org"].id),
        HTTP_X_WORKSPACE_ID=str(tenant["workspace"].id),
    )
    return client


@pytest.fixture
def scopes(db):
    a, b = _tenant("schema-a"), _tenant("schema-b")
    ca, cb = _client(a), _client(b)
    yield a, b, ca, cb
    clear_workspace_context()


def path(tenant):
    return f"/model-hub/datasets/{tenant['dataset'].id}/schema/"


def declared(mode="reject"):
    return {
        "declared": {
            "type": "object",
            "properties": {"question": {"type": "string", "enum": ["allowed"]}},
            "required": ["question"],
            "additionalProperties": False,
        },
        "enforce": mode,
        "preset": "inferred",
    }


def install(tenant, mode="reject"):
    dataset = tenant["dataset"]
    dataset.dataset_config = {"schema": declared(mode), "dismiss_banner": True}
    dataset.save(update_fields=["dataset_config"])


def snapshot(tenant):
    dataset = tenant["dataset"]
    dataset.refresh_from_db()
    return {
        "config": dataset.dataset_config,
        "columns": list(
            Column.no_workspace_objects.filter(dataset=dataset).order_by("id").values()
        ),
        "rows": list(
            Row.no_workspace_objects.filter(dataset=dataset).order_by("id").values()
        ),
        "cells": list(
            Cell.no_workspace_objects.filter(dataset=dataset).order_by("id").values()
        ),
    }


def edit(client, tenant, value, row=None, column=None):
    return client.post(
        f"/model-hub/develops/{tenant['dataset'].id}/update_cell_value/",
        {
            "row_id": str((row or tenant["rows"][0]).id),
            "column_id": str((column or tenant["column"]).id),
            "new_value": value,
        },
        format="json",
    )


def add(client, tenant, values):
    return client.post(
        f"/model-hub/develops/{tenant['dataset'].id}/add_rows/",
        {
            "rows": [
                {"cells": [{"column_name": "question", "value": value}]}
                for value in values
            ]
        },
        format="json",
    )


def upload(client, tenant, csv):
    return client.post(
        "/model-hub/develops/add_rows_from_file/",
        {
            "dataset_id": str(tenant["dataset"].id),
            "file": SimpleUploadedFile(
                "schema.csv", csv.encode(), content_type="text/csv"
            ),
        },
        format="multipart",
    )


@pytest.mark.django_db
class TestDatasetSchema:
    def test_save_read_and_table_round_trip_preserve_other_config(self, scopes):
        a, _, ca, _ = scopes
        install(a, "off")
        response = ca.put(path(a), declared(), format="json")
        assert response.status_code == 200, response.content
        assert ca.get(path(a)).json()["result"] == declared()
        a["dataset"].refresh_from_db()
        assert a["dataset"].dataset_config == {
            "schema": declared(),
            "dismiss_banner": True,
        }
        table = ca.get(f"/model-hub/develops/{a['dataset'].id}/get-dataset-table/")
        assert table.json()["result"]["dataset_config"]["schema"] == declared()

    def test_invalid_cell_edit_is_refused_without_changing_any_cell(self, scopes):
        a, _, ca, _ = scopes
        install(a)
        before = snapshot(a)
        response = edit(ca, a, "invalid")
        assert response.status_code == 400, response.content
        assert snapshot(a) == before
        assert edit(ca, a, "allowed").status_code == 200
        a["cells"][0].refresh_from_db()
        assert a["cells"][0].value == "allowed"

    @pytest.mark.parametrize(
        "values", [["allowed", "invalid"], ["allowed", ""], ["allowed", None]]
    )
    def test_invalid_added_batch_has_no_partial_rows_or_cells(self, scopes, values):
        a, _, ca, _ = scopes
        install(a)
        before = snapshot(a)
        response = add(ca, a, values)
        assert response.status_code == 400, response.content
        assert snapshot(a) == before

    def test_invalid_import_has_no_partial_rows_columns_or_config(self, scopes):
        a, _, ca, _ = scopes
        install(a)
        before = snapshot(a)
        response = upload(ca, a, "question,new_column\nallowed,a\ninvalid,b\n")
        assert response.status_code == 400, response.content
        assert snapshot(a) == before
        response = upload(ca, a, "question\nallowed\ninvalid\n")
        assert response.status_code == 400, response.content
        assert snapshot(a) == before

    def test_valid_added_rows_and_import_are_persisted(self, scopes):
        a, _, ca, _ = scopes
        install(a)
        response = add(ca, a, ["allowed", "allowed"])
        assert response.status_code == 200, response.content
        response = upload(ca, a, "question\nallowed\n")
        assert response.status_code == 200, response.content
        assert (
            Cell.no_workspace_objects.filter(
                dataset=a["dataset"], value="allowed"
            ).count()
            == 3
        )

    @pytest.mark.parametrize("mode", ["off", "check"])
    def test_non_reject_modes_preserve_existing_write_behaviour(self, scopes, mode):
        a, _, ca, _ = scopes
        install(a, mode)
        assert edit(ca, a, "invalid").status_code == 200
        assert add(ca, a, ["invalid"]).status_code == 200
        assert upload(ca, a, "question\ninvalid\n").status_code == 200

    def test_foreign_tenant_cannot_read_save_or_write(self, scopes):
        a, b, ca, cb = scopes
        install(a)
        before = snapshot(a)
        assert cb.get(path(a)).status_code in (403, 404)
        assert cb.put(path(a), declared("off"), format="json").status_code in (403, 404)
        assert edit(cb, a, "allowed").status_code in (400, 403, 404)
        # The schema check stays inert for an id AddDataRowsView cannot resolve;
        # the writer's own refusal applies (its catch-all currently answers 500).
        assert add(cb, a, ["allowed"]).status_code in (404, 500)
        assert upload(cb, a, "question\nallowed\n").status_code in (403, 404)
        assert snapshot(a) == before
        assert cb.put(path(b), declared(), format="json").status_code == 200
        assert ca.get(path(a)).status_code == 200

    def test_writer_resolved_dataset_without_schema_keeps_existing_writes(self, scopes):
        a, _, ca, _ = scopes
        # Annotation-queue exports and pre-workspace datasets carry no workspace;
        # the default workspace's add-row and import writers resolve them.
        Dataset.no_workspace_objects.filter(id=a["dataset"].id).update(workspace=None)
        response = add(ca, a, ["free text"])
        assert response.status_code == 200, response.content
        response = upload(ca, a, "question\nimported text\n")
        assert response.status_code == 200, response.content
        stored = set(
            Cell.no_workspace_objects.filter(dataset=a["dataset"]).values_list(
                "value", flat=True
            )
        )
        assert {"free text", "imported text"} <= stored
        # Declarations are stored only in the caller's exact workspace, so such a
        # dataset never reaches the strict checks that follow a rejecting schema.
        assert ca.put(path(a), declared(), format="json").status_code == 404
        a["dataset"].refresh_from_db()
        assert "schema" not in (a["dataset"].dataset_config or {})

    @pytest.mark.parametrize(
        "change",
        [
            {"$ref": "https://example.invalid/schema"},
            {"type": "object", "patternProperties": {".*": {"type": "string"}}},
            {"type": "object", "properties": {"question": {"type": "invalid"}}},
            {"type": "object", "required": "question"},
        ],
    )
    def test_unsupported_or_invalid_schemas_are_rejected_without_save(
        self, scopes, change
    ):
        a, _, ca, _ = scopes
        install(a)
        before = snapshot(a)
        data = copy.deepcopy(declared())
        data["declared"] = change
        response = ca.put(path(a), data, format="json")
        assert response.status_code == 400, response.content
        assert snapshot(a) == before

    def test_import_validates_missing_column_as_the_writer_stores_it(self, scopes):
        a, _, ca, _ = scopes
        data = declared()
        data["declared"] = {
            "type": "object",
            "properties": {"question": {"type": "null"}},
        }
        assert ca.put(path(a), data, format="json").status_code == 200
        before = snapshot(a)
        response = upload(ca, a, "another_column\nx\n")
        assert response.status_code == 400, response.content
        assert snapshot(a) == before

    def test_nested_json_constraints_reject_before_cell_persistence(self, scopes):
        a, _, ca, _ = scopes
        Column.no_workspace_objects.filter(id=a["column"].id).update(data_type="json")
        data = declared()
        data["declared"]["properties"]["question"] = {
            "type": "object",
            "required": ["items"],
            "additionalProperties": False,
            "properties": {
                "items": {"type": "array", "minItems": 1, "items": {"type": "boolean"}}
            },
        }
        assert ca.put(path(a), data, format="json").status_code == 200
        before = snapshot(a)
        for value in (
            '{"items":[]}',
            '{"items":["true"]}',
            '{"items":[NaN]}',
            '{"items":' + "[" * 1500 + "true" + "]" * 1500 + "}",
        ):
            response = edit(ca, a, value)
            assert response.status_code == 400, response.content
            assert snapshot(a) == before
        response = edit(ca, a, '{"items":[true,false]}')
        assert response.status_code == 200, response.content

    def test_undeclared_schema_matches_the_generated_object_contract(self, scopes):
        a, _, ca, _ = scopes
        response = ca.get(path(a))
        assert response.status_code == 200, response.content
        assert response.json()["result"] == {
            "declared": {},
            "enforce": "off",
            "preset": "",
        }
