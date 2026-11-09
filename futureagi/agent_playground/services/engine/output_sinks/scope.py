"""Tenant authorization shared by graph writes and cell output execution."""

import json
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import router


class SinkScopeError(ValidationError):
    """The destination is not a live cell location owned by this graph."""


def resolve_cell_destination(
    config, organization_id, workspace_id, *, using, lock=False, allow_deleted=False
):
    from model_hub.models.develop_dataset import Column, Dataset, Row

    try:
        ids = {
            key: UUID(str(config[key])) for key in ("dataset_id", "column_id", "row_id")
        }
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise SinkScopeError("Invalid cell output destination.") from exc
    if organization_id is None:
        raise SinkScopeError("Cell output requires a graph tenant scope.")

    # A no-capability save may retain soft-deleted destinations, but must still
    # prove their exact tenant and parent scope. Execution never opts into this.
    def destinations(model):
        manager = model._base_manager if allow_deleted else model.no_workspace_objects
        return manager.using(using)

    datasets = destinations(Dataset).filter(
        pk=ids["dataset_id"], organization_id=organization_id, workspace_id=workspace_id
    )
    parent_scope = {
        "dataset_id": ids["dataset_id"],
        "dataset__organization_id": organization_id,
        "dataset__workspace_id": workspace_id,
    }
    if not allow_deleted:
        parent_scope["dataset__deleted"] = False
    columns = destinations(Column).filter(pk=ids["column_id"], **parent_scope)
    rows = destinations(Row).filter(pk=ids["row_id"], **parent_scope)
    if lock:
        datasets = datasets.select_for_update()
        columns = columns.select_for_update(of=("self",))
        rows = rows.select_for_update(of=("self",))
    # Materialize on the write alias, in parent-first order when locking.
    if datasets.first() is None or columns.first() is None or rows.first() is None:
        raise SinkScopeError("Cell output destination is outside the graph scope.")
    return ids


def validate_node_sinks(config, graph, *, using=None, allow_deleted=False):
    from model_hub.models.develop_dataset import Cell

    if not isinstance(config, dict) or "_output_sinks" not in config:
        return
    sinks = config["_output_sinks"]
    if not isinstance(sinks, list):
        raise SinkScopeError("Output sinks must be a list.")
    for sink in sinks:
        if not isinstance(sink, dict):
            raise SinkScopeError("Invalid output sink.")
        if sink.get("name") == "cell":
            resolve_cell_destination(
                sink.get("config"),
                graph.organization_id,
                graph.workspace_id,
                using=using or router.db_for_write(Cell),
                allow_deleted=allow_deleted,
            )


def validate_node_sink_save(node, *, using, update_fields=None):
    """Only unchanged sinks on the same owner may retain deleted destinations."""
    from agent_playground.models.graph_version import GraphVersion

    original = (
        (
            type(node)
            ._base_manager.using(using)
            .select_for_update()
            .filter(pk=node.pk)
            .values("config", "graph_version_id", "deleted")
            .first()
        )
        if not node._state.adding
        else None
    )

    def saved_value(field):
        if original and update_fields is not None and field not in update_fields:
            if field != "graph_version_id" or "graph_version" not in update_fields:
                return original[field]
        return getattr(node, field)

    config = saved_value("config")
    if not isinstance(config, dict) or "_output_sinks" not in config:
        return
    graph_version_id = saved_value("graph_version_id")
    previous_config = original["config"] if original else None
    unchanged = (
        original is not None
        and original["graph_version_id"] == graph_version_id
        and (not original["deleted"] or saved_value("deleted"))
        and isinstance(previous_config, dict)
        and "_output_sinks" in previous_config
        # JSON equality must not conflate e.g. True with 1. Ignore other config.
        and json.dumps(previous_config["_output_sinks"], sort_keys=True)
        == json.dumps(config["_output_sinks"], sort_keys=True)
    )
    graph = (
        GraphVersion.no_workspace_objects.using(using)
        .select_related("graph")
        .get(pk=graph_version_id)
        .graph
    )
    validate_node_sinks(config, graph, using=using, allow_deleted=unchanged)
