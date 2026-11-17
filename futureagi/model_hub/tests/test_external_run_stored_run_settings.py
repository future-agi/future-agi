"""An external experiment run stores its evaluators' run settings only.

Creating an external run pins each evaluator binding it runs with; the pin
holds the binding's run settings (``evaluations.run_settings``) and the
binding keeps its own settings. A run's read returns each pin's settings as
run settings, also when the pin holds the binding's whole settings, as rows
written by earlier releases do; stored rows are left as they are. The choice-score map the results read builds from a pin is
the same as before. Runs from one binding compare by their run settings,
whichever way their pins were stored. Every value here is synthetic.
"""

import uuid

import pytest

from evaluations.run_settings import CREDENTIAL_FIELDS, run_settings
from model_hub.models.dataset_versions import DatasetVersion, DatasetVersionItem
from model_hub.models.develop_dataset import Dataset
from model_hub.models.evals_metric import (
    EvalTemplate,
    EvalTemplateVersion,
    UserEvalMetric,
)
from model_hub.models.experiments import ExperimentRun, ExperimentRunItem
from model_hub.serializers.experiments import ExperimentRunSerializer
from model_hub.services.experiment_run_creation import create_experiment_run
from model_hub.services.experiment_run_writes import _digest
from model_hub.services.persisted_run_comparison import (
    RunComparisonError,
    _bindings,
    compare_experiment_runs,
)
from model_hub.services.results_analysis import build_analysis
from model_hub.services.results_catalog import (
    metric_shape,
    pinned_choice_scores,
    project_run,
)
from model_hub.tests.test_evaluation_run_settings import (
    LEFT_OUT,
    RUN_SETTINGS,
    SERVICE_ACCOUNT,
    assert_holds_none_of,
    stored_settings,
)

# Every name run settings always leave out: each named field, and one name
# with the aws_ prefix.
LEFT_OUT_NAMES = [*sorted(CREDENTIAL_FIELDS), "aws_secret_access_key"]
PLANTED = "sk-test-SYNTHETIC-PLANTED-0007"
# What the results analysis says about a pair of runs it compares, and about
# a pair whose evaluator pins differ; and the run comparison's refusal.
COMPARED = {"state": "available", "reason": None}
NOT_COMPARED = {
    "state": "incompatible",
    "reason": "Evaluator version or configuration snapshots differ.",
}
REFUSED = "selected evaluator binding snapshots must match"


@pytest.fixture
def dataset_version(organization, workspace, user):
    dataset = Dataset.no_workspace_objects.create(
        name=f"stored-run-settings-{uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
        user=user,
        column_order=[],
        column_config={},
    )
    version = DatasetVersion.no_workspace_objects.create(dataset=dataset)
    item = DatasetVersionItem.no_workspace_objects.create(
        dataset_version=version, input={"case": 1}, expected={"answer": 1}
    )
    return version, item


def _binding(version, settings, organization, workspace, user, **pinned):
    """An evaluator binding on the version's dataset, holding ``settings``;
    ``pinned`` sets fields of its template's default version."""
    template = EvalTemplate.no_workspace_objects.create(
        name=f"stored-run-settings-{uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
        owner="user",
        eval_type="code",
    )
    EvalTemplateVersion.objects.create(
        eval_template=template,
        version_number=1,
        is_default=True,
        config_snapshot={"threshold": 0.5},
        organization=organization,
        workspace=workspace,
        **pinned,
    )
    return UserEvalMetric.no_workspace_objects.create(
        name=f"stored-run-settings-{uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
        template=template,
        dataset=version.dataset,
        user=user,
        config=settings,
    )


def _external_run(version, item, user, binding):
    run = create_experiment_run(
        run_id=uuid.uuid4(),
        dataset_version=version,
        created_by=user,
        task_kind="external",
        expected_items=[(item.example_id, 1)],
        evaluator_metrics=[binding],
    )
    return ExperimentRun.all_objects.get(pk=run.id)


def _scored_run(version, item, user, binding, value):
    """An external run from ``binding`` whose one trial is assessed at
    ``value``."""
    run = _external_run(version, item, user, binding)
    (pin,) = run.evaluator_versions
    ExperimentRunItem.no_workspace_objects.create(
        run=run,
        example_id=item.example_id,
        trial_number=1,
        status="completed",
        score_refs=[
            {
                "metric_id": pin["metric_id"],
                "evaluator_version_id": pin["evaluator_version_id"],
                "status": "assessed",
                "value": value,
            }
        ],
    )
    return run


def _stored_as(run, binding_config):
    """``run`` with its pin holding ``binding_config``, written directly, as a
    row written by an earlier release holds the binding's whole settings."""
    (pin,) = run.evaluator_versions
    ExperimentRun.all_objects.filter(pk=run.id).update(
        evaluator_versions=[{**pin, "binding_config": binding_config}]
    )
    return ExperimentRun.all_objects.get(pk=run.id)


def _comparisons(version, item, binding, baseline, candidate):
    """What the run comparison, the results analysis and the results catalog
    say about the pair: the comparison's refusal (None when it compares the
    runs), the analysis's availability for the pair, and each run's
    comparison group."""
    try:
        compare_experiment_runs(
            baseline_run_id=baseline.id,
            candidate_run_id=candidate.id,
            organization_id=version.dataset.organization_id,
            workspace_id=version.dataset.workspace_id,
            metric_id=binding.id,
        )
        refused = None
    except RunComparisonError as exc:
        refused = str(exc)
    selection = {
        "example_ids": [str(item.example_id)],
        "examples_total": 1,
        "complete": True,
    }
    analysis = build_analysis(
        version.dataset,
        [baseline, candidate],
        selection,
        {"baseline": str(baseline.id)},
    )
    (metric,) = analysis["metrics"]
    (pair,) = metric["vs_baseline"]
    groups = [
        project_run(run, counts={}, progress={})["comparison_group"]
        for run in (baseline, candidate)
    ]
    return refused, pair["availability"], groups


@pytest.mark.django_db
@pytest.mark.parametrize("name", LEFT_OUT_NAMES)
def test_a_new_external_run_stores_its_evaluators_run_settings_only(
    name, dataset_version, organization, workspace, user
):
    """The binding's settings hold a field that run settings leave out, under
    each such name, at the top and inside run_config. The run's pin holds the
    binding's run settings (mapping, choice scores, thresholds) and none of
    those fields; the binding's own settings are unchanged."""
    version, item = dataset_version
    settings = stored_settings()
    settings[name] = PLANTED
    settings["run_config"][name] = PLANTED
    binding = _binding(version, settings, organization, workspace, user)

    run = _external_run(version, item, user, binding)

    (pin,) = run.evaluator_versions
    assert pin["metric_id"] == str(binding.id)
    assert pin["binding_config"] == RUN_SETTINGS
    assert_holds_none_of(run.evaluator_versions, [*LEFT_OUT, PLANTED])
    binding.refresh_from_db()
    assert binding.config == settings


@pytest.mark.django_db
def test_a_run_read_returns_its_stored_evaluator_settings_as_run_settings(
    dataset_version, organization, workspace, user
):
    """A run whose pin holds each binding's whole settings, as rows written by
    earlier releases do (written here directly). ExperimentRunSerializer returns them as run
    settings and every other field of the pin as stored; the stored row is
    left as it is."""
    version, item = dataset_version
    binding = _binding(version, stored_settings(), organization, workspace, user)
    run = _external_run(version, item, user, binding)
    (pin,) = run.evaluator_versions
    earlier = [{**pin, "binding_config": stored_settings()}]
    ExperimentRun.all_objects.filter(pk=run.id).update(evaluator_versions=earlier)
    stored = ExperimentRun.all_objects.get(pk=run.id)

    returned = ExperimentRunSerializer(stored).data["evaluator_versions"]

    assert_holds_none_of(returned)
    (returned_pin,) = returned
    assert returned_pin["binding_config"] == RUN_SETTINGS
    assert {**returned_pin, "binding_config": None} == {**pin, "binding_config": None}
    stored.refresh_from_db()
    assert stored.evaluator_versions == earlier


@pytest.mark.django_db
def test_the_results_choice_map_of_a_new_run_is_unchanged(
    dataset_version, organization, workspace, user
):
    """The results read lays the binding's choice-score edit over the pin's
    choice map. A run whose pin holds its run settings gives the map that the
    same run gives when its pin holds the binding's whole settings."""
    version, item = dataset_version
    settings = {
        "api_key": PLANTED,
        "mapping": {"output": "column-1"},
        "run_config": {
            "choice_scores": {"Fail": 0.2},
            "credentials": dict(SERVICE_ACCOUNT),
        },
    }
    binding = _binding(
        version,
        settings,
        organization,
        workspace,
        user,
        output_type_normalized="deterministic",
        pass_threshold=0.5,
        choice_scores={"Pass": 1, "Fail": 0},
    )

    (pin,) = _external_run(version, item, user, binding).evaluator_versions
    before = {**pin, "binding_config": settings}

    assert metric_shape(pin)["output_type"] == "choice"
    assert pinned_choice_scores(pin, binding) == {"Pass": 1, "Fail": 0.2}
    assert pinned_choice_scores(pin, binding) == pinned_choice_scores(before, binding)


def _other_choice_score():
    """The binding's settings with one choice score changed: run settings
    that really differ."""
    settings = stored_settings()
    settings["choice_scores"] = {**settings["choice_scores"], "other": 0.5}
    return settings


@pytest.mark.django_db
@pytest.mark.parametrize("earlier", ["baseline", "candidate"])
def test_runs_from_one_binding_compare_by_their_run_settings(
    earlier, dataset_version, organization, workspace, user
):
    """Two runs from one binding: one whose pin holds the binding's whole
    settings, as rows written by earlier releases do (written here directly),
    and one whose pin holds its run settings. The run comparison,
    the results analysis and the results catalog compare them, and reading
    them leaves the stored row as it is."""
    version, item = dataset_version
    binding = _binding(version, stored_settings(), organization, workspace, user)
    runs = {
        "baseline": _scored_run(version, item, user, binding, 0.4),
        "candidate": _scored_run(version, item, user, binding, 0.6),
    }
    runs[earlier] = _stored_as(runs[earlier], stored_settings())
    stored = runs[earlier].evaluator_versions
    assert [
        runs[side].evaluator_versions[0]["binding_config"]
        for side in ("baseline", "candidate")
    ].count(RUN_SETTINGS) == 1

    refused, analysis, groups = _comparisons(
        version, item, binding, runs["baseline"], runs["candidate"]
    )

    assert refused is None
    assert analysis == COMPARED
    assert None not in groups and groups[0] == groups[1]
    runs[earlier].refresh_from_db()
    assert runs[earlier].evaluator_versions == stored


@pytest.mark.django_db
@pytest.mark.parametrize(
    "stored_as", [dict, run_settings], ids=["whole_settings", "run_settings"]
)
def test_runs_whose_run_settings_differ_do_not_compare(
    stored_as, dataset_version, organization, workspace, user
):
    """The candidate's pin holds settings with another choice score, stored
    as the whole settings or as run settings. The run comparison refuses the
    pair, the results analysis marks it incompatible and the two runs are in
    different comparison groups."""
    version, item = dataset_version
    binding = _binding(version, stored_settings(), organization, workspace, user)
    baseline = _scored_run(version, item, user, binding, 0.4)
    candidate = _stored_as(
        _scored_run(version, item, user, binding, 0.6),
        stored_as(_other_choice_score()),
    )

    refused, analysis, groups = _comparisons(
        version, item, binding, baseline, candidate
    )

    assert refused == REFUSED
    assert analysis == NOT_COMPARED
    assert None not in groups and groups[0] != groups[1]


@pytest.mark.django_db
@pytest.mark.parametrize("same", [True, False], ids=["same_digest", "other_digest"])
def test_digest_pins_compare_as_before(
    same, dataset_version, organization, workspace, user
):
    """A native run's pin keeps a digest of its binding's settings
    ({"sha256": ...}). Two digest pins (written here directly) compare when
    the digests are equal and not otherwise, and each is read as stored."""
    version, item = dataset_version
    binding = _binding(version, stored_settings(), organization, workspace, user)
    digest = {"sha256": _digest(stored_settings())}
    other = {"sha256": _digest(_other_choice_score())}
    baseline = _stored_as(_scored_run(version, item, user, binding, 0.4), digest)
    candidate = _stored_as(
        _scored_run(version, item, user, binding, 0.6), digest if same else other
    )

    refused, analysis, groups = _comparisons(
        version, item, binding, baseline, candidate
    )

    for run in (baseline, candidate):
        assert _bindings(run)[str(binding.id)] == run.evaluator_versions[0]
    expected = (None, COMPARED) if same else (REFUSED, NOT_COMPARED)
    assert (refused, analysis) == expected
    assert None not in groups and (groups[0] == groups[1]) is same
