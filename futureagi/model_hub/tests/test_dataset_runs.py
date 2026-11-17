"""SRV-4: a dataset's experiment runs, their #N labels and app links, and the
dataset baseline pin, through the real views."""

import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.db import close_old_connections
from django.urls import reverse
from rest_framework import status

from model_hub.models.dataset_versions import DatasetVersion, DatasetVersionItem
from model_hub.models.develop_dataset import Dataset
from model_hub.models.experiments import DatasetBaseline, ExperimentRun
from model_hub.services import experiment_run_creation

# reverse() in a test body may be the session's first URLconf import; see
# urlconf_loaded_without_workspace_context in model_hub/tests/conftest.py.
pytestmark = pytest.mark.usefixtures("urlconf_loaded_without_workspace_context")

FRONTEND = "http://localhost:3031"


def _dataset(organization, workspace, user, cases=2, name="runs-list-dataset"):
    dataset = Dataset.no_workspace_objects.create(
        name=name,
        organization=organization,
        workspace=workspace,
        user=user,
        column_order=[],
        column_config={},
    )
    version, examples = _version(dataset, cases)
    return dataset, version, examples


def _version(dataset, cases=2):
    version = DatasetVersion.no_workspace_objects.create(dataset=dataset)
    examples = [
        DatasetVersionItem.no_workspace_objects.create(
            dataset_version=version, input={"case": value}, expected={"answer": value}
        )
        for value in range(cases)
    ]
    return version, examples


def _create(client, version, examples, metadata=None, run_id=None):
    body = {
        "run_id": run_id or str(uuid.uuid4()),
        "dataset_version_id": str(version.id),
        "expected_items": [
            {"example_id": str(example.example_id), "trial_number": 1}
            for example in examples
        ],
        "evaluator_metric_ids": [],
        "model_config": {},
        "metadata": metadata if metadata is not None else {"name": "fixture run"},
    }
    response = client.post(
        reverse("external-experiment-run-create"), body, format="json"
    )
    assert response.status_code == status.HTTP_200_OK, response.json()
    return response.json()["result"]


def _finish(client, run, examples, item_status="completed"):
    response = client.post(
        reverse("external-experiment-run-results", kwargs={"run_id": run["id"]}),
        {
            "items": [
                {
                    "example_id": str(example.example_id),
                    "trial_number": 1,
                    "receipt_key": f"{run['id']}-{example.example_id}",
                    "status": item_status,
                    "output": {"answer": index} if item_status == "completed" else None,
                }
                for index, example in enumerate(examples)
            ]
        },
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK, response.json()
    return response.json()["result"]["run"]


def _list(client, dataset, **query):
    return client.get(
        reverse("dataset-experiment-runs", kwargs={"dataset_id": dataset.id}), query
    )


def _baseline_url(dataset):
    return reverse("dataset-baseline", kwargs={"dataset_id": dataset.id})


@pytest.fixture
def frontend(settings):
    settings.FRONTEND_BASE_URL = FRONTEND
    return FRONTEND


@pytest.mark.django_db
def test_runs_are_listed_newest_first_with_labels_counts_and_app_links(
    auth_client, organization, workspace, user, frontend
):
    dataset, version, examples = _dataset(organization, workspace, user)
    first = _create(auth_client, version, examples, {"name": "first"})
    second = _create(auth_client, version, examples, {"name": "second"})
    third = _create(auth_client, version, examples, {"name": 7})
    # The create response carries the label and the link too.
    assert (first["ordinal"], first["label"]) == (1, "#1")
    assert (second["ordinal"], third["ordinal"]) == (2, 3)
    assert first["app_url"] == (
        f"{frontend}/dashboard/develop/{dataset.id}?tab=experiments&run={first['id']}"
    )
    _finish(auth_client, second, examples[:1])

    response = _list(auth_client, dataset)

    assert response.status_code == status.HTTP_200_OK, response.json()
    result = response.json()["result"]
    assert result["dataset_id"] == str(dataset.id)
    assert result["total"] == 3 and result["has_more"] is False
    assert [run["label"] for run in result["runs"]] == ["#3", "#2", "#1"]
    newest, middle, oldest = result["runs"]
    assert newest["name"] is None  # metadata.name was not a string
    assert middle["name"] == "second" and oldest["name"] == "first"
    assert middle["status"] == "running"
    assert middle["plan_size"] == 2
    assert middle["item_counts"] == {
        "pending": 0,
        "running": 0,
        "completed": 1,
        "failed": 0,
        "skipped": 0,
        "not_started": 1,
    }
    assert oldest["item_counts"]["not_started"] == 2
    assert all(run["final"] is True for run in result["runs"])
    assert all(run["is_baseline"] is False for run in result["runs"])
    assert oldest["app_url"] == first["app_url"]
    assert oldest["task_kind"] == "external"
    assert oldest["dataset_version_id"] == str(version.id)
    assert result["baseline"] == {
        "baseline_run_id": None,
        "label": None,
        "pinned_by": None,
        "pinned_at": None,
    }
    # The SRV-3 header reads the same label.
    detail = auth_client.get(
        reverse("experiment-run-detail", kwargs={"run_id": second["id"]})
    ).json()["result"]
    assert (detail["ordinal"], detail["label"]) == (2, "#2")
    assert detail["app_url"] == middle["app_url"]


@pytest.mark.django_db
def test_app_url_is_null_when_the_deployment_sets_no_frontend_url(
    auth_client, organization, workspace, user, settings
):
    settings.FRONTEND_BASE_URL = ""
    dataset, version, examples = _dataset(organization, workspace, user)
    created = _create(auth_client, version, examples)
    assert created["app_url"] is None
    (listed,) = _list(auth_client, dataset).json()["result"]["runs"]
    assert listed["app_url"] is None


@pytest.mark.django_db
def test_ordinals_are_per_dataset_span_versions_and_are_never_reused(
    auth_client, organization, workspace, user
):
    dataset, version, examples = _dataset(organization, workspace, user)
    other, other_version, other_examples = _dataset(
        organization, workspace, user, name="runs-list-other"
    )
    first = _create(auth_client, version, examples)
    second = _create(auth_client, version, examples)
    assert _create(auth_client, other_version, other_examples)["ordinal"] == 1

    # A replay of the same creation keeps its ordinal.
    replay = _create(auth_client, version, examples, run_id=second["id"])
    assert replay["ordinal"] == 2

    # A deleted run's number is not handed out again.
    ExperimentRun.no_workspace_objects.filter(id=second["id"]).update(deleted=True)
    later_version, later_examples = _version(dataset)
    third = _create(auth_client, later_version, later_examples)
    assert third["ordinal"] == 3

    labels = [
        run["label"] for run in _list(auth_client, dataset).json()["result"]["runs"]
    ]
    assert labels == ["#3", "#1"]
    assert first["ordinal"] == 1


@pytest.mark.django_db
def test_status_version_filters_and_paging(auth_client, organization, workspace, user):
    dataset, version, examples = _dataset(organization, workspace, user)
    done = _create(auth_client, version, examples)
    _finish(auth_client, done, examples)
    _create(auth_client, version, examples)
    later_version, later_examples = _version(dataset)
    on_later = _create(auth_client, later_version, later_examples)

    completed = _list(auth_client, dataset, status="completed").json()["result"]
    assert [run["id"] for run in completed["runs"]] == [done["id"]]
    drafts_and_done = _list(auth_client, dataset, status="draft,completed").json()
    assert drafts_and_done["result"]["total"] == 3

    by_version = _list(
        auth_client, dataset, dataset_version_id=str(later_version.id)
    ).json()["result"]
    assert [run["id"] for run in by_version["runs"]] == [on_later["id"]]

    page = _list(auth_client, dataset, limit=2).json()["result"]
    assert (page["total"], len(page["runs"]), page["has_more"]) == (3, 2, True)
    rest = _list(auth_client, dataset, limit=2, offset=2).json()["result"]
    assert [run["label"] for run in rest["runs"]] == ["#1"]
    assert rest["has_more"] is False

    # A run on a deleted version is not listed.
    DatasetVersion.no_workspace_objects.filter(id=later_version.id).update(deleted=True)
    assert _list(auth_client, dataset).json()["result"]["total"] == 2

    assert _list(auth_client, dataset, status="done").status_code == 400
    assert _list(auth_client, dataset, limit=0).status_code == 400
    assert _list(auth_client, dataset, sort="newest").status_code == 400


@pytest.mark.django_db
def test_baseline_pin_replace_and_unpin_round_trip(
    auth_client, organization, workspace, user
):
    dataset, version, examples = _dataset(organization, workspace, user)
    first = _create(auth_client, version, examples)
    assert _finish(auth_client, first, examples)["status"] == "completed"
    second = _create(auth_client, version, examples)
    assert _finish(auth_client, second, examples, "failed")["status"] == "failed"
    third = _create(auth_client, version, examples)
    _finish(auth_client, third, examples[:1])
    _finish(
        auth_client,
        {"id": third["id"]},
        examples[1:],
        "skipped",
    )

    unpinned_read = auth_client.get(_baseline_url(dataset))
    assert unpinned_read.status_code == status.HTTP_200_OK, unpinned_read.json()
    assert unpinned_read.json()["result"] == {
        "baseline_run_id": None,
        "label": None,
        "pinned_by": None,
        "pinned_at": None,
    }

    pinned = auth_client.put(
        _baseline_url(dataset), {"run_id": first["id"]}, format="json"
    )
    assert pinned.status_code == status.HTTP_200_OK, pinned.json()
    body = pinned.json()["result"]
    assert body["baseline_run_id"] == first["id"]
    assert body["label"] == "#1"
    assert body["pinned_by"] in {getattr(user, "name", "") or user.email, user.email}
    assert body["pinned_at"] is not None
    # GET reads the same payload the PUT returned.
    assert auth_client.get(_baseline_url(dataset)).json()["result"] == body

    listed = _list(auth_client, dataset).json()["result"]
    assert listed["baseline"]["baseline_run_id"] == first["id"]
    flags = {run["id"]: run["is_baseline"] for run in listed["runs"]}
    assert flags == {first["id"]: True, second["id"]: False, third["id"]: False}

    # A partial run can replace it; the one row per dataset is reused.
    replaced = auth_client.put(
        _baseline_url(dataset), {"run_id": third["id"]}, format="json"
    )
    assert replaced.status_code == status.HTTP_200_OK, replaced.json()
    assert replaced.json()["result"]["label"] == "#3"
    assert DatasetBaseline.all_objects.filter(dataset=dataset).count() == 1

    # A failed run cannot be the baseline, and the pin stays.
    refused = auth_client.put(
        _baseline_url(dataset), {"run_id": second["id"]}, format="json"
    )
    assert refused.status_code == status.HTTP_409_CONFLICT, refused.json()
    assert "failed" in str(refused.json())
    assert (
        _list(auth_client, dataset).json()["result"]["baseline"]["baseline_run_id"]
        == third["id"]
    )

    unpinned = auth_client.delete(_baseline_url(dataset))
    assert unpinned.status_code == status.HTTP_200_OK, unpinned.json()
    assert unpinned.json()["result"]["baseline_run_id"] is None
    unpinned_body = unpinned.json()["result"]
    assert auth_client.get(_baseline_url(dataset)).json()["result"] == unpinned_body
    assert auth_client.delete(_baseline_url(dataset)).status_code == 200
    listed = _list(auth_client, dataset).json()["result"]
    assert listed["baseline"]["baseline_run_id"] is None
    assert not any(run["is_baseline"] for run in listed["runs"])

    # Pinning again revives the same row.
    again = auth_client.put(
        _baseline_url(dataset), {"run_id": first["id"]}, format="json"
    )
    assert again.status_code == status.HTTP_200_OK, again.json()
    assert DatasetBaseline.all_objects.filter(dataset=dataset).count() == 1


@pytest.mark.django_db
def test_runs_that_cannot_be_pinned(auth_client, organization, workspace, user):
    dataset, version, examples = _dataset(organization, workspace, user)
    draft = _create(auth_client, version, examples)
    sample = _create(auth_client, version, examples, {"non_final": True})
    assert _finish(auth_client, sample, examples)["status"] == "completed"
    other, other_version, other_examples = _dataset(
        organization, workspace, user, name="runs-list-other"
    )
    elsewhere = _create(auth_client, other_version, other_examples)
    _finish(auth_client, elsewhere, other_examples)

    def put(run_id):
        return auth_client.put(
            _baseline_url(dataset), {"run_id": run_id}, format="json"
        )

    assert put(draft["id"]).status_code == status.HTTP_409_CONFLICT
    non_final = put(sample["id"])
    assert non_final.status_code == status.HTTP_409_CONFLICT
    assert "non-final" in str(non_final.json())
    (listed,) = [
        run
        for run in _list(auth_client, dataset).json()["result"]["runs"]
        if run["id"] == sample["id"]
    ]
    assert listed["final"] is False
    assert put(elsewhere["id"]).status_code == status.HTTP_404_NOT_FOUND
    assert put(str(uuid.uuid4())).status_code == status.HTTP_404_NOT_FOUND
    assert (
        auth_client.put(
            _baseline_url(dataset),
            {"run_id": elsewhere["id"], "extra": 1},
            format="json",
        ).status_code
        == status.HTTP_400_BAD_REQUEST
    )


@pytest.mark.django_db
def test_a_pinned_run_that_is_deleted_reads_as_no_baseline(
    auth_client, organization, workspace, user
):
    dataset, version, examples = _dataset(organization, workspace, user)
    run = _create(auth_client, version, examples)
    _finish(auth_client, run, examples)
    assert (
        auth_client.put(
            _baseline_url(dataset), {"run_id": run["id"]}, format="json"
        ).status_code
        == 200
    )
    ExperimentRun.no_workspace_objects.filter(id=run["id"]).update(deleted=True)
    result = _list(auth_client, dataset).json()["result"]
    assert result["baseline"]["baseline_run_id"] is None
    assert result["runs"] == []
    read = auth_client.get(_baseline_url(dataset)).json()["result"]
    assert read["baseline_run_id"] is None and read["label"] is None
    # The deleted run cannot be pinned again either.
    assert (
        auth_client.put(
            _baseline_url(dataset), {"run_id": run["id"]}, format="json"
        ).status_code
        == 404
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "hidden_by", ["organization", "workspace", "deleted", "missing"]
)
def test_a_dataset_outside_the_caller_scope_is_404_everywhere(
    auth_client, organization, workspace, user, hidden_by
):
    from accounts.models.organization import Organization
    from accounts.models.workspace import Workspace

    dataset, version, examples = _dataset(organization, workspace, user)
    run = _create(auth_client, version, examples)
    _finish(auth_client, run, examples)
    if hidden_by == "organization":
        other = Organization.objects.create(name="other-runs-org")
        Dataset.no_workspace_objects.filter(id=dataset.id).update(organization=other)
    elif hidden_by == "workspace":
        other = Workspace.no_workspace_objects.create(
            name="other-runs-workspace",
            organization=organization,
            created_by=user,
            is_default=False,
        )
        Dataset.no_workspace_objects.filter(id=dataset.id).update(workspace=other)
    elif hidden_by == "deleted":
        Dataset.no_workspace_objects.filter(id=dataset.id).update(deleted=True)
    else:
        dataset = Dataset(id=uuid.uuid4())
    assert _list(auth_client, dataset).status_code == 404
    assert auth_client.get(_baseline_url(dataset)).status_code == 404
    assert (
        auth_client.put(
            _baseline_url(dataset), {"run_id": run["id"]}, format="json"
        ).status_code
        == 404
    )
    assert auth_client.delete(_baseline_url(dataset)).status_code == 404


@pytest.mark.django_db
def test_a_viewer_and_an_api_key_can_list_but_only_writers_pin(
    auth_client, organization, workspace, user
):
    from rest_framework.test import APIClient

    from accounts.models.organization_membership import OrganizationMembership
    from accounts.models.user import OrgApiKey
    from accounts.models.workspace import WorkspaceMembership
    from tfc.constants.levels import Level

    dataset, version, examples = _dataset(organization, workspace, user)
    run = _create(auth_client, version, examples)
    _finish(auth_client, run, examples)
    key = OrgApiKey.no_workspace_objects.create(
        name="runs-list-fixture",
        organization=organization,
        workspace=workspace,
        user=user,
        type="user",
    )
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=key.api_key, HTTP_X_SECRET_KEY=key.secret_key)
    assert _list(client, dataset).status_code == status.HTTP_200_OK
    # The same key with write access pins.
    assert (
        client.put(_baseline_url(dataset), {"run_id": run["id"]}, format="json")
    ).status_code == status.HTTP_200_OK

    OrganizationMembership.no_workspace_objects.filter(
        user=user, organization=organization
    ).update(level=Level.VIEWER, role="Viewer")
    WorkspaceMembership.no_workspace_objects.filter(
        user=user, workspace=workspace
    ).update(level=Level.WORKSPACE_VIEWER, role="workspace_viewer")
    listed = _list(client, dataset)
    assert listed.status_code == status.HTTP_200_OK, listed.json()
    assert listed.json()["result"]["baseline"]["baseline_run_id"] == run["id"]
    read = client.get(_baseline_url(dataset))
    assert read.status_code == status.HTTP_200_OK, read.json()
    assert read.json()["result"]["baseline_run_id"] == run["id"]
    assert (
        client.put(_baseline_url(dataset), {"run_id": run["id"]}, format="json")
    ).status_code == status.HTTP_403_FORBIDDEN
    assert client.delete(_baseline_url(dataset)).status_code == (
        status.HTTP_403_FORBIDDEN
    )


@pytest.mark.django_db(transaction=True)
def test_concurrent_creates_on_two_versions_of_one_dataset_take_distinct_ordinals(
    organization, workspace, user, monkeypatch
):
    """Review S1: ordinals rest on the dataset row lock, not on a database
    constraint (the dataset is reached through the version). Two creates on
    two versions of one dataset start together; each pauses after the lock
    and before it reads max(ordinal). Without the lock both would read the
    same maximum and take the same number."""
    dataset, version, examples = _dataset(organization, workspace, user)
    _create_direct(version, examples, user)  # ordinal 1
    other_version, other_examples = _version(dataset)
    real_snapshots = experiment_run_creation._evaluator_snapshots

    def slow_snapshots(*args, **kwargs):
        time.sleep(0.5)
        return real_snapshots(*args, **kwargs)

    monkeypatch.setattr(experiment_run_creation, "_evaluator_snapshots", slow_snapshots)
    barrier = Barrier(2)

    def create(on_version, on_examples):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return _create_direct(on_version, on_examples, user).ordinal
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(create, version, examples),
            pool.submit(create, other_version, other_examples),
        ]
        ordinals = sorted(future.result(timeout=60) for future in futures)

    assert ordinals == [2, 3]
    stored = sorted(
        ExperimentRun.all_objects.filter(
            dataset_version__dataset_id=dataset.id
        ).values_list("ordinal", flat=True)
    )
    assert stored == [1, 2, 3]


def _create_direct(version, examples, user):
    return experiment_run_creation.create_experiment_run(
        run_id=uuid.uuid4(),
        dataset_version=version,
        created_by=user,
        task_kind="external",
        expected_items=[(example.example_id, 1) for example in examples],
    )
