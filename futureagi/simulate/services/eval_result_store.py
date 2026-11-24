"""Keeping a run's own verdict when grading again replaces it.

Every path that overwrites a call's ``eval_outputs`` slot for an eval that
already had a verdict first keeps that verdict in ``SimulateEvalResult``, exactly as
it was stored, as "the run's own score, replaced". ``keep_replaced_verdicts``
is the one function that writes these rows; rows are never updated after.

The state, label or score and reason are read from the stored slot where it
says them. Nothing is guessed: a shape that says none of the known states is
kept as ``unmapped``.
"""

from __future__ import annotations

import copy
import uuid
from collections.abc import Mapping
from typing import Any

from django.db.models import QuerySet
from django.utils import timezone

from simulate.models import SimulateEvalConfig, SimulateEvalResult, TestExecution
from simulate.services.scoring_status import (
    REASON_EVAL_RUN_CANCELLED,
    normalize_eval_entry,
)

State = SimulateEvalResult.State


def state_of_stored_verdict(slot: Mapping[str, Any]) -> tuple[str, str]:
    """The result state and cause of one stored ``eval_outputs`` slot."""
    if slot.get("denial"):
        # Grading was refused (usage or credits) before the evaluator answered.
        return State.CANCELLED, "denied"
    status = normalize_eval_entry(slot)
    if status == "succeeded":
        if slot.get("output") is None:
            # Stored as finished, with nothing it finished with.
            return State.UNMAPPED, "no_output"
        return State.ANSWERED, ""
    if status == "failed":
        return State.GRADING_ERROR, ""
    if status == "pending":
        return State.PENDING, ""
    if status == "timed_out":
        # Only the platform's scoring sweep writes this status, when the
        # grading never came back (scoring_status.close_due_scoring). The
        # evaluator's own failure or timeout is stored as failed, above.
        return State.INFRASTRUCTURE_ERROR, "timed_out"
    code = slot.get("code") or slot.get("reason_code")
    if code == "run_cancelled" or slot.get("reason") == REASON_EVAL_RUN_CANCELLED:
        return State.CANCELLED, "run_cancelled"
    if code == "input_missing":
        return State.UNMEASURED, "input_missing"
    return State.UNMAPPED, "skipped"


def _label_and_score(slot: Mapping[str, Any]) -> tuple[str | None, float | None]:
    output = slot.get("output")
    if isinstance(output, bool):
        return str(output).lower(), None
    if isinstance(output, (int, float)):
        return None, float(output)
    if isinstance(output, str):
        return output, None
    return None, None


def _is_placeholder(slot: Any) -> bool:
    """A slot holding only a pending placeholder: there is no verdict to keep."""
    return isinstance(slot, Mapping) and normalize_eval_entry(slot) == "pending"


def question_definition(eval_config) -> dict[str, Any]:
    """The configured eval as it stood when its verdict was replaced.

    It is not necessarily what graded the old verdict: the slot records no
    version, and the config may have been edited since.
    """
    return {
        "as_of": "replacement",
        "not_the_grader": True,
        "name": eval_config.name,
        "eval_template_id": str(eval_config.eval_template_id),
        "eval_template_name": getattr(eval_config.eval_template, "name", None),
        "mapping": copy.deepcopy(eval_config.mapping),
        "config": copy.deepcopy(eval_config.config),
        "model": eval_config.model,
    }


def eval_configs_of_run_test(run_test_id) -> dict[str, Any]:
    """The run test's configured evals by slot key, removed ones included."""
    return {
        str(config.id): config
        for config in SimulateEvalConfig.all_objects.filter(
            run_test_id=run_test_id
        ).select_related("eval_template")
    }


def replaced_verdict_rows(
    *,
    call_execution,
    slots: Mapping[str, Any],
    eval_configs: Mapping[str, Any],
    organization_id,
    workspace_id,
    replacement_id: uuid.UUID,
    replaced_at,
    replaced_by=None,
    definitions: Mapping[str, dict] | None = None,
) -> list[SimulateEvalResult]:
    """Unsaved rows keeping each verdict in ``slots`` that is about to be replaced.

    ``slots`` maps a slot key (an eval config id) to what the slot held. A
    placeholder is not a verdict and gets no row. ``definitions`` overrides the
    definition read from the config, for a caller that edits the config first.
    """
    rows = []
    for key, slot in slots.items():
        if _is_placeholder(slot):
            continue
        eval_config = eval_configs.get(str(key))
        if definitions and str(key) in definitions:
            definition = definitions[str(key)]
        elif eval_config is not None:
            definition = question_definition(eval_config)
        else:
            definition = None
        stored = slot if isinstance(slot, Mapping) else {"output": slot}
        state, cause = state_of_stored_verdict(stored)
        label, score = _label_and_score(stored)
        reason = stored.get("reason")
        rows.append(
            SimulateEvalResult(
                organization_id=organization_id,
                workspace_id=workspace_id,
                origin=SimulateEvalResult.Origin.REPLACED_OWN_SCORE,
                replacement_id=replacement_id,
                call_execution=call_execution,
                eval_config=eval_config,
                question_key=str(key),
                question_definition=definition,
                state=state,
                cause=cause,
                label=label,
                score=score,
                reason=reason if isinstance(reason, str) else None,
                replaced_verdict=slot,
                replaced_at=replaced_at,
                replaced_by=replaced_by,
            )
        )
    return rows


def keep_replaced_verdicts(rows: list[SimulateEvalResult]) -> None:
    """Write the rows that keep replaced verdicts. The only writer of such rows."""
    if rows:
        SimulateEvalResult.objects.bulk_create(rows, batch_size=500)


class ClearedVerdicts:
    """The verdicts an eval-only rerun is about to clear, one request at a time.

    ``add`` a call once nothing else can fail before it is saved, with the
    slots it held before they were emptied; ``keep`` in the transaction that
    saves the emptied calls.
    """

    def __init__(self, replaced_by=None, replacement_id=None, replaced_at=None):
        self.replacement_id = replacement_id or uuid.uuid4()
        self.replaced_at = replaced_at or timezone.now()
        self.replaced_by = replaced_by
        self._rows: list[SimulateEvalResult] = []
        self._runs: dict[Any, tuple[Any, Any, Any]] = {}
        self._configs: dict[Any, dict[str, Any]] = {}

    def _run_of(self, test_execution_id):
        if test_execution_id not in self._runs:
            run = (
                TestExecution.all_objects.filter(id=test_execution_id)
                .values_list(
                    "run_test_id",
                    "run_test__organization_id",
                    "run_test__workspace_id",
                )
                .first()
            )
            self._runs[test_execution_id] = run or (None, None, None)
        return self._runs[test_execution_id]

    def add(self, call_execution, slots=None) -> None:
        """Collect a call's verdicts: its ``eval_outputs``, or ``slots`` as they were."""
        if slots is None:
            slots = call_execution.eval_outputs
        if not isinstance(slots, Mapping) or not slots:
            return
        run_test_id, organization_id, workspace_id = self._run_of(
            call_execution.test_execution_id
        )
        if run_test_id is None:
            return
        if run_test_id not in self._configs:
            self._configs[run_test_id] = eval_configs_of_run_test(run_test_id)
        self._rows.extend(
            replaced_verdict_rows(
                call_execution=call_execution,
                slots=dict(slots),
                eval_configs=self._configs[run_test_id],
                organization_id=organization_id,
                workspace_id=workspace_id,
                replacement_id=self.replacement_id,
                replaced_at=self.replaced_at,
                replaced_by=self.replaced_by,
            )
        )

    def keep(self) -> None:
        # Taken first, so a failed write never carries rows into the next keep.
        rows, self._rows = self._rows, []
        keep_replaced_verdicts(rows)


def forget_replacement(
    replacement_id: uuid.UUID, call_execution_ids=None, question_key=None
) -> None:
    """Delete one request's rows, when its old slots were put back on the calls.

    ``call_execution_ids`` and ``question_key`` limit it to the slots put
    back, when the request put back only some of them. Only call this inside
    the transaction that puts the slots back.
    """
    rows = SimulateEvalResult.all_objects.filter(
        origin=SimulateEvalResult.Origin.REPLACED_OWN_SCORE,
        replacement_id=replacement_id,
    )
    if call_execution_ids is not None:
        rows = rows.filter(call_execution_id__in=list(call_execution_ids))
    if question_key is not None:
        rows = rows.filter(question_key=str(question_key))
    rows.delete()


def readable_results(queryset: QuerySet) -> QuerySet:
    """Results whose call and run are not marked deleted. Every reader filters through this."""
    return queryset.filter(
        call_execution__deleted=False, call_execution__test_execution__deleted=False
    )
