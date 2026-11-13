"""Compare endpoint paging and compare_id reuse, driven like CompareData.jsx.

The grid requests ten-row blocks: block 0 without a compare_id, later blocks
with the compare_id returned by block 0. Only the S3 page transport is
replaced with an in-memory store; page writing and slicing are the real code.

Backend replicas share that object store but not their local disk. A replica
is simulated by pointing COMPARE_DATASET_LOCAL_ROOT at its own directory
between requests.

transaction=True is required because the view builds the comparison in
ThreadPoolExecutor workers, which open their own database connections and
cannot see rows created inside a test transaction.
"""

import copy
import csv
import io
import json
import threading
import uuid
from datetime import datetime, timedelta

import pytest
from rest_framework import status

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from conftest import WorkspaceAwareAPIClient
from model_hub.models.choices import DataTypeChoices, SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

COMPARE_URL = "/model-hub/datasets/{dataset_id}/compare-datasets/"
DOWNLOAD_URL = "/model-hub/datasets/{dataset_id}/compare-datasets/download/"
ROW_URL = "/model-hub/datasets/get-compare-row/{compare_id}/{row_id}/"
DELETE_URL = "/model-hub/datasets/delete-compare/{compare_id}/"
PAGE_SIZE = 10


@pytest.fixture
def page_store(monkeypatch, tmp_path):
    """In-memory replacement for the compare page JSON objects."""
    from model_hub.utils.compare_storage import CompareObjectUnavailable

    store = {}
    lock = threading.Lock()

    def write(compare_id, name, data):
        key = f"compare/{compare_id}/{name}"
        with lock:
            store[key] = json.loads(json.dumps(data))

    def read(compare_id, name):
        key = f"compare/{compare_id}/{name}"
        with lock:
            if key not in store:
                raise CompareObjectUnavailable(name)
            return json.loads(json.dumps(store[key]))

    def delete_metadata(compare_id):
        with lock:
            store.pop(f"compare/{compare_id}/metadata.json", None)

    monkeypatch.setattr("model_hub.views.develop_dataset.write_compare_json", write)
    monkeypatch.setattr("model_hub.views.develop_dataset.read_compare_json", read)
    monkeypatch.setattr(
        "model_hub.views.develop_dataset.delete_compare_metadata", delete_metadata
    )
    monkeypatch.setenv("COMPARE_DATASET_LOCAL_ROOT", str(tmp_path / "compare"))
    return store


def _make_dataset(organization, workspace, name, inputs, output_prefix):
    dataset = Dataset.objects.create(
        name=name, organization=organization, workspace=workspace
    )
    input_col = Column.objects.create(
        name="input",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    output_col = Column.objects.create(
        name="output",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    for order, value in enumerate(inputs):
        row = Row.objects.create(dataset=dataset, order=order)
        Cell.objects.create(dataset=dataset, column=input_col, row=row, value=value)
        Cell.objects.create(
            dataset=dataset,
            column=output_col,
            row=row,
            value=f"{output_prefix}-{value}",
        )
    return dataset, input_col, output_col


def _post(auth_client, dataset_id, other_ids, page, compare_id, base="input"):
    return auth_client.post(
        COMPARE_URL.format(dataset_id=dataset_id),
        {
            "dataset_ids": [str(other_id) for other_id in other_ids],
            "base_column_name": base,
            "page_size": PAGE_SIZE,
            "current_page_index": page,
            "compare_id": compare_id,
        },
        format="json",
    )


def _result(response):
    assert response.status_code == status.HTTP_200_OK, response.content[:500]
    body = response.json()
    assert body.get("status") is True, body
    return body["result"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("n_common", [11, 12, 21])
def test_compare_blocks_return_each_joined_row_once(
    auth_client, organization, workspace, page_store, n_common
):
    shared = [f"shared-{index:02d}" for index in range(n_common)]
    left, left_input, _ = _make_dataset(
        organization, workspace, "Paging left", [*shared, "left-only"], "left"
    )
    right, _, _ = _make_dataset(
        organization, workspace, "Paging right", [*shared, "right-only"], "right"
    )

    blocks = []
    compare_id = None
    total_blocks = (n_common + PAGE_SIZE - 1) // PAGE_SIZE
    for page in range(total_blocks):
        result = _result(_post(auth_client, left.id, [right.id], page, compare_id))
        compare_id = compare_id or result["metadata"]["compare_id"]
        assert result["metadata"] == {
            "compare_id": compare_id,
            "total_rows": n_common,
            "total_pages": total_blocks,
        }
        blocks.append(
            [row[str(left_input.id)]["cell_value"] for row in result["table"]]
        )

    remainder = n_common % PAGE_SIZE or PAGE_SIZE
    stored_rows = {
        key.rsplit("/", 1)[-1]: len(value["table"])
        for key, value in page_store.items()
        if key.startswith(f"compare/{compare_id}/page_")
    }
    assert stored_rows == {
        f"page_{page}.json": PAGE_SIZE if page < total_blocks else remainder
        for page in range(1, total_blocks + 1)
    }
    assert [len(block) for block in blocks] == [PAGE_SIZE] * (total_blocks - 1) + [
        remainder
    ]
    returned = [value for block in blocks for value in block]
    assert sorted(returned) == shared


@pytest.mark.django_db(transaction=True)
def test_cached_compare_id_is_rejected_for_another_selection(
    auth_client, organization, workspace, page_store
):
    shared = [f"stale-{index:02d}" for index in range(3)]
    d0, _, _ = _make_dataset(organization, workspace, "Stale d0", shared, "d0")
    d1, _, d1_output = _make_dataset(organization, workspace, "Stale d1", shared, "d1")
    d2, _, _ = _make_dataset(organization, workspace, "Stale d2", shared, "d2")

    compare_a = _result(_post(auth_client, d0.id, [d1.id], 0, None))["metadata"][
        "compare_id"
    ]

    # The comparison it was built for may keep reusing it.
    reused = _result(_post(auth_client, d0.id, [d1.id], 0, compare_a))
    assert reused["metadata"]["compare_id"] == compare_a
    assert all(str(d1_output.id) in row for row in reused["table"])

    # A different compared dataset or base column must not be served d0-vs-d1.
    for other_ids, base in (([d2.id], "input"), ([d1.id], "output")):
        response = _post(auth_client, d0.id, other_ids, 0, compare_a, base=base)
        assert response.status_code == status.HTTP_400_BAD_REQUEST, (
            other_ids,
            base,
            response.content[:500],
        )
        assert str(d1_output.id) not in response.content.decode()


@pytest.fixture
def worker(monkeypatch, page_store):
    """The cleanup worker: another machine, reaching only the shared store."""
    dispatched = []

    def start_activity(activity_name, args=(), **kwargs):
        (compare_id,) = args
        dispatched.append((activity_name, str(compare_id)))
        prefix = f"compare/{compare_id}/"
        for key in [key for key in page_store if key.startswith(prefix)]:
            del page_store[key]
        return f"{activity_name}-test"

    monkeypatch.setattr("tfc.temporal.drop_in.start_activity", start_activity)
    return dispatched


@pytest.fixture
def other_org(user):
    """A second organization with its own owner, workspace and client."""
    suffix = uuid.uuid4().hex[:8]
    organization = Organization.objects.create(name=f"Compare Other Org {suffix}")
    owner = User.objects.create_user(
        email=f"compare-other-{suffix}@futureagi.com",
        password="testpassword123",
        name="Compare Other User",
        organization=organization,
        organization_role=OrganizationRoles.OWNER,
    )
    membership, _ = OrganizationMembership.no_workspace_objects.get_or_create(
        user=owner,
        organization=organization,
        defaults={
            "role": OrganizationRoles.OWNER,
            "level": Level.OWNER,
            "is_active": True,
        },
    )
    workspace = Workspace.objects.create(
        name=f"Compare Other Workspace {suffix}",
        organization=organization,
        is_default=True,
        is_active=True,
        created_by=owner,
    )
    WorkspaceMembership.no_workspace_objects.get_or_create(
        user=owner,
        workspace=workspace,
        defaults={
            "role": "Workspace Owner",
            "level": Level.OWNER,
            "is_active": True,
            "organization_membership": membership,
        },
    )
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=owner)
    client.set_workspace(workspace)
    yield client, organization, workspace
    client.stop_workspace_injection()


def _replica(monkeypatch, tmp_path, name):
    """Serve the following requests from a replica with its own local disk."""
    root = tmp_path / name
    monkeypatch.setenv("COMPARE_DATASET_LOCAL_ROOT", str(root))
    return root


def _stored(page_store, compare_id):
    prefix = f"compare/{compare_id}/"
    return copy.deepcopy(
        {key: value for key, value in page_store.items() if key.startswith(prefix)}
    )


def _row_ids(result):
    return [row["row_id"] for row in result["table"]]


def _download(client, dataset_id, other_ids, compare_id, base="input"):
    return client.post(
        DOWNLOAD_URL.format(dataset_id=dataset_id),
        {
            "dataset_ids": [str(other_id) for other_id in other_ids],
            "base_column_name": base,
            "compare_id": compare_id,
        },
        format="json",
    )


def _csv_rows(response):
    assert response.status_code == status.HTTP_200_OK, response
    body = b"".join(response.streaming_content).decode()
    return list(csv.DictReader(io.StringIO(body)))


@pytest.mark.django_db(transaction=True)
def test_compare_blocks_row_detail_and_download_work_on_any_replica(
    auth_client, organization, workspace, page_store, monkeypatch, tmp_path
):
    shared = [f"replica-{index:02d}" for index in range(21)]
    left, left_input, _ = _make_dataset(
        organization, workspace, "Replica left", [*shared, "left-only"], "left"
    )
    right, _, _ = _make_dataset(
        organization, workspace, "Replica right", [*shared, "right-only"], "right"
    )

    _replica(monkeypatch, tmp_path, "replica-a")
    first = _result(_post(auth_client, left.id, [right.id], 0, None))
    compare_id = first["metadata"]["compare_id"]
    built = _stored(page_store, compare_id)

    # The later blocks reach a replica without a local copy of the comparison.
    _replica(monkeypatch, tmp_path, "replica-b")
    blocks = [first] + [
        _result(_post(auth_client, left.id, [right.id], page, compare_id))
        for page in (1, 2)
    ]
    assert [block["metadata"]["compare_id"] for block in blocks] == [compare_id] * 3
    # It is read, not rebuilt, so the rows keep the ids the grid already holds.
    assert _stored(page_store, compare_id) == built
    returned = [
        row[str(left_input.id)]["cell_value"]
        for block in blocks
        for row in block["table"]
    ]
    assert sorted(returned) == shared
    assert len({row_id for block in blocks for row_id in _row_ids(block)}) == len(
        shared
    )

    # Row detail on a third replica steps across the block edge like the drawer.
    _replica(monkeypatch, tmp_path, "replica-c")
    edge = _row_ids(first)[-1]
    detail = auth_client.get(ROW_URL.format(compare_id=compare_id, row_id=edge))
    assert detail.status_code == status.HTTP_200_OK, detail.content[:500]
    body = detail.json()["result"]
    assert [row["row_id"] for row in body["table"]] == [edge]
    assert body["prev_row_id"] == _row_ids(first)[-2]
    assert body["next_row_id"] == _row_ids(blocks[1])[0]

    exported = _csv_rows(_download(auth_client, left.id, [right.id], compare_id))
    assert sorted(row["input"] for row in exported) == shared


@pytest.mark.django_db(transaction=True)
def test_compare_id_for_another_selection_is_rejected_on_any_replica(
    auth_client, organization, workspace, page_store, monkeypatch, tmp_path
):
    shared = [f"selection-{index:02d}" for index in range(3)]
    d0, _, _ = _make_dataset(organization, workspace, "Selection d0", shared, "d0")
    d1, _, d1_output = _make_dataset(
        organization, workspace, "Selection d1", shared, "d1"
    )
    d2, _, d2_output = _make_dataset(
        organization, workspace, "Selection d2", shared, "d2"
    )

    _replica(monkeypatch, tmp_path, "replica-a")
    compare_id = _result(_post(auth_client, d0.id, [d1.id], 0, None))["metadata"][
        "compare_id"
    ]
    built = _stored(page_store, compare_id)

    _replica(monkeypatch, tmp_path, "replica-b")
    response = _post(auth_client, d0.id, [d2.id], 0, compare_id)
    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content[:500]
    assert _stored(page_store, compare_id) == built

    _replica(monkeypatch, tmp_path, "replica-a")
    reused = _result(_post(auth_client, d0.id, [d1.id], 0, compare_id))
    assert reused["table"]
    for row in reused["table"]:
        assert str(d1_output.id) in row
        assert str(d2_output.id) not in row


@pytest.mark.django_db(transaction=True)
def test_deleted_compare_is_gone_on_every_replica(
    auth_client, organization, workspace, page_store, worker, monkeypatch, tmp_path
):
    shared = [f"delete-{index:02d}" for index in range(3)]
    left, _, _ = _make_dataset(organization, workspace, "Delete left", shared, "left")
    right, _, _ = _make_dataset(
        organization, workspace, "Delete right", shared, "right"
    )

    builder = _replica(monkeypatch, tmp_path, "replica-a")
    first = _result(_post(auth_client, left.id, [right.id], 0, None))
    compare_id = first["metadata"]["compare_id"]
    row_id = _row_ids(first)[0]
    assert (builder / compare_id).is_dir()

    # Exit reaches another replica; the worker removes the shared files.
    _replica(monkeypatch, tmp_path, "replica-b")
    deleted = auth_client.delete(DELETE_URL.format(compare_id=compare_id))
    assert deleted.status_code == status.HTTP_200_OK, deleted.content[:500]
    assert worker == [("delete_compare_folder_activity", compare_id)]
    assert _stored(page_store, compare_id) == {}

    # The local copy left on the replica that built it does not revive it.
    _replica(monkeypatch, tmp_path, "replica-a")
    for response in (
        auth_client.get(ROW_URL.format(compare_id=compare_id, row_id=row_id)),
        _post(auth_client, left.id, [right.id], 0, compare_id),
        auth_client.delete(DELETE_URL.format(compare_id=compare_id)),
    ):
        assert response.status_code == status.HTTP_404_NOT_FOUND, response.content[:500]
    assert len(worker) == 1

    # Exit on the replica that built a comparison also removes its local copy.
    second = _result(_post(auth_client, left.id, [right.id], 0, None))["metadata"][
        "compare_id"
    ]
    assert (builder / second).is_dir()
    deleted = auth_client.delete(DELETE_URL.format(compare_id=second))
    assert deleted.status_code == status.HTTP_200_OK, deleted.content[:500]
    assert not (builder / second).exists()
    assert _stored(page_store, second) == {}


@pytest.mark.django_db(transaction=True)
def test_compare_ids_are_scoped_to_the_requesting_workspace(
    auth_client, user, organization, workspace, page_store, worker, other_org
):
    other_client, other_organization, other_workspace = other_org
    shared = [f"scope-{index:02d}" for index in range(3)]
    left, _, _ = _make_dataset(organization, workspace, "Scope left", shared, "left")
    right, _, _ = _make_dataset(organization, workspace, "Scope right", shared, "right")

    sibling_workspace = Workspace.objects.create(
        name=f"Compare Sibling Workspace {uuid.uuid4().hex[:8]}",
        organization=organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )
    sibling_client = WorkspaceAwareAPIClient()
    sibling_client.force_authenticate(user=user)
    sibling_client.set_workspace(sibling_workspace)

    first = _result(_post(auth_client, left.id, [right.id], 0, None))
    compare_id = first["metadata"]["compare_id"]
    row_id = _row_ids(first)[0]
    built = _stored(page_store, compare_id)

    callers = [
        (other_client, other_organization, other_workspace),
        (sibling_client, organization, sibling_workspace),
    ]
    for client, caller_organization, caller_workspace in callers:
        own_left, _, _ = _make_dataset(
            caller_organization, caller_workspace, "Caller left", shared, "own-left"
        )
        own_right, _, _ = _make_dataset(
            caller_organization, caller_workspace, "Caller right", shared, "own-right"
        )
        unknown = client.get(ROW_URL.format(compare_id=uuid.uuid4(), row_id=row_id))

        for response in (
            client.get(ROW_URL.format(compare_id=compare_id, row_id=row_id)),
            _post(client, own_left.id, [own_right.id], 0, compare_id),
            _post(client, left.id, [right.id], 0, compare_id),
            _download(client, own_left.id, [own_right.id], compare_id),
            client.delete(DELETE_URL.format(compare_id=compare_id)),
        ):
            # Indistinguishable from an id that does not exist.
            assert response.status_code == status.HTTP_404_NOT_FOUND, response.content[
                :500
            ]
            assert response.json() == unknown.json()
        assert unknown.status_code == status.HTTP_404_NOT_FOUND, unknown.content[:500]

    assert worker == []
    assert _stored(page_store, compare_id) == built
    detail = auth_client.get(ROW_URL.format(compare_id=compare_id, row_id=row_id))
    assert detail.status_code == status.HTTP_200_OK, detail.content[:500]


@pytest.mark.django_db(transaction=True)
def test_unknown_compare_id_is_not_found_without_server_details(
    auth_client, organization, workspace, page_store, worker
):
    shared = [f"unknown-{index:02d}" for index in range(3)]
    left, _, _ = _make_dataset(organization, workspace, "Unknown left", shared, "left")
    right, _, _ = _make_dataset(
        organization, workspace, "Unknown right", shared, "right"
    )
    compare_id = str(uuid.uuid4())

    for response in (
        auth_client.get(ROW_URL.format(compare_id=compare_id, row_id=uuid.uuid4())),
        _post(auth_client, left.id, [right.id], 1, compare_id),
        _download(auth_client, left.id, [right.id], compare_id),
        auth_client.delete(DELETE_URL.format(compare_id=compare_id)),
    ):
        assert response.status_code == status.HTTP_404_NOT_FOUND, response.content[:500]
        # Its own code: a client tells it apart from a dataset not found.
        assert response.json()["code"] == "compare_not_found"
        body = response.content.decode()
        for detail in ("metadata.json", "No such file", "media", compare_id):
            assert detail not in body

    # A dataset that is not found keeps the generic code.
    missing_dataset = _download(auth_client, uuid.uuid4(), [right.id], compare_id)
    assert missing_dataset.status_code == status.HTTP_404_NOT_FOUND
    assert missing_dataset.json()["code"] == "not_found"

    # Nothing was built or removed under the supplied id.
    assert _stored(page_store, compare_id) == {}
    assert worker == []


@pytest.mark.django_db(transaction=True)
def test_a_storage_error_is_unavailable_not_an_unknown_comparison(
    auth_client, organization, workspace, page_store, worker, monkeypatch
):
    from model_hub.utils.compare_storage import CompareStorageUnavailable

    shared = [f"unavailable-{index:02d}" for index in range(3)]
    left, _, _ = _make_dataset(
        organization, workspace, "Unavailable left", shared, "left"
    )
    right, _, _ = _make_dataset(
        organization, workspace, "Unavailable right", shared, "right"
    )
    compare_id = str(uuid.uuid4())

    def unavailable(compare_id, name):
        raise CompareStorageUnavailable(name)

    monkeypatch.setattr(
        "model_hub.views.develop_dataset.read_compare_json", unavailable
    )

    for response in (
        auth_client.get(ROW_URL.format(compare_id=compare_id, row_id=uuid.uuid4())),
        _post(auth_client, left.id, [right.id], 1, compare_id),
        _download(auth_client, left.id, [right.id], compare_id),
        auth_client.delete(DELETE_URL.format(compare_id=compare_id)),
    ):
        # Not "not found": the comparison may still exist, so the client
        # must not rebuild it.
        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE, (
            response.content[:500]
        )
        assert response.json()["code"] == "compare_storage_unavailable"
        body = response.content.decode()
        for detail in ("metadata.json", "CompareStorageUnavailable", compare_id):
            assert detail not in body

    # Nothing was built or removed under the supplied id.
    assert _stored(page_store, compare_id) == {}
    assert worker == []


def _rows_by_input(dataset):
    return {
        cell.value: str(cell.row_id)
        for cell in Cell.objects.filter(dataset=dataset, column__name="input")
    }


@pytest.mark.django_db(transaction=True)
def test_build_with_another_organizations_dataset_is_not_found(
    auth_client, organization, workspace, other_org, monkeypatch, tmp_path
):
    _, other_organization, other_workspace = other_org
    compare_root = _replica(monkeypatch, tmp_path, "replica-a")
    shared = [f"foreign-build-{index:02d}" for index in range(3)]
    left, _, _ = _make_dataset(organization, workspace, "Build left", shared, "left")
    foreign, _, _ = _make_dataset(
        other_organization, other_workspace, "Build foreign", shared, "foreign"
    )

    unknown = _post(auth_client, left.id, [uuid.uuid4()], 0, None)
    for response in (
        _post(auth_client, left.id, [foreign.id], 0, None),
        _post(auth_client, foreign.id, [left.id], 0, None),
    ):
        # Indistinguishable from a dataset that does not exist.
        assert response.status_code == status.HTTP_404_NOT_FOUND, response.content[:500]
        assert response.json() == unknown.json()
        assert "foreign-" not in response.content.decode()
    assert unknown.status_code == status.HTTP_404_NOT_FOUND, unknown.content[:500]
    # Nothing was started for the refused selections.
    assert not compare_root.exists() or not any(compare_root.iterdir())


@pytest.mark.django_db(transaction=True)
def test_download_without_compare_id_refuses_rows_of_another_organization(
    auth_client, organization, workspace, other_org
):
    _, other_organization, other_workspace = other_org
    shared = [f"foreign-rows-{index:02d}" for index in range(3)]
    left, _, _ = _make_dataset(organization, workspace, "Rows left", shared, "left")
    right, _, _ = _make_dataset(organization, workspace, "Rows right", shared, "right")
    foreign, _, _ = _make_dataset(
        other_organization, other_workspace, "Rows foreign", shared, "secret"
    )
    left_rows, right_rows = _rows_by_input(left), _rows_by_input(right)
    foreign_rows = _rows_by_input(foreign)

    def download(dataset_info, dataset_id=left.id):
        return auth_client.post(
            DOWNLOAD_URL.format(dataset_id=dataset_id),
            {
                "dataset_ids": [str(right.id)],
                "base_column_name": "input",
                "dataset_info": dataset_info,
                "common_column_names": ["input", "output"],
            },
            format="json",
        )

    # The caller's own rows export as before.
    own = {
        value: {str(left.id): left_rows[value], str(right.id): right_rows[value]}
        for value in shared
    }
    exported = _csv_rows(download(own))
    assert sorted(row["input"] for row in exported) == shared
    assert {row["output-Rows right"] for row in exported} == {
        f"right-{value}" for value in shared
    }

    unknown = download(own, dataset_id=uuid.uuid4())
    assert unknown.status_code == status.HTTP_404_NOT_FOUND, unknown.content[:500]
    for listed_under in (left, right):
        pointed = copy.deepcopy(own)
        pointed[shared[0]][str(listed_under.id)] = foreign_rows[shared[0]]
        response = download(pointed)
        # Answered like an unknown dataset, with none of the foreign cells.
        assert response.status_code == status.HTTP_404_NOT_FOUND, response
        assert response.json() == unknown.json()
        assert "secret-" not in response.content.decode()


def _age_metadata(page_store, compare_id, hours):
    key = f"compare/{compare_id}/metadata.json"
    created = datetime.fromisoformat(page_store[key]["created_at"])
    page_store[key]["created_at"] = (created - timedelta(hours=hours)).isoformat()


@pytest.mark.django_db(transaction=True)
def test_a_comparison_expires_two_hours_after_it_was_created(
    auth_client, organization, workspace, page_store, worker, monkeypatch, tmp_path
):
    shared = [f"expiry-{index:02d}" for index in range(3)]
    left, _, _ = _make_dataset(organization, workspace, "Expiry left", shared, "left")
    right, _, _ = _make_dataset(
        organization, workspace, "Expiry right", shared, "right"
    )
    _replica(monkeypatch, tmp_path, "replica-a")
    fresh = _result(_post(auth_client, left.id, [right.id], 0, None))
    fresh_id = fresh["metadata"]["compare_id"]
    old = _result(_post(auth_client, left.id, [right.id], 0, None))
    old_id = old["metadata"]["compare_id"]
    _age_metadata(page_store, fresh_id, hours=1.5)
    _age_metadata(page_store, old_id, hours=2.5)

    unknown = auth_client.get(
        ROW_URL.format(compare_id=uuid.uuid4(), row_id=uuid.uuid4())
    )
    for response in (
        auth_client.get(ROW_URL.format(compare_id=old_id, row_id=_row_ids(old)[0])),
        _post(auth_client, left.id, [right.id], 0, old_id),
        auth_client.post(
            DOWNLOAD_URL.format(dataset_id=left.id),
            {
                "dataset_ids": [str(right.id)],
                "base_column_name": "input",
                "compare_id": old_id,
            },
            format="json",
        ),
        auth_client.delete(DELETE_URL.format(compare_id=old_id)),
    ):
        assert response.status_code == status.HTTP_404_NOT_FOUND, response.content[:500]
        assert response.json() == unknown.json()

    kept = auth_client.get(
        ROW_URL.format(compare_id=fresh_id, row_id=_row_ids(fresh)[0])
    )
    assert kept.status_code == status.HTTP_200_OK, kept.content[:500]
    assert _result(_post(auth_client, left.id, [right.id], 0, fresh_id))["table"]
