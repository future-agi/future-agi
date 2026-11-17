"""CI verdicts use persisted Results evidence and the real experiment route."""

import uuid

import pytest
from rest_framework.test import APIClient

from accounts.models.organization import Organization
from accounts.models.user import OrgApiKey
from accounts.models.workspace import Workspace, WorkspaceMembership
from model_hub.models.dataset_versions import DatasetVersion, DatasetVersionItem
from model_hub.models.develop_dataset import Dataset
from model_hub.models.experiments import (
    DatasetBaseline,
    ExperimentRun,
    ExperimentRunItem,
    ExperimentsTable,
)
from model_hub.services.experiment_run_creation import create_experiment_run
from model_hub.services.external_metrics import (
    ExternalMetricDefinition,
    create_or_reuse_external_metric,
)
from tfc.constants.levels import Level

pytestmark = pytest.mark.django_db


@pytest.fixture
def ci_runs(organization, workspace, user):
    dataset = Dataset.no_workspace_objects.create(
        name="CI results",
        organization=organization,
        workspace=workspace,
        user=user,
        column_order=[],
        column_config={},
    )
    experiment = ExperimentsTable.no_workspace_objects.create(
        dataset=dataset,
        user=user,
        name="Candidate experiment",
    )
    version = DatasetVersion.no_workspace_objects.create(dataset=dataset)
    examples = [
        DatasetVersionItem.no_workspace_objects.create(
            dataset_version=version,
            input={"case": index},
        )
        for index in range(4)
    ]
    metrics = [
        create_or_reuse_external_metric(
            dataset=dataset,
            user=user,
            definition=ExternalMetricDefinition(
                name=name,
                output_type="numeric",
                source_hash=letter * 64,
                higher_is_better=True,
                pass_threshold=0.5,
            ),
        ).metric
        for name, letter in (("quality", "a"), ("accuracy", "b"))
    ]
    runs = []
    for values in ([0.0, 1.0, 0.5, None], [1.0, 0.0, 0.5, 1.0]):
        run = create_experiment_run(
            run_id=uuid.uuid4(),
            dataset_version=version,
            created_by=user,
            task_kind="external",
            experiment=experiment,
            evaluator_metrics=metrics,
            expected_items=[(example.example_id, 1) for example in examples],
        )
        ExperimentRun.no_workspace_objects.filter(pk=run.pk).update(status="completed")
        run.refresh_from_db()
        for example, value in zip(examples, values, strict=False):
            ExperimentRunItem.no_workspace_objects.create(
                run=run,
                example_id=example.example_id,
                trial_number=1,
                status="completed",
                score_refs=[
                    {
                        "metric_id": pin["metric_id"],
                        "evaluator_version_id": pin["evaluator_version_id"],
                        "status": "not_assessed" if value is None else "assessed",
                        "value": value,
                    }
                    for pin in run.evaluator_versions
                ],
            )
        runs.append(run)
    baseline, candidate = runs
    pin = DatasetBaseline.no_workspace_objects.create(
        dataset=dataset,
        run=baseline,
        pinned_by=user,
    )
    return experiment, baseline, candidate, metrics, pin


def _url(experiment):
    return f"/model-hub/experiments/v2/{experiment.id}/ci-summary/"


def _expected(world, *, passing=False, threshold=0):
    experiment, baseline, candidate, metrics, _ = world
    regressed = 0 if passing else 2
    return {
        "experiment_id": str(experiment.id),
        "run_id": str(candidate.id),
        "baseline_run_id": str(baseline.id),
        "evaluators": {
            str(metric.id): {
                "improved": 1,
                "regressed": 0 if passing else 1,
                "unchanged": 2 if passing else 1,
                "not_comparable": 1,
                "mean": 0.875 if passing else 0.625,
                "baseline_mean": 0.5,
            }
            for metric in metrics
        },
        "regressed_total": regressed,
        "passed": regressed <= threshold,
    }


def _evidence(world):
    experiment, baseline, candidate, _, pin = world
    return (
        list(
            ExperimentRun.no_workspace_objects.filter(
                pk__in=[baseline.pk, candidate.pk]
            )
            .order_by("id")
            .values()
        ),
        list(
            ExperimentRunItem.no_workspace_objects.filter(
                run_id__in=[baseline.pk, candidate.pk]
            )
            .order_by("id")
            .values()
        ),
        list(ExperimentsTable.no_workspace_objects.filter(pk=experiment.pk).values()),
        list(DatasetBaseline.no_workspace_objects.filter(pk=pin.pk).values()),
    )


@pytest.mark.parametrize("passing", [False, True])
def test_verdict_matches_all_evaluator_counts(auth_client, ci_runs, passing):
    experiment, _, candidate, _, _ = ci_runs
    if passing:
        item = ExperimentRunItem.no_workspace_objects.get(
            run=candidate,
            score_refs__0__value=0.0,
        )
        for score in item.score_refs:
            score["value"] = 1.0
        item.save(update_fields=["score_refs"])
    before = _evidence(ci_runs)
    response = auth_client.get(_url(experiment), {"run_id": str(candidate.id)})
    assert response.status_code == 200
    assert response.json() == _expected(ci_runs, passing=passing)
    assert _evidence(ci_runs) == before


@pytest.mark.parametrize("threshold,passed", [(0, False), (1, False), (2, True)])
def test_threshold_is_inclusive_across_evaluators(
    auth_client, ci_runs, threshold, passed
):
    experiment, _, candidate, _, _ = ci_runs
    response = auth_client.get(
        _url(experiment),
        {"run_id": str(candidate.id), "max_regressed": threshold},
    )
    assert response.status_code == 200
    assert response.json() == _expected(ci_runs, threshold=threshold)
    assert response.json()["passed"] is passed


def test_missing_baseline_has_exact_conflict_message(auth_client, ci_runs):
    experiment, _, candidate, _, pin = ci_runs
    DatasetBaseline.no_workspace_objects.filter(pk=pin.pk).update(deleted=True)
    response = auth_client.get(_url(experiment), {"run_id": str(candidate.id)})
    assert response.status_code == 409
    assert response.json()["detail"] == "No baseline run is pinned for this dataset."


def test_run_from_another_experiment_is_not_found(auth_client, ci_runs):
    experiment, _, candidate, _, _ = ci_runs
    other = ExperimentsTable.no_workspace_objects.create(
        dataset=experiment.dataset,
        user=experiment.user,
        name="Other experiment",
    )
    ExperimentRun.no_workspace_objects.filter(pk=candidate.pk).update(experiment=other)
    response = auth_client.get(_url(experiment), {"run_id": str(candidate.id)})
    assert response.status_code == 404
    assert response.json()["detail"] == "Run not found in this experiment."


@pytest.mark.parametrize("scope", ["organization", "workspace", "deleted"])
def test_experiment_outside_request_scope_is_not_found(auth_client, ci_runs, scope):
    experiment, _, candidate, _, _ = ci_runs
    assert auth_client.get(
        _url(experiment), {"run_id": str(candidate.id)}
    ).json() == _expected(ci_runs)
    if scope == "deleted":
        Dataset.no_workspace_objects.filter(pk=experiment.dataset_id).update(
            deleted=True
        )
    else:
        organization = (
            Organization.objects.create(name="Other organization")
            if scope == "organization"
            else experiment.dataset.organization
        )
        workspace = Workspace.no_workspace_objects.create(
            name="Other workspace",
            organization=organization,
            is_default=False,
            created_by=experiment.user,
        )
        Dataset.no_workspace_objects.filter(pk=experiment.dataset_id).update(
            organization=organization,
            workspace=workspace,
        )
    response = auth_client.get(_url(experiment), {"run_id": str(candidate.id)})
    assert response.status_code == 404
    assert response.json()["detail"] == "Experiment not found"


def test_api_key_authenticates_on_the_registered_route(ci_runs, user, workspace):
    experiment, _, candidate, _, _ = ci_runs
    key = OrgApiKey.no_workspace_objects.create(
        name="CI read fixture",
        user=user,
        organization=workspace.organization,
        workspace=workspace,
        type="user",
    )
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=key.api_key, HTTP_X_SECRET_KEY=key.secret_key)
    response = client.get(_url(experiment), {"run_id": str(candidate.id)})
    assert response.status_code == 200
    assert response.json() == _expected(ci_runs)


@pytest.mark.parametrize("extra", [{"max_regressed": -1}, {"max_regressed": "x"}])
def test_invalid_threshold_is_rejected(auth_client, ci_runs, extra):
    experiment, _, candidate, _, _ = ci_runs
    response = auth_client.get(
        _url(experiment),
        {"run_id": str(candidate.id), **extra},
    )
    assert response.status_code == 400


def test_baseline_itself_is_not_a_candidate(auth_client, ci_runs):
    experiment, baseline, _, _, _ = ci_runs
    response = auth_client.get(_url(experiment), {"run_id": str(baseline.id)})
    assert response.status_code == 400
    assert response.json()["detail"] == "Run must differ from the baseline run."


def test_unassessed_scores_are_not_zero_filled(auth_client, ci_runs):
    experiment, _, candidate, metrics, _ = ci_runs
    for item in ExperimentRunItem.no_workspace_objects.filter(run=candidate):
        for score in item.score_refs:
            score.update(status="not_assessed", value=None)
        item.save(update_fields=["score_refs"])
    response = auth_client.get(_url(experiment), {"run_id": str(candidate.id)})
    assert response.status_code == 200
    expected = _expected(ci_runs)
    expected.update(regressed_total=0, passed=True)
    expected["evaluators"] = {
        str(metric.id): {
            "improved": 0,
            "regressed": 0,
            "unchanged": 0,
            "not_comparable": 4,
            "mean": None,
            "baseline_mean": 0.5,
        }
        for metric in metrics
    }
    assert response.json() == expected


@pytest.mark.parametrize("query", [{}, {"run_id": "invalid"}])
def test_a_valid_run_identifier_is_required(auth_client, ci_runs, query):
    assert auth_client.get(_url(ci_runs[0]), query).status_code == 400


def test_anonymous_requests_are_rejected(ci_runs):
    experiment, _, candidate, _, _ = ci_runs
    response = APIClient().get(_url(experiment), {"run_id": str(candidate.id)})
    assert response.status_code == 401


def test_summary_route_is_read_only(auth_client, ci_runs):
    experiment, _, candidate, _, _ = ci_runs
    before = _evidence(ci_runs)
    response = auth_client.post(
        _url(experiment),
        {"run_id": str(candidate.id)},
        format="json",
    )
    assert response.status_code == 405
    assert _evidence(ci_runs) == before


def _comment_url(experiment):
    return f"{_url(experiment)}comment/"


def _comment_expected(world, *, threshold=0, counts=(2, 2, 2, 2), rows=None):
    experiment, baseline, candidate, metrics, _ = world
    improved, regressed, unchanged, not_comparable = counts
    if rows is None:
        item = ExperimentRunItem.no_workspace_objects.get(
            run=candidate, score_refs__0__value=0.0
        )
        rows = [
            f"| `{item.example_id}` | `{metric_id}` | 1 | 0 |"
            for metric_id in sorted(str(metric.id) for metric in metrics)
        ]
    passed = regressed <= threshold
    verdict = "PASS" if passed else "FAIL"
    path = f"/dashboard/develop/experiment/{experiment.id}/data"
    lines = [
        f"## Evaluation CI: {verdict}",
        f"Regressed comparisons: **{regressed}** (allowed: **{threshold}**).",
        f"Run: `{candidate.id}`",
        f"Baseline: `{baseline.id}`",
        "",
        f"Improved: **{improved}** | Regressed: **{regressed}** | "
        f"Unchanged: **{unchanged}** | Not comparable: **{not_comparable}**",
        "Counts are row/evaluator comparisons, not unique rows.",
        "",
    ]
    if rows:
        lines += [
            f"### Regressed rows (showing {len(rows)} of {regressed})",
            "| Row ID | Evaluator ID | Baseline | Run |",
            "| --- | --- | ---: | ---: |",
            *rows,
            "",
            "Sorted by absolute score change, then row ID and evaluator ID.",
            "Scores retain comparison precision; categorical scores are pass rates.",
        ]
    else:
        lines += ["No regressed row/evaluator comparisons."]
    lines += ["", f"[View experiment Results]({path})", ""]
    return {"body": "\n".join(lines), "passed": passed, "results_path": path}


@pytest.mark.parametrize("threshold", [0, 2])
def test_comment_pins_exact_pass_and_fail_markdown(auth_client, ci_runs, threshold):
    experiment, _, candidate, _, _ = ci_runs
    before = _evidence(ci_runs)
    query = {"run_id": str(candidate.id), "max_regressed": threshold}
    response = auth_client.get(_comment_url(experiment), query)
    assert response.status_code == 200
    assert response.json() == _comment_expected(ci_runs, threshold=threshold)
    assert auth_client.get(_comment_url(experiment), query).json() == response.json()
    assert _evidence(ci_runs) == before


def test_comment_pins_no_regressions_message(auth_client, ci_runs):
    experiment, _, candidate, _, _ = ci_runs
    item = ExperimentRunItem.no_workspace_objects.get(
        run=candidate, score_refs__0__value=0.0
    )
    for score in item.score_refs:
        score["value"] = 1.0
    item.save(update_fields=["score_refs"])
    response = auth_client.get(_comment_url(experiment), {"run_id": str(candidate.id)})
    assert response.status_code == 200
    assert response.json() == _comment_expected(ci_runs, counts=(2, 0, 4, 2), rows=[])


@pytest.mark.parametrize("top_n", [None, 1, 3, 20])
def test_comment_orders_regressions_and_caps_only_the_table(
    auth_client, ci_runs, top_n
):
    experiment, baseline, candidate, metrics, _ = ci_runs
    items = list(
        ExperimentRunItem.no_workspace_objects.filter(run=candidate).order_by(
            "example_id"
        )
    )
    for item, value in zip(items, [0.5, 0.0, 0.0, None], strict=True):
        base_item = ExperimentRunItem.no_workspace_objects.get(
            run=baseline, example_id=item.example_id
        )
        for score in base_item.score_refs:
            score.update(value=1.0, status="assessed")
        base_item.save(update_fields=["score_refs"])
        for score in item.score_refs:
            score.update(
                value=value, status="assessed" if value is not None else "not_assessed"
            )
        item.save(update_fields=["score_refs"])
    # Largest change first; row UUID then evaluator UUID break ties, not DB order.
    rows = [
        f"| `{item.example_id}` | `{metric_id}` | 1 | {value} |"
        for item, value in [(items[1], "0"), (items[2], "0"), (items[0], "0.5")]
        for metric_id in sorted(str(metric.id) for metric in metrics)
    ]
    response = auth_client.get(
        _comment_url(experiment),
        {
            "run_id": str(candidate.id),
            **({"top_n": top_n} if top_n is not None else {}),
        },
    )
    assert response.status_code == 200
    assert response.json() == _comment_expected(
        ci_runs, counts=(0, 6, 0, 2), rows=rows[: top_n or 5]
    )


def test_comment_missing_baseline_is_explicit(auth_client, ci_runs):
    experiment, baseline, candidate, _, pin = ci_runs
    DatasetBaseline.no_workspace_objects.filter(pk=pin.pk).update(deleted=True)
    response = auth_client.get(_comment_url(experiment), {"run_id": str(candidate.id)})
    assert response.status_code == 409
    assert response.json()["detail"] == "No baseline run is pinned for this dataset."
    # An explicit baseline is reproducible without changing the dataset pin.
    response = auth_client.get(
        _comment_url(experiment),
        {"run_id": str(candidate.id), "baseline_run_id": str(baseline.id)},
    )
    assert response.status_code == 200
    assert response.json() == _comment_expected(ci_runs)
    pin.refresh_from_db()
    assert pin.deleted is True


def test_comment_uses_chosen_baseline_instead_of_current_pin(auth_client, ci_runs):
    experiment, baseline, candidate, _, pin = ci_runs
    DatasetBaseline.no_workspace_objects.filter(pk=pin.pk).update(run=candidate)
    response = auth_client.get(
        _comment_url(experiment),
        {"run_id": str(candidate.id), "baseline_run_id": str(baseline.id)},
    )
    assert response.status_code == 200
    assert response.json() == _comment_expected(ci_runs)
    pin.refresh_from_db()
    assert pin.run_id == candidate.id


@pytest.mark.parametrize("scope", ["organization", "workspace", "deleted"])
def test_comment_rejects_experiments_outside_scope(auth_client, ci_runs, scope):
    experiment, _, candidate, _, _ = ci_runs
    if scope == "deleted":
        Dataset.no_workspace_objects.filter(pk=experiment.dataset_id).update(
            deleted=True
        )
    else:
        organization = (
            Organization.objects.create(name="Other CI organization")
            if scope == "organization"
            else experiment.dataset.organization
        )
        other_workspace = Workspace.no_workspace_objects.create(
            name="Other CI workspace",
            organization=organization,
            is_default=False,
            created_by=experiment.user,
        )
        Dataset.no_workspace_objects.filter(pk=experiment.dataset_id).update(
            organization=organization, workspace=other_workspace
        )
    response = auth_client.get(_comment_url(experiment), {"run_id": str(candidate.id)})
    assert response.status_code == 404
    assert response.json()["detail"] == "Experiment not found"


def test_comment_org_api_key_is_workspace_scoped(ci_runs, user, workspace):
    experiment, _, candidate, _, _ = ci_runs
    other_workspace = Workspace.no_workspace_objects.create(
        name="Other CI key workspace",
        organization=workspace.organization,
        is_default=False,
        created_by=user,
    )
    # A key pinned to a workspace authenticates only for its members, so the
    # 404 below comes from the comment's own workspace scope, not from auth.
    WorkspaceMembership.no_workspace_objects.update_or_create(
        workspace=other_workspace,
        user=user,
        defaults={
            "level": Level.WORKSPACE_MEMBER,
            "role": "workspace_member",
            "is_active": True,
            "deleted": False,
        },
    )
    for key_workspace, expected_status in [(workspace, 200), (other_workspace, 404)]:
        key = OrgApiKey.no_workspace_objects.create(
            name="CI comment fixture",
            user=user,
            organization=workspace.organization,
            workspace=key_workspace,
            type="user",
        )
        client = APIClient()
        client.credentials(HTTP_X_API_KEY=key.api_key, HTTP_X_SECRET_KEY=key.secret_key)
        response = client.get(_comment_url(experiment), {"run_id": str(candidate.id)})
        assert response.status_code == expected_status
        if expected_status == 200:
            assert response.json() == _comment_expected(ci_runs)
        else:
            assert response.json()["detail"] == "Experiment not found"


@pytest.mark.parametrize(
    "extra",
    [
        {"top_n": 0},
        {"top_n": 21},
        {"top_n": "invalid"},
        {"max_regressed": -1},
        {"baseline_run_id": "invalid"},
        {"unknown": "value"},
    ],
)
def test_comment_rejects_invalid_options(auth_client, ci_runs, extra):
    experiment, _, candidate, _, _ = ci_runs
    response = auth_client.get(
        _comment_url(experiment), {"run_id": str(candidate.id), **extra}
    )
    assert response.status_code == 400


@pytest.mark.parametrize("which", [1, 2])
def test_comment_requires_completed_runs(auth_client, ci_runs, which):
    experiment, _, candidate, _, _ = ci_runs
    ExperimentRun.no_workspace_objects.filter(pk=ci_runs[which].pk).update(
        status="running"
    )
    response = auth_client.get(_comment_url(experiment), {"run_id": str(candidate.id)})
    assert response.status_code == 409
    assert response.json()["detail"] == (
        "CI comments require completed candidate and baseline runs."
    )


def test_comment_never_contains_row_inputs_outputs_or_names(auth_client, ci_runs):
    experiment, baseline, candidate, _, _ = ci_runs
    DatasetVersionItem.no_workspace_objects.filter(
        dataset_version=baseline.dataset_version
    ).update(
        input={"private_input": "do-not-publish-input"},
        expected={"private_expected": "do-not-publish-expected"},
    )
    ExperimentRunItem.no_workspace_objects.filter(run=candidate).update(
        output={"private_output": "do-not-publish-output"},
        metadata={"private_metadata": "do-not-publish-metadata"},
    )
    ExperimentsTable.no_workspace_objects.filter(pk=experiment.pk).update(
        name="do-not-publish-experiment-name"
    )
    response = auth_client.get(_comment_url(experiment), {"run_id": str(candidate.id)})
    assert response.status_code == 200
    assert response.json() == _comment_expected(ci_runs)
    assert "do-not-publish" not in response.json()["body"]


def test_comment_refuses_missing_or_same_baseline(auth_client, ci_runs):
    experiment, _, candidate, _, _ = ci_runs
    for baseline, status, detail in [
        (str(uuid.uuid4()), 404, "Run not found on this dataset"),
        (str(candidate.id), 400, "Run must differ from the baseline run."),
    ]:
        response = auth_client.get(
            _comment_url(experiment),
            {"run_id": str(candidate.id), "baseline_run_id": baseline},
        )
        assert response.status_code == status
        assert response.json()["detail"] == detail


def test_comment_route_requires_auth_and_is_read_only(auth_client, ci_runs):
    experiment, _, candidate, _, _ = ci_runs
    query = {"run_id": str(candidate.id)}
    assert APIClient().get(_comment_url(experiment), query).status_code == 401
    before = _evidence(ci_runs)
    response = auth_client.post(_comment_url(experiment), query, format="json")
    assert response.status_code == 405
    assert _evidence(ci_runs) == before


def test_comment_preserves_small_regressions_in_displayed_scores(auth_client, ci_runs):
    experiment, _, candidate, metrics, _ = ci_runs
    item = ExperimentRunItem.no_workspace_objects.get(
        run=candidate, score_refs__0__value=0.0
    )
    for score in item.score_refs:
        score["value"] = 0.9999999
    item.save(update_fields=["score_refs"])
    response = auth_client.get(_comment_url(experiment), {"run_id": str(candidate.id)})
    assert response.status_code == 200
    rows = [
        f"| `{item.example_id}` | `{metric_id}` | 1 | 0.9999999 |"
        for metric_id in sorted(str(metric.id) for metric in metrics)
    ]
    assert response.json() == _comment_expected(ci_runs, rows=rows)
