"""Real create/admit/worker bridge; workflow/provider/evaluator transports replaced.

Requires the supplied P01/P02 overlays and the pinned P03 fix-r2 catalog.
No test manually binds a canonical run. These sources are not executed receipts.
"""
import json
import uuid
from unittest.mock import patch

import pytest
from django.db import transaction

from model_hub.models.choices import CellStatus
from model_hub.models.develop_dataset import Cell, Column, Row
from model_hub.models.experiments import (
    ExperimentPromptConfig,
    ExperimentRunItem,
    ExperimentsTable,
)
from model_hub.models.native_experiment_runs import NativeRunBinding
from model_hub.services.experiment_run_writes import RunWriteError
from model_hub.services.experiment_submit import (
    admit_submission,
    stage_submission,
    state_of,
)
from model_hub.services.native_run_projection import project_run
from model_hub.tests import test_experiment_append_variant as fixtures
from tfc.temporal.experiments import activities

# Worker helpers close old connections and async transport hops need committed rows.
# A rollback-only django_db transaction is not a worker connection lifecycle.
pytestmark = pytest.mark.django_db(transaction=True)
tenants = fixtures.tenants
START = fixtures.START
PROVIDER = "model_hub.views.experiment_runner.RunPrompt.litellm_response"


class _NativeFixtureEvaluator:
    """Registry identity for the explicitly replaced evaluator engine boundary."""


@pytest.fixture(autouse=True)
def native_fixture_evaluator():
    """Cover HTTP column creation as well as later worker evaluation.

    Transport tests import this fixture explicitly: importing create() alone does
    not make this module's autouse fixtures available to their test module.
    Nested native_eval_transport contexts still supply test-specific failures
    and call-count assertions; registry lookup and unknown-type rejection stay real.
    """
    with native_eval_transport():
        yield


def create_body(actor):
    from model_hub.models.evals_metric import EvalTemplate, EvalTemplateVersion

    template, version = fixtures.prompt(actor)
    column = Column.no_workspace_objects.get(dataset=actor["exp"]["dataset"])
    version.prompt_config_snapshot["messages"] = [{
        "role": "user", "content": "{{" + str(column.id) + "}}",
    }]
    version.save(update_fields=["prompt_config_snapshot"])
    # The pinned create API requires a metric. Keep its real scoped template,
    # version pin, mapping, columns and native receipts; replace only execution.
    metric_name = "bridge-output-check-" + uuid.uuid4().hex
    evaluator = EvalTemplate.no_workspace_objects.create(
        name=metric_name, owner="user", eval_type="llm",
        organization=actor["org"], workspace=actor["workspace"],
        criteria="Evaluate {{output}}",
        config={"eval_type_id": "NativeFixture", "output": "score", "required_keys": ["output"]},
    )
    pin = EvalTemplateVersion.no_workspace_objects.create(
        eval_template=evaluator, version_number=1, is_default=True,
        organization=actor["org"], workspace=actor["workspace"],
        config_snapshot=dict(evaluator.config),
    )
    return {"name": "bridge-" + uuid.uuid4().hex,
            "dataset_id": str(actor["exp"]["dataset"].id),
            "experiment_type": "llm", "prompt_config": [fixtures.entry(template, version)],
            "user_eval_metrics": [{
                "name": evaluator.name, "template_id": str(evaluator.id),
                "pinned_version_id": str(pin.id), "config": {"mapping": {"output": "output"}},
            }]}


@pytest.mark.parametrize("kind", ["prompt", "graph"])
def test_evaluator_payload_helpers_use_unique_paired_names(tenants, kind):
    from model_hub.models.evals_metric import EvalTemplate

    actor = tenants["a"]
    if kind == "prompt":
        bodies = [create_body(actor) for _ in range(2)]
    else:
        from model_hub.tests import test_experiment_native_graph as graph_fixture
        graph, version, _ = graph_fixture.make_graph(actor)
        bodies = [graph_fixture.payload(actor, graph, version) for _ in range(2)]
    metrics = [body["user_eval_metrics"][0] for body in bodies]
    templates = [EvalTemplate.no_workspace_objects.get(pk=metric["template_id"])
                 for metric in metrics]
    assert len({template.id for template in templates}) == 2
    assert len({template.name for template in templates}) == 2
    assert len({metric["name"] for metric in metrics}) == 2
    assert all(metric["name"] == template.name
               for metric, template in zip(metrics, templates))


def create(actor, body=None, *, transport=None):
    body = body or create_body(actor)
    with patch(START, **(transport or {"return_value": "admitted-workflow"})) as start:
        response = actor["client"].post("/model-hub/experiments/v2/", body, format="json")
    # Preserve the API error instead of masking a rejected request as DoesNotExist.
    assert response.status_code in (200, 503), response.content
    experiment = ExperimentsTable.no_workspace_objects.get(name=body["name"])
    return response, experiment, body, start


def setup(experiment):
    config = ExperimentPromptConfig.no_workspace_objects.get(experiment_dataset__experiment=experiment)
    context = state_of(experiment)["workflow_args"]["native_runs"][str(config.id)]
    result = activities._setup_prompt_v2_sync(
        str(experiment.id), str(experiment.snapshot_dataset_id), str(config.id),
        native_context=context,
    )
    return context, result


def row(experiment, context, prepared, row_id):
    return activities._process_row_sync(
        row_id, prepared["column_id"], str(experiment.snapshot_dataset_id), str(experiment.id),
        prepared["messages"], prepared["model_name"], prepared["model_config"],
        prepared["output_format"], prepared["run_prompt_config"], native_context=context,
    )


def finish(context, prepared):
    return activities._check_experiment_dataset_status_sync(
        prepared["experiment_dataset_id"], native_context=context,
    )


def test_create_dispatch_records_and_seals_real_native_trials(tenants):
    actor = tenants["a"]
    response, experiment, body, start = create(actor)
    assert response.status_code == 200, response.content
    binding = NativeRunBinding.objects.get(snapshot__experiment=experiment)
    assert binding.run.status == "queued" and binding.run.items.count() == 0
    assert str(binding.execution_id) == start.call_args.kwargs["admission_id"]
    assert str(binding.run_id) != str(binding.native_config_id)
    context, prepared = setup(experiment)
    with patch(PROVIDER, autospec=True, return_value=("actual output", {})) as provider:
        for row_id in prepared["row_ids"]:
            assert row(experiment, context, prepared, row_id)["status"] == "COMPLETED"
    assert provider.call_count == len(prepared["row_ids"])
    finish(context, prepared)
    binding.run.refresh_from_db()
    assert binding.run.status == "completed" and binding.run.completed_at is not None
    assert binding.run.items.count() == len(prepared["row_ids"])
    assert all(i.output == "actual output" and i.trial_number == 1 for i in binding.run.items.all())
    assert project_run(binding.run, dataset=experiment.dataset)["counts"]["completed"] == len(prepared["row_ids"])
    assert Cell.no_workspace_objects.filter(column_id=prepared["column_id"], value="actual output").count() == len(prepared["row_ids"])


def test_p03_reads_nonempty_native_run_after_real_create_and_execution(tenants):
    from model_hub.services.results_catalog import project_run as results_project_run
    response, experiment, _, _ = create(tenants["a"])
    assert response.status_code == 200, response.content
    context, prepared = setup(experiment)
    with patch(PROVIDER, autospec=True, return_value=("result", {})):
        for row_id in prepared["row_ids"]:
            row(experiment, context, prepared, row_id)
    finish(context, prepared)
    run = NativeRunBinding.objects.get(run_id=context["run_id"]).run
    result = results_project_run(run, include=["config"])
    assert result["run_id"] == str(run.id) and result["status"] == "completed"
    response = tenants["a"]["client"].get(f"/model-hub/datasets/{experiment.dataset_id}/runs/")
    assert response.status_code == 200, response.content
    assert str(run.id) in {r["run_id"] for r in response.json()["result"]["results"]}


def test_immutable_values_and_names_not_live_snapshot_cells_drive_provider(tenants):
    response, experiment, _, _ = create(tenants["a"])
    assert response.status_code == 200, response.content
    binding = NativeRunBinding.objects.get(snapshot__experiment=experiment)
    link = binding.snapshot.examples.select_related("version_item").get()
    expected = next(iter(link.version_item.input["values"].values()))
    Cell.no_workspace_objects.filter(dataset=experiment.snapshot_dataset).update(value="MUTATED")
    Column.no_workspace_objects.filter(id__in=binding.snapshot.column_map.values()).update(name="renamed")
    context, prepared = setup(experiment)
    with patch(PROVIDER, autospec=True, return_value=("result", {})) as provider:
        row(experiment, context, prepared, prepared["row_ids"][0])
    actual_messages = provider.call_args.args[0].messages
    assert expected in str(actual_messages) and "MUTATED" not in str(actual_messages)


def test_terminal_activity_replay_does_not_repeat_provider_or_mutate_receipt(tenants):
    response, experiment, _, _ = create(tenants["a"])
    assert response.status_code == 200, response.content
    context, prepared = setup(experiment)
    with patch(PROVIDER, autospec=True, return_value=("first", {})) as provider:
        row(experiment, context, prepared, prepared["row_ids"][0])
        finish(context, prepared)
        item = NativeRunBinding.objects.get(run_id=context["run_id"]).run.items.get()
        receipt = item.metadata
        row(experiment, context, prepared, prepared["row_ids"][0])
        finish(context, prepared)
        provider.assert_called_once()
    item.refresh_from_db()
    assert item.output == "first" and item.metadata == receipt


@pytest.mark.parametrize("error", [ValueError("bad request"), RuntimeError("provider unavailable")])
def test_actual_provider_failure_is_terminal_evidence_not_false_success(tenants, error):
    response, experiment, _, _ = create(tenants["a"])
    assert response.status_code == 200, response.content
    context, prepared = setup(experiment)
    with patch(PROVIDER, autospec=True, side_effect=error):
        result = row(experiment, context, prepared, prepared["row_ids"][0])
    assert result["status"] == "FAILED"
    finish(context, prepared)
    run = NativeRunBinding.objects.get(run_id=context["run_id"]).run
    assert run.status == "failed" and run.completed_at is not None
    assert run.items.get().error["type"] == type(error).__name__
    assert Cell.no_workspace_objects.get(column_id=prepared["column_id"]).status == CellStatus.ERROR.value


def test_ambiguous_admission_stays_draft_and_same_retry_reuses_identity(tenants):
    actor = tenants["a"]
    response, experiment, body, failed = create(actor, transport={"side_effect": RuntimeError("RPC uncertain")})
    assert response.status_code == 503
    binding = NativeRunBinding.objects.get(snapshot__experiment=experiment)
    assert binding.run.status == "draft"
    context = state_of(experiment)["workflow_args"]["native_runs"][str(binding.native_config_id)]
    with pytest.raises(RunWriteError):
        activities._setup_prompt_v2_sync(str(experiment.id), str(experiment.snapshot_dataset_id), str(binding.native_config_id), native_context=context)
    with patch(START, return_value="recovered") as retry:
        response = actor["client"].post("/model-hub/experiments/v2/", body, format="json")
        assert response.status_code == 200, response.content
        assert retry.call_args == failed.call_args
        again = actor["client"].post("/model-hub/experiments/v2/", body, format="json")
        assert again.status_code == 200 and retry.call_count == 1
    binding.run.refresh_from_db()
    assert binding.run.status == "queued" and NativeRunBinding.objects.filter(snapshot__experiment=experiment).count() == 1


def test_changed_effective_config_refuses_before_provider(tenants):
    response, experiment, _, _ = create(tenants["a"])
    assert response.status_code == 200, response.content
    context, prepared = setup(experiment)
    ExperimentPromptConfig.no_workspace_objects.filter(id=context["config_id"]).update(model_params={"temperature": 0.8})
    with patch(PROVIDER, autospec=True) as provider:
        assert row(experiment, context, prepared, prepared["row_ids"][0])["status"] == "FAILED"
        provider.assert_not_called()
    finish(context, prepared)


def test_foreign_row_and_tampered_dispatch_refuse_without_claim_or_provider(tenants):
    response, experiment, _, _ = create(tenants["a"])
    assert response.status_code == 200, response.content
    context, prepared = setup(experiment)
    foreign_row = str(tenants["b"]["exp"]["snapshot_row"].id)
    before = list(Cell.no_workspace_objects.filter(column_id=prepared["column_id"]).values())
    with patch(PROVIDER, autospec=True) as provider:
        with pytest.raises(RunWriteError):
            row(experiment, context, prepared, foreign_row)
        bad = {**context, "columns": []}
        with pytest.raises(RunWriteError):
            row(experiment, bad, prepared, prepared["row_ids"][0])
        provider.assert_not_called()
    assert NativeRunBinding.objects.get(run_id=context["run_id"]).run.items.count() == 0
    assert list(Cell.no_workspace_objects.filter(column_id=prepared["column_id"]).values()) == before


def test_unbound_classic_submission_and_row_call_are_unchanged(tenants):
    experiment = tenants["a"]["exp"]["experiment"]
    with transaction.atomic():
        stage_submission(experiment, {}, "update", max_concurrent_rows=10)
    with patch(START, return_value="classic") as start:
        admit_submission(str(experiment.id))
    assert "native_runs" not in start.call_args.kwargs
    assert not NativeRunBinding.objects.filter(snapshot__experiment=experiment).exists()
    from model_hub.views import experiment_runner
    args = [str(uuid.uuid4()) for _ in range(4)] + [[], "gpt-4o-mini", {}, "string", {}]
    with patch.object(experiment_runner, "_process_row_impl") as classic, patch(
        "tfc.temporal.experiments.run_policy.persisted_prompt_status", return_value="COMPLETED"
    ):
        activities._process_row_sync(*args)
    assert classic.call_args.kwargs == {"skip_celery_evals": True}


def test_cancelled_native_run_cannot_be_reported_as_completed(tenants):
    response, experiment, _, _ = create(tenants["a"])
    assert response.status_code == 200, response.content
    context, prepared = setup(experiment)
    activities._cancel_native_runs(str(experiment.id), {context["config_id"]: context})
    run = NativeRunBinding.objects.get(run_id=context["run_id"]).run
    assert run.status == "cancelled" and run.completed_at is not None
    with patch(PROVIDER, autospec=True) as provider:
        with pytest.raises(RunWriteError):
            row(experiment, context, prepared, prepared["row_ids"][0])
        provider.assert_not_called()
    assert run.items.count() == 0


def test_pinned_plan_requires_every_actual_row_before_seal(tenants):
    actor = tenants["a"]
    source = actor["exp"]["dataset"]
    source_column = Column.no_workspace_objects.get(dataset=source)
    second = Row.no_workspace_objects.create(dataset=source, order=1)
    Cell.no_workspace_objects.create(dataset=source, row=second, column=source_column, value="second")
    response, experiment, _, _ = create(actor)
    assert response.status_code == 200, response.content
    context, prepared = setup(experiment)
    assert len(prepared["row_ids"]) == 2
    with patch(PROVIDER, autospec=True, side_effect=[("first", {}), ValueError("second failed")]):
        row(experiment, context, prepared, prepared["row_ids"][0])
        with pytest.raises(RunWriteError):
            finish(context, prepared)
        row(experiment, context, prepared, prepared["row_ids"][1])
    finish(context, prepared)
    run = NativeRunBinding.objects.get(run_id=context["run_id"]).run
    assert run.status == "partial" and run.completed_at is not None
    assert set(run.items.values_list("status", flat=True)) == {"completed", "failed"}


def test_original_digit_uuid_and_snapshot_uuid_have_identical_rendering():
    from model_hub.views.experiment_runner import _render_native_messages
    original = "00000000-0000-4000-8000-000000000001"
    copied = "00000000-0000-4000-8000-000000000002"
    columns = [{"source_id": original, "snapshot_id": copied, "name": "input", "data_type": "text"}]
    for template in ("{{" + original + "}}", "{{" + copied + "}}", "{{input}}"):
        result = _render_native_messages([{"role": "user", "content": template}], {original: "pinned"}, columns, "gpt-4o-mini", "jinja2")
        assert result == [{"role": "user", "content": [{"type": "text", "text": "pinned"}]}]


@pytest.mark.parametrize("reported_cost,source,expected", [(0, "available_models", 0), (0.125, "available_models", 0.125), (0.125, "default", None)])
def test_reported_usage_and_known_cost_preserve_zero_and_unknown(tenants, reported_cost, source, expected):
    response, experiment, _, _ = create(tenants["a"])
    assert response.status_code == 200, response.content
    context, prepared = setup(experiment)
    usage = {"prompt_tokens": 7, "completion_tokens": 0, "total_tokens": 7}
    info = {"metadata": {"usage": usage, "cost": {"total_cost": reported_cost, "pricing_source": source}}}
    with patch(PROVIDER, autospec=True, return_value=("actual", info)) as provider:
        assert row(experiment, context, prepared, prepared["row_ids"][0])["status"] == "COMPLETED"
    provider.assert_called_once()
    run = NativeRunBinding.objects.get(run_id=context["run_id"]).run
    assert run.evaluator_versions  # Evaluators must not erase the prompt's telemetry.
    item = run.items.get()
    assert item.status == "completed" and item.score_refs
    assert item.cost == expected and item.token_usage == usage
    assert item.latency_ms is not None and item.latency_ms >= 0


def test_provider_failure_payload_is_not_completed_evidence(tenants):
    response, experiment, _, _ = create(tenants["a"])
    assert response.status_code == 200, response.content
    context, prepared = setup(experiment)
    with patch(PROVIDER, autospec=True, return_value=("provider failed", {"failure": "provider rejection"})):
        result = row(experiment, context, prepared, prepared["row_ids"][0])
    assert result["status"] == "FAILED"
    finish(context, prepared)
    run = NativeRunBinding.objects.get(run_id=context["run_id"]).run
    assert run.status == "failed" and run.items.get().error["type"] == "ProviderFailure"


def test_pending_failure_cleanup_preserves_draft_but_explicit_stop_cancels(tenants):
    response, experiment, body, _ = create(
        tenants["a"], transport={"side_effect": RuntimeError("ack lost")},
    )
    assert response.status_code == 503
    contexts = state_of(experiment)["workflow_args"]["native_runs"]
    binding = NativeRunBinding.objects.get(snapshot__experiment=experiment)
    activities._cancel_native_runs(str(experiment.id), contexts, include_draft=False)
    binding.run.refresh_from_db()
    assert binding.run.status == "draft" and binding.run.completed_at is None
    activities._cancel_native_runs(str(experiment.id), contexts)
    binding.run.refresh_from_db()
    assert binding.run.status == "cancelled" and binding.run.completed_at is not None
    with patch(START, return_value="must-not-reopen"):
        retry = tenants["a"]["client"].post("/model-hub/experiments/v2/", body, format="json")
    assert retry.status_code == 503
    binding.run.refresh_from_db()
    assert binding.run.status == "cancelled" and binding.run.items.count() == 0
    assert state_of(experiment)["state"] == "pending"


def test_native_admission_wait_checks_exact_committed_context(tenants):
    from model_hub.services.experiment_submit import native_admission_ready
    response, experiment, _, _ = create(
        tenants["a"], transport={"side_effect": RuntimeError("ack lost")},
    )
    assert response.status_code == 503
    contexts = state_of(experiment)["workflow_args"]["native_runs"]
    assert native_admission_ready(str(experiment.id), contexts) is False
    config_id, context = next(iter(contexts.items()))
    for altered in ({}, {config_id: {**context, "execution_id": str(uuid.uuid4())}},
                    {"foreign-config": context}):
        with pytest.raises(RunWriteError):
            native_admission_ready(str(experiment.id), altered)
    binding = NativeRunBinding.objects.get(snapshot__experiment=experiment)
    assert binding.run.status == "draft" and binding.run.items.count() == 0


# Slice (c): authored before its implementation. No runtime receipt is claimed.
def complete_current(experiment, output="first"):
    contexts = state_of(experiment)["workflow_args"].get("native_runs", {})
    for context in contexts.values():
        prepared = activities._setup_prompt_v2_sync(
            str(experiment.id), str(experiment.snapshot_dataset_id), context["config_id"],
            native_context=context,
        )
        with patch(PROVIDER, autospec=True, return_value=(output, {})):
            for row_id in prepared["row_ids"]:
                row(experiment, context, prepared, row_id)
        finish(context, prepared)
    from model_hub.models.choices import StatusType
    ExperimentsTable.no_workspace_objects.filter(pk=experiment.pk).update(status=StatusType.COMPLETED.value)
    experiment.refresh_from_db()


def rerun(actor, experiments, **transport):
    from django.urls import reverse
    body = {"experiment_ids": [str(experiment.id) for experiment in experiments]}
    with patch(START, **(transport or {"return_value": "rerun-workflow"})) as start:
        response = actor["client"].post(reverse("experiments-v2-rerun"), body, format="json")
    return response, start


def test_put_changed_prompt_gets_fresh_identity_and_keeps_old_receipt(tenants):
    actor = tenants["a"]
    response, experiment, body, _ = create(actor)
    assert response.status_code == 200, response.content
    complete_current(experiment)
    old = NativeRunBinding.objects.get(snapshot__experiment=experiment)
    before = list(old.run.items.values())
    entry = {**body["prompt_config"][0], "id": str(old.native_config_id), "model": "gpt-4o"}
    url = f"/model-hub/experiments/v2/{experiment.id}/"
    with patch(START, return_value="updated") as start:
        response = actor["client"].put(url, {"prompt_config": [entry]}, format="json")
        assert response.status_code == 200, response.content
        again = actor["client"].put(url, {"prompt_config": [entry]}, format="json")
        assert again.status_code == 200, again.content
        start.assert_called_once()
    fresh = NativeRunBinding.objects.filter(snapshot__experiment=experiment).exclude(pk=old.pk).get()
    assert fresh.execution_id != old.execution_id and fresh.run_id != old.run_id
    assert fresh.native_config_id == old.native_config_id
    assert fresh.snapshot_id == old.snapshot_id and fresh.run.status == "queued"
    assert start.call_args.kwargs["native_runs"][str(fresh.native_config_id)]["run_id"] == str(fresh.run_id)
    complete_current(experiment, "updated output")
    assert list(old.run.items.values()) == before
    assert fresh.run.items.get().output == "updated output"


def test_put_pending_retry_keeps_execution_and_rejects_competing_edit(tenants):
    actor = tenants["a"]
    response, experiment, body, _ = create(actor)
    assert response.status_code == 200, response.content
    complete_current(experiment)
    config = ExperimentPromptConfig.no_workspace_objects.get(experiment_dataset__experiment=experiment)
    payload = {"prompt_config": [{**body["prompt_config"][0], "id": str(config.id), "model": "gpt-4o"}]}
    url = f"/model-hub/experiments/v2/{experiment.id}/"
    with patch(START, side_effect=RuntimeError("lost acknowledgement")) as first:
        response = actor["client"].put(url, payload, format="json")
    assert response.status_code == 503, response.content
    pending = state_of(experiment)
    with patch(START, return_value="recovered") as start:
        denied = actor["client"].put(url, {"column_id": None}, format="json")
        assert denied.status_code == 400, denied.content
        assert state_of(experiment) == pending
        start.assert_not_called()
        response = actor["client"].put(url, payload, format="json")
    assert response.status_code == 200, response.content
    assert start.call_args == first.call_args
    assert NativeRunBinding.objects.filter(snapshot__experiment=experiment).count() == 2


def test_put_select_clear_restore_invalidates_only_accepted_replay(tenants):
    actor = tenants["a"]
    response, experiment, _, _ = create(actor)
    assert response.status_code == 200, response.content
    complete_current(experiment)
    binding = NativeRunBinding.objects.get(snapshot__experiment=experiment)
    target_id = str(next(iter(binding.snapshot.column_map.values())))
    url = f"/model-hub/experiments/v2/{experiment.id}/"
    with patch(START, return_value="base-column-run") as start:
        selected = actor["client"].put(url, {"column_id": target_id}, format="json")
        assert selected.status_code == 200, selected.content
        complete_current(experiment)
        cleared = actor["client"].put(url, {"column_id": None}, format="json")
        assert cleared.status_code == 200, cleared.content
        experiment.refresh_from_db()
        assert experiment.column_id is None and start.call_count == 1
        restored = actor["client"].put(url, {"column_id": target_id}, format="json")
        assert restored.status_code == 200, restored.content
        experiment.refresh_from_db()
        assert str(experiment.column_id) == target_id and start.call_count == 2
    # Base-column changes cannot manufacture prompt executions.
    assert NativeRunBinding.objects.filter(snapshot__experiment=experiment).count() == 1


def test_whole_rerun_uses_new_identity_then_terminal_same_body_runs_again(tenants):
    actor = tenants["a"]
    response, experiment, _, _ = create(actor)
    assert response.status_code == 200, response.content
    complete_current(experiment)
    old = NativeRunBinding.objects.get(snapshot__experiment=experiment)
    before = list(old.run.items.values())
    response, start = rerun(actor, [experiment])
    assert response.status_code == 200, response.content
    fresh_context = state_of(experiment)["workflow_args"]["native_runs"][str(old.native_config_id)]
    assert fresh_context["run_id"] != str(old.run_id)
    assert start.call_args.kwargs["admission_id"] == fresh_context["execution_id"]
    # Repeating while the accepted execution is active must not replace it.
    response, duplicate = rerun(actor, [experiment])
    assert response.status_code == 200, response.content
    duplicate.assert_not_called()
    complete_current(experiment, "second")
    response, next_start = rerun(actor, [experiment])
    assert response.status_code == 200, response.content
    next_start.assert_called_once()
    assert NativeRunBinding.objects.filter(snapshot__experiment=experiment).count() == 3
    assert list(old.run.items.values()) == before


def test_whole_rerun_lost_ack_retry_does_not_reset_cells_or_duplicate_run(tenants):
    actor = tenants["a"]
    response, experiment, _, _ = create(actor)
    assert response.status_code == 200, response.content
    complete_current(experiment)
    before = list(Cell.no_workspace_objects.filter(dataset=experiment.snapshot_dataset).order_by("id").values())
    response, failed = rerun(actor, [experiment], side_effect=RuntimeError("uncertain start"))
    assert response.status_code == 503, response.content
    state = state_of(experiment)
    context = next(iter(state["workflow_args"]["native_runs"].values()))
    binding = NativeRunBinding.objects.get(run_id=context["run_id"])
    assert binding.run.status == "draft"
    assert list(Cell.no_workspace_objects.filter(dataset=experiment.snapshot_dataset).order_by("id").values()) == before
    response, retried = rerun(actor, [experiment])
    assert response.status_code == 200, response.content
    assert retried.call_args == failed.call_args
    assert NativeRunBinding.objects.filter(snapshot__experiment=experiment).count() == 2


def test_rerun_batch_recovery_reuses_even_already_completed_member(tenants):
    actor = tenants["a"]
    experiments = []
    for _ in range(2):
        response, experiment, _, _ = create(actor)
        assert response.status_code == 200, response.content
        complete_current(experiment)
        experiments.append(experiment)
    # Batch dispatch uses deterministic ID ordering.
    experiments.sort(key=lambda value: str(value.id))
    response, start = rerun(actor, experiments, side_effect=["accepted", RuntimeError("uncertain")])
    assert response.status_code == 503, response.content
    original_contexts = [state_of(exp)["workflow_args"]["native_runs"] for exp in experiments]
    complete_current(experiments[0], "accepted member finished")
    response, retried = rerun(actor, list(reversed(experiments)))
    assert response.status_code == 200, response.content
    retried.assert_called_once()
    assert retried.call_args == start.call_args_list[1]
    assert [state_of(exp)["workflow_args"]["native_runs"] for exp in experiments] == original_contexts
    assert all(NativeRunBinding.objects.filter(snapshot__experiment=exp).count() == 2 for exp in experiments)


def test_rerun_mixed_tenant_batch_has_no_partial_effects(tenants):
    experiments = []
    for actor in (tenants["a"], tenants["b"]):
        response, experiment, _, _ = create(actor)
        assert response.status_code == 200, response.content
        complete_current(experiment)
        experiments.append(experiment)
    before = [state_of(exp) for exp in experiments]
    response, start = rerun(tenants["a"], experiments)
    assert response.status_code == 404, response.content
    start.assert_not_called()
    assert [state_of(exp) for exp in experiments] == before


def test_nonmatching_edit_cannot_overlap_a_bound_active_run(tenants):
    response, experiment, _, _ = create(tenants["a"])
    assert response.status_code == 200, response.content
    before = state_of(experiment)
    with patch(START) as start:
        response = tenants["a"]["client"].put(
            f"/model-hub/experiments/v2/{experiment.id}/", {"column_id": None}, format="json",
        )
    assert response.status_code == 400, response.content
    start.assert_not_called()
    assert state_of(experiment) == before


# Item 4(b): evaluator acceptance regressions, authored before integration.
# These specify the completed plan-bound evaluator contract; execution is not claimed.
from model_hub.tests.test_native_run_binding import native_world  # noqa: E402,F401


@pytest.mark.parametrize("operation", ["create", "append_variant", "update", "rerun"])
def test_attached_evaluator_admission_binds_exact_versions_and_plan(native_world, operation):
    from model_hub.tests.test_native_run_binding import add_native_eval

    w = native_world; w["experiment"].refresh_from_db()
    metric, pin = add_native_eval(w)
    args = {"max_concurrent_rows": 10}
    if operation in {"append_variant", "update"}:
        args.update(rerun_prompt_config_ids=[str(w["config"].id)],
                    rerun_agent_config_ids=[], rerun_eval_template_ids=[])
    before_cells = list(Cell.no_workspace_objects.filter(dataset=w["snapshot"]).order_by("id").values())
    with transaction.atomic():
        state = stage_submission(w["experiment"], {}, operation,
                                 authorized_workspace=w["dataset"].workspace, **args)
    contexts = state["workflow_args"]["native_runs"]
    assert set(contexts) == {str(w["config"].id)}
    context = contexts[str(w["config"].id)]
    binding = NativeRunBinding.objects.get(run_id=context["run_id"])
    assert binding.run.status == "draft" and not binding.run.items.exists()
    target = state["execution_plan"]["targets"][str(w["config"].id)]
    assert target["evaluator_versions"] == binding.run.evaluator_versions
    assert {(p["metric_id"], p["evaluator_version_id"]) for p in target["evaluator_versions"]} == {
        (str(metric.id), str(pin.id))}
    assert target["snapshot_row_ids"] == sorted(str(row.id) for row in w["row_map"].values())
    assert list(Cell.no_workspace_objects.filter(dataset=w["snapshot"]).order_by("id").values()) == before_cells
    with patch(START, return_value="pinned-evaluator-workflow") as starter:
        first = admit_submission(str(w["experiment"].id), state["admission_id"])
        assert admit_submission(str(w["experiment"].id), state["admission_id"]) == first
        starter.assert_called_once()
        assert starter.call_args.kwargs["native_runs"] == contexts


def test_eval_only_update_binds_plan_without_scheduling_prompt_generation(native_world):
    from model_hub.tests.test_native_run_binding import add_native_eval

    w = native_world; w["experiment"].refresh_from_db()
    with transaction.atomic():
        stage_submission(w["experiment"], {}, "create", authorized_workspace=w["dataset"].workspace)
    with patch(START, return_value="initial-output"):
        admit_submission(str(w["experiment"].id))
    complete_current(w["experiment"], "immutable previous output")
    original = NativeRunBinding.objects.get(snapshot__experiment=w["experiment"])
    old_items = list(original.run.items.order_by("id").values())
    old_cells = list(Cell.no_workspace_objects.filter(column=w["output_column"]).order_by("id").values())
    metric, pin = add_native_eval(w)
    with transaction.atomic():
        state = stage_submission(w["experiment"], {"eval_only": str(metric.id)}, "update",
            authorized_workspace=w["dataset"].workspace,
            rerun_prompt_config_ids=[], rerun_agent_config_ids=[],
            rerun_eval_template_ids=[str(metric.id)])
    assert state["execution_plan"]["operation"] == "update"
    targets = state["execution_plan"]["targets"]
    assert targets and all((str(metric.id), str(pin.id)) in {
        (p["metric_id"], p["evaluator_version_id"]) for p in target["evaluator_versions"]}
        for target in targets.values())
    assert state["workflow_args"]["rerun_prompt_config_ids"] == []
    assert state["workflow_args"]["rerun_agent_config_ids"] == []
    assert state["workflow_args"]["rerun_eval_template_ids"] == [str(metric.id)]
    assert all(target["run_id"] != str(original.run_id) for target in targets.values())
    assert list(original.run.items.order_by("id").values()) == old_items
    assert list(Cell.no_workspace_objects.filter(column=w["output_column"]).order_by("id").values()) == old_cells


def test_graph_only_version_update_reconciles_and_stages_without_running_other_prompt(tenants):
    from copy import deepcopy

    from agent_playground.models import GraphVersion, Node, Port
    from model_hub.models.choices import SourceChoices
    from model_hub.tests.test_native_prompt_transport import r8_drive_graph

    world = r8_create_graph(tenants["a"], extra_prompt=True)
    experiment = world["experiment"]
    # Finish BOTH V1 variants through their real native workers before PUT.
    prompt_context, prepared = setup(experiment)
    with native_eval_transport(), patch(PROVIDER, return_value=("unrelated prompt", {})):
        for row_id in prepared["row_ids"]:
            row(experiment, prompt_context, prepared, row_id)
        finish(prompt_context, prepared)
    assert r8_drive_graph(world)[0].status == "COMPLETED"
    assert activities._check_experiment_status_sync(str(experiment.id))["is_complete"]
    _, previous, _ = r8_graph_context(world)
    history = list(previous.run.items.order_by("id").values())
    previous_plan = deepcopy(previous.run.metadata["native_identity"]["graph_plan"])
    old_columns = list(previous.variant.columns.filter(source=SourceChoices.EXPERIMENT.value).values_list("id", flat=True))
    old_cells = list(Cell.no_workspace_objects.filter(column_id__in=old_columns).order_by("id").values())
    prompt_run = NativeRunBinding.objects.get(run_id=prompt_context["run_id"]).run
    prompt_history = list(prompt_run.items.order_by("id").values())
    prompt_columns = list(prompt_run.native_binding.variant.columns.values_list("id", flat=True))
    prompt_cells = list(Cell.no_workspace_objects.filter(column_id__in=prompt_columns).order_by("id").values())
    world["version"].status = "inactive"
    world["version"].save(update_fields=["status"])
    version = GraphVersion.no_workspace_objects.create(
        graph=world["version"].graph, version_number=2, status="active", tags=["native-fixture"],
    )
    nodes = []
    for label in ("V2-a",):
        node = Node.no_workspace_objects.create(graph_version=version, node_template=world["node"].node_template,
            type="atomic", name=label, config={"input_key": "query", "transform": "upper", "prefix": label + ":"})
        for direction, name in (("input", "query"), ("output", "response")):
            Port.no_workspace_objects.create(node=node, key="custom", display_name=name,
                direction=direction, data_schema={"type": "string"})
        nodes.append(node)
    version.full_clean()
    prompt = ExperimentPromptConfig.no_workspace_objects.get(experiment_dataset__experiment=experiment)
    body = {"prompt_config": [
        {"id": str(world["config"].id), "agent_id": str(version.graph_id), "agent_version": str(version.id)},
        {**world["body"]["prompt_config"][1], "id": str(prompt.id)},
    ]}
    with patch(START, return_value="graph-v2-admitted") as starter:
        response = world["actor"]["client"].put(f"/model-hub/experiments/v2/{experiment.id}/", body, format="json")
    assert response.status_code == 200, response.content
    state = state_of(experiment)
    assert state["workflow_args"]["rerun_prompt_config_ids"] == []
    assert state["workflow_args"]["rerun_agent_config_ids"] == [str(world["config"].id)]
    assert set(state["workflow_args"]["native_runs"]) == {str(world["config"].id)}
    assert not any("graph" in reason for reason in state.get("native_bridge_pending", []))
    world["version"] = version
    context, current, target = r8_graph_context(world)
    assert starter.call_args.kwargs["native_runs"] == state["workflow_args"]["native_runs"]
    assert current.run_id != previous.run_id
    plan = current.run.metadata["native_identity"]["graph_plan"]
    assert set(plan["node_column_mapping"]) == {str(n.id) for n in nodes}
    assert len(plan["terminal_column_ids"]) == 1
    assert not set(plan["node_column_mapping"].values()) & {str(c) for c in old_columns}
    # Classic preserves V1 columns/links. Native planning selects only V2.
    assert set(old_columns) <= set(current.variant.columns.values_list("id", flat=True))
    assert r8_drive_graph(world)[0].status == "COMPLETED"
    current.run.refresh_from_db(); previous.run.refresh_from_db()
    assert current.run.status == "completed" and current.run.items.count() == len(target["example_ids"])
    assert all(set(item.metadata["native_graph"]["node_results"]) == {str(n.id) for n in nodes}
               for item in current.run.items.all())
    assert history == list(previous.run.items.order_by("id").values())
    assert previous.run.metadata["native_identity"]["graph_plan"] == previous_plan
    assert old_cells == list(Cell.no_workspace_objects.filter(column_id__in=old_columns).order_by("id").values())
    assert prompt_history == list(prompt_run.items.order_by("id").values())
    assert prompt_cells == list(Cell.no_workspace_objects.filter(column_id__in=prompt_columns).order_by("id").values())
    assert project_run(previous.run, dataset=experiment.dataset)["run_id"] == str(previous.run_id)


# P1 recovery tests intentionally seed a prompt-only native snapshot and invoke
# product stage/admit rather than the metric-requiring HTTP create API. Binding,
# admission and guard logic stay real; canonical IDs come from the product.
def seed_native_admission(actor):
    from model_hub.tests.test_native_run_binding import make_native_world

    template, version = fixtures.prompt(actor, "cohort input")
    world = make_native_world(actor["org"], actor["workspace"], actor["user"], version)
    config = world["config"]
    config.model = "gpt-4o-mini"
    config.save(update_fields=["model"])
    experiment = world["experiment"]
    experiment.refresh_from_db()
    body = {"prompt_config": [fixtures.entry(template, version)]}
    with transaction.atomic():
        stage_submission(experiment, body, "create", authorized_workspace=actor["workspace"])
    with patch(START, return_value="fixture-admission"):
        admit_submission(str(experiment.id))
    return experiment, body


# P02A-R2-COR-001: authored before the admission journal repair (not executed).
@pytest.mark.parametrize("edit", ["clear", "update", "append", "clear_then_update"])
def test_pending_cohort_survives_completed_sibling_edit(tenants, edit):
    actor = tenants["a"]
    members = []
    for _ in range(2):
        experiment, body = seed_native_admission(actor)
        complete_current(experiment)
        members.append((experiment, body))
    members.sort(key=lambda pair: str(pair[0].id))
    first, first_body = members[0]
    pending, _ = members[1]
    first_binding = NativeRunBinding.objects.get(snapshot__experiment=first)
    target = str(next(iter(first_binding.snapshot.column_map.values())))
    url = f"/model-hub/experiments/v2/{first.id}/"
    with patch(START, return_value="base-column"):
        response = actor["client"].put(url, {"column_id": target}, format="json")
    assert response.status_code == 200, response.content
    complete_current(first)
    response, initial = rerun(actor, [first, pending], side_effect=["accepted", RuntimeError("ack lost")])
    assert response.status_code == 503, response.content
    first_admission = state_of(first)
    pending_admission = state_of(pending)
    complete_current(first, "sibling completed")
    prior_runs = list(NativeRunBinding.objects.filter(snapshot__experiment=first).values_list("run_id", flat=True))
    prior_items = {str(binding.run_id): list(binding.run.items.values()) for binding in
                   NativeRunBinding.objects.filter(snapshot__experiment=first)}
    if edit == "clear_then_update":
        with patch(START) as no_dispatch:
            response = actor["client"].put(url, {"column_id": None}, format="json")
        assert response.status_code == 200, response.content
        no_dispatch.assert_not_called()
    if edit == "clear":
        payload = {"column_id": None}
    else:
        entry = {**first_body["prompt_config"][0], "model": "gpt-4o"}
        if edit in {"update", "clear_then_update"}:
            entry["id"] = str(first_binding.native_config_id)
        payload = {"prompt_config": [entry]}
        if edit == "append":
            payload["append_only"] = True
    with patch(START, return_value="newer-sibling-work") as edit_start:
        response = actor["client"].put(url, payload, format="json")
    assert response.status_code == 200, response.content
    if edit == "clear":
        edit_start.assert_not_called()
        assert "request_hash" not in state_of(first)
    else:
        edit_start.assert_called_once()
        assert state_of(first)["admission_id"] != first_admission["admission_id"]
    after_edit = state_of(first)
    first_count = NativeRunBinding.objects.filter(snapshot__experiment=first).count()
    with patch(PROVIDER, autospec=True) as provider:
        response, recovered = rerun(actor, [pending, first])
        provider.assert_not_called()
    assert response.status_code == 200, response.content
    recovered.assert_called_once()
    assert recovered.call_args == initial.call_args_list[1]
    assert state_of(first) == after_edit
    assert state_of(pending)["admission_id"] == pending_admission["admission_id"]
    assert state_of(pending)["workflow_args"] == pending_admission["workflow_args"]
    assert NativeRunBinding.objects.filter(snapshot__experiment=first).count() == first_count
    assert NativeRunBinding.objects.filter(snapshot__experiment=pending).count() == 2
    for binding in NativeRunBinding.objects.filter(run_id__in=prior_runs):
        assert list(binding.run.items.values()) == prior_items[str(binding.run_id)]


def test_pending_cohort_rejects_partial_and_foreign_recovery(tenants):
    actor = tenants["a"]
    members = []
    for _ in range(2):
        experiment, _ = seed_native_admission(actor)
        complete_current(experiment)
        members.append(experiment)
    members.sort(key=lambda experiment: str(experiment.id))
    response, _ = rerun(actor, members, side_effect=["accepted", RuntimeError("ack lost")])
    assert response.status_code == 503, response.content
    before = [state_of(experiment) for experiment in members]
    response, partial = rerun(actor, [members[1]])
    assert response.status_code == 400, response.content
    partial.assert_not_called()
    response, foreign = rerun(tenants["b"], members)
    assert response.status_code == 404, response.content
    foreign.assert_not_called()
    assert [state_of(experiment) for experiment in members] == before


def test_admission_history_is_not_replaced_by_invalidated_latest_cache():
    from model_hub.services.experiment_submit import (
        ADMISSIONS_KEY,
        STATE_KEY,
        _remember_submission,
    )

    admitted = {"admission_id": "execution-a", "request_hash": "original-digest",
                "batch_id": "batch-a", "state": "accepted", "workflow_id": "wf-a",
                "workflow_args": {"native_runs": {"config": {"run_id": "run-a"}}}}
    from copy import deepcopy
    config = {STATE_KEY: deepcopy(admitted)}
    _remember_submission(config, config[STATE_KEY])
    config[STATE_KEY].pop("request_hash")
    _remember_submission(config, config[STATE_KEY])
    assert config[ADMISSIONS_KEY]["execution-a"] == admitted
    config[STATE_KEY]["workflow_args"]["native_runs"].clear()
    assert config[ADMISSIONS_KEY]["execution-a"] == admitted


def test_exact_cohort_admission_does_not_dispatch_newer_sibling_intent(tenants):
    from model_hub.services.experiment_submit import stage_rerun_batch

    actor = tenants["a"]
    first, body = seed_native_admission(actor)
    second, _ = seed_native_admission(actor)
    for experiment in (first, second):
        complete_current(experiment)
    members = sorted([first, second], key=lambda experiment: str(experiment.id))
    response, _ = rerun(actor, members, side_effect=["accepted", RuntimeError("ack lost")])
    assert response.status_code == 503, response.content
    first, second = members
    complete_current(first)
    request = {"experiment_ids": [str(experiment.id) for experiment in members], "max_concurrent_rows": 10}
    with transaction.atomic():
        admissions = stage_rerun_batch(members, request, actor["workspace"])
    # Interleave a new, ambiguous sibling submission after recovery resolved IDs.
    config = ExperimentPromptConfig.no_workspace_objects.get(experiment_dataset__experiment=first)
    with transaction.atomic():
        stage_submission(first, {"interleaved": True}, "update",
                         authorized_workspace=actor["workspace"],
                         rerun_prompt_config_ids=[str(config.id)])
    newer = state_of(first)
    assert newer["state"] == "pending" and newer["admission_id"] != admissions[str(first.id)]
    with patch(START, return_value="recovered") as start:
        for experiment in members:
            admit_submission(str(experiment.id), admissions[str(experiment.id)])
    start.assert_called_once()
    assert start.call_args.kwargs["experiment_id"] == str(second.id)
    assert state_of(first) == newer


# Item 4(a): authored before the corresponding product replacements.
SUBSET_START = "tfc.temporal.experiments.start_rerun_cells_v2_workflow"


def subset_world(actor):
    from model_hub.models.choices import SourceChoices
    from model_hub.models.experiments import ExperimentDatasetTable
    experiment, _ = seed_native_admission(actor)
    complete_current(experiment)
    first = ExperimentPromptConfig.no_workspace_objects.get(experiment_dataset__experiment=experiment)
    variant = ExperimentDatasetTable.no_workspace_objects.create(name="subset-B", experiment=experiment)
    second = ExperimentPromptConfig.no_workspace_objects.create(
        experiment_dataset=variant, prompt_version=first.prompt_version,
        prompt_template=first.prompt_template, name="subset-B", model="gpt-4o-mini",
    )
    output = Column.no_workspace_objects.create(
        dataset=experiment.snapshot_dataset, name="subset-B", data_type="text",
        source=SourceChoices.EXPERIMENT.value, source_id=str(variant.id),
    )
    variant.columns.add(output)
    with transaction.atomic():
        stage_submission(experiment, {"new": str(second.id)}, "update",
                         authorized_workspace=actor["workspace"], rerun_prompt_config_ids=[str(second.id)])
    with patch(START, return_value="second-admitted"):
        admit_submission(str(experiment.id))
    complete_current(experiment, "old-B")
    configs = [first, second]
    columns = [config.experiment_dataset.columns.get() for config in configs]
    rows = [str(row_id) for row_id in Row.no_workspace_objects.filter(
        dataset=experiment.snapshot_dataset, deleted=False,
    ).order_by("order", "id").values_list("id", flat=True)]
    return experiment, configs, columns, rows


def subset_post(actor, experiment, body, key="subset-key", **transport):
    headers = {"HTTP_IDEMPOTENCY_KEY": key} if key is not None else {}
    with patch(SUBSET_START, **(transport or {"return_value": "subset-admitted"})) as start:
        response = actor["client"].post(
            f"/model-hub/experiments/v2/{experiment.id}/rerun-cells/", body,
            format="json", **headers,
        )
    return response, start


@pytest.mark.parametrize("mode", ["cells", "columns", "failed_only", "mixed"])
def test_subset_plan_executes_exact_per_config_rows_and_replays_terminal_key(tenants, mode):
    actor = tenants["a"]
    experiment, configs, columns, rows = subset_world(actor)
    expected = {str(configs[0].id): [rows[0]], str(configs[1].id): [rows[1]]}
    body = {"cells": [{"column_id": str(columns[i].id), "row_id": rows[i]} for i in range(2)]}
    if mode in {"columns", "failed_only"}:
        body = {"source_ids": [str(config.experiment_dataset_id) for config in configs]}
        if mode == "columns":
            expected = {str(config.id): rows for config in configs}
        else:
            body["failed_only"] = True
            for i, column in enumerate(columns):
                Cell.no_workspace_objects.filter(column=column, row_id=rows[i]).update(status=CellStatus.ERROR.value)
    elif mode == "mixed":
        body["source_ids"] = [str(configs[0].experiment_dataset_id)]
        expected[str(configs[0].id)] = rows
    old_runs = list(NativeRunBinding.objects.filter(snapshot__experiment=experiment).values_list("run_id", flat=True))
    from model_hub.models.experiments import ExperimentRunItem
    old_receipts = list(ExperimentRunItem.no_workspace_objects.filter(run_id__in=old_runs).order_by("id").values())
    cells_before = {str(cell.id): (str(cell.column_id), str(cell.row_id), cell.value, cell.status)
                    for cell in Cell.no_workspace_objects.filter(dataset=experiment.snapshot_dataset)}
    response, start = subset_post(actor, experiment, body)
    assert response.status_code == 200, response.content
    state = state_of(experiment)
    plan = state["execution_plan"]
    assert plan["plan_id"] == state["admission_id"] == start.call_args.kwargs["admission_id"]
    assert plan["execution_key"] == "subset-key" and plan["operation"] == "subset"
    assert plan["snapshot_dataset_id"] == str(experiment.snapshot_dataset_id)
    contexts = state["workflow_args"]["native_runs"]
    assert set(contexts) == set(expected)
    assert start.call_args.kwargs["native_runs"] == contexts
    # Temporal copies a context into every row activity: rows live in the plan.
    import json
    assert not any(row_id in json.dumps(contexts) for row_id in rows)
    with patch(PROVIDER, autospec=True, return_value=("subset-result", {})) as provider:
        for config_id, context in contexts.items():
            target = plan["targets"][config_id]
            assert "target" not in context and context["row_count"] == len(target["snapshot_row_ids"])
            assert target["snapshot_row_ids"] == sorted(expected[config_id])
            assert context["plan_id"] == plan["plan_id"]
            assert context["plan_hash"] == state["execution_plan_hash"]
            prepared = activities._setup_prompt_v2_sync(
                str(experiment.id), str(experiment.snapshot_dataset_id), config_id,
                rerun_row_ids=target["snapshot_row_ids"], native_context=context,
            )
            assert set(prepared["row_ids"]) == set(expected[config_id])
            for row_id in prepared["row_ids"]:
                assert row(experiment, context, prepared, row_id)["status"] == "COMPLETED"
            finish(context, prepared)
            run = NativeRunBinding.objects.get(run_id=context["run_id"]).run
            assert run.status == "completed" and run.items.count() == len(expected[config_id])
            assert {str(item.example_id) for item in run.items.all()} == set(target["example_ids"])
        assert provider.call_count == sum(map(len, expected.values()))
    assert list(ExperimentRunItem.no_workspace_objects.filter(run_id__in=old_runs).order_by("id").values()) == old_receipts
    targeted = {(str(column.id), row_id) for config, column in zip(configs, columns)
                for row_id in expected[str(config.id)]}
    for cell in Cell.no_workspace_objects.filter(dataset=experiment.snapshot_dataset):
        before = cells_before.get(str(cell.id))
        if before and (str(cell.column_id), str(cell.row_id)) not in targeted:
            assert (str(cell.column_id), str(cell.row_id), cell.value, cell.status) == before
    complete_current(experiment)
    replay, repeated = subset_post(actor, experiment, body)
    assert replay.status_code == 200 and replay.json() == response.json()
    repeated.assert_not_called()
    assert state_of(experiment)["execution_plan"] == plan
    assert NativeRunBinding.objects.filter(snapshot__experiment=experiment).count() == len(old_runs) + len(expected)


def test_subset_key_conflict_and_normalized_replay_do_not_mutate_cells(tenants):
    actor = tenants["a"]
    experiment, configs, columns, rows = subset_world(actor)
    body = {"source_ids": [str(config.experiment_dataset_id) for config in configs]}
    response, _ = subset_post(actor, experiment, body)
    assert response.status_code == 200, response.content
    before = state_of(experiment)
    reverse = {"source_ids": list(reversed(body["source_ids"])) + [body["source_ids"][0]],
               "failed_only": False, "max_concurrent_rows": 10}
    response, start = subset_post(actor, experiment, reverse)
    assert response.status_code == 200
    start.assert_not_called()
    response, start = subset_post(actor, experiment, {"source_ids": body["source_ids"][:1]})
    assert response.status_code == 409, response.content
    start.assert_not_called()
    assert state_of(experiment) == before


def test_subset_ambiguous_admission_reuses_draft_plan_before_any_reset(tenants):
    actor = tenants["a"]
    experiment, configs, columns, rows = subset_world(actor)
    body = {"cells": [{"column_id": str(columns[0].id), "row_id": rows[0]}], "failed_only": True}
    Cell.no_workspace_objects.filter(column=columns[0], row_id=rows[0]).update(status=CellStatus.ERROR.value)
    before = list(Cell.no_workspace_objects.filter(dataset=experiment.snapshot_dataset).order_by("id").values())
    response, first = subset_post(actor, experiment, body, side_effect=RuntimeError("ack lost"))
    assert response.status_code == 503, response.content
    state = state_of(experiment)
    context = next(iter(state["workflow_args"]["native_runs"].values()))
    assert NativeRunBinding.objects.get(run_id=context["run_id"]).run.status == "draft"
    from model_hub.services.experiment_submit import native_admission_ready
    assert native_admission_ready(str(experiment.id), state["workflow_args"]["native_runs"]) is False
    assert list(Cell.no_workspace_objects.filter(dataset=experiment.snapshot_dataset).order_by("id").values()) == before
    response, retry = subset_post(actor, experiment, body)
    assert response.status_code == 200, response.content
    assert first.call_args == retry.call_args
    assert state_of(experiment)["execution_plan"] == state["execution_plan"]
    assert native_admission_ready(str(experiment.id), state["workflow_args"]["native_runs"]) is True


@pytest.mark.parametrize("bad_key", ["", " ", "x" * 129])
def test_subset_invalid_execution_key_refuses_before_intent(tenants, bad_key):
    actor = tenants["a"]
    experiment, configs, _, _ = subset_world(actor)
    before = state_of(experiment)
    response, start = subset_post(actor, experiment, {"source_ids": [str(configs[0].experiment_dataset_id)]}, key=bad_key)
    assert response.status_code == 400, response.content
    start.assert_not_called()
    assert state_of(experiment) == before


def test_subset_foreign_scope_and_foreign_row_have_no_partial_effects(tenants):
    actor = tenants["a"]
    experiment, _, columns, rows = subset_world(actor)
    body = {"cells": [{"column_id": str(columns[0].id), "row_id": rows[0]}]}
    before = state_of(experiment)
    response, start = subset_post(tenants["b"], experiment, body)
    # Classic rerun-cells reports an invisible experiment as 400, unchanged here.
    assert response.status_code == 400, response.content
    start.assert_not_called()
    body["cells"].append({"column_id": str(columns[1].id), "row_id": str(uuid.uuid4())})
    response, start = subset_post(actor, experiment, body)
    assert response.status_code == 400, response.content
    start.assert_not_called()
    assert state_of(experiment) == before


def test_subset_out_of_plan_row_and_tampered_persisted_plan_refuse_before_provider(tenants):
    from copy import deepcopy

    from model_hub.models.develop_dataset import Dataset
    from model_hub.services.experiment_submit import ADMISSIONS_KEY
    actor = tenants["a"]
    experiment, configs, columns, rows = subset_world(actor)
    response, _ = subset_post(actor, experiment, {"cells": [{"column_id": str(columns[0].id), "row_id": rows[0]}]})
    assert response.status_code == 200, response.content
    state = state_of(experiment)
    context = state["workflow_args"]["native_runs"][str(configs[0].id)]
    prepared = activities._setup_prompt_v2_sync(str(experiment.id), str(experiment.snapshot_dataset_id), str(configs[0].id), native_context=context)
    with patch(PROVIDER) as provider:
        with pytest.raises(RunWriteError):
            row(experiment, context, prepared, rows[1])
        provider.assert_not_called()
        snapshot = Dataset.no_workspace_objects.get(pk=experiment.snapshot_dataset_id)
        config = deepcopy(snapshot.dataset_config)
        config[ADMISSIONS_KEY][state["admission_id"]]["execution_plan"]["targets"][str(configs[0].id)]["snapshot_row_ids"] = rows
        snapshot.dataset_config = config
        snapshot.save(update_fields=["dataset_config"])
        with pytest.raises(RunWriteError):
            row(experiment, context, prepared, rows[0])
        provider.assert_not_called()


def test_subset_failed_only_empty_is_not_broadcast_and_new_key_gets_new_execution(tenants):
    actor = tenants["a"]
    experiment, configs, _, _ = subset_world(actor)
    body = {"source_ids": [str(configs[0].experiment_dataset_id)], "failed_only": True}
    before = state_of(experiment)
    response, start = subset_post(actor, experiment, body)
    assert response.status_code == 400, response.content
    start.assert_not_called()
    assert state_of(experiment) == before
    body["failed_only"] = False
    named = {"side_effect": lambda **kwargs: "wf-" + kwargs["admission_id"]}
    response, _ = subset_post(actor, experiment, body, key="first", **named)
    assert response.status_code == 200, response.content
    first = state_of(experiment)["admission_id"]
    complete_current(experiment)
    response, _ = subset_post(actor, experiment, body, key="second", **named)
    assert response.status_code == 200, response.content
    second = state_of(experiment)
    assert second["admission_id"] != first
    response, start = subset_post(actor, experiment, body, key="first", **named)
    assert response.status_code == 200 and response.json()["result"]["workflow_id"] == "wf-" + first
    start.assert_not_called()
    assert state_of(experiment) == second


def test_p01_explicit_subset_binding_is_hashed_and_does_not_narrow_snapshot_validation(tenants):
    from model_hub.tests.test_native_run_binding import bind, make_native_world
    actor = tenants["a"]
    _, version = fixtures.prompt(actor)
    world = make_native_world(actor["org"], actor["workspace"], actor["user"], version)
    world["config"].model = "gpt-4o-mini"
    world["config"].save(update_fields=["model"])
    rows = [str(row.id) for row in world["row_map"].values()]
    run = bind(world, snapshot_row_ids=rows[:1])
    from model_hub.services.experiment_run_writes import _planned
    assert len(_planned(run)) == 1
    assert bind(world, snapshot_row_ids=rows[:1]).id == run.id
    with pytest.raises(RunWriteError):
        bind(world, snapshot_row_ids=rows[1:])
    with pytest.raises(RunWriteError):
        bind(world, execution_id=uuid.uuid4(), snapshot_row_ids=[])
    with pytest.raises(RunWriteError):
        bind(world, execution_id=uuid.uuid4(), snapshot_row_ids=[str(uuid.uuid4())])
    Row.no_workspace_objects.filter(pk=rows[1]).update(deleted=True)
    with pytest.raises(RunWriteError):
        bind(world, execution_id=uuid.uuid4(), snapshot_row_ids=rows[:1])


def test_subset_unsupported_requests_keep_classic_executor_without_plan(tenants):
    from model_hub.models.native_experiment_runs import NativeExperimentSnapshot
    from model_hub.services.experiment_submit import stage_subset_submission
    actor = tenants["a"]
    experiment, configs, columns, rows = subset_world(actor)
    before = state_of(experiment)
    input_column = next(iter(NativeExperimentSnapshot.objects.get(experiment=experiment).column_map.values()))
    with transaction.atomic(), pytest.raises(ValueError, match="Evaluator selectors"):
        stage_subset_submission(experiment, {"user_eval_metric_ids": [str(uuid.uuid4())]}, actor["workspace"])
    unsupported = [
        {"source_ids": [str(uuid.uuid4())]},
        {"cells": [{"column_id": input_column, "row_id": rows[0]}]},
    ]
    with transaction.atomic():
        for data in unsupported:
            assert stage_subset_submission(experiment, data, actor["workspace"]) is None
    assert state_of(experiment) == before
    assert NativeRunBinding.objects.filter(snapshot__experiment=experiment).count() == 2


def test_subset_without_key_retries_active_request_but_repeats_terminal(tenants):
    actor = tenants["a"]
    experiment, configs, _, _ = subset_world(actor)
    body = {"source_ids": [str(configs[0].experiment_dataset_id)]}
    response, lost = subset_post(actor, experiment, body, key=None, side_effect=RuntimeError("ack lost"))
    assert response.status_code == 503, response.content
    pending = state_of(experiment)
    response, retry = subset_post(actor, experiment, body, key=None)
    assert response.status_code == 200, response.content
    assert state_of(experiment)["admission_id"] == pending["admission_id"]
    assert retry.call_args == lost.call_args
    complete_current(experiment)
    response, again = subset_post(actor, experiment, body, key=None)
    assert response.status_code == 200, response.content
    assert state_of(experiment)["admission_id"] != pending["admission_id"]
    again.assert_called_once()


# P02A-R4-COR-001: RED source first; no test execution in this packet.
@pytest.mark.parametrize("start_observed", [True, False])
def test_pending_subset_stop_tombstones_key_and_releases_new_request(tenants, start_observed):
    from copy import deepcopy

    from model_hub.models.develop_dataset import Dataset
    from model_hub.services.experiment_submit import (
        ADMISSIONS_KEY,
        SubmissionCancelled,
        native_admission_ready,
    )

    actor = tenants["a"]
    experiment, configs, columns, rows = subset_world(actor)
    body = {"cells": [{"column_id": str(columns[0].id), "row_id": rows[0]}]}
    old_receipts = {str(b.run_id): list(b.run.items.values()) for b in
                    NativeRunBinding.objects.filter(snapshot__experiment=experiment)}
    response, initial = subset_post(actor, experiment, body, key="stopped-K1",
                                   side_effect=RuntimeError("ack lost" if start_observed else "not started"))
    assert response.status_code == 503, response.content
    pending = deepcopy(state_of(experiment))
    contexts = pending["workflow_args"]["native_runs"]
    with patch(PROVIDER) as provider, patch(SUBSET_START) as starter, patch(START) as whole:
        stopped = actor["client"].post(f"/model-hub/experiments/v2/{experiment.id}/stop/", {}, format="json")
        assert stopped.status_code == 200, stopped.content
        cancelled = state_of(experiment)
        assert cancelled["state"] == "cancelled"
        assert cancelled["admission_id"] == pending["admission_id"]
        assert cancelled["execution_plan"] == pending["execution_plan"]
        assert cancelled["workflow_args"] == pending["workflow_args"]
        assert cancelled["request_hash"] == pending["request_hash"]
        with pytest.raises(SubmissionCancelled):
            native_admission_ready(str(experiment.id), contexts)
        with pytest.raises(SubmissionCancelled):
            admit_submission(str(experiment.id), pending["admission_id"])
        provider.assert_not_called(); starter.assert_not_called(); whole.assert_not_called()
    for ctx in contexts.values():
        run = NativeRunBinding.objects.get(run_id=ctx["run_id"]).run
        assert run.status == "cancelled" and run.completed_at is not None and run.items.count() == 0
    replay, retry = subset_post(actor, experiment, body, key="stopped-K1")
    assert replay.status_code == 409, replay.content
    retry.assert_not_called()
    fresh, admitted = subset_post(actor, experiment, body, key="fresh-K2")
    assert fresh.status_code == 200, fresh.content
    admitted.assert_called_once()
    current = deepcopy(state_of(experiment))
    assert current["admission_id"] != pending["admission_id"]
    # Late Stop delivery from the old workflow must not cancel/reset the new plan.
    before_cells = list(Cell.no_workspace_objects.filter(dataset=experiment.snapshot_dataset).order_by("id").values())
    result = activities._cleanup_native_execution_sync(str(experiment.id), contexts, stopped=True)
    assert result["status"] == "COMPLETED" and result["cells_cleaned"] == 0
    assert state_of(experiment) == current
    assert list(Cell.no_workspace_objects.filter(dataset=experiment.snapshot_dataset).order_by("id").values()) == before_cells
    journal = Dataset.no_workspace_objects.get(pk=experiment.snapshot_dataset_id).dataset_config[ADMISSIONS_KEY]
    assert journal[pending["admission_id"]] == cancelled
    for binding in NativeRunBinding.objects.filter(run_id__in=old_receipts):
        assert list(binding.run.items.values()) == old_receipts[str(binding.run_id)]
    ctx = next(iter(current["workflow_args"]["native_runs"].values()))
    prepared = activities._setup_prompt_v2_sync(str(experiment.id), str(experiment.snapshot_dataset_id),
                                              ctx["config_id"], native_context=ctx)
    with patch(PROVIDER, autospec=True, return_value=("fresh-only", {})) as provider:
        row(experiment, ctx, prepared, rows[0]); row(experiment, ctx, prepared, rows[0])
        provider.assert_called_once()
    finish(ctx, prepared)


@pytest.mark.parametrize("operation", ["create", "update", "append_variant", "rerun"])
def test_pending_whole_admission_stop_releases_without_restarting_old_intent(native_world, operation):
    from copy import deepcopy

    from model_hub.services.experiment_submit import (
        SubmissionCancelled,
        cancel_pending_submission,
    )
    w = native_world; experiment = w["experiment"]; experiment.refresh_from_db()
    args = {"max_concurrent_rows": 10}
    if operation in {"update", "append_variant"}:
        args["rerun_prompt_config_ids"] = [str(w["config"].id)]
    with transaction.atomic():
        pending = stage_submission(experiment, {"old": True}, operation,
                                   authorized_workspace=w["dataset"].workspace, **args)
    with patch(START, side_effect=RuntimeError("ack lost")):
        with pytest.raises(RuntimeError):
            admit_submission(str(experiment.id))
    contexts = deepcopy(pending["workflow_args"]["native_runs"])
    with patch(START) as starter, patch(PROVIDER) as provider:
        tombstone = cancel_pending_submission(str(experiment.id))
        assert tombstone["state"] == "cancelled"
        with pytest.raises(SubmissionCancelled):
            admit_submission(str(experiment.id), pending["admission_id"])
        starter.assert_not_called(); provider.assert_not_called()
    experiment.refresh_from_db()
    with transaction.atomic():
        fresh = stage_submission(experiment, {"new": True}, operation,
                                 authorized_workspace=w["dataset"].workspace, **args)
    with patch(START, return_value="new-workflow") as starter:
        admit_submission(str(experiment.id), fresh["admission_id"])
        starter.assert_called_once()
    current = state_of(experiment)
    activities._cleanup_native_execution_sync(str(experiment.id), contexts, stopped=True)
    activities._cleanup_native_execution_sync(str(experiment.id), contexts, stopped=False)
    assert state_of(experiment) == current
    assert NativeRunBinding.objects.get(run_id=next(iter(contexts.values()))["run_id"]).run.status == "cancelled"


def test_pending_stop_rejects_foreign_or_incomplete_context_without_release(tenants):
    from copy import deepcopy
    actor = tenants["a"]
    experiment, configs, columns, rows = subset_world(actor)
    response, _ = subset_post(actor, experiment, {"source_ids": [str(c.experiment_dataset_id) for c in configs]},
                             side_effect=RuntimeError("ack lost"))
    assert response.status_code == 503
    pending = deepcopy(state_of(experiment)); contexts = pending["workflow_args"]["native_runs"]
    before = list(Cell.no_workspace_objects.filter(dataset=experiment.snapshot_dataset).order_by("id").values())
    foreign = tenants["b"]["client"].post(f"/model-hub/experiments/v2/{experiment.id}/stop/", {}, format="json")
    assert foreign.status_code == 404
    one_key = next(iter(contexts))
    for bad in ({one_key: contexts[one_key]}, {k: {**v, "plan_hash": "tampered"} for k, v in contexts.items()}):
        with pytest.raises(RunWriteError):
            activities._cleanup_native_execution_sync(str(experiment.id), bad, stopped=True)
    assert state_of(experiment) == pending
    assert list(Cell.no_workspace_objects.filter(dataset=experiment.snapshot_dataset).order_by("id").values()) == before
    assert all(b.run.status == "draft" for b in NativeRunBinding.objects.filter(execution_id=pending["admission_id"]))


def test_failure_cleanup_does_not_release_unacknowledged_plan(native_world):
    w = native_world; w["experiment"].refresh_from_db()
    with transaction.atomic():
        pending = stage_submission(w["experiment"], {}, "create", authorized_workspace=w["dataset"].workspace)
    result = activities._cleanup_native_execution_sync(str(w["experiment"].id),
                        pending["workflow_args"]["native_runs"], stopped=False)
    assert result["cells_cleaned"] == 0
    assert state_of(w["experiment"]) == pending
    assert NativeRunBinding.objects.get(execution_id=pending["admission_id"]).run.status == "draft"


@pytest.mark.parametrize("eval_only", [False, True])
def test_evaluator_subset_commits_plan_and_preserves_existing_output(native_world, eval_only):
    from model_hub.services.experiment_submit import stage_subset_submission
    from model_hub.tests.test_native_run_binding import add_native_eval
    w = native_world; w["experiment"].refresh_from_db()
    with transaction.atomic():
        stage_submission(w["experiment"], {}, "create", authorized_workspace=w["dataset"].workspace)
    with patch(START, return_value="source-output"):
        admit_submission(str(w["experiment"].id))
    complete_current(w["experiment"], "prior output")
    prior = {str(binding.run_id): list(binding.run.items.order_by("id").values()) for binding in
             NativeRunBinding.objects.filter(snapshot__experiment=w["experiment"])}
    metric, pin = add_native_eval(w)
    selected_row = str(next(iter(w["row_map"].values())).id)
    body = ({"user_eval_metric_ids": [str(metric.id)]} if eval_only else
            {"cells": [{"column_id": str(w["output_column"].id), "row_id": selected_row}]})
    before = list(Cell.no_workspace_objects.filter(dataset=w["snapshot"]).order_by("id").values())
    with transaction.atomic():
        state = stage_subset_submission(w["experiment"], body, w["dataset"].workspace, "eval-subset-key")
    assert state is not None and state["state"] == "pending"
    targets = state["execution_plan"]["targets"]
    assert targets
    for target in targets.values():
        assert {(p["metric_id"], p["evaluator_version_id"]) for p in target["evaluator_versions"]} == {
            (str(metric.id), str(pin.id))}
        if not eval_only:
            assert target["snapshot_row_ids"] == [selected_row]
    if eval_only:
        assert state["workflow_args"].get("prompt_config_ids", []) == []
    assert list(Cell.no_workspace_objects.filter(dataset=w["snapshot"]).order_by("id").values()) == before
    with transaction.atomic():
        assert stage_subset_submission(w["experiment"], body, w["dataset"].workspace, "eval-subset-key") == state
    for binding in NativeRunBinding.objects.filter(run_id__in=prior):
        assert list(binding.run.items.order_by("id").values()) == prior[str(binding.run_id)]


def test_attached_evaluator_pin_deleted_after_admission_refuses_before_provider(native_world):
    from model_hub.tests.test_native_run_binding import add_native_eval
    w = native_world; w["experiment"].refresh_from_db()
    metric, pin = add_native_eval(w)
    with transaction.atomic():
        state = stage_submission(w["experiment"], {}, "create", authorized_workspace=w["dataset"].workspace)
    context = state["workflow_args"]["native_runs"][str(w["config"].id)]
    with patch(START, return_value="pinned-eval"):
        admit_submission(str(w["experiment"].id))
    pin.delete()
    with patch(PROVIDER) as provider:
        with pytest.raises(RunWriteError):
            activities._setup_prompt_v2_sync(str(w["experiment"].id), str(w["snapshot"].id),
                                            str(w["config"].id), native_context=context)
        provider.assert_not_called()
    assert not NativeRunBinding.objects.get(run_id=context["run_id"]).run.items.exists()


# Item 4(b), r6: actual engine/billing boundaries only are replaced. Admission,
# immutable row resolution, evaluator mapping, formatting and recording are real.
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock


def attach_executable_native_eval(w):
    from model_hub.tests.test_native_run_binding import add_native_eval
    metric, pin = add_native_eval(w)
    pin.config_snapshot = {"eval_type_id": "NativeFixture", "output": "score",
                           "required_keys": ["input", "output"]}
    pin.save(update_fields=["config_snapshot"])
    template = metric.template
    template.config = dict(pin.config_snapshot)
    template.save(update_fields=["config"])
    metric.config = {"mapping": {"input": str(w["source_column"].id), "output": "output"}, "config": {}}
    metric.save(update_fields=["config"])
    return metric, pin


@contextmanager
def native_eval_transport(*, score=0, engine_error=False, billing_denied=False):
    from types import SimpleNamespace

    from evaluations.engine import instance, registry
    from model_hub.views import eval_runner

    # Build the real built-ins before adding this one test-only identity. Do not
    # replace get_eval_class: unrelated or misspelled types must still be refused.
    registry._build_registry()
    real_create_instance = instance.create_eval_instance
    # This evaluator has no custom formatter: keep the real dataset formatting.
    # A bare MagicMock invents a callable format_result and returns a mock score.
    engine = MagicMock(format_result=None)
    engine.cost = {}; engine.token_usage = {}
    engine.run.return_value = SimpleNamespace(eval_results=[{
        "metrics": [{"id": "score", "value": score}], "reason": "actual engine result", "failure": False,
    }])
    if engine_error:
        engine.run.side_effect = RuntimeError("engine unavailable")
    billing = MagicMock()
    def bill(*args, **kwargs):
        if billing_denied:
            raise ValueError("billing refused")
        return SimpleNamespace(status=eval_runner.APICallStatusChoices.PROCESSING.value,
            config=json.dumps(kwargs["config"], default=str), log_id=uuid.uuid4(), save=MagicMock())
    billing.side_effect = bill

    def create_fixture_instance(eval_class, *args, **kwargs):
        if eval_class is _NativeFixtureEvaluator:
            return engine, None
        return real_create_instance(eval_class, *args, **kwargs)

    with ExitStack() as stack:
        stack.enter_context(patch.dict(registry._REGISTRY, {"NativeFixture": _NativeFixtureEvaluator}))
        stack.enter_context(patch.object(instance, "create_eval_instance", side_effect=create_fixture_instance))
        stack.enter_context(patch.object(eval_runner, "log_and_deduct_cost_for_api_request", billing))
        stack.enter_context(patch.object(eval_runner, "refund_cost_for_api_call", MagicMock()))
        yield engine, billing


@pytest.mark.parametrize("operation", ["create", "append_variant", "update", "rerun"])
def test_evaluator_actual_outcomes_use_pinned_inputs_before_terminal_receipt(native_world, operation):
    w = native_world; w["experiment"].refresh_from_db()
    metric, pin = attach_executable_native_eval(w)
    args = {} if operation in {"create", "rerun"} else {
        "rerun_prompt_config_ids": [str(w["config"].id)], "rerun_eval_template_ids": []}
    with transaction.atomic():
        state = stage_submission(w["experiment"], {}, operation,
                                authorized_workspace=w["dataset"].workspace, **args)
    with patch(START, return_value="native-eval-admitted"):
        admit_submission(str(w["experiment"].id), state["admission_id"])
    # Mutable source/snapshot cells must not become the evaluator's input.
    Cell.no_workspace_objects.filter(dataset=w["snapshot"]).update(value="mutated after admission")
    context, prepared = setup(w["experiment"])
    with native_eval_transport(score=0) as (engine, billing), patch(PROVIDER, return_value=("actual output", {})) as provider:
        for row_id in prepared["row_ids"]:
            assert row(w["experiment"], context, prepared, row_id)["status"] == "COMPLETED"
        finish(context, prepared)
        receipts = list(ExperimentRunItem.no_workspace_objects.filter(run_id=context["run_id"]).order_by("id").values())
        for row_id in prepared["row_ids"]:
            row(w["experiment"], context, prepared, row_id)
        assert provider.call_count == engine.run.call_count == billing.call_count == len(prepared["row_ids"])
    assert {call.kwargs["input"] for call in engine.run.call_args_list} == {"input-0", "input-1"}
    assert {call.kwargs["output"] for call in engine.run.call_args_list} == {"actual output"}
    run = NativeRunBinding.objects.get(run_id=context["run_id"]).run
    assert run.status == "completed"
    for item in run.items.all():
        assert item.output == "actual output" and item.status == "completed"
        assert item.score_refs == [{"metric_id": str(metric.id), "evaluator_version_id": str(pin.id),
                                   "status": "assessed", "value": 0}]
    assert receipts == list(run.items.order_by("id").values())


@pytest.mark.parametrize("failure", ["evaluator", "billing", "prompt"])
def test_evaluator_failures_are_per_row_evidence_not_missing_or_false_scores(native_world, failure):
    w = native_world; w["experiment"].refresh_from_db()
    metric, pin = attach_executable_native_eval(w)
    with transaction.atomic():
        stage_submission(w["experiment"], {}, "create", authorized_workspace=w["dataset"].workspace)
    with patch(START, return_value="failure-admitted"):
        admit_submission(str(w["experiment"].id))
    context, prepared = setup(w["experiment"])
    with native_eval_transport(engine_error=failure == "evaluator", billing_denied=failure == "billing") as (engine, billing), patch(
        PROVIDER, return_value=("output", {"failure": True} if failure == "prompt" else {}),
    ):
        for row_id in prepared["row_ids"]:
            row(w["experiment"], context, prepared, row_id)
        finish(context, prepared)
    run = NativeRunBinding.objects.get(run_id=context["run_id"]).run
    assert run.completed_at is not None
    for item in run.items.all():
        assert len(item.score_refs) == 1
        ref = item.score_refs[0]
        assert ref["metric_id"] == str(metric.id) and ref["evaluator_version_id"] == str(pin.id)
        assert ref["status"] == ("not_assessed" if failure == "prompt" else "error")
        assert ref.get("value") is None
        assert item.status == ("failed" if failure == "prompt" else "completed")
    if failure in {"billing", "prompt"}:
        engine.run.assert_not_called()
    if failure == "prompt":
        billing.assert_not_called()


@pytest.mark.parametrize("operation", ["update", "subset"])
def test_eval_only_worker_uses_exact_prior_receipt_without_prompt_or_output_reset(native_world, operation):
    from model_hub.services.experiment_submit import stage_subset_submission
    w = native_world; w["experiment"].refresh_from_db()
    with transaction.atomic():
        stage_submission(w["experiment"], {}, "create", authorized_workspace=w["dataset"].workspace)
    with patch(START, return_value="original-prompt"):
        admit_submission(str(w["experiment"].id))
    complete_current(w["experiment"], "immutable output")
    parent = NativeRunBinding.objects.get(snapshot__experiment=w["experiment"])
    previous = list(parent.run.items.order_by("id").values())
    metric, pin = attach_executable_native_eval(w)
    with transaction.atomic():
        if operation == "subset":
            state = stage_subset_submission(w["experiment"], {"user_eval_metric_ids": [str(metric.id)]},
                                            w["dataset"].workspace, "eval-only-key")
        else:
            state = stage_submission(w["experiment"], {}, "update", authorized_workspace=w["dataset"].workspace,
                rerun_prompt_config_ids=[], rerun_agent_config_ids=[], rerun_eval_template_ids=[str(metric.id)])
    for target in state["execution_plan"]["targets"].values():
        assert target["execution_mode"] == "eval_only"
        assert {source["run_id"] for source in target["evaluation_sources"].values()} == {str(parent.run_id)}
    starter = "tfc.temporal.experiments.start_rerun_cells_v2_workflow" if operation == "subset" else START
    with patch(starter, return_value="eval-only-admitted"):
        admit_submission(str(w["experiment"].id), state["admission_id"])
    Cell.no_workspace_objects.filter(column=w["output_column"]).update(value="mutable decoy")
    cells_before = list(Cell.no_workspace_objects.filter(column=w["output_column"]).order_by("id").values())
    context, prepared = setup(w["experiment"])
    with native_eval_transport(score=1) as (engine, _), patch(PROVIDER) as provider:
        for row_id in prepared["row_ids"]:
            row(w["experiment"], context, prepared, row_id)
        finish(context, prepared)
        for row_id in prepared["row_ids"]:
            row(w["experiment"], context, prepared, row_id)
        provider.assert_not_called()
    assert engine.run.call_count == len(prepared["row_ids"])
    assert {call.kwargs["output"] for call in engine.run.call_args_list} == {"immutable output"}
    current = NativeRunBinding.objects.get(run_id=context["run_id"])
    assert current.run_id != parent.run_id and current.run.status == "completed"
    assert all(item.output == "immutable output" and item.token_usage is None and item.cost is None
               for item in current.run.items.all())
    assert previous == list(parent.run.items.order_by("id").values())
    assert cells_before == list(Cell.no_workspace_objects.filter(column=w["output_column"]).order_by("id").values())


def test_evaluator_binding_drift_is_rejected_before_claim_or_provider(native_world):
    w = native_world; w["experiment"].refresh_from_db()
    metric, _ = attach_executable_native_eval(w)
    with transaction.atomic():
        state = stage_submission(w["experiment"], {}, "create", authorized_workspace=w["dataset"].workspace)
    with patch(START, return_value="admitted"):
        admit_submission(str(w["experiment"].id))
    context = state["workflow_args"]["native_runs"][str(w["config"].id)]
    metric.config = {"mapping": {"output": "different"}, "config": {}}
    metric.save(update_fields=["config"])
    with patch(PROVIDER) as provider, pytest.raises(RunWriteError):
        activities._setup_prompt_v2_sync(str(w["experiment"].id), str(w["snapshot"].id),
                                        str(w["config"].id), native_context=context)
    provider.assert_not_called()
    assert not ExperimentRunItem.no_workspace_objects.filter(run_id=context["run_id"]).exists()



def test_eval_only_cell_selection_keeps_metric_row_pairs_exact(native_world):
    from model_hub.models.choices import SourceChoices
    from model_hub.services.experiment_submit import stage_subset_submission
    w = native_world; w["experiment"].refresh_from_db()
    with transaction.atomic():
        stage_submission(w["experiment"], {}, "create", authorized_workspace=w["dataset"].workspace)
    with patch(START, return_value="initial"):
        admit_submission(str(w["experiment"].id))
    complete_current(w["experiment"], "original-output")
    a, _ = attach_executable_native_eval(w)
    b, _ = attach_executable_native_eval(w)
    rows = sorted(str(row.id) for row in w["row_map"].values())
    columns = []
    for metric in (a, b):
        column = Column.no_workspace_objects.create(dataset=w["snapshot"], name=str(metric.id), data_type="float",
            source=SourceChoices.EXPERIMENT_EVALUATION.value,
            source_id=f"{w['variant'].id}-{w['output_column'].id}-sourceid-{metric.id}")
        w["variant"].columns.add(column)
        for row_id in rows:
            Cell.no_workspace_objects.create(dataset=w["snapshot"], column=column, row_id=row_id,
                                             value="stale", status=CellStatus.ERROR.value)
        columns.append(column)
    body = {"cells": [{"column_id": str(columns[0].id), "row_id": rows[0]},
                      {"column_id": str(columns[1].id), "row_id": rows[1]}], "failed_only": True}
    untouched = [Cell.no_workspace_objects.get(column=columns[0], row_id=rows[1]).id,
                 Cell.no_workspace_objects.get(column=columns[1], row_id=rows[0]).id]
    before = list(Cell.no_workspace_objects.filter(id__in=untouched).order_by("id").values())
    with transaction.atomic():
        state = stage_subset_submission(w["experiment"], body, w["dataset"].workspace, "exact-eval-cells")
    target = state["execution_plan"]["targets"][str(w["config"].id)]
    assert target["evaluator_rows"] == {rows[0]: [str(a.id)], rows[1]: [str(b.id)]}
    with patch(SUBSET_START, return_value="exact-eval-cells-admitted"):
        admit_submission(str(w["experiment"].id), state["admission_id"])
    context, prepared = setup(w["experiment"])
    with native_eval_transport(score=0.25) as (engine, _), patch(PROVIDER) as provider:
        for row_id in prepared["row_ids"]:
            row(w["experiment"], context, prepared, row_id)
        finish(context, prepared)
        provider.assert_not_called()
    assert engine.run.call_count == 2
    assert before == list(Cell.no_workspace_objects.filter(id__in=untouched).order_by("id").values())
    for item in ExperimentRunItem.no_workspace_objects.filter(run_id=context["run_id"]):
        assert sorted(ref["status"] for ref in item.score_refs) == ["assessed", "not_assessed"]
        assert next(ref for ref in item.score_refs if ref["status"] == "assessed")["value"] == 0.25
        assert next(ref for ref in item.score_refs if ref["status"] == "not_assessed")["reason"] == "not_selected"


def test_eval_only_request_admits_canonical_context_and_projects_lineage(tenants):
    from model_hub.models.native_experiment_runs import NativeExperimentSnapshot
    from model_hub.services import results_catalog
    actor = tenants["a"]
    experiment, _ = seed_native_admission(actor)
    complete_current(experiment, "request-original")
    parent = NativeRunBinding.objects.get(snapshot__experiment=experiment)
    snapshot = NativeExperimentSnapshot.objects.get(experiment=experiment)
    w = {"dataset": experiment.dataset, "snapshot": experiment.snapshot_dataset,
         "experiment": experiment, "created_by": actor["user"],
         "source_column": Column.no_workspace_objects.get(pk=next(iter(snapshot.column_map)))}
    metric, _ = attach_executable_native_eval(w)
    response, starter = subset_post(actor, experiment, {"user_eval_metric_ids": [str(metric.id)]}, "request-eval-only")
    assert response.status_code == 200, response.content
    state = state_of(experiment)
    assert state["workflow_args"]["eval_only"] is True and not state["workflow_args"]["prompt_config_ids"]
    assert starter.call_args.kwargs["native_runs"] == state["workflow_args"]["native_runs"]
    context, prepared = setup(experiment)
    with native_eval_transport(score=1), patch(PROVIDER) as provider:
        for row_id in prepared["row_ids"]:
            row(experiment, context, prepared, row_id)
        finish(context, prepared)
        provider.assert_not_called()
    run = NativeRunBinding.objects.get(run_id=context["run_id"]).run
    projected = results_catalog.project_run(run, include=("lineage",))
    assert projected["kind"] == "evaluation"
    assert projected["parent_run_id"] == str(parent.run_id)
    assert projected["parent_run_ids"] == [str(parent.run_id)]
    assert {ref["run_id"] for ref in projected["evaluation_sources"].values()} == {str(parent.run_id)}


def test_closed_evaluator_receipts_remain_readable_after_binding_config_edit(native_world):
    w = native_world; w["experiment"].refresh_from_db()
    metric, _ = attach_executable_native_eval(w)
    with transaction.atomic():
        stage_submission(w["experiment"], {}, "create", authorized_workspace=w["dataset"].workspace)
    with patch(START, return_value="closed-evaluator"):
        admit_submission(str(w["experiment"].id))
    with native_eval_transport(score=0):
        complete_current(w["experiment"])
    run = NativeRunBinding.objects.get(snapshot__experiment=w["experiment"]).run
    before = list(run.items.order_by("id").values())
    metric.config = {"mapping": {"output": "changed"}, "config": {}}
    metric.save(update_fields=["config"])
    assert project_run(run, dataset=w["dataset"])["run_id"] == str(run.id)
    assert before == list(run.items.order_by("id").values())



def test_pending_evaluator_config_drift_cannot_block_explicit_stop(native_world):
    from model_hub.services.experiment_submit import cancel_pending_submission
    w = native_world; w["experiment"].refresh_from_db()
    metric, _ = attach_executable_native_eval(w)
    with transaction.atomic():
        state = stage_submission(w["experiment"], {}, "create", authorized_workspace=w["dataset"].workspace)
    contexts = state["workflow_args"]["native_runs"]
    metric.config = {"mapping": {}, "config": {"changed": True}}
    metric.save(update_fields=["config"])
    stopped = cancel_pending_submission(str(w["experiment"].id))
    assert stopped["state"] == "cancelled"
    assert stopped["workflow_args"]["native_runs"] == contexts
    for context in contexts.values():
        run = NativeRunBinding.objects.get(run_id=context["run_id"]).run
        assert run.status == "cancelled" and not run.items.exists()



@pytest.mark.parametrize("policy", ["zero", "exclude"])
def test_failed_native_output_preserves_explicit_error_policy_without_running_evaluator(native_world, policy):
    w = native_world; w["experiment"].refresh_from_db()
    w["experiment"].run_policy = {"error_policy": policy}
    w["experiment"].save(update_fields=["run_policy"])
    attach_executable_native_eval(w)
    with transaction.atomic():
        stage_submission(w["experiment"], {}, "create", authorized_workspace=w["dataset"].workspace)
    with patch(START, return_value="error-policy-admitted"):
        admit_submission(str(w["experiment"].id))
    context, prepared = setup(w["experiment"])
    with native_eval_transport() as (engine, billing), patch(PROVIDER, side_effect=RuntimeError("provider failed")):
        for row_id in prepared["row_ids"]:
            row(w["experiment"], context, prepared, row_id)
        finish(context, prepared)
        engine.run.assert_not_called(); billing.assert_not_called()
    for item in ExperimentRunItem.no_workspace_objects.filter(run_id=context["run_id"]):
        assert item.status == "failed"
        ref = item.score_refs[0]
        if policy == "zero":
            assert ref["status"] == "assessed" and ref["value"] == 0
            assert ref["provenance"] == "error_policy"
        else:
            assert ref["status"] == "not_assessed" and ref.get("value") is None
        assert "not run" in ref["explanation"]


# r7 COR-002: exercise the outer usage preflight, not inner per-call billing.
@contextmanager
def native_preflight(checker):
    import sys
    from types import ModuleType
    module = ModuleType("ee.usage.services.metering")
    module.check_usage = checker
    # Test boundary only; no enterprise module source or live quota service.
    with patch.dict(sys.modules, {module.__name__: module}):
        yield


def prepared_native_refusal(w, eval_only):
    from model_hub.services.experiment_submit import stage_subset_submission
    w["experiment"].refresh_from_db()
    if eval_only:
        with transaction.atomic():
            stage_submission(w["experiment"], {}, "create", authorized_workspace=w["dataset"].workspace)
        with patch(START, return_value="refusal-parent"):
            admit_submission(str(w["experiment"].id))
        complete_current(w["experiment"], "pinned parent output")
    metric, pin = attach_executable_native_eval(w)
    with transaction.atomic():
        if eval_only:
            state = stage_subset_submission(w["experiment"], {"user_eval_metric_ids": [str(metric.id)]},
                w["dataset"].workspace, "refusal-eval-only")
        else:
            state = stage_submission(w["experiment"], {}, "create", authorized_workspace=w["dataset"].workspace)
    with patch(SUBSET_START if eval_only else START, return_value="refusal-admitted"):
        admit_submission(str(w["experiment"].id), state["admission_id"])
    context, prepared = setup(w["experiment"])
    return metric, pin, context, prepared


def native_result_reason(w, metric):
    from model_hub.models.choices import SourceChoices
    result = Column.no_workspace_objects.get(dataset=w["snapshot"], deleted=False,
        source=SourceChoices.EXPERIMENT_EVALUATION.value,
        source_id=f"{w['variant'].id}-{w['output_column'].id}-sourceid-{metric.id}")
    reason = Column.no_workspace_objects.get(dataset=w["snapshot"], deleted=False,
        source=SourceChoices.EVALUATION_REASON.value, source_id=f"{result.id}-sourceid-{metric.id}")
    return result, reason


@pytest.mark.parametrize("eval_only", [False, True])
@pytest.mark.parametrize("refusal", ["preflight_denied", "preflight_raises", "adapter_raises"])
def test_native_refusal_settles_reason_and_result_before_canonical_completion(native_world, eval_only, refusal):
    from model_hub.models.choices import StatusType
    from model_hub.views.eval_runner import NativeEvaluationRunner
    w = native_world
    metric, pin, context, prepared = prepared_native_refusal(w, eval_only)
    result, reason = native_result_reason(w, metric)
    cells = Cell.no_workspace_objects.filter(dataset=w["snapshot"], column__in=[result, reason], deleted=False)
    assert cells.count() == 2 * len(prepared["row_ids"])
    assert set(cells.values_list("status", flat=True)) == {CellStatus.RUNNING.value}
    output_before = list(Cell.no_workspace_objects.filter(column=w["output_column"]).order_by("id").values())
    checker = MagicMock(return_value=SimpleNamespace(allowed=False))
    if refusal == "preflight_raises":
        checker.side_effect = RuntimeError("quota service refused")
    with ExitStack() as stack:
        stack.enter_context(native_preflight(checker))
        engine, billing = stack.enter_context(native_eval_transport())
        provider = stack.enter_context(patch(PROVIDER, return_value=("output", {})))
        if refusal == "adapter_raises":
            # Worker fallback must settle cells too, not fabricate only score_refs.
            stack.enter_context(patch.object(NativeEvaluationRunner, "evaluate_native_row",
                                            side_effect=ValueError("adapter refused")))
        for row_id in prepared["row_ids"]:
            row(w["experiment"], context, prepared, row_id)
            assert set(cells.filter(row_id=row_id).values_list("status", flat=True)) == {CellStatus.ERROR.value}
        assert finish(context, prepared)["is_complete"] is True
        assert activities._check_experiment_status_sync(str(w["experiment"].id))["is_complete"] is True
        receipts = list(ExperimentRunItem.no_workspace_objects.filter(run_id=context["run_id"]).order_by("id").values())
        for row_id in prepared["row_ids"]:
            row(w["experiment"], context, prepared, row_id)
        engine.run.assert_not_called()
        billing.assert_not_called()
        if eval_only:
            provider.assert_not_called()
        else:
            assert provider.call_count == len(prepared["row_ids"])
    if refusal == "adapter_raises":
        checker.assert_not_called()
    else:
        assert checker.call_count == len(prepared["row_ids"])
    run = NativeRunBinding.objects.get(run_id=context["run_id"]).run
    assert run.status == "completed" and run.completed_at is not None
    assert run.items.count() == len(prepared["row_ids"])
    for item in run.items.all():
        # The error score carries the reason text its result cell stores.
        assert item.score_refs == [{"metric_id": str(metric.id), "evaluator_version_id": str(pin.id),
                                   "status": "error", "reason": "evaluator_execution_failed",
                                   "explanation": "Native evaluator execution failed"}]
    assert receipts == list(run.items.order_by("id").values())
    result.refresh_from_db(); reason.refresh_from_db(); w["variant"].refresh_from_db(); w["experiment"].refresh_from_db()
    assert result.status == reason.status == w["variant"].status == w["experiment"].status == StatusType.COMPLETED.value
    assert not cells.filter(status=CellStatus.RUNNING.value).exists()
    if eval_only:
        assert output_before == list(Cell.no_workspace_objects.filter(column=w["output_column"]).order_by("id").values())


@pytest.mark.parametrize("stop_kind", ["evaluator", "experiment"])
def test_native_preflight_refusal_preserves_stop_markers(native_world, stop_kind):
    from model_hub.models.choices import StatusType
    from model_hub.models.evals_metric import UserEvalMetric
    w = native_world
    metric, _, context, prepared = prepared_native_refusal(w, False)
    result, reason = native_result_reason(w, metric)
    selected_row = prepared["row_ids"][0]
    cells = Cell.no_workspace_objects.filter(dataset=w["snapshot"], column__in=[result, reason], row_id=selected_row)
    def refuse_after_stop(*args, **kwargs):
        if stop_kind == "evaluator":
            UserEvalMetric.no_workspace_objects.filter(pk=metric.id).update(status=StatusType.CANCELLED.value)
        else:
            ExperimentsTable.no_workspace_objects.filter(pk=w["experiment"].id).update(status=StatusType.CANCELLED.value)
        cells.update(status=CellStatus.ERROR.value, value="User stopped evaluation", value_infos='{"reason":"stopped"}')
        return SimpleNamespace(allowed=False)
    with native_preflight(MagicMock(side_effect=refuse_after_stop)), native_eval_transport() as (engine, billing), patch(
        PROVIDER, return_value=("output", {}),
    ):
        if stop_kind == "experiment":
            with pytest.raises(RunWriteError, match="cancelled"):
                row(w["experiment"], context, prepared, selected_row)
        else:
            row(w["experiment"], context, prepared, selected_row)
        engine.run.assert_not_called(); billing.assert_not_called()
    assert list(cells.values_list("value", flat=True)) == ["User stopped evaluation"] * 2
    assert set(cells.values_list("status", flat=True)) == {CellStatus.ERROR.value}
    items = ExperimentRunItem.no_workspace_objects.filter(run_id=context["run_id"])
    if stop_kind == "evaluator":
        assert items.get().score_refs[0]["reason"] == "evaluator_stopped"
    else:
        assert not items.filter(status__in=["completed", "failed", "skipped"]).exists()


def test_native_refusal_persistence_failure_cannot_seal_canonical_row(native_world):
    from model_hub.views.eval_runner import NativeEvaluationRunner
    w = native_world
    _, _, context, prepared = prepared_native_refusal(w, False)
    with native_preflight(MagicMock(return_value=SimpleNamespace(allowed=False))), patch(
        PROVIDER, return_value=("output", {}),
    ), patch.object(NativeEvaluationRunner, "record_execution_failure", side_effect=RuntimeError("write unavailable")):
        with pytest.raises(RuntimeError, match="write unavailable"):
            row(w["experiment"], context, prepared, prepared["row_ids"][0])
    assert not ExperimentRunItem.no_workspace_objects.filter(
        run_id=context["run_id"], status__in=["completed", "failed", "skipped"],
    ).exists()


# r8 design 4(c): regression sources first, intentionally not xfailed.
# No production graph bridge is enabled by this test-only checkpoint.
def r8_create_graph(actor, *, key="r8-graph-create", transport=None, graph_factory=None,
                    extra_prompt=False, prompt_chain=False):
    """Real authorized HTTP create; only workflow transport is replaced.

    Use the existing deterministic graph fixture with a real evaluator pin.
    The module fixture registers its engine before HTTP eval-column creation.
    """
    from model_hub.models.evals_metric import EvalTemplate, EvalTemplateVersion
    from model_hub.models.experiments import ExperimentAgentConfig
    from model_hub.tests import test_experiment_native_graph as graph_fixture

    source = actor["exp"]["dataset"]
    source_column = Column.no_workspace_objects.get(dataset=source, deleted=False)
    source_column.name = "query"
    source_column.save(update_fields=["name"])
    Cell.no_workspace_objects.filter(dataset=source, column=source_column).update(value="pinned graph input")
    graph, version, node = (graph_factory or graph_fixture.make_graph)(actor)
    body = graph_fixture.payload(actor, graph, version)
    if extra_prompt:
        template, prompt_version = fixtures.prompt(actor, "unrelated prompt")
        body["prompt_config"].append(fixtures.entry(template, prompt_version))
    if prompt_chain:
        body["user_eval_metrics"][0]["config"]["mapping"]["input"] = "prompt_chain"
    body["name"] = "r8-graph-" + uuid.uuid4().hex
    template = EvalTemplate.no_workspace_objects.get(pk=body["user_eval_metrics"][0]["template_id"])
    # Save a new default snapshot; payload already created the first version.
    template.config = {"eval_type_id": "NativeFixture", "output": "score",
                       "required_keys": ["input", "output"] if prompt_chain else ["output"]}
    template.save(update_fields=["config"])
    pin = EvalTemplateVersion.objects.create_version(
        eval_template=template,
        organization=actor["org"], workspace=actor["workspace"],
        config_snapshot=dict(template.config), criteria=template.criteria,
        user=actor["user"],
    )
    with patch(START, **(transport or {"return_value": "r8-graph-admitted"})) as start:
        response = actor["client"].post(
            graph_fixture.V2, body, format="json", HTTP_IDEMPOTENCY_KEY=key,
        )
    assert response.status_code == (503 if transport else 200), response.content
    experiment = ExperimentsTable.no_workspace_objects.get(name=body["name"])
    config = ExperimentAgentConfig.no_workspace_objects.get(experiment_dataset__experiment=experiment, deleted=False)
    return {"actor": actor, "experiment": experiment, "config": config, "node": node,
            "version": version, "pin": pin, "body": body, "key": key, "start": start}


def r8_graph_context(world):
    """Assert the actual r7 omission before touching any proposed new field."""
    experiment, config = world["experiment"], world["config"]
    state = state_of(experiment)
    contexts = state.get("workflow_args", {}).get("native_runs", {})
    assert str(config.id) in contexts, "graph admission must persist a canonical plan-bound context"
    context = contexts[str(config.id)]
    binding = NativeRunBinding.objects.get(run_id=context["run_id"])
    assert binding.native_config_id == config.id
    assert binding.run.task_kind == "graph"
    assert str(binding.execution_id) == context["execution_id"] == state["admission_id"]
    assert binding.run.agent_version_id == world["version"].id
    assert binding.run.metadata["execution_plan_id"] == context["plan_id"]
    target = state["execution_plan"]["targets"][str(config.id)]
    assert target["run_id"] == str(binding.run_id)
    assert target["task_kind"] == "graph"
    assert target["execution_mode"] == "graph"
    links = list(binding.snapshot.examples.select_related("version_item"))
    assert set(target["snapshot_row_ids"]) == {str(link.snapshot_row_id) for link in links}
    assert set(target["example_ids"]) == {str(link.version_item.example_id) for link in links}
    return context, binding, target


def test_r8_graph_create_binds_real_snapshot_plan_and_evaluator_version(tenants):
    world = r8_create_graph(tenants["a"])
    context, binding, target = r8_graph_context(world)
    assert binding.run.status == "queued" and binding.run.items.count() == 0
    assert binding.run_id not in {binding.native_config_id, binding.variant_id}
    assert context["plan_id"] == world["start"].call_args.kwargs["admission_id"]
    assert {pin["evaluator_version_id"] for pin in target["evaluator_versions"]} == {str(world["pin"].id)}
    assert state_of(world["experiment"])["execution_plan"]["execution_key"] == world["key"]
    assert context == world["start"].call_args.kwargs["native_runs"][str(world["config"].id)]


def test_r8_graph_ambiguous_start_reuses_draft_plan_without_reset(tenants):
    from copy import deepcopy
    world = r8_create_graph(tenants["a"], transport={"side_effect": RuntimeError("lost acknowledgement")})
    context, binding, _ = r8_graph_context(world)
    assert binding.run.status == "draft" and binding.run.items.count() == 0
    plan = deepcopy(state_of(world["experiment"])["execution_plan"])
    before = list(Cell.no_workspace_objects.filter(dataset=world["experiment"].snapshot_dataset).order_by("id").values())
    with patch(START, return_value="same-accepted-graph") as replay:
        response = world["actor"]["client"].post(
            "/model-hub/experiments/v2/", world["body"], format="json",
            HTTP_IDEMPOTENCY_KEY=world["key"],
        )
    assert response.status_code == 200, response.content
    assert replay.call_args == world["start"].call_args
    assert state_of(world["experiment"])["execution_plan"] == plan
    assert NativeRunBinding.objects.filter(snapshot__experiment=world["experiment"]).count() == 1
    assert before == list(Cell.no_workspace_objects.filter(dataset=world["experiment"].snapshot_dataset).order_by("id").values())
    binding.run.refresh_from_db()
    assert binding.run.status == "queued"


@pytest.mark.parametrize("tamper", ["row", "plan", "config"])
def test_r8_graph_scope_and_plan_tamper_refuse_without_graph_or_cell_write(tenants, tamper):
    from copy import deepcopy

    from agent_playground.models import GraphExecution
    from model_hub.views.experiment_runner import _native_dispatch
    world = r8_create_graph(tenants["a"])
    context, binding, target = r8_graph_context(world)
    bad = deepcopy(context)
    row_id = target["snapshot_row_ids"][0]
    if tamper == "row":
        row_id = str(tenants["b"]["exp"]["snapshot_row"].id)
    elif tamper == "plan":
        bad["plan_id"] = str(uuid.uuid4())
    else:
        bad["config_id"] = str(uuid.uuid4())
    cells = Cell.no_workspace_objects.filter(dataset=world["experiment"].snapshot_dataset).order_by("id")
    before, graphs = list(cells.values()), GraphExecution.no_workspace_objects.count()
    with pytest.raises(RunWriteError):
        _native_dispatch(str(world["experiment"].id), bad,
                         dataset_id=str(world["experiment"].snapshot_dataset_id), row_id=row_id, full_plan=True)
    assert GraphExecution.no_workspace_objects.count() == graphs
    assert binding.run.items.count() == 0 and list(cells.values()) == before
