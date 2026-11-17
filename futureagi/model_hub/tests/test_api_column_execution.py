"""API-column output compatibility and explicit tenant scope in each entry path."""

import json
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace
from model_hub.models.api_key import SecretModel
from model_hub.tests.rerun_fixtures import generation_orm
from model_hub.views import dynamic_columns as columns
from tfc.middleware.workspace_context import clear_workspace_context
from tfc.tests import outbound_fixture

outbound_wire = outbound_fixture.outbound_wire

ERROR = ("destination not allowed or unreachable", {"response_status": 400})


def config(url):
    return {
        "url": url,
        "method": "POST",
        "output_type": "object",
        "body": {"value": "hello"},
    }


@pytest.mark.parametrize("output_type", ["object", "array", "string", "number"])
def test_public_api_column_output_compatibility(outbound_wire, output_type):
    request_config = config(outbound_wire.url + "/echo")
    request_config["output_type"] = output_type
    value, info = columns.AddApiColumnView()._make_api_call(None, request_config)
    assert info == {"response_status": 200}
    if output_type != "object":
        value = json.loads(value)
    assert value["body"] == '{"value": "hello"}'
    assert value["method"] == "POST"


@pytest.mark.parametrize("location", ["headers", "params"])
@pytest.mark.django_db
def test_secret_scope_without_middleware(
    location, organization, workspace, outbound_wire
):
    own = SecretModel.no_workspace_objects.create(
        name="own-fixture",
        organization=organization,
        workspace=workspace,
        key="own-value",
    )
    other_org = Organization.objects.create(name="other-fixture")
    other_user = User.objects.create_user(
        email="other-fixture@example.test", name="Other Fixture", organization=other_org
    )
    other_workspace = Workspace.objects.create(
        name="other-fixture", organization=other_org, created_by=other_user
    )
    same_org_workspace = Workspace.objects.create(
        name="sibling-fixture",
        organization=organization,
        created_by=workspace.created_by,
    )
    foreign = SecretModel.no_workspace_objects.create(
        name="foreign-fixture",
        organization=other_org,
        workspace=other_workspace,
        key="foreign-value",
    )
    sibling = SecretModel.no_workspace_objects.create(
        name="sibling-fixture",
        organization=organization,
        workspace=same_org_workspace,
        key="sibling-value",
    )
    deleted = SecretModel.no_workspace_objects.create(
        name="deleted-fixture",
        organization=organization,
        workspace=workspace,
        key="deleted-value",
    )
    SecretModel.no_workspace_objects.filter(id=deleted.id).update(deleted=True)
    clear_workspace_context()
    dataset = SimpleNamespace(
        organization_id=organization.id, workspace_id=workspace.id
    )
    cell = SimpleNamespace(row=SimpleNamespace(dataset=dataset))
    for secret_id in [
        foreign.id,
        sibling.id,
        deleted.id,
        "00000000-0000-0000-0000-000000000000",
    ]:
        request_config = config(outbound_wire.url)
        request_config[location] = {
            "X-Fixture": {"type": "Secret", "value": str(secret_id)}
        }
        assert columns.AddApiColumnView()._make_api_call(cell, request_config) == ERROR
    assert not outbound_wire.received
    request_config[location]["X-Fixture"]["value"] = str(own.id)
    assert columns.AddApiColumnView()._make_api_call(cell, request_config)[1] == {
        "response_status": 200
    }
    _, path, headers, _ = outbound_wire.received[-1]
    assert (
        headers["X-Fixture"] if location == "headers" else path.split("=", 1)[1]
    ) == "own-value"


@pytest.mark.parametrize("module_name", ["dynamic_columns", "develop_dataset"])
def test_preview_and_conditional_without_source_keep_dataset_scope(
    monkeypatch, module_name
):
    conditional_columns = import_module(f"model_hub.views.{module_name}")
    dataset = SimpleNamespace(organization_id="organization", workspace_id="workspace")
    row = SimpleNamespace(id="row", dataset=dataset)
    seen = []

    def invoke(self, cell, config, *, dataset=None):
        seen.append((getattr(cell, "row", None), dataset))
        return ERROR

    monkeypatch.setattr(columns.AddApiColumnView, "_make_api_call", invoke)
    monkeypatch.setattr(columns, "Cell", lambda **kwargs: SimpleNamespace(**kwargs))
    columns.PreviewDatasetOperationView()._preview_api_call(
        {"config": config("http://public.fixture.test")}, [row], "organization", dataset
    )
    monkeypatch.setattr(conditional_columns.AddApiColumnView, "_make_api_call", invoke)
    monkeypatch.setattr(
        conditional_columns, "Cell", lambda **kwargs: SimpleNamespace(**kwargs)
    )
    result = conditional_columns.ConditionalColumnView()._process_branch(
        row,
        {
            "branch_node_config": {
                "type": "api_call",
                "config": {"config": config("http://public.fixture.test")},
            }
        },
        "organization",
    )
    assert result == ERROR
    assert seen == [(row, dataset), (None, dataset)]


@pytest.mark.parametrize("rerun", [False, True])
def test_background_create_and_rerun_store_generic_cell_error(monkeypatch, rerun):
    dataset = SimpleNamespace(
        id="dataset", organization_id="organization", workspace_id="workspace"
    )
    row = SimpleNamespace(
        id="row", dataset=dataset, dataset_id=dataset.id, deleted=False, order=0
    )
    existing = SimpleNamespace(
        row_id="row", row=row, dataset_id=dataset.id, column_id="column",
        value="previous", status="PASS", deleted=False,
    )
    saved = []
    cell_manager = Mock()
    cell_manager.filter.return_value.select_related.return_value = [existing]
    cell_manager.bulk_create.side_effect = lambda values, **kwargs: saved.extend(values)
    cell_manager.bulk_update.side_effect = lambda values, *args, **kwargs: saved.extend(
        values
    )
    cells = Mock(side_effect=lambda **kwargs: SimpleNamespace(deleted=False, **kwargs))
    cells.objects = cell_manager
    rows = Mock()
    rows.objects.filter.return_value.order_by.return_value = [row]
    datasets = Mock()
    datasets.no_workspace_objects.get.return_value = dataset
    monkeypatch.setattr(columns, "Cell", cells)
    monkeypatch.setattr(columns, "Row", rows)
    column_model = Mock()
    monkeypatch.setattr(columns, "Column", column_model)
    monkeypatch.setattr(columns, "Dataset", datasets)
    request_config = config("http://127.0.0.1/")
    tracked_column = SimpleNamespace(
        id="column", dataset_id=dataset.id, name="API output",
        source="api_call", metadata=request_config, deleted=False,
    )
    with generation_orm(
        column=tracked_column, rows=lambda: [row], cells=lambda: saved,
        cell_factory=cells,
        on_column_update=column_model.objects.filter.return_value.update,
    ) as snapshots:
        columns.add_api_column_async._original_func(
            request_config, "dataset", 1, "column", is_rerun=rerun
        )
    assert len(saved) == 1
    assert saved[0].value == ERROR[0]
    assert saved[0].status == columns.CellStatus.ERROR.value
    assert json.loads(saved[0].value_infos) == ERROR[1]
    assert set(snapshots) == {("column", "row")}
    assert snapshots[("column", "row")].fingerprint.startswith("sha256:")
    assert tracked_column.status == columns.StatusType.COMPLETED.value


def test_legacy_entry_delegates_to_canonical_destination_check():
    from model_hub.views.develop_dataset import AddApiColumnView as LegacyApiColumn

    assert LegacyApiColumn()._make_api_call(None, config("http://127.0.0.1/")) == ERROR
