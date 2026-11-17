"""Experiment evaluations are steered by saved feedback like dataset runs.

Feedback saved on a dataset's evaluation is stored under that dataset's own
column ids. An experiment on the dataset runs the same evaluation on its
snapshot, whose columns have new ids. Its judge calls carry the feedback the
dataset run of that evaluation reads: the run workspace's feedback and
same-organization feedback saved with no workspace before the release cutoff,
for datasets with and without a workspace. An experiment uses the feedback of
its own workspace, and the dataset's feedback when the dataset has no
workspace. Feedback saved on the experiment's own page keeps steering it.

The experiment is created through the experiments API and its rows run in the
worker functions. Real ORM rows, feedback views, readers and vector SQL; only
embedding inference, the prompt call, billing and the judge engine are
replaced.
"""

import uuid

import pytest

from accounts.models.workspace import Workspace
from agentic_eval.core.embeddings import embedding_manager as em
from model_hub.models.choices import (
    CellStatus,
    DataTypeChoices,
    FeedbackSourceChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import (
    EvalTemplate,
    EvalTemplateVersion,
    UserEvalMetric,
)
from model_hub.models.native_experiment_runs import NativeExperimentSnapshot
from model_hub.tests.test_feedback_rag_workspace_scope import (
    feedback_store as feedback_store,
)
from model_hub.tests.test_native_prompt_create_bridge import (
    create,
    create_body,
    row,
    setup,
)
from model_hub.tests.test_native_prompt_create_bridge import (
    tenants as tenants,
)
from model_hub.tests.test_pinned_version_native import native_engine, upstream_output

pytestmark = [pytest.mark.integration, pytest.mark.django_db(transaction=True)]

FEEDBACK_URL = "/model-hub/feedback/"
JUDGE_CONFIG = {
    "eval_type_id": "CustomPromptEvaluator",
    "output": "Pass/Fail",
    "required_keys": ["output"],
    "rule_prompt": "Is {{output}} a correct answer?",
    "model": "turing_small",
}
# Read by the dataset run and by the experiment on that dataset.
READ = {"dataset-feedback", "older-unscoped"}
# Unrelated feedback rows, stored for the same evaluation and column. Each run
# uses the feedback of its own workspace, and the dataset's feedback when the
# dataset has no workspace, saved before the cutoff: READ.
UNRELATED = {
    "unrelated-scoped-1",
    "unrelated-scoped-2",
    "unrelated-unscoped",
    "newer-unscoped",
}
MARKERS = READ | UNRELATED | {"experiment-feedback"}
KINDS = ["no-workspace", "workspace"]


def _markers(value):
    text = str(value)
    return {marker for marker in MARKERS if f"{marker}-comment" in text}


def _source_row_dict(world, marker):
    """The row a dataset feedback writer embeds: the source row's cells by
    column id and by name, then the feedback."""
    return {
        str(world.question.id): world.question_value,
        world.question.name: world.question_value,
        "feedback_comment": f"{marker}-comment",
        "feedback_value": "Failed",
        "feedback_id": str(uuid.uuid4()),
    }


def _insert_unscoped(world, marker, *, organization_id):
    """A dataset feedback vector as production stored it before the release
    cutoff: no workspace key at all, keyed by the source column id."""
    metadata = {
        "item_id": str(uuid.uuid4()),
        "organization_id": str(organization_id),
        "input_type": "text",
        "index_column": world.question_value,
        **_source_row_dict(world, marker),
        "feedback_input": str(world.question.id),
    }
    world.store.manager.db_client.upsert_vector(
        table_name=em.FEEDBACK_TABLE_NAME,
        eval_id=str(world.template.id),
        vector=[1.0, 0.0],
        metadata=metadata,
        unique_keys=["item_id"],
        mark_existing_deleted=False,
    )


def _write(world, row_dict, field, *, organization_id, workspace_id):
    """The feedback writer every feedback view calls."""
    world.store.manager.parallel_process_metadata(
        eval_id=world.template.id,
        metadatas=row_dict,
        inputs_formater=[field],
        organization_id=organization_id,
        workspace_id=workspace_id,
    )


def _save_dataset_feedback(world, marker):
    """Feedback saved on the dataset's evaluation cell and submitted, through
    the feedback API the dataset page calls."""
    client = world.actor["client"]
    created = client.post(
        FEEDBACK_URL,
        {
            "source": FeedbackSourceChoices.DATASET.value,
            "source_id": str(world.eval_column.id),
            "user_eval_metric": str(world.dataset_metric.id),
            "value": "Failed",
            "explanation": f"{marker}-comment",
            "row_id": str(world.rows[0].id),
        },
        format="json",
    )
    assert created.status_code == 200, created.content
    feedback_id = created.json()["result"]["id"]
    submitted = client.post(
        f"{FEEDBACK_URL}submit-feedback/",
        {
            "action_type": "retune",
            "feedback_id": str(feedback_id),
            "user_eval_metric_id": str(world.dataset_metric.id),
            "value": "Failed",
            "explanation": f"{marker}-comment",
        },
        format="json",
    )
    assert submitted.status_code == 200, submitted.content


class World:
    pass


def _world(accounts, store, kind):
    """A dataset with an evaluation and saved feedback.

    The feedback on the evaluation cell is saved through the API while the
    dataset is in the default workspace. With ``kind == "no-workspace"`` the
    dataset then loses its workspace, like a dataset made before workspaces.
    The unrelated rows are keyed by the same source column id, so the run's
    scope alone picks what it uses: the feedback of its own workspace, and
    the dataset's feedback when the dataset has no workspace, saved before
    the cutoff.
    """
    world = World()
    world.store = store
    world.actor = actor = accounts["a"]
    world.unrelated = accounts["b"]
    world.dataset = dataset = actor["exp"]["dataset"]
    # The experiment body names the dataset's one input column.
    world.body = create_body(actor)
    world.question = Column.no_workspace_objects.get(dataset=dataset)
    world.rows = [actor["exp"]["row"]]
    world.question_value = "Is the sky blue?"
    Cell.no_workspace_objects.filter(
        dataset=dataset, column=world.question, row=world.rows[0]
    ).update(value=world.question_value)
    second_row = Row.no_workspace_objects.create(dataset=dataset, order=1)
    Cell.no_workspace_objects.create(
        dataset=dataset,
        column=world.question,
        row=second_row,
        value="Is grass green?",
        status=CellStatus.PASS.value,
    )
    world.rows.append(second_row)

    entry = world.body["user_eval_metrics"][0]
    world.template = EvalTemplate.no_workspace_objects.get(pk=entry["template_id"])
    world.template.config = dict(JUDGE_CONFIG)
    world.template.criteria = JUDGE_CONFIG["rule_prompt"]
    world.template.save(update_fields=["config", "criteria"])
    pin = EvalTemplateVersion.no_workspace_objects.get(pk=entry["pinned_version_id"])
    pin.config_snapshot = dict(JUDGE_CONFIG)
    pin.save(update_fields=["config_snapshot"])
    # The experiment maps the judge's input to the dataset's question column,
    # as the dataset's own evaluation does.
    entry["model"] = "turing_small"
    entry["config"] = {"mapping": {"output": str(world.question.id)}, "config": {}}

    world.dataset_metric = UserEvalMetric.no_workspace_objects.create(
        name="dataset judge " + uuid.uuid4().hex[:8],
        organization=actor["org"],
        workspace=actor["workspace"],
        user=actor["user"],
        template=world.template,
        dataset=dataset,
        model="turing_small",
        config={"mapping": {"output": str(world.question.id)}, "config": {}},
        status=StatusType.COMPLETED.value,
    )
    world.eval_column = Column.no_workspace_objects.create(
        name="dataset judge",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.EVALUATION.value,
        source_id=str(world.dataset_metric.id),
        status=StatusType.COMPLETED.value,
    )
    for each in world.rows:
        Cell.no_workspace_objects.create(
            dataset=dataset,
            column=world.eval_column,
            row=each,
            value="Passed",
            status=CellStatus.PASS.value,
        )

    _save_dataset_feedback(world, "dataset-feedback")
    if kind == "no-workspace":
        Dataset.no_workspace_objects.filter(pk=dataset.pk).update(workspace=None)
        dataset.refresh_from_db()
        assert dataset.workspace_id is None, "SETUP: the dataset has no workspace"

    run_org = actor["org"].id
    unrelated_workspace = Workspace.no_workspace_objects.create(
        name="workspace " + uuid.uuid4().hex[:8],
        organization=actor["org"],
        is_default=False,
        is_active=True,
        created_by=actor["user"],
    )
    _insert_unscoped(world, "older-unscoped", organization_id=run_org)
    _insert_unscoped(
        world, "unrelated-unscoped", organization_id=world.unrelated["org"].id
    )
    question = str(world.question.id)
    _write(
        world,
        _source_row_dict(world, "unrelated-scoped-1"),
        question,
        organization_id=run_org,
        workspace_id=unrelated_workspace.id,
    )
    _write(
        world,
        _source_row_dict(world, "unrelated-scoped-2"),
        question,
        organization_id=world.unrelated["org"].id,
        workspace_id=world.unrelated["workspace"].id,
    )
    # This build marks what it writes with no workspace.
    _write(
        world,
        _source_row_dict(world, "newer-unscoped"),
        question,
        organization_id=run_org,
        workspace_id=None,
    )
    stored = _stored(world)
    assert set(stored) == READ | UNRELATED, (
        f"SETUP: every feedback row is stored once: {sorted(stored)}"
    )
    marked = stored["newer-unscoped"].get(em.FEEDBACK_NO_WORKSPACE_AFTER_CUTOFF_KEY)
    assert marked == "1", "SETUP: this build marks feedback it writes with no workspace"
    return world


def _stored(world):
    """Active feedback vectors of the evaluation, by marker."""
    rows = world.store.manager.db_client.client.execute(
        "SELECT metadata.key, metadata.value FROM feedbacks WHERE deleted = 0"
    )
    stored = {}
    for keys, values in rows:
        metadata = dict(zip(keys, values, strict=True))
        for marker in _markers(metadata.get("feedback_comment", "")):
            assert marker not in stored, f"SETUP: one vector per marker ({marker})"
            stored[marker] = metadata
    return stored


def _dataset_run_read(world):
    """What a dataset run of the evaluation reads: its metric, its keys."""
    from model_hub.views.eval_runner import EvaluationRunner

    metric = UserEvalMetric.no_workspace_objects.select_related(
        "template", "dataset"
    ).get(pk=world.dataset_metric.pk)
    runner = EvaluationRunner(
        user_eval_metric_id=str(metric.id),
        format_output=True,
        source="dataset_evaluation",
    )
    runner.user_eval_metric = metric
    runner.dataset = metric.dataset
    runner.eval_template = metric.template
    required_field, mapping = runner._get_required_fields_and_mappings(
        user_eval_metric=metric, mapping=[world.question_value]
    )
    return runner.get_few_shot_examples(mapping, required_field)


def _create_experiment(world):
    response, experiment, _body, _start = create(world.actor, world.body)
    assert response.status_code == 200, response.content
    experiment.refresh_from_db()
    snapshot = NativeExperimentSnapshot.objects.get(experiment=experiment)
    world.snapshot_question = snapshot.column_map[str(world.question.id)]
    assert world.snapshot_question != str(world.question.id), (
        "SETUP: the snapshot copies the question column under a new id"
    )
    assert experiment.snapshot_dataset.workspace_id == world.dataset.workspace_id, (
        "SETUP: the snapshot keeps the dataset's workspace"
    )
    return experiment


def _run_rows(experiment):
    """Each row of the experiment, as the worker runs it. Returns the judge
    calls' keyword arguments."""
    context, prepared = setup(experiment)
    with native_engine() as judge, upstream_output():
        for row_id in prepared["row_ids"]:
            result = row(experiment, context, prepared, row_id)
            assert result["status"] == "COMPLETED", result
    calls = [call.kwargs for call in judge.engine.run.call_args_list]
    assert len(calls) == len(prepared["row_ids"]) == 2, (
        "SETUP: the judge runs once per experiment row"
    )
    return calls


def _carrying(calls, markers):
    return [call for call in calls if markers <= _markers(call.get("few_shots"))]


@pytest.mark.parametrize("kind", KINDS)
def test_experiment_judge_calls_carry_saved_dataset_feedback(
    tenants, feedback_store, kind
):
    world = _world(tenants, feedback_store, kind)
    assert _markers(_dataset_run_read(world)) == READ, (
        "SETUP: the dataset run of the evaluation reads the run workspace's "
        "feedback and older same-organization feedback with no workspace"
    )
    experiment = _create_experiment(world)
    calls = _run_rows(experiment)
    steered = _carrying(calls, READ)
    assert len(steered) == len(calls), (
        f"{len(steered)} of {len(calls)} experiment judge calls carried the "
        "feedback the dataset run of the same evaluation reads; read: "
        f"{[sorted(_markers(call.get('few_shots'))) for call in calls]}"
    )
    unrelated_read = [
        sorted(_markers(call.get("few_shots")) & UNRELATED) for call in calls
    ]
    assert not any(unrelated_read), (
        "an experiment uses the feedback of its own workspace, and the "
        "dataset's feedback when the dataset has no workspace, saved before "
        f"the cutoff; it also read: {unrelated_read}"
    )


def test_experiment_feedback_read_uses_the_source_dataset_column_keys(
    tenants, feedback_store, monkeypatch
):
    world = _world(tenants, feedback_store, "no-workspace")
    experiment = _create_experiment(world)
    real = em.EmbeddingManager.retrieve_avg_rag_based_examples
    keys = []

    def recording(self, *args, **kwargs):
        keys.append([str(column) for column in kwargs.get("input_cols") or []])
        return real(self, *args, **kwargs)

    monkeypatch.setattr(
        em.EmbeddingManager, "retrieve_avg_rag_based_examples", recording
    )
    _run_rows(experiment)
    assert keys, "SETUP: the experiment's judge reads feedback"
    question = str(world.question.id)
    assert any(question in each for each in keys), (
        "an experiment's feedback read names the source dataset's column ids, "
        f"the keys the dataset's feedback is stored under ({question}); it "
        f"named {keys} (the snapshot copies the column as "
        f"{world.snapshot_question})"
    )


def test_experiment_judge_calls_carry_its_own_and_the_dataset_feedback(
    tenants, feedback_store
):
    world = _world(tenants, feedback_store, "workspace")
    experiment = _create_experiment(world)
    # Feedback saved on the experiment's page is stored under the snapshot's
    # column ids and the dataset's workspace.
    row_dict = {
        world.snapshot_question: world.question_value,
        world.question.name: world.question_value,
        "feedback_comment": "experiment-feedback-comment",
        "feedback_value": "Failed",
        "feedback_id": str(uuid.uuid4()),
    }
    _write(
        world,
        row_dict,
        world.snapshot_question,
        organization_id=world.actor["org"].id,
        workspace_id=world.actor["workspace"].id,
    )
    calls = _run_rows(experiment)
    wanted = READ | {"experiment-feedback"}
    steered = _carrying(calls, wanted)
    assert len(steered) == len(calls), (
        f"{len(steered)} of {len(calls)} experiment judge calls carried both "
        "the experiment's own feedback and the dataset's; read: "
        f"{[sorted(_markers(call.get('few_shots'))) for call in calls]}"
    )
    for call in calls:
        shots = list(call.get("few_shots") or [])
        assert shots.count(shots[0]) == 1, (
            "the few-shot instruction block opens the examples once"
        )


@pytest.mark.parametrize("kind", KINDS)
def test_experiment_page_feedback_keeps_steering_its_runs(
    tenants, feedback_store, kind
):
    world = _world(tenants, feedback_store, kind)
    experiment = _create_experiment(world)
    workspace_id = world.dataset.workspace_id
    row_dict = {
        world.snapshot_question: world.question_value,
        world.question.name: world.question_value,
        "feedback_comment": "experiment-feedback-comment",
        "feedback_value": "Failed",
        "feedback_id": str(uuid.uuid4()),
    }
    _write(
        world,
        row_dict,
        world.snapshot_question,
        organization_id=world.actor["org"].id,
        workspace_id=workspace_id,
    )
    calls = _run_rows(experiment)
    carried = [
        "experiment-feedback" in _markers(call.get("few_shots")) for call in calls
    ]
    if kind == "workspace":
        assert all(carried), (
            "feedback saved on an experiment's page steers its runs: "
            f"{carried}"
        )
    else:
        assert not any(carried), (
            "an experiment uses the feedback of its own workspace, and the "
            "dataset's feedback when the dataset has no workspace, saved before "
            "the cutoff; feedback saved on its page while its dataset has no "
            f"workspace is written after the cutoff: {carried}"
        )
    unrelated_read = [
        sorted(_markers(call.get("few_shots")) & UNRELATED) for call in calls
    ]
    assert not any(unrelated_read), (
        "an experiment uses the feedback of its own workspace, and the "
        "dataset's feedback when the dataset has no workspace, saved before "
        f"the cutoff; it also read: {unrelated_read}"
    )
