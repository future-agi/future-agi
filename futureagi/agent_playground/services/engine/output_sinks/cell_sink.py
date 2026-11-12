"""
Cell Output Sink — writes node outputs to model_hub.Cell model.

Required sink config keys:
    - dataset_id: str (UUID)
    - column_id: str (UUID)
    - row_id: str (UUID)

Optional sink config keys:
    - output_key: str — which output port value to store (defaults to first output key)
"""

import json
from typing import Any

import structlog

from agent_playground.services.engine.output_sink import (
    BaseOutputSink,
    OutputSinkContext,
    register_sink,
)

logger = structlog.get_logger(__name__)

# Map graph execution status to Cell status
_STATUS_MAP = {
    "SUCCESS": "pass",
    "FAILED": "error",
    "SKIPPED": "error",
}


class CellOutputSink(BaseOutputSink):
    """
    Writes node output to a model_hub.Cell.

    On SUCCESS: stores the output value with status=PASS.
    On FAILED: stores error info in value and reason in value_infos with status=ERROR.

    Status cascade (column → experiment_dataset → experiment) is NOT handled here.
    The Temporal workflow calls check_and_update_column_status at the right time
    (after all rows complete), same pattern as ProcessPromptWorkflow.
    """

    def validate_config(self, config: dict[str, Any]) -> None:
        required = ("dataset_id", "column_id", "row_id")
        missing = [k for k in required if k not in config]
        if missing:
            raise ValueError(f"CellOutputSink missing required config keys: {missing}")

    def store(self, context: OutputSinkContext) -> None:
        from django.core.exceptions import ValidationError
        from django.db import router, transaction

        from agent_playground.models.node import Node
        from agent_playground.services.engine.output_sinks.scope import (
            SinkScopeError,
            resolve_cell_destination,
        )
        from model_hub.models.develop_dataset import Cell

        using = router.db_for_write(Cell)
        with transaction.atomic(using=using):
            # The tenant comes from the persisted node, never from sink JSON or
            # caller-supplied metadata. This covers success AND failure sinks.
            try:
                node = (
                    Node.no_workspace_objects.using(using)
                    .select_related("graph_version__graph")
                    .select_for_update(of=("self", "graph_version__graph"))
                    .get(
                        pk=context.node_id,
                        graph_version__deleted=False,
                        graph_version__graph__deleted=False,
                    )
                )
            except (Node.DoesNotExist, ValidationError, ValueError) as exc:
                raise SinkScopeError(
                    "Cell output requires a live graph scope."
                ) from exc
            graph = node.graph_version.graph
            ids = resolve_cell_destination(
                context.config,
                graph.organization_id,
                graph.workspace_id,
                using=using,
                lock=True,
            )
            cells = Cell.all_objects.using(using).filter(
                dataset__organization_id=graph.organization_id,
                dataset__workspace_id=graph.workspace_id,
                dataset__deleted=False,
                column__dataset_id=ids["dataset_id"],
                column__deleted=False,
                row__dataset_id=ids["dataset_id"],
                row__deleted=False,
            )
            self._store_authorized(context, cells, ids, graph, using)

    def _store_authorized(self, context, cells, ids, graph, using):
        dataset_id = ids["dataset_id"]
        column_id = ids["column_id"]
        row_id = ids["row_id"]
        output_key = context.config.get("output_key")

        # Guard: don't overwrite cleanup state if experiment was cancelled
        # while the graph execution activity's thread was in-flight.
        experiment_id = context.config.get("experiment_id")
        if experiment_id:
            import uuid as _uuid

            from agent_playground.services.engine.output_sinks.scope import (
                SinkScopeError,
            )
            from model_hub.models.choices import StatusType
            from model_hub.models.experiments import ExperimentsTable

            experiment_status = (
                ExperimentsTable.no_workspace_objects.using(using)
                .filter(
                    pk=_uuid.UUID(str(experiment_id)),
                    dataset__organization_id=graph.organization_id,
                    dataset__workspace_id=graph.workspace_id,
                    dataset__deleted=False,
                )
                .values_list("status", flat=True)
                .first()
            )
            if experiment_status is None:
                raise SinkScopeError("Experiment is outside the graph scope.")
            if experiment_status == StatusType.CANCELLED.value:
                logger.info(
                    "cell_output_sink_skipped_cancelled",
                    dataset_id=dataset_id,
                    column_id=column_id,
                    row_id=row_id,
                )
                return

        cell_status = _STATUS_MAP.get(context.status, "pass")

        if context.status == "SUCCESS":
            # Extract value from outputs
            if output_key and output_key in context.outputs:
                value = context.outputs[output_key]
            elif context.outputs:
                value = next(iter(context.outputs.values()))
            else:
                value = ""

            # Serialize non-string values
            if value is not None and not isinstance(value, str):
                value = json.dumps(value)

            value_infos = context.metadata if context.metadata else {}
        else:
            # FAILED — store error info
            error_msg = context.metadata.get("error", "") if context.metadata else ""
            if not error_msg and context.outputs:
                error_msg = str(next(iter(context.outputs.values()), ""))
            value = error_msg
            value_infos = {"reason": error_msg}

        cells.update_or_create(
            column_id=column_id,
            row_id=row_id,
            dataset_id=dataset_id,
            defaults={
                "value": str(value) if value else "",
                "value_infos": json.dumps(value_infos),
                "status": cell_status,
                "deleted": False,
            },
        )

        logger.info(
            "cell_output_sink_stored",
            node_id=context.node_id,
            node_name=context.node_name,
            dataset_id=dataset_id,
            column_id=column_id,
            row_id=row_id,
            status=cell_status,
        )


register_sink("cell", CellOutputSink())
