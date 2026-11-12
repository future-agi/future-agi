"""Run Prompt renders f-string templates with the strict renderer.

A saved Run Prompt column whose config says "f-string" inserts plain names,
dictionary keys and list indexes as before. A field that is not one of those
fails the row with a plain message and nothing is sent to the model. The
preview, the other template formats and the accepted config are unchanged.
A saved mustache column resolves names by dictionary key and list index only.
"""

from unittest.mock import patch

import pytest
from rest_framework import status

from model_hub.models.choices import (
    CellStatus,
    DataTypeChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Cell, Column
from model_hub.models.run_prompt import RunPrompter
from model_hub.services import credit_ledger_port as credit_ledger
from model_hub.tests import test_run_prompt_api as run_prompt_api

dataset = run_prompt_api.dataset
input_column = run_prompt_api.input_column
row = run_prompt_api.row
cell = run_prompt_api.cell

REFUSED = "This template can only insert a named value or one of its keys."
PREVIEW_URL = "/model-hub/develops/preview_run_prompt_column/"
ADD_URL = "/model-hub/develops/add_run_prompt_column/"


def _byok(**_kwargs):
    return credit_ledger.PayerRoute.byok(provider="openai")


def _text_message(text):
    return [{"role": "user", "content": [{"type": "text", "text": text}]}]


def _sent_text(messages):
    parts = []
    for message in messages:
        content = message["content"]
        if isinstance(content, str):
            parts.append(content)
        else:
            parts.extend(item.get("text", "") for item in content)
    return "".join(parts)


@pytest.fixture
def profile_cell(db, dataset, row):
    column = Column.objects.create(
        name="profile",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    dataset.column_order.append(str(column.id))
    dataset.save()
    return Cell.objects.create(
        dataset=dataset,
        column=column,
        row=row,
        value='{"city": "Paris", "tags": ["red", "blue"]}',
    )


@pytest.fixture
def model_calls(monkeypatch):
    """Record the messages handed to the model; no provider is called."""
    from model_hub.services import selective_rerun
    from model_hub.views import run_prompt as view

    sent = []

    def _litellm_response(self, *args, **kwargs):
        sent.append(self.messages)
        return "answer", {"data": {"response": "answer"}, "metadata": {"usage": {}}}

    try:
        from ee.usage.services import emitter
    except ImportError:
        emitter = None
    if emitter is not None:
        monkeypatch.setattr(emitter, "emit", lambda event: None)
    monkeypatch.setattr(view, "log_and_deduct_cost_for_api_request", None)
    monkeypatch.setattr(view, "close_old_connections", lambda: None)
    monkeypatch.setattr(selective_rerun, "input_fingerprints", lambda *args: {})
    monkeypatch.setattr(selective_rerun, "save_fingerprints", lambda *args: None)
    monkeypatch.setattr(view.RunPrompt, "litellm_response", _litellm_response)
    with credit_ledger.use_route_resolver(_byok):
        yield sent


def _run_saved_column(
    dataset, organization, workspace, row, text, template_format="f-string"
):
    """Run one row of a saved Run Prompt column the way the worker does."""
    from model_hub.views.run_prompt import RunPrompts

    prompter = RunPrompter.objects.create(
        name="answer",
        dataset=dataset,
        organization=organization,
        workspace=workspace,
        model="gpt-4o",
        messages=_text_message(text),
        output_format="string",
        run_prompt_config={"template_format": template_format},
        status=StatusType.RUNNING.value,
    )
    column = Column.objects.create(
        name="answer",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.RUN_PROMPT.value,
        source_id=prompter.id,
    )
    runner = RunPrompts(prompter.id)
    runner.load_run_prompt_id()
    runner.is_editing = False
    runner.process_row(row, column)
    return Cell.objects.get(column=column, row=row, deleted=False)


def _preview(auth_client, dataset, text, run_prompt_config):
    return auth_client.post(
        PREVIEW_URL,
        {
            "dataset_id": str(dataset.id),
            "name": "answer",
            "first_n_rows": 1,
            "config": {
                "model": "gpt-4o",
                "messages": _text_message(text),
                "output_format": "string",
                "run_prompt_config": run_prompt_config,
            },
        },
        format="json",
    )


@pytest.mark.django_db
class TestSavedRunPromptColumnFString:
    def test_plain_names_keys_and_indexes_render(
        self, model_calls, dataset, organization, workspace, row, cell, profile_cell
    ):
        result = _run_saved_column(
            dataset,
            organization,
            workspace,
            row,
            "Answer {Input Column} for {profile[city]} ({profile[tags][1]})",
        )

        assert len(model_calls) == 1
        assert _sent_text(model_calls[0]) == "Answer Test input value for Paris (blue)"
        assert result.status == CellStatus.PASS.value
        assert result.value == "answer"

    def test_a_field_that_is_not_a_key_or_index_fails_the_row(
        self, model_calls, dataset, organization, workspace, row, cell, profile_cell
    ):
        result = _run_saved_column(
            dataset, organization, workspace, row, "Answer {Input Column.__class__}"
        )

        assert model_calls == []
        assert result.status == CellStatus.ERROR.value
        assert result.value == REFUSED


@pytest.mark.django_db
class TestSavedRunPromptColumnMustache:
    def test_a_correct_template_renders_as_before(
        self, model_calls, dataset, organization, workspace, row, cell, profile_cell
    ):
        result = _run_saved_column(
            dataset,
            organization,
            workspace,
            row,
            "Answer {{Input Column}} for {{profile.city}}: "
            "{{#profile.tags}}[{{.}}]{{/profile.tags}}",
            "mustache",
        )

        assert len(model_calls) == 1
        sent = _sent_text(model_calls[0])
        assert sent == "Answer Test input value for Paris: [red][blue]"
        assert result.status == CellStatus.PASS.value

    def test_a_dotted_name_resolves_by_key_and_index_only(
        self, model_calls, dataset, organization, workspace, row, cell, profile_cell
    ):
        result = _run_saved_column(
            dataset,
            organization,
            workspace,
            row,
            "Answer [{{Input Column.__class__}}] for {{profile.city}}",
            "mustache",
        )

        assert len(model_calls) == 1
        sent = _sent_text(model_calls[0])
        assert "class" not in sent
        assert sent == "Answer [] for Paris"
        assert result.status == CellStatus.PASS.value


@pytest.mark.django_db
class TestPreviewRunPromptColumnTemplates:
    def test_plain_names_render(
        self, model_calls, auth_client, dataset, row, cell, profile_cell
    ):
        response = _preview(
            auth_client, dataset, "Answer {{Input Column}} for {{profile.city}}", {}
        )

        assert response.status_code == status.HTTP_200_OK, response.content
        assert len(model_calls) == 1
        assert _sent_text(model_calls[0]) == "Answer Test input value for Paris"

    def test_a_field_that_is_not_a_key_or_index_is_not_inserted(
        self, model_calls, auth_client, dataset, row, cell, profile_cell
    ):
        response = _preview(
            auth_client,
            dataset,
            "Answer {Input Column.__class__}",
            {"template_format": "f-string"},
        )

        assert response.status_code == status.HTTP_200_OK, response.content
        for messages in model_calls:
            assert "<class" not in _sent_text(messages)
        assert "<class" not in response.content.decode()


@pytest.mark.django_db
class TestRunPromptTemplateFormatsUnchanged:
    def test_a_new_fstring_config_is_still_accepted(
        self, auth_client, dataset, input_column
    ):
        with patch("model_hub.tasks.run_prompt.process_prompts_single.apply_async"):
            response = auth_client.post(
                ADD_URL,
                {
                    "dataset_id": str(dataset.id),
                    "name": "F-string answer",
                    "config": {
                        "messages": _text_message("What is {Input Column}?"),
                        "output_format": "string",
                        "run_prompt_config": {"template_format": "f-string"},
                    },
                },
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK, response.content
        prompter = RunPrompter.objects.get(name="F-string answer", dataset=dataset)
        assert prompter.run_prompt_config["template_format"] == "f-string"

    @pytest.mark.parametrize(
        "template_format,text",
        [
            ("mustache", "Answer {{Input Column}} for {{profile.city}}"),
            ("jinja", "Answer {{Input Column}} for {{ profile.city }}"),
        ],
    )
    def test_saved_mustache_and_jinja_columns_render_as_before(
        self,
        model_calls,
        dataset,
        organization,
        workspace,
        row,
        cell,
        profile_cell,
        template_format,
        text,
    ):
        result = _run_saved_column(
            dataset, organization, workspace, row, text, template_format
        )

        assert len(model_calls) == 1
        assert _sent_text(model_calls[0]) == "Answer Test input value for Paris"
        assert result.status == CellStatus.PASS.value
