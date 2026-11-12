"""Execution-time receipts; no reconstruction from mutable classic cells."""
import uuid

import pytest

from model_hub.models.dataset_versions import DatasetVersionItem
from model_hub.models.experiments import ExperimentRunItem
from model_hub.models.native_experiment_runs import NativeExampleBinding
from model_hub.services.experiment_run_writes import RunWriteError
from model_hub.services.native_run_projection import project_run
from model_hub.services.native_run_recording import (
    finish_native_run, mark_native_run, record_attempt, start_attempt, trace_context,
)
from model_hub.tests.test_native_run_binding import (
    add_native_eval, bind, configure_prompt, graph_variant,
    legacy_native_world, native_world,  # noqa: F401
)

pytestmark = pytest.mark.django_db


def prepared(world):
    run = bind(world)
    mark_native_run(dataset=world["dataset"], run_id=run.id, status="queued")
    return run


def rows(world):
    return list(NativeExampleBinding.objects.filter(snapshot__experiment=world["experiment"]).order_by("row_order"))


def start(world, run, link, key="attempt-1"):
    return start_attempt(
        dataset=world["dataset"], run_id=run.id, snapshot_row_id=link.snapshot_row_id,
        attempt_key=key,
    )


def record(world, run, link, key="attempt-1", **kwargs):
    return record_attempt(
        dataset=world["dataset"], run_id=run.id, snapshot_row_id=link.snapshot_row_id,
        attempt_key=key, status=kwargs.pop("status", "completed"), **kwargs,
    )


def test_duplicate_attempt_and_result_replay_do_not_duplicate_evidence(native_world):
    w = native_world
    run = prepared(w)
    link = rows(w)[0]
    first = start(w, run, link)
    assert start(w, run, link).id == first.id
    result = record(w, run, link, output={"answer": False}, cost=0, latency_ms=0)
    assert record(w, run, link, output={"answer": False}, cost=0, latency_ms=0).id == result.id
    result.refresh_from_db()
    assert result.output == {"answer": False}
    assert result.cost == 0
    assert result.input == link.version_item.input
    assert ExperimentRunItem.objects.count() == 1
    with pytest.raises(RunWriteError):
        record(w, run, link, output={"answer": True}, cost=0, latency_ms=0)
    with pytest.raises(RunWriteError):
        start(w, run, link, key="different-attempt")


def test_no_implicit_admission_and_no_result_without_claim(native_world):
    w = native_world
    run = bind(w)
    link = rows(w)[0]
    with pytest.raises(RunWriteError):
        start(w, run, link)
    mark_native_run(dataset=w["dataset"], run_id=run.id, status="queued")
    with pytest.raises(RunWriteError):
        record(w, run, link, output="unclaimed")
    assert ExperimentRunItem.objects.count() == 0


def test_real_terminal_set_drives_finalization_and_replay_after_seal(native_world):
    w = native_world
    run = prepared(w)
    links = rows(w)
    start(w, run, links[0])
    record(w, run, links[0], output="ok")
    with pytest.raises(RunWriteError):
        finish_native_run(dataset=w["dataset"], run_id=run.id)
    start(w, run, links[1])
    record(w, run, links[1], status="failed", error={"kind": "infra", "message": "worker unavailable"})
    result = finish_native_run(dataset=w["dataset"], run_id=run.id)
    assert result.status == "partial"
    assert result.completed_at is not None
    assert record(w, run, links[0], output="ok").status == "completed"
    payload = project_run(result, dataset=w["dataset"])
    assert payload["counts"]["completed"] == payload["counts"]["failed"] == 1
    assert payload["counts"]["not_started"] == 0
    assert payload["cost_usd"] is None
    assert payload["availability"]["cost_usd"] == "not_recorded_for_every_attempt"


@pytest.mark.parametrize("field,value", [
    ("trial_number", 0), ("trial_number", 2), ("trial_number", True),
    ("snapshot_row_id", uuid.UUID(int=1)),
])
def test_unplanned_identity_cannot_be_recorded(native_world, field, value):
    w = native_world
    run = prepared(w)
    args = {
        "dataset": w["dataset"], "run_id": run.id,
        "snapshot_row_id": rows(w)[0].snapshot_row_id, "trial_number": 1,
        "attempt_key": "attempt-1",
    }
    with pytest.raises(RunWriteError):
        start_attempt(**{**args, field: value})
    assert ExperimentRunItem.objects.count() == 0


def test_actual_trace_identifiers_and_context_are_not_invented(native_world):
    w = native_world
    run = prepared(w)
    link = rows(w)[0]
    start(w, run, link)
    context = trace_context(dataset=w["dataset"], run_id=run.id, snapshot_row_id=link.snapshot_row_id)
    assert context["fi.experiment.run_id"] == str(run.id)
    assert context["fi.experiment.example_id"] == str(link.version_item.example_id)
    result = record(w, run, link, trace_id="actual-root-trace", root_span_id="actual-root-span", output=0)
    assert result.trace_id == "actual-root-trace"
    assert result.root_span_id == "actual-root-span"
    assert result.output == 0


def test_replay_revalidates_deleted_version_item(native_world):
    w = native_world
    run = prepared(w)
    link = rows(w)[0]
    start(w, run, link)
    record(w, run, link, output="ok")
    DatasetVersionItem.objects.filter(id=link.version_item_id).update(deleted=True)
    with pytest.raises(RunWriteError):
        record(w, run, link, output="ok")


def test_foreign_dataset_cannot_claim_record_or_project(native_world, organization):
    from model_hub.models.develop_dataset import Dataset

    w = native_world
    run = prepared(w)
    link = rows(w)[0]
    foreign = Dataset.objects.create(name="different", organization=organization, column_order=[])
    for action in (start_attempt, record_attempt):
        extra = {"status": "completed"} if action is record_attempt else {}
        with pytest.raises(RunWriteError):
            action(dataset=foreign, run_id=run.id, snapshot_row_id=link.snapshot_row_id, attempt_key="x", **extra)
    with pytest.raises(RunWriteError):
        project_run(run, dataset=foreign)


def test_sanitized_external_projection_does_not_invent_settings(native_world):
    from model_hub.services.experiment_run_creation import create_experiment_run

    w = native_world
    native = bind(w)
    identities = native.metadata["_futureagi_run_write"]["plan"]["identities"]
    external = create_experiment_run(
        run_id=uuid.uuid4(), dataset_version=native.dataset_version, created_by=w["created_by"],
        task_kind="external", expected_items=[(uuid.UUID(i["example_id"]), i["trial_number"]) for i in identities],
        model_config={"headers": {"Authorization": "fixture"}, "model": {"name": "external", "api_key": "fixture"}},
    )
    view = project_run(external, dataset=w["dataset"])
    assert view["run_id"] == str(external.id)
    assert view["concurrency"] is None
    assert view["trials"] is None
    assert "fixture" not in str(view["config"])
    assert view["availability"]["settings"] == "not_recorded"


def test_skipped_is_not_an_assessed_failure(native_world):
    w = native_world
    run = prepared(w)
    link = rows(w)[0]
    start(w, run, link)
    item = record(w, run, link, status="skipped", error={"kind": "not_reached"})
    assert item.status == "skipped"
    assert item.score_refs == []
    assert item.cost is None


def test_completed_attempt_waits_for_pinned_evaluator_outcomes(native_world):
    from model_hub.tests.test_native_run_binding import add_native_eval

    w = native_world
    metric, pin = add_native_eval(w)
    run = prepared(w)
    link = rows(w)[0]
    start(w, run, link)
    with pytest.raises(RunWriteError):
        record(w, run, link, output="not-yet-evaluated")
    item = record(w, run, link, output="evaluated", score_refs=[{
        "metric_id": str(metric.id), "evaluator_version_id": str(pin.id),
        "status": "assessed", "value": 0,
    }])
    assert item.score_refs[0]["value"] == 0
    assert item.status == "completed"


def test_deleted_native_config_cannot_claim_or_read(native_world):
    w = native_world
    run = prepared(w)
    w["config"].delete()
    with pytest.raises(RunWriteError):
        start(w, run, rows(w)[0])
    with pytest.raises(RunWriteError):
        project_run(run, dataset=w["dataset"])


@pytest.mark.parametrize("kind", ["prompt", "graph"])
def test_legacy_default_scope_binds_records_and_projects(legacy_native_world, kind):
    w = legacy_native_world
    if kind == "graph":
        w = graph_variant(w, w["authorized_workspace"])
    metric, pin = add_native_eval(w)
    run = prepared(w)
    assert bind(w).id == run.id
    assert run.metadata["native_identity"]["authorization_workspace_id"] == str(w["authorized_workspace"].pk)
    assert run.dataset_version.workspace_id is None
    assert w["snapshot"].workspace_id is None
    for link in rows(w):
        start(w, run, link)
        record(w, run, link, output="ok", score_refs=[{
            "metric_id": str(metric.id), "evaluator_version_id": str(pin.id),
            "status": "assessed", "value": 1,
        }])
    result = finish_native_run(dataset=w["dataset"], run_id=run.id)
    payload = project_run(result, dataset=w["dataset"])
    assert payload["status"] == "completed"
    assert payload["counts"]["completed"] == 2
    assert payload["comparable"] is True
    assert payload["task_kind"] == kind
    assert payload["eval_versions"][0]["evaluator_version_id"] == str(pin.id)
    # Authorizing and executing in the default workspace must not relabel
    # any persisted legacy source, snapshot or immutable version tenant.
    for instance in (w["dataset"], w["snapshot"], result.dataset_version):
        instance.refresh_from_db()
        assert instance.workspace_id is None
        assert instance.organization_id == w["authorized_workspace"].organization_id


@pytest.mark.parametrize("version_settings,overrides,expected", [
    ({"temperature": 0, "max_tokens": 64}, {}, {"temperature": 0, "max_tokens": 64}),
    ({"temperature": 0, "max_tokens": 64},
     {"configuration": {"temperature": 0.7, "maxTokens": 96}},
     {"temperature": 0.7, "max_tokens": 96}),
    ({"temperature": 0, "tools": [{"type": "function", "function": {"name": "lookup"}}],
      "tool_choice": "required"}, {}, {"temperature": 0}),
])
def test_effective_prompt_config_survives_worker_materialization(
        native_world, version_settings, overrides, expected):
    from model_hub.services.experiment_config_resolver import resolve_prompt_config
    from model_hub.services.native_run_recording import prompt_execution_config

    w = native_world
    config = configure_prompt(w, version_settings, **overrides)
    run = bind(w)
    payload = project_run(run, dataset=w["dataset"])
    assert payload["config"]["model"]["params"] == expected
    resolved = resolve_prompt_config(config)
    executed = prompt_execution_config(dataset=w["dataset"], run_id=run.id)
    assert executed["configuration"] == resolved["configuration"]
    assert executed["model_params"] == resolved["model_params"]
    if version_settings.get("tools"):
        assert executed["configuration"]["tools"] == version_settings["tools"]
        assert executed["configuration"]["tool_choice"] == "required"
        assert payload["config"]["tools"] == [{"type": "function"}]
    # Same writes as _setup_prompt_v2_sync, without starting a worker/provider.
    config.model_params, config.configuration = resolved["model_params"], resolved["configuration"]
    config.save(update_fields=["model_params", "configuration"])
    assert bind(w).id == run.id
    assert prompt_execution_config(dataset=w["dataset"], run_id=run.id) == executed
    assert project_run(run, dataset=w["dataset"])["config"] == payload["config"]


@pytest.mark.parametrize("field,value", [
    ("model", "different-provider-model"), ("model_params", {"temperature": 0.5}),
    ("configuration", {"tools": [{"type": "function", "function": {"name": "different"}}]}),
])
def test_effective_config_change_refuses_replay_and_dispatch(native_world, field, value):
    from model_hub.services.native_run_recording import prompt_execution_config

    w = native_world
    config = configure_prompt(w, {"temperature": 0, "max_tokens": 64})
    run = bind(w)
    setattr(config, field, value)
    config.save(update_fields=[field])
    with pytest.raises(RunWriteError):
        bind(w)
    with pytest.raises(RunWriteError):
        prompt_execution_config(dataset=w["dataset"], run_id=run.id)


@pytest.mark.parametrize("reference", ["prompt", "metric", "template", "pin"])
def test_legacy_default_scope_does_not_admit_nondefault_references(legacy_native_world, reference):
    from accounts.models.workspace import Workspace

    w = legacy_native_world
    metric, pin = add_native_eval(w)
    foreign = Workspace.no_workspace_objects.create(
        name="Other workspace", organization=w["dataset"].organization, created_by=w["created_by"],
    )
    target = {"prompt": w["config"].prompt_template, "metric": metric,
              "template": metric.template, "pin": pin}[reference]
    type(target).no_workspace_objects.filter(pk=target.pk).update(workspace=foreign)
    with pytest.raises(RunWriteError):
        bind(w)


def test_legacy_bound_scope_is_revalidated_for_worker_and_read(legacy_native_world):
    from accounts.models.workspace import Workspace

    w = legacy_native_world
    run = prepared(w)
    Workspace.no_workspace_objects.filter(pk=w["authorized_workspace"].pk).update(is_active=False)
    with pytest.raises(RunWriteError):
        start(w, run, rows(w)[0])
    with pytest.raises(RunWriteError):
        project_run(run, dataset=w["dataset"])
