"""Schema saves and complete write batches retain tenant and persistence boundaries."""

import copy
import uuid

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.authentication import generate_encrypted_message
from accounts.models.auth_token import AuthToken, AuthTokenType
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import WorkspaceMembership
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.tests.test_dsx_l1_dataset_list_tenancy import _tenant
from tfc.constants.levels import Level
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)


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


def _legacy(tenant):
    """Make the tenant's dataset a legacy dataset with no workspace."""
    Dataset.no_workspace_objects.filter(id=tenant["dataset"].id).update(workspace=None)
    tenant["dataset"].refresh_from_db()


def _default_workspace_viewer(tenant):
    """An organization Member whose default-workspace role is viewer."""
    set_workspace_context(workspace=tenant["workspace"], organization=tenant["org"])
    user = User.objects.create_user(
        email=f"schema-reader-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="synthetic-schema-password",
        name="schema reader",
        organization=tenant["org"],
        organization_role="Member",
    )
    membership, _ = OrganizationMembership.no_workspace_objects.update_or_create(
        user=user,
        organization=tenant["org"],
        defaults={"role": "Member", "level": Level.MEMBER, "is_active": True},
    )
    WorkspaceMembership.no_workspace_objects.update_or_create(
        user=user,
        workspace=tenant["workspace"],
        defaults={
            "role": "workspace_viewer",
            "level": Level.WORKSPACE_VIEWER,
            "is_active": True,
            "organization_membership": membership,
        },
    )
    clear_workspace_context()
    return user


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

    def test_dataset_with_no_workspace_saves_and_enforces_its_schema(self, scopes):
        a, _, ca, _ = scopes
        # Seeded demo datasets and pre-workspace datasets carry no workspace;
        # the organization's default workspace reads and saves their schema,
        # and the add-row and import writers then enforce it.
        _legacy(a)
        response = ca.get(path(a))
        assert response.status_code == 200, response.content
        assert response.json()["result"] == {
            "declared": {},
            "enforce": "off",
            "preset": "",
        }
        response = ca.put(path(a), declared(), format="json")
        assert response.status_code == 200, response.content
        assert ca.get(path(a)).json()["result"] == declared()
        before = snapshot(a)
        assert before["config"]["schema"] == declared()
        response = add(ca, a, ["invalid"])
        assert response.status_code == 400, response.content
        response = upload(ca, a, "question\ninvalid\n")
        assert response.status_code == 400, response.content
        assert snapshot(a) == before
        response = add(ca, a, ["allowed"])
        assert response.status_code == 200, response.content
        a["dataset"].refresh_from_db()
        assert a["dataset"].workspace_id is None

    def test_cell_edit_on_a_dataset_with_no_workspace_follows_its_schema(self, scopes):
        a, _, ca, _ = scopes
        _legacy(a)
        install(a)
        before = snapshot(a)
        response = edit(ca, a, "invalid")
        assert response.status_code == 400, response.content
        assert snapshot(a) == before
        response = edit(ca, a, "allowed")
        assert response.status_code == 200, response.content
        a["cells"][0].refresh_from_db()
        assert a["cells"][0].value == "allowed"
        a["dataset"].refresh_from_db()
        assert a["dataset"].workspace_id is None

    @pytest.mark.parametrize("mode", ["off", "check"])
    def test_cell_edit_on_a_dataset_with_no_workspace_keeps_non_reject_modes(
        self, scopes, mode
    ):
        a, _, ca, _ = scopes
        _legacy(a)
        install(a, mode)
        response = edit(ca, a, "invalid")
        assert response.status_code == 200, response.content
        a["cells"][0].refresh_from_db()
        assert a["cells"][0].value == "invalid"

    def test_split_and_schema_writes_follow_the_row_edit_check(self, scopes):
        a, _, ca, _ = scopes
        _legacy(a)
        reader = _default_workspace_viewer(a)
        assert not reader.can_write_to_workspace(a["workspace"])
        cr = _client({**a, "user": reader})
        splits = f"/model-hub/datasets/{a['dataset'].id}/splits/"
        rows = f"/model-hub/develops/{a['dataset'].id}/delete_row/"
        own = ca.post(splits, {"name": "train"}, format="json")
        assert own.status_code == 200, own.content
        split_id = own.json()["result"]["id"]

        def writes(client):
            return {
                "cell edit": edit(client, a, "allowed"),
                "split create": client.post(splits, {"name": "more"}, format="json"),
                "split rename": client.patch(
                    f"{splits}{split_id}/", {"name": "renamed"}, format="json"
                ),
                "schema save": client.put(path(a), declared("off"), format="json"),
                "split delete": client.delete(f"{splits}{split_id}/"),
                "row delete": client.delete(
                    rows,
                    {"row_ids": [str(a["rows"][1].id)], "selected_all_rows": False},
                    format="json",
                ),
            }

        # A default-workspace user who cannot edit or delete rows is refused
        # every split and schema write the same way.
        refused = writes(cr)
        statuses = {name: r.status_code for name, r in refused.items()}
        assert statuses == dict.fromkeys(refused, 403), statuses
        assert len({r.json()["message"] for r in refused.values()}) == 1
        rows_left = Row.no_workspace_objects.filter(dataset=a["dataset"], deleted=False)
        assert rows_left.count() == 2
        # The owner, who may edit and delete those rows, makes every one.
        made = writes(ca)
        statuses = {name: r.status_code for name, r in made.items()}
        assert statuses == dict.fromkeys(made, 200), statuses
        assert rows_left.count() == 1

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
        assert response.status_code == 200, response.content

        rows = Row.no_workspace_objects.filter(dataset=a["dataset"])
        old_row_ids = {row["id"] for row in before["rows"]}
        assert rows.count() == len(before["rows"]) + 1
        appended_row = rows.exclude(id__in=old_row_ids).get()
        appended_cells = Cell.no_workspace_objects.filter(
            dataset=a["dataset"], row=appended_row
        )
        assert appended_cells.count() == 2
        assert appended_cells.get(column=a["column"]).value == ""
        assert appended_cells.get(column__name="another_column").value == "x"

        after = snapshot(a)
        assert [
            row for row in after["rows"] if row["id"] in old_row_ids
        ] == before["rows"]
        old_cell_ids = {cell["id"] for cell in before["cells"]}
        assert [
            cell for cell in after["cells"] if cell["id"] in old_cell_ids
        ] == before["cells"]
        assert after["config"] == before["config"]

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
