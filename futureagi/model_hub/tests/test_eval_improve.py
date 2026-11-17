"""EVAL-E2E-083: improve a judge without changing saved definitions or labels."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from model_hub.models.choices import OwnerChoices
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import EvalTemplate, EvalTemplateVersion, UserEvalMetric
from model_hub.models.score import Score
from model_hub.tests.test_eval_agreement import AgreementTenant


@pytest.fixture
def world(organization, workspace, user, auth_client):
    scope = {"organization": organization, "workspace": workspace}
    template = EvalTemplate.no_workspace_objects.create(
        **scope, name="agreement-draft", owner=OwnerChoices.USER.value,
        eval_type="llm", criteria="Check {{output}}.", model="turing_large",
        config={"eval_type_id": "CustomPromptEvaluator", "rule_prompt": "Check {{output}}.",
                "model": "turing_large", "output": "Pass/Fail", "required_keys": ["output"]},
    )
    dataset = Dataset.no_workspace_objects.create(
        **scope, name="labelled examples", column_order=[], column_config={},
    )
    inputs = Column.no_workspace_objects.create(
        dataset=dataset, name="answer", data_type="text", source="OTHERS",
    )
    binding = UserEvalMetric.no_workspace_objects.create(
        **scope, name="judge", dataset=dataset, template=template,
        config={"mapping": {"output": str(inputs.id)}}, model="turing_large",
    )
    output = Column.no_workspace_objects.create(
        dataset=dataset, name="judge", data_type="json", source="evaluation", source_id=str(binding.id),
    )
    label = AnnotationsLabels.no_workspace_objects.create(
        **scope, name="review", type="categorical",
        settings={"options": [{"label": "Pass"}, {"label": "Fail"}], "multi_choice": False,
                  "rule_prompt": "", "auto_annotate": False, "strategy": None},
    )
    rows = []
    for index, human in enumerate(["Pass", "Fail", "Pass", "Fail"]):
        row = Row.no_workspace_objects.create(dataset=dataset, order=index)
        Cell.no_workspace_objects.create(
            dataset=dataset, column=inputs, row=row, value=f"answer-{index}", status="pass",
        )
        Cell.no_workspace_objects.create(
            dataset=dataset, column=output, row=row,
            value=json.dumps({"choice": "Pass"}), status="pass",
        )
        Score.no_workspace_objects.create(
            **scope, dataset_row=row, source_type="dataset_row", label=label,
            annotator=user, score_source="human", value={"selected": [human]},
            notes=f"review-note-{index}",
        )
        rows.append(row)
    return SimpleNamespace(
        **scope, user=user, client=auth_client, template=template, dataset=dataset,
        binding=binding, label=label, rows=rows, inputs=inputs,
        url=f"/model-hub/eval-templates/{template.id}/improve/",
        body={"source_key": f"{binding.id}:{label.id}", "source_template_id": str(template.id),
              "draft_share": 50, "seed": "repeatable"},
    )


@pytest.fixture
def models(monkeypatch):
    generation = Mock()
    generation._get_completion_content.return_value = json.dumps({
        "candidates": [{"kind": "add", "clause": "Reject odd numbered answers."}],
    })
    platform = Mock(return_value=generation)
    monkeypatch.setattr("model_hub.services.model_route.platform_llm", platform)
    monkeypatch.setattr("model_hub.throttles.AIEvalWriterThrottle.allow_request", lambda *args: True)
    calls = []

    def create(**kwargs):
        prompt = kwargs["config"]["rule_prompt"]
        started = False

        def run(**inputs):
            nonlocal started
            # Construction is now a preflight, not a model dispatch.
            if not started:
                calls.append(kwargs)
                started = True
            assert set(inputs) == {"output", "required_keys"}, "Human labels must never reach the scoring judge"
            assert inputs["required_keys"] == ["output"]
            verdict = "Fail" if "Reject odd" in prompt and inputs["output"] in {"answer-1", "answer-3"} else "Pass"
            from agentic_eval.core_evals.fi_utils.evals_result import BatchRunResult

            return BatchRunResult(eval_results=[{"data": {"result": verdict}, "reason": "mocked judge"}])

        return SimpleNamespace(
            run=Mock(side_effect=run), _managed_route_active=lambda: True,
            _managed_route_completion=Mock(),
        ), None

    monkeypatch.setattr("evaluations.engine.instance.create_eval_instance", create)
    return SimpleNamespace(platform=platform, generation=generation, calls=calls)


def post(world, **overrides):
    return world.client.post(world.url, {**world.body, **overrides}, format="json")


def result(response):
    assert response.status_code == 200, response.content
    assert response.json()["status"] is True
    return response.json()["result"]


def stored(world):
    return {
        "template": list(EvalTemplate.no_workspace_objects.filter(pk=world.template.pk).values()),
        "versions": list(EvalTemplateVersion.no_workspace_objects.filter(eval_template=world.template).values()),
        "cells": list(Cell.no_workspace_objects.filter(dataset=world.dataset).order_by("id").values()),
        "scores": list(Score.no_workspace_objects.filter(organization=world.organization).order_by("id").values()),
    }


def test_candidates_are_generated_and_independently_scored_without_writes(world, models):
    before = stored(world)
    data = result(post(world))
    candidate = data["candidates"][0]
    assert candidate["kind"] == "add"
    assert candidate["clause"] == "Reject odd numbered answers."
    assert candidate["agreement"] == {"draft": 1.0, "check": 1.0, "all": 1.0}
    assert data["before"]["stored"] == 0.5
    assert data["before"]["all"] == 0.5
    assert data["current"]["judge_model"] == "turing_large"
    assert candidate["disagreements"] == []
    assert stored(world) == before
    assert models.platform.call_args.kwargs["organization"] == world.organization
    assert models.platform.call_args.kwargs["workspace"] == world.workspace
    assert all(c["organization_id"] == str(world.organization.id) for c in models.calls)
    assert all(c["workspace_id"] == str(world.workspace.id) for c in models.calls)


def test_generation_sees_only_drafting_rows_and_the_split_is_repeatable(world, models):
    first = result(post(world))
    payload = json.loads(models.generation._get_completion_content.call_args.kwargs["messages"][1]["content"])
    assert {r["row_id"] for r in payload["examples"]} == set(first["split"]["draft_rows"])
    assert not set(first["split"]["draft_rows"]) & set(first["split"]["check_rows"])
    assert len(first["split"]["check_rows"]) == 2
    assert len(payload["examples"]) == 2
    assert first["draft_notes"] == 2
    assert first["split"] == result(post(world))["split"]
    assert set(first["split"]["draft_rows"] + first["split"]["check_rows"]) == {str(r.id) for r in world.rows}


def test_selected_edits_return_scores_flips_and_do_not_save_a_version(world, models):
    before = stored(world)
    data = result(post(world, edits=[{"kind": "add", "clause": "Reject odd numbered answers.",
                                       "source": "labels", "from": {"row_labels": []}}]))
    assert data["draft"]["instructions"] == "Check {{output}}.\nReject odd numbered answers."
    assert data["after"] == {"draft": 1.0, "check": 1.0, "all": 1.0}
    assert data["before"]["all"] == 0.5
    assert len(data["verdicts"]) == 4
    assert {f["row_id"] for f in data["flips"]} == {str(world.rows[i].id) for i in (1, 3)}
    assert all(f["toward_reviewer"] for f in data["flips"])
    assert data["disagreements"] == []
    models.platform.assert_not_called()
    assert stored(world) == before


def test_edited_instructions_override_edits_and_report_each_disagreement(world, models):
    data = result(post(world, edits=[{"kind": "add", "clause": "Reject odd numbered answers."}],
                       instructions="An edited {{output}} prompt."))
    assert data["draft"]["instructions"] == "An edited {{output}} prompt."
    assert data["after"]["all"] == 0.5
    assert {p["row_id"] for p in data["disagreements"]} == {str(world.rows[i].id) for i in (1, 3)}
    assert all(p["human"] == "Fail" and p["judge"] == "Pass" for p in data["disagreements"])
    assert data["flips"] == []
    models.platform.assert_not_called()


def test_latest_saved_version_is_used_for_both_current_and_draft(world, models):
    EvalTemplateVersion.no_workspace_objects.create(
        eval_template=world.template, organization=world.organization, workspace=world.workspace,
        version_number=2, criteria="Saved {{output}} instructions.", model="turing_small",
        config_snapshot={**world.template.config, "rule_prompt": "Saved {{output}} instructions."},
    )
    data = result(post(world, edits=[{"kind": "add", "clause": "Reject odd numbered answers."}]))
    assert data["current"] == {"from_saved": True, "version": 2, "judge_model": "turing_small"}
    assert data["draft"]["instructions"].startswith("Saved {{output}} instructions.")
    assert all(c["model"] == "turing_small" for c in models.calls)


@pytest.mark.parametrize("field", ["target", "source_template_id", "source_key", "mapped_column"])
def test_other_organization_identifiers_are_denied_before_model_calls(world, models, field):
    result(post(world))  # Positive control: not a denial-only test of an absent route.
    foreign = AgreementTenant("improve-foreign")
    try:
        models.platform.reset_mock()
        models.calls.clear()
        body = dict(world.body)
        url = world.url
        if field == "target":
            url = f"/model-hub/eval-templates/{foreign.template.id}/improve/"
        elif field == "source_template_id":
            body[field] = str(foreign.template.id)
        elif field == "source_key":
            body[field] = f"{foreign.binding.id}:{foreign.label.id}"
        else:
            world.binding.config = {"mapping": {"output": str(foreign.column.id)}}
            world.binding.save(update_fields=["config"])
        response = world.client.post(url, body, format="json")
        assert response.status_code == 404, response.content
        assert foreign.dataset.name not in str(response.content)
        models.platform.assert_not_called()
        assert models.calls == []
    finally:
        foreign.client.stop_workspace_injection()


def test_same_org_other_workspace_template_is_not_visible(world, models):
    result(post(world))
    foreign = AgreementTenant("improve-workspace")
    try:
        EvalTemplate.no_workspace_objects.filter(pk=foreign.template.pk).update(organization=world.organization)
        models.platform.reset_mock()
        response = world.client.post(
            f"/model-hub/eval-templates/{foreign.template.id}/improve/", world.body, format="json",
        )
        assert response.status_code == 404
        models.platform.assert_not_called()
    finally:
        foreign.client.stop_workspace_injection()


@pytest.mark.parametrize("overrides", [
    {"draft_share": 0}, {"draft_share": 100}, {"draft_share": "NaN"}, {"seed": " "},
    {"source_key": "invalid"}, {"source_template_id": "invalid"},
    {"instructions": " "}, {"edits": [{"kind": "replace", "clause": "x"}]},
    {"edits": [{"kind": "remove", "clause": "not in current prompt"}]},
    {"organization_id": "invented"}, {"edits": []},
])
def test_invalid_request_is_rejected_without_model_calls(world, models, overrides):
    assert post(world, **overrides).status_code == 400
    models.platform.assert_not_called()
    assert models.calls == []


def test_labelled_population_cap_is_checked_before_any_model_call(world, models):
    output = Column.no_workspace_objects.get(dataset=world.dataset, source="evaluation")
    for index in range(4, 101):
        row = Row.no_workspace_objects.create(dataset=world.dataset, order=index)
        Cell.no_workspace_objects.create(
            dataset=world.dataset, row=row, column=output, value="Pass", status="pass",
        )
        Score.no_workspace_objects.create(
            organization=world.organization, workspace=world.workspace, dataset_row=row,
            source_type="dataset_row", label=world.label, annotator=world.user,
            score_source="human", value={"selected": ["Pass"]},
        )
    assert post(world).status_code == 413
    models.platform.assert_not_called()
    assert models.calls == []


def test_dataset_binary_label_spelling_matches_the_live_judge(world, models):
    world.label.settings = {
        **world.label.settings, "options": [{"label": "Passed"}, {"label": "Failed"}],
    }
    world.label.save(update_fields=["settings"])
    for index, row in enumerate(world.rows):
        Score.no_workspace_objects.filter(dataset_row=row).update(
            value={"selected": ["Passed" if index % 2 == 0 else "Failed"]},
        )
        Cell.no_workspace_objects.filter(row=row, column__source="evaluation").update(value="Passed")
    data = result(post(world, instructions="Check {{output}}. Reject odd numbered answers."))
    assert data["after"]["all"] == 1
    assert data["before"]["stored"] == 0.5
    assert {v["after"] for v in data["verdicts"]} == {"Passed", "Failed"}


def test_a_caller_owned_copy_can_use_the_original_templates_label_source(world, models):
    copy = EvalTemplate.no_workspace_objects.create(
        organization=world.organization, workspace=world.workspace, owner=OwnerChoices.USER.value,
        name="aligned-copy", criteria=world.template.criteria, config=world.template.config,
        model=world.template.model, eval_type="llm",
    )
    world.url = f"/model-hub/eval-templates/{copy.id}/improve/"
    data = result(post(world))
    assert data["candidates"][0]["agreement"]["all"] == 1
    assert not EvalTemplateVersion.no_workspace_objects.filter(eval_template=copy).exists()


def test_too_few_labelled_rows_is_not_reported_as_perfect_agreement(world, models):
    Score.no_workspace_objects.filter(organization=world.organization).exclude(dataset_row=world.rows[0]).update(deleted=True)
    assert post(world).status_code == 400
    models.platform.assert_not_called()


def test_failed_stored_cells_are_excluded_from_the_labelled_population(world, models):
    Cell.no_workspace_objects.filter(row=world.rows[0], column__source="evaluation").update(status="error")
    data = result(post(world))
    assert str(world.rows[0].id) not in data["split"]["draft_rows"] + data["split"]["check_rows"]
    assert data["before"]["stored"] == pytest.approx(1 / 3)


@pytest.mark.parametrize("output", ["not JSON", '{"candidates": [{"kind": "add", "clause": ""}]}'])
def test_malformed_generation_is_a_generic_upstream_error(world, models, output):
    models.generation._get_completion_content.return_value = output
    response = post(world)
    assert response.status_code == 502
    assert output not in str(response.content)
    assert models.calls == []


def test_upstream_failure_does_not_leak_provider_details_or_mutate(world, models):
    before = stored(world)
    models.generation._get_completion_content.side_effect = RuntimeError("private-provider-detail")
    response = post(world)
    assert response.status_code == 502
    assert "private-provider-detail" not in str(response.content)
    assert stored(world) == before


def test_judge_failure_is_not_counted_as_a_disagreement(world, models, monkeypatch):
    monkeypatch.setattr("evaluations.engine.instance.create_eval_instance", Mock(side_effect=RuntimeError("judge detail")))
    response = post(world, edits=[{"kind": "add", "clause": "Reject odd numbered answers."}])
    assert response.status_code == 502
    assert "judge detail" not in str(response.content)


def test_agreement_read_offers_improve_without_running_models_or_claiming_training(world, models):
    response = world.client.get(world.url.replace("/improve/", "/agreement/"))
    data = result(response)
    assert data["improvement"] == {
        "mode": "improve", "from_saved": False, "version": None, "judge_model": "turing_large",
    }
    assert "improve_mode" not in data  # The legacy mode also enables training.
    assert data["report"]["n"] == 4
    models.platform.assert_not_called()
    assert models.calls == []


def test_scoring_renders_mapped_values_through_the_real_judge(world, monkeypatch):
    from agentic_eval.core.llm.llm import LLM
    from agentic_eval.core_evals.fi_evals.llm.custom_prompt_evaluator.evaluator import CustomPromptEvaluator

    def init(llm, **kwargs):
        # Only transport setup is replaced: run, _evaluate and rendering stay real.
        llm.model_name = kwargs["model_name"]
        llm.temperature = 0
        llm.max_tokens = 256
        llm.token_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        llm.cost = {"prompt_cost": 0, "completion_cost": 0, "total_cost": 0}
        llm.model_route = object()

    monkeypatch.setattr(LLM, "__init__", init)
    transport = Mock(return_value='{"result": "Pass", "explanation": "test judge"}')
    monkeypatch.setattr(LLM, "call_llm", transport)
    monkeypatch.setattr("model_hub.throttles.AIEvalWriterThrottle.allow_request", lambda *args: True)

    def create(**kwargs):
        return CustomPromptEvaluator(
            rule_prompt=kwargs["config"]["rule_prompt"], model="test-judge", provider="openai",
        ), None

    monkeypatch.setattr("evaluations.engine.instance.create_eval_instance", create)
    result(post(world, instructions="Recheck {{output}}."))
    prompts = [json.dumps(call.kwargs["prompt"]) for call in transport.call_args_list]
    assert len(prompts) == 8
    for index in range(4):
        assert sum(f"answer-{index}" in prompt for prompt in prompts) == 2
    assert all("{{output}}" not in prompt for prompt in prompts)
    assert all("review-note-" not in prompt for prompt in prompts)


@pytest.mark.parametrize("snapshot", [
    {"choices": ["Yes", "No"], "choice_scores": {"Yes": 1, "No": 0}, "multi_choice": True},
    {"choices": [], "choice_scores": None, "multi_choice": False},
    {"choices": None, "choice_scores": None, "multi_choice": None},
])
def test_saved_choice_definition_replaces_live_choices_in_every_scoring_pass(world, models, snapshot):
    world.template.choices = ["A", "B"]
    world.template.choice_scores = {"A": 1, "B": 0}
    world.template.multi_choice = True
    world.template.save(update_fields=["choices", "choice_scores", "multi_choice"])
    EvalTemplateVersion.no_workspace_objects.create(
        eval_template=world.template, organization=world.organization, workspace=world.workspace,
        version_number=2, criteria="Saved {{output}} instructions.", model="turing_large",
        choice_scores={"stale": 1}, config_snapshot={**world.template.config, **snapshot},
    )
    result(post(world, instructions="Draft {{output}} instructions."))
    assert len(models.calls) == 2
    for call in models.calls:
        definition = call["eval_template"]
        assert definition.choices == (snapshot["choices"] or [])
        assert definition.choice_scores == snapshot["choice_scores"]
        assert definition.multi_choice == snapshot["multi_choice"]
    assert models.calls[0]["config"]["rule_prompt"] == "Saved {{output}} instructions."


@pytest.mark.parametrize("overrides,rows", [({}, 7), ({"instructions": "Check {{output}}."}, 13)])
def test_total_scoring_budget_is_refused_before_inputs_or_models(world, models, monkeypatch, overrides, rows):
    from model_hub.services import eval_improve

    report = {"report": {"pairs": [{}] * rows}}
    monkeypatch.setattr(eval_improve, "agreement_report", Mock(return_value=report))
    inputs = Mock(side_effect=AssertionError("Work bound must precede input loading"))
    monkeypatch.setattr(eval_improve, "_inputs", inputs)
    response = post(world, **overrides)
    assert response.status_code == 413
    assert "24" in str(response.content)
    inputs.assert_not_called()
    models.platform.assert_not_called()
    assert models.calls == []


def test_deadline_stops_dispatch_after_generation(world, models, monkeypatch):
    from model_hub.services import eval_improve
    import time

    now = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    score = Mock(side_effect=AssertionError("No scoring after deadline"))
    monkeypatch.setattr(eval_improve, "_score", score)

    def generate(**kwargs):
        now[0] += 31
        return '{"candidates": []}'

    models.generation._get_completion_content.side_effect = generate
    response = post(world)
    assert response.status_code == 504
    assert "time budget" in str(response.content).lower()
    score.assert_not_called()


def test_deadline_stops_before_the_next_row(world, models, monkeypatch):
    from agentic_eval.core_evals.fi_utils.evals_result import BatchRunResult
    import time

    now = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])

    def run(**kwargs):
        now[0] += 31
        return BatchRunResult(eval_results=[{"data": {"result": "Pass"}}])

    judge = SimpleNamespace(
        _managed_route_active=lambda: True, _managed_route_completion=Mock(),
        run=Mock(side_effect=run),
    )
    monkeypatch.setattr("evaluations.engine.instance.create_eval_instance", lambda **kwargs: (judge, None))
    assert post(world, instructions="Check {{output}}.").status_code == 504
    judge.run.assert_called_once()
    models.platform.assert_not_called()


def test_unbounded_generation_transport_is_refused_before_any_dispatch(world, models):
    models.generation._managed_route_active.return_value = False
    response = post(world)
    assert response.status_code == 400
    assert "bounded managed" in str(response.content).lower()
    models.generation._get_completion_content.assert_not_called()
    assert models.calls == []


def test_generation_and_scoring_share_the_transport_deadline(world, models, monkeypatch):
    from agentic_eval.core_evals.fi_utils.evals_result import BatchRunResult
    import time

    now = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    transport = Mock(return_value="ignored")
    models.generation._managed_route_completion = transport

    def generate(**kwargs):
        models.generation._managed_route_completion({})
        now[0] += 2
        return '{"candidates": []}'

    models.generation._get_completion_content.side_effect = generate
    judge = SimpleNamespace(_managed_route_active=lambda: True, _managed_route_completion=transport)

    def run(**kwargs):
        judge._managed_route_completion({})
        now[0] += 1
        return BatchRunResult(eval_results=[{"data": {"result": "Pass"}}])

    judge.run = run
    monkeypatch.setattr("evaluations.engine.instance.create_eval_instance", lambda **kwargs: (judge, None))
    result(post(world))
    assert transport.call_count == 5
    assert [call.kwargs.get("deadline_monotonic") for call in transport.call_args_list] == [130.0] * 5


def test_turing_judge_uses_a_scoped_bounded_route_not_the_legacy_client(world, models, monkeypatch):
    from agentic_eval.core_evals.fi_utils.evals_result import BatchRunResult
    from model_hub.services.model_route import OP_CUSTOM_PROMPT_EVAL

    bound = []
    binding = object()
    bind = Mock(return_value=binding)
    monkeypatch.setattr("model_hub.services.model_route.bind_managed_route", bind)
    judge = SimpleNamespace(
        _is_turing=True, provider="turing", _managed_route_active=lambda: bool(bound),
        _bind_model_route=bound.append, _managed_route_completion=Mock(),
        run=Mock(return_value=BatchRunResult(eval_results=[{"data": {"result": "Pass"}}])),
    )
    monkeypatch.setattr("evaluations.engine.instance.create_eval_instance", lambda **kwargs: (judge, None))
    result(post(world, instructions="Check {{output}}."))
    bind.assert_called_once_with(
        organization_id=str(world.organization.id), workspace_id=str(world.workspace.id),
        model_name="turing_large", provider="turing", operation=OP_CUSTOM_PROMPT_EVAL,
    )
    assert bound == [binding]
    assert judge._is_turing is False
    assert judge.run.call_count == 8
    models.platform.assert_not_called()


def test_unbounded_judge_transport_is_refused_before_generation(world, models, monkeypatch):
    judge = SimpleNamespace(_managed_route_active=lambda: False, run=Mock())
    monkeypatch.setattr("evaluations.engine.instance.create_eval_instance", lambda **kwargs: (judge, None))
    response = post(world)
    assert response.status_code == 400
    assert "bounded managed" in str(response.content).lower()
    models.platform.assert_not_called()
    judge.run.assert_not_called()


def test_improve_requires_authentication(world, api_client):
    response = api_client.post(world.url, world.body, format="json")
    assert response.status_code in (401, 403)
