"""Resolve experiment tool snapshots and scoped tool IDs at the provider boundary."""

import uuid

from django.db.models import Q

from model_hub.models.openai_tools import Tools, openai_tool_envelope


def experiment_tools(tools, dataset):
    """Use saved definitions as snapshots, but resolve IDs in the run's tenant.

    Prompt versions store workbench objects (name/description/config), not Tools
    primary keys. Convert them with the same helper as the workbench. Already
    enveloped definitions are accepted too, so persisted configs remain usable
    on rerun. Only explicit IDs read the catalog, with worker-safe scope rather
    than request thread-local managers. Missing/deleted/foreign IDs fail closed.
    """
    tools = tools or []
    ids = {uuid.UUID(str(tool)) for tool in tools if not isinstance(tool, dict)}
    by_id = {}
    if ids:
        if not dataset.organization_id:
            raise ValueError("Tool is not accessible to this experiment")
        workspace = dataset.workspace
        scope = Q(workspace_id=workspace.id) if workspace else Q()
        if workspace and workspace.is_default:
            scope |= Q(workspace__isnull=True) | Q(
                workspace__is_default=True,
                workspace__organization_id=dataset.organization_id,
            )
        by_id = {
            tool.id: tool
            for tool in Tools.no_workspace_objects.filter(
                scope,
                organization_id=dataset.organization_id,
                id__in=ids,
                deleted=False,
            )
        }
        if ids != by_id.keys():
            raise ValueError("Tool is not accessible to this experiment")

    resolved = []
    for tool in tools:
        if not isinstance(tool, dict):
            resolved.append(by_id[uuid.UUID(str(tool))].as_openai_tool())
        elif isinstance(tool.get("config"), dict):
            resolved.append(
                openai_tool_envelope(
                    tool.get("name"), tool.get("description"), tool["config"]
                )
            )
        elif tool.get("type") == "function" and isinstance(tool.get("function"), dict):
            resolved.append(openai_tool_envelope(None, None, tool))
        else:
            raise ValueError("Invalid experiment tool definition")
    return resolved
