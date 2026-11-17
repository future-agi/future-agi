"""A dataset row's runs across experiments: GET datasets/{id}/rows/{row_id}/runs/.

Real persisted rows, versions, runs and tries; real route and auth scope. The
row reaches its runs through the example its dataset versions give it
(a version item's legacy_row). Classic experiments, with no recorded run,
are counted, never listed.
"""

import uuid
from datetime import timedelta

import pytest
from django.urls import NoReverseMatch, reverse
from rest_framework.exceptions import ValidationError

from model_hub.models.dataset_versions import DatasetVersion, DatasetVersionItem
from model_hub.models.develop_dataset import Dataset, Row
from model_hub.models.experiments import (
    ExperimentRun,
    ExperimentRunItem,
    ExperimentsTable,
)
from model_hub.services.experiment_run_writes import _RUN_KEY, _digest, _identities

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("urlconf_loaded_without_workspace_context"),
]


def dataset_with_rows(organization, workspace, user, count=2):
    dataset = Dataset.no_workspace_objects.create(
        name="Row runs",
        organization=organization,
        workspace=workspace,
        user=user,
        column_order=[],
        column_config={},
    )
    rows = [
        Row.no_workspace_objects.create(dataset=dataset, order=i + 1)
        for i in range(count)
    ]
    return dataset, rows


def version_of(dataset, rows, expected=None):
    """A dataset version holding one example per row, linked to the row."""
    version = DatasetVersion.no_workspace_objects.create(dataset=dataset)
    items = {}
    for row in rows:
        items[row.id] = DatasetVersionItem.no_workspace_objects.create(
            dataset_version=version,
            legacy_row=row,
            example_id=DatasetVersionItem.example_id_for_legacy_row(row.id),
            input={"question": f"row {row.order}"},
            expected=(expected or {}).get(row.id, {"answer": row.order}),
        )
    return version, items


def experiment(dataset, user, name):
    return ExperimentsTable.no_workspace_objects.create(
        name=name, dataset=dataset, user=user, experiment_type="llm", prompt_config=[]
    )


def metric(dataset, user):
    from model_hub.services.external_metrics import (
        ExternalMetricDefinition,
        create_or_reuse_external_metric,
    )

    return create_or_reuse_external_metric(
        dataset=dataset,
        user=user,
        definition=ExternalMetricDefinition(
            name="Correct", output_type="pass_fail", source_hash="b" * 64
        ),
    )


def run_of(version, items, user, binding, exp=None):
    from model_hub.services.experiment_run_creation import create_experiment_run

    run = create_experiment_run(
        run_id=uuid.uuid4(),
        dataset_version=version,
        created_by=user,
        task_kind="external",
        experiment=exp,
        evaluator_metrics=[binding.metric],
        expected_items=[(item.example_id, 1) for item in items.values()],
    )
    ExperimentRun.no_workspace_objects.filter(pk=run.id).update(status="completed")
    run.refresh_from_db()
    return run


def earlier(model, obj, days):
    """Move a record back in time, so newest-first orders never tie."""
    model.all_objects.filter(pk=obj.pk).update(
        created_at=obj.created_at - timedelta(days=days)
    )


def tried(run, item, binding, value, output="an answer"):
    return ExperimentRunItem.no_workspace_objects.create(
        run=run,
        example_id=item.example_id,
        trial_number=1,
        status="completed",
        output={"text": output},
        latency_ms=120,
        cost=0.01,
        score_refs=[
            {
                "metric_id": str(binding.metric.id),
                "evaluator_version_id": str(binding.version.id),
                "status": "assessed",
                "value": value,
            }
        ],
    )


def url(dataset, row):
    return reverse(
        "dataset-row-runs", kwargs={"dataset_id": dataset.id, "row_id": row.id}
    )


def read(client, dataset, row, **params):
    return client.get(url(dataset, row), params)


def result(response):
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["status"] is True
    return body["result"]


@pytest.fixture
def two_experiments(organization, workspace, user):
    """Row 1 ran in two experiments (passes in the first, fails in the
    second); row 2 ran only in the first."""
    dataset, rows = dataset_with_rows(organization, workspace, user)
    version, items = version_of(dataset, rows)
    binding = metric(dataset, user)
    first = run_of(version, items, user, binding, experiment(dataset, user, "Prompt A"))
    second = run_of(
        version, items, user, binding, experiment(dataset, user, "Prompt B")
    )
    tried(first, items[rows[0].id], binding, 1, "Paris")
    tried(second, items[rows[0].id], binding, 0, "Lyon")
    tried(first, items[rows[1].id], binding, 1)
    return dataset, rows, (first, second), binding


def test_the_route_exists():
    try:
        reverse(
            "dataset-row-runs",
            kwargs={"dataset_id": uuid.uuid4(), "row_id": uuid.uuid4()},
        )
    except NoReverseMatch:
        pytest.fail("GET datasets/{id}/rows/{row_id}/runs/ has no route")


def test_lists_the_rows_runs_across_experiments(auth_client, two_experiments):
    dataset, rows, (first, second), binding = two_experiments
    data = result(read(auth_client, dataset, rows[0]))
    runs = {r["run_id"]: r for r in data["runs"]}
    assert set(runs) == {str(first.id), str(second.id)}
    assert runs[str(first.id)]["experiment_name"] == "Prompt A"
    assert runs[str(second.id)]["experiment_name"] == "Prompt B"
    assert runs[str(first.id)]["output"] == {"text": "Paris"}
    assert runs[str(second.id)]["output"] == {"text": "Lyon"}
    key = str(binding.metric.id)
    assert runs[str(first.id)]["evals"][key]["value"] == 1
    assert runs[str(second.id)]["evals"][key]["value"] == 0
    assert runs[str(first.id)]["status"] == "completed"
    assert runs[str(first.id)]["trials"] == 1
    assert runs[str(first.id)]["label"] == f"#{first.ordinal}"
    assert [m["key"] for m in data["metrics"]] == [key]
    assert data["metrics"][0]["name"] == "Correct"
    assert data["metrics"][0]["output_type"] == "pass_fail"
    assert data["summary"] == {
        "runs": 2,
        "experiments": 2,
        "primary_metric_name": "Correct",
        "primary_scored": 2,
        "primary_pass": 1,
        "classic_experiments": 0,
        "flags": {
            "hard_case": False,
            "fails_of_runs": None,
            "flaky": False,
            "expected_changed_after_runs": 0,
        },
    }


def test_lists_only_the_runs_that_tried_this_row(auth_client, two_experiments):
    dataset, rows, (first, _), _ = two_experiments
    data = result(read(auth_client, dataset, rows[1]))
    assert [r["run_id"] for r in data["runs"]] == [str(first.id)]
    assert data["summary"]["runs"] == 1


def test_a_row_in_no_run_is_empty(auth_client, organization, workspace, user):
    dataset, rows = dataset_with_rows(organization, workspace, user, 1)
    version_of(dataset, rows)
    data = result(read(auth_client, dataset, rows[0]))
    assert data["runs"] == [] and data["metrics"] == []
    assert data["summary"]["runs"] == 0
    assert data["summary"]["classic_experiments"] == 0


def test_a_row_never_in_a_version_is_empty(auth_client, organization, workspace, user):
    dataset, rows = dataset_with_rows(organization, workspace, user, 1)
    data = result(read(auth_client, dataset, rows[0]))
    assert data["runs"] == [] and data["summary"]["runs"] == 0


def test_classic_experiments_are_counted_not_listed(auth_client, two_experiments, user):
    dataset, rows, _, _ = two_experiments
    experiment(dataset, user, "Classic")
    data = result(read(auth_client, dataset, rows[0]))
    assert data["summary"]["classic_experiments"] == 1
    assert data["summary"]["runs"] == 2
    assert all(r["experiment_name"] != "Classic" for r in data["runs"])


@pytest.mark.parametrize("other_organization", [False, True])
def test_another_workspace_or_organization_is_not_found(
    auth_client, user, other_organization, organization
):
    from accounts.models.organization import Organization
    from accounts.models.workspace import Workspace

    org = (
        Organization.objects.create(name="Other tenant")
        if other_organization
        else organization
    )
    ws = Workspace.objects.create(
        name="Other workspace",
        organization=org,
        is_default=False,
        is_active=True,
        created_by=user,
    )
    dataset, rows = dataset_with_rows(org, ws, user, 1)
    version, items = version_of(dataset, rows)
    identities = [{"example_id": str(items[rows[0].id].example_id), "trial_number": 1}]
    run = ExperimentRun.no_workspace_objects.create(
        dataset_version=version,
        task_kind="external",
        status="completed",
        ordinal=1,
        evaluator_versions=[],
        metadata={
            _RUN_KEY: {
                "plan": {
                    "identities": identities,
                    "plan_hash": _digest(_identities(identities)),
                }
            }
        },
    )
    ExperimentRunItem.no_workspace_objects.create(
        run=run,
        example_id=items[rows[0].id].example_id,
        trial_number=1,
        status="completed",
        output={"text": "foreign"},
    )
    response = read(auth_client, dataset, rows[0])
    assert response.status_code == 404
    assert str(run.id) not in response.content.decode()


def test_a_row_of_another_dataset_is_not_found(
    auth_client, organization, workspace, user, two_experiments
):
    dataset, _, _, _ = two_experiments
    _, others = dataset_with_rows(organization, workspace, user, 1)
    assert read(auth_client, dataset, others[0]).status_code == 404


def test_a_deleted_row_is_found_only_as_of_a_version(auth_client, two_experiments):
    dataset, rows, (first, second), _ = two_experiments
    Row.all_objects.filter(pk=rows[0].pk).update(deleted=True)
    assert read(auth_client, dataset, rows[0]).status_code == 404
    data = result(read(auth_client, dataset, rows[0], version=1))
    assert {r["run_id"] for r in data["runs"]} == {str(first.id), str(second.id)}


def test_unauthenticated_read_is_refused(api_client, two_experiments):
    dataset, rows, _, _ = two_experiments
    assert read(api_client, dataset, rows[0]).status_code in (401, 403)


@pytest.mark.parametrize(
    "params",
    [{"scope": "everything"}, {"sort": "random"}, {"version": 0}, {"unexpected": "1"}],
)
def test_rejects_unsupported_queries(params):
    from model_hub.serializers.row_runs import DatasetRowRunsQuerySerializer

    with pytest.raises(ValidationError):
        DatasetRowRunsQuerySerializer(data=params).is_valid(raise_exception=True)


def test_sorts_worst_and_best_first(auth_client, two_experiments):
    dataset, rows, (first, second), _ = two_experiments
    worst = result(read(auth_client, dataset, rows[0], sort="worst"))
    best = result(read(auth_client, dataset, rows[0], sort="best"))
    assert [r["run_id"] for r in worst["runs"]] == [str(second.id), str(first.id)]
    assert [r["run_id"] for r in best["runs"]] == [str(first.id), str(second.id)]


def test_scopes_to_the_current_version_and_to_baseline_and_latest(
    auth_client, organization, workspace, user
):
    from model_hub.services.dataset_runs import pin_baseline

    dataset, rows = dataset_with_rows(organization, workspace, user, 1)
    old, old_items = version_of(dataset, rows)
    binding = metric(dataset, user)
    pinned = run_of(old, old_items, user, binding)
    middle = run_of(old, old_items, user, binding)
    current, current_items = version_of(
        dataset, rows, {rows[0].id: {"answer": "changed"}}
    )
    newest = run_of(current, current_items, user, binding)
    for run, items in (
        (pinned, old_items),
        (middle, old_items),
        (newest, current_items),
    ):
        tried(run, items[rows[0].id], binding, 1)
    earlier(DatasetVersion, old, 2)
    earlier(ExperimentRun, pinned, 2)
    earlier(ExperimentRun, middle, 1)
    pin_baseline(dataset, run_id=pinned.id, user=user)

    everything = result(read(auth_client, dataset, rows[0]))
    assert everything["summary"]["runs"] == 3
    on_current = result(read(auth_client, dataset, rows[0], scope="current_version"))
    assert [r["run_id"] for r in on_current["runs"]] == [str(newest.id)]
    assert on_current["summary"]["runs"] == 3
    pair = result(read(auth_client, dataset, rows[0], scope="baseline_latest"))
    assert {r["run_id"] for r in pair["runs"]} == {str(pinned.id), str(newest.id)}
    by_id = {r["run_id"]: r for r in everything["runs"]}
    assert by_id[str(pinned.id)]["is_dataset_baseline"] is True
    assert by_id[str(newest.id)]["is_dataset_baseline"] is False
    # The row's expected value changed after the two runs on the older version.
    assert by_id[str(pinned.id)]["row_changed_after"] is True
    assert by_id[str(newest.id)]["row_changed_after"] is False
    assert everything["summary"]["flags"]["expected_changed_after_runs"] == 2


def test_names_a_hard_case(auth_client, organization, workspace, user):
    dataset, rows = dataset_with_rows(organization, workspace, user, 1)
    version, items = version_of(dataset, rows)
    binding = metric(dataset, user)
    for value in (0, 0, 0, 1):
        tried(run_of(version, items, user, binding), items[rows[0].id], binding, value)
    flags = result(read(auth_client, dataset, rows[0]))["summary"]["flags"]
    assert flags["hard_case"] is True
    assert flags["fails_of_runs"] == [3, 4]


def test_each_run_names_the_rows_example(auth_client, two_experiments):
    # The experiment's Data page opens a row by its example id.
    dataset, rows, (first, second), _ = two_experiments
    example = str(DatasetVersionItem.example_id_for_legacy_row(rows[0].id))
    data = result(read(auth_client, dataset, rows[0]))
    assert {r["run_id"]: r["example_id"] for r in data["runs"]} == {
        str(first.id): example,
        str(second.id): example,
    }
    assert example != str(rows[0].id)


def bad_native_record(run):
    """A run that names an experiment and is not external, with no native
    binding: its native provenance cannot be proven."""
    ExperimentRun.no_workspace_objects.filter(pk=run.pk).update(task_kind="prompt")


def test_a_run_with_a_bad_native_record_is_left_out(auth_client, two_experiments):
    dataset, rows, (first, second), _ = two_experiments
    bad_native_record(second)
    data = result(read(auth_client, dataset, rows[0]))
    assert [r["run_id"] for r in data["runs"]] == [str(first.id)]
    assert data["summary"]["runs"] == 1


def test_a_read_whose_every_run_fails_its_check_still_fails(
    auth_client, two_experiments
):
    # Green at the base: leaving out every run would read as "never ran".
    dataset, rows, (first, second), _ = two_experiments
    bad_native_record(first)
    bad_native_record(second)
    assert read(auth_client, dataset, rows[0]).status_code == 404


def test_the_read_does_not_project_each_run(auth_client, two_experiments, monkeypatch):
    from model_hub.services import results_catalog, row_runs

    def projected(*args, **kwargs):
        raise AssertionError("the full run projection ran for one run")

    monkeypatch.setattr(row_runs, "project_run", projected, raising=False)
    monkeypatch.setattr(results_catalog, "project_run", projected)
    monkeypatch.setattr(results_catalog, "native_projection", projected)
    dataset, rows, (first, second), _ = two_experiments
    data = result(read(auth_client, dataset, rows[0]))
    by_id = {r["run_id"]: r for r in data["runs"]}
    assert by_id[str(first.id)]["label"] == f"#{first.ordinal}"
    assert by_id[str(first.id)]["experiment_name"] == "Prompt A"
    assert by_id[str(second.id)]["experiment_name"] == "Prompt B"
    version = by_id[str(first.id)]["dataset_version"]
    assert version["id"] == str(first.dataset_version_id)


def test_evaluators_are_validated_once(auth_client, two_experiments, monkeypatch):
    from model_hub.services import results_catalog

    calls = []
    original = results_catalog.validate_evaluators

    def counted(dataset, runs):
        calls.append(len(runs))
        return original(dataset, runs)

    monkeypatch.setattr(results_catalog, "validate_evaluators", counted)
    dataset, rows, _, _ = two_experiments
    result(read(auth_client, dataset, rows[0]))
    assert calls == [2]


def test_the_query_count_does_not_grow_with_runs(
    auth_client, organization, workspace, user, django_assert_max_num_queries
):
    # Ten external runs are read in no more queries than one run: the read
    # looks nothing up once per run, the runs' scope checks included.
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    dataset, rows = dataset_with_rows(organization, workspace, user, 1)
    version, items = version_of(dataset, rows)
    binding = metric(dataset, user)
    tried(run_of(version, items, user, binding), items[rows[0].id], binding, 1)
    with CaptureQueriesContext(connection) as one:
        assert result(read(auth_client, dataset, rows[0]))["summary"]["runs"] == 1
    for _ in range(9):
        tried(run_of(version, items, user, binding), items[rows[0].id], binding, 1)
    with django_assert_max_num_queries(len(one.captured_queries)) as ten:
        assert result(read(auth_client, dataset, rows[0]))["summary"]["runs"] == 10
    one_run, ten_runs = len(one.captured_queries), len(ten.captured_queries)
    # Shown with pytest -rP, so a passing run still names both counts.
    print(f"row read queries: 1 run {one_run}, 10 runs {ten_runs}")
