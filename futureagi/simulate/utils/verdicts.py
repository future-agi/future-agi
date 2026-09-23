from __future__ import annotations

# The status strings a row can carry that mean "not a real verdict yet (or
# not one any more)". Compared exactly as written -- case is not normalized
# (``lld-1-contracts.puml``'s ``Verdict.status`` note).
#
# ``_FAILED_STATUS`` is a literal, not an import of
# ``model_hub.models.choices.StatusType``, on purpose: importing that module
# executes ``model_hub/models/__init__.py``, which pulls in the entire
# model_hub model layer (``pandas``, ``PIL``, ``django.db.models``, …). This
# module is imported at module scope by both
# ``simulate.services.test_executor`` and
# ``simulate.temporal.activities.xl``, and must stay free of any model-layer
# import (whole-change review round 3, L2 -- a prior fix moved this predicate
# into ``simulate/utils/processing_outcomes.py`` claiming that module
# "already imported" ``StatusType`` at module scope; it did not, and the
# import it added pulled the model layer into a util both callers load
# eagerly). The value matches ``StatusType.FAILED.value``
# (``model_hub/models/choices.py``, ``FAILED = "Failed"``).
_PENDING_STATUS = "pending"
_SKIPPED_STATUS = "skipped"
_FAILED_STATUS = "Failed"
_NON_SEALING_STATUSES = {_PENDING_STATUS, _SKIPPED_STATUS, _FAILED_STATUS}


def has_stored_verdict(call_execution, eval_config_id) -> bool:
    """True when the call already holds a sealed verdict row for this config.

    One predicate for every sealed-verdict guard: the per-eval loop skip and
    the "skipped" payload write in ``TestExecutor._run_simulate_evaluations``
    (``simulate/services/test_executor.py``), and the matching loop-skip and
    no-transcript guards in ``xl.py::_run_evaluations_standalone``. They must
    all agree, so they all import this one function.

    In plain words: only a row that is not a pending placeholder, a skipped
    payload or a ``Failed`` grading seals -- not only a *completed* one. A
    ``{"status": "pending"}`` placeholder -- written by the bulk rerun paths
    before grading starts (``views/run_test.py:5471``, ``:8224``,
    ``ai_tools/tools/simulation/run_new_evals.py:196``; all three then
    dispatch ``test_executor.py::run_new_evals_on_call_executions_task``,
    whose dispatch loop hard-codes ``skip_existing=False`` in the
    ``apply_async`` kwargs it sends to ``_run_simulate_evaluations_task``,
    so the branch is live -- cited by symbol, not a line range, since this
    file moves under its own edits, whole-change review round 5, L1) --
    does not seal: grading must still run and replace it. A
    skipped payload (``status`` ``"skipped"``, from
    ``processing_outcomes.build_skipped_eval_output_payload``) and an errored
    grading row (``status`` ``"Failed"``) are not verdicts either and do not
    seal: the run-level add (TH-8046) grades those calls again. A missing
    key, ``None``, and ``{}`` also do not seal. Contract
    ``api_contracts/harness/eval-offer-backend-frontend.md`` v1.6 F2 (owner's
    rule, 2026-09-23 night, whole-change review round 2 M2).

    Implementation note -- why this checks for the three *non-sealing*
    statuses instead of checking for the one sealing status
    (``"Completed"``): every write in
    ``TestExecutor._run_single_simulate_evaluation`` (``test_executor.py``)
    stamps a ``status`` key, but ``xl.py::_run_single_evaluation`` -- the
    judge call behind the Temporal standalone path
    (``_run_evaluations_standalone``) -- still does not for a *successful*
    grading: that write carries no ``status`` key at all, exactly as before.
    Requiring an exact ``status == "Completed"`` match would therefore treat
    that real, successfully-graded verdict as unsealed and let
    ``skip_existing=True`` silently overwrite it -- exactly the corruption
    F2 forbids. Checking for the three known non-sealing shapes instead
    means any other non-empty row, including one with no ``status`` key,
    seals -- so the predicate needed no change for whole-change review round
    3's M1 fix, which instead changed the *writers*: both of
    ``_run_single_evaluation``'s error writes now carry ``"status":
    StatusType.FAILED.value`` (the same value the Celery path writes), and
    ``_run_evaluations_standalone``'s "no transcript" branch now writes
    through ``build_skipped_eval_output_payload`` (``status`` ``"skipped"``)
    instead of an ad-hoc four-key dict -- so a *fresh* errored or
    no-transcript row on the Temporal path no longer seals. A row written by
    the code as it stood *before* that fix -- a status-less errored or
    no-transcript row -- is indistinguishable from any other status-less
    completed row and still seals under this predicate; it is treated as a
    verdict and can only be re-graded by the eval-only re-run, which wipes
    ``eval_outputs`` wholesale (whole-change review round 3, M1).
    """
    row = (call_execution.eval_outputs or {}).get(str(eval_config_id))
    if not row:
        return False
    if isinstance(row, dict) and row.get("status") in _NON_SEALING_STATUSES:
        return False
    return True
