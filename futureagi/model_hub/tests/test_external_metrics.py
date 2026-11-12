"""SRV-5: external metrics are created once, pinned into external runs, carry
uploaded scores into compare, store only a source hash, and never execute."""

import hashlib
import uuid

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from rest_framework import status

from model_hub.models.choices import StatusType
from model_hub.models.dataset_versions import DatasetVersion, DatasetVersionItem
from model_hub.models.develop_dataset import Dataset
from model_hub.models.evals_metric import (
    EXTERNAL_EVAL_TYPE,
    EvalTemplate,
    EvalTemplateVersion,
    UserEvalMetric,
)

# reverse() in a test body may be the session's first URLconf import; see
# urlconf_loaded_without_workspace_context in model_hub/tests/conftest.py.
pytestmark = pytest.mark.usefixtures("urlconf_loaded_without_workspace_context")

SENTINEL = "SENTINEL-evaluator-source-7f3a"


def _hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _body(**overrides):
    return {
        "name": "correct",
        "output_type": "pass_fail",
        "higher_is_better": True,
        "pass_threshold": 0.5,
        "source_hash": _hash(f"def correct(output, expected): ...  # {SENTINEL}"),
        **overrides,
    }


def _url(dataset_id):
    return reverse("dataset-external-metrics", kwargs={"dataset_id": dataset_id})


@pytest.fixture
def dataset_fixture(organization, workspace, user):
    dataset = Dataset.no_workspace_objects.create(
        name="external-metrics-dataset",
        organization=organization,
        workspace=workspace,
        user=user,
        column_order=[],
        column_config={},
    )
    version = DatasetVersion.no_workspace_objects.create(dataset=dataset)
    examples = [
        DatasetVersionItem.no_workspace_objects.create(
            dataset_version=version, input={"case": value}, expected={"answer": value}
        )
        for value in (1, 2)
    ]
    return {"dataset": dataset, "version": version, "examples": examples}


def _create_metric(auth_client, dataset, **overrides):
    response = auth_client.post(_url(dataset.id), _body(**overrides), format="json")
    assert response.status_code == status.HTTP_200_OK, response.json()
    return response.json()["result"]


@pytest.mark.django_db
def test_create_then_reuse_and_any_definition_change_is_a_new_metric(
    auth_client, dataset_fixture
):
    dataset = dataset_fixture["dataset"]
    first = _create_metric(auth_client, dataset)
    assert first["created"] is True
    assert first["name"] == "correct"
    assert first["output_type"] == "pass_fail"
    assert first["higher_is_better"] is True
    assert first["pass_threshold"] == 0.5
    assert first["source_hash"] == _body()["source_hash"]
    assert first["version_number"] == 1
    assert first["dataset_id"] == str(dataset.id)

    again = _create_metric(auth_client, dataset)
    assert again["created"] is False
    assert {k: again[k] for k in ("metric_id", "evaluator_version_id")} == {
        k: first[k] for k in ("metric_id", "evaluator_version_id")
    }

    changed_source = _create_metric(
        auth_client, dataset, source_hash=_hash("def correct(): return 1")
    )
    changed_threshold = _create_metric(auth_client, dataset, pass_threshold=0.9)
    changed_direction = _create_metric(
        auth_client,
        dataset,
        output_type="numeric",
        higher_is_better=False,
        pass_threshold=12.5,
    )
    ids = {
        first["metric_id"],
        changed_source["metric_id"],
        changed_threshold["metric_id"],
        changed_direction["metric_id"],
    }
    assert len(ids) == 4
    assert all(
        row["created"] for row in (changed_source, changed_threshold, changed_direction)
    )
    assert changed_direction["output_type"] == "numeric"
    assert changed_direction["pass_threshold"] == 12.5

    template = EvalTemplate.no_workspace_objects.get(id=first["template_id"])
    assert template.eval_type == EXTERNAL_EVAL_TYPE
    assert template.visible_ui is False and template.allow_edit is False
    # No evaluator class can be resolved for it: every engine path that
    # reads config["eval_type_id"] fails closed.
    assert "eval_type_id" not in template.config
    metric = UserEvalMetric.no_workspace_objects.get(id=first["metric_id"])
    assert metric.status == StatusType.INACTIVE.value
    assert metric.show_in_sidebar is False
    assert str(metric.pinned_version_id) == first["evaluator_version_id"]
    assert EvalTemplateVersion.objects.resolve_for_metric(metric).id == uuid.UUID(
        first["evaluator_version_id"]
    )

    listed = auth_client.get(_url(dataset.id))
    assert listed.status_code == status.HTTP_200_OK, listed.json()
    rows = listed.json()["result"]["metrics"]
    assert [row["metric_id"] for row in rows] == [
        first["metric_id"],
        changed_source["metric_id"],
        changed_threshold["metric_id"],
        changed_direction["metric_id"],
    ]
    named = auth_client.get(_url(dataset.id), {"name": "correct", "limit": 2})
    page = named.json()["result"]
    assert len(page["metrics"]) == 2 and page["has_more"] is True


@pytest.mark.django_db
def test_list_pages_only_metrics_that_resolve_to_a_version(
    auth_client, dataset_fixture
):
    """Review S7: a metric whose versions are all deleted is not listed, and it
    no longer counts toward has_more."""
    dataset = dataset_fixture["dataset"]
    gone = _create_metric(auth_client, dataset, name="gone")
    kept = _create_metric(auth_client, dataset, name="kept")
    EvalTemplateVersion.objects.filter(eval_template_id=gone["template_id"]).update(
        deleted=True
    )

    page = auth_client.get(_url(dataset.id), {"limit": 1}).json()["result"]

    assert [row["metric_id"] for row in page["metrics"]] == [kept["metric_id"]]
    assert page["has_more"] is False
    # The next POST of the same definition re-pins it, and it lists again.
    again = _create_metric(auth_client, dataset, name="gone")
    assert again["metric_id"] == gone["metric_id"]
    listed = auth_client.get(_url(dataset.id)).json()["result"]["metrics"]
    assert {row["name"] for row in listed} == {"gone", "kept"}


@pytest.mark.django_db
def test_pinned_into_external_run_ingested_and_compared(auth_client, dataset_fixture):
    fixture = dataset_fixture
    metric = _create_metric(
        auth_client, fixture["dataset"], output_type="percentage", pass_threshold=0.5
    )
    runs = []
    for scores in ((1.0, 0.0), (1.0, 1.0)):
        body = {
            "run_id": str(uuid.uuid4()),
            "dataset_version_id": str(fixture["version"].id),
            "expected_items": [
                {"example_id": str(example.example_id), "trial_number": 1}
                for example in fixture["examples"]
            ],
            "evaluator_metric_ids": [metric["metric_id"]],
            "model_config": {"provider": "fixture", "model": "deterministic"},
            "metadata": {"code_revision": "fixture-v1"},
        }
        created = auth_client.post(
            reverse("external-experiment-run-create"), body, format="json"
        )
        assert created.status_code == status.HTTP_200_OK, created.json()
        binding = created.json()["result"]["evaluator_bindings"][0]
        assert binding["metric_id"] == metric["metric_id"]
        assert binding["evaluator_version_id"] == metric["evaluator_version_id"]
        assert (
            binding["config_snapshot"]["external"]["source_hash"]
            == (metric["source_hash"])
        )
        items = [
            {
                **identity,
                "receipt_key": f"{body['run_id']}-{index}",
                "status": "completed",
                "output": {"answer": index},
                "score_refs": [
                    {
                        "metric_id": metric["metric_id"],
                        "evaluator_version_id": metric["evaluator_version_id"],
                        "status": "assessed",
                        "value": score,
                    }
                ],
            }
            for index, (identity, score) in enumerate(
                zip(body["expected_items"], scores, strict=True)
            )
        ]
        ingested = auth_client.post(
            reverse(
                "external-experiment-run-results", kwargs={"run_id": body["run_id"]}
            ),
            {"items": items},
            format="json",
        )
        assert ingested.status_code == status.HTTP_200_OK, ingested.json()
        assert ingested.json()["result"]["run"]["status"] == "completed"
        runs.append(body["run_id"])

    # A score_ref naming a metric the run did not pin is refused.
    stranger = _create_metric(
        auth_client, fixture["dataset"], name="stranger", pass_threshold=None
    )
    refused = auth_client.post(
        reverse("external-experiment-run-results", kwargs={"run_id": runs[0]}),
        {
            "items": [
                {
                    "example_id": str(fixture["examples"][0].example_id),
                    "trial_number": 1,
                    "receipt_key": f"{runs[0]}-0",
                    "status": "completed",
                    "output": {"answer": 0},
                    "score_refs": [
                        {
                            "metric_id": stranger["metric_id"],
                            "evaluator_version_id": stranger["evaluator_version_id"],
                            "status": "assessed",
                            "value": 1.0,
                        }
                    ],
                }
            ]
        },
        format="json",
    )
    assert refused.status_code == status.HTTP_409_CONFLICT
    # The refusal names the unpinned pair (not a replay or terminal-state
    # conflict, although this run is already completed).
    assert "metric/evaluator pair is not pinned" in str(refused.json())

    compared = auth_client.post(
        reverse("experiment-run-compare"),
        {
            "baseline_run_id": runs[0],
            "candidate_run_id": runs[1],
            "metric_id": metric["metric_id"],
            "passing_score": 0.5,
            "higher_is_better": True,
        },
        format="json",
    )
    assert compared.status_code == status.HTTP_200_OK, compared.json()
    result = compared.json()["result"]
    assert result["evaluator_version_id"] == metric["evaluator_version_id"]
    assert result["comparison"]["baseline"]["score_average"] == 0.5
    assert result["comparison"]["candidate"]["score_average"] == 1.0
    assert result["comparison"]["status_counts"].get("improved") == 1

    # The SRV-3 read returns the uploaded evidence unchanged.
    page = auth_client.get(
        reverse("experiment-run-items", kwargs={"run_id": runs[1]})
    ).json()["result"]
    assert {ref["value"] for item in page["items"] for ref in item["score_refs"]} == {
        1.0
    }


@pytest.mark.django_db
def test_source_is_never_accepted_or_stored(auth_client, dataset_fixture):
    dataset = dataset_fixture["dataset"]
    with_source = auth_client.post(
        _url(dataset.id),
        {**_body(), "source": f"def correct(output, expected):  # {SENTINEL}"},
        format="json",
    )
    assert with_source.status_code == status.HTTP_400_BAD_REQUEST
    assert "source" in with_source.json()["details"]
    assert not UserEvalMetric.no_workspace_objects.filter(dataset=dataset).exists()

    created = _create_metric(auth_client, dataset)
    template = EvalTemplate.no_workspace_objects.get(id=created["template_id"])
    version = EvalTemplateVersion.objects.get(id=created["evaluator_version_id"])
    metric = UserEvalMetric.no_workspace_objects.get(id=created["metric_id"])
    stored = repr(
        [
            template.__dict__,
            version.__dict__,
            metric.__dict__,
        ]
    )
    assert SENTINEL not in stored
    assert created["source_hash"] in stored

    for bad in ("ABC", "g" * 64, _body()["source_hash"].upper()):
        response = auth_client.post(
            _url(dataset.id), _body(source_hash=bad), format="json"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_validation_conflicts_scope_and_write_access(
    auth_client, dataset_fixture, organization, workspace, user
):
    from accounts.models.organization_membership import OrganizationMembership
    from accounts.models.workspace import Workspace, WorkspaceMembership
    from tfc.constants.levels import Level

    dataset = dataset_fixture["dataset"]
    for overrides in (
        {"pass_threshold": 1.5},
        {"output_type": "choices"},
        {"name": "bad\nname"},
        {"name": ""},
    ):
        response = auth_client.post(_url(dataset.id), _body(**overrides), format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST, overrides

    hosted_template = EvalTemplate.no_workspace_objects.create(
        name=f"hosted-score-{uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
        owner="user",
        eval_type="code",
    )
    UserEvalMetric.no_workspace_objects.create(
        name="taken",
        organization=organization,
        workspace=workspace,
        template=hosted_template,
        dataset=dataset,
        user=user,
    )
    conflict = auth_client.post(_url(dataset.id), _body(name="taken"), format="json")
    assert conflict.status_code == status.HTTP_409_CONFLICT

    other = Workspace.no_workspace_objects.create(
        name="other-external-metrics-workspace",
        organization=organization,
        created_by=user,
        is_default=False,
    )
    hidden = Dataset.no_workspace_objects.create(
        name="hidden-external-metrics-dataset",
        organization=organization,
        workspace=other,
        user=user,
        column_order=[],
        column_config={},
    )
    assert auth_client.post(_url(hidden.id), _body(), format="json").status_code == 404
    assert auth_client.get(_url(hidden.id)).status_code == 404
    assert auth_client.get(_url(uuid.uuid4())).status_code == 404

    OrganizationMembership.no_workspace_objects.filter(
        user=user, organization=organization
    ).update(level=Level.VIEWER, role="Viewer")
    WorkspaceMembership.no_workspace_objects.filter(
        user=user, workspace=workspace
    ).update(level=Level.WORKSPACE_VIEWER, role="workspace_viewer")
    denied = auth_client.post(_url(dataset.id), _body(name="viewer"), format="json")
    assert denied.status_code == status.HTTP_403_FORBIDDEN
    assert not UserEvalMetric.no_workspace_objects.filter(name="viewer").exists()
    assert auth_client.get(_url(dataset.id)).status_code == status.HTTP_200_OK


@pytest.mark.django_db
def test_every_execution_path_refuses_an_external_metric(
    auth_client, dataset_fixture, user, workspace
):
    dataset = dataset_fixture["dataset"]
    created = _create_metric(auth_client, dataset)
    metric = UserEvalMetric.no_workspace_objects.get(id=created["metric_id"])

    # Dataset eval run (the user-eval run and re-run share this route).
    start = auth_client.post(
        reverse("start-evals-process", kwargs={"dataset_id": dataset.id}),
        {"user_eval_ids": [str(metric.id)]},
        format="json",
    )
    assert start.status_code == status.HTTP_400_BAD_REQUEST, start.json()
    assert "external" in str(start.json()).lower()
    metric.refresh_from_db()
    assert metric.status == StatusType.INACTIVE.value

    # Compare-datasets start-eval (selects metrics by name).
    compare = auth_client.post(
        reverse("compare-datasets-start-eval", kwargs={"dataset_id": dataset.id}),
        {"user_eval_names": ["correct"], "dataset_ids": [str(dataset.id)]},
        format="json",
    )
    assert compare.status_code == status.HTTP_400_BAD_REQUEST, compare.json()
    assert "external" in str(compare.json()).lower()
    metric.refresh_from_db()
    assert metric.status == StatusType.INACTIVE.value

    # Edit-and-run.
    edit = auth_client.post(
        reverse(
            "edit-and-run-column",
            kwargs={"dataset_id": dataset.id, "eval_id": metric.id},
        ),
        {"name": "correct", "config": {}, "run": True},
        format="json",
    )
    assert edit.status_code == status.HTTP_400_BAD_REQUEST, edit.json()
    assert "external" in str(edit.json()).lower()

    # Attaching the external template as an ordinary dataset eval.
    add = auth_client.post(
        reverse("add_user_eval", kwargs={"dataset_id": dataset.id}),
        {
            "name": "sneaky",
            "template_id": created["template_id"],
            "config": {"mapping": {}},
            "run": True,
        },
        format="json",
    )
    assert add.status_code == status.HTTP_400_BAD_REQUEST, add.json()
    assert "external" in str(add.json()).lower()
    assert not UserEvalMetric.no_workspace_objects.filter(name="sneaky").exists()

    # The model refuses any save that queues or marks execution.
    for executing in (
        StatusType.NOT_STARTED.value,
        StatusType.RUNNING.value,
        StatusType.EXPERIMENT_EVALUATION.value,
    ):
        metric.status = executing
        with pytest.raises(ValidationError):
            metric.save()
    metric.refresh_from_db()
    assert metric.status == StatusType.INACTIVE.value

    # The eval worker's runner refuses before marking the metric running.
    from model_hub.views.eval_runner import EvaluationRunner

    UserEvalMetric.no_workspace_objects.filter(id=metric.id).update(
        status=StatusType.NOT_STARTED.value
    )
    with pytest.raises(ValidationError):
        EvaluationRunner(user_eval_metric_id=metric.id)

    # The hosted eval API (evaluate by template name) refuses it.
    from types import SimpleNamespace

    from sdk.serializers.evaluations import ConfigureEvaluationsSerializer

    template_name = EvalTemplate.no_workspace_objects.get(
        id=created["template_id"]
    ).name
    serializer = ConfigureEvaluationsSerializer(
        data={
            "eval_templates": template_name,
            "inputs": {"output": "x"},
            "model_name": None,
            "config": {},
        },
        context={"request": SimpleNamespace(user=user, workspace=workspace)},
    )
    assert not serializer.is_valid()
    assert "external metric" in str(serializer.errors)

    # The CI/CD evaluate-pipeline API refuses it before creating a run.
    from rest_framework.exceptions import ValidationError as DRFValidationError

    from sdk.serializers.eval_ci_cd import CICDEvaluationItemSerializer

    with pytest.raises(DRFValidationError, match="external"):
        CICDEvaluationItemSerializer()._get_eval_template(template_name, user)


@pytest.mark.django_db
def test_composite_runner_refuses_an_external_child(
    auth_client, dataset_fixture, organization, workspace, user
):
    from model_hub.models.evals_metric import CompositeEvalChild
    from model_hub.services.external_metrics import ExternalMetricExecutionError
    from model_hub.tasks.composite_runner import CompositeEvaluationRunner

    dataset = dataset_fixture["dataset"]
    created = _create_metric(auth_client, dataset)
    parent = EvalTemplate.no_workspace_objects.create(
        name=f"composite-{uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
        owner="user",
        eval_type="llm",
        template_type="composite",
    )
    CompositeEvalChild.objects.create(
        parent=parent,
        child=EvalTemplate.no_workspace_objects.get(id=created["template_id"]),
        order=0,
    )
    binding = UserEvalMetric.no_workspace_objects.create(
        name="composite-binding",
        organization=organization,
        workspace=workspace,
        template=parent,
        dataset=dataset,
        user=user,
    )
    # The constructor loads the template and its children before any work.
    with pytest.raises(ExternalMetricExecutionError):
        CompositeEvaluationRunner(user_eval_metric_id=binding.id)


# ---------------------------------------------------------------------------
# Review M1: every remaining path that attaches or starts a metric for server
# execution refuses an external metric with a 400 that names it, before any
# write (status, source_id, columns, experiment links, workflows).
# ---------------------------------------------------------------------------


def _external(auth_client, dataset):
    created = _create_metric(auth_client, dataset)
    return created, UserEvalMetric.no_workspace_objects.get(id=created["metric_id"])


def _column(dataset, name="output"):
    from model_hub.models.choices import DataTypeChoices, SourceChoices
    from model_hub.models.develop_dataset import Column

    return Column.no_workspace_objects.create(
        name=name,
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.RUN_PROMPT.value,
    )


def _column_count(*datasets):
    from model_hub.models.develop_dataset import Column

    return Column.no_workspace_objects.filter(dataset__in=datasets).count()


def _assert_untouched(metric):
    metric.refresh_from_db()
    assert metric.status == StatusType.INACTIVE.value
    assert metric.source_id in ("", None)


def _assert_refused(response):
    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.json()
    assert "external" in str(response.json()).lower()


def _experiment(dataset, column, *, snapshot=None):
    from model_hub.models.experiments import ExperimentsTable

    return ExperimentsTable.objects.create(
        name=f"external-guard-{uuid.uuid4().hex[:8]}",
        dataset=dataset,
        column=column,
        status=StatusType.COMPLETED.value,
        snapshot_dataset=snapshot,
    )


@pytest.mark.django_db
def test_run_additional_evaluations_refuses_an_external_metric(
    auth_client, dataset_fixture
):
    from unittest.mock import patch

    dataset = dataset_fixture["dataset"]
    _, metric = _external(auth_client, dataset)
    experiment = _experiment(dataset, _column(dataset))
    columns_before = _column_count(dataset)

    with (
        patch("tfc.temporal.experiments.start_experiment_v2_workflow") as workflow,
        patch("model_hub.views.experiments.ExperimentRunner") as runner,
    ):
        response = auth_client.post(
            reverse(
                "run-additional-evaluations",
                kwargs={"experiment_id": str(experiment.id)},
            ),
            {"eval_template_ids": [str(metric.id)]},
            format="json",
        )

    _assert_refused(response)
    workflow.assert_not_called()
    runner.assert_not_called()
    _assert_untouched(metric)
    assert _column_count(dataset) == columns_before
    assert not experiment.user_eval_template_ids.filter(id=metric.id).exists()


@pytest.mark.django_db
def test_legacy_experiment_create_and_update_refuse_an_external_metric(
    auth_client, dataset_fixture
):
    from unittest.mock import patch

    from model_hub.models.experiments import ExperimentsTable

    dataset = dataset_fixture["dataset"]
    _, metric = _external(auth_client, dataset)
    column = _column(dataset)
    existing = _experiment(dataset, column)
    columns_before = _column_count(dataset)
    body = {
        "name": "legacy-with-external",
        "dataset_id": str(dataset.id),
        "column_id": str(column.id),
        "prompt_config": {"model": "gpt-4"},
        "user_eval_template_ids": [str(metric.id)],
    }

    with patch("tfc.temporal.experiments.start_experiment_workflow") as workflow:
        created = auth_client.post(reverse("experiments-create"), body, format="json")
        updated = auth_client.put(
            reverse("experiments-create"),
            {
                **body,
                "name": existing.name,
                "experiment_id": str(existing.id),
                "re_run": True,
            },
            format="json",
        )

    _assert_refused(created)
    _assert_refused(updated)
    workflow.assert_not_called()
    assert not ExperimentsTable.objects.filter(name="legacy-with-external").exists()
    assert not existing.user_eval_template_ids.exists()
    _assert_untouched(metric)
    assert _column_count(dataset) == columns_before


@pytest.mark.django_db
def test_add_experiment_eval_refuses_an_external_template(auth_client, dataset_fixture):
    from unittest.mock import patch

    dataset = dataset_fixture["dataset"]
    created, metric = _external(auth_client, dataset)
    experiment = _experiment(dataset, _column(dataset))
    columns_before = _column_count(dataset)
    templates_before = EvalTemplate.no_workspace_objects.count()

    with (
        patch("tfc.temporal.experiments.start_experiment_v2_workflow") as workflow,
        patch("model_hub.views.experiments.ExperimentRunner") as runner,
    ):
        response = auth_client.post(
            reverse(
                "add-experiment-eval", kwargs={"experiment_id": str(experiment.id)}
            ),
            {
                "name": "copied-external",
                "template_id": created["template_id"],
                "config": {"mapping": {}},
                "save_as_template": True,
                "run": True,
            },
            format="json",
        )

    _assert_refused(response)
    workflow.assert_not_called()
    runner.assert_not_called()
    # save_as_template did not copy the external config into a new template.
    assert EvalTemplate.no_workspace_objects.count() == templates_before
    assert not UserEvalMetric.no_workspace_objects.filter(
        name="copied-external"
    ).exists()
    _assert_untouched(metric)
    assert _column_count(dataset) == columns_before


@pytest.mark.django_db
def test_experiment_v2_eval_entries_refuse_an_external_template(
    auth_client, dataset_fixture, organization, workspace
):
    from model_hub.serializers.experiments import EvalMetricEntrySerializer

    created, _ = _external(auth_client, dataset_fixture["dataset"])
    refused = EvalMetricEntrySerializer(
        data={"template_id": created["template_id"], "name": "x", "config": {}}
    )
    assert not refused.is_valid()
    assert "external" in str(refused.errors["template_id"]).lower()

    # Positive control: an ordinary template passes the same validation.
    ordinary = EvalTemplate.no_workspace_objects.create(
        name=f"ordinary-{uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
        owner="user",
        eval_type="code",
    )
    accepted = EvalMetricEntrySerializer(
        data={"template_id": str(ordinary.id), "name": "x", "config": {}}
    )
    assert accepted.is_valid(), accepted.errors


@pytest.mark.django_db
def test_experiment_rerun_cells_refuses_an_external_metric(
    auth_client, dataset_fixture, organization, workspace, user
):
    from unittest.mock import patch

    dataset = dataset_fixture["dataset"]
    _, metric = _external(auth_client, dataset)
    snapshot = Dataset.no_workspace_objects.create(
        name="external-guard-snapshot",
        organization=organization,
        workspace=workspace,
        user=user,
        column_order=[],
        column_config={},
    )
    experiment = _experiment(dataset, _column(dataset), snapshot=snapshot)
    columns_before = _column_count(dataset, snapshot)

    with (
        patch("tfc.temporal.experiments.start_rerun_cells_v2_workflow") as cells,
        patch("tfc.temporal.experiments.start_experiment_v2_workflow") as v2,
    ):
        response = auth_client.post(
            reverse(
                "experiments-v2-rerun-cells",
                kwargs={"experiment_id": str(experiment.id)},
            ),
            {"user_eval_metric_ids": [str(metric.id)]},
            format="json",
        )

    _assert_refused(response)
    cells.assert_not_called()
    v2.assert_not_called()
    _assert_untouched(metric)
    assert _column_count(dataset, snapshot) == columns_before


@pytest.mark.django_db
def test_optimisation_create_and_update_refuse_an_external_metric(
    auth_client, dataset_fixture, monkeypatch
):
    from model_hub.models.develop_optimisation import OptimizationDataset

    dataset = dataset_fixture["dataset"]
    _, metric = _external(auth_client, dataset)
    column = _column(dataset)
    created_columns = []
    monkeypatch.setattr(
        "model_hub.views.develop_optimisation.DevelopOptimizer.create_column",
        lambda self: created_columns.append(self),
    )
    existing = OptimizationDataset.no_workspace_objects.create(
        name="existing-optimisation",
        optimize_type="PROMPT_TEMPLATE",
        dataset=dataset,
        column=column,
        messages=[{"role": "user", "content": "Answer {{input}}"}],
        model_config={"model_name": "gpt-4o-mini"},
        user_eval_template_mapping={},
        prompt_name="Prompt",
    )
    columns_before = _column_count(dataset)

    created = auth_client.post(
        reverse("optimisation_create"),
        {
            "name": "optimisation-with-external",
            "dataset_id": str(dataset.id),
            "column_id": str(column.id),
            "messages": [{"role": "user", "content": "Answer {{input}}"}],
            "user_eval_template_ids": [str(metric.id)],
            "model_config": {"model_name": "gpt-4o-mini", "temperature": 0},
            "optimize_type": "PROMPT_TEMPLATE",
            "user_eval_template_mapping": {},
            "prompt_name": "Prompt",
        },
        format="json",
    )
    updated = auth_client.put(
        reverse("optimisation_update", kwargs={"pk": existing.id}),
        {"user_eval_template_ids": [str(metric.id)]},
        format="json",
    )

    _assert_refused(created)
    _assert_refused(updated)
    assert created_columns == []
    assert not OptimizationDataset.no_workspace_objects.filter(
        name="optimisation-with-external"
    ).exists()
    assert not existing.user_eval_template_ids.exists()
    _assert_untouched(metric)
    assert _column_count(dataset) == columns_before


@pytest.mark.django_db
def test_dataset_optimization_run_refuses_an_external_metric(
    auth_client, dataset_fixture
):
    from unittest.mock import patch

    from model_hub.models.optimize_dataset import OptimizeDataset

    dataset = dataset_fixture["dataset"]
    _, metric = _external(auth_client, dataset)
    column = _column(dataset)

    with patch(
        "tfc.temporal.dataset_optimization.client.start_dataset_optimization_workflow"
    ) as workflow:
        response = auth_client.post(
            "/model-hub/dataset-optimization/",
            {
                "name": "optimization-run-with-external",
                "column_id": str(column.id),
                "optimizer_algorithm": OptimizeDataset.OptimizerAlgorithm.RANDOM_SEARCH,
                "optimizer_config": {"num_variations": 1},
                "user_eval_template_ids": [str(metric.id)],
            },
            format="json",
        )

    _assert_refused(response)
    workflow.assert_not_called()
    assert not OptimizeDataset.objects.filter(
        name="optimization-run-with-external"
    ).exists()
    _assert_untouched(metric)


@pytest.mark.django_db
def test_evaluate_rows_refuses_an_external_metric_and_the_task_skips_it(
    auth_client, dataset_fixture
):
    from unittest.mock import patch

    from model_hub.models.develop_dataset import Row
    from model_hub.views.develop_dataset import run_evaluation_task

    dataset = dataset_fixture["dataset"]
    _, metric = _external(auth_client, dataset)
    row = Row.no_workspace_objects.create(dataset=dataset, order=0)

    with patch(
        "model_hub.views.develop_dataset.run_evaluation_task.apply_async"
    ) as queued:
        response = auth_client.post(
            reverse("evaluate-rows"),
            {"user_eval_metric_ids": [str(metric.id)], "row_ids": [str(row.id)]},
            format="json",
        )
    _assert_refused(response)
    queued.assert_not_called()
    _assert_untouched(metric)

    # Backstop: the task itself never runs, or writes a status to, an
    # external metric, whoever queued it.
    with patch("model_hub.views.develop_dataset.EvaluationRunner") as runner:
        run_evaluation_task._original_func(
            {"metric_ids": [str(metric.id)], "row_ids": [str(row.id)]}
        )
    runner.assert_not_called()
    _assert_untouched(metric)


@pytest.mark.django_db
def test_feedback_actions_refuse_an_external_metric(
    auth_client, dataset_fixture, organization, workspace, user
):
    from unittest.mock import patch

    from model_hub.models.evals_metric import Feedback

    dataset = dataset_fixture["dataset"]
    _, metric = _external(auth_client, dataset)
    column = _column(dataset, name="score-column")
    feedback = Feedback.no_workspace_objects.create(
        source="dataset",
        source_id=str(column.id),
        value="1",
        user=user,
        organization=organization,
        workspace=workspace,
    )

    with patch(
        "model_hub.views.develop_dataset.run_evaluation_task.apply_async"
    ) as queued:
        for action_type in ("recalculate_row", "recalculate_dataset", "retune"):
            response = auth_client.post(
                "/model-hub/feedback/submit-feedback/",
                {
                    "action_type": action_type,
                    "feedback_id": str(feedback.id),
                    "user_eval_metric_id": str(metric.id),
                },
                format="json",
            )
            _assert_refused(response)
    queued.assert_not_called()
    _assert_untouched(metric)
    feedback.refresh_from_db()
    assert feedback.user_eval_metric_id is None
    assert feedback.action_type is None


@pytest.mark.django_db
def test_eval_groups_never_take_an_external_template(
    auth_client, dataset_fixture, organization, workspace
):
    from model_hub.services.eval_group import get_accessible_eval_templates_by_ids

    created, _ = _external(auth_client, dataset_fixture["dataset"])
    ordinary = EvalTemplate.no_workspace_objects.create(
        name=f"group-ordinary-{uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
        owner="user",
        eval_type="code",
    )
    # Positive control: the same scope accepts an ordinary user template.
    assert [
        template.id
        for template in get_accessible_eval_templates_by_ids(
            [ordinary.id], organization, workspace
        )
    ] == [ordinary.id]
    with pytest.raises(ValueError):
        get_accessible_eval_templates_by_ids(
            [created["template_id"]], organization, workspace
        )


@pytest.mark.django_db
def test_eval_worker_skips_an_external_metric_left_in_an_executing_status(
    auth_client, dataset_fixture, organization, workspace, user
):
    from unittest.mock import patch

    from model_hub.tasks.user_evaluation import execute_evaluation

    dataset = dataset_fixture["dataset"]
    _, metric = _external(auth_client, dataset)
    # A bulk .update() skips the model guard; simulate an unaudited path.
    UserEvalMetric.no_workspace_objects.filter(id=metric.id).update(
        status=StatusType.NOT_STARTED.value
    )
    # Positive control: an ordinary metric in the same state is picked up, so
    # the worker query really sees this dataset's metrics.
    ordinary = UserEvalMetric.no_workspace_objects.create(
        name="worker-ordinary",
        organization=organization,
        workspace=workspace,
        template=EvalTemplate.no_workspace_objects.create(
            name=f"worker-ordinary-{uuid.uuid4().hex[:8]}",
            organization=organization,
            workspace=workspace,
            owner="user",
            eval_type="code",
        ),
        dataset=dataset,
        user=user,
        status=StatusType.NOT_STARTED.value,
    )
    # Call the raw function: the temporal_activity wrapper runs its own
    # close_old_connections() (tfc/temporal/drop_in/decorator.py), which closes
    # the test transaction's connection ("the connection is closed").
    with (
        patch("model_hub.tasks.user_evaluation.close_old_connections"),
        patch("model_hub.tasks.user_evaluation.process_evaluation_single_task") as task,
    ):
        execute_evaluation._original_func()
    queued = [call.kwargs["args"][0]["eval_id"] for call in task.apply_async.mock_calls]
    assert str(ordinary.id) in queued
    assert str(metric.id) not in queued
    metric.refresh_from_db()
    # Not picked up, so not moved to Running (and never stuck there).
    assert metric.status == StatusType.NOT_STARTED.value


@pytest.mark.django_db
def test_service_error_types_map_to_distinct_statuses(
    auth_client, dataset_fixture, organization, workspace, user
):
    """Review S4: invalid is 400, a vanished dataset is 404, a clash is 409."""
    from model_hub.services.external_metrics import (
        ExternalMetricConflict,
        ExternalMetricDatasetNotFound,
        ExternalMetricDefinition,
        ExternalMetricInvalid,
        create_or_reuse_external_metric,
    )

    dataset = dataset_fixture["dataset"]
    good = ExternalMetricDefinition(
        name="svc", output_type="pass_fail", source_hash=_hash("svc")
    )
    with pytest.raises(ExternalMetricInvalid):
        create_or_reuse_external_metric(
            dataset=dataset,
            definition=ExternalMetricDefinition(
                name="svc", output_type="choices", source_hash=_hash("svc")
            ),
            user=user,
        )

    hosted = EvalTemplate.no_workspace_objects.create(
        name=f"svc-hosted-{uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
        owner="user",
        eval_type="code",
    )
    UserEvalMetric.no_workspace_objects.create(
        name="svc-taken",
        organization=organization,
        workspace=workspace,
        template=hosted,
        dataset=dataset,
        user=user,
    )
    with pytest.raises(ExternalMetricConflict):
        create_or_reuse_external_metric(
            dataset=dataset,
            definition=ExternalMetricDefinition(
                name="svc-taken", output_type="pass_fail", source_hash=_hash("svc")
            ),
            user=user,
        )

    # The dataset is deleted between the view's scope check and the lock.
    Dataset.no_workspace_objects.filter(id=dataset.id).update(deleted=True)
    with pytest.raises(ExternalMetricDatasetNotFound):
        create_or_reuse_external_metric(dataset=dataset, definition=good, user=user)
