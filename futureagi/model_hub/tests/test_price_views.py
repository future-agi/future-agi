"""The model list and the dataset views show the price that is billed, on its date.

``listed_pricing`` gives a model-list row the price billed now. ``cell_token_cost``
prices a run-prompt column's cells each at the price in force when the run that
wrote its tokens started (``column_metadata["run_started_at"]``, falling back to
``created_at`` for a cell without it; never ``updated_at``), so a column run before
and after a price change, or rerun after one, costs the sum of both. The tests that need the database build a small dataset; the rest read the
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


def _cell(dataset, column, order, created_at, updated_at=None, run_started_at=None):
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
    # A queryset update leaves both stamps as given (the model sets them on save).
    Cell.objects.filter(id=cell.id).update(
        created_at=created_at, updated_at=updated_at or created_at
    )
    if run_started_at is not None:
        Cell.objects.filter(id=cell.id).update(
            column_metadata={"run_started_at": run_started_at.isoformat()}
        )
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
def test_each_cell_is_priced_on_the_date_it_was_created(dated_cells):
    _, run_prompter, column = dated_cells

    stats = cell_token_cost(Cell.objects.filter(column=column), run_prompter.model)

    # 4.5 at 0.75/3.75, then 9.0 at 1.5/7.5 twice.
    assert stats["cost"] == 22.5
    assert stats["cell_count"] == 3
    assert stats["prompt_tokens"] == 3_000_000
    assert stats["completion_tokens"] == 3_000_000
    assert stats["response_time"] == 6.0


@pytest.mark.django_db
def test_a_cell_saved_again_after_a_price_change_keeps_its_old_price(prompt_column):
    dataset, run_prompter, column = prompt_column
    # Run before the change, then saved again after it (an edit, a re-save of any
    # kind): updated_at moves, the run's date does not.
    _cell(dataset, column, 0, BEFORE_2027, updated_at=AFTER_2027)

    stats = cell_token_cost(Cell.objects.filter(column=column), run_prompter.model)

    # 1M in at 0.75 and 1M out at 3.75; at the 2027 rates it would be 9.0.
    assert stats["cost"] == 4.5


@pytest.mark.django_db
def test_a_cell_rerun_after_a_price_change_is_priced_at_the_rerun(prompt_column):
    dataset, run_prompter, column = prompt_column
    # First run before the change; the rerun reuses the cell and rewrites its
    # tokens after it, so the run that wrote them started after the change.
    _cell(dataset, column, 0, BEFORE_2027, run_started_at=AFTER_2027)

    stats = cell_token_cost(Cell.objects.filter(column=column), run_prompter.model)

    # 1M in at 1.5 and 1M out at 7.5; at the first run's rates it would be 4.5.
    assert stats["cost"] == 9.0


@pytest.mark.django_db
def test_a_cell_run_before_a_change_and_edited_after_keeps_the_old_price(
    prompt_column,
):
    dataset, run_prompter, column = prompt_column
    _cell(
        dataset,
        column,
        0,
        BEFORE_2027,
        updated_at=AFTER_2027,
        run_started_at=BEFORE_2027,
    )

    stats = cell_token_cost(Cell.objects.filter(column=column), run_prompter.model)

    assert stats["cost"] == 4.5


@pytest.mark.django_db
def test_a_cell_with_no_run_start_is_priced_at_its_creation(prompt_column):
    dataset, run_prompter, column = prompt_column
    # Written before run_started_at existed: created before the change, saved after.
    legacy = _cell(dataset, column, 0, BEFORE_2027, updated_at=AFTER_2027)
    audio = _cell(dataset, column, 1, AFTER_2027)
    Cell.objects.filter(id=audio.id).update(
        column_metadata={"audio_duration_seconds": 3.0}
    )

    assert Cell.objects.get(id=legacy.id).column_metadata == {}
    stats = cell_token_cost(Cell.objects.filter(column=column), run_prompter.model)

    # 4.5 for the first cell (its creation), 9.0 for the second.
    assert stats["cost"] == 13.5


@pytest.mark.django_db
def test_a_cell_can_be_saved_with_only_the_run_start(prompt_column):
    dataset, _, column = prompt_column
    cell = _cell(dataset, column, 0, BEFORE_2027)

    cell.column_metadata = {"run_started_at": AFTER_2027.isoformat()}
    cell.save()  # full_clean runs the column_metadata validator

    cell.refresh_from_db()
    assert cell.column_metadata == {"run_started_at": AFTER_2027.isoformat()}


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
def test_the_dataset_column_metadata_averages_the_dated_cost(auth_client, dated_cells):
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
def test_the_parts_are_priced_just_before_each_date_and_on_it(dated_cells, monkeypatch):
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


# --- Every run writes the instant it started with the tokens


def _run_row(
    monkeypatch,
    run_prompter,
    column,
    row,
    now,
    edit_mode,
    value_info=None,
    populate=None,
):
    """Run ``RunPrompts.process_row`` once with the model call stubbed.

    ``now`` is the clock: one instant, or the instants successive reads of it
    return (the last repeats).
    """
    from model_hub.views import run_prompt as run_prompt_view

    if value_info is None:
        value_info = {
            "data": {"response": "new"},
            "metadata": {
                "usage": {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000},
                "response_time": 2.0,
            },
        }
    instants = list(now) if isinstance(now, list | tuple) else [now]
    clock = iter(instants)

    class StubRunPrompt:
        def __init__(self, **kwargs):
            pass

        def litellm_response(self):
            return "new", value_info

    monkeypatch.setattr(run_prompt_view, "RunPrompt", StubRunPrompt)
    monkeypatch.setattr(run_prompt_view, "log_and_deduct_cost_for_api_request", None)
    monkeypatch.setattr(run_prompt_view, "close_old_connections", lambda: None)
    monkeypatch.setattr(
        run_prompt_view,
        "populate_placeholders",
        populate or (lambda messages, **kwargs: messages),
    )
    monkeypatch.setattr(run_prompt_view, "_utc_now", lambda: next(clock, instants[-1]))

    runner = run_prompt_view.RunPrompts(run_prompter.id)
    runner.load_run_prompt_id()
    runner.is_editing = False  # run_prompt() sets it before it calls process_row
    runner.process_row(row, column, edit_mode=edit_mode)
    return Cell.objects.get(row=row, column=column)


@pytest.mark.django_db
def test_a_first_run_writes_its_start_with_the_tokens(prompt_column, monkeypatch):
    dataset, run_prompter, column = prompt_column
    row = Row.objects.create(dataset=dataset, order=0)

    cell = _run_row(monkeypatch, run_prompter, column, row, AFTER_2027, False)

    assert cell.column_metadata == {"run_started_at": AFTER_2027.isoformat()}
    assert cell.prompt_tokens == 1_000_000
    stats = cell_token_cost(Cell.objects.filter(column=column), run_prompter.model)
    assert stats["cost"] == 9.0


@pytest.mark.django_db
def test_a_rerun_of_a_cell_writes_the_start_of_the_rerun(prompt_column, monkeypatch):
    dataset, run_prompter, column = prompt_column
    old = _cell(dataset, column, 0, BEFORE_2027, run_started_at=BEFORE_2027)
    Cell.objects.filter(id=old.id).update(
        column_metadata={
            "run_started_at": BEFORE_2027.isoformat(),
            "audio_duration_seconds": 3.0,
        }
    )

    cell = _run_row(monkeypatch, run_prompter, column, old.row, AFTER_2027, True)

    assert cell.id == old.id
    assert cell.column_metadata == {
        "run_started_at": AFTER_2027.isoformat(),
        "audio_duration_seconds": 3.0,
    }
    stats = cell_token_cost(Cell.objects.filter(column=column), run_prompter.model)
    # The first run's rates would give 4.5.
    assert stats["cost"] == 9.0


@pytest.mark.django_db
def test_a_cell_made_by_an_edit_mode_run_writes_its_start(prompt_column, monkeypatch):
    dataset, run_prompter, column = prompt_column
    row = Row.objects.create(dataset=dataset, order=0)

    cell = _run_row(monkeypatch, run_prompter, column, row, BEFORE_2027, True)

    assert cell.column_metadata == {"run_started_at": BEFORE_2027.isoformat()}


@pytest.mark.django_db
def test_the_call_stamps_the_instant_it_started_not_the_start_of_the_row(
    prompt_column, monkeypatch
):
    dataset, run_prompter, column = prompt_column
    row = Row.objects.create(dataset=dataset, order=0)
    before_midnight = datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC)
    call_start = datetime(2027, 1, 1, 0, 0, 1, tzinfo=UTC)

    # The row took the first reading; its bookkeeping then ran past midnight, and
    # the call started at the second.
    cell = _run_row(
        monkeypatch, run_prompter, column, row, [before_midnight, call_start], False
    )

    assert cell.column_metadata == {"run_started_at": call_start.isoformat()}
    stats = cell_token_cost(Cell.objects.filter(column=column), run_prompter.model)
    assert stats["cost"] == 9.0


@pytest.mark.django_db
def test_a_row_that_fails_before_its_call_keeps_the_start_of_the_row(
    prompt_column, monkeypatch
):
    dataset, run_prompter, column = prompt_column
    row = Row.objects.create(dataset=dataset, order=0)

    def fail(messages, **kwargs):
        raise RuntimeError("placeholders failed")

    # The model call is never reached, so only the start of the row was taken.
    cell = _run_row(
        monkeypatch,
        run_prompter,
        column,
        row,
        [BEFORE_2027, AFTER_2027],
        False,
        populate=fail,
    )

    assert cell.status == CellStatus.ERROR.value
    assert cell.column_metadata == {"run_started_at": BEFORE_2027.isoformat()}


class _FalsyInfo(dict):
    """A value_info that is falsy whatever it holds."""

    def __bool__(self):
        return False


@pytest.mark.django_db
def test_a_first_run_with_a_falsy_value_info_saves_a_cell_with_no_tokens(
    prompt_column, monkeypatch
):
    dataset, run_prompter, column = prompt_column
    row = Row.objects.create(dataset=dataset, order=0)

    cell = _run_row(
        monkeypatch,
        run_prompter,
        column,
        row,
        AFTER_2027,
        False,
        value_info=_FalsyInfo(),
    )

    assert cell.prompt_tokens is None
    assert cell.completion_tokens is None
    assert cell.response_time is None
    assert cell.column_metadata == {"run_started_at": AFTER_2027.isoformat()}


@pytest.mark.django_db
@pytest.mark.parametrize("stored", [None, [], "text", 7])
def test_a_run_over_a_non_dict_column_metadata_still_stamps_its_start(
    prompt_column, monkeypatch, stored
):
    dataset, run_prompter, column = prompt_column
    old = _cell(dataset, column, 0, BEFORE_2027)
    Cell.objects.filter(id=old.id).update(column_metadata=stored)

    cell = _run_row(monkeypatch, run_prompter, column, old.row, AFTER_2027, True)

    assert cell.column_metadata == {"run_started_at": AFTER_2027.isoformat()}


# --- A stored run start is checked on save and guarded when read


@pytest.mark.django_db
@pytest.mark.parametrize(
    "bad",
    ["not a date", "2026-13-45T00:00:00+00:00", "1767225600", "2027-01-01", 1767225600],
)
def test_a_cell_is_not_saved_with_a_run_start_that_is_not_iso(prompt_column, bad):
    from django.core.exceptions import ValidationError

    dataset, _, column = prompt_column
    cell = _cell(dataset, column, 0, BEFORE_2027)

    cell.column_metadata = {"run_started_at": bad}
    with pytest.raises(ValidationError):
        cell.save()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "good",
    [
        AFTER_2027.isoformat(),
        "2027-01-02T12:00:00Z",
        "2027-01-02T12:00:00.123456+00:00",
    ],
)
def test_a_run_start_is_stored_as_the_string_it_was_given(prompt_column, good):
    dataset, _, column = prompt_column
    cell = _cell(dataset, column, 0, BEFORE_2027)

    cell.column_metadata = {"run_started_at": good}
    cell.save()

    cell.refresh_from_db()
    assert cell.column_metadata == {"run_started_at": good}


@pytest.mark.django_db
@pytest.mark.parametrize(
    "bad",
    [
        "not a date",
        "2026-13-45T00:00:00+00:00",
        "2026-02-30T00:00:00+00:00",
        "1767225600",
        "",
        7,
        None,
        ["2027-01-02T12:00:00+00:00"],
    ],
)
def test_a_bad_run_start_already_stored_is_priced_at_the_creation_of_the_cell(
    prompt_column, bad
):
    dataset, run_prompter, column = prompt_column
    stamped = _cell(dataset, column, 0, BEFORE_2027, run_started_at=AFTER_2027)
    broken = _cell(dataset, column, 1, BEFORE_2027)
    # Written around the model's validation, as an old or hand-made row could be.
    Cell.objects.filter(id=broken.id).update(column_metadata={"run_started_at": bad})

    stats = cell_token_cost(Cell.objects.filter(column=column), run_prompter.model)

    # 9.0 for the stamped cell (its run start), 4.5 for the broken one (created
    # before the change); the aggregate does not fail on the bad value.
    assert stats["cost"] == 13.5
    assert stats["cell_count"] == 2
    assert stamped.id != broken.id


# --- The embedding writer keeps what another writer stamped


def _write_between_the_read_and_the_write(cell_id, metadata):
    """An execute wrapper: just before the statement that writes
    ``column_metadata``, another writer stamps the cell."""
    from django.db import connection

    state = {"done": False}

    def wrapper(execute, sql, params, many, context):
        if (
            not state["done"]
            and sql.lstrip().upper().startswith("UPDATE")
            and "column_metadata" in sql
        ):
            state["done"] = True
            Cell.objects.filter(id=cell_id).update(column_metadata=metadata)
        return execute(sql, params, many, context)

    return connection.execute_wrapper(wrapper), state


def _embedding_manager():
    from unittest.mock import MagicMock

    from agentic_eval.core.embeddings.embedding_manager import EmbeddingManager

    manager = EmbeddingManager.__new__(EmbeddingManager)
    manager.db_client = MagicMock()
    return manager


@pytest.mark.django_db
def test_embedding_a_cell_keeps_a_run_start_written_while_it_ran(prompt_column):
    dataset, _, column = prompt_column
    cell = _cell(dataset, column, 0, BEFORE_2027)
    stamp = {"run_started_at": AFTER_2027.isoformat()}
    hook, state = _write_between_the_read_and_the_write(cell.id, stamp)

    with hook:
        done = _embedding_manager().bulk_insert_embeddings(
            "t",
            [
                {
                    "eval_id": "e",
                    "vector": [0.1],
                    "metadata": {},
                    "cell_id": str(cell.id),
                }
            ],
        )

    assert state["done"]
    assert done == [str(cell.id)]
    cell.refresh_from_db()
    assert cell.column_metadata == {
        "run_started_at": AFTER_2027.isoformat(),
        "embedding": True,
        "audio_duration_seconds": 0,
    }


@pytest.mark.django_db
def test_embedding_a_cell_keeps_its_audio_duration_and_sets_its_own_key(
    prompt_column,
):
    dataset, _, column = prompt_column
    kept = _cell(dataset, column, 0, BEFORE_2027)
    bare = _cell(dataset, column, 1, BEFORE_2027)
    Cell.objects.filter(id=kept.id).update(
        column_metadata={"audio_duration_seconds": 3.5, "embedding": False}
    )
    Cell.objects.filter(id=bare.id).update(column_metadata=None)

    done = _embedding_manager().bulk_insert_embeddings(
        "t",
        [
            {"eval_id": "e", "vector": [0.1], "metadata": {}, "cell_id": str(c.id)}
            for c in (kept, bare)
        ],
    )

    assert sorted(done) == sorted([str(kept.id), str(bare.id)])
    kept.refresh_from_db()
    bare.refresh_from_db()
    assert kept.column_metadata == {"audio_duration_seconds": 3.5, "embedding": True}
    assert bare.column_metadata == {"audio_duration_seconds": 0, "embedding": True}


# --- The cell writers leave the token counts and the run start alone


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
            ".filter(...).update(...)"
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
def test_a_queryset_reset_keeps_the_token_counts_and_the_run_start(prompt_column):
    dataset, _, column = prompt_column
    cell = _cell(dataset, column, 0, BEFORE_2027, run_started_at=BEFORE_2027)

    Cell.objects.filter(column=column).update(
        value="",
        value_infos=json.dumps({}),
        status=CellStatus.RUNNING.value,
    )

    cell.refresh_from_db()
    assert cell.column_metadata == {"run_started_at": BEFORE_2027.isoformat()}
    assert cell.prompt_tokens == 1_000_000
    assert cell.completion_tokens == 1_000_000
    assert cell.status == CellStatus.RUNNING.value
