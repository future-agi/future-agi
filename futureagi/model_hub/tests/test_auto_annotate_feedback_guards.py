"""API and column-allocation regressions that isolate feedback scope guards."""

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.exceptions import ObjectDoesNotExist

from model_hub.models.develop_annotations import Annotations, AnnotationsLabels
from model_hub.models.develop_dataset import Cell, Column
from model_hub.tests import test_auto_annotate_tenant_scope as scope_tests
from model_hub.tests.test_auto_annotate_tenant_scope import (
    _annotation,
    _output_column,
)
from model_hub.utils import auto_annotate
from model_hub.views.develop_annotations import AnnotationsViewSet
from tfc.middleware.workspace_context import clear_workspace_context

pytestmark = pytest.mark.django_db
tenants = scope_tests.tenants


def _view(tenant):
    view = AnnotationsViewSet()
    view.request = SimpleNamespace(
        user=tenant.user,
        organization=tenant.organization,
        workspace=tenant.workspace,
        query_params={},
    )
    return view


def _update(tenant, column):
    return {
        "row_id": str(tenant.row.id),
        "column_id": str(column.id),
        "label_id": str(tenant.label.id),
        "value": ["Yes"],
    }


@pytest.mark.parametrize(
    "mismatch",
    ["wrong_output", "unattached_label", "attached_wrong_scope", "attached_wrong_org"],
)
def test_feedback_rejects_independent_label_and_output_mismatches(tenants, mismatch):
    own, foreign, _ = tenants
    annotation, output = _annotation(own)
    annotation.assigned_users.add(own.user)
    update = _update(own, output)
    if mismatch == "wrong_output":
        # The column belongs to the correct dataset but to another annotation/label.
        wrong = _output_column(own.dataset, "other-annotation-source")
        update["column_id"] = str(wrong.id)
    elif mismatch == "unattached_label":
        annotation.labels.clear()
    elif mismatch == "attached_wrong_org":
        # Workspace middleware alone cannot catch an inconsistent organization FK.
        AnnotationsLabels.objects.filter(pk=own.label.pk).update(
            organization=foreign.organization
        )
    else:
        annotation.labels.add(foreign.label)
        update["label_id"] = str(foreign.label.id)
        Column.objects.filter(pk=output.pk).update(
            source_id=f"{annotation.id}-sourceid-{foreign.label.id}"
        )
    with (
        patch.object(
            auto_annotate.generate_annotations_task, "apply_async"
        ) as dispatch,
        patch("model_hub.views.develop_annotations.EmbeddingManager") as embedding,
    ):
        response = own.client.post(
            f"/model-hub/annotations/{annotation.id}/update_cells/",
            {"label_values": [update]},
            format="json",
        )
    assert response.status_code == 404, response.content
    dispatch.assert_not_called()
    embedding.assert_not_called()
    clear_workspace_context()
    assert not Cell.objects.exists()


@pytest.mark.parametrize("endpoint", ["feedback", "update"])
def test_api_rejects_annotation_with_foreign_stored_dataset(tenants, endpoint):
    own, foreign, _ = tenants
    annotation, _ = _annotation(own)
    annotation.assigned_users.add(own.user)
    Annotations.objects.filter(pk=annotation.pk).update(dataset=foreign.dataset)
    if endpoint == "feedback":
        column = _output_column(
            foreign.dataset, f"{annotation.id}-sourceid-{own.label.id}"
        )
        update = dict(_update(own, column), row_id=str(foreign.row.id))
        with (
            patch.object(auto_annotate.generate_annotations_task, "apply_async"),
            patch("model_hub.views.develop_annotations.EmbeddingManager"),
        ):
            response = own.client.post(
                f"/model-hub/annotations/{annotation.id}/update_cells/",
                {"label_values": [update]},
                format="json",
            )
    else:
        response = own.client.patch(
            f"/model-hub/annotations/{annotation.id}/",
            {"name": "Should not persist"},
            format="json",
        )
    assert response.status_code == 404, response.content
    clear_workspace_context()
    annotation.refresh_from_db()
    assert annotation.name == "Colliding annotation name"
    assert not Cell.objects.exists()


def test_update_rejects_sibling_label_before_saving(tenants):
    own, _, sibling = tenants
    annotation, output = _annotation(own)
    response = own.client.patch(
        f"/model-hub/annotations/{annotation.id}/",
        {
            "name": "Should not persist",
            "labels": [{"id": str(sibling.label.id), "required": False}],
        },
        format="json",
    )
    assert response.status_code == 404, response.content
    clear_workspace_context()
    annotation.refresh_from_db()
    assert annotation.name == "Colliding annotation name"
    assert list(annotation.labels.values_list("id", flat=True)) == [own.label.id]
    output.refresh_from_db()
    assert not output.deleted


@pytest.mark.parametrize("kind", ["label_values", "response_field_values"])
def test_feedback_validates_entire_batch_before_any_write(tenants, kind):
    own, foreign, _ = tenants
    annotation, output = _annotation(own)
    annotation.assigned_users.add(own.user)
    good = _update(own, output)
    bad = dict(
        good, column_id=str(_output_column(foreign.dataset, output.source_id).id)
    )
    if kind == "response_field_values":
        good.pop("label_id")
        bad.pop("label_id")
    with (
        patch.object(
            auto_annotate.generate_annotations_task, "apply_async"
        ) as dispatch,
        patch("model_hub.views.develop_annotations.EmbeddingManager"),
    ):
        response = own.client.post(
            f"/model-hub/annotations/{annotation.id}/update_cells/",
            {kind: [good, bad]},
            format="json",
        )
    assert response.status_code == 404, response.content
    clear_workspace_context()
    assert not Cell.objects.exists()
    dispatch.assert_not_called()


def test_feedback_ignores_review_in_foreign_cell_dataset(tenants):
    own, foreign, _ = tenants
    annotation, output = _annotation(own)
    annotation.assigned_users.add(own.user)
    sentinel = Cell.objects.create(
        dataset=foreign.dataset,
        row=own.row,
        column=output,
        value="Foreign review",
        feedback_info={"annotation": {"user_id": str(foreign.user.id)}},
    )
    # If the existing-cell dataset filter is dropped, allocation is erroneously
    # entered and sees a full one-response slot already owned by another user.
    Cell.objects.create(
        dataset=own.dataset,
        row=own.row,
        column=output,
        value="Pending",
        feedback_info={"annotation": {"verified": True}},
    )
    with (
        patch.object(auto_annotate.generate_annotations_task, "apply_async"),
        patch("model_hub.views.develop_annotations.EmbeddingManager"),
        patch.object(
            AnnotationsViewSet, "can_user_annotate", wraps=_view(own).can_user_annotate
        ) as allocate,
    ):
        response = own.client.post(
            f"/model-hub/annotations/{annotation.id}/update_cells/",
            {"label_values": [_update(own, output)]},
            format="json",
        )
    assert response.status_code == 200, response.content
    allocate.assert_not_called()
    clear_workspace_context()
    sentinel.refresh_from_db()
    assert sentinel.value == "Foreign review"


@pytest.mark.parametrize("source", ["stored_cell", "allocation"])
def test_feedback_revalidates_column_returned_by_allocation(tenants, source):
    own, foreign, _ = tenants
    annotation, output = _annotation(own)
    annotation.assigned_users.add(own.user)
    annotation.responses = 2
    annotation.save()
    Cell.objects.create(
        dataset=own.dataset,
        row=own.row,
        column=output,
        value="Other reviewer",
        feedback_info={"annotation": {"user_id": str(foreign.user.id)}},
    )
    foreign_column = _output_column(foreign.dataset, output.source_id)
    sentinel = Cell.objects.create(
        dataset=own.dataset,
        row=own.row,
        column=foreign_column,
        value="Untouched",
        feedback_info={"annotation": {"user_id": str(own.user.id)}},
    )
    # "stored_cell": allocation itself must ignore the stored cell whose column
    # is in another dataset, so no slot is free. "allocation": even if
    # allocation returned that column, update_cells must reject it.
    allocation = (
        patch.object(
            AnnotationsViewSet,
            "can_user_annotate",
            return_value=(True, str(foreign_column.id)),
        )
        if source == "allocation"
        else nullcontext()
    )
    with (
        patch.object(
            auto_annotate.generate_annotations_task, "apply_async"
        ) as dispatch,
        patch("model_hub.views.develop_annotations.EmbeddingManager"),
        allocation,
    ):
        response = own.client.post(
            f"/model-hub/annotations/{annotation.id}/update_cells/",
            {"label_values": [_update(own, output)]},
            format="json",
        )
    expected = 404 if source == "allocation" else 403
    assert response.status_code == expected, response.content
    clear_workspace_context()
    sentinel.refresh_from_db()
    assert sentinel.value == "Untouched"
    dispatch.assert_not_called()


@pytest.mark.parametrize("mismatch", ["column_dataset", "column_deleted"])
def test_feedback_rag_excludes_inconsistent_input_columns(tenants, mismatch):
    own, foreign, _ = tenants
    annotation, output = _annotation(own)
    annotation.assigned_users.add(own.user)
    bad = _output_column(
        foreign.dataset if mismatch == "column_dataset" else own.dataset, "input"
    )
    Cell.objects.create(
        dataset=own.dataset, row=own.row, column=bad, value="Must not reach embeddings"
    )
    if mismatch == "column_deleted":
        bad.delete()
    with (
        patch.object(auto_annotate.generate_annotations_task, "apply_async"),
        patch("model_hub.views.develop_annotations.EmbeddingManager") as embeddings,
    ):
        response = own.client.post(
            f"/model-hub/annotations/{annotation.id}/update_cells/",
            {"label_values": [_update(own, output)]},
            format="json",
        )
    assert response.status_code == 200, response.content
    formatter = embeddings.return_value.data_formatter
    formatter.assert_called_once()
    row_dict = formatter.call_args.kwargs["row_dict"]
    assert str(bad.id) not in row_dict
    assert str(output.id) in row_dict


@pytest.mark.parametrize("mismatch", ["row", "label"])
def test_column_allocation_rejects_wrong_row_or_unattached_label(tenants, mismatch):
    own, foreign, _ = tenants
    annotation, _ = _annotation(own)
    annotation.assigned_users.add(own.user)
    row = own.row
    if mismatch == "row":
        row = foreign.row
    else:
        annotation.labels.clear()
    with pytest.raises(ObjectDoesNotExist):
        _view(own).can_user_annotate(annotation.id, row.id, own.label.id, own.user.id)


@pytest.mark.parametrize("filled", [False, True])
def test_column_allocation_never_returns_foreign_dataset_column(tenants, filled):
    own, foreign, _ = tenants
    annotation, output = _annotation(own)
    annotation.assigned_users.add(own.user)
    # Chronological order is significant to both allocation branches.
    if not filled:
        output.delete()
        foreign_column = _output_column(foreign.dataset, output.source_id)
        expected = _output_column(own.dataset, output.source_id)
    else:
        annotation.responses = 2
        annotation.save()
        Cell.objects.create(
            dataset=own.dataset,
            row=own.row,
            column=output,
            value="First response",
            feedback_info={"annotation": {"user_id": str(foreign.user.id)}},
        )
        foreign_column = _output_column(foreign.dataset, output.source_id)
        expected = _output_column(own.dataset, output.source_id)
    allowed, column_id = _view(own).can_user_annotate(
        annotation.id, own.row.id, own.label.id, own.user.id
    )
    assert allowed
    assert column_id == expected.id
    assert column_id != foreign_column.id


@pytest.mark.parametrize("owner", ["self", "unassigned"])
def test_full_allocation_ignores_cells_whose_column_is_in_another_dataset(
    tenants, owner
):
    own, foreign, _ = tenants
    annotation, output = _annotation(own)
    annotation.assigned_users.add(own.user)
    # A stored cell in this dataset whose column belongs to another dataset
    # with the same source_id. Cell foreign keys alone do not keep the two
    # datasets equal, so allocation must check the column's dataset too.
    other_column = _output_column(foreign.dataset, output.source_id)
    feedback = {"annotation": {"user_id": str(own.user.id)}} if owner == "self" else {}
    Cell.objects.create(
        dataset=own.dataset,
        row=own.row,
        column=other_column,
        value="Value from another dataset",
        feedback_info=feedback,
    )
    allowed, column_id = _view(own).can_user_annotate(
        annotation.id, own.row.id, own.label.id, own.user.id
    )
    assert str(column_id) != str(other_column.id)
    assert allowed
    assert str(column_id) == str(output.id)


def test_annotate_row_only_reads_columns_of_the_annotation_dataset(tenants):
    own, foreign, _ = tenants
    annotation, output = _annotation(own)
    annotation.assigned_users.add(own.user)
    other_column = _output_column(foreign.dataset, output.source_id)
    Cell.objects.create(
        dataset=own.dataset,
        row=own.row,
        column=other_column,
        value="Value from another dataset",
        feedback_info={},
    )
    # Even if allocation ever returned another dataset's column, annotate_row
    # must not read it.
    with patch.object(
        AnnotationsViewSet,
        "can_user_annotate",
        return_value=(True, str(other_column.id)),
    ):
        response = own.client.get(
            f"/model-hub/annotations/{annotation.id}/annotate_row/"
            f"?row_order={own.row.order}"
        )
    assert "Value from another dataset" not in response.content.decode()


@pytest.mark.parametrize("record", ["annotation", "label"])
@pytest.mark.parametrize("field", ["organization", "workspace"])
def test_column_allocation_checks_request_scope_without_middleware(
    tenants, record, field
):
    own, foreign, sibling = tenants
    annotation, _ = _annotation(own)
    annotation.assigned_users.add(own.user)
    target = annotation if record == "annotation" else own.label
    value = foreign.organization if field == "organization" else sibling.workspace
    type(target).no_workspace_objects.filter(pk=target.pk).update(**{field: value})
    clear_workspace_context()
    with pytest.raises(ObjectDoesNotExist):
        _view(own).can_user_annotate(
            annotation.id, own.row.id, own.label.id, own.user.id
        )
    assert not Cell.objects.exists()


def test_feedback_revalidates_response_column_after_label_dispatch(tenants):
    own, foreign, _ = tenants
    annotation, output = _annotation(own)
    annotation.assigned_users.add(own.user)
    response_column = _output_column(own.dataset, "response-field")
    sentinel = Cell.objects.create(
        dataset=own.dataset,
        row=own.row,
        column=response_column,
        value="Untouched",
    )

    def move_response_column(*args, **kwargs):
        # Both batch references passed prevalidation. A stored relationship then
        # changes at the external Temporal boundary, before the response write.
        Column.objects.filter(pk=response_column.pk).update(dataset=foreign.dataset)

    with (
        patch.object(
            auto_annotate.generate_annotations_task,
            "apply_async",
            side_effect=move_response_column,
        ) as dispatch,
        patch("model_hub.views.develop_annotations.EmbeddingManager"),
    ):
        response = own.client.post(
            f"/model-hub/annotations/{annotation.id}/update_cells/",
            {
                "label_values": [_update(own, output)],
                "response_field_values": [
                    {
                        "row_id": str(own.row.id),
                        "column_id": str(response_column.id),
                        "value": "Must not be written",
                    }
                ],
            },
            format="json",
        )
    dispatch.assert_called_once()
    assert response.status_code == 404, response.content
    clear_workspace_context()
    sentinel.refresh_from_db()
    assert sentinel.value == "Untouched"


def test_create_rejects_input_column_from_foreign_dataset(tenants):
    from model_hub.tests.test_auto_annotate_tenant_scope import _payload

    own, foreign, _ = tenants
    own.label.settings["inputs"] = [str(_output_column(foreign.dataset, "input").id)]
    own.label.save()
    with patch.object(
        auto_annotate.generate_annotations_task, "apply_async"
    ) as dispatch:
        response = own.client.post(
            "/model-hub/annotations/", _payload(own), format="json"
        )
    assert response.status_code == 400, response.content
    dispatch.assert_not_called()
    clear_workspace_context()
    assert not Annotations.objects.exists()
