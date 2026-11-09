"""Dataset eval selections retain saved-version identity and tenant isolation."""

import pytest

from model_hub.models.develop_dataset import Row
from model_hub.models.evals_metric import EvalTemplateVersion, UserEvalMetric
from model_hub.tests.test_dsx_l4_experiment_eval_pin import (
    _second_tenant,
    _template,
    _world,
)


@pytest.fixture
def pin_case(organization, workspace, user):
    world = _world(organization, workspace, user, "dataset-pin")
    template, versions = _template(organization, workspace, "dataset-pin")
    return world, template, versions


def body(world, template, name="pinned-eval", **fields):
    return {
        "name": name,
        "template_id": str(template.id),
        "model": "gpt-4o-mini",
        "run": False,
        "config": {"mapping": {"output": str(world["column"].id)}, "config": {}},
        **fields,
    }


def add_url(world):
    return f"/model-hub/develops/{world['dataset'].id}/add_user_eval/"


def edit_url(world, metric):
    return (
        f"/model-hub/develops/{world['dataset'].id}/edit_and_run_user_eval/{metric.id}/"
    )


def create(client, world, template, **fields):
    response = client.post(
        add_url(world), body(world, template, **fields), format="json"
    )
    assert response.status_code == 200, response.content
    return UserEvalMetric.no_workspace_objects.get(dataset=world["dataset"])


@pytest.mark.django_db
@pytest.mark.parametrize("selection", ["v1", "latest", None])
def test_create_retains_the_selected_saved_version(auth_client, pin_case, selection):
    world, template, (v1, v2) = pin_case
    value = str(v1.id) if selection == "v1" else selection
    # Latest is the newest version even if an older version is the default.
    EvalTemplateVersion.objects.filter(eval_template=template).update(is_default=False)
    EvalTemplateVersion.objects.filter(id=v1.id).update(is_default=True)
    metric = create(auth_client, world, template, pinned_version_id=value)
    expected = {"v1": v1.id, "latest": v2.id, None: None}[selection]
    assert metric.pinned_version_id == expected
    assert EvalTemplateVersion.objects.filter(eval_template=template).count() == 2
    response = auth_client.get(
        f"/model-hub/develops/{world['dataset'].id}/get_evals_list/",
        {"eval_type": "user"},
    )
    assert response.status_code == 200, response.content
    item = next(
        row for row in response.json()["result"]["evals"] if row["id"] == str(metric.id)
    )
    assert item["pinned_version_id"] == (str(expected) if expected else None)


@pytest.mark.django_db
@pytest.mark.parametrize("selection", ["v1", "latest", None])
def test_update_selects_without_creating_an_unrequested_version(
    auth_client, pin_case, selection
):
    world, template, (v1, v2) = pin_case
    metric = create(auth_client, world, template)
    # Seed an existing pin independently of the create contract being tested.
    metric.pinned_version = v2
    metric.save(update_fields=["pinned_version"])
    value = str(v1.id) if selection == "v1" else selection
    response = auth_client.post(
        edit_url(world, metric),
        body(world, template, pinned_version_id=value),
        format="json",
    )
    assert response.status_code == 200, response.content
    metric.refresh_from_db()
    assert (
        metric.pinned_version_id
        == {"v1": v1.id, "latest": v2.id, None: None}[selection]
    )
    assert EvalTemplateVersion.objects.filter(eval_template=template).count() == 2


@pytest.mark.django_db
@pytest.mark.parametrize("operation", ["create", "update"])
@pytest.mark.parametrize("invalid", ["other-template", "deleted", "malformed"])
def test_invalid_selection_changes_neither_bindings_nor_versions(
    auth_client, pin_case, organization, workspace, operation, invalid
):
    world, template, (v1, _v2) = pin_case
    if invalid == "other-template":
        _other, (selected, _) = _template(organization, workspace, "other")
        value = str(selected.id)
    elif invalid == "deleted":
        EvalTemplateVersion.objects.filter(id=v1.id).update(deleted=True)
        value = str(v1.id)
    else:
        value = "not-a-version"
    metric = create(auth_client, world, template) if operation == "update" else None
    before = list(
        UserEvalMetric.no_workspace_objects.filter(dataset=world["dataset"]).values()
    )
    count = EvalTemplateVersion.objects.filter(eval_template=template).count()
    response = auth_client.post(
        edit_url(world, metric) if metric else add_url(world),
        body(world, template, pinned_version_id=value),
        format="json",
    )
    assert response.status_code == 400, response.content
    assert (
        list(
            UserEvalMetric.no_workspace_objects.filter(
                dataset=world["dataset"]
            ).values()
        )
        == before
    )
    assert EvalTemplateVersion.objects.filter(eval_template=template).count() == count


@pytest.mark.django_db
def test_foreign_version_and_dataset_are_refused_without_writes(pin_case):
    world_a, template_a, (version_a, _) = pin_case
    org_b, workspace_b, user_b, client_b = _second_tenant("Dataset pin B")
    try:
        world_b = _world(org_b, workspace_b, user_b, "dataset-pin-b")
        template_b, _ = _template(org_b, workspace_b, "dataset-pin-b")
        metric_b = create(client_b, world_b, template_b)
        for path, payload, statuses in [
            (add_url(world_a), body(world_a, template_a), (403, 404)),
            (
                add_url(world_b),
                body(world_b, template_a, name="foreign-template"),
                (403, 404),
            ),
            (
                add_url(world_b),
                body(
                    world_b,
                    template_b,
                    name="foreign-version",
                    pinned_version_id=str(version_a.id),
                ),
                (400,),
            ),
            (
                edit_url(world_b, metric_b),
                body(world_b, template_b, pinned_version_id=str(version_a.id)),
                (400,),
            ),
        ]:
            response = client_b.post(path, payload, format="json")
            assert response.status_code in statuses, response.content
        metric_b.refresh_from_db()
        assert metric_b.pinned_version_id is None
        assert (
            UserEvalMetric.no_workspace_objects.filter(
                dataset=world_b["dataset"]
            ).count()
            == 1
        )
        assert not UserEvalMetric.no_workspace_objects.filter(
            dataset=world_a["dataset"]
        ).exists()
    finally:
        client_b.stop_workspace_injection()


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["error-config", "cell-result"])
def test_selected_version_uses_its_saved_choices_not_the_live_template(
    pin_case, settings, path
):
    from model_hub.views.eval_runner import EvaluationRunner

    # Historical execution is the dataset.evalVersions ON state; with every
    # switch OFF a pinned binding keeps the live choices
    # (test_eval_live_choices_after_v1.py, the D19-2 OFF-state test).
    settings.DSX_EVAL_SAVED_CHOICES_ENABLED = True
    world, template, (v1, _v2) = pin_case
    template.config = {"eval_type_id": "CustomPromptEvaluator", "output": "choices"}
    template.choices = ["new-choice"]
    template.choice_scores = {"new-choice": 0.9}
    template.save()
    # A historical version has different choices, independently of the live definition.
    v1.config_snapshot = {
        "eval_type_id": "CustomPromptEvaluator",
        "output": "choices",
        "choices": ["saved-choice"],
        "choice_scores": {"saved-choice": 0.2},
    }
    v1.choice_scores = {"saved-choice": 0.2}
    v1.save(update_fields=["config_snapshot", "choice_scores"])
    metric = UserEvalMetric.no_workspace_objects.create(
        name="saved-choices",
        dataset=world["dataset"],
        template=template,
        organization=template.organization,
        workspace=template.workspace,
        pinned_version=v1,
        config={"mapping": {"output": str(world["column"].id)}},
    )
    # Exercise the production config builder, with no evaluator or network call.
    runner = EvaluationRunner.__new__(EvaluationRunner)
    runner.eval_template = template
    runner.user_eval_metric = metric
    runner.user_eval_metric_id = metric.id
    runner._resolved_version = EvalTemplateVersion.objects.resolve_for_metric(metric)
    runner.version_number = runner._resolved_version.version_number
    runner.futureagi_eval = False
    if path == "error-config":
        config = runner._prepare_eval_config({}, model="turing_large")
        assert config["choices"] == ["saved-choice"]
        assert config["choice_scores"] == {"saved-choice": 0.2}
    else:
        row = Row.no_workspace_objects.get(dataset=world["dataset"])
        value = runner.format_output(
            {"output": "choices", "data": "saved-choice"}, row=row
        )
        assert value == {"score": 0.2, "choice": "saved-choice"}
