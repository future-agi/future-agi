"""A choice evaluator's results read its label, not its stored score.

An evaluator with output_type "deterministic" and choice_scores
{"Pass": 1, "Fail": 0} stores each answer as its choice's score (1 for
"Pass"). The run catalog (metric_defs) and the grid and matrix reads
(summary.metrics) describe it as a choice evaluator and carry the map the
run scored with, so the results grid, matrix and row drawer can show "Pass"
where they showed "1". The map is only a label source: the verdict, the
direction and the pass threshold are unchanged. Pure functions; no database.
"""

import uuid
from types import SimpleNamespace

import pytest

from model_hub.services import results_catalog
from model_hub.services.experiment_run_writes import _digest
from model_hub.services.results_catalog import metric_definitions, metric_shape
from model_hub.services.results_grid import assessed_scores
from model_hub.services.results_root_causes import pinned_metric_direction

CHOICES = {"Pass": 1, "Fail": 0}


def native_pin(metric_id, config=None, **fields):
    """The pin native_run_binding.native_evaluator_pins stores for a native run."""
    return {
        "definition_hash": "0" * 64,
        "metric_id": metric_id,
        "evaluator_version_id": str(uuid.uuid4()),
        "template_id": str(uuid.uuid4()),
        "version_number": 1,
        "binding_config": {"sha256": _digest(config)},
        "binding_model": {"sha256": _digest(None)},
        "output_type_normalized": "deterministic",
        "pass_threshold": 0.5,
        "choice_scores": dict(CHOICES),
        **fields,
    }


def metric(config=None, multi_choice=False):
    return SimpleNamespace(
        name="judge",
        config=config,
        template=SimpleNamespace(multi_choice=multi_choice),
    )


def run_with(*pins):
    return SimpleNamespace(
        id=uuid.uuid4(),
        evaluator_versions=list(pins),
        dataset_version=SimpleNamespace(dataset=SimpleNamespace(id=uuid.uuid4())),
    )


@pytest.fixture
def evaluators(monkeypatch):
    """Stand in for validate_evaluators: the scope checks are tested elsewhere."""
    found = {}
    monkeypatch.setattr(
        results_catalog, "validate_evaluators", lambda dataset, runs: found
    )
    return found


def test_a_deterministic_evaluator_with_a_choice_map_is_a_choice_metric():
    pin = native_pin(str(uuid.uuid4()))
    assert metric_shape(pin)["output_type"] == "choice"
    # The legacy "choices" output name is the same evaluator kind.
    legacy = {**pin, "output_type_normalized": "choices"}
    assert metric_shape(legacy)["output_type"] == "choice"


def test_metric_defs_name_the_choice_type_and_its_map(evaluators):
    key = str(uuid.uuid4())
    config = {"mapping": {"output": "output"}}
    evaluators[key] = metric(config)
    run = run_with(native_pin(key, config))
    [definition] = metric_definitions([run])
    assert definition["key"] == key
    assert definition["name"] == "judge"
    assert definition["type"] == "deterministic"
    assert definition["output_type"] == "choice"
    assert definition["choice_scores"] == {"Pass": 1, "Fail": 0}
    # Labels never invent a direction or move the threshold.
    assert definition["higher_is_better"] is None
    assert definition["direction"] is None
    assert definition["pass_threshold"] == 0.5


def test_the_label_never_changes_a_verdict_or_a_direction():
    pin = native_pin(str(uuid.uuid4()))
    score = assessed_scores(
        [
            {
                "metric_id": pin["metric_id"],
                "evaluator_version_id": pin["evaluator_version_id"],
                "status": "assessed",
                "value": 1,
            }
        ],
        {pin["metric_id"]: pin},
    )[pin["metric_id"]]
    assert score["value"] == 1
    assert score["passed"] is None
    assert pinned_metric_direction(pin) is None
    assert metric_shape(pin)["higher_is_better"] is None


def test_a_binding_choice_score_edit_is_the_map_the_run_scored_with(evaluators):
    key = str(uuid.uuid4())
    config = {"run_config": {"choice_scores": {"Pass": 0.8}}}
    evaluators[key] = metric(config)
    [definition] = metric_definitions([run_with(native_pin(key, config))])
    assert definition["choice_scores"] == {"Pass": 0.8, "Fail": 0}


def test_a_stored_binding_config_is_read_from_the_pin(evaluators):
    # Pins written by experiment_run_creation keep the binding config itself.
    key = str(uuid.uuid4())
    evaluators[key] = metric({"run_config": {}})
    pin = native_pin(
        key, binding_config={"run_config": {"choice_scores": {"Fail": 0.2}}}
    )
    [definition] = metric_definitions([run_with(pin)])
    assert definition["output_type"] == "choice"
    assert definition["choice_scores"] == {"Pass": 1, "Fail": 0.2}


def test_a_binding_edited_after_the_run_gives_no_map(evaluators):
    # The native pin keeps only a digest; a later edit proves nothing about
    # which scores the run used, so the cells keep their numbers.
    key = str(uuid.uuid4())
    evaluators[key] = metric({"run_config": {"choice_scores": {"Pass": 0}}})
    [definition] = metric_definitions([run_with(native_pin(key, {"run_config": {}}))])
    assert definition["output_type"] == "choice"
    assert definition["choice_scores"] is None


def test_a_multi_choice_evaluator_gives_no_map(evaluators):
    # A multi-choice score is the mean of the picked labels.
    key = str(uuid.uuid4())
    evaluators[key] = metric(multi_choice=True)
    [definition] = metric_definitions([run_with(native_pin(key))])
    assert definition["output_type"] == "choice"
    assert definition["choice_scores"] is None


def test_runs_that_scored_with_different_maps_advertise_none(evaluators):
    key = str(uuid.uuid4())
    evaluators[key] = metric()
    first = run_with(native_pin(key))
    second = run_with(native_pin(key, choice_scores={"Pass": 1, "Fail": 0.5}))
    [definition] = metric_definitions([first, second])
    assert definition["output_type"] == "choice"
    assert definition["choice_scores"] is None
    assert len(definition["runs"]) == 2


@pytest.mark.parametrize("choice_scores", [None, {}])
def test_a_deterministic_evaluator_without_a_map_stays_untyped(
    evaluators, choice_scores
):
    key = str(uuid.uuid4())
    evaluators[key] = metric()
    [definition] = metric_definitions(
        [run_with(native_pin(key, choice_scores=choice_scores))]
    )
    assert definition["output_type"] is None
    assert definition["choice_scores"] is None


def test_other_evaluator_kinds_keep_their_type_and_carry_no_map(evaluators):
    key = str(uuid.uuid4())
    evaluators[key] = metric()
    pin = native_pin(key, output_type_normalized="pass_fail", choice_scores=None)
    [definition] = metric_definitions([run_with(pin)])
    assert definition["output_type"] == "pass_fail"
    assert definition["higher_is_better"] is True
    assert definition["choice_scores"] is None
