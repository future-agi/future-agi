"""One rule decides where an experiment opens, and the list says it too.

The runs view shows an experiment only through runs (ExperimentRun) bound to
its variants. An experiment opens there only when the runs view can show all
of it: it has a NativeExperimentSnapshot and every live variant has at least
one counted run. Any other experiment (made by the production writers, run
with a prompt that has no saved version, or rerun or extended through the
classic path) opens in the original results view, which reads the classic
rows and stats, where every variant is kept. The experiments list carries the
same answer per row, and the detail carries it beside results_availability,
whose reasons do not change.

Production-shaped fixtures are written row by row the way the production
writers store them and never call this build's snapshot service (which also
records a NativeExperimentSnapshot). Fixtures made on this build use this
build's own writers (create_dataset_snapshot, bind_native_run).
"""

import json
import uuid
from types import SimpleNamespace

import pytest
from django.db import DatabaseError, connection
from django.test.utils import CaptureQueriesContext
from rest_framework import serializers

from model_hub.models.choices import (
    CellStatus,
    DatasetSourceChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.experiments import (
    ExperimentDatasetTable,
    ExperimentPromptConfig,
    ExperimentRun,
    ExperimentsTable,
)
from model_hub.models.native_experiment_runs import NativeRunBinding
from model_hub.serializers import experiments as experiment_serializers
from model_hub.serializers.experiments import (
    ExperimentDetailV2Serializer,
    ExperimentListV2Serializer,
)
from model_hub.services.dataset_snapshot import create_dataset_snapshot
from model_hub.services.native_run_binding import bind_native_run

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("urlconf_loaded_without_workspace_context"),
]

LIST_URL = "/model-hub/experiments/v2/list/"
MODEL = "gpt-4o-mini"
PROMPT_NAME = "Support prompt"


def detail_url(experiment):
    return f"/model-hub/experiments/v2/{experiment.id}/"


# ---------------------------------------------------------------------------
# Writers: production shapes, written row by row
# ---------------------------------------------------------------------------


def source_dataset(organization, workspace, user, name):
    """A dataset with one input column and two rows, as the dataset page saves it."""
    dataset = Dataset.no_workspace_objects.create(
        name=name,
        organization=organization,
        workspace=workspace,
        user=user,
        column_order=[],
        column_config={},
    )
    question = Column.no_workspace_objects.create(
        dataset=dataset,
        name="question",
        data_type="text",
        source=SourceChoices.OTHERS.value,
    )
    for order in (1, 2):
        row = Row.no_workspace_objects.create(dataset=dataset, order=order)
        Cell.no_workspace_objects.create(
            dataset=dataset, column=question, row=row, value=f"Question {order}"
        )
    Dataset.no_workspace_objects.filter(pk=dataset.pk).update(
        column_order=[str(question.id)]
    )
    dataset.refresh_from_db()
    return dataset


def mark_completed(experiment, variant, output):
    """The runner's completion checks move each level to Completed with update()."""
    Column.no_workspace_objects.filter(pk=output.pk).update(
        status=StatusType.COMPLETED.value
    )
    ExperimentDatasetTable.no_workspace_objects.filter(pk=variant.pk).update(
        status=StatusType.COMPLETED.value
    )
    ExperimentsTable.no_workspace_objects.filter(pk=experiment.pk).update(
        status=StatusType.COMPLETED.value
    )
    experiment.refresh_from_db()
    return experiment


def write_answers(dataset, output, rows):
    """Run output cells (views/experiment_runner.py _process_row_impl)."""
    for row in rows:
        Cell.no_workspace_objects.create(
            dataset=dataset,
            column=output,
            row=row,
            value=f"Answer {row.order}",
            value_infos=json.dumps({"reason": f"Answer {row.order}"}),
            status=CellStatus.PASS.value,
        )


def production_snapshot_experiment(dataset, user, prompt_version, name):
    """The production build's experiment create and run, with its snapshot copy.

    Create (views/experiments.py ExperimentsTableV2View.post): the experiment
    with prompt_config=[], a snapshot copy of the dataset
    (services/dataset_snapshot.py create_dataset_snapshot: columns with
    source_id = the source column id, rows, cells, linked as
    snapshot_dataset), one variant per prompt and model with its
    ExperimentPromptConfig, and its output column (column_service
    create_experiment_column: source=experiment, source_id=variant id). Run:
    one output cell per snapshot row. No ExperimentRun, no
    NativeExperimentSnapshot (that model does not exist on production).
    """
    experiment = ExperimentsTable.no_workspace_objects.create(
        name=name,
        dataset=dataset,
        column=None,
        experiment_type="llm",
        prompt_config=[],
        user=user,
        run_scope={},
        run_metadata={},
        run_policy={},
    )
    snapshot = Dataset.no_workspace_objects.create(
        source=DatasetSourceChoices.EXPERIMENT_SNAPSHOT.value,
        name=f"[Snapshot] {dataset.name}",
        organization=dataset.organization,
        workspace=dataset.workspace,
        model_type=dataset.model_type,
        column_config=dataset.column_config or {},
        dataset_config=dataset.dataset_config or {},
        column_order=[],
    )
    columns = {}
    for column in Column.no_workspace_objects.filter(dataset=dataset):
        columns[column.id] = Column.no_workspace_objects.create(
            name=column.name,
            data_type=column.data_type,
            dataset=snapshot,
            source=column.source,
            source_id=str(column.id),
            metadata=column.metadata or {},
            status=column.status,
        )
    rows = {}
    for row in Row.no_workspace_objects.filter(dataset=dataset).order_by("order"):
        rows[row.id] = Row.no_workspace_objects.create(
            dataset=snapshot, order=row.order, metadata=row.metadata or {}
        )
    for cell in Cell.no_workspace_objects.filter(dataset=dataset):
        Cell.no_workspace_objects.create(
            dataset=snapshot,
            column=columns[cell.column_id],
            row=rows[cell.row_id],
            value=cell.value,
            value_infos=cell.value_infos,
            status=cell.status,
        )
    ExperimentsTable.no_workspace_objects.filter(pk=experiment.pk).update(
        snapshot_dataset=snapshot
    )
    variant_name = f"{PROMPT_NAME}-{MODEL}"
    variant = ExperimentDatasetTable.no_workspace_objects.create(
        name=variant_name,
        experiment=experiment,
        status=StatusType.NOT_STARTED.value,
    )
    ExperimentPromptConfig.no_workspace_objects.create(
        experiment_dataset=variant,
        prompt_template=prompt_version.original_template,
        prompt_version=prompt_version,
        name=variant_name,
        model=MODEL,
        model_display_name=None,
        model_config={},
        model_params={},
        configuration={},
        output_format="string",
        order=0,
        messages=None,
    )
    output = Column.no_workspace_objects.create(
        name=variant_name,
        data_type="text",
        dataset=snapshot,
        source=SourceChoices.EXPERIMENT.value,
        source_id=str(variant.id),
        status=StatusType.NOT_STARTED.value,
    )
    variant.columns.add(output)
    Dataset.no_workspace_objects.filter(pk=snapshot.pk).update(
        column_order=[*(str(c.id) for c in columns.values()), str(output.id)]
    )
    write_answers(snapshot, output, rows.values())
    return mark_completed(experiment, variant, output)


def production_experiment_without_snapshot(dataset, user, name):
    """The production build's first experiment create and run (no snapshot).

    Create (views/experiments.py ExperimentsTableView.post): the experiment
    with its prompt_config list. Run (views/experiment_runner.py
    process_prompt): a variant "<experiment>-<prompt>-<model>" linked by the
    experiment FK and the experiments_datasets list, its output column and
    cells on the experiment's own dataset.
    """
    experiment = ExperimentsTable.no_workspace_objects.create(
        name=name,
        dataset=dataset,
        column=None,
        prompt_config=[
            {
                "name": PROMPT_NAME,
                "messages": [{"role": "user", "content": "Answer {{question}}"}],
                "model": [MODEL],
                "configuration": {},
            }
        ],
        user=user,
    )
    variant = ExperimentDatasetTable.no_workspace_objects.create(
        name=f"{experiment.name}-{PROMPT_NAME}-{MODEL}",
        experiment=experiment,
        status=StatusType.RUNNING.value,
    )
    output = Column.no_workspace_objects.create(
        name=variant.name,
        data_type="text",
        dataset=dataset,
        source=SourceChoices.EXPERIMENT.value,
        source_id=str(variant.id),
        status=StatusType.RUNNING.value,
    )
    variant.columns.add(output)
    experiment.experiments_datasets.add(variant)
    rows = Row.no_workspace_objects.filter(dataset=dataset).order_by("order")
    write_answers(dataset, output, rows)
    return mark_completed(experiment, variant, output)


def sample_experiment(dataset):
    """The sample experiment every organization gets (accounts/user_onboard.py).

    upload_demo_dataset writes "Response Generation" with its prompt_config
    list, Completed, no user and no snapshot; one Completed variant with a
    legacy prompt config and no experiment FK, linked only through
    experiments_datasets; and its output column (source=experiment,
    source_id=variant id) with cells on the dataset itself.
    """
    target = Column.no_workspace_objects.get(
        dataset=dataset, source=SourceChoices.OTHERS.value
    )
    experiment = ExperimentsTable.no_workspace_objects.create(
        name="Response Generation",
        dataset=dataset,
        column_id=target.id,
        prompt_config=[
            {
                "name": "Generate Answer",
                "messages": [
                    {"role": "user", "content": "Answer the question: {{question}}"}
                ],
                "model": ["o1-mini"],
                "configuration": {},
            }
        ],
        status=StatusType.COMPLETED.value,
    )
    variant = ExperimentDatasetTable.no_workspace_objects.create(
        status=StatusType.COMPLETED.value,
        name="Response Generation-Generate Answer-1-o1-mini",
        legacy_prompt_config={
            "name": "Generate Answer",
            "model": "o1-mini",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Answer the question: {{question}}"}
                    ],
                }
            ],
        },
    )
    experiment.experiments_datasets.add(variant)
    output = Column.no_workspace_objects.create(
        name="Response Generation-Generate Answer-1-o1-mini",
        source=SourceChoices.EXPERIMENT.value,
        dataset=dataset,
        data_type="text",
        source_id=str(variant.id),
        status=StatusType.COMPLETED.value,
    )
    variant.columns.add(output)
    rows = Row.no_workspace_objects.filter(dataset=dataset).order_by("order")
    write_answers(dataset, output, rows)
    experiment.refresh_from_db()
    return experiment


# ---------------------------------------------------------------------------
# Writers: experiments made on this build, with this build's own services
# ---------------------------------------------------------------------------


def snapshot_experiment(dataset, user, prompt_version, name):
    """Created here, before its run is bound: snapshot identity, one variant.

    create_dataset_snapshot records the NativeExperimentSnapshot; the variant
    uses a prompt with a saved version.
    """
    experiment = ExperimentsTable.objects.create(
        dataset=dataset, name=name, user=user, experiment_type="llm"
    )
    snapshot, _, _ = create_dataset_snapshot(dataset, experiment)
    experiment.refresh_from_db()
    variant, output = add_variant(experiment, snapshot, "A", prompt_version)
    return experiment, snapshot, variant, output


def recorded_experiment(dataset, user, prompt_version, name):
    """Created here and fully recorded: snapshot identity, one variant, its run.

    bind_native_run records the variant's ExperimentRun and NativeRunBinding,
    as the create path does for a prompt with a saved version
    (services/experiment_submit.py _stage_native_prompt_runs).
    """
    experiment, snapshot, variant, output = snapshot_experiment(
        dataset, user, prompt_version, name
    )
    run = bind_native_run(
        dataset=dataset,
        experiment=experiment,
        variant=variant,
        execution_id=uuid.uuid4(),
        output_column=output,
        created_by=user,
        effective_settings={"trials": 1, "concurrency": 10},
    )
    return experiment, snapshot, variant, run


def add_variant(experiment, snapshot, label, prompt_version=None):
    """One variant as the classic create and update paths write it.

    views/experiments.py _diff_and_update_configs, "NEW prompt": a variant
    with the experiment FK, its ExperimentPromptConfig (prompt_version null for
    a prompt with no saved version, its messages inline) and its output column
    in the snapshot.
    """
    name = f"{label}-{MODEL}"
    variant = ExperimentDatasetTable.objects.create(
        name=name, experiment=experiment, status=StatusType.NOT_STARTED.value
    )
    ExperimentPromptConfig.objects.create(
        experiment_dataset=variant,
        prompt_template=prompt_version.original_template if prompt_version else None,
        prompt_version=prompt_version,
        name=name,
        model=MODEL,
        model_params={"temperature": 0.2},
        messages=None
        if prompt_version
        else [{"role": "user", "content": [{"type": "text", "text": "Answer"}]}],
    )
    output = Column.objects.create(
        dataset=snapshot,
        name=name,
        data_type="text",
        source=SourceChoices.EXPERIMENT.value,
        source_id=str(variant.id),
    )
    variant.columns.add(output)
    return variant, output


@pytest.fixture
def world(organization, workspace, user, prompt_version):
    older_only = source_dataset(organization, workspace, user, "Older only")
    new_only = source_dataset(organization, workspace, user, "New only")
    both = source_dataset(organization, workspace, user, "Both kinds")

    recorded, _, _, _ = recorded_experiment(new_only, user, prompt_version, "Recorded")
    recorded_b, _, _, _ = recorded_experiment(
        both, user, prompt_version, "Recorded too"
    )

    # Created here with a second variant whose prompt has no saved version:
    # the create path binds no run for it (experiment_submit.py:413-417).
    mixed, mixed_snapshot, _, _ = recorded_experiment(
        both, user, prompt_version, "Mixed"
    )
    add_variant(mixed, mixed_snapshot, "B")

    # Its only run is a quick test, which the detail does not count.
    quick, _, _, quick_run = recorded_experiment(
        both, user, prompt_version, "Quick test only"
    )
    ExperimentRun.no_workspace_objects.filter(pk=quick_run.pk).update(
        metadata={**quick_run.metadata, "kind": "quick_test"}
    )

    # Its only run was deleted.
    deleted, _, _, deleted_run = recorded_experiment(
        both, user, prompt_version, "Deleted run"
    )
    ExperimentRun.no_workspace_objects.filter(pk=deleted_run.pk).update(deleted=True)

    # Created here, its run not bound yet.
    waiting, _, _, _ = snapshot_experiment(
        both, user, prompt_version, "Snapshot, no runs yet"
    )

    return {
        "datasets": {"older_only": older_only, "new_only": new_only, "both": both},
        "older_snapshot": production_snapshot_experiment(
            older_only, user, prompt_version, "Older with snapshot"
        ),
        "older_without_snapshot": production_experiment_without_snapshot(
            older_only, user, "Older without snapshot"
        ),
        "sample": sample_experiment(older_only),
        "recorded": recorded,
        "recorded_b": recorded_b,
        "mixed": mixed,
        "quick_test_only": quick,
        "deleted_run_only": deleted,
        "snapshot_no_runs": waiting,
        "older_b": production_snapshot_experiment(
            both, user, prompt_version, "Older beside runs"
        ),
    }


def listed(client, dataset):
    response = client.get(LIST_URL, {"dataset_id": str(dataset.id), "limit": 50})
    assert response.status_code == 200, response.data
    return {row["id"]: row for row in response.data["results"]}


def detail_of(client, experiment):
    response = client.get(detail_url(experiment))
    assert response.status_code == 200, response.data
    return response.data["result"]


def rows_payload(client, experiment):
    response = client.get(f"/model-hub/experiments/v2/{experiment.id}/rows/")
    assert response.status_code == 200, response.data
    return json.dumps(response.data, default=str)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_list_and_detail_declare_the_same_read_only_boolean():
    for serializer in (ExperimentListV2Serializer, ExperimentDetailV2Serializer):
        field = serializer().fields["opens_in_full_results"]
        assert field.read_only, serializer.__name__
        documented = serializer.get_opens_in_full_results._swagger_serializer
        assert isinstance(documented, serializers.BooleanField), serializer.__name__


def test_the_documented_field_says_where_every_other_experiment_opens():
    for serializer in (ExperimentListV2Serializer, ExperimentDetailV2Serializer):
        documented = serializer.get_opens_in_full_results._swagger_serializer
        help_text = str(documented.help_text)
        for words in (
            "opens in the original results view, where all of its variants show",
            "created before runs were recorded",
            "calls an HTTP endpoint",
            "unversioned prompt",
            "classic rerun or append",
        ):
            assert words in help_text, (serializer.__name__, words)


def test_a_failed_marker_read_is_null_on_a_write_response_and_fails_a_read(
    world, monkeypatch
):
    def unreadable(_experiment):
        raise DatabaseError("the marker could not be read")

    monkeypatch.setattr(experiment_serializers, "opens_in_full_results", unreadable)
    experiment = ExperimentsTable.no_workspace_objects.get(pk=world["recorded"].pk)
    for method in ("POST", "PUT", "PATCH"):
        context = {"request": SimpleNamespace(method=method)}
        serializer = ExperimentDetailV2Serializer(experiment, context=context)
        assert serializer.get_opens_in_full_results(experiment) is None, method
    context = {"request": SimpleNamespace(method="GET")}
    serializer = ExperimentDetailV2Serializer(experiment, context=context)
    with pytest.raises(DatabaseError):
        serializer.get_opens_in_full_results(experiment)
    documented = ExperimentDetailV2Serializer.get_opens_in_full_results
    assert documented._swagger_serializer.allow_null


def test_a_dataset_with_only_production_experiments_lists_them_all_as_original(
    auth_client, world
):
    rows = listed(auth_client, world["datasets"]["older_only"])
    assert {key: row["opens_in_full_results"] for key, row in rows.items()} == {
        str(world["older_snapshot"].id): False,
        str(world["older_without_snapshot"].id): False,
        str(world["sample"].id): False,
    }


def test_a_dataset_with_only_fully_recorded_experiments(auth_client, world):
    rows = listed(auth_client, world["datasets"]["new_only"])
    assert {key: row["opens_in_full_results"] for key, row in rows.items()} == {
        str(world["recorded"].id): True,
    }


def test_a_dataset_with_both_marks_each_experiment(auth_client, world):
    rows = listed(auth_client, world["datasets"]["both"])
    assert {key: row["opens_in_full_results"] for key, row in rows.items()} == {
        str(world["recorded_b"].id): True,
        str(world["mixed"].id): False,
        str(world["quick_test_only"].id): False,
        str(world["deleted_run_only"].id): False,
        str(world["snapshot_no_runs"].id): False,
        str(world["older_b"].id): False,
    }


@pytest.mark.parametrize(
    "shape, full, reason",
    [
        ("older_snapshot", False, "native_identity_not_recorded"),
        ("older_without_snapshot", False, "native_identity_not_recorded"),
        ("sample", False, "native_identity_not_recorded"),
        ("recorded", True, None),
        # Runs exist, so results_availability keeps its reason (none); the
        # unversioned variant is not in the runs view, so it opens original.
        ("mixed", False, None),
        ("quick_test_only", False, "native_runs_not_recorded"),
        ("deleted_run_only", False, "native_runs_not_recorded"),
        ("snapshot_no_runs", False, "native_runs_not_recorded"),
    ],
)
def test_the_list_marker_and_the_detail_decision_agree(
    auth_client, world, shape, full, reason
):
    experiment = world[shape]
    row = listed(auth_client, experiment.dataset)[str(experiment.id)]
    detail = detail_of(auth_client, experiment)
    assert row["opens_in_full_results"] is detail["opens_in_full_results"] is full
    # The decision sits beside results_availability, whose reasons do not change.
    assert detail["results_availability"]["reason"] == reason


def test_a_rerun_of_a_production_experiment_stays_original(auth_client, world):
    """This build's rerun binds no run for an experiment without snapshot identity."""
    from model_hub.services.experiment_submit import _stage_native_prompt_runs

    older = world["older_snapshot"]
    before = (ExperimentRun.objects.count(), NativeRunBinding.objects.count())
    runs, pending = _stage_native_prompt_runs(older, str(uuid.uuid4()), {}, None)
    assert runs == {}
    assert pending == [
        "historical execution has no immutable snapshot identity; classic execution only"
    ]
    assert (ExperimentRun.objects.count(), NativeRunBinding.objects.count()) == before
    row = listed(auth_client, older.dataset)[str(older.id)]
    assert row["opens_in_full_results"] is False
    assert detail_of(auth_client, older)["opens_in_full_results"] is False


def test_a_rerun_that_adds_an_unversioned_variant_moves_the_experiment_and_keeps_the_variant(
    auth_client, organization, workspace, user, prompt_version
):
    dataset = source_dataset(organization, workspace, user, "Rerun with changes")
    experiment, snapshot, first, _ = recorded_experiment(
        dataset, user, prompt_version, "Recorded, then extended"
    )
    assert detail_of(auth_client, experiment)["opens_in_full_results"] is True

    added, added_output = add_variant(experiment, snapshot, "B")

    row = listed(auth_client, dataset)[str(experiment.id)]
    assert row["opens_in_full_results"] is False
    assert detail_of(auth_client, experiment)["opens_in_full_results"] is False
    # The original results view still reads both variants' columns.
    payload = rows_payload(auth_client, experiment)
    first_output = first.columns.get(source=SourceChoices.EXPERIMENT.value)
    assert str(first_output.id) in payload
    assert str(added_output.id) in payload
    assert str(added.id) in payload or added.name in payload


def test_the_sample_experiment_reads_its_rows_and_has_no_summary(auth_client, world):
    """Every organization's sample experiment has no snapshot dataset."""
    sample = world["sample"]
    assert sample.snapshot_dataset_id is None
    detail = detail_of(auth_client, sample)
    assert detail["opens_in_full_results"] is False
    assert detail["snapshot_dataset_id"] is None
    payload = rows_payload(auth_client, sample)
    assert "Answer 1" in payload and "Answer 2" in payload
    # The stats the Summary reads refuse an experiment with no snapshot, here
    # as on the production build; the page says so in words instead.
    stats = auth_client.get(f"/model-hub/experiments/v2/{sample.id}/stats/")
    assert stats.status_code == 400
    assert "no snapshot dataset" in json.dumps(stats.data)


def test_an_unannotated_instance_gets_the_same_answer(world):
    for shape, expected in (
        ("older_snapshot", False),
        ("recorded", True),
        ("mixed", False),
    ):
        plain = ExperimentsTable.no_workspace_objects.get(pk=world[shape].pk)
        data = ExperimentListV2Serializer(plain).data
        assert data["opens_in_full_results"] is expected, shape


def test_the_marker_adds_no_query_per_row(
    auth_client, organization, workspace, user, prompt_version
):
    dataset = source_dataset(organization, workspace, user, "Query count")
    production_snapshot_experiment(dataset, user, prompt_version, "First")
    params = {"dataset_id": str(dataset.id), "limit": 50}
    auth_client.get(LIST_URL, params)  # settle per-session reads first

    with CaptureQueriesContext(connection) as one_row:
        response = auth_client.get(LIST_URL, params)
    assert response.status_code == 200, response.data
    assert [row["opens_in_full_results"] for row in response.data["results"]] == [False]

    production_experiment_without_snapshot(dataset, user, "Second")
    recorded_experiment(dataset, user, prompt_version, "Third")
    mixed, mixed_snapshot, _, _ = recorded_experiment(
        dataset, user, prompt_version, "Fourth"
    )
    add_variant(mixed, mixed_snapshot, "B")
    sample_experiment(dataset)

    with CaptureQueriesContext(connection) as five_rows:
        response = auth_client.get(LIST_URL, params)
    assert response.status_code == 200, response.data
    assert sorted(row["opens_in_full_results"] for row in response.data["results"]) == [
        False,
        False,
        False,
        False,
        True,
    ]
    assert len(five_rows.captured_queries) == len(one_row.captured_queries), [
        query["sql"] for query in five_rows.captured_queries
    ]
