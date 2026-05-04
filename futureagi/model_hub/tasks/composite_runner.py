"""
CompositeEvaluationRunner — Phase 7 wiring, Phase B.

Runs a composite `EvalTemplate` against a dataset/experiment row batch the
same way `EvaluationRunner` runs a single-template eval. Every downstream
flow (experiments, datasets, trace evaluations) that funnels through
`process_eval_batch_async_task` picks up composite support for free once
that task branches on `template_type`.

Scope of Phase B:
- Load composite + children once, resolve weights once
- For each row: resolve mapping → call shared `execute_composite_children_sync`
  → write one aggregate Cell and one parent Evaluation row linked to N
  child Evaluation rows via `parent_evaluation` FK
- Works for both `aggregation_enabled=True` (aggregate score populates the
  cell) and `aggregation_enabled=False` (cell carries a summary payload,
  children stand alone but are still linked to the parent row for UI
  drill-down)

Out of scope for Phase B (tracked in Phase C / F):
- Per-child Column layout when aggregation is off — for now we still use
  a single aggregate column. Phase C decides the experiment/dataset
  column model change.
- Per-child Cell fan-out — drill-down uses Evaluation rows, not cells.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from django.db import transaction

from model_hub.models.choices import (
    CellStatus,
    ModelChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import (
    CompositeEvalChild,
    EvalTemplate,
    UserEvalMetric,
)
from model_hub.models.evaluation import Evaluation, StatusChoices
from model_hub.utils.composite_execution import (
    CompositeRunOutcome,
    execute_composite_children_sync,
)
from model_hub.utils.eval_result_columns import infer_eval_result_column_data_type

logger = logging.getLogger(__name__)


class CompositeEvaluationRunner:
    """Dataset/experiment-aware composite runner.

    Public surface mirrors `EvaluationRunner` so the async task can
    dispatch between single and composite without any shape change at the
    call site.
    """

    def __init__(
        self,
        user_eval_metric_id,
        experiment_dataset=None,
        column=None,
        optimize=None,
        source: str | None = None,
        source_id: str | None = None,
        source_configs: dict | None = None,
        **_ignored,
    ):
        self.user_eval_metric_id = user_eval_metric_id
        self.experiment_dataset = experiment_dataset
        self.column = column  # input column for experiment flows
        self.optimize = optimize
        self.source = source
        self.source_id = str(source_id) if source_id else None
        self.source_configs = source_configs or {}

        self.user_eval_metric: UserEvalMetric | None = None
        self.template: EvalTemplate | None = None
        self.dataset: Dataset | None = None
        self.children: list[CompositeEvalChild] = []
        self.organization = None
        self.workspace = None

        self._load()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load(self) -> None:
        self.user_eval_metric = UserEvalMetric.objects.select_related(
            "template", "dataset"
        ).get(id=self.user_eval_metric_id)
        self.template = self.user_eval_metric.template

        if self.template.template_type != "composite":
            raise ValueError(
                f"CompositeEvaluationRunner called with non-composite template "
                f"{self.template.id} (template_type={self.template.template_type!r})"
            )

        self.dataset = self.user_eval_metric.dataset
        self.organization = (
            self.dataset.organization if self.dataset else self.template.organization
        )
        self.workspace = self.dataset.workspace if self.dataset else None

        self.children = list(
            CompositeEvalChild.objects.filter(parent=self.template, deleted=False)
            .select_related("child", "pinned_version")
            .order_by("order")
        )
        if not self.children:
            raise ValueError(f"Composite template {self.template.id} has no children.")

    # ------------------------------------------------------------------
    # Column plumbing
    # ------------------------------------------------------------------

    @classmethod
    def build_column_config(
        cls,
        user_eval_metric: UserEvalMetric,
        dataset: Dataset,
        experiment_dataset=None,
        column: Column | None = None,
        optimize=None,
    ) -> dict:
        """Build the result-column config for a composite metric.

        Exposed as a classmethod so experiment pre-creation call sites
        can produce the correct column shape without instantiating the
        full runner (which loads children + validates the metric).

        Aggregation on → numeric score column. Aggregation off → text
        (the cell carries the summary string; individual child values
        live on the child Evaluation rows linked via `parent_evaluation`).
        """
        template = user_eval_metric.template
        source = SourceChoices.EVALUATION.value
        source_id = str(user_eval_metric.id)
        name = user_eval_metric.name

        if experiment_dataset and column:
            source = SourceChoices.EXPERIMENT_EVALUATION.value
            source_id = (
                f"{experiment_dataset.id}-{column.id}"
                f"-sourceid-{user_eval_metric.id}"
            )
            name = f"{user_eval_metric.name}-{column.name}"
        elif optimize and column:
            source = SourceChoices.OPTIMISATION_EVALUATION.value
            source_id = f"{optimize.id}-sourceid-{user_eval_metric.id}"
            name = f"{user_eval_metric.name}-{column.name}"

        data_type = infer_eval_result_column_data_type(template)

        return {
            "name": name,
            "data_type": data_type,
            "source": source,
            "source_id": source_id,
            "dataset": dataset,
        }

    def _get_column_config(self) -> dict:
        return self.build_column_config(
            user_eval_metric=self.user_eval_metric,
            dataset=self.dataset,
            experiment_dataset=self.experiment_dataset,
            column=self.column,
            optimize=self.optimize,
        )

    def _get_or_create_column(self) -> Column:
        column_config = self._get_column_config()
        with transaction.atomic():
            dataset_obj = Dataset.no_workspace_objects.select_for_update().get(
                id=self.dataset.id
            )
            try:
                column, created = Column.objects.get_or_create(**column_config)
            except Exception:
                column = Column.objects.get(
                    dataset=column_config["dataset"],
                    source=column_config["source"],
                    source_id=column_config["source_id"],
                    deleted=False,
                )
                created = False

            if created:
                if self.experiment_dataset:
                    column.status = StatusType.RUNNING.value
                    column.save(update_fields=["status"])
                    self.experiment_dataset.columns.add(column)
                else:
                    order = dataset_obj.column_order or []
                    order.append(str(column.id))
                    dataset_obj.column_order = order
                    dataset_obj.save(update_fields=["column_order"])

        return column

    # ------------------------------------------------------------------
    # Row mapping resolution
    # ------------------------------------------------------------------

    def _resolve_row_mapping(self, row: Row) -> dict[str, Any]:
        """Pull cell values from a row into the `{var_name: value}` shape
        `run_eval_func` expects.

        Reuses `process_mapping` from the single-eval runner so JSON path
        resolution, KB UUIDs, and special values all behave identically.
        """
        from model_hub.views.eval_runner import process_mapping

        mappings = (self.user_eval_metric.config or {}).get("mapping") or {}
        if not isinstance(mappings, dict) or not mappings:
            return {}

        replace_column_id = self.column.id if self.column else None
        active_column_id = self.column.id if self.column else None

        required_field, mapping_values = process_mapping(
            mappings,
            row,
            replace_column_id=replace_column_id,
            column_id=active_column_id,
            run_prompt_column=False,
            runner=None,
            eval_template_name=self.template.name,
        )

        return dict(zip(required_field, mapping_values, strict=False))

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _get_reason_column(self, column: Column) -> Column | None:
        """Return the EVALUATION_REASON column paired with this composite
        parent column, if one exists. Composite creation flows materialize
        it via source_id = "{column.id}-sourceid-{user_eval_metric.id}"
        (see develop_dataset.py composite eval creation).
        """
        return Column.objects.filter(
            dataset=self.dataset,
            source=SourceChoices.EVALUATION_REASON.value,
            source_id=f"{column.id}-sourceid-{self.user_eval_metric.id}",
            deleted=False,
        ).first()

    def _write_cell(
        self,
        column: Column,
        row: Row,
        outcome: CompositeRunOutcome,
    ) -> None:
        """Write a single aggregate Cell for this row's composite result,
        plus the paired reason cell if a reason column exists. Keeping the
        reason cell in lockstep prevents the UI from leaving a loading
        skeleton next to a finished composite.
        """
        if self.template.aggregation_enabled and outcome.aggregate_score is not None:
            cell_value: str = f"{outcome.aggregate_score:.4f}"
            cell_status = CellStatus.PASS.value
        else:
            # Aggregation off (or all children failed) — store the summary
            # string. Pass/fail is surfaced in value_infos for the UI; the
            # Cell model only has pass/running/error, so the cell itself
            # is marked PASS once the run finishes cleanly.
            cell_value = outcome.summary or ""
            cell_status = CellStatus.PASS.value

        value_infos = {
            "composite_id": str(self.template.id),
            "aggregation_enabled": self.template.aggregation_enabled,
            "aggregate_score": outcome.aggregate_score,
            "aggregate_pass": outcome.aggregate_pass,
            "aggregation_function": self.template.aggregation_function,
            "summary": outcome.summary,
            "children": [cr.model_dump() for cr in outcome.child_results],
        }

        # Stop guard: if the user stopped this eval mid-run, drop the
        # late composite result so we don't clobber the
        # "User stopped evaluation" marker.
        from model_hub.services.experiment_utils import is_user_eval_stopped

        if is_user_eval_stopped(self.user_eval_metric_id):
            return

        Cell.objects.update_or_create(
            dataset=self.dataset,
            column=column,
            row=row,
            defaults={
                "value_infos": json.dumps(value_infos),
                "value": cell_value,
                "status": cell_status,
            },
        )

        # Mirror the composite outcome onto the paired reason cell so its
        # column is never left empty / stuck in a loading state.
        reason_column = self._get_reason_column(column)
        if reason_column is None:
            return

        reason_text = outcome.summary or ""
        if not reason_text:
            failed = [
                cr for cr in outcome.child_results if cr.status != "completed"
            ]
            if failed and len(failed) == len(outcome.child_results):
                # Every child failed — surface a concise rollup instead of a
                # blank cell.
                reason_text = (
                    "All child evaluations failed. "
                    "See child evals for details."
                )
        if not reason_text:
            reason_text = "No reasoning available."

        # If every child errored, mark the reason cell ERROR so the UI can
        # style it consistently with the children's error cells. Otherwise
        # the composite produced usable output (even if NaN aggregate), so
        # PASS is the right terminal state.
        all_children_failed = bool(outcome.child_results) and all(
            cr.status != "completed" for cr in outcome.child_results
        )
        reason_status = (
            CellStatus.ERROR.value if all_children_failed else CellStatus.PASS.value
        )

        Cell.objects.update_or_create(
            dataset=self.dataset,
            column=reason_column,
            row=row,
            defaults={
                "value_infos": json.dumps({"reason": reason_text}),
                "value": reason_text,
                "status": reason_status,
            },
        )

    def _write_evaluation_rows(
        self,
        row: Row,
        outcome: CompositeRunOutcome,
    ) -> Evaluation:
        """Write the parent Evaluation row + N child Evaluation rows.

        Children always link back to the parent via `parent_evaluation`
        even when `aggregation_enabled` is False — the UI uses that
        linkage for drill-down.
        """
        user = self.user_eval_metric.user

        parent_row = Evaluation.objects.create(
            user=user,
            organization=self.organization,
            workspace=self.workspace,
            eval_template=self.template,
            status=StatusChoices.COMPLETED,
            input_data={"row_id": str(row.id)},
            eval_config={
                "composite": True,
                "aggregation_enabled": self.template.aggregation_enabled,
                "aggregation_function": self.template.aggregation_function,
            },
            data={
                "aggregate_score": outcome.aggregate_score,
                "aggregate_pass": outcome.aggregate_pass,
                "summary": outcome.summary,
            },
            reason=outcome.summary or "",
            value=(
                outcome.aggregate_score if self.template.aggregation_enabled else None
            ),
        )

        for cr in outcome.child_results:
            try:
                child_template = next(
                    link.child
                    for link in self.children
                    if str(link.child_id) == cr.child_id
                )
            except StopIteration:
                logger.warning(
                    "Composite child result references unknown child_id=%s", cr.child_id
                )
                continue

            Evaluation.objects.create(
                user=user,
                organization=self.organization,
                workspace=self.workspace,
                eval_template=child_template,
                parent_evaluation=parent_row,
                status=(
                    StatusChoices.COMPLETED
                    if cr.status == "completed"
                    else StatusChoices.FAILED
                ),
                input_data={"row_id": str(row.id)},
                eval_config={"child_of": str(self.template.id), "order": cr.order},
                data={
                    "score": cr.score,
                    "output": cr.output,
                    "output_type": cr.output_type,
                    "weight": cr.weight,
                },
                reason=cr.reason or "",
                value=cr.score,
                error_message=cr.error or "",
            )

        return parent_row

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def run_prompt(self, row_ids: list[str] | None = None) -> None:
        """Entry point called by `process_eval_batch_async_task`.

        Shape mirrors `EvaluationRunner.run_prompt` — the async task can
        swap runners without changing its call site.
        """
        logger.info(
            "CompositeEvaluationRunner.run_prompt template=%s metric=%s row_ids=%s",
            self.template.id,
            self.user_eval_metric_id,
            row_ids,
        )
        try:
            self.user_eval_metric.status = StatusType.RUNNING.value
            self.user_eval_metric.save(update_fields=["status"])

            column = self._get_or_create_column()

            rows_qs = Row.objects.filter(
                dataset_id=self.dataset.id, deleted=False
            ).select_related("dataset")
            if row_ids:
                rows_qs = rows_qs.filter(id__in=row_ids)
            rows = list(rows_qs.order_by("order"))

            # Mark any missing cells as running so the UI has something
            # to show while we work through the rows. Prime both the
            # aggregate column and its paired reason column (if any) so
            # neither side is left in a phantom loading state when the
            # run finishes.
            try:
                from model_hub.views.eval_runner import bulk_update_or_create_cells

                row_ids_list = [r.id for r in rows]
                running_defaults = {
                    "value_infos": json.dumps({}),
                    "value": "",
                    "status": CellStatus.RUNNING.value,
                }
                bulk_update_or_create_cells(
                    row_ids_list,
                    column.id,
                    self.dataset.id,
                    running_defaults,
                )
                reason_column = self._get_reason_column(column)
                if reason_column is not None:
                    bulk_update_or_create_cells(
                        row_ids_list,
                        reason_column.id,
                        self.dataset.id,
                        running_defaults,
                    )
            except Exception:
                logger.exception("bulk_update_or_create_cells failed — continuing")

            weight_overrides = self.user_eval_metric.composite_weight_overrides or None

            for row in rows:
                self._run_single_row(row, column, weight_overrides)

            if not row_ids:
                column.status = StatusType.COMPLETED.value
                column.save(update_fields=["status"])

            self.user_eval_metric.status = StatusType.COMPLETED.value
            self.user_eval_metric.save(update_fields=["status"])
        except Exception as e:
            logger.exception("CompositeEvaluationRunner.run_prompt failed")
            try:
                self.user_eval_metric.status = StatusType.ERROR.value
                self.user_eval_metric.save(update_fields=["status"])
            except Exception:
                logger.exception("Failed to mark user_eval_metric as ERROR")
            # Flip any still-running composite cells (+ paired reason cells)
            # to ERROR so the UI doesn't leave a loading skeleton behind.
            try:
                from model_hub.utils.eval_cell_status import (
                    mark_eval_cells_stopped,
                )

                mark_eval_cells_stopped(
                    self.user_eval_metric,
                    reason=str(e)[:500] or "Composite evaluation failed",
                )
            except Exception:
                logger.exception("Failed to mark composite cells as ERROR")
            raise e

    def _run_single_row(
        self,
        row: Row,
        column: Column,
        weight_overrides: dict[str, float] | None,
    ) -> None:
        try:
            mapping = self._resolve_row_mapping(row)

            # `run_eval_func` expects `config` in its nested form:
            # `{"config": {...}, "mapping": {...}, ...}`. Composite parent
            # bindings don't carry per-child runtime params — each child
            # template has its own config — so forward the binding's
            # top-level runtime overrides untouched and let the helper
            # thread them through.
            parent_config = self.user_eval_metric.config or {}
            runtime_config = {k: v for k, v in parent_config.items() if k != "mapping"}

            model = self.user_eval_metric.model or ModelChoices.TURING_LARGE.value

            outcome = execute_composite_children_sync(
                parent=self.template,
                child_links=self.children,
                mapping=mapping,
                config=runtime_config,
                org=self.organization,
                workspace=self.workspace,
                model=model,
                source="composite_eval_dataset",
                weight_overrides=weight_overrides,
            )

            self._write_cell(column, row, outcome)
            self._write_evaluation_rows(row, outcome)
        except Exception as e:
            logger.exception("Composite row %s failed: %s", row.id, e)
            try:
                error_payload = json.dumps(
                    {
                        "error": str(e)[:500],
                        "debug_info": (
                            f"Composite evaluation failed for row {row.id}. "
                            "Check child eval configs and input mappings."
                        ),
                    }
                )
                Cell.objects.filter(
                    column=column, row=row, dataset=self.dataset, deleted=False
                ).update(
                    status=CellStatus.ERROR.value,
                    value=error_payload,
                )
                # Mirror the failure onto any paired EVALUATION_REASON cell
                # for this row so the UI doesn't leave a loading skeleton
                # next to the failed aggregate cell. Reason columns use
                # source_id = "{eval_column_id}-sourceid-{user_eval_metric_id}".
                Cell.objects.filter(
                    row=row,
                    dataset=self.dataset,
                    deleted=False,
                    column__source=SourceChoices.EVALUATION_REASON.value,
                    column__source_id__endswith=(
                        f"-sourceid-{self.user_eval_metric.id}"
                    ),
                ).update(
                    status=CellStatus.ERROR.value,
                    value="Evaluation failed",
                    value_infos=error_payload,
                )
            except Exception:
                logger.exception("Failed to mark composite cell as ERROR")
