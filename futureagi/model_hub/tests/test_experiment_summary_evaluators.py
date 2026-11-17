"""Run-level evaluators consume the whole sealed population exactly once."""

import uuid
from unittest.mock import Mock

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.models.organization import Organization
from model_hub.models.dataset_versions import DatasetVersion
from model_hub.models.develop_dataset import Dataset
from model_hub.models.evals_metric import EvalTemplate, EvalTemplateVersion
from model_hub.models.experiment_summary import ExperimentSummaryResult
from model_hub.models.experiments import (
    ExperimentRun,
    ExperimentRunItem,
    ExperimentsTable,
)
from model_hub.services import experiment_summary as service
from model_hub.services.experiment_run_writes import _digest, finalize_run

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("urlconf_loaded_without_workspace_context"),
]


@pytest.mark.parametrize(
    "view_name,method,success_status,serializer_name",
    [
        ("SummaryBindingView", "get", 200, "SummaryBindingsResponseSerializer"),
        ("SummaryBindingView", "post", 201, "SummaryBindingResponseSerializer"),
        ("SummaryScoresView", "get", 200, "SummaryScoresResponseSerializer"),
        ("SummaryScoresView", "post", 202, "SummaryScoreResponseSerializer"),
    ],
)
def test_summary_endpoints_document_typed_errors(
    view_name, method, success_status, serializer_name
):
    from model_hub.serializers import experiment_summary as serializers
    from model_hub.serializers.contracts import ModelHubErrorResponseSerializer
    from model_hub.views import experiment_summary as views

    handler = getattr(getattr(views, view_name), method)
    assert handler._swagger_auto_schema["responses"] == {
        success_status: getattr(serializers, serializer_name),
        400: ModelHubErrorResponseSerializer,
        401: ModelHubErrorResponseSerializer,
        403: ModelHubErrorResponseSerializer,
        404: ModelHubErrorResponseSerializer,
        409: ModelHubErrorResponseSerializer,
        500: ModelHubErrorResponseSerializer,
    }


@pytest.fixture
def summary_fixture(organization, workspace, user, monkeypatch):
    dataset = Dataset.no_workspace_objects.create(
        name="Summary cases",
        organization=organization,
        workspace=workspace,
        user=user,
        column_order=[],
        column_config={},
    )
    experiment = ExperimentsTable.no_workspace_objects.create(
        name="Summary experiment",
        dataset=dataset,
        user=user,
    )
    version = DatasetVersion.no_workspace_objects.create(dataset=dataset)
    identities = [
        {"example_id": str(uuid.UUID(int=index)), "trial_number": 1}
        for index in range(1, 4)
    ]
    run = ExperimentRun.no_workspace_objects.create(
        experiment=experiment,
        dataset_version=version,
        status="completed",
        completed_at=timezone.now(),
        metadata={
            "_futureagi_run_write": {
                "plan": {
                    "identities": identities,
                    "plan_hash": _digest(identities),
                }
            }
        },
    )
    for identity, output in zip(identities, [True, True, False], strict=True):
        ExperimentRunItem.no_workspace_objects.create(
            run=run,
            **identity,
            status="completed",
            input={"text": "case"},
            expected=True,
            output=output,
            score_refs=[{"status": "assessed", "value": int(output)}],
        )
    template = EvalTemplate.no_workspace_objects.create(
        name="aggregate_correctness",
        owner="user",
        eval_type="code",
        template_type="single",
        visible_ui=True,
        organization=organization,
        workspace=workspace,
        config={"eval_type_id": "CustomCodeEval", "code": "original"},
        output_type_normalized="percentage",
    )
    pinned = EvalTemplateVersion.objects.create_version(
        eval_template=template,
        config_snapshot=template.config,
        user=user,
        organization=organization,
        workspace=workspace,
    )
    binding = service.create_binding(
        experiment=experiment,
        template=template,
        version=pinned,
        name="Whole-run accuracy",
        mapping={"rows": "input.rows"},
    )
    dispatch = Mock()
    monkeypatch.setattr(service, "_dispatch", dispatch)
    return experiment, run, template, pinned, binding, dispatch


def scores_url(experiment, run):
    return reverse(
        "experiment-summary-scores",
        kwargs={
            "experiment_id": experiment.id,
            "run_id": run.id,
        },
    )


def bindings_url(experiment):
    return reverse(
        "experiment-summary-evaluators",
        kwargs={
            "experiment_id": experiment.id,
        },
    )


def test_whole_run_aggregate_and_replayed_delivery(summary_fixture, monkeypatch):
    _, run, _, version, binding, _ = summary_fixture
    calls = []

    def evaluate(result, rows):
        calls.append(rows)
        return {
            "score": sum(row["output"] == row["expected"] for row in rows) / 3,
            "reason": "Two of three correct",
            "metadata": {"n": 3},
        }

    monkeypatch.setattr(service, "_evaluate", evaluate)
    result = service.request_summary(run=run, binding=binding)
    service.process_summary_result(result.id)
    service.process_summary_result(result.id)
    again = service.request_summary(run=run, binding=binding, retry_failed=True)
    result.refresh_from_db()
    assert again.id == result.id
    assert ExperimentSummaryResult.objects.count() == 1
    assert len(calls) == 1
    assert result.status == "completed"
    assert result.score == 2 / 3
    assert result.reason == "Two of three correct"
    assert result.metadata == {"n": 3}
    assert result.version_id == version.id
    assert result.attempt == 1
    assert [row["output"] for row in calls[0]] == [True, True, False]
    assert [row["expected"] for row in calls[0]] == [True, True, True]
    assert calls[0][0]["scores"] == [{"status": "assessed", "value": 1}]
    assert calls[0][0]["input"] == {"text": "case"}


def test_summary_api_requests_workspace_neutral_urlconf(request):
    assert "urlconf_loaded_without_workspace_context" in request.fixturenames


def test_real_runner_formats_aggregate_and_reuses_result(summary_fixture, monkeypatch):
    from types import SimpleNamespace

    from evaluations.engine import instance
    from model_hub.models.choices import ModelChoices
    from model_hub.services.external_run_evaluations import StoredEvaluationRunner
    from model_hub.views import eval_runner

    experiment, run, template, _, _, _ = summary_fixture
    fields = ["rows", "outputs", "expected", "scores", "run"]
    template.eval_type = "llm"
    template.model = ModelChoices.TURING_LARGE.value
    template.config = {
        "eval_type_id": "CustomPromptEvaluator",
        "output": "score",
        "required_keys": fields,
        "rule_prompt": "Compare {{ rows }} with {{ outputs }}, {{ expected }}, "
        "{{ scores }} and {{ run }}.",
    }
    template.save(update_fields=["eval_type", "model", "config"])
    version = EvalTemplateVersion.objects.create_version(
        eval_template=template,
        config_snapshot=template.config,
        model=template.model,
        organization=experiment.dataset.organization,
        workspace=experiment.dataset.workspace,
    )
    binding = service.create_binding(
        experiment=experiment,
        template=template,
        version=version,
        name="Aggregate prompt",
        mapping={key: f"input.{key}" for key in fields},
    )
    rows = [
        {
            "item_id": str(item.id),
            "example_id": str(item.example_id),
            "trial_number": 1,
            "status": "completed",
            "input": {"text": "case"},
            "expected": True,
            "output": output,
            "scores": [{"status": "assessed", "value": int(output)}],
        }
        for item, output in zip(
            ExperimentRunItem.no_workspace_objects.filter(run=run).order_by(
                "example_id"
            ),
            [True, True, False],
            strict=True,
        )
    ]
    expected_kwargs = {
        "rows": rows,
        "outputs": [True, True, False],
        "expected": [True, True, True],
        "scores": [[{"status": "assessed", "value": value}] for value in [1, 1, 0]],
        "run": {"id": str(run.id), "status": "completed", "row_count": 3},
        "few_shots": [],
        "required_keys": fields,
        "optional_keys": fields,
    }
    invoke = Mock(
        return_value=SimpleNamespace(
            eval_results=[
                {
                    "metrics": [{"id": "accuracy", "value": 2 / 3}],
                    "reason": "Two of three correct",
                    "metadata": {"n": 3, "correct": 2},
                }
            ]
        )
    )
    evaluator = SimpleNamespace(run=invoke, cost={}, token_usage={})
    build = Mock(return_value=(evaluator, None))
    monkeypatch.setattr(instance, "create_eval_instance", build)
    # Only external side effects are isolated. Mapping, validation, the summary
    # adapter, StoredEvaluationRunner.evaluate and both formatters stay real.
    monkeypatch.setattr(eval_runner, "log_and_deduct_cost_for_api_request", None)
    emit = Mock()
    monkeypatch.setattr(StoredEvaluationRunner, "_emit_cell_usage", emit)
    EvalTemplate.no_workspace_objects.filter(pk=template.id).update(
        config={"eval_type_id": "CustomPromptEvaluator", "rule_prompt": "later"},
    )

    result = service.request_summary(run=run, binding=binding)
    assert service.process_summary_result(result.id) == {
        "id": str(result.id),
        "status": "completed",
    }
    service.process_summary_result(result.id)
    again = service.request_summary(run=run, binding=binding, retry_failed=True)
    result.refresh_from_db()
    invoke.assert_called_once_with(**expected_kwargs)
    assert build.call_count == 1
    built = build.call_args.kwargs
    assert built["eval_template"].config == binding.definition["template"]["config"]
    assert built["version_number"] == version.version_number
    assert built["organization_id"] == str(experiment.dataset.organization_id)
    assert built["workspace_id"] == str(experiment.dataset.workspace_id)
    assert emit.call_count == 1
    assert again.id == result.id
    assert ExperimentSummaryResult.objects.filter(run=run, binding=binding).count() == 1
    assert result.status == "completed"
    assert result.score == 2 / 3
    assert result.reason == "Two of three correct"
    assert result.metadata == {"n": 3, "correct": 2}
    assert result.version_id == version.id
    assert result.attempt == 1


def test_failure_is_sanitized_and_retry_is_explicit(summary_fixture, monkeypatch):
    _, run, _, _, binding, _ = summary_fixture
    evaluate = Mock(side_effect=RuntimeError("private provider diagnostic"))
    monkeypatch.setattr(service, "_evaluate", evaluate)
    result = service.request_summary(run=run, binding=binding)
    service.process_summary_result(result.id)
    result.refresh_from_db()
    assert result.status == "failed"
    assert result.score is None
    assert result.reason == ""
    assert result.error_code == "evaluation_failed"
    service.request_summary(run=run, binding=binding)
    service.process_summary_result(result.id)
    assert evaluate.call_count == 1
    evaluate.side_effect = None
    evaluate.return_value = {"score": 0, "reason": "No passes", "metadata": {}}
    service.request_summary(run=run, binding=binding, retry_failed=True)
    service.process_summary_result(result.id)
    result.refresh_from_db()
    assert result.status == "completed"
    assert result.score == 0
    assert result.attempt == 2
    assert result.error_code == ""


@pytest.mark.parametrize("score", [float("nan"), float("inf"), "0.5", None])
def test_invalid_output_is_not_a_score(summary_fixture, monkeypatch, score):
    _, run, _, _, binding, _ = summary_fixture
    monkeypatch.setattr(service, "_evaluate", lambda *args: {"score": score})
    result = service.request_summary(run=run, binding=binding)
    service.process_summary_result(result.id)
    result.refresh_from_db()
    assert result.status == "failed"
    assert result.score is None
    assert result.error_code == "invalid_output"


def test_missing_population_does_not_invoke_evaluator(summary_fixture, monkeypatch):
    _, run, _, _, binding, _ = summary_fixture
    ExperimentRunItem.no_workspace_objects.filter(run=run).first().delete()
    evaluate = Mock()
    monkeypatch.setattr(service, "_evaluate", evaluate)
    result = service.request_summary(run=run, binding=binding)
    service.process_summary_result(result.id)
    result.refresh_from_db()
    assert evaluate.call_count == 0
    assert result.status == "failed"
    assert result.error_code == "incomplete_population"
    assert result.score is None


def test_running_claim_cannot_be_replayed(summary_fixture, monkeypatch):
    _, run, _, _, binding, _ = summary_fixture
    result = service.request_summary(run=run, binding=binding)
    ExperimentSummaryResult.objects.filter(pk=result.id).update(
        status="running",
        started_at=timezone.now(),
        attempt=1,
    )
    evaluate = Mock()
    monkeypatch.setattr(service, "_evaluate", evaluate)
    service.request_summary(run=run, binding=binding, retry_failed=True)
    service.process_summary_result(result.id)
    assert evaluate.call_count == 0
    result.refresh_from_db()
    assert result.status == "running"
    assert result.attempt == 1


def test_live_template_changes_do_not_move_binding(summary_fixture, monkeypatch):
    _, run, template, version, binding, _ = summary_fixture
    template.config = {"eval_type_id": "CustomCodeEval", "code": "changed"}
    template.save(update_fields=["config"])
    EvalTemplateVersion.objects.create_version(
        eval_template=template,
        config_snapshot=template.config,
    )
    binding.refresh_from_db()
    assert binding.definition["template"]["config"]["code"] == "original"
    assert binding.version_id == version.id
    captured = []

    def evaluate(result, rows):
        captured.append(result.binding.definition["pin"])
        return {"score": 1, "reason": "Pinned", "metadata": {}}

    monkeypatch.setattr(service, "_evaluate", evaluate)
    result = service.request_summary(run=run, binding=binding)
    service.process_summary_result(result.id)
    assert captured[0]["evaluator_version_id"] == str(version.id)
    assert captured[0]["config_snapshot"]["code"] == "original"


def test_terminal_run_schedules_after_commit(
    summary_fixture,
    django_capture_on_commit_callbacks,
):
    _, run, _, _, binding, dispatch = summary_fixture
    run.status = "running"
    run.completed_at = None
    run.save(update_fields=["status", "completed_at"])
    with django_capture_on_commit_callbacks(execute=True):
        finalize_run(
            run_id=run.id,
            organization_id=run.dataset_version.dataset.organization_id,
            workspace_id=run.dataset_version.dataset.workspace_id,
        )
        assert dispatch.call_count == 0
    result = ExperimentSummaryResult.objects.get(run=run, binding=binding)
    assert result.status == "pending"
    dispatch.assert_called_once_with(result.id)


@pytest.mark.django_db(transaction=True)
def test_unpublished_commit_can_be_recovered_through_api(
    auth_client,
    summary_fixture,
    monkeypatch,
):
    from django.db import connection, transaction

    from model_hub.tasks.user_evaluation import process_evaluation_single_task

    experiment, run, _, _, binding, _ = summary_fixture
    # Restore the actual dispatcher; intercept only the task transport.
    monkeypatch.undo()
    publish = Mock()
    monkeypatch.setattr(process_evaluation_single_task, "apply_async", publish)
    run.status = "running"
    run.completed_at = None
    run.save(update_fields=["status", "completed_at"])
    callbacks = []
    # Model a process exiting after its transaction commits but before its
    # registered callback executes. This test has no enclosing test transaction.
    with monkeypatch.context() as capture:
        capture.setattr(transaction, "on_commit", callbacks.append)
        finalize_run(
            run_id=run.id,
            organization_id=experiment.dataset.organization_id,
            workspace_id=experiment.dataset.workspace_id,
        )
    assert connection.in_atomic_block is False
    assert len(callbacks) == 1
    assert publish.call_count == 0
    result = ExperimentSummaryResult.objects.get(run=run, binding=binding)
    assert (result.status, result.dispatched, result.error_code) == (
        "pending",
        False,
        "",
    )
    url = scores_url(experiment, run)
    response = auth_client.get(url)
    assert response.status_code == 200
    (score,) = response.json()["result"]["scores"]
    assert score["publication_pending"] is True
    assert score["error_code"] == ""
    for _ in range(2):
        response = auth_client.post(
            url,
            {
                "binding_id": str(binding.id),
                "retry_failed": False,
            },
            format="json",
        )
        assert response.status_code == 202
        assert response.json()["result"]["id"] == str(result.id)
    # Even a delayed original callback cannot publish a second task.
    callbacks[0]()
    publish.assert_called_once_with(
        args=({"type": "summary_run", "result_id": str(result.id)},),
        task_id=f"summary-evaluation-{result.id}-0",
        dispatch_timeout_seconds=10,
    )
    response = auth_client.get(url)
    assert response.status_code == 200
    assert response.json()["result"]["scores"][0]["publication_pending"] is False
    evaluate = Mock(return_value={"score": 1, "reason": "Recovered", "metadata": {}})
    monkeypatch.setattr(service, "_evaluate", evaluate)
    service.process_summary_result(result.id)
    service.process_summary_result(result.id)
    result.refresh_from_db()
    assert evaluate.call_count == 1
    assert ExperimentSummaryResult.objects.count() == 1
    assert (result.status, result.score, result.reason, result.attempt) == (
        "completed",
        1,
        "Recovered",
        1,
    )


@pytest.mark.parametrize("mode", ["interruption", "recording_failure"])
@pytest.mark.parametrize("complete", [False, True])
def test_http_failure_seals_schedule_whole_population(
    summary_fixture,
    monkeypatch,
    django_capture_on_commit_callbacks,
    mode,
    complete,
):
    from django.db import transaction

    from model_hub.services import http_run_recording as http

    experiment, run, _, _, binding, dispatch = summary_fixture
    run.status = "running"
    run.completed_at = None
    run.save(update_fields=["status", "completed_at"])
    if not complete:
        ExperimentRunItem.no_workspace_objects.filter(run=run).first().delete()
    # Selection is unrelated to sealing; keep the actual DB writes and callback.
    monkeypatch.setattr(
        http,
        "_recording_runs",
        lambda *args: ExperimentRun.no_workspace_objects.select_for_update().filter(
            pk=run.id,
            completed_at__isnull=True,
        ),
    )
    evaluate = Mock(return_value={"score": 2 / 3, "reason": "All rows", "metadata": {}})
    monkeypatch.setattr(service, "_evaluate", evaluate)
    with django_capture_on_commit_callbacks(execute=True):
        with transaction.atomic():
            if mode == "interruption":
                http._end_run(experiment, run, stopped=False)
            else:
                with http.canonical_http_write(
                    experiment.id,
                    "http-admission",
                    operation="finish",
                    terminal=True,
                ):
                    raise RuntimeError("recording unavailable")
            assert dispatch.call_count == 0
    run.refresh_from_db()
    assert run.status == "failed"
    assert run.completed_at is not None
    if mode == "recording_failure":
        assert run.metadata["_futureagi_run_write"]["http_recording_error"] == {
            "operation": "finish",
            "reason": "canonical_operation_failed",
            "error_type": "RuntimeError",
        }
    result = ExperimentSummaryResult.objects.get(run=run, binding=binding)
    dispatch.assert_called_once_with(result.id)
    service.process_summary_result(result.id)
    service.process_summary_result(result.id)
    result.refresh_from_db()
    assert result.status == ("completed" if complete else "failed")
    assert result.score == (2 / 3 if complete else None)
    assert result.error_code == ("" if complete else "incomplete_population")
    assert evaluate.call_count == int(complete)
    assert result.attempt == 1


def test_nonterminal_http_recording_failure_does_not_schedule(
    summary_fixture,
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    from model_hub.services import http_run_recording as http

    experiment, run, _, _, _, dispatch = summary_fixture
    run.status = "running"
    run.completed_at = None
    run.save(update_fields=["status", "completed_at"])
    monkeypatch.setattr(http, "_recording_runs", lambda *args: [run])
    with django_capture_on_commit_callbacks(execute=True):
        with http.canonical_http_write(
            experiment.id,
            "http-admission",
            operation="receipt",
            terminal=False,
        ):
            raise RuntimeError("recording unavailable")
    run.refresh_from_db()
    assert (run.status, run.completed_at) == ("running", None)
    assert ExperimentSummaryResult.objects.count() == 0
    assert dispatch.call_count == 0


def test_read_and_rerun_api_are_idempotent(auth_client, summary_fixture, monkeypatch):
    experiment, run, _, version, binding, _ = summary_fixture
    monkeypatch.setattr(
        service,
        "_evaluate",
        lambda *args: {
            "score": 0.75,
            "reason": "Threshold",
            "metadata": {"threshold": 0.7},
        },
    )
    url = scores_url(experiment, run)
    response = auth_client.post(url, {"binding_id": str(binding.id)}, format="json")
    assert response.status_code == 202
    result_id = response.json()["result"]["id"]
    service.process_summary_result(result_id)
    response = auth_client.get(url)
    assert response.status_code == 200
    (score,) = response.json()["result"]["scores"]
    assert score["id"] == result_id
    assert score["status"] == "completed"
    assert score["score"] == 0.75
    assert score["version_id"] == str(version.id)
    assert score["reason"] == "Threshold"
    replay = auth_client.post(url, {"binding_id": str(binding.id)}, format="json")
    assert replay.status_code == 202
    assert replay.json()["result"]["id"] == result_id


def test_foreign_dataset_denies_binding_and_result_reads(auth_client, summary_fixture):
    experiment, run, template, version, binding, _ = summary_fixture
    foreign = Organization.objects.create(name="Other tenant")
    Dataset.no_workspace_objects.filter(pk=experiment.dataset_id).update(
        organization=foreign,
    )
    assert auth_client.get(bindings_url(experiment)).status_code == 404
    assert auth_client.get(scores_url(experiment, run)).status_code == 404
    response = auth_client.post(
        scores_url(experiment, run),
        {
            "binding_id": str(binding.id),
        },
        format="json",
    )
    assert response.status_code == 404
    assert ExperimentSummaryResult.objects.count() == 0


def test_other_experiment_run_is_not_accepted(auth_client, summary_fixture):
    experiment, run, _, _, binding, _ = summary_fixture
    other = ExperimentsTable.no_workspace_objects.create(
        name="Other experiment",
        dataset=experiment.dataset,
    )
    ExperimentRun.no_workspace_objects.filter(pk=run.id).update(experiment=other)
    response = auth_client.post(
        scores_url(experiment, run),
        {
            "binding_id": str(binding.id),
        },
        format="json",
    )
    assert response.status_code == 404
    assert ExperimentSummaryResult.objects.count() == 0


def test_foreign_template_and_mismatched_version_are_refused(
    auth_client,
    summary_fixture,
):
    experiment, _, template, version, _, _ = summary_fixture
    request = {
        "name": "New summary",
        "template_id": str(template.id),
        "version_id": str(uuid.uuid4()),
    }
    assert (
        auth_client.post(
            bindings_url(experiment),
            request,
            format="json",
        ).status_code
        == 404
    )
    foreign = Organization.objects.create(name="Other evaluator tenant")
    EvalTemplate.no_workspace_objects.filter(pk=template.id).update(
        organization=foreign,
    )
    request["version_id"] = str(version.id)
    assert (
        auth_client.post(
            bindings_url(experiment),
            request,
            format="json",
        ).status_code
        == 404
    )


def test_shared_runner_gets_one_aggregate_item(summary_fixture, monkeypatch):
    from model_hub.services.external_run_evaluations import StoredEvaluationRunner

    _, run, template, version, binding, _ = summary_fixture
    EvalTemplate.no_workspace_objects.filter(pk=template.id).update(
        config={"eval_type_id": "CustomCodeEval", "code": "later definition"},
    )
    seen = []

    def evaluate(runner, item):
        seen.append(item.input)
        assert runner._resolved_version.id == version.id
        assert runner.eval_template.config["code"] == "original"
        assert runner.source_configs["run_id"] == str(run.id)
        rows = item.input["rows"]
        runner.summary_metadata = {"n": len(rows)}
        return {
            "status": "assessed",
            "value": sum(row["output"] == row["expected"] for row in rows) / 3,
            "explanation": "Aggregate",
        }

    monkeypatch.setattr(StoredEvaluationRunner, "evaluate", evaluate)
    result = service.request_summary(run=run, binding=binding)
    service.process_summary_result(result.id)
    result.refresh_from_db()
    assert len(seen) == 1
    assert seen[0]["outputs"] == [True, True, False]
    assert seen[0]["expected"] == [True, True, True]
    assert seen[0]["run"] == {
        "id": str(run.id),
        "status": "completed",
        "row_count": 3,
    }
    assert result.status == "completed"
    assert result.score == 2 / 3
    assert result.reason == "Aggregate"
    assert result.metadata == {"n": 3}


def test_mutated_version_is_not_executed(summary_fixture, monkeypatch):
    from model_hub.services.external_run_evaluations import StoredEvaluationRunner

    _, run, _, version, binding, _ = summary_fixture
    EvalTemplateVersion.all_objects.filter(pk=version.id).update(
        config_snapshot={"eval_type_id": "CustomCodeEval", "code": "different"},
    )
    evaluate = Mock()
    monkeypatch.setattr(StoredEvaluationRunner, "evaluate", evaluate)
    result = service.request_summary(run=run, binding=binding)
    service.process_summary_result(result.id)
    result.refresh_from_db()
    assert evaluate.call_count == 0
    assert result.status == "failed"
    assert result.score is None
    assert result.error_code == "evaluation_failed"


def test_binding_api_returns_concrete_version_without_definition(
    auth_client,
    summary_fixture,
):
    experiment, _, template, version, _, _ = summary_fixture
    response = auth_client.post(
        bindings_url(experiment),
        {
            "name": "Pinned aggregate",
            "template_id": str(template.id),
            "version_id": str(version.id),
        },
        format="json",
    )
    assert response.status_code == 201
    result = response.json()["result"]
    assert result["version_id"] == str(version.id)
    assert result["version_number"] == version.version_number
    assert result["template_id"] == str(template.id)
    assert result["mapping"] == {"rows": "input.rows"}
    assert "definition" not in result
    assert "config_snapshot" not in result


def test_other_workspace_is_not_visible(auth_client, summary_fixture, user):
    from accounts.models.workspace import Workspace

    experiment, run, _, _, _, _ = summary_fixture
    other = Workspace.objects.create(
        name="Another workspace",
        organization=user.organization,
        created_by=user,
    )
    Dataset.no_workspace_objects.filter(pk=experiment.dataset_id).update(
        workspace=other,
    )
    assert auth_client.get(bindings_url(experiment)).status_code == 404
    assert auth_client.get(scores_url(experiment, run)).status_code == 404


def test_read_only_member_cannot_schedule(
    auth_client,
    summary_fixture,
    user,
    monkeypatch,
):
    experiment, run, _, _, binding, _ = summary_fixture
    monkeypatch.setattr(type(user), "can_write_to_workspace", lambda *args: False)
    assert auth_client.get(scores_url(experiment, run)).status_code == 200
    response = auth_client.post(
        scores_url(experiment, run),
        {
            "binding_id": str(binding.id),
        },
        format="json",
    )
    assert response.status_code == 403
    assert ExperimentSummaryResult.objects.count() == 0


def test_unsealed_run_is_not_evaluated(auth_client, summary_fixture):
    experiment, run, _, _, binding, _ = summary_fixture
    ExperimentRun.no_workspace_objects.filter(pk=run.id).update(completed_at=None)
    response = auth_client.post(
        scores_url(experiment, run),
        {
            "binding_id": str(binding.id),
        },
        format="json",
    )
    assert response.status_code == 400
    assert ExperimentSummaryResult.objects.count() == 0


def test_population_limit_fails_without_sampling(summary_fixture, monkeypatch):
    _, run, _, _, binding, _ = summary_fixture
    monkeypatch.setattr(service, "MAX_ROWS", 2)
    evaluate = Mock()
    monkeypatch.setattr(service, "_evaluate", evaluate)
    result = service.request_summary(run=run, binding=binding)
    service.process_summary_result(result.id)
    result.refresh_from_db()
    assert result.status == "failed"
    assert result.error_code == "population_too_large"
    assert result.score is None
    assert evaluate.call_count == 0


def test_dispatch_failure_remains_pending_and_can_be_republished(
    summary_fixture,
    monkeypatch,
):
    from model_hub.tasks.user_evaluation import process_evaluation_single_task

    _, run, _, _, binding, _ = summary_fixture
    result = service.request_summary(run=run, binding=binding)
    # Restore the real dispatcher, while keeping the transport fully isolated.
    monkeypatch.undo()
    publish = Mock(side_effect=RuntimeError("transport unavailable"))
    monkeypatch.setattr(process_evaluation_single_task, "apply_async", publish)
    service._dispatch(result.id)
    result.refresh_from_db()
    assert result.status == "pending"
    assert result.dispatched is False
    assert result.error_code == "dispatch_failed"
    publish.side_effect = None
    service._dispatch(result.id)
    result.refresh_from_db()
    assert result.dispatched is True
    assert result.status == "pending"
    assert result.error_code == ""
    assert publish.call_count == 2
    assert publish.call_args.kwargs["args"] == (
        {
            "type": "summary_run",
            "result_id": str(result.id),
        },
    )


def test_scope_change_fails_summary_without_blocking_terminal_run(
    summary_fixture,
    django_capture_on_commit_callbacks,
):
    _, run, _, _, binding, dispatch = summary_fixture
    binding.definition["workspace_id"] = str(uuid.uuid4())
    binding.save(update_fields=["definition"])
    run.status = "running"
    run.completed_at = None
    run.save(update_fields=["status", "completed_at"])
    with django_capture_on_commit_callbacks(execute=True):
        finalized = finalize_run(
            run_id=run.id,
            organization_id=run.dataset_version.dataset.organization_id,
            workspace_id=run.dataset_version.dataset.workspace_id,
        )
    assert finalized.status == "completed"
    result = ExperimentSummaryResult.objects.get(run=run, binding=binding)
    dispatch.assert_called_once_with(result.id)
    service.process_summary_result(result.id)
    result.refresh_from_db()
    assert result.status == "failed"
    assert result.error_code == "scope_changed"
    assert result.score is None
