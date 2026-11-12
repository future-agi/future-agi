"""DB-free regressions for partial dataset prompt edits through the real API view.

Only persistence and dispatch are mocked; request validation, message processing,
column updates and HTTP envelopes use production code.
"""

import sys
import uuid
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.db.backends.base.base import BaseDatabaseWrapper
from rest_framework.test import APIRequestFactory, force_authenticate

from model_hub.models.choices import (
    CellStatus,
    DataTypeChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Column, Dataset
from model_hub.services import selective_rerun
from model_hub.tests.rerun_fixtures import MemoryQuerySet, isolated_transactions
from model_hub.views import run_prompt

PATH = "/model-hub/develops/edit_run_prompt_column/"
CONFIG = {
    "model": "existing-model",
    # Preserve stored messages, including empty system content, unless replaced.
    "messages": [
        {"role": "system", "content": ""},
        {"role": "user", "content": "Existing prompt"},
    ],
    "output_format": "string",
    "temperature": 0.7,
    "frequency_penalty": 0.1,
    "presence_penalty": 0.2,
    "max_tokens": 512,
    "top_p": 0.9,
    "response_format": None,
    "tool_choice": "auto",
    "concurrency": 5,
    "run_prompt_config": {"temperature": 0.7, "max_tokens": 512, "voice": "keep"},
}


@pytest.fixture
def edit(monkeypatch):
    monkeypatch.setattr(
        BaseDatabaseWrapper,
        "ensure_connection",
        Mock(side_effect=AssertionError("This regression must not access a database")),
    )
    dataset = Dataset(id=uuid.uuid4(), organization_id=uuid.uuid4())
    prompt = SimpleNamespace(
        id=uuid.uuid4(),
        name="Existing prompt name",
        status=StatusType.COMPLETED.value,
        tools=Mock(),
        save=Mock(),
        **deepcopy(CONFIG),
    )
    # A column can have been renamed separately from its RunPrompter.
    column = Column(
        id=uuid.uuid4(),
        dataset=dataset,
        name="Existing column name",
        source=SourceChoices.RUN_PROMPT.value,
        source_id=str(prompt.id),
        data_type=DataTypeChoices.TEXT.value,
    )
    monkeypatch.setattr(column, "save", Mock())
    querysets = {}
    for kind, row in (
        ("dataset", dataset),
        ("column", column),
        ("run_prompter", prompt),
    ):
        queryset = Mock()
        queryset.filter.return_value = queryset
        queryset.select_for_update.return_value = queryset
        queryset.first.return_value = row
        monkeypatch.setattr(
            run_prompt, f"_request_{kind}_queryset", Mock(return_value=queryset)
        )
        querysets[kind] = queryset
    cells = Mock()
    cells_filter = Mock(return_value=cells)
    monkeypatch.setattr(run_prompt.Cell.objects, "filter", cells_filter)
    row = SimpleNamespace(
        id=uuid.uuid4(), dataset_id=dataset.id, deleted=False, order=0
    )
    rows_filter = Mock(
        side_effect=lambda **lookup: MemoryQuerySet([row]).filter(**lookup)
    )
    # selected_rows imports its own Row model; leave the selector itself real.
    monkeypatch.setattr(
        selective_rerun, "Row", SimpleNamespace(objects=SimpleNamespace(filter=rows_filter))
    )
    tools = Mock(return_value=[SimpleNamespace(id="new-tool")])
    monkeypatch.setattr(run_prompt.Tools.objects, "filter", tools)
    catalog = Mock(return_value=True)
    monkeypatch.setattr(run_prompt, "is_model_in_catalog", catalog)
    dispatch = Mock(return_value=SimpleNamespace(id="workflow-id"))
    # Importing the task module initializes Redis clients; replace that boundary.
    monkeypatch.setitem(
        sys.modules,
        "model_hub.tasks.run_prompt",
        SimpleNamespace(process_prompts_single=SimpleNamespace(apply_async=dispatch)),
    )

    def post(updates):
        request = APIRequestFactory().post(
            PATH,
            {"dataset_id": str(dataset.id), "column_id": str(column.id), **updates},
            format="json",
        )
        force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
        with isolated_transactions() as boundary:
            def committed_dispatch(*args, **kwargs):
                assert not boundary.frames, "Prompt dispatch must follow atomic exit"
                return SimpleNamespace(id="workflow-id")

            dispatch.side_effect = committed_dispatch
            return run_prompt.EditRunPromptColumnView.as_view()(request)

    return SimpleNamespace(
        post=post,
        dataset=dataset,
        prompt=prompt,
        column=column,
        cells=cells,
        cells_filter=cells_filter,
        rows_filter=rows_filter,
        row_ids=[str(row.id)],
        tools=tools,
        catalog=catalog,
        dispatch=dispatch,
        querysets=querysets,
    )


def assert_config(edit, **updates):
    expected = {**deepcopy(CONFIG), **updates}
    assert {field: getattr(edit.prompt, field) for field in CONFIG} == expected


def assert_rerun(edit, response):
    assert response.status_code == 200, response.data
    assert response.data["status"] is True
    edit.prompt.save.assert_called_once_with()
    assert edit.prompt.status == StatusType.RUNNING.value
    # Preserve previous cell output; only selected rows transition to RUNNING.
    edit.cells.update.assert_called_once_with(status=CellStatus.RUNNING.value)
    edit.cells_filter.assert_called_once_with(
        column=edit.column, dataset=edit.dataset,
        row_id__in=edit.row_ids, deleted=False,
    )
    edit.rows_filter.assert_called_once_with(dataset_id=edit.dataset.id, deleted=False)
    edit.querysets["run_prompter"].select_for_update.assert_called_once_with(of=("self",))
    edit.dispatch.assert_called_once_with(
        args=({
            "type": "editing", "prompt_id": str(edit.prompt.id),
            "row_ids": edit.row_ids,
        },)
    )


def assert_no_mutation(edit):
    edit.prompt.save.assert_not_called()
    edit.column.save.assert_not_called()
    edit.cells.update.assert_not_called()
    edit.cells_filter.assert_not_called()
    edit.rows_filter.assert_not_called()
    edit.prompt.tools.clear.assert_not_called()
    edit.prompt.tools.set.assert_not_called()
    edit.dispatch.assert_not_called()
    assert_config(edit)
    assert edit.prompt.name == "Existing prompt name"
    assert edit.column.name == "Existing column name"


@pytest.mark.parametrize("config", [{}, {"config": None}, {"config": {}}])
def test_name_only_preserves_existing_config(edit, config):
    response = edit.post({"name": "Renamed output", **config})

    assert_rerun(edit, response)
    assert edit.prompt.name == edit.column.name == "Renamed output"
    assert_config(edit)
    edit.prompt.tools.clear.assert_not_called()
    edit.prompt.tools.set.assert_not_called()
    edit.catalog.assert_not_called()


@pytest.mark.parametrize("name", [{}, {"name": None}])
def test_config_only_preserves_existing_names_and_unsent_config(edit, name):
    response = edit.post({"config": {"concurrency": 3}, **name})

    assert_rerun(edit, response)
    assert edit.prompt.name == "Existing prompt name"
    assert edit.column.name == "Existing column name"
    assert_config(edit, concurrency=3)
    edit.prompt.tools.clear.assert_not_called()
    edit.prompt.tools.set.assert_not_called()


@pytest.mark.parametrize(
    "stored",
    [
        CONFIG["run_prompt_config"],
        {
            "reasoning": {
                "reasoning_effort": "high",
                "budget_tokens": 1024,
                "show_reasoning_process": True,
            },
            "voice": "alloy",
            "booleans": {"enabled": True},
            "dropdowns": {"mode": "old"},
        },
        None,
    ],
)
@pytest.mark.parametrize("sent", [{"temperature": None}, {}])
def test_sent_run_prompt_config_replaces_stored_settings(edit, stored, sent):
    edit.prompt.run_prompt_config = deepcopy(stored)
    response = edit.post({"name": "Renamed", "config": {"run_prompt_config": sent}})

    assert_rerun(edit, response)
    # Frontend edits send the whole object; omitted settings must be removed.
    assert edit.prompt.run_prompt_config == sent
    expected_temperature = None if "temperature" in sent else CONFIG["temperature"]
    assert edit.prompt.temperature == expected_temperature


@pytest.mark.parametrize("stored", [CONFIG["run_prompt_config"], None])
def test_omitted_run_prompt_config_keeps_stored_settings(edit, stored):
    edit.prompt.run_prompt_config = deepcopy(stored)
    response = edit.post({"name": "Renamed", "config": {"concurrency": 3}})

    assert_rerun(edit, response)
    assert edit.prompt.run_prompt_config == stored
    assert edit.prompt.concurrency == 3


@pytest.mark.parametrize("name", [{}, {"name": None}])
@pytest.mark.parametrize("config", [{}, {"config": None}, {"config": {}}])
def test_no_effective_update_returns_400_without_side_effects(edit, name, config):
    response = edit.post({**name, **config})

    assert response.status_code == 400, response.data
    assert "nothing to update" in str(response.data).lower()
    assert_no_mutation(edit)


@pytest.mark.parametrize("tools", [[], None, [{"id": "new-tool"}]])
def test_explicit_tools_update_retains_replace_or_clear_behavior(edit, tools):
    response = edit.post({"name": "Renamed", "config": {"tools": tools}})

    assert_rerun(edit, response)
    edit.prompt.tools.clear.assert_called_once_with()
    if tools:
        edit.tools.assert_called_once_with(id__in=["new-tool"])
        edit.prompt.tools.set.assert_called_once_with(edit.tools.return_value)
    else:
        edit.tools.assert_not_called()
        edit.prompt.tools.set.assert_not_called()


def test_name_and_config_update_still_validates_model_and_cleans_sent_messages(edit):
    messages = [
        {"role": "system", "content": ""},
        {"role": "user", "content": "Replacement prompt"},
    ]
    response = edit.post(
        {"name": "Renamed", "config": {"model": "new-model", "messages": messages}}
    )

    assert_rerun(edit, response)
    assert edit.prompt.name == edit.column.name == "Renamed"
    assert edit.prompt.model == "new-model"
    assert edit.prompt.messages == messages[1:]
    edit.catalog.assert_called_once_with(
        "new-model", organization_id=edit.dataset.organization_id
    )


def test_invalid_model_still_returns_400_before_mutation(edit):
    edit.catalog.return_value = False
    response = edit.post({"name": "Renamed", "config": {"model": "removed-model"}})

    assert response.status_code == 400, response.data
    assert_no_mutation(edit)


@pytest.mark.parametrize("missing", ["dataset", "column", "run_prompter"])
def test_missing_or_out_of_scope_resource_still_returns_404(edit, missing):
    edit.querysets[missing].first.return_value = None
    response = edit.post({"name": "Renamed", "config": {"concurrency": 3}})

    assert response.status_code == 404, response.data
    assert_no_mutation(edit)


@pytest.mark.parametrize("invalid", [{"config": []}, {"name": ""}, {"unknown": True}])
def test_invalid_request_still_returns_400_before_mutation(edit, invalid):
    response = edit.post(invalid)

    assert response.status_code == 400, response.data
    assert_no_mutation(edit)
