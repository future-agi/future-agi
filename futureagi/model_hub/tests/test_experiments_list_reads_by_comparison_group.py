"""The dataset's Experiments list reads each run's numbers from the run
catalog and the results matrix. The matrix answers runs of one comparison
group per read, and adds the dataset's pinned baseline unless the read says
baseline=none. These tests go through the real routes: runs of several
experiments, in four comparison groups (shared plan; other evaluators;
another dataset version; two tries per row), with a pinned baseline in one
of them.
"""

import uuid

import pytest
from django.urls import reverse

from model_hub.models.dataset_versions import DatasetVersion, DatasetVersionItem
from model_hub.models.develop_dataset import Dataset
from model_hub.models.experiments import ExperimentsTable

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("urlconf_loaded_without_workspace_context"),
]

# The list's per-read run cap (useLeaderboardNumbers MATRIX_RUNS_PER_READ).
RUNS_PER_READ = 8


def _result(response):
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["status"] is True
    return body["result"]


def _metric(client, dataset, name, source_hash):
    return _result(
        client.post(
            reverse("dataset-external-metrics", kwargs={"dataset_id": dataset.id}),
            {
                "name": name,
                "output_type": "numeric",
                "higher_is_better": True,
                "pass_threshold": 0.5,
                "source_hash": source_hash,
            },
            format="json",
        )
    )


def _version(dataset, count):
    version = DatasetVersion.no_workspace_objects.create(dataset=dataset)
    items = [
        DatasetVersionItem.no_workspace_objects.create(
            dataset_version=version, input={"case": index}
        )
        for index in range(count)
    ]
    return version, [str(item.example_id) for item in items]


def _run(client, version, example_ids, metric, experiment, values, trials=1):
    """One external run, created and finished through the public routes.

    ``values`` holds one score per (row, try), in row order then try order; a
    value of None records that try as failed with no score.
    """
    run_id = str(uuid.uuid4())
    identities = [
        {"example_id": eid, "trial_number": t}
        for eid in example_ids
        for t in range(1, trials + 1)
    ]
    created = _result(
        client.post(
            reverse("external-experiment-run-create"),
            {
                "run_id": run_id,
                "dataset_version_id": str(version.id),
                "expected_items": identities,
                "evaluator_metric_ids": [metric["metric_id"]],
                "experiment_id": str(experiment.id),
            },
            format="json",
        )
    )
    pin = created["evaluator_bindings"][0]
    items = []
    for index, (identity, value) in enumerate(zip(identities, values, strict=True)):
        item = {
            **identity,
            "receipt_key": f"{run_id}-{index}",
            "latency_ms": 1000 + index,
            "cost": 0.01,
        }
        if value is None:
            item.update(status="failed", error={"kind": "task_error"})
        else:
            item.update(
                status="completed",
                output={"answer": index},
                score_refs=[
                    {
                        "metric_id": metric["metric_id"],
                        "evaluator_version_id": pin["evaluator_version_id"],
                        "status": "assessed",
                        "value": value,
                    }
                ],
            )
        items.append(item)
    ingested = _result(
        client.post(
            reverse("external-experiment-run-results", kwargs={"run_id": run_id}),
            {"items": items},
            format="json",
        )
    )
    assert ingested["run"]["status"] in ("completed", "partial", "failed")
    return run_id


def _catalog(client, dataset):
    return _result(
        client.get(
            reverse("dataset-results-runs", kwargs={"dataset_id": dataset.id}),
            {"include": "metrics,config"},
        )
    )["results"]


def _matrix_read(client, dataset, run_ids, **extra):
    return client.get(
        reverse("dataset-results", kwargs={"dataset_id": dataset.id}),
        {
            "runs": ",".join(run_ids),
            "shape": "matrix",
            "page": 0,
            "page_size": 500,
            **extra,
        },
    )


def _list_reads(runs):
    """The reads the list makes: per comparison group, at most 8 runs each.

    A run with no comparison group has no immutable plan and is not read.
    """
    groups = {}
    for run in runs:
        if run["comparison_group"]:
            groups.setdefault(run["comparison_group"], []).append(run["run_id"])
    reads = []
    for ids in groups.values():
        ids = sorted(ids)
        for start in range(0, len(ids), RUNS_PER_READ):
            reads.append(ids[start : start + RUNS_PER_READ])
    return reads


def _pass_rate(matrix, run_id):
    """Passed tries over scored tries, errors counting as fails (pass@1)."""
    passed = scored = 0
    for row in matrix["rows"]:
        for value in row["cells"][run_id]["trials"]:
            if value in (True, False, "error"):
                scored += 1
                passed += value is True
    return passed, scored


@pytest.fixture
def listed(auth_client, organization, workspace, user):
    dataset = Dataset.no_workspace_objects.create(
        name="Experiments list numbers",
        organization=organization,
        workspace=workspace,
        user=user,
        column_order=[],
        column_config={},
    )
    first, first_rows = _version(dataset, 3)
    second, second_rows = _version(dataset, 2)
    accuracy = _metric(auth_client, dataset, "accuracy", "a" * 64)
    tone = _metric(auth_client, dataset, "tone", "b" * 64)
    experiments = [
        ExperimentsTable.no_workspace_objects.create(
            name=f"Experiment {name}", dataset=dataset, user=user
        )
        for name in ("A", "B", "C", "D")
    ]
    a, b, c, d = experiments
    on_first = (auth_client, first, first_rows)
    runs = {
        # Group 1: two runs on the same plan and evaluator.
        "a1": _run(*on_first, accuracy, a, [1, 1, 0]),
        "a2": _run(*on_first, accuracy, a, [1, 1, 1]),
        # Group 2: the same rows scored by another evaluator.
        "b1": _run(*on_first, tone, b, [0, 1, None]),
        # Group 3: another dataset version.
        "c1": _run(auth_client, second, second_rows, accuracy, c, [1, 0]),
        # Group 4: two tries per row.
        "d1": _run(*on_first, accuracy, d, [1, 0, 1, 1, 0, 0], trials=2),
    }
    pinned = auth_client.put(
        reverse("dataset-baseline", kwargs={"dataset_id": dataset.id}),
        {"run_id": runs["a2"]},
        format="json",
    )
    assert pinned.status_code == 200, pinned.content
    return dataset, runs


EXPECTED = {"a1": (2, 3), "a2": (3, 3), "b1": (1, 3), "c1": (1, 2), "d1": (3, 6)}


def test_the_catalog_names_four_comparison_groups(auth_client, listed):
    dataset, runs = listed
    catalog = _catalog(auth_client, dataset)
    groups = {r["run_id"]: r["comparison_group"] for r in catalog}
    assert set(groups) == set(runs.values())
    assert groups[runs["a1"]] == groups[runs["a2"]]
    assert len({groups[runs[k]] for k in ("a1", "b1", "c1", "d1")}) == 4


def test_one_read_of_every_run_is_refused(auth_client, listed):
    """The list's old read: every run in one chunk, sorted by id."""
    dataset, runs = listed
    response = _matrix_read(auth_client, dataset, sorted(runs.values()))
    assert response.status_code == 409


def test_a_group_read_without_baseline_none_takes_the_pinned_run(auth_client, listed):
    """The pinned run is in another group, so the read is refused."""
    dataset, runs = listed
    assert _matrix_read(auth_client, dataset, [runs["b1"]]).status_code == 409
    assert _matrix_read(auth_client, dataset, [runs["c1"]]).status_code == 409


def test_each_group_read_answers_and_every_run_has_its_pass_rate(auth_client, listed):
    dataset, runs = listed
    catalog = _catalog(auth_client, dataset)
    reads = _list_reads(catalog)
    assert len(reads) == 4
    seen = {}
    for ids in reads:
        matrix = _result(_matrix_read(auth_client, dataset, ids, baseline="none"))
        assert matrix["summary"]["baseline_run_id"] is None
        assert set(matrix["columns"]) == set(ids)
        for run_id in ids:
            seen[run_id] = _pass_rate(matrix, run_id)
    assert seen == {runs[key]: rate for key, rate in EXPECTED.items()}


def test_each_try_carries_its_recorded_values(auth_client, listed):
    dataset, runs = listed
    matrix = _result(_matrix_read(auth_client, dataset, [runs["b1"]], baseline="none"))
    cells = [row["cells"][runs["b1"]] for row in matrix["rows"]]
    assert sorted(c["trace_summary"][0]["status"] for c in cells) == [
        "completed",
        "completed",
        "error",
    ]
    assert all(c["cost_usd"] == [0.01] for c in cells)
    assert sorted(c["wall_ms"][0] for c in cells) == [1000, 1001, 1002]
    two = _result(_matrix_read(auth_client, dataset, [runs["d1"]], baseline="none"))
    assert all(len(row["cells"][runs["d1"]]["wall_ms"]) == 2 for row in two["rows"])
