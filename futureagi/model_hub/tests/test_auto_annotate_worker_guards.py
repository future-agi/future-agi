"""Discriminating stored-FK fixtures for each auto-annotation worker boundary."""

from unittest.mock import Mock, patch

import pytest
from django.core.exceptions import ObjectDoesNotExist
from django.db import connection

from accounts.models.workspace import Workspace
from model_hub.models.develop_annotations import Annotations, AnnotationsLabels
from model_hub.models.develop_dataset import Cell, Column, Dataset
from model_hub.tests import test_auto_annotate_tenant_scope as scope_tests
from model_hub.tests.test_auto_annotate_tenant_scope import (
    _annotation,
    _output_column,
)
from model_hub.utils import auto_annotate

pytestmark = pytest.mark.django_db
tenants = scope_tests.tenants
model_boundaries = scope_tests.model_boundaries


def _run(tenant, annotation, **kwargs):
    auto_annotate.generate_annotations_task._original_func(
        tenant.label.id, annotation.id, tenant.organization.id, **kwargs
    )


@pytest.mark.parametrize(
    "mismatch",
    [
        "annotation_org",
        "dataset_org",
        "dataset_workspace",
        "label_org",
        "label_workspace",
        "unattached_label",
        "foreign_project",
        "deleted_project",
        "default_dataset_foreign_workspace",
        "default_label_foreign_workspace",
        "default_dataset_nondefault_workspace",
        "default_label_nondefault_workspace",
    ],
)
def test_worker_validates_each_scope_relationship(tenants, mismatch):
    own, foreign, sibling = tenants
    annotation, _ = _annotation(own)
    # Change exactly one stored relationship; all other filters must match.
    if mismatch == "annotation_org":
        Annotations.objects.filter(pk=annotation.pk).update(
            organization=foreign.organization
        )
    elif mismatch == "dataset_org":
        Dataset.objects.filter(pk=own.dataset.pk).update(
            organization=foreign.organization
        )
    elif mismatch == "dataset_workspace":
        Dataset.objects.filter(pk=own.dataset.pk).update(workspace=sibling.workspace)
    elif mismatch == "label_org":
        AnnotationsLabels.objects.filter(pk=own.label.pk).update(
            organization=foreign.organization
        )
    elif mismatch == "label_workspace":
        AnnotationsLabels.objects.filter(pk=own.label.pk).update(
            workspace=sibling.workspace
        )
    elif mismatch == "unattached_label":
        annotation.labels.clear()
    elif mismatch == "foreign_project":
        AnnotationsLabels.objects.filter(pk=own.label.pk).update(
            project=foreign.project
        )
    elif mismatch == "deleted_project":
        own.project.delete()
    else:
        Annotations.objects.filter(pk=annotation.pk).update(workspace=None)
        Dataset.objects.filter(pk=own.dataset.pk).update(workspace=None)
        AnnotationsLabels.objects.filter(pk=own.label.pk).update(workspace=None)
        default = Workspace.objects.create(
            organization=foreign.organization,
            is_default=True,
            name="Default",
            created_by=foreign.user,
        )
        target = own.dataset if mismatch.startswith("default_dataset") else own.label
        type(target).objects.filter(pk=target.pk).update(
            workspace=own.workspace if "nondefault" in mismatch else default
        )
    with patch.object(auto_annotate.AutoAnnotation, "generate_annotations") as generate:
        with pytest.raises((ObjectDoesNotExist, ValueError)):
            _run(own, annotation)
    generate.assert_not_called()
    assert not Cell.objects.exists()


def test_worker_requires_org_explicitly(tenants):
    own = tenants[0]
    annotation, _ = _annotation(own)
    with pytest.raises(ValueError, match="organization ID is required"):
        auto_annotate.generate_annotations_task._original_func(
            own.label.id, annotation.id
        )


def test_worker_allows_label_without_project(tenants, model_boundaries):
    own = tenants[0]
    annotation, column = _annotation(own)
    AnnotationsLabels.objects.filter(pk=own.label.pk).update(project=None)
    _run(own, annotation)
    assert Cell.objects.get(column=column).value == "Yes"


@pytest.mark.parametrize(
    ("helper", "mismatch"),
    [
        (helper, mismatch)
        for helper in ("dynamic", "input")
        for mismatch in ("cell_dataset", "column_dataset", "column_deleted")
    ]
    + [("input", "row_dataset"), ("input", "row_deleted")],
)
def test_input_helpers_reject_each_inconsistent_reference(tenants, helper, mismatch):
    own, foreign, _ = tenants
    column = _output_column(
        foreign.dataset if mismatch == "column_dataset" else own.dataset, "input"
    )
    row = foreign.row if mismatch == "row_dataset" else own.row
    Cell.objects.create(
        dataset=foreign.dataset if mismatch == "cell_dataset" else own.dataset,
        row=row,
        column=column,
        value="Must not be read",
    )
    if mismatch == "column_deleted":
        column.delete()
    if mismatch == "row_deleted":
        row.delete()
    if helper == "dynamic":
        with pytest.raises(ValueError):
            auto_annotate.replace_dynamic_ids(f"{{{{{column.id}}}}}", row)
    else:
        with pytest.raises(Cell.DoesNotExist):
            auto_annotate._get_input_values([str(column.id)], own.dataset.id, row.id)


@pytest.mark.parametrize(
    "mismatch",
    ["cell_dataset", "row_dataset", "column_dataset", "row_deleted", "column_deleted"],
)
def test_feedback_dataframe_validates_each_foreign_key(tenants, mismatch):
    own, foreign, _ = tenants
    column = _output_column(
        foreign.dataset if mismatch == "column_dataset" else own.dataset, "feedback"
    )
    row = foreign.row if mismatch == "row_dataset" else own.row
    Cell.objects.create(
        dataset=foreign.dataset if mismatch == "cell_dataset" else own.dataset,
        row=row,
        column=column,
        value="Must not be read",
        feedback_info={"update": True},
    )
    if mismatch == "column_deleted":
        column.delete()
    if mismatch == "row_deleted":
        row.delete()
    assert auto_annotate.dataset_to_dataframe(own.dataset.id, "feedback").empty


def test_feedback_dataframe_ignores_foreign_dataset_base_cell(tenants):
    own, foreign, _ = tenants
    output = _output_column(own.dataset, "feedback")
    base = _output_column(own.dataset, "input")
    Column.objects.filter(pk=base.pk).update(source="input")
    Cell.objects.create(
        dataset=own.dataset,
        row=own.row,
        column=output,
        value="Yes",
        feedback_info={"update": True},
    )
    Cell.objects.create(
        dataset=foreign.dataset, row=own.row, column=base, value="Foreign base cell"
    )
    frame = auto_annotate.dataset_to_dataframe(own.dataset.id, "feedback")
    assert frame["label_value"].tolist() == ["Yes"]
    assert frame[base.id].isna().all()


@pytest.mark.parametrize("mismatch", ["column_dataset", "column_deleted"])
def test_feedback_dataframe_revalidates_columns_after_projection(tenants, mismatch):
    own, foreign, _ = tenants
    output = _output_column(own.dataset, "feedback")
    base = _output_column(own.dataset, "input")
    Column.objects.filter(pk=base.pk).update(source="input")
    Cell.objects.create(
        dataset=own.dataset,
        row=own.row,
        column=output,
        value="Yes",
        feedback_info={"update": True},
    )
    Cell.objects.create(
        dataset=own.dataset, row=own.row, column=base, value="Must not be read"
    )
    cell_reads = 0

    def change_column_before_base_read(execute, sql, params, many, context):
        nonlocal cell_reads
        if sql.startswith("SELECT") and 'FROM "model_hub_cell"' in sql:
            cell_reads += 1
            if cell_reads == 2:
                # The column-ID projection has already been materialized. Execute
                # a real stored-state change before the later base-cell SELECT;
                # never replace an ORM call or fabricate query results.
                changes = (
                    {"dataset": foreign.dataset}
                    if mismatch == "column_dataset"
                    else {"deleted": True}
                )
                Column.objects.filter(pk=base.pk).update(**changes)
        return execute(sql, params, many, context)

    with connection.execute_wrapper(change_column_before_base_read):
        frame = auto_annotate.dataset_to_dataframe(own.dataset.id, "feedback")
    assert cell_reads == 2
    assert frame["label_value"].tolist() == ["Yes"]
    assert frame[base.id].isna().all()


@pytest.mark.parametrize("failure", [False, True])
def test_column_statuses_never_cross_dataset_boundary(
    tenants, model_boundaries, failure
):
    own, foreign, sibling = tenants
    annotation, output = _annotation(own)
    same_workspace_dataset = Dataset.objects.create(
        name="Other dataset", organization=own.organization, workspace=own.workspace
    )
    sentinels = [
        _output_column(dataset, output.source_id)
        for dataset in (foreign.dataset, sibling.dataset, same_workspace_dataset)
    ]
    Column.objects.filter(pk__in=[c.pk for c in sentinels]).update(status="sentinel")

    def assert_statuses(_payload):
        assert (
            list(
                Column.objects.filter(pk__in=[c.pk for c in sentinels]).values_list(
                    "status", flat=True
                )
            )
            == ["sentinel"] * 3
        )
        if failure:
            raise RuntimeError("Model unavailable")
        return {"choices": "Yes", "explanation": "Test response"}

    model_boundaries[0].evaluate.side_effect = assert_statuses
    _run(own, annotation)
    # A failed assertion inside the model would be caught by the worker; also
    # inspect the call-time columns outside that exception handler.
    assert model_boundaries[0].evaluate.call_count == 1
    assert (
        list(
            Column.objects.filter(pk__in=[c.pk for c in sentinels]).values_list(
                "status", flat=True
            )
        )
        == ["sentinel"] * 3
    )
    output.refresh_from_db()
    assert output.status == ("Running" if failure else "Completed")
    assert Cell.objects.get(column=output, row=own.row).status == (
        "error" if failure else "pass"
    )
    assert not Cell.objects.filter(column__in=sentinels).exists()


@pytest.mark.parametrize(
    "mismatch", ["cell_dataset", "column_dataset", "column_deleted"]
)
def test_reset_does_not_touch_out_of_scope_cells(tenants, model_boundaries, mismatch):
    own, foreign, _ = tenants
    annotation, output = _annotation(own)
    sentinel_column = output
    if mismatch != "cell_dataset":
        sentinel_column = _output_column(
            foreign.dataset if mismatch == "column_dataset" else own.dataset,
            output.source_id,
        )
    sentinel = Cell.objects.create(
        dataset=foreign.dataset if mismatch == "cell_dataset" else own.dataset,
        row=own.row,
        column=sentinel_column,
        value="Untouched",
        status="pass",
        feedback_info={"annotation": {"verified": False}},
    )
    if mismatch == "column_deleted":
        sentinel_column.delete()
    _run(own, annotation)
    sentinel.refresh_from_db()
    assert sentinel.value == "Untouched"
    assert sentinel.status == "pass"
    assert (
        Cell.objects.get(dataset=own.dataset, row=own.row, column=output).value == "Yes"
    )


@pytest.mark.parametrize(
    "mismatch", ["cell_dataset", "column_dataset", "column_deleted"]
)
def test_rag_revalidates_inputs_changed_since_payload_read(
    tenants, model_boundaries, mismatch
):
    own, foreign, _ = tenants
    annotation, output = _annotation(own)
    column = _output_column(own.dataset, "input")
    cell = Cell.objects.create(
        dataset=own.dataset, row=own.row, column=column, value="Input"
    )
    own.label.settings.update(inputs=[str(column.id)], strategy=auto_annotate.RAG_TXT)
    own.label.save()

    def change_stored_relationship(_inputs):
        # Simulate a concurrent edit between the first payload read and the RAG
        # read, at the external embedding boundary, without mocking any ORM call.
        if mismatch == "cell_dataset":
            Cell.objects.filter(pk=cell.pk).update(dataset=foreign.dataset)
        elif mismatch == "column_dataset":
            Column.objects.filter(pk=column.pk).update(dataset=foreign.dataset)
        else:
            column.delete()
        return ["text"]

    embeddings = Mock()
    embeddings.inputs_type_list.side_effect = change_stored_relationship
    embeddings.process_examples.return_value = []
    with (
        patch.object(auto_annotate, "EmbeddingManager", return_value=embeddings),
        patch.object(auto_annotate, "log_and_deduct_cost_for_api_request"),
        patch.object(auto_annotate, "count_tiktoken_tokens", return_value=1),
    ):
        _run(own, annotation)
    model_boundaries[0].evaluate.assert_not_called()
    embeddings.retrieve_avg_rag_based_examples.assert_not_called()
    assert Cell.objects.get(column=output, row=own.row).status == "error"


def test_run_model_rejects_foreign_row_when_called_directly(tenants):
    own, foreign, _ = tenants
    annotation, _ = _annotation(own)
    agent = Mock()
    with pytest.raises(ValueError, match="Row does not belong"):
        auto_annotate.AutoAnnotation(own.label, annotation).run_model(
            agent, {}, foreign.row
        )
    agent.evaluate.assert_not_called()
    assert not Cell.objects.exists()
