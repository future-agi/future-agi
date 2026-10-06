from __future__ import annotations

# The status strings a row can carry that mean "not a real verdict yet (or
# not one any more)". Compared exactly as written -- case is not normalized.
#
# ``_FAILED_STATUS`` is a literal, not an import of
# ``model_hub.models.choices.StatusType``: that import pulls in the whole
# model_hub model layer (pandas, PIL, django.db.models, ...), and this module
# is imported at module scope by both ``simulate.services.test_executor`` and
# ``simulate.temporal.activities.xl``, so it must stay free of any
# model-layer import. The value matches ``StatusType.FAILED.value``.
_PENDING_STATUS = "pending"
_SKIPPED_STATUS = "skipped"
_FAILED_STATUS = "Failed"
_NON_SEALING_STATUSES = {_PENDING_STATUS, _SKIPPED_STATUS, _FAILED_STATUS}


def has_stored_verdict(call_execution, eval_config_id) -> bool:
    """True when the call already holds a sealed verdict row for this config.

    Shared by every sealed-verdict guard: the per-eval loop skip and the
    "skipped" payload write in ``TestExecutor._run_simulate_evaluations``,
    and the matching loop-skip and no-transcript guards in
    ``xl.py::_run_evaluations_standalone``.

    Only a pending placeholder, a skipped payload, or a ``Failed`` grading
    counts as unsealed -- any other non-empty row seals, not only a
    ``"Completed"`` one. That's deliberate: ``xl.py::_run_single_evaluation``
    writes no ``status`` key at all on a successful grade, so requiring an
    exact ``"Completed"`` match would treat a real verdict as unsealed and
    let ``skip_existing=True`` silently overwrite it.
    """
    row = (call_execution.eval_outputs or {}).get(str(eval_config_id))
    if not row:
        return False
    if isinstance(row, dict) and row.get("status") in _NON_SEALING_STATUSES:
        return False
    return True
