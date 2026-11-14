"""Database and dispatch regressions for categorical annotation inputs."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from agentic_eval.core.llm.llm import LLM
from model_hub.models.choices import (
    DatasetSourceChoices,
    DataTypeChoices,
    SourceChoices,
)
from model_hub.models.develop_annotations import Annotations
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.tests.test_auto_annotate_tenant_scope import _payload, _tenant
from model_hub.utils.auto_annotate import _get_input_values, generate_annotations_task
from tfc.middleware.workspace_context import clear_workspace_context


@pytest.mark.django_db
def test_get_values_resolves_cell_by_column(organization, workspace):
    dataset = Dataset.objects.create(
        name=f"auto-annotate dataset {uuid.uuid4().hex[:8]}",
        source=DatasetSourceChoices.BUILD.value,
        organization=organization,
        workspace=workspace,
    )
    row = Row.objects.create(dataset=dataset, order=1)
    column = Column.objects.create(
        name="input_col",
        data_type=DataTypeChoices.TEXT.value,
        dataset=dataset,
        source=SourceChoices.OTHERS.value,
    )
    Cell.objects.create(dataset=dataset, row=row, column=column, value="hello world")
    result = _get_input_values([str(column.id)], dataset.id, row.id)

    assert result == ["hello world"]


@pytest.mark.django_db
def test_categorical_dispatch_reads_inputs_and_persists_model_result():
    """Keep the ORM, task serialization and three-stage agent real.

    Only Temporal transport and external model/billing boundaries are replaced.
    Unlike an empty-input worker test, this exercises the configured column IDs
    all the way from annotation creation to the model prompt and stored result.
    """
    own = _tenant("input-workflow")
    try:
        first = Column.objects.create(
            name="Question",
            dataset=own.dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.OTHERS.value,
        )
        second = Column.objects.create(
            name="Context",
            dataset=own.dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.OTHERS.value,
        )
        for column, value in (
            (first, "Synthetic question"),
            (second, "Useful context"),
        ):
            Cell.objects.create(
                dataset=own.dataset, row=own.row, column=column, value=value
            )
        own.label.settings["inputs"] = [str(second.id), str(first.id)]
        own.label.save()
        temporal_client = SimpleNamespace(namespace="test", start_workflow=AsyncMock())
        with patch(
            "tfc.temporal.common.client.get_client",
            AsyncMock(return_value=temporal_client),
        ):
            response = own.client.post(
                "/model-hub/annotations/", _payload(own), format="json"
            )
        assert response.status_code == 200, response.content
        temporal_client.start_workflow.assert_awaited_once()
        wire_input = temporal_client.start_workflow.call_args.args[1]
        assert wire_input.args[2] == str(own.organization.id)
        clear_workspace_context()

        with (
            patch.object(LLM, "_init_client"),
            patch.object(
                LLM,
                "_get_completion_content",
                side_effect=[
                    "Read the supplied question and context.",
                    "The matching category is Yes.",
                    '{"choices": ["Yes"], "explanation": "The context is useful."}',
                ],
            ) as completion,
            patch(
                "ee.usage.services.metering.check_usage",
                return_value=SimpleNamespace(allowed=True),
            ),
            patch(
                "model_hub.utils.auto_annotate.count_tiktoken_tokens", return_value=1
            ),
            patch("model_hub.utils.auto_annotate.log_and_deduct_cost_for_api_request"),
            patch("ee.usage.services.emitter.emit"),
        ):
            generate_annotations_task._original_func(
                *wire_input.args, **wire_input.kwargs
            )

        assert completion.call_count == 3
        content = completion.call_args_list[0].kwargs["messages"][0]["content"]
        text = "\n".join(block.get("text", "") for block in content)
        assert text.index("Useful context") < text.index("Synthetic question")
        annotation = Annotations.objects.get(id=wire_input.args[1])
        output_column = annotation.columns.get()
        output = Cell.objects.get(
            dataset=own.dataset, row=own.row, column=output_column
        )
        assert output.status == "pass"
        assert output.value == str(["Yes"])
        assert output.feedback_info["annotation"] == {
            "auto_annotate": True,
            "label_id": str(own.label.id),
            "annotation_id": str(annotation.id),
            "verified": False,
            "explanation": "The context is useful.",
        }
        assert output_column.status == "Completed"
    finally:
        clear_workspace_context()
