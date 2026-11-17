"""The Row header sort and row navigation share one deterministic ordering."""

import json

import pytest

from model_hub.models.develop_dataset import Row
from model_hub.tests.test_dsx_l1_dataset_list_tenancy import tenants  # noqa: F401


@pytest.mark.django_db
@pytest.mark.parametrize("direction", ["ascending", "descending"])
def test_row_order_sort_pages_and_neighbors_agree(tenants, direction):  # noqa: F811
    a, _b, clients = tenants
    dataset = a["dataset"]
    twins = sorted(a["rows"], key=lambda row: row.id)
    Row.no_workspace_objects.filter(id__in=[row.id for row in twins]).update(order=1)
    # These rows deliberately have no cells: Row sorting cannot require a cell.
    low = Row.no_workspace_objects.create(dataset=dataset, order=0)
    high = Row.no_workspace_objects.create(dataset=dataset, order=2)
    expected = [low, *twins, high] if direction == "ascending" else [high, *twins, low]
    expected_ids = [str(row.id) for row in expected]
    sort = [{"column_id": "system:order", "type": direction}]
    root = f"/model-hub/develops/{dataset.id}"
    table_ids = []
    for page in range(2):
        response = clients["a"].get(
            f"{root}/get-dataset-table/",
            {
                "sort": json.dumps(sort),
                "page_size": 2,
                "current_page_index": page,
            },
        )
        assert response.status_code == 200, response.content
        result = response.json()["result"]
        ids = [row["row_id"] for row in result["table"]]
        assert ids == expected_ids[page * 2 : (page + 1) * 2]
        assert result["metadata"]["has_more"] is (page == 0)
        table_ids.extend(ids)
    assert table_ids == expected_ids
    # The equal-order rows straddle the page boundary. UUIDs still ascend when
    # the primary order descends, matching the continuation predicate.
    for index, row_id in enumerate(expected_ids):
        response = clients["a"].post(
            f"{root}/get-row-data/",
            {"row_id": row_id, "filters": [], "sort": sort},
            format="json",
        )
        assert response.status_code == 200, response.content
        result = response.json()["result"]
        assert result["current"]["row_id"] == row_id
        assert result["next"]["row_id"] == expected_ids[index + 1 :]


@pytest.mark.django_db
@pytest.mark.parametrize("column_id", ["system:unknown", "system:row_failed"])
def test_unknown_system_sort_keys_are_rejected(tenants, column_id):  # noqa: F811
    a, _b, clients = tenants
    root = f"/model-hub/develops/{a['dataset'].id}"
    sort = [{"column_id": column_id, "type": "ascending"}]
    table = clients["a"].get(f"{root}/get-dataset-table/", {"sort": json.dumps(sort)})
    detail = clients["a"].post(
        f"{root}/get-row-data/",
        {"row_id": str(a["rows"][0].id), "sort": sort},
        format="json",
    )
    assert table.status_code == 400
    assert detail.status_code == 400
