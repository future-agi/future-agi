"""The model list and the dataset views show the price that is billed, on its date.

``listed_pricing`` gives a model-list row the price billed now. ``cell_token_cost``
prices a run-prompt column's cells each at the price in force on the date the cell
was last written, so a column run before and after a price change costs the sum of
both. The tests that need the database build a small dataset; the rest read the
price tables.
"""

from __future__ import annotations

import ast
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentic_eval.core_evals.run_prompt import model_pricing
from agentic_eval.core_evals.run_prompt.available_models import AVAILABLE_MODELS
from agentic_eval.core_evals.run_prompt.litellm_models import LiteLLMModelManager
from agentic_eval.core_evals.run_prompt.model_pricing import (
    listed_pricing,
    price_change_dates,
)
from model_hub.models.choices import (
    CellStatus,
    DataTypeChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.custom_models import CustomAIModel
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.run_prompt import RunPrompter
from tfc.utils import functions
from tfc.utils.functions import cell_token_cost, get_prompt_stats

J2 = datetime(2027, 1, 2, tzinfo=UTC)
START_2026 = datetime(2026, 1, 1, tzinfo=UTC)
START_2027 = datetime(2027, 1, 1, tzinfo=UTC)
BEFORE_2027 = datetime(2026, 12, 31, 12, tzinfo=UTC)
AFTER_2027 = datetime(2027, 1, 2, 12, tzinfo=UTC)
RUN_PROMPT_VIEW = Path(__file__).resolve().parents[1] / "views" / "run_prompt.py"

ONE_MILLION_EACH = {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}


# --- The model list


@pytest.fixture
def at_january_2(monkeypatch):
    monkeypatch.setattr(model_pricing, "_utc_now", lambda: J2)


def _row(rows, name):
    return next(row for row in rows if row["model_name"] == name)


@pytest.mark.django_db
def test_the_model_list_shows_the_price_billed_now(at_january_2, organization):
    catalogue_before = [dict(row) for row in AVAILABLE_MODELS]
    CustomAIModel.objects.create(
        user_model_id="gpt-4o",
        provider="openai",
        input_token_cost=0.0,
        output_token_cost=0.0,
        organization=organization,
    )

    rows = LiteLLMModelManager("any", organization_id=organization.id).models

    # The organisation's own model: its price is its own, never a table's.
    assert _row(rows, "gpt-4o")["custom"] is True
    assert listed_pricing(_row(rows, "gpt-4o")) is None
    # gemini-3.7-flash is billed 1.5/7.5 from 1 January 2027.
    assert listed_pricing(_row(rows, "gemini/gemini-3.7-flash")) == {
        "input_per_1M_tokens": 1.5,
        "output_per_1M_tokens": 7.5,
        "cached_input_per_1M_tokens": 0.15,
    }
    # A speech row skips the gateway's per-token table.
    tts = _row(rows, "vertex_ai/gemini-2.5-pro-preview-tts")
    assert tts["mode"] == "tts"
    assert listed_pricing(tts) == {
        "input_per_1M_tokens": 1.25,
        "output_per_1M_tokens": 5.0,
    }
    # A catalogue row with no `pricing` that the gateway's table prices shows the
    # price it bills.
    no_pricing = _row(rows, "replicate/meta/llama-3-70b")
    assert "pricing" not in no_pricing
    assert listed_pricing(no_pricing) == {
        "input_per_1M_tokens": 0.65,
        "output_per_1M_tokens": 2.75,
    }
    # The catalogue itself is not changed by listing.
    assert AVAILABLE_MODELS == catalogue_before
    assert not any("custom" in row for row in AVAILABLE_MODELS)


def test_a_row_that_bills_the_default_lists_no_price():
    # The catalogue's row is per 1k characters: a token call bills the default.
    row = {"model_name": "openrouter/google/gemini-pro-1.5", "mode": "chat"}

    assert listed_pricing(row) is None


def test_image_and_transcription_rows_keep_their_own_price():
    # Only a token-call row needs a per-token input price to be shown.
    assert listed_pricing(
        {"model_name": "256-x-256/dall-e-2", "mode": "image_generation"}
    ) == {"per_image": 0.016}
    assert listed_pricing(
        {"model_name": "azure/whisper-1", "mode": "audio_transcription"}
    ) == {"per_minute_audio": 0.006}
    assert listed_pricing({"model_name": "flux-general-en", "mode": "stt"}) == {
        "input_per_minute": 0.0077
    }


# --- Dataset views


@pytest.fixture
def prompt_column(organization, workspace):
    """A dataset with a completed run-prompt column of gemini-3.7-flash."""
    dataset = Dataset.objects.create(
        name="Price dataset", organization=organization, workspace=workspace
    )
    run_prompter = RunPrompter.objects.create(
        name="Priced prompt",
        dataset=dataset,
        organization=organization,
        workspace=workspace,
        status=StatusType.COMPLETED.value,
        model="gemini-3.7-flash",
        messages=[{"role": "user", "content": "Hi"}],
        run_prompt_config={},
    )
    column = Column.objects.create(
        name="Output",
        data_type=DataTypeChoices.TEXT.value,
        dataset=dataset,
        source=SourceChoices.RUN_PROMPT.value,
        source_id=str(run_prompter.id),
    )
    return dataset, run_prompter, column


def _cell(dataset, column, order, updated_at):
    row = Row.objects.create(dataset=dataset, order=order)
    cell = Cell.objects.create(
        dataset=dataset,
        row=row,
        column=column,
        value="out",
        status=CellStatus.PASS.value,
        prompt_tokens=1_000_000,
        completion_tokens=1_000_000,
        response_time=2.0,
    )
    # A queryset update leaves updated_at as given (the model sets it on save).
    Cell.objects.filter(id=cell.id).update(updated_at=updated_at)
    return cell


@pytest.fixture
def dated_cells(prompt_column):
    dataset, _, column = prompt_column
    _cell(dataset, column, 0, BEFORE_2027)
    _cell(dataset, column, 1, AFTER_2027)
    # Exactly midnight: the new price applies from this instant.
    _cell(dataset, column, 2, START_2027)
    return prompt_column


@pytest.mark.django_db
def test_each_cell_is_priced_on_the_date_it_was_written(dated_cells):
    _, run_prompter, column = dated_cells

    stats = cell_token_cost(Cell.objects.filter(column=column), run_prompter.model)

    # 4.5 at 0.75/3.75, then 9.0 at 1.5/7.5 twice.
    assert stats["cost"] == 22.5
    assert stats["cell_count"] == 3
    assert stats["prompt_tokens"] == 3_000_000
    assert stats["completion_tokens"] == 3_000_000
    assert stats["response_time"] == 6.0


@pytest.mark.django_db
def test_a_model_with_no_dated_rows_is_priced_in_one_part(dated_cells, monkeypatch):
    _, _, column = dated_cells
    seen = []
    real = functions.calculate_total_cost

    def record(model, usage, **kwargs):
        seen.append(kwargs.get("at"))
        return real(model, usage, **kwargs)

    monkeypatch.setattr(functions, "calculate_total_cost", record)

    stats = cell_token_cost(Cell.objects.filter(column=column), "gpt-4o")

    # 2.5 in + 10 out per million, for three cells.
    assert stats["cost"] == 37.5
    assert seen == [None]


@pytest.mark.django_db(transaction=True)
def test_the_prompt_stats_average_the_dated_cost(dated_cells):
    dataset, run_prompter, _ = dated_cells

    stats = get_prompt_stats([run_prompter], dataset.id)

    assert stats["avg_cost"] == round(22.5 / 3, 4)
    assert stats["avg_tokens"] == 2_000_000.0


@pytest.mark.django_db(transaction=True)
def test_the_dataset_column_metadata_averages_the_dated_cost(
    auth_client, dated_cells
):
    dataset, _, column = dated_cells

    response = auth_client.get(
        f"/model-hub/develops/{dataset.id}/get-dataset-table/",
        {"column_config_only": "true", "page_size": 10, "current_page_index": 0},
    )

    assert response.status_code == 200
    config = next(
        item
        for item in response.json()["result"]["column_config"]
        if item["id"] == str(column.id)
    )
    assert config["metadata"]["average_cost"] == pytest.approx(22.5 / 3)
    assert config["metadata"]["average_tokens"] == pytest.approx(2_000_000)
    assert config["metadata"]["average_latency"] == pytest.approx(2.0)


def test_the_change_dates_follow_the_key_that_sets_the_price():
    # deepinfra/... resolves to gemini-3.6-flash in the gateway's table; the
    # catalogue never resolves this name.
    assert price_change_dates("deepinfra/google/gemini-3.6-flash") == (
        START_2026,
        START_2027,
    )
    assert price_change_dates("gpt-4o") == ()


@pytest.mark.django_db
def test_the_parts_are_priced_just_before_each_date_and_on_it(
    dated_cells, monkeypatch
):
    _, run_prompter, column = dated_cells
    seen = []

    def record(model, usage, **kwargs):
        seen.append(kwargs["at"])
        return {"total_cost": 0}

    monkeypatch.setattr(functions, "calculate_total_cost", record)

    cell_token_cost(Cell.objects.filter(column=column), run_prompter.model)

    assert seen == [
        START_2026 - timedelta(microseconds=1),
        START_2026,
        START_2027,
    ]


# --- The cell writers leave updated_at and the token counts alone


def _function(tree, name, within=None):
    scope = tree
    if within:
        scope = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef) and node.name == within
        )
    return next(
        node
        for node in ast.walk(scope)
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _is_cell_objects(node):
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "objects"
        and isinstance(node.value, ast.Name)
        and node.value.id == "Cell"
    )


def _cell_writers(function):
    """Every ``Cell.objects`` in a function, with whether it is ``.filter(...).update(...)``."""
    allowed = {}
    for node in ast.walk(function):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "update"
            and isinstance(node.func.value, ast.Call)
            and isinstance(node.func.value.func, ast.Attribute)
            and node.func.value.func.attr == "filter"
            and _is_cell_objects(node.func.value.func.value)
        ):
            allowed[id(node.func.value.func.value)] = {
                keyword.arg for keyword in node.keywords
            }
    return [
        (node, allowed.get(id(node)))
        for node in ast.walk(function)
        if _is_cell_objects(node)
    ]


@pytest.mark.parametrize(
    ("function_name", "within"),
    [("post", "EditRunPromptColumnView"), ("run_all_prompts_task", None)],
)
def test_a_rerun_resets_cells_with_a_queryset_update_of_three_fields(
    function_name, within
):
    tree = ast.parse(RUN_PROMPT_VIEW.read_text(encoding="utf-8"))
    function = _function(tree, function_name, within)

    writers = _cell_writers(function)
    assert writers, f"{function_name} no longer resets cells; update this test"
    for node, keywords in writers:
        assert keywords is not None, (
            f"{function_name} line {node.lineno}: Cell.objects is used other than as "
            ".filter(...).update(...), which would set updated_at"
        )
        assert keywords <= {"value", "value_infos", "status"}, (
            f"{function_name} line {node.lineno}: update() sets {sorted(keywords)}"
        )

    saves = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "save"
    ]
    for node in saves:
        receiver = node.func.value
        assert isinstance(receiver, ast.Name) and receiver.id in {
            "run_prompter",
            "run_prompt",
        }, (
            f"{function_name} line {node.lineno}: a save() on something but the run prompt"
        )


@pytest.mark.django_db
def test_a_queryset_reset_keeps_updated_at_and_the_token_counts(prompt_column):
    dataset, _, column = prompt_column
    cell = _cell(dataset, column, 0, BEFORE_2027)

    Cell.objects.filter(column=column).update(
        value="",
        value_infos=json.dumps({}),
        status=CellStatus.RUNNING.value,
    )

    cell.refresh_from_db()
    assert cell.updated_at == BEFORE_2027
    assert cell.prompt_tokens == 1_000_000
    assert cell.completion_tokens == 1_000_000
    assert cell.status == CellStatus.RUNNING.value
