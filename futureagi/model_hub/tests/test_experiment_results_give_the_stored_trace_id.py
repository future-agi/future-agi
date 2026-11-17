"""Experiment results give each trace id in the span store's form.

Platform python-task runs and SDK runs record OpenTelemetry's 32 hex digits;
the span store keeps the same 128 bits as a dashed UUID string, the form every
trace view, link and annotation uses. The Results grid, its matrix and row runs,
and the run item read give that form, so the trace drawer and everything it
opens use it. Stored rows are not rewritten.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

from model_hub.services.experiment_run_read import _score_ref, item_view
from model_hub.services.results_grid import result_cell, trial_record

DASHED = "4bf92f35-77b3-4da0-a3ce-929d0e0e4736"
OPENTELEMETRY = "4bf92f3577b34da0a3ce929d0e0e4736"
EVAL_DASHED = "0af76519-16cd-43dd-8448-eb211c80319c"
EVAL_OPENTELEMETRY = "0af7651916cd43dd8448eb211c80319c"


def _item(trace_id, score_refs=()):
    at = datetime(2026, 11, 14, tzinfo=UTC)
    return SimpleNamespace(
        id="44444444-4444-4444-8444-444444444444",
        example_id="example-1",
        trial_number=1,
        status="completed",
        metadata={},
        error=None,
        output="echo",
        cost=None,
        latency_ms=12,
        token_usage=None,
        trace_id=trace_id,
        root_span_id="00f067aa0ba902b7",
        score_refs=list(score_refs),
        created_at=at,
        updated_at=at,
    )


def _trial(trace_id):
    run = SimpleNamespace(status="completed")
    return trial_record(_item(trace_id), 1, run, {})


def test_a_trial_recorded_with_an_opentelemetry_id_gives_its_dashed_id():
    assert _trial(OPENTELEMETRY)["trace_id"] == DASHED


def test_a_results_cell_lists_the_dashed_ids_of_its_trials():
    cell = result_cell([_trial(OPENTELEMETRY), _trial(DASHED.upper())])

    assert cell["trace_ids"] == [DASHED, DASHED]
    assert [t["trace_id"] for t in cell["trials_detail"]] == [DASHED, DASHED]


def test_a_trial_recorded_with_a_dashed_id_gives_it_as_before():
    assert _trial(DASHED)["trace_id"] == DASHED


def test_a_trial_without_a_trace_gives_none():
    assert _trial(None)["trace_id"] is None


def test_the_run_item_read_gives_the_dashed_id_and_keeps_the_stored_row():
    item = _item(OPENTELEMETRY, [{"metric_id": "m", "trace_id": EVAL_OPENTELEMETRY}])

    view = item_view(item, 1000)

    assert view["trace_id"] == DASHED
    assert view["score_refs"][0]["trace_id"] == EVAL_DASHED
    assert item.trace_id == OPENTELEMETRY


def test_a_score_reference_gives_its_eval_trace_in_the_dashed_form():
    assert _score_ref({"trace_id": EVAL_OPENTELEMETRY})["trace_id"] == EVAL_DASHED
    assert _score_ref({"trace_id": EVAL_DASHED})["trace_id"] == EVAL_DASHED
    assert _score_ref({})["trace_id"] is None
