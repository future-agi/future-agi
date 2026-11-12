"""Focused M4 regressions: execute complete views with in-memory I/O adapters.

No database, network or worker is required. These tests patch persistence and
request authorization, not the ranking, pin selection or reference resolution.
"""
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from model_hub.views import experiments as views
from model_hub.models.evals_metric import EvalTemplateVersion


class Rows(list):
    def all(self):
        return self

    def filter(self, **conditions):
        def matches(row):
            for key, expected in conditions.items():
                parts = key.split("__")
                operation = parts.pop() if parts[-1] in ("in", "isnull") else "eq"
                value = row
                for part in parts:
                    value = getattr(value, "id" if part == "pk" else part, None)
                if operation == "in" and str(value) not in map(str, expected):
                    return False
                if operation == "eq" and str(value) != str(expected):
                    return False
            return True
        return Rows(row for row in self if matches(row))

    def exclude(self, **conditions):
        matching = self.filter(**conditions)
        return Rows(row for row in self if row not in matching)

    def first(self):
        return self[0] if self else None

    def exists(self):
        return bool(self)

    def get(self, **conditions):
        rows = self.filter(**conditions)
        assert len(rows) == 1, conditions
        return rows[0]

    def select_related(self, *unused):
        return self

    prefetch_related = select_related

    def values_list(self, field, flat=False):
        assert flat
        return [getattr(row, field) for row in self]


def responses():
    return NS(
        success_response=lambda data: NS(status_code=200, data={"result": data}),
        bad_request=lambda message: NS(status_code=400, data={"message": message}),
        not_found=lambda message: NS(status_code=404, data={"message": message}),
    )


def invoke(method, instance, request, experiment_id):
    # Contract decorators are covered elsewhere; supply already-validated data.
    while hasattr(method, "__wrapped__"):
        method = method.__wrapped__
    return method(instance, request, experiment_id)


@pytest.fixture
def add_world(monkeypatch):
    organization = NS(id="org")
    workspace = NS(id="workspace")
    template = NS(id="template", name="judge", config={"required_keys": ["output"]},
                  template_type="single", choices=[], owner="user", organization_id="org",
                  description="judge", eval_tags=[], criteria="", multi_choice=False,
                  output_type_normalized="score", choice_scores={}, pass_threshold=0.5)
    pin = NS(id="selected-version", version_number=2, eval_template=template,
             eval_template_id=template.id, deleted=False,
             config_snapshot={"required_keys": ["output"], "code": "return 1"})
    experiment = NS(id="experiment", dataset=NS(id="source", organization=organization), deleted=False,
                    snapshot_dataset=NS(id="snapshot"), snapshot_dataset_id="snapshot",
                    user_eval_template_ids=Mock())
    request = NS(organization=organization, workspace=workspace, user=NS(organization=organization),
                 validated_data={"name": "judge-v2", "template_id": template.id,
                                 "config": {"mapping": {"output": "output"}},
                                 "pinned_version_id": pin.id, "run": False})
    monkeypatch.setattr(views, "_scoped_experiment_queryset", Mock(return_value=Rows([experiment])))
    monkeypatch.setattr(views, "_accessible_eval_templates", Mock(return_value=Rows([template])))
    monkeypatch.setattr(views, "_usable_external_template", Mock(return_value=False))
    monkeypatch.setattr(views, "caller_workspace", lambda request: workspace)
    monkeypatch.setattr(views, "request_workspace", lambda request: workspace)
    monkeypatch.setattr(views, "validate_eval_references", Mock())
    monkeypatch.setattr(EvalTemplateVersion, "objects", Rows([pin]))
    manager = Mock()
    manager.filter.return_value.exists.return_value = False
    manager.create.side_effect = lambda **kwargs: NS(id="new-metric", **kwargs)
    monkeypatch.setattr(views.UserEvalMetric, "objects", manager)
    handler = views.AddExperimentEvalView()
    handler._gm = responses()
    return NS(template=template, pin=pin, experiment=experiment, request=request,
              manager=manager, handler=handler)


def add(world):
    return invoke(views.AddExperimentEvalView.post, world.handler, world.request, "experiment")


def test_j10_002_add_persists_selected_version(add_world):
    world = add_world
    response = add(world)
    assert response.status_code == 200, response.data
    metric = world.experiment.user_eval_template_ids.add.call_args.args[0]
    assert getattr(metric, "pinned_version", None) is world.pin
    assert metric.config["version_pin"] == "v2"
    assert metric.dataset_id == "snapshot"
    assert response.data["result"]["eval_id"] == str(metric.id)


@pytest.mark.parametrize("invalid", ["unknown", "deleted", "other-template"])
def test_j10_002_refuses_invalid_pin_before_write(add_world, invalid):
    world = add_world
    if invalid == "unknown":
        world.request.validated_data["pinned_version_id"] = "missing-version"
    elif invalid == "deleted":
        world.pin.deleted = True
    else:
        world.pin.eval_template = NS(id="someone-elses-template")
    response = add(world)
    assert response.status_code == 400, response.data
    world.manager.create.assert_not_called()
    world.experiment.user_eval_template_ids.add.assert_not_called()


def test_j10_002_mapping_uses_selected_definition(add_world):
    world = add_world
    world.template.config = {"required_keys": ["old_input"]}
    response = add(world)
    assert response.status_code == 200, response.data
    assert world.manager.create.call_args.kwargs["pinned_version"] is world.pin


def test_j10_002_missing_selected_mapping_is_refused(add_world):
    world = add_world
    world.pin.config_snapshot["required_keys"] = ["reference"]
    response = add(world)
    assert response.status_code == 400, response.data
    world.manager.create.assert_not_called()


def test_j10_002_no_selection_keeps_template_default(add_world):
    world = add_world
    world.request.validated_data.pop("pinned_version_id")
    response = add(world)
    assert response.status_code == 200, response.data
    assert world.manager.create.call_args.kwargs.get("pinned_version") is None


def test_j10_002_save_new_template_cannot_keep_old_templates_pin(add_world, monkeypatch):
    world = add_world
    world.request.validated_data["save_as_template"] = True
    constructor = Mock()
    constructor.objects.filter.return_value.exists.return_value = False
    constructor.no_workspace_objects.filter.return_value.exists.return_value = False
    monkeypatch.setattr(views, "EvalTemplate", constructor)
    response = add(world)
    assert response.status_code == 400, response.data
    constructor.assert_not_called()
    world.manager.create.assert_not_called()


@pytest.fixture
def ranking(monkeypatch):
    comparisons = Mock()
    datasets = Mock()
    datasets.filter.return_value.exists.return_value = True
    monkeypatch.setattr(views.ExperimentComparison, "objects", comparisons)
    monkeypatch.setattr(views.ExperimentDatasetTable, "objects", datasets)
    return comparisons


def variant(name, latency, completion, total, quality=80):
    return {"dataset_id": name, "avg_response_time": latency,
            "avg_completion_tokens": completion, "avg_total_tokens": total, "avg_score": quality}


def test_j10_003_equal_quality_faster_cheaper_wins(ranking):
    metrics = [variant("fast", 100, 5, 10), variant("slow", 1000, 50, 100)]
    views.rank_and_persist_comparisons("experiment", metrics, {})
    assert metrics[0]["dataset_id"] == "fast"
    assert metrics[0]["rank"] == 1
    assert metrics[0]["overall_rating"] == pytest.approx(8.75)
    assert ranking.update_or_create.call_count == 2


def test_j10_003_importance_changes_winner_after_normalization(ranking):
    metrics = [variant("fast", 100, 100, 100, None), variant("cheap", 1000, 10, 10, None)]
    latency_focused = deepcopy(metrics)
    views.rank_and_persist_comparisons("experiment", latency_focused,
                                      {"response_time": 4, "completion_tokens": 1, "total_tokens": 1})
    assert latency_focused[0]["dataset_id"] == "fast"
    assert latency_focused[0]["overall_rating"] == pytest.approx(40 / 6)
    views.rank_and_persist_comparisons("experiment", metrics,
                                      {"response_time": 1, "completion_tokens": 4, "total_tokens": 4})
    assert metrics[0]["dataset_id"] == "cheap"


def test_j10_003_zero_weights_exclude_cost_metrics(ranking):
    metrics = [variant("quality", 1000, 50, 100, 90), variant("fast", 100, 5, 10, 10)]
    views.rank_and_persist_comparisons("experiment", metrics,
                                      {"response_time": 0, "completion_tokens": 0, "total_tokens": 0})
    assert metrics[0]["dataset_id"] == "quality"
    assert metrics[0]["overall_rating"] == pytest.approx(10)


def test_j10_003_all_disabled_without_quality_is_neutral(ranking):
    metrics = [variant("a", 0, 0, 0, None), variant("b", 0, 0, 0, None)]
    views.rank_and_persist_comparisons("experiment", metrics,
                                      {"response_time": 0, "completion_tokens": 0, "total_tokens": 0})
    assert [row["overall_rating"] for row in metrics] == [0, 0]


@pytest.fixture
def reference_world(monkeypatch):
    organization = NS(id="org")
    snapshot = NS(id="snapshot")
    column = NS(id="snapshot-column", source_id="original-column", dataset=snapshot,
                name="Expected answer", deleted=False, source="Text")
    experiment = NS(id="experiment", name="test", column=column, column_id=column.id,
                    dataset=NS(id="source", organization_id=organization.id),
                    snapshot_dataset=snapshot, snapshot_dataset_id=snapshot.id,
                    user_eval_template_ids=Rows(), experiment_datasets=Rows())
    monkeypatch.setattr(views, "_scoped_experiment_queryset", Mock(return_value=Rows([experiment])))
    monkeypatch.setattr(views.Column, "objects", Rows([column]))
    monkeypatch.setattr(views.Cell, "objects", Rows())
    monkeypatch.setattr(views.ExperimentComparison, "objects", Rows())
    request = NS(organization=organization, user=NS(organization=organization), validated_data={})
    return NS(column=column, experiment=experiment, request=request, snapshot=snapshot)


@pytest.mark.parametrize("historical", [False, True])
def test_j10_004_summary_contains_reference(reference_world, historical):
    world = reference_world
    if historical:
        world.experiment.column = NS(id="original-column")
        world.experiment.column_id = "original-column"
    handler = views.ExperimentStatsV2View()
    handler._gm = responses()
    response = invoke(views.ExperimentStatsV2View.get, handler, world.request, "experiment")
    assert response.status_code == 200, response.data
    assert [r["experiment_dataset_name"] for r in response.data["result"]["table_data"]] == ["Expected answer"]


@pytest.mark.parametrize("historical", [False, True])
def test_j10_004_comparison_contains_reference(reference_world, monkeypatch, historical):
    world = reference_world
    if historical:
        world.experiment.column = NS(id="original-column")
        world.experiment.column_id = "original-column"
    handler = views.ExperimentDatasetComparisonV2View()
    handler._gm = responses()
    handler._calculate_base_metrics = Mock(return_value=variant("snapshot", 0, 0, 0))
    rank = Mock()
    monkeypatch.setattr(views, "rank_and_persist_comparisons", rank)
    response = invoke(views.ExperimentDatasetComparisonV2View.post, handler, world.request, "experiment")
    assert response.status_code == 200, response.data
    handler._calculate_base_metrics.assert_called_once_with(world.snapshot, world.column, [], [])
    assert response.data["result"]["total_datasets"] == 1
    rank.assert_called_once()


@pytest.mark.parametrize("invalid", ["deleted", "other-dataset"])
def test_j10_004_summary_excludes_out_of_scope_reference(reference_world, invalid):
    world = reference_world
    if invalid == "deleted":
        world.column.deleted = True
    else:
        world.column.dataset = NS(id="other-snapshot")
    handler = views.ExperimentStatsV2View()
    handler._gm = responses()
    response = invoke(views.ExperimentStatsV2View.get, handler, world.request, "experiment")
    assert response.status_code == 200, response.data
    assert response.data["result"]["table_data"] == []
