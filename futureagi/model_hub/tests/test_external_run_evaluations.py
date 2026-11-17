"""Stored external outputs are evaluated without executing their producer."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from copy import copy, deepcopy
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from django.db import close_old_connections
from django.urls import reverse

from model_hub.models.evals_metric import (
    EvalTemplate,
    EvalTemplateVersion,
    UserEvalMetric,
)
from model_hub.models.experiments import (
    ExperimentRun,
    ExperimentRunItem,
    ExperimentsTable,
)
from model_hub.models.native_experiment_runs import NativeRunBinding
from model_hub.services.experiment_run_creation import create_experiment_run
from model_hub.services.experiment_run_ingestion import (
    ExternalRunResultInput,
    ingest_external_run_results,
)
from model_hub.services.experiment_run_writes import _RUN_KEY, transition_run
from model_hub.tests.test_experiment_run_api import _dataset_fixture

pytestmark = pytest.mark.usefixtures("urlconf_loaded_without_workspace_context")


@pytest.fixture
def stored_run(organization, workspace, user):
    dataset, version, examples = _dataset_fixture(organization, workspace, user)
    template = EvalTemplate.no_workspace_objects.create(
        name="stored-output-check",
        organization=organization,
        workspace=workspace,
        owner="user",
        eval_type="llm",
        config={
            "eval_type_id": "CustomPromptEvaluator",
            "required_keys": ["question", "expected", "answer"],
            "output": "score",
        },
    )
    pin = EvalTemplateVersion.objects.create(
        eval_template=template,
        version_number=1,
        is_default=True,
        organization=organization,
        workspace=workspace,
        config_snapshot=deepcopy(template.config),
        criteria="Check the stored answer",
        model="turing_large",
        output_type_normalized="percentage",
        pass_threshold=0.5,
    )
    metric = UserEvalMetric.no_workspace_objects.create(
        name="stored-output-binding",
        organization=organization,
        workspace=workspace,
        user=user,
        dataset=dataset,
        template=template,
        config={"config": {}, "reason_column": True},
    )
    parent = create_experiment_run(
        run_id=uuid.uuid4(),
        dataset_version=version,
        created_by=user,
        task_kind="external",
        expected_items=[(examples[0].example_id, trial) for trial in (1, 2)],
    )
    scope = {"organization_id": organization.id, "workspace_id": workspace.id}
    ingest_external_run_results(
        run_id=parent.id,
        results=[
            ExternalRunResultInput(
                example_id=examples[0].example_id,
                trial_number=trial,
                receipt_key=f"parent-{trial}",
                status="completed",
                output={"answer": trial},
                trace_id=f"parent-trace-{trial}",
            )
            for trial in (1, 2)
        ],
        **scope,
    )
    parent.refresh_from_db()
    items = list(parent.items.order_by("trial_number"))
    return SimpleNamespace(
        parent=parent,
        items=items,
        dataset=dataset,
        version=version,
        metric=metric,
        template=template,
        pin=pin,
        scope=scope,
        body={
            "run_id": str(uuid.uuid4()),
            "evaluator_metric_id": str(metric.id),
            "item_ids": [str(item.id) for item in items],
            "mapping": {
                "question": "input.case",
                "expected": "expected.answer",
                "answer": "output.answer",
            },
        },
    )


def _evidence(world):
    return (
        list(ExperimentRun.all_objects.filter(id=world.parent.id).values()),
        list(
            ExperimentRunItem.all_objects.filter(run=world.parent)
            .order_by("id")
            .values()
        ),
    )


def _post(client, world, body=None):
    return client.post(
        reverse("external-experiment-run-evaluate", kwargs={"run_id": world.parent.id}),
        body or world.body,
        format="json",
    )


@pytest.fixture
def dispatched(monkeypatch):
    from model_hub.tasks.user_evaluation import process_evaluation_single_task

    dispatch = Mock()
    monkeypatch.setattr(process_evaluation_single_task, "apply_async", dispatch)
    return dispatch


@pytest.mark.django_db
@pytest.mark.parametrize("invalid", ["foreign_item", "unknown_metric", "mapping"])
def test_invalid_selection_does_not_create_or_dispatch(
    auth_client,
    stored_run,
    dispatched,
    invalid,
):
    body = deepcopy(stored_run.body)
    if invalid == "foreign_item":
        body["item_ids"] = [str(uuid.uuid4())]
    elif invalid == "unknown_metric":
        body["evaluator_metric_id"] = str(uuid.uuid4())
    else:
        body["mapping"] = {"answer": "output.missing"}
    before = _evidence(stored_run)
    assert _post(auth_client, stored_run, body).status_code == 409
    assert ExperimentRun.all_objects.filter(id=body["run_id"]).count() == 0
    assert dispatched.call_count == 0
    assert _evidence(stored_run) == before


@pytest.mark.django_db
@pytest.mark.parametrize("invalid", ["many_evaluators", "duplicate_items", "scope"])
def test_request_shape_is_strict_and_bounded(auth_client, stored_run, invalid):
    body = deepcopy(stored_run.body)
    if invalid == "many_evaluators":
        body["evaluator_metric_ids"] = [str(stored_run.metric.id)]
    elif invalid == "duplicate_items":
        body["item_ids"] *= 2
    else:
        body["workspace_id"] = str(uuid.uuid4())
    assert _post(auth_client, stored_run, body).status_code == 400


@pytest.mark.django_db
def test_anonymous_and_viewer_requests_do_not_admit(
    auth_client,
    stored_run,
    user,
    organization,
    workspace,
    dispatched,
):
    from rest_framework.test import APIRequestFactory

    from accounts.models.organization_membership import OrganizationMembership
    from accounts.models.workspace import WorkspaceMembership
    from model_hub.views.experiment_run_api import ExternalRunEvaluationView
    from tfc.constants.levels import Level

    anonymous = APIRequestFactory().post("/", stored_run.body, format="json")
    denied = ExternalRunEvaluationView.as_view(authentication_classes=[])(
        anonymous, run_id=stored_run.parent.id
    )
    assert denied.status_code == 403
    OrganizationMembership.no_workspace_objects.filter(
        user=user, organization=organization
    ).update(level=Level.VIEWER, role="Viewer")
    WorkspaceMembership.no_workspace_objects.filter(
        user=user, workspace=workspace
    ).update(level=Level.WORKSPACE_VIEWER, role="workspace_viewer")
    assert _post(auth_client, stored_run).status_code == 403
    assert dispatched.call_count == 0


@pytest.mark.django_db
@pytest.mark.parametrize("hidden", ["organization", "workspace"])
def test_foreign_parent_is_not_visible(
    auth_client,
    stored_run,
    organization,
    user,
    hidden,
    dispatched,
):
    from accounts.models.organization import Organization
    from accounts.models.workspace import Workspace
    from model_hub.models.develop_dataset import Dataset

    if hidden == "organization":
        other = Organization.objects.create(name="other-stored-output-org")
        change = {"organization": other}
    else:
        other = Workspace.no_workspace_objects.create(
            name="other-stored-output-workspace",
            organization=organization,
            created_by=user,
            is_default=False,
        )
        change = {"workspace": other}
    Dataset.no_workspace_objects.filter(id=stored_run.dataset.id).update(**change)
    assert _post(auth_client, stored_run).status_code == 404
    assert dispatched.call_count == 0


@pytest.mark.django_db
@pytest.mark.parametrize("kind", ["agent", "composite", "external"])
def test_unsupported_evaluator_types_do_not_dispatch(
    auth_client,
    stored_run,
    dispatched,
    kind,
):
    change = {"eval_type": kind}
    if kind == "composite":
        change = {"template_type": "composite"}
    EvalTemplate.no_workspace_objects.filter(id=stored_run.template.id).update(**change)
    assert _post(auth_client, stored_run).status_code == 409
    assert dispatched.call_count == 0
    assert ExperimentRun.all_objects.filter(id=stored_run.body["run_id"]).count() == 0


@pytest.mark.django_db
def test_foreign_evaluator_template_is_refused_before_dispatch(
    auth_client,
    stored_run,
    dispatched,
):
    from accounts.models.organization import Organization

    other = Organization.objects.create(name="other-stored-evaluator-org")
    EvalTemplate.no_workspace_objects.filter(id=stored_run.template.id).update(
        organization=other
    )
    before = _evidence(stored_run)
    assert _post(auth_client, stored_run).status_code == 409
    assert dispatched.call_count == 0
    assert ExperimentRun.all_objects.filter(id=stored_run.body["run_id"]).count() == 0
    assert _evidence(stored_run) == before


@pytest.mark.django_db
@pytest.mark.parametrize("location", ["template", "version"])
def test_dataset_few_shot_examples_are_refused_before_admission(
    auth_client,
    stored_run,
    dispatched,
    synthetic,
    location,
    django_capture_on_commit_callbacks,
):
    from model_hub.models.develop_dataset import Cell, Dataset
    from model_hub.services.experiment_run_writes import RunWriteError
    from model_hub.tests.test_few_shot_examples import _create_dataset_with_rows

    world = stored_run
    examples = _create_dataset_with_rows(world.dataset.organization, row_count=1)
    Dataset.no_workspace_objects.filter(id=examples.id).update(
        workspace_id=world.dataset.workspace_id,
    )
    config = {
        **world.pin.config_snapshot,
        "few_shot_examples": [{"id": str(examples.id), "name": examples.name}],
    }
    if location == "template":
        EvalTemplate.no_workspace_objects.filter(id=world.template.id).update(
            config=config,
        )
    else:
        # A saved prompt has its own static-example expansion path.
        config["rule_prompt"] = "Check {{answer}}"
        EvalTemplateVersion.objects.filter(id=world.pin.id).update(
            config_snapshot=config,
        )
    before = _evidence(world)
    for value in ("original example", "changed example"):
        assert (
            Cell.no_workspace_objects.filter(dataset=examples).update(
                value=value,
            )
            == 3
        )
        with django_capture_on_commit_callbacks(execute=True):
            refused = _post(auth_client, world)
        assert refused.status_code == 409
        assert ExperimentRun.all_objects.filter(id=world.body["run_id"]).count() == 0
        assert dispatched.call_count == 0
        with pytest.raises(
            RunWriteError,
            match="Stored evaluation child was not found",
        ):
            _worker(world.body["run_id"])
    assert synthetic == []
    assert _evidence(world) == before


@pytest.mark.django_db
@pytest.mark.parametrize("linked", [False, True])
@pytest.mark.parametrize("selected_only", [False, True])
def test_admission_pins_selected_receipts_in_a_queued_child(
    auth_client,
    stored_run,
    dispatched,
    linked,
    selected_only,
):
    from model_hub.services.experiment_run_writes import _digest

    world = stored_run
    if linked:
        experiment = ExperimentsTable.no_workspace_objects.create(
            name="stored-admission-experiment",
            dataset=world.dataset,
        )
        ExperimentRun.no_workspace_objects.filter(id=world.parent.id).update(
            experiment=experiment,
        )
        world.parent.refresh_from_db()
    selected = world.items[1:] if selected_only else world.items
    body = {**world.body, "item_ids": [str(item.id) for item in selected]}
    before = _evidence(world)
    response = _post(auth_client, world, body)
    assert response.status_code == 200, response.json()
    child = ExperimentRun.no_workspace_objects.get(id=body["run_id"])
    assert (child.status, child.started_at, child.completed_at) == (
        "queued",
        None,
        None,
    )
    assert child.dataset_version_id == world.parent.dataset_version_id
    assert child.experiment_id == world.parent.experiment_id
    assert child.model_config == world.parent.model_config
    assert child.items.count() == 0
    assert NativeRunBinding.objects.filter(run=child).count() == 0
    result = response.json()["result"]
    assert result["id"] == body["run_id"]
    assert result["status"] == "queued"
    assert result["expected_items"] == [
        {"example_id": str(item.example_id), "trial_number": item.trial_number}
        for item in selected
    ]
    # FA's comparison identity (evaluator_comparison_identity.py) adds a sealed
    # "comparison" block to each admitted pin; check it exactly, apart from the pin.
    # It describes the binding the child runs, so it includes the stored mapping.
    pins = child.evaluator_versions
    assert [{k: v for k, v in pin.items() if k != "comparison"} for pin in pins] == [
        {
            "metric_id": str(world.metric.id),
            "evaluator_version_id": str(world.pin.id),
            "template_id": str(world.template.id),
            "binding_config": {**world.metric.config, "mapping": body["mapping"]},
            "binding_model": world.metric.model,
            **{
                name: getattr(world.pin, name)
                for name in (
                    "version_number",
                    "prompt_messages",
                    "config_snapshot",
                    "criteria",
                    "model",
                    "output_type_normalized",
                    "pass_threshold",
                    "choice_scores",
                )
            },
        }
    ]
    from model_hub.services.evaluator_comparison_identity import (
        _comparison,
        _identity,
    )

    metric = UserEvalMetric.no_workspace_objects.select_related("template").get(
        pk=world.metric.pk
    )
    admitted = copy(metric)
    admitted.config = {**(metric.config or {}), "mapping": body["mapping"]}
    assert [_comparison(pin) for pin in pins] == [
        {"metric_id": str(metric.id), "identity": _identity(admitted, world.pin)}
    ]
    admission = child.metadata[_RUN_KEY]["stored_evaluation"]
    assert admission["parent_run_id"] == str(world.parent.id)
    assert admission["mapping"] == body["mapping"]
    assert admission["template"]["config"] == world.pin.config_snapshot
    assert admission["dispatch_pending"] is True
    assert admission["sources"] == [
        {
            "item_id": str(item.id),
            "example_id": str(item.example_id),
            "trial_number": item.trial_number,
            "receipt": item.metadata[_RUN_KEY]["result_receipt"],
            "evidence_hash": _digest(
                {
                    name: getattr(item, name)
                    for name in (
                        "input",
                        "expected",
                        "status",
                        "output",
                        "error",
                        "trace_id",
                        "root_span_id",
                        "latency_ms",
                        "token_usage",
                        "cost",
                        "score_refs",
                    )
                }
            ),
        }
        for item in selected
    ]
    # Admission is observable before any post-commit worker publication.
    assert dispatched.call_count == 0
    upload = auth_client.post(
        reverse("external-experiment-run-results", kwargs={"run_id": child.id}),
        {
            "items": [
                {
                    "example_id": str(selected[0].example_id),
                    "trial_number": selected[0].trial_number,
                    "receipt_key": "caller",
                    "status": "completed",
                    "output": "replacement",
                }
            ]
        },
        format="json",
    )
    assert upload.status_code == 409
    child.refresh_from_db()
    assert child.status == "queued"
    assert child.items.count() == 0
    assert _evidence(world) == before


@pytest.mark.django_db
def test_queued_replay_preserves_admission_when_defaults_change(
    auth_client,
    stored_run,
    dispatched,
):
    world = stored_run
    before = _evidence(world)
    response = _post(auth_client, world)
    assert response.status_code == 200
    admitted = list(ExperimentRun.all_objects.filter(id=world.body["run_id"]).values())
    world.pin.is_default = False
    world.pin.save(update_fields=["is_default"])
    EvalTemplateVersion.objects.create(
        eval_template=world.template,
        version_number=2,
        is_default=True,
        config_snapshot={"different": True},
    )
    UserEvalMetric.no_workspace_objects.filter(id=world.metric.id).update(
        config={"mapping": {"answer": "unrelated"}},
        model="changed",
    )
    EvalTemplate.no_workspace_objects.filter(id=world.template.id).update(
        config={"eval_type_id": "AgentEvaluator"},
        name="changed-live-name",
    )
    replay = _post(auth_client, world)
    assert replay.status_code == 200
    assert replay.json()["result"] == response.json()["result"]
    assert (
        list(ExperimentRun.all_objects.filter(id=world.body["run_id"]).values())
        == admitted
    )
    assert (
        ExperimentRunItem.all_objects.filter(
            run_id=world.body["run_id"],
        ).count()
        == 0
    )
    assert dispatched.call_count == 0
    assert _evidence(world) == before


@pytest.mark.django_db
@pytest.mark.parametrize("field", ["item_ids", "mapping", "evaluator_metric_id"])
def test_queued_identifier_refuses_a_different_request(
    auth_client,
    stored_run,
    dispatched,
    field,
):
    world = stored_run
    before = _evidence(world)
    assert _post(auth_client, world).status_code == 200
    admitted = list(ExperimentRun.all_objects.filter(id=world.body["run_id"]).values())
    changes = {
        "item_ids": [str(world.items[0].id)],
        "mapping": {"answer": "input"},
        "evaluator_metric_id": str(uuid.uuid4()),
    }
    assert (
        _post(
            auth_client,
            world,
            {**world.body, field: changes[field]},
        ).status_code
        == 409
    )
    assert (
        list(ExperimentRun.all_objects.filter(id=world.body["run_id"]).values())
        == admitted
    )
    assert dispatched.call_count == 0
    assert _evidence(world) == before


@pytest.mark.django_db
@pytest.mark.parametrize("tamper", ["status", "receipt", "output"])
def test_damaged_parent_receipts_do_not_admit_a_child(
    auth_client,
    stored_run,
    dispatched,
    tamper,
):
    world = stored_run
    changes = {tamper: "changed"}
    if tamper == "receipt":
        metadata = deepcopy(world.items[0].metadata)
        metadata[_RUN_KEY]["result_receipt"]["payload_hash"] = "changed"
        changes = {"metadata": metadata}
    elif tamper == "status":
        changes = {"status": "running"}
    ExperimentRunItem.no_workspace_objects.filter(id=world.items[0].id).update(
        **changes,
    )
    before = _evidence(world)
    assert _post(auth_client, world).status_code == 409
    assert ExperimentRun.all_objects.filter(id=world.body["run_id"]).count() == 0
    assert dispatched.call_count == 0
    assert _evidence(world) == before


@pytest.fixture
def synthetic(monkeypatch):
    from model_hub.services.external_run_evaluations import StoredEvaluationRunner
    from model_hub.views.eval_runner import EvaluationRunner

    calls = []

    class Evaluator:
        cost = {}
        token_usage = {}

        def run(self, **values):
            calls.append(deepcopy(values))
            return {"metrics": [{"value": 0.75}], "reason": "stored answer checked"}

        def format_result(self, response, template):
            return 0.75

    monkeypatch.setattr(
        StoredEvaluationRunner, "_create_eval_instance", lambda *a, **k: Evaluator()
    )
    monkeypatch.setattr(
        StoredEvaluationRunner, "_refuse_pinned_model_without_key", lambda self: None
    )
    monkeypatch.setattr(
        StoredEvaluationRunner, "_handle_api_call", lambda *a, **k: None
    )
    monkeypatch.setattr(
        StoredEvaluationRunner, "_emit_cell_usage", lambda *a, **k: None
    )
    monkeypatch.setattr(
        StoredEvaluationRunner,
        "_format_response",
        lambda self, value, **kwargs: {"output": "score", **value},
    )

    # Every classic writer and producer is forbidden, not only HTTP transport.
    def forbidden(*args, **kwargs):
        raise AssertionError("Stored evaluation must not execute or write a producer")

    for method in ("_create_cell", "_create_or_update_column", "_create_tags_cell"):
        monkeypatch.setattr(EvaluationRunner, method, forbidden)
    from model_hub.views.run_prompt import RunPrompts

    monkeypatch.setattr(RunPrompts, "run_prompt", forbidden)
    from model_hub.services import http_experiment_source
    from model_hub.views import experiment_runner

    producers = []
    for owner, name in (
        (http_experiment_source, "run_http_snapshot_row"),
        (http_experiment_source, "execute_and_persist_http_row"),
        (experiment_runner, "RunPrompt"),
    ):
        guard = Mock(side_effect=forbidden)
        monkeypatch.setattr(owner, name, guard)
        producers.append(guard)
    yield calls
    assert [guard.call_count for guard in producers] == [0, 0, 0]


def _worker(run_id, *, independent_connection=False):
    from model_hub.tasks import user_evaluation

    task = user_evaluation.process_evaluation_single_task
    payload = {"type": "stored_run", "run_id": str(run_id)}
    if independent_connection:
        return task(payload)
    # Preserve pytest's rollback transaction; the decorator also closes it.
    with patch.object(user_evaluation, "close_old_connections"):
        return task._original_func(payload)


@pytest.mark.django_db
@pytest.mark.parametrize("linked", [False, True])
def test_stored_outputs_are_scored_in_a_new_readable_child(
    auth_client,
    stored_run,
    dispatched,
    synthetic,
    django_capture_on_commit_callbacks,
    linked,
):
    world = stored_run
    if linked:
        experiment = ExperimentsTable.no_workspace_objects.create(
            name="stored-output-experiment", dataset=world.dataset
        )
        ExperimentRun.no_workspace_objects.filter(id=world.parent.id).update(
            experiment=experiment
        )
        world.parent.refresh_from_db()
    before = _evidence(world)
    with django_capture_on_commit_callbacks(execute=True):
        admitted = _post(auth_client, world)
    assert admitted.status_code == 200, admitted.json()
    child = ExperimentRun.no_workspace_objects.get(id=world.body["run_id"])
    assert child.experiment_id == world.parent.experiment_id
    assert child.status == "queued"
    assert child.evaluator_versions[0]["evaluator_version_id"] == str(world.pin.id)
    assert NativeRunBinding.objects.filter(run=child).count() == 0
    assert dispatched.call_count == 1
    assert dispatched.call_args.kwargs["args"] == (
        {"type": "stored_run", "run_id": str(child.id)},
    )
    assert _worker(child.id) == {"run_id": str(child.id), "status": "completed"}
    assert [(v["question"], v["expected"], v["answer"]) for v in synthetic] == [
        (1, 1, 1),
        (1, 1, 2),
    ]
    for item in child.items.order_by("trial_number"):
        parent = world.items[item.trial_number - 1]
        assert (item.input, item.expected, item.output) == (
            parent.input,
            parent.expected,
            parent.output,
        )
        assert item.score_refs == [
            {
                "metric_id": str(world.metric.id),
                "evaluator_version_id": str(world.pin.id),
                "status": "assessed",
                "value": 0.75,
                "explanation": "stored answer checked",
            }
        ]
        assert (item.trace_id, item.root_span_id) == (None, None)
    read = auth_client.get(reverse("experiment-run-items", kwargs={"run_id": child.id}))
    assert read.status_code == 200
    assert read.json()["result"]["total"] == 2
    assert read.json()["result"]["items"][0]["score_refs"][0]["value"] == 0.75
    assert _evidence(world) == before


@pytest.mark.django_db
def test_replays_keep_pins_and_never_dispatch_or_evaluate_again(
    auth_client,
    stored_run,
    dispatched,
    synthetic,
    django_capture_on_commit_callbacks,
):
    world = stored_run
    with django_capture_on_commit_callbacks(execute=True):
        assert _post(auth_client, world).status_code == 200
    world.pin.is_default = False
    world.pin.save(update_fields=["is_default"])
    EvalTemplateVersion.objects.create(
        eval_template=world.template,
        version_number=2,
        is_default=True,
        config_snapshot={"different": True},
    )
    UserEvalMetric.no_workspace_objects.filter(id=world.metric.id).update(
        config={"mapping": {"answer": "unrelated"}}, model="changed"
    )
    before = _evidence(world)
    with django_capture_on_commit_callbacks(execute=True):
        replay = _post(auth_client, world)
    assert replay.status_code == 200
    assert dispatched.call_count == 1
    assert replay.json()["result"]["evaluator_bindings"][0][
        "evaluator_version_id"
    ] == str(world.pin.id)
    assert _worker(world.body["run_id"])["status"] == "completed"
    assert _worker(world.body["run_id"])["status"] == "completed"
    assert len(synthetic) == 2
    assert _evidence(world) == before


@pytest.mark.django_db
def test_fresh_identifier_intentionally_repeats_one_selected_item(
    auth_client,
    stored_run,
    dispatched,
    synthetic,
    django_capture_on_commit_callbacks,
):
    world = stored_run
    body = {**world.body, "item_ids": [str(world.items[1].id)]}
    for identifier in (world.body["run_id"], str(uuid.uuid4())):
        with django_capture_on_commit_callbacks(execute=True):
            response = _post(auth_client, world, {**body, "run_id": identifier})
        assert response.status_code == 200
        assert _worker(identifier)["status"] == "completed"
    assert dispatched.call_count == 2
    assert [v["answer"] for v in synthetic] == [2, 2]


@pytest.mark.django_db
@pytest.mark.parametrize("field", ["item_ids", "mapping", "evaluator_metric_id"])
def test_changed_request_cannot_reuse_a_child_identifier(
    auth_client,
    stored_run,
    dispatched,
    django_capture_on_commit_callbacks,
    field,
):
    with django_capture_on_commit_callbacks(execute=True):
        assert _post(auth_client, stored_run).status_code == 200
    changes = {
        "item_ids": [str(stored_run.items[0].id)],
        "mapping": {"answer": "input"},
        "evaluator_metric_id": str(uuid.uuid4()),
    }
    assert (
        _post(
            auth_client, stored_run, {**stored_run.body, field: changes[field]}
        ).status_code
        == 409
    )
    assert dispatched.call_count == 1


@pytest.mark.django_db
def test_public_upload_and_cancelled_child_cannot_execute(
    auth_client,
    stored_run,
    dispatched,
    synthetic,
    django_capture_on_commit_callbacks,
):
    world = stored_run
    with django_capture_on_commit_callbacks(execute=True):
        assert _post(auth_client, world).status_code == 200
    before = _evidence(world)
    upload = auth_client.post(
        reverse(
            "external-experiment-run-results", kwargs={"run_id": world.body["run_id"]}
        ),
        {
            "items": [
                {
                    "example_id": str(world.items[0].example_id),
                    "trial_number": 1,
                    "receipt_key": "caller",
                    "status": "completed",
                    "output": "forged",
                }
            ]
        },
        format="json",
    )
    assert upload.status_code == 409
    transition_run(run_id=world.body["run_id"], status="cancelled", **world.scope)
    assert _worker(world.body["run_id"])["status"] == "cancelled"
    assert synthetic == []
    assert _evidence(world) == before


@pytest.mark.django_db
@pytest.mark.parametrize("tamper", ["output", "receipt", "definition"])
def test_changed_stored_evidence_is_refused_before_evaluation(
    auth_client,
    stored_run,
    dispatched,
    synthetic,
    django_capture_on_commit_callbacks,
    tamper,
):
    world = stored_run
    with django_capture_on_commit_callbacks(execute=True):
        assert _post(auth_client, world).status_code == 200
    if tamper == "definition":
        EvalTemplateVersion.objects.filter(id=world.pin.id).update(criteria="changed")
    elif tamper == "output":
        ExperimentRunItem.no_workspace_objects.filter(id=world.items[0].id).update(
            output="changed"
        )
    else:
        metadata = deepcopy(world.items[0].metadata)
        metadata[_RUN_KEY]["result_receipt"]["payload_hash"] = "changed"
        ExperimentRunItem.no_workspace_objects.filter(id=world.items[0].id).update(
            metadata=metadata
        )
    from model_hub.services.experiment_run_writes import RunWriteError

    with pytest.raises(RunWriteError):
        _worker(world.body["run_id"])
    assert synthetic == []


@pytest.mark.django_db(transaction=True)
def test_overlapping_workers_share_a_durable_claim(
    auth_client,
    stored_run,
    dispatched,
    synthetic,
    monkeypatch,
):
    from model_hub.services.external_run_evaluations import StoredEvaluationRunner

    entered, release = Event(), Event()
    original = StoredEvaluationRunner.evaluate

    def pause(self, item):
        entered.set()
        assert release.wait(timeout=10) is True
        return original(self, item)

    monkeypatch.setattr(StoredEvaluationRunner, "evaluate", pause)
    world = stored_run
    body = {**world.body, "item_ids": [str(world.items[0].id)]}
    assert _post(auth_client, world, body).status_code == 200

    def work():
        close_old_connections()
        try:
            return _worker(body["run_id"], independent_connection=True)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(work)
        try:
            assert entered.wait(timeout=10) is True
            assert work() == {"run_id": body["run_id"], "status": "running"}
        finally:
            release.set()
        assert future.result(timeout=10) == {
            "run_id": body["run_id"],
            "status": "completed",
        }
    assert len(synthetic) == 1
    assert (
        ExperimentRunItem.no_workspace_objects.filter(run_id=body["run_id"]).count()
        == 1
    )


@pytest.mark.django_db
def test_another_evaluator_gets_its_own_child_pin(
    auth_client,
    stored_run,
    dispatched,
    synthetic,
    django_capture_on_commit_callbacks,
    user,
):
    world = stored_run
    other = UserEvalMetric.no_workspace_objects.create(
        name="another-stored-binding",
        template=world.template,
        dataset=world.dataset,
        user=user,
        organization=world.dataset.organization,
        workspace=world.dataset.workspace,
        config={},
    )
    for metric in (world.metric, other):
        body = {
            **world.body,
            "run_id": str(uuid.uuid4()),
            "evaluator_metric_id": str(metric.id),
            "item_ids": [str(world.items[0].id)],
        }
        with django_capture_on_commit_callbacks(execute=True):
            assert _post(auth_client, world, body).status_code == 200
        assert _worker(body["run_id"])["status"] == "completed"
        item = ExperimentRunItem.no_workspace_objects.get(run_id=body["run_id"])
        assert item.score_refs[0]["metric_id"] == str(metric.id)
    assert len(synthetic) == 2


@pytest.mark.django_db
def test_adapter_passes_the_frozen_definition_to_the_shared_engine(
    auth_client,
    stored_run,
    dispatched,
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    from evaluations.engine import instance, registry
    from model_hub.services.external_run_evaluations import StoredEvaluationRunner

    world = stored_run
    with django_capture_on_commit_callbacks(execute=True):
        assert _post(auth_client, world).status_code == 200
    EvalTemplate.no_workspace_objects.filter(id=world.template.id).update(
        config={"eval_type_id": "AgentEvaluator"}, name="changed-live-name"
    )
    child = ExperimentRun.no_workspace_objects.select_related(
        "dataset_version__dataset"
    ).get(id=world.body["run_id"])
    runner = StoredEvaluationRunner(child)
    evaluator = object()
    engine = Mock(return_value=(evaluator, None))
    monkeypatch.setattr(instance, "create_eval_instance", engine)
    monkeypatch.setattr(registry, "get_eval_class", lambda kind: kind)
    assert runner._create_eval_instance(config={}) is evaluator
    kwargs = engine.call_args.kwargs
    assert kwargs["eval_class"] == "CustomPromptEvaluator"
    assert kwargs["eval_template"].name == "stored-output-check"
    assert kwargs["eval_template"].config == world.pin.config_snapshot
    assert kwargs["version_number"] == 1
    assert kwargs["saved_choices"] is True
    assert kwargs["runtime_config"]["mapping"] == world.body["mapping"]
    assert kwargs["organization_id"] == str(world.dataset.organization_id)
    assert kwargs["workspace_id"] == str(world.dataset.workspace_id)
    assert engine.call_count == 1


@pytest.mark.django_db
def test_interrupted_claim_is_not_automatically_repeated(
    auth_client,
    stored_run,
    dispatched,
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    from model_hub.services.external_run_evaluations import StoredEvaluationRunner

    world = stored_run
    with django_capture_on_commit_callbacks(execute=True):
        assert _post(auth_client, world).status_code == 200
    evaluate = Mock(side_effect=RuntimeError("worker interrupted"))
    monkeypatch.setattr(StoredEvaluationRunner, "evaluate", evaluate)
    with pytest.raises(RuntimeError, match="worker interrupted"):
        _worker(world.body["run_id"])
    assert _worker(world.body["run_id"])["status"] == "running"
    assert evaluate.call_count == 1
    assert (
        ExperimentRunItem.no_workspace_objects.filter(
            run_id=world.body["run_id"], status="running"
        ).count()
        == 1
    )


@pytest.mark.django_db
def test_cancel_during_evaluation_retains_the_started_result(
    auth_client,
    stored_run,
    dispatched,
    synthetic,
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    from model_hub.services.external_run_evaluations import StoredEvaluationRunner

    world = stored_run
    with django_capture_on_commit_callbacks(execute=True):
        assert _post(auth_client, world).status_code == 200
    before = _evidence(world)
    original = StoredEvaluationRunner.evaluate
    stopped = []

    def stop(self, item):
        outcome = original(self, item)
        transition_run(run_id=world.body["run_id"], status="cancelled", **world.scope)
        stopped.extend(
            ExperimentRun.all_objects.filter(id=world.body["run_id"]).values()
        )
        return outcome

    monkeypatch.setattr(StoredEvaluationRunner, "evaluate", stop)
    assert _worker(world.body["run_id"])["status"] == "cancelled"
    item = ExperimentRunItem.no_workspace_objects.get(run_id=world.body["run_id"])
    assert item.score_refs == [
        {
            "metric_id": str(world.metric.id),
            "evaluator_version_id": str(world.pin.id),
            "status": "assessed",
            "value": 0.75,
            "explanation": "stored answer checked",
        }
    ]
    assert item.status == "completed"
    assert (item.example_id, item.trial_number, item.output) == (
        world.items[0].example_id,
        1,
        world.items[0].output,
    )
    assert item.metadata[_RUN_KEY]["result_receipt"]["receipt_key"] == (
        f"stored-evaluation-{world.items[0].id}"
    )
    assert _worker(world.body["run_id"])["status"] == "cancelled"
    assert (
        list(ExperimentRun.all_objects.filter(id=world.body["run_id"]).values())
        == stopped
    )
    assert len(synthetic) == 1
    assert _evidence(world) == before


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("interruption", ["failure", "unknown", "before_callback"])
def test_unpublished_child_recovers_with_the_same_identifier(
    auth_client,
    stored_run,
    dispatched,
    synthetic,
    monkeypatch,
    interruption,
    user,
):
    from temporalio.exceptions import WorkflowAlreadyStartedError

    from model_hub.services import external_run_evaluations as service

    world = stored_run
    before = _evidence(world)
    if interruption == "before_callback":
        # Model process exit after commit but before the callback can publish.
        with monkeypatch.context() as patch:
            patch.setattr(service, "_dispatch", Mock())
            assert _post(auth_client, world).status_code == 200
        assert dispatched.call_count == 0
    else:
        dispatched.side_effect = RuntimeError("publication interrupted")
        with pytest.raises(RuntimeError, match="publication interrupted"):
            service.admit_external_run_evaluation(
                parent=world.parent,
                actor=user,
                run_id=uuid.UUID(world.body["run_id"]),
                evaluator_metric_id=world.metric.id,
                item_ids=[item.id for item in world.items],
                mapping=world.body["mapping"],
            )
        assert dispatched.call_count == 1
    child = ExperimentRun.no_workspace_objects.get(id=world.body["run_id"])
    assert child.status == "queued"
    assert child.items.count() == 0
    assert (
        child.metadata[_RUN_KEY][service.STORED_EVALUATION]["dispatch_pending"] is True
    )
    admitted_metadata = deepcopy(child.metadata)
    admitted_pins = deepcopy(child.evaluator_versions)
    assert synthetic == []

    # Recovery must not reconstruct the admission from mutable defaults.
    world.pin.is_default = False
    world.pin.save(update_fields=["is_default"])
    EvalTemplateVersion.objects.create(
        eval_template=world.template,
        version_number=2,
        is_default=True,
        config_snapshot={"different": True},
    )
    UserEvalMetric.no_workspace_objects.filter(id=world.metric.id).update(
        config={"mapping": {"answer": "unrelated"}},
        model="changed",
    )
    dispatched.side_effect = (
        WorkflowAlreadyStartedError(
            f"task-stored-run-evaluation-{child.id}",
            "TaskRunnerWorkflow",
        )
        if interruption == "unknown"
        else None
    )
    assert _post(auth_client, world).status_code == 200
    child.refresh_from_db()
    admitted_metadata[_RUN_KEY][service.STORED_EVALUATION]["dispatch_pending"] = False
    assert child.metadata == admitted_metadata
    assert child.evaluator_versions == admitted_pins
    assert ExperimentRun.all_objects.filter(id=child.id).count() == 1
    expected_attempts = 1 if interruption == "before_callback" else 2
    assert [call.kwargs for call in dispatched.call_args_list] == [
        {
            "args": ({"type": "stored_run", "run_id": str(child.id)},),
            "task_id": f"stored-run-evaluation-{child.id}",
            "dispatch_timeout_seconds": 10,
        }
    ] * expected_attempts

    # A delayed duplicate callback also observes the durable acknowledgement.
    service._dispatch(child.id)
    assert _post(auth_client, world).status_code == 200
    assert dispatched.call_count == expected_attempts
    assert _worker(child.id) == {"run_id": str(child.id), "status": "completed"}
    assert _worker(child.id) == {"run_id": str(child.id), "status": "completed"}
    assert [values["answer"] for values in synthetic] == [1, 2]
    assert list(
        child.items.order_by("trial_number").values_list(
            "trial_number",
            "status",
            "score_refs",
        )
    ) == [
        (
            trial,
            "completed",
            [
                {
                    "metric_id": str(world.metric.id),
                    "evaluator_version_id": str(world.pin.id),
                    "status": "assessed",
                    "value": 0.75,
                    "explanation": "stored answer checked",
                }
            ],
        )
        for trial in (1, 2)
    ]
    assert _post(auth_client, world).status_code == 200
    assert dispatched.call_count == expected_attempts
    assert _evidence(world) == before


@pytest.mark.django_db
@pytest.mark.parametrize("location", ["template", "version"])
def test_literal_few_shot_examples_remain_frozen_after_admission(
    auth_client,
    stored_run,
    dispatched,
    monkeypatch,
    location,
    django_capture_on_commit_callbacks,
):
    from evaluations.engine.instance import (
        apply_pinned_version_prompt,
        prepare_eval_config,
    )
    from model_hub.services.external_run_evaluations import StoredEvaluationRunner
    from model_hub.utils import few_shot_examples

    world = stored_run
    literals = [
        {
            "id": str(world.dataset.id),
            "input": "stored example",
            "output": "stored answer",
            "score": "Passed",
        }
    ]
    config = {**world.pin.config_snapshot, "few_shot_examples": literals}
    if location == "template":
        EvalTemplate.no_workspace_objects.filter(id=world.template.id).update(
            config=config,
        )
    else:
        config["rule_prompt"] = "Check {{answer}}"
        EvalTemplateVersion.objects.filter(id=world.pin.id).update(
            config_snapshot=config,
        )
    with django_capture_on_commit_callbacks(execute=True):
        assert _post(auth_client, world).status_code == 200
    before = _evidence(world)
    EvalTemplate.no_workspace_objects.filter(id=world.template.id).update(
        config={
            **config,
            "few_shot_examples": [{"id": str(world.dataset.id)}],
        }
    )
    read_examples = Mock(side_effect=AssertionError("No live examples may be read"))
    monkeypatch.setattr(few_shot_examples, "_examples_from_datasets", read_examples)
    child = ExperimentRun.no_workspace_objects.select_related(
        "dataset_version__dataset",
    ).get(id=world.body["run_id"])
    runner = StoredEvaluationRunner(child)
    prepared, _ = prepare_eval_config(
        eval_template=runner.eval_template,
        config={},
        model="turing_large",
        organization_id=str(world.dataset.organization_id),
        workspace_id=str(world.dataset.workspace_id),
    )
    prepared = apply_pinned_version_prompt(
        prepared,
        runner._resolved_version,
        runner.eval_template,
    )
    assert prepared["few_shot_examples"] == literals
    assert read_examples.call_count == 0
    assert dispatched.call_count == 1
    assert _evidence(world) == before
