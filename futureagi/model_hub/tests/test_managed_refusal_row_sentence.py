"""A native prompt row that Future AGI's managed access refused says why, in
the refusal's own words.

A managed-access refusal's message is its class's own copy, written by the
ledger port; it never carries a provider's text. The row records it under its
own code, and the output cell, the experiment data drawer, the results grid
and the run's First error read it. Every other failure still reads "Run failed
on this row", and a provider's text reaches none of them. The row records a
refusal without raising, so the run's retry policy never retries it; a refusal
that asks to wait still waits and tries again inside the call, as before.

Real admission, setup, row execution and recording. The workflow start, the
evaluator engine and the provider call are stand-ins; the refusal is raised by
the row's call (or, where named, by the real meter over a stand-in ledger).
"""

import json
from decimal import Decimal
from unittest.mock import patch

import pytest

from model_hub.models.choices import CellStatus
from model_hub.services import credit_ledger_port as cl
from model_hub.tests.test_experiment_row_failure_codes import (
    PROMPT_RESPONSE,
    RUN_FAILED,
    admitted,
    bound_run,
    first_error,
    grid_error,
    output_cells,
    reason_of,
    run_every_row,
)
from model_hub.tests.test_native_prompt_create_bridge import (  # noqa: F401
    native_fixture_evaluator,
)
from model_hub.tests.test_native_run_binding import native_world as native_world
from model_hub.tests.test_row_limits_on_managed_access import (
    claimed_answer,
    managed_route,
    run_rows,
    started,
)

# Row execution manages worker connections, as the native bridge tests do.
pytestmark = pytest.mark.django_db(transaction=True)

REFUSED = "managed_call_refused"
# The per-row limits refusals keep their own codes, whose sentences are fixed.
ROW_LIMIT_CODES = {
    "ManagedRowLimitsUnavailableError": "row_limits_need_own_key",
    "ManagedRowLimitsUnsupportedModelError": "row_limits_unsupported",
}
# A provider's own words: they must reach no place a person reads.
MARKER = "upstream said 500 for request req-7c41 with key sk-test-marker"


def refusal_classes():
    """Every managed-access refusal class with its own words, from the ledger
    port itself, so a class added later is covered too."""
    found, todo = set(), [cl.ManagedCallRefusal]
    while todo:
        for sub in todo.pop().__subclasses__():
            todo.append(sub)
            if sub.__module__ == cl.__name__ and (
                {"default_message", "message_key"} & set(vars(sub))
            ):
                found.add(sub)
    return sorted(found, key=lambda cls: cls.__qualname__)


REFUSALS = refusal_classes()


@pytest.fixture
def meter(monkeypatch):
    """The real meter over a stand-in ledger; records each wait instead of sleeping."""
    waits = []
    monkeypatch.setattr(cl, "estimate_call_cost", lambda *a, **kw: Decimal("0.10"))
    monkeypatch.setattr(cl, "_sleep", waits.append)
    cl.reset_meter_safety()
    yield waits
    cl.reset_meter_safety()


def drawer_cells(client, w, prepared):
    """What the experiment data drawer reads for the rows' output cells."""
    response = client.post(
        "/model-hub/experiments/v2/row-diff/",
        {
            "experiment_id": str(w["experiment"].id),
            "column_ids": [str(prepared["column_id"])],
            "row_ids": [str(row_id) for row_id in prepared["row_ids"]],
            "compare_column_ids": [],
        },
        format="json",
    )
    assert response.status_code == 200, response.content
    result = response.json()["result"]
    return {
        row_id: result[str(row_id)][str(prepared["column_id"])]
        for row_id in prepared["row_ids"]
    }


def failed_rows(w, error):
    """Every row's call raises ``error``; returns the run's context and setup."""
    _, _, context, prepared = admitted(w)
    with patch(PROMPT_RESPONSE, side_effect=error) as prompt_response:
        results = run_every_row(w["experiment"], context, prepared)
    # Each row ran its call once and ended with a status, raising nothing.
    assert [r["status"] for r in results] == ["FAILED"] * len(prepared["row_ids"])
    assert prompt_response.call_count == len(prepared["row_ids"])
    return context, prepared


def test_the_ledger_port_has_the_refusal_classes_these_tests_cover():
    assert len(REFUSALS) >= 21
    assert {
        cl.CreditsExhaustedError,
        cl.ManagedRefusedAfterDispatchError,
        cl.CreditLedgerUnavailableError,
        cl.ManagedRowLimitsUnavailableError,
    } <= set(REFUSALS)


def test_the_refusal_code_keeps_the_refusals_own_words():
    from model_hub.services.row_failures import (
        MANAGED_CALL_REFUSED,
        coded_message,
        prompt_failure,
    )

    refusal = cl.CreditsExhaustedError()
    assert MANAGED_CALL_REFUSED == REFUSED
    assert prompt_failure(refusal) == (REFUSED, refusal.message)
    assert (
        coded_message({"code": REFUSED, "message": refusal.message}) == refusal.message
    )


@pytest.mark.parametrize(
    "refusal_class", REFUSALS, ids=[f"refusal-{n}" for n in range(len(REFUSALS))]
)
def test_a_refused_row_says_why_in_the_refusals_own_words(
    native_world, auth_client, refusal_class
):
    w = native_world
    refusal = refusal_class()
    sentence = refusal.message
    assert sentence and sentence == str(refusal)
    code = ROW_LIMIT_CODES.get(refusal_class.__name__, REFUSED)
    context, prepared = failed_rows(w, refusal)
    run = bound_run(context)
    items = list(run.items.all())
    assert len(items) == len(prepared["row_ids"])
    for item in items:
        assert item.error == {
            "type": refusal_class.__name__,
            "code": code,
            "message": sentence,
        }
        assert grid_error(run, item) == sentence
    for cell in output_cells(prepared):
        assert (cell.value, cell.status) == (sentence, CellStatus.ERROR.value)
        assert reason_of(cell) == {"reason": sentence}
    assert first_error(run) == sentence
    # The experiment data drawer reads the same cell.
    for shown in drawer_cells(auth_client, w, prepared).values():
        assert (shown["cell_value"], shown["status"]) == (
            sentence,
            CellStatus.ERROR.value,
        )
        assert shown["value_infos"] == {"reason": sentence}


def shown_texts(run, prepared, client, w):
    """Every place a person reads the rows' failure, as text. The drawer is
    read last: a request clears the test's workspace context."""
    items = list(run.items.all())
    assert len(items) == len(prepared["row_ids"])
    texts = {"first error": first_error(run) or ""}
    for item in items:
        texts[f"item {item.id}"] = json.dumps(item.error)
        texts[f"grid {item.id}"] = grid_error(run, item) or ""
    for cell in output_cells(prepared):
        texts[f"cell {cell.id}"] = f"{cell.value} {cell.value_infos}"
    texts["drawer"] = json.dumps(drawer_cells(client, w, prepared))
    return texts


@pytest.mark.parametrize(
    "error_class", [RuntimeError, ValueError], ids=["error-0", "error-1"]
)
def test_a_providers_text_never_reaches_the_cell(
    native_world, auth_client, error_class
):
    w = native_world
    context, prepared = failed_rows(w, error_class(MARKER))
    run = bound_run(context)
    for item in run.items.all():
        assert item.error == {
            "type": error_class.__name__,
            "code": "row_failed",
            "message": RUN_FAILED,
        }
    for cell in output_cells(prepared):
        assert (cell.value, reason_of(cell)) == (RUN_FAILED, {"reason": RUN_FAILED})
    for where, text in shown_texts(run, prepared, auth_client, w).items():
        assert MARKER not in text, where


def test_a_refusal_shows_its_own_words_never_the_error_it_came_from(
    native_world, auth_client
):
    w = native_world
    refusal = cl.ManagedCallFailedAfterDispatchError()
    refusal.__cause__ = RuntimeError(MARKER)
    context, prepared = failed_rows(w, refusal)
    run = bound_run(context)
    for item in run.items.all():
        assert item.error["code"] == REFUSED
        assert item.error["message"] == refusal.message
    for where, text in shown_texts(run, prepared, auth_client, w).items():
        assert MARKER not in text, where


def test_a_refusal_is_not_retried_by_the_runs_retry_policy(native_world, meter):
    from tfc.temporal.experiments.workflows import ROW_PROCESSING_RETRY_POLICY

    w = native_world
    context, prepared = started(w, None)
    rows = prepared["row_ids"]
    ledger = cl.MockCreditLedger(balances={w["dataset"].organization_id: Decimal("1")})
    refusals = [cl.refusal_for_code(cl.CODE_BILLING_UNAVAILABLE) for _ in rows]
    ledger.refuse_next_holds = list(refusals)
    # Marked retryable, and the policy would retry this type if it left the row.
    assert all(cl.is_retryable_refusal(refusal) for refusal in refusals)
    assert (
        type(refusals[0]).__name__
        not in ROW_PROCESSING_RETRY_POLICY.non_retryable_error_types
    )
    with cl.use_credit_ledger(ledger), cl.use_route_resolver(managed_route):
        statuses, call = run_rows(w, context, prepared)
    # Each row ends in the activity with a status: nothing reaches the policy.
    assert statuses == ["FAILED"] * len(rows)
    call.assert_not_called()
    # One hold per row and no wait: the call did not try again either.
    assert [entry[0] for entry in ledger.calls].count("hold") == len(rows)
    assert meter == []
    run = bound_run(context)
    items = list(run.items.all())
    assert len(items) == len(rows)
    for item in items:
        assert item.status == "failed"
        assert item.error["code"] == REFUSED
        assert item.error["message"] == refusals[0].message


def test_a_refusal_that_asks_to_wait_still_waits_and_tries_again_within_the_call(
    native_world, meter
):
    w = native_world
    context, prepared = started(w, None)
    rows = prepared["row_ids"]
    ledger = cl.MockCreditLedger(balances={w["dataset"].organization_id: Decimal("1")})
    refusal = cl.refusal_for_code(
        cl.CODE_CREDITS_INSUFFICIENT,
        metadata={
            "limit_source": cl.LIMIT_SOURCE_IN_FLIGHT,
            "remedy_hint": cl.REMEDY_RETRY,
        },
        retry_after=2,
    )
    assert refusal.backoff and cl.is_retryable_refusal(refusal)
    ledger.refuse_next_holds = [refusal]
    with cl.use_credit_ledger(ledger), cl.use_route_resolver(managed_route):
        statuses, call = run_rows(w, context, prepared, side_effect=claimed_answer)
    assert statuses == ["COMPLETED"] * len(rows)
    assert call.call_count == len(rows)
    assert meter == [cl.backoff_seconds(refusal)]
    run = bound_run(context)
    assert {(item.status, item.output) for item in run.items.all()} == {
        ("completed", "answer")
    }
