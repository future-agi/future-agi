"""Dataset column and row mutations preserve parent scope."""

import pytest

from model_hub.models.choices import DataTypeChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.tests.test_dataset_list_tenancy import tenants as tenants_fixture

pytestmark = pytest.mark.django_db
tenants = tenants_fixture


@pytest.mark.parametrize("own", [False, True])
@pytest.mark.parametrize("endpoint", ["columns", "static", "multiple", "rename", "duplicate"])
def test_dataset_column_and_row_mutation_scope(tenants, own, endpoint):
    a, b, clients = tenants
    target = a if own else b
    dataset = target["dataset"]
    new_name = "Scoped new column"
    data_type = DataTypeChoices.TEXT.value
    payloads = {
        "columns": ("post", f"/model-hub/develops/{dataset.id}/add_columns/", {"new_columns_data": [{"name": new_name, "data_type": data_type}]}),
        "static": ("post", f"/model-hub/develops/{dataset.id}/add_static_column/", {"new_column_name": new_name, "column_type": data_type}),
        "multiple": ("post", f"/model-hub/develops/{dataset.id}/add_multiple_static_columns/", {"columns": [{"new_column_name": new_name, "column_type": data_type}]}),
        "rename": ("put", f"/model-hub/develops/{dataset.id}/update_column_name/{target['column'].id}/", {"new_column_name": new_name}),
        "duplicate": ("post", f"/model-hub/datasets/{dataset.id}/duplicate-rows/", {"row_ids": [str(target["rows"][0].id)], "num_copies": 1}),
    }
    models = (Dataset, Row, Column, Cell)
    before = {m: list(m._base_manager.order_by("pk").values()) for m in models}
    method, url, payload = payloads[endpoint]
    response = getattr(clients["a"], method)(url, payload, format="json")
    assert response.status_code == (200 if own else 404), response.data
    if not own:
        assert {m: list(m._base_manager.order_by("pk").values()) for m in models} == before
    elif endpoint == "duplicate":
        assert Row.no_workspace_objects.filter(dataset=dataset, deleted=False).count() == 3
    else:
        assert Column.no_workspace_objects.filter(dataset=dataset, name=new_name, deleted=False).exists()
