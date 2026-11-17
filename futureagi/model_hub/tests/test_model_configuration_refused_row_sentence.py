"""A prompt row refused for its model configuration says so, in the refusal's
own sentence, under its own code.

A model configuration, or a saved key a call would be given, that may not be
used is refused before any call (ModelConfigurationRefused): for instance a
key saved for Vertex AI under AI Providers that gives no complete
service-account key. The row records the refusal under its own code with the
refusal's own sentence, and the output cell, the experiment data drawer, the
results grid and the run's First error read it. A row recorded before keeps
what it shows, and every other failure still reads "Run failed on this row".
The row records the refusal without raising, so the run's retry policy never
sees it.

Real admission, setup, row execution and recording. The workflow start and the
evaluator engine are stand-ins, and the provider call is never made. The
refusal is raised by the row's call, or, where named, by the product's own
route lookup reading the workspace's saved key.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from model_hub.models.api_key import ApiKey
from model_hub.models.choices import CellStatus
from model_hub.models.develop_dataset import Cell
from model_hub.services.results_grid import first_failure_message, result_cell
from model_hub.tests.test_experiment_row_failure_codes import (
    PROMPT_RESPONSE,
    RUN_FAILED,
    bound_run,
    classic_world,
    first_error,
    grid_error,
    grid_try,
    output_cells,
    reason_of,
)
from model_hub.tests.test_managed_refusal_row_sentence import drawer_cells, failed_rows
from model_hub.tests.test_native_prompt_create_bridge import (  # noqa: F401
    native_fixture_evaluator,
)
from model_hub.tests.test_native_run_binding import native_world as native_world
from model_hub.tests.test_row_limits_on_managed_access import run_rows, started
from model_hub.utils.model_endpoints import ModelConfigurationRefused

# Row execution manages worker connections, as the native bridge tests do.
pytestmark = pytest.mark.django_db(transaction=True)

CODE = "model_configuration_refused"
SENTENCE = (
    "This model configuration is not valid; enter the key and settings from "
    "your provider."
)
REFUSED_TYPE = "ModelConfigurationRefused"
# A Vertex AI key as the AI Providers card saves it: a configuration that gives
# a location and no service-account key.
NO_SERVICE_ACCOUNT_KEY = {"location": "us-central1"}


def vertex_chat_model():
    """A chat model the catalog runs on Vertex AI."""
    from agentic_eval.core_evals.run_prompt.litellm_models import LiteLLMModelManager

    manager = LiteLLMModelManager("vertex_ai")
    names = sorted(
        model["model_name"]
        for model in manager.models
        if model.get("mode") == "chat" and model.get("providers") == "vertex_ai"
    )
    assert names, "the catalog runs no chat model on Vertex AI"
    return names[0]


def assert_rows_say_the_sentence(run, prepared):
    """Each row, its grid cell, its output cell and the run's First error say
    the refusal's sentence."""
    items = list(run.items.all())
    assert len(items) == len(prepared["row_ids"])
    for item in items:
        # The refusal reached the row with its own type.
        assert item.error["type"] == REFUSED_TYPE
        assert item.error == {"type": REFUSED_TYPE, "code": CODE, "message": SENTENCE}
        assert grid_error(run, item) == SENTENCE
    for cell in output_cells(prepared):
        assert (cell.value, cell.status) == (SENTENCE, CellStatus.ERROR.value)
        assert reason_of(cell) == {"reason": SENTENCE}
    assert first_error(run) == SENTENCE


def test_the_refusal_records_its_own_code_and_the_refusals_own_sentence():
    from model_hub.services.row_failures import (
        MODEL_CONFIGURATION_REFUSED,
        coded_error,
        coded_message,
        prompt_failure,
    )
    from model_hub.utils.model_endpoints import INVALID_MODEL_CONFIGURATION

    refusal = ModelConfigurationRefused()
    assert MODEL_CONFIGURATION_REFUSED == CODE
    # The sentence every such refusal already shows, as model_endpoints has it.
    assert str(refusal) == INVALID_MODEL_CONFIGURATION == SENTENCE
    assert prompt_failure(refusal) == (CODE, SENTENCE)
    assert coded_error(REFUSED_TYPE, CODE) == {
        "type": REFUSED_TYPE,
        "code": CODE,
        "message": SENTENCE,
    }
    # A fixed sentence: the code's, whatever text was stored beside it.
    assert coded_message({"code": CODE, "message": "stored text"}) == SENTENCE
    assert coded_message({"code": CODE}) == SENTENCE


def test_another_configuration_error_or_the_sentence_as_plain_text_reads_run_failed():
    from model_hub.services.row_failures import prompt_failure
    from model_hub.utils.model_endpoints import CustomModelConfigError

    # The code comes from the refusal's type, never from its text.
    for error in (CustomModelConfigError("another setting"), ValueError(SENTENCE)):
        assert prompt_failure(error) == ("row_failed", RUN_FAILED)


def test_a_row_refused_for_its_model_configuration_says_so_under_its_own_code(
    native_world, auth_client
):
    w = native_world
    context, prepared = failed_rows(w, ModelConfigurationRefused())
    assert_rows_say_the_sentence(bound_run(context), prepared)
    # The experiment data drawer reads the same cell. Read last: a request
    # clears the test's workspace context.
    for shown in drawer_cells(auth_client, w, prepared).values():
        assert (shown["cell_value"], shown["status"]) == (
            SENTENCE,
            CellStatus.ERROR.value,
        )
        assert shown["value_infos"] == {"reason": SENTENCE}


def test_a_row_on_a_vertex_model_whose_saved_key_gives_no_service_account_key_says_so(
    native_world, user
):
    w = native_world
    # The workspace's Vertex AI key, saved under AI Providers.
    ApiKey.no_workspace_objects.create(
        provider="vertex_ai",
        organization=w["dataset"].organization,
        workspace=w["dataset"].workspace,
        config_json=dict(NO_SERVICE_ACCOUNT_KEY),
        user=user,
    )
    context, prepared = started(w, None, model=vertex_chat_model())
    # No route stand-in: the product's own lookup reads the saved key.
    statuses, call = run_rows(w, context, prepared)
    call.assert_not_called()
    assert statuses == ["FAILED"] * len(prepared["row_ids"])
    assert_rows_say_the_sentence(bound_run(context), prepared)


def test_the_results_grid_cell_shows_the_refusals_sentence():
    def error_of(*trials):
        return result_cell(list(trials))["error"]

    stored = {"type": REFUSED_TYPE, "code": CODE, "message": "stored text"}
    assert error_of(grid_try(1, "error", stored)) == SENTENCE
    assert error_of(grid_try(1, "error", {"code": CODE})) == SENTENCE
    assert error_of(grid_try(1, "completed"), grid_try(2, "error", stored)) == SENTENCE


def test_the_runs_first_error_shows_the_refusals_sentence():
    def item(status, error=None):
        return SimpleNamespace(status=status, error=error)

    evidence = {
        ("a", 1): item("completed"),
        ("b", 1): item(
            "failed", {"type": REFUSED_TYPE, "code": CODE, "message": "stored text"}
        ),
    }
    assert first_failure_message(evidence) == SENTENCE


def test_a_row_recorded_before_under_the_general_code_still_reads_run_failed():
    def item(status, error=None):
        return SimpleNamespace(status=status, error=error)

    before = {"type": REFUSED_TYPE, "code": "row_failed", "message": RUN_FAILED}
    assert result_cell([grid_try(1, "error", before)])["error"] == RUN_FAILED
    assert first_failure_message({("a", 1): item("failed", before)}) == RUN_FAILED
    # A row recorded with no code gives no coded sentence: the grid cell and the
    # row drawer keep their own fallback, "Run failed on this row".
    uncoded = {"type": REFUSED_TYPE, "message": "stored text"}
    assert result_cell([grid_try(1, "error", uncoded)])["error"] is None
    assert first_failure_message({("a", 1): item("failed", uncoded)}) is None


def test_a_classic_row_refused_for_its_model_configuration_shows_the_sentence(
    organization, workspace, user
):
    from model_hub.views.experiment_runner import _process_row_impl

    experiment, target, column = classic_world(
        organization, workspace, user, display_name=None, linked=True
    )
    # The row is recorded and nothing is raised, so nothing is retried.
    with patch(PROMPT_RESPONSE, side_effect=ModelConfigurationRefused()):
        _process_row_impl(
            row_id=target.id,
            column_id=column.id,
            dataset_id=experiment.dataset_id,
            experiment_id=experiment.id,
            messages=[{"role": "user", "content": "Say hi"}],
            model="model-a",
            model_config={},
            skip_celery_evals=True,
        )
    cell = Cell.no_workspace_objects.get(column=column, row=target)
    assert (cell.value, cell.status) == (SENTENCE, CellStatus.ERROR.value)
    assert reason_of(cell) == {"reason": SENTENCE}
