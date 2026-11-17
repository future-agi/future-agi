"""Stored HTTP evaluation contracts, seeded by real canonical writes.

Tests-first specifications, not execution receipts. Initial HTTP calls happen
only in the shared canonical fixture helpers. Scoring exercises the real native
worker and canonical recorder with a synthetic evaluator result, never an agent
transport or a test-created execution receipt.
"""

from contextlib import ExitStack, contextmanager
from copy import deepcopy
from unittest.mock import patch
from uuid import uuid4

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from model_hub.models.choices import CellStatus, SourceChoices, StatusType
from model_hub.models.develop_dataset import Cell
from model_hub.models.evals_metric import EvalTemplateVersion, UserEvalMetric
from model_hub.models.experiments import ExperimentRunItem
from model_hub.models.native_experiment_runs import NativeRunBinding
from model_hub.services import http_experiment_source as http
from model_hub.services.experiment_run_writes import _RUN_KEY, RunWriteError
from model_hub.services.experiment_submit import state_of
from model_hub.services.http_evaluation_fence import HttpEvaluationStopped
from model_hub.services.native_run_binding import native_output_sources
from model_hub.tests.test_http_canonical_evidence import (
    _cells,
    _dispatch,
    _evaluate,
    _evidence,
    _finish,
    _run,
    _stage,
    _stop,
)
from model_hub.tests.test_http_canonical_evidence import tenants as tenants
from model_hub.views import experiment_runner
from model_hub.views.eval_runner import NativeEvaluationRunner
from model_hub.views.experiments import AddExperimentEvalView
from tfc.middleware.workspace_context import workspace_context
from tfc.temporal.experiments import activities
from tfc.utils import outbound_destination

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("urlconf_loaded_without_workspace_context"),
]


@contextmanager
def _scoring_only():
    """Capture only workflow submission; prohibit every agent execution seam."""
    with ExitStack() as stack:
        starts = [
            stack.enter_context(patch(path, return_value="stored-evaluation-workflow"))
            for path in (
                "tfc.temporal.experiments.start_experiment_v2_workflow",
                "tfc.temporal.experiments.start_rerun_cells_v2_workflow",
            )
        ]
        blocked = [
            stack.enter_context(
                patch.object(
                    owner,
                    name,
                    side_effect=AssertionError(
                        "Stored-output scoring must never execute the HTTP agent"
                    ),
                )
            )
            for owner, name in (
                (http, "run_http_snapshot_row"),
                (http, "execute_and_persist_http_row"),
                (http, "prepare_http_experiment"),
                (http, "bind_http_submission"),
                (outbound_destination, "request"),
                (experiment_runner, "RunPrompt"),
            )
        ]
        try:
            yield starts
        finally:
            for call in blocked:
                call.assert_not_called()


def _terminal(tenants, *, presentation=False):
    world = _stage(tenants, evaluated=True, presentation=presentation)
    for link in world.links:
        assert _dispatch(world, link, traced=True)["status"] == "COMPLETED"
        _evaluate(world, link, 1)
    _finish(world)
    assert _run(world).status == "completed"
    return world


def _appended_terminal(tenants, *, later=False, recording_failure=None):
    from model_hub.models.develop_dataset import Row
    from model_hub.models.experiments import ExperimentDatasetTable
    from model_hub.services.native_run_binding import run_examples
    from model_hub.tests.test_http_canonical_evidence import _accept, _body
    from model_hub.tests.test_http_canonical_evidence_append import _put

    world = _terminal(tenants, presentation=True)
    original = _run(world)
    row = Row.no_workspace_objects.create(
        dataset=world.dataset,
        order=10,
        metadata={"category": "captured", "split_names": ["holdout"]},
    )
    Cell.no_workspace_objects.create(
        dataset=world.dataset,
        row=row,
        column=world.dataset.column_set.get(name="Question"),
        value="appended",
        status=CellStatus.PASS.value,
    )
    Cell.no_workspace_objects.create(
        dataset=world.dataset,
        row=row,
        column=world.dataset.column_set.get(name="Expected"),
        value="expected:appended",
    )
    world.inputs[str(row.pk)] = "appended"
    bodies = [{"append_only": True, "row_ids": [str(row.pk)]}]
    if later:
        config = http.public_http_source(world.source)
        config.pop("id")
        config.pop("order")
        config["name"] = "Later endpoint"
        bodies.append({"append_only": True, "prompt_config": [config]})
    for body in bodies:
        _body(_put(world, body, str(uuid4())))
        state = state_of(world.experiment)
        world.admission = state["admission_id"]
        (source_id,) = state["http_plan"]["targets"]
        world.source = ExperimentDatasetTable.no_workspace_objects.get(pk=source_id)
        world.output = http._http_output_column(world.experiment, world.source)
        _accept(world)
        run = _run(world)
        with ExitStack() as stack:
            if recording_failure:
                from django.db import IntegrityError

                from model_hub.services import http_run_recording as recording

                original_write = getattr(recording, recording_failure)

                def write_then_fail(*args, _write=original_write, **kwargs):
                    _write(*args, **kwargs)
                    raise IntegrityError("Canonical storage failure")

                stack.enter_context(
                    patch.object(
                        recording, recording_failure, side_effect=write_then_fail
                    )
                )
            for link in run_examples(run.native_binding, run):
                assert _dispatch(world, link)["status"] == "COMPLETED"
                _evaluate(world, link, 1)
        _finish(world)
    world.links = http._http_rows(http._http_snapshot(world.experiment))
    assert len(world.links) == 3
    assert world.dataset.dataset_versions.count() == 2
    assert run.dataset_version.parent_version_id == original.dataset_version_id
    return world


def _new_version(world):
    return EvalTemplateVersion.objects.create(
        eval_template=world.metric.template,
        version_number=2,
        is_default=False,
        organization=world.actor["org"],
        workspace=world.actor["workspace"],
        output_type_normalized="pass_fail",
    )


def _add(world, version, *, run=True, actor=None):
    actor = actor or world.actor
    request = APIRequestFactory().post(
        "/",
        {
            "name": "Stored answer judge",
            "template_id": str(world.metric.template_id),
            "pinned_version_id": str(version.pk),
            "config": deepcopy(world.metric.config),
            "model": "fixture-model",
            "run": run,
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY="stored-output-add",
    )
    request.organization = actor["org"]
    request.workspace = actor["workspace"]
    force_authenticate(request, user=actor["user"])
    with workspace_context(actor["workspace"]):
        return AddExperimentEvalView.as_view()(
            request, experiment_id=world.experiment.pk
        )


def _rescore(world, door, *, metric_id=None, actor=None, run=True, key=None):
    actor = actor or world.actor
    metric_id = str(metric_id or world.metric.pk)
    if door == "edit":
        url = f"/model-hub/develops/{world.dataset.pk}/edit_and_run_user_eval/{metric_id}/"
        body = {
            "experiment_id": str(world.experiment.pk),
            "config": deepcopy(world.metric.config),
            "run": run,
        }
    elif door == "drawer":
        url = f"/model-hub/develops/{world.dataset.pk}/start_evals_process/"
        body = {
            "experiment_id": str(world.experiment.pk),
            "user_eval_ids": [metric_id],
        }
    else:
        url = f"/model-hub/experiments/v2/{world.experiment.pk}/rerun-cells/"
        body = {"user_eval_metric_ids": [metric_id], "failed_only": False}
    return actor["client"].post(
        url, body, format="json", HTTP_IDEMPOTENCY_KEY=key or f"stored-output-{door}"
    )


def _assert_stored_admission(world, before, metric, version, starts):
    """A mere successful response or classic Cell reset is not admission."""
    world.experiment.refresh_from_db()
    state = state_of(world.experiment)
    assert state.get("admission_id") != world.admission, (
        "Stored HTTP scoring needs a new admitted evaluation-only identity"
    )
    assert state.get("state") == "accepted"
    assert sum(start.call_count for start in starts) == 1
    current = _evidence(world)
    for key in ("runs", "items", "bindings"):
        ids = {row["id"] for row in before[key]}
        assert [row for row in current[key] if row["id"] in ids] == before[key]
    # The old output includes its original usage, trace and transport receipt.
    assert _cells(world, column=world.output) == [
        row for row in before["cells"] if row["column_id"] == world.output.pk
    ]
    binding = NativeRunBinding.objects.select_related("run", "snapshot").get(
        variant=world.source, execution_id=state["admission_id"]
    )
    identity = binding.run.metadata["native_identity"]
    assert identity["execution_mode"] == "eval_only"
    assert identity["evaluation_sources"] == native_output_sources(
        binding.snapshot, world.source, world.output, world.links
    )
    assert [pin["metric_id"] for pin in binding.run.evaluator_versions] == [
        str(metric.pk)
    ]
    pin = binding.run.evaluator_versions[0]
    assert pin["evaluator_version_id"] == str(version.pk)
    assert pin["explicit_pin"] is True
    assert identity["snapshot_row_ids"] == [
        str(link.snapshot_row_id) for link in world.links
    ]


@pytest.mark.parametrize("door", ["add", "rerun"])
def test_later_source_scores_all_captured_members(tenants, door):
    from model_hub.models.experiments import ExperimentDatasetTable
    from model_hub.services.native_run_binding import run_examples

    world = _appended_terminal(tenants, later=True)
    before = _evidence(world)
    generation = _run(world).dataset_version_id
    with _scoring_only() as starts:
        _start_stored(world, starts, door=door)
        contexts = state_of(world.experiment)["workflow_args"]["native_runs"]
        assert len(contexts) == 2
        parent_versions = set()
        children = []
        for source_id, context in contexts.items():
            world.source = ExperimentDatasetTable.no_workspace_objects.get(pk=source_id)
            world.output = http._http_output_column(world.experiment, world.source)
            setup = activities._setup_native_prompt(
                str(world.experiment.pk),
                str(world.experiment.snapshot_dataset_id),
                source_id,
                context,
            )
            rows = {str(link.snapshot_row_id) for link in world.links}
            assert set(setup["row_ids"]) == rows
            child = _run(world, context["execution_id"])
            assert child.dataset_version_id == generation
            assert len(run_examples(child.native_binding, child)) == 3
            pins = child.metadata["native_identity"]["evaluation_sources"]
            for ref in pins.values():
                parent = ExperimentRunItem.no_workspace_objects.get(pk=ref["item_id"])
                parent_versions.add(parent.run.dataset_version_id)
                assert (
                    ref["receipt_hash"]
                    == (parent.metadata[_RUN_KEY]["result_receipt"]["payload_hash"])
                )
            with _synthetic_score(world, context) as evaluate:
                for link in world.links:
                    assert _worker(world, context, link)["status"] == "COMPLETED"
            assert evaluate.call_count == 3
            child = _finish_stored(world, context)
            children.append(child)
            assert [item.score_refs[0]["value"] for item in child.items.all()] == [
                0.75,
                0.75,
                0.75,
            ]
            _assert_parent_unchanged(world, before)
        assert parent_versions == {generation, child.dataset_version.parent_version_id}
    _assert_scored_reads(world, children)


def _assert_scored_reads(world, children):
    from django.urls import reverse

    from model_hub.models.develop_dataset import Row
    from model_hub.serializers.dataset_results import DatasetResultsResponseSerializer
    from model_hub.tests.test_http_canonical_evidence import _body

    client = world.actor["client"]
    ids = {str(row["id"]) for row in _evidence(world)["runs"]}
    for deleted in (False, True):
        if deleted:
            Row.no_workspace_objects.filter(dataset=world.dataset).update(
                deleted=True, metadata={"category": "edited"}
            )
            Cell.no_workspace_objects.filter(dataset=world.dataset).update(
                value="edited"
            )
        before = _evidence(world)
        detail = _body(client.get(f"/model-hub/experiments/v2/{world.experiment.pk}/"))
        catalog = _body(
            client.get(
                reverse(
                    "dataset-results-runs", kwargs={"dataset_id": world.dataset.pk}
                ),
                {"experiment_id": str(world.experiment.pk)},
            )
        )
        assert {entry["run_id"] for entry in detail["runs"]} == ids
        assert {entry["run_id"] for entry in catalog["results"]} == ids
        assert detail["opens_in_full_results"] is True
        for child in children:
            rid = str(child.pk)
            for shape in ("grid", "matrix"):
                response = client.get(
                    reverse("dataset-results", kwargs={"dataset_id": world.dataset.pk}),
                    {"runs": rid, "baseline": "none", "shape": shape},
                )
                result = _body(response)
                contract = DatasetResultsResponseSerializer(data=response.json())
                assert contract.is_valid(), contract.errors
                assert contract.validated_data["result"]["rows"] == result["rows"]
                assert result["total"] == 3
                assert {r["values"]["Question"] for r in result["rows"]} == set(
                    world.inputs.values()
                )
                aggregate = result["summary"]["aggregates"][str(world.metric.pk)][rid]
                assert (aggregate["value"], aggregate["n"]) == (0.75, 3)
                for row in result["rows"]:
                    question = row["values"]["Question"]
                    assert row["values"] == {"Question": question}
                    if shape == "grid":
                        assert row["inputs"] == {"Question": question}
                    assert row["expected"] == {
                        "value": {"Expected": f"expected:{question}"},
                        "primary": "Expected",
                    }
                    assert row["metadata"] == {
                        "category": "captured",
                        "split_names": ["holdout"],
                    }
            for link in world.links:
                rows = _body(
                    client.get(
                        reverse(
                            "dataset-row-runs",
                            kwargs={
                                "dataset_id": world.dataset.pk,
                                "row_id": link.source_row_id,
                            },
                        ),
                        {"version": 1},
                    )
                )
                saved = next(entry for entry in rows["runs"] if entry["run_id"] == rid)
                assert saved["example_id"] == str(link.version_item.example_id)
                question = world.inputs[str(link.source_row_id)]
                assert saved["output"] == f"answer:{question}"
                assert saved["dataset_version"]["id"] == str(child.dataset_version_id)
        assert _evidence(world) == before


@pytest.mark.parametrize("door", ["add", "rerun"])
@pytest.mark.parametrize("missing", ["original", "appended", "later"])
def test_missing_generation_receipt_rolls_back_scoring(tenants, door, missing):
    world = _appended_terminal(tenants, later=missing == "later")
    items = ExperimentRunItem.no_workspace_objects.filter(
        run__experiment=world.experiment
    )
    if missing == "original":
        item = items.filter(run__dataset_version__parent_version__isnull=True).first()
    else:
        item = items.get(
            run=_run(world), example_id=world.links[-1].version_item.example_id
        )
    metadata = deepcopy(item.metadata)
    metadata[_RUN_KEY].pop("result_receipt")
    items.filter(pk=item.pk).update(metadata=metadata)
    before = _evidence(world)
    state = deepcopy(state_of(world.experiment))
    metrics = list(world.experiment.user_eval_template_ids.values_list("pk", flat=True))
    version = _new_version(world)
    evaluator_count = UserEvalMetric.no_workspace_objects.count()
    with _scoring_only() as starts:
        response = _add(world, version) if door == "add" else _rescore(world, door)
    assert response.status_code == 400
    assert [start.call_count for start in starts] == [0, 0]
    assert _evidence(world) == before
    world.experiment.refresh_from_db()
    assert state_of(world.experiment) == state
    assert UserEvalMetric.no_workspace_objects.count() == evaluator_count
    assert (
        list(world.experiment.user_eval_template_ids.values_list("pk", flat=True))
        == metrics
    )


@pytest.mark.parametrize(
    "function",
    [
        "claim_http_item",
        "record_http_output",
        "record_http_evaluation",
    ],
)
def test_appended_recorder_failures_keep_classic_entry_point(tenants, function):
    from model_hub.serializers.experiments import (
        opens_in_full_results,
        with_full_results,
    )
    from model_hub.tests.test_experiment_full_results_marker import detail_of, listed

    world = _appended_terminal(tenants, recording_failure=function)
    assert _run(world).status == "failed"
    cell = Cell.no_workspace_objects.get(
        row_id=world.links[-1].snapshot_row_id, column=world.output
    )
    assert (cell.value, cell.status) == ("answer:appended", CellStatus.PASS.value)
    annotated = with_full_results(
        type(world.experiment).no_workspace_objects.filter(pk=world.experiment.pk)
    ).get()
    assert opens_in_full_results(world.experiment) is False
    assert opens_in_full_results(annotated) is False
    client = world.actor["client"]
    assert detail_of(client, world.experiment)["opens_in_full_results"] is False
    assert (
        listed(client, world.dataset)[str(world.experiment.pk)]["opens_in_full_results"]
        is False
    )


@pytest.mark.parametrize("field", ["metadata", "source_trace_id", "external_id"])
def test_stored_parent_requires_unchanged_member_content_and_lineage(tenants, field):
    from model_hub.models.dataset_versions import DatasetVersionItem

    world = _appended_terminal(tenants)
    member = world.links[0].version_item
    value = {"category": "changed"} if field == "metadata" else "changed"
    DatasetVersionItem.no_workspace_objects.filter(pk=member.pk).update(
        **{field: value}
    )
    before = _evidence(world)
    with _scoring_only() as starts:
        response = _rescore(world, "rerun")
    assert response.status_code == 400
    assert [start.call_count for start in starts] == [0, 0]
    assert _evidence(world) == before


def test_add_and_run_admits_stored_http_output_with_new_evaluator(tenants):
    world = _terminal(tenants)
    before = _evidence(world)
    version = _new_version(world)
    with _scoring_only() as starts:
        response = _add(world, version)
        assert response.status_code == 200, response.data
        metric = UserEvalMetric.no_workspace_objects.get(
            pk=response.data["result"]["eval_id"]
        )
        _assert_stored_admission(world, before, metric, version, starts)


@pytest.mark.parametrize("door", ["edit", "drawer", "rerun"])
def test_run_evaluations_admits_stored_http_output_with_new_pin(tenants, door):
    world = _terminal(tenants)
    before = _evidence(world)
    version = _new_version(world)
    world.metric.pinned_version = version
    world.metric.save(update_fields=["pinned_version"])
    with _scoring_only() as starts:
        response = _rescore(world, door)
        assert response.status_code == 200, response.content
        _assert_stored_admission(world, before, world.metric, version, starts)


def test_explicit_http_eval_save_does_not_admit_or_change_receipts(tenants):
    world = _terminal(tenants)
    before = _evidence(world)
    with _scoring_only() as starts:
        response = _rescore(world, "edit", run=False)
        assert response.status_code == 200, response.content
    assert all(start.call_count == 0 for start in starts)
    assert _evidence(world) == before
    world.experiment.refresh_from_db()
    assert state_of(world.experiment)["admission_id"] == world.admission


@pytest.mark.parametrize("door", ["edit", "drawer", "rerun"])
def test_foreign_caller_cannot_score_stored_http_run(tenants, door):
    world = _terminal(tenants)
    before = _evidence(world)
    with _scoring_only() as starts:
        response = _rescore(world, door, actor=tenants["b"])
    assert response.status_code in {400, 403, 404}, response.content
    assert all(start.call_count == 0 for start in starts)
    assert _evidence(world) == before


def test_unlisted_metric_cannot_score_stored_http_run(tenants):
    world = _terminal(tenants)
    before = _evidence(world)
    with _scoring_only() as starts:
        response = _rescore(world, "rerun", metric_id=uuid4())
    assert response.status_code == 400, response.content
    assert all(start.call_count == 0 for start in starts)
    assert _evidence(world) == before


def test_active_http_run_cannot_be_rescored(tenants):
    world = _stage(tenants, evaluated=True)
    before = _evidence(world)
    with _scoring_only() as starts:
        response = _rescore(world, "edit")
    assert response.status_code == 400, response.content
    assert all(start.call_count == 0 for start in starts)
    assert _evidence(world) == before


def test_terminal_http_admission_cannot_authorize_late_score(tenants):
    world = _terminal(tenants)
    before = _evidence(world)
    with _scoring_only(), pytest.raises(HttpEvaluationStopped):
        _evaluate(world, world.links[0], 0)
    assert _evidence(world) == before


def test_stopped_http_admission_cannot_authorize_late_score(tenants):
    world = _stage(tenants, evaluated=True)
    _dispatch(world, world.links[0])
    _stop(world)
    before = _evidence(world)
    with _scoring_only(), pytest.raises(HttpEvaluationStopped):
        _evaluate(world, world.links[0], 0)
    assert _evidence(world) == before
    world.experiment.refresh_from_db()
    assert world.experiment.status == StatusType.CANCELLED.value


def test_add_save_only_preserves_parent_receipts_without_dispatch(tenants):
    world = _terminal(tenants)
    before = _evidence(world)
    with _scoring_only() as starts:
        response = _add(world, _new_version(world), run=False)
        assert response.status_code == 200, response.data
    assert all(start.call_count == 0 for start in starts)
    _assert_parent_unchanged(world, before)
    world.experiment.refresh_from_db()
    assert state_of(world.experiment)["admission_id"] == world.admission


def _start_stored(world, starts, *, door="edit", version=None, key=None):
    before = _evidence(world)
    version = version or _new_version(world)
    if door == "add":
        response = _add(world, version)
        assert response.status_code == 200, response.data
        world.metric = UserEvalMetric.no_workspace_objects.get(
            pk=response.data["result"]["eval_id"]
        )
    else:
        world.metric.pinned_version = version
        world.metric.save(update_fields=["pinned_version"])
        response = _rescore(world, door, key=key)
        assert response.status_code == 200, response.content
    _assert_stored_admission(world, before, world.metric, version, starts)
    context = state_of(world.experiment)["workflow_args"]["native_runs"][
        str(world.source.pk)
    ]
    with patch.object(activities, "close_old_connections"):
        running = activities._mark_experiment_running_sync(
            str(world.experiment.pk), refuse_if_stopped=True
        )
    assert running["status"] == "READY"
    setup = activities._setup_native_prompt(
        str(world.experiment.pk),
        str(world.experiment.snapshot_dataset_id),
        str(world.source.pk),
        context,
    )
    assert set(setup["row_ids"]) == {str(link.snapshot_row_id) for link in world.links}
    return context


def _worker(world, context, link):
    return experiment_runner._process_native_prompt_row(
        str(link.snapshot_row_id),
        str(world.output.pk),
        str(world.experiment.snapshot_dataset_id),
        str(world.experiment.pk),
        context,
    )


def _synthetic_score(world, context, after_evaluate=None):
    """Replace evaluator computation only; keep real result/reason writers."""

    def score(evaluator, row, mappings):
        child = NativeRunBinding.objects.select_related("run").get(
            run_id=context["run_id"]
        )
        parent = child.run.metadata["native_identity"]["evaluation_sources"][
            str(row.pk)
        ]
        item = ExperimentRunItem.no_workspace_objects.get(pk=parent["item_id"])
        assert evaluator._native_output == item.output
        assert evaluator._native_messages is None
        assert evaluator._native_pin["evaluator_version_id"] == str(
            world.metric.pinned_version_id
        )
        assert mappings == world.metric.config["mapping"]
        if after_evaluate:
            after_evaluate()
        response = {"reason": "stored answer checked"}
        column = evaluator._native_result_column()
        reason = evaluator._create_reason_column(
            evaluator.dataset, "", parent_column=column
        )
        assert reason is not None
        evaluator._create_reason_cell(
            evaluator.dataset,
            reason,
            row,
            response,
            response["reason"],
            CellStatus.PASS.value,
        )
        return response, CellStatus.PASS.value, 0.75

    return patch.object(
        NativeEvaluationRunner, "_process_eval_result", autospec=True, side_effect=score
    )


def _assert_parent_unchanged(world, before):
    current = _evidence(world)
    for key in ("runs", "items", "bindings"):
        ids = {row["id"] for row in before[key]}
        assert [row for row in current[key] if row["id"] in ids] == before[key]
    assert _cells(world, column=world.output) == [
        row for row in before["cells"] if row["column_id"] == world.output.pk
    ]


def _finish_stored(world, context):
    with patch.object(activities, "close_old_connections"):
        activities._check_experiment_dataset_status_sync(str(world.source.pk), context)
    run = _run(world, context["execution_id"])
    assert run.status == "completed" and run.completed_at is not None
    return run


@pytest.mark.parametrize("door", ["add", "edit", "drawer", "rerun"])
@pytest.mark.parametrize("appended", [False, True])
def test_stored_worker_writes_score_reason_and_new_canonical_receipt(
    tenants, door, appended
):
    world = _appended_terminal(tenants) if appended else _terminal(tenants)
    before = _evidence(world)
    with _scoring_only() as starts:
        context = _start_stored(world, starts, door=door)
        with _synthetic_score(world, context) as evaluate:
            for link in world.links:
                assert _worker(world, context, link)["status"] == "COMPLETED"
        assert evaluate.call_count == len(world.links)
        run = _finish_stored(world, context)
    assert str(run.pk) != str(_run(world).pk)
    for link in world.links:
        item = run.items.get(example_id=link.version_item.example_id, trial_number=1)
        parent_ref = run.metadata["native_identity"]["evaluation_sources"][
            str(link.snapshot_row_id)
        ]
        parent = ExperimentRunItem.no_workspace_objects.get(pk=parent_ref["item_id"])
        assert item.output == parent.output
        assert item.input == parent.input and item.expected == parent.expected
        assert item.status == "completed"
        assert item.metadata[_RUN_KEY]["result_receipt"]["payload_hash"]
        assert item.trace_id is None and item.root_span_id is None
        assert item.token_usage is None and item.cost is None
        assert len(item.score_refs) == 1
        ref = item.score_refs[0]
        assert ref["metric_id"] == str(world.metric.pk)
        assert ref["evaluator_version_id"] == str(world.metric.pinned_version_id)
        assert ref["status"] == "assessed" and ref["value"] == 0.75
        assert ref.get("explanation") == "stored answer checked"
        score = world.source.columns.get(
            source=SourceChoices.EXPERIMENT_EVALUATION.value,
            source_id=f"{world.source.pk}-{world.output.pk}-sourceid-{world.metric.pk}",
            deleted=False,
        )
        saved = Cell.no_workspace_objects.get(column=score, row=link.snapshot_row)
        assert float(saved.value) == 0.75 and saved.status == CellStatus.PASS.value
        reason = world.source.columns.get(
            source=SourceChoices.EVALUATION_REASON.value,
            source_id=f"{score.pk}-sourceid-{world.metric.pk}",
            deleted=False,
        )
        saved_reason = Cell.no_workspace_objects.get(
            column=reason, row=link.snapshot_row
        )
        assert saved_reason.value == "stored answer checked"
        assert saved_reason.status == CellStatus.PASS.value
    _assert_parent_unchanged(world, before)


@pytest.mark.parametrize("sealed", [False, True])
@pytest.mark.parametrize("appended", [False, True])
def test_stored_worker_receipt_replay_never_evaluates_again(tenants, sealed, appended):
    world = _appended_terminal(tenants) if appended else _terminal(tenants)
    with _scoring_only() as starts:
        context = _start_stored(world, starts)
        with _synthetic_score(world, context):
            for link in world.links:
                assert _worker(world, context, link)["status"] == "COMPLETED"
        if sealed:
            _finish_stored(world, context)
        before = _evidence(world)
        with patch.object(
            NativeEvaluationRunner,
            "_process_eval_result",
            side_effect=AssertionError(
                "Recorded scores must replay without evaluation"
            ),
        ) as evaluate:
            for link in world.links:
                assert _worker(world, context, link)["status"] == "COMPLETED"
        evaluate.assert_not_called()
        assert _evidence(world) == before


def test_stored_admission_key_replay_does_not_dispatch(tenants):
    world = _terminal(tenants)
    with _scoring_only() as starts:
        context = _start_stored(world, starts, door="rerun")
        before = _evidence(world)
        for start in starts:
            start.reset_mock()
        response = _rescore(world, "rerun")
        assert response.status_code == 200, response.content
        assert all(start.call_count == 0 for start in starts)
        assert _evidence(world) == before
        world.experiment.refresh_from_db()
        assert state_of(world.experiment)["admission_id"] == context["execution_id"]


@pytest.mark.parametrize("superseded", [False, True])
@pytest.mark.parametrize("appended", [False, True])
def test_stored_worker_stop_or_new_admission_fences_late_score(
    tenants, superseded, appended
):
    world = _appended_terminal(tenants) if appended else _terminal(tenants)
    parent = _evidence(world)
    after_stop = {}
    with _scoring_only() as starts:
        context = _start_stored(world, starts)

        def stop_during_evaluation():
            # The HTTP fixture's _stop targets world.admission (the immutable
            # parent). Stop this child through the same real request instead.
            with patch(
                "tfc.temporal.experiments.cancel_all_experiment_workflows",
                return_value={
                    "main_cancelled": True,
                    "rerun_cancelled": 0,
                    "main_confirmed": True,
                    "reruns_confirmed": True,
                },
            ):
                response = world.actor["client"].post(
                    f"/model-hub/experiments/v2/{world.experiment.pk}/stop/",
                    {},
                    format="json",
                )
            assert response.status_code == 200, response.content
            child = _run(world, context["execution_id"])
            assert child.status == "cancelled" and child.completed_at is not None
            if superseded:
                for start in starts:
                    start.reset_mock()
                replacement = _start_stored(
                    world,
                    starts,
                    version=world.metric.pinned_version,
                    key="stored-output-after-stop",
                )
                assert replacement["execution_id"] != context["execution_id"]
            after_stop.update(_evidence(world))

        with _synthetic_score(world, context, stop_during_evaluation) as evaluate:
            try:
                _worker(world, context, world.links[-1] if appended else world.links[0])
            except (RunWriteError, HttpEvaluationStopped):
                pass
        evaluate.assert_called_once()
        assert after_stop, "The evaluator must reach the Stop/stale write boundary"
        assert _evidence(world)["cells"] == after_stop["cells"]
        child = _run(world, context["execution_id"])
        assert not any(
            ref.get("status") == "assessed"
            for item in child.items.all()
            for ref in item.score_refs
        )
    _assert_parent_unchanged(world, parent)


@pytest.mark.django_db(transaction=True)
def test_stored_overlapping_delivery_preserves_owner_score_and_receipt(tenants):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, local

    from django.db import connections

    from model_hub.services import native_run_recording

    world = _terminal(tenants)
    parent = _evidence(world)
    entered, release_owner = Event(), Event()
    duplicate_claimed, release_duplicate = Event(), Event()
    delivery = local()
    real_start = native_run_recording.start_attempt
    computed = []
    duplicate_claim = []

    def claim(**kwargs):
        if delivery.name != "B":
            return real_start(**kwargs)
        # Pause only AFTER B actually attempts the durable claim. On RED this
        # holds a stale RUNNING item; on FIX the real claim refuses execution.
        try:
            item = real_start(**kwargs)
        except RunWriteError:
            duplicate_claim.append("refused")
            raise
        else:
            duplicate_claim.append(item.status)
            return item
        finally:
            duplicate_claimed.set()
            assert release_duplicate.wait(30), "duplicate continuation not released"

    def score(evaluator, row, mappings):
        computed.append(delivery.name)
        if delivery.name == "A":
            entered.set()
            assert release_owner.wait(30), "owner evaluation not released"
        response = {"reason": f"owner-{delivery.name}"}
        return response, CellStatus.PASS.value, 0.75 if delivery.name == "A" else 0.25

    def deliver(name, context):
        delivery.name = name
        try:
            return _worker(world, context, world.links[0])
        except RunWriteError as exc:
            return exc
        finally:
            connections.close_all()

    with _scoring_only() as starts:
        context = _start_stored(world, starts, door="rerun")
        with (
            patch.object(native_run_recording, "start_attempt", side_effect=claim),
            patch.object(
                NativeEvaluationRunner,
                "_process_eval_result",
                autospec=True,
                side_effect=score,
            ),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            try:
                owner = pool.submit(deliver, "A", context)
                assert entered.wait(30), "owner never reached evaluator computation"
                duplicate = pool.submit(deliver, "B", context)
                assert duplicate_claimed.wait(30), "duplicate never reached real claim"
                release_owner.set()
                assert owner.result(timeout=30)["status"] == "COMPLETED"
                child = _run(world, context["execution_id"])
                assert child.status == "running" and child.completed_at is None
                assert child.items.count() == 1, (
                    "the other planned row must remain unfinished"
                )
                item = child.items.get()
                assert item.status == "completed"
                assert item.score_refs[0]["value"] == 0.75
                assert item.score_refs[0]["explanation"] == "owner-A"
                sealed = _evidence(world)
                release_duplicate.set()
                result = duplicate.result(timeout=30)
            finally:
                release_owner.set()
                release_duplicate.set()
        # RED fails on actual duplicate computation, never on a timeout.
        assert computed == ["A"]
        # A terminal fence alone is insufficient: B must not acquire in-flight
        # execution rights while A is still computing outside database locks.
        assert duplicate_claim == ["refused"]
        assert isinstance(result, RunWriteError)
        assert _evidence(world) == sealed
        with patch.object(
            NativeEvaluationRunner,
            "_process_eval_result",
            side_effect=AssertionError("terminal retry must replay"),
        ) as evaluate:
            assert _worker(world, context, world.links[0])["status"] == "COMPLETED"
        evaluate.assert_not_called()
    _assert_parent_unchanged(world, parent)


@pytest.mark.parametrize("writer", ["score", "reason"])
def test_stored_terminal_row_refuses_stale_classic_writer(tenants, writer):
    world = _terminal(tenants)
    with _scoring_only() as starts:
        context = _start_stored(world, starts, door="rerun")
        with _synthetic_score(world, context) as evaluate:
            assert _worker(world, context, world.links[0])["status"] == "COMPLETED"
        # Keep the exact pre-terminal evaluator instance, not a fresh status.
        evaluator = evaluate.call_args.args[0]
        child = _run(world, context["execution_id"])
        assert child.status == "running" and child.items.count() == 1
        score = world.source.columns.get(
            source=SourceChoices.EXPERIMENT_EVALUATION.value,
            source_id=f"{world.source.pk}-{world.output.pk}-sourceid-{world.metric.pk}",
            deleted=False,
        )
        reason = world.source.columns.get(
            source=SourceChoices.EVALUATION_REASON.value,
            source_id=f"{score.pk}-sourceid-{world.metric.pk}",
            deleted=False,
        )
        before = _evidence(world)
        write = (
            evaluator._create_cell
            if writer == "score"
            else evaluator._create_reason_cell
        )
        with pytest.raises(RunWriteError, match="terminal"):
            write(
                evaluator.dataset,
                score if writer == "score" else reason,
                world.links[0].snapshot_row,
                {"reason": "late overwrite"},
                0.25 if writer == "score" else "late overwrite",
                CellStatus.PASS.value,
            )
        assert _evidence(world) == before


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_stored_real_temporal_parent_child_records_scores_without_http(
    tenants, monkeypatch
):
    import asyncio

    from asgiref.sync import sync_to_async
    from temporalio.client import Client
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import UnsandboxedWorkflowRunner, Worker

    from tfc.temporal.experiments import client as workflow_client
    from tfc.temporal.experiments.workflows import (
        ProcessPromptV2Workflow,
        RerunCellsV2Workflow,
    )

    def prepare():
        world = _terminal(tenants)
        before = _evidence(world)
        world.metric.pinned_version = _new_version(world)
        world.metric.save(update_fields=["pinned_version"])
        return world, before

    world, before = await sync_to_async(prepare)()
    # Same loopback facility as test_experiment_run_control; no skip/fallback.
    async with await WorkflowEnvironment.start_local() as env:

        async def client():
            return await Client.connect(env.client.service_client.config.target_host)

        monkeypatch.setattr(workflow_client, "get_client", client)
        with ExitStack() as stack:
            forbidden = [
                stack.enter_context(
                    patch.object(
                        owner,
                        name,
                        side_effect=AssertionError(
                            "stored Temporal scoring must not prepare or dispatch HTTP/prompt work"
                        ),
                    )
                )
                for owner, name in (
                    (http, "prepare_http_experiment"),
                    (http, "bind_http_submission"),
                    (http, "run_http_snapshot_row"),
                    (http, "execute_and_persist_http_row"),
                    (outbound_destination, "request"),
                    (experiment_runner, "RunPrompt"),
                )
            ]

            def admit():
                # Real authenticated admission AND real Temporal starter. No
                # manual Running transition, setup helper or row helper here.
                response = _rescore(world, "rerun")
                assert response.status_code == 200, response.content
                world.experiment.refresh_from_db()
                state = state_of(world.experiment)
                assert state["state"] == "accepted"
                context = state["workflow_args"]["native_runs"][str(world.source.pk)]
                assert _run(world, context["execution_id"]).status == "queued"
                assert state["workflow_args"]["eval_only"] is True
                return state, context

            # Admit before starting the worker: the parent consumes the exact
            # committed input, rather than a test reconstruction of its fields.
            state, context = await sync_to_async(admit)()
            evaluate = stack.enter_context(_synthetic_score(world, context))
            async with Worker(
                env.client,
                task_queue="tasks_l",
                workflow_runner=UnsandboxedWorkflowRunner(),
                workflows=[RerunCellsV2Workflow, ProcessPromptV2Workflow],
                activities=[
                    activities.setup_experiment_activity,
                    activities.mark_experiment_running_activity,
                    activities.analyze_dependencies_activity,
                    activities.resolve_edt_columns_activity,
                    activities.setup_prompt_v2_activity,
                    activities.process_row_activity,
                    activities.run_base_evaluation_activity,
                    activities.process_batch_template_eval_activity,
                    activities.check_column_status_activity,
                    activities.check_experiment_dataset_status_activity,
                    activities.check_experiment_status_activity,
                    activities.cleanup_running_cells_activity,
                    activities.stop_experiment_cleanup_activity,
                    activities.mark_experiment_failed_activity,
                ],
            ):
                handle = env.client.get_workflow_handle(state["workflow_id"])
                result = await asyncio.wait_for(handle.result(), 90)
                assert result["total_rows_processed"] == len(world.links)
                assert result["failed_rows"] == 0
                history = await handle.fetch_history()
                children = [
                    event.start_child_workflow_execution_initiated_event_attributes
                    for event in history.events
                    if event.HasField(
                        "start_child_workflow_execution_initiated_event_attributes"
                    )
                ]
                assert [child.workflow_type.name for child in children] == [
                    "ProcessPromptV2Workflow"
                ]
            assert evaluate.call_count == len(world.links)
            for call in forbidden:
                call.assert_not_called()

    def assert_persisted():
        run = _run(world, context["execution_id"])
        assert run.status == "completed" and run.completed_at is not None
        assert run.items.count() == len(world.links)
        for link in world.links:
            item = run.items.get(
                example_id=link.version_item.example_id, trial_number=1
            )
            assert item.status == "completed"
            assert item.metadata[_RUN_KEY]["result_receipt"]["payload_hash"]
            ref = item.score_refs[0]
            assert ref["metric_id"] == str(world.metric.pk)
            assert ref["evaluator_version_id"] == str(world.metric.pinned_version_id)
            assert ref["status"] == "assessed" and ref["value"] == 0.75
            assert ref["explanation"] == "stored answer checked"
            score = world.source.columns.get(
                source=SourceChoices.EXPERIMENT_EVALUATION.value,
                source_id=f"{world.source.pk}-{world.output.pk}-sourceid-{world.metric.pk}",
                deleted=False,
            )
            reason = world.source.columns.get(
                source=SourceChoices.EVALUATION_REASON.value,
                source_id=f"{score.pk}-sourceid-{world.metric.pk}",
                deleted=False,
            )
            assert (
                float(
                    Cell.no_workspace_objects.get(
                        column=score, row=link.snapshot_row
                    ).value
                )
                == 0.75
            )
            assert (
                Cell.no_workspace_objects.get(
                    column=reason, row=link.snapshot_row
                ).value
                == "stored answer checked"
            )
        _assert_parent_unchanged(world, before)

    await sync_to_async(assert_persisted)()
