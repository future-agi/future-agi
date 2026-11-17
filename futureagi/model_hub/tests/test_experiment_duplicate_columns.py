"""Copied evaluator inputs cross the source/new-snapshot boundary by identity."""

# Imported pytest fixtures are injected by name.
# ruff: noqa: F811

import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from model_hub.models.choices import DataTypeChoices, SourceChoices
from model_hub.models.develop_dataset import Column, Dataset
from model_hub.serializers.experiments import ExperimentDetailV2Serializer
from model_hub.tests.test_experiment_version_params import version_world  # noqa: F401
from model_hub.views.eval_runner import NativeEvaluationRunner
from model_hub.views.experiments import _translate_eval_mapping

pytestmark = pytest.mark.django_db


def test_copied_evaluator_columns_reach_the_new_immutable_inputs(version_world):
    dataset = version_world[0]
    fixture = (
        Path(__file__).resolve().parents[3]
        / "frontend/src/sections/develop-detail/Experiment/__tests__"
        / "copiedEvaluatorMapping.json"
    )
    body = json.loads(fixture.read_text())
    snapshot = Dataset.no_workspace_objects.create(
        name="Snapshot", organization=dataset.organization, workspace=dataset.workspace
    )
    columns = []
    for index, (snapshot_id, source_id) in enumerate(body["source_column_ids"].items()):
        Column.no_workspace_objects.create(
            id=source_id,
            dataset=dataset,
            name=f"source-{index}",
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.OTHERS.value,
        )
        columns.append(
            Column.no_workspace_objects.create(
                id=snapshot_id,
                dataset=snapshot,
                name=f"saved-{index}",
                source_id=source_id,
                data_type=DataTypeChoices.TEXT.value,
                source=SourceChoices.OTHERS.value,
            )
        )
    instance = SimpleNamespace(
        dataset_id=dataset.id, snapshot_dataset_id=snapshot.id, column=columns[0]
    )
    serializer = ExperimentDetailV2Serializer()
    assert serializer.get_source_column_ids(instance) == body["source_column_ids"]
    assert serializer.get_source_column_id(instance) == str(columns[0].source_id)
    assert serializer.fields["source_column_id"].read_only is True
    assert serializer.fields["source_column_id"].allow_null is True
    assert serializer.fields["source_column_ids"].read_only is True

    # This is the exact mapping the native and HTTP frontend tests serialize.
    fresh = {
        uuid.UUID(column.source_id): SimpleNamespace(id=uuid.uuid4())
        for column in columns
    }
    translated = _translate_eval_mapping(body["source_mapping"], fresh)
    reference, question = [str(fresh[uuid.UUID(c.source_id)].id) for c in columns]
    assert translated == {
        "reference": reference,
        "expected": f"{reference}.answer",
        "input": [f"{question}.question", f"{reference}.answer"],
        "output": "output",
    }
    runner = NativeEvaluationRunner.__new__(NativeEvaluationRunner)
    runner._native_values = {
        reference: '{"answer":"saved answer"}',
        question: '{"question":"saved question"}',
    }
    runner._native_output = "new output"
    assert runner._prepare_mapping_data(None, translated) == (
        ["reference", "expected", "input", "output"],
        [
            '{"answer":"saved answer"}',
            "saved answer",
            ["saved question", "saved answer"],
            "new output",
        ],
    )


def test_reference_provenance_ignores_unavailable_and_unrelated_columns(version_world):
    dataset = version_world[0]
    source = Column.no_workspace_objects.get(id=dataset.column_order[0])
    snapshot = Dataset.no_workspace_objects.create(
        name="Snapshot", organization=dataset.organization, workspace=dataset.workspace
    )
    saved = Column.no_workspace_objects.create(
        dataset=snapshot,
        name="question",
        source_id=str(source.id),
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    instance = SimpleNamespace(
        dataset_id=dataset.id, snapshot_dataset_id=snapshot.id, column=saved
    )
    serializer = ExperimentDetailV2Serializer()
    assert serializer.get_source_column_id(instance) == str(source.id)
    instance.column = source
    assert serializer.get_source_column_id(instance) == str(source.id)
    instance.column = None
    assert serializer.get_source_column_id(instance) is None
    instance.column = saved

    foreign = Dataset.no_workspace_objects.create(
        name="Other source",
        organization=dataset.organization,
        workspace=dataset.workspace,
    )
    other = Column.no_workspace_objects.create(
        dataset=foreign,
        name=source.name,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    for unavailable in (str(other.id), "legacy-name", str(uuid.uuid4())):
        saved.source_id = unavailable
        saved.save(update_fields=["source_id"])
        assert serializer.get_source_column_ids(instance) == {}
        assert serializer.get_source_column_id(instance) is None
    saved.source_id = str(source.id)
    saved.save(update_fields=["source_id"])
    Column.no_workspace_objects.filter(id=source.id).update(deleted=True)
    assert serializer.get_source_column_ids(instance) == {}
    assert serializer.get_source_column_id(instance) is None
    source.refresh_from_db()
    instance.column = source
    assert serializer.get_source_column_id(instance) is None
    instance.snapshot_dataset_id = None
    assert serializer.get_source_column_ids(instance) == {}
