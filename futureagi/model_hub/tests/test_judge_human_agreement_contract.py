"""Regression contract: no inferred majority, scale conversion or vocabulary mapping."""

from types import SimpleNamespace

import pytest

from model_hub.models.annotation_queues import AnnotationQueueLabel
from model_hub.models.score import Score
from model_hub.tests.test_judge_human_agreement_integration import (
    _make_custom_eval_config,
    _make_eval_row,
    _make_eval_template,
    _make_label,
    _make_project,
    _make_queue,
    _make_queue_item,
    _make_span,
    _make_trace,
    _make_user,
)
from model_hub.utils import annotation_queue_helpers as helpers
from model_hub.utils.annotation_queue_helpers import (
    _calculate_judge_human_agreement,
    _majority_value,
    _normalize_eval_output,
    _normalize_human_score_value,
)


@pytest.mark.parametrize("votes", [["a", "a", "b", "c"], ["a", "a", "b", "c", "d"]])
def test_plurality_is_not_majority(votes):
    assert _majority_value(votes) is None


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.1, 80, True])
def test_percentage_requires_finite_unit_interval(value):
    assert _normalize_eval_output({"output_float": value}, "percentage") is None
    assert (
        _normalize_human_score_value({"value": value}, "numeric", "percentage") is None
    )


@pytest.mark.parametrize(
    "output,settings,choices",
    [
        ("percentage", {"min": 0, "max": 100}, []),
        ("percentage", {}, []),
        ("pass_fail", {"options": [{"label": "yes"}, {"label": "no"}]}, []),
        (
            "deterministic",
            {"options": [{"label": "positive"}, {"label": "negative"}]},
            ["good", "bad"],
        ),
    ],
)
def test_incompatible_declared_scales_are_not_comparable(output, settings, choices):
    config = SimpleNamespace(
        config={}, eval_template=SimpleNamespace(choices=choices, multi_choice=False)
    )
    assert not helpers._matching_label_scale(settings, output, config)


@pytest.mark.django_db
@pytest.mark.parametrize("maximum,expected", [(1, 1.0), (100, None)])
def test_real_database_scale_controls_denominator(
    organization, workspace, maximum, expected
):
    project = _make_project(organization, workspace)
    trace = _make_trace(project)
    span = _make_span(project, trace)
    template = _make_eval_template(
        organization, workspace, output_type_normalized="percentage"
    )
    config = _make_custom_eval_config(project, template)
    queue = _make_queue(organization, workspace, project, custom_eval_config=config)
    item = _make_queue_item(queue, span, organization)
    label = _make_label(
        organization,
        workspace,
        type="numeric",
        settings={
            "min": 0,
            "max": maximum,
            "step_size": 0.01,
            "display_type": "slider",
        },
    )
    _make_eval_row(span, trace, config, output_float=0.8)
    Score.objects.create(
        queue_item=item, label=label, organization=organization, value={"value": 0.8}
    )
    result = _calculate_judge_human_agreement(queue)
    assert result["overall_agreement"] == expected
    assert result["total_comparisons"] == (1 if maximum == 1 else 0)
    assert result["labels"][str(label.id)]["comparable"] is (maximum == 1)


@pytest.mark.django_db
@pytest.mark.parametrize("state", ["projectless", "deleted", "workspace"])
def test_unavailable_evaluator_links_rejected(organization, workspace, user, state):
    from accounts.models.workspace import Workspace
    from model_hub.serializers.annotation_queues import AnnotationQueueSerializer

    project = _make_project(organization, workspace)
    template = _make_eval_template(organization, workspace)
    config = _make_custom_eval_config(project, template)
    queue = _make_queue(organization, workspace, project)
    if state == "projectless":
        queue.project = None
        queue.save()
    elif state == "deleted":
        config.deleted = True
        config.save()
    else:
        other = Workspace.no_workspace_objects.create(
            name="Other review workspace",
            organization=organization,
            is_default=False,
            created_by=user,
        )
        Project = type(project)
        Project.no_workspace_objects.filter(pk=project.pk).update(workspace=other)
    request = SimpleNamespace(
        user=user, organization=organization, workspace=workspace, data={}
    )
    serializer = AnnotationQueueSerializer(
        queue,
        data={"custom_eval_config": str(config.id)},
        partial=True,
        context={"request": request},
    )
    assert not serializer.is_valid()
    assert "custom_eval_config" in serializer.errors


@pytest.mark.django_db
@pytest.mark.parametrize("through_api", [False, True], ids=["helper", "api"])
@pytest.mark.parametrize(
    "choices,multi_choice,expected",
    [
        (["blue", "red"], False, 1.0),
        ([], False, None),
        (["green", "red"], False, None),
        (["blue", "red"], True, None),
    ],
    ids=["matching", "missing-choices", "different-vocabulary", "different-mode"],
)
def test_nullable_config_uses_declared_template_metadata(
    organization,
    workspace,
    user,
    auth_client,
    through_api,
    choices,
    multi_choice,
    expected,
):
    project = _make_project(organization, workspace)
    trace = _make_trace(project)
    span = _make_span(project, trace)
    template = _make_eval_template(
        organization,
        workspace,
        output_type_normalized="deterministic",
        choices=choices,
        multi_choice=multi_choice,
    )
    config = _make_custom_eval_config(project, template, config=None)
    config.refresh_from_db()
    assert config.config is None
    queue = _make_queue(
        organization, workspace, project, custom_eval_config=config, created_by=user
    )
    item = _make_queue_item(queue, span, organization)
    label = _make_label(
        organization,
        workspace,
        settings={
            "options": [{"label": "blue"}, {"label": "red"}],
            "multi_choice": False,
            "rule_prompt": "",
            "auto_annotate": False,
            "strategy": None,
        },
    )
    AnnotationQueueLabel.objects.create(queue=queue, label=label)
    _make_eval_row(span, trace, config, output_str_list=["blue"])
    Score.objects.create(
        queue_item=item,
        label=label,
        organization=organization,
        annotator=user,
        value={"selected": ["blue"]},
    )
    if through_api:
        response = auth_client.get(
            f"/model-hub/annotation-queues/{queue.id}/agreement/"
        )
        assert response.status_code == 200, response.data
        result = response.data["result"]["judge_vs_human"]
    else:
        result = _calculate_judge_human_agreement(queue)
    assert result["overall_agreement"] == expected
    assert result["total_comparisons"] == (1 if expected is not None else 0)
    assert result["labels"][str(label.id)]["comparable"] is (expected is not None)


@pytest.mark.django_db
@pytest.mark.parametrize("json_output", [False, True])
@pytest.mark.parametrize(
    "multi_choice,judge,votes,expected",
    [
        (True, ["alpha", "Beta"], [["Alpha", "beta"]], 1.0),
        (False, ["alpha"], [["Alpha"]], 1.0),
        (True, ["alpha", "Beta"], [["Alpha"]], 0.0),
        (
            True,
            ["alpha", "Beta"],
            [["Alpha", "beta"], ["Beta", "alpha"], ["beta"]],
            1.0,
        ),
        (True, ["alpha", "Beta"], [["Alpha", "beta"], ["beta"]], None),
    ],
)
def test_categorical_casefold_before_sorting_through_agreement_api(
    organization,
    workspace,
    user,
    auth_client,
    json_output,
    multi_choice,
    judge,
    votes,
    expected,
):
    import json

    project = _make_project(organization, workspace)
    trace = _make_trace(project)
    span = _make_span(project, trace)
    template = _make_eval_template(
        organization,
        workspace,
        output_type_normalized="deterministic",
        choices=["alpha", "Beta"],
        multi_choice=multi_choice,
    )
    config = _make_custom_eval_config(project, template)
    queue = _make_queue(organization, workspace, project, created_by=user)
    item = _make_queue_item(queue, span, organization)
    label = _make_label(
        organization,
        workspace,
        settings={
            "options": [{"label": "Alpha"}, {"label": "beta"}],
            "multi_choice": multi_choice,
            "auto_annotate": False,
            "rule_prompt": "",
            "strategy": None,
        },
    )
    AnnotationQueueLabel.objects.create(queue=queue, label=label)
    output = (
        {"output_str": json.dumps({"choices": judge})}
        if json_output
        else {"output_str_list": judge}
    )
    _make_eval_row(span, trace, config, status="completed", **output)
    for index, selection in enumerate(votes):
        Score.objects.create(
            queue_item=item,
            label=label,
            organization=organization,
            annotator=user if index == 0 else _make_user(organization),
            value={"selected": selection},
        )
    url = f"/model-hub/annotation-queues/{queue.pk}/agreement/"
    before = auth_client.get(url)
    assert before.status_code == 200, before.data
    human_only = before.data["result"]
    assert human_only.pop("judge_vs_human") is None
    if len(votes) > 1:
        # Human-human scoring remains case-sensitive, even when judge votes pool.
        assert human_only["overall_agreement"] == 0.0
        assert all(
            pair["agreement_pct"] == 0.0 for pair in human_only["annotator_pairs"]
        )

    queue.custom_eval_config = config
    queue.save(update_fields=["custom_eval_config"])
    response = auth_client.get(url)
    assert response.status_code == 200, response.data
    result = response.data["result"].pop("judge_vs_human")
    assert response.data["result"] == human_only
    assert result["labels"][str(label.pk)]["comparable"] is True
    assert result["total_comparisons"] == (0 if expected is None else 1)
    assert result["overall_agreement"] == expected
