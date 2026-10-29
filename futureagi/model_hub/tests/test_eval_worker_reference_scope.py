"""Evaluation workers resolve references through their authorized dataset."""

from unittest.mock import AsyncMock, patch

import pytest
from asgiref.sync import async_to_sync
from django.core.exceptions import ValidationError

from model_hub.models.choices import AnnotationTypeChoices, SourceChoices
from model_hub.models.develop_dataset import Cell, Column
from model_hub.models.run_prompt import RunPrompter
from model_hub.tests.test_annotation_grid_scope import _annotation
from model_hub.tests.test_dataset_list_tenancy import tenants as tenants_fixture
from model_hub.tests.test_knowledge_base_request_scope import kb_for
from model_hub.tests.test_model_hub_related_scope import _request
from model_hub.views.develop_annotations import AnnotationsViewSet
from model_hub.views.eval_runner import (
    EvaluationRunner,
    _resolve_column_reference,
    process_mapping,
)
from model_hub.views.prompt_template import (
    replace_ids_with_column_name,
    replace_ids_with_column_name_async,
)
from tfc.middleware.workspace_context import clear_workspace_context

pytestmark = pytest.mark.django_db
tenants = tenants_fixture


@pytest.mark.parametrize("scope", ["own", "organization", "workspace", "unknown", "deleted"])
@pytest.mark.parametrize("as_list", [False, True])
def test_mapping_knowledge_base_ids_require_dataset_scope(tenants, scope, as_list):
    a, b, _ = tenants
    kb_id = str(kb_for(a, b, scope))
    clear_workspace_context()
    keys, values = process_mapping({"context": [kb_id] if as_list else kb_id}, a["rows"][0])
    if scope == "own":
        assert keys == ["context"]
        assert values == ([[kb_id]] if as_list else [kb_id])
    else:
        assert kb_id not in repr(values)


@pytest.mark.parametrize("reference", ["column", "prompter"])
@pytest.mark.parametrize("own", [False, True])
def test_mapping_prompt_messages_require_dataset_scope(tenants, own, reference):
    a, b, _ = tenants
    owner = a if own else b
    column_owner = owner if reference == "column" else a
    prompt = RunPrompter.no_workspace_objects.create(
        name="Reference messages", organization=owner["org"], workspace=owner["workspace"],
        dataset=owner["dataset"], model="offline", messages=[{"role": "system", "content": "Scoped instructions"}],
    )
    column = column_owner["column"]
    column.source = SourceChoices.RUN_PROMPT.value
    column.source_id = str(prompt.id)
    column.save()
    clear_workspace_context()
    keys, values = process_mapping(
        {"prompt": str(column.id)}, a["rows"][0], eval_template_name="prompt_instruction_adherence",
    )
    if own:
        assert values[0]["system_prompt"] == prompt.messages
    else:
        assert "Scoped instructions" not in repr(values)


def test_column_reference_keeps_uuid_within_dataset(tenants):
    a, b, _ = tenants
    clear_workspace_context()
    assert _resolve_column_reference(str(b["column"].id), a["dataset"].id) is None
    assert _resolve_column_reference(str(a["column"].id), a["dataset"].id) == a["column"]


@pytest.mark.parametrize("own", [False, True])
def test_replacement_column_is_authorized_before_clearing_cells(tenants, own):
    a, b, _ = tenants
    column = (a if own else b)["column"]
    runner = EvaluationRunner.__new__(EvaluationRunner)
    runner.is_only_eval = True
    runner.replace_column_id = column.id
    before = list(Cell.all_objects.order_by("id").values())
    clear_workspace_context()
    if own:
        assert runner._create_or_update_column(a["dataset"], {}) == column
    else:
        with pytest.raises(Column.DoesNotExist):
            runner._create_or_update_column(a["dataset"], {})
        assert list(Cell.all_objects.order_by("id").values()) == before


@pytest.mark.parametrize("asynchronous", [False, True])
def test_column_name_expansion_requires_explicit_scope(tenants, asynchronous):
    a, b, _ = tenants
    b["column"].name = "Private reference name"
    b["column"].save()
    text = "{{" + str(a["column"].id) + "}} {{" + str(b["column"].id) + "}}"
    fn = async_to_sync(replace_ids_with_column_name_async) if asynchronous else replace_ids_with_column_name
    clear_workspace_context()
    assert fn(text) == text
    result = fn(text, organization=a["org"], workspace=a["workspace"])
    assert "{{question}}" in result
    assert str(b["column"].id) in result
    assert b["column"].name not in result


@pytest.mark.parametrize("scope", ["own", "organization", "workspace", "unknown", "deleted"])
def test_eval_instance_checks_current_knowledge_base_scope(tenants, scope):
    a, b, _ = tenants
    kb_id = kb_for(a, b, scope)
    runner = EvaluationRunner.__new__(EvaluationRunner)
    runner.organization_id = a["org"].id
    runner.workspace_id = a["workspace"].id
    runner.eval_class = object
    runner.eval_template = None
    runner.user_eval_metric = None
    runner.version_number = None
    runner.futureagi_eval = False
    clear_workspace_context()
    with patch("evaluations.engine.instance.create_eval_instance", return_value=("instance", None)) as create:
        if scope == "own":
            assert runner._create_eval_instance({}, kb_id=kb_id) == "instance"
            create.assert_called_once()
        else:
            with pytest.raises(ValueError, match="Knowledge base not found"):
                runner._create_eval_instance({}, kb_id=kb_id)
            create.assert_not_called()


def test_prompt_stream_expands_names_after_scope_resolution(tenants):
    from sockets.prompt_stream_consumer import PromptStreamConsumer

    a, b, _ = tenants
    b["column"].name = "Private reference name"
    b["column"].save()
    text = "{{" + str(a["column"].id) + "}} {{" + str(b["column"].id) + "}}"
    consumer = PromptStreamConsumer()

    async def authorized(**kwargs):
        await kwargs["run"](a["workspace"], str(a["org"].id))

    consumer._execute_with_org = authorized
    consumer._make_ws_manager = lambda org_id: None
    with patch("sockets.prompt_stream_consumer.improve_prompt_async", new_callable=AsyncMock) as improve:
        async_to_sync(consumer.execute_improve_prompt_async)({"original_prompt": text}, "example")
    rendered = improve.call_args.kwargs["original_prompt"]
    assert "{{question}}" in rendered
    assert str(b["column"].id) in rendered
    assert b["column"].name not in rendered


@pytest.mark.parametrize("own", [False, True])
def test_annotation_input_validation_uses_uniform_error_before_writes(tenants, own):
    a, b, _ = tenants
    annotation, label = _annotation(a)
    label.type = AnnotationTypeChoices.CATEGORICAL.value
    label.settings = {"inputs": [str((a if own else b)["column"].id)]}
    view = AnnotationsViewSet()
    view.request = _request(a, "post", {})
    before = list(Column.no_workspace_objects.order_by("id").values())
    if own:
        view.process_new_annotaion(annotation, [label], [])
        assert Column.no_workspace_objects.count() == len(before) + annotation.responses
    else:
        with pytest.raises(ValidationError) as raised:
            view.process_new_annotaion(annotation, [label], [])
        assert b["column"].name not in str(raised.value)
        assert list(Column.no_workspace_objects.order_by("id").values()) == before
